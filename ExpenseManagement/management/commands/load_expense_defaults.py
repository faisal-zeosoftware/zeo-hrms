"""
UAE starter set for Expense management: categories and a default policy.

    python manage.py load_expense_defaults --schema=<company schema>     (one company)
    python manage.py load_expense_defaults                               (every company)

Existing categories / policies (same code / name) are left as they are, so the company's own changes stay.
Rates and limits are examples – each company sets its own in Expense → Policies.
"""
from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

# (code, name, kind, gl account, receipt above AED, description required)
CATEGORIES = [
    ('MIL', 'Mileage', 'mileage', '6110', None, True),
    ('AIR', 'Travel - Air', 'travel', '6120', D('0'), False),
    ('HTL', 'Hotel', 'hotel', '6130', D('0'), False),
    ('MEAL', 'Meals', 'meals', '6140', D('50'), False),
    ('PD', 'Per diem', 'per_diem', '6145', None, False),
    ('TAXI', 'Taxi / Transport', 'travel', '6150', D('50'), False),
    ('FUEL', 'Fuel', 'general', '6160', D('0'), False),
    ('PARK', 'Parking / Salik', 'general', '6170', D('100'), False),
    ('TEL', 'Telephone', 'general', '6180', D('0'), False),
    ('ENT', 'Client entertainment', 'meals', '6190', D('0'), True),
    ('GOV', 'Visa & government fees', 'general', '6210', D('0'), True),
    ('OFF', 'Office supplies', 'general', '6220', D('50'), False),
    ('TRN', 'Training', 'general', '6230', D('0'), True),
    ('OTH', 'Other', 'general', '6990', D('0'), True),
]
# category code: (per expense, per day, per report, warn | block)
LIMITS = {
    'MEAL': (D('150'), D('300'), None, 'warn'),
    'TAXI': (D('200'), D('400'), None, 'warn'),
    'HTL': (D('800'), D('800'), None, 'warn'),
    'ENT': (D('1500'), None, D('5000'), 'warn'),
    'OFF': (D('500'), None, None, 'block'),
    'TEL': (D('300'), None, D('300'), 'warn'),
}


def load(stdout=None):
    from ExpenseManagement.models import ExpenseCategory, ExpensePolicy, ExpensePolicyLimit, ExpenseSettings
    made = 0
    cats = {}
    for code, name, kind, gl, receipt, desc in CATEGORIES:
        c = ExpenseCategory.objects.filter(code=code).first() or ExpenseCategory.objects.filter(name=name).first()
        if c is None:
            c = ExpenseCategory.objects.create(code=code, name=name, kind=kind, gl_account=gl, receipt_required_above=receipt,
                                               description_required=desc)
            made += 1
        cats[code] = c
    pol = ExpensePolicy.objects.filter(is_default=True).first()
    if pol is None and not ExpensePolicy.objects.filter(name='Standard expense policy').exists():
        pol = ExpensePolicy.objects.create(name='Standard expense policy', is_default=True, mileage_rate=D('0.50'), per_diem_rate=D('150'),
                                           currency='AED', approval_roles=['reporting_manager', 'branch_hr'],
                                           description='Default policy – change the rates and limits to the company rules.')
        for code, (pe, pd, pr, act) in LIMITS.items():
            ExpensePolicyLimit.objects.create(policy=pol, category=cats[code], per_expense_limit=pe, per_day_limit=pd,
                                              per_report_limit=pr, block_or_warn=act)
        made += 1
    ExpenseSettings.get()
    return made


class Command(BaseCommand):
    help = 'Create UAE expense categories and a default expense policy (existing ones are kept).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='Company schema (default: every company)')

    def handle(self, *args, **opts):
        schemas = [opts['schema']] if opts.get('schema') else list(
            get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        for s in schemas:
            with schema_context(s):
                n = load()
            self.stdout.write(f'{s}: {n} record(s) created')
