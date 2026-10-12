"""v1.21.0 – apps of a company from the command line.

    python manage.py zeoapps_setup --schema adepttest                         # list the apps and their state
    python manage.py zeoapps_setup --schema adepttest --activate property [--industry property_management]
    python manage.py zeoapps_setup --schema adepttest --deactivate projects
    python manage.py zeoapps_setup --roles                                    # role groups of every app, every company
    python manage.py zeoapps_setup --fill-branches [--default-branch HO]      # v1.22.0 branch of existing CRM / Property /
                                                                              # Maintenance records (from their parent record;
                                                                              # the rest: --default-branch, or left shared)
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'List, activate or deactivate the apps of a company (HRMS, Projects, CRM, Property, Maintenance, HSE).'

    def add_arguments(self, p):
        p.add_argument('--schema')
        p.add_argument('--activate')
        p.add_argument('--deactivate')
        p.add_argument('--industry')
        p.add_argument('--roles', action='store_true')
        p.add_argument('--fill-branches', action='store_true')
        p.add_argument('--clean-todos', action='store_true',
                       help='v1.22.0: delete to-dos of this company assigned to users of other companies (made before 1.22.0)')
        p.add_argument('--default-branch', help='branch code for records whose branch cannot be found (default: leave them shared)')

    def handle(self, *a, **o):
        from ZeoApps import services as S
        ts = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            ts = ts.filter(schema_name=o['schema'])
        for t in ts:
            with schema_context(t.schema_name):
                if o.get('roles'):
                    for k in S.KEYS:
                        try:
                            S.make_roles(k)
                        except Exception as e:
                            self.stderr.write(f'{t.schema_name}: roles {k} failed: {e}')
                    self.stdout.write(f'{t.schema_name}: role groups ready')
                if o.get('fill_branches'):
                    self.stdout.write(f'{t.schema_name}: branches filled {fill_branches(o.get("default_branch"))}')
                if o.get('clean_todos'):
                    self.stdout.write(f'{t.schema_name}: to-dos of users of other companies deleted: {clean_todos(t.schema_name)}')
                if o.get('activate'):
                    for k in o['activate'].split(','):
                        self.stdout.write(f'{t.schema_name}: {k} activated {S.activate(k.strip(), o.get("industry"))}')
                if o.get('deactivate'):
                    for k in o['deactivate'].split(','):
                        S.deactivate(k.strip())
                        self.stdout.write(f'{t.schema_name}: {k} deactivated')
                for s in S.all_states():
                    self.stdout.write(f'{t.schema_name}: {s["title"]:12s} {"active" if s["active"] else "-":7s} {s["industry_label"]}')


def clean_todos(schema):
    """v1.22.0: before 1.22.0 "users with role X" also found users of other companies; their to-dos here are removed."""
    from django.contrib.auth import get_user_model
    try:
        from Chatter.models import Activity
    except Exception:
        return 0
    members = get_user_model().objects.filter(tenants__schema_name=schema).values_list('pk', flat=True)
    return Activity.objects.exclude(assigned_to_id__in=list(members)).delete()[0]


# masters shared by every branch unless a branch is chosen (owners, tenants / buyers, vendors)
SHARED = {'Owner', 'Party', 'Vendor'}


def fill_branches(default_code=None):
    """v1.22.0: give existing kit records their branch – from the record they belong to, else the default branch."""
    from django.apps import apps
    from zeo.appkit.engine import derive_branch, has_branch
    from OrganisationManager.models import brnch_mstr
    default = brnch_mstr.objects.filter(branch_code=default_code).values_list('pk', flat=True).first() if default_code else None
    out = {}
    # parents first, so children can copy them
    order = ['Contact', 'Building', 'Location', 'Listing', 'Unit', 'Agreement', 'Opportunity', 'Asset', 'Reservation', 'Lease', 'SaleContract',
             'PreventivePlan', 'Inspection', 'WorkOrder', 'Activity', 'Viewing', 'Offer', 'Bill', 'Cheque', 'Receipt', 'Commission', 'MoveOut',
             'OwnerStatement', 'Quote', 'CostLine']
    models = [m for lbl in ('ZeoCRM', 'ZeoProperty', 'ZeoMaintenance') if apps.is_installed(lbl)
              for m in apps.get_app_config(lbl).get_models() if has_branch(m)]
    models.sort(key=lambda m: order.index(m.__name__) if m.__name__ in order else 99)
    for m in models:
        n = 0
        for obj in m.objects.filter(branch__isnull=True):
            b = derive_branch(obj) or (None if m.__name__ in SHARED else default)
            if b:
                m.objects.filter(pk=obj.pk).update(branch_id=b)
                n += 1
        if n:
            out[m.__name__] = n
    return out
