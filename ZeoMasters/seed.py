"""v1.33.0 – standard masters per country: tax codes (VAT / GST), units of measure, payment terms. Only adds what is missing."""
from decimal import Decimal

from django.db import transaction

from .models import UOM, PaymentTerm, TaxCode

UOMS = [('PCS', 'Pieces', 'unit'), ('BOX', 'Box', 'unit'), ('SET', 'Set', 'unit'), ('KG', 'Kilogram', 'weight'), ('G', 'Gram', 'weight'),
        ('TON', 'Tonne', 'weight'), ('M', 'Metre', 'length'), ('CM', 'Centimetre', 'length'), ('SQM', 'Square metre', 'area'), ('SQFT', 'Square foot', 'area'),
        ('L', 'Litre', 'volume'), ('ML', 'Millilitre', 'volume'), ('HR', 'Hour', 'time'), ('DAY', 'Day', 'time'), ('MONTH', 'Month', 'time'),
        ('YEAR', 'Year', 'time'), ('JOB', 'Job / lump sum', 'other')]

TERMS = [('ADV', '100% in advance', 0, 'advance', 1), ('COD', 'On delivery', 0, 'net', 1), ('NET15', 'Net 15 days', 15, 'net', 1),
         ('NET30', 'Net 30 days', 30, 'net', 1), ('NET45', 'Net 45 days', 45, 'net', 1), ('NET60', 'Net 60 days', 60, 'net', 1),
         ('NET90', 'Net 90 days', 90, 'net', 1), ('5050', '50% advance, 50% on delivery', 0, 'advance', 2), ('PDC4', '4 post-dated cheques', 90, 'cheques', 4)]

# country → [(code, name, kind, rate, default)]
TAX = {
    'AE': [('VAT5', 'VAT 5%', 'vat', 5, True), ('VAT0', 'Zero-rated 0%', 'vat', 0, False), ('EXEMPT', 'Exempt', 'none', 0, False), ('OOS', 'Out of scope', 'none', 0, False)],
    'SA': [('VAT15', 'VAT 15%', 'vat', 15, True), ('VAT0', 'Zero-rated 0%', 'vat', 0, False), ('EXEMPT', 'Exempt', 'none', 0, False)],
    'OM': [('VAT5', 'VAT 5%', 'vat', 5, True), ('VAT0', 'Zero-rated 0%', 'vat', 0, False), ('EXEMPT', 'Exempt', 'none', 0, False)],
    'BH': [('VAT10', 'VAT 10%', 'vat', 10, True), ('VAT0', 'Zero-rated 0%', 'vat', 0, False), ('EXEMPT', 'Exempt', 'none', 0, False)],
    'QA': [('NOTAX', 'No VAT (not yet introduced)', 'none', 0, True)],
    'KW': [('NOTAX', 'No VAT (not yet introduced)', 'none', 0, True)],
    'IN': [('GST18', 'GST 18%', 'gst', 18, True), ('GST5', 'GST 5%', 'gst', 5, False), ('GST12', 'GST 12%', 'gst', 12, False), ('GST28', 'GST 28%', 'gst', 28, False),
           ('GST0', 'GST 0% / exempt', 'gst', 0, False)],
    'GEN': [('STD', 'Standard tax', 'vat', 0, True), ('NOTAX', 'No tax', 'none', 0, False)],
}
VERIFY = 'Standard rates at the time of the release – verify with the tax authority before use.'


def seed(country='GEN', user=None):
    """Adds the missing standard masters; returns {added: [...], kept: n}."""
    country = (country or 'GEN').upper()
    rows = TAX.get(country) or TAX['GEN']
    added, kept = [], 0
    with transaction.atomic():
        for code, name, cat in UOMS:
            if UOM.objects.filter(code=code).exists():
                kept += 1
            else:
                UOM.objects.create(code=code, name=name, category=cat)
                added.append(f'Unit {name}')
        for code, name, days, kind, n in TERMS:
            if PaymentTerm.objects.filter(code=code).exists():
                kept += 1
            else:
                PaymentTerm.objects.create(code=code, name=name, days=days, kind=kind, instalments=n, is_default=(code == 'NET30' and not PaymentTerm.objects.filter(is_default=True).exists()))
                added.append(f'Term {name}')
        has_default = TaxCode.objects.filter(is_default=True, is_active=True).exists()
        for code, name, kind, rate, default in rows:
            if TaxCode.objects.filter(code=code).exists():
                kept += 1
                continue
            TaxCode.objects.create(code=code, name=name, kind=kind, rate=Decimal(str(rate)), country=country, is_default=default and not has_default)
            if default and not has_default:
                has_default = True
            added.append(f'Tax {name}')
    return {'added': added, 'kept': kept, 'note': VERIFY}


def country_code():
    """ISO code of the company's country pack (CountryPack) or GEN."""
    try:
        from CountryPack.services import pack_code
        return (pack_code() or 'GEN').upper()
    except Exception:
        return 'GEN'
