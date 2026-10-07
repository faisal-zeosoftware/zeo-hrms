from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Course(models.Model):
    MODE_CHOICES = [('internal_classroom', 'Internal - classroom'), ('internal_elearning', 'Internal - e-learning'),
                    ('external_classroom', 'External - classroom'), ('external_online', 'External - online')]
    TYPE_CHOICES = [('mandatory', 'Mandatory (all staff)'), ('mandatory_role', 'Mandatory (role)'), ('optional', 'Optional')]

    code = models.CharField(max_length=30, unique=True, blank=True)
    title = models.CharField(max_length=200)
    provider = models.CharField(max_length=150, blank=True, null=True, help_text="Leave empty for internal courses")
    mode = models.CharField(max_length=30, choices=MODE_CHOICES, default='internal_classroom')
    duration_hours = models.DecimalField(max_digits=6, decimal_places=1, default=8)
    target_audience = models.CharField(max_length=200, blank=True, null=True)
    course_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='optional')
    departments = models.ManyToManyField('OrganisationManager.dept_master', blank=True, related_name='courses')
    cost_per_head = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pass_mark = models.PositiveSmallIntegerField(default=70)
    certificate_validity_months = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Empty = no expiry")
    objectives = models.TextField(blank=True, null=True)
    content_file = models.FileField(upload_to='learning/content/', null=True, blank=True)
    content_link = models.URLField(blank=True, null=True)
    auto_assign_new_joiners = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['code']

    def save(self, *args, **kwargs):
        if not self.code:
            last = Course.objects.filter(code__startswith='CRS-').order_by('-code').values_list('code', flat=True).first()
            n = int(last.split('-')[1]) + 1 if last and last.split('-')[1].isdigit() else 1
            self.code = f"CRS-{n:03d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.title}"


class TrainingNeed(models.Model):
    SOURCE_CHOICES = [('appraisal', 'Appraisal'), ('pip', 'PIP'), ('manager', 'Manager request'), ('self', 'Self request'),
                      ('new_joiner', 'New joiner'), ('compliance', 'Compliance / expiry'), ('hr', 'HR')]
    PRIORITY_CHOICES = [('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'), ('mandatory', 'Mandatory')]
    STATUS_CHOICES = [('identified', 'Identified'), ('pending_approval', 'Pending approval'), ('planned', 'Planned'),
                      ('nominated', 'Nominated'), ('completed', 'Completed'), ('cancelled', 'Cancelled')]

    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='training_needs')
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True, related_name='needs')
    need = models.CharField(max_length=255)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='manager')
    source_reference = models.CharField(max_length=150, blank=True, null=True)
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='identified')
    target_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', '-created_at']

    def __str__(self):
        return f"{self.employee} - {self.need}"


class TrainingSession(models.Model):
    STATUS_CHOICES = [('draft', 'Draft'), ('published', 'Published'), ('in_progress', 'In progress'),
                      ('completed', 'Completed'), ('cancelled', 'Cancelled')]

    code = models.CharField(max_length=30, unique=True, blank=True)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name='sessions')
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    trainer_employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions_trained')
    trainer_external = models.CharField(max_length=150, blank=True, null=True)
    venue = models.CharField(max_length=150, blank=True, null=True)
    online_link = models.URLField(blank=True, null=True)
    seats = models.PositiveIntegerField(default=20)
    cost_per_head = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Empty = course cost")
    nomination_deadline = models.DateField(null=True, blank=True)
    bond_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=1000, help_text="Training bond needed when cost per head is above this")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']

    @property
    def effective_cost(self):
        return self.cost_per_head if self.cost_per_head is not None else self.course.cost_per_head

    @property
    def bond_required(self):
        return (self.effective_cost or Decimal('0')) > (self.bond_threshold or Decimal('0'))

    def confirmed_count(self):
        return self.nominations.filter(seat_status='confirmed').count()

    def clean(self):
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError("End date must be on or after the start date.")
        if self.nomination_deadline and self.start_date and self.nomination_deadline > self.start_date:
            raise ValidationError("Nomination deadline must be before the session starts.")

    def save(self, *args, **kwargs):
        if not self.code:
            from zeo.module_helpers import next_document_number
            self.code = next_document_number(TrainingSession, 'code', 'TS', width=3)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.course.title}"


class Nomination(models.Model):
    BY_CHOICES = [('manager', 'Manager'), ('self', 'Self'), ('hr', 'HR / L&D'), ('auto', 'Automatic')]
    APPROVAL = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')]
    SEAT = [('pending', 'Pending'), ('confirmed', 'Confirmed'), ('waitlist', 'Waitlist'), ('cancelled', 'Cancelled')]

    session = models.ForeignKey(TrainingSession, on_delete=models.CASCADE, related_name='nominations')
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='training_nominations')
    need = models.ForeignKey(TrainingNeed, on_delete=models.SET_NULL, null=True, blank=True, related_name='nominations')
    nominated_by = models.CharField(max_length=10, choices=BY_CHOICES, default='manager')
    reason = models.CharField(max_length=255, blank=True, null=True)
    manager_status = models.CharField(max_length=10, choices=APPROVAL, default='pending')
    ld_status = models.CharField(max_length=10, choices=APPROVAL, default='pending')
    seat_status = models.CharField(max_length=10, choices=SEAT, default='pending')
    rejection_reason = models.CharField(max_length=255, blank=True, null=True)
    bond_required = models.BooleanField(default=False)
    bond_accepted_on = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey('UserManagement.CustomUser', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('session', 'employee')
        ordering = ['session', 'created_at']

    def __str__(self):
        return f"{self.employee} @ {self.session}"


class ParticipantResult(models.Model):
    nomination = models.OneToOneField(Nomination, on_delete=models.CASCADE, related_name='result')
    attended = models.BooleanField(default=True)
    attendance_percent = models.PositiveSmallIntegerField(default=100)
    pre_test_score = models.PositiveSmallIntegerField(null=True, blank=True)
    post_test_score = models.PositiveSmallIntegerField(null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    feedback_rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1-5")
    trainer_rating = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1-5")
    feedback_comment = models.TextField(blank=True, null=True)

    def clean(self):
        for f in ('pre_test_score', 'post_test_score', 'attendance_percent'):
            v = getattr(self, f)
            if v is not None and v > 100:
                raise ValidationError(f"{f.replace('_', ' ')} must be 0-100.")
        for f in ('feedback_rating', 'trainer_rating'):
            v = getattr(self, f)
            if v is not None and not 1 <= v <= 5:
                raise ValidationError(f"{f.replace('_', ' ')} must be 1-5.")

    def evaluate(self):
        course = self.nomination.session.course
        if not self.attended or self.attendance_percent < 80:
            self.passed = False
        elif self.post_test_score is not None:
            self.passed = self.post_test_score >= course.pass_mark
        else:
            self.passed = True  # attendance-only course
        return self.passed


class Certificate(models.Model):
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='training_certificates')
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True, related_name='certificates')
    title = models.CharField(max_length=200)
    session = models.ForeignKey(TrainingSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='certificates')
    certificate_number = models.CharField(max_length=50, blank=True, null=True)
    issued_by = models.CharField(max_length=150, blank=True, null=True)
    issued_on = models.DateField(default=timezone.now)
    expiry_date = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to='learning/certificates/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiry_date', '-issued_on']

    @property
    def superseded(self):
        """True when the employee already holds a newer certificate for the same course (renewed)."""
        if not self.course_id:
            return False
        return Certificate.objects.filter(employee_id=self.employee_id, course_id=self.course_id,
                                          issued_on__gt=self.issued_on).exclude(pk=self.pk).exists()

    @property
    def status(self):
        if self.superseded:
            return 'renewed'
        if not self.expiry_date:
            return 'valid'
        today = timezone.localdate()
        if self.expiry_date < today:
            return 'expired'
        if self.expiry_date <= today + relativedelta(days=60):
            return 'expiring'
        return 'valid'

    def __str__(self):
        return f"{self.employee} - {self.title}"


def default_bond_schedule():
    return [{"months": 12, "percent": 100}, {"months": 18, "percent": 50}]


class TrainingBond(models.Model):
    STATUS_CHOICES = [('pending', 'Pending acceptance'), ('active', 'Active'), ('completed', 'Completed (served)'),
                      ('recovered', 'Recovered'), ('waived', 'Waived')]

    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='training_bonds')
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True, related_name='bonds')
    session = models.ForeignKey(TrainingSession, on_delete=models.SET_NULL, null=True, blank=True, related_name='bonds')
    nomination = models.OneToOneField(Nomination, on_delete=models.SET_NULL, null=True, blank=True, related_name='bond')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    start_date = models.DateField(default=timezone.now)
    schedule = models.JSONField(default=default_bond_schedule, help_text='[{"months": 12, "percent": 100}, ...] - leaving within N months repays percent')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    accepted_on = models.DateTimeField(null=True, blank=True)
    recovered_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['-start_date']

    def clean(self):
        if not isinstance(self.schedule, list) or not all(isinstance(s, dict) and 'months' in s and 'percent' in s for s in self.schedule):
            raise ValidationError('Schedule must be a list like [{"months": 12, "percent": 100}].')

    def recoverable_amount(self, on_date=None):
        """Amount the employee repays if they leave on on_date."""
        # Only a bond the employee has accepted is enforceable, and only once training has started.
        if self.status != 'active':
            return Decimal('0')
        on_date = on_date or timezone.localdate()
        if isinstance(on_date, str):
            on_date = date.fromisoformat(on_date)
        if on_date < self.start_date:
            return Decimal('0')
        for step in sorted(self.schedule, key=lambda s: s['months']):
            if on_date < self.start_date + relativedelta(months=int(step['months'])):
                pct = Decimal(str(step['percent']))
                return (self.amount * pct / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return Decimal('0')

    def __str__(self):
        return f"{self.employee} - {self.amount}"
