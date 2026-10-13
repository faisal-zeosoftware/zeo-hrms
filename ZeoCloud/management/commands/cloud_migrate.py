"""v1.24.0 – update every database after copying a new version.

    python manage.py cloud_migrate

1. shared migrations in the main database (same as migrate_schemas --shared);
2. shared migrations in every own database;
3. company migrations in every company, wherever it lives;
4. the shared rows every own database needs (masters, content types, users).
Use it instead of migrate_schemas on a ZEO Cloud server. Safe to run again.
"""
from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = 'Migrate the main database, every own database and every company'

    def handle(self, *a, **o):
        from ZeoCloud import owndb
        from ZeoCloud.models import DatabaseServer
        self.stdout.write('Main database – shared tables …')
        call_command('migrate_schemas', '--shared', interactive=False, verbosity=1)
        for srv in DatabaseServer.objects.filter(state='ready'):
            self.stdout.write(f'Own database {srv.key} ({srv.name}) – shared tables …')
        out = owndb.migrate_all()
        for k, v in out.items():
            self.stdout.write(f'  {k}: {v}')
        self.stdout.write(self.style.SUCCESS(f'Done – {len(out)} own database(s), every company migrated, shared rows copied.'))
