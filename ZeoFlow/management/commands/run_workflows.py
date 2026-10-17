"""v1.29.0 – run the workflow scheduler (waits, delayed starts, date triggers, scheduled rules, approval reminders)
for every company – what Celery beat does every 15 minutes. Cron: */15 * * * * python manage.py run_workflows"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run due workflow actions, date and schedule triggers and approval reminders for every company'

    def add_arguments(self, parser):
        parser.add_argument('--schema', help='one company only')

    def handle(self, *args, **opts):
        from ZeoFlow.tasks import each
        from ZeoFlow.engine import run_due
        for sch, res in each(run_due, opts.get('schema')).items():
            self.stdout.write(f'{sch}: {res}')
