"""SLA clock: add minutes to a moment and measure elapsed minutes, either around the clock (24 / 7) or only in
working hours (ProjectsSettings work days / hours, ZEO holiday calendars)."""
from datetime import datetime, time, timedelta, timezone as dt_tz
from zoneinfo import ZoneInfo

from django.utils import timezone

MAX_DAYS = 800   # safety net for the day loops


class WorkCalendar:
    def __init__(self, settings=None):
        from .models import ProjectsSettings
        s = settings or ProjectsSettings.get()
        try:
            self.tz = ZoneInfo(s.timezone or 'Asia/Dubai')
        except Exception:
            self.tz = ZoneInfo('Asia/Dubai')
        days = [int(d) for d in (s.work_days or []) if str(d).isdigit() and 0 <= int(d) <= 6]
        self.days = set(days) if days else {0, 1, 2, 3, 4}
        self.start = _as_time(s.work_start, time(9, 0))
        self.end = _as_time(s.work_end, time(18, 0))
        if self.end <= self.start:
            self.start, self.end = time(9, 0), time(18, 0)
        self.calendar_ids = [int(x) for x in (s.holiday_calendar_ids or []) if str(x).isdigit()]
        self._holidays = None

    @property
    def holidays(self):
        if self._holidays is None:
            self._holidays = set()
            try:
                from calendars.models import holiday
                qs = holiday.objects.filter(restricted=False)
                if self.calendar_ids:
                    qs = qs.filter(calendar_id__in=self.calendar_ids)
                for h in qs.values('start_date', 'end_date'):
                    d, end = h['start_date'], h['end_date'] or h['start_date']
                    n = 0
                    while d and d <= end and n < 60:
                        self._holidays.add(d)
                        d += timedelta(days=1)
                        n += 1
            except Exception:
                pass
        return self._holidays

    def is_work_day(self, d):
        return d.weekday() in self.days and d not in self.holidays

    def day_window(self, d):
        """(start, end) of working time on date d as aware datetimes, or None."""
        if not self.is_work_day(d):
            return None
        return (datetime.combine(d, self.start, tzinfo=self.tz), datetime.combine(d, self.end, tzinfo=self.tz))

    def minutes_per_day(self):
        return (datetime.combine(datetime.today(), self.end) - datetime.combine(datetime.today(), self.start)).seconds // 60


def _as_time(v, default):
    if isinstance(v, time):
        return v
    try:
        h, m = str(v).split(':')[:2]
        return time(int(h), int(m))
    except Exception:
        return default


def add_minutes(start, minutes, business=True, cal=None):
    """Moment `minutes` after `start` (aware datetime)."""
    if start is None:
        return None
    minutes = int(minutes or 0)
    if not business:
        return start + timedelta(minutes=minutes)
    cal = cal or WorkCalendar()
    cur = start.astimezone(cal.tz)
    left = minutes
    for _ in range(MAX_DAYS):
        win = cal.day_window(cur.date())
        if win:
            s, e = win
            if cur < s:
                cur = s
            if cur < e:
                avail = int((e - cur).total_seconds() // 60)
                if left <= avail:
                    return (cur + timedelta(minutes=left)).astimezone(dt_tz.utc)
                left -= avail
        cur = datetime.combine(cur.date() + timedelta(days=1), time(0, 0), tzinfo=cal.tz)
    return (start + timedelta(minutes=minutes)).astimezone(dt_tz.utc)


def minutes_between(a, b, business=True, cal=None):
    """Elapsed minutes from a to b (0 when b <= a)."""
    if a is None or b is None or b <= a:
        return 0
    if not business:
        return int((b - a).total_seconds() // 60)
    cal = cal or WorkCalendar()
    cur, end = a.astimezone(cal.tz), b.astimezone(cal.tz)
    total = 0
    for _ in range(MAX_DAYS):
        if cur >= end:
            break
        win = cal.day_window(cur.date())
        if win:
            s, e = win
            lo, hi = max(cur, s), min(end, e)
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        cur = datetime.combine(cur.date() + timedelta(days=1), time(0, 0), tzinfo=cal.tz)
    return total


def human(minutes):
    """'45 min', '4 h', '2 h 30 min', '3 working days' style text for a target."""
    if minutes is None:
        return ''
    m = int(minutes)
    if m < 60:
        return f'{m} min'
    h, r = divmod(m, 60)
    return f'{h} h' + (f' {r} min' if r else '')
