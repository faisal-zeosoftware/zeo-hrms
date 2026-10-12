"""Management dashboard and personal work view (/zeo-projects/api/dashboard/, /my/)."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.utils.dateparse import parse_date
from rest_framework.response import Response

from . import access as A
from . import rows as RW
from . import services as S
from .models import (DONE_STAGES, OPEN_STAGES, AMCContract, DevRequest, Milestone, Ticket, WorkLog)
from .views import Base, guard

D0 = Decimal('0')


def period(request):
    q = request.query_params
    today = S.local_today()
    to = parse_date(q.get('to') or '') or today
    frm = parse_date(q.get('from') or '') or (to - timedelta(days=29))
    if frm > to:
        frm, to = to, frm
    return frm, to


def count_by(qs, field, label_field=None, limit=12):
    rows = qs.values(field, *( [label_field] if label_field else [])).annotate(n=Count('id')).order_by('-n')[:limit]
    return [{'id': r[field], 'label': (r.get(label_field) if label_field else r[field]) or 'Not set', 'n': r['n']} for r in rows]


def support_block(request, frm, to):
    base = A.ticket_scope(request)
    open_qs = base.filter(stage__in=OPEN_STAGES)
    period_qs = base.filter(created_at__date__gte=frm, created_at__date__lte=to)
    resolved_qs = base.filter(resolved_at__date__gte=frm, resolved_at__date__lte=to)
    now = S.now()
    breached_open = open_qs.filter(Q(resolution_breached=True) | Q(response_breached=True) | Q(resolution_due__lt=now, paused_at__isnull=True)).count()
    # first response / resolution averages (clock minutes)
    resp, reso = [], []
    for t in base.filter(first_response_at__isnull=False, created_at__date__gte=frm, created_at__date__lte=to).only('created_at', 'first_response_at'):
        resp.append((t.first_response_at - t.created_at).total_seconds() / 60)
    for t in resolved_qs.only('created_at', 'resolved_at'):
        reso.append((t.resolved_at - t.created_at).total_seconds() / 60)
    resolved_total = resolved_qs.count()
    met = resolved_qs.filter(resolution_breached=False).count()
    # received vs resolved per week
    weeks = []
    start = frm - timedelta(days=frm.weekday())
    while start <= to and len(weeks) < 26:
        end = start + timedelta(days=6)
        weeks.append({'week': start.isoformat(), 'received': base.filter(created_at__date__gte=start, created_at__date__lte=end).count(),
                      'resolved': base.filter(resolved_at__date__gte=start, resolved_at__date__lte=end).count()})
        start += timedelta(days=7)
    by_emp = open_qs.values('assigned_to', 'assigned_to__emp_first_name', 'assigned_to__emp_last_name').annotate(n=Count('id')).order_by('-n')[:15]
    csat = base.filter(satisfaction__isnull=False, closed_at__date__gte=frm).aggregate(a=Avg('satisfaction'), n=Count('id'))
    return {
        'open': open_qs.count(), 'unassigned': open_qs.filter(assigned_to__isnull=True).count(),
        'breached_open': breached_open, 'critical_open': open_qs.filter(priority='critical').count(),
        'waiting_customer': open_qs.filter(stage='waiting_customer').count(),
        'received': period_qs.count(), 'resolved': resolved_total,
        'sla_compliance_pct': round(met * 100 / resolved_total, 1) if resolved_total else None,
        'avg_first_response_min': round(sum(resp) / len(resp)) if resp else None,
        'avg_resolution_min': round(sum(reso) / len(reso)) if reso else None,
        'reopened': base.filter(reopen_count__gt=0, updated_at__date__gte=frm).count(),
        'csat_avg': round(csat['a'], 2) if csat['a'] else None, 'csat_count': csat['n'],
        'by_priority': count_by(open_qs, 'priority'),
        'by_team': count_by(open_qs, 'team', 'team__name'),
        'by_customer': count_by(open_qs, 'customer', 'customer__name'),
        'by_stage': count_by(open_qs, 'stage'),
        'by_employee': [{'id': r['assigned_to'], 'label': ' '.join(x for x in [r['assigned_to__emp_first_name'], r['assigned_to__emp_last_name']] if x) or 'Unassigned',
                         'n': r['n']} for r in by_emp],
        'weekly': weeks,
        'breached_list': [RW.ticket_row(t) for t in open_qs.filter(Q(resolution_breached=True) | Q(response_breached=True))
                          .select_related('customer', 'contact', 'product', 'category', 'contract', 'team', 'assigned_to', 'requester_employee')
                          .order_by('resolution_due')[:10]],
    }


def amc_block(request, frm, to):
    today = S.local_today()
    qs = A.in_branches(AMCContract.objects.all(), request).exclude(status__in=('draft', 'cancelled')).select_related('customer')
    for c in qs:
        S.refresh_contract_status(c, today)
    live = qs.filter(start_date__lte=today, end_date__gte=today)

    def expiring(days):
        return live.filter(end_date__lte=today + timedelta(days=days)).exclude(renewal_status='won').count()

    total_hours = used_hours = D0
    unlimited = 0
    contracts = []
    for c in live.prefetch_related('products'):
        u = S.contract_usage(c)
        if c.unlimited_hours:
            unlimited += 1
        else:
            total_hours += u['included_hours']
            used_hours += u['used_hours']
        contracts.append({'id': c.id, 'number': c.number, 'customer_name': c.customer.name, 'end_date': RW.d(c.end_date),
                          'value': float(u['value']), 'revenue': float(u['revenue']), 'cost': float(u['cost']),
                          'margin': float(u['margin']), 'margin_pct': u['margin_pct'], 'used_hours': float(u['used_hours']),
                          'included_hours': float(u['included_hours']), 'remaining_hours': RW.num(u['remaining_hours']),
                          'unlimited': c.unlimited_hours, 'outstanding': float(u['outstanding'])})
    ended = qs.filter(end_date__gte=frm, end_date__lte=to)
    renewed = ended.filter(renewal_status='won').count()
    lost = ended.filter(renewal_status='lost').count()
    return {
        'active': live.count(), 'expiring_30': expiring(30), 'expiring_60': expiring(60), 'expiring_90': expiring(90),
        'expired': qs.filter(end_date__lt=today).exclude(renewal_status='won').filter(end_date__gte=today - timedelta(days=90)).count(),
        'renewals_pending': qs.filter(end_date__lte=today + timedelta(days=90)).exclude(renewal_status__in=('won', 'lost')).filter(end_date__gte=today - timedelta(days=90)).count(),
        'renewed_in_period': renewed, 'lost_in_period': lost,
        'renewal_rate_pct': round(renewed * 100 / (renewed + lost), 1) if (renewed + lost) else None,
        'hours_included': float(total_hours), 'hours_used': float(used_hours), 'hours_remaining': float(total_hours - used_hours),
        'unlimited_contracts': unlimited,
        'contract_value': float(sum((x['value'] for x in contracts), 0)),
        'by_profit': sorted(contracts, key=lambda x: x['margin'])[:10],
        'expiring_list': [RW.contract_row(c) for c in live.filter(end_date__lte=today + timedelta(days=90)).exclude(renewal_status='won')
                          .select_related('customer', 'sla_policy', 'account_manager', 'previous', 'renewal_owner').prefetch_related('products').order_by('end_date')[:15]],
    }


def projects_block(request, frm, to, with_money):
    from ProjectManagement.models import Project, Task
    ids = A.visible_project_ids(request)
    projects = list(Project.objects.filter(pk__in=ids).exclude(status__in=('cancelled',)))
    today = S.local_today()
    ms_all = defaultdict(list)
    for m in Milestone.objects.filter(project_id__in=ids):
        ms_all[m.project_id].append(m)
    health = defaultdict(int)
    rows = []
    planned = actual = D0
    for p in projects:
        if p.status == 'completed':
            health['done'] += 1
            continue
        r = {'id': p.pk, 'title': p.title, 'end_date': RW.d(p.end_date)}
        if with_money:
            prof = S.project_profit(p)
            h = S.project_health(p, ms_all[p.pk], prof.get('_summary'))
            planned += prof['budget_hours']
            actual += prof['actual_hours']
            r.update({'budget_hours': float(prof['budget_hours']), 'actual_hours': float(prof['actual_hours']),
                      'hours_used_pct': prof['hours_used_pct'], 'revenue': float(prof['revenue']), 'margin': float(prof['expected_margin']),
                      'margin_pct': prof['margin_pct'], 'invoiced': float(prof['invoiced'])})
        else:
            h = S.project_health(p, ms_all[p.pk])
        r['health'] = h
        health[h] += 1
        rows.append(r)
    overdue_ms = [dict(RW.milestone_row(m), project_title=next((p.title for p in projects if p.pk == m.project_id), ''))
                  for ms in ms_all.values() for m in ms if m.planned_date and m.planned_date < today and m.status != 'accepted']
    tasks = Task.objects.filter(project_id__in=ids).exclude(status='completed')
    pending = tasks.values('task_members', 'task_members__emp_first_name', 'task_members__emp_last_name').annotate(
        n=Count('id', distinct=True), late=Count('id', filter=Q(end_date__lt=today), distinct=True)).order_by('-n')[:15]
    return {
        'active': len(rows), 'on_track': health['on_track'], 'at_risk': health['at_risk'], 'delayed': health['delayed'],
        'done': health['done'], 'milestones_overdue': len(overdue_ms), 'overdue_milestones': overdue_ms[:10],
        'planned_hours': float(planned), 'actual_hours': float(actual),
        'tasks_open': tasks.count(), 'tasks_overdue': tasks.filter(end_date__lt=today).count(),
        'pending_by_employee': [{'id': r['task_members'], 'label': ' '.join(x for x in [r['task_members__emp_first_name'], r['task_members__emp_last_name']] if x) or 'Not assigned',
                                 'n': r['n'], 'late': r['late']} for r in pending],
        'projects': sorted(rows, key=lambda r: {'delayed': 0, 'at_risk': 1, 'on_track': 2}.get(r['health'], 3))[:20],
    }


def productivity_block(request, frm, to):
    """Per employee: support hours (work logs) + project hours (timesheets), billable share and utilisation."""
    from EmpManagement.models import emp_master
    try:
        from ProjectControl.services import hours_of
        from ProjectControl.models import CostRateSetting, TimesheetExtra
        hpd = Decimal(CostRateSetting.get().hours_per_day or 8)
    except Exception:
        hours_of, TimesheetExtra, hpd = None, None, Decimal(8)
    from ProjectManagement.models import TimeSheet
    cal = S.sla.WorkCalendar()
    work_days, d = 0, frm
    while d <= to:
        work_days += 1 if cal.is_work_day(d) else 0
        d += timedelta(days=1)
    available = Decimal(work_days) * hpd
    data = defaultdict(lambda: {'support': D0, 'project': D0, 'billable': D0})
    for w in A.in_branches(WorkLog.objects.all(), request).filter(date__gte=frm, date__lte=to).exclude(status='rejected').values('employee', 'hours', 'billable', 'counts_against_amc', 'ticket__customer'):
        e = data[w['employee']]
        e['support'] += w['hours']
        if w['billable'] or w['counts_against_amc']:
            e['billable'] += w['hours']   # AMC hours are paid work too
    ts = TimeSheet.objects.filter(date__gte=frm, date__lte=to)
    if not A.can(request, 'dashboard') and not A.is_admin(request):
        me = A.my_emp(request)
        ts = ts.filter(employee=me) if me else ts.none()
    extras = {x.timesheet_id: x for x in TimesheetExtra.objects.filter(timesheet_id__in=ts.values('id'))} if TimesheetExtra else {}
    for t in ts.values('id', 'employee', 'time_spent'):
        x = extras.get(t['id'])
        h = (x.hours if x and not x.running else (hours_of(t['time_spent']) if hours_of else D0))
        e = data[t['employee']]
        e['project'] += h
        if x is None or x.billable:
            e['billable'] += h
    names = {e.pk: S.emp_name(e) for e in emp_master.objects.filter(pk__in=list(data.keys()))}
    waiting = dict(A.in_branches(WorkLog.objects.all(), request).filter(status='submitted').values_list('employee').annotate(n=Count('id')))
    out = []
    for eid, v in data.items():
        total = v['support'] + v['project']
        out.append({'id': eid, 'label': names.get(eid, '?'), 'support_hours': float(v['support']), 'project_hours': float(v['project']),
                    'total_hours': float(total), 'billable_hours': float(v['billable']),
                    'billable_pct': round(float(v['billable'] * 100 / total), 1) if total else None,
                    'utilisation_pct': round(float(v['billable'] * 100 / available), 1) if available else None,
                    'worklogs_waiting': waiting.get(eid, 0)})
    out.sort(key=lambda r: -r['total_hours'])
    return {'available_hours_per_person': float(available), 'work_days': work_days, 'employees': out[:40],
            'worklogs_waiting': A.in_branches(WorkLog.objects.all(), request).filter(status='submitted').count()}


class DashboardView(Base):
    """GET ?from=&to=&blocks=support,amc,projects,productivity"""

    @guard
    def get(self, request):
        A.need(request, 'dashboard', 'The dashboard is for managers. Ask for the Projects dashboard right.')
        frm, to = period(request)
        bq = request.query_params.get('branch')   # v1.22.0 Branch picked on the dashboard
        if bq and str(bq).isdigit():
            allowed = A.branch_ids(request)
            request._zeo_dash_branch = int(bq) if allowed is None or int(bq) in allowed else -1
        blocks = (request.query_params.get('blocks') or 'support,amc,projects,productivity').split(',')
        out = {'from': frm.isoformat(), 'to': to.isoformat(), 'currency': S.settings().currency}
        if 'support' in blocks:
            out['support'] = support_block(request, frm, to)
        if 'amc' in blocks and A.can(request, 'amc_view'):
            out['amc'] = amc_block(request, frm, to)
        if 'projects' in blocks:
            out['projects'] = projects_block(request, frm, to, A.can(request, 'finance') or A.is_admin(request))
        if 'productivity' in blocks:
            out['productivity'] = productivity_block(request, frm, to)
        return Response(RW.jsonable(out))


class MyWorkView(Base):
    """GET → the user's open tickets, tickets of their teams waiting for someone, their work logs this week,
    development tasks and items waiting for their approval."""

    @guard
    def get(self, request):
        e = A.my_emp(request)
        rel = ('customer', 'contact', 'product', 'category', 'contract', 'team', 'assigned_to', 'requester_employee')
        mine = A.in_branches(Ticket.objects.all(), request).filter(assigned_to=e, stage__in=OPEN_STAGES).select_related(*rel).order_by('resolution_due') if e else Ticket.objects.none()
        team_q = A.in_branches(Ticket.objects.all(), request).filter(stage__in=OPEN_STAGES, assigned_to__isnull=True).filter(Q(team__members=e) | Q(team__lead=e)).distinct() if e else Ticket.objects.none()
        if A.can(request, 'ticket_manage'):
            team_q = A.in_branches(Ticket.objects.all(), request).filter(stage__in=OPEN_STAGES, assigned_to__isnull=True)
        week = S.local_today() - timedelta(days=S.local_today().weekday())
        logs = A.in_branches(WorkLog.objects.all(), request).filter(employee=e, date__gte=week).select_related('ticket', 'ticket__customer', 'employee') if e else WorkLog.objects.none()
        devs = A.in_branches(DevRequest.objects.all(), request).filter(assigned_to=e).exclude(status__in=('done', 'rejected', 'cancelled')).select_related('ticket', 'customer') if e else DevRequest.objects.none()
        approvals = {
            'dev_tech': A.in_branches(DevRequest.objects.all(), request).filter(status='submitted').count() if A.can(request, 'dev_tech') else 0,
            'dev_commercial': A.in_branches(DevRequest.objects.all(), request).filter(status='tech_approved').count() if A.can(request, 'dev_commercial') else 0,
            'worklogs': A.in_branches(WorkLog.objects.all(), request).filter(status='submitted').count() if A.can(request, 'worklog_approve') else 0,
            'tickets': A.in_branches(Ticket.objects.all(), request).filter(approval_status='pending', stage__in=OPEN_STAGES).count() if A.can(request, 'ticket_manage') else 0,
        }
        tasks = []
        if e is not None:
            from ProjectManagement.models import Task
            from .models import TaskDetail
            today = S.local_today()
            qs = Task.objects.filter(task_members=e).exclude(status='completed') \
                .exclude(project__status__in=('completed', 'cancelled')).select_related('project').order_by('end_date', 'id')[:60]
            det = {x.task_id: x for x in TaskDetail.objects.filter(task_id__in=[t.pk for t in qs])}
            for t in qs:
                dd = det.get(t.pk)
                tasks.append({'id': t.pk, 'title': t.title, 'project_id': t.project_id, 'project': t.project.title, 'status': t.status,
                              'status_label': t.get_status_display(), 'end_date': RW.d(t.end_date), 'start_date': RW.d(t.start_date),
                              'overdue': bool(t.end_date and t.end_date < today), 'days_late': (today - t.end_date).days if t.end_date and t.end_date < today else 0,
                              'planned_hours': RW.num(dd.estimated_hours) if dd else 0, 'progress': dd.progress if dd else 0})
        requested = A.in_branches(Ticket.objects.all(), request).filter(requester_employee=e).select_related(*rel).order_by('-created_at')[:20] if e else []
        return Response({
            'employee': RW.emp_ref(e), 'rights': A.rights(request),
            'tickets': [RW.ticket_row(t) for t in mine[:100]],
            'unassigned': [RW.ticket_row(t) for t in team_q.select_related(*rel).order_by('resolution_due')[:50]],
            'worklogs_week': [RW.worklog_row(w) for w in logs],
            'hours_week': float(sum((w.hours for w in logs), D0)) if e else 0,
            'dev_requests': [RW.dev_row(d) for d in devs],
            'approvals': approvals,
            'requested': [RW.ticket_row(t, staff=False) for t in requested],
            'tasks': tasks,
        })
