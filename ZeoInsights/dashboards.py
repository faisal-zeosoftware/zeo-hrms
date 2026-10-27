"""v1.53.0 – the dashboard builder: cards made from reports (built-in or made with the report builder).

A card runs its report as the person looking at the dashboard (their rights and branches apply) for the card's period, and shows
  number – the number of rows, or the total of a number column;
  bar    – the rows counted (or a number column added up) by the values of a column, the 12 largest (the rest as "Other");
  line   – the same by month of a date column;
  table  – the first rows with chosen columns.
"""
import re
from datetime import date, timedelta

PERIODS = [('report', 'The report\'s own period'), ('this_month', 'This month'), ('last_month', 'Last month'), ('this_year', 'This year'),
           ('last_12', 'Last 12 months'), ('next_30', 'Next 30 days'), ('next_90', 'Next 90 days')]
VIEWS = ('number', 'bar', 'line', 'table')
SIZES = ('s', 'w', 'f')


def period_range(p, today=None):
    t = today or date.today()
    if p == 'this_month':
        nxt = (t.replace(day=28) + timedelta(days=4)).replace(day=1)
        return t.replace(day=1), nxt - timedelta(days=1)
    if p == 'last_month':
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if p == 'this_year':
        return t.replace(month=1, day=1), t.replace(month=12, day=31)
    if p == 'last_12':
        start = (t.replace(day=1) - timedelta(days=330)).replace(day=1)
        return start, t
    if p == 'next_30':
        return t, t + timedelta(days=30)
    if p == 'next_90':
        return t, t + timedelta(days=90)
    return None, None


def report_data(request, key, params):
    """Run a report of the report centre as the user of the request; (data, error)."""
    from rest_framework.test import APIRequestFactory, force_authenticate
    from django.db import connection
    from DashboardManagement.reports import ReportView
    q = {k: v for k, v in (params or {}).items() if v not in (None, '')}
    q['schema'] = getattr(connection, 'schema_name', '')
    rf = APIRequestFactory().get(f'/dashboard/api/reports/{key}/', q)
    rf.tenant = getattr(connection, 'tenant', None)
    for h in ('HTTP_X_LANGUAGE', 'HTTP_X_SELECTED_BRANCHES', 'HTTP_X_SELECTED_LOCATIONS'):
        if h in request.META:
            rf.META[h] = request.META[h]
    force_authenticate(rf, user=request.user)
    r = ReportView.as_view()(rf, key=key)
    if r.status_code != 200:
        d = getattr(r, 'data', {}) or {}
        return None, (d.get('detail') if isinstance(d, dict) else str(d)) or 'The report could not be run.'
    return r.data, ''


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def card_data(request, card):
    key = card.get('report') or ''
    a, b = period_range(card.get('period') or 'report')
    params = {'from': a.isoformat(), 'to': b.isoformat(), 'as_of': b.isoformat()} if a else {}
    data, err = report_data(request, key, params)
    out = {'id': card.get('id'), 'title': card.get('title') or '', 'view': card.get('view') or 'number', 'report': key, 'error': err,
           'size': card.get('size') or 's'}
    if data is None:
        return out
    cols = {c['key']: c for c in data['columns']}
    rows = data['rows']
    out.update({'report_title': data['title'], 'count': data['count'], 'period': data.get('period'), 'title': out['title'] or data['title']})
    value = card.get('value') or 'count'
    vcol = value.split(':', 1)[1] if value.startswith('sum:') else ''
    if vcol and vcol not in cols:
        vcol = ''
    out['value_label'] = f"Total {cols[vcol]['label']}" if vcol else 'Number'

    def measure(rs):
        return round(sum(_num(r.get(vcol)) for r in rs), 2) if vcol else len(rs)

    view = out['view']
    if view == 'number':
        out['value'] = measure(rows)
        return out
    if view == 'table':
        show = [c for c in (card.get('columns') or []) if c in cols] or [c['key'] for c in data['columns'][:5]]
        n = min(max(int(card.get('limit') or 8), 1), 50)
        out['columns'] = [{'key': k, 'label': cols[k]['label'], 'type': cols[k]['type']} for k in show]
        out['rows'] = [{k: r.get(k) for k in show} | {k: r[k] for k in ('_m', '_id') if k in r} for r in rows[:n]]
        out['more'] = max(len(rows) - n, 0)
        return out
    g = card.get('group') or ''
    if g not in cols:
        out['error'] = 'Choose the column to group by.'
        return out
    buckets = {}
    for r in rows:
        v = r.get(g)
        if view == 'line':
            m = re.match(r'^(\d{4})-(\d{2})', str(v or ''))
            if not m:
                continue
            k = f'{m.group(1)}-{m.group(2)}'
        else:
            k = '(empty)' if v in (None, '') else str(v)
        buckets.setdefault(k, []).append(r)
    pairs = [(k, measure(rs)) for k, rs in buckets.items()]
    if view == 'line':
        pairs.sort(key=lambda p: p[0])
        if a and b:   # every month of the period, also the empty ones
            want, cur = [], a.replace(day=1)
            while cur <= b and len(want) < 36:
                want.append(f'{cur.year}-{cur.month:02d}')
                cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            have = dict(pairs)
            pairs = [(k, have.get(k, 0)) for k in want]
    else:
        pairs.sort(key=lambda p: -p[1])
        if len(pairs) > 12:
            rest = round(sum(v for _, v in pairs[11:]), 2)
            pairs = pairs[:11] + [('Other', rest)]
    out['labels'] = [p[0] for p in pairs]
    out['values'] = [p[1] for p in pairs]
    out['group_label'] = cols[g]['label']
    out['value'] = measure(rows)
    return out
