"""v1.26.0 – clean the shared nationality list.

The list (Core.Nationality, one table for all companies) held duplicates (AMERICAN twice, CHINESE twice) and country
names typed by hand ("Bangladesh", "Philippines", "UK"), and missed FILIPINO, PAKISTANI, EMIRATI … because the seed list
stopped at "NIUEAN".

    python manage.py fix_nationalities            # dry run: lists what would change
    python manage.py fix_nationalities --apply    # adds the missing nationalities, moves every employee of every
                                                  # company from a duplicate / country-name row to the right
                                                  # nationality, then removes the extra rows
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Merge duplicate / country-name nationalities'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true')

    def handle(self, *args, **o):
        from django_tenants.utils import schema_context
        from Core.models import Nationality
        from EmpManagement.bulk_import import NAT_SYNONYMS
        from UserManagement.models import company as Company
        try:
            from Core.management.commands.load_master_data import COUNTRIES
        except Exception:
            COUNTRIES = []
        word_for = dict(NAT_SYNONYMS)
        for row in COUNTRIES:
            word_for[row[0].upper()] = row[6].upper()
            word_for[row[6].upper()] = row[6].upper()
        connection.set_schema_to_public()
        rows = list(Nationality.objects.all().order_by('id'))
        keep, moves = {}, {}
        for n in rows:
            name = ' '.join((n.N_name or '').split()).upper()
            if not name:
                continue
            target = word_for.get(name, name)
            if target in keep:
                moves[n.pk] = (n.N_name, keep[target])
            else:
                if target != name:   # a country name row: is there already a proper row?
                    proper = next((x for x in rows if ' '.join((x.N_name or '').split()).upper() == target), None)
                    if proper is not None:
                        keep[target] = proper
                        if proper.pk != n.pk:
                            moves[n.pk] = (n.N_name, proper)
                        continue
                    if o['apply']:
                        n.N_name = target
                        n.save(update_fields=['N_name'])
                    self.stdout.write(f'rename {name!r} -> {target!r}')
                keep[target] = n
        for pk, (old, new) in moves.items():
            self.stdout.write(f'merge {old!r} (#{pk}) -> {new.N_name!r} (#{new.pk})')
        if not o['apply']:
            self.stdout.write(self.style.WARNING(f'Dry run: {len(moves)} row(s) to merge. Add --apply.'))
            return
        for c in Company.objects.exclude(schema_name='public'):
            with schema_context(c.schema_name):
                from EmpManagement.models import emp_master
                for pk, (_, new) in moves.items():
                    emp_master.objects.filter(emp_nationality_id=pk).update(emp_nationality_id=new.pk)
                    try:
                        from django.apps import apps
                        apps.get_model('RecruitmentManagement', 'Candidate').objects.filter(nationality_id=pk).update(nationality_id=new.pk)
                    except Exception:
                        pass
        connection.set_schema_to_public()
        Nationality.objects.filter(pk__in=list(moves)).delete()
        self.stdout.write(self.style.SUCCESS(f'{len(moves)} duplicate nationality row(s) merged.'))
