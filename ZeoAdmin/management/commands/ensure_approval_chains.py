"""v1.56.1 – python manage.py ensure_approval_chains [--schema S] [--dry]: the standard approval chains (the reporting manager
approves) for every branch and request type that has none – leave, late in / early out, advance salary, resignation, air ticket,
loan types. Existing chains are not changed."""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Add the reporting-manager approval chains that are missing (every company or --schema).'

    def add_arguments(self, p):
        p.add_argument('--schema', default='')
        p.add_argument('--dry', action='store_true', help='Only list what is missing')

    def handle(self, *a, **o):
        from ZeoAdmin.approval_defaults import ensure, missing
        schemas = [o['schema']] if o['schema'] else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        for s in schemas:
            try:
                with schema_context(s):
                    if o['dry']:
                        m = missing()
                        self.stdout.write(f'{s}: ' + ('; '.join(f'{k} ({", ".join(v)})' for k, v in m) or 'nothing missing'))
                    else:
                        self.stdout.write(f'{s}: ' + ('; '.join(ensure()) or 'nothing missing'))
            except Exception as e:   # noqa: BLE001
                self.stdout.write(self.style.WARNING(f'{s}: {e}'))
