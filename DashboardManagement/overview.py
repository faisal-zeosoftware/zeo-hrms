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


def rpt(key, f=None, **period):
    """Link from a dashboard figure to the report rows behind it (v1.8.1)."""
    from urllib.parse import urlencode
    q = {f'f_{k}': v for k, v in (f or {}).items() if v not in (None, '')}
    q.update({k: str(v) for k, v in period.items() if v})
    return f'{M_}report-options/r/{key}' + ('?' + urlencode(q) if q else '')


def rec(obj):
    """Link from a dashboard line to its record."""
    if obj is None:
        return None
    if obj._meta.label == 'EmpManagement.emp_master':
        return f'{M_}sub-sidebar/employee-details/{obj.pk}/details'
    return f'{M_}report-options/rec/{obj._meta.label}/{obj.pk}'


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
             [{'label': 'Active employees', 'value': active.count() if active is not None else 0, 'link': rpt('employees', {'status': 'Active'})},
              {'label': 'Joined this month', 'value': joiners, 'tone': 'good' if joiners else '', 'link': rpt('joiners-leavers', {'event': 'Joined'}, **{'from': month_start, 'to': today})},
              {'label': 'Leaving in 60 days', 'value': leavers, 'tone': 'warn' if leavers else '', 'link': rpt('exits', **{'from': today, 'to': today + timedelta(days=60)})}],
             [{'label': d['emp_dept_id__dept_name'] or 'No department', 'value': d['n'], 'link': rpt('employees', {'department': d['emp_dept_id__dept_name'], 'status': 'Active'})} for d in by_dept], 'Largest departments')
    safe(people)

    # ---------------- leave
    def leave():
        if not _can(c, 'view_employee_leave_request', 'view_leaveapproval'):
            return
        L = _qs(request, 'calendars.employee_leave_request')
        on = L.filter(status__iexact='approved', start_date__lte=today, end_date__gte=today).count()
        nxt = L.filter(status__iexact='approved', start_date__gt=today, start_date__lte=today + timedelta(days=7)).count()
        card('leave', 'Leave', 'event_busy', 'leave-options/leave-request',
             [{'label': 'On leave today', 'value': on, 'link': rpt('leave', {'status': 'Approved'}, **{'from': today, 'to': today})},
              {'label': 'Waiting for approval', 'value': _st(L, 'pending'), 'tone': 'warn', 'link': rpt('leave', {'status': 'Pending'}, **{'from': '2000-01-01', 'to': '2099-12-31'})},
              {'label': 'Starting in 7 days', 'value': nxt, 'link': rpt('leave', {'status': 'Approved', 'starting': 'Yes'}, **{'from': today + timedelta(days=1), 'to': today + timedelta(days=7)})}])
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
             [{'label': 'Checked in', 'value': present, 'tone': 'good', 'link': rpt('attendance', {'checked_in': 'Yes'}, **{'from': today, 'to': today})},
              {'label': 'Not checked in', 'value': max(total - present, 0), 'tone': 'warn' if total - present > 0 else '', 'link': rpt('attendance', {'checked_in': 'No'}, **{'from': today, 'to': today})},
              {'label': 'Late / early requests', 'value': _st(LI, 'pending') if LI is not None else 0, 'link': rpt('late-early', {'status': 'Pending'}, **{'from': '2000-01-01', 'to': '2099-12-31'})}])
    safe(attendance)

    # ---------------- overtime
    def overtime():
        if not _can(c, 'view_employeeovertime', 'view_attendance'):
            return
        OT = _qs(request, 'calendars.EmployeeOvertime')
        if OT is None:
            return
        mq = OT.filter(date__gte=month_start, date__lte=today)
        hrs = _num(mq.aggregate(s=Sum('hours'))['s'])
        pend = _num(mq.filter(approved=False).aggregate(s=Sum('hours'))['s'])
        top = list(mq.values('employee').annotate(h=Sum('hours')).order_by('-h')[:4])
        from .reports import full_name
        names = {e.id: full_name(e) for e in _model('EmpManagement.emp_master').objects.filter(id__in=[t['employee'] for t in top])}
        per = {'from': month_start, 'to': today}
        card('overtime', 'Overtime this month', 'more_time', 'shift-options/employee-overtime',
             [{'label': 'Overtime hours', 'value': round(hrs, 1), 'link': rpt('overtime', **per)},
              {'label': 'Hours not approved yet', 'value': round(pend, 1), 'tone': 'warn' if pend else '', 'link': rpt('overtime', {'approved': 'No'}, **per)},
              {'label': 'People with overtime', 'value': mq.values('employee').distinct().count(), 'link': rpt('overtime-pay', **per)}],
             [{'label': names.get(t['employee'], ''), 'value': f"{_num(t['h']):g} h",
               'link': rpt('overtime', {'employee': names.get(t['employee'], '')}, **per)} for t in top], 'Most hours')
    safe(overtime)

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
             [{'label': f'Net pay · {last.name}' if last else 'No payroll run yet', 'value': _num(net), 'money': True, 'link': rec(last)},
              {'label': 'Payslips in last run', 'value': P.filter(payroll_run=last).count() if last else 0,
               'link': rpt('payroll-register', {'run': last.name}, **{'from': '2000-01-01', 'to': '2099-12-31'}) if last else None},
              {'label': 'Payslip approvals waiting', 'value': pend, 'tone': 'warn' if pend else '', 'link': M_ + 'salary-options/payslip-approval'}])
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
             [{'label': 'Loan balance outstanding', 'value': _num(out), 'money': True, 'link': rpt('loans', {'open': 'Yes'})},
              {'label': 'Loan requests waiting', 'value': _st(L, 'pending') if L is not None else 0, 'tone': 'warn', 'link': rpt('loans', {'status': 'Pending'})},
              {'label': 'Advance requests waiting', 'value': _st(ADV, 'pending') if ADV is not None else 0, 'link': rpt('advances', {'status': 'Pending'})},
              {'label': 'Air ticket requests waiting', 'value': _st(AT, 'pending') if AT is not None else 0, 'link': M_ + 'air-ticket-options/airticket-request'}])
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
             [{'label': 'Assets registered', 'value': total, 'link': rpt('assets')},
              {'label': 'With employees', 'value': allocated, 'tone': 'good', 'link': rpt('assets', {'held': 'Yes'})},
              {'label': 'Free to allocate', 'value': max(total - allocated, 0), 'link': rpt('assets', {'held': 'No'})},
              {'label': 'Requests waiting', 'value': _st(AR, 'pending') if AR is not None else 0, 'tone': 'warn', 'link': M_ + 'asset-options/asset-request'}],
             [{'label': (s['status'] or 'No status').replace('_', ' ').capitalize(), 'value': s['n'],
               'link': rpt('assets', {'status': (s['status'] or '').replace('_', ' ').capitalize()})} for s in by_status], 'By status')
    safe(assets)

    # ---------------- documents
    def documents():
        if not _can(c, 'view_emp_documents', 'view_notification'):
            return
        D = _qs(request, 'EmpManagement.Emp_Documents')
        exp = D.filter(emp_doc_expiry_date__lt=today).count()
        soon = D.filter(emp_doc_expiry_date__gte=today, emp_doc_expiry_date__lte=today + timedelta(days=30))
        items = [{'label': f'{d.document_type.type_name if d.document_type_id else "Document"} · {d.emp_id.emp_first_name if d.emp_id_id else ""}',
                  'value': d.emp_doc_expiry_date.strftime('%d %b'), 'link': rec(d)} for d in soon.select_related('document_type', 'emp_id').order_by('emp_doc_expiry_date')[:5]]
        card('documents', 'Documents', 'description', 'sub-sidebar/document-expired',
             [{'label': 'Expired', 'value': exp, 'tone': 'bad' if exp else '', 'link': rpt('documents', {'status': 'Expired'})},
              {'label': 'Expiring in 30 days', 'value': soon.count(), 'tone': 'warn' if soon.exists() else '', 'link': rpt('documents', {'status': 'Expiring in 30 days'})}],
             items, 'Next to expire')
    safe(documents)

    # ---------------- tickets / requests (general and document requests are the HR help-desk tickets)
    def requests_():
        if not _can(c, 'view_generalrequest', 'view_documentrequest'):
            return
        G = _qs(request, 'EmpManagement.GeneralRequest')
        DR = _qs(request, 'EmpManagement.DocumentRequest') if _model('EmpManagement.DocumentRequest') else None
        week_ago = today - timedelta(days=7)
        g_open = _st(G, 'pending', 'in progress') if G is not None else 0
        d_open = _st(DR, 'pending', 'in progress') if DR is not None else 0
        late = sum(q.filter(Q(status__iexact='pending') | Q(status__iexact='in progress'), created_at_date__lt=week_ago).count() for q in (G, DR) if q is not None)
        raised = sum(q.filter(created_at_date__gte=month_start).count() for q in (G, DR) if q is not None)
        closed = sum(q.filter(created_at_date__gte=month_start).filter(Q(status__iexact='approved') | Q(status__iexact='rejected') | Q(status__iexact='closed')).count()
                     for q in (G, DR) if q is not None)
        by_type = list(G.filter(Q(status__iexact='pending') | Q(status__iexact='in progress')).values('request_type__name').annotate(n=Count('id')).order_by('-n')[:5]) if G is not None else []
        card('requests', 'Tickets / requests', 'support_agent', 'general-sidebar/general-request',
             [{'label': 'Open tickets', 'value': g_open + d_open, 'tone': 'warn' if g_open + d_open else '',
               'link': rpt('general-requests', {'status': 'Pending'}, **{'from': '2000-01-01', 'to': '2099-12-31'})},
              {'label': 'Open more than 7 days', 'value': late, 'tone': 'bad' if late else '', 'link': rpt('general-requests', {'status': 'Pending'}, **{'from': '2000-01-01', 'to': week_ago})},
              {'label': 'Document requests open', 'value': d_open, 'link': M_ + 'general-sidebar/document-request'},
              {'label': 'Raised this month', 'value': raised, 'link': rpt('general-requests', **{'from': month_start, 'to': today})},
              {'label': 'Closed this month', 'value': closed, 'tone': 'good' if closed else '', 'link': rpt('general-requests', **{'from': month_start, 'to': today})}],
             [{'label': t['request_type__name'] or 'Other', 'value': t['n'], 'link': rpt('general-requests', {'type': t['request_type__name'] or '', 'status': 'Pending'}, **{'from': '2000-01-01', 'to': '2099-12-31'})}
              for t in by_type], 'Open by type')
    safe(requests_)

    # ---------------- exits
    def exits():
        if not _can(c, 'view_employeeresignation'):
            return
        R = _qs(request, 'EmpManagement.EmployeeResignation')
        card('exits', 'Resignations', 'logout', 'sub-sidebar/resignation-request',
             [{'label': 'Waiting for approval', 'value': _st(R, 'pending'), 'tone': 'warn', 'link': rpt('exits', {'status': 'Pending'}, **{'from': '2000-01-01', 'to': '2099-12-31'})},
              {'label': 'Approved this year', 'value': R.filter(status__iexact='approved', resigned_on__year=today.year).count(),
               'link': rpt('exits', {'status': 'Approved'}, **{'from': today.replace(month=1, day=1), 'to': today.replace(month=12, day=31)})}])
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
             [{'label': 'Open positions', 'value': (openj.aggregate(s=Sum('openings'))['s'] or 0) if openj is not None else 0, 'link': rpt('recruitment', {'open': 'Yes'})},
              {'label': 'Candidates in process', 'value': A.exclude(stage__in=('hired', 'rejected', 'withdrawn')).count() if A is not None else 0, 'link': rpt('recruitment', {'open': 'Yes'})},
              {'link': M_ + 'recruitment-options/requisitions', 'label': 'Requisitions to approve', 'value': MR.filter(status__in=('submitted', 'pending', 'in_approval')).count() if MR is not None else 0, 'tone': 'warn'}])
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
             [{'label': cyc.name if cyc else 'No active cycle', 'value': sheets.count() if sheets is not None else 0, 'suffix': 'goal sheets', 'link': rpt('appraisals', {'cycle_name': cyc.name if cyc else ''})},
              {'label': 'Reviewed', 'value': done, 'tone': 'good', 'link': rpt('appraisals', {'cycle_name': cyc.name if cyc else '', 'reviewed': 'Yes'})},
              {'label': 'Still open', 'value': (sheets.count() - done) if sheets is not None else 0, 'tone': 'warn', 'link': rpt('appraisals', {'cycle_name': cyc.name if cyc else '', 'reviewed': 'No'})}])
    safe(performance)

    # ---------------- learning
    def learning():
        if not _can(c, 'view_trainingsession', 'view_course', 'view_nomination'):
            return
        S = _qs(request, 'LearningManagement.TrainingSession')
        N = _qs(request, 'LearningManagement.Nomination')
        up = S.filter(start_date__gte=today).order_by('start_date') if S is not None else None
        items = [{'label': s.course.title if s.course_id else s.code, 'value': s.start_date.strftime('%d %b'), 'link': rec(s)} for s in up.select_related('course')[:4]] if up is not None else []
        card('learning', 'Learning', 'school', 'learning-options',
             [{'label': 'Sessions coming up', 'value': up.count() if up is not None else 0, 'link': M_ + 'learning-options/calendar'},
              {'link': rpt('training', {'waiting': 'Yes'}, **{'from': '2000-01-01', 'to': '2099-12-31'}), 'label': 'Nominations to approve', 'value': N.filter(Q(manager_status='pending') | Q(ld_status='pending')).count() if N is not None else 0, 'tone': 'warn'}],
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
