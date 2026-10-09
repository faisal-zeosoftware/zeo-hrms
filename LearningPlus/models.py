"""
LearningPlus: extra tables for the Learning module (training masters, trainers / providers, online courses,
date-wise attendance, cost & budget, skills, alert markers).

Links to LearningManagement / EmpManagement / OrganisationManager rows are plain integer ids
(course_id, session_id, nomination_id, employee_id, department_id, designation_id) so this app
has its own migration chain and never changes the existing tables.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

DELIVERY = [('classroom', 'Classroom'), ('online', 'Online'), ('blended', 'Blended')]
LEVEL = [('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced'), ('expert', 'Expert')]
DEFAULT_SKILL_LEVELS = {1: 'Awareness', 2: 'Basic', 3: 'Intermediate', 4: 'Advanced', 5: 'Expert'}


class TrainingCategory(models.Model):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=30, unique=True)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'training categories'

    def __str__(self):
        return f"{self.code} - {self.name}"


class Provider(models.Model):
    name = models.CharField(max_length=150)
    contact_person = models.CharField(max_length=120, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=40, blank=True, null=True)
    trn = models.CharField('TRN', max_length=20, blank=True, null=True, help_text='UAE VAT tax registration number (15 digits)')
    website = models.URLField(blank=True, null=True)
    accreditation = models.CharField(max_length=200, blank=True, null=True, help_text='e.g. KHDA, ADEK, DOH, CIPD approved')
    contract_expiry = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def clean(self):
        if self.trn and (not self.trn.isdigit() or len(self.trn) != 15):
            raise ValidationError({'trn': 'TRN must be 15 digits.'})

    def __str__(self):
        return self.name


class Trainer(models.Model):
    TYPE = [('internal', 'Internal (employee)'), ('external', 'External')]
    trainer_type = models.CharField(max_length=10, choices=TYPE, default='external')
    employee_id = models.IntegerField(null=True, blank=True, help_text='emp_master id for internal trainers')
    name = models.CharField(max_length=150, blank=True, null=True, help_text='External trainer name (internal: filled from the employee)')
    provider = models.ForeignKey(Provider, on_delete=models.SET_NULL, null=True, blank=True, related_name='trainers')
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=40, blank=True, null=True)
    expertise = models.CharField(max_length=255, blank=True, null=True)
    rate_per_hour = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rate_per_day = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def clean(self):
        if self.trainer_type == 'internal' and not self.employee_id:
            raise ValidationError({'employee_id': 'Pick the employee for an internal trainer.'})
        if self.trainer_type == 'external' and not self.name:
            raise ValidationError({'name': 'Enter the external trainer name.'})

    def __str__(self):
        return self.name or f"Employee #{self.employee_id}"


class Venue(models.Model):
    name = models.CharField(max_length=150)
    location = models.CharField(max_length=200, blank=True, null=True)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    cost_per_day = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CourseExtra(models.Model):
    course_id = models.IntegerField(unique=True)
    category = models.ForeignKey(TrainingCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='courses')
    provider = models.ForeignKey(Provider, on_delete=models.SET_NULL, null=True, blank=True, related_name='courses')
    delivery = models.CharField(max_length=10, choices=DELIVERY, default='classroom')
    language = models.CharField(max_length=40, default='English')
    level = models.CharField(max_length=15, choices=LEVEL, default='beginner')


class SessionExtra(models.Model):
    session_id = models.IntegerField(unique=True)
    trainer = models.ForeignKey(Trainer, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    provider = models.ForeignKey(Provider, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    venue = models.ForeignKey(Venue, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    other_costs = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text='Lump sum not itemised in cost lines')
    notes = models.CharField(max_length=255, blank=True, null=True)


class CourseModule(models.Model):
    CONTENT = [('video', 'Video'), ('document', 'Document'), ('link', 'Link'), ('quiz', 'Quiz')]
    course_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=1)
    content_type = models.CharField(max_length=10, choices=CONTENT, default='video')
    url = models.URLField(blank=True, null=True)
    file = models.FileField(upload_to='learning/modules/', null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=10)
    pass_mark = models.PositiveSmallIntegerField(null=True, blank=True, help_text='Quiz only; empty = course pass mark')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['course_id', 'order', 'id']

    def __str__(self):
        return self.title


class ModuleProgress(models.Model):
    STATUS = [('not_started', 'Not started'), ('in_progress', 'In progress'), ('completed', 'Completed')]
    employee_id = models.IntegerField(db_index=True)
    module = models.ForeignKey(CourseModule, on_delete=models.CASCADE, related_name='progress')
    status = models.CharField(max_length=12, choices=STATUS, default='not_started')
    progress = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)

    class Meta:
        unique_together = ('employee_id', 'module')


class OnlineEnrolment(models.Model):
    STATUS = [('enrolled', 'Enrolled'), ('in_progress', 'In progress'), ('completed', 'Completed'), ('failed', 'Not passed')]
    course_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(db_index=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    score = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default='enrolled')
    nomination_id = models.IntegerField(null=True, blank=True, help_text='Set when the course is part of a session nomination')
    certificate_id = models.IntegerField(null=True, blank=True)
    assigned_by = models.IntegerField(null=True, blank=True, help_text='user id')

    class Meta:
        unique_together = ('course_id', 'employee_id')
        ordering = ['-enrolled_at']


class SessionAttendance(models.Model):
    session_id = models.IntegerField(db_index=True)
    nomination_id = models.IntegerField(db_index=True)
    date = models.DateField()
    present = models.BooleanField(default=True)
    hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    remarks = models.CharField(max_length=150, blank=True, null=True)

    class Meta:
        unique_together = ('nomination_id', 'date')
        ordering = ['session_id', 'date']


class TrainingBudget(models.Model):
    year = models.PositiveIntegerField()
    department_id = models.IntegerField(null=True, blank=True, help_text='Empty = company-wide budget')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        unique_together = ('year', 'department_id')
        ordering = ['-year', 'department_id']


class SessionCost(models.Model):
    KIND = [('trainer_fee', 'Trainer fee'), ('venue', 'Venue'), ('material', 'Material'), ('travel', 'Travel'), ('other', 'Other')]
    session_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=12, choices=KIND, default='trainer_fee')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=200, blank=True, null=True)
    invoice_ref = models.CharField(max_length=60, blank=True, null=True)

    class Meta:
        ordering = ['session_id', 'id']

    def clean(self):
        if self.amount is not None and self.amount < Decimal('0'):
            raise ValidationError({'amount': 'Amount cannot be negative.'})


class Skill(models.Model):
    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=80, blank=True, null=True, help_text='e.g. Technical, Safety, Soft skill')
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return self.name


class SkillLevel(models.Model):
    """Optional relabelling of the 1-5 scale (defaults in DEFAULT_SKILL_LEVELS)."""
    level = models.PositiveSmallIntegerField(unique=True)
    label = models.CharField(max_length=40)
    description = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ['level']

    def clean(self):
        if self.level is not None and not 1 <= self.level <= 5:
            raise ValidationError({'level': 'Level must be 1-5.'})


def _level_ok(v, field):
    if v is not None and not 1 <= int(v) <= 5:
        raise ValidationError({field: 'Level must be 1-5.'})


class CourseSkill(models.Model):
    course_id = models.IntegerField(db_index=True)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='courses')
    level_gained = models.PositiveSmallIntegerField(default=2)

    class Meta:
        unique_together = ('course_id', 'skill')

    def clean(self):
        _level_ok(self.level_gained, 'level_gained')


class EmployeeSkill(models.Model):
    SOURCE = [('course', 'Course passed'), ('online', 'Online course'), ('manual', 'Manual'), ('appraisal', 'Appraisal')]
    employee_id = models.IntegerField(db_index=True)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='employees')
    level = models.PositiveSmallIntegerField(default=1)
    source = models.CharField(max_length=10, choices=SOURCE, default='manual')
    source_ref = models.CharField(max_length=100, blank=True, null=True)
    verified_by = models.IntegerField(null=True, blank=True, help_text='user id')
    date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ('employee_id', 'skill')
        ordering = ['employee_id', 'skill']

    def clean(self):
        _level_ok(self.level, 'level')


class RoleSkill(models.Model):
    designation_id = models.IntegerField(db_index=True)
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='roles')
    required_level = models.PositiveSmallIntegerField(default=3)

    class Meta:
        unique_together = ('designation_id', 'skill')

    def clean(self):
        _level_ok(self.required_level, 'required_level')


class AlertLog(models.Model):
    """Marker so scheduled alerts go out once: kind='cert_expiry' key='60'|'30'|'7'|'expired', kind='nom_escalation' key='manager'|'ld'."""
    kind = models.CharField(max_length=20)
    ref_id = models.IntegerField()
    key = models.CharField(max_length=20)
    sent_at = models.DateTimeField(auto_now_add=True)
    recipients = models.PositiveSmallIntegerField(default=0)

    class Meta:
        unique_together = ('kind', 'ref_id', 'key')
