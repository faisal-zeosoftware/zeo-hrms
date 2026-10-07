from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class RequisitionApprovalLevel(models.Model):
    """Approval chain for manpower requisitions (same idea as LoanApprovalLevels)."""
    level = models.PositiveIntegerField(unique=True)
    role = models.CharField(max_length=80, help_text="e.g. Department Head, HR Manager, Finance, CEO")
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='requisition_approval_levels')
    only_above_budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                            help_text="Only required when the maximum salary is above this amount")

    class Meta:
        ordering = ['level']

    def __str__(self):
        return f"Level {self.level} - {self.role}"


class ManpowerRequisition(models.Model):
    TYPE_CHOICES = [('new', 'New position (budgeted)'), ('new_unbudgeted', 'New position (not budgeted)'), ('replacement', 'Replacement')]
    EMPLOYMENT_CHOICES = [('full_time', 'Full time'), ('part_time', 'Part time'), ('contract', 'Fixed-term contract'), ('intern', 'Internship')]
    STATUS_CHOICES = [('draft', 'Draft'), ('pending', 'Pending approval'), ('approved', 'Approved'), ('rejected', 'Rejected'),
                      ('on_hold', 'On hold'), ('closed', 'Closed')]

    document_number = models.CharField(max_length=30, unique=True, blank=True)
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.SET_NULL, null=True, blank=True, related_name='manpower_requisitions')
    department = models.ForeignKey('OrganisationManager.dept_master', on_delete=models.PROTECT, related_name='manpower_requisitions')
    designation = models.ForeignKey('OrganisationManager.desgntn_master', on_delete=models.PROTECT, related_name='manpower_requisitions')
    position_title = models.CharField(max_length=150)
    headcount = models.PositiveIntegerField(default=1)
    requisition_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='new')
    replacing_employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='replacement_requisitions')
    employment_type = models.CharField(max_length=20, choices=EMPLOYMENT_CHOICES, default='full_time')
    min_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Monthly total package (AED)")
    max_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    work_location = models.CharField(max_length=150, blank=True, null=True)
    target_joining_date = models.DateField(null=True, blank=True)
    is_emiratisation = models.BooleanField(default=False, help_text="Nafis / Emiratisation position")
    reporting_to = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='requisitions_reporting_to')
    justification = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    requested_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='manpower_requisitions')
    submitted_on = models.DateTimeField(null=True, blank=True)
    approved_on = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        if self.min_salary and self.max_salary and self.min_salary > self.max_salary:
            raise ValidationError("Minimum salary cannot be greater than maximum salary.")
        if self.requisition_type == 'replacement' and not self.replacing_employee_id:
            raise ValidationError("Select the employee being replaced.")

    def save(self, *args, **kwargs):
        if not self.document_number:
            from zeo.module_helpers import next_document_number
            self.document_number = next_document_number(ManpowerRequisition, 'document_number', 'MRF')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.document_number} - {self.position_title}"


class RequisitionApproval(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('skipped', 'Skipped')]
    requisition = models.ForeignKey(ManpowerRequisition, on_delete=models.CASCADE, related_name='approvals')
    level = models.PositiveIntegerField()
    role = models.CharField(max_length=80)
    approver = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='requisition_approvals')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    comments = models.TextField(blank=True, null=True)
    acted_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='requisition_approvals_acted')
    acted_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['requisition', 'level']
        unique_together = ('requisition', 'level')


class JobOpening(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published'), ('interviewing', 'Interviewing'),
                      ('on_hold', 'On hold'), ('filled', 'Filled'), ('closed', 'Closed')]
    CHANNEL_CHOICES = ['Careers page', 'LinkedIn', 'Bayt', 'Naukrigulf', 'Indeed', 'Nafis portal', 'Dubizzle Jobs', 'Referral', 'Internal only']

    job_code = models.CharField(max_length=30, unique=True, blank=True)
    requisition = models.ForeignKey(ManpowerRequisition, on_delete=models.SET_NULL, null=True, blank=True, related_name='job_openings')
    title = models.CharField(max_length=150)
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.SET_NULL, null=True, blank=True, related_name='job_openings')
    department = models.ForeignKey('OrganisationManager.dept_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='job_openings')
    designation = models.ForeignKey('OrganisationManager.desgntn_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='job_openings')
    openings = models.PositiveIntegerField(default=1)
    employment_type = models.CharField(max_length=20, choices=ManpowerRequisition.EMPLOYMENT_CHOICES, default='full_time')
    location = models.CharField(max_length=150, blank=True, null=True)
    experience_min_years = models.PositiveIntegerField(default=0)
    min_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    description = models.TextField()
    requirements = models.TextField(blank=True, null=True)
    channels = models.JSONField(default=list, blank=True, help_text="List of publishing channels")
    closing_date = models.DateField(null=True, blank=True)
    is_emiratisation = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    published_on = models.DateTimeField(null=True, blank=True)
    hiring_manager = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='job_openings_managed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        if self.requisition_id and self.requisition.status != 'approved':
            raise ValidationError("The manpower requisition must be approved first.")
        if self.min_salary and self.max_salary and self.min_salary > self.max_salary:
            raise ValidationError("Minimum salary cannot be greater than maximum salary.")

    def save(self, *args, **kwargs):
        if not self.job_code:
            from zeo.module_helpers import next_document_number
            self.job_code = next_document_number(JobOpening, 'job_code', 'JOB')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.job_code} - {self.title}"


class Candidate(models.Model):
    VISA_CHOICES = [('uae_national', 'UAE National'), ('gcc_national', 'GCC National'), ('own_visa', 'Own / family visa'),
                    ('employment_visa', 'Employment visa (needs transfer)'), ('visit_visa', 'Visit visa'),
                    ('outside_uae', 'Outside UAE - needs sponsorship'), ('golden_visa', 'Golden visa')]
    SOURCE_CHOICES = [('careers', 'Careers page'), ('linkedin', 'LinkedIn'), ('bayt', 'Bayt'), ('naukrigulf', 'Naukrigulf'),
                      ('indeed', 'Indeed'), ('nafis', 'Nafis portal'), ('referral', 'Employee referral'),
                      ('agency', 'Recruitment agency'), ('walk_in', 'Walk-in'), ('other', 'Other')]

    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60, blank=True, null=True)
    email = models.EmailField()
    phone = models.CharField(max_length=30, blank=True, null=True)
    gender = models.CharField(max_length=1, choices=[('M', 'Male'), ('F', 'Female'), ('O', 'Other')], blank=True, null=True)
    date_of_birth = models.DateField(null=True, blank=True)
    nationality = models.ForeignKey('Core.Nationality', on_delete=models.SET_NULL, null=True, blank=True, related_name='candidates')
    current_location = models.CharField(max_length=100, blank=True, null=True)
    current_employer = models.CharField(max_length=150, blank=True, null=True)
    current_designation = models.CharField(max_length=150, blank=True, null=True)
    experience_years = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    notice_period_days = models.PositiveIntegerField(default=30)
    current_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    expected_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    visa_status = models.CharField(max_length=20, choices=VISA_CHOICES, default='outside_uae')
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='careers')
    referred_by = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='referred_candidates')
    skills = models.TextField(blank=True, null=True)
    languages = models.CharField(max_length=200, blank=True, null=True)
    cv = models.FileField(upload_to='recruitment/cv/', null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    consent_to_retain = models.BooleanField(default=False, help_text="Candidate agreed that we keep the CV for future roles")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def full_name(self):
        return " ".join(p for p in [self.first_name, self.last_name] if p)

    def __str__(self):
        return self.full_name


class CandidateDocument(models.Model):
    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=120, help_text="e.g. Passport copy, Emirates ID copy, Degree certificate")
    file = models.FileField(upload_to='recruitment/documents/')
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Application(models.Model):
    STAGES = [('applied', 'Applied'), ('screening', 'Screening'), ('interview', 'Interview'), ('offer', 'Offer'),
              ('hired', 'Hired'), ('rejected', 'Rejected'), ('withdrawn', 'Withdrawn')]
    FLOW = ['applied', 'screening', 'interview', 'offer', 'hired']

    candidate = models.ForeignKey(Candidate, on_delete=models.CASCADE, related_name='applications')
    job = models.ForeignKey(JobOpening, on_delete=models.CASCADE, related_name='applications')
    stage = models.CharField(max_length=20, choices=STAGES, default='applied')
    screening_score = models.PositiveSmallIntegerField(null=True, blank=True, help_text="0-100")
    rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1-5 stars")
    rejection_reason = models.CharField(max_length=255, blank=True, null=True)
    screened_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='screened_applications')
    applied_on = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('candidate', 'job')
        ordering = ['-applied_on']

    def clean(self):
        if self.rating is not None and not 1 <= self.rating <= 5:
            raise ValidationError("Rating must be 1 to 5.")
        if self.screening_score is not None and self.screening_score > 100:
            raise ValidationError("Screening score must be 0 to 100.")

    def __str__(self):
        return f"{self.candidate} → {self.job}"


class ApplicationStageHistory(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='history')
    from_stage = models.CharField(max_length=20, blank=True, null=True)
    to_stage = models.CharField(max_length=20)
    note = models.CharField(max_length=255, blank=True, null=True)
    changed_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']


class Interview(models.Model):
    MODE_CHOICES = [('office', 'In office'), ('online', 'Online (Teams / Zoom)'), ('phone', 'Phone')]
    STATUS_CHOICES = [('scheduled', 'Scheduled'), ('completed', 'Completed'), ('cancelled', 'Cancelled'), ('no_show', 'No-show')]
    RECOMMENDATION_CHOICES = [('proceed', 'Proceed'), ('hold', 'Hold'), ('reject', 'Reject')]

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='interviews')
    round_name = models.CharField(max_length=80, default='Round 1 - Technical')
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=60)
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='office')
    location_or_link = models.CharField(max_length=255, blank=True, null=True)
    panel = models.ManyToManyField('EmpManagement.emp_master', blank=True, related_name='interview_panels')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    overall_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    recommendation = models.CharField(max_length=10, choices=RECOMMENDATION_CHOICES, blank=True, null=True)
    comments = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_at']

    def recompute(self):
        scores = list(self.scores.all())
        total_w = sum((s.weight for s in scores), Decimal('0'))
        if total_w:
            self.overall_score = (sum((s.weight * s.score for s in scores), Decimal('0')) / total_w).quantize(Decimal('0.01'))
        else:
            self.overall_score = None


class InterviewScore(models.Model):
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name='scores')
    criterion = models.CharField(max_length=120)
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=25)
    score = models.PositiveSmallIntegerField(help_text="1-5")

    def clean(self):
        if not 1 <= (self.score or 0) <= 5:
            raise ValidationError("Score must be 1 to 5.")


class Offer(models.Model):
    CONTRACT_CHOICES = [('limited', 'Limited (fixed-term)'), ('unlimited', 'Unlimited')]
    STATUS_CHOICES = [('draft', 'Draft'), ('pending_approval', 'Pending approval'), ('approved', 'Approved'),
                      ('sent', 'Sent to candidate'), ('accepted', 'Accepted'), ('declined', 'Declined'),
                      ('expired', 'Expired'), ('withdrawn', 'Withdrawn'), ('joined', 'Joined')]

    document_number = models.CharField(max_length=30, unique=True, blank=True)
    application = models.OneToOneField(Application, on_delete=models.CASCADE, related_name='offer')
    position_title = models.CharField(max_length=150)
    department = models.ForeignKey('OrganisationManager.dept_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers')
    designation = models.ForeignKey('OrganisationManager.desgntn_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers')
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers')
    category = models.ForeignKey('OrganisationManager.ctgry_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers')
    reporting_manager = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers_reporting')
    contract_type = models.CharField(max_length=20, choices=CONTRACT_CHOICES, default='limited')
    contract_years = models.PositiveSmallIntegerField(default=2)
    probation_months = models.PositiveSmallIntegerField(default=6)
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    housing_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transport_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    other_allowance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    annual_leave_days = models.PositiveSmallIntegerField(default=30)
    air_ticket = models.CharField(max_length=150, blank=True, null=True, default='Annual economy ticket to home country')
    medical_insurance = models.CharField(max_length=150, blank=True, null=True)
    joining_date = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    terms = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    approved_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers_approved')
    approved_on = models.DateTimeField(null=True, blank=True)
    sent_on = models.DateTimeField(null=True, blank=True)
    responded_on = models.DateTimeField(null=True, blank=True)
    decline_reason = models.CharField(max_length=255, blank=True, null=True)
    employee = models.OneToOneField('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='recruitment_offer')
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='offers_created')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def total_monthly(self):
        return (self.basic_salary or 0) + (self.housing_allowance or 0) + (self.transport_allowance or 0) + (self.other_allowance or 0)

    def clean(self):
        if self.basic_salary is not None and self.basic_salary <= 0:
            raise ValidationError("Basic salary must be greater than zero.")
        if self.valid_until and self.valid_until < timezone.localdate() and self.status in ('draft', 'pending_approval'):
            raise ValidationError("Offer validity date is in the past.")

    def save(self, *args, **kwargs):
        if not self.document_number:
            from zeo.module_helpers import next_document_number
            self.document_number = next_document_number(Offer, 'document_number', 'OFR')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.document_number} - {self.application.candidate}"


DEFAULT_VISA_STEPS = [
    'Offer letter signed', 'MOHRE offer letter signed', 'Work permit / entry permit issued',
    'Entry to UAE / change of status', 'Medical fitness test', 'Emirates ID biometrics',
    'Labour contract signed (MOHRE)', 'Residence visa stamped', 'Medical insurance enrolled',
]
NATIONAL_STEPS = ['Offer letter signed', 'MOHRE / Nafis registration', 'Labour contract signed (MOHRE)', 'Medical insurance enrolled']

DEFAULT_ONBOARDING_TASKS = [
    ('IT', 'Laptop and company email account'), ('IT', 'ZEO ESS login'), ('Admin', 'Access card and workstation'),
    ('HR', 'Welcome kit and policy acknowledgement'), ('HR', 'Bank account details for WPS salary'),
    ('Manager', '30-60-90 day goals'), ('L&D', 'Induction and UAE Labour Law course'),
]


class VisaStep(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('in_progress', 'In progress'), ('done', 'Done'), ('not_applicable', 'Not applicable')]
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name='visa_steps')
    sequence = models.PositiveSmallIntegerField(default=1)
    name = models.CharField(max_length=150)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    step_date = models.DateField(null=True, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    document = models.FileField(upload_to='recruitment/visa/', null=True, blank=True)
    remarks = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['offer', 'sequence']


class OnboardingTask(models.Model):
    TEAM_CHOICES = [('HR', 'HR'), ('IT', 'IT'), ('Admin', 'Admin'), ('Manager', 'Manager'), ('L&D', 'L&D'), ('Finance', 'Finance')]
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name='onboarding_tasks')
    team = models.CharField(max_length=20, choices=TEAM_CHOICES, default='HR')
    title = models.CharField(max_length=200)
    owner = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='onboarding_tasks_owned')
    due_date = models.DateField(null=True, blank=True)
    is_done = models.BooleanField(default=False)
    done_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['offer', 'is_done', 'id']

    def save(self, *args, **kwargs):
        if self.is_done and not self.done_on:
            self.done_on = timezone.now()
        if not self.is_done:
            self.done_on = None
        super().save(*args, **kwargs)
