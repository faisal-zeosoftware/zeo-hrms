"""
Shared helpers for the Performance, Recruitment and Learning modules.

- TenantModelPermission: same rules as the existing *Permission classes
  (superuser / tenant superuser / group permission codename), derived
  automatically from the view's model, plus an ESS fallback for
  self-service actions.
- EmployeeScopedQuerysetMixin: non-admin users only see their own rows.
- next_document_number: MRF-2026-0001 style numbering.
- notify: best-effort in-app notification + email via the existing
  EmpManagement.utils.send_notification_email. It never blocks a
  transaction when email is not configured.
"""
import logging
from django.utils import timezone
from rest_framework import permissions

logger = logging.getLogger(__name__)

# Custom viewset actions -> model permission verb
DEFAULT_ACTION_VERBS = {
    'list': 'view', 'retrieve': 'view', 'summary': 'view', 'distribution': 'view',
    'create': 'add', 'update': 'change', 'partial_update': 'change', 'destroy': 'delete',
}


def tenant_permissions(user):
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        return UserTenantPermissions.objects.get(profile=user)
    except Exception:
        return None


def is_tenant_admin(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    perms = tenant_permissions(user)
    return bool(perms and perms.is_superuser)


def user_has_codename(user, codename):
    perms = tenant_permissions(user)
    if not perms:
        return False
    if perms.is_superuser:
        return True
    for group in perms.groups.all():
        if group.permissions.filter(codename=codename).exists():
            return True
    return False


def current_employee(user):
    """The emp_master row linked to this login (ESS), if any."""
    if not user or not user.is_authenticated:
        return None
    from EmpManagement.models import emp_master
    return emp_master.objects.filter(users=user).first()


class TenantModelPermission(permissions.BasePermission):
    """
    Allow when the user is (tenant) superuser or one of their groups holds
    <verb>_<model>. Views may declare:
      action_verbs = {'approve': 'change', ...}
      self_service_actions = {'list', 'retrieve', 'submit_self', ...}
    Self-service actions are allowed for any logged-in user linked to an
    employee; the queryset (EmployeeScopedQuerysetMixin) and the action
    itself restrict what they can touch.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if is_tenant_admin(user):
            return True
        model = getattr(getattr(view, 'queryset', None), 'model', None)
        if model is None:
            return False
        verbs = dict(DEFAULT_ACTION_VERBS)
        verbs.update(getattr(view, 'action_verbs', {}) or {})
        verb = verbs.get(view.action)
        if verb and user_has_codename(user, f"{verb}_{model._meta.model_name}"):
            return True
        if view.action in (getattr(view, 'self_service_actions', set()) or set()):
            return current_employee(user) is not None
        return False


class EmployeeScopedQuerysetMixin:
    """
    employee_lookup: ORM path from the model to emp_master (e.g. 'employee',
    'sheet__employee'). manager_lookup: optional ORM path to the reporting
    manager user, so managers see their team. Admins and users holding the
    model's view permission see everything.
    """
    employee_lookup = 'employee'
    manager_lookup = None
    zeo_scope = False  # AccessControl: rows are scoped here (own / team), branches below

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user or not user.is_authenticated:
            return qs.none()
        if is_tenant_admin(user):
            return qs
        if user_has_codename(user, f"view_{qs.model._meta.model_name}"):
            try:  # branch-restricted HR users only see their branches
                from AccessControl.access import ctx, branch_filter
                return branch_filter(qs, qs.model, ctx(self.request).branches)
            except Exception:
                return qs
        from django.db.models import Q
        cond = Q(**{f"{self.employee_lookup}__users": user})
        if self.manager_lookup:
            cond |= Q(**{self.manager_lookup: user})
        return qs.filter(cond).distinct()


def next_document_number(model, field, prefix, width=4):
    year = timezone.now().year
    base = f"{prefix}-{year}-"
    last = (model.objects.filter(**{f"{field}__startswith": base})
            .order_by(f"-{field}").values_list(field, flat=True).first())
    n = 1
    if last:
        try:
            n = int(str(last).rsplit('-', 1)[1]) + 1
        except (ValueError, IndexError):
            n = model.objects.filter(**{f"{field}__startswith": base}).count() + 1
    return f"{base}{n:0{width}d}"


def notify(*, user=None, employee=None, title='', message='', notification_type='general', template_type=''):
    """Best effort: never raises, never blocks the business transaction."""
    if not user and employee is not None:
        user = getattr(employee, 'users', None)
    try:
        from EmpManagement.utils import send_notification_email, get_employee_context
        context = get_employee_context(employee) if employee is not None else {}
        send_notification_email(
            user=user, employee=employee, title=title, message=message[:255],
            notification_type=notification_type, template_type=template_type or notification_type,
            context=context, branch=getattr(employee, 'emp_branch_id', None),
        )
    except Exception as exc:  # pragma: no cover - notifications are optional
        logger.warning("notify() skipped: %s", exc)


class DisplayFieldsMixin:
    """
    Keeps foreign keys as ids (so the same JSON can be posted back) and adds
    human readable companions: <fk>_display, <m2m>_display (list) and
    <choice>_label. Employee FKs also get <fk>_code.
    """

    def to_representation(self, instance):
        data = super().to_representation(instance)
        for f in instance._meta.get_fields():
            if not getattr(f, 'concrete', False) and not f.many_to_many:
                continue
            if f.auto_created and not f.concrete:
                continue
            name = f.name
            if name not in data:
                continue
            try:
                if f.many_to_many:
                    data[f"{name}_display"] = [str(o) for o in getattr(instance, name).all()]
                elif f.is_relation:
                    obj = getattr(instance, name, None)
                    data[f"{name}_display"] = _label(obj)
                    if obj is not None and obj._meta.model_name == 'emp_master':
                        data[f"{name}_code"] = obj.emp_code
                elif getattr(f, 'choices', None):
                    data[f"{name}_label"] = getattr(instance, f"get_{name}_display")()
            except Exception:  # never fail serialization because of a label
                pass
        return data


def _label(obj):
    if obj is None:
        return None
    if obj._meta.model_name == 'emp_master':
        name = " ".join(p for p in [obj.emp_first_name, obj.emp_last_name] if p)
        return f"{name} ({obj.emp_code})" if name else obj.emp_code
    for attr in ('branch_name', 'dept_name', 'desgntn_job_title', 'ctgry_title', 'username'):
        if hasattr(obj, attr) and getattr(obj, attr):
            return getattr(obj, attr)
    return str(obj)


def model_clean(serializer, attrs, model):
    """Run model.clean() on serializer data so model rules apply to the API too."""
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework import serializers as drf_serializers
    m2m = {f.name for f in model._meta.many_to_many}
    plain = {k: v for k, v in attrs.items() if k not in m2m}
    instance = serializer.instance
    if instance is not None:
        tmp = model(**{f.attname: getattr(instance, f.attname) for f in model._meta.concrete_fields})
        for k, v in plain.items():
            setattr(tmp, k, v)
    else:
        tmp = model(**plain)
    try:
        tmp.clean()
    except DjangoValidationError as exc:
        raise drf_serializers.ValidationError(exc.message_dict if hasattr(exc, 'error_dict') else {'non_field_errors': exc.messages})
    return attrs
