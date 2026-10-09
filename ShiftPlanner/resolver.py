"""
v1.12.0 – the one place that says which shift an employee works on a day.  Stable API (used by attendance, leave,
payroll, the roster and My schedule):

    shift_for(employee, day)        -> dict | None
    off_day(employee, day)          -> True / False, or None when no shift information exists for the day
    scheduled_days(employee, a, b)  -> [date, …] working days between a and b (inclusive)
    Book(a, b, emp_ids)             -> the same answers for many employees / days with a handful of queries
                                       (book.get(employee, day) == shift_for(employee, day))

Priority (first match wins):
    1. 'roster'   – an entry of a PUBLISHED roster period (ShiftPlanner.RosterEntry; latest published period wins)
    2. 'override' – calendars.ShiftOverride (also written when a shift change / swap / cancellation / open shift is
                    approved for a day that has no published roster); override_shift empty = day off
    3. 'schedule' – calendars.EmployeeShiftSchedule active on the day whose assignment covers the employee
                    (employee, employee_offsets, department, branch, designation or category; direct employee
                    assignment beats group assignment, then the latest start date) – its ShiftPattern is resolved
                    with the employee's rotation offset; a pattern day without a shift = day off
    4. nothing    – shift_for returns None, off_day returns None (callers fall back to the weekend calendar)

The dict returned by shift_for:
    shift (calendars.Shift or None), shift_id, name, code, colour, date,
    start, end (naive datetimes; end is on the next day for a cross-midnight shift; None on a day off),
    break_minutes, hours (Decimal, end − start − break), off (bool), source ('roster' | 'override' | 'schedule'),
    grace_in_minutes, grace_out_minutes, min_hours, max_hours, half_day_hours (Decimal or None),
    night_shift (bool), ot_after_minutes, shift_allowance, night_allowance (Decimal per day)
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Q

ZERO = Decimal('0')
SOURCES = ('roster', 'override', 'schedule')


def _emp(employee):
    from EmpManagement.models import emp_master
    if isinstance(employee, emp_master):
        return employee
    return emp_master.objects.filter(pk=employee).first()


def _day(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, str):
        return date.fromisoformat(d[:10])
    return d


def crosses_midnight(shift):
    return bool(shift and shift.start_time and shift.end_time and shift.end_time <= shift.start_time)


def describe(shift, rule, day, source, off=False):
    """The resolver dict for `shift` on `day` (shift None or without times = day off)."""
    has_times = bool(shift and shift.start_time and shift.end_time)
    off = bool(off or not has_times)
    start = end = None
    brk = 0
    hours = ZERO
    if shift is not None and shift.break_duration:
        brk = int(shift.break_duration.total_seconds() // 60)
    if not off:
        start = datetime.combine(day, shift.start_time)
        end = datetime.combine(day, shift.end_time)
        if end <= start:
            end += timedelta(days=1)
        hours = (Decimal((end - start).total_seconds()) / Decimal(3600) - Decimal(brk) / Decimal(60)).quantize(Decimal('0.01'))
        if hours < 0:
            hours = ZERO
    night = (rule.night_shift if (rule is not None and not rule.night_auto) else crosses_midnight(shift))
    return {
        'date': day, 'shift': shift, 'shift_id': shift.pk if shift is not None else None,
        'name': shift.name if shift is not None else '', 'code': (rule.code if rule is not None else '') or '',
        'colour': (rule.colour if rule is not None else '') or '',
        'start': start, 'end': end, 'break_minutes': brk, 'hours': hours, 'off': off, 'source': source,
        'grace_in_minutes': rule.grace_in_minutes if rule is not None else 0,
        'grace_out_minutes': rule.grace_out_minutes if rule is not None else 0,
        'min_hours': rule.min_hours if rule is not None else None,
        'max_hours': rule.max_hours if rule is not None else None,
        'half_day_hours': rule.half_day_hours if rule is not None else None,
        'night_shift': bool(night) and not off,
        'ot_after_minutes': rule.ot_after_minutes if rule is not None else 0,
        'shift_allowance': (rule.shift_allowance if rule is not None else ZERO) if not off else ZERO,
        'night_allowance': (rule.night_allowance if (rule is not None and night) else ZERO) if not off else ZERO,
    }


class Book:
    """Pre-loaded shift facts for days a..b (and optionally only some employees)."""

    def __init__(self, a, b, emp_ids=None, roster=True, overrides=True):
        from calendars.models import EmployeeShiftSchedule, Shift, ShiftOverride
        from .models import RosterEntry, ShiftRule
        self.a, self.b = _day(a), _day(b)
        ids = None if emp_ids is None else {int(i) for i in emp_ids}
        self.shifts = {s.pk: s for s in Shift.objects.all()}
        self.rules = {r.shift_id: r for r in ShiftRule.objects.all()}
        self.entries = {}
        if roster:
            qs = RosterEntry.objects.filter(period__status='published', date__gte=self.a, date__lte=self.b)
            if ids is not None:
                qs = qs.filter(employee_id__in=ids)
            for e in qs.order_by('period__published_at', 'period_id', 'id'):  # later published periods win
                self.entries[(e.employee_id, e.date)] = e
        self.overrides = {}
        if overrides:
            qs = ShiftOverride.objects.filter(date__gte=self.a, date__lte=self.b)
            if ids is not None:
                qs = qs.filter(employee_id__in=ids)
            for o in qs.order_by('id'):
                self.overrides[(o.employee_id, o.date)] = o
        self.schedules = []
        qs = EmployeeShiftSchedule.objects.filter(start_date__lte=self.b).filter(Q(end_date__gte=self.a) | Q(end_date__isnull=True)) \
            .select_related('shift_pattern').prefetch_related('employee', 'departments', 'branches', 'designations', 'categories')
        for s in qs:
            direct = {e.pk for e in s.employee.all()} | {int(k) for k in (s.employee_offsets or {}) if str(k).isdigit()}
            self.schedules.append({
                'obj': s, 'direct': direct, 'dept': {x.pk for x in s.departments.all()}, 'branch': {x.pk for x in s.branches.all()},
                'desig': {x.pk for x in s.designations.all()}, 'cat': {x.pk for x in s.categories.all()},
            })
        self._pattern_cache = {}

    # ------------------------------------------------------------------
    def schedule_for(self, emp, day):
        """(schedule, direct?) covering the employee on day, best first."""
        best, key = None, None
        for s in self.schedules:
            o = s['obj']
            if o.start_date > day or (o.end_date and o.end_date < day):
                continue
            direct = emp.pk in s['direct']
            group = (emp.emp_dept_id_id in s['dept'] or emp.emp_branch_id_id in s['branch']
                     or emp.emp_desgntn_id_id in s['desig'] or emp.emp_ctgry_id_id in s['cat'])
            if not (direct or group):
                continue
            k = (1 if direct else 0, o.start_date, o.pk)
            if key is None or k > key:
                best, key = o, k
        return best

    def _pattern_shift(self, sched, emp, day):
        anchor_off = 0
        if sched.employee_offsets:
            try:
                anchor_off = int(sched.employee_offsets.get(str(emp.pk)) or 0)
            except (TypeError, ValueError):
                anchor_off = 0
        ck = (sched.pk, anchor_off, day)
        if ck not in self._pattern_cache:
            self._pattern_cache[ck] = sched.get_shift_for_date(day, employee=emp)
        return self._pattern_cache[ck]

    def get(self, employee, day, sources=SOURCES):
        emp = _emp(employee)
        day = _day(day)
        if emp is None or day is None:
            return None
        if 'roster' in sources:
            e = self.entries.get((emp.pk, day))
            if e is not None:
                sh = None if e.off else self.shifts.get(e.shift_id)
                r = describe(sh, self.rules.get(e.shift_id) if sh else None, day, 'roster', off=e.off or sh is None)
                r['entry_id'] = e.pk
                r['period_id'] = e.period_id
                return r
        if 'override' in sources:
            o = self.overrides.get((emp.pk, day))
            if o is not None:
                sh = self.shifts.get(o.override_shift_id) if o.override_shift_id else None
                return describe(sh, self.rules.get(sh.pk) if sh else None, day, 'override', off=sh is None)
        if 'schedule' in sources:
            s = self.schedule_for(emp, day)
            if s is not None and s.shift_pattern_id:
                sh = self._pattern_shift(s, emp, day)
                if sh is not None:
                    sh = self.shifts.get(sh.pk, sh)
                r = describe(sh, self.rules.get(sh.pk) if sh else None, day, 'schedule', off=sh is None)
                r['schedule_id'] = s.pk
                return r
        return None


# ------------------------------------------------------------------ public API
def shift_for(employee, day):
    """Shift of the employee on the day (see the module docstring), or None when nothing is planned."""
    day = _day(day)
    emp = _emp(employee)
    if emp is None or day is None:
        return None
    return Book(day, day, [emp.pk]).get(emp, day)


def off_day(employee, day):
    """True on a planned day off, False on a shift day, None when no shift information exists (use the weekend calendar)."""
    r = shift_for(employee, day)
    return None if r is None else bool(r['off'])


def _weekend_names(emp):
    try:
        from calendars.utils import get_employee_weekend_calendar
        w = get_employee_weekend_calendar(emp)
    except Exception:
        w = None
    if not w:
        return set()
    return {d for d in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday') if getattr(w, d, None) == 'leave'}


def holidays(emp, a, b):
    """Public holidays of the employee's holiday calendar between a and b (same calendar choice as LeavePolicy)."""
    try:
        from calendars.utils import get_employee_holiday_calendar
        h = get_employee_holiday_calendar(emp)
    except Exception:
        h = None
    if not h:
        return set()
    out = set()
    for x in h.holiday_list.filter(start_date__lte=b):
        s = x.start_date
        e = getattr(x, 'end_date', None) or s
        while s <= e:
            if a <= s <= b:
                out.add(s)
            s += timedelta(days=1)
    return out


def scheduled_days(employee, a, b, book=None):
    """Working days between a and b (inclusive).

    A published roster entry decides alone (a rostered public holiday is a working day).  Otherwise a shift override /
    schedule decides, public holidays excluded.  Days without any shift information fall back to the weekend calendar
    and public holidays – the same rule LeavePolicy uses today.
    """
    emp = _emp(employee)
    a, b = _day(a), _day(b)
    if emp is None or a is None or b is None or b < a:
        return []
    book = book or Book(a, b, [emp.pk])
    hol = holidays(emp, a, b)
    wk = None
    out, d = [], a
    while d <= b:
        r = book.get(emp, d)
        if r is not None:
            if not r['off'] and (r['source'] == 'roster' or d not in hol):
                out.append(d)
        else:
            if wk is None:
                wk = _weekend_names(emp)
            if d.strftime('%A').lower() not in wk and d not in hol:
                out.append(d)
        d += timedelta(days=1)
    return out
