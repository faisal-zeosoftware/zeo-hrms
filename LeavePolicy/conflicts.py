"""v1.51.0 – leave conflicts: what the employee sees before sending a leave request, and the approver next to it.

* errors   – the leave rules (LeavePolicy.engine.validate_request) and a blocking blackout period;
* warnings – colleagues of the same designation in the department away on the same days, too few of the department at
  work, a blackout period that warns, rostered shifts left without cover, a public holiday next to the leave.
"""
from datetime import date, timedelta

from django.apps import apps
from django.core.exceptions import ValidationError

from Translations.i18n import tr

from . import engine as E
from .models import BlackoutPeriod, ConflictSettings

M = apps.get_model


def _name(e):
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)


def _days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def blackouts(emp, lt_id, a, b):
    out = []
    for p in BlackoutPeriod.objects.filter(active=True, start_date__lte=b, end_date__gte=a):
        if p.branch_ids and emp.emp_branch_id_id not in p.branch_ids:
            continue
        if p.department_ids and emp.emp_dept_id_id not in p.department_ids:
            continue
        if lt_id and int(lt_id) in [int(x) for x in p.exempt_leave_type_ids or []]:
            continue
        out.append(p)
    return out


def blackout_errors(req):
    """Blocking blackout periods for a request the employee makes (HR entering leave is not blocked)."""
    emp = req.employee
    by_hr = bool(getattr(req, 'created_by_id', None)) and req.created_by_id != getattr(emp, 'users_id', None)
    if by_hr:
        return []
    return [tr('No leave from {0:%d/%m/%Y} to {1:%d/%m/%Y}: {2}.', p.start_date, p.end_date, p.name) + (f' {p.note}' if p.note else '')
            for p in blackouts(emp, req.leave_type_id, req.start_date, req.end_date) if p.action == 'block']


def team_conflicts(emp, a, b, exclude_id=None):
    """(same-role colleagues away [{name, from, to, status, type}], department presence [{date, present, total, pct}])."""
    st = ConflictSettings.get()
    statuses = ('approved', 'pending') if st.count_pending else ('approved',)
    LR = M('calendars', 'employee_leave_request')
    Emp = M('EmpManagement', 'emp_master')
    dept = list(Emp.objects.filter(is_active=True, emp_dept_id=emp.emp_dept_id_id).exclude(pk=emp.pk)) if emp.emp_dept_id_id else []
    if not dept:
        return [], []
    ids = [e.pk for e in dept]
    reqs = list(LR.objects.filter(employee_id__in=ids, status__in=statuses, start_date__lte=b, end_date__gte=a).exclude(pk=exclude_id or 0)
                .select_related('leave_type', 'employee'))
    same = [r for r in reqs if emp.emp_desgntn_id_id and r.employee.emp_desgntn_id_id == emp.emp_desgntn_id_id]
    same_rows = [{'employee_id': r.employee_id, 'name': _name(r.employee), 'from': r.start_date, 'to': r.end_date, 'status': r.status,
                  'type': r.leave_type.name} for r in same]
    away = {}
    for r in reqs:
        for d in _days(max(r.start_date, a), min(r.end_date, b)):
            away.setdefault(d, set()).add(r.employee_id)
    wk, hol = E._weekend_days(emp), E._holidays(emp)
    total = len(dept) + 1
    presence = []
    for d in _days(a, b):
        if d.strftime('%A').lower() in wk or d in hol:
            continue
        present = total - 1 - len(away.get(d, ()))   # the employee asking is away too
        presence.append({'date': d, 'present': present, 'total': total, 'pct': round(present * 100 / total)})
    return same_rows, presence


def check(emp, lt, start, end, half=False, exclude_id=None, created_by_id=None, document=False):
    """{days, errors, warnings: [{code, level, text, people?}]} for a leave the employee would ask for."""
    LR = M('calendars', 'employee_leave_request')
    req = LR(employee=emp, leave_type=lt, start_date=start, end_date=end, dis_half_day=bool(half), status='pending',
             created_by_id=created_by_id if created_by_id is not None else emp.users_id)
    if exclude_id:
        req.pk = exclude_id
    if document:
        req.lv_document = 'x'   # the form has a document: the "needs a document" rule is satisfied
    errors = []
    try:
        E.validate_request(req)
    except ValidationError as ex:
        errors = [str(m) for m in ex.messages]
    errors += [e for e in blackout_errors(req) if e not in errors]
    line = E.line_for(emp, lt.pk)
    mode = line.count_days if line else ('calendar' if getattr(lt, 'include_weekend', False) else 'working')
    try:
        days = E.leave_days(emp, start, end, mode, bool(half))
    except Exception:
        days = (end - start).days + 1
    warnings = []
    st = ConflictSettings.get()
    same, presence = team_conflicts(emp, start, end, exclude_id)
    if same and len({x['employee_id'] for x in same}) >= max(st.same_role_max, 1):
        warnings.append({'code': 'same_role', 'level': 'warn', 'people': same,
                         'text': tr('{0} of the same role in your department will be away: {1}.', len({x["employee_id"] for x in same}),
                                    ', '.join(f"{x['name']} {x['from']:%d/%m}–{x['to']:%d/%m}{'' if x['status'] == 'approved' else ' (waiting)'}" for x in same[:5]))})
    low = [p for p in presence if p['pct'] < st.dept_min_present_pct]
    if low:
        worst = min(low, key=lambda p: p['pct'])
        warnings.append({'code': 'staffing', 'level': 'warn', 'days': low,
                         'text': tr('Only {0} of {1} in the department at work on {2:%d/%m/%Y} ({3}%, the minimum is {4}%); {5} day(s) below the minimum.',
                                    worst['present'], worst['total'], worst['date'], worst['pct'], st.dept_min_present_pct, len(low))})
    for p in blackouts(emp, lt.pk, start, end):
        if p.action == 'warn':
            warnings.append({'code': 'blackout', 'level': 'warn', 'text': tr('{0} ({1:%d/%m/%Y} – {2:%d/%m/%Y}): leave is discouraged.', p.name, p.start_date, p.end_date)
                             + (f' {p.note}' if p.note else '')})
    try:
        from ShiftPlanner.services import leave_roster_conflicts
        rc = leave_roster_conflicts(req)
        if rc:
            warnings.append({'code': 'roster', 'level': 'warn' if rc.get('short_days') else 'info', 'text': rc['text']})
    except Exception:
        pass
    hol = E._holidays(emp)
    near = sorted(d for d in (start - timedelta(days=1), end + timedelta(days=1)) if d in hol)
    if near:
        warnings.append({'code': 'holiday', 'level': 'info', 'text': tr('Next to the public holiday on {0}.', ', '.join(f'{d:%d/%m/%Y}' for d in near))})
    return {'days': days, 'errors': errors, 'warnings': warnings}


def for_approver(req):
    """Short text of the team conflicts of a pending request (shown next to the roster conflict)."""
    try:
        same, presence = team_conflicts(req.employee, req.start_date, req.end_date, req.pk)
    except Exception:
        return None
    st = ConflictSettings.get()
    parts = []
    if same and len({x['employee_id'] for x in same}) >= max(st.same_role_max, 1):
        parts.append(tr('Same role away: {0}.', ', '.join(f"{x['name']} {x['from']:%d/%m}–{x['to']:%d/%m}" for x in same[:4])))
    low = [p for p in presence if p['pct'] < st.dept_min_present_pct]
    if low:
        w = min(low, key=lambda p: p['pct'])
        parts.append(tr('Department at {0}% on {1:%d/%m} ({2} of {3}).', w['pct'], w['date'], w['present'], w['total']))
    return {'text': ' '.join(parts), 'same_role': same, 'low_days': len(low)} if parts else None
