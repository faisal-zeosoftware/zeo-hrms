"""v1.67.26 (pending item 1.62.12, item 237) – a company's own address (custom domain), e.g. https://hr.acme-trading.ae.

Settings › Company › Custom domain (company administrators):
  1. enter the address (hr.acme-trading.ae);
  2. at the domain's DNS provider add a CNAME record:  hr.acme-trading.ae  →  the ZEO server's host name (shown on the page; it
     is the host of ZEO_APP_URL) – or an A record to the server's IP address;
  3. press Check: ZEO looks the name up and compares it with the server; when they match the address is "verified" and saved as
     the company's domain (UserManagement.Domain – the table django-tenants already has; no new table, no migration).
A verified address opens the company straight away: the sign-in page shows the company and, after sign-in, opens it without the
company list (users with several companies can still switch from the top bar).

The server administrator adds the address once to the web server (nginx server_name, a free Let's Encrypt certificate with
certbot) and to ZEO_ALLOWED_HOSTS / ZEO_CSRF_TRUSTED_ORIGINS in .env – see README-UPDATE "Custom domain". Nothing is bought.
"""
import re
import socket
from urllib.parse import urlparse

from django.conf import settings
from django.db import connection
from django.utils import timezone

KEY = 'custom_domain'
HOST_RE = re.compile(r'^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def server_host():
    u = urlparse(getattr(settings, 'ZEO_APP_URL', '') or '')
    return (u.hostname or '').lower()


def _opt():
    from django.apps import apps
    CO = apps.get_model('ZeoApps', 'CompanyOption')
    o = CO.objects.filter(key=KEY).first()
    return CO, dict(o.value or {}) if o else {}


def view():
    _co, v = _opt()
    return {**v, 'server_host': server_host(), 'cname_target': server_host() or _tr('the host name of your ZEO server'),
            'url': f'https://{v["host"]}' if v.get('host') and v.get('status') == 'verified' else ''}


def _resolve(host):
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except (socket.gaierror, UnicodeError, OSError):
        return []


def set_host(host):
    host = (host or '').strip().lower().rstrip('.')
    host = re.sub(r'^https?://', '', host).split('/')[0]
    CO, v = _opt()
    if not host:
        _drop(v.get('host'))
        CO.objects.update_or_create(key=KEY, defaults={'value': {}})
        return view()
    if not HOST_RE.match(host):
        raise ValueError(_tr('Enter a host name such as hr.yourcompany.com (letters, digits, hyphens and dots).'))
    if host == server_host() or host in ('localhost',) or host.endswith('.localhost'):
        raise ValueError(_tr('Enter your company\'s own address, not the ZEO server\'s.'))
    from UserManagement.models import Domain
    other = Domain.objects.filter(domain=host).exclude(tenant__schema_name=connection.schema_name).first()
    if other is not None:
        raise ValueError(_tr('This address is already used by another company.'))
    if v.get('host') and v['host'] != host:
        _drop(v['host'])
    v = {'host': host, 'status': 'pending', 'set_at': timezone.now().isoformat(), 'message': _tr('Add the DNS record, then press Check.')}
    CO.objects.update_or_create(key=KEY, defaults={'value': v})
    return view()


def _drop(host):
    if not host:
        return
    from UserManagement.models import Domain
    Domain.objects.filter(domain=host, tenant__schema_name=connection.schema_name, is_primary=False).delete()


def check():
    CO, v = _opt()
    host = v.get('host')
    if not host:
        raise ValueError(_tr('Enter the address first.'))
    mine, srv = _resolve(host), server_host()
    target = _resolve(srv) if srv else []
    v['checked_at'] = timezone.now().isoformat()
    v['resolves_to'] = mine
    if not mine:
        v['status'], v['message'] = 'pending', _tr('{0} is not found in DNS yet – changes can take up to a few hours.', host)
    elif target and set(mine) & set(target):
        from UserManagement.models import Domain, company
        t = company.objects.get(schema_name=connection.schema_name)
        Domain.objects.get_or_create(domain=host, defaults={'tenant': t, 'is_primary': False})
        v['status'], v['message'], v['verified_at'] = 'verified', _tr('Verified – {0} points to this ZEO server.', host), timezone.now().isoformat()
    else:
        v['status'] = 'pending'
        v['message'] = _tr('{0} points to {1}, not to the ZEO server ({2}). Correct the DNS record.', host, ', '.join(mine), ', '.join(target) or srv or '?')
    CO.objects.update_or_create(key=KEY, defaults={'value': v})
    return view()


def company_for_host(host):
    """Public: the company whose verified address this is (the sign-in page asks with location.hostname)."""
    host = (host or '').strip().lower().rstrip('.')
    if not HOST_RE.match(host or 'x'):
        return None
    from UserManagement.models import Domain
    d = Domain.objects.select_related('tenant').filter(domain=host, is_primary=False).first()
    if d is None or d.tenant.schema_name == 'public':
        return None
    return {'schema': d.tenant.schema_name, 'name': d.tenant.name}
