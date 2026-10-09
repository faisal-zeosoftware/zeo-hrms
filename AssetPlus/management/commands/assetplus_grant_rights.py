"""Give the new asset rights to the groups that already manage assets (v1.12.0).

    python manage.py assetplus_grant_rights                 # every company
    python manage.py assetplus_grant_rights --schema adepttest --approvals

Groups holding change_asset get view / add / change of the new asset tables (transfers, maintenance, damage,
loss, disposal, recovery). With --approvals they also get the approve_* rights and the exit-clearance waiver.
Nothing is removed; run it again at any time.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

MODELS = ('assettransfer', 'maintenancerecord', 'maintenanceschedule', 'assetdamage', 'assetloss', 'assetdisposal', 'recoveryinstalment',
          'assetprofile', 'assetreturn', 'assetevent', 'assetfile')
APPROVALS = ('approve_assettransfer', 'approve_assetdamage', 'approve_assetloss', 'approve_assetdisposal', 'waive_assetclearance')


class Command(BaseCommand):
    help = 'Grant the AssetPlus rights to the groups that hold change_asset.'

    def add_arguments(self, parser):
        parser.add_argument('--schema')
        parser.add_argument('--approvals', action='store_true')

    def handle(self, *args, **o):
        schemas = [o['schema']] if o.get('schema') else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        for sch in schemas:
            with schema_context(sch):
                codes = [f'{v}_{m}' for m in MODELS for v in ('view', 'add', 'change')] + ['view_assetbookvalue', 'change_assetplussettings']
                if o.get('approvals'):
                    codes += list(APPROVALS)
                perms = list(Permission.objects.filter(content_type__app_label='AssetPlus', codename__in=codes))
                n = 0
                for g in Group.objects.filter(permissions__codename='change_asset').distinct():
                    g.permissions.add(*perms)
                    n += 1
                self.stdout.write(f'{sch}: {len(perms)} rights given to {n} group(s)')
