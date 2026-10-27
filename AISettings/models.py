"""v1.16.0 – AI settings per company (tenant schema).

Default: the built-in, free mode (nothing leaves the server) for Ask ZEO and for documents.
A company administrator may connect an external AI provider – only when the server administrator allows it
(ZEO_AI_EXTERNAL=true in .env). The API key is stored encrypted (see crypto.py) and never sent back to the browser.
Every external call is written to AICallLog (who, when, feature, sizes – never the content).
"""
from django.db import models

MODES = (('builtin', 'Built-in (free, data stays on the server)'), ('external', 'External AI'))
PROVIDERS = (('anthropic', 'Anthropic AI'), ('openai', 'OpenAI-compatible'), ('azure', 'Azure OpenAI'))
FEATURES = (('ask', 'Ask ZEO'), ('documents', 'Documents'), ('tickets', 'HR tickets'), ('test', 'Test connection'))


class AISettings(models.Model):
    mode = models.CharField(max_length=10, choices=MODES, default='builtin')
    provider = models.CharField(max_length=12, choices=PROVIDERS, default='anthropic')
    api_url = models.CharField(max_length=500, blank=True, default='', help_text='Empty = the provider\'s standard address')
    model = models.CharField(max_length=120, blank=True, default='')
    api_key_enc = models.TextField(blank=True, default='', help_text='Encrypted (Fernet); never returned by the API')
    key_last4 = models.CharField(max_length=4, blank=True, default='')
    use_ask = models.BooleanField(default=True, help_text='Use the external AI for Ask ZEO')
    use_documents = models.BooleanField(default=False, help_text='Use the external AI to read documents')
    use_tickets = models.BooleanField(default=False, help_text='v1.61.0 Use the external AI to suggest the category and priority of HR tickets')
    monthly_limit = models.PositiveIntegerField(default=500, help_text='External calls per calendar month (whole company)')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'AI settings'
        verbose_name_plural = 'AI settings'

    @classmethod
    def get(cls):
        row = cls.objects.order_by('id').first()
        return row or cls.objects.create()

    def __str__(self):
        return f'AI settings ({self.mode})'


class AICallLog(models.Model):
    """One row per external AI call (or refused / failed attempt). No question, answer or document content."""
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    user_id = models.IntegerField(null=True, blank=True)
    username = models.CharField(max_length=150, blank=True, default='')
    feature = models.CharField(max_length=10, choices=FEATURES)
    provider = models.CharField(max_length=12, blank=True, default='')
    model = models.CharField(max_length=120, blank=True, default='')
    sent = models.BooleanField(default=True, help_text='The request left the server (counts against the monthly limit)')
    ok = models.BooleanField(default=False)
    result = models.CharField(max_length=60, blank=True, default='', help_text='ok, http 401, unreachable, limit reached …')
    request_bytes = models.IntegerField(default=0)
    response_bytes = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)

    class Meta:
        ordering = ['-at', '-id']
        verbose_name = 'AI call'

    def __str__(self):
        return f'{self.at:%Y-%m-%d %H:%M} {self.feature} {self.result}'
