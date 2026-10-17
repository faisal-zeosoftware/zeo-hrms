"""v1.28.0 – licence limits (only with ZEO_EDITION=enterprise): active employees, companies and users."""
import logging
import time

from django.db.models.signals import m2m_changed, pre_save

log = logging.getLogger(__name__)
_emp = {'until': 0, 'n': 0}


def _err(msg):
    from rest_framework.exceptions import ValidationError
    return ValidationError({'detail': msg, 'licence': 'limit'})


def employee_pre_save(sender, instance, **kwargs):
    from .licence import LimitReached, check_add, edition_on, limit, usage
    if not edition_on() or limit('employees') is None or getattr(instance, 'is_active', True) is False:
        return
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).values_list('is_active', flat=True).first()
        if old:          # already counted
            return
    now = time.time()
    if _emp['until'] < now:
        _emp['n'], _emp['until'] = usage()['employees'], now + 60
    try:
        check_add('employees', _emp['n'])
    except LimitReached as e:
        raise _err(str(e))
    _emp['n'] += 1


def company_pre_save(sender, instance, **kwargs):
    from .licence import LimitReached, check_add, edition_on, limit
    if not edition_on() or instance.pk or limit('companies') is None:
        return
    n = sender.objects.exclude(schema_name__in=('public', 'zeo_template')).count()
    try:
        check_add('companies', n)
    except LimitReached as e:
        raise _err(str(e))


def tenants_changed(sender, instance, action, reverse, pk_set, **kwargs):
    from UserManagement.models import CustomUser
    from .licence import LimitReached, check_add, edition_on, limit
    if action != 'pre_add' or not edition_on() or limit('users') is None or not pk_set:
        return
    users = [instance] if not reverse else list(CustomUser.objects.filter(pk__in=pk_set))
    active = CustomUser.objects.filter(is_active=True).exclude(is_ess=True).filter(tenants__isnull=False).distinct()
    for u in users:
        if u.is_ess or active.filter(pk=u.pk).exists():
            continue
        try:
            check_add('users', active.count())
        except LimitReached as e:
            raise _err(str(e))


def connect():
    try:
        from EmpManagement.models import emp_master
        from UserManagement.models import CustomUser, company
        pre_save.connect(employee_pre_save, sender=emp_master, dispatch_uid='zeolicence_emp')
        pre_save.connect(company_pre_save, sender=company, dispatch_uid='zeolicence_company')
        m2m_changed.connect(tenants_changed, sender=CustomUser.tenants.through, dispatch_uid='zeolicence_users')
    except Exception:
        log.exception('licence signals')
