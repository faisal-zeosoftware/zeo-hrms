"""ZEO Manufacturing on the app kit (v1.79.0): bills of materials and operations on work centres, production orders (also from Sales
orders and from planning), issue and backflush through Inventory, operation time with labour and overhead absorbed, output into stock,
scrap, closing with the production variance in Accounting, planning of what to make and what to buy, cost roll-up."""
from decimal import Decimal

from zeo.appkit.engine import Act, App, AppError, Reg, d, in_branches, today

from . import services as S
from .models import (ROLES, Bom, BomLine, BomOperation, MaterialLine, MfgEvent, MfgFile, MfgSequence, MfgSettings, Movement, OperationLine, Plan,
                     ProductionOrder, WorkCentre)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-manufacturing/'

app = App(
    label='ZeoMfg', slug='zeo-manufacturing', title='Manufacturing', settings_model=MfgSettings, sequence_model=MfgSequence, event_model=MfgEvent,
    file_model=MfgFile, roles=ROLES, role_prefix='mfg_', manager_role='mfg_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'MFG': 'assembly', 'FNB': 'process'}, pack_models={'workcentre': (WorkCentre, 'code')},
    links=(('ZeoInventory', 'Inventory', 'Materials issued and the output move the stock through work in progress (production account sets).'),
           ('ZeoLedger', 'Accounting', 'Labour and overhead absorbed and the production variance are booked in the general ledger.'),
           ('ZeoSales', 'Sales', 'A confirmed Sales order becomes production orders for what is not in stock; planning reads the open orders.'),
           ('ZeoPurchase', 'Purchase', 'Planning turns the components to buy into a purchase request.')),
    page=PAGE,
)
app.settings_fields = ('default_warehouse', 'backflush', 'over_production', 'absorb_account', 'variance_account', 'plan_days')

MGR = ('mfg_manager',)
PLAN = ('mfg_manager', 'mfg_planner')
FLOOR = ('mfg_manager', 'mfg_planner', 'mfg_operator')
VIEW = ('mfg_planner', 'mfg_operator', 'mfg_viewer')
ALL = ('mfg_manager', 'mfg_planner', 'mfg_operator', 'mfg_viewer')
OPEN = ('released', 'in_progress')


def run(fn):
    try:
        return fn()
    except S.MfgError as e:
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
    from ZeoMasters.models import Product as _Product
    app.option('items', lambda: _Product.objects.filter(is_active=True), lambda x: f'{x.number} {x.name}', ('number', 'name', 'code', 'barcode'), _Product)
except Exception:   # noqa: BLE001
    pass
try:
    from ZeoSales.models import Order as _SO
    app.option('sales-orders', lambda: _SO.objects.filter(status__in=('confirmed', 'part_delivered')), lambda x: f'{x.number} {x.partner}', ('number', 'partner__name'), _SO)
except Exception:   # noqa: BLE001
    pass
app.option('work-centres', lambda: WorkCentre.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), WorkCentre)
app.option('boms', lambda: Bom.objects.filter(is_active=True).select_related('product'), lambda x: str(x), ('code', 'product__name'), Bom)
app.provide('manufacturing.orders', lambda: ProductionOrder.objects.all(), lambda x: f'{x.number} {x.product}', ('number',))


# ------------------------------------------------------------------ bills of materials
def bom_check(o, request, old):
    if Decimal(o.qty or 0) <= 0:
        raise AppError('Enter the quantity the bill makes.', 'qty')


def bom_saver(o, request, creating, old):
    if not o.code:
        o.code = app.next_number('bom', 'BOM')
    o.save()
    if o.is_default:
        Bom.objects.filter(product=o.product, is_default=True).exclude(pk=o.pk).update(is_default=False)
    _roll(o)
    return o


def _roll(bom):
    """the stored cost per unit follows every change of the bill, its components and its operations"""
    try:
        bom.unit_cost = S.rollup(bom)['unit']
    except S.MfgError:
        pass


def child_saver(o, request, creating, old):
    o.save()
    _roll(o.bom)
    return o


def bom_extra(o, request):
    try:
        c = S.rollup(o)
    except S.MfgError as e:
        return {'warnings': [e.message]}
    rows = []
    for ln in o.lines.select_related('component'):
        uc = S.unit_cost(ln.component)
        q = Decimal(ln.qty) * (1 + Decimal(ln.scrap) / 100)
        rows.append({'item': str(ln.component), 'qty': float(ln.qty), 'scrap': float(ln.scrap), 'unit_cost': float(uc), 'cost': float(S.r2(q * uc))})
    ops = []
    for op in o.operations.select_related('work_centre'):
        mins = Decimal(op.setup_min) + Decimal(op.run_min) * Decimal(o.qty)
        a, b = S.op_cost(op, mins)
        ops.append({'step': op.seq, 'operation': op.name, 'work_centre': str(op.work_centre), 'minutes': float(mins), 'cost': float(a + b)})
    out = {'tables': [tbl('Cost per unit', [('materials', 'Materials', 'number'), ('labour', 'Labour and machine', 'number'), ('overhead', 'Overhead', 'number'),
                                            ('unit', 'Cost per unit', 'number')], [{k: float(v) for k, v in c.items()}]),
                      tbl('Materials of one batch', [('item', 'Component', 'text'), ('qty', 'Quantity', 'number'), ('scrap', 'Scrap %', 'number'),
                                                     ('unit_cost', 'Unit cost', 'number'), ('cost', 'Cost', 'number')], rows)]}
    if ops:
        out['tables'].append(tbl('Operations of one batch', [('step', 'Step', 'number'), ('operation', 'Operation', 'text'), ('work_centre', 'Work centre', 'text'),
                                                             ('minutes', 'Minutes', 'number'), ('cost', 'Cost', 'number')], ops))
    return out


def bomline_check(o, request, old):
    if o.component_id and o.component_id == o.bom.product_id:
        raise AppError('An item is not a component of itself.', 'component')
    if Decimal(o.qty or 0) <= 0:
        raise AppError('Enter the quantity.', 'qty')


def a_bom_copy(o, request, f):
    n = Bom.objects.create(product=o.product, qty=o.qty, version=f.s('version') or '', is_default=False, note=o.note, code=app.next_number('bom', 'BOM'))
    for ln in o.lines.all():
        BomLine.objects.create(bom=n, component=ln.component, qty=ln.qty, scrap=ln.scrap, note=ln.note)
    for op in o.operations.all():
        BomOperation.objects.create(bom=n, seq=op.seq, name=op.name, work_centre=op.work_centre, setup_min=op.setup_min, run_min=op.run_min)
    return {'message': f'Copied as {n.code} – change it and make it the default when it is ready.'}


def a_bom_rollup(o, request, f):
    c = run(lambda: S.rollup(o))
    return {'message': f'Cost per unit {c["unit"]:,.4f} (materials {c["materials"]:,.2f}, labour and machine {c["labour"]:,.2f}, overhead {c["overhead"]:,.2f}).'}


# ------------------------------------------------------------------ production orders
def mo_defaults(o, request):
    if not o.start_date:
        o.start_date = today()
    if o.product_id and not o.bom_id:
        o.bom = S.default_bom(o.product)
    if not o.warehouse_id and S.settings().default_warehouse_id:
        o.warehouse_id = S.settings().default_warehouse_id
    o.created_by = o.created_by or request.user


def mo_check(o, request, old):
    if old is not None and old.status != 'draft':
        raise AppError('A released order is not changed – cancel it or close it.')
    if o.bom_id and o.product_id and o.bom.product_id != o.product_id:
        raise AppError('The bill of materials is of another item.', 'bom')
    if Decimal(o.qty or 0) <= 0:
        raise AppError('Enter the quantity to make.', 'qty')


def mo_saver(o, request, creating, old):
    if not o.number:
        o.number = app.next_number('order', 'MO')
    o.save()
    return o


def mo_extra(o, request):
    out = {'tables': []}
    warn = []
    if o.status in OPEN:
        sh = S.shortages(o)
        if sh:
            warn.append('Short of: ' + ', '.join(f'{x["item"]} {x["short"]:g}' for x in sh[:5]) + '.')
    if o.status == 'draft' and o.product_id and S.default_bom(o.product) is None:
        warn.append(f'{o.product} has no bill of materials.')
    out['warnings'] = warn
    if o.status != 'draft':
        out['tables'].append(tbl('Materials', [('item', 'Component', 'text'), ('planned', 'Planned', 'number'), ('issued', 'Issued', 'number'), ('left', 'To issue', 'number'),
                                               ('value', 'Value issued', 'number')],
                                 [{'item': str(m.component), 'planned': float(m.planned), 'issued': float(m.issued), 'left': float(max(Decimal(0), m.planned - m.issued)),
                                   'value': money(m.value), '_id': m.pk} for m in o.materials.select_related('component')]))
        ops = [{'step': x.seq, 'operation': x.name, 'work_centre': str(x.work_centre), 'planned': float(x.planned_min), 'actual': float(x.actual_min),
                'cost': money(x.cost), 'done': 'Yes' if x.done else ''} for x in o.operations.select_related('work_centre')]
        if ops:
            out['tables'].append(tbl('Operations', [('step', 'Step', 'number'), ('operation', 'Operation', 'text'), ('work_centre', 'Work centre', 'text'),
                                                    ('planned', 'Planned (min)', 'number'), ('actual', 'Actual (min)', 'number'), ('cost', 'Cost', 'number'), ('done', 'Done', 'text')], ops))
        c = S.cost_table(o)
        out['tables'].append(tbl('Cost', [('materials', 'Materials', 'number'), ('labour', 'Labour and machine', 'number'), ('overhead', 'Overhead', 'number'),
                                          ('actual', 'Actual cost', 'number'), ('to_stock', 'To stock', 'number'), ('in_wip', 'In work in progress', 'number'),
                                          ('variance', 'Variance', 'number')], [c]))
        mv = [{'date': d(x.date), 'what': x.get_kind_display(), 'qty': float(x.qty), 'value': money(x.value), 'document': x.document, 'note': x.note} for x in o.movements.all()[:50]]
        if mv:
            out['tables'].append(tbl('Shop floor', [('date', 'Date', 'text'), ('what', 'What', 'text'), ('qty', 'Quantity / minutes', 'number'), ('value', 'Value', 'number'),
                                                    ('document', 'Document', 'text'), ('note', 'Note', 'text')], mv))
    return out


def a_release(o, request, f):
    run(lambda: S.release(o, request.user))
    sh = S.shortages(o)
    app.event('order', o.pk, 'Released', request.user)
    return {'message': f'{o.number} released: {o.materials.count()} components, {o.operations.count()} operations, planned cost {o.unit_cost:,.2f} per unit.'
            + (' Short of: ' + ', '.join(f'{x["item"]} {x["short"]:g}' for x in sh[:5]) + '.' if sh else '')}


def a_issue(o, request, f):
    qtys = f.j('qtys') if f.has('qtys') else None
    v = run(lambda: S.issue(o, qtys or None, f.dt('date'), request.user))
    return {'message': f'Materials issued to work in progress: {v:,.2f}.'}


def a_time(o, request, f):
    v = run(lambda: S.report_time(o, f.i('operation') or None, f.s('minutes') or None, f.dt('date'), request.user))
    return {'message': f'Operation time reported: {v:,.2f} of labour, machine and overhead absorbed.'}


def a_produce(o, request, f):
    v = run(lambda: S.produce(o, f.s('qty') or 0, f.s('scrap') or 0, f.dt('date'), request.user))
    return {'message': f'Output reported: {S.n(o.done_qty)} of {S.n(o.qty)} made' + (f', {S.n(o.scrap_qty)} scrapped' if o.scrap_qty else '') + f'; {v:,.2f} to stock.'}


def a_close(o, request, f):
    v = run(lambda: S.close(o, f.dt('date'), request.user))
    return {'message': f'{o.number} closed – production variance {v:,.2f} ' + ('(unfavourable).' if v > 0 else '(favourable).' if v < 0 else '(none).')}


def a_cancel(o, request, f):
    if o.movements.exists():
        raise AppError('Materials or output are reported on this order – close it instead.')
    o.status = 'cancelled'
    o.save(update_fields=['status'])
    return {'message': 'Cancelled.'}


def a_from_sales(o, request, f):
    from ZeoSales.models import Order as SO
    so = SO.objects.filter(pk=f.i('sales_order')).first()
    if so is None:
        raise AppError('Choose the sales order.', 'sales_order')
    made = run(lambda: S.from_sales(so, request.user))
    return {'message': f'{len(made)} production order(s) for {so.number}: ' + ', '.join(f'{x.number} {x.product} × {S.n(x.qty)}' for x in made) + ' – release them.'}


def a_plan(o, request, f):
    p = run(lambda: S.plan(request.user))
    mk = sum(1 for x in p.rows if x['kind'] == 'make')
    by = sum(1 for x in p.rows if x['kind'] == 'buy')
    return {'message': f'Planning {p.number}: {mk} item(s) to make, {by} to buy – open it under Planning to make the orders.'}


def plan_extra(o, request):
    rows = [{'what': 'Make' if x['kind'] == 'make' else 'Buy', 'item': x['item'], 'qty': x['qty'], 'on_hand': x['on_hand'], 'open': x['open_orders'], 'for': x['for']}
            for x in o.rows or []]
    return {'tables': [tbl('Suggestions', [('what', 'What', 'text'), ('item', 'Item', 'text'), ('qty', 'Quantity', 'number'), ('on_hand', 'On hand', 'number'),
                                           ('open', 'Open orders / reserved', 'number'), ('for', 'For', 'text')], rows)]}


def a_apply(o, request, f):
    made, req, buys = run(lambda: S.apply_plan(o, request.user))
    msg = f'{len(made)} production order(s) made'
    if req is not None:
        msg += f'; purchase request {req.number} with {len(buys)} item(s)'
    elif buys:
        msg += f'; {len(buys)} item(s) to buy – switch on Purchase to make the request automatically'
    return {'message': msg + '.'}


R = app.reg
R(Reg('order', ProductionOrder, 'Production order', 'Production orders', 'Production',
      cols=('number', 'product', 'qty', 'done_qty', 'start_date', 'due_date', 'source_ref', 'status'),
      form=('product', 'bom', 'qty', 'warehouse', 'start_date', 'due_date', 'branch', 'note'), manage=MGR, view=VIEW, create=PLAN,
      search=('number', 'product__name', 'source_ref'), filters=('status', 'product', 'warehouse', 'source', 'branch'), title='number', subtitle='product',
      defaults=mo_defaults, check=mo_check, saver=mo_saver, extra=mo_extra, deletable=True, pdf='Production order', edit_when=lambda o: o.status == 'draft',
      info=('unit_cost', 'scrap_qty', 'source', 'source_ref', 'material_cost', 'labour_cost', 'overhead_cost', 'output_value', 'variance'),
      actions=[Act('release', 'Release', a_release, lambda o, r: o.status == 'draft' and app.any(r, *PLAN), style='primary',
                   confirm='The materials and the operations of the bill are copied to the order; the cost per unit is rolled up.'),
               Act('issue', 'Issue materials', a_issue, lambda o, r: o.status in OPEN and app.any(r, *FLOOR),
                   fields=(('date', 'Date (empty: today)', 'date', False), ('qtys', 'Quantities by line {material id: qty} (empty: everything left)', 'json', False))),
               Act('time', 'Report time', a_time, lambda o, r: o.status in OPEN and o.operations.exists() and app.any(r, *FLOOR),
                   fields=(('operation', 'Operation id (empty: every open operation)', 'int', False), ('minutes', 'Minutes (empty: the planned minutes)', 'decimal', False),
                           ('date', 'Date (empty: today)', 'date', False))),
               Act('produce', 'Report output', a_produce, lambda o, r: o.status in OPEN and app.any(r, *FLOOR), style='primary',
                   fields=(('qty', 'Quantity made', 'decimal', True), ('scrap', 'Quantity scrapped', 'decimal', False), ('date', 'Date (empty: today)', 'date', False))),
               Act('close', 'Close', a_close, lambda o, r: o.status in ('done', 'in_progress') and (o.done_qty or o.scrap_qty) and app.any(r, *MGR), style='primary',
                   confirm='What is left in work in progress for this order goes to the production variance in Accounting.', fields=(('date', 'Date (empty: today)', 'date', False),)),
               Act('from_sales', 'Orders for a sales order', a_from_sales, lambda o, r: app.any(r, *PLAN) and S.active('sales', 'ZeoSales'),
                   fields=(('sales_order', 'Sales order', 'opt:sales-orders', True),)),
               Act('plan', 'Run planning', a_plan, lambda o, r: app.any(r, *PLAN)),
               Act('cancel', 'Cancel', a_cancel, lambda o, r: o.status in ('draft', 'released') and app.any(r, *PLAN), style='danger')],
      help='Release copies the bill; issue the materials (or let the output take them – backflush), report the time and the output; close moves what is left in work in progress to the variance.'))
R(Reg('bom', Bom, 'Bill of materials', 'Bills of materials', 'Production', cols=('code', 'product', 'qty', 'version', 'unit_cost', 'is_default', 'is_active'),
      form=('product', 'qty', 'version', 'is_default', 'is_active', 'note'), manage=MGR, view=ALL, create=PLAN, search=('code', 'product__name', 'version'),
      filters=('is_active', 'is_default'), title='code', subtitle='product', check=bom_check, saver=bom_saver, extra=bom_extra, deletable=True, info=('unit_cost',),
      children=(('Components', 'bomline', 'bom'), ('Operations', 'bomop', 'bom')),
      actions=[Act('rollup', 'Roll up the cost', a_bom_rollup, lambda o, r: app.any(r, *PLAN)),
               Act('copy', 'New version', a_bom_copy, lambda o, r: app.any(r, *PLAN), fields=(('version', 'Version', 'text', False),))],
      help='What one batch of the item is made of (components with scrap %) and the operations on the work centres; the default bill is used by new orders.'))
R(Reg('bomline', BomLine, 'Component', 'Components', 'Production', cols=('component', 'qty', 'scrap', 'note'), form=('bom', 'component', 'qty', 'scrap', 'note'),
      manage=MGR, view=ALL, create=PLAN, child_of='bom', search=('component__name',), filters=(), title='component', check=bomline_check, saver=child_saver, files=False, deletable=True))
R(Reg('bomop', BomOperation, 'Operation', 'Operations', 'Production', cols=('seq', 'name', 'work_centre', 'setup_min', 'run_min'),
      form=('bom', 'seq', 'name', 'work_centre', 'setup_min', 'run_min'), manage=MGR, view=ALL, create=PLAN, child_of='bom', search=('name',), filters=(), title='name',
      saver=child_saver, files=False, deletable=True))
R(Reg('material', MaterialLine, 'Material', 'Materials', 'Production', cols=('order', 'component', 'planned', 'issued', 'value'), form=(), manage=MGR, view=ALL,
      create='none', search=('order__number', 'component__name'), filters=(), title='component', files=False))
R(Reg('movement', Movement, 'Shop floor entry', 'Shop floor', 'Production', cols=('date', 'order', 'kind', 'qty', 'value', 'document', 'note'), form=(), manage=MGR,
      view=ALL, create='none', search=('order__number', 'document'), filters=('kind',), title='document', files=False))
R(Reg('plan', Plan, 'Planning run', 'Planning', 'Production', cols=('number', 'date', 'horizon', 'status'), form=(), manage=MGR, view=ALL, create='none',
      search=('number',), filters=('status',), title='number', extra=plan_extra, files=False,
      actions=[Act('apply', 'Make the orders', a_apply, lambda o, r: o.status == 'draft' and app.any(r, *PLAN), style='primary',
                   confirm='Production orders for what to make; a purchase request for what to buy (Purchase).')],
      help='What to make and what to buy for the open sales orders and the reorder points – run it from a production order (Run planning).'))
R(Reg('workcentre', WorkCentre, 'Work centre', 'Work centres', 'Setup', cols=('code', 'name', 'hours_per_day', 'labour_rate', 'machine_rate', 'overhead_rate', 'efficiency', 'is_active'),
      form=('code', 'name', 'hours_per_day', 'labour_rate', 'machine_rate', 'overhead_rate', 'efficiency', 'warehouse', 'is_active'), manage=MGR, view=ALL,
      search=('code', 'name'), filters=('is_active',), title='name', deletable=True,
      help='Where the operations are done: capacity and the cost per hour of labour, machine and overhead.'))


# ------------------------------------------------------------------ dashboard, my work
def dashboard(request):
    t = today()
    qs = in_branches(ProductionOrder.objects.all(), request)
    open_ = qs.filter(status__in=OPEN)
    late = open_.filter(due_date__lt=t)
    drafts = qs.filter(status='draft')
    done = qs.filter(status='done')
    month = qs.filter(status__in=('done', 'closed'), movements__kind='output', movements__date__gte=t.replace(day=1)).distinct()
    var = sum((float(o.variance) for o in qs.filter(status='closed', movements__kind='close', movements__date__gte=t.replace(day=1)).distinct()), 0.0)
    wip = sum((float(S.wip(o)) for o in open_), 0.0) + sum((float(S.wip(o)) for o in done), 0.0)
    load = {}
    for op in OperationLine.objects.filter(order__status__in=OPEN, done=False).select_related('work_centre'):
        k = op.work_centre.code
        load[k] = load.get(k, 0) + float(op.planned_min - op.actual_min) / 60
    return {'cards': [{'label': 'Orders in production', 'value': open_.count(), 'link': 'r/order?status=in_progress'},
                      {'label': 'To release', 'value': drafts.count(), 'link': 'r/order?status=draft'},
                      {'label': 'Late', 'value': late.count(), 'tone': 'bad' if late.exists() else 'ok'},
                      {'label': 'Done, to close', 'value': done.count(), 'link': 'r/order?status=done'},
                      {'label': 'Work in progress', 'value': f'{wip:,.2f}'},
                      {'label': 'Orders finished this month', 'value': month.count()},
                      {'label': 'Variance this month', 'value': f'{var:,.2f}', 'tone': 'bad' if var > 0 else 'ok'}],
            'bars': [{'title': 'Open hours by work centre', 'items': [{'label': k, 'value': round(v, 1)} for k, v in sorted(load.items(), key=lambda x: -x[1])]}],
            'lists': [{'title': 'In production', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.product}', 'sub': f'due {d(o.due_date)}' if o.due_date else '',
                                                                          'badge': f'{S.n(o.done_qty)} / {S.n(o.qty)}'} for o in open_.select_related('product').order_by('due_date')[:10]]},
                      {'title': 'To release', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.product}', 'badge': S.n(o.qty)}
                                                                        for o in drafts.select_related('product')[:10]]}]}


def my_work(request):
    out = []
    if app.any(request, *FLOOR):
        out.append({'title': 'In production', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.product}', 'badge': f'{S.n(o.done_qty)} / {S.n(o.qty)}'}
                                                                       for o in ProductionOrder.objects.filter(status__in=OPEN).select_related('product')[:30]]})
    if app.any(request, *MGR):
        out.append({'title': 'Done – to close', 'kind': 'order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.product}'}
                                                                        for o in ProductionOrder.objects.filter(status='done').select_related('product')[:30]]})
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, S.daily

from zeo.appkit.features import Feature as _F   # noqa: E402

app.features = [
    _F('routing', 'Operations and work centres', 'Production', 'Operations on work centres in the bills; operation time absorbs labour, machine and overhead into the order.',
       regs=('bomop', 'workcentre'), in_use=lambda: BomOperation.objects.count()),
    _F('planning', 'Planning (what to make, what to buy)', 'Planning', 'Suggestions from the open sales orders and the reorder points; production orders and a purchase request in one step.',
       regs=('plan',), settings=('plan_days',), in_use=lambda: Plan.objects.count()),
    _F('from_sales', 'Make to order', 'Planning', 'A confirmed Sales order becomes production orders for what is not in stock.', fields={'order': ('source_ref',)},
       in_use=lambda: ProductionOrder.objects.filter(source='sales').count()),
    _F('scrap', 'Scrap and over-production', 'Production', 'Report scrapped quantities and allow a % more than ordered.', settings=('over_production',),
       in_use=lambda: ProductionOrder.objects.filter(scrap_qty__gt=0).count()),
    _F('versions', 'Bill versions', 'Production', 'Several versions of a bill; one is the default.', fields={'bom': ('version',)}, in_use=lambda: Bom.objects.exclude(version='').count()),
]
app.presets = {
    'assembly': ('Assembly', ('routing', 'from_sales', 'scrap')),
    'process': ('Process (food, chemicals)', ('planning', 'scrap', 'versions')),
    'all': ('Everything', tuple(f.key for f in app.features)),
}
