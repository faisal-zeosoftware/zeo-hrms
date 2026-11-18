"""v1.73.15 – demo data: the in / out punches of one month for every active employee of a demo company, then the attendance
engine works out the day (late, early, overtime, missing punch) exactly as for punches from a device.

Run by the data workbook (sheet "Attendance", How = job, job = demo-attendance) before the leave and payroll rows of the month,
inside the company's schema. Days off, public holidays and approved leave get no punches. The times are the same on every
server (no random numbers): most people arrive a few minutes early, about one day in fifteen is late, site / field staff with
overtime stay one to three hours longer on about one day in four, and one day in forty misses the out punch.
"""
import datetime
import zlib

DEMO_SOURCE = 'web'


def _h(s, mod):
    return zlib.crc32(s.encode()) % mod


def _month_off(e, first, last):
    """{date: True} for the days off of the employee in the month (weekend, public holiday, approved leave) – read once."""
    import datetime as dt
    from calendars.utils import get_employee_holidays, get_employee_weekend_days
    from calendars.models import employee_leave_request
    off = set()
    try:
        off |= set(get_employee_holidays(e, first, last))
    except Exception:   # noqa: BLE001
        pass
    try:
        wk = set(get_employee_weekend_days(e, first))
    except Exception:   # noqa: BLE001
        wk = set()
    d = first
    while d <= last:
        if d.strftime('%A') in wk:
            off.add(d)
        d += dt.timedelta(days=1)
    for lv in employee_leave_request.objects.filter(employee=e, status='approved', start_date__lte=last, end_date__gte=first).exclude(leave_type__unit='hours'):
        d = max(lv.start_date, first)
        while d <= min(lv.end_date, last):
            off.add(d)
            d += dt.timedelta(days=1)
    return off


def from_row(row):
    """The punches of one month, with the payroll run's memo open (shift plan, rules, weekend and holiday calendars are read
    once – the job does not change them)."""
    try:
        from PayrollManagement.perf import payroll_cache
    except ImportError:   # pragma: no cover
        return _month(row)
    with payroll_cache():
        return _month(row)


def _month(row):
    """row: {'month': 'YYYY-MM', 'until': 'YYYY-MM-DD'} → {'days': n, 'punches': n}.
    Days off are read once per employee and month; the attendance calendar is brought up to date once per employee and
    month (not after every day) – about five times faster than recomputing day by day, the same result."""
    from AttendancePlus import engine as E
    from AttendancePlus.models import Punch
    from EmpManagement.models import emp_master
    y, m = (int(x) for x in str(row.get('month'))[:7].split('-'))
    until = datetime.date.fromisoformat(str(row.get('until'))[:10])
    today = E.today()
    until = min(until, today)   # today: the morning check-in of those whose shift has started (no check-out yet)
    now = E.now()
    first = datetime.date(y, m, 1)
    if until < first:
        return {'days': 0, 'punches': 0}
    days = punches = 0
    for e in emp_master.objects.filter(is_active=True):
        off = _month_off(e, first, until)
        rule = E.rule_for(e, first)
        worked = []
        new = []
        day = first
        while day <= until:
            joined = getattr(e, 'emp_joined_date', None)
            if day in off or (joined and joined > day):
                day += datetime.timedelta(days=1)
                continue
            sh = E.shift_info(e, day, rule=rule)
            if not sh or sh.get('off') or not sh.get('start') or not sh.get('end'):
                day += datetime.timedelta(days=1)
                continue
            k = f'{e.emp_code}{day.isoformat()}'
            late = _h(k + 'L', 15) == 0
            t_in = sh['start'] + datetime.timedelta(minutes=(12 + _h(k + 'l', 35)) if late else -(2 + _h(k + 'i', 14)))
            ot = bool(getattr(e, 'emp_ot_applicable', False)) and _h(k + 'O', 4) == 0
            t_out = sh['end'] + datetime.timedelta(minutes=(60 + 30 * _h(k + 'o', 5)) if ot else _h(k + 'e', 20))
            if day == today:
                if t_in > now:
                    day += datetime.timedelta(days=1)
                    continue
                todo = [t_in]
            else:
                todo = [t_in] if _h(k + 'M', 40) == 0 else [t_in, t_out]
            for t in todo:
                new.append(Punch(employee_id=e.id, ts=t, kind='auto', source=DEMO_SOURCE, work_date=day, note='Demo data'))
            worked.append(day)
            day += datetime.timedelta(days=1)
        had = set(Punch.objects.filter(employee_id=e.id, work_date__gte=first, work_date__lte=until, is_void=False).values_list('work_date', flat=True))
        new = [p for p in new if p.work_date not in had]
        Punch.objects.bulk_create(new)
        punches += len(new)
        for d in worked:
            try:
                E.recompute_day(e, d, rule=rule, sync_calendar=False)
                days += 1
            except Exception:   # noqa: BLE001
                pass
        try:
            from calendars.utils import sync_attendance_calendar
            sync_attendance_calendar(e, first, until)
        except Exception:   # noqa: BLE001
            pass
    return {'days': days, 'punches': punches}
