"""Build the journal when a payroll run or a final settlement is approved (setting "build the journal when … approved")."""
import logging

from django.apps import apps
from django.db.models.signals import post_save

log = logging.getLogger(__name__)


def _run_saved(sender, instance, **kw):
    if instance.status != 'approved':
        return
    try:
        from .services import on_payroll_status
        on_payroll_status(instance)
    except Exception:   # noqa: BLE001 – never stop the payroll approval
        log.exception('accounting journal for payroll run %s', instance.pk)


def _settlement_saved(sender, instance, **kw):
    if instance.status != 'approved':
        return
    try:
        from .services import on_settlement_status
        on_settlement_status(instance)
    except Exception:   # noqa: BLE001
        log.exception('accounting journal for settlement %s', instance.pk)


def connect():
    post_save.connect(_run_saved, sender=apps.get_model('PayrollManagement', 'PayrollRun'), dispatch_uid='zeoacc-run')
    try:
        post_save.connect(_settlement_saved, sender=apps.get_model('PayrollPlus', 'ExitSettlement'), dispatch_uid='zeoacc-settlement')
    except LookupError:
        pass
