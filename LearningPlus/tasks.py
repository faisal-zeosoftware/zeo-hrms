"""Scheduled Learning jobs. Beat entries (zeo/settings.py CELERY_BEAT_SCHEDULE):
    'learning-certificate-expiry-alerts': {'task': 'LearningPlus.tasks.certificate_expiry_alerts', 'schedule': crontab(hour=7, minute=5)},
    'learning-nomination-escalations':    {'task': 'LearningPlus.tasks.nomination_escalations', 'schedule': crontab(hour=8, minute=5)},
"""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def _tenants():
    return list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))


def _each_tenant(fn, *args, **kwargs):
    out = {}
    for t in _tenants():
        try:
            with schema_context(t):
                out[t] = fn(*args, **kwargs)
        except Exception as exc:  # one company failing must not stop the others
            log.exception('learning job failed for %s', t)
            out[t] = f'error: {exc}'
    return out


@shared_task
def certificate_expiry_alerts(schema=None):
    """Daily: 60 / 30 / 7 day and expired alerts (once per threshold) + the existing renewal-need scan."""
    from .services import certificate_expiry_alerts as job
    if schema:
        with schema_context(schema):
            return {schema: job()}
    return _each_tenant(job)


@shared_task
def nomination_escalations(days=3, schema=None):
    """Daily: nominations waiting more than `days` days for the manager or L&D."""
    from .services import escalate_nominations as job
    if schema:
        with schema_context(schema):
            return {schema: job(days)}
    return _each_tenant(job, days)
