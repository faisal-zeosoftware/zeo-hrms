"""v1.45.0 – read the reply mailbox of every company (replies to the messages sent from records). In CELERY_BEAT_SCHEDULE:
    'chatter-replies': {'task': 'Chatter.tasks.check_reply_mailboxes', 'schedule': crontab(minute='*/5')},"""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


@shared_task
def check_reply_mailboxes(schema=None):
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
    for sch in schemas:
        try:
            with schema_context(sch):
                from django.db import connection
                if 'Chatter_mailsettings' not in connection.introspection.table_names():
                    continue
                from .collab import check_mailbox
                out[sch] = check_mailbox()
        except Exception as exc:
            log.exception('reply mailbox failed for %s', sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def send_reminders(schema=None):
    """v1.46.0 – record reminders due today → inbox and bell (CELERY_BEAT: 'chatter-reminders', every hour)."""
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
    for sch in schemas:
        try:
            with schema_context(sch):
                from django.db import connection
                if 'Chatter_activity' not in connection.introspection.table_names():
                    continue
                from .collab import send_reminders as run
                out[sch] = run()
        except Exception as exc:
            log.exception('reminders failed for %s', sch)
            out[sch] = f'error: {exc}'
    return out
