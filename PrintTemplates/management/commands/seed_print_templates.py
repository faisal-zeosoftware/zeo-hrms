"""v1.62.0 print – the sample print templates of every company (idempotent).

    python manage.py seed_print_templates                 # every company: add the missing samples
    python manage.py seed_print_templates --schema gdis   # one company
    python manage.py seed_print_templates --refresh       # also bring the samples up to the designs of this version
    python manage.py seed_print_templates --demo gdis     # demo company: "Modern" becomes the default payslip

A company's own copies, its defaults and its branding are never changed.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Add the sample print templates (payslip, letters, settlement …) to every company'

    def add_arguments(self, p):
        p.add_argument('--schema', default='')
        p.add_argument('--refresh', action='store_true')
        p.add_argument('--demo', default='', help='schema of a demo company: Modern becomes its default payslip')

    def handle(self, *a, **o):
        from PrintTemplates import services as S
        schemas = [o['schema']] if o['schema'] else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        if o['demo'] and o['demo'] not in schemas:
            schemas.append(o['demo'])
        for s in schemas:
            try:
                with schema_context(s):
                    made, upd = S.seed(refresh=o['refresh'])
                    extra = ''
                    if s == o['demo']:
                        extra = ' · default payslip: Modern' if S.set_demo_default('payslip', 'modern') else ' · default payslip already chosen'
                        from OrgStructure.models import CompanyProfile
                        prof = CompanyProfile.get()
                        if not prof.name_local:
                            prof.name_local = 'جولدن ديون للخدمات المتكاملة ذ.م.م'
                            prof.save(update_fields=['name_local'])
                self.stdout.write(f'{s}: {made} samples added, {upd} refreshed{extra}')
            except Exception as e:   # noqa: BLE001 – a company that was not migrated yet
                self.stdout.write(self.style.WARNING(f'{s}: skipped ({str(e)[:120]})'))
