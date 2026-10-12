"""HSE – set-up of a company (safe to run again; nothing the company changed is overwritten).

    python manage.py zeohse_setup                                   # every company: install the pack of its industry
    python manage.py zeohse_setup --schema adepttest --roles        # + role groups
    python manage.py zeohse_setup --schema adepttest --industry construction

v1.17.0: HSE is normally activated in the app (HSE › activation), where its industry is chosen. This command activates
it from the command line: with --industry that pack, otherwise the pack suggested by the company industry; a company
already activated only gets the extra pack (add-only). --roles creates the groups "HSE – <role>" (Settings → Permission groups);
add users to them as usual. Company administrators have every HSE role.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Set up HSE in a company: industry pack (by the company industry) and, with --roles, the HSE role groups.'

    def add_arguments(self, p):
        p.add_argument('--schema')
        p.add_argument('--industry', help='general / construction / manufacturing / healthcare / oil_gas')
        p.add_argument('--roles', action='store_true')

    def handle(self, *args, **o):
        from ZeoHSE.packs import PACKS, install
        from ZeoHSE.signals import industry_of
        T = get_tenant_model()
        tenants = T.objects.exclude(schema_name='public')
        if o.get('schema'):
            tenants = tenants.filter(schema_name=o['schema'])
        if o.get('industry') and o['industry'] not in PACKS:
            self.stderr.write('Unknown industry. Use: ' + ', '.join(PACKS))
            return
        for t in tenants:
            with schema_context(t.schema_name):
                from django.utils import timezone
                from ZeoHSE.models import HSESettings
                s = HSESettings.get()
                ind = o.get('industry') or (s.industry if s.activated_at else industry_of(t))
                first = s.activated_at is None
                self.stdout.write(f'{t.schema_name}: pack {ind} → {install(ind, primary=first)}')
                if first:
                    s = HSESettings.get()
                    s.industry, s.activated_at = ind, timezone.now()
                    s.industry_source = 'company' if ind == industry_of(t) else 'chosen'
                    s.save()
                    self.stdout.write(f'{t.schema_name}: HSE activated ({ind})')
                if o.get('roles'):
                    self.stdout.write(f'{t.schema_name}: {self.roles()}')

    def roles(self):
        from ZeoHSE.access import code_of
        from ZeoHSE.models import ROLES
        out = []
        for k, label in ROLES:
            g, _ = Group.objects.get_or_create(name=f'HSE – {label}')
            perm = Permission.objects.filter(content_type__app_label='ZeoHSE', codename=code_of(k)).first()
            if perm:
                g.permissions.add(perm)
            out.append(g.name)
        return 'groups: ' + ', '.join(out)
