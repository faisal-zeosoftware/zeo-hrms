"""ZEO Developer – scheduled jobs. In zeo/settings.py CELERY_BEAT_SCHEDULE:

    'zeodeveloper-run-due': {'task': 'ZeoDeveloper.tasks.run_due', 'schedule': crontab(minute=10)},

Without Celery: `python manage.py run_developer_schedules` every hour from cron. Each company runs in its own schema."""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def each(fn, schema=None):
    from ZeoCloud.lifecycle import off_schemas
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name__in=off_schemas()).exclude(schema_name='public')
                                           .values_list('schema_name', flat=True))
    for sch in schemas:
        try:
            with schema_context(sch):
                from django.db import connection
                if 'ZeoDeveloper_developersettings' not in connection.introspection.table_names():
                    continue
                out[sch] = fn()
        except Exception as exc:   # noqa: BLE001
            log.exception('developer job failed for %s', sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def run_due(schema=None):
    from .schedule import run_due as run
    return each(run, schema)
