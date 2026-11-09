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
    """E-mail addresses of the company's administrators."""
    # v1.67.7 (admin): the administrators OF THIS COMPANY (ZeoCloud/companyadmin.py – per company, the owner by default)
    try:
        from .companyadmin import admin_emails
        return admin_emails(schema)
    except Exception:
        log.exception('administrators of %s', schema)
        return []


def admin_people(schema):
    """v1.67.7 (admin): the administrators of the company as logins (each gets the e-mail in their own language)."""
    try:
        from .companyadmin import admin_users
        return [u for u in admin_users(schema) if u.email]
    except Exception:
        log.exception('administrators of %s', schema)
        return []


def _policy_on(schema):
    from .models import SecurityPolicy
    p = SecurityPolicy.objects.filter(schema=schema).values_list('alert_admins', flat=True).first()
    return True if p is None else bool(p)


# v1.67.8 (fixes) item 10: every security event sentence (stored filled in, in English). For each reader the stored sentence is
# matched against these templates and the template is translated with the same values (the values translated too where they
# are standard words) – older events are translated the same way.
SENTENCES = [
    '{0} was made an administrator of the company.',
    '{0} is no longer an administrator of the company.',
    'Permission group(s) given: {0}.',
    'Permission group(s) taken away: {0}.',
    'API key "{0}" created (read only).',
    'API key "{0}" created (read and write).',
    'Single sign-on "{0}" added (domains: {1}).',
    'Single sign-on "{0}" changed (domains: {1}).',
    'sign-in of {0} used on another device',
    'a spent sign-in token of {0} was used again from {1} – the sign-in was ended',
    'Sign-in refused: address {0} is in {1}.',
    'Sign-in with the right password refused: address {0} is in {1}.',
    'The authenticator app of {0} was reset by {1}.',
    '{0} wrong passwords / codes for the users of the company in the last {1} minutes (last from {2}).',
    'Bank-detail change for {0} ({1}) waits for a second person.',
    'Export of "{0}" refused: {1}.',
    'Export of {0} refused: {1}.',
    '{0} rows – more than the limit of {1}',
    '{0} other sign-in(s) ended',
    # logs and history cleaned (ZeoAdmin.history._sentence)
    'Removed {0} – {1} (archive {2}, {3} rows) – stopped at the time limit, the rest goes next time',
    'Removed {0} – {1} – stopped at the time limit, the rest goes next time',
    'Removed {0} – {1} (archive {2}, {3} rows)',
    'Removed {0} – {1}',
    '{0} {1} older than {2} days',
]
_RX = None


def _compiled():
    global _RX
    if _RX is None:
        import re
        out = []
        for t in SENTENCES:
            rx = '^' + re.sub(r'\\\{(\d+)\\\}', r'(.+?)', re.escape(t)) + '$'
            if t.startswith('Removed {0}'):   # the cleaned categories contain " – " themselves: the last " – " ends the list
                rx = rx.replace('(.+?)', '(.+)', 1)
            out.append((re.compile(rx, re.S), t))
        out.sort(key=lambda x: -len(x[1]))
        _RX = out
    return _RX


def sentence(text, lang, depth=0):
    """v1.67.8 (fixes) item 10: a stored event sentence in `lang` (names and values filled in as stored)."""
    from .mailtext import T
    if not text or not lang or str(lang).startswith('en') or depth > 3:
        return text
    t = T(text, lang)
    if t != text:
        return t
    if text[:1].islower():   # a standard word written in lower case inside the sentence ("bell notifications – read")
        up = text[:1].upper() + text[1:]
        t = T(up, lang)
        if t != up:
            return t
    ar = str(lang).startswith('ar')
    for rx, tpl in _compiled():
        m = rx.match(text)
        if m:
            return T(tpl, lang, *[sentence(g, lang, depth + 1) for g in m.groups()])
    if '; ' in text:   # a list of parts ("x older than 90 days; y older than 30 days")
        return ('؛ ' if ar else '; ').join(sentence(x, lang, depth + 1) for x in text.split('; '))
    return text


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
    people = admin_people(schema)   # v1.67.7 (admin): this company's administrators, each in their own language
    if not people:
        return False
    from .mailtext import T, lang_of, message
    from UserManagement.models import company
    cname = getattr(company.objects.filter(schema_name=schema).first(), 'name', schema)
    when = timezone.localtime(ev.created_at).strftime('%d/%m/%Y %H:%M')
    sent = False
    for p in people:
        L = lang_of(p, schema)
        lines = [T('Company: {0}', L, cname), T('When: {0}', L, when)]
        if ev.user_id:
            lines.append(T('User: {0} ({1})', L, _name(ev.user), getattr(ev.user, "email", "") or getattr(ev.user, "username", "")))
        if ev.by_id and ev.by_id != ev.user_id:
            lines.append(T('Done by: {0}', L, _name(ev.by)))
        if ev.ip:
            lines.append(T('Address: {0}', L, ev.ip))
        if ev.detail:
            lines.append('')
            lines.append(sentence(ev.detail, L))   # v1.67.8 (fixes) item 10: the event sentence in the reader's language too
        lines += ['', T('Open Settings › Sign-in security › Security dashboard in ZEO to see the details.', L),
                  T('You get this e-mail because you are an administrator of the company. It can be switched off in Settings › Sign-in security.', L)]
        subject = T('ZEO security: {0}', L, T(SUBJECTS.get(ev.kind, ev.get_kind_display()), L))
        try:
            message(subject, '\n'.join(lines), L, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [p.email]).send()
            sent = True
        except Exception:
            log.exception('security mail')
    if not sent:
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
            'by': _name(e.by), 'ip': e.ip, 'detail': _detail_for_screen(e.detail), 'emailed': e.emailed, 'at': e.created_at, 'schema': e.schema}


def _detail_for_screen(text):
    """v1.67.8 (fixes) item 10: the security dashboard shows the sentence in the user's language too."""
    try:
        from Translations.i18n import current_lang
        return sentence(text, current_lang())
    except Exception:   # noqa: BLE001
        return text
