"""Portal guard: tenant / owner / buyer portal users may only use the Property portal API.

Add after the Projects portal guard in settings.MIDDLEWARE:
    'ZeoProperty.middleware.PortalGuardMiddleware',
"""
from django.db import connection
from django.http import JsonResponse

ALLOWED = ('/zeo-property/api/portal/', '/users/', '/core/', '/media/', '/static/')


class PortalGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.path.startswith(ALLOWED) and connection.schema_name != 'public':
            auth = request.META.get('HTTP_AUTHORIZATION', '')
            if auth.startswith('Bearer '):
                uid = self._user_id(auth[7:].strip())
                if uid is not None:
                    from .portal import is_portal_user
                    try:
                        portal = is_portal_user(connection.schema_name, uid)
                    except Exception:
                        portal = False   # tables not migrated in this company
                    if portal:
                        return JsonResponse({'detail': 'Portal users can only use the portal.'}, status=403)
        return self.get_response(request)

    @staticmethod
    def _user_id(raw):
        try:
            from rest_framework_simplejwt.settings import api_settings
            from rest_framework_simplejwt.tokens import AccessToken
            return AccessToken(raw).get(api_settings.USER_ID_CLAIM)
        except Exception:
            return None
