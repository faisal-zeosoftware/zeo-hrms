"""v1.53.0 – the dashboard builder: cards made from reports (built-in or made with the report builder).

A card runs its report as the person looking at the dashboard (their rights and branches apply) for the card's period, and shows
  number – the number of rows, or the total of a number column;
  bar    – the rows counted (or a number column added up) by the values of a column, the 12 largest (the rest as "Other");
  line   – the same by month of a date column;
  table  – the first rows with chosen columns.
v1.73.4 (a more graphic look): column (vertical bars), donut (shares, the 6 largest + "Other"), area (a filled line by month) and
progress (the largest groups as bars with their share); a number card also gives the previous period and the change in %.
A card may carry a colour (PALETTE) and a Material icon name.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import re
from Translations.i18n import tr   # v1.62.0 ui
from datetime import date, timedelta

PERIODS = [('report', 'The report\'s own period'), ('this_month', 'This month'), ('last_month', 'Last month'), ('this_year', 'This year'),
           ('last_12', 'Last 12 months'), ('next_30', 'Next 30 days'), ('next_90', 'Next 90 days')]
VIEWS = ('number', 'bar', 'line', 'table', 'column', 'donut', 'area', 'progress')
THEMES = ('aurora', 'ocean', 'emerald', 'sunset', 'midnight')   # v1.73.5: the dashboard's look
PALETTE = ('violet', 'blue', 'teal', 'green', 'amber', 'rose', 'slate')
MONTHLY = ('line', 'area')
SIZES = ('s', 'w', 'f')


def period_range(p, today=None):
    t = today or _zt_today()
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
    for h in ('HTTP_X_LANGUAGE', 'HTTP_X_SELECTED_BRANCHES', 'HTTP_X_SELECTED_LOCATIONS', 'HTTP_X_BRANCH_IDS'):   # v1.62.0 ui X-Branch-Ids
        if h in request.META:
            rf.META[h] = request.META[h]
    force_authenticate(rf, user=request.user)
    r = ReportView.as_view(throttle_classes=[])(rf, key=key)   # v1.73.6 the card's own request was already counted
    if r.status_code != 200:
        d = getattr(r, 'data', {}) or {}
        return None, (d.get('detail') if isinstance(d, dict) else str(d)) or 'The report could not be run.'
    return r.data, ''


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def card_data(request, card, rng=None, drill=None):
    """rng = (from, to) of the dashboard's date filter (v1.73.5) – it replaces the card's own period.
    drill = the label of a bar / slice / row (or '*' for every row): the rows behind it instead of the figures (v1.73.5)."""
    key = card.get('report') or ''
    a, b = rng if rng else period_range(card.get('period') or 'report')
    params = {'from': a.isoformat(), 'to': b.isoformat(), 'as_of': b.isoformat()} if a else {}
    data, err = report_data(request, key, params)
    out = {'id': card.get('id'), 'title': card.get('title') or '', 'view': card.get('view') or 'number', 'report': key, 'error': err,
           'size': card.get('size') or 's', 'color': card.get('color') or '', 'icon': card.get('icon') or ''}
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
    if drill is not None and (drill == '*' or view in ('number', 'table')):
        return _drill(out, data, rows, measure, None, None)
    if view == 'number':
        out['value'] = measure(rows)
        # v1.62.0 ui: early in a month the report's own period (this month) is still empty – the card showed 0. Then it shows
        # the last complete month, and says so.
        _trend(request, key, card, out, measure, bool(rng))   # v1.73.4
        if not out['value'] and not rng and (card.get('period') or 'report') == 'report':
            la, lb = period_range('last_month')
            d2, e2 = report_data(request, key, {'from': la.isoformat(), 'to': lb.isoformat(), 'as_of': lb.isoformat()})
            if d2 is not None and d2.get('rows'):
                rows = d2['rows']
                v2 = measure(rows)
                if v2:
                    out.update({'value': v2, 'count': d2['count'], 'period': d2.get('period'), 'period_note': tr('Last complete month')})
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
        if view in MONTHLY:
            m = re.match(r'^(\d{4})-(\d{2})', str(v or ''))
            if not m:
                continue
            k = f'{m.group(1)}-{m.group(2)}'
        else:
            k = tr('Not set') if v in (None, '') else str(v)   # v1.73.4: was "(empty)"
        buckets.setdefault(k, []).append(r)
    pairs = [(k, measure(rs)) for k, rs in buckets.items()]
    if view in MONTHLY:
        pairs.sort(key=lambda p: p[0])
        if a and b:   # every month of the period, also the empty ones
            want, cur = [], a.replace(day=1)
            while cur <= b and len(want) < 36:
                want.append(f'{cur.year}-{cur.month:02d}')
                cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
            have = dict(pairs)
            pairs = [(k, have.get(k, 0)) for k in want]
    elif pairs and all(_month_key(k) for k, _ in pairs):
        # v1.62.0 ui: months ("May 2026", "2026-10", "10/2026") stay in calendar order – the biggest-first order put Oct after May
        pairs.sort(key=lambda p: _month_key(p[0]))
    else:
        pairs.sort(key=lambda p: -p[1])
        top = {'donut': 6, 'progress': 7}.get(view, 12)   # v1.73.4: a donut reads best with few slices
        if len(pairs) > top:
            rest = round(sum(v for _, v in pairs[top - 1:]), 2)
            for k2, _ in pairs[top - 1:]:   # v1.73.5: "Other" can be drilled too
                buckets.setdefault(tr('Other'), []).extend(buckets.pop(k2, []))
            pairs = pairs[:top - 1] + [(tr('Other'), rest)]
    out['labels'] = [p[0] for p in pairs]
    out['values'] = [p[1] for p in pairs]
    out['group_label'] = cols[g]['label']
    out['value'] = measure(rows)
    if drill is not None:
        return _drill(out, data, buckets.get(drill, []), measure, cols[g]['label'], drill)
    return out


def _drill(out, data, rows, measure, group_label, label):
    """v1.73.5: the rows behind a KPI, a bar, a slice or a list row – shown in the dashboard's side panel, each with its record link."""
    cols = data['columns'][:9]
    keys = [c['key'] for c in cols]
    return {'id': out['id'], 'title': out['title'], 'report': out['report'], 'report_title': out.get('report_title'), 'period': out.get('period'),
            'group_label': group_label, 'label': label, 'count': len(rows), 'value': measure(rows), 'value_label': out.get('value_label'),
            'columns': [{'key': c['key'], 'label': c['label'], 'type': c['type']} for c in cols],
            'rows': [{k: r.get(k) for k in keys} | {k: r[k] for k in ('_m', '_id', '_url') if k in r} for r in rows[:300]],
            'more': max(len(rows) - 300, 0)}


_MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def _month_key(k):
    """v1.62.0 ui: (year, month) of a month label, or None."""
    t = str(k or '').strip().lower()
    m = re.match(r'^([a-z]{3})[a-z]*\.?\s+(\d{4})$', t)
    if m and m.group(1) in _MON:
        return (int(m.group(2)), _MON[m.group(1)])
    m = re.match(r'^(\d{4})-(\d{1,2})$', t) or None
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m = re.match(r'^(\d{1,2})/(\d{4})$', t)
    if m:
        return (int(m.group(2)), int(m.group(1)))
    return None


def _trend(request, key, card, out, measure, filtered=False):
    """v1.73.4: the same number for the period before (same length), and the change in % – for the arrow on a number card."""
    if not filtered and (card.get('period') or 'report') == 'report':   # only for a period chosen on the card or the dashboard filter
        return
    per = out.get('period') or {}
    try:
        a, b = date.fromisoformat(str(per.get('from'))[:10]), date.fromisoformat(str(per.get('to'))[:10])
    except (TypeError, ValueError):
        return
    if b < a or (b - a).days > 400:
        return
    if a.day == 1 and (b + timedelta(days=1)).day == 1:   # whole months → the same number of months before
        months = (b.year - a.year) * 12 + b.month - a.month + 1
        pb = a - timedelta(days=1)
        pa = pb.replace(day=1)
        for _ in range(months - 1):
            pa = (pa - timedelta(days=1)).replace(day=1)
    else:
        pb = a - timedelta(days=1)
        pa = pb - (b - a)
    d, e = report_data(request, key, {'from': pa.isoformat(), 'to': pb.isoformat(), 'as_of': pb.isoformat()})
    if d is None:
        return
    prev = measure(d.get('rows') or [])
    out['prev'] = prev
    out['prev_period'] = {'from': pa.isoformat(), 'to': pb.isoformat()}
    out['delta'] = round((out['value'] - prev) * 100.0 / abs(prev), 1) if prev else None
