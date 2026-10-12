"""ZEO Maintenance (v1.18.0) – categories with SLA, locations, assets, vendors, work orders (triage → assign → quote →
approval → work → verify → close), cost lines and recharges, preventive plans and inspections.
Works alone (own locations); with ZEO Property it works on Property units, takes portal requests, asks owners for
approval above their limit, sends costs back as billing records and reports inspection results."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
MONEY = dict(max_digits=14, decimal_places=2)

ROLES = (('mnt_manager', 'Maintenance manager'), ('mnt_coordinator', 'Maintenance coordinator'), ('mnt_technician', 'Technician'),
         ('mnt_viewer', 'Maintenance viewer (read only)'))

PRIORITY = (('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent'))
WHO_PAYS = (('company', 'Company'), ('owner', 'Owner'), ('tenant', 'Tenant'), ('warranty', 'Warranty / developer'), ('decide', 'Decide per job'))


class MaintSettings(AppSettingsBase):
    approval_limit = models.DecimalField('manager approves jobs above', default=5000, **MONEY)
    markup_percent = models.DecimalField('recharge mark-up %', max_digits=5, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='AED')
    verify_by_requester = models.BooleanField('requester confirms the work', default=False)

    class Meta:
        permissions = [(k, f'Maintenance role: {v}') for k, v in ROLES]
        verbose_name = 'Maintenance settings'


class MaintSequence(SequenceBase):
    pass


class MaintEvent(EventBase):
    class Meta(EventBase.Meta):
        pass


class MaintFile(FileBase):
    pass


class Category(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    trade = models.CharField(max_length=40, blank=True)
    priority = models.CharField('default priority', max_length=8, choices=PRIORITY, default='normal')
    response_hours = models.PositiveSmallIntegerField('respond within (hours)', default=24)
    resolve_hours = models.PositiveSmallIntegerField('fix within (hours)', default=72)
    who_pays = models.CharField('who pays', max_length=10, choices=WHO_PAYS, default='decide')

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


INSPECTION_KINDS = (('move_in', 'Move-in'), ('move_out', 'Move-out'), ('unit_ready', 'Unit ready'), ('snag', 'Snag list'),
                    ('routine', 'Routine'), ('preventive', 'Preventive'))


class Checklist(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=12, choices=INSPECTION_KINDS, default='routine')
    items = models.JSONField(default=list, blank=True, help_text='["Walls and paint", "Doors and locks", …]')

    class Meta:
        ordering = ['kind', 'name']

    def __str__(self):
        return self.name


class Location(models.Model):
    """Places to maintain when ZEO Property is not used (or for common areas): building, floor, room, plant room…"""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    code = models.CharField(max_length=30, unique=True, blank=True)
    name = models.CharField(max_length=150)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    property_building_id = models.IntegerField('building (Property)', null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Vendor(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    code = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    trade = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    licence_expiry = models.DateField('trade licence expiry', null=True, blank=True)
    rating = models.PositiveSmallIntegerField('rating (1–5)', null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Asset(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    code = models.CharField(max_length=30, unique=True, blank=True)
    name = models.CharField(max_length=150)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL)
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    make = models.CharField(max_length=80, blank=True)
    model_no = models.CharField('model', max_length=80, blank=True)
    serial = models.CharField(max_length=80, blank=True)
    installed_on = models.DateField(null=True, blank=True)
    warranty_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=(('working', 'Working'), ('faulty', 'Faulty'), ('retired', 'Retired')), default='working')

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


WO_STATUS = (('new', 'New'), ('assigned', 'Assigned'), ('quoting', 'Waiting for quotes'), ('approval', 'Waiting for approval'),
             ('in_progress', 'In progress'), ('on_hold', 'On hold'), ('done', 'Work done'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))
SOURCES = (('staff', 'Staff'), ('tenant_portal', 'Tenant portal'), ('buyer_portal', 'Buyer portal'), ('owner_portal', 'Owner portal'), ('crm', 'CRM'),
           ('preventive', 'Preventive plan'), ('inspection', 'Inspection'), ('phone', 'Phone / e-mail'))


class WorkOrder(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT)
    priority = models.CharField(max_length=8, choices=PRIORITY, default='normal')
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT)
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL)
    source = models.CharField(max_length=14, choices=SOURCES, default='staff')
    requester = models.CharField('requested by', max_length=150, blank=True)
    requester_email = models.EmailField(blank=True)
    requester_phone = models.CharField(max_length=30, blank=True)
    access = models.CharField('access / best time', max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=WO_STATUS, default='new')
    technician = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL)
    who_pays = models.CharField('who pays', max_length=10, choices=WHO_PAYS, default='company')
    estimate = models.DecimalField('estimated cost', null=True, blank=True, **MONEY)
    approval_by = models.CharField('approval by', max_length=8, blank=True, choices=(('', '–'), ('manager', 'Manager'), ('owner', 'Owner')))
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_note = models.CharField(max_length=255, blank=True)
    response_due = models.DateTimeField(null=True, blank=True)
    resolve_due = models.DateTimeField('fix due', null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    cost = models.DecimalField('actual cost', default=0, **MONEY)
    work_note = models.TextField('work done', blank=True)
    rating = models.PositiveSmallIntegerField('requester rating (1–5)', null=True, blank=True)
    recharge_bill_id = models.IntegerField('billing record (Property)', null=True, blank=True)
    inspection = models.ForeignKey('Inspection', null=True, blank=True, on_delete=models.SET_NULL, related_name='work_orders')
    plan = models.ForeignKey('PreventivePlan', null=True, blank=True, on_delete=models.SET_NULL, related_name='work_orders')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.title}'


class Quote(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='quotes')
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT)
    amount = models.DecimalField(**MONEY)
    days = models.PositiveSmallIntegerField('days to finish', null=True, blank=True)
    note = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=(('received', 'Received'), ('selected', 'Selected'), ('rejected', 'Not selected')), default='received')

    class Meta:
        ordering = ['amount']


class CostLine(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    work_order = models.ForeignKey(WorkOrder, on_delete=models.CASCADE, related_name='costs')
    kind = models.CharField(max_length=10, choices=(('labour', 'Labour'), ('material', 'Material'), ('vendor', 'Vendor invoice'), ('other', 'Other')), default='material')
    description = models.CharField(max_length=200)
    qty = models.DecimalField('quantity', max_digits=10, decimal_places=2, default=1)
    rate = models.DecimalField(**MONEY)
    amount = models.DecimalField(default=0, **MONEY)
    vendor_invoice = models.CharField('vendor invoice no.', max_length=40, blank=True)

    class Meta:
        ordering = ['id']


class PreventivePlan(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.SET_NULL)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL)
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    every = models.PositiveSmallIntegerField(default=3)
    unit = models.CharField('every', max_length=6, choices=(('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months')), default='months')
    next_due = models.DateField()
    lead_days = models.PositiveSmallIntegerField('create the job days before', default=7)
    technician = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL)
    checklist = models.ForeignKey(Checklist, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['next_due']


class Inspection(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=12, choices=INSPECTION_KINDS, default='routine')
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL)
    property_lease_id = models.IntegerField('lease (Property)', null=True, blank=True)
    property_moveout_id = models.IntegerField('move-out (Property)', null=True, blank=True)
    reference = models.CharField(max_length=80, blank=True)
    scheduled_on = models.DateField()
    inspector = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    checklist = models.ForeignKey(Checklist, null=True, blank=True, on_delete=models.SET_NULL)
    results = models.JSONField(default=list, blank=True, help_text='[{"item": "…", "ok": true, "note": "", "cost": 0}]')
    status = models.CharField(max_length=10, choices=(('planned', 'Planned'), ('done', 'Done'), ('cancelled', 'Cancelled')), default='planned')
    findings = models.PositiveSmallIntegerField(default=0)
    deductions = models.DecimalField('tenant deductions', default=0, **MONEY)
    note = models.TextField(blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-scheduled_on']
    BRANCH_FROM = ('location', 'property_unit_id', 'asset', 'plan', 'inspection')   # v1.22.0: where the branch comes from
