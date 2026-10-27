"""v1.11.0 – employee transfer between branches / departments, and settling the days of a late or early return from leave."""
from django.db import models


class EmployeeTransfer(models.Model):
    STATUS = [('requested', 'Requested – waiting for HR'), ('scheduled', 'Scheduled'), ('done', 'Done'), ('cancelled', 'Cancelled'),
              ('rejected', 'Rejected')]   # v1.45.0: requested (e.g. a move on the org chart) until HR approves it
    number = models.CharField(max_length=30, blank=True, default='', db_index=True, help_text='v1.22.1: document number (Settings › Document numbering)')
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


class EmployeeWarning(models.Model):
    """v1.45.0 – a warning given to an employee (verbal, written, final); shown on the employee timeline."""
    LEVELS = [('verbal', 'Verbal warning'), ('written', 'Written warning'), ('final', 'Final warning')]
    employee_id = models.IntegerField(db_index=True)
    date = models.DateField()
    level = models.CharField(max_length=8, choices=LEVELS, default='written')
    subject = models.CharField(max_length=150)
    details = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=[('issued', 'Issued'), ('withdrawn', 'Withdrawn')], default='issued')
    acknowledged_on = models.DateField(null=True, blank=True)
    issued_by_id = models.IntegerField(null=True, blank=True)
    issued_by_name = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
