"""Scheduled jobs of the workflow engine. Add to CELERY_BEAT_SCHEDULE in zeo/settings.py:

    'zeoflow-run-due': {'task': 'ZeoFlow.tasks.run_due', 'schedule': crontab(minute='*/15')},

Without Celery: `python manage.py run_workflows` every 15 minutes from cron. Each job runs once per company."""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def each(fn, schema=None):
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
    for sch in schemas:
        try:
            with schema_context(sch):
                from django.db import connection
                names = connection.introspection.table_names()
                if 'ZeoFlow_workflow' not in names and 'zeoflow_workflow' not in names:
                    continue
                out[sch] = fn()
        except Exception as exc:
            log.exception('workflow job failed for %s', sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def run_due(schema=None):
    from .engine import run_due as run
    return each(run, schema)
