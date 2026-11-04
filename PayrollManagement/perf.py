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


def employee_weekend_days(employee, day=None):
    # v1.62.6 (item 22): the one weekend rule (calendars/workdays.py – most specific assignment wins, category counted).
    # Before: the first assignment by id naming the employee, the branch or the department (category ignored).
    from calendars.workdays import WeekendRules, model_days
    rules = memo(('wd_weekend_rules',), WeekendRules)
    return model_days(rules.pick(employee, day)[0], day)


def employee_holidays(employee, start_date, end_date):
    # v1.62.6 (item 22): the employee's holiday calendar by calendars.utils.get_employee_holiday_calendar's order
    from calendars.workdays import HolidayRules
    hol = memo(('wd_holiday_rules',), HolidayRules)
    a = start_date.date() if hasattr(start_date, 'date') and callable(start_date.date) else start_date
    b = end_date.date() if hasattr(end_date, 'date') and callable(end_date.date) else end_date
    return {d for d in hol.dates(employee) if a <= d <= b}


def working_day_plan(employee, start_date, end_date):
    """v1.62.6 (item 22): calendars.workdays.Plan for a payroll period – inside payroll_cache() one plan (one shift book, one read
    of the weekend / holiday assignments) for every employee of the run."""
    from calendars.workdays import Plan, WeekendRules, HolidayRules
    a = start_date.date() if hasattr(start_date, 'date') and callable(start_date.date) else start_date
    b = end_date.date() if hasattr(end_date, 'date') and callable(end_date.date) else end_date
    if not active():
        return Plan(a, b, [employee.pk])
    return memo(('wd_plan', a, b), lambda: Plan(a, b, None, rules=memo(('wd_weekend_rules',), WeekendRules),
                                                holidays=memo(('wd_holiday_rules',), HolidayRules)))
