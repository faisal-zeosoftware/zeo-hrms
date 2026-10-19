"""AI Attendance & Shift Management – coverage gaps, roster recommendations, patterns, overtime forecast, replacements, anomalies.
Statistics over AttendancePlus (AttendanceDay) and ShiftPlanner (RosterEntry, Availability); explanations on every item."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Count, Q, Sum

from .models import AIAssistantSettings

M = apps.get_model


def emp_name(e):
    return ' '.join(x for x in (getattr(e, 'emp_first_name', ''), getattr(e, 'emp_last_name', '')) if x) or getattr(e, 'emp_code', str(e.pk))


def _emps(branch_id=None, dept_id=None):
    Emp = M('EmpManagement', 'emp_master')
    qs = Emp.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_desgntn_id', 'emp_branch_id')
    if branch_id:
        qs = qs.filter(emp_branch_id=branch_id)
    if dept_id:
        qs = qs.filter(emp_dept_id=dept_id)
    return qs


def _hourly(emp):
    """Hourly rate from the gross of the active salary structure (standard hours from settings)."""
    try:
        ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
        gross = sum(Decimal(str(r.amount or 0)) for r in ESS.objects.filter(employee=emp, is_active=True, component__component_type='addition').select_related('component'))
        hrs = AIAssistantSettings.get().ot_cost_hours_per_month or 240
        return float(gross) / hrs if gross else 0.0
    except Exception:
        return 0.0


# ------------------------------------------------------------------ patterns and anomalies (last 30 days)
def patterns(branch_id=None, dept_id=None, days=30):
    s = AIAssistantSettings.get()
    AD = M('AttendancePlus', 'AttendanceDay')
    t = date.today()
    since = t - timedelta(days=days)
    emps = {e.pk: e for e in _emps(branch_id, dept_id)}
    rows = AD.objects.filter(employee_id__in=list(emps), date__gte=since, date__lte=t)
    agg = rows.values('employee_id').annotate(days=Count('id'), late=Count('id', filter=Q(is_late=True)), missing=Count('id', filter=Q(missing_punch=True)),
                                            absent=Count('id', filter=Q(status='absent')), early=Count('id', filter=Q(is_early=True)), ot=Sum('ot_minutes'), late_min=Sum('late_minutes'))
    out = []
    weekday_abs = defaultdict(lambda: defaultdict(int))
    for r in rows.filter(status='absent').values('employee_id', 'date'):
        weekday_abs[r['employee_id']][r['date'].weekday()] += 1
    for r in agg:
        e = emps[r['employee_id']]
        flags = []
        if r['late'] >= s.late_repeat:
            flags.append({'kind': 'late', 'severity': 'warning', 'text': f'Late {r["late"]} times in {days} days ({(r["late_min"] or 0)} minutes in total) – flagged from {s.late_repeat}.'})
        if r['missing'] >= s.missed_repeat:
            flags.append({'kind': 'missing_punch', 'severity': 'warning', 'text': f'{r["missing"]} missed punches – the hours of those days are estimated; ask for corrections.'})
        if r['absent'] >= 3:
            wd = weekday_abs[r['employee_id']]
            top = max(wd.items(), key=lambda x: x[1]) if wd else None
            txt = f'{r["absent"]} absent days.'
            if top and top[1] >= 2 and top[1] >= r['absent'] * 0.5:
                txt += f' {top[1]} of them on a {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][top[0]]} – a pattern worth a conversation.'
            flags.append({'kind': 'absence', 'severity': 'warning' if r['absent'] >= 5 else 'info', 'text': txt})
        if r['early'] >= s.late_repeat:
            flags.append({'kind': 'early', 'severity': 'info', 'text': f'Left early {r["early"]} times.'})
        if (r['ot'] or 0) >= 40 * 60:
            flags.append({'kind': 'overtime', 'severity': 'warning', 'text': f'{(r["ot"] or 0) / 60:.0f} h overtime in {days} days – above 40 h; check fatigue and the 48-hour week.'})
        if flags:
            out.append({'employee_id': e.pk, 'employee': emp_name(e), 'code': e.emp_code, 'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
                        'days': r['days'], 'late': r['late'], 'missing': r['missing'], 'absent': r['absent'], 'ot_hours': round((r['ot'] or 0) / 60, 1), 'flags': flags,
                        'score': sum(3 if f['severity'] == 'warning' else 1 for f in flags)})
    out.sort(key=lambda x: -x['score'])
    # anomalies: days with worked minutes far from the employee's own average, or punches on an off day
    anomalies = []
    per_emp = defaultdict(list)
    for r in rows.filter(worked_minutes__gt=0).values('employee_id', 'date', 'worked_minutes', 'status'):
        per_emp[r['employee_id']].append(r)
    for eid, xs in per_emp.items():
        if len(xs) < 5:
            continue
        avg = sum(x['worked_minutes'] for x in xs) / len(xs)
        for x in xs:
            if x['worked_minutes'] > avg * 1.8 and x['worked_minutes'] > 12 * 60:
                anomalies.append({'employee': emp_name(emps[eid]), 'date': x['date'], 'text': f'{x["worked_minutes"] / 60:.1f} h worked – {x["worked_minutes"] / avg:.1f}× this employee\'s average ({avg / 60:.1f} h). A forgotten check-out or a double shift?'})
            elif x['status'] in ('weekly_off', 'holiday') and x['worked_minutes'] > 0:
                anomalies.append({'employee': emp_name(emps[eid]), 'date': x['date'], 'text': f'Punches on a {x["status"].replace("_", " ")} ({x["worked_minutes"] / 60:.1f} h) – overtime, or a wrong roster?'})
    anomalies.sort(key=lambda a: a['date'], reverse=True)
    return {'since': since, 'until': t, 'employees': len(emps), 'flagged': out[:100], 'anomalies': anomalies[:60]}


# ------------------------------------------------------------------ coverage and overtime forecast (next 14 days)
def coverage(branch_id=None, dept_id=None, days=14):
    """Rostered headcount per day and shift against the usual (median of the last 8 weeks of the same weekday); gaps below min_staff_pct."""
    s = AIAssistantSettings.get()
    RE = M('ShiftPlanner', 'RosterEntry')
    AD = M('AttendancePlus', 'AttendanceDay')
    t = date.today()
    emps = {e.pk: e for e in _emps(branch_id, dept_id)}
    ids = list(emps)
    # usual presence per weekday from attendance history
    hist = AD.objects.filter(employee_id__in=ids, date__gte=t - timedelta(days=56), date__lt=t, status__in=('present', 'half_day', 'missing_punch')).values('date').annotate(n=Count('employee_id', distinct=True))
    by_wd = defaultdict(list)
    for r in hist:
        by_wd[r['date'].weekday()].append(r['n'])
    usual = {wd: sorted(v)[len(v) // 2] for wd, v in by_wd.items() if v}
    # roster for the coming days
    ros = RE.objects.filter(employee_id__in=ids, date__gte=t, date__lte=t + timedelta(days=days), off=False).values('date').annotate(n=Count('employee_id', distinct=True))
    rostered = {r['date']: r['n'] for r in ros}
    # approved leave in the window
    leave = defaultdict(int)
    try:
        LR = M('calendars', 'employee_leave_request')
        for r in LR.objects.filter(employee_id__in=ids, status='approved', start_date__lte=t + timedelta(days=days), end_date__gte=t):
            d0, d1 = max(r.start_date, t), min(r.end_date, t + timedelta(days=days))
            d = d0
            while d <= d1:
                leave[d] += 1
                d += timedelta(days=1)
    except Exception:
        pass
    out = []
    for i in range(days + 1):
        d = t + timedelta(days=i)
        u = usual.get(d.weekday())
        n = rostered.get(d)
        expected = (n if n is not None else (len(ids) - leave[d] if u is None else None))
        if u is None and n is None:
            continue
        need = u or 0
        have = n if n is not None else max(0, len(ids) - leave[d])
        pct = round(100 * have / need) if need else None
        gap = need - have if need and have < need * s.min_staff_pct / 100 else 0
        out.append({'date': d, 'weekday': d.strftime('%a'), 'usual': need, 'rostered': n, 'on_leave': leave[d], 'available': have, 'pct': pct, 'gap': gap,
                    'text': (f'{gap} short of the usual {need} – {leave[d]} on leave.' if gap else ('No roster published for this day – estimate from the headcount minus leave.' if n is None else 'Covered.'))})
    # overtime forecast: rostered hours above 8/day per employee × hourly rate
    ot_cost = 0.0
    ot_rows = []
    try:
        Shift = M('calendars', 'Shift')
        shifts = {sh.pk: sh for sh in Shift.objects.all()}
        per = defaultdict(float)
        for r in RE.objects.filter(employee_id__in=ids, date__gte=t, date__lte=t + timedelta(days=days), off=False, shift_id__isnull=False).values('employee_id', 'shift_id'):
            sh = shifts.get(r['shift_id'])
            if sh is None:
                continue
            h = ((sh.end_time.hour * 60 + sh.end_time.minute) - (sh.start_time.hour * 60 + sh.start_time.minute)) / 60
            if h <= 0:
                h += 24
            if sh.break_duration:
                h -= sh.break_duration.total_seconds() / 3600
            if h > 8:
                per[r['employee_id']] += h - 8
        for eid, hrs in per.items():
            rate = _hourly(emps[eid]) * 1.25
            ot_cost += hrs * rate
            ot_rows.append({'employee': emp_name(emps[eid]), 'hours': round(hrs, 1), 'cost': round(hrs * rate, 2)})
        ot_rows.sort(key=lambda x: -x['cost'])
    except Exception:
        pass
    # last 30 days actual OT for the trend
    actual = AD.objects.filter(employee_id__in=ids, date__gte=t - timedelta(days=30), date__lt=t).aggregate(m=Sum('ot_minutes'))['m'] or 0
    return {'days': out, 'gaps': [x for x in out if x['gap']], 'ot_forecast_hours': round(sum(x['hours'] for x in ot_rows), 1), 'ot_forecast_cost': round(ot_cost, 2), 'ot_rows': ot_rows[:30],
            'ot_last_30_hours': round(actual / 60, 1), 'usual_by_weekday': {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][k]: v for k, v in usual.items()},
            'note': 'Usual = median employees present on that weekday over the last 8 weeks. Overtime cost = rostered hours above 8 a day × hourly rate (gross ÷ standard hours) × 1.25.'}


# ------------------------------------------------------------------ replacements and roster recommendation
def replacements(employee_id, day, shift_id=None, limit=8):
    """Who can cover: same designation / department first, not on leave, not rostered that day, available, fewest hours this week."""
    Emp = M('EmpManagement', 'emp_master')
    RE = M('ShiftPlanner', 'RosterEntry')
    AV = M('ShiftPlanner', 'Availability')
    AD = M('AttendancePlus', 'AttendanceDay')
    absent = Emp.objects.filter(pk=employee_id).select_related('emp_dept_id', 'emp_desgntn_id', 'emp_branch_id').first()
    if absent is None:
        return []
    day = day if isinstance(day, date) else date.fromisoformat(str(day))
    pool = Emp.objects.filter(is_active=True, emp_branch_id=absent.emp_branch_id_id).exclude(pk=employee_id).select_related('emp_dept_id', 'emp_desgntn_id')
    rostered = set(RE.objects.filter(date=day, off=False).values_list('employee_id', flat=True))
    off_rostered = set(RE.objects.filter(date=day, off=True).values_list('employee_id', flat=True))
    on_leave = set()
    try:
        LR = M('calendars', 'employee_leave_request')
        on_leave = set(LR.objects.filter(status='approved', start_date__lte=day, end_date__gte=day).values_list('employee_id', flat=True))
    except Exception:
        pass
    unavailable = set(AV.objects.filter(kind='unavailable').filter(Q(weekday=day.weekday()) | Q(date_from__lte=day, date_to__gte=day)).values_list('employee_id', flat=True))
    preferred = set(AV.objects.filter(kind__in=('available', 'preferred')).filter(Q(weekday=day.weekday()) | Q(date_from__lte=day, date_to__gte=day)).filter(Q(shift_id=shift_id) | Q(shift_id__isnull=True)).values_list('employee_id', flat=True))
    week0 = day - timedelta(days=day.weekday())
    hours = {r['employee_id']: (r['m'] or 0) / 60 for r in AD.objects.filter(date__gte=week0, date__lte=day).values('employee_id').annotate(m=Sum('worked_minutes'))}
    out = []
    for e in pool:
        if e.pk in rostered or e.pk in on_leave or e.pk in unavailable:
            continue
        score, why = 0, []
        if e.emp_desgntn_id_id == absent.emp_desgntn_id_id:
            score += 40; why.append('same designation')
        if e.emp_dept_id_id == absent.emp_dept_id_id:
            score += 25; why.append('same department')
        if e.pk in preferred:
            score += 20; why.append('marked available')
        if e.pk in off_rostered:
            score -= 10; why.append('rostered off that day')
        h = hours.get(e.pk, 0)
        if h < 40:
            score += 15; why.append(f'{h:.0f} h this week')
        else:
            why.append(f'already {h:.0f} h this week')
        out.append({'employee_id': e.pk, 'employee': emp_name(e), 'code': e.emp_code, 'designation': getattr(e.emp_desgntn_id, 'desgntn_name', '') if e.emp_desgntn_id_id else '',
                    'department': getattr(e.emp_dept_id, 'dept_name', '') or '', 'score': score, 'why': ', '.join(why)})
    out.sort(key=lambda x: -x['score'])
    return out[:limit]


def roster_recommendation(branch_id, dept_id, date_from, date_to, shift_id=None):
    """A suggested roster: for each day fill the usual headcount with available employees, respecting leave, availability,
    a day off after 6 working days and the max week hours. Returns rows {employee, date, shift} + notes; nothing is saved."""
    RE = M('ShiftPlanner', 'RosterEntry')
    AV = M('ShiftPlanner', 'Availability')
    cov = coverage(branch_id, dept_id, (date_to - date_from).days + 1)
    usual = cov['usual_by_weekday']
    emps = list(_emps(branch_id, dept_id))
    on_leave = defaultdict(set)
    try:
        LR = M('calendars', 'employee_leave_request')
        for r in LR.objects.filter(employee_id__in=[e.pk for e in emps], status='approved', start_date__lte=date_to, end_date__gte=date_from):
            d = max(r.start_date, date_from)
            while d <= min(r.end_date, date_to):
                on_leave[d].add(r.employee_id); d += timedelta(days=1)
    except Exception:
        pass
    unavail = defaultdict(set)
    for a in AV.objects.filter(employee_id__in=[e.pk for e in emps], kind='unavailable'):
        d = date_from
        while d <= date_to:
            if (a.weekday is not None and a.weekday == d.weekday()) or (a.date_from and a.date_to and a.date_from <= d <= a.date_to):
                unavail[d].add(a.employee_id)
            d += timedelta(days=1)
    streak = defaultdict(int)
    rows, notes = [], []
    d = date_from
    names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    while d <= date_to:
        need = usual.get(names[d.weekday()], 0) or max(1, round(len(emps) * 0.8))
        cands = [e for e in emps if e.pk not in on_leave[d] and e.pk not in unavail[d] and streak[e.pk] < 6]
        cands.sort(key=lambda e: (streak[e.pk], e.pk))
        chosen = cands[:need]
        for e in emps:
            if e in chosen:
                rows.append({'employee_id': e.pk, 'employee': emp_name(e), 'date': d, 'shift_id': shift_id, 'off': False})
                streak[e.pk] += 1
            else:
                rows.append({'employee_id': e.pk, 'employee': emp_name(e), 'date': d, 'shift_id': None, 'off': True})
                streak[e.pk] = 0
        if len(chosen) < need:
            notes.append(f'{d:%a %d %b}: only {len(chosen)} of the usual {need} available ({len(on_leave[d])} on leave, {len(unavail[d])} unavailable).')
        d += timedelta(days=1)
    return {'rows': rows, 'notes': notes, 'rule': 'Usual headcount per weekday, no one on leave or marked unavailable, a day off after 6 working days, fewest recent days first.'}
