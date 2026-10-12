"""v1.15.1 – Repairs Asset.status so that it follows the allocations (the one source of truth since v1.15.1).

    python manage.py sync_asset_status                    # dry run: lists the assets whose status is wrong
    python manage.py sync_asset_status --apply            # fixes them
    python manage.py sync_asset_status --schema adepttest # one company only

Rule: an open employee allocation or an open location / department allocation = Assigned, none = Available.
Under maintenance and Disposed are kept (they are set by their own processes).
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Lists (dry run) or fixes (--apply) asset statuses that do not follow the allocations.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='make the changes (default: dry run)')
        parser.add_argument('--schema', help='only this company schema')

    def handle(self, *args, **o):
        from AssetPlus import services as S
        apply = bool(o.get('apply'))
        qs = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            qs = qs.filter(schema_name=o['schema'])
        w = self.stdout.write
        w('APPLY' if apply else 'DRY RUN (nothing is changed – add --apply to fix)')
        total = 0
        for t in qs.order_by('schema_name'):
            with schema_context(t.schema_name):
                rows = S.status_mismatches()
                w(f'\n== {t.schema_name} ({t.name}): {len(rows)} asset(s) with a wrong status')
                for a, cur, want in rows:
                    h = S.holder(a.pk)
                    why = f"held by {h['name']}" if h else 'not allocated'
                    w(f"   #{a.pk} {a.name} ({a.serial_number}): {cur} → {want} ({why})")
                    if apply:
                        S.sync_status(a.pk)
                total += len(rows)
        w(f'\n{total} asset(s) ' + ('fixed.' if apply else 'would be fixed.'))
