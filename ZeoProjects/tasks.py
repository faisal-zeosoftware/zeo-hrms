"""Scheduled jobs of Projects. Add to CELERY_BEAT_SCHEDULE in zeo/settings.py:

    'zeoprojects-sla-check': {'task': 'ZeoProjects.tasks.sla_check', 'schedule': crontab(minute='*/15')},
    'zeoprojects-mailbox': {'task': 'ZeoProjects.tasks.check_mailboxes', 'schedule': crontab(minute='*/5')},
    'zeoprojects-daily': {'task': 'ZeoProjects.tasks.daily', 'schedule': crontab(hour=7, minute=35)},

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
                if 'ZeoProjects_ticket' not in connection.introspection.table_names() and 'zeoprojects_ticket' not in connection.introspection.table_names():
                    continue   # not migrated in this company
                out[sch] = fn()
        except Exception as exc:
            log.exception('Projects job %s failed for %s', fn.__name__, sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def sla_check(schema=None):
    from .services import sla_check as run
    return _each(run, schema)


@shared_task
def check_mailboxes(schema=None):
    from .mailin import check_mailbox
    return _each(check_mailbox, schema)


@shared_task
def daily(schema=None):
    """Contract status, renewal reminders (90 / 60 / 30 / 0 days and after expiry); on Mondays the weekly summary e-mail."""
    from .reports import send_weekly
    from .services import local_today, renewal_reminders

    def run():
        from .plans import overdue_tasks
        out = {'renewal_reminders': renewal_reminders(), 'overdue_tasks': overdue_tasks()}
        if local_today().weekday() == 0:
            out['weekly_summary_to'] = send_weekly()
        return out
    return _each(run, schema)
