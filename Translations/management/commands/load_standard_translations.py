"""python manage.py load_standard_translations [--schema adepttest] [--lang ar] [--force]

v1.15.0 – Arabic names for the standard UAE lists (departments, designations, categories, leave types, document types,
salary components, religions, nationalities, countries incl. the GCC, employment types, expense categories, asset types) in
every company (or one). Safe to run again: existing translations are kept; --force refreshes only the ones it loaded itself."""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Load the Arabic names of the standard UAE lists into the translated master data (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='Only this company schema')
        parser.add_argument('--lang', default='ar')
        parser.add_argument('--force', action='store_true', help='Refresh translations loaded earlier from the standard lists')

    def handle(self, *args, **o):
        from Translations.services import table_ready, _TABLE_OK
        from Translations.standard import load
        schemas = [o['schema']] if o.get('schema') else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        for sc in schemas:
            with schema_context(sc):
                _TABLE_OK.pop(sc, None)
                if not table_ready():
                    self.stdout.write(self.style.WARNING(f'{sc}: Translations table missing – run migrate_schemas first'))
                    continue
                res = load(o['lang'], force=o['force'])
                tot = {k: sum(v[k] for v in res.values()) for k in ('matched', 'created', 'updated', 'kept')}
                self.stdout.write(f"{sc}: {tot['created']} created, {tot['updated']} updated, {tot['kept']} kept ({tot['matched']} records matched)")
