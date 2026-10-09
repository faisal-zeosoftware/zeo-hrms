"""
Report centre (v1.8.0) – every report in one design.

GET /dashboard/api/reports/                 → the reports this user may run (for the menu / catalogue)
GET /dashboard/api/reports/<key>/?from=YYYY-MM-DD&to=YYYY-MM-DD&branch=1,2
                                            → {key, title, description, period, columns, rows, totals, notes}

Every report:
* is limited to the branches the user may see (and to the branches picked in the top bar);
* shows the employee as "Full name (code)" with one Branch column;
* shows readable values (Approved, Yes / No …);
* takes an optional period (reports with dates open on a sensible default: this month or this year).
"""
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q, Sum
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

M = apps.get_model


# ------------------------------------------------------------------ helpers
def nice(v):
    """Readable value: approved → Approved, minor_damage → Minor damage, True → Yes."""
    if v is None or v == '':
        return ''
    if isinstance(v, bool):
        return 'Yes' if v else 'No'
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ('true', 'false'):
            return 'Yes' if s.lower() == 'true' else 'No'
        if re.fullmatch(r'[a-z][a-z_]*', s):
            s = s.replace('_', ' ')
            return s[:1].upper() + s[1:]
        if re.fullmatch(r'[A-Z][A-Z_]+', s) and len(s) > 2:
            s = s.replace('_', ' ').lower()
            return s[:1].upper() + s[1:]
        return s
    return v


def money(v):
    return round(float(v or 0), 2)


def full_name(e):
    if e is None:
        return ''
    name = ' '.join(x for x in [e.emp_first_name, e.emp_middle_name, e.emp_last_name] if x and str(x).strip())
    return f'{name} ({e.emp_code})' if name else e.emp_code


def src(obj):
    """Where a row drills down to: the record it was read from (v1.8.1)."""
    return {'_m': obj._meta.label, '_id': obj.pk} if obj is not None else {}


def drill(report, f=None, **period):
    """Drill from a total / count to the rows of another report, filtered (v1.8.1)."""
    d = {'report': report, 'f': {k: v for k, v in (f or {}).items() if v not in (None, '')}}
    d.update({k: str(v) for k, v in period.items() if v})
    return d


def emp_cols(e):
    """The same five employee columns in every report."""
    return {'_emp': e.id if e else None,
        'employee': full_name(e),
        'branch': e.emp_branch_id.branch_name if e and e.emp_branch_id_id else '',
        'department': e.emp_dept_id.dept_name if e and e.emp_dept_id_id else '',
        'designation': e.emp_desgntn_id.desgntn_job_title if e and e.emp_desgntn_id_id else '',
        'category': e.emp_ctgry_id.ctgry_title if e and e.emp_ctgry_id_id else '',
    }


EMP_COLS = [('employee', 'Employee', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'),
            ('designation', 'Designation', 'text'), ('category', 'Category', 'text')]


def years_between(a, b):
    if not a or not b:
        return None
    return round((b - a).days / 365.25, 2)


def hours_of(text):
    """'06:30' / '6.5' / '6' → 6.5"""
    if text is None:
        return 0
    s = str(text).strip()
    m = re.fullmatch(r'(\d+):(\d{1,2})(?::\d{1,2})?', s)
    if m:
        return round(int(m.group(1)) + int(m.group(2)) / 60, 2)
    try:
        return round(float(s), 2)
    except ValueError:
        return 0


def duration_hours(d):
    return round(d.total_seconds() / 3600, 2) if d else ''


def month_end(d):
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def basic_by_employee(emp_ids):
    """Active monthly basic per employee from the salary structure: the component named / coded Basic;
    only when there is none, the components whose payroll category is Basic."""
    by_name, by_cat = defaultdict(float), defaultdict(float)
    qs = M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee_id__in=emp_ids, is_active=True).select_related('component')
    for r in qs:
        c = r.component
        if c.component_type == 'deduction':
            continue
        if (c.code or '').upper() in ('BAS', 'BASIC') or (c.name or '').strip().lower() in ('basic', 'basic salary'):
            by_name[r.employee_id] += float(r.amount or 0)
        elif c.payroll_category == 'basic':
            by_cat[r.employee_id] += float(r.amount or 0)
    return {e: by_name.get(e) or by_cat.get(e, 0) for e in emp_ids}


VARIABLE = ('bonus', 'commission', 'overtime', 'leave_encashment', 'air_ticket', 'gratuity', 'advance_salary')


class Run:
    """What a report function gets: the request, the employees the user may see, and the period."""

    def __init__(self, request, report):
        from AccessControl.access import ctx
        self.request = request
        self.c = ctx(request)
        today = date.today()
        q = request.query_params
        self.date_from = _date(q.get('from'))
        self.date_to = _date(q.get('to'))
        default = report.get('period')
        if default and not (self.date_from and self.date_to):
            if default == 'month':
                self.date_from, self.date_to = today.replace(day=1), month_end(today)
            elif default == 'year':
                self.date_from, self.date_to = today.replace(month=1, day=1), today.replace(month=12, day=31)
            elif default == 'next90':
                self.date_from, self.date_to = today, today + timedelta(days=90)
            elif default == 'next30':
                self.date_from, self.date_to = today, today + timedelta(days=30)
        self.today = today
        allowed = self.c.branches  # None = every branch
        picked = [int(x) for x in re.findall(r'\d+', q.get('branch', '') or '')]
        if allowed is None:
            self.branches = picked or None
        else:
            self.branches = [b for b in picked if b in allowed] if picked else list(allowed)
        Emp = M('EmpManagement', 'emp_master')
        qs = Emp.objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id', 'emp_nationality',
                                        'emp_reporting_manager')
        if self.branches is not None:
            qs = qs.filter(emp_branch_id__in=self.branches)
        self.all_employees = qs
        self.employees = qs.filter(is_active=True)

    def emp_q(self, field='employee'):
        """Filter for records of the employees the user may see."""
        return Q(**{f'{field}__in': self.all_employees.values('id')})

    def in_period(self, field):
        q = Q()
        if self.date_from:
            q &= Q(**{f'{field}__gte': self.date_from})
        if self.date_to:
            q &= Q(**{f'{field}__lte': self.date_to})
        return q


def _date(s):
    from django.utils.dateparse import parse_date
    try:
        return parse_date(str(s)) if s else None
    except ValueError:
        return None


# ------------------------------------------------------------------ people
def r_employees(run):
    cf = defaultdict(dict)
    names = []
    for v in M('EmpManagement', 'Emp_CustomFieldValue').objects.filter(emp_master__in=run.all_employees.values('id')).values('emp_master_id', 'emp_custom_field', 'field_value'):
        cf[v['emp_master_id']][v['emp_custom_field']] = v['field_value']
        if v['emp_custom_field'] not in names:
            names.append(v['emp_custom_field'])
    rows = []
    for e in run.all_employees:
        r = emp_cols(e)
        r.update({
            'gender': {'M': 'Male', 'F': 'Female', 'O': 'Other'}.get(e.emp_gender or '', e.emp_gender or ''),
            'nationality': str(e.emp_nationality or ''),
            'dob': e.emp_date_of_birth, 'age': int(years_between(e.emp_date_of_birth, run.today) or 0) or '',
            'joined': e.emp_joined_date, 'service': years_between(e.emp_joined_date, run.today) or '',
            'confirmation': e.emp_date_of_confirmation,
            'manager': e.emp_reporting_manager.username if e.emp_reporting_manager_id else '',
            'mobile': e.emp_mobile_number_1 or '', 'company_email': e.emp_company_email or '', 'personal_email': e.emp_personal_email or '',
            'person_id': e.person_id or '', 'status': 'Active' if e.is_active else 'Inactive', 'ess': nice(bool(e.is_ess)),
            'ot': nice(bool(e.emp_ot_applicable)), 'attendance_source': nice(e.attendance_source or ''),
        })
        r.update(src(e))
        for n in names:
            r['cf_' + n] = nice(cf[e.id].get(n, ''))
        rows.append(r)
    cols = EMP_COLS + [('gender', 'Gender', 'text'), ('nationality', 'Nationality', 'text'), ('dob', 'Date of birth', 'date'), ('age', 'Age', 'number'),
                       ('joined', 'Joining date', 'date'), ('service', 'Service (years)', 'number'), ('confirmation', 'Confirmation date', 'date'),
                       ('manager', 'Reporting manager', 'text'), ('mobile', 'Mobile', 'text'), ('company_email', 'Company e-mail', 'text'),
                       ('personal_email', 'Personal e-mail', 'text'), ('person_id', 'Person ID', 'text'), ('status', 'Status', 'text'),
                       ('ess', 'ESS login', 'text'), ('ot', 'OT applicable', 'text'), ('attendance_source', 'Attendance source', 'text')]
    cols += [('cf_' + n, n, 'text') for n in names]
    return cols, rows


ORG_KEY = {'dept_master': 'department', 'desgntn_master': 'designation', 'ctgry_master': 'category'}


def _master(run, model, name_f, code_f, desc_f, active_f, emp_f):
    counts = defaultdict(int)
    for v in run.employees.values(emp_f):
        counts[v[emp_f]] += 1
    qs = M('OrganisationManager', model).objects.prefetch_related('branch').order_by(name_f)
    if run.branches is not None:
        qs = qs.filter(Q(branch__in=run.branches) | Q(branch__isnull=True) | Q(id__in=[k for k in counts if k])).distinct()
    rows = [{'name': getattr(o, name_f), 'code': getattr(o, code_f) or '', 'description': getattr(o, desc_f) or '',
             'branches': ', '.join(b.branch_name for b in o.branch.all()), 'active': nice(bool(getattr(o, active_f))),
             'employees': counts.get(o.id, 0), **src(o),
             '_cell': {'employees': drill('employees', {ORG_KEY[model]: getattr(o, name_f), 'status': 'Active'})}} for o in qs]
    return rows


def r_departments(run):
    return [('name', 'Department', 'text'), ('code', 'Code', 'text'), ('description', 'Description', 'text'), ('branches', 'Branches', 'text'),
            ('active', 'Active', 'text'), ('employees', 'Employees', 'number')], _master(run, 'dept_master', 'dept_name', 'dept_code', 'dept_description', 'dept_is_active', 'emp_dept_id')


def r_designations(run):
    return [('name', 'Designation', 'text'), ('code', 'Code', 'text'), ('description', 'Description', 'text'), ('branches', 'Branches', 'text'),
            ('active', 'Active', 'text'), ('employees', 'Employees', 'number')], _master(run, 'desgntn_master', 'desgntn_job_title', 'desgntn_code', 'desgntn_description', 'desgntn_is_active', 'emp_desgntn_id')


def r_categories(run):
    return [('name', 'Category', 'text'), ('code', 'Code', 'text'), ('description', 'Description', 'text'), ('branches', 'Branches', 'text'),
            ('active', 'Active', 'text'), ('employees', 'Employees', 'number')], _master(run, 'ctgry_master', 'ctgry_title', 'ctgry_code', 'ctgry_description', 'ctgry_is_active', 'emp_ctgry_id')


def _left(run):
    """employee id → (date left, reason, resignation) from resignations and end of service."""
    out = {}
    for r in M('EmpManagement', 'EmployeeResignation').objects.filter(run.emp_q()).exclude(status__iexact='rejected'):
        out[r.employee_id] = (r.last_working_date or r.resigned_on, nice(r.termination_type or 'resignation'), r)
    return out


def r_headcount(run):
    left = _left(run)
    groups = {}
    for e in run.all_employees:
        key = (e.emp_branch_id.branch_name if e.emp_branch_id_id else '(no branch)', e.emp_dept_id.dept_name if e.emp_dept_id_id else '(no department)')
        g = groups.setdefault(key, {'branch': key[0], 'department': key[1], 'active': 0, 'male': 0, 'female': 0, 'joined': 0, 'left': 0})
        if e.is_active:
            g['active'] += 1
            if e.emp_gender == 'M':
                g['male'] += 1
            elif e.emp_gender == 'F':
                g['female'] += 1
        if e.emp_joined_date and run.date_from <= e.emp_joined_date <= run.date_to:
            g['joined'] += 1
        lv = left.get(e.id)
        if lv and lv[0] and run.date_from <= lv[0] <= run.date_to:
            g['left'] += 1
    rows = sorted(groups.values(), key=lambda g: (g['branch'], g['department']))
    for g in rows:
        avg = g['active'] + (g['left'] - g['joined']) / 2
        g['turnover'] = round(100 * g['left'] / avg, 1) if avg > 0 else 0
        org = {'branch': g['branch'], 'department': g['department']}
        per = {'from': run.date_from, 'to': run.date_to}
        g['_drill'] = drill('employees', dict(org, status='Active'))
        g['_cell'] = {'active': g['_drill'], 'male': drill('employees', dict(org, status='Active', gender='Male')),
                      'female': drill('employees', dict(org, status='Active', gender='Female')),
                      'joined': drill('joiners-leavers', dict(org, event='Joined'), **per),
                      'left': drill('joiners-leavers', dict(org, event='Left'), **per)}
    return [('branch', 'Branch', 'text'), ('department', 'Department', 'text'), ('active', 'Active employees', 'number'), ('male', 'Male', 'number'),
            ('female', 'Female', 'number'), ('joined', 'Joined in period', 'number'), ('left', 'Left in period', 'number'),
            ('turnover', 'Turnover %', 'number')], rows


def r_joiners_leavers(run):
    rows = []
    for e in run.all_employees.filter(run.in_period('emp_joined_date')):
        rows.append({**emp_cols(e), 'event': 'Joined', 'date': e.emp_joined_date, 'reason': '', 'service': '', **src(e)})
    left = _left(run)
    for e in run.all_employees.filter(id__in=list(left)):
        d, why, res = left[e.id]
        if d and run.date_from <= d <= run.date_to:
            rows.append({**emp_cols(e), 'event': 'Left', 'date': d, 'reason': why, 'service': years_between(e.emp_joined_date, d) or '', **src(res)})
    rows.sort(key=lambda r: r['date'] or date.min)
    return EMP_COLS + [('event', 'Event', 'text'), ('date', 'Date', 'date'), ('reason', 'Reason', 'text'), ('service', 'Service (years)', 'number')], rows


def r_probation(run):
    rows = []
    for e in run.employees.filter(emp_date_of_confirmation__isnull=False, emp_date_of_confirmation__gte=run.today - timedelta(days=60)):
        days = (e.emp_date_of_confirmation - run.today).days
        st = 'Overdue' if days < 0 else 'Due in 30 days' if days <= 30 else 'Due in 90 days' if days <= 90 else 'Later'
        rows.append({**emp_cols(e), 'joined': e.emp_joined_date, 'confirmation': e.emp_date_of_confirmation, 'days_left': days, 'status': st, **src(e)})
    rows.sort(key=lambda r: r['days_left'])
    return EMP_COLS + [('joined', 'Joining date', 'date'), ('confirmation', 'Confirmation due', 'date'), ('days_left', 'Days left', 'number'),
                       ('status', 'Status', 'text')], rows


def r_birthdays(run):
    rows = []
    for e in run.employees:
        for kind, d0 in (('Birthday', e.emp_date_of_birth), ('Work anniversary', e.emp_joined_date)):
            if not d0:
                continue
            for y in range(run.date_from.year, run.date_to.year + 1):
                try:
                    d = d0.replace(year=y)
                except ValueError:
                    d = d0.replace(year=y, day=28)
                if run.date_from <= d <= run.date_to and (kind == 'Birthday' or y > d0.year):
                    rows.append({**emp_cols(e), 'event': kind, 'date': d, 'years': y - d0.year, **src(e)})
    rows.sort(key=lambda r: r['date'])
    return EMP_COLS + [('event', 'Event', 'text'), ('date', 'Date', 'date'), ('years', 'Years', 'number')], rows


def _expiry_status(d, today):
    if not d:
        return '', 'No expiry'
    days = (d - today).days
    return days, 'Expired' if days < 0 else 'Expiring in 30 days' if days <= 30 else 'Expiring in 90 days' if days <= 90 else 'Valid'


def r_documents(run):
    rows = []
    for d in M('EmpManagement', 'Emp_Documents').objects.filter(run.emp_q('emp_id')).select_related('emp_id__emp_branch_id', 'emp_id__emp_dept_id', 'emp_id__emp_desgntn_id', 'emp_id__emp_ctgry_id', 'document_type'):
        days, st = _expiry_status(d.emp_doc_expiry_date, run.today)
        rows.append({**emp_cols(d.emp_id), 'type': str(d.document_type or ''), 'number': d.emp_doc_number or '', 'issued': d.emp_doc_issued_date,
                     'expiry': d.emp_doc_expiry_date, 'days_left': days, 'status': st, 'active': nice(bool(d.is_active)), **src(d)})
    rows.sort(key=lambda r: (r['days_left'] == '', r['days_left'] if r['days_left'] != '' else 0))
    return EMP_COLS + [('type', 'Document type', 'text'), ('number', 'Number', 'text'), ('issued', 'Issued', 'date'), ('expiry', 'Expiry', 'date'),
                       ('days_left', 'Days left', 'number'), ('status', 'Status', 'text'), ('active', 'Active', 'text')], rows


# ------------------------------------------------------------------ leave
def r_leave(run):
    rows = []
    qs = M('calendars', 'employee_leave_request').objects.filter(run.emp_q()).filter(
        Q(start_date__lte=run.date_to) & Q(end_date__gte=run.date_from)).select_related('employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'leave_type')
    for l in qs:
        rows.append({**emp_cols(l.employee), 'number': l.document_number or '', 'type': str(l.leave_type or ''), 'from': l.start_date, 'to': l.end_date,
                     'applied': l.applied_days or l.number_of_days, 'approved': l.approved_days or 0, 'status': nice(l.status),
                     'reason': l.reason or '', 'requested': getattr(l, 'applied_on', None) or getattr(l, 'created_at', None), **src(l),
                     'starting': 'Yes' if l.start_date >= run.date_from else 'No'})
    rows.sort(key=lambda r: r['from'])
    return EMP_COLS + [('number', 'Document', 'text'), ('type', 'Leave type', 'text'), ('from', 'From', 'date'), ('to', 'To', 'date'),
                       ('applied', 'Days applied', 'number'), ('approved', 'Days approved', 'number'), ('status', 'Status', 'text'),
                       ('reason', 'Reason', 'text'), ('requested', 'Requested on', 'date')], rows


def r_leave_approvals(run):
    rows = []
    qs = M('calendars', 'LeaveApproval').objects.filter(leave_request__isnull=False, leave_request__employee__in=run.all_employees.values('id')).filter(
        Q(leave_request__start_date__lte=run.date_to) & Q(leave_request__end_date__gte=run.date_from)).select_related(
        'leave_request__employee__emp_branch_id', 'leave_request__employee__emp_dept_id', 'leave_request__employee__emp_desgntn_id',
        'leave_request__employee__emp_ctgry_id', 'leave_request__leave_type', 'approver')
    for a in qs:
        l = a.leave_request
        waited = (run.today - a.created_at).days if a.created_at and (a.status or '').lower() == 'pending' else ''
        rows.append({**emp_cols(l.employee), 'number': l.document_number or '', 'type': str(l.leave_type or ''), 'from': l.start_date, 'to': l.end_date,
                     'days': l.applied_days or l.number_of_days, 'level': a.level, 'approver': a.approver.username if a.approver_id else '',
                     'status': nice(a.status), 'days_waiting': waited, 'note': a.note or a.rejection_reason or '', **src(l)})
    return EMP_COLS + [('number', 'Leave request', 'text'), ('type', 'Leave type', 'text'), ('from', 'From', 'date'), ('to', 'To', 'date'),
                       ('days', 'Days', 'number'), ('level', 'Level', 'number'), ('approver', 'Approver', 'text'), ('status', 'Status', 'text'),
                       ('days_waiting', 'Days waiting', 'number'), ('note', 'Note', 'text')], rows


def _eligible(e, lt):
    cat = (lt.leave_category or '').lower()
    if cat == 'maternity':
        return e.emp_gender == 'F'
    if cat == 'paternity':
        return e.emp_gender == 'M'
    return True


def _policy_lines():
    """employee id → policy name, and (policy, leave type) → line – v1.10.0 leave policies (empty when not installed)."""
    try:
        from LeavePolicy import engine
        return engine
    except ImportError:
        return None


def r_leave_balance(run):
    """Balance per employee and leave type: the policy, earned / taken / encashed this leave year, waiting for approval and available."""
    rows = []
    qs = M('calendars', 'emp_leave_balance').objects.filter(run.emp_q()).filter(employee__is_active=True).select_related(
        'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'leave_type')
    year_start = run.today.replace(month=1, day=1)
    taken, pending = defaultdict(float), defaultdict(float)
    for l in M('calendars', 'employee_leave_request').objects.filter(run.emp_q(), status__in=('approved', 'pending'), start_date__gte=year_start).values(
            'employee_id', 'leave_type_id', 'approved_days', 'applied_days', 'status'):
        if l['status'] == 'approved':
            taken[(l['employee_id'], l['leave_type_id'])] += float(l['approved_days'] or l['applied_days'] or 0)
        else:
            pending[(l['employee_id'], l['leave_type_id'])] += float(l['applied_days'] or 0)
    eng = _policy_lines()
    earned, encashed = defaultdict(float), defaultdict(float)
    if eng is not None:
        from LeavePolicy.models import LeaveLedger
        for l in LeaveLedger.objects.filter(date__gte=year_start, kind__in=('accrual', 'reset', 'encashed')).values('employee_id', 'leave_type_id', 'kind', 'days'):
            (encashed if l['kind'] == 'encashed' else earned)[(l['employee_id'], l['leave_type_id'])] += abs(l['days']) if l['kind'] == 'encashed' else l['days']
    pol = {}
    for b in qs:
        if not _eligible(b.employee, b.leave_type):
            continue
        line = None
        if eng is not None:
            if b.employee_id not in pol:
                pol[b.employee_id] = eng.policy_for(b.employee)
            p = pol[b.employee_id]
            line = p.lines.filter(leave_type_id=b.leave_type_id).first() if p else None
            if p is not None and line is None:
                continue          # not a leave type of the employee's policy
            if line is not None and line.accrual in ('per_event', 'none'):
                continue          # no balance (per event / unpaid): see Leave requests
        k = (b.employee_id, b.leave_type_id)
        bal = round(float(b.balance or 0), 2)
        # v1.12.0: days a year by length of service, and the step's carry-forward limit
        per_year, svc_year, lim = '', '', None
        if line is not None:
            per_year = eng.days_a_year(line, b.employee, run.today)
            svc_year = eng.service_year(b.employee, run.today) if b.employee.emp_joined_date else ''
            st = eng.step_on(line, b.employee, run.today)[1]
            lim = st[2] if st and st[2] is not None else line.carry_forward_max
        rows.append({**emp_cols(b.employee), 'policy': pol.get(b.employee_id).name if pol.get(b.employee_id) else '', 'type': str(b.leave_type),
                     'per_year': per_year, 'service_year': svc_year,
                     'earned': round(earned.get(k, 0), 2), 'taken': taken.get(k, 0), 'encashed': round(encashed.get(k, 0), 2), 'balance': bal,
                     'pending': pending.get(k, 0), 'available': round(bal - pending.get(k, 0), 2),
                     'cf_limit': lim if lim is not None else '',
                     'over_limit': round(max(bal - lim, 0), 2) if lim is not None else '', **src(b),
                     '_cell': {'taken': drill('leave', {'employee': full_name(b.employee), 'type': str(b.leave_type), 'status': 'Approved'},
                                              **{'from': year_start, 'to': run.today.replace(month=12, day=31)}),
                               'pending': drill('leave', {'employee': full_name(b.employee), 'type': str(b.leave_type), 'status': 'Pending'},
                                                **{'from': '2000-01-01', 'to': '2099-12-31'}),
                               **{c: drill('leave-statement', {'employee': full_name(b.employee), 'type': str(b.leave_type)},
                                           **{'from': year_start, 'to': run.today.replace(month=12, day=31)}) for c in ('earned', 'encashed', 'balance')}}})
    return EMP_COLS + [('policy', 'Leave policy', 'text'), ('type', 'Leave type', 'text'), ('per_year', 'Days a year now', 'number'),
                       ('service_year', 'Service year', 'number'), ('earned', 'Earned this year', 'number'),
                       ('taken', 'Taken this year', 'number'), ('encashed', 'Encashed this year', 'number'), ('balance', 'Balance', 'number'),
                       ('pending', 'Waiting for approval', 'number'), ('available', 'Available', 'number'),
                       ('cf_limit', 'Carry-forward limit', 'number'), ('over_limit', 'Above the limit', 'number')], rows


def r_leave_statement(run):
    """Every change to a balance in the period: opening, earned, taken, cancelled, encashed, lapsed – with the running balance."""
    from LeavePolicy.models import LeaveLedger
    emps = {e.pk: e for e in run.all_employees}
    types = dict(M('calendars', 'leave_type').objects.values_list('id', 'name'))
    qs = LeaveLedger.objects.filter(employee_id__in=list(emps), date__lte=run.date_to).order_by('employee_id', 'leave_type_id', 'date', 'id')
    rows, run_bal, opened = [], defaultdict(float), set()
    for l in qs:
        k = (l.employee_id, l.leave_type_id)
        e = emps[l.employee_id]
        if l.date < run.date_from:
            run_bal[k] += l.days
            continue
        if k not in opened:
            opened.add(k)
            rows.append({**emp_cols(e), 'type': types.get(l.leave_type_id, ''), 'date': run.date_from, 'entry': 'Balance brought forward',
                         'days': round(run_bal[k], 2), 'balance': round(run_bal[k], 2), 'note': '', **src(e)})
        run_bal[k] += l.days
        r = {**emp_cols(e), 'type': types.get(l.leave_type_id, ''), 'date': l.date, 'entry': l.get_kind_display(), 'days': l.days,
             'balance': round(run_bal[k], 2), 'note': l.note}
        if l.ref_model and l.ref_id:
            r.update({'_m': l.ref_model, '_id': l.ref_id})
        else:
            r.update(src(e))
        rows.append(r)
    return EMP_COLS + [('type', 'Leave type', 'text'), ('date', 'Date', 'date'), ('entry', 'Entry', 'text'), ('days', 'Days', 'number'),
                       ('balance', 'Balance after', 'number'), ('note', 'Note', 'text')], rows


def r_leave_encashment(run):
    rows = []
    for x in M('PayrollManagement', 'LeaveEncashment').objects.filter(run.emp_q()).select_related(
            'leave_type', 'payroll_run', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        rows.append({**emp_cols(x.employee), 'type': str(x.leave_type), 'days': money(x.encashment_days), 'basic': money(x.basic_salary),
                     'amount': money(x.encashment_amount), 'status': x.get_status_display(), 'requested': x.created_at.date() if x.created_at else '',
                     'approved': x.approved_at.date() if x.approved_at else '', 'run': str(x.payroll_run) if x.payroll_run_id else '',
                     'remarks': x.remarks or '', **src(x)})
    rows.sort(key=lambda r: r['requested'] or date.min, reverse=True)
    return EMP_COLS + [('type', 'Leave type', 'text'), ('days', 'Days', 'number'), ('basic', 'Basic (AED)', 'number'), ('amount', 'Amount (AED)', 'number'),
                       ('status', 'Status', 'text'), ('requested', 'Requested', 'date'), ('approved', 'Approved', 'date'), ('run', 'Paid in payroll', 'text'),
                       ('remarks', 'Remarks', 'text')], rows


def r_leave_liability(run):
    """Unused annual leave valued on basic (UAE labour law: leave salary on basic) and, for comparison, on gross."""
    emps = list(run.employees)
    basic = basic_by_employee([e.id for e in emps])
    gross = gross_by_employee([e.id for e in emps])
    bal = defaultdict(float)
    for b in M('calendars', 'emp_leave_balance').objects.filter(employee__in=[e.id for e in emps], leave_type__leave_category='annual').values('employee_id', 'balance'):
        bal[b['employee_id']] += float(b['balance'] or 0)
    rows = []
    for e in emps:
        daily = basic.get(e.id, 0) * 12 / 365
        daily_g = gross.get(e.id, 0) * 12 / 365
        rows.append({**emp_cols(e), 'balance': round(bal.get(e.id, 0), 2), 'basic': money(basic.get(e.id, 0)), 'daily': round(daily, 2),
                     'liability': round(daily * bal.get(e.id, 0), 2), 'gross': money(gross.get(e.id, 0)), 'daily_gross': round(daily_g, 2),
                     'liability_gross': round(daily_g * bal.get(e.id, 0), 2), **src(e),
                     '_cell': {'balance': drill('leave-balance', {'employee': full_name(e)}), 'basic': drill('salary', {'employee': full_name(e)}),
                               'gross': drill('salary', {'employee': full_name(e)})}})
    return EMP_COLS + [('balance', 'Annual leave balance (days)', 'number'), ('basic', 'Monthly basic (AED)', 'number'),
                       ('daily', 'Daily basic (AED)', 'number'), ('liability', 'Leave liability – basic (AED)', 'number'),
                       ('gross', 'Monthly gross (AED)', 'number'), ('daily_gross', 'Daily gross (AED)', 'number'),
                       ('liability_gross', 'Leave liability – gross (AED)', 'number')], rows


# ------------------------------------------------------------------ time
def _attendance_days(run, e, first, last):
    from calendars.utils import get_attendance_summary
    data = get_attendance_summary(e, first, last) or {}
    return data.get('summary') or []


def _att_window(run, e):
    first = max(run.date_from, e.emp_joined_date) if e.emp_joined_date else run.date_from
    last = min(run.date_to, run.today)
    return first, last


def r_attendance(run):
    if (run.date_to - run.date_from).days > 92:
        raise ValueError('Choose at most 3 months.')
    times = {}
    for a in M('calendars', 'Attendance').objects.filter(run.emp_q(), date__gte=run.date_from, date__lte=run.date_to).values('id', 'employee_id', 'date', 'check_in_time', 'check_out_time', 'total_hours'):
        times[(a['employee_id'], a['date'])] = a
    rows = []
    for e in run.employees:
        first, last = _att_window(run, e)
        if first > last:
            continue
        for d in _attendance_days(run, e, first, last):
            t = times.get((e.id, d['date']), {})
            rows.append({**emp_cols(e), 'date': d['date'], 'status': d['status'], 'leave_type': d.get('leave_type') or '',
                         'in': t.get('check_in_time') or '', 'out': t.get('check_out_time') or '', 'hours': duration_hours(t.get('total_hours')),
                         **({'_m': 'calendars.Attendance', '_id': t['id']} if t.get('id') else src(e)),
                         'checked_in': 'Yes' if t.get('check_in_time') else 'No'})
    return EMP_COLS + [('date', 'Date', 'date'), ('status', 'Status', 'text'), ('leave_type', 'Leave type', 'text'), ('in', 'Check in', 'text'),
                       ('out', 'Check out', 'text'), ('hours', 'Hours', 'number')], rows


def r_attendance_summary(run):
    if (run.date_to - run.date_from).days > 92:
        raise ValueError('Choose at most 3 months.')
    late = defaultdict(int)
    for r in M('calendars', 'LateinEarlyoutRequest').objects.filter(run.emp_q(), date__gte=run.date_from, date__lte=run.date_to).values('employee_id'):
        late[r['employee_id']] += 1
    ot = defaultdict(float)
    for r in M('calendars', 'EmployeeOvertime').objects.filter(run.emp_q(), date__gte=run.date_from, date__lte=run.date_to).values('employee_id', 'hours'):
        ot[r['employee_id']] += float(r['hours'] or 0)
    rows = []
    for e in run.employees:
        first, last = _att_window(run, e)
        if first > last:
            continue
        days = _attendance_days(run, e, first, last)
        c = defaultdict(int)
        for d in days:
            c[d['status']] += 1
        working = c['Present'] + c['Absent'] + c['On Leave']
        rows.append({**emp_cols(e), 'from': first, 'to': last, 'working': working, 'present': c['Present'], 'absent': c['Absent'],
                     'leave': c['On Leave'], 'weekend': c['Weekend'], 'holiday': c['Holiday'],
                     'pct': round(100 * c['Present'] / working, 1) if working else 0, 'late': late.get(e.id, 0), 'ot': round(ot.get(e.id, 0), 2), **src(e)})
        per, who = {'from': first, 'to': last}, {'employee': full_name(e)}
        rows[-1]['_cell'] = {'present': drill('attendance', dict(who, status='Present'), **per), 'absent': drill('attendance', dict(who, status='Absent'), **per),
                             'leave': drill('attendance', dict(who, status='On Leave'), **per), 'weekend': drill('attendance', dict(who, status='Weekend'), **per),
                             'holiday': drill('attendance', dict(who, status='Holiday'), **per),
                             'working': drill('attendance', dict(who, status='Present|Absent|On Leave'), **per),
                             'late': drill('late-early', who, **per), 'ot': drill('overtime', who, **per)}
    return EMP_COLS + [('from', 'From', 'date'), ('to', 'To', 'date'), ('working', 'Working days', 'number'), ('present', 'Present', 'number'),
                       ('absent', 'Absent', 'number'), ('leave', 'On leave', 'number'), ('weekend', 'Weekend', 'number'), ('holiday', 'Holiday', 'number'),
                       ('pct', 'Attendance %', 'number'), ('late', 'Late in / early out requests', 'number'), ('ot', 'Overtime hours', 'number')], rows


def r_late_early(run):
    rows = [{**emp_cols(r.employee), 'number': r.document_number or '', 'date': r.date, 'type': nice(r.request_type), 'reason': r.reason or '', 'status': nice(r.status), **src(r)}
            for r in M('calendars', 'LateinEarlyoutRequest').objects.filter(run.emp_q(), run.in_period('date')).select_related(
                'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id').order_by('date')]
    return EMP_COLS + [('number', 'Document', 'text'), ('date', 'Date', 'date'), ('type', 'Type', 'text'), ('reason', 'Reason', 'text'), ('status', 'Status', 'text')], rows


def r_overtime(run):
    rows = [{**emp_cols(o.employee), 'date': o.date, 'type': o.get_ot_type_display() or '', 'slab': o.get_slab_display() or '', 'hours': float(o.hours or 0),
             'approved': nice(bool(o.approved)), 'source': o.get_source_display() or '', **src(o)}
            for o in M('calendars', 'EmployeeOvertime').objects.filter(run.emp_q(), run.in_period('date')).select_related(
                'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id').order_by('date')]
    return EMP_COLS + [('date', 'Date', 'date'), ('type', 'Type', 'text'), ('slab', 'Slab', 'text'), ('hours', 'Hours', 'number'),
                       ('approved', 'Approved', 'text'), ('source', 'Source', 'text')], rows


# ------------------------------------------------------------------ payroll
def _payslips(run, components=False):
    qs = M('PayrollManagement', 'Payslip').objects.filter(run.emp_q()).select_related(
        'payroll_run', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id')
    if components:
        qs = qs.prefetch_related('components__component')
    keep = []
    for p in qs:
        pr = p.payroll_run
        start = date(pr.year, pr.month, 1)
        if month_end(start) >= run.date_from and start <= run.date_to:
            keep.append(p)
    keep.sort(key=lambda p: (p.payroll_run.year, p.payroll_run.month, p.employee.emp_code))
    return keep


def r_payroll_register(run):
    rows = []
    for p in _payslips(run):
        pr = p.payroll_run
        rows.append({**emp_cols(p.employee), 'run': pr.name, 'period': date(pr.year, pr.month, 1).strftime('%b %Y'), 'working_days': p.total_working_days,
                     'days_worked': p.days_worked, 'gross': money(p.gross_salary), 'additions': money(p.total_additions), 'deductions': money(p.total_deductions),
                     'arrears': money(p.arrears), 'net': money(p.net_salary), 'status': nice(p.status), 'run_status': nice(pr.status), **src(p)})
    return EMP_COLS + [('run', 'Payroll run', 'text'), ('period', 'Period', 'text'), ('working_days', 'Working days', 'number'),
                       ('days_worked', 'Days worked', 'number'), ('gross', 'Gross (AED)', 'number'), ('additions', 'Additions (AED)', 'number'),
                       ('deductions', 'Deductions (AED)', 'number'), ('arrears', 'Arrears (AED)', 'number'), ('net', 'Net (AED)', 'number'),
                       ('status', 'Payslip status', 'text'), ('run_status', 'Run status', 'text')], rows


def r_wps(run):
    banks = {}
    for b in M('EmpManagement', 'EmployeeBankDetail').objects.filter(run.emp_q(), is_active=True):
        banks[b.employee_id] = b
    rows = []
    for p in _payslips(run, components=True):
        pr, e, b = p.payroll_run, p.employee, banks.get(p.employee_id)
        start = pr.attendance_start_date or date(pr.year, pr.month, 1)
        end = pr.attendance_end_date or month_end(date(pr.year, pr.month, 1))
        variable = round(sum(float(c.amount or 0) for c in p.components.all()
                             if c.component.component_type == 'addition' and c.component.payroll_category in VARIABLE), 2)
        variable = min(variable, money(p.net_salary))
        leave_days = max(0, (p.total_working_days or 0) - (p.days_worked or 0))
        rows.append({'record': 'EDR', 'person_id': e.person_id or '', 'routing': (b.route_code if b else '') or '', 'iban': (b.iban_number if b else '') or '',
                     'start': start, 'end': end, 'days': (end - start).days + 1, 'fixed': round(money(p.net_salary) - variable, 2), 'variable': variable,
                     'leave_days': leave_days, **emp_cols(e), 'bank': (b.bank_name if b else '') or '', 'check': 'Missing Person ID' if not e.person_id else 'Missing IBAN' if not (b and b.iban_number) else 'OK', **src(p)})
    return [('record', 'Record', 'text'), ('person_id', 'Employee ID (MOHRE)', 'text'), ('routing', 'Agent / routing code', 'text'), ('iban', 'IBAN', 'text'),
            ('start', 'Pay start', 'date'), ('end', 'Pay end', 'date'), ('days', 'Days in period', 'number'), ('fixed', 'Fixed salary (AED)', 'number'),
            ('variable', 'Variable salary (AED)', 'number'), ('leave_days', 'Leave days', 'number')] + EMP_COLS + [('bank', 'Bank', 'text'), ('check', 'Check', 'text')], rows


def r_salary(run):
    comps = []
    per = defaultdict(dict)
    line = {}
    for s in M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee__in=run.employees.values('id'), is_active=True).select_related('component'):
        n = s.component.name
        if s.component.component_type == 'deduction':
            continue
        if n not in comps:
            comps.append(n)
        per[s.employee_id][n] = per[s.employee_id].get(n, 0) + money(s.amount)
        line[(s.employee_id, n)] = s
    order = sorted(comps, key=lambda n: (n.lower() != 'basic', n))
    rows = []
    for e in run.employees:
        r = {**emp_cols(e), 'joined': e.emp_joined_date}
        total = 0
        for n in order:
            r['c_' + n] = per[e.id].get(n, 0)
            total += r['c_' + n]
        r['total'] = round(total, 2)
        r.update(src(e))
        r['_cell'] = {'c_' + n: src(line[(e.id, n)]) for n in order if (e.id, n) in line}
        rows.append(r)
    return EMP_COLS + [('joined', 'Joining date', 'date')] + [('c_' + n, n + ' (AED)', 'number') for n in order] + [('total', 'Monthly total (AED)', 'number')], rows


def r_salary_revisions(run):
    rows = []
    for h in M('PayrollManagement', 'SalaryRevisionHistory').objects.filter(run.emp_q()).filter(run.in_period('revised_on__date') if run.date_from else Q()).select_related(
            'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'component', 'created_by').order_by('revised_on'):
        old, new = money(h.old_amount), money(h.new_amount)
        rows.append({**emp_cols(h.employee), 'component': str(h.component or ''), 'old': old, 'new': new, 'change': round(new - old, 2),
                     'pct': round(100 * (new - old) / old, 1) if old else '', 'effective': h.effective_period or '',
                     'revised_on': h.revised_on.date() if h.revised_on else '', 'by': h.created_by.username if h.created_by_id else '', 'remarks': h.remarks or '', **src(h)})
    return EMP_COLS + [('component', 'Component', 'text'), ('old', 'Old (AED)', 'number'), ('new', 'New (AED)', 'number'), ('change', 'Change (AED)', 'number'),
                       ('pct', 'Change %', 'number'), ('effective', 'Effective', 'text'), ('revised_on', 'Revised on', 'date'), ('by', 'Revised by', 'text'),
                       ('remarks', 'Remarks', 'text')], rows


def r_loans(run):
    paid = defaultdict(float)
    for r in M('PayrollManagement', 'LoanRepayment').objects.values('loan_id', 'amount_paid'):
        paid[r['loan_id']] += float(r['amount_paid'] or 0)
    rows = []
    for l in M('PayrollManagement', 'LoanApplication').objects.filter(run.emp_q()).select_related(
            'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'loan_type'):
        amount = money(l.amount_requested)
        balance = money(l.remaining_balance) if l.remaining_balance is not None else round(amount - paid.get(l.id, 0), 2)
        emi = money(l.emi_amount)
        rows.append({**emp_cols(l.employee), 'number': l.document_number or '', 'type': str(l.loan_type or ''), 'amount': amount, 'emi': emi,
                     'repaid': round(paid.get(l.id, 0), 2), 'balance': balance, 'left': math.ceil(balance / emi) if emi and balance > 0 else 0,
                     'disbursed': l.disbursement_date, 'status': nice(l.status), **src(l),
                     'open': 'Yes' if l.status in ('Approved', 'Disbursed', 'In Progress', 'Paused') else 'No'})
    return EMP_COLS + [('number', 'Document', 'text'), ('type', 'Loan type', 'text'), ('amount', 'Amount (AED)', 'number'), ('emi', 'Instalment (AED)', 'number'),
                       ('repaid', 'Repaid (AED)', 'number'), ('balance', 'Outstanding (AED)', 'number'), ('left', 'Instalments left', 'number'),
                       ('disbursed', 'Disbursed on', 'date'), ('status', 'Status', 'text')], rows


def r_advances(run):
    rows = [{**emp_cols(a.employee), 'number': a.document_number or '', 'amount': money(a.requested_amount), 'reason': a.reason or '',
             'status': nice(a.status), 'requested': a.created_at.date() if a.created_at else '', **src(a)}
            for a in M('PayrollManagement', 'AdvanceSalaryRequest').objects.filter(run.emp_q()).select_related(
                'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id')]
    return EMP_COLS + [('number', 'Document', 'text'), ('amount', 'Amount (AED)', 'number'), ('reason', 'Reason', 'text'), ('status', 'Status', 'text'),
                       ('requested', 'Requested on', 'date')], rows


def r_air_tickets(run):
    rows = []
    for a in M('PayrollManagement', 'AirTicketAllocation').objects.filter(run.emp_q()).select_related(
            'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'policy'):
        days, _ = _expiry_status(a.expiry_date, run.today)
        rows.append({**emp_cols(a.employee), 'policy': str(a.policy or ''), 'class': nice(getattr(a.policy, 'travel_class', '') or ''),
                     'amount': money(a.amount), 'remaining': money(a.remaining_amount), 'allocated': a.allocated_date, 'expiry': a.expiry_date,
                     'days_left': days, 'status': nice(a.status), 'active': nice(bool(a.is_active)), **src(a)})
    return EMP_COLS + [('policy', 'Policy', 'text'), ('class', 'Class', 'text'), ('amount', 'Entitlement (AED)', 'number'), ('remaining', 'Remaining (AED)', 'number'),
                       ('allocated', 'Allocated', 'date'), ('expiry', 'Expires', 'date'), ('days_left', 'Days left', 'number'), ('status', 'Status', 'text'),
                       ('active', 'Active', 'text')], rows


def gratuity_uae(years, basic):
    """UAE labour law: 21 days' basic per year for the first 5 years, 30 days per year after that,
    nothing before 1 year, capped at 2 years' basic. Daily basic = basic × 12 / 365."""
    if not years or years < 1 or not basic:
        return 0, 0
    days = 21 * min(years, 5) + 30 * max(0, years - 5)
    amount = basic * 12 / 365 * days
    return round(days, 2), round(min(amount, basic * 24), 2)


def r_gratuity(run):
    emps = list(run.employees)
    basic = basic_by_employee([e.id for e in emps])
    rows = []
    for e in emps:
        yrs = years_between(e.emp_joined_date, run.today) or 0
        days, amount = gratuity_uae(yrs, basic.get(e.id, 0))
        rows.append({**emp_cols(e), 'joined': e.emp_joined_date, 'years': yrs, 'basic': money(basic.get(e.id, 0)), 'days': days, 'amount': amount, **src(e),
                     '_cell': {'basic': drill('salary', {'employee': full_name(e)})}})
    return EMP_COLS + [('joined', 'Joining date', 'date'), ('years', 'Service (years)', 'number'), ('basic', 'Monthly basic (AED)', 'number'),
                       ('days', 'Gratuity days', 'number'), ('amount', 'Gratuity accrued (AED)', 'number')], rows


# ------------------------------------------------------------------ exit, requests, assets
def r_exits(run):
    rows = []
    for r in M('EmpManagement', 'EmployeeResignation').objects.filter(run.emp_q()).filter(
            Q(resigned_on__gte=run.date_from, resigned_on__lte=run.date_to) | Q(last_working_date__gte=run.date_from, last_working_date__lte=run.date_to)).select_related(
            'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        eos = getattr(r, 'eos', None)
        try:
            eos = r.eos
        except Exception:
            eos = None
        total = sum(money(getattr(eos, k, 0)) for k in ('gratuity_amount', 'notice_pay', 'final_month_salary', 'air_ticket')) if eos else ''
        rows.append({**emp_cols(r.employee), 'number': r.document_number or '', 'type': nice(r.termination_type or ''), 'resigned': r.resigned_on,
                     'notice': r.notice_period or 0, 'last_day': r.last_working_date, 'service': years_between(r.employee.emp_joined_date, r.last_working_date or r.resigned_on) or '',
                     'status': nice(r.status), 'gratuity': money(eos.gratuity_amount) if eos else '', 'settlement': round(total, 2) if total != '' else '',
                     'eos_status': nice(eos.status) if eos else 'Not started', **src(r),
                     '_cell': {k: src(eos) for k in ('gratuity', 'settlement', 'eos_status')} if eos else {}})
    return EMP_COLS + [('number', 'Document', 'text'), ('type', 'Type', 'text'), ('resigned', 'Resigned on', 'date'), ('notice', 'Notice (days)', 'number'),
                       ('last_day', 'Last working day', 'date'), ('service', 'Service (years)', 'number'), ('status', 'Status', 'text'),
                       ('gratuity', 'Gratuity (AED)', 'number'), ('settlement', 'Final settlement (AED)', 'number'), ('eos_status', 'End of service', 'text')], rows


def r_general_requests(run):
    rows = [{**emp_cols(g.employee), 'number': g.document_number or '', 'type': str(g.request_type or ''), 'reason': g.reason or '',
             'amount': money(g.total) if g.total is not None else '', 'status': nice(g.status), 'processed': nice(bool(g.is_processed)),
             'requested': g.created_at_date, 'remarks': g.remarks or '', **src(g)}
            for g in M('EmpManagement', 'GeneralRequest').objects.filter(run.emp_q()).filter(run.in_period('created_at_date')).select_related(
                'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'request_type')]
    return EMP_COLS + [('number', 'Document', 'text'), ('type', 'Request type', 'text'), ('reason', 'Reason', 'text'), ('amount', 'Amount', 'number'),
                       ('status', 'Status', 'text'), ('processed', 'Processed', 'text'), ('requested', 'Requested on', 'date'), ('remarks', 'Remarks', 'text')], rows


def r_assets(run):
    holder = {}
    for a in M('OrganisationManager', 'AssetAllocation').objects.filter(returned_date__isnull=True).select_related('employee'):
        holder[a.asset_id] = a
    vis = set(run.all_employees.values_list('id', flat=True))
    rows = []
    for a in M('OrganisationManager', 'Asset').objects.select_related('asset_type'):
        h = holder.get(a.id)
        if run.branches is not None and h and h.employee_id not in vis:
            continue
        rows.append({'type': str(a.asset_type or ''), 'name': a.name, 'serial': a.serial_number or '', 'model': a.model or '', 'purchased': a.purchase_date,
                     'status': nice(a.status), 'condition': nice(a.condition), 'held_by': full_name(h.employee) if h else '', 'since': h.assigned_date if h else '', **src(a),
                     '_emp': h.employee_id if h else None, 'held': 'Yes' if h else 'No', '_cell': {'since': src(h)} if h else {}})
    return [('type', 'Asset type', 'text'), ('name', 'Asset', 'text'), ('serial', 'Serial number', 'text'), ('model', 'Model', 'text'),
            ('purchased', 'Purchase date', 'date'), ('status', 'Status', 'text'), ('condition', 'Condition', 'text'), ('held_by', 'Held by', 'text'),
            ('since', 'Since', 'date')], rows


def r_asset_transactions(run):
    rows = []
    for a in M('OrganisationManager', 'AssetAllocation').objects.filter(run.emp_q()).select_related(
            'asset', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        rows.append({**emp_cols(a.employee), 'asset': str(a.asset), 'serial': a.asset.serial_number or '', 'assigned': a.assigned_date, 'returned': a.returned_date or '',
                     'days': ((a.returned_date or run.today) - a.assigned_date).days if a.assigned_date else '', 'condition': nice(a.return_condition or ''),
                     'state': 'Returned' if a.returned_date else 'With employee', **src(a), '_cell': {'asset': src(a.asset), 'serial': src(a.asset)}})
    rows.sort(key=lambda r: r['assigned'] or date.min)
    return EMP_COLS + [('asset', 'Asset', 'text'), ('serial', 'Serial number', 'text'), ('assigned', 'Assigned', 'date'), ('returned', 'Returned', 'date'),
                       ('days', 'Days held', 'number'), ('condition', 'Return condition', 'text'), ('state', 'Now', 'text')], rows


# ------------------------------------------------------------------ talent, projects
def r_appraisals(run):
    rows = []
    for g in M('PerformanceManagement', 'GoalSheet').objects.filter(run.emp_q()).select_related(
            'cycle', 'manager', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        try:
            o = g.outcome
        except Exception:
            o = None
        rows.append({**emp_cols(g.employee), 'cycle': str(g.cycle or ''), 'manager': full_name(g.manager) if g.manager_id and hasattr(g.manager, 'emp_code') else str(g.manager or ''),
                     'status': nice(g.status), 'self': money(g.self_score) if g.self_score is not None else '', 'mgr': money(g.manager_score) if g.manager_score is not None else '',
                     'rating': g.final_rating or g.proposed_rating or '', 'increment': money(o.increment_percent) if o and o.increment_percent is not None else '',
                     'new_basic': money(o.new_basic) if o and o.new_basic is not None else '', 'bonus': money(o.bonus_amount) if o and o.bonus_amount is not None else '',
                     'effective': o.effective_date if o else '', **src(g), 'cycle_name': g.cycle.name if g.cycle_id else '',
                     'reviewed': 'Yes' if g.status in ('reviewed', 'calibrated', 'approved', 'acknowledged', 'completed') else 'No', '_cell': {k: src(o) for k in ('increment', 'new_basic', 'bonus', 'effective')} if o else {}})
    return EMP_COLS + [('cycle', 'Cycle', 'text'), ('manager', 'Manager', 'text'), ('status', 'Status', 'text'), ('self', 'Self score', 'number'),
                       ('mgr', 'Manager score', 'number'), ('rating', 'Final rating', 'number'), ('increment', 'Increment %', 'number'),
                       ('new_basic', 'New basic (AED)', 'number'), ('bonus', 'Bonus (AED)', 'number'), ('effective', 'Effective', 'date')], rows


def r_recruitment(run):
    stages = defaultdict(lambda: defaultdict(int))
    for a in M('RecruitmentManagement', 'Application').objects.values('job_id', 'stage'):
        stages[a['job_id']][a['stage']] += 1
    rows = []
    qs = M('RecruitmentManagement', 'JobOpening').objects.select_related('branch', 'department', 'designation')
    if run.branches is not None:
        qs = qs.filter(Q(branch__in=run.branches) | Q(branch__isnull=True))
    for j in qs:
        s = stages[j.id]
        opened = j.published_on.date() if hasattr(j.published_on, 'date') else j.published_on
        rows.append({'code': j.job_code or '', 'title': j.title, 'branch': str(j.branch or ''), 'department': str(j.department or ''), 'openings': j.openings or 0,
                     'status': nice(j.status), 'applicants': sum(s.values()), 'screening': s['screening'], 'interview': s['interview'], 'offer': s['offer'],
                     'hired': s['hired'], 'rejected': s['rejected'] + s['withdrawn'], 'published': opened or '', 'closing': j.closing_date or '',
                     'days_open': (run.today - opened).days if opened else '', 'emiratisation': nice(bool(j.is_emiratisation)), **src(j),
                     'open': 'No' if j.status in ('draft', 'filled', 'closed', 'cancelled', 'on_hold') else 'Yes'})
    return [('code', 'Job code', 'text'), ('title', 'Position', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'),
            ('openings', 'Openings', 'number'), ('status', 'Status', 'text'), ('applicants', 'Applicants', 'number'), ('screening', 'Screening', 'number'),
            ('interview', 'Interview', 'number'), ('offer', 'Offer', 'number'), ('hired', 'Hired', 'number'), ('rejected', 'Rejected / withdrawn', 'number'),
            ('published', 'Published', 'date'), ('closing', 'Closing date', 'date'), ('days_open', 'Days open', 'number'), ('emiratisation', 'Emiratisation', 'text')], rows


def r_training(run):
    rows = []
    for n in M('LearningManagement', 'Nomination').objects.filter(run.emp_q()).select_related(
            'session__course', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        s = n.session
        if s and s.start_date and not (run.date_from <= s.start_date <= run.date_to):
            continue
        try:
            r = n.result
        except Exception:
            r = None
        rows.append({**emp_cols(n.employee), 'course': str(s.course) if s else '', 'session': s.code if s else '', 'from': s.start_date if s else '',
                     'to': s.end_date if s else '', 'manager': nice(n.manager_status), 'ld': nice(n.ld_status), 'seat': nice(n.seat_status),
                     'attended': nice(bool(r.attended)) if r else '', 'score': money(r.post_test_score) if r and r.post_test_score is not None else '',
                     'passed': nice(bool(r.passed)) if r and r.passed is not None else '', 'cost': money(s.cost_per_head) if s and s.cost_per_head is not None else '', **src(n),
                     'waiting': 'Yes' if 'pending' in (n.manager_status, n.ld_status) else 'No',
                     '_cell': dict({k: src(s) for k in ('course', 'session', 'from', 'to', 'cost')} if s else {}, **({k: src(r) for k in ('attended', 'score', 'passed')} if r else {}))})
    return EMP_COLS + [('course', 'Course', 'text'), ('session', 'Session', 'text'), ('from', 'From', 'date'), ('to', 'To', 'date'),
                       ('manager', 'Manager', 'text'), ('ld', 'L&D', 'text'), ('seat', 'Seat', 'text'), ('attended', 'Attended', 'text'),
                       ('score', 'Post-test score', 'number'), ('passed', 'Passed', 'text'), ('cost', 'Cost (AED)', 'number')], rows


def r_certificates(run):
    rows = []
    for c in M('LearningManagement', 'Certificate').objects.filter(run.emp_q()).select_related(
            'course', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        days, st = _expiry_status(c.expiry_date, run.today)
        rows.append({**emp_cols(c.employee), 'title': c.title or str(c.course or ''), 'number': c.certificate_number or '', 'issued_by': c.issued_by or '',
                     'issued': c.issued_on, 'expiry': c.expiry_date, 'days_left': days, 'status': st, **src(c)})
    return EMP_COLS + [('title', 'Certificate', 'text'), ('number', 'Number', 'text'), ('issued_by', 'Issued by', 'text'), ('issued', 'Issued on', 'date'),
                       ('expiry', 'Expiry', 'date'), ('days_left', 'Days left', 'number'), ('status', 'Status', 'text')], rows


def r_timesheets(run):
    rows = [{**emp_cols(t.employee), 'project': str(t.project or ''), 'task': str(t.task or ''), 'date': t.date, 'hours': hours_of(t.time_spent),
             'status': nice(t.status), 'description': t.description or '', **src(t), '_cell': {'project': src(t.project), 'task': src(t.task)} if t.project_id else {}}
            for t in M('ProjectManagement', 'TimeSheet').objects.filter(run.emp_q()).filter(run.in_period('date')).select_related(
                'project', 'task', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id').order_by('date')]
    return EMP_COLS + [('project', 'Project', 'text'), ('task', 'Task', 'text'), ('date', 'Date', 'date'), ('hours', 'Hours', 'number'),
                       ('status', 'Status', 'text'), ('description', 'Description', 'text')], rows


# ------------------------------------------------------------------ v1.12: people analytics, time, payroll cost, recruitment and performance reports
SEL = ('employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id')
GENDER = {'M': 'Male', 'F': 'Female', 'O': 'Other'}
MARITAL = {'M': 'Married', 'S': 'Single', 'D': 'Divorced', 'divorced': 'Divorced', 'W': 'Widowed', 'widow': 'Widowed'}


def is_national(e):
    """UAE national (Emirati) – GPSSA pension and Emiratisation."""
    n = str(getattr(e, 'emp_nationality', '') or '').upper()
    return 'EMIRAT' in n or n in ('UAE', 'UNITED ARAB EMIRATES')


def gross_by_employee(emp_ids):
    """Monthly gross (every addition in the active salary structure) per employee."""
    out = defaultdict(float)
    for r in M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee_id__in=emp_ids, is_active=True).select_related('component'):
        if r.component.component_type != 'deduction':
            out[r.employee_id] += float(r.amount or 0)
    return {e: out.get(e, 0) for e in emp_ids}


def _months(d_from, d_to):
    """First day of every month from d_from to d_to."""
    out, m = [], d_from.replace(day=1)
    while m <= d_to:
        out.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return out


def _per(d):
    return {'from': d.replace(day=1), 'to': month_end(d)}


def day_statuses(emps, d_from, d_to, today):
    """Bulk day status per employee (same rules as the attendance report: weekend → holiday → present → on leave → absent).
    Days before joining and after yesterday are left out. No weekend calendar assigned → Saturday / Sunday."""
    from calendars.utils import get_employee_holidays, get_employee_weekend_days
    emps = list(emps)
    ids = [e.id for e in emps]
    last_day = min(d_to, today - timedelta(days=1))
    out = {}
    if not emps or last_day < d_from:
        return out
    present = set(M('calendars', 'Attendance').objects.filter(employee_id__in=ids, date__gte=d_from, date__lte=last_day).values_list('employee_id', 'date'))
    leave, pending = {}, {}
    for l in M('calendars', 'employee_leave_request').objects.filter(employee_id__in=ids, status__in=('approved', 'pending'), start_date__lte=last_day,
                                                                      end_date__gte=d_from).select_related('leave_type'):
        d = max(l.start_date, d_from)
        while d <= min(l.end_date, last_day):
            (leave if l.status == 'approved' else pending)[(l.employee_id, d)] = l
            d += timedelta(days=1)
    wk_cache = {}
    for e in emps:
        key = (e.emp_branch_id_id, e.emp_dept_id_id)
        try:
            wk = get_employee_weekend_days(e)
        except Exception:
            wk = set()
        wk = wk or {'Saturday', 'Sunday'}
        try:
            hol = get_employee_holidays(e, d_from, last_day)
        except Exception:
            hol = set()
        first = max(d_from, e.emp_joined_date) if e.emp_joined_date else d_from
        days = []
        d = first
        while d <= last_day:
            if d.strftime('%A') in wk:
                st = 'Weekend'
            elif d in hol:
                st = 'Holiday'
            elif (e.id, d) in present:
                st = 'Present'
            elif (e.id, d) in leave:
                st = 'On Leave'
            else:
                st = 'Absent'
            days.append({'date': d, 'status': st, 'leave': leave.get((e.id, d)), 'pending': pending.get((e.id, d))})
            d += timedelta(days=1)
        out[e.id] = days
        wk_cache[key] = wk
    return out


def r_location(run):
    groups = {}
    for e in run.employees:
        b = e.emp_branch_id.branch_name if e.emp_branch_id_id else '(no branch)'
        w = e.work_location.branch_name if getattr(e, 'work_location_id', None) else b
        g = groups.setdefault((b, w), {'branch': b, 'work_location': w, 'active': 0, 'male': 0, 'female': 0, 'nationals': 0, 'expats': 0, 'joined': 0})
        g['active'] += 1
        if e.emp_gender == 'M':
            g['male'] += 1
        elif e.emp_gender == 'F':
            g['female'] += 1
        g['nationals' if is_national(e) else 'expats'] += 1
        if e.emp_joined_date and run.date_from <= e.emp_joined_date <= run.date_to:
            g['joined'] += 1
    total = sum(g['active'] for g in groups.values()) or 1
    rows = sorted(groups.values(), key=lambda g: (g['branch'], g['work_location']))
    for g in rows:
        g['share'] = round(100 * g['active'] / total, 1)
        g['emiratisation'] = round(100 * g['nationals'] / g['active'], 1) if g['active'] else 0
        org = {'branch': g['branch']}
        g['_drill'] = drill('employees', dict(org, status='Active'))
        g['_cell'] = {'active': g['_drill'], 'male': drill('employees', dict(org, status='Active', gender='Male')),
                      'female': drill('employees', dict(org, status='Active', gender='Female'))}
    return [('branch', 'Branch', 'text'), ('work_location', 'Work location', 'text'), ('active', 'Active employees', 'number'), ('share', 'Share %', 'number'),
            ('male', 'Male', 'number'), ('female', 'Female', 'number'), ('nationals', 'UAE nationals', 'number'), ('expats', 'Expatriates', 'number'),
            ('emiratisation', 'Emiratisation %', 'number'), ('joined', 'Joined in period', 'number')], rows


def _age_band(dob, today):
    if not dob:
        return 'Unknown'
    a = int(years_between(dob, today) or 0)
    return 'Under 25' if a < 25 else '25–34' if a < 35 else '35–44' if a < 45 else '45–54' if a < 55 else '55 and over'


AGE_ORDER = ['Under 25', '25–34', '35–44', '45–54', '55 and over', 'Unknown']


def r_demographics(run):
    emps = list(run.employees)
    total = len(emps)
    dims = [('Age band', lambda e: _age_band(e.emp_date_of_birth, run.today), None),
            ('Gender', lambda e: GENDER.get(e.emp_gender or '', 'Not set'), 'gender'),
            ('Nationality', lambda e: str(e.emp_nationality) if e.emp_nationality_id else 'Not set', 'nationality'),
            ('UAE national', lambda e: 'UAE national' if is_national(e) else 'Expatriate', None),
            ('Marital status', lambda e: MARITAL.get(e.emp_marital_status or '', 'Not set'), None),
            ('Religion', lambda e: str(e.emp_relegion) if getattr(e, 'emp_relegion_id', None) else 'Not set', None),
            ('Service', lambda e: (lambda y: 'Under 1 year' if y < 1 else '1–3 years' if y < 3 else '3–5 years' if y < 5 else '5–10 years' if y < 10 else '10 years and over')(years_between(e.emp_joined_date, run.today) or 0) if e.emp_joined_date else 'Unknown', None)]
    rows = []
    for dim, fn, fcol in dims:
        c, male, female, ages = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(list)
        for e in emps:
            k = fn(e)
            c[k] += 1
            male[k] += e.emp_gender == 'M'
            female[k] += e.emp_gender == 'F'
            if e.emp_date_of_birth:
                ages[k].append(years_between(e.emp_date_of_birth, run.today))
        keys = sorted(c, key=lambda k: (AGE_ORDER.index(k) if k in AGE_ORDER else 99, -c[k], k)) if dim == 'Age band' else sorted(c, key=lambda k: (-c[k], k))
        for k in keys:
            r = {'dimension': dim, 'group': k, 'count': c[k], 'pct': round(100 * c[k] / total, 1) if total else 0, 'male': male[k], 'female': female[k],
                 'avg_age': round(sum(ages[k]) / len(ages[k]), 1) if ages[k] else ''}
            if fcol and k != 'Not set':
                r['_drill'] = drill('employees', {fcol: k, 'status': 'Active'})
            rows.append(r)
    return ([('dimension', 'Dimension', 'text'), ('group', 'Group', 'text'), ('count', 'Employees', 'number'), ('pct', 'Share %', 'number'),
             ('male', 'Male', 'number'), ('female', 'Female', 'number'), ('avg_age', 'Average age', 'number')], rows,
            {'no_totals': ['count', 'male', 'female'], 'notes': [f'{total} active employees; each dimension adds up to this headcount.']})


def _headcount_on(emps, left, d):
    n = 0
    for e in emps:
        if e.emp_joined_date and e.emp_joined_date > d:
            continue
        lv = left.get(e.id)
        if lv and lv[0] and lv[0] <= d:     # employed up to and including the last working day; gone at the end of it
            continue
        if not e.is_active and not lv:
            continue
        n += 1
    return n


def turnover_months(run, months):
    """Monthly opening / joined / left / closing headcount and turnover % for the visible employees."""
    emps = list(run.all_employees)
    left = _left(run)
    out = []
    for m0 in months:
        m1 = month_end(m0)
        opening = _headcount_on(emps, left, m0 - timedelta(days=1))
        joined = sum(1 for e in emps if e.emp_joined_date and m0 <= e.emp_joined_date <= m1)
        lft = sum(1 for e in emps if left.get(e.id) and left[e.id][0] and m0 <= left[e.id][0] <= m1)
        closing = _headcount_on(emps, left, m1)
        avg = (opening + closing) / 2
        out.append({'month': m0.strftime('%b %Y'), 'm0': m0, 'opening': opening, 'joined': joined, 'left': lft, 'closing': closing, 'average': round(avg, 1),
                    'turnover': round(100 * lft / avg, 2) if avg else 0})
    return out


def r_turnover_trend(run):
    last = min(run.date_to, run.today)
    rows = []
    for r in turnover_months(run, _months(max(run.date_from, last - timedelta(days=366 * 3)), last)):
        r['annualised'] = round(r['turnover'] * 12, 1)
        per = _per(r.pop('m0'))
        r['_cell'] = {'joined': drill('joiners-leavers', {'event': 'Joined'}, **per), 'left': drill('joiners-leavers', {'event': 'Left'}, **per)}
        rows.append(r)
    tot_left = sum(r['left'] for r in rows)
    avg = sum(r['average'] for r in rows) / len(rows) if rows else 0
    note = f'Turnover in the period: {tot_left} leavers / average headcount {avg:.1f} = {100 * tot_left / avg:.1f}%.' if avg else ''
    return ([('month', 'Month', 'text'), ('opening', 'Opening headcount', 'number'), ('joined', 'Joined', 'number'), ('left', 'Left', 'number'),
             ('closing', 'Closing headcount', 'number'), ('average', 'Average headcount', 'number'), ('turnover', 'Turnover %', 'number'),
             ('annualised', 'Annualised turnover %', 'number')], rows, {'no_totals': ['opening', 'closing', 'average'], 'notes': [note] if note else []})


def _att_rows(run, extra_q=Q()):
    if (run.date_to - run.date_from).days > 92:
        raise ValueError('Choose at most 3 months.')
    return M('calendars', 'Attendance').objects.filter(run.emp_q(), run.in_period('date')).filter(extra_q).select_related('shift', *SEL).order_by('date', 'employee__emp_code')


def r_missing_punch(run):
    """Check-in without check-out (or the reverse). Today is shown as 'Still at work' – not yet a missing punch."""
    rows = []
    for a in _att_rows(run, Q(check_in_time__isnull=True) | Q(check_out_time__isnull=True)):
        if a.check_in_time and not a.check_out_time:
            issue = 'Still at work' if a.date >= run.today else 'No check-out'
        elif a.check_out_time and not a.check_in_time:
            issue = 'No check-in'
        else:
            issue = 'No punches'
        rows.append({**emp_cols(a.employee), 'date': a.date, 'shift': a.shift.name if a.shift_id else '', 'in': a.check_in_time or '', 'out': a.check_out_time or '',
                     'issue': issue, **src(a)})
    return EMP_COLS + [('date', 'Date', 'date'), ('shift', 'Shift', 'text'), ('in', 'Check in', 'text'), ('out', 'Check out', 'text'), ('issue', 'Issue', 'text')], rows


def _late_early(run, early):
    from .services import ShiftResolver, minutes_early, minutes_late
    att = list(_att_rows(run, Q(check_in_time__isnull=False)))
    res = ShiftResolver({a.employee_id for a in att}, run.date_from, run.date_to)
    rows = []
    for a in att:
        sh = res.shift(a.employee_id, a.date, a)
        m = minutes_early(a, sh) if early else minutes_late(a, sh)
        if not m:
            continue
        rows.append({**emp_cols(a.employee), 'date': a.date, 'shift': getattr(sh, 'name', '') or 'Default 09:00–18:00',
                     'planned': (getattr(sh, 'end_time', None) if early else getattr(sh, 'start_time', None)) or ('18:00' if early else '09:00'),
                     'punch': a.check_out_time if early else a.check_in_time, 'minutes': m, 'hours': round(m / 60, 2), **src(a)})
    return rows


def r_late_arrivals(run):
    rows = _late_early(run, early=False)
    return EMP_COLS + [('date', 'Date', 'date'), ('shift', 'Shift', 'text'), ('planned', 'Shift start', 'text'), ('punch', 'Checked in', 'text'),
                       ('minutes', 'Minutes late', 'number'), ('hours', 'Hours late', 'number')], rows


def r_early_departures(run):
    rows = _late_early(run, early=True)
    return EMP_COLS + [('date', 'Date', 'date'), ('shift', 'Shift', 'text'), ('planned', 'Shift end', 'text'), ('punch', 'Checked out', 'text'),
                       ('minutes', 'Minutes early', 'number'), ('hours', 'Hours early', 'number')], rows


def r_absence(run):
    if (run.date_to - run.date_from).days > 92:
        raise ValueError('Choose at most 3 months.')
    rows = []
    days = day_statuses(run.employees, run.date_from, run.date_to, run.today)
    for e in run.employees:
        for d in days.get(e.id, []):
            if d['status'] != 'Absent':
                continue
            p = d['pending']
            rows.append({**emp_cols(e), 'date': d['date'], 'weekday': d['date'].strftime('%A'), 'days': 1,
                         'note': f"Leave pending approval ({p.leave_type})" if p else 'No punch and no leave', **(src(p) if p else src(e))})
    return EMP_COLS + [('date', 'Date', 'date'), ('weekday', 'Day', 'text'), ('days', 'Days absent', 'number'), ('note', 'Note', 'text')], rows


def r_department_leave(run):
    g = {}
    for l in M('calendars', 'employee_leave_request').objects.filter(run.emp_q(), run.in_period('start_date')).select_related('leave_type', 'employee__emp_dept_id', 'employee__emp_branch_id'):
        dept = l.employee.emp_dept_id.dept_name if l.employee.emp_dept_id_id else '(no department)'
        k = (dept, str(l.leave_type or ''))
        r = g.setdefault(k, {'department': k[0], 'type': k[1], 'requests': 0, 'emps': set(), 'approved': 0.0, 'pending': 0.0, 'rejected': 0})
        r['requests'] += 1
        r['emps'].add(l.employee_id)
        days = float(l.approved_days or l.applied_days or l.number_of_days or 0)
        if l.status == 'approved':
            r['approved'] += days
        elif l.status == 'pending':
            r['pending'] += float(l.applied_days or l.number_of_days or 0)
        else:
            r['rejected'] += 1
    heads = defaultdict(int)
    for e in run.employees:
        heads[e.emp_dept_id.dept_name if e.emp_dept_id_id else '(no department)'] += 1
    rows = []
    per = {'from': run.date_from, 'to': run.date_to}
    for k in sorted(g):
        r = g[k]
        n = len(r.pop('emps'))
        rows.append({**r, 'employees': n, 'approved': round(r['approved'], 2), 'pending': round(r['pending'], 2), 'headcount': heads.get(k[0], 0),
                     'per_head': round(r['approved'] / heads[k[0]], 2) if heads.get(k[0]) else '',
                     '_drill': drill('leave', {'department': k[0], 'type': k[1]}, **per),
                     '_cell': {'approved': drill('leave', {'department': k[0], 'type': k[1], 'status': 'Approved'}, **per),
                               'pending': drill('leave', {'department': k[0], 'type': k[1], 'status': 'Pending'}, **per)}})
    return [('department', 'Department', 'text'), ('type', 'Leave type', 'text'), ('requests', 'Requests', 'number'), ('employees', 'Employees on leave', 'number'),
            ('approved', 'Days approved', 'number'), ('pending', 'Days pending', 'number'), ('rejected', 'Rejected requests', 'number'),
            ('headcount', 'Department headcount', 'number'), ('per_head', 'Approved days per head', 'number')], rows, {'no_totals': ['headcount', 'per_head', 'employees']}


def _register(run, kind):
    comps, rows = [], []
    for p in _payslips(run, components=True):
        pr = p.payroll_run
        r = {**emp_cols(p.employee), 'run': pr.name, 'period': date(pr.year, pr.month, 1).strftime('%b %Y'), **src(p)}
        if kind == 'allowance':
            r['basic'] = 0
        total = 0
        for c in p.components.all():
            ct = c.component.component_type
            if (kind == 'deduction') != (ct == 'deduction'):
                continue
            if kind == 'allowance' and ct != 'addition':
                continue
            if kind == 'allowance' and c.component.payroll_category == 'basic':
                r['basic'] = round(r['basic'] + money(c.amount), 2)
                continue
            n = c.component.name
            if n not in comps:
                comps.append(n)
            r['c_' + n] = round(r.get('c_' + n, 0) + money(c.amount), 2)
            total += money(c.amount)
        if total == 0 and not r.get('basic'):
            continue
        r['total'] = round(total, 2)
        r['gross'] = money(p.gross_salary)
        rows.append(r)
    order = sorted(comps, key=lambda n: (n.lower() != 'basic', n))
    for r in rows:
        for n in order:
            r.setdefault('c_' + n, 0)
    label = 'Total deductions (AED)' if kind == 'deduction' else 'Total allowances (AED)'
    lead = [('basic', 'Basic (AED)', 'number')] if kind == 'allowance' else []
    return EMP_COLS + [('run', 'Payroll run', 'text'), ('period', 'Period', 'text')] + lead + [('c_' + n, n + ' (AED)', 'number') for n in order] + \
        [('total', label, 'number'), ('gross', 'Gross (AED)', 'number')], rows


def r_allowance_register(run):
    return _register(run, 'allowance')


def r_deduction_register(run):
    return _register(run, 'deduction')


def ot_hourly(basic):
    """UAE labour law: hourly wage on basic (basic × 12 / 365 / 8)."""
    return basic * 12 / 365 / 8


OT_FACTOR = {'NORMAL': 1.25, 'WEEKEND': 1.5, 'HOLIDAY': 1.5}


def r_overtime_pay(run):
    """Overtime per employee and month: hours by type, the pay due by law (125% normal, 150% rest day / holiday on basic)
    and what the payroll paid as an overtime component."""
    agg = {}
    emps = {}
    for o in M('calendars', 'EmployeeOvertime').objects.filter(run.emp_q(), run.in_period('date')).select_related(*SEL):
        k = (o.employee_id, o.date.replace(day=1))
        emps[o.employee_id] = o.employee
        a = agg.setdefault(k, {'normal': 0.0, 'rest': 0.0, 'approved': 0.0})
        h = float(o.hours or 0)
        a['normal' if o.ot_type == 'NORMAL' else 'rest'] += h
        a['approved'] += h if o.approved else 0
    paid = defaultdict(float)
    for p in _payslips(run, components=True):
        for c in p.components.all():
            if c.component.payroll_category == 'overtime' and c.component.component_type == 'addition':
                k = (p.employee_id, date(p.payroll_run.year, p.payroll_run.month, 1))
                paid[k] += money(c.amount)
                emps[p.employee_id] = p.employee
                agg.setdefault(k, {'normal': 0.0, 'rest': 0.0, 'approved': 0.0})
    basic = basic_by_employee(list(emps))
    rows = []
    for (eid, m0), a in sorted(agg.items(), key=lambda x: (x[0][1], emps[x[0][0]].emp_code)):
        rate = ot_hourly(basic.get(eid, 0))
        due = rate * (a['normal'] * OT_FACTOR['NORMAL'] + a['rest'] * OT_FACTOR['WEEKEND'])
        e = emps[eid]
        rows.append({**emp_cols(e), 'month': m0.strftime('%b %Y'), 'normal': round(a['normal'], 2), 'rest': round(a['rest'], 2), 'hours': round(a['normal'] + a['rest'], 2),
                     'approved': round(a['approved'], 2), 'rate': round(rate, 2), 'due': round(due, 2), 'paid': round(paid.get((eid, m0), 0), 2),
                     'difference': round(paid.get((eid, m0), 0) - due, 2), **src(e),
                     '_cell': {k: drill('overtime', {'employee': full_name(e)}, **_per(m0)) for k in ('normal', 'rest', 'hours', 'approved')}})
    return EMP_COLS + [('month', 'Month', 'text'), ('normal', 'Normal OT hours', 'number'), ('rest', 'Rest day / holiday OT hours', 'number'),
                       ('hours', 'Total OT hours', 'number'), ('approved', 'Approved hours', 'number'), ('rate', 'Hourly basic (AED)', 'number'),
                       ('due', 'OT pay by law (AED)', 'number'), ('paid', 'OT paid in payroll (AED)', 'number'), ('difference', 'Paid − due (AED)', 'number')], rows, \
        {'no_totals': ['rate'], 'notes': ['OT pay by law = hourly basic × 125% (normal days) or 150% (rest days and public holidays). Hourly basic = basic × 12 / 365 / 8.']}


PENSION_EMPLOYER = 0.125   # GPSSA employer share for UAE nationals (of the contributory salary)
PENSION_CATS = ('basic', 'hra', 'housing_allowance', 'transport_allowance')


def employer_cost(p, struct_basic=0.0):
    """One payslip → gross, employer-side monthly accruals (gratuity, leave salary, GPSSA pension) and total labour cost."""
    comps = list(p.components.all())
    basic = sum(money(c.amount) for c in comps if c.component.payroll_category == 'basic' and c.component.component_type != 'deduction') or struct_basic
    pr = p.payroll_run
    m1 = month_end(date(pr.year, pr.month, 1))
    yrs = years_between(p.employee.emp_joined_date, m1) or 0
    daily = basic * 12 / 365
    gratuity = daily * (21 if yrs < 5 else 30) / 12
    leave = daily * 30 / 12
    contributory = sum(money(c.amount) for c in comps if c.component.payroll_category in PENSION_CATS and c.component.component_type != 'deduction') or basic
    pension = contributory * PENSION_EMPLOYER if is_national(p.employee) else 0
    gross = money(p.gross_salary)
    return {'basic': round(basic, 2), 'gross': gross, 'gratuity': round(gratuity, 2), 'leave_accrual': round(leave, 2), 'pension': round(pension, 2),
            'cost': round(gross + gratuity + leave + pension, 2)}


def r_payroll_cost(run):
    slips = _payslips(run, components=True)
    sb = basic_by_employee(list({p.employee_id for p in slips}))
    rows = []
    for p in slips:
        pr = p.payroll_run
        rows.append({**emp_cols(p.employee), 'run': pr.name, 'period': date(pr.year, pr.month, 1).strftime('%b %Y'), 'net': money(p.net_salary),
                     **employer_cost(p, sb.get(p.employee_id, 0)), **src(p)})
    return EMP_COLS + [('run', 'Payroll run', 'text'), ('period', 'Period', 'text'), ('basic', 'Basic (AED)', 'number'), ('gross', 'Gross (AED)', 'number'),
                       ('net', 'Net (AED)', 'number'), ('gratuity', 'Gratuity accrual (AED)', 'number'), ('leave_accrual', 'Leave salary accrual (AED)', 'number'),
                       ('pension', 'Employer pension – GPSSA (AED)', 'number'), ('cost', 'Total employer cost (AED)', 'number')], rows, \
        {'notes': ['Employer cost = gross + gratuity accrual (21 days’ basic a year, 30 after 5 years) + leave salary accrual (30 days’ basic a year) '
                   '+ GPSSA employer share 12.5% for UAE nationals.']}


def r_department_cost(run):
    slips = _payslips(run, components=True)
    sb = basic_by_employee(list({p.employee_id for p in slips}))
    g = {}
    for p in slips:
        pr = p.payroll_run
        dept = p.employee.emp_dept_id.dept_name if p.employee.emp_dept_id_id else '(no department)'
        br = p.employee.emp_branch_id.branch_name if p.employee.emp_branch_id_id else ''
        k = (date(pr.year, pr.month, 1), br, dept)
        r = g.setdefault(k, {'period': k[0].strftime('%b %Y'), 'branch': br, 'department': dept, 'employees': set(), 'gross': 0, 'net': 0, 'gratuity': 0,
                             'leave_accrual': 0, 'pension': 0, 'cost': 0})
        ec = employer_cost(p, sb.get(p.employee_id, 0))
        r['employees'].add(p.employee_id)
        r['net'] += money(p.net_salary)
        for f in ('gross', 'gratuity', 'leave_accrual', 'pension', 'cost'):
            r[f] += ec[f]
    rows = []
    for k in sorted(g):
        r = g[k]
        n = len(r.pop('employees'))
        r = {f: (round(v, 2) if isinstance(v, float) else v) for f, v in r.items()}
        r.update({'employees': n, 'per_head': round(r['cost'] / n, 2) if n else 0,
                  '_drill': drill('payroll-cost', {'department': k[2], 'branch': k[1], 'period': r['period']}, **_per(k[0]))})
        rows.append(r)
    return [('period', 'Period', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'), ('employees', 'Employees paid', 'number'),
            ('gross', 'Gross (AED)', 'number'), ('net', 'Net (AED)', 'number'), ('gratuity', 'Gratuity accrual (AED)', 'number'),
            ('leave_accrual', 'Leave salary accrual (AED)', 'number'), ('pension', 'Employer pension (AED)', 'number'),
            ('cost', 'Total employer cost (AED)', 'number'), ('per_head', 'Cost per employee (AED)', 'number')], rows, {'no_totals': ['per_head']}


def _period_amounts(slips):
    """(year, month) → employee → component → amount (+ '__gross', '__net'), and employee objects."""
    out, emps, src_ = defaultdict(lambda: defaultdict(lambda: defaultdict(float))), {}, {}
    for p in slips:
        k = (p.payroll_run.year, p.payroll_run.month)
        emps[p.employee_id] = p.employee
        src_[(k, p.employee_id)] = p
        for c in p.components.all():
            out[k][p.employee_id][c.component.name] += money(c.amount)
        out[k][p.employee_id]['__gross'] += money(p.gross_salary)
        out[k][p.employee_id]['__net'] += money(p.net_salary)
    return out, emps, src_


def r_payroll_variance(run):
    """Each employee and component: this month against the month before, with the change and % change."""
    import copy
    wide = copy.copy(run)
    wide.date_from, wide.date_to = date(1900, 1, 1), run.date_to
    data, emps, slips = _period_amounts(_payslips(wide, components=True))
    periods = sorted(k for k in data if date(k[0], k[1], 1) <= run.date_to)
    cur = next((k for k in reversed(periods) if month_end(date(k[0], k[1], 1)) >= run.date_from), None)
    if cur is None:
        return [('employee', 'Employee', 'text')], [], {'notes': ['No payroll in the period.']}
    prev = next((k for k in reversed(periods) if k < cur), None)
    cur_l = date(cur[0], cur[1], 1).strftime('%b %Y')
    prev_l = date(prev[0], prev[1], 1).strftime('%b %Y') if prev else '—'
    rows = []
    for eid in sorted(set(data[cur]) | set(data.get(prev, {})), key=lambda i: emps[i].emp_code):
        c_, p_ = data[cur].get(eid, {}), (data[prev].get(eid, {}) if prev else {})
        note = 'Not paid this month' if eid not in data[cur] else ('First payroll' if prev and eid not in data[prev] else '')
        names = sorted((set(c_) | set(p_)) - {'__gross', '__net'}, key=lambda n: (n.lower() != 'basic', n)) + ['__gross', '__net']
        for n in names:
            a, b = round(p_.get(n, 0), 2), round(c_.get(n, 0), 2)
            ch = round(b - a, 2)
            rows.append({**emp_cols(emps[eid]), 'component': {'__gross': 'Gross', '__net': 'Net'}.get(n, n), 'line': 'Total' if n.startswith('__') else 'Component',
                         'previous': a, 'current': b, 'change': ch, 'pct': round(100 * ch / a, 1) if a else '',
                         'changed': 'Yes' if ch else 'No', 'note': note,
                         **src(slips.get((cur, eid)) or slips.get((prev, eid)))})
    return EMP_COLS + [('component', 'Component', 'text'), ('line', 'Line', 'text'), ('previous', f'{prev_l} (AED)', 'number'), ('current', f'{cur_l} (AED)', 'number'),
                       ('change', 'Change (AED)', 'number'), ('pct', 'Change %', 'number'), ('changed', 'Changed', 'text'), ('note', 'Note', 'text')], rows, \
        {'no_totals': ['previous', 'current', 'change'], 'notes': [f'{cur_l} compared with {prev_l}. Filter Line = Total for gross and net per employee.']}


# ---- recruitment
def _jobs(run):
    qs = M('RecruitmentManagement', 'JobOpening').objects.select_related('branch', 'department', 'designation', 'requisition')
    if run.branches is not None:
        qs = qs.filter(Q(branch__in=run.branches) | Q(branch__isnull=True))
    return qs


def _d(v):
    if v is None:
        return None
    return v.date() if hasattr(v, 'date') and callable(v.date) else v


def _hire_facts(run):
    """application id → facts: applied, offer (accepted on / joining), hired on (stage history), source."""
    apps_ = M('RecruitmentManagement', 'Application').objects.filter(job__in=_jobs(run)).select_related('candidate', 'job__branch', 'job__department')
    hist = defaultdict(dict)
    for h in M('RecruitmentManagement', 'ApplicationStageHistory').objects.filter(application__in=apps_).order_by('changed_at'):
        hist[h.application_id].setdefault(h.to_stage, _d(h.changed_at))
    offers = {o.application_id: o for o in M('RecruitmentManagement', 'Offer').objects.filter(application__in=apps_).select_related('employee')}
    out = {}
    for a in apps_:
        o = offers.get(a.id)
        accepted = _d(o.responded_on) if o and o.status in ('accepted', 'joined') else None
        joined = (o.employee.emp_joined_date if o and o.employee_id else (o.joining_date if o and o.status == 'joined' else None))
        hired = hist[a.id].get('hired') or (accepted if a.stage == 'hired' else None)
        out[a.id] = {'app': a, 'offer': o, 'applied': _d(a.applied_on), 'accepted': accepted, 'joined': joined, 'hired': hired,
                     'stages': set(hist[a.id]) | {a.stage}}
    return out


def r_candidates(run):
    rows = []
    for f in _hire_facts(run).values():
        a, c, o = f['app'], f['app'].candidate, f['offer']
        if not (run.date_from <= f['applied'] <= run.date_to):
            continue
        rows.append({'candidate': f'{c.first_name} {c.last_name or ""}'.strip(), 'email': c.email or '', 'phone': c.phone or '', 'gender': GENDER.get(c.gender or '', ''),
                     'nationality': str(c.nationality or ''), 'location': c.current_location or '', 'experience': money(c.experience_years) if c.experience_years is not None else '',
                     'expected': money(c.expected_salary) if c.expected_salary is not None else '', 'notice': c.notice_period_days if c.notice_period_days is not None else '',
                     'source': nice(c.source or ''), 'job': a.job.title, 'branch': str(a.job.branch or ''), 'department': str(a.job.department or ''),
                     'stage': nice(a.stage), 'applied': f['applied'], 'rating': a.rating or '', 'offer': nice(o.status) if o else '', **src(a),
                     '_cell': {'candidate': src(c), 'job': src(a.job), **({'offer': src(o)} if o else {})}})
    rows.sort(key=lambda r: r['applied'], reverse=True)
    return [('candidate', 'Candidate', 'text'), ('email', 'E-mail', 'text'), ('phone', 'Phone', 'text'), ('gender', 'Gender', 'text'), ('nationality', 'Nationality', 'text'),
            ('location', 'Current location', 'text'), ('experience', 'Experience (years)', 'number'), ('expected', 'Expected salary (AED)', 'number'),
            ('notice', 'Notice (days)', 'number'), ('source', 'Source', 'text'), ('job', 'Position', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'),
            ('stage', 'Stage', 'text'), ('applied', 'Applied on', 'date'), ('rating', 'Rating', 'number'), ('offer', 'Offer', 'text')], rows, {'no_totals': ['expected', 'experience']}


def _days(a, b):
    return (b - a).days if a and b else ''


def time_to_hire_rows(run):
    rows = []
    for f in _hire_facts(run).values():
        a = f['app']
        done = f['hired'] or f['accepted']
        if not done or not (run.date_from <= done <= run.date_to):
            continue
        c = a.candidate
        rows.append({'candidate': f'{c.first_name} {c.last_name or ""}'.strip(), 'job': a.job.title, 'branch': str(a.job.branch or ''), 'department': str(a.job.department or ''),
                     'source': nice(c.source or ''), 'applied': f['applied'], 'accepted': f['accepted'] or '', 'hired': f['hired'] or '', 'joined': f['joined'] or '',
                     'days_hire': _days(f['applied'], done), 'days_join': _days(f['applied'], f['joined']), **src(a),
                     '_cell': {'accepted': src(f['offer'])} if f['offer'] else {}})
    return rows


def r_time_to_hire(run):
    rows = time_to_hire_rows(run)
    vals = [r['days_hire'] for r in rows if r['days_hire'] != '']
    note = f'Average time to hire: {sum(vals) / len(vals):.1f} days over {len(vals)} hires.' if vals else 'No hires in the period.'
    return [('candidate', 'Candidate', 'text'), ('job', 'Position', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'), ('source', 'Source', 'text'),
            ('applied', 'Applied on', 'date'), ('accepted', 'Offer accepted', 'date'), ('hired', 'Marked hired', 'date'), ('joined', 'Joined', 'date'),
            ('days_hire', 'Days to hire', 'number'), ('days_join', 'Days to join', 'number')], rows, {'no_totals': ['days_hire', 'days_join'], 'notes': [note]}


def r_time_to_fill(run):
    facts = _hire_facts(run)
    by_job = defaultdict(list)
    for f in facts.values():
        d = f['hired'] or f['accepted']
        if d:
            by_job[f['app'].job_id].append(d)
    rows = []
    for j in _jobs(run):
        start = _d(j.requisition.approved_on) if j.requisition_id and j.requisition.approved_on else _d(j.published_on) or _d(j.created_at)
        if start and not (run.date_from <= start <= run.date_to):
            continue
        fills = sorted(by_job.get(j.id, []))
        filled = len(fills) >= (j.openings or 1) or j.status == 'filled'
        end = fills[min(len(fills), j.openings or 1) - 1] if fills else None
        rows.append({'code': j.job_code or '', 'title': j.title, 'branch': str(j.branch or ''), 'department': str(j.department or ''),
                     'requisition': j.requisition.document_number if j.requisition_id else '', 'opened': start or '', 'published': _d(j.published_on) or '',
                     'openings': j.openings or 0, 'hired': len(fills), 'filled_on': end if filled and end else '', 'status': nice(j.status),
                     'days_fill': _days(start, end) if filled and end else '', 'days_open': _days(start, run.today) if not filled else '', **src(j),
                     '_cell': {'requisition': src(j.requisition)} if j.requisition_id else {}})
    vals = [r['days_fill'] for r in rows if r['days_fill'] != '']
    note = f'Average time to fill: {sum(vals) / len(vals):.1f} days over {len(vals)} filled positions.' if vals else 'No position was filled in the period.'
    return [('code', 'Job code', 'text'), ('title', 'Position', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'),
            ('requisition', 'Requisition', 'text'), ('opened', 'Approved / opened', 'date'), ('published', 'Published', 'date'), ('openings', 'Openings', 'number'),
            ('hired', 'Hired', 'number'), ('filled_on', 'Filled on', 'date'), ('status', 'Status', 'text'), ('days_fill', 'Days to fill', 'number'),
            ('days_open', 'Days open so far', 'number')], rows, {'no_totals': ['days_fill', 'days_open'], 'notes': [note]}


def _recruitment_expenses(run):
    """Hook: recruitment costs booked in Expense management (category name / code with 'recruit'), when that module is installed."""
    try:
        Exp = M('ExpenseManagement', 'Expense')
    except LookupError:
        return None
    try:
        q = Exp.objects.filter(Q(category__name__icontains='recruit') | Q(category__code__icontains='recruit') | Q(category__name__icontains='hiring'), run.in_period('date'))
        return float(q.exclude(status__in=('rejected', 'draft')).aggregate(s=Sum('amount_aed'))['s'] or 0)
    except Exception:
        return None


def r_cost_per_hire(run):
    facts = _hire_facts(run)
    VS = M('RecruitmentManagement', 'VisaStep')
    visa = defaultdict(float)
    for v in VS.objects.filter(offer__application__in=[f['app'].id for f in facts.values()]).values('offer__application__job_id', 'cost', 'step_date'):
        visa[v['offer__application__job_id']] += float(v['cost'] or 0)
    hires, agency, package = defaultdict(int), defaultdict(int), defaultdict(float)
    for f in facts.values():
        d = f['hired'] or f['accepted']
        if d and run.date_from <= d <= run.date_to:
            j = f['app'].job_id
            hires[j] += 1
            agency[j] += f['app'].candidate.source == 'agency'
            o = f['offer']
            if o:
                package[j] += sum(money(getattr(o, k)) for k in ('basic_salary', 'housing_allowance', 'transport_allowance', 'other_allowance')) * 12
    rows = []
    for j in _jobs(run):
        if not (hires.get(j.id) or visa.get(j.id)):
            continue
        cost = visa.get(j.id, 0)
        rows.append({'code': j.job_code or '', 'title': j.title, 'branch': str(j.branch or ''), 'department': str(j.department or ''), 'hires': hires.get(j.id, 0),
                     'agency': agency.get(j.id, 0), 'visa_cost': round(cost, 2), 'cost_per_hire': round(cost / hires[j.id], 2) if hires.get(j.id) else '',
                     'package': round(package.get(j.id, 0), 2), **src(j)})
    total_h = sum(r['hires'] for r in rows)
    exp = _recruitment_expenses(run)
    notes = ['Cost = visa & onboarding step costs (Pre-onboarding & visa). Recruitment has no advert / agency fee fields: '
             'book those in Expense management under a “Recruitment” category to include them below.']
    if exp is not None:
        notes.append(f'Recruitment expenses booked in the period: AED {exp:,.2f}' + (f' → AED {(exp + sum(r["visa_cost"] for r in rows)) / total_h:,.2f} per hire overall.' if total_h else '.'))
    return [('code', 'Job code', 'text'), ('title', 'Position', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'), ('hires', 'Hires', 'number'),
            ('agency', 'Agency hires', 'number'), ('visa_cost', 'Visa & onboarding cost (AED)', 'number'), ('cost_per_hire', 'Cost per hire (AED)', 'number'),
            ('package', 'Annual package of hires (AED)', 'number')], rows, {'no_totals': ['cost_per_hire'], 'notes': notes}


def r_source_effectiveness(run):
    g = {}
    for f in _hire_facts(run).values():
        if not (run.date_from <= f['applied'] <= run.date_to):
            continue
        s = nice(f['app'].candidate.source or 'other')
        r = g.setdefault(s, {'source': s, 'cands': set(), 'applications': 0, 'screened': 0, 'interviewed': 0, 'offers': 0, 'hired': 0, 'days': []})
        r['cands'].add(f['app'].candidate_id)
        r['applications'] += 1
        st = f['stages']
        r['screened'] += bool(st & {'screening', 'interview', 'offer', 'hired'})
        r['interviewed'] += bool(st & {'interview', 'offer', 'hired'})
        r['offers'] += bool(f['offer']) or bool(st & {'offer', 'hired'})
        done = f['hired'] or f['accepted']
        if done or f['app'].stage == 'hired':
            r['hired'] += 1
            if done:
                r['days'].append((done - f['applied']).days)
    rows = []
    for s in sorted(g, key=lambda k: -g[k]['applications']):
        r = g[s]
        days = r.pop('days')
        r['candidates'] = len(r.pop('cands'))
        r['interview_rate'] = round(100 * r['interviewed'] / r['applications'], 1) if r['applications'] else 0
        r['hire_rate'] = round(100 * r['hired'] / r['applications'], 1) if r['applications'] else 0
        r['avg_days'] = round(sum(days) / len(days), 1) if days else ''
        rows.append(r)
    return [('source', 'Source', 'text'), ('candidates', 'Candidates', 'number'), ('applications', 'Applications', 'number'), ('screened', 'Screened', 'number'),
            ('interviewed', 'Interviewed', 'number'), ('offers', 'Offers', 'number'), ('hired', 'Hired', 'number'), ('interview_rate', 'Interview rate %', 'number'),
            ('hire_rate', 'Hire rate %', 'number'), ('avg_days', 'Average days to hire', 'number')], rows, {'no_totals': ['avg_days']}


# ---- performance
def _sheets(run):
    return M('PerformanceManagement', 'GoalSheet').objects.filter(run.emp_q()).select_related('cycle', *SEL).prefetch_related('goals__kpi')


def r_kpi(run):
    rows = []
    for s in _sheets(run):
        for g in s.goals.all():
            rows.append({**emp_cols(s.employee), 'cycle': s.cycle.name, 'kpi_code': g.kpi.code if g.kpi_id else '', 'kpi': g.kpi.name if g.kpi_id else '',
                         'goal': g.title, 'type': nice(g.goal_type), 'weight': money(g.weight), 'target': g.target or '', 'due': g.due_date or '',
                         'progress': g.progress_percent or 0, 'status': nice(g.progress_status or ''), 'self': g.self_rating or '', 'manager': g.manager_rating or '',
                         'cycle_name': s.cycle.name, **src(s), '_cell': {'kpi': {'_m': 'PerformanceManagement.KPI', '_id': g.kpi_id}} if g.kpi_id else {}})
    return EMP_COLS + [('cycle', 'Cycle', 'text'), ('kpi_code', 'KPI code', 'text'), ('kpi', 'KPI / competency', 'text'), ('goal', 'Goal', 'text'), ('type', 'Type', 'text'),
                       ('weight', 'Weight %', 'number'), ('target', 'Target', 'text'), ('due', 'Due', 'date'), ('progress', 'Progress %', 'number'),
                       ('status', 'Progress status', 'text'), ('self', 'Self rating', 'number'), ('manager', 'Manager rating', 'number')], rows, {'no_totals': ['weight']}


def r_goal_achievement(run):
    from .services import weighted_progress
    rows = []
    for s in _sheets(run):
        goals = list(s.goals.all())
        rows.append({**emp_cols(s.employee), 'cycle': s.cycle.name, 'stage': nice(s.status), 'goals': len(goals), 'progress': weighted_progress(goals),
                     'done': sum(1 for g in goals if g.progress_status == 'done'), 'on_track': sum(1 for g in goals if g.progress_status in ('on_track', 'ahead')),
                     'at_risk': sum(1 for g in goals if g.progress_status in ('at_risk', 'off_track')),
                     'rating': s.final_rating or s.proposed_rating or '', 'cycle_name': s.cycle.name, **src(s),
                     '_cell': {'goals': drill('kpi', {'employee': full_name(s.employee), 'cycle_name': s.cycle.name})}})
    rows.sort(key=lambda r: (r['cycle'], -r['progress']))
    return EMP_COLS + [('cycle', 'Cycle', 'text'), ('stage', 'Stage', 'text'), ('goals', 'Goals', 'number'), ('progress', 'Achievement % (weighted)', 'number'),
                       ('done', 'Goals done', 'number'), ('on_track', 'On track / ahead', 'number'), ('at_risk', 'At risk / off track', 'number'),
                       ('rating', 'Rating', 'number')], rows


def r_department_performance(run):
    from .services import weighted_progress
    g = {}
    for s in _sheets(run):
        dept = s.employee.emp_dept_id.dept_name if s.employee.emp_dept_id_id else '(no department)'
        r = g.setdefault((s.cycle.name, dept), {'cycle': s.cycle.name, 'department': dept, 'sheets': 0, 'ratings': [], 'mgr': [], 'self': [], 'prog': [],
                                                **{f'r{i}': 0 for i in range(1, 6)}})
        r['sheets'] += 1
        rt = s.final_rating or s.proposed_rating
        if rt:
            r['ratings'].append(rt)
            r[f'r{rt}'] += 1
        if s.manager_score is not None:
            r['mgr'].append(float(s.manager_score))
        if s.self_score is not None:
            r['self'].append(float(s.self_score))
        goals = list(s.goals.all())
        if goals:
            r['prog'].append(weighted_progress(goals))
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else ''
    rows = []
    for k in sorted(g):
        r = g[k]
        rows.append({'cycle': r['cycle'], 'department': r['department'], 'sheets': r['sheets'], 'rated': len(r['ratings']), 'avg_rating': avg(r['ratings']),
                     'avg_mgr': avg(r['mgr']), 'avg_self': avg(r['self']), 'avg_progress': avg(r['prog']), **{f'r{i}': r[f'r{i}'] for i in range(1, 6)},
                     '_drill': drill('appraisals', {'department': r['department'], 'cycle_name': r['cycle']})})
    return [('cycle', 'Cycle', 'text'), ('department', 'Department', 'text'), ('sheets', 'Goal sheets', 'number'), ('rated', 'Rated', 'number'),
            ('avg_rating', 'Average rating', 'number'), ('avg_mgr', 'Average manager score', 'number'), ('avg_self', 'Average self score', 'number'),
            ('avg_progress', 'Average goal achievement %', 'number')] + [(f'r{i}', f'Rated {i}', 'number') for i in range(1, 6)], rows


# ------------------------------------------------------------------ registry
# key: (title, section, description, function, period default or None, permission codes – any of them)
REPORTS = {
    'employees': ('Employees', 'People', 'Every employee with personal, job and contact details, including the form designer fields.', r_employees, None, ['view_report', 'view_emp_master']),
    'departments': ('Departments', 'People', 'Departments with their branches and number of employees.', r_departments, None, ['view_dept_report', 'view_dept_master']),
    'designations': ('Designations', 'People', 'Designations with their branches and number of employees.', r_designations, None, ['view_designtn_report', 'view_desgntn_master']),
    'categories': ('Categories', 'People', 'Categories with their branches and number of employees.', r_categories, None, ['view_ctgry_master']),
    'headcount': ('Headcount and turnover', 'People', 'Active employees by branch and department, with joiners, leavers and turnover in the period.', r_headcount, 'year', ['view_report', 'view_emp_master']),
    'joiners-leavers': ('Joiners and leavers', 'People', 'Employees who joined or left in the period.', r_joiners_leavers, 'year', ['view_report', 'view_emp_master']),
    'probation': ('Probation due', 'People', 'Confirmation dates that are overdue or coming up.', r_probation, None, ['view_report', 'view_emp_master']),
    'birthdays': ('Birthdays and work anniversaries', 'People', 'Birthdays and service anniversaries in the period.', r_birthdays, 'month', ['view_report', 'view_emp_master']),
    'documents': ('Documents and expiry', 'People', 'Employee documents with days left and expiry status.', r_documents, None, ['view_doc_report', 'view_emp_documents']),
    'leave': ('Leave requests', 'Leave & attendance', 'Leave requests that overlap the period.', r_leave, 'year', ['view_leavereport', 'view_employee_leave_request']),
    'leave-approvals': ('Leave approvals', 'Leave & attendance', 'Every approval step of the leave requests in the period, with the time it took.', r_leave_approvals, 'year', ['view_leaveapprovalreport', 'view_leaveapproval']),
    'leave-balance': ('Leave balance', 'Leave & attendance', 'Balances of the leave types in each employee\'s leave policy: earned, taken and encashed this year, waiting for approval, available and above the carry-forward limit.', r_leave_balance, None, ['view_lvbalancereport', 'view_emp_leave_balance']),
    'leave-statement': ('Leave statement', 'Leave & attendance', 'Every change to a leave balance in the period – opening, earned, taken, cancelled, encashed, lapsed – with the balance after each line.', r_leave_statement, 'year', ['view_lvbalancereport', 'view_emp_leave_balance']),
    'leave-encashment': ('Leave encashment', 'Leave & attendance', 'Leave encashments with days, amount, status and the payroll that paid them.', r_leave_encashment, None, ['view_leaveencashment', 'view_lvbalancereport']),
    'attendance': ('Attendance (daily)', 'Leave & attendance', 'One row per employee and day with check-in and check-out (up to 3 months).', r_attendance, 'month', ['view_attendancereport', 'view_attendance']),
    'attendance-summary': ('Attendance summary', 'Leave & attendance', 'Per employee: present, absent, on leave, attendance %, late requests and overtime – ready for payroll.', r_attendance_summary, 'month', ['view_attendancereport', 'view_attendance']),
    'late-early': ('Late in / early out', 'Leave & attendance', 'Late-in and early-out requests in the period.', r_late_early, 'month', ['view_lateinearlyoutrequest', 'view_attendancereport']),
    'overtime': ('Overtime', 'Leave & attendance', 'Overtime hours by employee and date.', r_overtime, 'month', ['view_employeeovertime', 'view_attendancereport']),
    'payroll-register': ('Payroll register', 'Payroll', 'Payslips of the payroll runs in the period: gross, additions, deductions and net.', r_payroll_register, 'year', ['view_payslip', 'view_payrollrun']),
    'wps': ('WPS salary file data', 'Payroll', 'Salary lines in the WPS SIF (EDR) order, with a check for missing Person ID or IBAN.', r_wps, 'month', ['view_payslip', 'view_payrollrun']),
    'salary': ('Salary by employee', 'Payroll', 'Current monthly salary components per employee.', r_salary, None, ['view_employeesalarystructure', 'view_payslip']),
    'salary-revisions': ('Salary revisions', 'Payroll', 'Every salary change with old and new amount.', r_salary_revisions, 'year', ['view_salaryrevisionhistory', 'view_employeesalarystructure']),
    'gratuity': ('Gratuity accrual', 'Payroll', 'End-of-service gratuity accrued to date (UAE labour law: 21 / 30 days’ basic per year, capped at 2 years’ basic).', r_gratuity, None, ['view_employeesalarystructure', 'view_payslip']),
    'leave-liability': ('Leave liability', 'Payroll', 'Value of unused annual leave: balance × daily basic.', r_leave_liability, None, ['view_employeesalarystructure', 'view_payslip']),
    'loans': ('Loans outstanding', 'Payroll', 'Loans with amount, repaid, outstanding and instalments left.', r_loans, None, ['view_loanapplication']),
    'advances': ('Advance salary', 'Payroll', 'Advance salary requests and their status.', r_advances, None, ['view_advancesalaryrequest']),
    'air-tickets': ('Air ticket entitlement', 'Payroll', 'Air ticket allocations with remaining amount and expiry.', r_air_tickets, None, ['view_airticketallocation', 'view_airticketrequest']),
    'exits': ('Resignations and end of service', 'Requests & exits', 'Resignations in the period with notice, last day and final settlement.', r_exits, 'year', ['view_employeeresignation', 'view_endofservice']),
    'general-requests': ('General requests', 'Requests & exits', 'General requests in the period.', r_general_requests, 'year', ['view_generalrequestreport', 'view_generalrequest']),
    'assets': ('Assets', 'Requests & exits', 'Company assets with status, condition and who holds them.', r_assets, None, ['view_assetreport', 'view_asset']),
    'asset-transactions': ('Asset transactions', 'Requests & exits', 'Asset hand-overs and returns with days held.', r_asset_transactions, None, ['view_assettransactionreport', 'view_assetallocation']),
    'appraisals': ('Appraisal results', 'Talent & projects', 'Goal sheets with scores, final rating, increment and bonus.', r_appraisals, None, ['view_goalsheet', 'view_appraisaloutcome']),
    'recruitment': ('Recruitment pipeline', 'Talent & projects', 'Job openings with applicants by stage, hires and days open.', r_recruitment, None, ['view_jobopening', 'view_application']),
    'training': ('Training', 'Talent & projects', 'Nominations in the period with approvals, attendance, scores and cost.', r_training, 'year', ['view_nomination', 'view_trainingsession']),
    'certificates': ('Certificates and expiry', 'Talent & projects', 'Training certificates with days left and expiry status.', r_certificates, None, ['view_certificate']),
    'timesheets': ('Timesheet hours', 'Talent & projects', 'Hours booked per employee, project and task.', r_timesheets, 'month', ['view_timesheet']),
    # v1.12 – people analytics, time exceptions, payroll cost, recruitment and performance
    'location': ('Headcount by location', 'People', 'Active employees per branch and work location with gender, UAE nationals / expatriates and Emiratisation %.', r_location, 'year', ['view_report', 'view_emp_master']),
    'demographics': ('Employee demographics', 'People', 'Active employees by age band, gender, nationality, UAE national, marital status, religion and length of service.', r_demographics, None, ['view_report', 'view_emp_master']),
    'turnover-trend': ('Turnover trend (monthly)', 'People', 'Opening and closing headcount, joiners, leavers and turnover % for every month in the period.', r_turnover_trend, 'year', ['view_report', 'view_emp_master']),
    'missing-punch': ('Missing punches', 'Leave & attendance', 'Attendance days with a check-in but no check-out (or the reverse) – up to 3 months.', r_missing_punch, 'month', ['view_attendancereport', 'view_attendance']),
    'late-arrivals': ('Late arrivals', 'Leave & attendance', 'Check-ins after the shift start plus 15 minutes grace, from the punches and the shift of the day.', r_late_arrivals, 'month', ['view_attendancereport', 'view_attendance']),
    'early-departures': ('Early departures', 'Leave & attendance', 'Check-outs more than 15 minutes before the shift end, from the punches and the shift of the day.', r_early_departures, 'month', ['view_attendancereport', 'view_attendance']),
    'absence': ('Absence', 'Leave & attendance', 'Working days with no punch and no approved leave (weekends and holidays excluded) – up to 3 months.', r_absence, 'month', ['view_attendancereport', 'view_attendance']),
    'department-leave': ('Leave by department', 'Leave & attendance', 'Leave requests starting in the period by department and leave type: requests, people, days approved and pending.', r_department_leave, 'year', ['view_leavereport', 'view_employee_leave_request']),
    'allowance-register': ('Allowance register', 'Payroll', 'Every addition paid in the payroll runs of the period, one column per component.', r_allowance_register, 'month', ['view_payslip', 'view_payrollrun']),
    'deduction-register': ('Deduction register', 'Payroll', 'Every deduction taken in the payroll runs of the period, one column per component.', r_deduction_register, 'month', ['view_payslip', 'view_payrollrun']),
    'overtime-pay': ('Overtime pay', 'Payroll', 'Overtime hours per employee and month, the pay due by law (125% / 150% of hourly basic) and the overtime paid in payroll.', r_overtime_pay, 'month', ['view_payslip', 'view_employeeovertime']),
    'payroll-cost': ('Payroll cost (employer)', 'Payroll', 'Per payslip: gross, net and the employer’s monthly gratuity accrual, leave salary accrual and GPSSA pension – total labour cost.', r_payroll_cost, 'month', ['view_payslip', 'view_payrollrun']),
    'department-cost': ('Department cost', 'Payroll', 'Employer labour cost by month, branch and department, with cost per employee.', r_department_cost, 'year', ['view_payslip', 'view_payrollrun']),
    'payroll-variance': ('Payroll variance', 'Payroll', 'Every employee and component this month against the month before: change and % change, plus gross and net.', r_payroll_variance, 'month', ['view_payslip', 'view_payrollrun']),
    'candidates': ('Candidates', 'Talent & projects', 'Candidates who applied in the period with source, experience, expected salary, position and stage.', r_candidates, 'year', ['view_candidate', 'view_application']),
    'time-to-hire': ('Time to hire', 'Talent & projects', 'Hires in the period: days from application to offer accepted / hired, and to joining.', r_time_to_hire, 'year', ['view_application', 'view_offer', 'view_jobopening']),
    'time-to-fill': ('Time to fill', 'Talent & projects', 'Positions opened in the period: days from requisition approval (or publishing) to the last opening filled.', r_time_to_fill, 'year', ['view_jobopening', 'view_manpowerrequisition']),
    'cost-per-hire': ('Cost per hire', 'Talent & projects', 'Hires per position with visa & onboarding cost and cost per hire (recruitment expenses when booked in Expense management).', r_cost_per_hire, 'year', ['view_jobopening', 'view_offer']),
    'source-effectiveness': ('Source effectiveness', 'Talent & projects', 'Candidates, interviews, offers and hires by source, with interview and hire rate and days to hire.', r_source_effectiveness, 'year', ['view_candidate', 'view_application']),
    'kpi': ('KPI report', 'Talent & projects', 'Every goal / KPI per employee and cycle: weight, target, progress and ratings.', r_kpi, None, ['view_goalsheet', 'view_kpi']),
    'goal-achievement': ('Goal achievement', 'Talent & projects', 'Weighted goal achievement % per employee and cycle with goals done, on track and at risk.', r_goal_achievement, None, ['view_goalsheet']),
    'department-performance': ('Department performance', 'Talent & projects', 'Average rating, scores and goal achievement by department and cycle, with the rating distribution.', r_department_performance, None, ['view_goalsheet', 'view_appraisaloutcome']),
}

# v1.12.0: headcount by location / division / section / cost centre / grade / position / employment type (OrgStructure)
try:
    from django.apps import apps as _apps
    if _apps.is_installed('OrgStructure'):
        from OrgStructure.reports import REPORT_SPECS as _ORG_REPORTS
        REPORTS.update(_ORG_REPORTS)
except Exception:  # pragma: no cover - the reports work without it
    pass


def _org_hidden():
    try:
        from django.apps import apps as _apps
        if _apps.is_installed('OrgStructure'):
            from OrgStructure.reports import hidden_reports
            return hidden_reports()
    except Exception:
        pass
    return set()


def _org_extend(request, key, cols, rows):
    """v1.12.0: the switched-on org fields as columns next to the employee columns; Category dropped when switched off."""
    try:
        from django.apps import apps as _apps
        if _apps.is_installed('OrgStructure'):
            from OrgStructure.reports import extend_report
            return extend_report(request, key, cols, rows)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('report org columns failed')
    return cols, rows


def _allowed(c, codes):
    return c.admin or any(code in c.codes for code in codes)


class ReportListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from AccessControl.access import ctx
        c = ctx(request)
        hidden = _org_hidden()
        out = [{'key': k, 'title': v[0], 'section': v[1], 'description': v[2], 'period': v[4], 'perms': v[5]}
               for k, v in REPORTS.items() if _allowed(c, v[5]) and k not in hidden]
        return Response({'reports': out})


class ReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, key):
        from AccessControl.access import ctx
        spec = REPORTS.get(key)
        if spec is None:
            return Response({'detail': 'Unknown report.'}, status=status.HTTP_404_NOT_FOUND)
        title, section, description, fn, period, codes = spec
        c = ctx(request)
        if not _allowed(c, codes):
            return Response({'detail': 'You may not run this report.'}, status=status.HTTP_403_FORBIDDEN)
        run = Run(request, {'period': period})
        if (run.date_from and run.date_to) and run.date_to < run.date_from:
            return Response({'detail': 'The end date is before the start date.'}, status=status.HTTP_400_BAD_REQUEST)
        if not (run.date_from and run.date_to):
            run.date_from = run.date_from or date(1900, 1, 1)
            run.date_to = run.date_to or date(2999, 12, 31)
            shown = None
        else:
            shown = {'from': run.date_from, 'to': run.date_to}
        try:
            res = fn(run)
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        cols, rows = res[0], res[1]
        cols, rows = _org_extend(request, key, cols, rows)
        extra = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}
        skip = set(extra.get('no_totals') or ())
        columns = [{'key': k, 'label': l, 'type': t} for k, l, t in cols]
        totals = {}
        for k, l, t in cols:
            if t == 'number' and k not in skip and not re.search(r'(%|days left|days open|days waiting|days in period|working days|days worked|notice|instalments left|\bage\b|level|years\b|rating|score|service|daily|balance after|limit)', l, re.I):
                vals = [r.get(k) for r in rows if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
                if vals:
                    totals[k] = round(sum(vals), 2)
        return Response({'key': key, 'title': title, 'section': section, 'description': description, 'period': shown,
                         'has_period': period is not None, 'columns': columns, 'rows': rows, 'totals': totals, 'count': len(rows),
                         'notes': [n for n in (extra.get('notes') or []) if n]})


def _run_report(request, key):
    """(spec, payload) or (None, Response) – shared by the report and the e-mail views."""
    from AccessControl.access import ctx
    spec = REPORTS.get(key)
    if spec is None:
        return None, Response({'detail': 'Unknown report.'}, status=status.HTTP_404_NOT_FOUND)
    if not _allowed(ctx(request), spec[5]):
        return None, Response({'detail': 'You may not run this report.'}, status=status.HTTP_403_FORBIDDEN)
    r = ReportView().get(request, key)
    return (spec, r.data) if r.status_code == 200 else (None, r)


class ReportEmailView(APIView):
    """POST /dashboard/api/reports/<key>/email/?from=&to=&branch= – sends the report (CSV) to the user's e-mail,
    e.g. the documents that expire this month to HR."""
    permission_classes = [IsAuthenticated]

    def post(self, request, key):
        import csv
        import io
        import logging
        from django.conf import settings
        from django.core.mail import EmailMessage, get_connection
        spec, data = _run_report(request, key)
        if spec is None:
            return data
        to = (request.user.email or '').strip()
        if not to:
            return Response({'detail': 'Your user has no e-mail address. Add one in Users first.'}, status=status.HTTP_400_BAD_REQUEST)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c['label'] for c in data['columns']])
        for row in data['rows']:
            w.writerow(['' if row.get(c['key']) is None else row.get(c['key']) for c in data['columns']])
        period = f" ({data['period']['from']} to {data['period']['to']})" if data.get('period') else ''
        body = (f"{data['title']}{period}\n{data['description']}\n\n{data['count']} rows – the full list is attached.\n"
                + ''.join(f"\n{c['label']}: {data['totals'][c['key']]:,}" for c in data['columns'] if c['key'] in data['totals']))
        try:
            cfg = M('EmpManagement', 'EmailConfiguration').objects.filter(is_active=True).first()
        except LookupError:
            cfg = None
        try:
            if cfg:
                conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user,
                                      password=cfg.email_host_password, use_tls=cfg.email_use_tls)
                sender = cfg.email_host_user
            else:
                conn = get_connection()
                sender = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or getattr(settings, 'EMAIL_HOST_USER', None)
            msg = EmailMessage(subject=f"{data['title']}{period}", body=body, from_email=sender, to=[to], connection=conn)
            msg.attach(f"{key}-{date.today().isoformat()}.csv", '﻿' + buf.getvalue(), 'text/csv')
            msg.send()
        except Exception as exc:  # mail server down or not set up
            logging.getLogger(__name__).warning('report e-mail failed: %s', exc)
            return Response({'detail': 'The e-mail could not be sent. Check the e-mail settings.'}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'sent_to': to, 'rows': data['count']})


# report → the record types its rows drill down to (gives a report's users the right to open them, v1.8.1)
REPORT_MODELS = {
    'employees': {'EmpManagement.emp_master'}, 'headcount': set(), 'joiners-leavers': {'EmpManagement.emp_master', 'EmpManagement.EmployeeResignation'},
    'probation': {'EmpManagement.emp_master'}, 'birthdays': {'EmpManagement.emp_master'},
    'departments': {'OrganisationManager.dept_master'}, 'designations': {'OrganisationManager.desgntn_master'}, 'categories': {'OrganisationManager.ctgry_master'},
    'documents': {'EmpManagement.Emp_Documents'}, 'leave': {'calendars.employee_leave_request'}, 'leave-approvals': {'calendars.employee_leave_request', 'calendars.LeaveApproval'},
    'leave-balance': {'calendars.emp_leave_balance'}, 'leave-statement': {'calendars.employee_leave_request', 'PayrollManagement.LeaveEncashment', 'EmpManagement.emp_master'},
    'leave-encashment': {'PayrollManagement.LeaveEncashment'}, 'attendance': {'calendars.Attendance', 'EmpManagement.emp_master'}, 'attendance-summary': {'EmpManagement.emp_master'},
    'late-early': {'calendars.LateinEarlyoutRequest'}, 'overtime': {'calendars.EmployeeOvertime'},
    'payroll-register': {'PayrollManagement.Payslip', 'PayrollManagement.PayrollRun', 'PayrollManagement.PayslipComponent'}, 'wps': {'PayrollManagement.Payslip'},
    'salary': {'EmpManagement.emp_master', 'PayrollManagement.EmployeeSalaryStructure'}, 'salary-revisions': {'PayrollManagement.SalaryRevisionHistory'},
    'gratuity': {'EmpManagement.emp_master'}, 'leave-liability': {'EmpManagement.emp_master'},
    'loans': {'PayrollManagement.LoanApplication', 'PayrollManagement.LoanRepayment'}, 'advances': {'PayrollManagement.AdvanceSalaryRequest'},
    'air-tickets': {'PayrollManagement.AirTicketAllocation'}, 'exits': {'EmpManagement.EmployeeResignation', 'EmpManagement.EndOfService'},
    'general-requests': {'EmpManagement.GeneralRequest'}, 'assets': {'OrganisationManager.Asset', 'OrganisationManager.AssetAllocation'},
    'asset-transactions': {'OrganisationManager.AssetAllocation', 'OrganisationManager.Asset'},
    'appraisals': {'PerformanceManagement.GoalSheet', 'PerformanceManagement.AppraisalOutcome'}, 'recruitment': {'RecruitmentManagement.JobOpening', 'RecruitmentManagement.Application'},
    'training': {'LearningManagement.Nomination', 'LearningManagement.TrainingSession', 'LearningManagement.ParticipantResult'},
    'certificates': {'LearningManagement.Certificate'}, 'timesheets': {'ProjectManagement.TimeSheet', 'ProjectManagement.Project', 'ProjectManagement.Task'},
    'location': set(), 'demographics': set(), 'turnover-trend': set(),
    'missing-punch': {'calendars.Attendance'}, 'late-arrivals': {'calendars.Attendance'}, 'early-departures': {'calendars.Attendance'},
    'absence': {'EmpManagement.emp_master', 'calendars.employee_leave_request'}, 'department-leave': set(),
    'allowance-register': {'PayrollManagement.Payslip'}, 'deduction-register': {'PayrollManagement.Payslip'}, 'overtime-pay': {'EmpManagement.emp_master'},
    'payroll-cost': {'PayrollManagement.Payslip'}, 'department-cost': set(), 'payroll-variance': {'PayrollManagement.Payslip'},
    'candidates': {'RecruitmentManagement.Application', 'RecruitmentManagement.Candidate', 'RecruitmentManagement.JobOpening', 'RecruitmentManagement.Offer'},
    'time-to-hire': {'RecruitmentManagement.Application', 'RecruitmentManagement.Offer'},
    'time-to-fill': {'RecruitmentManagement.JobOpening', 'RecruitmentManagement.ManpowerRequisition'}, 'cost-per-hire': {'RecruitmentManagement.JobOpening'},
    'source-effectiveness': set(), 'kpi': {'PerformanceManagement.GoalSheet', 'PerformanceManagement.KPI'}, 'goal-achievement': {'PerformanceManagement.GoalSheet'},
    'department-performance': set(),
}


# v1.11.0: project / timesheet reports from ProjectControl
try:
    from ProjectControl.reports import (r_project_entries, r_project_hours, r_project_cost, r_employee_cost,
                                        r_project_profitability, r_timesheet_approvals, r_billable)
    REPORTS.update({
        'project-entries': ('Timesheet entries (detail)', 'Talent & projects', 'Every timesheet entry with billable flag, approval, cost and billed value.', r_project_entries, 'month', ['view_timesheet']),
        'project-hours': ('Project hours', 'Talent & projects', 'Planned vs actual hours per project, billable / non-billable and by approval status.', r_project_hours, 'month', ['view_timesheet', 'view_projectfinance']),
        'project-cost': ('Project cost', 'Talent & projects', 'Cost per project and employee (approved time at the rate frozen on approval).', r_project_cost, 'month', ['view_projectfinance']),
        'employee-cost': ('Employee cost', 'Talent & projects', 'Hours, billable %, cost and billed value per employee.', r_employee_cost, 'month', ['view_projectfinance']),
        'project-profitability': ('Project profitability', 'Talent & projects', 'Billed value, cost, profit, margin and budget used per project.', r_project_profitability, 'year', ['view_projectfinance']),
        'timesheet-approvals': ('Timesheet approvals', 'Talent & projects', 'Timesheet entries with approval status, approver, waiting days and corrections.', r_timesheet_approvals, 'month', ['view_timesheet']),
        'billable-hours': ('Billable vs non-billable', 'Talent & projects', 'Billable and non-billable hours per employee and project.', r_billable, 'month', ['view_timesheet', 'view_projectfinance']),
    })
    REPORT_MODELS.update({
        'project-entries': {'ProjectManagement.TimeSheet', 'ProjectManagement.Project', 'ProjectManagement.Task'},
        'project-hours': {'ProjectManagement.Project'}, 'project-cost': {'ProjectManagement.Project'},
        'employee-cost': {'EmpManagement.emp_master'}, 'project-profitability': {'ProjectManagement.Project'},
        'timesheet-approvals': {'ProjectManagement.TimeSheet'}, 'billable-hours': {'ProjectManagement.Project'},
    })
except ImportError:
    pass


# v1.12.0: shift roster and shift request reports from ShiftPlanner
if apps.is_installed('ShiftPlanner'):
    from ShiftPlanner.reports import r_shift_roster, r_shift_requests
    REPORTS.update({
        'shift-roster': ('Shift roster', 'Leave & attendance', 'Published shift roster per employee and day: shift, times, hours, night shifts and changes after publishing.', r_shift_roster, 'month', ['view_rosterperiod', 'view_employeeshiftschedule']),
        'shift-requests': ('Shift requests', 'Leave & attendance', 'Shift swaps, changes, cancellations and open-shift claims with status and approver.', r_shift_requests, 'month', ['view_shiftrequest', 'view_rosterperiod', 'view_employeeshiftschedule']),
    })
    REPORT_MODELS.update({'shift-roster': set(), 'shift-requests': set()})


# v1.12.0: attendance rule reports from AttendancePlus (daily status, late / early, missing punch, corrections)
if apps.is_installed('AttendancePlus'):
    from AttendancePlus.reports import REPORTS as _ATT_REPORTS
    REPORTS.update(_ATT_REPORTS)
    REPORT_MODELS.update({k: set() for k in _ATT_REPORTS})


# v1.12.0: asset register / maintenance / recovery / disposal reports from AssetPlus
if apps.is_installed('AssetPlus'):
    from AssetPlus.reports import REPORTS as _ASSET_REPORTS, REPORT_MODELS as _ASSET_REPORT_MODELS
    REPORTS.update(_ASSET_REPORTS)
    REPORT_MODELS.update(_ASSET_REPORT_MODELS)


# v1.13.0: employee master reports from EmployeeProfile (identity expiry, probation due with extensions – replaces the
# plain "Probation due", employees without emergency contact, master data completeness, employee history)
if apps.is_installed('EmployeeProfile'):
    from EmployeeProfile.reports import REPORTS as _PROFILE_REPORTS, REPORT_MODELS as _PROFILE_REPORT_MODELS
    REPORTS.update(_PROFILE_REPORTS)
    REPORT_MODELS.update(_PROFILE_REPORT_MODELS)
