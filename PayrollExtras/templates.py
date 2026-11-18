"""v1.73.7 – salary template defaults: an item's amount, or a % of Basic (or of another payroll category), filled into an
employee's empty amounts when they get the template (Salary templates screen, Employee transfer, New employee)."""
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps

Q2 = Decimal('0.01')


def defaults(structure_id):
    TD = apps.get_model('PayrollExtras', 'TemplateDefault')
    return {d.component_id: d for d in TD.objects.filter(structure_id=structure_id)}


def _base(rows, sc, category):
    """the employee's amount of the category: amounts being entered (by code) first, else their salary lines"""
    SC = apps.get_model('PayrollManagement', 'SalaryComponent')
    codes = {c.code: c for c in SC.objects.filter(code__in=list((sc or {}).keys()))}
    entered = [Decimal(str(v)) for k, v in (sc or {}).items() if k in codes and codes[k].payroll_category == category]
    if entered:
        return sum(entered, Decimal('0'))
    if category == 'basic':
        from PayrollManagement.basic import basic_from_rows
        return basic_from_rows(rows)
    return sum((Decimal(str(r.amount or 0)) for r in rows if r.component.payroll_category == category), Decimal('0'))


def default_amount(structure_id, comp, rows, sc=None):
    d = defaults(structure_id).get(comp.pk) if structure_id else None
    if d is None:
        return None
    if d.percent is not None:
        return (_base(rows, sc, d.of_category or 'basic') * d.percent / Decimal('100')).quantize(Q2, ROUND_HALF_UP)
    if d.amount is not None:
        return Decimal(str(d.amount)).quantize(Q2)
    return None


def fill(structure, emp_ids):
    """fill the empty (0) amounts of the template's items for these employees: fixed amounts first, then percentages"""
    ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    ds = defaults(structure.pk)
    if not ds:
        return 0
    n = 0
    for eid in emp_ids:
        for pass_pct in (False, True):
            rows = list(ESS.objects.filter(employee_id=eid, is_active=True).select_related('component'))
            for r in rows:
                d = ds.get(r.component_id)
                if d is None or (d.percent is not None) != pass_pct or Decimal(str(r.amount or 0)) != 0:
                    continue
                v = default_amount(structure.pk, r.component, rows)
                if v:
                    ESS.objects.filter(pk=r.pk).update(amount=v)
                    n += 1
    return n
