"""ZEO Accounting (v1.76.0) – the general ledger on the app kit.

Chart of accounts, fiscal years and periods, journals, journal entries (posted entries are never edited – corrected by a reversal),
customer invoices and credit notes, supplier bills and debit notes, receipts and payments with allocation and post-dated cheques,
bank statements and reconciliation, multi-currency with revaluation, cost centres, budgets, UAE VAT (VAT 201 by emirate), corporate
tax (9 % above AED 375,000, small business relief), e-invoicing data (PINT AE). Partners and items are the shared masters
(ZeoMasters); HR, payroll and Inventory journals come from the accounting outbox (ZeoAccounting.JournalBatch)."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, SequenceBase

USER = settings.AUTH_USER_MODEL
BRANCH = 'OrganisationManager.brnch_mstr'
PARTNER = 'ZeoMasters.Partner'
PRODUCT = 'ZeoMasters.Product'
MONEY = dict(max_digits=18, decimal_places=3)
RATE = dict(max_digits=18, decimal_places=8)

ROLES = (('gl_manager', 'Finance manager – set-up, periods, year end, approvals'),
         ('gl_accountant', 'Accountant – journals, invoices, bills, payments, bank'),
         ('gl_ar', 'Receivables – customer invoices and receipts'),
         ('gl_ap', 'Payables – supplier bills and payments'),
         ('gl_viewer', 'Accounting viewer (read only)'))

TYPES = (('asset', 'Asset'), ('liability', 'Liability'), ('equity', 'Equity'), ('income', 'Income'), ('expense', 'Expense'))
GROUPS = (
    ('cash', 'Cash'), ('bank', 'Bank'), ('receivable', 'Receivable'), ('current_asset', 'Other current asset'), ('inventory', 'Inventory'),
    ('prepayment', 'Prepayment'), ('fixed_asset', 'Fixed asset'), ('accum_depr', 'Accumulated depreciation'), ('non_current_asset', 'Other non-current asset'),
    ('payable', 'Payable'), ('current_liability', 'Other current liability'), ('tax', 'Tax (VAT, corporate tax)'), ('non_current_liability', 'Non-current liability'),
    ('equity', 'Capital and reserves'), ('retained', 'Retained earnings'),
    ('revenue', 'Revenue'), ('other_income', 'Other income'),
    ('cost_of_sales', 'Cost of sales'), ('expense', 'Operating expense'), ('depreciation', 'Depreciation'), ('finance_cost', 'Finance cost'), ('other_expense', 'Other expense'),
    ('income_tax', 'Income tax expense'),
)
GROUP_TYPE = {'cash': 'asset', 'bank': 'asset', 'receivable': 'asset', 'current_asset': 'asset', 'inventory': 'asset', 'prepayment': 'asset',
              'fixed_asset': 'asset', 'accum_depr': 'asset', 'non_current_asset': 'asset', 'payable': 'liability', 'current_liability': 'liability',
              'tax': 'liability', 'non_current_liability': 'liability', 'equity': 'equity', 'retained': 'equity', 'revenue': 'income',
              'other_income': 'income', 'cost_of_sales': 'expense', 'expense': 'expense', 'depreciation': 'expense', 'finance_cost': 'expense',
              'other_expense': 'expense', 'income_tax': 'expense'}
CT = (('deductible', 'Deductible'), ('half', '50 % deductible (entertainment)'), ('non_deductible', 'Not deductible (fines, penalties, donations …)'),
      ('exempt_income', 'Exempt income (dividends, qualifying gains)'), ('ct_expense', 'Corporate tax itself (added back)'))
CONTROL = (('', 'None'), ('receivable', 'Customer balances'), ('payable', 'Supplier balances'), ('bank', 'Bank / cash'))
EMIRATES = (('AZ', 'Abu Dhabi'), ('DU', 'Dubai'), ('SH', 'Sharjah'), ('AJ', 'Ajman'), ('UQ', 'Umm Al Quwain'), ('RK', 'Ras Al Khaimah'), ('FU', 'Fujairah'))
TREATMENTS = (('SR', 'Standard rated 5 %'), ('ZR', 'Zero rated'), ('EX', 'Exempt'), ('OS', 'Out of scope'), ('RC', 'Reverse charge (imported services)'),
              ('IM', 'Imported goods (reverse charge through customs)'), ('BL', 'Blocked input tax (not recoverable)'))
JOURNAL_KINDS = (('general', 'General'), ('sale', 'Sales'), ('purchase', 'Purchases'), ('bank', 'Bank'), ('cash', 'Cash'),
                 ('opening', 'Opening balances'), ('fx', 'Exchange revaluation'), ('closing', 'Year end'), ('outbox', 'From other apps'))
ENTRY_STATUS = (('draft', 'Draft'), ('posted', 'Posted'), ('reversed', 'Reversed'))
DOC_KINDS = (('invoice', 'Customer invoice'), ('credit', 'Customer credit note'), ('bill', 'Supplier bill'), ('debit', 'Supplier debit note'))
DOC_STATUS = (('draft', 'Draft'), ('posted', 'Posted'), ('part_paid', 'Partly paid'), ('paid', 'Paid'), ('cancelled', 'Cancelled'))
PAY_KINDS = (('receipt', 'Receipt from a customer'), ('payment', 'Payment to a supplier'), ('transfer', 'Transfer between bank / cash accounts'))
PAY_METHODS = (('transfer', 'Bank transfer'), ('cheque', 'Cheque'), ('cash', 'Cash'), ('card', 'Card'), ('other', 'Other'))
PAY_STATUS = (('draft', 'Draft'), ('posted', 'Posted'), ('pdc', 'Post-dated cheque (held)'), ('cleared', 'Cheque cleared'), ('bounced', 'Cheque returned'),
              ('reversed', 'Reversed'))
PERIOD_STATUS = (('open', 'Open'), ('closed', 'Closed – the finance manager may still post'), ('locked', 'Locked'))
VAT_PERIODS = (('quarterly', 'Quarterly'), ('monthly', 'Monthly'))
EINV_STATUS = (('', 'Not sent'), ('ready', 'Ready for the service provider'), ('sent', 'Sent'), ('accepted', 'Accepted'), ('rejected', 'Rejected'))
SOURCES = (('manual', 'Manual'), ('invoice', 'Invoice / bill'), ('payment', 'Receipt / payment'), ('outbox', 'Other ZEO app'), ('fx', 'Revaluation'),
           ('closing', 'Year end'), ('opening', 'Opening balance'), ('recurring', 'Recurring'), ('bank', 'Bank'))


class LedgerSettings(AppSettingsBase):
    currency = models.CharField(max_length=3, default='AED')
    fy_start_month = models.PositiveSmallIntegerField('financial year starts (month)', default=1)
    fy_start_day = models.PositiveSmallIntegerField('financial year starts (day of the month, 1–28)', default=1,
                                                    help_text='v1.79.3 like Sage 200 Evolution: e.g. 26 with month 3 → years run 26 March to 25 March')
    vat_registered = models.BooleanField('VAT registered', default=True)
    trn = models.CharField('tax registration number (TRN)', max_length=20, blank=True)
    vat_period = models.CharField('VAT return', max_length=10, choices=VAT_PERIODS, default='quarterly')
    vat_first_month = models.PositiveSmallIntegerField('first month of a VAT quarter (stagger)', default=1)
    emirate = models.CharField('emirate of the business', max_length=2, choices=EMIRATES, default='DU')
    lock_date = models.DateField('nothing on or before (lock date)', null=True, blank=True)
    prior_year_posting = models.BooleanField('allow posting to closed years (finance manager, journal entries)', default=False,
                                             help_text='Like Sage 300 "Allow posting to prior years": the profit or loss of a late entry goes to retained earnings at once')
    ct_trn = models.CharField('corporate tax registration number', max_length=20, blank=True)
    ct_sbr = models.BooleanField('elect small business relief (revenue up to AED 3 m)', default=False)
    ct_qfzp = models.BooleanField('qualifying free zone person', default=False)
    einvoicing = models.BooleanField('e-invoicing (PINT AE) data on invoices', default=False)
    endpoint_id = models.CharField('Peppol participant ID', max_length=60, blank=True)
    auto_post = models.BooleanField('post invoices and payments on save', default=False)
    decimals = models.PositiveSmallIntegerField(default=2)
    accounts = models.JSONField(default=dict, blank=True, help_text='role → account code (receivable, payable, vat_out, vat_in, fx_gain …)')


class PostingRight(models.Model):
    """v1.79.3 per-user posting rights (Sage 200 Evolution agent date and period-lock rights)"""
    user = models.OneToOneField(USER, on_delete=models.CASCADE, related_name='+')
    allow_locked = models.BooleanField('may post in locked periods and locked modules', default=False)
    periods_back = models.PositiveSmallIntegerField('may post this many periods back (empty: no limit)', null=True, blank=True)
    periods_ahead = models.PositiveSmallIntegerField('may post this many periods ahead (empty: no limit)', null=True, blank=True)
    date_from = models.DateField('may post from (date)', null=True, blank=True)
    date_to = models.DateField('may post up to (date)', null=True, blank=True)
    lock_gl = models.BooleanField('no postings: general ledger', default=False)
    lock_ar = models.BooleanField('no postings: customers', default=False)
    lock_ap = models.BooleanField('no postings: suppliers', default=False)
    lock_bank = models.BooleanField('no postings: bank and cash', default=False)
    lock_apps = models.BooleanField('no postings: other ZEO apps', default=False)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return str(self.user)


class LedgerSequence(SequenceBase):
    pass


class LedgerEvent(EventBase):
    pass


class LedgerFile(FileBase):
    pass


class GLAccount(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    name_ar = models.CharField('name (Arabic)', max_length=150, blank=True)
    group = models.CharField(max_length=24, choices=GROUPS, default='expense')
    type = models.CharField(max_length=10, choices=TYPES, default='expense', editable=False)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children', verbose_name='heading')
    is_heading = models.BooleanField('heading (no postings)', default=False)
    control = models.CharField('control account', max_length=10, choices=CONTROL, blank=True)
    currency = models.CharField('currency (empty: company)', max_length=3, blank=True)
    reconcile = models.BooleanField('matched (open items)', default=False)
    tax = models.ForeignKey('Tax', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='default tax')
    ct_treatment = models.CharField('corporate tax', max_length=16, choices=CT, default='deductible')
    cost_centre_required = models.BooleanField(default=False)
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='Empty: every branch')
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['code']

    def save(self, *a, **kw):
        self.type = GROUP_TYPE.get(self.group, self.type)
        super().save(*a, **kw)

    def __str__(self):
        return f'{self.code} {self.name}'


class FiscalYear(models.Model):
    name = models.CharField(max_length=20, unique=True)
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=8, choices=(('open', 'Open'), ('closed', 'Closed')), default='open')
    closing_entry = models.ForeignKey('Entry', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-start']

    def __str__(self):
        return self.name


MODULES = (('gl', 'General ledger (journal entries)'), ('ar', 'Customers (invoices, credit notes)'), ('ap', 'Suppliers (bills, debit notes)'),
           ('bank', 'Bank and cash (receipts, payments, transfers)'), ('apps', 'Other ZEO apps (payroll, inventory, property …)'))


class Period(models.Model):
    year = models.ForeignKey(FiscalYear, on_delete=models.CASCADE, related_name='periods')
    number = models.PositiveSmallIntegerField()
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=8, choices=PERIOD_STATUS, default='open')
    # v1.79.3 module locks (Sage 200 Evolution style) – a locked module takes no postings dated in the period
    lock_gl = models.BooleanField('locked: general ledger', default=False)
    lock_ar = models.BooleanField('locked: customers', default=False)
    lock_ap = models.BooleanField('locked: suppliers', default=False)
    lock_bank = models.BooleanField('locked: bank and cash', default=False)
    lock_apps = models.BooleanField('locked: other ZEO apps', default=False)

    class Meta:
        ordering = ['start']
        unique_together = [('year', 'number')]

    def __str__(self):   # v1.79.3 a period that does not start on the 1st shows its dates
        return f'{self.start:%b %Y}' if self.start.day == 1 else f'P{self.number} {self.start:%d/%m}–{self.end:%d/%m/%Y}'


class CostCentre(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Tax(models.Model):
    code = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=80)
    use = models.CharField(max_length=8, choices=(('sale', 'Sales'), ('purchase', 'Purchases')), default='sale')
    treatment = models.CharField(max_length=2, choices=TREATMENTS, default='SR')
    rate = models.DecimalField('rate %', max_digits=6, decimal_places=3, default=5)
    recoverable = models.DecimalField('recoverable %', max_digits=6, decimal_places=2, default=100)
    account = models.ForeignKey(GLAccount, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='VAT account')
    rc_account = models.ForeignKey(GLAccount, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='reverse-charge output account')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['use', 'code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Journal(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=JOURNAL_KINDS, default='general')
    account = models.ForeignKey(GLAccount, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='bank / cash account')
    pdc_account = models.ForeignKey(GLAccount, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='post-dated cheques account')
    currency = models.CharField(max_length=3, blank=True)
    iban = models.CharField('IBAN', max_length=34, blank=True)
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Entry(models.Model):
    number = models.CharField(max_length=30, blank=True, db_index=True)
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name='entries')
    date = models.DateField()
    reference = models.CharField(max_length=80, blank=True)
    narration = models.CharField(max_length=255, blank=True)
    currency = models.CharField(max_length=3, blank=True, help_text='Empty: the company currency')
    rate = models.DecimalField(null=True, blank=True, **RATE)
    status = models.CharField(max_length=10, choices=ENTRY_STATUS, default='draft')
    source = models.CharField(max_length=10, choices=SOURCES, default='manual')
    source_ref = models.CharField(max_length=60, blank=True)
    reversal_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='reversals')
    auto_reverse = models.DateField('reverse on', null=True, blank=True)
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    total = models.DecimalField(default=0, **MONEY)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        verbose_name_plural = 'entries'

    def __str__(self):
        return self.number or f'Draft {self.pk}'


class EntryLine(models.Model):
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(GLAccount, on_delete=models.PROTECT, related_name='lines')
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    cost_centre = models.ForeignKey(CostCentre, null=True, blank=True, on_delete=models.SET_NULL, related_name='lines')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(default=0, **MONEY)
    credit = models.DecimalField(default=0, **MONEY)
    currency = models.CharField(max_length=3, blank=True)
    amount_currency = models.DecimalField('amount in the currency', default=0, **MONEY)
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    tax_base = models.DecimalField('taxable amount', null=True, blank=True, **MONEY)
    vat_box = models.CharField(max_length=4, blank=True)
    emirate = models.CharField(max_length=2, choices=EMIRATES, blank=True)
    due_date = models.DateField(null=True, blank=True)
    doc = models.ForeignKey('Document', null=True, blank=True, on_delete=models.SET_NULL, related_name='entry_lines')
    match = models.CharField('matched as', max_length=20, blank=True, db_index=True)
    bank_line = models.ForeignKey('BankLine', null=True, blank=True, on_delete=models.SET_NULL, related_name='matched_lines')
    date = models.DateField(null=True, blank=True, db_index=True)        # the entry's date (copied for fast reports)
    posted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['entry', 'id']


class Document(models.Model):
    """customer invoice / credit note, supplier bill / debit note"""
    kind = models.CharField(max_length=8, choices=DOC_KINDS, default='invoice')
    number = models.CharField(max_length=30, blank=True, db_index=True)
    partner = models.ForeignKey(PARTNER, on_delete=models.PROTECT, related_name='+')
    date = models.DateField()
    supply_date = models.DateField('date of supply', null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    supplier_ref = models.CharField('supplier invoice no.', max_length=60, blank=True)
    reference = models.CharField('your reference / PO', max_length=60, blank=True)
    original = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='adjust_notes', verbose_name='original invoice')
    currency = models.CharField(max_length=3, blank=True)
    rate = models.DecimalField(null=True, blank=True, **RATE)
    emirate = models.CharField('place of supply (emirate)', max_length=2, choices=EMIRATES, blank=True)
    journal = models.ForeignKey(Journal, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    payment_term = models.ForeignKey('ZeoMasters.PaymentTerm', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=10, choices=DOC_STATUS, default='draft')
    net = models.DecimalField(default=0, **MONEY)
    vat = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    balance = models.DecimalField('to pay', default=0, **MONEY)
    total_base = models.DecimalField('total in the company currency', default=0, **MONEY)
    entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notes = models.TextField(blank=True)
    source = models.CharField(max_length=30, blank=True, help_text='App that made it (sales, purchase …)')
    source_ref = models.CharField(max_length=60, blank=True)
    einv_type = models.CharField('e-invoice type', max_length=3, blank=True, help_text='380 invoice, 381 credit note, 480 …')
    einv_uuid = models.CharField(max_length=40, blank=True)
    einv_status = models.CharField(max_length=10, choices=EINV_STATUS, blank=True)
    einv_message = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or f'Draft {self.get_kind_display()}'

    @property
    def sale(self):
        return self.kind in ('invoice', 'credit')


class DocumentLine(models.Model):
    doc = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(PRODUCT, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    description = models.CharField(max_length=255, blank=True)
    account = models.ForeignKey(GLAccount, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    qty = models.DecimalField(default=1, max_digits=16, decimal_places=4)
    price = models.DecimalField(default=0, max_digits=18, decimal_places=6)
    discount = models.DecimalField('discount %', default=0, max_digits=6, decimal_places=3)
    tax = models.ForeignKey(Tax, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    net = models.DecimalField(default=0, **MONEY)
    vat = models.DecimalField(default=0, **MONEY)
    cost_centre = models.ForeignKey(CostCentre, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['doc', 'id']


class Payment(models.Model):
    kind = models.CharField(max_length=8, choices=PAY_KINDS, default='receipt')
    number = models.CharField(max_length=30, blank=True, db_index=True)
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    date = models.DateField()
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name='+', verbose_name='bank / cash')
    to_journal = models.ForeignKey(Journal, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='to bank / cash')
    method = models.CharField(max_length=8, choices=PAY_METHODS, default='transfer')
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3, blank=True)
    rate = models.DecimalField(null=True, blank=True, **RATE)
    cheque_no = models.CharField(max_length=30, blank=True)
    cheque_date = models.DateField('cheque date', null=True, blank=True)
    cheque_bank = models.CharField(max_length=80, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=PAY_STATUS, default='draft')
    entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    clear_entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    unallocated = models.DecimalField(default=0, **MONEY)
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Draft'


class Allocation(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='allocations')
    doc = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField('amount (in the payment currency)', **MONEY)
    fx_diff = models.DecimalField('realised exchange difference', default=0, **MONEY)

    class Meta:
        ordering = ['id']


class BankStatement(models.Model):
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name='statements')
    number = models.CharField(max_length=30, blank=True)
    date = models.DateField()
    opening = models.DecimalField(default=0, **MONEY)
    closing = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=10, choices=(('open', 'Open'), ('done', 'Reconciled')), default='open')

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or f'{self.journal.code} {self.date}'


class BankLine(models.Model):
    statement = models.ForeignKey(BankStatement, on_delete=models.CASCADE, related_name='lines')
    date = models.DateField()
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField('balance change (+ in, − out)', **MONEY)
    matched = models.BooleanField(default=False)

    class Meta:
        ordering = ['date', 'id']


class Budget(models.Model):
    name = models.CharField(max_length=80)
    year = models.ForeignKey(FiscalYear, on_delete=models.CASCADE, related_name='budgets')
    account = models.ForeignKey(GLAccount, on_delete=models.CASCADE, related_name='budgets')
    cost_centre = models.ForeignKey(CostCentre, null=True, blank=True, on_delete=models.CASCADE, related_name='budgets')
    amounts = models.JSONField(default=list, blank=True, help_text='12 monthly amounts')
    total = models.DecimalField(default=0, **MONEY)

    class Meta:
        ordering = ['year', 'account__code']


class FxRate(models.Model):
    currency = models.CharField(max_length=3)
    date = models.DateField()
    rate = models.DecimalField('1 unit = … company currency', **RATE)

    class Meta:
        ordering = ['currency', '-date']
        unique_together = [('currency', 'date')]


class Recurring(models.Model):
    name = models.CharField(max_length=80)
    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name='+')
    every = models.CharField(max_length=8, choices=(('month', 'Every month'), ('quarter', 'Every quarter'), ('year', 'Every year')), default='month')
    next_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    lines = models.JSONField(default=list, blank=True, help_text='[{account, debit, credit, description, cost_centre}]')
    auto_post = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['next_date']


class VatReturn(models.Model):
    start = models.DateField()
    end = models.DateField()
    status = models.CharField(max_length=10, choices=(('draft', 'Draft'), ('filed', 'Filed')), default='draft')
    boxes = models.JSONField(default=dict, blank=True)
    adjustments = models.JSONField(default=dict, blank=True, help_text='box → {amount, vat}')
    due = models.DecimalField(default=0, **MONEY)
    filed_at = models.DateField(null=True, blank=True)
    reference = models.CharField('FTA reference', max_length=40, blank=True)
    entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-end']

    def __str__(self):
        return f'VAT {self.start:%d/%m/%Y} – {self.end:%d/%m/%Y}'


class CtReturn(models.Model):
    year = models.ForeignKey(FiscalYear, on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=10, choices=(('draft', 'Draft'), ('filed', 'Filed')), default='draft')
    revenue = models.DecimalField(default=0, **MONEY)
    profit = models.DecimalField('accounting profit', default=0, **MONEY)
    adjustments = models.JSONField(default=list, blank=True)
    taxable = models.DecimalField('taxable income', default=0, **MONEY)
    tax = models.DecimalField(default=0, **MONEY)
    sbr = models.BooleanField('small business relief', default=False)
    entry = models.ForeignKey(Entry, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-year__start']


# v1.79.4 financial report designer: a statement laid out to the customer's requirement – its lines (headings, accounts, formulas)
# and its columns (this period, last year, budget, variance, % of a line). Each design is a report of the report centre (gl-fr-<id>).
FR_BASIS = (('period', 'Movement in the period (profit and loss style)'), ('position', 'Balance at the date (balance sheet style)'))
FR_SCALE = (('1', 'Units'), ('1000', 'Thousands'), ('1000000', 'Millions'))


class FinReport(models.Model):
    name = models.CharField(max_length=120)
    name_ar = models.CharField('name (Arabic)', max_length=120, blank=True)
    basis = models.CharField(max_length=10, choices=FR_BASIS, default='period')
    section = models.CharField('report centre section', max_length=60, default='Designed statements')
    subtitle = models.CharField('subtitle (printed under the title)', max_length=200, blank=True)
    scale = models.CharField('show amounts in', max_length=8, choices=FR_SCALE, default='1')
    show_codes = models.BooleanField('show account codes', default=False)
    hide_zero = models.BooleanField('hide lines and accounts that are zero', default=True)
    lines = models.JSONField(default=list, blank=True, help_text='The lines from top to bottom')
    columns = models.JSONField(default=list, blank=True, help_text='The amount columns from left to right')
    notes = models.TextField('notes (printed under the report)', blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
