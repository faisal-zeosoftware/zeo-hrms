"""v1.26.0 – per-company rights of invited logins (CompanyAccess).

ZEO stores one UserTenantPermissions row per login in the shared schema; every permission check loads it with
`UserTenantPermissions.objects.get(profile=…)` and reads `.is_superuser` and `.groups`. For a login that has a
CompanyAccess row for the company being worked on, the loaded row is changed in memory (never saved): the
administrator flag and the permission groups come from CompanyAccess. The person's own account is not affected.
"""
import threading
import time

from django.db import connection

_lock = threading.Lock()
_cache = {'until': 0.0, 'rows': {}}      # (profile id, schema) -> (admin, [group names])
TTL = 10.0


def _rows():
    now = time.time()
    if _cache['until'] > now:
        return _cache['rows']
    with _lock:
        if _cache['until'] > now:
            return _cache['rows']
        rows = {}
        try:
            from .models import CompanyAccess
            for user_id, schema, admin, groups in CompanyAccess.objects.values_list('user_id', 'company__schema_name', 'admin', 'groups'):
                rows[(user_id, schema)] = (bool(admin), list(groups or []))
        except Exception:
            rows = _cache['rows']
        _cache['rows'], _cache['until'] = rows, now + TTL
        return rows


def forget():
    _cache['until'] = 0.0


def current_schema():
    s = getattr(connection, 'schema_name', 'public')
    return s if s and s != 'public' else None


def access_of(profile_id, schema=None):
    schema = schema or current_schema()
    if not schema or not profile_id:
        return None
    return _rows().get((profile_id, schema))


def apply(utp, schema=None):
    ov = access_of(getattr(utp, 'profile_id', None), schema)
    if ov is None:
        return utp
    from django.contrib.auth.models import Group, Permission
    admin, names = ov
    utp.is_superuser = admin
    utp.is_staff = True
    cache = getattr(utp, '_prefetched_objects_cache', None)
    if cache is None:
        cache = utp._prefetched_objects_cache = {}
    cache['groups'] = Group.objects.filter(name__in=names)            # evaluated in the open company
    cache['user_permissions'] = Permission.objects.none()
    utp._zeo_company_access = schema or current_schema()
    return utp


def install():
    from tenant_users.permissions.models import UserTenantPermissions as UTP
    if getattr(UTP, '_zeo_overlay', False):
        return
    orig = UTP.from_db.__func__

    def from_db(cls, db, field_names, values):
        inst = orig(cls, db, field_names, values)
        try:
            apply(inst)
        except Exception:
            pass
        return inst

    UTP.from_db = classmethod(from_db)
    UTP._zeo_overlay = True


def is_admin(user, schema=None):
    """Company administrator in the open company (the overlay included)."""
    from tenant_users.permissions.models import UserTenantPermissions as UTP
    ov = access_of(user.pk, schema)
    if ov is not None:
        return ov[0]
    return UTP.objects.filter(profile=user, is_superuser=True).exists()
