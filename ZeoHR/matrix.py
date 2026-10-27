"""v1.58.0 – concurrent assignments and matrix (dotted-line) reporting.

* Assignment     – a second place an employee works at the same time (secondment, second role, project, acting role) with a share
                   of the time, a manager and dates; the rest of the time is the main job of the employee record.
* MatrixLink     – the functional (dotted-line) manager of an employee besides the reporting manager.
* FunctionalReview – the functional manager's input on an appraisal; the reporting manager stays the final reviewer.

Used by: payroll cost allocation (PayrollPlus.costalloc – the assignment shares when the employee has no cost rule of its own),
approvals (LeavePolicy.approvers role "functional_manager"), My team ("Dotted line"), the org chart and the reports here.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from Translations.i18n import tr as _tr

from .models import Assignment, FunctionalReview, MatrixLink

LIVE = ('planned', 'active')
D100 = Decimal('100')


def today():
    return timezone.localdate()


def pct(x):
    """40.00 → '40', 12.50 → '12.5' (never '4E+1')."""
    v = format(Decimal(str(x)).normalize(), 'f')
    return v


def status_for(a, on=None):
    """The status a (not cancelled) assignment has on a day."""
    on = on or today()
    if a.status == 'cancelled':
        return 'cancelled'
    if a.end_date and a.end_date < on:
        return 'ended'
    if a.start_date and a.start_date > on:
        return 'planned'
    return 'active'


def overlapping(emp_id, start, end, exclude=None):
    qs = Assignment.objects.filter(employee_id=emp_id, status__in=LIVE).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
    if end:
        qs = qs.filter(start_date__lte=end)
    if exclude:
        qs = qs.exclude(pk=exclude)
    return list(qs)


def peak_share(emp_id, start, end, percent, exclude=None):
    """Highest total share of the employee's assignments on any day of [start, end] with this one added."""
    others = overlapping(emp_id, start, end, exclude)
    days = {start} | {o.start_date for o in others if o.start_date >= start and (end is None or o.start_date <= end)}
    best = Decimal('0')
    for dday in days:
        tot = Decimal(percent) + sum((o.percent for o in others if o.start_date <= dday and (o.end_date is None or o.end_date >= dday)), Decimal('0'))
        best = max(best, tot)
    return best, others


def active_on(emp_ids, on=None):
    on = on or today()
    return Assignment.objects.filter(employee_id__in=emp_ids, status__in=LIVE, start_date__lte=on).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=on)).select_related('host_branch', 'department', 'designation', 'project', 'manager')


def main_share(emp_id, on=None):
    return max(Decimal('0'), D100 - sum((a.percent for a in active_on([emp_id], on)), Decimal('0')))


def matrix_link(emp_id, on=None):
    on = on or today()
    m = MatrixLink.objects.filter(employee_id=emp_id).select_related('functional_manager').first()
    if m is None:
        return None
    if (m.valid_from and m.valid_from > on) or (m.valid_until and m.valid_until < on):
        return None
    if not m.functional_manager.is_active:
        return None
    return m


def functional_manager(emp, on=None):
    """The user who approves as the employee's functional manager: the matrix link, else the manager of the largest active
    assignment that has "this manager approves" ticked. None when there is none."""
    if emp is None:
        return None
    m = matrix_link(emp.pk, on)
    if m is not None:
        return m.functional_manager
    best = None
    for a in active_on([emp.pk], on):
        if a.manager_id and a.manager_approves and a.manager.is_active and (best is None or a.percent > best.percent):
            best = a
    return best.manager if best else None


def dotted_reports(user, on=None):
    """[(employee, relation text)] of the people for whom `user` is a functional manager or an assignment manager."""
    on = on or today()
    out = {}
    for m in MatrixLink.objects.filter(functional_manager=user).select_related('employee', 'employee__emp_dept_id', 'employee__emp_desgntn_id'):
        if (m.valid_from and m.valid_from > on) or (m.valid_until and m.valid_until < on):
            continue
        if m.employee.is_active is False:
            continue
        out[m.employee_id] = (m.employee, _tr('Functional manager') + (f' – {m.area}' if m.area else ''))
    for a in Assignment.objects.filter(manager=user, status__in=LIVE, start_date__lte=on).filter(Q(end_date__isnull=True) | Q(end_date__gte=on)).select_related(
            'employee', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'department', 'host_branch', 'project'):
        if a.employee.is_active is False:
            continue
        where = a.project.title if a.project_id else (a.department.dept_name if a.department_id else (a.host_branch.branch_name if a.host_branch_id else ''))
        txt = f'{_tr(a.get_kind_display())}{(" – " + where) if where else ""}, {a.percent.normalize():f}%'
        if a.employee_id in out:
            out[a.employee_id] = (out[a.employee_id][0], out[a.employee_id][1] + '; ' + txt)
        else:
            out[a.employee_id] = (a.employee, txt)
    return sorted(out.values(), key=lambda x: (x[0].emp_first_name or '').lower())


# ------------------------------------------------------------------ payroll cost allocation
def cost_splits(emp_ids, start, end):
    """{emp_id: [[kind, id, percent, None, label]]} – the assignment shares of the month, by the days each assignment was active.
    Only assignments with "split the salary cost"; a project assignment goes to the project, the others to the department."""
    days_in = (end - start).days + 1
    out = {}
    for a in Assignment.objects.filter(employee_id__in=emp_ids, cost_split=True, status__in=('active', 'ended', 'planned'),
                                       start_date__lte=end).filter(Q(end_date__isnull=True) | Q(end_date__gte=start)):
        if a.status == 'cancelled':
            continue
        if a.project_id:
            kind, pk = 'project', a.project_id
        elif a.department_id:
            kind, pk = 'department', a.department_id
        else:
            continue
        s = max(a.start_date, start)
        e = min(a.end_date or end, end)
        n = (e - s).days + 1
        if n <= 0:
            continue
        pct = a.percent * Decimal(n) / Decimal(days_in)
        out.setdefault(a.employee_id, []).append([kind, pk, pct, None, a.number])
    for k, rows in out.items():   # never more than 100 % in a month (overlaps are refused, this is a guard)
        tot = sum((r[2] for r in rows), Decimal('0'))
        if tot > D100:
            for r in rows:
                r[2] = r[2] * D100 / tot
    return out


# ------------------------------------------------------------------ statuses and appraisal input
def refresh_statuses(on=None):
    on = on or today()
    n = 0
    for a in Assignment.objects.filter(status__in=LIVE):
        st = status_for(a, on)
        if st != a.status:
            a.status = st
            if st == 'ended' and not a.ended_at:
                a.ended_at = timezone.now()
            a.save(update_fields=['status', 'ended_at'])
            n += 1
    return n


def open_reviews(employee_ids=None):
    """A functional review to give for each goal sheet of an active cycle whose employee has a functional manager (matrix link)."""
    from django.apps import apps
    GS = apps.get_model('PerformanceManagement', 'GoalSheet')
    links = MatrixLink.objects.select_related('functional_manager')
    if employee_ids is not None:
        links = links.filter(employee_id__in=employee_ids)
    made = 0
    on = today()
    for m in links:
        if (m.valid_until and m.valid_until < on) or not m.functional_manager.is_active:
            continue
        for s in GS.objects.filter(employee_id=m.employee_id, cycle__status__in=('active', 'calibration')):
            if s.manager_id == m.functional_manager_id:
                continue    # the same person is the reporting manager
            _, c = FunctionalReview.objects.get_or_create(sheet=s, reviewer=m.functional_manager,
                                                          defaults={'employee_id': m.employee_id, 'weight': m.review_weight})
            made += int(c)
    return made


def blended(sheet):
    """{functional: [...], blended_score} for a goal sheet – the reporting manager's score mixed with the functional ratings."""
    revs = list(FunctionalReview.objects.filter(sheet=sheet, status='submitted').select_related('reviewer'))
    from zeo.appkit.engine import user_name
    out = {'functional': [{'reviewer': user_name(r.reviewer) or r.reviewer.username, 'rating': r.rating, 'weight': pct(r.weight),
                           'strengths': r.strengths, 'improvements': r.improvements, 'submitted_at': r.submitted_at} for r in revs],
           'blended_score': None}
    ms = sheet.manager_score
    rated = [r for r in revs if r.rating]
    if ms is not None and rated:
        w = min(sum((r.weight for r in rated), Decimal('0')), Decimal('90')) / D100
        f = sum((Decimal(r.rating) * r.weight for r in rated), Decimal('0')) / sum((r.weight for r in rated), Decimal('0')) if w else Decimal('0')
        out['blended_score'] = str(((Decimal(ms) * (1 - w)) + f * w).quantize(Decimal('0.01')))
    return out


# ------------------------------------------------------------------ reports
def fte_by_department(on=None, branches=None):
    """[{department, home, seconded_in, seconded_out, fte}] – the main job counts with its share, assignments in the host department.
    branches: only employees of these branches (home) and assignments hosted by them; None = the whole company."""
    from EmpManagement.models import emp_master
    on = on or today()
    qs = emp_master.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('emp_dept_id')
    if branches is not None:
        hosted = Assignment.objects.filter(host_branch_id__in=branches, status__in=LIVE).values('employee_id')
        qs = qs.filter(Q(emp_branch_id__in=branches) | Q(pk__in=hosted))
    emps = {e.pk: e for e in qs}
    asg = list(active_on(list(emps.keys()), on))
    if branches is not None:
        home_ids = {e.pk for e in emps.values() if e.emp_branch_id_id in branches}
        asg = [a for a in asg if a.employee_id in home_ids or a.host_branch_id in branches]
        emps_home = {k: v for k, v in emps.items() if k in home_ids}
    else:
        emps_home = emps
    by_emp = {}
    for a in asg:
        by_emp.setdefault(a.employee_id, []).append(a)
    rows = {}

    def row(dept):
        key = dept.pk if dept else 0
        if key not in rows:
            rows[key] = {'department_id': key, 'department': dept.dept_name if dept else 'No department', 'home': 0, 'fte': Decimal('0'),
                         'seconded_in': 0, 'seconded_out': 0}
        return rows[key]
    for e in emps_home.values():
        r = row(e.emp_dept_id)
        r['home'] += 1
        share = max(Decimal('0'), D100 - sum((a.percent for a in by_emp.get(e.pk, [])), Decimal('0'))) / D100
        r['fte'] += share
        if by_emp.get(e.pk):
            r['seconded_out'] += 1
    for a in asg:
        if a.employee_id not in emps:
            continue
        r = row(a.department if a.department_id else emps[a.employee_id].emp_dept_id)
        r['fte'] += a.percent / D100
        r['seconded_in'] += 1
    out = sorted(rows.values(), key=lambda r: r['department'])
    for r in out:
        r['fte'] = str(r['fte'].quantize(Decimal('0.01')))
    return out
