"""v1.52.0 – sign-in security endpoints (under /users/):

  2fa/login/                 POST {challenge, code}           – the second step of a sign-in with the authenticator app
  2fa/                       GET my status · POST {action: setup | confirm {code} | disable {code} | recovery {code}}
  sessions/                  GET my sign-ins · POST {action: end {id} | sign_out (this one) | end_others | end_all}
  token/refresh/             POST {refresh} – like simplejwt, refused for a signed-out session
  security/policy/           GET / PUT the sign-in rules of the open company (administrators)
  security/users/            GET the company's users (authenticator, lock, last sign-in, sessions) · POST {user_id, action: unlock |
                             end_sessions | reset_2fa}
  impersonate/               POST {user_id, read_only} – open ZEO as a user of the company (administrators; audit log)
  impersonate/stop/          POST – end the impersonation session
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import connection
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import security as S
from Translations.i18n import tr

U = get_user_model()


def bad(msg, code=400):
    return Response({'detail': msg}, status=code)


def _sid(request):
    a = getattr(request, 'auth', None)
    try:
        return a.get('sid') if a is not None else ''
    except Exception:
        return ''


def login_payload(user, request, how, otp=False):
    t = S.tokens(user, request, how, otp=otp)
    rules = S.strictest(user)
    return {'access': t['access'], 'refresh': t['refresh'], 'user_id': user.pk, 'username': user.username, 'email': user.email,
            'is_ess': bool(user.is_ess), 'is_superuser': bool(user.is_superuser), 'must_change_password': getattr(user, 'must_change_password', False),
            'tenants': [{'id': x.id, 'name': x.name, 'schema_name': x.schema_name} for x in user.tenants.all()],
            'tenant_id': [x.schema_name for x in user.tenants.all()], 'password_expired': S.password_expired(user),
            'setup_2fa': S.needs_2fa(user, rules), 'two_factor_done': True}


class TwoFactorLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        try:
            d = signing.loads(str(request.data.get('challenge') or ''), salt='zeo-2fa-login', max_age=600)
        except signing.BadSignature:
            return bad('The sign-in took too long – sign in again.', 401)
        user = U.objects.filter(pk=d.get('u'), is_active=True).first()
        if user is None:
            return bad('Sign in again.', 401)
        s = S.sec(user)
        if S.locked(user, s):
            mins = int((s.locked_until - timezone.now()).total_seconds() // 60) + 1
            return bad(f'Too many wrong tries – try again in {mins} minute(s).', 403)
        if not S.check_code(user, request.data.get('code')):
            return bad('The code is not correct. Use the 6-digit code of your authenticator app, or a recovery code.')
        from . import netguard as NG   # v1.61.0
        if NG.refused_everywhere(user, NG.client_ip(request)):
            NG.refused_signin(user, request)
            return bad(tr('Your company allows ZEO only from its own networks. Your address {0} is not one of them.', NG.client_ip(request)), 403)
        from . import geo   # v1.62.0
        cc = geo.refused_everywhere(user, NG.client_ip(request))
        if cc:
            S.event('country_refused', user=user, ip=NG.client_ip(request), detail=f'Sign-in refused: address {NG.client_ip(request)} is in {cc}.')
            return bad(tr('Your company allows ZEO only from its countries. Your address {0} is in {1}.', NG.client_ip(request), cc), 403)
        return Response(login_payload(user, request, 'authenticator', otp=True))


class TwoFactorView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        s = S.sec(request.user)
        return Response({'on': s.totp_on, 'since': s.totp_since, 'recovery_left': len(s.recovery or []),
                         'required': S.strictest(request.user)['require_2fa'], 'must_setup': S.needs_2fa(request.user)})

    def post(self, request):
        u, s = request.user, S.sec(request.user)
        act = request.data.get('action')
        if getattr(request, 'auth', None) is not None and request.auth.get('imp'):
            return bad('Not while viewing as another user.', 403)
        if act == 'setup':
            if s.totp_on:
                return bad('The authenticator app is already on. Switch it off first to set up another phone.')
            secret = S.new_secret()
            s.totp_secret = S.enc(secret)
            s.save(update_fields=['totp_secret', 'updated_at'])
            acct = u.email or u.username
            return Response({'secret': ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4)), 'uri': S.otpauth_uri(secret, acct), 'account': acct})
        if act == 'confirm':
            secret = S.dec(s.totp_secret)
            if not secret:
                return bad('Start the set-up first.')
            st = S.match_step(secret, request.data.get('code'))
            if st is None:
                return bad('The code does not match. Check the time of the phone and type the code shown now.')
            codes = S.new_recovery_codes(s)
            s.totp_on, s.totp_since, s.last_step = True, timezone.now(), st
            s.save()
            return Response({'on': True, 'recovery_codes': codes})
        if act in ('disable', 'recovery'):
            if not s.totp_on:
                return bad('The authenticator app is not on.')
            if not S.check_code(u, request.data.get('code')):
                return bad('The code is not correct.')
            if act == 'recovery':
                codes = S.new_recovery_codes(s)
                s.save(update_fields=['recovery', 'updated_at'])
                return Response({'on': True, 'recovery_codes': codes})
            if S.required_for(u):
                return bad('Your company requires the authenticator app – it cannot be switched off.')
            s.totp_on, s.totp_secret, s.recovery = False, '', []
            s.save()
            return Response({'on': False})
        return bad('Unknown action.')


class SessionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import LoginSession
        cur = _sid(request)
        from django.conf import settings as dj
        life = dj.SIMPLE_JWT.get('REFRESH_TOKEN_LIFETIME') or timedelta(days=14)
        rows = LoginSession.objects.filter(user=request.user, revoked_at__isnull=True, created_at__gte=timezone.now() - life)[:50]
        recent = LoginSession.objects.filter(user=request.user, revoked_at__isnull=False).order_by('-revoked_at')[:10]
        return Response({'current_sid_end': cur[-6:] if cur else '', 'active': [S.session_json(x, cur) for x in rows],
                         'ended': [S.session_json(x, cur) for x in recent]})

    def post(self, request):
        from .models import LoginSession
        cur = _sid(request)
        qs = LoginSession.objects.filter(user=request.user)
        act = request.data.get('action')
        if act == 'end':
            n = S.revoke(qs.filter(pk=request.data.get('id')), 'signed out by the user')
        elif act == 'sign_out':
            n = S.revoke(qs.filter(sid=cur), 'signed out') if cur else 0
        elif act == 'end_others':
            n = S.revoke(qs.exclude(sid=cur), 'signed out from another sign-in')
        elif act == 'end_all':
            n = S.revoke(qs, 'signed out everywhere')
        else:
            return bad('Unknown action.')
        return Response({'ended': n})


class RefreshView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        # v1.62.0 rotation: a new refresh token comes back with the access token; the one sent is spent
        try:
            return Response(S.refresh(request.data.get('refresh'), request))
        except S.RefreshRefused as e:
            return Response({'detail': e.msg, 'code': e.code}, status=401)


# ------------------------------------------------------------------ company administrators
def _schema(request):
    """/users/ runs in the public schema – the company comes from ?schema= (or the open schema)."""
    schema = getattr(connection, 'schema_name', '')
    if schema in ('', 'public'):
        schema = request.query_params.get('schema') or request.META.get('HTTP_X_SCHEMA') or ''
    if not schema or schema == 'public':
        return ''
    u = request.user
    if not (u.is_superuser or u.tenants.filter(schema_name=schema).exists()):
        return ''
    return schema


def _company_admin(request):
    schema = _schema(request)
    if not schema:
        return None
    try:
        from django_tenants.utils import schema_context
        from AccessControl.access import ctx
        with schema_context(schema):
            c = ctx(request)
            if c and c.admin:
                return schema
    except Exception:
        pass
    return None


def _policy(schema):
    from .models import SecurityPolicy
    p, _ = SecurityPolicy.objects.get_or_create(schema=schema)
    return p


POLICY_FIELDS = ('min_length', 'need_upper', 'need_lower', 'need_digit', 'need_symbol', 'expiry_days', 'history', 'require_2fa', 'max_failed',
                 'lock_minutes', 'allow_impersonation', 'ip_scope', 'ip_allowlist', 'alert_new_signin', 'alert_lock',
                 # v1.62.0
                 'idle_minutes', 'admin_session_hours', 'admin_idle_minutes', 'bind_device', 'country_scope', 'country_allow', 'alert_admins',
                 'step_up', 'step_up_minutes', 'step_up_actions', 'mask_sensitive', 'export_control', 'export_row_limit', 'export_watermark',
                 'bank_two_person', 'upload_scan', 'signin_id', 'signin_id_categories', 'link_hours', 'hr_types_password', 'default_password_on',
                 'default_password_days', 'default_password_set_at')
BOOL62 = ('bind_device', 'alert_admins', 'step_up', 'mask_sensitive', 'export_control', 'export_watermark', 'bank_two_person', 'upload_scan',
          'hr_types_password')
INT62 = {'idle_minutes': (0, 1440), 'admin_session_hours': (0, 720), 'admin_idle_minutes': (0, 1440), 'step_up_minutes': (1, 120),
         'export_row_limit': (0, 1000000), 'link_hours': (1, 336), 'default_password_days': (1, 30)}
STEP_UP_ACTIONS = ('payroll_approve', 'wps', 'bank', 'rights', 'export')


def _policy_out(p, request):
    from . import netguard as NG
    out = {f: getattr(p, f) for f in POLICY_FIELDS}
    out['your_ip'] = NG.client_ip(request)
    try:   # v1.62.0 the country database and the visitor's country
        from . import geo
        out['country_db'] = geo.status()
        out['your_country'] = geo.country_of(out['your_ip'])
    except Exception:
        out['country_db'], out['your_country'] = {'ready': False}, ''
    out['default_password_set'] = bool(p.default_password_hash)
    try:
        from zeo.uploadguard import _clamd_path
        out['clamav'] = bool(_clamd_path())
    except Exception:
        out['clamav'] = False
    from django.conf import settings as dj
    out['access_minutes'] = int(dj.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds() // 60)
    out['https'] = bool(getattr(dj, 'ZEO_HTTPS', False))
    return out


WEAK = {'admin', 'administrator', 'password', 'passw0rd', '123456', '1234567', '12345678', '123456789', '1234567890', 'qwerty', 'welcome',
        'zeo', 'zeohrms', 'changeme', 'abc123', 'letmein', 'p@ssw0rd', 'password1', 'admin123', 'test', 'user'}


def weak_default(pw, company_name=''):
    low = str(pw or '').lower()
    core = ''.join(ch for ch in low if ch.isalnum())
    if low in WEAK or core in WEAK or (company_name and ''.join(c for c in company_name.lower() if c.isalnum()) in core):
        return 'This password is too easy to guess (a common password or the company name).'
    if len(set(low)) < 4:
        return 'Use more different characters.'
    return ''


def _policy_put62(p, d, schema, request):
    """The 1.62.0 settings of PUT security/policy/. Returns a problem text or ''."""
    for f in BOOL62:
        if f in d:
            setattr(p, f, str(d[f]).lower() in ('1', 'true'))
    for f, (lo, hi) in INT62.items():
        if f in d:
            try:
                v = int(d[f] or 0)
            except (TypeError, ValueError):
                return tr('{0}: give a whole number.', f)
            if not lo <= v <= hi:
                return tr('{0}: {1} to {2}.', f, lo, hi)
            setattr(p, f, v)
    if 'step_up_actions' in d:
        acts = d.get('step_up_actions') or []
        if isinstance(acts, str):
            acts = [a.strip() for a in acts.split(',') if a.strip()]
        p.step_up_actions = [a for a in acts if a in STEP_UP_ACTIONS]
    if 'country_scope' in d or 'country_allow' in d:
        from . import geo
        scope = d.get('country_scope', p.country_scope)
        if scope not in ('off', 'staff', 'all'):
            return 'Choose who the country rule applies to.'
        codes = geo.parse_codes(d.get('country_allow', p.country_allow))
        if scope != 'off' and not codes:
            return 'List at least one country code (e.g. AE), or turn the country rule off.'
        cc = geo.country_of(S.client_ip(request))
        from .netguard import applies
        if scope != 'off' and cc and cc not in codes and applies(scope, request.user):
            return tr('You are in {0} – it is not in the list, you would lock yourself out. Add it first.', cc)
        p.country_scope, p.country_allow = scope, ', '.join(codes)
        geo.forget(schema)
    if d.get('signin_id') in ('optional', 'email'):
        p.signin_id = d['signin_id']
    if 'signin_id_categories' in d:
        m = d.get('signin_id_categories') or {}
        if not isinstance(m, dict):
            return 'signin_id_categories: {category id: "email" | "optional"}.'
        p.signin_id_categories = {str(k): v for k, v in m.items() if v in ('email', 'optional') and str(k).isdigit()}
    if 'default_password_on' in d:
        on = str(d['default_password_on']).lower() in ('1', 'true')
        if on and not (p.default_password_hash or d.get('default_password')):
            return 'Type the company default password first.'
        p.default_password_on = on
    if d.get('default_password'):
        from django.contrib.auth.hashers import make_password
        from UserManagement.models import company
        pw = str(d['default_password'])
        cname = getattr(company.objects.filter(schema_name=schema).first(), 'name', '')
        prob = weak_default(pw, cname)
        if not prob:
            rules = {'min_length': max(10, p.min_length), 'need_upper': p.need_upper, 'need_lower': p.need_lower, 'need_digit': True,
                     'need_symbol': p.need_symbol, 'history': 0}
            pr = S.password_problems(None, pw, rules)
            if pr:
                prob = tr('The default password needs {0}.', ', '.join(pr))
        if prob:
            return prob
        p.default_password_hash = make_password(pw)
        p.default_password_set_at = timezone.now()
    S.forget_rules(list(_company_users(schema).values_list('pk', flat=True)[:5000]))
    try:
        from zeo import exportguard
        from AccessControl import respfilter
        exportguard.forget(schema)
        respfilter.forget(schema)
        from . import stepup
        stepup.forget()
        from ZeoAdmin import bankguard
        bankguard.forget(schema)
    except Exception:
        pass
    return ''


class PolicyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        schema = _company_admin(request)
        if not schema:
            return bad('Company administrators only.', 403)
        p = _policy(schema)
        return Response(_policy_out(p, request))

    def put(self, request):
        schema = _company_admin(request)
        if not schema:
            return bad('Company administrators only.', 403)
        p = _policy(schema)
        d = request.data
        try:
            for f in ('min_length', 'expiry_days', 'history', 'max_failed', 'lock_minutes'):
                if f in d:
                    setattr(p, f, int(d[f]))
        except (TypeError, ValueError):
            return bad('Give whole numbers.')
        if not 6 <= p.min_length <= 64:
            return bad('The minimum length is 6 to 64.')
        if p.expiry_days > 365 or p.history > 24 or not 3 <= p.max_failed <= 20 or not 1 <= p.lock_minutes <= 1440:
            return bad('Expiry at most 365 days, history at most 24, 3–20 wrong tries, lock 1–1440 minutes.')
        for f in ('need_upper', 'need_lower', 'need_digit', 'need_symbol', 'allow_impersonation'):
            if f in d:
                setattr(p, f, str(d[f]).lower() in ('1', 'true'))
        if d.get('require_2fa') in ('off', 'admins', 'staff', 'all'):
            p.require_2fa = d['require_2fa']
        for f in ('alert_new_signin', 'alert_lock'):   # v1.61.0
            if f in d:
                setattr(p, f, str(d[f]).lower() in ('1', 'true'))
        if 'ip_scope' in d or 'ip_allowlist' in d:
            from . import netguard as NG
            scope = d.get('ip_scope', p.ip_scope)
            if scope not in ('off', 'staff', 'all'):
                return bad(tr('Choose who the allow-list applies to.'))
            text = str(d.get('ip_allowlist', p.ip_allowlist) or '')[:8000]
            prob = NG.check_save(request.user, scope, text, NG.client_ip(request))
            if prob:
                return bad(tr(prob) if '{' not in prob else prob)
            p.ip_scope, p.ip_allowlist = scope, text
            NG.forget(schema)
        prob = _policy_put62(p, d, schema, request)   # v1.62.0
        if prob:
            return bad(tr(prob))
        p.save()
        return Response(_policy_out(p, request))


def _company_users(schema):
    return U.objects.filter(tenants__schema_name=schema).distinct().order_by('email', 'username')


class SignInAlertsView(APIView):
    """v1.61.0 GET my sign-in alerts (?company=1: the company's users, administrators only); POST {id, answer: was_me | not_me}.
    "Not me" ends every sign-in of the user and asks for a new password at the next sign-in."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import SignInAlert
        from . import netguard as NG
        if request.query_params.get('company') in ('1', 'true'):
            schema = _company_admin(request)
            if not schema:
                return bad(tr('Company administrators only.'), 403)
            qs = SignInAlert.objects.filter(user__in=_company_users(schema), created_at__gte=timezone.now() - timedelta(days=90)).select_related('user')[:300]
            return Response({'rows': [NG.alert_json(a, True) for a in qs]})
        qs = SignInAlert.objects.filter(user=request.user)[:100]
        return Response({'rows': [NG.alert_json(a) for a in qs], 'open': SignInAlert.objects.filter(user=request.user, status='open').count()})

    def post(self, request):
        from .models import LoginSession, SignInAlert
        if getattr(request, 'auth', None) is not None and hasattr(request.auth, 'get') and request.auth.get('imp'):
            return bad(tr('Not while viewing as another user.'), 403)
        a = SignInAlert.objects.filter(pk=request.data.get('id'), user=request.user).first()
        if a is None:
            return bad(tr('Alert not found.'), 404)
        ans = request.data.get('answer')
        if ans not in ('was_me', 'not_me'):
            return bad(tr('Answer "was_me" or "not_me".'))
        a.status, a.answered_at = ans, timezone.now()
        a.save(update_fields=['status', 'answered_at'])
        ended = 0
        if ans == 'not_me':
            ended = S.revoke(LoginSession.objects.filter(user=request.user), 'the user reported a sign-in that was not theirs')
            u = request.user
            if hasattr(u, 'must_change_password'):
                u.must_change_password = True
                u.save(update_fields=['must_change_password'])
        return Response({'status': a.status, 'ended': ended})


class CompanyUsersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import LoginSecurity, LoginSession
        schema = _company_admin(request)
        if not schema:
            return bad('Company administrators only.', 403)
        users = list(_company_users(schema)[:2000])
        secs = {s.user_id: s for s in LoginSecurity.objects.filter(user__in=users)}
        now = timezone.now()
        act = {}
        for uid in LoginSession.objects.filter(user__in=users, revoked_at__isnull=True, created_at__gte=now - timedelta(days=14)).values_list('user_id', flat=True):
            act[uid] = act.get(uid, 0) + 1
        out = []
        q = (request.query_params.get('q') or '').lower()
        for u in users:
            name = u.get_full_name() or u.username
            if q and q not in f'{name} {u.email} {u.username}'.lower():
                continue
            s = secs.get(u.pk)
            out.append({'id': u.pk, 'name': name, 'email': u.email, 'username': u.username, 'is_ess': bool(u.is_ess), 'active': u.is_active,
                        'last_login': u.last_login, 'authenticator': bool(s and s.totp_on), 'locked': bool(s and s.locked_until and s.locked_until > now),
                        'sessions': act.get(u.pk, 0), 'me': u.pk == request.user.pk})
        return Response({'rows': out, 'policy': _policy_out(_policy(schema), request)})

    def post(self, request):
        from .models import LoginSecurity, LoginSession
        schema = _company_admin(request)
        if not schema:
            return bad('Company administrators only.', 403)
        u = _company_users(schema).filter(pk=request.data.get('user_id')).first()
        if u is None:
            return bad('User not found in this company.', 404)
        act = request.data.get('action')
        if act == 'unlock':
            LoginSecurity.objects.filter(user=u).update(failed=0, locked_until=None)
            return Response({'detail': 'Unlocked.'})
        if act == 'end_sessions':
            n = S.revoke(LoginSession.objects.filter(user=u), f'signed out by the administrator ({request.user.get_full_name() or request.user.username})')
            return Response({'detail': f'{n} sign-in(s) ended.'})
        if act == 'reset_2fa':
            if u.tenants.exclude(schema_name=schema).exists() and not request.user.is_superuser:
                return bad('This user also works in other companies – ask the platform administrator.', 403)
            LoginSecurity.objects.filter(user=u).update(totp_on=False, totp_secret='', recovery=[])
            S.event('2fa_reset', user=u, by=request.user, schema=schema, ip=S.client_ip(request),
                    detail=f'The authenticator app of {u.get_full_name() or u.username} was reset by {request.user.get_full_name() or request.user.username}.')
            return Response({'detail': 'The authenticator app was reset; the user sets it up again.'})
        return bad('Unknown action.')


def _audit(schema, target, action_text, by, request):
    try:
        from django_tenants.utils import schema_context
        from Chatter.models import RecordLog
        with schema_context(schema):
            RecordLog.objects.create(model='UserManagement.customuser', object_id=str(target.pk), record_label=(target.get_full_name() or target.username)[:255],
                                     action='login_as', changes={'impersonation': {'label': 'Viewing as this user', 'old': '', 'new': action_text}},
                                 user=by, user_name=(by.get_full_name() or by.username)[:150], ip=S.client_ip(request), path=request.path[:255],
                                 method=request.method, agent=request.META.get('HTTP_USER_AGENT', '')[:255])
    except Exception:
        pass


class ImpersonateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        schema = _company_admin(request)
        if not schema:
            return bad('Company administrators only.', 403)
        if getattr(request, 'auth', None) is not None and request.auth.get('imp'):
            return bad('You are already viewing as another user – stop first.')
        if not _policy(schema).allow_impersonation:
            return bad('Viewing as another user is switched off in the sign-in rules of the company.', 403)
        u = _company_users(schema).filter(pk=request.data.get('user_id'), is_active=True).first()
        if u is None:
            return bad('User not found in this company.', 404)
        if u.pk == request.user.pk:
            return bad('Choose another user.')
        if u.is_superuser or (S.is_company_admin(u) and not request.user.is_superuser):
            return bad('An administrator cannot be viewed as – choose a user who is not an administrator.', 403)
        ro = str(request.data.get('read_only', True)).lower() not in ('0', 'false')
        t = S.tokens(u, request, 'impersonation', otp=True, impersonator=request.user, read_only=ro, schema=schema, lifetime=timedelta(hours=1))
        _audit(schema, u, f'started by {request.user.get_full_name() or request.user.username}' + (' (read only)' if ro else ' (can change data)'),
               request.user, request)
        return Response({'access': t['access'], 'refresh': t['refresh'], 'user_id': u.pk, 'username': u.username, 'email': u.email,
                         'name': u.get_full_name() or u.username, 'is_ess': bool(u.is_ess), 'read_only': ro, 'expires_in': 3600})


class ImpersonateStopView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import LoginSession
        a = getattr(request, 'auth', None)
        sid = _sid(request)
        if a is None or not a.get('imp') or not sid:
            return Response({'detail': 'Not viewing as another user.'})
        S.revoke(LoginSession.objects.filter(sid=sid), 'impersonation ended')
        by = U.objects.filter(pk=a.get('imp')).first()
        sch = a.get('isch') or _schema(request)
        if by is not None and sch:
            _audit(sch, request.user, f'ended by {by.get_full_name() or by.username}', by, request)
        return Response({'detail': 'Ended.'})


# ------------------------------------------------------------------ v1.62.0 step-up and the security dashboard
class StepUpView(APIView):
    """POST {password} or {code}: the password (or the authenticator code) typed again – sensitive actions are open for the
    company's minutes."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from . import stepup
        sid = _sid(request)
        if not sid:
            return bad(tr('Sign in again.'), 401)
        if getattr(request, 'auth', None) is not None and request.auth.get('imp'):
            return bad(tr('Not while viewing as another user.'), 403)
        ok, msg = stepup.verify(request.user, sid, str(request.data.get('password') or ''), str(request.data.get('code') or ''))
        if not ok:
            return bad(tr(msg), 400 if 'wait' not in msg else 403)
        return Response({'ok': True, 'until_minutes': 10})


class SecurityDashboardView(APIView):
    """GET the security dashboard of the open company (administrators): users without the authenticator, wrong passwords,
    active sign-ins, API keys, last exports, administrators and rights given, recent security events."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django_tenants.utils import schema_context
        from .admin_alerts import event_json
        from .models import LoginSecurity, LoginSession, SecurityEvent
        schema = _company_admin(request)
        if not schema:
            return bad(tr('Company administrators only.'), 403)
        now = timezone.now()
        users = list(_company_users(schema).filter(is_active=True)[:5000])
        ids = [u.pk for u in users]
        secs = {s.user_id: s for s in LoginSecurity.objects.filter(user_id__in=ids)}
        staff = [u for u in users if not u.is_ess]
        no_2fa = [{'id': u.pk, 'name': u.get_full_name() or u.username, 'email': u.email, 'admin': S.is_company_admin(u)}
                  for u in staff if not (secs.get(u.pk) and secs[u.pk].totp_on)]
        week = now - timedelta(days=7)
        failed = SecurityEvent.objects.filter(kind__in=('failed_password', 'failed_code'), user_id__in=ids, created_at__gte=week)
        by_user = {}
        for uid, ip in failed.values_list('user_id', 'ip'):
            r = by_user.setdefault(uid, {'count': 0, 'ips': set()})
            r['count'] += 1
            r['ips'].add(ip)
        names = {u.pk: (u.get_full_name() or u.username) for u in users}
        failed_rows = sorted([{'user': names.get(k, ''), 'count': v['count'], 'addresses': sorted(x for x in v['ips'] if x)[:5]}
                              for k, v in by_user.items()], key=lambda r: -r['count'])[:30]
        life = timedelta(days=14)
        active = LoginSession.objects.filter(user_id__in=ids, revoked_at__isnull=True, created_at__gte=now - life).select_related('user')
        sess = [{'user': names.get(x.user_id, ''), 'ip': x.ip, 'country': x.country, 'device': S.session_json(x)['device'],
                 'since': x.created_at, 'last_seen': x.last_seen, 'admin': x.admin} for x in active.order_by('-last_seen')[:100]]
        locked = [names.get(s.user_id, '') for s in secs.values() if s.locked_until and s.locked_until > now]
        keys, exports, admins = [], [], []
        with schema_context(schema):
            try:
                from ZeoAdmin.models import ApiKey, ExportLog
                for k in ApiKey.objects.all().order_by('-id')[:50]:
                    keys.append({'name': k.name, 'read_only': getattr(k, 'read_only', False), 'active': getattr(k, 'active', True),
                                 'last_used': getattr(k, 'last_used', None), 'expires': getattr(k, 'expires_at', None),
                                 'scopes': getattr(k, 'scopes', []), 'user': names.get(getattr(k, 'user_id', None), '')})
                for e in ExportLog.objects.filter(at__gte=now - timedelta(days=30)).select_related('user')[:100]:
                    exports.append({'user': (e.user.get_full_name() or e.user.username) if e.user_id else '', 'path': e.path, 'kind': e.kind,
                                    'rows': e.rows, 'refused': e.refused, 'at': e.at})
            except Exception:
                pass
            try:
                from tenant_users.tenants.models import UserTenantPermissions
                for t in UserTenantPermissions.objects.filter(is_superuser=True).select_related('profile')[:100]:
                    u = t.profile
                    admins.append({'name': u.get_full_name() or u.username, 'email': u.email,
                                   'authenticator': bool(secs.get(u.pk) and secs[u.pk].totp_on)})
            except Exception:
                pass
        evs = SecurityEvent.objects.filter(created_at__gte=now - timedelta(days=30)).filter(
            models_q(schema, ids)).exclude(kind__in=('failed_password', 'failed_code')).select_related('user', 'by')[:200]
        return Response({'users': len(users), 'staff': len(staff), 'without_authenticator': no_2fa, 'failed_week': failed.count(),
                         'failed_by_user': failed_rows, 'locked': locked, 'sessions': sess, 'session_count': active.count(),
                         'api_keys': keys, 'exports': exports, 'admins': admins, 'events': [event_json(e) for e in evs],
                         'policy': _policy_out(_policy(schema), request)})


def models_q(schema, ids):
    from django.db.models import Q
    return Q(schema=schema) | Q(schema='', user_id__in=ids)


# ------------------------------------------------------------------ v1.62.0 activation without e-mail, the default password's first check
class ActivateSendView(APIView):
    """POST {username}: a code to the mobile on file (first sign-in or forgot password without e-mail). The answer is the
    same whether the user exists or not."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from ZeoAdmin.giveaccess import activate_send
        try:
            activate_send(request.data.get('username'))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('activate send')
        return Response({'detail': tr('If your user name is right and HR set up WhatsApp / SMS, a code is on its way to your mobile.')})


class ActivateView(APIView):
    """POST {username, code, password}: the code of the slip or the message, and the employee's own new password."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from ZeoAdmin.giveaccess import activate
        try:
            u = activate(request.data.get('username'), request.data.get('code'), request.data.get('password'))
        except ValueError as e:
            return bad(str(e))
        return Response({'detail': tr('Your password is set. Sign in with your user name and your new password.'), 'username': u.username})


class FirstCheckView(APIView):
    """POST {challenge, eid_last4, dob}: the first sign-in with the company default password asks the last 4 digits of the
    Emirates ID and the date of birth (3 wrong tries lock the account until HR unlocks it)."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        from ZeoAdmin.giveaccess import default_state, first_check
        try:
            d = signing.loads(str(request.data.get('challenge') or ''), salt='zeo-first-check', max_age=600)
        except signing.BadSignature:
            return bad(tr('The sign-in took too long – sign in again.'), 401)
        user = U.objects.filter(pk=d.get('u'), is_active=True).first()
        if user is None:
            return bad(tr('Sign in again.'), 401)
        if S.locked(user):
            return bad(tr('Your account is locked after wrong tries – ask HR to unlock it.'), 403)
        ds = default_state(user)
        if ds is None or ds['expired']:
            return bad(tr('Your start password has expired – ask HR to give you access again.'), 403)
        if not first_check(user, d.get('s'), request.data.get('eid_last4'), request.data.get('dob')):
            S.fail(user)
            return bad(tr('The Emirates ID digits or the date of birth do not match.'))
        S.login_ok(user)
        out = login_payload(user, request, 'start password')
        out['must_change_password'] = True
        return Response(out)
