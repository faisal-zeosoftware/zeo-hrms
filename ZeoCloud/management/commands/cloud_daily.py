"""v1.23.0 – send today's ZEO Cloud reminders (also run every day by Celery beat)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'ZEO Cloud renewal / grace / read-only reminders'

    def handle(self, *args, **o):
        from ZeoCloud.tasks import run_daily
        self.stdout.write(f'{run_daily()} reminder(s) sent.')
