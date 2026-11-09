"""v1.67.7 (admin) – administrator rights per company.

Before 1.67.7 the administrator flag lived on the login's one rights record (django-tenant-users' UserTenantPermissions). ZEO
keeps that app in the SHARED schema (SHARED_APPS: one row per login for the whole server, not one per company), so a login
that administered one company administered every company it belonged to. Moving django-tenant-users to the company schemas
would move the permission groups too (and need migrations of a core package in every company), so the flag per company is
kept in a small non-core store instead: ZeoCloud.CompanyAdmin (public schema, one row per login and company).

The rule (`is_admin`):
  1. an invited login (ZeoCloud.CompanyAccess, v1.26.0) – its per-company flag there, as before;
  2. a CompanyAdmin row for the login and the company – its value (on or off);
  3. no row – the default: the OWNER of the company (the account owner on ZEO Cloud, else the database creator: the first
     system administrator) and the system administrators are administrators; every other login is not.
So the owner is administrator of every company of the account / database, companies made later included, until someone
changes it; any other login is administrator only where a row says so.

Where it is used: the rights record loaded for a company carries this company's value (ZeoCloud/rights.py `apply`, every
`UserTenantPermissions.objects.get/filter(...)` read), a system administrator switched off in one company is not one in that
company's requests (`mask_request_user`), and the lists of administrators (security e-mails, dashboard, alerts, the admin-
branches group, approvers that fall back to the administrators) use `admin_ids`.

Changes (`set_admin`) are refused when they would leave a company without an administrator, when the person changing it is
neither the owner nor an administrator of that company, and when someone other than the owner takes the owner's rights.
"""
import logging
import threading
import time

from django.db import connection

log = logging.getLogger(__name__)

_lock = threading.Lock()
_cache = {'until': 0.0, 'rows': {}, 'sys': set(), 'owners': {}, 'cids': {}}
TTL = 10.0


class AdminError(Exception):
    def __init__(self, msg, code='refused', status=409):
        super().__init__(msg)
        self.msg, self.code, self.status = msg, code, status


def forget(*a, **kw):
    _cache['until'] = 0.0
    try:
        from . import rights
        rights.forget()
    except Exception:
        pass


def _load():
    now = time.time()
    if _cache['until'] > now:
        return _cache
    with _lock:
        if _cache['until'] > now:
            return _cache
        from django_tenants.utils import schema_context
        try:
            with schema_context('public'):
                from django.contrib.auth import get_user_model
                from .models import AccountCompany, CompanyAdmin
                from UserManagement.models import company as Company
                rows = {(u, s): bool(a) for u, s, a in CompanyAdmin.objects.values_list('user_id', 'company__schema_name', 'admin')}
                U = get_user_model()
                sysids = set(U.objects.filter(is_superuser=True, is_active=True).values_list('id', flat=True))
                cids = dict(Company.objects.values_list('schema_name', 'id'))
                owners = {s: o for s, o in AccountCompany.objects.filter(account__owner__isnull=False)
                          .values_list('company__schema_name', 'account__owner_id')}
                first_sys = min(sysids) if sysids else None
                owners['*'] = first_sys
            _cache.update(rows=rows, sys=sysids, owners=owners, cids=cids, until=now + TTL)
        except Exception:
            log.exception('company administrators')
            _cache['until'] = now + 1.0
        return _cache


def current_schema():
    s = getattr(connection, 'schema_name', 'public')
    return s if s and s != 'public' else None


def _uid(user):
    return user if isinstance(user, int) else getattr(user, 'pk', None)


def owner_id(schema):
    """The owner of the company: the owner of its ZEO Cloud account, else the database creator (first system administrator)."""
    if not schema or schema == 'public':
        return None
    c = _load()
    return c['owners'].get(schema) or c['owners'].get('*')


def is_owner(user, schema=None):
    uid = _uid(user)
    schema = schema or current_schema()
    return bool(uid) and uid == owner_id(schema)


def is_system_admin(user):
    uid = _uid(user)
    return bool(uid) and uid in _load()['sys']


def explicit(user, schema):
    """The stored value (True / False) or None when the default applies."""
    return _load()['rows'].get((_uid(user), schema))


def is_admin(user, schema=None):
    """Administrator of the company `schema` (default: the open company). Without a company: administrator of any company."""
    uid = _uid(user)
    if not uid:
        return False
    schema = schema or current_schema()
    if not schema:
        return any_admin(uid)
    try:
        from .rights import access_of
        ov = access_of(uid, schema)
    except Exception:
        ov = None
    if ov is not None:
        return bool(ov[0])
    v = explicit(uid, schema)
    if v is not None:
        return v
    return uid in _load()['sys'] or uid == owner_id(schema)


def any_admin(uid):
    c = _load()
    if uid in c['sys'] or any(u == uid and a for (u, _), a in c['rows'].items()):
        return True
    try:
        from .rights import _rows as _ca
        if any(u == uid and v[0] for (u, _), v in _ca().items()):
            return True
    except Exception:
        pass
    return uid in set(c['owners'].values())


def members(schema):
    """Active logins of the company."""
    from django.contrib.auth import get_user_model
    from django_tenants.utils import schema_context
    with schema_context('public'):
        return list(get_user_model().objects.filter(tenants__schema_name=schema, is_active=True).distinct().order_by('id'))


def admin_ids(schema=None):
    """Ids of the active logins that administer the company."""
    schema = schema or current_schema()
    if not schema:
        return set()
    from django.contrib.auth import get_user_model
    from django_tenants.utils import schema_context
    with schema_context('public'):
        ids = list(get_user_model().objects.filter(tenants__schema_name=schema, is_active=True).values_list('id', flat=True).distinct())
    return {i for i in ids if is_admin(i, schema)}


def admin_users(schema=None):
    from django.contrib.auth import get_user_model
    from django_tenants.utils import schema_context
    ids = admin_ids(schema)
    with schema_context('public'):
        return list(get_user_model().objects.filter(pk__in=ids).order_by('id'))


def admin_emails(schema):
    return sorted({u.email for u in admin_users(schema) if u.email})


def switched_off(user, schema):
    """v1.67.8 (fixes) item 11: this login was switched off as administrator of the company `schema` (an explicit "no")."""
    return bool(schema) and explicit(_uid(user), schema) is False


def sysadmin_in(user, schema):
    """v1.67.8 (fixes) item 11: a system administrator (server level) who still administers the company `schema` – not one
    switched off there. Server-level paths that reach company data without naming the company in the request (group view,
    media files) use this per company instead of `is_superuser`."""
    if not getattr(user, 'is_superuser', False) and not getattr(user, '_zeo_su_masked', False):
        return False
    return not switched_off(user, schema)


def mask_request_user(user, schema):
    """A system administrator whose administrator rights were switched off in this company is not a system administrator in
    this company's requests (in memory only – `CustomUser` saves keep the stored value, see `_user_pre_save`)."""
    if not schema or schema == 'public' or not getattr(user, 'is_superuser', False):
        return user
    if explicit(user.pk, schema) is False:
        user.is_superuser = False
        user._zeo_su_masked = True
    return user


def _user_pre_save(sender, instance, raw=False, **kw):
    if getattr(instance, '_zeo_su_masked', False):
        instance.is_superuser = True


# ------------------------------------------------------------------ changes
def _company(schema):
    from UserManagement.models import company as Company
    from django_tenants.utils import schema_context
    with schema_context('public'):
        return Company.objects.filter(schema_name=schema).first()


def can_manage(by, schema):
    """The owner of the company or one of its administrators (system administrators and ZEO Cloud staff too)."""
    if by is None or not getattr(by, 'is_authenticated', False):
        return False
    if is_owner(by, schema) or is_admin(by, schema):
        return True
    try:
        from .services import is_platform_admin
        return is_platform_admin(by)
    except Exception:
        return False


def check_change(target_id, schema, on, by=None):
    """Raise AdminError when the change may not be made."""
    from Translations.i18n import tr
    comp = _company(schema)
    cname = getattr(comp, 'name', schema)
    if by is not None and not can_manage(by, schema):
        raise AdminError(tr('Only the owner or an administrator of {0} can change who administers it.', cname), 'not_admin', 403)
    if on:
        return
    if not is_admin(target_id, schema):
        return
    if is_owner(target_id, schema) and by is not None and _uid(by) != target_id:
        raise AdminError(tr('Only the owner can remove their own administrator rights in {0}.', cname), 'owner', 403)
    others = admin_ids(schema) - {target_id}
    if not others:
        raise AdminError(tr('{0} needs at least one administrator. Make another user administrator of {0} first.', cname), 'last_admin', 409)


def write(target_id, schema, on, by=None):
    """Store the value for one company (no checks – see `set_admin`)."""
    from django_tenants.utils import schema_context
    from .models import CompanyAccess, CompanyAdmin
    comp = _company(schema)
    if comp is None:
        return
    with schema_context('public'):
        ca = CompanyAccess.objects.filter(user_id=target_id, company=comp).first()
        if ca is not None:   # an invited login keeps its rights for this company in CompanyAccess
            if ca.admin != bool(on):
                ca.admin = bool(on)
                ca.save(update_fields=['admin', 'updated_at'])
        else:
            CompanyAdmin.objects.update_or_create(user_id=target_id, company=comp,
                                                  defaults={'admin': bool(on), 'by_id': _uid(by) if by is not None else None})
    forget()
    sync_flag(target_id)


def sync_flag(uid):
    """The old shared flag says "administrator of at least one company" (read only where no company is open)."""
    try:
        from django_tenants.utils import schema_context
        from tenant_users.permissions.models import UserTenantPermissions as UTP
        with schema_context('public'):
            UTP.objects.filter(profile_id=uid).update(is_superuser=any_admin(uid))
    except Exception:
        log.exception('admin flag of %s', uid)


def _admin_group(schema, uid, on):
    """The 1.67.6 "Company administrators" group (every branch) follows the administrator rights in this company."""
    try:
        from django_tenants.utils import schema_context
        from tenant_users.permissions.models import UserTenantPermissions as UTP
        from ZeoApps import adminbranches as AB
        with schema_context(schema):
            if on:
                AB.ensure_group(link_admins=True)
            else:
                ids = AB._option_ids()
                utp = UTP.objects.filter(profile_id=uid).first()
                if utp is not None and ids:
                    UTP.groups.through.objects.filter(usertenantpermissions_id=utp.pk, group_id__in=ids).delete()
                try:
                    from ZeoApps import groupscope as GS
                    GS.forget()
                except Exception:
                    pass
    except Exception:
        log.exception('company administrators group of %s', schema)


def set_admin(target, schema, on, by=None, request=None):
    """Make `target` administrator of the company `schema` (on) or not (off). Raises AdminError when refused."""
    from django.contrib.auth import get_user_model
    from django_tenants.utils import schema_context
    from Translations.i18n import tr
    uid = _uid(target)
    with schema_context('public'):
        user = get_user_model().objects.filter(pk=uid).first()
        if user is None or not user.tenants.filter(schema_name=schema).exists():
            raise AdminError(tr('This login is not a user of this company.'), 'not_member', 404)
    before = is_admin(uid, schema)
    check_change(uid, schema, on, by)
    if before == bool(on) and explicit(uid, schema) is not None:
        return {'admin': before, 'changed': False}
    write(uid, schema, on, by)
    _admin_group(schema, uid, on)
    if by is not None and before != bool(on):
        try:
            from .security import event, forget_rules
            from .netguard import client_ip
            forget_rules([uid])
            name = user.get_full_name() or user.username
            event('admin_granted' if on else 'rights_changed', user=user, by=by, schema=schema,
                  ip=client_ip(request) if request is not None else '',
                  detail=f'{name} was made an administrator of the company.' if on else f'{name} is no longer an administrator of the company.')
        except Exception:
            log.exception('admin event')
    return {'admin': bool(on), 'changed': before != bool(on)}


# ------------------------------------------------------------------ the old write paths (UserTenantPermissions saves)
def _current_request():
    try:
        from Chatter.tracking import current_request
        return current_request()
    except Exception:
        return None


def _utp_pre_save(sender, instance, raw=False, **kw):
    """Code that saves the rights record inside a company (Assign permissions, scripts, the company set-up) changes the
    administrator rights of THAT company only. Refused (400) when it would leave the company without an administrator."""
    instance._zeo_admin_write = None
    if raw:
        return
    schema = current_schema()
    if not schema or not getattr(instance, 'profile_id', None):
        return
    try:
        from .rights import access_of
        if access_of(instance.profile_id, schema) is not None:
            return   # invited login: CompanyAccess (permission viewset)
    except Exception:
        pass
    want = bool(instance.is_superuser)
    before = is_admin(instance.profile_id, schema)
    if instance.pk is None:
        if not want:   # a new rights record of a plain user: nothing to store
            return
    elif want == before:   # loaded with this company's value (rights.apply) and not changed
        return
    req = _current_request()
    if not want and req is not None:
        try:
            check_change(instance.profile_id, schema, False, getattr(req, 'user', None) if getattr(getattr(req, 'user', None), 'is_authenticated', False) else None)
        except AdminError as e:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({'is_superuser': [e.msg]})
    instance._zeo_admin_write = (schema, want)


def _utp_post_save(sender, instance, raw=False, **kw):
    w = getattr(instance, '_zeo_admin_write', None)
    if not w:
        return
    instance._zeo_admin_write = None
    schema, want = w
    req = _current_request()
    by = getattr(req, 'user', None) if req is not None and getattr(getattr(req, 'user', None), 'is_authenticated', False) else None
    write(instance.profile_id, schema, want, by)
    if not want:
        _admin_group(schema, instance.profile_id, False)


def _utp_post_delete(sender, instance, **kw):
    try:
        from .models import CompanyAdmin
        from django_tenants.utils import schema_context
        with schema_context('public'):
            CompanyAdmin.objects.filter(user_id=instance.profile_id).delete()
        forget()
    except Exception:
        log.exception('company administrators of a removed rights record')


def _tenants_changed(sender, instance, action, reverse, pk_set, **kwargs):
    """Access to a company removed -> the administrator rights in it go too."""
    if action not in ('post_remove', 'post_clear'):
        return
    from .models import CompanyAdmin
    if action == 'post_clear':
        if reverse:
            CompanyAdmin.objects.filter(company_id=instance.pk).delete()
        else:
            CompanyAdmin.objects.filter(user_id=instance.pk).delete()
    elif pk_set:
        if reverse:
            CompanyAdmin.objects.filter(company_id=instance.pk, user_id__in=pk_set).delete()
        else:
            CompanyAdmin.objects.filter(user_id=instance.pk, company_id__in=pk_set).delete()
    forget()


_done = False


def install():
    global _done
    if _done:
        return
    _done = True
    from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
    from tenant_users.permissions.models import UserTenantPermissions as UTP
    from UserManagement.models import CustomUser
    from .models import CompanyAdmin
    pre_save.connect(_utp_pre_save, sender=UTP, dispatch_uid='zeo_company_admin_pre')
    post_save.connect(_utp_post_save, sender=UTP, dispatch_uid='zeo_company_admin_post')
    post_delete.connect(_utp_post_delete, sender=UTP, dispatch_uid='zeo_company_admin_del')
    pre_save.connect(_user_pre_save, sender=CustomUser, dispatch_uid='zeo_company_admin_mask')
    post_save.connect(forget, sender=CustomUser, dispatch_uid='zeo_company_admin_user')
    post_save.connect(forget, sender=CompanyAdmin, dispatch_uid='zeo_company_admin_row')
    post_delete.connect(forget, sender=CompanyAdmin, dispatch_uid='zeo_company_admin_row_del')
    m2m_changed.connect(_tenants_changed, sender=CustomUser.tenants.through, dispatch_uid='zeo_company_admin_tenants')


def admin_q(schema=None, field='profile_id'):
    """A filter for "is an administrator of the company" on a query of rights records (field profile_id) or logins (id).
    Use it instead of `is_superuser=True`, which is the shared flag ("administrator of some company")."""
    from django.db.models import Q
    return Q(**{f'{field}__in': list(admin_ids(schema))})
