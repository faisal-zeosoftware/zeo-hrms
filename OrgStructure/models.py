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
