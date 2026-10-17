"""v1.23.0 – limits checked when employees and users are added (only for companies linked to a cloud account)."""
import logging

from django.db import connection
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save

log = logging.getLogger(__name__)
_done = False


def _err(msg):
    from rest_framework.exceptions import ValidationError
    return ValidationError({'detail': msg, 'cloud': 'limit'})


def employee_pre_save(sender, instance, **kwargs):
    from .services import LimitError, check_employee_add, cloud_on
    if not cloud_on() or instance.pk:
        return
    schema = getattr(connection, 'schema_name', 'public')
    if schema == 'public' or getattr(instance, 'is_active', True) is False:
        return
    try:
        check_employee_add(schema)
    except LimitError as e:
        raise _err(str(e))


def tenants_changed(sender, instance, action, reverse, pk_set, **kwargs):
    from .services import LimitError, check_user_add, cloud_on, notice_removed
    from .models import AccountCompany
    if not cloud_on() or not pk_set:
        return
    from UserManagement.models import CustomUser, company as Company
    if action == 'pre_add':
        pairs = [(instance, c) for c in Company.objects.filter(pk__in=pk_set)] if not reverse else \
                [(u, instance) for u in CustomUser.objects.filter(pk__in=pk_set)]
        for u, c in pairs:
            try:
                check_user_add(u, c)
            except LimitError as e:
                raise _err(str(e))
    elif action == 'post_add':
        # v1.24.0: a user given a company in an own database needs a copy of the user row there
        ids = [instance.pk] if not reverse else list(pk_set)
        comp_ids = list(pk_set) if not reverse else [instance.pk]
        keys = set(AccountCompany.objects.filter(company_id__in=comp_ids).exclude(db_alias='').values_list('db_alias', flat=True))
        if keys:
            _push_users(ids, keys)
    elif action == 'post_remove':
        pairs = [(instance, cid) for cid in pk_set] if not reverse else [(u, instance.pk) for u in CustomUser.objects.filter(pk__in=pk_set)]
        for u, cid in pairs:
            link = AccountCompany.objects.select_related('account').filter(company_id=cid).first()
            if link is None:
                continue
            still = u.tenants.filter(cloud__account=link.account).exists()
            if not still:
                notice_removed(u, link.account)


def _own_dbs():
    from .models import DatabaseServer
    try:
        return DatabaseServer.objects.filter(state='ready').exists()
    except Exception:
        return False


def _push_users(ids, keys=None):
    """After the transaction is committed (the copy is read through another connection)."""
    from .services import cloud_on
    if not cloud_on() or not _own_dbs():
        return
    from django.db import transaction
    from . import owndb

    def go():
        for i in ids:
            owndb.push_user(i, keys)
    transaction.on_commit(go)


def user_saved(sender, instance, raw=False, **kwargs):
    if not raw:
        _push_users([instance.pk])


def shared_row_saved(sender, instance, raw=False, **kwargs):
    """v1.24.0: masters (countries, currencies …) and content types are copied to every own database."""
    from .services import cloud_on
    if raw or not cloud_on() or not _own_dbs():
        return
    from django.db import transaction
    from . import owndb
    pk = instance.pk
    transaction.on_commit(lambda: owndb.push_row(sender, pk))


def connect():
    global _done
    if _done:
        return
    _done = True
    try:
        from EmpManagement.models import emp_master
        pre_save.connect(employee_pre_save, sender=emp_master, dispatch_uid='zeocloud_emp_limit')
    except Exception:
        log.exception('employee limit signal')
    try:
        from UserManagement.models import CustomUser
        m2m_changed.connect(tenants_changed, sender=CustomUser.tenants.through, dispatch_uid='zeocloud_tenants')
        post_save.connect(user_saved, sender=CustomUser, dispatch_uid='zeocloud_user_copy')
    except Exception:
        log.exception('user limit signal')
    try:   # v1.26.0 per-company rights of invited logins
        from UserManagement.models import CustomUser
        from .models import CompanyAccess
        m2m_changed.connect(access_tenants_changed, sender=CustomUser.tenants.through, dispatch_uid='zeocloud_access_tenants')
        post_save.connect(_rights_forget, sender=CompanyAccess, dispatch_uid='zeocloud_access_save')
        post_delete.connect(_rights_forget, sender=CompanyAccess, dispatch_uid='zeocloud_access_del')
    except Exception:
        log.exception('company access signals')
    try:
        from django.apps import apps
        from django.contrib.contenttypes.models import ContentType
        for m in list(apps.get_app_config('Core').get_models()) + [ContentType]:
            post_save.connect(shared_row_saved, sender=m, dispatch_uid=f'zeocloud_copy_{m._meta.db_table}')
    except Exception:
        log.exception('shared row copy signals')


def _rights_forget(*a, **kw):
    from . import rights
    rights.forget()


def access_tenants_changed(sender, instance, action, reverse, pk_set, **kwargs):
    """v1.26.0: access to a company removed -> the invited rights for it go too (on every server, cloud or not)."""
    if action != 'post_remove' or not pk_set:
        return
    from .models import CompanyAccess
    if reverse:
        CompanyAccess.objects.filter(company_id=instance.pk, user_id__in=pk_set).delete()
    else:
        CompanyAccess.objects.filter(user_id=instance.pk, company_id__in=pk_set).delete()
