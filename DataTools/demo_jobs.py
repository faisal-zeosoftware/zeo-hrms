"""v1.73.15 – demo data jobs: make the 10 demo companies (DataTools/demo_data.py) in the background, show progress, remove them.

    start(level, user)    → job id; level 'simple' or 'heavy'. Up to PARALLEL companies are made at the same time.
    status(job)           → {state, level, done, total, companies: [{key, name, country, industry, state, schema, rows, failed, seconds}], ...}
    demo_companies()      → the demo companies on this server (CompanyOption key "demo" in each schema)
    remove(user)          → deletes every demo company (only companies marked as demo – never a real one)

A demo company is an ordinary company: made like any new company (the ready template when `cloud_template` was run, else
the migrations), then marked as demo, neutralized (no e-mail, no outside AI), allowed to save transactions before its setup
guide is finished, given the public holidays of the demo months, and loaded with its workbook through the screens' API.
"""
import logging
import os
import tempfile
import threading
import time
import uuid

from django.core.cache import cache
from django.db import connection

log = logging.getLogger(__name__)
JOB = 'zeo:demojob:{}'
LAST = 'zeo:demojob:last'
PARALLEL = int(os.environ.get('ZEO_DEMO_PARALLEL', '3') or 3)
APPS_ALL = ['hrms', 'payroll', 'projects', 'crm', 'property', 'maintenance', 'assets', 'people', 'hse']


_STATE_LOCK = threading.RLock()


def _shared_cache():
    """True when the cache is shared between processes (Redis) – then each demo company is made in its own process."""
    from django.conf import settings
    return 'redis' in str(settings.CACHES.get('default', {}).get('BACKEND', '')).lower()


def _job(job, **kw):
    with _STATE_LOCK:
        d = cache.get(JOB.format(job)) or {}
        d.update(kw)
        cache.set(JOB.format(job), d, 60 * 60 * 24)
        return d


def _ckey(job, key):
    return JOB.format(job) + ':c:' + key


def _company_state(job, key, **kw):
    """The state of one company of the job – its own cache key: one writer each, no lost updates between processes."""
    k = _ckey(job, key)
    d = cache.get(k) or {}
    d.update(kw)
    cache.set(k, d, 60 * 60 * 24)


def _host():
    import socket
    return socket.gethostname()


def _gone(d):
    """v1.73.18: the process that ran the job no longer exists on this server (restarted during the company template)."""
    pid = d.get('pid')
    if not pid or d.get('host') != _host():
        return False
    try:
        os.kill(int(pid), 0)
        return False
    except (OSError, ValueError):
        return True


def status(job=None):
    job = job or cache.get(LAST)
    d = cache.get(JOB.format(job)) if job else None
    if not d:
        return None
    d = dict(d, job=job)
    beat = d.get('beat') or d.get('started') or 0
    if d.get('state') == 'running' and (time.time() - beat > 180 or _gone(d)):   # v1.73.18 (merge test): a job stopped by a server restart stayed
        _job(job, state='failed', finished=time.time(),               # "running" on Choose a company for ever – now said, with Start again
             error='Stopped – the server was restarted while the demo data was being made. Start it again.')
        d = dict(cache.get(JOB.format(job)) or d, job=job)
    comps = []
    for c in d.get('companies', []):
        comps.append(dict(c, **(cache.get(_ckey(job, c['key'])) or {})))
    d['companies'] = comps
    d['done'] = sum(1 for c in comps if c['state'] in ('done', 'failed'))
    if d.get('state') == 'running' and d.get('started'):
        el = time.time() - d['started']
        d['elapsed'] = int(el)
        done = d['done']
        if done:
            left = (d['total'] - done) * el / done
        else:
            from .demo_data import LEVELS
            lo = int(str(d.get('minutes') or LEVELS[d['level']]['minutes']).split('–')[1]) * 60
            left = max(60, lo - el)
        d['seconds_left'] = int(left)
    return d


def running():
    d = status()
    beat = (d or {}).get('beat') or (d or {}).get('started') or 0
    if d and d.get('state') == 'running' and time.time() - beat > 180:   # no heartbeat for 3 minutes: the server was restarted
        _job(d['job'], state='failed', error='Stopped – the server was restarted while the demo data was being made. Start it again.',
             finished=time.time())
        return None
    return d if d and d.get('state') == 'running' else None


def start(level, user, scope='all', industry=None, country=None, run_now=False, specs=None):
    """Make the demo companies: scope 'all' (the 10 companies of the 7 countries) or 'one' (one industry, in `country`).
    Returns the job id; a job already running is returned instead of a second one."""
    from .demo_data import LEVELS, COUNTRIES, INDUSTRIES, plan_for, estimate
    if level not in LEVELS:
        raise ValueError('level must be simple or heavy')
    r = running()
    if r:
        return r['job']
    specs = [tuple(x) for x in (specs or plan_for(scope, industry, country))]
    job = uuid.uuid4().hex[:16]
    _job(job, state='running', level=level, scope=scope, total=len(specs), done=0, started=time.time(), by=getattr(user, 'pk', None), error='',
         pid=os.getpid(), host=_host(),   # v1.73.18 (merge test): the process that runs the job – a restart is seen at once
         minutes=estimate(level, 'one' if len(specs) == 1 else 'all'), specs=[list(x) for x in specs],
         companies=[{'key': p[0], 'name': p[3], 'country': COUNTRIES[p[1]]['country'], 'industry': INDUSTRIES[p[2]]['title'],
                     'state': 'waiting', 'schema': '', 'rows': 0, 'failed': 0, 'seconds': 0, 'step': ''} for p in specs])
    cache.set(LAST, job, 60 * 60 * 24 * 30)
    args = (job, level, specs, getattr(user, 'pk', None))
    if run_now:
        _run(*args)
    else:
        threading.Thread(target=_run, args=args, daemon=True).start()
    return job


def refresh(user, level=None):
    """Make the demo companies on this server again with fresh dates (the same companies and level, or another level)."""
    have = demo_companies()
    last = status() or {}
    specs = [tuple(d['spec']) for d in have if d.get('spec')]
    for sp in last.get('specs') or []:   # a company of the last job that could not be made is tried again
        if tuple(sp)[0] not in {x[0] for x in specs}:
            specs.append(tuple(sp))
    if not specs:
        raise ValueError('There are no demo companies to refresh.')
    return start(level or (have[0].get('level') if have else last.get('level')) or 'simple', user, specs=specs)


def make_one_safe(job, sp, level, user_id):
    """make_one with one more try when PostgreSQL broke a lock cycle; a failure is recorded on the company, never raised."""
    from django.db import close_old_connections
    try:
        try:
            make_one(job, sp, level, user_id)
        except Exception as e1:   # noqa: BLE001
            if 'deadlock' not in str(e1).lower():
                raise
            log.warning('demo company %s: deadlock – trying again', sp[0])
            close_old_connections()
            time.sleep(5)
            make_one(job, sp, level, user_id)
    except Exception as e:   # noqa: BLE001 – the other companies go on
        log.exception('demo company %s', sp[0])
        _company_state(job, sp[0], state='failed', step=str(e)[:300])
    finally:
        close_old_connections()


def _run(job, level, specs, user_id):
    """Make the companies: up to PARALLEL at a time, each in its own process (manage.py make_demo_data --one-company) when the
    cache is shared (Redis) – the web server is not slowed down and every CPU core is used; else in threads of this process."""
    import json
    import subprocess
    import sys
    from django.conf import settings
    from django.db import close_old_connections
    close_old_connections()
    try:
        _job(job, beat=time.time())
        _ensure_template(job)
        # a refresh: the old demo companies go first, one after the other (dropping a schema while another is being copied
        # can deadlock in PostgreSQL)
        old = [d for d in demo_companies() if d.get('key') in {sp[0] for sp in specs}]
        if old:
            _job(job, step='Removing the old demo companies')
            for d in old:
                _drop(d)
                _job(job, beat=time.time())
            _job(job, step='')
        queue = list(specs)
        n = max(1, min(PARALLEL, len(specs)))
        if _shared_cache():
            manage = os.path.join(str(settings.BASE_DIR), 'manage.py')
            procs = []
            while queue or procs:
                while queue and len(procs) < n:
                    sp = queue.pop(0)
                    cmd = [sys.executable, manage, 'make_demo_data', '--one-company', json.dumps(list(sp)), '--job', job,
                           '--level', level, '--user-id', str(user_id or '')]
                    procs.append((sp, subprocess.Popen(cmd, cwd=str(settings.BASE_DIR), stdout=subprocess.DEVNULL,
                                                       stderr=subprocess.DEVNULL, env=dict(os.environ))))
                    time.sleep(2)   # the template copies start one after the other
                _job(job, beat=time.time())
                for sp, pr in list(procs):
                    if pr.poll() is not None:
                        procs.remove((sp, pr))
                        st = cache.get(_ckey(job, sp[0])) or {}
                        if st.get('state') not in ('done', 'failed'):
                            _company_state(job, sp[0], state='failed', step=f'stopped (exit code {pr.returncode})')
                time.sleep(3)
        else:
            lock = threading.Lock()

            def worker():
                close_old_connections()
                while True:
                    with lock:
                        if not queue:
                            break
                        sp = queue.pop(0)
                    make_one_safe(job, sp, level, user_id)
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(n)]
            for t in threads:
                t.start()
                time.sleep(2)
            while any(t.is_alive() for t in threads):
                _job(job, beat=time.time())
                for t in threads:
                    t.join(timeout=5)
        d = status(job) or {}
        bad = [c for c in d.get('companies', []) if c['state'] == 'failed']
        _job(job, state='done' if not bad else 'done_with_errors', finished=time.time())
    except Exception as e:   # noqa: BLE001
        log.exception('demo job')
        _job(job, state='failed', error=str(e)[:400], finished=time.time())
    finally:
        close_old_connections()


def _ensure_template(job):
    """The demo companies are copies of the ready template (seconds each); it is made once when missing (a few minutes)."""
    from ZeoCloud.services import TEMPLATE, template_ready
    from django.core.management import call_command
    if template_ready() and _template_version() != _code_version():
        # v1.73.18 (merge test): a template made by an older version kept that version's company defaults (no "BAS" salary component,
        # no leave types per branch) – the demo companies lost their salaries and leave; it is made again once per version
        _job(job, step='Preparing the company template (once, a few minutes)', beat=time.time() + 1800)
        connection.set_schema_to_public()
        call_command('cloud_template', rebuild=True)
        _set_template_version()
        _job(job, step='', beat=time.time())
        return
    if template_ready():
        # v1.73.18 (merge test): a template made by an older version lacked the newer tables ("relation ZeoApps_appstate does
        # not exist") and every demo company failed – the template is brought up to this version first (nothing to do = seconds)
        _job(job, step='Updating the company template', beat=time.time() + 1800)
        from django.core.management import call_command
        connection.set_schema_to_public()
        call_command('migrate_schemas', schema_name=TEMPLATE, interactive=False, verbosity=0)
        _job(job, step='', beat=time.time())
        return
    _job(job, step='Preparing the company template (once, a few minutes)', beat=time.time() + 1800)   # no beat while it runs
    connection.set_schema_to_public()
    call_command('cloud_template')
    _set_template_version()
    _job(job, step='')


def _code_version():
    try:
        from Chatter.nav import RELEASES
        return RELEASES[0]['version']
    except Exception:   # noqa: BLE001
        return ''


def _template_version():
    """v1.73.18: the version that made the template company ('' = unknown, an older template)."""
    from django_tenants.utils import schema_context
    from ZeoCloud.services import TEMPLATE
    try:
        with schema_context(TEMPLATE):
            from ZeoApps.models import CompanyOption
            o = CompanyOption.objects.filter(key='template_version').first()
            return str((o.value or {}).get('version') or '') if o else ''
    except Exception:   # noqa: BLE001
        return ''


def _set_template_version():
    from django_tenants.utils import schema_context
    from ZeoCloud.services import TEMPLATE
    try:
        with schema_context(TEMPLATE):
            from ZeoApps.models import CompanyOption
            CompanyOption.objects.update_or_create(key='template_version', defaults={'value': {'version': _code_version()}})
    except Exception:   # noqa: BLE001
        log.exception('template version')


def make_one(job, spec, level, user_id):
    """Create one demo company and load its data. → its schema."""
    from django_tenants.utils import schema_context
    from Core.models import cntry_mstr
    from UserManagement.models import company as Company, CustomUser
    from UserManagement import company_jobs as CJ
    from .demo_data import COUNTRIES, LEVELS, build, months_for
    key, cc, ind, name, code, cities = spec
    t0 = time.time()
    _company_state(job, key, state='creating', step='Creating the company')
    connection.set_schema_to_public()
    old = demo_company(key)
    if old is not None:   # made again (refresh): the old copy goes first
        _drop(old)
    country = cntry_mstr.objects.filter(country_name__iexact=COUNTRIES[cc]['country']).first()
    if country is None:
        raise ValueError(f'The country {COUNTRIES[cc]["country"]} is not in the country list (run load_master_data).')
    data = {'name': name, 'country': country, 'industry_type': ind}
    cur = country.currency.order_by('id').first() if hasattr(country, 'currency') else None
    if cur is not None:
        data['currency'] = cur
    user = CustomUser.objects.filter(pk=user_id).first() if user_id else None
    cj, schema = CJ.start(data, APPS_ALL, user, run_now=True)
    st = CJ.status(cj) or {}
    if st.get('state') != 'done':
        raise ValueError(st.get('error') or 'the company could not be created')
    _company_state(job, key, state='loading', schema=schema, step='Loading the demo data')
    from django.utils import timezone
    today = timezone.localdate()
    months = months_for(today, LEVELS[level]['months'])
    with schema_context(schema):
        prepare(schema, spec, level, user, months)
        today = _company_today()
        ctx = context()
    tmpl = _template_path()
    raw = build(spec, level, today, ctx, tmpl)
    fd, path = tempfile.mkstemp(suffix='.xlsx', prefix=f'zeo-demo-{key}-')
    with os.fdopen(fd, 'wb') as fh:
        fh.write(raw)
    try:
        tot, failed = load(schema, path, user, on_sheet=lambda s, mo='', n=0: _company_state(job, key, step=f'Loading: {s} {mo}'.strip()))
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    with schema_context(schema):
        reviewed()
        from ZeoApps.models import CompanyOption
        o = CompanyOption.objects.filter(key='demo').first()
        v = dict(o.value or {}) if o else {}
        v.update(rows=tot.get('ok', 0), failed=tot.get('failed', 0), seconds=int(time.time() - t0), errors=failed[:50])
        CompanyOption.objects.update_or_create(key='demo', defaults={'value': v})
    _company_state(job, key, state='done', rows=tot.get('ok', 0), failed=tot.get('failed', 0), seconds=int(time.time() - t0), step='')
    return schema


def reviewed():
    """The industry-pack items of a demo company count as reviewed (the apps show no "waiting for review" banner)."""
    from django.apps import apps
    from django.utils import timezone
    n = 0
    for m in apps.get_models():
        names = {f.name for f in m._meta.fields}
        if 'review_status' not in names or m._meta.app_label not in ('ZeoCRM', 'ZeoHSE', 'ZeoProperty', 'ZeoMaintenance', 'ZeoAssets',
                                                                     'ZeoPeople', 'ZeoProjects', 'ZeoDocs', 'ZeoHR'):
            continue
        extra = {'reviewed_at': timezone.now()} if 'reviewed_at' in names else {}
        try:
            n += m.objects.exclude(review_status='reviewed').update(review_status='reviewed', **extra)
        except Exception:   # noqa: BLE001
            log.exception('review status of %s', m.__name__)
    return n


def _company_today():
    try:
        from AttendancePlus.engine import today
        return today()
    except Exception:   # noqa: BLE001
        from django.utils import timezone
        return timezone.localdate()


def _template_path():
    from .workbook_jobs import TEMPLATE_PATH
    return TEMPLATE_PATH


def prepare(schema, spec, level, user, months):
    """Inside the new company: the demo mark, no e-mail / outside AI, transactions before the setup guide is finished,
    public holidays of every demo month."""
    import datetime
    from ZeoApps.models import CompanyOption
    from ZeoApps.duplicate import neutralize_company
    CompanyOption.objects.update_or_create(key='demo', defaults={'value': {
        'on': True, 'key': spec[0], 'code': spec[4], 'spec': list(spec), 'level': level, 'made': datetime.date.today().isoformat(),
        'from': f'{months[0][0]}-{months[0][1]:02d}', 'to': f'{months[-1][0]}-{months[-1][1]:02d}', 'by': getattr(user, 'pk', None)}})
    neutralize_company(getattr(user, 'pk', None))
    from zeo.mailguard import forget
    forget(schema)
    try:
        from ZeoAdmin.setup_gate import set_allow
        set_allow(True, user)
    except Exception:   # noqa: BLE001
        log.exception('setup gate of the demo company %s', schema)
    try:   # expense categories, policies and limits (as setup_zeo --expense-defaults)
        from django.core.management import call_command
        import io
        call_command('load_expense_defaults', schema=schema, verbosity=0, stdout=io.StringIO())
    except Exception:   # noqa: BLE001
        log.exception('expense defaults of the demo company %s', schema)
    try:   # the setup wizards of the apps (Projects, CRM, Property, Maintenance, Assets, People, HSE)
        from ZeoApps import appsetup
        for a in appsetup.APPS_WITH_WIZARD:
            appsetup.set_allow(a, True, user)
    except Exception:   # noqa: BLE001
        log.exception('app setup gates of the demo company %s', schema)
    try:
        from django.apps import apps
        from CountryPack.holidays import seed_calendar
        from CountryPack.services import pack_code
        pack = pack_code(schema)
        Cal = apps.get_model('calendars', 'holiday_calendar')
        for cal in Cal.objects.all():
            for y in sorted({m[0] for m in months}):
                seed_calendar(cal, pack, from_year=y, user=user)
    except Exception:   # noqa: BLE001
        log.exception('holidays of the demo company %s', schema)


def context():
    """What the new company already has (the workbook names these and adds only the rest)."""
    from django.apps import apps
    M = apps.get_model
    depts = list(M('OrganisationManager', 'dept_master').objects.values_list('dept_name', flat=True))
    desigs = list(M('OrganisationManager', 'desgntn_master').objects.values_list('desgntn_job_title', flat=True))
    cats = list(M('OrganisationManager', 'ctgry_master').objects.values_list('ctgry_title', flat=True))
    docs = list(M('EmpManagement', 'document_type').objects.values_list('type_name', flat=True))
    leave = {}
    for lt in M('calendars', 'leave_type').objects.all().order_by('id'):
        n = (lt.name or '')
        low = n.lower()
        if 'annual' in low and 'annual' not in leave:
            leave['annual'] = n
        elif 'sick' in low and 'sick' not in leave:
            leave['sick'] = n
    pay = _pay_split()
    from ZeoApps.services import active_keys
    return {'depts': depts, 'desigs': desigs, 'cats': cats, 'docs': docs, 'leave': leave, 'pay': pay, 'apps': list(active_keys()),
            'opp_x': _industry_values()}


def _industry_values():
    """Values for the CRM industry fields an opportunity needs before it can be won (required, or required from a stage)."""
    import datetime
    out = {}
    try:
        from ZeoCRM.models import IndustryField
        for f in IndustryField.objects.filter(on_opportunity=True):
            if not (f.required or f.required_from):
                continue
            t = (f.field_type or 'text').lower()
            ch = [c for c in (f.choices or '').splitlines() if c.strip()]
            out[f.code] = (ch[0] if ch else 'Demo') if t in ('choice', 'select', 'list', 'multi') else \
                (40 if t in ('number', 'decimal', 'int', 'money', 'currency') else
                 ('yes' if t in ('yesno', 'bool', 'boolean') else (datetime.date.today().isoformat() if t == 'date' else 'Demo value')))
    except Exception:   # noqa: BLE001
        log.exception('CRM industry fields')
    return out


def _has(app, model):
    from django.apps import apps
    try:
        apps.get_model(app, model)
        return True
    except LookupError:
        return False


def _pay_split():
    """[(component code, share)] of the monthly salary: basic, housing, transport (the codes of the company's own components)."""
    from django.apps import apps
    SC = apps.get_model('PayrollManagement', 'SalaryComponent')
    rows = list(SC.objects.all().values('code', 'name'))
    def find(*words):
        for r in rows:
            n = f"{r['code']} {r['name']}".lower()
            if any(w in n for w in words):
                return r['code']
        return None
    basic = find('basic') or 'BAS'
    housing = find('housing', 'hra', 'house rent')
    transport = find('transport', 'conveyance')
    out = [(basic, 0.55)]
    if housing:
        out.append((housing, 0.30 if transport else 0.45))
    if transport:
        out.append((transport, 0.15))
    if len(out) == 1:
        out = [(basic, 1.0)]
    return out


def load(schema, path, user, on_sheet=None):
    """Run the workbook into the company (as `user`, else the company administrator). → (totals, failed rows)."""
    from django.core.management.base import CommandError
    from DataTools.management.commands.load_workbook import Loader, read_book, run_book, totals
    from zeo import mailguard
    L = Loader(None, schema, getattr(user, 'username', '') or '', verbose=0)
    try:
        specs, data = read_book(path, L, None)
    except CommandError as e:
        raise ValueError(str(e))
    with mailguard.suppressed():
        run_book(L, specs, data, '', on_sheet=on_sheet)
    return totals(L), [x for x in L.log if x.get('kind') != 'sheet'][:200] + [x for x in L.log if x.get('kind') == 'sheet'][:20]


# ---------------------------------------------------------------------------------------------- the demo companies
def demo_companies():
    """[{id, name, schema, key, level, from, to, made, rows, failed}] – every company marked as demo."""
    from django_tenants.utils import schema_context
    from UserManagement.models import company as Company
    out = []
    connection.set_schema_to_public()
    for c in Company.objects.exclude(schema_name='public').order_by('id'):
        try:
            with schema_context(c.schema_name):
                from ZeoApps.models import CompanyOption
                o = CompanyOption.objects.filter(key='demo').first()
                if o and (o.value or {}).get('on'):
                    out.append(dict(o.value, id=c.pk, name=c.name, schema=c.schema_name))
        except Exception:   # noqa: BLE001 – a company on another database / not migrated
            continue
    return out


def demo_company(key):
    for d in demo_companies():
        if d.get('key') == key:
            return d
    return None


def is_demo(schema):
    from django_tenants.utils import schema_context
    try:
        with schema_context(schema):
            from ZeoApps.models import CompanyOption
            o = CompanyOption.objects.filter(key='demo').first()
            return bool(o and (o.value or {}).get('on'))
    except Exception:   # noqa: BLE001
        return False


def _drop(d):
    from UserManagement.models import company as Company
    connection.set_schema_to_public()
    c = Company.objects.filter(pk=d['id']).first()
    if c is not None and is_demo(c.schema_name):   # never a company that is not marked as demo
        from UserManagement.models import CustomUser
        # the logins made by the demo (employee codes of this company) – only when they open no other company
        mine = [u for u in CustomUser.objects.filter(tenants=c, is_superuser=False)
                if u.tenants.exclude(pk=c.pk).count() == 0 and d.get('code') and str(u.username).upper().startswith(d['code'] + '-')]
        c.delete(force_drop=True)
        for u in mine:
            retire_login(u)


def retire_login(u):
    """A login made by the demo: switched off and renamed, so a new demo can use its user name and e-mail again (logins
    are never deleted in ZEO – their history stays)."""
    try:
        u.tenants.clear()
        u.is_active = False
        u.username = f'removed-{u.pk}-{u.username}'[:150]
        u.email = f'removed-{u.pk}@demo.invalid'
        u.save(update_fields=['is_active', 'username', 'email'])
    except Exception:   # noqa: BLE001
        log.exception('demo login %s', u.username)


def remove(user=None):
    """Delete every demo company. → the names deleted."""
    if running():
        raise ValueError('Demo companies are being made – wait until the job has finished.')
    gone = []
    for d in demo_companies():
        _drop(d)
        gone.append(d['name'])
    log.info('demo companies removed by %s: %s', getattr(user, 'username', '?'), gone)
    return gone
