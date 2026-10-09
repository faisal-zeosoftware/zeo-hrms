"""
Expense management (Zoho Expense style): categories, policies with limits, cost centres, trips, advances,
expenses (with receipts, mileage and per diem), expense reports with role-based approval, reimbursement
(direct or with the payroll) and an accounting export.

Employees, branches, departments, users, projects and payroll runs are stored as integer ids (no foreign keys
into the existing apps), so this app installs with its own migration and changes no table of the other apps.
"""
from decimal import Decimal

from django.db import models

ZERO = Decimal('0.00')


class ExpenseCategory(models.Model):
    KINDS = [('general', 'General'), ('mileage', 'Mileage'), ('per_diem', 'Per diem'), ('travel', 'Travel'),
             ('hotel', 'Hotel'), ('meals', 'Meals')]
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=20, choices=KINDS, default='general')
    gl_account = models.CharField(max_length=50, blank=True, default='', help_text='Expense account code in the books')
    receipt_required_above = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                                 help_text='A receipt is needed when the amount (AED) is above this; empty = never')
    description_required = models.BooleanField(default=False)
    description = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'expense categories'

    def __str__(self):
        return self.name


class ExpensePolicy(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    category_ids = models.JSONField(default=list, blank=True, help_text='Employee categories (empty = all)')
    branch_ids = models.JSONField(default=list, blank=True, help_text='Branches (empty = all)')
    grade_ids = models.JSONField(default=list, blank=True, help_text='Grades = designations (empty = all)')
    mileage_rate = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal('0.500'), help_text='Per km')
    per_diem_rate = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO, help_text='Per day')
    currency = models.CharField(max_length=3, default='AED')
    approval_roles = models.JSONField(default=list, blank=True,
                                      help_text="Approval levels by role, e.g. ['reporting_manager', 'branch_hr']")
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', 'name']
        verbose_name_plural = 'expense policies'

    def __str__(self):
        return self.name

    def roles(self):
        return list(self.approval_roles or []) or ['reporting_manager', 'branch_hr']


class ExpensePolicyLimit(models.Model):
    ACTIONS = [('warn', 'Warn'), ('block', 'Block')]
    policy = models.ForeignKey(ExpensePolicy, on_delete=models.CASCADE, related_name='limits')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.CASCADE, related_name='policy_limits')
    per_expense_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    per_day_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    per_report_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    block_or_warn = models.CharField(max_length=5, choices=ACTIONS, default='warn')

    class Meta:
        ordering = ['policy', 'category__name']
        unique_together = ('policy', 'category')


class CostCenter(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    department_id = models.IntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} - {self.name}'


class ExpenseSettings(models.Model):
    """One row: the accounts of the accounting export."""
    payable_account = models.CharField(max_length=50, default='2150', help_text='Employee payable / reimbursements due')
    advance_account = models.CharField(max_length=50, default='1450', help_text='Employee advances (clearing)')
    default_expense_account = models.CharField(max_length=50, default='6990', help_text='For categories without an account')

    class Meta:
        verbose_name_plural = 'expense settings'

    @classmethod
    def get(cls):
        return cls.objects.order_by('pk').first() or cls.objects.create()


class Trip(models.Model):
    STATUS = [('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('closed', 'Closed')]
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    purpose = models.CharField(max_length=200)
    from_place = models.CharField(max_length=100, blank=True, default='')
    to_place = models.CharField(max_length=100, blank=True, default='')
    start_date = models.DateField()
    end_date = models.DateField()
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    advance_requested = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=12, choices=STATUS, default='draft')
    notes = models.TextField(blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-id']

    def __str__(self):
        return f'{self.number} {self.purpose}'


class ExpenseReport(models.Model):
    STATUS = [('draft', 'Draft'), ('submitted', 'Awaiting approval'), ('approved', 'Approved'), ('rejected', 'Rejected'),
              ('sent_back', 'Sent back'), ('reimbursed', 'Reimbursed')]
    VIA = [('direct', 'Direct (bank / cash)'), ('payroll', 'With the payroll')]
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    policy = models.ForeignKey(ExpensePolicy, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200)
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True, related_name='reports')
    from_date = models.DateField(null=True, blank=True)
    to_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='draft')
    total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    advance_applied = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    amount_to_reimburse = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    reimburse_via = models.CharField(max_length=10, choices=VIA, blank=True, default='')
    reimbursed_on = models.DateField(null=True, blank=True)
    reimbursement_reference = models.CharField(max_length=100, blank=True, default='')
    payroll_run_id = models.IntegerField(null=True, blank=True)
    round = models.PositiveSmallIntegerField(default=0, help_text='Approval round (a sent-back report starts a new one)')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']
        permissions = (('reimburse_expensereport', 'Can reimburse expense reports'),
                       ('export_expensereport', 'Can export expense accounting entries'))

    def __str__(self):
        return f'{self.number} {self.title}'


class Expense(models.Model):
    STATUS = [('unreported', 'Unreported'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'),
              ('reimbursed', 'Reimbursed')]
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True)
    date = models.DateField()
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name='expenses')
    merchant = models.CharField(max_length=150, blank=True, default='')
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=3, default='AED')
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=6, default=Decimal('1'))
    amount_aed = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    description = models.TextField(blank=True, default='')
    receipt = models.FileField(upload_to='expense_receipts/%Y/%m/', null=True, blank=True)
    mileage_km = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    mileage_from = models.CharField(max_length=150, blank=True, default='')
    mileage_to = models.CharField(max_length=150, blank=True, default='')
    per_diem_days = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    project_id = models.IntegerField(null=True, blank=True)
    cost_center = models.ForeignKey(CostCenter, on_delete=models.SET_NULL, null=True, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    billable = models.BooleanField(default=False)
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    report = models.ForeignKey(ExpenseReport, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses')
    status = models.CharField(max_length=12, choices=STATUS, default='unreported')
    policy_violations = models.JSONField(default=list, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.date} {self.category} {self.amount_aed}'


class ExpenseAdvance(models.Model):
    STATUS = [('requested', 'Requested'), ('approved', 'Approved'), ('paid', 'Paid'), ('settled', 'Settled'), ('rejected', 'Rejected')]
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True)
    trip = models.ForeignKey(Trip, on_delete=models.SET_NULL, null=True, blank=True, related_name='advances')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    purpose = models.CharField(max_length=200, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS, default='requested')
    reference = models.CharField(max_length=100, blank=True, default='', help_text='Payment reference')
    approved_by_id = models.IntegerField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.number} {self.amount}'


class AdvanceApplication(models.Model):
    """Part of a paid advance used against an expense report."""
    advance = models.ForeignKey(ExpenseAdvance, on_delete=models.CASCADE, related_name='applications')
    report = models.ForeignKey(ExpenseReport, on_delete=models.CASCADE, related_name='advance_lines')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)


class ExpenseApproval(models.Model):
    STATUS = [('waiting', 'Waiting (earlier level first)'), ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'),
              ('sent_back', 'Sent back'), ('skipped', 'Skipped')]
    report = models.ForeignKey(ExpenseReport, on_delete=models.CASCADE, null=True, blank=True, related_name='approvals')
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, null=True, blank=True, related_name='approvals')
    round = models.PositiveSmallIntegerField(default=1)
    level = models.PositiveSmallIntegerField(default=1)
    approver_user_id = models.IntegerField(db_index=True)
    role = models.CharField(max_length=30, blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS, default='waiting')
    note = models.TextField(blank=True, default='')
    acted_by_user_id = models.IntegerField(null=True, blank=True)
    acted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['round', 'level', 'id']
