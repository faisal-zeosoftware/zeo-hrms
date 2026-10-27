"""ZEO accounting integration (v1.35.0): HR and payroll events become balanced journal entries that are posted to the
company's accounting application (Odoo, Zoho Books) or exported (Excel / CSV) – or pulled by any application through the API.

Department, employee and branch can each be carried on the journal lines as an analytic account / reporting tag, as a
separate GL code, or not at all – a setting of each company. The dimensions are frozen on the line when the journal is
built, so a later transfer never moves a posted cost."""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
MONEY = dict(max_digits=14, decimal_places=2)

CONNECTORS = (('export', 'Excel / CSV export only'), ('odoo', 'Odoo'), ('zoho', 'Zoho Books'), ('api', 'Pulled by another application (ZEO API)'))
DIM_MODE = (('none', 'Not posted'), ('analytic', 'Analytic account / reporting tag'), ('gl', 'Separate GL code'), ('both', 'GL code and analytic'))
DIM_MODE_EMP = (('none', 'Not posted'), ('analytic', 'Analytic account / reporting tag'), ('gl', 'Separate GL code'), ('both', 'GL code and analytic'),
                ('partner', 'Partner / contact on the line'))
DIMS_ON = (('cost', 'Cost (expense) lines only'), ('all', 'Every line, also payables and deductions'))
POST_AS = (('draft', 'Draft – finance posts it in the accounting app'), ('posted', 'Posted'))
POST_STYLE = (('journal', 'Journal vouchers (JV) – one balanced journal entry per document'),
              ('documents', 'Invoices, bills and payments – synced as documents'))   # v1.62.0


class AccountingSettings(models.Model):
    connector = models.CharField(max_length=10, choices=CONNECTORS, default='export')
    # posting rules
    department_mode = models.CharField('department posting', max_length=10, choices=DIM_MODE, default='analytic')
    employee_mode = models.CharField('employee posting', max_length=10, choices=DIM_MODE_EMP, default='none')
    branch_mode = models.CharField('branch posting', max_length=10, choices=DIM_MODE, default='none')
    dims_on = models.CharField('lines that carry department / employee', max_length=5, choices=DIMS_ON, default='cost')
    dept_gl_pattern = models.CharField('department GL code pattern', max_length=40, default='{account}-{dept}',
                                       help_text='Used when a department has no GL code of its own. {account} {dept} {branch}')
    emp_gl_pattern = models.CharField('employee GL code pattern', max_length=40, default='{account}-{emp}',
                                      help_text='Used when an employee has no GL code of its own. {account} {emp} {dept}')
    branch_gl_pattern = models.CharField('branch GL code pattern', max_length=40, default='{account}-{branch}')
    post_as = models.CharField(max_length=8, choices=POST_AS, default='draft')
    # v1.62.0 how Property invoices / receipts / vendor bills reach Odoo or Zoho Books: as JVs, or as customer invoices and credit
    # notes, customer payments matched to the invoices, and vendor bills (payroll, payouts, commissions, refunds stay JVs)
    post_style = models.CharField('send documents as', max_length=10, choices=POST_STYLE, default='journal')
    sales_journal_code = models.CharField('customer invoices journal (Odoo code)', max_length=20, default='INV', blank=True)
    purchase_journal_code = models.CharField('vendor bills journal (Odoo code)', max_length=20, default='BILL', blank=True)
    bank_journal_code = models.CharField('payments journal (Odoo code)', max_length=20, default='BNK1', blank=True)
    sale_tax_ref = models.CharField('VAT on sales – tax id (empty = the tax with the same rate)', max_length=40, blank=True)
    purchase_tax_ref = models.CharField('VAT on purchases – tax id (empty = the tax with the same rate)', max_length=40, blank=True)
    auto_build = models.BooleanField('build the journal when a payroll run is approved', default=True)
    auto_post = models.BooleanField('send it to the accounting app at once', default=False)
    partner_lines = models.BooleanField('business partner on receivable / payable lines', default=True,
                                        help_text='v1.44.0: tenant, buyer, owner, vendor and broker lines carry the partner (Odoo partner / Zoho contact; the BP number in exports)')
    journal_code = models.CharField('journal (Odoo code / Zoho notes)', max_length=20, default='SAL', blank=True)
    currency = models.CharField(max_length=3, default='AED')
    # default accounts
    payable_account = models.CharField('salaries payable', max_length=40, default='2110')
    rounding_account = models.CharField('rounding differences', max_length=40, default='6999')
    gratuity_expense_account = models.CharField('gratuity expense', max_length=40, default='6150')
    gratuity_provision_account = models.CharField('gratuity provision', max_length=40, default='2310')
    leave_expense_account = models.CharField('leave salary expense', max_length=40, default='6155')
    leave_provision_account = models.CharField('leave salary provision', max_length=40, default='2320')
    settlement_payable_account = models.CharField('final settlement payable', max_length=40, default='2115')
    default_expense_account = models.CharField('salary expense when a component has no account', max_length=40, default='6100')
    default_deduction_account = models.CharField('deductions when a component has no account', max_length=40, default='2190')
    # Odoo
    odoo_url = models.CharField(max_length=200, blank=True)
    odoo_db = models.CharField(max_length=100, blank=True)
    odoo_login = models.CharField('Odoo user (login)', max_length=150, blank=True)
    odoo_api = models.CharField('Odoo API', max_length=8, choices=(('json2', 'JSON-2 (Odoo 19 and later)'), ('xmlrpc', 'XML-RPC (Odoo 18 and earlier)')),
                                default='json2')
    odoo_company_id = models.IntegerField('Odoo company id', null=True, blank=True)
    odoo_dept_plan_id = models.IntegerField('Odoo analytic plan for departments', null=True, blank=True)
    odoo_emp_plan_id = models.IntegerField('Odoo analytic plan for employees', null=True, blank=True)
    odoo_branch_plan_id = models.IntegerField('Odoo analytic plan for branches', null=True, blank=True)
    # Zoho Books
    zoho_api_domain = models.CharField('Zoho API domain', max_length=120, default='https://www.zohoapis.com', blank=True)
    zoho_accounts_domain = models.CharField('Zoho accounts domain', max_length=120, default='https://accounts.zoho.com', blank=True)
    zoho_org_id = models.CharField('Zoho organisation id', max_length=40, blank=True)
    zoho_client_id = models.CharField(max_length=120, blank=True)
    zoho_dept_tag_id = models.CharField('Zoho reporting tag for departments', max_length=40, blank=True)
    zoho_emp_tag_id = models.CharField('Zoho reporting tag for employees', max_length=40, blank=True)
    zoho_branch_tag_id = models.CharField('Zoho reporting tag for branches', max_length=40, blank=True)
    # secrets (encrypted, never sent back to the browser)
    secrets = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [('manage_accounting', 'Can set up the accounting integration'),
                       ('post_accounting', 'Can build, post and reverse accounting journals'),
                       ('view_accounting', 'Can see accounting journals')]

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


MAP_KIND = (('component', 'Salary component'), ('payable', 'Payable (net salary, settlement)'), ('provision', 'Provision (gratuity, leave salary)'),
            ('settlement', 'Final settlement line'), ('expense', 'Expense category'), ('property', 'Property (v1.40.0)'),
            ('allocation', 'Cost allocation (v1.50.0)'))


class AccountMap(models.Model):
    """The accounts of a salary component (or settlement line, expense category). The account can be given for one
    department / employee / branch too – the most specific row wins."""
    kind = models.CharField(max_length=10, choices=MAP_KIND, default='component')
    key = models.CharField(max_length=60, help_text='component id, settlement line key or expense category id')
    label = models.CharField(max_length=150, blank=True)
    debit_account = models.CharField(max_length=40, blank=True, help_text='Additions and employer cost: the expense account')
    credit_account = models.CharField(max_length=40, blank=True, help_text='Deductions and employer contributions: the liability account')
    department_id = models.IntegerField(null=True, blank=True)
    employee_id = models.IntegerField(null=True, blank=True)
    branch_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['kind', 'key', 'department_id', 'employee_id']


DIM_KIND = (('department', 'Department'), ('employee', 'Employee'), ('branch', 'Branch'))


class DimensionMap(models.Model):
    """A ZEO department / employee / branch in the accounting app: its analytic account or reporting-tag option, its GL code
    (or GL suffix used by the pattern) and, for employees, the partner / contact."""
    kind = models.CharField(max_length=10, choices=DIM_KIND)
    local_id = models.IntegerField()
    label = models.CharField(max_length=150, blank=True)
    gl_code = models.CharField('GL code (or code used in the pattern)', max_length=40, blank=True)
    analytic_ref = models.CharField('analytic account id / tag option id', max_length=60, blank=True)
    analytic_name = models.CharField(max_length=150, blank=True)
    partner_ref = models.CharField('partner / contact id', max_length=60, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('kind', 'local_id')]
        ordering = ['kind', 'label']


SOURCES = (('payroll', 'Payroll run'), ('provision', 'Month-end provisions'), ('settlement', 'Final settlement'), ('expense', 'Expense claims'),
           ('reversal', 'Reversal'), ('assets', 'Asset depreciation'),
           # v1.40.0 Property app (one journal per Property document; source_id = its id, refunds "R<id>" under prop_receipt)
           ('prop_bill', 'Property invoices / credit notes'), ('prop_receipt', 'Property receipts'), ('prop_payout', 'Owner payouts'),
           ('prop_commission', 'Property commissions'), ('prop_vendor', 'Property vendor bills'),
           ('cost_allocation', 'Payroll cost allocation'))   # v1.50.0
STATUS = (('draft', 'Draft – check it'), ('ready', 'Ready to post'), ('posting', 'Posting'), ('posted', 'Posted'), ('failed', 'Failed'),
          ('exported', 'Exported'), ('reversed', 'Reversed'), ('cancelled', 'Cancelled'))


class JournalBatch(models.Model):
    """One journal entry for the accounting app (the outbox). Never changed once posted: a correction is a reversal."""
    number = models.CharField(max_length=30, blank=True, db_index=True)
    source = models.CharField(max_length=16, choices=SOURCES)   # v1.40.0: 16 for prop_commission
    source_id = models.CharField(max_length=40, blank=True, db_index=True)
    source_label = models.CharField(max_length=200, blank=True)
    date = models.DateField()
    reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='draft', db_index=True)
    connector = models.CharField(max_length=10, blank=True)
    total_debit = models.DecimalField(default=0, **MONEY)
    total_credit = models.DecimalField(default=0, **MONEY)
    lines_count = models.PositiveIntegerField(default=0)
    rules = models.JSONField(default=dict, blank=True, help_text='posting rules used (department / employee / branch modes)')
    problems = models.JSONField(default=list, blank=True, help_text='what blocks posting (unmapped departments, accounts …)')
    external_id = models.CharField('id in the accounting app', max_length=60, blank=True)
    external_ref = models.CharField('number in the accounting app', max_length=80, blank=True)
    idempotency_key = models.CharField(max_length=80, unique=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    reversal_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='reversals')
    branch_id = models.IntegerField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class JournalLine(models.Model):
    batch = models.ForeignKey(JournalBatch, on_delete=models.CASCADE, related_name='lines')
    seq = models.PositiveIntegerField(default=0)
    account = models.CharField(max_length=60)
    base_account = models.CharField(max_length=40, blank=True, help_text='the account before the department / employee GL code')
    label = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(default=0, **MONEY)
    credit = models.DecimalField(default=0, **MONEY)
    department_id = models.IntegerField(null=True, blank=True)
    department = models.CharField(max_length=150, blank=True)
    employee_id = models.IntegerField(null=True, blank=True)
    employee = models.CharField(max_length=200, blank=True)
    branch_id = models.IntegerField(null=True, blank=True)
    branch = models.CharField(max_length=150, blank=True)
    analytics = models.JSONField(default=dict, blank=True, help_text='{"department": ref, "employee": ref, "branch": ref}')
    partner_ref = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ['batch', 'seq']


class ProvisionBalance(models.Model):
    """The provision already posted per employee (gratuity, leave salary) – the next run posts only the change."""
    kind = models.CharField(max_length=10, choices=(('gratuity', 'Gratuity'), ('leave', 'Leave salary')))
    employee_id = models.IntegerField()
    amount = models.DecimalField(default=0, **MONEY)
    as_of = models.DateField()
    batch = models.ForeignKey(JournalBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        unique_together = [('kind', 'employee_id')]


class SyncLog(models.Model):
    batch = models.ForeignKey(JournalBatch, null=True, blank=True, on_delete=models.CASCADE, related_name='log')
    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=40)
    ok = models.BooleanField(default=True)
    detail = models.TextField(blank=True)
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-at', '-id']


class ApiClient(models.Model):
    """Another application that reads journals through the ZEO API (client credentials: client id + secret → 1-hour token)."""
    name = models.CharField(max_length=100)
    client_id = models.CharField(max_length=40, unique=True)
    secret_hash = models.CharField(max_length=200)
    can_ack = models.BooleanField('may mark journals as posted', default=True)
    is_active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ApiClientHook(models.Model):
    """Webhook of another application: called when a journal is ready / posted (ZEO API, pull mode)."""
    name = models.CharField(max_length=100)
    url = models.URLField()
    events = models.JSONField(default=list, blank=True, help_text='["journal.ready", "journal.posted"]')
    secret = models.CharField(max_length=100, blank=True, help_text='HMAC-SHA256 signature key (X-ZEO-Signature)')
    is_active = models.BooleanField(default=True)
    last_status = models.CharField(max_length=120, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
