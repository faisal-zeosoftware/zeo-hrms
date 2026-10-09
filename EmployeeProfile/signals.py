"""v1.13.0 – keep EmploymentInfo in step with the employee, resignations and end of service.

Every handler is guarded: it does nothing in the public schema or in a company whose EmployeeProfile tables are not
migrated yet, and it never lets an error stop the original save.
"""
import logging

from django.db.models.signals import post_save

log = logging.getLogger(__name__)
_connected = False


def _employee_saved(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    from . import services as S
    if not S.table_ready():
        return
    try:
        if created:
            S.ensure_info(instance)
        else:
            from .models import EmploymentInfo
            info = EmploymentInfo.objects.filter(employee_id=instance.pk).first()
            if info is not None:
                S.refresh_probation(instance, info)
    except Exception:
        log.exception('EmployeeProfile: employment info on employee save')


def _resignation_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from . import services as S
    if not S.table_ready():
        return
    try:
        S.sync_from_resignation(instance)
    except Exception:
        log.exception('EmployeeProfile: leaving from resignation')


def _eos_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from . import services as S
    if not S.table_ready():
        return
    try:
        S.sync_from_eos(instance)
    except Exception:
        log.exception('EmployeeProfile: leaving from end of service')


def connect():
    global _connected
    if _connected:
        return
    from django.apps import apps
    post_save.connect(_employee_saved, sender=apps.get_model('EmpManagement', 'emp_master'), dispatch_uid='empprofile_emp_saved')
    post_save.connect(_resignation_saved, sender=apps.get_model('EmpManagement', 'EmployeeResignation'), dispatch_uid='empprofile_resignation_saved')
    post_save.connect(_eos_saved, sender=apps.get_model('EmpManagement', 'EndOfService'), dispatch_uid='empprofile_eos_saved')
    _connected = True
