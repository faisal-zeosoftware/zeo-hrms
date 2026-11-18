"""v1.70.1 – Document Center (DMS SOP phases 1 and 2).

One folder tree for the company:
  * system folders made from the apps' records (HR › Employees › <code – name>, Projects › <project>, Business partners › …,
    Property › Buildings › …, Assets › …, HSE › Sites › …, CRM › Accounts › …) – who may open them follows the app's own rights;
  * users' own folders (anywhere they may create), shared through folder rights.

Documents are registered with a number, category, metadata, confidentiality and an owner; every file change is a version
(1.0, 1.1, 2.0 …). A category with a workflow makes its documents controlled: draft → review → approval → published; a published
version is never edited – a change is a new revision; the old published version becomes superseded.
"""
from django.db import models

USER = 'UserManagement.CustomUser'

RIGHTS = ('view', 'create', 'upload', 'edit', 'review', 'approve', 'publish', 'download', 'print', 'share', 'archive', 'dispose', 'admin')
RIGHT_LABELS = {'view': 'View', 'create': 'Create folders', 'upload': 'Upload', 'edit': 'Edit', 'review': 'Review', 'approve': 'Approve',
                'publish': 'Publish', 'download': 'Download', 'print': 'Print', 'share': 'Share', 'archive': 'Archive / delete',
                'dispose': 'Dispose', 'admin': 'Manage rights'}
PRESETS = {
    'viewer': ('view', 'download', 'print'),
    'contributor': ('view', 'download', 'print', 'create', 'upload', 'edit'),
    'reviewer': ('view', 'download', 'print', 'create', 'upload', 'edit', 'review'),
    'approver': ('view', 'download', 'print', 'create', 'upload', 'edit', 'review', 'approve', 'publish'),
    'manager': ('view', 'download', 'print', 'create', 'upload', 'edit', 'review', 'approve', 'publish', 'share', 'archive', 'admin'),
}
STATUS = [('draft', 'Draft'), ('submitted', 'Submitted'), ('under_review', 'Under review'), ('revision_required', 'Revision required'),
          ('pending_approval', 'Pending approval'), ('approved', 'Approved'), ('published', 'Published'), ('rejected', 'Rejected'),
          ('superseded', 'Superseded'), ('expired', 'Expired'), ('archived', 'Archived'), ('obsolete', 'Obsolete'), ('disposed', 'Disposed')]
CONFIDENTIALITY = [('public', 'Public'), ('internal', 'Internal'), ('confidential', 'Confidential'), ('restricted', 'Restricted')]
ALERT_DAYS = [90, 60, 30, 15, 7]   # v1.73.6 the SOP's alerts before expiry
RETENTION_START = [('created', 'Registered'), ('effective', 'Effective date'), ('expiry', 'Expiry date'), ('archived', 'Archived')]
RETENTION_ACTION = [('review', 'Review'), ('dispose', 'Dispose'), ('archive', 'Archive')]


class DocCategory(models.Model):
    """Document type / category (with subcategories): its own fields, numbering, workflow and defaults."""
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=12, help_text='Short code in the document number, e.g. POL')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    description = models.CharField(max_length=255, blank=True, default='')
    fields = models.JSONField(default=list, blank=True, help_text='[{key, label, field_type (the 20 designer types), options, required, section, order, help_text, placeholder, default, hidden, read_only, rules}]')
    number_pattern = models.CharField(max_length=120, blank=True, default='{dept}-{cat}-{yyyy}-{seq:4}',
                                      help_text='{company} {branch} {dept} {cat} {yyyy} {yy} {mm} {seq:N}')
    number_reset = models.CharField(max_length=10, default='yearly', choices=[('never', 'Never'), ('yearly', 'Every year'), ('monthly', 'Every month')])
    workflow = models.ForeignKey('DocWorkflow', null=True, blank=True, on_delete=models.SET_NULL, related_name='categories')
    default_confidentiality = models.CharField(max_length=12, choices=CONFIDENTIALITY, default='internal')
    expiry_required = models.BooleanField(default=False)
    retention_years = models.PositiveSmallIntegerField(null=True, blank=True)
    # v1.73.6 (DMS phase 3) expiry and renewal, retention
    alert_days = models.JSONField(default=list, blank=True, help_text='days before expiry the renewal owner is told, e.g. [90, 60, 30, 15, 7] (empty = these)')
    renewal_owner = models.CharField(max_length=8, default='owner', choices=[('owner', 'Document owner'), ('user', 'A named user')])
    renewal_user_id = models.IntegerField(null=True, blank=True)
    escalate_days = models.PositiveSmallIntegerField(default=7, help_text='days after expiry without a renewal before it is escalated')
    escalate_user_id = models.IntegerField(null=True, blank=True, help_text='empty = the Document Center administrators')
    retention_start = models.CharField(max_length=10, default='effective', choices=RETENTION_START)
    retention_action = models.CharField(max_length=8, default='review', choices=RETENTION_ACTION)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        unique_together = ('parent', 'name')
        permissions = [('manage_dms', 'Configure the Document Center (categories, workflows, numbering, all folders)')]

    def __str__(self):
        return f'{self.parent.name} › {self.name}' if self.parent_id else self.name


class DocCounter(models.Model):
    key = models.CharField(max_length=160, unique=True)
    value = models.PositiveIntegerField(default=0)


class DocWorkflow(models.Model):
    """Review and approval stages: [{name, kind: review|approve, users: [ids], groups: [ids], role: owner_manager|dept_head|'',
    mode: any|all, due_days}] – stages run one after another; inside a stage everyone at once (parallel), any one or all of them."""
    name = models.CharField(max_length=120, unique=True)
    stages = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class DocFolder(models.Model):
    KINDS = [('root', 'System folder'), ('record', 'Record folder'), ('user', 'Folder')]
    name = models.CharField(max_length=200)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='children')
    kind = models.CharField(max_length=8, choices=KINDS, default='user')
    provider = models.CharField(max_length=30, blank=True, default='', db_index=True, help_text='record folders: employee, project, partner …')
    record_id = models.IntegerField(null=True, blank=True, db_index=True)
    branch_id = models.IntegerField(null=True, blank=True)
    owner_id = models.IntegerField(null=True, blank=True)
    private = models.BooleanField(default=False, help_text='"My folders": only the owner (and the rights given) see it')
    inherit = models.BooleanField(default=True, help_text='takes the rights of the folder above')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by_id = models.IntegerField(null=True, blank=True)
    fields = models.JSONField(default=dict, blank=True, help_text='v1.70.7: values of the folder fields {key: value}')

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['provider', 'record_id'])]

    def __str__(self):
        return self.name


class DocGrant(models.Model):
    """Rights on a folder (and its subfolders while they inherit) or on one document."""
    WHO = [('user', 'User'), ('group', 'Permission group'), ('branch', 'Branch'), ('department', 'Department'), ('everyone', 'Everyone')]
    folder = models.ForeignKey(DocFolder, null=True, blank=True, on_delete=models.CASCADE, related_name='grants')
    document = models.ForeignKey('Document', null=True, blank=True, on_delete=models.CASCADE, related_name='grants')
    who = models.CharField(max_length=10, choices=WHO)
    who_id = models.IntegerField(null=True, blank=True)
    rights = models.JSONField(default=list)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Document(models.Model):
    number = models.CharField(max_length=60, blank=True, default='', db_index=True)
    title = models.CharField(max_length=255)
    folder = models.ForeignKey(DocFolder, on_delete=models.PROTECT, related_name='documents')
    category = models.ForeignKey(DocCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='documents')
    department_id = models.IntegerField(null=True, blank=True)
    branch_id = models.IntegerField(null=True, blank=True)
    employee_id = models.IntegerField(null=True, blank=True)
    project_id = models.IntegerField(null=True, blank=True)
    partner_id = models.IntegerField(null=True, blank=True)
    owner_id = models.IntegerField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    confidentiality = models.CharField(max_length=12, choices=CONFIDENTIALITY, default='internal')
    tags = models.JSONField(default=list, blank=True)
    remarks = models.TextField(blank=True, default='')
    fields = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='draft', db_index=True)
    controlled = models.BooleanField(default=False, help_text='goes through review / approval; published versions are never edited')
    current = models.ForeignKey('Version', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='newest version')
    published = models.ForeignKey('Version', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='version in force')
    locked_by_id = models.IntegerField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    retention_until = models.DateField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by_id = models.IntegerField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    # v1.73.6 (DMS phase 3)
    renewal_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='renewals', help_text='the document this one renews')
    renewal_owner_id = models.IntegerField(null=True, blank=True, help_text='who renews it (empty = the category rule, else the owner)')
    legal_hold = models.BooleanField(default=False, help_text='never deleted, emptied from the trash or disposed while held')
    legal_hold_reason = models.CharField(max_length=255, blank=True, default='')
    legal_hold_by_id = models.IntegerField(null=True, blank=True)
    legal_hold_at = models.DateTimeField(null=True, blank=True)
    archive_location = models.CharField(max_length=120, blank=True, default='', help_text='where the paper original is kept (box, shelf, store)')
    archived_by_id = models.IntegerField(null=True, blank=True)
    retention_reviewed_at = models.DateTimeField(null=True, blank=True)
    retention_review_note = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.number} {self.title}'.strip()


class Version(models.Model):
    STATES = [('draft', 'Draft'), ('in_review', 'In review'), ('returned', 'Returned for correction'), ('approved', 'Approved'),
              ('published', 'Published'), ('rejected', 'Rejected'), ('superseded', 'Superseded')]
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='versions')
    major = models.PositiveIntegerField(default=1)
    minor = models.PositiveIntegerField(default=0)
    file = models.FileField(upload_to='dms/%Y/%m/')
    file_name = models.CharField(max_length=255)
    size = models.BigIntegerField(default=0)
    mime = models.CharField(max_length=120, blank=True, default='')
    sha256 = models.CharField(max_length=64, blank=True, default='', db_index=True)
    text = models.TextField(blank=True, default='', help_text='text read from the file, for search and comparison')
    # v1.73.10 (DMS phase 5) where the text came from: text (in the file), ocr (read from the picture), none (nothing readable), '' = not tried
    text_source = models.CharField(max_length=6, blank=True, default='', db_index=True)
    ocr_at = models.DateTimeField(null=True, blank=True)
    change_note = models.CharField(max_length=500, blank=True, default='')
    state = models.CharField(max_length=12, choices=STATES, default='draft')
    restored_from = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    author_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-major', '-minor', '-id']

    @property
    def label(self):
        return f'{self.major}.{self.minor}'


class Approval(models.Model):
    DECISIONS = [('waiting', 'Waiting'), ('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('returned', 'Returned'),
                 ('skipped', 'Not needed')]
    version = models.ForeignKey(Version, on_delete=models.CASCADE, related_name='approvals')
    stage = models.PositiveSmallIntegerField(default=0)
    stage_name = models.CharField(max_length=120, blank=True, default='')
    kind = models.CharField(max_length=8, default='approve')
    mode = models.CharField(max_length=4, default='any')
    approver_id = models.IntegerField()
    decision = models.CharField(max_length=10, choices=DECISIONS, default='waiting')
    comment = models.TextField(blank=True, default='')
    due_date = models.DateField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    delegated_from_id = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['stage', 'id']


class Distribution(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='distributions')
    version = models.ForeignKey(Version, on_delete=models.CASCADE, related_name='distributions')
    user_id = models.IntegerField()
    via = models.CharField(max_length=40, blank=True, default='')
    require_ack = models.BooleanField(default=False)
    sent_by_id = models.IntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    viewed_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('version', 'user_id')


class ShareLink(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='links')
    version = models.ForeignKey(Version, null=True, blank=True, on_delete=models.CASCADE, related_name='+', help_text='empty = the version in force')
    token = models.CharField(max_length=64, unique=True)
    recipient = models.CharField(max_length=200, blank=True, default='')
    expires_at = models.DateTimeField()
    max_downloads = models.PositiveIntegerField(null=True, blank=True)
    downloads = models.PositiveIntegerField(default=0)
    allow_download = models.BooleanField(default=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Activity(models.Model):
    document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.CASCADE, related_name='activity')
    folder = models.ForeignKey(DocFolder, null=True, blank=True, on_delete=models.CASCADE, related_name='activity')
    user_id = models.IntegerField(null=True, blank=True)
    action = models.CharField(max_length=30, db_index=True)
    detail = models.CharField(max_length=500, blank=True, default='')
    ip = models.CharField(max_length=64, blank=True, default='')
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-at', '-id']


class DocField(models.Model):
    """v1.70.7 – a field of every document or of every folder, added with the form designer of the Document Center
    (the category's own fields stay on the category). Values: Document.fields / DocFolder.fields {key: value}."""
    SCOPES = [('document', 'Every document'), ('folder', 'Folders')]
    scope = models.CharField(max_length=10, choices=SCOPES)
    key = models.CharField(max_length=40)
    label = models.CharField(max_length=80)
    field_type = models.CharField(max_length=20, default='text')
    options = models.JSONField(default=list, blank=True)
    required = models.BooleanField(default=False)
    section = models.CharField(max_length=80, blank=True, default='')
    order = models.IntegerField(default=0)
    help_text = models.CharField(max_length=255, blank=True, default='')
    placeholder = models.CharField(max_length=120, blank=True, default='')
    default = models.CharField(max_length=255, blank=True, default='')
    hidden = models.BooleanField(default=False, help_text='not shown on the forms; values kept')
    read_only = models.BooleanField(default=False, help_text='only its default or a Document Center administrator fills it')
    rules = models.JSONField(default=dict, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scope', 'order', 'id']
        unique_together = ('scope', 'key')

    def __str__(self):
        return f'{self.scope}: {self.label}'


# ================================================================== v1.73.6 – DMS SOP phase 3
class ExpiryAlert(models.Model):
    """one row per alert sent – so an alert is never sent twice (days before expiry, escalation, retention review)"""
    KINDS = [('alert', 'Before expiry'), ('escalation', 'Escalation'), ('retention', 'Retention review')]
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='expiry_alerts')
    kind = models.CharField(max_length=10, choices=KINDS, default='alert')
    days = models.IntegerField(default=0)
    to_user_id = models.IntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('document', 'kind', 'days')


class DisposalRecord(models.Model):
    """the disposal register: what was disposed, under which rule, who authorised it, the evidence – kept after the files are gone"""
    METHODS = [('deleted', 'Files deleted'), ('shredded', 'Paper shredded'), ('returned', 'Returned to the owner'), ('transferred', 'Transferred'),
               ('other', 'Other')]
    document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL, related_name='disposals')
    number = models.CharField(max_length=60, blank=True, default='')
    title = models.CharField(max_length=255, blank=True, default='')
    category = models.CharField(max_length=160, blank=True, default='')
    retention_until = models.DateField(null=True, blank=True)
    early = models.BooleanField(default=False, help_text='disposed before the end of retention (administrator, with a reason)')
    reason = models.CharField(max_length=255, blank=True, default='')
    method = models.CharField(max_length=12, choices=METHODS, default='deleted')
    certificate = models.CharField(max_length=60, blank=True, default='', help_text='destruction certificate / reference')
    evidence = models.FileField(upload_to='dms/disposal/', blank=True, default='')
    evidence_name = models.CharField(max_length=255, blank=True, default='')
    authorised_by_id = models.IntegerField(null=True, blank=True)
    authorised_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-authorised_at']


class Correspondence(models.Model):
    """incoming and outgoing letters: the registers with reference numbers, approval of outgoing letters, dispatch, deadlines"""
    DIRECTIONS = [('in', 'Incoming'), ('out', 'Outgoing')]
    CHANNELS = [('email', 'E-mail'), ('courier', 'Courier'), ('post', 'Post'), ('hand', 'By hand'), ('portal', 'Government portal'), ('fax', 'Fax')]
    STATES = [('registered', 'Registered'), ('assigned', 'Assigned'), ('draft', 'Draft'), ('pending_approval', 'Waiting for approval'),
              ('approved', 'Approved'), ('rejected', 'Rejected'), ('dispatched', 'Dispatched'), ('replied', 'Replied'), ('closed', 'Closed')]
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    number = models.CharField(max_length=40, blank=True, default='', db_index=True)
    date = models.DateField()
    subject = models.CharField(max_length=255)
    party = models.CharField(max_length=200, help_text='sender (incoming) or addressee (outgoing)')
    partner_id = models.IntegerField(null=True, blank=True)
    their_reference = models.CharField(max_length=80, blank=True, default='')
    channel = models.CharField(max_length=8, choices=CHANNELS, default='email')
    document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL, related_name='correspondence', help_text='the letter in the Document Center')
    reply_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replies')
    deadline = models.DateField(null=True, blank=True, help_text='reply / action due')
    assigned_to_id = models.IntegerField(null=True, blank=True)
    branch_id = models.IntegerField(null=True, blank=True)
    department_id = models.IntegerField(null=True, blank=True)
    confidentiality = models.CharField(max_length=12, choices=CONFIDENTIALITY, default='internal')
    status = models.CharField(max_length=16, choices=STATES, default='registered', db_index=True)
    approver_id = models.IntegerField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True, default='')
    dispatch_method = models.CharField(max_length=8, choices=CHANNELS, blank=True, default='')
    tracking = models.CharField(max_length=80, blank=True, default='')
    dispatched_at = models.DateTimeField(null=True, blank=True)
    remarks = models.TextField(blank=True, default='')
    reminded_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-id']
        permissions = [('manage_correspondence', 'Register and dispatch correspondence (incoming and outgoing registers)')]

    def __str__(self):
        return f'{self.number} {self.subject}'


# ================================================================== v1.73.9 DMS phase 4
class DocChecklist(models.Model):
    """Required documents of a kind of record (an employee: passport, visa, Emirates ID, contract …): one item per category.
    A record's checklist is complete when each required item has a document of its category in the record's folder."""
    PROVIDERS = [('employee', 'Employees'), ('project', 'Projects'), ('partner', 'Business partners'), ('building', 'Buildings'),
                 ('asset', 'Assets'), ('hse_site', 'HSE sites'), ('crm_account', 'CRM accounts')]
    name = models.CharField(max_length=120)
    provider = models.CharField(max_length=20, choices=PROVIDERS, default='employee')
    description = models.CharField(max_length=255, blank=True, default='')
    branch_ids = models.JSONField(default=list, blank=True, help_text='[] = every branch')
    department_ids = models.JSONField(default=list, blank=True, help_text='employees only; [] = every department')
    items = models.JSONField(default=list, blank=True, help_text='[{key, label, category_id, required, needs_expiry, note}]')
    active = models.BooleanField(default=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['provider', 'name']
        unique_together = ('provider', 'name')


class UploadRequest(models.Model):
    """External portal: a link someone outside the company (a candidate, a supplier, a tenant) opens to upload the documents asked
    for, straight into a record's folder – no user account. Each upload waits for review before it counts."""
    STATES = [('open', 'Open'), ('completed', 'All received'), ('revoked', 'Withdrawn'), ('expired', 'Expired')]
    token = models.CharField(max_length=64, unique=True)
    folder = models.ForeignKey(DocFolder, on_delete=models.CASCADE, related_name='upload_requests')
    checklist = models.ForeignKey(DocChecklist, null=True, blank=True, on_delete=models.SET_NULL, related_name='requests')
    items = models.JSONField(default=list, help_text='[{key, label, category_id}]')
    received = models.JSONField(default=list, blank=True, help_text='[{key, document_id, file_name, at}]')
    recipient_name = models.CharField(max_length=200)
    recipient_email = models.CharField(max_length=200, blank=True, default='')
    message = models.TextField(blank=True, default='')
    expires_at = models.DateTimeField()
    max_files = models.PositiveSmallIntegerField(default=20)
    state = models.CharField(max_length=10, choices=STATES, default='open')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class DocTemplate(models.Model):
    """Controlled template / form: HTML with merge fields ({{ employee.name }}, {{ today }}, {{ answer.reason }}) and questions to
    fill in. Only a published version makes documents; changing it is a draft until published again (the versions are kept)."""
    STATES = [('draft', 'Draft'), ('published', 'Published'), ('retired', 'Retired')]
    name = models.CharField(max_length=160, unique=True)
    provider = models.CharField(max_length=20, blank=True, default='', help_text="the record kind it is made for ('' = none)")
    category = models.ForeignKey(DocCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='templates')
    body = models.TextField(blank=True, default='', help_text='the working copy')
    questions = models.JSONField(default=list, blank=True, help_text='[{key, label, field_type: text|textarea|number|date|select, options, required}]')
    published_body = models.TextField(blank=True, default='')
    published_questions = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=0, help_text='0 = never published')
    history = models.JSONField(default=list, blank=True, help_text='[{version, body, questions, published_at, published_by}]')
    state = models.CharField(max_length=10, choices=STATES, default='draft')
    published_at = models.DateTimeField(null=True, blank=True)
    published_by_id = models.IntegerField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']


class SignRequest(models.Model):
    """E-signature of one version of a document: signers inside (users) and outside (a link), one after another or all at once.
    When everyone has signed, a signed copy (the file + a signature certificate page) becomes a new version of the document."""
    STATES = [('out', 'Out for signature'), ('completed', 'Signed'), ('declined', 'Declined'), ('cancelled', 'Cancelled'), ('expired', 'Expired')]
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='sign_requests')
    version = models.ForeignKey(Version, on_delete=models.CASCADE, related_name='sign_requests')
    message = models.TextField(blank=True, default='')
    sequential = models.BooleanField(default=True, help_text='one after another in the order given')
    state = models.CharField(max_length=10, choices=STATES, default='out')
    expires_at = models.DateTimeField()
    signed_version = models.ForeignKey(Version, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class Signer(models.Model):
    STATES = [('waiting', 'Waiting for the one before'), ('pending', 'To sign'), ('signed', 'Signed'), ('declined', 'Declined')]
    request = models.ForeignKey(SignRequest, on_delete=models.CASCADE, related_name='signers')
    order = models.PositiveSmallIntegerField(default=1)
    user_id = models.IntegerField(null=True, blank=True)
    name = models.CharField(max_length=200)
    email = models.CharField(max_length=200, blank=True, default='')
    token = models.CharField(max_length=64, unique=True)
    state = models.CharField(max_length=10, choices=STATES, default='waiting')
    typed_name = models.CharField(max_length=200, blank=True, default='')
    image = models.TextField(blank=True, default='', help_text='the drawn signature (PNG data URL)')
    signed_at = models.DateTimeField(null=True, blank=True)
    ip = models.CharField(max_length=64, blank=True, default='')
    user_agent = models.CharField(max_length=255, blank=True, default='')
    decline_reason = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        ordering = ['order', 'id']
