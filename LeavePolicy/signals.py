"""
Leave requests → leave ledger and attendance calendar (pay slabs); payslips → encashments marked as paid (v1.10.0).
The existing request model keeps moving the balance itself; these handlers add the ledger line and the pay effect.
"""
import logging
from datetime import date

from django.apps import apps
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from . import engine

log = logging.getLogger(__name__)
LR = 'calendars.employee_leave_request'


@receiver(pre_save, sender=LR)
def _remember_status(sender, instance, **kwargs):
    old = sender.objects.filter(pk=instance.pk).values('status', 'approved_days', 'applied_days', 'number_of_days').first() if instance.pk else None
    instance._lp_old = old


@receiver(post_save, sender=LR)
def _on_leave_saved(sender, instance, created, **kwargs):
    old = getattr(instance, '_lp_old', None)
    was = (old or {}).get('status')
    now = instance.status
    try:
        from django.db.models import Sum
        net = engine._models()[3].objects.filter(ref_model=instance._meta.label, ref_id=instance.pk, kind__in=('taken', 'cancelled')) \
            .aggregate(s=Sum('days'))['s'] or 0
        # the request can be saved again inside its own post_save (auto-approval without a workflow): post once per change
        if now == 'approved' and was != 'approved' and net < 0:
            return
        if now != 'approved' and was == 'approved' and net >= 0:
            return
        if now == 'approved' and was != 'approved':
            days = float(instance.approved_days or instance.number_of_days or 0)
            engine.post(instance.employee_id, instance.leave_type_id, instance.start_date, 'taken', -days, ref=instance,
                        note=f'{instance.document_number} {instance.start_date:%d/%m/%Y}–{instance.end_date:%d/%m/%Y}')
            engine.sync_calendar(instance, approved=True)
        elif was == 'approved' and now != 'approved':
            days = float((old or {}).get('approved_days') or (old or {}).get('number_of_days') or 0)
            engine.post(instance.employee_id, instance.leave_type_id, date.today(), 'cancelled', days, ref=instance,
                        note=f'{instance.document_number} cancelled')
            engine.sync_calendar(instance, approved=False)
    except Exception:          # never block the leave itself
        log.exception('leave ledger update failed for %s', instance.pk)


@receiver(post_save, sender='PayrollManagement.Payslip')
def _mark_encashments_paid(sender, instance, created, **kwargs):
    """The approved encashments a payslip paid (variable leave_encashment_amount) are marked processed with the run."""
    if not created or not instance.payroll_run_id:
        return
    try:
        LE = apps.get_model('PayrollManagement', 'LeaveEncashment')
        run = instance.payroll_run
        end = run.attendance_end_date or date(run.year, run.month, 28)
        LE.objects.filter(employee_id=instance.employee_id, status='approved', payroll_run__isnull=True,
                          approved_at__date__lte=end).update(status='processed', payroll_run=run)
    except Exception:
        log.exception('marking encashments for payslip %s failed', instance.pk)


@receiver(post_save, sender='calendars.LeaveApproval')
def _start_clock(sender, instance, created, **kwargs):
    """v1.11.0: remember when an approval step started waiting (escalation counts from here)."""
    if not created or instance.status != 'Pending':
        return
    try:
        from django.utils import timezone
        from .models import ApprovalClock
        ApprovalClock.objects.get_or_create(approval_id=instance.pk, defaults={'started_at': timezone.now()})
    except Exception:
        log.exception('approval clock failed for %s', instance.pk)
