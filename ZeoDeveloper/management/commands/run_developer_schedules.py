from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'ZEO Developer: run the daily / monthly scripts and scheduled calculations that are due (every company, or --schema).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', default=None)
        parser.add_argument('--force', action='store_true', help='run every scheduled job now, due or not')

    def handle(self, *a, **o):
        from ZeoDeveloper.tasks import each
        from ZeoDeveloper.schedule import run_due
        res = each(lambda: run_due(force=o['force']), o['schema'])
        for sch, r in res.items():
            self.stdout.write(f'{sch}: {r}')
