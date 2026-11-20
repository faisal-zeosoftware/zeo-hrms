"""v1.79.0 Manufacturing reports in the report centre: production by item, cost of orders (planned and actual), variances, work in
progress, work centre load, material usage."""
from collections import defaultdict
from decimal import Decimal


def mfg_reports(a, page):
    from .models import MaterialLine, Movement, OperationLine, ProductionOrder
    from . import services as S
    codes = [a.code(r) for r, _ in a.roles]
    mgr = [a.code('mfg_manager')]

    def f(x):
        return float(x or 0)

    def by_item(run):
        agg = defaultdict(lambda: {'orders': set(), 'made': Decimal(0), 'scrap': Decimal(0), 'value': Decimal(0)})
        for m in Movement.objects.filter(kind__in=('output', 'scrap'), date__gte=run.date_from, date__lte=run.date_to).select_related('order__product'):
            v = agg[str(m.order.product)]
            v['orders'].add(m.order_id)
            if m.kind == 'output':
                v['made'] += m.qty
                v['value'] += m.value
            else:
                v['scrap'] += m.qty
        rows = [{'item': k, 'orders': len(v['orders']), 'made': f(v['made']), 'scrap': f(v['scrap']), 'value': f(v['value']),
                 'scrap_pct': round(f(v['scrap']) * 100 / (f(v['made']) + f(v['scrap'])), 1) if v['made'] + v['scrap'] else None} for k, v in sorted(agg.items())]
        return ([('item', 'Item', 'text'), ('orders', 'Orders', 'number'), ('made', 'Made', 'number'), ('scrap', 'Scrapped', 'number'), ('scrap_pct', 'Scrap %', 'number'),
                 ('value', 'To stock', 'number')], rows, {'no_totals': ('scrap_pct',)})

    def costs(run):
        rows = []
        for o in ProductionOrder.objects.filter(status__in=('done', 'closed', 'in_progress'), start_date__gte=run.date_from, start_date__lte=run.date_to).select_related('product'):
            act = o.material_cost + o.labour_cost + o.overhead_cost
            rows.append({'order': o.number, 'item': str(o.product), 'made': f(o.done_qty), 'planned_unit': f(o.unit_cost), 'materials': f(o.material_cost),
                         'labour': f(o.labour_cost), 'overhead': f(o.overhead_cost), 'actual_unit': round(f(act) / f(o.done_qty), 4) if o.done_qty else None,
                         'variance': f(o.variance), 'status': o.get_status_display(), '_url': f'{page}r/order/{o.pk}'})
        return ([('order', 'Order', 'text'), ('item', 'Item', 'text'), ('made', 'Made', 'number'), ('planned_unit', 'Planned per unit', 'number'), ('materials', 'Materials', 'number'),
                 ('labour', 'Labour and machine', 'number'), ('overhead', 'Overhead', 'number'), ('actual_unit', 'Actual per unit', 'number'), ('variance', 'Variance', 'number'),
                 ('status', 'Status', 'text')], rows, {'no_totals': ('planned_unit', 'actual_unit'), 'filters': ['status']})

    def variances(run):
        rows = []
        for m in Movement.objects.filter(kind='close', date__gte=run.date_from, date__lte=run.date_to).select_related('order__product'):
            o = m.order
            rows.append({'order': o.number, 'item': str(o.product), 'date': m.date, 'made': f(o.done_qty), 'scrapped': f(o.scrap_qty), 'variance': f(m.value), 'entry': m.document})
        return ([('order', 'Order', 'text'), ('item', 'Item', 'text'), ('date', 'Closed', 'date'), ('made', 'Made', 'number'), ('scrapped', 'Scrapped', 'number'),
                 ('variance', 'Variance (+ unfavourable)', 'number'), ('entry', 'Entry', 'text')], rows)

    def wip(run):
        rows = [{'order': o.number, 'item': str(o.product), 'status': o.get_status_display(), 'materials': f(o.material_cost), 'labour': f(o.labour_cost + o.overhead_cost),
                 'to_stock': f(o.output_value), 'in_wip': f(S.wip(o)), '_url': f'{page}r/order/{o.pk}'}
                for o in ProductionOrder.objects.filter(status__in=('released', 'in_progress', 'done')).select_related('product')]
        return ([('order', 'Order', 'text'), ('item', 'Item', 'text'), ('status', 'Status', 'text'), ('materials', 'Materials', 'number'), ('labour', 'Labour and overhead', 'number'),
                 ('to_stock', 'To stock', 'number'), ('in_wip', 'In work in progress', 'number')], rows)

    def load(run):
        agg = defaultdict(lambda: {'open': Decimal(0), 'done': Decimal(0), 'hpd': Decimal(8)})
        for op in OperationLine.objects.filter(order__status__in=('released', 'in_progress', 'done', 'closed')).select_related('work_centre'):
            v = agg[str(op.work_centre)]
            v['hpd'] = op.work_centre.hours_per_day or Decimal(8)
            if op.done:
                v['done'] += op.actual_min / 60
            else:
                v['open'] += max(Decimal(0), op.planned_min - op.actual_min) / 60
        rows = [{'work_centre': k, 'open_hours': round(f(v['open']), 1), 'days': round(f(v['open']) / f(v['hpd']), 1), 'hours_done': round(f(v['done']), 1)} for k, v in sorted(agg.items())]
        return [('work_centre', 'Work centre', 'text'), ('open_hours', 'Open hours', 'number'), ('days', 'Days of work', 'number'), ('hours_done', 'Hours reported', 'number')], rows

    def usage(run):
        agg = defaultdict(lambda: {'planned': Decimal(0), 'issued': Decimal(0), 'value': Decimal(0)})
        for m in MaterialLine.objects.filter(order__start_date__gte=run.date_from, order__start_date__lte=run.date_to, order__status__in=('done', 'closed')).select_related('component'):
            v = agg[str(m.component)]
            v['planned'] += m.planned
            v['issued'] += m.issued
            v['value'] += m.value
        rows = [{'component': k, 'planned': f(v['planned']), 'issued': f(v['issued']), 'difference': f(v['issued'] - v['planned']), 'value': f(v['value'])} for k, v in sorted(agg.items())]
        return [('component', 'Component', 'text'), ('planned', 'Planned', 'number'), ('issued', 'Used', 'number'), ('difference', 'Difference', 'number'), ('value', 'Value', 'number')], rows

    return {'mfg-by-item': ('Production by item', 'Manufacturing', 'Made, scrapped and the value to stock per item.', by_item, 'month', codes),
            'mfg-costs': ('Cost of production orders', 'Manufacturing', 'Planned and actual cost per unit: materials, labour, overhead, variance.', costs, 'month', codes),
            'mfg-variances': ('Production variances', 'Manufacturing', 'The variance of each closed order and its entry.', variances, 'month', mgr),
            'mfg-wip': ('Work in progress', 'Manufacturing', 'What is in work in progress for each open order.', wip, None, codes),
            'mfg-load': ('Work centre load', 'Manufacturing', 'Open hours and days of work per work centre.', load, None, codes),
            'mfg-usage': ('Material usage', 'Manufacturing', 'Planned against used components of the finished orders.', usage, 'month', codes)}
