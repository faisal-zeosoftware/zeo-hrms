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

    def handle(self, *args, **o):
        from LeavePolicy.approvers import escalate_due
        today = date.fromisoformat(o['date']) if o.get('date') else None
        for t in ([o['schema']] if o.get('schema') else _tenants()):
            with schema_context(t):
                esc = escalate_due(dry=o['dry_run'])
                jobs = daily_jobs(today, dry=o['dry_run'])
                self.stdout.write(f'{t}: escalations {len(esc)}, {jobs}')
