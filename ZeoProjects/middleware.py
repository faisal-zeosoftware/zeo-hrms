"""Portal guard: customer portal users may only use the portal API.

Add after the Chatter middleware in settings.MIDDLEWARE:
    'ZeoProjects.middleware.PortalGuardMiddleware',
It runs inside the company schema (after UserManagement.middleware.SchemaMiddleware) and reads the user id from
the JWT without a database query; the "is this a portal user" answer is cached for five minutes.
"""
from django.db import connection
from django.http import JsonResponse

ALLOWED = ('/zeo-projects/api/portal/', '/users/', '/core/', '/media/', '/static/')


class PortalGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if not path.startswith(ALLOWED) and connection.schema_name != 'public':
            auth = request.META.get('HTTP_AUTHORIZATION', '')
            if auth.startswith('Bearer '):
                uid = self._user_id(auth[7:].strip())
                if uid is not None:
                    from .portal_users import is_portal_user
                    try:
                        portal = is_portal_user(connection.schema_name, uid)
                    except Exception:
                        portal = False   # e.g. the tables are not migrated yet in this company
                    if portal:
                        return JsonResponse({'detail': 'Customer portal users can only use the portal.'}, status=403)
        return self.get_response(request)

    @staticmethod
    def _user_id(raw):
        try:
            from rest_framework_simplejwt.tokens import AccessToken
            from rest_framework_simplejwt.settings import api_settings
            return AccessToken(raw).get(api_settings.USER_ID_CLAIM)
        except Exception:
            return None
