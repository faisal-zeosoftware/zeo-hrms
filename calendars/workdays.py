"""v1.62.6 (item 22) – the ONE answer to "is this a working day for this employee?" used by attendance, leave day counting,
payroll working days, AttendancePlus, the employee record and My schedule.

Order (first match decides):
    1. roster      – an entry of a published roster (ShiftPlanner)            shift → working (also on a public holiday;
                                                                              Day.holiday still True there – holiday pay)
    2. override    – a shift override for the day (shift change / swap)      no shift → day off
    3. pattern     – the employee's shift schedule / pattern                  no shift → day off
    4. weekend     – the weekend calendar (below); public holidays are off on 2–4.

Weekend calendar of an employee on a day – the most specific assignment wins:
    employee > designation > category > department > branch
Category assignments always count (before: calendars.utils.get_employee_weekend_days ignored them, and
get_employee_weekend_calendar let a branch weekend win over a category weekend). Inside one level the calendar of the day's
year wins, then the newest assignment. A weekend change with a start date (LeavePolicy/weekend_change.py) is kept in the
CompanyOption 'weekend_changes' (no new column): before its start date the assignment still gives the old calendar, or does
not count at all when it was made by the change.

Public holidays: the employee's holiday calendar (employee > branch > department > category – calendars.utils
get_employee_holiday_calendar, now also used by get_employee_holidays).

Stable API:
    day(emp, d)                 -> Day(working, off, holiday, weekend, source, label, shift)
    working_days(emp, a, b)     -> [date, …]
    weekend_of(emp, d=None)     -> (weekend_calendar | None, level | None, assignment | None)
    weekend_names(emp, d=None)  -> {'Friday', …}
    Plan(a, b, emp_ids=None)    -> the same answers for many employees / days with a handful of queries
    explain(emp, d=None)        -> what decides the working days (employee record, My schedule)
"""
from collections import namedtuple
from datetime import date, datetime, timedelta

from django.apps import apps

LEVELS = ('employee', 'designation', 'category', 'department', 'branch')
LEVEL_LABEL = {'employee': 'Employee', 'designation': 'Designation', 'category': 'Category', 'department': 'Department',
               'branch': 'Branch'}
DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
OPTION_KEY = 'weekend_changes'

Day = namedtuple('Day', 'working off holiday weekend source label shift')


def _d(x):
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, str):
        return date.fromisoformat(x[:10])
    return x


def _today():
    try:
        from AttendancePlus.engine import today
        return today()
    except Exception:
        from django.utils import timezone
        return timezone.localdate()


def _attr(emp, level):
    if level == 'employee':
        return emp.pk
    return {'designation': getattr(emp, 'emp_desgntn_id_id', None), 'category': getattr(emp, 'emp_ctgry_id_id', None),
            'department': getattr(emp, 'emp_dept_id_id', None), 'branch': getattr(emp, 'emp_branch_id_id', None)}[level]


def model_days(w, d=None):
    """Day names off in weekend calendar `w` (on day d: alternate weeks such as {'Saturday': '2,4'} counted)."""
    if w is None:
        return set()
    out = {n for n in DAY_NAMES if getattr(w, n.lower(), None) == 'leave'}
    alt = getattr(w, 'alternate_weekends', None) or {}
    if alt and isinstance(alt, dict):
        for name, pattern in alt.items():
            n = str(name).capitalize()
            if n not in DAY_NAMES:
                continue
            weeks = {int(x) for x in str(pattern).split(',') if x.strip().isdigit()}
            if d is None:
                out.discard(n)
            elif d.strftime('%A') == n:
                if ((d.day - 1) // 7) + 1 in weeks:
                    out.add(n)
                else:
                    out.discard(n)
    return out


# ------------------------------------------------------------------ weekend changes with a start date (JSON store)
def load_changes():
    """{assignment id: [{'from': date, 'old': weekend id | None, 'new': weekend id}, …] sorted by start date}"""
    out = {}
    try:
        CO = apps.get_model('ZeoApps', 'CompanyOption')
        o = CO.objects.filter(key=OPTION_KEY).first()
    except Exception:
        o = None
    for c in ((o.value or {}).get('changes') or []) if o else []:
        if c.get('undone'):
            continue
        for p in c.get('parts') or []:
            try:
                out.setdefault(int(p['assignment']), []).append({'from': _d(c['start_date']), 'old': p.get('old'), 'new': p.get('new')})
            except (KeyError, TypeError, ValueError):
                continue
    for v in out.values():
        v.sort(key=lambda x: x['from'])
    return out


class WeekendRules:
    """Every weekend assignment read once."""

    def __init__(self):
        AW, W = apps.get_model('calendars', 'assign_weekend'), apps.get_model('calendars', 'weekend_calendar')
        self.models = {w.pk: w for w in W.objects.all()}
        self.items = []
        for a in AW.objects.prefetch_related('employee', 'designation', 'category', 'department', 'branch').order_by('pk'):
            sets = {'employee': {x.pk for x in a.employee.all()}, 'designation': {x.pk for x in a.designation.all()},
                    'category': {x.pk for x in a.category.all()}, 'department': {x.pk for x in a.department.all()},
                    'branch': {x.pk for x in a.branch.all()}}
            self.items.append((a, sets))
        self.changes = load_changes()

    def model_on(self, a, d):
        """The calendar assignment `a` gives on day d (None: the assignment does not count yet)."""
        for c in self.changes.get(a.pk, ()):
            if d < c['from']:
                return self.models.get(c['old']) if c['old'] else None
        return self.models.get(a.weekend_model_id)

    def pick(self, emp, d=None):
        d = d or _today()
        for level in LEVELS:
            v = _attr(emp, level)
            if v is None:
                continue
            found = []
            for a, sets in self.items:
                if a.related_to and a.related_to != level:
                    continue
                if v in sets[level]:
                    m = self.model_on(a, d)
                    if m is not None:
                        found.append((1 if getattr(m, 'year', None) == d.year else 0, a.pk, a, m))
            if found:
                found.sort(key=lambda x: (x[0], x[1]))
                _, _, a, m = found[-1]
                return m, level, a
        return None, None, None


class HolidayRules:
    """Holiday calendar of an employee: employee > branch > department > category (calendars.utils
    get_employee_holiday_calendar); dates of each calendar read once."""
    ORDER = ('employee', 'branch', 'department', 'category')

    def __init__(self):
        AH = apps.get_model('calendars', 'assign_holiday')
        self.items = []
        for a in AH.objects.select_related('holiday_model').prefetch_related('employee', 'branch', 'department', 'category').order_by('pk'):
            self.items.append((a, {'employee': {x.pk for x in a.employee.all()}, 'branch': {x.pk for x in a.branch.all()},
                                   'department': {x.pk for x in a.department.all()}, 'category': {x.pk for x in a.category.all()}}))
        self._dates = {}

    def calendar(self, emp):
        for level in self.ORDER:
            v = _attr(emp, level)
            if v is None:
                continue
            for a, sets in self.items:
                if a.related_to == level and v in sets[level]:
                    return a.holiday_model
        return None

    def dates(self, emp):
        h = self.calendar(emp)
        if h is None:
            return set()
        if h.pk not in self._dates:
            out = set()
            for s, e in h.holiday_list.all().values_list('start_date', 'end_date'):
                x = s
                while x <= (e or s):
                    out.add(x)
                    x += timedelta(days=1)
            self._dates[h.pk] = out
        return self._dates[h.pk]


def _book(a, b, emp_ids):
    if not apps.is_installed('ShiftPlanner'):
        return None
    try:
        from ShiftPlanner.resolver import Book
        return Book(a, b, emp_ids)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('shift book failed; weekend calendar only')
        return None


class Plan:
    """Working-day facts for days a..b (one shift book, one read of the weekend / holiday assignments)."""

    def __init__(self, a, b, emp_ids=None, book=None, rules=None, holidays=None):
        self.a, self.b = _d(a), _d(b)
        self.book = book if book is not None else _book(self.a, self.b, emp_ids)
        self.rules = rules or WeekendRules()
        self.hol = holidays or HolidayRules()
        self._wk = {}

    def weekend(self, emp, d):
        return self.rules.pick(emp, d)

    def weekend_days(self, emp, d):
        m, _, _ = self.rules.pick(emp, d)
        return model_days(m, d)

    def day(self, emp, d):
        d = _d(d)
        hol = d in self.hol.dates(emp)
        r = None
        if self.book is not None and self.book.a <= d <= self.book.b:
            try:
                r = self.book.get(emp, d)
            except Exception:
                r = None
        if r is not None:
            src = {'roster': 'roster', 'override': 'override', 'schedule': 'pattern'}.get(r.get('source'), r.get('source'))
            label = {'roster': 'Roster', 'override': 'Shift override', 'pattern': 'Shift pattern'}.get(src, src)
            if r.get('off'):
                return Day(False, True, hol, False, src, label + ': day off', r)
            if src == 'roster':   # a rostered shift is a working day, also on a public holiday (holiday stays True: holiday pay)
                return Day(True, False, hol, False, src, label + ': ' + (r.get('name') or ''), r)
            return Day(not hol, False, hol, False, src, ('Public holiday' if hol else label + ': ' + (r.get('name') or '')), r)
        m, level, _ = self.rules.pick(emp, d)
        wk = d.strftime('%A') in model_days(m, d)
        if wk:
            return Day(False, True, hol, True, 'weekend', 'Weekend', None)
        if hol:
            return Day(False, False, True, False, 'weekend' if m is not None else 'none', 'Public holiday', None)
        return Day(True, False, False, False, 'weekend' if m is not None else 'none', 'Working day', None)

    def working_days(self, emp, a=None, b=None):
        a, b = _d(a or self.a), _d(b or self.b)
        out, d = [], a
        while d <= b:
            if self.day(emp, d).working:
                out.append(d)
            d += timedelta(days=1)
        return out


def _emp(e):
    EM = apps.get_model('EmpManagement', 'emp_master')
    return e if isinstance(e, EM) else EM.objects.filter(pk=e).first()


def day(emp, d, plan=None):
    emp, d = _emp(emp), _d(d)
    return (plan or Plan(d, d, [emp.pk])).day(emp, d)


def working_days(emp, a, b, book=None):
    emp = _emp(emp)
    a, b = _d(a), _d(b)
    if emp is None or a is None or b is None or b < a:
        return []
    return Plan(a, b, [emp.pk], book=book).working_days(emp)


def weekend_of(emp, d=None, rules=None):
    emp = _emp(emp)
    if emp is None:
        return None, None, None
    return (rules or WeekendRules()).pick(emp, _d(d) if d else None)


def weekend_names(emp, d=None, rules=None):
    m, _, _ = weekend_of(emp, d, rules)
    return model_days(m, _d(d) if d else None)


def holiday_dates(emp, a=None, b=None):
    out = HolidayRules().dates(_emp(emp))
    if a is not None and b is not None:
        return {x for x in out if a <= x <= b}
    return set(out)


def _name_of(level, emp):
    try:
        if level == 'employee':
            return f'{emp.emp_first_name or ""} {emp.emp_last_name or ""}'.strip()
        if level == 'designation':
            return emp.emp_desgntn_id.desgntn_job_title if emp.emp_desgntn_id_id else ''
        if level == 'category':
            return emp.emp_ctgry_id.ctgry_title if emp.emp_ctgry_id_id else ''
        if level == 'department':
            return emp.emp_dept_id.dept_name if emp.emp_dept_id_id else ''
        if level == 'branch':
            return emp.emp_branch_id.branch_name if emp.emp_branch_id_id else ''
    except Exception:
        return ''
    return ''


def explain(emp, d=None, days=14):
    """What decides the employee's working days: the weekend rule with every level that has an assignment (the winner marked),
    shift schedule / pattern, roster and overrides, and the next `days` days with their source."""
    emp = _emp(emp)
    d = _d(d) if d else _today()
    end = d + timedelta(days=max(days, 1) - 1)
    plan = Plan(d, end, [emp.pk])
    m, level, a = plan.rules.pick(emp, d)
    levels = []
    for lv in LEVELS:
        v = _attr(emp, lv)
        cal = None
        if v is not None:
            for x, sets in plan.rules.items:
                if (not x.related_to or x.related_to == lv) and v in sets[lv]:
                    mm = plan.rules.model_on(x, d)
                    if mm is not None:
                        cal = mm
        levels.append({'level': lv, 'label': LEVEL_LABEL[lv], 'name': _name_of(lv, emp) if v is not None else '',
                       'calendar': cal.calendar_code if cal else '', 'days': sorted(model_days(cal), key=DAY_NAMES.index) if cal else [],
                       'wins': lv == level})
    upcoming = []
    for c in plan.rules.changes.get(a.pk, ()) if a else ():
        if c['from'] > d:
            nm = plan.rules.models.get(c['new'])
            upcoming.append({'from': c['from'].isoformat(), 'calendar': nm.calendar_code if nm else '',
                             'days': sorted(model_days(nm), key=DAY_NAMES.index) if nm else []})
    sched = None
    if plan.book is not None:
        try:
            s = plan.book.schedule_for(emp, d)
            if s is not None:
                sched = {'id': s.pk, 'name': s.schedule_name or '', 'pattern': getattr(s.shift_pattern, 'name', '') if s.shift_pattern_id else '',
                         'from': s.start_date.isoformat() if s.start_date else None, 'to': s.end_date.isoformat() if s.end_date else None}
        except Exception:
            sched = None
    rows, by_src = [], {}
    x = d
    while x <= end:
        f = plan.day(emp, x)
        by_src[f.source] = by_src.get(f.source, 0) + 1
        rows.append({'date': x.isoformat(), 'weekday': x.strftime('%A'), 'working': f.working, 'off': f.off, 'holiday': f.holiday,
                     'source': f.source, 'label': f.label, 'shift': (f.shift or {}).get('name') or ''})
        x += timedelta(days=1)
    decides = max(by_src, key=by_src.get) if by_src else 'none'
    return {
        'employee': emp.pk, 'date': d.isoformat(),
        'order': ['roster', 'override', 'pattern', 'weekend'],
        'weekend': {'calendar': m.calendar_code if m else '', 'calendar_id': m.pk if m else None, 'level': level or '',
                    'level_label': LEVEL_LABEL.get(level, ''), 'name': _name_of(level, emp) if level else '',
                    'days': sorted(model_days(m), key=DAY_NAMES.index) if m else [], 'levels': levels, 'upcoming': upcoming},
        'schedule': sched,
        'holiday_calendar': (lambda h: h.calendar_title if h else '')(plan.hol.calendar(emp)),
        'decides': decides, 'days': rows,
        'missing': m is None and sched is None and not any(r['source'] in ('roster', 'override', 'pattern') for r in rows),
    }
