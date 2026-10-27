"""v1.61.0 – networks allowed to use a company, and sign-in alerts.

IP allow-list (Settings › Sign-in security › Networks): a company lists the IP addresses / networks its users may work from,
for staff users or for everybody. Checked
  * on every company request (IPAllowMiddleware) – a signed-in user on another network gets 403 with the address it saw;
  * at sign-in – refused only when every company of the user refuses the network (a user of two companies can still reach the
    other one).
A Django superuser (the server owner) is never refused, so a wrong list cannot lock everybody out; saving a list that would
refuse the administrator saving it is not allowed either.

Sign-in alerts: an e-mail and a row in My account › Sign-in alerts when
  * a user signs in from a network AND a device not seen in the last 180 days (not on the first sign-in);
  * wrong passwords lock the account;
  * a sign-in is refused because of the allow-list.
v1.62.0: the country is shown when the server has a free country database (ZeoCloud.geo, ZEO_GEOIP_DB).

The client address: the last X-Forwarded-For entry (the one the proxy in front of ZEO added), else REMOTE_ADDR – the first entry
is typed by the visitor and can be faked.
"""
import ipaddress
import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

log = logging.getLogger(__name__)
OPEN = ('/users/', '/core/', '/language/', '/static', '/media', '/admin', '/cloud/', '/recruitment/api/careers/', '/licence/')
POLICY_TTL = 30
KNOWN_DAYS = 180


# ------------------------------------------------------------------ addresses
def client_ip(request):
    if request is None:
        return ''
    xf = request.META.get('HTTP_X_FORWARDED_FOR', '')
    ip = xf.split(',')[-1].strip() if xf else request.META.get('REMOTE_ADDR', '')
    return (ip or '')[:60]


def parse(text):
    """(networks, problems) from one address / network per line (commas and spaces also separate; # starts a note)."""
    nets, bad = [], []
    for raw in re.split(r'[\n,;]+', str(text or '')):
        item = raw.split('#', 1)[0].strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            bad.append(item)
    return nets, bad


def allowed(ip, nets):
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    if getattr(a, 'ipv4_mapped', None):
        a = a.ipv4_mapped
    return any(a.version == n.version and a in n for n in nets)


def network_of(ip):
    """The /24 (IPv4) or /64 (IPv6) network – a home or office line keeps it when the last digits change."""
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return str(ip or '')
    return str(ipaddress.ip_network(f'{a}/{24 if a.version == 4 else 64}', strict=False))


def device_of(agent):
    ua = str(agent or '')
    if not ua:
        return 'Unknown'
    if 'zeo-mobile' in ua.lower() or 'capacitor' in ua.lower():
        app = 'ZEO app'
    else:
        app = next((n for k, n in (('Edg', 'Edge'), ('OPR', 'Opera'), ('Firefox', 'Firefox'), ('Chrome', 'Chrome'), ('Safari', 'Safari')) if k in ua), 'Browser')
    os_ = next((n for k, n in (('Windows', 'Windows'), ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'), ('Mac OS', 'Mac'),
                               ('Linux', 'Linux')) if k in ua), 'other')
    return f'{app} on {os_}'


# ------------------------------------------------------------------ the allow-list
def _policy(schema):
    k = f'zeo:netpol:{schema}'
    v = cache.get(k)
    if v is None:
        from .models import SecurityPolicy
        p = SecurityPolicy.objects.filter(schema=schema).values('ip_scope', 'ip_allowlist').first() or {'ip_scope': 'off', 'ip_allowlist': ''}
        v = (p['ip_scope'], p['ip_allowlist'])
        cache.set(k, v, POLICY_TTL)
    return v


def forget(schema):
    cache.delete(f'zeo:netpol:{schema}')


def applies(scope, user):
    if scope == 'off' or user is None or getattr(user, 'is_superuser', False):
        return False
    return scope == 'all' or (scope == 'staff' and not getattr(user, 'is_ess', False))


def refused(user, schema, ip):
    """True when the company's allow-list refuses this user on this address."""
    scope, text = _policy(schema)
    if not applies(scope, user):
        return False
    nets, _ = parse(text)
    if not nets:
        return False
    return not allowed(ip, nets)


def refused_everywhere(user, ip):
    schemas = [t.schema_name for t in user.tenants.all()] if getattr(user, 'pk', None) else []
    return bool(schemas) and all(refused(user, s, ip) for s in schemas)


def check_save(user, scope, text, ip):
    """A problem text, or None. The saver's own address must stay allowed."""
    nets, bad = parse(text)
    if bad:
        return 'Not an IP address or network: ' + ', '.join(bad[:5])
    if scope != 'off' and not nets:
        return 'List at least one address or network, or turn the allow-list off.'
    if len(nets) > 200:
        return 'At most 200 networks.'
    if scope != 'off' and applies(scope, user) and not allowed(ip, nets):
        return f'Your own address {ip} is not in the list – you would lock yourself out. Add it (or its network) first.'
    return None


class IPAllowMiddleware:
    """Refuses company requests from networks that the company's allow-list does not include."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS' or request.path.startswith(OPEN):
            return self.get_response(request)
        try:
            from django.db import connection
            schema = getattr(connection, 'schema_name', 'public')
            if schema and schema != 'public':
                scope, text = _policy(schema)
                if scope != 'off' and text.strip():
                    from .middleware import _json, _user
                    user = _user(request)
                    ip = client_ip(request)
                    if user is not None and refused(user, schema, ip):
                        from Translations.i18n import tr
                        return _json({'detail': tr('This company can only be used from its own networks. Your address {0} is not one of them.', ip),
                                      'ip_blocked': True, 'ip': ip}, 403)
        except Exception:
            log.exception('IP allow-list check')
        return self.get_response(request)


# ------------------------------------------------------------------ alerts
def _rules(user):
    from .security import policies_for
    ps = policies_for(user)
    if not ps:
        return {'alert_new_signin': True, 'alert_lock': True}
    return {'alert_new_signin': any(p.alert_new_signin for p in ps), 'alert_lock': any(p.alert_lock for p in ps)}


def _mail(user, alert):
    if not getattr(user, 'email', ''):
        return False
    from django.conf import settings
    from django.core.mail import send_mail
    when = timezone.localtime(alert.created_at).strftime('%d %b %Y %H:%M')
    try:   # v1.62.0 the country, when the server has a country database
        from .geo import country_of
        cc = country_of(alert.ip)
    except Exception:
        cc = ''
    shown = f'{alert.ip} ({cc})' if cc else alert.ip
    if alert.kind == 'new_signin':
        subj = 'ZEO: new sign-in to your account'
        body = (f'Your ZEO account {user.email} was signed in to on {when} from {alert.device}, address {shown}.\n\n'
                'If this was you, there is nothing to do. If it was not you, open My account › Sign-in alerts, choose "This was not me" '
                '(all sign-ins are ended) and change your password.')
    elif alert.kind == 'locked':
        subj = 'ZEO: your account was locked after wrong passwords'
        body = (f'Too many wrong passwords were typed for your ZEO account {user.email} (last from address {shown}, {when}). '
                'The account is locked for a few minutes.\n\nIf this was not you, change your password after the lock ends.')
    else:
        subj = 'ZEO: a sign-in from a network your company does not allow'
        body = (f'Someone signed in with your password on {when} from address {shown} ({alert.device}); your company allows ZEO '
                'only from its own networks, so the sign-in was refused.\n\nIf this was not you, change your password.')
    try:
        send_mail(subj, body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [user.email], fail_silently=False)
        return True
    except Exception:
        log.exception('sign-in alert mail')
        return False


def _alert(user, kind, ip='', agent='', sid='', schema=''):
    from .models import SignInAlert
    # at most one alert of a kind per user and address a day
    if SignInAlert.objects.filter(user=user, kind=kind, ip=ip, created_at__gte=timezone.now() - timedelta(days=1)).exists():
        return None
    a = SignInAlert.objects.create(user=user, kind=kind, ip=ip[:60], agent=(agent or '')[:255], device=device_of(agent)[:80], sid=sid, schema=schema)
    if _mail(user, a):
        SignInAlert.objects.filter(pk=a.pk).update(emailed=True)
        a.emailed = True
    return a


def after_signin(user, session):
    """Called by security.start_session for every new sign-in."""
    if session.how == 'impersonation' or session.impersonator_id:
        return None
    if not _rules(user)['alert_new_signin']:
        return None
    from .models import LoginSession
    prev = list(LoginSession.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(days=KNOWN_DAYS), impersonator__isnull=True)
                .exclude(pk=session.pk).values_list('ip', 'agent')[:500])
    if not prev:
        return None   # the first sign-in is not "new"
    net, dev = network_of(session.ip), device_of(session.agent)
    if any(network_of(i) == net for i, _ in prev) or any(device_of(a) == dev for _, a in prev):
        return None
    return _alert(user, 'new_signin', session.ip, session.agent, session.sid, session.schema)


def after_lock(user):
    if not _rules(user)['alert_lock']:
        return None
    try:
        from Chatter.tracking import current_request
        r = current_request()
    except Exception:
        r = None
    return _alert(user, 'locked', client_ip(r), r.META.get('HTTP_USER_AGENT', '') if r is not None else '')


def refused_signin(user, request):
    return _alert(user, 'blocked_ip', client_ip(request), request.META.get('HTTP_USER_AGENT', '') if request is not None else '')


def alert_json(a, with_user=False):
    try:
        from .geo import country_of
        cc = country_of(a.ip)
    except Exception:
        cc = ''
    out = {'id': a.id, 'kind': a.kind, 'kind_text': dict(a.KINDS).get(a.kind, a.kind), 'ip': a.ip, 'country': cc, 'device': a.device,
           'created_at': a.created_at, 'emailed': a.emailed, 'status': a.status, 'answered_at': a.answered_at}
    if with_user:
        u = a.user
        out['user'] = (u.get_full_name() or u.username) if u else ''
        out['email'] = getattr(u, 'email', '')
    return out
