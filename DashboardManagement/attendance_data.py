"""
v1.16.0 – one attendance day engine for every attendance report and dashboard figure.

Two sources exist for an employee's day:
  * AttendancePlus.AttendanceDay – the daily result computed by the attendance rules (punches, shift, late / early, OT);
  * calendars.Attendance          – the older check-in / check-out row (manual entry, imports, old punch screens).
The daily results are only stored for days that were recalculated (`rebuild_attendance_days` fills the history), so a
report that read only one of them showed empty or different numbers. Here every day is read the same way:

    AttendanceDay (status set)  →  otherwise derived from calendars.Attendance + approved leave + holidays + weekly off

Status codes (register):  P present · HD half day · MP missing punch (in without out) · A absent · L leave · H holiday ·
WO weekly off · RW worked on a weekly off / holiday · NM today, not marked yet.
Late / early minutes come from the daily result, or (old rows) from the planned shift + 15 minutes grace (same rule as the
"Late arrivals" report). Overtime hours always come from calendars.EmployeeOvertime (the one place OT rows are written).
"""
from collections import defaultdict
from datetime import datetime, timedelta

from django.apps import apps

CODES = {
    'Present': 'P', 'Half day': 'HD', 'Missing punch': 'MP', 'Absent': 'A', 'Leave': 'L', 'Holiday': 'H',
    'Weekly off': 'WO', 'Worked on rest day': 'RW', 'Not marked yet': 'NM',
}
AP_STATUS = {'present': 'Present', 'absent': 'Absent', 'half_day': 'Half day', 'leave': 'Leave', 'holiday': 'Holiday',
             'weekly_off': 'Weekly off', 'missing_punch': 'Missing punch', 'not_marked': 'Not marked yet'}
WORKED = ('Present', 'Half day', 'Missing punch', 'Worked on rest day')
LEGEND = 'P present · HD half day · MP missing punch · A absent · L leave · H holiday · WO weekly off · RW worked on a rest day · NM not marked yet'


def _model(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _local_time(dt):
    if not dt:
        return ''
    try:
        from AttendancePlus.engine import local
        return local(dt).strftime('%H:%M')
    except Exception:
        return dt.strftime('%H:%M')


def _hhmm(t):
    return t.strftime('%H:%M') if t else ''


def _hours_of_row(a):
    if a.total_hours:
        return round(a.total_hours.total_seconds() / 3600, 2)
    if a.check_in_time and a.check_out_time:
        start = datetime.combine(a.date, a.check_in_time)
        end = datetime.combine(a.date, a.check_out_time)
        if end <= start:
            end += timedelta(days=1)
        return round((end - start).total_seconds() / 3600, 2)
    return 0.0


def weekend_days(e):
    try:
        from calendars.utils import get_employee_weekend_days
        wk = get_employee_weekend_days(e)
    except Exception:
        wk = set()
    return wk or {'Saturday', 'Sunday'}


def holidays(e, d_from, d_to):
    try:
        from calendars.utils import get_employee_holidays
        return get_employee_holidays(e, d_from, d_to) or set()
    except Exception:
        return set()


def holiday_names(d_from, d_to):
    H = _model('calendars.holiday')
    out = {}
    if H is None:
        return out
    for h in H.objects.filter(start_date__lte=d_to, end_date__gte=d_from):
        d = max(h.start_date, d_from)
        while d <= min(h.end_date, d_to):
            out.setdefault(d, h.description)
            d += timedelta(days=1)
    return out


def days(emps, d_from, d_to, today, with_late=True):
    """{employee id: [day dict, ...]} for every day from d_from to min(d_to, today), from the joining date on.

    day dict: date, status, code, source ('rules' / 'attendance' / 'calendar'), in, out, hours, late, early, ot, ot_approved,
              leave_type, leave (request or None), pending (pending leave or None), att_id, day_id, half_leave, note
    """
    emps = list(emps)
    ids = [e.id for e in emps]
    last = min(d_to, today)
    out = {}
    if not ids or last < d_from:
        return out
    AD = _model('AttendancePlus.AttendanceDay')
    rules, unmarked = {}, set()
    if AD is not None:
        for d in AD.objects.filter(employee_id__in=ids, date__gte=d_from, date__lte=last):
            if d.status == 'not_marked':   # v1.17.0 (audit L6): a day the attendance app marks "not marked" is not "absent"
                unmarked.add((d.employee_id, d.date))
                continue
            rules[(d.employee_id, d.date)] = d
    Att = _model('calendars.Attendance')
    att = {}
    for a in Att.objects.filter(employee_id__in=ids, date__gte=d_from, date__lte=last).select_related('shift'):
        att[(a.employee_id, a.date)] = a
    leave, pending = {}, {}
    LR = _model('calendars.employee_leave_request')
    for l in LR.objects.filter(employee_id__in=ids, status__in=('approved', 'pending'), start_date__lte=last, end_date__gte=d_from).select_related('leave_type'):
        d = max(l.start_date, d_from)
        while d <= min(l.end_date, last):
            (leave if l.status == 'approved' else pending)[(l.employee_id, d)] = l
            d += timedelta(days=1)
    ot, ot_ok = defaultdict(float), defaultdict(float)
    OT = _model('calendars.EmployeeOvertime')
    if OT is not None:
        for o in OT.objects.filter(employee_id__in=ids, date__gte=d_from, date__lte=last).values('employee_id', 'date', 'hours', 'approved'):
            h = float(o['hours'] or 0)
            ot[(o['employee_id'], o['date'])] += h
            if o['approved']:
                ot_ok[(o['employee_id'], o['date'])] += h
    late = {}
    if with_late and att:
        from .services import ShiftResolver, minutes_early, minutes_late
        legacy = [a for k, a in att.items() if k not in rules and a.check_in_time]
        if legacy:
            res = ShiftResolver({a.employee_id for a in legacy}, d_from, last)
            for a in legacy:
                sh = res.shift(a.employee_id, a.date, a)
                late[(a.employee_id, a.date)] = (minutes_late(a, sh), minutes_early(a, sh))
    # v1.17.0: days before the company recorded any attendance are not "absent" (shown as not marked)
    starts = []
    if AD is not None:
        starts.append(AD.objects.exclude(status='not_marked').order_by('date').values_list('date', flat=True).first())
    starts.append(Att.objects.order_by('date').values_list('date', flat=True).first())
    att_start = min([x for x in starts if x], default=None)
    for e in emps:
        wk = weekend_days(e)
        hol = holidays(e, d_from, last)
        first = max(d_from, e.emp_joined_date) if e.emp_joined_date else d_from
        rows = []
        d = first
        while d <= last:
            k = (e.id, d)
            r = {'date': d, 'source': 'calendar', 'in': '', 'out': '', 'hours': 0.0, 'late': 0, 'early': 0, 'ot': round(ot.get(k, 0), 2),
                 'ot_approved': round(ot_ok.get(k, 0), 2), 'leave_type': '', 'leave': leave.get(k), 'pending': pending.get(k),
                 'att_id': None, 'day_id': None, 'half_leave': False, 'note': ''}
            a = att.get(k)
            if a is not None:
                r['att_id'] = a.id
            lv = leave.get(k)
            if lv is not None:
                r['leave_type'] = lv.leave_type.name if lv.leave_type_id else 'Leave'
                r['half_leave'] = bool(lv.dis_half_day)
            ad = rules.get(k)
            if ad is not None:
                st = AP_STATUS.get(ad.status, 'Absent')
                r.update(source='rules', day_id=ad.id, **{'in': _local_time(ad.first_in), 'out': _local_time(ad.last_out)},
                         hours=round((ad.worked_minutes or 0) / 60, 2), late=ad.late_minutes or 0, early=ad.early_minutes or 0)
                if st in ('Weekly off', 'Holiday') and (ad.worked_minutes or ad.first_in):
                    st = 'Worked on rest day'
                if st == 'Leave' and not r['leave_type']:
                    r['leave_type'] = 'Leave'
            else:
                off = d.strftime('%A') in wk
                is_hol = d in hol
                if a is not None and a.check_in_time:
                    r.update(source='attendance', **{'in': _hhmm(a.check_in_time), 'out': _hhmm(a.check_out_time)}, hours=_hours_of_row(a))
                    lt = late.get(k, (0, 0))
                    r['late'], r['early'] = lt[0] or 0, lt[1] or 0
                    if off or is_hol:
                        st = 'Worked on rest day'
                    elif not a.check_out_time and d < today:
                        st = 'Missing punch'
                    elif lv is not None and lv.dis_half_day:
                        st = 'Half day'
                    else:
                        st = 'Present'
                elif off:
                    st = 'Weekly off'
                elif is_hol:
                    st = 'Holiday'
                elif lv is not None:
                    st = 'Half day' if lv.dis_half_day else 'Leave'
                elif d >= today:
                    st = 'Not marked yet'
                elif att_start is None or d < att_start:
                    st = 'Not marked yet'
                    r['note'] = 'Before attendance was recorded in ZEO'
                elif k in unmarked and a is None:
                    st = 'Not marked yet'
                    r['note'] = 'The attendance app has not marked this day yet'
                else:
                    st = 'Absent'
                    p = pending.get(k)
                    r['note'] = f'Leave waiting for approval ({p.leave_type})' if p is not None else 'No punch and no leave'
            r['status'] = st
            r['code'] = CODES[st]
            rows.append(r)
            d += timedelta(days=1)
        out[e.id] = rows
    return out


def counts(day_rows):
    """Totals of one employee's days (the monthly summary / dashboard figures)."""
    c = defaultdict(float)
    by_leave = defaultdict(float)
    for r in day_rows:
        st = r['status']
        c[st] += 1
        if st == 'Leave':
            by_leave[r['leave_type'] or 'Leave'] += 1
        elif st == 'Half day' and r['leave_type']:
            by_leave[r['leave_type']] += 0.5
        if r['late']:
            c['late_count'] += 1
            c['late_minutes'] += r['late']
        if r['early']:
            c['early_count'] += 1
            c['early_minutes'] += r['early']
        c['hours'] += r['hours'] or 0
        c['ot'] += r['ot'] or 0
        c['ot_approved'] += r['ot_approved'] or 0
    c['worked_days'] = sum(c[s] for s in WORKED)
    return c, by_leave
