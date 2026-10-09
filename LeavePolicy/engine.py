"""
UAE leave rules engine (v1.10.0): which policy an employee has, checks on a leave request, pay slabs,
the leave ledger, monthly accrual and the leave-year end.

UAE Federal Decree-Law 33 of 2021 (Labour Law) and Cabinet Resolution 1 of 2022 – the defaults in setup.py:
* Annual leave 30 days a year; nothing in the first 6 months, then 2 days a month until 1 year of service.
  Unused leave may be carried forward (half by default) and is paid at the end of service on basic wage.
* Sick leave 90 days a year after probation: 15 days full pay, 30 days half pay, 45 days unpaid; medical report.
* Maternity 60 days: 45 full pay, 15 half pay (+45 unpaid days for complications).
* Parental leave 5 working days within 6 months of the birth (both parents).
* Bereavement 5 days (spouse), 3 days (parent, child, sibling, grandchild, grandparent).
* Study leave 10 working days a year after 2 years of service.
* Hajj (unpaid, up to 30 days, once in service) and unpaid leave by agreement.
Every number is a policy line setting, so a company can give more than the law.
"""
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

log = logging.getLogger(__name__)
M = apps.get_model
ACTIVE_STATUSES = ('pending', 'approved')
TAG = 'Leave rules'      # remark on attendance-calendar days written by the engine


def _models():
    from .models import EmployeeLeavePolicy, LeaveLedger, LeavePolicy, LeavePolicyLine
    return LeavePolicy, LeavePolicyLine, EmployeeLeavePolicy, LeaveLedger


# ------------------------------------------------------------------ policy lookup
def managed_type_ids():
    """Leave types handled by an active policy (the old accrual / reset jobs skip them)."""
    _, Line, _, _ = _models()
    try:
        return set(Line.objects.filter(policy__active=True).values_list('leave_type_id', flat=True))
    except Exception:        # table not migrated yet
        return set()


def policy_for(emp):
    Policy, _, EmpPolicy, _ = _models()
    if emp is None:
        return None
    o = EmpPolicy.objects.filter(employee_id=emp.pk, policy__active=True).select_related('policy').first()
    if o:
        return o.policy
    cat = emp.emp_ctgry_id_id
    if cat:
        for p in Policy.objects.filter(active=True):
            if cat in (p.category_ids or []):
                return p
    return Policy.objects.filter(active=True, is_default=True).first()


def line_for(emp, leave_type_id):
    p = policy_for(emp)
    if p is None:
        return None
    return p.lines.filter(leave_type_id=leave_type_id).first()


def months_of_service(emp, on):
    j = emp.emp_joined_date
    if not j or on < j:
        return 0
    m = (on.year - j.year) * 12 + (on.month - j.month)
    if on.day < j.day:
        m -= 1
    return max(m, 0)


def leave_year(policy, d):
    start_month = getattr(policy, 'leave_year_start', 1) or 1
    y = d.year if d.month >= start_month else d.year - 1
    start = date(y, start_month, 1)
    end = date(y + 1, start_month, 1) - timedelta(days=1)
    return start, end


# ------------------------------------------------------------------ counting days
def _weekend_days(emp):
    from calendars.utils import get_employee_weekend_calendar
    w = get_employee_weekend_calendar(emp)
    if not w:
        return set()
    return {d for d in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday') if getattr(w, d, None) == 'leave'}


def _holidays(emp):
    from calendars.utils import get_employee_holiday_calendar
    h = get_employee_holiday_calendar(emp)
    if not h:
        return set()
    out = set()
    for x in h.holiday_list.all():
        s = x.start_date
        e = getattr(x, 'end_date', None) or s
        while s <= e:
            out.add(s)
            s += timedelta(days=1)
    return out


def _shift_days(emp, a, b):
    """v1.12.0: working days from the shift planner (published roster > shift change > shift pattern); days without
    shift information fall back to the weekend calendar + holidays inside the planner. None when it is not installed."""
    if not apps.is_installed('ShiftPlanner'):
        return None
    try:
        from ShiftPlanner.resolver import scheduled_days as _sd
        return set(_sd(emp, a, b))
    except Exception:
        log.exception('shift planner working days failed; using the weekend calendar')
        return None


def leave_days(emp, start, end, mode, half=False):
    """Days a leave counts: every calendar day, or working days only (no weekends / public holidays / roster days off)."""
    if half:
        return 0.5
    if mode != 'working':
        return float((end - start).days + 1)
    sd = _shift_days(emp, start, end)
    if sd is not None:
        return float(len(sd))
    wk, hol = _weekend_days(emp), _holidays(emp)
    n, d = 0, start
    while d <= end:
        if d.strftime('%A').lower() not in wk and d not in hol:
            n += 1
        d += timedelta(days=1)
    return float(n)


def count_mode(req):
    """Policy's way of counting days for this request, or None (then the leave type's own setting applies)."""
    try:
        line = line_for(req.employee, req.leave_type_id)
    except Exception:
        return None
    return line.count_days if line else None


# ------------------------------------------------------------------ balances
def balance_row(emp_id, lt_id):
    B = M('calendars', 'emp_leave_balance')
    b, _ = B.objects.get_or_create(employee_id=emp_id, leave_type_id=lt_id, defaults={'balance': 0, 'openings': 0})
    if b.balance is None:
        b.balance = 0
        b.save(update_fields=['balance'])
    return b


def post(emp_id, lt_id, d, kind, days, ref=None, note='', user_id=None, move_balance=False):
    """One ledger line; optionally move the balance too (accrual, opening, encashment – the leave request moves its own)."""
    _, _, _, Ledger = _models()
    Ledger.objects.create(employee_id=emp_id, leave_type_id=lt_id, date=d, kind=kind, days=round(float(days), 2),
                          ref_model=ref._meta.label if ref is not None else '', ref_id=ref.pk if ref is not None else None,
                          note=note[:255], created_by_id=user_id)
    if move_balance:
        b = balance_row(emp_id, lt_id)
        b.balance = round(float(b.balance or 0) + float(days), 2)
        b.save(update_fields=['balance', 'updated_at'])


def balance_as_of(emp_id, lt_id, d):
    _, _, _, Ledger = _models()
    return round(Ledger.objects.filter(employee_id=emp_id, leave_type_id=lt_id, date__lte=d).aggregate(s=Sum('days'))['s'] or 0, 2)


def _days_of(r):
    return float(r.approved_days or r.applied_days or r.number_of_days or 0) if r.status == 'approved' else float(r.applied_days or r.number_of_days or 0)


def used_in(emp_id, lt_id, start, end, exclude_id=None, statuses=ACTIVE_STATUSES):
    LR = M('calendars', 'employee_leave_request')
    qs = LR.objects.filter(employee_id=emp_id, leave_type_id=lt_id, status__in=statuses, start_date__gte=start, start_date__lte=end)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return round(sum(_days_of(r) for r in qs), 2)


# ------------------------------------------------------------------ checks on a request
def leave_type_problems(req):
    """Rules set on the leave type itself (Leave types screen), for every leave type with or without a policy."""
    out, emp, lt = [], req.employee, req.leave_type
    if lt.valid_from and req.start_date < lt.valid_from:
        out.append(f'{lt.name} can be taken from {lt.valid_from:%d/%m/%Y}.')
    if lt.valid_to and req.end_date > lt.valid_to:
        out.append(f'{lt.name} can be taken until {lt.valid_to:%d/%m/%Y}.')
    crit = list(M('calendars', 'applicablity_critirea').objects.filter(leave_type=lt).prefetch_related('branch', 'department', 'designation', 'role'))
    if crit:
        def fits(c):
            if c.gender and c.gender != 'B' and (emp.emp_gender or '') != c.gender:
                return False
            for rel, val in ((c.branch, emp.emp_branch_id_id), (c.department, emp.emp_dept_id_id),
                             (c.designation, emp.emp_desgntn_id_id), (c.role, emp.emp_ctgry_id_id)):
                ids = {x.pk for x in rel.all()}
                if ids and val not in ids:
                    return False
            return True
        if not any(fits(c) for c in crit):
            out.append(f'{lt.name} does not apply to {emp.emp_first_name} (see the leave type\'s applicability: gender, branch, department, designation, category).')
    return out


def validate_request(req):
    """Raise ValidationError with every rule the request breaks. Runs when a request is created or its dates / type change."""
    errors = []
    emp, lt = req.employee, req.leave_type
    if req.end_date < req.start_date:
        raise ValidationError('The end date is before the start date.')
    LR = M('calendars', 'employee_leave_request')
    clash = LR.objects.filter(employee=emp, status__in=ACTIVE_STATUSES, start_date__lte=req.end_date, end_date__gte=req.start_date)
    if req.pk:
        clash = clash.exclude(pk=req.pk)
    c = clash.select_related('leave_type').first()
    if c:
        errors.append(f'{emp.emp_first_name} already has {c.leave_type.name} {c.document_number or ""} from {c.start_date:%d/%m/%Y} to {c.end_date:%d/%m/%Y} ({c.status}).')
    errors += leave_type_problems(req)
    line = line_for(emp, lt.pk)
    if line is None:
        if errors:
            raise ValidationError(errors)
        return
    policy = line.policy
    today = date.today()
    days = leave_days(emp, req.start_date, req.end_date, line.count_days, bool(req.dis_half_day))
    if line.gender != 'B' and (emp.emp_gender or '') != line.gender:
        errors.append(f'{lt.name} is only for {"female" if line.gender == "F" else "male"} employees.')
    svc = months_of_service(emp, req.start_date)
    if line.min_service_months and svc < line.min_service_months:
        errors.append(f'{lt.name} needs {line.min_service_months} months of service; {emp.emp_first_name} will have {svc} on {req.start_date:%d/%m/%Y}.')
    if line.first_year_uae and svc < 6:
        errors.append(f'{lt.name} can be taken after 6 months of service (UAE Labour Law); {emp.emp_first_name} will have {svc} months.')
    conf = emp.emp_date_of_confirmation
    if line.after_probation and conf and req.start_date < conf:
        errors.append(f'{lt.name} is available after probation ({conf:%d/%m/%Y}). During probation use unpaid leave.')
    if line.notice_days and req.pk is None and (req.start_date - today).days < line.notice_days:
        errors.append(f'{lt.name} must be requested {line.notice_days} days in advance (from {today + timedelta(days=line.notice_days):%d/%m/%Y}).')
    if line.max_per_request and days > line.max_per_request:
        errors.append(f'{lt.name} is at most {line.max_per_request:g} days per request; this request is {days:g} days.')
    y0, y1 = leave_year(policy, req.start_date)
    if line.max_per_year:
        used = used_in(emp.pk, lt.pk, y0, y1, exclude_id=req.pk)
        if used + days > line.max_per_year:
            errors.append(f'{lt.name} is at most {line.max_per_year:g} days a leave year; {used:g} already taken or requested, {max(line.max_per_year - used, 0):g} left.')
    if line.max_times_in_service:
        n = LR.objects.filter(employee=emp, leave_type=lt, status__in=ACTIVE_STATUSES).exclude(pk=req.pk).count()
        if n >= line.max_times_in_service:
            errors.append(f'{lt.name} can be taken {line.max_times_in_service} time(s) in service; already taken.')
    if line.requires_document and not req.lv_document:
        errors.append(f'{lt.name} needs a supporting document (e.g. medical report, certificate) – attach it to the request.')
    if line.accrual in ('monthly', 'yearly', 'earned') and lt.type != 'unpaid' and not (line.allow_negative or lt.negative):
        bal = float(balance_row(emp.pk, lt.pk).balance or 0)
        pending = used_in(emp.pk, lt.pk, date(1900, 1, 1), date(2999, 12, 31), exclude_id=req.pk, statuses=('pending',))
        if days > bal - pending + 1e-9:
            errors.append(f'Not enough {lt.name}: balance {bal:g} days' + (f', {pending:g} days waiting for approval' if pending else '') + f', this request {days:g} days.')
    if errors:
        raise ValidationError(errors)


# ------------------------------------------------------------------ pay slabs → attendance calendar → payroll
def slabs_for(emp, lt):
    line = line_for(emp, lt.pk)
    if line and line.pay_slabs:
        return [(float(d), float(p)) for d, p in line.pay_slabs]
    if getattr(lt, 'enable_leave_pay_rule', False):
        return [(float(r.days), float(r.pay_percentage)) for r in lt.pay_rules.all().order_by('sequence')]
    return []


def paid_split(req):
    """[(day, paid fraction of the day)] for an approved request: unpaid types 0 %, pay slabs by the days already taken this leave year."""
    emp, lt = req.employee, req.leave_type
    line = line_for(emp, lt.pk)
    mode = line.count_days if line else ('calendar' if lt.include_weekend else 'working')
    wk, hol = (_weekend_days(emp), _holidays(emp)) if mode == 'working' else (set(), set())
    sd = _shift_days(emp, req.start_date, req.end_date) if mode == 'working' else None
    days = []
    d = req.start_date
    while d <= req.end_date:
        if mode != 'working' or (d in sd if sd is not None else (d.strftime('%A').lower() not in wk and d not in hol)):
            days.append(d)
        d += timedelta(days=1)
    unit = 0.5 if req.dis_half_day else 1.0
    if lt.type == 'unpaid':
        return [(d, 0.0, unit) for d in days]
    slabs = slabs_for(emp, lt)
    if not slabs:
        return [(d, 1.0, unit) for d in days]
    policy = line.policy if line else None
    y0, _ = leave_year(policy, req.start_date) if policy else (date(req.start_date.year, 1, 1), None)
    LR = M('calendars', 'employee_leave_request')
    before = sum(_days_of(r) for r in LR.objects.filter(employee=emp, leave_type=lt, status='approved', start_date__gte=y0)
                 .filter(Q(start_date__lt=req.start_date) | Q(start_date=req.start_date, pk__lt=req.pk)))
    out, used = [], before
    for d in days:
        pct, edge = 0.0, 0.0
        for n, p in slabs:
            edge += n
            if used < edge:
                pct = p
                break
        out.append((d, pct / 100.0, unit))
        used += unit
    return out


def sync_calendar(req, approved=True):
    """Write the leave days into the attendance calendar with their unpaid part, so payroll deducts them."""
    AC = M('calendars', 'AttendanceCalendar')
    if not approved:
        AC.objects.filter(employee=req.employee, date__range=(req.start_date, req.end_date), is_manual=False,
                          remarks__startswith=TAG, leave_type=req.leave_type).delete()
        return []
    out = []
    for d, paid, unit in paid_split(req):
        if AC.objects.filter(employee=req.employee, date=d, is_manual=True).exists():
            continue
        unpaid = round(unit * (1 - paid), 2)
        AC.objects.update_or_create(employee=req.employee, date=d, defaults={
            'status': 'Leave', 'leave_type': req.leave_type, 'is_half_day': unit < 1, 'half_day_period': req.half_day_period,
            'unpaid_fraction': Decimal(str(unpaid)), 'remarks': f'{TAG}: {req.document_number} – {int(paid * 100)}% pay'})
        out.append((d, paid))
    return out


# ------------------------------------------------------------------ accrual and leave-year end
def _active_employees():
    return M('EmpManagement', 'emp_master').objects.filter(is_active=True).select_related('emp_ctgry_id')


def exit_date(emp):
    """Last working day from the employee's resignation / termination (not rejected), if any."""
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        r = R.objects.filter(employee=emp).exclude(status__iexact='rejected').exclude(status__iexact='cancelled').order_by('-last_working_date').first()
        return r.last_working_date if r else None
    except Exception:
        return None


def _accrual_employees(m_start):
    """Active employees, plus those who left during the month (their last month is prorated)."""
    E = M('EmpManagement', 'emp_master')
    ids = set(E.objects.filter(is_active=True).values_list('pk', flat=True))
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        ids |= set(R.objects.filter(last_working_date__gte=m_start).exclude(status__iexact='rejected').values_list('employee_id', flat=True))
    except Exception:
        pass
    return E.objects.filter(pk__in=ids).select_related('emp_ctgry_id')


def unpaid_days(emp, a, b):
    """Calendar days of approved leave of unpaid leave types between a and b."""
    LR = M('calendars', 'employee_leave_request')
    n = 0.0
    for r in LR.objects.filter(employee=emp, status='approved', leave_type__type='unpaid', start_date__lte=b, end_date__gte=a):
        s, e = max(r.start_date, a), min(r.end_date, b)
        n += 0.5 if r.dis_half_day else (e - s).days + 1
    return n


# ------------------------------------------------------------------ v1.12.0 days by length of service
def service_year(emp, on):
    """1 in the first year of service, 2 in the second, …"""
    return months_of_service(emp, on) // 12 + 1


def steps_of(line):
    """The line's service steps, cleaned and sorted: [(from_year, days, carry_forward_max or None)]."""
    out = []
    for s in (getattr(line, 'service_steps', None) or []):
        try:
            y, d = int(s.get('from_year')), float(s.get('days'))
        except (TypeError, ValueError, AttributeError):
            continue
        cf = s.get('carry_forward_max')
        cf = None if cf in (None, '') else float(cf)
        if y >= 2 and d >= 0:
            out.append((y, d, cf))
    return sorted(out)


def step_on(line, emp, on):
    """(days a year, the step (from_year, days, cf) or None) for the employee's service year on `on`."""
    yr = service_year(emp, on) if emp is not None and emp.emp_joined_date else 1
    hit = None
    for st in steps_of(line):
        if st[0] <= yr:
            hit = st
    return (hit[1], hit) if hit else (float(line.days_per_year or 0), None)


def days_a_year(line, emp, on):
    return step_on(line, emp, on)[0]


def blended_days(line, emp, a, b):
    """Days a year averaged over a..b, so a month (or leave year) in which the employee moves to the next step
    is earned part at the old rate and part at the new one. Returns (days a year, explanation)."""
    if not steps_of(line) or b < a:
        return float(line.days_per_year or 0), ''
    n, tot, seen, d = 0, 0.0, [], a
    while d <= b:
        v, st = step_on(line, emp, d)
        tot += v
        n += 1
        if not seen or seen[-1][0] != v:
            seen.append((v, d, st))
        d += timedelta(days=1)
    avg = tot / n
    if len(seen) == 1:
        v, _, st = seen[0]
        return avg, (f'service year {service_year(emp, b)}: {v:g} days a year' if st else '')
    parts = [f'{v:g} days a year from {d0:%d/%m}' for v, d0, _ in seen]
    return avg, 'next service step: ' + ', '.join(parts)


# ------------------------------------------------------------------ v1.12.0 days worked
def scheduled_days(emp, a, b):
    """Working days between a and b by the shift planner, else the employee's weekend and holiday calendars."""
    sd = _shift_days(emp, a, b)
    if sd is not None:
        return sorted(sd)
    wk, hol = _weekend_days(emp), _holidays(emp)
    out, d = [], a
    while d <= b:
        if d.strftime('%A').lower() not in wk and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def worked_days(line, emp, a, b):
    """(days worked, scheduled working days) between a and b: scheduled = no weekends / public holidays.
    Worked = days with a check-in (or Present in the attendance calendar) + paid leave days when the line says so."""
    sched = scheduled_days(emp, a, b)
    if not sched:
        return 0.0, 0.0
    S = set(sched)
    if getattr(line, 'worked_source', 'punch') == 'calendar':
        AC = M('calendars', 'AttendanceCalendar')
        got = {x: 1.0 for x in AC.objects.filter(employee=emp, date__range=(a, b), status='Present').values_list('date', flat=True) if x in S}
    else:
        A = M('calendars', 'Attendance')
        got = {x: 1.0 for x in A.objects.filter(employee=emp, date__range=(a, b), check_in_time__isnull=False).values_list('date', flat=True) if x in S}
    if getattr(line, 'worked_paid_leave', True):
        AC = M('calendars', 'AttendanceCalendar')
        cal_leave = {r.date: r for r in AC.objects.filter(employee=emp, date__range=(a, b), status='Leave')}
        LR = M('calendars', 'employee_leave_request')
        for r in LR.objects.filter(employee=emp, status='approved', start_date__lte=b, end_date__gte=a).select_related('leave_type'):
            x = max(r.start_date, a)
            while x <= min(r.end_date, b):
                if x in S:
                    unit = 0.5 if r.dis_half_day else 1.0
                    row = cal_leave.get(x)
                    if row is not None:
                        paid = max(unit - float(row.unpaid_fraction or 0), 0.0)
                    else:
                        paid = 0.0 if getattr(r.leave_type, 'type', '') == 'unpaid' else unit
                    got[x] = min(got.get(x, 0.0) + paid, 1.0)
                x += timedelta(days=1)
    return float(sum(got.values())), float(len(sched))


def month_fraction(line, emp, m_start, m_end, left=None):
    """(part of the month earned, explanation) by the line's prorate method: none / calendar days / fixed days / days worked."""
    joined = emp.emp_joined_date
    a = max(m_start, joined) if joined else m_start
    b = min(m_end, left) if left else m_end
    if b < a:
        return 0.0, 'not employed this month'
    if line.prorate == 'none':
        return 1.0, ''
    if line.prorate == 'worked':
        worked, total = worked_days(line, emp, a, b)
        if a > m_start or b < m_end:      # joined / left this month: out of the whole month's working days
            total = float(len(scheduled_days(emp, m_start, m_end)))
        if not total:
            return 1.0, ''
        frac = max(min(worked / total, 1.0), 0.0)
        if frac >= 1:
            return 1.0, ''
        src = 'check-ins' if getattr(line, 'worked_source', 'punch') != 'calendar' else 'attendance calendar'
        return frac, f'worked {worked:g} of {total:g} working days ({src}' + (', paid leave counted' if getattr(line, 'worked_paid_leave', True) else '') + ')'
    unpaid = unpaid_days(emp, a, b) if line.prorate_unpaid else 0.0
    if line.prorate == 'fixed':
        base = float(line.prorate_fixed_days or 30)
        first = a.day - 1 if a > m_start else 0
        last = min(b.day, base) if b < m_end else base
        worked, total = max(last - first, 0), base
    else:
        worked, total = float((b - a).days + 1), float((m_end - m_start).days + 1)
    frac = max(min((worked - unpaid) / total, 1.0), 0.0)
    if frac >= 1:
        return 1.0, ''
    parts = []
    if a > m_start:
        parts.append(f'joined {a:%d/%m}')
    if b < m_end:
        parts.append(f'left {b:%d/%m}')
    if unpaid:
        parts.append(f'{unpaid:g} unpaid days')
    return frac, f'{", ".join(parts)}: {worked - unpaid:g} of {total:g} days' + (' (fixed-day month)' if line.prorate == 'fixed' else ' (calendar days)')


def year_fraction(line, y0, y1, joined):
    """Part of the leave year from `joined` to the end, by the prorate method (days worked: calendar days, as yearly leave is given up front)."""
    if line.prorate == 'none' or not joined or joined <= y0:
        return 1.0
    if line.prorate == 'fixed':
        base = float(line.prorate_fixed_days or 30)
        months_after = (y1.year - joined.year) * 12 + (y1.month - joined.month)
        return min((months_after * base + max(base - (joined.day - 1), 0)) / (12 * base), 1.0)
    return ((y1 - joined).days + 1) / ((y1 - y0).days + 1)


def run_accrual(on=None, user_id=None, preview=False):
    """Credit the leave earned for the month that ends on/before `on` (default: last month). Safe to run again."""
    _, Line, _, Ledger = _models()
    on = on or (date.today().replace(day=1) - timedelta(days=1))
    m_end = (on.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    m_start = m_end.replace(day=1)
    tag = f'{m_end:%Y-%m}'
    rows = []
    for emp in _accrual_employees(m_start):
        p = policy_for(emp)
        if p is None or not emp.emp_joined_date or emp.emp_joined_date > m_end:
            continue
        left = exit_date(emp)
        if left and left < m_start:
            continue
        y0, y1 = leave_year(p, m_end)
        for line in p.lines.all():
            if line.accrual not in ('monthly', 'yearly'):
                continue
            if line.gender != 'B' and (emp.emp_gender or '') != line.gender:
                continue
            done = Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind__in=('accrual', 'reset'), note__startswith=tag).exists()
            if done:
                continue
            svc = months_of_service(emp, m_end)
            amt, kind, note = 0.0, 'accrual', tag
            if line.accrual == 'monthly':
                if line.first_year_uae:
                    if svc < 6:
                        amt = 0
                    elif svc == 6:
                        amt = 12.0          # 2 days for each of the first 6 months, once they are completed
                        note = f'{tag} · 6 months of service: 2 days × 6 months'
                    elif svc <= 12:       # months 7–12 of the first year: 2 days each (24 days in year one)
                        amt = 2.0
                    else:
                        frac, why = month_fraction(line, emp, m_start, m_end, left)
                        rate, step = blended_days(line, emp, m_start, m_end)
                        amt = rate / 12 * frac
                        why = ' · '.join(x for x in (step, why) if x)
                        if why:
                            note = f'{tag} · {why}'
                else:
                    if line.min_service_months and svc < line.min_service_months:
                        continue
                    frac, why = month_fraction(line, emp, m_start, m_end, left)
                    rate, step = blended_days(line, emp, m_start, m_end)
                    amt = rate / 12 * frac
                    why = ' · '.join(x for x in (step, why) if x)
                    if why:
                        note = f'{tag} · {why}'
            else:   # yearly: the year's entitlement once per leave year, in the first month the employee is eligible
                if line.min_service_months and svc < line.min_service_months:
                    continue
                conf = emp.emp_date_of_confirmation
                if line.after_probation and conf and conf > m_end:
                    continue
                if Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind__in=('accrual', 'reset'), date__range=(y0, y1)).exists():
                    continue
                if m_start == y0:     # close last leave year first if nobody did (carry-forward limit, encash / lapse)
                    closed = close_line(emp, line, y0 - timedelta(days=1), user_id=user_id, preview=preview)
                    if closed:
                        rows.append(dict(closed, kind='year_end', days=-closed['lapsed'] if closed['lapsed'] else 0,
                                         note=f'{tag} · leave year {y0 - timedelta(days=1):%Y} closed: {closed["carry_forward"]:g} carried'))
                full, step = blended_days(line, emp, y0, y1)
                full = round(full, 2)
                amt, kind, note = full, 'accrual', f'{tag} · {full:g} days for the leave year' + (f' ({step})' if step else '')
                joined = emp.emp_joined_date
                if joined and joined > y0 and not line.min_service_months and not line.after_probation and line.prorate != 'none':
                    amt = round(full * year_fraction(line, y0, y1, joined), 2)   # joined during this leave year
                    how = line.get_prorate_display().lower() if line.prorate != 'worked' else 'calendar days'
                    note = f'{tag} · {amt:g} of {full:g} days (joined {joined:%d/%m/%Y}, {how})'
            amt = round(amt, 2)
            if not amt:
                continue
            rows.append({'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} ({emp.emp_code})', 'leave_type_id': line.leave_type_id,
                         'kind': kind, 'days': amt, 'note': note})
            if not preview:
                with transaction.atomic():
                    post(emp.pk, line.leave_type_id, m_start if line.accrual == 'yearly' else m_end, kind, amt, note=note, user_id=user_id, move_balance=True)
                    try:
                        M('calendars', 'leave_accrual_transaction').objects.create(employee_id=emp.pk, leave_type_id=line.leave_type_id,
                                                                                  accrual_date=m_end, amount=Decimal(str(amt)), year=m_end.year)
                    except Exception:
                        pass
    rows += expire_carry_forward(m_end, user_id=user_id, preview=preview)
    try:
        from .compoff import expire as comp_expire
        rows += comp_expire(m_end, user_id=user_id, preview=preview)
    except Exception:
        log.exception('compensatory off expiry failed')
    return {'month': tag, 'lines': rows, 'count': len(rows), 'preview': preview}


def _carried(emp_id, lt_id, ye):
    _, _, _, Ledger = _models()
    x = Ledger.objects.filter(employee_id=emp_id, leave_type_id=lt_id, kind='carry_forward', date=ye).first()
    if not x:
        return None
    import re
    m = re.match(r'([0-9.]+) days carried', x.note or '')
    return float(m.group(1)) if m else None


def expire_carry_forward(m_end, user_id=None, preview=False):
    """Carried-forward days not used within the policy line's expiry months lapse at the end of that month."""
    _, _, _, Ledger = _models()
    out = []
    for emp in _active_employees():
        p = policy_for(emp)
        if p is None:
            continue
        y0, _ = leave_year(p, m_end)
        for line in p.lines.filter(carry_forward_expiry_months__gt=0):
            exp = (y0.replace(day=1) + timedelta(days=31 * line.carry_forward_expiry_months)).replace(day=1) - timedelta(days=1)
            if exp != m_end:
                continue
            note = f'{m_end:%Y-%m} · carry-forward expired'
            if Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind='lapsed', note__startswith=note).exists():
                continue
            cf = _carried(emp.pk, line.leave_type_id, y0 - timedelta(days=1))
            if not cf:
                continue
            used = -float(Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind__in=('taken', 'cancelled', 'encashed'),
                                                date__range=(y0, m_end)).aggregate(s=Sum('days'))['s'] or 0)
            bal = float(balance_row(emp.pk, line.leave_type_id).balance or 0)
            lapse = round(min(max(cf - max(used, 0), 0), max(bal, 0)), 2)
            if lapse <= 0:
                continue
            out.append({'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} ({emp.emp_code})', 'leave_type_id': line.leave_type_id,
                        'kind': 'lapsed', 'days': -lapse, 'note': f'{note}: {cf:g} carried, {max(used, 0):g} used by {m_end:%d/%m/%Y}'})
            if not preview:
                post(emp.pk, line.leave_type_id, m_end, 'lapsed', -lapse, note=out[-1]['note'], user_id=user_id, move_balance=True)
    return out


def cf_limit(line, emp=None, on=None):
    """Days that may be carried: the service step's limit, else the line's limit; empty = all for monthly-earned leave,
    none for leave given yearly."""
    if emp is not None and on is not None:
        _, st = step_on(line, emp, on)
        if st and st[2] is not None:
            return float(st[2])
    if line.carry_forward_max is not None:
        return float(line.carry_forward_max)
    return None if line.accrual == 'monthly' else 0.0


def close_line(emp, line, ye, user_id=None, preview=False):
    """Close one leave type of one employee at the leave-year end `ye`. None when already closed."""
    _, _, _, Ledger = _models()
    if Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind='carry_forward', date=ye).exists():
        return None
    bal = float(balance_row(emp.pk, line.leave_type_id).balance or 0)
    lim = cf_limit(line, emp, ye)
    carry = bal if lim is None or bal <= 0 else min(bal, lim)
    excess = round(max(bal - carry, 0), 2)
    action = line.excess_action if excess > 0 else 'none'
    row = {'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} ({emp.emp_code})', 'leave_type_id': line.leave_type_id,
           'balance': bal, 'carry_forward': round(carry if action != 'keep' else bal, 2), 'excess': excess, 'action': action,
           'lapsed': excess if action == 'lapse' else 0}
    if preview:
        return row
    post(emp.pk, line.leave_type_id, ye, 'carry_forward', 0, note=f'{row["carry_forward"]:g} days carried forward', user_id=user_id)
    if action == 'lapse':
        post(emp.pk, line.leave_type_id, ye, 'lapsed', -excess, note='Above the carry-forward limit', user_id=user_id, move_balance=True)
    elif action == 'encash':
        enc = encashment_request(emp, line.leave_type_id, excess, remarks=f'Leave year end {ye:%d/%m/%Y}: above the carry-forward limit')
        row['encashment_id'] = enc.pk if enc else None
    return row


def run_year_end(year_end=None, user_id=None, preview=False, policy_ids=None):
    """Leave-year end: days above the carry-forward limit are kept (flagged), sent for encashment or lapse, per policy line."""
    rows = []
    for emp in _active_employees():
        p = policy_for(emp)
        if p is None or (policy_ids is not None and p.pk not in policy_ids):
            continue
        ye = year_end or (leave_year(p, date.today())[0] - timedelta(days=1))
        for line in p.lines.filter(accrual__in=('monthly', 'yearly')):
            if not (line.gender == 'B' or (emp.emp_gender or '') == line.gender):
                continue
            row = close_line(emp, line, ye, user_id=user_id, preview=preview)
            if row:
                rows.append(row)
    return {'year_end': (year_end.isoformat() if year_end else None), 'lines': rows, 'preview': preview}


# ------------------------------------------------------------------ encashment
def basic_of(emp):
    try:
        from DashboardManagement.reports import basic_by_employee
        return float(basic_by_employee([emp.pk]).get(emp.pk, 0))
    except Exception:
        return 0.0


def encashment_request(emp, lt_id, days, remarks=''):
    LE = M('PayrollManagement', 'LeaveEncashment')
    basic = basic_of(emp)
    bal = float(balance_row(emp.pk, lt_id).balance or 0)
    return LE.objects.create(employee=emp, leave_type_id=lt_id, leave_balance=Decimal(str(bal)), encashment_days=Decimal(str(round(days, 2))),
                             basic_salary=Decimal(str(basic)), total_salary=Decimal(str(basic)), fixed_days=Decimal('30'),
                             formula_used='basic_salary / 30 * encashment_days',
                             encashment_amount=Decimal(str(round(basic / 30 * days, 2))), status='pending', remarks=remarks)


def check_encashment(enc):
    """Errors that stop an encashment from being approved."""
    errs = []
    emp, lt_id, days = enc.employee, enc.leave_type_id, float(enc.encashment_days or 0)
    if days <= 0:
        errs.append('Days to encash must be more than 0.')
    bal = float(balance_row(emp.pk, lt_id).balance or 0)
    if days > bal + 1e-9:
        errs.append(f'Only {bal:g} days in the balance.')
    line = line_for(emp, lt_id)
    if line is not None:
        if not line.encashable:
            errs.append(f'The policy {line.policy.name} does not allow encashing this leave type.')
        if line.encash_max_per_year:
            LE = M('PayrollManagement', 'LeaveEncashment')
            y0, y1 = leave_year(line.policy, date.today())
            done = sum(float(x.encashment_days) for x in LE.objects.filter(employee=emp, leave_type_id=lt_id, status__in=('approved', 'processed'),
                                                                          approved_at__date__gte=y0, approved_at__date__lte=y1).exclude(pk=enc.pk))
            if done + days > line.encash_max_per_year:
                errs.append(f'At most {line.encash_max_per_year:g} days can be encashed a leave year; {done:g} already encashed.')
    return errs
