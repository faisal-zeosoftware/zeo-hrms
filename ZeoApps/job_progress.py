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

v1.67.2 (signup) – the sign-up bar stood still at 51–57 % for minutes (user report of 5 Nov 2026): the copy of the company
template is ONE database statement (django_tenants clone_schema), and its % was only a guess from the time and an assumed
speed, capped at 95 % of the step – so the bar ran ahead, stopped at "almost done" and the "time left" grew from that
frozen %. Now:
  * clone_watched() follows the real work of the copy: the relation locks the copying connection holds (one per table,
    index and sequence it has made or read – they grow steadily from 0 to about 4 per relation of the source) are read
    from pg_locks every second; a copy that waits for another job's lock says so ("waiting for another database task");
  * the copy is checked first against PostgreSQL's lock room (max_locks_per_transaction) – "out of shared memory"
    becomes CopyLockRoom with a clear message (the template copy then makes the tables with the migrations instead);
  * learn='…' keeps the seconds of each step of the last jobs: the step weights follow them, `expected` (the usual total)
    and `eta` (the end) come from them – the screen says "usually about N" from real figures, not a fixed text;
  * the % only moves forward; `moved_at` = when it last moved: with_elapsed() hides the time left when the % has not
    moved for 15 s or the end is overdue (no "about 2 min left" from a frozen %);
  * heartbeat=True: a beat every 5 s from a thread of the job – a job whose process stopped (server restart) shows as
    `stalled` after 90 s instead of running for ever.
"""
import threading
import time

from django.core.cache import cache
from django.db import connection

RATE_KEY = 'zeo:jobprogress:copy_rate'     # bytes per second of the last schema copy
DEFAULT_RATE = 256 * 1024     # measured: the 33 MB / 845-table template took about 150 s (clone_schema)
SECS_KEY = 'zeo:jobprogress:copy_secs:{}'   # seconds of the last copy of that schema
DUMP_RATE = 1024 * 1024   # v1.62.1 pg_dump -Fc + pg_restore of one company (move): first guess until one is measured


STALL_SECS = 90     # v1.67.2 (signup) no beat for this long = the job's process has stopped
STILL_SECS = 15     # v1.67.2 (signup) the % has not moved for this long: no time left is shown


def with_elapsed(d):
    """Copy of a job dict with elapsed / remaining seconds (computed now). v1.67.2 (signup): `remaining` only from a %
    that moves – from the learned end (`eta`) when there is one; `stalled` when the job's beat stopped; `slow` when the %
    has not moved for 15 s; `overdue` when the learned end has passed."""
    if not d:
        return d
    d = dict(d)
    st = d.get('started')
    if st:
        now = time.time()
        end = d.get('ended') or now
        d['elapsed'] = int(end - st)
        p = d.get('percent') or 0
        if d.get('state') == 'running':
            beat = d.get('beat')
            moved = d.get('moved_at') or st
            if beat and now - beat > STALL_SECS:
                d['stalled'] = True
            elif now - moved > STILL_SECS or d.get('waiting'):
                d['slow'] = True
            elif d.get('eta'):
                left = d['eta'] - now
                if left >= 1:
                    d['remaining'] = int(left)
                else:
                    d['overdue'] = True
            elif 'eta' not in d and 8 <= p < 100 and d['elapsed'] >= 5:   # jobs without learned steps (as before)
                d['remaining'] = int(d['elapsed'] * (100 - p) / p)
    return d


class Progress:
    def __init__(self, setter, plan, learn=None, heartbeat=False, **first):
        """setter(**kw) stores fields in the job; plan = [(key, label, weight), …].
        v1.67.2 (signup): learn = a name under which the seconds of the steps are kept (the weights and the end follow
        the last jobs of that name); heartbeat = a beat every 5 s (a stopped job shows as stalled)."""
        self._lock = threading.Lock()
        self._setter = setter
        self.learn = learn
        self.typical = _typical_steps(learn, [k for k, _l, _w in plan]) if learn else None   # {key: seconds} or None
        if self.typical:   # the weights of the last jobs (the plan's own weights before the first one)
            plan = [(k, l, max(0.5, self.typical[k])) for k, l, _w in plan]
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
        self.fpct = 0.0
        self.moved_at = self.t0
        if self.typical:
            first.setdefault('expected', int(round(sum(self.typical.values()))))
        self.set(state='running', percent=0, started=self.t0, stages=self.stages, steps=len(plan), step_no=1,
                 detail='', moved_at=self.t0, **({'eta': None} if learn else {}), **({'beat': self.t0} if heartbeat else {}),
                 **_label(plan[0][1], 'step'), **first)
        self._beat_stop = threading.Event()
        if heartbeat:
            threading.Thread(target=self._beat, daemon=True).start()

    def _beat(self):
        """v1.67.2 (signup): a beat every 5 s while the job runs (max. 6 hours)."""
        t_end = time.time() + 6 * 3600
        while not self._beat_stop.wait(5.0) and time.time() < t_end:
            try:
                self.set(beat=time.time())
            except Exception:
                pass

    def _eta(self, now):
        """The expected end (epoch) from the learned step seconds and the real fraction of the current step; None when
        there is nothing to go by (first job of its kind)."""
        if not self.typical or self.cur is None:
            return None
        keys = [k for k, _l, _w in self.plan]
        i = keys.index(self.cur)
        f = (self.fpct - self.lo[self.cur]) / (self.span[self.cur] or 1)
        f = max(0.0, min(1.0, f))
        t_in = now - self.tstep
        typ = max(0.5, self.typical[self.cur])
        if f >= 0.05 and t_in >= 3:
            # the rest of the step at the usual time, scaled by the pace of this job – more so the further it is
            # (the start of a copy is slower than its middle: extrapolating from 5 % alone overshot 2×)
            pace = t_in / (f * typ)
            w = min(1.0, f / 0.5)
            cur_left = (1 - f) * typ * ((1 - w) + w * pace)
        else:
            cur_left = max(0.0, typ - t_in)
        return now + cur_left + sum(self.typical[k] for k in keys[i + 1:])

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
            now = time.time()
            if kw.get('percent') is not None and kw['percent'] > getattr(self, '_last_sent', -1):
                self._last_sent = kw['percent']
                self.moved_at = now
                kw['moved_at'] = now   # v1.67.2 (signup)
            if self.learn and kw.get('state', 'running') == 'running':
                e = self._eta(now)
                kw['eta'] = round(e, 1) if e else None
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
        kw.setdefault('waiting', False)   # v1.67.2 (signup) a new step is not waiting for a lock
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
            if self.learn:   # v1.67.2 (signup) the seconds of the steps of this job, for the next ones
                _learn_steps(self.learn, {s['key']: s['seconds'] for s in self.stages if s['seconds'] is not None})
        self._beat_stop.set()
        self.set(state=state, percent=self.pct, stages=self.stages, ended=now, **({'eta': None} if self.learn else {}), **kw)

    def _pct(self, v):
        self.fpct = max(self.fpct, min(99.0, float(v)))
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

    def __init__(self, P, probe, learn_copy=None, learn_secs=None, every=2.0, on_exit=None):
        self.P, self.probe, self.learn, self.learn_secs, self.every = P, probe, learn_copy, learn_secs, every
        self.on_exit = on_exit   # v1.67.2 (signup) called with (ok, seconds) after the block
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
                    self.P.sub(r[0], r[1], **(r[2] if len(r) > 2 else {}))   # v1.67.2 (signup) extra job fields
        finally:
            close_old_connections()

    def __exit__(self, et, ev, tb):
        self.stop.set()
        self.th.join(timeout=5)
        if self.on_exit is not None:
            try:
                self.on_exit(et is None, max(0.5, time.time() - self.t0))
            except Exception:
                pass
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
        # v1.67.2 (signup): from the catalogue (pages of the tables, their indexes and toast) – pg_total_relation_size
        # waits when another job holds a lock on one table, and the bar then stood at 0 % without a word
        c.execute("select coalesce(sum(k.relpages), 0)::bigint * current_setting('block_size')::bigint from pg_class k "
                  "join pg_namespace n on n.oid = k.relnamespace where n.nspname = %s and k.relkind in ('r', 'i', 't', 'm', 'S')",
                  [schema])
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


# ------------------------------------------------------------------ v1.67.2 (signup): learned step seconds
STEPS_KEY = 'zeo:jobprogress:steps:{}'


def _typical_steps(name, keys):
    """{step key: the middle value of its seconds in the last jobs of this name}, or None before the first one."""
    try:
        hist = cache.get(STEPS_KEY.format(name)) or []
    except Exception:
        return None
    hist = [h for h in hist if isinstance(h, dict) and all(k in h for k in keys)]
    if not hist:
        return None
    out = {}
    for k in keys:
        v = sorted(float(h[k]) for h in hist)
        out[k] = v[len(v) // 2]
    return out


def _learn_steps(name, secs):
    try:
        hist = list(cache.get(STEPS_KEY.format(name)) or [])[-4:] + [secs]
        cache.set(STEPS_KEY.format(name), hist, 60 * 60 * 24 * 90)
    except Exception:
        pass


def typical_total(name):
    """The usual seconds of a whole job of this name (None before the first one)."""
    t = None
    try:
        hist = cache.get(STEPS_KEY.format(name)) or []
        tots = sorted(sum(float(v) for v in h.values()) for h in hist if isinstance(h, dict) and h)
        t = tots[len(tots) // 2] if tots else None
    except Exception:
        pass
    return t


# ------------------------------------------------------------------ v1.67.2 (signup): a schema copy with real progress
LOCKS_KEY = 'zeo:jobprogress:clone_locks_per_relation'
LOCKS_PER_RELATION = 4.25   # measured 6 Nov 2026: the 5,363 relations of the template → 22,779 relation locks at the end
LOCK_ROOM_FACTOR = 2.2      # measured: with max_locks_per_transaction 64 (room 6,400) the copy failed at 14,476 locks


class CopyLockRoom(Exception):
    """The copy needs more locks than PostgreSQL has room for ("out of shared memory")."""

    def __init__(self, needed, setting, advice):
        self.needed, self.setting, self.advice = needed, setting, advice
        super().__init__(f'The copy needs about {needed:,} database locks; PostgreSQL max_locks_per_transaction is {setting}. '
                         f'Set it to at least {advice} in postgresql.conf and restart PostgreSQL.')


def schema_info(schema, cx=None):
    """Relations, tables and bytes of a schema, from the catalogue only (no lock on its tables)."""
    from django.db import connections
    cx = cx or connections['default']
    with cx.cursor() as c:
        c.execute("select count(*), count(*) filter (where k.relkind in ('r', 'p')), "
                  "coalesce(sum(k.relpages), 0)::bigint * current_setting('block_size')::bigint "
                  "from pg_class k join pg_namespace n on n.oid = k.relnamespace where n.nspname = %s", [schema])
        rel, tables, size = c.fetchone()
    return {'relations': int(rel or 0), 'tables': int(tables or 0), 'bytes': int(size or 0)}


def lock_room(cx=None):
    """(max_locks_per_transaction, room of the shared lock table = setting × (max_connections + max_prepared_transactions))."""
    from django.db import connections
    cx = cx or connections['default']
    with cx.cursor() as c:
        c.execute("select current_setting('max_locks_per_transaction')::int, current_setting('max_connections')::int, "
                  "current_setting('max_prepared_transactions')::int")
        per, conns, prep = c.fetchone()
    return per, per * (conns + prep), conns + prep


def locks_needed(info):
    try:
        ratio = float(cache.get(LOCKS_KEY) or LOCKS_PER_RELATION)
    except Exception:
        ratio = LOCKS_PER_RELATION
    return max(1, int(info['relations'] * ratio))


def check_lock_room(info):
    """Raise CopyLockRoom when the copy clearly cannot fit in PostgreSQL's lock table."""
    per, room, slots = lock_room()
    need = locks_needed(info)
    if need > room * LOCK_ROOM_FACTOR:
        advice = 1024
        while advice * slots < need:
            advice *= 2
        raise CopyLockRoom(need, per, advice)
    return need


def is_lock_room_error(e):
    t = str(e).lower()
    return 'out of shared memory' in t or 'max_locks_per_transaction' in t or getattr(getattr(e, '__cause__', None), 'pgcode', '') == '53200'


def _clone_probe(pid, label, info, expected, seen):
    from django.db import connections
    mb = info['bytes'] / 1024 / 1024
    tables = info['tables']

    def probe(_secs):
        now = time.time()
        with connections['default'].cursor() as c:
            c.execute("select count(*) from pg_locks where pid = %s and locktype = 'relation'", [pid])
            n = int(c.fetchone()[0] or 0)
            c.execute('select wait_event_type from pg_stat_activity where pid = %s', [pid])
            r = c.fetchone()
        seen['max'] = max(seen.get('max', 0), n)
        f = min(0.99, seen['max'] / max(expected, 1))
        if r and r[0] == 'Lock':   # waiting for a lock another job holds (a migration, a backup, another copy …)
            seen['wait_from'] = seen.get('wait_from') or now
            waited = int(now - seen['wait_from'])
            if waited >= 5:   # `waiting`: no time left is shown while it waits
                return f, ('{0} – waiting for another database task to finish ({1} s)', [label, waited]), {'waiting': True}
        else:
            seen['wait_from'] = None
        return f, ('{0} ({1} MB, {2} tables)', [label, f'{mb:,.0f}', f'{tables:,}']), {'waiting': False}
    return probe


def clone_watched(target, source, dest, label='Copying the data'):
    """v1.67.2 (signup): django_tenants clone_schema (one statement) with real progress for target.sub(): the relation
    locks the copying connection holds, every second. Raises CopyLockRoom before (clearly too little room) or instead of
    "out of shared memory". In the main database only (own databases keep copy_watch)."""
    from django.db import connection
    from django_tenants.clone import CloneSchema
    connection.set_schema_to_public()
    info = schema_info(source)
    need = check_lock_room(info)
    with connection.cursor() as c:
        c.execute('select pg_backend_pid()')
        pid = c.fetchone()[0]
    seen = {}

    def learn(ok, secs):
        if ok and seen.get('max') and info['relations']:
            # the locks per relation of this copy (the last sample before the end is close to the total)
            cache.set(LOCKS_KEY, round(max(1.0, seen['max'] / info['relations']), 3), 60 * 60 * 24 * 90)
        if ok:
            _remember('clone:' + source, secs)
    try:
        with _Watch(target, _clone_probe(pid, label, info, need, seen), every=1.0, on_exit=learn):
            CloneSchema().clone_schema(source, dest, clone_mode='DATA')
        target.sub(0, None, waiting=False)   # the copy is over (the % never goes back)
    except Exception as e:
        if is_lock_room_error(e):
            per, _room, slots = lock_room()
            advice = 1024
            while advice * slots < need:
                advice *= 2
            raise CopyLockRoom(need, per, advice) from e
        raise
    return info
