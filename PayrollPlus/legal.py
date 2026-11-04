"""v1.59.0 – date-versioned legal rules.

* LegalRate              – a legal figure used by the code (KNOWN_RATES) with a value from a date; without a row the
                           built-in value (the figure the code always used) applies.
* StatutorySchemeVersion – new rates of a statutory scheme from a date (statutory.compute uses the version in force).
* CountryPolicyVersion   – gratuity / annual leave / overtime of a country policy from a date (CountryPack.services).

Which day: CountryPack.services.rule_date() – the payroll sets the first day of the pay period, the end of service the last
working day, the provisions the month end; anything else uses today. No real future law changes are loaded: HR adds a
version when a law changes, with the law reference.
"""
import copy
from decimal import Decimal

from django.apps import apps

M = apps.get_model

KNOWN_RATES = {
    'ae_gpssa_employee_new': {'country': 'AE', 'default': Decimal('0.11'),
                              'label': 'GPSSA employee rate – joined on / after 31 Oct 2023 (fraction, 0.11 = 11 %)'},
    'ae_gpssa_employee_old': {'country': 'AE', 'default': Decimal('0.05'),
                              'label': 'GPSSA employee rate – joined before 31 Oct 2023 (fraction)'},
    'ae_gpssa_employer': {'country': 'AE', 'default': Decimal('0.125'),
                          'label': 'GPSSA employer rate, net of the government share (fraction)'},
    'ae_gpssa_salary_min': {'country': 'AE', 'default': Decimal('3000'), 'label': 'GPSSA contribution salary – minimum (AED / month)'},
    'ae_gpssa_salary_max': {'country': 'AE', 'default': Decimal('70000'), 'label': 'GPSSA contribution salary – maximum (AED / month)'},
    # v1.62.3: the figures that differ for nationals insured before 31 Oct 2023 (old law, Federal Law 7/1999) and for new-law
    # employees with a contribution salary of AED 20,000 or more (no government share – the employer pays 15 %)
    'ae_gpssa_salary_min_old': {'country': 'AE', 'default': Decimal('1000'),
                                'label': 'GPSSA contribution salary – minimum, joined before 31 Oct 2023 (AED / month)'},
    'ae_gpssa_salary_max_old': {'country': 'AE', 'default': Decimal('50000'),
                                'label': 'GPSSA contribution salary – maximum, joined before 31 Oct 2023 (AED / month)'},
    'ae_gpssa_employer_full': {'country': 'AE', 'default': Decimal('0.15'),
                               'label': 'GPSSA employer rate without the government share – joined on / after 31 Oct 2023 with a contribution salary at or above the limit below (fraction)'},
    'ae_gpssa_subsidy_salary_limit': {'country': 'AE', 'default': Decimal('20000'),
                                      'label': 'GPSSA – the government pays 2.5 % of the employer share below this contribution salary, joined on / after 31 Oct 2023 (AED / month)'},
    # v1.62.11 (item 229) ADPF – Abu Dhabi Pension Fund (UAE nationals employed in Abu Dhabi, instead of GPSSA).
    # Sources: Abu Dhabi Law No. (18) of 2023 amending Law No. (2) of 2000, Official Gazette of Abu Dhabi 1 Dec 2023 (abudhabi.gov.ae):
    # Art. 15 – existing members 5 % / employer 15 %, members first covered from 1 Dec 2023 11 % / 15 %; Art. 1 – private sector pensionable
    # salary = the contract salary, minimum AED 6,000, maximum AED 100,000; Art. 4 Second (4) – in-service members AED 150,000. Confirmed by
    # u.ae (pensions and social security for UAE citizens: 11 % / 15 %, AED 100,000 for new entrants), Zoho Payroll UAE (ADPF 11 % / 15 %,
    # AED 100,000, from 1 Dec 2023) and Sovereign Group (ADPF: 11 % / 5 %, AED 100,000 / 150,000).
    'ae_adpf_employee_new': {'country': 'AE', 'default': Decimal('0.11'),
                             'label': 'ADPF employee rate – first insured on / after 1 Dec 2023 (fraction, 0.11 = 11 %)'},
    'ae_adpf_employee_old': {'country': 'AE', 'default': Decimal('0.05'), 'label': 'ADPF employee rate – insured before 1 Dec 2023 (fraction)'},
    'ae_adpf_employer': {'country': 'AE', 'default': Decimal('0.15'), 'label': 'ADPF employer rate (fraction)'},
    'ae_adpf_salary_min': {'country': 'AE', 'default': Decimal('6000'), 'label': 'ADPF pensionable salary – minimum, private sector (AED / month)'},
    'ae_adpf_salary_max': {'country': 'AE', 'default': Decimal('100000'),
                           'label': 'ADPF pensionable salary – maximum, first insured on / after 1 Dec 2023 (AED / month)'},
    'ae_adpf_salary_max_old': {'country': 'AE', 'default': Decimal('150000'),
                               'label': 'ADPF pensionable salary – maximum, insured before 1 Dec 2023 (AED / month)'},
    # v1.62.11 (item 229) DIFC DEWS – DIFC Employment Law No. 2 of 2019 (as amended by DIFC Law No. 4 of 2020 and DIFC Law Amendment Law
    # No. 1 of 2024), Art. 66(7): from the Qualifying Scheme Commencement Date (1 Feb 2020; the start date for later joiners) the employer
    # pays monthly 5.83 % of the Monthly Basic Wage for the first 5 years of service (service before the date included) and 8.33 % for each
    # additional year; Art. 66(1)-(2): gratuity only for service before that date (21 days' Basic Wage a year for the first 5 years, 30 days
    # after, at least 1 year of continuous employment); Art. 65(2): GPSSA-registered employees are exempt. Sources: DIFC Law Amendment Law
    # No. 1 of 2024 (difc.ae), DIFC Courts SCT 285/2023 Mapik v Mesil (quotes Art. 66), Simmons & Simmons and Addleshaw Goddard (DEWS
    # 2020 – start 1 Feb 2020, Basic Wage at the termination date for the gratuity, basic at least 50 % of the wage; the 50 % also CMS).
    'ae_dews_rate_first': {'country': 'AE', 'default': Decimal('0.0583'), 'label': 'DIFC DEWS employer contribution – first years of service (fraction of the monthly basic wage)'},
    'ae_dews_rate_after': {'country': 'AE', 'default': Decimal('0.0833'), 'label': 'DIFC DEWS employer contribution – after those years (fraction of the monthly basic wage)'},
    'ae_dews_years': {'country': 'AE', 'default': Decimal('5'), 'label': 'DIFC DEWS – years of service at the first rate'},
    'ae_dews_basic_floor': {'country': 'AE', 'default': Decimal('0.5'),
                            'label': 'DIFC DEWS – the basic wage used is at least this share of the monthly wage (fraction)'},
    'ae_dews_gratuity_days_first': {'country': 'AE', 'default': Decimal('21'), 'label': 'DIFC gratuity before DEWS – days of basic wage a year, first 5 years'},
    'ae_dews_gratuity_days_after': {'country': 'AE', 'default': Decimal('30'), 'label': 'DIFC gratuity before DEWS – days of basic wage a year, after 5 years'},
    # not verified with two sources – built with the known base and shown as "to be confirmed"
    'ae_dews_day_divisor': {'country': 'AE', 'default': Decimal('30.4167'), 'tbc': True,
                            'label': 'DIFC gratuity before DEWS – the monthly basic wage is divided by this for one day (30.4167 = 12 months ÷ 365 days, one adviser; to be confirmed)'},
    'ae_dews_last_month_to_employee': {'country': 'AE', 'default': Decimal('1'), 'tbc': True,
                                       'label': 'DIFC DEWS – the contribution of the days the final settlement pays as salary (no payroll ran for them) is paid to the employee (1 = yes, 0 = no; to be confirmed)'},
}

ADPF_NEW_LAW = __import__('datetime').date(2023, 12, 1)   # v1.62.11 (item 229): Law 18/2023 in force from its publication on 1 Dec 2023
DEWS_START = __import__('datetime').date(2020, 2, 1)       # v1.62.11 (item 229): Qualifying Scheme Commencement Date (DIFC Law No. 4 of 2020)


def adpf_terms(employee, base, on=None):
    """v1.62.11 (item 229) – the ADPF figures for one national: {'law': 'old'|'new', 'emp', 'er', 'base'} (base = contract salary,
    kept between the minimum and the maximum of Payroll › Legal rates). 'New' = joined on / after 1 Dec 2023 (first insured from then;
    a national insured in the Fund before keeps the old rate – see the open points: no flag for an earlier registration yet)."""
    joined = getattr(employee, 'emp_joined_date', None)
    old = bool(joined and joined < ADPF_NEW_LAW)
    emp = rate('ae_adpf_employee_old' if old else 'ae_adpf_employee_new', on)
    er = rate('ae_adpf_employer', on)
    lo = rate('ae_adpf_salary_min', on)
    hi = rate('ae_adpf_salary_max_old' if old else 'ae_adpf_salary_max', on)
    b = Decimal(str(base or 0))
    if b > 0:
        b = min(max(b, lo), hi)
    return {'law': 'old' if old else 'new', 'emp': emp, 'er': er, 'base': b}


def service_years(employee, on):
    """Years of service from the joining day to `on` (365.25-day years)."""
    joined = getattr(employee, 'emp_joined_date', None)
    if not joined or not on or on < joined:
        return Decimal('0')
    return Decimal((on - joined).days) / Decimal('365.25')


def dews_terms(employee, basic, wage, on=None):
    """v1.62.11 (item 229) – DIFC DEWS for one employee in a month: {'rate', 'base', 'years', 'first'}: 5.83 % of the monthly basic
    wage for the first 5 years of service (service before 1 Feb 2020 included), 8.33 % after; the basic used is at least 50 % of the
    monthly wage."""
    from CountryPack.services import rule_date
    day = on or rule_date()
    yrs = service_years(employee, day)
    first = yrs < rate('ae_dews_years', on)
    r = rate('ae_dews_rate_first' if first else 'ae_dews_rate_after', on)
    b = Decimal(str(basic or 0))
    floor = Decimal(str(wage or 0)) * rate('ae_dews_basic_floor', on)
    if floor > b:
        b = floor
    return {'rate': r, 'base': b, 'years': yrs, 'first': first}

def gpssa_terms(employee, base, on=None, scheme_emp=None, scheme_er=None, scheme_min=None, scheme_max=None):
    """v1.62.3 – the GPSSA figures for one national: {'law': 'old'|'new', 'emp': fraction, 'er': fraction, 'base': capped base}.
    Joined before 31 Oct 2023 → old law: employee 5 %, employer 12.5 %, salary 1,000–50,000. On / after → new law: employee
    11 %, employer 12.5 % (the government pays 2.5 %) below AED 20,000 and 15 % from 20,000, salary 3,000–70,000. Every
    figure comes from Payroll › Legal rates (built-in values otherwise); a statutory scheme's own rates are its new-law
    figures (scheme_*), so a company that entered different new-law rates keeps them."""
    from datetime import date as _date
    joined = getattr(employee, 'emp_joined_date', None)
    old = bool(joined and joined < _date(2023, 10, 31))
    D = lambda v: Decimal(str(v))
    if old:
        emp = rate('ae_gpssa_employee_old', on)
        er = rate('ae_gpssa_employer', on)
        lo, hi = rate('ae_gpssa_salary_min_old', on), rate('ae_gpssa_salary_max_old', on)
    else:
        emp = D(scheme_emp) if scheme_emp is not None else rate('ae_gpssa_employee_new', on)
        er = D(scheme_er) if scheme_er is not None else rate('ae_gpssa_employer', on)
        lo = D(scheme_min) if scheme_min is not None else rate('ae_gpssa_salary_min', on)
        hi = D(scheme_max) if scheme_max is not None else rate('ae_gpssa_salary_max', on)
    b = Decimal(str(base or 0))
    if b > 0:
        b = min(max(b, lo), hi)
    if not old and b >= rate('ae_gpssa_subsidy_salary_limit', on):
        er = max(er, rate('ae_gpssa_employer_full', on))
    return {'law': 'old' if old else 'new', 'emp': emp, 'er': er, 'base': b}


def plain(v):
    """Decimal without trailing zeros and never in E notation: 0.1200 → 0.12, 10000.0000 → 10000."""
    return Decimal(format(Decimal(v).normalize(), 'f'))


def _on(on):
    if on is not None:
        return on
    from CountryPack.services import rule_date
    return rule_date()


def rate_row(key, on=None):
    try:
        return M('PayrollPlus', 'LegalRate').objects.filter(key=key, effective_from__lte=_on(on)).order_by('-effective_from').first()
    except Exception:
        return None


def rate(key, on=None, default=None):
    """The value of a legal figure on a day: the newest LegalRate row in force, else the built-in default."""
    r = rate_row(key, on)
    if r is not None:
        return plain(r.value)
    if default is not None:
        return Decimal(str(default))
    k = KNOWN_RATES.get(key)
    return k['default'] if k else None


def scheme_version(scheme, on=None):
    try:
        return scheme.versions.filter(effective_from__lte=_on(on)).order_by('-effective_from').first()
    except Exception:
        return None


def scheme_on(scheme, on=None):
    """The scheme with the rates in force on the day (a copy with the version's rate fields), and the version (or None)."""
    from .models import RATE_FIELDS
    v = scheme_version(scheme, on)
    if v is None:
        return scheme, None
    s = copy.copy(scheme)
    for f in RATE_FIELDS:
        setattr(s, f, getattr(v, f))
    return s, v
