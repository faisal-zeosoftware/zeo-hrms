"""v1.14.0 – Country packs: the resolved pack of the company and the helpers other apps use.

Other apps import these lazily and fall back to today's UAE behaviour when the app is not installed:

    try:
        from CountryPack.services import feature
        on = feature('wps_sif')
    except Exception:
        on = True

current_pack(schema_name=None) -> dict      the resolved pack (keys fixed by CONTRACT14)
label(key) / feature(key) / pack_code()    one caption / one switch / the pack code
is_national(nationality_name) -> bool      a national of the pack's country (Emirati for AE)
gratuity_amount(years, basic, termination, branch_id) -> Decimal   by the branch's country rule set
"""
import logging
import re
import time
from decimal import Decimal
from fractions import Fraction

from django.apps import apps
from django.core.cache import cache
from django.db import connection

from .packs import (AE_LABELS, CURRENCY_DECIMALS, FEATURE_KEYS, LABEL_KEYS, NEUTRAL_LEAVE_TYPES, PACK_ORDER, PACKS, pack_for_code)
from Translations.i18n import tr  # v1.15.0 language packs: messages in the user's language

log = logging.getLogger(__name__)
M = apps.get_model

CACHE_TTL = 300          # shared cache (redis in production)
LOCAL_TTL = 10           # per-process memo, so a report that asks for every employee does not go to the cache each time
_local = {}              # key -> (expires, value)
WPS_SIF_OFF = "The WPS SIF file is for UAE companies. Turn on 'WPS SIF' in Settings → Country settings if you need it."
TERMINATION_RESIGN = {'resignation'}


# ------------------------------------------------------------------ cache
def _schema(schema_name=None):
    return schema_name or getattr(connection, 'schema_name', None) or 'public'


def _ck(schema):
    return f'countrypack:v1:{schema}'


def clear_cache(schema_name=None):
    schema = _schema(schema_name)
    for k in [k for k in _local if k[0] == schema]:
        _local.pop(k, None)
    try:
        cache.delete(_ck(schema))
    except Exception:  # cache down: the local memo is cleared, the shared entry expires by itself
        log.warning('country pack cache could not be cleared for %s', schema)


def _memo(key, fn, ttl=LOCAL_TTL):
    hit = _local.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    v = fn()
    _local[key] = (now + ttl, v)
    return v


def on_company_saved(sender, instance, **kwargs):
    """post_save of UserManagement.company: the country (or currency) may have changed."""
    if getattr(instance, 'schema_name', None):
        clear_cache(instance.schema_name)


# ------------------------------------------------------------------ the company and its settings row
def _company(schema):
    C = M('UserManagement', 'company')
    return C.objects.select_related('country', 'currency').filter(schema_name=schema).first()


def _in_schema(schema, fn):
    if getattr(connection, 'schema_name', None) == schema:
        return fn()
    from django_tenants.utils import schema_context
    with schema_context(schema):
        return fn()


def _settings_row(schema):
    def get():
        try:
            S = M('CountryPack', 'CountryPackSettings')
            return S.objects.order_by('id').first()
        except Exception:  # table not migrated yet (company being created)
            return None
    return _in_schema(schema, get)


def _has_gratuity_table(schema):
    def get():
        try:
            return M('OrganisationManager', 'GratuityTable').objects.filter(is_active=True).exists()
        except Exception:
            return False
    return _in_schema(schema, get)


# ------------------------------------------------------------------ rule sets (OrgStructure country policies)
def _norm_policy(p):
    return {'id': p.pk, 'code': p.code, 'name': p.name, 'country_code': (p.country_code or '').upper(), 'weekend_days': list(p.weekend_days or []),
            'gratuity': dict(p.gratuity or {}), 'annual_leave': dict(p.annual_leave or {})}


def _seed(country_code):
    try:
        from OrgStructure.seed import STANDARD
    except Exception:
        return None
    for d in STANDARD:
        if d['country_code'] == (country_code or '').upper():
            return d
    return None


def _norm_seed(d):
    return {'id': None, 'code': d['code'], 'name': d['name'], 'country_code': d['country_code'], 'weekend_days': list(d['weekend_days']),
            'gratuity': dict(d['gratuity']), 'annual_leave': dict(d['annual_leave'])}


def _country_rule(country_code):
    """The active rule set of a country in this company (standard first), else the standard GCC set from the code, else None."""
    cc = (country_code or '').upper()[:2]
    if len(cc) != 2:
        return None
    try:
        CP = M('OrgStructure', 'CountryPolicy')
        p = CP.objects.filter(active=True, country_code=cc).order_by('-is_standard', 'id').first()
        if p is not None:
            return _norm_policy(p)
    except LookupError:
        pass
    d = _seed(cc)
    return _norm_seed(d) if d else None


def rule_set_for_branch(branch_id=None, schema_name=None):
    """Rule set in force for a branch: the one applied to the branch (OrgStructure), else the branch country's, else the
    company's (pack country). None = no rule set (neutral pack without one)."""
    schema = _schema(schema_name)

    def resolve():
        if branch_id:
            try:
                from OrgStructure.services import country_policy_for
                p = country_policy_for(branch_id)
                if p is not None:
                    return _norm_policy(p)
            except Exception:
                pass
            b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch_id).select_related('br_country').first()
            cc = (getattr(getattr(b, 'br_country', None), 'country_code', '') or '').upper()[:2] if b else ''
            if cc:
                r = _country_rule(cc)
                if r:
                    return r
        return company_rule_set(schema)
    return _memo((schema, 'rule', branch_id or 0), lambda: _in_schema(schema, resolve))


def company_rule_set(schema_name=None):
    schema = _schema(schema_name)
    p = current_pack(schema)
    cc = p['country_code'] if p['pack'] != 'GEN' else (p.get('_company_country_code') or '')
    return _in_schema(schema, lambda: _country_rule(cc))


# ------------------------------------------------------------------ the resolved pack
def _currency(comp, pack):
    cur = getattr(comp, 'currency', None) if comp else None
    if cur is None and comp is not None and comp.country_id:
        cur = M('Core', 'crncy_mstr').objects.filter(country_id=comp.country_id).order_by('id').first()
    if cur is not None:
        code = (cur.currency_code or '').upper()
        return {'code': code, 'symbol': cur.symbol or code, 'decimals': CURRENCY_DECIMALS.get(code, 2)}
    code = PACKS[pack]['currency'] or 'AED'
    return {'code': code, 'symbol': code, 'decimals': CURRENCY_DECIMALS.get(code, 2)}


def base_pack_code(comp):
    cc = (getattr(getattr(comp, 'country', None), 'country_code', '') or '') if comp else ''
    return pack_for_code(cc) if comp is not None else 'AE'


def _nationality_word(country_code):
    try:
        from Core.management.commands.load_master_data import COUNTRIES
        for row in COUNTRIES:
            if row[1] == country_code:
                return row[6]
    except Exception:
        pass
    return ''


def _resolve(schema):
    comp = _company(schema) if schema and schema != 'public' else None
    row = _settings_row(schema) if comp is not None else None
    auto = base_pack_code(comp)
    override = (getattr(row, 'pack_override', '') or '').upper()
    code = override if override in PACKS else auto
    P = PACKS[code]
    country = getattr(comp, 'country', None) if comp else None
    cc = ((getattr(country, 'country_code', '') or '').upper()[:2]) if country else 'AE'
    labels = dict(P['labels'])
    features = dict(P['features'])
    nat_words = list(P['nationality_words'])
    nat_exact = list(P['nationality_exact'])
    if code == 'GEN':
        word = _nationality_word(cc)
        if word:
            labels['nationals'] = word.title()
            labels['nationals_plural'] = f'{word.title()} nationals'
            nat_words.append(word.upper())
        if country is not None:
            nat_exact.append(country.country_name.upper())
        features['gratuity'] = _has_gratuity_table(schema) if comp is not None else False
    lo = {k: str(v) for k, v in ((getattr(row, 'label_overrides', None) or {}).items()) if k in labels and v not in (None, '')}
    fo = {k: bool(v) for k, v in ((getattr(row, 'feature_overrides', None) or {}).items()) if k in features and v is not None}
    defaults = {'labels': dict(labels), 'features': dict(features)}
    labels.update(lo)
    features.update(fo)
    out = {
        'pack': code,
        'country_code': cc if comp is not None else 'AE',
        'country_name': country.country_name if country is not None else 'United Arab Emirates',
        'currency': _currency(comp, code),
        'labels': {k: labels.get(k, '') for k in LABEL_KEYS},
        'features': {k: bool(features.get(k, False)) for k in FEATURE_KEYS},
        'rules': {'rule_set_id': None, 'rule_set_name': '', 'weekend_days': [5, 6]},
        'overridden': {'labels': sorted(k for k in lo if lo[k] != defaults['labels'].get(k)),
                       'features': sorted(k for k in fo if fo[k] != defaults['features'].get(k))},
        # internal (not part of the API payload)
        '_auto_pack': auto, '_defaults': defaults, '_company_country_code': cc if comp is not None else 'AE',
        '_nat_words': nat_words, '_nat_exact': nat_exact, '_id_format': P['id_format'], '_document_types': list(P['document_types']),
    }
    rcc = P['country_code'] if code != 'GEN' else out['_company_country_code']
    try:
        rule = _in_schema(schema, lambda: _country_rule(rcc)) if comp is not None else _norm_seed(_seed('AE'))
    except Exception:
        rule = None
    if rule:
        out['rules'] = {'rule_set_id': rule['id'], 'rule_set_name': rule['name'], 'weekend_days': rule['weekend_days']}
    return out


def current_pack(schema_name=None):
    """The resolved pack of the company (schema). Cached; cleared on a settings change or a company country change."""
    schema = _schema(schema_name)

    def load():
        try:
            v = cache.get(_ck(schema))
        except Exception:
            v = None
        if v is None:
            v = _resolve(schema)
            try:
                cache.set(_ck(schema), v, CACHE_TTL)
            except Exception:
                pass
        return v
    return _memo((schema, 'pack', 0), load)


def public_pack(p):
    """The API payload (CONTRACT14 keys only)."""
    return {k: v for k, v in p.items() if not k.startswith('_')}


def pack_code(schema_name=None):
    return current_pack(schema_name)['pack']


def label(key, schema_name=None):
    return current_pack(schema_name)['labels'].get(key, AE_LABELS.get(key, ''))


def feature(key, schema_name=None):
    return bool(current_pack(schema_name)['features'].get(key, True))


def is_ae(schema_name=None):
    return pack_code(schema_name) == 'AE'


def is_national(nationality_name, schema_name=None):
    """A national of the pack's country. AE: 'Emirati' / 'UAE' / 'United Arab Emirates' (the rule the reports always used)."""
    n = str(nationality_name or '').strip().upper()
    if not n:
        return False
    p = current_pack(schema_name)
    return any(w in n for w in p['_nat_words']) or n in p['_nat_exact']


def id_format(schema_name=None):
    """'emirates_id' (AE), 'iqama' (SA) or None (no fixed format)."""
    return current_pack(schema_name)['_id_format']


# ------------------------------------------------------------------ gratuity
def _share(table, years):
    for r in table or []:
        lo, hi = r.get('from_year') or 0, r.get('to_year')
        if years >= lo and (hi is None or years < hi):
            s = float(r.get('share') or 0)
            for exact in (Fraction(1, 3), Fraction(2, 3)):      # 0.3333 / 0.6667 in the rule set = one third / two thirds
                if abs(s - float(exact)) < 0.001:
                    return float(exact)
            return s
    return 0.0


def _table_rules():
    try:
        return [{'from_year': float(r.minimum_value) + 1, 'to_year': float(r.maximum_value) if r.maximum_value else None,
                 'days': float(r.termination_days), 'resign_days': float(r.resignation_days)}
                for r in M('OrganisationManager', 'GratuityTable').objects.filter(is_active=True).order_by('minimum_value')]
    except Exception:
        return []


def gratuity_service_years(start, end, unpaid_days=0, branch_id=None, schema_name=None):
    """v1.20.2: eligible service in years from the joining day to `end` (both counted), less the unpaid days ÷ 365.
    The rule set's gratuity "service_basis" decides how the years are counted: "anniversary" (default – whole years by the
    calendar, the part year in days ÷ 365) or "days" (all days ÷ 365, as many payroll workbooks do)."""
    from EmpManagement.utils import service_years
    if not start or not end or end < start:
        return Decimal('0')
    rule = rule_set_for_branch(branch_id, schema_name) or {}
    if str((rule.get('gratuity') or {}).get('service_basis') or '') == 'days':
        yrs = Decimal((end - start).days + 1) / Decimal('365')
    else:
        yrs = Decimal(service_years(start, end))
    return max(yrs - Decimal(str(unpaid_days or 0)) / Decimal('365'), Decimal('0'))


def gratuity_breakdown(years, basic_monthly, termination='termination', branch_id=None, schema_name=None, wage_monthly=None):
    """{days, amount, per_year_days, daily, cap, rule_set, uae} – floats, amount and days rounded to 2 decimals.

    Slab {"from_year": 1, "to_year": 5, "days": 21} = 21 days for each year from the 1st to the 5th (fractions pro rata).
    Daily wage: basic × 12 / 365 for the UAE rule set (as the reports always did), else basic / 30 (a month's wage = 30 days).
    min_service_years: nothing below it (with a resignation share table, it is the share table that applies on resignation,
    and termination by the employer pays the full award for any service – Saudi Labour Law arts. 84–85)."""
    years = float(years or 0)
    basic = float(basic_monthly or 0)
    out = {'days': 0, 'amount': 0, 'per_year_days': 0.0, 'daily': 0.0, 'cap': None, 'rule_set': None, 'uae': False}
    if not feature('gratuity', schema_name):
        return out
    rule = rule_set_for_branch(branch_id, schema_name)
    if rule is None or not (rule.get('gratuity') or {}).get('slabs'):
        # neutral pack: only the company's own gratuity table (Organisation → Gratuity)
        slabs = _in_schema(_schema(schema_name), _table_rules)
        if not slabs:
            return out
        g = {'slabs': [{'from_year': s['from_year'], 'to_year': s['to_year'],
                        'days': s['resign_days'] if termination in TERMINATION_RESIGN else s['days']} for s in slabs], 'min_service_years': 0}
        out['rule_set'] = 'gratuity table'
        uae = False
    else:
        g = rule['gratuity']
        out['rule_set'] = rule['code']
        basis = str(g.get('daily_basis') or '')
        uae = basis == '365' or (not basis and rule['country_code'] == 'AE')
    out['uae'] = uae
    slabs = g.get('slabs') or []
    daily = basic * 12 / 365 if uae else basic / 30
    # the slab of the current service year (monthly accrual), also below the minimum service
    for s in slabs:
        lo = float(s.get('from_year') or 1) - 1
        hi = float(s['to_year']) if s.get('to_year') not in (None, '') else float('inf')
        if lo < max(years, 0.0001) <= hi:
            out['per_year_days'] = float(s.get('days') or 0)
    out['daily'] = daily
    cap = g.get('cap_years_of_pay')
    if cap:
        # v1.20.2: "cap_basis": "wage" = the cap is so many years of the full monthly wage (default: of the basic)
        cap_on = float(wage_monthly) if g.get('cap_basis') == 'wage' and wage_monthly else basic
        out['cap'] = cap_on * 12 * float(cap)
    if not years or not basic:
        return out
    shares = g.get('resignation_share')
    share = 1.0
    if shares and termination in TERMINATION_RESIGN:
        share = _share(shares, years)
        if share <= 0:
            return out
    elif not shares and years < float(g.get('min_service_years') or 0):
        return out
    days = 0.0
    for s in slabs:
        lo = float(s.get('from_year') or 1) - 1
        hi = float(s['to_year']) if s.get('to_year') not in (None, '') else float('inf')
        seg = min(years, hi) - lo
        if seg > 0:
            days += float(s.get('days') or 0) * seg
    amount = daily * days * share
    if cap:
        amount = min(amount, out['cap'])
    out.update(days=round(days, 2), amount=round(amount, 2))
    return out


def gratuity_amount(years, basic_monthly, termination='termination', branch_id=None, schema_name=None):
    """End-of-service gratuity / award by the branch's country rule set (AE = 21 days' basic a year for 5 years, 30 after,
    nothing before 1 year, capped at 2 years' basic – the same figures as before country packs)."""
    return Decimal(str(gratuity_breakdown(years, basic_monthly, termination, branch_id, schema_name)['amount']))


def uses_uae_gratuity(branch_id=None, schema_name=None):
    """True when the branch follows the UAE rule set – callers then keep their own (unchanged) UAE calculation."""
    try:
        if not feature('gratuity', schema_name):
            return False
        r = rule_set_for_branch(branch_id, schema_name)
        return bool(r and r['country_code'] == 'AE')
    except Exception:
        log.exception('country pack: rule set lookup failed – UAE gratuity kept')
        return True


# ------------------------------------------------------------------ ID formats
def clean_iqama(v):
    """Saudi Iqama (residents, starts with 2) / national ID (Saudis, starts with 1): 10 digits."""
    s = re.sub(r'[\s\-]', '', str(v or ''))
    if not s:
        return ''
    if not s.isdigit() or len(s) != 10:
        raise ValueError(tr('Enter the Iqama / national ID as 10 digits.'))
    if s[0] not in '12':
        raise ValueError(tr('An Iqama number starts with 2 and a Saudi national ID with 1 – check the number.'))
    return s


def clean_generic_id(v, max_len=18):
    s = re.sub(r'\s+', ' ', str(v or '')).strip().upper()
    if not s:
        return ''
    if len(s) > max_len or not re.fullmatch(r'[A-Z0-9][A-Z0-9 /\-]*', s):
        raise ValueError(tr('Enter the ID number with up to {0} letters, digits and dashes.', max_len))
    return s


# ------------------------------------------------------------------ document types for a new company
def document_types_for_country(country):
    """Default document types of a new company in this country (AE: Emirates ID, Passport – as before)."""
    cc = (getattr(country, 'country_code', '') or '').upper()[:2] if country is not None else ''
    code = pack_for_code(cc)
    return list(PACKS[code]['document_types'])


# ------------------------------------------------------------------ report captions
_PHRASES_NON_AE = [
    ('UAE labour law: 21 / 30 days’ basic per year, capped at 2 years’ basic', 'by the country rule set in force'),
    ('(21 days’ basic a year, 30 after 5 years)', '(by the country rule set in force)'),
    ('+ GPSSA employer share 12.5% for UAE nationals.', ''),
    ('Employer pension – GPSSA', 'Employer pension'),
    ('and GPSSA pension', 'and employer pension'),
    ('UAE employee-file fields', 'employee-file fields'),
    ('Emiratisation', 'Nationals share'),
    (', labour card, work permit', ', work permit'),
]
_PROTECT = 'United Arab Emirates'


def report_replacements(schema_name=None):
    """[(old, new)] for captions built in Python. Empty for an AE company without changed captions."""
    p = current_pack(schema_name)

    def build():
        pairs = []
        if p['pack'] != 'AE':
            pairs += _PHRASES_NON_AE
            pairs += [('UAE nationals', p['labels']['nationals_plural']), ('UAE national', f"{p['labels']['nationals']} national")]
        for k in LABEL_KEYS:
            new = p['labels'].get(k) or ''
            if new and new != AE_LABELS[k]:
                pairs.append((AE_LABELS[k], new))
        cur = p['currency']['code']
        if cur and cur != 'AED':
            pairs.append(('AED', cur))
        seen, out = set(), []
        for a, b in sorted(pairs, key=lambda x: -len(x[0])):
            if a not in seen:
                seen.add(a)
                out.append((a, b))
        return out
    return _memo((_schema(schema_name), 'repl', 0), build)


def localize(text, pairs=None):
    if not text or not isinstance(text, str):
        return text
    pairs = report_replacements() if pairs is None else pairs
    if not pairs:
        return text
    s = text.replace(_PROTECT, '\x00')
    for a, b in pairs:
        if a in s:
            s = re.sub(r'\b' + re.escape(a) + r'\b', b, s) if a[-1].isalnum() and a[0].isalnum() else s.replace(a, b)
    return s.replace('\x00', _PROTECT).replace('  ', ' ').strip()


def localize_report(data):
    """Report payload (DashboardManagement ReportView): title, description, notes, column labels and row values that are exactly
    a UAE caption ("Emirates ID", "UAE national" …)."""
    pairs = report_replacements()
    if not pairs:
        return data
    exact = dict(pairs)
    for k in ('title', 'description'):
        if k in data:
            data[k] = localize(data[k], pairs)
    if data.get('notes'):
        data['notes'] = [n for n in (localize(n, pairs) for n in data['notes']) if n]
    for c in data.get('columns') or []:
        c['label'] = localize(c.get('label'), pairs)
    for r in data.get('rows') or []:
        if isinstance(r, dict):
            for k, v in r.items():
                if isinstance(v, str) and v in exact:
                    r[k] = localize(v, pairs)
    return data


# ------------------------------------------------------------------ apply defaults
def _weekday_fields():
    return ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def apply_defaults(user=None, schema_name=None):
    """Loads the pack's defaults into the company: document types, the country rule set, leave types (only when the company
    has none) and the weekend of the rule set on the untouched default weekend calendar. Safe to run again."""
    schema = _schema(schema_name)
    p = current_pack(schema)
    added, kept = [], []

    def run():
        from django.db import transaction
        with transaction.atomic():
            # 1. document types
            DT = M('EmpManagement', 'document_type')
            Branch = M('OrganisationManager', 'brnch_mstr')
            branches = list(Branch.objects.all())
            for name in p['_document_types']:
                if DT.objects.filter(type_name__iexact=name).exists():
                    kept.append({'kind': 'Document type', 'name': name, 'detail': 'already there'})
                    continue
                d = DT.objects.create(type_name=name, description=f'Default {name} document type')
                d.branch.set(branches)
                added.append({'kind': 'Document type', 'name': name, 'detail': 'added for every branch'})
            # 2. the country rule set (Organisation → Country policies)
            cc = p['country_code'] if p['pack'] != 'GEN' else p['_company_country_code']
            seed = _seed(cc)
            rule_name = ''
            try:
                CP = M('OrgStructure', 'CountryPolicy')
                if seed and not CP.objects.filter(country_code=cc).exists():
                    row = CP(is_standard=True, **{k: v for k, v in seed.items()})
                    if CP.objects.filter(code=seed['code']).exists():
                        row = None
                    if row is not None:
                        row.save()
                        added.append({'kind': 'Country rule set', 'name': seed['name'], 'detail': 'Organisation → Country policies'})
                elif seed:
                    kept.append({'kind': 'Country rule set', 'name': seed['name'], 'detail': 'already there'})
                r = CP.objects.filter(active=True, country_code=cc).order_by('-is_standard', 'id').first() if cc else None
                rule = _norm_policy(r) if r else (_norm_seed(seed) if seed else None)
            except LookupError:
                rule = _norm_seed(seed) if seed else None
            rule_name = rule['name'] if rule else ''
            # 3. leave types – only when the company has none
            LT = M('calendars', 'leave_type')
            if LT.objects.exists():
                kept.append({'kind': 'Leave types', 'name': f'{LT.objects.count()} leave types', 'detail': 'the company has leave types – not changed'})
            elif p['pack'] == 'AE':
                from LeavePolicy import setup as leave_setup
                res = leave_setup.load(user)
                for n in res.get('created_leave_types', []):
                    added.append({'kind': 'Leave type', 'name': n, 'detail': 'UAE Labour Law'})
            else:
                WF = M('calendars', 'LVApprovalWorkflow')
                for name, code, cat, typ, half, desc in NEUTRAL_LEAVE_TYPES:
                    t = LT.objects.create(name=name, code=code, type=typ, unit='days', negative=False, description=desc[:200], allow_half_day=half,
                                          include_weekend=True, include_holiday=True, leave_category=cat, use_common_workflow=False,
                                          include_dashboard=cat in ('annual', 'sick'), created_by=user)
                    if not WF.objects.filter(request_type=t).exists():
                        wf = WF.objects.create(request_type=t, approval_type='reporting_manager', created_by=user)
                        wf.branch.set(branches)
                    added.append({'kind': 'Leave type', 'name': name, 'detail': desc})
            # 4. v1.18.0 – UAE: the GPSSA pension deduction of Emirati employees (0 for everybody else)
            if p['pack'] == 'AE':
                SC = M('PayrollManagement', 'SalaryComponent')
                if SC.objects.filter(code='GPSSA').exists():
                    kept.append({'kind': 'Salary component', 'name': 'Pension – GPSSA (employee)', 'detail': 'already there'})
                else:
                    SC.objects.create(name='Pension – GPSSA (employee)', code='GPSSA', component_type='deduction', payroll_category='other_allowance',
                                      component_value_type='variable', formula='pension_salary * pension_employee_rate * is_national',
                                      deduct_leave=False, show_in_payslip=True, branch=branches[0] if len(branches) == 1 else None,
                                      description='Employee share of the GPSSA pension for UAE nationals (5 % / 11 % of basic + housing + transport)')
                    added.append({'kind': 'Salary component', 'name': 'Pension – GPSSA (employee)', 'detail': 'UAE nationals – Federal Decree-Law 57/2023'})
            # 5. weekend of the rule set on the default weekend calendar (only while it still has the standard Saturday–Sunday)
            if rule and rule.get('weekend_days'):
                want = set(int(d) for d in rule['weekend_days'])
                days = _weekday_fields()
                std = {5, 6}
                WC = M('calendars', 'weekend_calendar')
                for w in WC.objects.filter(description__startswith='Default Weekend Calendar'):
                    off = {i for i, d in enumerate(days) if getattr(w, d) == 'leave'}
                    full = all(getattr(w, d) in ('leave', 'fullday') for d in days)
                    if off == want:
                        kept.append({'kind': 'Weekend', 'name': w.description, 'detail': 'already ' + ', '.join(days[i].title() for i in sorted(want))})
                    elif off == std and full and not w.is_alternate:
                        for i, d in enumerate(days):
                            setattr(w, d, 'leave' if i in want else 'fullday')
                        w.save()
                        added.append({'kind': 'Weekend', 'name': w.description,
                                      'detail': ', '.join(days[i].title() for i in sorted(want)) + f' (from {rule_name})'})
                    else:
                        kept.append({'kind': 'Weekend', 'name': w.description, 'detail': 'changed by the company – not touched'})
    _in_schema(schema, run)
    clear_cache(schema)
    return {'pack': p['pack'], 'added': added, 'kept': kept,
            'message': (f'{len(added)} default(s) added.' if added else 'Nothing to add – the company already has the country defaults.')}


def packs_list():
    return [{'code': c, 'name': PACKS[c]['name']} for c in PACK_ORDER]


def swap_currency(obj, schema_name=None):
    """Replaces the word AED in every string of a payload with the company currency (no change for AED companies)."""
    cur = current_pack(schema_name)['currency']['code']
    if not cur or cur == 'AED':
        return obj
    rx = re.compile(r'\bAED\b')

    def walk(o):
        if isinstance(o, str):
            return rx.sub(cur, o)
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return walk(obj)
