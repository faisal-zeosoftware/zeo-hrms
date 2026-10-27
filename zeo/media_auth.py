"""v1.56.1 – uploaded files need a login (release checklist 13 "Files need login").

Before, nginx served /media/ to anyone with the link. Now every /media/ request goes through Django, which serves the file
only to a signed-in user:
  - the browser: the cookie `zeo_m` (HttpOnly, set by /users/media-cookie/ right after sign-in, valid 12 hours, renewed by
    the app) – images, PDFs and downloads keep working with their plain /media/ address;
  - the phone app and API clients: `Authorization: Bearer <token>` or `?zm=<media token>` (the same signed value).
v1.62.0: a file linked to a record (ZeoCloud.mediareg) opens only for a user of its company who may open the record.
Company logos (media/logos/) stay public – they are on the sign-in page and in e-mails.
Behind nginx the file is sent by nginx itself (X-Accel-Redirect to the internal location /_protected_media/); with the
development server Django streams it. ZEO_MEDIA_LOGIN=false in .env switches the check off (the old behaviour).
"""
import mimetypes
import os
import posixpath

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404, HttpResponse

COOKIE = 'zeo_m'
MAX_AGE = 12 * 3600
SALT = 'zeo-media-v1'
PUBLIC_PREFIXES = ('logos/',)


def make_token(user):
    return signing.TimestampSigner(salt=SALT).sign(str(user.pk))


def _user_from_token(value):
    if not value:
        return None
    try:
        uid = signing.TimestampSigner(salt=SALT).unsign(value, max_age=MAX_AGE)
    except signing.BadSignature:
        return None
    from django.contrib.auth import get_user_model
    return get_user_model().objects.filter(pk=uid, is_active=True).first()


def _user_from_jwt(request):
    h = request.META.get('HTTP_AUTHORIZATION', '')
    if not h.startswith('Bearer '):
        return None
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        auth = JWTAuthentication()
        tok = auth.get_validated_token(h[7:].strip())
        from ZeoCloud.security import revoked   # v1.62.0 a signed-out session's token opens no file
        if tok.get('sid') and revoked(tok.get('sid')):
            return None
        return auth.get_user(tok)
    except Exception:
        return None


def user_of(request):
    return _user_from_token(request.COOKIES.get(COOKIE)) or _user_from_token(request.GET.get('zm')) or _user_from_jwt(request)


def allowed(request):
    return bool(user_of(request))


def record_allows(user, path):
    """v1.62.0 the file's company and record: True / False, None for a file that is in no record."""
    try:
        from ZeoCloud.mediareg import may_open
        return may_open(user, path)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('media record check')
        return None


def set_cookie(response, request, user):
    token = make_token(user)
    response.set_cookie(COOKIE, token, max_age=MAX_AGE, httponly=True, samesite='Lax', secure=request.is_secure(), path='/media/')
    return token


def serve(request, path):
    path = posixpath.normpath(path).lstrip('/')
    if path.startswith('..') or '/../' in f'/{path}/':
        raise Http404
    root = os.path.realpath(settings.MEDIA_ROOT)
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(root + os.sep):
        raise Http404
    # the sign-in check comes first: without a login a missing file answers the same as a present one (nothing to probe)
    if getattr(settings, 'ZEO_MEDIA_LOGIN', True) and not path.startswith(PUBLIC_PREFIXES):
        user = user_of(request)
        if user is None:
            r = HttpResponse('<!doctype html><meta charset="utf-8"><title>Sign in</title><body style="font:15px system-ui;padding:40px">'
                             '<h1 style="font-size:20px">Sign in to ZEO to open this file</h1>'
                             '<p>Files are only shown to signed-in users. Sign in, then open the link again.</p>', status=401)
            r['Cache-Control'] = 'no-store'
            return r
        # v1.62.0 the file's company and record: only a user of that company who may open the record (gap 1)
        ok = record_allows(user, path)
        if ok is None and getattr(settings, 'ZEO_MEDIA_STRICT', False) and not user.is_superuser:
            ok = False
        if ok is False:
            r = HttpResponse('<!doctype html><meta charset="utf-8"><title>Not allowed</title><body style="font:15px system-ui;padding:40px">'
                             '<h1 style="font-size:20px">You cannot open this file</h1>'
                             '<p>The file belongs to a record you do not have access to.</p>', status=403)
            r['Cache-Control'] = 'no-store'
            return r
    if not os.path.isfile(full):
        raise Http404
    if getattr(settings, 'ZEO_MEDIA_ACCEL', False):   # nginx sends the file (internal location /_protected_media/)
        r = HttpResponse()
        r['X-Accel-Redirect'] = '/_protected_media/' + path
        r['Content-Type'] = mimetypes.guess_type(full)[0] or 'application/octet-stream'
    else:
        r = FileResponse(open(full, 'rb'))
    r['Cache-Control'] = 'private, max-age=3600'
    r['X-Content-Type-Options'] = 'nosniff'
    return r
