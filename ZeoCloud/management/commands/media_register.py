"""v1.62.0 – link every existing uploaded file to its company and record (run once after the update; safe to run again).

    python manage.py media_register              # every company
    python manage.py media_register --schema s1  # one company
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Link the uploaded files to their company and record (files open only for users who may open the record).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', default='')

    def handle(self, *args, **o):
        from django_tenants.utils import get_tenant_model
        from ZeoCloud.mediareg import register_schema
        schemas = [o['schema']] if o['schema'] else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        total = 0
        for s in schemas:
            try:
                n = register_schema(s)
            except Exception as e:   # a company whose tables are not there (e.g. being made)
                self.stdout.write(f'{s}: skipped ({e})')
                continue
            total += n
            self.stdout.write(f'{s}: {n} files')
        self.stdout.write(self.style.SUCCESS(f'{total} files linked in {len(schemas)} companies.'))
