"""Scheduled AttendancePlus jobs. Beat entry (zeo/settings.py CELERY_BEAT_SCHEDULE):
    'attendance-plus-nightly': {'task': 'AttendancePlus.tasks.attendance_nightly', 'schedule': crontab(hour=2, minute=15)},
    'attendance-plus-missing-punch': {'task': 'AttendancePlus.tasks.missing_punch_check', 'schedule': crontab(minute=20)},
"""
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def _each_tenant(fn, schema=None, *args):
    out = {}
    schemas = [schema] if schema else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
    for t in schemas:
        try:
            with schema_context(t):
                out[t] = fn(*args)
        except Exception as exc:  # one company failing must not stop the others
            log.exception('attendance job failed for %s', t)
            out[t] = f'error: {exc}'
    return out


@shared_task
def attendance_nightly(schema=None):
    """Daily: recompute yesterday for every active employee (absent / half day / OT / calendar) and notify missing punches."""
    from .engine import nightly
    return _each_tenant(nightly, schema)


@shared_task
def missing_punch_check(schema=None):
    """Hourly: open days whose shift ended N hours ago become 'missing punch' and the employee is notified."""
    from .engine import refresh_missing, notify_missing_punches

    def job():
        refresh_missing()
        return notify_missing_punches()
    return _each_tenant(job, schema)
