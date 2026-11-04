"""
v1.62.0 talent-ops – read-only summaries for the loan and advance salary screens (requests with their stage, approvals as one row
per request with Approve / Reject in the list, repayments with the employee, the loan and the payroll month, approval levels and
escalation in words).

Nothing is calculated or changed here: loan schedules, instalments, balances, approvals and payroll deductions stay where they
are (PayrollManagement models, PayrollPlus.loans). The screens keep saving through the existing /payroll/api/… endpoints
(loan-approval/<id>/approve|reject, approval-salaryrequest/<id>/approve|reject, the request forms).

GET  api/loan-desk/requests/?kind=loan|advance&branch_id=      requests (employee, type, amount, instalment, paid, left, stage) + tiles
GET  api/loan-desk/approvals/?kind=loan|advance&box=waiting|open|decided   one row per request, the step this user can act on
GET  api/loan-desk/repayments/?branch_id=                      loan instalments (employee, loan, payroll month and pay date)
GET  api/loan-desk/levels/?kind=loan|advance                   approval workflows with their levels and escalation
"""
import calendar
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework.response import Response

from Translations.i18n import tr

from . import services as S
from .air_tickets import AtBase, emp_name, user_names

M = apps.get_model
ZERO = Decimal('0.00')
LOAN_READ = {'view_loanapplication', 'change_loanapplication', 'add_loanapplication', 'view_loanapproval', 'change_loanapproval'}
ADV_READ = {'view_advancesalaryrequest', 'change_advancesalaryrequest', 'add_advancesalaryrequest', 'view_advancesalaryapproval',
            'change_advancesalaryapproval'}
REP_READ = LOAN_READ | {'view_loanrepayment', 'change_loanrepayment'}
LOAN_LEVEL_READ = {'view_loanapprovallevels', 'change_loanapprovallevels', 'add_loanapprovallevels', 'view_loanapprovalworkflow',
                   'view_loan_escalation', 'change_loan_escalation'}
ADV_LEVEL_READ = {'view_advancecommonworkflow', 'change_advancecommonworkflow', 'add_advancecommonworkflow', 'view_advanceapprovalworkflow',
                  'view_advsalary_escalation', 'change_advsalary_escalation'}
LOAN_RUNNING = ('Approved', 'Disbursed', 'In Progress', 'Paused')


def d2(x):
    return str(S.q2(x if x is not None else ZERO))


def branch_of(r):
    if r.branch_id:
        return r.branch.branch_name
    e = r.employee
    return e.emp_branch_id.branch_name if e and e.emp_branch_id_id else ''


def month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


# ---------------------------------------------------------------- loans
def loan_paid(ids):
    R = M('PayrollManagement', 'LoanRepayment')
    return {x['loan_id']: Decimal(str(x['s'] or 0)) for x in R.objects.filter(loan_id__in=ids).values('loan_id').annotate(s=Sum('amount_paid'))}


def loan_workflows():
    W = M('PayrollManagement', 'LoanApprovalWorkflow')
    out = []
    for w in W.objects.prefetch_related('branch', 'loan_levels'):
        out.append((w, {b.pk for b in w.branch.all()}))
    return out


def loan_workflow(r, wfs):
    """The workflow the request follows (as LoanApplication.move_to_next_level picks it: type + branch, else the type's first)."""
    br = r.employee.emp_branch_id_id if r.employee_id else None
    for w, brs in wfs:
        if w.loan_type_id == r.loan_type_id and br in brs:
            return w
    return next((w for w, _ in wfs if w.loan_type_id == r.loan_type_id), None)


def adv_workflow(r, wfs):
    br = r.employee.emp_branch_id_id if r.employee_id else None
    return next((w for w, brs in wfs if br in brs), None)


def progress(steps, wf, levels_rel, names):
    kind = wf.approval_type if wf else ''
    n_levels = len(getattr(wf, levels_rel).all()) if wf else 0
    total = 0 if kind == 'no_approval' else (1 if kind == 'reporting_manager' else n_levels)
    done = len({s.level for s in steps if s.status == 'Approved'})
    waiting = next((s for s in sorted(steps, key=lambda s: s.level) if s.status == 'Pending'), None)
    who = ''
    if waiting:
        who = names.get(waiting.deligate_to_id if waiting.is_deligate and waiting.deligate_to_id else waiting.approver_id, '')
    rej = next((s for s in steps if s.status == 'Rejected'), None)
    return {'type': kind, 'done': done, 'total': max(total, done, len({s.level for s in steps})), 'waiting_for': who,
            'waiting_level': waiting.level if waiting else None, 'waiting_since': waiting.created_at if waiting else None,
            'rejected_by': names.get(rej.approver_id, '') if rej else '',
            'reason': (rej.rejection_reason or rej.note or '') if rej else ''}


def loan_stage(r, paid, bal):
    """in_approval | rejected | approved (nothing deducted yet) | repaying | paused | closed"""
    if r.status == 'Pending':
        return 'in_approval'
    if r.status == 'Rejected':
        return 'rejected'
    if r.status == 'Closed' or (r.status in LOAN_RUNNING and paid > 0 and bal <= 0):
        return 'closed'
    if r.status == 'Paused':
        return 'paused'
    return 'repaying' if paid > 0 else 'approved'


def loan_row(r, paid_map, wfs, names):
    amount = Decimal(str(r.amount_requested or 0))
    paid = paid_map.get(r.pk, ZERO)
    bal = max(ZERO, amount - paid) if r.status != 'Rejected' else ZERO
    steps = list(r.approvals.all())
    e = r.employee
    emi = Decimal(str(r.emi_amount or 0))
    months_left = 0
    if emi > 0 and bal > 0:
        months_left = int((bal / emi).to_integral_value(rounding='ROUND_CEILING'))
    return {
        'id': r.pk, 'kind': 'loan', 'number': r.document_number or str(r.pk), 'employee_id': e.pk, 'employee': emp_name(e),
        'code': e.emp_code, 'branch': branch_of(r), 'type': str(r.loan_type) if r.loan_type_id else '',
        'amount': d2(amount), 'instalment': d2(emi), 'months': r.repayment_period, 'months_left': months_left,
        'paid': d2(paid), 'remaining': d2(bal), 'status': r.status, 'stage': loan_stage(r, paid, bal),
        'applied': timezone.localtime(r.applied_on).date() if r.applied_on else None, 'disbursed': r.disbursement_date,
        'reason': r.rejection_reason or '', 'progress': progress(steps, loan_workflow(r, wfs), 'loan_levels', names),
    }


def loans_qs():
    LA = M('PayrollManagement', 'LoanApplication')
    return LA.objects.select_related('employee', 'employee__emp_branch_id', 'branch', 'loan_type').prefetch_related('approvals')


# ---------------------------------------------------------------- advance salary
def adv_workflows():
    W = M('PayrollManagement', 'AdvanceApprovalWorkflow')
    return [(w, {b.pk for b in w.branch.all()}) for w in W.objects.prefetch_related('branch', 'advance_levels')]


def adv_stage(r):
    return {'Pending': 'in_approval', 'Approved': 'approved', 'Deducted': 'deducted', 'Rejected': 'rejected', 'Cancelled': 'cancelled',
            'Paused': 'paused'}.get(r.status, (r.status or '').lower())


def adv_row(r, wfs, names):
    e = r.employee
    steps = list(r.approvals.all())
    amount = Decimal(str(r.requested_amount or 0))
    return {
        'id': r.pk, 'kind': 'advance', 'number': r.document_number or str(r.pk), 'employee_id': e.pk, 'employee': emp_name(e),
        'code': e.emp_code, 'branch': branch_of(r), 'type': tr('Advance salary'), 'amount': d2(amount),
        'instalment': d2(amount), 'months': 1, 'months_left': 1 if r.status == 'Approved' else 0,
        'paid': d2(amount if r.status == 'Deducted' else ZERO), 'remaining': d2(amount if r.status in ('Approved', 'Paused') else ZERO),
        'status': r.status, 'stage': adv_stage(r), 'note': r.reason or '', 'reason': r.remarks if r.status == 'Rejected' else '',
        'applied': timezone.localtime(r.created_at).date() if r.created_at else None,
        'progress': progress(steps, adv_workflow(r, wfs), 'advance_levels', names),
    }


def advs_qs():
    A = M('PayrollManagement', 'AdvanceSalaryRequest')
    return A.objects.select_related('employee', 'employee__emp_branch_id', 'branch').prefetch_related('approvals')


def step_names(reqs):
    U = M('UserManagement', 'CustomUser')
    ids = set()
    for r in reqs:
        for s in r.approvals.all():
            ids.update(x for x in (s.approver_id, s.deligate_to_id) if x)
    return user_names(list(U.objects.filter(pk__in=ids)))


class DeskBase(AtBase):
    def kind(self, request):
        k = request.query_params.get('kind') or 'loan'
        if k not in ('loan', 'advance', 'payroll'):
            raise S.Refused(tr('Unknown list.'), status=404)
        return k


class RequestsView(DeskBase):
    def get(self, request):
        kind = self.kind(request)
        today = timezone.localdate()
        if kind == 'loan':
            self.need(LOAN_READ)
            reqs = list(self.in_branches(loans_qs(), 'employee__emp_branch_id', request).order_by('-applied_on', '-id')[:500])
            names = step_names(reqs)
            paid = loan_paid([r.pk for r in reqs])
            wfs = loan_workflows()
            rows = [loan_row(r, paid, wfs, names) for r in reqs]
            can_edit, can_delete = self.can({'change_loanapplication'}), self.can({'delete_loanapplication'})
        else:
            self.need(ADV_READ)
            reqs = list(self.in_branches(advs_qs(), 'employee__emp_branch_id', request).order_by('-created_at', '-id')[:500])
            names = step_names(reqs)
            wfs = adv_workflows()
            rows = [adv_row(r, wfs, names) for r in reqs]
            can_edit, can_delete = self.can({'change_advancesalaryrequest'}), self.can({'delete_advancesalaryrequest'})
        running = [r for r in rows if r['stage'] in ('approved', 'repaying', 'paused')]
        summary = {
            'in_approval': len([r for r in rows if r['stage'] == 'in_approval']),
            'in_approval_amount': d2(sum((Decimal(r['amount']) for r in rows if r['stage'] == 'in_approval'), ZERO)),
            'running': len(running), 'outstanding': d2(sum((Decimal(r['remaining']) for r in running), ZERO)),
            'monthly': d2(sum((Decimal(r['instalment']) for r in running if r['stage'] != 'paused'), ZERO)),
            'closed_year': len([r for r in rows if r['stage'] in ('closed', 'deducted') and r['applied'] and r['applied'].year == today.year]),
            'waiting_me': waiting_count(kind, request.user),
        }
        return Response({'rows': rows, 'summary': summary, 'kind': kind, 'can_edit': can_edit, 'can_delete': can_delete})


def waiting_count(kind, u):
    AP = M('PayrollManagement', 'LoanApproval' if kind == 'loan' else 'AdvanceSalaryApproval')
    return AP.objects.filter(Q(approver=u) | Q(deligate_to=u, is_deligate=True), status='Pending').count()


class ApprovalsView(DeskBase):
    """One row per request. box=waiting (default): requests with a step waiting for this user (approver or delegate);
    box=open: every request still in approval (HR / a company admin – a superuser may decide any step, as the existing endpoint allows);
    box=decided: requests on which this user decided a step (a company admin: every decided request)."""
    def get(self, request):
        kind = self.kind(request)
        u = request.user
        box = request.query_params.get('box') or 'waiting'
        loan = kind == 'loan'
        AP = M('PayrollManagement', 'LoanApproval' if loan else 'AdvanceSalaryApproval')
        rel = 'loan_request' if loan else 'request'
        mine = Q(approver=u) | Q(deligate_to=u, is_deligate=True)
        steps = AP.objects.all()
        if box == 'open':
            if not (self.c.admin or self.can(LOAN_READ if loan else ADV_READ)):
                raise S.Refused(tr('You do not have permission to do this.'), status=403)
            steps = steps.filter(status='Pending')
        elif box == 'decided':
            steps = steps.exclude(status='Pending')
            if not self.c.admin:
                steps = steps.filter(mine)
        else:
            steps = steps.filter(mine, status='Pending')
        req_ids = list(dict.fromkeys(steps.order_by('-id').values_list(f'{rel}_id', flat=True)))
        qs = (loans_qs() if loan else advs_qs()).filter(pk__in=[x for x in req_ids if x])
        if box == 'open':
            qs = qs.filter(status='Pending')
        qs = self.in_branches(qs, 'employee__emp_branch_id', request)
        reqs = list(qs)
        names = step_names(reqs)
        if loan:
            paid, wfs = loan_paid([r.pk for r in reqs]), loan_workflows()
        else:
            wfs = adv_workflows()
        is_super = bool(getattr(u, 'is_superuser', False))
        rows = []
        for r in reqs:
            row = loan_row(r, paid, wfs, names) if loan else adv_row(r, wfs, names)
            st = sorted(r.approvals.all(), key=lambda s: (s.level, s.id))
            pending = next((s for s in st if s.status == 'Pending'), None)
            my_pending = next((s for s in st if s.status == 'Pending' and (s.approver_id == u.pk or (s.is_deligate and s.deligate_to_id == u.pk))), None)
            act = my_pending or (pending if is_super else None)
            decided = [s for s in st if s.status != 'Pending' and (self.c.admin or s.approver_id == u.pk)]
            last = decided[-1] if decided else None
            row.update({
                'approval_id': (act or pending or last or (st[-1] if st else None)).id if st else None,
                'can_act': bool(act), 'for_me': bool(my_pending),
                'level': (act or pending).level if (act or pending) else (last.level if last else None),
                'waiting_since': (act or pending).created_at if (act or pending) else None,
                'steps': [{'level': s.level, 'role': s.role or '', 'who': names.get(s.approver_id, ''), 'status': s.status,
                           'note': s.note or '', 'reason': s.rejection_reason or '', 'on': s.updated_at} for s in st],
                'decided': ({'status': last.status, 'level': last.level, 'by': names.get(last.approver_id, ''), 'on': last.updated_at}
                            if last else None),
            })
            rows.append(row)
        order = {rid: i for i, rid in enumerate(req_ids)}
        rows.sort(key=lambda x: order.get(x['id'], 0))
        open_n = (M('PayrollManagement', 'LoanApplication' if loan else 'AdvanceSalaryRequest').objects.filter(status='Pending').count()
                  if (self.c.admin or self.can(LOAN_READ if loan else ADV_READ)) else None)
        return Response({'rows': rows, 'box': box, 'kind': kind, 'waiting': waiting_count(kind, u), 'open': open_n})


class RepaymentsView(DeskBase):
    """Loan instalments taken by payroll (and settlements outside payroll): employee, loan, payroll month and its pay date."""
    def get(self, request):
        self.need(REP_READ)
        R = M('PayrollManagement', 'LoanRepayment')
        qs = R.objects.select_related('loan', 'loan__employee', 'loan__employee__emp_branch_id', 'loan__loan_type', 'payslip__payroll_run')
        qs = self.in_branches(qs, 'loan__employee__emp_branch_id', request)
        if request.query_params.get('loan'):
            qs = qs.filter(loan_id=request.query_params['loan'])
        rows = []
        for x in qs:
            pr = x.payslip.payroll_run if x.payslip_id and x.payslip.payroll_run_id else None
            if pr:
                month = date(int(pr.year), int(pr.month), 1)
                paid_on = pr.payment_date or month_end(int(pr.year), int(pr.month))
            else:
                month = (x.repayment_date or timezone.localdate()).replace(day=1)
                paid_on = x.repayment_date
            l, e = x.loan, x.loan.employee
            rows.append({'id': x.pk, 'loan_id': l.pk, 'number': l.document_number or str(l.pk), 'employee_id': e.pk, 'employee': emp_name(e),
                         'code': e.emp_code, 'branch': branch_of(l), 'type': str(l.loan_type) if l.loan_type_id else '',
                         'month': month, 'paid_on': paid_on, 'amount': d2(x.amount_paid), 'remaining': d2(x.remaining_balance),
                         'payslip_id': x.payslip_id, 'run': pr.name if pr else '', 'how': 'payroll' if pr else 'outside'})
        rows.sort(key=lambda r: (r['month'], r['number']), reverse=True)
        months = sorted({r['month'] for r in rows}, reverse=True)
        last = months[0] if months else None
        summary = {'count': len(rows), 'total': d2(sum((Decimal(r['amount']) for r in rows), ZERO)),
                   'last_month': last, 'last_total': d2(sum((Decimal(r['amount']) for r in rows if r['month'] == last), ZERO)),
                   'loans': len({r['loan_id'] for r in rows})}
        return Response({'rows': rows, 'summary': summary})


class LevelsView(DeskBase):
    def get(self, request):
        kind = self.kind(request)
        U = M('UserManagement', 'CustomUser')
        if kind == 'loan':
            self.need(LOAN_LEVEL_READ)
            wfs = [w for w, _ in loan_workflows()]
            rel = 'loan_levels'
        elif kind == 'payroll':   # the payslip approval workflow (Payroll › Approval levels)
            self.need({'view_payslipcommonworkflow', 'change_payslipcommonworkflow', 'view_payslipapprovalworkflow', 'view_payrollrun'})
            W = M('PayrollManagement', 'PayslipApprovalWorkflow')
            wfs = list(W.objects.prefetch_related('branch', 'payslip_levels'))
            rel = 'payslip_levels'
        else:
            self.need(ADV_LEVEL_READ)
            wfs = [w for w, _ in adv_workflows()]
            rel = 'advance_levels'
        users = set()
        for w in wfs:
            for l in getattr(w, rel).all():
                users.update(x for x in (l.approver_id, getattr(l, 'escalate_to_id', None)) if x)
        names = user_names(list(U.objects.filter(pk__in=users)))
        rows = []
        for w in sorted(wfs, key=lambda w: w.pk):
            rows.append({'id': w.id, 'type': w.approval_type, 'branches': sorted(b.branch_name for b in w.branch.all()),
                         'loan_type': str(w.loan_type) if kind == 'loan' and w.loan_type_id else '',
                         'levels': [{'id': l.id, 'level': l.level, 'role': l.role or '', 'approver': names.get(l.approver_id, ''),
                                     'escalate_to': names.get(getattr(l, 'escalate_to_id', None), ''), 'days': getattr(l, 'escalate_after_days', 0),
                                     'hours': getattr(l, 'escalate_after_hours', 0), 'minutes': getattr(l, 'escalate_after_minutes', 0)}
                                    for l in sorted(getattr(w, rel).all(), key=lambda l: l.level)]})
        return Response({'rows': rows, 'kind': kind})
