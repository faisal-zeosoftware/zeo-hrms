"""v1.67.13 (service) – mark companies that used Projects before 1.67.13 as industry IT with the IT pack (their current setup)
and give existing AMC contracts the IT AMC type of their contract type. Dry run unless --apply.

    python manage.py projects_industry_upgrade                 # every company, list only
    python manage.py projects_industry_upgrade --apply
    python manage.py projects_industry_upgrade --schema acme --apply
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Projects industry packs: existing companies become IT (dry run unless --apply)'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true')
        p.add_argument('--schema', default='')

    def handle(self, *a, **o):
        from ZeoProjects.packs import upgrade
        qs = get_tenant_model().objects.exclude(schema_name='public')
        if o['schema']:
            qs = qs.filter(schema_name=o['schema'])
        for t in qs:
            with schema_context(t.schema_name):
                try:
                    from ZeoProjects.models import ProjectsSettings
                    used = ProjectsSettings.objects.exists()
                except Exception:   # noqa: BLE001
                    used = False
                if not used:
                    self.stdout.write(f'{t.schema_name}: Projects not set up – skipped')
                    continue
                out = upgrade(apply=o['apply'])
                self.stdout.write(f'{t.schema_name}: ' + ('; '.join(out) if out else 'nothing to do'))
        if not o['apply']:
            self.stdout.write('Dry run – add --apply to make the changes.')
