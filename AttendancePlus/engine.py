"""AttendancePlus engine (v1.12.0): rules, shift resolution, punch → work day, the daily result and
the one overtime path.

Public functions
    tz()                               tenant time zone
    rule_for(employee)                 most specific active AttendanceRule (or defaults)
    shift_info(employee, day, rule)    planned shift (ShiftPlanner resolver when installed, else calendars)
    work_date_for(employee, ts, rule)  the day a punch belongs to (cross-midnight shifts)
    add_punch(...)                     store a punch (idempotent) and recompute the day
    recompute_day(employee, day)       compute + store AttendanceDay, calendars Attendance, overtime, calendar
    recompute_from_attendance(att)     the hook calendars.Attendance.save() uses (single OT path)
    day_summary(employee, start, end)  totals for payroll / ESS
"""
import logging
import threading
from datetime import date as date_cls, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.utils import timezone

from . import models as M

log = logging.getLogger(__name__)
_local = threading.local()
_tz_cache = {}


# ----------------------------------------------------------------------------- basics
def tz():
    schema = getattr(connection, 'schema_name', 'public')
    if schema in _tz_cache:
        return _tz_cache[schema]
    name = None
    try:
        from UserManagement.models import company
        comp = company.objects.filter(schema_name=schema).select_related('country').first()
        name = getattr(getattr(comp, 'country', None), 'timezone', None)
    except Exception:
        name = None
    try:
        zone = ZoneInfo(name or 'Asia/Dubai')
    except Exception:
        zone = ZoneInfo('Asia/Dubai')
    _tz_cache[schema] = zone
    return zone


def now():
    return timezone.now()


def local(dt):
    return dt.astimezone(tz()) if dt else None


def today():
    return local(now()).date()


def aware(day, t):
    return datetime.combine(day, t).replace(tzinfo=tz())


def as_aware(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return dt.replace(tzinfo=tz())
    return dt


def minutes(td):
    return int(round(td.total_seconds() / 60)) if td else 0


def emp(employee_or_id):
    from EmpManagement.models import emp_master
    if isinstance(employee_or_id, emp_master):
        return employee_or_id
    return emp_master.objects.select_related('emp_branch_id').get(pk=employee_or_id)


# ----------------------------------------------------------------------------- rules
SCOPE_RANK = {'employee': 5, 'category': 4, 'department': 3, 'branch': 2, 'company': 1}


def rule_for(employee):
    """Most specific active rule: employee > category > department > branch > company, then priority."""
    best, best_key = None, None
    for r in M.AttendanceRule.objects.filter(is_active=True):
        ok = (r.scope == 'company'
              or (r.scope == 'branch' and r.branch_id == employee.emp_branch_id_id)
              or (r.scope == 'department' and r.department_id == employee.emp_dept_id_id)
              or (r.scope == 'category' and r.category_id == employee.emp_ctgry_id_id)
              or (r.scope == 'employee' and r.employee_id == employee.id))
        if not ok:
            continue
        key = (SCOPE_RANK.get(r.scope, 0), r.priority, -r.id)
        if best_key is None or key > best_key:
            best, best_key = r, key
    return best or M.AttendanceRule(name='Default rules', id=None)


def method_allowed(rule, method):
    allowed = rule.allowed_methods or []
    return not allowed or method in allowed or method in ('correction', 'manual', 'import')


# ----------------------------------------------------------------------------- shifts
def _resolver():
    try:
        from ShiftPlanner import resolver   # built by the shift planner app (v1.12.0)
        return resolver
    except Exception:
        return None


def _calendars_shift(employee, day):
    """Fallback shift: ShiftOverride > EmployeeShiftSchedule pattern > Attendance.shift."""
    from calendars.models import ShiftOverride, EmployeeShiftSchedule, Attendance
    from django.db.models import Q
    ov = ShiftOverride.objects.filter(employee=employee, date=day).select_related('override_shift').first()
    if ov and ov.override_shift_id:
        return ov.override_shift
    try:
        for s in EmployeeShiftSchedule.objects.filter(start_date__lte=day).filter(
                Q(end_date__gte=day) | Q(end_date__isnull=True)).order_by('-start_date'):
            if s.get_assigned_employees().filter(id=employee.id).exists():
                sh = s.get_shift_for_date(day, employee)
                if sh is not None:
                    return sh
                return 'OFF' if s.shift_pattern_id else None
    except Exception:
        log.debug('shift schedule lookup failed', exc_info=True)
    att = Attendance.objects.filter(employee=employee, date=day).select_related('shift').first()
    if att and att.shift_id:
        return att.shift
    return None


def shift_info(employee, day, rule=None):
    """{'shift_id','name','start','end','break_minutes','off','night_shift','grace_in','grace_out',
    'min_hours','max_hours','half_day_hours','ot_after_minutes','source'} or None (no shift planned)."""
    rule = rule or rule_for(employee)
    res = _resolver()
    if res is not None:
        try:
            d = res.shift_for(employee, day)
        except Exception:
            log.warning('ShiftPlanner resolver failed – using calendars shifts', exc_info=True)
            d = 'fallback'
        if d != 'fallback':
            if not d:
                return _rule_default(rule, day)
            if d.get('off'):
                return {'off': True, 'source': d.get('source', 'planner'), 'shift_id': d.get('shift_id'), 'name': str(d.get('shift') or 'Off')}
            start, end = as_aware(d.get('start')), as_aware(d.get('end'))
            if start and end and end <= start:
                end += timedelta(days=1)
            return {
                'off': False, 'source': d.get('source', 'planner'), 'shift_id': d.get('shift_id'),
                'name': str(getattr(d.get('shift'), 'name', d.get('shift')) or ''), 'start': start, 'end': end,
                'break_minutes': int(d.get('break_minutes') or 0),
                'night_shift': bool(d.get('night_shift')),
                # shift-level values win when set (0 / empty = use the attendance rule)
                'grace_in': d.get('grace_in_minutes') or None, 'grace_out': d.get('grace_out_minutes') or None,
                'min_hours': d.get('min_hours') or None, 'max_hours': d.get('max_hours') or None,
                'half_day_hours': d.get('half_day_hours') or None, 'ot_after_minutes': d.get('ot_after_minutes') or None,
            }
    sh = _calendars_shift(employee, day)
    if sh == 'OFF':
        return {'off': True, 'source': 'pattern', 'shift_id': None, 'name': 'Off'}
    if sh is None or not sh.start_time or not sh.end_time:
        if sh is not None and not sh.start_time:
            return {'off': True, 'source': 'shift', 'shift_id': sh.id, 'name': sh.name}
        return _rule_default(rule, day)
    start = aware(day, sh.start_time)
    end = aware(day, sh.end_time)
    if end <= start:
        end += timedelta(days=1)
    return {'off': False, 'source': 'calendars', 'shift_id': sh.id, 'name': sh.name, 'start': start, 'end': end,
            'break_minutes': minutes(sh.break_duration) if sh.break_duration else 0, 'night_shift': False,
            'grace_in': None, 'grace_out': None, 'min_hours': None, 'max_hours': None, 'half_day_hours': None,
            'ot_after_minutes': None}


def _rule_default(rule, day):
    if rule.default_start and rule.default_end:
        start, end = aware(day, rule.default_start), aware(day, rule.default_end)
        if end <= start:
            end += timedelta(days=1)
        return {'off': False, 'source': 'rule', 'shift_id': None, 'name': 'Rule hours', 'start': start, 'end': end,
                'break_minutes': 0, 'night_shift': False, 'grace_in': None, 'grace_out': None, 'min_hours': None,
                'max_hours': None, 'half_day_hours': None, 'ot_after_minutes': None}
    return None


def is_off_day(employee, day, shift=None):
    """(kind, label): kind in ('holiday', 'weekly_off', None)."""
    from calendars.utils import get_employee_weekend_days, get_employee_holidays
    try:
        if day in get_employee_holidays(employee, day, day):
            return 'holiday', 'Public holiday'
    except Exception:
        pass
    if shift and shift.get('off'):
        return 'weekly_off', 'Off day (shift plan)'
    res = _resolver()
    if res is not None and shift is None:
        try:
            if res.off_day(employee, day):
                return 'weekly_off', 'Off day (shift plan)'
        except Exception:
            pass
    try:
        if day.strftime('%A') in get_employee_weekend_days(employee):
            return 'weekly_off', 'Weekend'
    except Exception:
        pass
    return None, ''


def leave_on(employee, day):
    from calendars.models import employee_leave_request
    return employee_leave_request.objects.filter(employee=employee, status='approved', start_date__lte=day,
                                                 end_date__gte=day).select_related('leave_type').first()


# ----------------------------------------------------------------------------- punch → day
def work_date_for(employee, ts, rule=None):
    rule = rule or rule_for(employee)
    lt = local(ts)
    d = lt.date()
    if rule.cross_midnight == 'calendar':
        return d
    prev = d - timedelta(days=1)
    sp = shift_info(employee, prev, rule)
    if sp and not sp.get('off') and sp.get('end') and sp['end'].date() > prev:
        # yesterday's shift runs past midnight: punches until 6 h after its end belong to it,
        # unless today's own shift has (nearly) started
        st = shift_info(employee, d, rule)
        today_start = st.get('start') if st and not st.get('off') else None
        if ts <= sp['end'] + timedelta(hours=6) and (today_start is None or ts < today_start - timedelta(hours=2)):
            return prev
    elif lt.hour < (rule.day_change_hour or 0):
        last = M.Punch.objects.filter(employee_id=employee.id, is_void=False, work_date=prev).order_by('-ts').first()
        if last and last.resolved_kind in ('in', 'break_in', 'lunch_in') and ts - last.ts < timedelta(hours=16):
            return prev
    return d


def resolve_kinds(punches):
    """Fill resolved_kind: explicit kinds are kept; 'auto' alternates in / out (ignoring break punches)."""
    state = 'out'
    for p in punches:
        if p.kind == 'auto':
            p.resolved_kind = 'in' if state == 'out' else 'out'
        else:
            p.resolved_kind = p.kind
        if p.resolved_kind == 'in':
            state = 'in'
        elif p.resolved_kind == 'out':
            state = 'out'
    return punches


def next_kind(employee_id, day):
    """What an 'auto' punch will be now: in or out."""
    ps = resolve_kinds(list(M.Punch.objects.filter(employee_id=employee_id, work_date=day, is_void=False).order_by('ts')))
    state = 'out'
    for p in ps:
        if p.resolved_kind in ('in', 'out'):
            state = p.resolved_kind
    return 'out' if state == 'in' else 'in'


def _round(dt, rule, is_in):
    step = rule.rounding_minutes or 0
    if not dt or step <= 1 or rule.rounding_mode == 'none':
        return dt
    lt = local(dt)
    base = lt.replace(second=0, microsecond=0)
    m = base.hour * 60 + base.minute
    down = m - m % step
    up = down if (m % step == 0 and lt.second == 0) else down + step
    mode = rule.rounding_mode
    if mode == 'nearest':
        target = down if (m - down) < step / 2 else up
    elif mode == 'strict':
        target = up if is_in else down
    else:
        target = down if is_in else up
    return base.replace(hour=0, minute=0) + timedelta(minutes=target)


def _night_minutes(intervals, rule):
    total = 0
    for a, b in intervals:
        la, lb = local(a), local(b)
        d = la.date() - timedelta(days=1)
        while d <= lb.date():
            ns = aware(d, rule.night_start)
            ne = aware(d, rule.night_end)
            if ne <= ns:
                ne += timedelta(days=1)
            lo, hi = max(la, ns), min(lb, ne)
            if hi > lo:
                total += minutes(hi - lo)
            d += timedelta(days=1)
    return total


def _absence_exempt(employee, rule):
    if not rule.mark_absent_if_no_punch:
        return True
    if rule.exempt_manual_source and (employee.attendance_source or 'manual') == 'manual':
        return True
    if employee.emp_ctgry_id_id and employee.emp_ctgry_id_id in [int(x) for x in (rule.exempt_category_ids or []) if str(x).isdigit()]:
        return True
    if employee.id in [int(x) for x in (rule.exempt_employee_ids or []) if str(x).isdigit()]:
        return True
    return False


def _legacy_punches(employee, day):
    """No punches in AttendancePlus: use the calendars Attendance row (manual entry / old import)."""
    from calendars.models import Attendance
    att = Attendance.objects.filter(employee=employee, date=day).first()
    out = []
    if att and att.check_in_time:
        tin = aware(day, att.check_in_time)
        out.append(M.Punch(employee_id=employee.id, ts=tin, kind='in', resolved_kind='in', source='manual', work_date=day))
        if att.check_out_time:
            tout = aware(day, att.check_out_time)
            if tout <= tin:
                tout += timedelta(days=1)
            out.append(M.Punch(employee_id=employee.id, ts=tout, kind='out', resolved_kind='out', source='manual', work_date=day))
    return out


def _dec(v, default=0):
    try:
        return Decimal(str(v)) if v not in (None, '') else Decimal(str(default))
    except Exception:
        return Decimal(str(default))


def compute(employee, day, rule=None, at=None):
    """The daily result as a dict (not saved)."""
    rule = rule or rule_for(employee)
    at = at or now()
    sh = shift_info(employee, day, rule)
    off_kind, off_label = is_off_day(employee, day, sh)
    lv = leave_on(employee, day)
    punches = list(M.Punch.objects.filter(employee_id=employee.id, work_date=day, is_void=False).order_by('ts'))
    resolve_kinds(punches)
    legacy = False
    if not punches:
        punches = _legacy_punches(employee, day)
        legacy = bool(punches)

    working_shift = sh if sh and not sh.get('off') else None
    r = {
        'employee_id': employee.id, 'date': day, 'branch_id': employee.emp_branch_id_id,
        'shift_id': (sh or {}).get('shift_id'), 'shift_name': (sh or {}).get('name', '') or '',
        'shift_start': working_shift and working_shift.get('start'), 'shift_end': working_shift and working_shift.get('end'),
        'rule_id': rule.id, 'first_in': None, 'last_out': None, 'worked_minutes': 0, 'break_minutes': 0,
        'late_minutes': 0, 'early_minutes': 0, 'ot_minutes': 0, 'ot_type': '', 'ot_rate': Decimal('1'),
        'night_minutes': 0, 'status': 'not_marked', 'is_late': False, 'is_early': False, 'is_night_shift': False,
        'missing_punch': False, 'over_max_hours': False, 'punch_count': len(punches),
        'sources': sorted({p.source for p in punches}), 'flags': {},
    }
    flags = r['flags']
    if legacy:
        flags['from_attendance_row'] = True
    if employee.emp_joined_date and day < employee.emp_joined_date:
        flags['before_joining'] = True
        return r

    # ---- intervals from punches
    ins = [p for p in punches if p.resolved_kind == 'in']
    outs = [p for p in punches if p.resolved_kind == 'out']
    first_in = ins[0].ts if ins else None
    last_out = None
    if first_in:
        later = [p for p in outs if p.ts > first_in]
        last_out = later[-1].ts if later else None
    open_in = bool(first_in) and (not last_out or (ins and ins[-1].ts > last_out))
    first_in_r = _round(first_in, rule, True)
    last_out_r = _round(last_out, rule, False)
    r['first_in'], r['last_out'] = first_in, last_out

    intervals, breaks, taken_break = [], 0, False
    cur = None
    brk_start = None
    for p in punches:
        k = p.resolved_kind
        t = p.ts
        if p is (ins[0] if ins else None):
            t = first_in_r
        if last_out is not None and p.ts == last_out and k == 'out':
            t = last_out_r
        if k == 'in':
            if cur is None:
                cur = t
        elif k == 'out':
            if cur is not None and t > cur:
                intervals.append((cur, t))
            elif cur is None and intervals and t > intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], t)   # a second clock-out without a clock-in: the later one counts
            cur = None
        elif k in ('break_out', 'lunch_out'):
            brk_start = t
        elif k in ('break_in', 'lunch_in'):
            if brk_start is not None and t > brk_start:
                breaks += minutes(t - brk_start)
                taken_break = True
            brk_start = None
    # gaps between an out and the next in count as time away (break)
    gaps = 0
    for (a1, b1), (a2, b2) in zip(intervals, intervals[1:]):
        if a2 > b1:
            gaps += minutes(a2 - b1)
    gross = sum(minutes(b - a) for a, b in intervals)
    shift_break = (working_shift or {}).get('break_minutes') or rule.break_minutes or 0
    deduct = 0
    if taken_break:
        deduct = max(0, breaks - shift_break) if rule.break_paid else breaks
    elif gross and rule.auto_deduct_break and not rule.break_paid and gaps == 0 \
            and Decimal(gross) / 60 >= _dec(rule.auto_deduct_after_hours):
        deduct = shift_break
        flags['auto_break'] = shift_break
    r['break_minutes'] = (breaks if taken_break else (deduct if 'auto_break' in flags else 0)) + gaps
    worked = max(0, gross - deduct)

    # ---- max hours
    max_h = _dec((working_shift or {}).get('max_hours') or rule.max_hours)
    if max_h > 0 and worked > max_h * 60:
        r['over_max_hours'] = True
        if rule.max_hours_action == 'cap':
            flags['capped_from'] = worked
            worked = int(max_h * 60)
    r['worked_minutes'] = worked

    # ---- night
    r['night_minutes'] = _night_minutes(intervals, rule)
    r['is_night_shift'] = bool((working_shift or {}).get('night_shift')) or (
        r['night_minutes'] >= (rule.night_min_minutes or 0) and r['night_minutes'] > 0)

    # ---- base status
    if lv and not lv.dis_half_day:
        r['status'] = 'leave'
        flags['leave_type'] = getattr(lv.leave_type, 'name', '')
    elif off_kind:
        r['status'] = off_kind
        flags['off'] = off_label
    is_workday = r['status'] == 'not_marked'
    half_leave = bool(lv and lv.dis_half_day)
    if half_leave:
        flags['half_day_leave'] = getattr(lv.leave_type, 'name', '')

    shift_end_for_missing = (working_shift or {}).get('end') or aware(day, time(23, 59))
    if first_in and open_in:
        if at > shift_end_for_missing + timedelta(hours=float(rule.missing_punch_after_hours or 0)):
            r['missing_punch'] = True
    if first_in and not last_out and not r['missing_punch']:
        flags['in_progress'] = True

    if is_workday:
        if not punches:
            if day < today() or (day == today() and working_shift and at > working_shift['end']):
                if _absence_exempt(employee, rule):
                    r['status'] = 'present'
                    flags['no_punch_exempt'] = True
                else:
                    r['status'] = 'half_day' if half_leave else 'absent'
            else:
                r['status'] = 'not_marked'
        elif r['missing_punch']:
            r['status'] = 'missing_punch'
        else:
            r['status'] = 'present'

    # ---- late / early (working day with a shift)
    if is_workday and working_shift and first_in:
        g_in = working_shift.get('grace_in')
        g_in = rule.grace_in_minutes if g_in is None else int(g_in)
        start = working_shift['start']
        if half_leave and lv.half_day_period == 'first_half':
            start = start + (working_shift['end'] - start) / 2
        if first_in_r > start + timedelta(minutes=g_in):
            base = start if rule.late_from == 'shift_start' else start + timedelta(minutes=g_in)
            r['late_minutes'] = minutes(first_in_r - base)
            r['is_late'] = r['late_minutes'] > 0
        if last_out:
            g_out = working_shift.get('grace_out')
            g_out = rule.grace_out_minutes if g_out is None else int(g_out)
            end = working_shift['end']
            if half_leave and lv.half_day_period == 'second_half':
                end = working_shift['start'] + (end - working_shift['start']) / 2
            if last_out_r < end - timedelta(minutes=g_out):
                r['early_minutes'] = minutes(end - last_out_r)
                r['is_early'] = True
        if r['status'] == 'present':
            worst = None
            if rule.late_tolerance_minutes and r['late_minutes'] > rule.late_tolerance_minutes and rule.late_beyond_action != 'none':
                worst = rule.late_beyond_action
                flags['late_beyond_tolerance'] = True
            if rule.early_tolerance_minutes and r['early_minutes'] > rule.early_tolerance_minutes and rule.early_beyond_action != 'none':
                flags['early_beyond_tolerance'] = True
                if worst != 'absent':
                    worst = rule.early_beyond_action
            if worst:
                r['status'] = worst

    # ---- minimum hours (complete days only)
    if is_workday and r['status'] == 'present' and first_in and last_out and not half_leave:
        full = _dec((working_shift or {}).get('min_hours') or rule.min_hours_full_day)
        half = _dec((working_shift or {}).get('half_day_hours') or rule.min_hours_half_day)
        if full > 0 and worked < full * 60:
            if half > 0 and worked < half * 60:
                r['status'] = 'absent'
            else:
                r['status'] = 'half_day'
            flags['below_min_hours'] = True
    elif is_workday and half_leave and r['status'] == 'present':
        r['status'] = 'half_day'

    # ---- overtime
    r.update(_overtime(employee, rule, r, working_shift, worked, is_workday, half_leave))
    return r


def _ot_rate(employee, rule, ot_type, night=False):
    val = {'NORMAL': rule.ot_rate_normal, 'WEEKEND': rule.ot_rate_weekend, 'HOLIDAY': rule.ot_rate_holiday}.get(ot_type)
    if night and ot_type == 'NORMAL' and rule.ot_rate_night:
        val = rule.ot_rate_night
    if val:
        return Decimal(str(val))
    try:
        from PayrollManagement.utils import get_ot_rate
        v = get_ot_rate(employee, ot_type)
        if v:
            return Decimal(str(v))
    except Exception:
        pass
    return {'NORMAL': Decimal('1.25'), 'WEEKEND': Decimal('1.50'), 'HOLIDAY': Decimal('1.50')}[ot_type]


def _overtime(employee, rule, r, working_shift, worked, is_workday, half_leave):
    out = {'ot_minutes': 0, 'ot_type': '', 'ot_rate': Decimal('1')}
    if not (employee.emp_ot_applicable and rule.ot_enabled) or not worked or r['status'] in ('leave',):
        return out
    if r['status'] in ('holiday', 'weekly_off'):
        ot_type = 'HOLIDAY' if r['status'] == 'holiday' else 'WEEKEND'
        extra = worked
    else:
        if not r['last_out'] or r['status'] in ('missing_punch', 'absent') or half_leave:
            return out
        ot_type = 'NORMAL'
        if rule.ot_basis == 'shift' and working_shift:
            sched = minutes(working_shift['end'] - working_shift['start'])
            if not rule.break_paid:
                sched -= (working_shift.get('break_minutes') or rule.break_minutes or 0)
            extra = worked - sched
        else:
            extra = worked - int(_dec(rule.ot_daily_threshold_hours, 8) * 60)
        after = working_shift.get('ot_after_minutes') if working_shift else None
        after = rule.ot_after_minutes if after is None else int(after)
        extra -= after
    if extra < (rule.ot_min_minutes or 0):
        extra = 0
    cap = _dec(rule.ot_max_hours_per_day)
    if cap > 0:
        extra = min(extra, int(cap * 60))
    extra = max(0, int(extra))
    out.update(ot_minutes=extra, ot_type=ot_type if extra else '',
               ot_rate=_ot_rate(employee, rule, ot_type, r.get('is_night_shift')) if extra else Decimal('1'))
    return out


# ----------------------------------------------------------------------------- persist
def _guard():
    return getattr(_local, 'busy', 0)


class _busy:
    def __enter__(self):
        _local.busy = getattr(_local, 'busy', 0) + 1

    def __exit__(self, *a):
        _local.busy -= 1


def recompute_day(employee, day, rule=None, at=None, sync_calendar=True):
    employee = emp(employee)
    rule = rule or rule_for(employee)
    with _busy():
        r = compute(employee, day, rule, at)
        old = M.AttendanceDay.objects.filter(employee_id=employee.id, date=day).first()
        fields = {k: v for k, v in r.items() if k not in ('employee_id', 'date')}
        if old:
            fields['penalty_waived'] = old.penalty_waived
            fields['notified_missing_at'] = old.notified_missing_at if r['missing_punch'] else None
            if old.flags.get('late_penalty'):
                fields['flags']['late_penalty'] = old.flags['late_penalty']
            fields['period_ot_minutes'] = old.period_ot_minutes
        rec, _ = M.AttendanceDay.objects.update_or_create(employee_id=employee.id, date=day, defaults=fields)
        _sync_attendance_row(employee, day, r)
        _period_ot(employee, day, rule)
        rec.refresh_from_db()
        _write_overtime(employee, rec, rule)
        if sync_calendar and day <= today() and r['status'] != 'not_marked':
            try:
                from calendars.utils import sync_attendance_calendar
                sync_attendance_calendar(employee, day, day)
            except Exception:
                log.warning('attendance calendar sync failed', exc_info=True)
    return rec


def _sync_attendance_row(employee, day, r):
    """Keep calendars.Attendance (used by the existing screens, payroll worked_hours) in line."""
    from calendars.models import Attendance
    if r['flags'].get('from_attendance_row'):
        return
    att = Attendance.objects.filter(employee=employee, date=day).first()
    if not r['first_in']:
        return
    tin = local(r['first_in']).time().replace(microsecond=0)
    tout = local(r['last_out']).time().replace(microsecond=0) if r['last_out'] else None
    if att is None:
        att = Attendance(employee=employee, date=day)
    att.check_in_time = tin
    att.check_out_time = tout
    if r['shift_id'] and not att.shift_id:
        from calendars.models import Shift
        att.shift = Shift.objects.filter(pk=r['shift_id']).first()
    if not tout:
        att.total_hours = None
    att.save()


def _period_ot(employee, day, rule):
    """Weekly / monthly thresholds: hours worked above the threshold that are not already daily OT
    are placed on the last worked day of the week / month."""
    wk = _dec(rule.ot_weekly_threshold_hours)
    mo = _dec(rule.ot_monthly_threshold_hours)
    try:   # legacy OvertimeRule WEEKLY / MONTHLY thresholds when the rule has none
        from calendars.models import OvertimeRule
        if not wk:
            x = OvertimeRule.objects.filter(is_active=True, rule_type='WEEKLY', policy__is_active=True, policy__ot_type='NORMAL').first()
            wk = Decimal(x.threshold_hours.total_seconds() / 3600) if x else wk
        if not mo:
            x = OvertimeRule.objects.filter(is_active=True, rule_type='MONTHLY', policy__is_active=True, policy__ot_type='NORMAL').first()
            mo = Decimal(x.threshold_hours.total_seconds() / 3600) if x else mo
    except Exception:
        pass
    if not (employee.emp_ot_applicable and rule.ot_enabled) or (not wk and not mo):
        if M.AttendanceDay.objects.filter(employee_id=employee.id, period_ot_minutes__gt=0, date__year=day.year, date__month=day.month).exists():
            M.AttendanceDay.objects.filter(employee_id=employee.id, date__year=day.year, date__month=day.month).update(period_ot_minutes=0)
        return
    if wk:   # the weekly threshold is used when set, otherwise the monthly one
        a = day - timedelta(days=day.weekday())
        b, thr = a + timedelta(days=6), wk
    else:
        a = day.replace(day=1)
        b, thr = (a + timedelta(days=32)).replace(day=1) - timedelta(days=1), mo
    days = list(M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(a, b)).order_by('date'))
    normal = [d for d in days if d.status in ('present', 'half_day') and d.worked_minutes]
    before = {d.pk: d.period_ot_minutes for d in days}
    for d in days:
        d.period_ot_minutes = 0
    total = sum(d.worked_minutes for d in normal)
    daily = sum(d.ot_minutes for d in normal)
    extra = int(total - thr * 60 - daily)
    if extra > 0 and normal:
        normal[-1].period_ot_minutes = extra
    for d in days:
        if d.period_ot_minutes != before[d.pk]:
            d.save(update_fields=['period_ot_minutes'])
            if d.date != day:
                _write_overtime(employee, d, rule)


def _write_overtime(employee, rec, rule):
    """The ONE place attendance overtime rows are written: one row per employee / day / OT type.
    Approval is kept unless the hours went up; with "needs approval" off the row is approved at once.
    Manual OT rows are never touched."""
    from calendars.models import EmployeeOvertime
    total = (rec.ot_minutes or 0) + (rec.period_ot_minutes or 0)
    ot_type = rec.ot_type or ('NORMAL' if total else '')
    rows = EmployeeOvertime.objects.filter(employee=employee, date=rec.date).exclude(source='MANUAL')
    if not total:
        rows.delete()
        return
    rows.exclude(ot_type=ot_type).delete()
    hours = (Decimal(total) / Decimal(60)).quantize(Decimal('0.01'))
    row = EmployeeOvertime.objects.filter(employee=employee, date=rec.date, ot_type=ot_type).first()
    if row and row.source == 'MANUAL':
        return
    auto_ok = not rule.ot_needs_approval
    if row is None:
        EmployeeOvertime.objects.create(employee=employee, date=rec.date, ot_type=ot_type, slab='OT', hours=hours,
                                        approved=auto_ok, source='ATTENDANCE')
        return
    changed = []
    if row.hours != hours:
        if hours > row.hours and not auto_ok:
            row.approved = False
            changed.append('approved')
        row.hours = hours
        changed.append('hours')
    if auto_ok and not row.approved:
        row.approved = True
        changed.append('approved')
    if changed:
        row.save(update_fields=list(set(changed)))


def recompute_from_attendance(attendance):
    """Hook for calendars.Attendance.save (manual entry, imports, old punch screens)."""
    if _guard():
        return
    try:
        recompute_day(attendance.employee, attendance.date)
    except Exception:
        log.exception('AttendancePlus recompute failed for attendance %s', attendance.pk)


# ----------------------------------------------------------------------------- punches
class PunchError(Exception):
    pass


def add_punch(employee, ts=None, kind='auto', source='web', *, device=None, kiosk=None, device_ref='', ip='',
              lat=None, lng=None, location='', photo=None, verified_by='', user_id=None, correction=None,
              note='', check=True, recompute=True):
    """Store a punch (idempotent on employee + time) and recompute its work day.
    Returns (punch, created)."""
    employee = emp(employee)
    ts = as_aware(ts) or now()
    ts = ts.replace(microsecond=0)
    rule = rule_for(employee)
    existing = M.Punch.objects.filter(employee_id=employee.id, ts=ts).first()
    if existing:
        return existing, False
    if check:
        if rule.min_minutes_between_punches and source not in ('device', 'import', 'correction'):
            last = M.Punch.objects.filter(employee_id=employee.id, is_void=False).order_by('-ts').first()
            if last and abs((ts - last.ts).total_seconds()) < rule.min_minutes_between_punches * 60 and kind in ('auto', last.kind):
                raise PunchError(f'You punched less than {rule.min_minutes_between_punches} minute(s) ago. Wait a moment and try again.')
    day = work_date_for(employee, ts, rule)
    if kind == 'auto' and source not in ('device', 'import'):
        kind = next_kind(employee.id, day)
    with transaction.atomic():
        p = M.Punch.objects.create(employee_id=employee.id, ts=ts, kind=kind, source=source, work_date=day,
                                   device=device, kiosk=kiosk, device_ref=device_ref or '', ip=ip or '',
                                   lat=lat, lng=lng, location=location or '', photo=photo, verified_by=verified_by or '',
                                   created_by_id=user_id, correction=correction, note=note or '')
    ps = resolve_kinds(list(M.Punch.objects.filter(employee_id=employee.id, work_date=day, is_void=False).order_by('ts')))
    for x in ps:
        M.Punch.objects.filter(pk=x.pk).exclude(resolved_kind=x.resolved_kind).update(resolved_kind=x.resolved_kind)
    p.refresh_from_db()
    if recompute:
        recompute_day(employee, day, rule)
    return p, True


# ----------------------------------------------------------------------------- summaries
def day_summary(employee, start, end):
    from django.db.models import Sum, Count, Q
    qs = M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end))
    a = qs.aggregate(
        late_count=Count('id', filter=Q(is_late=True)), late_minutes=Sum('late_minutes'),
        early_count=Count('id', filter=Q(is_early=True)), early_minutes=Sum('early_minutes'),
        absent_days=Count('id', filter=Q(status='absent')), missing=Count('id', filter=Q(status='missing_punch') | Q(missing_punch=True)),
        half=Count('id', filter=Q(status='half_day')), present=Count('id', filter=Q(status__in=('present', 'half_day', 'missing_punch'))),
        brk=Sum('break_minutes'), worked=Sum('worked_minutes'), night=Count('id', filter=Q(is_night_shift=True)),
        night_minutes=Sum('night_minutes'),
    )
    return {k: (v or 0) for k, v in a.items()}


def nightly(day=None):
    """Recompute yesterday (and flag missing punches) for every active employee of the current company."""
    from EmpManagement.models import emp_master
    day = day or (today() - timedelta(days=1))
    n = 0
    for e in emp_master.objects.filter(is_active=True).select_related('emp_branch_id'):
        try:
            recompute_day(e, day)
            n += 1
        except Exception:
            log.exception('nightly attendance failed for %s', e.pk)
    notify_missing_punches()
    return n


def notify_missing_punches():
    from EmpManagement.models import emp_master
    sent = 0
    for d in M.AttendanceDay.objects.filter(missing_punch=True, notified_missing_at__isnull=True, date__gte=today() - timedelta(days=7)):
        e = emp_master.objects.filter(pk=d.employee_id).select_related('users').first()
        if not e:
            continue
        try:
            from zeo.module_helpers import notify
            notify(employee=e, title='Missing punch – please request a correction',
                   message=f'You have a clock-in without a clock-out on {d.date:%d %b %Y}. Open My attendance and request a correction.',
                   notification_type='attendance')
        except Exception:
            pass
        d.notified_missing_at = now()
        d.save(update_fields=['notified_missing_at'])
        sent += 1
    return sent


def refresh_missing(employee_ids=None):
    """Re-check open days so a missing punch shows up once the shift end + N hours has passed."""
    qs = M.AttendanceDay.objects.filter(flags__in_progress=True)
    if employee_ids is not None:
        qs = qs.filter(employee_id__in=employee_ids)
    for d in qs[:500]:
        recompute_day(d.employee_id, d.date, sync_calendar=False)


def as_date(v):
    if isinstance(v, date_cls):
        return v
    return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
