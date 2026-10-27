"""ZEO administration (v1.52.0), per company:

* ReportSchedule   – a report e-mailed on a schedule (daily / weekly / monthly) to a list, as Excel (xlsx) or CSV
* ApiKey           – a key for an integration: acts as a chosen user (read only or read / write), hashed, optional expiry / IPs
* Webhook          – outgoing webhooks on record events (created / updated / deleted) of chosen record types, signed (HMAC)
* WebhookDelivery  – the deliveries (status, response, retries)
* ImportMapping    – the column mapping of an import, remembered per screen and file layout
* ImportBatch      – an import that was committed: the records it created, so it can be undone
* OutOfOffice      – my approvals go to a colleague between two dates
* RetentionPolicy, AnonymisedEmployee, PrivacyExport – data retention and privacy (v1.53.0)
* TestCompany      – a test company made from the setup of this company (v1.53.0)
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL


class ReportSchedule(models.Model):
    FREQ = [('daily', 'Every day'), ('weekly', 'Every week'), ('monthly', 'Every month')]
    FORMATS = [('xlsx', 'Excel (xlsx)'), ('csv', 'CSV')]
    PERIODS = [('last_month', 'Last month'), ('this_month', 'This month so far'), ('last_week', 'Last week'), ('yesterday', 'Yesterday'),
               ('today', 'As of the day it is sent')]
    name = models.CharField(max_length=120)
    report = models.CharField(max_length=80, help_text='report key (Reports)')
    params = models.JSONField(default=dict, blank=True, help_text='branch ids etc.')
    period = models.CharField(max_length=12, choices=PERIODS, default='last_month')
    frequency = models.CharField(max_length=8, choices=FREQ, default='monthly')
    weekday = models.PositiveSmallIntegerField(default=0, help_text='0 = Monday (weekly)')
    day = models.PositiveSmallIntegerField(default=1, help_text='day of the month (monthly)')
    hour = models.PositiveSmallIntegerField(default=7)
    fmt = models.CharField('format', max_length=4, choices=FORMATS, default='xlsx')
    recipients = models.JSONField(default=list, blank=True, help_text='e-mail addresses')
    owner = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+', help_text='runs with the rights of this user')
    active = models.BooleanField(default=True)
    last_run = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=255, blank=True)
    next_run = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']


class ApiKey(models.Model):
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+', help_text='the key acts as this user (rights and branches)')
    read_only = models.BooleanField(default=True)
    allowed_ips = models.JSONField(default=list, blank=True)
    scopes = models.JSONField(default=list, blank=True, help_text='v1.61.0 API v1 scopes, e.g. ["employees:read"]')
    v1_only = models.BooleanField(default=False, help_text='v1.61.0 the key may call /api/v1/ only')
    expires_at = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    last_used = models.DateTimeField(null=True, blank=True)
    last_ip = models.CharField(max_length=60, blank=True)
    uses = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']


class Webhook(models.Model):
    name = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    secret = models.TextField('signing secret (encrypted)', blank=True)
    models_list = models.JSONField('record types', default=list, help_text='["calendars.employee_leave_request", …]')
    events = models.JSONField(default=list, help_text='created, updated, deleted')
    active = models.BooleanField(default=True)
    last_status = models.CharField(max_length=120, blank=True)
    last_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']


class WebhookDelivery(models.Model):
    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name='deliveries')
    event = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, default='queued')   # queued / sent / failed
    code = models.PositiveSmallIntegerField(null=True, blank=True)
    response = models.CharField(max_length=255, blank=True)
    tries = models.PositiveSmallIntegerField(default=0)
    next_try = models.DateTimeField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class ImportMapping(models.Model):
    endpoint = models.CharField(max_length=200, db_index=True)
    name = models.CharField(max_length=120)
    headers_key = models.CharField(max_length=64, db_index=True, help_text='hash of the file columns')
    headers = models.JSONField(default=list)
    mapping = models.JSONField(default=dict, help_text='{file column: field}')
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    used = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class ImportBatch(models.Model):
    STATUS = [('done', 'Imported'), ('undone', 'Undone'), ('partly', 'Partly undone')]
    endpoint = models.CharField(max_length=200)
    label = models.CharField(max_length=120, blank=True)
    file_name = models.CharField(max_length=200, blank=True)
    model = models.CharField(max_length=100, blank=True, help_text='app_label.model of the created records')
    created_ids = models.JSONField(default=list)
    rows = models.PositiveIntegerField(default=0)
    ok = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=8, choices=STATUS, default='done')
    undo_note = models.TextField(blank=True)
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    undone_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    undone_at = models.DateTimeField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class OutOfOffice(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    delegate = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    start_date = models.DateField()
    end_date = models.DateField()
    note = models.CharField(max_length=255, blank=True)
    include_pending = models.BooleanField(default=True, help_text='also hand over the approvals already waiting')
    active = models.BooleanField(default=True)
    moved = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date']


# ------------------------------------------------------------------ v1.53.0 data retention and privacy (68)
class RetentionPolicy(models.Model):
    """One row per company: when the personal data of employees who left is anonymised."""
    anonymise_after_years = models.PositiveSmallIntegerField(default=0, help_text='years after the last working day; 0 = never')
    automatic = models.BooleanField(default=False, help_text='the daily job anonymises who is due (otherwise HR does it from the list)')
    delete_documents = models.BooleanField(default=True, help_text='employee documents (passport, visa …) and their files are deleted')
    delete_attachments = models.BooleanField(default=False, help_text='also photos and files on leave, attendance and requests')
    updated_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)
    last_run = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=255, blank=True, default='')


class AnonymisedEmployee(models.Model):
    """An employee whose personal data was removed (the payroll, leave and attendance records stay, without the person)."""
    employee_id = models.IntegerField(db_index=True)
    emp_code = models.CharField(max_length=50)
    left_on = models.DateField(null=True, blank=True)
    removed = models.JSONField(default=dict, blank=True, help_text='what was removed: fields and records per type')
    automatic = models.BooleanField(default=False)
    by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at']


class PrivacyExport(models.Model):
    """An export of all the data about an employee (PDPL / GDPR access request)."""
    employee_id = models.IntegerField(db_index=True)
    emp_code = models.CharField(max_length=50)
    records = models.PositiveIntegerField(default=0)
    files = models.PositiveIntegerField(default=0)
    by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    self_service = models.BooleanField(default=False)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at']


# ------------------------------------------------------------------ v1.53.0 test company (67)
class TestCompany(models.Model):
    """A test company made from this company's setup (not its data); e-mails off."""
    STATES = [('running', 'Being made'), ('done', 'Ready'), ('failed', 'Failed'), ('deleted', 'Deleted')]
    name = models.CharField(max_length=100)
    schema_name = models.CharField(max_length=63, blank=True, default='')
    company_id = models.IntegerField(null=True, blank=True)
    kinds = models.JSONField(default=list, blank=True)
    state = models.CharField(max_length=8, choices=STATES, default='running')
    step = models.CharField(max_length=120, blank=True, default='')
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default='')
    by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at']


# ------------------------------------------------------------------ v1.56.0 setup guide (77)
class SetupGuide(models.Model):
    """One row per company: the progress of the setup guide and the settings it keeps that have no other home."""
    steps = models.JSONField(default=dict, blank=True, help_text='{step: {status: done | skipped, at, by}}')
    current = models.CharField(max_length=20, blank=True, default='start')
    policies = models.JSONField(default=dict, blank=True, help_text='HR policy defaults: notice_days, contract_type, salary_days …')
    eid = models.JSONField(default=dict, blank=True, help_text='{year: {confirmed: [holiday ids], at, by}}')
    accepted = models.JSONField(default=dict, blank=True, help_text='{check key: {note, by, at}} – warnings accepted at go-live')
    live_at = models.DateTimeField(null=True, blank=True)
    live_by_id = models.IntegerField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class NumberingPattern(models.Model):
    """One pattern for every document type: tokens {TYPE} {BRANCH} {LOC} {YYYY} {YY} {MM} {SEQ:n}."""
    RESTART = [('year', 'Every year'), ('month', 'Every month'), ('never', 'Never')]
    SCOPE = [('company', 'Per company'), ('branch', 'Per branch'), ('location', 'Per location')]
    pattern = models.CharField(max_length=80, default='{TYPE}-{BRANCH}-{YYYY}-{SEQ:5}')
    restart = models.CharField(max_length=6, choices=RESTART, default='year')
    scope = models.CharField(max_length=8, choices=SCOPE, default='branch')
    codes = models.JSONField(default=dict, blank=True, help_text='{document type: code}')
    active = models.BooleanField(default=False)
    applied_at = models.DateTimeField(null=True, blank=True)
    applied_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()


class NumberingCounter(models.Model):
    type = models.CharField(max_length=50)
    scope_key = models.CharField(max_length=40, default='', blank=True)
    period = models.CharField(max_length=8, default='', blank=True)
    last = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['type', 'scope_key', 'period'], name='zeoadmin_numcounter_unique')]


class SignInInvite(models.Model):
    """An e-mail link to choose a password (instead of a password sent by e-mail)."""
    user_id = models.IntegerField(db_index=True)
    employee_id = models.IntegerField(null=True, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    email = models.CharField(max_length=200, blank=True, default='')
    sent_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    by_id = models.IntegerField(null=True, blank=True)
    # v1.62.0 Give access: an e-mail link, a printed slip (code + QR) or a code by WhatsApp / SMS
    kind = models.CharField(max_length=8, default='link', choices=(('link', 'E-mail link'), ('slip', 'Printed slip'), ('code', 'WhatsApp / SMS code'),
                                                                   ('kiosk', 'Kiosk PIN code')))
    code_hash = models.CharField(max_length=64, blank=True, default='', db_index=True, help_text='sha256 of the slip / message code')
    tries = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-sent_at']


# ------------------------------------------------------------------ v1.61.0 legal hold
class LegalHold(models.Model):
    """The records of an employee may not be deleted or anonymised while a hold is active (a claim, a labour case, an audit).
    Released holds stay as history."""
    employee_id = models.IntegerField(db_index=True)
    emp_code = models.CharField(max_length=50, blank=True)
    reason = models.CharField(max_length=300)
    case_ref = models.CharField(max_length=120, blank=True, default='', help_text='court / labour case or claim number')
    placed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    placed_at = models.DateTimeField(auto_now_add=True)
    released_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    released_at = models.DateTimeField(null=True, blank=True)
    release_note = models.CharField(max_length=300, blank=True, default='')
    # v1.62.0 a hold on a case (disciplinary case, dispute) or on the whole company – history cleaning leaves them out
    scope = models.CharField(max_length=8, default='employee', choices=(('employee', 'Employee'), ('case', 'Case'), ('company', 'Whole company')))
    case_model = models.CharField(max_length=120, blank=True, default='', help_text='app_label.ModelName of the held case')
    case_id = models.CharField(max_length=64, blank=True, default='')
    case_label = models.CharField(max_length=200, blank=True, default='')

    class Meta:
        ordering = ['-placed_at']



# ------------------------------------------------------------------ v1.62.0 history cleaning, exports, bank changes, access, rights
class HistoryPolicy(models.Model):
    """One row per company: how long each kind of log / history is kept (days; 0 = for ever)."""
    days = models.JSONField(default=dict, blank=True, help_text='{category: days}; missing = the default of the category')
    nightly = models.BooleanField(default=False, help_text='the nightly job cleans by these periods')
    updated_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)


class HistoryRun(models.Model):
    """A cleaning (or its preview): what was removed, the archive of the audit history removed, who / the nightly job."""
    HOW = [('preview', 'Preview'), ('manual', 'Clean now'), ('nightly', 'Nightly job'), ('user', 'My recently viewed'), ('server', 'Server files')]
    how = models.CharField(max_length=8, choices=HOW)
    categories = models.JSONField(default=list, blank=True)
    counts = models.JSONField(default=dict, blank=True, help_text='{category: {rows, bytes, kept_by_hold}}')
    archive = models.CharField(max_length=300, blank=True, default='', help_text='the zip of the audit history removed')
    archive_rows = models.PositiveIntegerField(default=0)
    finished = models.BooleanField(default=True, help_text='False when the time limit stopped it (it goes on next night)')
    by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)
    seconds = models.FloatField(default=0)

    class Meta:
        ordering = ['-at']


class ExportLog(models.Model):
    """Every export (Excel, CSV, PDF) – who, which screen, how many rows."""
    user = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    path = models.CharField(max_length=300)
    kind = models.CharField(max_length=10, blank=True, default='')
    rows = models.IntegerField(null=True, blank=True)
    bytes = models.PositiveIntegerField(default=0)
    refused = models.CharField(max_length=200, blank=True, default='')
    watermarked = models.BooleanField(default=False)
    ip = models.CharField(max_length=60, blank=True, default='')
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-at']


class BankChange(models.Model):
    """A bank-detail change made by HR that waits for a second person (company rule), and every change told to the employee."""
    STATES = [('pending', 'Waiting for a second person'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('applied', 'Applied directly')]
    employee_id = models.IntegerField(db_index=True)
    emp_code = models.CharField(max_length=50, blank=True, default='')
    model = models.CharField(max_length=120, help_text='app_label.ModelName of the bank record')
    object_id = models.CharField(max_length=64, blank=True, default='', help_text='empty = a new bank record')
    action = models.CharField(max_length=8, default='update', choices=[('create', 'Add'), ('update', 'Change'), ('delete', 'Remove')])
    old = models.JSONField(default=dict, blank=True)
    new = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=8, choices=STATES, default='pending')
    requested_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True, default='')

    class Meta:
        ordering = ['-requested_at']


class AccessGrant(models.Model):
    """Give access: how a login was given to an employee (or why not) – the audit of access given, links, slips, codes."""
    METHODS = [('link', 'E-mail link'), ('existing', 'Existing login added'), ('generate', 'Generated password'), ('code', 'WhatsApp / SMS code'),
               ('slip', 'Printed slip'), ('kiosk', 'Kiosk only'), ('typed', 'Password typed by HR'), ('default', 'Company default password'),
               ('sso', 'Single sign-on'), ('missed', 'Not given')]
    employee_id = models.IntegerField(db_index=True)
    emp_code = models.CharField(max_length=50, blank=True, default='')
    user_id = models.IntegerField(null=True, blank=True)
    method = models.CharField(max_length=10, choices=METHODS)
    detail = models.CharField(max_length=300, blank=True, default='')
    by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at']


class SecurityRight(models.Model):
    """No rows – it carries the rights of the 1.62.0 data protection rules (Permission groups)."""
    name = models.CharField(max_length=10, blank=True)

    class Meta:
        default_permissions = ()
        permissions = [('see_bank_details', 'Can see full bank details (IBAN, account number)'),
                       ('see_identity_numbers', 'Can see full Emirates ID and passport numbers'),
                       ('see_salaries', 'Can see salaries when they are masked'),
                       ('export_lists', 'Can export lists (Excel, CSV, PDF)'),
                       ('export_large', 'Can export large lists (above the row limit)'),
                       ('approve_bank_change', 'Can approve bank-detail changes'),
                       ('give_access', 'Can give employees access (logins)'),
                       ('clean_history', 'Can clean logs and history')]
