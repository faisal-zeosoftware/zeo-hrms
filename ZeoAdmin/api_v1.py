"""v1.61.0 – the public API, version 1: a small, stable set of endpoints for integrations (an ERP, a BI tool, a time-clock
server), with API keys that carry scopes.

    /api/v1/                        index: the endpoints and the scopes of the key
    /api/v1/employees/              employees:read      ?updated_since=YYYY-MM-DD&active=1&branch=&department=
    /api/v1/employees/<id>/         employees:read
    /api/v1/branches/  departments/  designations/       org:read
    /api/v1/leave-requests/         leave:read          ?from=&to=&status=&employee=
    /api/v1/leave-balances/         leave:read          ?employee=
    /api/v1/attendance-days/        attendance:read     ?from=&to=&employee=   (from–to at most 62 days)
    /api/v1/attendance/punches/     attendance:write    POST {employee_id | employee_code, time (ISO), kind: in|out|auto, device_ref}
    /api/v1/payslips/               payroll:read        ?year=&month=&employee=

Rules: an API key only (Authorization: Api-Key zeo_… or X-API-Key) and ?schema=<company>; the key needs the scope of the
endpoint; the key's user must be a staff user, and the key sees the branches of that user. Lists are paged
(?page=, ?page_size= up to 500). Field names and meanings in v1 do not change; new fields may be added.
Keys marked "API v1 only" cannot call ZEO's internal endpoints at all.
"""
from datetime import date, datetime, timedelta

from django.apps import apps
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .apikeys import ApiKeyAuthentication

M = apps.get_model
VERSION = '1.0'
SCOPES = (
    ('employees:read', 'Employees – read'),
    ('org:read', 'Branches, departments, designations – read'),
    ('leave:read', 'Leave requests and balances – read'),
    ('attendance:read', 'Attendance days – read'),
    ('attendance:write', 'Attendance punches – add (time clocks)'),
    ('payroll:read', 'Payslip totals – read'),
)
SCOPE_KEYS = [s for s, _ in SCOPES]
MAX_PAGE = 500


def _d(v):
    if v in (None, ''):
        return None
    return v.isoformat() if isinstance(v, (date, datetime)) else v


class V1(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]
    scope = None
    rights = ()   # any of these permission codes of the key's user (administrators always)

    def initial(self, request, *a, **k):
        super().initial(request, *a, **k)
        from rest_framework.exceptions import PermissionDenied
        from AccessControl.access import ctx
        from .models import ApiKey
        self.key = ApiKey.objects.filter(pk=(request.auth or {}).get('api_key')).first() if isinstance(request.auth, dict) else None
        if self.key is None:
            raise PermissionDenied('API v1 is used with an API key.')
        if getattr(self.key.user, 'is_ess', False):
            raise PermissionDenied('The API key must belong to a staff user, not a self-service employee.')
        need = self.scope_for(request)
        if need and need not in (self.key.scopes or []):
            raise PermissionDenied(f'This API key does not have the scope "{need}". Add it in Settings › API keys.')
        self.c = ctx(request)
        if self.rights and not (self.c and (self.c.admin or set(self.rights) & set(self.c.codes))):
            raise PermissionDenied('The user of this API key may not see this data in ZEO.')

    def scope_for(self, request):
        return self.scope

    # ---------------------------------------------------------------- helpers
    def branches(self):
        return None if self.c is None or self.c.admin else self.c.branches

    def page(self, request, qs, row):
        try:
            size = max(1, min(int(request.query_params.get('page_size') or 100), MAX_PAGE))
            pg = max(1, int(request.query_params.get('page') or 1))
        except ValueError:
            return Response({'detail': 'page and page_size are whole numbers.'}, status=400)
        n = qs.count()
        items = list(qs[(pg - 1) * size: pg * size])
        return Response({'count': n, 'page': pg, 'page_size': size, 'next_page': pg + 1 if pg * size < n else None,
                         'results': [row(x) for x in items]})

    def bad(self, msg, code=400):
        return Response({'detail': msg}, status=code)


def emp_row(e):
    return {'id': e.pk, 'code': e.emp_code, 'first_name': e.emp_first_name, 'last_name': e.emp_last_name or '',
            'company_email': e.emp_company_email or '', 'mobile': e.emp_mobile_number_1 or '', 'gender': e.emp_gender or '',
            'joined_on': _d(e.emp_joined_date), 'active': e.is_active is not False,
            'branch': {'id': e.emp_branch_id_id, 'name': e.emp_branch_id.branch_name if e.emp_branch_id_id else None},
            'department': {'id': e.emp_dept_id_id, 'name': e.emp_dept_id.dept_name if e.emp_dept_id_id else None},
            'designation': {'id': e.emp_desgntn_id_id, 'name': e.emp_desgntn_id.desgntn_job_title if e.emp_desgntn_id_id else None},
            'reporting_manager_user_id': getattr(e, 'emp_reporting_manager_id', None), 'updated_at': _d(e.updated_at)}


class IndexView(V1):
    def get(self, request):
        base = '/api/v1/'
        return Response({'name': 'ZEO HRMS API', 'version': VERSION, 'key': self.key.name, 'scopes': self.key.scopes or [],
                         'all_scopes': [{'scope': s, 'meaning': m} for s, m in SCOPES],
                         'endpoints': [{'path': base + p, 'methods': ms, 'scope': sc} for p, ms, sc in (
                             ('employees/', ['GET'], 'employees:read'), ('employees/<id>/', ['GET'], 'employees:read'),
                             ('branches/', ['GET'], 'org:read'), ('departments/', ['GET'], 'org:read'), ('designations/', ['GET'], 'org:read'),
                             ('leave-requests/', ['GET'], 'leave:read'), ('leave-balances/', ['GET'], 'leave:read'),
                             ('attendance-days/', ['GET'], 'attendance:read'), ('attendance/punches/', ['POST'], 'attendance:write'),
                             ('payslips/', ['GET'], 'payroll:read'))],
                         'paging': 'page, page_size (max 500)', 'company': 'add ?schema=<company> to every call'})


class EmployeesView(V1):
    scope = 'employees:read'
    rights = ('view_emp_master', 'change_emp_master')

    def get(self, request, pk=None):
        E = M('EmpManagement', 'emp_master')
        qs = E.objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id').order_by('id')
        br = self.branches()
        if br is not None:
            qs = qs.filter(emp_branch_id__in=br)
        if pk is not None:
            e = qs.filter(pk=pk).first()
            return Response(emp_row(e)) if e else self.bad('Employee not found.', 404)
        q = request.query_params
        if q.get('active') in ('1', 'true'):
            qs = qs.exclude(is_active=False)
        elif q.get('active') in ('0', 'false'):
            qs = qs.filter(is_active=False)
        for k, f in (('branch', 'emp_branch_id'), ('department', 'emp_dept_id')):
            if q.get(k):
                if not str(q[k]).isdigit():
                    return self.bad(f'{k} is an id.')
                qs = qs.filter(**{f: int(q[k])})
        if q.get('updated_since'):
            d = parse_date(q['updated_since'][:10])
            if d is None:
                return self.bad('updated_since is a date YYYY-MM-DD.')
            qs = qs.filter(updated_at__date__gte=d)
        return self.page(request, qs, emp_row)


class OrgView(V1):
    scope = 'org:read'
    kind = None

    def get(self, request):
        if self.kind == 'branches':
            qs = M('OrganisationManager', 'brnch_mstr').objects.order_by('id')
            br = self.branches()
            if br is not None:
                qs = qs.filter(pk__in=br)
            return self.page(request, qs, lambda b: {'id': b.pk, 'code': b.branch_code or '', 'name': b.branch_name, 'active': b.br_is_active is not False})
        if self.kind == 'departments':
            qs = M('OrganisationManager', 'dept_master').objects.order_by('id')
            return self.page(request, qs, lambda d: {'id': d.pk, 'code': d.dept_code or '', 'name': d.dept_name, 'active': d.dept_is_active is not False})
        qs = M('OrganisationManager', 'desgntn_master').objects.order_by('id')
        return self.page(request, qs, lambda d: {'id': d.pk, 'code': d.desgntn_code or '', 'name': d.desgntn_job_title, 'active': d.desgntn_is_active is not False})


def _range(request, max_days=None):
    q = request.query_params
    f, t = parse_date((q.get('from') or '')[:10]) if q.get('from') else None, parse_date((q.get('to') or '')[:10]) if q.get('to') else None
    if (q.get('from') and f is None) or (q.get('to') and t is None):
        return None, None, 'from and to are dates YYYY-MM-DD.'
    if max_days:
        t = t or timezone.localdate()
        f = f or (t - timedelta(days=30))
        if (t - f).days > max_days or t < f:
            return None, None, f'from–to is at most {max_days} days.'
    return f, t, None


class LeaveRequestsView(V1):
    scope = 'leave:read'
    rights = ('view_employee_leave_request', 'change_employee_leave_request')

    def get(self, request):
        f, t, err = _range(request)
        if err:
            return self.bad(err)
        qs = M('calendars', 'employee_leave_request').objects.select_related('leave_type', 'employee').order_by('id')
        br = self.branches()
        if br is not None:
            qs = qs.filter(employee__emp_branch_id__in=br)
        if f:
            qs = qs.filter(end_date__gte=f)
        if t:
            qs = qs.filter(start_date__lte=t)
        q = request.query_params
        if q.get('status'):
            qs = qs.filter(status__iexact=q['status'])
        if q.get('employee'):
            if not str(q['employee']).isdigit():
                return self.bad('employee is an id.')
            qs = qs.filter(employee_id=int(q['employee']))
        return self.page(request, qs, lambda r: {
            'id': r.pk, 'number': r.document_number or '', 'employee_id': r.employee_id, 'employee_code': r.employee.emp_code if r.employee_id else '',
            'leave_type': {'id': r.leave_type_id, 'code': getattr(r.leave_type, 'code', '') or '', 'name': getattr(r.leave_type, 'name', '')},
            'start_date': _d(r.start_date), 'end_date': _d(r.end_date), 'days': float(r.approved_days or r.number_of_days or 0),
            'half_day': bool(r.dis_half_day), 'status': r.status, 'applied_on': _d(r.applied_on)})


class LeaveBalancesView(V1):
    scope = 'leave:read'
    rights = ('view_emp_leave_balance', 'view_employee_leave_request', 'change_employee_leave_request')

    def get(self, request):
        qs = M('calendars', 'emp_leave_balance').objects.select_related('leave_type', 'employee').order_by('employee_id', 'leave_type_id')
        br = self.branches()
        if br is not None:
            qs = qs.filter(employee__emp_branch_id__in=br)
        e = request.query_params.get('employee')
        if e:
            if not str(e).isdigit():
                return self.bad('employee is an id.')
            qs = qs.filter(employee_id=int(e))
        return self.page(request, qs, lambda b: {
            'employee_id': b.employee_id, 'employee_code': b.employee.emp_code if b.employee_id else '',
            'leave_type': {'id': b.leave_type_id, 'code': getattr(b.leave_type, 'code', '') or '', 'name': getattr(b.leave_type, 'name', '')},
            'balance': float(b.balance or 0), 'updated_at': _d(b.updated_at)})


class AttendanceDaysView(V1):
    scope = 'attendance:read'
    rights = ('view_attendanceday', 'change_attendanceday', 'view_attendance')

    def get(self, request):
        f, t, err = _range(request, 62)
        if err:
            return self.bad(err)
        qs = M('AttendancePlus', 'AttendanceDay').objects.filter(date__range=(f, t)).order_by('date', 'employee_id')
        br = self.branches()
        if br is not None:
            ids = M('EmpManagement', 'emp_master').objects.filter(emp_branch_id__in=br).values_list('id', flat=True)
            qs = qs.filter(employee_id__in=list(ids))
        e = request.query_params.get('employee')
        if e:
            if not str(e).isdigit():
                return self.bad('employee is an id.')
            qs = qs.filter(employee_id=int(e))
        return self.page(request, qs, lambda d: {
            'employee_id': d.employee_id, 'date': _d(d.date), 'status': d.status, 'shift': d.shift_name, 'first_in': _d(d.first_in),
            'last_out': _d(d.last_out), 'worked_minutes': d.worked_minutes, 'late_minutes': d.late_minutes, 'early_minutes': d.early_minutes,
            'overtime_minutes': d.ot_minutes, 'missing_punch': d.missing_punch})


class PunchesView(V1):
    scope = 'attendance:write'
    rights = ('add_punch', 'change_attendanceday', 'add_attendance')

    def post(self, request):
        from AttendancePlus import engine as AE
        d = request.data or {}
        E = M('EmpManagement', 'emp_master')
        qs = E.objects.all()
        br = self.branches()
        if br is not None:
            qs = qs.filter(emp_branch_id__in=br)
        if str(d.get('employee_id') or '').isdigit():
            e = qs.filter(pk=int(d['employee_id'])).first()
        elif d.get('employee_code'):
            e = qs.filter(emp_code=str(d['employee_code'])).first()
        else:
            return self.bad('Give employee_id or employee_code.')
        if e is None:
            return self.bad('Employee not found.', 404)
        ts = parse_datetime(str(d.get('time') or ''))
        if ts is None:
            return self.bad('time is an ISO date-time, e.g. 2026-10-26T08:02:00+04:00.')
        if timezone.is_naive(ts):
            ts = timezone.make_aware(ts)
        if ts > timezone.now() + timedelta(minutes=5):
            return self.bad('time is in the future.')
        kind = str(d.get('kind') or 'auto')
        if kind not in ('in', 'out', 'auto'):
            return self.bad('kind is in, out or auto.')
        try:
            p, created = AE.add_punch(e, ts, kind=kind, source='device', device_ref=str(d.get('device_ref') or f'api:{self.key.prefix}')[:64],
                                      ip=(self.key.last_ip or '')[:64], user_id=self.key.user_id, note='API v1')
        except Exception as ex:
            return self.bad(str(ex))
        return Response({'id': p.pk, 'created': created, 'employee_id': e.pk, 'time': _d(p.ts), 'work_date': _d(p.work_date),
                         'kind': p.resolved_kind or p.kind}, status=201 if created else 200)


class PayslipsView(V1):
    scope = 'payroll:read'
    rights = ('view_payslip', 'view_payrollrun')

    def get(self, request):
        q = request.query_params
        qs = M('PayrollManagement', 'Payslip').objects.select_related('payroll_run', 'employee').order_by('id')
        br = self.branches()
        if br is not None:
            qs = qs.filter(employee__emp_branch_id__in=br)
        try:
            if q.get('year'):
                qs = qs.filter(payroll_run__year=int(q['year']))
            if q.get('month'):
                qs = qs.filter(payroll_run__month=int(q['month']))
            if q.get('employee'):
                qs = qs.filter(employee_id=int(q['employee']))
        except ValueError:
            return self.bad('year, month and employee are numbers.')
        return self.page(request, qs, lambda s: {
            'id': s.pk, 'employee_id': s.employee_id, 'employee_code': s.employee.emp_code if s.employee_id else '',
            'year': s.payroll_run.year if s.payroll_run_id else None, 'month': s.payroll_run.month if s.payroll_run_id else None,
            'gross': float(s.gross_salary or 0), 'additions': float(s.total_additions or 0), 'deductions': float(s.total_deductions or 0),
            'net': float(s.net_salary or 0), 'currency': str(s.currency or ''), 'status': s.status, 'confirmed': bool(s.confirm_status)})
