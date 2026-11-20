"""ZEO Sales (v1.77.0) – pricing, credit control, confirmation, deliveries through Inventory, invoices through Accounting, returns,
commissions."""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import Commission, Delivery, DiscountRule, Invoice, Order, OrderLine, Return, SalesSettings

log = logging.getLogger(__name__)
D0 = Decimal('0')


class SalesError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def must(cond, message, field=None):
    if not cond:
        raise SalesError(message, field)


def r2(x):
    return Decimal(x or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def settings():
    return SalesSettings.get()


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
        raise SalesError(e.message, e.field)
    except Exception as e:   # the other app's own error (InvError, LedgerError …) – its message on the screen
        if hasattr(e, 'message'):
            raise SalesError(e.message, getattr(e, 'field', None))
        raise


def ledger_on():
    from zeo.appkit.engine import REGISTRY
    try:
        from ZeoApps.services import is_active
        return 'ZeoLedger' in REGISTRY and is_active('accounting')
    except Exception:   # noqa: BLE001
        return False


def inventory_on():
    from zeo.appkit.engine import REGISTRY
    try:
        from ZeoApps.services import is_active
        return 'ZeoInventory' in REGISTRY and is_active('inventory')
    except Exception:   # noqa: BLE001
        return False


# ------------------------------------------------------------------ prices
def list_price(order, product, qty):
    """the price book of the order (or of the customer, or the default one), else the item's list price"""
    pb = order.price_book or getattr(order.partner, 'price_book', None)
    if pb is None:
        from ZeoMasters.models import PriceBook
        pb = PriceBook.objects.filter(is_default=True, is_active=True).first()
    if pb is not None and pb.is_active and (not pb.valid_from or pb.valid_from <= order.date) and (not pb.valid_to or pb.valid_to >= order.date):
        try:
            p = pb.price_of(product, qty)
            if p is not None:
                return Decimal(str(p))
        except Exception:   # noqa: BLE001
            pass
    return Decimal(product.sale_price or 0)


def best_rule(order, product, qty):
    """the rule with the lowest priority number that applies (item, category, every item; customer; quantity; dates)"""
    on = order.date
    qs = DiscountRule.objects.filter(is_active=True, min_qty__lte=qty).filter(Q(date_from__isnull=True) | Q(date_from__lte=on)).filter(
        Q(date_to__isnull=True) | Q(date_to__gte=on)).filter(Q(partner__isnull=True) | Q(partner=order.partner))
    qs = qs.filter(Q(product=product) | Q(product__isnull=True, category=product.category_id) | Q(product__isnull=True, category__isnull=True))
    return qs.order_by('priority', '-min_qty').first()


def price_line(ln):
    """fills the price, discount and VAT of a line (the user's own price or discount is kept)"""
    o = ln.order
    p = ln.product
    if p is not None:
        if not ln.description:
            ln.description = p.name
        ln.is_service = p.kind in ('service', 'non_stock') or not p.track_stock
        if not ln.price:
            ln.price = list_price(o, p, ln.qty)
        if not ln.discount and not ln.rule_id:
            r = best_rule(o, p, ln.qty)
            if r is not None:
                ln.rule = r
                if r.kind == 'percent':
                    ln.discount = r.value
                elif r.kind == 'price':
                    ln.price = r.value
        if not ln.tax_code:
            ln.tax_code = default_tax(p)
        if not ln.unit_cost:
            uc = call('ZeoInventory', 'unit_cost', product_id=p.pk, warehouse_id=o.warehouse_id) if inventory_on() and p.track_stock else None
            ln.unit_cost = Decimal(str(uc)) if uc is not None else Decimal(p.cost_price or 0)
    ln.net = r2(Decimal(ln.qty) * Decimal(ln.price) * (1 - Decimal(ln.discount or 0) / 100))
    ln.vat = r2(ln.net * vat_rate(ln.tax_code) / 100)
    return ln


def default_tax(product):
    if product.tax_id:
        rate = Decimal(product.tax.rate or 0)
        return 'SR' if rate > 0 else ('ZR' if getattr(product, 'tax_category', '') == 'Z' else ('EX' if getattr(product, 'tax_category', '') == 'E' else 'SR'))
    return 'SR'


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
    return Decimal(5) if code == 'SR' else D0


def totals(o):
    net = vat = cost = D0
    for ln in o.lines.all():
        net += ln.net
        vat += ln.vat
        cost += r2(Decimal(ln.qty) * Decimal(ln.unit_cost or 0))
    o.net, o.vat, o.total, o.cost = r2(net), r2(vat), r2(net + vat), r2(cost)
    o.margin = r2(o.net - o.cost)
    o.save(update_fields=['net', 'vat', 'total', 'cost', 'margin'])
    return o


# ------------------------------------------------------------------ credit control
def credit_problems(o):
    st = settings()
    p = o.partner
    out = []
    if getattr(p, 'status', 'active') == 'blocked':
        out.append(f'{p} is blocked: {p.blocked_reason or "see the partner"}.')
    if getattr(p, 'credit_hold', False):
        out.append(f'{p} is on credit hold: {p.credit_hold_reason or "see the partner"}.')
    if st.credit_control == 'off':
        return out
    bal = call('ZeoLedger', 'partner_balance', partner_id=p.pk, sale=True) if ledger_on() else None
    open_ = Decimal(str(bal['open'])) if bal else Decimal(p.balance_receivable or 0)
    others = Order.objects.filter(partner=p, status__in=('confirmed', 'part_delivered', 'delivered', 'hold')).exclude(pk=o.pk)
    not_invoiced = sum((x.total - x.invoiced for x in others), D0)
    exposure = open_ + not_invoiced + o.total
    if p.credit_limit is not None and p.credit_limit > 0 and exposure > p.credit_limit:
        out.append(f'Over the credit limit: {exposure:,.2f} owed and ordered against a limit of {p.credit_limit:,.2f}.')
    if bal and bal['oldest_overdue_days'] > st.overdue_days:
        out.append(f'An invoice is {bal["oldest_overdue_days"]} days overdue ({bal["overdue"]:,.2f} overdue).')
    return out


def needs_approval(o):
    st = settings()
    why = []
    if st.approve_discount is not None and o.lines.filter(discount__gt=st.approve_discount).exists():
        why.append(f'a discount above {st.approve_discount:g} %')
    if st.approve_amount is not None and o.total > st.approve_amount:
        why.append(f'more than {st.approve_amount:,.2f}')
    for ln in o.lines.select_related('product'):
        mp = getattr(ln.product, 'min_price', None) if ln.product_id else None
        if mp and ln.qty and (ln.net / ln.qty) < mp:
            why.append(f'{ln.product} below its minimum price {mp:,.2f}')
    return why


# ------------------------------------------------------------------ the order
@transaction.atomic
def confirm(o, user=None, manager=False, release=False):
    must(o.status in ('quote', 'sent', 'approval', 'hold'), 'Only a quotation can be confirmed.')
    must(o.lines.exists(), 'Add the lines.')
    totals(o)
    why = needs_approval(o)
    if why and not manager and o.status != 'approval':
        o.status = 'approval'
        o.hold_reason = 'Needs the sales manager: ' + ', '.join(why)
        o.save(update_fields=['status', 'hold_reason'])
        return o
    if why and not manager:
        raise SalesError('Waiting for the sales manager: ' + ', '.join(why) + '.')
    probs = credit_problems(o)
    if probs and settings().credit_control == 'block' and not release:   # only the sales manager's "release" passes a credit hold
        o.status, o.hold_reason = 'hold', ' '.join(probs)
        o.save(update_fields=['status', 'hold_reason'])
        return o
    if not o.number:
        o.number = app().next_number('order', 'SO')
    o.status, o.hold_reason, o.confirmed_at = 'confirmed', ' '.join(probs) if probs else '', timezone.now()
    if manager and why:
        o.approved_by = user
    o.save()
    if o.crm_order_id is None and o.crm_quote_id:
        try:
            from ZeoCRM.models import Quotation
            Quotation.objects.filter(pk=o.crm_quote_id).update(status='accepted')
        except Exception:   # noqa: BLE001
            pass
    return o


def policy(o):
    return o.invoice_policy or settings().invoice_policy


@transaction.atomic
def deliver(o, qtys=None, on=None, user=None):
    """an Inventory delivery for the lines (all still to deliver, or {line id: qty}); services are delivered without stock"""
    must(o.status in ('confirmed', 'part_delivered'), 'Only a confirmed order is delivered.')
    on = on or timezone.localdate()
    lines = []
    picked = []
    for ln in o.lines.select_related('product'):
        left = Decimal(ln.qty) - ln.delivered
        q = Decimal(str((qtys or {}).get(str(ln.pk), left))) if qtys else left
        if q <= 0:
            continue
        must(q <= left, f'{ln.description}: only {left:g} left to deliver.')
        picked.append((ln, q))
        if ln.product_id and ln.product.track_stock and not ln.is_service:
            lines.append({'product_id': ln.product_id, 'qty': q})
    must(picked, 'Nothing left to deliver.')
    doc = None
    if lines and inventory_on():
        wh = o.warehouse_id or (settings().default_warehouse_id)
        must(wh, 'Choose the warehouse of the order (or the default warehouse in the Sales set-up).', 'warehouse')
        doc = call('ZeoInventory', 'post_doc', kind='delivery', warehouse_id=wh, lines=lines, date=on, partner_id=o.partner_id, reference=o.reference or o.number,
                   origin_app='sales', origin_ref=o.number, user=user)
    dv = Delivery.objects.create(order=o, date=on, stock_doc_id=(doc or {}).get('id'), stock_number=(doc or {}).get('number', ''),
                                 lines=[{'line': ln.pk, 'qty': str(q)} for ln, q in picked], value=Decimal(str((doc or {}).get('value') or 0)), created_by=user)
    for ln, q in picked:
        ln.delivered += q
        ln.save(update_fields=['delivered'])
    _status(o)
    return dv


def _status(o):
    ls = list(o.lines.all())
    all_del = all(ln.delivered >= ln.qty for ln in ls)
    any_del = any(ln.delivered > 0 for ln in ls)
    all_inv = all(ln.invoiced_qty >= ln.qty for ln in ls)
    if o.status in ('cancelled', 'closed', 'quote', 'sent', 'approval', 'hold', 'lost'):
        return o
    o.status = 'invoiced' if all_inv and all_del else ('delivered' if all_del else ('part_delivered' if any_del else 'confirmed'))
    o.save(update_fields=['status'])
    return o


@transaction.atomic
def invoice(o, on=None, user=None, down_percent=None):
    """the Accounting invoice: by the invoicing policy (delivered or ordered, less what was invoiced); a down payment as a % of the order"""
    must(o.status in ('confirmed', 'part_delivered', 'delivered'), 'Only a confirmed order is invoiced.')
    must(ledger_on(), 'Switch on the Accounting app to invoice from Sales.')
    on = on or timezone.localdate()
    cur = o.currency
    if down_percent:
        amt = r2(o.net * Decimal(str(down_percent)) / 100)
        must(amt > 0, 'The down payment is 0.')
        res = call('ZeoLedger', 'make_doc', kind='invoice', partner_id=o.partner_id, date=on, currency=cur, reference=o.reference or o.number, emirate=o.emirate,
                   branch_id=o.branch_id, source='sales', source_ref=o.number, user=user,
                   lines=[{'description': f'Down payment {down_percent:g} % – order {o.number}', 'qty': 1, 'price': amt, 'tax_code': 'SR', 'account_code': '2400'}])
        inv = Invoice.objects.create(order=o, kind='down', date=on, doc_id=res['id'], doc_number=res['number'], total=Decimal(str(res['total'])))
        o.down_payment += amt
        o.invoiced += inv.total
        o.save(update_fields=['down_payment', 'invoiced'])
        return inv
    pol = policy(o)
    lines, picked = [], []
    for ln in o.lines.select_related('product'):
        base = Decimal(ln.qty) if (pol == 'ordered' or ln.is_service) else ln.delivered
        q = base - ln.invoiced_qty - ln.returned if pol != 'ordered' else base - ln.invoiced_qty
        if q <= 0:
            continue
        picked.append((ln, q))
        lines.append({'product_id': ln.product_id, 'description': ln.description, 'qty': q, 'price': ln.price, 'discount': ln.discount, 'tax_code': ln.tax_code})
    must(lines, 'Nothing to invoice – deliver first (the order is invoiced by what was delivered).' if pol == 'delivered' else 'Everything is invoiced.')
    if o.down_payment:   # the down payment is deducted on the final invoice
        lines.append({'description': f'Less down payment – order {o.number}', 'qty': -1, 'price': o.down_payment, 'tax_code': 'SR', 'account_code': '2400'})
    res = call('ZeoLedger', 'make_doc', kind='invoice', partner_id=o.partner_id, date=on, currency=cur, reference=o.reference or o.number, emirate=o.emirate,
               branch_id=o.branch_id, source='sales', source_ref=o.number, user=user, lines=lines)
    inv = Invoice.objects.create(order=o, kind='invoice', date=on, doc_id=res['id'], doc_number=res['number'], total=Decimal(str(res['total'])),
                                 lines=[{'line': ln.pk, 'qty': str(q)} for ln, q in picked])
    for ln, q in picked:
        ln.invoiced_qty += q
        ln.save(update_fields=['invoiced_qty'])
    if o.down_payment:
        o.down_payment = D0
    o.invoiced += inv.total
    o.save(update_fields=['invoiced', 'down_payment'])
    _status(o)
    commission_for(o, inv)
    return inv


@transaction.atomic
def do_return(rt, user=None):
    """stock back (Inventory customer return) and a credit note (Accounting) for the quantities returned"""
    must(rt.status == 'draft', 'This return is done already.')
    o = rt.order
    stock, credit = [], []
    picked = []
    for x in rt.lines or []:
        ln = o.lines.filter(pk=int(x['line'])).select_related('product').first()
        q = Decimal(str(x.get('qty') or 0))
        if ln is None or q <= 0:
            continue
        must(q <= ln.delivered - ln.returned, f'{ln.description}: at most {ln.delivered - ln.returned:g} can be returned.')
        picked.append((ln, q))
        if rt.restock and ln.product_id and ln.product.track_stock:
            stock.append({'product_id': ln.product_id, 'qty': q, 'unit_cost': ln.unit_cost})
        credit.append({'product_id': ln.product_id, 'description': f'Return: {ln.description}', 'qty': q, 'price': ln.price, 'discount': ln.discount, 'tax_code': ln.tax_code})
    must(picked, 'Enter the quantities returned.')
    if stock and inventory_on():
        wh = o.warehouse_id or settings().default_warehouse_id
        doc = call('ZeoInventory', 'post_doc', kind='return_in', warehouse_id=wh, lines=stock, date=rt.date, partner_id=o.partner_id, reference=o.number,
                   origin_app='sales', origin_ref=rt.number or o.number, user=user)
        rt.stock_number = (doc or {}).get('number', '')
    if rt.refund and ledger_on():
        orig = o.invoices.filter(kind='invoice').order_by('-date', '-id').first()
        res = call('ZeoLedger', 'make_doc', kind='credit', partner_id=o.partner_id, date=rt.date, currency=o.currency, reference=o.number, emirate=o.emirate,
                   branch_id=o.branch_id, source='sales', source_ref=rt.number or o.number, original_id=orig.doc_id if orig else None, user=user, lines=credit)
        rt.credit_number, rt.total = res['number'], Decimal(str(res['total']))
        Invoice.objects.create(order=o, kind='credit', date=rt.date, doc_id=res['id'], doc_number=res['number'], total=-Decimal(str(res['total'])))
        o.invoiced -= Decimal(str(res['total']))
        o.save(update_fields=['invoiced'])
    for ln, q in picked:
        ln.returned += q
        if rt.refund:
            ln.invoiced_qty = max(D0, ln.invoiced_qty - q)
        ln.save(update_fields=['returned', 'invoiced_qty'])
    rt.status = 'done'
    if not rt.number:
        rt.number = app().next_number('return', 'SRT')
    rt.save()
    return rt


# ------------------------------------------------------------------ commissions
def commission_for(o, inv):
    if not o.salesperson_id:
        return None
    pct = Decimal(o.team.commission) if o.team_id else D0
    if not pct:
        return None
    base = r2(inv.total * o.net / o.total) if o.total else r2(inv.total)   # net of VAT
    st = settings()
    return Commission.objects.create(salesperson_id=o.salesperson_id, order=o, invoice=inv, date=inv.date, base=base, percent=pct, amount=r2(base * pct / 100),
                                     status='earned' if st.commission_on == 'invoiced' else 'waiting')


def update_commissions():
    """'paid' commissions are earned once the invoice is paid in Accounting"""
    n = 0
    for c in Commission.objects.filter(status='waiting').select_related('invoice'):
        s = call('ZeoLedger', 'doc_status', doc_id=c.invoice.doc_id) if c.invoice_id and ledger_on() else None
        if s and s['status'] == 'paid':
            c.status = 'earned'
            c.save(update_fields=['status'])
            n += 1
    return n


# ------------------------------------------------------------------ from CRM
@transaction.atomic
def from_crm(crm_order=None, crm_quote=None, user=None):
    """a CRM sales order (or accepted quotation) becomes a Sales order with its lines; the customer is the CRM account's business partner"""
    src = crm_order or crm_quote
    must(src is not None, 'Choose the CRM order or quotation.')
    acc = src.account
    partner = getattr(acc, 'partner', None) if acc else None
    must(partner is not None, f'{acc or "The CRM record"} has no business partner – open the account in CRM and link it.')
    exists = Order.objects.filter(crm_order=crm_order).first() if crm_order else Order.objects.filter(crm_quote=crm_quote).first()
    if exists:
        return exists
    o = Order.objects.create(partner=partner, date=timezone.localdate(), reference=src.reference or src.number, currency=src.currency if src.currency != 'AED' else '',
                             payment_term=src.payment_term, price_book=src.price_book, notes=src.notes, terms=src.terms, branch=src.branch,
                             crm_order=crm_order, crm_quote=crm_quote, status='quote', quote_number=app().next_number('quote', 'SQ'), created_by=user)
    for x in src.lines.all():
        ln = OrderLine(order=o, product=x.product, description=x.description, qty=x.qty, price=x.unit_price, discount=x.discount_pct,
                       tax_code='SR' if Decimal(x.tax_rate or 0) > 0 else 'ZR')
        price_line(ln)
        ln.save()
    totals(o)
    return o


def daily():
    out = {'commissions': 0, 'expired': 0}
    try:
        out['commissions'] = update_commissions()
    except Exception:   # noqa: BLE001
        log.exception('commissions')
    out['expired'] = Order.objects.filter(status__in=('quote', 'sent'), valid_until__lt=timezone.localdate()).update(status='lost', lost_reason='Expired')
    return out
