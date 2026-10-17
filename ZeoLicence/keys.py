"""v1.28.0 – Adept's public keys for Enterprise licence files.

Adept makes its key pair once, on the server where it issues licences (the ZEO Cloud console):
    python manage.py zeo_licence keys --generate --out /etc/zeo/licence_private.pem
That prints the public key. Put it in ADEPT_PUBLIC_KEYS below (key id → public key) before an Enterprise package is
handed to a client, and set ZEO_LICENCE_PRIVATE_KEY=/etc/zeo/licence_private.pem in that server's .env. The private
key never leaves that server and is never in a package. A server may also add keys with ZEO_LICENCE_PUBLIC_KEYS
(id:key,id:key) in its .env.

TEST_KEYS are accepted only with ZEO_LICENCE_ACCEPT_TEST=true (Adept's test servers).
"""
ADEPT_PUBLIC_KEYS = {
    # 'adept-2026': '<public key printed by zeo_licence keys --generate>',
}

TEST_KEYS = {
    'test-2026': 'inMFelpEutGoqj1cfZ2/+M9d4ubnwRbxAqYsnCMBzjI=',
}
