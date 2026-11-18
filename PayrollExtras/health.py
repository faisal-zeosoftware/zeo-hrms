"""v1.73.7 – Payroll health check (before a run): what would make a wrong or missing payslip, each with a link to fix it."""
from collections import defaultdict
from decimal import Decimal

from django.apps import apps

M = apps.get_model


def check(employees, run_id=None):
    """employees: active emp_master rows (the user's branches). Returns [{code, text, employee_id, employee, link}]"""
    from PayrollManagement.signals import structure_for
    from PayrollManagement.basic import basic_from_rows
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    PS = M('PayrollManagement', 'PayStructure')
    rows = defaultdict(list)
    ids = [e.pk for e in employees]
    for r in ESS.objects.filter(employee_id__in=ids, is_active=True).select_related('component'):
        rows[r.employee_id].append(r)
    rules = list(PS.objects.prefetch_related('branch', 'department', 'category', 'designation'))
    out = []

    def add(code, text, e, link):
        out.append({'code': code, 'text': text, 'employee_id': e.pk if e else None,
                    'employee': (f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip() + f' ({e.emp_code})') if e else '', 'link': link})
    for e in employees:
        rs = rows.get(e.pk, [])
        if not rs:
            add('no_salary', 'No salary – the employee has no active salary item.', e, f'/main-sidebar/salary-options/employee-salary?employee={e.pk}')
            continue
        if not basic_from_rows(rs):
            add('no_basic', 'No Basic – gratuity, leave salary and the unpaid-leave deduction need a Basic-category item.', e,
                f'/main-sidebar/salary-options/employee-salary?employee={e.pk}')
        for r in rs:
            c = r.component
            if c.component_value_type == 'fixed' and not (c.formula or '').strip() and Decimal(str(r.amount or 0)) == 0:
                add('zero_amount', f'{c.name}: the amount is 0.', e, f'/main-sidebar/salary-options/employee-salary?employee={e.pk}')
            if c.payroll_category == 'basic' and c.component_type == 'deduction':
                add('basic_deduction', f'{c.name} is a deduction marked Basic.', e, '/main-sidebar/salary-options/salary')
        # the payroll rule: none, or two equally specific ones
        scores = []
        for ps in rules:
            br = {b.pk for b in ps.branch.all()}
            if br and e.emp_branch_id_id not in br:
                continue
            sc, ok = (1 if br else 0), True
            for rel, val in (('category', e.emp_ctgry_id_id), ('department', e.emp_dept_id_id), ('designation', e.emp_desgntn_id_id)):
                s = {x.pk for x in getattr(ps, rel).all()}
                if s:
                    if val not in s:
                        ok = False
                        break
                    sc += 2
            if ok:
                scores.append(sc)
        if rules and not scores:
            add('no_rule', 'No payroll rule matches – the payroll uses the calendar month and its days.', e, '/main-sidebar/salary-options/pay-structure')
        elif len(scores) > 1 and sorted(scores)[-1] == sorted(scores)[-2]:
            add('two_rules', 'Two payroll rules match equally – the first one is used. Make one more specific.', e, '/main-sidebar/salary-options/pay-structure')
    if run_id:
        try:
            from .paid import skips
            emps = {e.pk: e for e in employees}
            for s in skips(run_id):
                add('paid_already', 'Left out of the run: ' + s['reason'], emps.get(s['employee_id']), f'/main-sidebar/salary-options/payroll-runs')
        except Exception:   # noqa: BLE001
            pass
    # categories not used by any item / items whose category is inactive
    try:
        from .categories import rows as cat_rows
        used = set(M('PayrollManagement', 'SalaryComponent').objects.values_list('payroll_category', flat=True))
        for c in cat_rows():
            if not c.active and c.key in used:
                out.append({'code': 'inactive_category', 'text': f'Payroll category {c.name} is inactive but salary items use it.', 'employee_id': None,
                            'employee': '', 'link': '/main-sidebar/salary-options/payroll-categories'})
    except Exception:   # noqa: BLE001
        pass
    return out
