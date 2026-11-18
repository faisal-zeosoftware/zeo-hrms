"""v1.73.7 – one rule for "Basic" everywhere (payslip formulas, gratuity / end of service, leave salary and encashment, reports):
the sum of the employee's active salary lines whose payroll category is Basic (additions only).

Before, the payslip took the first Basic line by id, gratuity and encashment the latest or the first one, and the reports
preferred an item named "Basic" – an employee with two Basic items (e.g. "Basic – Hourly" and "Basic adjustment") got different
Basic figures in different places. Only when an employee has no Basic-category line at all is an item named / coded Basic used
(the old report rule), so companies that never set the category keep their figures."""
from decimal import Decimal

from django.apps import apps

ZERO = Decimal('0.00')
NAMES = ('basic', 'basic salary')
CODES = ('BAS', 'BASIC')


def _is_basic_cat(comp):
    return getattr(comp, 'payroll_category', '') == 'basic' and getattr(comp, 'component_type', '') != 'deduction'


def _is_basic_name(comp):
    return getattr(comp, 'component_type', '') != 'deduction' and (
        (comp.code or '').upper() in CODES or (comp.name or '').strip().lower() in NAMES)


def basic_from_rows(rows):
    """rows: EmployeeSalaryStructure lines (active ones) with .component and .amount"""
    rows = [r for r in rows if getattr(r, 'component', None) is not None]
    cat = [r for r in rows if _is_basic_cat(r.component)]
    use = cat or [r for r in rows if _is_basic_name(r.component)]
    return sum((Decimal(str(r.amount or 0)) for r in use), ZERO)


def basic_rows(rows):
    rows = [r for r in rows if getattr(r, 'component', None) is not None]
    return [r for r in rows if _is_basic_cat(r.component)] or [r for r in rows if _is_basic_name(r.component)]


def basic_of(employee_or_id):
    ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    eid = getattr(employee_or_id, 'pk', employee_or_id)
    return basic_from_rows(ESS.objects.filter(employee_id=eid, is_active=True).select_related('component'))


def basic_map(emp_ids):
    ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    by = {}
    for r in ESS.objects.filter(employee_id__in=list(emp_ids), is_active=True).select_related('component'):
        by.setdefault(r.employee_id, []).append(r)
    return {e: basic_from_rows(by.get(e, [])) for e in emp_ids}


# ---------------------------------------------------------------- v1.73.7 the bases the payroll categories' ticks define
def _keys(flag):
    try:
        from PayrollExtras.categories import keys_with
        return keys_with(flag)
    except Exception:   # noqa: BLE001
        return {'basic'}


def base_from_rows(rows, flag):
    """the salary the ticked categories make: gratuity / leave_salary / encashment / pension. With only Basic ticked (the default)
    this is basic_from_rows – the same figure as before."""
    keys = _keys(flag)
    rows = [r for r in rows if getattr(r, 'component', None) is not None]
    total = sum((Decimal(str(r.amount or 0)) for r in rows if r.component.payroll_category in keys - {'basic'}
                 and r.component.component_type != 'deduction'), ZERO)
    if 'basic' in keys:
        total += basic_from_rows(rows)
    return total


def base_of(employee_or_id, flag):
    ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    eid = getattr(employee_or_id, 'pk', employee_or_id)
    return base_from_rows(ESS.objects.filter(employee_id=eid, is_active=True).select_related('component'), flag)


def base_map(emp_ids, flag):
    ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    by = {}
    for r in ESS.objects.filter(employee_id__in=list(emp_ids), is_active=True).select_related('component'):
        by.setdefault(r.employee_id, []).append(r)
    return {e: base_from_rows(by.get(e, []), flag) for e in emp_ids}
