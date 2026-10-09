"""
Project / timesheet reports for the report centre (DashboardManagement/reports.py conventions):
`def r_x(run): return columns, rows` – run.emp_q() limits to the employees the user may see (branches),
run.in_period('date') to the period; rows carry src(obj) and `_cell` drill-downs.

The DashboardManagement helpers are imported inside the functions so that DashboardManagement.reports can
import this module at any place without a circular import.
"""
from collections import defaultdict
from decimal import Decimal

from . import services as S


def _h():
    from DashboardManagement import reports as R
    return R


def _f(v):
    return round(float(v or 0), 2)


def _period(run):
    return {'from': run.date_from, 'to': run.date_to}


def _entries(run):
    """[(timesheet, values, extra)] of the visible employees in the period."""
    from ProjectManagement.models import TimeSheet
    qs = TimeSheet.objects.filter(run.emp_q()).filter(run.in_period('date')).select_related(
        'project', 'task', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id').order_by('date', 'id')
    rows = list(qs)
    ex = S.extras_for(t.pk for t in rows)
    book = S.RateBook()
    return [(t, S.entry_values(t, ex.get(t.pk), book), ex.get(t.pk)) for t in rows], book


def _projects(run):
    from django.db.models import Q
    from ProjectManagement.models import Project
    qs = Project.objects.all()
    if run.branches is not None:
        qs = qs.filter(Q(branches__in=run.branches) | Q(branches__isnull=True)).distinct()
    return qs.order_by('title')


# ------------------------------------------------------------------ detail (drill target)
def r_project_entries(run):
    """Every timesheet entry with billable flag, approval status, cost and billed value."""
    R = _h()
    rows = []
    data, _ = _entries(run)
    for t, v, e in data:
        rows.append({**R.emp_cols(t.employee), 'project': t.project.title, 'task': t.task.title if t.task_id else '', 'date': t.date,
                     'hours': _f(v['hours']), 'billable': R.nice(bool(v['billable'])), 'approval': R.nice(v['status']),
                     'cost': R.money(v['cost']), 'billed': R.money(v['bill']), 'description': t.description or '',
                     'reason': e.rejection_reason if e else '', **R.src(t), '_cell': {'project': R.src(t.project), 'task': R.src(t.task) if t.task_id else {}}})
    return R.EMP_COLS + [('project', 'Project', 'text'), ('task', 'Task', 'text'), ('date', 'Date', 'date'), ('hours', 'Hours', 'number'),
                         ('billable', 'Billable', 'text'), ('approval', 'Approval', 'text'), ('cost', 'Cost', 'number'), ('billed', 'Billed value', 'number'),
                         ('description', 'Description', 'text'), ('reason', 'Rejection reason', 'text')], rows


# ------------------------------------------------------------------ project hours
def r_project_hours(run):
    """Planned vs actual hours per project, split billable / non-billable and by approval status."""
    from ProjectManagement.models import Task
    from .models import TaskPlan
    R = _h()
    data, book = _entries(run)
    agg = defaultdict(lambda: defaultdict(Decimal))
    for t, v, _ in data:
        a = agg[t.project_id]
        a['hours'] += v['hours']
        a['billable' if v['billable'] else 'non_billable'] += v['hours']
        a[v['status']] += v['hours']
    plans = defaultdict(Decimal)
    task_project = dict(Task.objects.values_list('id', 'project_id'))
    for p in TaskPlan.objects.all():
        if p.task_id in task_project:
            plans[task_project[p.task_id]] += Decimal(p.planned_hours or 0)
    rows = []
    for p in _projects(run):
        a = agg.get(p.pk, {})
        f = book.finance(p.pk)
        if not a and p.status in ('completed', 'cancelled', 'expired'):
            continue
        base = (f.budget_hours if f and f.budget_hours else plans.get(p.pk)) or 0
        hours = a.get('hours', 0)
        rows.append({'project': p.title, 'status': R.nice(p.status), 'planned': _f(plans.get(p.pk)), 'budget_hours': _f(f.budget_hours if f else 0),
                     'hours': _f(hours), 'billable': _f(a.get('billable')), 'non_billable': _f(a.get('non_billable')),
                     'approved': _f(a.get('approved')), 'submitted': _f(a.get('submitted')), 'draft': _f(a.get('draft')), 'rejected': _f(a.get('rejected')),
                     'used_pct': S.pct(hours, base) if base else '', **R.src(p),
                     '_cell': {'hours': R.drill('project-entries', {'project': p.title}, **_period(run)),
                               'billable': R.drill('project-entries', {'project': p.title, 'billable': 'Yes'}, **_period(run)),
                               'non_billable': R.drill('project-entries', {'project': p.title, 'billable': 'No'}, **_period(run))}})
    return [('project', 'Project', 'text'), ('status', 'Status', 'text'), ('planned', 'Planned hours', 'number'),
            ('budget_hours', 'Budget hours', 'number'), ('hours', 'Actual hours', 'number'), ('billable', 'Billable hours', 'number'),
            ('non_billable', 'Non-billable hours', 'number'), ('approved', 'Approved hours', 'number'), ('submitted', 'Waiting approval', 'number'),
            ('draft', 'Draft hours', 'number'), ('rejected', 'Rejected hours', 'number'), ('used_pct', 'Hours used %', 'number')], rows


# ------------------------------------------------------------------ project cost
def r_project_cost(run):
    """Cost per project and employee: hours × hourly cost rate (approved entries at the rate frozen on approval)."""
    R = _h()
    data, _ = _entries(run)
    agg = {}
    for t, v, _ in data:
        a = agg.setdefault((t.project_id, t.employee_id), {'t': t, 'hours': Decimal(0), 'cost': Decimal(0), 'approved_cost': Decimal(0)})
        a['hours'] += v['hours']
        a['cost'] += v['cost']
        if v['status'] == 'approved':
            a['approved_cost'] += v['cost']
    rows = []
    for a in sorted(agg.values(), key=lambda a: (a['t'].project.title, R.full_name(a['t'].employee))):
        t = a['t']
        rows.append({**R.emp_cols(t.employee), 'project': t.project.title, 'hours': _f(a['hours']), 'cost': R.money(a['cost']),
                     'approved_cost': R.money(a['approved_cost']), 'estimated_cost': R.money(a['cost'] - a['approved_cost']), **R.src(t.project),
                     '_cell': {'hours': R.drill('project-entries', {'project': t.project.title, 'employee': R.full_name(t.employee)}, **_period(run)),
                               'cost': R.drill('project-entries', {'project': t.project.title, 'employee': R.full_name(t.employee)}, **_period(run))}})
    return [('project', 'Project', 'text')] + R.EMP_COLS + [('hours', 'Hours', 'number'), ('cost', 'Cost', 'number'),
                                                            ('approved_cost', 'Approved cost', 'number'), ('estimated_cost', 'Not yet approved (estimate)', 'number')], rows


# ------------------------------------------------------------------ employee cost
def r_employee_cost(run):
    """Per employee: hours, billable %, cost and billed value over all projects."""
    R = _h()
    data, book = _entries(run)
    agg = {}
    for t, v, _ in data:
        a = agg.setdefault(t.employee_id, {'e': t.employee, 'hours': Decimal(0), 'billable': Decimal(0), 'cost': Decimal(0), 'billed': Decimal(0),
                                           'projects': set()})
        a['hours'] += v['hours']
        a['billable'] += v['hours'] if v['billable'] else 0
        a['cost'] += v['cost']
        a['billed'] += v['bill']
        a['projects'].add(t.project_id)
    rows = []
    for a in sorted(agg.values(), key=lambda a: R.full_name(a['e'])):
        e = a['e']
        rows.append({**R.emp_cols(e), 'projects': len(a['projects']), 'hours': _f(a['hours']), 'billable': _f(a['billable']),
                     'billable_pct': S.pct(a['billable'], a['hours']) if a['hours'] else '', 'hour_rate': _f(S.salary_hour_rate(e, run.date_to, book.setting)),
                     'cost': R.money(a['cost']), 'billed': R.money(a['billed']), **R.src(e),
                     '_cell': {'hours': R.drill('project-entries', {'employee': R.full_name(e)}, **_period(run)),
                               'cost': R.drill('project-cost', {'employee': R.full_name(e)}, **_period(run))}})
    return R.EMP_COLS + [('projects', 'Projects', 'number'), ('hours', 'Hours', 'number'), ('billable', 'Billable hours', 'number'),
                         ('billable_pct', 'Billable %', 'number'), ('hour_rate', 'Salary cost per hour (daily basis)', 'number'),
                         ('cost', 'Cost', 'number'), ('billed', 'Billed value', 'number')], rows


# ------------------------------------------------------------------ profitability
def r_project_profitability(run):
    """Per project: billed value (T&M: bill rate × billable hours; fixed: contract value), cost, profit, margin, budget used."""
    R = _h()
    data, book = _entries(run)
    agg = defaultdict(lambda: defaultdict(Decimal))
    for t, v, _ in data:
        a = agg[t.project_id]
        a['hours'] += v['hours']
        a['cost'] += v['cost']
        a['bill'] += v['bill']
    rows = []
    for p in _projects(run):
        a = agg.get(p.pk, {})
        f = book.finance(p.pk)
        if not a and not f:
            continue
        bt = f.billing_type if f else 'time_and_material'
        billed = Decimal(f.contract_value or 0) if bt == 'fixed' else (Decimal(0) if bt == 'non_billable' else a.get('bill', Decimal(0)))
        cost = a.get('cost', Decimal(0))
        profit = billed - cost
        budget = Decimal(f.budget_amount or 0) if f else Decimal(0)
        rows.append({'project': p.title, 'customer': f.customer_name if f else '', 'billing': R.nice(bt), 'status': R.nice(p.status),
                     'contract': R.money(f.contract_value if f else 0), 'budget': R.money(budget), 'hours': _f(a.get('hours')),
                     'cost': R.money(cost), 'billed': R.money(billed), 'profit': R.money(profit),
                     'margin': S.pct(profit, billed) if billed else '', 'budget_used': S.pct(cost, budget) if budget else '', **R.src(p),
                     '_cell': {'hours': R.drill('project-entries', {'project': p.title}, **_period(run)),
                               'cost': R.drill('project-cost', {'project': p.title}, **_period(run))}})
    return [('project', 'Project', 'text'), ('customer', 'Customer', 'text'), ('billing', 'Billing', 'text'), ('status', 'Status', 'text'),
            ('contract', 'Contract value', 'number'), ('budget', 'Cost budget', 'number'), ('hours', 'Hours', 'number'), ('cost', 'Cost', 'number'),
            ('billed', 'Billed value', 'number'), ('profit', 'Profit', 'number'), ('margin', 'Margin %', 'number'),
            ('budget_used', 'Budget used %', 'number')], rows


# ------------------------------------------------------------------ approvals
def r_timesheet_approvals(run):
    """Every timesheet entry with its approval status, approver, dates, waiting days and corrections."""
    from UserManagement.models import CustomUser
    R = _h()
    data, _ = _entries(run)
    uids = {e.approver_user_id for _, _, e in data if e and e.approver_user_id}
    users = {u.pk: u for u in CustomUser.objects.filter(pk__in=uids)}
    rows = []
    for t, v, e in data:
        st = v['status']
        waited = (run.today - e.submitted_at.date()).days if e and st == 'submitted' and e.submitted_at else ''
        rows.append({**R.emp_cols(t.employee), 'project': t.project.title, 'task': t.task.title if t.task_id else '', 'date': t.date,
                     'hours': _f(v['hours']), 'billable': R.nice(bool(v['billable'])), 'status': R.nice(st),
                     'submitted': e.submitted_at.date() if e and e.submitted_at else '',
                     'approver': (users[e.approver_user_id].username if e and e.approver_user_id in users else ''),
                     'decided': e.approved_at.date() if e and e.approved_at else '', 'days_waiting': waited,
                     'reason': e.rejection_reason if e else '', 'corrections': len(e.corrected_from or []) if e else 0, **R.src(t)})
    return R.EMP_COLS + [('project', 'Project', 'text'), ('task', 'Task', 'text'), ('date', 'Date', 'date'), ('hours', 'Hours', 'number'),
                         ('billable', 'Billable', 'text'), ('status', 'Approval', 'text'), ('submitted', 'Submitted on', 'date'),
                         ('approver', 'Approved / rejected by', 'text'), ('decided', 'Decided on', 'date'), ('days_waiting', 'Days waiting', 'number'),
                         ('reason', 'Rejection reason', 'text'), ('corrections', 'Corrections', 'number')], rows


# ------------------------------------------------------------------ billable vs non-billable
def r_billable(run):
    """Per employee and project: billable and non-billable hours and the billable %."""
    R = _h()
    data, _ = _entries(run)
    agg = {}
    for t, v, _ in data:
        a = agg.setdefault((t.employee_id, t.project_id), {'t': t, 'b': Decimal(0), 'n': Decimal(0), 'bill': Decimal(0)})
        a['b' if v['billable'] else 'n'] += v['hours']
        a['bill'] += v['bill']
    rows = []
    for a in sorted(agg.values(), key=lambda a: (R.full_name(a['t'].employee), a['t'].project.title)):
        t = a['t']
        tot = a['b'] + a['n']
        f = {'project': t.project.title, 'employee': R.full_name(t.employee)}
        rows.append({**R.emp_cols(t.employee), 'project': t.project.title, 'billable': _f(a['b']), 'non_billable': _f(a['n']), 'hours': _f(tot),
                     'billable_pct': S.pct(a['b'], tot) if tot else '', 'billed': R.money(a['bill']), **R.src(t.project),
                     '_cell': {'billable': R.drill('project-entries', {**f, 'billable': 'Yes'}, **_period(run)),
                               'non_billable': R.drill('project-entries', {**f, 'billable': 'No'}, **_period(run)),
                               'hours': R.drill('project-entries', f, **_period(run))}})
    return R.EMP_COLS + [('project', 'Project', 'text'), ('billable', 'Billable hours', 'number'), ('non_billable', 'Non-billable hours', 'number'),
                         ('hours', 'Total hours', 'number'), ('billable_pct', 'Billable %', 'number'), ('billed', 'Billed value', 'number')], rows
