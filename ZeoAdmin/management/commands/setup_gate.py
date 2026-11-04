"""v1.62.1 – required setup before HR transactions, for Adept staff on the server.

  python manage.py setup_gate --schema acme              shows the state
  python manage.py setup_gate --schema acme --allow      "Allow transactions before the setup is finished"
  python manage.py setup_gate --schema acme --wait       transactions wait for the setup steps again
  python manage.py setup_gate --schema acme --off        the rule does not apply to the company at all
"""
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import schema_context


class Command(BaseCommand):
    help = 'Show or change the required-setup rule of a company (v1.62.1).'

    def add_arguments(self, parser):
        parser.add_argument('--schema', required=True)
        g = parser.add_mutually_exclusive_group()
        g.add_argument('--allow', action='store_true')
        g.add_argument('--wait', action='store_true')
        g.add_argument('--off', action='store_true')

    def handle(self, *a, **o):
        from UserManagement.models import company
        if not company.objects.filter(schema_name=o['schema']).exists():
            raise CommandError(f"No company with schema {o['schema']}.")
        with schema_context(o['schema']):
            from ZeoAdmin import setup_gate as GATE
            from ZeoAdmin.models import SetupGuide
            if o['allow'] or o['wait']:
                GATE.set_allow(o['allow'])
            if o['off']:
                g = SetupGuide.get()
                g.gate_on = False
                g.save(update_fields=['gate_on', 'updated_at'])
                GATE.forget()
            st = GATE.state()
            names = GATE.open_names(st)
            self.stdout.write(f"{o['schema']}: rule {'on' if st['enforced'] else 'off'}, allowed early: {'yes' if st['allow'] else 'no'}, "
                              f"blocked: {'yes' if st['blocked'] else 'no'}; open steps: {', '.join(names) or 'none'}")
