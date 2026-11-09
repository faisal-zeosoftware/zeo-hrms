"""v1.67.6 (setup) item 4 – add the missing document numbering series of the active apps of existing companies.

    python manage.py add_numbering_series                 # dry run, every company: what would be added
    python manage.py add_numbering_series --schema gdis   # one company
    python manage.py add_numbering_series --apply         # add them

Never renumbers: existing series are not touched; a new series of Projects, CRM, Property, Maintenance, Asset Management,
People or HSE starts after the last number the app issued, with the app's own prefix and length.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'Add the missing document numbering series of the active apps (dry run unless --apply).'

    def add_arguments(self, p):
        p.add_argument('--schema', default='')
        p.add_argument('--apply', action='store_true')

    def handle(self, *a, **o):
        from UserManagement.models import company
        from ZeoApps import numbering as N
        qs = company.objects.exclude(schema_name__in=('public', 'zeo_template'))
        if o['schema']:
            qs = qs.filter(schema_name=o['schema'])
        total = 0
        for c in qs.order_by('schema_name'):
            with schema_context(c.schema_name):
                rows = N.ensure(apply=o['apply'])
            total += len(rows)
            self.stdout.write(f'{c.schema_name}: {len(rows)} series {"added" if o["apply"] else "to add"}')
            for r in rows:
                self.stdout.write(f"  {r['app']:<11} {r['label']:<28} {r['branch']:<30} {r['prefix']}-{str(r['current_number'] + 1).zfill(r['total_length'] - len(r['prefix']) - 1)}"
                                  f"  (next number){'  company-wide' if r['company_wide'] else ''}")
        self.stdout.write(self.style.SUCCESS(f'{total} series {"added" if o["apply"] else "would be added (dry run – --apply adds them)"}'))
