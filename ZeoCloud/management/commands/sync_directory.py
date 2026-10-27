"""v1.54.0 – python manage.py sync_directory [--dry] [--id N]: Active Directory / LDAP users, groups and disabled accounts now
(the same as the nightly run of the Celery task ZeoAdmin daily)."""
import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Synchronise ZEO logins with Active Directory / LDAP (sign-ins with nightly sync on, or one with --id).'

    def add_arguments(self, parser):
        parser.add_argument('--dry', action='store_true', help='only count, change nothing')
        parser.add_argument('--id', type=int, help='one sign-in (also when its nightly sync is off)')

    def handle(self, *args, **o):
        from ZeoCloud import directory as D
        from ZeoCloud.models import SSOProvider
        qs = SSOProvider.objects.filter(kind='ldap', enabled=True)
        qs = qs.filter(pk=o['id']) if o.get('id') else qs.filter(ldap_sync=True)
        for p in qs:
            try:
                rep = D.sync(p, dry=o['dry'])
            except D.DirectoryError as e:
                rep = {'error': str(e)}
            self.stdout.write(f'{p.name}: {json.dumps(rep)}')
        if not qs.exists():
            self.stdout.write('No Active Directory / LDAP sign-in to synchronise.')
