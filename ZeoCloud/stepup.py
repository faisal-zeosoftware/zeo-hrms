"""v1.62.0 – "password asked again" before sensitive actions (README-UPDATE 83.7, step-up check).

When a company switches it on (Settings › Sign-in security › Ask again before sensitive actions), these actions need the
password – or the authenticator code when the user has the app – typed again within the last N minutes (default 10):
  payroll_approve  approving / confirming payroll and payslips, final settlements
  wps              the WPS / SIF salary file
  bank             adding, changing or removing an employee's bank details
  rights           permission groups, permissions, user branch access, user logins and their rights
  export           Excel / CSV / PDF exports of lists
The API answers 403 {"code": "step_up_required", "step_up": <action>}; the app asks for the password, calls
POST /users/step-up/ and repeats the request. Requests with an API key are not asked (integrations have no password).
"""
import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)
WRITE = ('POST', 'PUT', 'PATCH', 'DELETE')
ACTIONS = {
    'payroll_approve': [(WRITE, re.compile(r'^/payroll(-plus)?/.*(approve|confirm|finali[sz]e|lock)', re.I))],
    'wps': [(None, re.compile(r'^/(payroll|payroll-plus|dashboard)/.*(sif|wps)', re.I))],
    'bank': [(WRITE, re.compile(r'^/(employee|employee-profile)/.*bank', re.I))],
    'rights': [(WRITE, re.compile(r'^/organisation/api/(group|perm|permissions|user-branch-access)(/|$)', re.I)),
               (WRITE, re.compile(r'^/users/api/(user|invites)(/|$)', re.I))],
    'export': [(('GET', 'POST'), re.compile(r'.*')), ],
}
EXPORT_Q = re.compile(r'(^|&)(file|format|export)=(xlsx|csv|pdf|excel)(&|$)', re.I)
LABELS = {'payroll_approve': 'approving payroll', 'wps': 'the WPS salary file', 'bank': 'changing bank details',
          'rights': 'changing user rights', 'export': 'exporting a list'}


def action_of(request):
    p, m = request.path, request.method
    if m == 'OPTIONS':
        return None
    for kind, rules in ACTIONS.items():
        if kind == 'export':
            if m == 'GET' and (EXPORT_Q.search(request.META.get('QUERY_STRING', '')) or re.search(r'/export(/|$|-)', p)):
                return 'export'
            if m == 'POST' and p.startswith('/zeo-admin/api/exports/check/'):   # exports made in the browser
                return 'export'
            continue
        for methods, rx in rules:
            if (methods is None or m in methods) and rx.search(p):
                return kind
    return None


def _token(request):
    h = request.META.get('HTTP_AUTHORIZATION', '')
    if not h.startswith('Bearer '):
        return None
    try:
        from rest_framework_simplejwt.tokens import UntypedToken
        return UntypedToken(h[7:].strip())
    except Exception:
        return None


def _policies(user, schema):
    from .models import SecurityPolicy
    if schema and schema != 'public':
        return list(SecurityPolicy.objects.filter(schema=schema))
    return list(SecurityPolicy.objects.filter(schema__in=user.tenants.values('schema_name')))


def rule_for(user, schema, kind):
    """Minutes allowed since the last confirmation, or 0 when the company does not ask for this action."""
    k = f'zeo:stepup-rule:{cache.get("zeo:stepgen", 0)}:{user.pk}:{schema}:{kind}'
    v = cache.get(k)
    if v is None:
        v = 0
        for p in _policies(user, schema):
            if p.step_up and (not p.step_up_actions or kind in p.step_up_actions):
                v = max(1, p.step_up_minutes or 10) if not v else min(v, max(1, p.step_up_minutes or 10))
        cache.set(k, v, 30)
    return v


def forget():
    """A company changed its rules: every cached step-up rule is old."""
    try:
        cache.incr('zeo:stepgen')
    except ValueError:
        cache.set('zeo:stepgen', 1, None)


def confirmed_at(sid):
    k = f'zeo:stepup:{sid}'
    v = cache.get(k)
    if v is None:
        from .models import LoginSession
        v = LoginSession.objects.filter(sid=sid).values_list('step_up_at', flat=True).first() or 0
        cache.set(k, v, 30)
    return v or None


def confirm(sid):
    from .models import LoginSession
    now = timezone.now()
    LoginSession.objects.filter(sid=sid).update(step_up_at=now)
    cache.set(f'zeo:stepup:{sid}', now, 30)
    return now


class StepUpMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            kind = action_of(request)
            if kind:
                tok = _token(request)
                if tok is not None and tok.get('sid'):
                    from django.contrib.auth import get_user_model
                    from django.db import connection
                    user = get_user_model().objects.filter(pk=tok.get('user_id')).first()
                    schema = getattr(connection, 'schema_name', 'public')
                    if user is not None and not user.is_superuser:
                        mins = rule_for(user, schema, kind)
                        if mins:
                            at = confirmed_at(tok['sid'])
                            if not at or at < timezone.now() - timedelta(minutes=mins):
                                from Translations.i18n import tr
                                from .middleware import _json
                                return _json({'detail': tr('Type your password again to continue ({0}).', tr(LABELS[kind])),
                                              'code': 'step_up_required', 'step_up': kind, 'totp': _totp(user)}, 403)
        except Exception:
            log.exception('step-up check')
        return self.get_response(request)


def _totp(user):
    from .security import totp_on
    return totp_on(user)


def verify(user, sid, password='', code=''):
    """(ok, message). The authenticator code when the user has the app, else the password."""
    from . import security as S
    if S.locked(user):
        return False, 'Too many wrong tries – wait a few minutes.'
    ok = False
    if code and S.totp_on(user):
        ok = S.check_code(user, code)
    elif password:
        try:
            from .directory import password_ok
            ok = bool(password_ok(user, password)[0])
        except Exception:
            ok = user.check_password(password)
        if not ok:
            S.fail(user, kind='step_up_failed')
    if not ok:
        return False, 'The password (or code) is not correct.'
    S.login_ok(user)
    confirm(sid)
    return True, ''
