"""v1.13.1 – first-time setup of a new ZEO HRMS installation (run once after `migrate_schemas --shared`).

Creates (each step is skipped when it already exists, so the command can be run again):
  1. shared master data (countries, emirates, currencies, nationalities, religions, languages, VAT);
  2. the public tenant and its domain;
  3. the first administrator (a superuser who can open every company);
  4. the first company (its own database schema, with all tables) and its first branch;
  5. the country defaults of the company's country pack (document types, country rule set, weekend; v1.14.0 –
     skip with --no-country-defaults) and optional company defaults: UAE leave types and policies, expense categories and policy.

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
        p.add_argument('--currency', default=None, help="Currency code (default: the country's currency, AED for the UAE)")
        p.add_argument('--timezone', default=None, help="IANA time zone (default: the country's, Asia/Dubai for the UAE)")
        p.add_argument('--domain', default='localhost', help='Host name of the public tenant (default: localhost)')
        p.add_argument('--admin-username', required=True)
        p.add_argument('--admin-email', required=True)
        p.add_argument('--admin-password', help='Better: leave out and type it, or set ZEO_ADMIN_PASSWORD')
        p.add_argument('--branch', help='First branch name (e.g. "Head office")')
        p.add_argument('--branch-code', default='HO')
        p.add_argument('--probation-days', type=int, default=180)
        p.add_argument('--uae-leave', action='store_true', help='Load the UAE leave types and Office / Labour policies')
        p.add_argument('--expense-defaults', action='store_true', help='Load UAE expense categories and a default policy')
        # v1.21.0 apps of the company and its industry type (each app's default industry; Settings → Apps can change both later)
        p.add_argument('--apps', default='hrms,projects',
                       help='Apps the company uses, comma separated: hrms, projects, crm, property, maintenance, hse (default: hrms,projects)')
        p.add_argument('--industry', default=None,
                       help='Company industry type: IT, HC, FIN, EDU, MFG, CON, OG, PM, RET or OTH (General)')
        p.add_argument('--no-country-defaults', action='store_true',
                       help="Do not load the country pack defaults (document types, country rule set, weekend) for the company's country")

    def handle(self, *args, **o):
        from Core.models import cntry_mstr, crncy_mstr
        from UserManagement.models import CustomUser, Domain, company

        # 1. master data
        call_command('load_master_data', stdout=self.stdout)
        country = cntry_mstr.objects.filter(country_name__iexact=o['country']).first()
        if country is None:
            raise CommandError(f'Country "{o["country"]}" not found – use the English name, e.g. "United Arab Emirates".')
        # v1.14.0: currency and time zone follow the country unless given (UAE: AED, Asia/Dubai as before)
        if o.get('currency'):
            currency = crncy_mstr.objects.filter(currency_code__iexact=o['currency']).first()
        else:
            currency = crncy_mstr.objects.filter(country=country).order_by('id').first() or crncy_mstr.objects.filter(currency_code='AED').first()
        o['timezone'] = o.get('timezone') or country.timezone or 'Asia/Dubai'

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
            t = company(name=o['company'], schema_name=o.get('schema') or None, country=country, currency=currency, timezone=o['timezone'],
                        industry_type=(o.get('industry') or '').upper() or None)
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
                # v1.18.0: saving the company already made one branch (named after the company) and tied the weekend and holiday
                # calendars, leave types and salary components to it. The first branch asked for here is that branch, renamed –
                # before, a second, empty branch was added and employees put in it had no holiday or weekend calendar.
                auto = list(brnch_mstr.objects.all()[:2])
                if len(auto) == 1 and auto[0].branch_name == t.name:
                    b = auto[0]
                    b.branch_name, b.branch_code, b.probation_period_days = o['branch'], o['branch_code'], o['probation_days']
                    if b.br_city == 'Sample City':
                        b.br_city = ''
                    if b.br_pincode == '123456':
                        b.br_pincode = ''
                    b.save()
                    self.stdout.write(self.style.SUCCESS(f'Branch "{o["branch"]}" ({o["branch_code"]}) set up.'))
                else:
                    brnch_mstr.objects.create(branch_name=o['branch'], branch_code=o['branch_code'], br_country=country,
                                              probation_period_days=o['probation_days'], br_created_by=user)
                    self.stdout.write(self.style.SUCCESS(f'Branch "{o["branch"]}" ({o["branch_code"]}) created.'))
            # 5. defaults
            if not o.get('no_country_defaults'):
                try:
                    from CountryPack.services import apply_defaults
                    r = apply_defaults(user, schema_name=t.schema_name)
                    self.stdout.write(self.style.SUCCESS(
                        f'Country defaults ({r["pack"]}): ' + (', '.join(f'{a["kind"]} {a["name"]}' for a in r['added']) or 'nothing to add') + '.'))
                except ImportError:
                    pass
            if o.get('uae_leave'):
                from LeavePolicy import setup as leave_setup
                r = leave_setup.load(user)
                self.stdout.write(self.style.SUCCESS(f'UAE leave setup: {len(r.get("created_leave_types", []))} leave types, '
                                                     f'{len(r.get("created_policies", []))} policies.'))
        if o.get('expense_defaults'):
            call_command('load_expense_defaults', schema=t.schema_name, stdout=self.stdout)
        # v1.20.0: Projects app defaults (SLA policy, teams, products, categories, plan template) and its role groups
        try:
            from django.apps import apps as _apps
            chosen = [k.strip().lower() for k in (o.get('apps') or '').split(',') if k.strip()]
            if _apps.is_installed('ZeoProjects') and (not chosen or 'projects' in chosen):   # v1.22.0: only when Projects is chosen
                call_command('zeoprojects_setup', schema=t.schema_name, roles=True, stdout=self.stdout)
        except Exception as e:   # the company is set up; the Projects defaults can be added later
            self.stdout.write(self.style.WARNING(f'Projects defaults not added ({e}); run: python manage.py zeoprojects_setup --schema {t.schema_name} --roles'))
        # v1.21.0: the apps of the company (on / off, industry from the company) and the role groups of every installed app
        try:
            from ZeoApps import services as AS
            if o.get('industry') and (t.industry_type or '') != o['industry'].upper():
                t.industry_type = o['industry'].upper()
                t.save(update_fields=['industry_type'])
            keys = [k.strip().lower() for k in (o.get('apps') or '').split(',') if k.strip()]
            done, failed = AS.setup_new_company(t, keys, user)
            with schema_context(t.schema_name):
                for k in AS.KEYS:
                    if AS.installed(k):
                        try:
                            AS.make_roles(k)
                        except Exception:
                            pass
            self.stdout.write(self.style.SUCCESS(f'Apps active: {", ".join(done) or "none"}.'))
            for k, why in failed.items():
                self.stdout.write(self.style.WARNING(f'{k} not activated ({why}); activate it in Settings → Apps.'))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'Apps not set ({e}); choose them in Settings → Apps.'))
        self.stdout.write(self.style.SUCCESS(
            f'Done. Log in with the e-mail "{user.email}" and choose "{t.name}". Next: Settings → Branch permissions, '
            f'Organisation → Settings, Employees → Setup.'))
