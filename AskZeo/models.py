"""Ask ZEO 1.71.0 – the chat assistant of ZEO (tenant app: one set of tables per company).

Tables
* AskSettings      one row per company: the company switches (Ask ZEO on, "Ask ZEO may change setup", salary kept from the AI …)
* Conversation     a chat of one user
* Message          one question or answer (who asked what, the intent found, the source of the answer)
* Upload           a file attached to a question (template upload, document to read)
* Change           every change made via Ask ZEO: preview → confirm → applied → undone, with the values before and after
* AskUsage         one row per answer: user, feature, mode (built-in / AI), AI calls, tokens and estimated cost (1.72.0)
* CompanyDoc / DocPart   company documents (policy, handbook, SOP) cut into sections for the free local search (1.72.0, A3)
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL

SOURCES = (('data', 'Company data'), ('document', 'Company document'), ('general', 'General knowledge'), ('help', 'ZEO help'),
           ('check', 'ZEO check'))
CHANGE_STATES = (('preview', 'Preview'), ('applied', 'Applied'), ('undone', 'Undone'), ('failed', 'Failed'), ('cancelled', 'Cancelled'))


class AskSettings(models.Model):
    enabled = models.BooleanField(default=True, help_text='Ask ZEO is open to the users of this company')
    may_change_setup = models.BooleanField(default=False, help_text='Ask ZEO may change setup and data (always after a preview and a confirmation)')
    employees_may_ask = models.BooleanField(default=True, help_text='Self-service users may ask about their own data')
    hide_salary = models.BooleanField(default=True, help_text='Salary amounts are never sent to the AI (kept on the server)')
    mask_names = models.BooleanField(default=False, help_text='Employee names are replaced by codes before anything is sent to the AI')
    monthly_budget_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text='0 = no budget (only the call limit of AI settings)')
    model_ask = models.CharField(max_length=120, blank=True, default='', help_text='Model for Ask ZEO (empty = the AI settings model)')
    model_small = models.CharField(max_length=120, blank=True, default='', help_text='Smaller model for documents, mapping and tickets')
    price_in_per_mtok = models.DecimalField(max_digits=8, decimal_places=3, default=3, help_text='USD per million input tokens (for the estimate)')
    price_out_per_mtok = models.DecimalField(max_digits=8, decimal_places=3, default=15, help_text='USD per million output tokens (for the estimate)')
    summary_email = models.BooleanField(default=False, help_text='Monthly AI summary e-mail to HR and management')
    summary_to = models.TextField(blank=True, default='', help_text='E-mail addresses, comma separated (empty = company administrators)')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'Ask ZEO settings'
        verbose_name_plural = 'Ask ZEO settings'

    @classmethod
    def get(cls):
        return cls.objects.order_by('id').first() or cls.objects.create()

    def __str__(self):
        return 'Ask ZEO settings'


class Conversation(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    title = models.CharField(max_length=200, blank=True, default='')
    suite = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return self.title or f'Conversation {self.pk}'


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=(('user', 'User'), ('assistant', 'Ask ZEO')))
    text = models.TextField(blank=True, default='')
    intent = models.CharField(max_length=40, blank=True, default='')
    source = models.CharField(max_length=10, choices=SOURCES, blank=True, default='')
    mode = models.CharField(max_length=10, blank=True, default='', help_text='builtin / ai')
    data = models.JSONField(default=dict, blank=True, help_text='cards, links and actions of an answer')
    language = models.CharField(max_length=5, blank=True, default='en')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.role}: {self.text[:60]}'


class Upload(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name='uploads')
    file = models.FileField(upload_to='ask_zeo/%Y/%m/')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    kind = models.CharField(max_length=20, blank=True, default='', help_text='workbook / spreadsheet / document / image / other')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return self.name


class Change(models.Model):
    """A change proposed and (after confirmation) made via Ask ZEO. Undo puts the values back."""
    user = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='+')
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name='changes')
    kind = models.CharField(max_length=40, help_text='update / create / action / guided step …')
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default='')
    plan = models.JSONField(default=dict, blank=True, help_text='what will be done: [{op, model, id, values}] or an action and its arguments')
    before = models.JSONField(default=list, blank=True, help_text='values before the change, per record')
    after = models.JSONField(default=list, blank=True, help_text='values after the change, per record')
    result = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=10, choices=CHANGE_STATES, default='preview', db_index=True)
    undoable = models.BooleanField(default=True)
    error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    undone_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-created_at', '-id']
        permissions = [('view_all_askzeo_changes', 'Can see every change made via Ask ZEO')]

    def __str__(self):
        return f'{self.title} ({self.state})'


class AskUsage(models.Model):
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='+')
    feature = models.CharField(max_length=30, default='ask')
    mode = models.CharField(max_length=10, default='builtin')
    calls = models.PositiveIntegerField(default=0)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cached = models.BooleanField(default=False)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=5, default=0)

    class Meta:
        ordering = ['-at', '-id']


DOC_ACCESS = (('all', 'Everybody in the company'), ('backoffice', 'HR and managers'), ('admin', 'Administrators only'))
DOC_KINDS = (('policy', 'Policy'), ('handbook', 'Employee handbook'), ('sop', 'SOP / procedure'), ('other', 'Other'))


class CompanyDoc(models.Model):
    """v1.72.0 (A3) – a company document Ask ZEO answers from (policy, handbook, SOP); cut into DocPart sections for the local search."""
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=DOC_KINDS, default='policy')
    file = models.FileField(upload_to='ask_zeo/docs/%Y/%m/')
    file_name = models.CharField(max_length=255, blank=True, default='')
    access = models.CharField(max_length=12, choices=DOC_ACCESS, default='all')
    branches = models.ManyToManyField('OrganisationManager.brnch_mstr', blank=True, related_name='+',
                                      help_text='Only these branches (empty = every branch)')
    active = models.BooleanField(default=True)
    chars = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(USER, null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['title', 'id']

    def __str__(self):
        return self.title


class DocPart(models.Model):
    doc = models.ForeignKey(CompanyDoc, on_delete=models.CASCADE, related_name='parts')
    order = models.PositiveIntegerField(default=0)
    title = models.CharField(max_length=200, blank=True, default='')
    text = models.TextField()

    class Meta:
        ordering = ['doc', 'order']
