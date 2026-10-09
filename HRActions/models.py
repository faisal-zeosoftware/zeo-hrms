"""v1.11.0 – employee transfer between branches / departments, and settling the days of a late or early return from leave."""
from django.db import models


class EmployeeTransfer(models.Model):
    STATUS = [('scheduled', 'Scheduled'), ('done', 'Done'), ('cancelled', 'Cancelled')]
    employee_id = models.IntegerField(db_index=True)
    effective_date = models.DateField()
    from_branch_id = models.IntegerField(null=True, blank=True)
    to_branch_id = models.IntegerField(null=True, blank=True)
    from_department_id = models.IntegerField(null=True, blank=True)
    to_department_id = models.IntegerField(null=True, blank=True)
    from_designation_id = models.IntegerField(null=True, blank=True)
    to_designation_id = models.IntegerField(null=True, blank=True)
    from_category_id = models.IntegerField(null=True, blank=True)
    to_category_id = models.IntegerField(null=True, blank=True)
    from_manager_id = models.IntegerField(null=True, blank=True, help_text='reporting manager (user id)')
    to_manager_id = models.IntegerField(null=True, blank=True)
    salary_changes = models.JSONField(default=dict, blank=True, help_text='{component code: new monthly amount}')
    options = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUS, default='scheduled')
    result = models.JSONField(default=dict, blank=True, help_text='what was moved')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-effective_date', '-id']
        permissions = [('run_employee_transfer', 'Can carry out employee transfers')]


class RejoinSettlement(models.Model):
    TREATMENT = [('unpaid', 'Unpaid leave'), ('leave', 'From a leave balance'), ('split', 'Leave balance first, rest unpaid'),
                 ('absent', 'Absent'), ('excused', 'Excused (paid, no leave)'), ('early', 'Early return: days back to the balance'),
                 ('on_time', 'On time')]
    rejoining_id = models.IntegerField(unique=True, help_text='calendars.EmployeeRejoining')
    employee_id = models.IntegerField(db_index=True)
    leave_request_id = models.IntegerField()
    rejoin_date = models.DateField()
    gap_days = models.FloatField(default=0)
    treatment = models.CharField(max_length=10, choices=TREATMENT)
    leave_type_id = models.IntegerField(null=True, blank=True)
    days_from_leave = models.FloatField(default=0)
    unpaid_days = models.FloatField(default=0)
    absent_days = models.FloatField(default=0)
    excused_days = models.FloatField(default=0)
    returned_days = models.FloatField(default=0)
    request_ids = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')
    settled_by_id = models.IntegerField(null=True, blank=True)
    settled_at = models.DateTimeField(auto_now_add=True)
