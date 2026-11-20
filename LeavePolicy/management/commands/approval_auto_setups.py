"""v1.73.19 (item 5): approval set-ups that ZEO saved by itself before 1.73.19.

When a general request, resignation or loan without an approval set-up was approved by the reporting manager, ZEO saved a
"No approval" set-up for its type (level "Auto Level" / "Auto Approval" with no approver) – every later request of that type was then
approved by itself. This command lists them in every company; with --fix it removes them, so the requests go to the reporting manager
again (or set the levels in Approval rules).
    python manage.py approval_auto_setups [--schema S] [--fix]"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

SETS = (('EmpManagement.ApprovalWorkflow', 'levels', 'request_type', 'General request'),
        ('EmpManagement.ResignationApprovalWorkflow', 'resignation_levels', None, 'Resignation'),
        ('PayrollManagement.LoanApprovalWorkflow', 'loan_levels', 'loan_type', 'Loan'))


def auto_setups():
    out = []
    for lab, rel, typ, name in SETS:
        try:
            W = apps.get_model(lab)
        except LookupError:
            continue
        for w in W.objects.filter(approval_type='no_approval'):
            lv = list(getattr(w, rel).all())
            if lv and all(x.approver_id is None and (x.role or '') in ('Auto Level', 'Auto Approval') for x in lv):
                out.append((w, name, str(getattr(w, typ, '') or '') if typ else ''))
    return out


class Command(BaseCommand):
    help = 'List (and with --fix remove) the "No approval" set-ups ZEO saved by itself before 1.73.19.'

    def add_arguments(self, p):
        p.add_argument('--schema')
        p.add_argument('--fix', action='store_true')

    def handle(self, *a, **o):
        T = get_tenant_model()
        qs = T.objects.exclude(schema_name='public')
        if o.get('schema'):
            qs = qs.filter(schema_name=o['schema'])
        total = 0
        for t in qs:
            try:
                with schema_context(t.schema_name):
                    rows = auto_setups()
                    for w, name, typ in rows:
                        self.stdout.write(f'{t.schema_name}: {name} {typ} – "No approval" saved by ZEO (#{w.pk})' + (' – removed' if o['fix'] else ''))
                        if o['fix']:
                            w.delete()
                    total += len(rows)
            except Exception as e:   # noqa: BLE001
                self.stdout.write(f'{t.schema_name}: skipped ({e})')
        self.stdout.write(f'{total} set-up(s) {"removed" if o["fix"] else "found"}.')
