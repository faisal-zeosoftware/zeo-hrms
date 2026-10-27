"""v1.52.0 – background jobs of ZEO administration (CELERY_BEAT_SCHEDULE):
    'zeoadmin-schedules'  every 15 minutes – scheduled reports that are due
    'zeoadmin-webhooks'   every 5 minutes  – failed webhook deliveries to try again
    'zeoadmin-heartbeat'  every 5 minutes  – the scheduler's heartbeat (Health checks)
    'zeoadmin-daily'      every day 00:20  – out-of-office periods starting today hand over the waiting approvals; old sign-in sessions pruned; data retention (v1.53.0)
"""
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
            log.exception('%s failed for %s', fn.__name__, sch)
            out[sch] = f'error: {exc}'
    return out


@shared_task
def run_schedules(schema=None):
    from .schedules import due
    return _each(due, 'ZeoAdmin_reportschedule', schema)


@shared_task
def retry_webhooks(schema=None):
    from .webhooks import retry_due
    return _each(retry_due, 'ZeoAdmin_webhookdelivery', schema)


@shared_task
def heartbeat():
    from .health import heartbeat as beat
    beat()
    return 'ok'


@shared_task
def daily(schema=None):
    from .delegation import daily as ooo
    out = _each(ooo, 'ZeoAdmin_outofoffice', schema)
    from .privacy import daily as retention   # v1.53.0 anonymise employees who left N years ago (when automatic)
    out['retention'] = _each(retention, 'ZeoAdmin_retentionpolicy', schema)
    from .setup_guide import eid_reminder   # v1.56.0 Eid dates to confirm (moon sighting)
    out['eid'] = _each(eid_reminder, 'ZeoAdmin_setupguide', schema)
    if schema is None:   # v1.54.0 Active Directory: new users, groups and disabled accounts every night
        try:
            from ZeoCloud.directory import nightly
            out['directory'] = nightly()
        except Exception:
            log.exception('directory sync')
    if schema is None:   # sign-in sessions older than any token they could have issued (refresh tokens live 14 days)
        try:
            from datetime import timedelta
            from django.utils import timezone
            from ZeoCloud.models import LoginSession
            out['sessions_pruned'] = LoginSession.objects.filter(created_at__lt=timezone.now() - timedelta(days=30)).delete()[0]
        except Exception:
            log.exception('session prune')
    return out


@shared_task
def history_nightly(schema=None):
    """v1.62.0 log and history cleaning of the companies that switched the nightly job on; the shared staging / temporary files."""
    from .history import clean_server_files, nightly
    out = _each(nightly, 'ZeoAdmin_historypolicy', schema)
    if schema is None:
        try:
            out['server_files'] = clean_server_files()
        except Exception:
            log.exception('server files')
    return out
