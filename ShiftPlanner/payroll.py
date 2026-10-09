"""
v1.12.0 – shift figures for payroll formulas.

    variables(employee, start, end) -> {name: Decimal}

    shift_days                scheduled shift days (published roster / override / schedule; days off and leave days left out)
    night_shift_days          of those, night / cross-midnight shifts
    worked_shift_days         shift days with an attendance check-in
    worked_night_shift_days   night shift days with an attendance check-in
    shift_allowance_total     sum of the shift allowance per day of the shifts worked (worked_shift_days)
    night_allowance_total     sum of the night allowance per night worked (worked_night_shift_days)
    scheduled_shift_hours     net hours of the shift days (shift length − break)
    roster_off_days           planned days off in the period (from shift information, weekend calendar not counted)
    leave_on_shift_days       shift days the employee was on approved leave
    shift_changes             approved swap / change / cancellation requests of the employee (also as swap partner) in the period

The lead merges them into PayrollManagement.signals.get_formula_variables (see INTEGRATION.md).
"""
from datetime import timedelta
from decimal import Decimal

from .resolver import Book, ZERO, _day, _emp

NAMES = ('shift_days', 'night_shift_days', 'worked_shift_days', 'worked_night_shift_days', 'shift_allowance_total',
         'night_allowance_total', 'scheduled_shift_hours', 'roster_off_days', 'leave_on_shift_days', 'shift_changes')


def variables(employee, start, end):
    out = {k: ZERO for k in NAMES}
    emp = _emp(employee)
    a, b = _day(start), _day(end)
    if emp is None or a is None or b is None or b < a:
        return out
    from calendars.models import Attendance
    from .models import ShiftRequest
    from .services import leave_map
    book = Book(a, b, [emp.pk])
    worked = set(Attendance.objects.filter(employee=emp, date__gte=a, date__lte=b, check_in_time__isnull=False).values_list('date', flat=True))
    leave = {d for (eid, d), v in leave_map([emp.pk], a, b).items() if v['status'] == 'approved'}
    d = a
    while d <= b:
        r = book.get(emp, d)
        if r is not None:
            if r['off']:
                out['roster_off_days'] += 1
            elif d in leave:
                out['leave_on_shift_days'] += 1
            else:
                out['shift_days'] += 1
                out['scheduled_shift_hours'] += r['hours']
                if r['night_shift']:
                    out['night_shift_days'] += 1
                if d in worked:
                    out['worked_shift_days'] += 1
                    out['shift_allowance_total'] += r['shift_allowance'] or ZERO
                    if r['night_shift']:
                        out['worked_night_shift_days'] += 1
                        out['night_allowance_total'] += r['night_allowance'] or ZERO
        d += timedelta(days=1)
    from django.db.models import Q
    out['shift_changes'] = Decimal(ShiftRequest.objects.filter(Q(employee_id=emp.pk) | Q(swap_employee_id=emp.pk), status='approved',
                                                               date__gte=a, date__lte=b).count())
    return {k: Decimal(v).quantize(Decimal('0.01')) for k, v in out.items()}
