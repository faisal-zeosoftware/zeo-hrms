"""v1.49.0 – month-end provisions on the 1st and paused loans that resume (CELERY_BEAT: 'payrollplus-provisions', 'payrollplus-loans')."""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def _each(fn, table, schema=None):
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
    for sch in schemas:
        try:
            with schema_context(sch):
                from django.db import connection
                if table not in connection.introspection.table_names():
                    continue
                out[sch] = fn()
        except Exception as exc:
            log.exception('payroll plus job failed for %s', sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def month_end_provisions(schema=None):
    from .provisions import make_run
    return _each(lambda: str(make_run().month), 'PayrollPlus_provisionrun', schema)


@shared_task
def resume_loans(schema=None):
    from .loans import resume_due
    return _each(resume_due, 'PayrollPlus_loanevent', schema)
