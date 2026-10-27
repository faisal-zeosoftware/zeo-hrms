"""v1.50.0 – multi-level cost allocation: an employee's payroll cost of a month split over projects, cost centres and
departments, by fixed percentages or by the timesheet hours of the month. What is not allocated stays on the employee's own
cost centre (Organisation → cost centre of the employee) or, without one, the department.

The cost of an employee is the gross pay of the month's payslips (processed, approved or paid runs). The result can be
exported (CSV) or posted to accounting as a reclassification journal: the cost leaves the employee's department and goes
to each project / cost centre (analytic tags `project` / `cost_center`).
"""
import calendar
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db.models import Q

M = apps.get_model
Q2 = Decimal('0.01')
KINDS = ('project', 'cost_center', 'department')


class AllocError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:
        return Decimal('0')


def q(v):
    return d(v).quantize(Q2, rounding=ROUND_HALF_UP)


def _model(app, name):
    try:
        return M(app, name)
    except LookupError:
        return None


def target_names(pairs):
    """{(kind, id): name} for the targets."""
    out = {}
    by = defaultdict(set)
    for k, i in pairs:
        by[k].add(i)
    P = _model('ProjectManagement', 'Project')
    if by.get('project') and P:
        out.update({('project', p.pk): p.title for p in P.objects.filter(pk__in=by['project'])})
    CC = _model('OrgStructure', 'CostCenter')
    if by.get('cost_center') and CC:
        out.update({('cost_center', c.pk): f'{c.code} {c.name}'.strip() for c in CC.objects.filter(pk__in=by['cost_center'])})
    D = M('OrganisationManager', 'dept_master')
    if by.get('department'):
        out.update({('department', x.pk): x.dept_name for x in D.objects.filter(pk__in=by['department'])})
    return out


def check_lines(basis, lines):
    out, total = [], Decimal('0')
    for i, ln in enumerate(lines or []):
        k = (ln or {}).get('kind')
        if k not in KINDS:
            raise AllocError(f'Line {i + 1}: choose a project, cost centre or department.')
        try:
            pk = int(ln.get('id'))
        except (TypeError, ValueError):
            raise AllocError(f'Line {i + 1}: choose the {k.replace("_", " ")}.')
        pct = d(ln.get('percent'))
        if basis == 'percent' and not (0 < pct <= 100):
            raise AllocError(f'Line {i + 1}: a percentage between 0 and 100.')
        total += pct
        out.append({'kind': k, 'id': pk, 'percent': str(q(pct)) if basis == 'percent' else ''})
    if basis == 'percent':
        if not out:
            raise AllocError('Add at least one line.')
        if total > 100:
            raise AllocError(f'The percentages add up to {q(total)} – at most 100 (the rest stays on the employee\'s own cost centre).')
    names = target_names({(x['kind'], x['id']) for x in out})
    for x in out:
        if (x['kind'], x['id']) not in names:
            raise AllocError(f'{x["kind"].replace("_", " ").title()} {x["id"]} not found.')
    return out


def rule_for(emp_id, month_end, rules=None):
    from .models import CostAllocation
    qs = rules if rules is not None else CostAllocation.objects.filter(employee_id=emp_id)
    for r in sorted([r for r in qs if r.employee_id == emp_id], key=lambda r: r.valid_from or date.min, reverse=True):
        if (r.valid_from is None or r.valid_from <= month_end) and (r.valid_until is None or r.valid_until >= month_end.replace(day=1)):
            return r
    return None


def _hours(s):
    try:
        h, m = str(s or '0:0').split(':')[:2]
        return Decimal(int(h)) + Decimal(int(m)) / 60
    except (ValueError, TypeError):
        return Decimal('0')


def timesheet_hours(emp_ids, start, end):
    """{emp_id: {project_id: hours}} – rejected entries left out."""
    TS = _model('ProjectManagement', 'TimeSheet')
    if TS is None:
        return {}
    ex = {}
    TE = _model('ProjectControl', 'TimesheetExtra')
    rows = list(TS.objects.filter(employee_id__in=emp_ids, date__range=(start, end)).values('id', 'employee_id', 'project_id', 'time_spent'))
    if TE is not None and rows:
        ex = {x.timesheet_id: x for x in TE.objects.filter(timesheet_id__in=[r['id'] for r in rows])}
    out = defaultdict(lambda: defaultdict(Decimal))
    for r in rows:
        x = ex.get(r['id'])
        if x is not None and x.approval_status == 'rejected':
            continue
        h = d(x.hours) if x is not None and x.hours else _hours(r['time_spent'])
        if h > 0:
            out[r['employee_id']][r['project_id']] += h
    return out


def home_target(emp):
    """The employee's own cost centre, or the department."""
    EO = _model('OrgStructure', 'EmployeeOrg')
    if EO is not None:
        o = EO.objects.filter(employee_id=emp.pk, cost_center__isnull=False).first()
        if o is not None:
            return ('cost_center', o.cost_center_id)
    return ('department', emp.emp_dept_id_id) if emp.emp_dept_id_id else ('department', 0)


def compute(month, emp_ok=None):
    """Allocation of the month: {month, rows, targets, totals, runs}."""
    start = month.replace(day=1)
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    PS = M('PayrollManagement', 'Payslip')
    slips = PS.objects.filter(payroll_run__month=month.month, payroll_run__year=month.year,
                              payroll_run__status__in=('processed', 'approved', 'paid')).select_related(
        'employee', 'employee__emp_dept_id', 'employee__emp_branch_id', 'payroll_run')
    cost = defaultdict(Decimal)
    emps, runs = {}, set()
    for p in slips:
        if emp_ok is not None and not emp_ok(p.employee_id):
            continue
        cost[p.employee_id] += d(p.gross_salary)
        emps[p.employee_id] = p.employee
        runs.add(p.payroll_run_id)
    from .models import CostAllocation
    rules = list(CostAllocation.objects.filter(employee_id__in=emps.keys()))
    hours = timesheet_hours(list(emps.keys()), start, end)
    try:   # v1.58.0 concurrent assignments: their share of the month when the employee has no cost rule of its own
        from ZeoHR.matrix import cost_splits
        asg = cost_splits(list(emps.keys()), start, end)
    except Exception:
        asg = {}
    rows, pairs = [], set()
    for eid, total in cost.items():
        e = emps[eid]
        r = rule_for(eid, end, rules)
        home = home_target(e)
        splits = []
        if r is not None and r.basis == 'percent':
            for ln in r.lines or []:
                splits.append([ln['kind'], ln['id'], d(ln['percent'])])
        elif r is None and asg.get(eid):
            for kind, pk, pct, _, num in asg[eid]:
                splits.append([kind, pk, pct, None, num])
        elif r is not None and r.basis == 'timesheet':
            hs = hours.get(eid) or {}
            tot_h = sum(hs.values(), Decimal('0'))
            for pid, h in sorted(hs.items()):
                splits.append(['project', pid, (h * 100 / tot_h) if tot_h else Decimal('0'), h])
        alloc = Decimal('0')
        out = []
        for sp in splits:
            amt = q(total * sp[2] / 100)
            alloc += amt
            out.append({'kind': sp[0], 'id': sp[1], 'percent': str(q(sp[2])), 'amount': str(amt), 'hours': str(q(sp[3])) if len(sp) > 3 and sp[3] is not None else '',
                        **({'assignment': sp[4]} if len(sp) > 4 else {})})
            pairs.add((sp[0], sp[1]))
        rest = q(total - alloc)
        if rest:
            out.append({'kind': home[0], 'id': home[1], 'percent': str(q(rest * 100 / total)) if total else '0', 'amount': str(rest), 'hours': '',
                        'home': True})
            pairs.add(home)
        rows.append({'employee_id': eid, 'code': e.emp_code, 'name': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                     'department': e.emp_dept_id.dept_name if e.emp_dept_id_id else '', 'department_id': e.emp_dept_id_id,
                     'branch_id': e.emp_branch_id_id, 'cost': str(q(total)),
                     'basis': r.basis if r else ('assignments' if asg.get(eid) else ''), 'rule_id': r.pk if r else None, 'splits': out})
    names = target_names(pairs)
    totals = defaultdict(lambda: {'amount': Decimal('0'), 'employees': 0})
    for row in rows:
        for sp in row['splits']:
            sp['name'] = names.get((sp['kind'], sp['id'])) or ('No department' if sp['kind'] == 'department' else '?')
            t = totals[(sp['kind'], sp['id'])]
            t['amount'] += d(sp['amount'])
            t['employees'] += 1
            t['name'] = sp['name']
    rows.sort(key=lambda r: r['code'] or '')
    targets = sorted([{'kind': k, 'id': i, 'name': v['name'], 'amount': str(q(v['amount'])), 'employees': v['employees']}
                      for (k, i), v in totals.items()], key=lambda x: (KINDS.index(x['kind']), -d(x['amount'])))
    return {'month': start.isoformat(), 'rows': rows, 'targets': targets, 'total': str(q(sum(cost.values(), Decimal('0')))),
            'allocated': str(q(sum((d(sp['amount']) for r in rows for sp in r['splits'] if not sp.get('home')), Decimal('0')))),
            'runs': sorted(runs), 'employees': len(rows)}


def post(month, user=None, emp_ok=None):
    from ZeoAccounting.services import build_cost_allocation
    res = compute(month, emp_ok)
    if not res['rows']:
        raise AllocError('No processed payroll in this month.')
    return build_cost_allocation(month, res['rows'], user)


def rule_json(r, names=None):
    names = names or target_names({(x['kind'], x['id']) for x in r.lines or []})
    E = M('EmpManagement', 'emp_master')
    e = E.objects.filter(pk=r.employee_id).first()
    return {'id': r.pk, 'employee_id': r.employee_id, 'employee': ' '.join(x for x in ((e.emp_first_name, e.emp_last_name) if e else ()) if x),
            'code': e.emp_code if e else '', 'basis': r.basis, 'valid_from': r.valid_from, 'valid_until': r.valid_until, 'note': r.note,
            'lines': [{**x, 'name': names.get((x['kind'], x['id']), '?')} for x in r.lines or []]}


def options():
    out = {'project': [], 'cost_center': [], 'department': []}
    P = _model('ProjectManagement', 'Project')
    if P:
        out['project'] = [{'id': p.pk, 'name': p.title} for p in P.objects.filter(is_active=True).order_by('title')[:500]]
    CC = _model('OrgStructure', 'CostCenter')
    if CC:
        out['cost_center'] = [{'id': c.pk, 'name': f'{c.code} {c.name}'.strip()} for c in CC.objects.all().order_by('code')[:500]]
    D = M('OrganisationManager', 'dept_master')
    out['department'] = [{'id': x.pk, 'name': x.dept_name} for x in D.objects.all().order_by('dept_name')[:500]]
    return out
