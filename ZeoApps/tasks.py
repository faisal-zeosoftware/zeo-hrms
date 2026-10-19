"""v1.21.0 – daily jobs of the CRM, Property and Maintenance apps (reservation expiry, renewal reminders, cheques to deposit,
late first contact, expired offers, preventive jobs, late jobs) for every company where the app is active."""
import importlib
import logging

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)
KIT = ('ZeoCRM', 'ZeoProperty', 'ZeoMaintenance', 'ZeoAssets', 'ZeoPeople')


@shared_task
def daily():
    from zeo.appkit.engine import REGISTRY
    out = {}
    for label in KIT:
        importlib.import_module(f'{label}.app')
    for t in get_tenant_model().objects.exclude(schema_name='public'):
        with schema_context(t.schema_name):
            for label in KIT:
                app = REGISTRY[label]
                try:
                    if app.daily and app.activated():
                        out[f'{t.schema_name}.{label}'] = app.daily()
                except Exception:
                    log.exception('daily %s %s', t.schema_name, label)
    return out
