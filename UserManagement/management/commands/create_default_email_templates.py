from django.core.management.base import BaseCommand

from UserManagement.models import company
from UserManagement.signals import create_default_email_templates


class Command(BaseCommand):
    help = "Create the default e-mail templates (all request types) for every company that is missing them."

    def handle(self, *args, **options):
        for tenant in company.objects.exclude(schema_name='public'):
            create_default_email_templates(sender=None, tenant=tenant)
            self.stdout.write(f"templates checked for {tenant.schema_name}")
