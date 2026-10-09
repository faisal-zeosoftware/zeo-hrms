"""Celery task (optional beat entry): remind employees about company policies they have not acknowledged in time."""
from datetime import timedelta

from celery import shared_task


@shared_task
def remind_unacknowledged_policies():
    from django.apps import apps
    from django.utils import timezone
    from django_tenants.utils import get_tenant_model, schema_context
    out = {}
    for t in get_tenant_model().objects.exclude(schema_name='public'):
        with schema_context(t.schema_name):
            try:
                apps.get_model('OrgStructure', 'PolicyVersion')
            except LookupError:
                continue
            from .models import PolicyVersion
            from .views import _policies, remind_policy
            from django.db import connection
            if 'OrgStructure_policyversion' not in connection.introspection.table_names():
                continue
            now = timezone.now()
            n = 0
            for v in PolicyVersion.objects.filter(requires_ack=True):
                due = (v.version_date or now) + timedelta(days=v.due_days)
                if due > now or (v.last_reminded_at and v.last_reminded_at > now - timedelta(days=7)):
                    continue
                p = _policies().filter(pk=v.policy_id).first()
                if p is not None:
                    n += remind_policy(p)
            out[t.schema_name] = n
    return out
