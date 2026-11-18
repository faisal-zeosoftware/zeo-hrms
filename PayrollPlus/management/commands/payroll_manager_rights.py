"""v1.67.24 (pending 285) – the rights of the standard "Payroll Manager" group (OrganisationManager/signals.py PAYROLL_CODES).

New installs get them from the seed (post_migrate sets the shared standard group). Companies that have their own copy of the group
(ZeoApps CompanyGroup.template_id) keep their copy as it is until this command adds the missing rights – it only ADDS, it never takes
a right away (a company may have given its payroll team more):

    python manage.py payroll_manager_rights            # dry run: which rights each copy would get
    python manage.py payroll_manager_rights --apply    # add them
    python manage.py payroll_manager_rights --schema <company> --apply
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import get_tenant_model, schema_context

TEMPLATE = 'Payroll Manager'


class Command(BaseCommand):
    help = 'v1.67.24: give the companies\' copies of the Payroll Manager group the standard payroll rights they miss (adds only).'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true')
        p.add_argument('--schema', action='append')

    def handle(self, *a, **o):
        from OrganisationManager.signals import PAYROLL_CODES
        perms = list(Permission.objects.filter(codename__in=PAYROLL_CODES))
        tpl = Group.objects.filter(name=TEMPLATE).first()
        targets = []
        if tpl is not None:
            targets.append((tpl, 'standard group (shared)'))
        for sch in (o.get('schema') or [t.schema_name for t in get_tenant_model().objects.exclude(schema_name='public')]):
            try:
                with schema_context(sch):
                    from ZeoApps.models import CompanyGroup
                    ids = list(CompanyGroup.objects.filter(template_id=tpl.pk).values_list('group_id', flat=True)) if tpl else []
            except Exception:   # noqa: BLE001
                continue
            targets += [(g, f'copy of {sch}') for g in Group.objects.filter(pk__in=ids)]
        n = 0
        with transaction.atomic():
            for g, why in targets:
                have = set(g.permissions.values_list('pk', flat=True))
                miss = [p for p in perms if p.pk not in have]
                if not miss:
                    continue
                n += 1
                self.stdout.write(f'{"add" if o["apply"] else "would add"} {len(miss)} right(s) → {g.name} ({why}): '
                                  + ', '.join(sorted({p.codename for p in miss}))[:400])
                if o['apply']:
                    g.permissions.add(*miss)
        if o['apply']:
            try:
                from ZeoApps import groupscope
                groupscope.forget()
            except Exception:   # noqa: BLE001
                pass
        self.stdout.write(f'{n} group(s) {"updated" if o["apply"] else "to update (dry run)"}.')
