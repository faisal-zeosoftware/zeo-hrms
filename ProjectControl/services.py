"""
Project control rules in one place: who sees what, timesheet validation, hours, billable default,
hourly cost rate from the salary structure, approval and the project / employee summaries.

Used by ProjectControl.views, ProjectControl.reports and ProjectManagement.views (timesheet API).
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone

D0 = Decimal('0')
MAX_DAY_HOURS = Decimal('24')
LONG_TIMER_HOURS = Decimal('12')
# payroll categories that are not fixed monthly pay
VARIABLE_CATEGORIES = {'overtime', 'bonus', 'commission', 'leave_encashment', 'gratuity', 'air_ticket', 'advance_salary',
                       'loan', 'pf', 'esi', 'tax'}


def _pm():
    from ProjectManagement.models import Project, Task, TimeSheet
    return Project, Task, TimeSheet


def q2(v):
    return Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def q4(v):
    return Decimal(v or 0).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ hours
def hours_of(text):
    """'06:30' → 6.5, '6.5' → 6.5 (Decimal, 2 places)."""
    if text is None:
        return D0
    s = str(text).strip()
    m = re.fullmatch(r'(\d+):(\d{1,2})(?::\d{1,2})?', s)
    if m:
        return q2(Decimal(int(m.group(1))) + Decimal(int(m.group(2))) / Decimal(60))
    try:
        return q2(Decimal(s))
    except Exception:
        return D0


def to_hhmm(hours):
    mins = int((Decimal(hours or 0) * 60).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    mins = max(mins, 0)
    return f'{mins // 60:02d}:{mins % 60:02d}'


def week_start(d):
    return d - timedelta(days=d.weekday())


def local_today():
    return timezone.localdate()


# ------------------------------------------------------------------ who sees what
def _ctx(request):
    from AccessControl.access import ctx
    return ctx(request)


def is_admin(request):
    return _ctx(request).admin


def has(request, *codes):
    c = _ctx(request)
    return c.admin or any(x in c.codes for x in codes)


def visible_projects(request):
    Project, _, _ = _pm()
    c = _ctx(request)
    qs = Project.objects.all()
    if c.admin:
        return qs
    q = Q(pk__in=[])
    if c.emp:
        q |= Q(managers=c.emp) | Q(members=c.emp) | Q(tasks__task_members=c.emp) | Q(tasks__task_managers=c.emp)
    if 'view_project' in c.codes or 'view_timesheet' in c.codes or 'view_projectfinance' in c.codes:
        q |= Q(branches__in=c.branches or []) | Q(branches__isnull=True)
    return qs.filter(pk__in=qs.filter(q).values('pk'))


def visible_tasks(request):
    _, Task, _ = _pm()
    c = _ctx(request)
    if c.admin:
        return Task.objects.all()
    return Task.objects.filter(project__in=visible_projects(request).values('pk'))


def visible_timesheets(request):
    """Admin: all. HR (view_timesheet): employees of their branches. Everybody: own entries, entries on projects /
    tasks they manage and entries of the employees who report to them."""
    _, _, TimeSheet = _pm()
    c = _ctx(request)
    qs = TimeSheet.objects.all()
    if c.admin:
        return qs
    q = Q(employee__emp_reporting_manager=c.user)
    if c.emp:
        q |= Q(employee=c.emp) | Q(project__managers=c.emp) | Q(task__task_managers=c.emp)
    if 'view_timesheet' in c.codes:
        q |= Q(employee__emp_branch_id__in=c.branches or [])
    return qs.filter(pk__in=qs.filter(q).values('pk'))


def manages_project(request, project):
    c = _ctx(request)
    if c.admin:
        return True
    return bool(c.emp and project.managers.filter(pk=c.emp.pk).exists())


def can_see_finance(request, project):
    """Costs / rates / profit: admin, project finance users (branch), the project's managers."""
    c = _ctx(request)
    if c.admin or manages_project(request, project):
        return True
    if 'view_projectfinance' in c.codes:
        return visible_projects(request).filter(pk=project.pk).exists()
    return False


def can_edit_finance(request, project):
    c = _ctx(request)
    if c.admin or manages_project(request, project):
        return True
    return 'change_projectfinance' in c.codes and visible_projects(request).filter(pk=project.pk).exists()


def can_edit_entry(request, ts):
    """Only the employee themselves, HR with change_timesheet (own branches) or an admin may change an entry."""
    c = _ctx(request)
    if c.admin:
        return True
    if c.emp and ts.employee_id == c.emp.pk:
        return True
    if 'change_timesheet' in c.codes:
        return c.branches is None or ts.employee.emp_branch_id_id in (c.branches or [])
    return False


def can_approve(user, ts, request=None):
    """Project manager / task manager / reporting manager of the entry, approve_timesheetextra holders or an admin –
    never on their own entry."""
    emp = ts.employee
    if emp.users_id and emp.users_id == user.id:
        return False
    if request is not None:
        c = _ctx(request)
        if c.admin:
            return True
        if 'approve_timesheetextra' in c.codes and (c.branches is None or emp.emp_branch_id_id in (c.branches or [])):
            return True
    if emp.emp_reporting_manager_id and emp.emp_reporting_manager_id == user.id:
        return True
    if ts.project.managers.filter(users=user).exists():
        return True
    if ts.task_id and ts.task.task_managers.filter(users=user).exists():
        return True
    return False


def approvable_timesheets(request):
    """Entries this user may approve (any status – filter it yourself)."""
    _, _, TimeSheet = _pm()
    c = _ctx(request)
    u = c.user
    qs = TimeSheet.objects.all()
    if not c.admin:
        q = Q(employee__emp_reporting_manager=u) | Q(project__managers__users=u) | Q(task__task_managers__users=u)
        if 'approve_timesheetextra' in c.codes:
            q |= Q(employee__emp_branch_id__in=c.branches or [])
        qs = qs.filter(pk__in=TimeSheet.objects.filter(q).values('pk'))
    return qs.exclude(employee__users=u)


# ------------------------------------------------------------------ validation
def is_assigned(emp, project, task=None):
    if project.members.filter(pk=emp.pk).exists() or project.managers.filter(pk=emp.pk).exists():
        return True
    if task is not None and (task.task_members.filter(pk=emp.pk).exists() or task.task_managers.filter(pk=emp.pk).exists()):
        return True
    return False


def day_hours(emp_id, day, exclude_id=None):
    _, _, TimeSheet = _pm()
    qs = TimeSheet.objects.filter(employee_id=emp_id, date=day)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return sum((hours_of(t) for t in qs.values_list('time_spent', flat=True)), D0)


def validate_entry(employee, project, task, day, hours, exclude_id=None):
    """Errors as {field: message} (empty = valid)."""
    err = {}
    if project is None:
        return {'project': 'Project is required.'}
    if employee is None:
        return {'employee': 'Employee is required.'}
    if day and day > local_today():
        err['date'] = 'You cannot log time for a future date.'
    if task is not None and task.project_id != project.pk:
        err['task'] = 'This task does not belong to the selected project.'
    elif not is_assigned(employee, project, task):
        err['employee'] = 'The employee is not a member or manager of this project / task.'
    if hours is not None and hours < 0:
        err['time_spent'] = 'Hours cannot be negative.'
    elif day and hours is not None:
        total = day_hours(employee.pk, day, exclude_id) + Decimal(hours)
        if total > MAX_DAY_HOURS:
            err['time_spent'] = f'More than 24 hours on {day:%d %b %Y} ({total} h in total).'
    return err


# ------------------------------------------------------------------ extras
def _models():
    from .models import CostRateSetting, MemberRate, ProjectFinance, TaskPlan, TimesheetExtra
    return ProjectFinance, MemberRate, TaskPlan, TimesheetExtra, CostRateSetting


def finance_of(project_id):
    ProjectFinance = _models()[0]
    return ProjectFinance.objects.filter(project_id=project_id).first()


def default_billable(project_id, task_id=None):
    _, _, TaskPlan, _, _ = _models()
    if task_id:
        p = TaskPlan.objects.filter(task_id=task_id).first()
        if p is not None:
            return bool(p.billable)
    f = finance_of(project_id)
    return not (f and f.billing_type == 'non_billable')


def extras_for(ids):
    TimesheetExtra = _models()[3]
    return {e.timesheet_id: e for e in TimesheetExtra.objects.filter(timesheet_id__in=list(ids))}


def get_extra(ts, create=True):
    TimesheetExtra = _models()[3]
    e = TimesheetExtra.objects.filter(timesheet_id=ts.pk).first()
    if e is None and create:
        e = TimesheetExtra.objects.create(timesheet_id=ts.pk, hours=hours_of(ts.time_spent),
                                          billable=default_billable(ts.project_id, ts.task_id))
    return e


def sync_extra(ts, billable=None):
    """Keep hours (from time_spent) and the billable flag in step with the timesheet row."""
    e = get_extra(ts)
    changed = False
    if not e.running:
        h = hours_of(ts.time_spent)
        if e.hours != h:
            e.hours, changed = h, True
    if billable is not None and bool(billable) != e.billable:
        e.billable, changed = bool(billable), True
    if changed:
        e.save()
    return e


def parse_bool(v):
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


# ------------------------------------------------------------------ cost rate
def _month(d):
    return (d.year, d.month)


def monthly_fixed_pay(emp_id, on_date=None, setting=None):
    """Fixed monthly earnings of the salary structure on that date (additions only, no variable pay)."""
    from PayrollManagement.models import EmployeeSalaryStructure
    on_date = on_date or local_today()
    setting = setting or _models()[4].get()
    include = {str(x) for x in (setting.include_allowance_categories or []) if x}
    total = D0
    for r in EmployeeSalaryStructure.objects.filter(employee_id=emp_id, is_active=True).select_related('component'):
        c = r.component
        if c.component_type != 'addition' or c.payroll_category in VARIABLE_CATEGORIES:
            continue
        if (c.component_value_type or 'fixed') != 'fixed':
            continue
        if include and c.payroll_category != 'basic' and c.payroll_category not in include:
            continue
        if r.valid_from and _month(r.valid_from) > _month(on_date):
            continue
        if r.valid_until and _month(r.valid_until) < _month(on_date):
            continue
        total += Decimal(r.amount or 0)
    return q2(total)


def working_days_per_month(emp):
    """fixed_working_days of the pay structure of the employee's branch, else 30."""
    from PayrollManagement.models import PayStructure
    ps = None
    if emp.emp_branch_id_id:
        ps = PayStructure.objects.filter(branch=emp.emp_branch_id_id).order_by('-id').first()
    if ps is None:
        ps = PayStructure.objects.filter(branch__isnull=True).order_by('-id').first()
    if ps is not None and ps.fixed_working_days:
        return int(ps.fixed_working_days)
    return 30


def salary_hour_rate(emp, on_date=None, setting=None):
    setting = setting or _models()[4].get()
    hpd = Decimal(setting.hours_per_day or 8) or Decimal(8)
    monthly = monthly_fixed_pay(emp.pk, on_date, setting)
    rate = monthly / Decimal(working_days_per_month(emp)) / hpd
    rate *= (Decimal(1) + Decimal(setting.overhead_percent or 0) / Decimal(100))
    return q4(rate)


class RateBook:
    """Cached cost / bill rates for one calculation."""

    def __init__(self):
        self.setting = _models()[4].get()
        self._member = {}
        self._fin = {}
        self._salary = {}

    def member(self, project_id, emp_id):
        key = (project_id, emp_id)
        if key not in self._member:
            self._member[key] = _models()[1].objects.filter(project_id=project_id, employee_id=emp_id).first()
        return self._member[key]

    def finance(self, project_id):
        if project_id not in self._fin:
            self._fin[project_id] = finance_of(project_id)
        return self._fin[project_id]

    def cost_rate(self, emp, project_id, on_date=None):
        m = self.member(project_id, emp.pk)
        if m is not None and m.cost_rate_override is not None:
            return q4(m.cost_rate_override)
        key = (emp.pk, _month(on_date or local_today()))
        if key not in self._salary:
            self._salary[key] = salary_hour_rate(emp, on_date, self.setting)
        return self._salary[key]

    def bill_rate(self, project_id, emp_id):
        m = self.member(project_id, emp_id)
        if m is not None and m.bill_rate is not None:
            return q2(m.bill_rate)
        f = self.finance(project_id)
        return q2(f.default_bill_rate) if f else D0

    def billing_type(self, project_id):
        f = self.finance(project_id)
        return f.billing_type if f else 'time_and_material'


def cost_rate(emp, project_id, on_date=None):
    return RateBook().cost_rate(emp, project_id, on_date)


def entry_values(ts, extra, book):
    """hours, billable, cost rate / amount, bill rate / amount of one entry. Approved entries use the snapshot
    taken at approval; others today's rates (estimate)."""
    hours = extra.hours if extra is not None and not extra.running else hours_of(ts.time_spent)
    billable = extra.billable if extra is not None else default_billable(ts.project_id, ts.task_id)
    status = extra.approval_status if extra is not None else 'draft'
    if extra is not None and status == 'approved' and extra.cost_rate is not None:
        crate, cost = extra.cost_rate, extra.cost_amount or D0
        brate, bill = extra.bill_rate or D0, extra.bill_amount or D0
    else:
        crate = book.cost_rate(ts.employee, ts.project_id, ts.date)
        cost = q2(crate * hours)
        brate = book.bill_rate(ts.project_id, ts.employee_id)
        bill = q2(brate * hours) if (billable and book.billing_type(ts.project_id) == 'time_and_material') else D0
    return {'hours': hours, 'billable': billable, 'status': status, 'cost_rate': crate, 'cost': q2(cost),
            'bill_rate': brate, 'bill': q2(bill), 'running': bool(extra and extra.running)}


# ------------------------------------------------------------------ approval
def submit(entries):
    now = timezone.now()
    done, skipped = [], []
    for ts in entries:
        e = get_extra(ts)
        if e.running:
            skipped.append({'id': ts.pk, 'reason': 'Timer is still running.'})
            continue
        if e.approval_status not in ('draft', 'rejected'):
            skipped.append({'id': ts.pk, 'reason': f'Already {e.approval_status}.'})
            continue
        if e.hours <= 0:
            skipped.append({'id': ts.pk, 'reason': 'No hours.'})
            continue
        e.approval_status, e.submitted_at = 'submitted', now
        e.approver_user_id, e.approved_at = None, None
        e.save()
        done.append(ts.pk)
    return done, skipped


def approve(entries, user, request=None):
    now = timezone.now()
    book = RateBook()
    done, skipped = [], []
    for ts in entries:
        e = get_extra(ts)
        if e.approval_status != 'submitted':
            skipped.append({'id': ts.pk, 'reason': f'Entry is {e.approval_status}, not submitted.'})
            continue
        if not can_approve(user, ts, request):
            skipped.append({'id': ts.pk, 'reason': 'You may not approve this entry.'})
            continue
        v = entry_values(ts, e, book)
        e.cost_rate, e.cost_amount = v['cost_rate'], v['cost']
        e.bill_rate, e.bill_amount = v['bill_rate'], v['bill']
        e.approval_status, e.approver_user_id, e.approved_at, e.rejection_reason = 'approved', user.id, now, ''
        e.save()
        done.append(ts.pk)
    return done, skipped


def reject(entries, user, reason, request=None):
    now = timezone.now()
    done, skipped = [], []
    for ts in entries:
        e = get_extra(ts)
        if e.approval_status != 'submitted':
            skipped.append({'id': ts.pk, 'reason': f'Entry is {e.approval_status}, not submitted.'})
            continue
        if not can_approve(user, ts, request):
            skipped.append({'id': ts.pk, 'reason': 'You may not reject this entry.'})
            continue
        e.approval_status, e.approver_user_id, e.approved_at, e.rejection_reason = 'rejected', user.id, now, reason
        e.save()
        done.append(ts.pk)
    return done, skipped


def snapshot(ts):
    return {'project': ts.project_id, 'task': ts.task_id, 'date': str(ts.date), 'time_spent': ts.time_spent,
            'description': ts.description or ''}


def record_correction(ts, before, extra):
    """A rejected entry that is changed goes back to draft; the earlier version is kept in corrected_from."""
    if extra.approval_status != 'rejected':
        return
    hist = list(extra.corrected_from or [])
    hist.append({**before, 'rejection_reason': extra.rejection_reason, 'corrected_at': timezone.now().isoformat()})
    extra.corrected_from = hist
    extra.approval_status = 'draft'
    extra.save()


# ------------------------------------------------------------------ summaries
def _period(qs, date_from=None, date_to=None):
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    return qs


def pct(a, b):
    return float(q2(Decimal(a) * 100 / Decimal(b))) if b else None


def project_summary(project, date_from=None, date_to=None):
    _, Task, TimeSheet = _pm()
    ProjectFinance, MemberRate, TaskPlan, _, _ = _models()
    book = RateBook()
    fin = book.finance(project.pk)
    tasks = list(Task.objects.filter(project=project).order_by('sequence', 'id'))
    plans = {p.task_id: p for p in TaskPlan.objects.filter(task_id__in=[t.pk for t in tasks])}
    planned = sum((Decimal(p.planned_hours or 0) for p in plans.values()), D0)
    qs = _period(TimeSheet.objects.filter(project=project), date_from, date_to).select_related('employee', 'task')
    rows = list(qs)
    ex = extras_for(r.pk for r in rows)
    tot = defaultdict(lambda: D0)
    by_emp, by_task = {}, {}
    for ts in rows:
        v = entry_values(ts, ex.get(ts.pk), book)
        h = v['hours']
        tot['hours'] += h
        tot['billable' if v['billable'] else 'non_billable'] += h
        tot['status_' + v['status']] += h
        tot['cost'] += v['cost']
        tot['bill'] += v['bill']
        e = by_emp.setdefault(ts.employee_id, {'employee_id': ts.employee_id, 'employee': _ename(ts.employee), 'hours': D0,
                                               'billable_hours': D0, 'cost': D0, 'billed': D0})
        e['hours'] += h
        e['billable_hours'] += h if v['billable'] else D0
        e['cost'] += v['cost']
        e['billed'] += v['bill']
        t = by_task.setdefault(ts.task_id, {'task_id': ts.task_id, 'task': ts.task.title if ts.task_id else '(no task)',
                                            'planned_hours': plans[ts.task_id].planned_hours if ts.task_id in plans else D0,
                                            'hours': D0, 'cost': D0})
        t['hours'] += h
        t['cost'] += v['cost']
    for t in tasks:
        if t.pk not in by_task:
            by_task[t.pk] = {'task_id': t.pk, 'task': t.title, 'planned_hours': plans[t.pk].planned_hours if t.pk in plans else D0,
                             'hours': D0, 'cost': D0}
    billing_type = fin.billing_type if fin else 'time_and_material'
    contract = q2(fin.contract_value) if fin else D0
    if billing_type == 'fixed':
        billed = contract
    elif billing_type == 'non_billable':
        billed = D0
    else:
        billed = q2(tot['bill'])
    cost = q2(tot['cost'])
    profit = billed - cost
    budget_amount = q2(fin.budget_amount) if fin else D0
    budget_hours = q2(fin.budget_hours) if fin else D0
    hour_base = budget_hours or planned
    return {
        'project_id': project.pk, 'project': project.title, 'status': project.status,
        'billing_type': billing_type, 'currency': fin.currency if fin else 'AED', 'customer': fin.customer_name if fin else '',
        'contract_value': contract, 'budget_amount': budget_amount, 'budget_hours': budget_hours,
        'planned_hours': q2(planned), 'actual_hours': q2(tot['hours']),
        'billable_hours': q2(tot['billable']), 'non_billable_hours': q2(tot['non_billable']),
        'approved_hours': q2(tot['status_approved']), 'submitted_hours': q2(tot['status_submitted']),
        'draft_hours': q2(tot['status_draft']), 'rejected_hours': q2(tot['status_rejected']),
        'cost': cost, 'billed': billed, 'profit': q2(profit), 'margin_pct': pct(profit, billed),
        'budget_used_pct': pct(cost, budget_amount), 'hours_used_pct': pct(tot['hours'], hour_base),
        'billable_pct': pct(tot['billable'], tot['hours']),
        'by_employee': sorted(({k: (q2(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in by_emp.values()),
                              key=lambda r: -r['hours']),
        'by_task': [{k: (q2(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in by_task.values()],
        'entries': len(rows),
    }


def employee_summary(emp, date_from=None, date_to=None, projects=None):
    _, _, TimeSheet = _pm()
    book = RateBook()
    qs = _period(TimeSheet.objects.filter(employee=emp), date_from, date_to).select_related('project', 'employee')
    if projects is not None:
        qs = qs.filter(project__in=projects)
    rows = list(qs)
    ex = extras_for(r.pk for r in rows)
    tot = defaultdict(lambda: D0)
    by_p = {}
    for ts in rows:
        v = entry_values(ts, ex.get(ts.pk), book)
        h = v['hours']
        tot['hours'] += h
        tot['billable'] += h if v['billable'] else D0
        tot['cost'] += v['cost']
        tot['billed'] += v['bill']
        p = by_p.setdefault(ts.project_id, {'project_id': ts.project_id, 'project': ts.project.title, 'hours': D0, 'billable_hours': D0,
                                            'cost': D0, 'billed': D0})
        p['hours'] += h
        p['billable_hours'] += h if v['billable'] else D0
        p['cost'] += v['cost']
        p['billed'] += v['bill']
    return {'employee_id': emp.pk, 'employee': _ename(emp), 'hours': q2(tot['hours']), 'billable_hours': q2(tot['billable']),
            'non_billable_hours': q2(tot['hours'] - tot['billable']), 'billable_pct': pct(tot['billable'], tot['hours']),
            'cost': q2(tot['cost']), 'billed': q2(tot['billed']), 'current_cost_rate': salary_hour_rate(emp),
            'by_project': sorted(({k: (q2(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in by_p.values()),
                                 key=lambda r: -r['hours'])}


def _ename(e):
    if e is None:
        return ''
    name = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x and str(x).strip())
    return f'{name} ({e.emp_code})' if name else e.emp_code


def assignable_projects(emp):
    """Projects / tasks the employee may book time on."""
    Project, Task, _ = _pm()
    pq = Project.objects.filter(Q(members=emp) | Q(managers=emp) | Q(tasks__task_members=emp) | Q(tasks__task_managers=emp)).distinct()
    out = []
    for p in pq.order_by('title'):
        full = p.members.filter(pk=emp.pk).exists() or p.managers.filter(pk=emp.pk).exists()
        tq = Task.objects.filter(project=p)
        if not full:
            tq = tq.filter(Q(task_members=emp) | Q(task_managers=emp)).distinct()
        out.append({'id': p.pk, 'title': p.title, 'status': p.status, 'billable': default_billable(p.pk),
                    'tasks': [{'id': t.pk, 'title': t.title, 'billable': default_billable(p.pk, t.pk)} for t in tq.order_by('sequence', 'id')]})
    return out


def week_grid(emp, start):
    _, _, TimeSheet = _pm()
    days = [start + timedelta(days=i) for i in range(7)]
    qs = TimeSheet.objects.filter(employee=emp, date__gte=days[0], date__lte=days[-1]).select_related('project', 'task').order_by('date', 'id')
    rows_ts = list(qs)
    ex = extras_for(t.pk for t in rows_ts)
    rows = {}
    for ts in rows_ts:
        e = ex.get(ts.pk)
        key = (ts.project_id, ts.task_id)
        r = rows.setdefault(key, {'project': ts.project_id, 'project_title': ts.project.title, 'task': ts.task_id,
                                  'task_title': ts.task.title if ts.task_id else '', 'billable': e.billable if e else default_billable(ts.project_id, ts.task_id),
                                  'cells': [{'date': d.isoformat(), 'hours': D0, 'entries': []} for d in days]})
        c = r['cells'][(ts.date - start).days]
        h = (e.hours if e and not e.running else hours_of(ts.time_spent))
        c['hours'] += h
        c['entries'].append({'id': ts.pk, 'hours': h, 'status': e.approval_status if e else 'draft', 'billable': e.billable if e else r['billable'],
                             'running': bool(e and e.running), 'rejection_reason': e.rejection_reason if e else '',
                             'description': ts.description or ''})
    out_rows = []
    day_tot = [D0] * 7
    for r in rows.values():
        tot = D0
        for i, c in enumerate(r['cells']):
            st = {x['status'] for x in c['entries']}
            c['status'] = (st.pop() if len(st) == 1 else ('mixed' if st else ''))
            c['locked'] = any(x['status'] in ('submitted', 'approved') or x['running'] for x in c['entries'])
            c['editable'] = not c['locked'] and len(c['entries']) <= 1 and days[i] <= local_today()
            c['rejection_reason'] = '; '.join(x['rejection_reason'] for x in c['entries'] if x['status'] == 'rejected' and x['rejection_reason'])
            c['hours'] = q2(c['hours'])
            tot += c['hours']
            day_tot[i] += c['hours']
        r['total'] = q2(tot)
        out_rows.append(r)
    out_rows.sort(key=lambda r: (r['project_title'], r['task_title']))
    all_e = [x for r in out_rows for c in r['cells'] for x in c['entries']]
    counts = defaultdict(int)
    for x in all_e:
        counts[x['status']] += 1
    return {'employee_id': emp.pk, 'employee': _ename(emp), 'week_start': start.isoformat(), 'days': [d.isoformat() for d in days],
            'rows': out_rows, 'day_totals': [q2(x) for x in day_tot], 'total': q2(sum(day_tot, D0)), 'status_counts': dict(counts),
            'rejected': [x for x in all_e if x['status'] == 'rejected']}
