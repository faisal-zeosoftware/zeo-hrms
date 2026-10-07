from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

RATING_CHOICES = [(1, '1 - Below expectations'), (2, '2 - Partially meets'), (3, '3 - Meets'),
                  (4, '4 - Exceeds'), (5, '5 - Outstanding')]


class KPI(models.Model):
    """KPI and competency library."""
    TYPE_CHOICES = [('kpi', 'KPI'), ('competency', 'Competency')]
    MEASURE_CHOICES = [('quantitative', 'Quantitative'), ('behavioural', 'Behavioural')]

    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    kpi_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='kpi')
    measure = models.CharField(max_length=20, choices=MEASURE_CHOICES, default='quantitative')
    department = models.ForeignKey('OrganisationManager.dept_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='performance_kpis')
    designation = models.ForeignKey('OrganisationManager.desgntn_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='performance_kpis')
    default_weight = models.DecimalField(max_digits=5, decimal_places=2, default=10)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='performance_kpis_created')

    class Meta:
        ordering = ['kpi_type', 'code']

    def __str__(self):
        return f"{self.code} - {self.name}"


class AppraisalTemplate(models.Model):
    name = models.CharField(max_length=150, unique=True)
    kpi_weight = models.DecimalField(max_digits=5, decimal_places=2, default=70)
    competency_weight = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    min_goals = models.PositiveIntegerField(default=3)
    max_goals = models.PositiveIntegerField(default=8)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def clean(self):
        if (self.kpi_weight or 0) + (self.competency_weight or 0) != 100:
            raise ValidationError("KPI weight + competency weight must equal 100.")
        if self.min_goals > self.max_goals:
            raise ValidationError("Minimum goals cannot exceed maximum goals.")

    def __str__(self):
        return self.name


class AppraisalCycle(models.Model):
    TYPE_CHOICES = [('annual', 'Annual'), ('half_yearly', 'Half-yearly'), ('probation', 'Probation review')]
    STATUS_CHOICES = [('draft', 'Draft'), ('active', 'Active'), ('calibration', 'Calibration'), ('closed', 'Closed')]

    name = models.CharField(max_length=150, unique=True)
    cycle_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='annual')
    template = models.ForeignKey(AppraisalTemplate, on_delete=models.PROTECT, related_name='cycles')
    period_from = models.DateField()
    period_to = models.DateField()
    eligible_joined_before = models.DateField(null=True, blank=True, help_text="Only employees who joined before this date are included")
    branches = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True, related_name='appraisal_cycles')
    departments = models.ManyToManyField('OrganisationManager.dept_master', blank=True, related_name='appraisal_cycles')
    goal_setting_due = models.DateField(null=True, blank=True)
    checkin_due = models.DateField(null=True, blank=True)
    self_appraisal_start = models.DateField(null=True, blank=True)
    self_appraisal_due = models.DateField(null=True, blank=True)
    manager_review_due = models.DateField(null=True, blank=True)
    calibration_due = models.DateField(null=True, blank=True)
    increment_effective_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    launched_on = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='appraisal_cycles_created')

    class Meta:
        ordering = ['-period_from']

    def clean(self):
        if self.period_to and self.period_from and self.period_to < self.period_from:
            raise ValidationError("Review period end must be after the start.")

    def eligible_employees(self):
        from EmpManagement.models import emp_master
        qs = emp_master.objects.filter(is_active=True)
        if self.eligible_joined_before:
            qs = qs.filter(emp_joined_date__lt=self.eligible_joined_before)
        if self.pk and self.branches.exists():
            qs = qs.filter(emp_branch_id__in=self.branches.all())
        if self.pk and self.departments.exists():
            qs = qs.filter(emp_dept_id__in=self.departments.all())
        return qs

    def __str__(self):
        return self.name


class IncrementBand(models.Model):
    """Increment % and bonus (in months of basic) per final rating within a cycle."""
    cycle = models.ForeignKey(AppraisalCycle, on_delete=models.CASCADE, related_name='increment_bands')
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    increment_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bonus_months = models.DecimalField(max_digits=4, decimal_places=2, default=0, help_text="Bonus as months of basic salary")
    guideline_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="Target share of employees for calibration")

    class Meta:
        unique_together = ('cycle', 'rating')
        ordering = ['cycle', '-rating']

    def __str__(self):
        return f"{self.cycle} - {self.rating}: {self.increment_percent}%"


DEFAULT_BANDS = [(5, 10, Decimal('1.0'), 10), (4, 6, Decimal('0.5'), 25), (3, 3, Decimal('0.25'), 50), (2, 0, 0, 10), (1, 0, 0, 5)]


class GoalSheet(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'), ('submitted', 'Goals submitted'), ('approved', 'Goals approved'),
        ('self_submitted', 'Self appraisal submitted'), ('reviewed', 'Manager reviewed'),
        ('calibrated', 'Calibrated'), ('acknowledged', 'Acknowledged'),
    ]
    cycle = models.ForeignKey(AppraisalCycle, on_delete=models.CASCADE, related_name='goal_sheets')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='goal_sheets')
    manager = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='team_goal_sheets', help_text="Reporting manager at launch")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    self_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    manager_score = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    proposed_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, null=True, blank=True)
    final_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, null=True, blank=True)
    key_achievements = models.TextField(blank=True, null=True)
    development_needs = models.TextField(blank=True, null=True)
    manager_comments = models.TextField(blank=True, null=True)
    return_reason = models.TextField(blank=True, null=True)
    acknowledged_on = models.DateTimeField(null=True, blank=True)
    employee_disagrees = models.BooleanField(default=False)
    disagreement_note = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cycle', 'employee')
        ordering = ['cycle', 'employee__emp_code']

    def __str__(self):
        return f"{self.cycle} - {self.employee}"

    # ---- rules ----
    def validate_goals(self):
        goals = list(self.goals.all())
        tpl = self.cycle.template
        if not (tpl.min_goals <= len(goals) <= tpl.max_goals):
            raise ValidationError(f"Goal sheet needs between {tpl.min_goals} and {tpl.max_goals} goals (has {len(goals)}).")
        total = sum((g.weight for g in goals), Decimal('0'))
        if total != Decimal('100'):
            raise ValidationError(f"Goal weights must total 100% (currently {total}%).")
        kpi = sum((g.weight for g in goals if g.goal_type == 'kpi'), Decimal('0'))
        if kpi != tpl.kpi_weight:
            raise ValidationError(f"KPI goals must total {tpl.kpi_weight}% and competencies {tpl.competency_weight}% (KPI is {kpi}%).")

    @staticmethod
    def weighted(goals, attr):
        rated = [(g.weight, getattr(g, attr)) for g in goals if getattr(g, attr)]
        if not rated:
            return None
        total_w = sum((w for w, _ in rated), Decimal('0'))
        score = sum((w * Decimal(r) for w, r in rated), Decimal('0')) / total_w
        return score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    def compute_scores(self):
        goals = list(self.goals.all())
        self.self_score = self.weighted(goals, 'self_rating')
        self.manager_score = self.weighted(goals, 'manager_rating')
        if self.manager_score is not None:
            self.proposed_rating = int(self.manager_score.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


class Goal(models.Model):
    TYPE_CHOICES = [('kpi', 'KPI'), ('competency', 'Competency')]
    PROGRESS_CHOICES = [('not_started', 'Not started'), ('on_track', 'On track'), ('ahead', 'Ahead'),
                        ('at_risk', 'At risk'), ('off_track', 'Off track'), ('done', 'Done')]

    sheet = models.ForeignKey(GoalSheet, on_delete=models.CASCADE, related_name='goals')
    kpi = models.ForeignKey(KPI, on_delete=models.SET_NULL, null=True, blank=True, related_name='goals')
    title = models.CharField(max_length=250)
    goal_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='kpi')
    weight = models.DecimalField(max_digits=5, decimal_places=2)
    target = models.CharField(max_length=250, blank=True, null=True)
    due_date = models.DateField(null=True, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    progress_status = models.CharField(max_length=20, choices=PROGRESS_CHOICES, default='not_started')
    self_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, null=True, blank=True)
    self_comment = models.TextField(blank=True, null=True)
    evidence = models.FileField(upload_to='performance/evidence/', null=True, blank=True)
    manager_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, null=True, blank=True)
    manager_comment = models.TextField(blank=True, null=True)
    revision_note = models.CharField(max_length=250, blank=True, null=True, help_text="Reason when the goal is changed after approval")

    class Meta:
        ordering = ['sheet', 'id']

    def clean(self):
        if self.weight is not None and (self.weight <= 0 or self.weight > 100):
            raise ValidationError("Weight must be between 0 and 100.")
        if self.progress_percent > 100:
            raise ValidationError("Progress cannot exceed 100%.")
        if self.kpi_id and not self.goal_type:
            self.goal_type = self.kpi.kpi_type

    def __str__(self):
        return self.title


class CheckIn(models.Model):
    sheet = models.ForeignKey(GoalSheet, on_delete=models.CASCADE, related_name='checkins')
    checkin_date = models.DateField(default=timezone.now)
    manager_comments = models.TextField(blank=True, null=True)
    employee_comments = models.TextField(blank=True, null=True)
    goal_changes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-checkin_date']


class CalibrationLog(models.Model):
    sheet = models.ForeignKey(GoalSheet, on_delete=models.CASCADE, related_name='calibration_logs')
    old_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    new_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    reason = models.CharField(max_length=255)
    changed_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']


class AppraisalOutcome(models.Model):
    STATUS_CHOICES = [('pending', 'Pending approval'), ('approved', 'Approved'), ('pushed', 'Pushed to payroll'), ('rejected', 'Rejected')]

    sheet = models.OneToOneField(GoalSheet, on_delete=models.CASCADE, related_name='outcome')
    final_rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    current_basic = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    increment_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    new_basic = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    bonus_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    effective_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    remarks = models.CharField(max_length=255, blank=True, null=True)
    pushed_on = models.DateTimeField(null=True, blank=True)
    pushed_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='appraisal_outcomes_pushed')

    def recalc(self):
        pct = self.increment_percent or Decimal('0')
        self.new_basic = (self.current_basic * (Decimal('1') + pct / Decimal('100'))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


class PerformanceImprovementPlan(models.Model):
    STATUS_CHOICES = [('active', 'Active'), ('extended', 'Extended'), ('completed', 'Completed - improved'), ('failed', 'Completed - not improved'), ('cancelled', 'Cancelled')]

    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='pips')
    sheet = models.ForeignKey(GoalSheet, on_delete=models.SET_NULL, null=True, blank=True, related_name='pips')
    start_date = models.DateField()
    end_date = models.DateField()
    objectives = models.TextField()
    support_plan = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    outcome_notes = models.TextField(blank=True, null=True)
    owner = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='pips_owned')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    def clean(self):
        if self.end_date and self.start_date and self.end_date <= self.start_date:
            raise ValidationError("PIP end date must be after the start date.")


class PIPReview(models.Model):
    pip = models.ForeignKey(PerformanceImprovementPlan, on_delete=models.CASCADE, related_name='reviews')
    review_date = models.DateField(default=timezone.now)
    progress_notes = models.TextField()
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, null=True, blank=True)
    reviewed_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-review_date']
