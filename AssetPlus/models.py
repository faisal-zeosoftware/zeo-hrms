"""
AssetPlus (v1.12.0) – full asset life cycle on top of the existing OrganisationManager asset tables.

The base tables (OrganisationManager.Asset / AssetType / AssetAllocation / AssetRequest) have no committed
migrations, so they are not changed. Everything new lives here in side tables keyed by the existing row ids
(integer columns, no foreign keys into the other apps):

* AssetTypeConfig  – per asset type: code prefix + next number, default depreciation
* AssetProfile     – per asset: code / tag, branch, location, department, purchase, warranty, insurance,
                     depreciation, label, notes and the extra status "lost" (base status choices cannot change)
* AssetFile        – photos and documents of an asset (or of one of its records)
* AllocationExtra  – per AssetAllocation: expected return, hand-over, employee acknowledgement
* SharedAllocation – an asset given to a location / department (shared asset, no single employee)
* AssetTransfer    – employee / location → employee / location, with approval
* MaintenanceSchedule / MaintenanceRecord – preventive plans (days / km / hours) and repairs
* AssetReturn      – return check list (condition, accessories, damage found, received by)
* AssetDamage / AssetLoss – incidents with responsibility, approval and recovery
* RecoveryInstalment – monthly payroll deductions (formula variable asset_recovery_amount)
* AssetDisposal    – sale / scrap / donation / write-off with book value and gain / loss
* AssetEvent       – one history timeline per asset
* ClearanceWaiver  – exit clearance released by HR although items are still open
* AssetPlusSettings – company settings (one row)
"""
from decimal import Decimal

from django.db import models

ZERO = Decimal('0.00')

DEPRECIATION = (('none', 'No depreciation'), ('straight_line', 'Straight line'), ('declining', 'Declining balance'))
RECOVERY_METHODS = (('none', 'No recovery'), ('payroll', 'Payroll deduction'), ('cash', 'Paid in cash'), ('waived', 'Waived'))
RESPONSIBILITY = (('company', 'Company'), ('employee', 'Employee'), ('shared', 'Shared (employee pays part)'), ('third_party', 'Third party / insurance'))


class AssetPlusSettings(models.Model):
    currency = models.CharField(max_length=3, default='AED')
    code_padding = models.PositiveSmallIntegerField(default=4)
    warranty_reminder_days = models.PositiveIntegerField(default=30)
    require_acknowledgement = models.BooleanField(default=True, help_text='Employees confirm in self-service that they received the asset')
    block_final_settlement = models.BooleanField(default=True, help_text='Final settlement cannot be processed while assets are still with the employee')
    max_instalments = models.PositiveSmallIntegerField(default=12)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        obj = cls.objects.order_by('id').first()
        return obj or cls.objects.create()


class AssetTypeConfig(models.Model):
    asset_type_id = models.IntegerField(unique=True)
    code_prefix = models.CharField(max_length=12, blank=True)
    next_number = models.PositiveIntegerField(default=1)
    depreciation_method = models.CharField(max_length=20, choices=DEPRECIATION, default='straight_line')
    useful_life_months = models.PositiveIntegerField(default=36)
    salvage_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    maintenance_interval_days = models.PositiveIntegerField(null=True, blank=True)


class AssetProfile(models.Model):
    EXTRA_STATUS = (('', 'None'), ('lost', 'Lost / stolen'))
    asset_id = models.IntegerField(unique=True)
    asset_code = models.CharField(max_length=40, unique=True)
    extra_status = models.CharField(max_length=12, choices=EXTRA_STATUS, blank=True, default='')
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    location = models.CharField(max_length=150, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=3, default='AED')
    vendor = models.CharField(max_length=150, blank=True)
    invoice_no = models.CharField(max_length=60, blank=True)
    invoice_file = models.FileField(upload_to='asset_plus/invoices/', null=True, blank=True)
    purchase_order = models.CharField(max_length=60, blank=True)
    warranty_start = models.DateField(null=True, blank=True)
    warranty_end = models.DateField(null=True, blank=True)
    warranty_reminder_days = models.PositiveIntegerField(null=True, blank=True)
    warranty_reminded_for = models.DateField(null=True, blank=True)
    insurance_provider = models.CharField(max_length=150, blank=True)
    insurance_policy_no = models.CharField(max_length=60, blank=True)
    insurance_expiry = models.DateField(null=True, blank=True)
    insurance_reminded_for = models.DateField(null=True, blank=True)
    depreciation_method = models.CharField(max_length=20, choices=DEPRECIATION, default='straight_line')
    depreciation_start = models.DateField(null=True, blank=True, help_text='Empty = purchase date')
    useful_life_months = models.PositiveIntegerField(default=36)
    salvage_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    declining_rate = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True,
                                         help_text='% per year; empty = double declining (200% ÷ life in years)')
    barcode = models.CharField(max_length=80, blank=True)
    meter_reading = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True, help_text='Current km / hours')
    notes = models.TextField(blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = (('view_assetbookvalue', 'Can see asset cost and book value'),)


class AssetFile(models.Model):
    KINDS = (('photo', 'Photo'), ('document', 'Document'), ('invoice', 'Invoice'), ('warranty', 'Warranty'), ('insurance', 'Insurance'),
             ('handover', 'Hand-over form'), ('return', 'Return form'), ('damage', 'Damage photo'), ('police_report', 'Police report'),
             ('maintenance', 'Maintenance'), ('disposal', 'Disposal'), ('other', 'Other'))
    asset_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=20, choices=KINDS, default='document')
    record_type = models.CharField(max_length=20, blank=True)    # allocation / damage / loss / maintenance / disposal / return
    record_id = models.IntegerField(null=True, blank=True)
    file = models.FileField(upload_to='asset_plus/files/%Y/%m/')
    name = models.CharField(max_length=255, blank=True)
    uploaded_by_id = models.IntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at', '-id']


class AllocationExtra(models.Model):
    ACK = (('pending', 'Waiting for the employee'), ('accepted', 'Accepted'), ('disputed', 'Accepted with remarks'), ('not_required', 'Not required'))
    allocation_id = models.IntegerField(unique=True)
    asset_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(db_index=True)
    expected_return_date = models.DateField(null=True, blank=True)
    accessories = models.CharField(max_length=255, blank=True)
    handover_condition = models.CharField(max_length=20, blank=True)
    handover_note = models.TextField(blank=True)
    handover_file = models.FileField(upload_to='asset_plus/handover/', null=True, blank=True)
    issued_by_id = models.IntegerField(null=True, blank=True)
    ack_status = models.CharField(max_length=15, choices=ACK, default='pending')
    ack_at = models.DateTimeField(null=True, blank=True)
    ack_condition_notes = models.TextField(blank=True)
    ack_by_id = models.IntegerField(null=True, blank=True)
    return_reminded_on = models.DateField(null=True, blank=True)
    transfer_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SharedAllocation(models.Model):
    TARGET = (('location', 'Location'), ('department', 'Department'))
    asset_id = models.IntegerField(db_index=True)
    target_type = models.CharField(max_length=12, choices=TARGET, default='location')
    branch_id = models.IntegerField(null=True, blank=True)
    location = models.CharField(max_length=150, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    custodian_employee_id = models.IntegerField(null=True, blank=True, help_text='Employee who looks after the shared asset')
    assigned_date = models.DateField()
    expected_return_date = models.DateField(null=True, blank=True)
    returned_date = models.DateField(null=True, blank=True)
    return_condition = models.CharField(max_length=20, blank=True)
    note = models.TextField(blank=True)
    issued_by_id = models.IntegerField(null=True, blank=True)
    transfer_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AssetTransfer(models.Model):
    PARTY = (('employee', 'Employee'), ('location', 'Location'), ('department', 'Department'), ('store', 'Store (unassigned)'))
    STATUS = (('pending', 'Waiting for approval'), ('approved', 'Approved'), ('completed', 'Completed'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))
    number = models.CharField(max_length=30, unique=True)
    asset_id = models.IntegerField(db_index=True)
    from_type = models.CharField(max_length=12, choices=PARTY)
    from_employee_id = models.IntegerField(null=True, blank=True)
    from_branch_id = models.IntegerField(null=True, blank=True)
    from_location = models.CharField(max_length=150, blank=True)
    from_department_id = models.IntegerField(null=True, blank=True)
    to_type = models.CharField(max_length=12, choices=PARTY)
    to_employee_id = models.IntegerField(null=True, blank=True)
    to_branch_id = models.IntegerField(null=True, blank=True)
    to_location = models.CharField(max_length=150, blank=True)
    to_department_id = models.IntegerField(null=True, blank=True)
    transfer_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS, default='pending')
    requested_by_id = models.IntegerField(null=True, blank=True)
    requested_by_employee_id = models.IntegerField(null=True, blank=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        permissions = (('approve_assettransfer', 'Can approve asset transfers'),)


class MaintenanceSchedule(models.Model):
    INTERVAL = (('days', 'Days'), ('km', 'Kilometres'), ('hours', 'Running hours'))
    asset_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=150)
    interval_type = models.CharField(max_length=8, choices=INTERVAL, default='days')
    interval_value = models.PositiveIntegerField()
    last_done_date = models.DateField(null=True, blank=True)
    last_done_reading = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    next_due_date = models.DateField(null=True, blank=True)
    next_due_reading = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    remind_days_before = models.PositiveIntegerField(default=7)
    vendor = models.CharField(max_length=150, blank=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    active = models.BooleanField(default=True)
    reminded_for = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MaintenanceRecord(models.Model):
    KIND = (('preventive', 'Preventive (scheduled)'), ('breakdown', 'Breakdown / repair'), ('inspection', 'Inspection'))
    STATUS = (('open', 'In maintenance'), ('closed', 'Completed'), ('cancelled', 'Cancelled'))
    RESULT = (('', '—'), ('fixed', 'Fixed'), ('not_fixed', 'Could not be fixed'), ('replaced', 'Part replaced'), ('ok', 'No fault found'))
    number = models.CharField(max_length=30, unique=True)
    asset_id = models.IntegerField(db_index=True)
    schedule_id = models.IntegerField(null=True, blank=True)
    damage_id = models.IntegerField(null=True, blank=True)
    kind = models.CharField(max_length=12, choices=KIND, default='breakdown')
    vendor = models.CharField(max_length=150, blank=True)
    description = models.TextField()
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    downtime_hours = models.DecimalField(max_digits=10, decimal_places=1, null=True, blank=True)
    reading = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    result = models.CharField(max_length=12, choices=RESULT, blank=True, default='')
    result_notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='open')
    status_before = models.CharField(max_length=20, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    closed_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-id']


class AssetReturn(models.Model):
    STATUS = (('requested', 'Return requested'), ('completed', 'Returned'), ('cancelled', 'Cancelled'))
    CONDITION = (('healthy', 'Healthy'), ('minor_damage', 'Minor damage'), ('major_damage', 'Major damage'))
    number = models.CharField(max_length=30, unique=True)
    asset_id = models.IntegerField(db_index=True)
    allocation_id = models.IntegerField(null=True, blank=True)
    shared_allocation_id = models.IntegerField(null=True, blank=True)
    employee_id = models.IntegerField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=10, choices=STATUS, default='requested')
    requested_by_id = models.IntegerField(null=True, blank=True)
    request_note = models.TextField(blank=True)
    preferred_date = models.DateField(null=True, blank=True)
    return_date = models.DateField(null=True, blank=True)
    condition = models.CharField(max_length=20, choices=CONDITION, blank=True)
    accessories_returned = models.BooleanField(default=True)
    missing_items = models.CharField(max_length=255, blank=True)
    damage_found = models.BooleanField(default=False)
    damage_description = models.TextField(blank=True)
    damage_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    damage_id = models.IntegerField(null=True, blank=True)
    data_wiped = models.BooleanField(default=False)
    received_by_id = models.IntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class _Incident(models.Model):
    """Shared fields of damage and loss: who is responsible, approval and recovery."""
    STATUS = (('reported', 'Waiting for approval'), ('approved', 'Approved'), ('recovering', 'Being recovered'), ('closed', 'Closed'),
              ('rejected', 'Rejected'))
    number = models.CharField(max_length=30, unique=True)
    asset_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(null=True, blank=True, db_index=True)
    allocation_id = models.IntegerField(null=True, blank=True)
    description = models.TextField()
    responsibility = models.CharField(max_length=12, choices=RESPONSIBILITY, default='company')
    recovery_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    recovery_method = models.CharField(max_length=10, choices=RECOVERY_METHODS, default='none')
    instalments = models.PositiveSmallIntegerField(default=1)
    first_deduction_month = models.DateField(null=True, blank=True, help_text='First day of the first payroll month')
    recovered_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=12, choices=STATUS, default='reported')
    source = models.CharField(max_length=12, default='hr')  # hr / ess / return
    reported_by_id = models.IntegerField(null=True, blank=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ['-id']


class AssetDamage(_Incident):
    SEVERITY = (('minor', 'Minor'), ('major', 'Major'), ('total', 'Beyond repair'))
    damage_date = models.DateField()
    severity = models.CharField(max_length=8, choices=SEVERITY, default='minor')
    repair_cost = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    return_id = models.IntegerField(null=True, blank=True)

    class Meta(_Incident.Meta):
        permissions = (('approve_assetdamage', 'Can approve asset damage and recovery'),)


class AssetLoss(_Incident):
    KIND = (('lost', 'Lost'), ('stolen', 'Stolen'))
    INVESTIGATION = (('open', 'Open'), ('investigating', 'Under investigation'), ('closed_found', 'Closed – asset found'),
                     ('closed_not_found', 'Closed – not found'))
    loss_date = models.DateField()
    kind = models.CharField(max_length=8, choices=KIND, default='lost')
    place = models.CharField(max_length=200, blank=True)
    police_report_no = models.CharField(max_length=60, blank=True)
    police_report_file = models.FileField(upload_to='asset_plus/police/', null=True, blank=True)
    investigation_status = models.CharField(max_length=20, choices=INVESTIGATION, default='open')
    investigation_notes = models.TextField(blank=True)
    status_before = models.CharField(max_length=20, blank=True)
    book_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    found_on = models.DateField(null=True, blank=True)

    class Meta(_Incident.Meta):
        permissions = (('approve_assetloss', 'Can approve lost / stolen assets and recovery'),)


class RecoveryInstalment(models.Model):
    STATUS = (('pending', 'To be deducted'), ('deducted', 'Deducted in payroll'), ('final_settlement', 'In final settlement'),
              ('cancelled', 'Cancelled'))
    source_type = models.CharField(max_length=8)   # damage / loss
    source_id = models.IntegerField()
    employee_id = models.IntegerField(db_index=True)
    month = models.DateField(help_text='First day of the payroll month')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=STATUS, default='pending')
    payroll_run_id = models.IntegerField(null=True, blank=True)
    payslip_id = models.IntegerField(null=True, blank=True)
    done_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['month', 'id']


class AssetDisposal(models.Model):
    METHOD = (('sale', 'Sale'), ('scrap', 'Scrap'), ('donate', 'Donation'), ('write_off', 'Write-off'))
    STATUS = (('pending', 'Waiting for approval'), ('approved', 'Disposed'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))
    number = models.CharField(max_length=30, unique=True)
    asset_id = models.IntegerField(db_index=True)
    method = models.CharField(max_length=10, choices=METHOD)
    disposal_date = models.DateField()
    value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, help_text='Sale price / scrap value received')
    buyer = models.CharField(max_length=150, blank=True)
    reason = models.TextField()
    book_value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    gain_loss = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    status_before = models.CharField(max_length=20, blank=True)
    requested_by_id = models.IntegerField(null=True, blank=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
        permissions = (('approve_assetdisposal', 'Can approve asset disposal'),)


class AssetEvent(models.Model):
    asset_id = models.IntegerField(db_index=True)
    event = models.CharField(max_length=30)
    summary = models.CharField(max_length=400)
    changes = models.JSONField(default=dict, blank=True)
    employee_id = models.IntegerField(null=True, blank=True, db_index=True)
    ref_type = models.CharField(max_length=20, blank=True)
    ref_id = models.IntegerField(null=True, blank=True)
    user_id = models.IntegerField(null=True, blank=True)
    user_name = models.CharField(max_length=150, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-at', '-id']


class ClearanceWaiver(models.Model):
    employee_id = models.IntegerField(db_index=True)
    reason = models.TextField()
    waived_by_id = models.IntegerField(null=True, blank=True)
    waived_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        permissions = (('waive_assetclearance', 'Can release exit clearance with assets open'),)
