"""
Compensatory off (v1.11.0): days earned by working on a weekend or public holiday, credited to the employee's
compensatory leave type, used through normal leave requests, and lapsing when not used in time.
"""
import logging
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Q, Sum

from . import engine

log = logging.getLogger(__name__)
M = apps.get_model


def comp_type(emp):
    """The compensatory leave type for the employee's branch (or one for every branch)."""
    LT = M('calendars', 'leave_type')
    qs = LT.objects.filter(is_compensatory=True)
    return qs.filter(branch=emp.emp_branch_id).first() or qs.filter(branch__isnull=True).first() or qs.first()


def days_for_hours(emp, hours):
    """Days earned for `hours` worked: policy line thresholds when set, otherwise 1 day for any work (old behaviour)."""
    t = comp_type(emp)
    line = engine.line_for(emp, t.pk) if t else None
    full = getattr(line, 'comp_full_day_hours', None)
    half = getattr(line, 'comp_half_day_hours', None)
    if not full and not half:
        return 1.0 if hours > 0 else 0.0
    if full and hours >= full:
        return 1.0
    if half and hours >= half:
        return 0.5
    return 0.0


def credit(emp, days, ref=None, note='', user_id=None, on=None):
    """Add compensatory days to the balance with a ledger line (ref = the allocation or request)."""
    t = comp_type(emp)
    if t is None:
        raise ValueError('No compensatory leave type: create a leave type with "Compensatory" switched on.')
    engine.post(emp.pk, t.pk, on or date.today(), 'accrual', days, ref=ref, note=note or 'Compensatory off', user_id=user_id, move_balance=True)
    return t


def expire(on=None, user_id=None, preview=False):
    """Compensatory days not used within the policy line's expiry days lapse (oldest used first)."""
    on = on or date.today()
    _, _, _, Ledger = engine._models()
    out = []
    for emp in engine._active_employees():
        t = comp_type(emp)
        if t is None:
            continue
        line = engine.line_for(emp, t.pk)
        if not line or not line.comp_expiry_days:
            continue
        rows = Ledger.objects.filter(employee_id=emp.pk, leave_type_id=t.pk)
        credits = list(rows.filter(kind__in=('accrual', 'opening'), days__gt=0).order_by('date', 'id').values_list('date', 'days'))
        used = -float(rows.filter(kind__in=('taken', 'cancelled', 'encashed', 'lapsed')).aggregate(s=Sum('days'))['s'] or 0)
        lapse = 0.0
        for d, n in credits:            # first in, first out
            take = min(n, max(used, 0))
            used -= take
            if d + timedelta(days=line.comp_expiry_days) <= on:
                lapse += n - take
        bal = float(engine.balance_row(emp.pk, t.pk).balance or 0)
        lapse = round(min(lapse, max(bal, 0)), 2)
        if lapse <= 0:
            continue
        note = f'Compensatory off not used within {line.comp_expiry_days} days'
        out.append({'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} ({emp.emp_code})', 'leave_type_id': t.pk,
                    'kind': 'lapsed', 'days': -lapse, 'note': note})
        if not preview:
            engine.post(emp.pk, t.pk, on, 'lapsed', -lapse, note=note, user_id=user_id, move_balance=True)
    return out
