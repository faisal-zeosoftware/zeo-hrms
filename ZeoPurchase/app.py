"""ZEO Purchase on the app kit (v1.78.0): purchase requests (also from Inventory's reorder list), requests for quotation with the
comparison, purchase orders with approval by amount, receipts through Inventory (partial, backorders), supplier bills through Accounting
with the three-way match, supplier returns with debit notes, supplier price lists and supplier performance."""
from decimal import Decimal

from django.db.models import F, Q

from zeo.appkit.engine import Act, App, AppError, Reg, d, in_branches, today

from . import services as S
from .models import ROLES, Bill, Order, OrderLine, PurchaseEvent, PurchaseFile, PurchaseSequence, PurchaseSettings, Quote, Receipt, Request, Return, Rfq, SupplierPrice
from .packs import PACKS

PAGE = '/main-sidebar/zeo-purchase/'

app = App(
    label='ZeoPurchase', slug='zeo-purchase', title='Purchase', settings_model=PurchaseSettings, sequence_model=PurchaseSequence, event_model=PurchaseEvent,
    file_model=PurchaseFile, roles=ROLES, role_prefix='pur_', manager_role='pur_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'RET': 'trading', 'TRD': 'trading', 'MFG': 'trading', 'CON': 'projects'}, pack_models={},
    links=(('ZeoInventory', 'Inventory', 'Receipts and supplier returns move the stock; the reorder list becomes a purchase request.'),
           ('ZeoLedger', 'Accounting', 'Supplier bills and debit notes are made in Accounting from the order (three-way match).')),
    page=PAGE,
)
app.settings_fields = ('currency', 'bill_policy', 'qty_tolerance', 'price_tolerance', 'over_receipt', 'approve_amount', 'approve_amount_2', 'rfq_min_quotes',
                       'default_warehouse')

MGR = ('pur_manager',)
BUY = ('pur_manager', 'pur_buyer')
REQ = ('pur_manager', 'pur_buyer', 'pur_requester')
AP = ('pur_manager', 'pur_ap')
VIEW = ('pur_buyer', 'pur_requester', 'pur_ap', 'pur_viewer')
ALL = ('pur_manager', 'pur_buyer', 'pur_requester', 'pur_ap', 'pur_viewer')
OPEN_PO = ('approved', 'sent', 'part_received', 'received')


def run(fn):
    try:
        return fn()
    except S.PurchaseError as e:
        raise AppError(e.message, e.field)


def tbl(label, cols, rows):
    return {'name': '_' + label.lower().replace(' ', '_')[:30], 'label': label, 'columns': [{'key': k, 'label': lb, 'type': t} for k, lb, t in cols], 'rows': rows}


def money(x):
    return float(x or 0)


def finance(request):
    """the second approval level: the finance manager of Accounting (or the company administrator)"""
    from zeo.appkit.engine import REGISTRY
    led = REGISTRY.get('ZeoLedger')
    return bool(led and led.has(request, 'gl_manager')) or app.is_admin(request)


try:
    from ZeoInventory.models import Warehouse as _Wh
    app.option('warehouses', lambda: _Wh.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), _Wh)
except Exception:   # noqa: BLE001
    pass
try:
    from ZeoMasters.models import PaymentTerm as _PT, Product as _Product
    app.option('items', lambda: _Product.objects.filter(is_active=True, is_purchased=True), lambda x: f'{x.number} {x.name}', ('number', 'name', 'code', 'barcode'), _Product)
    app.option('payment-terms', lambda: _PT.objects.filter(is_active=True), lambda x: x.name, ('code', 'name'), _PT)
except Exception:   # noqa: BLE001
    pass
app.option('orders', lambda: Order.objects.exclude(status__in=('draft', 'cancelled')), lambda x: f'{x.number} {x.supplier}', ('number', 'supplier_ref', 'supplier__name'), Order)
app.option('open-orders', lambda: Order.objects.filter(status__in=OPEN_PO), lambda x: f'{x.number} {x.supplier}', ('number', 'supplier__name'), Order)
app.option('quotes', lambda: Quote.objects.select_related('supplier', 'rfq'), lambda x: f'{x.rfq} – {x.supplier} ({x.total:,.2f})', ('supplier__name', 'reference'), Quote)
app.provide('purchase.orders', lambda: Order.objects.all(), lambda x: f'{x.number} {x.supplier}', ('number', 'supplier_ref'))


# ------------------------------------------------------------------ requests
def req_defaults(o, request):
    if not o.date:
        o.date = today()
    if not o.requested_by_id:
        o.requested_by = request.user
    if not o.warehouse_id and S.settings().default_warehouse_id:
        o.warehouse_id = S.settings().default_warehouse_id


def req_check(o, request, old):
    if old is not None and old.status not in ('draft', 'submitted'):
        raise AppError('An approved request is not changed.')
    for x in o.lines or []:
        if not x.get('product'):
            raise AppError('Choose the item on every line.', 'lines')
        try:
            q = Decimal(str(x.get('qty') or 0))
        except Exception:   # noqa: BLE001
            raise AppError('Enter the quantity of every line.', 'lines')
        if q <= 0:
            raise AppError('Enter the quantity of every line.', 'lines')


def req_saver(o, request, creating, old):
    if not o.number:
        o.number = app.next_number('request', 'PR')
    o.save()
    return o


def a_req_submit(o, request, f):
    if not o.lines:
        raise AppError('Add the items.', 'lines')
    o.status = 'submitted'
    o.save(update_fields=['status'])
    return {'message': 'Sent to the buyer for approval.'}


def a_req_approve(o, request, f):
    o.status, o.approved_by = 'approved', request.user
    o.save(update_fields=['status', 'approved_by'])
    app.event('request', o.pk, 'Approved', request.user)
    return {'message': 'Approved – order it or ask suppliers for quotes.'}


def a_req_reject(o, request, f):
    o.status = 'rejected'
    o.note = (o.note + '\n' if o.note else '') + f'Rejected: {f.s("note")}'
    o.save(update_fields=['status', 'note'])
    return {'message': 'Rejected.'}


def a_req_order(o, request, f):
    made = run(lambda: S.order_from_request(o, request.user))
    return {'message': 'Purchase order(s) made: ' + ', '.join(f'{x.supplier} ({x.total:,.2f})' for x in made) + ' – submit them for approval.',
            'open': f'r/order/{made[0].pk}' if len(made) == 1 else None}


def a_req_rfq(o, request, f):
    rfq = run(lambda: S.request_to_rfq(o, user=request.user))
    return {'message': f'Request for quotation {rfq.number} to {rfq.suppliers.count()} supplier(s).', 'open': f'r/rfq/{rfq.pk}'}


def a_reorder(o, request, f):
    req = run(lambda: S.from_reorder(request.user))
    return {'message': f'Purchase request {req.number} with {len(req.lines)} item(s) from the reorder list of Inventory.', 'open': f'r/request/{req.pk}'}


# ------------------------------------------------------------------ RFQs and quotes
def rfq_defaults(o, request):
    if not o.date:
        o.date = today()


def rfq_check(o, request, old):
    if old is not None and old.status in ('awarded', 'cancelled'):
        raise AppError('This RFQ is closed.')


def rfq_saver(o, request, creating, old):
    if not o.number:
        o.number = app.next_number('rfq', 'RFQ')
    o.save()
    return o


def rfq_extra(o, request):
    rows, tot = S.compare(o)
    out = {'tables': []}
    if o.quotes.exists():
        names = [str(q.supplier) for q in o.quotes.select_related('supplier')]
        out['tables'].append(tbl('Comparison', [('item', 'Item', 'text'), ('qty', 'Quantity', 'number')] + [(n, n, 'number') for n in names] + [('best', 'Best price', 'text')], rows))
        out['tables'].append(tbl('Quotes', [('supplier', 'Supplier', 'text'), ('total', 'Total', 'number'), ('lead_days', 'Delivery (days)', 'number'), ('valid_until', 'Valid until', 'text')],
                                 [dict(t, valid_until=d(t['valid_until'])) for t in tot]))
    return out


def a_rfq_send(o, request, f):
    if not o.lines:
        raise AppError('Add the items.', 'lines')
    if not o.suppliers.exists():
        raise AppError('Choose the suppliers.', 'suppliers')
    o.status = 'sent'
    o.save(update_fields=['status'])
    app.event('rfq', o.pk, 'Sent to ' + ', '.join(str(s) for s in o.suppliers.all()), request.user)
    return {'message': f'Marked as sent to {o.suppliers.count()} supplier(s) – print it or e-mail it from the record, then enter their quotes.'}


def a_rfq_award(o, request, f):
    q = o.quotes.filter(pk=f.i('quote')).first() if f.i('quote') else o.quotes.order_by('total').first()
    if q is None:
        raise AppError('Enter the supplier quotes first.')
    po = run(lambda: S.award(o, q, request.user))
    return {'message': f'Awarded to {q.supplier}: purchase order draft of {po.total:,.2f}.', 'open': f'r/order/{po.pk}'}


def quote_defaults(o, request):
    if o.rfq_id and not o.prices:
        o.prices = [{'product': x['product'], 'price': ''} for x in o.rfq.lines or []]


def quote_check(o, request, old):
    if o.rfq.status in ('awarded', 'cancelled'):
        raise AppError('This RFQ is closed.')
    if o.supplier_id and o.rfq.quotes.filter(supplier_id=o.supplier_id).exclude(pk=o.pk).exists():
        raise AppError('This supplier has a quote on the RFQ already.', 'supplier')


def quote_saver(o, request, creating, old):
    o.save()
    S.quote_total(o)
    if o.rfq.status == 'sent':
        o.rfq.status = 'compared'
        o.rfq.save(update_fields=['status'])
    return o


# ------------------------------------------------------------------ orders
def order_defaults(o, request):
    st = S.settings()
    if not o.date:
        o.date = today()
    if not o.warehouse_id and st.default_warehouse_id:
        o.warehouse_id = st.default_warehouse_id
    if o.supplier_id:
        p = o.supplier
        if not o.payment_term_id and getattr(p, 'payment_term_id', None):
            o.payment_term_id = p.payment_term_id
        if not o.currency and p.currency and p.currency.upper() != st.currency.upper():
            o.currency = p.currency.upper()
    o.buyer = o.buyer or request.user
    o.created_by = o.created_by or request.user


def order_check(o, request, old):
    if old is not None and old.status not in ('draft',):
        raise AppError('A submitted order is not changed – send it back to draft (Reject) or cancel what is left.')
    if o.supplier_id and getattr(o.supplier, 'status', 'active') == 'blocked':
        raise AppError(f'{o.supplier} is blocked: {o.supplier.blocked_reason or "see the partner"}.', 'supplier')


def order_saver(o, request, creating, old):
    if not o.number:
        o.number = app.next_number('order', 'PO')
    o.save()
    S.totals(o)
    return o


def order_extra(o, request):
    out = {'totals': {'subtotal': money(o.net), 'tax': money(o.vat), 'total': money(o.total), 'currency': o.currency or S.settings().currency,
                      'net': money(o.net), 'vat': money(o.vat), 'billed': money(o.billed)}}
    warn = []
    if o.status == 'draft':
        need = S.levels_needed(o)
        if need:
            warn.append('Needs approval: ' + ', '.join('the purchase manager' if x == 'pur_manager' else 'the finance manager' for x in need) + '.')
    if o.approval_note and o.status == 'approval':
        warn.insert(0, o.approval_note)
    late = o.expected_date and o.expected_date < today() and o.status in ('approved', 'sent', 'part_received')
    if late:
        warn.append(f'Late: expected on {d(o.expected_date)}.')
    out['warnings'] = warn
    rows = [{'item': ln.description, 'ordered': float(ln.qty), 'received': float(ln.received), 'billed': float(ln.billed_qty), 'returned': float(ln.returned),
             'to_receive': float(max(Decimal(0), ln.qty - ln.received)), 'price': float(ln.price)} for ln in o.lines.all()]
    out['tables'] = [tbl('Receive and bill', [('item', 'Item', 'text'), ('ordered', 'Ordered', 'number'), ('received', 'Received', 'number'), ('billed', 'Billed', 'number'),
                                              ('returned', 'Returned', 'number'), ('to_receive', 'To receive', 'number'), ('price', 'Order price', 'number')], rows)]
    rc = [{'date': d(x.date), 'number': x.stock_number or '(services)', 'note': x.delivery_note, 'value': money(x.value), 'on_time': 'Yes' if x.on_time else 'Late'} for x in o.receipts.all()]
    bl = [{'date': d(x.date), 'number': x.doc_number, 'ref': x.supplier_ref, 'kind': x.get_kind_display(), 'total': money(x.total)} for x in o.bills.all()]
    if rc:
        out['tables'].append(tbl('Receipts', [('date', 'Date', 'text'), ('number', 'Receipt', 'text'), ('note', 'Delivery note', 'text'), ('value', 'Value', 'number'),
                                              ('on_time', 'On time', 'text')], rc))
    if bl:
        out['tables'].append(tbl('Bills', [('date', 'Date', 'text'), ('number', 'Bill', 'text'), ('ref', 'Supplier invoice', 'text'), ('kind', 'Kind', 'text'), ('total', 'Total', 'number')], bl))
    last = o.bills.filter(kind='bill').first()
    if last and last.match:
        out['tables'].append(tbl('Three-way match (last bill)', [('item', 'Item', 'text'), ('ordered', 'Ordered', 'number'), ('received', 'Received', 'number'),
                                                                 ('bill_qty', 'Billed', 'number'), ('order_price', 'Order price', 'number'), ('bill_price', 'Bill price', 'number'),
                                                                 ('ok', 'Match', 'text')],
                                 [dict(m, ok='Yes' if m.get('qty_ok') and m.get('price_ok') else 'No') for m in last.match]))
    return out


def a_submit(o, request, f):
    run(lambda: S.submit(o, request.user))
    app.event('order', o.pk, f'Submitted: {o.get_status_display()}', request.user)
    return {'message': f'{o.number}: {o.approval_note} – sent for approval.' if o.status == 'approval' else f'{o.number} approved (below the approval amount).'}


def a_approve(o, request, f):
    roles = set()
    if app.any(request, *MGR):
        roles.add('pur_manager')
    if finance(request):
        roles.add('finance')
    run(lambda: S.approve(o, request.user, roles))
    app.event('order', o.pk, f'Approved ({o.approvals[-1]["level"]}) {f.s("note")}', request.user)
    return {'message': 'Approved.' if o.status == 'approved' else f'Approved – {o.approval_note.lower()}.'}


def a_reject(o, request, f):
    o.status, o.approvals, o.approval_note = 'draft', [], f'Sent back: {f.s("note")}'[:255]
    o.save(update_fields=['status', 'approvals', 'approval_note'])
    app.event('order', o.pk, f'Sent back to draft: {f.s("note")}', request.user)
    return {'message': 'Back to draft.'}


def a_send(o, request, f):
    o.status = 'sent'
    o.save(update_fields=['status'])
    app.event('order', o.pk, 'Sent to the supplier', request.user)
    return {'message': f'{o.number} marked as sent – print it or e-mail it from the record.'}


def a_receive(o, request, f):
    qtys = f.j('qtys') if f.has('qtys') else None
    rc = run(lambda: S.receive(o, qtys or None, f.dt('date'), request.user, f.s('delivery_note')))
    return {'message': f'Received{" – " + rc.stock_number if rc.stock_number else ""}; order {o.get_status_display().lower()}.'}


def a_bill(o, request, f):
    force = f.b('force') and app.any(request, *MGR)
    if f.b('force') and not force:
        raise AppError('Only the purchase manager accepts a bill that does not match.')
    b = run(lambda: S.bill(o, f.s('supplier_ref'), f.j('lines') if f.has('lines') else None, f.dt('date'), request.user, force))
    return {'message': f'Bill {b.doc_number} of {b.total:,.2f} made in Accounting (supplier invoice {b.supplier_ref}).'}


def a_return(o, request, f):
    rt = Return.objects.create(order=o, date=today(), reason=f.s('note'),
                               lines=[{'line': ln.pk, 'qty': str(ln.received - ln.returned)} for ln in o.lines.all() if ln.received > ln.returned])
    return {'message': 'Return drafted with everything received – change the quantities and press Done.', 'open': f'r/return/{rt.pk}'}


def a_copy(o, request, f):
    n = Order.objects.create(supplier=o.supplier, date=today(), branch=o.branch, warehouse=o.warehouse, payment_term=o.payment_term, currency=o.currency,
                             bill_policy=o.bill_policy, notes=o.notes, terms=o.terms, buyer=request.user, created_by=request.user, number=app.next_number('order', 'PO'))
    for ln in o.lines.all():
        OrderLine.objects.create(order=n, product=ln.product, description=ln.description, qty=ln.qty, price=ln.price, discount=ln.discount, tax_code=ln.tax_code,
                                 net=ln.net, vat=ln.vat, is_service=ln.is_service, account_code=ln.account_code)
    S.totals(n)
    return {'message': f'Copied as {n.number}.', 'open': f'r/order/{n.pk}'}


def a_cancel(o, request, f):
    if o.receipts.exists() or o.bills.exists():
        o.status, msg = 'closed', 'Closed: what is left is not received or billed.'
    else:
        o.status, msg = 'cancelled', 'Cancelled.'
    o.save(update_fields=['status'])
    app.event('order', o.pk, f'{msg} {f.s("note")}', request.user)
    return {'message': msg}


def line_defaults(o, request):
    if o.order_id:
        S.price_line(o)


def line_check(o, request, old):
    if o.order.status != 'draft':
        raise AppError('A submitted order is not changed.')
    if not o.product_id and not o.description:
        raise AppError('Choose the item or describe the line.', 'product')
    if o.qty is None or Decimal(o.qty) <= 0:
        raise AppError('Enter the quantity.', 'qty')
    if old is not None and (old.qty != o.qty or old.product_id != o.product_id) and old.price == o.price:
        o.price = 0
    S.price_line(o)


def line_saver(o, request, creating, old):
    o.save()
    S.totals(o.order)
    return o


def ret_check(o, request, old):
    if old is not None and old.status != 'draft':
        raise AppError('A done return is not changed.')
    if not o.date:
        o.date = today()


def a_ret_done(o, request, f):
    run(lambda: S.do_return(o, request.user))
    return {'message': f'Return {o.number} done' + (f'; stock out by {o.stock_number}' if o.stock_number else '') + (f'; debit note {o.debit_number}' if o.debit_number else '') + '.'}


R = app.reg
R(Reg('request', Request, 'Purchase request', 'Purchase requests', 'Purchase', cols=('number', 'date', 'needed_by', 'requested_by', 'department', 'source', 'status'),
      form=('date', 'needed_by', 'department', 'warehouse', 'branch', 'lines', 'note'), manage=MGR, view=VIEW, create=REQ, own=(('requested_by', 'user'),), own_edit=True,
      search=('number', 'department'), filters=('status', 'source', 'warehouse'), title='number', subtitle='department', defaults=req_defaults, check=req_check, saver=req_saver,
      edit_when=lambda o: o.status in ('draft', 'submitted'), deletable=True, info=('approved_by',),
      tables={'lines': [('product', 'Item', 'opt', 'items'), ('qty', 'Quantity', 'decimal'), ('note', 'Note', 'text')]},
      actions=[Act('submit', 'Submit', a_req_submit, lambda o, r: o.status == 'draft' and app.any(r, *REQ), style='primary'),
               Act('approve', 'Approve', a_req_approve, lambda o, r: o.status in ('draft', 'submitted') and app.any(r, *BUY), style='primary'),
               Act('reject', 'Reject', a_req_reject, lambda o, r: o.status == 'submitted' and app.any(r, *BUY), style='danger', fields=(('note', 'Why', 'text', True),)),
               Act('order', 'Make purchase orders', a_req_order, lambda o, r: o.status == 'approved' and app.any(r, *BUY), style='primary',
                   confirm='One purchase order per supplier: the cheapest supplier price of each item, or the preferred supplier of the item.'),
               Act('rfq', 'Ask suppliers for quotes', a_req_rfq, lambda o, r: o.status == 'approved' and app.any(r, *BUY)),
               Act('reorder', 'New request from the reorder list', a_reorder, lambda o, r: app.any(r, *BUY) and S.inventory_on())],
      help='What a department needs: approved by the buyer, then ordered from the best supplier or sent for quotes. The reorder list of Inventory makes one too.'))
R(Reg('rfq', Rfq, 'Request for quotation', 'Requests for quotation', 'Purchase', cols=('number', 'date', 'reply_by', 'request', 'awarded_to', 'status'),
      form=('date', 'reply_by', 'suppliers', 'lines', 'note'), manage=MGR, view=VIEW, create=BUY, search=('number',), filters=('status',), title='number',
      defaults=rfq_defaults, check=rfq_check, saver=rfq_saver, extra=rfq_extra, edit_when=lambda o: o.status in ('draft', 'sent', 'compared'), deletable=True, pdf='Request for quotation',
      tables={'lines': [('product', 'Item', 'opt', 'items'), ('qty', 'Quantity', 'decimal')]}, children=(('Supplier quotes', 'quote', 'rfq'),),
      actions=[Act('send', 'Mark as sent', a_rfq_send, lambda o, r: o.status == 'draft' and app.any(r, *BUY)),
               Act('award', 'Award', a_rfq_award, lambda o, r: o.status in ('sent', 'compared') and app.any(r, *BUY), style='primary',
                   fields=(('quote', 'Quote (empty: the lowest total)', 'opt:quotes', False),))],
      help='The same items to several suppliers; enter their prices and compare line by line; award to make the purchase order.'))
R(Reg('quote', Quote, 'Supplier quote', 'Supplier quotes', 'Purchase', cols=('rfq', 'supplier', 'reference', 'total', 'lead_days', 'valid_until'),
      form=('rfq', 'supplier', 'reference', 'prices', 'currency', 'lead_days', 'valid_until', 'note'), manage=MGR, view=VIEW, create=BUY, child_of='rfq',
      search=('supplier__name', 'reference'), filters=(), title='supplier', subtitle='rfq', defaults=quote_defaults, check=quote_check, saver=quote_saver, deletable=True,
      edit_when=lambda o: o.rfq.status not in ('awarded', 'cancelled'), info=('total',),
      tables={'prices': [('product', 'Item', 'opt', 'items'), ('price', 'Unit price', 'decimal')]}))
R(Reg('order', Order, 'Purchase order', 'Purchase orders', 'Purchase',
      cols=('number', 'date', 'supplier', 'supplier_ref', 'expected_date', 'total', 'billed', 'status'),
      form=('supplier', 'date', 'expected_date', 'supplier_ref', 'warehouse', 'payment_term', 'currency', 'bill_policy', 'branch', 'notes', 'terms'),
      manage=MGR, view=VIEW, create=BUY, own=(('buyer', 'user'), ('created_by', 'user')), own_edit=True,
      search=('number', 'supplier_ref', 'supplier__name'), filters=('status', 'supplier', 'warehouse', 'branch'),
      title='number', subtitle='supplier', defaults=order_defaults, check=order_check, saver=order_saver, extra=order_extra, deletable=True, pdf='Purchase order',
      edit_when=lambda o: o.status == 'draft', info=('billed', 'approval_note', 'rfq', 'request'),
      children=(('Lines', 'line', 'order'), ('Supplier returns', 'return', 'order')),
      actions=[Act('submit', 'Submit', a_submit, lambda o, r: o.status == 'draft' and app.any(r, *BUY), style='primary',
                   confirm='Orders above the approval amount go to the purchase manager (and the finance manager above the second amount).'),
               Act('approve', 'Approve', a_approve, lambda o, r: o.status == 'approval' and (app.any(r, *MGR) or finance(r)), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('reject', 'Send back', a_reject, lambda o, r: o.status in ('approval', 'approved') and not o.receipts.exists() and app.any(r, *MGR), style='danger',
                   fields=(('note', 'Why', 'text', True),)),
               Act('send', 'Mark as sent', a_send, lambda o, r: o.status == 'approved' and app.any(r, *BUY)),
               Act('receive', 'Receive', a_receive, lambda o, r: o.status in ('approved', 'sent', 'part_received') and app.any(r, *AP), style='primary',
                   fields=(('date', 'Receipt date (empty: today)', 'date', False), ('delivery_note', "Supplier's delivery note", 'text', False),
                           ('qtys', 'Quantities by line {line id: qty} (empty: everything left)', 'json', False))),
               Act('bill', 'Enter the bill', a_bill, lambda o, r: o.status in OPEN_PO and app.any(r, *AP),
                   fields=(('supplier_ref', "Supplier's invoice number", 'text', True), ('date', 'Bill date (empty: today)', 'date', False),
                           ('lines', 'Lines [{line, qty, price}] (empty: everything received and not billed)', 'json', False),
                           ('force', 'Accept although it does not match (purchase manager)', 'bool', False))),
               Act('return', 'Return to the supplier', a_return, lambda o, r: o.status in ('part_received', 'received', 'billed', 'closed') and app.any(r, *AP),
                   fields=(('note', 'Reason', 'text', False),)),
               Act('copy', 'Copy', a_copy, lambda o, r: app.any(r, *BUY)),
               Act('cancel', 'Cancel / close', a_cancel, lambda o, r: o.status in ('draft', 'approval', 'approved', 'sent', 'part_received', 'received') and app.any(r, *MGR),
                   style='danger', fields=(('note', 'Why', 'text', False),))],
      help='Approved by amount; received in full or in part (Inventory); the supplier bill is checked against what was ordered and received before it goes to Accounting.'))
R(Reg('line', OrderLine, 'Line', 'Lines', 'Purchase', cols=('product', 'description', 'qty', 'price', 'discount', 'tax_code', 'net', 'vat', 'received', 'billed_qty', 'returned'),
      form=('order', 'product', 'description', 'qty', 'price', 'discount', 'tax_code', 'account_code'), manage=MGR, view=VIEW, create=BUY, child_of='order',
      search=('description', 'product__name'), filters=(), title='description', defaults=line_defaults, check=line_check, saver=line_saver,
      edit_when=lambda o: o.order.status == 'draft', files=False, deletable=True, info=('net', 'vat', 'received', 'billed_qty', 'returned', 'is_service')))
R(Reg('return', Return, 'Supplier return', 'Supplier returns', 'Purchase', cols=('number', 'date', 'order', 'reason', 'total', 'stock_number', 'debit_number', 'status'),
      form=('order', 'date', 'reason', 'lines', 'debit_note'), manage=MGR, view=VIEW, create=AP, search=('number', 'order__number', 'reason'), filters=('status',),
      title='number', subtitle='order', check=ret_check, edit_when=lambda o: o.status == 'draft', info=('stock_number', 'debit_number', 'total'),
      tables={'lines': [('line', 'Order line id', 'int'), ('qty', 'Quantity', 'decimal')]},
      actions=[Act('done', 'Done', a_ret_done, lambda o, r: o.status == 'draft' and app.any(r, *AP), style='primary',
                   confirm='The stock goes out (Inventory) and a debit note is made against the bill (Accounting).')],
      help='Goods sent back to the supplier: out of stock and a debit note.'))
R(Reg('receipt', Receipt, 'Receipt', 'Receipts', 'Purchase', cols=('order', 'date', 'stock_number', 'delivery_note', 'value', 'on_time'), form=(), manage=MGR, view=ALL,
      create='none', search=('order__number', 'stock_number', 'delivery_note'), filters=('on_time',), title='stock_number', files=False))
R(Reg('bill', Bill, 'Supplier bill', 'Supplier bills', 'Purchase', cols=('order', 'kind', 'date', 'supplier_ref', 'doc_number', 'total'), form=(), manage=MGR, view=ALL, create='none',
      search=('order__number', 'doc_number', 'supplier_ref'), filters=('kind',), title='doc_number', files=False))
R(Reg('supplier_price', SupplierPrice, 'Supplier price', 'Supplier prices', 'Setup',
      cols=('supplier', 'product', 'supplier_code', 'min_qty', 'price', 'currency', 'lead_days', 'date_from', 'date_to', 'is_active'),
      form=('supplier', 'product', 'supplier_code', 'min_qty', 'price', 'currency', 'lead_days', 'date_from', 'date_to', 'is_active'), manage=MGR, view=ALL,
      search=('supplier__name', 'product__name', 'supplier_code'), filters=('supplier', 'is_active'), title='product', subtitle='supplier', deletable=True, files=False,
      help="Each supplier's price for an item, from a quantity, with the lead time – the lowest valid price is proposed on the order. Awarded quotes update it."))
try:   # the shared payment terms of Masters (also on Sales, CRM and Accounting)
    from ZeoMasters.models import PaymentTerm as _PT2
    R(Reg('payment_term', _PT2, 'Payment term', 'Payment terms', 'Setup', cols=('code', 'name', 'kind', 'days', 'instalments', 'is_default', 'is_active'),
          form=('code', 'name', 'kind', 'days', 'instalments', 'is_default', 'is_active'), manage=MGR, view=ALL, search=('code', 'name'), filters=('is_active',), title='name',
          deletable=True, files=False, help='Due dates of the supplier bills (shared with Sales, CRM and Accounting).'))
except Exception:   # noqa: BLE001
    pass


# ------------------------------------------------------------------ dashboard, my work
def dashboard(request):
    t = today()
    qs = in_branches(Order.objects.all(), request)
    month = qs.filter(date__gte=t.replace(day=1)).exclude(status__in=('draft', 'cancelled'))
    approval = qs.filter(status='approval')
    to_receive = qs.filter(status__in=('approved', 'sent', 'part_received'))
    late = to_receive.filter(expected_date__lt=t)
    to_bill = [o for o in qs.filter(status__in=OPEN_PO) if o.lines.filter(billed_qty__lt=F('received')).exists()]
    reqs = Request.objects.filter(status__in=('submitted', 'approved'))
    cur = S.settings().currency
    by_sup = {}
    for o in month.select_related('supplier'):
        k = str(o.supplier)
        by_sup[k] = by_sup.get(k, 0) + float(o.net)
    return {'cards': [{'label': f'Purchases this month ({cur})', 'value': f'{sum(float(o.net) for o in month):,.2f}'},
                      {'label': 'Requests to handle', 'value': reqs.count(), 'link': 'r/request?status=submitted'},
                      {'label': 'Waiting for approval', 'value': approval.count(), 'tone': 'bad' if approval.exists() else 'ok', 'link': 'r/order?status=approval'},
                      {'label': 'Orders to receive', 'value': to_receive.count(), 'link': 'r/order?status=approved'},
                      {'label': 'Late deliveries', 'value': late.count(), 'tone': 'bad' if late.exists() else 'ok'},
                      {'label': 'Received, not billed', 'value': len(to_bill), 'tone': 'bad' if to_bill else 'ok'}],
            'bars': [{'title': 'Purchases this month by supplier', 'items': [{'label': k, 'value': round(v, 2)} for k, v in sorted(by_sup.items(), key=lambda x: -x[1])[:10]]}],
            'lists': [{'title': 'To receive', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.supplier}', 'sub': f'expected {d(o.expected_date)}' if o.expected_date else '',
                                                                         'badge': f'{o.total:,.2f}'} for o in to_receive.select_related('supplier').order_by('expected_date')[:10]]},
                      {'title': 'Waiting for approval', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.supplier}', 'sub': o.approval_note[:80], 'badge': f'{o.total:,.2f}'}
                                                                                   for o in approval.select_related('supplier')[:10]]}]}


def my_work(request):
    out = []
    if app.any(request, *BUY):
        out.append({'title': 'Requests to approve or order', 'kind': 'request', 'rows': [{'id': r.pk, 'title': f'{r.number} {r.department}', 'badge': r.get_status_display()}
                                                                                       for r in Request.objects.filter(status__in=('submitted', 'approved'))[:30]]})
    if app.any(request, *MGR) or finance(request):
        out.append({'title': 'Orders to approve', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.supplier}', 'sub': o.approval_note[:80], 'badge': f'{o.total:,.2f}'}
                                                                          for o in Order.objects.filter(status='approval')[:30]]})
    mine = Request.objects.filter(requested_by=request.user).exclude(status__in=('ordered', 'cancelled'))[:30]
    if mine:
        out.append({'title': 'My purchase requests', 'kind': 'request', 'rows': [{'id': r.pk, 'title': r.number, 'badge': r.get_status_display()} for r in mine]})
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, S.daily

from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)

from zeo.appkit.features import Feature as _F   # noqa: E402

app.features = [
    _F('requests', 'Purchase requests', 'Procurement', 'Departments ask for items; the buyer approves and orders them (also from the reorder list of Inventory).', regs=('request',),
       fields={'order': ('request',)}, in_use=lambda: Request.objects.count()),
    _F('rfq', 'Requests for quotation', 'Procurement', 'The same items to several suppliers, their quotes compared line by line, the best awarded.', regs=('rfq', 'quote'),
       fields={'order': ('rfq',)}, settings=('rfq_min_quotes',), in_use=lambda: Rfq.objects.count()),
    _F('approvals', 'Order approvals by amount', 'Control', 'Orders above an amount wait for the purchase manager, above a second amount also for the finance manager.',
       settings=('approve_amount', 'approve_amount_2'), in_use=lambda: int(S.settings().approve_amount is not None)),
    _F('match', 'Three-way match tolerances', 'Control', 'Bills may differ from the order price and the received quantity within a %.',
       settings=('qty_tolerance', 'price_tolerance', 'over_receipt'), in_use=lambda: 0),
    _F('supplier_prices', 'Supplier price lists', 'Pricing', "Each supplier's prices by quantity with lead times; the best price proposed.", regs=('supplier_price',),
       in_use=lambda: SupplierPrice.objects.count()),
    _F('returns', 'Supplier returns', 'Receiving', 'Goods back to the supplier with a debit note.', regs=('return',), in_use=lambda: Return.objects.count()),
    _F('multi_currency', 'Purchases in foreign currencies', 'Billing', 'Orders and bills in USD, EUR … at the rate of Accounting.', fields={'order': ('currency',), 'quote': ('currency',)},
       in_use=lambda: Order.objects.exclude(currency='').count()),
]
app.presets = {
    'trading': ('Trading', ('supplier_prices', 'returns', 'multi_currency')),
    'controlled': ('Controlled procurement', ('requests', 'rfq', 'approvals', 'match', 'supplier_prices', 'returns')),
    'all': ('Everything', tuple(f.key for f in app.features)),
}
