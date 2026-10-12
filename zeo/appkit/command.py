"""Set-up command shared by the kit apps (each app has a two-line management command using make_command).

    python manage.py zeocrm_setup --schema adepttest --roles            # role groups "<App> – <role>"
    python manage.py zeocrm_setup --schema adepttest --activate [--industry property_management]
    python manage.py zeocrm_setup --daily                                # reminders / generators (run once a day by cron)
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


def make_roles(app):
    out = []
    for k, label in app.roles:
        g, _ = Group.objects.get_or_create(name=f'{app.title} – {label}')
        p = Permission.objects.filter(content_type__app_label=app.label, codename=app.code(k)).first()
        if p:
            g.permissions.add(p)
        out.append(g.name)
    return out


def make_command(label):
    class Command(BaseCommand):
        help = f'Set up {label}: role groups, activation with an industry pack, daily jobs.'

        def add_arguments(self, p):
            p.add_argument('--schema')
            p.add_argument('--roles', action='store_true')
            p.add_argument('--activate', action='store_true')
            p.add_argument('--industry')
            p.add_argument('--daily', action='store_true')

        def handle(self, *a, **o):
            from .engine import REGISTRY
            from . import packs as P
            import importlib
            importlib.import_module(f'{label}.app')
            app = REGISTRY[label]
            ts = get_tenant_model().objects.exclude(schema_name='public')
            if o.get('schema'):
                ts = ts.filter(schema_name=o['schema'])
            for t in ts:
                with schema_context(t.schema_name):
                    if o.get('roles'):
                        self.stdout.write(f'{t.schema_name}: groups {", ".join(make_roles(app))}')
                    if o.get('activate') or o.get('industry'):
                        s = app.settings()
                        ind = o.get('industry') or P.suggested(app)[2]
                        if s.activated_at:
                            self.stdout.write(f'{t.schema_name}: already active – pack {ind} {P.install(app, ind)}')
                        else:
                            self.stdout.write(f'{t.schema_name}: activated with {ind} {P.activate(app, ind, None)}')
                    if o.get('daily') and app.daily and app.activated():
                        try:
                            self.stdout.write(f'{t.schema_name}: daily {app.daily()}')
                        except Exception as e:
                            self.stderr.write(f'{t.schema_name}: daily failed {e}')
    return Command
