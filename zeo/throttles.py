"""v1.62.0 – rate limits (README-UPDATE 83.4, release checklist "Rate limits").

* ZeoUserThrottle   – every signed-in user: ZEO_RATE_USER (default 600 requests a minute);
* ZeoAnonThrottle   – requests without a login, per address: ZEO_RATE_ANON (120 a minute);
* ZeoScopedThrottle – lower limits on the doors attackers knock on:
    signin  sign-in, the authenticator step, single sign-on exchange             – per address (20 a minute)
    code    e-mail / SMS codes, set-password links, activation codes, step-up     – per address (20 a minute)
    reset   "forgot password" (codes sent, passwords reset)                      – per address (10 an hour)
    export  Excel / CSV / PDF downloads of lists                                  – per user (60 an hour)
    search  searches (?search= / ?q=)                                             – per user (120 a minute)
    paging  page after page of the employee list API                               – per user (240 a minute)
The address is the one the proxy in front of ZEO saw (the last X-Forwarded-For entry) – it cannot be typed by the visitor.
Answers 429 with "Retry-After". The counters live in the cache (Redis on a server).
"""
import re

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle, UserRateThrottle

SIGNIN = ('/users/token/', '/users/2fa/login/', '/users/sso/exchange/', '/users/validate-credentials/')
CODE = ('/users/verify-otp/', '/users/send-otp/', '/users/verify-reset-otp/', '/users/ess-verify-reset-otp/', '/users/sign-in-link/',
        '/users/activate/', '/users/step-up/', '/users/2fa/')
RESET = ('/users/send-reset-otp/', '/users/ess-send-reset-otp/', '/users/reset-password/', '/users/ess-reset-password/',
         '/users/activate/send/', '/users/forgot/')
EXPORT_Q = re.compile(r'(^|&)(file|format|export)=(xlsx|csv|pdf|excel)(&|$)', re.I)
PAGING = ('/employee/api/Employee/', '/api/v1/employees/')


def _ip(request):
    from ZeoCloud.netguard import client_ip
    return client_ip(request) or request.META.get('REMOTE_ADDR', '')


def _uid(request):
    """The signed-in user's id, or None – also for callers that are not users (accounting API clients, kiosks, API keys)."""
    u = getattr(request, 'user', None)
    if u is not None and getattr(u, 'is_authenticated', False) is True:
        return getattr(u, 'pk', None)
    return None


class ZeoUserThrottle(UserRateThrottle):
    def get_ident(self, request):
        return _ip(request)

    def get_cache_key(self, request, view):
        uid = _uid(request)
        ident = uid if uid is not None else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class ZeoAnonThrottle(AnonRateThrottle):
    def get_ident(self, request):
        return _ip(request)

    def allow_request(self, request, view):
        if request.method == 'OPTIONS':
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        if _uid(request) is not None:
            return None   # signed in: the user limit counts
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


def scope_of(request):
    p = request.path
    if request.method == 'OPTIONS':
        return None
    if request.method == 'POST' and p.startswith(SIGNIN):
        return 'signin'
    if request.method == 'POST' and p.startswith(RESET):
        return 'reset'
    if request.method == 'POST' and p.startswith(CODE):
        return 'code'
    if request.method == 'GET':
        qs = request.META.get('QUERY_STRING', '')
        if EXPORT_Q.search(qs) or '/export' in p:
            return 'export'
        if re.search(r'(^|&)(search|q)=[^&]', qs):
            return 'search'
        if p.startswith(PAGING) and re.search(r'(^|&)page=\d', qs):
            return 'paging'
    return None


class ZeoScopedThrottle(SimpleRateThrottle):
    scope = 'code'

    def allow_request(self, request, view):
        sc = scope_of(request)
        if sc is None:
            return True
        self.scope = sc
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        return super().allow_request(request, view)

    def get_rate(self):
        try:
            return self.THROTTLE_RATES[self.scope]
        except KeyError:
            return None

    def get_cache_key(self, request, view):
        if self.scope in ('export', 'search', 'paging') and _uid(request) is not None:
            ident = f'u{_uid(request)}'
        else:
            ident = _ip(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}
