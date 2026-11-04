"""v1.62.1 – progress of the long company jobs (create a company, duplicate a company, ZEO Cloud company), in words and in %.

Before, the screens only showed "Please wait, the company is being created… step 2 of 5". Now every job keeps:
  percent   0–100, from fixed step weights and, inside a step, the real work done:
            * schema + migrations: applied migrations of the new schema / migrations on disk (counted every 2 s),
            * copy of a schema (template or company): estimated from the source size and the speed of the last copy,
            * apps: one share per app;
  detail    what is being done now ("Creating the database tables – 120 of 232 (Payroll)", "Setting up Payroll (2 of 3)"),
  stages    the steps with their state (done / running / waiting) and seconds,
  started   epoch seconds (with_elapsed() adds elapsed and remaining at read time).
Usage:
    P = Progress(setter, [('create', 'Creating the company', 55), ('apps', 'Setting up the apps', 25), ...])
    P.begin('create', 'Copying the ready company template')
    with P.migrations(schema): comp.save()          # or: with P.copy(source_schema): clone_schema(...)
    P.sub(0.5, 'Setting up Payroll (2 of 3)')
    P.finish(state='done', company_id=..)
"""
import threading
import time

from django.core.cache import cache
from django.db import connection

RATE_KEY = 'zeo:jobprogress:copy_rate'     # bytes per second of the last schema copy
DEFAULT_RATE = 256 * 1024     # measured: the 33 MB / 845-table template took about 150 s (clone_schema)
SECS_KEY = 'zeo:jobprogress:copy_secs:{}'   # seconds of the last copy of that schema
DUMP_RATE = 1024 * 1024   # v1.62.1 pg_dump -Fc + pg_restore of one company (move): first guess until one is measured


def with_elapsed(d):
    """Copy of a job dict with elapsed / remaining seconds (computed now)."""
    if not d:
        return d
    d = dict(d)
    st = d.get('started')
    if st:
        end = d.get('ended') or time.time()
        d['elapsed'] = int(end - st)
        p = d.get('percent') or 0
        if d.get('state') == 'running' and 8 <= p < 100 and d['elapsed'] >= 5:
            d['remaining'] = int(d['elapsed'] * (100 - p) / p)
    return d


class Progress:
    def __init__(self, setter, plan, **first):
        """setter(**kw) stores fields in the job; plan = [(key, label, weight), …]."""
        self._lock = threading.Lock()
        self._setter = setter
        self.plan = plan
        tot = float(sum(w for _k, _l, w in plan)) or 1.0
        self.lo, acc = {}, 0.0
        for k, _l, w in plan:
            self.lo[k] = 100.0 * acc / tot
            acc += w
        self.span = {k: 100.0 * w / tot for k, _l, w in plan}
        self.cur = None
        self.t0 = time.time()
        self.tstep = self.t0
        self.stages = [dict({'key': k, 'state': 'waiting', 'seconds': None}, **_label(l)) for k, l, _w in plan]
        self.pct = 0
        self.set(state='running', percent=0, started=self.t0, stages=self.stages, steps=len(plan), step_no=1,
                 detail='', **_label(plan[0][1], 'step'), **first)

    @staticmethod
    def _detail(kw):
        """detail may be a text or (template, [params]) – the screen translates the template ('Setting up {0} ({1} of {2})')."""
        d = kw.get('detail')
        if isinstance(d, tuple):
            tpl, params = d[0], list(d[1]) if len(d) > 1 else []
            kw['detail'] = tpl.format(*params)
            kw['detail_t'], kw['detail_p'] = tpl, [str(x) for x in params]
        elif 'detail' in kw:
            kw['detail_t'], kw['detail_p'] = d or '', []

    def set(self, **kw):
        self._detail(kw)
        with self._lock:   # the watcher thread and the job thread both write the job
            self._setter(**kw)

    # --------------------------------------------------------------- steps
    def begin(self, key, detail='', **kw):
        now = time.time()
        for s in self.stages:
            if s['key'] == self.cur and s['state'] == 'running':
                s['state'] = 'done'; s['seconds'] = round(now - self.tstep, 1)
        self.cur, self.tstep = key, now
        no = 1
        for i, s in enumerate(self.stages):
            if s['key'] == key:
                s['state'] = 'running'; no = i + 1
        label = next(l for k, l, _w in self.plan if k == key)
        self._pct(self.lo[key])
        kw.update(_label(label, 'step'))
        self.set(step_no=no, detail=detail, percent=self.pct, stages=self.stages, **kw)

    def sub(self, fraction, detail=None, **kw):
        """fraction 0–1 of the current step done."""
        if self.cur is None:
            return
        f = max(0.0, min(1.0, float(fraction)))
        self._pct(self.lo[self.cur] + self.span[self.cur] * f)
        if detail is not None:
            kw['detail'] = detail
        self.set(percent=self.pct, **kw)

    def finish(self, state='done', **kw):
        now = time.time()
        for s in self.stages:
            if s['state'] == 'running':
                s['state'] = 'done' if state == 'done' else 'failed'
                s['seconds'] = round(now - self.tstep, 1)
        if state == 'done':
            self.pct = 100
            kw.setdefault('step', 'Done'); kw.setdefault('step_no', len(self.plan)); kw.setdefault('detail', '')
            kw.setdefault('step_t', kw['step']); kw.setdefault('step_p', [])
        self.set(state=state, percent=self.pct, stages=self.stages, ended=now, **kw)

    def _pct(self, v):
        self.pct = max(self.pct, int(round(min(99.0, v))))   # never goes back; 100 only on finish

    # --------------------------------------------------------------- watchers
    def migrations(self, schema):
        return _Watch(self, _migrations_probe(schema))

    def copy(self, source_schema, label='Copying the data', db='', kind=''):
        """db: the own database the schema lives in ('' = main); kind 'dump' = pg_dump / pg_restore (its own speed)."""
        return copy_watch(self, source_schema, label, db=db, kind=kind)

    def timed(self, name, default_secs, detail):
        return timed_watch(self, name, default_secs, detail)


def _label(l, prefix='label'):
    """A step label is a text or (template, [params]) – the screen translates the template ('Copying {0}')."""
    if isinstance(l, tuple):
        tpl, params = l[0], list(l[1]) if len(l) > 1 else []
        return {prefix: tpl.format(*params), prefix + '_t': tpl, prefix + '_p': [str(x) for x in params]}
    return {prefix: l, prefix + '_t': l, prefix + '_p': []}


class Share:
    """v1.62.1: a part (lo–hi) of the current step of P as a target with .sub() – a sub job (one company of a database
    copy, a move inside it) reports 0–1 and the parent's % moves within that part. wrap(detail) → the parent's detail."""

    def __init__(self, P, lo=0.0, hi=1.0, wrap=None):
        self.P, self.lo, self.hi, self.wrap = P, lo, hi, wrap

    def sub(self, fraction, detail=None, **kw):
        f = max(0.0, min(1.0, float(fraction)))
        if detail is not None and self.wrap:
            detail = self.wrap(detail)
        self.P.sub(self.lo + (self.hi - self.lo) * f, detail, **kw)

    def part(self, lo, hi, wrap=None):
        """A part of this part (lo–hi of it)."""
        w = self.hi - self.lo
        return Share(self.P, self.lo + w * lo, self.lo + w * hi, wrap or self.wrap)


def copy_watch(target, source_schema, label='Copying the data', db='', kind=''):
    """Watcher of a schema copy for any target with .sub() (a Progress or a Share)."""
    tag = f'{kind}:{db}:{source_schema}' if (db or kind) else source_schema
    return _Watch(target, _copy_probe(source_schema, label, db=db, kind=kind), learn_copy=(source_schema, db, kind, tag))


def timed_watch(target, name, default_secs, detail):
    """A step whose progress is not visible (one pg_dump, one CREATE DATABASE): estimated from the seconds it took the
    last times (default_secs the first time), never above 95 %."""
    hist = cache.get(SECS_KEY.format('t:' + name)) or []
    est = float(sorted(hist)[len(hist) // 2]) if hist else float(default_secs)

    def probe(secs):
        return min(0.95, secs / max(est, 0.5)), detail
    return _Watch(target, probe, learn_secs='t:' + name, every=1.0)


class _Watch:
    """Runs a probe every 2 s in its own thread while the with-block works; probe() → (fraction, detail) or None."""

    def __init__(self, P, probe, learn_copy=None, learn_secs=None, every=2.0):
        self.P, self.probe, self.learn, self.learn_secs, self.every = P, probe, learn_copy, learn_secs, every
        self.stop = threading.Event()

    def __enter__(self):
        self.t0 = time.time()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()
        return self

    def _run(self):
        from django.db import close_old_connections
        try:
            while not self.stop.wait(self.every):
                try:
                    r = self.probe(time.time() - self.t0)
                except Exception:
                    r = None
                if r:
                    self.P.sub(r[0], r[1])
        finally:
            close_old_connections()

    def __exit__(self, et, ev, tb):
        self.stop.set()
        self.th.join(timeout=5)
        if et is None and self.learn_secs:
            _remember(self.learn_secs, max(0.5, time.time() - self.t0))
        if et is None and self.learn:
            try:   # remember the copy speed for the next estimate
                if isinstance(self.learn, tuple):
                    schema, db, kind, tag = self.learn
                else:
                    schema, db, kind, tag = self.learn, '', '', self.learn
                size = _schema_bytes(schema, db)
                secs = max(0.5, time.time() - self.t0)
                _remember(tag, secs)   # the last 5 copies of this schema
                if size:
                    cache.set(RATE_KEY + (':' + kind if kind else ''), max(1.0, size / secs), 60 * 60 * 24 * 90)
            except Exception:
                pass
        return False


def _remember(tag, secs):
    try:
        hist = (cache.get(SECS_KEY.format(tag)) or [])
        hist = ([hist] if isinstance(hist, (int, float)) else list(hist))[-4:] + [round(secs, 1)]
        cache.set(SECS_KEY.format(tag), hist, 60 * 60 * 24 * 90)
    except Exception:
        pass


def _schema_bytes(schema, db=''):
    from django.db import connections
    if db:   # v1.62.1 a schema in an own database (ZEO Cloud Premium)
        from ZeoCloud.owndb import conn
        cx = conn(db)
    else:
        cx = connections['default']
    with cx.cursor() as c:
        c.execute("select coalesce(sum(pg_total_relation_size(format('%%I.%%I', table_schema, table_name))),0) "
                  "from information_schema.tables where table_schema=%s", [schema])
        return int(c.fetchone()[0] or 0)


def _migrations_probe(schema):
    from django.db.migrations.loader import MigrationLoader
    total = len(MigrationLoader(None, ignore_no_migrations=True).disk_migrations) or 1
    names = {}
    try:
        from django.apps import apps
        names = {c.label: str(c.verbose_name) for c in apps.get_app_configs()}
    except Exception:
        pass

    def probe(_secs):
        from django.db import connections
        with connections['default'].cursor() as c:
            c.execute("select to_regclass(%s)", [f'"{schema}".django_migrations'])
            if not c.fetchone()[0]:
                return 0.02, ('Creating the database', [])
            c.execute(f'select count(*), (select app from "{schema}".django_migrations order by id desc limit 1) '
                      f'from "{schema}".django_migrations')
            n, app = c.fetchone()
        app_name = names.get(app or '', app or '')
        if app_name:
            return min(0.99, n / total), ('Creating the database tables – {0} of {1} ({2})', [n, total, app_name])
        return min(0.99, n / total), ('Creating the database tables – {0} of {1}', [n, total])
    return probe


def _copy_probe(source, label, db='', kind=''):
    try:
        size = _schema_bytes(source, db)
    except Exception:
        size = 0
    rate = cache.get(RATE_KEY + (':' + kind if kind else '')) or (DUMP_RATE if kind == 'dump' else DEFAULT_RATE)
    hist = cache.get(SECS_KEY.format(f'{kind}:{db}:{source}' if (db or kind) else source)) or []
    hist = [hist] if isinstance(hist, (int, float)) else list(hist)
    if hist:   # the middle value of the last copies (one slow or fast copy does not swing the estimate)
        est = float(sorted(hist)[len(hist) // 2])
    else:
        est = max(4.0, size / rate) if size else 60.0
    mb = size / 1024 / 1024

    def probe(secs):
        # the copy is one database statement: its progress is not visible, so it is estimated (never above 95 %)
        f = min(0.95, secs / est)
        left = max(0, int(est - secs))
        if left <= 2:
            return f, ('{0} ({1} MB) – almost done', [label, f'{mb:,.0f}'])
        return f, ('{0} ({1} MB)', [label, f'{mb:,.0f}'])   # the time left is shown by the screen from the %
    return probe
