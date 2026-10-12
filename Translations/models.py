"""v1.15.0 – translated master data (like Odoo's translatable fields).

One row per (record, field, language): the department "Finance" (OrganisationManager.dept_master id 3, field dept_name)
has the Arabic value "المالية". The original column keeps the English (source) value; read APIs show the translation when
the request language is not English (see hooks.py). `source` is the English value the translation was made from, so a
changed English name shows the translation as "to check".
"""
from django.db import models


class FieldTranslation(models.Model):
    model = models.CharField(max_length=100, help_text='app_label.ModelName, e.g. OrganisationManager.dept_master')
    record_id = models.BigIntegerField()
    field = models.CharField(max_length=60)
    lang = models.CharField(max_length=10)
    value = models.TextField()
    source = models.TextField(blank=True, default='', help_text='The English value when the translation was saved')
    is_standard = models.BooleanField(default=False, help_text='Loaded from the standard UAE lists (not typed by a user)')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'field translation'
        constraints = [models.UniqueConstraint(fields=['model', 'record_id', 'field', 'lang'], name='translations_ft_unique')]
        indexes = [models.Index(fields=['model', 'lang'], name='translations_ft_model_lang')]

    def __str__(self):
        return f'{self.model}#{self.record_id}.{self.field}[{self.lang}] = {self.value}'
