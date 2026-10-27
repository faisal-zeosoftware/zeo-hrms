"""v1.62.0 – the country of an address, and the companies' country rules (Settings › Sign-in security › Countries).

The country comes from a FREE database file on the server (no paid service, nothing sent outside):
  * DB-IP "IP to Country Lite" (CSV, CC BY 4.0 – https://db-ip.com/db/download/ip-to-country-lite), plain or .gz; or
  * MaxMind GeoLite2-Country (.mmdb, free account) – needs the Python package `maxminddb`.
Set ZEO_GEOIP_DB=/path/to/file in .env and update the file monthly. Without the file the rules cannot be checked: they stay
inactive (the page says so) and nobody is refused. Private and local addresses have no country and are never refused.

Rule: a company allows a list of countries (e.g. AE, SA, OM) for its staff users or for everybody; a sign-in or a request
from another country is refused (403) and written as a security event (the administrators are told).
"""
import bisect
import csv
import gzip
import ipaddress
import logging
import os
import threading

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)
_lock = threading.Lock()
_db = {'path': None, 'kind': None, 'v4': None, 'v6': None, 'reader': None, 'rows': 0, 'error': ''}
OPEN = ('/users/', '/core/', '/language/', '/static', '/media', '/admin', '/cloud/', '/recruitment/api/careers/', '/licence/')


def db_path():
    return str(getattr(settings, 'ZEO_GEOIP_DB', '') or os.environ.get('ZEO_GEOIP_DB', '') or '')


def _load():
    path = db_path()
    if _db['path'] == path:
        return
    with _lock:
        if _db['path'] == path:
            return
        _db.update(path=path, kind=None, v4=None, v6=None, reader=None, rows=0, error='')
        if not path:
            return
        if not os.path.exists(path):
            _db['error'] = f'file not found: {path}'
            return
        try:
            if path.endswith('.mmdb'):
                import maxminddb
                _db['reader'] = maxminddb.open_database(path)
                _db['kind'] = 'mmdb'
                return
            opener = gzip.open if path.endswith('.gz') else open
            v4s, v4e, v4c, v6s, v6e, v6c = [], [], [], [], [], []
            with opener(path, 'rt', encoding='utf-8', newline='') as fh:
                for row in csv.reader(fh):
                    if len(row) < 3:
                        continue
                    try:
                        a, b = ipaddress.ip_address(row[0].strip()), ipaddress.ip_address(row[1].strip())
                    except ValueError:
                        continue
                    cc = row[2].strip().upper()[:2]
                    if a.version == 4:
                        v4s.append(int(a)); v4e.append(int(b)); v4c.append(cc)
                    else:
                        v6s.append(int(a)); v6e.append(int(b)); v6c.append(cc)
            for s_, e_, c_ in ((v4s, v4e, v4c), (v6s, v6e, v6c)):
                order = sorted(range(len(s_)), key=s_.__getitem__)
                s_[:], e_[:], c_[:] = [s_[i] for i in order], [e_[i] for i in order], [c_[i] for i in order]
            _db.update(kind='csv', v4=(v4s, v4e, v4c), v6=(v6s, v6e, v6c), rows=len(v4s) + len(v6s))
        except Exception as e:
            log.exception('country database')
            _db['error'] = str(e)[:200]


def status():
    _load()
    return {'file': db_path(), 'ready': bool(_db['kind']), 'kind': _db['kind'] or '', 'rows': _db['rows'], 'error': _db['error']}


def country_of(ip):
    """'AE', 'SA' … or '' (unknown, private address, no database)."""
    if not ip:
        return ''
    try:
        a = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return ''
    if getattr(a, 'ipv4_mapped', None):
        a = a.ipv4_mapped
    if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast:
        return ''
    _load()
    if not _db['kind']:
        return ''
    k = f'zeo:cc:{a}'
    v = cache.get(k)
    if v is not None:
        return v
    cc = ''
    try:
        if _db['kind'] == 'mmdb':
            rec = _db['reader'].get(str(a)) or {}
            cc = ((rec.get('country') or rec.get('registered_country') or {}).get('iso_code') or '').upper()
        else:
            s_, e_, c_ = _db['v4'] if a.version == 4 else _db['v6']
            n = int(a)
            i = bisect.bisect_right(s_, n) - 1
            if i >= 0 and s_[i] <= n <= e_[i]:
                cc = c_[i]
    except Exception:
        log.exception('country lookup')
    cc = '' if cc in ('ZZ', '--') else cc
    cache.set(k, cc, 3600)
    return cc


def parse_codes(text):
    import re
    return sorted({c.upper() for c in re.split(r'[\s,;]+', str(text or '')) if re.fullmatch(r'[A-Za-z]{2}', c)})


def _policy(schema):
    k = f'zeo:ccpol:{schema}'
    v = cache.get(k)
    if v is None:
        from .models import SecurityPolicy
        p = SecurityPolicy.objects.filter(schema=schema).values('country_scope', 'country_allow').first() or {'country_scope': 'off', 'country_allow': ''}
        v = (p['country_scope'], parse_codes(p['country_allow']))
        cache.set(k, v, 30)
    return v


def forget(schema):
    cache.delete(f'zeo:ccpol:{schema}')


def refused(user, schema, ip):
    """The country the address is in, when the company's rule refuses it; '' otherwise."""
    scope, codes = _policy(schema)
    from .netguard import applies
    if not codes or not applies(scope, user):
        return ''
    cc = country_of(ip)
    return cc if cc and cc not in codes else ''


def refused_everywhere(user, ip):
    schemas = [t.schema_name for t in user.tenants.all()] if getattr(user, 'pk', None) else []
    if not schemas:
        return ''
    found = [refused(user, s, ip) for s in schemas]
    return found[0] if all(found) else ''


class CountryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS' or request.path.startswith(OPEN):
            return self.get_response(request)
        try:
            from django.db import connection
            schema = getattr(connection, 'schema_name', 'public')
            if schema and schema != 'public':
                scope, codes = _policy(schema)
                if scope != 'off' and codes:
                    from .middleware import _json, _user
                    from .netguard import client_ip
                    user = _user(request)
                    ip = client_ip(request)
                    cc = refused(user, schema, ip) if user is not None else ''
                    if cc:
                        from Translations.i18n import tr
                        return _json({'detail': tr('This company can only be used from {0}. Your address {1} is in {2}.', ', '.join(codes), ip, cc),
                                      'country_blocked': True, 'country': cc, 'ip': ip}, 403)
        except Exception:
            log.exception('country check')
        return self.get_response(request)
