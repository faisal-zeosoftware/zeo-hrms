"""
Per-record extras available on every screen (Odoo-style "chatter"):

* RecordLog   – who created / changed / deleted a record, when, from where, and which values changed
* Message     – log notes written on a record
* Attachment  – files attached to a record (or to a note, or used as a designer "file" field value)
* Activity    – to-dos scheduled on a record for a user (shown in their To-do list)
* FieldDef / FieldValue – extra fields added to any screen with the form designer

A record is identified by its model ("app_label.model_name") and primary key, so the same
history, notes and fields follow the record whichever screen shows it.
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


class RecordLog(models.Model):
    ACTIONS = (('created', 'Created'), ('updated', 'Updated'), ('deleted', 'Deleted'))
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    record_label = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=10, choices=ACTIONS)
    changes = models.JSONField(default=dict, blank=True)   # {field: {"label", "old", "new"}}
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    user_name = models.CharField(max_length=150, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    ip = models.CharField(max_length=64, blank=True)
    path = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10, blank=True)
    agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-at', '-id']
        indexes = [models.Index(fields=['model', 'object_id', '-at'])]


class Attachment(models.Model):
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    file = models.FileField(upload_to='attachments/%Y/%m/')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    mime = models.CharField(max_length=120, blank=True)
    field = models.CharField(max_length=100, blank=True)   # set when the file is the value of a designer field
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at', '-id']


class Message(models.Model):
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    body = models.TextField()
    author = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    attachments = models.ManyToManyField(Attachment, blank=True, related_name='messages')

    class Meta:
        ordering = ['-created_at', '-id']


class Activity(models.Model):
    TYPES = (('todo', 'To-do'), ('call', 'Call'), ('meeting', 'Meeting'), ('email', 'E-mail'),
             ('document', 'Upload document'), ('approval', 'Approval'), ('follow_up', 'Follow-up'))
    STATES = (('open', 'Open'), ('done', 'Done'), ('cancelled', 'Cancelled'))
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    record_label = models.CharField(max_length=255, blank=True)
    screen = models.CharField(max_length=255, blank=True)   # API endpoint of the screen it was planned on
    page = models.CharField(max_length=255, blank=True)     # app page to open the record again
    activity_type = models.CharField(max_length=20, choices=TYPES, default='todo')
    summary = models.CharField(max_length=255)
    note = models.TextField(blank=True)
    due_date = models.DateField(db_index=True)
    assigned_to = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=10, choices=STATES, default='open', db_index=True)
    done_at = models.DateTimeField(null=True, blank=True)
    done_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ['due_date', 'id']


FIELD_TYPES = (
    ('text', 'Text'), ('textarea', 'Long text'), ('integer', 'Whole number'), ('decimal', 'Decimal number'),
    ('currency', 'Amount (AED)'), ('percent', 'Percentage'), ('date', 'Date'), ('datetime', 'Date and time'),
    ('time', 'Time'), ('checkbox', 'Yes / No'), ('dropdown', 'Dropdown'), ('multiselect', 'Multi-select'),
    ('radio', 'Radio buttons'), ('email', 'E-mail'), ('phone', 'Phone'), ('url', 'Web link'),
    ('file', 'File'), ('employee', 'Employee'), ('rating', 'Rating (1–5)'), ('color', 'Colour'),
)


class FieldDef(models.Model):
    """An extra field added to a screen with the form designer."""
    screen = models.CharField(max_length=255, db_index=True)    # list API of the screen, e.g. /organisation/api/Department/
    model = models.CharField(max_length=120, db_index=True)
    name = models.SlugField(max_length=100)
    label = models.CharField(max_length=150)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default='text')
    options = models.JSONField(default=list, blank=True)        # dropdown / multi-select / radio values
    required = models.BooleanField(default=False)
    section = models.CharField(max_length=100, blank=True)
    order = models.IntegerField(default=0)
    help_text = models.CharField(max_length=255, blank=True)
    default = models.CharField(max_length=255, blank=True)
    show_in_list = models.BooleanField(default=True)
    # v1.12.0: lowest / highest, length, pattern, show on, show only if, read-only for employees (DataTools.fieldrules)
    rules = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['section', 'order', 'id']
        unique_together = [('model', 'name')]


class FieldValue(models.Model):
    field = models.ForeignKey(FieldDef, on_delete=models.CASCADE, related_name='values')
    object_id = models.CharField(max_length=64, db_index=True)
    value = models.JSONField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        unique_together = [('field', 'object_id')]


class DashboardLayout(models.Model):
    """Widgets a user hid / ordered on their dashboard (dashboard designer)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    dashboard = models.CharField(max_length=40, default='main')
    hidden = models.JSONField(default=list, blank=True)
    order = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'dashboard')]


class ListLayout(models.Model):
    """Columns a user hid / ordered and the view they last used on a list (column chooser)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    key = models.CharField(max_length=255)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'key')]
