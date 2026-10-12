"""ZEO Property (v1.18.0) – buildings, units, owners, tenants / buyers, reservations, leases, sale contracts, billing records,
receipts, cheques, commissions, move-outs and owner statements, plus the tenant / owner / buyer portals.
Works alone; with ZEO CRM it takes reservations from won deals and opens renewal opportunities, with ZEO Maintenance it
sends move-in / move-out inspections and maintenance requests and receives recharges."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
MONEY = dict(max_digits=14, decimal_places=2)

ROLES = (
    ('prop_manager', 'Property manager'), ('prop_leasing', 'Leasing officer'), ('prop_sales', 'Sales officer'),
    ('prop_contracts', 'Contracts administrator'), ('prop_finance', 'Property accountant'), ('prop_commission', 'Commission approver'),
    ('prop_viewer', 'Property viewer (read only)'),
)


class PropertySettings(AppSettingsBase):
    currency = models.CharField(max_length=3, default='AED')
    reservation_days = models.PositiveSmallIntegerField('reservation valid (days)', default=7)
    renewal_alert_days = models.JSONField('renewal reminders (days before lease end)', default=list, blank=True,
                                          help_text='e.g. [90, 60, 30, 7]')
    deposit_percent = models.DecimalField('security deposit (% of annual rent)', max_digits=5, decimal_places=2, default=5)
    commercial_vat_percent = models.DecimalField('VAT on commercial rent %', max_digits=5, decimal_places=2, default=5)
    commission_percent = models.DecimalField('default agent commission %', max_digits=5, decimal_places=2, default=2)
    maintenance_approval_limit = models.DecimalField('owner approves maintenance above', default=1000, **MONEY)
    portal_url = models.CharField('portal address', max_length=200, blank=True)
    portal_tenant = models.BooleanField('tenant portal on', default=True)
    portal_owner = models.BooleanField('owner portal on', default=True)
    portal_buyer = models.BooleanField('buyer portal on', default=True)

    class Meta:
        permissions = [(k, f'Property role: {v}') for k, v in ROLES]
        verbose_name = 'Property settings'


class PropertySequence(SequenceBase):
    pass


class PropertyEvent(EventBase):
    class Meta(EventBase.Meta):
        pass


class PropertyFile(FileBase):
    pass


# ------------------------------------------------------------------ pack items
class UnitType(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    category = models.CharField(max_length=12, choices=(('residential', 'Residential'), ('commercial', 'Commercial'), ('other', 'Other')),
                                default='residential')

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return self.name


CHARGE_KINDS = (('rent', 'Rent'), ('deposit', 'Security deposit'), ('fee', 'Fee'), ('service', 'Service charge'), ('utility', 'Utility'),
                ('sale', 'Sale instalment'), ('maintenance', 'Maintenance recharge'), ('other', 'Other'))


class ChargeType(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=12, choices=CHARGE_KINDS, default='other')
    vat_percent = models.DecimalField('VAT %', max_digits=5, decimal_places=2, default=0)
    refundable = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class ChecklistItem(PackItem):
    """Handover checklist lines (lease move-in or sale handover)."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    applies_to = models.CharField(max_length=6, choices=(('lease', 'Lease move-in'), ('sale', 'Sale handover'), ('both', 'Both')), default='both')
    order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


# ------------------------------------------------------------------ people
class Owner(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=10, choices=(('person', 'Person'), ('company', 'Company')), default='person')
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    bank_details = models.TextField('bank details for payouts', blank=True)
    portal_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Party(models.Model):
    """A tenant or buyer."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField('tenant / buyer', max_length=8, choices=(('tenant', 'Tenant'), ('buyer', 'Buyer'), ('both', 'Tenant and buyer')), default='tenant')
    party_type = models.CharField('type', max_length=10, choices=(('person', 'Person'), ('company', 'Company')), default='person')
    name = models.CharField(max_length=150)
    company_name = models.CharField('company', max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    id_document = models.CharField('ID / trade licence reference', max_length=60, blank=True)
    address = models.TextField(blank=True)
    crm_contact_id = models.IntegerField('contact (CRM)', null=True, blank=True)
    portal_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'tenant / buyer'
        verbose_name_plural = 'tenants and buyers'

    def __str__(self):
        return self.name


# ------------------------------------------------------------------ portfolio
class Building(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField('code', max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=(('residential', 'Residential'), ('commercial', 'Commercial'), ('mixed', 'Mixed use'),
                                                    ('villa', 'Villa community'), ('land', 'Land')), default='residential')
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.SET_NULL, help_text='When one owner owns the whole building')
    floors = models.PositiveSmallIntegerField(null=True, blank=True)
    manager = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='property manager')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


UNIT_STATUS = (('available', 'Available'), ('reserved', 'Reserved'), ('let', 'Let'), ('notice', 'Under notice'), ('sold', 'Sold'),
               ('maintenance', 'Under maintenance'), ('unavailable', 'Not available'))


class Unit(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField('unit code', max_length=30, unique=True, blank=True)
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name='units')
    unit_no = models.CharField('unit no.', max_length=20)
    floor = models.CharField(max_length=10, blank=True)
    unit_type = models.ForeignKey(UnitType, null=True, blank=True, on_delete=models.PROTECT)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    bathrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    area = models.DecimalField('area (sq ft)', max_digits=10, decimal_places=2, null=True, blank=True)
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.SET_NULL, related_name='units')
    status = models.CharField(max_length=12, choices=UNIT_STATUS, default='available')
    rent = models.DecimalField('asking rent (per year)', null=True, blank=True, **MONEY)
    price = models.DecimalField('asking sale price', null=True, blank=True, **MONEY)
    furnishing = models.CharField(max_length=6, blank=True, choices=(('unf', 'Unfurnished'), ('semi', 'Semi-furnished'), ('furn', 'Furnished')))
    parking = models.PositiveSmallIntegerField('parking spaces', default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['building__name', 'unit_no']
        unique_together = [('building', 'unit_no')]

    def __str__(self):
        return f'{self.building.name} – {self.unit_no}'

    @property
    def label(self):
        return f'{self.building.name} {self.unit_no}'


class Agreement(models.Model):
    """Management agreement with an owner (fee, approval limit for maintenance)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name='agreements')
    units = models.ManyToManyField(Unit, blank=True, related_name='agreements')
    start = models.DateField()
    end = models.DateField(null=True, blank=True)
    fee_percent = models.DecimalField('management fee % of collected rent', max_digits=5, decimal_places=2, default=5)
    leasing_fee = models.DecimalField('leasing fee per new lease', default=0, **MONEY)
    approval_limit = models.DecimalField('owner approves maintenance above', null=True, blank=True, **MONEY)
    status = models.CharField(max_length=8, choices=(('draft', 'Draft'), ('active', 'Active'), ('ended', 'Ended')), default='draft')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-start']


# ------------------------------------------------------------------ deals
class Reservation(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='reservations')
    party = models.ForeignKey(Party, on_delete=models.PROTECT, verbose_name='tenant / buyer')
    kind = models.CharField('for', max_length=6, choices=(('lease', 'Lease'), ('sale', 'Sale')), default='lease')
    amount = models.DecimalField('deposit received', default=0, **MONEY)
    reserved_on = models.DateField()
    expires_on = models.DateField()
    status = models.CharField(max_length=10, default='active',
                              choices=(('active', 'Active'), ('converted', 'Converted'), ('expired', 'Expired'), ('cancelled', 'Cancelled')))
    crm_opportunity_id = models.IntegerField('opportunity (CRM)', null=True, blank=True)
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-reserved_on', '-id']


LEASE_STATUS = (('draft', 'Draft'), ('active', 'Active'), ('notice', 'Under notice'), ('ended', 'Ended'), ('renewed', 'Renewed'),
                ('terminated', 'Terminated'), ('cancelled', 'Cancelled'))
PAY_METHODS = (('cheque', 'Post-dated cheques'), ('transfer', 'Bank transfer'), ('card', 'Card'), ('cash', 'Cash'), ('direct_debit', 'Direct debit'))


class Lease(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='leases')
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='leases', verbose_name='tenant')
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.SET_NULL)
    start = models.DateField()
    end = models.DateField()
    annual_rent = models.DecimalField(**MONEY)
    deposit = models.DecimalField('security deposit', default=0, **MONEY)
    payments = models.PositiveSmallIntegerField('number of payments', default=4)
    method = models.CharField('payment method', max_length=12, choices=PAY_METHODS, default='cheque')
    status = models.CharField(max_length=10, choices=LEASE_STATUS, default='draft')
    version = models.PositiveSmallIntegerField(default=1)
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='next_versions', verbose_name='renewal of')
    registration_ref = models.CharField('government registration ref.', max_length=60, blank=True)
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    commission_percent = models.DecimalField('agent commission %', max_digits=5, decimal_places=2, null=True, blank=True)
    notice_given_on = models.DateField(null=True, blank=True)
    move_out_on = models.DateField('move-out date', null=True, blank=True)
    renewal_status = models.CharField(max_length=10, blank=True, default='',
                                      choices=(('', '–'), ('due', 'Renewal due'), ('offered', 'Offered'), ('renewing', 'Renewing'), ('leaving', 'Not renewing')))
    renewal_alerts = models.JSONField(default=list, blank=True)
    handover = models.JSONField('move-in checklist', default=list, blank=True)
    crm_opportunity_id = models.IntegerField('opportunity (CRM)', null=True, blank=True)
    mnt_inspection_id = models.IntegerField('move-in inspection (Maintenance)', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start', '-id']


class SaleContract(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='sales')
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='purchases', verbose_name='buyer')
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.SET_NULL)
    price = models.DecimalField(**MONEY)
    signed_on = models.DateField(null=True, blank=True)
    handover_due = models.DateField('handover planned', null=True, blank=True)
    handed_over_on = models.DateField(null=True, blank=True)
    plan = models.JSONField('payment plan', default=list, blank=True, help_text='[{"label": "Down payment", "percent": 20, "due": "2026-11-01"}]')
    status = models.CharField(max_length=12, default='draft',
                              choices=(('draft', 'Draft'), ('signed', 'Signed'), ('handed_over', 'Handed over'), ('cancelled', 'Cancelled')))
    handover = models.JSONField('handover checklist', default=list, blank=True)
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    commission_percent = models.DecimalField('agent commission %', max_digits=5, decimal_places=2, null=True, blank=True)
    crm_opportunity_id = models.IntegerField('opportunity (CRM)', null=True, blank=True)
    mnt_inspection_id = models.IntegerField('snag inspection (Maintenance)', null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-id']


# ------------------------------------------------------------------ money
BILL_STATUS = (('draft', 'Draft'), ('issued', 'Issued'), ('part_paid', 'Part paid'), ('paid', 'Paid'), ('void', 'Void'))


class Bill(models.Model):
    """A billing record. The invoice itself is made in the company's accounting system; its number goes in external ref."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, related_name='bills', verbose_name='tenant / buyer')
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.PROTECT, related_name='bills', help_text='When the owner pays (e.g. maintenance)')
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.PROTECT)
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT, related_name='bills')
    sale = models.ForeignKey(SaleContract, null=True, blank=True, on_delete=models.PROTECT, related_name='bills')
    charge = models.ForeignKey(ChargeType, on_delete=models.PROTECT, verbose_name='charge type')
    description = models.CharField(max_length=200, blank=True)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    due_date = models.DateField()
    amount = models.DecimalField(**MONEY)
    vat = models.DecimalField('VAT', default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    paid = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=10, choices=BILL_STATUS, default='draft')
    source = models.CharField(max_length=12, default='manual',
                              choices=(('manual', 'Manual'), ('lease', 'Lease schedule'), ('sale', 'Payment plan'), ('maintenance', 'Maintenance'), ('moveout', 'Move-out')))
    external_ref = models.CharField('invoice no. (accounting)', max_length=40, blank=True)
    external_status = models.CharField('accounting', max_length=10, default='not_sent',
                                       choices=(('not_sent', 'Not sent'), ('exported', 'Exported'), ('posted', 'Posted')))
    mnt_work_order_id = models.IntegerField('work order (Maintenance)', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['due_date', 'id']

    @property
    def balance(self):
        return (self.total or 0) - (self.paid or 0)


class Cheque(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT, related_name='cheques')
    party = models.ForeignKey(Party, on_delete=models.PROTECT, verbose_name='drawer')
    bill = models.ForeignKey(Bill, null=True, blank=True, on_delete=models.SET_NULL, related_name='cheques')
    cheque_no = models.CharField('cheque no.', max_length=30)
    bank = models.CharField(max_length=80, blank=True)
    amount = models.DecimalField(**MONEY)
    cheque_date = models.DateField('cheque date')
    status = models.CharField(max_length=10, default='received',
                              choices=(('received', 'In hand'), ('deposited', 'Deposited'), ('cleared', 'Cleared'), ('bounced', 'Bounced'),
                                       ('replaced', 'Replaced'), ('returned', 'Returned to tenant')))
    deposited_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['cheque_date', 'id']


class Receipt(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, verbose_name='from tenant / buyer')
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.PROTECT, verbose_name='from owner')
    received_on = models.DateField()
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=12, choices=PAY_METHODS, default='transfer')
    reference = models.CharField(max_length=60, blank=True)
    cheque = models.ForeignKey(Cheque, null=True, blank=True, on_delete=models.SET_NULL)
    allocated = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=8, choices=(('draft', 'Draft'), ('posted', 'Posted'), ('void', 'Void')), default='draft')
    external_ref = models.CharField('receipt no. (accounting)', max_length=40, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-received_on', '-id']


class Allocation(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name='allocations')
    bill = models.ForeignKey(Bill, on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField(**MONEY)


class Commission(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    agent = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name='+')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT)
    sale = models.ForeignKey(SaleContract, null=True, blank=True, on_delete=models.PROTECT)
    base = models.DecimalField('based on', **MONEY)
    percent = models.DecimalField('%', max_digits=5, decimal_places=2)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, default='pending',
                              choices=(('pending', 'Pending approval'), ('approved', 'Approved'), ('paid', 'Paid'), ('rejected', 'Rejected')))
    external_ref = models.CharField('payment ref. (accounting / payroll)', max_length=40, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-id']


class MoveOut(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name='moveouts')
    move_out_on = models.DateField('move-out date')
    inspection_on = models.DateField('inspection date', null=True, blank=True)
    status = models.CharField(max_length=10, default='planned',
                              choices=(('planned', 'Planned'), ('inspected', 'Inspected'), ('settled', 'Settled'), ('cancelled', 'Cancelled')))
    deductions = models.DecimalField(default=0, **MONEY)
    deduction_note = models.TextField('deductions – details', blank=True)
    unpaid = models.DecimalField('unpaid bills', default=0, **MONEY)
    refund = models.DecimalField('deposit to refund', default=0, **MONEY)
    settled_on = models.DateField(null=True, blank=True)
    mnt_inspection_id = models.IntegerField('move-out inspection (Maintenance)', null=True, blank=True)
    keys_returned = models.BooleanField(default=False)

    class Meta:
        ordering = ['-move_out_on']


class OwnerStatement(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    owner = models.ForeignKey(Owner, on_delete=models.PROTECT, related_name='statements')
    period_from = models.DateField()
    period_to = models.DateField()
    collected = models.DecimalField('rent collected', default=0, **MONEY)
    expenses = models.DecimalField('maintenance / expenses', default=0, **MONEY)
    fees = models.DecimalField('management fees', default=0, **MONEY)
    net = models.DecimalField('net to owner', default=0, **MONEY)
    lines = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=8, choices=(('draft', 'Draft'), ('issued', 'Issued'), ('paid', 'Paid out')), default='draft')
    external_ref = models.CharField('payout ref. (accounting)', max_length=40, blank=True)

    class Meta:
        ordering = ['-period_to']
    BRANCH_FROM = ('unit', 'lease', 'sale', 'owner', 'party')   # v1.22.0: where the branch comes from
    BRANCH_FROM = ('lease', 'bill', 'party')   # v1.22.0: where the branch comes from
    BRANCH_FROM = ('cheque', 'owner', 'party')   # v1.22.0: where the branch comes from
    BRANCH_FROM = ('lease', 'sale')   # v1.22.0: where the branch comes from
