"""Projects – first set-up of a company (safe to run again; nothing is overwritten).

    python manage.py zeoprojects_setup                      # every company
    python manage.py zeoprojects_setup --schema adepttest --roles

Creates the settings row, a "Standard" SLA policy with the sample targets of the specification, issue categories,
supported products, support teams and the default project plan template
"IT implementation – System study to Go-live". With --roles it also creates the role groups (Settings → Permission groups):
  Projects – Support Manager / Support Engineer / AMC Account Manager / Project Manager / Developer /
  Finance / Management
Add users to these groups as usual. Company administrators need no group.
"""
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db.models import Q
from django_tenants.utils import get_tenant_model, schema_context

CATEGORIES = [('Bug', False), ('Configuration', False), ('Training', False), ('Development', True), ('Infrastructure', False)]
PRODUCTS = [('Odoo', 'erp'), ('Sage 300', 'erp'), ('Microsoft 365', 'microsoft'), ('Cloud hosting', 'cloud'), ('Network', 'network')]
TEAMS = ['ERP', 'Development', 'Infrastructure', 'HR / Payroll']

ZP = 'ZeoProjects'
PM = ('ProjectManagement', 'ProjectControl')
ROLES = {
    'Projects – Support Manager': {
        ZP: ['view_ticket', 'add_ticket', 'change_ticket', 'assign_ticket', 'work_ticket', 'approve_worklog', 'view_worklog',
             'view_customer', 'add_customer', 'change_customer', 'view_contact', 'add_contact', 'change_contact', 'view_amccontract',
             'view_devrequest', 'add_devrequest', 'change_devrequest', 'approve_tech_devrequest', 'change_slapolicy',
             'change_supportteam', 'change_projectssettings', 'view_projectprofile'],
    },
    'Projects – Support Engineer': {
        ZP: ['add_ticket', 'work_ticket', 'view_customer', 'view_contact', 'add_devrequest'],
    },
    'Projects – AMC Account Manager': {
        ZP: ['view_ticket', 'view_customer', 'add_customer', 'change_customer', 'view_contact', 'add_contact', 'change_contact',
             'view_amccontract', 'add_amccontract', 'change_amccontract', 'renew_amccontract', 'view_amcbilling', 'view_projectprofile'],
    },
    'Projects – Project Manager': {
        ZP: ['view_customer', 'view_projectprofile', 'change_projectprofile', 'add_ticket', 'work_ticket'],
        'ProjectManagement': ['view_project', 'add_project', 'change_project', 'view_task', 'add_task', 'change_task',
                              'view_projectstage', 'add_projectstage', 'change_projectstage', 'view_timesheet'],
        'ProjectControl': ['view_projectfinance', 'change_projectfinance'],
    },
    'Projects – Developer': {
        ZP: ['work_ticket'],
        'ProjectManagement': ['view_task', 'change_task'],
    },
    'Projects – Finance': {
        ZP: ['view_customer', 'view_amccontract', 'view_amcbilling', 'change_amcbilling', 'approve_commercial_devrequest',
             'view_devrequest', 'view_projectprofile'],
        'ProjectControl': ['view_projectfinance'],
    },
    'Projects – Management': {
        ZP: ['view_ticket', 'view_customer', 'view_amccontract', 'view_amcbilling', 'view_devrequest', 'view_projectprofile',
             'approve_changerequest'],
        'ProjectManagement': ['view_project'],
        'ProjectControl': ['view_projectfinance'],
    },
}


class Command(BaseCommand):
    help = 'Set up Projects in a company: settings, SLA policy, categories, products, teams (and role groups with --roles).'

    def add_arguments(self, p):
        p.add_argument('--schema')
        p.add_argument('--roles', action='store_true', help='Create / update the Projects role groups')

    def handle(self, *args, **o):
        schemas = [o['schema']] if o.get('schema') else list(get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        for sch in schemas:
            with schema_context(sch):
                self.stdout.write(f'{sch}: {self.defaults()}')
                if o.get('roles'):
                    self.stdout.write(f'{sch}: {self.roles()}')

    def defaults(self):
        from ZeoProjects.models import IssueCategory, ProjectsSettings, ServiceProduct, SLAPolicy, SLATarget, SupportTeam
        from ZeoProjects.sla import WorkCalendar
        s = ProjectsSettings.get()
        day = WorkCalendar(s).minutes_per_day() or 540
        made = []
        pol = SLAPolicy.objects.filter(name='Standard').first()
        if pol is None:
            pol = SLAPolicy.objects.create(name='Standard', description='Sample targets – change them to your contracts',
                                           is_default=not SLAPolicy.objects.filter(is_default=True).exists())
            for pr, resp, res, bh in (('critical', 30, 240, False), ('high', 60, 480, False),
                                      ('medium', 240, 2 * day, True), ('low', day, 5 * day, True)):
                SLATarget.objects.create(policy=pol, priority=pr, response_minutes=resp, resolution_minutes=res, business_hours=bh)
            made.append('SLA policy')
        if s.default_sla_id is None:
            s.default_sla = pol
        teams = {}
        for name in TEAMS:
            teams[name], c = SupportTeam.objects.get_or_create(name=name)
            if c:
                made.append(f'team {name}')
        if s.default_team_id is None:
            s.default_team = teams['ERP']
        s.save()
        for name, dev in CATEGORIES:
            _, c = IssueCategory.objects.get_or_create(name=name, defaults={'needs_development': dev,
                                                                            'default_team': teams['Development'] if dev else (teams['Infrastructure'] if name == 'Infrastructure' else None)})
            if c:
                made.append(f'category {name}')
        for name, cat in PRODUCTS:
            _, c = ServiceProduct.objects.get_or_create(name=name, defaults={'category': cat,
                                                                             'default_team': teams['Infrastructure'] if cat in ('cloud', 'network') else teams['ERP']})
            if c:
                made.append(f'product {name}')
        from ZeoProjects.plans import install_default_templates
        made += [f'plan template {n}' for n in install_default_templates()]
        return ', '.join(made) or 'already set up'

    def roles(self):
        out = []
        for gname, apps in ROLES.items():
            g, _ = Group.objects.get_or_create(name=gname)
            q = Q()
            for app, codes in apps.items():
                q |= Q(content_type__app_label=app, codename__in=codes)
            perms = list(Permission.objects.filter(q))
            g.permissions.add(*perms)
            out.append(f'{gname} ({len(perms)} rights)')
        return '; '.join(out)
