"""
Central company / branch / role access control for every DRF view.

Installed once at start-up (AccessControl.apps.ready):

* ZeoAccess is added in front of each view's own permission classes:
    - login is required (except the login / OTP / password-reset endpoints);
    - the company in ?schema= must be one of the user's companies;
    - writes need the group permission add_/change_/delete_<model>, except
      self-service records (requests that belong to the user's own employee);
    - when a branch-restricted user creates or changes a record, its branch /
      employee must be in one of the user's branches.
* filter_queryset is wrapped so every list, detail, update and delete only
  sees rows the user may see:
    - company admin (superuser / company superuser): everything;
    - user holding view_<model>: only rows of the branches assigned in
      "User branch access" (no assignment -> own branch for ESS, none otherwise);
    - user without view_<model> (ESS): own rows, rows where they are the
      approver / delegate / user, and their team's leave and attendance rows.
      Reference data (leave types, departments ...) stays readable;
      company-wide confidential tables (payroll runs, groups, permissions)
      are hidden.

A view can opt out with `zeo_access = False` (permission) or
`zeo_scope = False` (row filter) when it applies equivalent rules itself.
"""
import logging
from django.db import connection
from django.db.models import Q, ForeignKey, OneToOneField, ManyToManyField
from rest_framework import permissions

logger = logging.getLogger(__name__)

PUBLIC_PATHS = (
    '/users/token/', '/users/validate-credentials/', '/users/verify-otp/', '/users/send-otp/',
    '/users/send-reset-otp/', '/users/verify-reset-otp/', '/users/reset-password/',
    '/users/ess-send-reset-otp/', '/users/ess-verify-reset-otp/', '/users/ess-reset-password/',
)
# APIViews without a model that show company-wide data: back-office users only
BACKOFFICE_VIEWS = {
    'ESSUserListView', 'UpdateESSUserView', 'save_notification_settings', 'ApplyOpeningsAPIView',
    'EmployeeAttendanceSummaryAPIView', 'MonthwiseAccrualSimulationView', 'LeaveResetPreviewAPIView',
    'SIFDataView', 'BenefitLiabilityAPIView', 'NoEssUerListView', 'TenantUserListView',
    'GroupPermTenantUserListView', 'ImmediateRejectAPIView',
}
# company-wide confidential tables with no employee / user link
CONFIDENTIAL = {'payrollrun', 'group', 'permission', 'usertenantpermissions', 'userbranchaccess'}
# employee-linked tables a reporting manager may read for their team
TEAM_KEYWORDS = ('leave', 'attendance', 'shift', 'overtime', 'latein', 'lateinearly', 'timesheet', 'rejoin', 'task')
EMP_FIELD_PRIORITY = ('employee', 'emp_id', 'emp', 'employee_id', 'emp_master', 'requested_employee')
BRANCH_FIELD_PRIORITY = ('branch', 'emp_branch_id', 'branch_id', 'brnch', 'work_location')
# audit columns (created_by ...) do not make a row personal
AUDIT_WORDS = ('created', 'updated', 'modified', 'deleted', 'changed')
VERBS = {'list': 'view', 'retrieve': 'view', 'create': 'add', 'update': 'change',
         'partial_update': 'change', 'destroy': 'delete', 'metadata': 'view'}

_cache = {}


def _models():
    from EmpManagement.models import emp_master
    from OrganisationManager.models import brnch_mstr
    from UserManagement.models import CustomUser, company
    return emp_master, brnch_mstr, CustomUser, company


def _pick(fields, priority):
    names = {f.name: f for f in fields}
    for p in priority:
        if p in names:
            return names[p]
    return fields[0] if fields else None


def emp_path(model):
    """ORM path from model to emp_master ('' for emp_master itself, None if unlinked)."""
    key = ('emp', model)
    if key in _cache:
        return _cache[key]
    Emp, _, _, _ = _models()
    path = None
    if model is Emp:
        path = ''
    else:
        direct = [f for f in model._meta.get_fields() if isinstance(f, (ForeignKey, OneToOneField)) and f.related_model is Emp]
        f = _pick(direct, EMP_FIELD_PRIORITY)
        if f:
            path = f.name
        else:  # one hop: payslip component -> payslip -> employee
            for f in model._meta.get_fields():
                if isinstance(f, (ForeignKey, OneToOneField)) and f.related_model not in (None, model) and f.related_model._meta.app_label != 'UserManagement':
                    inner = [g for g in f.related_model._meta.get_fields() if isinstance(g, (ForeignKey, OneToOneField)) and g.related_model is Emp]
                    g = _pick(inner, EMP_FIELD_PRIORITY)
                    if g:
                        path = f'{f.name}__{g.name}'
                        break
    _cache[key] = path
    return path


def user_fields(model):
    key = ('user', model)
    if key not in _cache:
        _, _, User, _ = _models()
        _cache[key] = [f.name for f in model._meta.get_fields()
                       if isinstance(f, (ForeignKey, OneToOneField, ManyToManyField)) and f.related_model is User
                       and not f.auto_created and not any(k in f.name for k in AUDIT_WORDS)]
    return _cache[key]


def branch_path(model):
    """(path, is_m2m) from model to brnch_mstr, or (None, False)."""
    key = ('branch', model)
    if key in _cache:
        return _cache[key]
    Emp, Branch, _, _ = _models()
    res = (None, False)
    direct = [f for f in model._meta.get_fields()
              if isinstance(f, (ForeignKey, OneToOneField, ManyToManyField)) and f.related_model is Branch and not f.auto_created]
    f = _pick(direct, BRANCH_FIELD_PRIORITY)
    if model is Branch:
        res = ('id', False)
    elif f:
        res = (f.name, isinstance(f, ManyToManyField))
    else:
        ep = emp_path(model)
        if ep:
            res = (f'{ep}__emp_branch_id', False)
        elif ep == '':
            res = ('emp_branch_id', False)
    _cache[key] = res
    return res


def request_schema(request):
    """Company schema the request works on: the active one, or ?schema= for /users/ endpoints."""
    if connection.schema_name != 'public':
        return connection.schema_name
    s = request.GET.get('schema') if hasattr(request, 'GET') else None
    return s if s and s != 'public' else None


class Ctx:
    """Per-request facts about the user (computed once)."""

    def __init__(self, request, schema):
        from django_tenants.utils import schema_context
        with schema_context(schema or 'public'):
            self._load(request, schema)

    def _load(self, request, schema):
        from tenant_users.tenants.models import UserTenantPermissions
        from OrganisationManager.models import UserBranchAccess
        Emp, _, _, _ = _models()
        self.user = u = request.user
        self.tenant_schema = schema or 'public'
        self.admin = bool(u.is_superuser)
        self.codes = set()
        utp = UserTenantPermissions.objects.filter(profile=u).prefetch_related('groups__permissions').first() if self.tenant_schema != 'public' else None
        if utp:
            self.admin = self.admin or bool(utp.is_superuser)
            for g in utp.groups.all():
                self.codes.update(p.codename for p in g.permissions.all())
        self.emp = Emp.objects.filter(users=u).first() if self.tenant_schema != 'public' else None
        self.backoffice = self.admin or bool(self.codes)
        self.branches = None
        if not self.admin and self.tenant_schema != 'public':
            ids = list(UserBranchAccess.objects.filter(user=u).values_list('branch__id', flat=True))
            ids = [i for i in ids if i]
            if not ids and self.emp and self.emp.emp_branch_id_id:
                ids = [self.emp.emp_branch_id_id]
            self.branches = ids


def ctx(request):
    schema = request_schema(request) or 'public'
    c = getattr(request, '_zeo_ctx', None)
    if c is None or c.tenant_schema != schema:
        c = Ctx(request, schema)
        request._zeo_ctx = c
    return c


def view_model(view):
    qs = getattr(view, 'queryset', None)
    if qs is not None:
        return qs.model
    sc = getattr(view, 'serializer_class', None)
    meta = getattr(sc, 'Meta', None)
    return getattr(meta, 'model', None)


def verb_for(request, view):
    action = getattr(view, 'action', None)
    if action in VERBS:
        return VERBS[action]
    if request.method in permissions.SAFE_METHODS:
        return 'view'
    return {'POST': 'add', 'DELETE': 'delete'}.get(request.method, 'change') if not action else 'change'


def _values(v):
    if isinstance(v, (list, tuple)):
        return [x for x in v if x not in (None, '')]
    if v in (None, ''):
        return []
    if isinstance(v, str) and ',' in v:
        return [x.strip() for x in v.split(',') if x.strip()]
    return [v]


def _data(request):
    d = request.data
    try:
        return d.dict() if hasattr(d, 'dict') else (d if isinstance(d, dict) else {})
    except Exception:
        return {}


class ZeoAccess(permissions.BasePermission):
    message = 'You do not have permission to do this.'

    def has_permission(self, request, view):
        path = request.path
        if path.startswith(PUBLIC_PATHS):
            return True
        user = request.user
        if not user or not user.is_authenticated:
            self.message = 'Please log in.'
            return False
        schema = request_schema(request)
        if schema and not user.is_superuser and not user.tenants.filter(schema_name=schema).exists():
            self.message = 'You do not have access to this company.'
            return False
        model = view_model(view)
        Emp, Branch, User, Company = _models()
        if model is User and request.method not in permissions.SAFE_METHODS and not user.is_superuser:
            # nobody but a superuser can hand out superuser / staff rights or companies they do not administer
            data = _data(request)
            if any(str(data.get(k)).lower() in ('true', '1') for k in ('is_superuser', 'is_staff')):
                self.message = 'Only a system administrator can grant administrator rights.'
                return False
            if 'tenants' in data:
                from django_tenants.utils import schema_context
                from tenant_users.tenants.models import UserTenantPermissions
                for tid in _values(data.get('tenants')):
                    comp = Company.objects.filter(pk=tid).first() if str(tid).isdigit() else None
                    if comp is None:
                        continue
                    with schema_context(comp.schema_name):
                        ok = UserTenantPermissions.objects.filter(profile=user, is_superuser=True).exists()
                    if not ok or not user.tenants.filter(pk=comp.pk).exists():
                        self.message = 'You can only add users to companies you administer.'
                        return False
        if model is Company:
            if request.method in permissions.SAFE_METHODS:
                return True
            if request.method == 'POST' and not getattr(view, 'detail', False):
                return user.is_superuser
            return True  # object check: admin of that company
        if not schema:
            return True
        c = ctx(request)
        if c.admin:
            return True
        if model is User and verb_for(request, view) in ('view', 'change'):
            return True  # rows / object check: own login only
        if model is None:
            if type(view).__name__ in BACKOFFICE_VIEWS and not c.backoffice:
                self.message = 'This page is for HR users only.'
                return False
            return True
        name = model._meta.model_name
        verb = verb_for(request, view)
        if f'{verb}_{name}' in c.codes:
            if verb in ('add', 'change') and not self._branch_ok(request, model, c):
                self.message = 'You can only work with records of your own branches.'
                return False
            return True
        if verb == 'view':
            return True  # rows are limited by filter_queryset
        ep = emp_path(model)
        if ep is not None or user_fields(model):
            if verb == 'add' and ep and c.emp:
                first = ep.split('__')[0]
                vals = _values(_data(request).get(first))
                if vals and any(str(v) not in (str(c.emp.id), str(c.emp.emp_code)) for v in vals):
                    self.message = 'You can only raise requests for yourself.'
                    return False
            return True  # self-service: object rules below
        self.message = f'You do not have permission to {verb} {model._meta.verbose_name}.'
        return False

    def has_object_permission(self, request, view, obj):
        if request.path.startswith(PUBLIC_PATHS):
            return True
        Emp, Branch, User, Company = _models()
        user = request.user
        if isinstance(obj, Company):
            if request.method in permissions.SAFE_METHODS or user.is_superuser:
                return True
            from django_tenants.utils import schema_context
            from tenant_users.tenants.models import UserTenantPermissions
            with schema_context(obj.schema_name):
                ok = UserTenantPermissions.objects.filter(profile=user, is_superuser=True).exists()
            ok = ok and user.tenants.filter(pk=obj.pk).exists()
            if not ok:
                self.message = 'Only the company administrator can change company details.'
            return ok
        if not request_schema(request):
            return True
        c = ctx(request)
        from .approvals import check as approval_check
        msg = approval_check(request, view, obj, c)
        if msg:
            self.message = msg
            return False
        if c.admin:
            return True
        model = type(obj)
        if isinstance(obj, User):
            if obj.id == user.id or f'{verb_for(request, view)}_customuser' in c.codes:
                return True
            self.message = 'You can only change your own login.'
            return False
        verb = verb_for(request, view)
        if f'{verb}_{model._meta.model_name}' in c.codes or verb == 'view':
            return True
        if verb == 'delete':
            ep = emp_path(model)
            if ep is not None and c.emp:
                own = model.objects.filter(pk=obj.pk, **({ep: c.emp} if ep else {'users': c.user})).exists()
                if not own:
                    self.message = 'You can only delete your own records.'
                return own
            uf = user_fields(model)
            return any(getattr(obj, f'{f}_id', None) == c.user.id for f in uf if hasattr(obj, f'{f}_id'))
        return True

    @staticmethod
    def _branch_ok(request, model, c):
        if c.branches is None:
            return True
        Emp, _, _, _ = _models()
        data = _data(request)
        allowed = {str(b) for b in c.branches}
        bp, _ = branch_path(model)
        if bp and '__' not in bp and bp != 'id':
            keys = [k for k in (bp, bp + '_id', 'branch', 'branch_id', 'branches') if k in data]
            vals = [v for k in keys for v in _values(data.get(k))]
            if vals and any(str(v) not in allowed for v in vals):
                return False
            if not vals and request.method == 'POST':
                return False  # branch users must pick one of their branches
        ep = emp_path(model)
        if ep:
            vals = _values(data.get(ep.split('__')[0]))
            ids = [v for v in vals if str(v).isdigit()]
            if ids and Emp.objects.filter(id__in=ids).exclude(emp_branch_id__in=c.branches).exists():
                return False
        return True


def branch_filter(qs, model, branches):
    if branches is None:
        return qs
    bp, m2m = branch_path(model)
    if not bp:
        return qs
    if bp == 'id':
        return qs.filter(id__in=branches)
    cond = Q(**{f'{bp}__in': branches}) | Q(**{f'{bp}__isnull': True})
    qs = qs.filter(cond)
    return qs.distinct() if (m2m or '__' in bp) else qs


def scope_queryset(request, view, qs):
    model = getattr(qs, 'model', None)
    if model is None or getattr(view, 'zeo_scope', True) is False:
        return qs
    user = request.user
    if not user or not user.is_authenticated:
        return qs if request.path.startswith(PUBLIC_PATHS) else qs.none()
    Emp, Branch, User, Company = _models()
    if model is Company:
        return qs if user.is_superuser else qs.filter(id__in=user.tenants.values('id'))
    if model is User:
        if user.is_superuser:
            return qs
        schema = request_schema(request)
        if not schema or not user.tenants.filter(schema_name=schema).exists():
            return qs.filter(id=user.id)
        c = ctx(request)
        if c.admin or c.backoffice:
            return qs.filter(tenants__schema_name=schema).distinct()
        return qs.filter(id=user.id)
    if connection.schema_name == 'public' or model._meta.app_label in ('Core', 'tenants', 'contenttypes'):
        return qs
    c = ctx(request)
    if c.admin:
        return qs
    name = model._meta.model_name
    if f'view_{name}' in c.codes:
        return branch_filter(qs, model, c.branches)
    if 'list' in (getattr(view, 'self_service_actions', None) or ()):
        return branch_filter(qs, model, c.branches)  # the view declares the list open to all staff
    q = Q()
    linked = False
    ep = emp_path(model)
    if ep is not None:
        linked = True
        if ep == '':
            q |= Q(users=user)
        elif c.emp:
            q |= Q(**{ep: c.emp})
            if any(k in name for k in TEAM_KEYWORDS):
                q |= Q(**{f'{ep}__emp_reporting_manager': user})
    for f in user_fields(model):
        linked = True
        q |= Q(**{f: user})
    if linked:
        return qs.filter(q).distinct() if q else qs.none()
    if name in CONFIDENTIAL:
        return qs.none()
    return branch_filter(qs, model, c.branches)  # reference data of the user's branches


_installed = False


def install():
    global _installed
    import os
    if _installed or os.environ.get('ZEO_ACCESS_OFF'):
        return
    from rest_framework.views import APIView
    from rest_framework.generics import GenericAPIView
    orig_gp = APIView.get_permissions
    orig_fq = GenericAPIView.filter_queryset

    def get_permissions(self):
        perms = orig_gp(self)
        if getattr(self, 'zeo_access', True) is False:
            return perms
        try:  # v1.13.0 employee self-service guard (HR-only fields, request status, payroll results)
            from .ess_guard import EssGuard
            return [ZeoAccess(), EssGuard()] + list(perms)
        except Exception:
            return [ZeoAccess()] + list(perms)

    def filter_queryset(self, queryset):
        qs = orig_fq(self, queryset)
        try:
            return scope_queryset(self.request, self, qs)
        except Exception:
            logger.exception('access scope failed for %s', type(self).__name__)
            try:
                return qs if ctx(self.request).admin else qs.none()
            except Exception:
                return qs.none()

    APIView.get_permissions = get_permissions
    GenericAPIView.filter_queryset = filter_queryset
    _installed = True
