"""v1.70.0 (pending items 142 – 151) – GCC and India country rules for the end of service, the statutory schemes, the deduction limit
and the final-settlement due date. The UAE rules are unchanged (this module returns None for a UAE branch).

Facts and their sources (each checked in two sources on 15 Nov 2026; "confirm" = one source only or sources differ – shown to the user):
  SA  award on the wage incl. recurring allowances (Labour Law arts. 84/85 – HRSD knowledge centre, Etqan / Teamed); resignation
      shares <2 y none, 2–5 one third, 5–10 two thirds, 10+ full; final pay within 1 week (employer ends) / 2 weeks (worker ends)
      (art. 88 – Qoyod, Commoner-law); deductions at most 50 % of the wage, employer-loan recovery 10 % (arts. 92/93 – HRSD, Qoyod);
      GOSI: base basic + housing, SAR 1,500 – 45,000 (PaySpace, Qoyod); first insured from 03/07/2024: annuities 9 % each side,
      +0.5 % every 1 July from 2025 to 11 % in 2028 (Qoyod, PaySpace/Mercans); SANED 0.75 % each, hazards 2 % employer.
  KW  indemnity on the wage incl. periodic allowances, 15 days a year for 5 years then a month, cap 18 months (Arab Times, Teamed);
      resignation <3 y none, 3–5 half, 5–10 two thirds, 10+ full (Teamed; Arab Times gives two thirds for 5–10 – the rest "confirm");
      loan recovery 10 %, other debts 25 % of the wage (ARY News 31/10/2026 – "confirm").
  BH  non-Bahraini: employer pays SIO 4.2 % of the wage a month (first 3 years) / 8.4 % (after) from 01/03/2024 and the SIO pays the
      award for that service; the employer still owes the gratuity of the service before (WTW, Mercans, Teamed); wage = contract wage
      + social allowance (WTW, Teamed); Bahraini (and GCC) nationals: SIO pension, no gratuity (WTW, Teamed).
  OM  gratuity on the basic: service before 31/07/2023 15 days a year for 3 years then 30; after, 30 days (PwC GCC alert 2026,
      Middle East Briefing); non-Omani: savings scheme 9 % of the basic paid by the employer to the SPF from 19/07/2027, the
      gratuity stops for the service after (Gulf News, Oman Observer, Teamed); Omanis: SPF pension, no gratuity (Teamed, PwC).
  QA  21 days' basic a year (Middle East Briefing, Teamed); Qatari nationals: social insurance, no gratuity (Teamed – "confirm").
  IN  Labour Codes in force 21/11/2025: fixed-term employees get gratuity after 1 year (KPMG, Fisher Phillips); "wages" = basic +
      DA (+ retaining allowance) and the excluded allowances above 50 % of the pay are added back (HSF, Fisher Phillips); final
      wages within 2 working days of the exit (HSF, KPMG); ceiling ₹ 20 lakh (Fisher Phillips, rule set).
"""
import calendar
import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
Q2 = Decimal('0.01')
ZERO = Decimal('0')
GCC = ('AE', 'SA', 'QA', 'OM', 'BH', 'KW')

BH_SIO_EOS_FROM = date(2024, 3, 1)
OM_NEW_LAW = date(2023, 7, 31)          # service from this day: 30 days a year from the first year
OM_SAVINGS_FROM = date(2027, 7, 19)
SA_GOSI_NEW_FROM = date(2024, 7, 3)
# v1.73.19 (item 147) Kuwait PIFSS for Kuwaiti nationals – Decree-Law 61/1976, Laws 101/2013 and 110/2014 (checked 17/11/2026: Teamed,
# MYND Solution, Oracle HCM). Marginal parts of the contribution wage: (up to, employee %, employer %)
KW_PIFSS_FLOOR = Decimal(230)
KW_PIFSS_TIERS = [(Decimal(1500), Decimal('10.5'), Decimal('11.5')),   # basic 5/10 + increase fund 2.5/1 + unemployment 0.5/0.5 + remuneration 2.5/–
                  (Decimal(2750), Decimal('8'), Decimal('11.5'))]       # supplementary 5/10 + increase fund 2.5/1 + unemployment 0.5/0.5
# v1.73.19 Oman Social Protection Fund, Omani employees (checked 17/11/2026: Teamed, Middle East Briefing): employee 7.5 % pension
# + 0.5 % job security; employer 11 % pension + 1 % work injury + 1 % maternity + 0.5 % job security, + 1 % sick leave from 19/07/2026
OM_SPF_SICK_FROM = date(2026, 7, 19)
OM_SPF_EMPLOYEE = Decimal(8)
OM_SPF_EMPLOYER = (Decimal('13.5'), Decimal('14.5'))
OM_SPF_CEILING = Decimal(3000)
IN_CODES_FROM = date(2025, 11, 21)

RESIGN = {
    'SA': [(0, 2, Decimal(0)), (2, 5, Decimal(1) / 3), (5, 10, Decimal(2) / 3), (10, None, Decimal(1))],
    'KW': [(0, 3, Decimal(0)), (3, 5, Decimal('0.5')), (5, 10, Decimal(2) / 3), (10, None, Decimal(1))],
}
NATIONALS_NO_GRATUITY = {'BH': 'SIO pension', 'OM': 'Social Protection Fund pension', 'QA': 'social insurance (confirm)'}
DUE_DAYS = {'SA': {'employer': 7, 'employee': 14}, 'IN': {'working_days': 2}}
DEDUCTION = {'SA': {'legal_pct': Decimal(50), 'loan_pct': Decimal(10), 'source': 'Saudi Labour Law arts. 92 – 93'},
             'KW': {'legal_pct': Decimal(25), 'loan_pct': Decimal(10), 'source': 'Kuwait Labour Law – 10 % for loans, 25 % for debts (confirm)'}}
CONFIRM = {'KW': 'Kuwait: the wage basis (basic with the fixed allowances) comes from one source – confirm.',   # v1.73.19 the resignation shares: two sources
           'QA': 'Qatar: Qatari nationals left out of the gratuity – one source – confirm.'}


def q2(v):
    return Decimal(str(v or 0)).quantize(Q2, ROUND_HALF_UP)


# ------------------------------------------------------------------ the employee's country
def country(emp):
    """ISO-2 of the rule set of the employee's branch (CountryPack), '' when none."""
    try:
        from CountryPack.services import rule_set_for_branch
        r = rule_set_for_branch(getattr(emp, 'emp_branch_id_id', None)) or {}
        return (r.get('country_code') or '').upper()
    except Exception:   # noqa: BLE001
        return ''


NAT_WORDS = (('SA', r'SAUDI(A|AN)?'), ('KW', r'KUWAITI?'), ('BH', r'BAHRAINI?'), ('OM', r'OMANI?'), ('QA', r'QATARI?'),
             ('AE', r'(EMIRATI|UAE|UNITED ARAB EMIRATES)'), ('IN', r'INDIAN?'))


def nationality_code(emp):
    """ISO-2 of the employee's nationality from its name as whole words ("Saudi", "Omani", "Indian" …; ROMANIAN is not OMANI)"""
    import re
    n = ' '.join(str(getattr(emp, 'emp_nationality', '') or '').upper().replace('-', ' ').split())
    if not n:
        return ''
    for cc, w in NAT_WORDS:
        if re.search(r'\b' + w + r'\b', n):
            return cc
    try:
        from CountryPack.packs import iso2
        return iso2(None, n)
    except Exception:   # noqa: BLE001
        return ''


def is_national(emp, cc):
    n = nationality_code(emp)
    if n:
        return n == cc
    try:
        from CountryPack.services import is_national as _n
        return bool(_n(str(getattr(emp, 'emp_nationality', '') or '')))
    except Exception:   # noqa: BLE001
        return False


def is_gcc_national(emp):
    return nationality_code(emp) in GCC


# ------------------------------------------------------------------ wages
def _rows(emp):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    return [r for r in ESS.objects.filter(employee=emp, is_active=True).select_related('component') if r.component.component_type == 'addition']


def basic_of(emp):
    return sum((Decimal(str(r.amount or 0)) for r in _rows(emp) if r.component.payroll_category == 'basic'), ZERO)


def fixed_wage(emp):
    """the fixed monthly wage: every fixed addition (basic and allowances), no variable pay (commission, overtime, bonus)"""
    out = ZERO
    for r in _rows(emp):
        c = r.component
        if getattr(c, 'component_value_type', 'fixed') not in ('fixed', '', None) and c.payroll_category != 'basic':
            continue
        if getattr(c, 'is_monthly_input', False) or getattr(c, 'monthly_input', False):
            continue
        out += Decimal(str(r.amount or 0))
    return out


def india_wages(emp):
    """Code on Wages 2019 s. 2(y): basic + DA (+ retaining allowance); the excluded allowances above 50 % of the total pay are added."""
    total, wages = ZERO, ZERO
    for r in _rows(emp):
        c = r.component
        a = Decimal(str(r.amount or 0))
        total += a
        nm = (c.name or '').lower()
        if c.payroll_category == 'basic' or 'dearness' in nm or nm.strip() in ('da', 'retaining allowance') or 'retaining' in nm:
            wages += a
    excluded = total - wages
    add = max(excluded - total / 2, ZERO)
    return wages + add, wages, add, total


def gratuity_wage(emp, cc):
    """(monthly wage of the gratuity, label)"""
    if cc in ('SA', 'KW', 'BH'):
        return fixed_wage(emp), tr('wage with the fixed allowances')
    if cc == 'IN':
        w, base, add, _t = india_wages(emp)
        return w, (tr('wages (basic + DA) plus {0:,.2f} of allowances above 50 % of the pay', add) if add else tr('wages (basic + DA)'))
    return basic_of(emp), tr('basic wage')


# ------------------------------------------------------------------ service
def years_between(a, b, branch_id=None):
    """service in years as the rule set counts it (CountryPack.gratuity_service_years: whole years by the calendar + the part year)"""
    if not a or not b or b < a:
        return Decimal(0)
    try:
        from CountryPack.services import gratuity_service_years
        return Decimal(str(gratuity_service_years(a, b, 0, branch_id)))
    except Exception:   # noqa: BLE001
        return Decimal((b - a).days + 1) / Decimal(365)


def _slab_days(years, slabs):
    days = Decimal(0)
    for s in slabs:
        lo = Decimal(str(s.get('from_year') or 1)) - 1
        hi = Decimal(str(s['to_year'])) if s.get('to_year') not in (None, '') else None
        seg = (min(years, hi) if hi is not None else years) - lo
        if seg > 0:
            days += Decimal(str(s.get('days') or 0)) * seg
    return days


def _rule(emp):
    try:
        from CountryPack.services import rule_set_for_branch
        return rule_set_for_branch(getattr(emp, 'emp_branch_id_id', None)) or {}
    except Exception:   # noqa: BLE001
        return {}


def is_fixed_term(emp):
    """a fixed-term (limited) contract: the labour contract type on the employee (Employee › Identity: Limited)"""
    try:
        row = M('EmployeeProfile', 'EmployeeIdentity').objects.filter(employee_id=emp.pk).values_list('mohre_contract_type', flat=True).first()
        return (row or '') == 'limited'
    except Exception:   # noqa: BLE001
        return False


def gratuity(emp, end, termination='termination', unpaid_days=0):
    """The end-of-service gratuity by the country of the employee's branch (not the UAE): None for a UAE branch (the UAE code applies),
    else {'country', 'amount', 'days', 'wage', 'wage_label', 'daily', 'years', 'share', 'steps', 'note', 'eligible', 'covered_from'}."""
    cc = country(emp)
    if not cc or cc == 'AE':
        return None
    joined = getattr(emp, 'emp_joined_date', None)
    out = {'country': cc, 'amount': ZERO, 'days': ZERO, 'wage': ZERO, 'wage_label': '', 'daily': ZERO, 'years': Decimal(0), 'share': Decimal(1),
           'steps': [], 'note': '', 'eligible': False, 'covered_from': None, 'confirm': CONFIRM.get(cc, '')}
    if not joined or not end or end < joined:
        return out
    if cc in NATIONALS_NO_GRATUITY and (is_national(emp, cc) or (cc == 'BH' and is_gcc_national(emp))):
        out['note'] = tr('No gratuity – national covered by the {0}', tr(NATIONALS_NO_GRATUITY[cc]))
        return out
    rule = (_rule(emp).get('gratuity') or {})
    wage, label = gratuity_wage(emp, cc)
    out.update(wage=q2(wage), wage_label=label)
    unpaid = Decimal(str(unpaid_days or 0)) / Decimal(365)
    total_years = max(years_between(joined, end) - unpaid, Decimal(0))
    out['years'] = total_years.quantize(Decimal('0.0001'))
    # who may have it at all (minimum service)
    min_years = Decimal(str(rule.get('min_service_years') or 1))
    if cc == 'IN':
        min_years = Decimal(1) if (is_fixed_term(emp) and end >= IN_CODES_FROM) else Decimal(5)
    shares = RESIGN.get(cc)
    resign = termination in ('resignation', 'resign')
    if resign and shares:
        share = next((s for lo, hi, s in shares if total_years >= lo and (hi is None or total_years < hi)), Decimal(1))
        out['share'] = share
        if share <= 0:
            out['steps'].append(tr('Resignation after {0} years – no award under the law', total_years.quantize(Q2)))
            return out
    elif total_years < min_years and not (cc in ('SA', 'KW') and not resign):
        out['steps'].append(tr('{0} years of service – the gratuity starts after {1} year(s)', total_years.quantize(Q2), min_years.normalize()))
        return out
    out['eligible'] = True
    # the part of the service the employer still owes (Bahrain: SIO from 01/03/2024; Oman: savings scheme from 19/07/2027 – non-nationals)
    paid_end = end
    if cc == 'BH' and end >= BH_SIO_EOS_FROM:
        paid_end = BH_SIO_EOS_FROM - timedelta(days=1)
        out['covered_from'] = BH_SIO_EOS_FROM
    if cc == 'OM' and end >= OM_SAVINGS_FROM:
        paid_end = OM_SAVINGS_FROM - timedelta(days=1)
        out['covered_from'] = OM_SAVINGS_FROM
    daily = wage / Decimal(rule.get('divisor') or 30)
    out['daily'] = daily
    if paid_end < joined:
        out['steps'].append(tr('All the service is after {0:%d/%m/%Y} – paid through the monthly contributions', out['covered_from']))
        return out
    yrs = max(years_between(joined, paid_end) - unpaid, Decimal(0))
    if cc == 'OM':
        old_end = min(paid_end, OM_NEW_LAW - timedelta(days=1))
        y_old = years_between(joined, old_end) if old_end >= joined else Decimal(0)
        y_new = max(yrs - y_old, Decimal(0))
        d_old = min(y_old, Decimal(3)) * 15 + max(y_old - 3, Decimal(0)) * 30
        days = d_old + y_new * 30
        out['steps'].append(tr('Service before 31/07/2023: {0} years – 15 days a year for 3 years, 30 after = {1} days', y_old.quantize(Q2), d_old.quantize(Q2)))
        out['steps'].append(tr('Service from 31/07/2023: {0} years × 30 days = {1} days', y_new.quantize(Q2), (y_new * 30).quantize(Q2)))
    else:
        slabs = rule.get('slabs') or [{'from_year': 1, 'to_year': None, 'days': 21}]
        days = _slab_days(yrs, slabs)
        for sl in slabs:
            out['steps'].append(tr('{0} days a year from year {1} of service', sl.get('days'), sl.get('from_year') or 1))
        out['steps'].append(tr('{0} years of service: {1} days', yrs.quantize(Q2), days.quantize(Q2)))
    amount = days * daily
    out['steps'].append(tr('{0} days × {1:,.2f} a day ({2}: {3:,.2f} ÷ {4}) = {5:,.2f}', days.quantize(Q2), daily.quantize(Q2), label, wage,
                           rule.get('divisor') or 30, amount.quantize(Q2)))
    if out['share'] < 1:
        amount = amount * out['share']
        out['steps'].append(tr('Resignation: {0} of the award = {1:,.2f}', {Decimal(1) / 3: '1/3', Decimal(2) / 3: '2/3', Decimal('0.5'): '1/2'}
                               .get(out['share'], str(out['share'])), amount.quantize(Q2)))
    cap = rule.get('cap_years_of_pay')
    if cc == 'KW':
        cap = cap or 1.5
    if cap:
        cmax = wage * 12 * Decimal(str(cap))
        if amount > cmax:
            amount = cmax
            out['steps'].append(tr('Capped at {0} years of the wage: {1:,.2f}', cap, cmax.quantize(Q2)))
    if rule.get('cap_amount') and amount > Decimal(str(rule['cap_amount'])):
        amount = Decimal(str(rule['cap_amount']))
        out['steps'].append(tr('Ceiling {0:,.2f}', amount))
    if out['covered_from']:
        out['steps'].append(tr('Service from {0:%d/%m/%Y} is paid through the monthly contributions ({1})', out['covered_from'],
                               tr('SIO end-of-service fund') if cc == 'BH' else tr('Social Protection Fund savings scheme')))
    out.update(amount=q2(amount), days=days.quantize(Q2))
    return out


def exit_gratuity(emp, lwd, termination, add, info, unpaid_days=0):
    """Final settlement (PayrollPlus.services.compute step 4): True when this module handled the gratuity (a non-UAE branch)."""
    g = gratuity(emp, lwd, termination, unpaid_days)
    if g is None:
        return False
    if g['note']:
        info('gratuity', g['note'], '', 'gratuity')
        return True
    if g['amount'] > 0:
        add('gratuity', tr('End-of-service gratuity ({0} days)', g['days']), 'earning', g['amount'],
            tr('{0} labour law – {1}: {2:,.2f}', g['country'], g['wage_label'], g['wage']), 'gratuity', g['steps'] + ([g['confirm']] if g['confirm'] else []))
    else:
        info('gratuity', tr('No gratuity'), '; '.join(g['steps']), 'gratuity')
    if g['covered_from']:
        info('gratuity_fund', tr('Service from {0:%d/%m/%Y}: paid by the fund from the monthly contributions', g['covered_from']), '', 'gratuity')
    return True


# ------------------------------------------------------------------ final settlement due date (item 151)
def settlement_due(emp, lwd, termination, default_days):
    """the day the final settlement is due by the law of the branch's country; None = the company setting applies"""
    cc = country(emp)
    rule = DUE_DAYS.get(cc)
    if not rule or not lwd:
        return None
    if 'working_days' in rule:
        d, n = lwd, 0
        weekend = {6}   # Sunday (India)
        while n < rule['working_days']:
            d += timedelta(days=1)
            if d.weekday() not in weekend:
                n += 1
        return d
    who = 'employee' if termination in ('resignation', 'resign') else 'employer'
    return lwd + timedelta(days=rule[who])


# ------------------------------------------------------------------ deduction limit (item 150)
def deduction_defaults(cc):
    return DEDUCTION.get((cc or '').upper())


# ------------------------------------------------------------------ statutory schemes (items 144 – 146)
def _first_july_steps(start, on, base, step, top):
    """GOSI new entrants: base % until 30/06/2025, +step every 1 July from 2025, at most top"""
    if on < date(2025, 7, 1):
        return base
    n = on.year - 2025 + (1 if on >= date(on.year, 7, 1) else 0)
    return min(base + step * n, top)


def gosi_annuity_rate(emp, on):
    """(employee %, employer %, basis) – first insured before 03/07/2024: 9 / 9; from then: 9 % + 0.5 % every 1 July from 2025, 11 % from 2028"""
    try:
        from .legal import insured_from
        first, basis = insured_from(emp, 'gosi')
    except Exception:   # noqa: BLE001
        first, basis = getattr(emp, 'emp_joined_date', None), 'joined'
    if not first or first < SA_GOSI_NEW_FROM:
        return Decimal(9), Decimal(9), 'old'
    r = _first_july_steps(first, on, Decimal(9), Decimal('0.5'), Decimal(11))
    return r, r, 'new'


def bh_sio_eos_rate(emp, on):
    """employer % of the wage to the SIO end-of-service fund: 4.2 for the first 3 years of service, 8.4 after (from 01/03/2024)"""
    if on < BH_SIO_EOS_FROM:
        return Decimal(0)
    joined = getattr(emp, 'emp_joined_date', None)
    y = years_between(joined, on) if joined else Decimal(0)
    return Decimal('4.2') if y < 3 else Decimal('8.4')


def om_savings_rate(emp, on):
    return Decimal(9) if on >= OM_SAVINGS_FROM else Decimal(0)


def statutory_override(scheme, emp, rows, nat, on):
    """(base, employee, employer) for the 1.70.0 schemes, None = the scheme's own rules (statutory.compute)"""
    from .statutory import base_amount
    code = (scheme.code or '').strip().lower()
    if code in ('gosi', 'saned', 'gosi_oh'):
        if country(emp) != 'SA':
            return None                                          # not a Saudi branch: the scheme's own rules
        if code != 'gosi_oh' and not is_national(emp, 'SA'):
            return Decimal('0.00'), Decimal('0.00'), Decimal('0.00')   # annuities and SANED: Saudi nationals only
        b = base_amount(rows, 'basic') + _housing(rows)       # GOSI contribution wage: basic + housing (not transport)
        lo = Decimal(str(scheme.min_base)) if scheme.min_base is not None else Decimal(1500)
        hi = Decimal(str(scheme.max_base)) if scheme.max_base is not None else Decimal(45000)
        b = min(max(b, lo), hi)
        e, r = Decimal(str(scheme.employee_rate or 0)), Decimal(str(scheme.employer_rate or 0))
        if code == 'gosi':
            ne, nr, law = gosi_annuity_rate(emp, on)
            if law == 'new':   # first insured from 03/07/2024: the law's schedule (the scheme's rates stay for the others)
                e, r = ne, nr
        return q2(b), q2(b * e / 100), q2(b * r / 100)
    if code == 'sio_eos':
        if country(emp) != 'BH':
            return None
        if is_gcc_national(emp):
            return Decimal('0.00'), Decimal('0.00'), Decimal('0.00')
        b = fixed_wage(emp)
        return q2(b), Decimal('0.00'), q2(b * bh_sio_eos_rate(emp, on) / 100)
    if code == 'pifss':   # v1.73.19 (item 147) the law's tiers for a Kuwait branch
        if country(emp) != 'KW':
            return None
        if not is_national(emp, 'KW'):
            return Decimal('0.00'), Decimal('0.00'), Decimal('0.00')
        b = max(base_amount(rows, scheme.base or 'basic_housing_transport'), KW_PIFSS_FLOOR)
        e, r = kw_pifss(b)
        return q2(min(b, KW_PIFSS_TIERS[-1][0])), e, r
    if code == 'pasi':    # v1.73.19 Oman SPF rates by date for an Oman branch
        if country(emp) != 'OM':
            return None
        if not is_national(emp, 'OM'):
            return Decimal('0.00'), Decimal('0.00'), Decimal('0.00')
        b = base_amount(rows, scheme.base or 'basic_housing_transport')
        if scheme.min_base is not None:
            b = max(b, Decimal(str(scheme.min_base)))
        b = min(b, OM_SPF_CEILING)
        er = OM_SPF_EMPLOYER[1] if on >= OM_SPF_SICK_FROM else OM_SPF_EMPLOYER[0]
        return q2(b), q2(b * OM_SPF_EMPLOYEE / 100), q2(b * er / 100)
    if code == 'om_savings':
        if country(emp) != 'OM':
            return None
        if is_gcc_national(emp):
            return Decimal('0.00'), Decimal('0.00'), Decimal('0.00')
        b = base_amount(rows, 'basic')
        return q2(b), Decimal('0.00'), q2(b * om_savings_rate(emp, on) / 100)
    return None


def kw_pifss(wage):
    """v1.73.19 (item 147): (employee, employer) PIFSS of a Kuwaiti's monthly contribution wage, tier by tier (floor 230, nothing above 2,750)."""
    w = max(Decimal(str(wage or 0)), KW_PIFSS_FLOOR)
    e = r = ZERO
    lo = ZERO
    for hi, pe, pr in KW_PIFSS_TIERS:
        part = max(ZERO, min(w, hi) - lo)
        e += part * pe / 100
        r += part * pr / 100
        lo = hi
    return q2(e), q2(r)


def _housing(rows):
    t = ZERO
    for r in rows:
        c = r.component
        if c.component_type == 'addition' and (c.payroll_category in ('hra', 'housing_allowance') or 'housing' in (c.name or '').lower()):
            t += Decimal(str(r.amount or 0))
    return t


def month_last(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])
