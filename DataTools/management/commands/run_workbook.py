"""v1.62.0 workbook – run a waiting check / load of Settings › Data workbook from the command line (no Celery, or to resume by hand):

    python manage.py run_workbook <run id> --schema <company>
    python manage.py run_workbook --upload <upload id> --kind load|check [--from-month 2026-08] --schema <company> [--user admin]
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Run a data-workbook check / load of Settings › Data workbook (in this process).'

    def add_arguments(self, p):
        p.add_argument('run', nargs='?', type=int)
        p.add_argument('--schema', required=True)
        p.add_argument('--upload', type=int)
        p.add_argument('--kind', default='load', choices=['load', 'check', 'quick'])
        p.add_argument('--from-month', default='')
        p.add_argument('--user', default='')

    def handle(self, *a, **o):
        from django_tenants.utils import schema_context
        from DataTools.models import WorkbookRun, WorkbookUpload
        from DataTools.workbook_jobs import execute, run_json
        from UserManagement.models import CustomUser
        schema = o['schema']
        with schema_context(schema):
            if o['run']:
                run = WorkbookRun.objects.filter(pk=o['run']).first()
                if run is None:
                    raise CommandError('No such run.')
                if run.state != 'queued':   # run again: a new run with the same options
                    run = WorkbookRun.objects.create(upload=run.upload, kind=run.kind, options=run.options, created_by=run.created_by)
            else:
                up = WorkbookUpload.objects.filter(pk=o['upload'] or 0).first()
                if up is None:
                    raise CommandError('Give a run id or --upload <id>.')
                user = CustomUser.objects.filter(username=o['user']).first() if o['user'] else up.uploaded_by
                run = WorkbookRun.objects.create(upload=up, kind=o['kind'], created_by=user,
                                                 options={'from_month': o['from_month']} if o['from_month'] else {})
            if not run.runner:
                WorkbookRun.objects.filter(pk=run.pk).update(runner='command')
        execute(schema, run.pk)
        with schema_context(schema):
            r = run_json(WorkbookRun.objects.get(pk=run.pk))
        self.stdout.write(f'Run {r["id"]} ({r["kind"]}): {r["state_text"]} – {r["message"]}')
