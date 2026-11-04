# v1.62.11 (items 228, 231): new module – the signal receivers of the payroll controls (PayrollPlus/payctl.py), connected in apps.ready
import logging

from django.db.models.signals import post_save, pre_delete, pre_save
from django.dispatch import receiver

log = logging.getLogger(__name__)
_FIN = ('gross_salary', 'net_salary', 'total_deductions', 'total_additions', 'total_working_days', 'days_worked', 'pro_rata_adjustment',
        'arrears', 'employee_id', 'payroll_run_id')


def _pc():
    from . import payctl
    return payctl


# ------------------------------------------------------------------ period locking (item 231)
@receiver(pre_save, sender='PayrollManagement.PayrollRun', dispatch_uid='v16211_lock_run_save')
def _lock_run_save(sender, instance, **kwargs):
    pc = _pc()
    if not pc.active_locks():
        return
    from datetime import date
    try:
        m = date(int(instance.year), int(instance.month), 1)
    except (TypeError, ValueError):
        return
    if instance.pk is None:
        lk = pc.locked_company_month(m)
        if lk is not None:
            raise pc.PeriodLocked(pc.lock_text(lk))
        return
    old = sender.objects.filter(pk=instance.pk).values('year', 'month', 'attendance_start_date', 'attendance_end_date').first()
    if old and (old['year'], old['month']) != (instance.year, instance.month):
        for mm in (m, date(int(old['year']), int(old['month']), 1)):
            lk = pc.locked_company_month(mm)
            if lk is not None:
                raise pc.PeriodLocked(pc.lock_text(lk))


@receiver(pre_delete, sender='PayrollManagement.PayrollRun', dispatch_uid='v16211_lock_run_delete')
def _lock_run_delete(sender, instance, **kwargs):
    pc = _pc()
    if not pc.active_locks():
        return
    P = sender.payslips.rel.related_model
    for p in P.objects.filter(payroll_run=instance).select_related('payroll_run')[:2000]:
        pc.guard_payslip(p, 'delete')
    lk = pc.locked_company_month(pc.run_month(instance))
    if lk is not None and pc._run_kind(instance)[0] != 'offcycle':
        raise pc.PeriodLocked(pc.lock_text(lk))


@receiver(pre_save, sender='PayrollManagement.Payslip', dispatch_uid='v16211_lock_payslip_save')
def _lock_payslip_save(sender, instance, update_fields=None, **kwargs):
    pc = _pc()
    if not pc.active_locks():
        return
    if instance.pk is None:
        pc.guard_payslip(instance)
        return
    if update_fields is not None and not (set(update_fields) & set(_FIN)):
        return   # status, PDF, e-mail flag … change no amount
    old = sender.objects.filter(pk=instance.pk).values(*_FIN).first()
    if old is None:
        return
    if any(str(old[k]) != str(getattr(instance, k)) for k in _FIN):
        pc.guard_payslip(instance)


@receiver(pre_delete, sender='PayrollManagement.Payslip', dispatch_uid='v16211_lock_payslip_delete')
def _lock_payslip_delete(sender, instance, **kwargs):
    _pc().guard_payslip(instance, 'delete')


@receiver(pre_save, sender='PayrollManagement.PayslipComponent', dispatch_uid='v16211_lock_line_save')
def _lock_line_save(sender, instance, **kwargs):
    pc = _pc()
    if not pc.active_locks():
        return
    if instance.pk is not None:
        old = sender.objects.filter(pk=instance.pk).values_list('amount', flat=True).first()
        if old is not None and pc.q2(old) == pc.q2(instance.amount):
            return
    try:
        pc.guard_payslip(instance.payslip)
    except sender.payslip.RelatedObjectDoesNotExist:
        return


@receiver(pre_delete, sender='PayrollManagement.PayslipComponent', dispatch_uid='v16211_lock_line_delete')
def _lock_line_delete(sender, instance, **kwargs):
    pc = _pc()
    if not pc.active_locks():
        return
    try:
        pc.guard_payslip(instance.payslip)
    except Exception as ex:
        if isinstance(ex, pc.PeriodLocked):
            raise


@receiver(pre_save, sender='calendars.Attendance', dispatch_uid='v16211_lock_att_save')
def _lock_att_save(sender, instance, **kwargs):
    pc = _pc()
    if not pc.active_locks():
        return
    pc.guard_day(instance.employee_id, instance.date)
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).values_list('date', flat=True).first()
        if old and old != instance.date:
            pc.guard_day(instance.employee_id, old)


@receiver(pre_delete, sender='calendars.Attendance', dispatch_uid='v16211_lock_att_delete')
def _lock_att_delete(sender, instance, **kwargs):
    _pc().guard_day(instance.employee_id, instance.date)


@receiver(pre_save, sender='calendars.EmployeeOvertime', dispatch_uid='v16211_lock_ot_save')
def _lock_ot_save(sender, instance, **kwargs):
    _pc().guard_day(instance.employee_id, instance.date)


@receiver(pre_delete, sender='calendars.EmployeeOvertime', dispatch_uid='v16211_lock_ot_delete')
def _lock_ot_delete(sender, instance, **kwargs):
    _pc().guard_day(instance.employee_id, instance.date)


@receiver(pre_save, sender='PayrollManagement.EmployeeSalaryStructure', dispatch_uid='v16211_lock_salary')
def _lock_salary(sender, instance, **kwargs):
    _pc().guard_salary_change(instance)


# ------------------------------------------------------------------ hold / release (item 228)
@receiver(pre_delete, sender='PayrollManagement.Payslip', dispatch_uid='v16211_hold_payslip_deleted')
def _hold_payslip_deleted(sender, instance, **kwargs):
    try:
        _pc().payslip_deleted(instance)
    except Exception:
        log.exception('holds of deleted payslip %s were not given back', instance.pk)


# ------------------------------------------------------------------ pay-date shift (item 231)
@receiver(pre_save, sender='PayrollManagement.PayrollRun', dispatch_uid='v16211_pay_date_shift')
def _pay_date_shift(sender, instance, **kwargs):
    if not instance.pk or not instance.payment_date:
        return
    old = sender.objects.filter(pk=instance.pk).values_list('payment_date', flat=True).first()
    if old == instance.payment_date:
        return
    try:
        _pc().apply_pay_date_shift(instance, save=False)
    except Exception:
        log.exception('pay date shift of run %s', instance.pk)


@receiver(post_save, sender='PayrollManagement.PayrollRunBranch', dispatch_uid='v16211_pay_date_shift_branch')
def _pay_date_shift_branch(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        run = instance.payroll_run
        _pc().apply_pay_date_shift(run, save=True)
    except Exception:
        log.exception('pay date shift of run %s', instance.payroll_run_id)


# ------------------------------------------------------------------ loan limit by gratuity (item 231)
@receiver(pre_save, sender='PayrollManagement.LoanApplication', dispatch_uid='v16211_loan_limit')
def _loan_limit(sender, instance, **kwargs):
    _pc().check_loan(instance)


# ------------------------------------------------------------------ revise salary with approval (item 228)
@receiver(post_save, sender='EmpManagement.GeneralRequest', dispatch_uid='v16211_revision_request')
def _revision_request(sender, instance, **kwargs):
    if not str(getattr(instance, 'remarks', '') or '').startswith('SALREV:'):
        return
    try:
        _pc().on_request_saved(instance)
    except Exception:
        log.exception('salary revision of general request %s', instance.pk)
