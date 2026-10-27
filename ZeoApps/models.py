"""v1.21.0 – the apps of a company.

HRMS and Projects keep their state here (they have no activation of their own). HSE, CRM, Property and Maintenance keep
it in their own settings (activated_at, industry) – ZeoApps only reads and writes it through their services.
A company without a row for HRMS / Projects uses them (as before 1.21.0), so existing companies are unchanged.
"""
from django.conf import settings
from django.db import models


class AppState(models.Model):
    app = models.CharField(max_length=20, unique=True, help_text='hrms | projects')
    active = models.BooleanField(default=True)
    industry = models.CharField(max_length=30, blank=True, default='')
    industry_source = models.CharField(max_length=10, blank=True, default='', help_text='company = from the company master; chosen = picked for this app')
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    changed_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ('view', 'change')
        permissions = [('manage_apps', 'Activate and deactivate the apps of the company')]


class CompanyGroup(models.Model):
    """v1.22.0 – permission groups made in this company (Settings › Permission groups).

    Groups live in the shared schema, so every company saw every company's groups. A company now lists the standard role
    groups, the groups made in it and the groups its own users are in.
    """
    group_id = models.IntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.62.0 isolation: the shared standard group this company's own copy replaces here (None = a group made in the
    # company) and the name the company sees (the stored group name must be unique on the whole server)
    template_id = models.IntegerField(null=True, blank=True)
    display_name = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        default_permissions = ()


class CompanyOption(models.Model):
    """v1.22.3 – small settings of the company: e.g. `neutralized` (a duplicate made for testing: no e-mails, no mailbox)."""
    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField(default=dict, blank=True)
    changed_at = models.DateTimeField(auto_now=True)

    class Meta:
        default_permissions = ()
