"""v1.33.0 – Shared masters of the ZEO business apps (the Odoo `product` / `res.partner` idea).

CRM uses them today (accounts, products, price books, tax codes, payment terms on quotations and sales orders); the Sales,
Inventory, Purchase and Accounting apps of later releases use the SAME tables, so nothing is re-keyed when they arrive.
Masters are company-wide (no branch): every branch sells the same products to the same accounts.
"""
from django.db import models

EMP = 'EmpManagement.emp_master'
MONEY = dict(max_digits=14, decimal_places=2)


TAX_CATEGORIES = (('', '–'), ('S', 'S – standard rate'), ('Z', 'Z – zero rated'), ('E', 'E – exempt'), ('O', 'O – out of scope'))


class UOM(models.Model):
    """Unit of measure. A unit may be a multiple of a base unit (1 box = 12 pcs → base = pcs, factor = 12)."""
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=60)
    category = models.CharField(max_length=10, default='unit', choices=(('unit', 'Units'), ('weight', 'Weight'), ('length', 'Length'), ('area', 'Area'),
                                                                        ('volume', 'Volume'), ('time', 'Time'), ('other', 'Other')))
    base = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='multiples', verbose_name='base unit',
                             help_text='Leave empty for a base unit (pcs, kg, m). A box of 12 pcs: base = pcs, factor = 12')
    factor = models.DecimalField('units of the base unit', max_digits=12, decimal_places=4, default=1)
    unece_code = models.CharField('e-invoice unit code', max_length=3, blank=True, help_text='UN/ECE Rec. 20 code used on e-invoices: C62 pieces, KGM kg, LTR litre, MTR metre, HUR hour, DAY day, MON month')   # v1.44.0
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'name']
        verbose_name = 'unit of measure'
        verbose_name_plural = 'units of measure'

    def __str__(self):
        return self.name

    def to_base(self, qty):
        return qty * self.factor if self.base_id else qty


class TaxCode(models.Model):
    code = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=8, default='vat', choices=(('vat', 'VAT'), ('gst', 'GST'), ('excise', 'Excise'), ('none', 'No tax')))
    rate = models.DecimalField('rate %', max_digits=6, decimal_places=3, default=0)
    inclusive = models.BooleanField('prices include tax', default=False)
    country = models.CharField(max_length=2, blank=True, help_text='ISO code of the country pack that seeded it; empty = made by the company')
    is_default = models.BooleanField('default for new products', default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'rate', 'name']

    def __str__(self):
        return f'{self.name} ({self.rate:g}%)' if self.rate else self.name


class PaymentTerm(models.Model):
    code = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=80)
    days = models.PositiveSmallIntegerField('due after (days)', default=30)
    kind = models.CharField(max_length=10, default='net', choices=(('net', 'Net days after invoice'), ('advance', 'Advance / before delivery'),
                                                                   ('eom', 'End of month + days'), ('cheques', 'Post-dated cheques')))
    instalments = models.PositiveSmallIntegerField('number of payments', default=1)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'days', 'name']

    def __str__(self):
        return self.name


class ProductCategory(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'product categories'

    def __str__(self):
        return self.name


ITEM_KINDS = (('goods', 'Goods'), ('service', 'Service'), ('bundle', 'Bundle / kit'), ('spare_part', 'Spare part'), ('consumable', 'Consumable'),
              ('asset_model', 'Asset model'), ('charge', 'Charge (rent, fee, utility)'), ('fee', 'Fee / subscription'))   # v1.43.0


class Product(models.Model):
    """The item master (v1.43.0): goods, services, spare parts, consumables, asset models, charges and fees – one list for every app.
    Stock quantities are NOT kept here – the apps keep their own stock per store / location (Asset Management parts, later Inventory)."""
    number = models.CharField(max_length=20, unique=True, blank=True)
    code = models.CharField('SKU / internal code', max_length=40, blank=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, default='goods', choices=ITEM_KINDS)
    category = models.ForeignKey(ProductCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='products')
    uom = models.ForeignKey(UOM, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='unit')
    barcode = models.CharField(max_length=40, blank=True)
    sale_price = models.DecimalField('list price', default=0, **MONEY)
    cost_price = models.DecimalField('cost', default=0, **MONEY)
    tax = models.ForeignKey(TaxCode, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='sales tax')
    track_stock = models.BooleanField('track stock (Inventory app)', default=False)
    min_price = models.DecimalField('minimum selling price', null=True, blank=True, **MONEY, help_text='A quotation line below this needs approval')
    description = models.TextField(blank=True, help_text='Printed on quotations')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.43.0 item master
    arabic_name = models.CharField('name in Arabic', max_length=200, blank=True)
    is_sold = models.BooleanField('can be sold', default=True)
    is_purchased = models.BooleanField('can be purchased', default=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    manufacturer_no = models.CharField('manufacturer part / model no.', max_length=80, blank=True)
    hs_code = models.CharField('HS code (customs)', max_length=20, blank=True)
    country_of_origin = models.CharField(max_length=60, blank=True)
    weight_kg = models.DecimalField('weight (kg)', max_digits=10, decimal_places=3, null=True, blank=True)
    purchase_price = models.DecimalField('last purchase price', null=True, blank=True, **MONEY)
    preferred_supplier = models.ForeignKey('Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='supplied_items')
    supplier_code = models.CharField("supplier's item code", max_length=60, blank=True)
    lead_time_days = models.PositiveSmallIntegerField('lead time (days)', null=True, blank=True)
    min_qty = models.DecimalField('minimum stock', max_digits=12, decimal_places=2, default=0)
    reorder_qty = models.DecimalField('reorder quantity', max_digits=12, decimal_places=2, default=0)
    valuation = models.CharField('stock valuation', max_length=10, default='average', choices=(('average', 'Weighted average'), ('fifo', 'FIFO'), ('standard', 'Standard cost')))
    standard_cost = models.DecimalField(null=True, blank=True, **MONEY)
    income_account = models.CharField('income account', max_length=40, blank=True, help_text='Code in the chart of accounts (Accounting / Odoo / Zoho)')
    expense_account = models.CharField('expense / COGS account', max_length=40, blank=True)
    stock_account = models.CharField('stock / asset account', max_length=40, blank=True)
    tags = models.JSONField(default=list, blank=True)
    used_in = models.JSONField('used in apps', default=list, blank=True)
    apps_text = models.CharField('used in', max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
    # v1.44.0 accounting-ready: e-invoice tax category, stock value
    tax_category = models.CharField('e-invoice tax category', max_length=1, blank=True, choices=TAX_CATEGORIES,
                                    help_text='S standard rate, Z zero rated, E exempt, O out of scope (empty: from the sales tax)')
    exemption_reason = models.CharField('exemption / zero-rate reason', max_length=150, blank=True, help_text='Printed on e-invoices for Z / E / O items')
    stock_qty = models.DecimalField('in stock', max_digits=14, decimal_places=2, default=0, help_text='Filled by ZEO from the stores of the apps')
    stock_value = models.DecimalField('stock value', default=0, **MONEY)

    class Meta:
        ordering = ['name']
        verbose_name = 'item'

    def __str__(self):
        return f'{self.code} {self.name}'.strip()


class PriceBook(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    currency = models.CharField(max_length=3, default='AED')
    kind = models.CharField(max_length=8, default='fixed', choices=(('fixed', 'Fixed prices per product'), ('markup', '% on the list price')))
    percent = models.DecimalField('% on the list price', max_digits=6, decimal_places=2, default=0, help_text='-10 = 10% below the list price (kind = % on the list price)')
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-is_default', 'name']

    def __str__(self):
        return self.name

    def price_of(self, product, qty=1):
        """Unit price of the product in this book, or None when the book has no price for it."""
        if self.kind == 'markup':
            return (product.sale_price * (100 + self.percent) / 100).quantize(product.sale_price.__class__('0.01'))
        best = None
        for it in self.items.filter(product=product).order_by('-min_qty'):
            if qty >= it.min_qty:
                best = it.price
                break
        return best


class PriceBookItem(models.Model):
    price_book = models.ForeignKey(PriceBook, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='prices')
    min_qty = models.DecimalField('from quantity', max_digits=12, decimal_places=2, default=1)
    price = models.DecimalField(**MONEY)

    class Meta:
        ordering = ['product__name', 'min_qty']
        unique_together = [('price_book', 'product', 'min_qty')]


ACCOUNT_KINDS = (('customer', 'Customer'), ('prospect', 'Prospect'), ('supplier', 'Supplier'), ('both', 'Customer and supplier'), ('partner', 'Partner / broker'))


class Account(models.Model):
    """A company you deal with (Odoo: res.partner is_company). CRM contacts are the people in it."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                               help_text='Branch that owns the account; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    legal_name = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=10, choices=ACCOUNT_KINDS, default='customer')
    industry = models.CharField(max_length=80, blank=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='subsidiaries', verbose_name='parent account')
    tax_id = models.CharField('tax registration no. (TRN / GSTIN)', max_length=30, blank=True)
    licence_no = models.CharField('trade licence no.', max_length=40, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=120, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    city = models.CharField(max_length=60, blank=True)
    country = models.CharField(max_length=60, blank=True)
    payment_term = models.ForeignKey(PaymentTerm, null=True, blank=True, on_delete=models.SET_NULL)
    price_book = models.ForeignKey(PriceBook, null=True, blank=True, on_delete=models.SET_NULL)
    currency = models.CharField(max_length=3, blank=True)
    credit_limit = models.DecimalField(null=True, blank=True, **MONEY)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='account manager')
    tags = models.JSONField(default=list, blank=True, help_text='e.g. VIP, Government, Reseller')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    partner = models.ForeignKey('Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='crm_accounts',
                                verbose_name='business partner')   # v1.41.0: the CRM part of a company partner

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


# ====================================================================== v1.41.0 business partners (Odoo res.partner / SAP BP)
from django.conf import settings as _settings
from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, SequenceBase

USER = _settings.AUTH_USER_MODEL

MASTER_ROLES = (
    ('mst_manager', 'Masters manager – edits business partners, merges duplicates'),
    ('mst_viewer', 'Business partners viewer (read only)'),
)

# roles a partner can have – any app may add one (customer / supplier like Odoo's ranks, plus the industry roles)
PARTNER_ROLES = (
    ('customer', 'Customer'), ('prospect', 'Prospect'), ('supplier', 'Supplier / vendor'), ('tenant', 'Tenant'), ('buyer', 'Buyer'),
    ('owner', 'Owner / landlord'), ('broker', 'Broker / agent'), ('contractor', 'Contractor'), ('student', 'Student'),
    ('guardian', 'Guardian'), ('patient', 'Patient'), ('insurer', 'Insurer'), ('employee', 'Employee'), ('other', 'Other'),
)
PARTNER_KINDS = (('individual', 'Individual'), ('company', 'Company'))
CONTACT_TYPES = (('contact', 'Contact'), ('invoice', 'Invoice address'), ('delivery', 'Delivery address'), ('other', 'Other address'))


class MasterSettings(AppSettingsBase):
    """Settings of the shared masters. Masters are always on (no activation): they exist under every business app."""
    duplicate_check = models.BooleanField('warn about possible duplicates on save', default=True)
    # v1.44.0 credit control
    credit_control = models.CharField('credit control', max_length=5, default='warn', choices=(('off', 'Off'), ('warn', 'Warn – the user may go on'), ('block', 'Block – a manager may go on')),
                                      help_text='Checked when a lease, sales order, AMC contract or bill is made for a customer over its credit limit or with overdue invoices')
    credit_overdue_days = models.PositiveSmallIntegerField('overdue days that stop new business', null=True, blank=True,
                                                           help_text='E.g. 60: an invoice more than 60 days overdue counts like an exceeded credit limit. Empty = not checked')
    lock_shared = models.BooleanField('only managers change a partner used by several apps', default=True,
                                      help_text='Names, phones, e-mails, IDs and tax numbers of a partner used in more than one app are changed by '
                                                'a Masters manager or the manager of an app; other users see them read-only.')

    class Meta:
        permissions = [(k, f'Masters role: {v}') for k, v in MASTER_ROLES]
        verbose_name = 'Masters settings'

    @classmethod
    def get(cls):
        from django.utils import timezone
        s = cls.objects.order_by('id').first() or cls.objects.create()
        if not s.activated_at:   # always on
            s.activated_at = timezone.now()
            s.save(update_fields=['activated_at'])
        return s


class MasterSequence(SequenceBase):
    pass


class MasterEvent(EventBase):
    pass


class MasterFile(FileBase):
    pass


BUYER_ID_TYPES = (('', '–'), ('CRN', 'Commercial registration'), ('MOM', 'MOMRAH licence'), ('MLS', 'MHRSD licence'), ('700', '700 number'),
                  ('SAG', 'MISA licence'), ('NAT', 'National ID'), ('GCC', 'GCC ID'), ('IQA', 'Iqama'), ('PAS', 'Passport'), ('OTH', 'Other ID'))


class Partner(models.Model):
    """One person or company the business deals with – customer, supplier, tenant, owner, student… (roles).
    The master keeps what every app and Accounting need to recognise, contact, invoice and pay the party; each app keeps its own
    details in its own record linked here (CRM contact / account, Property tenant / owner / broker / vendor, …)."""
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField('individual / company', max_length=10, choices=PARTNER_KINDS, default='individual')
    name = models.CharField('display name', max_length=200)
    first_name = models.CharField(max_length=80, blank=True)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80, blank=True)
    arabic_name = models.CharField('name in Arabic', max_length=200, blank=True)
    legal_name = models.CharField('legal name', max_length=200, blank=True)
    company_name = models.CharField('company (text)', max_length=200, blank=True, help_text='Company of an individual without a company record')
    # Odoo: parent_id + type – a person (or address) of a company
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children', verbose_name='company',
                               limit_choices_to={'kind': 'company'})
    contact_type = models.CharField('type', max_length=10, choices=CONTACT_TYPES, default='contact')
    job_title = models.CharField(max_length=80, blank=True)
    # individual
    gender = models.CharField(max_length=6, blank=True, choices=(('', '–'), ('male', 'Male'), ('female', 'Female')))
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    id_type = models.CharField('ID type', max_length=30, blank=True, help_text='Emirates ID, passport, Civil ID, QID, Iqama, Aadhaar… (country pack)')
    id_number = models.CharField('ID number', max_length=60, blank=True)
    id_expiry = models.DateField('ID expiry', null=True, blank=True)
    # company
    tax_id = models.CharField('tax registration no. (TRN / VAT / GSTIN)', max_length=40, blank=True)
    licence_no = models.CharField('trade licence no.', max_length=60, blank=True)
    licence_expiry = models.DateField('trade licence expiry', null=True, blank=True)
    registration_authority = models.CharField(max_length=100, blank=True)
    # contact
    mobile = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    other_phones = models.JSONField(default=list, blank=True, help_text='[{"label": "Home", "number": "+971…"}]')
    website = models.CharField(max_length=150, blank=True)
    address = models.TextField('main address', blank=True)
    city = models.CharField(max_length=80, blank=True)
    country = models.CharField(max_length=60, blank=True)
    addresses = models.JSONField('other addresses', default=list, blank=True, help_text='[{"label": "Site", "address": "…", "country": "AE"}]')
    preferred_language = models.CharField(max_length=5, blank=True, choices=(('', '–'), ('en', 'English'), ('ar', 'Arabic'), ('hi', 'Hindi'), ('ur', 'Urdu'),
                                                                            ('ml', 'Malayalam'), ('tl', 'Filipino'), ('fr', 'French'), ('ru', 'Russian')))
    preferred_channel = models.CharField(max_length=10, blank=True, choices=(('', '–'), ('email', 'E-mail'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp'),
                                                                            ('portal', 'Portal'), ('phone', 'Phone call')))
    # roles and status
    roles = models.JSONField('other roles', default=list, blank=True, help_text='Roles chosen here, e.g. a supplier with no app record yet. The apps add theirs (tenant, customer, owner…) from their own records.')
    roles_text = models.CharField('roles', max_length=200, blank=True)
    apps_text = models.CharField('used in', max_length=200, blank=True)
    status = models.CharField(max_length=10, default='active', choices=(('active', 'Active'), ('blocked', 'Blocked'), ('archived', 'Archived')),
                              help_text='Blocked: no new business in any app')
    blocked_reason = models.CharField(max_length=200, blank=True)
    tags = models.JSONField(default=list, blank=True)
    # commercial (company or individual customer / supplier)
    payment_term = models.ForeignKey(PaymentTerm, null=True, blank=True, on_delete=models.SET_NULL)
    price_book = models.ForeignKey(PriceBook, null=True, blank=True, on_delete=models.SET_NULL)
    currency = models.CharField(max_length=3, blank=True)
    credit_limit = models.DecimalField(null=True, blank=True, **MONEY)
    # v1.44.0 credit control and balances (filled by ZEO from the apps' invoices, receipts and bills)
    credit_hold = models.BooleanField('on credit hold', default=False, help_text='No new business on credit until a manager releases it')
    credit_hold_reason = models.CharField(max_length=200, blank=True)
    balance_receivable = models.DecimalField('receivable', default=0, **MONEY)
    balance_payable = models.DecimalField('payable', default=0, **MONEY)
    balance_overdue = models.DecimalField('overdue', default=0, **MONEY)
    balance_at = models.DateTimeField('balances worked out at', null=True, blank=True)
    # v1.44.0 e-invoicing (ZATCA / UAE PINT AE / Oman Fawtara / India GST) – structured address and ids
    street = models.CharField(max_length=150, blank=True)
    building_no = models.CharField('building no.', max_length=10, blank=True)
    additional_no = models.CharField('additional no.', max_length=10, blank=True, help_text='Saudi national address')
    district = models.CharField('district / area', max_length=80, blank=True)
    postal_code = models.CharField(max_length=12, blank=True)
    state = models.CharField('emirate / state / province', max_length=60, blank=True, help_text='India: the GST state code (e.g. 32 Kerala)')
    country_code = models.CharField('country code', max_length=2, blank=True, help_text='ISO code: AE, SA, OM, QA, KW, IN…')
    einvoice_channel = models.CharField('e-invoices sent by', max_length=7, blank=True, choices=(('', '–'), ('email', 'E-mail (PDF)'), ('peppol', 'Peppol / e-invoicing network'),
                                                                                         ('portal', 'Customer portal'), ('none', 'Not sent')))
    einvoice_id = models.CharField('e-invoice address (endpoint id)', max_length=60, blank=True, help_text='Peppol participant id, usually the tax number (UAE / Oman)')
    einvoice_scheme = models.CharField('endpoint scheme', max_length=10, blank=True, help_text='Peppol scheme id of the endpoint, e.g. 0235 (UAE TIN), 0088 (GLN)')
    buyer_id_type = models.CharField('other buyer id type', max_length=4, blank=True, choices=BUYER_ID_TYPES, help_text='Saudi e-invoices: needed when the buyer has no VAT number')
    buyer_id = models.CharField('other buyer id', max_length=40, blank=True)
    # links
    portal_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='employee (when also an employee)')
    accounting_ref = models.CharField('accounting partner id', max_length=60, blank=True, help_text='Id of this partner in Odoo / Zoho / the ZEO Accounting app')
    external_ids = models.JSONField(default=dict, blank=True)
    merged_into = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    consent = models.BooleanField('consent to keep personal data', default=False)
    consent_on = models.DateField(null=True, blank=True)
    retain_until = models.DateField('keep data until', null=True, blank=True)
    notes = models.TextField(blank=True)
    used_in = models.JSONField('used in apps', default=list, blank=True, help_text='Filled by ZEO: the app records of this partner')
    search_keys = models.CharField(max_length=400, blank=True, db_index=True, help_text='Normalised phone / e-mail / ID / tax no. for the duplicate check')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'business partner'
        verbose_name_plural = 'business partners'

    def __str__(self):
        return self.name

    @property
    def commercial_partner(self):
        """Odoo commercial_partner_id: invoices, balances and the credit limit roll up to the company."""
        return self.parent if (self.parent_id and self.parent.kind == 'company') else self


class PartnerBank(models.Model):
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='banks')
    bank = models.CharField(max_length=120)
    account_name = models.CharField('account holder', max_length=150, blank=True)
    account_no = models.CharField('account no.', max_length=40, blank=True)
    iban = models.CharField('IBAN', max_length=34, blank=True)
    swift = models.CharField('SWIFT / BIC', max_length=11, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_default', 'bank']
        verbose_name = 'bank account'

    def __str__(self):
        return f'{self.bank} {self.iban or self.account_no}'.strip()


class DuplicatePair(models.Model):
    """Two partners that look like the same person / company. A person decides: merge, or not a duplicate."""
    number = models.CharField(max_length=20, unique=True, blank=True)
    partner = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='+', verbose_name='partner')
    other = models.ForeignKey(Partner, on_delete=models.CASCADE, related_name='+', verbose_name='looks like')
    reason = models.CharField(max_length=120, blank=True)
    score = models.PositiveSmallIntegerField(default=0, help_text='100 = same ID / tax no.; 60 = same e-mail or mobile')
    status = models.CharField(max_length=10, default='open', choices=(('open', 'To review'), ('merged', 'Merged'), ('dismissed', 'Not a duplicate')))
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', '-score', '-id']
        verbose_name = 'possible duplicate'
        unique_together = [('partner', 'other')]

