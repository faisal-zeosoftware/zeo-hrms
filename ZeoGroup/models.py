"""v1.27.0 – group features across the companies of one customer (Premium and Enterprise).

A *group* is the companies of one ZEO Cloud account (Premium) or, on a server of its own (Enterprise), every company of
the server. Each company keeps its data in its own schema; the group screens read each company the user may see and
show them side by side. The tables here live in the shared (public) schema.
"""
from django.conf import settings
from django.db import models


class GroupRole(models.Model):
    """A person with a role on the whole group (besides the account owner, who has every group right)."""
    HR_HEAD, VIEWER = 'hr_head', 'viewer'
    ROLES = [(HR_HEAD, 'Group HR head – every company, HR rights'), (VIEWER, 'Group viewer – group dashboard and reports only')]
    account = models.ForeignKey('ZeoCloud.Account', on_delete=models.CASCADE, null=True, blank=True, related_name='group_roles',
                                help_text='Empty on a server of its own (Enterprise): the group is every company of the server')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='zeo_group_roles')
    role = models.CharField(max_length=12, choices=ROLES, default=VIEWER)
    granted = models.JSONField(default=list, blank=True, help_text='Company ids this role gave access to (taken back when it ends)')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('account', 'user')]

    def __str__(self):
        return f'{self.user_id} {self.role} @ {self.account_id or "server"}'


class CompanyTransfer(models.Model):
    """An employee moved to a sister company: made in the target, closed in the source, the two records linked."""
    source_schema = models.CharField(max_length=63)
    source_company = models.CharField(max_length=200)
    source_employee_id = models.IntegerField()
    target_schema = models.CharField(max_length=63)
    target_company = models.CharField(max_length=200)
    target_employee_id = models.IntegerField(null=True, blank=True)
    emp_code = models.CharField(max_length=50)
    target_code = models.CharField(max_length=50, blank=True)
    name = models.CharField(max_length=200)
    effective_date = models.DateField()
    keep_service_date = models.BooleanField(default=True)
    options = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    state = models.CharField(max_length=12, default='done')     # done / failed
    error = models.TextField(blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class SettingsCopy(models.Model):
    """Log of "copy settings to companies"."""
    source_schema = models.CharField(max_length=63)
    targets = models.JSONField(default=list)
    kinds = models.JSONField(default=list)
    overwrite = models.BooleanField(default=False)
    result = models.JSONField(default=dict, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']
