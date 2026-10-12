"""v1.15.0 – request language.

Put it right AFTER 'UserManagement.middleware.SchemaMiddleware' in MIDDLEWARE (so the company schema is active):
    'LanguagePack.middleware.LanguageMiddleware',

Order: header X-Language (the Angular app sends it on every call) → the user's own language → the company default → en.
Only languages the company offers count. Sets Django's active language (Django / DRF messages) and the language used by
LanguagePack.services.t(); answers carry `Content-Language`. With settings.LANGUAGE_PACK_DJANGO_ACTIVATE = False only t()
follows the request language and Django stays in English.
"""
import logging

from django.conf import settings
from django.db import connection
from django.utils import translation

from . import services as S

logger = logging.getLogger(__name__)


def jwt_user_id(request):
    """User id from a session login or a valid Bearer JWT (DRF authenticates later, in the view)."""
    u = getattr(request, 'user', None)
    if u is not None and getattr(u, 'is_authenticated', False):
        return u.pk
    auth = request.META.get('HTTP_AUTHORIZATION', '')
    if not auth.startswith('Bearer '):
        return None
    try:
        from rest_framework_simplejwt.tokens import AccessToken
        from rest_framework_simplejwt.settings import api_settings
        tok = AccessToken(auth.split(' ', 1)[1].strip())
        return tok.get(api_settings.USER_ID_CLAIM)
    except Exception:
        return None


class LanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        schema = getattr(connection, 'schema_name', 'public') or 'public'
        header = request.META.get('HTTP_X_LANGUAGE', '')
        try:
            if S.base_lang(header) and S.base_lang(header) in S.company_languages(schema):
                lang, src = S.base_lang(header), 'header'
            else:
                lang, src = S.resolve(header, jwt_user_id(request), schema)
        except Exception:      # tables not there yet (before migrate) – never block a request
            logger.debug('language resolution failed', exc_info=True)
            lang, src = S.SOURCE_LANG, 'default'
        request.LANGUAGE_CODE = lang
        request.zeo_lang = lang
        request.zeo_lang_source = src
        token = S.set_current(lang)
        activate = getattr(settings, 'LANGUAGE_PACK_DJANGO_ACTIVATE', True)
        if activate:
            translation.activate(lang)
        try:
            response = self.get_response(request)
        finally:
            S.reset_current(token)
            if activate:
                translation.deactivate()
        try:
            response.headers.setdefault('Content-Language', lang)
        except Exception:
            pass
        return response
