"""v1.28.0 – Enterprise licence checks for every request (only with ZEO_EDITION=enterprise).

  read-only (licence ended beyond the grace period, no licence after the evaluation, or one that starts later):
      saving is refused with 423 and the reason – viewing, exports, reports and print work; the licence screen,
      sign-in and the language stay open so the new file can be uploaded;
  an app the licence does not include: saving in it is refused (as for ZEO Cloud app licences).
"""
import json
import logging
import re

from django.http import HttpResponse

log = logging.getLogger(__name__)
SAFE = ('GET', 'HEAD', 'OPTIONS')
OPEN = ('/licence/', '/users/token', '/users/validate-credentials', '/users/verify-otp', '/users/send-otp',
        '/users/send-reset-otp', '/users/verify-reset-otp', '/users/reset-password', '/users/change-password',
        '/language/', '/admin', '/static', '/media', '/mobile/api/unlock/', '/users/sso/', '/users/2fa/', '/scim/', '/users/sign-in-link/')   # v1.54.0, v1.56.0
READONLY_OK = re.compile(r'notif|/language/api/me|assistant|/export|/report|/views/api', re.I)
APP_PREFIX = [('/payroll-plus/', 'payroll'), ('/accounting/', 'payroll'), ('/payroll/', 'payroll'), ('/zeo-projects/', 'projects'), ('/zeo-crm/', 'crm'),
              ('/zeo-property/', 'property'), ('/zeo-maintenance/', 'maintenance'), ('/zeo-assets/', 'assets'), ('/zeo-hse/', 'hse')]


def _json(data, status):
    return HttpResponse(json.dumps(data), status=status, content_type='application/json')


class LicenceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .licence import edition_on
        if not edition_on() or request.method in SAFE or request.path.startswith(OPEN) or READONLY_OK.search(request.path):
            return self.get_response(request)
        try:
            blocked = self.check(request)
        except Exception:
            log.exception('licence check')
            blocked = None
        return blocked or self.get_response(request)

    def check(self, request):
        from Translations.i18n import tr
        from .licence import app_allowed, current
        st = current()
        if st['read_only']:
            return _json({'detail': tr('Read-only: {0}', st['message']), 'licence': st['status']}, 423)
        app = next((a for p, a in APP_PREFIX if request.path.startswith(p)), None)
        if app and not app_allowed(app):
            return _json({'detail': tr('{0} is not in the ZEO licence of this server.', app.title()), 'licence': 'app'}, 423)
        return None
