"""v1.73.6 – make the default dashboards of a company (or of every company): manage.py zeoinsights_defaults [--schema <schema>]"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Make the default dashboards (Company overview and one per active app) where a company does not have them yet.'

    def add_arguments(self, parser):
        parser.add_argument('--schema', default='', help='One company (schema name); all companies when left out.')

    def handle(self, *args, **o):
        from tenant_users.tenants.models import UserTenantPermissions
        schemas = [o['schema']] if o['schema'] else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        for sch in schemas:
            with schema_context(sch):
                from ZeoInsights import defaults
                utp = UserTenantPermissions.objects.filter(is_superuser=True).select_related('profile').order_by('pk').first()
                if utp is None:
                    self.stdout.write(f'{sch}: no administrator – skipped')
                    continue
                made = defaults.ensure(utp.profile)
                self.stdout.write(f'{sch}: ' + (', '.join(made) if made else 'nothing new'))
