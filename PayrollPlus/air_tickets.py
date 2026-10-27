"""
v1.62.0 air ticket – read-only summaries for the air ticket screens (policies with their rules, allocations with the liability
tiles, requests and approvals with their progress, approval levels, the employee's own entitlement).

Nothing is calculated or changed here: entitlement, allocation amounts, approvals and the payroll encashment stay where they are
(PayrollManagement models, signals and tasks). The screens keep saving through the existing /payroll/api/airticket-* endpoints.

GET  api/air-tickets/policies/                  policies with who they apply to and their rules
GET  api/air-tickets/allocations/?branch_id=    allocations (employee, branch, used, remaining, next eligible, expiry) + summary
GET  api/air-tickets/requests/?branch_id=       requests (employee, ticket or cash, route, dates, amount, approval progress)
GET  api/air-tickets/approvals/?box=waiting|decided   approval steps waiting for this user (or decided)
GET  api/air-tickets/levels/                    approval workflows with their levels and escalation
GET  api/air-tickets/mine/                      the signed-in employee's entitlement, next eligible date and requests
"""
import json
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response

from Translations.i18n import tr

from . import services as S
from .views import Base

M = apps.get_model
ZERO = Decimal('0.00')
POLICY_READ = {'view_airticketpolicy', 'change_airticketpolicy', 'add_airticketpolicy', 'view_airticketrule', 'change_airticketrule'}
ALLOC_READ = {'view_airticketallocation', 'change_airticketallocation', 'add_airticketallocation'}
REQ_READ = {'view_airticketrequest', 'change_airticketrequest', 'add_airticketrequest'}
LEVEL_READ = {'view_airticketapprovalworkflow', 'change_airticketapprovalworkflow', 'view_airticketworkflow', 'view_airticket_escalation',
              'change_airticket_escalation'}
USED_REQ = ('APPROVED', 'PROCESSED')


def d2(x):
    return str(S.q2(x if x is not None else ZERO))


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:   # 29 February
        return d.replace(year=d.year + n, day=28)


def emp_name(e):
    if e is None:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x).strip() or e.emp_code


def branch_ids(request):
    """The branch switcher of the screen (?branch_id=[1,3] or 1,3); empty = all."""
    raw = request.query_params.get('branch_id') or ''
    try:
        v = json.loads(raw) if raw.startswith('[') else [x for x in raw.split(',') if x.strip()]
        return [int(x) for x in v]
    except (ValueError, TypeError):
        return []


def user_names(users):
    """CustomUser → the employee's name (else the user name)."""
    E = M('EmpManagement', 'emp_master')
    ids = [u.pk for u in users if u is not None]
    by_user = {e.users_id: emp_name(e) for e in E.objects.filter(users_id__in=ids)}
    return {u.pk: by_user.get(u.pk) or u.username for u in users if u is not None}


def rule_row(r):
    return {'id': r.id, 'type': r.rule_type, 'years': r.required_service_years, 'next_payroll': bool(r.apply_in_next_payroll),
            'remarks': r.remarks or ''}


def policy_row(p, counts=None):
    rules = sorted(p.rules.all(), key=lambda r: (r.required_service_years, r.id))
    return {
        'id': p.id, 'name': p.name, 'country': p.country.country_name if p.country_id else '', 'amount': d2(p.amount),
        'frequency_years': p.frequency_years, 'allowance_type': p.allowance_type, 'travel_class': p.travel_class,
        'allowed_in_probation': bool(p.allowed_in_probation), 'is_active': bool(p.is_active),
        'departments': [x.dept_name for x in p.eligible_departments.all()],
        'designations': [x.desgntn_job_title for x in p.eligible_designations.all()],
        'categories': [x.ctgry_title for x in p.eligible_categories.all()],
        'branches': [x.branch_name for x in p.branch.all()],
        'rules': [rule_row(r) for r in rules],
        'employees': (counts or {}).get(p.id, 0),
    }


def last_use(employee_id, policy_id):
    """Date of the employee's last ticket / encashment approved under the policy (departure date, else request date)."""
    R = M('PayrollManagement', 'AirTicketRequest')
    best = None
    for r in R.objects.filter(employee_id=employee_id, allocation__policy_id=policy_id, status__in=USED_REQ):
        d = r.departure_date or r.request_date
        if d and (best is None or d > best):
            best = d
    return best


def next_eligible(a, today):
    """Display only: the date the employee can next use the policy – the last use + the policy frequency; without a use yet,
    the joining date + the service the first rule asks for. None = now."""
    p = a.policy
    used = last_use(a.employee_id, a.policy_id)
    if used:
        nxt = add_years(used, max(p.frequency_years or 1, 1))
    else:
        years = [r.required_service_years for r in p.rules.all()]
        joined = getattr(a.employee, 'emp_joined_date', None)
        nxt = add_years(joined, min(years) if years else 0) if joined else None
    return nxt if nxt and nxt > today else None


def alloc_state(a, today, ticket_taken):
    """Approved | Used | Expired | Ticket taken | Pending | Rejected | Inactive (display word of the allocation)."""
    if a.status == 'REJECTED':
        return 'rejected'
    if not a.is_active:
        return 'inactive'
    if a.remaining_amount is not None and a.remaining_amount <= 0:
        return 'used'
    if a.expiry_date and a.expiry_date < today:
        return 'expired'
    if a.status == 'PENDING':
        return 'pending'
    if ticket_taken:
        return 'ticket_taken'
    return 'approved'


def alloc_row(a, today, ticket_taken=False):
    e = a.employee
    amount = a.amount or ZERO
    remaining = a.remaining_amount if a.remaining_amount is not None else amount
    nxt = next_eligible(a, today)
    return {
        'id': a.id, 'employee_id': e.pk, 'employee': emp_name(e), 'code': e.emp_code,
        'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '',
        'policy_id': a.policy_id, 'policy': a.policy.name, 'country': a.policy.country.country_name if a.policy.country_id else '',
        'allowance_type': a.policy.allowance_type, 'frequency_years': a.policy.frequency_years,
        'amount': d2(amount), 'used': d2(max(amount - remaining, ZERO)), 'remaining': d2(remaining),
        'allocated': a.allocated_date, 'expiry': a.expiry_date, 'next_eligible': nxt, 'eligible_now': nxt is None,
        'status': a.status, 'state': alloc_state(a, today, ticket_taken), 'is_active': bool(a.is_active),
    }


def is_open(a, today):
    return (a.is_active and a.status != 'REJECTED' and (a.remaining_amount or ZERO) > 0
            and not (a.expiry_date and a.expiry_date < today))


def workflow_for(branch_id):
    W = M('PayrollManagement', 'AirticketApprovalWorkflow')
    return W.objects.filter(branch__id=branch_id).prefetch_related('airticket_levels').first() if branch_id else None


def progress(r, names, wf_cache):
    """{done, total, waiting_for, type} of a request's approval."""
    if r.branch_id not in wf_cache:
        wf_cache[r.branch_id] = workflow_for(r.branch_id)
    wf = wf_cache[r.branch_id]
    kind = wf.approval_type if wf else ''
    total = 0 if kind == 'no_approval' else (1 if kind == 'reporting_manager' else len(wf.airticket_levels.all()) if wf else 0)
    steps = list(r.approvals.all())
    done = len({s.level for s in steps if s.status == 'Approved'})
    waiting = next((s for s in sorted(steps, key=lambda s: s.level) if s.status == 'Pending'), None)
    who = ''
    if waiting:
        who = names.get(waiting.deligate_to_id if waiting.is_deligate and waiting.deligate_to_id else waiting.approver_id, '')
    return {'type': kind, 'done': done, 'total': max(total, done, len({s.level for s in steps})), 'waiting_for': who,
            'rejected_by': next((names.get(s.approver_id, '') for s in steps if s.status == 'Rejected'), '')}


def request_row(r, names, wf_cache):
    e = r.employee
    a = r.allocation
    return {
        'id': r.id, 'number': r.document_number or '', 'employee_id': e.pk, 'employee': emp_name(e), 'code': e.emp_code,
        'branch': r.branch.branch_name if r.branch_id else (e.emp_branch_id.branch_name if e.emp_branch_id_id else ''),
        'policy': a.policy.name if a else '', 'allocation_id': r.allocation_id,
        'type': r.request_type, 'origin': r.origin or '', 'destination': r.destination or '',
        'request_date': r.request_date, 'departure': r.departure_date, 'return': r.return_date,
        'amount': d2(a.amount if a else ZERO), 'status': r.status, 'notes': r.notes or '',
        'progress': progress(r, names, wf_cache),
    }


def requests_qs():
    R = M('PayrollManagement', 'AirTicketRequest')
    return R.objects.select_related('employee', 'employee__emp_branch_id', 'branch', 'allocation', 'allocation__policy').prefetch_related('approvals')


def names_for_requests(rows):
    U = M('UserManagement', 'CustomUser')
    ids = set()
    for r in rows:
        for s in r.approvals.all():
            ids.update(x for x in (s.approver_id, s.deligate_to_id) if x)
    return user_names(list(U.objects.filter(pk__in=ids)))


class AtBase(Base):
    def need(self, codes):
        if not self.can(codes):
            raise S.Refused(tr('You do not have permission to do this.'), status=403)

    def in_branches(self, qs, field, request):
        if self.c.branches is not None:
            qs = qs.filter(**{f'{field}__in': self.c.branches})
        b = branch_ids(request)
        if b:
            qs = qs.filter(**{f'{field}__in': b})
        return qs


class PoliciesView(AtBase):
    def get(self, request):
        self.need(POLICY_READ | ALLOC_READ)
        P = M('PayrollManagement', 'AirTicketPolicy')
        A = M('PayrollManagement', 'AirTicketAllocation')
        today = timezone.localdate()
        counts = {}
        for a in A.objects.filter(is_active=True).exclude(status='REJECTED'):
            if is_open(a, today):
                counts.setdefault(a.policy_id, set()).add(a.employee_id)
        counts = {k: len(v) for k, v in counts.items()}
        qs = P.objects.select_related('country').prefetch_related('rules', 'eligible_departments', 'eligible_designations',
                                                                  'eligible_categories', 'branch').order_by('-is_active', 'name')
        return Response({'rows': [policy_row(p, counts) for p in qs], 'can_edit': self.can({'change_airticketpolicy'}),
                         'can_delete': self.can({'delete_airticketpolicy'})})


class AllocationsView(AtBase):
    def get(self, request):
        self.need(ALLOC_READ)
        A = M('PayrollManagement', 'AirTicketAllocation')
        R = M('PayrollManagement', 'AirTicketRequest')
        today = timezone.localdate()
        qs = A.objects.select_related('employee', 'employee__emp_branch_id', 'policy', 'policy__country').prefetch_related('policy__rules')
        qs = self.in_branches(qs, 'employee__emp_branch_id', request).order_by('employee__emp_code', '-allocated_date')
        taken = set(R.objects.filter(status__in=USED_REQ, request_type='TICKET').values_list('allocation_id', flat=True))
        rows = [alloc_row(a, today, a.id in taken) for a in qs]
        open_rows = [r for r in rows if r['state'] in ('approved', 'ticket_taken', 'pending') and Decimal(r['remaining']) > 0]
        soon = today + timedelta(days=90)
        due = [r for r in open_rows if r['eligible_now'] or (r['next_eligible'] and r['next_eligible'] <= soon)]
        emp_ids = {r['employee_id'] for r in rows}
        used = R.objects.filter(status__in=USED_REQ, employee_id__in=emp_ids).filter(
            Q(departure_date__year=today.year) | Q(departure_date__isnull=True, request_date__year=today.year))
        summary = {
            'entitled': len({r['employee_id'] for r in open_rows}),
            'liability': d2(sum((Decimal(r['remaining']) for r in open_rows), ZERO)),
            'used_this_year': used.count(), 'year': today.year,
            'due_90': len(due), 'due_90_amount': d2(sum((Decimal(r['remaining']) for r in due), ZERO)),
        }
        return Response({'rows': rows, 'summary': summary, 'today': today,
                         'can_edit': self.can({'change_airticketallocation'}), 'can_delete': self.can({'delete_airticketallocation'})})


class RequestsView(AtBase):
    def get(self, request):
        self.need(REQ_READ)
        qs = self.in_branches(requests_qs(), 'employee__emp_branch_id', request).order_by('-request_date', '-id')[:500]
        rows = list(qs)
        names = names_for_requests(rows)
        wf = {}
        return Response({'rows': [request_row(r, names, wf) for r in rows],
                         'can_edit': self.can({'change_airticketrequest'}), 'can_delete': self.can({'delete_airticketrequest'})})


class ApprovalsView(AtBase):
    """box=waiting (default): the steps waiting for this user (as approver or delegate); box=decided: the steps this user decided
    (a company admin sees every decided step)."""
    def get(self, request):
        AP = M('PayrollManagement', 'AirticketApproval')
        u = request.user
        mine = Q(approver=u) | Q(deligate_to=u, is_deligate=True)
        box = request.query_params.get('box') or 'waiting'
        qs = AP.objects.select_related('request', 'request__employee', 'request__employee__emp_branch_id', 'request__branch',
                                       'request__allocation', 'request__allocation__policy').prefetch_related('request__approvals')
        if box == 'decided':
            qs = qs.exclude(status='Pending')
            if not self.c.admin:
                qs = qs.filter(mine)
        else:
            qs = qs.filter(mine, status='Pending')
        steps = list(qs.order_by('-created_at')[:300])
        reqs = [s.request for s in steps]
        names = names_for_requests(reqs)
        names.update(user_names([s.approver for s in steps if s.approver_id]))
        wf = {}
        rows = []
        for s in steps:
            r = request_row(s.request, names, wf)
            r.update({'approval_id': s.id, 'level': s.level, 'role': s.role or '', 'step_status': s.status,
                      'approver': names.get(s.approver_id, ''), 'note': s.note or '', 'reason': s.rejection_reason or '',
                      'delegated': bool(s.is_deligate), 'waiting_since': s.created_at})
            rows.append(r)
        waiting = AP.objects.filter(mine, status='Pending').count()
        return Response({'rows': rows, 'box': box, 'waiting': waiting})


class LevelsView(AtBase):
    def get(self, request):
        self.need(LEVEL_READ)
        W = M('PayrollManagement', 'AirticketApprovalWorkflow')
        U = M('UserManagement', 'CustomUser')
        wfs = list(W.objects.prefetch_related('branch', 'airticket_levels').order_by('id'))
        users = set()
        for w in wfs:
            for l in w.airticket_levels.all():
                users.update(x for x in (l.approver_id, l.escalate_to_id) if x)
        names = user_names(list(U.objects.filter(pk__in=users)))
        rows = []
        for w in wfs:
            rows.append({'id': w.id, 'type': w.approval_type, 'branches': [b.branch_name for b in w.branch.all()],
                         'levels': [{'id': l.id, 'level': l.level, 'role': l.role or '', 'approver': names.get(l.approver_id, ''),
                                     'escalate_to': names.get(l.escalate_to_id, ''), 'days': l.escalate_after_days,
                                     'hours': l.escalate_after_hours, 'minutes': l.escalate_after_minutes}
                                    for l in w.airticket_levels.all()]})
        return Response({'rows': rows})


class MineView(AtBase):
    """The signed-in employee's air ticket: entitlements (remaining, next eligible, expiry), policy rules and requests."""
    def get(self, request):
        emp = self.c.emp
        if emp is None:
            return Response({'employee': None, 'allocations': [], 'requests': [], 'policies': []})
        A = M('PayrollManagement', 'AirTicketAllocation')
        R = M('PayrollManagement', 'AirTicketRequest')
        today = timezone.localdate()
        taken = set(R.objects.filter(employee=emp, status__in=USED_REQ, request_type='TICKET').values_list('allocation_id', flat=True))
        allocs = list(A.objects.filter(employee=emp).select_related('employee', 'employee__emp_branch_id', 'policy', 'policy__country')
                      .prefetch_related('policy__rules', 'policy__eligible_departments', 'policy__eligible_designations',
                                        'policy__eligible_categories', 'policy__branch').order_by('-allocated_date'))
        reqs = list(requests_qs().filter(employee=emp).order_by('-request_date', '-id')[:100])
        names = names_for_requests(reqs)
        wf = {}
        pols = {a.policy_id: policy_row(a.policy) for a in allocs}
        return Response({'employee': {'id': emp.pk, 'name': emp_name(emp), 'code': emp.emp_code},
                         'allocations': [alloc_row(a, today, a.id in taken) for a in allocs],
                         'requests': [request_row(r, names, wf) for r in reqs], 'policies': list(pols.values()), 'today': today})
