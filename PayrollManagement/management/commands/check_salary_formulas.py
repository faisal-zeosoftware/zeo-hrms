"""v1.11.0 – list salary-component formulas that cannot be calculated (they were paid as 0); --fix repairs the known default."""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

OLD_GRATUITY = '(Basic Salary ÷ 30 × 21) ÷ 12'


class Command(BaseCommand):
    help = 'Check every salary-component formula of every company; --fix replaces the old default gratuity text with monthly_gratuity_accrual.'

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true')
        parser.add_argument('--schema')

    def handle(self, *args, **o):
        from PayrollManagement.formula import known_names, problems
        from PayrollManagement.models import SalaryComponent
        tenants = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            tenants = tenants.filter(schema_name=o['schema'])
        bad = 0
        for t in tenants:
            with schema_context(t.schema_name):
                known = known_names()
                for c in SalaryComponent.objects.exclude(formula__isnull=True).exclude(formula=''):
                    if c.formula.strip() == OLD_GRATUITY and o['fix']:
                        SalaryComponent.objects.filter(pk=c.pk).update(formula='monthly_gratuity_accrual')
                        self.stdout.write(f'{t.schema_name}: {c.code} {c.name}: fixed → monthly_gratuity_accrual')
                        continue
                    if c.component_value_type != 'variable':
                        continue
                    errs = problems(c.formula, c.code, c.name, known)
                    if errs:
                        bad += 1
                        self.stdout.write(f'{t.schema_name}: {c.code} {c.name}: "{c.formula}" – {"; ".join(errs)}')
        self.stdout.write(self.style.SUCCESS(f'{bad} formula(s) to correct.') if not bad else self.style.WARNING(f'{bad} formula(s) to correct.'))
