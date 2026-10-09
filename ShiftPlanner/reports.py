"""
v1.12.0 – shift reports for the report centre (DashboardManagement/reports.py conventions: `def r_x(run): return columns, rows`).
Registered at the end of DashboardManagement/reports.py ('shift-roster', 'shift-requests').
"""
from datetime import timedelta


def _h():
    from DashboardManagement import reports as R
    return R


def r_shift_roster(run):
    """Every planned day of the employees the user may see: shift, times, hours, roster and status (published rosters)."""
    R = _h()
    from .models import RosterEntry, ShiftRule
    from .resolver import describe
    from .services import shifts_by_id
    if run.date_from and run.date_to and (run.date_to - run.date_from).days > 92:
        raise ValueError('Choose at most 3 months.')
    emps = {e.pk: e for e in run.all_employees}
    shifts = shifts_by_id()
    rules = {x.shift_id: x for x in ShiftRule.objects.all()}
    qs = RosterEntry.objects.filter(employee_id__in=list(emps), period__status='published').filter(run.in_period('date')) \
        .select_related('period').order_by('date', 'employee_id')
    rows = []
    for x in qs:
        e = emps.get(x.employee_id)
        sh = shifts.get(x.shift_id) if x.shift_id and not x.off else None
        info = describe(sh, rules.get(sh.pk) if sh else None, x.date, 'roster') if sh else None
        rows.append({**R.emp_cols(e), 'date': x.date, 'weekday': x.date.strftime('%A'), 'shift': sh.name if sh else 'Day off',
                     'start': sh.start_time.strftime('%H:%M') if sh and sh.start_time else '', 'end': sh.end_time.strftime('%H:%M') if sh and sh.end_time else '',
                     'hours': R.money(info['hours']) if info else 0, 'night': R.nice(bool(info and info['night_shift'])),
                     'roster': x.period.name, 'source': x.get_source_display(), 'changed': R.nice(x.changed_after_publish), **R.src(e)})
    return R.EMP_COLS + [('date', 'Date', 'date'), ('weekday', 'Day', 'text'), ('shift', 'Shift', 'text'), ('start', 'Start', 'text'),
                         ('end', 'End', 'text'), ('hours', 'Hours', 'number'), ('night', 'Night shift', 'text'), ('roster', 'Roster', 'text'),
                         ('source', 'Planned by', 'text'), ('changed', 'Changed after publishing', 'text')], rows


def r_shift_requests(run):
    """Shift swap, change and cancellation requests plus open-shift claims, with status and approver."""
    R = _h()
    from .models import OpenShiftClaim, ShiftRequest
    from .services import shift_label, shifts_by_id
    emps = {e.pk: e for e in run.all_employees}
    shifts = shifts_by_id()
    users = dict(R.M('UserManagement', 'CustomUser').objects.values_list('id', 'username'))
    rows = []
    for q in ShiftRequest.objects.filter(employee_id__in=list(emps)).filter(run.in_period('date')).order_by('date'):
        o = emps.get(q.swap_employee_id) if q.swap_employee_id else None
        rows.append({**R.emp_cols(emps.get(q.employee_id)), 'kind': q.get_kind_display(), 'date': q.date,
                     'from_shift': shift_label(shifts.get(q.from_shift_id)) if q.from_shift_id else 'Off',
                     'to_shift': shift_label(shifts.get(q.to_shift_id)) if q.to_shift_id else (('Off' if q.kind == 'cancel' else '')),
                     'colleague': R.full_name(o) if o else '', 'reason': q.reason, 'status': q.get_status_display(),
                     'decided_by': users.get(q.decided_by_id, ''), 'decided_at': q.decided_at.date() if q.decided_at else None,
                     'note': q.decision_note})
    for c in OpenShiftClaim.objects.filter(employee_id__in=list(emps)).filter(run.in_period('open_shift__date')).select_related('open_shift'):
        rows.append({**R.emp_cols(emps.get(c.employee_id)), 'kind': 'Open shift claim', 'date': c.open_shift.date, 'from_shift': '',
                     'to_shift': shift_label(shifts.get(c.open_shift.shift_id)), 'colleague': '', 'reason': c.note, 'status': c.get_status_display(),
                     'decided_by': users.get(c.decided_by_id, ''), 'decided_at': c.decided_at.date() if c.decided_at else None, 'note': c.decision_note})
    rows.sort(key=lambda r: (r['date'], r['employee']))
    return R.EMP_COLS + [('kind', 'Request', 'text'), ('date', 'Date', 'date'), ('from_shift', 'From shift', 'text'), ('to_shift', 'To shift', 'text'),
                         ('colleague', 'Swap with', 'text'), ('reason', 'Reason', 'text'), ('status', 'Status', 'text'),
                         ('decided_by', 'Decided by', 'text'), ('decided_at', 'Decided on', 'date'), ('note', 'Decision note', 'text')], rows
