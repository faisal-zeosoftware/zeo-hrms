"""Scheduled jobs of HSE. In zeo/settings.py CELERY_BEAT_SCHEDULE:

    'zeohse-sweep': {'task': 'ZeoHSE.tasks.sweep', 'schedule': crontab(minute='*/15')},
    'zeohse-daily': {'task': 'ZeoHSE.tasks.daily', 'schedule': crontab(hour=7, minute=40)},

Each job runs once per company; one company failing does not stop the others.
"""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def _each(fn, schema=None):
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
    for sch in schemas:
        try:
            with schema_context(sch):
                from django.db import connection
                if 'zeohse_permit' not in {t.lower() for t in connection.introspection.table_names()}:
                    continue   # not migrated in this company
                out[sch] = fn()
        except Exception as exc:
            log.exception('HSE job %s failed for %s', getattr(fn, '__name__', fn), sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def sweep(schema=None):
    """Expire permits past their validity; suspend permits whose gas test is out of date."""
    from .services import sweep_permits
    return _each(sweep_permits, schema)


@shared_task
def daily(schema=None):
    """Overdue actions (escalated to the HSE manager), health assessments due, environmental monitoring due, chemical / SDS
    reviews, compliance obligations (escalated to the owner's manager), document reviews, expiring competencies, monthly KPIs."""
    from .modules import daily_all
    from .services import action_reminders

    def run():
        out = {'action_reminders': action_reminders()}
        out.update(daily_all())
        return out
    return _each(run, schema)
