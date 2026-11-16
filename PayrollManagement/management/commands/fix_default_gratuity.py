"""v1.67.19 – the default Gratuity component (GTY, formula monthly_gratuity_accrual) was made as a payslip ADDITION, so a company
that put it in a salary structure paid the monthly accrual as cash in net pay and the WPS file. Gratuity is paid by the final
settlement; the monthly accrual is a provision (employer cost).

Lists (default) or, with --fix, changes the components still as made by ZEO (code GTY, formula monthly_gratuity_accrual,
type addition) to type "Others" and not shown on the payslip. A company that has changed the component (another formula, another
code) is not touched. Payslips already made are not changed.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'List (or with --fix change) the default Gratuity components that are paid as a payslip addition.'

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true')
        parser.add_argument('--schema')

    def handle(self, *args, **o):
        from PayrollManagement.models import SalaryComponent
        tenants = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            tenants = tenants.filter(schema_name=o['schema'])
        found = 0
        for t in tenants:
            with schema_context(t.schema_name):
                qs = SalaryComponent.objects.filter(code='GTY', component_type='addition', formula='monthly_gratuity_accrual')
                for c in qs:
                    found += 1
                    br = getattr(c.branch, 'branch_name', '') if c.branch_id else ''
                    self.stdout.write(f'{t.schema_name}: {c.code} {c.name} {br}: paid as an addition'
                                      + (' → changed to Others, not on the payslip' if o['fix'] else ''))
                if o['fix'] and found:
                    qs.update(component_type='others', show_in_payslip=False)
        msg = f'{found} default Gratuity component(s) ' + ('changed.' if o['fix'] else 'paid as an addition (run again with --fix).')
        self.stdout.write(self.style.SUCCESS(msg) if not found or o['fix'] else self.style.WARNING(msg))
