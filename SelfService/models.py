"""v1.13.0 – Employee self service: field policy, profile change requests, HR letters, grievances.

Tables are keyed by the existing row ids (employee_id, branch_id, user_id …) so the core apps need no migrations.
"""
from django.db import models

POLICY_CHOICES = [('self', 'Employee changes it directly'), ('request', 'Employee requests – HR approves'),
                  ('hr', 'HR only (shown read-only)'), ('hidden', 'Not shown in self service')]


class EssFieldPolicy(models.Model):
    """HR override of the default ESS policy of a profile field ('' field = adding / removing records of the group)."""
    group = models.CharField(max_length=40)
    field = models.CharField(max_length=80, blank=True, default='')
    policy = models.CharField(max_length=10, choices=POLICY_CHOICES)
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('group', 'field')]
        ordering = ['group', 'field']


class ProfileChangeRequest(models.Model):
    ACTIONS = [('create', 'Add'), ('update', 'Change'), ('delete', 'Remove')]
    STATUSES = [('pending', 'Waiting for HR'), ('approved', 'Approved'), ('rejected', 'Rejected'),
                ('withdrawn', 'Withdrawn'), ('failed', 'Approved – could not be applied')]
    number = models.CharField(max_length=30, blank=True, default='', db_index=True)
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    group = models.CharField(max_length=40)
    record_id = models.IntegerField(null=True, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS, default='update')
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=500, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUSES, default='pending', db_index=True)
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, default='')
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_record_id = models.IntegerField(null=True, blank=True)
    error = models.JSONField(default=dict, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']


class ChangeRequestAttachment(models.Model):
    request = models.ForeignKey(ProfileChangeRequest, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='ess/change_requests/')
    name = models.CharField(max_length=200, blank=True, default='')
    uploaded_by_id = models.IntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)


LETTER_TYPES = [
    ('salary_certificate', 'Salary certificate'),
    ('salary_transfer', 'Salary transfer letter (to bank)'),
    ('noc_travel', 'NOC – travel'),
    ('noc_visa', 'NOC – visa'),
    ('noc_other', 'NOC – other'),
    ('experience', 'Experience certificate'),
    ('employment', 'Employment certificate'),
    ('embassy', 'Embassy letter'),
    ('bank_account', 'Bank account opening letter'),
]


class LetterTemplate(models.Model):
    letter_type = models.CharField(max_length=30, choices=LETTER_TYPES)
    language = models.CharField(max_length=2, choices=[('en', 'English'), ('ar', 'Arabic')], default='en')
    branch_id = models.IntegerField(null=True, blank=True, help_text='empty = all branches')
    title = models.CharField(max_length=200)
    body = models.TextField(help_text='Text with placeholders such as {employee_name}, {designation}, {basic_salary}')
    footer = models.CharField(max_length=300, blank=True, default='')
    signatory_name = models.CharField(max_length=120, blank=True, default='')
    signatory_title = models.CharField(max_length=120, blank=True, default='')
    show_salary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('letter_type', 'language', 'branch_id')]
        ordering = ['letter_type', 'language']


class LetterRequest(models.Model):
    STATUSES = [('pending', 'Waiting for HR'), ('issued', 'Ready to download'), ('rejected', 'Rejected'), ('withdrawn', 'Withdrawn')]
    number = models.CharField(max_length=30, blank=True, default='', db_index=True)
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    letter_type = models.CharField(max_length=30, choices=LETTER_TYPES)
    language = models.CharField(max_length=2, choices=[('en', 'English'), ('ar', 'Arabic')], default='en')
    addressee = models.CharField(max_length=300, blank=True, default='')
    purpose = models.CharField(max_length=500, blank=True, default='')
    bank_name = models.CharField(max_length=150, blank=True, default='')
    travel_from = models.DateField(null=True, blank=True)
    travel_to = models.DateField(null=True, blank=True)
    destination = models.CharField(max_length=150, blank=True, default='')
    status = models.CharField(max_length=12, choices=STATUSES, default='pending', db_index=True)
    issued_directly = models.BooleanField(default=False)
    reference_no = models.CharField(max_length=40, blank=True, default='', db_index=True)
    verification_code = models.CharField(max_length=20, blank=True, default='', db_index=True)
    pdf = models.FileField(upload_to='ess/letters/', null=True, blank=True)
    body_snapshot = models.TextField(blank=True, default='')
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True, default='')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']


GRIEVANCE_CATEGORIES = [('harassment', 'Harassment'), ('pay', 'Pay'), ('workplace', 'Workplace'), ('manager', 'Manager'),
                        ('safety', 'Safety'), ('other', 'Other')]
GRIEVANCE_STATUSES = [('submitted', 'Submitted'), ('acknowledged', 'Acknowledged'), ('investigating', 'Investigating'),
                      ('resolved', 'Resolved'), ('closed', 'Closed')]


class GrievanceSetting(models.Model):
    """Per category: days to resolve and the grievance officers (user ids) who handle it."""
    category = models.CharField(max_length=20, choices=GRIEVANCE_CATEGORIES, unique=True)
    sla_days = models.PositiveIntegerField(default=10)
    acknowledge_days = models.PositiveIntegerField(default=2)
    officer_user_ids = models.JSONField(default=list, blank=True)
    escalate_to_user_ids = models.JSONField(default=list, blank=True)


class Grievance(models.Model):
    number = models.CharField(max_length=30, blank=True, default='', db_index=True)
    employee_id = models.IntegerField(db_index=True)
    branch_id = models.IntegerField(null=True, blank=True, db_index=True)
    category = models.CharField(max_length=20, choices=GRIEVANCE_CATEGORIES)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    against = models.CharField(max_length=200, blank=True, default='', help_text='person or area concerned (optional)')
    incident_date = models.DateField(null=True, blank=True)
    confidential = models.BooleanField(default=True)
    anonymous = models.BooleanField(default=False)
    token_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    status = models.CharField(max_length=15, choices=GRIEVANCE_STATUSES, default='submitted', db_index=True)
    assigned_user_ids = models.JSONField(default=list, blank=True)
    sla_days = models.PositiveIntegerField(default=10)
    due_date = models.DateField(null=True, blank=True)
    escalated = models.BooleanField(default=False)
    escalation_level = models.PositiveIntegerField(default=0)
    escalated_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True, default='')
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    feedback_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback_text = models.CharField(max_length=1000, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        permissions = [('handle_grievance', 'Can handle employee complaints / grievances')]


class GrievanceMessage(models.Model):
    grievance = models.ForeignKey(Grievance, on_delete=models.CASCADE, related_name='messages')
    author_user_id = models.IntegerField(null=True, blank=True)
    from_employee = models.BooleanField(default=False)
    internal = models.BooleanField(default=False, help_text='note between handlers, never shown to the employee')
    text = models.TextField(blank=True, default='')
    attachment = models.FileField(upload_to='ess/grievances/', null=True, blank=True)
    event = models.CharField(max_length=30, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
