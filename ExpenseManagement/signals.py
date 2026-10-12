"""Payslips → expense reports set to be paid with the payroll are marked reimbursed with that payroll run."""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


@receiver(post_save, sender='PayrollManagement.Payslip')
def _mark_expenses_paid(sender, instance, created, **kwargs):
    """The approved reports a payslip paid (variable expense_reimbursement_amount) are marked reimbursed with the run."""
    if not created or not getattr(instance, 'payroll_run_id', None):
        return
    try:
        # v1.15.1: only when a salary component of the employee pays them (formula with expense_reimbursement_amount);
        # before, reports were marked reimbursed by every payslip although nothing paid them
        from django.apps import apps
        ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
        if not ESS.objects.filter(employee_id=instance.employee_id, is_active=True, component__component_value_type='variable',
                                  component__formula__icontains='expense_reimbursement_amount').exists():
            return
        from .services import mark_paid_by_payslip
        mark_paid_by_payslip(instance)
    except Exception:
        log.exception('marking expense reports for payslip %s failed', getattr(instance, 'pk', None))
