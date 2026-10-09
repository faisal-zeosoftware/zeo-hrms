"""Rights for AttendancePlus (uses the central AccessControl ctx).

* HR: company admin, or anyone holding one of the existing attendance rights below (so the current
  HR groups work without new permission rows). Branch-restricted HR users only see their branches.
* Manager: the employees whose reporting manager is the user (and themselves).
* Employee: own rows only.
"""
from django.db import connection
from rest_framework.permissions import SAFE_METHODS, BasePermission

HR_CODES = {'change_attendance', 'add_attendance', 'change_attendance_manual', 'change_attendancepolicy',
            'change_attendancerule', 'add_attendancerule', 'change_correctionrequest', 'view_attendance_list'}
DEVICE_CODES = {'add_punch', 'change_punch', 'add_attendance', 'change_attendance'}


def c(request):
    from AccessControl.access import ctx
    return ctx(request)


def is_hr(request, model_name=None, verb=None):
    x = c(request)
    if x.admin:
        return True
    if model_name and verb and f'{verb}_{model_name}' in x.codes:
        return True
    return bool(x.codes & HR_CODES)


def me(request):
    return c(request).emp


def team_ids(request):
    from EmpManagement.models import emp_master
    return set(emp_master.objects.filter(emp_reporting_manager=request.user).values_list('id', flat=True))


def hr_employee_ids(request):
    """None = all; ids of the employees in the HR user's branches."""
    from EmpManagement.models import emp_master
    x = c(request)
    if x.admin or x.branches is None:
        return None
    return set(emp_master.objects.filter(emp_branch_id__in=x.branches).values_list('id', flat=True))


def visible_employee_ids(request):
    x = c(request)
    if x.admin:
        return None
    own = {x.emp.id} if x.emp else set()
    if is_hr(request):
        ids = hr_employee_ids(request)
        return None if ids is None else ids | own | team_ids(request)
    return own | team_ids(request)


def can_see_employee(request, emp_id):
    ids = visible_employee_ids(request)
    return ids is None or int(emp_id) in ids


def hr_for_employee(request, emp_id):
    if not is_hr(request):
        return False
    ids = hr_employee_ids(request)
    return ids is None or int(emp_id) in ids


def branch_ok(request, branch_id):
    x = c(request)
    if x.admin or x.branches is None or branch_id in (None, ''):
        return x.admin or x.branches is None
    return int(branch_id) in set(x.branches)


class APPermission(BasePermission):
    """Company membership; reads open (each view scopes its rows); writes for HR, except the
    self-service actions a view lists (those check the employee themselves)."""
    message = 'You do not have permission to do this.'

    def has_permission(self, request, view):
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        schema = connection.schema_name
        if schema == 'public':
            self.message = 'Select a company.'
            return False
        if not u.is_superuser and not u.tenants.filter(schema_name=schema).exists():
            self.message = 'You do not have access to this company.'
            return False
        if request.method in SAFE_METHODS:
            if getattr(view, 'hr_only_read', False) and not is_hr(request) \
                    and getattr(view, 'action', None) not in (getattr(view, 'self_service_actions', None) or ()):
                self.message = 'This page is for HR users only.'
                return False
            return True
        if getattr(view, 'action', None) in (getattr(view, 'self_service_actions', None) or ()):
            return True
        model = getattr(getattr(view, 'queryset', None), 'model', None)
        verb = {'POST': 'add', 'DELETE': 'delete'}.get(request.method, 'change')
        if is_hr(request, model._meta.model_name if model else None, verb):
            return True
        self.message = 'Only HR can change this.'
        return False
