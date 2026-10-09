"""
Project control: finance, rates, plans and the approval / costing data of timesheet entries.

The ProjectManagement tables are not changed: every row here points to them by plain integer id
(project_id, task_id, timesheet_id, employee_id), so this app can be installed on a company without
touching the existing migrations.
"""
from decimal import Decimal

from django.db import models

BILLING_TYPES = [('fixed', 'Fixed price'), ('time_and_material', 'Time & material'), ('non_billable', 'Non-billable')]
APPROVAL_STATUS = [('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected')]


class ProjectFinance(models.Model):
    project_id = models.IntegerField(unique=True, help_text='ProjectManagement.Project id')
    customer_name = models.CharField(max_length=200, blank=True, default='')
    contract_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    budget_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text='Cost budget')
    budget_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='AED')
    billing_type = models.CharField(max_length=20, choices=BILLING_TYPES, default='time_and_material')
    default_bill_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='Per hour')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['project_id']

    def __str__(self):
        return f'Finance of project {self.project_id}'


class MemberRate(models.Model):
    project_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(db_index=True)
    role = models.CharField(max_length=100, blank=True, default='')
    bill_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text='Per hour; empty = project default')
    cost_rate_override = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                             help_text='Per hour; empty = from the salary structure')

    class Meta:
        unique_together = ('project_id', 'employee_id')
        ordering = ['project_id', 'employee_id']

    def __str__(self):
        return f'Rate of employee {self.employee_id} on project {self.project_id}'


class TaskPlan(models.Model):
    task_id = models.IntegerField(unique=True)
    planned_hours = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    billable = models.BooleanField(default=True)

    def __str__(self):
        return f'Plan of task {self.task_id}'


class TimesheetExtra(models.Model):
    timesheet_id = models.IntegerField(unique=True)
    hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    billable = models.BooleanField(default=True)
    start_at = models.DateTimeField(null=True, blank=True)
    stop_at = models.DateTimeField(null=True, blank=True)
    running = models.BooleanField(default=False, db_index=True)
    approval_status = models.CharField(max_length=12, choices=APPROVAL_STATUS, default='draft', db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approver_user_id = models.IntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    cost_rate = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    cost_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    bill_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    bill_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    corrected_from = models.JSONField(default=list, blank=True, help_text='Earlier versions of a rejected entry')

    class Meta:
        permissions = (('approve_timesheetextra', 'Can approve any timesheet entry'),)

    def __str__(self):
        return f'Timesheet {self.timesheet_id} ({self.approval_status})'

    @property
    def locked(self):
        return self.approval_status in ('submitted', 'approved')


class CostRateSetting(models.Model):
    hours_per_day = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('8'))
    include_allowance_categories = models.JSONField(
        default=list, blank=True,
        help_text='Payroll categories of additions counted as fixed pay (empty = every fixed addition except variable pay)')
    overhead_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    def __str__(self):
        return 'Cost rate settings'

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls(hours_per_day=Decimal('8'), include_allowance_categories=[], overhead_percent=Decimal('0'))
