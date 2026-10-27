"""v1.62.0 isolation – the shared master lists of the Core app (public schema: countries, states, currencies,
nationalities, religions, languages, skills, tax systems) seen from a company.

Before 1.62.0 a company user with the add / change / delete right changed (or deleted) a country, currency or skill for
every company on the server. Now:
  * a row a company adds is that company's own (ZeoCloud SharedRowOwner) – listed only there, changed only there;
  * the platform's rows (no owner) are listed everywhere and changed only by a platform administrator (superuser or
    ZEO Cloud platform administrator) – a company gets a clear 403 and may add its own entry instead;
  * outside a company (public schema) nothing changes.
"""
from django.db import connection
from rest_framework import status
from rest_framework.response import Response


def _schema(request=None):
    """The open company. /core/ runs in the public schema (UserManagement SchemaMiddleware) – there the company is the
    ?schema= of the request, accepted only when it is one of the user's companies."""
    s = getattr(connection, 'schema_name', 'public')
    if s and s != 'public':
        return s
    if request is None:
        try:
            from Chatter.tracking import current_request
            request = current_request()
        except Exception:
            request = None
    sch = request.GET.get('schema') if request is not None and hasattr(request, 'GET') else None
    u = getattr(request, 'user', None)
    if sch and sch != 'public' and u is not None and getattr(u, 'is_authenticated', False):
        try:
            if u.tenants.filter(schema_name=sch).exists():
                return sch
        except Exception:
            return None
    return None


def _label(model):
    return model._meta.label_lower


def platform_admin(user):
    if getattr(user, 'is_superuser', False):
        return True
    try:
        from .services import is_platform_admin
        return is_platform_admin(user)
    except Exception:
        return False


def visible(qs, request=None):
    """The rows the open company sees: the platform's and its own (not other companies' own rows)."""
    sch = _schema(request)
    if not sch:
        return qs
    from .models import SharedRowOwner
    others = SharedRowOwner.objects.filter(model=_label(qs.model)).exclude(schema_name=sch).values_list('object_id', flat=True)
    return qs.exclude(pk__in=list(others))


def owned_here(obj, request=None):
    from .models import SharedRowOwner
    return SharedRowOwner.objects.filter(model=_label(type(obj)), object_id=obj.pk, schema_name=_schema(request)).exists()


def refuse():
    from Translations.i18n import tr
    return Response({'detail': tr('This entry is in the shared list used by every company on the server. Only the platform administrator can change or delete it – add your own entry instead.')},
                    status=status.HTTP_403_FORBIDDEN)


class SharedMasterMixin:
    """For the ModelViewSets of the shared Core lists (# v1.62.0 isolation)."""

    def get_queryset(self):
        return visible(super().get_queryset(), self.request)

    def _guarded(self, request):
        if not _schema(request) or platform_admin(request.user):
            return None
        obj = self.get_object()
        return None if owned_here(obj, request) else refuse()

    def update(self, request, *args, **kwargs):
        return self._guarded(request) or super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        refused = self._guarded(request)
        if refused:
            return refused
        sch = _schema(request)
        if sch and getattr(connection, 'schema_name', 'public') == 'public':
            # /core/ runs in the public schema: the delete looks for the rows that use the entry in the company's own tables
            # (in the public schema it failed with "relation EmpManagement_… does not exist")
            from django_tenants.utils import schema_context
            with schema_context(sch):
                return super().destroy(request, *args, **kwargs)
        return super().destroy(request, *args, **kwargs)

    def dispatch(self, request, *args, **kwargs):
        model = self.queryset.model
        before = None
        if request.method == 'POST' and request.GET.get('schema') not in (None, '', 'public'):
            before = model.objects.order_by('-pk').values_list('pk', flat=True).first() or 0
        resp = super().dispatch(request, *args, **kwargs)
        try:
            sch = _schema(self.request) if before is not None else None
            if sch and getattr(resp, 'status_code', 500) < 300 and not platform_admin(self.request.user):
                from .models import SharedRowOwner
                SharedRowOwner.objects.bulk_create([SharedRowOwner(model=_label(model), object_id=pk, schema_name=sch, created_by=self.request.user.pk)
                                                    for pk in model.objects.filter(pk__gt=before).values_list('pk', flat=True)], ignore_conflicts=True)
        except Exception:
            pass
        return resp


# ------------------------------------------------------------------ logins shared by several companies
def other_companies(user):
    """Schemas of the login's other companies (besides the open one)."""
    sch = _schema()
    return list(user.tenants.exclude(schema_name__in=[sch or '', 'public']).values_list('schema_name', flat=True))


def deactivate_login_here(user):
    """An employee left the open company: a login of this company only is switched off as before; a login that is also in
    other companies keeps working there – only its access to this company is removed (given back when the employee's
    self-service is switched on again)."""
    if user is None:
        return None
    if _schema() and other_companies(user):
        from UserManagement.models import company
        c = company.objects.filter(schema_name=_schema()).first()
        if c is not None:
            user.tenants.remove(c)
        return 'access_removed'
    user.is_active = False
    user.save(update_fields=['is_active'])
    return 'deactivated'
