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
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


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
