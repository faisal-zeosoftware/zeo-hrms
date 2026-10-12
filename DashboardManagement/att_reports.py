"""
v1.16.0 – attendance reports of the report centre, all read through attendance_data.days() so the daily results
(AttendancePlus) and the older attendance rows (calendars.Attendance) give the same answer everywhere.

  attendance            Attendance (daily)            one row per employee and day: status, code, in / out, hours, late, early, OT
  attendance-register   Attendance register           employee × day matrix with the status codes, plus totals
  attendance-summary    Attendance summary (monthly)  per employee and month: present, absent, leave by type, holidays, weekly off,
                                                      late / early count and minutes, OT, working hours, attendance %
  attendance-late-early Late in / early out (days)    every late or early day with minutes
  absence               Absence                       every working day with no punch and no approved leave
  overtime-summary      Overtime summary              per employee: OT hours by type, approved / waiting, days with OT

Filters: period (top), branch (top bar), and department / category / employee / status from the filter row or a drill-down.
"""
from collections import defaultdict
from datetime import date, timedelta

from . import attendance_data as AD
from .reports import EMP_COLS, M, drill, emp_cols, full_name, month_end, src

FILTERS = ['department', 'category', 'employee', 'status']
SUM_FILTERS = ['department', 'category', 'employee']


def _limit(run, days, what='3 months'):
    if (run.date_to - run.date_from).days > days:
        raise ValueError(f'Choose at most {what}.')


def _span(run):
    """The period, never later than today."""
    return run.date_from, min(run.date_to, run.today)


def _day_link(r, e):
    if r.get('day_id'):
        return {'_m': 'AttendancePlus.AttendanceDay', '_id': r['day_id']}
    if r.get('att_id'):
        return {'_m': 'calendars.Attendance', '_id': r['att_id']}
    if r.get('leave') is not None:
        return src(r['leave'])
    return src(e)


SOURCE = {'rules': 'Attendance rules', 'attendance': 'Attendance entry', 'calendar': 'Calendar (no punch)'}


def r_attendance(run):
    _limit(run, 92)
    d_from, d_to = _span(run)
    data = AD.days(run.employees, d_from, d_to, run.today)
    rows = []
    for e in run.employees:
        for r in data.get(e.id, []):
            rows.append({**emp_cols(e), 'date': r['date'], 'weekday': r['date'].strftime('%a'), 'status': r['status'], 'code': r['code'],
                         'leave_type': r['leave_type'], 'in': r['in'], 'out': r['out'], 'hours': r['hours'] or 0, 'late': r['late'] or 0,
                         'early': r['early'] or 0, 'ot': r['ot'] or 0, 'late_in': 'Yes' if r['late'] else 'No', 'early_out': 'Yes' if r['early'] else 'No',
                         'checked_in': 'Yes' if r['in'] else 'No', 'source': SOURCE[r['source']], 'note': r['note'], **_day_link(r, e),
                         '_cell': {'leave_type': src(r['leave'])} if r['leave'] is not None else {}})
    rows.sort(key=lambda x: (x['date'], x['employee']))
    return EMP_COLS + [('date', 'Date', 'date'), ('weekday', 'Day', 'text'), ('status', 'Status', 'text'), ('code', 'Code', 'text'),
                       ('leave_type', 'Leave type', 'text'), ('in', 'Check in', 'text'), ('out', 'Check out', 'text'), ('hours', 'Hours worked', 'number'),
                       ('late', 'Late (min)', 'number'), ('early', 'Early out (min)', 'number'), ('ot', 'OT hours', 'number'),
                       ('late_in', 'Late in', 'text'), ('early_out', 'Early out', 'text'), ('source', 'Source', 'text'), ('note', 'Note', 'text')], rows, \
        {'filters': FILTERS + ['late_in', 'early_out'], 'notes': [AD.LEGEND]}


def _cell_text(r):
    t = r['code']
    if r['late']:
        t += ' late'
    if r['early']:
        t += ' early'
    if r['ot']:
        t += f" +{r['ot']:g}h"
    return t


def r_attendance_register(run):
    """Employee × day: the status code of every day (P, A, L, H, WO, HD, MP, RW, NM) with late / early / OT, then totals."""
    _limit(run, 62, '2 months')
    d_from, d_to = _span(run)
    data = AD.days(run.employees, d_from, d_to, run.today)
    dates = []
    d = d_from
    while d <= d_to:
        dates.append(d)
        d += timedelta(days=1)
    rows = []
    for e in run.employees:
        lst = data.get(e.id)
        if not lst:
            continue
        who = {'employee': full_name(e)}
        per = {'from': d_from, 'to': d_to}
        row = {**emp_cols(e), **src(e), '_cell': {}}
        for r in lst:
            k = 'd' + r['date'].strftime('%Y%m%d')
            row[k] = _cell_text(r)
            row['_cell'][k] = drill('attendance', who, **{'from': r['date'], 'to': r['date']})
        c, by_leave = AD.counts(lst)
        row.update({'p': int(c['worked_days']), 'a': c['Absent'],
                    'l': c['Leave'], 'h': c['Holiday'], 'wo': c['Weekly off'], 'late': int(c['late_count']), 'early': int(c['early_count']),
                    'ot': round(c['ot'], 2), 'hours': round(c['hours'], 2)})
        row['_cell'].update({
            'p': drill('attendance', dict(who, status='|'.join(AD.WORKED)), **per), 'a': drill('attendance', dict(who, status='Absent'), **per),
            'l': drill('attendance', dict(who, status='Leave'), **per), 'h': drill('attendance', dict(who, status='Holiday'), **per),
            'wo': drill('attendance', dict(who, status='Weekly off'), **per), 'late': drill('attendance', dict(who, late_in='Yes'), **per),
            'early': drill('attendance', dict(who, early_out='Yes'), **per), 'ot': drill('overtime', who, **per), 'hours': drill('attendance', who, **per)})
        rows.append(row)
    cols = EMP_COLS + [('d' + x.strftime('%Y%m%d'), x.strftime('%d %a'), 'text') for x in dates] + [
        ('p', 'Present days', 'number'), ('a', 'Absent days', 'number'), ('l', 'Leave days', 'number'), ('h', 'Holidays', 'number'),
        ('wo', 'Weekly off', 'number'), ('late', 'Late days', 'number'), ('early', 'Early out days', 'number'), ('ot', 'OT hours', 'number'),
        ('hours', 'Hours worked', 'number')]
    return cols, rows, {'filters': SUM_FILTERS, 'notes': [AD.LEGEND + '. "late" / "early" = arrived late / left early, "+2h" = overtime hours. '
                                                          'Present days include half days, missing punches and work on a rest day. Select a day to see it.']}


def r_attendance_summary(run):
    """Per employee and calendar month of the period."""
    d_from, d_to = _span(run)
    if d_to < d_from:
        return EMP_COLS, [], {'notes': ['The period starts after today.']}
    data = AD.days(run.employees, d_from, d_to, run.today)
    leave_types = set()
    pre = []
    for e in run.employees:
        lst = data.get(e.id) or []
        by_month = defaultdict(list)
        for r in lst:
            by_month[r['date'].replace(day=1)].append(r)
        for m0 in sorted(by_month):
            c, by_leave = AD.counts(by_month[m0])
            leave_types.update(by_leave)
            pre.append((e, m0, by_month[m0], c, by_leave))
    lt = sorted(leave_types)
    rows = []
    for e, m0, lst, c, by_leave in pre:
        first, last = lst[0]['date'], lst[-1]['date']
        scheduled = len(lst) - c['Weekly off'] - c['Holiday'] - c['Worked on rest day'] - c['Not marked yet']
        present = c['Present'] + c['Missing punch'] + c['Half day']
        attended = c['Present'] + c['Missing punch'] + c['Half day'] * 0.5
        row = {**emp_cols(e), 'month': m0.strftime('%b %Y'), 'from': first, 'to': last, 'scheduled': scheduled, 'present': present,
               'half': c['Half day'], 'missing': c['Missing punch'], 'absent': c['Absent'], 'leave': c['Leave'],
               'holiday': c['Holiday'], 'weekend': c['Weekly off'], 'rest_worked': c['Worked on rest day'],
               'late': int(c['late_count']), 'late_min': int(c['late_minutes']), 'early': int(c['early_count']), 'early_min': int(c['early_minutes']),
               'ot': round(c['ot'], 2), 'ot_ok': round(c['ot_approved'], 2), 'hours': round(c['hours'], 2),
               'pct': round(100 * attended / scheduled, 1) if scheduled else 0, **src(e)}
        for t in lt:
            row['lt_' + t] = by_leave.get(t, 0)
        who, per = {'employee': full_name(e)}, {'from': first, 'to': last}
        row['_cell'] = {
            'present': drill('attendance', dict(who, status='Present|Missing punch|Half day'), **per), 'half': drill('attendance', dict(who, status='Half day'), **per),
            'missing': drill('attendance', dict(who, status='Missing punch'), **per), 'absent': drill('attendance', dict(who, status='Absent'), **per),
            'leave': drill('attendance', dict(who, status='Leave'), **per), 'holiday': drill('attendance', dict(who, status='Holiday'), **per),
            'weekend': drill('attendance', dict(who, status='Weekly off'), **per), 'rest_worked': drill('attendance', dict(who, status='Worked on rest day'), **per),
            'scheduled': drill('attendance', dict(who, status='Present|Missing punch|Half day|Absent|Leave'), **per),
            'late': drill('attendance', dict(who, late_in='Yes'), **per), 'late_min': drill('attendance', dict(who, late_in='Yes'), **per),
            'early': drill('attendance', dict(who, early_out='Yes'), **per), 'early_min': drill('attendance', dict(who, early_out='Yes'), **per),
            'ot': drill('overtime', who, **per), 'ot_ok': drill('overtime', dict(who, approved='Yes'), **per), 'hours': drill('attendance', who, **per)}
        for t in lt:
            row['_cell']['lt_' + t] = drill('attendance', dict(who, leave_type=t), **per)
        rows.append(row)
    cols = EMP_COLS + [('month', 'Month', 'text'), ('from', 'From', 'date'), ('to', 'To', 'date'), ('scheduled', 'Working days', 'number'),
                       ('present', 'Present', 'number'), ('half', 'Half days', 'number'), ('missing', 'Missing punch', 'number'),
                       ('absent', 'Absent', 'number'), ('leave', 'Leave (full days)', 'number')] + \
        [('lt_' + t, f'{t} (days)', 'number') for t in lt] + \
        [('holiday', 'Holidays', 'number'), ('weekend', 'Weekly off', 'number'), ('rest_worked', 'Worked on rest day', 'number'),
         ('late', 'Late days', 'number'), ('late_min', 'Late (min)', 'number'), ('early', 'Early out days', 'number'), ('early_min', 'Early out (min)', 'number'),
         ('ot', 'OT hours', 'number'), ('ot_ok', 'OT approved (h)', 'number'), ('hours', 'Hours worked', 'number'), ('pct', 'Attendance %', 'number')]
    return cols, rows, {'filters': SUM_FILTERS + ['month'], 'no_totals': ['pct'],
                        'notes': ['Working days = days in the period after joining, up to today, without weekly off and holidays. '
                                  'Attendance % = (present + missing punch + ½ half day) ÷ working days. Leave by type counts a half-day leave as ½.']}


def r_attendance_late_early(run):
    _limit(run, 92)
    d_from, d_to = _span(run)
    data = AD.days(run.employees, d_from, d_to, run.today)
    rows = []
    for e in run.employees:
        for r in data.get(e.id, []):
            if not (r['late'] or r['early']):
                continue
            kind = 'Late in and early out' if r['late'] and r['early'] else ('Late in' if r['late'] else 'Early out')
            rows.append({**emp_cols(e), 'date': r['date'], 'weekday': r['date'].strftime('%a'), 'type': kind, 'in': r['in'], 'out': r['out'],
                         'late': r['late'], 'early': r['early'], 'total': r['late'] + r['early'], 'hours': r['hours'], 'status': r['status'],
                         'source': SOURCE[r['source']], **_day_link(r, e)})
    rows.sort(key=lambda x: (x['date'], x['employee']))
    return EMP_COLS + [('date', 'Date', 'date'), ('weekday', 'Day', 'text'), ('type', 'Type', 'text'), ('in', 'Check in', 'text'), ('out', 'Check out', 'text'),
                       ('late', 'Late (min)', 'number'), ('early', 'Early out (min)', 'number'), ('total', 'Total (min)', 'number'),
                       ('hours', 'Hours worked', 'number'), ('status', 'Day status', 'text'), ('source', 'Source', 'text')], rows, \
        {'filters': FILTERS + ['type'], 'notes': ['Late = check-in more than 15 minutes after the shift start (or the minutes of the attendance rules); '
                                                  'early out = check-out more than 15 minutes before the shift end.']}


def r_absence(run):
    _limit(run, 92)
    d_from, d_to = _span(run)
    data = AD.days(run.employees, d_from, d_to, run.today)
    rows = []
    for e in run.employees:
        for r in data.get(e.id, []):
            if r['status'] != 'Absent':
                continue
            p = r['pending']
            rows.append({**emp_cols(e), 'date': r['date'], 'weekday': r['date'].strftime('%A'), 'days': 1,
                         'note': r['note'] or 'No punch and no leave', **(src(p) if p else _day_link(r, e))})
    rows.sort(key=lambda x: (x['date'], x['employee']))
    return EMP_COLS + [('date', 'Date', 'date'), ('weekday', 'Day', 'text'), ('days', 'Days absent', 'number'), ('note', 'Note', 'text')], rows, \
        {'filters': SUM_FILTERS}


def r_overtime_summary(run):
    """Per employee over the whole period: OT hours by type, approved and waiting, days with OT."""
    agg = {}
    emps = {}
    for o in M('calendars', 'EmployeeOvertime').objects.filter(run.emp_q(), run.in_period('date')).select_related(
            'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id'):
        emps[o.employee_id] = o.employee
        a = agg.setdefault(o.employee_id, {'normal': 0.0, 'weekend': 0.0, 'holiday': 0.0, 'approved': 0.0, 'waiting': 0.0, 'days': set()})
        h = float(o.hours or 0)
        a[{'WEEKEND': 'weekend', 'HOLIDAY': 'holiday'}.get(o.ot_type or 'NORMAL', 'normal')] += h
        a['approved' if o.approved else 'waiting'] += h
        a['days'].add(o.date)
    rows = []
    per = {'from': run.date_from, 'to': run.date_to}
    for eid, a in sorted(agg.items(), key=lambda x: emps[x[0]].emp_code):
        e = emps[eid]
        who = {'employee': full_name(e)}
        total = a['normal'] + a['weekend'] + a['holiday']
        rows.append({**emp_cols(e), 'days': len(a['days']), 'normal': round(a['normal'], 2), 'weekend': round(a['weekend'], 2), 'holiday': round(a['holiday'], 2),
                     'hours': round(total, 2), 'approved': round(a['approved'], 2), 'waiting': round(a['waiting'], 2), **src(e),
                     '_cell': {'days': drill('overtime', who, **per), 'normal': drill('overtime', dict(who, type='Normal OT|'), **per),
                               'weekend': drill('overtime', dict(who, type='Weekend OT'), **per), 'holiday': drill('overtime', dict(who, type='Holiday OT'), **per),
                               'hours': drill('overtime', who, **per), 'approved': drill('overtime', dict(who, approved='Yes'), **per),
                               'waiting': drill('overtime', dict(who, approved='No'), **per)}})
    return EMP_COLS + [('days', 'Days with OT', 'number'), ('normal', 'Normal OT (h)', 'number'), ('weekend', 'Rest day OT (h)', 'number'),
                       ('holiday', 'Holiday OT (h)', 'number'), ('hours', 'Total OT (h)', 'number'), ('approved', 'Approved (h)', 'number'),
                       ('waiting', 'Waiting for approval (h)', 'number')], rows, {'filters': SUM_FILTERS}


REPORTS = {
    'attendance': ('Attendance (daily)', 'Leave & attendance', 'One row per employee and day: status, check-in and check-out, hours, late, early out and overtime (up to 3 months). Reads the attendance rules results and the attendance entries.', r_attendance, 'month', ['view_attendancereport', 'view_attendance']),
    'attendance-register': ('Attendance register', 'Leave & attendance', 'Employee × day grid with the status code of every day (P, A, L, H, WO …), late, early out and overtime, with totals per employee (up to 2 months).', r_attendance_register, 'month', ['view_attendancereport', 'view_attendance']),
    'attendance-summary': ('Attendance summary (monthly)', 'Leave & attendance', 'Per employee and month: working days, present, absent, leave by type, holidays, weekly off, late and early out (days and minutes), overtime and hours worked – ready for payroll.', r_attendance_summary, 'month', ['view_attendancereport', 'view_attendance']),
    'attendance-late-early': ('Late in and early out (days)', 'Leave & attendance', 'Every day an employee came late or left early, with the minutes (up to 3 months).', r_attendance_late_early, 'month', ['view_attendancereport', 'view_attendance']),
    'absence': ('Absence', 'Leave & attendance', 'Working days with no punch and no approved leave (weekly off and holidays excluded) – up to 3 months.', r_absence, 'month', ['view_attendancereport', 'view_attendance']),
    'overtime-summary': ('Overtime summary', 'Leave & attendance', 'Per employee: overtime hours by type (normal, rest day, holiday), approved and waiting, days with overtime.', r_overtime_summary, 'month', ['view_employeeovertime', 'view_attendancereport']),
}
REPORT_MODELS = {
    'attendance': {'calendars.Attendance', 'AttendancePlus.AttendanceDay', 'EmpManagement.emp_master', 'calendars.employee_leave_request'},
    'attendance-register': {'EmpManagement.emp_master'}, 'attendance-summary': {'EmpManagement.emp_master'},
    'attendance-late-early': {'calendars.Attendance', 'AttendancePlus.AttendanceDay', 'EmpManagement.emp_master'},
    'absence': {'EmpManagement.emp_master', 'calendars.employee_leave_request', 'calendars.Attendance', 'AttendancePlus.AttendanceDay'},
    'overtime-summary': {'EmpManagement.emp_master'},
}
