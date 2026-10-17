"""v1.28.0 – licence keys and files from the command line.

  python manage.py zeo_licence keys --generate --out /etc/zeo/licence_private.pem   Adept, once (prints the public key)
  python manage.py zeo_licence show                                                 this server: installation ID and status
  python manage.py zeo_licence verify FILE                                          check a .zeolic file
"""
import base64
import json
import os

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'ZEO Enterprise licence: make Adept keys, show this installation, verify a licence file.'

    def add_arguments(self, p):
        p.add_argument('what', choices=['keys', 'show', 'verify'])
        p.add_argument('file', nargs='?')
        p.add_argument('--generate', action='store_true')
        p.add_argument('--out')

    def handle(self, *a, **o):
        if o['what'] == 'keys':
            if not o['generate'] or not o['out']:
                raise CommandError('Use: zeo_licence keys --generate --out /secure/path/licence_private.pem')
            if os.path.exists(o['out']):
                raise CommandError(f'{o["out"]} exists – keep it; a new key needs a new key id in ZeoLicence/keys.py.')
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            k = Ed25519PrivateKey.generate()
            fd = os.open(o['out'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            pub = base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
            self.stdout.write(f'Private key written to {o["out"]} (mode 600) – keep it on this server only, back it up safely.')
            self.stdout.write(f"Add to ZeoLicence/keys.py ADEPT_PUBLIC_KEYS:  'adept-2026': '{pub}',")
            self.stdout.write(f'And in this server\'s .env:  ZEO_LICENCE_PRIVATE_KEY={o["out"]}')
            return
        from ZeoLicence import licence as L
        if o['what'] == 'show':
            st = L.current(fresh=True)
            self.stdout.write(json.dumps({k: v for k, v in st.items()}, indent=1, default=str))
            return
        if not o['file']:
            raise CommandError('Give the licence file.')
        from ZeoLicence.signing import verify
        with open(o['file'], encoding='utf-8') as f:
            payload, kid = verify(f.read())
        self.stdout.write(f'Signed by key {kid}:')
        self.stdout.write(json.dumps(payload, indent=1))
