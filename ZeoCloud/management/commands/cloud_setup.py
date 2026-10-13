"""v1.23.0 – create the four plans (Standard, Advance, Premium, Enterprise). Safe to run again; prices and limits are then edited in the console."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create the ZEO Cloud plans'

    def handle(self, *args, **o):
        from ZeoCloud.services import ensure_plans
        n = ensure_plans()
        self.stdout.write(self.style.SUCCESS(f'ZEO Cloud plans ready ({n} created).'))
