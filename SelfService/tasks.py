"""v1.13.0 – daily complaint escalation (celery beat: SelfService.tasks.escalate_complaints)."""
from celery import shared_task


@shared_task
def escalate_complaints():
    from django_tenants.utils import get_tenant_model, schema_context
    total = 0
    for t in get_tenant_model().objects.exclude(schema_name='public'):
        with schema_context(t.schema_name):
            try:
                from .grievances import escalate_overdue
                total += escalate_overdue()
            except Exception:
                continue
    return total
