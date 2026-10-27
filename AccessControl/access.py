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
from Translations.i18n import tr  # v1.15.0 language packs: messages in the user's language

logger = logging.getLogger(__name__)

PUBLIC_PATHS = (
    '/users/token/', '/users/2fa/login/', '/users/sso/discover/', '/users/sso/start/', '/users/sso/oidc/', '/users/sso/saml/',
    '/users/sso/exchange/', '/users/sso/device/', '/scim/',   # v1.54.0 single sign-on (before a login exists), SCIM (its own token)
    '/users/validate-credentials/', '/users/verify-otp/', '/users/send-otp/',
    '/users/send-reset-otp/', '/users/verify-reset-otp/', '/users/reset-password/',
    '/users/ess-send-reset-otp/', '/users/ess-verify-reset-otp/', '/users/ess-reset-password/',
    '/users/sign-in-link/',   # v1.56.0 choose the password from the e-mail link (setup guide)
    '/users/activate/', '/users/first-check/',   # v1.62.0 activation without e-mail, the default password's extra check
    '/cloud/api/signup/', '/cloud/api/jobs/',   # v1.23.0 ZEO Cloud sign-up (before a login exists)
    '/mobile/api/info/', '/mobile/api/unlock/',
    '/licence/api/enterprise-request/',   # v1.28.0 Enterprise request (sign-up page, before a login exists)   # v1.25.0 mobile app: server check, fingerprint unlock
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
# v1.56.1 set-up and HR-only tables a login without the add right never creates itself (model-name words)
SELF_ADD_DENY = ('workflow', 'level', 'notif', 'escalation', 'rule', 'template', 'policy', 'setting', 'access', 'approval', 'allocation',
                 'permission', 'overtime', 'monthlyattendance', 'balance', 'payslip', 'salary', 'transaction', 'opening', 'accrual', 'recheck')
# v1.62.0 own requests whose model name contains one of the words above
SELF_ADD_ALLOW = {'advancesalaryrequest'}
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
            self.message = tr('Please log in.')
            return False
        schema = request_schema(request)
        if schema and not user.is_superuser and not user.tenants.filter(schema_name=schema).exists():
            self.message = tr('You do not have access to this company.')
            return False
        model = view_model(view)
        Emp, Branch, User, Company = _models()
        if model is User and request.method not in permissions.SAFE_METHODS and not user.is_superuser:
            # nobody but a superuser can hand out superuser / staff rights or companies they do not administer
            data = _data(request)
            if any(str(data.get(k)).lower() in ('true', '1') for k in ('is_superuser', 'is_staff')):
                self.message = tr('Only a system administrator can grant administrator rights.')
                return False
            if 'tenants' in data:
                from django_tenants.utils import schema_context
                from tenant_users.tenants.models import UserTenantPermissions
                for tid in _values(data.get('tenants')):
                    comp = Company.objects.filter(pk=tid).first() if str(tid).isdigit() else None
                    if comp is None:
                        continue
                    with schema_context(comp.schema_name):
                        ok = _zeo_is_admin(user)
                    if not ok or not user.tenants.filter(pk=comp.pk).exists():
                        self.message = tr('You can only add users to companies you administer.')
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
                self.message = tr('This page is for HR users only.')
                return False
            return True
        name = model._meta.model_name
        verb = verb_for(request, view)
        if f'{verb}_{name}' in c.codes:
            if verb in ('add', 'change') and not self._branch_ok(request, model, c, detail=bool(getattr(view, 'detail', False))):
                self.message = tr('You can only work with records of your own branches.')
                return False
            if verb in ('add', 'change') and not self._location_ok(request, model, verb, detail=bool(getattr(view, 'detail', False))):
                return False
            return True
        if verb == 'view':
            return True  # rows are limited by filter_queryset
        if model is Emp and getattr(view, 'action', None) in ('create', 'update', 'partial_update', 'destroy'):
            # v1.56.1 the employee master itself needs the HR right (a login without rights could add an employee, or change / delete
            # its own record); own details are changed through My profile, family / bank / documents through their own screens
            self.message = tr('Only HR users can add, change or delete employee records. Use My profile to ask for a change.')
            return False
        ep = emp_path(model)
        if ep is not None or user_fields(model):
            if verb == 'add':
                # v1.56.1 release checklist (Part C): a login without the add right could create set-up rows that only name a user
                # (approval levels, workflows, notification lists, escalation rules, user branch access) and HR records of an
                # employee (overtime, attendance summaries, allocations). Self-service adds stay for the user's own requests.
                # v1.62.0 the word 'salary' also matched the advance salary request, so employees could not ask for an advance in self-service
                if name in CONFIDENTIAL or (any(w in name for w in SELF_ADD_DENY) and name not in SELF_ADD_ALLOW):
                    self.message = tr('You do not have permission to {0} {1}.', verb, model._meta.verbose_name)
                    return False
                if ep and not c.emp:
                    self.message = tr('Your login is not linked to an employee record, so you cannot raise requests.')
                    return False
                data = _data(request)
                others = [f for f in user_fields(model) if any(str(v) != str(user.pk) for v in _values(data.get(f)))]
                if not ep and others:
                    self.message = tr('You do not have permission to {0} {1}.', verb, model._meta.verbose_name)
                    return False
            if verb == 'add' and ep and c.emp:
                first = ep.split('__')[0]
                vals = _values(_data(request).get(first))
                if vals and '__' in ep:
                    # v1.62.0 one hop (goal / check-in → goal sheet → employee): the value is the id of the linked record, so compare
                    # its employee – the user's own, or a direct report's (manager check-ins) – not the id itself
                    try:
                        rel = model._meta.get_field(first).related_model
                        owners = set(rel._default_manager.filter(pk__in=[v for v in vals if str(v).isdigit()]).values_list(ep.split('__', 1)[1], flat=True))
                    except Exception:
                        owners = set()
                    team = {c.emp.id} | set(Emp.objects.filter(emp_reporting_manager=user).values_list('id', flat=True))
                    if not owners or not owners <= team:
                        self.message = tr('You can only raise requests for yourself.')
                        return False
                    return True
                if vals and any(str(v) not in (str(c.emp.id), str(c.emp.emp_code)) for v in vals):
                    self.message = tr('You can only raise requests for yourself.')
                    return False
            return True  # self-service: object rules below
        self.message = tr('You do not have permission to {0} {1}.', verb, model._meta.verbose_name)
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
                ok = _zeo_is_admin(user)
            ok = ok and user.tenants.filter(pk=obj.pk).exists()
            if not ok:
                self.message = tr('Only the company administrator can change company details.')
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
            self.message = tr('You can only change your own login.')
            return False
        verb = verb_for(request, view)
        if f'{verb}_{model._meta.model_name}' in c.codes or verb == 'view':
            return True
        if verb == 'delete':
            ep = emp_path(model)
            if ep is not None and c.emp:
                own = model.objects.filter(pk=obj.pk, **({ep: c.emp} if ep else {'users': c.user})).exists()
                if not own:
                    self.message = tr('You can only delete your own records.')
                return own
            uf = user_fields(model)
            return any(getattr(obj, f'{f}_id', None) == c.user.id for f in uf if hasattr(obj, f'{f}_id'))
        return True

    def _location_ok(self, request, model, verb, detail=False):
        """v1.30.0 location level: the record's location / employee must be in the user's locations."""
        try:
            from OrgStructure.location import save_ok
            request._zeo_loc_detail = detail
            ok, msg = save_ok(request, model, _data(request), verb)
            if not ok:
                self.message = tr(msg)
            return ok
        except Exception:
            logger.exception('location check')
            return True

    @staticmethod
    def _branch_ok(request, model, c, detail=False):
        if c.branches is None:
            return True
        Emp, _, _, _ = _models()
        data = _data(request)
        allowed = {str(b) for b in c.branches}
        ep = emp_path(model)
        emp_ids = []
        if ep:
            first = ep.split('__')[0]
            vals = _values(data.get(first))
            emp_ids = [v for v in vals if str(v).isdigit()]
            if emp_ids and '__' in ep:
                # v1.62.0 one hop (goal → goal sheet → employee): the value is the linked record's id, not an employee id – a Dubai-only
                # user adding a goal to their own sheet was refused when the sheet id matched a Sharjah employee's id
                try:
                    rel = model._meta.get_field(first).related_model
                    emp_ids = [v for v in rel._default_manager.filter(pk__in=emp_ids).values_list(ep.split('__', 1)[1], flat=True) if v]
                except Exception:
                    pass
            if emp_ids and Emp.objects.filter(id__in=emp_ids).exclude(emp_branch_id__in=c.branches).exists():
                return False
        bp, _ = branch_path(model)
        if bp and '__' not in bp and bp != 'id':
            keys = [k for k in (bp, bp + '_id', 'branch', 'branch_id', 'branches', 'branch_ids') if k in data]   # v1.18.0: + branch_ids (Payroll run screen)
            vals = [v for k in keys for v in _values(data.get(k))]
            if vals and any(str(v) not in allowed for v in vals):
                return False
            # branch users must pick one of their branches when they CREATE a record. v1.18.0: not for an action on an existing
            # record (submit / approve / create opening … send no branch) – those were all refused for branch HR users; the
            # record itself is already limited to their branches by the row filter. Nor when the record names an employee
            # of their branches (HR entering a leave request for an employee: the branch comes from the employee)
            if not vals and request.method == 'POST' and not detail and not emp_ids:
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


def _branch_or_mine(qs, model, branches, user):
    """v1.28.1: rows of the user's branches – and, for approval / delegation tables, the rows addressed to the user
    (approver, delegate, assignee) whatever branch the request came from: an HR manager of branch B who is the level-1
    approver of a request raised in branch A saw an empty approvals page (the row was scoped out by branch)."""
    if branches is None:
        return qs
    uf = user_fields(model)
    if not uf:
        return branch_filter(qs, model, branches)
    bp, m2m = branch_path(model)
    if not bp:
        return qs
    cond = Q(id__in=branches) if bp == 'id' else (Q(**{f'{bp}__in': branches}) | Q(**{f'{bp}__isnull': True}))
    for f in uf:
        cond |= Q(**{f: user})
    return qs.filter(cond).distinct()


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
        return _branch_or_mine(qs, model, c.branches, user)
    if 'list' in (getattr(view, 'self_service_actions', None) or ()):
        return _branch_or_mine(qs, model, c.branches, user)  # the view declares the list open to all staff
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
            qs = scope_queryset(self.request, self, qs)
            try:   # v1.30.0 location level: the switcher's / the user's locations
                from OrgStructure.location import filter_qs
                model = getattr(qs, 'model', None)
                if model is not None and getattr(self, 'zeo_scope', True) is not False:
                    qs = filter_qs(self.request, qs, model)
            except Exception:
                logger.exception('location scope failed for %s', type(self).__name__)
            return qs
        except Exception:
            logger.exception('access scope failed for %s', type(self).__name__)
            try:
                return qs if ctx(self.request).admin else qs.none()
            except Exception:
                return qs.none()

    APIView.get_permissions = get_permissions
    GenericAPIView.filter_queryset = filter_queryset
    try:   # v1.62.0 hidden self-service fields left out of API answers; sensitive numbers masked
        from .respfilter import install as _rf
        _rf()
    except Exception:
        logger.exception('response filter install')
    _installed = True


# ---------------------------------------------------------------------------
# v1.22.0 branch rights: helpers for views that query models directly
# (@action methods, APIViews, integer employee_id / branch_id columns)
# ---------------------------------------------------------------------------
BRANCH_DENIED = 'You can only work with your branches.'


def allowed_branches(request):
    """v1.22.0 branch rights: None = every branch (company admin / public schema), else the user's branch ids."""
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return []
    if not request_schema(request):
        return None if user.is_superuser else []
    return ctx(request).branches


def emp_ids_in_branches(branches):
    """v1.22.0 branch rights: emp_master ids of the given branches (queryset, usable in __in)."""
    Emp, _, _, _ = _models()
    return Emp.objects.filter(emp_branch_id__in=list(branches or [])).values('id')


HR_APP_LABELS = ('EmpManagement', 'OrganisationManager', 'calendars', 'PayrollManagement', 'UserManagement', 'EmployeeProfile', 'LeavePolicy',
                 'AttendancePlus', 'PayrollPlus', 'ShiftPlanner', 'ExpenseManagement', 'RecruitmentManagement', 'PerformanceManagement',
                 'LearningManagement', 'SelfService', 'ProjectManagement')
# v1.62.0: ZeoHR (HR operations) left out – its rights are app roles (HR operations manager / HR officer / PRO / viewer); with them in
# this list a PRO or an HR operations viewer counted as HR back office and saw every login of the company in Settings › Users


def hr_backoffice(request):
    """v1.56.1: company admin, or a user whose groups hold a right of the HR / payroll / projects core (not only app-kit roles)."""
    try:
        c = ctx(request)
    except Exception:
        return False
    if c is None or c.admin:
        return c is not None
    if not hasattr(request, '_zeo_hrbo'):
        from tenant_users.permissions.models import UserTenantPermissions
        from django.contrib.auth.models import Permission
        utp = UserTenantPermissions.objects.filter(profile=request.user).first()
        request._zeo_hrbo = bool(utp) and Permission.objects.filter(group__in=utp.groups.all(), content_type__app_label__in=HR_APP_LABELS).exists()
    return request._zeo_hrbo


def limit_by_branch(request, qs, branch_path=None, emp_field=None, include_null=False):
    """v1.22.0 branch rights: limit qs to the user's branches (no-op for admins).

    branch_path: ORM path to a brnch_mstr FK or an integer branch id column;
    emp_field:   ORM path to an emp_master FK or an integer employee id column;
    neither:     automatic path (branch_path(model)).
    """
    qs = _limit_by_location(request, qs)   # v1.31.1 location level (no-op while control is off)
    # v1.56.1 release checklist: a self-service login (no rights at all) fell back to its own branch here, so reports and exports
    # built on this helper (employee export, leave / attendance / document reports …) gave every employee of that branch.
    # Employee-linked rows: only the user's own.
    try:
        c = ctx(request) if request_schema(request) else None
    except Exception:
        c = None
    if c is not None and not c.backoffice:
        ep = emp_path(qs.model) if not emp_field else emp_field
        if ep is not None:
            if c.emp is None:
                return qs.none()
            return qs.filter(pk=c.emp.pk) if ep == '' else qs.filter(**{ep: c.emp.pk})
    branches = allowed_branches(request)
    if branches is None:
        return qs
    model = qs.model
    q = None
    if emp_field:
        fk = False
        if '__' not in emp_field:
            try:
                f = model._meta.get_field(emp_field)
                fk = isinstance(f, (ForeignKey, OneToOneField))
            except Exception:
                fk = False
        else:
            fk = True
        q = Q(**{f'{emp_field}__emp_branch_id__in': branches}) if fk else Q(**{f'{emp_field}__in': emp_ids_in_branches(branches)})
        if include_null:
            q |= Q(**{f'{emp_field}__isnull': True})
    else:
        bp = branch_path
        if not bp:
            bp, _ = globals()['branch_path'](model)
        if not bp:
            return qs
        q = Q(**{f'{bp}__in': branches})
        if include_null:
            q |= Q(**{f'{bp}__isnull': True})
    qs = qs.filter(q)
    return qs.distinct() if (emp_field and '__' in emp_field) or (branch_path and '__' in branch_path) else qs


def _limit_by_location(request, qs):
    try:
        from OrgStructure.location import filter_qs
        return filter_qs(request, qs, qs.model)
    except Exception:
        return qs


def check_branch(request, branch_id=None, employee_id=None, employee_code=None):
    """v1.22.0 branch rights: raise PermissionDenied when a branch / employee is outside the user's branches.

    Each argument may be a single value or a list; unknown employees are left to the view (404 / validation).
    """
    from rest_framework.exceptions import PermissionDenied
    branches = allowed_branches(request)
    if branches is None:
        return
    allowed = {str(b) for b in branches}
    Emp, _, _, _ = _models()
    msg = tr(BRANCH_DENIED)
    for b in _values(branch_id):
        if str(getattr(b, 'pk', b)) not in allowed:
            raise PermissionDenied(msg)
    ids = [str(getattr(e, 'pk', e)) for e in _values(employee_id)]
    ids = [i for i in ids if i.isdigit()]
    if ids and Emp.objects.filter(id__in=ids).exclude(emp_branch_id__in=branches).exists():
        raise PermissionDenied(msg)
    codes = [str(c) for c in _values(employee_code)]
    if codes and Emp.objects.filter(emp_code__in=codes).exclude(emp_branch_id__in=branches).exists():
        raise PermissionDenied(msg)


def branch_allows(request, branch_id=None, employee_id=None, employee_code=None):
    """v1.22.0 branch rights: boolean form of check_branch."""
    from rest_framework.exceptions import PermissionDenied
    try:
        check_branch(request, branch_id, employee_id, employee_code)
        return True
    except PermissionDenied:
        return False


class BranchReportMixin:
    """v1.22.0 branch rights: standard reports of branch-restricted users only hold their branches' rows and are
    written to a per-user file, so they neither read nor overwrite the company-wide standard report."""

    def _branches(self):
        req = getattr(self, 'request', None)
        return None if req is None else allowed_branches(req)

    def _std_name(self, base):
        return base if self._branches() is None else f'{base}_u{self.request.user.pk}'

    def _scoped(self, qs, path):
        return qs if self._branches() is None else limit_by_branch(self.request, qs, branch_path=path)


def run_in_branches(branches, run):
    """v1.22.0 branch rights: a payroll run belongs to the user's branches when its branches, picked employees and
    payslips are all inside them (None = company admin = always)."""
    if branches is None:
        return True
    if run is None:
        return False
    for rel, path in (('branch', 'id'), ('employees', 'emp_branch_id'), ('payslips', 'employee__emp_branch_id')):
        mgr = getattr(run, rel, None)
        if mgr is not None and mgr.exclude(**{f'{path}__in': branches}).exists():
            return False
    has_any = any(getattr(run, rel).exists() for rel in ('branch', 'employees', 'payslips') if getattr(run, rel, None) is not None)
    return has_any   # a run with no branch / employee / payslip at all is company-wide: admins only


def deny_branch():
    """v1.22.0 branch rights: refuse with the standard message (HTTP 403)."""
    from rest_framework.exceptions import PermissionDenied
    raise PermissionDenied(tr(BRANCH_DENIED))


def company_perms():
    """v1.22.0 – the rights rows (UserTenantPermissions) of the users of the open company.

    The rights table is shared by all companies, so `UserTenantPermissions.objects` holds every company's users: "users with
    role X" picked people of other companies, who then got to-dos, e-mails and notifications about this company's records.
    """
    from django.db import connection
    from tenant_users.tenants.models import UserTenantPermissions
    qs = UserTenantPermissions.objects.all()
    sch = getattr(connection, 'schema_name', 'public')
    if sch and sch != 'public':
        qs = qs.filter(profile__tenants__schema_name=sch).distinct()
    return qs


def _zeo_is_admin(user):
    """v1.26.0: company administrator in the open company – an invited login's rights for this company included."""
    try:
        from ZeoCloud.rights import is_admin
        return is_admin(user)
    except ImportError:
        from tenant_users.tenants.models import UserTenantPermissions
        return UserTenantPermissions.objects.filter(profile=user, is_superuser=True).exists()
