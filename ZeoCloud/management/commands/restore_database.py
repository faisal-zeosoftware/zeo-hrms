"""v1.67.1 (companies) – a server administrator restores a deleted own database (with the companies deleted with it)
within 30 days:   python manage.py restore_database <key>      (keys: python manage.py restore_company --list)"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Restore a deleted own database and its companies (within 30 days)'

    def add_arguments(self, p):
        p.add_argument('key')

    def handle(self, *args, **o):
        from ZeoCloud import lifecycle as L
        try:
            r = L.restore_database(o['key'])
        except L.LifeError as e:
            raise CommandError(e.message)
        self.stdout.write(self.style.SUCCESS(f"Database restored: {L.db_title(r['server'])}; companies: {', '.join(r['companies']) or 'none'}."))
