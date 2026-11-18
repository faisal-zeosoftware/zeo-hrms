"""v1.73.7 – no day paid twice, none missed after a payroll-rule change.

Each payslip records the days it paid (PaidPeriod). Before a run pays an employee, window() compares its window with the days
already paid to that employee in other runs:
  - days already paid are taken off the start of the window (e.g. a Labour run paid 26 Oct – 25 Nov, the Office staff run of
    November pays 26 – 30 Nov only); a window with no day left is skipped (PayrollSkip, shown on the run and in the health check);
  - after a change of payroll rule (category, branch … – an employee transfer), unpaid days between the last paid day and the
    new window are added to the start, so the change month has no gap.
Payslips made before 1.73.7 have no record: their run's stored window (or its calendar month) stands in."""
import calendar
from datetime import date, timedelta

from django.apps import apps


def _paid(employee_id, exclude_run):
    PP = apps.get_model('PayrollExtras', 'PaidPeriod')
    Payslip = apps.get_model('PayrollManagement', 'Payslip')
    live = set(Payslip.objects.filter(employee_id=employee_id).values_list('pk', flat=True))   # a deleted payslip paid nothing
    out = [(p.start, p.end) for p in PP.objects.filter(employee_id=employee_id, payslip_id__in=live).exclude(run_id=exclude_run)]
    known = set(PP.objects.filter(employee_id=employee_id).values_list('payslip_id', flat=True))
    for ps in Payslip.objects.filter(employee_id=employee_id).exclude(payroll_run_id=exclude_run).exclude(pk__in=known).select_related('payroll_run'):
        r = ps.payroll_run
        try:
            if r.attendance_start_date and r.attendance_end_date:
                out.append((r.attendance_start_date, r.attendance_end_date))
            else:
                out.append((date(r.year, r.month, 1), date(r.year, r.month, calendar.monthrange(r.year, r.month)[1])))
        except Exception:   # noqa: BLE001
            continue
    return out


def _changed_rule(employee_id, after):
    """a done employee transfer that changed the payroll rule on / after the given day"""
    try:
        T = apps.get_model('HRActions', 'EmployeeTransfer')
    except LookupError:
        return False
    return T.objects.filter(employee_id=employee_id, status='done', effective_date__gt=after).exists()


def window(employee, run, start, end):
    """(start, end, note): the days this run pays the employee; start None = skip"""
    s = start.date() if hasattr(start, 'date') and not isinstance(start, date) else start
    e = end.date() if hasattr(end, 'date') and not isinstance(end, date) else end
    paid = _paid(employee.pk, run.pk)
    overlap = [b for a, b in paid if a <= e and b >= s]
    note = ''
    if overlap:
        last = max(overlap)
        if last >= e:
            return None, None, f'every day of {s:%d/%m/%Y} – {e:%d/%m/%Y} was paid already (up to {last:%d/%m/%Y})'
        s, note = last + timedelta(days=1), f'paid from {last + timedelta(days=1):%d/%m/%Y}: the days before were paid in another run'
    else:
        before = [b for a, b in paid if b < s]
        if before:
            last = max(before)
            if last < s - timedelta(days=1) and _changed_rule(employee.pk, last):
                s, note = last + timedelta(days=1), f'paid from {last + timedelta(days=1):%d/%m/%Y}: the days since the payroll rule changed'
    return s, e, note


def record(payslip, start, end, rule_id=None):
    PP = apps.get_model('PayrollExtras', 'PaidPeriod')
    s = start.date() if hasattr(start, 'date') and not isinstance(start, date) else start
    e = end.date() if hasattr(end, 'date') and not isinstance(end, date) else end
    PP.objects.update_or_create(payslip_id=payslip.pk, defaults={'employee_id': payslip.employee_id, 'run_id': payslip.payroll_run_id,
                                                                 'start': s, 'end': e, 'rule_id': rule_id})


def skip(run, employee, reason):
    apps.get_model('PayrollExtras', 'PayrollSkip').objects.update_or_create(run_id=run.pk, employee_id=employee.pk, defaults={'reason': reason[:255]})


def skips(run_id):
    return list(apps.get_model('PayrollExtras', 'PayrollSkip').objects.filter(run_id=run_id).values('employee_id', 'reason'))


def forget(payslip_id):
    """a payslip deleted (its run deleted or calculated again): its days are not paid any more"""
    apps.get_model('PayrollExtras', 'PaidPeriod').objects.filter(payslip_id=payslip_id).delete()
