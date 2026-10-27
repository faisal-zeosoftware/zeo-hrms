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
}


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
