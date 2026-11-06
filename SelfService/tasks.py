"""v1.13.0 – daily complaint escalation (celery beat: SelfService.tasks.escalate_complaints)."""
from ZeoApps.scheduling import hr_off as _zeo_off   # v1.67.1 (companies); v1.67.3 (apps) also the companies without HR
from celery import shared_task


@shared_task
def escalate_complaints():
    from django_tenants.utils import get_tenant_model, schema_context
    total = 0
    for t in get_tenant_model().objects.exclude(schema_name__in=_zeo_off()):
        with schema_context(t.schema_name):
            try:
                from .grievances import escalate_overdue
                total += escalate_overdue()
            except Exception:
                continue
    return total
