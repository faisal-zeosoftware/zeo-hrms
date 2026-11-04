"""
v1.62.3 – salary paid for leave days that became unpaid after the payslip was closed (the calendar of a paid month changed:
1.62.1 nightly job on half-pay / no-pay days, item 48; pay steps worked out again, item 49).

    python manage.py recover_leave_overpayments --schema gdis                 dry run: every approved payslip whose days worked
                                                                              differ from the calendar now (nothing is changed)
    python manage.py recover_leave_overpayments --schema gdis --apply         write the recovery lines (taken with the next payrolls)
    options: --employee 17  --payslip 216  --cap 20 (most a month as % of the payslip's gross; 0 = all at once)

The recovery waits as arrear lines (Payroll › Arrears) and the next payrolls deduct it as "Salary arrears recovery"; an
underpayment is paid as "Salary arrears". Running it again replaces the lines still waiting.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'Recover (or pay) salary of leave days whose pay changed after the payslip was closed (dry run unless --apply).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='Only this company (schema name).')
        parser.add_argument('--employee', type=int)
        parser.add_argument('--payslip', type=int)
        parser.add_argument('--cap', type=Decimal, default=Decimal('20'), help='Most a month, %% of gross (default 20).')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **o):
        qs = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            qs = qs.filter(schema_name=o['schema'])
        for t in qs:
            with schema_context(t.schema_name):
                from django.apps import apps
                from PayrollPlus import recovery as RC
                P = apps.get_model('PayrollManagement', 'Payslip')
                ps = P.objects.filter(status__iexact='approved').select_related('payroll_run', 'employee')
                if o.get('employee'):
                    ps = ps.filter(employee_id=o['employee'])
                if o.get('payslip'):
                    ps = ps.filter(pk=o['payslip'])
                n = 0
                for p in ps.order_by('employee_id', 'payroll_run__year', 'payroll_run__month'):
                    plan = RC.leave_overpayment(p)
                    if Decimal(plan['total']) == 0:
                        continue
                    n += 1
                    e = p.employee
                    self.stdout.write(f'{t.schema_name} {e.emp_code} payslip {p.pk} {plan["period"][0]}–{plan["period"][1]}: paid {plan["days_paid"]} days, '
                                      f'due {plan["days_due"]} – {"recover" if Decimal(plan["total"]) > 0 else "pay"} {abs(Decimal(plan["total"])):,.2f}')
                    if o.get('apply'):
                        r = RC.make_recovery(p, cap_pct=o['cap'])
                        for m in r.get('months', []):
                            self.stdout.write(f'    from {m["from"]}: {Decimal(m["total"]):,.2f}')
                self.stdout.write(f'{t.schema_name}: {n} payslip(s){"" if o.get("apply") else " (dry run – nothing written)"}')
