"""v1.50.0 – payroll what-if: a trial payroll with proposed salary changes, compared with the payroll as it is.

The trial uses the real payroll calculation (PayrollManagement process_payroll through a payroll run with the chosen
employees) inside a database transaction that is always rolled back, so nothing of it stays: no run, no payslip, no
loan or advance deduction, no approval. E-mails are held back while it runs. Applying a scenario writes the new amounts to
the employees' salary components; the change is kept in the salary revision history.

A rule: {component: id, change: percent|amount|set|add, value, scope: all|department|branch|designation|employees, ids: [..]}
  percent – the component goes up (or down) by value % ; amount – by value ; set – becomes value ;
  add – the employees get the component (a new allowance) with value as the amount.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

M = apps.get_model
Q2 = Decimal('0.01')
MAX_EMPLOYEES = 500
CHANGES = ('percent', 'amount', 'set', 'add')
SCOPES = ('all', 'department', 'branch', 'designation', 'employees')


class WhatIfError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


class _Rollback(Exception):
    pass


def d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:
        return Decimal('0')


def q(v):
    return d(v).quantize(Q2, rounding=ROUND_HALF_UP)


def check_rules(rules):
    SC = M('PayrollManagement', 'SalaryComponent')
    out = []
    for i, r in enumerate(rules or []):
        if not isinstance(r, dict):
            raise WhatIfError(f'Change {i + 1}: not valid.')
        comp = SC.objects.filter(pk=r.get('component')).first()
        if comp is None:
            raise WhatIfError(f'Change {i + 1}: choose the salary component.')
        ch = r.get('change') or 'percent'
        if ch not in CHANGES:
            raise WhatIfError(f'Change {i + 1}: choose how the component changes.')
        sc = r.get('scope') or 'all'
        if sc not in SCOPES:
            raise WhatIfError(f'Change {i + 1}: choose who it is for.')
        ids = [int(x) for x in (r.get('ids') or []) if str(x).isdigit()]
        if sc != 'all' and not ids:
            raise WhatIfError(f'Change {i + 1}: choose the {sc if sc != "employees" else "employees"}.')
        val = d(r.get('value'))
        if ch == 'percent' and not (-90 <= val <= 500):
            raise WhatIfError(f'Change {i + 1}: a percentage between -90 and 500.')
        if ch in ('set', 'add') and val < 0:
            raise WhatIfError(f'Change {i + 1}: the amount cannot be negative.')
        if comp.component_value_type == 'formula' and ch != 'add':
            # a formula component follows its formula; changing its amount would be overwritten by the calculation
            raise WhatIfError(f'Change {i + 1}: "{comp.name}" is calculated by a formula – change the components it uses instead.')
        out.append({'component': comp.pk, 'component_name': comp.name, 'change': ch, 'value': str(val), 'scope': sc, 'ids': ids})
    return out


def _in_scope(e, r):
    sc, ids = r['scope'], r['ids']
    return (sc == 'all' or (sc == 'department' and e.emp_dept_id_id in ids) or (sc == 'branch' and e.emp_branch_id_id in ids)
            or (sc == 'designation' and e.emp_desgntn_id_id in ids) or (sc == 'employees' and e.pk in ids))


def planned(rules, employees):
    """[(employee, component, old amount, new amount, existing row or None)] the rules give."""
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    SC = M('PayrollManagement', 'SalaryComponent')
    comps = {c.pk: c for c in SC.objects.filter(pk__in={r['component'] for r in rules})}
    rows = {(x.employee_id, x.component_id): x for x in ESS.objects.filter(employee__in=employees, component_id__in=comps.keys())}
    out = []
    for e in employees:
        for r in rules:
            c = comps[r['component']]
            if not _in_scope(e, r):
                continue
            row = rows.get((e.pk, c.pk))
            have = row is not None and row.is_active
            old = q(row.amount) if have else Decimal('0')
            v = d(r['value'])
            if r['change'] == 'add':
                new = q(v)
            elif not have:
                continue   # the employee does not have this component – only "add" gives it
            elif r['change'] == 'percent':
                new = q(old * (1 + v / 100))
            elif r['change'] == 'amount':
                new = q(old + v)
            else:
                new = q(v)
            if new < 0:
                new = Decimal('0')
            if new != old or not have:
                out.append((e, c, old, new, row))
                if row is not None:
                    row.amount, row.is_active = new, True   # a later rule on the same component builds on this one
                else:
                    rows[(e.pk, c.pk)] = row = ESS(employee=e, component=c, amount=new, is_active=True)
                    out[-1] = (e, c, old, new, None)
    return out


def _write(changes, user=None, remarks='', period=None):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    seen = {}
    for e, c, old, new, row in changes:
        seen[(e.pk, c.pk)] = (e, c, new)
    for (eid, cid), (e, c, new) in seen.items():
        row = ESS.objects.filter(employee_id=eid, component_id=cid).first()
        if row is None:
            row = ESS(employee=e, component=c)
        row.amount, row.is_active = new, True
        row._revised_by, row._remarks, row._effective_period = user, remarks[:255], period
        row.save()


def _employees(ids, visible=None):
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_branch_id', 'emp_desgntn_id')
    if ids:
        qs = qs.filter(pk__in=ids)
    if visible is not None:
        qs = qs.filter(pk__in=visible)
    qs = qs.filter(salary_structures__is_active=True).distinct().order_by('emp_code')
    n = qs.count()
    if n > MAX_EMPLOYEES:
        raise WhatIfError(f'{n} employees – a trial payroll takes at most {MAX_EMPLOYEES}. Choose a branch or department.')
    return list(qs)


def _trial(month, employees, changes):
    """{employee_id: {gross, deductions, net, components: {name: amount}}} of a payroll calculated and thrown away."""
    from django.test import override_settings
    Run = M('PayrollManagement', 'PayrollRun')
    PS = M('PayrollManagement', 'Payslip')
    PC = M('PayrollManagement', 'PayslipComponent')
    out = {}
    try:
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'), transaction.atomic():
            if changes:
                _write(changes, remarks='what-if trial (rolled back)')
            run = Run.objects.create(name='What-if trial (not saved)', month=month.month, year=month.year, status='pending')
            run.employees.add(*[e.pk for e in employees])
            slips = {p.pk: p for p in PS.objects.filter(payroll_run=run)}
            for p in slips.values():
                out[p.employee_id] = {'gross': q(p.gross_salary), 'deductions': q(p.total_deductions), 'net': q(p.net_salary), 'components': {}}
            for c in PC.objects.filter(payslip_id__in=slips.keys()).select_related('component'):
                k = out[slips[c.payslip_id].employee_id]['components']
                key = f'{c.component.component_type}:{c.component.name}'
                k[key] = q(k.get(key, 0) + d(c.amount))
            raise _Rollback()
    except _Rollback:
        pass
    return out


def _name(e):
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)


def simulate(sc, visible=None):
    rules = check_rules(sc.rules)
    emps = _employees(sc.employee_ids, visible)
    if not emps:
        raise WhatIfError('No active employee with a salary in this choice.')
    changes = planned(rules, emps)
    base = _trial(sc.month, emps, [])
    new = _trial(sc.month, emps, changes)
    T = lambda: {'gross': Decimal('0'), 'deductions': Decimal('0'), 'net': Decimal('0')}  # noqa: E731
    tb, tn = T(), T()
    by_dept = defaultdict(lambda: [Decimal('0'), Decimal('0'), 0, ''])
    by_comp = defaultdict(lambda: [Decimal('0'), Decimal('0')])
    changed_ids = {e.pk for e, *_ in changes}
    rows, missing = [], []
    for e in emps:
        b, n = base.get(e.pk), new.get(e.pk)
        if b is None or n is None:
            missing.append(f'{e.emp_code} {_name(e)}')
            continue
        for k in tb:
            tb[k] += b[k]
            tn[k] += n[k]
        dept = e.emp_dept_id.dept_name if e.emp_dept_id_id else '–'
        g = by_dept[dept]
        g[0] += b['gross']; g[1] += n['gross']; g[2] += 1; g[3] = dept
        for key in set(b['components']) | set(n['components']):
            by_comp[key][0] += b['components'].get(key, 0)
            by_comp[key][1] += n['components'].get(key, 0)
        diff = n['gross'] - b['gross']
        rows.append({'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), 'department': dept,
                     'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'changed': e.pk in changed_ids,
                     'base_gross': str(b['gross']), 'new_gross': str(n['gross']), 'base_net': str(b['net']), 'new_net': str(n['net']),
                     'diff': str(q(diff)), 'pct': str(q(diff * 100 / b['gross'])) if b['gross'] else ''})
    rows.sort(key=lambda r: (-d(r['diff']), r['code']))
    diff = {k: str(q(tn[k] - tb[k])) for k in tb}
    res = {
        'month': sc.month.isoformat(), 'employees': len(rows), 'changed': len(changed_ids),
        'base': {k: str(q(v)) for k, v in tb.items()}, 'new': {k: str(q(v)) for k, v in tn.items()}, 'diff': diff,
        'yearly_diff': str(q(d(diff['gross']) * 12)),
        'pct': str(q(d(diff['gross']) * 100 / tb['gross'])) if tb['gross'] else '',
        'by_department': sorted([{'name': v[3], 'employees': v[2], 'base': str(q(v[0])), 'new': str(q(v[1])), 'diff': str(q(v[1] - v[0]))}
                                 for v in by_dept.values()], key=lambda x: -d(x['diff'])),
        'by_component': sorted([{'kind': k.split(':', 1)[0], 'name': k.split(':', 1)[1], 'base': str(q(v[0])), 'new': str(q(v[1])),
                                 'diff': str(q(v[1] - v[0]))} for k, v in by_comp.items()], key=lambda x: (x['kind'], x['name'])),
        'changes': [{'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), 'component': c.name, 'old': str(old), 'new': str(new)}
                    for e, c, old, new, _ in changes][:2000],
        'rows': rows, 'missing': missing[:50], 'rules': rules,
    }
    sc.rules = rules
    sc.result = res
    sc.calculated_at = timezone.now()
    sc.save(update_fields=['rules', 'result', 'calculated_at'])
    return res


def apply(sc, user, visible=None):
    if sc.status == 'applied':
        raise WhatIfError('This scenario is already applied.')
    rules = check_rules(sc.rules)
    emps = _employees(sc.employee_ids, visible)
    changes = planned(rules, emps)
    if not changes:
        raise WhatIfError('The scenario changes nobody\'s salary.')
    eff = sc.effective_from or sc.month
    with transaction.atomic():
        _write(changes, user=user, remarks=f'What-if "{sc.name}"', period=eff.strftime('%B%Y'))
        sc.status, sc.applied_at, sc.applied_by_id = 'applied', timezone.now(), getattr(user, 'pk', None)
        sc.save(update_fields=['status', 'applied_at', 'applied_by_id'])
    return len({(e.pk, c.pk) for e, c, *_ in changes})


def scenario_json(sc, full=False):
    r = sc.result or {}
    out = {'id': sc.id, 'name': sc.name, 'month': sc.month, 'status': sc.status, 'rules': sc.rules, 'employee_ids': sc.employee_ids,
           'effective_from': sc.effective_from, 'applied_at': sc.applied_at, 'calculated_at': sc.calculated_at, 'created_at': sc.created_at,
           'summary': {k: r.get(k) for k in ('employees', 'changed', 'diff', 'yearly_diff', 'pct')} if r else None}
    if full:
        out['result'] = r
    return out
