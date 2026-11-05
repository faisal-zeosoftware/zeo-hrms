"""v1.67.1 (companies) – a server administrator restores a deleted company within 30 days.

    python manage.py restore_company <schema>        the company is active again and its users are linked again
                                                      (" (restored)" is added when its name is taken meanwhile)
    python manage.py restore_company --list          the deleted companies and databases that can still be restored
A company deleted together with its own database comes back with `restore_database <key>`.
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Restore a deleted company (within 30 days)'

    def add_arguments(self, p):
        p.add_argument('schema', nargs='?', default='')
        p.add_argument('--list', action='store_true')

    def handle(self, *args, **o):
        from ZeoCloud import lifecycle as L
        if o['list'] or not o['schema']:
            d = L.deleted_list()
            if not d['companies'] and not d['databases']:
                self.stdout.write('Nothing deleted to restore.')
            for c in d['companies']:
                self.stdout.write(f"company  {c['schema_name']:<30} {c['name']}  deleted {c['deleted_at'][:10] if c['deleted_at'] else ''}"
                                  f"  restore until {c['restore_until']}" + (f"  (with database {c['db_key']})" if c['db_key'] else ''))
            for x in d['databases']:
                self.stdout.write(f"database {x['key']:<30} {x['label']}  removed {x['retired_on']}  dropped on {x['delete_on']}")
            return
        try:
            life = L.restore_company(o['schema'])
        except L.LifeError as e:
            raise CommandError(e.message)
        self.stdout.write(self.style.SUCCESS(f'Company restored: {life.company.name} ({life.schema_name}); '
                                             f'{len((life.links or {}).get("tenants") or [])} user(s) linked again.'))
