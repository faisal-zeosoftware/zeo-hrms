"""ZEO insights (v1.53.0), per company:

* CustomReport     – a report made in the report builder: a record type, columns, filters, grouping with totals, sort.
                     It runs in the report centre as the report `c-<id>` (scheduling, e-mail, export work as for the others).
* CustomDashboard  – a dashboard made of report cards (a number, a bar chart by a column, a line by month, a short table),
                     private, for everybody or for chosen permission groups.
"""
from django.conf import settings
from django.db import models

USER = settings.AUTH_USER_MODEL
SHARE = [('me', 'Only me'), ('all', 'Everybody who may run it'), ('groups', 'Chosen permission groups')]


class CustomReport(models.Model):
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, default='')
    section = models.CharField(max_length=60, blank=True, default='My reports', help_text='heading in the report centre')
    model = models.CharField(max_length=80, help_text='record type, app_label.model')
    columns = models.JSONField(default=list, blank=True, help_text='[{field, label}]')
    filters = models.JSONField(default=list, blank=True, help_text='[{field, op, value, value2}]')
    group_by = models.CharField(max_length=120, blank=True, default='')
    aggregates = models.JSONField(default=list, blank=True, help_text='[{field, fn: count | sum | avg | min | max}] with a grouping')
    sort = models.CharField(max_length=120, blank=True, default='', help_text='field, -field for descending')
    limit = models.PositiveIntegerField(default=0, help_text='0 = all rows (at most 20 000)')
    date_field = models.CharField(max_length=120, blank=True, default='', help_text='the period of the report filters this date')
    shared = models.CharField(max_length=8, choices=SHARE, default='me')
    groups = models.JSONField(default=list, blank=True, help_text='permission group ids (shared = groups)')
    owner = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['section', 'name']

    def __str__(self):
        return self.name


class CustomDashboard(models.Model):
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, default='')
    cards = models.JSONField(default=list, blank=True, help_text='[{id, title, report, period, view, value, group, size, limit, columns}]')
    shared = models.CharField(max_length=8, choices=SHARE, default='me')
    groups = models.JSONField(default=list, blank=True)
    owner = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name
