"""ZEO Manufacturing (v1.79.0) – cost roll-up, release, issue and backflush, operation time, output, scrap, close with the variance,
planning (make and buy), orders from Sales.

Money flow (perpetual inventory): materials out of stock to work in progress (Inventory, reason MFG-ISSUE → WIP 1450); operation time
absorbs labour, machine and overhead (Accounting: Dr WIP, Cr absorbed); output into stock at the planned cost (Inventory, reason
MFG-OUTPUT → from WIP); closing moves what is left in WIP for the order to the production variance (Accounting)."""
import logging
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from .models import Bom, MaterialLine, MfgSettings, Movement, OperationLine, Plan, ProductionOrder

log = logging.getLogger(__name__)
D0 = Decimal('0')
REASONS = (('MFG-ISSUE', 'Production – materials issued', 'consumption', 'wip'), ('MFG-OUTPUT', 'Production – output to stock', 'adjustment', 'wip'),
           ('MFG-SCRAP', 'Production – scrap', 'scrap', 'prod_scrap'))


class MfgError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def must(cond, message, field=None):
    if not cond:
        raise MfgError(message, field)


def r2(x):
    return Decimal(x or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def r4(x):
    return Decimal(x or 0).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def n(x):
    return f'{Decimal(x or 0).normalize():f}'


def settings():
    return MfgSettings.get()


def app():
    from .app import app as a
    return a


def today():
    return timezone.localdate()


def call(label, name, **kw):
    from zeo.appkit.engine import REGISTRY, AppError
    a = REGISTRY.get(label)
    if a is None or name not in a.hooks:
        return None
    try:
        return a.hooks[name](**kw)
    except AppError as e:
        raise MfgError(e.message, e.field)
    except Exception as e:   # noqa: BLE001 – the other app's own error on the screen
        if hasattr(e, 'message'):
            raise MfgError(e.message, getattr(e, 'field', None))
        raise


def active(key, label):
    from zeo.appkit.engine import REGISTRY
    try:
        from ZeoApps.services import is_active
        return label in REGISTRY and is_active(key)
    except Exception:   # noqa: BLE001
        return False


def inventory_on():
    return active('inventory', 'ZeoInventory')


def ledger_on():
    return active('accounting', 'ZeoLedger')


def purchase_on():
    return active('purchase', 'ZeoPurchase')


# ------------------------------------------------------------------ set-up
def ensure_setup():
    """the Inventory reasons that send production moves to work in progress; the Accounting accounts of absorption and variance"""
    if inventory_on():
        from ZeoInventory.models import Reason
        for code, name, kind, role in REASONS:
            Reason.objects.get_or_create(code=code, defaults={'name': name, 'kind': kind, 'account_role': role})
    if ledger_on():
        from ZeoLedger.models import GLAccount
        st = settings()
        for code, name in ((st.absorb_account, 'Production labour and overhead absorbed'), (st.variance_account, 'Production variances')):
            if code:
                GLAccount.objects.get_or_create(code=code, defaults={'name': name, 'group': 'cost_of_sales'})


def default_bom(product):
    return Bom.objects.filter(product=product, is_active=True).order_by('-is_default', '-id').first()


def unit_cost(product, warehouse=None):
    """what a component costs now: Inventory's average / standard, else the item cost; a made component: its rolled-up cost"""
    if inventory_on():
        try:
            c = call('ZeoInventory', 'unit_cost', product_id=product.pk, warehouse_id=getattr(warehouse, 'pk', warehouse))
            if c:
                return Decimal(str(c))
        except MfgError:
            pass
    b = default_bom(product)
    if b is not None and b.unit_cost:
        return Decimal(b.unit_cost)
    return Decimal(product.cost_price or 0)


def op_cost(op, minutes):
    wc = op.work_centre
    eff = Decimal(wc.efficiency or 100) / 100 or Decimal(1)
    hours = Decimal(minutes) / 60 / eff
    return r2(hours * (Decimal(wc.labour_rate) + Decimal(wc.machine_rate))), r2(hours * Decimal(wc.overhead_rate))


def rollup(bom, depth=0):
    """cost of one unit: components (with scrap; a made component rolls up its own bill first) and the operations of one batch"""
    must(depth < 8, 'The bill of materials refers to itself (a component is made from the item it makes).')
    per = Decimal(bom.qty) or Decimal(1)
    mat = D0
    for ln in bom.lines.select_related('component'):
        sub = default_bom(ln.component)
        c = rollup(sub, depth + 1)['unit'] if sub is not None and not (inventory_on() and _has_stock_cost(ln.component)) else unit_cost(ln.component)
        mat += Decimal(ln.qty) * (1 + Decimal(ln.scrap) / 100) * c
    lab = ovh = D0
    for op in bom.operations.select_related('work_centre'):
        a, b = op_cost(op, Decimal(op.setup_min) + Decimal(op.run_min) * per)
        lab += a
        ovh += b
    unit = r4((mat + lab + ovh) / per)
    if depth == 0:
        Bom.objects.filter(pk=bom.pk).update(unit_cost=unit)
    return {'materials': r2(mat / per), 'labour': r2(lab / per), 'overhead': r2(ovh / per), 'unit': unit}


def _has_stock_cost(product):
    try:
        from ZeoInventory.models import StockValue
        return StockValue.objects.filter(product=product).exists()
    except Exception:   # noqa: BLE001
        return False


def explode(bom, qty):
    per = Decimal(bom.qty) or Decimal(1)
    f = Decimal(qty) / per
    mats = [(ln.component, r4(Decimal(ln.qty) * (1 + Decimal(ln.scrap) / 100) * f)) for ln in bom.lines.select_related('component')]
    ops = [(op, r2(Decimal(op.setup_min) + Decimal(op.run_min) * Decimal(qty))) for op in bom.operations.select_related('work_centre')]
    return mats, ops


# ------------------------------------------------------------------ orders
def shortages(o):
    """components not available in the warehouse for what is still to issue"""
    if not inventory_on() or not o.warehouse_id:
        return []
    from ZeoInventory import services as IS
    out = []
    for m in o.materials.select_related('component'):
        need = Decimal(m.planned) - Decimal(m.issued)
        if need <= 0 or not m.component.track_stock:
            continue
        av = Decimal(IS.available(m.component, o.warehouse))
        if av < need:
            out.append({'item': str(m.component), 'needed': float(need), 'available': float(av), 'short': float(need - av)})
    return out


@transaction.atomic
def release(o, user=None):
    must(o.status == 'draft', 'Only a draft order is released.')
    if o.bom_id is None:
        o.bom = default_bom(o.product)
    must(o.bom is not None, f'{o.product} has no bill of materials – add one in Manufacturing › Bills of materials.', 'bom')
    must(Decimal(o.qty) > 0, 'Enter the quantity to make.', 'qty')
    st = settings()
    o.warehouse = o.warehouse or st.default_warehouse
    must(o.warehouse_id or not inventory_on(), 'Choose the warehouse (or the default warehouse in the Manufacturing set-up).', 'warehouse')
    ensure_setup()
    o.materials.all().delete()
    o.operations.all().delete()
    mats, ops = explode(o.bom, o.qty)
    for comp, q in mats:
        MaterialLine.objects.create(order=o, component=comp, planned=q)
    for op, mins in ops:
        OperationLine.objects.create(order=o, seq=op.seq, name=op.name, work_centre=op.work_centre, planned_min=mins)
    o.unit_cost = rollup(o.bom)['unit']
    if not o.number:
        o.number = app().next_number('order', 'MO')
    o.start_date = o.start_date or today()
    if not o.due_date:
        days = sum((Decimal(m) for _, m in ops), D0) / 60 / 8
        o.due_date = o.start_date + timedelta(days=int(days) + 1)
    o.status = 'released'
    o.save()
    return o


def _inv_doc(kind, o, lines, on, user, reason):
    return call('ZeoInventory', 'post_doc', kind=kind, warehouse_id=o.warehouse_id, lines=lines, date=on, reference=o.number,
                origin_app='manufacturing', origin_ref=o.number, user=user, reason_code=reason) or {}


@transaction.atomic
def issue(o, qtys=None, on=None, user=None, note=''):
    """materials out of stock to work in progress: by line {material id: qty} or everything still to issue"""
    must(o.status in ('released', 'in_progress'), 'Release the order first.')
    on = on or today()
    picked = []
    for m in o.materials.select_related('component'):
        left = Decimal(m.planned) - Decimal(m.issued)
        q = Decimal(str(qtys.get(str(m.pk), 0))) if qtys else left
        if q <= 0:
            continue
        picked.append((m, q))
    must(picked, 'Nothing left to issue.')
    return _issue(o, picked, on, user, note or 'Issued')


def _issue(o, picked, on, user, note):
    stock = [{'product_id': m.component_id, 'qty': q} for m, q in picked if m.component.track_stock]
    val, docno = D0, ''
    if stock and inventory_on():
        res = _inv_doc('consumption', o, stock, on, user, 'MFG-ISSUE')
        vals = list(res.get('lines') or [])
        val, docno = Decimal(str(res.get('value') or 0)), res.get('number', '')
        i = 0
        for m, q in picked:
            if m.component.track_stock:
                m.value += Decimal(str(vals[i])) if i < len(vals) else D0
                i += 1
    for m, q in picked:
        if not (m.component.track_stock and inventory_on()):
            v = r2(q * unit_cost(m.component))
            m.value += v
            val += v
        m.issued += q
        m.save(update_fields=['issued', 'value'])
    o.material_cost += abs(val)
    o.status = 'in_progress'
    o.save(update_fields=['material_cost', 'status'])
    Movement.objects.create(order=o, kind='issue', date=on, qty=sum((q for _, q in picked), D0), value=abs(val), document=docno, note=note, created_by=user)
    return abs(val)


def _absorb(o, amount, on, user, note):
    """Dr work in progress, Cr labour and overhead absorbed – in Accounting"""
    if not amount or not ledger_on():
        return ''
    from ZeoLedger import services as LS
    from ZeoLedger.models import GLAccount, Journal
    ensure_setup()
    st = settings()
    wip = _wip_account()
    absorbed = GLAccount.objects.get(code=st.absorb_account)
    j = Journal.objects.filter(code='GJ').first() or Journal.objects.first()
    lines = [{'account': wip, 'debit': amount if amount > 0 else 0, 'credit': -amount if amount < 0 else 0},
             {'account': absorbed, 'debit': -amount if amount < 0 else 0, 'credit': amount if amount > 0 else 0}]
    e = LS.make_entry(j, on, lines, narration=f'{o.number} {note}'[:255], reference=o.number, source='outbox', source_ref=f'mfg:{o.number}', user=user)
    return e.number


def _wip_account():
    from ZeoLedger.models import GLAccount
    code = '1450'
    if inventory_on():
        try:
            from ZeoInventory import services as IS
            code = str(IS.account('wip') or '1450')
        except Exception:   # noqa: BLE001
            pass
    a = GLAccount.objects.filter(code=code).first()
    must(a is not None, f'The work in progress account {code} is missing in the chart of accounts.')
    return a


@transaction.atomic
def report_time(o, op_id=None, minutes=None, on=None, user=None, done=True):
    """operation time: the planned minutes when none are given; labour, machine and overhead absorbed into the order"""
    must(o.status in ('released', 'in_progress'), 'Release the order first.')
    on = on or today()
    ops = o.operations.filter(pk=op_id) if op_id else o.operations.filter(done=False)
    must(ops.exists(), 'No operation left to report.')
    total = D0
    for op in ops.select_related('work_centre'):
        mins = Decimal(str(minutes)) if minutes not in (None, '') else Decimal(op.planned_min) - Decimal(op.actual_min)
        if mins <= 0 and not done:
            continue
        lab, ovh = op_cost(op, mins)
        op.actual_min += mins
        op.cost += lab + ovh
        op.done = op.done or done
        op.save(update_fields=['actual_min', 'cost', 'done'])
        o.labour_cost += lab
        o.overhead_cost += ovh
        total += lab + ovh
        Movement.objects.create(order=o, kind='time', date=on, qty=mins, value=lab + ovh, note=f'{op.seq} {op.name} – {op.work_centre.code}', created_by=user)
    doc = _absorb(o, total, on, user, 'labour and overhead')
    if doc:
        Movement.objects.filter(order=o, kind='time', document='').update(document=doc)
    o.status = 'in_progress'
    o.save(update_fields=['labour_cost', 'overhead_cost', 'status'])
    return total


@transaction.atomic
def produce(o, qty, scrap=0, on=None, user=None):
    """output into stock at the planned cost; with backflush the materials of the quantity (made and scrapped) are taken first"""
    must(o.status in ('released', 'in_progress'), 'Release the order first.')
    on = on or today()
    q, sc = Decimal(str(qty or 0)), Decimal(str(scrap or 0))
    must(q > 0 or sc > 0, 'Enter the quantity made (or scrapped).', 'qty')
    st = settings()
    limit = Decimal(o.qty) * (1 + Decimal(st.over_production or 0) / 100) - Decimal(o.done_qty) - Decimal(o.scrap_qty)
    must(q + sc <= limit + Decimal('0.0001'), f'At most {n(limit)} more can be reported ({n(st.over_production)} % over the order).', 'qty')
    if st.backflush:   # what the reported quantity should have used so far, less what was issued already
        reached = (Decimal(o.done_qty) + Decimal(o.scrap_qty) + q + sc) / Decimal(o.qty)
        picked = []
        for m in o.materials.select_related('component'):
            due = r4(Decimal(m.planned) * reached) - Decimal(m.issued)
            if due > 0:
                picked.append((m, due))
        if picked:
            _issue(o, picked, on, user, 'Backflush')
    val = D0
    docno = ''
    if q > 0:
        val = r2(q * Decimal(o.unit_cost))
        if inventory_on() and o.product.track_stock:
            res = _inv_doc('adjustment', o, [{'product_id': o.product_id, 'qty': q, 'unit_cost': o.unit_cost}], on, user, 'MFG-OUTPUT')
            val, docno = Decimal(str(res.get('value') or val)), res.get('number', '')
        o.done_qty += q
        o.output_value += val
        Movement.objects.create(order=o, kind='output', date=on, qty=q, value=val, document=docno, created_by=user)
    if sc > 0:
        o.scrap_qty += sc
        Movement.objects.create(order=o, kind='scrap', date=on, qty=sc, note='Scrapped in production (its cost stays in the order and goes to the variance)', created_by=user)
    o.status = 'done' if Decimal(o.done_qty) + Decimal(o.scrap_qty) >= Decimal(o.qty) else 'in_progress'
    o.save(update_fields=['done_qty', 'scrap_qty', 'output_value', 'status'])
    return val


def wip(o):
    return r2(Decimal(o.material_cost) + Decimal(o.labour_cost) + Decimal(o.overhead_cost) - Decimal(o.output_value))


@transaction.atomic
def close(o, on=None, user=None):
    """what is left in work in progress for the order goes to the production variance (Dr variance, Cr WIP – or the other way)"""
    must(o.status in ('done', 'in_progress'), 'Only a done order (or one in progress) is closed.')
    must(Decimal(o.done_qty) > 0 or Decimal(o.scrap_qty) > 0, 'Report the output first.')
    on = on or today()
    v = wip(o)
    doc = ''
    if v and ledger_on():
        from ZeoLedger import services as LS
        from ZeoLedger.models import GLAccount, Journal
        ensure_setup()
        var = GLAccount.objects.get(code=settings().variance_account)
        wipa = _wip_account()
        j = Journal.objects.filter(code='GJ').first() or Journal.objects.first()
        lines = [{'account': var, 'debit': v if v > 0 else 0, 'credit': -v if v < 0 else 0},
                 {'account': wipa, 'debit': -v if v < 0 else 0, 'credit': v if v > 0 else 0}]
        doc = LS.make_entry(j, on, lines, narration=f'{o.number} production variance', reference=o.number, source='outbox', source_ref=f'mfg:{o.number}', user=user).number
    o.variance = v
    o.status = 'closed'
    o.save(update_fields=['variance', 'status'])
    Movement.objects.create(order=o, kind='close', date=on, value=v, document=doc, note='Production variance (unfavourable +, favourable −)', created_by=user)
    return v


def cost_table(o):
    planned = r2(Decimal(o.unit_cost) * (Decimal(o.done_qty) or Decimal(o.qty)))
    actual = r2(Decimal(o.material_cost) + Decimal(o.labour_cost) + Decimal(o.overhead_cost))
    return {'materials': float(o.material_cost), 'labour': float(o.labour_cost), 'overhead': float(o.overhead_cost), 'actual': float(actual),
            'planned_for_output': float(planned), 'to_stock': float(o.output_value), 'in_wip': float(wip(o)), 'variance': float(o.variance)}


# ------------------------------------------------------------------ from Sales, planning
@transaction.atomic
def from_sales(sales_order, user=None):
    """a production order for each line of a confirmed Sales order whose item has a bill of materials (what is not in stock)"""
    made = []
    for ln in sales_order.lines.select_related('product'):
        if not ln.product_id or default_bom(ln.product) is None:
            continue
        need = Decimal(ln.qty) - Decimal(ln.delivered)
        if inventory_on() and ln.product.track_stock and sales_order.warehouse_id:
            from ZeoInventory import services as IS
            need -= max(D0, Decimal(IS.available(ln.product, sales_order.warehouse)))
        open_qty = sum((Decimal(x.qty) - Decimal(x.done_qty) for x in ProductionOrder.objects.filter(source='sales', source_ref=sales_order.number, product=ln.product)
                        .exclude(status__in=('cancelled', 'closed'))), D0)
        need -= open_qty
        if need > 0:
            o = ProductionOrder.objects.create(product=ln.product, bom=default_bom(ln.product), qty=need, warehouse=sales_order.warehouse or settings().default_warehouse,
                                               due_date=sales_order.delivery_date, branch=sales_order.branch, source='sales', source_ref=sales_order.number, created_by=user,
                                               number=app().next_number('order', 'MO'))
            made.append(o)
    must(made, f'Nothing to make for {sales_order.number}: no line with a bill of materials short of stock.')
    return made


def plan(user=None, horizon_days=None):
    """demand: confirmed Sales orders not delivered (made items) up to the horizon + the made items below their reorder point;
    supply: on hand + open production orders → what to make; then the components of what to make against their stock → what to buy"""
    from collections import defaultdict
    st = settings()
    hz = today() + timedelta(days=horizon_days or st.plan_days or 30)
    demand = defaultdict(lambda: D0)
    why = defaultdict(list)
    try:
        from ZeoSales.models import OrderLine
        for ln in OrderLine.objects.filter(order__status__in=('confirmed', 'part_delivered')).select_related('product', 'order'):
            if ln.product_id and (ln.order.delivery_date is None or ln.order.delivery_date <= hz) and default_bom(ln.product) is not None:
                left = Decimal(ln.qty) - Decimal(ln.delivered)
                if left > 0:
                    demand[ln.product_id] += left
                    why[ln.product_id].append(ln.order.number)
    except Exception:   # noqa: BLE001 – Sales not installed
        pass
    rules = {}
    if inventory_on():
        from ZeoInventory.models import ReorderRule
        for r in ReorderRule.objects.filter(is_active=True).select_related('product'):
            if default_bom(r.product) is not None:
                rules[r.product_id] = r
    from ZeoMasters.models import Product
    rows = []
    stock = {}

    def onhand(p):
        if p.pk not in stock:
            if inventory_on() and p.track_stock:
                from ZeoInventory import services as IS
                stock[p.pk] = Decimal(IS.on_hand(p))
            else:
                stock[p.pk] = D0
        return stock[p.pk]

    comp_need = defaultdict(lambda: D0)
    comp_why = defaultdict(list)
    for pid in set(demand) | set(rules):
        p = Product.objects.get(pk=pid)
        open_mo = sum((Decimal(x.qty) - Decimal(x.done_qty) - Decimal(x.scrap_qty) for x in ProductionOrder.objects.filter(product=p, status__in=('draft', 'released', 'in_progress'))), D0)
        target = demand[pid]
        r = rules.get(pid)
        if r is not None:
            target += Decimal(r.max_qty or r.min_qty or 0) if onhand(p) + open_mo - demand[pid] <= Decimal(r.min_qty) else D0
        make = r4(target - onhand(p) - open_mo)
        if make <= 0:
            continue
        b = default_bom(p)
        rows.append({'kind': 'make', 'item_id': p.pk, 'item': str(p), 'qty': float(make), 'on_hand': float(onhand(p)), 'open_orders': float(open_mo),
                     'for': ', '.join(why[pid][:5]) or 'reorder point', 'bom_id': b.pk})
        for comp, q in explode(b, make)[0]:
            comp_need[comp.pk] += q
            comp_why[comp.pk].append(str(p))
    for cid, q in comp_need.items():
        c = Product.objects.get(pk=cid)
        if default_bom(c) is not None:
            continue   # a made component: planned on the next run (one level per run keeps it readable)
        reserved = sum((Decimal(m.planned) - Decimal(m.issued) for m in MaterialLine.objects.filter(component=c, order__status__in=('released', 'in_progress'))), D0)
        buy = r4(q + reserved - onhand(c))
        if buy > 0:
            rows.append({'kind': 'buy', 'item_id': c.pk, 'item': str(c), 'qty': float(buy), 'on_hand': float(onhand(c)), 'open_orders': float(reserved),
                         'for': ', '.join(sorted(set(comp_why[cid]))[:5])})
    p_ = Plan.objects.create(number=app().next_number('plan', 'MRP'), date=today(), horizon=hz, rows=rows, created_by=user)
    return p_


@transaction.atomic
def apply_plan(p, user=None):
    must(p.status == 'draft', 'The orders of this run are made already.')
    from ZeoMasters.models import Product
    made, req = [], None
    for x in p.rows or []:
        if x['kind'] == 'make':
            o = ProductionOrder.objects.create(product_id=x['item_id'], bom_id=x.get('bom_id'), qty=Decimal(str(x['qty'])), warehouse=settings().default_warehouse,
                                               source='planning', source_ref=p.number, created_by=user, number=app().next_number('order', 'MO'))
            made.append(o)
    buys = [x for x in p.rows or [] if x['kind'] == 'buy']
    if buys and purchase_on():
        from ZeoPurchase.models import Request
        from ZeoPurchase.app import app as pa
        req = Request.objects.create(number=pa.next_number('request', 'PR'), date=today(), requested_by=user, source='planning', status='approved',
                                     department='Production', warehouse=settings().default_warehouse,
                                     lines=[{'product': x['item_id'], 'qty': str(x['qty']), 'note': f'Planning {p.number}: {x["for"]}'} for x in buys])
    p.status = 'done'
    p.save(update_fields=['status'])
    return made, req, buys


def daily():
    return {}
