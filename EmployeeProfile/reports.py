"""
v1.13.0 – employee master reports for the report centre (DashboardManagement/reports.py conventions):
`def r_x(run): return columns, rows`; rows carry the five employee columns and src(employee) for the drill-down.
"""
from datetime import date

from . import services as S
from .models import EmergencyContact, EmployeeIdentity, EmploymentInfo


def _h():
    from DashboardManagement import reports as R
    return R


def _status(days):
    if days == '':
        return 'No expiry date'
    return 'Expired' if days < 0 else 'Expiring in 30 days' if days <= 30 else 'Expiring in 90 days' if days <= 90 else 'Valid'


def r_identity_expiry(run):
    """Passport / visa / Emirates ID / labour card / work permit / insurance of every employee with days left."""
    R = _h()
    emps = {e.pk: e for e in run.employees}
    rows = []
    kinds = [('Passport', 'passport_no', 'passport_expiry_date'), ('Visa', 'visa_number', 'visa_expiry_date'),
             ('Emirates ID', 'emirates_id', 'emirates_id_expiry_date'), ('Labour card', 'labour_card_no', 'labour_card_expiry_date'),
             ('Work permit', 'work_permit_no', 'work_permit_expiry_date'), ('Driving licence', 'driving_licence_no', 'driving_licence_expiry_date'), ('Medical insurance', 'insurance_card_no', 'insurance_expiry_date')]
    for i in EmployeeIdentity.objects.filter(employee_id__in=list(emps)):
        e = emps[i.employee_id]
        for label, nf, ef in kinds:
            num, exp = getattr(i, nf), getattr(i, ef)
            if not num and not exp:
                continue
            if run.date_to and exp and exp > run.date_to and run.date_to.year < 2999:
                continue
            days = (exp - run.today).days if exp else ''
            rows.append({**R.emp_cols(e), 'document': label, 'number': num or '', 'expiry': exp, 'days_left': days, 'status': _status(days), **R.src(e)})
    rows.sort(key=lambda r: (r['days_left'] == '', r['days_left'] if r['days_left'] != '' else 0))
    return R.EMP_COLS + [('document', 'Document', 'text'), ('number', 'Number', 'text'), ('expiry', 'Expiry', 'date'),
                         ('days_left', 'Days left', 'number'), ('status', 'Status', 'text')], rows


def r_probation_due(run):
    """Open probations (on probation / extended) with end date, extensions and days left; overdue first."""
    R = _h()
    from .models import ProbationExtension
    emps = {e.pk: e for e in run.employees}
    for e in emps.values():
        if not EmploymentInfo.objects.filter(employee_id=e.pk).exists():
            S.ensure_info(e)
    ext = {}
    for x in ProbationExtension.objects.filter(employee_id__in=list(emps)):
        ext[x.employee_id] = ext.get(x.employee_id, 0) + 1
    rows = []
    for i in EmploymentInfo.objects.filter(employee_id__in=list(emps), probation_status__in=('on_probation', 'extended')):
        e = emps[i.employee_id]
        days = (i.probation_end_date - run.today).days if i.probation_end_date else ''
        st = 'No end date' if days == '' else 'Overdue' if days < 0 else 'Due in 30 days' if days <= 30 else 'Due in 90 days' if days <= 90 else 'Later'
        rows.append({**R.emp_cols(e), 'joined': e.emp_joined_date, 'probation_end': i.probation_end_date, 'days_left': days,
                     'probation': i.get_probation_status_display(), 'extensions': ext.get(e.pk, 0), 'status': st,
                     'manager': e.emp_reporting_manager.username if e.emp_reporting_manager_id else '', **R.src(e)})
    rows.sort(key=lambda r: (r['days_left'] == '', r['days_left'] if r['days_left'] != '' else 0))
    return R.EMP_COLS + [('joined', 'Joining date', 'date'), ('probation_end', 'Probation ends', 'date'), ('days_left', 'Days left', 'number'),
                         ('probation', 'Probation', 'text'), ('extensions', 'Extensions', 'number'), ('status', 'Status', 'text'),
                         ('manager', 'Reporting manager', 'text')], rows


def r_no_emergency_contact(run):
    R = _h()
    have = set(EmergencyContact.objects.values_list('employee_id', flat=True))
    rows = []
    for e in run.employees:
        if e.pk in have:
            continue
        rows.append({**R.emp_cols(e), 'mobile': e.emp_mobile_number_1 or '', 'email': e.emp_personal_email or e.emp_company_email or '',
                     'joined': e.emp_joined_date, **R.src(e)})
    return R.EMP_COLS + [('mobile', 'Mobile', 'text'), ('email', 'E-mail', 'text'), ('joined', 'Joining date', 'date')], rows


def r_completeness(run):
    R = _h()
    emps = list(run.employees)
    ids = [e.pk for e in emps]
    idn = {i.employee_id: i for i in EmployeeIdentity.objects.filter(employee_id__in=ids)}
    contacts = set(EmergencyContact.objects.filter(employee_id__in=ids).values_list('employee_id', flat=True))
    from django.apps import apps
    banks = set(apps.get_model('EmpManagement', 'EmployeeBankDetail').objects.filter(employee_id__in=ids, is_active=True)
                .exclude(iban_number__isnull=True).exclude(iban_number='').values_list('employee_id', flat=True))
    rows = []
    for e in emps:
        c = S.completeness(e, idn.get(e.pk) or EmployeeIdentity(employee_id=e.pk), e.pk in contacts, e.pk in banks)
        rows.append({**R.emp_cols(e), 'score': c['score'], 'filled': f"{c['filled']} of {c['total']}", 'missing': ', '.join(c['missing']) or 'Nothing missing',
                     **R.src(e)})
    rows.sort(key=lambda r: r['score'])
    return R.EMP_COLS + [('score', 'Complete %', 'number'), ('filled', 'Fields filled', 'text'), ('missing', 'Missing', 'text')], rows


def r_history(run):
    R = _h()
    rows = []
    d_from = run.date_from
    d_to = run.date_to if run.date_to and run.date_to.year < 2999 else None
    labels = dict(S.HISTORY_TYPES)
    for e in run.all_employees:
        for ev in S.timeline(e, None, d_from, d_to):
            rows.append({**R.emp_cols(e), 'date': ev['date'], 'type': labels.get(ev['type'], ev['type']), 'event': ev['title'],
                         'detail': ev['detail'], 'by': ev['by'] or '', **R.src(e)})
    rows.sort(key=lambda r: (str(r['date']), r['employee']), reverse=True)
    return R.EMP_COLS + [('date', 'Date', 'date'), ('type', 'Type', 'text'), ('event', 'Event', 'text'), ('detail', 'Details', 'text'),
                         ('by', 'By', 'text')], rows


PERMS = ['view_report', 'view_emp_master']
REPORTS = {
    'identity-expiry': ('Identity documents expiry', 'People', 'Passport, visa, Emirates ID, labour card, work permit and insurance of each employee with days left.', r_identity_expiry, None, PERMS + ['view_emp_documents']),
    'probation': ('Probation due', 'People', 'Open probations with end date, extensions and days left – overdue first.', r_probation_due, None, PERMS),
    'no-emergency-contact': ('Employees without emergency contact', 'People', 'Active employees who have no emergency contact on file.', r_no_emergency_contact, None, PERMS),
    'master-completeness': ('Employee master data completeness', 'People', 'Share of the UAE employee-file fields filled per employee, with what is missing.', r_completeness, None, PERMS),
    'employee-history': ('Employee history', 'People', 'Joining, confirmation, probation decisions, transfers, salary revisions, organisation and field changes, resignations in the period.', r_history, 'month', PERMS),
}
REPORT_MODELS = {k: set() for k in REPORTS}
