"""
Organisation structure (v1.12.0): settings toggles, extra masters (locations, divisions, sections, cost centres, grades,
job positions, employment types), the employee's place in them, company-policy acknowledgements and country labour-law
rule sets.

Existing tables (employees, branches, departments, designations, categories, company policies) are referenced by their
integer id only – those apps have no committed migrations, so nothing here may depend on them.
Branch scoping of a master: `branch_ids` = [] means every branch, else the listed branch ids (like department ↔ branch).
"""
from django.db import models

FIELDS = ('locations', 'divisions', 'sections', 'cost_centers', 'grades', 'job_positions', 'employment_types', 'employee_categories')
DEFAULT_LABELS = {'locations': 'Location', 'divisions': 'Division', 'sections': 'Section', 'cost_centers': 'Cost centre',
                  'grades': 'Grade', 'job_positions': 'Job position', 'employment_types': 'Employment type',
                  'employee_categories': 'Category'}


class OrgSettings(models.Model):
    """One row per company: which org fields are used, their labels and whether the employee form requires them."""
    use_locations = models.BooleanField(default=False)
    use_divisions = models.BooleanField(default=False)
    use_sections = models.BooleanField(default=False)
    use_cost_centers = models.BooleanField(default=False)
    use_employee_categories = models.BooleanField(default=True, help_text='off = category hidden everywhere (data and leave-policy mapping are kept)')
    use_grades = models.BooleanField(default=False)
    use_job_positions = models.BooleanField(default=False)
    use_employment_types = models.BooleanField(default=False)
    labels = models.JSONField(default=dict, blank=True, help_text='{"sections": "Unit", ...}')
    mandatory = models.JSONField(default=dict, blank=True, help_text='{"sections": true, ...} on the employee form')
    # v1.30.0 location level: locations become a level under the branch – switcher, access, numbering, record tagging
    location_control = models.BooleanField(default=False, help_text='Location-level control on (off = locations are a plain master)')
    location_required = models.BooleanField(default=False, help_text='A new transaction must have a location')
    location_numbering = models.BooleanField(default=False, help_text='Document numbers from the location series when one exists')
    location_restrict = models.BooleanField(default=False, help_text='Users see only the records of their locations')
    location_models = models.JSONField(default=list, blank=True, help_text='["app.model", …] record types with a location; [] = the default set')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'organisation settings'
        verbose_name_plural = 'organisation settings'

    @classmethod
    def get(cls):
        row = cls.objects.order_by('id').first()
        return row or cls.objects.create()

    def is_on(self, field):
        return bool(getattr(self, 'use_' + field, False))

    def label(self, field):
        v = (self.labels or {}).get(field)
        return str(v).strip() if v and str(v).strip() else DEFAULT_LABELS.get(field, field)

    def active_fields(self):
        return [f for f in FIELDS if f != 'employee_categories' and self.is_on(f)]


class Master(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    branch_ids = models.JSONField(default=list, blank=True, help_text='[] = all branches')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        abstract = True
        ordering = ['code']

    def __str__(self):
        return f'{self.name} ({self.code})'


class Location(Master):
    address = models.TextField(blank=True, default='')
    city = models.CharField(max_length=80, blank=True, default='')
    country_id = models.IntegerField(null=True, blank=True, help_text='Core.cntry_mstr')
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # v1.30.0 location level: a location belongs to one branch (required when location control is on)
    branch_id = models.IntegerField(null=True, blank=True, help_text='OrganisationManager.brnch_mstr')
    manager_user_id = models.IntegerField(null=True, blank=True, help_text='UserManagement.CustomUser – location manager')
    is_default = models.BooleanField(default=False, help_text='Default location for new employees of the branch')
    # v1.31.0 – optional site details (all optional)
    LOCATION_TYPES = [('office', 'Office'), ('site', 'Project site'), ('camp', 'Labour camp / accommodation'), ('warehouse', 'Warehouse / yard'),
                      ('shop', 'Shop / outlet'), ('client', 'Client premises'), ('other', 'Other')]
    location_type = models.CharField(max_length=12, choices=LOCATION_TYPES, blank=True, default='')
    phone = models.CharField(max_length=40, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    po_box = models.CharField(max_length=40, blank=True, default='')
    geofence_radius_m = models.PositiveIntegerField(null=True, blank=True, help_text='Attendance geofence around the GPS point (metres)')
    timezone = models.CharField(max_length=50, blank=True, default='')
    shift_id = models.IntegerField(null=True, blank=True, help_text='ShiftPlanner shift used by default at this location')
    cost_center = models.ForeignKey('CostCenter', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    project_id = models.IntegerField(null=True, blank=True, help_text='ProjectManagement.project – a construction / service site')
    customer_name = models.CharField(max_length=150, blank=True, default='', help_text='Client at whose premises the site is')
    in_charge_employee_id = models.IntegerField(null=True, blank=True, help_text='EmpManagement.emp_master – site in-charge')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True, help_text='Temporary site: planned close')
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text='Beds / seats / headcount the site can hold')
    permit_no = models.CharField(max_length=60, blank=True, default='', help_text='Municipality / civil defence / site permit')
    permit_expiry = models.DateField(null=True, blank=True)

    class Meta(Master.Meta):
        pass


class Division(Master):
    head_employee_id = models.IntegerField(null=True, blank=True)

    class Meta(Master.Meta):
        pass


class DivisionDepartment(models.Model):
    """A department belongs to at most one division."""
    division = models.ForeignKey(Division, on_delete=models.CASCADE, related_name='departments')
    department_id = models.IntegerField(unique=True)


class Section(Master):
    department_id = models.IntegerField(help_text='OrganisationManager.dept_master')
    head_employee_id = models.IntegerField(null=True, blank=True)

    class Meta(Master.Meta):
        pass


class CostCenter(Master):
    department_id = models.IntegerField(null=True, blank=True)
    manager_id = models.IntegerField(null=True, blank=True, help_text='employee id')
    expense_cost_center_id = models.IntegerField(null=True, blank=True, help_text='ExpenseManagement.CostCenter kept in step')

    class Meta(Master.Meta):
        pass


class Grade(Master):
    level = models.IntegerField(default=1, help_text='rank, 1 = lowest')
    salary_min = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    salary_max = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default='AED')
    designation_ids = models.JSONField(default=list, blank=True, help_text='[] = any designation')
    benefits = models.TextField(blank=True, default='')

    class Meta(Master.Meta):
        ordering = ['level', 'code']


class JobPosition(Master):
    department_id = models.IntegerField(null=True, blank=True)
    designation_id = models.IntegerField(null=True, blank=True)
    grade = models.ForeignKey(Grade, null=True, blank=True, on_delete=models.PROTECT, related_name='positions')
    reports_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='children')
    headcount_budget = models.PositiveIntegerField(default=1)

    class Meta(Master.Meta):
        pass


class EmploymentType(Master):
    counts_for_gratuity = models.BooleanField(default=True)
    probation_days = models.PositiveIntegerField(default=0)
    has_end_date = models.BooleanField(default=False, help_text='contract / temporary: an end date is expected')

    class Meta(Master.Meta):
        pass


class EmployeeOrg(models.Model):
    """The employee's place in the extra structure (side table of EmpManagement.emp_master)."""
    employee_id = models.IntegerField(unique=True)
    location = models.ForeignKey(Location, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    division = models.ForeignKey(Division, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    section = models.ForeignKey(Section, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    cost_center = models.ForeignKey(CostCenter, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    grade = models.ForeignKey(Grade, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    job_position = models.ForeignKey(JobPosition, null=True, blank=True, on_delete=models.PROTECT, related_name='holders')
    employment_type = models.ForeignKey(EmploymentType, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    contract_end_date = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)


# ------------------------------------------------------------------ company policies
class PolicyVersion(models.Model):
    """Version of an OrganisationManager.CompanyPolicy: a new file or a change to the policy starts a new version
    that everyone it applies to must acknowledge again."""
    policy_id = models.IntegerField(unique=True)
    version = models.PositiveIntegerField(default=1)
    fingerprint = models.CharField(max_length=300, blank=True, default='')
    requires_ack = models.BooleanField(default=True)
    due_days = models.PositiveIntegerField(default=7, help_text='days to acknowledge before reminders')
    version_date = models.DateTimeField(null=True, blank=True)
    last_reminded_at = models.DateTimeField(null=True, blank=True)


class PolicyAcknowledgement(models.Model):
    policy_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(db_index=True)
    version = models.PositiveIntegerField(default=1)
    acknowledged_at = models.DateTimeField(auto_now_add=True)
    ip = models.CharField(max_length=60, blank=True, default='')
    user_id = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = [('policy_id', 'employee_id', 'version')]
        ordering = ['-acknowledged_at']


# ------------------------------------------------------------------ country labour-law rule sets
class CountryPolicy(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    country_code = models.CharField(max_length=2, help_text='ISO 3166 alpha-2, e.g. AE')
    country_name = models.CharField(max_length=80)
    law_reference = models.CharField(max_length=300, blank=True, default='')
    weekend_days = models.JSONField(default=list, blank=True, help_text='0=Mon … 6=Sun')
    daily_hours = models.DecimalField(max_digits=4, decimal_places=2, default=8)
    weekly_hours = models.DecimalField(max_digits=5, decimal_places=2, default=48)
    ramadan_daily_hours = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    annual_leave = models.JSONField(default=dict, blank=True,
                                    help_text='{"first_year_after_months": 6, "first_year_days_per_month": 2, "days": 30, "steps": [{"after_years": 5, "days": 30}], "basis": "calendar"}')
    sick_leave = models.JSONField(default=list, blank=True, help_text='[[days, pay %], ...] in order')
    maternity = models.JSONField(default=dict, blank=True, help_text='{"days": 60, "full_pay_days": 45, "half_pay_days": 15}')
    paternity_days = models.PositiveIntegerField(default=0)
    gratuity = models.JSONField(default=dict, blank=True,
                                help_text='{"basis": "basic", "slabs": [{"from_year": 1, "to_year": 5, "days": 21}], "cap_years_of_pay": 2, "min_service_years": 1, "by_contract": {...}}')
    overtime = models.JSONField(default=dict, blank=True, help_text='{"normal": 125, "night": 150, "rest_day": 150, "holiday": 150, "max_hours_per_day": 2}')
    notice_period = models.JSONField(default=dict, blank=True, help_text='{"min_days": 30, "max_days": 90}')
    probation_max_months = models.PositiveIntegerField(default=6)
    public_holiday_source = models.CharField(max_length=200, blank=True, default='')
    wps_notes = models.TextField(blank=True, default='')
    notes = models.TextField(blank=True, default='')
    active = models.BooleanField(default=True)
    is_standard = models.BooleanField(default=False, help_text='loaded from the standard GCC set')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['country_name', 'code']
        verbose_name_plural = 'country policies'

    def __str__(self):
        return self.name


class BranchCountryPolicy(models.Model):
    branch_id = models.IntegerField(unique=True)
    country_policy = models.ForeignKey(CountryPolicy, on_delete=models.CASCADE, related_name='branches')
    applied_at = models.DateTimeField(auto_now=True)
    applied_by_id = models.IntegerField(null=True, blank=True)


class PositionRequisition(models.Model):
    """Manpower requisitions raised from a job position (RecruitmentManagement.ManpowerRequisition id)."""
    position = models.ForeignKey(JobPosition, on_delete=models.CASCADE, related_name='requisitions')
    requisition_id = models.IntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


# ------------------------------------------------------------------ v1.30.0 location level
class UserLocationAccess(models.Model):
    """The locations a user may work in (empty = every location of the user's branches)."""
    user_id = models.IntegerField(unique=True, help_text='UserManagement.CustomUser')
    location_ids = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)


class RecordLocation(models.Model):
    """Which location a transaction belongs to (side table – the transaction apps have no committed migrations)."""
    model = models.CharField(max_length=80, help_text='app_label.model_name')
    record_id = models.IntegerField()
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='records')
    set_at = models.DateTimeField(auto_now=True)
    set_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = [('model', 'record_id')]
        indexes = [models.Index(fields=['location', 'model'])]


class LocationNumbering(models.Model):
    """Document numbering series per location and document type (falls back to the branch series when missing)."""
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='numbering')
    type = models.CharField(max_length=50)
    automatic_numbering = models.BooleanField(default=True)
    prefix = models.CharField(max_length=50)
    suffix = models.CharField(max_length=50, blank=True, default='')
    current_number = models.IntegerField(default=0)
    total_length = models.IntegerField(default=10)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = [('location', 'type')]
        ordering = ['location_id', 'type']

    def __str__(self):
        return f'{self.location.name} – {self.type}'

    def get_next_number(self):
        """Same rules as OrganisationManager.DocumentNumbering.get_next_number (prefix+suffix-number, fixed length)."""
        from django.core.exceptions import ValidationError
        from django.db import transaction
        from django.utils import timezone
        today = timezone.now().date()
        if self.start_date and self.end_date and not (self.start_date <= today <= self.end_date):
            raise ValidationError('Document number cannot be generated outside the valid date range.')
        with transaction.atomic():
            row = LocationNumbering.objects.select_for_update().get(id=self.id)
            base = f'{row.prefix or ""}{row.suffix or ""}'
            space = row.total_length - len(base) - 1
            if space < 1:
                raise ValidationError('Invalid total length configuration.')
            nxt = row.current_number + 1
            if nxt > int('9' * space):
                raise ValidationError('Document number limit reached.')
            row.current_number = nxt
            row.save(update_fields=['current_number'])
            return f'{base}-{str(nxt).zfill(space)}'


# ------------------------------------------------------------------ v1.31.0 company and branch profiles (optional legal details)
class CompanyProfile(models.Model):
    """One row per company (tenant schema): legal / registration details, signatory, stamp, bank accounts. Every field is
    optional; the captions follow the company's country pack (Trade licence / CR / CIN, TRN / VAT / GSTIN, GPSSA / GOSI / EPFO …).
    The core company row (UserManagement.company, public schema) is untouched."""
    COMPANY_TYPES = [('llc', 'Limited liability company (LLC)'), ('fz', 'Free-zone company (FZE / FZCO / FZ-LLC)'), ('branch', 'Branch of a foreign / mainland company'),
                     ('sole', 'Sole establishment / proprietorship'), ('psc', 'Private / public shareholding company'), ('partnership', 'Partnership'),
                     ('pvt', 'Private limited company'), ('public', 'Public limited company'), ('llp', 'LLP'), ('govt', 'Government / semi-government'),
                     ('ngo', 'Non-profit'), ('other', 'Other')]
    JURISDICTIONS = [('mainland', 'Mainland / onshore'), ('freezone', 'Free zone'), ('offshore', 'Offshore'), ('sez', 'Special economic zone'), ('', '–')]
    legal_name = models.CharField(max_length=200, blank=True, default='')
    name_local = models.CharField(max_length=200, blank=True, default='', help_text='Name in the local language (Arabic / Hindi …)')
    company_type = models.CharField(max_length=12, choices=COMPANY_TYPES, blank=True, default='')
    jurisdiction = models.CharField(max_length=10, blank=True, default='')
    authority = models.CharField(max_length=120, blank=True, default='', help_text='Licensing authority / free zone / registrar (DED, JAFZA, DMCC, MCI, MCA …)')
    licence_no = models.CharField(max_length=60, blank=True, default='')
    licence_issue = models.DateField(null=True, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    establishment_no = models.CharField(max_length=60, blank=True, default='', help_text='Labour-authority establishment / establishment card number')
    establishment_expiry = models.DateField(null=True, blank=True)
    tax_id = models.CharField(max_length=40, blank=True, default='', help_text='VAT / TRN / GSTIN')
    tax_reg_date = models.DateField(null=True, blank=True)
    corporate_tax_no = models.CharField(max_length=40, blank=True, default='', help_text='Corporate tax registration (UAE) / PAN (India)')
    pension_reg_no = models.CharField(max_length=60, blank=True, default='', help_text='Social-security / pension employer registration (GPSSA, GOSI, GRSIA, PASI, SIO, PIFSS, EPFO)')
    insurance_reg_no = models.CharField(max_length=60, blank=True, default='', help_text='Second scheme (ESIC, unemployment insurance …)')
    chamber_no = models.CharField(max_length=60, blank=True, default='', help_text='Chamber of commerce membership')
    extra_regs = models.JSONField(default=dict, blank=True, help_text='{key: value} country-specific registrations (TAN, PT, LIN …)')
    phone = models.CharField(max_length=40, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    website = models.CharField(max_length=120, blank=True, default='')
    po_box = models.CharField(max_length=40, blank=True, default='')
    contact_person = models.CharField(max_length=120, blank=True, default='')
    signatory_name = models.CharField(max_length=120, blank=True, default='')
    signatory_designation = models.CharField(max_length=120, blank=True, default='')
    signature = models.ImageField(upload_to='company/', null=True, blank=True)
    stamp = models.ImageField(upload_to='company/', null=True, blank=True)
    letterhead_header = models.TextField(blank=True, default='', help_text='Text / HTML printed at the top of letters')
    letterhead_footer = models.TextField(blank=True, default='')
    fiscal_year_start_month = models.PositiveSmallIntegerField(null=True, blank=True, help_text='1 = January … 4 = April (India)')
    week_start = models.PositiveSmallIntegerField(null=True, blank=True, help_text='0 = Monday … 6 = Sunday')
    bank_accounts = models.JSONField(default=list, blank=True, help_text='[{bank, account_name, account_no, iban, swift, currency, wps: true}]')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class BranchProfile(models.Model):
    """Optional details of a branch (side table of OrganisationManager.brnch_mstr)."""
    BRANCH_TYPES = [('head', 'Head office'), ('branch', 'Branch office'), ('site', 'Site office'), ('warehouse', 'Warehouse / yard'), ('shop', 'Shop / outlet'), ('other', 'Other')]
    branch_id = models.IntegerField(unique=True)
    branch_type = models.CharField(max_length=10, choices=BRANCH_TYPES, blank=True, default='')
    name_local = models.CharField(max_length=200, blank=True, default='')
    manager_user_id = models.IntegerField(null=True, blank=True)
    hr_contact_user_id = models.IntegerField(null=True, blank=True)
    licence_no = models.CharField(max_length=60, blank=True, default='', help_text='Own trade licence / CR of the branch, if any')
    licence_expiry = models.DateField(null=True, blank=True)
    establishment_no = models.CharField(max_length=60, blank=True, default='', help_text='Own establishment card / labour file of the branch')
    establishment_expiry = models.DateField(null=True, blank=True)
    tax_id = models.CharField(max_length=40, blank=True, default='', help_text='Separate VAT / GST registration of the branch (India: state GSTIN)')
    extra_regs = models.JSONField(default=dict, blank=True)
    cost_center = models.ForeignKey('CostCenter', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    timezone = models.CharField(max_length=50, blank=True, default='')
    is_default = models.BooleanField(default=False)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    po_box = models.CharField(max_length=40, blank=True, default='')
    bank_accounts = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
