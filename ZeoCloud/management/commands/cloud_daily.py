"""v1.23.0 – send today's ZEO Cloud reminders (also run every day by Celery beat).

v1.67.1 (companies): also removes the companies deleted more than 30 days ago and drops the own databases removed more
than 30 days ago (on every server). --date YYYY-MM-DD runs the job as on that day (tests, catching up).
"""
from datetime import date

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'ZEO Cloud renewal / grace / read-only reminders; removal of deleted companies and databases after 30 days'

    def add_arguments(self, p):
        p.add_argument('--date', default='', help='run as on this day (YYYY-MM-DD)')

    def handle(self, *args, **o):
        from ZeoCloud.tasks import run_daily
        d = date.fromisoformat(o['date']) if o.get('date') else None
        self.stdout.write(f'{run_daily(d)} reminder(s) sent.')
