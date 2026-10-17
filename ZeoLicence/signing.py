"""v1.28.0 – licence file format and signature (Ed25519).

    ZEO licence – <client> – valid until <date>          (lines before BEGIN are ignored – for people)
    -----BEGIN ZEO LICENCE-----
    <base64url(JSON payload)>.<key id>.<base64url(signature of the payload bytes)>
    -----END ZEO LICENCE-----
"""
import base64
import json

from django.conf import settings

BEGIN, END = '-----BEGIN ZEO LICENCE-----', '-----END ZEO LICENCE-----'


class LicenceError(Exception):
    pass


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _b64d(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def public_keys():
    from .keys import ADEPT_PUBLIC_KEYS, TEST_KEYS
    keys = dict(ADEPT_PUBLIC_KEYS)
    for part in (getattr(settings, 'ZEO_LICENCE_PUBLIC_KEYS', '') or '').split(','):
        if ':' in part:
            k, v = part.split(':', 1)
            keys[k.strip()] = v.strip()
    if getattr(settings, 'ZEO_LICENCE_ACCEPT_TEST', False):
        keys.update(TEST_KEYS)
    return keys


def load_private(path=None):
    from cryptography.hazmat.primitives import serialization
    path = path or getattr(settings, 'ZEO_LICENCE_PRIVATE_KEY', '')
    if not path:
        raise LicenceError('No private key: set ZEO_LICENCE_PRIVATE_KEY in .env (see ZeoLicence/keys.py).')
    with open(path, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def key_id_of(private_key):
    """The id of the matching public key (ADEPT_PUBLIC_KEYS / TEST_KEYS / ZEO_LICENCE_PUBLIC_KEYS)."""
    from cryptography.hazmat.primitives import serialization
    pub = base64.b64encode(private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    from .keys import ADEPT_PUBLIC_KEYS, TEST_KEYS
    for k, v in list(public_keys().items()) + list(TEST_KEYS.items()) + list(ADEPT_PUBLIC_KEYS.items()):
        if v == pub:
            return k
    explicit = getattr(settings, 'ZEO_LICENCE_KEY_ID', '')
    if explicit:
        return explicit
    raise LicenceError('The public key of ZEO_LICENCE_PRIVATE_KEY is not in ZeoLicence/keys.py – clients could not check the file.')


def sign(payload, private_key=None):
    pk = private_key or load_private()
    body = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    sig = pk.sign(body)
    head = f"ZEO licence – {payload.get('client', '')} – installation {payload.get('installation_id', '')} – valid until {payload.get('valid_until', '')}"
    return f"{head}\n{BEGIN}\n{_b64e(body)}.{key_id_of(pk)}.{_b64e(sig)}\n{END}\n"


def verify(text):
    """(payload dict, key id) – or LicenceError with the reason."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    t = (text or '').strip()
    if BEGIN not in t or END not in t:
        raise LicenceError('This is not a ZEO licence file.')
    core = t.split(BEGIN, 1)[1].split(END, 1)[0].strip().replace('\n', '').replace('\r', '').replace(' ', '')
    parts = core.split('.')
    if len(parts) != 3:
        raise LicenceError('The licence file is damaged.')
    body_b, kid, sig_b = parts
    keys = public_keys()
    if kid not in keys:
        raise LicenceError(f'The licence was signed with a key this ZEO does not know ({kid}).')
    try:
        body, sig = _b64d(body_b), _b64d(sig_b)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[kid])).verify(sig, body)
    except InvalidSignature:
        raise LicenceError('The licence file was changed – its signature does not match.')
    except Exception:
        raise LicenceError('The licence file is damaged.')
    return json.loads(body), kid
