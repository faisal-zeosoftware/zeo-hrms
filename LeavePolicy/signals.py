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
            engine.resync_year(instance)   # v1.62.2 (item 49): the other approved days of the leave year get their step again
            try:   # v1.65.0 (item 85): leave salary paid before the leave (leave type rule)
                from . import leave_rules
                leave_rules.advance_on_approved(instance)
            except Exception:
                log.exception('leave salary in advance failed for %s', instance.pk)
        elif was == 'approved' and now != 'approved':
            days = float((old or {}).get('approved_days') or (old or {}).get('number_of_days') or 0)
            engine.post(instance.employee_id, instance.leave_type_id, date.today(), 'cancelled', days, ref=instance,
                        note=f'{instance.document_number} cancelled')
            engine.sync_calendar(instance, approved=False)
            engine.resync_year(instance)   # v1.62.2 (item 49): a cancelled request frees its steps for the later days
            try:   # v1.65.0 (item 85): an advance of the leave salary is cancelled / taken back
                from . import leave_rules
                leave_rules.advance_cancelled(instance)
            except Exception:
                log.exception('leave salary advance on cancel failed for %s', instance.pk)
    except Exception:          # never block the leave itself
        log.exception('leave ledger update failed for %s', instance.pk)


@receiver(post_save, sender=LR, dispatch_uid='v1650_request_info')
def _v1650_request_info(sender, instance, created, **kwargs):
    """v1.65.0 (items 82, 84): the birth date / extension link given with the request (LeavePolicy.LeaveRequestInfo)."""
    try:
        from . import leave_rules
        leave_rules.remember(instance, created)
    except Exception:
        log.exception('leave request info failed for %s', instance.pk)


@receiver(post_save, sender=LR)
def _paid_period_arrears(sender, instance, **kwargs):
    """v1.62.5 (item 19): after the calendar was written (the handler above), the pay difference of the days in a payroll already
    made goes to the next payroll as arrear lines, when HR / payroll chose "Add to <next month> salary" (paid_period.check).
    Not caught: a leave whose difference cannot be written is not saved either (the difference would be lost again)."""
    from . import paid_period
    try:
        paid_period.after_save(instance)
    except Exception:
        log.exception('paid-period arrears failed for leave request %s', instance.pk)
        raise


# v1.62.4 (item 79): every change of a leave balance has a ledger line. The engine writes its line before it moves the balance
# (flag _lp_ledger) and an approved / cancelled request writes its line before the request model moves the balance; any other
# path (old screens, imports, the old accrual / reset / expiry jobs, the admin) changed the balance with no line, so the
# statement, the balance on a date (liability, year end) and the ledger check disagreed. Such a change now gets one line that
# brings the ledger to the balance: 'opening' when the type had no line yet, else the kind the path gives (_lp_kind) or
# 'adjustment'.
BAL = 'calendars.emp_leave_balance'


@receiver(pre_save, sender=BAL)
def _remember_balance(sender, instance, **kwargs):
    if getattr(instance, '_lp_ledger', False):
        return
    instance._lp_old_balance = sender.objects.filter(pk=instance.pk).values_list('balance', flat=True).first() if instance.pk else None


@receiver(post_save, sender=BAL)
def _balance_ledger_guard(sender, instance, created, **kwargs):
    if getattr(instance, '_lp_ledger', False):
        instance._lp_ledger = False
        return
    try:
        new = float(instance.balance or 0)
        old = float(getattr(instance, '_lp_old_balance', None) or 0)
        if abs(new - old) < 0.005:
            return
        from django.db.models import Sum
        Ledger = engine._models()[3]
        lines = Ledger.objects.filter(employee_id=instance.employee_id, leave_type_id=instance.leave_type_id)
        led = float(lines.aggregate(s=Sum('days'))['s'] or 0)
        gap = round(new - led, 2)
        if abs(gap) < 0.005:      # explained already (leave request line written first)
            return
        kind = getattr(instance, '_lp_kind', None) or ('opening' if not lines.exists() else 'adjustment')
        note = getattr(instance, '_lp_note', None) or f'Balance changed outside the leave ledger: {old:g} → {new:g}'
        Ledger.objects.create(employee_id=instance.employee_id, leave_type_id=instance.leave_type_id, date=date.today(), kind=kind,
                              days=gap, note=note[:255])
    except Exception:          # never block the save of the balance
        log.exception('leave balance ledger guard failed for %s', instance.pk)


@receiver(post_save, sender='PayrollManagement.Payslip')
def _mark_encashments_paid(sender, instance, created, **kwargs):
    """The approved encashments a payslip paid (variable leave_encashment_amount) are marked processed with the run."""
    if not created or not instance.payroll_run_id:
        return
    try:
        # v1.15.1: only when the employee's salary has a component that pays them (formula with leave_encashment_amount);
        # before, encashments were marked paid by every payslip although no component paid them
        ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
        if not ESS.objects.filter(employee_id=instance.employee_id, is_active=True, component__component_value_type='variable',
                                  component__formula__icontains='leave_encashment_amount').exists():
            return
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


# v1.62.11 (item 258): an approved leave encashment changed afterwards (days, amount, leave type) keeps the leave ledger right: the old
# "encashed" line is reversed and the new one posted (both move the balance), so balance = ledger (`run_leave_jobs --check`). Before,
# the days could be changed with no ledger line and no balance change (4 days taken from the balance, 6 paid). Refused when the
# encashment is already paid in a payroll (processed / linked to a run) or its period is locked (payctl, item 231).
LE_MODEL = 'PayrollManagement.LeaveEncashment'


@receiver(pre_save, sender=LE_MODEL, dispatch_uid='v16211_encashment_edit_check')
def _v16211_encashment_before(sender, instance, **kwargs):
    instance._v16211_enc_old = None
    if not instance.pk:
        return
    old = sender.objects.filter(pk=instance.pk).values('status', 'encashment_days', 'encashment_amount', 'leave_type_id', 'employee_id',
                                                        'payroll_run_id', 'approved_at').first()
    if old is None or old['status'] not in ('approved', 'processed') or instance.status != old['status']:
        return
    from decimal import Decimal
    days_changed = Decimal(str(old['encashment_days'] or 0)) != Decimal(str(instance.encashment_days or 0)) or \
        old['leave_type_id'] != instance.leave_type_id or old['employee_id'] != instance.employee_id
    amount_changed = Decimal(str(old['encashment_amount'] or 0)).quantize(Decimal('0.01')) != \
        Decimal(str(instance.encashment_amount or 0)).quantize(Decimal('0.01'))
    if not (days_changed or amount_changed):
        return
    from django.core.exceptions import ValidationError
    from Translations.i18n import tr
    if old['status'] == 'processed' or old['payroll_run_id']:
        run = apps.get_model('PayrollManagement', 'PayrollRun').objects.filter(pk=old['payroll_run_id']).first() if old['payroll_run_id'] else None
        raise ValidationError(tr('This leave encashment is already paid in the payroll ({0}): it cannot be changed. Make a new encashment '
                                 'for the difference, or delete / recalculate that payroll run first.', str(run) if run else tr('final settlement')))
    try:
        from PayrollPlus import payctl
        br = apps.get_model('EmpManagement', 'emp_master').objects.filter(pk=old['employee_id']).values_list('emp_branch_id', flat=True).first()
        for d in (old['approved_at'], date.today()):
            lk = payctl.locked_day(d, br) if d else None
            if lk is not None:
                raise payctl.PeriodLocked(payctl.lock_text(lk))
    except ImportError:
        pass
    if days_changed:
        b = engine.balance_row(instance.employee_id, instance.leave_type_id)
        room = float(b.balance or 0) + (float(old['encashment_days'] or 0) if old['leave_type_id'] == instance.leave_type_id
                                        and old['employee_id'] == instance.employee_id else 0)
        lt = apps.get_model('calendars', 'leave_type').objects.filter(pk=instance.leave_type_id).first()
        if float(instance.encashment_days or 0) > room + 1e-9 and not getattr(lt, 'negative', False):
            raise ValidationError(tr('Only {0:g} days in the balance.', round(room, 2)))
    instance._v16211_enc_old = dict(old, days_changed=days_changed)


@receiver(post_save, sender=LE_MODEL, dispatch_uid='v16211_encashment_edit_ledger')
def _v16211_encashment_after(sender, instance, created, **kwargs):
    old = getattr(instance, '_v16211_enc_old', None)
    instance._v16211_enc_old = None
    if not old:
        return
    uid = None
    try:
        from Chatter.tracking import current_request
        r = current_request()
        uid = getattr(getattr(r, 'user', None), 'pk', None) if r is not None else None
    except Exception:
        pass
    days, amt = float(instance.encashment_days or 0), float(instance.encashment_amount or 0)
    if old['days_changed']:
        od = float(old['encashment_days'] or 0)
        engine.post(old['employee_id'], old['leave_type_id'], date.today(), 'encashed', od, ref=instance, user_id=uid, move_balance=True,
                    note=f'Encashment changed: {od:g} days – AED {float(old["encashment_amount"] or 0):,.2f} reversed')
        engine.post(instance.employee_id, instance.leave_type_id, date.today(), 'encashed', -days, ref=instance, user_id=uid, move_balance=True,
                    note=f'Encashed {days:g} days – AED {amt:,.2f} (changed; paid with the next payroll)')
    else:   # only the amount: the days in the ledger stay, its line shows the new amount
        Ledger = engine._models()[3]
        last = Ledger.objects.filter(ref_model=instance._meta.label, ref_id=instance.pk, kind='encashed', days__lt=0).order_by('-id').first()
        if last is not None:
            last.note = f'Encashed {days:g} days – AED {amt:,.2f} (amount changed; paid with the next payroll)'[:255]
            last.save(update_fields=['note'])
