"""Asset Management jobs every 15 minutes (v1.34.0): helpdesk escalation, e-mail to ticket, status of work orders handed
over to the Maintenance app. Daily jobs (plans, overdue returns, HR sync) run with the other kit apps (ZeoApps.tasks.daily)."""
import importlib
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


@shared_task
def frequent(schema=None):
    importlib.import_module('ZeoAssets.app')
    from zeo.appkit.engine import REGISTRY
    from . import maint as MT
    from .mailin import check_mailbox
    app = REGISTRY['ZeoAssets']
    out = {}
    ts = get_tenant_model().objects.exclude(schema_name='public')
    if schema:
        ts = ts.filter(schema_name=schema)
    for t in ts:
        with schema_context(t.schema_name):
            try:
                if not app.activated():
                    continue
                r = {'escalated': MT.escalate(), 'handed_over': MT.sync_handed_over()}
                try:
                    r['mails'] = check_mailbox()
                except Exception as e:
                    r['mails'] = f'error: {e}'
                out[t.schema_name] = r
            except Exception:
                log.exception('Asset Management jobs %s', t.schema_name)
    return out
