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
