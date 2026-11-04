"""v1.11.0 – run the scheduled leave jobs by hand or from cron: escalations, and on the 1st the leave-year end and monthly accrual."""
from datetime import date

from django.core.management.base import BaseCommand
from django_tenants.utils import schema_context

from LeavePolicy.tasks import _tenants, daily_jobs


class Command(BaseCommand):
    help = 'Leave escalations, compensatory off expiry, and on the 1st the leave-year end and monthly accrual (all companies).'

    def add_arguments(self, parser):
        parser.add_argument('--date', help='Run as if today were YYYY-MM-DD')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--schema')
        parser.add_argument('--check', action='store_true', help='Only list balances that differ from their leave ledger')   # v1.62.4 (item 79)
        parser.add_argument('--fix', action='store_true', help='With --check: one adjustment ledger line per difference (balance unchanged)')   # v1.62.11

    def handle(self, *args, **o):
        from LeavePolicy.approvers import escalate_due
        today = date.fromisoformat(o['date']) if o.get('date') else None
        for t in ([o['schema']] if o.get('schema') else _tenants()):
            with schema_context(t):
                if o.get('check'):   # v1.62.4 (item 79): balance = sum of its ledger lines for every employee and leave type
                    from LeavePolicy.engine import ledger_mismatches
                    from django.db import connection
                    if 'LeavePolicy_leaveledger' not in connection.introspection.table_names():   # company without leave policies
                        self.stdout.write(f'{t}: no leave ledger (leave policies not set up) – skipped')
                        continue
                    bad = ledger_mismatches()
                    self.stdout.write(f'{t}: {len(bad)} balance(s) differ from the leave ledger' + ''.join(f'\n  employee {e} type {lt}: balance {b} ledger {s}' for e, lt, b, s in bad))
                    # v1.62.11 (pending item 253, user decision 3 Nov): --fix writes one dated adjustment line per difference so
                    # the ledger equals the balance (balances loaded before 1.62.4 without ledger lines); the balance is not changed
                    if o.get('fix') and bad:
                        from django.db import transaction
                        from LeavePolicy.engine import post
                        n = 0
                        with transaction.atomic():
                            for e, lt, b, s in bad:
                                if b is None:
                                    continue   # ledger lines without a balance row – listed only
                                post(e, lt, date.today(), 'adjustment', round(b - s, 2), note='Opening line for a balance loaded without a ledger line (v1.62.11)')
                                n += 1
                        self.stdout.write(f'{t}: {n} adjustment line(s) written; now {len(ledger_mismatches())} difference(s)')
                    continue
                esc = escalate_due(dry=o['dry_run'])
                jobs = daily_jobs(today, dry=o['dry_run'])
                self.stdout.write(f'{t}: escalations {len(esc)}, {jobs}')
