"""v1.73.16 – whose day is "today".

Three levels, the way Odoo does it, but with ZEO's branches:

* the **user** – what a person does on the screens: CustomUser.timezone when set; for a self-service user without one, the time
  zone of the branch of their employee record (a Riyadh employee's day is Riyadh's);
* the **branch** – the day of an employee's own records: OrgStructure.BranchProfile.timezone (Branch details › Time zone), else
  the company's;
* the **company** – jobs with nobody signed in (expiry reminders, accruals, daily jobs): company.timezone, else the company's
  country, else Asia/Dubai.

The request middleware (UserManagement.middleware.TenantTimezoneMiddleware) activates the user's zone; `today()` gives the date
in the active zone and, outside a request (Celery, management commands), in the zone of the company whose schema is active –
never the server's UTC day (between 00:00 and 04:00 in the UAE that was yesterday).
"""
import contextvars
import logging
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
DEFAULT = 'Asia/Dubai'
_ACTIVE = contextvars.ContextVar('zeo_tz_active', default=None)   # the zone a request activated (name)


@lru_cache(maxsize=512)
def zone(name):
    """a ZoneInfo for a name, or None when the name is empty or not a known zone"""
    n = str(name or '').strip()
    if not n:
        return None
    try:
        return ZoneInfo(n)
    except Exception:   # noqa: BLE001
        return None


def valid(name):
    return zone(name) is not None


def _schema():
    try:
        from django.db import connection
        return getattr(connection, 'schema_name', '') or ''
    except Exception:   # noqa: BLE001
        return ''


import time as _time

TTL = 300   # seconds a zone is remembered (a change in Company / Branch settings shows within 5 minutes in every worker)
_COMPANY = {}   # schema -> (zone name, when)
_BRANCH = {}    # (schema, branch) -> (zone name, when)


def company_zone_name(schema=None):
    s = schema if schema is not None else _schema()
    hit = _COMPANY.get(s)
    if hit and _time.time() - hit[1] < TTL:
        return hit[0]
    name = ''
    try:
        from django.db import connection
        from django_tenants.utils import get_public_schema_name
        tenant = getattr(connection, 'tenant', None)
        if tenant is None or getattr(tenant, 'schema_name', None) != s or not hasattr(tenant, 'country'):
            from UserManagement.models import company as Co
            from django_tenants.utils import schema_context
            with schema_context(get_public_schema_name()):
                tenant = Co.objects.select_related('country').filter(schema_name=s).first()
        if tenant is not None:
            name = (getattr(tenant, 'timezone', '') or '').strip()
            if not valid(name):
                c = getattr(tenant, 'country', None)
                name = (getattr(c, 'timezone', '') or '').strip() if c else ''
    except Exception:   # noqa: BLE001
        log.debug('company zone', exc_info=True)
    name = name if valid(name) else DEFAULT
    _COMPANY[s] = (name, _time.time())
    return name


def forget(schema=None):
    """after a company's, branch's or user's zone changed (this worker at once, the others within TTL)"""
    s = schema if schema is not None else _schema()
    _COMPANY.pop(s, None)
    for d in (_BRANCH, _USER):
        for k in [k for k in d if k[0] == s]:
            d.pop(k, None)


def _branch_zone(schema, branch_id):
    hit = _BRANCH.get((schema, branch_id))
    if hit and _time.time() - hit[1] < TTL:
        return hit[0]
    n = _branch_lookup(branch_id)
    _BRANCH[(schema, branch_id)] = (n, _time.time())
    return n


def _branch_lookup(branch_id):
    """the zone set on the branch (Branch details › Time zone). Not the branch's country: brnch_mstr.br_country defaults to
    the first country of the list, so branches never given a country would run on its time (found in the 1.73.16 tests)."""
    try:
        from django.apps import apps
        BP = apps.get_model('OrgStructure', 'BranchProfile')
        n = (BP.objects.filter(branch_id=branch_id).values_list('timezone', flat=True).first() or '').strip()
        if valid(n):
            return n
    except Exception:   # noqa: BLE001
        log.debug('branch zone', exc_info=True)
    return ''


def branch_zone_name(branch_id):
    """the branch's zone, else the company's"""
    if not branch_id:
        return company_zone_name()
    return _branch_zone(_schema(), int(branch_id)) or company_zone_name()


def employee_zone_name(emp):
    return branch_zone_name(getattr(emp, 'emp_branch_id_id', None) if emp is not None else None)


_USER = {}   # (schema, user id) -> (zone name, when)


def user_zone_name(user_or_id):
    """the user's own zone; a self-service user without one takes the branch of their employee record; else the company's"""
    uid = getattr(user_or_id, 'pk', user_or_id)
    key = (_schema(), uid)
    hit = _USER.get(key)
    if hit and _time.time() - hit[1] < TTL:
        return hit[0]
    n = _user_lookup(user_or_id)
    _USER[key] = (n, _time.time())
    return n


def _user_lookup(user_or_id):
    try:
        from UserManagement.models import CustomUser
        u = user_or_id if hasattr(user_or_id, 'pk') else CustomUser.objects.filter(pk=user_or_id).only('id', 'timezone').first()
        if u is not None:
            n = (getattr(u, 'timezone', '') or '').strip()
            if valid(n):
                return n
            from django.apps import apps
            E = apps.get_model('EmpManagement', 'emp_master')
            b = E.objects.filter(users_id=u.pk).values_list('emp_branch_id_id', flat=True).first()
            if b:
                return branch_zone_name(b)
    except Exception:   # noqa: BLE001
        log.debug('user zone', exc_info=True)
    return company_zone_name()


def activate(name):
    """make `name` the zone of this request / job (Django's current time zone and today())"""
    from django.utils import timezone as djtz
    z = zone(name) or zone(DEFAULT)
    djtz.activate(z)
    return _ACTIVE.set(str(z.key))


def deactivate(token=None):
    from django.utils import timezone as djtz
    djtz.deactivate()
    if token is not None:
        try:
            _ACTIVE.reset(token)
        except Exception:   # noqa: BLE001
            _ACTIVE.set(None)
    else:
        _ACTIVE.set(None)


def current_zone_name():
    return _ACTIVE.get() or company_zone_name()


def now():
    """the aware date-time now in the active (or the company's) zone"""
    from django.utils import timezone as djtz
    return djtz.now().astimezone(zone(current_zone_name()))


def today():
    """the date today for the person (in a request) or the company (in a job) – never the server's UTC day"""
    return now().date()


def today_for_employee(emp):
    """the employee's own day (their branch's zone)"""
    from django.utils import timezone as djtz
    return djtz.now().astimezone(zone(employee_zone_name(emp))).date()


def today_for_branch(branch_id):
    from django.utils import timezone as djtz
    return djtz.now().astimezone(zone(branch_zone_name(branch_id))).date()


def local(dt):
    """an aware date-time shown in the active zone (naive ones are taken as UTC)"""
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    from django.utils import timezone as djtz
    if djtz.is_naive(dt):
        dt = djtz.make_aware(dt, ZoneInfo('UTC'))
    return dt.astimezone(zone(current_zone_name()))


# --------------------------------------------------------------------------------------------- the user's own choice (v1.73.16)
CHOICES = [   # the zones of the countries ZEO has rules for, then the common others
    ('Asia/Dubai', 'UAE, Oman (UTC+4)'), ('Asia/Muscat', 'Oman (UTC+4)'), ('Asia/Riyadh', 'Saudi Arabia (UTC+3)'), ('Asia/Kuwait', 'Kuwait (UTC+3)'),
    ('Asia/Qatar', 'Qatar (UTC+3)'), ('Asia/Bahrain', 'Bahrain (UTC+3)'), ('Asia/Kolkata', 'India (UTC+5:30)'), ('Asia/Karachi', 'Pakistan (UTC+5)'),
    ('Asia/Manila', 'Philippines (UTC+8)'), ('Asia/Dhaka', 'Bangladesh (UTC+6)'), ('Asia/Kathmandu', 'Nepal (UTC+5:45)'), ('Asia/Colombo', 'Sri Lanka (UTC+5:30)'),
    ('Africa/Cairo', 'Egypt'), ('Asia/Amman', 'Jordan (UTC+3)'), ('Asia/Beirut', 'Lebanon'), ('Europe/London', 'United Kingdom'),
    ('Europe/Berlin', 'Central Europe'), ('America/New_York', 'US East'), ('UTC', 'UTC'),
]


def explain(user):
    """{timezone (own, '' = automatic), effective, source: user | branch | company} for the time zone menu"""
    own = (getattr(user, 'timezone', '') or '').strip()
    if valid(own):
        return {'timezone': own, 'effective': own, 'source': 'user'}
    try:
        from django.apps import apps
        b = apps.get_model('EmpManagement', 'emp_master').objects.filter(users_id=user.pk).values_list('emp_branch_id_id', flat=True).first()
    except Exception:   # noqa: BLE001
        b = None
    if b:
        return {'timezone': '', 'effective': branch_zone_name(b), 'source': 'branch'}
    return {'timezone': '', 'effective': company_zone_name(), 'source': 'company'}
