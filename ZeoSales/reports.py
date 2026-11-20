"""v1.77.0 Sales reports in the report centre: sales by customer, item, salesperson and month (with margin), backorders, to invoice,
quotation conversion, price rules used, commissions."""
from collections import defaultdict
from decimal import Decimal


def sales_reports(a, page):
    from .models import Commission, Order, OrderLine
    codes = [a.code(r) for r, _ in a.roles]
    mgr = [a.code('sal_manager')]
    DONE = ('confirmed', 'part_delivered', 'delivered', 'invoiced', 'closed')

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
            agg = defaultdict(lambda: {'orders': 0, 'net': Decimal(0), 'cost': Decimal(0)})
            for o in orders(run).select_related('partner', 'salesperson'):
                k = {'customer': str(o.partner), 'salesperson': str(o.salesperson or '–'), 'month': o.date.strftime('%Y-%m')}[key]
                agg[k]['orders'] += 1
                agg[k]['net'] += o.net
                agg[k]['cost'] += o.cost
            rows = [{key: k, 'orders': v['orders'], 'net': f(v['net']), 'cost': f(v['cost']), 'margin': f(v['net'] - v['cost']),
                     'margin_pct': round(float((v['net'] - v['cost']) / v['net'] * 100), 1) if v['net'] else None} for k, v in sorted(agg.items())]
            return ([(key, label, 'text'), ('orders', 'Orders', 'number'), ('net', 'Sales (net)', 'number'), ('cost', 'Cost', 'number'), ('margin', 'Margin', 'number'),
                     ('margin_pct', 'Margin %', 'number')], rows, {'no_totals': ('margin_pct',)})
        return fn

    def by_item(run):
        agg = defaultdict(lambda: {'qty': Decimal(0), 'net': Decimal(0), 'cost': Decimal(0)})
        for ln in OrderLine.objects.filter(order__in=orders(run)).select_related('product'):
            k = str(ln.product or ln.description)
            agg[k]['qty'] += ln.qty
            agg[k]['net'] += ln.net
            agg[k]['cost'] += ln.qty * ln.unit_cost
        rows = [{'item': k, 'qty': f(v['qty']), 'net': f(v['net']), 'cost': f(v['cost']), 'margin': f(v['net'] - v['cost'])} for k, v in sorted(agg.items(), key=lambda x: -x[1]['net'])]
        return [('item', 'Item', 'text'), ('qty', 'Quantity', 'number'), ('net', 'Sales (net)', 'number'), ('cost', 'Cost', 'number'), ('margin', 'Margin', 'number')], rows

    def backorders(run):
        rows = []
        for ln in OrderLine.objects.filter(order__status__in=('confirmed', 'part_delivered')).select_related('order__partner', 'product'):
            left = ln.qty - ln.delivered
            if left > 0:
                rows.append({'order': ln.order.number, 'customer': str(ln.order.partner), 'item': ln.description, 'ordered': f(ln.qty), 'delivered': f(ln.delivered),
                             'to_deliver': f(left), 'promised': ln.order.delivery_date, '_url': f'{page}r/order/{ln.order_id}'})
        return ([('order', 'Order', 'text'), ('customer', 'Customer', 'text'), ('item', 'Item', 'text'), ('ordered', 'Ordered', 'number'), ('delivered', 'Delivered', 'number'),
                 ('to_deliver', 'To deliver', 'number'), ('promised', 'Promised', 'date')], rows)

    def to_invoice(run):
        rows = []
        for o in Order.objects.filter(status__in=('confirmed', 'part_delivered', 'delivered')).select_related('partner'):
            amt = sum(((ln.delivered - ln.invoiced_qty - ln.returned) * ln.price * (1 - ln.discount / 100) for ln in o.lines.all() if ln.delivered > ln.invoiced_qty + ln.returned), Decimal(0))
            if amt > 0:
                rows.append({'order': o.number, 'customer': str(o.partner), 'delivered_not_invoiced': f(amt), '_url': f'{page}r/order/{o.pk}'})
        return [('order', 'Order', 'text'), ('customer', 'Customer', 'text'), ('delivered_not_invoiced', 'Delivered, not invoiced (net)', 'number')], rows

    def conversion(run):
        qs = Order.objects.filter(date__gte=run.date_from, date__lte=run.date_to)
        agg = defaultdict(lambda: {'quotes': 0, 'won': 0, 'lost': 0, 'won_value': Decimal(0)})
        for o in qs.select_related('salesperson'):
            k = str(o.salesperson or '–')
            agg[k]['quotes'] += 1
            if o.status in DONE:
                agg[k]['won'] += 1
                agg[k]['won_value'] += o.net
            elif o.status in ('lost', 'cancelled'):
                agg[k]['lost'] += 1
        rows = [{'salesperson': k, 'quotations': v['quotes'], 'won': v['won'], 'lost': v['lost'], 'rate': round(v['won'] * 100 / v['quotes'], 1) if v['quotes'] else None,
                 'won_value': f(v['won_value'])} for k, v in sorted(agg.items())]
        return ([('salesperson', 'Salesperson', 'text'), ('quotations', 'Quotations', 'number'), ('won', 'Won', 'number'), ('lost', 'Lost', 'number'), ('rate', 'Won %', 'number'),
                 ('won_value', 'Won value', 'number')], rows, {'no_totals': ('rate',)})

    def commissions(run):
        rows = [{'salesperson': str(c.salesperson), 'order': c.order.number, 'date': c.date, 'base': f(c.base), 'percent': f(c.percent), 'amount': f(c.amount), 'status': c.get_status_display()}
                for c in Commission.objects.filter(date__gte=run.date_from, date__lte=run.date_to).select_related('salesperson', 'order')]
        return ([('salesperson', 'Salesperson', 'text'), ('order', 'Order', 'text'), ('date', 'Date', 'date'), ('base', 'Base', 'number'), ('percent', '%', 'number'),
                 ('amount', 'Commission', 'number'), ('status', 'Status', 'text')], rows, {'no_totals': ('percent',), 'filters': ['salesperson', 'status']})

    return {'sal-by-customer': ('Sales by customer', 'Sales', 'Orders, sales, cost and margin per customer.', by('customer', 'Customer'), 'month', codes),
            'sal-by-item': ('Sales by item', 'Sales', 'Quantity, sales, cost and margin per item.', by_item, 'month', codes),
            'sal-by-salesperson': ('Sales by salesperson', 'Sales', 'Orders, sales and margin per salesperson.', by('salesperson', 'Salesperson'), 'month', codes),
            'sal-by-month': ('Sales by month', 'Sales', 'Orders, sales and margin per month.', by('month', 'Month'), 'year', codes),
            'sal-backorders': ('Backorders', 'Sales', 'Confirmed order lines not delivered yet.', backorders, None, codes),
            'sal-to-invoice': ('Delivered, not invoiced', 'Sales', 'Orders with goods delivered and not invoiced yet.', to_invoice, None, codes),
            'sal-conversion': ('Quotation conversion', 'Sales', 'Quotations, won and lost per salesperson.', conversion, 'month', codes),
            'sal-commissions': ('Commissions', 'Sales', 'Commission per salesperson and invoice: earned, waiting, paid.', commissions, 'month', mgr)}
