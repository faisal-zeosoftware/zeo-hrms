"""HSE rights. Roles are permissions on HSESettings (codenames hse_*), given through the "HSE – …" groups.
Every company member may report incidents and observations, request permits and see the live permit board."""
from rest_framework.permissions import BasePermission

from .models import ROLES

ROLE_KEYS = [k for k, _ in ROLES]
ROLE_LABEL = dict(ROLES)
# roles that work in HSE (see every operational record); senior management, department heads and the claims officer read only
NOT_STAFF = ('senior_mgmt', 'dept_head', 'claims_officer')
STAFF_CODES = {('hse_' + k if not k.startswith('hse_') else k) for k in ROLE_KEYS if k not in NOT_STAFF}


def code_of(role):
    return role if role.startswith('hse_') else 'hse_' + role


def ctx(request):
    try:
        from AccessControl.access import ctx as c
        return c(request)
    except Exception:
        return None


def is_admin(request):
    c = ctx(request)
    return bool(c and c.admin)


def has_role(request, role):
    c = ctx(request)
    return bool(c and (c.admin or code_of(role) in c.codes))


def roles(request):
    c = ctx(request)
    if c is None:
        return []
    return ROLE_KEYS[:] if c.admin else [k for k in ROLE_KEYS if code_of(k) in c.codes]


def is_staff(request):
    """Any HSE role (sees all records)."""
    c = ctx(request)
    return bool(c and (c.admin or STAFF_CODES & set(c.codes)))


def can_view_all(request):
    """HSE staff and senior management (read only) see every non-restricted record."""
    return is_staff(request) or has_role(request, 'senior_mgmt')


def any_role(request, *roles):
    return any(has_role(request, r) for r in roles)


def is_manager(request):
    return has_role(request, 'hse_manager')


def can_setup(request):
    return is_manager(request) or is_admin(request)


def my_emp(request):
    c = ctx(request)
    return c.emp if c else None


def can_see_health(request):
    return has_role(request, 'hse_manager') or has_role(request, 'occ_health')


def rights(request):
    r = {k: has_role(request, k) for k in ROLE_KEYS}
    r.update({'admin': is_admin(request), 'staff': is_staff(request), 'view_all': can_view_all(request), 'setup': can_setup(request),
              'health': can_see_health(request), 'employee_id': getattr(my_emp(request), 'pk', None),
              'user_id': request.user.pk if request.user.is_authenticated else None})
    return r


class Denied(Exception):
    def __init__(self, message='You do not have permission to do this.'):
        self.message = message


def need(cond, message):
    if not cond:
        raise Denied(message)


class CompanyMember(BasePermission):
    message = 'You do not have access to this company.'

    def has_permission(self, request, view):
        from django.db import connection
        u = request.user
        if not u or not u.is_authenticated:
            self.message = 'Please log in.'
            return False
        sch = connection.schema_name if connection.schema_name != 'public' else request.GET.get('schema')
        if not sch or sch == 'public':
            self.message = 'Choose a company.'
            return False
        return bool(u.is_superuser or u.tenants.filter(schema_name=sch).exists())


# ------------------------------------------------------------------ v1.22.0 branches (Settings › Branch permissions)
def branch_ids(request):
    """None = every branch (company administrator); else the user's branches."""
    from zeo.appkit.engine import branch_ids as b
    return b(request)


def bpath(model):
    """How a record reaches its branch: its site's branch, its own branch, its incident / permit's site, or its employee's branch."""
    names = {f.name for f in model._meta.fields}
    if model.__name__ == 'Site' or ('branch' in names and 'site' not in names):
        return 'branch'
    for f, p in (('site', 'site__branch'), ('incident', 'incident__site__branch'), ('permit', 'permit__site__branch'),
                 ('employee', 'employee__emp_branch_id'), ('injured_employee', 'injured_employee__emp_branch_id')):
        if f in names:
            return p
    return None


def in_branches(qs, request, path=None):
    """Records of the user's branches, plus records with no site / branch (shared by every branch)."""
    from django.db.models import Q
    p = path or bpath(qs.model)
    if not p:
        return qs
    pick = getattr(request, '_zeo_dash_branch', None) if request is not None else None
    if pick:   # the Branch picked on the dashboard
        qs = qs.filter(**{p: pick})
    b = branch_ids(request)
    if b is None:
        return qs
    return qs.filter(Q(**{p + '__in': b}) | Q(**{p + '__isnull': True}))


def branch_scoped(fn):
    """Decorator for the scope functions (request first): their rows limited to the user's branches."""
    from functools import wraps

    @wraps(fn)
    def w(request, *a, **k):
        return in_branches(fn(request, *a, **k), request)
    return w
