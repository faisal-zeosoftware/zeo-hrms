"""v1.56.0 – mid-year go-live (setup guide, step 11).

A company that starts ZEO in the middle of the year has paid its salaries elsewhere up to a date. Payroll runs of a period
that ends on or before that date leave the employee out (no second salary for a month already paid), and the gratuity
of a final settlement counts the service from the gratuity start date recorded here and the unpaid days before go-live
(Employment info).
"""
import calendar
import time
from datetime import date

from django.db import connection

_cache = {}


def _data():
    """(company paid-up-to date, {employee id: GoLiveEmployee}) for at most a minute."""
    key = getattr(connection, 'schema_name', '')
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    from .models import GoLiveEmployee, PayrollPlusSettings
    s = PayrollPlusSettings.objects.order_by('id').first()
    rows = {g.employee_id: g for g in GoLiveEmployee.objects.all()}
    val = (getattr(s, 'paid_up_to', None), rows)
    if len(_cache) > 200:
        _cache.clear()
    _cache[key] = (time.time(), val)
    return val


def forget():
    _cache.pop(getattr(connection, 'schema_name', ''), None)


def paid_up_to(employee_id):
    company, rows = _data()
    g = rows.get(employee_id)
    return g.paid_up_to if g is not None and g.paid_up_to else company


def run_end(run, period_end=None):
    if period_end:
        return period_end
    try:
        y, m = int(run.year), int(run.month)
        return date(y, m, calendar.monthrange(y, m)[1])
    except (TypeError, ValueError):
        return None


def paid_outside(run, employee, period_end=None):
    """True when the run's period was already paid outside ZEO for this employee."""
    upto = paid_up_to(employee.pk)
    if not upto:
        return False
    end = run_end(run, period_end)
    return bool(end and end <= upto)


def gratuity_start(employee, joined):
    company, rows = _data()
    g = rows.get(employee.pk)
    return g.gratuity_from if g is not None and g.gratuity_from else joined


def unpaid_before(employee):
    """Unpaid days before go-live (Employment info, since 1.20.2)."""
    try:
        from django.apps import apps
        v = apps.get_model('EmployeeProfile', 'EmploymentInfo').objects.filter(employee_id=employee.pk).values_list('unpaid_days_before_golive', flat=True).first()
        return float(v or 0)
    except LookupError:
        return 0.0
