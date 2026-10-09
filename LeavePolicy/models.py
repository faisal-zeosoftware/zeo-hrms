"""
Employee-wise leave policies and the UAE leave rules (v1.10.0).

A policy (e.g. "Office staff", "Labour") holds one line per leave type: days a year, how they are earned,
how days are counted, who may take it (gender, service, probation), limits, documents, pay slabs,
carry forward and encashment. Employees get a policy through their category, or one assigned to them.

The tables refer to leave types, employees and users by id (no foreign keys into the existing apps), so this app
installs with its own migration and needs no change to the tables of the existing apps.
"""
from django.db import models


class LeavePolicy(models.Model):
    KINDS = [('office', 'Office staff'), ('labour', 'Labour / site staff'), ('other', 'Other')]
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS, default='office')
    description = models.TextField(blank=True, default='')
    category_ids = models.JSONField(default=list, blank=True, help_text='Employee categories that get this policy')
    is_default = models.BooleanField(default=False, help_text='For employees whose category has no policy')
    leave_year_start = models.PositiveSmallIntegerField(default=1, help_text='Month the leave year starts (1 = January)')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        permissions = (('run_leave_accrual', 'Can run leave accrual and year end'),)

    def __str__(self):
        return self.name


class LeavePolicyLine(models.Model):
    ACCRUAL = [('monthly', 'Monthly (earned each month)'), ('yearly', 'Yearly (full days at the start of the leave year)'),
               ('per_event', 'Per event (no balance, limits per request / year)'), ('none', 'No balance (unpaid / on request)'),
               ('earned', 'Earned by work (compensatory off)')]
    PRORATE = [('none', 'No prorate (full amount)'), ('calendar', 'Calendar days of the month'), ('fixed', 'Fixed days a month (e.g. 30)'),
               ('worked', 'Days worked (attendance)')]
    WORKED_FROM = [('punch', 'Check-ins (attendance records)'), ('calendar', 'Attendance calendar (Present days)')]
    COUNT = [('calendar', 'Calendar days'), ('working', 'Working days (no weekends or public holidays)')]
    GENDER = [('B', 'Both'), ('M', 'Male'), ('F', 'Female')]
    EXCESS = [('keep', 'Keep (flag for HR)'), ('encash', 'Encash (request for approval)'), ('lapse', 'Lapse')]
    policy = models.ForeignKey(LeavePolicy, on_delete=models.CASCADE, related_name='lines')
    leave_type_id = models.IntegerField()
    days_per_year = models.FloatField(default=0)
    accrual = models.CharField(max_length=20, choices=ACCRUAL, default='monthly')
    first_year_uae = models.BooleanField(default=False, help_text='UAE annual leave: nothing in the first 6 months, then 2 days a month until 1 year')
    count_days = models.CharField(max_length=10, choices=COUNT, default='calendar')
    gender = models.CharField(max_length=1, choices=GENDER, default='B')
    min_service_months = models.PositiveIntegerField(default=0)
    after_probation = models.BooleanField(default=False)
    notice_days = models.PositiveIntegerField(default=0, help_text='Days in advance the request must be made')
    max_per_request = models.FloatField(null=True, blank=True)
    max_per_year = models.FloatField(null=True, blank=True)
    max_times_in_service = models.PositiveIntegerField(null=True, blank=True)
    requires_document = models.BooleanField(default=False)
    allow_negative = models.BooleanField(default=False)
    pay_slabs = models.JSONField(default=list, blank=True, help_text='[[days, pay %], …] per leave year, e.g. [[15,100],[30,50],[45,0]]')
    carry_forward_max = models.FloatField(null=True, blank=True, help_text='Days carried to the next leave year (empty = all)')
    carry_forward_expiry_months = models.PositiveIntegerField(null=True, blank=True)
    excess_action = models.CharField(max_length=10, choices=EXCESS, default='keep')
    encashable = models.BooleanField(default=False)
    encash_max_per_year = models.FloatField(null=True, blank=True)
    law_reference = models.CharField(max_length=200, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    # v1.11.0
    prorate = models.CharField(max_length=10, choices=PRORATE, default='calendar',
                               help_text='Part month / part year on joining and leaving (and unpaid leave when switched on)')
    prorate_fixed_days = models.PositiveSmallIntegerField(default=30, help_text='Days in a month for the fixed-days method')
    prorate_unpaid = models.BooleanField(default=False, help_text='Unpaid leave days in a month reduce what is earned')
    comp_full_day_hours = models.FloatField(null=True, blank=True, help_text='Compensatory off: hours worked on a weekend / holiday for 1 day')
    comp_half_day_hours = models.FloatField(null=True, blank=True, help_text='Compensatory off: hours for half a day')
    comp_expiry_days = models.PositiveIntegerField(null=True, blank=True, help_text='Compensatory off: days to use it before it lapses')
    # v1.12.0
    service_steps = models.JSONField(default=list, blank=True,
                                     help_text='Days by length of service: [{"from_year": 3, "days": 26, "carry_forward_max": null}, …]. '
                                               'Service year 1 (and any year before the first step) uses days_per_year.')
    worked_source = models.CharField(max_length=10, choices=WORKED_FROM, default='punch',
                                     help_text='Days worked prorate: where the days worked come from')
    worked_paid_leave = models.BooleanField(default=True, help_text='Days worked prorate: paid leave days count as days worked')

    class Meta:
        ordering = ['policy', 'id']
        unique_together = ('policy', 'leave_type_id')


class EmployeeLeavePolicy(models.Model):
    """A policy given to one employee, instead of the one of their category."""
    employee_id = models.IntegerField(unique=True)
    policy = models.ForeignKey(LeavePolicy, on_delete=models.CASCADE, related_name='employees')
    note = models.CharField(max_length=200, blank=True, default='')
    assigned_at = models.DateTimeField(auto_now_add=True)


class LeaveLedger(models.Model):
    """Every change to a leave balance, so a balance can be shown at any date and explained line by line."""
    KINDS = [('opening', 'Opening balance'), ('accrual', 'Earned'), ('taken', 'Taken'), ('cancelled', 'Cancelled leave returned'),
             ('encashed', 'Encashed'), ('carry_forward', 'Carried forward'), ('lapsed', 'Lapsed'), ('reset', 'Yearly reset'),
             ('adjustment', 'Adjustment')]
    employee_id = models.IntegerField(db_index=True)
    leave_type_id = models.IntegerField(db_index=True)
    date = models.DateField(db_index=True)
    kind = models.CharField(max_length=20, choices=KINDS)
    days = models.FloatField()
    ref_model = models.CharField(max_length=80, blank=True, default='')
    ref_id = models.IntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        indexes = [models.Index(fields=['employee_id', 'leave_type_id', 'date'])]


class OrgRole(models.Model):
    """v1.11.0 – who holds an approval role: HR manager of a branch, head of a department, company HR."""
    ROLES = [('branch_hr', 'Branch HR manager'), ('department_head', 'Department head'), ('company_hr', 'Company HR manager')]
    role = models.CharField(max_length=20, choices=ROLES)
    branch_id = models.IntegerField(null=True, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    user_id = models.IntegerField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('role', 'branch_id', 'department_id')


class LevelRole(models.Model):
    """v1.11.0 – an approval level answered by a role instead of a fixed user; escalation by role."""
    ROLES = [('user', 'Named user'), ('reporting_manager', 'Reporting manager'), ('manager_of_manager', "Reporting manager's manager"),
             ('branch_hr', 'Branch HR manager'), ('department_head', 'Department head'), ('company_hr', 'Company HR manager')]
    level_id = models.IntegerField(unique=True, help_text='calendars.LeaveApprovalLevels id')
    role = models.CharField(max_length=20, choices=ROLES, default='user')
    escalate_role = models.CharField(max_length=20, choices=ROLES, blank=True, default='')


class ApprovalClock(models.Model):
    """v1.11.0 – when a leave approval step started waiting (the approval itself only stores the date)."""
    approval_id = models.IntegerField(unique=True)
    started_at = models.DateTimeField()
    escalated_at = models.DateTimeField(null=True, blank=True)
