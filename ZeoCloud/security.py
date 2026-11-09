"""v1.52.0 – sign-in security (users are shared, so this lives in the public schema):

* authenticator app (TOTP, RFC 6238 – Google / Microsoft Authenticator, Authy …): the secret is kept encrypted; a code works
  once; 10 recovery codes (hashed). With it switched on, /users/token/ answers {two_factor: 'totp', challenge} and the tokens
  come from /users/2fa/login/ with the code;
* sign-in sessions: every sign-in gets a session id (claim 'sid' in the tokens); "sign out" / "sign out everywhere" ends
  it and its tokens are refused at once (SessionJWTAuthentication, refresh view);
* password rules of the company (length, upper / lower case, digits, symbols, expiry, no reuse) – a user of several
  companies gets the strictest; lock after N wrong passwords / codes;
* impersonation: a company administrator opens ZEO as a user (read only by default), written to the audit log.
"""
import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

ISSUER = 'ZEO HRMS'
STEP = 30
CACHE_S = 30


# ------------------------------------------------------------------ crypto helpers
def _fernet():
    from cryptography.fernet import Fernet
    seed = getattr(settings, 'ZEO_SECURITY_KEY', '') or settings.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('zeo-login-security:' + seed).encode()).digest()))


def enc(v):
    return _fernet().encrypt(v.encode()).decode() if v else ''


def dec(v):
    try:
        return _fernet().decrypt(v.encode()).decode() if v else ''
    except Exception:
        return ''


def sha(v):
    return hashlib.sha256(str(v).encode()).hexdigest()


# ------------------------------------------------------------------ TOTP (RFC 6238, SHA-1, 6 digits, 30 s)
def new_secret():
    return base64.b32encode(os.urandom(20)).decode().rstrip('=')


def totp(secret, step):
    key = base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)
    h = hmac.new(key, struct.pack('>Q', step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000).zfill(6)


def match_step(secret, code, window=1, at=None):
    """The 30-second step the code belongs to (±1 step for clock drift), or None."""
    code = re.sub(r'\s', '', str(code or ''))
    if not re.fullmatch(r'\d{6}', code):
        return None
    t = int((at or time.time()) // STEP)
    for w in range(-window, window + 1):
        if hmac.compare_digest(totp(secret, t + w), code):
            return t + w
    return None


def otpauth_uri(secret, account):
    from urllib.parse import quote
    return f'otpauth://totp/{quote(ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period={STEP}'


def sec(user):
    from .models import LoginSecurity
    s, _ = LoginSecurity.objects.get_or_create(user=user)
    return s


def totp_on(user):
    from .models import LoginSecurity
    return LoginSecurity.objects.filter(user=user, totp_on=True).exists()


def check_code(user, code):
    """Authenticator code or a recovery code. True / False (counts wrong codes; locks)."""
    s = sec(user)
    if locked(user, s):
        return False
    ok = False
    if s.totp_on and s.totp_secret:
        st = match_step(dec(s.totp_secret), code)
        if st is not None and st > (s.last_step or 0):
            s.last_step, ok = st, True
    if not ok:
        h = sha(re.sub(r'[\s-]', '', str(code or '')).lower())
        if h in (s.recovery or []):
            s.recovery = [x for x in s.recovery if x != h]
            ok = True
    if ok:
        s.failed = 0
        s.save(update_fields=['last_step', 'recovery', 'failed', 'updated_at'])
    else:
        fail(user, s, 'failed_code')
    return ok


def new_recovery_codes(s):
    codes = [f'{secrets.token_hex(2)}-{secrets.token_hex(2)}' for _ in range(10)]
    s.recovery = [sha(c.replace('-', '')) for c in codes]
    return codes


# ------------------------------------------------------------------ password policy and lock
def policies_for(user):
    from .models import SecurityPolicy
    schemas = [t.schema_name for t in user.tenants.all()] if getattr(user, 'pk', None) else []
    return list(SecurityPolicy.objects.filter(schema__in=schemas))


def strictest(user):
    ps = policies_for(user)
    out = {'min_length': 8, 'need_upper': False, 'need_lower': False, 'need_digit': False, 'need_symbol': False, 'expiry_days': 0,
           'history': 0, 'require_2fa': 'off', 'max_failed': 5, 'lock_minutes': 15}
    order = ['off', 'admins', 'staff', 'all']
    for p in ps:
        out['min_length'] = max(out['min_length'], p.min_length)
        for k in ('need_upper', 'need_lower', 'need_digit', 'need_symbol'):
            out[k] = out[k] or getattr(p, k)
        out['history'] = max(out['history'], p.history)
        out['max_failed'] = min(out['max_failed'], p.max_failed or 5)
        out['lock_minutes'] = max(out['lock_minutes'], p.lock_minutes or 15)
        if order.index(p.require_2fa) > order.index(out['require_2fa']):
            out['require_2fa'] = p.require_2fa
    exp = [p.expiry_days for p in ps if p.expiry_days]
    out['expiry_days'] = min(exp) if exp else 0
    # v1.62.0 sessions: the shortest idle time / admin session of the user's companies; device binding when any company asks
    for k in ('idle_minutes', 'admin_session_hours', 'admin_idle_minutes'):
        vals = [getattr(p, k, 0) for p in ps if getattr(p, k, 0)]
        out[k] = min(vals) if vals else 0
    out['bind_device'] = any(getattr(p, 'bind_device', False) for p in ps)
    return out


def session_rules(user_id):
    """v1.62.0 the session rules of a user, cached for a minute (checked on every request)."""
    k = f'zeo:srules:{user_id}'
    v = cache.get(k)
    if v is None:
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.filter(pk=user_id).first()
        r = strictest(u) if u is not None else {}
        v = {k2: r.get(k2, 0) for k2 in ('idle_minutes', 'admin_session_hours', 'admin_idle_minutes', 'bind_device')}
        cache.set(k, v, 60)
    return v


def _company_admin_limits(schema):
    """v1.67.8 (fixes) item 11: the administrator limits of one company (Settings › Sign-in security), cached a minute."""
    if not schema:
        return {'admin_session_hours': 0, 'admin_idle_minutes': 0}
    k = f'zeo:sadm:{schema}'
    v = cache.get(k)
    if v is None:
        from .models import SecurityPolicy
        p = SecurityPolicy.objects.filter(schema=schema).first()
        v = {'admin_session_hours': getattr(p, 'admin_session_hours', 0) or 0, 'admin_idle_minutes': getattr(p, 'admin_idle_minutes', 0) or 0}
        cache.set(k, v, 60)
    return v


def rules_here(user_id, schema):
    """v1.67.8 (fixes) item 11: (rules, admin) of a request to `schema`. The idle time for everybody stays the shortest of the
    user's companies (as since 1.62.0); the administrator limits (idle, session length) apply only when the user is an
    administrator OF THIS company by the per-company rule (ZeoCloud/companyadmin.py), with this company's values. Before,
    the sign-in marked the session "admin" when the user administered ANY company, and the strictest admin limits of all
    the user's companies were applied everywhere."""
    r = dict(session_rules(user_id))
    admin = False
    if schema:
        try:
            from .companyadmin import is_admin
            admin = bool(is_admin(user_id, schema))
        except Exception:
            admin = False
    lim = _company_admin_limits(schema) if admin else {'admin_session_hours': 0, 'admin_idle_minutes': 0}
    r.update(lim)
    return r, admin


def _session_schema(row, request=None):
    """The company a session works in now: the request's, else the last company it opened, else its sign-in company."""
    s = _req_schema(request) if request is not None else ''
    if not s:
        cos = list(getattr(row, 'companies', None) or [])
        s = cos[-1] if cos else (getattr(row, 'schema', '') or '')
    return s


def forget_rules(user_ids=None):
    for uid in (user_ids or []):
        cache.delete(f'zeo:srules:{uid}')


def password_problems(user, pw, rules=None):
    r = rules or strictest(user)
    pw = str(pw or '')
    out = []
    if len(pw) < r['min_length']:
        out.append(f'at least {r["min_length"]} characters')
    if r['need_upper'] and not re.search(r'[A-Z]', pw):
        out.append('a capital letter')
    if r['need_lower'] and not re.search(r'[a-z]', pw):
        out.append('a small letter')
    if r['need_digit'] and not re.search(r'\d', pw):
        out.append('a digit')
    if r['need_symbol'] and not re.search(r'[^A-Za-z0-9]', pw):
        out.append('a symbol such as ! # @')
    if r['history'] and getattr(user, 'pk', None):
        from django.contrib.auth.hashers import check_password
        s = sec(user)
        if any(check_password(pw, h) for h in (s.old_passwords or [])[-r['history']:]) or (user.password and user.check_password(pw)):
            out.append(f'not one of your last {r["history"]} passwords')
    return out


def check_password_rules(user, pw):
    """Raise a DRF ValidationError when the new password breaks the company rules."""
    p = password_problems(user, pw)
    if p:
        raise exceptions.ValidationError({'password': [f'The password needs {", ".join(p)}.']})


def password_changed(user, old_hash=None):
    s = sec(user)
    s.password_changed_at = timezone.now()
    if old_hash:
        s.old_passwords = ((s.old_passwords or []) + [old_hash])[-12:]
    s.save(update_fields=['password_changed_at', 'old_passwords', 'updated_at'])


def current_sid():
    """The sign-in session of the request being served ('' outside a request)."""
    try:
        from Chatter.tracking import current_request
        r = current_request()
    except Exception:
        r = None
    if r is None:
        return ''
    h = r.META.get('HTTP_AUTHORIZATION', '')
    if not h.startswith('Bearer '):
        return ''
    try:
        from rest_framework_simplejwt.tokens import UntypedToken
        return UntypedToken(h[7:].strip()).get('sid') or ''
    except Exception:
        return ''


def end_other_sessions(user, reason='the password was changed'):
    """v1.62.0 a new password ends every other sign-in of the user (the one that changed it stays)."""
    from .models import LoginSession
    cur = current_sid()
    qs = LoginSession.objects.filter(user=user)
    if cur:
        qs = qs.exclude(sid=cur)
    return revoke(qs, reason)


def password_expired(user):
    r = strictest(user)
    if not r['expiry_days']:
        return False
    s = sec(user)
    since = s.password_changed_at or getattr(user, 'date_joined', None)
    return bool(since and since + timedelta(days=r['expiry_days']) < timezone.now())


def locked(user, s=None):
    s = s or sec(user)
    return bool(s.locked_until and s.locked_until > timezone.now())


def fail(user, s=None, kind='failed_password'):
    s = s or sec(user)
    r = strictest(user)
    try:   # v1.62.0 the security dashboard counts wrong passwords / codes (many in a short time → the administrators are told)
        from Chatter.tracking import current_request
        event(kind, user=user, ip=client_ip(current_request()), notify=False)
    except Exception:
        pass
    s.failed = (s.failed or 0) + 1
    just_locked = False
    strict = False
    try:   # v1.62.0 a login still on the company default password: 3 wrong tries lock it until HR unlocks it
        from ZeoAdmin.giveaccess import default_state
        strict = default_state(user) is not None
    except Exception:
        strict = False
    if strict and s.failed >= 3:
        s.locked_until, s.failed = timezone.now() + timedelta(days=3650), 0
        just_locked = True
    elif s.failed >= (r['max_failed'] or 5):
        s.locked_until, s.failed = timezone.now() + timedelta(minutes=r['lock_minutes'] or 15), 0
        just_locked = True
    s.save(update_fields=['failed', 'locked_until', 'updated_at'])
    if just_locked:   # v1.61.0 tell the user
        event('locked', user=user, notify=False)
        try:
            from .netguard import after_lock
            after_lock(user)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('lock alert')


def login_ok(user):
    from .models import LoginSecurity
    LoginSecurity.objects.filter(user=user).exclude(failed=0).update(failed=0)


# ------------------------------------------------------------------ sessions
def client_ip(request):
    """v1.61.0: the address the proxy in front of ZEO saw (last X-Forwarded-For entry) – the first one can be typed by anybody."""
    from .netguard import client_ip as _ip
    return _ip(request)


def device_hash(request):
    """v1.62.0 the device of a request: its browser / app (user agent) and the app's own device id when it sends one."""
    if request is None:
        return ''
    ua = request.META.get('HTTP_USER_AGENT', '')
    dev = request.META.get('HTTP_X_ZEO_DEVICE', '')
    if not ua and not dev:
        return ''
    return sha(f'{ua}|{dev}')


def start_session(user, request=None, how='password', impersonator=None, read_only=False, schema=''):
    from .models import LoginSession
    try:   # v1.67.8 (fixes) item 11: administrator of the company signed in to (was: of any company); the limits are per request
        admin = bool(impersonator is None and schema and is_company_admin(user, schema))
    except Exception:
        admin = False
    try:
        from .geo import country_of
        country = country_of(client_ip(request)) or ''
    except Exception:
        country = ''
    s = LoginSession.objects.create(user=user, sid=uuid.uuid4().hex, ip=client_ip(request),
                                    agent=(request.META.get('HTTP_USER_AGENT', '') if request is not None else '')[:255], how=how,
                                    impersonator=impersonator, read_only=read_only, schema=schema, last_seen=timezone.now(),
                                    device_hash=device_hash(request), admin=admin, country=country,
                                    location=country, companies=[schema] if schema else [])   # v1.67.6 (setup) item 13 / 14
    try:
        from django.contrib.auth.models import update_last_login
        update_last_login(None, user)
    except Exception:
        pass
    try:   # v1.61.0 a sign-in from a new network and a new device → alert
        from .netguard import after_signin
        after_signin(user, s)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('sign-in alert')
    return s


def tokens(user, request=None, how='password', otp=False, impersonator=None, read_only=False, schema='', lifetime=None):
    """New refresh / access tokens bound to a new session."""
    from rest_framework_simplejwt.tokens import RefreshToken
    s = start_session(user, request, how, impersonator, read_only, schema)
    r = RefreshToken.for_user(user)
    r['sid'] = s.sid
    if otp:
        r['otp'] = True
    if impersonator is not None:
        r['imp'] = impersonator.pk
        r['ro'] = bool(read_only)
        r['isch'] = schema
    if not lifetime and s.admin:   # v1.62.0 administrators sign in again after the company's admin session hours
        hrs = _company_admin_limits(schema).get('admin_session_hours') or 0   # v1.67.8 (fixes) item 11: that company's value
        if hrs:
            lifetime = timedelta(hours=hrs)
    if lifetime:
        r.set_exp(lifetime=lifetime)
    a = r.access_token
    if lifetime:
        a.set_exp(lifetime=min(lifetime, a.lifetime))
    from .models import LoginSession
    LoginSession.objects.filter(pk=s.pk).update(rjti=r['jti'])
    return {'access': str(a), 'refresh': str(r), 'sid': s.sid}


def bind(token, user, request=None, how='password'):
    """Give a token made elsewhere (RefreshToken) a session: sets the 'sid' claim."""
    s = start_session(user, request, how)
    token['sid'] = s.sid
    from .models import LoginSession
    LoginSession.objects.filter(pk=s.pk).update(rjti=token.get('jti', ''))
    return s


# ------------------------------------------------------------------ v1.62.0 refresh with rotation
class RefreshRefused(Exception):
    def __init__(self, msg, code='session_ended', *args):
        try:
            from Translations.i18n import tr
            msg = tr(msg, *args)
        except Exception:
            msg = msg.format(*args) if args else msg
        super().__init__(msg)
        self.msg, self.code = msg, code


def event(kind, user=None, by=None, schema='', ip='', detail='', data=None, notify=True):
    """A security event (dashboard; e-mailed to the company administrators for the serious kinds). Never raises."""
    try:
        from .admin_alerts import record
        return record(kind, user=user, by=by, schema=schema, ip=ip, detail=detail, data=data, notify=notify)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('security event')
        return None


def _idle_limit(rules, admin):
    lim = rules.get('idle_minutes') or 0
    if admin and rules.get('admin_idle_minutes'):
        lim = min(lim, rules['admin_idle_minutes']) if lim else rules['admin_idle_minutes']
    return lim


def check_live(row, request=None, rules=None, admin=None):
    """Raise RefreshRefused when the session must end now: idle too long, the admin session is over, another device."""
    if rules is None or admin is None:   # v1.67.8 (fixes) item 11: administrator of the company the session works in
        rules, admin = rules_here(row.user_id, _session_schema(row, request))
    now = timezone.now()
    lim = _idle_limit(rules, admin)
    if lim and row.last_seen and row.last_seen < now - timedelta(minutes=lim):
        revoke(type(row).objects.filter(pk=row.pk), f'signed out after {lim} minutes without use')
        raise RefreshRefused('You were signed out after {0} minutes without use. Sign in again.', 'session_ended', lim)
    if admin and rules.get('admin_session_hours') and row.created_at < now - timedelta(hours=rules['admin_session_hours']):
        revoke(type(row).objects.filter(pk=row.pk), 'the administrator session ended')
        raise RefreshRefused('Administrators sign in again every {0} hours. Sign in again.', 'session_ended', rules['admin_session_hours'])
    if rules.get('bind_device') and row.device_hash and request is not None and device_hash(request) and device_hash(request) != row.device_hash:
        revoke(type(row).objects.filter(pk=row.pk), 'used on another device')
        event('device_changed', user=row.user, ip=client_ip(request), schema=row.schema,
              detail=f'sign-in of {timezone.localtime(row.created_at):%d/%m/%Y %H:%M} used on another device')   # v1.67.8 (fixes) item 10: dd/mm/yyyy
        raise RefreshRefused('This sign-in belongs to another device. Sign in again.')


def refresh(raw, request=None):
    """New access AND refresh token for a refresh token; the old refresh token is spent. A spent token that comes back
    (after the 60-second grace for two tabs refreshing at once) ends the whole session – it may have been stolen."""
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import RefreshToken
    from .models import LoginSession
    try:
        r = RefreshToken(str(raw or ''))
    except TokenError as e:
        raise RefreshRefused(str(e), 'token_not_valid')
    sid, jti = r.get('sid'), r.get('jti')
    if not sid:   # a token made before sessions existed: a new access token only
        return {'access': str(r.access_token)}
    if revoked(sid):
        raise RefreshRefused('You were signed out. Sign in again.')
    grace = cache.get(f'zeo:rot:{jti}')
    if grace:
        return grace
    row = LoginSession.objects.select_related('user').filter(sid=sid).first()
    if row is None or row.revoked_at:
        raise RefreshRefused('You were signed out. Sign in again.')
    iat = r.get('iat') or 0
    if row.rjti and jti != row.rjti and row.rotated_at and iat <= int(row.rotated_at.timestamp()):
        revoke(LoginSession.objects.filter(pk=row.pk), 'a used sign-in token came back')
        event('refresh_reuse', user=row.user, ip=client_ip(request), schema=row.schema,
              detail=f'a spent sign-in token of {timezone.localtime(row.created_at):%d/%m/%Y %H:%M} was used again from {client_ip(request) or "?"} – the sign-in was ended')   # v1.67.8 (fixes)
        raise RefreshRefused('This sign-in was ended for safety (an old sign-in token was used again). Sign in again.')
    check_live(row, request)
    if not row.user.is_active:
        raise RefreshRefused('Your account is deactivated.')
    n = RefreshToken.for_user(row.user)
    for k in ('sid', 'otp', 'imp', 'ro', 'isch', 'dev'):
        if r.get(k) is not None:
            n[k] = r.get(k)
    n['exp'] = r['exp']   # the session's end does not move: rotation is not a longer sign-in
    now = timezone.now()
    LoginSession.objects.filter(pk=row.pk).update(rjti=n['jti'], rotated_at=now, refreshed=row.refreshed + 1)
    a = n.access_token
    if a['exp'] > n['exp']:
        a['exp'] = n['exp']
    out = {'access': str(a), 'refresh': str(n)}
    cache.set(f'zeo:rot:{jti}', out, 60)
    return out


def revoked(sid):
    if not sid:
        return False
    k = f'zeo:sid:{sid}'
    v = cache.get(k)
    if v is None:
        from .models import LoginSession
        row = LoginSession.objects.filter(sid=sid).values_list('revoked_at', flat=True).first()
        v = 'x' if row is not None else 'ok'
        cache.set(k, v, CACHE_S)
    return v == 'x'


def seen(sid, idle_seconds=0):
    """The session was used. v1.62.0: the app sends X-Zeo-Idle (seconds since the person last touched it), so background
    requests (the bell, refreshing the token) do not keep an unattended session open."""
    k = f'zeo:sid-seen:{sid}'
    at = timezone.now() - timedelta(seconds=max(0, min(int(idle_seconds or 0), 7 * 86400)))
    last = cache.get(k)
    if last and (at - last).total_seconds() < 60:
        return
    cache.set(k, at, 600)
    from .models import LoginSession
    LoginSession.objects.filter(sid=sid).filter(models_q_older(at)).update(last_seen=at)
    cache.delete(f'zeo:sid-live:{sid}')


def models_q_older(at):
    from django.db.models import Q
    return Q(last_seen__isnull=True) | Q(last_seen__lt=at)


def idle_of(request):
    try:
        return int(float(request.META.get('HTTP_X_ZEO_IDLE', '0') or 0))
    except (TypeError, ValueError):
        return 0


def _should_end(st, rules, request, admin=False):
    """Quick test on the cached session state – the full check (which ends the session) runs only when this says so.
    v1.67.8 (fixes) item 11: `admin` = administrator of the request's company (was the sign-in's "any company" marker)."""
    uid, _stored_admin, created, last, dev = st
    now = timezone.now()
    lim = _idle_limit(rules, admin)
    if lim and last and last < now - timedelta(minutes=lim):
        return True
    if admin and rules.get('admin_session_hours') and created and created < now - timedelta(hours=rules['admin_session_hours']):
        return True
    if rules.get('bind_device') and dev:
        d = device_hash(getattr(request, '_request', request))
        if d and d != dev:
            return True
    return False


def _live_state(sid):
    """(user_id, admin, created_at, last_seen, device_hash) of a session, cached 30 s."""
    k = f'zeo:sid-live:{sid}'
    v = cache.get(k)
    if v is None:
        from .models import LoginSession
        v = LoginSession.objects.filter(sid=sid).values_list('user_id', 'admin', 'created_at', 'last_seen', 'device_hash').first() or ()
        cache.set(k, v, CACHE_S)
    return v


def revoke(qs, reason='signed out'):
    from .models import LoginSession
    sids = list(qs.filter(revoked_at__isnull=True).values_list('sid', flat=True))
    LoginSession.objects.filter(sid__in=sids).update(revoked_at=timezone.now(), revoked_reason=reason[:120])
    for s in sids:
        cache.set(f'zeo:sid:{s}', 'x', CACHE_S)
    return len(sids)


SAFE = ('GET', 'HEAD', 'OPTIONS')
# v1.67.6 (setup) item 14: the own security data of a login – never shown while an administrator views as that user
IMP_PRIVATE = ('/users/2fa/', '/users/sessions/', '/users/security/alerts/', '/users/sign-ins/', '/users/change-password/',
               '/zeo-admin/api/history/mine/')


def _req_schema(request):
    """The company a request works on (the open schema, else ?schema= / X-Schema for the public /users/ endpoints)."""
    from django.db import connection
    s = getattr(connection, 'schema_name', '') or ''
    if s and s != 'public':
        return s
    try:
        s = request.GET.get('schema') or request.META.get('HTTP_X_SCHEMA') or ''
    except Exception:
        s = ''
    return '' if s == 'public' else s


def _member_or_refuse(user, request):
    """v1.67.6 (setup) item 14: the safety net for every API call with a sign-in token. Views that check rights themselves
    (`zeo_access = False`: print templates, apps, setup screens …) used the shared rights table, where a company administrator is
    an administrator everywhere – the administrator of one company could read another company's print designs and print its
    records. A request to a company's schema now needs a login of that company (system administrators and ZEO Cloud staff
    excepted), whatever the view does."""
    from django.db import connection
    sch = getattr(connection, 'schema_name', '') or ''
    if not sch or sch == 'public' or getattr(user, 'is_superuser', False):
        return
    k = f'zeo:member:{user.pk}:{sch}'
    v = cache.get(k)
    if v is None:
        v = user.tenants.filter(schema_name=sch).exists()
        if not v:
            try:
                from .services import is_platform_admin
                v = is_platform_admin(user)
            except Exception:
                v = False
        if v:   # only a "yes" is remembered (a minute): a login just given access is never refused
            cache.set(k, v, 60)
    if not v:
        from Translations.i18n import tr
        raise exceptions.PermissionDenied(tr('You do not have access to this company.'), code='not_member')


def opened(sid, schema):
    """v1.67.6 (setup) item 14: remember that the sign-in `sid` opened `schema` (only a company the user belongs to – the
    access check refuses the others before any data is read; written once per sign-in and company)."""
    if not sid or not schema:
        return
    k = f'zeo:sid-co:{sid}:{schema}'
    if cache.get(k):
        return
    cache.set(k, 1, 6 * 3600)
    try:
        from .models import LoginSession
        row = LoginSession.objects.filter(sid=sid).only('id', 'companies', 'schema', 'user_id').first()
        if row is None or schema in (row.companies or []):
            return
        if not row.user.tenants.filter(schema_name=schema).exists() and not row.user.is_superuser:
            return
        LoginSession.objects.filter(pk=row.pk).update(companies=(row.companies or []) + [schema])
        _tell_admins(row, sid, schema)
    except Exception:
        pass


def _tell_admins(row, sid, schema):
    """v1.67.6 (setup) item 13: a new sign-in (new place, address or device) that opens a company whose sign-in rules ask for it
    ("Also e-mail the administrators") is e-mailed to that company's administrators – only the company it opened."""
    from .models import SecurityPolicy, SignInAlert
    if not SecurityPolicy.objects.filter(schema=schema, alert_admin_signin=True).exists():
        return
    a = SignInAlert.objects.filter(sid=sid, kind='new_signin').first()
    if a is None:
        return
    from .admin_alerts import admin_people
    from .mailtext import T, device_text, lang_of
    from .netguard import send_mail_for, why_text
    u = row.user
    # v1.67.7 (admin): this company's administrators (per company), each in their own language
    people = [p for p in admin_people(schema) if p.email and p.email.lower() != (u.email or '').lower()]
    if not people:
        return
    from UserManagement.models import company
    cname = getattr(company.objects.filter(schema_name=schema).first(), 'name', schema)
    when = timezone.localtime(a.created_at).strftime('%d/%m/%Y %H:%M')
    uname_ = u.get_full_name() or u.username
    for p in people:
        L = lang_of(p, schema)
        body = (T('Company: {0}', L, cname) + '\n' + T('User: {0} ({1})', L, uname_, u.email or u.username) + '\n'
                + T('When: {0}', L, when) + '\n' + T('Device: {0}', L, device_text(a.device, L)) + '\n'
                + T('Address: {0}', L, a.ip + (f' ({row.country})' if row.country else ''))
                + ('\n' + T('New: {0}.', L, why_text(a.why, lang=L)) if a.why else '') + '\n\n'
                + T('Open Settings › Sign-in security › Sign-in history in ZEO to see the sign-ins of the company.', L) + '\n'
                + T('You get this e-mail because the sign-in rules of the company ask to tell the administrators about new sign-ins.', L))
        try:
            send_mail_for(u, T('ZEO security: a new sign-in of {0}', L, uname_), body, [p.email], schema, lang=L)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('admin sign-in mail')


RO_ALLOWED = ('/users/impersonate/stop/', '/users/token/refresh/', '/chatter/api/flags/')


class SessionJWTAuthentication(JWTAuthentication):
    """The JWT login, plus: a signed-out session's tokens are refused; an impersonation that is read only cannot change data."""

    def authenticate(self, request):
        res = super().authenticate(request)
        if res is None:
            return None
        user, token = res
        sid = token.get('sid') if hasattr(token, 'get') else None
        if sid:
            if revoked(sid):
                raise exceptions.AuthenticationFailed('You were signed out. Sign in again.', code='session_ended')
            st = _live_state(sid)
            if st:   # v1.62.0 idle sign-out, the admin session end, device binding
                rules, adm = rules_here(st[0], _req_schema(request))   # v1.67.8 (fixes) item 11: per company
                if _should_end(st, rules, request, adm):
                    from .models import LoginSession
                    row = LoginSession.objects.filter(sid=sid).first()
                    if row is not None:
                        try:
                            check_live(row, request._request if hasattr(request, '_request') else request, rules, adm)
                        except RefreshRefused as e:
                            cache.delete(f'zeo:sid-live:{sid}')
                            raise exceptions.AuthenticationFailed(e.msg, code='session_ended')
            seen(sid, idle_of(request))
            opened(sid, _req_schema(request))   # v1.67.6 (setup) item 14: which companies this sign-in opened
        try:   # v1.67.7 (admin): a system administrator switched off as administrator of this company is not one here
            from .companyadmin import mask_request_user
            mask_request_user(user, _req_schema(request))
        except Exception:
            pass
        _member_or_refuse(user, request)   # v1.67.6 (setup) item 14: a company's API only for the users of that company
        if token.get('imp'):   # v1.67.6 (setup) item 14: "view as" stays inside its company and out of the user's own security data
            from Translations.i18n import tr
            sch = _req_schema(request)
            isch = token.get('isch') or ''
            if isch and sch and sch != isch:
                raise exceptions.PermissionDenied(tr('You are viewing as this user in another company. Stop viewing as the user first.'), code='impersonation_scope')
            if request.path.startswith(IMP_PRIVATE) and not (request.path.startswith('/users/security/alerts/')
                                                              and request.method in SAFE and request.GET.get('company') in ('1', 'true')):
                raise exceptions.PermissionDenied(tr("Not while viewing as another user: the user's sign-ins, devices and authenticator stay private."),
                                                  code='impersonation_private')
        if token.get('imp') and token.get('ro') and request.method not in SAFE and not any(request.path.startswith(p) for p in RO_ALLOWED):
            raise exceptions.PermissionDenied('You are viewing as this user (read only) – nothing can be changed.')
        if getattr(user, 'must_change_password', False) and not token.get('imp') and not any(request.path.startswith(p) for p in MUST_CHANGE_OK):
            # v1.62.0 a password someone else knows (generated, typed by HR, start password, temporary) – nothing else until it is changed
            from Translations.i18n import tr
            raise exceptions.PermissionDenied(tr('Choose your own password first.'), code='password_change_required')
        return res


MUST_CHANGE_OK = ('/users/change-password/', '/users/token/refresh/', '/users/media-cookie/', '/users/sessions/', '/users/logout',
                  '/language/', '/users/step-up/')


def session_json(s, current_sid=''):
    ua = s.agent or ''
    dev = 'ZEO app' if 'zeo-mobile' in ua.lower() or 'capacitor' in ua.lower() else (
        'Phone browser' if re.search(r'Mobile|Android|iPhone', ua) else ('Browser' if ua else 'Unknown'))
    br = next((b for b in ('Edg', 'Chrome', 'Firefox', 'Safari') if b in ua), '')
    return {'id': s.pk, 'sid_end': s.sid[-6:], 'ip': s.ip, 'device': f'{dev}{" · " + {"Edg": "Edge"}.get(br, br) if br else ""}', 'how': s.how,
            'created_at': s.created_at, 'last_seen': s.last_seen, 'revoked_at': s.revoked_at, 'current': s.sid == current_sid,
            'impersonator': (s.impersonator.get_full_name() or s.impersonator.username) if s.impersonator_id else ''}


def is_company_admin(user, schema=None):
    """Administrator of `schema`, or (no company given – the sign-in) of any of the user's companies."""
    if getattr(user, 'is_superuser', False) and not schema:
        return True
    try:   # v1.67.7 (admin): per company (ZeoCloud/companyadmin.py) – was the shared flag
        from .companyadmin import is_admin
        if schema:
            return is_admin(user, schema)
        return any(is_admin(user, t.schema_name) for t in user.tenants.all())
    except Exception:
        pass
    return False


def required_for(user, rules=None):
    """The company rules ask this user for the authenticator app."""
    need = (rules or strictest(user))['require_2fa']
    if need == 'off':
        return False
    if need == 'all':
        return True
    if need == 'staff':
        return not getattr(user, 'is_ess', False)
    try:   # v1.67.7 (admin): "administrators" = administrators of a company whose rule asks it (not of any company)
        schemas = [p.schema for p in policies_for(user) if p.require_2fa == 'admins']
        if schemas:
            return any(is_company_admin(user, s) for s in schemas)
    except Exception:
        pass
    return is_company_admin(user)


def needs_2fa(user, rules=None):
    """Required and not set up yet."""
    return required_for(user, rules) and not totp_on(user)
