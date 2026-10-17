"""v1.23.0 – ZEO Cloud rules: status of an account, usage against the limits, app licences, creating accounts and companies.

Everything here is a no-op for companies that are not linked to an account (own installations, companies made before ZEO Cloud).
Enforcement in requests only runs when settings.ZEO_CLOUD is on (see middleware.py).
"""
import logging
import re
import threading
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone

from .models import (APP_KEYS, AccessNotice, Account, AccountCompany, AccountEvent, AppLicence, Invoice, Plan,
                     PlatformAdmin, DatabaseServer)

log = logging.getLogger(__name__)
APP_TITLES = {'hrms': 'HR', 'payroll': 'Payroll', 'projects': 'Projects', 'crm': 'CRM', 'property': 'Property',
              'maintenance': 'Maintenance', 'hse': 'HSE'}


def cloud_on():
    return bool(getattr(settings, 'ZEO_CLOUD', False))


def tr(text, *args):
    try:
        from Translations.i18n import tr as _tr
        return _tr(text, *args)
    except Exception:
        return text.format(*args) if args else text


def is_platform_admin(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    try:
        return PlatformAdmin.objects.filter(user_id=user.pk).exists()
    except Exception:
        return False


# ------------------------------------------------------------------ registry lookups
def link_of_schema(schema):
    """AccountCompany of a schema (cached 30 s), or None."""
    if not schema or schema == 'public':
        return None
    key = f'zeo:cloud:link:{schema}'
    hit = cache.get(key)
    if hit is not None:
        return AccountCompany.objects.select_related('account', 'account__plan', 'company').filter(pk=hit).first() if hit else None
    link = AccountCompany.objects.select_related('account', 'account__plan', 'company').filter(company__schema_name=schema).first()
    cache.set(key, link.pk if link else 0, 30)
    return link


def account_of_schema(schema):
    link = link_of_schema(schema)
    return link.account if link else None


def forget(schema=None):
    if schema:
        cache.delete(f'zeo:cloud:link:{schema}')
    cache.delete_many([k for k in ('zeo:cloud:usage',)])


def event(account, kind, text, by=None, **data):
    try:
        AccountEvent.objects.create(account=account, kind=kind, text=text[:500], by=by if getattr(by, 'pk', None) else None, data=data)
    except Exception:
        log.exception('account event')


# ------------------------------------------------------------------ status
def status_of(acc, on=None):
    """{'status': trial|active|grace|suspended|cancelled, 'read_only', 'days_left', 'until', 'expired'}."""
    d = on or timezone.localdate()
    out = {'status': acc.state, 'read_only': False, 'days_left': None, 'until': None, 'expired': False, 'blocked': False}
    if acc.state == 'cancelled':
        out.update(read_only=True, blocked=True)
    elif acc.state == 'suspended':
        out.update(read_only=True)
    elif acc.state == 'trial':
        end = acc.trial_ends
        if end and d > end:
            out.update(status='suspended', read_only=True, expired=True, until=end.isoformat())
        elif end:
            out.update(days_left=(end - d).days, until=end.isoformat())
    else:  # active
        r = acc.renews_on
        if r and d > r:
            g = r + timedelta(days=acc.grace_days)
            if d <= g:
                out.update(status='grace', days_left=(g - d).days, until=g.isoformat())
            else:
                out.update(status='suspended', read_only=True, expired=True, until=g.isoformat())
        elif r:
            out.update(days_left=(r - d).days, until=r.isoformat())
    return out


def banner_of(acc, owner=False):
    """The line shown at the top of ZEO for the account (or None)."""
    st = status_of(acc)
    s, n = st['status'], st['days_left']
    if s == 'trial' and n is not None:
        return {'kind': 'trial', 'text': tr('Free trial – {0} days left.', n) if n != 1 else tr('Free trial – 1 day left.')}
    if s == 'active' and n is not None and n <= 30:
        return {'kind': 'renewal', 'text': tr('Renewal in {0} days ({1}).', n, st['until'])} if owner else None
    if s == 'grace':
        return {'kind': 'grace', 'text': tr('Subscription expired – grace period, {0} days left. Everything still works; after that ZEO becomes read-only until the invoice is paid.', n)}
    if s == 'suspended':
        return {'kind': 'readonly', 'text': tr('Read-only. The subscription is suspended. You can view, export and print. Nothing is deleted. Adding or changing records is off until the invoice is paid.')}
    if s == 'cancelled':
        return {'kind': 'cancelled', 'text': tr('This subscription is cancelled.')}
    return None


# ------------------------------------------------------------------ usage
def company_links(acc, include_archived=False):
    qs = acc.companies.select_related('company')
    return list(qs if include_archived else qs.filter(archived=False))


def _db_conn(schema):
    """v1.24.0: a connection to the database the company lives in, on the public schema (no switch of 'default',
    so it also works inside a transaction)."""
    from . import dbrouting, owndb
    return owndb.conn(dbrouting.db_of_schema(schema) or '')


def employees_in(schema):
    from EmpManagement.models import emp_master
    try:
        with _db_conn(schema).cursor() as c:
            c.execute(f'SELECT count(*) FROM "{schema}"."{emp_master._meta.db_table}" WHERE is_active')
            return c.fetchone()[0]
    except Exception:
        return 0


def users_of(acc):
    """Staff logins of the account's companies (employees using self service only, and Adept staff, do not count)."""
    from UserManagement.models import CustomUser
    ids = [l.company_id for l in company_links(acc)]
    admins = PlatformAdmin.objects.values('user_id')
    return CustomUser.objects.filter(tenants__id__in=ids, is_active=True).exclude(is_ess=True).exclude(id__in=admins).distinct()


def storage_mb(schema):
    try:
        with _db_conn(schema).cursor() as c:
            c.execute("SELECT COALESCE(SUM(pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(tablename))),0) "
                      "FROM pg_tables WHERE schemaname=%s", [schema])
            return round(c.fetchone()[0] / 1048576, 1)
    except Exception:
        return 0


def usage(acc, fresh=False):
    key = f'zeo:cloud:usage:{acc.pk}'
    if not fresh:
        hit = cache.get(key)
        if hit:
            return hit
    links = company_links(acc)
    out = {'companies': len(links), 'extra_companies': sum(1 for l in links if l.extra),
           'employees': sum(employees_in(l.company.schema_name) for l in links),
           'users': users_of(acc).count(),
           'storage_gb': round(sum(storage_mb(l.company.schema_name) for l in links) / 1024, 2),
           'databases': DatabaseServer.objects.filter(account=acc).exclude(state='retired').count()}   # v1.26.0
    cache.set(key, out, 60)
    return out


def limits(acc):
    p = acc.plan
    allowed = p.max_companies if p.max_companies is not None else None
    if p.extra_companies:
        allowed_now = p.included_companies + acc.extra_companies
        if p.max_companies is not None:
            allowed_now = min(allowed_now, p.max_companies)
    else:
        allowed_now = p.max_companies if p.max_companies is not None else None
    return {'companies_included': p.included_companies, 'companies_cap': allowed, 'companies_paid': allowed_now,
            'extra_allowed': p.extra_companies, 'employees': acc.limit('max_employees'), 'users': acc.limit('max_users'),
            'storage_gb': acc.limit('storage_gb'), 'group_features': p.group_features,
            'own_databases': p.hosting == 'own', 'database_price': float(p.database_price) if p.database_price is not None else None}   # v1.26.0


# ------------------------------------------------------------------ app licences
def licence(acc, app):
    if app == 'hrms':
        return {'app': 'hrms', 'state': 'included', 'licensed': True}
    lic = acc.apps.filter(app=app).first()
    if lic is None or not lic.licensed:
        return {'app': app, 'state': 'none', 'licensed': False}
    d = timezone.localdate()
    st = 'trial' if lic.trial else 'licensed'
    if lic.valid_until and d > lic.valid_until:
        st = 'expired'
    return {'app': app, 'state': st, 'licensed': st != 'expired', 'companies': lic.companies, 'users': lic.users,
            'valid_until': lic.valid_until.isoformat() if lic.valid_until else None,
            'days_left': (lic.valid_until - d).days if lic.valid_until and d <= lic.valid_until else None}


def app_active_in(schema, app):
    from django_tenants.utils import schema_context
    try:
        with schema_context(schema):
            from ZeoApps.services import is_active
            return bool(is_active(app))
    except Exception:
        return False


def companies_using(acc, app, exclude_schema=None):
    return [l.company.schema_name for l in company_links(acc)
            if l.company.schema_name != exclude_schema and app_active_in(l.company.schema_name, app)]


def can_activate(acc, app, schema):
    """(ok, message) – an app may be switched on in a company if the account has a free licence place for it."""
    if app == 'hrms':
        return True, ''
    lic = licence(acc, app)
    if lic['state'] == 'none':
        return False, tr('{0} is not in your plan. Ask the account owner to request it.', APP_TITLES.get(app, app))
    if lic['state'] == 'expired':
        return False, tr('The {0} licence ended on {1}.', APP_TITLES.get(app, app), lic['valid_until'])
    n = lic.get('companies')
    if n is not None and len(companies_using(acc, app, exclude_schema=schema)) >= n:
        return False, tr('{0} is licensed for {1} companies and all are in use.', APP_TITLES.get(app, app), n)
    return True, ''


def apps_for(acc, schema=None):
    """Licence state of every app for the Settings → Apps screen."""
    out = {}
    for app in ['hrms'] + APP_KEYS:
        lic = licence(acc, app)
        if app != 'hrms' and lic['state'] in ('licensed', 'trial') and lic.get('companies') is not None:
            lic['used'] = len(companies_using(acc, app))
        out[app] = lic
    return out


# ------------------------------------------------------------------ checks used by signals / views
class LimitError(Exception):
    pass


def check_company_add(acc):
    """(ok, extra, message) for one more company in the account."""
    st = status_of(acc)
    if st['read_only']:
        return False, False, tr('The subscription is read-only – no new companies can be created.')
    lim = limits(acc)
    n = len(company_links(acc))
    cap = lim['companies_cap']
    if cap is not None and n >= cap:
        return False, False, tr('Your plan allows {0} companies and all are in use.', cap)
    if lim['extra_allowed'] and n >= lim['companies_included']:
        return True, True, tr('This is company {0}. {1} includes {2}; it will be an extra company on your subscription.', n + 1, acc.plan.name, lim['companies_included'])
    return True, False, ''


def check_database_add(acc):
    """v1.26.0: (ok, message) for one more own database. No limit: Premium only, each extra database has its own
    subscription (14-day trial, then paid per year)."""
    st = status_of(acc)
    if st['read_only']:
        return False, tr('The subscription is read-only – no new databases can be created.')
    if acc.plan.hosting != 'own':
        return False, tr('Own databases are part of Premium – ask Adept to change the plan.')
    return True, ''


DB_TRIAL_DAYS = 14


def db_price(srv):
    """Price per year of an extra database (its own price, else the plan's)."""
    if srv.price is not None:
        return float(srv.price)
    p = srv.account.plan.database_price if srv.account_id else None
    return float(p) if p is not None else None


def db_status(srv, on=None):
    """v1.26.0 subscription of one own database.
    {'status': included|trial|active|grace|expired, 'read_only', 'days_left', 'until'}
    The first database comes with Premium (included). An extra one: 14-day trial, then paid until a date; after the
    paid date the account's grace days, then read-only (view, export, print) until it is paid."""
    d = on or timezone.localdate()
    out = {'status': 'included', 'read_only': False, 'days_left': None, 'until': None}
    if not srv.extra:
        return out
    grace = srv.account.grace_days if srv.account_id else 15
    if srv.paid_until and (not srv.trial_ends or srv.paid_until >= srv.trial_ends):
        if d <= srv.paid_until:
            out.update(status='active', days_left=(srv.paid_until - d).days, until=srv.paid_until.isoformat())
        elif d <= srv.paid_until + timedelta(days=grace):
            g = srv.paid_until + timedelta(days=grace)
            out.update(status='grace', days_left=(g - d).days, until=g.isoformat())
        else:
            out.update(status='expired', read_only=True, until=srv.paid_until.isoformat())
    elif srv.trial_ends:
        if d <= srv.trial_ends:
            out.update(status='trial', days_left=(srv.trial_ends - d).days, until=srv.trial_ends.isoformat())
        else:
            out.update(status='expired', read_only=True, until=srv.trial_ends.isoformat())
    return out


def db_status_of_key(key):
    """db_status of a database key, cached 60 s (checked on every request by the middleware)."""
    if not key:
        return None
    ck = f'zeo:cloud:dbst:{key}'
    hit = cache.get(ck)
    if hit is not None:
        return hit or None
    srv = DatabaseServer.objects.select_related('account').filter(key=key).first()
    st = db_status(srv) if srv else None
    if st is not None:
        st['label'] = srv.label or srv.name
    cache.set(ck, st or {}, 60)
    return st


def db_banner(key):
    st = db_status_of_key(key)
    if not st:
        return None
    n = st['days_left']
    if st['status'] == 'trial':
        return {'kind': 'trial', 'text': tr('Database {0}: free trial – {1} days left.', st['label'], n)}
    if st['status'] == 'grace':
        return {'kind': 'grace', 'text': tr('Database {0}: subscription expired – grace period, {1} days left.', st['label'], n)}
    if st['status'] == 'expired':
        return {'kind': 'readonly', 'text': tr('Database {0} is read-only: its trial or subscription ended. You can view, export and print; ask Adept to activate it.', st['label'])}
    return None


def check_employee_add(schema):
    acc = account_of_schema(schema)
    if acc is None:
        return
    lim = acc.limit('max_employees')
    if lim is None:
        return
    u = usage(acc, fresh=True)
    if u['employees'] >= lim:
        raise LimitError(tr('Employee limit reached: your {0} plan allows {1} employees and all are in use. Employees who left do not count – mark leavers as exited, or ask the account owner to add employees to the plan.', acc.plan.name, lim))


def check_user_add(user, company_obj):
    link = AccountCompany.objects.select_related('account', 'account__plan').filter(company=company_obj).first()
    if link is None or is_platform_admin(user):
        return
    acc = link.account
    dom = (acc.allowed_email_domain or '').strip().lstrip('@').lower()
    if dom and not user.is_ess:
        em = (user.email or '').lower()
        if not em.endswith('@' + dom):
            raise LimitError(tr('{0} only allows users with an e-mail ending in @{1}.', acc.name, dom))
    lim = acc.limit('max_users')
    if lim is None or user.is_ess:
        return
    current = users_of(acc)
    if current.filter(pk=user.pk).exists():
        return
    if current.count() >= lim:
        raise LimitError(tr('User limit reached: your plan allows {0} users and all are in use.', lim))


# ------------------------------------------------------------------ jobs (company creation runs in the background)
JOB = 'zeo:cloudjob:{}'
STEPS = ['Creating the company', 'Setting up the apps', 'Giving access', 'Finishing']


def _job(job, **kw):
    d = cache.get(JOB.format(job)) or {}
    d.update(kw)
    cache.set(JOB.format(job), d, 60 * 60 * 6)


def job_status(job):
    return cache.get(JOB.format(job))


def default_country():
    from Core.models import cntry_mstr
    return (cntry_mstr.objects.filter(country_name__iexact='United Arab Emirates').first() or cntry_mstr.objects.order_by('id').first())


def schema_for(name):
    from ZeoApps.duplicate import schema_for as sf
    return sf(name)


def start_company(acc, name, industry, apps, user, extra=False, country_id=None, grant_creator=True, db=''):
    """Create a company for the account in a background thread. Returns (job, schema)."""
    job = uuid.uuid4().hex[:16]
    schema = schema_for(name)
    _job(job, state='running', step=STEPS[0], step_no=1, steps=len(STEPS), account=acc.pk, name=name, schema=schema,
         by=getattr(user, 'pk', None), company_id=None, error='')
    t = threading.Thread(target=_make_company, args=(job, acc.pk, name, schema, industry, list(apps or []), getattr(user, 'pk', None), extra, country_id, True, grant_creator, db or ''), daemon=True)
    t.start()
    return job, schema


def make_company_now(acc, name, industry, apps, user, extra=False, country_id=None):
    """Same as start_company, in this thread (management commands, tests)."""
    job = uuid.uuid4().hex[:16]
    schema = schema_for(name)
    _job(job, state='running', step=STEPS[0], step_no=1, steps=len(STEPS), account=acc.pk, name=name, schema=schema, by=getattr(user, 'pk', None))
    _make_company(job, acc.pk, name, schema, industry, list(apps or []), getattr(user, 'pk', None), extra, country_id, own_thread=False)
    return job_status(job)


def grant_admin(user, comp):
    """The user may open the company and administers it (company administrator, not a system administrator)."""
    from django_tenants.utils import schema_context
    from tenant_users.tenants.models import UserTenantPermissions
    user.tenants.add(comp)
    with schema_context(comp.schema_name):
        utp, _ = UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_superuser': True, 'is_staff': True})
        if not utp.is_superuser:
            utp.is_superuser = True
            utp.save(update_fields=['is_superuser'])


def _make_company(job, acc_id, name, schema, industry, apps, user_id, extra, country_id, own_thread=True, grant_creator=True, db=''):
    from django.db import close_old_connections
    if own_thread:
        close_old_connections()
    try:
        from UserManagement.models import company as Company, CustomUser
        connection.set_schema_to_public()
        acc = Account.objects.get(pk=acc_id)
        apps = [a for a in apps if a in APP_KEYS]
        country = None
        if country_id:
            from Core.models import cntry_mstr
            country = cntry_mstr.objects.filter(pk=country_id).first()
        comp = Company(name=name[:100], schema_name=schema, country=country or default_country(), industry_type=industry or 'OTH')
        if template_ready():   # seconds: copy of the ready template schema (kept up to date by migrate_schemas)
            from django_tenants.clone import CloneSchema
            CloneSchema().clone_schema(TEMPLATE, schema, clone_mode='DATA')
            comp.auto_create_schema = False
        comp.save()   # without the template: creates the schema and runs the company migrations (about 2 minutes)
        AccountCompany.objects.create(company=comp, account=acc, extra=extra, db_alias='')   # made in the main database
        forget(schema)
        _job(job, step=STEPS[1], step_no=2)
        from ZeoApps.services import setup_new_company
        user = CustomUser.objects.filter(pk=user_id).first()
        done, failed = setup_new_company(comp, ['hrms'] + apps, user)
        _job(job, step=STEPS[2], step_no=3)
        people = {u for u in [acc.owner, user if grant_creator else None] if u is not None}   # Adept staff are not added
        for u in people:
            grant_admin(u, comp)
        _job(job, step=STEPS[3], step_no=4)
        moved = ''
        target = db or acc.db_alias   # v1.26.0: the database chosen on Create company, else the account's default one
        if target and not DatabaseServer.objects.filter(key=target, account=acc, state='ready').exists():
            target = acc.db_alias
        if target:   # v1.24.0 Premium: the new company goes to the account's own database (new – no wait needed)
            from . import owndb
            connection.set_schema_to_public()
            mv = owndb.start_move(comp, target, by=user, settle=0, run_now=True, account=acc)
            moved = mv.state if mv.state == 'done' else f'not moved: {mv.error}'[:200]
        event(acc, 'company', f'Company created – {comp.name}' + (' (extra company)' if extra else ''), user, schema=schema, apps=done)
        cache.delete(f'zeo:cloud:usage:{acc.pk}')
        _job(job, state='done', step='Done', step_no=len(STEPS), company_id=comp.pk, apps=done, failed=failed, moved=moved)
    except Exception as e:
        log.exception('cloud company')
        _job(job, state='failed', error=str(e)[:300])
        try:
            from UserManagement.models import company as Company
            from django_tenants.utils import schema_exists
            connection.set_schema_to_public()
            c = Company.objects.filter(schema_name=schema).first()
            if c is not None:
                c.delete(force_drop=True)
            elif schema_exists(schema):
                with connection.cursor() as cur:
                    cur.execute(f'DROP SCHEMA "{schema}" CASCADE')
        except Exception:
            log.exception('clean-up after a failed company')
    finally:
        if own_thread:
            try:
                connection.set_schema_to_public()
            except Exception:
                pass
            close_old_connections()


# ------------------------------------------------------------------ accounts
def plan_defaults():
    """The four plans (created once by `cloud_setup`; limits and prices are edited in the console)."""
    return [
        dict(code='standard', name='Standard', order=1, included_companies=1, max_companies=1, extra_companies=False, max_employees=50, max_users=5, storage_gb=10, group_features=False, hosting='shared'),
        dict(code='advance', name='Advance', order=2, included_companies=3, max_companies=10, extra_companies=True, max_employees=500, max_users=40, storage_gb=50, group_features=False, hosting='shared'),
        dict(code='premium', name='Premium', order=3, included_companies=1, max_companies=None, extra_companies=False, max_employees=1500, max_users=100, storage_gb=200, group_features=True, hosting='own'),
        dict(code='enterprise', name='Enterprise', order=4, included_companies=1, max_companies=None, extra_companies=False, max_employees=None, max_users=None, storage_gb=None, group_features=True, hosting='enterprise', active=False),
    ]


def ensure_plans():
    made = 0
    for d in plan_defaults():
        _, c = Plan.objects.get_or_create(code=d['code'], defaults=d)
        made += c
    return made


def create_account(name, owner_email, plan, by=None, owner=None, trial_days=14, trn='', country='United Arab Emirates', apps=(), limits=None, start=None):
    """A new customer account (trial by default). Apps: the paid apps it may use."""
    with transaction.atomic():
        acc = Account(name=name[:150], owner=owner, owner_email=owner_email, plan=plan, trn=trn or '', country=country or '',
                      hosting=plan.hosting if plan.hosting != 'enterprise' else 'enterprise', created_by=by if getattr(by, 'pk', None) else None)
        d = timezone.localdate()
        if start:
            acc.state, acc.renews_on = 'active', start
        else:
            acc.state, acc.trial_ends = 'trial', d + timedelta(days=trial_days)
        for k, v in (limits or {}).items():
            if k in ('max_employees', 'max_users', 'storage_gb') and v not in (None, ''):
                setattr(acc, k, int(v))
        acc.save()
        for a in apps:
            if a in APP_KEYS:
                AppLicence.objects.create(account=acc, app=a, licensed=True, trial=acc.state == 'trial',
                                          valid_until=acc.trial_ends if acc.state == 'trial' else None)
        event(acc, 'account', f'Account created – plan {plan.name}' + (f', trial until {acc.trial_ends}' if acc.trial_ends else ''), by)
    return acc


def mark_paid(inv, by=None):
    inv.state, inv.paid_on = 'paid', timezone.localdate()
    inv.save(update_fields=['state', 'paid_on'])
    acc = inv.account
    changed = []
    if acc.state in ('trial', 'suspended'):
        acc.state = 'active'
        changed.append('state')
    if inv.period_to and (acc.renews_on is None or inv.period_to > acc.renews_on):
        acc.renews_on = inv.period_to
        changed.append('renews_on')
    if acc.state == 'active' and not acc.renews_on:
        acc.renews_on = timezone.localdate() + timedelta(days=365)
        changed.append('renews_on')
    if changed:
        acc.save(update_fields=list(set(changed)))
    # trial app licences become normal licences once paid
    acc.apps.filter(trial=True).update(trial=False, valid_until=acc.renews_on)
    event(acc, 'invoice', f'Invoice {inv.number} marked as paid', by, invoice=inv.pk)
    return acc


def notice_removed(user, acc):
    try:
        AccessNotice.objects.create(user=user, text=tr('Your access to {0} was removed by its administrator.', acc.name))
        from django.core.mail import send_mail
        if user.email:
            send_mail(tr('Your access to {0} was removed', acc.name),
                      tr('Your access to the companies of {0} in ZEO was removed by its administrator. Your other ZEO companies are not affected.', acc.name),
                      None, [user.email], fail_silently=True)
    except Exception:
        log.exception('access notice')


VALID_DOMAIN = re.compile(r'^[a-z0-9.-]+\.[a-z]{2,}$')


def app_licence_state(acc, app):
    key = f'zeo:cloud:lic:{acc.pk}:{app}'
    hit = cache.get(key)
    if hit:
        return hit
    s = licence(acc, app)['state']
    cache.set(key, s, 30)
    return s


TEMPLATE = 'zeo_template'


def template_ready():
    """The template company (management command cloud_template) exists and is migrated."""
    try:
        from UserManagement.models import company as Company
        from django_tenants.utils import schema_exists
        return Company.objects.filter(schema_name=TEMPLATE).exists() and schema_exists(TEMPLATE)
    except Exception:
        return False
