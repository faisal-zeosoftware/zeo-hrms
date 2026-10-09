"""
Project control API (/project-control/api/...): weekly timesheet, timer, submit / approve / reject,
project finance and rates, project and employee summaries.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services as S
from .models import BILLING_TYPES, CostRateSetting, MemberRate, ProjectFinance, TaskPlan, TimesheetExtra


def _pm():
    from ProjectManagement.models import Project, Task, TimeSheet
    return Project, Task, TimeSheet


def _emp_model():
    from EmpManagement.models import emp_master
    return emp_master


def bad(msg, code=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'detail': msg, **extra}, status=code)


def _dec(v, field, allow_none=False):
    if v in (None, ''):
        if allow_none:
            return None
        return Decimal('0')
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise ValueError(f'{field}: not a number.')
    if d < 0:
        raise ValueError(f'{field}: cannot be negative.')
    return d


def _dates(request):
    q = request.query_params
    return parse_date(q.get('from') or '') if q.get('from') else None, parse_date(q.get('to') or '') if q.get('to') else None


def _me(request):
    return S._ctx(request).emp


def _target_employee(request, for_write=False):
    """The employee a request is about: ?employee= (when the user may see that employee's timesheets) or self."""
    c = S._ctx(request)
    eid = request.query_params.get('employee') or (request.data.get('employee') if for_write and hasattr(request.data, 'get') else None)
    if not eid or (c.emp and str(eid) == str(c.emp.pk)):
        return c.emp, None
    Emp = _emp_model()
    emp = Emp.objects.filter(pk=eid).first() if str(eid).isdigit() else None
    if emp is None:
        return None, bad('Employee not found.', status.HTTP_404_NOT_FOUND)
    if for_write:
        if not (c.admin or ('change_timesheet' in c.codes and emp.emp_branch_id_id in (c.branches or []))):
            return None, bad('You can only fill in your own timesheet.', status.HTTP_403_FORBIDDEN)
        return emp, None
    if c.admin or emp.emp_reporting_manager_id == c.user.id or S.visible_timesheets(request).filter(employee=emp).exists() \
            or ('view_timesheet' in c.codes and emp.emp_branch_id_id in (c.branches or [])):
        return emp, None
    return None, bad('You may not see this employee\'s timesheet.', status.HTTP_403_FORBIDDEN)


def entry_json(ts, e=None):
    e = e if e is not None else S.get_extra(ts, create=False)
    return {'id': ts.pk, 'project': ts.project_id, 'project_title': ts.project.title, 'task': ts.task_id,
            'task_title': ts.task.title if ts.task_id else '', 'employee': ts.employee_id, 'employee_name': S._ename(ts.employee),
            'date': ts.date, 'time_spent': ts.time_spent, 'hours': (e.hours if e and not e.running else S.hours_of(ts.time_spent)),
            'description': ts.description or '', 'status': ts.status,
            'billable': e.billable if e else S.default_billable(ts.project_id, ts.task_id),
            'approval_status': e.approval_status if e else 'draft', 'rejection_reason': e.rejection_reason if e else '',
            'running': bool(e and e.running), 'start_at': e.start_at if e else None, 'stop_at': e.stop_at if e else None,
            'submitted_at': e.submitted_at if e else None, 'approved_at': e.approved_at if e else None,
            'corrections': len(e.corrected_from or []) if e else 0}


# ------------------------------------------------------------------ projects for pickers
class MyProjectsView(APIView):
    """GET → projects / tasks the employee may book time on (?employee= for HR / managers)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp, err = _target_employee(request)
        if err:
            return err
        if emp is None:
            return Response({'projects': [], 'detail': 'Your user is not linked to an employee.'})
        return Response({'employee_id': emp.pk, 'projects': S.assignable_projects(emp)})


class ProjectListView(APIView):
    """GET → projects the user can see, with can_finance / can_edit flags (finance page picker)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        out = []
        for p in S.visible_projects(request).order_by('title'):
            out.append({'id': p.pk, 'title': p.title, 'status': p.status, 'start_date': p.start_date, 'end_date': p.end_date,
                        'can_finance': S.can_see_finance(request, p), 'can_edit': S.can_edit_finance(request, p)})
        return Response({'projects': out})


# ------------------------------------------------------------------ weekly grid
class WeekView(APIView):
    """GET ?week=YYYY-MM-DD(&employee=) → grid: rows project / task, columns Mon–Sun."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp, err = _target_employee(request)
        if err:
            return err
        if emp is None:
            return bad('Your user is not linked to an employee.')
        d = parse_date(request.query_params.get('week') or '') or S.local_today()
        grid = S.week_grid(emp, S.week_start(d))
        grid['today'] = S.local_today().isoformat()
        grid['is_me'] = bool(_me(request) and _me(request).pk == emp.pk)
        if grid['is_me']:
            grid['projects'] = S.assignable_projects(emp)
        return Response(grid)


class WeekSaveView(APIView):
    """POST {week, rows: [{project, task, billable, cells: [{date, hours}]}]} → creates / changes / removes entries.
    Locked cells (submitted / approved / running) and cells with several entries cannot be changed here."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Project, Task, TimeSheet = _pm()
        emp, err = _target_employee(request, for_write=True)
        if err:
            return err
        if emp is None:
            return bad('Your user is not linked to an employee.')
        d = parse_date(str(request.data.get('week') or '')) or S.local_today()
        start = S.week_start(d)
        days = {start + timedelta(days=i) for i in range(7)}
        errors = []
        changed = 0
        try:
            with transaction.atomic():
                for row in request.data.get('rows') or []:
                    project = Project.objects.filter(pk=row.get('project')).first()
                    if project is None:
                        errors.append({'row': row.get('project'), 'detail': 'Project not found.'})
                        continue
                    task = Task.objects.filter(pk=row.get('task')).first() if row.get('task') else None
                    billable = S.parse_bool(row.get('billable'))
                    for cell in row.get('cells') or []:
                        day = parse_date(str(cell.get('date') or ''))
                        if day not in days:
                            errors.append({'date': cell.get('date'), 'detail': 'Date is outside the week.'})
                            continue
                        try:
                            hours = S.q2(_dec(cell.get('hours'), 'hours'))
                        except ValueError as exc:
                            errors.append({'date': str(day), 'detail': str(exc)})
                            continue
                        existing = list(TimeSheet.objects.filter(employee=emp, project=project, task=task, date=day).order_by('id'))
                        ex = S.extras_for(t.pk for t in existing)
                        cur = sum((ex[t.pk].hours if t.pk in ex and not ex[t.pk].running else S.hours_of(t.time_spent) for t in existing), S.D0)
                        locked = any(ex.get(t.pk) and (ex[t.pk].locked or ex[t.pk].running) for t in existing)
                        if hours == cur:
                            if billable is not None and existing and not locked:
                                for t in existing:
                                    S.sync_extra(t, billable)
                            continue
                        if locked:
                            errors.append({'date': str(day), 'project': project.title, 'detail': 'Submitted or approved time cannot be changed.'})
                            continue
                        if len(existing) > 1:
                            errors.append({'date': str(day), 'project': project.title,
                                           'detail': 'This day has several entries – change them in the timesheet list.'})
                            continue
                        msg = S.validate_entry(emp, project, task, day, hours, exclude_id=existing[0].pk if existing else None)
                        if msg:
                            errors.append({'date': str(day), 'project': project.title, 'detail': ' '.join(msg.values())})
                            continue
                        if existing:
                            ts = existing[0]
                            if hours == 0:
                                TimesheetExtra.objects.filter(timesheet_id=ts.pk).delete()
                                ts.delete()
                            else:
                                before = S.snapshot(ts)
                                ts.time_spent = S.to_hhmm(hours)
                                ts.status = 'completed'
                                ts.save()
                                e = S.sync_extra(ts, billable)
                                S.record_correction(ts, before, e)
                        elif hours > 0:
                            ts = TimeSheet.objects.create(employee=emp, project=project, task=task, date=day, time_spent=S.to_hhmm(hours),
                                                          status='completed', description=row.get('description') or 'Weekly timesheet')
                            S.sync_extra(ts, billable if billable is not None else S.default_billable(project.pk, task.pk if task else None))
                        changed += 1
                if errors:
                    raise _Rollback()
        except _Rollback:
            return Response({'detail': 'Nothing was saved: ' + ' | '.join(f"{e.get('date', '')} {e['detail']}".strip() for e in errors),
                             'errors': errors}, status=status.HTTP_400_BAD_REQUEST)
        grid = S.week_grid(emp, start)
        grid['changed'] = changed
        return Response(grid)


class _Rollback(Exception):
    pass


class EntryBillableView(APIView):
    """POST {ids, billable} → sets the billable flag of draft / rejected entries the user may edit."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _, _, TimeSheet = _pm()
        flag = S.parse_bool(request.data.get('billable'))
        if flag is None:
            return bad('billable must be true or false.')
        done = []
        for ts in TimeSheet.objects.filter(pk__in=request.data.get('ids') or []).select_related('employee'):
            if not S.can_edit_entry(request, ts):
                continue
            e = S.get_extra(ts)
            if e.locked:
                continue
            e.billable = flag
            e.save()
            done.append(ts.pk)
        return Response({'updated': done})


# ------------------------------------------------------------------ timer
def _running(emp):
    _, _, TimeSheet = _pm()
    return TimesheetExtra.objects.filter(running=True, timesheet_id__in=TimeSheet.objects.filter(employee=emp).values('id')).first()


def _timer_json(e):
    _, _, TimeSheet = _pm()
    if e is None:
        return {'running': False}
    ts = TimeSheet.objects.select_related('project', 'task', 'employee').get(pk=e.timesheet_id)
    secs = int((timezone.now() - e.start_at).total_seconds()) if e.start_at else 0
    out = {'running': True, 'entry': entry_json(ts, e), 'start_at': e.start_at, 'elapsed_seconds': secs,
           'elapsed_hours': float(S.q2(Decimal(secs) / Decimal(3600)))}
    if Decimal(secs) / Decimal(3600) > S.LONG_TIMER_HOURS:
        out['warning'] = 'The timer has been running for more than 12 hours. Stop it and correct the time if you forgot it.'
    return out


class TimerView(APIView):
    """GET → the running timer of the user (or {running: false})."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp = _me(request)
        if emp is None:
            return Response({'running': False, 'detail': 'Your user is not linked to an employee.'})
        return Response(_timer_json(_running(emp)))


class TimerStartView(APIView):
    """POST {project, task?, description?, billable?} → new entry for today with a running timer."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Project, Task, TimeSheet = _pm()
        emp = _me(request)
        if emp is None:
            return bad('Your user is not linked to an employee.')
        if _running(emp):
            return bad('A timer is already running. Stop it first.', status.HTTP_409_CONFLICT, timer=_timer_json(_running(emp)))
        project = Project.objects.filter(pk=request.data.get('project')).first()
        if project is None:
            return bad('Pick a project.')
        task = Task.objects.filter(pk=request.data.get('task')).first() if request.data.get('task') else None
        today = S.local_today()
        msg = S.validate_entry(emp, project, task, today, S.D0)
        if msg:
            return Response(msg, status=status.HTTP_400_BAD_REQUEST)
        billable = S.parse_bool(request.data.get('billable'))
        with transaction.atomic():
            ts = TimeSheet.objects.create(employee=emp, project=project, task=task, date=today, time_spent='00:00', status='in_progress',
                                          description=request.data.get('description') or 'Timer')
            TimesheetExtra.objects.create(timesheet_id=ts.pk, hours=0, running=True, start_at=timezone.now(),
                                          billable=billable if billable is not None else S.default_billable(project.pk, task.pk if task else None))
        return Response(_timer_json(_running(emp)), status=status.HTTP_201_CREATED)


class TimerStopView(APIView):
    """POST {description?} → stops the running timer; hours = time since start (max 12 h unless confirm_long=true,
    and never more than what is left of the 24 h of that day)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _, _, TimeSheet = _pm()
        emp = _me(request)
        if emp is None:
            return bad('Your user is not linked to an employee.')
        e = _running(emp)
        if e is None:
            return bad('No timer is running.')
        ts = TimeSheet.objects.select_related('project', 'task', 'employee').get(pk=e.timesheet_id)
        now = timezone.now()
        hours = S.q2(Decimal((now - e.start_at).total_seconds()) / Decimal(3600)) if e.start_at else S.D0
        warnings = []
        if hours > S.LONG_TIMER_HOURS and not S.parse_bool(request.data.get('confirm_long')):
            warnings.append(f'The timer ran {hours} h; it was cut to 12 h. Correct the entry if needed.')
            hours = S.LONG_TIMER_HOURS
        left = S.MAX_DAY_HOURS - S.day_hours(emp.pk, ts.date, exclude_id=ts.pk)
        if hours > left:
            warnings.append(f'Only {left} h were left for {ts.date}; the entry was cut to that.')
            hours = max(left, S.D0)
        with transaction.atomic():
            ts.time_spent = S.to_hhmm(hours)
            ts.status = 'completed'
            if request.data.get('description'):
                ts.description = request.data.get('description')
            ts.save()
            e.running, e.stop_at, e.hours = False, now, S.hours_of(ts.time_spent)
            e.save()
        return Response({'running': False, 'entry': entry_json(ts, e), 'hours': e.hours, 'warnings': warnings})


# ------------------------------------------------------------------ submit / approve
class SubmitView(APIView):
    """POST {week} → submits the user's draft / rejected entries of that week; or {ids: [...]}."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _, _, TimeSheet = _pm()
        emp = _me(request)
        if emp is None:
            return bad('Your user is not linked to an employee.')
        qs = TimeSheet.objects.filter(employee=emp)
        ids = request.data.get('ids')
        if ids:
            qs = qs.filter(pk__in=ids)
        else:
            d = parse_date(str(request.data.get('week') or ''))
            if not d:
                return bad('Give the week (any date in it) or the entry ids.')
            start = S.week_start(d)
            qs = qs.filter(date__gte=start, date__lte=start + timedelta(days=6))
        entries = list(qs.select_related('project', 'task', 'employee'))
        if not entries:
            return bad('There is nothing to submit.')
        done, skipped = S.submit(entries)
        return Response({'submitted': done, 'skipped': skipped, 'detail': f'{len(done)} entr{"y" if len(done) == 1 else "ies"} submitted for approval.'})


class ApprovalListView(APIView):
    """GET ?status=submitted|approved|rejected|all → entries I may approve, grouped by employee and week."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        st = request.query_params.get('status') or 'submitted'
        qs = S.approvable_timesheets(request).select_related('project', 'task', 'employee')
        ids = list(qs.values_list('pk', flat=True))
        ex = S.extras_for(ids)
        if st != 'all':
            keep = [i for i in ids if (ex[i].approval_status if i in ex else 'draft') == st]
            qs = qs.filter(pk__in=keep)
        d_from, d_to = _dates(request)
        if d_from:
            qs = qs.filter(date__gte=d_from)
        if d_to:
            qs = qs.filter(date__lte=d_to)
        groups = {}
        for ts in qs.order_by('employee_id', 'date', 'id'):
            ws = S.week_start(ts.date)
            g = groups.setdefault((ts.employee_id, ws), {'employee_id': ts.employee_id, 'employee': S._ename(ts.employee),
                                                         'week_start': ws, 'week_end': ws + timedelta(days=6), 'hours': S.D0,
                                                         'billable_hours': S.D0, 'entries': []})
            j = entry_json(ts, ex.get(ts.pk))
            g['entries'].append(j)
            g['hours'] += j['hours']
            g['billable_hours'] += j['hours'] if j['billable'] else S.D0
        out = sorted(groups.values(), key=lambda g: (g['week_start'], g['employee']))
        return Response({'status': st, 'groups': out, 'count': sum(len(g['entries']) for g in out)})


class ApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _, _, TimeSheet = _pm()
        ids = request.data.get('ids') or []
        if not ids:
            return bad('Pick the entries to approve.')
        entries = list(TimeSheet.objects.filter(pk__in=ids).select_related('project', 'task', 'employee'))
        with transaction.atomic():
            done, skipped = S.approve(entries, request.user, request)
        if not done:
            return bad('Nothing was approved.', status.HTTP_403_FORBIDDEN if skipped and all('may not' in s['reason'] for s in skipped) else 400,
                       skipped=skipped)
        return Response({'approved': done, 'skipped': skipped})


class RejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _, _, TimeSheet = _pm()
        ids = request.data.get('ids') or []
        reason = (request.data.get('reason') or '').strip()
        if not ids:
            return bad('Pick the entries to reject.')
        if not reason:
            return bad('Give the reason for the rejection.')
        entries = list(TimeSheet.objects.filter(pk__in=ids).select_related('project', 'task', 'employee'))
        with transaction.atomic():
            done, skipped = S.reject(entries, request.user, reason, request)
        if not done:
            return bad('Nothing was rejected.', status.HTTP_403_FORBIDDEN if skipped and all('may not' in s['reason'] for s in skipped) else 400,
                       skipped=skipped)
        return Response({'rejected': done, 'skipped': skipped})


# ------------------------------------------------------------------ finance
FIN_FIELDS = ('customer_name', 'contract_value', 'budget_amount', 'budget_hours', 'currency', 'billing_type', 'default_bill_rate')


def _finance_json(request, project):
    _, Task, _ = _pm()
    f = S.finance_of(project.pk) or ProjectFinance(project_id=project.pk)
    rates = {m.employee_id: m for m in MemberRate.objects.filter(project_id=project.pk)}
    book = S.RateBook()
    members = []
    seen = set()
    for role, qs in (('Manager', project.managers.all()), ('Member', project.members.all())):
        for e in qs:
            if e.pk in seen:
                continue
            seen.add(e.pk)
            m = rates.get(e.pk)
            members.append({'employee_id': e.pk, 'employee': S._ename(e), 'project_role': role, 'role': m.role if m else '',
                            'bill_rate': m.bill_rate if m else None, 'cost_rate_override': m.cost_rate_override if m else None,
                            'salary_cost_rate': S.salary_hour_rate(e, setting=book.setting),
                            'effective_cost_rate': book.cost_rate(e, project.pk), 'effective_bill_rate': book.bill_rate(project.pk, e.pk)})
    plans = {p.task_id: p for p in TaskPlan.objects.filter(task_id__in=Task.objects.filter(project=project).values('id'))}
    tasks = [{'task_id': t.pk, 'task': t.title, 'planned_hours': plans[t.pk].planned_hours if t.pk in plans else S.D0,
              'billable': plans[t.pk].billable if t.pk in plans else (f.billing_type != 'non_billable')}
             for t in Task.objects.filter(project=project).order_by('sequence', 'id')]
    return {'project': {'id': project.pk, 'title': project.title, 'status': project.status, 'start_date': project.start_date,
                        'end_date': project.end_date},
            'finance': {k: getattr(f, k) for k in FIN_FIELDS}, 'billing_types': [{'value': v, 'label': l} for v, l in BILLING_TYPES],
            'members': members, 'tasks': tasks, 'can_edit': S.can_edit_finance(request, project)}


class FinanceView(APIView):
    """GET / PUT /finance/<project_id>/ – finance settings, member rates and task plans of one project.
    PUT {finance: {...}, member_rates: [{employee_id, role, bill_rate, cost_rate_override}], task_plans: [{task_id, planned_hours, billable}]}"""
    permission_classes = [IsAuthenticated]

    def _project(self, request, pk):
        Project, _, _ = _pm()
        return get_object_or_404(S.visible_projects(request), pk=pk)

    def get(self, request, pk):
        p = self._project(request, pk)
        if not S.can_see_finance(request, p):
            return bad('Only the project manager or finance users can see the project finances.', status.HTTP_403_FORBIDDEN)
        return Response(_finance_json(request, p))

    def put(self, request, pk):
        _, Task, _ = _pm()
        p = self._project(request, pk)
        if not S.can_edit_finance(request, p):
            return bad('Only the project manager or finance users can change the project finances.', status.HTTP_403_FORBIDDEN)
        data = request.data
        try:
            with transaction.atomic():
                fd = data.get('finance')
                if isinstance(fd, dict):
                    f = S.finance_of(p.pk) or ProjectFinance(project_id=p.pk)
                    for k in ('contract_value', 'budget_amount', 'budget_hours', 'default_bill_rate'):
                        if k in fd:
                            setattr(f, k, _dec(fd.get(k), k.replace('_', ' ')))
                    if 'billing_type' in fd:
                        if fd['billing_type'] not in dict(BILLING_TYPES):
                            raise ValueError('billing type: pick fixed, time_and_material or non_billable.')
                        f.billing_type = fd['billing_type']
                    if 'customer_name' in fd:
                        f.customer_name = (fd.get('customer_name') or '')[:200]
                    if 'currency' in fd:
                        f.currency = (fd.get('currency') or 'AED')[:3].upper()
                    f.save()
                member_ids = set(p.members.values_list('id', flat=True)) | set(p.managers.values_list('id', flat=True))
                for r in data.get('member_rates') or []:
                    eid = int(r.get('employee_id') or 0)
                    if eid not in member_ids:
                        raise ValueError(f'employee {eid} is not on this project.')
                    m, _ = MemberRate.objects.get_or_create(project_id=p.pk, employee_id=eid)
                    m.role = (r.get('role') or '')[:100]
                    m.bill_rate = _dec(r.get('bill_rate'), 'bill rate', allow_none=True)
                    m.cost_rate_override = _dec(r.get('cost_rate_override'), 'cost rate', allow_none=True)
                    m.save()
                task_ids = set(Task.objects.filter(project=p).values_list('id', flat=True))
                for r in data.get('task_plans') or []:
                    tid = int(r.get('task_id') or 0)
                    if tid not in task_ids:
                        raise ValueError(f'task {tid} is not part of this project.')
                    t, _ = TaskPlan.objects.get_or_create(task_id=tid)
                    t.planned_hours = _dec(r.get('planned_hours'), 'planned hours')
                    b = S.parse_bool(r.get('billable'))
                    if b is not None:
                        t.billable = b
                    t.save()
        except ValueError as exc:
            return bad(str(exc))
        return Response(_finance_json(request, p))


class ProjectSummaryView(APIView):
    """GET /project-summary/<project_id>/?from=&to= – planned vs actual, billable, cost, billed, profit, margin, budget used."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        p = get_object_or_404(S.visible_projects(request), pk=pk)
        if not S.can_see_finance(request, p):
            return bad('Only the project manager or finance users can see the project costs.', status.HTTP_403_FORBIDDEN)
        d_from, d_to = _dates(request)
        return Response(S.project_summary(p, d_from, d_to))


class EmployeeSummaryView(APIView):
    """GET /employee-summary/?employee=&from=&to= – hours, billable %, cost by project (self, manager, HR)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp, err = _target_employee(request)
        if err:
            return err
        if emp is None:
            return bad('Your user is not linked to an employee.')
        d_from, d_to = _dates(request)
        c = S._ctx(request)
        projects = None
        if not (c.admin or 'view_timesheet' in c.codes or 'view_projectfinance' in c.codes or emp.emp_reporting_manager_id == c.user.id
                or (c.emp and c.emp.pk == emp.pk)):
            projects = S.visible_projects(request).filter(managers=c.emp)  # project managers: only their projects
        out = S.employee_summary(emp, d_from, d_to, projects)
        if c.emp and c.emp.pk == emp.pk and not c.admin and 'view_projectfinance' not in c.codes:
            out.pop('current_cost_rate', None)
        return Response(out)


class CostRateView(APIView):
    """GET /cost-rate/<employee_id>/ – how the hourly cost rate is worked out (admin / finance users)."""
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if not S.has(request, 'view_projectfinance', 'view_employeesalarystructure'):
            return bad('Only finance users can see cost rates.', status.HTTP_403_FORBIDDEN)
        emp = get_object_or_404(_emp_model(), pk=pk)
        setting = CostRateSetting.get()
        monthly = S.monthly_fixed_pay(emp.pk, None, setting)
        days = S.working_days_per_month(emp)
        return Response({'employee_id': emp.pk, 'employee': S._ename(emp), 'monthly_fixed_pay': monthly, 'working_days': days,
                         'hours_per_day': setting.hours_per_day, 'overhead_percent': setting.overhead_percent,
                         'hourly_cost_rate': S.salary_hour_rate(emp, None, setting)})


class SettingsView(APIView):
    """GET / PUT cost rate settings (hours_per_day, include_allowance_categories, overhead_percent)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        s = CostRateSetting.get()
        return Response({'hours_per_day': s.hours_per_day, 'include_allowance_categories': s.include_allowance_categories,
                         'overhead_percent': s.overhead_percent, 'variable_categories': sorted(S.VARIABLE_CATEGORIES)})

    def put(self, request):
        if not S.has(request, 'change_costratesetting'):
            return bad('Only an administrator can change the cost rate settings.', status.HTTP_403_FORBIDDEN)
        s = CostRateSetting.objects.order_by('id').first() or CostRateSetting()
        try:
            if 'hours_per_day' in request.data:
                h = _dec(request.data.get('hours_per_day'), 'hours per day')
                if not (0 < h <= 24):
                    raise ValueError('hours per day: between 0 and 24.')
                s.hours_per_day = h
            if 'overhead_percent' in request.data:
                s.overhead_percent = _dec(request.data.get('overhead_percent'), 'overhead')
            if 'include_allowance_categories' in request.data:
                v = request.data.get('include_allowance_categories') or []
                if not isinstance(v, list):
                    raise ValueError('include allowance categories: a list.')
                s.include_allowance_categories = [str(x) for x in v]
        except ValueError as exc:
            return bad(str(exc))
        s.save()
        return self.get(request)
