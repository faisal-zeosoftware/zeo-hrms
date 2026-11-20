"""v1.79.1 – a large list import (Import on any list: employees, documents, leave, salary lines …) runs in the background.

Before, the browser sent the file in parts of 200 rows and waited for each part: the check and the import of 1,000+ rows took
minutes with "Checking the file…" / "Importing…" and nothing else on the screen, every part held one web worker for a minute or
more, and on a slower server a part reached the 300 s limit – the window looked hung and the import stopped half-way.

Now a file of more than 200 rows is one job:
  * the rows are kept in a file of the server (the temporary folder), the job's state in the cache (Redis) – no table;
  * the job runs in the background worker (Celery) when one answers, else in a thread of the web server
    (ZEO_IMPORT_BACKGROUND = auto | celery | thread);
  * it goes through the rows in the same parts of 200 as before (the same checks, the same saves, one Undo for the file), row
    by row, and writes the count – "1,240 of 5,000 rows" – which the window reads every 1.5 s;
  * the window can be closed while it imports: a notice (bell / to-do) says when it is done, with the counts;
  * Stop ends it after the part being saved; a job whose worker stopped (the server restarted) shows as stopped and
    Continue goes on from the next part (the parts saved stay saved – nothing is imported twice).
"""
import json
import logging
import os
import tempfile
import threading
import time
import uuid

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)
PART = 200                      # rows per part (as the window sent them before)
TTL = 2 * 24 * 3600             # the job and its results are kept two days
STALE = 180                     # running without a heartbeat this long (seconds) = the worker stopped
MODE = os.environ.get('ZEO_IMPORT_BACKGROUND', 'auto').lower()
_SAVE_EVERY = 0.5
MAX_JOB_ROWS = 1_000_000   # a background import (the file is sent in pieces, kept line by line, read part by part)
MAX_KEPT_PROBLEMS = 20000   # rows with a problem kept for the window and the error file (all are counted)


def _schema():
    from django.db import connection
    return getattr(connection, 'schema_name', 'public') or 'public'


def _key(token, schema=None):
    return f'zeo:impjob:{schema or _schema()}:{token}'


def _res_key(token, schema=None):
    return f'zeo:impjob:{schema or _schema()}:{token}:res'


def _stop_key(token, schema=None):
    return f'zeo:impjob:{schema or _schema()}:{token}:stop'


def _dir():
    d = getattr(settings, 'ZEO_IMPORT_DIR', '') or os.environ.get('ZEO_IMPORT_DIR', '') or os.path.join(tempfile.gettempdir(), 'zeo-imports')
    os.makedirs(d, exist_ok=True)
    return d


def _rows_path(schema, token):
    return os.path.join(_dir(), f'{schema}-{token}.json')


def _state_path(schema, token):
    return os.path.join(_dir(), f'{schema}-{token}.state')


def get(token, schema=None):
    """the job – from the cache, else from its state file (the cache was emptied: Redis or the server restarted)"""
    try:
        j = cache.get(_key(token, schema))
    except Exception:   # noqa: BLE001
        j = None
    if j is None and token and all(ch in '0123456789abcdef' for ch in token):
        try:
            with open(_state_path(schema or _schema(), token), encoding='utf-8') as fh:
                j = json.load(fh)
        except (OSError, ValueError):
            j = None
    return j


def _put(job):
    try:
        cache.set(_key(job['token'], job['schema']), job, TTL)
    except Exception:   # noqa: BLE001
        log.exception('import job %s: the cache could not be written', job.get('token'))
    try:   # v1.79.1 also on disk – Continue works after a restart of Redis / the server
        tmp = _state_path(job['schema'], job['token']) + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(job, fh, default=str)
        os.replace(tmp, _state_path(job['schema'], job['token']))
    except OSError:
        pass


def public(job):
    """what the window shows; a running job without a heartbeat for STALE seconds = stopped (Continue)"""
    if not job:
        return None
    out = {k: job.get(k) for k in ('token', 'status', 'dry_run', 'total', 'done', 'ok', 'failed', 'batch', 'message', 'label', 'started', 'ended', 'endpoint',
                                    'kind', 'pages', 'file', 'received', 'step', 'step_done', 'step_total', 'report', 'export')}
    out['only_problems'] = not job.get('task')   # v1.79.1 the results of a list import: the rows with a problem (the others are ready / imported)
    if job.get('status') in ('queued', 'running', 'uploading') and time.time() - (job.get('beat') or job.get('started') or 0) > STALE:
        out['status'] = 'stalled'
        out['message'] = 'The import stopped (the server or its background worker restarted). Press Continue to go on from row %s.' % (job.get('next', 0) + 1)
    return out


def results(token, schema=None):
    try:
        return cache.get(_res_key(token, schema)) or []
    except Exception:   # noqa: BLE001
        return []


# ---------------------------------------------------------------------------------------------- start / dispatch
def _write_rows(schema, token, rows, mode='w'):
    with open(_rows_path(schema, token), mode, encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str))
            fh.write('\n')


def _iter_rows(schema, token, start=0):
    with open(_rows_path(schema, token), encoding='utf-8') as fh:
        for i, line in enumerate(fh):
            if i >= start and line.strip():
                yield json.loads(line)


def start(request, endpoint, rows, dry, extra, more=False, total=None):
    """Keeps the rows (one per line), makes the job and starts it – or, with more=True, waits for the other pieces of the file
    (add_rows; the last piece starts it). Returns the job."""
    schema = request.query_params.get('schema', '') or _schema()
    token = uuid.uuid4().hex
    sweep_files()
    _write_rows(schema, token, rows)
    try:
        host = request.get_host()
    except Exception:   # noqa: BLE001
        host = 'localhost'
    job = {'token': token, 'schema': schema, 'user_id': request.user.pk, 'endpoint': endpoint, 'dry_run': bool(dry), 'total': int(total or len(rows)),
           'received': len(rows), 'done': 0, 'ok': 0, 'failed': 0, 'next': 0, 'batch': None, 'status': 'uploading' if more else 'queued', 'message': '',
           'extra': extra or {}, 'host': host, 'label': (extra or {}).get('label') or '', 'page': (extra or {}).get('page') or '', 'started': time.time(),
           'beat': time.time(), 'ended': None, 'branch_ids': request.META.get('HTTP_X_BRANCH_IDS', '')}
    _put(job)
    try:
        cache.set(_res_key(token, schema), [], TTL)
    except Exception:   # noqa: BLE001
        pass
    if not more:
        dispatch(schema, token)
    return job


def add_rows(token, rows, last=False):
    job = get(token)
    if not job:
        raise ValueError('This import is no longer kept – choose the file again.')
    if job.get('status') != 'uploading':
        raise ValueError('This import has all its rows already.')
    _write_rows(job['schema'], token, rows, mode='a')
    job['received'] = job.get('received', 0) + len(rows)
    job['beat'] = time.time()
    if last:
        job['total'] = job['received']
        job['status'] = 'queued'
    _put(job)
    if last:
        dispatch(job['schema'], token)
    return job


def _local_cache():
    return 'locmem' in str(settings.CACHES.get('default', {}).get('BACKEND', '')).lower()


def _worker_alive():
    try:
        v = cache.get('zeo:impworker')
        if v is not None:
            return v == 1
    except Exception:   # noqa: BLE001
        pass
    alive = False
    try:
        from zeo.celery import app as capp
        alive = bool(capp.control.ping(timeout=0.7))
    except Exception:   # noqa: BLE001
        alive = False
    try:
        cache.set('zeo:impworker', 1 if alive else 0, 60)
    except Exception:   # noqa: BLE001
        pass
    return alive


def dispatch(schema, token, thread=False):
    """thread=True: in this process (v1.79.2 a report the screen waits for – not queued behind long imports in the worker)"""
    if not thread and MODE != 'thread' and not _local_cache() and (MODE == 'celery' or _worker_alive()):
        try:
            from .tasks import run_import
            run_import.apply_async(args=[schema, token], retry=False)
            return 'celery'
        except Exception as ex:   # noqa: BLE001 – no broker: the thread below
            log.warning('import %s: the background worker could not be reached (%s) – a thread instead', token, ex)
    threading.Thread(target=execute, args=(schema, token), name=f'zeo-import-{token[:8]}', daemon=True).start()
    return 'thread'


def request_stop(token, schema=None):
    try:
        cache.set(_stop_key(token, schema), 1, TTL)
    except Exception:   # noqa: BLE001
        pass


def resume(token, schema=None):
    job = get(token, schema)
    if not job:
        raise ValueError('This import is no longer kept – choose the file again.')
    p = public(job)
    if p['status'] not in ('stalled', 'stopped', 'failed'):
        raise ValueError('This import is still running.')
    if not os.path.exists(_rows_path(job['schema'], token)):
        raise ValueError('The rows of this import are no longer kept – choose the file again (the rows already imported are skipped as duplicates).')
    try:
        cache.delete(_stop_key(token, job['schema']))
    except Exception:   # noqa: BLE001
        pass
    job.update({'status': 'queued', 'message': '', 'beat': time.time(), 'ended': None})
    job['done'] = job.get('next', 0)
    _put(job)
    dispatch(job['schema'], token)
    return job


# ---------------------------------------------------------------------------------------------- the worker
_ROW = threading.local()
_CARRY = threading.local()


def carry():
    """while a job runs its parts: the duplicates and the in-file links of the rows before (instead of sending them again with
    every part – a 500,000-row file sent 250,000 rows back with its middle part)"""
    return getattr(_CARRY, 'd', None)


def set_total(n):
    """v1.79.3 a job that counts its own rows (the export of a long list): how many there are"""
    j = getattr(_ROW, 'job', None)
    if j is not None:
        j['total'] = int(n)
        _put(j)


def row_done(ok):
    """called by the import after every row (DataTools.views._rows): the count of the running job"""
    j = getattr(_ROW, 'job', None)
    if j is None:
        return
    j['done'] += 1
    j['ok' if ok else 'failed'] += 1
    if j.get('kind') == 'request' and j['done'] % 50 == 0:   # v1.79.1 Stop pressed on a screen's own upload (DataTools/request_jobs.py)
        try:
            stop = cache.get(_stop_key(j['token'], j['schema']))
        except Exception:   # noqa: BLE001
            stop = None
        if stop:
            from .request_jobs import StopRequest
            raise StopRequest()
    now = time.time()
    if now - getattr(_ROW, 'saved', 0) >= _SAVE_EVERY:
        _ROW.saved = now
        j['beat'] = now
        _put(j)
        try:
            cache.set(_ROW.claim, 1, STALE)   # this worker still has the job
        except Exception:   # noqa: BLE001
            pass


def execute(schema, token):
    """Runs (or goes on with) the job: part after part through the import of the list, with the same checks and saves."""
    from django.db import close_old_connections, connection
    from django_tenants.utils import get_tenant_model, schema_context
    close_old_connections()
    job = get(token, schema)
    if not job:
        return
    claim = f'zeo:impjob:{schema}:{token}:claim'
    try:
        if not cache.add(claim, 1, STALE):   # another worker has it (refreshed while it runs)
            return
    except Exception:   # noqa: BLE001
        pass
    if job.get('task'):   # an import of another screen (punches, monthly inputs …)
        try:
            _ROW.claim = claim
            _execute_task(schema, token, job)
        except Exception as ex:   # noqa: BLE001
            log.exception('import task %s', token)
            job.update({'status': 'failed', 'ended': time.time(),
                        'message': (f'The file could not be made ({str(ex)[:200]}). Make it again, or narrow the report.' if job.get('kind') in ('pdf', 'export') else
                                    f'The import stopped after {job.get("done", 0):,} rows: {str(ex)[:200]}. The rows saved stay saved – import the file again to add the rest.')})
            _put(job)
            _notify(job)
        finally:
            _ROW.job = None
            try:
                cache.delete(claim)
                connection.close()
            except Exception:   # noqa: BLE001
                pass
        return
    if not os.path.exists(_rows_path(schema, token)):
        job.update({'status': 'failed', 'message': 'The rows of the import are no longer kept – choose the file again.', 'ended': time.time()})
        _put(job)
        return
    from rest_framework.test import APIRequestFactory, force_authenticate
    from .views import ImportView
    factory = APIRequestFactory()
    total = job['total']
    job.update({'status': 'running', 'beat': time.time()})
    job['done'] = job.get('next', 0)
    _put(job)
    _ROW.job, _ROW.saved, _ROW.claim = job, 0, claim
    _CARRY.d = {}
    lean = lambda r: {k: ('' if isinstance(v, str) and v.startswith('data:') else v) for k, v in (r or {}).items()} if isinstance(r, dict) else r   # noqa: E731
    try:
        with schema_context(schema):
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.get(pk=job['user_id'])
            tenant = get_tenant_model().objects.filter(schema_name=schema).first()
            view = ImportView.as_view()
            s = job.get('next', 0)
            ok_before, failed_before = job.get('ok_done', 0), job.get('failed_done', 0)   # the counts of the parts saved before (a job that goes on)
            job['ok'], job['failed'] = ok_before, failed_before
            first = True
            it = _iter_rows(schema, token, s)
            while s < total:
                if cache.get(_stop_key(token, schema)):
                    job.update({'status': 'stopped', 'message': f'Stopped by you after {s:,} of {total:,} rows.', 'ended': time.time()})
                    _put(job)
                    _notify(job)
                    return
                part = []
                for r in it:
                    part.append(r)
                    if len(part) >= PART:
                        break
                if not part:
                    break
                body = {'endpoint': job['endpoint'], 'rows': part, 'dry_run': job['dry_run'], 'row_offset': s,
                        **{k: v for k, v in (job.get('extra') or {}).items() if k not in ('page',)}}
                if job['dry_run'] and s and first:   # a check that goes on: the rows before, once
                    body['prior_rows'] = [lean(r) for r in _iter_rows(schema, token) if r is not None][:s]
                first = False
                if not job['dry_run'] and job.get('batch'):
                    body['batch'] = job['batch']
                req = factory.post(f'/tools/api/import/?schema={schema}', body, format='json', HTTP_HOST=job.get('host') or 'localhost')
                force_authenticate(req, user=user)
                req.tenant = tenant
                job['done'] = s
                resp = view(req)
                data = getattr(resp, 'data', None) or {}
                if resp.status_code >= 400:
                    raise RuntimeError(str(data.get('detail') if isinstance(data, dict) else data)[:300] or f'HTTP {resp.status_code}')
                if data.get('batch'):
                    job['batch'] = data['batch']
                bad = [x for x in (data.get('results') or []) if not x.get('ok')]
                if bad:
                    kept = results(token, schema)
                    if len(kept) < MAX_KEPT_PROBLEMS:
                        kept.extend(bad[:MAX_KEPT_PROBLEMS - len(kept)])
                        cache.set(_res_key(token, schema), kept, TTL)
                ok_before += data.get('ok', 0)
                failed_before += data.get('failed', 0)
                job['ok'], job['failed'] = ok_before, failed_before
                job['ok_done'], job['failed_done'] = ok_before, failed_before
                s += len(part)
                job['next'] = s
                job['done'] = min(s, total)
                job['beat'] = time.time()
                _put(job)
                close_old_connections()
            job.update({'status': 'done', 'done': total, 'ended': time.time(),
                        'message': (f'{job["ok"]:,} rows ready, {job["failed"]:,} need fixing.' if job['dry_run']
                                    else f'{job["ok"]:,} of {total:,} rows imported' + (f', {job["failed"]:,} not imported.' if job['failed'] else '.'))})
            _put(job)
            _notify(job)
            if not job['dry_run']:
                _cleanup(schema, token)
    except Exception as ex:   # noqa: BLE001
        log.exception('import job %s', token)
        job.update({'status': 'failed', 'message': f'The import stopped at row {job.get("next", 0) + 1:,}: {str(ex)[:200]} – press Continue to go on.', 'ended': time.time()})
        _put(job)
        _notify(job)
    finally:
        _ROW.job = None
        _CARRY.d = None
        try:
            cache.delete(claim)
        except Exception:   # noqa: BLE001
            pass
        try:
            connection.close()
        except Exception:   # noqa: BLE001
            pass


def _cleanup(schema, token):
    try:
        os.remove(_rows_path(schema, token))
    except OSError:
        pass


def _notify(job):
    """bell / to-do for the user who imported – only for the import (the check is read in the window)"""
    if job.get('dry_run'):
        return
    if job.get('report') or job.get('export'):   # v1.79.2 a report the screen waited for: a notice only when it took longer than the wait
        from .request_jobs import REPORT_WAIT
        if (job.get('ended') or time.time()) - job.get('started', 0) <= REPORT_WAIT + 2:
            return
    try:
        from Chatter.models import InboxItem
        what = job.get('label') or 'Import'
        ok = job.get('status') == 'done'
        if job.get('report'):
            key = (job.get('endpoint') or '').rstrip('/').rsplit('/', 1)[-1]
            InboxItem.objects.create(user_id=job['user_id'], kind='reminder', model='DataTools.Job', object_id=job['token'][:40], record_label=what[:255],
                                     page=f'/main-sidebar/report-options/r/{key}' + (f'?reportjob={job["token"]}' if ok else ''),
                                     title=(f'Report ready: {what}' if ok else f'Report stopped: {what}')[:255], body=job.get('message') or '', from_name='ZEO')
            return
        if job.get('export') and job.get('kind') == 'request':   # v1.79.3 the export of a long list: the notice downloads the file
            sep = '&' if '?' in (job.get('page') or '') else '?'
            InboxItem.objects.create(user_id=job['user_id'], kind='reminder', model='DataTools.Job', object_id=job['token'][:40], record_label=what[:255],
                                     page=(job.get('page') or '/main-sidebar/zeo-apps') + (f'{sep}exportjob={job["token"]}' if ok else ''),
                                     title=(f'Export ready: {what}' if ok else f'Export stopped: {what}')[:255], body=job.get('message') or '', from_name='ZEO')
            return
        if job.get('kind') in ('pdf', 'export'):
            w = 'PDF' if job.get('kind') == 'pdf' else 'Export'
            InboxItem.objects.create(user_id=job['user_id'], kind='reminder', model='DataTools.Job', object_id=job['token'][:40], record_label=what[:255],
                                     page=(job.get('page') or '/main-sidebar/report-options') + (f'?pdfjob={job["token"]}' if ok else ''),
                                     title=(f'{w} ready: {what}' if ok else f'{w} stopped: {what}')[:255], body=job.get('message') or '', from_name='ZEO')
            return
        InboxItem.objects.create(user_id=job['user_id'], kind='reminder', model='DataTools.Import', object_id=job['token'][:40],
                                 record_label=what[:255], page=job.get('page') or '/main-sidebar/settings/import-history',
                                 title=(f'Import finished: {what}' if ok else f'Import stopped: {what}')[:255], body=job.get('message') or '', from_name='ZEO')
    except Exception:   # noqa: BLE001
        log.exception('import notice')


def sweep_files(days=2):
    """files of jobs older than the two days a job is kept: rows, PDFs, Excel / CSV exports, kept reports (called when a job starts)"""
    try:
        cut = time.time() - days * 86400
        for f in os.listdir(_dir()):
            p = os.path.join(_dir(), f)
            if f.endswith(('.json', '.pdf', '.xlsx', '.csv', '.sqlite', '.state')) and os.path.getmtime(p) < cut:
                os.remove(p)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------- other imports (v1.79.1)
# Screens with their own import (attendance punches, monthly payroll inputs, rosters …) run the save of a large file as a job
# too: the screen's own import function, given the rows, with `on_row` for the count. Registered here by name.
TASKS = {}


def task(name):
    def reg(fn):
        TASKS[name] = fn
        return fn
    return reg


class Stop(Exception):
    pass


class Ctx:
    """what a task gets: the user, set_total(n) when the rows are expanded, row(ok) after every row (raises Stop when asked)"""

    def __init__(self, job, user):
        self.job, self.user = job, user

    def set_total(self, n):
        self.job['total'] = int(n)
        _put(self.job)

    def row(self, ok=True):
        row_done(ok)
        if self.job['done'] % 50 == 0 and cache.get(_stop_key(self.job['token'], self.job['schema'])):
            raise Stop()


def start_task(request, name, rows, params=None, label='', kind='import', dry=False):
    """rows: the rows read from the file; params: what the task needs besides (JSON-able). Returns the job."""
    schema = request.query_params.get('schema', '') or _schema()
    token = uuid.uuid4().hex
    _write_rows(schema, token, rows)
    job = {'token': token, 'schema': schema, 'user_id': request.user.pk, 'endpoint': '', 'task': name, 'params': params or {}, 'dry_run': bool(dry),
           'total': len(rows), 'done': 0, 'ok': 0, 'failed': 0, 'next': 0, 'batch': None, 'status': 'queued', 'message': '',
           'label': label, 'page': (params or {}).get('page') or '', 'started': time.time(), 'beat': time.time(), 'ended': None, 'kind': kind}
    _put(job)
    dispatch(schema, token)
    return job


def _execute_task(schema, token, job):
    from django.contrib.auth import get_user_model
    from django_tenants.utils import schema_context
    rows = list(_iter_rows(schema, token))
    fn = TASKS.get(job['task'])
    if fn is None:
        __import__(job['task'].split(':')[0])   # 'Module.path:name' – the module registers the task
        fn = TASKS[job['task']]
    job.update({'status': 'running', 'beat': time.time(), 'done': 0, 'ok': 0, 'failed': 0})
    _put(job)
    _ROW.job, _ROW.saved = job, 0
    with schema_context(schema):
        user = get_user_model().objects.get(pk=job['user_id'])
        try:
            result = fn(rows, job.get('params') or {}, Ctx(job, user))
            job.update({'status': 'done', 'done': job['total'], 'ended': time.time(), 'message': (result or {}).get('message') or f'{job["ok"]:,} of {job["total"]:,} rows done.'})
        except Stop:
            result = None
            job.update({'status': 'stopped', 'ended': time.time(),
                        'message': ('Stopped by you – no PDF was made.' if job.get('kind') == 'pdf' else
                                    f'Stopped by you after {job["done"]:,} of {job["total"]:,} rows. The rows saved stay saved – import the file again to add the rest (they are skipped as already there).')})
        try:
            cache.set(_res_key(token, schema), result, TTL)
        except Exception:   # noqa: BLE001
            log.exception('task result')
        _put(job)
        _notify(job)
        _cleanup(schema, token)
