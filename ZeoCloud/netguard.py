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
        return {'alert_new_signin': True, 'alert_lock': True, 'alert_rule': 'any'}
    return {'alert_new_signin': any(p.alert_new_signin for p in ps), 'alert_lock': any(p.alert_lock for p in ps),
            # v1.67.6 (setup) item 13: the stricter rule wins – "any" (a new place, address or device) unless every company chose "both"
            'alert_rule': 'both' if all(getattr(p, 'alert_rule', 'any') == 'both' for p in ps) else 'any'}


def send_mail_for(user, subject, body, to, schema='', lang=None):
    """v1.67.6 (setup) item 13: e-mail through the company mail connection (ZeoAdmin.schedules.mail_connection – Settings ›
    E-mail of the company), as the payslip and sign-in code e-mails. A sign-in has no company yet: the company of the
    sign-in when known, else the first of the user's companies with an active e-mail configuration, else the server's."""
    from django.core.mail import EmailMessage
    from django_tenants.utils import schema_context
    from ZeoAdmin.schedules import mail_connection
    order = [schema] if schema else []
    try:
        order += [c for c in user.tenants.exclude(schema_name='public').order_by('id').values_list('schema_name', flat=True) if c not in order]
    except Exception:
        pass
    conn = sender = None
    for sch in order:
        try:
            with schema_context(sch):
                from django.apps import apps
                if apps.get_model('EmpManagement', 'EmailConfiguration').objects.filter(is_active=True).exists():
                    conn, sender = mail_connection()
                    break
        except Exception:
            continue
    if conn is None:
        conn, sender = mail_connection() if not order else _server_connection()
    if lang:   # v1.67.7 (admin): with an HTML part – right-to-left for Arabic
        from .mailtext import message
        message(subject, body, lang, sender, to, conn).send()
        return True
    EmailMessage(subject=subject, body=body, from_email=sender, to=list(to), connection=conn).send()
    return True


def _server_connection():
    from django.conf import settings
    from django.core.mail import get_connection
    return get_connection(), getattr(settings, 'DEFAULT_FROM_EMAIL', None) or getattr(settings, 'EMAIL_HOST_USER', None)


WHY_TEXT = {'place': 'a new place', 'address': 'a new address', 'device': 'a new device', 'network': 'a new network'}


def why_text(why, translate=False, lang=None):
    parts = [WHY_TEXT.get(w, w) for w in (why or '').split(',') if w]
    if translate or lang:   # v1.67.6 (setup) the screens show it in the user's language; v1.67.7 (admin) the e-mails too (lang)
        try:
            from Translations.i18n import tr
            parts = [tr(x, lang=lang) if lang else tr(x) for x in parts]
        except Exception:
            pass
    return ('، ' if lang == 'ar' else ', ').join(parts)


def _mail(user, alert):
    if not getattr(user, 'email', ''):
        return False
    from .mailtext import T, lang_of
    sch = getattr(alert, 'schema', '') or ''
    L = lang_of(user, sch)   # v1.67.7 (admin): the user's own language (Arabic …), else the company's, else English
    when = timezone.localtime(alert.created_at).strftime('%d/%m/%Y %H:%M')
    try:   # v1.62.0 the country, when the server has a country database
        from .geo import country_of
        cc = country_of(alert.ip)
    except Exception:
        cc = ''
    shown = f'{alert.ip} ({cc})' if cc else alert.ip
    from .mailtext import device_text
    dev = device_text(alert.device, L)
    if alert.kind == 'new_signin':
        subj = T('ZEO: new sign-in to your account', L)
        what = why_text(getattr(alert, 'why', ''), lang=L)   # v1.67.6 (setup) item 13
        body = (T('Your ZEO account {0} was signed in to on {1} from {2}, address {3}.', L, user.email, when, dev, shown)
                + (' ' + T('New: {0}.', L, what) if what else '') + '\n\n'
                + T('If this was you, there is nothing to do. If it was not you, open My account › Sign-in alerts, choose "This was not me" (all sign-ins are ended) and change your password.', L))
    elif alert.kind == 'locked':
        subj = T('ZEO: your account was locked after wrong passwords', L)
        body = (T('Too many wrong passwords were typed for your ZEO account {0} (last from address {1}, {2}). The account is locked for a few minutes.', L, user.email, shown, when)
                + '\n\n' + T('If this was not you, change your password after the lock ends.', L))
    else:
        subj = T('ZEO: a sign-in from a network your company does not allow', L)
        body = (T('Someone signed in with your password on {0} from address {1} ({2}); your company allows ZEO only from its own networks, so the sign-in was refused.', L, when, shown, dev)
                + '\n\n' + T('If this was not you, change your password.', L))
    try:   # v1.67.6 (setup) item 13: the company mail connection (was the server's default)
        return send_mail_for(user, subj, body, [user.email], sch, lang=L)
    except Exception:
        log.exception('sign-in alert mail')
        return False


def _alert(user, kind, ip='', agent='', sid='', schema='', why=''):
    from .models import SignInAlert
    # at most one alert of a kind per user, address and device a day (v1.67.6: and device – a new phone on a known address)
    if SignInAlert.objects.filter(user=user, kind=kind, ip=ip, device=device_of(agent)[:80], created_at__gte=timezone.now() - timedelta(days=1)).exists():
        return None
    a = SignInAlert.objects.create(user=user, kind=kind, ip=ip[:60], agent=(agent or '')[:255], device=device_of(agent)[:80], sid=sid, schema=schema,
                                   why=why[:120])
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
                .exclude(pk=session.pk).values_list('ip', 'agent', 'country')[:500])
    if not prev:
        return None   # the first sign-in is not "new"
    net, dev = network_of(session.ip), device_of(session.agent)
    rule = _rules(user).get('alert_rule', 'any')
    if rule == 'both':   # before 1.67.6: only a new network AND a new device
        if any(network_of(i) == net for i, _, _ in prev) or any(device_of(a) == dev for _, a, _ in prev):
            return None
        return _alert(user, 'new_signin', session.ip, session.agent, session.sid, session.schema, 'network,device')
    # v1.67.6 (setup) item 13: a new place (country, when the server has the free country database), address or device
    why = []
    if session.country and session.country not in {c for _, _, c in prev if c}:
        why.append('place')
    if session.ip and session.ip not in {i for i, _, _ in prev}:
        why.append('address')
    if dev not in {device_of(a) for _, a, _ in prev}:
        why.append('device')
    if not why:
        return None
    return _alert(user, 'new_signin', session.ip, session.agent, session.sid, session.schema, ','.join(why))


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
           'created_at': a.created_at, 'emailed': a.emailed, 'status': a.status, 'answered_at': a.answered_at,
           'why': getattr(a, 'why', ''), 'why_text': why_text(getattr(a, 'why', ''), True)}   # v1.67.6 (setup)
    if with_user:
        u = a.user
        out['user'] = (u.get_full_name() or u.username) if u else ''
        out['email'] = getattr(u, 'email', '')
    return out
