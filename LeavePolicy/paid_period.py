"""v1.62.5 (item 19) – leave on days whose salary is already paid / processed.

A payroll run that is processed, approved or paid keeps its payslips: a leave applied, approved, changed or cancelled
afterwards on days of that run's period changes the attendance calendar, but never the closed payslip, so the pay
difference (unpaid days of an unpaid / half-pay leave, or days given back by a cancellation) was lost. Before 1.62.5 the
request model refused only by the month of the start date ("payroll for 11/2026 is already processed"), with no way on:
a company with a cut-off on the 25th had leave of 26–30 November (December payroll) refused, and leave crossing into a
closed period from a month without a run was saved silently.

Now:
* a day is in a closed period when the employee has a payslip of a processed / approved / paid run whose attendance window
  (attendance_start_date – attendance_end_date, e.g. 26/10–25/11 for "November" with a cut-off on the 25th) contains it;
  off-cycle runs do not count, nor pending runs (they are calculated again);
* applying, approving (the last level), changing the dates of, or cancelling a leave with such days is refused with
  "<Month> salary is already paid for <days>" (already processed: run not paid yet), code period_paid (HTTP 409);
* HR / payroll users (not the employee for their own leave) may answer "Add to <next month> salary"
  (confirm_paid_period=next_salary): the request is saved, and when the calendar changes (approval / cancellation) the
  difference of those days × the payslip's prorated components ÷ its working days is written as arrear lines for the next
  payroll (PayrollPlus.recovery.days_difference; cycles.include_arrears takes them). A choice made when the request was
  recorded is kept as a note on the request, so the approver later is not asked again.
"""
import calendar as _cal
import threading
from datetime import date as date_cls
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError

from Translations.i18n import tr

M = apps.get_model
CLOSED = ('processed', 'approved', 'paid')
CHOICE = 'next_salary'
KEY = 'confirm_paid_period'
NOTE = '[paid-period]'          # start of the note that keeps HR's choice on the request (Chatter); the text after it is translated
HR_CODES = {'change_employee_leave_request', 'add_lv_cancellation', 'change_leavepolicy', 'add_payrollrun', 'change_payrollrun',
            'change_payslip'}
_local = threading.local()


class PaidPeriodError(ValidationError):
    """The leave touches days of a payroll already made. `info` = the 409 answer (without the HR choice, added per reader)."""
    def __init__(self, info):
        self.info = info
        super().__init__(info['detail'], code='period_paid')


# ------------------------------------------------------------------ who may choose "Add to next salary"
@contextmanager
def deciding(request=None, user=None, choice=None):
    """Around a save from a screen: the user and their answer (request data confirm_paid_period)."""
    prev = getattr(_local, 'ctx', None)
    if request is not None:
        user = user or getattr(request, 'user', None)
        if choice is None:
            try:
                choice = request.data.get(KEY)
            except Exception:
                choice = None
            if choice is None:
                choice = request.query_params.get(KEY) if hasattr(request, 'query_params') else None
    _local.ctx = {'user': user, 'choice': choice}
    try:
        yield
    finally:
        _local.ctx = prev


def _ctx():
    c = getattr(_local, 'ctx', None)
    if c is not None:
        return c
    try:   # a save outside deciding(): the user of the current request (no choice: only the message)
        from Chatter.tracking import current_request
        r = current_request()
        return {'user': getattr(r, 'user', None) if r is not None else None, 'choice': (r.GET.get(KEY) if r is not None else None)}
    except Exception:
        return {'user': None, 'choice': None}


def may_override(user, emp):
    """HR / payroll (company admin or leave / payroll rights), never the employee for their own leave."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if emp is not None and getattr(emp, 'users_id', None) == user.pk:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    try:
        from tenant_users.tenants.models import UserTenantPermissions
        utp = UserTenantPermissions.objects.filter(profile=user).prefetch_related('groups__permissions').first()
        if utp is None:
            return False
        if utp.is_superuser:
            return True
        return any(p.codename in HR_CODES for g in utp.groups.all() for p in g.permissions.all())
    except Exception:
        return False


# ------------------------------------------------------------------ closed periods
def closed_periods(emp, a, b):
    """[{payslip, run, start, end}] of the employee's payslips of processed / approved / paid runs whose period overlaps a..b."""
    from PayrollPlus.cycles import _payslip_period, _run_info
    P = M('PayrollManagement', 'Payslip')
    out = []
    for p in P.objects.filter(employee_id=emp.pk, payroll_run__status__in=CLOSED).select_related('payroll_run'):
        try:
            if _run_info(p.payroll_run)[0] == 'offcycle':
                continue
            s, e, _f = _payslip_period(p)
        except Exception:
            continue
        if s <= b and e >= a:
            out.append({'payslip': p, 'run': p.payroll_run, 'start': s, 'end': e})
    return sorted(out, key=lambda x: x['start'])


def _days(req):
    """The days the request counts (working days when the policy / leave type counts working days)."""
    from . import engine
    if engine.is_hourly(getattr(req, 'leave_type', None)):
        return []
    try:
        line = engine.line_for(req.employee, req.leave_type_id)
    except Exception:
        line = None
    return engine._req_days(req, line)


def _unpaid_now(emp, days):
    AC = M('calendars', 'AttendanceCalendar')
    rows = {r.date: r for r in AC.objects.filter(employee=emp, date__in=list(days))}
    return {d: (Decimal(str(rows[d].unpaid_fraction or 0)) if d in rows else Decimal('0')) for d in days}, {d for d, r in rows.items() if r.is_manual}


def _unpaid_after(req, mode, days, manual, now):
    """The unpaid part of each day once the change is saved (what sync_calendar will write)."""
    from . import engine
    if mode == 'cancel':
        return {d: (now[d] if d in manual else Decimal('0')) for d in days}
    import copy
    r = copy.copy(req)
    r.status = 'approved'
    out = {d: now[d] for d in days}
    for d, paid, unit in engine.paid_split(r):
        if d in out and d not in manual:
            out[d] = Decimal(str(round(unit * (1 - paid), 2)))
    return out


def _month(run):
    return tr(_cal.month_name[run.month])


def _next_month(run):
    m = run.month % 12 + 1
    return tr(_cal.month_name[m])


def _money(v):
    v = Decimal(str(v))
    return ('+' if v > 0 else '') + f'{v:,.2f}'


def _ranges(days):
    """'03/11/2026 – 05/11/2026, 09/11/2026' (consecutive counted days, a gap of days off only, are one range)."""
    out, cur = [], None
    for d in sorted(days):
        if cur and (d - cur[1]).days <= 3:   # a weekend / holiday between counted days does not split the range
            cur[1] = d
            continue
        if cur:
            out.append(cur)
        cur = [d, d]
    if cur:
        out.append(cur)
    return ', '.join(f'{a:%d/%m/%Y}' if a == b else f'{a:%d/%m/%Y} – {b:%d/%m/%Y}' for a, b in out)


def _mode(req):
    """apply | approve | edit | cancel | None – how this save changes the request's days."""
    LR = type(req)
    prev = LR.objects.filter(pk=req.pk).values('status', 'start_date', 'end_date', 'leave_type_id', 'dis_half_day').first() if req.pk else None
    if prev is None:
        return ('apply' if req.status in ('pending', 'approved') else None), prev
    if prev['status'] == 'approved' and req.status != 'approved':
        return 'cancel', prev
    if prev['status'] != 'approved' and req.status == 'approved':
        return 'approve', prev
    if req.status in ('pending', 'approved') and (prev['start_date'], prev['end_date'], prev['leave_type_id'], prev['dis_half_day']) != \
            (req.start_date, req.end_date, req.leave_type_id, req.dis_half_day):
        return 'edit', prev
    if req.status == 'approved' and _hourly(req):   # v1.62.11 (item 260): the hours (or half) of an approved short leave changed
        hp = LR.objects.filter(pk=req.pk).values('applied_days', 'approved_days', 'half_day_period').first() or {}
        if (float(hp.get('approved_days') or hp.get('applied_days') or 0), hp.get('half_day_period')) != \
                (float(req.approved_days or req.applied_days or 0), req.half_day_period):
            return 'edit', prev
    return None, prev


def _confirmed_before(req):
    if not req.pk:
        return False
    try:
        from Chatter.models import Message
        return Message.objects.filter(model='calendars.employee_leave_request', object_id=str(req.pk), body__startswith=NOTE).exists()
    except Exception:
        return False


def plan(req, mode, prev=None):
    """{periods: [{payslip, run, start, end, days, diff}], ...} of the request's days in closed periods – None when there are none."""
    if _hourly(req) or (prev and _hourly_type(prev.get('leave_type_id'))):
        return plan_hours(req, mode, prev)   # v1.62.11 (item 260): short leave in hours
    emp = req.employee
    if mode == 'cancel' and prev is not None:
        import copy
        r = copy.copy(req)
        r.start_date, r.end_date, r.leave_type_id, r.dis_half_day = prev['start_date'], prev['end_date'], prev['leave_type_id'], prev['dis_half_day']
        r.status = 'approved'
        days = _days(r)
    else:
        days = _days(req)
    if not days:
        return None
    periods = closed_periods(emp, min(days), max(days))
    if not periods:
        return None
    hit = []
    for p in periods:
        ds = [d for d in days if p['start'] <= d <= p['end']]
        if ds:
            hit.append(dict(p, days=ds))
    if not hit:
        return None
    all_days = [d for p in hit for d in p['days']]
    now, manual = _unpaid_now(emp, all_days)
    writes = mode in ('approve', 'cancel') or (mode in ('apply', 'edit') and req.status == 'approved')
    after = _unpaid_after(req, mode, all_days, manual, now)
    from PayrollPlus.recovery import component_lines
    total = Decimal('0')
    for p in hit:
        diff = sum((after[d] - now[d] for d in p['days']), Decimal('0'))
        p['diff'] = diff
        _lines, t = component_lines(p['payslip'], diff)
        p['amount'] = -t                       # < 0: taken from the next salary
        total += -t
    return {'periods': hit, 'before': now, 'writes': writes, 'amount': total, 'mode': mode}


def info_of(req, pl):
    runs = [p['run'] for p in pl['periods']]
    paid = all(r.status == 'paid' for r in runs)
    months = []
    for r in runs:
        if _month(r) not in months:
            months.append(_month(r))
    last = max(runs, key=lambda r: (r.year, r.month))
    nxt = _next_month(last)
    days = [d for p in pl['periods'] for d in p['days']]
    when = _ranges(days)
    mtxt = ', '.join(months)
    head = tr('{0} salary is already paid for {1}.', mtxt, when) if paid else tr('{0} salary is already processed for {1}.', mtxt, when)
    amt = pl['amount']
    if pl.get('hourly'):   # v1.62.11 (item 260): short leave in hours
        hrs = pl.get('hours') or 0
        head = (tr('{0} salary is already paid for {1} ({2:g} h short leave).', mtxt, when, hrs) if paid
                else tr('{0} salary is already processed for {1} ({2:g} h short leave).', mtxt, when, hrs))
    return {
        'code': 'period_paid', 'detail': head, 'title': tr('Salary already paid') if paid else tr('Salary already processed'),
        'month': mtxt, 'next_month': nxt, 'paid': paid, 'days': when, 'day_count': float(len(days) if not req.dis_half_day else 0.5),
        'amount_preview': str(amt.quantize(Decimal('0.01'))), 'employee_id': req.employee_id,
        'hr_text': (tr('Cancel to change nothing, or add the pay difference ({0}) to the {1} salary.', _money(amt), nxt) if amt
                    else (tr('This short leave makes no pay difference: Add to {0} salary records it without changing pay.', nxt) if pl.get('hourly')
                          else tr('These days make no pay difference (paid leave): Add to {0} salary records the leave without changing pay.', nxt))),
        'hours': pl.get('hours') if pl.get('hourly') else None,
        'employee_text': tr('Ask HR or payroll to record this leave.'),
        'confirm': {'key': KEY, 'value': CHOICE, 'label': tr('Add to {0} salary', nxt)}, 'cancel_label': tr('Cancel'),
    }


def answer(info, user):
    """The 409 body for this reader: HR / payroll get the two choices, the employee the message only."""
    out = {k: v for k, v in info.items() if k not in ('hr_text', 'employee_text', 'confirm', 'cancel_label', 'employee_id')}
    emp = M('EmpManagement', 'emp_master').objects.filter(pk=info.get('employee_id')).first()
    if may_override(user, emp):
        out['detail'] = f"{info['detail']} {info['hr_text']}"
        out['confirm'] = info['confirm']
        out['cancel_label'] = info['cancel_label']
    else:
        out['detail'] = f"{info['detail']} {info['employee_text']}"
    return out


# ------------------------------------------------------------------ hooks of the request model
def check(req):
    """Called by employee_leave_request.clean(): refuse (PaidPeriodError), or remember what to write after the save."""
    req._pp = None
    if getattr(req, '_v1650_part', False):
        return   # v1.65.0 (item 83): part cancel – leave_rules.cancel_part asks about the days given back itself
    try:
        mode, prev = _mode(req)
    except Exception:
        return
    if mode is None:
        return
    try:   # v1.62.11 (item 231): a day in a LOCKED payroll period is refused outright (no "Add to next salary")
        from PayrollPlus.payctl import guard_leave
        guard_leave(req)
    except ImportError:
        pass
    pl = plan(req, mode, prev)
    if pl is None:
        return
    c = _ctx()
    ok = c.get('choice') == CHOICE and may_override(c.get('user'), req.employee)
    if not ok and mode == 'approve' and _confirmed_before(req):
        ok = True                                  # HR chose "Add to next salary" when the request was recorded
    if not ok:
        raise PaidPeriodError(info_of(req, pl))
    pl['user_id'] = getattr(c.get('user'), 'pk', None)
    pl['note'] = c.get('choice') == CHOICE
    pl['info'] = info_of(req, pl)
    req._pp = pl


def after_save(req):
    """Called by the leave ledger signal after the calendar was written: the arrear lines of the closed days, the note."""
    pl = getattr(req, '_pp', None)
    if not pl:
        return None
    req._pp = None
    from PayrollPlus.recovery import days_difference
    AC = M('calendars', 'AttendanceCalendar')
    out = []
    if pl['writes'] and pl.get('hourly'):   # v1.62.11 (item 260): the hour lines worked out before the save
        out = _write_hour_lines(req, pl)
    elif pl['writes']:
        for p in pl['periods']:
            rows = {r.date: Decimal(str(r.unpaid_fraction or 0)) for r in AC.objects.filter(employee_id=req.employee_id, date__in=p['days'])}
            diff = sum((rows.get(d, Decimal('0')) - pl['before'].get(d, Decimal('0')) for d in p['days']), Decimal('0'))
            if diff:
                what = 'cancelled' if pl['mode'] == 'cancel' else 'leave'
                res = days_difference(p['payslip'], diff, note=f'{req.document_number} {what}', effective_from=p['end'] + timedelta(days=1),
                                      user_id=pl.get('user_id'))
                out.append(res)
    if pl.get('note'):
        try:
            from Chatter.models import Message
            i = pl['info']
            Message.objects.create(model='calendars.employee_leave_request', object_id=str(req.pk), author_id=pl.get('user_id'), kind='note',
                                   body=NOTE + ' ' + tr('{0} salary closed for {1}: the pay difference ({2}) is added to the {3} salary.',
                                                        i['month'], i['days'], _money(Decimal(i['amount_preview'])), i['next_month']),
                                   record_label=req.document_number or '')
        except Exception:
            pass
    return out



# ------------------------------------------------------------------ v1.62.11 (item 260): short leave in hours
# A short leave (leave type counted in hours) is not a leave day: approved and paid, it covers the late-in / early-out minutes of its
# day (AttendancePlus.engine.short_leave_minutes); unpaid it covers nothing. On a day of a payroll already made, saving, approving,
# changing or cancelling it changes those minutes – and so the attendance figures the payslip's formulas used (late_minutes,
# early_minutes, late_count, early_count, absent_days, half_day_count) – but never the closed payslip. Same rule as day leave: refused
# with "<Month> salary is already paid …" (409), or HR / payroll choose "Add to <next month> salary": the difference of each of the
# payslip's formula components that use those figures (worked out with the payslip's period figures before and after the change) is
# written as arrear lines for the next payroll.
ATT_VARS = ('late_minutes', 'early_minutes', 'late_count', 'early_count', 'absent_days', 'half_day_count')


def _hourly_type(lt_id):
    if not lt_id:
        return False
    return M('calendars', 'leave_type').objects.filter(pk=lt_id, unit='hours').exists()


def _hourly(req):
    from . import engine
    return engine.is_hourly(getattr(req, 'leave_type', None))


def _minutes_of(hours, paid_type, period):
    if not paid_type or not hours:
        return {}
    k = period if period in ('first_half', 'second_half') else 'any'
    return {k: int(round(float(hours) * 60))}


def _day(emp, d, adjust=None):
    """The AttendancePlus day result (not saved), with the short leave of the request as it would be (adjust)."""
    from AttendancePlus import engine as AE
    AE.SHORT_LEAVE_ADJUST.fn = adjust
    try:
        return AE.compute(emp, d)
    finally:
        AE.SHORT_LEAVE_ADJUST.fn = None


def _att(r, waived):
    late = 0 if waived else int(r.get('late_minutes') or 0)
    early = 0 if waived else int(r.get('early_minutes') or 0)
    return {'late_minutes': late, 'early_minutes': early, 'late_count': int(bool(late) and bool(r.get('is_late'))),
            'early_count': int(bool(early) and bool(r.get('is_early'))), 'absent_days': int(r.get('status') == 'absent'),
            'half_day_count': int(r.get('status') == 'half_day')}


def hour_lines(payslip, delta):
    """([{component_id, code, paid, due, diff}], impact) – the payslip's formula components that use the attendance figures, worked out
    with the period's figures and with them changed by `delta`; impact > 0 = paid too little (paid back), < 0 = paid too much."""
    import re
    from decimal import ROUND_HALF_UP
    from PayrollManagement.signals import get_formula_variables
    from PayrollManagement.utils import evaluate_formula
    from PayrollPlus.cycles import _payslip_period
    if not any(delta.values()):
        return [], Decimal('0')
    PC = M('PayrollManagement', 'PayslipComponent')
    rows = [pc for pc in PC.objects.filter(payslip=payslip).select_related('component')
            if (pc.component.formula or '').strip() and any(re.search(rf'\b{v}\b', pc.component.formula) for v in ATT_VARS)]
    if not rows:
        return [], Decimal('0')
    s, e, _f = _payslip_period(payslip)
    emp = payslip.employee
    base = dict(get_formula_variables(emp, s, e))
    for pc in PC.objects.filter(payslip=payslip).select_related('component'):   # chained formulas see the amounts of the payslip
        if pc.component.code:
            base.setdefault(pc.component.code, pc.amount)
    after = dict(base)
    for k, v in delta.items():
        after[k] = Decimal(str(base.get(k) or 0)) + Decimal(str(v))
    q = Decimal('0.01')
    lines, tot = [], Decimal('0')
    for pc in rows:
        c = pc.component
        try:
            a = Decimal(str(evaluate_formula(c.formula, dict(base), emp, c) or 0))
            b = Decimal(str(evaluate_formula(c.formula, dict(after), emp, c) or 0))
        except Exception:
            continue
        ch = (b - a).quantize(q, ROUND_HALF_UP)
        if not ch:
            continue
        paid = Decimal(str(pc.amount or 0))
        impact = ch if c.component_type == 'addition' else -ch
        lines.append({'component_id': c.pk, 'code': c.code, 'name': c.name, 'paid': str(paid), 'due': str((paid + ch).quantize(q)), 'diff': str(impact)})
        tot += impact
    return lines, tot


def plan_hours(req, mode, prev=None):
    emp = req.employee
    LT = M('calendars', 'leave_type')
    hp = None
    if req.pk:
        hp = type(req).objects.filter(pk=req.pk).values('status', 'start_date', 'leave_type_id', 'applied_days', 'approved_days',
                                                        'half_day_period').first()
    old_on = hp is not None and hp['status'] == 'approved'
    old_day = hp['start_date'] if hp else None
    old_type = LT.objects.filter(pk=hp['leave_type_id']).first() if hp else None
    old_min = _minutes_of(hp.get('approved_days') or hp.get('applied_days'), old_on and old_type is not None and old_type.unit == 'hours'
                          and old_type.type != 'unpaid', hp.get('half_day_period')) if hp else {}
    new_on = mode != 'cancel' and _hourly(req)
    new_min = _minutes_of(req.approved_days or req.applied_days, new_on and req.leave_type.type != 'unpaid', req.half_day_period) if new_on else {}
    days = sorted({d for d in (old_day if old_on else None, req.start_date if new_on else None) if d})
    if not days:
        return None
    periods = closed_periods(emp, min(days), max(days))
    hit = []
    for p in periods:
        ds = [d for d in days if p['start'] <= d <= p['end']]
        if ds:
            hit.append(dict(p, days=ds))
    if not hit:
        return None
    AD = M('AttendancePlus', 'AttendanceDay')

    def adjust_for(d):
        def fn(employee, day, out):
            out = dict(out)
            if day != d:
                return out
            if old_on and old_day == d:
                for k, v in old_min.items():
                    out[k] = max(0, out.get(k, 0) - v)
            if new_on and req.start_date == d:
                for k, v in new_min.items():
                    out[k] = out.get(k, 0) + v
            return {k: v for k, v in out.items() if v}
        return fn
    total = Decimal('0')
    for p in hit:
        delta = {k: 0 for k in ATT_VARS}
        for d in p['days']:
            waived = AD.objects.filter(employee_id=emp.pk, date=d, penalty_waived=True).exists()
            b, a = _att(_day(emp, d), waived), _att(_day(emp, d, adjust_for(d)), waived)
            for k in ATT_VARS:
                delta[k] += a[k] - b[k]
        p['delta'] = delta
        p['lines'], p['amount'] = hour_lines(p['payslip'], delta)
        p['diff'] = Decimal('0')
        total += p['amount']
    hours = float(req.approved_days or req.applied_days or 0) if new_on else float((hp or {}).get('approved_days') or (hp or {}).get('applied_days') or 0)
    writes = mode in ('approve', 'cancel') or (mode in ('apply', 'edit') and req.status == 'approved')
    return {'periods': hit, 'before': {}, 'writes': writes, 'amount': total, 'mode': mode, 'hourly': True, 'hours': hours}


def _write_hour_lines(req, pl):
    from PayrollPlus.models import ArrearLine
    from PayrollPlus.recovery import TAG_LEAVE
    out = []
    what = 'cancelled' if pl['mode'] == 'cancel' else 'short leave'
    for p in pl['periods']:
        run = p['run']
        mon = date_cls(run.year, run.month, 1)
        made = []
        for ln in p.get('lines') or []:
            a = ArrearLine.objects.create(employee_id=req.employee_id, component_id=ln['component_id'], month=mon, source_payslip_id=p['payslip'].pk,
                                          old_amount=Decimal(ln['paid']), new_amount=Decimal(ln['due']), amount=Decimal(ln['diff']),
                                          effective_from=p['end'] + timedelta(days=1), created_by_id=pl.get('user_id'),
                                          note=f'{TAG_LEAVE} {mon:%m/%Y}: {pl.get("hours") or 0:g} h – {req.document_number} {what}'[:200])
            made.append({'id': a.pk, 'code': ln['code'], 'amount': str(a.amount)})
        out.append({'lines': made, 'total': str(p['amount'])})
    return out
