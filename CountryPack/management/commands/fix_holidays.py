"""v1.15.1 – Puts every company's holiday calendar in line with its country pack.

    python manage.py fix_holidays                    # dry run: lists what would change, changes nothing
    python manage.py fix_holidays --apply            # removes the old non-country defaults and adds missing country holidays
    python manage.py fix_holidays --schema adepttest # one company only

Removes only the holidays the old default seed added (Christmas Day, Labor Day) where they are not public holidays of
the company's country; holidays the company added itself are listed under "other" and kept.
"""
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context


class Command(BaseCommand):
    help = "Lists (dry run) or fixes (--apply) holiday calendars against the company's country pack."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='make the changes (default: dry run)')
        parser.add_argument('--schema', help='only this company schema')

    def handle(self, *args, **o):
        from CountryPack.services import current_pack
        from CountryPack.holidays import fix_plan
        apply = bool(o.get('apply'))
        qs = get_tenant_model().objects.exclude(schema_name='public')
        if o.get('schema'):
            qs = qs.filter(schema_name=o['schema'])
        w = self.stdout.write
        w(('APPLY' if apply else 'DRY RUN (nothing is changed – add --apply to fix)'))
        for t in qs.order_by('schema_name'):
            pack = current_pack(t.schema_name)['pack']
            with schema_context(t.schema_name):
                res = fix_plan(pack, apply=apply)
            w(f'\n== {t.schema_name} ({t.name}) – country pack {pack}')
            if not res:
                w('   no holiday calendar')
            for c in res:
                w(f"   calendar #{c['calendar_id']} {c['calendar']} ({c['year']})")
                for r in c['remove']:
                    w(f"     {'removed' if apply else 'remove'}: {r['description']} {r['start_date']} (not a public holiday in this country)")
                for h in c['add']:
                    w(f"     {'added' if apply else 'add'}: {h['description']} {h['start_date']}" + (f" – {h['end_date']}" if h['end_date'] != h['start_date'] else ''))
                for r in c['other']:
                    w(f"     kept (company holiday, not in the country list): {r['description']} {r['start_date']}")
                if apply:
                    w(f"     added {len(c['added'])}, removed {len(c['removed'])}" + (f", skipped: {'; '.join(c['skipped'])}" if c['skipped'] else ''))
                elif not (c['remove'] or c['add']):
                    w('     nothing to change')
