"""ZEO Purchase (v1.78.0) – prices, approvals, RFQ comparison, receipts through Inventory, bills through Accounting with the three-way
match, supplier returns, supplier performance."""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Bill, Order, OrderLine, PurchaseSettings, Quote, Receipt, Request, Return, Rfq, SupplierPrice

log = logging.getLogger(__name__)
D0 = Decimal('0')


class PurchaseError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def must(cond, message, field=None):
    if not cond:
        raise PurchaseError(message, field)


def n(x):
    """a quantity or price as people write it: 70, 45.8 (not 70.0000)"""
    return f'{Decimal(x or 0).normalize():f}'


def r2(x):
    return Decimal(x or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def settings():
    return PurchaseSettings.get()


def app():
    from .app import app as a
    return a


def call(label, name, **kw):
    from zeo.appkit.engine import REGISTRY, AppError
    a = REGISTRY.get(label)
    if a is None or name not in a.hooks:
        return None
    try:
        return a.hooks[name](**kw)
    except AppError as e:
        raise PurchaseError(e.message, e.field)
    except Exception as e:   # the other app's own error – its message on the screen
        if hasattr(e, 'message'):
            raise PurchaseError(e.message, getattr(e, 'field', None))
        raise


def active(key, label):
    from zeo.appkit.engine import REGISTRY
    try:
        from ZeoApps.services import is_active
        return label in REGISTRY and is_active(key)
    except Exception:   # noqa: BLE001
        return False


def ledger_on():
    return active('accounting', 'ZeoLedger')


def inventory_on():
    return active('inventory', 'ZeoInventory')


def grni():
    """the goods-received-not-invoiced account of Inventory: a stock item's bill clears what the receipt put there"""
    try:
        from ZeoInventory import services as IS
        return str(IS.account('grni') or '2150')
    except Exception:   # noqa: BLE001
        return '2150'


def stock_line(ln):
    return bool(ln.product_id and ln.product.track_stock and not ln.is_service and inventory_on())


# ------------------------------------------------------------------ prices
def best_price(supplier, product, qty, on=None):
    """the supplier's price list: the lowest price valid at the date for the quantity; else the item's last purchase price / cost"""
    on = on or timezone.localdate()
    sp = SupplierPrice.objects.filter(is_active=True, product=product, min_qty__lte=qty).filter(Q(date_from__isnull=True) | Q(date_from__lte=on)).filter(
        Q(date_to__isnull=True) | Q(date_to__gte=on))
    mine = sp.filter(supplier=supplier).order_by('price').first() if supplier is not None else None
    if mine is not None:
        return mine.price, mine
    return Decimal(product.purchase_price or product.cost_price or 0), None


def cheapest_supplier(product, qty, on=None):
    on = on or timezone.localdate()
    return SupplierPrice.objects.filter(is_active=True, product=product, min_qty__lte=qty).filter(Q(date_from__isnull=True) | Q(date_from__lte=on)).filter(
        Q(date_to__isnull=True) | Q(date_to__gte=on)).order_by('price', 'lead_days').first()


def vat_rate(code):
    if not code:
        return D0
    try:
        from ZeoLedger.models import Tax
        t = Tax.objects.filter(code=code).first()
        if t is not None:
            return Decimal(t.rate) if t.treatment in ('SR', 'BL') else D0
    except Exception:   # noqa: BLE001
        pass
    return Decimal(5) if code in ('SR', 'SR-P') else D0


def price_line(ln):
    o = ln.order
    p = ln.product
    if p is not None:
        if not ln.description:
            ln.description = p.name
        ln.is_service = p.kind in ('service', 'non_stock') or not p.track_stock
        if not ln.price:
            ln.price, _ = best_price(o.supplier, p, ln.qty, o.date)
    if not ln.tax_code:
        ln.tax_code = 'SR-P'
    ln.net = r2(Decimal(ln.qty) * Decimal(ln.price) * (1 - Decimal(ln.discount or 0) / 100))
    ln.vat = r2(ln.net * vat_rate(ln.tax_code) / 100)
    return ln


def totals(o):
    net = vat = D0
    for ln in o.lines.all():
        net += ln.net
        vat += ln.vat
    o.net, o.vat, o.total = r2(net), r2(vat), r2(net + vat)
    o.save(update_fields=['net', 'vat', 'total'])
    return o


# ------------------------------------------------------------------ requests and RFQs
def request_to_rfq(req, suppliers=(), user=None):
    must(req.status == 'approved', 'Approve the request first.')
    rfq = Rfq.objects.create(number=app().next_number('rfq', 'RFQ'), date=timezone.localdate(), request=req, lines=[{'product': x['product'], 'qty': x['qty']} for x in req.lines or []],
                             reply_by=timezone.localdate() + timedelta(days=7))
    if suppliers:
        rfq.suppliers.set(suppliers)
    else:   # the suppliers who have a price for the items
        from ZeoMasters.models import Product
        ids = set(SupplierPrice.objects.filter(product_id__in=[x['product'] for x in rfq.lines]).values_list('supplier_id', flat=True))
        ids |= {p.preferred_supplier_id for p in Product.objects.filter(pk__in=[x['product'] for x in rfq.lines]) if p.preferred_supplier_id}
        rfq.suppliers.set(list(ids))
    return rfq


def compare(rfq):
    """per line: each supplier's price and the best; per supplier: total, lead time"""
    from ZeoMasters.models import Product
    names = {p.pk: str(p) for p in Product.objects.filter(pk__in=[x['product'] for x in rfq.lines or []])}
    rows = []
    for i, x in enumerate(rfq.lines or []):
        row = {'item': names.get(int(x['product']), x['product']), 'qty': float(x['qty'])}
        best = None
        for q in rfq.quotes.select_related('supplier'):
            prices = q.prices or []
            pr = Decimal(str(prices[i]['price'])) if i < len(prices) and prices[i].get('price') not in (None, '') else None
            row[str(q.supplier)] = float(pr) if pr is not None else None
            if pr is not None and (best is None or pr < best[0]):
                best = (pr, str(q.supplier))
        row['best'] = best[1] if best else ''
        rows.append(row)
    totals_ = [{'supplier': str(q.supplier), 'total': float(q.total), 'lead_days': q.lead_days, 'valid_until': q.valid_until} for q in rfq.quotes.select_related('supplier')]
    return rows, sorted(totals_, key=lambda t: t['total'])


def quote_total(q):
    tot = D0
    for i, x in enumerate(q.rfq.lines or []):
        prices = q.prices or []
        if i < len(prices) and prices[i].get('price') not in (None, ''):
            tot += Decimal(str(prices[i]['price'])) * Decimal(str(x['qty']))
    q.total = r2(tot)
    q.save(update_fields=['total'])
    return q


@transaction.atomic
def award(rfq, quote, user=None):
    """the purchase order to the chosen supplier at the quoted prices; the supplier's price list remembers them"""
    must(rfq.status in ('sent', 'compared', 'draft'), 'This RFQ is closed.')
    from ZeoMasters.models import Product
    o = Order.objects.create(supplier=quote.supplier, date=timezone.localdate(), supplier_ref=quote.reference, rfq=rfq, request=rfq.request, currency=quote.currency,
                             expected_date=timezone.localdate() + timedelta(days=quote.lead_days or 0), warehouse_id=getattr(rfq.request, 'warehouse_id', None) or settings().default_warehouse_id,
                             created_by=user)
    for i, x in enumerate(rfq.lines or []):
        price = Decimal(str((quote.prices or [])[i]['price'])) if i < len(quote.prices or []) else D0
        p = Product.objects.get(pk=int(x['product']))
        ln = OrderLine(order=o, product=p, qty=Decimal(str(x['qty'])), price=price)
        price_line(ln)
        ln.save()
        SupplierPrice.objects.update_or_create(supplier=quote.supplier, product=p, min_qty=0, defaults={'price': price, 'lead_days': quote.lead_days, 'currency': quote.currency})
    totals(o)
    rfq.status, rfq.awarded_to = 'awarded', quote.supplier
    rfq.save(update_fields=['status', 'awarded_to'])
    if rfq.request_id:
        Request.objects.filter(pk=rfq.request_id).update(status='ordered')
    return o


# ------------------------------------------------------------------ approval
def levels_needed(o):
    st = settings()
    out = []
    if st.approve_amount is not None and o.total > st.approve_amount:
        out.append('pur_manager')
    if st.approve_amount_2 is not None and o.total > st.approve_amount_2:
        out.append('finance')
    return out


@transaction.atomic
def submit(o, user=None):
    must(o.status == 'draft', 'Only a draft is submitted.')
    must(o.lines.exists(), 'Add the lines.')
    totals(o)
    st = settings()
    if st.rfq_min_quotes and st.approve_amount is not None and o.total > st.approve_amount:
        n = o.rfq.quotes.count() if o.rfq_id else 0
        must(n >= st.rfq_min_quotes, f'Orders above {st.approve_amount:,.2f} need {st.rfq_min_quotes} supplier quotes (request for quotation) – {n} received.')
    if not o.number:
        o.number = app().next_number('order', 'PO')
    need = levels_needed(o)
    o.status = 'approval' if need else 'approved'
    o.approval_note = ('Needs: ' + ', '.join('the purchase manager' if x == 'pur_manager' else 'the finance manager' for x in need)) if need else ''
    o.save()
    return o


@transaction.atomic
def approve(o, user, roles):
    """roles: what the user is ({'pur_manager', 'finance'}); each level once, in order"""
    must(o.status == 'approval', 'Nothing to approve.')
    done = {a['level'] for a in o.approvals or []}
    left = [x for x in levels_needed(o) if x not in done]
    must(left, 'Already approved.')
    lvl = left[0]
    must(lvl in roles, f'This order waits for {"the purchase manager" if lvl == "pur_manager" else "the finance manager"}.')
    o.approvals = list(o.approvals or []) + [{'level': lvl, 'by': getattr(user, 'username', ''), 'at': timezone.now().isoformat()}]
    if len(left) == 1:
        o.status, o.approval_note = 'approved', ''
    else:
        o.approval_note = 'Needs: the finance manager'
    o.save()
    return o


# ------------------------------------------------------------------ receipts
def policy(o):
    return o.bill_policy or settings().bill_policy


@transaction.atomic
def receive(o, qtys=None, on=None, user=None, delivery_note=''):
    must(o.status in ('approved', 'sent', 'part_received'), 'Only an approved order is received.')
    on = on or timezone.localdate()
    st = settings()
    lines, picked = [], []
    for ln in o.lines.select_related('product'):
        left = Decimal(ln.qty) - ln.received
        q = Decimal(str((qtys or {}).get(str(ln.pk), left))) if qtys else left
        if q <= 0:
            continue
        limit = Decimal(ln.qty) * (1 + Decimal(st.over_receipt or 0) / 100) - ln.received
        must(q <= limit + Decimal('0.0001'), f'{ln.description}: at most {n(limit)} more can be received ({n(st.over_receipt)} % over the order).')
        picked.append((ln, q))
        if ln.product_id and ln.product.track_stock and not ln.is_service:
            price = Decimal(ln.price) * (1 - Decimal(ln.discount or 0) / 100)
            lines.append({'product_id': ln.product_id, 'qty': q, 'unit_cost': price})
    must(picked, 'Nothing left to receive.')
    doc = None
    if lines and inventory_on():
        wh = o.warehouse_id or st.default_warehouse_id
        must(wh, 'Choose the warehouse of the order (or the default warehouse in the Purchase set-up).', 'warehouse')
        doc = call('ZeoInventory', 'post_doc', kind='receipt', warehouse_id=wh, lines=lines, date=on, partner_id=o.supplier_id,
                   reference=delivery_note or o.supplier_ref or o.number, origin_app='purchase', origin_ref=o.number, user=user)
    rc = Receipt.objects.create(order=o, date=on, stock_doc_id=(doc or {}).get('id'), stock_number=(doc or {}).get('number', ''), delivery_note=delivery_note,
                                lines=[{'line': ln.pk, 'qty': str(q)} for ln, q in picked], value=Decimal(str((doc or {}).get('value') or 0)),
                                on_time=not o.expected_date or on <= o.expected_date, created_by=user)
    for ln, q in picked:
        ln.received += q
        ln.save(update_fields=['received'])
    _status(o)
    return rc


def _status(o):
    ls = list(o.lines.all())
    if o.status in ('cancelled', 'closed', 'draft', 'approval'):
        return o
    all_rec = all(ln.received >= ln.qty for ln in ls)
    any_rec = any(ln.received > 0 for ln in ls)
    all_bill = all(ln.billed_qty >= ln.qty for ln in ls)
    o.status = 'billed' if all_bill and all_rec else ('received' if all_rec else ('part_received' if any_rec else o.status))
    o.save(update_fields=['status'])
    return o


# ------------------------------------------------------------------ bills: the three-way match
def match(o, lines):
    """lines [{line, qty, price}] of the supplier's invoice against the order (price) and the receipts (quantity); tolerances from the set-up"""
    st = settings()
    out, problems = [], []
    pol = policy(o)
    for x in lines:
        ln = o.lines.get(pk=int(x['line']))
        q = Decimal(str(x['qty']))
        pr = Decimal(str(x.get('price') if x.get('price') not in (None, '') else ln.price))
        can = (Decimal(ln.qty) if (pol == 'ordered' or ln.is_service) else ln.received) - ln.billed_qty - (ln.returned if pol != 'ordered' else 0)
        qlim = can * (1 + Decimal(st.qty_tolerance or 0) / 100)
        plim = Decimal(ln.price) * (1 + Decimal(st.price_tolerance or 0) / 100)
        row = {'line': ln.pk, 'item': ln.description, 'ordered': float(ln.qty), 'received': float(ln.received), 'billed_before': float(ln.billed_qty),
               'bill_qty': float(q), 'order_price': float(ln.price), 'bill_price': float(pr), 'qty_ok': q <= qlim + Decimal('0.0001'), 'price_ok': pr <= plim + Decimal('0.000001')}
        if not row['qty_ok']:
            problems.append(f'{ln.description}: {n(q)} billed, {n(can)} received and not billed yet' + (f' (+{n(st.qty_tolerance)} % tolerance)' if st.qty_tolerance else '') + '.')
        if not row['price_ok']:
            problems.append(f'{ln.description}: price {pr:,.2f} above the order {Decimal(ln.price):,.2f}' + (f' (+{n(st.price_tolerance)} % tolerance)' if st.price_tolerance else '') + '.')
        out.append(row)
    return out, problems


@transaction.atomic
def bill(o, supplier_ref, lines=None, on=None, user=None, force=False):
    """the supplier's bill in Accounting from the order: by default everything received (or ordered) and not billed, at the order price"""
    must(o.status in ('approved', 'sent', 'part_received', 'received'), 'Only an approved order is billed.')
    must(ledger_on(), 'Switch on the Accounting app to enter supplier bills from Purchase.')
    must(supplier_ref, "Enter the supplier's invoice number.", 'supplier_ref')
    on = on or timezone.localdate()
    pol = policy(o)
    if not lines:
        lines = []
        for ln in o.lines.all():
            can = (Decimal(ln.qty) if (pol == 'ordered' or ln.is_service) else ln.received) - ln.billed_qty - (ln.returned if pol != 'ordered' else 0)
            if can > 0:
                lines.append({'line': ln.pk, 'qty': str(can), 'price': str(ln.price)})
    must(lines, 'Nothing to bill – receive first (the order is billed by what was received).' if pol == 'received' else 'Everything is billed.')
    rows, problems = match(o, lines)
    must(not problems or force, 'The bill does not match the order and the receipts: ' + ' '.join(problems))
    dl = []
    for x in lines:
        ln = o.lines.get(pk=int(x['line']))
        dl.append({'product_id': ln.product_id, 'description': ln.description, 'qty': x['qty'], 'price': x.get('price') or ln.price, 'discount': ln.discount,
                   'tax_code': ln.tax_code, 'account_code': ln.account_code or (grni() if stock_line(ln) else '')})
    res = call('ZeoLedger', 'make_doc', kind='bill', partner_id=o.supplier_id, date=on, currency=o.currency, reference=o.number, supplier_ref=supplier_ref,
               branch_id=o.branch_id, source='purchase', source_ref=o.number, user=user, lines=dl)
    b = Bill.objects.create(order=o, kind='bill', date=on, supplier_ref=supplier_ref, doc_id=res['id'], doc_number=res['number'], total=Decimal(str(res['total'])),
                            lines=lines, match=rows)
    for x in lines:
        ln = o.lines.get(pk=int(x['line']))
        ln.billed_qty += Decimal(str(x['qty']))
        ln.save(update_fields=['billed_qty'])
    o.billed += b.total
    o.save(update_fields=['billed'])
    _status(o)
    return b


@transaction.atomic
def do_return(rt, user=None):
    must(rt.status == 'draft', 'This return is done already.')
    o = rt.order
    stock, debit, picked = [], [], []
    for x in rt.lines or []:
        ln = o.lines.filter(pk=int(x['line'])).select_related('product').first()
        q = Decimal(str(x.get('qty') or 0))
        if ln is None or q <= 0:
            continue
        must(q <= ln.received - ln.returned, f'{ln.description}: at most {n(ln.received - ln.returned)} can be returned.')
        picked.append((ln, q))
        if ln.product_id and ln.product.track_stock and not ln.is_service:
            stock.append({'product_id': ln.product_id, 'qty': q})
        debit.append({'product_id': ln.product_id, 'description': f'Return: {ln.description}', 'qty': q, 'price': ln.price, 'discount': ln.discount, 'tax_code': ln.tax_code,
                      'account_code': grni() if stock_line(ln) else ''})
    must(picked, 'Enter the quantities returned.')
    if stock and inventory_on():
        wh = o.warehouse_id or settings().default_warehouse_id
        doc = call('ZeoInventory', 'post_doc', kind='return_out', warehouse_id=wh, lines=stock, date=rt.date, partner_id=o.supplier_id, reference=o.number,
                   origin_app='purchase', origin_ref=rt.number or o.number, user=user)
        rt.stock_number = (doc or {}).get('number', '')
    billed_any = any(ln.billed_qty > 0 for ln, _ in picked)
    if rt.debit_note and ledger_on() and billed_any:
        orig = o.bills.filter(kind='bill').order_by('-date', '-id').first()
        res = call('ZeoLedger', 'make_doc', kind='debit', partner_id=o.supplier_id, date=rt.date, currency=o.currency, reference=o.number,
                   supplier_ref=f'RET-{rt.pk}', source='purchase', source_ref=rt.number or o.number, original_id=orig.doc_id if orig else None, user=user, lines=debit)
        rt.debit_number, rt.total = res['number'], Decimal(str(res['total']))
        Bill.objects.create(order=o, kind='debit', date=rt.date, supplier_ref=f'RET-{rt.pk}', doc_id=res['id'], doc_number=res['number'], total=-Decimal(str(res['total'])))
        o.billed -= Decimal(str(res['total']))
        o.save(update_fields=['billed'])
    for ln, q in picked:
        ln.returned += q
        if rt.debit_note and billed_any:
            ln.billed_qty = max(D0, ln.billed_qty - q)
        ln.save(update_fields=['returned', 'billed_qty'])
    rt.status = 'done'
    if not rt.number:
        rt.number = app().next_number('return', 'PRT')
    rt.save()
    return rt


# ------------------------------------------------------------------ from Inventory
@transaction.atomic
def from_reorder(user=None):
    """Inventory's reorder list (items to buy) → one approved purchase request"""
    must(inventory_on(), 'Switch on Inventory to use its reorder list.')
    from ZeoInventory import services as IS
    rows = [x for x in IS.suggestions() if x.get('source') != 'transfer' and x.get('order_qty')]
    must(rows, 'Nothing is below its reorder point.')
    req = Request.objects.create(number=app().next_number('request', 'PR'), date=timezone.localdate(), requested_by=user, source='reorder', status='approved',
                                 lines=[{'product': x.get('item_id'), 'qty': str(x['order_qty']), 'note': f'Reorder ({x.get("warehouse", "")})'} for x in rows])
    return req


@transaction.atomic
def order_from_request(req, user=None):
    """an approved request → purchase orders grouped by the cheapest supplier of each item (its price list) or the item's preferred supplier"""
    must(req.status == 'approved', 'Approve the request first.')
    from ZeoMasters.models import Product
    by_sup = {}
    for x in req.lines or []:
        p = Product.objects.get(pk=int(x['product']))
        q = Decimal(str(x['qty']))
        sp = cheapest_supplier(p, q)
        sup = sp.supplier if sp else p.preferred_supplier
        must(sup is not None, f'{p}: no supplier – add its price in Purchase › Supplier prices or the preferred supplier on the item.')
        by_sup.setdefault(sup.pk, (sup, []))[1].append((p, q))
    made = []
    for sup, items in by_sup.values():
        o = Order.objects.create(supplier=sup, date=timezone.localdate(), request=req, warehouse=req.warehouse or settings().default_warehouse, branch=req.branch, created_by=user)
        for p, q in items:
            ln = OrderLine(order=o, product=p, qty=q)
            price_line(ln)
            ln.save()
        totals(o)
        made.append(o)
    req.status = 'ordered'
    req.save(update_fields=['status'])
    return made


def supplier_performance(date_from, date_to):
    from collections import defaultdict
    agg = defaultdict(lambda: {'orders': set(), 'receipts': 0, 'on_time': 0, 'days': [], 'spend': D0, 'returned': D0})
    for r in Receipt.objects.filter(date__gte=date_from, date__lte=date_to).select_related('order__supplier'):
        a = agg[r.order.supplier_id]
        a['name'] = str(r.order.supplier)
        a['orders'].add(r.order_id)
        a['receipts'] += 1
        a['on_time'] += 1 if r.on_time else 0
        a['days'].append((r.date - r.order.date).days)
    for o in Order.objects.filter(date__gte=date_from, date__lte=date_to).exclude(status__in=('draft', 'cancelled')).select_related('supplier'):
        a = agg[o.supplier_id]
        a['name'] = str(o.supplier)
        a['spend'] += o.net
    for rt in Return.objects.filter(date__gte=date_from, date__lte=date_to, status='done').select_related('order__supplier'):
        agg[rt.order.supplier_id]['returned'] += rt.total
    return [{'supplier': v.get('name', ''), 'spend': float(v['spend']), 'receipts': v['receipts'], 'on_time_pct': round(v['on_time'] * 100 / v['receipts'], 1) if v['receipts'] else None,
             'avg_lead_days': round(sum(v['days']) / len(v['days']), 1) if v['days'] else None, 'returned': float(v['returned'])} for v in agg.values()]


def daily():
    return {}
