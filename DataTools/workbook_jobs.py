"""v1.62.0 workbook – Settings › Data workbook: upload a data workbook, Check it (a trial load that is rolled back), Load it in
the background with live progress, resume, history, error report.

The engine is the loader of `python manage.py load_workbook` (DataTools/management/commands/load_workbook.py).

Where it runs (settings.ZEO_WORKBOOK_RUNNER, default "auto"):
  celery  – a Celery worker (task DataTools.tasks.run_workbook) when a worker answers and tasks are not run eagerly;
  process – otherwise a process of its own (manage.py run_workbook), so a restart of the web server does not stop it;
  thread  – when no process can be started, a background thread of the web server process.
A run that stops sending its heartbeat (the server restarted) shows as "Interrupted" – Load again (or with "from month"):
rows that were loaded are remembered (WorkbookRow) and skipped, so a second load never makes doubles.
`python manage.py run_workbook <run id> --schema <company>` runs a waiting run from the command line.
"""
import datetime
import hashlib
import io
import logging
import os
import tempfile
import threading
import time
import traceback

from django.conf import settings
from django.db import IntegrityError, connection, connections, transaction
from django.utils import timezone

log = logging.getLogger(__name__)
STALE_MINUTES = 10           # running without a heartbeat for this long → interrupted
QUEUE_FALLBACK_SECONDS = 90  # waiting for a Celery worker this long → run in a thread instead
MAX_BYTES = 30 * 1024 * 1024   # the upload check (zeo/uploadguard.py) allows Office files up to 30 MB
TEMPLATE_NAME = 'ZEO_Client_Data_Template_1.62.0.xlsx'
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), 'workbook_template', TEMPLATE_NAME)


def M(name):
    from django.apps import apps
    return apps.get_model('DataTools', name)


# ---------------------------------------------------------------------------------------------- upload
def save_upload(f, user):
    """f: an uploaded .xlsx. Reads its Index (sheets, rows, months). Raises ValueError with a message for the user."""
    name = (getattr(f, 'name', '') or 'workbook.xlsx')[:255]
    if not name.lower().endswith(('.xlsx', '.xlsm')):
        raise ValueError('Upload an Excel workbook (.xlsx) in the ZEO data workbook format – start from the template.')
    if f.size > MAX_BYTES:
        raise ValueError('The workbook is larger than 30 MB. Split it into two workbooks (setup data, then the history).')
    raw = f.read()
    from django.core.files.base import ContentFile
    sheets = summary_of(raw)
    up = M('WorkbookUpload')(file_name=os.path.basename(name), size=len(raw), sha1=hashlib.sha1(raw).hexdigest(), sheets=sheets, uploaded_by=user)
    up.file.save(os.path.basename(name), ContentFile(raw), save=False)
    up.save()
    return up


def summary_of(raw):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise ValueError('The file could not be opened as an Excel workbook (.xlsx). Save it again from Excel as "Excel Workbook (.xlsx)".')
    names = list(wb.sheetnames)
    index = next((n for n in names if n.strip().lower() == 'index'), None)
    if index is None:
        raise ValueError('The workbook has no "Index" sheet. Start from the template (Download the template) – its Index lists the sheets to load, in order.')
    by_low = {n.strip().lower(): n for n in names}
    it = wb[index].iter_rows(values_only=True)
    hdr = [str(h or '').strip().lower() for h in next(it, [])]
    out = []
    for r in it:
        d = dict(zip(hdr, r))
        if not d.get('sheet'):
            continue
        sheet = str(d['sheet']).strip()
        real = sheet if sheet in names else by_low.get(sheet.lower())
        rows, months, examples = 0, set(), 0
        if real:
            rws = wb[real].iter_rows(values_only=True)
            h = [str(x or '').strip() for x in next(rws, [])]
            mi = h.index('@month') if '@month' in h else None
            ei = h.index('@example') if '@example' in h else None
            for row in rws:
                if row is None or all(v in (None, '') for v in row):
                    continue
                if ei is not None and ei < len(row) and row[ei] not in (None, ''):
                    examples += 1
                    continue
                rows += 1
                if mi is not None and mi < len(row) and row[mi]:
                    months.add(str(row[mi])[:7])
        out.append({'sheet': sheet, 'found': bool(real), 'how': str(d.get('how') or 'import').strip().lower(),
                    'phase': str(d.get('phase') or 'setup').strip().lower(), 'screen': str(d.get('screen') or ''), 'rows': rows,
                    'examples': examples, 'months': sorted(months), 'mandatory': str(d.get('mandatory') or '').strip().lower().startswith(('y', 'go'))})
    return out


# ---------------------------------------------------------------------------------------------- start a run
def start(upload, kind, user, options=None, schema=None):
    """kind: check | quick | load. Returns the run (queued and handed to Celery or a thread)."""
    schema = schema or connection.schema_name
    Run = M('WorkbookRun')
    refresh_states(schema)
    busy = Run.objects.filter(state__in=('queued', 'running')).first()
    if busy is not None:
        raise ValueError(f'A {busy.get_kind_display().lower()} of "{busy.upload.file_name}" is still running – wait for it or stop it.')
    run = Run.objects.create(upload=upload, kind=kind, created_by=user, options=options or {},
                             progress={'done': 0, 'total': sum(s.get('rows', 0) for s in upload.sheets or []), 'sheet': '', 'month': ''})
    dispatch(run, schema)
    return run


_celery_seen = {'at': 0.0, 'ok': False}


def celery_ready():
    """A Celery worker answers (and tasks are not run eagerly inside the request)."""
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        return False
    if time.time() - _celery_seen['at'] < 60:
        return _celery_seen['ok']
    ok = False
    try:
        from zeo.celery import app
        with app.connection_for_write() as conn:
            conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0, timeout=2)
        ok = bool(app.control.inspect(timeout=1.5).ping())
    except Exception:   # noqa: BLE001 – no broker / no worker
        ok = False
    _celery_seen.update(at=time.time(), ok=ok)
    return ok


def dispatch(run, schema):
    """celery (a worker answers) → else a process of its own (survives a restart of the web server) → else a thread."""
    how = str(getattr(settings, 'ZEO_WORKBOOK_RUNNER', '') or os.environ.get('ZEO_WORKBOOK_RUNNER', '') or 'auto').lower()
    if how in ('auto', 'celery') and celery_ready():
        try:
            from .tasks import run_workbook
            run_workbook.apply_async((schema, run.pk), ignore_result=True)
            M('WorkbookRun').objects.filter(pk=run.pk).update(runner='celery')
            return
        except Exception:   # noqa: BLE001
            log.exception('workbook: Celery not reachable – a process runs it')
    if how in ('auto', 'celery', 'process'):
        try:
            start_process(run.pk, schema)
            return
        except Exception:   # noqa: BLE001
            log.exception('workbook: no process could be started – a thread runs it')
    start_thread(run.pk, schema)


def start_process(run_id, schema):
    """`manage.py run_workbook <run> --schema <company>` as a process of its own (same settings as the web server).
    ZEO_WORKBOOK_CMD_PREFIX (optional) is put in front, e.g. "nice -n 10"."""
    import shlex
    import subprocess
    import sys
    manage = os.path.join(str(settings.BASE_DIR), 'manage.py')
    cmd = shlex.split(os.environ.get('ZEO_WORKBOOK_CMD_PREFIX', '')) + [sys.executable, manage, 'run_workbook', str(run_id), '--schema', schema]
    env = dict(os.environ)
    env.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', 'zeo.settings'))
    logf = open(os.path.join(tempfile.gettempdir(), f'zeo-workbook-{schema}-{run_id}.log'), 'ab')
    p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                         cwd=str(settings.BASE_DIR), env=env)
    logf.close()
    M('WorkbookRun').objects.filter(pk=run_id).update(runner=f'process {p.pid}')


def start_thread(run_id, schema, why='thread'):
    M('WorkbookRun').objects.filter(pk=run_id).update(runner=why)
    threading.Thread(target=execute, args=(schema, run_id), kwargs={'own_thread': True}, daemon=True,
                     name=f'workbook-{schema}-{run_id}').start()


def _gone(runner):
    """The process of a 'process <pid>' runner has ended (on this server)."""
    try:
        pid = int(str(runner).split()[1])
    except (IndexError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except Exception:   # noqa: BLE001
        return False
    try:   # a zombie (ended, not yet collected by the web server) counts as ended
        with open(f'/proc/{pid}/stat') as fh:
            return fh.read().split(')')[-1].split()[0] == 'Z'
    except OSError:
        return False


def refresh_states(schema=None):
    """Runs whose worker went away → interrupted; runs a Celery worker never picked up → a process."""
    Run = M('WorkbookRun')
    now = timezone.now()
    msg = 'The run stopped without finishing (the server or the worker restarted). Press Load again: the rows already loaded are skipped.'
    for r in Run.objects.filter(state__in=('queued', 'running')):
        if r.state == 'running' and r.heartbeat_at and (now - r.heartbeat_at).total_seconds() > STALE_MINUTES * 60:
            Run.objects.filter(pk=r.pk, state='running').update(state='interrupted', finished_at=now, message=msg)
        elif str(r.runner).startswith('process ') and _gone(r.runner) and (now - r.created_at).total_seconds() > 20:
            Run.objects.filter(pk=r.pk, state__in=('queued', 'running')).update(state='interrupted', finished_at=now, message=msg)
        elif r.state == 'queued' and r.runner == 'celery' and (now - r.created_at).total_seconds() > QUEUE_FALLBACK_SECONDS:
            try:
                start_process(r.pk, schema or connection.schema_name)
            except Exception:   # noqa: BLE001
                start_thread(r.pk, schema or connection.schema_name, why='thread (no Celery worker answered)')


def request_stop(run):
    M('WorkbookRun').objects.filter(pk=run.pk, state__in=('queued', 'running')).update(stop_requested=True)


# ---------------------------------------------------------------------------------------------- the run itself
def _side_alias():
    """A second database connection for the progress of a trial (the trial's own connection is inside a transaction
    that is rolled back – its writes are not seen by the screen)."""
    alias = 'workbook_progress'
    if alias not in connections.settings:
        cfg = dict(connections.settings['default'])
        connections.settings[alias] = cfg
    return alias


class Progress:
    def __init__(self, schema, run_id, L, side=False):
        self.schema, self.run_id, self.L = schema, run_id, L
        self.alias = _side_alias() if side else 'default'
        self.t = 0.0
        self.t0 = time.time()
        self.done = 0
        self.total = 0
        self.sheet = ''
        self.month = ''
        self.stop = False

    def _q(self):
        from django_tenants.utils import schema_context
        if self.alias == 'default':
            return schema_context(self.schema)
        c = connections[self.alias]
        c.set_schema(self.schema)
        from contextlib import nullcontext
        return nullcontext()

    def row(self, sheet):
        self.done += 1
        if time.time() - self.t > 2:
            self.flush()

    def on_sheet(self, sheet, month, n):
        self.sheet, self.month = sheet, month
        self.flush()

    def state(self):
        tot = {k: sum(v.get(k, 0) for v in self.L.stats.values()) for k in ('ok', 'failed', 'skipped', 'past')}
        el = time.time() - self.t0
        eta = int(el / self.done * (self.total - self.done)) if self.done and self.total > self.done else 0
        return {'done': self.done, 'total': self.total, 'sheet': self.sheet, 'month': self.month, 'elapsed_s': int(el), 'eta_s': eta, **tot}

    def flush(self):
        self.t = time.time()
        try:
            with self._q():
                Run = M('WorkbookRun')
                upd = {'progress': self.state(), 'heartbeat_at': timezone.now()}
                if len(self.L.log) != getattr(self, '_nlog', 0):   # the rows that fail so far (the screen shows them while it runs)
                    self._nlog = len(self.L.log)
                    upd['errors'] = self.L.log[:300]
                Run.objects.using(self.alias).filter(pk=self.run_id).update(**upd)
                self.stop = bool(Run.objects.using(self.alias).filter(pk=self.run_id).values_list('stop_requested', flat=True).first())
        except Exception:   # noqa: BLE001 – progress is best effort
            log.exception('workbook progress')

    def should_stop(self):
        return self.stop


class Memory:
    """Rows loaded by earlier loads of this company (any file): a row with the same values is skipped."""
    def __init__(self, schema, run_id, write=True):
        from django_tenants.utils import schema_context
        self.schema, self.run_id, self.write = schema, run_id, write
        with schema_context(schema):
            self.d = {(s, d): (rid, ref) for s, d, rid, ref in M('WorkbookRow').objects.values_list('sheet', 'digest', 'record_id', 'ref')}

    def refs(self):
        return {ref: _id(rid) for (s, d), (rid, ref) in self.d.items() if ref and rid}

    def get(self, sheet, digest):
        hit = self.d.get((sheet[:64], digest))
        return (_id(hit[0]), hit[1]) if hit else None

    def add(self, sheet, digest, rid, ref):
        if not self.write:
            return
        from django_tenants.utils import schema_context
        self.d[(sheet[:64], digest)] = (str(rid or ''), ref or '')
        try:
            with schema_context(self.schema), transaction.atomic():
                M('WorkbookRow').objects.create(sheet=sheet[:64], digest=digest, record_id=str(rid or '')[:64], ref=(ref or '')[:200], run_id=self.run_id)
        except IntegrityError:
            pass


def _id(v):
    s = str(v or '')
    return int(s) if s.isdigit() else (s or None)


def guard_for(requester, schema):
    """@as only for logins of this company that are not system administrators (unless a system administrator loads it)."""
    def may(u):
        if requester is not None and requester.is_superuser:
            return None
        if u.is_superuser and (requester is None or u.pk != requester.pk):
            return f'The workbook may not act as "{u.username}" – a system administrator.'
        if not u.tenants.filter(schema_name=schema).exists():
            return f'The workbook may not act as "{u.username}" – the login is not a user of this company.'
        return None
    return may


def execute(schema, run_id, own_thread=False):
    """Run a queued check / load (Celery task, thread or the run_workbook command)."""
    from django.db import close_old_connections
    from django_tenants.utils import schema_context
    if own_thread:
        close_old_connections()
    Run = M('WorkbookRun')
    with schema_context(schema):
        if not Run.objects.filter(pk=run_id, state='queued').update(state='running', started_at=timezone.now(), heartbeat_at=timezone.now()):
            return   # already taken by another runner
        run = Run.objects.select_related('upload', 'created_by').get(pk=run_id)
        upload = run.upload
    tmp = None
    L = None
    stopped = False
    try:
        from DataTools.management.commands.load_workbook import Loader, read_book, run_book, totals, months_of, Stopped
        from django.core.management.base import CommandError
        with schema_context(schema):
            with upload.file.open('rb') as fh:
                raw = fh.read()
        fd, tmp = tempfile.mkstemp(suffix='.xlsx', prefix='zeo-wb-')
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
        trial, quick = run.kind == 'check', run.kind == 'quick'
        user = run.created_by.username if run.created_by else ''
        L = Loader(None, schema, user, check=quick, verbose=0, trial=trial,
                   may_act_as=guard_for(run.created_by, schema))
        mem = Memory(schema, run.pk, write=(run.kind == 'load'))
        L.memory = mem
        L.refs.update(mem.refs())
        L.block = 100
        prog = Progress(schema, run.pk, L, side=trial)
        L.on_row, L.should_stop = prog.row, prog.should_stop
        only = set(run.options.get('only') or [])
        fm = str(run.options.get('from_month') or '')[:7]
        try:
            specs, data = read_book(tmp, L, only or None)
        except CommandError as ex:
            raise ValueError(str(ex))
        mset = set(months_of(specs, data, fm))
        prog.total = sum(len(data[s['sheet']]) for s in specs if s['phase'] != 'monthly') + \
            sum(1 for s in specs if s['phase'] == 'monthly' for _, r in data[s['sheet']] if str(r.get('@month'))[:7] in mset or (not r.get('@month') and not fm))
        prog.flush()
        try:
            if trial:
                from zeo import mailguard
                eager = _celery_eager(True)
                try:
                    with mailguard.suppressed(), transaction.atomic():
                        try:
                            run_book(L, specs, data, fm, on_sheet=prog.on_sheet)
                        finally:
                            transaction.set_rollback(True)
                finally:
                    _celery_eager(eager)
            else:
                run_book(L, specs, data, fm, on_sheet=prog.on_sheet)
        except Stopped:
            stopped = True
        prog.alias = 'default'
        tot = totals(L)
        state = 'stopped' if stopped else 'done'
        msg = _summary(run.kind, tot, stopped)
        with schema_context(schema):
            Run.objects.filter(pk=run.pk).update(state=state, finished_at=timezone.now(), heartbeat_at=timezone.now(), stats=L.stats,
                                                 totals=tot, errors=L.log[:20000], message=msg, progress=prog.state())
    except Exception as ex:   # noqa: BLE001
        log.exception('workbook run %s', run_id)
        text = str(ex) if isinstance(ex, ValueError) else f'{type(ex).__name__}: {ex}\n' + traceback.format_exc()[-1500:]
        try:
            close_old_connections()
            with schema_context(schema):
                Run.objects.filter(pk=run_id).update(state='failed', finished_at=timezone.now(), message=text[:4000],
                                                     stats=L.stats if L else {}, errors=(L.log[:20000] if L else []))
        except Exception:   # noqa: BLE001
            log.exception('workbook run %s: state not saved', run_id)
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        try:
            if 'workbook_progress' in connections.settings:
                connections['workbook_progress'].close()
        except Exception:   # noqa: BLE001
            pass
        if own_thread:
            close_old_connections()


def _celery_eager(on):
    """Tasks the trial starts run inside it (a worker could not see the trial's records). Returns the old value."""
    try:
        from zeo.celery import app
        old = bool(app.conf.task_always_eager)
        app.conf.task_always_eager = bool(on)
        return old
    except Exception:   # noqa: BLE001
        return False


def _summary(kind, tot, stopped):
    if kind in ('check', 'quick'):
        s = f'{tot["ok"]} rows would load, {tot["skipped"]} are already there, {tot["failed"]} would fail.'
        if not tot['failed'] and not tot.get('problems'):
            s = 'Clean: ' + s + ' Nothing was saved – press Load.'
        else:
            s = 'Nothing was saved. ' + s + ' Download the error report, correct the rows and check again.'
    else:
        s = f'{tot["ok"]} rows loaded, {tot["skipped"]} already there, {tot["failed"]} failed.'
        if tot['failed']:
            s += ' Correct the failed rows (error report) and load the file again – the rows already loaded are skipped.'
    if tot.get('past'):
        s += f' {tot["past"]} rows left out – their day has passed.'
    if tot.get('examples'):
        s += f' {tot["examples"]} example rows ignored.'
    if stopped:
        s = 'Stopped. ' + s
    return s


# ---------------------------------------------------------------------------------------------- answers
def run_json(r, full=False):
    p = r.progress or {}
    total, done = p.get('total') or 0, p.get('done') or 0
    errs = r.errors or []
    sheets = []
    for name, st in (r.stats or {}).items():
        sheets.append({'sheet': name, 'ok': st.get('ok', 0), 'failed': st.get('failed', 0), 'skipped': st.get('skipped', 0),
                       'examples': st.get('examples', 0), 'past': st.get('past', 0),
                       'problems': sum(1 for e in errs if e.get('sheet') == name and e.get('kind') == 'sheet')})
    for e in errs:   # sheets with only a problem of the sheet (not in the stats)
        if e.get('kind') == 'sheet' and not any(s['sheet'] == e['sheet'] for s in sheets):
            sheets.append({'sheet': e['sheet'], 'ok': 0, 'failed': 0, 'skipped': 0, 'examples': 0, 'past': 0,
                           'problems': sum(1 for x in errs if x.get('sheet') == e['sheet'] and x.get('kind') == 'sheet')})
    out = {'id': r.pk, 'upload': r.upload_id, 'kind': r.kind, 'kind_text': r.get_kind_display(), 'state': r.state, 'state_text': r.get_state_display(),
           'runner': r.runner, 'by': (r.created_by.get_full_name() or r.created_by.username) if r.created_by else '',
           'created_at': r.created_at, 'started_at': r.started_at, 'finished_at': r.finished_at, 'options': r.options,
           'progress': {**p, 'percent': int(done * 100 / total) if total else (100 if r.state == 'done' else 0)},
           'totals': r.totals or {}, 'message': r.message, 'sheets': sheets, 'error_count': len(errs),
           'errors': errs if full else errs[:300], 'stop_requested': r.stop_requested}
    return out


def upload_json(u, runs=True, full=False):
    d = {'id': u.pk, 'file_name': u.file_name, 'size': u.size, 'sheets': u.sheets, 'rows': sum(s.get('rows', 0) for s in u.sheets or []),
         'uploaded_at': u.uploaded_at, 'uploaded_by': (u.uploaded_by.get_full_name() or u.uploaded_by.username) if u.uploaded_by else ''}
    if runs:
        rs = list(u.runs.select_related('created_by').all()[:30])
        d['run'] = run_json(rs[0], full) if rs else None
        d['runs'] = [{k: v for k, v in run_json(x).items() if k not in ('errors', 'sheets')} for x in rs]
    return d


# ---------------------------------------------------------------------------------------------- error report
def error_report(run):
    """An .xlsx: Summary, Problems (every reason with its fix), and each sheet with its failing rows exactly as in the file plus
    @reason / @fix – with Index and Columns, so it can be corrected and uploaded again (only the failed rows load)."""
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    navy, red, amber = '1B1640', 'FDE2DF', 'FFF1CC'
    errs = run.errors or []
    with run.upload.file.open('rb') as fh:
        src = load_workbook(io.BytesIO(fh.read()), read_only=True, data_only=False)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Summary'
    head = Font(bold=True, color='FFFFFF')
    fill = PatternFill('solid', fgColor=navy)

    def header(w, cols, widths):
        w.append(cols)
        for i, c in enumerate(w[w.max_row], start=1):
            c.font, c.fill = head, fill
            c.alignment = Alignment(vertical='center', wrap_text=True)
            w.column_dimensions[get_column_letter(i)].width = widths[i - 1] if i - 1 < len(widths) else 18
        w.freeze_panes = w.cell(row=w.max_row + 1, column=1)
    ws.append(['ZEO data workbook – ' + ('check' if run.kind != 'load' else 'load') + ' report'])
    ws['A1'].font = Font(bold=True, size=15, color=navy)
    ws.append(['File', run.upload.file_name])
    ws.append(['When', timezone.localtime(run.finished_at or run.created_at).strftime('%d/%m/%Y %H:%M')])
    ws.append(['By', (run.created_by.get_full_name() or run.created_by.username) if run.created_by else ''])
    ws.append(['Result', run.message or run.get_state_display()])
    ws.append([])
    header(ws, ['Sheet', 'Loaded / would load', 'Already there', 'Failed', 'Example rows', 'Problems of the sheet'], [34, 18, 14, 10, 14, 20])
    for s in run_json(run, full=True)['sheets']:
        ws.append([s['sheet'], s['ok'], s['skipped'], s['failed'], s['examples'], s['problems']])
        if s['failed'] or s['problems']:
            for c in ws[ws.max_row]:
                c.fill = PatternFill('solid', fgColor=red)
    pr = wb.create_sheet('Problems')
    header(pr, ['Sheet', 'Excel row', 'Column', 'Value', 'Reason', 'How to fix it'], [28, 10, 26, 24, 70, 60])
    for e in errs:
        pr.append([e.get('sheet'), e.get('row') or '', (f"{e.get('letter')} – " if e.get('letter') else '') + str(e.get('column') or ''),
                   e.get('value') or '', e.get('error'), e.get('fix') or ''])
        for c in pr[pr.max_row]:
            c.alignment = Alignment(wrap_text=True, vertical='top')
            if e.get('level') == 'warning':
                c.fill = PatternFill('solid', fgColor=amber)
    # the failing rows of each sheet, as in the file
    by_sheet = {}
    for e in errs:
        if e.get('row') and int(e.get('row') or 0) > 1:
            by_sheet.setdefault(e['sheet'], {}).setdefault(int(e['row']), []).append(e)
    names = {n.strip().lower(): n for n in src.sheetnames}
    if by_sheet:
        idx_name = names.get('index')
        if idx_name:
            ix = wb.create_sheet('Index')
            rows = list(src[idx_name].iter_rows(values_only=True))
            if rows:
                hdr = [str(h or '').strip().lower() for h in rows[0]]
                si = hdr.index('sheet') if 'sheet' in hdr else 1
                ix.append(list(rows[0]))
                for r in rows[1:]:
                    if r and si < len(r) and str(r[si] or '').strip() in by_sheet:
                        ix.append(list(r))
        cname = names.get('columns')
        if cname:
            cs = wb.create_sheet('Columns')
            for r in src[cname].iter_rows(values_only=True):
                if r and (str(r[0] or '').strip() in by_sheet or r[0] == 'Sheet'):
                    cs.append(list(r))
    for sheet, rows in by_sheet.items():
        real = sheet if sheet in src.sheetnames else names.get(sheet.strip().lower())
        if not real:
            continue
        w = wb.create_sheet(sheet[:31])
        it = src[real].iter_rows(values_only=True)
        hdr = list(next(it, []))
        while hdr and hdr[-1] in (None, ''):
            hdr.pop()
        w.append(hdr + ['@reason', '@fix', '@excel_row'])
        for i, c in enumerate(w[1], start=1):
            c.font, c.fill = head, PatternFill('solid', fgColor='5B4FE0' if str(c.value or '').startswith('@') else navy)
            w.column_dimensions[get_column_letter(i)].width = max(10, min(40, len(str(c.value or '')) + 4))
        w.column_dimensions[get_column_letter(len(hdr) + 1)].width = 70
        w.column_dimensions[get_column_letter(len(hdr) + 2)].width = 50
        w.freeze_panes = 'A2'
        for n, r in enumerate(it, start=2):
            if n not in rows:
                continue
            es = rows[n]
            vals = list(r[:len(hdr)]) + [None] * (len(hdr) - len(r or []))
            w.append(vals + [' | '.join(str(e.get('error')) for e in es), ' | '.join(sorted({str(e.get('fix') or '') for e in es} - {''})), n])
            for e in es:
                if e.get('letter'):
                    try:
                        from openpyxl.utils import column_index_from_string
                        w.cell(row=w.max_row, column=column_index_from_string(e['letter'])).fill = PatternFill('solid', fgColor=red)
                    except Exception:   # noqa: BLE001
                        pass
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def report_name(run):
    base = os.path.splitext(run.upload.file_name)[0][:60]
    return f'{base} - {"check" if run.kind != "load" else "load"} {run.pk} errors.xlsx'.encode('ascii', 'replace').decode().replace('?', '_')
