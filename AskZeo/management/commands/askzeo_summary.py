"""Ask ZEO 1.72.0 – the monthly summary e-mail for one company (or show it with --dry-run)."""
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'Send (or show with --dry-run) the Ask ZEO monthly summary e-mail of one company.'

    def add_arguments(self, p):
        p.add_argument('--schema', required=True)
        p.add_argument('--dry-run', action='store_true')

    def handle(self, *a, **o):
        if o['schema'] == 'public':
            raise CommandError('Give a company schema, not public.')
        from AskZeo.summary import send_for_company
        with schema_context(o['schema']):
            r = send_for_company(dry_run=o['dry_run'])
        self.stdout.write(f"sent={r.get('sent')} to={r.get('to')} mode={r.get('mode')} {r.get('why') or ''}")
        if r.get('text'):
            self.stdout.write(r['subject'])
            self.stdout.write(r['text'])
