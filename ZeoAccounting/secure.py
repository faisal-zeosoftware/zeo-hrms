"""Connector secrets (Odoo API key, Zoho client secret and refresh token) are kept encrypted in AccountingSettings.secrets
with a key made from the server's SECRET_KEY (Fernet, AES-128 + HMAC). They are never returned to the browser – the API
only says whether a secret is set."""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

NAMES = ('odoo_api_key', 'zoho_client_secret', 'zoho_refresh_token', 'zoho_access_token')


def _f():
    key = hashlib.sha256(('zeo-accounting:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def put(st, name, value):
    if name not in NAMES:
        raise ValueError(name)
    sec = dict(st.secrets or {})
    if value:
        sec[name] = _f().encrypt(str(value).encode()).decode()
    else:
        sec.pop(name, None)
    st.secrets = sec


def get(st, name):
    v = (st.secrets or {}).get(name)
    if not v:
        return ''
    try:
        return _f().decrypt(v.encode()).decode()
    except (InvalidToken, ValueError):   # SECRET_KEY changed – enter it again
        return ''


def present(st):
    return {n: bool(get(st, n)) for n in NAMES if n != 'zoho_access_token'}
