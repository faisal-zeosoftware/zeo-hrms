"""v1.62.0 – security events and the e-mails to the company administrators.

Every event is a row of ZeoCloud.SecurityEvent (public schema – logins are shared by the companies): the security dashboard
reads them. The serious kinds are e-mailed to the administrators of the companies concerned when the company keeps
"E-mail the administrators about security events" on (Settings › Sign-in security, on by default):
  a new administrator, a permission group given / changed, an API key created, single sign-on changed, an authenticator reset,
  a spent sign-in token used again, a sign-in used on another device, many wrong passwords in a short time, a sign-in refused
  by country, an upload refused as a virus, an export above the row limit.
The same kind for the same company and user is e-mailed at most once an hour.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)

MAIL_KINDS = {'admin_granted', 'rights_changed', 'api_key', 'sso_changed', '2fa_reset', 'refresh_reuse', 'device_changed', 'failed_burst',
              'country_refused', 'upload_refused', 'export_refused', 'history_cleaned', 'bank_change'}
BURST_N, BURST_MIN = 10, 15

SUBJECTS = {
    'admin_granted': 'a user was given administrator rights',
    'rights_changed': 'permission groups of a user were changed',
    'api_key': 'an API key was created',
    'sso_changed': 'single sign-on was changed',
    '2fa_reset': 'an authenticator app was reset',
    'refresh_reuse': 'a spent sign-in token was used again (possible theft)',
    'device_changed': 'a sign-in was used on another device',
    'failed_burst': 'many wrong passwords in a short time',
    'country_refused': 'a sign-in from a country that is not allowed',
    'upload_refused': 'an upload was refused (virus or blocked file)',
    'export_refused': 'an export was refused',
    'history_cleaned': 'logs and history were cleaned',
    'bank_change': 'a bank-detail change waits for a second person',
}


def _schemas_of(user, schema=''):
    if schema:
        return [schema]
    if user is None or not getattr(user, 'pk', None):
        return []
    try:
        return [t.schema_name for t in user.tenants.all()]
    except Exception:
        return []


def admins_of(schema):
    """E-mail addresses of the company's administrators (company superusers)."""
    from django_tenants.utils import schema_context
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        with schema_context(schema):
            ids = list(UserTenantPermissions.objects.filter(is_superuser=True).values_list('profile_id', flat=True))
    except Exception:
        return []
    from django.contrib.auth import get_user_model
    return sorted({e for e in get_user_model().objects.filter(pk__in=ids, is_active=True).values_list('email', flat=True) if e})


def _policy_on(schema):
    from .models import SecurityPolicy
    p = SecurityPolicy.objects.filter(schema=schema).values_list('alert_admins', flat=True).first()
    return True if p is None else bool(p)


def _name(u):
    if u is None:
        return ''
    return (u.get_full_name() or u.username or u.email or '').strip()


def _mail(ev, schema):
    if not _policy_on(schema):
        return False
    key = f'zeo:secmail:{schema}:{ev.kind}:{ev.user_id or 0}'
    if cache.get(key):
        return False
    to = admins_of(schema)
    if not to:
        return False
    from django.core.mail import send_mail
    from UserManagement.models import company
    cname = getattr(company.objects.filter(schema_name=schema).first(), 'name', schema)
    when = timezone.localtime(ev.created_at).strftime('%d %b %Y %H:%M')
    lines = [f'Company: {cname}', f'When: {when}']
    if ev.user_id:
        lines.append(f'User: {_name(ev.user)} ({getattr(ev.user, "email", "") or getattr(ev.user, "username", "")})')
    if ev.by_id and ev.by_id != ev.user_id:
        lines.append(f'Done by: {_name(ev.by)}')
    if ev.ip:
        lines.append(f'Address: {ev.ip}')
    if ev.detail:
        lines.append('')
        lines.append(ev.detail)
    lines += ['', 'Open Settings › Sign-in security › Security dashboard in ZEO to see the details.',
              'You get this e-mail because you are an administrator of the company. It can be switched off in Settings › Sign-in security.']
    try:
        send_mail(f'ZEO security: {SUBJECTS.get(ev.kind, ev.get_kind_display())}', '\n'.join(lines),
                  getattr(settings, 'DEFAULT_FROM_EMAIL', None), to, fail_silently=False)
    except Exception:
        log.exception('security mail')
        return False
    cache.set(key, 1, 3600)
    return True


def record(kind, user=None, by=None, schema='', ip='', detail='', data=None, notify=True):
    from django_tenants.utils import schema_context
    from .models import SecurityEvent
    with schema_context('public'):
        ev = SecurityEvent.objects.create(kind=kind, user=user if getattr(user, 'pk', None) else None, by=by if getattr(by, 'pk', None) else None,
                                          schema=schema or '', ip=(ip or '')[:60], detail=(detail or '')[:400], data=data or {})
        sent = False
        if notify and kind in MAIL_KINDS:
            for sch in _schemas_of(user, schema):
                sent = _mail(ev, sch) or sent
        if sent:
            SecurityEvent.objects.filter(pk=ev.pk).update(emailed=True)
        if kind in ('failed_password', 'failed_code'):
            _burst(user, ip)
    return ev


def _burst(user, ip):
    """Many wrong passwords for a company's users in a short time → one event (and e-mail) for the company."""
    from .models import SecurityEvent
    for sch in _schemas_of(user):
        k = f'zeo:burst:{sch}'
        if cache.get(k):
            continue
        since = timezone.now() - timedelta(minutes=BURST_MIN)
        from django.contrib.auth import get_user_model
        ids = get_user_model().objects.filter(tenants__schema_name=sch).values('pk')
        n = SecurityEvent.objects.filter(kind__in=('failed_password', 'failed_code'), created_at__gte=since, user_id__in=ids).count()
        if n >= BURST_N:
            cache.set(k, 1, 3600)
            record('failed_burst', schema=sch, ip=ip, detail=f'{n} wrong passwords / codes for the users of the company in the last {BURST_MIN} minutes '
                   f'(last from {ip or "?"}).', data={'count': n})


def event_json(e):
    return {'id': e.id, 'kind': e.kind, 'kind_text': e.get_kind_display(), 'user': _name(e.user), 'user_email': getattr(e.user, 'email', '') if e.user_id else '',
            'by': _name(e.by), 'ip': e.ip, 'detail': e.detail, 'emailed': e.emailed, 'at': e.created_at, 'schema': e.schema}
