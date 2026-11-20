"""ZEO Purchase (v1.78.0) – procure to pay on the shared suppliers and items: requests (also Inventory's reorder list and requisitions),
requests for quotation to several suppliers with the comparison, purchase orders with approval by amount, receipts through Inventory
(partial, backorders), supplier bills through Accounting with the three-way match (ordered, received, billed) and tolerances, supplier
returns (stock out and a debit note), supplier price lists and supplier performance."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
BRANCH = 'OrganisationManager.brnch_mstr'
PARTNER = 'ZeoMasters.Partner'
PRODUCT = 'ZeoMasters.Product'
MONEY = dict(max_digits=18, decimal_places=3)
QTY = dict(max_digits=16, decimal_places=4)

ROLES = (('pur_manager', 'Purchase manager – suppliers, prices, approvals'),
         ('pur_buyer', 'Buyer – requests for quotation and purchase orders'),
         ('pur_requester', 'Requester – purchase requests'),
         ('pur_ap', 'Purchase billing – receipts, bills, returns'),
         ('pur_viewer', 'Purchase viewer (read only)'))
REQ_STATUS = (('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('ordered', 'Ordered'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))
RFQ_STATUS = (('draft', 'Draft'), ('sent', 'Sent to suppliers'), ('compared', 'Quotes in'), ('awarded', 'Awarded'), ('cancelled', 'Cancelled'))
PO_STATUS = (('draft', 'Draft'), ('approval', 'Waiting for approval'), ('approved', 'Approved'), ('sent', 'Sent to the supplier'), ('part_received', 'Partly received'),
             ('received', 'Received'), ('billed', 'Billed'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))
BILL_POLICY = (('received', 'Bill what was received (three-way match)'), ('ordered', 'Bill what was ordered (services, advances)'))


class PurchaseSettings(AppSettingsBase):
    currency = models.CharField(max_length=3, default='AED')
    bill_policy = models.CharField(max_length=10, choices=BILL_POLICY, default='received')
    qty_tolerance = models.DecimalField('quantity tolerance % (bill vs received)', max_digits=6, decimal_places=2, default=0)
    price_tolerance = models.DecimalField('price tolerance % (bill vs order)', max_digits=6, decimal_places=2, default=2)
    over_receipt = models.DecimalField('receive up to % more than ordered', max_digits=6, decimal_places=2, default=0)
    approve_amount = models.DecimalField('orders above this amount need the purchase manager', null=True, blank=True, **MONEY)
    approve_amount_2 = models.DecimalField('orders above this amount also need the finance manager', null=True, blank=True, **MONEY)
    default_warehouse = models.ForeignKey('ZeoInventory.Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    rfq_min_quotes = models.PositiveSmallIntegerField('quotes needed above the approval amount', default=0)


class PurchaseSequence(SequenceBase):
    pass


class PurchaseEvent(EventBase):
    pass


class PurchaseFile(FileBase):
    pass


class SupplierPrice(PackItem):
    """the supplier's price list: price from a quantity, lead time, the supplier's code; the best price is proposed"""
    supplier = models.ForeignKey(PARTNER, on_delete=models.CASCADE, related_name='+')
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+')
    supplier_code = models.CharField("supplier's code", max_length=60, blank=True)
    min_qty = models.DecimalField('from quantity', default=0, **QTY)
    price = models.DecimalField(default=0, max_digits=18, decimal_places=6)
    currency = models.CharField(max_length=3, blank=True)
    lead_days = models.PositiveSmallIntegerField('lead time (days)', default=0)
    date_from = models.DateField(null=True, blank=True)
    date_to = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['product', 'price']


class Request(models.Model):
    """a purchase request (from a department, Inventory's reorder list or a requisition) – approved, then ordered or sent for quotation"""
    number = models.CharField(max_length=30, blank=True)
    date = models.DateField()
    needed_by = models.DateField(null=True, blank=True)
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    department = models.CharField('for (department, project …)', max_length=120, blank=True)
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    warehouse = models.ForeignKey('ZeoInventory.Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    lines = models.JSONField(default=list, blank=True, help_text='[{product, qty, note}]')
    source = models.CharField(max_length=20, blank=True, help_text='reorder, requisition or empty')
    status = models.CharField(max_length=10, choices=REQ_STATUS, default='draft')
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Draft request'


class Rfq(models.Model):
    number = models.CharField(max_length=30, blank=True)
    date = models.DateField()
    reply_by = models.DateField('quotes by', null=True, blank=True)
    suppliers = models.ManyToManyField(PARTNER, blank=True, related_name='+')
    lines = models.JSONField(default=list, blank=True, help_text='[{product, qty}]')
    request = models.ForeignKey(Request, null=True, blank=True, on_delete=models.SET_NULL, related_name='rfqs')
    status = models.CharField(max_length=10, choices=RFQ_STATUS, default='draft')
    awarded_to = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Draft RFQ'


class Quote(models.Model):
    """a supplier's answer to an RFQ: price per line, lead time, validity"""
    rfq = models.ForeignKey(Rfq, on_delete=models.CASCADE, related_name='quotes')
    supplier = models.ForeignKey(PARTNER, on_delete=models.PROTECT, related_name='+')
    reference = models.CharField('supplier quote no.', max_length=60, blank=True)
    prices = models.JSONField(default=list, blank=True, help_text='[{product, price}] in the RFQ line order')
    currency = models.CharField(max_length=3, blank=True)
    lead_days = models.PositiveSmallIntegerField('delivery (days)', default=0)
    valid_until = models.DateField(null=True, blank=True)
    total = models.DecimalField(default=0, **MONEY)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['total']


class Order(models.Model):
    number = models.CharField(max_length=30, blank=True, db_index=True)
    supplier = models.ForeignKey(PARTNER, on_delete=models.PROTECT, related_name='+')
    date = models.DateField()
    expected_date = models.DateField('expected delivery', null=True, blank=True)
    buyer = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    warehouse = models.ForeignKey('ZeoInventory.Warehouse', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    payment_term = models.ForeignKey('ZeoMasters.PaymentTerm', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    currency = models.CharField(max_length=3, blank=True)
    supplier_ref = models.CharField('supplier quote / reference', max_length=60, blank=True)
    bill_policy = models.CharField(max_length=10, choices=BILL_POLICY, blank=True, help_text='Empty: the set-up')
    rfq = models.ForeignKey(Rfq, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    request = models.ForeignKey(Request, null=True, blank=True, on_delete=models.SET_NULL, related_name='orders')
    status = models.CharField(max_length=14, choices=PO_STATUS, default='draft')
    approval_note = models.CharField(max_length=255, blank=True)
    approvals = models.JSONField(default=list, blank=True, help_text='[{level, by, at}]')
    net = models.DecimalField('net (before VAT)', default=0, **MONEY)
    vat = models.DecimalField('VAT', default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    billed = models.DecimalField(default=0, **MONEY)
    notes = models.TextField('notes to the supplier', blank=True)
    terms = models.TextField('terms and conditions', blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Draft PO'


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(PRODUCT, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    description = models.CharField(max_length=255, blank=True)
    qty = models.DecimalField(default=1, **QTY)
    price = models.DecimalField(default=0, max_digits=18, decimal_places=6)
    discount = models.DecimalField('discount %', default=0, max_digits=6, decimal_places=3)
    tax_code = models.CharField('VAT code', max_length=15, blank=True, help_text='Accounting tax code (SR-P, RC, IM …)')
    net = models.DecimalField('net (before VAT)', default=0, **MONEY)
    vat = models.DecimalField('VAT', default=0, **MONEY)
    received = models.DecimalField(default=0, **QTY)
    billed_qty = models.DecimalField('billed', default=0, **QTY)
    returned = models.DecimalField(default=0, **QTY)
    is_service = models.BooleanField(default=False)
    account_code = models.CharField('expense account (services)', max_length=20, blank=True)

    class Meta:
        ordering = ['order', 'id']


class Receipt(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='receipts')
    date = models.DateField()
    stock_doc_id = models.IntegerField(null=True, blank=True)
    stock_number = models.CharField(max_length=30, blank=True)
    delivery_note = models.CharField("supplier's delivery note", max_length=60, blank=True)
    lines = models.JSONField(default=list, blank=True)
    value = models.DecimalField(default=0, **MONEY)
    on_time = models.BooleanField(default=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class Bill(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='bills')
    kind = models.CharField(max_length=8, choices=(('bill', 'Bill'), ('debit', 'Debit note')), default='bill')
    date = models.DateField()
    supplier_ref = models.CharField('supplier invoice no.', max_length=60, blank=True)
    doc_id = models.IntegerField(null=True, blank=True)
    doc_number = models.CharField(max_length=30, blank=True)
    total = models.DecimalField(default=0, **MONEY)
    lines = models.JSONField(default=list, blank=True)
    match = models.JSONField(default=list, blank=True, help_text='the three-way match per line')

    class Meta:
        ordering = ['-date', '-id']


class Return(models.Model):
    number = models.CharField(max_length=30, blank=True)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='returns')
    date = models.DateField()
    reason = models.CharField(max_length=120, blank=True)
    lines = models.JSONField(default=list, blank=True, help_text='[{line, qty}]')
    debit_note = models.BooleanField(default=True)
    status = models.CharField(max_length=10, choices=(('draft', 'Draft'), ('done', 'Done')), default='draft')
    stock_number = models.CharField(max_length=30, blank=True)
    debit_number = models.CharField(max_length=30, blank=True)
    total = models.DecimalField(default=0, **MONEY)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Draft return'
