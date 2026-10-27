"""v1.49.0 – monthly gratuity, leave-salary and air-ticket provisions per employee.

The liability at the month end comes from the same reports HR already reads (Reports → Gratuity, Leave liability) and the
open air-ticket entitlements; the provision to book is the change on the previous month. A run is made on the 1st for the
month before (Celery beat 'payrollplus-provisions') or any time from the Provisions page; when the Accounting integration is
on, 'Post to accounting' sends the same amounts as the month-end provisions journal.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction

from .models import ProvisionLine, ProvisionRun

KINDS = ('gratuity', 'leave', 'air_ticket')
Q2 = Decimal('0.01')


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def last_month_end(today=None):
    t = today or date.today()
    return t.replace(day=1) - timedelta(days=1)


def _rows(fn, as_of):
    from ZeoAccounting.services import _report_rows
    return _report_rows(fn, as_of)


def liabilities(as_of):
    """{kind: {employee id: (amount, basis text)}} – v1.59.0: by the rules in force on the month end."""
    try:
        from CountryPack.services import rules_on
    except ImportError:
        return _liabilities(as_of)
    with rules_on(as_of):
        return _liabilities(as_of)


def _liabilities(as_of):
    out = {k: {} for k in KINDS}
    try:
        for r in _rows('r_gratuity', as_of):
            if r.get('_emp'):
                out['gratuity'][r['_emp']] = (Decimal(str(r.get('amount') or 0)).quantize(Q2),
                                              f"{r.get('years', '')} years, {r.get('days', '')} days of basic")
    except Exception:
        pass
    try:
        for r in _rows('r_leave_liability', as_of):
            if r.get('_emp'):
                out['leave'][r['_emp']] = (Decimal(str(r.get('liability') or 0)).quantize(Q2), f"{r.get('balance', '')} days")
    except Exception:
        pass
    AT = apps.get_model('PayrollManagement', 'AirTicketAllocation')
    agg = defaultdict(Decimal)
    for a in AT.objects.filter(is_active=True).exclude(status__in=('used', 'expired', 'cancelled', 'Used', 'Expired', 'Cancelled')):
        if a.expiry_date and a.expiry_date < as_of:
            continue
        agg[a.employee_id] += Decimal(str(a.remaining_amount if a.remaining_amount is not None else a.amount or 0))
    for k, v in agg.items():
        out['air_ticket'][k] = (v.quantize(Q2), 'open entitlement')
    return out


@transaction.atomic
def make_run(as_of=None, user_id=None):
    as_of = month_end(as_of or last_month_end())
    run = ProvisionRun.objects.filter(month=as_of).first()
    if run is not None and run.status == 'posted':
        return run
    if run is None:
        run = ProvisionRun.objects.create(month=as_of, made_by_id=user_id)
    prev_run = ProvisionRun.objects.filter(month__lt=as_of).order_by('-month').first()
    prev = {(l.employee_id, l.kind): l.closing for l in prev_run.lines.all()} if prev_run else {}
    run.lines.all().delete()
    now = liabilities(as_of)
    lines, totals = [], {k: {'opening': Decimal(0), 'closing': Decimal(0), 'movement': Decimal(0)} for k in KINDS}
    emps = set()
    for k in KINDS:
        keys = set(now[k]) | {e for (e, kk) in prev if kk == k}
        for emp in keys:
            closing, basis = now[k].get(emp, (Decimal(0), 'no longer due'))
            opening = Decimal(str(prev.get((emp, k), 0)))
            mv = (closing - opening).quantize(Q2)
            if not closing and not opening:
                continue
            lines.append(ProvisionLine(run=run, employee_id=emp, kind=k, opening=opening, closing=closing, movement=mv, basis=basis[:255]))
            t = totals[k]
            t['opening'] += opening
            t['closing'] += closing
            t['movement'] += mv
            emps.add(emp)
    ProvisionLine.objects.bulk_create(lines)
    run.employees = len(emps)
    run.totals = {k: {x: str(v.quantize(Q2)) for x, v in t.items()} for k, t in totals.items()}
    run.made_by_id = user_id or run.made_by_id
    run.save()
    return run


def run_json(run, lines=False, emp_ok=None):
    out = {'id': run.id, 'month': run.month, 'label': run.month.strftime('%B %Y'), 'status': run.status, 'status_label': run.get_status_display(),
           'employees': run.employees, 'totals': run.totals, 'made_at': run.made_at, 'posted_ref': run.posted_ref}
    if lines:
        E = apps.get_model('EmpManagement', 'emp_master')
        ls = list(run.lines.all())
        ids = {l.employee_id for l in ls}
        if emp_ok is not None:
            ids = {i for i in ids if emp_ok(i)}
        emps = {e.pk: e for e in E.objects.filter(pk__in=ids).select_related('emp_dept_id', 'emp_branch_id')}
        rows = {}
        for l in ls:
            e = emps.get(l.employee_id)
            if e is None:
                continue
            r = rows.setdefault(l.employee_id, {'employee_id': e.pk, 'code': e.emp_code, 'name': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                                                'department': str(e.emp_dept_id) if e.emp_dept_id_id else '', 'branch': str(e.emp_branch_id) if e.emp_branch_id_id else ''})
            r[l.kind] = {'opening': str(l.opening), 'closing': str(l.closing), 'movement': str(l.movement), 'basis': l.basis}
        out['rows'] = sorted(rows.values(), key=lambda r: r['name'])
    return out


def post(run, user=None):
    """Send the run's closing amounts as the month-end provisions journal (Accounting integration)."""
    from ZeoAccounting import services as A
    amounts = {'gratuity': {}, 'leave': {}}
    for l in run.lines.filter(kind__in=('gratuity', 'leave')):
        amounts[l.kind][l.employee_id] = l.closing
    batch = A.build_provisions(run.month, user, amounts=amounts)
    run.status = 'posted'
    run.posted_ref = str(getattr(batch, 'number', '') or getattr(batch, 'pk', '') or '')[:60]
    run.save(update_fields=['status', 'posted_ref'])
    return run
