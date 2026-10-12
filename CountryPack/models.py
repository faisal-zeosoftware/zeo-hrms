"""v1.14.0 – Country packs: one settings row per company (tenant schema).

The pack itself (captions, features, rules, defaults) is code (packs.py); this row only keeps the company's choices on top of it.
"""
from django.db import models


class CountryPackSettings(models.Model):
    pack_override = models.CharField(max_length=3, blank=True, default='',
                                     help_text='Empty = the pack of the company country; else AE, SA, QA, OM, BH, KW or GEN')
    label_overrides = models.JSONField(default=dict, blank=True, help_text='{"national_id": "Civil ID", ...}')
    feature_overrides = models.JSONField(default=dict, blank=True, help_text='{"wps_sif": true, ...}')
    updated_at = models.DateTimeField(auto_now=True)
    updated_by_id = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = 'country settings'
        verbose_name_plural = 'country settings'

    @classmethod
    def get(cls):
        row = cls.objects.order_by('id').first()
        return row or cls.objects.create()

    def __str__(self):
        return f'Country settings ({self.pack_override or "from country"})'
