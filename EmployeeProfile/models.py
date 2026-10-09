"""
v1.13.0 – Employee master extras (UAE identity, emergency contacts, dependants, WPS bank details, qualification
attestation, employment status / probation, employee code numbering).

EmpManagement has no committed migrations, so every table here is a side table keyed by the existing row id
(integer columns, no foreign keys into other apps). Validation lives in validators.py / services.py so the HR
screens, the import and the self-service app (SelfService, via services.apply_change) use the same rules.
"""
from django.db import models


class EmployeeIdentity(models.Model):
    TITLES = [('Mr', 'Mr'), ('Mrs', 'Mrs'), ('Ms', 'Ms'), ('Miss', 'Miss'), ('Dr', 'Dr'), ('Eng', 'Eng'), ('Prof', 'Prof')]
    VISA_TYPES = [('employment', 'Employment visa'), ('residence', 'Residence (family sponsored)'), ('investor', 'Investor / partner'),
                  ('golden', 'Golden visa'), ('green', 'Green visa'), ('freelance', 'Freelance permit'), ('mission', 'Mission / work permit'),
                  ('visit', 'Visit visa'), ('national', 'UAE / GCC national (no visa)'), ('other', 'Other')]
    MOHRE_TYPES = [('limited', 'Limited (fixed term)'), ('unlimited', 'Unlimited')]

    employee_id = models.IntegerField(unique=True)
    arabic_name = models.CharField(max_length=150, blank=True, default='')
    title = models.CharField(max_length=10, choices=TITLES, blank=True, default='')
    preferred_name = models.CharField(max_length=80, blank=True, default='')
    place_of_birth = models.CharField(max_length=80, blank=True, default='')   # v1.13.0 (0002)
    # passport
    passport_no = models.CharField(max_length=12, blank=True, default='', db_index=True)
    passport_issue_date = models.DateField(null=True, blank=True)
    passport_expiry_date = models.DateField(null=True, blank=True)
    passport_place_of_issue = models.CharField(max_length=80, blank=True, default='')
    passport_country_id = models.IntegerField(null=True, blank=True, help_text='Core.cntry_mstr')
    # residence visa
    visa_type = models.CharField(max_length=12, choices=VISA_TYPES, blank=True, default='')
    visa_number = models.CharField(max_length=30, blank=True, default='', help_text='entry permit / residence number')
    visa_uid = models.CharField(max_length=15, blank=True, default='', help_text='UID number (digits)')
    visa_file_no = models.CharField(max_length=30, blank=True, default='', help_text='file number, e.g. 201/2024/1234567')
    visa_issue_date = models.DateField(null=True, blank=True)
    visa_expiry_date = models.DateField(null=True, blank=True)
    visa_sponsor = models.CharField(max_length=120, blank=True, default='')
    # Emirates ID
    emirates_id = models.CharField(max_length=18, blank=True, default='', db_index=True, help_text='784-YYYY-NNNNNNN-N')
    emirates_id_issue_date = models.DateField(null=True, blank=True)
    emirates_id_expiry_date = models.DateField(null=True, blank=True)
    # labour (MOHRE)
    labour_card_no = models.CharField(max_length=20, blank=True, default='', db_index=True)
    labour_card_issue_date = models.DateField(null=True, blank=True)
    labour_card_expiry_date = models.DateField(null=True, blank=True)
    work_permit_no = models.CharField(max_length=20, blank=True, default='')
    work_permit_expiry_date = models.DateField(null=True, blank=True)
    mohre_contract_type = models.CharField(max_length=10, choices=MOHRE_TYPES, blank=True, default='')
    mohre_contract_no = models.CharField(max_length=30, blank=True, default='')
    mohre_contract_start = models.DateField(null=True, blank=True)
    mohre_contract_end = models.DateField(null=True, blank=True)
    # UAE driving licence (v1.13.0, 0002)
    EMIRATES = [('abu_dhabi', 'Abu Dhabi'), ('dubai', 'Dubai'), ('sharjah', 'Sharjah'), ('ajman', 'Ajman'),
                ('umm_al_quwain', 'Umm Al Quwain'), ('ras_al_khaimah', 'Ras Al Khaimah'), ('fujairah', 'Fujairah'), ('other', 'Other')]
    driving_licence_no = models.CharField(max_length=20, blank=True, default='', db_index=True)
    driving_licence_issue_date = models.DateField(null=True, blank=True)
    driving_licence_expiry_date = models.DateField(null=True, blank=True)
    driving_licence_emirate = models.CharField(max_length=16, choices=EMIRATES, blank=True, default='')
    driving_licence_categories = models.CharField(max_length=120, blank=True, default='', help_text='e.g. Light vehicle, Motorcycle')
    # medical insurance
    insurance_provider = models.CharField(max_length=80, blank=True, default='')
    insurance_card_no = models.CharField(max_length=40, blank=True, default='')
    insurance_expiry_date = models.DateField(null=True, blank=True)
    # link to the matching EmpManagement.Emp_Documents rows (so the existing expiry alerts / reports keep working)
    doc_links = models.JSONField(default=dict, blank=True, help_text='{passport|visa|emirates_id|labour_card|driving_licence: Emp_Documents id}')
    doc_problems = models.JSONField(default=dict, blank=True, help_text='last sync problem per kind')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f'Identity of employee {self.employee_id}'


class EmergencyContact(models.Model):
    employee_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=120)
    relation = models.CharField(max_length=40)
    mobile = models.CharField(max_length=24)
    alt_phone = models.CharField(max_length=24, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    address = models.CharField(max_length=255, blank=True, default='')
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['employee_id', '-is_primary', 'id']

    def __str__(self):
        return f'{self.name} ({self.relation})'


class DependentExtra(models.Model):
    GENDERS = [('M', 'Male'), ('F', 'Female'), ('O', 'Other')]
    family_id = models.IntegerField(unique=True, help_text='EmpManagement.emp_family')
    employee_id = models.IntegerField(db_index=True)
    gender = models.CharField(max_length=1, choices=GENDERS, blank=True, default='')
    nationality_id = models.IntegerField(null=True, blank=True, help_text='Core.Nationality')
    passport_no = models.CharField(max_length=12, blank=True, default='')
    passport_expiry_date = models.DateField(null=True, blank=True)
    emirates_id = models.CharField(max_length=18, blank=True, default='')
    emirates_id_expiry_date = models.DateField(null=True, blank=True)
    visa_number = models.CharField(max_length=30, blank=True, default='')
    visa_expiry_date = models.DateField(null=True, blank=True)
    insured = models.BooleanField(default=False)
    visa_sponsored = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)


class BankExtra(models.Model):
    MODES = [('wps', 'WPS (salary transfer)'), ('bank_transfer', 'Bank transfer (non-WPS)'), ('cash', 'Cash'), ('cheque', 'Cheque')]
    bank_detail_id = models.IntegerField(unique=True, help_text='EmpManagement.EmployeeBankDetail')
    employee_id = models.IntegerField(db_index=True)
    wps_agent = models.CharField(max_length=60, blank=True, default='', help_text='WPS agent / exchange house routing (agent ID)')
    payment_mode = models.CharField(max_length=14, choices=MODES, default='wps')
    is_primary = models.BooleanField(default=False)
    effective_from = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class QualificationExtra(models.Model):
    qualification_id = models.IntegerField(unique=True, help_text='EmpManagement.EmpQualification')
    employee_id = models.IntegerField(db_index=True)
    grade = models.CharField(max_length=40, blank=True, default='')
    country_id = models.IntegerField(null=True, blank=True, help_text='Core.cntry_mstr')
    attested = models.BooleanField(default=False, help_text='attested by the UAE Ministry of Foreign Affairs')
    attestation_date = models.DateField(null=True, blank=True)
    equivalency = models.BooleanField(default=False, help_text='equivalency certificate from the Ministry of Education')
    attachment = models.FileField(upload_to='qualification_attestation/', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class EmploymentInfo(models.Model):
    STATUS = [('active', 'Active'), ('probation', 'On probation'), ('on_notice', 'On notice'), ('left', 'Left'),
              ('terminated', 'Terminated'), ('absconded', 'Absconded'), ('retired', 'Retired')]
    PROBATION = [('on_probation', 'On probation'), ('extended', 'Extended'), ('confirmed', 'Confirmed'), ('failed', 'Not confirmed (failed)')]

    employee_id = models.IntegerField(unique=True)
    status = models.CharField(max_length=12, choices=STATUS, default='active')
    probation_end_date = models.DateField(null=True, blank=True)
    probation_status = models.CharField(max_length=12, choices=PROBATION, blank=True, default='')
    probation_days = models.PositiveIntegerField(null=True, blank=True, help_text='days the end date was worked out from')
    probation_source = models.CharField(max_length=20, blank=True, default='', help_text='employment_type | branch | manual')
    confirmed_on = models.DateField(null=True, blank=True)
    failed_on = models.DateField(null=True, blank=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True, default='')
    date_of_leaving = models.DateField(null=True, blank=True)
    leaving_reason = models.CharField(max_length=255, blank=True, default='')
    leaving_source = models.CharField(max_length=40, blank=True, default='', help_text='resignation:<id> | eos:<id> | probation | manual')
    last_reminded_on = models.DateField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)


class ProbationExtension(models.Model):
    employee_id = models.IntegerField(db_index=True)
    from_date = models.DateField(help_text='end date before the extension')
    to_date = models.DateField(help_text='new end date')
    reason = models.CharField(max_length=255)
    decided_by = models.IntegerField(null=True, blank=True, help_text='user id')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee_id', 'created_at', 'id']


class EmployeeCodeSetting(models.Model):
    branch_id = models.IntegerField(unique=True, null=True, blank=True, help_text='OrganisationManager.brnch_mstr; empty = company default')
    prefix = models.CharField(max_length=20, blank=True, default='')
    next_number = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=4)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['branch_id']

    def format(self, n=None):
        return f'{self.prefix}{str(self.next_number if n is None else n).zfill(self.padding or 1)}'
