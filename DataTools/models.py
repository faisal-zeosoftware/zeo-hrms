from django.conf import settings
from django.db import models


class FormSetting(models.Model):
    """
    Company-wide layout of a form designed in Settings → Customization → Form designer:
    field labels, mandatory / hidden flags, dropdown values, and the mandatory flag of custom fields.
    One row per form ('employee', 'custom:employee', 'custom:family' ...). Lives in the company schema,
    so every user of the company gets the same form.
    """
    form = models.CharField(max_length=60, unique=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        verbose_name = 'form setting'

    def __str__(self):
        return self.form
