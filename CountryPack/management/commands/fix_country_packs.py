"""v1.26.0 – repair companies that resolved to the neutral (GEN) pack although their country is a GCC country.

    python manage.py fix_country_packs                 # dry run: lists countries and companies
    python manage.py fix_country_packs --apply         # fixes country codes (UAE/ARE/971 -> AE …)
    python manage.py fix_country_packs --apply --defaults   # … and loads the country defaults into every company
                                                             #     (UAE leave types and policies, allowances, payslip
                                                             #     approval, public holidays, weekend, document types)
    --schema <name> limits --defaults to one company.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Repair GCC companies that resolved to the neutral country pack'

    def add_arguments(self, p):
        p.add_argument('--apply', action='store_true')
        p.add_argument('--defaults', action='store_true')
        p.add_argument('--schema', default='')

    def handle(self, *args, **o):
        from Core.models import cntry_mstr
        from UserManagement.models import company as Company
        from CountryPack.packs import iso2_of, pack_for_country
        connection.set_schema_to_public()
        fixed = 0
        for c in cntry_mstr.objects.all().order_by('id'):
            iso = iso2_of(c)
            if iso in ('AE', 'SA', 'QA', 'OM', 'BH', 'KW') and (c.country_code or '').strip().upper() != iso:
                clash = cntry_mstr.objects.filter(country_code__iexact=iso).exclude(pk=c.pk).first()
                self.stdout.write(f'country {c.pk} {c.country_name!r}: code {c.country_code!r} -> {iso}'
                                  + (f' (kept: code {iso} already used by {clash.country_name!r})' if clash else ''))
                if o['apply'] and not clash:
                    c.country_code = iso
                    c.save(update_fields=['country_code'])
                    fixed += 1
        for comp in Company.objects.exclude(schema_name='public').select_related('country').order_by('id'):
            if o['schema'] and comp.schema_name != o['schema']:
                continue
            pack = pack_for_country(comp.country)
            self.stdout.write(f'company {comp.schema_name}: {comp.country.country_name if comp.country_id else "-"} -> pack {pack}')
            if o['apply'] and o['defaults'] and comp.schema_name != 'zeo_template':
                from CountryPack import services as S
                S.clear_cache(comp.schema_name)
                res = S.apply_defaults(None, schema_name=comp.schema_name)
                for a in res['added']:
                    self.stdout.write(f"   + {a['kind']}: {a['name']} ({a['detail']})")
        self.stdout.write(self.style.SUCCESS(f'{fixed} country code(s) fixed.' if o['apply'] else 'Dry run – add --apply.'))
