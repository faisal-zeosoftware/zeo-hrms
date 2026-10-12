"""
v1.16.0 – per-run memo for payroll calculations (performance, defect D15).

A payroll run (or a formula component save, which recalculates every employee) asks the same questions again and again:
which shift does the employee work on each day of the period (the ShiftPlanner resolver used to be rebuilt for every
day of every employee – ~10 queries a day), which attendance rule applies, which weekend / holiday calendar …
Inside `with payroll_cache():` those answers are calculated once and reused; outside it every call works exactly as
before (no cache, no change of behaviour).

Only facts that a payroll run does not change are cached (shift plan, attendance rules, weekend / holiday calendars,
OT rates). Amounts, balances, loans … are always read fresh.

    from PayrollManagement.perf import payroll_cache, memo
    with payroll_cache():
        ...
        book = memo(('shift_book', start, end), lambda: Book(start, end))
"""
import threading
from contextlib import contextmanager

_local = threading.local()


def active():
    """True inside `payroll_cache()`."""
    return getattr(_local, 'cache', None) is not None


@contextmanager
def payroll_cache():
    """Open a memo for the duration of a payroll run / component recalculation (nested calls share the outer one)."""
    if getattr(_local, 'cache', None) is not None:
        yield _local.cache
        return
    _local.cache = {}
    try:
        yield _local.cache
    finally:
        _local.cache = None


def memo(key, factory):
    """factory() once per key while a payroll_cache() is open; without one, factory() every time."""
    cache = getattr(_local, 'cache', None)
    if cache is None:
        return factory()
    if key not in cache:
        cache[key] = factory()
    return cache[key]


# ---------------------------------------------------------------------------------------------------------------------
# v1.16.0 (D15): weekend days / public holidays of an employee inside a payroll run.
# calendars.utils.get_employee_weekend_days / get_employee_holidays read the employee's assignment with one query per
# employee: the first assignment (by id) that names the employee, the employee's branch or department (Django turns
# "branch = no branch" into "assignment without branches"). Inside payroll_cache() the assignments are read once and the
# same rule is applied here; outside it the calendars.utils functions are called unchanged.
# ---------------------------------------------------------------------------------------------------------------------
def _assignments(model_name, target):
    from django.apps import apps
    Model = apps.get_model('calendars', model_name)
    rows = []
    for a in Model.objects.order_by('pk').select_related(target).prefetch_related('employee', 'branch', 'department'):
        rows.append((a, {x.pk for x in a.employee.all()}, {x.pk for x in a.branch.all()}, {x.pk for x in a.department.all()}))
    return rows


def _first_assignment(rows, employee):
    b, d = employee.emp_branch_id_id, employee.emp_dept_id_id
    for a, emps, brs, deps in rows:
        if employee.pk in emps or (b in brs if b is not None else not brs) or (d in deps if d is not None else not deps):
            return a
    return None


def employee_weekend_days(employee):
    from calendars.utils import get_employee_weekend_days
    if not active():
        return get_employee_weekend_days(employee)
    a = _first_assignment(memo(('assign_weekend',), lambda: _assignments('assign_weekend', 'weekend_model')), employee)
    return set(a.weekend_model.get_weekend_days()) if a is not None else set()


def employee_holidays(employee, start_date, end_date):
    from calendars.utils import get_employee_holidays, daterange
    if not active():
        return get_employee_holidays(employee, start_date, end_date)
    a = _first_assignment(memo(('assign_holiday',), lambda: _assignments('assign_holiday', 'holiday_model')), employee)
    if a is None:
        return set()

    def dates():
        out = set()
        for h in a.holiday_model.holiday_list.filter(start_date__lte=end_date, end_date__gte=start_date):
            for day in daterange(h.start_date, h.end_date):
                out.add(day)
        return out
    return set(memo(('holiday_dates', a.holiday_model_id, start_date, end_date), dates))
