"""v1.13.1 – first-time setup of a new ZEO HRMS installation (run once after `migrate_schemas --shared`).

Creates (each step is skipped when it already exists, so the command can be run again):
  1. shared master data (countries, emirates, currencies, nationalities, religions, languages, VAT);
  2. the public tenant and its domain;
  3. the first administrator (a superuser who can open every company);
  4. the first company (its own database schema, with all tables) and its first branch;
  5. optional company defaults: UAE leave types and policies, expense categories and policy.

Example:
  python manage.py setup_zeo --company "Adept Business Solutions FZE" --admin-username admin \
      --admin-email admin@example.com --branch "Head office" --branch-code HO --uae-leave --expense-defaults
The administrator password is asked for (or taken from ZEO_ADMIN_PASSWORD).
"""
import getpass
import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'First-time setup: master data, public tenant, administrator, first company and branch.'

    def add_arguments(self, p):
        p.add_argument('--company', required=True, help='Company name, e.g. "Adept Business Solutions FZE"')
        p.add_argument('--schema', help='Database schema of the company (letters and digits; default: from the name)')
        p.add_argument('--country', default='United Arab Emirates')
        p.add_argument('--currency', default='AED')
        p.add_argument('--timezone', default='Asia/Dubai')
        p.add_argument('--domain', default='localhost', help='Host name of the public tenant (default: localhost)')
        p.add_argument('--admin-username', required=True)
        p.add_argument('--admin-email', required=True)
        p.add_argument('--admin-password', help='Better: leave out and type it, or set ZEO_ADMIN_PASSWORD')
        p.add_argument('--branch', help='First branch name (e.g. "Head office")')
        p.add_argument('--branch-code', default='HO')
        p.add_argument('--probation-days', type=int, default=180)
        p.add_argument('--uae-leave', action='store_true', help='Load the UAE leave types and Office / Labour policies')
        p.add_argument('--expense-defaults', action='store_true', help='Load UAE expense categories and a default policy')

    def handle(self, *args, **o):
        from Core.models import cntry_mstr, crncy_mstr
        from UserManagement.models import CustomUser, Domain, company

        # 1. master data
        call_command('load_master_data', stdout=self.stdout)
        country = cntry_mstr.objects.filter(country_name__iexact=o['country']).first()
        if country is None:
            raise CommandError(f'Country "{o["country"]}" not found – use the English name, e.g. "United Arab Emirates".')
        currency = crncy_mstr.objects.filter(currency_code__iexact=o['currency']).first()

        # 2. public tenant (no schema to create: "public" exists already)
        if not company.objects.filter(schema_name='public').exists():
            with transaction.atomic():
                company.objects.bulk_create([company(schema_name='public', name='Public', country=country)])
                pub = company.objects.get(schema_name='public')
                Domain.objects.create(domain=o['domain'], tenant=pub, is_primary=True)
            self.stdout.write(self.style.SUCCESS(f'Public tenant created (domain {o["domain"]}).'))
        else:
            self.stdout.write('Public tenant exists.')

        # 3. administrator
        user = CustomUser.objects.filter(username=o['admin_username']).first()
        if user is None:
            pw = o.get('admin_password') or os.environ.get('ZEO_ADMIN_PASSWORD')
            if not pw:
                pw = getpass.getpass('Password for the administrator: ')
                if pw != getpass.getpass('Again: '):
                    raise CommandError('The passwords do not match.')
            if len(pw) < 10:
                raise CommandError('Use a password of at least 10 characters.')
            user = CustomUser.objects.create_superuser(o['admin_username'], o['admin_email'], pw)
            self.stdout.write(self.style.SUCCESS(f'Administrator {user.username} created.'))
        else:
            self.stdout.write(f'Administrator {user.username} exists.')

        # 4. company (saving it creates the schema and all company tables – takes a minute)
        t = company.objects.filter(name=o['company']).first() or (company.objects.filter(schema_name=o['schema']).first() if o.get('schema') else None)
        if t is None:
            self.stdout.write('Creating the company schema and its tables (about a minute)…')
            t = company(name=o['company'], schema_name=o.get('schema') or None, country=country, currency=currency, timezone=o['timezone'])
            t.save()
            self.stdout.write(self.style.SUCCESS(f'Company "{t.name}" created – schema "{t.schema_name}".'))
        else:
            self.stdout.write(f'Company "{t.name}" exists – schema "{t.schema_name}".')
        if not user.tenants.filter(pk=t.pk).exists():
            user.tenants.add(t)
        # company-admin rights row (the menus and every screen read it; without it the administrator sees no pages)
        from tenant_users.permissions.models import UserTenantPermissions
        with schema_context(t.schema_name):
            utp, _ = UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_superuser': True, 'is_staff': True})
            if not (utp.is_superuser and utp.is_staff):
                utp.is_superuser = utp.is_staff = True
                utp.save(update_fields=['is_superuser', 'is_staff'])

        with schema_context(t.schema_name):
            from OrganisationManager.models import brnch_mstr
            if o.get('branch') and not brnch_mstr.objects.filter(branch_code=o['branch_code']).exists():
                brnch_mstr.objects.create(branch_name=o['branch'], branch_code=o['branch_code'], br_country=country,
                                          probation_period_days=o['probation_days'], br_created_by=user)
                self.stdout.write(self.style.SUCCESS(f'Branch "{o["branch"]}" ({o["branch_code"]}) created.'))
            # 5. defaults
            if o.get('uae_leave'):
                from LeavePolicy import setup as leave_setup
                r = leave_setup.load(user)
                self.stdout.write(self.style.SUCCESS(f'UAE leave setup: {len(r.get("created_leave_types", []))} leave types, '
                                                     f'{len(r.get("created_policies", []))} policies.'))
        if o.get('expense_defaults'):
            call_command('load_expense_defaults', schema=t.schema_name, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(
            f'Done. Log in with the e-mail "{user.email}" and choose "{t.name}". Next: Settings → Branch permissions, '
            f'Organisation → Settings, Employees → Setup.'))
