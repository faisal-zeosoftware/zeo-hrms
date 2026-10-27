"""v1.52.0 – API keys of the company for integrations (Power BI, a website, another system).

Header:  Authorization: Api-Key zeo_<prefix>_<secret>     (or X-API-Key: zeo_…), and ?schema=<company> as for every API call.
The key acts as the user it was made for – that user's rights and branches apply – read only (GET) or read / write.
Only the hash of the key is stored; the key is shown once when it is made. Optional: allowed IP addresses, expiry date.
"""
import hashlib
import secrets

from django.db import connection
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

SAFE = ('GET', 'HEAD', 'OPTIONS')


def make_key():
    prefix = secrets.token_hex(4)
    return prefix, f'zeo_{prefix}_{secrets.token_urlsafe(32)}'


def hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def client_ip(request):
    """v1.61.0: the address the proxy saw (last X-Forwarded-For entry), not the first one the caller can type."""
    from ZeoCloud.netguard import client_ip as _ip
    return _ip(request)


class ApiKeyAuthentication(BaseAuthentication):
    keyword = 'Api-Key'

    def _raw(self, request):
        h = request.META.get('HTTP_AUTHORIZATION', '')
        if h.startswith(self.keyword + ' '):
            return h[len(self.keyword) + 1:].strip()
        return request.META.get('HTTP_X_API_KEY', '').strip() or None

    def authenticate(self, request):
        raw = self._raw(request)
        if not raw:
            return None
        if getattr(connection, 'schema_name', 'public') in ('', 'public'):
            raise exceptions.AuthenticationFailed('Give the company: ?schema=<company>.')
        from .models import ApiKey
        k = ApiKey.objects.select_related('user').filter(key_hash=hash_key(raw), active=True).first()
        if k is None:
            raise exceptions.AuthenticationFailed('API key not valid.')
        if k.expires_at and k.expires_at < timezone.localdate():
            raise exceptions.AuthenticationFailed('API key expired.')
        if k.v1_only and not request.path.startswith('/api/v1/'):   # v1.61.0
            raise exceptions.AuthenticationFailed('This API key is for /api/v1/ only.')
        ip = client_ip(request)
        if k.allowed_ips and ip not in k.allowed_ips:
            raise exceptions.AuthenticationFailed(f'API key not allowed from {ip}.')
        if k.read_only and request.method not in SAFE:
            raise exceptions.PermissionDenied('This API key is read only.')
        if not k.user.is_active:
            raise exceptions.AuthenticationFailed('The user of this API key is not active.')
        ApiKey.objects.filter(pk=k.pk).update(last_used=timezone.now(), last_ip=ip[:60], uses=(k.uses or 0) + 1)
        request._zeo_api_key = k.pk
        return (k.user, {'api_key': k.pk})

    def authenticate_header(self, request):
        return self.keyword
