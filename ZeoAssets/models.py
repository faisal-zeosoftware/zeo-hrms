"""ZEO Asset Management (v1.33.0) – a separate app on the ZEO app kit.

One shared register: every asset card belongs to one HR asset (OrganisationManager.Asset + AssetPlus.AssetProfile).
HR keeps custody, employee requests, damage / loss recovery through payroll and exit clearance; this app adds categories
with depreciation rules, a location tree with floor plans, QR / barcode / RFID labels and scanning, check-out / check-in,
reservations, physical audits, capital work in progress, depreciation runs with journal export, and disposal.
Links (only while the other app is installed and activated): Maintenance (assets and work orders), Property (units)."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
BRANCH = 'OrganisationManager.brnch_mstr'
MONEY = dict(max_digits=14, decimal_places=2)
BRANCH_HELP = 'Branch of the record – users see the records of their branches; empty = every branch'

ROLES = (('ast_manager', 'Asset manager'), ('ast_coordinator', 'Asset coordinator / storekeeper'), ('ast_finance', 'Fixed asset accountant'),
         ('ast_auditor', 'Asset auditor'), ('ast_viewer', 'Asset viewer (read only)'),
         # v1.34.0 release 2
         ('ast_maint_planner', 'Maintenance planner'), ('ast_technician', 'Technician'), ('ast_helpdesk', 'Helpdesk agent'),
         ('ast_storekeeper', 'Spare parts storekeeper'))

DEPRECIATION = (('none', 'No depreciation'), ('straight_line', 'Straight line'), ('declining', 'Declining balance'))
CONVENTION = (('full_month', 'Full month in the month it starts'), ('next_month', 'From the next month'), ('daily', 'By days'))
LABEL_SIZES = (('small', 'Small 50 × 25 mm'), ('medium', 'Medium 70 × 35 mm'), ('large', 'Large 100 × 50 mm'))
MNT_SYNC = (('none', 'Do not send'), ('critical', 'Critical and high assets'), ('all', 'Every asset'))


class AssetSettings(AppSettingsBase):
    currency = models.CharField(max_length=3, default='AED')
    fiscal_year_start = models.PositiveSmallIntegerField('fiscal year starts in month (1–12)', default=1)
    capitalise_threshold = models.DecimalField('capitalise items costing at least', default=1000, **MONEY,
                                               help_text='Cheaper items are expensed: no depreciation')
    convention = models.CharField('depreciation start', max_length=12, choices=CONVENTION, default='full_month')
    label_size = models.CharField(max_length=8, choices=LABEL_SIZES, default='medium')
    label_title = models.CharField('label heading', max_length=60, blank=True, help_text='Empty = company name')
    checkout_days = models.PositiveSmallIntegerField('default check-out days', default=7)
    audit_approval = models.BooleanField('audits need manager approval to close', default=True)
    maintenance_sync = models.CharField('send assets to Maintenance', max_length=8, choices=MNT_SYNC, default='critical',
                                        help_text='Only while the Maintenance app is active and linked')
    # v1.34.0 release 2
    handover_work_orders = models.BooleanField('hand work orders over to the Maintenance app', default=False,
                                               help_text='When the Maintenance app is active and linked, new work orders are done there; '
                                                         'their status comes back to the asset')
    wo_approval_limit = models.DecimalField('work orders above this cost need approval', default=5000, **MONEY)
    labour_rate = models.DecimalField('default labour rate per hour', default=0, **MONEY)
    ticket_prefix = models.CharField(max_length=10, default='AHD')
    mail_enabled = models.BooleanField('create tickets from e-mail', default=False)
    mail_host = models.CharField('IMAP server', max_length=120, blank=True)
    mail_port = models.PositiveIntegerField('IMAP port', default=993)
    mail_user = models.CharField('mailbox user', max_length=120, blank=True)
    mail_password = models.CharField('mailbox password', max_length=200, blank=True)
    mail_ssl = models.BooleanField('IMAP over SSL', default=True)

    class Meta:
        permissions = [(k, f'Asset Management role: {v}') for k, v in ROLES]
        verbose_name = 'Asset Management settings'


class AssetSequence(SequenceBase):
    pass


class AssetEventLog(EventBase):
    class Meta(EventBase.Meta):
        pass


class AssetFileStore(FileBase):
    pass


class Category(PackItem):
    """Asset category with its depreciation rule. Each category is one HR asset type (made automatically)."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    hr_type = models.ForeignKey('OrganisationManager.AssetType', null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                verbose_name='HR asset type')
    code_prefix = models.CharField('asset code prefix', max_length=12, blank=True, help_text='e.g. LAP → LAP-0001')
    capitalise = models.BooleanField('fixed asset (capitalise)', default=True)
    method = models.CharField('depreciation method', max_length=20, choices=DEPRECIATION, default='straight_line')
    life_months = models.PositiveIntegerField('useful life (months)', default=36)
    residual_percent = models.DecimalField('residual value %', max_digits=5, decimal_places=2, default=0)
    declining_rate = models.DecimalField('declining rate % per year', max_digits=6, decimal_places=2, null=True, blank=True,
                                         help_text='Empty = double declining (200% ÷ life in years)')
    gl_asset = models.CharField('GL account – asset', max_length=30, blank=True)
    gl_accumulated = models.CharField('GL account – accumulated depreciation', max_length=30, blank=True)
    gl_expense = models.CharField('GL account – depreciation expense', max_length=30, blank=True)
    gl_disposal = models.CharField('GL account – gain / loss on disposal', max_length=30, blank=True)
    requires_serial = models.BooleanField('serial number required', default=False)
    audit_every_months = models.PositiveSmallIntegerField('physical check every (months)', null=True, blank=True)

    class Meta:
        ordering = ['code']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


LOCATION_KINDS = (('site', 'Site'), ('building', 'Building'), ('floor', 'Floor'), ('room', 'Room / area'), ('store', 'Store / warehouse'),
                  ('vehicle', 'Vehicle'), ('external', 'External / customer site'))


class Location(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    code = models.CharField(max_length=30, unique=True, blank=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=LOCATION_KINDS, default='room')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    address = models.CharField(max_length=255, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    floor_plan = models.ImageField(upload_to='zeo_assets/floorplans/', null=True, blank=True)
    custodian = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='person in charge')
    property_building_id = models.IntegerField('building (Property)', null=True, blank=True)
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    mnt_location_id = models.IntegerField('location (Maintenance)', null=True, blank=True)
    is_active = models.BooleanField(default=True)

    BRANCH_FROM = ('parent',)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'

    def path(self):
        names, x, guard = [], self, 0
        while x is not None and guard < 12:
            names.append(x.name)
            x, guard = x.parent, guard + 1
        return ' › '.join(reversed(names))

    def descendants(self):
        ids, frontier = [self.pk], [self.pk]
        for _ in range(12):
            frontier = list(Location.objects.filter(parent_id__in=frontier).values_list('pk', flat=True))
            if not frontier:
                break
            ids += frontier
        return ids


STATUS = (('cwip', 'Under construction (CWIP)'), ('available', 'Available'), ('assigned', 'Checked out / assigned'),
          ('maintenance', 'Under maintenance'), ('lost', 'Lost / stolen'), ('disposed', 'Disposed'))
CONDITION = (('healthy', 'Good'), ('minor_damage', 'Minor damage'), ('major_damage', 'Major damage'))
CRITICALITY = (('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'))
OWNERSHIP = (('owned', 'Owned'), ('leased', 'Leased / rented'), ('third_party', 'Customer / third party'))


class AssetCard(models.Model):
    """The asset as Asset Management sees it. Fields marked "from HR" are kept in step with the HR asset."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    hr_asset = models.OneToOneField('OrganisationManager.Asset', null=True, blank=True, on_delete=models.CASCADE, related_name='asset_card',
                                    verbose_name='HR asset')
    code = models.CharField('asset code', max_length=40, blank=True, db_index=True)
    name = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='cards')
    serial_number = models.CharField(max_length=100, blank=True)
    model_no = models.CharField('model', max_length=100, blank=True)
    manufacturer = models.CharField(max_length=100, blank=True)
    specification = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='available')
    condition = models.CharField(max_length=14, choices=CONDITION, default='healthy')
    criticality = models.CharField(max_length=8, choices=CRITICALITY, default='medium')
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='cards')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='components', verbose_name='part of')
    holder = models.CharField('with', max_length=150, blank=True, help_text='From HR: employee, department or location holding it')
    holder_employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='held by employee')
    due_back = models.DateField('due back', null=True, blank=True)
    # purchase and ownership
    purchase_date = models.DateField()
    purchase_cost = models.DecimalField('cost', default=0, **MONEY)
    supplier = models.CharField(max_length=150, blank=True)
    supplier_partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='supplier (business partner)')   # v1.42.0
    model_item = models.ForeignKey('ZeoMasters.Product', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='model (item master)')   # v1.43.0 item master
    invoice_no = models.CharField('invoice no.', max_length=60, blank=True)
    purchase_order = models.CharField('purchase order', max_length=60, blank=True)
    warranty_end = models.DateField('warranty until', null=True, blank=True)
    ownership = models.CharField(max_length=12, choices=OWNERSHIP, default='owned')
    lessor = models.CharField('lessor / owner', max_length=150, blank=True)
    lease_end = models.DateField('lease ends', null=True, blank=True)
    # identification and tracking
    barcode = models.CharField(max_length=80, blank=True, db_index=True)
    rfid_tag = models.CharField('RFID tag', max_length=80, blank=True, db_index=True)
    gps_device = models.CharField('GPS tracker id', max_length=80, blank=True)
    last_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_seen_at = models.DateTimeField('last seen', null=True, blank=True)
    plan_x = models.DecimalField('floor plan x %', max_digits=5, decimal_places=2, null=True, blank=True)
    plan_y = models.DecimalField('floor plan y %', max_digits=5, decimal_places=2, null=True, blank=True)
    last_audited_on = models.DateField('last physical check', null=True, blank=True)
    # fixed asset book
    capitalised_on = models.DateField('capitalised on', null=True, blank=True)
    depreciation_start = models.DateField(null=True, blank=True, help_text='Empty = purchase date')
    method = models.CharField('depreciation method', max_length=20, choices=DEPRECIATION, default='straight_line')
    life_months = models.PositiveIntegerField('useful life (months)', default=36)
    residual_value = models.DecimalField('residual value', default=0, **MONEY)
    declining_rate = models.DecimalField('declining rate % per year', max_digits=6, decimal_places=2, null=True, blank=True)
    accumulated = models.DecimalField('accumulated depreciation', default=0, **MONEY)
    book_value = models.DecimalField('net book value', default=0, **MONEY)
    depreciated_to = models.DateField('depreciated up to', null=True, blank=True)
    # links to other apps (plain ids – only while linked)
    mnt_asset_id = models.IntegerField('asset (Maintenance)', null=True, blank=True)
    property_unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    BRANCH_FROM = ('location',)

    class Meta:
        ordering = ['code']
        verbose_name = 'asset'

    def __str__(self):
        return f'{self.code} {self.name}'.strip()


MOVE_KINDS = (('check_out', 'Check-out'), ('check_in', 'Check-in'), ('move', 'Location move'), ('transfer', 'Transfer'),
              ('gps', 'GPS position'), ('scan', 'Scan'))


class Movement(models.Model):
    """Every movement of an asset (custody changes are done through HR allocations and listed here too)."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    card = models.ForeignKey(AssetCard, on_delete=models.CASCADE, related_name='movements', verbose_name='asset')
    kind = models.CharField(max_length=10, choices=MOVE_KINDS)
    on = models.DateTimeField('when', auto_now_add=True)
    from_text = models.CharField('from', max_length=200, blank=True)
    to_text = models.CharField('to', max_length=200, blank=True)
    from_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    to_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    due_back = models.DateField(null=True, blank=True)
    condition = models.CharField(max_length=14, choices=CONDITION, blank=True)
    note = models.CharField(max_length=255, blank=True)
    hr_ref = models.CharField('HR reference', max_length=40, blank=True)
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    BRANCH_FROM = ('card',)

    class Meta:
        ordering = ['-on', '-id']


RES_STATUS = (('requested', 'Requested'), ('confirmed', 'Confirmed'), ('checked_out', 'Checked out'), ('returned', 'Returned'),
              ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))


class Reservation(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    card = models.ForeignKey(AssetCard, on_delete=models.CASCADE, related_name='reservations', verbose_name='asset')
    employee = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name='+', verbose_name='for')
    start = models.DateField('from')
    end = models.DateField('until')
    purpose = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=RES_STATUS, default='requested')
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('card',)

    class Meta:
        ordering = ['-start', '-id']


AUDIT_STATUS = (('draft', 'Draft'), ('counting', 'Counting'), ('review', 'Waiting for approval'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))


class Audit(models.Model):
    """Physical verification campaign: a location (with the places under it) and / or categories, counted by scanning."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=150)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name='+',
                                 help_text='With every place under it; empty = the whole branch')
    categories = models.ManyToManyField(Category, blank=True, related_name='+', help_text='Empty = every category')
    auditors = models.ManyToManyField(EMP, blank=True, related_name='+')
    due_date = models.DateField('count by', null=True, blank=True)
    blind = models.BooleanField('blind count (auditors do not see the expected list)', default=False)
    self_check = models.BooleanField('ask holders to confirm their assets', default=False)
    status = models.CharField(max_length=10, choices=AUDIT_STATUS, default='draft')
    expected = models.PositiveIntegerField(default=0)
    found = models.PositiveIntegerField(default=0)
    missing = models.PositiveIntegerField(default=0)
    misplaced = models.PositiveIntegerField(default=0)
    unexpected = models.PositiveIntegerField(default=0)
    damaged = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.TextField(blank=True)

    BRANCH_FROM = ('location',)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.number} {self.name}'


LINE_RESULT = (('pending', 'Not counted yet'), ('found', 'Found'), ('misplaced', 'Found in another place'), ('damaged', 'Found damaged'),
               ('missing', 'Missing'), ('unexpected', 'Not on the list (found here)'))


class AuditLine(models.Model):
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name='lines')
    card = models.ForeignKey(AssetCard, on_delete=models.CASCADE, related_name='+', verbose_name='asset')
    expected_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    found_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    holder = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='held by')
    result = models.CharField(max_length=10, choices=LINE_RESULT, default='pending')
    condition = models.CharField(max_length=14, choices=CONDITION, blank=True)
    counted_at = models.DateTimeField(null=True, blank=True)
    counted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    self_confirmed = models.BooleanField('confirmed by the holder', default=False)
    move_here = models.BooleanField('update the location when closing', default=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['audit', 'card__code']
        unique_together = [('audit', 'card')]


class CapitalCost(models.Model):
    """Costs built up on an asset under construction (CWIP), or added later to a capitalised asset."""
    card = models.ForeignKey(AssetCard, on_delete=models.CASCADE, related_name='capital_costs', verbose_name='asset')
    date = models.DateField()
    description = models.CharField(max_length=200)
    supplier = models.CharField(max_length=150, blank=True)
    supplier_partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='supplier (business partner)')   # v1.42.0
    invoice_no = models.CharField('invoice no.', max_length=60, blank=True)
    amount = models.DecimalField(**MONEY)
    capitalised = models.BooleanField(default=False)

    class Meta:
        ordering = ['date', 'id']


RUN_STATUS = (('draft', 'Draft'), ('posted', 'Posted (locked)'), ('reversed', 'Reversed'))


class DepreciationRun(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP + ' (empty = all)')
    number = models.CharField(max_length=20, blank=True)
    period_from = models.DateField('from')
    period_to = models.DateField('to')
    status = models.CharField(max_length=10, choices=RUN_STATUS, default='draft')
    assets = models.PositiveIntegerField(default=0)
    total = models.DecimalField('depreciation', default=0, **MONEY)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-period_to', '-id']

    def __str__(self):
        return f'{self.number} {self.period_from:%b %Y}–{self.period_to:%b %Y}' if self.period_from else self.number


class DepreciationLine(models.Model):
    run = models.ForeignKey(DepreciationRun, on_delete=models.CASCADE, related_name='lines')
    card = models.ForeignKey(AssetCard, on_delete=models.PROTECT, related_name='+', verbose_name='asset')
    category = models.ForeignKey(Category, null=True, on_delete=models.SET_NULL, related_name='+')
    months = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    opening = models.DecimalField('opening book value', **MONEY)
    amount = models.DecimalField('depreciation', **MONEY)
    closing = models.DecimalField('closing book value', **MONEY)
    gl_expense = models.CharField('debit', max_length=30, blank=True)
    gl_accumulated = models.CharField('credit', max_length=30, blank=True)

    class Meta:
        ordering = ['run', 'card__code']


# ====================================================================== v1.34.0 release 2
PRIORITY = (('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent'))
WO_KIND = (('preventive', 'Preventive'), ('corrective', 'Corrective'), ('breakdown', 'Breakdown'), ('inspection', 'Inspection'),
           ('calibration', 'Calibration'))


class ActivityType(PackItem):
    """Maintenance activity (service, inspection, calibration …) with its checklist."""
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=12, choices=WO_KIND, default='preventive')
    checklist = models.JSONField(default=list, blank=True, help_text='["Check oil level", "Replace filter", …]')
    hours = models.DecimalField('usual duration (hours)', max_digits=6, decimal_places=2, default=1)
    out_of_service = models.BooleanField('asset is out of service during the work', default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SlaRule(PackItem):
    """Helpdesk response and fix times by priority."""
    code = models.CharField(max_length=20, unique=True)
    priority = models.CharField(max_length=8, choices=PRIORITY)
    response_hours = models.DecimalField('respond within (hours)', max_digits=6, decimal_places=2, default=4)
    resolve_hours = models.DecimalField('fix within (hours)', max_digits=6, decimal_places=2, default=24)
    escalate_hours = models.DecimalField('escalate after (hours late)', max_digits=6, decimal_places=2, default=4)

    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.get_priority_display()}: {self.response_hours} h / {self.resolve_hours} h'


class Team(models.Model):
    """Support / maintenance team: tickets and work orders are assigned to a team, then to a technician."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    code = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField(max_length=100)
    leader = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='team leader')
    members = models.ManyToManyField(EMP, blank=True, related_name='+')
    email = models.EmailField('team e-mail', blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


PLAN_UNIT = (('days', 'Days'), ('weeks', 'Weeks'), ('months', 'Months'), ('meter', 'Meter (km / hours)'))


class MaintenancePlan(models.Model):
    """Preventive / recurring maintenance of an asset (or of every asset of a category)."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    name = models.CharField(max_length=150)
    card = models.ForeignKey(AssetCard, null=True, blank=True, on_delete=models.CASCADE, related_name='plans', verbose_name='asset')
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.CASCADE, related_name='+',
                                 help_text='Instead of one asset: every asset of this category')
    activity = models.ForeignKey(ActivityType, on_delete=models.PROTECT, related_name='+')
    every = models.PositiveIntegerField(default=1)
    unit = models.CharField(max_length=8, choices=PLAN_UNIT, default='months')
    next_due = models.DateField(null=True, blank=True, help_text='Date plans')
    next_meter = models.DecimalField('next at meter', max_digits=12, decimal_places=1, null=True, blank=True, help_text='Meter plans')
    lead_days = models.PositiveSmallIntegerField('create the work order days before', default=7)
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    technician = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    vendor = models.CharField(max_length=150, blank=True)
    vendor_partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='vendor (business partner)')   # v1.42.0
    estimate = models.DecimalField('estimated cost', null=True, blank=True, **MONEY)
    is_active = models.BooleanField(default=True)

    BRANCH_FROM = ('card',)

    class Meta:
        ordering = ['next_due', 'id']

    def __str__(self):
        return f'{self.number} {self.name}'


WO_STATUS = (('approval', 'Waiting for approval'), ('open', 'Open'), ('assigned', 'Assigned'), ('in_progress', 'In progress'),
             ('on_hold', 'On hold'), ('handed_over', 'With the Maintenance app'), ('done', 'Work done'), ('closed', 'Closed'),
             ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))


class WorkOrder(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=200)
    card = models.ForeignKey(AssetCard, on_delete=models.CASCADE, related_name='work_orders', verbose_name='asset')
    kind = models.CharField(max_length=12, choices=WO_KIND, default='corrective')
    activity = models.ForeignKey(ActivityType, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    plan = models.ForeignKey(MaintenancePlan, null=True, blank=True, on_delete=models.SET_NULL, related_name='work_orders')
    ticket = models.ForeignKey('Ticket', null=True, blank=True, on_delete=models.SET_NULL, related_name='work_orders')
    priority = models.CharField(max_length=8, choices=PRIORITY, default='normal')
    status = models.CharField(max_length=12, choices=WO_STATUS, default='open')
    description = models.TextField(blank=True)
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    technician = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    vendor = models.CharField(max_length=150, blank=True)
    vendor_partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='vendor (business partner)')   # v1.42.0
    scheduled_on = models.DateField('planned date', null=True, blank=True)
    due_date = models.DateField('finish by', null=True, blank=True)
    estimate = models.DecimalField('estimated cost', null=True, blank=True, **MONEY)
    out_of_service = models.BooleanField('asset out of service during the work', default=False)
    checklist = models.JSONField(default=list, blank=True)
    meter_reading = models.DecimalField('meter reading (km / hours)', max_digits=12, decimal_places=1, null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    work_note = models.TextField('work done', blank=True)
    labour_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    labour_rate = models.DecimalField(default=0, **MONEY)
    labour_cost = models.DecimalField(default=0, **MONEY)
    parts_cost = models.DecimalField(default=0, **MONEY)
    other_cost = models.DecimalField('vendor / other cost', default=0, **MONEY)
    total_cost = models.DecimalField(default=0, **MONEY)
    downtime_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approval_note = models.CharField(max_length=255, blank=True)
    hr_maintenance_id = models.IntegerField(null=True, blank=True)
    mnt_work_order_id = models.IntegerField('work order (Maintenance)', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('card',)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.number} {self.title}'


T_STATUS = (('new', 'New'), ('assigned', 'Assigned'), ('in_progress', 'In progress'), ('waiting', 'Waiting for the user'),
            ('resolved', 'Resolved – to confirm'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))
T_KIND = (('breakdown', 'Breakdown'), ('complaint', 'Complaint'), ('incident', 'Incident (damage, loss, safety)'), ('request', 'Service request'))
T_SOURCE = (('staff', 'Staff'), ('self', 'Employee'), ('email', 'E-mail'), ('scan', 'Scan'), ('mobile', 'Mobile'))


class Ticket(models.Model):
    """Helpdesk: a complaint, breakdown or incident about an asset."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    subject = models.CharField(max_length=200)
    card = models.ForeignKey(AssetCard, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets', verbose_name='asset')
    kind = models.CharField(max_length=10, choices=T_KIND, default='breakdown')
    priority = models.CharField(max_length=8, choices=PRIORITY, default='normal')
    status = models.CharField(max_length=12, choices=T_STATUS, default='new')
    description = models.TextField(blank=True)
    source = models.CharField(max_length=8, choices=T_SOURCE, default='staff')
    reporter = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='raised by')
    reporter_email = models.EmailField('raised by (e-mail)', blank=True)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    assignee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='assigned to')
    response_due = models.DateTimeField(null=True, blank=True)
    resolve_due = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    escalation = models.PositiveSmallIntegerField('escalation level', default=0)
    resolution = models.TextField(blank=True)
    repair_cost = models.DecimalField(default=0, **MONEY)
    rating = models.PositiveSmallIntegerField('user rating (1–5)', null=True, blank=True)
    feedback = models.CharField('user feedback', max_length=255, blank=True)
    mail_message_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('card', 'location')

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.number} {self.subject}'


class Part(models.Model):
    """Spare part or consumable."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    code = models.CharField(max_length=40, unique=True, blank=True)
    name = models.CharField(max_length=150)
    group = models.CharField('part group', max_length=80, blank=True)
    unit = models.CharField(max_length=20, default='pcs')
    manufacturer_no = models.CharField('manufacturer part no.', max_length=80, blank=True)
    supplier = models.CharField(max_length=150, blank=True)
    supplier_partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='supplier (business partner)')   # v1.42.0
    unit_cost = models.DecimalField('average cost', default=0, max_digits=14, decimal_places=4)
    min_qty = models.DecimalField('minimum stock', default=0, max_digits=12, decimal_places=2)
    reorder_qty = models.DecimalField('reorder quantity', default=0, max_digits=12, decimal_places=2)
    on_hand = models.DecimalField('in stock (all stores)', default=0, max_digits=12, decimal_places=2)
    categories = models.ManyToManyField(Category, blank=True, related_name='+', verbose_name='fits categories')
    cards = models.ManyToManyField(AssetCard, blank=True, related_name='parts', verbose_name='fits assets')
    is_consumable = models.BooleanField('consumable', default=False)
    is_active = models.BooleanField(default=True)

    item = models.ForeignKey('ZeoMasters.Product', null=True, blank=True, on_delete=models.SET_NULL, related_name='ast_parts', verbose_name='item (master)')   # v1.43.0 item master
    class Meta:
        ordering = ['code']

    def __str__(self):
        return f'{self.code} {self.name}'


class Stock(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name='stock')
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='+', verbose_name='store')
    qty = models.DecimalField(default=0, max_digits=12, decimal_places=2)
    bin = models.CharField('bin / shelf', max_length=40, blank=True)

    class Meta:
        unique_together = [('part', 'location')]
        ordering = ['part__code']


MOVE = (('receipt', 'Receipt'), ('issue', 'Issue'), ('transfer', 'Transfer'), ('return', 'Return to store'), ('adjust', 'Count adjustment'))


class StockMove(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=MOVE)
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='moves')
    qty = models.DecimalField(max_digits=12, decimal_places=2)
    from_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='from store')
    to_location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name='+', verbose_name='to store')
    unit_cost = models.DecimalField(default=0, max_digits=14, decimal_places=4)
    value = models.DecimalField(default=0, **MONEY)
    work_order = models.ForeignKey(WorkOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name='parts_used')
    ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    card = models.ForeignKey(AssetCard, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='for asset')
    reference = models.CharField('supplier invoice / reference', max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)
    by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('to_location', 'from_location')

    class Meta:
        ordering = ['-date', '-id']


PR_STATUS = (('requested', 'Requested'), ('approved', 'Approved'), ('issued', 'Issued'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))


class PartRequest(models.Model):
    """Inventory request: a technician asks the store for parts (for a work order); approval, then issue."""
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    requested_by = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    work_order = models.ForeignKey(WorkOrder, null=True, blank=True, on_delete=models.SET_NULL, related_name='part_requests')
    store = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='+')
    needed_by = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=PR_STATUS, default='requested')
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    BRANCH_FROM = ('store',)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.number


class PartRequestLine(models.Model):
    request = models.ForeignKey(PartRequest, on_delete=models.CASCADE, related_name='lines')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='+')
    qty = models.DecimalField(max_digits=12, decimal_places=2)
    issued = models.DecimalField(default=0, max_digits=12, decimal_places=2)

    class Meta:
        ordering = ['id']


SC_STATUS = (('draft', 'Draft'), ('counting', 'Counting'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))


class StockCount(models.Model):
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text=BRANCH_HELP)
    number = models.CharField(max_length=20, blank=True)
    store = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='+')
    status = models.CharField(max_length=10, choices=SC_STATUS, default='draft')
    note = models.CharField(max_length=255, blank=True)
    lines_counted = models.PositiveIntegerField(default=0)
    differences = models.PositiveIntegerField(default=0)
    value_difference = models.DecimalField(default=0, **MONEY)
    closed_at = models.DateTimeField(null=True, blank=True)

    BRANCH_FROM = ('store',)

    class Meta:
        ordering = ['-id']


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name='lines')
    part = models.ForeignKey(Part, on_delete=models.PROTECT, related_name='+')
    expected = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    counted = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    difference = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ['part__code']
        unique_together = [('count', 'part')]
