"""v1.67.30 – kept for the 1.67.30 notes. Since 1.67.31 this runs `repair_lost_migrations`, which repairs ZeoMasters 0007 and every
other migration lost while a company was created (see that command)."""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Same as repair_lost_migrations (1.67.31).'

    def add_arguments(self, p):
        p.add_argument('--schema')
        p.add_argument('--check', action='store_true')

    def handle(self, *a, **o):
        kw = {'check': o.get('check', False)}
        if o.get('schema'):
            kw['schema'] = o['schema']
        call_command('repair_lost_migrations', **kw)
