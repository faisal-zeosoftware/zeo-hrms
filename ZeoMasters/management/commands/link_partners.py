"""v1.67.4 (masters) – link the existing customers, suppliers, vendors, owners, tenants, contractors and typed supplier / customer /
merchant names of every app to business partners. DRY RUN unless --apply (see ZeoMasters/linking.py)."""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Link existing app records to business partners: TRN → e-mail / phone → exact name → new partner. Dry run unless --apply.'
    what = 'partners'
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--schema', default='', help='one company (schema); default every company')
        parser.add_argument('--apply', action='store_true', help='keep the links (without it nothing is saved)')
        if self.what != 'locations':
            parser.add_argument('--no-create', action='store_true', help='do not make missing partners / items – leave those unlinked')

    def handle(self, *args, **o):
        from ZeoMasters import linking as LK
        schemas = [o['schema']] if o['schema'] else [t.schema_name for t in get_tenant_model().objects.exclude(schema_name='public')]
        mode = 'APPLIED' if o['apply'] else 'DRY RUN – nothing saved (add --apply to keep)'
        for s in schemas:
            with schema_context(s):
                try:
                    self.stdout.write(f'{s}: link {self.what} – {mode}')
                    if self.what == 'locations':
                        rows, differ = LK.run(LK.link_locations, o['apply'])
                        self.stdout.write(LK.table(rows))
                        for d in differ:
                            self.stdout.write(f'  not joined (different names – decide by hand): {d}')
                    else:
                        fn = LK.link_partners if self.what == 'partners' else LK.link_items
                        self.stdout.write(LK.table(LK.run(fn, o['apply'], not o['no_create'])))
                except Exception as e:   # noqa: BLE001 – one company's problem does not stop the others
                    self.stdout.write(self.style.WARNING(f'{s}: {e}'))
