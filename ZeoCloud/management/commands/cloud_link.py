"""v1.23.0 – put existing companies into a cloud account.

    cloud_link --account "Sahra Group" --owner owner@sahra.ae --plan advance --schemas sahra_con,sahra_fm [--active-until 2027-03-01]
The account is created when it does not exist; its owner becomes company administrator of the companies.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q


class Command(BaseCommand):
    help = 'Link existing companies to a ZEO Cloud account'

    def add_arguments(self, p):
        p.add_argument('--account', required=True)
        p.add_argument('--owner', required=True)
        p.add_argument('--plan', default='advance')
        p.add_argument('--schemas', required=True)
        p.add_argument('--apps', default='payroll,projects,crm,property,maintenance,hse')
        p.add_argument('--active-until')

    def handle(self, *args, **o):
        from UserManagement.models import CustomUser, company as C
        from ZeoCloud import services as S
        from ZeoCloud.models import Account, AccountCompany, Plan
        S.ensure_plans()
        plan = Plan.objects.filter(code=o['plan']).first()
        if plan is None:
            raise CommandError('Unknown plan')
        owner = CustomUser.objects.filter(Q(email__iexact=o['owner']) | Q(username__iexact=o['owner'])).first()
        if owner is None:
            raise CommandError(f"No user {o['owner']}")
        acc = Account.objects.filter(name=o['account']).first()
        if acc is None:
            until = date.fromisoformat(o['active_until']) if o.get('active_until') else None
            acc = S.create_account(o['account'], owner.email or owner.username, plan, owner=owner,
                                   apps=[a for a in o['apps'].split(',') if a], start=until)
        n = 0
        for sch in [s.strip() for s in o['schemas'].split(',') if s.strip()]:
            comp = C.objects.filter(schema_name=sch).first()
            if comp is None:
                self.stderr.write(f'No company {sch}')
                continue
            if AccountCompany.objects.filter(company=comp).exists():
                self.stderr.write(f'{sch} is already in an account')
                continue
            ok, extra, msg = S.check_company_add(acc)
            AccountCompany.objects.create(company=comp, account=acc, extra=extra)
            S.grant_admin(owner, comp)
            S.forget(sch)
            n += 1
        self.stdout.write(self.style.SUCCESS(f'{n} companies linked to {acc.name} (plan {acc.plan.name}).'))
