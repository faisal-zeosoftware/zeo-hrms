"""v1.62.3 – recover salary paid for leave days that the leave rules made unpaid after the payslip was closed.

When the attendance calendar of a paid month changes afterwards (e.g. the nightly job of 1.62.1 wrote half-pay maternity days as
full pay, item 48; or the pay steps of a leave year were worked out again, item 49), the closed payslip keeps the old days.
`leave_overpayment(payslip)` compares the payslip's days worked with the unpaid days now in the calendar for its period; the
difference × each prorated component ÷ the payslip's working days is the amount paid too much (or too little).

`make_recovery(payslip, cap_pct=20)` writes it as PayrollPlus arrear lines, taken with the next payrolls: a recovery is spread
so that no month takes more than cap_pct % of the payslip's gross (UAE Labour Law: recovery of amounts paid in excess of the
worker's entitlement is limited to 20 % of the wage – the company may enter its own limit); an underpayment is paid at once.
Nothing is written twice for the same payslip (lines with the note tag are replaced while still waiting).
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.db import transaction

M = apps.get_model
Q2 = Decimal('0.01')
TAG = 'Leave days corrected after payslip'


def _period(p):
    from .cycles import _payslip_period
    s, e, _f = _payslip_period(p)
    return s, e


def leave_overpayment(payslip):
    """{days_paid, days_due, diff_days, lines: [{component_id, code, paid, due, diff}], total} – total > 0 = paid too much."""
    from .cycles import scalable
    AC = M('calendars', 'AttendanceCalendar')
    PC = M('PayrollManagement', 'PayslipComponent')
    s, e = _period(payslip)
    work = Decimal(str(payslip.total_working_days or 0))
    paid_days = Decimal(str(payslip.days_worked or 0))
    unpaid = sum((Decimal(str(x or 0)) for x in AC.objects.filter(employee_id=payslip.employee_id, date__range=(s, e))
                  .values_list('unpaid_fraction', flat=True)), Decimal('0'))
    # the days before joining / after the last working day, exactly as the payroll counts them (PayrollManagement.signals)
    try:
        from PayrollManagement.signals import last_working_day
        emp = payslip.employee
        joined, lwd = emp.emp_joined_date, last_working_day(emp)
        off = (joined - s).days if joined and joined > s else 0
        off += (e - lwd).days if lwd and lwd < e else 0
        if off:
            window = Decimal((e - s).days + 1)
            pm = 'share'
            try:
                from PayrollPlus.models import PayrollPlusSettings
                pm = PayrollPlusSettings.get().part_month
            except Exception:
                pass
            if pm == 'days':
                unpaid += max(Decimal('0'), work - min(window - Decimal(off), work))
            else:
                unpaid += (Decimal(off) * work / window).quantize(Q2)
    except Exception:
        pass
    due_days = max(work - unpaid, Decimal('0'))
    diff = paid_days - due_days
    out = {'payslip': payslip.pk, 'period': [s.isoformat(), e.isoformat()], 'days_paid': str(paid_days), 'days_due': str(due_days),
           'diff_days': str(diff), 'lines': [], 'total': '0.00', 'gross': str(payslip.gross_salary or 0)}
    if not work or not diff:
        return out
    out['lines'], tot = component_lines(payslip, diff)   # v1.62.5 (item 19): shared with the paid-period leave (days_difference)
    out['total'] = str(tot.quantize(Q2))
    return out


def component_lines(payslip, diff):
    """v1.62.5 (item 19): ([{component_id, code, paid, due, diff}], total) – `diff` days paid too much (negative: too little)
    × each prorated addition of the payslip ÷ its working days (the month's amount before the days paid were applied)."""
    from .cycles import scalable
    PC = M('PayrollManagement', 'PayslipComponent')
    work = Decimal(str(payslip.total_working_days or 0))
    paid_days = Decimal(str(payslip.days_worked or 0))
    diff = Decimal(str(diff or 0))
    lines, tot = [], Decimal('0')
    if not work or not diff or paid_days <= 0:
        return lines, tot
    for pc in PC.objects.filter(payslip=payslip).select_related('component'):
        c = pc.component
        if c.component_type != 'addition' or not scalable(c):
            continue
        amt = Decimal(str(pc.amount or 0))
        full = amt * work / paid_days          # the month's amount before the days paid were applied
        over = (full * diff / work).quantize(Q2, ROUND_HALF_UP)
        if not over:
            continue
        lines.append({'component_id': c.pk, 'code': c.code, 'paid': str(amt), 'due': str((amt - over).quantize(Q2)), 'diff': str(over)})
        tot += over
    return lines, tot


TAG_LEAVE = 'Leave on a paid payroll period'   # v1.62.5 (item 19)


@transaction.atomic
def days_difference(payslip, diff, note='', effective_from=None, user_id=None):
    """v1.62.5 (item 19): a leave saved on days of a payroll already made ("Add to next salary"): `diff` days paid too much (a
    leave made the days unpaid → recovered) or too little (a cancelled unpaid leave → paid back) written as one arrear line per
    prorated component, all taken by the next payroll (effective_from: the day after the payslip's period, so the closed run
    never takes them). Returns {lines, total} – total < 0 = deducted from the next salary."""
    from .models import ArrearLine
    s, e = _period(payslip)
    run = payslip.payroll_run
    mon = date(run.year, run.month, 1)   # the run's month ("November" of a run 26/10–25/11 with a cut-off on the 25th)
    lines, tot = component_lines(payslip, diff)
    eff = effective_from or (e + timedelta(days=1))
    # v1.62.5 (item 19): a recovery is spread so that no month takes more than 20 % of the wage (UAE Labour Law limit for
    # recovering an amount paid in excess – the same rule as make_recovery, 1.62.3); a payback is paid at once
    gross = Decimal(str(payslip.gross_salary or 0))
    cap = (gross * Decimal('20') / 100).quantize(Q2, ROUND_HALF_UP) if tot > 0 and gross > 0 else None
    n = 1 if not cap else max(1, int((tot / cap).to_integral_value(rounding='ROUND_CEILING')))
    def _plus(d, k):   # the same day k months later (the next payroll periods also with a cut-off such as the 25th)
        y, m = divmod(d.month - 1 + k, 12)
        y, m = d.year + y, m + 1
        return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))
    starts = [_plus(eff, k) for k in range(n)]
    left = {ln['component_id']: Decimal(ln['diff']) for ln in lines}
    made = []
    for i, mo in enumerate(starts):
        share_total = sum(left.values())
        take = min(cap, share_total) if cap else share_total
        for ln in lines:
            cid = ln['component_id']
            rest = left[cid]
            part = rest if i == n - 1 else ((take * rest / share_total).quantize(Q2, ROUND_HALF_UP) if share_total else Decimal('0'))
            if not part:
                continue
            left[cid] = rest - part
            a = ArrearLine.objects.create(employee_id=payslip.employee_id, component_id=cid, month=mon,
                                          source_payslip_id=payslip.pk, old_amount=Decimal(ln['paid']), new_amount=Decimal(ln['due']),
                                          amount=-part, effective_from=mo, created_by_id=user_id,
                                          note=(f'{TAG_LEAVE} {mon:%m/%Y}: {Decimal(str(diff)):g} day(s)' + (f' – part {i + 1} of {n}' if n > 1 else '')
                                                + (f' – {note}' if note else ''))[:200])
            made.append({'id': a.pk, 'code': ln['code'], 'amount': str(a.amount), 'from': mo.isoformat()})
    return {'lines': made, 'total': str(-tot.quantize(Q2)), 'months': n}


def _month_starts(after, n):
    y, m = after.year, after.month
    out = []
    for _ in range(n):
        m += 1
        if m > 12:
            y, m = y + 1, 1
        out.append(date(y, m, 1))
    return out


@transaction.atomic
def make_recovery(payslip, cap_pct=Decimal('20'), user=None, first_month=None):
    """Write the arrear lines (recovery spread over months at most cap_pct % of gross). Returns the plan."""
    from .models import ArrearLine
    plan = leave_overpayment(payslip)
    total = Decimal(plan['total'])
    s, e = _period(payslip)
    ArrearLine.objects.filter(employee_id=payslip.employee_id, source_payslip_id=payslip.pk, status='pending', note__startswith=TAG).delete()
    plan['months'] = []
    if not total:
        return plan
    gross = Decimal(str(payslip.gross_salary or 0))
    cap = (gross * Decimal(str(cap_pct)) / 100).quantize(Q2, ROUND_HALF_UP) if total > 0 and cap_pct else None
    n = 1 if not cap or cap <= 0 else int((total / cap).to_integral_value(rounding='ROUND_CEILING'))
    first = first_month or _month_starts(e, 1)[0]
    months = [first] + _month_starts(first, n - 1)[:n - 1]
    left = {ln['component_id']: Decimal(ln['diff']) for ln in plan['lines']}
    for i, mo in enumerate(months):
        take = min(cap, sum(left.values())) if cap else sum(left.values())
        share_total = sum(left.values())
        month_lines = []
        for cid, rest in list(left.items()):
            if i == len(months) - 1:
                part = rest
            else:
                part = (take * rest / share_total).quantize(Q2, ROUND_HALF_UP) if share_total else Decimal('0')
            if not part:
                continue
            left[cid] = rest - part
            ln = next(x for x in plan['lines'] if x['component_id'] == cid)
            ArrearLine.objects.create(employee_id=payslip.employee_id, component_id=cid, month=s.replace(day=1), source_payslip_id=payslip.pk,
                                      old_amount=Decimal(ln['paid']), new_amount=Decimal(ln['due']), amount=-part,
                                      effective_from=mo, created_by_id=getattr(user, 'pk', None),
                                      note=f'{TAG} {s:%m/%Y}: {plan["diff_days"]} day(s) – part {i + 1} of {len(months)}'[:200])
            month_lines.append({'code': ln['code'], 'amount': str(-part)})
        plan['months'].append({'from': mo.isoformat(), 'lines': month_lines, 'total': str(-sum(Decimal(x['amount']) for x in month_lines) * -1)})
    return plan
