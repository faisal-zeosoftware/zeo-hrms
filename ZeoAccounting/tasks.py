"""Celery: every company – retry failed postings (hourly) and the month-end provisions (on the 1st)."""
from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context


def _each(fn):
    out = {}
    for t in get_tenant_model().objects.exclude(schema_name='public'):
        with schema_context(t.schema_name):
            try:
                out[t.schema_name] = fn()
            except Exception as ex:   # noqa: BLE001 – one company never stops the others
                out[t.schema_name] = f'error: {ex}'
    return out


@shared_task
def hourly():
    from .services import retry_failed
    return _each(retry_failed)


@shared_task
def month_start():
    from .services import month_end_provisions
    return _each(lambda: getattr(month_end_provisions(), 'number', None))
