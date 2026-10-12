"""v1.15.0 – Language packs (like Odoo): languages, translated terms, user and company language.

Everything lives in the PUBLIC schema (the app is in SHARED_APPS only):
  * Language / Term – the packs (loaded from i18n/packs/<lang>.json by `manage.py load_language_packs`, edited on
    Settings → Languages → Translations); the English text as written in the code is the key (`source`).
  * UserLanguage – a user's own language (users are shared between companies).
  * CompanyLanguageSettings – one row per company (keyed by the tenant schema name): default language and the languages
    the company uses. It is kept in the public schema on purpose: an app listed in both SHARED_APPS and TENANT_APPS gets
    ALL its tables in every company schema, and a company copy of the Term table would hide the public one.
"""
import hashlib

from django.db import models

DIRS = [('ltr', 'Left to right'), ('rtl', 'Right to left')]


def source_hash(text):
    return hashlib.sha1((text or '').encode('utf-8')).hexdigest()


class Language(models.Model):
    code = models.CharField(max_length=10, unique=True, help_text='ISO code: en, ar, …')
    name = models.CharField(max_length=60, help_text='Name in the language itself (العربية)')
    english_name = models.CharField(max_length=60)
    dir = models.CharField(max_length=3, choices=DIRS, default='ltr')
    active = models.BooleanField(default=True)
    date_format = models.CharField(max_length=20, default='dd/MM/yyyy')
    number = models.JSONField(default=dict, blank=True, help_text='{"decimal": ".", "group": ","}')
    version = models.PositiveIntegerField(default=1, help_text='Goes up on every change of the terms (browser cache key)')
    sequence = models.PositiveIntegerField(default=10)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sequence', 'code']
        verbose_name = 'language'

    def __str__(self):
        return f'{self.english_name} ({self.code})'


class Term(models.Model):
    lang = models.CharField(max_length=10, db_index=True)
    source = models.TextField(help_text='The English text as written in the code (the key)')
    source_hash = models.CharField(max_length=40, editable=False)
    value = models.TextField(blank=True, default='', help_text='Translation; empty = missing (the English text is shown)')
    module = models.CharField(max_length=60, blank=True, default='', db_index=True)
    where = models.JSONField(default=list, blank=True, help_text='["file:line", …] from i18n/source.json')
    in_source = models.BooleanField(default=True, help_text='Still found in the code by the last extraction')
    public = models.BooleanField(default=False, help_text='Shown before login (login page) – served without a login')
    is_reviewed = models.BooleanField(default=False)
    edited = models.BooleanField(default=False, help_text='Changed on the Translations screen – the pack loader keeps it')
    updated_by = models.IntegerField(null=True, blank=True)
    updated_by_name = models.CharField(max_length=150, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['source']
        unique_together = [('lang', 'source_hash')]
        verbose_name = 'translation'
        verbose_name_plural = 'translations'

    def save(self, *args, **kwargs):
        self.source_hash = source_hash(self.source)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'[{self.lang}] {self.source[:60]}'


class UserLanguage(models.Model):
    user_id = models.IntegerField(unique=True)
    lang = models.CharField(max_length=10)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'user language'


class CompanyLanguageSettings(models.Model):
    schema_name = models.CharField(max_length=63, unique=True)
    default_lang = models.CharField(max_length=10, default='en')
    active_langs = models.JSONField(default=list, blank=True, help_text='Language codes the company uses; empty = every active language')
    updated_by_id = models.IntegerField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'company language settings'
        verbose_name_plural = 'company language settings'

    def __str__(self):
        return f'{self.schema_name}: {self.default_lang}'
