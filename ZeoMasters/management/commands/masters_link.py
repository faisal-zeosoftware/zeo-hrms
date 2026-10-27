"""v1.41.0 – give every app record (v1.43.0: and every spare part, charge type, service product, chemical an item) (CRM contacts / accounts, Property tenants / owners / brokers / vendors, …) a business partner.

    python manage.py masters_link                    # every company schema
    python manage.py masters_link --schema q7con     # one company

Safe to run again: only records without a partner are handled. Records linked to each other (CRM contact ↔ tenant) and records
with the same ID / tax number share one partner; records that only share a phone or e-mail get their own partner and are listed
under Possible duplicates for a person to decide."""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Link the existing app records to business partners (v1.41.0).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', default='')

    def handle(self, *args, **o):
        from ZeoMasters import partners as PT
        from ZeoMasters import items as IT
        from ZeoMasters.models import DuplicatePair, Partner
        schemas = [o['schema']] if o['schema'] else [t.schema_name for t in get_tenant_model().objects.exclude(schema_name='public')]
        for s in schemas:
            with schema_context(s):
                try:
                    n = PT.link_all()
                    m = IT.link_all()   # v1.43.0 item master
                    self.stdout.write(f'{s}: {n} records linked to partners, {m} to items – {Partner.objects.count()} partners, '
                                      f'{DuplicatePair.objects.filter(status="open").count()} possible duplicates to review')
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'{s}: {e}'))
