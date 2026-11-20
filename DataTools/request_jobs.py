"""v1.79.1 – a large upload on a screen with its own import runs in the background, like the list Import.

The list Import (any list) and the punch import have their own background jobs. The other uploads – monthly inputs, opening
balances, salary history, recoveries, pay splits, the month roster, identity details, organisation details, equipment, asset
management, Inventory opening stock, the older bulk uploads of masters, attendance and overtime … – each read the whole file in
one request: a file of a few thousand lines kept the window on "Working…" until the browser or the web server gave up.

Now an upload of more than ZEO_BACKGROUND_UPLOAD_ROWS lines (default 500) to one of these screens is answered at once (202) with
a job, and the same request is sent again in the background (Celery, else a thread – DataTools/import_jobs.py): the screen's own
code does the same test or import, the window shows the progress and gets the screen's own answer when it is done (the
interceptor of the web app, z-job.interceptor.ts) – so the screens show their result as before, green / red, line by line.
Nothing is changed in the screens' imports; a small upload is answered in the request as before.
"""
import json
import logging
import os
import re
import time
import uuid

from django.core.cache import cache
from django.http import JsonResponse

from .import_jobs import _dir, _put, _res_key, dispatch, public, task, Stop, TTL

log = logging.getLogger(__name__)
THRESHOLD = int(os.environ.get('ZEO_BACKGROUND_UPLOAD_ROWS', '500') or 500)
_UPLOAD = re.compile(r'(?:^|/)(?:import|[a-z-]*bulk-?upl[a-z-]*|import-attendance|bulk-upload-salary)(?:/[a-z0-9_-]+)?/?$', re.I)
_OWN_JOBS = re.compile(r'^/(?:tools/api/|employee/api/emp-bulkupload/import|attendance-plus/api/punches/import|zeo-admin/api/import|chatter/)', re.I)
_SHEETS = ('.csv', '.txt', '.xlsx', '.xlsm', '.xls')
_HEADERS = ('HTTP_AUTHORIZATION', 'HTTP_COOKIE', 'HTTP_HOST', 'HTTP_X_LANGUAGE', 'HTTP_ACCEPT_LANGUAGE', 'HTTP_X_ZEO_LOCATION',
            'HTTP_X_ZEO_LOCATIONS', 'HTTP_X_SCHEMA', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_REAL_IP', 'HTTP_USER_AGENT', 'HTTP_ORIGIN',
            'HTTP_X_COMPANY', 'HTTP_X_BRANCH', 'HTTP_X_BRANCHES', 'REMOTE_ADDR')


def is_upload(path):
    return bool(_UPLOAD.search(path or '')) and not _OWN_JOBS.match(path or '')


def _count_sheet(f):
    name = (getattr(f, 'name', '') or '').lower()
    try:
        f.seek(0)
        if name.endswith(('.csv', '.txt')):
            n = sum(1 for line in f if line.strip())
            return max(0, n - 1)
        if name.endswith(('.xlsx', '.xlsm')):
            from openpyxl import load_workbook
            wb = load_workbook(f, read_only=True, data_only=True)
            n = 0
            for ws in wb.worksheets[:1]:
                n = ws.max_row if ws.max_row and ws.max_row > 1 else sum(1 for _ in ws.iter_rows(values_only=True))
            wb.close()
            return max(0, (n or 0) - 1)
        if name.endswith('.xls'):
            import xlrd
            f.seek(0)
            return max(0, xlrd.open_workbook(file_contents=f.read()).sheet_by_index(0).nrows - 1)
    except Exception:   # noqa: BLE001 – the screen itself says what is wrong with the file
        return 0
    finally:
        try:
            f.seek(0)
        except Exception:   # noqa: BLE001
            pass
    return 0


def _json_body(request):
    if 'json' not in (request.META.get('CONTENT_TYPE') or ''):
        return None
    try:
        return json.loads(request.body or b'{}')
    except Exception:   # noqa: BLE001
        return None


def _rows_of(request, body):
    if body is not None:
        rows = body.get('rows') if isinstance(body, dict) else body if isinstance(body, list) else None
        return len(rows) if isinstance(rows, list) else 0
    return max([_count_sheet(f) for f in request.FILES.values() if (f.name or '').lower().endswith(_SHEETS)] or [0])


def _user(request):
    try:
        from rest_framework.request import Request
        from rest_framework.settings import api_settings
        dr = Request(request)
        for cls in api_settings.DEFAULT_AUTHENTICATION_CLASSES:
            res = cls().authenticate(dr)
            if res:
                return res[0]
    except Exception:   # noqa: BLE001 – not signed in / bad token: the view answers as before
        return None
    u = getattr(request, 'user', None)
    return u if getattr(u, 'is_authenticated', False) else None


def _flag(src, *names):
    for n in names:
        if src is not None and n in src:
            v = src.get(n)
            return str(v).lower() in ('1', 'true', 'yes', 'on')
    return None


def _label(path):
    p = [x for x in path.strip('/').split('/') if x and x not in ('api', 'import', 'opening')]
    words = (p[-1] if p else 'upload').replace('-', ' ').replace('_', ' ')
    return words[:1].upper() + words[1:]


def maybe_background(request):
    """the 202 answer with the job when the upload is large, else None (the request goes on as before)"""
    if request.method != 'POST' or request.META.get('HTTP_X_ZEO_JOB') or not is_upload(request.path):
        return None
    body = _json_body(request)
    n = _rows_of(request, body)
    if n <= THRESHOLD:
        return None
    user = _user(request)
    if user is None:
        return None
    from django.db import connection
    schema = request.GET.get('schema') or getattr(connection, 'schema_name', '') or 'public'
    token = uuid.uuid4().hex
    folder = os.path.join(_dir(), f'{schema}-{token}-req')
    os.makedirs(folder, exist_ok=True)
    files = []
    for field in request.FILES:
        for i, f in enumerate(request.FILES.getlist(field)):
            p = os.path.join(folder, f'{len(files)}-{re.sub(r"[^A-Za-z0-9._-]", "_", f.name or "file")[-80:]}')
            with open(p, 'wb') as out:
                for chunk in f.chunks():
                    out.write(chunk)
            files.append({'field': field, 'name': f.name, 'path': p, 'type': getattr(f, 'content_type', '') or 'application/octet-stream'})
    fields = [[k, v] for k in request.POST for v in request.POST.getlist(k)] if body is None else []
    src = body if isinstance(body, dict) else request.POST
    apply_ = _flag(src, 'apply', 'commit')
    dry_ = _flag(src, 'dry_run', 'dry')
    if apply_ is None:
        apply_ = _flag(request.GET, 'apply', 'commit')
    dry = (not apply_) if apply_ is not None else (dry_ if dry_ is not None else False)
    meta = {k: request.META[k] for k in _HEADERS if request.META.get(k)}
    params = {'path': request.path, 'query': request.META.get('QUERY_STRING', ''), 'meta': meta, 'fields': fields, 'files': files,
              'json': body, 'folder': folder}
    job = {'token': token, 'schema': schema, 'user_id': user.pk, 'endpoint': request.path, 'task': 'DataTools.request_jobs:replay', 'params': params,
           'dry_run': dry, 'total': n, 'done': 0, 'ok': 0, 'failed': 0, 'next': 0, 'batch': None, 'status': 'queued', 'message': '',
           'label': _label(request.path), 'page': request.META.get('HTTP_REFERER', '')[-300:].split('#')[-1] if request.META.get('HTTP_REFERER') else '',
           'started': time.time(), 'beat': time.time(), 'ended': None, 'kind': 'request'}
    from .import_jobs import _rows_path
    open(_rows_path(schema, token), 'w').close()   # no rows of its own: the request is sent again as it came
    _put(job)
    dispatch(schema, token)
    out = public(job)
    try:
        from Translations.i18n import tr
    except Exception:   # noqa: BLE001
        def tr(x, *a):
            return x.format(*a)
    msg = tr('The file has {0} lines – it is tested in the background.' if dry else 'The file has {0} lines – it is imported in the background.', f'{n:,}')
    return JsonResponse({'job': out, 'background': True, 'detail': msg}, status=202)


class BackgroundUploadMiddleware:
    """the large uploads of the screens with their own import (see the module) – after the schema, licence and network checks"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            r = maybe_background(request)
        except Exception:   # noqa: BLE001 – never in the way of an upload
            log.exception('background upload')
            r = None
        return r if r is not None else self.get_response(request)


class StopRequest(BaseException):
    """raised by row_done (Stop pressed) inside the screen's code – a BaseException, so the screens' own except Exception
    around a row does not swallow it; their transaction is rolled back"""


@task('DataTools.request_jobs:replay')
def replay(rows, params, ctx):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client
    job = ctx.job
    meta = dict(params.get('meta') or {})
    meta['HTTP_X_ZEO_JOB'] = '1'
    url = params['path'] + (('?' + params['query']) if params.get('query') else '')
    c = Client(enforce_csrf_checks=False, raise_request_exception=False)
    try:
        if params.get('method') == 'GET':   # v1.79.2 a report that takes long
            resp = c.get(url, **meta)
        elif params.get('json') is not None:
            resp = c.post(url, data=json.dumps(params['json']), content_type='application/json', **meta)
        else:
            data = {}
            for k, v in params.get('fields') or []:
                data.setdefault(k, []).append(v)
            for f in params.get('files') or []:
                with open(f['path'], 'rb') as fh:
                    data.setdefault(f['field'], []).append(SimpleUploadedFile(f['name'], fh.read(), content_type=f['type']))
            data = {k: (v[0] if len(v) == 1 else v) for k, v in data.items()}
            resp = c.post(url, data=data, **meta)
    except StopRequest:
        raise Stop()
    finally:
        _clean(params)
    ct = resp.get('Content-Type', '') or ''
    out = {'status': resp.status_code, 'content_type': ct}
    if 'json' in ct:
        try:
            out['data'] = json.loads(resp.content or b'null')
        except Exception:   # noqa: BLE001
            out['data'] = {'detail': resp.content[:500].decode('utf-8', 'replace')}
    else:   # a file (an error file / a document): kept like the exports, downloaded with the job's file
        disp = resp.get('Content-Disposition', '') or ''
        m = re.search(r'filename="?([^";]+)"?', disp)
        name = m.group(1) if m else 'result'
        path = os.path.join(_dir(), f'{job["schema"]}-{job["token"]}-{re.sub(r"[^A-Za-z0-9._-]", "_", name)[-80:]}')
        with open(path, 'wb') as fh:
            fh.write(b''.join(resp.streaming_content) if getattr(resp, 'streaming', False) else resp.content)
        job['file'], job['file_path'] = name, path
        out['file'] = name
    d = out.get('data') if isinstance(out.get('data'), dict) else {}
    ok = d.get('ok') if isinstance(d.get('ok'), int) else None
    bad = d.get('failed') if isinstance(d.get('failed'), int) else d.get('errors') if isinstance(d.get('errors'), int) else (len(d['errors']) if isinstance(d.get('errors'), list) else None)
    if ok is not None:
        job['ok'] = ok
    if bad is not None:
        job['failed'] = bad
    what = job.get('label') or 'Upload'
    if resp.status_code >= 400:
        msg = f'{what}: {(d.get("detail") or d.get("error") or "the file was not accepted")}'
    elif job.get('kind') == 'request' and params.get('method') == 'GET':
        msg = f'{what}: ready.'
    elif job.get('dry_run'):
        msg = f'{what}: test done – {job["total"]:,} lines' + (f', {bad:,} with problems.' if bad else (', all correct.' if bad == 0 else '.'))
    else:
        msg = f'{what}: import done – {job["total"]:,} lines' + (f', {bad:,} not imported.' if bad else '.')
    return dict(out, message=str(msg)[:400])


def _clean(params):
    import shutil
    try:
        shutil.rmtree(params.get('folder') or '', ignore_errors=True)
    except Exception:   # noqa: BLE001
        pass


def stop_requested(job):
    try:
        from .import_jobs import _stop_key
        return bool(cache.get(_stop_key(job['token'], job['schema'])))
    except Exception:   # noqa: BLE001
        return False


def result_of(token, schema=None):
    try:
        return cache.get(_res_key(token, schema))
    except Exception:   # noqa: BLE001
        return None


REPORT_WAIT = float(os.environ.get('ZEO_REPORT_WAIT_SECONDS', '20') or 20)


def get_in_background(request, title, wait=None, report=False, detail='', page=''):
    """v1.79.3 – a long GET (a report, the export of a long list) worked out in a background thread of this server; the request waits for it
    up to `wait` seconds and answers as before when ready, else with a job (202) the screen follows (z-job.interceptor.ts) – an export
    ends with its file. None: run it in this request (asked from the job itself, or the background is off)."""
    wait = REPORT_WAIT if wait is None else wait
    if request.META.get('HTTP_X_ZEO_JOB') or REPORT_WAIT <= 0:
        return None
    from django.db import connection
    from rest_framework.response import Response
    from .import_jobs import _rows_path, get
    user = request.user
    schema = request.query_params.get('schema') or getattr(connection, 'schema_name', '') or 'public'
    token = uuid.uuid4().hex
    meta = {k: request.META[k] for k in _HEADERS if request.META.get(k)}
    params = {'method': 'GET', 'path': request.path, 'query': request.META.get('QUERY_STRING', ''), 'meta': meta, 'fields': [], 'files': [], 'json': None, 'folder': ''}
    job = {'token': token, 'schema': schema, 'user_id': user.pk, 'endpoint': request.path, 'task': 'DataTools.request_jobs:replay', 'params': params,
           'dry_run': False, 'total': 0, 'done': 0, 'ok': 0, 'failed': 0, 'next': 0, 'batch': None, 'status': 'queued', 'message': '',
           'label': str(title)[:80], 'page': page, 'started': time.time(), 'beat': time.time(), 'ended': None, 'kind': 'request', 'report': bool(report),
           'export': not report}
    open(_rows_path(schema, token), 'w').close()
    _put(job)
    dispatch(schema, token, thread=True)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(max(0.005, min(0.15, end - time.time())))
        j = get(token, schema) or {}
        if j.get('status') == 'done':
            res = result_of(token, schema) or {}
            if 'data' in res:
                return Response(res['data'], status=res.get('status') or 200)
            break
        if j.get('status') in ('failed', 'stopped', 'stalled'):
            break
    j = get(token, schema) or job
    if j.get('status') in ('failed', 'stopped'):
        return Response({'detail': j.get('message') or 'The report could not be made.'}, status=500)
    return Response({'job': public(j), 'background': True, 'detail': detail or 'The report is long – it is prepared in the background.'}, status=202)


def report_in_background(request, title):
    """v1.79.2 – a report of the report screen is worked out in a background thread of this server; the request waits for it up to
    REPORT_WAIT seconds (a short report is answered as before). A longer one – a daily attendance of 11,000 employees for three
    months took 112 s, near the 300 s a request may take – is answered with a job (202): the screen shows "Preparing the report…"
    and gets the report when it is ready (z-job.interceptor.ts), then pages through it as before. None: run it in this request."""
    if request.query_params.get('paged') != '1':
        return None
    return get_in_background(request, title, report=True)


__all__ = ['get_in_background', 'report_in_background', 'BackgroundUploadMiddleware', 'StopRequest', 'is_upload', 'maybe_background', 'THRESHOLD', 'TTL']
