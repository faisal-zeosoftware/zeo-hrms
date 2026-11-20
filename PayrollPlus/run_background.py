"""v1.75.1 – Large payroll runs are calculated in the background.

Before, the payroll of every employee was calculated inside the request that saved the run. With 1,000+ employees the request
ran for minutes: the web server's time limit (300 s) ended it, the browser showed "Calculating…" with no end, and the web
worker was busy for everyone else. Now:

* the request saves the run (number, branches, employees) and returns at once;
* the calculation runs in the background worker (Celery – the zeo-celery service of the installation); with no worker
  answering within QUEUE_WAIT seconds it runs in a thread of the web server instead (the next progress poll starts it);
* it is saved in batches of BATCH employees, each committed on its own – a failure keeps the batches before it, and
  "Continue" (POST payroll-plus/api/run-progress/continue/<run>/) calculates only the employees still without a payslip;
* the run is locked while it is calculated (no second calculation, no approval, no delete) – see is_calculating();
* the progress ("2,340 of 5,000 employees") is in run_progress.

Settings (environment):
  ZEO_PAYROLL_BACKGROUND  auto (default) | celery | thread | off   (off = always in the request, as before)
  ZEO_PAYROLL_ASK_MIN     the screen asks for the background; runs of this many employees or fewer stay in the request (default 50)
  ZEO_PAYROLL_SYNC_MAX    runs with more employees than this go to the background even when the screen did not ask (default 300)
  ZEO_PAYROLL_BATCH       employees per committed batch (default 100)
"""
import logging
import os
import threading
import time
from contextlib import contextmanager

from django.core.cache import cache
from django.db import connection, transaction

from . import run_progress as RP

log = logging.getLogger(__name__)

MODE = (os.environ.get('ZEO_PAYROLL_BACKGROUND') or 'auto').strip().lower()
try:
    SYNC_MAX = int(os.environ.get('ZEO_PAYROLL_SYNC_MAX') or 300)
except ValueError:
    SYNC_MAX = 300
try:   # the screen asks for the background; runs this small are still calculated in the request (a second or two)
    ASK_MIN = int(os.environ.get('ZEO_PAYROLL_ASK_MIN') or 50)
except ValueError:
    ASK_MIN = 50
try:
    BATCH = max(10, int(os.environ.get('ZEO_PAYROLL_BATCH') or 100))
except ValueError:
    BATCH = 100
QUEUE_WAIT = 20      # seconds a queued calculation waits for the Celery worker before a thread takes it
LOCK_TTL = 4 * 3600


class Busy(Exception):
    """The run is being calculated already."""


def _schema():
    return getattr(connection, 'schema_name', None) or 'public'


def _lock_key(run_id, schema=None):
    return 'zeo:paylock:%s:%s' % (schema or _schema(), run_id)


def _claim_key(token, schema=None):
    return 'zeo:payclaim:%s:%s' % (schema or _schema(), token)


def enabled():
    return MODE != 'off'


def wanted(asked, n_employees):
    """The screen asked for the background, or the run is too large for one request."""
    if not enabled():
        return False
    n = int(n_employees or 0)
    return (bool(asked) and n > ASK_MIN) or n > SYNC_MAX


def truthy(v):
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


@contextmanager
def deferred():
    """While saving the run: the m2m handler does not calculate (PayrollManagement.signals._BG)."""
    from PayrollManagement import signals as SG
    SG._BG.defer = True
    SG._BG.deferred = None
    try:
        yield SG._BG
    finally:
        SG._BG.defer = False


def is_calculating(run_id):
    """→ the progress of the run's calculation while it runs (or waits for the worker), else None."""
    try:
        tok = cache.get(_lock_key(run_id))
    except Exception:
        tok = None
    if not tok:
        return None
    p = RP.get(tok)
    if p is None or p['status'] in ('done', 'failed', 'stalled'):
        return None
    return p


def refuse_if_calculating(run_id):
    if is_calculating(run_id):
        raise Busy('This payroll is still being calculated. Wait until it is finished.')


def start(run, token=None, user=None, n_employees=None):
    """Called inside the request's transaction after the run is saved: locks the run, writes the 'queued' progress and hands
    the calculation to the worker once the transaction is committed. → the progress token."""
    token = RP.clean_token(token) or ('run%s-%d' % (run.pk, int(time.time() * 1000)))
    schema = _schema()
    try:
        cache.delete(_claim_key(token, schema))
    except Exception:
        pass
    lk = _lock_key(run.pk, schema)
    old = None
    try:
        old = cache.get(lk)
    except Exception:
        pass
    if old and is_calculating(run.pk):
        raise Busy('This payroll is already being calculated.')
    try:
        cache.set(lk, token, LOCK_TTL)
    except Exception:
        pass
    n = n_employees if n_employees is not None else run.employees.count()
    who = RP._user_name(user)
    job = {'token': token, 'schema': schema, 'run_id': run.pk, 'label': str(getattr(run, 'name', '') or ''), 'by': who,
           'status': 'queued', 'total': int(n or 0), 'done': 0, 'skipped': 0, 'current': '', 'started': time.time(), 'ended': None,
           'message': '', 'background': True, 'queued_at': time.time()}
    RP._save(job, force=True)
    RP._TL.job = None   # the request's middleware must not mark it done
    transaction.on_commit(lambda: _dispatch(schema, run.pk, token, who))
    return token


def _worker_alive():
    """auto: is a Celery worker answering? (asked at most once a minute; no worker = the thread at once, no 20 s wait)"""
    try:
        v = cache.get('zeo:payworker')
        if v is not None:
            return v == 1
    except Exception:
        pass
    alive = False
    try:
        from zeo.celery import app as capp
        alive = bool(capp.control.ping(timeout=0.7))
    except Exception:
        alive = False
    try:
        cache.set('zeo:payworker', 1 if alive else 0, 60)
    except Exception:
        pass
    return alive


def _dispatch(schema, run_id, token, who):
    if MODE == 'auto' and not _worker_alive():
        _thread(schema, run_id, token, who)
        return
    if MODE in ('auto', 'celery'):
        try:
            from .tasks import calculate_run_task
            calculate_run_task.apply_async(args=[schema, run_id, token, who], retry=False)
            return
        except Exception as ex:   # no broker – the thread below
            log.warning('payroll run %s: the background worker could not be reached (%s) – calculated in a thread', run_id, ex)
            if MODE == 'celery':
                _fail(schema, token, run_id, 'The background worker (zeo-celery) could not be reached. Start it and press Continue.')
                return
    _thread(schema, run_id, token, who)


def _thread(schema, run_id, token, who):
    t = threading.Thread(target=run_job, args=(schema, run_id, token, who), name='zeo-payroll-%s' % run_id, daemon=True)
    t.start()


def _fail(schema, token, run_id, msg):
    d = None
    try:
        d = cache.get(RP._key(token, schema))
    except Exception:
        pass
    d = d or {'token': token, 'schema': schema, 'run_id': run_id, 'started': time.time(), 'total': 0, 'done': 0}
    d.update({'status': 'failed', 'message': msg, 'ended': time.time()})
    d['schema'] = schema
    RP._save(d, force=True)
    try:
        cache.delete(_lock_key(run_id, schema))
    except Exception:
        pass


def kick_waiting():
    """Called by the progress reads: a calculation queued longer than QUEUE_WAIT (no Celery worker took it) runs in a thread."""
    if MODE != 'auto':
        return
    now = time.time()
    for j in RP.active():
        if j['status'] == 'queued' and now - (j.get('started') or now) > QUEUE_WAIT:
            try:
                if not cache.add('zeo:paykick:%s:%s' % (_schema(), j['token']), 1, 600):
                    continue
            except Exception:
                pass
            log.warning('payroll run %s: no background worker took the calculation – calculated in a thread', j['run_id'])
            _thread(_schema(), j['run_id'], j['token'], j.get('by') or '')


def run_job(schema, run_id, token, who=''):
    """The calculation itself (Celery task or thread)."""
    from django_tenants.utils import schema_context
    try:
        if not cache.add(_claim_key(token, schema), 1, LOCK_TTL):   # the worker and a thread never both calculate
            return
        q = cache.get(RP._key(token, schema)) or {}
        if q.get('status') != 'queued':   # a late message of a calculation already done (the worker was started later)
            return
    except Exception:
        pass
    try:
        with schema_context(schema):
            from django.apps import apps
            from PayrollManagement.signals import calculate_run
            PR = apps.get_model('PayrollManagement', 'PayrollRun')
            run = PR.objects.filter(pk=run_id).first()
            if run is None:
                _fail(schema, token, run_id, 'The payroll run was deleted.')
                return
            RP.begin_request(token)
            RP._TL.user = who
            P = apps.get_model('PayrollManagement', 'Payslip')
            todo_ids = list(run.employees.exclude(pk__in=P.objects.filter(payroll_run=run).values('employee_id')).values_list('pk', flat=True))
            todo = run.employees.filter(pk__in=todo_ids)   # Continue: only the employees still without a payslip
            job = {'token': token, 'schema': schema, 'run_id': run.pk, 'label': str(run.name or ''), 'by': who, 'status': 'preparing',
                   '_fixed': True, 'total': len(todo_ids), 'done': 0, 'skipped': 0, 'current': '', 'started': time.time(), 'ended': None, 'message': '',
                   'background': True, '_implicit': False}
            RP._TL.job = job
            RP._save(job, force=True)
            ok, msg = True, ''
            try:
                calculate_run(run, todo, batch=BATCH)
                missing = run.employees.exclude(pk__in=apps.get_model('PayrollManagement', 'Payslip').objects
                                                .filter(payroll_run=run).values('employee_id')).count()
                job['skipped'] = missing
                if str(run.status or '').lower() == 'pending' and apps.get_model('PayrollManagement', 'Payslip').objects.filter(payroll_run=run).exists():
                    PR.objects.filter(pk=run.pk).update(status='processed')
            except Exception as ex:   # the batches before the failing one are kept
                log.exception('payroll run %s: background calculation failed', run_id)
                ok, msg = False, 'The calculation stopped: %s. The payslips calculated before are kept – press Continue.' % (str(ex)[:200] or ex.__class__.__name__)
            RP.end_request(ok, msg)
            _notify(run, who, ok, msg)
    finally:
        try:
            if cache.get(_lock_key(run_id, schema)) == token:
                cache.delete(_lock_key(run_id, schema))
        except Exception:
            pass
        if threading.current_thread() is not threading.main_thread():
            try:
                connection.close()
            except Exception:
                pass


def _notify(run, who, ok, msg):
    """A to-do / bell notice for the user who started the calculation (best effort)."""
    try:
        from django.apps import apps
        RC = apps.get_model('PayrollPlus', 'RunControl')
        rc = RC.objects.filter(run_id=run.pk).first()
        uid = getattr(rc, 'prepared_by_id', None)
        if not uid:
            return
        from Chatter.models import InboxItem
        title = ('Payroll calculated: %s' % (run.name or run.pk)) if ok else ('Payroll calculation stopped: %s' % (run.name or run.pk))
        InboxItem.objects.create(user_id=uid, kind='reminder', model='PayrollManagement.PayrollRun', object_id=str(run.pk),
                                 record_label=str(run.name or run.pk)[:255], page='/main-sidebar/salary-options/payroll-run/%s' % run.pk,
                                 title=title[:255], body=msg or 'The payslips are ready to check.', from_name='ZEO')
    except Exception:
        pass


def continue_run(run, user, token=None):
    """Continue / resume a stopped calculation: only the employees without a payslip are calculated."""
    if is_calculating(run.pk):
        raise Busy('This payroll is already being calculated.')
    st = str(run.status or '').lower()
    if st not in ('pending', 'processed'):
        raise Busy('Only a payroll that is not approved or paid can be calculated.')
    from django.apps import apps
    P = apps.get_model('PayrollManagement', 'Payslip')
    left = run.employees.exclude(pk__in=P.objects.filter(payroll_run=run).values('employee_id')).count()
    with transaction.atomic():
        return start(run, token, user, n_employees=left), left
