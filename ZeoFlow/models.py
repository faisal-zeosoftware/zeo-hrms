"""
ZEO Workflow automation (v1.29.0) – one engine for every app.

An administrator writes a rule without code: WHEN (trigger) a record of a module is created / updated / a field
changes / a date comes / at a scheduled time / by hand, IF (conditions, AND / OR groups) …, THEN (actions, in order)
update fields, create a record, send an e-mail, notify, create a task, assign an owner, submit for approval, wait,
branch, stop. Every run is logged (what started it, every action's result, errors, retries). The approval engine
(levels, approver by user / manager / role / department, parallel or sequential, amount routing, delegation, deadlines,
escalation, comments, resubmission) can be used by a rule or on its own.

Phase 1 (core): rules, triggers, conditions, e-mail, notifications, tasks, field updates.
Phase 2 (business processes): approval engine, scheduled actions (date triggers, schedules, waits), conditional
branches, record creation, escalation.
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL

TRIGGERS = (
    ('created', 'When a record is created'),
    ('updated', 'When a record is updated'),
    ('field_changed', 'When a selected field changes'),
    ('date', 'On, before or after a date field'),
    ('schedule', 'At a scheduled time'),
    ('manual', 'By hand (Run automation button)'),
)
STATES = (('draft', 'Draft'), ('active', 'Active'), ('inactive', 'Inactive'), ('archived', 'Archived'))


class Workflow(models.Model):
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    module = models.CharField(max_length=30, blank=True, help_text='app key: hrms, payroll, projects, crm, property, maintenance, hse')
    model = models.CharField(max_length=120, db_index=True, help_text='app_label.model (empty for a schedule with no record)')
    trigger = models.CharField(max_length=20, choices=TRIGGERS, default='created', db_index=True)
    trigger_config = models.JSONField(default=dict, blank=True, help_text='field / from / to; date_field, offset_days, when; every, time, weekday, day')
    conditions = models.JSONField(default=dict, blank=True, help_text='{"op": "and", "items": [{"field", "operator", "value"} | group]}')
    actions = models.JSONField(default=list, blank=True, help_text='[{"type": "send_email", ...}, …] in order')
    delay_minutes = models.PositiveIntegerField(default=0, help_text='wait this long after the trigger before the actions run')
    state = models.CharField(max_length=10, choices=STATES, default='draft', db_index=True)
    once_per_record = models.BooleanField(default=False, help_text='run at most once for the same record')
    max_runs_per_day = models.PositiveIntegerField(default=0, help_text='0 = no limit')
    owner = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)
    published_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    runs_count = models.PositiveIntegerField(default=0)
    errors_count = models.PositiveIntegerField(default=0)
    history = models.JSONField(default=list, blank=True, help_text='published versions: [{version, at, by, snapshot}]')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


RUN_STATES = (('ok', 'Done'), ('failed', 'Failed'), ('skipped', 'Skipped'), ('waiting', 'Waiting'),
              ('cancelled', 'Cancelled'), ('running', 'Running'))


class WorkflowRun(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name='runs')
    version = models.PositiveIntegerField(default=1)
    model = models.CharField(max_length=120, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    record_label = models.CharField(max_length=255, blank=True)
    trigger = models.CharField(max_length=20, blank=True)
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=RUN_STATES, default='running', db_index=True)
    steps = models.JSONField(default=list, blank=True, help_text='[{"index", "type", "status", "detail"}]')
    error = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)
    initiated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    initiator = models.CharField(max_length=20, default='system', help_text='system, user, schedule, approval, retry')
    correlation_id = models.CharField(max_length=40, blank=True, db_index=True)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    day = models.DateField(null=True, blank=True, db_index=True, help_text='local date of the run (date triggers run once a day per record)')

    class Meta:
        ordering = ['-started_at', '-id']


class ScheduledAction(models.Model):
    KINDS = (('wait', 'Wait inside a rule'), ('delay', 'Delayed start'), ('date', 'Date trigger'), ('schedule', 'Scheduled rule'),
             ('reminder', 'Approval reminder'))
    STATES = (('pending', 'Pending'), ('done', 'Done'), ('cancelled', 'Cancelled'), ('failed', 'Failed'))
    workflow = models.ForeignKey(Workflow, null=True, blank=True, on_delete=models.CASCADE, related_name='scheduled')
    run = models.ForeignKey(WorkflowRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='scheduled')
    kind = models.CharField(max_length=12, choices=KINDS, default='wait')
    model = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    record_label = models.CharField(max_length=255, blank=True)
    due_at = models.DateTimeField(db_index=True)
    resume_from = models.PositiveIntegerField(default=0, help_text='index of the next action')
    actions = models.JSONField(default=list, blank=True, help_text='the remaining actions of a branch (waits inside a branch)')
    status = models.CharField(max_length=10, choices=STATES, default='pending', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    done_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['due_at', 'id']


class FlowTemplate(models.Model):
    """E-mail / in-app notification template with {{ variables }} ({{ record.field }}, {{ user.name }}, {{ link }} …)."""
    KINDS = (('email', 'E-mail'), ('inapp', 'In-app notification'), ('both', 'E-mail and in-app'))
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=6, choices=KINDS, default='email')
    model = models.CharField(max_length=120, blank=True, help_text='record type the variables refer to (optional)')
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class FlowNotification(models.Model):
    """In-app notification written by a rule (shown in the bell as 'Automation')."""
    recipient = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='flow_notifications')
    title = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    link = models.CharField(max_length=300, blank=True)
    model = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    run = models.ForeignKey(WorkflowRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']


# ------------------------------------------------------------------------------------------------------- approvals
class ApprovalFlow(models.Model):
    """A configurable approval chain for a record type. steps = [{level, name, mode: sequential|parallel,
    approvers: [{type: user|manager|role|department_head|field|creator_manager, user, group, field}], min_amount,
    amount_field, deadline_hours, escalate_to (user id), remind_after_hours}]"""
    name = models.CharField(max_length=120)
    model = models.CharField(max_length=120, db_index=True)
    description = models.TextField(blank=True)
    steps = models.JSONField(default=list, blank=True)
    allow_self_approval = models.BooleanField(default=False)
    require_comment_on_reject = models.BooleanField(default=True)
    status_field = models.CharField(max_length=80, blank=True, help_text='field of the record set on the final decision (optional)')
    approved_value = models.CharField(max_length=80, blank=True)
    rejected_value = models.CharField(max_length=80, blank=True)
    pending_value = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


REQ_STATES = (('pending', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'),
              ('changes', 'Changes requested'), ('cancelled', 'Cancelled'))


class ApprovalRequest(models.Model):
    flow = models.ForeignKey(ApprovalFlow, on_delete=models.CASCADE, related_name='requests')
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    record_label = models.CharField(max_length=255, blank=True)
    record_link = models.CharField(max_length=300, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=10, choices=REQ_STATES, default='pending', db_index=True)
    current_level = models.PositiveIntegerField(default=1)
    submitted_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True, help_text='note of the person submitting')
    resubmitted_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    run = models.ForeignKey(WorkflowRun, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    callbacks = models.JSONField(default=dict, blank=True, help_text='{"workflow": id, "on_approved": [...], "on_rejected": [...]}')
    correlation_id = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ['-submitted_at', '-id']


STEP_STATES = (('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('changes', 'Changes requested'),
               ('skipped', 'Skipped'), ('waiting', 'Waiting for an earlier level'))


class ApprovalStep(models.Model):
    request = models.ForeignKey(ApprovalRequest, on_delete=models.CASCADE, related_name='steps')
    level = models.PositiveIntegerField(default=1)
    level_name = models.CharField(max_length=120, blank=True)
    mode = models.CharField(max_length=10, default='sequential')
    approver = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='flow_approvals')
    delegate = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='flow_delegated_approvals')
    status = models.CharField(max_length=10, choices=STEP_STATES, default='waiting', db_index=True)
    comment = models.TextField(blank=True)
    attachment = models.FileField(upload_to='flow/approvals/', null=True, blank=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    escalated_to = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    escalated_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['level', 'id']


class FlowAudit(models.Model):
    """Configuration changes of rules, approval flows and templates (who changed what, when)."""
    kind = models.CharField(max_length=20)
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=20)
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    detail = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-at', '-id']
