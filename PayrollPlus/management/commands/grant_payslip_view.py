"""v1.62.3 (item 141, user decision 1 Nov) – payroll preparers always get "Can view payslip".

The standard "Payroll Manager" group (seed: OrganisationManager/signals.py PAYROLL_CODES) had no view_payslip: a payroll preparer
saw only released payslips, so the draft payslips of a run could not be confirmed / sent for approval. New installs get it from the
seed (post_migrate); this command gives it to existing installs without a migration:

    python manage.py grant_payslip_view            # dry run: what would get the right
    python manage.py grant_payslip_view --apply    # do it

It adds view_payslip to the shared standard "Payroll Manager" group (used by every company that has no copy of it) and, in every
company (or --schema <name>, repeatable), to the company's own copies of that group (ZeoApps CompanyGroup.template_id). Other groups
that can prepare payroll (add_payrollrun) but cannot see payslips are listed only, or given the right too with --all-preparers.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import get_tenant_model, schema_context

TEMPLATE = 'Payroll Manager'


class Command(BaseCommand):
    help = 'v1.62.3: give "Can view payslip" to the standard Payroll Manager group and to the companies\' copies of it.'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true', help='add the right (default: dry run)')
        p.add_argument('--schema', action='append', help='only these companies (copies of the group)')
        # v1.62.3 (item 141): the user's rule is "payroll preparers always get Can view payslip" – also other groups that can
        # prepare payroll (add_payrollrun), e.g. a company's own "Finance" group
        p.add_argument('--all-preparers', action='store_true', help='also every other group that can prepare payroll')

    def handle(self, *a, **o):
        perm = Permission.objects.filter(codename='view_payslip', content_type__app_label='PayrollManagement').first()
        if perm is None:
            self.stdout.write('view_payslip permission not found – run migrate first.')
            return
        apply = o['apply']
        tpl = Group.objects.filter(name=TEMPLATE).first()
        targets = []
        if tpl is not None and not tpl.permissions.filter(pk=perm.pk).exists():
            targets.append((tpl, 'standard group (shared)'))
        schemas = o.get('schema') or [t.schema_name for t in get_tenant_model().objects.exclude(schema_name='public')]
        for sch in schemas:
            try:
                with schema_context(sch):
                    from ZeoApps.models import CompanyGroup
                    ids = list(CompanyGroup.objects.filter(template_id=tpl.pk).values_list('group_id', flat=True)) if tpl else []
            except Exception:
                continue
            for g in Group.objects.filter(pk__in=ids).exclude(permissions=perm):
                targets.append((g, f'copy of {sch}'))
        for g, why in targets:
            self.stdout.write(f'{"add" if apply else "would add"} view_payslip → {g.name} ({why})')
        if apply and targets:
            with transaction.atomic():
                for g, _ in targets:
                    g.permissions.add(perm)
            try:
                from ZeoApps import groupscope
                groupscope.forget()
            except Exception:
                pass
        others = Group.objects.filter(permissions__codename='add_payrollrun').exclude(permissions=perm).exclude(
            pk__in=[g.pk for g, _ in targets] + ([tpl.pk] if tpl else [])).distinct()
        if o.get('all_preparers'):
            for g in others:
                self.stdout.write(f'{"add" if apply else "would add"} view_payslip → {g.name} (can prepare payroll)')
                if apply:
                    g.permissions.add(perm)
            if apply:
                try:
                    from ZeoApps import groupscope
                    groupscope.forget()
                except Exception:
                    pass
            others = []
        for g in others:
            self.stdout.write(f'info: "{g.name}" can prepare payroll (add_payrollrun) without view_payslip – not changed (company decision)')
        self.stdout.write(f'{len(targets)} group(s) {"updated" if apply else "to update (dry run)"}.')
