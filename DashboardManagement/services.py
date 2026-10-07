"""Read-only dashboard aggregations for ESS users and managers (all modules).

Everything here is computed from the existing models; the app has no tables of its own.
Scope rules
  * ESS ("me")    – the employee linked to the logged-in user.
  * Team          – direct and indirect reports of the logged-in user (emp_reporting_manager chain).
  * Company       – every active employee; only for superusers / tenant superusers (HR admins).
Salary figures are only returned in company scope.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q, Sum
from django.utils import timezone

LATE_GRACE_MIN = 15
DEFAULT_START = time(9, 0)


def M(label):
    app, model = label.split('.')
    try:
        return apps.get_model(app, model)
    except LookupError:
        return None


def emp_name(e):
    if not e:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code


def emp_brief(e):
    return {'id': e.id, 'code': e.emp_code, 'name': emp_name(e),
            'department': e.emp_dept_id.dept_name if e.emp_dept_id_id else None,
            'designation': e.emp_desgntn_id.desgntn_job_title if e.emp_desgntn_id_id else None,
            'photo': e.emp_profile_pic.url if getattr(e, 'emp_profile_pic', None) else None}


def is_admin(user):
    if user.is_superuser:
        return True
    try:
        from tenant_users.permissions.models import UserTenantPermissions
        p = UserTenantPermissions.objects.filter(profile=user).first()
        return bool(p and p.is_superuser)
    except Exception:
        return False


def my_employee(user):
    Emp = M('EmpManagement.emp_master')
    return Emp.objects.filter(users=user).select_related('emp_dept_id', 'emp_desgntn_id', 'emp_reporting_manager').first()


def team_ids(user, depth=6):
    """direct + indirect reports (reporting manager is a user)."""
    Emp = M('EmpManagement.emp_master')
    ids, frontier = set(), [user.id]
    for _ in range(depth):
        rows = list(Emp.objects.filter(emp_reporting_manager_id__in=frontier).values_list('id', 'users_id'))
        new = [r for r in rows if r[0] not in ids]
        if not new:
            break
        ids.update(r[0] for r in new)
        frontier = [r[1] for r in new if r[1]]
        if not frontier:
            break
    return ids


def scope_employees(user, scope, department=None):
    Emp = M('EmpManagement.emp_master')
    qs = Emp.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('emp_dept_id', 'emp_desgntn_id', 'users')
    if scope == 'company' and is_admin(user):
        pass
    else:
        qs = qs.filter(id__in=team_ids(user))
    if department:
        qs = qs.filter(emp_dept_id_id=department)
    return qs


def is_working_day(d):
    return d.weekday() < 5  # Mon–Fri (UAE federal weekend Sat/Sun)


# --------------------------------------------------------------------------- leave / attendance helpers
def leave_on(emp_ids, d_from, d_to=None, statuses=('approved',)):
    LR = M('calendars.employee_leave_request')
    d_to = d_to or d_from
    return LR.objects.filter(employee_id__in=emp_ids, status__in=statuses, start_date__lte=d_to, end_date__gte=d_from).select_related('employee', 'leave_type')


def shift_start(att):
    st = getattr(getattr(att, 'shift', None), 'start_time', None)
    return st or DEFAULT_START


def is_late(att):
    if not att.check_in_time:
        return False
    st = shift_start(att)
    limit = (datetime.combine(att.date, st) + timedelta(minutes=LATE_GRACE_MIN)).time()
    return att.check_in_time > limit


def hours(att):
    th = att.total_hours
    if th:
        return round(th.total_seconds() / 3600, 2)
    if att.check_in_time and att.check_out_time:
        return round((datetime.combine(att.date, att.check_out_time) - datetime.combine(att.date, att.check_in_time)).total_seconds() / 3600, 2)
    return 0


def today_status(emp_qs, today):
    Att = M('calendars.Attendance')
    ids = list(emp_qs.values_list('id', flat=True))
    att = {a.employee_id: a for a in Att.objects.filter(employee_id__in=ids, date=today).select_related('shift')}
    on_leave = {l.employee_id: l for l in leave_on(ids, today)}
    present = [i for i in ids if i in att]
    late = [i for i in present if is_late(att[i])]
    not_punched = [i for i in ids if i not in att and i not in on_leave] if is_working_day(today) else []
    return {'ids': ids, 'att': att, 'present': present, 'late': late, 'on_leave': list(on_leave), 'leave_rows': on_leave, 'not_punched': not_punched}


# --------------------------------------------------------------------------- request registry (all modules)
REQUESTS = [
    # key, label, request model, approval model, approval fk, request date field, summary(fn)
    ('leave', 'Leave', 'calendars.employee_leave_request', 'calendars.LeaveApproval', 'leave_request', 'applied_on',
     lambda r: f"{r.leave_type.name if r.leave_type_id else 'Leave'} · {r.start_date:%d/%m} – {r.end_date:%d/%m} ({r.number_of_days or 0:g} d)"),
    ('latein', 'Late in / early out', 'calendars.LateinEarlyoutRequest', 'calendars.LateinEarlyoutApproval', 'lateinearlyout_request', 'date',
     lambda r: f"{(r.request_type or '').replace('_', ' ').title()} · {r.date:%d/%m/%Y}" if r.date else (r.request_type or '')),
    ('general', 'General request', 'EmpManagement.GeneralRequest', 'EmpManagement.Approval', 'general_request', 'created_at_date',
     lambda r: f"{r.request_type.name if r.request_type_id else ''} · {r.reason[:40] if r.reason else ''}"),
    ('document', 'Document request', 'EmpManagement.DocumentRequest', 'EmpManagement.DocumentApproval', 'document_request', 'created_at_date',
     lambda r: f"{r.request_type.type_name if r.request_type_id else ''} · {r.reason[:40] if r.reason else ''}"),
    ('asset', 'Asset request', 'OrganisationManager.AssetRequest', 'OrganisationManager.AssetApproval', 'asset_request', 'request_date',
     lambda r: f"{r.asset_type.name if r.asset_type_id else ''}{' · ' + r.requested_asset.name if r.requested_asset_id else ''}"),
    ('loan', 'Loan', 'PayrollManagement.LoanApplication', 'PayrollManagement.LoanApproval', 'loan_request', 'applied_on',
     lambda r: f"{r.loan_type.loan_type if r.loan_type_id else 'Loan'} · AED {r.amount_requested:,.0f} / {r.repayment_period} m"),
    ('advance', 'Advance salary', 'PayrollManagement.AdvanceSalaryRequest', 'PayrollManagement.AdvanceSalaryApproval', 'request', 'created_at',
     lambda r: f"AED {r.requested_amount:,.0f} · {r.reason[:40] if r.reason else ''}"),
    ('airticket', 'Air ticket', 'PayrollManagement.AirTicketRequest', 'PayrollManagement.AirticketApproval', 'request', 'request_date',
     lambda r: f"{(r.request_type or '').title()}{' · ' + (r.origin or '') + '→' + (r.destination or '') if r.destination else ''}"),
    ('resignation', 'Resignation', 'EmpManagement.EmployeeResignation', 'EmpManagement.ResignationApproval', 'resignation_request', 'document_date',
     lambda r: f"Last working day {r.last_working_date:%d/%m/%Y}" if r.last_working_date else 'Resignation'),
]
REQ = {r[0]: r for r in REQUESTS}


def _date_of(obj, field):
    v = getattr(obj, field, None)
    if isinstance(v, datetime):
        v = timezone.localtime(v).date() if timezone.is_aware(v) else v.date()
    return v


def _status_key(s):
    s = (s or '').lower()
    if s in ('pending', 'in progress', 'submitted', 'escalated'):
        return 'pending'
    if s in ('approved', 'processed', 'deducted', 'disbursed', 'closed', 'paid'):
        return 'approved'
    if s in ('rejected', 'cancelled', 'declined'):
        return 'rejected'
    return s or 'pending'


def my_requests(emp, limit=None):
    rows = []
    for key, label, rm, am, fk, dfield, summ in REQUESTS:
        RM = M(rm)
        if not RM or not emp:
            continue
        qs = RM.objects.filter(employee=emp)
        for r in qs:
            try:
                text = summ(r)
            except Exception:
                text = ''
            rows.append({'module': key, 'module_label': label, 'id': r.id, 'document_number': getattr(r, 'document_number', None),
                         'summary': text, 'date': _date_of(r, dfield), 'status': r.status, 'status_key': _status_key(r.status)})
    rows.sort(key=lambda x: (x['date'] or date.min, x['id']), reverse=True)
    return rows[:limit] if limit else rows


def pending_approvals(user):
    """approvals waiting for this user (or delegated to them), all modules incl. the new ones"""
    out = []
    for key, label, rm, am, fk, dfield, summ in REQUESTS:
        AM = M(am)
        if not AM:
            continue
        qs = AM.objects.filter(Q(approver=user) | Q(deligate_to=user, is_deligate=True)).filter(status__iexact='pending')
        qs = qs.select_related(fk)
        for a in qs:
            r = getattr(a, fk, None)
            if r is None:
                continue
            e = getattr(r, 'employee', None)
            try:
                text = summ(r)
            except Exception:
                text = ''
            out.append({'module': key, 'module_label': label, 'approval_id': a.id, 'request_id': r.id, 'level': getattr(a, 'level', None),
                        'document_number': getattr(r, 'document_number', None), 'summary': text, 'date': _date_of(r, dfield),
                        'employee_id': e.id if e else None, 'employee': emp_name(e), 'employee_code': e.emp_code if e else None})
    # new modules
    GS = M('PerformanceManagement.GoalSheet')
    if GS:
        for s in GS.objects.filter(manager=user, status__in=('submitted', 'self_submitted')).select_related('employee', 'cycle'):
            out.append({'module': 'goals', 'module_label': 'Goal sheet', 'approval_id': s.id, 'request_id': s.id, 'level': 1, 'document_number': None,
                        'summary': f"{s.cycle.name} · {'goals to approve' if s.status == 'submitted' else 'self appraisal to review'}",
                        'date': timezone.localtime(s.updated_at).date() if getattr(s, 'updated_at', None) else None,
                        'employee_id': s.employee_id, 'employee': emp_name(s.employee), 'employee_code': s.employee.emp_code})
    NOM = M('LearningManagement.Nomination')
    if NOM:
        ids = team_ids(user)
        for n in NOM.objects.filter(employee_id__in=ids, manager_status='pending').exclude(seat_status='cancelled').select_related('employee', 'session__course'):
            out.append({'module': 'nomination', 'module_label': 'Training nomination', 'approval_id': n.id, 'request_id': n.id, 'level': 1, 'document_number': n.session.code,
                        'summary': f"{n.session.course.title} · {n.session.start_date:%d/%m/%Y}", 'date': n.session.start_date,
                        'employee_id': n.employee_id, 'employee': emp_name(n.employee), 'employee_code': n.employee.emp_code})
    RA = M('RecruitmentManagement.RequisitionApproval')
    if RA:
        for a in RA.objects.filter(approver=user, status='pending').select_related('requisition'):
            r = a.requisition
            if r.status != 'pending':
                continue
            out.append({'module': 'requisition', 'module_label': 'Manpower requisition', 'approval_id': a.id, 'request_id': r.id, 'level': a.level,
                        'document_number': r.document_number, 'summary': f"{r.position_title} · {r.headcount} HC", 'date': timezone.localtime(r.created_at).date(),
                        'employee_id': None, 'employee': r.requested_by.username if r.requested_by_id else '', 'employee_code': None})
    out.sort(key=lambda x: (x['date'] or date.min), reverse=False)
    return out


# --------------------------------------------------------------------------- ESS summary
def ess_summary(user):
    emp = my_employee(user)
    today = timezone.localdate()
    data = {'today': today, 'employee': None, 'has_team': bool(team_ids(user, depth=1)), 'is_admin': is_admin(user)}
    if not emp:
        data['message'] = 'This login is not linked to an employee record.'
        data['approvals'] = summarize_approvals(pending_approvals(user))
        return data
    data['employee'] = dict(emp_brief(emp), joined=emp.emp_joined_date,
                            manager=emp.emp_reporting_manager.username if emp.emp_reporting_manager_id else None,
                            service_years=round((today - emp.emp_joined_date).days / 365.25, 1) if emp.emp_joined_date else None)

    # leave balances
    LB = M('calendars.emp_leave_balance')
    LR = M('calendars.employee_leave_request')
    used = defaultdict(float)
    for r in LR.objects.filter(employee=emp, status='approved', start_date__year=today.year):
        used[r.leave_type_id] += float(r.approved_days or r.number_of_days or 0)
    bal = []
    for b in LB.objects.filter(employee=emp).select_related('leave_type').order_by('leave_type__name'):
        if not (b.openings or b.balance or used.get(b.leave_type_id)):
            continue
        bal.append({'leave_type_id': b.leave_type_id, 'name': b.leave_type.name, 'balance': b.balance or 0, 'openings': b.openings or 0, 'used': used.get(b.leave_type_id, 0)})
    data['leave_balances'] = bal
    nxt = LR.objects.filter(employee=emp, status__in=('approved', 'pending'), end_date__gte=today).order_by('start_date').first()
    data['next_leave'] = {'type': nxt.leave_type.name, 'from': nxt.start_date, 'to': nxt.end_date, 'days': nxt.number_of_days, 'status': nxt.status} if nxt else None

    # attendance – this month + last 30 days
    Att = M('calendars.Attendance')
    month_start = today.replace(day=1)
    att = list(Att.objects.filter(employee=emp, date__gte=today - timedelta(days=45), date__lte=today).select_related('shift').order_by('date'))
    by_day = {a.date: a for a in att}
    leaves = list(leave_on([emp.id], today - timedelta(days=45), today))
    leave_days = set()
    for l in leaves:
        d = max(l.start_date, today - timedelta(days=45))
        while d <= min(l.end_date, today):
            leave_days.add(d)
            d += timedelta(days=1)
    m_days = [month_start + timedelta(days=i) for i in range((today - month_start).days + 1)]
    work = [d for d in m_days if is_working_day(d)]
    m_att = [by_day[d] for d in m_days if d in by_day]
    data['attendance'] = {
        'month_label': today.strftime('%B %Y'),
        'working_days': len(work), 'present': len(m_att), 'late': sum(1 for a in m_att if is_late(a)),
        'leave': len([d for d in work if d in leave_days]),
        'absent': len([d for d in work if d not in by_day and d not in leave_days and d < today]),
        'avg_hours': round(sum(hours(a) for a in m_att) / len(m_att), 2) if m_att else 0,
        'today': {'checked_in': by_day[today].check_in_time if today in by_day else None, 'checked_out': by_day[today].check_out_time if today in by_day else None},
    }
    trend = []
    for i in range(29, -1, -1):
        d = today - timedelta(days=i)
        a = by_day.get(d)
        trend.append({'date': d, 'hours': hours(a) if a else 0, 'late': bool(a and is_late(a)), 'working': is_working_day(d),
                      'status': 'present' if a else ('leave' if d in leave_days else ('off' if not is_working_day(d) else ('absent' if d < today else 'today')))})
    data['attendance_trend'] = trend

    # requests (all modules)
    reqs = my_requests(emp)
    data['requests'] = {'recent': reqs[:8], 'counts': dict(Counter(r['status_key'] for r in reqs)),
                        'by_module': [{'module': k, 'label': REQ[k][1], 'count': c, 'pending': sum(1 for r in reqs if r['module'] == k and r['status_key'] == 'pending')}
                                      for k, c in Counter(r['module'] for r in reqs).items()]}

    # payroll
    PS = M('PayrollManagement.Payslip')
    last = PS.objects.filter(employee=emp).select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month', '-id').first()
    data['payslip'] = {'id': last.id, 'period': f"{last.payroll_run.name}" if last.payroll_run_id else '', 'gross': last.gross_salary, 'deductions': last.total_deductions,
                       'net': last.net_salary, 'status': last.status} if last else None
    LA = M('PayrollManagement.LoanApplication')
    loans = list(LA.objects.filter(employee=emp, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')))
    data['loans'] = {'count': len(loans), 'outstanding': sum((l.remaining_balance or 0) for l in loans), 'emi': sum((l.emi_amount or 0) for l in loans)}
    ATA = M('PayrollManagement.AirTicketAllocation')
    al = ATA.objects.filter(employee=emp, is_active=True, status='APPROVED').order_by('-id').first() if ATA else None
    data['air_ticket'] = {'amount': al.amount, 'remaining': al.remaining_amount, 'expiry': al.expiry_date} if al else None

    # assets & documents
    AA = M('OrganisationManager.AssetAllocation')
    data['assets'] = [{'name': a.asset.name, 'serial': a.asset.serial_number, 'since': a.assigned_date} for a in AA.objects.filter(employee=emp, returned_date__isnull=True).select_related('asset')]
    DOC = M('EmpManagement.Emp_Documents')
    docs = []
    for d in DOC.objects.filter(emp_id=emp).select_related('document_type').order_by('emp_doc_expiry_date'):
        if d.emp_doc_expiry_date:
            days = (d.emp_doc_expiry_date - today).days
            docs.append({'type': d.document_type.type_name if d.document_type_id else '', 'number': d.emp_doc_number, 'expiry': d.emp_doc_expiry_date, 'days': days,
                         'state': 'expired' if days < 0 else ('expiring' if days <= 60 else 'valid')})
    data['documents'] = docs

    # performance & learning
    GS = M('PerformanceManagement.GoalSheet')
    if GS:
        s = GS.objects.filter(employee=emp, cycle__status__in=('active', 'calibration')).select_related('cycle').order_by('-cycle__period_from').first()
        if s:
            goals = list(s.goals.all())
            prog = round(sum(float(g.progress_percent or 0) * float(g.weight or 0) for g in goals) / max(sum(float(g.weight or 0) for g in goals), 1), 0) if goals else 0
            data['performance'] = {'cycle': s.cycle.name, 'status': s.status, 'status_label': s.get_status_display(), 'goals': len(goals), 'progress': prog,
                                   'self_score': s.self_score, 'final_rating': s.final_rating}
    NOM = M('LearningManagement.Nomination')
    if NOM:
        data['training'] = [{'course': n.session.course.title, 'date': n.session.start_date, 'seat': n.seat_status}
                            for n in NOM.objects.filter(employee=emp, session__start_date__gte=today).exclude(seat_status='cancelled').select_related('session__course').order_by('session__start_date')[:5]]
    CERT = M('LearningManagement.Certificate')
    if CERT:
        data['certificates'] = [{'title': c.title, 'expiry': c.expiry_date, 'status': c.status} for c in CERT.objects.filter(employee=emp).order_by('-issued_on')[:6]]

    data['holidays'] = upcoming_holidays(today)
    data['announcements'] = announcements()
    data['approvals'] = summarize_approvals(pending_approvals(user))
    return data


def upcoming_holidays(today, n=5):
    H = M('calendars.holiday')
    return [{'name': h.description, 'from': h.start_date, 'to': h.end_date} for h in H.objects.filter(end_date__gte=today).order_by('start_date')[:n]]


def announcements(n=4):
    A = M('OrganisationManager.Announcement')
    now = timezone.now()
    qs = A.objects.filter(Q(expires_at__isnull=True) | Q(expires_at__gte=now)).order_by('-is_sticky', '-created_at')[:n]
    return [{'id': a.id, 'title': a.title, 'message': (a.message or '')[:220], 'date': timezone.localtime(a.created_at).date() if a.created_at else None, 'sticky': a.is_sticky} for a in qs]


def summarize_approvals(items):
    by = Counter(i['module'] for i in items)
    labels = {i['module']: i['module_label'] for i in items}
    today = timezone.localdate()
    oldest = min((i['date'] for i in items if i['date']), default=None)
    return {'total': len(items), 'by_module': [{'module': k, 'label': labels[k], 'count': v} for k, v in by.most_common()],
            'oldest_days': (today - oldest).days if oldest else None, 'items': items[:6]}


# --------------------------------------------------------------------------- manager / HR summary
def team_summary(user, scope='team', department=None):
    today = timezone.localdate()
    admin = is_admin(user)
    if scope == 'company' and not admin:
        scope = 'team'
    emps = list(scope_employees(user, scope, department))
    ids = [e.id for e in emps]
    emp_by_id = {e.id: e for e in emps}
    st = today_status(scope_employees(user, scope, department), today)
    data = {'today': today, 'scope': scope, 'is_admin': admin, 'department': department, 'headcount': len(ids)}
    data['today_status'] = {'present': len(st['present']), 'late': len(st['late']), 'on_leave': len(st['on_leave']), 'not_punched': len(st['not_punched']),
                            'working_day': is_working_day(today)}

    # attendance trend – last 14 working days, % of headcount present
    Att = M('calendars.Attendance')
    days, d = [], today
    while len(days) < 14:
        if is_working_day(d):
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    pres = Counter(Att.objects.filter(employee_id__in=ids, date__in=days).values_list('date', flat=True))
    late_by_day = Counter(a.date for a in Att.objects.filter(employee_id__in=ids, date__in=days).select_related('shift') if is_late(a))
    lv = leave_on(ids, days[0], days[-1])
    leave_by_day = Counter()
    for l in lv:
        for x in days:
            if l.start_date <= x <= l.end_date:
                leave_by_day[x] += 1
    data['attendance_trend'] = [{'date': x, 'present': pres.get(x, 0), 'late': late_by_day.get(x, 0), 'on_leave': leave_by_day.get(x, 0),
                                 'rate': round(pres.get(x, 0) * 100 / len(ids), 1) if ids else 0} for x in days]

    # who is out – next 14 days
    out = leave_on(ids, today, today + timedelta(days=14), statuses=('approved', 'pending')).order_by('start_date')
    data['out_next_14'] = [{'employee_id': l.employee_id, 'employee': emp_name(l.employee), 'type': l.leave_type.name if l.leave_type_id else '',
                            'from': l.start_date, 'to': l.end_date, 'days': l.number_of_days, 'status': l.status} for l in out[:12]]

    # composition
    if scope == 'company' and not department:
        comp = Counter((e.emp_dept_id_id, e.emp_dept_id.dept_name if e.emp_dept_id_id else 'No department') for e in emps)
        data['composition'] = {'by': 'department', 'rows': [{'key': k[0], 'label': k[1], 'count': v} for k, v in comp.most_common()]}
    else:
        comp = Counter((e.emp_desgntn_id_id, e.emp_desgntn_id.desgntn_job_title if e.emp_desgntn_id_id else 'No designation') for e in emps)
        data['composition'] = {'by': 'designation', 'rows': [{'key': k[0], 'label': k[1], 'count': v} for k, v in comp.most_common()]}

    # approvals waiting for me
    data['approvals'] = summarize_approvals(pending_approvals(user))

    # open requests from the team (all modules)
    open_by = Counter()
    for key, label, rm, am, fk, dfield, summ in REQUESTS:
        RM = M(rm)
        if RM:
            n = RM.objects.filter(employee_id__in=ids, status__iregex=r'^(pending|in progress)$').count()
            if n:
                open_by[key] = n
    data['team_open_requests'] = [{'module': k, 'label': REQ[k][1], 'count': v} for k, v in open_by.most_common()]

    # documents expiring (60 days) / expired
    DOC = M('EmpManagement.Emp_Documents')
    dq = DOC.objects.filter(emp_id_id__in=ids, emp_doc_expiry_date__lte=today + timedelta(days=60))
    data['documents'] = {'expired': dq.filter(emp_doc_expiry_date__lt=today).count(), 'expiring': dq.filter(emp_doc_expiry_date__gte=today).count()}

    # birthdays & work anniversaries this month
    data['celebrations'] = sorted(
        [{'employee_id': e.id, 'employee': emp_name(e), 'kind': 'Birthday', 'day': e.emp_date_of_birth.day} for e in emps if e.emp_date_of_birth and e.emp_date_of_birth.month == today.month] +
        [{'employee_id': e.id, 'employee': emp_name(e), 'kind': f"{today.year - e.emp_joined_date.year} yr anniversary", 'day': e.emp_joined_date.day}
         for e in emps if e.emp_joined_date and e.emp_joined_date.month == today.month and e.emp_joined_date.year < today.year],
        key=lambda x: x['day'])[:8]

    # performance – active cycle status of the team
    GS = M('PerformanceManagement.GoalSheet')
    if GS:
        cyc = M('PerformanceManagement.AppraisalCycle').objects.filter(status__in=('active', 'calibration')).order_by('-period_from').first()
        if cyc:
            order = ['draft', 'submitted', 'approved', 'self_submitted', 'reviewed', 'calibrated', 'acknowledged']
            labels = dict(GS._meta.get_field('status').choices)
            cnt = Counter(GS.objects.filter(cycle=cyc, employee_id__in=ids).values_list('status', flat=True))
            data['performance'] = {'cycle_id': cyc.id, 'cycle': cyc.name, 'rows': [{'key': s, 'label': labels[s], 'count': cnt.get(s, 0)} for s in order]}

    # learning
    NOM = M('LearningManagement.Nomination')
    CERT = M('LearningManagement.Certificate')
    if NOM:
        data['learning'] = {
            'upcoming_trainings': NOM.objects.filter(employee_id__in=ids, session__start_date__gte=today).exclude(seat_status='cancelled').count(),
            'certificates_expiring': sum(1 for c in CERT.objects.filter(employee_id__in=ids, expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60)) if c.status in ('expiring', 'expired')),
        }

    # assets in the team
    AA = M('OrganisationManager.AssetAllocation')
    data['assets_assigned'] = AA.objects.filter(employee_id__in=ids, returned_date__isnull=True).count()

    # recruitment
    MR = M('RecruitmentManagement.ManpowerRequisition')
    JO = M('RecruitmentManagement.JobOpening')
    if MR:
        mq = MR.objects.all() if scope == 'company' else MR.objects.filter(requested_by=user)
        data['recruitment'] = {'open_requisitions': mq.filter(status__in=('pending', 'approved')).count(),
                               'open_jobs': JO.objects.filter(status__in=('published', 'interviewing')).count() if scope == 'company' else
                               JO.objects.filter(requisition__requested_by=user, status__in=('published', 'interviewing')).count()}

    # payroll (HR admins only)
    if scope == 'company':
        PS = M('PayrollManagement.Payslip')
        PR = M('PayrollManagement.PayrollRun')
        runs = list(PR.objects.order_by('-year', '-month')[:6])
        runs.reverse()
        pay = []
        for r in runs:
            agg = PS.objects.filter(payroll_run=r, employee_id__in=ids).aggregate(g=Sum('gross_salary'), d=Sum('total_deductions'), n=Sum('net_salary'))
            pay.append({'run_id': r.id, 'label': f"{date(r.year, r.month, 1):%b %Y}", 'gross': agg['g'] or 0, 'deductions': agg['d'] or 0, 'net': agg['n'] or 0})
        data['payroll'] = pay
        LA = M('PayrollManagement.LoanApplication')
        data['loans_outstanding'] = LA.objects.filter(employee_id__in=ids, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')).aggregate(s=Sum('remaining_balance'))['s'] or 0
    data['holidays'] = upcoming_holidays(today, 3)
    return data


# --------------------------------------------------------------------------- employee 360
def can_view_employee(user, emp):
    if is_admin(user):
        return True
    if emp.users_id == user.id:
        return True
    return emp.id in team_ids(user)


def employee_360(user, emp):
    today = timezone.localdate()
    data = {'employee': dict(emp_brief(emp), joined=emp.emp_joined_date, email=emp.emp_company_email, mobile=str(emp.emp_mobile_number_1 or ''),
                             manager=emp.emp_reporting_manager.username if emp.emp_reporting_manager_id else None,
                             nationality=emp.emp_nationality.N_name if getattr(emp, 'emp_nationality_id', None) else None)}
    LB = M('calendars.emp_leave_balance')
    data['leave_balances'] = [{'name': b.leave_type.name, 'balance': b.balance, 'openings': b.openings}
                              for b in LB.objects.filter(employee=emp).select_related('leave_type') if (b.balance or b.openings)]
    Att = M('calendars.Attendance')
    m0 = today.replace(day=1)
    att = list(Att.objects.filter(employee=emp, date__gte=m0, date__lte=today).select_related('shift'))
    data['attendance'] = {'present': len(att), 'late': sum(1 for a in att if is_late(a)), 'avg_hours': round(sum(hours(a) for a in att) / len(att), 2) if att else 0,
                          'last': [{'date': a.date, 'in': a.check_in_time, 'out': a.check_out_time, 'hours': hours(a), 'late': is_late(a)}
                                   for a in sorted(att, key=lambda a: a.date, reverse=True)[:7]]}
    data['requests'] = my_requests(emp, limit=10)
    AA = M('OrganisationManager.AssetAllocation')
    data['assets'] = [{'name': a.asset.name, 'serial': a.asset.serial_number, 'since': a.assigned_date} for a in AA.objects.filter(employee=emp, returned_date__isnull=True).select_related('asset')]
    DOC = M('EmpManagement.Emp_Documents')
    data['documents'] = [{'type': d.document_type.type_name if d.document_type_id else '', 'expiry': d.emp_doc_expiry_date,
                          'days': (d.emp_doc_expiry_date - today).days if d.emp_doc_expiry_date else None} for d in DOC.objects.filter(emp_id=emp).select_related('document_type')]
    GS = M('PerformanceManagement.GoalSheet')
    if GS:
        s = GS.objects.filter(employee=emp).select_related('cycle').order_by('-cycle__period_from').first()
        data['performance'] = {'cycle': s.cycle.name, 'status': s.get_status_display(), 'self_score': s.self_score, 'manager_score': s.manager_score,
                               'final_rating': s.final_rating} if s else None
    NOM = M('LearningManagement.Nomination')
    if NOM:
        data['training'] = [{'course': n.session.course.title, 'date': n.session.start_date, 'seat': n.seat_status}
                            for n in NOM.objects.filter(employee=emp).exclude(seat_status='cancelled').select_related('session__course').order_by('-session__start_date')[:5]]
    if is_admin(user):
        PS = M('PayrollManagement.Payslip')
        p = PS.objects.filter(employee=emp).select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month').first()
        data['payslip'] = {'period': p.payroll_run.name, 'gross': p.gross_salary, 'deductions': p.total_deductions, 'net': p.net_salary} if p else None
    return data


# --------------------------------------------------------------------------- drill-down lists
def col(key, label, typ='text'):
    return {'key': key, 'label': label, 'type': typ}


EMP_COLS = [col('code', 'Code'), col('name', 'Employee'), col('department', 'Department'), col('designation', 'Designation')]


def drill(user, metric, params):
    today = timezone.localdate()
    scope = params.get('scope') or 'team'
    if scope == 'company' and not is_admin(user):
        scope = 'team'
    department = params.get('department') or None
    module = params.get('module')
    title, cols, rows = metric, EMP_COLS, []

    if metric in ('my_requests', 'my_attendance', 'my_documents', 'my_payslips', 'my_leave'):
        emp = my_employee(user)
        if not emp:
            return {'title': 'No employee record', 'columns': [], 'rows': []}
        if metric == 'my_requests':
            reqs = my_requests(emp)
            if module:
                reqs = [r for r in reqs if r['module'] == module]
            st = params.get('status')
            if st:
                reqs = [r for r in reqs if r['status_key'] == st]
            return {'title': 'My requests' + (f" – {REQ[module][1]}" if module in REQ else ''),
                    'columns': [col('module_label', 'Type'), col('document_number', 'Number'), col('summary', 'Details'), col('date', 'Date', 'date'), col('status', 'Status', 'status')],
                    'rows': reqs}
        if metric == 'my_attendance':
            Att = M('calendars.Attendance')
            d0 = today - timedelta(days=int(params.get('days') or 30))
            rows = [{'date': a.date, 'shift': a.shift.name if a.shift_id else '', 'in': a.check_in_time, 'out': a.check_out_time, 'hours': hours(a), 'late': 'Late' if is_late(a) else ''}
                    for a in Att.objects.filter(employee=emp, date__gte=d0).select_related('shift').order_by('-date')]
            return {'title': 'My attendance', 'columns': [col('date', 'Date', 'date'), col('shift', 'Shift'), col('in', 'In', 'time'), col('out', 'Out', 'time'), col('hours', 'Hours', 'number'), col('late', 'Flag', 'status')], 'rows': rows}
        if metric == 'my_payslips':
            PS = M('PayrollManagement.Payslip')
            rows = [{'period': p.payroll_run.name if p.payroll_run_id else '', 'gross': p.gross_salary, 'deductions': p.total_deductions, 'net': p.net_salary, 'status': p.status,
                     'lines': ', '.join(f"{c.component.name} {c.amount:,.0f}" for c in p.components.select_related('component') if c.component_id)}
                    for p in PS.objects.filter(employee=emp).select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month')]
            return {'title': 'My payslips', 'columns': [col('period', 'Payroll'), col('gross', 'Gross', 'money'), col('deductions', 'Deductions', 'money'), col('net', 'Net', 'money'), col('lines', 'Components')], 'rows': rows}
        if metric == 'my_leave':
            LR = M('calendars.employee_leave_request')
            qs = LR.objects.filter(employee=emp).select_related('leave_type').order_by('-start_date')
            if params.get('leave_type'):
                qs = qs.filter(leave_type_id=params['leave_type'])
            rows = [{'document_number': r.document_number, 'type': r.leave_type.name, 'from': r.start_date, 'to': r.end_date, 'days': r.number_of_days, 'status': r.status} for r in qs]
            return {'title': 'My leave', 'columns': [col('document_number', 'Number'), col('type', 'Type'), col('from', 'From', 'date'), col('to', 'To', 'date'), col('days', 'Days', 'number'), col('status', 'Status', 'status')], 'rows': rows}
        if metric == 'my_documents':
            return {'title': 'My documents', 'columns': [col('type', 'Document'), col('number', 'Number'), col('expiry', 'Expiry', 'date'), col('days', 'Days left', 'number'), col('state', 'Status', 'status')],
                    'rows': ess_summary(user).get('documents', [])}

    if metric == 'approvals':
        items = pending_approvals(user)
        if module:
            items = [i for i in items if i['module'] == module]
        return {'title': 'Waiting for my approval' + (f" – {items[0]['module_label']}" if module and items else ''),
                'columns': [col('module_label', 'Type'), col('document_number', 'Number'), col('employee', 'Employee'), col('summary', 'Details'), col('date', 'Requested', 'date'), col('age', 'Waiting', 'days')],
                'rows': [dict(i, age=(today - i['date']).days if i['date'] else None) for i in items]}

    emps = scope_employees(user, scope, department)
    ids = list(emps.values_list('id', flat=True))

    def emp_rows(qs_ids, extra=None):
        out = []
        for e in emps.filter(id__in=qs_ids):
            r = dict(emp_brief(e), employee_id=e.id)
            if extra:
                r.update(extra.get(e.id, {}))
            out.append(r)
        return out

    if metric == 'headcount':
        dep = params.get('group_department')
        des = params.get('group_designation')
        qs = emps
        if dep:
            qs = qs.filter(emp_dept_id_id=dep)
        if des:
            qs = qs.filter(emp_desgntn_id_id=des)
        rows = [dict(emp_brief(e), employee_id=e.id, joined=e.emp_joined_date) for e in qs.order_by('emp_code')]
        return {'title': 'Employees', 'columns': EMP_COLS + [col('joined', 'Joined', 'date')], 'rows': rows}
    if metric in ('present_today', 'late_today', 'not_punched_today', 'on_leave_today', 'attendance_day'):
        d = date.fromisoformat(params['date']) if params.get('date') else today
        st = today_status(emps, d)
        if metric == 'on_leave_today':
            rows = [dict(emp_brief(l.employee), employee_id=l.employee_id, type=l.leave_type.name, to=l.end_date) for l in st['leave_rows'].values()]
            return {'title': f"On leave · {d:%d/%m/%Y}", 'columns': EMP_COLS + [col('type', 'Leave'), col('to', 'Back after', 'date')], 'rows': rows}
        if metric == 'not_punched_today':
            return {'title': f"Not checked in · {d:%d/%m/%Y}", 'columns': EMP_COLS, 'rows': emp_rows(st['not_punched'])}
        sel = st['late'] if metric == 'late_today' else st['present']
        if params.get('flag') == 'late':
            sel = st['late']
        extra = {i: {'in': st['att'][i].check_in_time, 'out': st['att'][i].check_out_time, 'late': 'Late' if i in st['late'] else ''} for i in sel}
        return {'title': f"{'Late' if sel is st['late'] else 'Present'} · {d:%d/%m/%Y}", 'columns': EMP_COLS + [col('in', 'In', 'time'), col('out', 'Out', 'time'), col('late', 'Flag', 'status')],
                'rows': emp_rows(sel, extra)}
    if metric == 'out_next':
        out = leave_on(ids, today, today + timedelta(days=int(params.get('days') or 30)), statuses=('approved', 'pending')).order_by('start_date')
        rows = [dict(emp_brief(l.employee), employee_id=l.employee_id, type=l.leave_type.name, **{'from': l.start_date}, to=l.end_date, days=l.number_of_days, status=l.status) for l in out]
        return {'title': 'Leave – next 30 days', 'columns': EMP_COLS[:2] + [col('type', 'Leave'), col('from', 'From', 'date'), col('to', 'To', 'date'), col('days', 'Days', 'number'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'team_requests':
        rows = []
        for key, label, rm, am, fk, dfield, summ in REQUESTS:
            if module and key != module:
                continue
            RM = M(rm)
            for r in RM.objects.filter(employee_id__in=ids, status__iregex=r'^(pending|in progress)$').select_related('employee'):
                try:
                    text = summ(r)
                except Exception:
                    text = ''
                rows.append(dict(employee_id=r.employee_id, code=r.employee.emp_code, name=emp_name(r.employee), module_label=label,
                                 document_number=getattr(r, 'document_number', None), summary=text, date=_date_of(r, dfield), status=r.status))
        return {'title': 'Open requests from the team', 'columns': [col('module_label', 'Type'), col('document_number', 'Number'), col('name', 'Employee'), col('summary', 'Details'), col('date', 'Date', 'date'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'documents':
        DOC = M('EmpManagement.Emp_Documents')
        q = DOC.objects.filter(emp_id_id__in=ids, emp_doc_expiry_date__lte=today + timedelta(days=60)).select_related('emp_id', 'document_type').order_by('emp_doc_expiry_date')
        if params.get('state') == 'expired':
            q = q.filter(emp_doc_expiry_date__lt=today)
        elif params.get('state') == 'expiring':
            q = q.filter(emp_doc_expiry_date__gte=today)
        rows = [{'employee_id': d.emp_id_id, 'code': d.emp_id.emp_code, 'name': emp_name(d.emp_id), 'type': d.document_type.type_name if d.document_type_id else '',
                 'number': d.emp_doc_number, 'expiry': d.emp_doc_expiry_date, 'days': (d.emp_doc_expiry_date - today).days,
                 'state': 'expired' if d.emp_doc_expiry_date < today else 'expiring'} for d in q]
        return {'title': 'Documents expired / expiring in 60 days', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('type', 'Document'), col('number', 'Number'), col('expiry', 'Expiry', 'date'), col('days', 'Days', 'number'), col('state', 'Status', 'status')], 'rows': rows}
    if metric == 'goal_status':
        GS = M('PerformanceManagement.GoalSheet')
        q = GS.objects.filter(employee_id__in=ids, cycle_id=params.get('cycle')).select_related('employee', 'cycle')
        if params.get('status'):
            q = q.filter(status=params['status'])
        rows = [{'employee_id': s.employee_id, 'code': s.employee.emp_code, 'name': emp_name(s.employee), 'status': s.get_status_display(), 'self_score': s.self_score,
                 'manager_score': s.manager_score, 'final_rating': s.final_rating} for s in q]
        return {'title': 'Goal sheets', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('status', 'Stage', 'status'), col('self_score', 'Self', 'number'), col('manager_score', 'Manager', 'number'), col('final_rating', 'Final', 'number')], 'rows': rows}
    if metric == 'trainings':
        NOM = M('LearningManagement.Nomination')
        q = NOM.objects.filter(employee_id__in=ids, session__start_date__gte=today).exclude(seat_status='cancelled').select_related('employee', 'session__course').order_by('session__start_date')
        rows = [{'employee_id': n.employee_id, 'code': n.employee.emp_code, 'name': emp_name(n.employee), 'course': n.session.course.title, 'date': n.session.start_date, 'seat': n.seat_status} for n in q]
        return {'title': 'Upcoming trainings', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('course', 'Course'), col('date', 'Date', 'date'), col('seat', 'Seat', 'status')], 'rows': rows}
    if metric == 'certificates':
        CERT = M('LearningManagement.Certificate')
        rows = [{'employee_id': c.employee_id, 'code': c.employee.emp_code, 'name': emp_name(c.employee), 'title': c.title, 'expiry': c.expiry_date, 'status': c.status}
                for c in CERT.objects.filter(employee_id__in=ids, expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60)).select_related('employee') if c.status in ('expiring', 'expired')]
        return {'title': 'Certificates expiring / expired', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('title', 'Certificate'), col('expiry', 'Expiry', 'date'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'assets':
        AA = M('OrganisationManager.AssetAllocation')
        rows = [{'employee_id': a.employee_id, 'code': a.employee.emp_code, 'name': emp_name(a.employee), 'asset': a.asset.name, 'serial': a.asset.serial_number, 'since': a.assigned_date}
                for a in AA.objects.filter(employee_id__in=ids, returned_date__isnull=True).select_related('employee', 'asset')]
        return {'title': 'Assets with the team', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('asset', 'Asset'), col('serial', 'Serial'), col('since', 'Since', 'date')], 'rows': rows}
    if metric == 'celebrations':
        return {'title': 'Birthdays & anniversaries this month', 'columns': [col('employee', 'Employee'), col('kind', 'Occasion'), col('day', 'Day', 'number')],
                'rows': team_summary(user, scope, department)['celebrations']}
    if metric == 'payroll_run' and scope == 'company':
        PS = M('PayrollManagement.Payslip')
        q = PS.objects.filter(payroll_run_id=params.get('run'), employee_id__in=ids).select_related('employee__emp_dept_id', 'employee__emp_desgntn_id').order_by('employee__emp_code')
        rows = [dict(emp_brief(p.employee), employee_id=p.employee_id, gross=p.gross_salary, deductions=p.total_deductions, net=p.net_salary,
                     lines=', '.join(f"{c.component.name} {c.amount:,.0f}" for c in p.components.select_related('component') if c.component_id and c.component.component_type == 'deduction')) for p in q]
        return {'title': 'Payslips', 'columns': EMP_COLS[:3] + [col('gross', 'Gross', 'money'), col('deductions', 'Deductions', 'money'), col('net', 'Net', 'money'), col('lines', 'Deduction lines')], 'rows': rows}
    if metric == 'loans' and scope == 'company':
        LA = M('PayrollManagement.LoanApplication')
        rows = [{'employee_id': l.employee_id, 'code': l.employee.emp_code, 'name': emp_name(l.employee), 'type': l.loan_type.loan_type if l.loan_type_id else '', 'amount': l.amount_requested,
                 'emi': l.emi_amount, 'remaining': l.remaining_balance, 'status': l.status}
                for l in LA.objects.filter(employee_id__in=ids, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')).select_related('employee', 'loan_type')]
        return {'title': 'Loans outstanding', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('type', 'Loan'), col('amount', 'Amount', 'money'), col('emi', 'EMI', 'money'), col('remaining', 'Remaining', 'money'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'recruitment':
        MR = M('RecruitmentManagement.ManpowerRequisition')
        q = MR.objects.filter(status__in=('pending', 'approved'))
        if scope != 'company':
            q = q.filter(requested_by=user)
        rows = [{'document_number': r.document_number, 'position': r.position_title, 'department': r.department.dept_name if r.department_id else '', 'headcount': r.headcount, 'status': r.get_status_display()} for r in q]
        return {'title': 'Open requisitions', 'columns': [col('document_number', 'Number'), col('position', 'Position'), col('department', 'Department'), col('headcount', 'HC', 'number'), col('status', 'Status', 'status')], 'rows': rows}
    return {'title': 'Unknown metric', 'columns': [], 'rows': []}
