"""
v1.18.0 – payroll controls, leave-encashment approval steps and the final settlement (hospital workflow test).

* PayrollPlusSettings   one row: approval chain for leave encashment, segregation of duties, WPS / settlement deadlines
* RunControl            who prepared / approved / paid a payroll run (the preparer may not approve or release it)
* EncashmentStep        HR confirms the balance → Finance verifies the amount → management approves (then paid by payroll)
* ExitSettlement        one consolidated final settlement per end of service: every component calculated on its own,
                        HR calculates → Finance validates → management approves → Finance pays (due 14 days after the
                        last working day)
"""
from django.db import models


class PayrollPlusSettings(models.Model):
    encashment_chain = models.BooleanField(default=False, help_text='Leave encashment: HR confirms → Finance verifies → management approves')
    segregation = models.BooleanField(default=True, help_text='Whoever prepares a payroll run may not approve or release it')
    wps_late_days = models.PositiveSmallIntegerField(default=15, help_text='Flag a salary paid more than this many days after the month it is for')
    settlement_due_days = models.PositiveSmallIntegerField(default=14, help_text='Final settlement due this many days after the last working day')
    # v1.20.2: how a joiner / leaver is paid for a part month when salaries are paid on fixed days (e.g. 30)
    PART_MONTH = [('share', 'Share of the calendar month (days not employed × fixed days ÷ days in the month)'),
                  ('days', 'Days employed, at most the fixed days (a full month always pays the fixed days)')]
    part_month = models.CharField(max_length=8, choices=PART_MONTH, default='share')
    # v1.20.2: pay start / end dates in the WPS file (EDR) for a run with a cut-off (e.g. 26th – 25th)
    WPS_PERIOD = [('month', 'The salary month (1st – last day)'), ('attendance', "The run's attendance cycle (e.g. 26th – 25th)")]
    wps_period = models.CharField(max_length=12, choices=WPS_PERIOD, default='month')
    # v1.50.0: language of the payslip PDF
    PAYSLIP_LANG = [('reader', "The reader's language (English or the language chosen in ZEO)"), ('en', 'English'),
                    ('en_ar', 'English and Arabic on the same payslip')]
    payslip_language = models.CharField(max_length=8, choices=PAYSLIP_LANG, default='reader')
    # v1.55.0: pay frequencies other than monthly must be switched on here before they are offered anywhere
    pay_weekly = models.BooleanField(default=False, help_text='Offer weekly pay calendars')
    pay_biweekly = models.BooleanField(default=False, help_text='Offer bi-weekly pay calendars')
    pay_semimonthly = models.BooleanField(default=False, help_text='Offer semi-monthly pay calendars')
    # v1.56.0: mid-year go-live – months up to this date were paid outside ZEO; payroll runs for them leave everybody out
    paid_up_to = models.DateField(null=True, blank=True, help_text='Salary paid outside ZEO up to this date (go-live)')
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()

    def pay_frequencies(self):
        """v1.55.0 – the frequencies switched on in Payroll controls (weekly / biweekly / semimonthly)."""
        return [k for k, f in (('weekly', 'pay_weekly'), ('biweekly', 'pay_biweekly'), ('semimonthly', 'pay_semimonthly')) if getattr(self, f)]


class BranchWPS(models.Model):
    """v1.20.2 – a branch that is its own MoHRE establishment (own establishment card and WPS file): its employer ID and the
    routing code of its salary bank. Branches without a row use the company's (Settings → Company)."""
    branch_id = models.IntegerField(unique=True)
    employer_unique_id = models.CharField(max_length=13, blank=True, default='')
    bank_routing_code = models.CharField(max_length=9, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


class RunControl(models.Model):
    run_id = models.IntegerField(unique=True)
    prepared_by_id = models.IntegerField(null=True, blank=True)
    prepared_at = models.DateTimeField(auto_now_add=True)
    approved_by_id = models.IntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by_id = models.IntegerField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_ref = models.CharField(max_length=60, blank=True, default='')


class EncashmentStep(models.Model):
    STEPS = [('hr', 'HR confirmed the balance'), ('finance', 'Finance verified the amount'), ('management', 'Approved by management')]
    encashment_id = models.IntegerField(db_index=True)
    step = models.CharField(max_length=12, choices=STEPS)
    user_id = models.IntegerField()
    user_name = models.CharField(max_length=150, blank=True, default='')
    at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        unique_together = [('encashment_id', 'step')]
        ordering = ['encashment_id', 'at']


class ExitSettlement(models.Model):
    STATUS = [('draft', 'Draft – HR calculating'), ('submitted', 'Waiting for Finance'), ('validated', 'Waiting for management approval'),
              ('approved', 'Approved – to be paid'), ('paid', 'Paid'), ('cancelled', 'Cancelled')]
    number = models.CharField(max_length=30, blank=True, default='', db_index=True, help_text='v1.22.1: document number (Settings › Document numbering)')
    eos_id = models.IntegerField(unique=True)
    employee_id = models.IntegerField(db_index=True)
    calc_date = models.DateField()
    joined = models.DateField(null=True, blank=True)
    last_working_date = models.DateField()
    exit_type = models.CharField(max_length=30, blank=True, default='')
    service_days = models.IntegerField(default=0)
    unpaid_days = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    basic = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    wage = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='monthly basic + fixed allowances')
    salary_from = models.DateField(null=True, blank=True)
    salary_to = models.DateField(null=True, blank=True)
    notice_waived = models.BooleanField(default=False)
    lines = models.JSONField(default=list, blank=True, help_text='[{key, label, kind: earning|deduction, amount, basis}]')
    total_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=STATUS, default='draft')
    due_date = models.DateField(null=True, blank=True)
    submitted_by_id = models.IntegerField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    validated_by_id = models.IntegerField(null=True, blank=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    approved_by_id = models.IntegerField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_by_id = models.IntegerField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    payment_ref = models.CharField(max_length=60, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # v1.62.0 EOS – the Leavers workspace: exit reason beyond the resignation types, HR's own lines, asset decisions, history
    exit_reason = models.CharField(max_length=30, blank=True, default='', help_text='v1.62.0: end_of_contract … (the resignation type stays)')
    other_lines = models.JSONField(default=list, blank=True, help_text='v1.62.0: [{id, kind, label, amount, note, by_id, by, at}] – kept on recalculation')
    asset_decisions = models.JSONField(default=dict, blank=True, help_text='v1.62.0: {asset key: {decision: returned|recover|waive, amount, note, by, at}}')
    history = models.JSONField(default=list, blank=True, help_text='v1.62.0: [{action, by, at, note}]')
    last_action_at = models.DateTimeField(null=True, blank=True, help_text='v1.62.0: time of the latest step (the history shows it)')
    # v1.62.11 (EOS settings): HR's entries on the draft (gratuity adjustment, leave balance / unpaid days overrides, contract leave days,
    # unpaid salary from, air ticket entered by HR) and the rules the last calculation used – PayrollPlus/eos_settings.py
    inputs = models.JSONField(default=dict, blank=True, help_text='v1.62.11: HR entries on the draft + the rules of the last calculation')
    accounting_date = models.DateField(null=True, blank=True, help_text='v1.62.11: date of the journal (empty = paid on, else the last working day)')

    class Meta:
        ordering = ['-last_working_date', '-id']
        permissions = [('validate_exitsettlement', 'Can validate final settlements (Finance)'),
                       ('approve_exitsettlement', 'Can approve final settlements (management)'),
                       ('pay_exitsettlement', 'Can mark final settlements as paid (Finance)'),
                       ('finance_check_leaveencashment', 'Can verify leave encashment amounts (Finance)'),
                       ('approve_leaveencashment_final', 'Can give the final approval of leave encashments (management)')]


# ------------------------------------------------------------------ v1.31.0 statutory payroll schemes per country
class StatutoryScheme(models.Model):
    """A statutory contribution / deduction of the company country (GPSSA, GOSI, GRSIA, PASI, SIO, PIFSS, EPF, ESI, professional
    tax, income tax …). Seeded by "Apply country defaults" (Settings → Country settings) from CountryPack.statutory, editable here.
    Payroll formulas get, per employee: stat_<code>_base, stat_<code>_emp, stat_<code>_er and the totals
    statutory_employee_total / statutory_employer_total; the salary components made by "Apply" use them."""
    KINDS = [('percent', 'Percentage of a wage base'), ('fixed', 'Fixed amount per month'), ('slab', 'Monthly slab on the base'),
             ('annual_slab', 'Annual income-tax slabs (monthly = yearly tax ÷ 12)')]
    APPLIES = [('all', 'Every employee'), ('nationals', 'Nationals only'), ('expats', 'Expatriates only')]
    BASES = [('basic', 'Basic salary'), ('basic_housing_transport', 'Basic + housing + transport'), ('basic_da', 'Basic + dearness allowance'),
             ('gross', 'Gross salary (all additions)')]
    code = models.CharField(max_length=20, help_text='Formula name: stat_<code>_emp / _er (letters, digits, _)')
    name = models.CharField(max_length=120)
    country = models.CharField(max_length=3, blank=True, default='', help_text='ISO-2 of the pack it came from')
    authority = models.CharField(max_length=80, blank=True, default='')
    kind = models.CharField(max_length=12, choices=KINDS, default='percent')
    applies_to = models.CharField(max_length=10, choices=APPLIES, default='all')
    base = models.CharField(max_length=24, choices=BASES, default='basic')
    min_base = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    max_base = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text='Wage ceiling')
    eligible_gross_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text='Only when the gross is at most this (ESI)')
    employee_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0, help_text='Percent, e.g. 11 = 11 %')
    employer_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    employee_fixed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_fixed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    slabs = models.JSONField(default=list, blank=True, help_text='[{"to": 7500, "employee": 0, "employer": 0, "rate": 5}] – amounts (slab) or percent rates (annual_slab)')
    standard_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='annual_slab: deducted from yearly income first')
    rebate_income_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='annual_slab: no tax when taxable income is at most this')
    cess_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0, help_text='annual_slab: percent added to the tax (India: 4 % cess)')
    employee_component = models.CharField(max_length=20, blank=True, default='', help_text='Salary component code of the employee deduction')
    employer_component = models.CharField(max_length=20, blank=True, default='', help_text='Salary component code of the employer contribution (hidden from the payslip)')
    active = models.BooleanField(default=True)
    effective_from = models.DateField(null=True, blank=True)
    law_reference = models.CharField(max_length=400, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    is_standard = models.BooleanField(default=False, help_text='Seeded from the country pack')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['country', 'code']
        unique_together = [('code',)]

    def __str__(self):
        return f'{self.name} ({self.code})'


# ------------------------------------------------------------------ v1.59.0 date-versioned legal rules
RATE_FIELDS = ('min_base', 'max_base', 'eligible_gross_max', 'employee_rate', 'employer_rate', 'employee_fixed', 'employer_fixed', 'slabs',
               'standard_deduction', 'rebate_income_limit', 'cess_rate')


class StatutorySchemeVersion(models.Model):
    """New rates of a statutory scheme from a date. The payroll of a month uses the newest version with effective_from ≤ the
    first day of the pay period, else the rates on the scheme itself – so a re-run of a past month keeps that month's rates."""
    scheme = models.ForeignKey(StatutoryScheme, on_delete=models.CASCADE, related_name='versions')
    effective_from = models.DateField()
    min_base = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    max_base = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    eligible_gross_max = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    employee_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    employer_rate = models.DecimalField(max_digits=7, decimal_places=4, default=0)
    employee_fixed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    employer_fixed = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    slabs = models.JSONField(default=list, blank=True)
    standard_deduction = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    rebate_income_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cess_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    law_reference = models.CharField(max_length=400, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheme', '-effective_from']
        unique_together = [('scheme', 'effective_from')]


class LegalRate(models.Model):
    """A legal figure the code uses (e.g. the GPSSA employee rate) from a date. Without a row the built-in value applies."""
    key = models.CharField(max_length=60)
    country = models.CharField(max_length=2, blank=True, default='')
    value = models.DecimalField(max_digits=14, decimal_places=4)
    effective_from = models.DateField()
    law_reference = models.CharField(max_length=400, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['key', '-effective_from']
        unique_together = [('key', 'effective_from')]


# ------------------------------------------------------------------ v1.49.0 monthly provisions and loan changes
class ProvisionRun(models.Model):
    """Month-end liability of every employee – gratuity (end of service), leave salary and air tickets – and the change on
    the previous month (the provision to book). Made on the 1st for the month before; posted to accounting when connected."""
    STATUS = [('draft', 'Draft'), ('final', 'Final'), ('posted', 'Posted to accounting')]
    month = models.DateField(unique=True, help_text='last day of the month')
    status = models.CharField(max_length=8, choices=STATUS, default='draft')
    employees = models.PositiveIntegerField(default=0)
    totals = models.JSONField(default=dict, blank=True)
    made_by_id = models.IntegerField(null=True, blank=True)
    made_at = models.DateTimeField(auto_now=True)
    posted_ref = models.CharField(max_length=60, blank=True, default='')

    class Meta:
        ordering = ['-month']


class ProvisionLine(models.Model):
    KINDS = [('gratuity', 'Gratuity (end of service)'), ('leave', 'Leave salary'), ('air_ticket', 'Air tickets')]
    run = models.ForeignKey(ProvisionRun, on_delete=models.CASCADE, related_name='lines')
    employee_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    opening = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    closing = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    movement = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    basis = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        unique_together = [('run', 'employee_id', 'kind')]


class LoanEvent(models.Model):
    """A change to an approved loan outside payroll: top-up, early settlement, new instalment, pause / resume."""
    KINDS = [('topup', 'Top-up'), ('settle', 'Early settlement'), ('reschedule', 'New instalment'), ('pause', 'Paused'), ('resume', 'Resumed'),
             ('exit', 'Settled at exit')]
    loan_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    old_emi = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    new_emi = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    old_period = models.PositiveIntegerField(null=True, blank=True)
    new_period = models.PositiveIntegerField(null=True, blank=True)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')
    user_id = models.IntegerField(null=True, blank=True)
    user_name = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']


# ------------------------------------------------------------------ v1.50.0 payroll what-if and cost allocation
class WhatIfScenario(models.Model):
    """A trial payroll with proposed salary changes, compared with the payroll as it is. Nothing is saved to payroll until
    the scenario is applied (the amounts are then written to the employees' salary, with the revision history)."""
    STATUS = [('draft', 'Draft'), ('applied', 'Applied')]
    name = models.CharField(max_length=120)
    month = models.DateField(help_text='first day of the month the trial payroll is calculated for')
    rules = models.JSONField(default=list, blank=True)
    employee_ids = models.JSONField(default=list, blank=True, help_text='empty = every active employee the user may see')
    result = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=8, choices=STATUS, default='draft')
    effective_from = models.DateField(null=True, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by_id = models.IntegerField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    calculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-id']


class CostAllocation(models.Model):
    """How an employee's cost is split: fixed percentages over projects / cost centres / departments, or the timesheet hours
    of the month (the rest goes to the employee's own cost centre or department)."""
    BASIS = [('percent', 'Fixed percentages'), ('timesheet', 'Timesheet hours of the month')]
    employee_id = models.IntegerField(db_index=True)
    basis = models.CharField(max_length=10, choices=BASIS, default='percent')
    lines = models.JSONField(default=list, blank=True, help_text='[{kind: project|cost_center|department, id, percent}]')
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True, default='')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee_id', '-valid_from']


# ------------------------------------------------------------------ v1.55.0 pay calendars, off-cycle runs, arrears
class PayCalendar(models.Model):
    """A pay frequency other than monthly for some employees (weekly, every two weeks, twice a month)."""
    FREQ = (('weekly', 'Weekly'), ('biweekly', 'Bi-weekly'), ('semimonthly', 'Semi-monthly (1–15, 16–end)'))
    name = models.CharField(max_length=80)
    frequency = models.CharField(max_length=12, choices=FREQ)
    first_start = models.DateField(help_text='weekly / every two weeks: the first day of the first period (e.g. a Saturday)')
    pay_days_after = models.PositiveSmallIntegerField(default=3, help_text='pay date = period end + these days')
    employee_ids = models.JSONField(default=list, blank=True, help_text='employees paid on this calendar')
    category_ids = models.JSONField(default=list, blank=True, help_text='or every employee of these categories')
    active = models.BooleanField(default=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']


class CalendarRun(models.Model):
    """The payroll run of one period of a pay calendar (a normal PayrollRun with the period's dates)."""
    calendar = models.ForeignKey(PayCalendar, on_delete=models.CASCADE, related_name='runs')
    period_start = models.DateField()
    period_end = models.DateField()
    pay_date = models.DateField(null=True, blank=True)
    payroll_run_id = models.IntegerField(unique=True)
    closes_month = models.BooleanField(default=False, help_text='the last period ending in its month: loans and advances are deducted here')
    factor = models.DecimalField(max_digits=8, decimal_places=6, default=1)
    scaled = models.JSONField(default=list, blank=True, help_text='payslip ids already prorated to the period')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-period_start']
        unique_together = [('calendar', 'period_start')]


class OffCycleRun(models.Model):
    """A payroll run outside the schedule: bonus, commission, correction, final settlement, advance …"""
    KINDS = (('bonus', 'Bonus'), ('commission', 'Commission'), ('correction', 'Correction'), ('final', 'Final settlement'),
             ('advance', 'Salary advance'), ('arrears', 'Arrears'), ('other', 'Other'))
    kind = models.CharField(max_length=12, choices=KINDS)
    title = models.CharField(max_length=120)
    pay_date = models.DateField()
    payroll_run_id = models.IntegerField(unique=True)
    lines = models.JSONField(default=list, blank=True, help_text='[{employee_id, component_id, amount, note}]')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class ArrearLine(models.Model):
    """The difference of one component in one paid payslip after a salary change with an earlier effective date."""
    STATES = (('pending', 'Waiting for the next payroll'), ('included', 'Paid'), ('cancelled', 'Cancelled'))
    employee_id = models.IntegerField(db_index=True)
    component_id = models.IntegerField()
    month = models.DateField(help_text='first day of the month (or the period start) of the payslip it corrects')
    source_payslip_id = models.IntegerField(null=True, blank=True)
    old_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    new_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    paid_ratio = models.DecimalField(max_digits=8, decimal_places=6, default=1)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATES, default='pending', db_index=True)
    included_payslip_id = models.IntegerField(null=True, blank=True)
    effective_from = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee_id', 'month', 'component_id']


class GoLiveEmployee(models.Model):
    """v1.56.0 – mid-year go-live data of one employee: salary paid up to (when it differs from the company's) and the date
    gratuity service is counted from (when not the joining date). The unpaid days before go-live are kept where they were
    since 1.20.2: EmployeeProfile.EmploymentInfo.unpaid_days_before_golive."""
    employee_id = models.IntegerField(unique=True)
    paid_up_to = models.DateField(null=True, blank=True)
    gratuity_from = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)


# ------------------------------------------------------------------ v1.62.7 (item 18) accrue now – pay later
# v1.62.7 (item 18): own tables in PayrollPlus (its own migrations) instead of new columns on the core SalaryComponent / Payslip – a
# per-employee balance needs a ledger that reports and the final settlement can add up; JSON in CompanyOption could not be queried.
class AccrualSetting(models.Model):
    """v1.62.7 (item 18) – a salary component that is accrued each month (type Others, not in the month's net pay) and paid later:
    in the payout month's payroll run and / or with the final settlement. One row per component (all its branch copies – same code –
    get the same setting)."""
    component_id = models.IntegerField(unique=True)
    enabled = models.BooleanField(default=True)
    payout_month = models.PositiveSmallIntegerField(null=True, blank=True, help_text='1–12: the month whose payroll run pays the balance; empty = never by payroll')
    include_payout_month = models.BooleanField(default=True, help_text="the payout month's own amount is paid in the same run")
    pay_at_exit = models.BooleanField(default=True, help_text='the balance is paid with the final settlement')
    accrual_account = models.CharField(max_length=40, blank=True, default='224005', help_text='accrued liability (GL)')
    payout_component_id = models.IntegerField(null=True, blank=True, help_text='the addition component of the payout line (made by ZEO)')
    previous_type = models.CharField(max_length=20, blank=True, default='', help_text='the component type before it was accrued (given back when switched off)')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class AccrualEntry(models.Model):
    """v1.62.7 (item 18) – one movement of an employee's accrued balance: opening balance (go-live), the month's accrual (a payslip),
    a payout (payroll run of the payout month) or the payout at exit (final settlement). Paid amounts are negative. The rows of a
    payslip go when the payslip is deleted (run deleted / recalculated); those of a rejected payslip do not count."""
    KINDS = [('opening', 'Opening balance'), ('accrual', 'Accrued'), ('payout', 'Paid by payroll'), ('exit', 'Paid at final settlement'),
             ('adjust', 'Adjustment')]
    employee_id = models.IntegerField(db_index=True)
    component_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=10, choices=KINDS)
    month = models.DateField(help_text='first day of the salary month (the payroll run’s month); the date of an opening balance')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payslip_id = models.IntegerField(null=True, blank=True, db_index=True)
    run_id = models.IntegerField(null=True, blank=True, db_index=True)
    settlement_id = models.IntegerField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['month', 'id']


# ------------------------------------------------------------------ v1.62.11 (items 228, 231) payroll controls (group payctl)
# v1.62.11 (items 228, 231): own tables in PayrollPlus (migration 0016) – no new column in the core apps.
class SalaryHold(models.Model):
    """v1.62.11 (item 228) – the salary of a month held in full or in part. The payslip keeps the salary earned (net pay unchanged);
    the held part is not paid in that month (WPS file / bank list: payctl.payable_amount) and is paid as the line "Held salary
    released" in a later payroll run after it is released."""
    KINDS = [('full', 'Whole salary'), ('amount', 'An amount'), ('percent', 'A percentage')]
    STATUS = [('pending', 'Waiting for the payroll run'), ('held', 'Held'), ('to_release', 'Release in the next payroll run'),
              ('released', 'Released'), ('cancelled', 'Cancelled')]
    employee_id = models.IntegerField(db_index=True)
    month = models.DateField(help_text='first day of the salary month that is held')
    kind = models.CharField(max_length=8, choices=KINDS, default='full')
    value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='amount (AED) or percent of the net pay')
    reason = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUS, default='pending', db_index=True)
    payslip_id = models.IntegerField(null=True, blank=True, db_index=True)
    held_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    release_from = models.DateField(null=True, blank=True, help_text='first day of the month whose run pays the release (empty = the next run)')
    release_payslip_id = models.IntegerField(null=True, blank=True, db_index=True)
    released_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    history = models.JSONField(default=list, blank=True, help_text='[{action, by, by_id, at, note}]')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'employee_id', 'id']


class PeriodLock(models.Model):
    """v1.62.11 (item 231) – a closed payroll period that nothing may change (payslips, attendance, leave, salary changes)."""
    STATUS = [('locked', 'Locked'), ('unlocked', 'Unlocked')]
    month = models.DateField(help_text='first day of the salary month')
    branch_id = models.IntegerField(null=True, blank=True, help_text='empty = every branch')
    start = models.DateField(help_text='first day covered (the attendance window of the month’s runs)')
    end = models.DateField()
    status = models.CharField(max_length=10, choices=STATUS, default='locked', db_index=True)
    locked_by_id = models.IntegerField(null=True, blank=True)
    locked_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-month', 'branch_id']
        permissions = (('unlock_periodlock', 'Can unlock a locked payroll period'),)


class PeriodLockEvent(models.Model):
    """v1.62.11 (item 231) – the log of a period lock: locked / unlocked (with the reason) / locked again."""
    lock = models.ForeignKey(PeriodLock, on_delete=models.CASCADE, related_name='events')
    action = models.CharField(max_length=10)
    reason = models.CharField(max_length=255, blank=True, default='')
    user_id = models.IntegerField(null=True, blank=True)
    user_name = models.CharField(max_length=150, blank=True, default='')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']


class SalaryRevisionRequest(models.Model):
    """v1.62.11 (item 228) – Revise salary: new amounts from an effective date, approved through the general request workflow of the
    request type "Salary revision" (Requests › Approval levels); applied when the request is approved."""
    STATUS = [('pending', 'Waiting for approval'), ('applied', 'Approved and applied'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'),
              ('failed', 'Approved – not applied')]
    employee_id = models.IntegerField(db_index=True)
    effective_from = models.DateField()
    lines = models.JSONField(default=list, blank=True, help_text='[{component_id, code, name, old, new}]')
    reason = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS, default='pending', db_index=True)
    general_request_id = models.IntegerField(null=True, blank=True, db_index=True)
    arrears = models.JSONField(default=dict, blank=True, help_text='the arrear lines made for past months {lines, total}')
    result = models.CharField(max_length=255, blank=True, default='')
    history = models.JSONField(default=list, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-id']


class ServicePeriod(models.Model):
    """v1.62.11 (item 228) – the employments of an employee: a re-hired leaver gets a new period, the old one is closed (its final
    settlement stays)."""
    employee_id = models.IntegerField(db_index=True)
    start = models.DateField()
    end = models.DateField(null=True, blank=True)
    settlement_id = models.IntegerField(null=True, blank=True)
    gratuity_continuity = models.BooleanField(default=False, help_text='gratuity service counted from the first period')
    note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee_id', 'start']


# ------------------------------------------------------------------ v1.63.0 (items 27, 28) payroll group "pay"
# v1.63.0 (items 27, 28): own tables in PayrollPlus (migration 0018) – no new column in the core apps.
class PaySplit(models.Model):
    """v1.63.0 (item 27) – one salary paid through two methods: the main account (the employee's payment mode) plus a second part –
    a second bank account (bank transfer outside WPS), cash, cheque or a company own method – by a fixed amount or a % of the net pay.
    Dated: the row with the latest `effective_from` on or before the end of the salary month counts; method 'none' ends the split
    from its date. Older rows are the history."""
    BASIS = [('amount', 'Fixed amount'), ('percent', 'Percentage of the net pay')]
    STATUS = [('active', 'Active'), ('cancelled', 'Cancelled')]
    employee_id = models.IntegerField(db_index=True)
    effective_from = models.DateField()
    method = models.CharField(max_length=20, help_text="bank_transfer / cash / cheque / a company method code / 'none' (split ended)")
    basis = models.CharField(max_length=8, choices=BASIS, default='percent')
    value = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='amount (AED) or % of the net pay paid by the second method')
    bank_name = models.CharField(max_length=120, blank=True, default='')
    iban = models.CharField(max_length=34, blank=True, default='')
    account_number = models.CharField(max_length=50, blank=True, default='')
    routing_code = models.CharField(max_length=9, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS, default='active', db_index=True)
    history = models.JSONField(default=list, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee_id', '-effective_from', '-id']


class Recovery(models.Model):
    """v1.63.0 (item 28) – a recovery schedule of any deduction (accommodation, transport, uniform, damage …): a total recovered by
    monthly instalments from a start month – over N months or an amount per month – on the payslip line of a deduction component,
    within its own monthly cap and the company's net-pay controls; what is not taken carries to the next month."""
    MODES = [('months', 'Number of months'), ('amount', 'Amount per month')]
    STATUS = [('pending', 'Waiting for approval'), ('active', 'Active'), ('paused', 'Paused'), ('completed', 'Recovered in full'),
              ('settled', 'Settled early'), ('written_off', 'Written off'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')]
    EXIT = [('deduct', 'Deduct the balance in the final settlement'), ('write_off', 'Write off the balance at exit')]
    number = models.CharField(max_length=30, blank=True, default='', db_index=True)
    employee_id = models.IntegerField(db_index=True)
    component_id = models.IntegerField(help_text='PayrollManagement.SalaryComponent (a deduction)')
    total = models.DecimalField(max_digits=12, decimal_places=2)
    start_month = models.DateField(help_text='first day of the first salary month')
    mode = models.CharField(max_length=8, choices=MODES, default='months')
    months = models.PositiveIntegerField(null=True, blank=True)
    per_month = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='the planned instalment')
    monthly_cap = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='never more than this in one month')
    reason = models.CharField(max_length=255, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUS, default='pending', db_index=True)
    exit_action = models.CharField(max_length=10, choices=EXIT, default='deduct')
    settle_next = models.BooleanField(default=False, help_text='settle early: the next payroll takes the whole balance')
    general_request_id = models.IntegerField(null=True, blank=True, db_index=True)
    history = models.JSONField(default=list, blank=True, help_text='[{action, by, by_id, at, note}]')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']


class RecoveryEntry(models.Model):
    """v1.63.0 (item 28) – a movement of a recovery: an instalment taken by a payslip (due = planned + carried in; amount = taken;
    the rest carries to the next month), a settlement paid early, a write-off, the balance taken by the final settlement. An entry of a
    rejected / deleted payslip is given back (status given_back – not counted)."""
    KINDS = [('payroll', 'Deducted by payroll'), ('settled', 'Settled early'), ('write_off', 'Written off'), ('exit', 'Final settlement'),
             ('exit_write_off', 'Written off at exit')]
    recovery = models.ForeignKey(Recovery, on_delete=models.CASCADE, related_name='entries')
    kind = models.CharField(max_length=14, choices=KINDS, default='payroll')
    month = models.DateField()
    due = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payslip_id = models.IntegerField(null=True, blank=True, db_index=True)
    line_id = models.IntegerField(null=True, blank=True, help_text='PayslipComponent of the instalment')
    settlement_id = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, default='taken', db_index=True, help_text='taken / given_back')
    note = models.CharField(max_length=255, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['month', 'id']
