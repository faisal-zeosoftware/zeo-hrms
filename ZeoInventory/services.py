"""ZEO Inventory engine (v1.75.0): posting, costing (replay per costing key), landed cost, journals, valuation at any date,
ageing, Inventory ↔ GL reconciliation, the periodic closing stock JV and the period close.

How costs are kept (Business Central): a posted movement never changes. Its cost is worked out by replaying, in date order, every
movement of its costing key (item × company / branch / warehouse) with the item's costing method; when a late or back-dated
movement or a landed cost changes the cost an outflow should have had, the difference is booked as a new cost amount
(StockValue "adjustment") dated on the outflow's date – or on the first open day when that period is closed – and posted to the GL."""
import datetime
import threading
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import F, Max, Min, Q, Sum
from django.utils import timezone

from .models import (ACCOUNT_ROLES, ADJ_TYPES, OperationType, DEFAULT_ACCOUNTS, AccountSet, Bin, ClosingJV, PutawayRule, CostApplication, CostLayer, InvSequence, InvSettings, ItemSetting,
                     LandedCost, Reservation, StandardCost, StockDoc, StockDocLine, StockMove, StockValue, Warehouse)

Z = Decimal('0')
C2 = Decimal('0.01')
C6 = Decimal('0.000001')
IN_KINDS = ('receipt', 'opening', 'return_in', 'transfer_in', 'dropship_in')
OFFSET = {'receipt': 'grni', 'opening': 'opening', 'return_in': 'cogs', 'delivery': 'cogs', 'consumption': 'consumption', 'scrap': 'scrap',
          'adjustment': 'adjustment', 'count': 'count_variance', 'return_out': 'grni', 'transfer_out': 'transit', 'transfer_in': 'transit',
          'dropship_in': 'grni', 'dropship_out': 'cogs', 'consign_in': 'consign_payable', 'consign_out': 'consign_payable'}


class InvError(Exception):
    def __init__(self, message, field=None, status=400):
        super().__init__(message)
        self.message, self.field, self.status = message, field, status


_TL = threading.local()


def q2(v):
    """an amount, rounded to the company's amount precision (set by settings(); 2 until then)"""
    return Decimal(v or 0).quantize(getattr(_TL, 'amt', C2), ROUND_HALF_UP)


def q6(v):
    return Decimal(v or 0).quantize(C6, ROUND_HALF_UP)


def settings():
    st = InvSettings.get()
    if st.default_set_id is None and st.activated_at:   # v1.75.0 the company default account set (Sage 300), made once
        try:
            from .models import SET_ROLES
            ds = AccountSet.objects.filter(code='STD', parent__isnull=True).first() or AccountSet.objects.create(
                code='STD', name='Standard (company default)', kind='item',
                accounts={r: str((st.accounts or {}).get(r) or DEFAULT_ACCOUNTS.get(r, '')) for r in SET_ROLES})
            InvSettings.objects.filter(pk=st.pk).update(default_set=ds)
            st.default_set_id, st.default_set = ds.pk, ds
        except Exception:   # noqa: BLE001 – a read-only moment; next time
            pass
    try:
        _TL.amt = Decimal(1).scaleb(-int(st.dp_amount if st.dp_amount is not None else currency_decimals()))
    except Exception:
        _TL.amt = C2
    return st


# ------------------------------------------------------------------ decimal precision (Odoo "decimal accuracy")
def currency_decimals():
    try:
        from CountryPack.services import current_pack
        return int((current_pack().get('currency') or {}).get('decimals', 2))
    except Exception:
        return 2


def dp(kind, st=None):
    st = st or settings()
    if kind == 'amount':
        return st.dp_amount if st.dp_amount is not None else currency_decimals()
    return {'cost': st.dp_cost, 'qty': st.dp_qty, 'weight': st.dp_weight, 'percent': st.dp_percent}[kind]


def rnd(v, places):
    return Decimal(v or 0).quantize(Decimal(1).scaleb(-int(places)), ROUND_HALF_UP)


def ra(v, st=None):
    """an amount, to the company's amount precision"""
    return rnd(v, dp('amount', st)) if st is not None else q2(v)


def rq(v, st=None):
    return rnd(v, dp('qty', st))


def rcost(v, st=None):
    return rnd(v, dp('cost', st))


def too_precise(v, places):
    """True when `v` has more decimals than allowed"""
    if v in (None, ''):
        return False
    d = Decimal(str(v)).normalize()
    return -d.as_tuple().exponent > int(places) if d.as_tuple().exponent < 0 else False


# ------------------------------------------------------------------ units of measure
def to_root(u):
    """(root unit, factor): how many of the root (base) unit one `u` is – following Masters' "multiple of" chain"""
    f, seen = Decimal(1), set()
    while u is not None and u.base_id and u.pk not in seen:
        seen.add(u.pk)
        f *= Decimal(u.factor or 1)
        u = u.base
    return u, f


def unit_factor(product, uom):
    """stock units of `product` in one `uom`: the item's own conversion, else the shared units in the same category"""
    from .models import ItemUnit
    stock = product.uom
    if uom is None or (stock is not None and uom.pk == stock.pk):
        return Decimal(1)
    iu = ItemUnit.objects.filter(product=product, uom=uom, is_active=True).first()
    if iu is not None:
        return Decimal(iu.factor)
    if stock is None:
        raise InvError(f'{product} has no stock unit – set its unit in Masters › Items, or add {uom.code} to the item\'s units.', 'uom')
    r1, f1 = to_root(uom)
    r2, f2 = to_root(stock)
    if r1 is None or r2 is None or r1.pk != r2.pk:
        raise InvError(f'{uom.code} cannot be turned into {stock.code} for {product} – add it to the item\'s units with its conversion.', 'uom')
    return f1 / f2


def unit_rounding(uom):
    from .models import UnitSetting
    if uom is None:
        return None
    x = UnitSetting.objects.filter(uom=uom).first()
    return Decimal(x.rounding) if x is not None else None


def line_base(ln, save=True, st=None):
    """the line in the item's stock unit: base quantity, base cost (catch weight: priced per kg of the weight), base counted"""
    st = st or settings()
    f = unit_factor(ln.product, ln.uom)
    ln.factor = f
    ln.base_qty = rq(Decimal(ln.qty or 0) * f, st)
    ln.base_counted = rq(Decimal(ln.counted_qty) * f, st) if ln.counted_qty is not None else None
    s = item_setting(ln.product)
    if s is not None and s.catch_weight and ln.unit_cost is not None and ln.weight and ln.base_qty:
        ln.base_cost = Decimal(ln.weight) * Decimal(ln.unit_cost) / abs(ln.base_qty)   # unit cost is per kg
    else:
        ln.base_cost = Decimal(ln.unit_cost) / f if ln.unit_cost is not None else None
    rate = doc_rate(ln.doc, st) if ln.doc_id else Decimal(1)
    if ln.base_cost is not None and rate != 1:   # v1.75.0 a cost in a foreign currency is kept in the company currency
        ln.base_cost = Decimal(ln.base_cost) * rate
    if save and ln.pk:
        type(ln).objects.filter(pk=ln.pk).update(factor=ln.factor, base_qty=ln.base_qty, base_counted=ln.base_counted, base_cost=ln.base_cost)
    return ln


def doc_rate(doc, st=None):
    """company currency per 1 unit of the document's currency (1 when it is the company's)"""
    st = st or settings()
    if not doc.currency or doc.currency.upper() == (st.currency or '').upper():
        return Decimal(1)
    if doc.rate:
        return Decimal(doc.rate)
    r = fx_rate(doc.currency, doc.date)
    if r is None:
        raise InvError(f'Enter the exchange rate of {doc.currency} (none found in Accounting for {doc.date:%d/%m/%Y}).', 'rate')
    return r


def fx_rate(cur, on):
    """the exchange rate from Accounting / Masters when there is one"""
    for label, model in (('ZeoLedger', 'FxRate'), ('ZeoAccounting', 'ExchangeRate'), ('ZeoMasters', 'ExchangeRate'), ('ZeoMasters', 'CurrencyRate')):
        try:
            M_ = apps.get_model(label, model)
        except LookupError:
            continue
        try:
            f = [x.name for x in M_._meta.fields]
            cf = 'currency' if 'currency' in f else ('code' if 'code' in f else None)
            df = 'date' if 'date' in f else ('valid_from' if 'valid_from' in f else None)
            rf = 'rate' if 'rate' in f else None
            if not (cf and rf):
                continue
            qs = M_.objects.filter(**{cf: cur.upper()})
            if df:
                qs = qs.filter(**{f'{df}__lte': on}).order_by(f'-{df}')
            r = qs.first()
            if r is not None:
                return Decimal(getattr(r, rf))
        except Exception:   # noqa: BLE001
            continue
    return None


def today():
    from zeo.appkit.engine import today as _t
    return _t()


def next_seq():
    with transaction.atomic():
        s, _ = InvSequence.objects.select_for_update().get_or_create(kind='_seq')
        s.last += 1
        s.save(update_fields=['last'])
        return s.last


# ------------------------------------------------------------------ item set-up, costing keys, accounts
def item_setting(product):
    return ItemSetting.objects.filter(product=product).first()


def method_of(product, st=None):
    s = item_setting(product)
    if s and s.method:
        return s.method
    if s and s.tracking == 'serial':
        return 'serial'
    if s and s.tracking == 'lot':
        return 'lot'
    if getattr(product, 'valuation', '') in ('fifo', 'standard'):
        return product.valuation
    return (st or settings()).default_method or 'average'


def tracking_of(product):
    s = item_setting(product)
    return s.tracking if s else 'none'


def costing_key(product_id, warehouse, st=None):
    lvl = (st or settings()).costing_level
    if lvl == 'warehouse':
        return f'w{warehouse.pk}:{product_id}'
    if lvl == 'branch':
        return f'b{warehouse.branch_id or 0}:{product_id}'
    return f'c:{product_id}'


def std_cost(product, on):
    r = StandardCost.objects.filter(product=product, effective_from__lte=on).order_by('-effective_from').first()
    if r is not None:
        return Decimal(r.cost)
    s = item_setting(product)
    if s and s.standard_cost is not None:
        return Decimal(s.standard_cost)
    return Decimal(product.standard_cost or product.cost_price or 0)


def _cat_chain(cat_id):
    from .models import CategoryAccounts
    PC = apps.get_model('ZeoMasters', 'ProductCategory')
    out, seen = [], set()
    c = PC.objects.filter(pk=cat_id).first() if cat_id else None
    while c is not None and c.pk not in seen:
        seen.add(c.pk)
        ca = CategoryAccounts.objects.filter(category=c).select_related('account_set', 'production_set').first()
        out.append((c, ca))
        c = c.parent
    return out


def item_set(product, st=None, kind='item'):
    """the item's account set (Sage 300): its own, else its category's (or a parent category's), else the company default set → (set, from)"""
    f = 'account_set' if kind == 'item' else 'production_set'
    ist = ItemSetting.objects.filter(product=product).select_related(f).first() if product is not None else None
    if ist is not None and getattr(ist, f + '_id'):
        return getattr(ist, f), 'item'
    for c, ca in _cat_chain(getattr(product, 'category_id', None)):
        if ca is not None and getattr(ca, f + '_id'):
            return getattr(ca, f), f'category {c.code}'
    if kind == 'item':
        st = st or settings()
        if st.default_set_id:
            return st.default_set, 'company default set'
    return None, ''


def account_trace(role, product=None, warehouse=None, st=None):
    """(account, where it came from): category roles → the item category (and its parents); set roles → the item's account set,
    its warehouse override; then the company accounts in the set-up and the built-in code. 1.75.0 draft rows by category / warehouse are read last."""
    from .models import CATEGORY_ROLES, PROD_ROLES
    st = st or settings()
    wid = getattr(warehouse, 'pk', warehouse)
    if role == 'inventory' and getattr(warehouse, 'is_transit', False):   # v1.75.0 stock in transit is on the transit account
        acc, frm = account_trace('transit', product, None, st)
        return acc, f'in transit – {frm}'
    if role in CATEGORY_ROLES:
        for c, ca in _cat_chain(getattr(product, 'category_id', None)):
            if ca is not None and (ca.accounts or {}).get(role):
                return str(ca.accounts[role]), f'category {c.code}'
    prod = role in {k for k, _, _ in PROD_ROLES}
    aset, frm = item_set(product, st, 'production' if prod else 'item') if product is not None else ((st.default_set, 'company default set') if st.default_set_id and not prod else (None, ''))
    if aset is not None:
        if wid:
            loc = AccountSet.objects.filter(parent=aset, warehouse_id=wid, is_active=True).first()
            if loc is not None and (loc.accounts or {}).get(role):
                return str(loc.accounts[role]), f'set {aset.code or aset.name} × warehouse {loc.warehouse.code}'
        if (aset.accounts or {}).get(role):
            return str(aset.accounts[role]), f'set {aset.code or aset.name} ({frm})'
    cat = getattr(product, 'category_id', None)
    legacy = AccountSet.objects.filter(is_active=True, parent__isnull=True, code='')
    for flt in ((cat, wid), (cat, None), (None, wid)):
        if flt == (None, None):
            continue
        x = legacy.filter(category_id=flt[0], warehouse_id=flt[1]).first()
        if x is not None and (x.accounts or {}).get(role):
            return str(x.accounts[role]), f'account set {x.name}'
    acc = (st.accounts or {}).get(role)
    if acc:
        return str(acc), 'company accounts (set-up)'
    return DEFAULT_ACCOUNTS.get(role, ''), 'built-in default'


def account(role, product=None, warehouse=None, st=None):
    return account_trace(role, product, warehouse, st)[0]


def accounts_used(product, warehouse=None, st=None):
    from .models import PROD_ROLES
    st = st or settings()
    names = {k: n for k, n, _ in ACCOUNT_ROLES}
    names.update({k: n for k, n, _ in PROD_ROLES})
    roles = [k for k, _, _ in ACCOUNT_ROLES] + ([k for k, _, _ in PROD_ROLES] if item_set(product, st, 'production')[0] is not None else [])
    return [{'role': names[r], 'code': r, 'account': account_trace(r, product, warehouse, st)[0], 'from': account_trace(r, product, warehouse, st)[1]} for r in roles]


@transaction.atomic
def change_item_set(ist, new_set, on=None, user=None):
    """an item moves to another account set: with stock held, its value moves from the old Inventory account(s) to the new one
    (a transfer journal, Sage-like control); later postings use the new set"""
    st = settings()
    on = on or today()
    check_open(on, st)
    product = ist.product
    old_by = defaultdict(lambda: Z)
    for r in StockValue.objects.filter(product=product, in_stock=True).values('inventory_account', 'warehouse_id', 'costing_key').annotate(s=Sum('amount')):
        old_by[(r['inventory_account'], r['warehouse_id'], r['costing_key'])] += Decimal(r['s'] or 0)
    ist.account_set = new_set
    ist.save(update_fields=['account_set'])
    whs = {w.pk: w for w in Warehouse.objects.all()}
    made = []
    for (acc, wid, key), amt in old_by.items():
        amt = q2(amt)
        new_acc = account('inventory', product, whs.get(wid), st)
        if not amt or new_acc == acc:
            continue
        clr = account('revaluation', product, whs.get(wid), st)
        gl = 'none' if st.accounting_mode == 'periodic' else 'pending'
        note = f'Account set changed to {new_set.code or new_set.name}: stock value moved {acc} → {new_acc}'
        for a, inv in ((-amt, acc), (amt, new_acc)):
            made.append(StockValue.objects.create(product=product, warehouse_id=wid, costing_key=key, valuation_date=on, seq=next_seq(), kind='transfer',
                                                  amount=a, inventory_account=inv, offset_role='revaluation', offset_account=clr, gl=gl, note=note))
    gl_after_post(st)
    return made


# ------------------------------------------------------------------ quantities
def on_hand(product, warehouse=None, on=None, lot=None, serial=None, bin_=None):
    qs = StockMove.objects.filter(product=product)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    if on is not None:
        qs = qs.filter(date__lte=on)
    if lot:
        qs = qs.filter(lot=lot)
    if serial:
        qs = qs.filter(serial=serial)
    if bin_ is not None:
        qs = qs.filter(bin=bin_)
    return Decimal(qs.aggregate(s=Sum('qty'))['s'] or 0)


def reserved(product, warehouse):
    return Decimal(Reservation.objects.filter(product=product, warehouse=warehouse, status='active').aggregate(s=Sum('qty'))['s'] or 0)


def available(product, warehouse):
    return on_hand(product, warehouse) - reserved(product, warehouse)


def first_open_day(st=None):
    lk = (st or settings()).lock_date
    return lk + datetime.timedelta(days=1) if lk else None


def check_open(on, st=None):
    lk = (st or settings()).lock_date
    if lk and on <= lk:
        raise InvError(f'The stock period is closed up to {lk:%d/%m/%Y} – post on a later date.', 'date')


def open_date(on, st=None):
    lk = (st or settings()).lock_date
    return on if not lk or on > lk else lk + datetime.timedelta(days=1)


# ------------------------------------------------------------------ posting a document
def _lines(doc):
    return list(doc.lines.select_related('product', 'bin', 'to_bin'))


def validate(doc, st=None):
    st = st or settings()
    if doc.status != 'draft':
        raise InvError('Only a draft can be posted.')
    check_open(doc.date, st)
    lines = _lines(doc)
    if not lines:
        raise InvError('Add the items first.')
    if doc.kind == 'transfer':
        if not doc.to_warehouse_id:
            raise InvError('Choose the warehouse to transfer to.', 'to_warehouse')
        if doc.to_warehouse_id == doc.warehouse_id:
            raise InvError('Transfer to another warehouse.', 'to_warehouse')
    out_need = defaultdict(Decimal)
    if doc.kind == 'adjustment' and st.adj_reason and not doc.reason_id:
        raise InvError('Choose the reason of the adjustment (the set-up asks for one on every adjustment).')
    for i, ln in enumerate(lines, 1):
        p = ln.product
        if not p.track_stock and p.kind not in ('goods', 'spare_part', 'consumable', 'bundle'):
            raise InvError(f'Line {i}: {p} is not a stock item (Items › track stock).')
        if doc.kind == 'count':
            if ln.base_counted is None or ln.base_counted < 0:
                raise InvError(f'Line {i}: enter the counted quantity.')
        elif doc.kind == 'adjustment':
            if ln.adj_type in COST_ONLY:
                if not ln.cost_amount or ln.cost_amount <= 0:
                    raise InvError(f'Line {i}: enter the cost amount.')
                if method_of(p, st) == 'standard':
                    raise InvError(f'Line {i}: {p} is at standard cost – change its cost in Standard costs (a new cost from a date).')
                continue
            if not ln.base_qty:
                raise InvError(f'Line {i}: enter the quantity (+ in, − out).')
            if ln.adj_type in ('both_in', 'both_out') and (not ln.cost_amount or ln.cost_amount <= 0):
                raise InvError(f'Line {i}: enter the cost amount of the quantity.')
        elif ln.base_qty is None or ln.base_qty <= 0:
            raise InvError(f'Line {i}: the quantity must be more than 0.')
        tr = tracking_of(p)
        if doc.operation_id and doc.operation.needs_lots and not (ln.lot or ln.serial):
            raise InvError(f'Line {i}: {doc.operation.name} asks for the lot or serial number of every line.')
        if tr == 'lot' and not ln.lot:
            raise InvError(f'Line {i}: {p} is tracked by lot – enter the lot.')
        if tr in ('lot', 'serial') and doc.kind in ('delivery', 'consumption', 'transfer', 'dropship') and (ln.lot or ln.serial):
            from .models import LotRecord
            lr = LotRecord.objects.filter(product=p, lot=ln.lot if tr == 'lot' else '', serial=ln.serial if tr == 'serial' else '').first()
            if lr is not None and lr.status != 'available':
                raise InvError(f'Line {i}: {p} {ln.lot or ln.serial} is {lr.get_status_display().lower()} – it cannot leave.')
            if lr is not None and lr.expiry and lr.expiry < doc.date and doc.kind != 'transfer':
                raise InvError(f'Line {i}: {p} {ln.lot or ln.serial} expired on {lr.expiry:%d/%m/%Y}.')
        if tr == 'serial':
            if not ln.serial:
                raise InvError(f'Line {i}: {p} is tracked by serial number – enter it.')
            if doc.kind != 'count' and abs(ln.base_qty) != 1:
                raise InvError(f'Line {i}: a serial number is one unit – one line per serial number.')
        if doc.kind in ('receipt', 'opening', 'dropship') and (ln.base_cost is None or ln.base_cost < 0) and not ln.owner_id:
            if not (method_of(p, st) == 'standard'):
                raise InvError(f'Line {i}: enter the unit cost.')
        inflow = doc.kind in ('receipt', 'opening', 'return_in', 'dropship') or (doc.kind == 'adjustment' and ln.base_qty > 0)
        if tr == 'serial' and inflow and doc.kind != 'dropship' and on_hand(p, serial=ln.serial) > 0:
            raise InvError(f'Line {i}: serial number {ln.serial} is already in stock.')
        if not inflow and doc.kind != 'count':
            out_need[(p.pk, ln.lot if tr == 'lot' else '', ln.serial if tr == 'serial' else '')] += abs(ln.base_qty)
    wh = doc.warehouse
    _TL.warnings = []
    for (pid, lot, ser), need in out_need.items():
        p = next(x.product for x in lines if x.product_id == pid)
        pol = neg_policy(p, wh, st)
        if pol == 'allow':
            continue
        low, low_on = lowest_balance(p, wh, doc.date, lot=lot or None, serial=ser or None)
        if need > low:
            what = f' lot {lot}' if lot else (f' serial {ser}' if ser else '')
            when = 'at its date' if low_on == doc.date else f'on {low_on:%d/%m/%Y} (a later document uses it)'
            msg = (f'Not enough {p}{what} in {wh.code} {when}: {low.normalize():f} available, {need.normalize():f} needed')
            if pol == 'block':
                raise InvError(msg + ' – negative stock is not allowed.')
            _TL.warnings.append(msg + ' – posted below zero (the policy warns); the cost is corrected when the receipt comes.')
    if doc.document_date and (doc.document_date.year, doc.document_date.month) != (doc.date.year, doc.date.month):
        _TL.warnings.append(f'The document date {doc.document_date:%d/%m/%Y} and the posting date {doc.date:%d/%m/%Y} are in different months.')
    return lines


def neg_policy(product, wh, st=None):
    """block / warn / allow: the item's own, else the warehouse's, else the company's; lots and serial numbers are never negative"""
    if tracking_of(product) in ('lot', 'serial'):
        return 'block'
    ist = item_setting(product)
    if ist is not None and ist.negative:
        return ist.negative
    if wh is not None and wh.negative:
        return wh.negative
    if wh is not None and wh.allow_negative:
        return 'allow'
    return (st or settings()).negative or 'block'


def lowest_balance(product, wh, on, lot=None, serial=None):
    """(the lowest quantity, its date) from `on` onwards – a back-dated issue must not make stock negative at its date or after"""
    base = on_hand(product, wh, on=on, lot=lot, serial=serial)
    qs = StockMove.objects.filter(product=product, warehouse=wh, date__gt=on)
    if lot:
        qs = qs.filter(lot=lot)
    if serial:
        qs = qs.filter(serial=serial)
    low, low_on, run_ = base, on, base
    for r in qs.values('date').annotate(s=Sum('qty')).order_by('date'):
        run_ += Decimal(r['s'] or 0)
        if run_ < low:
            low, low_on = run_, r['date']
    return low, low_on


def warnings():
    return list(getattr(_TL, 'warnings', []) or [])


def backdate_impact(doc):
    """before a back-dated document is posted: the later documents whose cost changes and by how much (posted in a savepoint and rolled back)"""
    pids = list(doc.lines.values_list('product_id', flat=True))
    later = list(StockDoc.objects.filter(status='posted', moves__product_id__in=pids, date__gt=doc.date).distinct().values_list('pk', 'total_value'))
    if not later or doc.status != 'draft':
        return []
    before = dict(later)
    out = []
    with transaction.atomic():
        sid = transaction.savepoint()
        try:
            post(StockDoc.objects.get(pk=doc.pk))
            for d in StockDoc.objects.filter(pk__in=before).order_by('date', 'pk'):
                if q2(d.total_value) != q2(before[d.pk]):
                    out.append({'id': d.pk, 'number': d.number, 'date': d.date, 'kind': d.get_kind_display(), 'before': float(before[d.pk]),
                                'after': float(d.total_value), 'change': float(q2(Decimal(d.total_value) - Decimal(before[d.pk])))})
        except InvError as e:
            out = [{'error': e.message}]
        finally:
            transaction.savepoint_rollback(sid)
    return out


def putaway(product, wh):
    """the bin for goods coming into `wh`: the first put-away rule (by priority; an item rule before a category rule before a general one)"""
    rules = [r for r in PutawayRule.objects.filter(warehouse=wh, is_active=True).select_related('bin')
             if (r.product_id in (None, product.pk)) and (r.category_id in (None, product.category_id)) and not (r.product_id and r.product_id != product.pk)]
    rules.sort(key=lambda r: (r.priority, r.product_id is None, r.category_id is None, r.pk))
    for r in rules:
        if r.method in ('fixed', 'receiving') and r.bin_id and r.bin.is_active:
            return r.bin
        if r.method == 'same':
            held = (StockMove.objects.filter(product=product, warehouse=wh, bin__isnull=False, bin__is_active=True).values('bin_id')
                    .annotate(q=Sum('qty')).filter(q__gt=0).order_by('-q'))
            if held:
                return Bin.objects.get(pk=held[0]['bin_id'])
        if r.method == 'empty':
            bins = Bin.objects.filter(warehouse=wh, is_active=True)
            if r.zone:
                bins = bins.filter(zone=r.zone)
            if r.bin_kind:
                bins = bins.filter(kind=r.bin_kind)
            used = {x['bin_id'] for x in StockMove.objects.filter(warehouse=wh, bin__isnull=False).values('bin_id').annotate(q=Sum('qty')).filter(q__gt=0)}
            b = next((b for b in bins.order_by('code') if b.pk not in used), None)
            if b is not None:
                return b
    return None


def _bins(doc, ln):
    """the bins of a line left without: the operation type's default bins, then the put-away rules for what comes in"""
    op = doc.operation
    inflow = doc.kind in ('receipt', 'opening', 'return_in') or (doc.kind in ('adjustment', 'count') and (ln.base_qty or 0) > 0)
    changed = []
    if ln.bin_id is None:
        b = op.default_bin if op is not None and op.default_bin_id else None
        if b is None and inflow:
            b = putaway(ln.product, doc.warehouse)
        if b is not None:
            ln.bin = b
            changed.append('bin')
    if doc.kind == 'transfer' and ln.to_bin_id is None:
        b = op.default_to_bin if op is not None and op.default_to_bin_id else None
        if b is None:
            b = putaway(ln.product, doc.to_warehouse)
        if b is not None:
            ln.to_bin = b
            changed.append('to_bin')
    if changed:
        ln.save(update_fields=changed)


def _move(doc, ln, kind, wh, qty, st, in_cost=None, source=None, lot=None, serial=None, bin_=None, offset=None, owner=None):
    p = ln.product
    own = owner if owner is not None else (ln.owner if getattr(ln, 'owner_id', None) and kind in ('receipt', 'return_out') else None)
    return StockMove.objects.create(doc=doc, line=ln, date=doc.date, seq=next_seq(), kind=kind, product=p, warehouse=wh, branch_id=wh.branch_id,
                                    bin=bin_, lot=ln.lot if lot is None else lot, serial=ln.serial if serial is None else serial, expiry=ln.expiry,
                                    qty=qty, costing_key=(f'own{own.pk}:{p.pk}' if own is not None else costing_key(p.pk, wh, st)),
                                    in_cost=None if own is not None else in_cost, source_move=source, owner=own,
                                    offset_role=offset or OFFSET.get(kind, 'adjustment'))


def consign_price(partner, product, on):
    from .models import ConsignmentPrice
    r = ConsignmentPrice.objects.filter(partner=partner, product=product, valid_from__lte=on).order_by('-valid_from').first()
    if r is None:
        raise InvError(f'No agreed consignment price of {product} with {partner} at {on:%d/%m/%Y} – add it in Consignment prices.')
    return Decimal(r.price)


def consign_stock(product, wh, on=None):
    """{supplier: qty} of supplier consignment stock in the warehouse (oldest receipt first)"""
    qs = StockMove.objects.filter(product=product, warehouse=wh, owner__isnull=False)
    if on is not None:
        qs = qs.filter(date__lte=on)
    out = {}
    P = apps.get_model('ZeoMasters', 'Partner')
    for r in qs.values('owner_id').annotate(s=Sum('qty'), first=Min('date')).order_by('first'):
        if Decimal(r['s'] or 0) > 0:
            out[P.objects.get(pk=r['owner_id'])] = Decimal(r['s'])
    return out


def _takeover(doc, ln, wh, need, st):
    """stock leaving that is the supplier's: it becomes ours at the agreed price first (Dr Inventory / Cr Consignment payable)"""
    p = ln.product
    cons = consign_stock(p, wh, doc.date)
    if not cons:
        return []
    own = Decimal(StockMove.objects.filter(product=p, warehouse=wh, owner__isnull=True, date__lte=doc.date).aggregate(s=Sum('qty'))['s'] or 0)
    ist = item_setting(p)
    tot = sum(cons.values(), Z)
    t = min(need, tot) if (ist is not None and ist.consign_first) else min(max(Z, need - max(own, Z)), tot)
    made = []
    for partner, avail in cons.items():
        if t <= 0:
            break
        take = min(t, avail)
        price = consign_price(partner, p, doc.date)
        made.append(_move(doc, ln, 'consign_out', wh, -take, st, owner=partner))
        made.append(_move(doc, ln, 'consign_in', wh, take, st, in_cost=price, offset='consign_payable'))
        t -= take
    return made


def removal_of(product, wh):
    s = item_setting(product)
    return (s.removal if s and s.removal else '') or wh.removal or 'fefo'


def pick_stock(product, wh, qty, lot='', on=None, bin_id=None):
    """[(lot, bin_id, qty)] – where a delivery of `qty` is taken from, by the removal strategy (FEFO: earliest expiry, then oldest;
    FIFO: oldest receipt; LIFO: newest); within one lot when `lot` is given"""
    qs = StockMove.objects.filter(product=product, warehouse=wh)
    if on is not None:
        qs = qs.filter(date__lte=on)
    if lot:
        qs = qs.filter(lot=lot)
    if bin_id:
        qs = qs.filter(bin_id=bin_id)
    rows = [r for r in qs.values('lot', 'bin_id').annotate(q=Sum('qty'), first=Min('date'), last=Max('date'), exp=Min('expiry')) if r['q'] > 0]
    if not lot:   # v1.75.0 lots not available (quarantine, blocked, expired, recalled) or too short-lived are not taken
        from .models import LotRecord
        bad = set(LotRecord.objects.filter(product=product).exclude(status='available').values_list('lot', flat=True))
        ist = item_setting(product)
        day = on or today()
        minl = ist.min_life_days if ist is not None and ist.min_life_days else 0
        rows = [r for r in rows if r['lot'] not in bad and not (r['exp'] and (r['exp'] < day or (r['exp'] - day).days < minl))]
    how = removal_of(product, wh)
    far = datetime.date(9999, 12, 31)
    if how == 'lifo':
        rows.sort(key=lambda r: (r['last'], r['lot'], r['bin_id'] or 0), reverse=True)
    elif how == 'fifo':
        rows.sort(key=lambda r: (r['first'], r['lot'], r['bin_id'] or 0))
    else:
        rows.sort(key=lambda r: (r['exp'] or far, r['first'], r['lot'], r['bin_id'] or 0))
    out, need = [], Decimal(qty)
    for r in rows:
        if need <= 0:
            break
        take = min(Decimal(r['q']), need)
        out.append((r['lot'], r['bin_id'], take))
        need -= take
    return out


def _allocate_out(doc):
    """an outgoing line without its lot (lot items) or bin (when the item is held in bins) gets them by the removal strategy;
    a line taking from several lots / bins is split into one line each"""
    out_kinds = ('delivery', 'scrap', 'consumption', 'return_out', 'transfer')
    for ln in list(doc.lines.select_related('product')):
        if not (doc.kind in out_kinds or (doc.kind == 'adjustment' and ln.base_qty < 0)) or ln.serial:
            continue
        tr = tracking_of(ln.product)
        in_bins = StockMove.objects.filter(product=ln.product, warehouse=doc.warehouse, bin__isnull=False).exists()
        if (tr != 'lot' or ln.lot) and (ln.bin_id or not in_bins):
            continue
        qty = abs(Decimal(ln.base_qty))
        parts = pick_stock(ln.product, doc.warehouse, qty, lot=ln.lot, on=doc.date, bin_id=ln.bin_id)
        if not parts or sum((p[2] for p in parts), Z) < qty:
            continue   # not enough – the stock check says so
        sign = -1 if doc.kind == 'adjustment' else 1
        for i, (lot, bin_id, q) in enumerate(parts):
            if i == 0:
                ln.lot, ln.bin_id, ln.base_qty = lot or ln.lot, ln.bin_id or bin_id, q * sign
                ln.qty = rq(q * sign / ln.factor)
                ln.save(update_fields=['lot', 'bin', 'qty', 'base_qty'])
            else:
                StockDocLine.objects.create(doc=doc, product=ln.product, uom=ln.uom, factor=ln.factor, qty=rq(q * sign / ln.factor), base_qty=q * sign,
                                            lot=lot or ln.lot, bin_id=ln.bin_id if ln.bin_id and not bin_id else bin_id,
                                            to_bin=ln.to_bin, expiry=ln.expiry, note=(ln.note or '')[:200] + ' (split by the removal strategy)')


@transaction.atomic
def post(doc, user=None):
    st = settings()
    if doc.status == 'draft':
        _explode_kits(doc)
        _packing_lines(doc)
        for ln in doc.lines.select_related('product', 'product__uom', 'uom'):
            line_base(ln, st=st)
        _allocate_out(doc)
    lines = validate(doc, st)
    role = doc.reason.account_role if doc.reason_id else None
    moves, cost_lines, both_out = [], [], []
    for ln in lines:
        if doc.kind not in ('count', 'dropship'):
            _bins(doc, ln)
        p, wh = ln.product, doc.warehouse
        k = doc.kind
        if k in ('receipt', 'opening'):
            moves.append(_move(doc, ln, k, wh, ln.base_qty, st, in_cost=ln.base_cost, bin_=ln.bin))
        elif k == 'return_in':
            src = _source_out(doc, ln)
            moves.append(_move(doc, ln, 'return_in', wh, ln.base_qty, st, in_cost=ln.base_cost, source=src, bin_=ln.bin))
        elif k in ('delivery', 'scrap', 'consumption'):
            moves += _takeover(doc, ln, wh, Decimal(ln.base_qty), st)
            moves.append(_move(doc, ln, k, wh, -ln.base_qty, st, bin_=ln.bin, offset=role if k != 'delivery' and role else None))
        elif k == 'return_out':
            src = _source_in(doc, ln)
            moves.append(_move(doc, ln, 'return_out', wh, -ln.base_qty, st, source=src, bin_=ln.bin))
        elif k == 'adjustment':
            if ln.adj_type in COST_ONLY:
                cost_lines.append(ln)
                continue
            mv = _move(doc, ln, 'adjustment', wh, ln.base_qty, st, in_cost=ln.base_cost if ln.base_qty > 0 else None, bin_=ln.bin, offset=role)
            moves.append(mv)
            if ln.adj_type == 'both_out':
                both_out.append((ln, mv))
        elif k == 'count':
            have = on_hand(p, wh, on=doc.date, lot=ln.lot or None, serial=ln.serial or None, bin_=ln.bin if ln.bin_id else None)
            ln.expected_qty = rq(have / ln.factor)
            diff = Decimal(ln.base_counted) - have
            ln.base_qty, ln.qty = diff, rq(diff / ln.factor)
            ln.save(update_fields=['expected_qty', 'qty', 'base_qty'])
            if diff:
                moves.append(_move(doc, ln, 'count', wh, diff, st, in_cost=ln.base_cost if diff > 0 else None, bin_=ln.bin))
        elif k == 'dropship':   # v1.75.0 the supplier delivers straight to the customer: in and out at once, at the supplier's cost
            dsin = _move(doc, ln, 'dropship_in', wh, ln.base_qty, st, in_cost=ln.base_cost)
            moves += [dsin, _move(doc, ln, 'dropship_out', wh, -ln.base_qty, st, source=dsin)]
        elif k == 'transfer':
            out = _move(doc, ln, 'transfer_out', wh, -ln.base_qty, st, bin_=ln.bin)
            to = transit_warehouse(wh) if doc.two_step else doc.to_warehouse
            moves += [out, _move(doc, ln, 'transfer_in', to, ln.base_qty, st, source=out, bin_=ln.to_bin)]
    doc.status, doc.posted_by, doc.posted_at = 'posted', user if (user and getattr(user, 'is_authenticated', False)) else None, timezone.now()
    if doc.kind == 'transfer' and doc.two_step:
        doc.transit_status = 'transit'
    doc.save(update_fields=['status', 'posted_by', 'posted_at', 'transit_status'])
    recost_keys({m.costing_key for m in moves}, st)
    _lot_records(doc, moves)
    if doc.kind == 'return_in':
        _rma(doc, user)
    keys = set()
    for ln in cost_lines:   # cost increase / decrease: the value changes, the quantity does not
        amt = q2(Decimal(ln.cost_amount)) * (1 if ln.adj_type == 'cost_in' else -1)
        keys |= _cost_entry(doc, ln.product, doc.warehouse, amt, st, lot=ln.lot, serial=ln.serial, role=role,
                            note=f'{dict(ADJ_TYPES)[ln.adj_type]}{" – " + ln.note if ln.note else ""}')
    for ln, mv in both_out:   # both decrease: the quantity leaves and the stock value goes down by the amount given
        mv.refresh_from_db()
        extra = q2(-Decimal(ln.cost_amount) - Decimal(mv.value))
        if extra:
            keys |= _cost_entry(doc, ln.product, doc.warehouse, extra, st, role=role, note='Both decrease – the cost amount given')
    if keys:
        recost_keys(keys, st)
    _doc_totals(doc)
    gl_after_post(st, doc=doc)
    _sync_products({m.product_id for m in moves} | {ln.product_id for ln in cost_lines})
    return doc


def adjust_preview(doc, st=None):
    """before posting an adjustment: per line the quantity and the unit cost before → after (at the document date, per costing key)"""
    st = st or settings()
    tot = _key_totals(doc.date)
    run_ = {}
    rows = []
    for ln in doc.lines.select_related('product', 'product__uom', 'uom').order_by('id'):
        line_base(ln, save=False, st=st)
        key = costing_key(ln.product_id, doc.warehouse, st)
        q, v = run_.get(key) or (tot[key]['qty'] if key in tot else Z, tot[key]['value'] if key in tot else Z)
        unit = (v / q) if q else Decimal(ln.product.cost_price or 0)
        t = ln.adj_type or ('qty_out' if (ln.base_qty or 0) < 0 else 'qty_in')
        dq, dv = Z, Z
        if t in COST_ONLY:
            dv = Decimal(ln.cost_amount or 0) * (1 if t == 'cost_in' else -1)
        elif t == 'both_out':
            dq, dv = Decimal(ln.base_qty), -Decimal(ln.cost_amount or 0)
        elif Decimal(ln.base_qty or 0) > 0:
            dq = Decimal(ln.base_qty)
            dv = dq * (Decimal(ln.base_cost) if ln.base_cost is not None else unit)
        else:
            dq = Decimal(ln.base_qty or 0)
            dv = dq * unit
        q2_, v2_ = q + dq, v + dv
        run_[key] = (q2_, v2_)
        rows.append({'item': str(ln.product), 'type': dict(ADJ_TYPES).get(t, t), 'qty_before': float(q), 'qty_after': float(q2_),
                     'unit_before': float(q6(unit)), 'unit_after': float(q6(v2_ / q2_)) if q2_ else 0, 'value_before': float(q2(v)), 'value_after': float(q2(v2_)),
                     'change': float(q2(dv))})
    return rows


COST_ONLY = ('cost_in', 'cost_out')


def _cost_entry(doc, product, wh, amount, st, lot='', serial='', role=None, note=''):
    """a cost-only amount on the stock held (no quantity): to the receipts of the lot / serial given, else spread over the stock held
    (average / standard: the item's value). Dr Inventory / Cr the reason's account (or Inventory revaluation); a decrease the other way."""
    key = costing_key(product.pk, wh, st)
    off_role = role or 'revaluation'
    inv, off = account('inventory', product, wh, st), account(off_role, product, wh, st)
    held = CostLayer.objects.filter(costing_key=key, qty_left__gt=0)
    if serial:
        held = held.filter(serial=serial)
    elif lot:
        held = held.filter(lot=lot)
    if (lot or serial) and not held.exists():
        raise InvError(f'No stock of {product} {"serial " + serial if serial else "lot " + lot} in {wh.code} to change the cost of.')
    if not (lot or serial) or method_of(product, st) in ('average', 'standard'):
        held = CostLayer.objects.none()
    if on_hand(product, None if st.costing_level != 'warehouse' else wh) <= 0:
        raise InvError(f'{product} has no stock in hand – a cost change needs stock to change.')
    parts = _split(amount, {L.move_id: Decimal(L.qty_left) for L in held}) if held.exists() else {None: q2(amount)}
    for mid, a in parts.items():
        if not a:
            continue
        StockValue.objects.create(move_id=mid, doc=doc, product=product, warehouse=wh, costing_key=key, valuation_date=doc.date, seq=next_seq(),
                                  kind='revaluation', amount=a, inventory_account=inv, offset_role=off_role, offset_account=off,
                                  gl='none' if st.accounting_mode == 'periodic' else 'pending', note=note[:255])
    return {key}


def _source_out(doc, ln):
    """a customer return takes the cost of the delivery it returns (the original issue cost)"""
    if not doc.return_of_id:
        return None
    return StockMove.objects.filter(doc_id=doc.return_of_id, product=ln.product, qty__lt=0).order_by('seq').first()


def _source_in(doc, ln):
    """a return to the supplier takes the stock of the receipt it returns first"""
    if not doc.return_of_id:
        return None
    return StockMove.objects.filter(doc_id=doc.return_of_id, product=ln.product, qty__gt=0).order_by('seq').first()


CONSIGN_KINDS = ('consign_in', 'consign_out')   # the take-over of consignment stock is not the document's own movement


def _doc_totals(doc):
    agg = StockMove.objects.filter(doc=doc).aggregate(q=Sum('qty'), v=Sum('value'))
    cost = {}
    if doc.kind == 'adjustment':   # cost-only lines (no move): their cost amounts
        for ln in doc.lines.all():
            if ln.adj_type in COST_ONLY:
                cost[ln.pk] = Decimal(ln.cost_amount or 0) * (1 if ln.adj_type == 'cost_in' else -1)
            elif ln.adj_type == 'both_out':
                cost[ln.pk] = -Decimal(ln.cost_amount or 0)
    for ln in doc.lines.all():
        v = cost[ln.pk] if ln.pk in cost else (StockMove.objects.filter(line=ln, warehouse=doc.warehouse).exclude(kind__in=CONSIGN_KINDS).aggregate(v=Sum('value'))['v'] or 0)
        StockDocLine.objects.filter(pk=ln.pk).update(value=abs(q2(v)))
    tv = abs(q2(sum((m.value for m in StockMove.objects.filter(doc=doc, warehouse=doc.warehouse).exclude(kind__in=CONSIGN_KINDS)), Z) +
                Decimal(StockValue.objects.filter(doc=doc, kind='revaluation').aggregate(s=Sum('amount'))['s'] or 0)))
    StockDoc.objects.filter(pk=doc.pk).update(total_qty=abs(Decimal(agg['q'] or 0)) if doc.kind != 'transfer' else
                                              sum((ln.base_qty for ln in doc.lines.all()), Z), total_value=tv)


@transaction.atomic
def reverse(doc, user=None, on=None):
    """a posted document is never changed: the reversal posts the opposite movements (dated today or the given date)"""
    st = settings()
    if doc.status != 'posted':
        raise InvError('Only a posted document can be reversed.')
    on = on or today()
    check_open(on, st)
    rev = StockDoc.objects.create(branch_id=doc.branch_id, number=f'{doc.number}-R', kind=doc.kind, date=on, document_date=doc.document_date,
                                  warehouse=doc.warehouse, to_warehouse=doc.to_warehouse, partner=doc.partner, reason=doc.reason,
                                  reference=f'Reversal of {doc.number}', status='posted', reversal_of=doc,
                                  created_by=user if (user and getattr(user, 'is_authenticated', False)) else None, posted_at=timezone.now())
    olds = list(StockMove.objects.filter(doc=doc).order_by('seq'))
    for m in olds:
        if m.qty > 0 and on_hand(m.product, m.warehouse, lot=m.lot or None, serial=m.serial or None) < m.qty and neg_policy(m.product, m.warehouse, st) == 'block':
            raise InvError(f'{m.product} was already used – {on_hand(m.product, m.warehouse).normalize():f} left in {m.warehouse.code}; the receipt cannot be reversed.')
    keys = set()
    for m in olds:
        # an inflow is taken back from its own layer; an outflow comes back at the cost it left with
        nm = StockMove.objects.create(doc=rev, line=m.line, date=on, seq=next_seq(), kind=m.kind, product=m.product, warehouse=m.warehouse,
                                      branch_id=m.branch_id, bin=m.bin, lot=m.lot, serial=m.serial, expiry=m.expiry, qty=-m.qty,
                                      costing_key=m.costing_key, in_cost=None, source_move=m, offset_role=m.offset_role)
        keys.add(nm.costing_key)
    extra_p = set()
    for v in StockValue.objects.filter(doc=doc, kind='revaluation'):   # cost adjustments are taken back too
        StockValue.objects.create(move_id=v.move_id, doc=rev, product=v.product, warehouse=v.warehouse, costing_key=v.costing_key, valuation_date=on,
                                  seq=next_seq(), kind='revaluation', amount=-Decimal(v.amount), inventory_account=v.inventory_account,
                                  offset_role=v.offset_role, offset_account=v.offset_account, gl='none' if st.accounting_mode == 'periodic' else 'pending',
                                  note=f'Reversal: {v.note}'[:255])
        keys.add(v.costing_key)
        extra_p.add(v.product_id)
    doc.status = 'reversed'
    doc.save(update_fields=['status'])
    recost_keys(keys, st)
    _doc_totals(rev)
    gl_after_post(st, doc=rev)
    _sync_products({m.product_id for m in olds} | extra_p)
    return rev


# ------------------------------------------------------------------ costing: replay per costing key
class _Layer:
    __slots__ = ('db', 'move_id', 'date', 'seq', 'lot', 'serial', 'qty_in', 'left', 'unit')

    def __init__(self, db, move_id, date, seq, lot, serial, qty_in, unit):
        self.db, self.move_id, self.date, self.seq, self.lot, self.serial = db, move_id, date, seq, lot, serial
        self.qty_in, self.left, self.unit = qty_in, qty_in, unit


def recost_keys(keys, st=None, max_rounds=6):
    """replay every key; a transfer-in / return whose source changed is in another key – those keys run again"""
    st = st or settings()
    todo, rounds, every = set(keys), 0, set()
    while todo and rounds < max_rounds:
        rounds += 1
        changed = set()
        for k in sorted(todo):
            changed |= _recost(k, st)
        every |= changed
        todo = set()
        if changed:
            todo = set(StockMove.objects.filter(source_move_id__in=changed).values_list('costing_key', flat=True))
    for d in StockDoc.objects.filter(pk__in=set(StockMove.objects.filter(pk__in=every, doc__isnull=False).values_list('doc_id', flat=True)), status__in=('posted', 'reversed')):
        _doc_totals(d)
    return rounds


def _booked(move_ids):
    out = defaultdict(Decimal)
    for r in StockValue.objects.filter(move_id__in=move_ids, in_stock=True, kind__in=('direct', 'adjustment')).values('move_id').annotate(s=Sum('amount')):
        out[r['move_id']] = Decimal(r['s'] or 0)
    return out


def _recost(key, st):
    """→ the ids of the moves whose value changed"""
    if key.startswith('own'):   # v1.75.0 supplier consignment stock: counted, not valued
        return set()
    moves = list(StockMove.objects.filter(costing_key=key).select_related('product', 'warehouse', 'source_move').order_by('date', 'seq', 'id'))
    if not moves:
        return set()
    product = moves[0].product
    meth = method_of(product, st)
    events = [(m.date, m.seq, 'm', m) for m in moves]
    for v in StockValue.objects.filter(costing_key=key, kind__in=('landed', 'revaluation'), in_stock=True).order_by('valuation_date', 'seq'):
        events.append((v.valuation_date, v.seq, 'v', v))
    events.sort(key=lambda e: (e[0], e[1]))
    layers, by_move = [], {}
    neg_pending = []   # issues below zero waiting for a receipt: (move, qty short, unit used)
    neg_fixed = set()
    avg_q, avg_v = Z, Z
    should, apps_rows, last_unit = {}, [], None
    src_cost = {}   # moves of other keys: their booked value
    for e in events:
        if e[2] == 'v':
            v = e[3]
            amt = Decimal(v.amount)
            if v.move_id in by_move and by_move[v.move_id].left > 0:
                L = by_move[v.move_id]
                L.unit = L.unit + amt / L.left
            elif layers and meth in ('fifo', 'lifo', 'lot', 'serial'):
                open_ = [L for L in layers if L.left > 0] or layers[-1:]
                tot = sum((L.left for L in open_), Z) or Decimal(1)
                for L in open_:
                    L.unit = L.unit + amt / tot if L.left > 0 else L.unit
            avg_v += amt
            continue
        m = e[3]
        q = Decimal(m.qty)
        if q > 0:   # inflow
            if m.source_move_id:
                s = m.source_move
                sv = should.get(s.pk)
                if sv is None:
                    sv = Decimal(s.value)
                unit = abs(sv) / abs(Decimal(s.qty)) if s.qty else Z
            elif meth == 'standard':
                unit = std_cost(product, m.date)
            elif m.in_cost is not None:
                unit = Decimal(m.in_cost)
            else:   # an adjustment / count in without a cost: the current cost
                unit = (avg_v / avg_q) if avg_q > 0 else (last_unit if last_unit is not None else Decimal(product.cost_price or 0))
            L = _Layer(None, m.pk, m.date, m.seq, m.lot, m.serial, q, unit)
            layers.append(L)
            by_move[m.pk] = L
            should[m.pk] = q2(q * unit)
            avg_q += q
            avg_v += q * unit
            last_unit = unit
            if neg_pending and not m.source_move_id and meth != 'standard' and m.kind != 'dropship_in':
                # v1.75.0 negative stock: issues made below zero (costed at the last cost) take this receipt's cost now – the cost
                # adjustment books the difference on the issue (negative stock corrections report)
                left_in = q
                while neg_pending and left_in > 0:
                    om, short, uu = neg_pending[0]
                    if meth in ('lot', 'serial') and (om.lot != m.lot or om.serial != m.serial):
                        break
                    sq = min(short, left_in)
                    delta = sq * (unit - uu)
                    should[om.pk] = q2(should[om.pk] - delta)
                    neg_fixed.add(om.pk)
                    if meth == 'average':
                        avg_v -= delta
                    else:
                        L.left -= sq
                        apps_rows.append((om, L, sq, sq * unit))
                    left_in -= sq
                    if short - sq > 0:
                        neg_pending[0] = (om, short - sq, uu)
                    else:
                        neg_pending.pop(0)
        else:   # outflow
            need = -q
            cost = Z
            from_source = bool(m.source_move_id and m.source_move_id in by_move and m.kind in ('return_out', 'receipt', 'opening', 'return_in', 'adjustment', 'count', 'transfer_in', 'dropship_out'))
            if from_source:
                cands = [by_move[m.source_move_id]] + [L for L in layers if L.move_id != m.source_move_id]
            elif meth == 'lot':
                cands = [L for L in layers if L.lot == m.lot]
            elif meth == 'serial':
                cands = [L for L in layers if L.serial == m.serial]
            else:
                cands = list(layers)
            if meth == 'lifo' and not from_source:
                cands = list(reversed(cands))
            taken = Z
            for L in cands:
                if need <= 0:
                    break
                if L.left <= 0:
                    continue
                take = min(L.left, need)
                L.left -= take
                need -= take
                taken += take
                c = take * L.unit
                cost += c
                apps_rows.append((m, L, take, c))
            if from_source and meth in ('average', 'standard'):
                # a return to the supplier / a reversed receipt leaves at the cost it came in with (the applied entry)
                src = by_move[m.source_move_id]
                cost = -q * (Decimal(should.get(m.source_move_id, Z)) / src.qty_in if src.qty_in else src.unit)
            elif meth == 'average':
                unit = (avg_v / avg_q) if avg_q > 0 else (last_unit if last_unit is not None else Decimal(product.cost_price or 0))
                cost = -q * unit
                short = min(-q, -(avg_q + q)) if avg_q + q < 0 else Z
                if short > 0 and not from_source:
                    neg_pending.append((m, short, unit))
            elif meth == 'standard':
                cost = -q * std_cost(product, m.date)
            elif need > 0:   # negative stock: the rest at the last cost (corrected when the next receipt comes)
                uu = last_unit if last_unit is not None else Decimal(product.cost_price or 0)
                cost += need * uu
                if not from_source:
                    neg_pending.append((m, need, uu))
            should[m.pk] = -q2(cost)
            avg_q += q
            avg_v -= cost
            if avg_q <= 0:
                avg_v = avg_v if avg_q < 0 else Z
    if meth == 'standard':
        _ppv(moves, product, st)
    # what was booked against what it should be
    booked = _booked([m.pk for m in moves])
    changed = set()
    open_day = first_open_day(st)
    for m in moves:
        target = should.get(m.pk, Z)
        diff = q2(target - booked.get(m.pk, Z))
        if diff:
            first = not StockValue.objects.filter(move=m, kind='direct').exists() and m.pk not in neg_fixed
            vd = m.date if (open_day is None or m.date >= open_day) else open_day
            _value(m, 'direct' if first else 'adjustment', diff, vd, st)
            changed.add(m.pk)
        if q2(Decimal(m.value)) != q2(target):
            StockMove.objects.filter(pk=m.pk).update(value=q2(target))
    # layers and applications as they stand now
    db_layers = {L.move_id: L for L in CostLayer.objects.filter(costing_key=key)}
    keep = set()
    for L in layers:
        x = db_layers.get(L.move_id)
        if x is None:
            x = CostLayer.objects.create(product=product, costing_key=key, move_id=L.move_id, received_date=L.date, lot=L.lot, serial=L.serial,
                                         qty_in=L.qty_in, qty_left=L.left, unit_cost=q6(L.unit))
        elif x.qty_left != L.left or q6(x.unit_cost) != q6(L.unit):
            CostLayer.objects.filter(pk=x.pk).update(qty_left=L.left, unit_cost=q6(L.unit))
        keep.add(L.move_id)
        L.db = x.pk if hasattr(x, 'pk') else x
    CostLayer.objects.filter(costing_key=key).exclude(move_id__in=keep).delete()
    CostApplication.objects.filter(out_move__costing_key=key).delete()
    CostApplication.objects.bulk_create([CostApplication(out_move=m, layer_id=L.db, qty=t, cost=q2(c), date=m.date) for m, L, t, c in apps_rows if L.db])
    return changed


def _ppv(moves, product, st):
    """standard cost: the difference between the price paid and the standard goes to purchase price variance (not to stock)"""
    for m in moves:
        if m.qty <= 0 or m.in_cost is None or m.source_move_id or m.kind not in ('receipt', 'opening'):
            continue
        target = q2(Decimal(m.qty) * (Decimal(m.in_cost) - std_cost(product, m.date)))
        have = q2(StockValue.objects.filter(move=m, kind='variance').aggregate(s=Sum('amount'))['s'] or 0)
        if target != have:
            StockValue.objects.create(move=m, doc_id=m.doc_id, product=product, warehouse=m.warehouse, costing_key=m.costing_key,
                                      valuation_date=open_date(m.date, st), seq=next_seq(), kind='variance', amount=target - have,
                                      inventory_account=account('ppv', product, m.warehouse, st), offset_role=m.offset_role,
                                      offset_account=account(m.offset_role or 'grni', product, m.warehouse, st), in_stock=False,
                                      gl='none' if st.accounting_mode == 'periodic' else 'pending', note='Purchase price variance')


@transaction.atomic
def revalue_standard(product, new_cost, on, user=None, note=''):
    """a new standard cost from a date: the stock held then is revalued (revaluation account)"""
    st = settings()
    check_open(on, st)
    new_cost = Decimal(str(new_cost))
    if new_cost < 0:
        raise InvError('The standard cost cannot be negative.', 'cost')
    old = std_cost(product, on)
    StandardCost.objects.update_or_create(product=product, effective_from=on, defaults={'cost': new_cost, 'note': note[:255]})
    made = []
    keys = set(StockMove.objects.filter(product=product).values_list('costing_key', flat=True).distinct())
    for k in keys:
        q = Decimal(StockMove.objects.filter(costing_key=k, date__lt=on).aggregate(s=Sum('qty'))['s'] or 0)
        if not q:
            continue
        m = StockMove.objects.filter(costing_key=k).select_related('warehouse').order_by('-date', '-seq').first()
        amt = q2(q * (new_cost - old))
        if amt:
            made.append(StockValue.objects.create(product=product, warehouse=m.warehouse, costing_key=k, valuation_date=on, seq=next_seq(), kind='revaluation',
                                                  amount=amt, inventory_account=account('inventory', product, m.warehouse, st), offset_role='revaluation',
                                                  offset_account=account('revaluation', product, m.warehouse, st),
                                                  gl='none' if st.accounting_mode == 'periodic' else 'pending', note=f'Standard cost {old} → {new_cost}'))
    recost_keys(keys, st)
    gl_after_post(st)
    _sync_products({product.pk})
    return made


def _value(m, kind, amount, vd, st, note=''):
    inv = account('inventory', m.product, m.warehouse, st)
    off_role = m.offset_role or OFFSET.get(m.kind, 'adjustment')
    return StockValue.objects.create(move=m, doc_id=m.doc_id, product=m.product, warehouse=m.warehouse, costing_key=m.costing_key, valuation_date=vd,
                                     seq=next_seq(), kind=kind, amount=amount, inventory_account=inv, offset_role=off_role,
                                     offset_account=account(off_role, m.product, m.warehouse, st),
                                     gl='none' if st.accounting_mode == 'periodic' else 'pending', note=note[:255])


# ------------------------------------------------------------------ landed cost
def landed_lines(lc):
    rec = lc.receipts or (lc.estimate.receipts if lc.estimate_id else [])
    ids = [int(x) for x in (rec or []) if str(x).isdigit()]
    return list(StockMove.objects.filter(doc_id__in=ids, qty__gt=0, kind__in=('receipt', 'opening')).select_related('product', 'warehouse', 'line').order_by('seq'))


def charge_amounts(lc, moves=None):
    """a charge given as a % of the goods value gets its amount from the receipts (customs duty 5 %, insurance 0.5 %)"""
    moves = landed_lines(lc) if moves is None else moves
    goods = sum((abs(Decimal(m.value)) for m in moves), Z)
    for ch in lc.charges.all():
        if ch.percent is not None:
            amt = q2(goods * Decimal(ch.percent) / 100)
            if amt != q2(ch.amount):
                type(ch).objects.filter(pk=ch.pk).update(amount=amt)
    return goods


def allocate(lc):
    """→ [{move, line, item, qty, amount, to_stock, to_cogs …}] – the split of every charge over the receipt lines, then of each line into
    the part still in stock (raises its cost) and the part already used (COGS) at the landed cost's date. An actual that settles an
    estimate splits only the difference (actual − estimate) of each line."""
    moves = landed_lines(lc)
    if not moves:
        raise InvError('Choose the posted goods receipts the charges belong to.', 'receipts')
    charge_amounts(lc, moves)
    charges = list(lc.charges.all())
    if not charges:
        raise InvError('Add the charges (freight, duty …).')
    alloc = {m.pk: Z for m in moves}
    for ch in charges:
        amt = Decimal(ch.amount)
        if amt <= 0:
            raise InvError(f'{ch.get_charge_display()}: enter the amount or the %.')
        if ch.basis == 'manual':
            man = {int(k): Decimal(str(v)) for k, v in (ch.manual or {}).items()}
            if q2(sum(man.values(), Z)) != q2(amt):
                raise InvError(f'The manual split of {ch.get_charge_display()} must add up to {amt}.')
            for m in moves:
                alloc[m.pk] += man.get(m.line_id, Z)
            continue
        base = {}
        for m in moves:
            if ch.basis == 'quantity':
                base[m.pk] = Decimal(m.qty)
            elif ch.basis == 'weight':
                base[m.pk] = Decimal(m.line.weight) if m.line and m.line.weight is not None else Decimal(m.product.weight_kg or 0) * Decimal(m.qty)
            elif ch.basis == 'volume':
                base[m.pk] = Decimal(m.line.volume or 0) if m.line else Z
            else:
                base[m.pk] = abs(Decimal(m.value))
        tot = sum(base.values(), Z)
        if tot <= 0:
            raise InvError(f'{ch.get_charge_display()}: the receipts have no {ch.get_basis_display().lower().replace("by ", "")} to split by.')
        rest = amt
        for i, m in enumerate(moves):
            part = q2(amt * base[m.pk] / tot) if i < len(moves) - 1 else rest   # the rounding cent on the last line
            alloc[m.pk] += part
            rest -= part
    est = {}
    if lc.estimate_id:
        est = {int(r['move']): Decimal(str(r['amount'])) for r in (lc.estimate.allocation or [])}
    out = []
    for m in moves:
        actual = q2(alloc[m.pk])
        a = q2(actual - est.get(m.pk, Z))
        layer = CostLayer.objects.filter(move=m).first()
        used = Z
        if layer is not None:
            used = Decimal(CostApplication.objects.filter(layer=layer, date__lte=lc.date).aggregate(s=Sum('qty'))['s'] or 0)
        qty = Decimal(m.qty)
        in_stock = max(qty - used, Z)
        to_stock = q2(a * in_stock / qty) if qty else a
        out.append({'move': m.pk, 'line': m.line_id, 'item': str(m.product), 'warehouse': m.warehouse.code, 'qty': float(qty), 'in_stock': float(in_stock),
                    'actual': float(actual), 'estimate': float(est.get(m.pk, Z)), 'amount': float(a), 'to_stock': float(to_stock), 'to_cogs': float(a - to_stock),
                    'uplift': float(q6(actual / qty)) if qty else 0, 'unit_before': float(q6(abs(Decimal(m.value)) / qty)) if qty else 0})
    return out


def _lc_value(lc, m, amount, held, offset_role, st, note):
    return StockValue.objects.create(
        move=m, landed=lc, product=m.product, warehouse=m.warehouse, costing_key=m.costing_key, valuation_date=lc.date, seq=next_seq(), kind='landed',
        amount=amount, inventory_account=account('inventory' if held else 'cogs', m.product, m.warehouse, st), offset_role=offset_role,
        offset_account=account(offset_role, m.product, m.warehouse, st), in_stock=held, gl='none' if st.accounting_mode == 'periodic' else 'pending',
        note=note[:255])


@transaction.atomic
def post_landed(lc, user=None):
    """estimate: Dr Inventory (held) / COGS (used) – Cr Landed cost accrual. Actual: Cr Landed cost clearing (the bills); when it settles an
    estimate only the difference per line is posted, the estimate's accrual moves to clearing and the estimate is settled."""
    st = settings()
    if lc.status != 'draft':
        raise InvError('Only a draft can be posted.')
    check_open(lc.date, st)
    e = lc.estimate if lc.estimate_id else None
    if lc.kind == 'estimate' and e is not None:
        raise InvError('An estimate cannot settle another estimate.', 'estimate')
    if e is not None and (e.kind != 'estimate' or e.status != 'posted'):
        raise InvError('Choose a posted estimate that is not settled yet.', 'estimate')
    if e is not None and not lc.receipts:
        lc.receipts = list(e.receipts or [])
    rows = allocate(lc)
    role = 'landed_accrual' if lc.kind == 'estimate' else 'landed_clearing'
    tag = 'estimate' if lc.kind == 'estimate' else ('difference to the estimate' if e is not None else 'actual')
    keys = set()
    for r in rows:
        m = StockMove.objects.select_related('product', 'warehouse').get(pk=r['move'])
        if r['to_stock']:
            _lc_value(lc, m, Decimal(str(r['to_stock'])), True, role, st, f'{lc.number} {tag} on stock held')
            keys.add(m.costing_key)
        if r['to_cogs']:
            _lc_value(lc, m, Decimal(str(r['to_cogs'])), False, role, st, f'{lc.number} {tag} on stock already used')
    if e is not None and q2(e.total):
        # the estimate's accrual is now a bill: Dr Landed cost accrual / Cr Landed cost clearing
        m = StockMove.objects.select_related('product', 'warehouse').get(pk=rows[0]['move'])
        StockValue.objects.create(move=m, landed=lc, product=m.product, warehouse=m.warehouse, costing_key=m.costing_key, valuation_date=lc.date,
                                  seq=next_seq(), kind='landed', amount=q2(e.total), inventory_account=account('landed_accrual', m.product, m.warehouse, st),
                                  offset_role='landed_clearing', offset_account=account('landed_clearing', m.product, m.warehouse, st), in_stock=False,
                                  gl='none' if st.accounting_mode == 'periodic' else 'pending', note=f'{lc.number} settles {e.number}')
        e.status = 'settled'
        e.save(update_fields=['status'])
    lc.status, lc.posted_at, lc.allocation = 'posted', timezone.now(), rows
    lc.total = q2(sum((Decimal(str(r['actual'])) for r in rows), Z))
    lc.to_stock = q2(sum((Decimal(str(r['to_stock'])) for r in rows), Z))
    lc.to_cogs = q2(sum((Decimal(str(r['to_cogs'])) for r in rows), Z))
    lc.save()
    recost_keys(keys, st)
    gl_after_post(st, landed=lc)
    return lc


# ------------------------------------------------------------------ journals
def gl_after_post(st, doc=None, landed=None):
    if st.accounting_mode == 'periodic' or st.posting_timing != 'immediate':
        return []
    return post_to_gl(st)


def _jb():
    return apps.get_model('ZeoAccounting', 'JournalBatch'), apps.get_model('ZeoAccounting', 'JournalLine')


JOURNAL_PLACES = 2   # the Accounting outbox keeps 2 decimals (ZeoAccounting.JournalLine)


def jlines(acc):
    """{account: signed amount} → balanced [(account, amount)] at the outbox's precision; a rounding difference (3-decimal
    currencies) goes to the largest line so the journal still balances"""
    rows = [(a, rnd(x, JOURNAL_PLACES)) for a, x in acc.items() if rnd(x, JOURNAL_PLACES) != 0]
    diff = sum((x for _, x in rows), Z)
    if diff and rows:
        i = max(range(len(rows)), key=lambda k: abs(rows[k][1]))
        rows[i] = (rows[i][0], rows[i][1] - diff)
        rows = [r for r in rows if r[1] != 0]
    return rows


@transaction.atomic
def post_to_gl(st=None, up_to=None, user=None):
    """every cost amount not yet in the GL → one balanced journal per document / landed cost / adjustment day"""
    st = st or settings()
    if st.accounting_mode == 'periodic':
        return []
    JB, JL = _jb()
    qs = StockValue.objects.filter(gl='pending').select_related('doc', 'landed', 'move')
    if up_to:
        qs = qs.filter(valuation_date__lte=up_to)
    groups = defaultdict(list)
    for v in qs:
        if v.landed_id:
            g = ('L', v.landed_id, v.valuation_date)
        elif v.kind == 'adjustment':
            g = ('A', 0, v.valuation_date)
        else:
            g = ('D', v.doc_id or 0, v.valuation_date)
        groups[g].append(v)
    made = []
    for g, rows in sorted(groups.items(), key=lambda x: (x[0][2], x[0][0], x[0][1])):
        acc = defaultdict(Decimal)
        for v in rows:
            acc[v.inventory_account] += Decimal(v.amount)
            acc[v.offset_account] -= Decimal(v.amount)
        lines = jlines(acc)
        jid = None
        if lines:
            if g[0] == 'L':
                lc = rows[0].landed
                label, src_id = f'Landed cost {lc.number} {lc.shipment}'.strip(), lc.number
            elif g[0] == 'A':
                label, src_id = f'Inventory cost adjustment {g[2]:%d/%m/%Y}', f'ADJ-{g[2]:%Y%m%d}'
            else:
                dc = rows[0].doc
                label, src_id = (f'{dc.get_kind_display()} {dc.number} {dc.reference}'.strip() if dc else 'Inventory'), (dc.number if dc else '')
            dr = sum((x for _, x in lines if x > 0), Z)
            b = JB.objects.create(number='', source='inventory', source_id=str(src_id)[:40], source_label=label[:200], date=g[2], document_date=g[2],
                                  reference=label[:120], status='ready', total_debit=q2(dr), total_credit=q2(dr), lines_count=len(lines),
                                  idempotency_key=f'inv:{g[0]}:{g[1]}:{g[2]:%Y%m%d}:{rows[0].pk}:{rows[-1].pk}',
                                  branch_id=rows[0].move.branch_id if rows[0].move_id else None, created_by=user)
            b.number = f'INV-JE-{b.pk:05d}'
            b.save(update_fields=['number'])
            for i, (a, x) in enumerate(sorted(lines, key=lambda t: -t[1])):
                JL.objects.create(batch=b, seq=i + 1, account=a, base_account=a, label=label[:255], debit=x if x > 0 else Z, credit=-x if x < 0 else Z,
                                  branch_id=b.branch_id)
            jid = b.pk
            made.append(b.pk)
        StockValue.objects.filter(pk__in=[v.pk for v in rows]).update(gl='posted', journal_id=jid)
    return made


def journals_of(doc=None, landed=None):
    qs = StockValue.objects.filter(journal_id__isnull=False)
    qs = qs.filter(doc=doc) if doc is not None else qs.filter(landed=landed)
    ids = sorted(set(qs.values_list('journal_id', flat=True)))
    JB, JL = _jb()
    return [{'id': b.pk, 'number': b.number, 'date': b.date.isoformat(), 'status': b.status,
             'lines': [{'account': x.account, 'debit': float(x.debit), 'credit': float(x.credit)} for x in b.lines.all()]}
            for b in JB.objects.filter(pk__in=ids).prefetch_related('lines')]


# ------------------------------------------------------------------ valuation, ageing, reconciliation
def _key_totals(on, product=None):
    """per costing key at the cut-off: quantity (own stock), value (in-stock cost amounts dated up to it), per-warehouse quantity"""
    mq = StockMove.objects.filter(date__lte=on, owner__isnull=True)
    vq = StockValue.objects.filter(valuation_date__lte=on, in_stock=True)
    if product is not None:
        mq, vq = mq.filter(product=product), vq.filter(product=product)
    keys = defaultdict(lambda: {'qty': Z, 'value': Z, 'wh': defaultdict(lambda: Z), 'whv': defaultdict(lambda: Z), 'product_id': None})
    for r in mq.values('costing_key', 'product_id', 'warehouse_id').annotate(s=Sum('qty')):
        k = keys[r['costing_key']]
        k['product_id'] = r['product_id']
        k['qty'] += Decimal(r['s'] or 0)
        k['wh'][r['warehouse_id']] += Decimal(r['s'] or 0)
    for r in vq.values('costing_key', 'product_id', 'warehouse_id').annotate(s=Sum('amount')):
        k = keys[r['costing_key']]
        k['product_id'] = r['product_id']
        k['value'] += Decimal(r['s'] or 0)
        k['whv'][r['warehouse_id']] += Decimal(r['s'] or 0)
    return keys


def _split(total, weights):
    """split an amount by weights at the money places; the rounding rest goes to the largest part – the parts always add up to the total"""
    total = q2(total)
    ws = {k: Decimal(w) for k, w in weights.items()}
    tw = sum(ws.values(), Z)
    if not ws:
        return {}
    if tw == 0:
        big = next(iter(ws))
        return {k: (total if k == big else Z) for k in ws}
    out = {k: q2(total * w / tw) for k, w in ws.items()}
    rest = total - sum(out.values(), Z)
    if rest:
        big = max(ws, key=lambda k: abs(ws[k]))
        out[big] += rest
    return out


def warehouse_values(on, product=None):
    """{(product_id, warehouse_id): (qty, value)} at the cut-off. Warehouse costing: the warehouse's own cost amounts. Company / branch
    costing: one cost per key, so a warehouse holds its quantity at the key's unit cost (the rounding rest on the largest holding) –
    the warehouses always add up to the item's value."""
    st = settings()
    out = {}
    for key, k in _key_totals(on, product).items():
        whs = set(k['wh']) | set(k['whv'])
        if st.costing_level == 'warehouse' or len(whs) <= 1:
            for w in whs:
                out[(k['product_id'], w)] = (k['wh'].get(w, Z), q2(k['whv'].get(w, Z)) if len(whs) > 1 else q2(k['value']))
            continue
        held = {w: q for w, q in k['wh'].items() if q}
        if not held:   # nothing held: any value left (rounding) stays with the warehouse it was booked on
            held = {w: Z for w in whs}
            parts = _split(k['value'], {w: abs(k['whv'].get(w, Z)) or Decimal(1) for w in whs})
        else:
            parts = _split(k['value'], held)
        for w in whs | set(parts):
            pk = (k['product_id'], w)
            q0, v0 = out.get(pk, (Z, Z))
            out[pk] = (q0 + k['wh'].get(w, Z), v0 + parts.get(w, Z))
    return out


def valuation(on=None, group='item', warehouse=None, product=None):
    """stock and value at the cut-off (valuation date up to it) – the same figure as the stock ledger closing, the ageing total and,
    in perpetual mode, the GL inventory accounts (with what is not yet posted)."""
    on = on or today()
    f = {'item': ('product_id',), 'warehouse': ('warehouse_id',), 'item_warehouse': ('product_id', 'warehouse_id'), 'lot': ('product_id', 'lot'),
         'serial': ('product_id', 'serial')}.get(group, ('product_id',))
    agg = defaultdict(lambda: [Z, Z])
    if 'lot' in f or 'serial' in f:
        x = f[1]
        mq = StockMove.objects.filter(date__lte=on, owner__isnull=True)
        vq = StockValue.objects.filter(valuation_date__lte=on, in_stock=True)
        if warehouse is not None:
            mq, vq = mq.filter(warehouse=warehouse), vq.filter(warehouse=warehouse)
        if product is not None:
            mq, vq = mq.filter(product=product), vq.filter(product=product)
        for r in mq.values('product_id', x).annotate(s=Sum('qty')):
            agg[(r['product_id'], r[x])][0] += Decimal(r['s'] or 0)
        for r in vq.values('product_id', f'move__{x}').annotate(s=Sum('amount')):
            agg[(r['product_id'], r[f'move__{x}'] or '')][1] += Decimal(r['s'] or 0)
    else:
        for (pid, wid), (qv, vv) in warehouse_values(on, product).items():
            if warehouse is not None and wid != getattr(warehouse, 'pk', warehouse):
                continue
            k = tuple({'product_id': pid, 'warehouse_id': wid}[n] for n in f)
            agg[k][0] += qv
            agg[k][1] += vv
    P = apps.get_model('ZeoMasters', 'Product')
    names = {p.pk: str(p) for p in P.objects.filter(pk__in={k[0] for k in agg if f[0] == 'product_id'})}
    whs = {w.pk: str(w) for w in Warehouse.objects.all()}
    rows = []
    for k in sorted(agg, key=lambda x: tuple(str(i) for i in x)):
        qv, vv = agg[k][0], q2(agg[k][1])
        if not qv and not vv:
            continue
        r = {'qty': float(qv), 'value': float(vv), 'unit_cost': float(q6(vv / qv)) if qv else 0}
        for name, x in zip(f, k):
            if name == 'product_id':
                r['item_id'], r['item'] = x, names.get(x, '')
            elif name == 'warehouse_id':
                r['warehouse_id'], r['warehouse'] = x, whs.get(x, '')
            else:
                r[name] = x
        rows.append(r)
    return rows


def valuation_by_document(on, warehouse=None):
    """operations view: what the documents dated (document date, else posting date) up to `on` hold – stock in transit and at customers apart"""
    from django.db.models.functions import Coalesce
    mq = StockMove.objects.filter(owner__isnull=True).annotate(dd=Coalesce('doc__document_date', 'date')).filter(dd__lte=on)
    vq = StockValue.objects.filter(in_stock=True).annotate(dd=Coalesce('doc__document_date', 'move__doc__document_date', 'valuation_date')).filter(dd__lte=on)
    if warehouse is not None:
        mq, vq = mq.filter(warehouse=warehouse), vq.filter(warehouse=warehouse)
    agg = defaultdict(lambda: [Z, Z])
    for r in mq.values('product_id', 'warehouse_id').annotate(s=Sum('qty')):
        agg[(r['product_id'], r['warehouse_id'])][0] += Decimal(r['s'] or 0)
    for r in vq.values('product_id', 'warehouse_id').annotate(s=Sum('amount')):
        agg[(r['product_id'], r['warehouse_id'])][1] += Decimal(r['s'] or 0)
    P = apps.get_model('ZeoMasters', 'Product')
    names = {p.pk: str(p) for p in P.objects.filter(pk__in={k[0] for k in agg})}
    whs = {w.pk: w for w in Warehouse.objects.all()}
    rows = []
    for (pid, wid), (qv, vv) in sorted(agg.items(), key=lambda kv: (names.get(kv[0][0], ''), kv[0][1] or 0)):
        if not qv and not q2(vv):
            continue
        w = whs.get(wid)
        rows.append({'item_id': pid, 'item': names.get(pid, ''), 'warehouse_id': wid, 'warehouse': str(w or ''), 'where': where_of(w), 'qty': float(qv),
                     'value': float(q2(vv)), 'unit_cost': float(q6(vv / qv)) if qv else 0})
    return rows


def where_of(w):
    if w is None:
        return ''
    if w.is_transit:
        return 'In transit'
    if getattr(w, 'kind', '') == 'customer':
        return 'At customers'
    return 'In our warehouses'


def movement_summary(date_from, date_to):
    """per item: opening, in, out, closing quantity and value between two dates – it ties to the stock ledger and the valuation.
    v1.79.3 added up by the database (before: the whole stock ledger was built only to add it up)."""
    from datetime import timedelta
    from django.db.models import Case, DecimalField, Value, When
    before = date_from - timedelta(days=1)
    mq = StockMove.objects.filter(owner__isnull=True)
    vq = StockValue.objects.filter(in_stock=True)
    agg = defaultdict(lambda: {'oq': Z, 'ov': Z, 'iq': Z, 'oq_': Z, 'iv': Z, 'ovv': Z})
    dec = DecimalField(max_digits=24, decimal_places=6)
    for r in mq.filter(date__lte=before).values('product_id').annotate(s=Sum('qty')):
        agg[r['product_id']]['oq'] += Decimal(r['s'] or 0)
    for r in vq.filter(valuation_date__lte=before).values('product_id').annotate(s=Sum('amount')):
        agg[r['product_id']]['ov'] += Decimal(r['s'] or 0)
    for r in mq.filter(date__gte=date_from, date__lte=date_to).values('product_id').annotate(
            i=Sum(Case(When(qty__gt=0, then='qty'), default=Value(0), output_field=dec)), o=Sum(Case(When(qty__lt=0, then='qty'), default=Value(0), output_field=dec))):
        agg[r['product_id']]['iq'] += Decimal(r['i'] or 0)
        agg[r['product_id']]['oq_'] -= Decimal(r['o'] or 0)
    # values as the ledger shows them: a direct cost on its move's day sits on the move (its sign), every other amount a line of its own
    for r in vq.filter(valuation_date__gte=date_from, valuation_date__lte=date_to).values('product_id').annotate(
            i=Sum(Case(When(amount__gt=0, then='amount'), default=Value(0), output_field=dec)),
            o=Sum(Case(When(amount__lt=0, then='amount'), default=Value(0), output_field=dec))):
        agg[r['product_id']]['iv'] += Decimal(r['i'] or 0)
        agg[r['product_id']]['ovv'] -= Decimal(r['o'] or 0)
    P = apps.get_model('ZeoMasters', 'Product')
    names = {p.pk: str(p) for p in P.objects.filter(pk__in=list(agg))}
    rows = []
    for pid in sorted(agg, key=lambda x: names.get(x, '')):
        a = agg[pid]
        if not (a['oq'] or q2(a['ov']) or a['iq'] or a['oq_'] or q2(a['iv']) or q2(a['ovv'])):
            continue
        rows.append({'item_id': pid, 'item': names.get(pid, ''), 'opening_qty': a['oq'], 'opening_value': q2(a['ov']), 'in_qty': a['iq'], 'in_value': q2(a['iv']),
                     'out_qty': a['oq_'], 'out_value': q2(a['ovv']), 'closing_qty': a['oq'] + a['iq'] - a['oq_'],
                     'closing_value': q2(a['ov'] + a['iv'] - a['ovv'])})
    return rows


def value_on(on, inventory_account=None):
    qs = StockValue.objects.filter(valuation_date__lte=on, in_stock=True)
    if inventory_account:
        qs = qs.filter(inventory_account=inventory_account)
    return q2(qs.aggregate(s=Sum('amount'))['s'] or 0)


def _ages(on):
    """per layer: [(receipt date, qty)] it still holds at the cut-off. A transfer in keeps the receipt dates of the layers its
    transfer out took (so moving stock between warehouses does not make it young again).
    v1.79.3 read as values; the applications only of the transfers (a million moves: gigabytes of objects before)."""
    used = {r['layer_id']: Decimal(r['s'] or 0) for r in CostApplication.objects.filter(date__lte=on).values('layer_id').annotate(s=Sum('qty'))}
    layers = {}
    srcs = set()
    for lid, key, rd, qin, unit, kind, src in CostLayer.objects.filter(received_date__lte=on).values_list(
            'id', 'costing_key', 'received_date', 'qty_in', 'unit_cost', 'move__kind', 'move__source_move_id').iterator(chunk_size=20000):
        layers[lid] = (key, rd, Decimal(qin), Decimal(unit), kind, src)
        if kind == 'transfer_in' and src:
            srcs.add(src)
    took = defaultdict(list)   # out move -> [(layer, qty)]
    sl = sorted(srcs)
    for k in range(0, len(sl), 20000):
        for om, lid, q in CostApplication.objects.filter(date__lte=on, out_move_id__in=sl[k:k + 20000]).values_list('out_move_id', 'layer_id', 'qty'):
            took[om].append((lid, Decimal(q)))
    memo = {}

    def origin(lid, depth=0):   # [(date, share of 1)]
        if lid in memo:
            return memo[lid]
        key, rd, qin, unit, kind, src = layers[lid]
        res = [(rd, Decimal(1))]
        if kind == 'transfer_in' and src and depth < 20:
            got = took.get(src) or []
            tot = sum((q for _, q in got), Z)
            if tot > 0:
                res = []
                for sid, q in got:
                    if sid not in layers:
                        res.append((rd, q / tot))
                        continue
                    for dt, sh in origin(sid, depth + 1):
                        res.append((dt, sh * q / tot))
        memo[lid] = res
        return res

    out = defaultdict(list)   # costing key -> [(date, qty, layer unit cost)]
    for lid, (key, rd, qin, unit, kind, src) in layers.items():
        left = qin - used.get(lid, Z)
        if left <= 0:
            continue
        for dt, sh in origin(lid):
            out[key].append((dt, left * sh, unit))
    return out


def ageing(on=None, buckets=None, product=None):
    """the stock held at the cut-off by the age of its receipt; per item the total is the valuation at the cut-off (FIFO / lot / serial:
    each layer at its cost, average / standard: at the item's cost at the cut-off)."""
    on = on or today()
    st = settings()
    bks = buckets or [int(x) for x in str(st.ageing_buckets or '30,60,90,180').split(',') if x.strip().isdigit()]
    edges = sorted(bks)
    labels = [f'0–{edges[0]}'] + [f'{edges[i - 1] + 1}–{edges[i]}' for i in range(1, len(edges))] + [f'{edges[-1]}+']
    ages = _ages(on)
    P = apps.get_model('ZeoMasters', 'Product')
    rows = defaultdict(lambda: {'qty': [Z] * len(labels), 'value': [Z] * len(labels)})
    kt = _key_totals(on, product)
    prods = {p.pk: p for p in P.objects.filter(pk__in={k['product_id'] for k in kt.values()})}   # v1.79.3 read once
    for key, k in kt.items():
        pid = k['product_id']
        prod = prods.get(pid)
        meth = method_of(prod, st) if prod is not None else 'average'
        qty_b, w_b = [Z] * len(labels), [Z] * len(labels)
        for dt, q, unit in ages.get(key, []):
            i = next((j for j, e in enumerate(edges) if (on - dt).days <= e), len(edges))
            qty_b[i] += q
            w_b[i] += q * unit if meth in ('fifo', 'lifo', 'lot', 'serial') else q
        rest = k['qty'] - sum(qty_b, Z)   # negative stock / stock without a layer: the newest bucket
        if rest:
            qty_b[0] += rest
            w_b[0] += rest * ((sum(w_b, Z) / sum(qty_b, Z)) if meth in ('fifo', 'lifo', 'lot', 'serial') and sum(qty_b, Z) else Decimal(1))
        if not any(qty_b) and not q2(k['value']):
            continue
        parts = _split(k['value'], {i: w for i, w in enumerate(w_b)})
        r = rows[pid]
        r['item'] = str(prod) if prod is not None else ''
        for i in range(len(labels)):
            r['qty'][i] += qty_b[i]
            r['value'][i] += parts.get(i, Z)
    out = []
    for pid, r in sorted(rows.items(), key=lambda x: x[1]['item']):
        out.append({'item_id': pid, 'item': r['item'], 'buckets': [{'label': lb, 'qty': float(q), 'value': float(q2(v))} for lb, q, v in zip(labels, r['qty'], r['value'])],
                    'qty': float(sum(r['qty'], Z)), 'value': float(q2(sum(r['value'], Z)))})
    return {'labels': labels, 'rows': out, 'as_of': on.isoformat(), 'qty': float(sum((Decimal(str(x['qty'])) for x in out), Z)),
            'value': float(q2(sum((Decimal(str(x['value'])) for x in out), Z)))}


LEDGER_KINDS = {'landed': 'Landed cost', 'revaluation': 'Revaluation', 'variance': 'Standard cost variance', 'adjustment': 'Cost adjustment',
                'rounding': 'Rounding', 'closing': 'Closing stock', 'transfer': 'Account set change'}


def stock_ledger(date_from, date_to, product=None, warehouse=None):
    """opening, every quantity move with its cost, every cost-only entry (landed cost, revaluation, cost adjustment, variance) on its
    valuation date, the running balance and the closing – the closing is the valuation at date_to. With company / branch costing a
    warehouse filter shows that warehouse's quantities and the cost amounts booked on it.
    v1.79.3 read as values, not one object per move (a million moves: minutes and gigabytes before)."""
    from datetime import timedelta
    before = date_from - timedelta(days=1)
    mq = StockMove.objects.filter(owner__isnull=True)
    vq = StockValue.objects.filter(in_stock=True)
    if product is not None:
        mq, vq = mq.filter(product=product), vq.filter(product=product)
    if warehouse is not None:
        mq, vq = mq.filter(warehouse=warehouse), vq.filter(warehouse=warehouse)
    oq = defaultdict(lambda: Z, {r['product_id']: Decimal(r['s'] or 0) for r in mq.filter(date__lte=before).values('product_id').annotate(s=Sum('qty'))})
    ov = defaultdict(lambda: Z, {r['product_id']: Decimal(r['s'] or 0) for r in vq.filter(valuation_date__lte=before).values('product_id').annotate(s=Sum('amount'))})
    whc = dict(Warehouse.objects.values_list('pk', 'code'))
    on_move = defaultdict(lambda: Z)
    rest = []
    for vid, mid, kind, vd, seq, amt, did, lid, wid, pid, note, mdate, mlot in vq.filter(valuation_date__gte=date_from, valuation_date__lte=date_to).values_list(
            'id', 'move_id', 'kind', 'valuation_date', 'seq', 'amount', 'doc_id', 'landed_id', 'warehouse_id', 'product_id', 'note', 'move__date',
            'move__lot').iterator(chunk_size=20000):
        if mid and kind == 'direct' and vd == mdate:
            on_move[mid] += Decimal(amt)
        else:
            rest.append((vid, mid, kind, vd, seq, amt, did, lid, wid, pid, note, mlot))
    ev = defaultdict(list)
    pids = set()
    dnum = {}

    def num(doc_id):
        return dnum.get(doc_id, '') if doc_id else ''
    for mid, dt, seq, did, kind, wid, pid, lot, serial, qty in mq.filter(date__gte=date_from, date__lte=date_to).values_list(
            'id', 'date', 'seq', 'doc_id', 'kind', 'warehouse_id', 'product_id', 'lot', 'serial', 'qty').iterator(chunk_size=20000):
        pids.add(pid)
        ev[pid].append((dt, seq, 0, {'date': dt, 'document': did, 'doc_id': did, 'kind': kind.replace('_', ' '), 'warehouse': whc.get(wid, ''), 'item': pid,
                                     'lot': lot or serial, 'qty': Decimal(qty), 'value': on_move.pop(mid, Z), 'note': ''}))
    lnum = {}
    if rest:
        from .models import LandedCost
        lnum = dict(LandedCost.objects.filter(pk__in={r[7] for r in rest if r[7]}).values_list('pk', 'number'))
    for vid, mid, kind, vd, seq, amt, did, lid, wid, pid, note, mlot in rest:
        pids.add(pid)
        ev[pid].append((vd, seq, 1, {'date': vd, 'document': did, 'doc_id': did, 'landed_id': lid, 'landed': lnum.get(lid, ''),
                                     'kind': LEDGER_KINDS.get(kind, 'Cost') if kind != 'direct' else 'Cost', 'warehouse': whc.get(wid, '') if wid else '',
                                     'item': pid, 'lot': (mlot or '') if mid else '', 'qty': Z, 'value': Decimal(amt), 'note': note}))
    dids = {e[3]['doc_id'] for x in ev.values() for e in x if e[3]['doc_id']}
    dl = sorted(dids)
    for k in range(0, len(dl), 20000):
        dnum.update(StockDoc.objects.filter(pk__in=dl[k:k + 20000]).values_list('pk', 'number'))
    P = apps.get_model('ZeoMasters', 'Product')
    for pid in set(oq) | set(ov):
        if oq[pid] or q2(ov[pid]):
            pids.add(pid)
    names = {p.pk: str(p) for p in P.objects.filter(pk__in=pids)}
    items = []
    for pid in sorted(pids, key=lambda x: names.get(x, '')):
        rq_, rv = oq[pid], ov[pid]
        rows = []
        nm = names.get(pid, '')
        for _, _, _, r in sorted(ev.get(pid, []), key=lambda e: (e[0], e[1], e[2])):
            rq_ += r['qty']
            rv += r['value']
            r['balance_qty'], r['balance_value'] = rq_, q2(rv)
            r['item'] = nm
            r['document'] = num(r['doc_id']) or r.pop('landed', '')
            r.pop('landed', None)
            rows.append(r)
        items.append({'item_id': pid, 'item': nm, 'opening_qty': oq[pid], 'opening_value': q2(ov[pid]), 'rows': rows, 'closing_qty': rq_,
                      'closing_value': q2(rv), 'in_qty': sum((r['qty'] for r in rows if r['qty'] > 0), Z),
                      'out_qty': -sum((r['qty'] for r in rows if r['qty'] < 0), Z)})
    return {'items': items, 'closing_value': q2(sum((i['closing_value'] for i in items), Z)), 'opening_value': q2(sum((i['opening_value'] for i in items), Z))}


CLEARING_ROLES = ('grni', 'transit', 'landed_clearing', 'landed_accrual', 'consign_payable')


def clearing(on=None, roles=CLEARING_ROLES, detail=True):
    """the clearing accounts the stock journals use (goods received not invoiced, goods in transit, landed cost clearing / accrual,
    consignment payable): per account what the stock side booked up to the date, the GL balance and so what other journals (supplier
    invoices, freight bills) have cleared; with the open documents behind the stock side."""
    on = on or today()
    st = settings()
    accs = defaultdict(set)
    for r in roles:
        accs[account(r, st=st)].add(r)
    for a, r in StockValue.objects.filter(offset_role__in=roles).values_list('offset_account', 'offset_role').distinct():
        if a:
            accs[a].add(r)
    out = []
    for acc in sorted(accs):
        base = StockValue.objects.filter(valuation_date__lte=on)
        cr = base.filter(offset_account=acc)
        dr = base.filter(inventory_account=acc)
        side = q2(Decimal(dr.aggregate(s=Sum('amount'))['s'] or 0) - Decimal(cr.aggregate(s=Sum('amount'))['s'] or 0))
        gl = gl_balance(acc, on)
        pend = q2(Decimal(dr.filter(gl='pending').aggregate(s=Sum('amount'))['s'] or 0) - Decimal(cr.filter(gl='pending').aggregate(s=Sum('amount'))['s'] or 0))
        docs = []
        if detail:
            per = defaultdict(lambda: {'amount': Z})
            for v in list(cr.select_related('doc', 'landed')) + list(dr.select_related('doc', 'landed')):
                k = ('doc', v.doc_id) if v.doc_id else ('landed', v.landed_id)
                x = per[k]
                x['amount'] += -Decimal(v.amount) if v.offset_account == acc else Decimal(v.amount)
                x['number'] = v.doc.number if v.doc_id else (v.landed.number if v.landed_id else '')
                x['date'] = v.doc.date if v.doc_id else (v.landed.date if v.landed_id else v.valuation_date)
                x['partner'] = str(getattr(v.doc, 'partner', '') or '') if v.doc_id else ''
            docs = [{'kind': k[0], 'id': k[1], 'number': x['number'], 'date': x['date'], 'partner': x['partner'], 'amount': q2(x['amount'])}
                    for k, x in sorted(per.items(), key=lambda kv: (kv[1]['date'], kv[1]['number'])) if q2(x['amount'])]
        out.append({'account': acc, 'roles': sorted(accs[acc]), 'label': ', '.join({x[0]: x[1] for x in ACCOUNT_ROLES}.get(r, r) for r in sorted(accs[acc])),
                    'stock_side': side, 'gl': gl, 'unposted': pend, 'cleared': q2(gl - (side - pend)), 'open': q2(side + (gl - (side - pend))), 'docs': docs})
    return {'as_of': on.isoformat(), 'rows': out}


def gl_balance(acc, on):
    JB, JL = _jb()
    agg = JL.objects.filter(account=acc, batch__date__lte=on).exclude(batch__status__in=('cancelled', 'failed', 'draft')).aggregate(d=Sum('debit'), c=Sum('credit'))
    return q2(Decimal(agg['d'] or 0) - Decimal(agg['c'] or 0))


def reconciliation(on=None):
    on = on or today()
    st = settings()
    accs = set(StockValue.objects.filter(in_stock=True).values_list('inventory_account', flat=True)) | {account('inventory', st=st)}
    rows = []
    for a in sorted(x for x in accs if x):
        val = value_on(on, a)
        gl = gl_balance(a, on)
        pend = q2(StockValue.objects.filter(inventory_account=a, in_stock=True, valuation_date__lte=on, gl='pending').aggregate(s=Sum('amount'))['s'] or 0)
        periodic = st.accounting_mode == 'periodic'
        diff = q2(val - gl - (Z if periodic else pend))
        rows.append({'account': a, 'valuation': float(val), 'gl': float(gl), 'unposted': float(pend), 'difference': float(diff),
                     'note': 'Periodic: the closing stock JV of the period brings the GL to the valuation' if periodic and diff else ''})
    grni = account('grni', st=st)
    return {'as_of': on.isoformat(), 'mode': st.accounting_mode, 'rows': rows, 'grni': {'account': grni, 'gl': float(gl_balance(grni, on))}}


# ------------------------------------------------------------------ periodic closing stock JV, period close
def propose_closing(period_end):
    st = settings()
    if st.accounting_mode != 'periodic':
        raise InvError('The closing stock JV is for periodic inventory accounting (Inventory › Setup).')
    accs = set(StockValue.objects.filter(in_stock=True).values_list('inventory_account', flat=True)) | {account('inventory', st=st)}
    lines = []
    for a in sorted(x for x in accs if x):
        closing = value_on(period_end, a)
        opening = gl_balance(a, period_end)
        ch = q2(closing - opening)
        if ch or closing:
            lines.append({'account': a, 'opening': float(opening), 'closing': float(closing), 'change': float(ch),
                          'change_account': account('inventory_change', st=st)})
    jv, _ = ClosingJV.objects.get_or_create(period_end=period_end, defaults={'status': 'proposed'})
    if jv.status == 'posted':
        raise InvError(f'The closing stock JV of {period_end:%d/%m/%Y} is already posted.')
    jv.lines = lines
    jv.total_change = q2(sum((Decimal(str(x['change'])) for x in lines), Z))
    if not jv.number:
        from .app import app
        jv.number = app.next_number('closing', 'CSJ')
    jv.save()
    return jv


@transaction.atomic
def post_closing(jv, user=None):
    if jv.status != 'proposed':
        raise InvError('Only a proposed JV can be posted.')
    JB, JL = _jb()
    lines = []
    for x in jv.lines or []:
        ch = Decimal(str(x['change']))
        if ch:
            lines.append((x['account'], ch))
            lines.append((x['change_account'], -ch))
    jid = None
    if lines:
        acc = defaultdict(Decimal)
        for a, v in lines:
            acc[a] += v
        lines = jlines(acc)
        dr = sum((v for _, v in lines if v > 0), Z)
        b = JB.objects.create(source='inventory', source_id=jv.number, source_label=f'Closing stock {jv.period_end:%d/%m/%Y}', date=jv.period_end,
                              document_date=jv.period_end, reference=f'Closing stock {jv.number}', status='ready', total_debit=q2(dr), total_credit=q2(dr),
                              lines_count=len(lines), idempotency_key=f'inv:closing:{jv.pk}:{jv.period_end:%Y%m%d}', created_by=user)
        b.number = f'INV-JE-{b.pk:05d}'
        b.save(update_fields=['number'])
        for i, (a, v) in enumerate(lines):
            JL.objects.create(batch=b, seq=i + 1, account=a, base_account=a, label=f'Closing stock {jv.period_end:%d/%m/%Y}', debit=v if v > 0 else Z,
                              credit=-v if v < 0 else Z)
        jid = b.pk
    jv.status, jv.journal_id, jv.posted_at = 'posted', jid, timezone.now()
    jv.save()
    st = settings()
    if not st.lock_date or st.lock_date < jv.period_end:   # the period's stock value is frozen
        st.lock_date = jv.period_end
        st.save(update_fields=['lock_date'])
    return jv


@transaction.atomic
def close_period(up_to, user=None):
    """run the cost adjustment, post what is pending (perpetual) and close the stock period"""
    st = settings()
    if st.negative != 'allow':
        neg = [r for r in valuation(up_to, group='item_warehouse') if r['qty'] < 0]
        if neg:
            raise InvError('Stock is negative at the date – ' + ', '.join(f'{r["item"]} in {r["warehouse"]} {r["qty"]:g}' for r in neg[:5])
                           + '. Post the missing receipts (or a count) first.')
    keys = set(StockMove.objects.values_list('costing_key', flat=True).distinct())
    recost_keys(keys, st)
    if st.accounting_mode == 'perpetual':
        post_to_gl(st, up_to=up_to, user=user)
        if StockValue.objects.filter(gl='pending', valuation_date__lte=up_to).exists():
            raise InvError('Some cost amounts are not in the GL yet.')
    st.lock_date = up_to
    st.save(update_fields=['lock_date'])
    return st


def adjust_all(user=None):
    st = settings()
    keys = set(StockMove.objects.values_list('costing_key', flat=True).distinct())
    n_before = StockValue.objects.count()
    recost_keys(keys, st)
    made = StockValue.objects.count() - n_before
    if st.accounting_mode == 'perpetual' and st.posting_timing == 'immediate':
        post_to_gl(st, user=user)
    return made


def _sync_products(pids):
    """the item master's "in stock" and "stock value" (ZeoMasters.Product) from the Inventory ledger"""
    P = apps.get_model('ZeoMasters', 'Product')
    for pid in pids:
        q = Decimal(StockMove.objects.filter(product_id=pid, owner__isnull=True).aggregate(s=Sum('qty'))['s'] or 0)
        v = q2(StockValue.objects.filter(product_id=pid, in_stock=True).aggregate(s=Sum('amount'))['s'] or 0)
        upd = {'stock_qty': q2(q), 'stock_value': v}
        if q > 0:   # v1.75.0 the cost shown in Masters (read-only there while Inventory is on) is the Inventory cost
            upd['cost_price'] = q2(v / q)
        P.objects.filter(pk=pid).update(**upd)


def daily():
    st = settings()
    out = {'journals': 0, 'below_reorder': 0}
    if st.accounting_mode == 'perpetual' and st.posting_timing == 'day_end':
        out['journals'] = len(post_to_gl(st, up_to=today()))
    for s in ItemSetting.objects.filter(min_qty__gt=0).select_related('product'):
        if on_hand(s.product) < s.min_qty:
            out['below_reorder'] += 1
    out['lots_expired'] = lot_status_daily()
    return out


# ------------------------------------------------------------------ v1.75.0 reorder planning
OUT_KINDS_USE = ('delivery', 'consumption', 'scrap', 'transfer_out')


def daily_use(product, wh=None, days=90, on=None):
    on = on or today()
    qs = StockMove.objects.filter(product=product, kind__in=OUT_KINDS_USE, date__gt=on - datetime.timedelta(days=days), date__lte=on)
    if wh is not None:
        qs = qs.filter(warehouse=wh)
    return -Decimal(qs.aggregate(s=Sum('qty'))['s'] or 0) / Decimal(days)


def incoming(product, wh):
    """drafts that will bring the item into the warehouse (receipts, returns, transfers to it)"""
    a = StockDocLine.objects.filter(product=product, doc__status='draft', doc__kind__in=('receipt', 'return_in', 'opening'), doc__warehouse=wh)
    b = StockDocLine.objects.filter(product=product, doc__status='draft', doc__kind='transfer', doc__to_warehouse=wh)
    return Decimal(a.aggregate(s=Sum('base_qty'))['s'] or 0) + Decimal(b.aggregate(s=Sum('base_qty'))['s'] or 0)


def lead_time(rule):
    return rule.lead_time_days if rule.lead_time_days is not None else (rule.product.lead_time_days or 0)


def suggested_point(rule, days=90):
    """reorder point = average daily use (last `days`) × lead time + safety stock (fixed, or days of use)"""
    use = daily_use(rule.product, rule.warehouse, days)
    safety = Decimal(rule.safety_qty or 0) + (use * rule.safety_days if rule.safety_days else Z)
    return rq(use * lead_time(rule) + safety), use


def _order_qty(rule, projected):
    p = rule.policy
    if p == 'fixed':
        q = Decimal(rule.reorder_qty or 0) or (Decimal(rule.max_qty or 0) - projected)
    elif p == 'lot':
        q = Decimal(rule.min_qty) - projected
    else:
        q = (Decimal(rule.max_qty or 0) or Decimal(rule.min_qty) + Decimal(rule.reorder_qty or 0)) - projected
    q = max(q, Z)
    if rule.moq and q < rule.moq:
        q = Decimal(rule.moq)
    if rule.multiple and rule.multiple > 0 and q % rule.multiple:
        q = (q // rule.multiple + 1) * rule.multiple
    return q


def suggestions(warehouse=None):
    """the items at or below their reorder point (projected = on hand − reserved + incoming): how much, from where, days of cover"""
    from .models import ReorderRule
    qs = ReorderRule.objects.filter(is_active=True).select_related('product', 'warehouse', 'source_warehouse', 'product__preferred_supplier')
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    out = []
    for r in qs:
        oh, res, inc = on_hand(r.product, r.warehouse), reserved(r.product, r.warehouse), incoming(r.product, r.warehouse)
        proj = oh - res + inc
        use = daily_use(r.product, r.warehouse)
        cover = (oh - res) / use if use > 0 else None
        row = {'rule': r.pk, 'item_id': r.product_id, 'item': str(r.product), 'warehouse_id': r.warehouse_id, 'warehouse': r.warehouse.code, 'on_hand': oh,
               'reserved': res, 'incoming': inc, 'projected': proj, 'min': Decimal(r.min_qty), 'max': Decimal(r.max_qty), 'daily_use': rq(use),
               'days_cover': rq(cover) if cover is not None else None, 'stockout': (today() + datetime.timedelta(days=int(cover))) if cover is not None else None,
               'lead_time': lead_time(r)}
        if proj > r.min_qty or (r.min_qty == 0 and proj > 0):
            continue
        q = _order_qty(r, proj)
        if q <= 0:
            continue
        src, sq = 'buy', q
        if r.source == 'transfer' and r.source_warehouse_id:
            other = ReorderRule.objects.filter(product=r.product, warehouse=r.source_warehouse).first()
            spare = available(r.product, r.source_warehouse) - (Decimal(other.min_qty) if other else Z)
            if spare > 0:
                src, sq = 'transfer', min(q, spare)
        row.update({'order_qty': sq, 'source': src, 'from': r.source_warehouse.code if src == 'transfer' else str(r.product.preferred_supplier or '')})
        out.append(row)
    return out


@transaction.atomic
def replenish(rule, user=None):
    """one click: a draft transfer from the other warehouse, else a draft goods receipt from the preferred supplier (a purchase request when
    Purchase is on) – the storekeeper checks and posts it"""
    sug = next((x for x in suggestions(rule.warehouse) if x['rule'] == rule.pk), None)
    if sug is None:
        raise InvError(f'{rule.product} at {rule.warehouse.code} is above its reorder point – nothing to order.')
    if sug['source'] == 'transfer':
        doc = StockDoc.objects.create(kind='transfer', date=today(), warehouse=rule.source_warehouse, to_warehouse=rule.warehouse,
                                      reference=f'Reorder {rule.product}', branch_id=rule.source_warehouse.branch_id)
    else:
        doc = StockDoc.objects.create(kind='receipt', date=today(), warehouse=rule.warehouse, partner=rule.product.preferred_supplier,
                                      reference=f'Reorder {rule.product}', branch_id=rule.warehouse.branch_id)
    from .app import PREFIX, app
    op = OperationType.objects.filter(warehouse=doc.warehouse, kind=doc.kind, is_active=True).order_by('sequence', 'id').first()
    if op is not None:
        doc.operation = op
    doc.number = app.next_number(f'op_{op.pk}', op.code) if op is not None and op.code else app.next_number(f'doc_{doc.kind}', PREFIX.get(doc.kind, 'STK'))
    doc.save(update_fields=['number', 'operation'])
    StockDocLine.objects.create(doc=doc, product=rule.product, uom=rule.product.uom, qty=sug['order_qty'], factor=1, base_qty=sug['order_qty'],
                                unit_cost=rule.product.purchase_price or rule.product.cost_price or None,
                                base_cost=rule.product.purchase_price or rule.product.cost_price or None)
    return doc


def abc(days=365, on=None):
    """ABC by the value used in the last `days` (A: the items making 80 % of it, B: the next 15 %, C: the rest)"""
    on = on or today()
    used = defaultdict(lambda: Z)
    for r in StockMove.objects.filter(kind__in=OUT_KINDS_USE, date__gt=on - datetime.timedelta(days=days), date__lte=on).exclude(kind='transfer_out') \
            .values('product_id').annotate(v=Sum('value')):
        used[r['product_id']] += -Decimal(r['v'] or 0)
    tot = sum(used.values(), Z)
    P = apps.get_model('ZeoMasters', 'Product')
    names = {p.pk: str(p) for p in P.objects.filter(pk__in=used)}
    rows, run_ = [], Z
    for pid, v in sorted(used.items(), key=lambda kv: -kv[1]):
        run_ += v
        share = (run_ / tot * 100) if tot else Z
        cls = 'A' if share <= 80 or not rows else ('B' if share <= 95 else 'C')
        rows.append({'item_id': pid, 'item': names.get(pid, ''), 'used': q2(v), 'share': q2(v / tot * 100) if tot else Z, 'cumulative': q2(share), 'class': cls})
    return rows



# ------------------------------------------------------------------ v1.75.0 packing materials, packages, drop ship warehouse
def _packing_lines(doc):
    """packing rules of the operation (or of every document of the kind): the materials are added as lines (consumption on the same document
    kind is not possible – they leave as their own issue lines of a delivery / a consumption document)"""
    from .models import PackingRule
    rules = PackingRule.objects.filter(is_active=True).filter(Q(operation_id=doc.operation_id) if doc.operation_id else Q(pk__in=[])) | \
        PackingRule.objects.filter(is_active=True, operation__isnull=True, kind=doc.kind)
    if doc.kind not in ('delivery', 'consumption', 'scrap') or not rules.exists():
        return
    lines = [x for x in doc.lines.all() if not x.note.startswith('Packing material')]
    if not lines:
        return
    doc.lines.filter(note__startswith='Packing material').delete()
    for r in rules.select_related('material', 'material__uom'):
        q = Decimal(r.qty) * (1 if r.per == 'doc' else len(lines) if r.per == 'line' else sum((Decimal(x.base_qty or x.qty or 0) for x in lines), Z))
        if q > 0:
            StockDocLine.objects.create(doc=doc, product=r.material, uom=r.material.uom, qty=q, note=f'Packing material ({r.get_per_display().lower()})')


def packs_text(product, qty):
    """3 CTN + 5 PCS: the quantity in the item's packaging levels (largest first)"""
    from .models import ItemUnit
    q = Decimal(qty or 0)
    if not q:
        return '0'
    units = list(ItemUnit.objects.filter(product=product, is_active=True).exclude(level='').select_related('uom').order_by('-factor'))
    parts = []
    for u in units:
        f = Decimal(u.factor)
        if f > 1 and abs(q) >= f:
            n = int(abs(q) // f)
            parts.append(f'{n} {u.uom.code}')
            q = q - (n * f if q > 0 else -n * f)
    if q or not parts:
        parts.append(f'{q.normalize():f} {product.uom.code if product.uom_id else ""}'.strip())
    return ' + '.join(parts)


def dropship_warehouse():
    w = Warehouse.objects.filter(code='DROPSHIP').first()
    if w is None:
        w = Warehouse.objects.create(code='DROPSHIP', name='Drop ship (no stock – supplier to customer)', negative='allow')
    return w


# ------------------------------------------------------------------ v1.75.0 scanning (barcode / QR / GS1)
def _gs1(code):
    """GS1-128 / DataMatrix: (01) GTIN, (10) lot, (17) expiry YYMMDD, (21) serial – with brackets or the FNC1 separator"""
    import re
    c = code.strip()
    out = {}
    if c.startswith(']C1') or c.startswith(']d2') or c.startswith(']Q3'):
        c = c[3:]
    if '(' in c:
        for ai, v in re.findall(r'\((\d{2,4})\)([^(]*)', c):
            out[ai] = v.strip()
        return out
    if not re.match(r'^(01\d{14}|10|17\d{6}|21)', c) or len(c) < 16:
        return {}
    i = 0
    while i < len(c):
        ai = c[i:i + 2]
        if ai == '01':
            out['01'], i = c[i + 2:i + 16], i + 16
        elif ai == '17':
            out['17'], i = c[i + 2:i + 8], i + 8
        elif ai in ('10', '21'):
            j = c.find('\x1d', i + 2)
            j = len(c) if j < 0 else j
            out[ai], i = c[i + 2:j], j + 1
        else:
            break
    return out


def scan(code, warehouse=None):
    """what a scanned code is: an item (its barcode, a packaging barcode = that many units, its code / number), a lot, a serial number,
    a bin – or a GS1 code carrying the item, lot, expiry and serial"""
    from .models import ItemUnit
    P = apps.get_model('ZeoMasters', 'Product')
    code = (code or '').strip()
    if not code:
        raise InvError('Scan or type a code.')
    g = _gs1(code)
    res = {'code': code}
    if g:
        gt = g.get('01', '').lstrip('0')
        iu = ItemUnit.objects.filter(barcode__in=[g.get('01', ''), gt]).select_related('product', 'uom').first() if gt else None
        p = iu.product if iu else (P.objects.filter(barcode__in=[g.get('01', ''), gt]).first() if gt else None)
        if p is None:
            raise InvError(f'No item has the GTIN {g.get("01", "")}.')
        res.update({'kind': 'gs1', 'product': p, 'uom': iu.uom if iu else p.uom, 'factor': Decimal(iu.factor) if iu else Decimal(1),
                    'lot': g.get('10', ''), 'serial': g.get('21', '')})
        if g.get('17'):
            y, mth, d = int(g['17'][:2]), int(g['17'][2:4]), int(g['17'][4:6]) or 1
            res['expiry'] = datetime.date(2000 + y, mth, d)
        return res
    iu = ItemUnit.objects.filter(barcode=code, is_active=True).select_related('product', 'uom').first()
    if iu is not None:
        res.update({'kind': 'package', 'product': iu.product, 'uom': iu.uom, 'factor': Decimal(iu.factor)})
        return res
    p = P.objects.filter(Q(barcode=code) | Q(code__iexact=code) | Q(number__iexact=code)).first()
    if p is not None:
        res.update({'kind': 'item', 'product': p, 'uom': p.uom, 'factor': Decimal(1)})
        return res
    b = Bin.objects.filter(code__iexact=code, **({'warehouse': warehouse} if warehouse is not None else {})).first()
    if b is not None:
        res.update({'kind': 'bin', 'bin': b})
        return res
    m = StockMove.objects.filter(serial=code).select_related('product').order_by('-seq').first()
    if m is not None:
        res.update({'kind': 'serial', 'product': m.product, 'uom': m.product.uom, 'factor': Decimal(1), 'serial': code})
        return res
    m = StockMove.objects.filter(lot=code).select_related('product').order_by('-seq').first()
    if m is not None:
        res.update({'kind': 'lot', 'product': m.product, 'uom': m.product.uom, 'factor': Decimal(1), 'lot': code, 'expiry': m.expiry})
        return res
    raise InvError(f'Unknown code "{code[:60]}" – not an item, packaging, lot, serial number or bin.')


def scan_into(doc, code, qty=1, bin_=None):
    """a scan on a draft document: adds the line or adds to the line of the same item / unit / lot / serial / bin"""
    if doc.status != 'draft':
        raise InvError('The document is posted.')
    r = scan(code, doc.warehouse)
    if r['kind'] == 'bin':
        return r, None
    p = r['product']
    q = Decimal(qty or 1)
    f = dict(product=p, uom=r.get('uom'), lot=r.get('lot', ''), serial=r.get('serial', ''))
    if bin_ is not None:
        f['bin'] = bin_
    ln = doc.lines.filter(**f).first()
    if ln is not None and not r.get('serial'):
        if doc.kind == 'count':
            ln.counted_qty = Decimal(ln.counted_qty or 0) + q
        else:
            ln.qty = Decimal(ln.qty or 0) + q
        ln.save()
    else:
        if r.get('serial') and doc.lines.filter(product=p, serial=r['serial']).exists():
            raise InvError(f'Serial number {r["serial"]} is already on this document.')
        ln = StockDocLine(doc=doc, expiry=r.get('expiry'), **f)
        if doc.kind == 'count':
            ln.counted_qty, ln.qty = q, 0
        else:
            ln.qty = q
        if doc.kind in ('receipt', 'opening', 'dropship'):
            c = p.purchase_price or p.cost_price or None
            ln.unit_cost = Decimal(c) * r['factor'] if c is not None else None
        ln.save()
    return r, ln


# ------------------------------------------------------------------ v1.75.0 related products, alternatives, tags
def related(product, kind=None):
    from .models import ItemLink
    qs = ItemLink.objects.filter(product=product).select_related('related')
    back = ItemLink.objects.filter(related=product, both_ways=True).select_related('product')
    if kind:
        qs, back = qs.filter(kind=kind), back.filter(kind=kind)
    out = [{'item_id': x.related_id, 'item': str(x.related), 'kind': x.kind, 'kind_label': x.get_kind_display(), 'qty': x.default_qty, 'sequence': x.sequence}
           for x in qs]
    out += [{'item_id': x.product_id, 'item': str(x.product), 'kind': x.kind, 'kind_label': x.get_kind_display(), 'qty': x.default_qty, 'sequence': x.sequence}
            for x in back if x.product_id not in {o['item_id'] for o in out}]
    return sorted(out, key=lambda o: (o['kind'], o['sequence']))


def alternatives(product, wh, need=None):
    """the alternatives / substitutes of an item with stock available in the warehouse (offered when the item is short)"""
    P = apps.get_model('ZeoMasters', 'Product')
    out = []
    for r in related(product, 'alternative'):
        p = P.objects.get(pk=r['item_id'])
        a = available(p, wh)
        if a > 0 and (need is None or a >= need):
            out.append({**r, 'available': a})
    return out


def import_item_tags():
    """the free-text tags typed on items in Masters become tag records on the items' Inventory settings"""
    from .models import Tag
    P = apps.get_model('ZeoMasters', 'Product')
    n = 0
    for p in P.objects.all().only('pk', 'tags'):
        raw = p.tags if isinstance(p.tags, list) else str(p.tags or '').replace(';', ',').split(',')
        names = [str(t).strip()[:40] for t in raw if str(t).strip()]
        if not names:
            continue
        ist = ItemSetting.objects.filter(product=p).first() or ItemSetting.objects.create(product=p)
        for nm in names:
            t = Tag.objects.filter(name__iexact=nm).first() or Tag.objects.create(name=nm)
            ist.tags.add(t)
            n += 1
    return n


def tags_of(product):
    ist = item_setting(product)
    return ', '.join(t.name for t in ist.tags.all()) if ist is not None else ''


# ------------------------------------------------------------------ v1.75.0 stock count plans
@transaction.atomic
def count_start(plan, user=None):
    """the count document of a plan: a line per item × lot × serial × bin in scope holding stock at the start (frozen as expected)"""
    from .app import PREFIX, app
    if plan.status != 'draft':
        raise InvError('The count has started already.')
    wh = plan.warehouse
    qs = StockMove.objects.filter(warehouse=wh, date__lte=plan.date, owner__isnull=True)
    if plan.category_id:
        qs = qs.filter(product__category_id=plan.category_id)
    if plan.zone:
        qs = qs.filter(bin__zone=plan.zone)
    if plan.abc_class:
        cls = {r['item_id'] for r in abc(365, plan.date) if r['class'] == plan.abc_class}
        qs = qs.filter(product_id__in=cls)
    rows = [r for r in qs.values('product_id', 'lot', 'serial', 'bin_id').annotate(s=Sum('qty')) if Decimal(r['s'] or 0) != 0]
    op = OperationType.objects.filter(warehouse=wh, kind='count', is_active=True).first()
    doc = StockDoc.objects.create(kind='count', date=plan.date, warehouse=wh, operation=op, reference=f'Count {plan.number} {plan.name}'[:80], branch_id=wh.branch_id,
                                  created_by=user if (user and getattr(user, 'is_authenticated', False)) else None)
    doc.number = app.next_number(f'op_{op.pk}', op.code) if op is not None and op.code else app.next_number('doc_count', PREFIX['count'])
    doc.save(update_fields=['number'])
    P = apps.get_model('ZeoMasters', 'Product')
    prods = {p.pk: p for p in P.objects.filter(pk__in={r['product_id'] for r in rows}).select_related('uom')}
    snap = []
    for r in sorted(rows, key=lambda x: (str(prods[x['product_id']]), x['lot'], x['serial'])):
        p = prods[r['product_id']]
        q = Decimal(r['s'])
        StockDocLine.objects.create(doc=doc, product=p, uom=p.uom, qty=0, expected_qty=q, factor=1, lot=r['lot'], serial=r['serial'], bin_id=r['bin_id'])
        snap.append({'item_id': p.pk, 'lot': r['lot'], 'serial': r['serial'], 'bin_id': r['bin_id'], 'qty': float(q)})
    plan.doc, plan.snapshot, plan.status = doc, snap, 'counting'
    plan.save(update_fields=['doc', 'snapshot', 'status'])
    return doc


def count_review(plan, save=True):
    """per line: expected (frozen), counted, difference and value; lines above the tolerance must be recounted"""
    doc = plan.doc
    if doc is None:
        raise InvError('Start the count first.')
    st = settings()
    tot = _key_totals(plan.date)
    rows, recount, value, missing = [], [], Z, 0
    for ln in doc.lines.select_related('product', 'bin').order_by('id'):
        exp = Decimal(ln.expected_qty or 0)
        if ln.counted_qty is None:
            missing += 1
        cnt = Decimal(ln.counted_qty) * Decimal(ln.factor or 1) if ln.counted_qty is not None else None
        diff = (cnt - exp) if cnt is not None else None
        k = tot.get(costing_key(ln.product_id, doc.warehouse, st))
        unit = (k['value'] / k['qty']) if k and k['qty'] else Decimal(ln.product.cost_price or 0)
        val = q2(diff * unit) if diff is not None else None
        over = diff is not None and ((plan.tolerance_qty is not None and abs(diff) > plan.tolerance_qty) or
                                     (plan.tolerance_value is not None and abs(val) > plan.tolerance_value))
        if over:
            recount.append(ln.pk)
        value += val or Z
        rows.append({'line': ln.pk, 'item': str(ln.product), 'lot': ln.lot or ln.serial, 'bin': ln.bin.code if ln.bin_id else '', 'expected': float(exp),
                     'counted': float(cnt) if cnt is not None else None, 'difference': float(diff) if diff is not None else None,
                     'value': float(val) if val is not None else None, 'recount': 'Recount' if over else ''})
    if save and plan.status in ('counting', 'recount'):
        if missing:
            raise InvError(f'{missing} lines are not counted yet – enter the counted quantity (0 when there is none).')
        new = 'recount' if recount and plan.status == 'counting' else plan.status if recount else 'counting'
        if new != plan.status:
            plan.status = new
            plan.save(update_fields=['status'])
    return {'rows': rows, 'recount': recount, 'value': q2(value), 'missing': missing}



# ------------------------------------------------------------------ v1.75.0 consignment settlement
def consign_used(partner, date_to):
    """supplier consignment stock taken over (used) and not settled yet, at the agreed prices"""
    rows = []
    for m in StockMove.objects.filter(owner=partner, kind='consign_out', settlement__isnull=True, date__lte=date_to).select_related('product', 'doc').order_by('date', 'seq'):
        price = consign_price(partner, m.product, m.date)
        q = -Decimal(m.qty)
        rows.append({'move': m.pk, 'date': m.date.isoformat(), 'document': m.doc.number if m.doc_id else '', 'item': str(m.product), 'qty': float(q), 'price': float(price),
                     'amount': float(q2(q * price))})
    return rows


@transaction.atomic
def post_settlement(stl, user=None):
    """the statement owed to the supplier: Dr Consignment payable / Cr Accounts payable; the used moves are marked settled"""
    st = settings()
    if stl.status != 'draft':
        raise InvError('The settlement is posted.')
    rows = consign_used(stl.partner, stl.date_to)
    if not rows:
        raise InvError('Nothing of this supplier was used and not settled.')
    total = q2(sum((Decimal(str(r['amount'])) for r in rows), Z))
    jid = None
    if st.accounting_mode == 'perpetual':
        JB, JL = _jb()
        lab = f'Consignment settlement {stl.number} – {stl.partner}'
        b = JB.objects.create(source='inventory', source_id=(stl.number or f'CST-{stl.pk}')[:40], source_label=lab[:200], date=stl.date, document_date=stl.date,
                              reference=(stl.supplier_invoice or lab)[:120], status='ready', total_debit=total, total_credit=total, lines_count=2,
                              idempotency_key=f'inv:consign:{stl.pk}:{stl.date:%Y%m%d}', created_by=user if (user and getattr(user, 'is_authenticated', False)) else None)
        b.number = f'INV-JE-{b.pk:05d}'
        b.save(update_fields=['number'])
        a1, a2 = account('consign_payable', st=st), account('payable', st=st)
        JL.objects.create(batch=b, seq=1, account=a1, base_account=a1, label=f'{stl.partner} consignment used to {stl.date_to:%d/%m/%Y}'[:255], debit=total, credit=Z)
        JL.objects.create(batch=b, seq=2, account=a2, base_account=a2, label=f'{stl.partner} {stl.supplier_invoice}'.strip()[:255], debit=Z, credit=total)
        jid = b.pk
    StockMove.objects.filter(pk__in=[r['move'] for r in rows]).update(settlement=stl)
    stl.lines, stl.total, stl.status, stl.journal_id, stl.posted_at = rows, total, 'posted', jid, timezone.now()
    stl.save()
    return stl


def consignment_stock(on=None):
    """supplier consignment stock (quantity, at the agreed price, age) per supplier, item and warehouse"""
    on = on or today()
    st = settings()
    out = []
    for r in StockMove.objects.filter(owner__isnull=False, date__lte=on).values('owner_id', 'owner__name', 'product_id', 'product__name', 'warehouse__code') \
            .annotate(s=Sum('qty'), first=Min('date')).order_by('owner__name', 'product__name'):
        q = Decimal(r['s'] or 0)
        if q <= 0:
            continue
        P = apps.get_model('ZeoMasters', 'Partner')
        Pr = apps.get_model('ZeoMasters', 'Product')
        try:
            price = consign_price(P.objects.get(pk=r['owner_id']), Pr.objects.get(pk=r['product_id']), on)
        except InvError:
            price = None
        age = (on - r['first']).days
        out.append({'supplier': r['owner__name'], 'item': r['product__name'], 'warehouse': r['warehouse__code'], 'qty': float(q),
                    'price': float(price) if price is not None else None, 'value': float(q2(q * price)) if price is not None else None, 'age': age,
                    'old': age > (st.consign_age_days or 180)})
    return out


# ------------------------------------------------------------------ v1.75.0 attributes and variants
def attr_shown(ia):
    a = ia.attribute
    if a.kind == 'choice':
        return ia.value.value if ia.value_id else ''
    if a.kind == 'number':
        return f'{Decimal(ia.number).normalize():f} {a.unit}'.strip() if ia.number is not None else ''
    if a.kind == 'yesno':
        return '' if ia.yes is None else ('Yes' if ia.yes else 'No')
    return ia.text


def missing_attributes(product):
    """the required attributes of the item's category (and its parents) that the item has no value for"""
    from .models import CategoryAttribute, ItemAttribute
    cats = [c.pk for c, _ in _cat_chain(getattr(product, 'category_id', None))]
    req = CategoryAttribute.objects.filter(category_id__in=cats, required=True).select_related('attribute')
    have = {x.attribute_id for x in ItemAttribute.objects.filter(product=product).exclude(shown='')}
    return [r.attribute.name for r in req if r.attribute_id not in have]


@transaction.atomic
def generate_variants(tpl, user=None):
    """every combination of the template's axis values (less the excluded ones) becomes an item of its own: code / name from the patterns,
    the template's category, unit, prices and stock settings copied; the attribute values set"""
    import itertools
    from .models import ItemAttribute, Variant
    from ZeoMasters import items as ITM
    P = apps.get_model('ZeoMasters', 'Product')
    base = tpl.product
    axes = list(tpl.axes.select_related('attribute').order_by('sequence', 'id'))
    if not axes:
        raise InvError('Add the axes (size, colour …) with their values first.')
    lists = []
    for ax in axes:
        vals = list(ax.values.filter(is_active=True).order_by('sequence', 'value'))
        if not vals:
            raise InvError(f'Choose the values of {ax.attribute.name}.')
        lists.append(vals)
    excl = {tuple(str(x).upper() for x in e) for e in (tpl.excluded or []) if isinstance(e, (list, tuple))}
    ist = item_setting(base)
    made, kept = [], 0
    for combo in itertools.product(*lists):
        codes = tuple((v.code or v.value).upper() for v in combo)
        if codes in excl:
            continue
        key = '-'.join(str(v.pk) for v in combo)
        if Variant.objects.filter(template=tpl, key=key).exists():
            kept += 1
            continue
        vcode = '-'.join(v.code or v.value for v in combo)
        vlabel = ' / '.join(v.value for v in combo)
        code = tpl.code_pattern.format(code=base.code or base.number, values=vcode, name=base.name)[:40]
        if P.objects.filter(code__iexact=code).exists():
            raise InvError(f'An item with the code {code} exists already – change the code pattern.')
        p = P(kind=base.kind, code=code, name=tpl.name_pattern.format(name=base.name, values=vlabel, code=base.code)[:200], category=base.category, uom=base.uom,
              sale_price=base.sale_price, purchase_price=base.purchase_price, cost_price=base.cost_price, tax=base.tax, track_stock=base.track_stock,
              is_sold=base.is_sold, is_purchased=base.is_purchased, preferred_supplier=base.preferred_supplier)
        ITM.save_item(p)
        Variant.objects.create(template=tpl, product=p, key=key, label=vlabel)
        for ax, v in zip(axes, combo):
            ItemAttribute.objects.create(product=p, attribute=ax.attribute, value=v, shown=v.value)
        if ist is not None:
            ItemSetting.objects.create(product=p, method=ist.method, tracking=ist.tracking, removal=ist.removal, account_set=ist.account_set,
                                       min_qty=ist.min_qty, reorder_qty=ist.reorder_qty, shelf_life_days=ist.shelf_life_days)
        made.append(p)
    return made, kept


def variant_matrix(tpl, wh=None):
    """stock per variant as a grid: the first axis down, the other axes across"""
    axes = list(tpl.axes.select_related('attribute').order_by('sequence', 'id'))
    rows_ax = list(axes[0].values.order_by('sequence', 'value')) if axes else []
    cols = []
    if len(axes) > 1:
        import itertools
        cols = list(itertools.product(*[list(a.values.order_by('sequence', 'value')) for a in axes[1:]]))
    var = {v.key: v for v in tpl.variants.select_related('product')}
    out = []
    for rv in rows_ax:
        r = {'value': rv.value}
        for c in (cols or [()]):
            key = '-'.join(str(x.pk) for x in (rv,) + tuple(c))
            v = var.get(key)
            r[' / '.join(x.value for x in c) or 'qty'] = float(on_hand(v.product, wh)) if v else None
        out.append(r)
    return [' / '.join(x.value for x in c) for c in cols] or ['qty'], out


@transaction.atomic
def grid_into(doc, tpl, grid):
    """grid entry: {"RED/L": 5, "BLUE/M": 2, …} (value codes or values of the axes, in axis order) → a line per variant"""
    if doc.status != 'draft':
        raise InvError('The document is posted.')
    axes = list(tpl.axes.order_by('sequence', 'id'))
    var = {v.key: v for v in tpl.variants.select_related('product')}
    made = []
    for k, q in (grid or {}).items():
        if not q:
            continue
        parts = [x.strip() for x in str(k).replace('|', '/').split('/')]
        if len(parts) != len(axes):
            raise InvError(f'"{k}": give one value per axis ({", ".join(a.attribute.name for a in axes)}).')
        vids = []
        for ax, part in zip(axes, parts):
            v = ax.values.filter(Q(code__iexact=part) | Q(value__iexact=part)).first()
            if v is None:
                raise InvError(f'{part} is not a value of {ax.attribute.name}.')
            vids.append(str(v.pk))
        v = var.get('-'.join(vids))
        if v is None:
            raise InvError(f'There is no variant {k} – make the variants first.')
        p = v.product
        ln = StockDocLine.objects.create(doc=doc, product=p, uom=p.uom, qty=Decimal(str(q)),
                                         unit_cost=(p.purchase_price or p.cost_price or None) if doc.kind in ('receipt', 'opening') else None)
        made.append(ln)
    return made



# ------------------------------------------------------------------ v1.75.0 lots and serial numbers
def next_lot(product, on=None):
    """PREFIX-YYMMDD-001 for an item with a lot / serial prefix"""
    ist = item_setting(product)
    pre = (ist.lot_prefix if ist is not None else '') or ''
    if not pre:
        return ''
    on = on or today()
    stem = f'{pre}-{on:%y%m%d}-'
    from .models import LotRecord
    have = set(LotRecord.objects.filter(product=product, lot__startswith=stem).values_list('lot', flat=True)) | \
        set(LotRecord.objects.filter(product=product, serial__startswith=stem).values_list('serial', flat=True)) | \
        set(StockDocLine.objects.filter(product=product, lot__startswith=stem).values_list('lot', flat=True)) | \
        set(StockDocLine.objects.filter(product=product, serial__startswith=stem).values_list('serial', flat=True))
    n = 1
    while f'{stem}{n:03d}' in have:
        n += 1
    return f'{stem}{n:03d}'


def _lot_records(doc, moves):
    """the lot / serial record of every inflow of a tracked item (expiry from the shelf life when not given)"""
    from .models import LotRecord
    for m in moves:
        if m.qty <= 0 or not (m.lot or m.serial) or m.kind in ('transfer_in',):
            continue
        tr = tracking_of(m.product)
        if tr not in ('lot', 'serial'):
            continue
        lot, ser = (m.lot, '') if tr == 'lot' else ('', m.serial)
        lr = LotRecord.objects.filter(product=m.product, lot=lot, serial=ser).first()
        exp = m.expiry
        if exp is None:
            ist = item_setting(m.product)
            if ist is not None and ist.shelf_life_days:
                exp = m.date + datetime.timedelta(days=ist.shelf_life_days)
                StockMove.objects.filter(pk=m.pk).update(expiry=exp)
        if lr is None:
            LotRecord.objects.create(product=m.product, lot=lot, serial=ser, expiry=exp, supplier=doc.partner if doc.kind == 'receipt' else None,
                                     alert_date=(exp - datetime.timedelta(days=30)) if exp else None)


def lot_status_daily(on=None):
    """nightly: lots past their expiry become expired (they are not picked any more)"""
    from .models import LotRecord
    on = on or today()
    return LotRecord.objects.filter(status='available', expiry__lt=on).update(status='expired')


def trace(product, lot='', serial=''):
    """forward and backward trace of a lot / serial number: every movement, document and partner (receipts from suppliers, deliveries to customers)"""
    qs = StockMove.objects.filter(product=product).select_related('doc', 'doc__partner', 'warehouse').order_by('date', 'seq')
    qs = qs.filter(serial=serial) if serial else qs.filter(lot=lot)
    return [{'date': m.date, 'document': m.doc.number if m.doc_id else '', 'doc_id': m.doc_id, 'kind': m.kind.replace('_', ' '), 'warehouse': m.warehouse.code,
             'partner': str(m.doc.partner or '') if m.doc_id else '', 'qty': float(m.qty)} for m in qs]


@transaction.atomic
def reclassify(lr, new_lot=None, new_expiry=None, user=None):
    """rename a lot / serial or change its expiry everywhere (moves, lines, record) – audited by the caller"""
    tr = 'serial' if lr.serial else 'lot'
    old = lr.serial or lr.lot
    if new_lot and new_lot != old:
        from .models import LotRecord
        if LotRecord.objects.filter(product=lr.product, **{tr: new_lot}).exclude(pk=lr.pk).exists():
            raise InvError(f'{new_lot} exists already for {lr.product}.')
        StockMove.objects.filter(product=lr.product, **{tr: old}).update(**{tr: new_lot})
        StockDocLine.objects.filter(product=lr.product, **{tr: old}).update(**{tr: new_lot})
        setattr(lr, tr, new_lot)
    if new_expiry is not None:
        StockMove.objects.filter(product=lr.product, **{tr: getattr(lr, tr)}).update(expiry=new_expiry)
        lr.expiry = new_expiry
        if lr.status == 'expired' and new_expiry >= today():
            lr.status = 'available'
    lr.save()
    return lr


def expiring(days=30, on=None):
    """lots in stock expiring within `days` (and already expired)"""
    from .models import LotRecord
    on = on or today()
    out = []
    for lr in LotRecord.objects.filter(expiry__isnull=False, expiry__lte=on + datetime.timedelta(days=days)).select_related('product'):
        q = on_hand(lr.product, lot=lr.lot or None, serial=lr.serial or None)
        if q > 0:
            out.append({'id': lr.pk, 'item': str(lr.product), 'lot': lr.serial or lr.lot, 'expiry': lr.expiry, 'days': (lr.expiry - on).days, 'qty': float(q),
                        'status': lr.get_status_display()})
    return sorted(out, key=lambda r: r['expiry'])



# ------------------------------------------------------------------ v1.75.0 two-step transfers
def transit_warehouse(src):
    w = Warehouse.objects.filter(is_transit=True, branch_id=src.branch_id).order_by('id').first()
    if w is None:
        code = 'TRANSIT' if not Warehouse.objects.filter(code='TRANSIT').exists() else f'TRANSIT{src.branch_id or 0}'
        w = Warehouse.objects.create(code=code, name='Goods in transit', is_transit=True, branch_id=src.branch_id, negative='block')
    return w


def shipment_left(doc):
    """per line of a two-step transfer: shipped, received so far, still in transit"""
    rec = defaultdict(lambda: Z)
    for a in StockDocLine.objects.filter(doc__shipment_of=doc, doc__status='posted').exclude(doc__kind='scrap'):
        if a.note.startswith('L') and a.note[1:].split(' ')[0].isdigit():
            rec[int(a.note[1:].split(' ')[0])] += Decimal(a.base_qty)
    lost = defaultdict(lambda: Z)
    for a in StockDocLine.objects.filter(doc__shipment_of=doc, doc__status='posted', doc__kind='scrap'):
        if a.note.startswith('L') and a.note[1:].split(' ')[0].isdigit():
            lost[int(a.note[1:].split(' ')[0])] += Decimal(a.base_qty)
    return [{'line': ln.pk, 'item': str(ln.product), 'lot': ln.lot or ln.serial, 'shipped': Decimal(ln.base_qty), 'received': rec[ln.pk], 'short': lost[ln.pk],
             'left': Decimal(ln.base_qty) - rec[ln.pk] - lost[ln.pk]} for ln in doc.lines.select_related('product').order_by('id')]


def _arrival(doc, kind, qtys, on, user=None, reason=None, note=''):
    from .app import PREFIX, app
    left = {r['line']: r['left'] for r in shipment_left(doc)}
    transit = transit_warehouse(doc.warehouse)
    a = StockDoc.objects.create(kind=kind, date=on, document_date=on, warehouse=transit, to_warehouse=doc.to_warehouse if kind == 'transfer' else None,
                                shipment_of=doc, reason=reason, reference=f'{"Arrival" if kind == "transfer" else "Short on arrival"} of {doc.number}'[:80],
                                branch_id=doc.to_warehouse.branch_id if kind == 'transfer' else transit.branch_id, note=note,
                                created_by=user if (user and getattr(user, 'is_authenticated', False)) else None)
    a.number = app.next_number(f'doc_{kind}', PREFIX.get(kind, 'STK'))
    a.save(update_fields=['number'])
    n = 0
    for ln in doc.lines.select_related('product', 'product__uom'):
        q = Decimal(str(qtys.get(ln.pk, qtys.get(str(ln.pk), 0)) or 0))
        if q <= 0:
            continue
        if q > left.get(ln.pk, Z):
            raise InvError(f'{ln.product}: {q.normalize():f} is more than the {left.get(ln.pk, Z).normalize():f} still in transit.')
        StockDocLine.objects.create(doc=a, product=ln.product, uom=ln.product.uom, qty=q, factor=1, base_qty=q, lot=ln.lot, serial=ln.serial, expiry=ln.expiry,
                                    to_bin=ln.to_bin if kind == 'transfer' else None, note=f'L{ln.pk}')
        n += 1
    if not n:
        a.delete()
        raise InvError('Enter the quantities.')
    post(a, user)
    rows = shipment_left(doc)
    doc.transit_status = 'received' if all(r['left'] <= 0 and not r['short'] for r in rows) else \
        ('closed' if all(r['left'] <= 0 for r in rows) else 'partly')
    doc.save(update_fields=['transit_status'])
    return a


@transaction.atomic
def receive_transfer(doc, qtys, on=None, user=None):
    """the other warehouse receives (all or part) of a two-step transfer: transit → destination"""
    if not (doc.kind == 'transfer' and doc.two_step and doc.status == 'posted'):
        raise InvError('Only a posted two-step transfer is received.')
    return _arrival(doc, 'transfer', qtys, on or today(), user)


@transaction.atomic
def close_short(doc, qtys, on=None, user=None, note=''):
    """short / damaged on arrival: what will not arrive is written off from transit (scrap, reason Lost in transit)"""
    from .models import Reason
    if not (doc.kind == 'transfer' and doc.two_step and doc.status == 'posted'):
        raise InvError('Only a posted two-step transfer is closed short.')
    rsn = Reason.objects.filter(kind='scrap', is_active=True).first()
    return _arrival(doc, 'scrap', qtys, on or today(), user, reason=rsn, note=note)


def transit_list(on=None):
    """two-step transfers still in transit with the expected date and age"""
    on = on or today()
    out = []
    for d in StockDoc.objects.filter(kind='transfer', two_step=True, status='posted', transit_status__in=('transit', 'partly')).select_related('warehouse', 'to_warehouse'):
        left = sum((r['left'] for r in shipment_left(d)), Z)
        out.append({'id': d.pk, 'number': d.number, 'from': d.warehouse.code, 'to': d.to_warehouse.code, 'shipped_on': d.date, 'expected': d.expected_date,
                    'days': (on - d.date).days, 'overdue': bool(d.expected_date and d.expected_date < on), 'left': float(left), 'status': d.get_transit_status_display()})
    return out


# ------------------------------------------------------------------ v1.75.0 warehouse rights
def allowed_warehouses(user):
    """None: every warehouse; else the ids of the warehouses the user may work in (warehouses with no users listed are open to all)"""
    if not Warehouse.objects.filter(users__isnull=False).exists():
        return None
    return set(Warehouse.objects.filter(Q(users=user) | Q(users__isnull=True)).values_list('pk', flat=True))


# ------------------------------------------------------------------ v1.75.0 kits
def kit_of(product):
    from .models import KitComponent
    return list(KitComponent.objects.filter(kit=product).select_related('component', 'component__uom'))


def _explode_kits(doc):
    """a kit on a delivery / issue: its components leave instead (the kit line keeps the record)"""
    if doc.kind not in ('delivery', 'consumption', 'scrap'):
        return
    for ln in list(doc.lines.select_related('product')):
        comps = kit_of(ln.product)
        if not comps or ln.note.startswith('Kit '):
            continue
        q = Decimal(ln.base_qty or ln.qty or 0)
        for c in comps:
            StockDocLine.objects.create(doc=doc, product=c.component, uom=c.component.uom, qty=q * Decimal(c.qty), note=f'Kit {ln.product.number or ln.product.code} ×{q.normalize():f}')
        ln.delete()


# ------------------------------------------------------------------ v1.75.0 ageing-based provision (IFRS – NRV)
def provision_needed(on=None):
    """the provision the matrix asks for at the date: per item, the stock value in each age band × its %; expired lots 100 %"""
    from .models import LotRecord
    on = on or today()
    st = settings()
    matrix = sorted([(int(a), Decimal(str(b))) for a, b in (st.provision_matrix or []) if str(a).isdigit()], key=lambda x: x[0])
    if not matrix:
        return []
    ag = ageing(on, buckets=[m[0] for m in matrix])
    rows = []
    pct = [Decimal(0)] + [m[1] for m in matrix]
    expired = {(x.product_id, x.lot) for x in LotRecord.objects.filter(expiry__lt=on)}
    for r in ag['rows']:
        need = sum((Decimal(str(b['value'])) * pct[i] / 100 for i, b in enumerate(r['buckets'])), Z)
        exp_val = Z
        for (pid, lot) in expired:
            if pid == r['item_id']:
                v = valuation(on, group='lot', product=pid)
                exp_val += sum((Decimal(str(x['value'])) for x in v if x.get('lot') == lot), Z)
        need = max(need, exp_val) if exp_val else need
        if need:
            rows.append({'item_id': r['item_id'], 'item': r['item'], 'value': Decimal(str(r['value'])), 'provision': q2(need)})
    return rows


@transaction.atomic
def post_provision(on=None, user=None):
    """the monthly provision journal: Dr Inventory write-down / Cr Inventory provision for the change since the last one (a release the other way)"""
    on = on or today()
    st = settings()
    need = q2(sum((r['provision'] for r in provision_needed(on)), Z))
    have = -gl_balance(account('provision', st=st), on)
    ch = q2(need - have)
    if not ch:
        return None, need
    JB, JL = _jb()
    lab = f'Inventory provision at {on:%d/%m/%Y} (matrix): {need:,.2f}'
    b = JB.objects.create(source='inventory', source_id=f'PROV-{on:%Y%m%d}', source_label=lab[:200], date=on, document_date=on, reference=lab[:120], status='ready',
                          total_debit=abs(ch), total_credit=abs(ch), lines_count=2, idempotency_key=f'inv:prov:{on:%Y%m%d}:{need}',
                          created_by=user if (user and getattr(user, 'is_authenticated', False)) else None)
    b.number = f'INV-JE-{b.pk:05d}'
    b.save(update_fields=['number'])
    wd, pv = account('write_down', st=st), account('provision', st=st)
    JL.objects.create(batch=b, seq=1, account=wd, base_account=wd, label=lab[:255], debit=ch if ch > 0 else Z, credit=-ch if ch < 0 else Z)
    JL.objects.create(batch=b, seq=2, account=pv, base_account=pv, label=lab[:255], debit=-ch if ch < 0 else Z, credit=ch if ch > 0 else Z)
    return b, need


# ------------------------------------------------------------------ v1.75.0 KPIs and the integrity check
def kpis(date_from, date_to):
    """stock turnover, days of inventory, dead stock, stock accuracy (counts), fill rate"""
    days = max((date_to - date_from).days + 1, 1)
    cogs = -Decimal(StockMove.objects.filter(kind__in=('delivery', 'consumption', 'dropship_out'), date__gte=date_from, date__lte=date_to)
                    .aggregate(s=Sum('value'))['s'] or 0)   # v1.79.3 added up by the database
    v0 = sum((Decimal(str(r['value'])) for r in valuation(date_from - datetime.timedelta(days=1))), Z)
    val1 = valuation(date_to)
    v1 = sum((Decimal(str(r['value'])) for r in val1), Z)
    avg = (v0 + v1) / 2
    turn = (cogs / avg * Decimal(365) / days) if avg else Z
    moved = set(StockMove.objects.filter(kind__in=OUT_KINDS_USE, date__gt=date_to - datetime.timedelta(days=180), date__lte=date_to).values_list('product_id', flat=True))
    dead = sum((Decimal(str(r['value'])) for r in val1 if r['item_id'] not in moved and r['qty'] > 0), Z)
    cl = StockDocLine.objects.filter(doc__kind='count', doc__status='posted', doc__date__gte=date_from, doc__date__lte=date_to)
    n = cl.count()
    acc = (Decimal(cl.filter(qty=0).count()) / n * 100) if n else None
    dl = StockDocLine.objects.filter(doc__kind='delivery', doc__date__gte=date_from, doc__date__lte=date_to)
    fill = (Decimal(dl.filter(doc__status='posted').count()) / dl.count() * 100) if dl.count() else None
    return [{'kpi': 'Stock turnover (a year)', 'value': float(round(turn, 2)), 'note': f'cost of goods {q2(cogs):,.2f} ÷ average stock {q2(avg):,.2f}'},
            {'kpi': 'Days of inventory', 'value': float(round(Decimal(365) / turn, 1)) if turn else None, 'note': '365 ÷ turnover'},
            {'kpi': 'Dead stock value (no use in 180 days)', 'value': float(q2(dead)), 'note': f'{(dead / v1 * 100 if v1 else 0):.1f} % of the stock value'},
            {'kpi': 'Stock accuracy (count lines without a difference)', 'value': float(round(acc, 1)) if acc is not None else None, 'note': f'{n} count lines'},
            {'kpi': 'Fill rate (delivery lines posted)', 'value': float(round(fill, 1)) if fill is not None else None, 'note': f'{dl.count()} delivery lines'},
            {'kpi': 'Stock value at the end', 'value': float(q2(v1)), 'note': ''}]


def integrity(on=None):
    """recalculates and compares: every move's booked value, the layers against the stock, valuation = ledger = ageing, Inventory ↔ GL, journals
    not posted – a list of findings (empty = everything ties)"""
    on = on or today()
    out = []
    # v1.79.3 compared by the database (a million moves: one list of every move id and an object per move before)
    from django.db.models.functions import Coalesce, Round
    from django.db.models import DecimalField, Value
    dec = DecimalField(max_digits=24, decimal_places=6)
    bad = (StockMove.objects.exclude(costing_key__startswith='own')
           .annotate(b=Coalesce(Sum('values__amount', filter=Q(values__in_stock=True, values__kind__in=('direct', 'adjustment'))), Value(0), output_field=dec))
           .annotate(br=Round('b', 2), vr=Round('value', 2)).exclude(br=F('vr')).values_list('pk', 'value', 'b')[:500])
    for pk, v, b in bad:
        out.append({'check': 'Move value', 'what': f'move {pk}', 'expected': float(q2(v)), 'found': float(q2(b))})
    tot = _key_totals(on)
    lay = defaultdict(lambda: Z)
    for L in CostLayer.objects.all().values('costing_key', 'qty_left'):
        lay[L['costing_key']] += Decimal(L['qty_left'])
    for key, k in _key_totals(today()).items():
        if k['qty'] > 0 and q2(lay.get(key, Z)) != q2(k['qty']):
            out.append({'check': 'Cost layers', 'what': key, 'expected': float(k['qty']), 'found': float(lay.get(key, Z))})
    val = q2(sum((k['value'] for k in tot.values()), Z))
    # v1.79.3 the ledger's closing by its own rule (opening + every in-stock amount on its valuation date up to the cut-off), added up by the
    # database – building a million ledger rows only for this figure took minutes and gigabytes
    led = q2(StockValue.objects.filter(in_stock=True, valuation_date__lte=on).aggregate(s=Sum('amount'))['s'] or 0)
    ag = Decimal(str(ageing(on)['value']))
    for name, v in (('Stock ledger closing', led), ('Ageing total', ag)):
        if q2(v) != val:
            out.append({'check': name, 'what': 'all items', 'expected': float(val), 'found': float(q2(v))})
    for r in reconciliation(on)['rows']:
        if r['difference']:
            out.append({'check': 'Inventory ↔ GL', 'what': r['account'], 'expected': r['valuation'], 'found': r['gl'] + r['unposted']})
    pend = StockValue.objects.filter(gl='pending', valuation_date__lte=on).count()
    if pend and settings().posting_timing == 'immediate':
        out.append({'check': 'Journals', 'what': f'{pend} cost amounts not posted', 'expected': 0, 'found': pend})
    return out



# ------------------------------------------------------------------ v1.75.0 returns with inspection (RMA)
def _rma(doc, user=None):
    """after a customer return: lines to scrap are scrapped, lines to repair go to the repair (quarantine) warehouse"""
    from .app import PREFIX, app
    from .models import Reason
    scrap = [ln for ln in doc.lines.select_related('product') if ln.decision == 'scrap']
    repair = [ln for ln in doc.lines.select_related('product') if ln.decision == 'repair']
    made = []
    for kind, lines in (('scrap', scrap), ('transfer', repair)):
        if not lines:
            continue
        to = None
        if kind == 'transfer':
            to = Warehouse.objects.filter(code='REPAIR').first() or Warehouse.objects.create(code='REPAIR', name='Repair / quarantine', branch_id=doc.warehouse.branch_id)
        d = StockDoc.objects.create(kind=kind, date=doc.date, document_date=doc.date, warehouse=doc.warehouse, to_warehouse=to, return_of=None,
                                    reason=Reason.objects.filter(kind='scrap', is_active=True).first() if kind == 'scrap' else None,
                                    reference=f'Return inspection of {doc.number}'[:80], branch_id=doc.branch_id)
        d.number = app.next_number(f'doc_{kind}', PREFIX.get(kind, 'STK'))
        d.save(update_fields=['number'])
        for ln in lines:
            StockDocLine.objects.create(doc=d, product=ln.product, uom=ln.uom, qty=ln.qty, lot=ln.lot, serial=ln.serial, expiry=ln.expiry,
                                        note=f'{ln.get_condition_display() if ln.condition else ""} {ln.return_reason}'.strip()[:255])
        post(d, user)
        made.append(d)
    return made
