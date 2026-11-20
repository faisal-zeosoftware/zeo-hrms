"""Creating the reviewed document in its app – with the app's own defaults, numbering, prices, VAT and rules.
Every target is created as a draft / quotation the user then works on; a customer PO is confirmed as a sales order through the
Sales app's own confirmation (approvals and credit holds still apply)."""
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction

TARGETS = {'sales_quote': ('quote',), 'sales_order': ('order', 'quote'), 'purchase_quote': ('po', 'rfq_quote'),
           'purchase_request': ('request', 'rfq', 'po'), 'price_list': ('price_list',)}


class CaptureError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def must(c, msg, field=None):
    if not c:
        raise CaptureError(msg, field)


def dec(v, default='0'):
    try:
        return Decimal(str(v if v not in (None, '') else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def ddate(v):
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def rights(request, kind, target):
    """(app, allowed) – the user needs the app's create right for the target"""
    if kind in ('sales_quote', 'sales_order'):
        from ZeoSales.app import USERS, app
        return app, app.activated() and app.any(request, *USERS)
    from ZeoPurchase.app import BUY, MGR, REQ, app
    if target == 'price_list':   # master data: the Purchase manager, with Supplier prices switched on
        from .pricelist import feature_on
        return app, app.activated() and app.any(request, *MGR) and feature_on()
    return app, app.activated() and app.any(request, *(REQ if target == 'request' else BUY))


def may_update_prices(request):
    """the prices of a supplier's quote may go to the price list – as when a buyer awards an RFQ"""
    from ZeoPurchase.app import BUY, app

    from .pricelist import feature_on
    return app.activated() and app.any(request, *BUY) and feature_on()


def partner_of(request, kind, payload):
    from ZeoMasters.models import Partner
    pid = payload.get('partner_id')
    if pid:
        p = Partner.objects.filter(pk=int(pid)).first()
        must(p is not None, 'Choose the customer / supplier.', 'partner')
        must(getattr(p, 'status', 'active') != 'blocked', f'{p} is blocked.', 'partner')
        return p
    np_ = payload.get('new_partner') or {}
    if np_.get('name'):
        from ZeoMasters.partners import Partner as PM, next_number, save_partner, set_roles
        P = PM()
        p = P(number=next_number(), kind='company', name=str(np_['name'])[:200], email=str(np_.get('email') or '')[:254],
              phone=str(np_.get('phone') or '')[:30], tax_id=str(np_.get('trn') or '')[:40])
        set_roles(p, ['customer' if kind.startswith('sales') else 'supplier'])
        save_partner(p)
        return p
    return None


def new_item(request, kind, l):
    """v1.79.5 an item that is not in ZEO yet, added from the line (Masters › Items numbering and defaults)"""
    from ZeoMasters.app import item_defaults, item_saver
    from ZeoMasters.models import Product
    ni = l.get('new_item') or {}
    name = str(ni.get('name') or l.get('description') or '').strip()[:150]
    must(name, 'Give the new item a name.', 'lines')
    code = str(ni.get('code') or l.get('code') or '').strip()[:40]
    if code:
        dup = Product.objects.filter(code__iexact=code).first()
        must(dup is None, f'An item with the code {code} exists already: {dup} – choose it instead.', 'lines')
    price = dec(ni.get('price') if ni.get('price') not in (None, '') else l.get('price'))
    p = Product(name=name, code=code, kind='goods', is_sold=True, is_purchased=True,
                sale_price=price if kind.startswith('sales') else 0, purchase_price=price if not kind.startswith('sales') and price else None,
                cost_price=price if not kind.startswith('sales') else 0)
    item_defaults(p, request)
    item_saver(p, request, True, None)
    return p.pk


def attach_photo(request, job, n, pid):
    """v1.79.5 the photo the user matched with becomes a picture of the item (Masters › Items › Files)"""
    ph = ((job.extracted or {}).get('_photos') or {}).get(str(n))
    if not ph or not pid:
        return False
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from ZeoMasters.models import MasterFile
    with default_storage.open(ph['path'], 'rb') as fh:
        data = fh.read()
    mf = MasterFile(model='item', object_id=pid, name=ph['name'], size=len(data), note='Photo from document capture', uploaded_by=request.user)
    mf.file.save(ph['name'], ContentFile(data), save=False)
    mf.save()
    return True


def _lines(payload, need_product=False, request=None, kind='', job=None):
    out = []
    for i, l in enumerate(payload.get('lines') or []):
        if l.get('skip'):
            continue
        if l.get('new_item') and not l.get('product') and request is not None:
            l['product'] = new_item(request, kind, l)
            l['changed'] = True
        if l.get('save_photo') and l.get('product') and job is not None:
            attach_photo(request, job, l.get('n'), int(l['product']))
        qty = dec(l.get('qty'), '1')
        must(qty > 0, f'Line {i + 1}: the quantity must be more than 0.', 'lines')
        pid = int(l['product']) if l.get('product') else None
        must(pid or (not need_product and (l.get('description') or '').strip()), f'Line {i + 1}: choose the item.', 'lines')
        out.append({'product': pid, 'description': str(l.get('description') or '')[:255], 'qty': qty, 'code': str(l.get('code') or '')[:60],
                    'min_qty': dec(l.get('min_qty')), 'price': dec(l.get('price')) if l.get('price') not in (None, '') else None, 'discount': dec(l.get('discount_pct'))})
    must(out, 'There are no lines to create.', 'lines')
    return out


@transaction.atomic
def create(request, job, payload):
    kind = job.kind
    target = payload.get('target') or TARGETS[kind][0]
    must(target in TARGETS[kind], 'This document cannot be created from it.')
    app, ok = rights(request, kind, target)
    must(ok, f'You may not create this in {app.title} (or {app.title} is not activated).')
    p = partner_of(request, kind, payload)
    if kind.startswith('sales'):
        return _sales(request, app, job, payload, p, target)
    return _purchase(request, app, job, payload, p, target)


# ------------------------------------------------------------------ Sales
def _sales(request, app, job, payload, partner, target):
    from ZeoMasters.models import Product
    from ZeoSales import services as S
    from ZeoSales.app import order_defaults, order_saver
    from ZeoSales.models import Order, OrderLine
    must(partner is not None, 'Choose the customer (or add it as a new customer).', 'partner')
    lines = _lines(payload, request=request, kind=job.kind, job=job)
    o = Order(partner=partner, date=ddate(payload.get('date')) or None, valid_until=ddate(payload.get('valid_until')),
              delivery_date=ddate(payload.get('delivery_date')), reference=str(payload.get('reference') or '')[:60],
              notes=str(payload.get('notes') or '')[:2000])
    cur = str(payload.get('currency') or '').upper()[:3]
    order_defaults(o, request)
    if cur and cur != (S.settings().currency or 'AED').upper():
        o.currency = cur
    o = order_saver(o, request, True, None)
    for l in lines:
        ln = OrderLine(order=o, product=Product.objects.get(pk=l['product']) if l['product'] else None, description=l['description'], qty=l['qty'],
                       price=l['price'] if l['price'] is not None else 0, discount=l['discount'])
        S.price_line(ln)
        ln.save()
    S.totals(o)
    app.event('order', o.pk, f'Made from {job.source_name or "a document"} (document capture)', request.user)
    msg = f'Quotation {o.quote_number} created ({o.total:,.2f}).'
    if target == 'order':
        from ZeoSales.app import MGR
        try:
            S.confirm(o, request.user, manager=app.any(request, *MGR))
        except Exception as e:   # noqa: BLE001
            msg += f' Not confirmed: {getattr(e, "message", str(e))}'
        o.refresh_from_db()
        if o.status == 'confirmed':
            msg = f'Sales order {o.number} created and confirmed ({o.total:,.2f}) for customer PO {o.reference or "–"}.'
        elif o.status in ('approval', 'hold'):
            msg = f'Quotation {o.quote_number} created – {o.get_status_display().lower()}: {o.hold_reason}'
    return {'target': 'sales.order', 'id': o.pk, 'number': o.number or o.quote_number, 'link': f'/main-sidebar/zeo-sales/r/order/{o.pk}', 'message': msg}


# ------------------------------------------------------------------ Purchase
def _purchase(request, app, job, payload, partner, target):
    from django.utils import timezone

    from ZeoMasters.models import Product
    from ZeoPurchase import services as S
    from ZeoPurchase.app import order_defaults, order_saver
    from ZeoPurchase.models import Order, OrderLine, Quote, Request, Rfq
    today = timezone.localdate()
    if target == 'request':
        lines = _lines(payload, need_product=True, request=request, kind=job.kind, job=job)
        r = Request(date=today, needed_by=ddate(payload.get('delivery_date')), requested_by=request.user, note=str(payload.get('notes') or '')[:2000],
                    department=str(payload.get('department') or '')[:120],
                    lines=[{'product': l['product'], 'qty': str(l['qty']), 'note': l['description'][:120]} for l in lines], warehouse=S.settings().default_warehouse)
        r.number = app.next_number('request', 'PR')
        r.save()
        app.event('request', r.pk, f'Made from {job.source_name or "a document"} (document capture)', request.user)
        return {'target': 'purchase.request', 'id': r.pk, 'number': r.number, 'link': f'/main-sidebar/zeo-purchase/r/request/{r.pk}',
                'message': f'Purchase request {r.number} created with {len(lines)} item(s) – submit it for approval.'}
    if target == 'rfq':
        lines = _lines(payload, need_product=True, request=request, kind=job.kind, job=job)
        q = Rfq(date=today, reply_by=ddate(payload.get('reply_by')) or today + __import__('datetime').timedelta(days=7), note=str(payload.get('notes') or '')[:2000],
                lines=[{'product': l['product'], 'qty': str(l['qty'])} for l in lines])
        q.number = app.next_number('rfq', 'RFQ')
        q.save()
        sups = [int(x) for x in payload.get('suppliers') or [] if str(x).isdigit()] or ([partner.pk] if partner else [])
        if not sups:
            ids = set(S.SupplierPrice.objects.filter(product_id__in=[l['product'] for l in lines]).values_list('supplier_id', flat=True))
            ids |= {pp.preferred_supplier_id for pp in Product.objects.filter(pk__in=[l['product'] for l in lines]) if pp.preferred_supplier_id}
            sups = list(ids)
        q.suppliers.set(sups)
        app.event('rfq', q.pk, f'Made from {job.source_name or "a document"} (document capture)', request.user)
        return {'target': 'purchase.rfq', 'id': q.pk, 'number': q.number, 'link': f'/main-sidebar/zeo-purchase/r/rfq/{q.pk}',
                'message': f'RFQ {q.number} created for {len(lines)} item(s), {len(sups)} supplier(s).'}
    must(partner is not None, 'Choose the supplier (or add it as a new supplier).', 'partner')
    upd = bool(payload.get('update_prices')) and may_update_prices(request)
    vfrom, vto, lead = ddate(payload.get('valid_from')) or ddate(payload.get('date')), ddate(payload.get('valid_until')), payload.get('lead_days')
    if target == 'price_list':
        from .pricelist import save_lines, summary
        for l in payload.get('lines') or []:
            if not l.get('skip') and l.get('qty') in (None, '', 0, '0'):
                l['qty'] = 1   # a price list has no quantity: the "from quantity" is kept apart
        lines = [l for l in _lines(payload, need_product=True, request=request, kind=job.kind, job=job) if l['price'] is not None]
        must(lines, 'No line has both an item and a price.', 'lines')
        r = save_lines(partner, lines, currency=str(payload.get('currency') or ''), lead_days=int(lead) if lead not in (None, '') else None,
                       valid_from=vfrom, valid_to=vto)
        app.event('supplier_price', 0, f'Price list of {partner} from {job.source_name or "a document"}: {summary(r)}', request.user)
        return {'target': 'purchase.supplier_price', 'id': partner.pk, 'number': partner.name[:40], 'prices': r,
                'link': f'/main-sidebar/zeo-purchase/r/supplier_price?supplier={partner.pk}',
                'message': f"Price list of {partner} updated – {r['new']} new, {r['changed']} changed, {r['same']} unchanged."}
    if target == 'rfq_quote':
        rfq = Rfq.objects.filter(pk=int(payload.get('rfq_id') or 0)).first()
        must(rfq is not None, 'Choose the RFQ this quote answers.', 'rfq')
        must(rfq.status not in ('awarded', 'cancelled', 'closed'), f'{rfq} is {rfq.get_status_display().lower()}.', 'rfq')
        lines = _lines(payload, need_product=True, request=request, kind=job.kind, job=job)
        by_p = {}
        for l in lines:
            by_p.setdefault(l['product'], l)
        prices = []
        for x in rfq.lines or []:
            l = by_p.get(int(x['product']))
            prices.append({'product': int(x['product']), 'price': str(l['price'] * (1 - l['discount'] / 100)) if l and l['price'] is not None else ''})
        must(any(x['price'] for x in prices), 'None of the RFQ items is in the quote – check the matches.', 'lines')
        qt = Quote.objects.create(rfq=rfq, supplier=partner, reference=str(payload.get('reference') or '')[:60], prices=prices,
                                  currency=str(payload.get('currency') or '')[:3].upper(), lead_days=int(payload.get('lead_days') or 0),
                                  valid_until=ddate(payload.get('valid_until')), note=f'From {job.source_name or "a document"}'[:255])
        S.quote_total(qt)
        if partner.pk not in set(rfq.suppliers.values_list('pk', flat=True)):
            rfq.suppliers.add(partner)
        app.event('rfq', rfq.pk, f'Quote of {partner} recorded from {job.source_name or "a document"} (document capture)', request.user)
        missing = sum(1 for x in prices if not x['price'])
        pl = ''
        if upd:
            from .pricelist import save_lines, summary
            pl = ' ' + summary(save_lines(partner, lines, currency=qt.currency, lead_days=qt.lead_days or None, valid_from=vfrom, valid_to=vto)).capitalize() + '.'
        return {'target': 'purchase.quote', 'id': qt.pk, 'number': rfq.number, 'link': f'/main-sidebar/zeo-purchase/r/rfq/{rfq.pk}',
                'message': f'Quote of {partner} recorded on {rfq.number}: {qt.total:,.2f}' + (f' – {missing} RFQ line(s) not quoted.' if missing else '.') + pl}
    lines = _lines(payload, request=request, kind=job.kind, job=job)
    o = Order(supplier=partner, date=today, expected_date=ddate(payload.get('delivery_date')), supplier_ref=str(payload.get('reference') or '')[:60],
              notes=str(payload.get('notes') or '')[:2000], buyer=request.user)
    order_defaults(o, request)
    cur = str(payload.get('currency') or '').upper()[:3]
    if cur and cur != (S.settings().currency or 'AED').upper():
        o.currency = cur
    if not o.expected_date and payload.get('lead_days'):
        o.expected_date = today + __import__('datetime').timedelta(days=int(payload['lead_days']))
    o = order_saver(o, request, True, None)
    for l in lines:
        ln = OrderLine(order=o, product=Product.objects.get(pk=l['product']) if l['product'] else None, description=l['description'], qty=l['qty'],
                       price=l['price'] if l['price'] is not None else 0, discount=l['discount'])
        S.price_line(ln)
        ln.save()
    S.totals(o)
    app.event('order', o.pk, f'Made from {job.source_name or "a document"} (document capture)', request.user)
    pl = ''
    if upd and job.kind == 'purchase_quote':
        from .pricelist import save_lines, summary
        pl = ' ' + summary(save_lines(partner, [l for l in lines if l['product']], currency=o.currency, lead_days=int(lead) if lead not in (None, '') else None,
                                      valid_from=vfrom, valid_to=vto)).capitalize() + '.'
    return {'target': 'purchase.order', 'id': o.pk, 'number': o.number, 'link': f'/main-sidebar/zeo-purchase/r/order/{o.pk}',
            'message': f'Purchase order {o.number} created as a draft ({o.total:,.2f}) – check it and submit it for approval.' + pl}
