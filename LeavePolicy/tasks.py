"""Scheduled leave jobs (v1.11.0) – Celery beat entries are in zeo/settings.py; `manage.py run_leave_jobs` does the same by hand / cron."""
import logging
from datetime import date, timedelta

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def _tenants():
    return list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))


def daily_jobs(today=None, dry=False):
    """On the 1st: close leave years that end yesterday, then credit last month. Every day: compensatory off expiry."""
    from . import engine, compoff
    from .models import LeavePolicy
    today = today or date.today()
    out = {'year_end': 0, 'accrual': 0, 'comp_off_lapsed': 0}
    if today.day == 1:
        ids = list(LeavePolicy.objects.filter(active=True, leave_year_start=today.month).values_list('pk', flat=True))
        if ids:
            out['year_end'] = len(engine.run_year_end(today - timedelta(days=1), preview=dry, policy_ids=ids)['lines'])
        out['accrual'] = engine.run_accrual(today - timedelta(days=1), preview=dry)['count']
    out['comp_off_lapsed'] = len(compoff.expire(today, preview=dry))
    if not dry:   # v1.11.0: scheduled employee transfers whose date has come
        try:
            from HRActions.transfer import due
            out['transfers'] = due(today)
        except Exception:
            log.exception('scheduled transfers failed')
    return out


@shared_task
def leave_daily_jobs():
    for t in _tenants():
        try:
            with schema_context(t):
                log.info('leave jobs %s: %s', t, daily_jobs())
        except Exception:
            log.exception('leave jobs failed for %s', t)


@shared_task
def leave_escalations():
    from .approvers import escalate_due
    for t in _tenants():
        try:
            with schema_context(t):
                done = escalate_due()
                if done:
                    log.info('leave escalations %s: %s', t, done)
        except Exception:
            log.exception('leave escalation failed for %s', t)
