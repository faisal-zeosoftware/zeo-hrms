# v1.67.11 (loans): new module
"""v1.67.11 (loans) – a deleted payslip (run deleted / recalculated) took no advance instalment and cut nothing: its AdvanceInstalment and
DeductionCut rows go; cuts it had taken wait again for the next payslip."""
import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver

log = logging.getLogger(__name__)


@receiver(pre_delete, sender='PayrollManagement.Payslip')
def _v16711_payslip_deleted(sender, instance, **kwargs):
    try:
        from django.db import transaction
        from .lending import give_back_payslip
        with transaction.atomic():
            give_back_payslip(instance.pk, delete=True)
    except Exception:
        log.exception('advance instalments of deleted payslip %s', instance.pk)
