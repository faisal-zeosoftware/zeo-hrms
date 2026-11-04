"""v1.62.2 (items 45, 46): the salary of an employee as it was valid on a day / in a pay period.

EmployeeSalaryStructure keeps one row per component (unique employee + component) with the current amount and, optionally,
valid_from / valid_until. Earlier amounts are in SalaryRevisionHistory (old → new per change, with the effective period when
the change was given one – appraisal, what-if, transfer, a row with valid_from). Payroll used the current amount for every
month, so an increase effective March 2027 was paid in November 2026 and arrears used today's salary for every past month.

Rules
- a row is not valid after valid_until (0) and, when it has no earlier amount, not before valid_from (0);
- a change with an effective date (effective_period 'March2027' = 1 Mar 2027, or an ISO date; valid_from of the row when it is
  in that month gives the exact day) applies from that date; before it the old amount of the change applies;
- a change without an effective date (an edit before 1.62.2, or a correction) has no date: it applies to every month as before;
- in a period with a change on a day inside it the amount is shared by calendar days (old × days before ÷ days + new × days
  from ÷ days) over the days the employee was employed in the period – a joiner's row valid from the joining day is the full
  amount, the joiner pro-rating of the payroll then applies once, as before.
"""
import copy
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps

_MONTHS = {m: i for i, m in enumerate(('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september',
                                       'october', 'november', 'december'), start=1)}


def _d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:
        return Decimal('0')


def _day(x):
    return x.date() if isinstance(x, datetime) else x


def parse_effective(text):
    """'March2027' / 'March 2027' / 'Mar 2027' → 1 Mar 2027; '2027-03-15' → that day; else None."""
    s = str(text or '').strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.match(r'^([A-Za-z]+)\s*[-/ ]?\s*(\d{4})$', s)
    if m:
        name = m.group(1).lower()
        mon = _MONTHS.get(name) or next((v for k, v in _MONTHS.items() if k.startswith(name[:3])), None)
        if mon:
            return date(int(m.group(2)), mon, 1)
    return None


def _changes(row, history=None):
    """The row's timeline [(from day or None = from the start, amount)], built from its changes in the order they were made: a
    change with an effective day sets the amount from that day on (a later instruction wins over an earlier one for the days it
    covers), a change without a day sets it for every day (as before 1.62.2). The last amount is the row's current amount.
    [] when no change has an effective day (the row's amount applies, within valid_from / valid_until)."""
    if history is None:
        H = apps.get_model('PayrollManagement', 'SalaryRevisionHistory')
        history = H.objects.filter(employee_id=row.employee_id, component_id=row.component_id).first()
    if history is None:
        return []
    revs = list(history.revisions or [])
    if not revs and (history.effective_period or history.new_amount is not None):
        revs = [{'old_amount': history.old_amount, 'new_amount': history.new_amount, 'effective_period': history.effective_period}]
    vf = getattr(row, 'valid_from', None)
    line, dated = None, False
    for r in revs:
        eff = parse_effective(r.get('effective_period'))
        if line is None:
            line = [(None, _d(r.get('old_amount')))]
        new = _d(r.get('new_amount'))
        if eff is None:
            line = [(None, new)]
            continue
        dated = True
        if vf and (vf.year, vf.month) == (eff.year, eff.month) and eff.day == 1:
            eff = vf      # the row's own valid_from gives the day inside the month
        line = [x for x in line if x[0] is None or x[0] < eff] + [(eff, new)]
    if not dated:
        return []
    line[-1] = (line[-1][0], _d(row.amount))   # the current amount (also when the row was changed without the history)
    return line


def amount_on(row, day, changes=None):
    """The amount of the row valid on the day."""
    day = _day(day)
    vu = getattr(row, 'valid_until', None)
    if vu and day > vu:
        return Decimal('0')
    ch = _changes(row) if changes is None else changes
    if ch:
        amt = ch[0][1]
        for eff, a in ch:
            if eff is None or eff <= day:
                amt = a
        return amt
    vf = getattr(row, 'valid_from', None)
    if vf and day < vf:
        return Decimal('0')
    return _d(row.amount)


def segments(row, start, end, changes=None):
    """[(from, to, amount)] of the row inside start..end (days with the same amount together)."""
    a, b = _day(start), _day(end)
    if not a or not b or b < a:
        return []
    ch = _changes(row) if changes is None else changes
    cuts = {a}
    for eff, _ in ch:
        if eff is not None and a < eff <= b:
            cuts.add(eff)
    vf, vu = getattr(row, 'valid_from', None), getattr(row, 'valid_until', None)
    if vf and a < vf <= b:
        cuts.add(vf)
    if vu and a <= vu < b:
        cuts.add(vu + timedelta(days=1))
    cuts = sorted(cuts)
    out = []
    for i, f in enumerate(cuts):
        t = (cuts[i + 1] - timedelta(days=1)) if i + 1 < len(cuts) else b
        out.append((f, t, amount_on(row, f, ch)))
    return out


def amount_in_period(row, start, end, changes=None):
    """The monthly amount of the row for the period: the amount valid in it, shared by calendar days when it changes inside
    the period (rounded to 0.01)."""
    segs = segments(row, start, end, changes)
    if not segs:
        return _d(row.amount)
    if len(segs) == 1:
        return segs[0][2]
    days = Decimal((_day(end) - _day(start)).days + 1)
    tot = sum((amt * Decimal((t - f).days + 1) for f, t, amt in segs), Decimal('0'))
    return (tot / days).quantize(Decimal('0.01'))


def valid_share(row, start, end, changes=None):
    """Share (0..1) of the days of start..end on which the row is valid at all: not after valid_until, and not before valid_from
    when the row has no earlier amount (a formula component given from a date)."""
    a, b = _day(start), _day(end)
    if not a or not b or b < a:
        return Decimal('1')
    ch = _changes(row) if changes is None else changes
    vf, vu = getattr(row, 'valid_from', None), getattr(row, 'valid_until', None)
    f = max(a, vf) if (vf and not ch) else a
    t = min(b, vu) if vu else b
    if t < f:
        return Decimal('0')
    return Decimal((t - f).days + 1) / Decimal((b - a).days + 1)


def employed_window(employee, start, end):
    """The days of start..end the employee was employed (joining date / last working day inside the period)."""
    a, b = _day(start), _day(end)
    j = getattr(employee, 'emp_joined_date', None)
    try:
        from .signals import last_working_day
        l = last_working_day(employee)
    except Exception:
        l = None
    f = max(a, j) if j and j > a else a
    t = min(b, l) if l and l < b else b
    return (f, t) if t >= f else (a, b)


def period_rows(employee, start, end, rows=None):
    """The employee's active salary rows with `.amount` set (in memory only) to the amount valid in the period – shared by
    days over the days employed; a row not valid on any day of the period is left out. `.v1622_full` keeps the stored amount."""
    if rows is None:
        ESS = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
        rows = list(ESS.objects.filter(employee=employee, is_active=True).select_related('component'))
    a, b = employed_window(employee, start, end)
    H = apps.get_model('PayrollManagement', 'SalaryRevisionHistory')
    hist = {h.component_id: h for h in H.objects.filter(employee=employee)}
    out = []
    for r in rows:
        r = copy.copy(r)   # never saved: the stored row keeps its amount
        ch = _changes(r, hist.get(r.component_id))
        vf, vu = getattr(r, 'valid_from', None), getattr(r, 'valid_until', None)
        if (vu and vu < a) or (vf and vf > b and not ch):
            continue
        r.v1622_full = r.amount
        r.v1622_changes = ch
        r.v1622_share = valid_share(r, a, b, ch)
        r.amount = amount_in_period(r, a, b, ch)
        out.append(r)
    return out
