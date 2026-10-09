"""Payroll formula variables from the AttendancePlus daily results (v1.12.0).
Called by PayrollManagement.signals.get_formula_variables; names documented in PayrollManagement.formula.VARIABLES."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum, Count

from . import engine as E
from . import models as M

D0 = Decimal('0')


def _d(v, q='0.01'):
    return Decimal(str(v or 0)).quantize(Decimal(q))


def standard_hours(employee, start, end):
    """Planned hours from the shifts (net of unpaid break); working days without a shift use the rule's
    default hours or 8 h. Returns (hours, days_with_shift) – hours None when nothing could be planned."""
    from calendars.utils import get_employee_weekend_days, get_employee_holidays
    rule = E.rule_for(employee)
    try:
        wk = set(get_employee_weekend_days(employee))
        hol = set(get_employee_holidays(employee, start, end))
    except Exception:
        wk, hol = set(), set()
    total, shift_days, d = 0, 0, start
    any_shift = False
    while d <= end:
        sh = E.shift_info(employee, d, rule)
        if sh and sh.get('off'):
            d += timedelta(days=1)
            continue
        if d in hol or (not sh and d.strftime('%A') in wk):
            d += timedelta(days=1)
            continue
        if sh and sh.get('start') and sh.get('end'):
            any_shift = True
            shift_days += 1
            m = E.minutes(sh['end'] - sh['start'])
            if not rule.break_paid:
                m -= (sh.get('break_minutes') or rule.break_minutes or 0)
            total += max(0, m)
        else:
            total += 8 * 60
        d += timedelta(days=1)
    return (_d(Decimal(total) / 60) if any_shift else None), shift_days


def variables(employee, start, end, overtime_ids=None):
    from calendars.models import EmployeeOvertime
    qs = M.AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end))
    a = qs.aggregate(
        late_count=Count('id', filter=Q(is_late=True, penalty_waived=False)),
        late_minutes=Sum('late_minutes', filter=Q(penalty_waived=False)),
        early_count=Count('id', filter=Q(is_early=True, penalty_waived=False)),
        early_minutes=Sum('early_minutes', filter=Q(penalty_waived=False)),
        absent_days=Count('id', filter=Q(status='absent')),
        missing_punch_days=Count('id', filter=Q(status='missing_punch')),
        half_day_count=Count('id', filter=Q(status='half_day')),
        break_minutes=Sum('break_minutes'),
        worked=Sum('worked_minutes'),
    )
    out = {
        'late_count': _d(a['late_count'], '1'), 'late_minutes': _d(a['late_minutes'], '1'),
        'early_count': _d(a['early_count'], '1'), 'early_minutes': _d(a['early_minutes'], '1'),
        'absent_days': _d(a['absent_days'], '1'), 'missing_punch_days': _d(a['missing_punch_days'], '1'),
        'half_day_count': _d(a['half_day_count'], '1'), 'break_minutes': _d(a['break_minutes'], '1'),
        'net_worked_hours': _d(Decimal(a['worked'] or 0) / 60),
    }
    pen = D0
    for f in qs.exclude(flags={}).values_list('flags', flat=True):
        pen += Decimal(str((f or {}).get('late_penalty') or 0)) + Decimal(str((f or {}).get('early_penalty') or 0))
    out['late_penalty_days'] = _d(pen)

    # rate-weighted approved OT (rate of the day's result, else the OT policy / rule rate)
    rates = {d: r for d, r in qs.values_list('date', 'ot_rate')}
    rows = EmployeeOvertime.objects.filter(employee=employee, date__range=(start, end))
    paid = rows.filter(pk__in=overtime_ids) if overtime_ids is not None else rows.filter(Q(approved=True) | Q(source='MANUAL'))
    rule = E.rule_for(employee)
    w = {'NORMAL': D0, 'WEEKEND': D0, 'HOLIDAY': D0}
    for o in paid:
        t = o.ot_type or 'NORMAL'
        rate = rates.get(o.date)
        if not rate or rate == 1:
            rate = E._ot_rate(employee, rule, t)
        w[t] = w.get(t, D0) + Decimal(str(o.hours)) * Decimal(str(rate))
    out['normal_ot_weighted_hours'] = _d(w['NORMAL'])
    out['weekend_ot_weighted_hours'] = _d(w['WEEKEND'])
    out['holiday_ot_weighted_hours'] = _d(w['HOLIDAY'])
    out['ot_weighted_hours'] = _d(sum(w.values()))
    out['ot_pending_hours'] = _d(rows.filter(approved=False).exclude(source='MANUAL').aggregate(s=Sum('hours'))['s'])

    hrs, days = standard_hours(employee, start, end)
    out['scheduled_shift_days'] = _d(days, '1')
    if hrs is not None:
        out['standard_hours'] = hrs
    return out
