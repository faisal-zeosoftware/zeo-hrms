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
        permissions = [('view_audit_log', 'Can see the company-wide audit log')]   # v1.46.0


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
    KINDS = (('note', 'Log note (internal)'), ('message', 'Message (sent to the followers)'), ('email_in', 'E-mail reply'))
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    body = models.TextField()
    author = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    attachments = models.ManyToManyField(Attachment, blank=True, related_name='messages')
    # v1.45.0 Send message / @mentions / e-mail replies (Odoo chatter)
    kind = models.CharField(max_length=10, choices=KINDS, default='note', db_index=True)
    subject = models.CharField(max_length=200, blank=True)
    recipients = models.JSONField(default=list, blank=True, help_text='[{"name", "email", "user_id"}] – who the message went to')
    mentions = models.JSONField(default=list, blank=True, help_text='user ids @mentioned in the text')
    from_name = models.CharField(max_length=150, blank=True, help_text='e-mail replies: the sender')
    from_email = models.CharField(max_length=200, blank=True)
    email_id = models.CharField('e-mail Message-ID', max_length=255, blank=True, db_index=True)
    record_label = models.CharField(max_length=255, blank=True)
    page = models.CharField(max_length=255, blank=True, help_text='app page of the record (link in e-mails and the inbox)')
    # v1.46.0 comment threads: replies under a note, reactions, resolved threads
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='replies')
    reactions = models.JSONField(default=dict, blank=True, help_text='{"👍": [user ids], "✅": [...]}')
    resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']


class Activity(models.Model):
    TYPES = (('todo', 'To-do'), ('call', 'Call'), ('meeting', 'Meeting'), ('email', 'E-mail'),
             ('document', 'Upload document'), ('approval', 'Approval'), ('follow_up', 'Follow-up'),
             ('reminder', 'Reminder'))   # v1.46.0 "remind me about this record"
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
    notified_at = models.DateTimeField(null=True, blank=True, help_text='v1.46.0: reminder sent to the inbox and the bell')

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


class FormLayout(models.Model):
    """v1.39.0 form layout designer: how the fields of a screen's form are arranged for the whole company – order, width,
    hidden fields and where the designer's extra fields sit between the standard ones. One row per screen.
    data = {"blocks": [{"key": "name:leave_type" | "label:Start date" | "extra:cost_centre", "width": "quarter|third|half|full|", "hidden": false}],
            "sections": [{"before": "<key>", "title": "Dates"}]}"""
    screen = models.CharField(max_length=255, unique=True)
    model = models.CharField(max_length=120, blank=True)
    data = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


# ================================================================== v1.45.0 collaboration (Odoo chatter, Discuss, favourites, tags)
class Follower(models.Model):
    """Who follows a record: gets its messages (in-app and by e-mail). A user, or an outside e-mail address."""
    REASONS = (('creator', 'Created the record'), ('employee', 'The employee'), ('assignee', 'Activity assigned'), ('author', 'Wrote on the record'),
               ('mentioned', '@mentioned'), ('approver', 'Approver'), ('manual', 'Added'), ('self', 'Follows'))
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    email = models.CharField(max_length=200, blank=True)
    name = models.CharField(max_length=150, blank=True)
    reason = models.CharField(max_length=10, choices=REASONS, default='manual')
    muted = models.BooleanField(default=False, help_text='Unfollowed: not added again automatically')
    record_label = models.CharField(max_length=255, blank=True)
    page = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['model', 'object_id'])]


class InboxItem(models.Model):
    """One line of a user's inbox (Odoo Discuss): a message on a followed record, an @mention, an assignment."""
    KINDS = (('message', 'Message'), ('mention', 'Mention'), ('email', 'E-mail reply'), ('assigned', 'Assigned to you'), ('transfer', 'Transfer request'),
             ('reply', 'Reply'), ('reminder', 'Reminder'))   # v1.46.0
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    kind = models.CharField(max_length=10, choices=KINDS, default='message', db_index=True)
    model = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    record_label = models.CharField(max_length=255, blank=True)
    page = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    from_name = models.CharField(max_length=150, blank=True)
    message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.CASCADE, related_name='+')
    is_read = models.BooleanField(default=False, db_index=True)
    done = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']


class Star(models.Model):
    """A record the user starred (favourite)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    model = models.CharField(max_length=120)
    object_id = models.CharField(max_length=64)
    label = models.CharField(max_length=255, blank=True)
    page = models.CharField(max_length=255, blank=True)
    endpoint = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [('user', 'model', 'object_id')]


class RecentRecord(models.Model):
    """Records the user opened lately (the last 30 are kept)."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    model = models.CharField(max_length=120)
    object_id = models.CharField(max_length=64)
    label = models.CharField(max_length=255, blank=True)
    page = models.CharField(max_length=255, blank=True)
    endpoint = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(db_index=True)

    class Meta:
        ordering = ['-at']
        unique_together = [('user', 'model', 'object_id')]


TAG_COLOURS = (('grey', 'Grey'), ('red', 'Red'), ('orange', 'Orange'), ('yellow', 'Yellow'), ('green', 'Green'), ('teal', 'Teal'),
               ('blue', 'Blue'), ('purple', 'Purple'), ('pink', 'Pink'))


class Tag(models.Model):
    """A colour tag that can be put on any record of any screen."""
    name = models.CharField(max_length=60, unique=True)
    color = models.CharField(max_length=8, choices=TAG_COLOURS, default='blue')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']


class TagLink(models.Model):
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name='links')
    model = models.CharField(max_length=120, db_index=True)
    object_id = models.CharField(max_length=64, db_index=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('tag', 'model', 'object_id')]


class MailSettings(models.Model):
    """The company mailbox that receives replies to the messages sent from records (one row). The password is kept encrypted."""
    enabled = models.BooleanField(default=False)
    reply_to = models.CharField('reply address', max_length=200, blank=True, help_text='The address the messages go out with as Reply-To, e.g. zeo-replies@company.ae')
    imap_host = models.CharField(max_length=200, blank=True)
    imap_port = models.PositiveIntegerField(default=993)
    imap_ssl = models.BooleanField(default=True)
    imap_user = models.CharField(max_length=200, blank=True)
    imap_password = models.TextField(blank=True, help_text='encrypted')
    folder = models.CharField(max_length=60, default='INBOX')
    last_check = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    replies_received = models.PositiveIntegerField(default=0)


# ------------------------------------------------------------------ v1.47.0 navigation, wall, team hub
class UserFlag(models.Model):
    """Small per-user settings of the screens: the release notes seen, the guided tours done, tours switched off."""
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    key = models.CharField(max_length=80)
    value = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'key')]


class WallReaction(models.Model):
    """A reaction on an item of the company wall: a birthday, a work anniversary, a new joiner or an announcement.
    item = 'bday:<employee id>:<year>', 'anniv:<employee id>:<year>', 'join:<employee id>' or 'ann:<announcement id>'."""
    item = models.CharField(max_length=60, db_index=True)
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    emoji = models.CharField(max_length=8)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('item', 'user', 'emoji')]


class WallComment(models.Model):
    """A wish or a comment on an item of the company wall."""
    item = models.CharField(max_length=60, db_index=True)
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    body = models.TextField()
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['at', 'id']


class OneOnOne(models.Model):
    """A 1:1 meeting of a manager with a team member: the agenda and notes (shared with the employee when chosen),
    the manager's private note and the action items."""
    employee = models.ForeignKey('EmpManagement.emp_master', on_delete=models.CASCADE, related_name='one_on_ones')
    manager = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    date = models.DateField(db_index=True)
    agenda = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    private_note = models.TextField(blank=True, help_text='seen by the manager (and HR admins) only')
    actions = models.JSONField(default=list, blank=True, help_text='[{"text": ..., "done": false}]')
    shared = models.BooleanField(default=True, help_text='the employee sees the agenda, notes and action items')
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']


# ------------------------------------------------------------------ v1.51.0 WhatsApp / SMS and mobile push settings
class MessagingSettings(models.Model):
    """The company's own WhatsApp / SMS gateway (optional): approvals waiting, decisions and payslips sent to the employee's
    mobile number. The customer brings the account (Twilio, WhatsApp Cloud API of Meta, or any HTTP gateway); the token is
    kept encrypted. Off until the company switches it on."""
    PROVIDERS = [('twilio', 'Twilio (SMS and WhatsApp)'), ('whatsapp_cloud', 'WhatsApp Business – Cloud API (Meta)'),
                 ('http', 'Other SMS / WhatsApp gateway (HTTP POST)')]
    CHANNELS = [('whatsapp', 'WhatsApp'), ('sms', 'SMS')]
    enabled = models.BooleanField(default=False)
    provider = models.CharField(max_length=16, choices=PROVIDERS, default='twilio')
    channel = models.CharField(max_length=10, choices=CHANNELS, default='whatsapp')
    account_id = models.CharField('account SID / phone number ID', max_length=120, blank=True)
    sender = models.CharField('from (number or sender name)', max_length=60, blank=True)
    secret = models.TextField('token (encrypted)', blank=True)
    secret_last4 = models.CharField(max_length=4, blank=True)
    url = models.CharField('gateway URL (HTTP)', max_length=300, blank=True)
    template = models.CharField('WhatsApp template name (Cloud API)', max_length=120, blank=True,
                                help_text='an approved template with one body parameter; empty = plain text (only inside the 24-hour window)')
    template_lang = models.CharField(max_length=10, default='en', blank=True)
    country_code = models.CharField('country code for local numbers', max_length=4, default='971', blank=True)
    events = models.JSONField(default=list, blank=True, help_text='approval, decision, payslip, helpdesk, notification')
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_result = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class MessageLog(models.Model):
    user_id = models.IntegerField(null=True, blank=True, db_index=True)
    employee_id = models.IntegerField(null=True, blank=True)
    to = models.CharField(max_length=30)
    channel = models.CharField(max_length=10)
    kind = models.CharField(max_length=20, blank=True)
    text = models.TextField()
    status = models.CharField(max_length=10, default='queued')   # queued / sent / failed / skipped
    result = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class PushSettings(models.Model):
    """The company's own Firebase project for push notifications to its build of the ZEO app (optional; without it the
    server-wide ZEO_FCM_CREDENTIALS file is used). The service-account key is kept encrypted."""
    enabled = models.BooleanField(default=True)
    project_id = models.CharField(max_length=120, blank=True)
    client_email = models.CharField(max_length=200, blank=True)
    key = models.TextField('service-account JSON (encrypted)', blank=True)
    events = models.JSONField(default=list, blank=True, help_text='empty = every kind (approval, decision, payslip, notification)')
    last_test_at = models.DateTimeField(null=True, blank=True)
    last_test_result = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
