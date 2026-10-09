"""Scheduled AssetPlus jobs (v1.12.0). Beat entry (zeo/settings.py CELERY_BEAT_SCHEDULE):
    'asset-daily-reminders': {'task': 'AssetPlus.tasks.asset_daily_reminders', 'schedule': crontab(hour=7, minute=20)},
Maintenance due / overdue, warranty and insurance expiry, overdue returns – once per due date.
"""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


@shared_task
def asset_daily_reminders(schema=None):
    from .services import daily_reminders
    if schema:
        with schema_context(schema):
            return {schema: daily_reminders()}
    out = {}
    for t in get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True):
        try:
            with schema_context(t):
                out[t] = daily_reminders()
        except Exception as exc:  # one company failing must not stop the others
            log.exception('asset reminders failed for %s', t)
            out[t] = f'error: {exc}'
    return out
