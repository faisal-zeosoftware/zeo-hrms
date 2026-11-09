"""v1.67.8 (fixes) item 6 – clear the end date that branches' document numbering rows got automatically (made + 1 year).

    python manage.py clear_auto_end_dates                 # dry run, every company: the rows that would be cleared
    python manage.py clear_auto_end_dates --schema gdis   # one company
    python manage.py clear_auto_end_dates --apply         # clear them

Only rows whose end date is exactly the start date + 365 days, whose start date is the day the row was made and whose dates
were never changed by a user (change history) – see ZeoApps/enddates.py. Numbers, prefixes and counters are not touched.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'Clear the automatic one-year end date of document numbering rows (dry run unless --apply).'

    def add_arguments(self, p):
        p.add_argument('--schema', default='')
        p.add_argument('--apply', action='store_true')

    def handle(self, *a, **o):
        from UserManagement.models import company
        from ZeoApps import enddates as E
        qs = company.objects.exclude(schema_name='public')
        if o['schema']:
            qs = qs.filter(schema_name=o['schema'])
        total = 0
        for c in qs.order_by('schema_name'):
            with schema_context(c.schema_name):
                rows = E.clear(apply=o['apply'])
            total += len(rows)
            self.stdout.write(f'{c.schema_name}: {len(rows)} row(s) {"cleared" if o["apply"] else "to clear"}')
            for r in rows:
                self.stdout.write(f"  {r['branch'][:30]:<30} {r['type']:<28} {r['start_date']:%d/%m/%Y} – {r['end_date']:%d/%m/%Y}")
        self.stdout.write(self.style.SUCCESS(f'{total} end date(s) {"cleared" if o["apply"] else "would be cleared (dry run – --apply clears them)"}'))
