"""v1.62.0 – encrypted backups (AES-256-GCM, streamed in 4 MB pieces – a backup of any size, little memory).

The key is NOT kept with the backup: ZEO_BACKUP_KEY in .env (make one with `python manage.py zeo_backup --new-key`) – keep a copy
of it outside the server (password manager / safe). Without the key the backup cannot be read – by anybody, us included.

File format (.enc): b'ZEOENC1' | kdf byte (0 = raw 32-byte key, 1 = passphrase → scrypt) | salt (16) | chunk size (4)
then pieces: nonce (12) | length (4) | ciphertext + tag. The piece number and a "last piece" flag are bound to every piece, so a
file cut short or put in another order does not decrypt.
"""
import base64
import hashlib
import os
import struct

MAGIC = b'ZEOENC1'
CHUNK = 4 * 1024 * 1024


class KeyError_(Exception):
    pass


def new_key():
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def _raw(key):
    try:
        b = base64.urlsafe_b64decode(key + '=' * (-len(key) % 4))
        if len(b) == 32:
            return b
    except Exception:
        pass
    return None


def _derive(key, kdf, salt):
    if kdf == 0:
        b = _raw(key)
        if b is None:
            raise KeyError_('The backup was made with a 32-byte key – ZEO_BACKUP_KEY is not that key.')
        return b
    return hashlib.scrypt(key.encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def encrypt_file(src, dst, key):
    """Encrypt src → dst. Returns the SHA-256 of the plain file."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    kdf = 0 if _raw(key) is not None else 1
    salt = os.urandom(16)
    aes = AESGCM(_derive(key, kdf, salt))
    h = hashlib.sha256()
    size = os.path.getsize(src)
    with open(src, 'rb') as fi, open(dst, 'wb') as fo:
        fo.write(MAGIC + bytes([kdf]) + salt + struct.pack('>I', CHUNK))
        idx, done = 0, 0
        while True:
            piece = fi.read(CHUNK)
            done += len(piece)
            last = done >= size
            h.update(piece)
            nonce = os.urandom(12)
            ct = aes.encrypt(nonce, piece, struct.pack('>QB', idx, 1 if last else 0))
            fo.write(nonce + struct.pack('>I', len(ct)) + ct)
            idx += 1
            if last:
                break
    return h.hexdigest()


def decrypt_stream(src, key, out=None):
    """Decrypt src; write to `out` (a file object) when given. Returns the SHA-256 of the plain content. Raises on any damage
    or a wrong key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    h = hashlib.sha256()
    with open(src, 'rb') as fi:
        head = fi.read(len(MAGIC) + 1 + 16 + 4)
        if not head.startswith(MAGIC):
            raise ValueError('not a ZEO encrypted backup file')
        kdf = head[len(MAGIC)]
        salt = head[len(MAGIC) + 1:len(MAGIC) + 17]
        aes = AESGCM(_derive(key, kdf, salt))
        idx, last = 0, False
        while True:
            hdr = fi.read(16)
            if not hdr:
                break
            if len(hdr) < 16:
                raise ValueError('the file is cut short')
            nonce, n = hdr[:12], struct.unpack('>I', hdr[12:])[0]
            ct = fi.read(n)
            if len(ct) < n:
                raise ValueError('the file is cut short')
            plain = None
            for flag in (0, 1):
                try:
                    plain = aes.decrypt(nonce, ct, struct.pack('>QB', idx, flag))
                    last = bool(flag)
                    break
                except InvalidTag:
                    continue
            if plain is None:
                raise ValueError('wrong key, or the file is damaged')
            h.update(plain)
            if out is not None:
                out.write(plain)
            idx += 1
            if last:
                if fi.read(1):
                    raise ValueError('data after the last piece')
                break
        if not last:
            raise ValueError('the file is cut short (the last piece is missing)')
    return h.hexdigest()
