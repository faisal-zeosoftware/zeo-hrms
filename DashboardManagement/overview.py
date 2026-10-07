"""
Company at a glance (main dashboard, v1.7.0): one card per module with the numbers that need attention.

Every count goes through the same access rules as the lists (AccessControl.scope_queryset), so a branch
HR user sees their branches only, and a card is left out when the user has no right to see that module.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Count, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)
M_ = '/main-sidebar/'


def _model(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


class _View:
    zeo_scope = True


def _qs(request, label):
    from AccessControl.access import scope_queryset
    m = _model(label)
    if m is None:
        return None
    return scope_queryset(request, _View(), m._default_manager.all())


def _can(c, *codes):
    return c.admin or any(x in c.codes for x in codes)


def _num(v):
    if isinstance(v, Decimal):
        return float(v)
    return v or 0


def _st(qs, *values):
    q = Q()
    for v in values:
        q |= Q(status__iexact=v)
    return qs.filter(q).count()


def overview(request):
    from AccessControl.access import ctx
    c = ctx(request)
    today = timezone.localdate()
    month_start = today.replace(day=1)
    cards = []

    def card(key, title, icon, route, kpis, items=None, note=''):
        cards.append({'key': key, 'title': title, 'icon': icon, 'route': M_ + route, 'kpis': kpis, 'items': items or [], 'note': note})

    def safe(fn):
        try:
            fn()
        except Exception:
            logger.exception('dashboard card failed: %s', getattr(fn, '__name__', ''))

    # ---------------- people
    def people():
        if not _can(c, 'view_emp_master'):
            return
        e = _qs(request, 'EmpManagement.emp_master')
        active = e.filter(is_active=True) if e is not None else None
        joiners = active.filter(emp_joined_date__gte=month_start).count() if active is not None else 0
        R = _qs(request, 'EmpManagement.EmployeeResignation')
        leavers = R.filter(last_working_date__gte=today, last_working_date__lte=today + timedelta(days=60)).exclude(status__iexact='rejected').count() if R is not None else 0
        by_dept = list(active.values('emp_dept_id__dept_name').annotate(n=Count('id')).order_by('-n')[:5]) if active is not None else []
        card('people', 'People', 'groups', 'sub-sidebar/employee-master',
             [{'label': 'Active employees', 'value': active.count() if active is not None else 0},
              {'label': 'Joined this month', 'value': joiners, 'tone': 'good' if joiners else ''},
              {'label': 'Leaving in 60 days', 'value': leavers, 'tone': 'warn' if leavers else ''}],
             [{'label': d['emp_dept_id__dept_name'] or 'No department', 'value': d['n']} for d in by_dept], 'Largest departments')
    safe(people)

    # ---------------- leave
    def leave():
        if not _can(c, 'view_employee_leave_request', 'view_leaveapproval'):
            return
        L = _qs(request, 'calendars.employee_leave_request')
        on = L.filter(status__iexact='approved', start_date__lte=today, end_date__gte=today).count()
        nxt = L.filter(status__iexact='approved', start_date__gt=today, start_date__lte=today + timedelta(days=7)).count()
        card('leave', 'Leave', 'event_busy', 'leave-options/leave-request',
             [{'label': 'On leave today', 'value': on}, {'label': 'Waiting for approval', 'value': _st(L, 'pending'), 'tone': 'warn'},
              {'label': 'Starting in 7 days', 'value': nxt}])
    safe(leave)

    # ---------------- attendance
    def attendance():
        if not _can(c, 'view_attendance'):
            return
        A = _qs(request, 'calendars.Attendance')
        e = _qs(request, 'EmpManagement.emp_master')
        present = A.filter(date=today).values('employee').distinct().count()
        total = e.filter(is_active=True).count() if e is not None else 0
        LI = _qs(request, 'calendars.LateInEarlyOutRequest') if _model('calendars.LateInEarlyOutRequest') else None
        card('attendance', 'Attendance today', 'fact_check', 'attendance-sidebar/employee-full-attendance',
             [{'label': 'Checked in', 'value': present, 'tone': 'good'}, {'label': 'Not checked in', 'value': max(total - present, 0), 'tone': 'warn' if total - present > 0 else ''},
              {'label': 'Late / early requests', 'value': _st(LI, 'pending') if LI is not None else 0}])
    safe(attendance)

    # ---------------- payroll
    def payroll():
        if not _can(c, 'view_payrollrun', 'view_payslip'):
            return
        P = _qs(request, 'PayrollManagement.Payslip')
        run = _qs(request, 'PayrollManagement.PayrollRun')
        last = run.order_by('-year', '-month', '-id').first() if run is not None else None
        net = P.filter(payroll_run=last).aggregate(s=Sum('net_salary'))['s'] if last else 0
        pend = _st(_qs(request, 'PayrollManagement.PayslipApproval'), 'pending') if _model('PayrollManagement.PayslipApproval') else 0
        card('payroll', 'Payroll', 'payments', 'salary-options/pay-roll',
             [{'label': f'Net pay · {last.name}' if last else 'No payroll run yet', 'value': _num(net), 'money': True},
              {'label': 'Payslips in last run', 'value': P.filter(payroll_run=last).count() if last else 0},
              {'label': 'Payslip approvals waiting', 'value': pend, 'tone': 'warn' if pend else ''}])
    safe(payroll)

    # ---------------- loans, advance, air tickets
    def benefits():
        if not _can(c, 'view_loanapplication', 'view_advancesalaryrequest', 'view_airticketrequest'):
            return
        L = _qs(request, 'PayrollManagement.LoanApplication')
        open_l = L.filter(status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')) if L is not None else None
        out = open_l.aggregate(s=Sum('remaining_balance'))['s'] if open_l is not None else 0
        ADV = _qs(request, 'PayrollManagement.AdvanceSalaryRequest')
        AT = _qs(request, 'PayrollManagement.AirTicketRequest')
        card('benefits', 'Loans & benefits', 'savings', 'loan-sidebar/loan-application',
             [{'label': 'Loan balance outstanding', 'value': _num(out), 'money': True},
              {'label': 'Loan requests waiting', 'value': _st(L, 'pending') if L is not None else 0, 'tone': 'warn'},
              {'label': 'Advance requests waiting', 'value': _st(ADV, 'pending') if ADV is not None else 0},
              {'label': 'Air ticket requests waiting', 'value': _st(AT, 'pending') if AT is not None else 0}])
    safe(benefits)

    # ---------------- assets
    def assets():
        if not _can(c, 'view_asset', 'view_assetallocation', 'view_assetrequest'):
            return
        A = _qs(request, 'OrganisationManager.Asset')
        AL = _qs(request, 'OrganisationManager.AssetAllocation')
        AR = _qs(request, 'OrganisationManager.AssetRequest')
        allocated = AL.filter(returned_date__isnull=True).values('asset').distinct().count() if AL is not None else 0
        total = A.count() if A is not None else 0
        by_status = list(A.values('status').annotate(n=Count('id')).order_by('-n')[:5]) if A is not None else []
        card('assets', 'Assets', 'inventory_2', 'asset-options/asset-master',
             [{'label': 'Assets registered', 'value': total}, {'label': 'With employees', 'value': allocated, 'tone': 'good'},
              {'label': 'Free to allocate', 'value': max(total - allocated, 0)},
              {'label': 'Requests waiting', 'value': _st(AR, 'pending') if AR is not None else 0, 'tone': 'warn'}],
             [{'label': (s['status'] or 'No status').replace('_', ' ').capitalize(), 'value': s['n']} for s in by_status], 'By status')
    safe(assets)

    # ---------------- documents
    def documents():
        if not _can(c, 'view_emp_documents', 'view_notification'):
            return
        D = _qs(request, 'EmpManagement.Emp_Documents')
        exp = D.filter(emp_doc_expiry_date__lt=today).count()
        soon = D.filter(emp_doc_expiry_date__gte=today, emp_doc_expiry_date__lte=today + timedelta(days=30))
        items = [{'label': f'{d.document_type.type_name if d.document_type_id else "Document"} · {d.emp_id.emp_first_name if d.emp_id_id else ""}',
                  'value': d.emp_doc_expiry_date.strftime('%d %b')} for d in soon.select_related('document_type', 'emp_id').order_by('emp_doc_expiry_date')[:5]]
        card('documents', 'Documents', 'description', 'sub-sidebar/document-expired',
             [{'label': 'Expired', 'value': exp, 'tone': 'bad' if exp else ''}, {'label': 'Expiring in 30 days', 'value': soon.count(), 'tone': 'warn' if soon.exists() else ''}],
             items, 'Next to expire')
    safe(documents)

    # ---------------- requests
    def requests_():
        if not _can(c, 'view_generalrequest', 'view_documentrequest'):
            return
        G = _qs(request, 'EmpManagement.GeneralRequest')
        DR = _qs(request, 'EmpManagement.DocumentRequest') if _model('EmpManagement.DocumentRequest') else None
        card('requests', 'Requests', 'assignment', 'general-sidebar/general-request',
             [{'label': 'General requests waiting', 'value': _st(G, 'pending'), 'tone': 'warn'},
              {'label': 'Document requests waiting', 'value': _st(DR, 'pending') if DR is not None else 0},
              {'label': 'Raised this month', 'value': G.filter(created_at_date__gte=month_start).count() if G is not None else 0}])
    safe(requests_)

    # ---------------- exits
    def exits():
        if not _can(c, 'view_employeeresignation'):
            return
        R = _qs(request, 'EmpManagement.EmployeeResignation')
        card('exits', 'Resignations', 'logout', 'sub-sidebar/resignation-request',
             [{'label': 'Waiting for approval', 'value': _st(R, 'pending'), 'tone': 'warn'},
              {'label': 'Approved this year', 'value': R.filter(status__iexact='approved', resigned_on__year=today.year).count()}])
    safe(exits)

    # ---------------- recruitment
    def recruitment():
        if not _can(c, 'view_jobopening', 'view_candidate', 'view_manpowerrequisition'):
            return
        J = _qs(request, 'RecruitmentManagement.JobOpening')
        A = _qs(request, 'RecruitmentManagement.Application')
        MR = _qs(request, 'RecruitmentManagement.ManpowerRequisition')
        openj = J.exclude(status__in=('draft', 'filled', 'closed', 'cancelled', 'on_hold')) if J is not None else None
        card('recruitment', 'Recruitment', 'person_add', 'recruitment-options',
             [{'label': 'Open positions', 'value': (openj.aggregate(s=Sum('openings'))['s'] or 0) if openj is not None else 0},
              {'label': 'Candidates in process', 'value': A.exclude(stage__in=('hired', 'rejected', 'withdrawn')).count() if A is not None else 0},
              {'label': 'Requisitions to approve', 'value': MR.filter(status__in=('submitted', 'pending', 'in_approval')).count() if MR is not None else 0, 'tone': 'warn'}])
    safe(recruitment)

    # ---------------- performance
    def performance():
        if not _can(c, 'view_appraisalcycle', 'view_goalsheet'):
            return
        CY = _qs(request, 'PerformanceManagement.AppraisalCycle')
        GS = _qs(request, 'PerformanceManagement.GoalSheet')
        cyc = CY.exclude(status__in=('closed', 'draft')).order_by('-period_from').first() if CY is not None else None
        sheets = GS.filter(cycle=cyc) if (GS is not None and cyc) else None
        done = sheets.filter(status__in=('reviewed', 'calibrated', 'approved', 'acknowledged', 'completed')).count() if sheets is not None else 0
        card('performance', 'Performance', 'speed', 'performance-options',
             [{'label': cyc.name if cyc else 'No active cycle', 'value': sheets.count() if sheets is not None else 0, 'suffix': 'goal sheets'},
              {'label': 'Reviewed', 'value': done, 'tone': 'good'},
              {'label': 'Still open', 'value': (sheets.count() - done) if sheets is not None else 0, 'tone': 'warn'}])
    safe(performance)

    # ---------------- learning
    def learning():
        if not _can(c, 'view_trainingsession', 'view_course', 'view_nomination'):
            return
        S = _qs(request, 'LearningManagement.TrainingSession')
        N = _qs(request, 'LearningManagement.Nomination')
        up = S.filter(start_date__gte=today).order_by('start_date') if S is not None else None
        items = [{'label': s.course.title if s.course_id else s.code, 'value': s.start_date.strftime('%d %b')} for s in up.select_related('course')[:4]] if up is not None else []
        card('learning', 'Learning', 'school', 'learning-options',
             [{'label': 'Sessions coming up', 'value': up.count() if up is not None else 0},
              {'label': 'Nominations to approve', 'value': N.filter(Q(manager_status='pending') | Q(ld_status='pending')).count() if N is not None else 0, 'tone': 'warn'}],
             items, 'Next sessions')
    safe(learning)

    return {'cards': cards, 'today': today}


def my_space(request):
    """The logged-in user's own items for the main dashboard (any user linked to an employee)."""
    from .services import ess_summary
    try:
        d = ess_summary(request.user)
    except Exception:
        logger.exception('my space failed')
        return {'employee': None}
    if not d.get('employee'):
        return {'employee': None}
    keep = ('employee', 'leave_balances', 'next_leave', 'payslip', 'loans', 'air_ticket', 'assets', 'documents', 'requests', 'performance', 'training')
    return {k: d.get(k) for k in keep}
