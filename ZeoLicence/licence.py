"""v1.28.0 – the licence of this installation (Enterprise servers).

Checks are on only with ZEO_EDITION=enterprise in .env – every other server (ZEO Cloud, servers installed before
1.28.0) works as before.

Status:
  valid      the newest uploaded file is signed by Adept, for this installation, and today is in its period
  expiring   valid, ends within 30 days (banner)
  grace      ended less than ZEO_LICENCE_GRACE_DAYS (15) days ago – everything works, banner to administrators
  expired    ended longer ago – read-only (view, export, print; saving refused)
  evaluation no file yet, within ZEO_LICENCE_EVALUATION_DAYS (30) days of the installation – everything works
  missing    no valid file and the evaluation ended – read-only
"""
import datetime as dt
import time

from django.conf import settings
from django.utils import timezone

from .signing import LicenceError, verify

APPS_ALWAYS = ('settings', 'hrms')       # Settings and HR come with every licence
_cache = {'until': 0, 'value': None}
TTL = 30


def edition_on():
    return str(getattr(settings, 'ZEO_EDITION', '') or '').lower() == 'enterprise'


def installation_id():
    from .models import Installation
    return Installation.current().installation_id


def _days(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def forget():
    _cache['until'] = 0


def _parse_date(s):
    return dt.date.fromisoformat(str(s)[:10]) if s else None


def check_text(text):
    """Payload of a licence text for THIS installation, or LicenceError."""
    payload, kid = verify(text)
    if payload.get('product') != 'zeo' or payload.get('edition') != 'enterprise':
        raise LicenceError('This is not a ZEO Enterprise licence.')
    inst = installation_id()
    if str(payload.get('installation_id') or '') != inst:
        raise LicenceError(f'This licence is for installation {payload.get("installation_id")}, this server is {inst}.')
    if not _parse_date(payload.get('valid_until')):
        raise LicenceError('The licence has no end date.')
    payload['_key'] = kid
    return payload


def current(fresh=False):
    if not fresh and _cache['until'] > time.time() and _cache['value'] is not None:
        return _cache['value']
    from .models import Installation, LicenceFile
    today = timezone.localdate()
    out = {'edition': 'enterprise' if edition_on() else '', 'installation_id': installation_id(), 'licence': None,
           'status': 'off', 'read_only': False, 'days_left': None, 'message': '', 'problem': ''}
    lic, problem = None, ''
    for f in LicenceFile.objects.all()[:10]:            # newest first; the newest file that checks counts
        try:
            lic = check_text(f.text)
            break
        except LicenceError as e:
            problem = problem or str(e)
    grace = _days('ZEO_LICENCE_GRACE_DAYS', 15)
    if lic:
        vf, vu = _parse_date(lic.get('valid_from')) or today, _parse_date(lic['valid_until'])
        out['licence'] = {k: v for k, v in lic.items() if not k.startswith('_')}
        left = (vu - today).days
        out['days_left'] = left
        if today < vf:
            status = 'not_yet'
        elif left >= 0:
            status = 'expiring' if left <= 30 else 'valid'
        elif -left <= grace:
            status = 'grace'
        else:
            status = 'expired'
    else:
        inst = Installation.current()
        ev = _days('ZEO_LICENCE_EVALUATION_DAYS', 30)
        used = (today - timezone.localtime(inst.created_at).date()).days
        out['days_left'] = ev - used
        status = 'evaluation' if used <= ev else 'missing'
        out['problem'] = problem
    out['status'] = status if edition_on() else 'off'
    out['read_only'] = edition_on() and status in ('expired', 'missing', 'not_yet')
    out['message'] = message(out)
    _cache['value'], _cache['until'] = out, time.time() + TTL
    return out


def message(st):
    from Translations.i18n import tr
    s = st['status']
    if s == 'expiring':
        return tr('The ZEO licence ends in {0} days. Ask Adept for the renewal and upload the new file in Settings → Licence.', st['days_left'])
    if s == 'grace':
        return tr('The ZEO licence has ended. Everything works for {0} more days – then ZEO becomes read-only. Upload the new licence file in Settings → Licence.',
                  max(0, _days('ZEO_LICENCE_GRACE_DAYS', 15) + (st['days_left'] or 0)))
    if s == 'expired':
        return tr('The ZEO licence has ended – ZEO is read-only (view, export, print). Upload the new licence file in Settings → Licence.')
    if s == 'evaluation':
        return tr('No licence file yet – {0} days left to upload it (Settings → Licence).', max(0, st['days_left'] or 0))
    if s == 'missing':
        return tr('No valid licence file – ZEO is read-only. Upload the licence file from Adept in Settings → Licence.')
    if s == 'not_yet':
        return tr('The uploaded licence starts later – ZEO is read-only until then.')
    return ''


# ------------------------------------------------------------------ what the licence allows
def _lic():
    st = current()
    return st.get('licence') or {}


def feature_on(name):
    if not edition_on():
        return True
    st = current()
    if st['status'] in ('evaluation',):
        return True
    return name in (_lic().get('features') or [])


def app_allowed(app):
    if not edition_on() or app in APPS_ALWAYS:
        return True
    st = current()
    if st['status'] == 'evaluation':
        return True
    apps = _lic().get('apps')
    return apps is None or app in apps


def limit(name):
    if not edition_on():
        return None
    v = (_lic().get('limits') or {}).get(name)
    try:
        return int(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def usage():
    from django.apps import apps as A
    from django_tenants.utils import schema_context
    from UserManagement.models import CustomUser, company as Company
    comps = list(Company.objects.exclude(schema_name__in=('public', 'zeo_template')))
    emps = 0
    for c in comps:
        try:
            with schema_context(c.schema_name):
                emps += A.get_model('EmpManagement', 'emp_master').objects.filter(is_active=True).count()
        except Exception:
            pass
    users = CustomUser.objects.filter(is_active=True).exclude(is_ess=True).count()
    return {'companies': len(comps), 'employees': emps, 'users': users}


class LimitReached(Exception):
    pass


def check_add(name, current_count):
    from Translations.i18n import tr
    lim = limit(name)
    if lim is not None and current_count >= lim:
        label = {'companies': tr('companies'), 'employees': tr('employees'), 'users': tr('users')}.get(name, name)
        raise LimitReached(tr('The ZEO licence allows {0} {1} and all are in use. Ask Adept for a new licence file.', lim, label))
