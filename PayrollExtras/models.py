"""v1.68.0 (pending items 29 – 40) – UAE payroll extras. New tables only; nothing in the core apps changes.

  CommissionPlan / CommissionEntry      commission and incentive plans: tiers on sales or target achievement → monthly input
  RetentionBonus                        retention / deferred bonus: paid on its date through payroll, clawed back at the final settlement
  ServiceChargePool                     service charge / tips pool of a month and branch, shared by points, days or equally → monthly input
  PayBasis                              hourly / daily paid employees: the rate the payroll formulas read (hourly_rate, daily_rate)
  ILOERecord                            unemployment insurance (ILOE) subscription of each employee: category, policy, expiry, status
  EmiratisationProfile / Settings       Nafis and Emiratisation: skill level, Nafis registration and salary support; the quota of the company
  BulkAdvance                           one-click Ramadan / Eid salary advance for many employees, recovered in instalments
"""
from django.db import models

MONEY = dict(max_digits=12, decimal_places=2)


class CommissionPlan(models.Model):
    BASIS = (('sales', 'On the sales amount entered'), ('target', 'On the % of the target reached'))
    MODE = (('slab', 'Each slab at its own rate (progressive)'), ('flat', 'The whole amount at the rate of the slab reached'),
            ('fixed', 'A fixed amount for the slab reached'))
    name = models.CharField(max_length=120, unique=True)
    basis = models.CharField(max_length=8, choices=BASIS, default='sales')
    mode = models.CharField(max_length=6, choices=MODE, default='slab')
    tiers = models.JSONField(default=list, help_text='[{"upto": 50000, "rate": 2}, {"upto": null, "rate": 3}] – rate in %, or "amount" for fixed')
    cap = models.DecimalField('at most a month', null=True, blank=True, **MONEY)
    component_code = models.CharField('paid through the monthly input', max_length=20, default='ZCOMM')
    active = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        permissions = [('manage_payrollextras', 'Can manage commission, retention bonus, service charge and bulk advances')]


class CommissionEntry(models.Model):
    STATUS = (('draft', 'Draft'), ('approved', 'Approved'), ('sent', 'Sent to payroll'))
    plan = models.ForeignKey(CommissionPlan, on_delete=models.PROTECT, related_name='entries')
    employee_id = models.IntegerField(db_index=True)
    month = models.DateField(help_text='first day of the payroll month')
    sales = models.DecimalField(default=0, **MONEY)
    target = models.DecimalField(null=True, blank=True, **MONEY)
    commission = models.DecimalField(default=0, **MONEY)
    breakdown = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='draft')
    note = models.CharField(max_length=255, blank=True, default='')
    approved_by_id = models.IntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'employee_id']
        unique_together = [('plan', 'employee_id', 'month')]


class RetentionBonus(models.Model):
    STATUS = (('awarded', 'Awarded'), ('sent', 'Sent to payroll'), ('paid', 'Paid'), ('clawed', 'Clawed back'), ('cancelled', 'Cancelled'))
    BASIS = (('full', 'The whole bonus'), ('pro_rata', 'Pro rata to the months not served'))
    employee_id = models.IntegerField(db_index=True)
    amount = models.DecimalField(**MONEY)
    award_date = models.DateField()
    pay_month = models.DateField(help_text='first day of the payroll month it is paid in')
    component_code = models.CharField(max_length=20, default='ZRETB')
    clawback_months = models.PositiveSmallIntegerField(default=12, help_text='repaid when the employee leaves within this many months of the payment')
    clawback_basis = models.CharField(max_length=8, choices=BASIS, default='pro_rata')
    status = models.CharField(max_length=10, choices=STATUS, default='awarded')
    reason = models.CharField(max_length=255, blank=True, default='')
    settlement_id = models.IntegerField(null=True, blank=True)
    clawed_amount = models.DecimalField(default=0, **MONEY)
    history = models.JSONField(default=list, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-award_date', '-id']


class ServiceChargePool(models.Model):
    METHOD = (('points', 'By points (designation / grade points)'), ('days', 'By days worked'), ('equal', 'Equally'))
    STATUS = (('draft', 'Draft'), ('sent', 'Sent to payroll'))
    month = models.DateField()
    branch_id = models.IntegerField()
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=8, choices=METHOD, default='points')
    component_code = models.CharField(max_length=20, default='ZSVC')
    points = models.JSONField(default=dict, blank=True, help_text='{"designation:<id>": 2, "employee:<id>": 1.5} – per employee wins')
    shares = models.JSONField(default=list, blank=True, help_text='[{employee_id, name, weight, amount}] – worked out on Preview')
    excluded = models.JSONField(default=list, blank=True, help_text='employee ids left out')
    status = models.CharField(max_length=6, choices=STATUS, default='draft')
    sent_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'branch_id']
        unique_together = [('month', 'branch_id')]


class PayBasis(models.Model):
    BASIS = (('monthly', 'Monthly salary'), ('hourly', 'Paid by the hour'), ('daily', 'Paid by the day'))
    employee_id = models.IntegerField(unique=True)
    basis = models.CharField(max_length=8, choices=BASIS, default='monthly')
    hourly_rate = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    daily_rate = models.DecimalField(default=0, max_digits=10, decimal_places=2)
    contract_hours_week = models.DecimalField(null=True, blank=True, max_digits=5, decimal_places=2, help_text='part time: contracted hours a week')
    updated_at = models.DateTimeField(auto_now=True)


class ILOERecord(models.Model):
    STATUS = (('subscribed', 'Subscribed'), ('not_subscribed', 'Not subscribed'), ('exempt', 'Exempt'))
    PAID_BY = (('employee', 'The employee'), ('employer', 'The company on the employee\'s behalf'))
    employee_id = models.IntegerField(unique=True)
    status = models.CharField(max_length=16, choices=STATUS, default='not_subscribed')
    exempt_reason = models.CharField(max_length=30, blank=True, default='', choices=(
        ('', '–'), ('investor', 'Investor (owner of the establishment)'), ('domestic', 'Domestic worker'), ('temporary', 'Temporary contract'),
        ('juvenile', 'Under 18'), ('retiree', 'Emirati retiree with a pension in a new job')))
    category = models.CharField(max_length=2, blank=True, default='', help_text='A: basic salary up to 16,000; B: above – worked out when empty')
    policy_no = models.CharField(max_length=60, blank=True, default='')
    start_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    paid_by = models.CharField(max_length=10, choices=PAID_BY, default='employee')
    payment = models.CharField(max_length=10, blank=True, default='', choices=(('', '–'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
                                                                              ('half', 'Every 6 months'), ('yearly', 'Yearly'), ('two_years', 'Two years')))
    note = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


class EmiratisationProfile(models.Model):
    SKILL = ((0, 'Not set'), (1, 'Skill level 1'), (2, 'Skill level 2'), (3, 'Skill level 3'), (4, 'Skill level 4'), (5, 'Skill level 5'))
    employee_id = models.IntegerField(unique=True)
    skill_level = models.PositiveSmallIntegerField(default=0, choices=SKILL, help_text='MOHRE skill level of the work permit (1 – 3 = skilled)')
    nafis_registered = models.BooleanField(default=False)
    nafis_no = models.CharField(max_length=40, blank=True, default='')
    salary_support = models.BooleanField('gets Nafis salary support', default=False)
    support_until = models.DateField(null=True, blank=True)
    counts_for_quota = models.BooleanField(default=True)
    note = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


class EmiratisationSettings(models.Model):
    """Company targets – entered by HR from MOHRE's notice (the rules change every year)."""
    target_pct = models.DecimalField('target: Emiratis as % of the skilled workforce', default=0, max_digits=5, decimal_places=2)
    period_label = models.CharField(max_length=40, blank=True, default='', help_text='e.g. first half 2026 (deadline 30 June)')
    deadline = models.DateField(null=True, blank=True)
    fine_per_head_month = models.DecimalField('contribution a month for each Emirati not hired', default=0, **MONEY)
    min_nationals = models.PositiveSmallIntegerField('companies of 20 – 49 employees: Emiratis required', default=0)
    skilled_levels = models.CharField(max_length=20, default='1,2,3', help_text='skill levels that count as skilled')
    note = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


class BulkAdvance(models.Model):
    MODE = (('fixed', 'The same amount for everyone'), ('percent', 'A % of the monthly basic salary'), ('gross_pct', 'A % of the monthly gross salary'))
    STATUS = (('draft', 'Draft'), ('created', 'Advances made'), ('cancelled', 'Cancelled'))
    name = models.CharField(max_length=120)
    mode = models.CharField(max_length=10, choices=MODE, default='percent')
    value = models.DecimalField(max_digits=12, decimal_places=2)
    instalments = models.PositiveSmallIntegerField(default=2)
    max_amount = models.DecimalField(null=True, blank=True, **MONEY)
    employee_ids = models.JSONField(default=list, blank=True)
    lines = models.JSONField(default=list, blank=True, help_text='[{employee_id, name, amount, advance_id, skipped}]')
    status = models.CharField(max_length=10, choices=STATUS, default='draft')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class GCCPensionRate(models.Model):
    """34 – GCC nationals working in the UAE: GPSSA collects the contributions of their home scheme (unified GCC extension of insurance
    protection) at the home country's rates and on its wage base. The rates are seeded switched off: HR checks them with GPSSA and switches
    a country on; the payroll formulas then get gcc_pension_emp / gcc_pension_er / gcc_pension_base for the employees of that nationality."""
    BASES = (('basic', 'Basic salary'), ('basic_housing', 'Basic + housing allowance'), ('gross', 'Gross salary (fixed additions)'))
    country = models.CharField(max_length=2, unique=True, help_text='SA, KW, OM, BH, QA')
    name = models.CharField(max_length=40)
    employee_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    employer_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    base = models.CharField(max_length=14, choices=BASES, default='gross')
    max_base_aed = models.DecimalField('wage ceiling (AED a month)', null=True, blank=True, max_digits=12, decimal_places=2)
    active = models.BooleanField(default=False)
    confirmed = models.BooleanField('rates checked with GPSSA', default=False)
    source = models.CharField(max_length=300, blank=True, default='')
    effective_from = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['country']


class PayrollCategory(models.Model):
    """v1.73.7 – Payroll › Setup › Payroll categories. The 21 system categories (their keys are fixed – the calculations read
    them) and the company's own. A salary item's payroll category (SalaryComponent.payroll_category, unchanged) holds the key.
    The ticks say what an item of the category counts towards; their defaults are exactly the rules ZEO had in code
    (PayrollExtras/categories.py SYSTEM), so nothing changes until someone changes a tick."""
    KINDS = [('addition', 'Addition'), ('deduction', 'Deduction'), ('employer', 'Employer contribution')]
    key = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=80)
    name_ar = models.CharField(max_length=80, blank=True, default='')
    header = models.CharField(max_length=80, blank=True, default='', help_text='column title in the reports (empty = the name)')
    header_ar = models.CharField(max_length=80, blank=True, default='')
    order = models.PositiveIntegerField(default=100)
    kind = models.CharField(max_length=10, choices=KINDS, default='addition')
    system = models.BooleanField(default=False)
    like = models.CharField(max_length=50, blank=True, default='', help_text='a company category behaves like this system category')
    gratuity = models.BooleanField(default=False, help_text='counts for gratuity / end of service')
    pension = models.BooleanField(default=False, help_text='counts for the pension contribution salary (GPSSA, GCC schemes)')
    leave_salary = models.BooleanField(default=False, help_text='counts for leave salary and the leave liability')
    encashment = models.BooleanField(default=False, help_text='counts for leave encashment')
    wps_variable = models.BooleanField(default=False, help_text='paid as variable pay in the WPS salary file (else fixed)')
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['order', 'name']
        permissions = [('manage_payrollcategory', 'Set up payroll categories')]

    def __str__(self):
        return self.name


class PaidPeriod(models.Model):
    """v1.73.7 – the days each payslip pays (the attendance window the payroll used for the employee). A later run never pays
    these days again: its window starts the day after (PayrollExtras/paid.py)."""
    employee_id = models.IntegerField(db_index=True)
    payslip_id = models.IntegerField(unique=True)
    run_id = models.IntegerField(db_index=True)
    start = models.DateField()
    end = models.DateField()
    rule_id = models.IntegerField(null=True, blank=True, help_text='the payroll rule (pay structure) used')
    created_at = models.DateTimeField(auto_now_add=True)


class PayrollSkip(models.Model):
    """v1.73.7 – an employee left out of a payroll run, and why (every day of the run's window was paid already)."""
    run_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField()
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('run_id', 'employee_id')


class TemplateDefault(models.Model):
    """v1.73.7 – a salary template's default for one item: a fixed amount, or a percentage of the employee's Basic (or of another
    payroll category). It only fills an employee's empty (0) amount when they get the template – it never overwrites one."""
    structure_id = models.IntegerField(db_index=True, help_text='PayrollManagement.SalaryStructure')
    component_id = models.IntegerField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    percent = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    of_category = models.CharField(max_length=50, default='basic')

    class Meta:
        unique_together = ('structure_id', 'component_id')
