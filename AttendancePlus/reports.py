"""v1.12.0 – attendance reports for the report centre (DashboardManagement/reports.py conventions:
`def r_x(run): return columns, rows`). Registered at the end of DashboardManagement/reports.py."""
from . import engine as E
from . import models as M


def _R():
    from DashboardManagement import reports as R
    return R


def _t(v):
    return E.local(v).strftime('%H:%M') if v else ''


def _days(run, **flt):
    R = _R()
    if run.date_from and run.date_to and (run.date_to - run.date_from).days > 92:
        raise ValueError('Choose at most 3 months.')
    emps = {e.pk: e for e in run.all_employees}
    qs = M.AttendanceDay.objects.filter(employee_id__in=list(emps), **flt).filter(run.in_period('date')).order_by('date', 'employee_id')
    return R, emps, qs


def r_att_daily_status(run):
    R, emps, qs = _days(run)
    rows = []
    for d in qs:
        e = emps.get(d.employee_id)
        rows.append({**R.emp_cols(e), 'date': d.date, 'shift': d.shift_name, 'status': d.get_status_display(), 'in': _t(d.first_in),
                     'out': _t(d.last_out), 'worked': round(d.worked_minutes / 60, 2), 'break': d.break_minutes, 'late': d.late_minutes,
                     'early': d.early_minutes, 'ot': round((d.ot_minutes + d.period_ot_minutes) / 60, 2),
                     'night': R.nice(d.is_night_shift), 'sources': ', '.join(d.sources or []), **R.src(e)})
    return R.EMP_COLS + [('date', 'Date', 'date'), ('shift', 'Shift', 'text'), ('status', 'Status', 'text'), ('in', 'First in', 'text'),
                         ('out', 'Last out', 'text'), ('worked', 'Worked hours', 'number'), ('break', 'Break (min)', 'number'),
                         ('late', 'Late (min)', 'number'), ('early', 'Early (min)', 'number'), ('ot', 'OT hours', 'number'),
                         ('night', 'Night shift', 'text'), ('sources', 'Punch methods', 'text')], rows


def r_att_late_early(run):
    from django.db.models import Q
    R, emps, qs = _days(run)
    rows = []
    for d in qs.filter(Q(is_late=True) | Q(is_early=True)):
        e = emps.get(d.employee_id)
        rows.append({**R.emp_cols(e), 'date': d.date, 'shift': d.shift_name, 'start': _t(d.shift_start), 'in': _t(d.first_in),
                     'late': d.late_minutes, 'end': _t(d.shift_end), 'out': _t(d.last_out), 'early': d.early_minutes,
                     'status': d.get_status_display(), 'waived': R.nice(d.penalty_waived), **R.src(e)})
    return R.EMP_COLS + [('date', 'Date', 'date'), ('shift', 'Shift', 'text'), ('start', 'Shift start', 'text'), ('in', 'First in', 'text'),
                         ('late', 'Late (min)', 'number'), ('end', 'Shift end', 'text'), ('out', 'Last out', 'text'),
                         ('early', 'Early (min)', 'number'), ('status', 'Day status', 'text'), ('waived', 'Penalty waived', 'text')], rows


def r_att_missing_punch(run):
    from django.db.models import Q
    R, emps, qs = _days(run)
    rows = []
    for d in qs.filter(Q(missing_punch=True) | Q(status='missing_punch')):
        e = emps.get(d.employee_id)
        cr = M.CorrectionRequest.objects.filter(employee_id=d.employee_id, date=d.date).exclude(status='cancelled').first()
        rows.append({**R.emp_cols(e), 'date': d.date, 'shift': d.shift_name, 'in': _t(d.first_in),
                     'notified': R.nice(bool(d.notified_missing_at)), 'correction': cr.get_status_display() if cr else 'Not requested', **R.src(e)})
    return R.EMP_COLS + [('date', 'Date', 'date'), ('shift', 'Shift', 'text'), ('in', 'Clock in', 'text'),
                         ('notified', 'Employee notified', 'text'), ('correction', 'Correction', 'text')], rows


def r_att_corrections(run):
    R = _R()
    emps = {e.pk: e for e in run.all_employees}
    rows = []
    for c in M.CorrectionRequest.objects.filter(employee_id__in=list(emps)).filter(run.in_period('date')).order_by('date'):
        e = emps.get(c.employee_id)
        rows.append({**R.emp_cols(e), 'date': c.date, 'kind': c.get_kind_display(), 'in': _t(c.proposed_in), 'out': _t(c.proposed_out),
                     'reason': c.reason, 'status': c.get_status_display(), 'manager_note': c.manager_note, 'hr_note': c.hr_note,
                     'requested': c.created_at.date(), **R.src(e)})
    return R.EMP_COLS + [('date', 'Date', 'date'), ('kind', 'Type', 'text'), ('in', 'Proposed in', 'text'), ('out', 'Proposed out', 'text'),
                         ('reason', 'Reason', 'text'), ('status', 'Status', 'text'), ('manager_note', 'Manager note', 'text'),
                         ('hr_note', 'HR note', 'text'), ('requested', 'Requested on', 'date')], rows


REPORTS = {
    'att-daily-status': ('Daily attendance status', 'Leave & attendance', 'Status of every employee and day from the attendance rules: in / out, worked hours, break, late, early, OT, night shift and punch method.', r_att_daily_status, 'month', ['view_attendance', 'view_attendancereport']),
    'att-late-early': ('Late arrivals & early departures (rules)', 'Leave & attendance', 'Days late or left early against the planned shift, in minutes, with waived penalties.', r_att_late_early, 'month', ['view_attendance', 'view_attendancereport']),
    'att-missing-punch': ('Missing punches (rules)', 'Leave & attendance', 'Clock-ins without a clock-out after the shift end, whether the employee was told and the correction status.', r_att_missing_punch, 'month', ['view_attendance', 'view_attendancereport']),
    'att-corrections': ('Attendance corrections', 'Leave & attendance', 'Correction requests with proposed times, manager and HR decisions.', r_att_corrections, 'month', ['view_attendance', 'view_attendancereport']),
}
