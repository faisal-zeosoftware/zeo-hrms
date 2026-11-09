# v1.67.8 (fixes)
"""v1.67.8 (fixes) – item 5: no HR defaults in a company without HR; they are loaded when HR is activated.

A new company is a copy of the company template (or made by the migrations and the branch signal
`OrganisationManager.signals.create_tenant_defaults`); either way it came with HR's defaults: the default weekend and holiday
calendars (with the country's public holidays) and their branch assignments, six leave types, seven salary components and
the country's document types; Create company then ran `CountryPack.apply_defaults` (UAE leave types, labour-law rule set,
payroll components …) for every company, with or without HR.

* `strip(user)` – called by `setup_new_company` when HR is not among the apps: the untouched HR / Payroll defaults that
  nothing uses are removed and the company is marked (`CompanyOption 'hr_defaults_deferred'`).
* `CountryPack.services.apply_defaults` does nothing while HR is off (it says so).
* `ensure(user)` – called when HR is activated: in a marked company (or one without any leave type, holiday calendar and
  weekend calendar) the defaults are made the way a new company with HR gets them, then the country pack is applied.
"""
import logging
from datetime import date

from django.apps import apps
from django.utils import timezone

log = logging.getLogger(__name__)
KEY = 'hr_defaults_deferred'

DEFAULT_LEAVES = [   # the six of the branch signal (OrganisationManager.signals)
    ("Sick Leave", "SL", "paid", True, False, "sick"),
    ("Annual Leave", "AL", "paid", False, False, "annual"),
    ("Casual Leave", "CL", "paid", False, False, "casual"),
    ("Maternity Leave", "ML", "paid", False, False, "maternity"),
    ("Paternity Leave", "PL", "paid", False, False, "paternity"),
    ("Compensatory Leave", "COMP", "paid", False, True, "compensatory"),
]
DEFAULT_COMPONENTS = [   # the seven of the branch signal
    ("Basic", "addition", "BAS", "basic", "fixed", ""),
    ("HRA", "addition", "HRA", "hra", "fixed", ""),
    ("Air Ticket", "addition", "ATK", "air_ticket", "fixed", ""),
    ("Petty Cash", "addition", "PC", "other_allowance", "fixed", ""),
    ("Gratuity", "addition", "GTY", "gratuity", "variable", "monthly_gratuity_accrual"),
    ("Loan Deduction", "deduction", "LOAN", "loan", "variable", ""),
    ("Advance Salary Deduction", "deduction", "ADV", "advance_salary", "variable", ""),
]


def M(label):
    return apps.get_model(label)


def _opt():
    return M('ZeoApps.CompanyOption')


def deferred():
    try:
        return _opt().objects.filter(key=KEY).exists()
    except Exception:   # noqa: BLE001
        return False


def _unused(obj):
    try:
        from DataTools.usage import usage
        n, _k = usage(obj)
        return n == 0
    except Exception:   # noqa: BLE001
        return False


def strip(user=None):
    """Company without HR, right after it was made: remove the HR / Payroll defaults that nothing uses."""
    from django.db import transaction
    removed = {}

    def drop(label, qs, check=True):
        n = 0
        for o in list(qs):
            if check and not _unused(o):
                continue
            o.delete()
            n += 1
        if n:
            removed[label] = removed.get(label, 0) + n

    with transaction.atomic():
        # assignments first (they make the calendars "used")
        drop('holiday assignments', M('calendars.assign_holiday').objects.all(), check=False)
        drop('weekend assignments', M('calendars.assign_weekend').objects.all(), check=False)
        drop('holidays', M('calendars.holiday').objects.all())
        drop('holiday calendars', M('calendars.holiday_calendar').objects.all())
        drop('weekend calendars', M('calendars.weekend_calendar').objects.all())
        for lbl in ('calendars.LVApprovalWorkflow',):
            try:
                drop('leave approval set-ups', M(lbl).objects.all(), check=False)
            except LookupError:
                pass
        drop('leave types', M('calendars.leave_type').objects.all())
        drop('salary components', M('PayrollManagement.SalaryComponent').objects.all())
        drop('document types', M('EmpManagement.document_type').objects.all())
        _opt().objects.update_or_create(key=KEY, defaults={'value': {'at': timezone.now().isoformat(), 'by': getattr(user, 'pk', None),
                                                                     'removed': removed}})
    return removed


def _branches():
    return list(M('OrganisationManager.brnch_mstr').objects.order_by('id'))


def ensure(user=None):
    """HR switched on: the HR defaults of a new company, when the company was made without HR (or has none at all)."""
    LT, HC, WC = M('calendars.leave_type'), M('calendars.holiday_calendar'), M('calendars.weekend_calendar')
    marked = deferred()
    if not marked and (LT.objects.exists() or HC.objects.exists() or WC.objects.exists()):
        return None
    from django.db import connection
    added = []
    branches = _branches()
    first = branches[0] if branches else None
    year = date.today().year
    tag = (connection.schema_name or '')[:5]
    if not WC.objects.exists():
        w = WC.objects.create(description=f'Default Weekend Calendar {year}', calendar_code=f'WEND-{year}-{tag}', year=year,
                              monday='fullday', tuesday='fullday', wednesday='fullday', thursday='fullday', friday='fullday',
                              saturday='leave', sunday='leave')
        a = M('calendars.assign_weekend').objects.create(related_to='branch', weekend_model=w)
        a.branch.set(branches)
        added.append('weekend calendar')
    if not HC.objects.exists():
        h = HC.objects.create(calendar_title=f'Default Holiday Calendar {year}', year=year)
        a = M('calendars.assign_holiday').objects.create(related_to='branch', holiday_model=h)
        a.branch.set(branches)
        added.append('holiday calendar')   # its public holidays: CountryPack.apply_defaults (4d) below
    if not LT.objects.exists() and first is not None:
        sfx = (connection.schema_name or '')[:3].upper()
        for name, code, typ, pay_rule, comp, cat in DEFAULT_LEAVES:
            LT.objects.update_or_create(code=f'{code}-{sfx}', branch=first, defaults={
                'name': name, 'type': typ, 'unit': 'days', 'negative': False, 'description': f'Default {name}', 'allow_half_day': True,
                'include_weekend': False, 'include_holiday': False, 'use_common_workflow': True, 'include_dashboard': True,
                'enable_leave_pay_rule': pay_rule, 'is_compensatory': comp, 'leave_category': cat})
        added.append('leave types')
    SC = M('PayrollManagement.SalaryComponent')
    if not SC.objects.exists() and first is not None:
        for name, ctype, code, cat, vtype, formula in DEFAULT_COMPONENTS:
            SC.objects.get_or_create(code=code, branch=first, defaults={'name': name, 'component_type': ctype, 'payroll_category': cat,
                                                                         'component_value_type': vtype, 'formula': formula,
                                                                         'description': f'Default {name} Component', 'show_in_payslip': True})
        added.append('salary components')
    try:
        from CountryPack import services as CP
        CP.clear_cache(connection.schema_name)
        res = CP.apply_defaults(user, schema_name=connection.schema_name)
        added += [f"{a['kind']}: {a['name']}" for a in res.get('added', [])]
    except Exception:   # noqa: BLE001 – the administrator can load them in Settings › Country settings
        log.exception('country defaults when HR is activated')
    _opt().objects.filter(key=KEY).delete()
    return added
