"""v1.79.6 the supplier price list (Purchase › Supplier prices) kept up to date from documents: a supplier's quotation or a
whole price list (Excel, PDF, CSV, e-mail). A changed price closes the old row (valid to the day before) and starts a new one,
so the price history stays; an unchanged price only refreshes the supplier's code, lead time and validity."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone


def feature_on():
    try:
        from zeo.appkit import features as F
        from ZeoPurchase.app import app
        return F.on(app, 'supplier_prices')
    except Exception:   # noqa: BLE001
        return True


def upsert(supplier, product, price, *, min_qty=0, code='', currency='', lead_days=None, valid_from=None, valid_to=None):
    """('new' | 'changed' | 'same', old price or None)"""
    from ZeoPurchase.models import SupplierPrice
    from ZeoPurchase.services import settings as psettings
    today = timezone.localdate()
    start = valid_from or today
    base = (psettings().currency or 'AED').upper() if hasattr(psettings(), 'currency') else 'AED'
    cur = (currency or '').upper()
    cur = '' if cur in ('', base) else cur
    price, min_qty = Decimal(str(price)), Decimal(str(min_qty or 0))
    if min_qty <= 1:   # MOQ 1 = any quantity, the same row as "from 0"
        min_qty = Decimal(0)
    qs = SupplierPrice.objects.filter(supplier=supplier, product=product, min_qty=min_qty, is_active=True).filter(
        Q(date_to__isnull=True) | Q(date_to__gte=start)).filter(Q(currency=cur) | Q(currency__iexact=base if not cur else cur)).order_by('-date_from', '-id')
    cur_row = qs.first()
    extra = {k: v for k, v in (('supplier_code', (code or '')[:60]), ('lead_days', lead_days)) if v not in (None, '')}
    if cur_row is not None and abs(Decimal(cur_row.price) - price) < Decimal('0.000001'):
        for k, v in extra.items():
            setattr(cur_row, k, v)
        if valid_to:
            cur_row.date_to = valid_to
        cur_row.save()
        return 'same', None
    if cur_row is not None:
        old = Decimal(cur_row.price)
        if cur_row.date_from and cur_row.date_from >= start:   # it starts on the same day: corrected in place
            cur_row.price = price
            for k, v in extra.items():
                setattr(cur_row, k, v)
            cur_row.date_to = valid_to
            cur_row.save()
            return 'changed', old
        cur_row.date_to = start - timedelta(days=1)
        cur_row.save(update_fields=['date_to'])
        SupplierPrice.objects.create(supplier=supplier, product=product, min_qty=min_qty, price=price, currency=cur, date_from=start, date_to=valid_to,
                                     supplier_code=extra.get('supplier_code') or cur_row.supplier_code,
                                     lead_days=extra.get('lead_days', cur_row.lead_days) or 0)
        return 'changed', old
    SupplierPrice.objects.create(supplier=supplier, product=product, min_qty=min_qty, price=price, currency=cur, date_from=start, date_to=valid_to,
                                 supplier_code=extra.get('supplier_code', ''), lead_days=extra.get('lead_days') or 0)
    return 'new', None


def save_lines(supplier, lines, *, currency='', lead_days=None, valid_from=None, valid_to=None):
    """lines: [{product, price, discount, min_qty, code}] → counts and the changes"""
    from ZeoMasters.models import Product
    out = {'new': 0, 'changed': 0, 'same': 0, 'changes': []}
    for l in lines:
        if not l.get('product') or l.get('price') is None:
            continue
        p = Product.objects.get(pk=l['product'])
        net = Decimal(str(l['price'])) * (1 - Decimal(str(l.get('discount') or 0)) / 100)
        what, old = upsert(supplier, p, net.quantize(Decimal('0.000001')), min_qty=l.get('min_qty') or 0, code=l.get('code') or '', currency=currency,
                           lead_days=lead_days, valid_from=valid_from, valid_to=valid_to)
        out[what] += 1
        if what == 'changed':
            out['changes'].append({'item': str(p), 'old': float(old), 'new': float(net), 'pct': round((float(net) - float(old)) / float(old) * 100, 1) if old else None})
    return out


def summary(r):
    s = f"price list: {r['new']} new, {r['changed']} changed, {r['same']} unchanged"
    if r['changes']:
        s += ' (' + '; '.join(f"{c['item'][:40]} {c['old']:,.2f} → {c['new']:,.2f}" + (f" {c['pct']:+.1f} %" if c['pct'] is not None else '') for c in r['changes'][:5]) + ')'
    return s
