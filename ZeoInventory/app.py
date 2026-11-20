"""ZEO Inventory on the app kit (v1.75.0): warehouses and bins, stock documents (receipt, delivery, transfer, adjustment, count, scrap,
internal use, returns, opening stock), costing (average, FIFO, LIFO, standard, lot, serial) per company, branch or warehouse, landed
cost, perpetual or periodic accounting with journals to the Accounting outbox, the closing stock JV, period close and the
Inventory ↔ GL reconciliation. Items and partners are the shared masters (ZeoMasters)."""
from decimal import Decimal

from django.db.models import Q, Sum

from zeo.appkit.engine import Act, App, AppError, Reg, d, in_branches, today

from . import services as S
from .models import (KitComponent, Requisition, RequisitionLine, LotRecord, Attribute, AttributeValue, CategoryAttribute, ConsignmentPrice, ConsignmentSettlement, ItemAttribute, TemplateAxis, Variant, VariantTemplate, CountPlan, ItemLink, LINK_KINDS, PackingRule, Tag, ReorderRule, ACCOUNT_ROLES, CATEGORY_ROLES, PROD_ROLES, SET_ROLES, CategoryAccounts, METHODS, ROLES, AccountSet, Bin, ClosingJV, InvEvent, InvFile, InvSequence, InvSettings, ItemSetting, ItemUnit, LandedCharge, UnitSetting,
                     LandedCost, OperationType, PeriodClose, PutawayRule, Reason, Reservation, StandardCost, StockDoc, StockDocLine, StockMove, Warehouse)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-inventory/'
PREFIX = {'receipt': 'GRN', 'delivery': 'DN', 'transfer': 'TRF', 'adjustment': 'ADJ', 'count': 'CNT', 'scrap': 'SCR', 'consumption': 'ISS',
          'return_in': 'CRN', 'return_out': 'SRN', 'opening': 'OPN', 'dropship': 'DS'}

app = App(
    label='ZeoInventory', slug='zeo-inventory', title='Inventory', settings_model=InvSettings, sequence_model=InvSequence, event_model=InvEvent,
    file_model=InvFile, roles=ROLES, role_prefix='inv_', manager_role='inv_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'RET': 'retail', 'MFG': 'manufacturing', 'HC': 'food', 'FNB': 'food', 'TRD': 'retail'},
    pack_models={'reason': (Reason, 'code')},
    links=(('ZeoAccounting', 'Accounting', 'Stock journals (perpetual) and the closing stock JV (periodic) go to the accounting outbox.'),
           ('ZeoAssets', 'Asset Management', 'Spare parts are items of the shared item master; their stock can be kept here.')),
    page=PAGE,
)
app.settings_fields = ('currency', 'default_set', 'accounting_mode', 'posting_timing', 'costing_level', 'default_method', 'allow_lifo', 'ageing_buckets',
                      'dp_amount', 'dp_cost', 'dp_qty', 'dp_weight', 'dp_percent', 'adj_reason', 'adj_limit', 'provision_matrix',
                      'negative', 'soft_close_date', 'backdate_days')

MGR = ('inv_manager',)
STORE = ('inv_manager', 'inv_storekeeper')
ACCT = ('inv_manager', 'inv_accountant')
VIEW = ('inv_accountant', 'inv_viewer', 'inv_storekeeper')
ALL = ('inv_manager', 'inv_storekeeper', 'inv_accountant', 'inv_viewer')


def must(cond, message, field=None):
    """a wrong or missing value (400 with the message on the field) – need() is for rights (403)"""
    if not cond:
        raise AppError(message, field)


def run(fn):
    """services errors → the kit's error (field and message on the screen)"""
    try:
        return fn()
    except S.InvError as e:
        raise AppError(e.message, e.field)


def settings_check(s, request):
    old = InvSettings.objects.filter(pk=s.pk).first()
    if old is None:
        return
    moved = StockMove.objects.exists()
    if moved and old.costing_level != s.costing_level:
        raise AppError('The costing level cannot change once stock has moved (the costs are kept per level).', 'costing_level')
    if moved and old.accounting_mode != s.accounting_mode and s.lock_date is None:
        raise AppError('Close the stock period first, then change between perpetual and periodic from the next day.', 'accounting_mode')
    if s.default_method == 'lifo' and not s.allow_lifo:
        raise AppError('LIFO is not allowed under IFRS (IAS 2.25) – tick "offer LIFO" only for management reports.', 'default_method')
    for f, top in (('dp_amount', 3), ('dp_cost', 6), ('dp_qty', 4), ('dp_weight', 4), ('dp_percent', 3)):
        v = getattr(s, f)
        if v is not None and not 0 <= int(v) <= top:
            raise AppError(f'{InvSettings._meta.get_field(f).verbose_name.capitalize()}: 0 to {top} decimals (what is stored).', f)
    bad = [x for x in str(s.ageing_buckets or '').split(',') if x.strip() and not x.strip().isdigit()]
    if bad:
        raise AppError('Ageing buckets: days separated by commas, e.g. 30,60,90,180.', 'ageing_buckets')


app.settings_check = settings_check


# ------------------------------------------------------------------ options
def items_qs():
    from ZeoMasters.models import Product
    return Product.objects.filter(is_active=True).exclude(kind__in=('service', 'charge', 'fee'))


def item_label(x):
    return f'{x.number} {x.name}' + (f' · {x.code}' if x.code else '')


from ZeoMasters.models import Location as _Loc, Product as _Product, ProductCategory as _Cat  # noqa: E402

app.option('items', items_qs, item_label, ('number', 'name', 'code', 'barcode'), _Product)
app.option('categories', lambda: _Cat.objects.all(), lambda x: x.name, ('name', 'code'), _Cat)
app.option('locations', lambda: _Loc.objects.all(), lambda x: str(x), ('name', 'code'), _Loc)
app.option('warehouses', lambda: Warehouse.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), Warehouse)
app.option('bins', lambda: Bin.objects.filter(is_active=True).select_related('warehouse'), lambda x: str(x), ('code', 'name', 'warehouse__code'), Bin)
app.option('operations', lambda: OperationType.objects.filter(is_active=True).select_related('warehouse'), lambda x: str(x), ('name', 'code', 'warehouse__code'), OperationType)
app.option('reasons', lambda: Reason.objects.filter(is_active=True), lambda x: x.name, ('code', 'name'), Reason)
app.option('docs', lambda: StockDoc.objects.exclude(status='draft'), lambda x: f'{x.number} {x.get_kind_display()} {d(x.date)}', ('number', 'reference'), StockDoc)
app.option('receipts', lambda: StockDoc.objects.filter(status='posted', kind__in=('receipt', 'opening')),
           lambda x: f'{x.number} {d(x.date)} {x.partner or ""} {x.reference}'.strip(), ('number', 'reference'))
from ZeoMasters.models import UOM as _UOM  # noqa: E402
app.option('units', lambda: _UOM.objects.filter(is_active=True), lambda x: f'{x.code} – {x.name}', ('code', 'name'), _UOM)
app.option('estimates', lambda: LandedCost.objects.filter(kind='estimate', status='posted'), lambda x: f'{x.number} {x.shipment} ({x.total})', ('number', 'shipment'))
app.option('landed-costs', lambda: LandedCost.objects.all(), lambda x: f'{x.number} {x.shipment}', ('number', 'shipment'), LandedCost)
app.option('account-sets', lambda: AccountSet.objects.filter(is_active=True, parent__isnull=True).exclude(code=''), lambda x: f'{x.code} – {x.name}', ('code', 'name'), AccountSet)
app.option('tags', lambda: Tag.objects.filter(is_active=True), lambda x: x.name, ('name',), Tag)
app.provide('inventory.warehouses', lambda: Warehouse.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'))
app.provide('inventory.docs', lambda: StockDoc.objects.all(), lambda x: f'{x.number} {x.get_kind_display()} ({x.get_status_display()})', ('number',))


# ------------------------------------------------------------------ warehouses, bins
def wh_saver(o, request, creating, old):
    if not o.code:
        o.code = app.next_number('warehouse', 'WH')
    if Warehouse.objects.filter(code=o.code).exclude(pk=o.pk).exists():
        raise AppError('This code is already used.', 'code')
    if not o.shared_location_id:   # the store in the shared list of locations (Masters › Stores and locations)
        from ZeoMasters.locations import save_shared
        from ZeoMasters.models import Location
        o.shared_location = save_shared(Location(name=o.name[:150], code=o.code[:30], kind='store', branch_id=o.branch_id, address=o.address[:255]))
    o.save()
    if creating:
        standard_operations(o)
    return o


STD_OPS = (('Receipts', 'receipt', 'IN'), ('Deliveries', 'delivery', 'OUT'), ('Internal transfers', 'transfer', 'INT'), ('Adjustments', 'adjustment', 'ADJ'),
           ('Stock counts', 'count', 'CNT'), ('Scrap', 'scrap', 'SCR'), ('Internal use', 'consumption', 'ISS'), ('Customer returns', 'return_in', 'RET'),
           ('Supplier returns', 'return_out', 'RTS'), ('Opening stock', 'opening', 'OPN'))


def standard_operations(w):
    """Odoo: a new warehouse comes with its operation types (own number series ZTA-IN-00001 …); deliveries return through Customer
    returns, receipts through Supplier returns; scrap and internal use with their default reasons"""
    if w.operations.exists():
        return
    made = {}
    for i, (name, kind, suffix) in enumerate(STD_OPS):
        reason = None
        if kind in ('scrap', 'consumption'):
            reason = Reason.objects.filter(kind=kind, is_active=True).order_by('pk').first()
        made[kind] = OperationType.objects.create(warehouse=w, name=name, kind=kind, code=f'{w.code}-{suffix}'[:20], sequence=(i + 1) * 10, reason=reason)
    OperationType.objects.filter(pk=made['delivery'].pk).update(return_type=made['return_in'])
    OperationType.objects.filter(pk=made['receipt'].pk).update(return_type=made['return_out'])


def wh_extra(o, request):
    rows = S.valuation(group='item', warehouse=o)
    t = tbl('Stock in this warehouse', [('item', 'Item', 'text'), ('qty', 'On hand', 'number'), ('unit_cost', 'Unit cost', 'number'), ('value', 'Value', 'number')], rows[:1000])
    if len(rows) > 1000:   # v1.79.3 the page says so and opens the full list (before: the first 200 items, without saying so)
        t.update({'count': len(rows), 'more_link': f'/main-sidebar/report-options/r/inv-valuation', 'more_label': 'Stock on hand and value'})
    return {'stock': rows[:1000], 'stock_value': round(sum(r['value'] for r in rows), 2), 'tables': [t]}


# ------------------------------------------------------------------ documents
def doc_defaults(o, request):
    op = o.operation if o.operation_id else None
    if op is not None:
        o.kind, o.warehouse = op.kind, op.warehouse
        if op.kind == 'transfer' and not o.to_warehouse_id and op.to_warehouse_id:
            o.to_warehouse = op.to_warehouse
        if not o.reason_id and op.reason_id:
            o.reason = op.reason
    if not o.date:
        o.date = today()
    if not o.document_date:
        o.document_date = o.date


def doc_scope(qs, request):
    if app.is_admin(request) or app.any(request, 'inv_manager', 'inv_accountant', 'inv_viewer'):
        return qs
    ok = S.allowed_warehouses(request.user)
    return qs if ok is None else qs.filter(Q(warehouse_id__in=ok) | Q(to_warehouse_id__in=ok))


def doc_check(o, request, old):
    if o.operation_id:
        o.kind, o.warehouse_id = o.operation.kind, o.operation.warehouse_id
    must(o.kind, 'Choose the operation type.')
    if o.kind == 'dropship':   # v1.75.0 no stock of ours in between
        if not o.warehouse_id or o.warehouse.code != 'DROPSHIP':
            o.warehouse = S.dropship_warehouse()
        must(o.partner_id, 'Choose the customer the supplier delivers to.', 'partner')
        must(o.supplier_id, 'Choose the supplier who delivers.', 'supplier')
    must(o.warehouse_id, 'Choose the warehouse.')
    ok = S.allowed_warehouses(request.user) if not app.any(request, 'inv_manager') else None   # v1.75.0 warehouse rights
    if ok is not None:
        for w, f_ in ((o.warehouse, 'warehouse'), (o.to_warehouse, 'to_warehouse')):
            if w is not None and w.pk not in ok and not (f_ == 'to_warehouse' and o.kind == 'transfer'):
                raise AppError(f'You do not work in {w.code} – ask the inventory manager.', f_)
    if o.currency and o.currency.upper() != S.settings().currency.upper() and o.kind in ('receipt', 'opening', 'dropship', 'return_out'):
        o.currency = o.currency.upper()
        if not o.rate:
            r = S.fx_rate(o.currency, o.date)
            must(r, f'Enter the exchange rate of {o.currency} (Accounting has none for the date).', 'rate')
            o.rate = r
    elif o.currency:
        o.currency, o.rate = '', None
    if o.kind == 'transfer':
        must(o.to_warehouse_id, 'Choose the warehouse to transfer to.')
        if o.to_warehouse_id == o.warehouse_id:
            raise AppError('Transfer to another warehouse.', 'to_warehouse')
    elif o.to_warehouse_id:
        o.to_warehouse = None
    if o.kind in ('adjustment', 'scrap', 'consumption') and o.reason_id and o.reason.kind != o.kind and not (o.kind == 'adjustment'):
        raise AppError(f'"{o.reason}" is a reason for {o.reason.get_kind_display().lower()}.', 'reason')
    if o.return_of_id:
        want = {'return_in': ('delivery',), 'return_out': ('receipt', 'opening')}.get(o.kind)
        if not want or o.return_of.kind not in want or o.return_of.status != 'posted':
            raise AppError('A customer return is of a posted delivery; a return to the supplier of a posted receipt.', 'return_of')
    lk = S.settings().lock_date
    if lk and o.date and o.date <= lk:
        raise AppError(f'The stock period is closed up to {d(lk)} – choose a later date.', 'date')


def doc_saver(o, request, creating, old):
    if old is not None and old.kind != o.kind and o.lines.exists():
        raise AppError('The kind cannot change once lines are added.', 'kind')
    if creating and not o.number:
        op = o.operation if o.operation_id else None
        o.number = app.next_number(f'op_{op.pk}', op.code) if op is not None and op.code else app.next_number(f'doc_{o.kind}', PREFIX.get(o.kind, 'STK'))
    if creating and request is not None and getattr(request.user, 'is_authenticated', False):
        o.created_by = request.user
    o.save()
    return o


def a_post(o, request, f):
    st = S.settings()
    mgr = app.any(request, 'inv_manager')
    warn = []
    if st.soft_close_date and o.date <= st.soft_close_date:   # v1.75.0 soft close: only the manager, with a warning
        if not mgr:
            raise AppError(f'The stock period is soft-closed up to {d(st.soft_close_date)} – the inventory manager posts on or before it.', 'date')
        warn.append(f'Posted in the soft-closed period (up to {d(st.soft_close_date)}).')
    if st.backdate_days is not None and (today() - o.date).days > st.backdate_days and not mgr:
        raise AppError(f'Dated {(today() - o.date).days} days back – more than {st.backdate_days}, so the inventory manager posts it '
                       f'(see "Later documents whose cost changes" on the document).', 'date')
    if o.kind == 'adjustment' and st.adj_limit and not app.any(request, 'inv_manager'):   # v1.75.0 approval above the value limit
        ch = sum((abs(Decimal(str(r['change']))) for r in S.adjust_preview(o, st)), Decimal(0))
        if ch > st.adj_limit:
            raise AppError(f'This adjustment changes the stock value by {ch:,.2f} – above {st.adj_limit:,.2f}, so the inventory manager posts it '
                           f'(it stays a draft; the manager sees it on the Operations board).')
    run(lambda: S.post(o, request.user))
    warn += S.warnings()
    o.refresh_from_db()
    app.event('doc', o.pk, f'Posted: {o.total_qty.normalize():f} units, value {o.total_value}' + (' – ' + ' '.join(warn) if warn else ''), request.user)
    return {'message': f'{o.number} posted. Stock value {o.total_value} {S.settings().currency}.' + (' ⚠ ' + ' '.join(warn) if warn else ''),
            'warnings': warn}


def a_grid(o, request, f):
    tpl = f.obj('template', VariantTemplate, True, 'Item with variants')
    raw = f.s('grid')
    import json
    try:
        grid = json.loads(raw) if raw.strip().startswith('{') else {k.strip(): v.strip() for k, v in (x.split('=') for x in raw.replace('\n', ';').split(';') if '=' in x)}
    except Exception:   # noqa: BLE001
        raise AppError('Enter the grid as VALUE/VALUE=quantity; … (e.g. RED/L=5; BLUE/M=2).', 'grid')
    made = run(lambda: S.grid_into(o, tpl, grid))
    return {'message': f'{len(made)} lines added from the grid.'}


def a_serials(o, request, f):
    """bulk serial numbers: one per line (pasted or scanned), or a range SN0001-SN0050"""
    import re
    from django.apps import apps as dj
    P = dj.get_model('ZeoMasters', 'Product')
    p = f.obj('product', P, True, 'Item')
    must(S.tracking_of(p) == 'serial', f'{p} is not tracked by serial number.', 'product')
    raw = f.s('serials')
    sers = []
    for part in re.split(r'[\n;, ]+', raw):
        part = part.strip()
        m = re.match(r'^(.*?)(\d+)-(.*?)(\d+)$', part)
        if m and m.group(1) == m.group(3) and len(m.group(2)) == len(m.group(4)):
            a, b = int(m.group(2)), int(m.group(4))
            must(b >= a and b - a < 5000, 'A range runs low to high, up to 5,000 numbers.', 'serials')
            sers += [f'{m.group(1)}{i:0{len(m.group(2))}d}' for i in range(a, b + 1)]
        elif part:
            sers.append(part)
    must(sers, 'Enter the serial numbers.', 'serials')
    dup = [x for x in set(sers) if sers.count(x) > 1] + list(o.lines.filter(product=p, serial__in=sers).values_list('serial', flat=True))
    must(not dup, f'Twice: {", ".join(sorted(set(dup))[:10])}.', 'serials')
    cost = f.n('unit_cost', None)
    for x in sers:
        StockDocLine.objects.create(doc=o, product=p, uom=p.uom, qty=1 if o.kind != 'count' else 0, counted_qty=1 if o.kind == 'count' else None, serial=x,
                                    unit_cost=cost if o.kind in ('receipt', 'opening') else None)
    return {'message': f'{len(sers)} serial numbers added ({sers[0]} … {sers[-1]}).'}


def a_scan(o, request, f):
    b = f.obj('bin', Bin, False, 'Bin') if f.has('bin') else None
    r, ln = run(lambda: S.scan_into(o, f.s('code'), f.n('qty', None) or 1, b))
    if ln is None:
        return {'message': f'Bin {r["bin"].code} – scan the items now.', 'bin': r['bin'].pk}
    q = ln.counted_qty if o.kind == 'count' else ln.qty
    return {'message': f'{ln.product} {("lot " + ln.lot) if ln.lot else ""}{("serial " + ln.serial) if ln.serial else ""} – {Decimal(q).normalize():f} '
                       f'{ln.uom.code if ln.uom_id else ""} on the line.', 'line': ln.pk}


def a_receive(o, request, f):
    left = S.shipment_left(o)
    qtys = f.j('qtys') if f.has('qtys') else None
    if not qtys:
        qtys = {r['line']: r['left'] for r in left if r['left'] > 0}   # all that is in transit
    a = run(lambda: S.receive_transfer(o, qtys, f.dt('date') or today(), request.user))
    app.event('doc', o.pk, f'Received: {a.number}', request.user)
    return {'message': f'{a.number}: received into {o.to_warehouse.code}.', 'open': f'r/doc/{a.pk}'}


def a_short(o, request, f):
    qtys = f.j('qtys') if f.has('qtys') else None
    if not qtys:
        qtys = {r['line']: r['left'] for r in S.shipment_left(o) if r['left'] > 0}
    a = run(lambda: S.close_short(o, qtys, f.dt('date') or today(), request.user, f.s('note')))
    app.event('doc', o.pk, f'Closed short: {a.number} – {f.s("note")}', request.user)
    return {'message': f'{a.number}: written off from transit (short / damaged).'}


def a_dispatch(o, request, f):
    o.delivery_status = 'out'
    o.driver, o.vehicle = f.s('driver') or o.driver, f.s('vehicle') or o.vehicle
    o.save(update_fields=['delivery_status', 'driver', 'vehicle'])
    app.event('doc', o.pk, f'Out for delivery – {o.driver} {o.vehicle}'.strip(), request.user)
    return {'message': f'{o.number} is out for delivery.'}


def a_deliver(o, request, f):
    from django.utils import timezone
    st_ = f.s('status') or 'delivered'
    must(st_ in ('delivered', 'partly', 'refused'), 'Choose delivered, partly delivered or refused.')
    must(f.s('received_by') or st_ == 'refused', 'Enter who received it.', 'received_by')
    o.delivery_status, o.delivered_at, o.pod_name = st_, timezone.now(), f.s('received_by')
    o.pod_signature, o.pod_photo, o.pod_gps = f.s('signature')[:200000], f.s('photo')[:400000], f.s('gps')[:60]
    o.save(update_fields=['delivery_status', 'delivered_at', 'pod_name', 'pod_signature', 'pod_photo', 'pod_gps'])
    app.event('doc', o.pk, f'Proof of delivery: {o.get_delivery_status_display()} – {o.pod_name} {o.pod_gps}'.strip(), request.user)
    return {'message': f'{o.number}: {o.get_delivery_status_display().lower()}.'}


def a_reverse(o, request, f):
    on = f.dt('date') or today()
    rev = run(lambda: S.reverse(o, request.user, on))
    app.event('doc', o.pk, f'Reversed by {rev.number}' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return {'message': f'Reversed by {rev.number}.', 'open': f'r/doc/{rev.pk}'}


def a_cancel(o, request, f):
    o.status = 'cancelled'
    o.save(update_fields=['status'])
    app.event('doc', o.pk, 'Cancelled' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_return(o, request, f):
    kind = 'return_in' if o.kind == 'delivery' else 'return_out'
    op = o.operation.return_type if o.operation_id and o.operation.return_type_id else \
        OperationType.objects.filter(warehouse=o.warehouse, kind=kind, is_active=True).order_by('sequence').first()
    num = app.next_number(f'op_{op.pk}', op.code) if op is not None and op.code else app.next_number(f'doc_{kind}', PREFIX[kind])
    r = StockDoc.objects.create(branch_id=o.branch_id, number=num, kind=kind, date=today(), document_date=today(), operation=op,
                                warehouse=op.warehouse if op is not None else o.warehouse, partner=o.partner, reference=f'Return of {o.number}', return_of=o,
                                created_by=request.user)
    for ln in o.lines.all():
        StockDocLine.objects.create(doc=r, product=ln.product, qty=ln.qty, lot=ln.lot, serial=ln.serial, expiry=ln.expiry, bin=ln.bin, note=f'From {o.number}')
    app.event('doc', o.pk, f'Return {r.number} started', request.user)
    return {'message': f'{r.number} made with the lines of {o.number} – change the quantities and post it.', 'open': f'r/doc/{r.pk}'}


def doc_extra(o, request):
    moves = [{'item': str(m.product), 'warehouse': m.warehouse.code, 'kind': m.kind, 'qty': float(m.qty), 'lot': m.lot, 'serial': m.serial,
              'value': float(m.value), 'unit_cost': float(round(abs(m.value) / abs(m.qty), 4)) if m.qty else 0}
             for m in StockMove.objects.filter(doc=o).select_related('product', 'warehouse').order_by('seq')]
    out = {'moves': moves, 'journals': S.journals_of(doc=o)}
    tabs = []
    if o.kind == 'count' and o.status != 'draft':
        out['count'] = [{'item': str(x.product), 'expected': float(x.expected_qty or 0), 'counted': float(x.counted_qty or 0),
                         'difference': float(x.qty or 0), 'value': float(x.value or 0)} for x in o.lines.select_related('product')]
        tabs.append(tbl('Count result', [('item', 'Item', 'text'), ('expected', 'Expected', 'number'), ('counted', 'Counted', 'number'),
                                         ('difference', 'Difference', 'number'), ('value', 'Value', 'number')], out['count']))
    if o.status == 'draft' and o.lines.exists() and StockMove.objects.filter(product_id__in=o.lines.values('product_id'), date__gt=o.date).exists():
        imp = S.backdate_impact(o)
        if imp and 'error' not in imp[0]:
            out['impact'] = imp
            tabs.append(tbl('Later documents whose cost changes (back-dated)', [('number', 'Document', 'text'), ('date', 'Date', 'date'), ('kind', 'Kind', 'text'),
                                                                                ('before', 'Cost now', 'number'), ('after', 'Cost after posting', 'number'),
                                                                                ('change', 'Change', 'number')], imp))
        elif imp:
            out['impact_error'] = imp[0]['error']
    if o.kind == 'transfer' and o.two_step and o.status == 'posted':
        sl = S.shipment_left(o)
        out['shipment'] = sl
        tabs.append(tbl('In transit', [('item', 'Item', 'text'), ('lot', 'Lot / serial', 'text'), ('shipped', 'Shipped', 'number'), ('received', 'Received', 'number'),
                                       ('short', 'Short / damaged', 'number'), ('left', 'Still in transit', 'number')],
                        [{**r, 'shipped': float(r['shipped']), 'received': float(r['received']), 'short': float(r['short']), 'left': float(r['left'])} for r in sl]))
    if o.kind in ('delivery', 'consumption') and o.status == 'draft':   # v1.75.0 short items: their alternatives with stock
        alts = []
        for ln in o.lines.select_related('product'):
            short = Decimal(ln.base_qty or ln.qty or 0) - S.available(ln.product, o.warehouse)
            if short > 0:
                for a in S.alternatives(ln.product, o.warehouse):
                    alts.append({'line': ln.pk, 'item': str(ln.product), 'short': float(short), 'alternative': a['item'], 'alternative_id': a['item_id'],
                                 'available': float(a['available'])})
        if alts:
            out['alternatives'] = alts
            tabs.append(tbl('Short – alternatives with stock', [('item', 'Item', 'text'), ('short', 'Short', 'number'), ('alternative', 'Alternative', 'text'),
                                                                ('available', 'Available', 'number')], alts))
    if o.lines.exists():
        out['packages'] = [{'item': str(ln.product), 'qty': S.packs_text(ln.product, ln.base_qty or ln.qty)} for ln in o.lines.select_related('product', 'product__uom')]
    if o.kind == 'adjustment' and o.status == 'draft' and o.lines.exists():
        try:
            out['preview'] = S.adjust_preview(o)
            tabs.append(tbl('Preview: before → after', [('item', 'Item', 'text'), ('type', 'Adjustment', 'text'), ('qty_before', 'Quantity before', 'number'),
                                                        ('qty_after', 'Quantity after', 'number'), ('unit_before', 'Unit cost before', 'number'),
                                                        ('unit_after', 'Unit cost after', 'number'), ('value_before', 'Value before', 'number'),
                                                        ('value_after', 'Value after', 'number')], out['preview']))
        except S.InvError as e:
            out['preview_error'] = e.message
    if moves:
        tabs.append(tbl('Stock moves', [('item', 'Item', 'text'), ('warehouse', 'Warehouse', 'text'), ('lot', 'Lot / serial', 'text'), ('qty', 'Quantity', 'number'),
                                        ('unit_cost', 'Unit cost', 'number'), ('value', 'Value', 'number')], [{**m, 'lot': m['lot'] or m['serial']} for m in moves]))
    if out['journals']:
        tabs.append(tbl('Journals', JCOLS, jrows(out['journals'])))
    out['tables'] = tabs
    return out


# ------------------------------------------------------------------ lines
def line_defaults(o, request):
    if not (o.doc_id and o.product_id):
        return
    k = o.doc.kind
    if o.uom_id is None:   # the item's purchase unit on what comes from suppliers, its sales unit on what goes to customers
        flag = 'is_purchase' if k in ('receipt', 'return_out') else 'is_sales' if k in ('delivery', 'return_in') else None
        iu = ItemUnit.objects.filter(product_id=o.product_id, is_active=True, **{flag: True}).first() if flag else None
        o.uom = iu.uom if iu is not None else o.product.uom
    if o.unit_cost is None and k in ('receipt', 'opening'):
        c = o.product.purchase_price or o.product.cost_price or None
        if c is not None:
            try:
                c = Decimal(c) * S.unit_factor(o.product, o.uom)
            except S.InvError:
                pass
        o.unit_cost = c


def line_check(o, request, old):
    must(o.doc.status == 'draft', 'The document is posted – lines cannot change.')
    must(o.product_id, 'Choose the item.')
    k = o.doc.kind
    if k == 'count':
        must(o.counted_qty is not None and o.counted_qty >= 0, 'Enter the counted quantity.')
    elif k == 'adjustment':   # v1.75.0 adjustment types as Sage 300: quantity, cost or both – increase or decrease
        t = o.adj_type or ('qty_out' if (o.qty or 0) < 0 else 'qty_in')
        o.adj_type = t
        if t in S.COST_ONLY:
            must(o.cost_amount and o.cost_amount > 0, 'Enter the cost amount to add or take off the stock held.', 'cost_amount')
            if S.method_of(o.product) == 'standard':
                raise AppError(f'{o.product} is at standard cost – change its cost in Standard costs (a new cost from a date).', 'adj_type')
            o.qty, o.unit_cost = 0, None
        else:
            must(o.qty, 'Enter the quantity.', 'qty')
            o.qty = abs(o.qty) * (1 if t.endswith('_in') else -1)
            if t in ('both_in', 'both_out'):
                must(o.cost_amount and o.cost_amount > 0, 'Enter the cost amount of the quantity.', 'cost_amount')
                if t == 'both_in':
                    o.unit_cost = S.rcost(Decimal(o.cost_amount) / abs(Decimal(o.qty)))
            else:
                o.cost_amount = None
        if o.cost_amount is not None and S.too_precise(o.cost_amount, S.dp('amount', S.settings())):
            raise AppError(f'Amount allows {S.dp("amount", S.settings())} decimals.', 'cost_amount')
    else:
        must(o.qty and o.qty > 0, 'The quantity must be more than 0.')
    tr = S.tracking_of(o.product)
    cost_only = k == 'adjustment' and o.adj_type in S.COST_ONLY
    taking = k in ('delivery', 'scrap', 'consumption', 'return_out', 'transfer') or (k == 'adjustment' and (o.qty or 0) < 0) or cost_only
    if tr in ('lot', 'serial') and not (o.lot or o.serial) and not taking:   # v1.75.0 numbered from the item's prefix
        nl = S.next_lot(o.product, o.doc.date)
        if nl:
            if tr == 'lot':
                o.lot = nl
            else:
                o.serial = nl
    if tr == 'lot' and not o.lot and not taking:   # what goes out may leave the lot to the removal strategy (FEFO / FIFO)
        raise AppError(f'{o.product} is tracked by lot – enter the lot.', 'lot')
    if tr == 'serial':
        if not o.serial:
            raise AppError(f'{o.product} is tracked by serial number – enter it.', 'serial')
        if k != 'count' and not cost_only and abs(o.qty) != 1:
            raise AppError('A serial number is one unit.', 'qty')
    if o.bin_id and o.bin.warehouse_id != o.doc.warehouse_id:
        raise AppError('The bin is in another warehouse.', 'bin')
    if o.to_bin_id and o.to_bin.warehouse_id != o.doc.to_warehouse_id:
        raise AppError('The "to" bin is in another warehouse.', 'to_bin')
    if S.method_of(o.product) == 'lifo' and not S.settings().allow_lifo:
        raise AppError(f'{o.product} uses LIFO, which the set-up does not offer (not IFRS).', 'product')
    st = S.settings()
    for f, kind, label in (('qty', 'qty', 'Quantity'), ('counted_qty', 'qty', 'Counted'), ('unit_cost', 'cost', 'Unit cost'),
                           ('weight', 'weight', 'Weight'), ('volume', 'weight', 'Volume')):
        if S.too_precise(getattr(o, f), S.dp(kind, st)):
            raise AppError(f'{label} allows {S.dp(kind, st)} decimals.', f)
    try:
        f = S.unit_factor(o.product, o.uom)
    except S.InvError as e:
        raise AppError(e.message, 'uom')
    unit = o.uom or o.product.uom
    step = S.unit_rounding(unit)
    for fld in ('qty', 'counted_qty'):
        v = getattr(o, fld)
        if step and v is not None and Decimal(v) % step:
            raise AppError(f'{unit.code} is counted in steps of {step.normalize():f} – {Decimal(v).normalize():f} is not one.', fld)
    base_step = S.unit_rounding(o.product.uom) if o.product.uom_id else None
    if base_step and o.qty and (Decimal(o.qty) * f) % base_step:
        raise AppError(f'{Decimal(o.qty).normalize():f} {unit.code} is {(Decimal(o.qty) * f).normalize():f} {o.product.uom.code} – not a whole step of '
                       f'{base_step.normalize():f} {o.product.uom.code}.', 'qty')
    iu = ItemUnit.objects.filter(product_id=o.product_id, uom_id=o.uom_id, is_active=True).first() if o.uom_id else None
    if iu is not None and iu.full_only and o.qty and Decimal(o.qty) % 1:
        if iu.full_only == 'round':   # v1.75.0 full packages only: rounded up
            o.qty = (Decimal(o.qty) // 1) + 1
        else:
            raise AppError(f'{o.uom.code} is sold in full packages – {Decimal(o.qty).normalize():f} is not one (enter {int(Decimal(o.qty)) + 1} or the loose units).', 'qty')
    if iu is not None and o.qty:   # weight and volume from the packaging (landed cost by weight / volume, shipping)
        if not o.weight and iu.gross_kg:
            o.weight = S.rq(abs(Decimal(o.qty)) * Decimal(iu.gross_kg))
        if not o.volume and iu.length_cm and iu.width_cm and iu.height_cm:
            o.volume = S.rq(abs(Decimal(o.qty)) * Decimal(iu.length_cm) * Decimal(iu.width_cm) * Decimal(iu.height_cm) / Decimal(1000000))
    cw = S.item_setting(o.product)
    if cw is not None and cw.catch_weight and k in ('receipt', 'opening', 'return_in', 'delivery', 'return_out') and not o.weight:
        raise AppError(f'{o.product} is a catch-weight item – enter the actual weight (kg); the unit cost is per kg.', 'weight')
    S.line_base(o, save=False, st=st)


# ------------------------------------------------------------------ landed cost
def lc_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('landed', 'LC')
    o.save()
    return o


def a_add_receipt(o, request, f):
    rid = f.i('receipt', True, 'Goods receipt')
    must(StockDoc.objects.filter(pk=rid, status='posted', kind__in=('receipt', 'opening')).exists(), 'Choose a posted goods receipt.')
    ids = [int(x) for x in (o.receipts or []) if str(x).isdigit()]
    if rid not in ids:
        ids.append(rid)
    o.receipts = ids
    o.save(update_fields=['receipts'])
    return o


def a_clear_receipts(o, request, f):
    o.receipts = []
    o.save(update_fields=['receipts'])
    return o


def a_preview(o, request, f):
    rows = run(lambda: S.allocate(o))
    return {'message': f'Split of {sum(r["amount"] for r in rows):,.2f}: {sum(r["to_stock"] for r in rows):,.2f} to stock held, '
                       f'{sum(r["to_cogs"] for r in rows):,.2f} to stock already used (COGS).', 'data': rows}


def a_post_landed(o, request, f):
    run(lambda: S.post_landed(o, request.user))
    app.event('landed', o.pk, f'Posted: {o.to_stock} to stock, {o.to_cogs} to cost of sales', request.user)
    return {'message': f'{o.number} posted: {o.to_stock} to stock held, {o.to_cogs} to stock already used.'}


def lc_extra(o, request):
    recs = [{'id': x.pk, 'number': x.number, 'date': d(x.date), 'partner': str(x.partner or ''), 'value': float(x.total_value)}
            for x in StockDoc.objects.filter(pk__in=[int(i) for i in (o.receipts or []) if str(i).isdigit()])]
    out = {'receipts_list': recs, 'allocation': o.allocation, 'journals': S.journals_of(landed=o) if o.status == 'posted' else []}
    if o.status == 'draft' and recs and o.charges.exists():
        try:
            out['preview'] = S.allocate(o)
        except S.InvError as e:
            out['preview_error'] = e.message
    split = o.allocation if o.status == 'posted' else out.get('preview')
    tabs = [tbl('Goods receipts', [('number', 'Number', 'text'), ('date', 'Date', 'text'), ('partner', 'Supplier', 'text'), ('value', 'Value', 'number')], recs)]
    if split:
        tabs.append(tbl('Split' if o.status == 'posted' else 'Split (preview)', [('item', 'Item', 'text'), ('qty', 'Received', 'number'), ('in_stock', 'Still held', 'number'),
                                                                                 ('amount', 'Charges', 'number'), ('to_stock', 'To stock', 'number'), ('to_cogs', 'To COGS', 'number')], split))
    if out['journals']:
        tabs.append(tbl('Journals', JCOLS, jrows(out['journals'])))
    out['tables'] = tabs
    return out


def charge_check(o, request, old):
    must(o.landed.status == 'draft', 'The landed cost is posted.')
    if o.charge == 'vat':   # v1.75.0 import VAT is recoverable input VAT, never part of the stock cost
        raise AppError('Import VAT is not a landed cost – it is input VAT: book the customs VAT in Accounting as input VAT, not here.', 'charge')
    st = S.settings()
    if o.currency and o.currency.upper() != st.currency.upper():   # v1.75.0 a bill in a foreign currency
        o.currency = o.currency.upper()
        must(o.fc_amount and o.fc_amount > 0, f'Enter the amount in {o.currency}.', 'fc_amount')
        if not o.rate:
            o.rate = S.fx_rate(o.currency, o.landed.date)
            must(o.rate, f'Enter the exchange rate of {o.currency}.', 'rate')
        o.amount, o.percent = S.q2(Decimal(o.fc_amount) * Decimal(o.rate)), None
    elif o.currency:
        o.currency, o.rate, o.fc_amount = '', None, None
    if o.percent is not None:
        must(0 < o.percent <= 1000, 'The % must be more than 0.', 'percent')
        if S.too_precise(o.percent, S.dp('percent', st)):
            raise AppError(f'% allows {S.dp("percent", st)} decimals.', 'percent')
        o.amount = 0   # worked out from the goods value of the receipts
    else:
        must(o.amount and o.amount > 0, 'Enter the amount or the % of the goods value.', 'amount')
        if S.too_precise(o.amount, S.dp('amount', st)):
            raise AppError(f'Amount allows {S.dp("amount", st)} decimals.', 'amount')


def lc_check(o, request, old):
    if o.kind == 'estimate':
        o.estimate = None
    elif o.estimate_id:
        e = o.estimate
        if e.kind != 'estimate' or e.status != 'posted':
            raise AppError('Choose a posted estimate that is not settled yet.', 'estimate')
        if not o.receipts:
            o.receipts = list(e.receipts or [])


# ------------------------------------------------------------------ item settings, standard cost, account sets
def a_change_set(o, request, f):
    new = f.obj('account_set', AccountSet, True, 'New account set')
    must(new.kind == 'item' and not new.parent_id, 'Choose a main item account set.')
    must(new.pk != o.account_set_id, 'The item already uses this set.')
    made = run(lambda: S.change_item_set(o, new, f.dt('date') or today(), request.user))
    app.event('item', o.pk, f'Account set → {new.code}' + (f'; stock value moved ({len(made) // 2} transfer)' if made else ''), request.user)
    return {'message': f'{o.product} now uses {new.code}' + ('; its stock value moved to the new Inventory account.' if made else '.')}


def item_check(o, request, old):
    if o.account_set_id and (o.account_set.kind != 'item' or o.account_set.parent_id):
        raise AppError('Choose a main item account set.', 'account_set')
    if o.production_set_id and o.production_set.kind != 'production':
        raise AppError('Choose a production account set.', 'production_set')
    held = S.on_hand(o.product) if o.product_id else 0
    if old is not None and old.account_set_id != o.account_set_id and (held or S.StockValue.objects.filter(product_id=o.product_id).exists()):
        cur = S.q2(S.StockValue.objects.filter(product_id=o.product_id, in_stock=True).aggregate(s=Sum('amount'))['s'] or 0)
        if held or cur:
            raise AppError('The item holds stock – use "Change the account set" (its stock value moves to the new Inventory account with a transfer journal).', 'account_set')
    if old is None and not o.method and o.account_set_id and o.account_set.default_method:
        o.method = o.account_set.default_method
    if o.method == 'lifo' and not S.settings().allow_lifo:
        raise AppError('LIFO is not allowed under IFRS (IAS 2.25). Tick "offer LIFO" in the set-up for management reports only.', 'method')
    moved = StockMove.objects.filter(product_id=o.product_id).exists()
    if old is not None and moved:
        if (old.method or '') != (o.method or ''):
            raise AppError('The costing method cannot change once the item has moved. Use a new item, or move the stock out and back in.', 'method')
        if old.tracking != o.tracking:
            raise AppError('Lot / serial tracking cannot change once the item has moved.', 'tracking')
    if old is None and moved and (o.method or o.tracking != 'none'):
        cur_method = S.method_of(o.product)
        if o.method and o.method != cur_method or o.tracking != 'none':
            raise AppError('The item has already moved with the company default – its method and tracking cannot change now.', 'method')
    if o.method == 'serial' and o.tracking != 'serial':
        o.tracking = 'serial'
    if o.method == 'lot' and o.tracking != 'lot':
        o.tracking = 'lot'


def item_extra(o, request):
    p = o.product
    bw = S.valuation(group='item_warehouse', product=p)
    return {'on_hand': float(S.on_hand(p)), 'value': float(S.q2(sum((Decimal(str(r['value'])) for r in S.valuation(product=p)), Decimal(0)))),
            'method': dict(METHODS).get(S.method_of(p), ''), 'by_warehouse': bw,
            'accounts': S.accounts_used(p), 'related': S.related(p), 'tags': S.tags_of(p), 'missing_attributes': S.missing_attributes(p),
            'attributes': [{'attribute': x.attribute.name, 'value': x.shown} for x in ItemAttribute.objects.filter(product=p).select_related('attribute')],
            'tables': [tbl('Related items', [('kind_label', 'Kind', 'text'), ('item', 'Item', 'text'), ('qty', 'Default quantity', 'number')], S.related(p)),
                       tbl('Accounts this item uses', [('role', 'Account role', 'text'), ('account', 'Account', 'text'), ('from', 'From', 'text')], S.accounts_used(p)),
                       tbl('Stock by warehouse', [('warehouse', 'Warehouse', 'text'), ('qty', 'On hand', 'number'), ('unit_cost', 'Unit cost', 'number'), ('value', 'Value', 'number')], bw),
                       tbl('Units', [('unit', 'Unit', 'text'), ('factor', 'Stock units in one', 'text'), ('use', 'Default for', 'text')],
                           [{'unit': f'{u.uom.code} – {u.uom.name}', 'factor': f'{u.factor.normalize():f} {p.uom.code if p.uom_id else ""}',
                             'use': ', '.join(x for x, y in (('purchase', u.is_purchase), ('sales', u.is_sales)) if y)} for u in ItemUnit.objects.filter(product=p).select_related('uom')])]}


def std_saver(o, request, creating, old):
    """a standard cost from a date revalues the stock held then"""
    if old is not None:
        raise AppError('A standard cost is kept as history – add a new one from a later date.')
    run(lambda: S.revalue_standard(o.product, o.cost, o.effective_from, request.user, o.note))
    return StandardCost.objects.get(product=o.product, effective_from=o.effective_from)


ROLE_CHOICES = '|'.join(f'{k}={n}' for k, n, _ in ACCOUNT_ROLES)
ALL_ROLE_CHOICES = '|'.join(f'{k}={n}' for k, n, _ in ACCOUNT_ROLES + PROD_ROLES if k not in CATEGORY_ROLES)
CAT_ROLE_CHOICES = '|'.join(f'{k}={n}' for k, n, _ in ACCOUNT_ROLES if k in CATEGORY_ROLES)


def accset_defaults(o, request):
    if o.parent_id and not o.name:
        o.name = f'{o.parent.code or o.parent.name} × {o.warehouse.code if o.warehouse_id else "?"}'


def accset_check(o, request, old):
    o.code = (o.code or '').strip().upper()
    if o.parent_id:   # a warehouse override of a set
        must(o.warehouse_id, 'A warehouse override needs the warehouse.', 'warehouse')
        must(not o.parent.parent_id, 'Choose a main account set.', 'parent')
        o.kind = o.parent.kind
        if AccountSet.objects.filter(parent_id=o.parent_id, warehouse_id=o.warehouse_id).exclude(pk=o.pk or 0).exists():
            raise AppError('This set already has an override for that warehouse.', 'warehouse')
        if not o.name:
            o.name = f'{o.parent.code or o.parent.name} × {o.warehouse.code}'
    else:
        must(o.code, 'Enter the code of the set (TRADE, SPARES, RAW, FG …).', 'code')
        if AccountSet.objects.filter(code=o.code, parent__isnull=True).exclude(pk=o.pk or 0).exists():
            raise AppError(f'{o.code} is already an account set.', 'code')
        o.warehouse = None
        o.category = None


def a_set_account(o, request, f):
    role = f.s('role')
    must(role in {k for k, _, _ in ACCOUNT_ROLES + PROD_ROLES}, 'Choose the account role.')
    acc = dict(o.accounts or {})
    if f.s('account'):
        acc[role] = f.s('account')[:40]
    else:
        acc.pop(role, None)
    o.accounts = acc
    o.save(update_fields=['accounts'])
    return o


def acc_extra(o, request):
    st = S.settings()
    src = PROD_ROLES if o.kind == 'production' else tuple(x for x in ACCOUNT_ROLES if x[0] in SET_ROLES)
    rows = [{'role': n, 'code': k, 'account': (o.accounts or {}).get(k, ''), 'default': (st.accounts or {}).get(k) or c} for k, n, c in src]
    used = ItemSetting.objects.filter(account_set=o).count() if o.kind == 'item' else ItemSetting.objects.filter(production_set=o).count()
    return {'roles': rows, 'items': used,
            'tables': [tbl('Accounts', [('role', 'Account role', 'text'), ('account', 'Account in this set', 'text'), ('default', 'Company default', 'text')], rows)]}


def cat_extra(o, request):
    st = S.settings()
    rows = [{'role': n, 'code': k, 'account': (o.accounts or {}).get(k, ''), 'default': (st.accounts or {}).get(k) or c} for k, n, c in ACCOUNT_ROLES if k in CATEGORY_ROLES]
    return {'roles': rows, 'tables': [tbl('Accounts', [('role', 'Account role', 'text'), ('account', 'Account of this category', 'text'), ('default', 'Company default', 'text')], rows)]}


# ------------------------------------------------------------------ closing stock JV (periodic), period close (perpetual)
def jv_saver(o, request, creating, old):
    if creating:
        jv = run(lambda: S.propose_closing(o.period_end))
        return jv
    o.save()
    return o


def a_repropose(o, request, f):
    run(lambda: S.propose_closing(o.period_end))
    return {'message': 'The proposal is worked out again from the valuation at the date.'}


def a_post_jv(o, request, f):
    run(lambda: S.post_closing(o, request.user))
    app.event('closing', o.pk, f'Posted; stock period closed up to {d(o.period_end)}', request.user)
    return {'message': f'Closing stock JV posted; the stock period is closed up to {d(o.period_end)}.'}


def jv_extra(o, request):
    rec = S.reconciliation(o.period_end)
    return {'proposal': o.lines, 'reconciliation': rec,
            'tables': [tbl('Proposal', [('account', 'Inventory account', 'text'), ('opening', 'In the GL', 'number'), ('closing', 'Stock value', 'number'),
                                        ('change', 'Change', 'number'), ('change_account', 'Against', 'text')], o.lines or []), rec_table(rec)]}


def pc_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('close', 'SPC')
    o.save()
    return o


def a_provision(o, request, f):
    b, need = run(lambda: S.post_provision(o.period_end, request.user))
    app.event('close', o.pk, f'Inventory provision {need:,.2f}' + (f' – journal {b.number}' if b else ' – no change'), request.user)
    return {'message': f'Provision needed {need:,.2f}' + (f'; journal {b.number} for the change.' if b else '; already booked.')}


def a_close(o, request, f):
    st = S.settings()
    must(st.accounting_mode == 'perpetual', 'In periodic accounting the period closes when its closing stock JV is posted.')
    run(lambda: S.close_period(o.period_end, request.user))
    rec = S.reconciliation(o.period_end)
    o.valuation = S.q2(sum((Decimal(str(r['valuation'])) for r in rec['rows']), Decimal(0)))
    o.gl_balance = S.q2(sum((Decimal(str(r['gl'])) for r in rec['rows']), Decimal(0)))
    o.difference = o.valuation - o.gl_balance
    o.status, o.closed_by = 'closed', request.user
    o.snapshot = [{k: r.get(k) for k in ('item', 'warehouse', 'qty', 'unit_cost', 'value')} for r in S.valuation(o.period_end, group='item_warehouse')]
    from django.utils import timezone
    o.closed_at = timezone.now()
    o.save()
    app.event('close', o.pk, f'Closed: stock {o.valuation}, GL {o.gl_balance}', request.user)
    return {'message': f'Stock period closed up to {d(o.period_end)}. Stock value {o.valuation}, GL {o.gl_balance}.'}


def a_reopen(o, request, f):
    st = S.settings()
    later = PeriodClose.objects.filter(status='closed', period_end__gt=o.period_end).exists() or \
        ClosingJV.objects.filter(status='posted', period_end__gt=o.period_end).exists()
    must(not later, 'A later period is closed – reopen that one first.')
    prev = PeriodClose.objects.filter(status='closed', period_end__lt=o.period_end).order_by('-period_end').first()
    st.lock_date = prev.period_end if prev else None
    st.save(update_fields=['lock_date'])
    o.status = 'reopened'
    o.save(update_fields=['status'])
    app.event('close', o.pk, 'Reopened: ' + f.s('note'), request.user)
    return o


def a_adjust(o, request, f):
    n = run(lambda: S.adjust_all(request.user))
    made = run(lambda: S.post_to_gl(S.settings(), user=request.user)) if S.settings().accounting_mode == 'perpetual' else []
    return {'message': f'Cost adjustment run: {n} cost amount(s) added; {len(made)} journal(s) to Accounting.'}


def pc_extra(o, request):
    rec = S.reconciliation(o.period_end)
    tabs = [rec_table(rec)]
    if o.snapshot:
        tabs.append(tbl(f'Stock valuation at the close ({d(o.period_end)}, as closed)', [('item', 'Item', 'text'), ('warehouse', 'Warehouse', 'text'),
                                                                                        ('qty', 'On hand', 'number'), ('unit_cost', 'Unit cost', 'number'),
                                                                                        ('value', 'Value', 'number')], o.snapshot))
    return {'reconciliation': rec, 'pending': S.StockValue.objects.filter(gl='pending').count(), 'snapshot': o.snapshot, 'tables': tabs}


# ------------------------------------------------------------------ reservations
def res_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('reservation', 'RSV')
    if o.status == 'active':
        free = S.available(o.product, o.warehouse) + (old.qty if old is not None and old.status == 'active' else 0)
        if o.qty > free:
            raise AppError(f'Only {free.normalize():f} of {o.product} is free in {o.warehouse.code}.', 'qty')
    if creating and getattr(request.user, 'is_authenticated', False):
        o.created_by = request.user
    o.save()
    return o


def tbl(label, cols, rows):
    """a table on the record page (the kit's generic tables): cols [(key, label, 'text' | 'number')]"""
    return {'name': '_' + label.lower().replace(' ', '_')[:30], 'label': label, 'columns': [{'key': k, 'label': lb, 'type': t} for k, lb, t in cols], 'rows': rows}


JCOLS = [('journal', 'Journal', 'text'), ('date', 'Date', 'text'), ('account', 'Account', 'text'), ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number')]


def jrows(journals):
    return [{'journal': j['number'], 'date': j['date'], **x} for j in journals for x in j['lines']]


def rec_table(rec):
    return tbl('Inventory ↔ GL reconciliation', [('account', 'Inventory account', 'text'), ('valuation', 'Stock valuation', 'number'), ('gl', 'GL balance', 'number'),
                                                ('unposted', 'Not yet in the GL', 'number'), ('difference', 'Difference', 'number')], rec['rows'])


def op_check(o, request, old):
    for f, label in (('default_bin', 'default bin'), ):
        b = getattr(o, f)
        if b is not None and b.warehouse_id != o.warehouse_id:
            raise AppError(f'The {label} is in another warehouse.', f)
    if o.default_to_bin_id and o.default_to_bin.warehouse_id != (o.to_warehouse_id or 0):
        raise AppError('The default "to" bin must be in the "to" warehouse.', 'default_to_bin')
    if o.kind != 'transfer' and (o.to_warehouse_id or o.default_to_bin_id):
        o.to_warehouse, o.default_to_bin = None, None
    if o.to_warehouse_id == o.warehouse_id and o.to_warehouse_id:
        raise AppError('Transfer to another warehouse.', 'to_warehouse')
    if o.return_type_id and o.return_type.kind not in ('return_in', 'return_out'):
        raise AppError('Returns go through a customer return or supplier return type.', 'return_type')
    o.code = (o.code or '').strip().upper()


BIN_KINDS = {'storage', 'picking', 'receiving', 'dispatch', 'quarantine', 'damaged'}


def putaway_check(o, request, old):
    if o.method in ('fixed', 'receiving'):
        must(o.bin_id, 'Choose the bin.')
    if o.bin_id and o.bin.warehouse_id != o.warehouse_id:
        raise AppError('The bin is in another warehouse.', 'bin')
    if o.bin_kind and o.bin_kind not in BIN_KINDS:
        raise AppError('Bin type: storage, picking, receiving, dispatch, quarantine or damaged.', 'bin_kind')
    if o.product_id and o.category_id:
        o.category = None


def unit_check(o, request, old):
    must(o.rounding and o.rounding > 0, 'The rounding step must be more than 0 (1 = whole units, 0.001 = thousandths).', 'rounding')


def item_unit_check(o, request, old):
    must(o.factor and o.factor > 0, 'How many stock units make one – more than 0.', 'factor')
    if o.product.uom_id and o.uom_id == o.product.uom_id:
        raise AppError("This is the item's stock unit already.", 'uom')
    if old is not None and old.factor != o.factor and StockDocLine.objects.filter(product_id=o.product_id, uom_id=o.uom_id, doc__status='draft').exists():
        pass   # drafts are converted again when they are posted
    for f in ('is_purchase', 'is_sales'):
        if getattr(o, f):
            ItemUnit.objects.filter(product_id=o.product_id, **{f: True}).exclude(pk=o.pk or 0).update(**{f: False})


def bin_saver(o, request, creating, old):
    if not o.code:
        o.code = '-'.join(x for x in (o.zone, o.aisle, o.rack, o.shelf) if x)[:30]
    must(o.code, 'Enter the code, or the zone / aisle / rack / shelf it is made from.')
    if Bin.objects.filter(warehouse_id=o.warehouse_id, code=o.code).exclude(pk=o.pk).exists():
        raise AppError(f'{o.code} is already a bin of this warehouse.', 'code')
    o.save()
    return o


def when(states, *roles):
    return lambda o, r: o.status in states and app.any(r, *roles)


# ------------------------------------------------------------------ registers
R = app.reg
R(Reg('doc', StockDoc, 'Stock document', 'Stock documents', 'Stock',
      cols=('number', 'kind', 'date', 'warehouse', 'to_warehouse', 'partner', 'reference', 'total_qty', 'total_value', 'status'),
      form=('operation', 'kind', 'date', 'document_date', 'warehouse', 'to_warehouse', 'partner', 'supplier', 'reason', 'reference', 'charge_to', 'return_of',
            'two_step', 'expected_date', 'currency', 'rate', 'ship_to', 'site_contact', 'site_phone', 'requested_date', 'ship_method', 'driver', 'vehicle', 'tags', 'note'),
      manage=STORE, view=VIEW, search=('number', 'reference', 'charge_to', 'partner__name'),
      filters=('operation', 'kind', 'status', 'warehouse', 'to_warehouse', 'partner', 'reason', 'delivery_status', 'tags'), title='number', subtitle='kind',
      defaults=doc_defaults, check=doc_check, saver=doc_saver, edit_when=lambda o: o.status == 'draft', extra=doc_extra, scope_fn=doc_scope,
      info=('total_qty', 'total_value', 'posted_at', 'posted_by', 'reversal_of', 'origin_app', 'origin_ref'),
      children=(('Lines', 'line', 'doc'), ('Returns', 'doc', 'return_of')), deletable=True,
      actions=[Act('serials', 'Serial numbers (bulk)', a_serials, lambda o, r: o.status == 'draft' and app.any(r, *STORE),
                   fields=(('product', 'Item', 'opt:items', True), ('serials', 'Serial numbers (one per line, or a range SN0001-SN0050)', 'text', True),
                           ('unit_cost', 'Unit cost (receipts)', 'decimal', False))),
               Act('grid', 'Size × colour grid', a_grid, lambda o, r: o.status == 'draft' and app.any(r, *STORE),
                   fields=(('template', 'Item with variants', 'opt:templates', True), ('grid', 'Quantities (RED/L=5; BLUE/M=2)', 'text', True))),
               Act('scan', 'Scan', a_scan, when(('draft',), *STORE), fields=(('code', 'Barcode / QR / lot / serial / bin', 'text', True), ('qty', 'Quantity', 'decimal', False))),
               Act('receive', 'Receive', a_receive, lambda o, r: o.kind == 'transfer' and o.two_step and o.status == 'posted' and o.transit_status in ('transit', 'partly')
                   and app.any(r, *STORE), style='primary', fields=(('date', 'Received on', 'date', False), ('qtys', 'Quantities by line {line id: qty} (empty: all)', 'json', False))),
               Act('short', 'Close short / damaged', a_short, lambda o, r: o.kind == 'transfer' and o.two_step and o.status == 'posted'
                   and o.transit_status in ('transit', 'partly') and app.any(r, 'inv_manager'), style='danger',
                   fields=(('note', 'What happened', 'text', True), ('qtys', 'Quantities by line (empty: all still in transit)', 'json', False))),
               Act('dispatch', 'Out for delivery', a_dispatch, lambda o, r: o.kind in ('delivery', 'dropship') and o.status == 'posted' and o.delivery_status in ('', 'ready')
                   and app.any(r, *STORE), fields=(('driver', 'Driver', 'text', False), ('vehicle', 'Vehicle', 'text', False))),
               Act('deliver', 'Proof of delivery', a_deliver, lambda o, r: o.kind in ('delivery', 'dropship') and o.status == 'posted' and app.any(r, *STORE),
                   fields=(('status', 'Result', 'choice:delivered=Delivered|partly=Partly delivered|refused=Refused', True), ('received_by', 'Received by', 'text', False),
                           ('signature', 'Signature', 'text', False), ('photo', 'Photo', 'text', False), ('gps', 'GPS', 'text', False))),
               Act('post', 'Post', a_post, when(('draft',), *STORE), style='primary',
                   confirm='Posting moves the stock and its value; a posted document is corrected only by reversing it.'),
               Act('return', 'Make a return', a_return, lambda o, r: o.status == 'posted' and o.kind in ('delivery', 'receipt') and app.any(r, *STORE)),
               Act('reverse', 'Reverse', a_reverse, lambda o, r: o.status == 'posted' and not o.reversal_of_id and app.any(r, 'inv_manager', 'inv_accountant'),
                   style='danger', fields=(('date', 'Reversal date (empty: today)', 'date', False), ('note', 'Why', 'text', True))),
               Act('cancel', 'Cancel', a_cancel, when(('draft',), *STORE), style='danger', fields=(('note', 'Why', 'text', False),))],
      help='Every stock movement: receive, deliver, transfer, adjust, count, scrap, use internally, return, opening stock. '
           'Post to move the stock and its value; reverse to correct.'))
R(Reg('line', StockDocLine, 'Line', 'Lines', 'Stock',
      cols=('product', 'adj_type', 'qty', 'uom', 'unit_cost', 'cost_amount', 'counted_qty', 'base_qty', 'lot', 'serial', 'expiry', 'bin', 'to_bin', 'weight', 'value'),
      form=('doc', 'product', 'owner', 'adj_type', 'uom', 'qty', 'unit_cost', 'cost_amount', 'counted_qty', 'lot', 'serial', 'expiry', 'bin', 'to_bin', 'weight', 'volume',
            'return_reason', 'condition', 'decision', 'note'),
      manage=STORE, view=VIEW, child_of='doc', search=('product__name', 'product__number', 'lot', 'serial'), filters=(), title='product',
      defaults=line_defaults, check=line_check, edit_when=lambda o: o.doc.status == 'draft', files=False, deletable=True,
      info=('expected_qty', 'factor', 'base_qty', 'base_cost', 'value')))
R(Reg('landed', LandedCost, 'Landed cost', 'Landed costs', 'Costing',
      cols=('number', 'kind', 'date', 'shipment', 'estimate', 'total', 'to_stock', 'to_cogs', 'status'), form=('kind', 'date', 'shipment', 'estimate', 'note'),
      manage=ACCT, view=('inv_viewer', 'inv_storekeeper'), search=('number', 'shipment'), filters=('kind', 'status'), title='number', subtitle='shipment',
      check=lc_check, saver=lc_saver, edit_when=lambda o: o.status == 'draft', extra=lc_extra, info=('receipts', 'total', 'to_stock', 'to_cogs', 'posted_at'),
      children=(('Charges', 'charge', 'landed'),), deletable=True,
      actions=[Act('add_receipt', 'Add a goods receipt', a_add_receipt, when(('draft',), *ACCT), fields=(('receipt', 'Goods receipt', 'opt:receipts', True),)),
               Act('clear_receipts', 'Remove the receipts', a_clear_receipts, lambda o, r: o.status == 'draft' and bool(o.receipts) and app.any(r, *ACCT)),
               Act('preview', 'Preview the split', a_preview, lambda o, r: o.status == 'draft' and app.any(r, *ACCT)),
               Act('post', 'Post', a_post_landed, when(('draft',), *ACCT), style='primary',
                   confirm='The charges go to the stock still held; the part on stock already used goes to cost of sales.')],
      help='Freight, duty, insurance, clearing … added to the cost of the goods received: split by value, quantity, weight, volume or by hand.'))
R(Reg('charge', LandedCharge, 'Charge', 'Charges', 'Costing', cols=('charge', 'partner', 'percent', 'amount', 'basis', 'invoice'),
      form=('landed', 'charge', 'partner', 'currency', 'fc_amount', 'rate', 'amount', 'percent', 'basis', 'manual', 'invoice'), manage=ACCT, view=('inv_viewer',), child_of='landed',
      search=('invoice',), filters=('charge', 'basis'), title='charge', check=charge_check, edit_when=lambda o: o.landed.status == 'draft', files=False,
      deletable=True))
R(Reg('reservation', Reservation, 'Reservation', 'Reservations', 'Stock', cols=('number', 'product', 'warehouse', 'qty', 'reference', 'until', 'status'),
      form=('product', 'warehouse', 'qty', 'reference', 'until', 'status'), manage=STORE, view=VIEW, search=('number', 'reference', 'product__name'),
      filters=('status', 'warehouse'), title='number', subtitle='product', saver=res_saver, deletable=True,
      help='Stock held for an order or project: it is not available for other deliveries.'))
R(Reg('closing', ClosingJV, 'Closing stock JV', 'Closing stock JVs', 'Costing', cols=('number', 'period_end', 'total_change', 'status', 'journal_id'),
      form=('period_end', 'note'), manage=ACCT, view=('inv_viewer',), search=('number',), filters=('status',), title='number', subtitle='period_end',
      saver=jv_saver, edit_when=lambda o: o.status == 'proposed', extra=jv_extra, info=('lines', 'total_change', 'journal_id', 'posted_at'),
      actions=[Act('repropose', 'Work out again', a_repropose, when(('proposed',), *ACCT)),
               Act('post', 'Post', a_post_jv, when(('proposed',), *ACCT), style='primary',
                   confirm='The closing stock JV goes to Accounting and the stock period closes at its date.')],
      help='Periodic accounting: at the month end the stock value at the date is booked against the change in inventory.'))
R(Reg('close', PeriodClose, 'Period close', 'Period closes', 'Costing', cols=('number', 'period_end', 'valuation', 'gl_balance', 'difference', 'status'),
      form=('period_end', 'note'), manage=ACCT, view=('inv_viewer',), search=('number',), filters=('status',), title='number', subtitle='period_end',
      saver=pc_saver, edit_when=lambda o: o.status == 'draft', extra=pc_extra, info=('valuation', 'gl_balance', 'difference', 'closed_by', 'closed_at'),
      actions=[Act('adjust', 'Run the cost adjustment', a_adjust, lambda o, r: o.status != 'closed' and app.any(r, *ACCT)),
               Act('provision', 'Post the inventory provision', a_provision, lambda o, r: app.any(r, *ACCT),
                   confirm='Dr Inventory write-down / Cr Inventory provision for the change the provision matrix asks for at this date.'),
               Act('close', 'Close the period', a_close, when(('draft', 'reopened'), *ACCT), style='primary',
                   confirm='Nothing can be dated on or before this date afterwards; later corrections go to the first open day.'),
               Act('reopen', 'Reopen', a_reopen, lambda o, r: o.status == 'closed' and app.is_admin(r) or (o.status == 'closed' and app.any(r, *MGR)),
                   style='danger', fields=(('note', 'Why', 'text', True),))],
      help='Perpetual accounting: run the cost adjustment, post every cost amount and close the stock period.'))
R(Reg('warehouse', Warehouse, 'Warehouse', 'Warehouses', 'Setup', cols=('code', 'name', 'branch', 'shared_location', 'removal', 'allow_negative', 'is_transit', 'is_active'),
      form=('code', 'name', 'branch', 'address', 'shared_location', 'removal', 'negative', 'allow_negative', 'is_transit', 'tags', 'users', 'is_active'), manage=MGR, view=ALL,
      search=('code', 'name'), filters=('is_active', 'branch'), title='name', subtitle='code', saver=wh_saver, extra=wh_extra,
      children=(('Operation types', 'operation', 'warehouse'), ('Bins', 'bin', 'warehouse'), ('Put-away rules', 'putaway', 'warehouse')),
      help='Stores and warehouses. Each is also a store in Masters › Stores and locations.'))
R(Reg('bin', Bin, 'Bin', 'Bins', 'Setup', cols=('warehouse', 'code', 'zone', 'aisle', 'rack', 'shelf', 'kind', 'name', 'is_active'),
      form=('warehouse', 'zone', 'aisle', 'rack', 'shelf', 'code', 'kind', 'name', 'is_active'),
      manage=MGR, view=ALL, child_of='warehouse', search=('code', 'name', 'zone', 'aisle', 'rack'), filters=('warehouse', 'kind', 'zone', 'is_active'),
      title='code', subtitle='kind', saver=bin_saver, files=False, deletable=True,
      help='Places inside a warehouse: zone, aisle, rack and shelf; the code is made from them when left empty (A-01-R2-S3).'))
R(Reg('operation', OperationType, 'Operation type', 'Operation types', 'Setup',
      cols=('warehouse', 'name', 'kind', 'code', 'default_bin', 'to_warehouse', 'return_type', 'reason', 'needs_lots', 'is_active'),
      form=('warehouse', 'name', 'kind', 'code', 'sequence', 'default_bin', 'to_warehouse', 'default_to_bin', 'return_type', 'reason', 'needs_lots', 'is_active'),
      manage=MGR, view=ALL, child_of='warehouse', search=('name', 'code'), filters=('warehouse', 'kind', 'is_active'), title='name', subtitle='kind',
      check=op_check, files=False, deletable=True, children=(('Documents', 'doc', 'operation'),),
      help='Receipts, deliveries, transfers … of each warehouse (as Odoo): own number series, default bins, the return type. A new document starts from one.'))
R(Reg('putaway', PutawayRule, 'Put-away rule', 'Put-away rules', 'Setup', cols=('warehouse', 'priority', 'product', 'category', 'method', 'bin', 'zone', 'bin_kind', 'is_active'),
      form=('warehouse', 'priority', 'product', 'category', 'method', 'bin', 'zone', 'bin_kind', 'is_active'), manage=MGR, view=ALL, child_of='warehouse',
      search=('product__name', 'zone'), filters=('warehouse', 'method', 'is_active'), title='method', check=putaway_check, files=False, deletable=True,
      help='Where goods coming in are put when a line has no bin: by item, item category or any item – a fixed bin, the bin already holding the item, an empty bin or the receiving bin.'))
R(Reg('unit', UnitSetting, 'Unit rounding', 'Unit rounding', 'Setup', cols=('uom', 'rounding'), form=('uom', 'rounding'), manage=MGR, view=ALL,
      search=('uom__code', 'uom__name'), filters=(), title='uom', files=False, deletable=True, check=unit_check,
      help='How finely each unit is counted: PCS 1 (whole pieces), KG 0.001, LTR 0.01. The units themselves are in Masters › Units of measure.'))
R(Reg('item_unit', ItemUnit, 'Item unit', 'Item units', 'Setup', cols=('product', 'uom', 'factor', 'is_purchase', 'is_sales', 'barcode', 'is_active'),
      form=('product', 'uom', 'factor', 'level', 'is_purchase', 'is_sales', 'barcode', 'full_only', 'length_cm', 'width_cm', 'height_cm', 'gross_kg', 'net_kg',
            'is_active'), manage=MGR, view=ALL,
      search=('product__name', 'product__number', 'uom__code', 'barcode'), filters=('uom', 'is_purchase', 'is_sales', 'is_active'), title='product', subtitle='uom',
      check=item_unit_check, files=False, deletable=True,
      help="The other units an item is bought, sold or moved in, with the item's own conversion (1 CTN = 24 PCS); the default purchase and sales units."))
R(Reg('item', ItemSetting, 'Item stock setting', 'Item stock settings', 'Setup',
      cols=('product', 'method', 'tracking', 'removal', 'standard_cost', 'min_qty', 'reorder_qty', 'shelf_life_days'),
      form=('product', 'method', 'tracking', 'removal', 'catch_weight', 'standard_cost', 'account_set', 'production_set', 'negative', 'returnable', 'deposit', 'lot_prefix', 'min_life_days', 'tags', 'min_qty', 'reorder_qty', 'shelf_life_days', 'note'),
      manage=MGR, view=ALL,
      search=('product__name', 'product__number', 'product__code'), filters=('method', 'tracking', 'account_set', 'tags', 'returnable'), title='product', check=item_check, extra=item_extra,
      actions=[Act('change_set', 'Change the account set', a_change_set, lambda o, r: app.any(r, 'inv_manager', 'inv_accountant'),
                   fields=(('account_set', 'New account set', 'opt:account-sets', True), ('date', 'Date of the transfer journal', 'date', False)),
                   confirm='The stock value held moves from the old Inventory account to the new one (a transfer journal); later postings use the new set.')],
      files=False, help='How an item of the item master is costed and tracked; reorder point. Items without a row use the company default.'))
R(Reg('standard', StandardCost, 'Standard cost', 'Standard costs', 'Setup', cols=('product', 'effective_from', 'cost', 'note'),
      form=('product', 'effective_from', 'cost', 'note'), manage=('inv_manager', 'inv_accountant'), view=ALL, search=('product__name', 'product__number'),
      filters=('product',), title='product', saver=std_saver, edit_when=lambda o: False, files=False,
      help='Standard cost items: a new cost from a date revalues the stock held then (revaluation account).'))
R(Reg('accounts', AccountSet, 'Account set', 'Account sets', 'Setup', cols=('code', 'name', 'kind', 'parent', 'warehouse', 'default_method', 'is_active'),
      form=('code', 'name', 'kind', 'parent', 'warehouse', 'default_method', 'is_active'), manage=('inv_manager', 'inv_accountant'), view=ALL,
      search=('code', 'name'), filters=('kind', 'parent', 'warehouse', 'is_active'), title='name', subtitle='code', extra=acc_extra, files=False, deletable=True,
      check=accset_check, defaults=accset_defaults, children=(('Warehouse overrides', 'accounts', 'parent'),),
      actions=[Act('set_account', 'Set an account', a_set_account, lambda o, r: app.any(r, 'inv_manager', 'inv_accountant'),
                   fields=(('role', 'Account role', f'choice:{ALL_ROLE_CHOICES}', True), ('account', 'Account code (empty: the default)', 'text', False)))],
      help='As Sage 300: every stock item has one account set (its own, else its category\'s, else the company default STD) holding the inventory-side '
           'accounts – inventory control, GRNI, adjustments, count variance, transit, landed cost clearing / accrual, price variance, revaluation, write-down. '
           'A row with an account set and a warehouse overrides accounts for that warehouse. Production sets (Manufacturing): WIP and the variances.'))
R(Reg('cat_accounts', CategoryAccounts, 'Category accounts', 'Category accounts', 'Setup', cols=('category', 'account_set', 'production_set'),
      form=('category', 'account_set', 'production_set'), manage=('inv_manager', 'inv_accountant'), view=ALL, search=('category__name', 'category__code'),
      filters=('account_set',), title='category', extra=cat_extra, files=False, deletable=True,
      actions=[Act('set_account', 'Set an account', a_set_account, lambda o, r: app.any(r, 'inv_manager', 'inv_accountant'),
                   fields=(('role', 'Account role', f'choice:{CAT_ROLE_CHOICES}', True), ('account', 'Account code (empty: the default)', 'text', False)))],
      help='As Sage 300 I/C categories: per item category the sales-side accounts (revenue, sales returns, cost of goods sold, cost variance, damaged goods, '
           'internal use) and the account set its items take. A sub-category without a row uses its parent\'s.'))
def a_suggest_rp(o, request, f):
    rp, use = S.suggested_point(o)
    o.min_qty = rp
    if o.max_qty and o.max_qty < rp:
        o.max_qty = rp
    o.save(update_fields=['min_qty', 'max_qty'])
    return {'message': f'Reorder point {rp.normalize():f}: average use {S.rq(use).normalize():f} a day × lead time {S.lead_time(o)} days + safety stock.'}


def a_replenish(o, request, f):
    doc = run(lambda: S.replenish(o, request.user))
    app.event('doc', doc.pk, f'Made by reorder planning ({o})', request.user)
    return {'message': f'{doc.number} made as a draft ({doc.get_kind_display()}) – check and post it.', 'open': f'r/doc/{doc.pk}'}


def rule_check(o, request, old):
    if o.max_qty and o.max_qty < o.min_qty:
        raise AppError('The maximum is below the reorder point.', 'max_qty')
    if o.source == 'transfer':
        must(o.source_warehouse_id, 'Choose the warehouse it is transferred from.', 'source_warehouse')
        must(o.source_warehouse_id != o.warehouse_id, 'Transfer from another warehouse.', 'source_warehouse')


def rule_extra(o, request):
    sug = next((x for x in S.suggestions(o.warehouse) if x['rule'] == o.pk), None)
    rp, use = S.suggested_point(o)
    oh, res, inc = S.on_hand(o.product, o.warehouse), S.reserved(o.product, o.warehouse), S.incoming(o.product, o.warehouse)
    rows = [{'what': 'On hand', 'value': float(oh)}, {'what': 'Reserved', 'value': float(res)}, {'what': 'Incoming (drafts)', 'value': float(inc)},
            {'what': 'Projected', 'value': float(oh - res + inc)}, {'what': 'Average use a day (90 days)', 'value': float(S.rq(use))},
            {'what': 'Suggested reorder point', 'value': float(rp)}]
    if sug:
        rows.append({'what': f'Order now ({sug["source"]} {sug["from"]})', 'value': float(sug['order_qty'])})
    return {'suggestion': sug, 'tables': [tbl('Planning', [('what', 'What', 'text'), ('value', 'Quantity', 'number')], rows)]}


R(Reg('reorder', ReorderRule, 'Reorder rule', 'Reorder rules', 'Stock',
      cols=('product', 'warehouse', 'min_qty', 'max_qty', 'reorder_qty', 'policy', 'lead_time_days', 'safety_qty', 'source', 'source_warehouse', 'is_active'),
      form=('product', 'warehouse', 'min_qty', 'max_qty', 'reorder_qty', 'policy', 'multiple', 'moq', 'lead_time_days', 'safety_qty', 'safety_days', 'source',
            'source_warehouse', 'is_active'), manage=MGR, view=ALL, search=('product__name', 'product__number', 'warehouse__code'),
      filters=('warehouse', 'policy', 'source', 'is_active'), title='product', subtitle='warehouse', check=rule_check, extra=rule_extra, files=False, deletable=True,
      actions=[Act('suggest_rp', 'Suggest the reorder point', a_suggest_rp, lambda o, r: app.any(r, *MGR)),
               Act('replenish', 'Order now (draft)', a_replenish, lambda o, r: app.any(r, 'inv_manager', 'inv_storekeeper'), style='primary')],
      help='Per item and warehouse: the reorder point, maximum, policy (fixed / up to max / the shortage), order multiple and minimum, lead time and safety '
           'stock. Projected stock = on hand − reserved + incoming drafts; at or below the point the item is suggested – bought from its preferred supplier '
           'or transferred from another warehouse with spare stock. Select rows and "Order now" makes the drafts.'))
def tag_import(o, request, f):
    n = S.import_item_tags()
    return {'message': f'{n} tags of items in Masters › Items are now tag records on the items.'}


def link_check(o, request, old):
    must(o.related_id != o.product_id, 'An item cannot be related to itself.', 'related')


def plan_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('countplan', 'CP')
    o.save()
    return o


def a_plan_start(o, request, f):
    doc = run(lambda: S.count_start(o, request.user))
    app.event('countplan', o.pk, f'Counting started: {doc.number}, {doc.lines.count()} lines (stock frozen)', request.user)
    return {'message': f'{doc.number} made with {doc.lines.count()} lines; the stock at the start is kept. Count and enter the quantities (or scan).',
            'open': f'r/doc/{doc.pk}'}


def a_plan_review(o, request, f):
    res = run(lambda: S.count_review(o))
    return {'message': (f'{len(res["recount"])} lines above the tolerance – recount them.' if res['recount'] else 'Every line is within the tolerance.')
            + f' Variance value {res["value"]:,.2f}.'}


def a_plan_post(o, request, f):
    first = o.status == 'counting'
    res = run(lambda: S.count_review(o))
    if first and res['recount']:   # not posted: the lines above the tolerance are recounted first (the plan shows them)
        return {'message': f'{len(res["recount"])} lines are above the tolerance – recount them (enter the counted quantity again), then post.',
                'recount': res['recount'], 'posted': False}
    if o.approval_value and abs(res['value']) > o.approval_value and not app.any(request, 'inv_manager'):
        raise AppError(f'The variance {res["value"]:,.2f} is above {o.approval_value:,.2f} – the inventory manager approves and posts it.')
    run(lambda: S.post(o.doc, request.user))
    o.status = 'posted'
    o.save(update_fields=['status'])
    if o.every_days:   # cycle counting: the next one
        from datetime import timedelta
        CountPlan.objects.create(name=o.name, warehouse=o.warehouse, zone=o.zone, category=o.category, abc_class=o.abc_class, every_days=o.every_days,
                                 date=o.date + timedelta(days=o.every_days), blind=o.blind, tolerance_qty=o.tolerance_qty, tolerance_value=o.tolerance_value,
                                 approval_value=o.approval_value, number=app.next_number('countplan', 'CP'))
    return {'message': f'Count posted: variance {res["value"]:,.2f}.', 'open': f'r/doc/{o.doc_id}'}


def plan_extra(o, request):
    if not o.doc_id:
        return {}
    res = S.count_review(o, save=False)
    cols = [('item', 'Item', 'text'), ('lot', 'Lot / serial', 'text'), ('bin', 'Bin', 'text')] + ([] if o.blind and o.status == 'counting' else [('expected', 'Expected', 'number')]) + \
        [('counted', 'Counted', 'number'), ('difference', 'Difference', 'number'), ('value', 'Value', 'number'), ('recount', 'Recount', 'text')]
    return {'review': res, 'tables': [tbl('Count', cols, res['rows'])]}


R(Reg('tag', Tag, 'Tag', 'Tags', 'Setup', cols=('name', 'colour', 'is_active'), form=('name', 'colour', 'is_active'), manage=MGR, view=ALL, search=('name',),
      filters=('is_active',), title='name', files=False, deletable=True,
      actions=[Act('import', 'Bring in the tags typed on items', tag_import, lambda o, r: app.any(r, *MGR))],
      help='Tags with a colour – on items, warehouses and stock documents; filter the lists and the stock reports by them.'))
R(Reg('link', ItemLink, 'Related item', 'Related items', 'Setup', cols=('product', 'kind', 'related', 'default_qty', 'both_ways', 'sequence'),
      form=('product', 'kind', 'related', 'default_qty', 'both_ways', 'sequence'), manage=MGR, view=ALL, search=('product__name', 'related__name'),
      filters=('kind', 'both_ways'), title='product', subtitle='related', check=link_check, files=False, deletable=True,
      help='Optional products, accessories, alternatives (offered when the item is short on a delivery), spare parts, up-sell and frequently bought '
           'together – one way or both ways. Sales and Purchase offer them.'))
R(Reg('packing', PackingRule, 'Packing rule', 'Packing rules', 'Setup', cols=('operation', 'kind', 'material', 'per', 'qty', 'is_active'),
      form=('operation', 'kind', 'material', 'per', 'qty', 'is_active'), manage=MGR, view=ALL, search=('material__name',), filters=('kind', 'per', 'is_active'),
      title='material', files=False, deletable=True,
      help='Packing materials (boxes, pallets, tape, labels) a delivery or an issue uses up: added as lines when it is posted – per document, per line or per unit.'))
R(Reg('countplan', CountPlan, 'Stock count plan', 'Stock count plans', 'Stock',
      cols=('number', 'name', 'warehouse', 'zone', 'category', 'abc_class', 'date', 'blind', 'status'),
      form=('name', 'warehouse', 'zone', 'category', 'abc_class', 'every_days', 'date', 'blind', 'tolerance_qty', 'tolerance_value', 'approval_value', 'note'),
      manage=('inv_manager', 'inv_storekeeper'), view=ALL, search=('number', 'name'), filters=('warehouse', 'status', 'abc_class'), title='name', subtitle='number',
      saver=plan_saver, extra=plan_extra, edit_when=lambda o: o.status == 'draft', files=False, deletable=True,
      actions=[Act('start', 'Start counting', a_plan_start, lambda o, r: o.status == 'draft' and app.any(r, 'inv_manager', 'inv_storekeeper'), style='primary',
                   confirm='The stock now is kept as the expected quantity; a count document is made with a line per item, lot and bin.'),
               Act('review', 'Check the variances', a_plan_review, lambda o, r: o.status in ('counting', 'recount') and app.any(r, 'inv_manager', 'inv_storekeeper')),
               Act('post_count', 'Post the count', a_plan_post, lambda o, r: o.status in ('counting', 'recount') and app.any(r, 'inv_manager', 'inv_storekeeper'),
                   style='primary', confirm='The differences are posted to the count variance account.')],
      help='Whole warehouse, a zone, an item category or an ABC class (cycle counting, repeated every N days); blind count; lines above the quantity / '
           'value tolerance are recounted; variances above the approval value need the inventory manager.'))
# ------------------------------------------------------------------ v1.75.0 attributes and variants
def ia_check(o, request, old):
    a = o.attribute
    if a.kind == 'choice':
        must(o.value_id, 'Choose the value.', 'value')
        must(o.value.attribute_id == a.pk, f'Choose a value of {a.name}.', 'value')
    o.shown = S.attr_shown(o)[:140]


def tpl_check(o, request, old):
    if Variant.objects.filter(product=o.product).exists():
        raise AppError('A variant cannot be a template itself.', 'product')
    for f in ('code_pattern', 'name_pattern'):
        must('{values}' in (getattr(o, f) or ''), 'The pattern needs {values}.', f)


def axis_check(o, request, old):
    must(o.attribute.kind == 'choice' and o.attribute.makes_variants, 'Choose a list attribute that can make variants (size, colour …).', 'attribute')


def a_generate(o, request, f):
    made, kept = run(lambda: S.generate_variants(o, request.user))
    app.event('template', o.pk, f'{len(made)} variants made', request.user)
    return {'message': f'{len(made)} variant items made in Masters › Items' + (f'; {kept} were there already.' if kept else '.')}


def tpl_extra(o, request):
    heads, rows = S.variant_matrix(o)
    vs = [{'item': str(v.product), 'label': v.label, 'on_hand': float(S.on_hand(v.product))} for v in o.variants.select_related('product')]
    return {'matrix': {'columns': heads, 'rows': rows},
            'tables': [tbl('Stock by variant', [('value', o.axes.order_by('sequence', 'id').first().attribute.name if o.axes.exists() else 'Value', 'text')] +
                           [(h, h, 'number') for h in heads], rows),
                       tbl('Variant items', [('item', 'Item', 'text'), ('label', 'Variant', 'text'), ('on_hand', 'On hand', 'number')], vs)]}


def plan_variants_option():
    return VariantTemplate.objects.all().select_related('product')


app.option('templates', plan_variants_option, lambda x: str(x.product), ('product__name', 'product__code'), VariantTemplate)
app.option('attributes', lambda: Attribute.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Attribute)
app.option('attribute-values', lambda: AttributeValue.objects.filter(is_active=True).select_related('attribute'), lambda x: f'{x.attribute.name}: {x.value}',
           ('value', 'code', 'attribute__name'), AttributeValue)

R(Reg('attribute', Attribute, 'Attribute', 'Attributes', 'Setup', cols=('name', 'code', 'kind', 'unit', 'makes_variants', 'is_active'),
      form=('name', 'code', 'kind', 'unit', 'makes_variants', 'sequence', 'is_active'), manage=MGR, view=ALL, search=('name', 'code'), filters=('kind', 'makes_variants'),
      title='name', files=False, deletable=True, children=(('Values', 'attr_value', 'attribute'),),
      help='Properties of items – brand, voltage, grade (descriptive, searchable) and size, colour (they can make variants).'))
R(Reg('attr_value', AttributeValue, 'Attribute value', 'Attribute values', 'Setup', cols=('attribute', 'value', 'code', 'sequence', 'is_active'),
      form=('attribute', 'value', 'code', 'sequence', 'is_active'), manage=MGR, view=ALL, child_of='attribute', search=('value', 'code'), filters=('attribute',),
      title='value', files=False, deletable=True))
R(Reg('cat_attribute', CategoryAttribute, 'Category attribute', 'Category attributes', 'Setup', cols=('category', 'attribute', 'required', 'sequence'),
      form=('category', 'attribute', 'required', 'sequence'), manage=MGR, view=ALL, search=('category__name', 'attribute__name'), filters=('category', 'required'),
      title='category', subtitle='attribute', files=False, deletable=True, help='The attribute set of an item category – the attributes its items show; required ones are checked.'))
R(Reg('item_attribute', ItemAttribute, 'Item attribute', 'Item attributes', 'Setup', cols=('product', 'attribute', 'shown'),
      form=('product', 'attribute', 'value', 'text', 'number', 'yes'), manage=MGR, view=ALL, search=('product__name', 'product__number', 'shown'),
      filters=('attribute',), title='product', subtitle='attribute', check=ia_check, files=False, deletable=True,
      help='The attribute values of an item – searchable ("shown"); the required attributes of its category are listed on the item stock setting.'))
R(Reg('template', VariantTemplate, 'Item with variants', 'Items with variants', 'Setup', cols=('product', 'code_pattern', 'name_pattern'),
      form=('product', 'code_pattern', 'name_pattern', 'excluded', 'note'), manage=MGR, view=ALL, search=('product__name', 'product__code'), title='product', filters=(),
      check=tpl_check, extra=tpl_extra, files=False, deletable=True, children=(('Axes', 'axis', 'template'), ('Variants', 'variant', 'template')),
      actions=[Act('generate', 'Make the variants', a_generate, lambda o, r: app.any(r, *MGR), style='primary',
                   confirm='Every combination of the axis values (less the excluded ones) becomes an item in Masters › Items.')],
      help='An item that comes in sizes and colours: each combination is an item of its own (own stock, cost, barcode); stock documents take a size × colour grid.'))
R(Reg('axis', TemplateAxis, 'Axis', 'Axes', 'Setup', cols=('template', 'attribute', 'values', 'sequence'), form=('template', 'attribute', 'values', 'sequence'),
      manage=MGR, view=ALL, child_of='template', search=('attribute__name',), filters=(), title='attribute', check=axis_check, files=False, deletable=True))
R(Reg('variant', Variant, 'Variant', 'Variants', 'Setup', cols=('template', 'product', 'label', 'is_active'), form=('template', 'product', 'label', 'is_active'),
      manage=MGR, view=ALL, child_of='template', search=('product__name', 'label'), filters=('template', 'is_active'), title='product', files=False,
      edit_when=lambda o: False))


# ------------------------------------------------------------------ v1.75.0 consignment
def stl_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('consignment', 'CST')
    if o.status == 'draft':
        rows = S.consign_used(o.partner, o.date_to)
        o.lines, o.total = rows, S.q2(sum((Decimal(str(r['amount'])) for r in rows), Decimal(0)))
    o.save()
    return o


def a_post_stl(o, request, f):
    run(lambda: S.post_settlement(o, request.user))
    app.event('consignment', o.pk, f'Posted: {o.total} owed to {o.partner}', request.user)
    return {'message': f'{o.number} posted: {o.total} owed to {o.partner} (Dr Consignment payable / Cr Accounts payable).'}


def stl_extra(o, request):
    rows = o.lines if o.status != 'draft' else S.consign_used(o.partner, o.date_to)
    return {'tables': [tbl('Consignment stock used', [('date', 'Date', 'date'), ('document', 'Document', 'text'), ('item', 'Item', 'text'), ('qty', 'Quantity', 'number'),
                                                      ('price', 'Agreed price', 'number'), ('amount', 'Amount', 'number')], rows)]}


R(Reg('consign_price', ConsignmentPrice, 'Consignment price', 'Consignment prices', 'Costing', cols=('partner', 'product', 'price', 'valid_from'),
      form=('partner', 'product', 'price', 'valid_from', 'note'), manage=ACCT + MGR, view=ALL, search=('partner__name', 'product__name'), filters=('partner',),
      title='product', subtitle='partner', files=False, deletable=True,
      help="The agreed price of a supplier's consignment item: when the stock is used it becomes ours at this price (Dr Inventory / Cr Consignment payable)."))
R(Reg('consignment', ConsignmentSettlement, 'Consignment settlement', 'Consignment settlements', 'Costing', cols=('number', 'partner', 'date_to', 'date', 'total', 'status'),
      form=('partner', 'date_to', 'date', 'supplier_invoice', 'note'), manage=ACCT + MGR, view=ALL, search=('number', 'partner__name', 'supplier_invoice'),
      filters=('partner', 'status'), title='number', subtitle='partner', saver=stl_saver, extra=stl_extra, edit_when=lambda o: o.status == 'draft', files=False,
      actions=[Act('post', 'Post', a_post_stl, when(('draft',), *(ACCT + MGR)), style='primary', confirm='Dr Consignment payable / Cr Accounts payable for what was used.')],
      help='Supplier consignment stock used up to a date: the statement owed to the supplier.'))
# ------------------------------------------------------------------ v1.75.0 lots and serial numbers
def lot_extra(o, request):
    tr = S.trace(o.product, o.lot, o.serial)
    out_ = [x for x in tr if x['kind'] in ('delivery', 'dropship out') and x['partner']]
    on = S.on_hand(o.product, lot=o.lot or None, serial=o.serial or None)
    return {'trace': tr, 'customers': out_, 'on_hand': float(on),
            'tables': [tbl('Trace (every movement)', [('date', 'Date', 'date'), ('document', 'Document', 'text'), ('kind', 'Movement', 'text'), ('warehouse', 'Warehouse', 'text'),
                                                      ('partner', 'Supplier / customer', 'text'), ('qty', 'Quantity', 'number')], tr),
                       tbl('Delivered to (recall list)', [('date', 'Date', 'date'), ('document', 'Delivery', 'text'), ('partner', 'Customer', 'text'), ('qty', 'Quantity', 'number')], out_)]}


def a_lot_status(o, request, f):
    old = o.get_status_display()
    o.status = f.s('status')
    must(o.status in dict(LotRecord._meta.get_field('status').choices), 'Choose the status.')
    o.note = (o.note + '\n' if o.note else '') + f'{today():%d/%m/%Y} {old} → {o.get_status_display()}: {f.s("note")}'
    o.save(update_fields=['status', 'note'])
    app.event('lot', o.pk, f'Status {old} → {o.get_status_display()}' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return {'message': f'{o.serial or o.lot} is {o.get_status_display().lower()}.'}


def a_lot_reclass(o, request, f):
    old = (o.serial or o.lot, o.expiry)
    run(lambda: S.reclassify(o, f.s('new_code') or None, f.dt('expiry'), request.user))
    app.event('lot', o.pk, f'Reclassified: {old[0]} → {o.serial or o.lot}; expiry {old[1]} → {o.expiry} – {f.s("note")}', request.user)
    return {'message': f'{o.serial or o.lot} updated everywhere.'}


def a_recall(o, request, f):
    o.status = 'recalled'
    o.note = (o.note + '\n' if o.note else '') + f'{today():%d/%m/%Y} RECALL: {f.s("note")}'
    o.save(update_fields=['status', 'note'])
    cust = [x for x in S.trace(o.product, o.lot, o.serial) if x['kind'] in ('delivery', 'dropship out') and x['partner']]
    app.event('lot', o.pk, f'Recall started: blocked, {len(cust)} deliveries to customers – {f.s("note")}', request.user)
    return {'message': f'{o.serial or o.lot} recalled: blocked in stock; {len(cust)} deliveries to {len({c["partner"] for c in cust})} customers listed for returns.'}


R(Reg('lot', LotRecord, 'Lot / serial number', 'Lots and serial numbers', 'Stock',
      cols=('product', 'lot', 'serial', 'supplier_lot', 'expiry', 'status'),
      form=('product', 'lot', 'serial', 'supplier_lot', 'supplier', 'mfg_date', 'expiry', 'best_before', 'alert_date', 'warranty_until', 'attributes', 'note'),
      manage=STORE, view=ALL, search=('lot', 'serial', 'supplier_lot', 'product__name', 'product__number'), filters=('status', 'product', 'supplier'),
      title='product', subtitle='lot', extra=lot_extra, files=True, deletable=False,
      actions=[Act('status', 'Change the quality status', a_lot_status, lambda o, r: app.any(r, *STORE),
                   fields=(('status', 'Status', 'choice:available=Available|quarantine=Quarantine (inspection)|blocked=Blocked|expired=Expired', True),
                           ('note', 'Why (inspection, certificate of analysis …)', 'text', False))),
               Act('reclass', 'Reclassify', a_lot_reclass, lambda o, r: app.any(r, 'inv_manager'),
                   fields=(('new_code', 'New lot / serial number', 'text', False), ('expiry', 'New expiry', 'date', False), ('note', 'Why', 'text', True))),
               Act('recall', 'Recall', a_recall, lambda o, r: o.status != 'recalled' and app.any(r, 'inv_manager'), style='danger',
                   fields=(('note', 'Reason of the recall', 'text', True),), confirm='The lot is blocked; the deliveries to customers are listed for the returns.')],
      help='Every lot / serial number: dates (made, expiry, best before, alert), supplier lot, warranty, attributes (grade, shade, origin), quality status '
           '(only available lots are picked; expired ones are blocked every night), the trace of every movement and the recall list.'))
# ------------------------------------------------------------------ v1.75.0 kits, requisitions
def kit_check(o, request, old):
    must(o.kit_id != o.component_id, 'A kit cannot hold itself.', 'component')
    must(o.qty and o.qty > 0, 'The quantity in one kit must be more than 0.', 'qty')


def req_saver(o, request, creating, old):
    if creating and not o.number:
        o.number = app.next_number('requisition', 'MR')
        o.requested_by = request.user if getattr(request.user, 'is_authenticated', False) else None
    o.save()
    return o


def a_req_submit(o, request, f):
    must(o.lines.exists(), 'Add the items first.')
    o.status = 'submitted'
    o.save(update_fields=['status'])
    app.event('requisition', o.pk, 'Submitted for approval', request.user)
    return {'message': f'{o.number} sent for approval.'}


def a_req_approve(o, request, f):
    o.status, o.approved_by = ('approved' if f.s('decision') != 'reject' else 'rejected'), request.user
    o.save(update_fields=['status', 'approved_by'])
    app.event('requisition', o.pk, f'{o.get_status_display()}: {f.s("note")}', request.user)
    return {'message': f'{o.number} {o.get_status_display().lower()}.'}


def a_req_issue(o, request, f):
    from .app import PREFIX
    op = OperationType.objects.filter(warehouse=o.warehouse, kind='consumption', is_active=True).first()
    d_ = StockDoc.objects.create(kind='consumption', date=f.dt('date') or today(), warehouse=o.warehouse, operation=op, charge_to=o.charge_to,
                                 reference=f'Requisition {o.number}'[:80], branch_id=o.warehouse.branch_id, reason=op.reason if op is not None else None,
                                 created_by=request.user if getattr(request.user, 'is_authenticated', False) else None)
    d_.number = app.next_number(f'op_{op.pk}', op.code) if op is not None and op.code else app.next_number('doc_consumption', PREFIX['consumption'])
    d_.save(update_fields=['number'])
    for ln in o.lines.select_related('product', 'product__uom'):
        StockDocLine.objects.create(doc=d_, product=ln.product, uom=ln.product.uom, qty=ln.qty, note=ln.note)
    run(lambda: S.post(d_, request.user))
    o.status, o.doc = 'issued', d_
    o.save(update_fields=['status', 'doc'])
    app.event('requisition', o.pk, f'Issued: {d_.number}', request.user)
    return {'message': f'{o.number} issued with {d_.number} (charged to {o.charge_to}).', 'open': f'r/doc/{d_.pk}'}


R(Reg('kit', KitComponent, 'Kit component', 'Kit components', 'Setup', cols=('kit', 'component', 'qty', 'sequence'), form=('kit', 'component', 'qty', 'sequence'),
      manage=MGR, view=ALL, search=('kit__name', 'component__name'), filters=('kit',), title='kit', subtitle='component', check=kit_check, files=False, deletable=True,
      help='Kits / bundles: delivering or issuing the kit takes its components out of stock.'))
R(Reg('requisition', Requisition, 'Material requisition', 'Material requisitions', 'Stock', cols=('number', 'date', 'charge_to', 'warehouse', 'needed_by', 'status'),
      form=('date', 'warehouse', 'charge_to', 'needed_by', 'note'), manage=ALL, view=ALL, search=('number', 'charge_to'), filters=('status', 'warehouse'),
      title='number', subtitle='charge_to', saver=req_saver, edit_when=lambda o: o.status == 'draft', files=True, deletable=True,
      children=(('Items', 'req_line', 'requisition'),),
      actions=[Act('submit', 'Submit', a_req_submit, lambda o, r: o.status == 'draft'),
               Act('approve', 'Approve / reject', a_req_approve, lambda o, r: o.status == 'submitted' and app.any(r, 'inv_manager'),
                   fields=(('decision', 'Decision', 'choice:approve=Approve|reject=Reject', True), ('note', 'Note', 'text', False))),
               Act('issue', 'Issue from the store', a_req_issue, lambda o, r: o.status == 'approved' and app.any(r, *STORE), style='primary',
                   fields=(('date', 'Issue date', 'date', False),))],
      help='A department, project or asset asks for items; the inventory manager approves; the store issues them (internal consumption charged to it).'))
R(Reg('req_line', RequisitionLine, 'Requisition item', 'Requisition items', 'Stock', cols=('product', 'qty', 'note'), form=('requisition', 'product', 'qty', 'note'),
      manage=ALL, view=ALL, child_of='requisition', search=('product__name',), filters=(), title='product', files=False, deletable=True,
      edit_when=lambda o: o.requisition.status == 'draft'))
R(Reg('reason', Reason, 'Reason', 'Reasons', 'Setup', cols=('code', 'name', 'kind', 'account_role', 'is_active', 'review_status'),
      form=('code', 'name', 'kind', 'account_role', 'is_active'), manage=MGR, view=ALL, search=('code', 'name'), filters=('kind', 'is_active', 'review_status'),
      title='name', review=True, deletable=True, files=False))


# ------------------------------------------------------------------ hooks for Sales, Purchase, Manufacturing (v1.76 – v1.79)
def h_post_doc(*, kind, warehouse_id, lines, date=None, partner_id=None, reference='', origin_app='', origin_ref='', to_warehouse_id=None, return_of_id=None,
               user=None, reason_code=''):
    """another app posts a stock document: lines [{product_id, qty, unit_cost, lot, serial, expiry}] → {id, number, value, lines: [value]}"""
    doc = StockDoc.objects.create(kind=kind, date=date or today(), document_date=date or today(), warehouse_id=warehouse_id, to_warehouse_id=to_warehouse_id,
                                  partner_id=partner_id, reference=reference[:80], origin_app=origin_app[:20], origin_ref=origin_ref[:60], return_of_id=return_of_id,
                                  number=app.next_number(f'doc_{kind}', PREFIX.get(kind, 'STK')), created_by=user)
    doc.branch_id = doc.warehouse.branch_id
    doc.reason = Reason.objects.filter(code=reason_code).first() if reason_code else None   # v1.79.0 Manufacturing: the moves go to work in progress
    doc.save(update_fields=['branch', 'reason'])
    for x in lines:
        StockDocLine.objects.create(doc=doc, product_id=x['product_id'], qty=Decimal(str(x.get('qty') or 0)),
                                    unit_cost=Decimal(str(x['unit_cost'])) if x.get('unit_cost') not in (None, '') else None,
                                    counted_qty=x.get('counted_qty'), lot=x.get('lot') or '', serial=x.get('serial') or '', expiry=x.get('expiry'))
    S.post(doc, user)
    doc.refresh_from_db()
    return {'id': doc.pk, 'number': doc.number, 'value': float(doc.total_value), 'lines': [float(ln.value) for ln in doc.lines.all()]}


def h_on_hand(*, product_id, warehouse_id=None):
    from ZeoMasters.models import Product
    p = Product.objects.get(pk=product_id)
    wh = Warehouse.objects.get(pk=warehouse_id) if warehouse_id else None
    return {'on_hand': float(S.on_hand(p, wh)), 'available': float(S.available(p, wh)) if wh else float(S.on_hand(p))}


def h_unit_cost(*, product_id, warehouse_id=None):
    from ZeoMasters.models import Product
    p = Product.objects.get(pk=product_id)
    wh = Warehouse.objects.get(pk=warehouse_id) if warehouse_id else None
    v = S.valuation(group='item', warehouse=wh, product=p)
    return float(v[0]['unit_cost']) if v else float(p.cost_price or 0)


app.hooks.update({'post_doc': h_post_doc, 'on_hand': h_on_hand, 'unit_cost': h_unit_cost})


# ------------------------------------------------------------------ dashboard, my work, daily
def dashboard(request):
    st = S.settings()
    vals = S.valuation(group='item')
    total = sum(r['value'] for r in vals)
    neg = [r for r in vals if r['qty'] < 0]
    low = []
    for s in ItemSetting.objects.filter(min_qty__gt=0).select_related('product')[:500]:
        q = S.on_hand(s.product)
        if q < s.min_qty:
            low.append({'id': s.pk, 'title': str(s.product), 'sub': f'{q.normalize():f} in stock, reorder at {s.min_qty.normalize():f}', 'badge': f'{s.reorder_qty.normalize():f}'})
    docs = in_branches(StockDoc.objects.all(), request)
    drafts = docs.filter(status='draft')
    rec = S.reconciliation()
    diff = sum(abs(r['difference']) for r in rec['rows'])
    by_wh = S.valuation(group='warehouse')
    return {'cards': [{'label': f'Stock value ({st.currency})', 'value': f'{total:,.2f}'},
                      {'label': 'Items in stock', 'value': len([r for r in vals if r['qty'] > 0])},
                      {'label': 'Below the reorder point', 'value': len(low), 'tone': 'bad' if low else 'ok', 'link': 'r/item'},
                      {'label': 'Drafts to post', 'value': drafts.count(), 'link': 'r/doc?status=draft'},
                      {'label': 'Negative stock', 'value': len(neg), 'tone': 'bad' if neg else 'ok'},
                      {'label': 'Lots expiring in 30 days', 'value': len(S.expiring(30)), 'tone': 'bad' if S.expiring(30) else 'ok', 'link': 'r/lot'},
                      {'label': 'To reorder', 'value': len(S.suggestions()), 'link': 'r/reorder'},
                      {'label': f'Accrued landed cost ({st.currency})', 'value': f'{float(LandedCost.objects.filter(kind="estimate", status="posted").aggregate(s=Sum("total"))["s"] or 0):,.2f}',
                       'link': 'r/landed?kind=estimate&status=posted', 'hint': 'Estimates not settled by the bills yet'},
                      {'label': 'Stock ↔ GL difference', 'value': f'{diff:,.2f}', 'tone': 'bad' if diff and st.accounting_mode == 'perpetual' else 'ok',
                       'hint': 'Perpetual' if st.accounting_mode == 'perpetual' else 'Periodic: until the closing stock JV'}],
            'bars': [{'title': 'Stock value by warehouse', 'items': [{'label': r.get('warehouse', ''), 'value': round(r['value'], 2)} for r in by_wh]}],
            'lists': [{'title': 'Operations', 'kind': 'operation', 'rows': ops_board(request)},
                      {'title': 'Below the reorder point', 'kind': 'item', 'rows': low[:10]},
                      {'title': 'Drafts to post', 'kind': 'doc', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.get_kind_display()}', 'sub': str(x.warehouse), 'badge': d(x.date)}
                                                                         for x in drafts.select_related('warehouse')[:10]]}]}


def ops_board(request):
    """Odoo's inventory overview: each operation type with its drafts to post"""
    from django.db.models import Count, Q
    rows = []
    qs = in_branches(OperationType.objects.filter(is_active=True), request).select_related('warehouse').annotate(
        drafts=Count('docs', filter=Q(docs__status='draft')), done=Count('docs', filter=Q(docs__status='posted')))
    for o in qs.order_by('warehouse__code', 'sequence')[:40]:
        if o.drafts or o.kind in ('receipt', 'delivery', 'transfer'):
            rows.append({'id': o.pk, 'title': f'{o.warehouse.code} · {o.name}', 'sub': f'{o.drafts} to post · {o.done} posted', 'badge': o.code})
    return rows


def my_work(request):
    out = []
    if app.any(request, *STORE):
        out.append({'title': 'Drafts to post', 'kind': 'doc', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.get_kind_display()}', 'sub': str(x.warehouse), 'badge': d(x.date)}
                                                                      for x in in_branches(StockDoc.objects.filter(status='draft'), request).select_related('warehouse')[:30]]})
    if app.any(request, *ACCT):
        out.append({'title': 'Landed costs to post', 'kind': 'landed', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.shipment}', 'badge': d(x.date)}
                                                                               for x in LandedCost.objects.filter(status='draft')[:30]]})
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, S.daily

from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)


# ------------------------------------------------------------------ v1.75.0 Settings › Features (#155): standard on, the rest when switched on
from zeo.appkit.features import Feature as _F   # noqa: E402


def _ex(model, **kw):
    return lambda: model.objects.filter(**kw).count()


def _ops_extra():
    std = {'IN', 'OUT', 'INT', 'ADJ', 'CNT', 'SCR', 'ISS', 'RET', 'RTS', 'OPN'}
    return sum(1 for c in OperationType.objects.values_list('code', flat=True) if (c or '').split('-')[-1] not in std)


app.features = [
    _F('methods', 'Costing methods other than moving average', 'Costing', 'FIFO, lot, serial and LIFO (management only) per item; the company default method.',
       fields={'item': ('method',)}, settings=('default_method', 'allow_lifo'),
       in_use=lambda: ItemSetting.objects.exclude(method__in=('', 'average')).count() + (S.settings().default_method != 'average')),
    _F('standard_cost', 'Standard cost', 'Costing', 'Items at a standard cost; a new cost from a date revalues the stock held; price variance.',
       regs=('standard',), fields={'item': ('standard_cost',)}, in_use=lambda: StandardCost.objects.count()),
    _F('costing_level', 'Costing per branch or per warehouse', 'Costing', 'One cost per item for each branch or warehouse instead of the company.',
       settings=('costing_level',), in_use=lambda: int(S.settings().costing_level != 'company')),
    _F('periodic', 'Periodic accounting', 'Costing', 'No stock journals; a closing stock JV at the month end.', regs=('closing',),
       settings=('accounting_mode', 'posting_timing'), in_use=lambda: int(S.settings().accounting_mode != 'perpetual')),
    _F('landed_estimates', 'Landed cost estimates and %', 'Costing', 'Estimated charges at receipt (accrual) settled by the bills; charges as a % of the goods value.',
       fields={'landed': ('kind', 'estimate'), 'charge': ('percent',)}, in_use=_ex(LandedCost, kind='estimate')),
    _F('fx', 'Costs in foreign currencies', 'Costing', 'Receipts and landed charges in USD, EUR … with the exchange rate; the cost is kept in the company currency.',
       fields={'doc': ('currency', 'rate'), 'charge': ('currency', 'fc_amount', 'rate')}, in_use=lambda: StockDoc.objects.exclude(currency='').count()),
    _F('provision', 'Ageing provision (IFRS)', 'Costing', 'A provision matrix by age posts the monthly provision journal.', settings=('provision_matrix',),
       in_use=lambda: int(bool(S.settings().provision_matrix))),
    _F('bins', 'Bins and put-away', 'Warehouse', 'Zones, aisles, racks and bins; put-away rules; removal by bin.', regs=('bin', 'putaway'),
       fields={'line': ('bin', 'to_bin'), 'operation': ('default_bin', 'default_to_bin')}, in_use=lambda: Bin.objects.count()),
    _F('operations', 'Own operation types', 'Warehouse', 'Operation types beyond the standard set (own number series, default bins, return types).',
       regs=('operation',), fields={'doc': ('operation',)}, in_use=_ops_extra),
    _F('two_step', 'Two-step transfers', 'Warehouse', 'Ship, then receive at the other warehouse: stock in transit, partial receipt, short on arrival.',
       fields={'doc': ('two_step', 'expected_date')}, in_use=_ex(StockDoc, two_step=True)),
    _F('dropship', 'Drop shipping', 'Warehouse', 'The supplier delivers straight to the customer (no stock in between).', fields={'doc': ('supplier',)},
       in_use=_ex(StockDoc, kind='dropship')),
    _F('lots', 'Lots and serial numbers', 'Tracking', 'Batches and serial numbers with expiry, quality status, trace and recall; FEFO.', regs=('lot',),
       fields={'line': ('lot', 'serial', 'expiry'), 'item': ('tracking', 'lot_prefix', 'min_life_days', 'shelf_life_days')},
       in_use=lambda: ItemSetting.objects.exclude(tracking='none').count() + LotRecord.objects.count()),
    _F('uom', 'Units of measure conversions', 'Items', 'Buy and sell in cartons, boxes …; the item\'s own conversions; rounding steps.', regs=('item_unit', 'unit'),
       fields={'line': ('uom',)}, in_use=lambda: ItemUnit.objects.count() + UnitSetting.objects.count()),
    _F('catch_weight', 'Catch weight', 'Items', 'Counted in units, priced by the actual weight (meat, fish, cheese).', fields={'item': ('catch_weight',)},
       needs=('uom',), in_use=_ex(ItemSetting, catch_weight=True)),
    _F('packaging', 'Packaging and packing materials', 'Items', 'Carton / pallet levels with dimensions and barcodes, packing materials, returnable packaging.',
       regs=('packing',), fields={'item_unit': ('level', 'full_only', 'length_cm', 'width_cm', 'height_cm', 'gross_kg', 'net_kg'), 'item': ('returnable', 'deposit')},
       needs=('uom',), in_use=lambda: PackingRule.objects.count() + ItemSetting.objects.filter(returnable=True).count()),
    _F('attributes', 'Item attributes', 'Items', 'Brand, voltage, grade … per item category, searchable.', regs=('attribute', 'attr_value', 'cat_attribute', 'item_attribute'),
       in_use=lambda: Attribute.objects.count()),
    _F('variants', 'Variants and grid entry', 'Items', 'Items in sizes and colours – each combination an item; size × colour grid on documents.',
       regs=('template', 'axis', 'variant'), needs=('attributes',), in_use=lambda: VariantTemplate.objects.count()),
    _F('related', 'Related items', 'Items', 'Optional products, accessories, alternatives offered when short, spare parts, up-sell.', regs=('link',),
       in_use=lambda: ItemLink.objects.count()),
    _F('kits', 'Kits and bundles', 'Items', 'Delivering or issuing a kit takes its components.', regs=('kit',), in_use=lambda: KitComponent.objects.count()),
    _F('consignment', 'Supplier consignment', 'Stock control', 'Stock the supplier owns until used; take-over at the agreed price; settlements.',
       regs=('consign_price', 'consignment'), fields={'line': ('owner',)}, in_use=lambda: StockMove.objects.filter(owner__isnull=False).count()),
    _F('reorder', 'Reorder planning', 'Stock control', 'Reorder rules per warehouse, lead time, safety stock, suggestions and "Order now".', regs=('reorder',),
       in_use=lambda: ReorderRule.objects.count()),
    _F('count_plans', 'Count plans and cycle counting', 'Stock control', 'Blind counts, recount tolerance, approval, ABC cycle counting.', regs=('countplan',),
       in_use=lambda: CountPlan.objects.count()),
    _F('requisitions', 'Material requisitions', 'Stock control', 'A department or project asks for items; approval; issue from the store.',
       regs=('requisition', 'req_line'), in_use=lambda: Requisition.objects.count()),
]
app.presets = {
    'food': ('Food trading and distribution', ('lots', 'uom', 'catch_weight', 'packaging', 'reorder', 'count_plans', 'landed_estimates', 'fx', 'bins')),
    'pharma': ('Pharmacy and medical', ('lots', 'uom', 'packaging', 'reorder', 'count_plans', 'bins')),
    'retail': ('Retail and fashion', ('variants', 'attributes', 'uom', 'packaging', 'reorder', 'count_plans', 'kits', 'related')),
    'trading': ('General trading and import', ('uom', 'packaging', 'landed_estimates', 'fx', 'reorder', 'two_step', 'dropship', 'related')),
    'manufacturing': ('Manufacturing', ('bins', 'lots', 'uom', 'standard_cost', 'requisitions', 'reorder', 'count_plans', 'methods')),
    'all': ('Everything', tuple(f.key for f in app.features)),
}
