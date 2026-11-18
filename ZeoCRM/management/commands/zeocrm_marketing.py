"""v1.67.29 – python manage.py zeocrm_marketing [--schema X]

Run every few minutes (cron: */5 * * * *, or a Celery beat entry): sends the campaign messages that are due (scheduled sends and
the rest of large sends) and reads the CRM e-mail inboxes (Setup › E-mail inboxes) of every company where CRM is active."""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'CRM marketing jobs: scheduled campaign sends and e-mail inboxes.'

    def add_arguments(self, p):
        p.add_argument('--schema')

    def handle(self, *a, **o):
        from ZeoCRM.app import MARKETING, app
        ts = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            ts = ts.filter(schema_name=o['schema'])
        for t in ts:
            with schema_context(t.schema_name):
                try:
                    if not app.activated():
                        continue
                    out = MARKETING['run_jobs']()
                    if out['sent'] or out['inbox']:
                        self.stdout.write(f'{t.schema_name}: {out}')
                except Exception as e:   # noqa: BLE001
                    self.stderr.write(f'{t.schema_name}: {e}')
