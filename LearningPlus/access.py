"""Rights for LearningPlus (uses the central AccessControl ctx).

* L&D / HR: company admin, or anyone holding one of the Learning rights below (so the existing
  HR groups work without new permission rows). Branch-restricted HR users only see their branches.
* Manager: their team (emp_reporting_manager = the user) and themselves.
* Employee: own rows only.
"""
from django.db import connection
from rest_framework.permissions import SAFE_METHODS, BasePermission

LD_CODES = {'add_course', 'change_course', 'add_trainingsession', 'change_trainingsession', 'change_nomination'}


def c(request):
    from AccessControl.access import ctx
    return ctx(request)


def is_hr(request, model_name=None, verb=None):
    x = c(request)
    if x.admin:
        return True
    if model_name and verb and f"{verb}_{model_name}" in x.codes:
        return True
    return bool(x.codes & LD_CODES)


def me(request):
    return c(request).emp


def team_ids(request):
    from EmpManagement.models import emp_master
    return set(emp_master.objects.filter(emp_reporting_manager=request.user).values_list('id', flat=True))


def visible_employee_ids(request):
    """None = every employee; otherwise the set of emp_master ids this user may see."""
    from EmpManagement.models import emp_master
    x = c(request)
    if x.admin:
        return None
    own = {x.emp.id} if x.emp else set()
    if is_hr(request):
        if x.branches is None:
            return None
        return set(emp_master.objects.filter(emp_branch_id__in=x.branches).values_list('id', flat=True)) | own | team_ids(request)
    return own | team_ids(request)


def can_see_employee(request, emp_id):
    ids = visible_employee_ids(request)
    return ids is None or int(emp_id) in ids


def employees_qs(request):
    from EmpManagement.models import emp_master
    ids = visible_employee_ids(request)
    qs = emp_master.objects.all()
    return qs if ids is None else qs.filter(id__in=ids)


class LPPermission(BasePermission):
    """Company membership; reads open (rows are scoped by each view); writes for L&D / HR,
    except the self-service actions a view lists (those check the employee themselves)."""
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
            return True
        if getattr(view, 'action', None) in (getattr(view, 'self_service_actions', None) or ()):
            return True
        if getattr(view, 'self_service', False):
            return True
        model = getattr(getattr(view, 'queryset', None), 'model', None)
        verb = {'POST': 'add', 'DELETE': 'delete'}.get(request.method, 'change')
        if is_hr(request, model._meta.model_name if model else None, verb):
            return True
        self.message = 'Only L&D / HR can change this.'
        return False
