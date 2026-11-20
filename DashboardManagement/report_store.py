"""v1.79.1 – a very long report stays on the server.

A report of more than 20,000 rows (the daily attendance of 11,000 employees for three months is about a million rows – 450 MB as
one answer, more than a browser or the web server can hold) is kept for the user in a small SQLite file of the server, and the
screen asks for one page at a time (search and sort in the file). Excel / CSV of every row is written from the file in the
background (DataTools/import_jobs.py) and downloaded when ready. A short report is answered whole, as before.

    GET dashboard/api/reports/<key>/?paged=1       → the first page + {'paged': {'token', 'count'}} when the report is long
    GET dashboard/api/reports/page/<token>/?page=&size=&q=&sort=&dir=
    POST dashboard/api/reports/page/<token>/export/ {'format': 'xlsx' | 'csv'} → a job (count, then the file)
"""
import json
import os
import sqlite3
import time
import uuid

from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from DataTools.import_jobs import _dir, task

PAGED_ROWS = 20000     # more rows than this: kept on the server, one page at a time
TTL = 24 * 3600
MAX_PAGE = 500


def _key(token):
    return f'zeo:repstore:{token}'


def _path(token):
    return os.path.join(_dir(), f'report-{token}.sqlite')


def keep(request, key, data):
    """data: the whole report answer. Writes its rows to the file and returns the answer with the first page only."""
    sweep()
    # v1.79.3 the user's earlier copy of the same report goes (a million-row report is hundreds of MB – kept copies filled the disk)
    last = f'zeo:repstore:last:{request.user.pk}:{key}:{getattr(request, "tenant", None) and request.tenant.schema_name or ""}'
    try:
        old = cache.get(last)
        if old:
            os.remove(_path(old))
            cache.delete(_key(old))
    except (OSError, Exception):   # noqa: BLE001
        pass
    token = uuid.uuid4().hex
    try:
        cache.set(last, token, TTL)
    except Exception:   # noqa: BLE001
        pass
    cols = [c['key'] for c in data['columns']]
    extra = sorted({k for r in data['rows'][:50] for k in r if k.startswith('_') or k not in cols})   # links of the rows (_m, _id …)
    allk = cols + [k for k in extra if k not in cols]
    db = sqlite3.connect(_path(token))
    try:
        db.execute('PRAGMA journal_mode=OFF')
        db.execute('PRAGMA synchronous=OFF')
        db.execute('CREATE TABLE r (n INTEGER PRIMARY KEY, ' + ', '.join(f'c{i}' for i in range(len(allk))) + ', j TEXT, s TEXT)')
        ins = 'INSERT INTO r VALUES (?, ' + ', '.join('?' for _ in allk) + ', ?, ?)'

        def gen():
            for n, row in enumerate(data['rows']):
                vals = [_cell(row.get(k)) for k in allk]
                yield (n, *vals, json.dumps(row, default=str), ' '.join(str(v) for v in vals[:len(cols)] if v not in (None, '')).lower())
        db.executemany(ins, gen())
        db.commit()
    finally:
        db.close()
    meta = {'user_id': request.user.pk, 'key': key, 'cols': allk, 'columns': data['columns'], 'count': len(data['rows']), 'title': data.get('title') or key,
            'made': time.time()}
    cache.set(_key(token), meta, TTL)
    out = dict(data)
    out['rows'] = data['rows'][:100]
    out['paged'] = {'token': token, 'count': len(data['rows']), 'size': 100}
    return out


def _cell(v):
    if v is None or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, bool):
        return int(v)
    return str(v)


def _meta(request, token):
    m = cache.get(_key(token))
    if not m or m['user_id'] != request.user.pk or not os.path.exists(_path(token)):
        return None
    return m


def _query(m, token, q='', sort='', desc=False):
    where, args = '', []
    q = (q or '').strip().lower()
    if q:
        where = 'WHERE s LIKE ?'
        args.append(f'%{q}%')
    order = 'n'
    if sort in m['cols']:
        i = m['cols'].index(sort)
        order = f'c{i} {"DESC" if desc else "ASC"}, n'
    return where, args, order


class ReportPageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, token):
        m = _meta(request, token)
        if m is None:
            return Response({'detail': 'This report is no longer kept – run it again.'}, status=status.HTTP_404_NOT_FOUND)
        size = max(1, min(MAX_PAGE, int(request.query_params.get('size') or 100)))
        page = max(1, int(request.query_params.get('page') or 1))
        where, args, order = _query(m, token, request.query_params.get('q'), request.query_params.get('sort') or '', request.query_params.get('dir') == 'desc')
        db = sqlite3.connect(_path(token))
        try:
            count = db.execute(f'SELECT COUNT(*) FROM r {where}', args).fetchone()[0] if where else m['count']
            rows = [json.loads(x[0]) for x in db.execute(f'SELECT j FROM r {where} ORDER BY {order} LIMIT ? OFFSET ?', args + [size, (page - 1) * size])]
        finally:
            db.close()
        return Response({'rows': rows, 'count': count, 'page': page, 'size': size, 'total': m['count']})

    def post(self, request, token, action='export'):
        from DataTools import import_jobs as J
        m = _meta(request, token)
        if m is None:
            return Response({'detail': 'This report is no longer kept – run it again.'}, status=status.HTTP_404_NOT_FOUND)
        fmt = 'csv' if (request.data or {}).get('format') == 'csv' else 'xlsx'
        q = (request.data or {}).get('q') or ''
        job = J.start_task(request, 'DashboardManagement.report_store:export', [], {'token': token, 'format': fmt, 'q': q, 'sort': (request.data or {}).get('sort') or '',
                                                                                   'dir': (request.data or {}).get('dir') or '', 'page': f'/main-sidebar/report-options/r/{m["key"]}'},
                           label=m['title'], kind='export')
        return Response({'job': J.public(job)}, status=status.HTTP_202_ACCEPTED)


def export_path(schema, token, fmt):
    return os.path.join(_dir(), f'{schema}-{token}.{fmt}')


@task('DashboardManagement.report_store:export')
def export(rows, params, ctx):
    """every row of the kept report (with the search and sort of the screen) as Excel or CSV, written as it is read"""
    m = cache.get(_key(params['token']))
    if not m:
        raise ValueError('The report is no longer kept – run it again.')
    job = ctx.job
    where, args, order = _query(m, params['token'], params.get('q'), params.get('sort') or '', params.get('dir') == 'desc')
    db = sqlite3.connect(_path(params['token']))
    labels = [c['label'] for c in m['columns']]
    keys = [c['key'] for c in m['columns']]
    idx = [m['cols'].index(k) for k in keys]
    fmt = params.get('format') or 'xlsx'
    out = export_path(job['schema'], job['token'], fmt)
    try:
        count = db.execute(f'SELECT COUNT(*) FROM r {where}', args).fetchone()[0] if where else m['count']
        ctx.set_total(count)
        cur = db.execute(f'SELECT {", ".join(f"c{i}" for i in idx)} FROM r {where} ORDER BY {order}', args)
        if fmt == 'csv':
            import csv
            with open(out, 'w', encoding='utf-8-sig', newline='') as fh:
                w = csv.writer(fh)
                w.writerow(labels)
                for row in cur:
                    w.writerow(['' if v is None else v for v in row])
                    ctx.row(True)
        else:
            if count > 1_048_575:
                raise ValueError(f'{count:,} rows are more than one Excel sheet holds (1,048,575) – export CSV, or narrow the report.')
            from openpyxl import Workbook
            wb = Workbook(write_only=True)
            ws = wb.create_sheet((m['title'] or 'Report')[:30])
            ws.append(labels)
            for row in cur:
                ws.append(['' if v is None else v for v in row])
                ctx.row(True)
            wb.save(out)
    finally:
        db.close()
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in (m['title'] or m['key']))[:60]
    job['file'] = f'{safe}-{time.strftime("%Y-%m-%d")}.{fmt}'
    job['file_path'] = out
    return {'file': job['file'], 'rows': count, 'message': f'{m["title"]}: {count:,} rows – the {fmt.upper()} file is ready to download.'}


STORE_MB = int(os.environ.get('ZEO_REPORT_STORE_MB', '4000') or 4000)   # v1.79.3 the kept reports and files together at most (the oldest go first)


def sweep(hours=24):
    cut = time.time() - hours * 3600
    try:
        kept = []
        for f in os.listdir(_dir()):
            p = os.path.join(_dir(), f)
            if f.startswith('report-') or f.endswith(('.xlsx', '.csv', '.pdf')):
                if os.path.getmtime(p) < cut:
                    os.remove(p)
                else:
                    kept.append((os.path.getmtime(p), os.path.getsize(p), p))
        total = sum(k[1] for k in kept)
        for _, size, p in sorted(kept):   # v1.79.3 over the limit: the oldest first
            if total <= STORE_MB * 1024 * 1024:
                break
            os.remove(p)
            total -= size
    except OSError:
        pass
