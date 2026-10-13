"""v1.23.0 – the template company that new cloud companies are copied from (seconds instead of about 2 minutes).

    cloud_template            create it when it does not exist
    cloud_template --rebuild  drop and create it again
It is an ordinary company (schema zeo_template), so migrate_schemas keeps it up to date. Nobody is a user of it.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create the ZEO Cloud template company'

    def add_arguments(self, p):
        p.add_argument('--rebuild', action='store_true')

    def handle(self, *args, **o):
        from django.db import connection
        from UserManagement.models import company as Company
        from ZeoCloud.services import TEMPLATE, default_country
        connection.set_schema_to_public()
        old = Company.objects.filter(schema_name=TEMPLATE).first()
        if old is not None and not o['rebuild']:
            self.stdout.write('The template exists.')
            return
        if old is not None:
            old.users.clear() if hasattr(old, 'users') else None
            old.delete(force_drop=True)
        c = Company(name='ZEO template (do not open)', schema_name=TEMPLATE, country=default_country(), industry_type='OTH')
        c.save()
        # nobody is a user of the template (the company signal may have added administrators)
        from UserManagement.models import CustomUser
        for u in CustomUser.objects.filter(tenants=c):
            u.tenants.remove(c)
        self.stdout.write(self.style.SUCCESS('Template company ready.'))
