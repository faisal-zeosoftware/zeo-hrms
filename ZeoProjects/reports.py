"""Projects reports (/zeo-projects/api/reports/ and /reports/<key>/).

Every report returns the same shape so one screen shows them all:
    {key, title, group, description, filters: [...], columns: [{key, label, type}], rows: [...], totals: {...}}
Column types: text, number, hours, money, pct, minutes, date, datetime, badge.
A row may carry `link_to` = {page, id} (or {page, query}) for the drill-down.

Filters (query string): from, to (dates), customer, team, employee, project, status, window (days), group (support
"open tickets by" grouping). Rows are limited by the user's rights exactly like the screens.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils.dateparse import parse_date

from . import access as A
from . import services as S
from . import sla
from .models import (DONE_STAGES, OPEN_STAGES, PRIORITY_ORDER, AMCContract, DevRequest, Milestone, ProjectProfile, Ticket,
                     WorkLog)

D0 = Decimal('0')


def f(v):
    return None if v is None else float(v)


def col(key, label, type='text'):
    return {'key': key, 'label': label, 'type': type}


class P:
    """Parsed filters."""

    def __init__(self, request):
        q = request.query_params
        self.request = request   # v1.22.0: branch rights in every report
        today = S.local_today()
        self.to = parse_date(q.get('to') or '') or today
        self.frm = parse_date(q.get('from') or '') or (self.to - timedelta(days=29))
        if self.frm > self.to:
            self.frm, self.to = self.to, self.frm
        self.customer = q.get('customer') or None
        self.team = q.get('team') or None
        self.employee = q.get('employee') or None
        self.project = q.get('project') or None
        self.status = q.get('status') or ''
        self.contract = q.get('contract') or None
        self.group = q.get('group') or 'employee'
        try:
            self.window = max(1, min(365, int(q.get('window') or 90)))
        except ValueError:
            self.window = 90
        self.today = today


def tickets(request, p, qs=None):
    qs = A.ticket_scope(request, qs if qs is not None else A.in_branches(Ticket.objects.all(), request).all())
    if p.customer:
        qs = qs.filter(customer_id=p.customer)
    if p.team:
        qs = qs.filter(team_id=p.team)
    if p.employee:
        qs = qs.filter(assigned_to_id=p.employee)
    return qs


def ename(first, last, fallback='Not assigned'):
    n = ' '.join(x for x in (first, last) if x)
    return n or fallback


def tlink(t_id):
    return {'page': 'tickets', 'id': t_id}


# ------------------------------------------------------------------ support
def r_open_tickets(request, p):
    """Open tickets grouped by employee / team / customer / priority."""
    fields = {'employee': ('assigned_to', 'assigned_to__emp_first_name', 'assigned_to__emp_last_name'),
              'team': ('team', 'team__name'), 'customer': ('customer', 'customer__name'), 'priority': ('priority',)}
    g = p.group if p.group in fields else 'employee'
    qs = tickets(request, p).filter(stage__in=OPEN_STAGES)
    now = S.now()
    data = defaultdict(lambda: {'open': 0, 'critical': 0, 'high': 0, 'breached': 0, 'waiting': 0, 'oldest': None, 'id': None, 'label': ''})
    for t in qs.values('id', 'priority', 'stage', 'resolution_breached', 'response_breached', 'resolution_due', 'paused_at', 'created_at', *fields[g]):
        k = t[fields[g][0]]
        d = data[k]
        d['id'] = k
        if g == 'employee':
            d['label'] = ename(t['assigned_to__emp_first_name'], t['assigned_to__emp_last_name'])
        elif g == 'team':
            d['label'] = t['team__name'] or 'No team'
        elif g == 'customer':
            d['label'] = t['customer__name'] or 'Internal'
        else:
            d['label'] = dict(Ticket._meta.get_field('priority').choices).get(k, k)
        d['open'] += 1
        d['critical'] += t['priority'] == 'critical'
        d['high'] += t['priority'] == 'high'
        d['waiting'] += t['stage'] == 'waiting_customer'
        if t['resolution_breached'] or t['response_breached'] or (t['resolution_due'] and not t['paused_at'] and t['resolution_due'] < now):
            d['breached'] += 1
        age = (now - t['created_at']).total_seconds() / 86400
        d['oldest'] = max(d['oldest'] or 0, age)
    rows = []
    qkey = {'employee': 'assigned', 'team': 'team', 'customer': 'customer', 'priority': 'priority'}[g]
    for d in data.values():
        rows.append({'label': d['label'], 'open': d['open'], 'critical': d['critical'], 'high': d['high'], 'breached': d['breached'],
                     'waiting': d['waiting'], 'oldest_days': round(d['oldest'] or 0, 1),
                     'link_to': {'page': 'tickets', 'query': {'view': 'open', qkey: d['id'] if d['id'] is not None else ('none' if g == 'employee' else '')}}})
    rows.sort(key=lambda r: PRIORITY_ORDER.get(r['link_to']['query'][qkey], 9) if g == 'priority' else -r['open'])
    label = {'employee': 'Engineer', 'team': 'Team', 'customer': 'Customer', 'priority': 'Priority'}[g]
    return {'columns': [col('label', label), col('open', 'Open', 'number'), col('critical', 'Critical', 'number'), col('high', 'High', 'number'),
                        col('breached', 'SLA breached', 'number'), col('waiting', 'Waiting for customer', 'number'),
                        col('oldest_days', 'Oldest (days)', 'number')],
            'rows': rows, 'totals': {'label': 'Total', 'open': sum(r['open'] for r in rows), 'critical': sum(r['critical'] for r in rows),
                                     'high': sum(r['high'] for r in rows), 'breached': sum(r['breached'] for r in rows),
                                     'waiting': sum(r['waiting'] for r in rows)}}


def r_received_resolved(request, p):
    """Per week: received, resolved, closed, reopened and the open backlog at the end of the week."""
    base = tickets(request, p)
    rows = []
    start = p.frm - timedelta(days=p.frm.weekday())
    while start <= p.to and len(rows) < 60:
        end = start + timedelta(days=6)
        rec = base.filter(created_at__date__gte=start, created_at__date__lte=end).count()
        res = base.filter(resolved_at__date__gte=start, resolved_at__date__lte=end).count()
        clo = base.filter(closed_at__date__gte=start, closed_at__date__lte=end).count()
        backlog = base.filter(created_at__date__lte=end).filter(Q(resolved_at__isnull=True) | Q(resolved_at__date__gt=end)).count()
        rows.append({'week': start.isoformat(), 'received': rec, 'resolved': res, 'closed': clo, 'backlog': backlog,
                     'net': rec - res})
        start += timedelta(days=7)
    return {'columns': [col('week', 'Week starting', 'date'), col('received', 'Received', 'number'), col('resolved', 'Resolved', 'number'),
                        col('closed', 'Closed', 'number'), col('net', 'Received – resolved', 'number'), col('backlog', 'Open at week end', 'number')],
            'rows': rows, 'totals': {'week': 'Total', 'received': sum(r['received'] for r in rows), 'resolved': sum(r['resolved'] for r in rows),
                                     'closed': sum(r['closed'] for r in rows), 'net': sum(r['net'] for r in rows)},
            'chart': {'type': 'bars', 'x': 'week', 'series': ['received', 'resolved']}}


def r_response_times(request, p):
    """Per engineer (or team): average first response and resolution in working hours and clock time, SLA compliance."""
    by_team = p.group == 'team'
    qs = tickets(request, p).filter(created_at__date__gte=p.frm, created_at__date__lte=p.to).select_related('assigned_to', 'team')
    cal = sla.WorkCalendar()
    data = defaultdict(lambda: defaultdict(float))
    names = {}
    for t in qs:
        k = (t.team_id if by_team else t.assigned_to_id)
        names[k] = (t.team.name if t.team_id else 'No team') if by_team else (S.emp_name(t.assigned_to) if t.assigned_to_id else 'Not assigned')
        d = data[k]
        d['tickets'] += 1
        if t.first_response_at:
            d['resp_n'] += 1
            d['resp_bh'] += sla.minutes_between(t.created_at, t.first_response_at, True, cal)
            d['resp_clock'] += (t.first_response_at - t.created_at).total_seconds() / 60
            d['resp_met'] += 0 if t.response_breached else 1
        if t.resolved_at:
            d['res_n'] += 1
            d['res_bh'] += sla.minutes_between(t.created_at, t.resolved_at, True, cal)
            d['res_clock'] += (t.resolved_at - t.created_at).total_seconds() / 60
            d['res_met'] += 0 if t.resolution_breached else 1
        d['breached'] += 1 if (t.response_breached or t.resolution_breached) else 0
    rows = []
    for k, d in data.items():
        rows.append({'label': names[k], 'tickets': int(d['tickets']), 'responded': int(d['resp_n']), 'resolved': int(d['res_n']),
                     'resp_bh': round(d['resp_bh'] / d['resp_n']) if d['resp_n'] else None,
                     'resp_clock': round(d['resp_clock'] / d['resp_n']) if d['resp_n'] else None,
                     'res_bh': round(d['res_bh'] / d['res_n']) if d['res_n'] else None,
                     'res_clock': round(d['res_clock'] / d['res_n']) if d['res_n'] else None,
                     'resp_pct': round(d['resp_met'] * 100 / d['resp_n'], 1) if d['resp_n'] else None,
                     'res_pct': round(d['res_met'] * 100 / d['res_n'], 1) if d['res_n'] else None,
                     'breached': int(d['breached'])})
    rows.sort(key=lambda r: -r['tickets'])
    tot = {k: sum(data[x][k] for x in data) for k in ('tickets', 'resp_n', 'res_n', 'resp_met', 'res_met', 'breached', 'resp_bh', 'res_bh')}
    return {'columns': [col('label', 'Team' if by_team else 'Engineer'), col('tickets', 'Tickets', 'number'), col('responded', 'Responded', 'number'),
                        col('resp_bh', 'Avg. first response (working)', 'minutes'), col('resp_clock', 'Avg. first response (clock)', 'minutes'),
                        col('resp_pct', 'Response SLA met', 'pct'), col('resolved', 'Resolved', 'number'),
                        col('res_bh', 'Avg. resolution (working)', 'minutes'), col('res_clock', 'Avg. resolution (clock)', 'minutes'),
                        col('res_pct', 'Resolution SLA met', 'pct'), col('breached', 'Breached', 'number')],
            'rows': rows,
            'totals': {'label': 'All', 'tickets': int(tot['tickets']), 'responded': int(tot['resp_n']), 'resolved': int(tot['res_n']),
                       'resp_bh': round(tot['resp_bh'] / tot['resp_n']) if tot['resp_n'] else None,
                       'res_bh': round(tot['res_bh'] / tot['res_n']) if tot['res_n'] else None,
                       'resp_pct': round(tot['resp_met'] * 100 / tot['resp_n'], 1) if tot['resp_n'] else None,
                       'res_pct': round(tot['res_met'] * 100 / tot['res_n'], 1) if tot['res_n'] else None,
                       'breached': int(tot['breached'])}}


def r_sla_breaches(request, p):
    """Tickets that missed the response or resolution target (open ones and those created in the period)."""
    now = S.now()
    qs = tickets(request, p).filter(Q(response_breached=True) | Q(resolution_breached=True) |
                                    Q(stage__in=OPEN_STAGES, resolution_due__lt=now, paused_at__isnull=True))
    qs = qs.filter(Q(stage__in=OPEN_STAGES) | Q(created_at__date__gte=p.frm, created_at__date__lte=p.to))
    rows = []
    for t in qs.select_related('customer', 'assigned_to', 'team').order_by('resolution_due'):
        st = S.sla_state(t)
        over = None
        if t.resolution_due:
            end = t.resolved_at or now
            over = round((end - t.resolution_due).total_seconds() / 60) if end > t.resolution_due else 0
        rows.append({'number': t.number, 'subject': t.subject, 'customer': t.customer.name if t.customer_id else 'Internal',
                     'priority': t.get_priority_display(), 'stage': t.get_stage_display(),
                     'engineer': S.emp_name(t.assigned_to) if t.assigned_to_id else '', 'team': t.team.name if t.team_id else '',
                     'missed': 'Response and resolution' if (t.response_breached and (t.resolution_breached or st['state'] == 'breached'))
                     else ('Response' if t.response_breached else 'Resolution'),
                     'due': t.resolution_due.isoformat() if t.resolution_due else None, 'over': over, 'escalation': t.escalation_level,
                     'link_to': tlink(t.id)})
    return {'columns': [col('number', 'Ticket'), col('subject', 'Subject'), col('customer', 'Customer'), col('priority', 'Priority', 'badge'),
                        col('stage', 'Stage', 'badge'), col('engineer', 'Engineer'), col('team', 'Team'), col('missed', 'Missed'),
                        col('due', 'Resolve by', 'datetime'), col('over', 'Over by (clock)', 'minutes'), col('escalation', 'Escalation', 'number')],
            'rows': rows, 'totals': {'number': f'{len(rows)} ticket(s)'}}


def r_reopened_csat(request, p):
    """Per engineer: resolved, reopened, reopen rate, ratings and average satisfaction."""
    qs = tickets(request, p).filter(resolved_at__date__gte=p.frm, resolved_at__date__lte=p.to)
    data = defaultdict(lambda: {'resolved': 0, 'reopened': 0, 'ratings': 0, 'score': 0, 'low': 0})
    names = {}
    for t in qs.values('assigned_to', 'assigned_to__emp_first_name', 'assigned_to__emp_last_name', 'reopen_count', 'satisfaction'):
        k = t['assigned_to']
        names[k] = ename(t['assigned_to__emp_first_name'], t['assigned_to__emp_last_name'])
        d = data[k]
        d['resolved'] += 1
        d['reopened'] += 1 if t['reopen_count'] else 0
        if t['satisfaction']:
            d['ratings'] += 1
            d['score'] += t['satisfaction']
            d['low'] += 1 if t['satisfaction'] <= 2 else 0
    rows = [{'label': names[k], 'resolved': d['resolved'], 'reopened': d['reopened'],
             'reopen_pct': round(d['reopened'] * 100 / d['resolved'], 1) if d['resolved'] else None,
             'ratings': d['ratings'], 'csat': round(d['score'] / d['ratings'], 2) if d['ratings'] else None, 'low': d['low'],
             'link_to': {'page': 'tickets', 'query': {'view': 'done', 'assigned': k if k else 'none'}}} for k, d in data.items()]
    rows.sort(key=lambda r: -r['resolved'])
    R = sum(r['resolved'] for r in rows)
    N = sum(r['ratings'] for r in rows)
    return {'columns': [col('label', 'Engineer'), col('resolved', 'Resolved', 'number'), col('reopened', 'Reopened', 'number'),
                        col('reopen_pct', 'Reopen rate', 'pct'), col('ratings', 'Ratings', 'number'), col('csat', 'Avg. rating (of 5)', 'number'),
                        col('low', 'Ratings 1–2', 'number')],
            'rows': rows, 'totals': {'label': 'All', 'resolved': R, 'reopened': sum(r['reopened'] for r in rows),
                                     'reopen_pct': round(sum(r['reopened'] for r in rows) * 100 / R, 1) if R else None, 'ratings': N,
                                     'csat': round(sum(d['score'] for d in data.values()) / N, 2) if N else None,
                                     'low': sum(r['low'] for r in rows)}}


# ------------------------------------------------------------------ AMC
def contracts(p):
    qs = A.in_branches(AMCContract.objects.all(), p.request).exclude(status__in=('draft', 'cancelled')).select_related('customer', 'account_manager')
    for c in qs:
        S.refresh_contract_status(c, p.today)
    if p.customer:
        qs = qs.filter(customer_id=p.customer)
    if p.status:
        qs = qs.filter(status__in=p.status.split(','))
    return qs


def r_amc_profit(request, p):
    """Every contract: value, billed, paid, outstanding, hours included / used / left, cost, revenue, margin."""
    A.need(request, 'amc_view')
    rows = []
    for c in contracts(p).prefetch_related('products'):
        u = S.contract_usage(c)
        rows.append({'number': c.number, 'customer': c.customer.name, 'status': c.get_status_display(), 'end_date': c.end_date.isoformat(),
                     'days_left': (c.end_date - p.today).days, 'value': f(u['value']), 'billed': f(u['billed']), 'paid': f(u['paid']),
                     'outstanding': f(u['outstanding']), 'included': 'Unlimited' if c.unlimited_hours else f(u['included_hours']),
                     'used': f(u['used_hours']), 'remaining': f(u['remaining_hours']) if u['remaining_hours'] is not None else None,
                     'used_pct': u['used_pct'], 'outside': f(u['outside_value']), 'revenue': f(u['revenue']), 'cost': f(u['cost']),
                     'margin': f(u['margin']), 'margin_pct': u['margin_pct'], 'link_to': {'page': 'contracts', 'id': c.id}})
    rows.sort(key=lambda r: r['margin'] if r['margin'] is not None else 0)
    s = lambda k: round(sum((r[k] or 0) for r in rows if isinstance(r[k], (int, float))), 2)
    rev, cost = s('revenue'), s('cost')
    return {'columns': [col('number', 'Contract'), col('customer', 'Customer'), col('status', 'Status', 'badge'), col('end_date', 'Ends', 'date'),
                        col('days_left', 'Days left', 'number'), col('value', 'Value', 'money'), col('billed', 'Billed', 'money'),
                        col('paid', 'Paid', 'money'), col('outstanding', 'Outstanding', 'money'), col('included', 'Hours incl.', 'hours'),
                        col('used', 'Hours used', 'hours'), col('remaining', 'Hours left', 'hours'), col('used_pct', 'Used', 'pct'),
                        col('outside', 'Chargeable outside', 'money'), col('revenue', 'Revenue', 'money'), col('cost', 'Cost', 'money'),
                        col('margin', 'Margin', 'money'), col('margin_pct', 'Margin %', 'pct')],
            'rows': rows, 'totals': {'number': f'{len(rows)} contract(s)', 'value': s('value'), 'billed': s('billed'), 'paid': s('paid'),
                                     'outstanding': s('outstanding'), 'used': s('used'), 'outside': s('outside'), 'revenue': rev, 'cost': cost,
                                     'margin': round(rev - cost, 2), 'margin_pct': round((rev - cost) * 100 / rev, 1) if rev else None}}


def r_amc_expiring(request, p):
    """Contracts ending in the next N days (30 / 60 / 90 buckets) and expired ones not renewed."""
    A.need(request, 'amc_view')
    rows = []
    for c in contracts(p).filter(end_date__lte=p.today + timedelta(days=p.window), end_date__gte=p.today - timedelta(days=p.window)) \
            .exclude(renewal_status='won').select_related('renewal_owner').order_by('end_date'):
        left = (c.end_date - p.today).days
        bucket = 'Expired' if left < 0 else ('0–30 days' if left <= 30 else ('31–60 days' if left <= 60 else '61–90+ days'))
        u = S.contract_usage(c)
        rows.append({'bucket': bucket, 'number': c.number, 'customer': c.customer.name, 'end_date': c.end_date.isoformat(), 'days_left': left,
                     'value': f(c.value), 'renewal': c.get_renewal_status_display(),
                     'owner': S.emp_name(c.renewal_owner or c.account_manager) if (c.renewal_owner_id or c.account_manager_id) else '',
                     'quote': c.renewal_quote_ref, 'quoted': f(c.renewal_value), 'used_pct': u['used_pct'],
                     'link_to': {'page': 'contracts', 'id': c.id}})
    return {'columns': [col('bucket', 'When', 'badge'), col('number', 'Contract'), col('customer', 'Customer'), col('end_date', 'Ends', 'date'),
                        col('days_left', 'Days left', 'number'), col('value', 'Value', 'money'), col('renewal', 'Renewal', 'badge'),
                        col('owner', 'Owner'), col('quote', 'Quotation'), col('quoted', 'Quoted value', 'money'), col('used_pct', 'Hours used', 'pct')],
            'rows': rows, 'totals': {'bucket': f'{len(rows)} contract(s)', 'value': round(sum(r['value'] or 0 for r in rows), 2),
                                     'quoted': round(sum(r['quoted'] or 0 for r in rows), 2)}}


def r_amc_renewals(request, p):
    """Contracts that ended (or end) in the period: renewed, lost (with reason) or still pending; renewal rate."""
    A.need(request, 'amc_view')
    qs = contracts(p).filter(end_date__gte=p.frm, end_date__lte=p.to).order_by('end_date')
    rows = []
    for c in qs:
        new = c.renewals.exclude(status='cancelled').order_by('-id').first()
        rows.append({'number': c.number, 'customer': c.customer.name, 'end_date': c.end_date.isoformat(), 'value': f(c.value),
                     'result': c.get_renewal_status_display(), 'new_contract': new.number if new else '',
                     'new_value': f(new.value) if new else None, 'change': f(new.value - c.value) if new else None,
                     'lost_reason': c.lost_reason, 'link_to': {'page': 'contracts', 'id': c.id}})
    won = sum(1 for r in rows if r['result'] == 'Renewed')
    lost = sum(1 for r in rows if r['result'] == 'Lost')
    return {'columns': [col('number', 'Contract'), col('customer', 'Customer'), col('end_date', 'Ends', 'date'), col('value', 'Value', 'money'),
                        col('result', 'Renewal', 'badge'), col('new_contract', 'New contract'), col('new_value', 'New value', 'money'),
                        col('change', 'Change', 'money'), col('lost_reason', 'Why lost')],
            'rows': rows, 'totals': {'number': f'{won} renewed · {lost} lost · {len(rows) - won - lost} pending',
                                     'value': round(sum(r['value'] or 0 for r in rows), 2),
                                     'new_value': round(sum(r['new_value'] or 0 for r in rows), 2)},
            'summary': {'renewal_rate_pct': round(won * 100 / (won + lost), 1) if (won + lost) else None}}


# ------------------------------------------------------------------ projects
def projects(request, p):
    from ProjectManagement.models import Project
    qs = Project.objects.filter(pk__in=A.visible_project_ids(request)).exclude(status='cancelled')
    if p.project:
        qs = qs.filter(pk=p.project)
    if p.customer:
        qs = qs.filter(pk__in=ProjectProfile.objects.filter(customer_id=p.customer).values('project_id'))
    if p.status:
        qs = qs.filter(status__in=p.status.split(','))
    return qs


def r_project_status(request, p):
    """Every project: health, dates, milestones, tasks, planned vs actual hours, budget used, value, invoiced, margin."""
    from ProjectManagement.models import Task
    money = A.can(request, 'finance') or A.is_admin(request)
    rows = []
    profs = {x.project_id: x for x in ProjectProfile.objects.select_related('customer')}
    for pr in projects(request, p).order_by('end_date'):
        ms = list(Milestone.objects.filter(project_id=pr.pk))
        tasks = Task.objects.filter(project=pr)
        prof = profs.get(pr.pk)
        r = {'project': pr.title, 'number': prof.number if prof else '', 'customer': prof.customer.name if prof and prof.customer_id else 'Internal',
             'status': pr.get_status_display(), 'start': pr.start_date.isoformat() if pr.start_date else None,
             'end': pr.end_date.isoformat() if pr.end_date else None,
             'milestones': f"{len([m for m in ms if m.status == 'accepted'])} / {len(ms)}",
             'ms_overdue': len([m for m in ms if m.planned_date and m.planned_date < p.today and m.status != 'accepted']),
             'tasks_open': tasks.exclude(status='completed').count(),
             'tasks_overdue': tasks.exclude(status='completed').filter(end_date__lt=p.today).count(), 'link_to': {'page': 'projects', 'id': pr.pk}}
        if money:
            x = S.project_profit(pr)
            r.update({'health': S.project_health(pr, ms, x.get('_summary')),
                      'planned': f(x['budget_hours']), 'actual': f(x['actual_hours']), 'hours_pct': x['hours_used_pct'],
                      'budget': f(x['budget_amount']), 'budget_pct': x['budget_used_pct'], 'revenue': f(x['revenue']),
                      'invoiced': f(x['invoiced']), 'cost': f(x['employee_cost'] + x['expenses']), 'margin': f(x['expected_margin']),
                      'margin_pct': x['margin_pct']})
        else:
            r['health'] = S.project_health(pr, ms)
        r['health'] = {'on_track': 'On track', 'at_risk': 'At risk', 'delayed': 'Delayed', 'done': 'Done'}.get(r['health'], r['health'])
        rows.append(r)
    cols = [col('project', 'Project'), col('customer', 'Customer'), col('health', 'Health', 'badge'), col('status', 'Status'),
            col('end', 'Planned end', 'date'), col('milestones', 'Milestones accepted'), col('ms_overdue', 'Milestones late', 'number'),
            col('tasks_open', 'Open tasks', 'number'), col('tasks_overdue', 'Late tasks', 'number')]
    totals = {'project': f'{len(rows)} project(s)', 'ms_overdue': sum(r['ms_overdue'] for r in rows),
              'tasks_open': sum(r['tasks_open'] for r in rows), 'tasks_overdue': sum(r['tasks_overdue'] for r in rows)}
    if money:
        cols += [col('planned', 'Planned h', 'hours'), col('actual', 'Actual h', 'hours'), col('hours_pct', 'Hours used', 'pct'),
                 col('budget', 'Cost budget', 'money'), col('budget_pct', 'Budget used', 'pct'), col('revenue', 'Revenue', 'money'),
                 col('invoiced', 'Invoiced', 'money'), col('cost', 'Cost so far', 'money'), col('margin', 'Expected margin', 'money'),
                 col('margin_pct', 'Margin %', 'pct')]
        for k in ('planned', 'actual', 'budget', 'revenue', 'invoiced', 'cost', 'margin'):
            totals[k] = round(sum(r.get(k) or 0 for r in rows), 2)
    return {'columns': cols, 'rows': rows, 'totals': totals}


def r_milestones_overdue(request, p):
    from ProjectManagement.models import Project
    ids = list(projects(request, p).values_list('pk', flat=True))
    titles = dict(Project.objects.filter(pk__in=ids).values_list('id', 'title'))
    profs = {x.project_id: x for x in ProjectProfile.objects.filter(project_id__in=ids).select_related('customer')}
    rows = []
    for m in Milestone.objects.filter(project_id__in=ids, planned_date__lt=p.today).exclude(status='accepted').order_by('planned_date'):
        pr = profs.get(m.project_id)
        rows.append({'project': titles.get(m.project_id, ''), 'customer': pr.customer.name if pr and pr.customer_id else '',
                     'milestone': m.name, 'planned': m.planned_date.isoformat(), 'days_late': (p.today - m.planned_date).days,
                     'status': m.get_status_display(), 'amount': f(m.amount), 'link_to': {'page': 'projects', 'id': m.project_id}})
    return {'columns': [col('project', 'Project'), col('customer', 'Customer'), col('milestone', 'Milestone'), col('planned', 'Planned', 'date'),
                        col('days_late', 'Days late', 'number'), col('status', 'Status', 'badge'), col('amount', 'Amount', 'money')],
            'rows': rows, 'totals': {'project': f'{len(rows)} milestone(s)', 'amount': round(sum(r['amount'] or 0 for r in rows), 2)}}


def r_tasks_by_employee(request, p):
    """Per employee: open, in progress, late tasks and planned hours still open; open and late tickets."""
    from ProjectManagement.models import Task
    from EmpManagement.models import emp_master
    ids = list(projects(request, p).values_list('pk', flat=True))
    data = defaultdict(lambda: {'open': 0, 'progress': 0, 'late': 0, 'hours': D0, 'tickets': 0, 'tickets_late': 0})
    try:
        from ProjectControl.models import TaskPlan
        plans = dict(TaskPlan.objects.values_list('task_id', 'planned_hours'))
    except Exception:
        plans = {}
    for t in Task.objects.filter(project_id__in=ids).exclude(status='completed').prefetch_related('task_members'):
        for e in t.task_members.all():
            if p.employee and str(e.pk) != str(p.employee):
                continue
            d = data[e.pk]
            d['open'] += 1
            d['progress'] += t.status == 'in_progress'
            d['late'] += 1 if (t.end_date and t.end_date < p.today) else 0
            d['hours'] += Decimal(plans.get(t.pk) or 0)
    now = S.now()
    for t in tickets(request, p).filter(stage__in=OPEN_STAGES, assigned_to__isnull=False).values('assigned_to', 'resolution_due', 'paused_at', 'resolution_breached'):
        d = data[t['assigned_to']]
        d['tickets'] += 1
        if t['resolution_breached'] or (t['resolution_due'] and not t['paused_at'] and t['resolution_due'] < now):
            d['tickets_late'] += 1
    names = {e.pk: S.emp_name(e) for e in emp_master.objects.filter(pk__in=list(data.keys()))}
    rows = [{'label': names.get(k, '?'), 'open': d['open'], 'progress': d['progress'], 'late': d['late'], 'hours': f(d['hours']),
             'tickets': d['tickets'], 'tickets_late': d['tickets_late'], 'overdue_total': d['late'] + d['tickets_late'],
             'link_to': {'page': 'tickets', 'query': {'view': 'open', 'assigned': k}}} for k, d in data.items()]
    rows.sort(key=lambda r: (-r['overdue_total'], -r['open']))
    return {'columns': [col('label', 'Employee'), col('open', 'Open tasks', 'number'), col('progress', 'In progress', 'number'),
                        col('late', 'Late tasks', 'number'), col('hours', 'Planned h open', 'hours'), col('tickets', 'Open tickets', 'number'),
                        col('tickets_late', 'Tickets past SLA', 'number'), col('overdue_total', 'All overdue work', 'number')],
            'rows': rows, 'totals': {'label': 'All', **{k: sum(r[k] for r in rows) for k in ('open', 'progress', 'late', 'tickets', 'tickets_late', 'overdue_total')},
                                     'hours': round(sum(r['hours'] or 0 for r in rows), 2)}}


# ------------------------------------------------------------------ people
def r_utilisation(request, p):
    """Per employee: available hours, support and project hours, billable hours, billable share and utilisation, open work."""
    from .dashboard import productivity_block
    block = productivity_block(request, p.frm, p.to)
    work = {r['label']: r for r in r_tasks_by_employee(request, p)['rows']}
    rows = []
    for e in block['employees']:
        w = work.get(e['label'], {})
        if p.employee and str(e['id']) != str(p.employee):
            continue
        rows.append({'label': e['label'], 'available': block['available_hours_per_person'], 'support': e['support_hours'],
                     'project': e['project_hours'], 'total': e['total_hours'], 'billable': e['billable_hours'],
                     'billable_pct': e['billable_pct'], 'utilisation': e['utilisation_pct'],
                     'open_tasks': w.get('open', 0), 'overdue': w.get('overdue_total', 0), 'waiting': e['worklogs_waiting']})
    return {'columns': [col('label', 'Employee'), col('available', 'Available h', 'hours'), col('support', 'Support h', 'hours'),
                        col('project', 'Project h', 'hours'), col('total', 'Total h', 'hours'), col('billable', 'Billable h', 'hours'),
                        col('billable_pct', 'Billable share', 'pct'), col('utilisation', 'Billable utilisation', 'pct'),
                        col('open_tasks', 'Open tasks', 'number'), col('overdue', 'Overdue work', 'number'), col('waiting', 'Logs waiting', 'number')],
            'rows': rows, 'totals': {'label': f"{block['work_days']} working days", **{k: round(sum(r[k] or 0 for r in rows), 2)
                                                                                    for k in ('support', 'project', 'total', 'billable')},
                                     'waiting': sum(r['waiting'] for r in rows)}}


def r_worklogs_pending(request, p):
    qs = A.in_branches(WorkLog.objects.all(), request).filter(status='submitted').select_related('ticket', 'ticket__customer', 'employee')
    if not A.can(request, 'worklog_approve'):
        e = A.my_emp(request)
        qs = qs.filter(employee=e) if e else qs.none()
    if p.employee:
        qs = qs.filter(employee_id=p.employee)
    rows = [{'date': w.date.isoformat(), 'employee': S.emp_name(w.employee), 'ticket': w.ticket.number, 'subject': w.ticket.subject,
             'customer': w.ticket.customer.name if w.ticket.customer_id else 'Internal', 'hours': f(w.hours),
             'type': ('Onsite' if w.onsite else 'Remote') + (' · chargeable' if w.billable else (' · AMC' if w.counts_against_amc else '')),
             'charges': f(w.travel_cost + w.other_charges), 'days_waiting': (p.today - w.created_at.date()).days, 'link_to': {'page': 'worklogs', 'id': None}}
            for w in qs.order_by('date')]
    return {'columns': [col('date', 'Date', 'date'), col('employee', 'Employee'), col('ticket', 'Ticket'), col('subject', 'Subject'),
                        col('customer', 'Customer'), col('hours', 'Hours', 'hours'), col('type', 'Type'), col('charges', 'Charges', 'money'),
                        col('days_waiting', 'Waiting (days)', 'number')],
            'rows': rows, 'totals': {'date': f'{len(rows)} log(s)', 'hours': round(sum(r['hours'] or 0 for r in rows), 2),
                                     'charges': round(sum(r['charges'] or 0 for r in rows), 2)}}


# ------------------------------------------------------------------ v1.15.0 work history
def r_work_history(request, p):
    from . import history as H
    from ProjectManagement.models import Project
    data = H.collect(request, p)
    cols = [col(k, label, 'hours' if k == 'hours' else ('date' if k == 'date' else 'text')) for k, label in H.ENTRY_COLS]
    cols[2]['type'] = 'badge'
    cq = A.in_branches(AMCContract.objects.all(), request).exclude(status__in=('draft', 'cancelled')).select_related('customer')
    if p.customer:
        cq = cq.filter(customer_id=p.customer)
    pq = Project.objects.filter(pk__in=A.visible_project_ids(request)).order_by('title')
    if p.customer:
        pq = pq.filter(pk__in=ProjectProfile.objects.filter(customer_id=p.customer).values('project_id'))
    from .models import Contact
    rec = []
    if p.customer:
        rec = [c.email for c in Contact.objects.filter(customer_id=p.customer, is_active=True).exclude(email='').order_by('-is_primary', 'name')[:5]]
    summary = data['summary']
    return {'columns': cols, 'rows': data['entries'],
            'totals': {'date': 'Total', 'hours': data['total_hours'],
                       'description': ' · '.join(f"{H.KIND_LABEL[k]} {v:g} h" for k, v in summary.items())},
            'summary': {'hours': summary, 'amc': data['amc'], 'tickets': len(data['tickets']), 'tasks': len(data['tasks'])},
            'options': {'contracts': [{'id': c.id, 'name': f'{c.number} – {c.customer.name}'} for c in cq.order_by('-start_date')[:200]],
                        'projects': [{'id': x.id, 'name': x.title} for x in pq[:300]], 'recipients': rec},
            'can_send': any(A.can(request, k) for k in ('ticket_manage', 'project_edit', 'customer_edit', 'amc_edit')) or A.is_admin(request)}


# ------------------------------------------------------------------ catalogue
SUPPORT_R = ('ticket_all', 'ticket_work')   # engineers see the support reports for the tickets they can see
REPORTS = [
    # key, group, title, description, filters, right, function
    ('open-tickets', 'Support', 'Open tickets by engineer, team, customer or priority', 'Where the open work is and how much of it is late.',
     ['group:employee,team,customer,priority', 'customer', 'team'], SUPPORT_R, r_open_tickets),
    ('received-resolved', 'Support', 'Tickets received vs resolved', 'Weekly intake, output and the backlog trend.',
     ['period', 'customer', 'team', 'employee'], SUPPORT_R, r_received_resolved),
    ('response-times', 'Support', 'Response and resolution times, SLA compliance', 'Average first response and resolution in working hours and clock time.',
     ['period', 'group:employee,team', 'customer', 'team'], SUPPORT_R, r_response_times),
    ('sla-breaches', 'Support', 'SLA breached tickets', 'Every ticket that missed its response or resolution target.',
     ['period', 'customer', 'team', 'employee'], SUPPORT_R, r_sla_breaches),
    ('reopened-csat', 'Support', 'Reopened tickets and customer satisfaction', 'Reopen rate and ratings per engineer.',
     ['period', 'customer', 'team'], SUPPORT_R, r_reopened_csat),
    ('amc-profit', 'AMC', 'AMC contracts – hours, billing and profitability', 'Every contract with hours used and left, billed, paid, cost and margin.',
     ['customer', 'status:active,expiring,expired,renewed'], 'amc_view', r_amc_profit),
    ('amc-expiring', 'AMC', 'Contracts expiring in 30 / 60 / 90 days', 'What to renew next, with the renewal follow-up.',
     ['window', 'customer'], 'amc_view', r_amc_expiring),
    ('amc-renewals', 'AMC', 'Renewals won, lost and pending', 'Contracts ending in the period and the renewal result.',
     ['period', 'customer'], 'amc_view', r_amc_renewals),
    ('project-status', 'Projects', 'Project status, budget and margin', 'Health, milestones, tasks, hours and money of every project.',
     ['customer', 'status:new,in_progress,on_hold,completed'], None, r_project_status),
    ('milestones-overdue', 'Projects', 'Overdue milestones', 'Milestones past their planned date and not accepted.',
     ['customer'], None, r_milestones_overdue),
    ('tasks-by-employee', 'Projects', 'Pending tasks and overdue work by employee', 'Open and late tasks plus open and late tickets per person.',
     ['customer', 'employee'], None, r_tasks_by_employee),
    ('work-history', 'History', 'Work history – AMC and projects', 'Tasks done and time spent for a customer, AMC or project; send it as PDF and Excel.',
     ['period', 'customer', 'contract', 'project', 'employee'], None, r_work_history),
    ('utilisation', 'People', 'Employee utilisation and workload', 'Support vs project hours, billable utilisation and open work.',
     ['period', 'employee'], 'dashboard', r_utilisation),
    ('worklogs-pending', 'People', 'Work logs waiting for approval', 'Time logged on tickets that is not approved yet.',
     ['employee'], None, r_worklogs_pending),
]
BY_KEY = {r[0]: r for r in REPORTS}


def allowed(request, right):
    if right is None or A.is_admin(request):
        return True
    return any(A.can(request, r) for r in ((right,) if isinstance(right, str) else right))


def catalogue(request):
    return [{'key': k, 'group': g, 'title': t, 'description': d, 'filters': fl} for k, g, t, d, fl, right, _ in REPORTS if allowed(request, right)]


def run(request, key):
    if key not in BY_KEY:
        raise S.ProjectsError('Unknown report.', status=404)
    k, g, title, desc, fl, right, fn = BY_KEY[key]
    if not allowed(request, right):
        raise A.Denied('You do not have the right to see this report.')
    p = P(request)
    out = fn(request, p)
    out.update({'key': k, 'group': g, 'title': title, 'description': desc, 'filters': fl, 'from': p.frm.isoformat(), 'to': p.to.isoformat(),
                'currency': S.settings().currency})
    return out


# ------------------------------------------------------------------ weekly e-mail to management
def weekly_summary(request=None):
    """Plain-text summary of the last 7 days for managers (sent on Mondays by the daily job)."""
    from .dashboard import amc_block
    to = S.local_today()
    frm = to - timedelta(days=6)
    now = S.now()
    cur = S.settings().currency
    T = A.in_branches(Ticket.objects.all(), request).all()
    open_qs = T.filter(stage__in=OPEN_STAGES)
    late = open_qs.filter(Q(resolution_breached=True) | Q(response_breached=True) | Q(resolution_due__lt=now, paused_at__isnull=True))
    resolved = T.filter(resolved_at__date__gte=frm, resolved_at__date__lte=to)
    met = resolved.filter(resolution_breached=False).count()
    rated = T.filter(satisfaction__isnull=False, resolved_at__date__gte=frm, resolved_at__date__lte=to)
    avg = rated.aggregate(a=__import__('django.db.models', fromlist=['Avg']).Avg('satisfaction'))['a']
    a = amc_block(None, frm, to)
    pend = A.in_branches(WorkLog.objects.all(), request).filter(status='submitted').count()
    lines = [f'Projects – weekly summary {frm:%d %b} – {to:%d %b %Y}', '',
             'SUPPORT',
             f"  Open tickets: {open_qs.count()} ({open_qs.filter(priority='critical').count()} critical, "
             f"{open_qs.filter(assigned_to__isnull=True).count()} not assigned)",
             f"  Past SLA (open): {late.count()}",
             f"  Received / resolved this week: {T.filter(created_at__date__gte=frm, created_at__date__lte=to).count()} / {resolved.count()}",
             f"  Resolved within SLA: {round(met * 100 / resolved.count(), 1) if resolved.count() else '-'}%",
             f"  Customer rating: {round(avg, 2) if avg else '-'} of 5 ({rated.count()} ratings)",
             f"  Work logs waiting for approval: {pend}", '',
             'AMC',
             f"  Active contracts: {a['active']} · ending in 30 / 60 / 90 days: {a['expiring_30']} / {a['expiring_60']} / {a['expiring_90']}",
             f"  Renewals pending: {a['renewals_pending']} · expired, not renewed: {a['expired']}",
             f"  Hours used / included: {a['hours_used']:.0f} / {a['hours_included']:.0f}", '']
    late_list = list(late.select_related('customer', 'assigned_to').order_by('resolution_due')[:10])
    if late_list:
        lines.append('PAST SLA')
        lines += [f"  {t.number} {t.subject[:60]} – {t.customer.name if t.customer_id else 'Internal'} – "
                  f"{S.emp_name(t.assigned_to) if t.assigned_to_id else 'not assigned'}" for t in late_list]
        lines.append('')
    if a['expiring_list']:
        lines.append('ENDING SOON')
        lines += [f"  {c['number']} {c.get('customer_name', '')} – ends {c.get('end_date')} – {cur} {float(c.get('value') or 0):,.0f}"
                  for c in a['expiring_list'][:10]]
        lines.append('')
    lines.append('Open Projects → Reports in ZEO for the full reports with Excel and PDF export.')
    return '\n'.join(lines)


def send_weekly(force=False):
    s = S.settings()
    if not s.weekly_report_enabled and not force:
        return 0
    to = [x.strip() for x in (s.weekly_report_emails or '').replace(';', ',').split(',') if '@' in x]
    if not to:
        users = S.users_with('assign_ticket') + S.users_with('approve_changerequest', admins_if_none=False)
        to = [u.email for u in users if u.email]
    to = sorted(set(to))
    if not to:
        return 0
    S.send_mail(to, 'Projects – weekly summary', weekly_summary())
    return len(to)
