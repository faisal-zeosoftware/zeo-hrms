# v1.63.0 (items 27, 28): new module – signal receivers of the salary split and the recovery schedules, connected in apps.ready
import logging

from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

log = logging.getLogger(__name__)


@receiver(post_save, sender='EmpManagement.GeneralRequest', dispatch_uid='v1630_recovery_request')
def _recovery_request(sender, instance, **kwargs):
    """v1.63.0 (item 28): a "Recovery schedule" request approved → the recovery is active; rejected → rejected"""
    if not str(getattr(instance, 'remarks', '') or '').startswith('RECOV:'):
        return
    try:
        from . import recoveries
        recoveries.on_request_saved(instance)
    except Exception:
        log.exception('recovery of general request %s', instance.pk)


@receiver(pre_delete, sender='PayrollManagement.Payslip', dispatch_uid='v1630_recovery_payslip_deleted')
def _recovery_payslip_deleted(sender, instance, **kwargs):
    """v1.63.0 (item 28): a deleted payslip (run deleted / recalculated) gives its recovery instalments back (its lines go with it)"""
    try:
        from . import recoveries
        recoveries.give_back_payslip(instance.pk, delete_lines=False)
    except Exception:
        log.exception('recovery instalments of deleted payslip %s', instance.pk)
