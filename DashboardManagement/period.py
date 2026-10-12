"""v1.16.0 – dashboard periods: one picker for the whole dashboard plus an override per card.

Query parameters every dashboard endpoint accepts:
    period=<preset>              today, yesterday, this_week, last_7, this_month, last_30, this_quarter, this_year
    from=YYYY-MM-DD&to=YYYY-MM-DD a custom period (wins over `period`)
"""
from datetime import date, timedelta

from django.utils.dateparse import parse_date

PRESETS = ('today', 'yesterday', 'this_week', 'last_7', 'this_month', 'last_30', 'this_quarter', 'this_year')
LABELS = {'today': 'Today', 'yesterday': 'Yesterday', 'this_week': 'This week', 'last_7': 'Last 7 days', 'this_month': 'This month',
          'last_30': 'Last 30 days', 'this_quarter': 'This quarter', 'this_year': 'This year', 'custom': 'Custom'}


def preset_range(preset, today):
    if preset == 'today':
        return today, today
    if preset == 'yesterday':
        y = today - timedelta(days=1)
        return y, y
    if preset == 'this_week':   # Monday – Sunday (UAE working week since 2022)
        a = today - timedelta(days=today.weekday())
        return a, a + timedelta(days=6)
    if preset == 'last_7':
        return today - timedelta(days=6), today
    if preset == 'last_30':
        return today - timedelta(days=29), today
    if preset == 'this_quarter':
        m = (today.month - 1) // 3 * 3 + 1
        a = date(today.year, m, 1)
        b = date(today.year + (1 if m == 10 else 0), 1 if m == 10 else m + 3, 1) - timedelta(days=1)
        return a, b
    if preset == 'this_year':
        return date(today.year, 1, 1), date(today.year, 12, 31)
    a = today.replace(day=1)   # this_month
    return a, (a + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def _d(v):
    try:
        return parse_date(str(v)) if v else None
    except ValueError:
        return None


def resolve(params, today, default='this_month'):
    """(from, to, preset) from the request parameters."""
    a, b = _d(params.get('from')), _d(params.get('to'))
    if a and b:
        if b < a:
            a, b = b, a
        if (b - a).days > 3700:
            b = a + timedelta(days=3700)
        hit = next((p for p in PRESETS if preset_range(p, today) == (a, b)), 'custom')
        return a, b, hit
    p = params.get('period') or default
    if p not in PRESETS:
        p = default
    a, b = preset_range(p, today)
    return a, b, p


def label(a, b, today):
    hit = next((p for p in PRESETS if preset_range(p, today) == (a, b)), None)
    if hit:
        return LABELS[hit]
    if a == b:
        return a.strftime('%d %b %Y')
    return f"{a.strftime('%d %b %Y')} – {b.strftime('%d %b %Y')}"
