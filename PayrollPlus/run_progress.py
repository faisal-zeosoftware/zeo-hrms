"""v1.75.1 – Live progress of a payroll calculation ("Calculating 432 of 1,000 employees").

A payroll run of 1,000 employees takes minutes; the page used to show only "Calculating…". Now every calculation
(new run, recalculate, any other path through PayrollManagement.signals.process_payroll) keeps a progress record in the
cache (Redis) – outside the database transaction of the run, so another request can read it while the run is calculated.

The browser sends a token of its own in the header X-Zeo-Progress with the request that calculates; it then reads
GET payroll-plus/api/run-progress/?token=<token>   → {status, total, done, percent, current, elapsed_s, eta_s, ...}
GET payroll-plus/api/run-progress/                 → {'jobs': [...]} every calculation of the company running now (or ended
                                                      in the last 10 minutes) – the Payroll runs page shows them to every user.
Without the header (a scheduled job, another screen) the record is kept under the run: token "run<id>".

status: preparing → running → finishing (payslips done; totals, provisions, journals) → done | failed
"""
import re
import threading
import time

from django.core.cache import cache
from django.db import connection

_TL = threading.local()
TTL = 3600            # a record lives an hour at most
KEEP_ENDED = 600      # an ended calculation is listed for 10 minutes
SAVE_EVERY = 0.4      # seconds between cache writes while running (the last employee is always written)
STALL = 180           # no step for 3 minutes while "running" → reported as stalled


def _schema():
    return getattr(connection, 'schema_name', None) or 'public'


def _key(token, schema=None):
    return 'zeo:payprog:%s:%s' % (schema or _schema(), token)


def _index_key(schema=None):
    return 'zeo:payprog:%s:index' % (schema or _schema())


def clean_token(t):
    t = re.sub(r'[^A-Za-z0-9_.-]', '', str(t or ''))[:64]
    return t or None


def _save(job, force=False):
    now = time.time()
    if not force and now - job.get('_saved', 0) < SAVE_EVERY:
        return
    job['_saved'] = now
    job['updated'] = now
    schema = job.get('schema')
    out = {k: v for k, v in job.items() if not k.startswith('_')}
    try:
        cache.set(_key(job['token'], schema), out, TTL)
        idx = cache.get(_index_key(schema)) or []
        if job['token'] not in idx:
            idx = [t for t in idx if t != job['token']][-49:] + [job['token']]
            cache.set(_index_key(schema), idx, TTL)
    except Exception:   # progress is a help – never stops the payroll
        pass


def _user_name(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return ''
    try:
        n = (user.get_full_name() or '').strip()
    except Exception:
        n = ''
    return n or getattr(user, 'username', '') or getattr(user, 'email', '') or ''


# ---------------------------------------------------------------- request side (middleware)
def begin_request(token, user=None):
    _TL.token = clean_token(token)
    _TL.user = _user_name(user) if user is not None else ''
    _TL.job = None


def end_request(ok=True, message=''):
    job = getattr(_TL, 'job', None)
    if job is not None:
        if ok:
            job['status'] = 'done'
            job['done'] = job.get('total', 0)
            job['current'] = ''
        else:
            job['status'] = 'failed'
            job['message'] = (message or 'The calculation stopped with an error – nothing was saved.')[:300]
        job['ended'] = time.time()
        _save(job, force=True)
    _TL.token = None
    _TL.job = None
    _TL.user = ''


class ProgressMiddleware:
    """Reads X-Zeo-Progress; at the end of the request marks the calculation done or failed (the run is saved or rolled back)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tok = request.headers.get('X-Zeo-Progress')
        if not tok:
            return self.get_response(request)
        begin_request(tok, getattr(request, 'user', None))
        ok, msg = False, ''
        try:
            resp = self.get_response(request)
            ok = getattr(resp, 'status_code', 500) < 400
            if not ok:
                try:
                    d = getattr(resp, 'data', None)
                    msg = (d.get('detail') if isinstance(d, dict) else '') or ''
                except Exception:
                    msg = ''
            return resp
        finally:
            end_request(ok, str(msg or ''))


# ---------------------------------------------------------------- payroll side
def _job(run):
    job = getattr(_TL, 'job', None)
    if job is not None:
        return job
    token = getattr(_TL, 'token', None)
    implicit = not token
    if implicit:
        token = 'run%s' % getattr(run, 'pk', '')
        old = None
        try:
            old = cache.get(_key(token))
        except Exception:
            pass
        if old and old.get('status') in ('preparing', 'running', 'finishing') and time.time() - old.get('updated', 0) < STALL:
            job = dict(old)
            _TL.job = job
            job['_implicit'] = True
            return job
    job = {'token': token, 'schema': _schema(), 'run_id': getattr(run, 'pk', None),
           'label': str(getattr(run, 'name', '') or '') or ('%s/%s' % (getattr(run, 'month', ''), getattr(run, 'year', ''))),
           'by': getattr(_TL, 'user', '') or '', 'status': 'preparing', 'total': 0, 'done': 0, 'skipped': 0,
           'current': '', 'started': time.time(), 'ended': None, 'message': '', '_implicit': implicit}
    _TL.job = job
    return job


def add_total(run, n):
    """process_payroll is called once per branch / pay structure / pay window: each call adds its employees."""
    try:
        job = _job(run)
        if not job.get('_fixed'):   # a background job knows its total from the start
            job['total'] = int(job.get('total') or 0) + int(n or 0)
        job['run_id'] = getattr(run, 'pk', job.get('run_id'))
        if run is not None and getattr(run, 'name', ''):
            job['label'] = str(run.name)
        job['status'] = 'running'
        _save(job, force=True)
    except Exception:
        pass


def step(run, employee=None):
    """One employee started (counted whether it is calculated, skipped as already done or left out of the run)."""
    job = getattr(_TL, 'job', None)
    if job is None:
        return
    try:
        job['done'] = min(int(job['done']) + 1, int(job['total']) or int(job['done']) + 1)
        if employee is not None:
            code = getattr(employee, 'emp_code', '') or ''
            name = ' '.join(x for x in [getattr(employee, 'emp_first_name', '') or '', getattr(employee, 'emp_last_name', '') or ''] if x).strip()
            job['current'] = ('%s %s' % (code, name)).strip()
        _save(job, force=job['done'] >= job['total'])
    except Exception:
        pass


def part_done(run):
    """The employees of one process_payroll call are done; with no request to end it (implicit) a finished total is 'done'."""
    job = getattr(_TL, 'job', None)
    if job is None:
        return
    try:
        if job['done'] >= job['total']:
            job['status'] = 'done' if job.get('_implicit') else 'finishing'
            job['current'] = ''
            if job['status'] == 'done':
                job['ended'] = time.time()
        _save(job, force=True)
        if job.get('_implicit'):
            _TL.job = None
    except Exception:
        pass


# ---------------------------------------------------------------- reading
def _public(d):
    if not d:
        return None
    now = time.time()
    total, done = int(d.get('total') or 0), int(d.get('done') or 0)
    started = d.get('started') or now
    end = d.get('ended') or now
    elapsed = max(0.0, end - started)
    rate = done / elapsed if elapsed > 0 and done else 0
    st = d.get('status')
    if st in ('preparing', 'running', 'finishing') and now - (d.get('updated') or now) > STALL:
        st = 'stalled'
    eta = round((total - done) / rate) if rate and st == 'running' and total > done else None
    return {'token': d.get('token'), 'run_id': d.get('run_id'), 'label': d.get('label') or '', 'by': d.get('by') or '',
            'status': st, 'total': total, 'done': done, 'percent': (100 if st == 'done' else (round(done * 100.0 / total, 1) if total else 0)),
            'current': d.get('current') or '', 'elapsed_s': round(elapsed), 'eta_s': eta,
            'per_minute': round(rate * 60) if rate else None, 'message': d.get('message') or '',
            'started': started, 'ended': d.get('ended')}


def get(token):
    t = clean_token(token)
    if not t:
        return None
    try:
        return _public(cache.get(_key(t)))
    except Exception:
        return None


def active():
    """The company's calculations running now, and those ended in the last 10 minutes (newest first)."""
    now = time.time()
    out = []
    try:
        idx = cache.get(_index_key()) or []
    except Exception:
        idx = []
    keep = []
    for t in idx:
        try:
            d = cache.get(_key(t))
        except Exception:
            d = None
        if not d:
            continue
        if d.get('ended') and now - d['ended'] > KEEP_ENDED:
            continue
        if not d.get('ended') and now - (d.get('updated') or 0) > TTL:
            continue
        keep.append(t)
        out.append(_public(d))
    if len(keep) != len(idx):
        try:
            cache.set(_index_key(), keep, TTL)
        except Exception:
            pass
    out.sort(key=lambda j: -(j.get('started') or 0))
    return out
