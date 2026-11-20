"""ZEO Inventory (v1.75.0) – stock on the shared item master (ZeoMasters.Product) and business partners.

The ledger follows Microsoft Business Central (every quantity move and every cost amount kept, never edited: StockMove, StockValue,
CostLayer, CostApplication – exact valuation at any past date), costs per item and costing key like Sage 300 (company, branch or
warehouse level – SAP's choice of the valuation level), with the costing methods average, FIFO, LIFO, standard, lot and serial.
Perpetual (a journal for every value event) or periodic (the month-end closing stock JV) accounting; landed cost on stock still
held and on stock already sold; back-dated moves corrected by the cost adjustment; period close; Inventory ↔ GL reconciliation."""
from decimal import Decimal

from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
BRANCH = 'OrganisationManager.brnch_mstr'
PRODUCT = 'ZeoMasters.Product'
PARTNER = 'ZeoMasters.Partner'
MONEY = dict(max_digits=18, decimal_places=3)   # 3 places: BHD, KWD, OMR (the company's precision rounds it)
QTY = dict(max_digits=16, decimal_places=4)
COST = dict(max_digits=18, decimal_places=6)
BRANCH_HELP = 'Branch of the record (empty: every branch)'

ROLES = (('inv_manager', 'Inventory manager – set-up, costing, period close'),
         ('inv_storekeeper', 'Storekeeper – receipts, deliveries, transfers, counts'),
         ('inv_accountant', 'Inventory accountant – valuation, landed cost, closing stock, reconciliation'),
         ('inv_viewer', 'Inventory viewer (read only)'))

MODES = (('perpetual', 'Perpetual – a journal entry for every movement that changes stock value'),
         ('periodic', 'Periodic – no stock journals; the month-end closing stock JV'))
TIMING = (('immediate', 'At once'), ('day_end', 'Day-end batch'), ('on_request', 'On request'))
LEVELS = (('company', 'Company (one cost per item)'), ('branch', 'Branch (a cost per item and branch)'),
          ('warehouse', 'Warehouse (a cost per item and warehouse)'))
METHODS = (('average', 'Moving average'), ('fifo', 'FIFO – first in, first out'), ('lifo', 'LIFO – last in, first out (not IFRS)'),
           ('standard', 'Standard cost'), ('lot', 'Lot – each lot its own cost'), ('serial', 'Serial / specific – each unit its own cost'))
REMOVAL = (('', 'The warehouse\'s strategy'), ('fefo', 'FEFO – first expiry first out (then FIFO)'), ('fifo', 'FIFO – first in, first out'),
           ('lifo', 'LIFO – last in, first out'))
TRACK = (('none', 'No lots or serial numbers'), ('lot', 'Lots (batch, expiry)'), ('serial', 'Serial numbers'))

# account roles of the account determination (the default code of each – a company changes them in the set-up)
ACCOUNT_ROLES = (
    ('inventory', 'Inventory', '1400'), ('grni', 'Goods received not invoiced (GRNI)', '2150'), ('cogs', 'Cost of goods sold', '5000'),
    ('adjustment', 'Stock adjustment', '5810'), ('count_variance', 'Count variance', '5820'), ('scrap', 'Scrap and write-off', '5830'),
    ('transit', 'Stock in transit', '1410'), ('ppv', 'Purchase price variance', '5840'), ('revaluation', 'Inventory revaluation', '5850'),
    ('landed_clearing', 'Landed cost clearing', '2160'), ('consumption', 'Internal consumption', '5870'), ('opening', 'Opening balance (equity)', '3900'),
    ('purchases', 'Purchases (periodic)', '5100'), ('inventory_change', 'Change in inventory (periodic)', '5110'),
    ('write_down', 'Inventory write-down (NRV)', '5860'), ('provision', 'Inventory provision', '1490'),
    ('landed_accrual', 'Landed cost accrual (estimates)', '2165'),
    ('consign_payable', 'Consignment payable (supplier stock used)', '2170'), ('payable', 'Accounts payable', '2100'),
    ('revenue', 'Sales revenue', '4000'), ('sales_returns', 'Sales returns', '4100'), ('cost_variance', 'Cost variance', '5845'),
    ('shipment_clearing', 'Shipment clearing', '2155'),
)
DEFAULT_ACCOUNTS = {k: c for k, _, c in ACCOUNT_ROLES}
# v1.75.0 as Sage 300 I/C: the account set (one per item) holds the inventory-side accounts; the item category holds the sales side
CATEGORY_ROLES = ('revenue', 'sales_returns', 'cogs', 'cost_variance', 'scrap', 'consumption')
SET_ROLES = tuple(k for k, _, _ in ACCOUNT_ROLES if k not in CATEGORY_ROLES)
# production account sets (Manufacturing 1.79 – kept here so every GL mapping is on one screen)
PROD_ROLES = (
    ('wip', 'Work in progress', '1450'), ('mat_usage_var', 'Material usage variance', '5910'), ('mat_price_var', 'Material price variance', '5911'),
    ('labour_eff_var', 'Labour efficiency variance', '5920'), ('labour_rate_var', 'Labour rate variance', '5921'), ('machine_var', 'Machine variance', '5930'),
    ('overhead_spend_var', 'Overhead spending variance', '5940'), ('overhead_vol_var', 'Overhead volume variance', '5941'), ('prod_scrap', 'Production scrap', '5950'),
    ('yield_var', 'Yield variance', '5951'), ('subcontract_var', 'Sub-contract variance', '5960'), ('rework_var', 'Rework variance', '5961'),
    ('coproduct_diff', 'Co-product split difference', '5970'), ('disassembly_diff', 'Disassembly difference', '5971'),
)
DEFAULT_ACCOUNTS.update({k: c for k, _, c in PROD_ROLES})

DOC_KINDS = (('receipt', 'Goods receipt'), ('delivery', 'Delivery'), ('transfer', 'Transfer'), ('adjustment', 'Stock adjustment'),
             ('count', 'Stock count'), ('scrap', 'Scrap / write-off'), ('consumption', 'Internal consumption'),
             ('return_in', 'Customer return'), ('return_out', 'Return to supplier'), ('opening', 'Opening stock'),
             ('dropship', 'Drop ship (supplier → customer)'))
DELIVERY_STATUS = (('', '—'), ('ready', 'Ready'), ('out', 'Out for delivery'), ('delivered', 'Delivered'), ('partly', 'Partly delivered'), ('refused', 'Refused'))
SHIP_METHODS = (('', '—'), ('own', 'Own vehicle'), ('courier', 'Courier'), ('collect', 'Customer collects'))
DOC_STATUS = (('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled'), ('reversed', 'Reversed'))
NEG = (('block', 'Block – not below zero'), ('warn', 'Warn – post with a warning'), ('allow', 'Allow'))
ADJ_TYPES = (('qty_in', 'Quantity increase'), ('qty_out', 'Quantity decrease'), ('cost_in', 'Cost increase'), ('cost_out', 'Cost decrease'),
             ('both_in', 'Both increase'), ('both_out', 'Both decrease'))
VALUE_KINDS = (('direct', 'Direct cost'), ('landed', 'Landed cost'), ('revaluation', 'Revaluation'), ('variance', 'Standard cost variance'),
               ('adjustment', 'Cost adjustment'), ('rounding', 'Rounding'), ('closing', 'Closing stock (periodic)'), ('transfer', 'Account set change'))


class InvSettings(AppSettingsBase):
    currency = models.CharField(max_length=3, default='AED')
    accounting_mode = models.CharField('inventory accounting', max_length=10, choices=MODES, default='perpetual')
    posting_timing = models.CharField('journal timing (perpetual)', max_length=10, choices=TIMING, default='immediate')
    costing_level = models.CharField('costing level', max_length=10, choices=LEVELS, default='company')
    default_method = models.CharField('default costing method', max_length=10, choices=METHODS, default='average')
    allow_lifo = models.BooleanField('offer LIFO (not allowed under IFRS – management reports only)', default=False)
    lock_date = models.DateField('stock period closed up to', null=True, blank=True)
    negative = models.CharField('negative stock (company)', max_length=6, choices=NEG, default='block',
                                help_text='A warehouse or an item may have its own; lots and serial numbers are never negative')
    soft_close_date = models.DateField('soft-closed up to', null=True, blank=True, help_text='Only the inventory manager posts on or before it (with a warning)')
    backdate_days = models.PositiveIntegerField('back-dated beyond (days) needs the inventory manager', null=True, blank=True)
    provision_matrix = models.JSONField('provision matrix (IFRS)', default=list, blank=True,
                                        help_text='[[days held over, % provided], …] e.g. [[180, 25], [365, 100]]; expired lots 100 %')
    adj_limit = models.DecimalField('adjustments above this value need the inventory manager', null=True, blank=True, max_digits=16, decimal_places=3)
    adj_reason = models.BooleanField('a reason on every adjustment', default=True)
    accounts = models.JSONField(default=dict, blank=True, help_text='account code per role – empty roles take the default codes')
    default_set = models.ForeignKey('AccountSet', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='default account set')
    ageing_buckets = models.CharField('ageing buckets (days)', max_length=60, default='30,60,90,180')
    consign_age_days = models.PositiveSmallIntegerField('consignment stock old after (days)', default=180)
    # decimal precision (Odoo "decimal accuracy"): what is stored, shown and posted is rounded to it
    dp_amount = models.PositiveSmallIntegerField('decimals – amounts', null=True, blank=True, help_text="Empty: the company's currency (AED 2, KWD / BHD / OMR 3)")
    dp_cost = models.PositiveSmallIntegerField('decimals – unit cost / price', default=4)
    dp_qty = models.PositiveSmallIntegerField('decimals – quantity', default=3)
    dp_weight = models.PositiveSmallIntegerField('decimals – weight and volume', default=3)
    dp_percent = models.PositiveSmallIntegerField('decimals – percentages', default=3)

    class Meta:
        permissions = [(k, f'Inventory role: {v}') for k, v in ROLES]
        verbose_name = 'Inventory settings'


class InvSequence(SequenceBase):
    pass


class InvEvent(EventBase):
    class Meta(EventBase.Meta):
        pass


class InvFile(FileBase):
    pass


class Reason(PackItem):
    """Why stock was adjusted, scrapped or consumed (pack item) – and the account role it posts to."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=12, choices=(('adjustment', 'Adjustment'), ('scrap', 'Scrap / write-off'), ('consumption', 'Consumption')),
                            default='adjustment')
    account_role = models.CharField(max_length=20, default='adjustment', choices=[(k, n) for k, n, _ in ACCOUNT_ROLES])

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Warehouse(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    code = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=120)
    shared_location = models.ForeignKey('ZeoMasters.Location', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                        help_text='The store in the shared list of locations (made by ZEO)')
    allow_negative = models.BooleanField('allow negative stock', default=False)
    negative = models.CharField('negative stock', max_length=6, choices=NEG, blank=True, help_text='Empty: the company policy (set-up)')
    removal = models.CharField('removal strategy', max_length=6, choices=REMOVAL[1:], default='fefo',
                               help_text='Which lot and bin a delivery takes when the line does not say')
    tags = models.ManyToManyField('Tag', blank=True, related_name='warehouses')
    users = models.ManyToManyField(USER, blank=True, related_name='+', help_text='Storekeepers of this warehouse only (empty: every Inventory user)')
    is_transit = models.BooleanField('transit warehouse', default=False)
    kind = models.CharField('warehouse type', max_length=10, default='internal',
                            choices=(('internal', 'Our warehouse'), ('customer', 'Consignment at a customer')))
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='customer (consignment)')
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'.strip()


class Bin(models.Model):
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='bins')
    code = models.CharField(max_length=30, blank=True, help_text='Empty: made from zone-aisle-rack-shelf')
    name = models.CharField(max_length=80, blank=True)
    zone = models.CharField(max_length=20, blank=True)
    aisle = models.CharField(max_length=20, blank=True)
    rack = models.CharField(max_length=20, blank=True)
    shelf = models.CharField('shelf / level', max_length=20, blank=True)
    kind = models.CharField('bin type', max_length=12, default='storage',
                            choices=(('storage', 'Storage'), ('picking', 'Picking'), ('receiving', 'Receiving'), ('dispatch', 'Dispatch'),
                                     ('quarantine', 'Quarantine'), ('damaged', 'Damaged goods')))
    is_active = models.BooleanField(default=True)

    BRANCH_FROM = ('warehouse',)

    class Meta:
        ordering = ['warehouse__code', 'code']
        unique_together = [('warehouse', 'code')]

    def __str__(self):
        return f'{self.warehouse.code}/{self.code}'


PUTAWAY = (('fixed', 'Fixed bin'), ('same', 'Same bin as the stock already there'), ('empty', 'An empty bin (zone / bin type)'),
           ('receiving', 'The receiving bin'))


class PutawayRule(models.Model):
    """Where goods coming in are put: the first rule (by priority) that fits the item gives the bin of a line left without one."""
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='putaway_rules')
    priority = models.PositiveSmallIntegerField(default=10, help_text='Lower first')
    product = models.ForeignKey(PRODUCT, null=True, blank=True, on_delete=models.CASCADE, related_name='+', verbose_name='item',
                                help_text='Empty: every item (of the category)')
    category = models.ForeignKey('ZeoMasters.ProductCategory', null=True, blank=True, on_delete=models.CASCADE, related_name='+', verbose_name='item category')
    method = models.CharField(max_length=10, choices=PUTAWAY, default='fixed')
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.CASCADE, related_name='+', help_text='Fixed bin / receiving bin')
    zone = models.CharField(max_length=20, blank=True, help_text='Empty bin: only in this zone')
    bin_kind = models.CharField('bin type', max_length=12, blank=True, help_text='Empty bin: only of this type')
    is_active = models.BooleanField(default=True)

    BRANCH_FROM = ('warehouse',)

    class Meta:
        ordering = ['warehouse__code', 'priority', 'id']

    def __str__(self):
        return f'{self.warehouse.code} #{self.priority} {self.get_method_display()}'


class UnitSetting(models.Model):
    """Inventory's rounding of a unit of measure of the shared list (Masters › Units of measure): PCS 1, KG 0.001 …"""
    uom = models.OneToOneField('ZeoMasters.UOM', on_delete=models.CASCADE, related_name='inv_setting', verbose_name='unit')
    rounding = models.DecimalField('rounding step', max_digits=12, decimal_places=6, default=Decimal('0.001'),
                                   help_text='Quantities of this unit are multiples of it: 1 = whole units, 0.001 = grams of a kg')

    class Meta:
        ordering = ['uom__name']

    def __str__(self):
        return f'{self.uom} ({self.rounding.normalize():f})'


class ItemUnit(models.Model):
    """Another unit an item is bought, sold or moved in, with the item's own conversion (1 CTN = 24 PCS of this item)."""
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='inv_units', verbose_name='item')
    uom = models.ForeignKey('ZeoMasters.UOM', on_delete=models.PROTECT, related_name='+', verbose_name='unit')
    factor = models.DecimalField('stock units in one', max_digits=18, decimal_places=6, help_text="How many of the item's stock unit make one of this unit")
    is_purchase = models.BooleanField('purchase unit', default=False)
    is_sales = models.BooleanField('sales / issue unit', default=False)
    level = models.CharField('packaging level', max_length=8, blank=True, choices=(('', 'Unit'), ('inner', 'Inner'), ('carton', 'Carton'), ('pallet', 'Pallet')))
    length_cm = models.DecimalField('length (cm)', null=True, blank=True, max_digits=10, decimal_places=2)
    width_cm = models.DecimalField('width (cm)', null=True, blank=True, max_digits=10, decimal_places=2)
    height_cm = models.DecimalField('height (cm)', null=True, blank=True, max_digits=10, decimal_places=2)
    gross_kg = models.DecimalField('gross weight (kg)', null=True, blank=True, max_digits=12, decimal_places=3)
    net_kg = models.DecimalField('net weight (kg)', null=True, blank=True, max_digits=12, decimal_places=3)
    full_only = models.CharField('full packages', max_length=6, blank=True, choices=(('', 'Any quantity'), ('warn', 'Warn when not full'), ('round', 'Round up to full')))
    barcode = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['product__name', 'factor']
        unique_together = [('product', 'uom')]

    def __str__(self):
        return f'1 {self.uom.code} = {self.factor.normalize():f}'


class ItemSetting(models.Model):
    """The Inventory settings of an item of the shared item master."""
    product = models.OneToOneField(PRODUCT, on_delete=models.CASCADE, related_name='inv_setting')
    method = models.CharField('costing method', max_length=10, choices=(('', 'Company default'),) + METHODS, blank=True, default='')
    tracking = models.CharField(max_length=8, choices=TRACK, default='none')
    removal = models.CharField('removal strategy', max_length=6, choices=REMOVAL, blank=True, default='')
    consign_first = models.BooleanField('use supplier consignment stock first', default=False,
                                        help_text='Off: own stock first, consignment stock when own stock runs out')
    catch_weight = models.BooleanField('catch weight – counted in units, priced by kg', default=False,
                                       help_text='Receipts are priced per kg of the actual weight; every line records the weight')
    standard_cost = models.DecimalField(null=True, blank=True, **COST)
    account_set = models.ForeignKey('AccountSet', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
                                    help_text="Empty: the item category's set, else the company default set")
    negative = models.CharField('negative stock', max_length=6, choices=NEG, blank=True, help_text='Empty: the warehouse / company policy')
    returnable = models.BooleanField('returnable packaging (pallets, crates, cylinders)', default=False)
    lot_prefix = models.CharField('lot / serial numbering prefix', max_length=12, blank=True, help_text='A receipt line without a lot gets PREFIX-YYMMDD-001 …')
    min_life_days = models.PositiveIntegerField('minimum remaining shelf life to deliver (days)', null=True, blank=True)
    deposit = models.DecimalField('deposit per unit', null=True, blank=True, max_digits=14, decimal_places=3)
    tags = models.ManyToManyField('Tag', blank=True, related_name='items')
    production_set = models.ForeignKey('AccountSet', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
                                       verbose_name='production account set (made items)')
    min_qty = models.DecimalField('reorder point', default=0, **QTY)
    reorder_qty = models.DecimalField('reorder quantity', default=0, **QTY)
    shelf_life_days = models.PositiveIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['product__name']

    def __str__(self):
        return str(self.product)


class StandardCost(models.Model):
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+')
    cost = models.DecimalField(**COST)
    effective_from = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['product', '-effective_from']
        unique_together = [('product', 'effective_from')]


class AccountSet(models.Model):
    """v1.75.0 Sage 300 account set: a code (TRADE, SPARES, RAW, FG …) holding the inventory-side accounts; every stock item has one (its own,
    else its category's, else the company default). Type "production" (Manufacturing): WIP and the variances. A set × warehouse row
    (warehouse given) overrides accounts for that warehouse. Rows of 1.75.0 drafts with a category / warehouse only are still read."""
    code = models.CharField(max_length=20, blank=True, db_index=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=(('item', 'Item (inventory)'), ('production', 'Production (manufacturing)')), default='item')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='locations',
                               verbose_name='account set (for a warehouse override)')
    default_method = models.CharField('costing method of new items of this set', max_length=10, choices=METHODS, blank=True)
    category = models.ForeignKey('ZeoMasters.ProductCategory', null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    accounts = models.JSONField(default=dict, blank=True, help_text='{role: account code} – the roles not given come from the next set / the company')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class OperationType(models.Model):
    """Odoo operation types: Receipts, Deliveries, Internal transfers … per warehouse – own number series, default bins, return type."""
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='operations')
    name = models.CharField(max_length=80)
    kind = models.CharField('type of operation', max_length=12, choices=DOC_KINDS)
    code = models.CharField('number prefix', max_length=20, blank=True, help_text='e.g. ZTA-IN (numbers ZTA-IN-00001)')
    sequence = models.PositiveSmallIntegerField(default=10)
    default_bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='default bin')
    to_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='default "to" warehouse')
    default_to_bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='default "to" bin')
    return_type = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='returns through')
    reason = models.ForeignKey(Reason, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='default reason')
    needs_lots = models.BooleanField('ask for lots / serial numbers on every line', default=False)
    is_active = models.BooleanField(default=True)

    BRANCH_FROM = ('warehouse',)

    class Meta:
        ordering = ['warehouse__code', 'sequence', 'id']

    def __str__(self):
        return f'{self.warehouse.code}: {self.name}'


class StockDoc(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=24, blank=True, db_index=True)
    kind = models.CharField(max_length=12, choices=DOC_KINDS, blank=True, help_text='Filled from the operation type')
    date = models.DateField('posting date')
    document_date = models.DateField(null=True, blank=True)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+', blank=True, help_text='Filled from the operation type')
    to_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='to warehouse')
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='customer / supplier')
    operation = models.ForeignKey(OperationType, null=True, blank=True, on_delete=models.PROTECT, related_name='docs', verbose_name='operation type')
    reason = models.ForeignKey(Reason, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    reference = models.CharField('reference (PO, SO, DN, invoice …)', max_length=80, blank=True)
    charge_to = models.CharField('charged to (project, department, asset …)', max_length=120, blank=True)
    return_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='returns', verbose_name='return of')
    status = models.CharField(max_length=10, choices=DOC_STATUS, default='draft')
    note = models.TextField(blank=True)
    total_qty = models.DecimalField(default=0, **QTY)
    total_value = models.DecimalField(default=0, **MONEY)
    origin_app = models.CharField(max_length=20, blank=True, help_text='The app that made it (sales, purchase, manufacturing …)')
    origin_ref = models.CharField(max_length=60, blank=True)
    reversal_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='reversals')
    # v1.75.0 delivery to the customer's location and drop shipping
    supplier = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='supplier (drop ship)')
    two_step = models.BooleanField('two steps (ship, then receive)', default=False, help_text='Transfers: the goods are in transit until the other warehouse receives them')
    expected_date = models.DateField('expected arrival', null=True, blank=True)
    transit_status = models.CharField(max_length=10, blank=True, choices=(('', '—'), ('transit', 'In transit'), ('partly', 'Partly received'),
                                                                         ('received', 'Received'), ('closed', 'Closed short')))
    shipment_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='arrivals', verbose_name='arrival of the shipment')
    currency = models.CharField(max_length=3, blank=True, help_text='Empty: the company currency; the unit costs are in this currency')
    rate = models.DecimalField('exchange rate', max_digits=16, decimal_places=8, null=True, blank=True, help_text='Company currency per 1 unit of the document currency')
    ship_to = models.TextField('ship-to address', blank=True)
    site_contact = models.CharField(max_length=80, blank=True)
    site_phone = models.CharField(max_length=30, blank=True)
    requested_date = models.DateField('requested delivery date', null=True, blank=True)
    ship_method = models.CharField('delivery method', max_length=8, choices=SHIP_METHODS, blank=True)
    driver = models.CharField(max_length=80, blank=True)
    vehicle = models.CharField(max_length=40, blank=True)
    delivery_status = models.CharField(max_length=10, choices=DELIVERY_STATUS, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    pod_name = models.CharField('received by', max_length=80, blank=True)
    pod_signature = models.TextField('signature (image data)', blank=True)
    pod_photo = models.TextField('photo (image data)', blank=True)
    pod_gps = models.CharField('GPS', max_length=60, blank=True)
    tags = models.ManyToManyField('Tag', blank=True, related_name='docs')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    posted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    posted_at = models.DateTimeField(null=True, blank=True)

    BRANCH_FROM = ('warehouse',)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.number} {self.get_kind_display()}'


class StockDocLine(models.Model):
    """quantity, unit cost and counted are in the line's unit; base_* in the item's stock unit (what the ledger uses)"""
    doc = models.ForeignKey(StockDoc, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+', verbose_name='item')
    uom = models.ForeignKey('ZeoMasters.UOM', null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='unit',
                            help_text="Empty: the item's stock unit")
    owner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='owner (supplier consignment)',
                              help_text='Receipts / returns of goods the supplier still owns: counted, not valued')
    adj_type = models.CharField('adjustment type', max_length=10, blank=True, choices=ADJ_TYPES,
                                help_text='Stock adjustments (as Sage 300): quantity, cost or both – increase or decrease')
    qty = models.DecimalField('quantity', default=0, **QTY)
    unit_cost = models.DecimalField('unit cost (receipts, opening, adjustments in)', null=True, blank=True, **COST)
    cost_amount = models.DecimalField('cost amount (cost / both adjustments)', null=True, blank=True, **MONEY)
    counted_qty = models.DecimalField('counted (stock count)', null=True, blank=True, **QTY)
    expected_qty = models.DecimalField('expected (stock count)', null=True, blank=True, **QTY)
    factor = models.DecimalField('stock units in one', default=1, max_digits=18, decimal_places=6)
    base_qty = models.DecimalField('quantity in the stock unit', default=0, **QTY)
    base_cost = models.DecimalField('unit cost in the stock unit', null=True, blank=True, **COST)
    base_counted = models.DecimalField('counted in the stock unit', null=True, blank=True, **QTY)
    lot = models.CharField('lot / batch', max_length=40, blank=True)
    serial = models.CharField('serial number', max_length=60, blank=True)
    expiry = models.DateField(null=True, blank=True)
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    to_bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='to bin')
    weight = models.DecimalField('weight (kg)', null=True, blank=True, **QTY)
    volume = models.DecimalField('volume (CBM)', null=True, blank=True, **QTY)
    value = models.DecimalField(default=0, **MONEY)
    return_reason = models.CharField('return reason', max_length=80, blank=True)
    condition = models.CharField(max_length=10, blank=True, choices=(('', '—'), ('new', 'As new'), ('used', 'Used / opened'), ('damaged', 'Damaged'), ('faulty', 'Faulty')))
    decision = models.CharField(max_length=10, blank=True, choices=(('', 'Restock'), ('restock', 'Restock'), ('repair', 'Send to repair (quarantine)'), ('scrap', 'Scrap')))
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['id']

    def delete(self, *a, **kw):
        if self.doc.status != 'draft':   # the lines of a posted document stay with its moves
            raise models.ProtectedError('The document is posted – its lines stay.', [self.doc])
        return super().delete(*a, **kw)


class StockMove(models.Model):
    """One quantity movement – never changed (corrections by reversal)."""
    doc = models.ForeignKey(StockDoc, null=True, blank=True, on_delete=models.PROTECT, related_name='moves')
    line = models.ForeignKey(StockDocLine, null=True, blank=True, on_delete=models.SET_NULL, related_name='moves')
    date = models.DateField(db_index=True)
    seq = models.BigIntegerField(default=0, db_index=True)
    kind = models.CharField(max_length=12)
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+')
    branch_id = models.IntegerField(null=True, blank=True)
    bin = models.ForeignKey(Bin, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    lot = models.CharField(max_length=40, blank=True, db_index=True)
    serial = models.CharField(max_length=60, blank=True, db_index=True)
    expiry = models.DateField(null=True, blank=True)
    qty = models.DecimalField(**QTY)
    costing_key = models.CharField(max_length=60, db_index=True)
    in_cost = models.DecimalField('unit cost given (inflows)', null=True, blank=True, **COST)
    owner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.PROTECT, related_name='+', help_text='supplier consignment stock (not valued)')
    settlement = models.ForeignKey('ConsignmentSettlement', null=True, blank=True, on_delete=models.SET_NULL, related_name='moves',
                                   help_text='consignment stock taken over: the settlement it is on')
    source_move = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                    help_text='transfer in ← its transfer out; return ← the move it returns')
    offset_role = models.CharField(max_length=20, blank=True, help_text='account role on the other side of the inventory account')
    value = models.DecimalField('value booked (direct + adjustments)', default=0, **MONEY)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'seq', 'id']


class StockValue(models.Model):
    """One cost amount on the inventory side (+ raises stock value) with its valuation date – never changed."""
    move = models.ForeignKey(StockMove, null=True, blank=True, on_delete=models.PROTECT, related_name='values')
    doc = models.ForeignKey(StockDoc, null=True, blank=True, on_delete=models.PROTECT, related_name='values')
    landed = models.ForeignKey('LandedCost', null=True, blank=True, on_delete=models.PROTECT, related_name='values')
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+')
    warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    costing_key = models.CharField(max_length=60, db_index=True, blank=True)
    valuation_date = models.DateField(db_index=True)
    seq = models.BigIntegerField(default=0, db_index=True)
    kind = models.CharField(max_length=12, choices=VALUE_KINDS)
    amount = models.DecimalField(**MONEY)
    inventory_account = models.CharField(max_length=40, blank=True)
    offset_role = models.CharField(max_length=20, blank=True)
    offset_account = models.CharField(max_length=40, blank=True)
    in_stock = models.BooleanField(default=True, help_text='False: the part of a landed cost on stock already sold (no inventory side)')
    gl = models.CharField(max_length=10, default='pending', help_text='pending / posted / none (periodic)')
    journal_id = models.IntegerField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['valuation_date', 'id']


class CostLayer(models.Model):
    """One inflow: FIFO / LIFO / lot / serial costs come from here; average and standard items keep them for ageing."""
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+')
    costing_key = models.CharField(max_length=60, db_index=True)
    move = models.OneToOneField(StockMove, on_delete=models.CASCADE, related_name='layer')
    received_date = models.DateField()
    lot = models.CharField(max_length=40, blank=True)
    serial = models.CharField(max_length=60, blank=True)
    qty_in = models.DecimalField(**QTY)
    qty_left = models.DecimalField(**QTY)
    unit_cost = models.DecimalField(**COST)

    class Meta:
        ordering = ['received_date', 'move__seq', 'id']


class CostApplication(models.Model):
    """Which outflow used which inflow (Business Central item application)."""
    out_move = models.ForeignKey(StockMove, on_delete=models.CASCADE, related_name='applications')
    layer = models.ForeignKey(CostLayer, on_delete=models.CASCADE, related_name='applications')
    qty = models.DecimalField(**QTY)
    cost = models.DecimalField(**MONEY)
    date = models.DateField()

    class Meta:
        ordering = ['date', 'id']


class Reservation(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+', verbose_name='item')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+')
    qty = models.DecimalField(**QTY)
    reference = models.CharField('for (order, project …)', max_length=80)
    until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=(('active', 'Active'), ('released', 'Released'), ('used', 'Used')), default='active')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('warehouse',)

    class Meta:
        ordering = ['-id']


CHARGES = (('freight', 'Freight'), ('insurance', 'Insurance'), ('duty', 'Customs duty'), ('clearing', 'Clearing / agency'),
           ('port', 'Port handling'), ('demurrage', 'Demurrage'), ('inspection', 'Inspection'), ('transport', 'Local transport'), ('other', 'Other'),
           ('vat', 'Import VAT (not a cost – input VAT)'))
BASES = (('value', 'By value'), ('quantity', 'By quantity'), ('weight', 'By weight'), ('volume', 'By volume (CBM)'), ('manual', 'Manual'))


class LandedCost(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    date = models.DateField('posting date')
    shipment = models.CharField('shipment / BL / AWB / container', max_length=120, blank=True)
    receipts = models.JSONField('goods receipts', default=list, blank=True, help_text='ids of the posted receipts the charges go to')
    kind = models.CharField(max_length=8, choices=(('actual', 'Actual (the bills)'), ('estimate', 'Estimate (accrued at receipt)')), default='actual')
    estimate = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='actuals', verbose_name='settles the estimate',
                                 help_text='Actual: the estimate it replaces – only the difference is posted')
    status = models.CharField(max_length=10, choices=(('draft', 'Draft'), ('posted', 'Posted'), ('settled', 'Settled'), ('cancelled', 'Cancelled')), default='draft')
    total = models.DecimalField(default=0, **MONEY)
    to_stock = models.DecimalField('to stock on hand', default=0, **MONEY)
    to_cogs = models.DecimalField('to stock already used (COGS)', default=0, **MONEY)
    allocation = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-id']


class LandedCharge(models.Model):
    landed = models.ForeignKey(LandedCost, on_delete=models.CASCADE, related_name='charges')
    charge = models.CharField(max_length=12, choices=CHARGES, default='freight')
    partner = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='supplier of the charge')
    amount = models.DecimalField(default=0, **MONEY)
    percent = models.DecimalField('% of the goods value', max_digits=7, decimal_places=3, null=True, blank=True,
                                  help_text='e.g. customs duty 5 – the amount is worked out from the receipts')
    basis = models.CharField(max_length=10, choices=BASES, default='value')
    manual = models.JSONField('manual split', default=dict, blank=True, help_text='{receipt line id: amount} for the manual basis')
    invoice = models.CharField('invoice no.', max_length=60, blank=True)
    currency = models.CharField(max_length=3, blank=True, help_text='Empty: the company currency')
    fc_amount = models.DecimalField('amount in the currency', null=True, blank=True, **MONEY)
    rate = models.DecimalField('exchange rate', max_digits=16, decimal_places=8, null=True, blank=True)

    class Meta:
        ordering = ['id']

    def delete(self, *a, **kw):
        if self.landed.status != 'draft':
            raise models.ProtectedError('The landed cost is posted – its charges stay.', [self.landed])
        return super().delete(*a, **kw)


class ClosingJV(models.Model):
    """Periodic mode: the month-end closing stock journal (proposal from the cut-off valuation, reviewed, posted)."""
    number = models.CharField(max_length=20, blank=True)
    period_end = models.DateField(unique=True)
    status = models.CharField(max_length=10, choices=(('proposed', 'Proposed'), ('posted', 'Posted'), ('cancelled', 'Cancelled')), default='proposed')
    lines = models.JSONField(default=list, blank=True, help_text='[{account, opening, closing, change}]')
    total_change = models.DecimalField(default=0, **MONEY)
    journal_id = models.IntegerField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-period_end']


class PeriodClose(models.Model):
    """Perpetual mode: closing the stock period – the cost adjustment is run, every cost amount is posted, and no movement or cost
    can be dated on or before the date any more (later corrections go to the first open day)."""
    number = models.CharField(max_length=20, blank=True)
    period_end = models.DateField('close up to', unique=True)
    status = models.CharField(max_length=10, choices=(('draft', 'Draft'), ('closed', 'Closed'), ('reopened', 'Reopened')), default='draft')
    valuation = models.DecimalField('stock value at the date', default=0, **MONEY)
    gl_balance = models.DecimalField('inventory accounts in the GL', default=0, **MONEY)
    difference = models.DecimalField(default=0, **MONEY)
    snapshot = models.JSONField(default=list, blank=True, help_text='the valuation per item and warehouse at the close – reprinted exactly later')
    note = models.TextField(blank=True)
    closed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-period_end']


# ------------------------------------------------------------------ item attributes and variants (v1.75.0)
ATTR_KINDS = (('choice', 'List of values'), ('text', 'Text'), ('number', 'Number'), ('yesno', 'Yes / no'))


class Attribute(models.Model):
    """A property of items: descriptive (brand, voltage, grade) and searchable; a list attribute can also make variants (size, colour)."""
    name = models.CharField(max_length=60)
    code = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=8, choices=ATTR_KINDS, default='choice')
    unit = models.CharField('unit (numbers)', max_length=12, blank=True, help_text='mm, kg, V …')
    makes_variants = models.BooleanField('can make variants', default=False, help_text='List attributes only: size, colour …')
    sequence = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sequence', 'name']

    def __str__(self):
        return self.name


class AttributeValue(models.Model):
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='values')
    value = models.CharField(max_length=60)
    code = models.CharField('code (SKU part)', max_length=12, blank=True, help_text='RED, XL … – used in variant codes')
    sequence = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['attribute__sequence', 'sequence', 'value']
        unique_together = [('attribute', 'value')]

    def __str__(self):
        return f'{self.attribute.name}: {self.value}'


class CategoryAttribute(models.Model):
    """The attribute set of an item category: the attributes its items show (mandatory or not)."""
    category = models.ForeignKey('ZeoMasters.ProductCategory', on_delete=models.CASCADE, related_name='+', verbose_name='item category')
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='+')
    required = models.BooleanField(default=False)
    sequence = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ['category__name', 'sequence']
        unique_together = [('category', 'attribute')]

    def __str__(self):
        return f'{self.category}: {self.attribute}'


class ItemAttribute(models.Model):
    """The value of one attribute on one item."""
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='inv_attributes', verbose_name='item')
    attribute = models.ForeignKey(Attribute, on_delete=models.CASCADE, related_name='+')
    value = models.ForeignKey(AttributeValue, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='value (list)')
    text = models.CharField('value (text)', max_length=120, blank=True)
    number = models.DecimalField('value (number)', max_digits=16, decimal_places=4, null=True, blank=True)
    yes = models.BooleanField('value (yes / no)', null=True, blank=True)
    shown = models.CharField(max_length=140, blank=True, db_index=True, help_text='The value as text (search)')

    class Meta:
        ordering = ['product__name', 'attribute__sequence']
        unique_together = [('product', 'attribute')]

    def __str__(self):
        return f'{self.attribute}: {self.shown}'


class VariantTemplate(models.Model):
    """An item that comes in variants (T-shirt in sizes and colours): each combination is an item of its own in the item master."""
    product = models.OneToOneField(PRODUCT, on_delete=models.CASCADE, related_name='inv_template', verbose_name='template item')
    code_pattern = models.CharField('variant code', max_length=40, default='{code}-{values}', help_text='{code} of the template and {values} codes: TSH-RED-L')
    name_pattern = models.CharField('variant name', max_length=60, default='{name} – {values}', help_text='T-shirt – Red / L')
    excluded = models.JSONField('combinations not made', default=list, blank=True, help_text='[["RED", "XXL"], …] – value codes')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['product__name']

    def __str__(self):
        return str(self.product)


class TemplateAxis(models.Model):
    template = models.ForeignKey(VariantTemplate, on_delete=models.CASCADE, related_name='axes')
    attribute = models.ForeignKey(Attribute, on_delete=models.PROTECT, related_name='+')
    values = models.ManyToManyField(AttributeValue, related_name='+', blank=True)
    sequence = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ['template', 'sequence', 'id']
        unique_together = [('template', 'attribute')]

    def __str__(self):
        return f'{self.template}: {self.attribute}'


class Variant(models.Model):
    template = models.ForeignKey(VariantTemplate, on_delete=models.CASCADE, related_name='variants')
    product = models.OneToOneField(PRODUCT, on_delete=models.PROTECT, related_name='inv_variant', verbose_name='variant item')
    key = models.CharField(max_length=120, help_text='the value ids, in axis order')
    label = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['template', 'key']
        unique_together = [('template', 'key')]

    def __str__(self):
        return str(self.product)


# ------------------------------------------------------------------ consignment (v1.75.0)
class ConsignmentPrice(models.Model):
    """The agreed price of a supplier's consignment item: what is owed when the stock is used."""
    partner = models.ForeignKey(PARTNER, on_delete=models.CASCADE, related_name='+', verbose_name='supplier')
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+', verbose_name='item')
    price = models.DecimalField('agreed price (stock unit)', **COST)
    valid_from = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['partner__name', 'product__name', '-valid_from']
        unique_together = [('partner', 'product', 'valid_from')]


class ConsignmentSettlement(models.Model):
    """Supplier consignment stock used in a period: the statement owed to the supplier (posted: Dr Consignment payable / Cr Payable)."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    partner = models.ForeignKey(PARTNER, on_delete=models.PROTECT, related_name='+', verbose_name='supplier')
    date_to = models.DateField('used up to')
    date = models.DateField('posting date')
    status = models.CharField(max_length=10, choices=(('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled')), default='draft')
    lines = models.JSONField(default=list, blank=True)
    total = models.DecimalField(default=0, **MONEY)
    supplier_invoice = models.CharField(max_length=60, blank=True)
    journal_id = models.IntegerField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.number} {self.partner}'


class CategoryAccounts(models.Model):
    """v1.75.0 Sage 300 I/C category accounts: the sales side per item category (revenue, returns, COGS, cost variance, damaged goods, internal use)
    and the account set its items take. A sub-category without its own row uses its parent's."""
    category = models.OneToOneField('ZeoMasters.ProductCategory', on_delete=models.CASCADE, related_name='inv_accounts')
    account_set = models.ForeignKey(AccountSet, null=True, blank=True, on_delete=models.PROTECT, related_name='+', help_text='The set of the items of this category')
    production_set = models.ForeignKey(AccountSet, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='production account set')
    accounts = models.JSONField(default=dict, blank=True, help_text='{role: account code} – revenue, sales_returns, cogs, cost_variance, scrap, consumption')

    class Meta:
        ordering = ['category__name']

    def __str__(self):
        return str(self.category)


class ReorderRule(models.Model):
    """v1.75.0 reorder planning per item × warehouse: levels, policy, lead time, safety stock, where it comes from."""
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+', verbose_name='item')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE, related_name='reorder_rules')
    min_qty = models.DecimalField('reorder point (min)', default=0, **QTY)
    max_qty = models.DecimalField('maximum', default=0, **QTY)
    reorder_qty = models.DecimalField('reorder quantity', default=0, **QTY)
    policy = models.CharField(max_length=8, default='max', choices=(('fixed', 'Fixed quantity'), ('max', 'Up to the maximum'), ('lot', 'Lot-for-lot (the shortage)')))
    multiple = models.DecimalField('order multiple', default=0, **QTY)
    moq = models.DecimalField('minimum order quantity', default=0, **QTY)
    lead_time_days = models.PositiveIntegerField('lead time (days)', null=True, blank=True, help_text="Empty: the item's lead time in Masters")
    safety_qty = models.DecimalField('safety stock', default=0, **QTY)
    safety_days = models.PositiveIntegerField('safety stock (days of use)', null=True, blank=True)
    source = models.CharField(max_length=8, default='buy', choices=(('buy', 'Buy from the supplier'), ('transfer', 'Transfer from a warehouse')))
    source_warehouse = models.ForeignKey(Warehouse, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='from warehouse')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['warehouse__code', 'product__name']
        unique_together = [('product', 'warehouse')]

    def __str__(self):
        return f'{self.product} @ {self.warehouse.code}'


class Tag(models.Model):
    """v1.75.0 tags with a colour – on items (their Inventory settings), warehouses and stock documents; filter and group by them"""
    name = models.CharField(max_length=40, unique=True)
    colour = models.CharField(max_length=9, default='#64748b')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


LINK_KINDS = (('optional', 'Optional product'), ('accessory', 'Accessory'), ('alternative', 'Alternative / substitute'), ('spare', 'Spare part'),
              ('upsell', 'Up-sell (better model)'), ('together', 'Frequently bought together'))


class ItemLink(models.Model):
    """v1.75.0 related products of an item (Sales and Purchase offer them; a short delivery offers the alternatives with stock)"""
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='inv_links', verbose_name='item')
    related = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+', verbose_name='related item')
    kind = models.CharField(max_length=12, choices=LINK_KINDS, default='optional')
    both_ways = models.BooleanField('both ways', default=False)
    default_qty = models.DecimalField('default quantity', default=1, **QTY)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ['product__name', 'kind', 'sequence', 'id']
        unique_together = [('product', 'related', 'kind')]

    def __str__(self):
        return f'{self.product} → {self.related}'


class PackingRule(models.Model):
    """v1.75.0 packing materials (boxes, pallets, tape, labels) used up automatically by the documents of an operation"""
    operation = models.ForeignKey(OperationType, null=True, blank=True, on_delete=models.CASCADE, related_name='packing_rules')
    kind = models.CharField('or every document of the kind', max_length=12, choices=DOC_KINDS, blank=True)
    material = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+', verbose_name='packing material')
    per = models.CharField(max_length=8, default='doc', choices=(('doc', 'Per document'), ('line', 'Per line'), ('unit', 'Per unit')))
    qty = models.DecimalField(default=1, **QTY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['id']


class CountPlan(models.Model):
    """v1.75.0 a stock count: scope, blind count, recount tolerance, approval – it makes the count document(s) with the stock frozen at the start"""
    number = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=80)
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+')
    zone = models.CharField(max_length=20, blank=True)
    category = models.ForeignKey('ZeoMasters.ProductCategory', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    abc_class = models.CharField('ABC class (cycle count)', max_length=1, blank=True, choices=(('', 'All'), ('A', 'A'), ('B', 'B'), ('C', 'C')))
    every_days = models.PositiveIntegerField('repeat every (days)', null=True, blank=True)
    date = models.DateField('count date')
    blind = models.BooleanField('blind count (expected hidden)', default=True)
    tolerance_qty = models.DecimalField('recount above (quantity)', null=True, blank=True, **QTY)
    tolerance_value = models.DecimalField('recount above (value)', null=True, blank=True, max_digits=14, decimal_places=3)
    approval_value = models.DecimalField('the manager approves variances above (value)', null=True, blank=True, max_digits=14, decimal_places=3)
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'Draft'), ('counting', 'Counting'), ('recount', 'Recount'), ('posted', 'Posted'),
                                                                      ('cancelled', 'Cancelled')))
    doc = models.ForeignKey(StockDoc, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='count document')
    snapshot = models.JSONField(default=list, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.number} {self.name}'


LOT_STATUS = (('available', 'Available'), ('quarantine', 'Quarantine (inspection)'), ('blocked', 'Blocked'), ('expired', 'Expired'), ('recalled', 'Recalled'))


class LotRecord(models.Model):
    """v1.75.0 a lot or serial number of an item: its dates, quality status and attributes – made on the first receipt"""
    product = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='+', verbose_name='item')
    lot = models.CharField('lot / batch', max_length=40, blank=True, db_index=True)
    serial = models.CharField('serial number', max_length=60, blank=True, db_index=True)
    supplier_lot = models.CharField("supplier's lot", max_length=40, blank=True)
    supplier = models.ForeignKey(PARTNER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    mfg_date = models.DateField('made on', null=True, blank=True)
    expiry = models.DateField('expiry', null=True, blank=True)
    best_before = models.DateField(null=True, blank=True)
    alert_date = models.DateField('alert from', null=True, blank=True)
    warranty_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=LOT_STATUS, default='available')
    attributes = models.JSONField(default=dict, blank=True, help_text='grade, shade, origin …')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product__name', 'expiry', 'lot', 'serial']
        unique_together = [('product', 'lot', 'serial')]

    def __str__(self):
        return f'{self.product} {self.serial or self.lot}'


class KitComponent(models.Model):
    """v1.75.0 a kit / bundle: delivering or issuing the kit takes its components out of stock"""
    kit = models.ForeignKey(PRODUCT, on_delete=models.CASCADE, related_name='inv_kit_lines', verbose_name='kit')
    component = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+')
    qty = models.DecimalField('quantity in one kit', **QTY)
    sequence = models.PositiveIntegerField(default=10)

    class Meta:
        ordering = ['kit__name', 'sequence', 'id']
        unique_together = [('kit', 'component')]


class Requisition(models.Model):
    """v1.75.0 a material requisition: a department / project asks for items, a manager approves, the store issues them"""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    date = models.DateField()
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name='+', verbose_name='issue from')
    charge_to = models.CharField('for (department, project, asset …)', max_length=120)
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'),
                                                                      ('issued', 'Issued'), ('rejected', 'Rejected')))
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    doc = models.ForeignKey(StockDoc, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='issue document')
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('warehouse',)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.number} {self.charge_to}'


class RequisitionLine(models.Model):
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+', verbose_name='item')
    qty = models.DecimalField('quantity', **QTY)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['id']
