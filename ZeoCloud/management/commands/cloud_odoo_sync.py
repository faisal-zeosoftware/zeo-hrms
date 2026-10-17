"""v1.28.0 – read the payment state of unpaid ZEO Cloud invoices from Odoo (paid there → paid in ZEO)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Sync the payment state of unpaid invoices from Odoo.'

    def handle(self, *a, **o):
        from ZeoCloud.odoo import sync_all
        n, errors = sync_all()
        self.stdout.write(f'{n} invoice(s) now paid.')
        for e in errors:
            self.stdout.write(f'  {e}')
