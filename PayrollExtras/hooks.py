"""v1.68.0 – the retention bonus clawback is put on a leaver's draft final settlement when it is made (and again on each save of
the draft, once per bonus)."""
import logging

log = logging.getLogger(__name__)


def _settlement_saved(sender, instance, created, raw=False, **kw):
    if raw or instance.status != 'draft' or getattr(instance, '_zeo_pe_busy', False):
        return
    try:
        from django.apps import apps
        from django.db import transaction
        RB = apps.get_model('PayrollExtras', 'RetentionBonus')
        with transaction.atomic():   # a company not migrated yet (no table) must not spoil the caller's transaction
            if not RB.objects.filter(employee_id=instance.employee_id, status__in=('sent', 'paid')).exists():
                return
        instance._zeo_pe_busy = True
        try:
            from .services import add_clawbacks
            add_clawbacks(instance)
        finally:
            instance._zeo_pe_busy = False
    except Exception:   # noqa: BLE001 – never stops the settlement
        log.debug('retention clawback', exc_info=True)


def _payslip_deleted(sender, instance, **kwargs):
    try:
        from .paid import forget
        forget(instance.pk)
    except Exception:   # noqa: BLE001
        pass


def _run_deleted(sender, instance, **kwargs):
    try:
        from django.apps import apps as _a
        _a.get_model('PayrollExtras', 'PayrollSkip').objects.filter(run_id=instance.pk).delete()
    except Exception:   # noqa: BLE001
        pass


def _template_employees(sender, instance, action, pk_set, **kwargs):
    """v1.73.7: employees added to a salary template get its default amounts in their empty (0) amounts"""
    if action != 'post_add' or not pk_set:
        return
    try:
        from .templates import fill
        fill(instance, list(pk_set))
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('template defaults')


def connect():
    from django.apps import apps
    from django.db.models.signals import post_save
    from django.db.models.signals import m2m_changed
    from django.db.models.signals import post_delete
    try:   # v1.73.7: a deleted payslip's days are free again (and a deleted run's skips go)
        post_delete.connect(_payslip_deleted, sender=apps.get_model('PayrollManagement', 'Payslip'), dispatch_uid='pe_paid_forget')
        post_delete.connect(_run_deleted, sender=apps.get_model('PayrollManagement', 'PayrollRun'), dispatch_uid='pe_skip_forget')
    except LookupError:
        pass
    try:   # after PayrollManagement's own receiver (it makes the lines at 0) – registered later, so it runs later
        m2m_changed.connect(_template_employees, sender=apps.get_model('PayrollManagement', 'SalaryStructure').employees.through,
                            dispatch_uid='pe_template_defaults')
    except LookupError:
        pass
    try:
        post_save.connect(_settlement_saved, sender=apps.get_model('PayrollPlus', 'ExitSettlement'), dispatch_uid='pe_clawback')
    except LookupError:
        pass
