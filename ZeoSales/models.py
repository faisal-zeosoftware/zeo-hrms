"""ZEO Sales (v1.77.0) – order to cash on the shared masters: quotations and sales orders (from CRM too), prices from the price books
and discount rules, credit control, deliveries through Inventory (partial, backorders), invoices through Accounting (by order or by
delivery, down payments), returns (stock back and a credit note), commissions of the salespeople."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
BRANCH = 'OrganisationManager.brnch_mstr'
PARTNER = 'ZeoMasters.Partner'
PRODUCT = 'ZeoMasters.Product'
EMP = 'EmpManagement.emp_master'
MONEY = dict(max_digits=18, decimal_places=3)
QTY = dict(max_digits=16, decimal_places=4)

ROLES = (('sal_manager', 'Sales manager – prices, discounts, credit, approvals'),
         ('sal_user', 'Salesperson – quotations and orders'),
         ('sal_billing', 'Sales billing – deliveries, invoices, returns'),
         ('sal_viewer', 'Sales viewer (read only)'))
STATUS = (('quote', 'Quotation'), ('sent', 'Quotation sent'), ('approval', 'Waiting for approval'), ('confirmed', 'Confirmed'), ('hold', 'On credit hold'),
          ('part_delivered', 'Partly delivered'), ('delivered', 'Delivered'), ('invoiced', 'Invoiced'), ('closed', 'Closed'), ('cancelled', 'Cancelled'),
          ('lost', 'Lost'))
INVOICE_POLICY = (('delivered', 'Invoice what was delivered'), ('ordered', 'Invoice what was ordered (services, advance billing)'))
CREDIT = (('off', 'No credit control'), ('warn', 'Warn when over the limit or overdue'), ('block', 'Hold the order when over the limit or overdue'))
RULE_KINDS = (('percent', 'Discount %'), ('price', 'Fixed price'), ('free', 'Buy X get Y free'))
RETURN_STATUS = (('draft', 'Draft'), ('done', 'Done'), ('cancelled', 'Cancelled'))


class SalesSettings(AppSettingsBase):
    currency = models.CharField(max_length=3, default='AED')
    invoice_policy = models.CharField(max_length=10, choices=INVOICE_POLICY, default='delivered')
    credit_control = models.CharField(max_length=6, choices=CREDIT, default='warn')
    overdue_days = models.PositiveSmallIntegerField('hold when an invoice is overdue by more than (days)', default=30)
    quote_days = models.PositiveSmallIntegerField('quotation valid for (days)', default=30)
    approve_discount = models.DecimalField('discount above this % needs the sales manager', max_digits=6, decimal_places=2, null=True, blank=True)
    approve_amount = models.DecimalField('orders above this amount need the sales manager', null=True, blank=True, **MONEY)
    default_warehouse = models.ForeignKey('ZeoInventory.Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    commission_on = models.CharField('commission when', max_length=8, choices=(('invoiced', 'Invoiced'), ('paid', 'Paid')), default='paid')
    min_margin = models.DecimalField('warn below this margin %', max_digits=6, decimal_places=2, null=True, blank=True)


class SalesSequence(SequenceBase):
    pass


class SalesEvent(EventBase):
    pass


class SalesFile(FileBase):
    pass


class Team(PackItem):
    name = models.CharField(max_length=80)
    leader = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    members = models.ManyToManyField(EMP, blank=True, related_name='+')
    commission = models.DecimalField('commission % (default)', max_digits=6, decimal_places=2, default=0)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class DiscountRule(PackItem):
    """price rules (Odoo pricelist rules / Sage 300 sales price by quantity): by item, category or every item; partner or partner tag; quantity; dates"""
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=8, choices=RULE_KINDS, default='percent')
    product = models.ForeignKey(PRODUCT, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    category = models.ForeignKey('ZeoMasters.ProductCategory', null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    min_qty = models.DecimalField('from quantity', default=0, **QTY)
    value = models.DecimalField('discount % / price', default=0, max_digits=18, decimal_places=6)
    free_qty = models.DecimalField('free quantity (buy X get Y)', default=0, **QTY)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)
    priority = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ['priority', 'name']

    def __str__(self):
        return self.name


class Order(models.Model):
    """a quotation becomes the sales order when it is confirmed (one record, as Odoo)"""
    number = models.CharField(max_length=30, blank=True, db_index=True)
    quote_number = models.CharField(max_length=30, blank=True)
    partner = models.ForeignKey(PARTNER, on_delete=models.PROTECT, related_name='+', verbose_name='customer')
    date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    delivery_date = models.DateField('promised delivery', null=True, blank=True)
    salesperson = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    warehouse = models.ForeignKey('ZeoInventory.Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    price_book = models.ForeignKey('ZeoMasters.PriceBook', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    payment_term = models.ForeignKey('ZeoMasters.PaymentTerm', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    currency = models.CharField(max_length=3, blank=True)
    emirate = models.CharField('place of supply (emirate)', max_length=2, blank=True)
    reference = models.CharField('customer PO', max_length=60, blank=True)
    invoice_policy = models.CharField(max_length=10, choices=INVOICE_POLICY, blank=True, help_text='Empty: the set-up')
    status = models.CharField(max_length=16, choices=STATUS, default='quote')
    hold_reason = models.CharField(max_length=255, blank=True)
    net = models.DecimalField('net (before VAT)', default=0, **MONEY)
    vat = models.DecimalField('VAT', default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    cost = models.DecimalField('cost of the goods', default=0, **MONEY)
    margin = models.DecimalField(default=0, **MONEY)
    invoiced = models.DecimalField(default=0, **MONEY)
    down_payment = models.DecimalField('down payment invoiced', default=0, **MONEY)
    crm_quote = models.ForeignKey('ZeoCRM.Quotation', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    crm_order = models.ForeignKey('ZeoCRM.SalesOrder', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    lost_reason = models.CharField(max_length=120, blank=True)
    notes = models.TextField('notes to the customer', blank=True)
    terms = models.TextField('terms and conditions', blank=True)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or self.quote_number or 'Draft'


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(PRODUCT, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=255, blank=True)
    qty = models.DecimalField(default=1, **QTY)
    price = models.DecimalField(default=0, max_digits=18, decimal_places=6)
    discount = models.DecimalField('discount %', default=0, max_digits=6, decimal_places=3)
    rule = models.ForeignKey(DiscountRule, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='price rule applied')
    tax_code = models.CharField('VAT code', max_length=15, blank=True, help_text='Accounting tax code (SR, ZR, EX …)')
    net = models.DecimalField('net (before VAT)', default=0, **MONEY)
    vat = models.DecimalField('VAT', default=0, **MONEY)
    unit_cost = models.DecimalField(default=0, max_digits=18, decimal_places=6)
    delivered = models.DecimalField('delivered', default=0, **QTY)
    invoiced_qty = models.DecimalField('invoiced', default=0, **QTY)
    returned = models.DecimalField('returned', default=0, **QTY)
    is_service = models.BooleanField(default=False)

    class Meta:
        ordering = ['order', 'id']


class Delivery(models.Model):
    """one delivery of an order: the Inventory delivery document it posted"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='deliveries')
    date = models.DateField()
    stock_doc_id = models.IntegerField(null=True, blank=True)
    stock_number = models.CharField(max_length=30, blank=True)
    lines = models.JSONField(default=list, blank=True, help_text='[{line, qty}]')
    value = models.DecimalField('cost of the goods', default=0, **MONEY)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class Invoice(models.Model):
    """an Accounting invoice made from an order (the document lives in Accounting)"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='invoices')
    kind = models.CharField(max_length=10, choices=(('invoice', 'Invoice'), ('down', 'Down payment'), ('credit', 'Credit note')), default='invoice')
    date = models.DateField()
    doc_id = models.IntegerField(null=True, blank=True)
    doc_number = models.CharField(max_length=30, blank=True)
    total = models.DecimalField(default=0, **MONEY)
    lines = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-date', '-id']


class Return(models.Model):
    number = models.CharField(max_length=30, blank=True)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='returns')
    date = models.DateField()
    reason = models.CharField(max_length=120, blank=True)
    lines = models.JSONField(default=list, blank=True, help_text='[{line, qty}]')
    refund = models.BooleanField('credit note', default=True)
    restock = models.BooleanField('back to stock', default=True)
    status = models.CharField(max_length=10, choices=RETURN_STATUS, default='draft')
    stock_number = models.CharField(max_length=30, blank=True)
    credit_number = models.CharField(max_length=30, blank=True)
    total = models.DecimalField(default=0, **MONEY)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Draft return'


class Commission(models.Model):
    salesperson = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='+')
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='commissions')
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    date = models.DateField()
    base = models.DecimalField(default=0, **MONEY)
    percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    amount = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=10, choices=(('earned', 'Earned'), ('waiting', 'Waiting for payment'), ('paid', 'Paid out')), default='waiting')

    class Meta:
        ordering = ['-date', '-id']
