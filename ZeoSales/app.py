"""ZEO Sales on the app kit (v1.77.0): quotations and sales orders (also from CRM), prices from the price books and the price rules, credit
control and approvals, deliveries through Inventory (partial, backorders), invoices and down payments through Accounting, returns, sales
teams and commissions."""
from decimal import Decimal

from django.db.models import F, Q, Sum

from zeo.appkit.engine import Act, App, AppError, Reg, d, in_branches, today

from . import services as S
from .models import ROLES, Commission, Delivery, DiscountRule, Invoice, Order, OrderLine, Return, SalesEvent, SalesFile, SalesSequence, SalesSettings, Team
from .packs import PACKS

PAGE = '/main-sidebar/zeo-sales/'

app = App(
    label='ZeoSales', slug='zeo-sales', title='Sales', settings_model=SalesSettings, sequence_model=SalesSequence, event_model=SalesEvent,
    file_model=SalesFile, roles=ROLES, role_prefix='sal_', manager_role='sal_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'RET': 'trading', 'TRD': 'trading', 'MFG': 'trading'}, pack_models={'team': (Team, 'name'), 'rule': (DiscountRule, 'name')},
    links=(('ZeoInventory', 'Inventory', 'Deliveries and returns move the stock (delivery and customer return documents).'),
           ('ZeoLedger', 'Accounting', 'Invoices, down payments and credit notes are made in Accounting; credit control reads the customer balance.'),
           ('ZeoCRM', 'CRM', 'A CRM quotation or sales order becomes a Sales order.')),
    page=PAGE,
)
app.settings_fields = ('currency', 'invoice_policy', 'credit_control', 'overdue_days', 'quote_days', 'approve_discount', 'approve_amount', 'default_warehouse',
                       'commission_on', 'min_margin')

MGR = ('sal_manager',)
USERS = ('sal_manager', 'sal_user')
BILL = ('sal_manager', 'sal_billing')
VIEW = ('sal_user', 'sal_billing', 'sal_viewer')
ALL = ('sal_manager', 'sal_user', 'sal_billing', 'sal_viewer')


def run(fn):
    try:
        return fn()
    except S.SalesError as e:
        raise AppError(e.message, e.field)


def tbl(label, cols, rows):
    return {'name': '_' + label.lower().replace(' ', '_')[:30], 'label': label, 'columns': [{'key': k, 'label': lb, 'type': t} for k, lb, t in cols], 'rows': rows}


def money(x):
    return float(x or 0)


try:
    from ZeoInventory.models import Warehouse as _Wh
    app.option('warehouses', lambda: _Wh.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), _Wh)
except Exception:   # noqa: BLE001
    pass
try:
    from ZeoMasters.models import PaymentTerm as _PT, PriceBook as _PB, Product as _Product, ProductCategory as _Cat
    app.option('items', lambda: _Product.objects.filter(is_active=True, is_sold=True), lambda x: f'{x.number} {x.name}', ('number', 'name', 'code', 'barcode'), _Product)
    app.option('price-books', lambda: _PB.objects.filter(is_active=True), lambda x: f'{x.code} {x.name}', ('code', 'name'), _PB)
    app.option('payment-terms', lambda: _PT.objects.filter(is_active=True), lambda x: x.name, ('code', 'name'), _PT)
    app.option('categories', lambda: _Cat.objects.all(), lambda x: x.name, ('name', 'code'), _Cat)
except Exception:   # noqa: BLE001
    pass
try:
    from EmpManagement.models import emp_master as _E
    app.option('employees', lambda: _E.objects.filter(is_active=True), lambda x: f'{x.emp_code} {x.emp_first_name} {x.emp_last_name or ""}'.strip(),
               ('emp_code', 'emp_first_name', 'emp_last_name'), _E)
except Exception:   # noqa: BLE001
    pass
try:
    from ZeoCRM.models import Quotation as _CQ, SalesOrder as _CS
    app.option('crm-quotes', lambda: _CQ.objects.filter(status__in=('accepted', 'sent', 'approved')), lambda x: f'{x.number} {x.account or ""}', ('number',), _CQ)
    app.option('crm-orders', lambda: _CS.objects.exclude(status__in=('cancelled', 'closed')), lambda x: f'{x.number} {x.account or ""}', ('number',), _CS)
except Exception:   # noqa: BLE001
    pass
app.option('teams', lambda: Team.objects.filter(is_active=True), lambda x: x.name, ('name',), Team)
app.option('orders', lambda: Order.objects.exclude(status__in=('quote', 'sent', 'lost', 'cancelled')), lambda x: f'{x.number} {x.partner}', ('number', 'reference', 'partner__name'), Order)
app.provide('sales.orders', lambda: Order.objects.all(), lambda x: f'{x.number or x.quote_number} {x.partner}', ('number', 'quote_number'))


# ------------------------------------------------------------------ orders
def order_defaults(o, request):
    st = S.settings()
    if not o.date:
        o.date = today()
    if not o.valid_until:
        from datetime import timedelta
        o.valid_until = o.date + timedelta(days=st.quote_days or 30)
    if not o.warehouse_id and st.default_warehouse_id:
        o.warehouse_id = st.default_warehouse_id
    if o.partner_id:
        p = o.partner
        if not o.payment_term_id and p.payment_term_id:
            o.payment_term_id = p.payment_term_id
        if not o.price_book_id and p.price_book_id:
            o.price_book_id = p.price_book_id
        if not o.currency and p.currency and p.currency.upper() != st.currency.upper():
            o.currency = p.currency.upper()
    if not o.salesperson_id:
        e = app.my_emp(request)
        if e is not None:
            o.salesperson = e
    o.created_by = o.created_by or request.user


def order_check(o, request, old):
    if old is not None and old.status not in ('quote', 'sent', 'approval', 'hold'):
        raise AppError('A confirmed order is not changed – cancel what is left, or return.')
    if o.partner_id and getattr(o.partner, 'status', 'active') == 'blocked':
        raise AppError(f'{o.partner} is blocked: {o.partner.blocked_reason or "see the partner"}.', 'partner')


def order_saver(o, request, creating, old):
    if not o.quote_number:
        o.quote_number = app.next_number('quote', 'SQ')
    o.save()
    S.totals(o)
    return o


def order_extra(o, request):
    out = {'totals': {'subtotal': money(o.net), 'tax': money(o.vat), 'currency': o.currency or S.settings().currency,   # v1.78.0 the keys of the Totals card
                      'net': money(o.net), 'vat': money(o.vat), 'total': money(o.total), 'cost': money(o.cost), 'margin': money(o.margin),
                      'margin_pct': round(float(o.margin / o.net * 100), 1) if o.net else None, 'invoiced': money(o.invoiced)}}
    warn = []
    if o.status in ('quote', 'sent', 'approval', 'hold'):
        warn += S.credit_problems(o) if S.settings().credit_control != 'off' else []
        mm = S.settings().min_margin
        if mm is not None and o.net and o.margin / o.net * 100 < mm:
            warn.append(f'Margin {o.margin / o.net * 100:.1f} % is below {mm:g} %.')
    if o.hold_reason:
        warn.insert(0, o.hold_reason)
    out['warnings'] = warn
    rows = [{'item': ln.description, 'ordered': float(ln.qty), 'delivered': float(ln.delivered), 'invoiced': float(ln.invoiced_qty), 'returned': float(ln.returned),
             'backorder': float(max(Decimal(0), ln.qty - ln.delivered))} for ln in o.lines.all()]
    out['tables'] = [tbl('Fulfilment', [('item', 'Item', 'text'), ('ordered', 'Ordered', 'number'), ('delivered', 'Delivered', 'number'), ('invoiced', 'Invoiced', 'number'),
                                        ('returned', 'Returned', 'number'), ('backorder', 'To deliver', 'number')], rows)]
    dv = [{'date': d(x.date), 'number': x.stock_number or '(services)', 'value': money(x.value)} for x in o.deliveries.all()]
    iv = [{'date': d(x.date), 'number': x.doc_number, 'kind': x.get_kind_display(), 'total': money(x.total)} for x in o.invoices.all()]
    if dv:
        out['tables'].append(tbl('Deliveries', [('date', 'Date', 'text'), ('number', 'Delivery', 'text'), ('value', 'Cost of goods', 'number')], dv))
    if iv:
        out['tables'].append(tbl('Invoices', [('date', 'Date', 'text'), ('number', 'Invoice', 'text'), ('kind', 'Kind', 'text'), ('total', 'Total', 'number')], iv))
    return out


def a_send(o, request, f):
    o.status = 'sent'
    o.save(update_fields=['status'])
    app.event('order', o.pk, 'Quotation sent', request.user)
    return {'message': f'Quotation {o.quote_number} marked as sent – print it or e-mail it from the record.'}


def a_confirm(o, request, f):
    mgr = app.any(request, *MGR)
    run(lambda: S.confirm(o, request.user, manager=mgr))
    app.event('order', o.pk, f'Status: {o.get_status_display()}' + (f' – {o.hold_reason}' if o.hold_reason else ''), request.user)
    if o.status == 'approval':
        return {'message': f'{o.hold_reason} – sent to the sales manager.'}
    if o.status == 'hold':
        return {'message': f'On credit hold: {o.hold_reason}'}
    return {'message': f'Confirmed as sales order {o.number}.' + (f' Note: {o.hold_reason}' if o.hold_reason else '')}


def a_release(o, request, f):
    o.status = 'quote' if o.status == 'approval' else o.status
    run(lambda: S.confirm(o, request.user, manager=True, release=True))
    app.event('order', o.pk, f'Approved / released by the sales manager: {f.s("note")}', request.user)
    return {'message': f'Released: sales order {o.number}.'}


def a_deliver(o, request, f):
    qtys = f.j('qtys') if f.has('qtys') else None
    dv = run(lambda: S.deliver(o, qtys or None, f.dt('date'), request.user))
    return {'message': f'Delivered{" – " + dv.stock_number if dv.stock_number else ""}; order {o.get_status_display().lower()}.'}


def a_invoice(o, request, f):
    pct = f.s('down_percent')
    inv = run(lambda: S.invoice(o, f.dt('date'), request.user, Decimal(pct) if pct else None))
    return {'message': f'{"Down payment invoice" if inv.kind == "down" else "Invoice"} {inv.doc_number} of {inv.total:,.2f} made in Accounting.'}


def a_cancel(o, request, f):
    if o.deliveries.exists() or o.invoices.exists():
        o.status = 'closed'
        msg = 'Closed: what is left is not delivered or invoiced.'
    else:
        o.status = 'cancelled'
        msg = 'Cancelled.'
    o.save(update_fields=['status'])
    app.event('order', o.pk, f'{msg} {f.s("note")}', request.user)
    return {'message': msg}


def a_lost(o, request, f):
    o.status, o.lost_reason = 'lost', f.s('note')[:120]
    o.save(update_fields=['status', 'lost_reason'])
    return {'message': 'Quotation lost.'}


def a_copy(o, request, f):
    n = Order.objects.create(partner=o.partner, date=today(), salesperson=o.salesperson, team=o.team, branch=o.branch, warehouse=o.warehouse, price_book=o.price_book,
                             payment_term=o.payment_term, currency=o.currency, emirate=o.emirate, notes=o.notes, terms=o.terms, created_by=request.user,
                             quote_number=app.next_number('quote', 'SQ'))
    for ln in o.lines.all():
        OrderLine.objects.create(order=n, product=ln.product, description=ln.description, qty=ln.qty, price=ln.price, discount=ln.discount, tax_code=ln.tax_code,
                                 net=ln.net, vat=ln.vat, unit_cost=ln.unit_cost, is_service=ln.is_service)
    S.totals(n)
    return {'message': f'Copied as quotation {n.quote_number}.', 'open': f'r/order/{n.pk}'}


def a_return(o, request, f):
    rt = Return.objects.create(order=o, date=today(), reason=f.s('note'),
                               lines=[{'line': ln.pk, 'qty': str(ln.delivered - ln.returned)} for ln in o.lines.all() if ln.delivered > ln.returned])
    return {'message': 'Return drafted with everything delivered – change the quantities and press Done.', 'open': f'r/return/{rt.pk}'}


def a_from_crm(o, request, f):
    """on the register: a CRM order or quotation → a Sales order (action of any order, as the kit has no register-level action)"""
    from ZeoCRM.models import Quotation as CQ, SalesOrder as CS
    co = CS.objects.filter(pk=f.i('crm_order')).first() if f.i('crm_order') else None
    cq = CQ.objects.filter(pk=f.i('crm_quote')).first() if f.i('crm_quote') else None
    n = run(lambda: S.from_crm(co, cq, request.user))
    return {'message': f'Quotation {n.quote_number} made from CRM {getattr(co or cq, "number", "")}.', 'open': f'r/order/{n.pk}'}


def line_defaults(o, request):
    if o.order_id:
        S.price_line(o)


def line_check(o, request, old):
    if o.order.status not in ('quote', 'sent', 'approval', 'hold'):
        raise AppError('A confirmed order is not changed.')
    if not o.product_id and not o.description:
        raise AppError('Choose the item or describe the line.', 'product')
    if old is not None and (old.qty != o.qty or old.product_id != o.product_id):
        if old.price == o.price:
            o.price = 0
        if old.discount == o.discount and o.rule_id:
            o.discount, o.rule = 0, None
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
    return {'message': f'Return {o.number} done' + (f'; stock back by {o.stock_number}' if o.stock_number else '') + (f'; credit note {o.credit_number}' if o.credit_number else '') + '.'}


def comm_pay(o, request, f):
    o.status = 'paid'
    o.save(update_fields=['status'])
    return {'message': 'Marked as paid out.'}


R = app.reg
QUOTE_ST = ('quote', 'sent', 'approval', 'hold')
R(Reg('order', Order, 'Quotation / order', 'Quotations and orders', 'Sales',
      cols=('number', 'quote_number', 'date', 'partner', 'salesperson', 'reference', 'total', 'margin', 'invoiced', 'delivery_date', 'status'),
      form=('partner', 'date', 'valid_until', 'delivery_date', 'salesperson', 'team', 'reference', 'warehouse', 'price_book', 'payment_term', 'currency', 'emirate',
            'invoice_policy', 'branch', 'notes', 'terms'),
      manage=MGR, view=VIEW, create=USERS, own=(('salesperson', 'emp'), ('created_by', 'user')), own_edit=True,
      search=('number', 'quote_number', 'reference', 'partner__name'), filters=('status', 'partner', 'salesperson', 'team', 'warehouse', 'branch'),
      title='number', subtitle='partner', defaults=order_defaults, check=order_check, saver=order_saver, extra=order_extra, deletable=True, pdf='Quotation',
      edit_when=lambda o: o.status in QUOTE_ST,
      info=('cost', 'margin', 'invoiced', 'down_payment', 'hold_reason', 'approved_by', 'confirmed_at', 'crm_quote', 'crm_order', 'lost_reason'),
      children=(('Lines', 'line', 'order'), ('Returns', 'return', 'order')),
      actions=[Act('send', 'Mark as sent', a_send, lambda o, r: o.status == 'quote' and app.any(r, *USERS)),
               Act('confirm', 'Confirm', a_confirm, lambda o, r: o.status in ('quote', 'sent') and app.any(r, *USERS), style='primary',
                   confirm='The quotation becomes a sales order (credit checked; a big discount or amount goes to the sales manager).'),
               Act('release', 'Approve / release', a_release, lambda o, r: o.status in ('approval', 'hold') and app.any(r, *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('deliver', 'Deliver', a_deliver, lambda o, r: o.status in ('confirmed', 'part_delivered') and app.any(r, *BILL), style='primary',
                   fields=(('date', 'Delivery date (empty: today)', 'date', False), ('qtys', 'Quantities by line {line id: qty} (empty: everything left)', 'json', False))),
               Act('invoice', 'Invoice', a_invoice, lambda o, r: o.status in ('confirmed', 'part_delivered', 'delivered') and app.any(r, *BILL),
                   fields=(('date', 'Invoice date (empty: today)', 'date', False), ('down_percent', 'Down payment % (empty: a normal invoice)', 'decimal', False))),
               Act('return', 'Return', a_return, lambda o, r: o.status in ('part_delivered', 'delivered', 'invoiced', 'closed') and app.any(r, *BILL), fields=(('note', 'Reason', 'text', False),)),
               Act('copy', 'Copy', a_copy, lambda o, r: app.any(r, *USERS)),
               Act('crm', 'Make an order from CRM', a_from_crm, lambda o, r: app.any(r, *USERS),
                   fields=(('crm_order', 'CRM sales order', 'opt:crm-orders', False), ('crm_quote', 'CRM quotation', 'opt:crm-quotes', False))),
               Act('lost', 'Lost', a_lost, lambda o, r: o.status in ('quote', 'sent') and app.any(r, *USERS), style='danger', fields=(('note', 'Why', 'text', True),)),
               Act('cancel', 'Cancel / close', a_cancel, lambda o, r: o.status in ('quote', 'sent', 'approval', 'hold', 'confirmed', 'part_delivered', 'delivered') and app.any(r, *MGR),
                   style='danger', fields=(('note', 'Why', 'text', False),))],
      help='A quotation becomes the sales order when it is confirmed; deliver (Inventory) and invoice (Accounting) all of it or part; returns give the stock back and a credit note.'))
R(Reg('line', OrderLine, 'Line', 'Lines', 'Sales', cols=('product', 'description', 'qty', 'price', 'discount', 'tax_code', 'net', 'vat', 'delivered', 'invoiced_qty', 'returned'),
      form=('order', 'product', 'description', 'qty', 'price', 'discount', 'tax_code'), manage=MGR, view=VIEW, create=USERS, child_of='order', search=('description', 'product__name'),
      filters=(), title='description', defaults=line_defaults, check=line_check, saver=line_saver, edit_when=lambda o: o.order.status in QUOTE_ST, files=False, deletable=True,
      info=('rule', 'net', 'vat', 'unit_cost', 'delivered', 'invoiced_qty', 'returned')))
R(Reg('return', Return, 'Customer return', 'Customer returns', 'Sales', cols=('number', 'date', 'order', 'reason', 'total', 'stock_number', 'credit_number', 'status'),
      form=('order', 'date', 'reason', 'lines', 'refund', 'restock'), manage=MGR, view=VIEW, create=BILL, search=('number', 'order__number', 'reason'), filters=('status',),
      title='number', subtitle='order', check=ret_check, edit_when=lambda o: o.status == 'draft', info=('stock_number', 'credit_number', 'total'),
      tables={'lines': [('line', 'Order line id', 'int'), ('qty', 'Quantity', 'decimal')]},
      actions=[Act('done', 'Done', a_ret_done, lambda o, r: o.status == 'draft' and app.any(r, *BILL), style='primary',
                   confirm='The stock comes back (Inventory) and a credit note is made (Accounting).')],
      help='Goods the customer sends back: back to stock and a credit note against the invoice.'))
R(Reg('delivery', Delivery, 'Delivery', 'Deliveries', 'Sales', cols=('order', 'date', 'stock_number', 'value'), form=(), manage=MGR, view=ALL, create='none',
      search=('order__number', 'stock_number'), filters=(), title='stock_number', files=False))
R(Reg('invoice', Invoice, 'Sales invoice', 'Sales invoices', 'Sales', cols=('order', 'kind', 'date', 'doc_number', 'total'), form=(), manage=MGR, view=ALL, create='none',
      search=('order__number', 'doc_number'), filters=('kind',), title='doc_number', files=False))
R(Reg('commission', Commission, 'Commission', 'Commissions', 'Sales', cols=('salesperson', 'order', 'date', 'base', 'percent', 'amount', 'status'), form=(),
      manage=MGR, view=('sal_viewer',), own=(('salesperson', 'emp'),), create='none', search=('order__number',), filters=('status', 'salesperson'), title='order', files=False,
      actions=[Act('pay', 'Paid out', comm_pay, lambda o, r: o.status == 'earned' and app.any(r, *MGR))],
      help='Commission of the salesperson on each invoice (the team\'s %), earned when invoiced or when paid (set-up).'))
R(Reg('team', Team, 'Sales team', 'Sales teams', 'Setup', cols=('name', 'leader', 'commission', 'is_active'), form=('name', 'leader', 'members', 'commission', 'is_active'),
      manage=MGR, view=ALL, search=('name',), filters=('is_active',), title='name', help='Salespeople in teams with the commission % of the team.'))
try:   # the shared price books of Masters (also on the CRM menu)
    from ZeoMasters.models import PaymentTerm as _PT2, PriceBook as _PB2, PriceBookItem as _PBI
    R(Reg('price_book', _PB2, 'Price book', 'Price books', 'Setup', cols=('code', 'name', 'currency', 'kind', 'percent', 'valid_from', 'valid_to', 'is_default', 'is_active'),
          form=('code', 'name', 'currency', 'kind', 'percent', 'valid_from', 'valid_to', 'is_default', 'is_active'), manage=MGR, view=ALL, search=('code', 'name'),
          filters=('kind', 'is_active'), title='name', deletable=True, files=False, children=(('Prices', 'price_item', 'price_book'),),
          help='Price lists by customer group: fixed prices per item (with quantity breaks) or a % on the list price. Shared with CRM.'))
    R(Reg('price_item', _PBI, 'Price', 'Prices', 'Setup', cols=('price_book', 'product', 'min_qty', 'price'), form=('price_book', 'product', 'min_qty', 'price'),
          manage=MGR, view=ALL, search=('product__name', 'price_book__name'), filters=('price_book',), title='product', subtitle='price_book', child_of='price_book',
          deletable=True, files=False))
    R(Reg('payment_term', _PT2, 'Payment term', 'Payment terms', 'Setup', cols=('code', 'name', 'kind', 'days', 'instalments', 'is_default', 'is_active'),
          form=('code', 'name', 'kind', 'days', 'instalments', 'is_default', 'is_active'), manage=MGR, view=ALL, search=('code', 'name'), filters=('is_active',), title='name',
          deletable=True, files=False, help='Due dates of the invoices (shared with CRM and Accounting).'))
except Exception:   # noqa: BLE001
    pass
R(Reg('rule', DiscountRule, 'Price rule', 'Price rules and promotions', 'Setup',
      cols=('name', 'kind', 'product', 'category', 'partner', 'min_qty', 'value', 'free_qty', 'date_from', 'date_to', 'priority', 'is_active'),
      form=('name', 'kind', 'product', 'category', 'partner', 'min_qty', 'value', 'free_qty', 'date_from', 'date_to', 'priority', 'is_active'), manage=MGR, view=ALL,
      search=('name',), filters=('kind', 'is_active'), title='name',
      help='Discounts and special prices by item, category or every item; for a customer; from a quantity; between dates. The lowest priority number wins.'))


# ------------------------------------------------------------------ dashboard, my work
def dashboard(request):
    t = today()
    qs = in_branches(Order.objects.all(), request)
    month = qs.filter(confirmed_at__date__gte=t.replace(day=1), status__in=('confirmed', 'part_delivered', 'delivered', 'invoiced', 'closed'))
    quotes = qs.filter(status__in=('quote', 'sent'))
    to_deliver = qs.filter(status__in=('confirmed', 'part_delivered'))
    to_invoice = [o for o in qs.filter(status__in=('part_delivered', 'delivered', 'confirmed')) if o.lines.filter(invoiced_qty__lt=F('delivered')).exists() or (S.policy(o) == 'ordered' and o.invoiced < o.total)]
    approval = qs.filter(status__in=('approval', 'hold'))
    cur = S.settings().currency
    by_sp = {}
    for o in month.select_related('salesperson'):
        k = str(o.salesperson or '–')
        by_sp[k] = by_sp.get(k, 0) + float(o.net)
    return {'cards': [{'label': f'Sales this month ({cur})', 'value': f'{sum(float(o.net) for o in month):,.2f}'},
                      {'label': 'Open quotations', 'value': quotes.count(), 'link': 'r/order?status=quote'},
                      {'label': f'Quotations value ({cur})', 'value': f'{sum(float(o.net) for o in quotes):,.2f}'},
                      {'label': 'Orders to deliver', 'value': to_deliver.count(), 'link': 'r/order?status=confirmed'},
                      {'label': 'To invoice', 'value': len(to_invoice), 'tone': 'bad' if to_invoice else 'ok'},
                      {'label': 'Waiting for approval / on hold', 'value': approval.count(), 'tone': 'bad' if approval.exists() else 'ok', 'link': 'r/order?status=approval'},
                      {'label': f'Margin this month ({cur})', 'value': f'{sum(float(o.margin) for o in month):,.2f}'}],
            'bars': [{'title': 'Sales this month by salesperson', 'items': [{'label': k, 'value': round(v, 2)} for k, v in sorted(by_sp.items(), key=lambda x: -x[1])]}],
            'lists': [{'title': 'To deliver', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.partner}', 'sub': f'promised {d(o.delivery_date)}' if o.delivery_date else '', 'badge': f'{o.total:,.2f}'}
                                                                        for o in to_deliver.select_related('partner').order_by('delivery_date')[:10]]},
                      {'title': 'Waiting for approval / on hold', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.quote_number} {o.partner}', 'sub': o.hold_reason[:80], 'badge': f'{o.total:,.2f}'}
                                                                                           for o in approval.select_related('partner')[:10]]}]}


def my_work(request):
    out = []
    me = app.my_emp(request)
    if me is not None:
        out.append({'title': 'My open quotations', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.quote_number} {o.partner}', 'badge': f'{o.total:,.2f}'}
                                                                           for o in Order.objects.filter(salesperson=me, status__in=('quote', 'sent'))[:30]]})
    if app.any(request, *MGR):
        out.append({'title': 'To approve', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.quote_number} {o.partner}', 'sub': o.hold_reason[:80]}
                                                                   for o in Order.objects.filter(status__in=('approval', 'hold'))[:30]]})
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, S.daily

from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)

from zeo.appkit.features import Feature as _F   # noqa: E402

app.features = [
    _F('price_rules', 'Price rules and promotions', 'Pricing', 'Discounts and special prices by item, category, customer, quantity and dates; buy X get Y.', regs=('rule',),
       fields={'line': ('rule',)}, in_use=lambda: DiscountRule.objects.count()),
    _F('approvals', 'Discount and amount approvals', 'Control', 'Orders with a discount or an amount above the set-up limits wait for the sales manager.',
       settings=('approve_discount', 'approve_amount', 'min_margin'), in_use=lambda: int(S.settings().approve_discount is not None or S.settings().approve_amount is not None)),
    _F('credit', 'Credit control', 'Control', 'Warn or hold an order over the credit limit or with overdue invoices.', settings=('credit_control', 'overdue_days'),
       in_use=lambda: int(S.settings().credit_control == 'block')),
    _F('returns', 'Customer returns', 'Fulfilment', 'Goods back to stock and a credit note.', regs=('return',), in_use=lambda: Return.objects.count()),
    _F('down_payments', 'Down payments', 'Billing', 'Advance invoices as a % of the order, deducted on the final invoice.', fields={'order': ('down_payment',)},
       in_use=lambda: Invoice.objects.filter(kind='down').count()),
    _F('commissions', 'Sales teams and commissions', 'People', 'Teams, the commission % and commissions on invoices (when invoiced or paid).', regs=('team', 'commission'),
       fields={'order': ('team',)}, settings=('commission_on',), in_use=lambda: Commission.objects.count()),
    _F('multi_currency', 'Sales in foreign currencies', 'Billing', 'Orders and invoices in USD, EUR … at the rate of Accounting.', fields={'order': ('currency',)},
       in_use=lambda: Order.objects.exclude(currency='').count()),
    _F('crm', 'Orders from CRM', 'Pipeline', 'A CRM quotation or sales order becomes a Sales order with its lines.', fields={'order': ('crm_quote', 'crm_order')},
       in_use=lambda: Order.objects.filter(Q(crm_quote__isnull=False) | Q(crm_order__isnull=False)).count()),
]
app.presets = {
    'trading': ('Trading', ('price_rules', 'credit', 'returns', 'down_payments')),
    'distribution': ('Distribution with a sales team', ('price_rules', 'approvals', 'credit', 'returns', 'commissions', 'multi_currency')),
    'all': ('Everything', tuple(f.key for f in app.features)),
}
