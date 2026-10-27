"""
v1.54.0 – single sign-on (SSO): Microsoft Entra ID, Google Workspace, any OpenID Connect provider and SAML 2.0.

Flow (the browser goes to the provider and back):
  login page  →  GET  /users/sso/discover/?email=          which provider (by e-mail domain), is a password still allowed
              →  GET  /users/sso/start/<id>/?app=&back=    the address of the provider's sign-in (state kept 10 minutes)
  provider    →  GET  /users/sso/oidc/callback/            code → id_token checked (signature, issuer, audience, nonce, expiry)
              →  POST /users/sso/saml/acs/<id>/            signed SAML response checked (signature, audience, recipient, time, request id)
              →  redirect to the app: <app><back>?sso=<ticket>   (or ?sso_error=<message>)
  login page  →  POST /users/sso/exchange/ {ticket}         the usual sign-in answer (tokens, companies …) – the ticket works once, 2 minutes

Safety rules:
  * the e-mail must be in one of the provider's domains; public mail domains (gmail.com …) cannot be claimed;
  * ZEO trusts a provider only for users whose companies are ALL covered by it – a user of another company keeps the password;
  * superusers and platform administrators never sign in with SSO;
  * a provider is changed only by an administrator of every company it covers.
"""
import base64
import hashlib
import logging
import re
import secrets
import urllib.parse
import uuid
import zlib
from datetime import datetime, timedelta, timezone as dt_tz

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)
U = get_user_model()

STATE_TTL = 600
TICKET_TTL = 120
SKEW = 180
PUBLIC_DOMAINS = {'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'msn.com', 'yahoo.com', 'ymail.com', 'icloud.com',
                  'me.com', 'aol.com', 'proton.me', 'protonmail.com', 'gmx.com', 'mail.com', 'zoho.com', 'yandex.com', 'rediffmail.com'}
DOMAIN_RE = re.compile(r'^(?=.{3,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')
GOOGLE_DISCOVERY = 'https://accounts.google.com/.well-known/openid-configuration'
DEVICE = '#device'   # 'back' of a sign-in started by the phone app


class SSOError(Exception):
    """A message for the person signing in."""


# ------------------------------------------------------------------ secrets
def enc(plain):
    from AISettings.service import encrypt_key
    return encrypt_key(plain)


def dec(token):
    from AISettings.service import decrypt_key
    return decrypt_key(token) or ''


# ------------------------------------------------------------------ providers
def domain_of(email):
    e = (email or '').strip().lower()
    return e.rsplit('@', 1)[1] if '@' in e else ''


def provider_for(email):
    from .models import SSOProvider
    d = domain_of(email)
    if not d:
        return None
    for p in SSOProvider.objects.filter(enabled=True):
        if d in [x.lower() for x in p.domains or []]:
            return p
    return None


def clean_domains(values, own=None):
    """Lower-case domains; refuses public mail domains and domains of another provider. Returns (domains, error)."""
    from .models import SSOProvider
    out = []
    for v in values or []:
        d = str(v).strip().lower().lstrip('@')
        if not d:
            continue
        if not DOMAIN_RE.match(d):
            return None, f'"{d}" is not an e-mail domain (example: company.ae).'
        if d in PUBLIC_DOMAINS:
            return None, f'{d} is a public mail service – only your company\'s own domain can sign in with SSO.'
        if d not in out:
            out.append(d)
    if not out:
        return None, 'Enter the e-mail domain of your company (example: company.ae).'
    for p in SSOProvider.objects.exclude(pk=getattr(own, 'pk', None)):
        clash = set(out) & {x.lower() for x in p.domains or []}
        if clash:
            return None, f'{", ".join(sorted(clash))} already signs in with "{p.name}".'
    return out, None


def is_platform_admin(user):
    try:
        from .models import PlatformAdmin
        return PlatformAdmin.objects.filter(user=user).exists()
    except Exception:
        return False


def may_use(provider, user):
    """(True, '') or (False, why): the provider may sign this user in."""
    if user.is_superuser or is_platform_admin(user):
        return False, 'Administrators of the server sign in with their password.'
    if not user.is_active:
        return False, 'Your login is switched off. Ask your HR team.'
    mine = set(user.tenants.values_list('schema_name', flat=True))
    if not mine:
        return False, 'Your login has no company yet. Ask your HR team.'
    covered = set(provider.schemas or [])
    if not mine <= covered:
        return False, 'Your login also has companies that do not use this sign-in. Sign in with your password.'
    return True, ''


def password_blocked(user):
    """The provider that enforces SSO for this user's e-mail (password sign-in refused), or None."""
    if user is None or user.is_superuser or is_platform_admin(user):
        return None
    p = provider_for(user.email)
    if p is None or not p.enforce or p.kind == 'ldap':   # LDAP: the password is typed in ZEO and checked by the domain controller
        return None
    ok, _ = may_use(p, user)
    return p if ok else None


# ------------------------------------------------------------------ addresses
def api_base(request):
    base = getattr(settings, 'ZEO_PUBLIC_API_URL', '') or ''
    import os
    base = base or os.environ.get('ZEO_PUBLIC_API_URL', '')
    if base:
        return base.rstrip('/')
    proto = request.META.get('HTTP_X_FORWARDED_PROTO') or ('https' if request.is_secure() else 'http')
    host = request.META.get('HTTP_X_FORWARDED_HOST') or request.get_host()
    return f'{proto.split(",")[0].strip()}://{host.split(",")[0].strip()}'


def redirect_uri(request):
    return api_base(request) + '/users/sso/oidc/callback/'


def acs_url(request, p):
    return api_base(request) + f'/users/sso/saml/acs/{p.pk}/'


def sp_entity_id(request, p):
    return api_base(request) + f'/users/sso/saml/metadata/{p.pk}/'


def allowed_app(origin, request):
    """The app address the browser comes back to must be ZEO's own (ZEO_APP_URL, the allowed origins or the API host)."""
    o = (origin or '').rstrip('/')
    if not re.match(r'^https?://[^/\s]+$', o):
        return None
    allowed = {x.rstrip('/') for x in getattr(settings, 'CORS_ALLOWED_ORIGINS', []) or []}
    app = (getattr(settings, 'ZEO_APP_URL', '') or '').rstrip('/')
    if app:
        p = urllib.parse.urlparse(app)
        allowed.add(f'{p.scheme}://{p.netloc}')
    allowed.add(api_base(request))
    return o if o in allowed else None


# ------------------------------------------------------------------ the phone app (no address of its own to come back to)
def device_new(url):
    """A sign-in started by the phone app: a secret poll id for the app, a short code to compare in the browser."""
    poll = secrets.token_urlsafe(32)
    code = ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(4))
    cache.set('zeo-sso-dev:' + poll, {'code': code, 'ticket': ''}, STATE_TTL)
    cache.set('zeo-sso-devurl:' + hashlib.sha256(url.encode()).hexdigest(), poll, STATE_TTL)
    return {'poll': poll, 'code': code}


def device_get(url_hash):
    poll = cache.get('zeo-sso-devurl:' + (url_hash or ''))
    d = cache.get('zeo-sso-dev:' + poll) if poll else None
    return dict(d, poll=poll) if d else None


def device_done(poll, ticket_):
    d = cache.get('zeo-sso-dev:' + poll)
    if d is not None:
        cache.set('zeo-sso-dev:' + poll, dict(d, ticket=ticket_), TICKET_TTL)


def device_poll(poll):
    d = cache.get('zeo-sso-dev:' + (poll or ''))
    if d is None:
        return None, None
    if d.get('ticket'):
        cache.delete('zeo-sso-dev:' + poll)
        return d, d['ticket']
    return d, None


def back_to(app, back, **q):
    path = back if isinstance(back, str) and back.startswith('/') and not back.startswith('//') else '/login'
    return f'{app}{path}{"&" if "?" in path else "?"}{urllib.parse.urlencode(q)}'


# ------------------------------------------------------------------ OpenID Connect
def discovery(p):
    if p.kind == 'microsoft':
        if not p.tenant:
            raise SSOError('Enter the directory (tenant) ID of your Microsoft Entra ID.')
        return f'https://login.microsoftonline.com/{urllib.parse.quote(p.tenant.strip())}/v2.0/.well-known/openid-configuration'
    if p.kind == 'google':
        return GOOGLE_DISCOVERY
    if not p.discovery_url:
        raise SSOError('Enter the discovery address of the provider (…/.well-known/openid-configuration).')
    return p.discovery_url


def oidc_config(p):
    url = discovery(p)
    key = 'zeo-sso-disc:' + hashlib.sha256(url.encode()).hexdigest()
    conf = cache.get(key)
    if conf is None:
        import requests
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            conf = r.json()
        except Exception as e:
            raise SSOError(f'The sign-in provider could not be reached ({str(e)[:120]}).')
        for k in ('issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            if not conf.get(k):
                raise SSOError('The provider\'s discovery document has no ' + k + '.')
        cache.set(key, conf, 3600)
    return conf


def oidc_start(p, request, app, back, email=''):
    conf = oidc_config(p)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    q = {'client_id': p.client_id, 'response_type': 'code', 'redirect_uri': redirect_uri(request), 'scope': 'openid email profile',
         'state': state, 'nonce': nonce, 'code_challenge': challenge, 'code_challenge_method': 'S256', 'response_mode': 'query'}
    if email:
        q['login_hint'] = email
    if p.kind == 'google' and p.domains:
        q['hd'] = p.domains[0]
    url = conf['authorization_endpoint'] + ('&' if '?' in conf['authorization_endpoint'] else '?') + urllib.parse.urlencode(q)
    cache.set('zeo-sso-state:' + state, {'p': p.pk, 'nonce': nonce, 'verifier': verifier, 'app': app, 'back': back,
                                          'redirect': redirect_uri(request), 'device_url': hashlib.sha256(url.encode()).hexdigest()}, STATE_TTL)
    return url


def _jwks_key(conf, token):
    import jwt
    client = jwt.PyJWKClient(conf['jwks_uri'], cache_keys=True, timeout=10)
    return client.get_signing_key_from_jwt(token).key


def oidc_finish(state, code):
    """(provider, e-mail, state data) from the provider's answer. Raises SSOError."""
    import jwt
    import requests
    from .models import SSOProvider
    st = cache.get('zeo-sso-state:' + (state or ''))
    if not st:
        raise SSOError('The sign-in took too long or was already used. Start again.')
    cache.delete('zeo-sso-state:' + state)
    p = SSOProvider.objects.filter(pk=st['p'], enabled=True).first()
    if p is None:
        raise SSOError('This sign-in is switched off.')
    conf = oidc_config(p)
    data = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': st['redirect'], 'client_id': p.client_id,
            'client_secret': dec(p.client_secret), 'code_verifier': st['verifier']}
    try:
        r = requests.post(conf['token_endpoint'], data=data, timeout=15, headers={'Accept': 'application/json'})
        body = r.json()
    except Exception as e:
        raise SSOError(f'The sign-in provider did not answer ({str(e)[:100]}).')
    if r.status_code != 200 or not body.get('id_token'):
        raise SSOError('The sign-in provider refused: ' + str(body.get('error_description') or body.get('error') or r.status_code)[:200])
    tok = body['id_token']
    try:
        key = _jwks_key(conf, tok)
        claims = jwt.decode(tok, key=key, algorithms=['RS256', 'RS384', 'RS512', 'ES256', 'PS256'], audience=p.client_id,
                            issuer=conf['issuer'], leeway=SKEW, options={'require': ['exp', 'iat', 'iss', 'aud']})
    except Exception as e:
        raise SSOError(f'The sign-in answer could not be verified ({str(e)[:120]}).')
    if claims.get('nonce') != st['nonce']:
        raise SSOError('The sign-in answer does not belong to this sign-in. Start again.')
    if p.kind == 'microsoft':
        email = claims.get('email') or claims.get('preferred_username') or claims.get('upn')
    else:
        email = claims.get('email')
        if p.kind == 'google' and (claims.get('hd') or '').lower() not in [d.lower() for d in p.domains or []]:
            raise SSOError('This Google account is not an account of your company.')
        if claims.get('email_verified') in (False, 'false'):
            raise SSOError('The e-mail of this account is not verified by the provider.')
    return p, (email or '').strip().lower(), st


# ------------------------------------------------------------------ SAML 2.0
NS = {'samlp': 'urn:oasis:names:tc:SAML:2.0:protocol', 'saml': 'urn:oasis:names:tc:SAML:2.0:assertion',
      'ds': 'http://www.w3.org/2000/09/xmldsig#'}
EMAIL_ATTRS = ('email', 'mail', 'emailaddress', 'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress',
               'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn', 'urn:oid:0.9.2342.19200300.100.1.3', 'upn')


def pem(cert):
    c = (cert or '').strip()
    if not c:
        return ''
    if 'BEGIN CERTIFICATE' not in c:
        body = re.sub(r'\s+', '', c)
        c = '-----BEGIN CERTIFICATE-----\n' + '\n'.join(body[i:i + 64] for i in range(0, len(body), 64)) + '\n-----END CERTIFICATE-----'
    return c


def cert_ok(cert):
    try:
        from cryptography import x509
        x509.load_pem_x509_certificate(pem(cert).encode())
        return True
    except Exception:
        return False


def saml_start(p, request, app, back, email=''):
    if not (p.idp_sso_url and p.idp_cert):
        raise SSOError('Enter the sign-in address and the certificate of the identity provider.')
    rid = '_' + uuid.uuid4().hex
    now = datetime.now(dt_tz.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    xml = (f'<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
           f'ID="{rid}" Version="2.0" IssueInstant="{now}" Destination="{_x(p.idp_sso_url)}" '
           f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" AssertionConsumerServiceURL="{_x(acs_url(request, p))}">'
           f'<saml:Issuer>{_x(sp_entity_id(request, p))}</saml:Issuer>'
           f'<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/></samlp:AuthnRequest>')
    deflated = zlib.compress(xml.encode())[2:-4]
    relay = secrets.token_urlsafe(24)
    q = {'SAMLRequest': base64.b64encode(deflated).decode(), 'RelayState': relay}
    if email:
        q['login_hint'] = email
    url = p.idp_sso_url + ('&' if '?' in p.idp_sso_url else '?') + urllib.parse.urlencode(q)
    cache.set('zeo-sso-saml:' + relay, {'p': p.pk, 'rid': rid, 'app': app, 'back': back, 'acs': acs_url(request, p),
                                        'entity': sp_entity_id(request, p), 'device_url': hashlib.sha256(url.encode()).hexdigest()}, STATE_TTL)
    return url


def _x(s):
    return str(s).replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;')


def _time(s):
    return datetime.strptime(re.sub(r'\.\d+', '', s.strip()).replace('Z', ''), '%Y-%m-%dT%H:%M:%S').replace(tzinfo=dt_tz.utc)


def saml_finish(p, saml_response, relay):
    """(e-mail, state data) from a POSTed SAMLResponse. Raises SSOError."""
    from lxml import etree
    from signxml import XMLVerifier
    st = cache.get('zeo-sso-saml:' + (relay or ''))
    if not st or st['p'] != p.pk:
        raise SSOError('The sign-in took too long or was already used. Start again.')
    cache.delete('zeo-sso-saml:' + relay)
    try:
        raw = base64.b64decode(saml_response or '')
    except Exception:
        raise SSOError('The sign-in answer could not be read.')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    try:
        doc = etree.fromstring(raw, parser=parser)
    except Exception:
        raise SSOError('The sign-in answer could not be read.')
    status = doc.find('samlp:Status/samlp:StatusCode', NS)
    if status is not None and not status.get('Value', '').endswith(':Success'):
        raise SSOError('The identity provider did not sign you in (' + status.get('Value', '').rsplit(':', 1)[-1] + ').')
    if doc.get('InResponseTo') and doc.get('InResponseTo') != st['rid']:
        raise SSOError('The sign-in answer does not belong to this sign-in. Start again.')
    try:
        verified = XMLVerifier().verify(raw, x509_cert=pem(p.idp_cert), ignore_ambiguous_key_info=True)
        signed = verified.signed_xml if not isinstance(verified, list) else verified[0].signed_xml
    except Exception as e:
        raise SSOError(f'The signature of the sign-in answer is not valid ({str(e)[:100]}).')
    # only what was signed counts: the signed element is the Assertion, or the Response that holds it
    a = signed if signed.tag == '{%s}Assertion' % NS['saml'] else signed.find('saml:Assertion', NS)
    if a is None:
        raise SSOError('The sign-in answer has no signed assertion.')
    if p.idp_entity_id:
        iss = a.find('saml:Issuer', NS)
        if iss is None or (iss.text or '').strip() != p.idp_entity_id.strip():
            raise SSOError('The sign-in answer comes from another identity provider.')
    now = datetime.now(dt_tz.utc)
    cond = a.find('saml:Conditions', NS)
    if cond is not None:
        if cond.get('NotBefore') and now + timedelta(seconds=SKEW) < _time(cond.get('NotBefore')):
            raise SSOError('The sign-in answer is not valid yet – check the clock of the server.')
        if cond.get('NotOnOrAfter') and now - timedelta(seconds=SKEW) >= _time(cond.get('NotOnOrAfter')):
            raise SSOError('The sign-in answer has expired. Start again.')
        auds = [x.text.strip() for x in cond.findall('saml:AudienceRestriction/saml:Audience', NS) if x.text]
        if auds and st['entity'] not in auds:
            raise SSOError('The sign-in answer is meant for another application (audience).')
    conf = a.find('saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData', NS)
    if conf is not None:
        if conf.get('Recipient') and conf.get('Recipient') != st['acs']:
            raise SSOError('The sign-in answer was sent to another address.')
        if conf.get('NotOnOrAfter') and now - timedelta(seconds=SKEW) >= _time(conf.get('NotOnOrAfter')):
            raise SSOError('The sign-in answer has expired. Start again.')
        if conf.get('InResponseTo') and conf.get('InResponseTo') != st['rid']:
            raise SSOError('The sign-in answer does not belong to this sign-in. Start again.')
    email = ''
    want = (p.email_attribute or '').strip()
    for at in a.findall('saml:AttributeStatement/saml:Attribute', NS):
        name = at.get('Name', '')
        if (want and name == want) or (not want and name.lower() in EMAIL_ATTRS):
            v = at.find('saml:AttributeValue', NS)
            if v is not None and v.text and '@' in v.text:
                email = v.text
                break
    if not email and not want:
        nid = a.find('saml:Subject/saml:NameID', NS)
        if nid is not None and nid.text and '@' in nid.text:
            email = nid.text
    if not email:
        raise SSOError('The sign-in answer has no e-mail address.')
    return email.strip().lower(), st


def sp_metadata(request, p):
    return (f'<?xml version="1.0"?>\n<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{_x(sp_entity_id(request, p))}">'
            f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
            f'<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>'
            f'<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="{_x(acs_url(request, p))}" index="0"/>'
            f'</md:SPSSODescriptor></md:EntityDescriptor>')


# ------------------------------------------------------------------ the user
def user_for(p, email):
    """The ZEO login of the e-mail (linked or created as the provider allows). Raises SSOError."""
    if domain_of(email) not in [d.lower() for d in p.domains or []]:
        raise SSOError(f'{email} is not an e-mail of the domains that sign in with "{p.name}".')
    users = list(U.objects.filter(email__iexact=email, is_active=True))
    if len(users) > 1:
        cover = [u for u in users if may_use(p, u)[0]]
        if len(cover) != 1:
            raise SSOError('More than one login uses this e-mail. Ask your HR team to keep one.')
        users = cover
    user = users[0] if users else None
    if user is None:
        user = _employee_login(p, email)
    if user is None:
        raise SSOError(f'There is no ZEO login for {email}. Ask your HR team to give you access.')
    ok, why = may_use(p, user)
    if not ok:
        raise SSOError(why)
    return user


def _employee_login(p, email):
    """An employee with this company e-mail in a covered company: their login, or (create_users) a new self-service login."""
    from django_tenants.utils import schema_context
    from UserManagement.models import company as Company
    for schema in p.schemas or []:
        comp = Company.objects.filter(schema_name=schema).first()
        if comp is None:
            continue
        with schema_context(schema):
            from EmpManagement.models import emp_master
            from django.db.models import Q
            e = emp_master.objects.filter(Q(emp_company_email__iexact=email) | Q(emp_personal_email__iexact=email)).filter(is_active=True).first()
            if e is None:
                continue
            if e.users_id:
                u = U.objects.filter(pk=e.users_id, is_active=True).first()
                if u is not None:
                    return u
            if not p.create_users:
                return None
            base = (e.emp_code or email.split('@')[0])[:120]
            name, n = base, 2
            while U.objects.filter(username=name).exists():
                name, n = f'{base}.{schema}'[:140] if n == 2 else f'{base}.{schema}{n}'[:150], n + 1
            u = U.objects.create_user(username=name, email=email, password=secrets.token_urlsafe(24), is_ess=True)
            u.tenants.add(comp)
            emp_master.objects.filter(pk=e.pk).update(users=u, is_ess=True)
            log.info('sso: self-service login %s made for %s in %s', u.username, e.emp_code, schema)
            return u
    return None


# ------------------------------------------------------------------ tickets
def ticket(user, p):
    t = secrets.token_urlsafe(32)
    cache.set('zeo-sso-ticket:' + t, {'u': user.pk, 'p': p.pk}, TICKET_TTL)
    from .models import SSOProvider
    SSOProvider.objects.filter(pk=p.pk).update(last_used=timezone.now(), last_error='')
    return t


def redeem(t):
    key = 'zeo-sso-ticket:' + (t or '')
    d = cache.get(key)
    if not d:
        return None, None
    cache.delete(key)
    from .models import SSOProvider
    return U.objects.filter(pk=d['u'], is_active=True).first(), SSOProvider.objects.filter(pk=d['p']).first()


def note_error(p, msg):
    if p is not None:
        from .models import SSOProvider
        SSOProvider.objects.filter(pk=p.pk).update(last_error=str(msg)[:300])
