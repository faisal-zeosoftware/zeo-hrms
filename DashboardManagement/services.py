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


DEFAULT_END = time(18, 0)
EARLY_GRACE_MIN = 15


class ShiftResolver:
    """The shift an employee works on a day: shift override → shift schedule (pattern) → the shift on the punch.
    Built once for a set of employees and a period (one query per schedule; pattern look-ups are cached)."""

    def __init__(self, emp_ids, d_from, d_to):
        emp_ids = set(emp_ids)
        self.over, self.sched, self._cache = {}, [], {}
        self.book = None
        from django.apps import apps as _apps
        if _apps.is_installed('ShiftPlanner'):  # v1.12.0: the shift planner's resolver (published roster first)
            from ShiftPlanner.resolver import Book
            self.book = Book(d_from, d_to, emp_ids)
        SO, SCH = M('calendars.ShiftOverride'), M('calendars.EmployeeShiftSchedule')
        Emp = M('EmpManagement.emp_master')
        self.emps = {e.id: e for e in Emp.objects.filter(id__in=emp_ids)}
        if SO:
            for o in SO.objects.filter(employee_id__in=emp_ids, date__gte=d_from, date__lte=d_to).select_related('override_shift'):
                self.over[(o.employee_id, o.date)] = o.override_shift
        if SCH:
            for s in SCH.objects.filter(Q(end_date__isnull=True) | Q(end_date__gte=d_from), start_date__lte=d_to).select_related('shift_pattern').order_by('-start_date', '-id'):
                try:
                    ids = set(s.get_assigned_employees().values_list('id', flat=True)) & emp_ids
                except Exception:
                    ids = set()
                if ids:
                    self.sched.append((s, ids))

    def shift(self, emp_id, d, att=None):
        if self.book is not None and self.emps.get(emp_id) is not None:
            r = self.book.get(self.emps[emp_id], d)
            if r is not None:
                if not r['off']:
                    return r['shift']
                return att.shift if att is not None and att.shift_id else None
        if (emp_id, d) in self.over and self.over[(emp_id, d)] is not None:
            return self.over[(emp_id, d)]
        for s, ids in self.sched:
            if emp_id in ids:
                key = (s.id, emp_id, d)
                if key not in self._cache:
                    try:
                        self._cache[key] = s.get_shift_for_date(d, self.emps.get(emp_id))
                    except Exception:
                        self._cache[key] = None
                if self._cache[key] is not None:
                    return self._cache[key]
        return att.shift if att is not None and att.shift_id else None


def _dt(d, t):
    return datetime.combine(d, t)


def minutes_late(att, shift=None):
    """Minutes after the shift start (0 when on time or within the grace minutes)."""
    if not att.check_in_time:
        return 0
    st = getattr(shift, 'start_time', None) or shift_start(att)
    m = int((_dt(att.date, att.check_in_time) - _dt(att.date, st)).total_seconds() // 60)
    return m if m > LATE_GRACE_MIN else 0


def minutes_early(att, shift=None):
    """Minutes before the shift end at check-out (0 when not early); handles shifts that end after midnight."""
    if not (att.check_out_time and att.check_in_time):
        return 0
    st = getattr(shift, 'start_time', None) or shift_start(att)
    en = getattr(shift, 'end_time', None) or getattr(getattr(att, 'shift', None), 'end_time', None) or DEFAULT_END
    end_dt = _dt(att.date, en) + (timedelta(days=1) if en <= st else timedelta())
    out_dt = _dt(att.date, att.check_out_time) + (timedelta(days=1) if att.check_out_time < att.check_in_time else timedelta())
    m = int((end_dt - out_dt).total_seconds() // 60)
    return m if m > EARLY_GRACE_MIN else 0


def weighted_progress(goals):
    goals = list(goals)
    if not goals:
        return 0
    w = sum(float(g.weight or 0) for g in goals)
    if w <= 0:
        return round(sum(float(g.progress_percent or 0) for g in goals) / len(goals), 1)
    return round(sum(float(g.progress_percent or 0) * float(g.weight or 0) for g in goals) / w, 1)


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


def module_label(key):   # v1.17.1: request types of the newer modules (dash171) next to the registry above
    if key in REQ:
        return REQ[key][1]
    from .dash171 import LABELS
    return LABELS.get(key, key)


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
            rows.append({'module': key, 'module_label': label, 'id': r.id, '_m': rm, '_id': r.id, 'document_number': getattr(r, 'document_number', None),
                         'summary': text, 'date': _date_of(r, dfield), 'status': r.status, 'status_key': _status_key(r.status)})
    if emp:   # v1.17.1: the newer modules (expense, corrections, shift requests, profile changes, letters, complaints, asset returns, timesheets)
        from .dash171 import requests_of
        for r in requests_of([emp.id]):
            rows.append(dict({k: r[k] for k in ('module', 'module_label', 'id', '_m', '_id', 'document_number', 'summary', 'date', 'status', 'status_key')},
                             status_label=r['status']))
    rows.sort(key=lambda x: (x['date'] or date.min, x['id']), reverse=True)
    return rows[:limit] if limit else rows


def pending_approvals(user, request=None):
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
            out.append({'module': key, 'module_label': label, 'approval_id': a.id, 'request_id': r.id, '_m': rm, '_id': r.id, 'level': getattr(a, 'level', None),
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
    # v1.17.1: the newer modules – each module's own rule for who decides (dash171.waiting_for)
    from .dash171 import waiting_for
    for r in waiting_for(user, request):
        out.append({k: r[k] for k in ('module', 'module_label', 'approval_id', 'request_id', '_m', '_id', 'level', 'document_number', 'summary', 'date',
                                      'employee_id', 'employee', 'employee_code')})
    out.sort(key=lambda x: (x['date'] or date.min), reverse=False)
    return out


# --------------------------------------------------------------------------- ESS summary
def ess_summary(user, d_from=None, d_to=None):
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
    data['next_leave'] = {'type': nxt.leave_type.name, 'from': nxt.start_date, 'to': nxt.end_date, 'days': nxt.number_of_days, 'status': nxt.status, '_m': nxt._meta.label, '_id': nxt.id} if nxt else None

    # attendance – the dashboard period (v1.16.0; was this month + last 30 days), both attendance sources (attendance_data)
    from . import attendance_data as AD
    if not (d_from and d_to):
        d_from, d_to = today.replace(day=1), today
    rib_from = max(d_from, min(d_to, today) - timedelta(days=61))
    lst = AD.days([emp], d_from, d_to, today).get(emp.id, [])
    c_, _bl = AD.counts(lst)
    worked = [r for r in lst if r['status'] in AD.WORKED]
    by_day = {r['date']: r for r in lst}
    t = by_day.get(today)
    Att = M('calendars.Attendance')
    ta = Att.objects.filter(employee=emp, date=today).first()
    data['attendance'] = {
        'month_label': f"{d_from:%d %b} – {min(d_to, today):%d %b %Y}" if d_from != min(d_to, today) else f"{d_from:%d %b %Y}",
        'working_days': len([r for r in lst if r['status'] not in ('Weekly off', 'Holiday', 'Worked on rest day', 'Not marked yet')]),
        'present': int(c_['Present'] + c_['Missing punch'] + c_['Half day']), 'late': int(c_['late_count']),
        'leave': int(c_['Leave']), 'absent': int(c_['Absent']),
        'avg_hours': round(sum(r['hours'] for r in worked) / len(worked), 2) if worked else 0,
        'today': {'checked_in': ta.check_in_time if ta else (t['in'] or None if t else None), 'checked_out': ta.check_out_time if ta else (t['out'] or None if t else None),
                  # v1.17.0 (audit L6): a weekly off / holiday is not "not checked in yet"
                  'off': (t['status'] in ('Weekly off', 'Holiday')) if t else not is_working_day(today),
                  'off_label': (t['status'] if t else 'Weekly off')},
        'from': d_from, 'to': d_to, 'shown_to': min(d_to, today),   # v1.17.0: dates shown in the pack format (no English months)
    }
    trend = []
    for r in lst:
        if r['date'] < rib_from:
            continue
        st = r['status']
        key = 'present' if st in AD.WORKED else 'leave' if st == 'Leave' else 'off' if st in ('Weekly off', 'Holiday') else 'today' if st == 'Not marked yet' else 'absent'
        trend.append({'date': r['date'], 'hours': r['hours'] or 0, 'late': bool(r['late']), 'working': st not in ('Weekly off', 'Holiday', 'Worked on rest day'),
                      'status': key, 'code': r['code']})
    data['attendance_trend'] = trend
    OT = M('calendars.EmployeeOvertime')
    if OT:
        oq = OT.objects.filter(employee=emp, date__gte=d_from, date__lte=d_to)
        data['overtime'] = {'month_label': data['attendance']['month_label'], 'hours': round(float(oq.aggregate(s=Sum('hours'))['s'] or 0), 2),
                            'approved': round(float(oq.filter(approved=True).aggregate(s=Sum('hours'))['s'] or 0), 2), 'entries': oq.count()}

    # requests (all modules)
    reqs = my_requests(emp)
    data['requests'] = {'recent': reqs[:8], 'counts': dict(Counter(r['status_key'] for r in reqs)),
                        'by_module': [{'module': k, 'label': module_label(k), 'count': c, 'pending': sum(1 for r in reqs if r['module'] == k and r['status_key'] == 'pending')}
                                      for k, c in Counter(r['module'] for r in reqs).items()]}

    # payroll
    PS = M('PayrollManagement.Payslip')
    last = PS.objects.filter(employee=emp, status__in=('Approved', 'approved', 'paid', 'Paid')).select_related('payroll_run').filter(   # v1.13.0: never drafts
        Q(payroll_run__year__lt=d_to.year) | Q(payroll_run__year=d_to.year, payroll_run__month__lte=d_to.month)).order_by('-payroll_run__year', '-payroll_run__month', '-id').first()  # v1.16.0: up to the period end
    data['payslip'] = {'id': last.id, 'period': run_name(last.payroll_run) if last.payroll_run_id else '', 'gross': last.gross_salary, 'deductions': last.total_deductions,
                       'net': last.net_salary, 'status': last.status, '_m': last._meta.label, '_id': last.id} if last else None
    LA = M('PayrollManagement.LoanApplication')
    loans = list(LA.objects.filter(employee=emp, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')))
    data['loans'] = {'count': len(loans), 'outstanding': sum((l.remaining_balance or 0) for l in loans), 'emi': sum((l.emi_amount or 0) for l in loans)}
    ATA = M('PayrollManagement.AirTicketAllocation')
    al = ATA.objects.filter(employee=emp, is_active=True, status='APPROVED').order_by('-id').first() if ATA else None
    data['air_ticket'] = {'amount': al.amount, 'remaining': al.remaining_amount, 'expiry': al.expiry_date} if al else None

    # assets & documents
    AA = M('OrganisationManager.AssetAllocation')
    data['assets'] = [{'name': a.asset.name, 'serial': a.asset.serial_number, 'since': a.assigned_date, '_m': a._meta.label, '_id': a.id} for a in AA.objects.filter(employee=emp, returned_date__isnull=True).select_related('asset')]
    from .documents import current_documents   # v1.17.0 (audit C1): current document per type, renewed copies left out
    docs = []
    for d in current_documents([emp.id], today):
        if d['expiry']:
            days = d['days']
            docs.append({'type': d['type'], 'number': d['number'], 'expiry': d['expiry'], 'days': days,
                         'state': 'expired' if days < 0 else ('expiring' if days <= 60 else 'valid'), '_m': d['_m'], '_id': d['_id']})
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
    if CERT:   # v1.17.1: days left and the record (My view lists them with their expiry state)
        data['certificates'] = [{'title': c.title, 'expiry': c.expiry_date, 'status': c.status, 'days': (c.expiry_date - today).days if c.expiry_date else None,
                                 '_m': c._meta.label, '_id': c.id} for c in CERT.objects.filter(employee=emp).order_by('-issued_on')[:6]]

    data['tasks'] = my_tasks(user, emp, today)
    data['holidays'] = upcoming_holidays(today, emp=emp)
    data['announcements'] = announcements(emp=emp)   # v1.17.1: only the announcements aimed at the employee
    from . import dash171 as D   # v1.17.1: company policies, entitlements, my shift, my expense reports
    pol = D.my_policies(emp, user.pk, today)
    if pol:
        data['policies'] = {'items': pol, 'to_ack': sum(1 for p in pol if p['state'] in ('to_acknowledge', 'overdue')),
                            'overdue': sum(1 for p in pol if p['state'] == 'overdue'), 'acknowledged': sum(1 for p in pol if p['state'] == 'acknowledged')}
    ent = D.entitlements(emp, today)
    if ent:
        data['entitlements'] = ent
    sh = D.my_shift(emp, today)
    if sh:
        data['shift'] = sh
    ex = D.my_expenses(emp)
    if ex:
        data['expenses'] = ex
    data['approvals'] = summarize_approvals(pending_approvals(user))
    return data


def _task_rows(user, emp, today):
    rows = []
    T = M('ProjectManagement.Task')
    if T and emp:
        for t in T.objects.filter(task_members=emp, is_active=True).exclude(status='completed').select_related('project').distinct().order_by('end_date', 'id'):
            rows.append({'kind': 'task', 'kind_label': 'Project task', '_m': t._meta.label, '_id': t.id, 'title': t.title, 'project': str(t.project) if t.project_id else '',
                         'due': t.end_date, 'status': (t.status or '').replace('_', ' '), 'overdue': bool(t.end_date and t.end_date < today),
                         'link': '/main-sidebar/project-options/project-tasks'})
    ACT = M('Chatter.Activity')
    if ACT and user is not None:
        for a in ACT.objects.filter(assigned_to=user, state='open').order_by('due_date', 'id'):
            rows.append({'kind': 'activity', 'kind_label': (a.get_activity_type_display() or 'To-do'), '_m': a.model if a.object_id and str(a.object_id).isdigit() and '.' in (a.model or '') else None,
                         '_id': int(a.object_id) if a.object_id and str(a.object_id).isdigit() else None, 'title': a.summary or a.record_label or 'Activity',
                         'project': a.record_label or '', 'due': a.due_date, 'status': a.state, 'overdue': bool(a.due_date and a.due_date < today),
                         'link': a.page or '/main-sidebar/todo'})
    rows.sort(key=lambda r: (r['due'] is None, r['due'] or date.max))
    return rows


def my_tasks(user, emp, today=None):
    """Open project tasks where the employee is a member, plus open to-dos / meetings planned for the user (Chatter)."""
    today = today or timezone.localdate()
    rows = _task_rows(user, emp, today)
    return {'open': len(rows), 'overdue': sum(1 for r in rows if r['overdue']),
            'due_week': sum(1 for r in rows if r['due'] and today <= r['due'] <= today + timedelta(days=7)), 'items': rows[:6]}


def upcoming_holidays(today, n=5, emp=None):
    H = M('calendars.holiday')
    qs = H.objects.filter(end_date__gte=today)
    if emp is not None:   # v1.15.1: the employee's own holiday calendar (a company can have one per branch / country)
        try:
            from calendars.utils import get_employee_holiday_calendar
            hc = get_employee_holiday_calendar(emp) or getattr(emp, 'holiday_calendar', None)
            if hc is not None:
                qs = qs.filter(calendar=hc)
        except Exception:
            pass
    return [{'name': h.description, 'from': h.start_date, 'to': h.end_date} for h in qs.order_by('start_date', 'id')[:n]]


def announcements(n=4, emp=None):
    A = M('OrganisationManager.Announcement')
    now = timezone.now()
    if emp is not None:   # v1.17.1: an employee sees the announcements aimed at them (branch / department / … / specific employees), live and scheduled
        from OrganisationManager.announcements import announcements_for
        qs = announcements_for(emp)[:n]
    else:
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
def team_summary(user, scope='team', department=None, d_from=None, d_to=None, only=None):
    """v1.16.0: d_from / d_to = the dashboard period (attendance trend, who is out, celebrations, overtime, payroll);
    only = a set of section keys to compute (a card with its own period reloads just its section)."""
    today = timezone.localdate()
    if not (d_from and d_to):
        d_from, d_to = today.replace(day=1), (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    def want(k):
        return only is None or k in only
    admin = is_admin(user)
    if scope == 'company' and not admin:
        scope = 'team'
    emps = list(scope_employees(user, scope, department))
    ids = [e.id for e in emps]
    emp_by_id = {e.id: e for e in emps}
    st = today_status(scope_employees(user, scope, department), today)
    data = {'today': today, 'scope': scope, 'is_admin': admin, 'department': department, 'headcount': len(ids),
            'period': {'from': d_from, 'to': d_to}}
    data['today_status'] = {'present': len(st['present']), 'late': len(st['late']), 'on_leave': len(st['on_leave']), 'not_punched': len(st['not_punched']),
                            'working_day': is_working_day(today)}

    # attendance trend – working days of the period up to today (at most the last 31), % of headcount present
    # v1.16.0: both attendance sources (daily results + attendance entries) through attendance_data
    from . import attendance_data as AD
    days, d = [], min(d_to, today)
    while d >= d_from and len(days) < 31:
        if is_working_day(d):
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    pres, late_by_day, leave_by_day = Counter(), Counter(), Counter()
    if days and want('attendance_trend'):
        for lst in AD.days(emps, days[0], days[-1], today).values():
            for r in lst:
                if r['date'] not in days:
                    continue
                if r['status'] in AD.WORKED:
                    pres[r['date']] += 1
                    if r['late']:
                        late_by_day[r['date']] += 1
                elif r['status'] == 'Leave':
                    leave_by_day[r['date']] += 1
    data['attendance_trend'] = [{'date': x, 'present': pres.get(x, 0), 'late': late_by_day.get(x, 0), 'on_leave': leave_by_day.get(x, 0),
                                 'rate': round(pres.get(x, 0) * 100 / len(ids), 1) if ids else 0} for x in days]

    # who is out – in the period (approved and pending leave)
    out = leave_on(ids, d_from, d_to, statuses=('approved', 'pending')).order_by('start_date')
    data['out_count'] = out.count()
    data['out_next_14'] = [{'employee_id': l.employee_id, 'employee': emp_name(l.employee), 'type': l.leave_type.name if l.leave_type_id else '',
                            'from': l.start_date, 'to': l.end_date, 'days': l.number_of_days, 'status': l.status, '_m': l._meta.label, '_id': l.id} for l in out[:12]]
    # v1.17.0: team leave planner – the next 14 days from today: leave requests (approved / pending) on each day;
    # a day opens drill out_next with from = to = that day (same rows)
    pl = list(leave_on(ids, today, today + timedelta(days=13), statuses=('approved', 'pending')).values('start_date', 'end_date', 'status'))
    data['planner'] = []
    for i in range(14):
        day = today + timedelta(days=i)
        on = [x for x in pl if x['start_date'] <= day <= x['end_date']]
        data['planner'].append({'date': day, 'out': len(on), 'pending': sum(1 for x in on if str(x['status']).lower() == 'pending'),
                                'working': is_working_day(day)})

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
    from . import dash171 as D   # v1.17.1: open requests of the newer modules too
    for r in D.requests_of(ids, open_only=True):
        open_by[r['module']] += 1
    data['team_open_requests'] = [{'module': k, 'label': module_label(k), 'count': v} for k, v in open_by.most_common()]

    # documents expiring (60 days) / expired
    from .documents import current_documents   # v1.17.0 (audit C1): current documents only (= drill 'documents')
    cd = [d for d in current_documents(ids, today) if d['days'] is not None and d['days'] <= 60]
    data['documents'] = {'expired': sum(1 for d in cd if d['days'] < 0), 'expiring': sum(1 for d in cd if d['days'] >= 0),
                         'expiring_30': sum(1 for d in cd if 0 <= d['days'] <= 30)}
    data['probation'] = team_probation(ids, today)   # v1.17.0: probations of the team ending (decisions due)
    if scope == 'team':   # v1.17.1: team members who have not acknowledged the current company policies; open shifts not taken yet
        pr = D.team_policy_rows(emps, today)
        if pr or D._policy_ctx():
            data['policies'] = {'people': len(pr), 'overdue_people': sum(1 for r in pr if r['state'] == 'Overdue')}
        if apps.is_installed('ShiftPlanner'):
            data['open_shifts'] = {'count': len(D.open_shifts(emps, today))}

    # birthdays & work anniversaries in the period (v1.16.0; was: this month)
    data['celebrations'] = celebrations(emps, d_from, d_to)[:8]

    # performance – active cycle status of the team
    GS = M('PerformanceManagement.GoalSheet')
    if GS:
        cyc = M('PerformanceManagement.AppraisalCycle').objects.filter(status__in=('active', 'calibration')).order_by('-period_from').first()
        if cyc:
            order = ['draft', 'submitted', 'approved', 'self_submitted', 'reviewed', 'calibrated', 'acknowledged']
            labels = dict(GS._meta.get_field('status').choices)
            cnt = Counter(GS.objects.filter(cycle=cyc, employee_id__in=ids).values_list('status', flat=True))
            data['performance'] = {'cycle_id': cyc.id, 'cycle': cyc.name, 'rows': [{'key': s, 'label': labels[s], 'count': cnt.get(s, 0)} for s in order]}
            data['goals'] = team_goals(ids, cyc, emp_by_id)
        data['ratings'] = team_ratings(ids)

    # overtime in the period
    data['overtime'] = team_overtime(ids, today, emp_by_id, d_from, d_to)

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
        runs = list(PR.objects.exclude(status='pending').filter(id__in=PS.objects.values('payroll_run_id'))   # v1.15.1: processed runs with payslips
                    .filter(Q(year__gt=d_from.year) | Q(year=d_from.year, month__gte=d_from.month))     # v1.16.0: runs of the period
                    .filter(Q(year__lt=d_to.year) | Q(year=d_to.year, month__lte=d_to.month)).order_by('-year', '-month', '-id')[:12])
        runs.reverse()
        pay = []
        for r in runs:
            agg = PS.objects.filter(payroll_run=r, employee_id__in=ids).aggregate(g=Sum('gross_salary'), d=Sum('total_deductions'), n=Sum('net_salary'))
            pay.append({'run_id': r.id, 'label': f"{date(r.year, r.month, 1):%b %Y}", 'gross': agg['g'] or 0, 'deductions': agg['d'] or 0, 'net': agg['n'] or 0,
                        'from': date(r.year, r.month, 1), 'to': (date(r.year, r.month, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)})
        data['payroll'] = pay
        LA = M('PayrollManagement.LoanApplication')
        data['loans_outstanding'] = LA.objects.filter(employee_id__in=ids, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')).aggregate(s=Sum('remaining_balance'))['s'] or 0
    data['holidays'] = upcoming_holidays(today, 3)
    return data


def run_name(run):
    """v1.17.0 (audit L9): a payroll run's name in the request language (its Arabic name in Arabic when it has one)."""
    try:
        from Translations.i18n import current_lang
        if (current_lang() or 'en') == 'ar' and getattr(run, 'arabic_name', ''):
            return run.arabic_name
    except Exception:
        pass
    return run.name or ''


def probation_rows(ids, today):
    """v1.17.0: open probations (on probation / extended) of these employees with their end date and state."""
    EI = M('EmployeeProfile.EmploymentInfo')
    if EI is None:
        return []
    Emp = M('EmpManagement.emp_master')
    emps = {e.id: e for e in Emp.objects.filter(id__in=list(ids)).select_related('emp_dept_id', 'emp_desgntn_id')}
    rows = []
    for i in EI.objects.filter(employee_id__in=list(emps), probation_status__in=('on_probation', 'extended')):
        e = emps[i.employee_id]
        d = i.probation_end_date
        days = (d - today).days if d else None
        state = 'no_date' if d is None else 'overdue' if days < 0 else 'due_30' if days <= 30 else 'due_90' if days <= 90 else 'later'
        rows.append(dict(emp_brief(e), employee_id=e.id, end=d, days=days, state=state,
                         state_label={'no_date': 'No end date', 'overdue': 'Overdue', 'due_30': 'Ending in 30 days', 'due_90': 'In 31–90 days', 'later': 'Later'}[state],
                         probation=i.get_probation_status_display()))
    rows.sort(key=lambda r: (r['end'] is None, r['end'] or date.max))
    return rows


def team_probation(ids, today):
    rows = probation_rows(ids, today)
    c = Counter(r['state'] for r in rows)
    return {'overdue': c.get('overdue', 0), 'due_30': c.get('due_30', 0), 'due_90': c.get('due_90', 0),
            'next': [{k: r[k] for k in ('employee_id', 'name', 'end', 'days', 'state', 'state_label')} for r in rows if r['state'] in ('overdue', 'due_30', 'due_90')][:5]}


def my_input_rows(user, emp, today=None):
    """v1.17.0 – the user's own items that need their input: overdue tasks and to-dos, goals to set in the active appraisal
    cycle, own documents expired or expiring within 30 days, expense reports still in draft."""
    today = today or timezone.localdate()
    rows = []
    for t in _task_rows(user, emp, today):
        if t['overdue']:
            rows.append({'kind': 'Overdue task', 'title': t['title'], 'date': t['due'], 'link': t.get('link'), '_m': t.get('_m'), '_id': t.get('_id')})
    if emp is not None:
        GS = M('PerformanceManagement.GoalSheet')
        if GS:
            for s in GS.objects.filter(employee=emp, status='draft', cycle__status='active').select_related('cycle'):
                rows.append({'kind': 'Goals to set', 'title': s.cycle.name, 'date': s.cycle.period_to if hasattr(s.cycle, 'period_to') else None,
                             'link': '/main-sidebar/performance-options/goal-setting', '_m': s._meta.label, '_id': s.id})
        from .documents import current_documents   # v1.17.0 (audit C1)
        for d in current_documents([emp.id], today):
            if d['days'] is None or d['days'] > 30:
                continue
            rows.append({'kind': 'Document expired' if d['days'] < 0 else 'Document to renew', 'title': d['type'],
                         'date': d['expiry'], 'link': '/main-sidebar/self-service/documents', '_m': d['_m'], '_id': d['_id']})
        ER = M('ExpenseManagement.ExpenseReport')
        if ER:
            for r in ER.objects.filter(employee_id=emp.id, status='draft'):
                rows.append({'kind': 'Expense report not submitted', 'title': f"{r.number} {r.title}".strip(), 'date': None,
                             'link': '/main-sidebar/expense', '_m': r._meta.label, '_id': r.id})
    rows.sort(key=lambda r: (r['date'] is None, r['date'] or date.max))
    return rows


def payroll_runs_to_approve(user, branches=None):
    """v1.17.0: processed payroll runs (waiting for approval), in the user's branches."""
    PR = M('PayrollManagement.PayrollRun')
    qs = PR.objects.filter(status='processed').order_by('year', 'month', 'id')
    if branches is not None:
        qs = qs.filter(Q(branch__in=branches) | Q(branch__isnull=True)).distinct()
    return list(qs)


def payslip_approvals_for(user):
    PA = M('PayrollManagement.PayslipApproval')
    if PA is None:
        return []
    return list(PA.objects.filter(approver=user, status__iexact='pending').select_related('request__payroll_run', 'employee').order_by('created_at'))


def celebrations(emps, d_from, d_to):
    """Birthdays and work anniversaries that fall in the period (v1.16.0)."""
    out = []
    for e in emps:
        for kind, d0 in (('Birthday', e.emp_date_of_birth), ('anniversary', e.emp_joined_date)):
            if not d0:
                continue
            for y in range(d_from.year, d_to.year + 1):
                try:
                    d = d0.replace(year=y)
                except ValueError:
                    d = d0.replace(year=y, day=28)
                if d_from <= d <= d_to and (kind == 'Birthday' or y > d0.year):
                    out.append({'employee_id': e.id, 'employee': emp_name(e), 'kind': 'Birthday' if kind == 'Birthday' else f'{y - d0.year} yr anniversary',
                                'day': d.day, 'date': d})
    return sorted(out, key=lambda x: x['date'])


def team_overtime(ids, today, emp_by_id=None, d_from=None, d_to=None):
    OT = M('calendars.EmployeeOvertime')
    if not OT:
        return None
    m0 = d_from or today.replace(day=1)
    end = d_to or today
    qs = OT.objects.filter(employee_id__in=ids, date__gte=m0, date__lte=end)
    per, approved, total = defaultdict(float), 0.0, 0.0
    for o in qs.values('employee_id', 'hours', 'approved'):
        h = float(o['hours'] or 0)
        per[o['employee_id']] += h
        total += h
        approved += h if o['approved'] else 0
    if emp_by_id is None:
        emp_by_id = {e.id: e for e in M('EmpManagement.emp_master').objects.filter(id__in=list(per))}
    top = sorted(per.items(), key=lambda x: -x[1])[:5]
    return {'month_label': today.strftime('%B %Y') if not d_from else '', 'hours': round(total, 2), 'approved': round(approved, 2), 'pending': round(total - approved, 2),
            'people': len(per), 'top': [{'employee_id': k, 'employee': emp_name(emp_by_id.get(k)), 'hours': round(v, 2)} for k, v in top]}


def team_goals(ids, cyc, emp_by_id=None):
    GS = M('PerformanceManagement.GoalSheet')
    sheets = list(GS.objects.filter(cycle=cyc, employee_id__in=ids).select_related('employee').prefetch_related('goals'))
    rows = []
    for s in sheets:
        goals = list(s.goals.all())
        rows.append({'sheet_id': s.id, 'employee_id': s.employee_id, 'employee': emp_name(s.employee), 'goals': len(goals), 'progress': weighted_progress(goals),
                     'done': sum(1 for g in goals if g.progress_status == 'done'), 'at_risk': sum(1 for g in goals if g.progress_status in ('at_risk', 'off_track'))})
    rows.sort(key=lambda r: r['progress'])
    with_goals = [r for r in rows if r['goals']]
    return {'cycle_id': cyc.id, 'cycle': cyc.name, 'sheets': len(rows), 'avg_progress': round(sum(r['progress'] for r in with_goals) / len(with_goals), 1) if with_goals else 0,
            'at_risk': sum(r['at_risk'] for r in rows), 'completed_goals': sum(r['done'] for r in rows), 'total_goals': sum(r['goals'] for r in rows), 'rows': rows[:8]}


def team_ratings(ids):
    """Final (or proposed) rating 1–5 of the team in the latest cycle that has ratings."""
    GS = M('PerformanceManagement.GoalSheet')
    if not GS:
        return None
    q = GS.objects.filter(employee_id__in=ids).filter(Q(final_rating__isnull=False) | Q(proposed_rating__isnull=False)).select_related('cycle').order_by('-cycle__period_from')
    first = q.first()
    if not first:
        return {'cycle': None, 'cycle_id': None, 'rated': 0, 'avg': None, 'rows': [{'rating': r, 'count': 0} for r in range(1, 6)]}
    cnt = Counter((s.final_rating or s.proposed_rating) for s in q.filter(cycle=first.cycle))
    rated = sum(cnt.values())
    return {'cycle': first.cycle.name, 'cycle_id': first.cycle_id, 'rated': rated, 'avg': round(sum(k * v for k, v in cnt.items()) / rated, 2) if rated else None,
            'rows': [{'rating': r, 'count': cnt.get(r, 0)} for r in range(1, 6)]}


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
    data['assets'] = [{'name': a.asset.name, 'serial': a.asset.serial_number, 'since': a.assigned_date, '_m': a._meta.label, '_id': a.id} for a in AA.objects.filter(employee=emp, returned_date__isnull=True).select_related('asset')]
    from .documents import current_documents   # v1.17.0 (audit C1)
    data['documents'] = [{'type': d['type'], 'expiry': d['expiry'], 'days': d['days']} for d in current_documents([emp.id], today)]
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
        p = PS.objects.filter(employee=emp).exclude(status='rejected').exclude(payroll_run__status='pending').select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month', '-id').first()   # v1.15.1: no drafts
        data['payslip'] = {'period': run_name(p.payroll_run), 'gross': p.gross_salary, 'deductions': p.total_deductions, 'net': p.net_salary} if p else None
    return data


# --------------------------------------------------------------------------- drill-down lists
def col(key, label, typ='text'):
    return {'key': key, 'label': label, 'type': typ}


EMP_COLS = [col('code', 'Code'), col('name', 'Employee'), col('department', 'Department'), col('designation', 'Designation')]


def drill(user, metric, params, request=None):
    today = timezone.localdate()
    scope = params.get('scope') or 'team'
    if scope == 'company' and not is_admin(user):
        scope = 'team'
    department = params.get('department') or None
    module = params.get('module')
    title, cols, rows = metric, EMP_COLS, []

    if metric == 'my_tasks':
        emp = my_employee(user)
        rows = _task_rows(user, emp, today)
        if params.get('state') == 'overdue':
            rows = [r for r in rows if r['overdue']]
        for r in rows:
            r['flag'] = 'Overdue' if r['overdue'] else ''
        return {'title': 'My tasks and to-dos', 'columns': [col('kind_label', 'Type'), col('title', 'Task'), col('project', 'Project / record'), col('due', 'Due', 'date'),
                                                            col('status', 'Status', 'status'), col('flag', 'Flag', 'status')], 'rows': rows}

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
        if metric == 'my_attendance':   # v1.16.0: every day of the period from both attendance sources, filter by status
            from . import attendance_data as AD
            try:
                d1 = date.fromisoformat(str(params.get('to'))[:10]) if params.get('to') else today
                d0 = date.fromisoformat(str(params.get('from'))[:10]) if params.get('from') else today - timedelta(days=int(params.get('days') or 30))
            except ValueError:
                d0, d1 = today - timedelta(days=30), today
            want_st = [x for x in (params.get('status') or '').split('|') if x]
            rows = []
            for r in reversed(AD.days([emp], d0, d1, today).get(emp.id, [])):
                if want_st and r['status'] not in want_st and not ('late' in want_st and r['late']):
                    continue
                x = {'date': r['date'], 'status': r['status'], 'in': r['in'], 'out': r['out'], 'hours': r['hours'], 'late': 'Late' if r['late'] else '', 'leave': r['leave_type']}
                if r['att_id']:
                    x.update(_m='calendars.Attendance', _id=r['att_id'])
                elif r['day_id']:
                    x.update(_m='AttendancePlus.AttendanceDay', _id=r['day_id'])
                elif r['leave'] is not None:
                    x.update(_m=r['leave']._meta.label, _id=r['leave'].id)
                rows.append(x)
            return {'title': 'My attendance', 'columns': [col('date', 'Date', 'date'), col('status', 'Status', 'status'), col('in', 'In', 'time'), col('out', 'Out', 'time'),
                                                          col('hours', 'Hours', 'number'), col('late', 'Flag', 'status'), col('leave', 'Leave')], 'rows': rows}
        if metric == 'my_payslips':
            PS = M('PayrollManagement.Payslip')
            rows = [{'_m': p._meta.label, '_id': p.id, 'period': run_name(p.payroll_run) if p.payroll_run_id else '', 'gross': p.gross_salary, 'deductions': p.total_deductions, 'net': p.net_salary, 'status': p.status,
                     'lines': ', '.join(f"{c.component.name} {c.amount:,.0f}" for c in p.components.select_related('component') if c.component_id)}
                    for p in PS.objects.filter(employee=emp, status__in=('Approved', 'approved', 'paid', 'Paid')).select_related('payroll_run').order_by('-payroll_run__year', '-payroll_run__month')]  # v1.13.0: approved / paid only
            return {'title': 'My payslips', 'columns': [col('period', 'Payroll'), col('gross', 'Gross', 'money'), col('deductions', 'Deductions', 'money'), col('net', 'Net', 'money'), col('lines', 'Components')], 'rows': rows}
        if metric == 'my_leave':
            LR = M('calendars.employee_leave_request')
            qs = LR.objects.filter(employee=emp).select_related('leave_type').order_by('-start_date')
            if params.get('leave_type'):
                qs = qs.filter(leave_type_id=params['leave_type'])
            rows = [{'_m': r._meta.label, '_id': r.id, 'document_number': r.document_number, 'type': r.leave_type.name, 'from': r.start_date, 'to': r.end_date, 'days': r.number_of_days, 'status': r.status} for r in qs]
            return {'title': 'My leave', 'columns': [col('document_number', 'Number'), col('type', 'Type'), col('from', 'From', 'date'), col('to', 'To', 'date'), col('days', 'Days', 'number'), col('status', 'Status', 'status')], 'rows': rows}
        if metric == 'my_documents':
            return {'title': 'My documents', 'columns': [col('type', 'Document'), col('number', 'Number'), col('expiry', 'Expiry', 'date'), col('days', 'Days left', 'number'), col('state', 'Status', 'status')],
                    'rows': ess_summary(user).get('documents', [])}

    if metric == 'my_leave_ledger':   # v1.17.0 (audit M6): a balance line opens the statement of that leave type
        emp = my_employee(user)
        rows = []
        if emp is not None:
            lt = params.get('leave_type')
            LT = M('calendars.leave_type')
            name = LT.objects.filter(id=lt).values_list('name', flat=True).first() or ''
            try:
                from LeavePolicy.models import LeaveLedger
                bal = 0.0
                for l in LeaveLedger.objects.filter(employee_id=emp.id, leave_type_id=lt).order_by('date', 'id'):
                    bal += float(l.days or 0)
                    r = {'date': l.date, 'entry': l.get_kind_display(), 'days': float(l.days or 0), 'balance': round(bal, 2), 'note': l.note or ''}
                    if l.ref_model and l.ref_id:
                        r.update({'_m': l.ref_model, '_id': l.ref_id})
                    rows.append(r)
            except Exception:
                pass
            B = M('calendars.emp_leave_balance')
            now = B.objects.filter(employee_id=emp.id, leave_type_id=lt).values_list('balance', flat=True).first()
            if now is not None:
                rows.append({'date': today, 'entry': 'Balance now', 'days': None, 'balance': float(now), 'note': ''})
        return {'title': 'Leave statement', 'subtitle': name if emp else '', 'columns': [col('date', 'Date', 'date'), col('entry', 'Entry', 'status'), col('days', 'Days', 'number'),
                                                                   col('balance', 'Balance after', 'number'), col('note', 'Note')], 'rows': rows}
    if metric == 'my_policies':   # v1.17.1: company policies that apply to me (state=open: still to acknowledge)
        from .dash171 import my_policies
        emp = my_employee(user)
        rows = my_policies(emp, user.pk, today) if emp else []
        if params.get('state') == 'open':
            rows = [r for r in rows if r['state'] in ('to_acknowledge', 'overdue')]
        return {'title': 'Company policies', 'columns': [col('title', 'Policy'), col('version', 'Version', 'number'), col('date', 'Version date', 'date'),
                                                         col('due', 'Acknowledge by', 'date'), col('state_label', 'Status', 'status')], 'rows': rows}
    if metric == 'my_expenses':   # v1.17.1: my expense reports by status
        from .dash171 import my_expense_rows
        rows = my_expense_rows(my_employee(user), params.get('status') or None)
        return {'title': 'My expense reports', 'columns': [col('number', 'Report'), col('title', 'Title'), col('date', 'Date', 'date'), col('total', 'Total', 'money'),
                                                           col('reimburse', 'To reimburse', 'money'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'my_input':   # v1.17.0: "Needs your action" – own items that need input
        rows = my_input_rows(user, my_employee(user), today)
        return {'title': 'Your items needing input', 'columns': [col('kind', 'What'), col('title', 'Item'), col('date', 'Date', 'date')], 'rows': rows}
    if metric == 'payslip_approvals':   # v1.17.0
        rows = [{'_m': 'PayrollManagement.Payslip', '_id': a.request_id, 'run': a.request.payroll_run.name if a.request.payroll_run_id else '',
                 'employee': emp_name(a.employee), 'code': a.employee.emp_code, 'level': a.level, 'net': a.request.net_salary,
                 'date': timezone.localtime(a.created_at).date() if a.created_at else None} for a in payslip_approvals_for(user)]
        return {'title': 'Payslips waiting for your approval', 'columns': [col('run', 'Payroll run'), col('code', 'Code'), col('employee', 'Employee'),
                                                                          col('level', 'Level', 'number'), col('net', 'Net', 'money'), col('date', 'Since', 'date')], 'rows': rows}
    if metric == 'payroll_to_approve':   # v1.17.0
        c_ = None
        if request is not None:
            from AccessControl.access import ctx as _ctx
            c_ = _ctx(request)
            if not (c_.admin or {'view_payrollrun', 'change_payrollrun'} & c_.codes):
                return {'title': 'Payroll runs to approve', 'columns': [], 'rows': []}
        rows = []
        PS = M('PayrollManagement.Payslip')
        for r in payroll_runs_to_approve(user, c_.branches if c_ is not None else None):
            rows.append({'_m': r._meta.label, '_id': r.id, 'run': r.name or f"{date(r.year, r.month, 1):%B %Y}", 'period': date(r.year, r.month, 1),
                         'payslips': PS.objects.filter(payroll_run=r).count(), 'status': r.get_status_display()})
        return {'title': 'Payroll runs to approve', 'columns': [col('run', 'Payroll run'), col('period', 'Month', 'month'), col('payslips', 'Payslips', 'number'),
                                                                col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'approvals':
        items = pending_approvals(user, request)
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
    def _pd(k, default):
        try:
            return date.fromisoformat(str(params.get(k))[:10]) if params.get(k) else default
        except ValueError:
            return default
    p_from, p_to = _pd('from', today.replace(day=1)), _pd('to', today)
    if metric == 'attendance_day':   # v1.16.0: the same day rows as the trend bar (both attendance sources)
        from . import attendance_data as AD
        d = _pd('date', today)
        rows = []
        for e in emps:
            for r in AD.days([e], d, d, today).get(e.id, []):
                if r['status'] in AD.WORKED and (params.get('flag') != 'late' or r['late']):
                    x = dict(emp_brief(e), employee_id=e.id, **{'in': r['in'], 'out': r['out']}, late='Late' if r['late'] else '', status=r['status'])
                    if r['att_id']:
                        x.update(_m='calendars.Attendance', _id=r['att_id'])
                    elif r['day_id']:
                        x.update(_m='AttendancePlus.AttendanceDay', _id=r['day_id'])
                    rows.append(x)
        return {'title': f"Present · {d:%d/%m/%Y}", 'columns': EMP_COLS + [col('in', 'In', 'time'), col('out', 'Out', 'time'), col('status', 'Status', 'status'), col('late', 'Flag', 'status')],
                'rows': rows}
    if metric in ('present_today', 'late_today', 'not_punched_today', 'on_leave_today'):
        d = date.fromisoformat(params['date']) if params.get('date') else today
        st = today_status(emps, d)
        if metric == 'on_leave_today':
            rows = [dict(emp_brief(l.employee), employee_id=l.employee_id, type=l.leave_type.name, to=l.end_date, _m=l._meta.label, _id=l.id) for l in st['leave_rows'].values()]
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
        a_, b_ = (p_from, p_to) if params.get('from') else (today, today + timedelta(days=int(params.get('days') or 30)))
        out = leave_on(ids, a_, b_, statuses=('approved', 'pending')).order_by('start_date')
        rows = [dict(emp_brief(l.employee), employee_id=l.employee_id, type=l.leave_type.name, **{'from': l.start_date}, to=l.end_date, days=l.number_of_days, status=l.status, _m=l._meta.label, _id=l.id) for l in out]
        return {'title': 'Leave in the period' if params.get('from') else 'Leave – next 30 days', 'columns': EMP_COLS[:2] + [col('type', 'Leave'), col('from', 'From', 'date'), col('to', 'To', 'date'), col('days', 'Days', 'number'), col('status', 'Status', 'status')], 'rows': rows}
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
                rows.append(dict(employee_id=r.employee_id, code=r.employee.emp_code, name=emp_name(r.employee), module_label=label, _m=rm, _id=r.id,
                                 document_number=getattr(r, 'document_number', None), summary=text, date=_date_of(r, dfield), status=r.status))
        from .dash171 import requests_of   # v1.17.1: the newer modules
        for r in requests_of(ids, open_only=True):
            if module and r['module'] != module:
                continue
            rows.append(dict(employee_id=r['employee_id'], code=r['employee_code'], name=r['employee'], module_label=r['module_label'], _m=r['_m'], _id=r['_id'],
                             document_number=r['document_number'], summary=r['summary'], date=r['date'], status=r['status']))
        return {'title': 'Open requests from the team', 'columns': [col('module_label', 'Type'), col('document_number', 'Number'), col('name', 'Employee'), col('summary', 'Details'), col('date', 'Date', 'date'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'probation':   # v1.17.0: the team's probations ending
        want = [x for x in (params.get('state') or 'overdue|due_30|due_90').split('|') if x]
        rows = [r for r in probation_rows(ids, today) if r['state'] in want]
        return {'title': 'Probation ending', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('department', 'Department'), col('end', 'Probation ends', 'date'),
                                                         col('days', 'Days left', 'number'), col('state_label', 'Status', 'status')], 'rows': rows}
    if metric == 'documents':
        DOC = M('EmpManagement.Emp_Documents')
        try:   # v1.17.0: ?days=30 (strip "Needs your action"); default 60 as before
            win = max(0, min(366, int(params.get('days') or 60)))
        except (TypeError, ValueError):
            win = 60
        from .documents import current_documents   # v1.17.0 (audit C1): current documents only, one per employee + type
        Emp = M('EmpManagement.emp_master')
        emps = {e.id: e for e in Emp.objects.filter(id__in=list(ids))}
        q = [d for d in current_documents(ids, today) if d['days'] is not None and d['days'] <= win]
        if params.get('state') == 'expired':
            q = [d for d in q if d['days'] < 0]
        elif params.get('state') == 'expiring':
            q = [d for d in q if d['days'] >= 0]
        rows = [{'_m': d['_m'], '_id': d['_id'], 'employee_id': d['employee_id'], 'code': emps[d['employee_id']].emp_code, 'name': emp_name(emps[d['employee_id']]),
                 'type': d['type'], 'number': d['number'], 'expiry': d['expiry'], 'days': d['days'],
                 'state': 'expired' if d['days'] < 0 else 'expiring'} for d in q]
        return {'title': 'Documents expired / expiring in {0} days'.replace('{0}', str(win)), 'columns': [col('code', 'Code'), col('name', 'Employee'), col('type', 'Document'), col('number', 'Number'), col('expiry', 'Expiry', 'date'), col('days', 'Days', 'number'), col('state', 'Status', 'status')], 'rows': rows}
    if metric == 'goal_status':
        GS = M('PerformanceManagement.GoalSheet')
        q = GS.objects.filter(employee_id__in=ids, cycle_id=params.get('cycle')).select_related('employee', 'cycle')
        if params.get('status'):
            q = q.filter(status=params['status'])
        rows = [{'_m': s._meta.label, '_id': s.id, 'employee_id': s.employee_id, 'code': s.employee.emp_code, 'name': emp_name(s.employee), 'status': s.get_status_display(), 'self_score': s.self_score,
                 'manager_score': s.manager_score, 'final_rating': s.final_rating} for s in q]
        return {'title': 'Goal sheets', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('status', 'Stage', 'status'), col('self_score', 'Self', 'number'), col('manager_score', 'Manager', 'number'), col('final_rating', 'Final', 'number')], 'rows': rows}
    if metric == 'trainings':
        NOM = M('LearningManagement.Nomination')
        q = NOM.objects.filter(employee_id__in=ids, session__start_date__gte=today).exclude(seat_status='cancelled').select_related('employee', 'session__course').order_by('session__start_date')
        rows = [{'_m': n._meta.label, '_id': n.id, 'employee_id': n.employee_id, 'code': n.employee.emp_code, 'name': emp_name(n.employee), 'course': n.session.course.title, 'date': n.session.start_date, 'seat': n.seat_status} for n in q]
        return {'title': 'Upcoming trainings', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('course', 'Course'), col('date', 'Date', 'date'), col('seat', 'Seat', 'status')], 'rows': rows}
    if metric == 'team_policies':   # v1.17.1: team members who have not acknowledged the current company policies
        from .dash171 import team_policy_rows
        rows = team_policy_rows(emps, today)
        if params.get('state') == 'overdue':
            rows = [r for r in rows if r['state'] == 'Overdue']
        return {'title': 'Policies not acknowledged', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('policies', 'Policies'),
                                                                  col('count', 'Policies', 'number'), col('state', 'Status', 'status')], 'rows': rows}
    if metric == 'open_shifts':   # v1.17.1: open shifts of the team's branches / departments not taken yet
        from .dash171 import open_shifts
        return {'title': 'Open shifts not taken', 'columns': [col('date', 'Date', 'date'), col('shift', 'Shift'), col('branch', 'Branch'), col('department', 'Department'),
                                                              col('left', 'Places left', 'number'), col('claims', 'Claims waiting', 'number')], 'rows': open_shifts(emps, today)}
    if metric == 'certificates':
        CERT = M('LearningManagement.Certificate')
        rows = [{'_m': c._meta.label, '_id': c.id, 'employee_id': c.employee_id, 'code': c.employee.emp_code, 'name': emp_name(c.employee), 'title': c.title, 'expiry': c.expiry_date, 'status': c.status}
                for c in CERT.objects.filter(employee_id__in=ids, expiry_date__isnull=False, expiry_date__lte=today + timedelta(days=60)).select_related('employee') if c.status in ('expiring', 'expired')]
        return {'title': 'Certificates expiring / expired', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('title', 'Certificate'), col('expiry', 'Expiry', 'date'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'assets':
        AA = M('OrganisationManager.AssetAllocation')
        rows = [{'_m': a._meta.label, '_id': a.id, 'employee_id': a.employee_id, 'code': a.employee.emp_code, 'name': emp_name(a.employee), 'asset': a.asset.name, 'serial': a.asset.serial_number, 'since': a.assigned_date}
                for a in AA.objects.filter(employee_id__in=ids, returned_date__isnull=True).select_related('employee', 'asset')]
        return {'title': 'Assets with the team', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('asset', 'Asset'), col('serial', 'Serial'), col('since', 'Since', 'date')], 'rows': rows}
    if metric == 'celebrations':
        return {'title': 'Birthdays & anniversaries', 'columns': [col('employee', 'Employee'), col('kind', 'Occasion'), col('date', 'Date', 'date')],
                'rows': celebrations(emps, p_from, p_to)}
    if metric == 'payroll_run' and scope == 'company':
        PS = M('PayrollManagement.Payslip')
        q = PS.objects.filter(payroll_run_id=params.get('run'), employee_id__in=ids).select_related('employee__emp_dept_id', 'employee__emp_desgntn_id').order_by('employee__emp_code')
        rows = [dict(emp_brief(p.employee), employee_id=p.employee_id, _m=p._meta.label, _id=p.id, gross=p.gross_salary, deductions=p.total_deductions, net=p.net_salary,
                     lines=', '.join(f"{c.component.name} {c.amount:,.0f}" for c in p.components.select_related('component') if c.component_id and c.component.component_type == 'deduction')) for p in q]
        return {'title': 'Payslips', 'columns': EMP_COLS[:3] + [col('gross', 'Gross', 'money'), col('deductions', 'Deductions', 'money'), col('net', 'Net', 'money'), col('lines', 'Deduction lines')], 'rows': rows}
    if metric == 'loans' and scope == 'company':
        LA = M('PayrollManagement.LoanApplication')
        rows = [{'_m': l._meta.label, '_id': l.id, 'employee_id': l.employee_id, 'code': l.employee.emp_code, 'name': emp_name(l.employee), 'type': l.loan_type.loan_type if l.loan_type_id else '', 'amount': l.amount_requested,
                 'emi': l.emi_amount, 'remaining': l.remaining_balance, 'status': l.status}
                for l in LA.objects.filter(employee_id__in=ids, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')).select_related('employee', 'loan_type')]
        return {'title': 'Loans outstanding', 'columns': [col('code', 'Code'), col('name', 'Employee'), col('type', 'Loan'), col('amount', 'Amount', 'money'), col('emi', 'EMI', 'money'), col('remaining', 'Remaining', 'money'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'recruitment':
        MR = M('RecruitmentManagement.ManpowerRequisition')
        q = MR.objects.filter(status__in=('pending', 'approved'))
        if scope != 'company':
            q = q.filter(requested_by=user)
        rows = [{'_m': r._meta.label, '_id': r.id, 'document_number': r.document_number, 'position': r.position_title, 'department': r.department.dept_name if r.department_id else '', 'headcount': r.headcount, 'status': r.get_status_display()} for r in q]
        return {'title': 'Open requisitions', 'columns': [col('document_number', 'Number'), col('position', 'Position'), col('department', 'Department'), col('headcount', 'HC', 'number'), col('status', 'Status', 'status')], 'rows': rows}
    if metric == 'team_overtime':
        OT = M('calendars.EmployeeOvertime')
        q = OT.objects.filter(employee_id__in=ids, date__gte=p_from, date__lte=p_to).select_related('employee').order_by('-date')
        if params.get('employee'):
            q = q.filter(employee_id=params['employee'])
        rows = [{'_m': o._meta.label, '_id': o.id, 'employee_id': o.employee_id, 'code': o.employee.emp_code, 'name': emp_name(o.employee), 'date': o.date,
                 'type': o.get_ot_type_display(), 'hours': float(o.hours or 0), 'approved': 'Approved' if o.approved else 'Pending'} for o in q]
        return {'title': f"Overtime · {p_from:%d %b} – {p_to:%d %b %Y}", 'columns': [col('code', 'Code'), col('name', 'Employee'), col('date', 'Date', 'date'), col('type', 'Type'),
                                                                 col('hours', 'Hours', 'number'), col('approved', 'Status', 'status')], 'rows': rows}
    if metric == 'goal_progress':
        AC = M('PerformanceManagement.AppraisalCycle')
        cyc = AC.objects.filter(pk=params.get('cycle')).first() if params.get('cycle') else AC.objects.filter(status__in=('active', 'calibration')).order_by('-period_from').first()
        if not cyc:
            return {'title': 'Goal progress', 'columns': [], 'rows': []}
        g = team_goals(ids, cyc)
        GS = M('PerformanceManagement.GoalSheet')
        full = []
        for s in GS.objects.filter(cycle=cyc, employee_id__in=ids).select_related('employee').prefetch_related('goals'):
            goals = list(s.goals.all())
            full.append({'_m': s._meta.label, '_id': s.id, 'employee_id': s.employee_id, 'code': s.employee.emp_code, 'name': emp_name(s.employee), 'goals': len(goals),
                         'progress': weighted_progress(goals), 'done': sum(1 for x in goals if x.progress_status == 'done'),
                         'at_risk': sum(1 for x in goals if x.progress_status in ('at_risk', 'off_track')), 'status': s.get_status_display()})
        full.sort(key=lambda r: r['progress'])
        return {'title': f"Goal progress · {g['cycle']}", 'columns': [col('code', 'Code'), col('name', 'Employee'), col('goals', 'Goals', 'number'), col('progress', 'Progress %', 'number'),
                                                                      col('done', 'Done', 'number'), col('at_risk', 'At risk', 'number'), col('status', 'Stage', 'status')], 'rows': full}
    if metric == 'rating_dist':
        GS = M('PerformanceManagement.GoalSheet')
        q = GS.objects.filter(employee_id__in=ids, cycle_id=params.get('cycle')).select_related('employee', 'cycle')
        if params.get('rating'):
            r = int(params['rating'])
            q = q.filter(Q(final_rating=r) | Q(final_rating__isnull=True, proposed_rating=r))
        else:
            q = q.filter(Q(final_rating__isnull=False) | Q(proposed_rating__isnull=False))
        rows = [{'_m': s._meta.label, '_id': s.id, 'employee_id': s.employee_id, 'code': s.employee.emp_code, 'name': emp_name(s.employee), 'rating': s.final_rating or s.proposed_rating,
                 'final': 'Final' if s.final_rating else 'Proposed', 'manager_score': s.manager_score, 'status': s.get_status_display()} for s in q]
        return {'title': 'Performance ratings' + (f" · {params['rating']}" if params.get('rating') else ''),
                'columns': [col('code', 'Code'), col('name', 'Employee'), col('rating', 'Rating', 'number'), col('final', 'Kind'), col('manager_score', 'Manager score', 'number'), col('status', 'Stage', 'status')],
                'rows': rows}
    return {'title': 'Unknown metric', 'columns': [], 'rows': []}
