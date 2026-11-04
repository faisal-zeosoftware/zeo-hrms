"""v1.62.3 (items 104, 125) – final settlements paid before 1.62.3.

From 1.62.3 paying a final settlement closes what it recovered (loans, salary advances, air ticket allocations – item 104) and makes
the leaver inactive with the login switched off (item 125). Settlements paid before still have them open / active:

    python manage.py close_paid_settlements --schema gdis                          # dry run: what is still open
    python manage.py close_paid_settlements --schema gdis --inactive-only --apply  # only make the paid leavers inactive (item 125)
    python manage.py close_paid_settlements --schema gdis --apply                  # also close the loans / advances / allocations

Without --schema every company is checked. Closing a loan records the settlement's loan line as a repayment outside payroll.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = 'v1.62.3: close what paid final settlements recovered and make their leavers inactive (dry run unless --apply).'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true')
        p.add_argument('--inactive-only', dest='inactive_only', action='store_true', help='only make the leavers inactive (item 125)')
        p.add_argument('--schema', action='append')

    def handle(self, *a, **o):
        from django.apps import apps
        schemas = o.get('schema') or [t.schema_name for t in get_tenant_model().objects.exclude(schema_name='public')]
        for sch in schemas:
            with schema_context(sch):
                try:
                    X = apps.get_model('PayrollPlus', 'ExitSettlement')
                    E = apps.get_model('EmpManagement', 'emp_master')
                    LA = apps.get_model('PayrollManagement', 'LoanApplication')
                    AS = apps.get_model('PayrollManagement', 'AdvanceSalaryRequest')
                    ALC = apps.get_model('PayrollManagement', 'AirTicketAllocation')
                    rows = list(X.objects.filter(status='paid').order_by('pk'))
                except Exception:
                    continue
                from PayrollPlus import services as S
                from PayrollPlus.loans import ACTIVE
                for st in rows:
                    emp = E.objects.filter(pk=st.employee_id).select_related('users').first()
                    if emp is None:
                        continue
                    keys = [str(l.get('key') or '') for l in (st.lines or []) if l.get('kind') == 'deduction']
                    loans = list(LA.objects.filter(employee_id=emp.pk, status__in=ACTIVE).values_list('document_number', 'status', 'remaining_balance'))
                    adv = list(AS.objects.filter(employee_id=emp.pk, status__iexact='approved', pk__in=[int(k.split('_')[1]) for k in keys
                                                 if k.startswith('advance_') and k.split('_')[1].isdigit()]).values_list('document_number', flat=True))
                    alc = ALC.objects.filter(employee_id=emp.pk, is_active=True).count()
                    active = emp.is_active is not False or emp.emp_status is not False
                    login = bool(emp.users_id and emp.users.is_active)
                    if not (active or login or loans or adv or alc):
                        continue
                    self.stdout.write(f'{sch} {st.number} {emp.emp_code}: active={active} login_on={login} open loans={loans} '
                                      f'advances={adv} open allocations={alc}')
                    if not o['apply']:
                        continue
                    with transaction.atomic():
                        done = {} if o['inactive_only'] else S.close_recovered(st, None)
                        done.update(S.make_leaver_inactive(st))
                    self.stdout.write(f'  done: {done}')
