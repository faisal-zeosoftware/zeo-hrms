"""v1.67.6 (setup) item 2 – fill the EMPTY fields of each company's default branch from its company master.

    python manage.py inherit_branch_fields                 # dry run, every company
    python manage.py inherit_branch_fields --schema gdis   # one company
    python manage.py inherit_branch_fields --apply         # write

Only empty fields (and the placeholders of the first branch: "Sample City", "123456", "BN-…", branch@example.com) are filled:
logo, address, city, P.O. box, emirate / state, country, phone, e-mail, TRN, trade licence, establishment card, bank accounts,
time zone, head office / default branch and the financial year. Nothing typed on the branch is changed.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'Fill the empty fields of the default branch from the company master (dry run unless --apply).'

    def add_arguments(self, p):
        p.add_argument('--schema', default='')
        p.add_argument('--apply', action='store_true')

    def handle(self, *a, **o):
        from UserManagement.models import company
        from ZeoApps import branchinherit as BI
        qs = company.objects.exclude(schema_name__in=('public', 'zeo_template'))
        if o['schema']:
            qs = qs.filter(schema_name=o['schema'])
        total = 0
        for c in qs.order_by('schema_name'):
            with schema_context(c.schema_name):
                rows, b = BI.plan()
                if o['apply'] and b is not None:
                    BI.apply(rows, b)
            total += len(rows)
            self.stdout.write(f'{c.schema_name}: {len(rows)} field(s) {"filled" if o["apply"] else "to fill"} on "{getattr(b, "branch_name", "-")}"')
            for t, f, cur, v in rows:
                self.stdout.write(f'  {t:<11} {f:<22} {str(cur or "–")[:30]:<30} → {str(v)[:60]}')
        self.stdout.write(self.style.SUCCESS(f'{total} field(s) {"filled" if o["apply"] else "would be filled (dry run – --apply writes them)"}'))
