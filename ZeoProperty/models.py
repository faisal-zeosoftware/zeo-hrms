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
    # v1.40.0 complete property management
    country = models.CharField(max_length=2, blank=True, help_text='AE, KW, OM, QA, SA, IN – from the company when empty (registration name, ID types, VAT)')
    reservation_approval = models.BooleanField('reservations need approval', default=False)
    reservation_fee_refundable = models.BooleanField('reservation fee refundable by default', default=True)
    lease_approval_discount = models.DecimalField('lease needs approval when rent is below the asking rent by more than %', max_digits=5, decimal_places=2, default=10)
    sale_approval_discount = models.DecimalField('sale needs approval when price is below the asking price by more than %', max_digits=5, decimal_places=2, default=5)
    deduction_approval_above = models.DecimalField('deposit deductions need approval above', default=0, **MONEY)
    refund_approval_above = models.DecimalField('refunds need approval above', default=0, **MONEY)
    statement_approval = models.BooleanField('owner statements need approval before issue', default=False)
    reminder_days_before = models.JSONField('payment reminders (days before due)', default=list, blank=True, help_text='e.g. [7, 1]')
    reminder_days_after = models.JSONField('payment reminders (days after due)', default=list, blank=True, help_text='e.g. [3, 10, 20]')
    escalate_after_days = models.PositiveSmallIntegerField('escalate to the manager after (days overdue)', default=30)
    late_fee_percent = models.DecimalField('late fee % of the overdue amount', max_digits=5, decimal_places=2, default=0)
    late_fee_grace_days = models.PositiveSmallIntegerField('late fee after (days overdue)', default=15)
    bounce_fee = models.DecimalField('bounced cheque fee', default=0, **MONEY)
    document_alert_days = models.PositiveSmallIntegerField('document expiry alerts (days before)', default=30)
    service_charge_instalments = models.PositiveSmallIntegerField('service charge estimates billed in (instalments per year)', default=4)
    payment_link = models.CharField('online payment link', max_length=300, blank=True,
                                    help_text='Your payment gateway link with {amount}, {ref} and {name}, e.g. https://pay.example.com/?amt={amount}&ref={ref} – shown on unpaid bills in the portal')
    messaging_url = models.CharField('SMS / WhatsApp provider URL', max_length=300, blank=True,
                                     help_text='HTTP endpoint of your SMS or WhatsApp provider; ZEO posts JSON {to, text, channel}. The API key is kept encrypted.')
    messaging_secret = models.TextField(blank=True, editable=False)
    listing_site = models.BooleanField('public listing page on', default=False)
    listing_agency = models.CharField('agency name on listings', max_length=120, blank=True)
    listing_feed_key = models.CharField('listing feed key', max_length=40, blank=True, help_text='Secret part of the Bayut / Property Finder feed address')

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

    item = models.ForeignKey('ZeoMasters.Product', null=True, blank=True, on_delete=models.SET_NULL, related_name='prop_charges', verbose_name='item (master)')   # v1.43.0 item master
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
    id_type = models.CharField('ID type', max_length=30, blank=True)                     # v1.40.0
    id_number = models.CharField('ID number', max_length=60, blank=True)
    id_expiry = models.DateField('ID expiry', null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    phones = models.JSONField('other phones', default=list, blank=True)
    tax_id = models.CharField('tax registration no.', max_length=40, blank=True)
    partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='prop_owners', verbose_name='business partner')   # v1.41.0

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
    # v1.40.0 contacts: several phones / addresses, country-wise ID, consent
    id_type = models.CharField('ID type', max_length=30, blank=True, help_text='Emirates ID, passport, Civil ID, QID, Iqama, Aadhaar… (country pack)')
    id_expiry = models.DateField('ID expiry', null=True, blank=True)
    nationality = models.CharField(max_length=60, blank=True)
    phones = models.JSONField('other phones', default=list, blank=True)
    addresses = models.JSONField('other addresses', default=list, blank=True)
    emergency_contact = models.CharField(max_length=150, blank=True)
    consent = models.BooleanField('consent to keep personal data', default=False)
    consent_on = models.DateField(null=True, blank=True)
    retain_until = models.DateField('keep data until', null=True, blank=True)
    broker = models.ForeignKey('Broker', null=True, blank=True, on_delete=models.SET_NULL, related_name='parties', verbose_name='introduced by (broker)')
    partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='prop_parties', verbose_name='business partner')   # v1.41.0

    class Meta:
        ordering = ['name']
        verbose_name = 'tenant / buyer'
        verbose_name_plural = 'tenants and buyers'

    def __str__(self):
        return self.name


# ------------------------------------------------------------------ portfolio
class Project(models.Model):
    """v1.40.0 – a project / portfolio: the level above buildings (community, tower cluster, land bank)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='Branch of the record; empty = every branch')
    number = models.CharField('code', max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=(('portfolio', 'Portfolio'), ('community', 'Community'), ('development', 'Development'), ('land', 'Land bank')), default='portfolio')
    country = models.CharField(max_length=2, blank=True, help_text='AE, KW, OM, QA, SA, IN…')
    city = models.CharField(max_length=80, blank=True)
    developer = models.CharField(max_length=150, blank=True)
    manager = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='portfolio manager')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


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
    # v1.40.0 hierarchy and master data
    project = models.ForeignKey(Project, null=True, blank=True, on_delete=models.SET_NULL, related_name='buildings')
    block = models.CharField('block / cluster', max_length=60, blank=True)
    subtype = models.CharField(max_length=20, blank=True, choices=(('', '–'), ('apartment', 'Apartment building'), ('tower', 'Tower'), ('villa', 'Villas'), ('townhouse', 'Townhouses'),
                                                                   ('office', 'Office building'), ('retail', 'Retail / mall'), ('warehouse', 'Warehouse / industrial'), ('labour', 'Staff accommodation'),
                                                                   ('hotel', 'Hotel apartments'), ('land', 'Plot')))
    plot_no = models.CharField('plot no.', max_length=40, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    gps = models.CharField('GPS (lat, lng)', max_length=60, blank=True)
    year_built = models.PositiveSmallIntegerField(null=True, blank=True)
    built_up_area = models.DecimalField('built-up area (sq ft)', max_digits=12, decimal_places=2, null=True, blank=True)
    amenities = models.JSONField(default=list, blank=True, help_text='Pool, gym, parking…')
    service_charge_rate = models.DecimalField('service charge rate (per sq ft / year)', max_digits=10, decimal_places=2, null=True, blank=True)
    valuation = models.DecimalField(null=True, blank=True, **MONEY)
    valuation_date = models.DateField(null=True, blank=True)
    external_ref = models.CharField('external ref. (municipality / accounting)', max_length=60, blank=True)
    insurance_expiry = models.DateField('building insurance expires', null=True, blank=True)
    permit_expiry = models.DateField('permit / civil defence expires', null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Floor(models.Model):
    """v1.40.0 – floors of a building (made by the unit generator or by hand)."""
    building = models.ForeignKey(Building, on_delete=models.CASCADE, related_name='floor_set')
    no = models.IntegerField('floor no.')
    name = models.CharField(max_length=40, blank=True)
    use = models.CharField(max_length=12, blank=True, choices=(('', '–'), ('residential', 'Residential'), ('office', 'Office'), ('retail', 'Retail'), ('parking', 'Parking'), ('service', 'Service')))
    rentable_area = models.DecimalField('rentable area (sq ft)', max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['building__name', 'no']
        unique_together = [('building', 'no')]

    def __str__(self):
        return self.name or f'Floor {self.no}'


UNIT_STATUS = (('available', 'Available'), ('reserved', 'Reserved'), ('let', 'Let'), ('notice', 'Under notice'), ('sold', 'Sold'),
               ('maintenance', 'Under maintenance'), ('unavailable', 'Not available'), ('sold_pending', 'Sold – pending completion'))


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
    # v1.40.0 property master
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='sub_units', verbose_name='part of (unit)', help_text='Sub-unit of a bigger unit (subdivided office, room of a villa)')
    level = models.ForeignKey(Floor, null=True, blank=True, on_delete=models.SET_NULL, related_name='units', verbose_name='floor record')
    view = models.CharField(max_length=60, blank=True)
    built_up_area = models.DecimalField('built-up area (sq ft)', max_digits=12, decimal_places=2, null=True, blank=True)
    rentable_area = models.DecimalField('rentable area (sq ft)', max_digits=12, decimal_places=2, null=True, blank=True)
    carpet_area = models.DecimalField('carpet area (sq ft)', max_digits=12, decimal_places=2, null=True, blank=True)
    plot_no = models.CharField('plot no.', max_length=40, blank=True)
    gps = models.CharField('GPS (lat, lng)', max_length=60, blank=True)
    amenities = models.JSONField(default=list, blank=True)
    valuation = models.DecimalField(null=True, blank=True, **MONEY)
    valuation_date = models.DateField(null=True, blank=True)
    service_charge_rate = models.DecimalField('service charge rate (per sq ft / year)', max_digits=10, decimal_places=2, null=True, blank=True)
    meters = models.JSONField('meter references', default=list, blank=True, help_text='[{"kind": "Electricity", "number": "DEWA 123"}]')
    external_ref = models.CharField('external ref. (title deed / accounting)', max_length=60, blank=True)
    co_owners = models.JSONField(default=list, blank=True, help_text='[{"owner": id, "percent": 50}] when a unit has several owners')
    next_available = models.DateField('next available from', null=True, blank=True)

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
                              choices=(('pending', 'Waiting for approval'), ('active', 'Active'), ('converted', 'Converted'), ('expired', 'Expired'), ('cancelled', 'Cancelled')))
    crm_opportunity_id = models.IntegerField('opportunity (CRM)', null=True, blank=True)
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notes = models.TextField(blank=True)
    fee_refundable = models.BooleanField('fee refundable if not converted', default=True)   # v1.40.0
    refunded = models.DecimalField(default=0, **MONEY)

    class Meta:
        ordering = ['-reserved_on', '-id']


LEASE_STATUS = (('draft', 'Draft'), ('active', 'Active'), ('notice', 'Under notice'), ('ended', 'Ended'), ('renewed', 'Renewed'),
                ('terminated', 'Terminated'), ('cancelled', 'Cancelled'), ('amended', 'Amended'))
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
    # v1.40.0 rent rules, parties, commercial terms, approval, e-acceptance
    change = models.CharField(max_length=10, blank=True, default='', choices=(('', 'New lease'), ('renewal', 'Renewal'), ('amendment', 'Amendment')))
    units = models.ManyToManyField(Unit, blank=True, related_name='extra_leases', verbose_name='other units on this lease', help_text='Commercial: several units on one lease')
    parties = models.JSONField('joint tenants / guarantors', default=list, blank=True, help_text='[{"party": id, "role": "joint", "share": 50}]')
    occupants = models.JSONField('authorised occupants', default=list, blank=True)
    rent_free_days = models.PositiveSmallIntegerField('rent-free days', default=0)
    escalation_percent = models.DecimalField('escalation % at renewal', max_digits=5, decimal_places=2, null=True, blank=True)
    rent_steps = models.JSONField('stepped rent', default=list, blank=True, help_text='[{"from": "2027-01-01", "annual_rent": 120000}]')
    schedule = models.JSONField('custom payment schedule', default=list, blank=True, help_text='[{"due": "2026-11-01", "amount": 30000, "label": "Q1"}] – used instead of equal payments when filled')
    charges = models.JSONField('extra charges', default=list, blank=True, help_text='[{"charge": id, "amount": 500, "per": "month"}] – parking, service, chiller…')
    service_charge = models.BooleanField('tenant pays service charges (CAM)', default=False)
    review_date = models.DateField('rent review date', null=True, blank=True)
    review_kind = models.CharField('rent review', max_length=8, blank=True, choices=(('', '–'), ('fixed', 'Fixed %'), ('index', 'Index-linked'), ('market', 'Open market')))
    review_percent = models.DecimalField('review %', max_digits=5, decimal_places=2, null=True, blank=True)
    break_date = models.DateField('break option date', null=True, blank=True)
    break_notice_days = models.PositiveSmallIntegerField('break notice (days)', default=90)
    turnover_percent = models.DecimalField('turnover rent %', max_digits=5, decimal_places=2, null=True, blank=True)
    fitout_days = models.PositiveSmallIntegerField('fit-out period (days)', default=0)
    allocations = models.JSONField('signage / storage / parking', default=list, blank=True, help_text='[{"kind": "parking", "detail": "P2-14", "amount": 0}]')
    late_fee = models.BooleanField('late fee applies', default=True)
    approval = models.CharField(max_length=10, blank=True, default='', choices=(('', '–'), ('needed', 'Approval needed'), ('requested', 'Approval requested'), ('approved', 'Approved'), ('rejected', 'Rejected')))
    approval_note = models.CharField(max_length=200, blank=True)
    accepted_on = models.DateTimeField('accepted online by the tenant', null=True, blank=True)
    accepted_ip = models.CharField(max_length=45, blank=True)
    room = models.CharField('room / bed (shared unit)', max_length=40, blank=True)

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
                              choices=(('draft', 'Draft'), ('approval', 'Waiting for approval'), ('signed', 'Signed – pending completion'), ('completed', 'Completed'),
                                       ('handed_over', 'Handed over'), ('cancelled', 'Cancelled')))
    handover = models.JSONField('handover checklist', default=list, blank=True)
    # v1.40.0 offers, co-buyers, completion, cancellation
    co_buyers = models.JSONField(default=list, blank=True, help_text='[{"party": id, "share": 50}]')
    offers = models.JSONField('offer revisions', default=list, blank=True, help_text='[{"date": "2026-10-01", "price": 950000, "note": "first offer"}]')
    discount_percent = models.DecimalField('discount % on the asking price', max_digits=5, decimal_places=2, default=0)
    completion_on = models.DateField('completed (title transferred)', null=True, blank=True)
    approval_note = models.CharField(max_length=200, blank=True)
    cancellation_fee = models.DecimalField('cancellation fee kept', default=0, **MONEY)
    amendments = models.JSONField(default=list, blank=True, help_text='[{"date": "...", "text": "..."}]')
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
                              choices=(('manual', 'Manual'), ('lease', 'Lease schedule'), ('sale', 'Payment plan'), ('maintenance', 'Maintenance'), ('moveout', 'Move-out'),
                                       ('recurring', 'Recurring charge'), ('utility', 'Utility'), ('service', 'Service charge'), ('late_fee', 'Late fee'), ('bounce', 'Bounced cheque fee'),
                                       ('credit', 'Credit note'), ('cancellation', 'Cancellation')))
    external_ref = models.CharField('invoice no. (accounting)', max_length=40, blank=True)
    external_status = models.CharField('accounting', max_length=10, default='not_sent',
                                       choices=(('not_sent', 'Not sent'), ('exported', 'Exported'), ('posted', 'Posted')))
    mnt_work_order_id = models.IntegerField('work order (Maintenance)', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.40.0 credit notes, collections
    is_credit = models.BooleanField('credit note', default=False)
    credit_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='credits', verbose_name='credit note for')
    disputed = models.BooleanField(default=False)
    dispute_note = models.CharField(max_length=200, blank=True)
    promise_date = models.DateField('promised payment date', null=True, blank=True)
    promise_note = models.CharField(max_length=200, blank=True)
    reminders = models.JSONField('reminders sent', default=list, blank=True)
    escalated_on = models.DateField(null=True, blank=True)
    late_fee_billed = models.BooleanField(default=False)
    settlement = models.ForeignKey('SettlementPlan', null=True, blank=True, on_delete=models.SET_NULL, related_name='bills')
    meter_reading = models.ForeignKey('MeterReading', null=True, blank=True, on_delete=models.SET_NULL, related_name='bills')
    budget = models.ForeignKey('ServiceChargeBudget', null=True, blank=True, on_delete=models.SET_NULL, related_name='bills')
    recurring = models.ForeignKey('RecurringCharge', null=True, blank=True, on_delete=models.SET_NULL, related_name='bills')

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
    # v1.40.0
    drawer_name = models.CharField('drawer (name on the cheque)', max_length=150, blank=True)
    beneficiary = models.CharField(max_length=150, blank=True)
    cleared_on = models.DateField(null=True, blank=True)
    replaced_by = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replaces', verbose_name='replacement cheque')
    bounce_fee_bill = models.ForeignKey(Bill, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    slip = models.ForeignKey('DepositSlip', null=True, blank=True, on_delete=models.SET_NULL, related_name='cheques', verbose_name='deposit slip')

    class Meta:
        ordering = ['cheque_date', 'id']


class Receipt(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, verbose_name='from tenant / buyer')
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.PROTECT, verbose_name='from owner')
    received_on = models.DateField()
    amount = models.DecimalField(**MONEY)
    method = models.CharField(max_length=12, choices=PAY_METHODS + (('credit', 'Credit note'), ('online', 'Online payment')), default='transfer')
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
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.PROTECT)
    sale = models.ForeignKey(SaleContract, null=True, blank=True, on_delete=models.PROTECT)
    base = models.DecimalField('based on', **MONEY)
    percent = models.DecimalField('%', max_digits=5, decimal_places=2)
    amount = models.DecimalField(**MONEY)
    status = models.CharField(max_length=10, default='pending',
                              choices=(('waiting', 'Waiting for collection'), ('pending', 'Pending approval'), ('approved', 'Approved'), ('paid', 'Paid'), ('rejected', 'Rejected'), ('reversed', 'Reversed')))
    external_ref = models.CharField('payment ref. (accounting / payroll)', max_length=40, blank=True)
    note = models.CharField(max_length=200, blank=True)
    # v1.40.0 rules, splits, brokers
    rule = models.ForeignKey('CommissionRule', null=True, blank=True, on_delete=models.SET_NULL, related_name='commissions')
    broker = models.ForeignKey('Broker', null=True, blank=True, on_delete=models.SET_NULL, related_name='commissions', verbose_name='external broker')
    share_percent = models.DecimalField('share of the commission %', max_digits=5, decimal_places=2, default=100)
    trigger = models.CharField(max_length=10, default='contract', choices=(('contract', 'On contract'), ('collection', 'On first collection')))
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['-id']


class MoveOut(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name='moveouts')
    move_out_on = models.DateField('move-out date')
    inspection_on = models.DateField('inspection date', null=True, blank=True)
    status = models.CharField(max_length=10, default='planned',
                              choices=(('planned', 'Planned'), ('inspected', 'Inspected'), ('approval', 'Deductions – waiting for approval'), ('settled', 'Settled'), ('cancelled', 'Cancelled')))
    deductions = models.DecimalField(default=0, **MONEY)
    deduction_note = models.TextField('deductions – details', blank=True)
    unpaid = models.DecimalField('unpaid bills', default=0, **MONEY)
    refund = models.DecimalField('deposit to refund', default=0, **MONEY)
    settled_on = models.DateField(null=True, blank=True)
    mnt_inspection_id = models.IntegerField('move-out inspection (Maintenance)', null=True, blank=True)
    keys_returned = models.BooleanField(default=False)
    # v1.40.0 deposit control
    deduction_lines = models.JSONField(default=list, blank=True, help_text='[{"item": "Paint", "amount": 300}]')
    approval = models.CharField(max_length=10, blank=True, default='', choices=(('', '–'), ('needed', 'Approval needed'), ('approved', 'Approved'), ('rejected', 'Rejected')))
    approval_note = models.CharField(max_length=200, blank=True)
    inspection = models.ForeignKey('Inspection', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='move-out inspection')

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
    status = models.CharField(max_length=8, choices=(('draft', 'Draft'), ('approval', 'Waiting for approval'), ('approved', 'Approved'), ('issued', 'Issued'), ('paid', 'Paid out')), default='draft')
    external_ref = models.CharField('payout ref. (accounting)', max_length=40, blank=True)
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='only this property')   # v1.40.0
    contributions = models.DecimalField('owner contributions', default=0, **MONEY)
    other_fees = models.DecimalField('leasing and other fees', default=0, **MONEY)
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-period_to']
    BRANCH_FROM = ('unit', 'lease', 'sale', 'owner', 'party')   # v1.22.0: where the branch comes from
    BRANCH_FROM = ('lease', 'bill', 'party')   # v1.22.0: where the branch comes from
    BRANCH_FROM = ('cheque', 'owner', 'party')   # v1.22.0: where the branch comes from
    BRANCH_FROM = ('lease', 'sale')   # v1.22.0: where the branch comes from


# ================================================================== v1.40.0 – complete property management
BR = dict(null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='Branch of the record; empty = every branch')


class Broker(models.Model):
    """External broker / agency that introduces tenants and buyers and shares the commission."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    company_name = models.CharField('agency', max_length=150, blank=True)
    licence_no = models.CharField('broker licence no.', max_length=60, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    commission_percent = models.DecimalField('share of the commission %', max_digits=5, decimal_places=2, default=50)
    bank_details = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='prop_brokers', verbose_name='business partner')   # v1.41.0

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CommissionRule(models.Model):
    """Fixed, % or tiered commission with a split between agents and the external broker; paid on contract or on first collection."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    name = models.CharField(max_length=120)
    applies_to = models.CharField(max_length=6, choices=(('lease', 'Leases'), ('sale', 'Sales'), ('both', 'Both')), default='both')
    kind = models.CharField(max_length=8, choices=(('percent', 'Percent of rent / price'), ('fixed', 'Fixed amount'), ('tiered', 'Tiered %')), default='percent')
    percent = models.DecimalField('%', max_digits=5, decimal_places=2, default=0)
    fixed = models.DecimalField('fixed amount', default=0, **MONEY)
    tiers = models.JSONField(default=list, blank=True, help_text='[{"upto": 100000, "percent": 5}, {"upto": null, "percent": 3}]')
    trigger = models.CharField(max_length=10, default='contract', choices=(('contract', 'On contract'), ('collection', 'On first collection')))
    split = models.JSONField('split between agents', default=list, blank=True, help_text='[{"agent": employee id, "percent": 60}] – empty = all to the deal agent')
    broker_percent = models.DecimalField('external broker share %', max_digits=5, decimal_places=2, default=50)
    min_amount = models.DecimalField('minimum', default=0, **MONEY)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class WaitingList(models.Model):
    """Prospects waiting for a unit (or a unit type in a building) to become available."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, verbose_name='tenant / buyer')
    kind = models.CharField('for', max_length=6, choices=(('lease', 'Lease'), ('sale', 'Sale')), default='lease')
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='specific unit')
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.SET_NULL)
    unit_type = models.ForeignKey(UnitType, null=True, blank=True, on_delete=models.SET_NULL)
    budget = models.DecimalField('budget (per year / price)', null=True, blank=True, **MONEY)
    needed_from = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, default='waiting', choices=(('waiting', 'Waiting'), ('offered', 'Offered'), ('reserved', 'Reserved'), ('closed', 'Closed')))
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']


class Refund(models.Model):
    """Money going back to a tenant / buyer: deposit, reservation fee, cancelled sale, overpayment."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=12, choices=(('deposit', 'Security deposit'), ('reservation', 'Reservation fee'), ('sale', 'Cancelled sale'), ('overpayment', 'Overpayment'), ('other', 'Other')), default='deposit')
    party = models.ForeignKey(Party, on_delete=models.PROTECT, related_name='refunds', verbose_name='tenant / buyer')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL, related_name='refunds')
    sale = models.ForeignKey(SaleContract, null=True, blank=True, on_delete=models.SET_NULL, related_name='refunds')
    reservation = models.ForeignKey(Reservation, null=True, blank=True, on_delete=models.SET_NULL, related_name='refunds')
    moveout = models.ForeignKey(MoveOut, null=True, blank=True, on_delete=models.SET_NULL, related_name='refunds')
    amount = models.DecimalField(**MONEY)
    reason = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, default='requested', choices=(('requested', 'Requested'), ('approved', 'Approved'), ('paid', 'Paid'), ('rejected', 'Rejected')))
    method = models.CharField(max_length=12, choices=PAY_METHODS, default='transfer')
    paid_on = models.DateField(null=True, blank=True)
    reference = models.CharField('payment ref.', max_length=60, blank=True)
    external_ref = models.CharField('voucher no. (accounting)', max_length=40, blank=True)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class DepositSlip(models.Model):
    """A bank deposit of several cheques on one day."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    bank = models.CharField('deposited at (bank / account)', max_length=120)
    deposited_on = models.DateField()
    total = models.DecimalField(default=0, **MONEY)
    count = models.PositiveSmallIntegerField('cheques', default=0)
    reference = models.CharField('bank slip ref.', max_length=60, blank=True)
    status = models.CharField(max_length=8, default='open', choices=(('open', 'Open'), ('banked', 'Banked')))
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-deposited_on', '-id']


class SettlementPlan(models.Model):
    """Overdue amount split into agreed instalments."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    party = models.ForeignKey(Party, on_delete=models.PROTECT, verbose_name='tenant / buyer')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL)
    total = models.DecimalField('amount to settle', **MONEY)
    instalments = models.JSONField(default=list, blank=True, help_text='[{"due": "2026-12-01", "amount": 5000}]')
    status = models.CharField(max_length=8, default='draft', choices=(('draft', 'Draft'), ('active', 'Active'), ('done', 'Completed'), ('broken', 'Broken')))
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class RecurringCharge(models.Model):
    """Service / utility / parking charge billed every month, quarter or year."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.PROTECT, verbose_name='tenant / buyer')
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.PROTECT)
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.PROTECT)
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL, related_name='recurring')
    charge = models.ForeignKey(ChargeType, on_delete=models.PROTECT, verbose_name='charge type')
    description = models.CharField(max_length=200, blank=True)
    amount = models.DecimalField(**MONEY)
    every = models.CharField(max_length=8, choices=(('month', 'Month'), ('quarter', 'Quarter'), ('year', 'Year')), default='month')
    next_due = models.DateField()
    until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, default='active', choices=(('active', 'Active'), ('paused', 'Paused'), ('ended', 'Ended')))

    class Meta:
        ordering = ['next_due']


DOC_CATEGORIES = (('lease', 'Lease contract'), ('spa', 'Sale agreement (SPA)'), ('id', 'ID / passport'), ('visa', 'Visa / residence'), ('licence', 'Trade licence'),
                  ('title', 'Title deed'), ('noc', 'NOC'), ('insurance', 'Insurance'), ('permit', 'Permit / certificate'), ('cheque', 'Cheque copy'), ('inspection', 'Inspection report'),
                  ('statement', 'Statement'), ('other', 'Other'))


class Document(models.Model):
    """Document control: category, who / what it belongs to, expiry, version. The file is attached to the record (Files)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    category = models.CharField(max_length=12, choices=DOC_CATEGORIES, default='other')
    name = models.CharField(max_length=150)
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.CASCADE, related_name='documents', verbose_name='tenant / buyer')
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.CASCADE, related_name='documents')
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.CASCADE, related_name='documents')
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.CASCADE, related_name='documents')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.CASCADE, related_name='documents')
    sale = models.ForeignKey(SaleContract, null=True, blank=True, on_delete=models.CASCADE, related_name='documents')
    vendor = models.ForeignKey('Vendor', null=True, blank=True, on_delete=models.CASCADE, related_name='documents')
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    mandatory = models.BooleanField(default=False)
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=8, default='valid', choices=(('valid', 'Valid'), ('expiring', 'Expiring'), ('expired', 'Expired'), ('replaced', 'Replaced')))
    alerted = models.BooleanField(default=False)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class DocTemplate(PackItem):
    """Text template of a printed document (lease, SPA, receipt…) per country and language; placeholders in {braces}."""
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=(('lease', 'Lease'), ('spa', 'Sale agreement'), ('receipt', 'Receipt'), ('statement', 'Owner statement'), ('notice', 'Notice'), ('letter', 'Letter')), default='lease')
    country = models.CharField(max_length=2, blank=True)
    language = models.CharField(max_length=2, default='en', choices=(('en', 'English'), ('ar', 'Arabic')))
    body = models.TextField(help_text='Clauses; use {tenant}, {unit}, {start}, {end}, {rent}, {deposit}, {company}, {registration}…')

    class Meta:
        ordering = ['kind', 'country', 'language']

    def __str__(self):
        return self.name


class ServiceChargeBudget(models.Model):
    """Service charges / CAM: annual budget per building, allocated to the units by area or share, billed as estimates and reconciled at year end."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    building = models.ForeignKey(Building, on_delete=models.PROTECT, related_name='budgets')
    year = models.PositiveSmallIntegerField()
    basis = models.CharField('allocation', max_length=6, default='area', choices=(('area', 'By rentable area'), ('equal', 'Equal per unit'), ('share', 'By ownership share %')))
    lines = models.JSONField('budget lines', default=list, blank=True, help_text='[{"category": "Cleaning", "budget": 120000, "actual": 0}]')
    budget_total = models.DecimalField(default=0, **MONEY)
    actual_total = models.DecimalField(default=0, **MONEY)
    billed_total = models.DecimalField('billed as estimates', default=0, **MONEY)
    charge_to = models.CharField(max_length=7, default='tenants', choices=(('tenants', 'Tenants (leases with service charge)'), ('owners', 'Unit owners')))
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'Draft'), ('approved', 'Approved'), ('billed', 'Estimates billed'), ('reconciled', 'Reconciled')))
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-year', 'building__name']
        unique_together = [('building', 'year')]


class Meter(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=12, choices=(('electricity', 'Electricity'), ('water', 'Water'), ('gas', 'Gas'), ('chiller', 'Chiller / cooling'), ('other', 'Other')), default='electricity')
    meter_no = models.CharField('meter no.', max_length=60)
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.PROTECT, related_name='meter_set', help_text='Empty = common-area / shared meter of the building')
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.PROTECT, related_name='meter_set')
    shared = models.BooleanField('shared meter (allocate to the units by area)', default=False)
    tariff = models.DecimalField('tariff per unit of consumption', max_digits=10, decimal_places=4, default=0)
    unit_name = models.CharField('consumption unit', max_length=10, default='kWh')
    last_reading = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_read_on = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['building__name', 'unit__unit_no', 'kind']

    def __str__(self):
        return f'{self.get_kind_display()} {self.meter_no}'


class MeterReading(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    meter = models.ForeignKey(Meter, on_delete=models.PROTECT, related_name='readings')
    read_on = models.DateField()
    reading = models.DecimalField(max_digits=14, decimal_places=2)
    previous = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    consumption = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount = models.DecimalField(default=0, **MONEY)
    unusual = models.BooleanField('unusual consumption', default=False)
    status = models.CharField(max_length=8, default='read', choices=(('read', 'Read'), ('billed', 'Billed'), ('skipped', 'Not billed')))
    read_by = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['-read_on', '-id']


class Listing(models.Model):
    """A unit advertised for rent or sale: approval, publication window, price history, channels; published on the public page and the portal feeds."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='listings')
    kind = models.CharField('for', max_length=6, choices=(('rent', 'Rent'), ('sale', 'Sale')), default='rent')
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField('price (rent per year / sale price)', **MONEY)
    channels = models.JSONField(default=list, blank=True, help_text='website, bayut, propertyfinder, dubizzle')
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'Draft'), ('approval', 'Waiting for approval'), ('approved', 'Approved'), ('published', 'Published'), ('withdrawn', 'Withdrawn')))
    publish_from = models.DateField(null=True, blank=True)
    publish_to = models.DateField(null=True, blank=True)
    price_history = models.JSONField(default=list, blank=True)
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    permit_no = models.CharField('advertising permit no.', max_length=60, blank=True)
    crm_listing_id = models.IntegerField('listing (CRM)', null=True, blank=True)
    views = models.PositiveIntegerField(default=0)
    enquiries = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class MessageTemplate(PackItem):
    """What is sent on each event (e-mail, SMS, WhatsApp); placeholders in {braces}."""
    code = models.CharField(max_length=30, unique=True)
    event = models.CharField(max_length=20, choices=(('reminder_before', 'Payment reminder (before due)'), ('reminder_after', 'Payment reminder (overdue)'), ('late_fee', 'Late fee charged'),
                                                     ('cheque_bounced', 'Cheque bounced'), ('renewal', 'Lease renewal'), ('lease_active', 'Lease activated'), ('receipt', 'Payment received'),
                                                     ('statement', 'Owner statement'), ('document_expiry', 'Document expiring'), ('reservation', 'Reservation'), ('announcement', 'Announcement'),
                                                     ('inspection', 'Inspection scheduled'), ('refund', 'Refund paid'), ('moveout', 'Move-out')))
    channel = models.CharField(max_length=8, choices=(('email', 'E-mail'), ('sms', 'SMS'), ('whatsapp', 'WhatsApp'), ('push', 'Portal notification')), default='email')
    language = models.CharField(max_length=2, default='en', choices=(('en', 'English'), ('ar', 'Arabic')))
    subject = models.CharField(max_length=150, blank=True)
    body = models.TextField(help_text='{name}, {unit}, {amount}, {due}, {number}, {company}, {link}')

    class Meta:
        ordering = ['event', 'channel']

    def __str__(self):
        return f'{self.get_event_display()} ({self.channel})'


class Announcement(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    title = models.CharField(max_length=150)
    body = models.TextField()
    audience = models.CharField(max_length=8, default='all', choices=(('all', 'Everyone'), ('tenants', 'Tenants'), ('owners', 'Owners'), ('buyers', 'Buyers')))
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.CASCADE, help_text='Only the people of this building')
    show_from = models.DateField()
    show_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=9, default='draft', choices=(('draft', 'Draft'), ('published', 'Published'), ('archived', 'Archived')))
    send_message = models.BooleanField('also send by e-mail / message', default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-show_from', '-id']


class PortalRequest(models.Model):
    """Requests from the portals: move-in, move-out, profile change, NOC, other – handled by the staff."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=10, choices=(('move_in', 'Move-in'), ('move_out', 'Move-out'), ('profile', 'Profile change'), ('noc', 'NOC letter'), ('parking', 'Parking / access card'), ('other', 'Other')), default='other')
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.CASCADE, related_name='requests', verbose_name='tenant / buyer')
    owner = models.ForeignKey(Owner, null=True, blank=True, on_delete=models.CASCADE, related_name='requests')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.SET_NULL)
    wanted_on = models.DateField('date wanted', null=True, blank=True)
    text = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True, help_text='Profile changes {field: new value}')
    status = models.CharField(max_length=8, default='open', choices=(('open', 'Open'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('done', 'Done')))
    answer = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class InspectionTemplate(PackItem):
    """Room-wise checklist used by inspections."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    applies_to = models.CharField(max_length=12, default='all', choices=(('all', 'Every unit type'), ('residential', 'Residential'), ('commercial', 'Commercial')))
    rooms = models.JSONField(default=list, blank=True, help_text='[{"room": "Living room", "item": "Walls and paint"}]')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Inspection(models.Model):
    """Move-in / move-out / routine / unit-ready / snag inspection with room-wise findings, severity, meter readings, keys and signatures.
    Standalone; when ZEO Maintenance is linked the work orders for the findings are raised there."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=10, choices=(('move_in', 'Move-in'), ('move_out', 'Move-out'), ('routine', 'Routine'), ('unit_ready', 'Unit ready'), ('snag', 'Snag list')), default='move_in')
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name='inspections')
    lease = models.ForeignKey(Lease, null=True, blank=True, on_delete=models.SET_NULL, related_name='inspections')
    sale = models.ForeignKey(SaleContract, null=True, blank=True, on_delete=models.SET_NULL, related_name='inspections')
    template = models.ForeignKey(InspectionTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    scheduled_on = models.DateField('date')
    inspector = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    items = models.JSONField('findings', default=list, blank=True, help_text='[{"room": "Kitchen", "item": "Sink", "condition": "ok|fair|poor|damaged", "severity": "low|medium|high", "note": "", "cost": 0}]')
    meter_readings = models.JSONField(default=list, blank=True, help_text='[{"meter": "DEWA 123", "reading": 10450}]')
    keys = models.JSONField('keys and cards handed', default=list, blank=True, help_text='[{"item": "Main door key", "qty": 2}]')
    compare_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='compare with (move-in inspection)')
    total_cost = models.DecimalField('cost of findings', default=0, **MONEY)
    tenant_name = models.CharField('tenant / buyer present', max_length=150, blank=True)
    tenant_signed_on = models.DateTimeField(null=True, blank=True)
    inspector_signed_on = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=9, default='planned', choices=(('planned', 'Planned'), ('done', 'Done'), ('signed', 'Signed'), ('cancelled', 'Cancelled')))
    mnt_inspection_id = models.IntegerField('inspection (Maintenance)', null=True, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-scheduled_on', '-id']


class KeyItem(models.Model):
    """Key / access card / remote register per unit."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name='key_items')
    kind = models.CharField(max_length=8, choices=(('key', 'Key'), ('card', 'Access card'), ('remote', 'Remote'), ('parking', 'Parking card'), ('other', 'Other')), default='key')
    code = models.CharField('tag / card no.', max_length=40, blank=True)
    qty = models.PositiveSmallIntegerField(default=1)
    held_by = models.CharField(max_length=10, default='office', choices=(('office', 'Office'), ('tenant', 'Tenant / buyer'), ('owner', 'Owner'), ('vendor', 'Vendor'), ('lost', 'Lost')))
    party = models.ForeignKey(Party, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='issued to')
    issued_on = models.DateField(null=True, blank=True)
    returned_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['unit__unit_no', 'kind']


class Vendor(models.Model):
    """Vendors of the Property app (cleaning, security, MEP…) with compliance and SLA. When ZEO Maintenance is linked its vendors are used for work orders."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=150)
    trade = models.CharField(max_length=80, blank=True)
    contact = models.CharField('contact person', max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    licence_no = models.CharField('trade licence no.', max_length=60, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    insurance_expiry = models.DateField('insurance expires', null=True, blank=True)
    tax_id = models.CharField('tax registration no.', max_length=40, blank=True)
    bank_details = models.TextField(blank=True)
    sla_hours = models.PositiveSmallIntegerField('SLA – respond within (hours)', null=True, blank=True)
    coverage = models.ManyToManyField(Building, blank=True, related_name='vendors', verbose_name='covers buildings')
    rating = models.PositiveSmallIntegerField('rating 1–5', null=True, blank=True)
    status = models.CharField(max_length=8, default='active', choices=(('active', 'Active'), ('blocked', 'Blocked'), ('inactive', 'Inactive')))
    mnt_vendor_id = models.IntegerField('vendor (Maintenance)', null=True, blank=True)
    notes = models.TextField(blank=True)
    partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='prop_vendors', verbose_name='business partner')   # v1.41.0

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PurchaseOrder(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR)
    number = models.CharField(max_length=20, unique=True, blank=True)
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name='orders')
    building = models.ForeignKey(Building, null=True, blank=True, on_delete=models.SET_NULL)
    unit = models.ForeignKey(Unit, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=200)
    lines = models.JSONField(default=list, blank=True, help_text='[{"item": "Paint 20 l", "qty": 2, "price": 120}]')
    total = models.DecimalField(default=0, **MONEY)
    ordered_on = models.DateField(null=True, blank=True)
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=9, default='draft', choices=(('draft', 'Draft'), ('approval', 'Waiting for approval'), ('approved', 'Approved'), ('received', 'Received'), ('invoiced', 'Invoice matched'), ('paid', 'Paid'), ('cancelled', 'Cancelled')))
    invoice_no = models.CharField('vendor invoice no.', max_length=60, blank=True)
    invoice_amount = models.DecimalField(null=True, blank=True, **MONEY)
    charge_to = models.CharField(max_length=7, default='company', choices=(('company', 'Company'), ('owner', 'Owner of the unit'), ('tenant', 'Tenant of the unit'), ('budget', 'Service charge budget')))
    mnt_work_order_id = models.IntegerField('work order (Maintenance)', null=True, blank=True)
    external_ref = models.CharField('bill no. (accounting)', max_length=40, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
