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
    COUNT = [('calendar', 'Calendar days'), ('working', 'Working days (no weekends or public holidays)'),
             ('cal_nohol', 'Calendar days except public holidays')]   # v1.65.0 (item 87)
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
             ('branch_hr', 'Branch HR manager'), ('department_head', 'Department head'), ('company_hr', 'Company HR manager'),
             ('functional_manager', 'Functional (dotted-line) manager')]   # v1.58.0
    level_id = models.IntegerField(unique=True, help_text='calendars.LeaveApprovalLevels id')
    role = models.CharField(max_length=20, choices=ROLES, default='user')
    escalate_role = models.CharField(max_length=20, choices=ROLES, blank=True, default='')


class ApprovalClock(models.Model):
    """v1.11.0 – when a leave approval step started waiting (the approval itself only stores the date)."""
    approval_id = models.IntegerField(unique=True)
    started_at = models.DateTimeField()
    escalated_at = models.DateTimeField(null=True, blank=True)


# ------------------------------------------------------------------ v1.51.0 leave conflicts
class ConflictSettings(models.Model):
    """When a leave request is shown with a warning: colleagues of the same role away, too few people left in the
    department, the public holiday next to it. One row per company."""
    same_role_max = models.PositiveSmallIntegerField(default=1, help_text='warn when this many others of the same designation in the department are away')
    dept_min_present_pct = models.PositiveSmallIntegerField(default=50, help_text='warn when fewer of the department are at work (%)')
    count_pending = models.BooleanField(default=True, help_text='requests waiting for approval count as away')
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class BlackoutPeriod(models.Model):
    """Days when leave is not taken (year-end closing, peak season, an audit): a warning or a block."""
    ACTIONS = [('warn', 'Warn'), ('block', 'Block (only HR can enter leave)')]
    name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    branch_ids = models.JSONField(default=list, blank=True, help_text='[] = every branch')
    department_ids = models.JSONField(default=list, blank=True, help_text='[] = every department')
    exempt_leave_type_ids = models.JSONField(default=list, blank=True, help_text='e.g. sick, emergency, bereavement')
    action = models.CharField(max_length=5, choices=ACTIONS, default='warn')
    note = models.CharField(max_length=255, blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_date']


# ------------------------------------------------------------------ v1.65.0 leave rules (items 82–97)
class LeaveTypeRule(models.Model):
    """v1.65.0 – rules of one leave type that the policy line does not hold (one row per leave type; a branch copy of the type
    uses the row of its type of the same code / name). Every rule is off by default = the behaviour before 1.65.0."""
    leave_type_id = models.IntegerField(unique=True)
    # item 82 – maternity / parental leave: the birth date on the request, taken within N months of the birth, once per birth
    birth_required = models.BooleanField(default=False)
    birth_window_months = models.PositiveSmallIntegerField(default=6)
    birth_window_from = models.CharField(max_length=10, default='birth', choices=[('birth', 'From the birth'), ('before', 'Up to 30 days before the birth')])
    one_per_birth = models.BooleanField(default=True)
    # item 86 – sandwich rule: weekends / public holidays between two leave days count as leave
    sandwich = models.BooleanField(default=False)
    # item 88 – the balance projected to the leave start (accrual up to then) is used for the balance check
    allow_projected = models.BooleanField(default=False)
    # item 85 – leave salary paid before the leave starts
    advance_salary = models.BooleanField(default=False)
    # item 92 – the supporting document only above N days (empty = as the policy line / always when required)
    document_above_days = models.FloatField(null=True, blank=True)
    # item 91 – HR confirms the employee's eligibility before the first request (e.g. Hajj); N years of service
    hr_eligibility = models.BooleanField(default=False)
    min_service_years = models.FloatField(null=True, blank=True)
    # item 94 – nursing breaks after this (maternity) leave
    nursing_breaks = models.BooleanField(default=False)
    # item 97 – not refused / warned during the notice period (e.g. sick leave)
    notice_exempt = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class LeaveRuleSettings(models.Model):
    """v1.65.0 – company leave rules (one row)."""
    NOTICE = [('allow', 'Allow (as before)'), ('warn', 'Warn'), ('block', 'Block (only HR enters leave)')]
    REDUCE = [('policy', 'As set on each policy line (as before)'), ('unpaid', 'Unpaid leave reduces annual leave earned'),
              ('unpaid_absence', 'Unpaid leave and absent days reduce annual leave earned')]
    EXIT6 = [('none', 'Nothing (as before)'), ('two_days', '2 days for each month of service'), ('pro_rata', 'Days a year x months / 12')]
    WHEN = [('next_payroll', 'In the next payroll before the leave'), ('off_cycle', 'At once in an off-cycle run')]
    BASIS = [('gross', 'Salary prorated by days (basic and allowances)'), ('basic', 'Basic salary only')]
    notice_leave = models.CharField(max_length=10, choices=NOTICE, default='allow')                 # item 97
    accrual_reduction = models.CharField(max_length=20, choices=REDUCE, default='policy')          # item 89
    exit_under_6m = models.CharField(max_length=10, choices=EXIT6, default='none')                  # item 90 (to be confirmed)
    advance_when = models.CharField(max_length=15, choices=WHEN, default='next_payroll')           # item 85
    advance_basis = models.CharField(max_length=10, choices=BASIS, default='gross')                 # item 85
    catch_up = models.BooleanField(default=True)                                                    # item 93
    catch_up_max_months = models.PositiveSmallIntegerField(default=12)
    nursing_enabled = models.BooleanField(default=True)                                             # item 94 (law)
    nursing_minutes = models.PositiveSmallIntegerField(default=60)
    nursing_months = models.PositiveSmallIntegerField(default=6)
    extension_skips_notice = models.BooleanField(default=True)                                      # item 84
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class LeaveRequestInfo(models.Model):
    """v1.65.0 – what a leave request carries besides the calendars request (no column added to the core table)."""
    request_id = models.IntegerField(unique=True)
    birth_date = models.DateField(null=True, blank=True)                     # item 82
    extension_of_id = models.IntegerField(null=True, blank=True, db_index=True)   # item 84
    original_end_date = models.DateField(null=True, blank=True)              # item 83 part cancel
    cancelled_days = models.FloatField(default=0)
    cancel_note = models.CharField(max_length=255, blank=True, default='')
    advance = models.JSONField(default=dict, blank=True)                      # item 85 {status, amount, lines, run}
    updated_at = models.DateTimeField(auto_now=True)


class LeaveEligibility(models.Model):
    """v1.65.0 (item 91) – HR's confirmation that an employee may take a leave type (e.g. Hajj, once in service). No religion or
    other protected attribute is stored: only "eligible – confirmed by HR" and "taken before (in this employment)"."""
    employee_id = models.IntegerField()
    leave_type_id = models.IntegerField()
    confirmed = models.BooleanField(default=False)
    taken_before = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True, default='')
    confirmed_by_id = models.IntegerField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('employee_id', 'leave_type_id')
