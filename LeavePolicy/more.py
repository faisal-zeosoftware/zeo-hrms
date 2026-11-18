"""
v1.73.8 – more leave rules on a policy line (LeavePolicyLine.more, a JSON object; every key optional, empty = no rule):

  past_days        a request may start at most N days in the past (the employee's own requests; HR is not limited)
  future_days      a request may start at most N days ahead
  max_consecutive  at most N days in a row, counting requests of the same type that touch this one (only days off between)
  min_gap_days     at least N days between two requests of the same type
  times / per      at most N requests a 'month' or a leave 'year'
  no_combine       [leave type ids] that may not touch this leave (e.g. no casual leave next to annual leave)
  marital          'married' / 'single' – only employees with that marital status
  designations     [ids] – only these designations
  except_departments / except_designations / except_locations  [ids] – everyone except these
  balance_cap      the balance never grows above N days (accrual stops at the cap)
  exceed           'lop' – more days than the balance are allowed and taken as unpaid (loss of pay); default refuse
  rh_dates         ['2026-12-25', …] restricted holidays: a request must be one of these days (limit with times / per year)
  anniversary      [{"after_years": 1, "days": 10, "repeat": "once" | "every", "every_years": 1}] – extra days on the
                   service anniversary (credited by the monthly accrual run in the month of the anniversary)

Off (no key) nothing changes: the rules below return nothing and the accrual adds nothing.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import logging
from datetime import date, timedelta

from django.apps import apps
from django.db import transaction

from Translations.i18n import tr

log = logging.getLogger(__name__)

RULES = ('past_days', 'future_days', 'max_consecutive', 'min_gap_days', 'times', 'per', 'no_combine', 'marital', 'designations',
         'except_departments', 'except_designations', 'except_locations', 'balance_cap', 'exceed', 'rh_dates', 'anniversary')
LABELS = {'past_days': 'Can be asked up to (days after it started)', 'future_days': 'Can be asked at most (days ahead)', 'max_consecutive': 'Days in a row at most',
          'min_gap_days': 'Days between two requests at least', 'times': 'Requests at most', 'balance_cap': 'Balance cap (days)'}
MARRIED = {'M'}
SINGLE = {'S', 'divorced', 'widow', 'D', 'W'}


def M(app, name):
    return apps.get_model(app, name)


def of(line):
    m = getattr(line, 'more', None) if line is not None else None
    return m if isinstance(m, dict) else {}


def _num(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _ids(v):
    out = []
    for x in v or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def clean(data):
    """The `more` object as it is saved: known keys only, numbers as numbers, empty values dropped. Raises ValueError."""
    if not isinstance(data, dict):
        return {}
    out = {}
    for k in ('past_days', 'future_days', 'max_consecutive', 'min_gap_days', 'times', 'balance_cap'):
        n = _num(data.get(k))
        if n is not None:
            if n < 0:
                raise ValueError(tr('{0} cannot be below 0.', tr(LABELS.get(k, k))))
            out[k] = int(n) if k in ('past_days', 'future_days', 'min_gap_days', 'times') else n
    if out.get('times'):
        out['per'] = data.get('per') if data.get('per') in ('month', 'year') else 'year'
    if data.get('marital') in ('married', 'single'):
        out['marital'] = data['marital']
    for k in ('no_combine', 'designations', 'except_departments', 'except_designations', 'except_locations'):
        v = _ids(data.get(k))
        if v:
            out[k] = v
    if data.get('exceed') == 'lop':
        out['exceed'] = 'lop'
    rh = []
    for d in data.get('rh_dates') or []:
        try:
            rh.append(date.fromisoformat(str(d)[:10]).isoformat())
        except ValueError:
            raise ValueError(tr('Restricted holiday: {0} is not a date.', d))
    if rh:
        out['rh_dates'] = sorted(set(rh))
    an = []
    for a in data.get('anniversary') or []:
        y, d = _num((a or {}).get('after_years')), _num((a or {}).get('days'))
        if not y or not d:
            continue
        rep = a.get('repeat') if a.get('repeat') in ('once', 'every') else 'once'
        ev = int(_num(a.get('every_years')) or 1)
        an.append({'after_years': int(y), 'days': d, 'repeat': rep, 'every_years': max(ev, 1)})
    if an:
        out['anniversary'] = an
    return out


# ------------------------------------------------------------------ who may take it
def eligible_problems(emp, lt, line):
    m, out = of(line), []
    if not m:
        return out
    ms = emp.emp_marital_status or ''
    if m.get('marital') == 'married' and ms not in MARRIED:
        out.append(tr('{0} is only for married employees.', lt.name))
    if m.get('marital') == 'single' and ms in MARRIED:
        out.append(tr('{0} is only for employees who are not married.', lt.name))
    des = getattr(emp, 'emp_desgntn_id_id', None)
    if m.get('designations') and des not in m['designations']:
        out.append(tr('{0} is not for the designation of {1}.', lt.name, emp.emp_first_name))
    if des in (m.get('except_designations') or []):
        out.append(tr('{0} is not for the designation of {1}.', lt.name, emp.emp_first_name))
    if getattr(emp, 'emp_dept_id_id', None) in (m.get('except_departments') or []):
        out.append(tr('{0} is not for the department of {1}.', lt.name, emp.emp_first_name))
    if m.get('except_locations'):
        try:
            loc = M('OrgStructure', 'EmployeeOrg').objects.filter(employee_id=emp.pk).values_list('location_id', flat=True).first()
        except LookupError:
            loc = None
        if loc in m['except_locations']:
            out.append(tr('{0} is not for the location of {1}.', lt.name, emp.emp_first_name))
    return out


def eligible(emp, line):
    return not eligible_problems(emp, type('T', (), {'name': ''})(), line)


# ------------------------------------------------------------------ the request
def _touches(emp, a, b):
    """True when nothing but days off lie between the days a (end of one leave) and b (start of the next)."""
    from .engine import scheduled_days
    if b <= a + timedelta(days=1):
        return True
    if (b - a).days > 15:
        return False
    return not scheduled_days(emp, a + timedelta(days=1), b - timedelta(days=1))


def request_problems(req, line, days, by_hr=False, today=None):
    """The rules of `more` the request breaks (texts)."""
    m = of(line)
    if not m:
        return []
    from .engine import ACTIVE_STATUSES, leave_year
    emp, lt = req.employee, req.leave_type
    today = today or _zt_today()
    out = eligible_problems(emp, lt, line)
    LR = M('calendars', 'employee_leave_request')
    if m.get('past_days') is not None and not by_hr and (today - req.start_date).days > m['past_days']:
        out.append(tr('{0} can be asked at most {1} days after it started (from {2:%d/%m/%Y}).', lt.name, m['past_days'], today - timedelta(days=m['past_days'])))
    if m.get('future_days') is not None and (req.start_date - today).days > m['future_days']:
        out.append(tr('{0} can be asked at most {1} days ahead (until {2:%d/%m/%Y}).', lt.name, m['future_days'], today + timedelta(days=m['future_days'])))
    if m.get('rh_dates'):
        allowed = {date.fromisoformat(d) for d in m['rh_dates']}
        d, bad = req.start_date, []
        while d <= req.end_date:
            if d not in allowed:
                bad.append(d)
            d += timedelta(days=1)
        if bad:
            out.append(tr('{0} is for the restricted holidays only ({1}); {2:%d/%m/%Y} is not one.', lt.name,
                          ', '.join(f'{x:%d/%m/%Y}' for x in sorted(allowed)), bad[0]))
    others = list(LR.objects.filter(employee=emp, status__in=ACTIVE_STATUSES, start_date__lte=req.end_date + timedelta(days=40),
                                    end_date__gte=req.start_date - timedelta(days=40)).exclude(pk=req.pk).select_related('leave_type'))
    same = [o for o in others if o.leave_type_id == lt.pk]
    if m.get('max_consecutive'):
        total, a, b = float(days), req.start_date, req.end_date
        chain = sorted(same, key=lambda o: o.start_date)
        moved = True
        while moved:
            moved = False
            for o in chain:
                if getattr(o, '_in', False):
                    continue
                if (o.end_date < a and _touches(emp, o.end_date, a)) or (o.start_date > b and _touches(emp, b, o.start_date)):
                    o._in, moved = True, True
                    total += float(o.approved_days or o.number_of_days or 0)
                    a, b = min(a, o.start_date), max(b, o.end_date)
        if total > m['max_consecutive'] + 1e-9:
            out.append(tr('{0} is at most {1:g} days in a row; with the leave next to it this is {2:g} days.', lt.name, m['max_consecutive'], total))
    if m.get('min_gap_days'):
        g = m['min_gap_days']
        for o in same:
            gap = (req.start_date - o.end_date).days - 1 if o.end_date < req.start_date else (o.start_date - req.end_date).days - 1 if o.start_date > req.end_date else -1
            if gap < g:
                out.append(tr('{0} needs at least {1} days between two requests; {2} {3:%d/%m/%Y}–{4:%d/%m/%Y} is too close.', lt.name, g,
                              o.document_number or '', o.start_date, o.end_date))
                break
    if m.get('times'):
        if m.get('per') == 'month':
            a = req.start_date.replace(day=1)
            b = (a + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            word = tr('a month')
        else:
            a, b = leave_year(line.policy, req.start_date, emp)
            word = tr('a leave year')
        n = LR.objects.filter(employee=emp, leave_type=lt, status__in=ACTIVE_STATUSES, start_date__gte=a, start_date__lte=b).exclude(pk=req.pk).count()
        if n >= m['times']:
            out.append(tr('{0} can be asked {1} time(s) {2}; {3} already.', lt.name, m['times'], word, n))
    if m.get('no_combine'):
        for o in others:
            if o.leave_type_id in m['no_combine'] and ((o.end_date < req.start_date and _touches(emp, o.end_date, req.start_date)) or
                                                       (o.start_date > req.end_date and _touches(emp, req.end_date, o.start_date))):
                out.append(tr('{0} cannot be taken next to {1} ({2} {3:%d/%m/%Y}–{4:%d/%m/%Y}).', lt.name, o.leave_type.name,
                              o.document_number or '', o.start_date, o.end_date))
                break
    return out


def exceed_lop(line):
    return of(line).get('exceed') == 'lop'


# ------------------------------------------------------------------ over the balance → unpaid
def lop_on_approve(instance):
    """pre_save of a request being approved: with exceed = lop, the days over the balance are approved as unpaid
    (approved_days = the balance; LeaveRequestInfo.lop_days = the rest). Returns the unpaid days or None."""
    from . import engine
    lt = instance.leave_type
    if engine.is_hourly(lt) or lt.type == 'unpaid':
        return None
    line = engine.line_for(instance.employee, lt.pk)
    if not exceed_lop(line):
        return None
    asked = float(instance.approved_days or instance.applied_days or instance.number_of_days or 0)
    bal = float(engine.balance_row(instance.employee_id, lt.pk).balance or 0)
    if asked <= bal + 1e-9:
        return None
    unit = 0.5 if instance.dis_half_day else 1.0
    ok = max(0.0, int(bal / unit) * unit)
    instance.approved_days = ok
    lop = round(asked - ok, 2)
    instance._lop_days = lop
    instance._lop_all = ok <= 0   # calendars deduct_leave_balance takes nothing
    if instance.pk:
        M('LeavePolicy', 'LeaveRequestInfo').objects.update_or_create(request_id=instance.pk, defaults={'lop_days': lop})
        # an approval saved with update_fields=['status', …] would not write approved_days – write it here
        type(instance).objects.filter(pk=instance.pk).update(approved_days=ok)
    return lop


def lop_of(req):
    if getattr(req, '_lop_days', None):
        return req._lop_days
    if not getattr(req, 'pk', None):
        return None
    try:
        return M('LeavePolicy', 'LeaveRequestInfo').objects.filter(request_id=req.pk).values_list('lop_days', flat=True).first()
    except Exception:   # noqa: BLE001
        return None


# ------------------------------------------------------------------ accrual: the cap and the anniversary credit
def capped(line, emp_id, amt):
    """(amount, note) – the accrual cut so the balance stays at most the cap."""
    cap = _num(of(line).get('balance_cap'))
    if cap is None or amt <= 0:
        return amt, ''
    from .engine import balance_row
    bal = float(balance_row(emp_id, line.leave_type_id).balance or 0)
    room = max(cap - bal, 0.0)
    if amt <= room + 1e-9:
        return amt, ''
    return round(room, 2), f'capped at {cap:g} days (balance {bal:g})'


def anniversaries(emp, m_start, m_end):
    """[(service years, anniversary date)] falling in the month."""
    j = emp.emp_joined_date
    if not j:
        return []
    out = []
    for y in range(max(1, m_start.year - j.year - 1), m_end.year - j.year + 1):
        try:
            d = j.replace(year=j.year + y)
        except ValueError:   # 29 February
            d = date(j.year + y, 3, 1)
        if m_start <= d <= m_end:
            out.append((y, d))
    return out


def anniversary_days(rules, years):
    n = 0.0
    for r in rules or []:
        a = int(r.get('after_years') or 0)
        if not a or years < a:
            continue
        if r.get('repeat') == 'every':
            if (years - a) % max(int(r.get('every_years') or 1), 1) == 0:
                n += float(r.get('days') or 0)
        elif years == a:
            n += float(r.get('days') or 0)
    return n


def run_anniversary(emp, lines, m_start, m_end, user_id=None, preview=False):
    from . import engine
    Ledger = engine._models()[3]
    rows = []
    hits = anniversaries(emp, m_start, m_end)
    if not hits:
        return rows
    left = engine.exit_date(emp)
    for line in lines:
        rules = of(line).get('anniversary')
        if not rules:
            continue
        for years, d in hits:
            if left and left < d:
                continue
            n = anniversary_days(rules, years)
            if not n:
                continue
            if Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind='anniversary', ref_model='anniversary', ref_id=years).exists():
                continue
            n, why = capped(line, emp.pk, n)
            if not n:
                continue
            note = f'{d:%Y-%m} · {years} year(s) of service on {d:%d/%m/%Y}' + (f' · {why}' if why else '')
            rows.append({'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} ({emp.emp_code})', 'leave_type_id': line.leave_type_id,
                         'kind': 'anniversary', 'days': n, 'note': note})
            if not preview:
                with transaction.atomic():
                    e = engine.post(emp.pk, line.leave_type_id, d, 'anniversary', n, note=note, user_id=user_id, move_balance=True)
                    Ledger.objects.filter(pk=getattr(e, 'pk', None)).update(ref_model='anniversary', ref_id=years)
    return rows


# ------------------------------------------------------------------ leave grant (HR gives days)
def grant(emp_ids, lt_id, days, on, reason, user_id=None):
    from . import engine
    out = []
    for eid in emp_ids:
        with transaction.atomic():
            e = engine.post(eid, lt_id, on, 'grant', float(days), note=(reason or 'Leave grant')[:255], user_id=user_id, move_balance=True)
        out.append(getattr(e, 'pk', None))
    return out
