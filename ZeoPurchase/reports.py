"""v1.78.0 Purchase reports in the report centre: purchases by supplier, item and month, open orders to receive, received not billed,
bills that did not match, supplier performance, price history."""
from collections import defaultdict
from decimal import Decimal


def purchase_reports(a, page):
    from .models import Bill, Order, OrderLine
    codes = [a.code(r) for r, _ in a.roles]
    mgr = [a.code('pur_manager')]
    DONE = ('approved', 'sent', 'part_received', 'received', 'billed', 'closed')

    def f(x):
        return float(x or 0)

    def orders(run):
        qs = Order.objects.filter(status__in=DONE, date__gte=run.date_from, date__lte=run.date_to)
        b = getattr(run, 'branches', None)
        if b:
            from django.db.models import Q
            qs = qs.filter(Q(branch_id__in=b) | Q(branch__isnull=True))
        return qs

    def by(key, label):
        def fn(run):
            agg = defaultdict(lambda: {'orders': 0, 'net': Decimal(0), 'vat': Decimal(0), 'billed': Decimal(0)})
            for o in orders(run).select_related('supplier'):
                k = {'supplier': str(o.supplier), 'month': o.date.strftime('%Y-%m')}[key]
                agg[k]['orders'] += 1
                agg[k]['net'] += o.net
                agg[k]['vat'] += o.vat
                agg[k]['billed'] += o.billed
            rows = [{key: k, 'orders': v['orders'], 'net': f(v['net']), 'vat': f(v['vat']), 'billed': f(v['billed'])} for k, v in sorted(agg.items())]
            return [(key, label, 'text'), ('orders', 'Orders', 'number'), ('net', 'Purchases (net)', 'number'), ('vat', 'VAT', 'number'), ('billed', 'Billed', 'number')], rows
        return fn

    def by_item(run):
        agg = defaultdict(lambda: {'qty': Decimal(0), 'net': Decimal(0), 'low': None, 'high': None})
        for ln in OrderLine.objects.filter(order__in=orders(run)).select_related('product'):
            k = str(ln.product or ln.description)
            v = agg[k]
            v['qty'] += ln.qty
            v['net'] += ln.net
            v['low'] = ln.price if v['low'] is None else min(v['low'], ln.price)
            v['high'] = ln.price if v['high'] is None else max(v['high'], ln.price)
        rows = [{'item': k, 'qty': f(v['qty']), 'net': f(v['net']), 'avg': round(f(v['net']) / f(v['qty']), 4) if v['qty'] else None, 'low': f(v['low']), 'high': f(v['high'])}
                for k, v in sorted(agg.items(), key=lambda x: -x[1]['net'])]
        return ([('item', 'Item', 'text'), ('qty', 'Quantity', 'number'), ('net', 'Purchases (net)', 'number'), ('avg', 'Average price', 'number'), ('low', 'Lowest price', 'number'),
                 ('high', 'Highest price', 'number')], rows, {'no_totals': ('avg', 'low', 'high')})

    def to_receive(run):
        rows = []
        for ln in OrderLine.objects.filter(order__status__in=('approved', 'sent', 'part_received')).select_related('order__supplier', 'product'):
            left = ln.qty - ln.received
            if left > 0:
                rows.append({'order': ln.order.number, 'supplier': str(ln.order.supplier), 'item': ln.description, 'ordered': f(ln.qty), 'received': f(ln.received),
                             'to_receive': f(left), 'expected': ln.order.expected_date, '_url': f'{page}r/order/{ln.order_id}'})
        return ([('order', 'Order', 'text'), ('supplier', 'Supplier', 'text'), ('item', 'Item', 'text'), ('ordered', 'Ordered', 'number'), ('received', 'Received', 'number'),
                 ('to_receive', 'To receive', 'number'), ('expected', 'Expected', 'date')], rows)

    def grni(run):
        rows = []
        for o in Order.objects.filter(status__in=('approved', 'sent', 'part_received', 'received')).select_related('supplier'):
            amt = sum(((ln.received - ln.billed_qty - ln.returned) * ln.price * (1 - ln.discount / 100) for ln in o.lines.all() if ln.received > ln.billed_qty + ln.returned), Decimal(0))
            if amt > 0:
                rows.append({'order': o.number, 'supplier': str(o.supplier), 'received_not_billed': f(amt), '_url': f'{page}r/order/{o.pk}'})
        return [('order', 'Order', 'text'), ('supplier', 'Supplier', 'text'), ('received_not_billed', 'Received, not billed (net)', 'number')], rows

    def mismatches(run):
        rows = []
        for b in Bill.objects.filter(kind='bill', date__gte=run.date_from, date__lte=run.date_to).select_related('order__supplier'):
            for m in b.match or []:
                if not (m.get('qty_ok') and m.get('price_ok')):
                    rows.append({'bill': b.doc_number, 'supplier': str(b.order.supplier), 'order': b.order.number, 'item': m.get('item'), 'received': m.get('received'),
                                 'billed': m.get('bill_qty'), 'order_price': m.get('order_price'), 'bill_price': m.get('bill_price')})
        return ([('bill', 'Bill', 'text'), ('supplier', 'Supplier', 'text'), ('order', 'Order', 'text'), ('item', 'Item', 'text'), ('received', 'Received', 'number'),
                 ('billed', 'Billed', 'number'), ('order_price', 'Order price', 'number'), ('bill_price', 'Bill price', 'number')], rows, {'no_totals': ('order_price', 'bill_price')})

    def performance(run):
        from .services import supplier_performance
        rows = supplier_performance(run.date_from, run.date_to)
        return ([('supplier', 'Supplier', 'text'), ('spend', 'Purchases (net)', 'number'), ('receipts', 'Receipts', 'number'), ('on_time_pct', 'On time %', 'number'),
                 ('avg_lead_days', 'Average days to receive', 'number'), ('returned', 'Returned', 'number')], rows, {'no_totals': ('on_time_pct', 'avg_lead_days')})

    return {'pur-by-supplier': ('Purchases by supplier', 'Purchase', 'Orders, purchases, VAT and billed per supplier.', by('supplier', 'Supplier'), 'month', codes),
            'pur-by-item': ('Purchases by item', 'Purchase', 'Quantity, purchases and the lowest / highest price per item.', by_item, 'month', codes),
            'pur-by-month': ('Purchases by month', 'Purchase', 'Orders and purchases per month.', by('month', 'Month'), 'year', codes),
            'pur-to-receive': ('Open orders to receive', 'Purchase', 'Approved order lines not received yet.', to_receive, None, codes),
            'pur-grni': ('Received, not billed', 'Purchase', 'Orders with goods received and no supplier bill yet.', grni, None, codes),
            'pur-mismatch': ('Bills that did not match', 'Purchase', 'Bill lines accepted above the received quantity or the order price.', mismatches, 'month', mgr),
            'pur-performance': ('Supplier performance', 'Purchase', 'Spend, on-time receipts, days to receive and returns per supplier.', performance, 'month', codes)}
