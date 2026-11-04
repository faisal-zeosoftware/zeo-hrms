"""v1.62.6 (item 22) – upgrade check and repair of employees whose public-holiday calendar was overwritten by a weekend
assignment by department or category (bug fixed on 1 Nov 2026; calendars/holiday_repair.py). Dry run by default.

  manage.py repair_holiday_calendars                       # dry run, every company schema
  manage.py repair_holiday_calendars --schema gdis         # dry run, one company
  manage.py repair_holiday_calendars --schema gdis --apply # repair the 'bug' rows (others are listed only)
"""
import json

from django.core.management.base import BaseCommand
from django.db import connection
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = "Find (and with --apply repair) employees whose holiday calendar was replaced by a department / category weekend assignment."

    def add_arguments(self, parser):
        parser.add_argument('--schema')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--report')

    def handle(self, *args, **o):
        from calendars import holiday_repair
        schemas = [o['schema']] if o.get('schema') else list(get_tenant_model().objects.exclude(schema_name='public')
                                                                .values_list('schema_name', flat=True).order_by('schema_name'))
        report = {}
        for s in schemas:
            try:
                with schema_context(s):
                    rows = holiday_repair.check()
                    done = holiday_repair.apply(rows) if o['apply'] else []
            except Exception as ex:   # a schema without the HR tables
                self.stdout.write(f'{s}: skipped ({type(ex).__name__}: {str(ex)[:80]})')
                connection.close()
                continue
            report[s] = {'rows': rows, 'repaired': done}
            bug = [r for r in rows if r['kind'] == 'bug']
            self.stdout.write(f"{s}: {len(bug)} to repair, {len(rows) - len(bug)} other difference(s)"
                              + (f", {len(done)} repaired" if o['apply'] else ' (dry run)'))
            for r in rows:
                self.stdout.write(f"   {r['code']} {r['name']}{'' if r['active'] else ' (inactive)'}: {r['current']} → "
                                  f"{r['expected'] or 'none (no holiday assignment)'} – {r['action']}")
        if o.get('report'):
            json.dump({'applied': o['apply'], 'schemas': report}, open(o['report'], 'w'), indent=1, default=str)
