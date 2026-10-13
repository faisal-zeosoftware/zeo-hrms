"""v1.23.0 – ZEO Cloud checks on every company request (only when settings.ZEO_CLOUD is on):

* the signed-in user must be a user of the company (Adept platform administrators excepted);
* the account's status: cancelled → closed; suspended (or trial / grace period over) → read-only;
* an app whose licence ended (or is not in the plan) is read-only in the company.
Companies that are not linked to a cloud account are not touched.
"""
import json
import logging
import re

from django.db import connection
from django.http import HttpResponse

log = logging.getLogger(__name__)
OPEN = ('/users/', '/core/', '/cloud/', '/language/', '/admin', '/static', '/media', '/zeo-apps/')
SAFE = ('GET', 'HEAD', 'OPTIONS')
READONLY_OK = re.compile(r'notif|/language/api/me|assistant|/export|/report|/views/api', re.I)
APP_PREFIX = [('/payroll-plus/', 'payroll'), ('/payroll/', 'payroll'), ('/zeo-projects/', 'projects'), ('/zeo-crm/', 'crm'),
              ('/zeo-property/', 'property'), ('/zeo-maintenance/', 'maintenance'), ('/zeo-hse/', 'hse')]


def _json(data, status):
    return HttpResponse(json.dumps(data), status=status, content_type='application/json')


def _user(request):
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        r = JWTAuthentication().authenticate(request)
        return r[0] if r else None
    except Exception:
        return None


class CloudMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .services import cloud_on
        if not cloud_on() or request.method == 'OPTIONS' or request.path.startswith(OPEN):
            return self.get_response(request)
        schema = getattr(connection, 'schema_name', 'public')
        if not schema or schema == 'public':
            return self.get_response(request)
        try:
            blocked = self.check(request, schema)
        except Exception:
            log.exception('cloud check')
            blocked = None
        return blocked or self.get_response(request)

    def check(self, request, schema):
        from .services import app_licence_state, is_platform_admin, link_of_schema, status_of, tr
        user = _user(request)
        if user is not None and not is_platform_admin(user) and not user.tenants.filter(schema_name=schema).exists():
            return _json({'detail': tr('You do not have access to this company.'), 'cloud': 'access'}, 403)
        link = link_of_schema(schema)
        if link is None:
            return None
        acc = link.account
        if link.moving:
            return _json({'detail': tr('{0} is being moved. This takes a few minutes; nothing is lost. Please try again shortly.', link.company.name), 'cloud': 'moving'}, 503)
        st = status_of(acc)
        if st['blocked']:
            return _json({'detail': tr('This subscription is cancelled.'), 'cloud': 'cancelled'}, 403)
        unsafe = request.method not in SAFE and not READONLY_OK.search(request.path)
        if st['read_only'] and unsafe:
            return _json({'detail': tr('Read-only: the subscription is suspended. You can view, export and print; adding or changing records is off until the invoice is paid.'), 'cloud': 'readonly'}, 423)
        if unsafe:
            app = next((a for p, a in APP_PREFIX if request.path.startswith(p)), None)
            if app:
                s = app_licence_state(acc, app)
                if s in ('none', 'expired'):
                    from .services import APP_TITLES
                    msg = tr('{0} is read-only: its licence ended.', APP_TITLES.get(app, app)) if s == 'expired' else \
                        tr('{0} is not in your plan.', APP_TITLES.get(app, app))
                    return _json({'detail': msg, 'cloud': 'app'}, 423)
        return None
