"""Ask ZEO 1.72.0 (A7) – monthly AI summary e-mail, on the 1st of each month for every company that switched it on."""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

from ZeoCloud.lifecycle import off_schemas as _zeo_off

log = logging.getLogger(__name__)


@shared_task
def monthly_summary():
    from .summary import send_for_company
    out = {}
    for t in get_tenant_model().objects.exclude(schema_name__in=list(_zeo_off()) + ['public']):
        with schema_context(t.schema_name):
            try:
                r = send_for_company()
                out[t.schema_name] = {k: r.get(k) for k in ('sent', 'why', 'mode')}
            except Exception:
                log.exception('Ask ZEO monthly summary %s', t.schema_name)
                out[t.schema_name] = {'sent': False, 'why': 'error'}
    return out
