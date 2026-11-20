"""ZEO Manufacturing (v1.79.0) – make to stock and make to order on the shared items: bills of materials with operations on work centres,
production orders (also from Sales orders and from planning), material issue and backflush through Inventory (to work in progress),
operation time with labour and overhead absorbed, output into stock, scrap, closing with the production variance in Accounting, material
planning (what to make, what to buy) and the cost roll-up."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
BRANCH = 'OrganisationManager.brnch_mstr'
PRODUCT = 'ZeoMasters.Product'
WAREHOUSE = 'ZeoInventory.Warehouse'
MONEY = dict(max_digits=18, decimal_places=3)
QTY = dict(max_digits=16, decimal_places=4)

ROLES = (('mfg_manager', 'Production manager – bills of materials, work centres, closing, costs'),
         ('mfg_planner', 'Production planner – planning and production orders'),
         ('mfg_operator', 'Shop floor – issue, operation time, output'),
         ('mfg_viewer', 'Manufacturing viewer (read only)'))
MO_STATUS = (('draft', 'Draft'), ('released', 'Released'), ('in_progress', 'In progress'), ('done', 'Done'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))


class MfgSettings(AppSettingsBase):
    default_warehouse = models.ForeignKey(WAREHOUSE, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                          verbose_name='components and finished goods warehouse')
    backflush = models.BooleanField('take the materials when the output is reported (backflush)', default=True)
    over_production = models.DecimalField('produce up to % more than ordered', max_digits=6, decimal_places=2, default=0)
    absorb_account = models.CharField('labour and overhead absorbed (GL account)', max_length=20, default='5990')
    variance_account = models.CharField('production variances (GL account)', max_length=20, default='5900')
    plan_days = models.PositiveSmallIntegerField('planning horizon (days)', default=30)


class MfgSequence(SequenceBase):
    pass


class MfgEvent(EventBase):
    pass


class MfgFile(FileBase):
    pass


class WorkCentre(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    hours_per_day = models.DecimalField('capacity (hours per day)', max_digits=6, decimal_places=2, default=8)
    labour_rate = models.DecimalField('labour cost per hour', default=0, **MONEY)
    machine_rate = models.DecimalField('machine cost per hour', default=0, **MONEY)
    overhead_rate = models.DecimalField('overhead per hour', default=0, **MONEY)
    efficiency = models.DecimalField('efficiency %', max_digits=6, decimal_places=2, default=100)
    warehouse = models.ForeignKey(WAREHOUSE, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Bom(models.Model):
    """the recipe of a made item: components per batch and the operations on the work centres"""
    code = models.CharField(max_length=30, blank=True)
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+', verbose_name='made item')
    qty = models.DecimalField('makes (quantity per batch)', default=1, **QTY)
    version = models.CharField(max_length=20, blank=True)
    is_default = models.BooleanField('the item\'s default', default=True)
    is_active = models.BooleanField(default=True)
    unit_cost = models.DecimalField('rolled-up cost per unit', default=0, **MONEY)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['product', '-is_default', 'code']

    def __str__(self):
        return f'{self.code} {self.product}'.strip()


class BomLine(models.Model):
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name='lines', verbose_name='bill of materials')
    component = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+')
    qty = models.DecimalField('quantity per batch', default=1, **QTY)
    scrap = models.DecimalField('scrap %', max_digits=6, decimal_places=2, default=0)
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['bom', 'id']


class BomOperation(models.Model):
    bom = models.ForeignKey(Bom, on_delete=models.CASCADE, related_name='operations', verbose_name='bill of materials')
    seq = models.PositiveSmallIntegerField('step', default=10)
    name = models.CharField(max_length=80)
    work_centre = models.ForeignKey(WorkCentre, on_delete=models.PROTECT, related_name='+')
    setup_min = models.DecimalField('set-up (minutes per order)', max_digits=10, decimal_places=2, default=0)
    run_min = models.DecimalField('run (minutes per unit)', max_digits=10, decimal_places=3, default=0)

    class Meta:
        ordering = ['bom', 'seq', 'id']


class ProductionOrder(models.Model):
    number = models.CharField(max_length=30, blank=True, db_index=True)
    product = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+', verbose_name='item to make')
    bom = models.ForeignKey(Bom, null=True, blank=True, on_delete=models.PROTECT, related_name='orders', verbose_name='bill of materials')
    qty = models.DecimalField('quantity to make', default=1, **QTY)
    done_qty = models.DecimalField('made', default=0, **QTY)
    scrap_qty = models.DecimalField('scrapped', default=0, **QTY)
    warehouse = models.ForeignKey(WAREHOUSE, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    start_date = models.DateField('start', null=True, blank=True)
    due_date = models.DateField('due', null=True, blank=True)
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    source = models.CharField(max_length=20, blank=True, help_text='sales, planning or empty')
    source_ref = models.CharField('for (sales order …)', max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=MO_STATUS, default='draft')
    unit_cost = models.DecimalField('planned cost per unit', default=0, **MONEY)
    material_cost = models.DecimalField('materials', default=0, **MONEY)
    labour_cost = models.DecimalField('labour and machine', default=0, **MONEY)
    overhead_cost = models.DecimalField('overhead', default=0, **MONEY)
    output_value = models.DecimalField('to stock', default=0, **MONEY)
    variance = models.DecimalField(default=0, **MONEY)
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.number or 'Draft production order'


class MaterialLine(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='materials')
    component = models.ForeignKey(PRODUCT, on_delete=models.PROTECT, related_name='+')
    planned = models.DecimalField('planned', default=0, **QTY)
    issued = models.DecimalField(default=0, **QTY)
    value = models.DecimalField('value issued', default=0, **MONEY)

    class Meta:
        ordering = ['order', 'id']


class OperationLine(models.Model):
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='operations')
    seq = models.PositiveSmallIntegerField('step', default=10)
    name = models.CharField(max_length=80)
    work_centre = models.ForeignKey(WorkCentre, on_delete=models.PROTECT, related_name='+')
    planned_min = models.DecimalField('planned (minutes)', max_digits=12, decimal_places=2, default=0)
    actual_min = models.DecimalField('actual (minutes)', max_digits=12, decimal_places=2, default=0)
    cost = models.DecimalField('cost absorbed', default=0, **MONEY)
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ['order', 'seq', 'id']


class Movement(models.Model):
    """what happened on the shop floor: materials issued / returned, output, scrap, time – with the Inventory document and the value"""
    order = models.ForeignKey(ProductionOrder, on_delete=models.CASCADE, related_name='movements')
    kind = models.CharField(max_length=10, choices=(('issue', 'Materials issued'), ('return', 'Materials returned'), ('output', 'Output to stock'),
                                                    ('scrap', 'Scrap'), ('time', 'Operation time'), ('close', 'Closing variance')))
    date = models.DateField()
    qty = models.DecimalField(default=0, **QTY)
    value = models.DecimalField(default=0, **MONEY)
    document = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class Plan(models.Model):
    """a planning run: what to make (production orders) and what to buy (purchase requests) for the sales orders and the reorder points"""
    number = models.CharField(max_length=30, blank=True)
    date = models.DateField()
    horizon = models.DateField('demand up to', null=True, blank=True)
    rows = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=10, choices=(('draft', 'Suggested'), ('done', 'Orders made')), default='draft')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number or 'Planning run'
