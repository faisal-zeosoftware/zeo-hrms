"""
Drill-down to the root record (v1.8.1).

GET /dashboard/api/record/<app.Model>/<id>/
    → the record a report row, a dashboard figure or another record points to:
      its fields (readable values, links to related records), its lines (child records),
      the employee it belongs to, the screen it is kept on and its API endpoint (for notes, files and history).

Access: company admin; or the view right of that model (or of a report that lists it); or the user's own records.
Branch users only open records of employees in their branches.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps
from django.db import models
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

M_ = '/main-sidebar/'

# model → (screen name, route of the screen that keeps it)
SCREENS = {
    'EmpManagement.emp_master': ('Employees', 'sub-sidebar/employee-master'),
    'EmpManagement.Emp_Documents': ('Expiring documents', 'sub-sidebar/document-expired'),
    'EmpManagement.EmployeeResignation': ('Resignation requests', 'sub-sidebar/resignation-request'),
    'EmpManagement.EndOfService': ('End of service', 'sub-sidebar/end-of-service'),
    'EmpManagement.GeneralRequest': ('General requests', 'general-sidebar/general-request'),
    'EmpManagement.DocumentRequest': ('Document requests', 'general-sidebar/document-request'),
    'EmpManagement.EmployeeBankDetail': ('Employees', 'sub-sidebar/employee-master'),
    'OrganisationManager.dept_master': ('Departments', 'sub-sidebar/department-master'),
    'OrganisationManager.desgntn_master': ('Designations', 'sub-sidebar/designation-master'),
    'OrganisationManager.ctgry_master': ('Categories', 'sub-sidebar/catogary-master'),
    'OrganisationManager.brnch_mstr': ('Branches', 'settings/branch-master'),
    'OrganisationManager.Asset': ('Asset register', 'asset-options/asset-master'),
    'OrganisationManager.AssetAllocation': ('Asset allocation', 'asset-options/asset-allocation'),
    'OrganisationManager.AssetRequest': ('Asset requests', 'asset-options/asset-request'),
    'calendars.employee_leave_request': ('Leave requests', 'leave-options/leave-request'),
    'calendars.LeaveApproval': ('Leave approvals', 'leave-options/leave-approvals'),
    'calendars.emp_leave_balance': ('Leave balance', 'leave-options/leave-balance'),
    'calendars.Attendance': ('Attendance list', 'attendance-sidebar/employee-full-attendance'),
    'calendars.LateinEarlyoutRequest': ('Late in / early out', 'attendance-sidebar/attendance-attendance-request'),
    'calendars.EmployeeOvertime': ('Overtime', 'shift-options/employee-overtime'),
    'PayrollManagement.PayrollRun': ('Payroll runs', 'salary-options/pay-roll'),
    'PayrollManagement.Payslip': ('Payroll runs', 'salary-options/pay-roll'),
    'PayrollManagement.EmployeeSalaryStructure': ('Employee salary', 'salary-options/employee-salary'),
    'PayrollManagement.SalaryRevisionHistory': ('Employee salary', 'salary-options/employee-salary'),
    'PayrollManagement.LoanApplication': ('Loan requests', 'loan-sidebar/loan-application'),
    'PayrollManagement.LoanRepayment': ('Loan repayments', 'loan-sidebar/loan-repayment'),
    'PayrollManagement.AdvanceSalaryRequest': ('Advance requests', 'salary-options/advance-salary-request'),
    'PayrollManagement.AirTicketAllocation': ('Air ticket allocation', 'air-ticket-options/airticket-allocation'),
    'PayrollManagement.AirTicketRequest': ('Air ticket requests', 'air-ticket-options/airticket-request'),
    'ProjectManagement.TimeSheet': ('Timesheets', 'project-options/project-timesheet'),
    'ProjectManagement.Project': ('Projects', 'project-options/project-master'),
    'ProjectManagement.Task': ('Tasks', 'project-options/project-tasks'),
    'PerformanceManagement.GoalSheet': ('Goal setting', 'performance-options/goal-setting'),
    'PerformanceManagement.AppraisalOutcome': ('Appraisal outcome', 'performance-options/outcomes'),
    'PerformanceManagement.AppraisalCycle': ('Appraisal cycle', 'performance-options/cycles'),
    'RecruitmentManagement.JobOpening': ('Job openings', 'recruitment-options/job-openings'),
    'RecruitmentManagement.Application': ('Candidate pipeline', 'recruitment-options/pipeline'),
    'RecruitmentManagement.Candidate': ('Candidates', 'recruitment-options/candidates'),
    'RecruitmentManagement.Interview': ('Interviews', 'recruitment-options/interviews'),
    'RecruitmentManagement.Offer': ('Offer letters', 'recruitment-options/offers'),
    'RecruitmentManagement.ManpowerRequisition': ('Manpower requisition', 'recruitment-options/requisitions'),
    'PerformanceManagement.KPI': ('KPI & competency', 'performance-options/kpi-library'),
    'calendars.ShiftOverride': ('Shift override', 'shift-options/shift-override'),
    'OrganisationManager.Announcement': ('Announcements', 'general-sidebar/announcement-master'),
    'LearningManagement.Nomination': ('Nominations', 'learning-options/nominations'),
    'LearningManagement.TrainingSession': ('Training calendar', 'learning-options/calendar'),
    'LearningManagement.Course': ('Course catalog', 'learning-options/courses'),
    'LearningManagement.ParticipantResult': ('Attendance & assessment', 'learning-options/results'),
    'LearningManagement.Certificate': ('Certificates', 'learning-options/certificates'),
}

# records that have a full page of their own in the app
def own_page(label, obj):
    if label == 'EmpManagement.emp_master':
        return f'{M_}sub-sidebar/employee-details/{obj.pk}/details'
    if label == 'PayrollManagement.PayrollRun':
        return f'{M_}salary-options/payroll-details/{obj.pk}'
    return None


SECRET = re.compile(r'password|token|otp|secret|salt|hash', re.I)
SKIP_CHILD_APPS = {'Chatter', 'DataTools', 'admin', 'contenttypes', 'auth', 'sessions', 'token_blacklist', 'django_celery_beat', 'simple_history'}
_ENDPOINTS = None


def endpoints():
    """model label → the list API that serves it (first one found)."""
    global _ENDPOINTS
    if _ENDPOINTS is not None:
        return _ENDPOINTS
    out = {}

    def walk(pats, prefix=''):
        for p in pats:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                cls = getattr(p.callback, 'cls', None)
                actions = getattr(p.callback, 'actions', None) or {}
                if not cls or actions.get('get') != 'list':
                    continue
                model = None
                qs = getattr(cls, 'queryset', None)
                if qs is not None:
                    model = qs.model
                else:
                    try:
                        model = cls.serializer_class.Meta.model
                    except Exception:
                        model = None
                path = ('/' + prefix + str(p.pattern)).replace('^', '').replace('$', '')
                if model is None or '(?P' in path or '<' in path or '\\.' in path:
                    continue
                out.setdefault(model._meta.label, path)
    try:
        walk(get_resolver().url_patterns)
    except Exception:
        pass
    _ENDPOINTS = out
    return out


def label_of(f):
    v = str(getattr(f, 'verbose_name', f.name) or f.name)
    if v.replace(' ', '_') == f.name:          # no verbose name given
        v = re.sub(r'^(emp|desgntn|dept|ctgry|brnch)_', '', f.name).replace('_', ' ')
        v = re.sub(r'\bid\b', '', v).strip() or f.name
    return v[:1].upper() + v[1:]


def readable(obj, f):
    """(text, link) of one field of a record."""
    if f.choices:
        return getattr(obj, f'get_{f.name}_display')(), None
    if isinstance(f, (models.ForeignKey, models.OneToOneField)):
        rel = getattr(obj, f.name, None)
        if rel is None:
            return '', None
        return person(rel) if rel._meta.label == 'EmpManagement.emp_master' else str(rel), {'m': rel._meta.label, 'id': rel.pk}
    v = getattr(obj, f.attname, None)
    if v is None or v == '':
        return '', None
    if isinstance(f, models.BooleanField):
        return ('Yes' if v else 'No'), None
    if isinstance(f, models.FileField):
        return (v.name.split('/')[-1], {'file': v.url}) if v else ('', None)
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M'), None
    if isinstance(v, date):
        return v.isoformat(), None
    if isinstance(v, timedelta):
        return f'{round(v.total_seconds() / 3600, 2)} h', None
    if isinstance(v, Decimal):
        return f'{v:,.2f}', None
    if isinstance(v, float):
        return f'{v:,.2f}'.rstrip('0').rstrip('.'), None
    return str(v), None


def nice_name(v):
    v = re.sub(r'^(emp|employee)[ _](?=\w)', 'employee ', str(v)).replace('_', ' ').strip()
    return v[:1].upper() + v[1:]


def person(e):
    name = ' '.join(x for x in [e.emp_first_name, e.emp_middle_name, e.emp_last_name] if x and str(x).strip())
    return f'{name} ({e.emp_code})' if name else e.emp_code


def employee_of(obj):
    """The employee a record belongs to (itself for an employee)."""
    if obj._meta.label == 'EmpManagement.emp_master':
        return obj
    for f in obj._meta.concrete_fields:
        if isinstance(f, models.ForeignKey) and f.related_model is not None and f.related_model._meta.label == 'EmpManagement.emp_master':
            return getattr(obj, f.name, None)
    # one step further: payslip line → payslip → employee, approval → leave request → employee
    for f in obj._meta.concrete_fields:
        if isinstance(f, models.ForeignKey) and f.related_model is not None and f.related_model._meta.app_label not in ('UserManagement', 'OrganisationManager'):
            rel = getattr(obj, f.name, None)
            if rel is not None:
                for g in rel._meta.concrete_fields:
                    if isinstance(g, models.ForeignKey) and g.related_model is not None and g.related_model._meta.label == 'EmpManagement.emp_master':
                        return getattr(rel, g.name, None)
    return None


def branch_of(obj):
    for name in ('branch', 'emp_branch_id'):
        f = next((x for x in obj._meta.concrete_fields if x.name == name), None)
        if f is not None:
            return getattr(obj, f.attname, None)
    return None


def extra_codes(label):
    """View rights of the reports that list this model."""
    from .reports import REPORTS, REPORT_MODELS
    codes = set()
    for key, labels in REPORT_MODELS.items():
        if label in labels and key in REPORTS:
            codes.update(REPORTS[key][5])
    return codes


def can_open(c, model, obj):
    if c.admin:
        return True
    label = model._meta.label
    emp = employee_of(obj)
    own = c.emp is not None and emp is not None and emp.pk == c.emp.pk
    if not own and emp is not None and c.emp is not None and label != 'EmpManagement.emp_master':
        from .services import can_view_employee
        try:
            own = can_view_employee(c.user, emp)    # a manager opens the records of his team
        except Exception:
            own = False
    codes = {f'view_{model._meta.model_name}'} | extra_codes(label)
    if not own and not (codes & c.codes):
        return False
    if c.branches is not None and not own:
        if emp is not None:
            return emp.emp_branch_id_id in c.branches
        b = branch_of(obj)
        if b is not None:
            return b in c.branches
    return True


def find_text(obj):
    """What to type in the screen's search box to find the record there."""
    for name in ('document_number', 'emp_code', 'job_code', 'code', 'emp_doc_number', 'serial_number', 'certificate_number', 'name', 'title'):
        v = getattr(obj, name, None)
        if v and isinstance(v, str):
            return v
    emp = employee_of(obj)
    return emp.emp_code if emp is not None else ''


class RecordView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, label, pk):
        from AccessControl.access import ctx
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError):
            return Response({'detail': 'Unknown record type.'}, status=status.HTTP_404_NOT_FOUND)
        obj = model._default_manager.filter(pk=pk).first()
        if obj is None:
            return Response({'detail': 'This record no longer exists.'}, status=status.HTTP_404_NOT_FOUND)
        c = ctx(request)
        if not can_open(c, model, obj):
            return Response({'detail': 'You may not open this record.'}, status=status.HTTP_403_FORBIDDEN)
        label = model._meta.label
        fields = []
        for f in model._meta.concrete_fields:
            if f.primary_key or SECRET.search(f.name):
                continue
            text, link = readable(obj, f)
            fields.append({'label': label_of(f), 'value': text, 'link': link})
        for f in model._meta.many_to_many:
            vals = list(getattr(obj, f.name).all()[:20])
            fields.append({'label': label_of(f), 'value': ', '.join(str(v) for v in vals), 'link': None})
        children = []
        if label not in ('EmpManagement.emp_master', 'OrganisationManager.brnch_mstr'):
            for rel in model._meta.related_objects:
                rm = rel.related_model
                if rel.many_to_many or rm._meta.app_label in SKIP_CHILD_APPS or rm._meta.label.startswith('Chatter'):
                    continue
                try:
                    if rel.one_to_one:
                        one = getattr(obj, rel.get_accessor_name(), None)
                        qs = [one] if one is not None else []
                    else:
                        qs = list(getattr(obj, rel.get_accessor_name()).all()[:50])
                except Exception:
                    continue
                if not qs:
                    continue
                cols = [f for f in rm._meta.concrete_fields if not f.primary_key and f.name != rel.field.name and not SECRET.search(f.name)
                        and not isinstance(f, models.FileField)][:7]
                rows = []
                for r in qs:
                    cells = []
                    for f in cols:
                        t, l = readable(r, f)
                        cells.append({'value': t, 'link': l})
                    rows.append({'cells': cells, 'm': rm._meta.label, 'id': r.pk})
                children.append({'title': 'Employees' if rm._meta.label == 'EmpManagement.emp_master' else nice_name(rm._meta.verbose_name_plural), 'columns': [label_of(f) for f in cols], 'rows': rows})
                if len(children) >= 8:
                    break
        emp = employee_of(obj)
        screen = SCREENS.get(label)
        title = person(obj) if label == 'EmpManagement.emp_master' else str(obj)
        if re.search(r'object \(\d+\)$', title):   # model without a name of its own
            num = getattr(obj, 'document_number', None)
            title = ' – '.join(x for x in [str(model._meta.verbose_name).capitalize(), num, person(emp) if emp is not None else ''] if x)
        return Response({
            'model': label, 'id': obj.pk, 'kind': nice_name(model._meta.verbose_name), 'title': title,
            'employee': {'id': emp.pk, 'name': person(emp), 'page': own_page('EmpManagement.emp_master', emp)} if emp is not None and emp is not obj else None,
            'fields': fields, 'children': children, 'endpoint': endpoints().get(label),
            'screen': {'name': screen[0], 'route': M_ + screen[1], 'find': find_text(obj)} if screen else None,
            'page': own_page(label, obj),
        })
