"""v1.73.22 (pending item 153) – brings the statutory schemes of an India company to 1.73.22:
* professional tax: the old standard row with the Maharashtra slabs only → the state-wise slabs (MH, KA, AP, TS, WB, GJ, TN);
  a row the company changed itself (its slabs are not the old default) is left alone and listed;
* labour welfare fund and the statutory bonus provision: added when missing (as "Apply country defaults" would);
* the salary components of the new schemes (as "Apply" in Payroll › Statutory schemes).
Only companies whose country pack is India. Without --fix it lists what it would do.

    python manage.py india_statutory_update            # list
    python manage.py india_statutory_update --fix      # update
"""
from django.apps import apps
from django.core.management.base import BaseCommand
from django_tenants.utils import get_tenant_model, schema_context

OLD_PT = [{'to': 7500, 'employee': 0, 'employer': 0}, {'to': 10000, 'employee': 175, 'employer': 0}, {'to': None, 'employee': 200, 'employer': 0}]


def _same(a, b):
    def n(rows):
        return [(None if r.get('to') in (None, '') else float(r.get('to')), float(r.get('employee') or 0), float(r.get('employer') or 0)) for r in rows or []]
    return n(a) == n(b)


class Command(BaseCommand):
    help = 'India: state-wise professional tax, labour welfare fund and statutory bonus schemes (v1.73.22).'

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true')
        parser.add_argument('--schema')

    def handle(self, *a, **o):
        from CountryPack import services as cp
        from CountryPack.packs import statutory_for
        T = get_tenant_model()
        schemas = [o['schema']] if o.get('schema') else list(T.objects.exclude(schema_name='public').values_list('schema_name', flat=True))
        new = {d['code']: d for d in statutory_for('IN')}
        n_comp = 0
        for sch in schemas:
            try:
                pack = cp.pack_code(sch)
            except Exception:   # noqa: BLE001
                continue
            if pack != 'IN':
                continue
            n_comp += 1
            with schema_context(sch):
                SS = apps.get_model('PayrollPlus', 'StatutoryScheme')
                notes = []
                pt = SS.objects.filter(code='pt').first()
                if pt is not None and pt.is_standard and _same(pt.slabs, OLD_PT):
                    notes.append('professional tax → state-wise slabs')
                    if o['fix']:
                        d = new['pt']
                        pt.name, pt.slabs, pt.law_reference = d['name'], d['slabs'], d['law_reference']
                        pt.save(update_fields=['name', 'slabs', 'law_reference'])
                elif pt is not None and not any(isinstance(r, dict) and r.get('state') for r in pt.slabs or []):
                    notes.append('professional tax kept (changed by the company – add the state rows yourself)')
                for code in ('lwf', 'bonus'):
                    if not SS.objects.filter(code=code).exists():
                        notes.append(f'{new[code]["name"]} added')
                        if o['fix']:
                            from CountryPack.packs import VERIFY
                            d = new[code]
                            row = SS(country='IN', is_standard=True, notes=(d.get('notes', '') + ('\n' if d.get('notes') else '') + VERIFY))
                            for k, v in d.items():
                                if k != 'notes' and hasattr(row, k):
                                    setattr(row, k, v)
                            row.save()
                if o['fix'] and notes:
                    from PayrollPlus.statutory import apply_components
                    added, _kept = apply_components(None)
                    if added:
                        notes.append('salary components: ' + ', '.join(added))
            self.stdout.write(f'{sch}: ' + ('; '.join(notes) if notes else 'up to date'))
        self.stdout.write(f'{n_comp} India compan{"y" if n_comp == 1 else "ies"} {"updated" if o["fix"] else "checked"}')
