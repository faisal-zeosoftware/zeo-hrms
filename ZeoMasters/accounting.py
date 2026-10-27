"""v1.44.0 – accounting-ready masters: tax numbers by country, e-invoice readiness of a partner, stock valuation of items.

Tax numbers (checked when the partner's country code is one of these; other countries are not checked):
  AE  TRN – 15 digits starting with 100
  SA  VAT – 15 digits starting and ending with 3
  OM  VATIN – OM followed by 10 digits
  IN  GSTIN – 2-digit state code, PAN (5 letters, 4 digits, 1 letter), entity no., Z, check character
  QA, KW  no VAT yet – any value is kept as it is
"""
import re
from collections import defaultdict
from decimal import Decimal

from django.apps import apps as django_apps

ZERO = Decimal('0.00')
TAX_RULES = {
    'AE': (re.compile(r'^100\d{12}$'), 'A UAE TRN has 15 digits and starts with 100.'),
    'SA': (re.compile(r'^3\d{13}3$'), 'A Saudi VAT number has 15 digits and starts and ends with 3.'),
    'OM': (re.compile(r'^OM\d{10}$'), 'An Oman VATIN is OM followed by 10 digits.'),
    'IN': (re.compile(r'^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$'), 'A GSTIN has 15 characters: state code, PAN, entity number, Z and a check character.'),
}
COUNTRY_NAMES = {'united arab emirates': 'AE', 'uae': 'AE', 'emirates': 'AE', 'saudi arabia': 'SA', 'ksa': 'SA', 'kingdom of saudi arabia': 'SA',
                 'oman': 'OM', 'sultanate of oman': 'OM', 'qatar': 'QA', 'kuwait': 'KW', 'india': 'IN', 'bahrain': 'BH'}


def country_code_of(p):
    cc = (p.country_code or '').strip().upper()
    if cc:
        return cc
    c = (p.country or '').strip()
    if len(c) == 2:
        return c.upper()
    return COUNTRY_NAMES.get(c.lower(), '')


def clean_tax_id(p):
    """Normalise the tax number (no spaces / dashes, upper case) and check it for the partner's country. Returns the error or ''."""
    t = re.sub(r'[\s\-]', '', p.tax_id or '').upper()
    p.tax_id = t
    if not t:
        return ''
    rule = TAX_RULES.get(country_code_of(p))
    if rule and not rule[0].match(t):
        return rule[1]
    return ''


def einvoice_missing(p):
    """What the partner still needs for e-invoices in its country (empty list = ready)."""
    cc = country_code_of(p)
    miss = []
    b2b = p.kind == 'company' or bool(p.tax_id)
    if cc == 'SA':
        if b2b:
            if not p.tax_id and not (p.buyer_id_type and p.buyer_id):
                miss.append('VAT number or other buyer id (CRN, 700 number…)')
            for f, label in (('street', 'street'), ('building_no', 'building no.'), ('district', 'district'), ('city', 'city'), ('postal_code', 'postal code')):
                if not getattr(p, f):
                    miss.append(label)
    elif cc == 'AE':
        if b2b:
            if not p.tax_id:
                miss.append('TRN')
            if p.einvoice_channel == 'peppol' and not (p.einvoice_id or p.tax_id):
                miss.append('e-invoice address (endpoint id)')
    elif cc == 'OM':
        if b2b and not p.tax_id:
            miss.append('VATIN')
    elif cc == 'IN':
        if b2b:
            if not p.tax_id:
                miss.append('GSTIN')
            if not p.state:
                miss.append('state code')
            if not p.postal_code:
                miss.append('PIN code')
    if p.einvoice_channel == 'email' and not p.email:
        miss.append('e-mail')
    if p.einvoice_channel == 'peppol' and not p.einvoice_id and not p.tax_id:
        miss.append('e-invoice address (endpoint id)')
    if b2b and not cc:
        miss.append('country code')
    return miss


def einvoice_defaults(p):
    """Fill what can be worked out: the country code from the country name; the Peppol endpoint from the tax number (UAE, Oman)."""
    if not p.country_code:
        p.country_code = country_code_of(p)
    if p.einvoice_channel == 'peppol' and not p.einvoice_id and p.tax_id and p.country_code in ('AE', 'OM'):
        p.einvoice_id = p.tax_id
        if not p.einvoice_scheme and p.country_code == 'AE':
            p.einvoice_scheme = '0235'


# ------------------------------------------------------------------ stock valuation
def _stock_by_item():
    """{item id: [(part, qty in all stores)]} from Asset Management."""
    try:
        Part = django_apps.get_model('ZeoAssets', 'Part')
        Stock = django_apps.get_model('ZeoAssets', 'Stock')
    except LookupError:
        return {}
    qty = defaultdict(lambda: ZERO)
    for pid, q in Stock.objects.values_list('part_id', 'qty'):
        qty[pid] += q or ZERO
    out = defaultdict(list)
    for p in Part.objects.filter(item__isnull=False):
        out[p.item_id].append((p, qty.get(p.pk, ZERO)))
    return out


def _fifo_cost(part, qty):
    """FIFO: the stock left is the latest receipts – value it at their costs (the average cost for what the receipts do not cover)."""
    Move = django_apps.get_model('ZeoAssets', 'StockMove')
    left, value = qty, ZERO
    for m in Move.objects.filter(part=part, kind='receipt').order_by('-date', '-id'):
        if left <= 0:
            break
        take = min(left, m.qty or ZERO)
        value += take * (m.unit_cost or ZERO)
        left -= take
    if left > 0:
        value += left * (part.unit_cost or ZERO)
    return value


def valuation(save=True):
    """Rows per item with stock: method, quantity, unit cost, value, stock account. Saves the item's stock quantity and value."""
    Product = django_apps.get_model('ZeoMasters', 'Product')
    stock = _stock_by_item()
    rows, total = [], ZERO
    items = Product.objects.filter(pk__in=list(stock)).select_related('uom', 'category')
    for it in items:
        qty = sum((q for _, q in stock[it.pk]), ZERO)
        if it.valuation == 'standard' and it.standard_cost is not None:
            value = qty * it.standard_cost
        elif it.valuation == 'fifo':
            value = sum((_fifo_cost(p, q) for p, q in stock[it.pk] if q > 0), ZERO)
        else:
            value = sum((q * (p.unit_cost or ZERO) for p, q in stock[it.pk]), ZERO)
        value = value.quantize(Decimal('0.01'))
        unit = (value / qty).quantize(Decimal('0.0001')) if qty else ZERO
        rows.append({'id': it.pk, 'number': it.number, 'code': it.code, 'name': it.name, 'kind': it.get_kind_display(),
                     'category': it.category.name if it.category_id else '', 'uom': it.uom.code if it.uom_id else '',
                     'method': it.get_valuation_display(), 'qty': qty, 'unit_cost': unit, 'value': value,
                     'stock_account': it.stock_account, 'standard_cost': it.standard_cost})
        total += value
        if save and (it.stock_qty != qty or it.stock_value != value):
            Product.objects.filter(pk=it.pk).update(stock_qty=qty, stock_value=value)
    if save:
        Product.objects.exclude(pk__in=list(stock)).exclude(stock_qty=0, stock_value=0).update(stock_qty=0, stock_value=0)
    rows.sort(key=lambda r: (-r['value'], r['name']))
    by_account = defaultdict(lambda: ZERO)
    for r in rows:
        by_account[r['stock_account'] or '(no account)'] += r['value']
    return {'rows': rows, 'total': total, 'by_account': [{'account': k, 'value': v} for k, v in sorted(by_account.items())]}
