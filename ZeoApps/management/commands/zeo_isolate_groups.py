"""v1.62.0 isolation – one-time upgrade step for the permission groups (README "Company isolation").

Before 1.62.0 the standard groups ("HR Manager", "Payroll Manager", "Projects – …", "CRM – …" …) were changed in place by
company administrators – for every company – and `migrate` puts "HR Manager" / "Payroll Manager" back to their standard
rights each time. From 1.62.0 a company's change makes its own copy. This command keeps the rights the companies had
before the upgrade:

    # 1. BEFORE migrate (new code in place, old database): note the rights of every group
    python manage.py zeo_isolate_groups --snapshot /root/zeo-groups-before.json
    # 2. migrate as usual (migrate_schemas)
    # 3. dry run – what would be copied, per company
    python manage.py zeo_isolate_groups --from-snapshot /root/zeo-groups-before.json
    # 4. do it
    python manage.py zeo_isolate_groups --from-snapshot /root/zeo-groups-before.json --apply

For every shared group whose rights now differ from the snapshot, each company that uses it (its own users or invited
logins) gets its own copy with the rights of the snapshot, and its users are moved to the copy – nobody's rights change.
Without --from-snapshot the command lists the shared groups and the companies that use them (--report).
Options: --schema <name> (repeatable) to limit the companies.
"""
import json

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_tenant_model, schema_context


def _codes(g):
    return sorted(f'{a}.{c}' for a, c in g.permissions.values_list('content_type__app_label', 'codename'))


class Command(BaseCommand):
    help = 'v1.62.0: keep the companies\' rights when the shared permission groups become per-company (snapshot, then copy).'

    def add_arguments(self, p):
        p.add_argument('--snapshot', help='write the rights of every group to this JSON file (run before migrate)')
        p.add_argument('--from-snapshot', dest='from_snapshot', help='copy changed shared groups per company from this file')
        p.add_argument('--apply', action='store_true', help='make the copies (default: dry run)')
        p.add_argument('--report', action='store_true', help='list the shared groups and the companies using them')
        p.add_argument('--schema', action='append', help='only these companies')

    def handle(self, *a, **o):
        if o.get('snapshot'):
            snap = {g.name: _codes(g) for g in Group.objects.all().order_by('name')}
            with open(o['snapshot'], 'w') as f:
                json.dump(snap, f, indent=1, ensure_ascii=False)
            self.stdout.write(f'{len(snap)} groups written to {o["snapshot"]}')
            return
        snap = None
        if o.get('from_snapshot'):
            try:
                snap = json.load(open(o['from_snapshot']))
            except Exception as e:
                raise CommandError(f'cannot read {o["from_snapshot"]}: {e}')
        from ZeoApps import groupscope as G
        ts = get_tenant_model().objects.exclude(schema_name='public').order_by('schema_name')
        if o.get('schema'):
            ts = ts.filter(schema_name__in=o['schema'])
        apply = bool(o.get('apply'))
        total = 0
        for t in ts:
            try:
                with schema_context(t.schema_name):
                    G.forget()
                    lines = self._company(G, snap, apply)
            except Exception as e:
                self.stderr.write(f'{t.schema_name}: skipped ({type(e).__name__}: {e})')
                continue
            for ln in lines:
                self.stdout.write(f'{t.schema_name}: {ln}')
            total += len(lines)
        G.forget()
        self.stdout.write(('Done' if apply else 'Dry run – nothing changed (add --apply)') + f': {total} line(s).')

    def _company(self, G, snap, apply):
        from tenant_users.permissions.models import UserTenantPermissions as UTP
        out = []
        cm = G.copy_map()
        used = set(UTP.objects.filter(profile__in=G.own_members()).values_list('groups', flat=True))
        try:
            from ZeoCloud.models import CompanyAccess
            from django.db import connection
            names = {n for gs in CompanyAccess.objects.filter(company__schema_name=connection.schema_name).values_list('groups', flat=True) for n in (gs or [])}
            used |= set(Group.objects.filter(name__in=names).values_list('pk', flat=True))
        except Exception:
            pass
        for g in Group.objects.filter(pk__in=[i for i in used if i]).order_by('name'):
            if g.pk in cm or G.SUFFIX.search(g.name):
                continue
            reason = G.shared_reason(g)
            if not reason:
                continue
            if snap is None:
                out.append(f'uses the shared group "{g.name}" ({reason})')
                continue
            before = snap.get(g.name)
            if before is None or sorted(before) == _codes(g):
                continue
            perms = []
            for code in before:
                app, _, c = code.partition('.')
                p = Permission.objects.filter(content_type__app_label=app, codename=c).first()
                if p:
                    perms.append(p)
            if apply:
                c, n = G.customise(g, permissions=perms)
                out.append(f'"{g.name}": own copy "{c.name}" with the rights it had before the upgrade ({len(perms)} rights, {n} users moved)')
            else:
                out.append(f'"{g.name}": would get its own copy with the rights it had before the upgrade ({len(perms)} rights instead of {g.permissions.count()})')
        return out
