"""
v1.17.0 (dash17) – two report-centre lists behind the HR operations dashboard (one home for every figure):

  requests-pending   Approvals queue: every request of every module still waiting for approval, oldest first
                     (leave, late in / early out, general, document, asset, loan, advance salary, air ticket, resignation),
                     with the step it is waiting at and who has to approve it.
  expiry-all         Documents and visas: employee documents AND the identity file (passport, visa, Emirates ID, labour card,
                     work permit, driving licence, medical insurance) of active employees, with days left and one expiry
                     bucket: Expired / Within 30 days / In 31–60 days / In 61–90 days / Later / No expiry date.

The dashboard counts its figures FROM these functions (overview.Calc), so a figure is always the number of rows its click shows.
"""
from django.apps import apps
from django.utils import timezone

from .reports import EMP_COLS, emp_cols, src

WAIT_STATUSES = r'^(pending)$'


def _m(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _type_label(key):
    from .services import REQ
    return REQ[key][1] if key in REQ else key


def r_requests_pending(run):
    from .services import REQUESTS, _date_of
    today = timezone.localdate()
    rows = []
    for key, label, rm, am, fk, dfield, summ in REQUESTS:
        RM, AM = _m(rm), _m(am)
        if RM is None:
            continue
        emp_f = 'employee'
        qs = RM.objects.filter(run.emp_q(emp_f)).filter(status__iregex=WAIT_STATUSES).select_related(
            'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id')
        steps = {}
        if AM is not None:
            try:
                for a in AM.objects.filter(**{f'{fk}__in': qs.values('id')}).filter(status__iexact='pending').select_related('approver').order_by('level', 'id'):
                    steps.setdefault(getattr(a, f'{fk}_id'), a)
            except Exception:   # an approval model without level / approver
                steps = {}
        for r in qs:
            d = _date_of(r, dfield)
            try:
                text = summ(r)
            except Exception:
                text = ''
            st = steps.get(r.id)
            waiting = (today - d).days if d else ''
            rows.append({**emp_cols(r.employee), 'type': label, 'number': getattr(r, 'document_number', None) or '', 'details': text,
                         'requested': d, 'waiting': waiting, 'over_7': 'Yes' if waiting != '' and waiting > 7 else 'No',
                         'step': getattr(st, 'level', '') if st is not None else '',
                         'approver': (st.approver.username if getattr(st, 'approver_id', None) else '') if st is not None else '',
                         'status': 'Pending', **src(r)})
    try:   # v1.17.1: the newer modules (expense, corrections, shift requests, profile changes, letters, complaints, transfers, returns, timesheets)
        from .dash171 import queue_rows
        rows += queue_rows(run)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('approvals queue: newer modules failed')
    rows.sort(key=lambda x: (x['requested'] is None, x['requested'] or today))
    return [('type', 'Request type', 'text'), ('number', 'Number', 'text')] + EMP_COLS + [
        ('details', 'Details', 'text'), ('requested', 'Requested on', 'date'), ('waiting', 'Days waiting', 'number'),
        ('over_7', 'Waiting more than 7 days', 'text'), ('step', 'Approval level', 'number'), ('approver', 'Waiting for', 'text'),
        ('status', 'Status', 'text')], rows, {'filters': ['type', 'branch', 'department', 'over_7']}


def bucket(days):
    if days == '':
        return 'No expiry date'
    if days < 0:
        return 'Expired'
    if days <= 30:
        return 'Within 30 days'
    if days <= 60:
        return 'In 31–60 days'
    if days <= 90:
        return 'In 61–90 days'
    return 'Later'


IDENTITY = [('Passport', 'passport_no', 'passport_expiry_date'), ('Visa', 'visa_number', 'visa_expiry_date'),
            ('Emirates ID', 'emirates_id', 'emirates_id_expiry_date'), ('Labour card', 'labour_card_no', 'labour_card_expiry_date'),
            ('Work permit', 'work_permit_no', 'work_permit_expiry_date'), ('Driving licence', 'driving_licence_no', 'driving_licence_expiry_date'),
            ('Medical insurance', 'insurance_card_no', 'insurance_expiry_date')]


def r_expiry_all(run):
    """v1.17.0 (audit C1): the CURRENT document of each employee + type (renewed / superseded copies left out, the employee
    document or the identity file – whichever expires last), so a figure is never inflated by old copies or counted twice."""
    from .documents import current_documents
    today = timezone.localdate()
    emps = {e.pk: e for e in run.employees}
    rows = []
    for d in current_documents(emps.keys(), today):
        e = emps[d['employee_id']]
        days = d['days'] if d['days'] is not None else ''
        rows.append({**emp_cols(e), 'source': d['source'], 'document': d['type'], 'number': d['number'], 'expiry': d['expiry'],
                     'days_left': days, 'status': bucket(days), '_m': d['_m'], '_id': d['_id']})
    rows.sort(key=lambda r: (r['days_left'] == '', r['days_left'] if r['days_left'] != '' else 0))
    return EMP_COLS + [('source', 'Source', 'text'), ('document', 'Document', 'text'), ('number', 'Number', 'text'), ('expiry', 'Expiry', 'date'),
                       ('days_left', 'Days left', 'number'), ('status', 'Expiry', 'text')], rows, {'filters': ['status', 'document', 'branch', 'department']}


def r_headcount_asof(run):
    """v1.17.0 (audit M4/M5): the employees on the payroll at the end of the period (the "to" date) – the people behind a
    headcount point of the Management chart; column "National" for the nationals share (country pack)."""
    from .reports import _left, is_national
    d = run.date_to
    left = _left(run)
    rows = []
    for e in run.all_employees.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id'):
        if e.emp_joined_date and e.emp_joined_date > d:
            continue
        lv = left.get(e.id)
        if lv and lv[0] and lv[0] <= d:
            continue
        if not e.is_active and not lv:
            continue
        rows.append({**emp_cols(e), 'nationality': str(getattr(e, 'emp_nationality', '') or ''), 'national': 'Yes' if is_national(e) else 'No',
                     'joined': e.emp_joined_date, 'last_day': lv[0] if lv else None, **src(e)})
    rows.sort(key=lambda r: r['employee'])
    return EMP_COLS + [('nationality', 'Nationality', 'text'), ('national', 'National', 'text'), ('joined', 'Joining date', 'date'),
                       ('last_day', 'Last working day', 'date')], rows, {'filters': ['branch', 'department', 'national']}


def r_requisitions(run):
    """v1.17.0 (audit M1): manpower requisitions with status; "waiting" = submitted / in approval (the ones to approve)."""
    MR = _m('RecruitmentManagement.ManpowerRequisition')
    if MR is None:
        return [], [], {}
    qs = MR.objects.select_related('branch', 'department', 'designation')
    if getattr(run, 'branches', None):
        from django.db.models import Q
        qs = qs.filter(Q(branch_id__in=run.branches) | Q(branch__isnull=True))
    rows = []
    for r in qs.order_by('-id'):
        rows.append({'number': r.document_number, 'position': r.position_title, 'branch': r.branch.branch_name if r.branch_id else '',
                     'department': str(r.department or ''), 'headcount': r.headcount, 'status': r.get_status_display(),
                     'waiting': 'Yes' if r.status in ('submitted', 'pending', 'in_approval') else 'No', 'target': r.target_joining_date, **src(r)})
    return [('number', 'Number', 'text'), ('position', 'Position', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'),
            ('headcount', 'Headcount', 'number'), ('status', 'Status', 'text'), ('waiting', 'Waiting for approval', 'text'), ('target', 'Target joining date', 'date')], \
        rows, {'filters': ['status', 'waiting', 'department']}


def r_sessions(run):
    """v1.17.0 (audit M2): training sessions with dates and status; "upcoming" = starting today or later."""
    S = _m('LearningManagement.TrainingSession')
    if S is None:
        return [], [], {}
    today = timezone.localdate()
    rows = []
    for x in S.objects.select_related('course').order_by('start_date'):
        rows.append({'session': x.code, 'course': x.course.title if x.course_id else '', 'start': x.start_date, 'end': x.end_date,
                     'venue': x.venue or '', 'seats': x.seats, 'status': x.get_status_display(), 'upcoming': 'Yes' if x.start_date >= today else 'No', **src(x)})
    return [('session', 'Session', 'text'), ('course', 'Course', 'text'), ('start', 'Starts', 'date'), ('end', 'Ends', 'date'), ('venue', 'Venue', 'text'),
            ('seats', 'Seats', 'number'), ('status', 'Status', 'text'), ('upcoming', 'Coming up', 'text')], rows, {'filters': ['status', 'upcoming']}


REPORTS = {
    'requests-pending': ('Approvals queue', 'Requests & exits', 'Every request still waiting for approval in all modules, oldest first, with the approval level and who it waits for.',
                         r_requests_pending, None, ['view_generalrequest', 'view_employee_leave_request', 'view_leaveapproval', 'view_loanapplication',
                                                    'view_advancesalaryrequest', 'view_airticketrequest', 'view_assetrequest', 'view_documentrequest',
                                                    'view_employeeresignation', 'view_lateinearlyoutrequest', 'view_emp_master']),
    'expiry-all': ('Documents and visas expiry', 'People', 'Employee documents and the identity file (passport, visa, Emirates ID, labour card, work permit, licence, insurance) of active employees, with days left.',
                   r_expiry_all, None, ['view_doc_report', 'view_emp_documents', 'view_emp_master']),
    'headcount-asof': ('Employees on a date', 'People', 'Employees on the payroll at the end of the period, with nationality and the nationals flag.',
                       r_headcount_asof, None, ['view_report', 'view_emp_master']),
    'requisitions': ('Manpower requisitions', 'Talent & projects', 'Manpower requisitions with status; the ones waiting for approval first.',
                     r_requisitions, None, ['view_manpowerrequisition']),
    'sessions': ('Training sessions', 'Talent & projects', 'Training sessions with dates, venue, seats and status.', r_sessions, None,
                 ['view_trainingsession', 'view_course']),
}
REPORT_MODELS = {
    'requests-pending': {'calendars.employee_leave_request', 'EmpManagement.GeneralRequest', 'EmpManagement.DocumentRequest', 'PayrollManagement.LoanApplication'},
    'expiry-all': {'EmpManagement.Emp_Documents'},
    'headcount-asof': {'EmpManagement.emp_master'}, 'requisitions': {'RecruitmentManagement.ManpowerRequisition'},
    'sessions': {'LearningManagement.TrainingSession'},
}

# reports the Management (CEO) dashboard opens: users with the CEO dashboard right may run them (no dead links)
CEO_REPORTS = ('headcount-asof', 'payroll-register', 'payroll-cost', 'department-cost', 'gratuity', 'leave-liability', 'overtime', 'overtime-pay', 'attendance-summary',
               'turnover-trend', 'joiners-leavers', 'employees', 'recruitment', 'time-to-hire', 'candidates', 'source-effectiveness', 'location', 'headcount')


def register(registry, models):
    registry.update(REPORTS)
    models.update(REPORT_MODELS)
    for k in CEO_REPORTS:
        spec = registry.get(k)
        if spec and 'view_ceo_dashboard' not in spec[5]:
            registry[k] = tuple(spec[:5]) + (list(spec[5]) + ['view_ceo_dashboard'],) + tuple(spec[6:])
