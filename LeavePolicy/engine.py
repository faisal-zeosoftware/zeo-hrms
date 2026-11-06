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
import copy
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from Translations.i18n import tr  # v1.15.0 language packs: messages in the user's language

log = logging.getLogger(__name__)

import threading as _threading
_run = _threading.local()   # v1.26.0: per-run look-ups of the monthly accrual (see run_accrual)
M = apps.get_model
ACTIVE_STATUSES = ('pending', 'approved')
TAG = 'Leave rules'      # remark on attendance-calendar days written by the engine


def _models():
    from .models import EmployeeLeavePolicy, LeaveLedger, LeavePolicy, LeavePolicyLine
    return LeavePolicy, LeavePolicyLine, EmployeeLeavePolicy, LeaveLedger


def _lr():
    from . import leave_rules   # v1.65.0 (items 82–97)
    return leave_rules


# ------------------------------------------------------------------ policy lookup
def managed_type_ids():
    """Leave types handled by an active policy (the old accrual / reset jobs skip them).
    v1.62.1: with the branch copies of those types (see twin_type) – the policy line covers them too."""
    _, Line, _, _ = _models()
    try:
        ids = set(Line.objects.filter(policy__active=True).values_list('leave_type_id', flat=True))
    except Exception:        # table not migrated yet
        return set()
    try:
        by_id, _, _ = _type_index()
        codes = {(by_id[j][0], by_id[j][1]) for j in ids if j in by_id and by_id[j][0] is not None and by_id[j][1]}
        names = {(by_id[j][0], by_id[j][2]) for j in ids if j in by_id and by_id[j][0] is not None and by_id[j][2]}
        for i, (b, c, n) in by_id.items():
            if i in ids or b is None:
                continue
            if any(cb != b and cc == c for cb, cc in codes) or any(nb != b and nn == n for nb, nn in names):
                ids.add(i)
    except Exception:
        log.exception('branch copies of the managed leave types')
    return ids


# ------------------------------------------------------------------ v1.62.1 branch copies of a leave type
# A company with several branches can have a copy of a leave type per branch ("Annual Leave" AL-GDI of the head office and
# "Annual Leave" AL-GDI of the Dubai branch – OrganisationManager/branch_setup.py copies them for a new branch). Policy
# lines name one leave type id, so before 1.62.1 the employees of the other branch earned their monthly leave on the head-office
# type they cannot request, and their requests on their own branch's type had no policy rule at all (no balance check,
# notice, first-year rule, pay slabs). A line now covers the copies of its type in the other branches: an employee gets the
# line on the copy of their own branch, unless the policy has a line of its own for that copy.
def _type_index():
    """({id: (branch_id, code, name)}, {(branch_id, code): id}, {(branch_id, name): id}) – code and name lower case."""
    cached = getattr(_run, 'types', None)
    if cached is not None:
        return cached
    by_id, by_code, by_name = {}, {}, {}
    for i, b, c, n in M('calendars', 'leave_type').objects.values_list('id', 'branch_id', 'code', 'name').order_by('id'):
        c, n = (c or '').strip().lower(), (n or '').strip().lower()
        by_id[i] = (b, c, n)
        if c:
            by_code.setdefault((b, c), i)
        if n:
            by_name.setdefault((b, n), i)
    return by_id, by_code, by_name


def twin_type(lt_id, branch_id):
    """The copy of leave type `lt_id` in branch `branch_id` (same code, else same name), or None – also when the type is
    company-wide or already of that branch."""
    by_id, by_code, by_name = _type_index()
    me = by_id.get(lt_id)
    if not me or me[0] is None or branch_id is None or me[0] == branch_id:
        return None
    return by_code.get((branch_id, me[1])) or by_name.get((branch_id, me[2]))


def emp_lines(emp, p, lines=None):
    """The policy lines as they apply to this employee: a line whose leave type belongs to another branch is given on the
    copy of the employee's branch (a copy of the line object with that leave_type_id, never saved; `_twin_of` = the line's
    own type); it is left out when the policy has a line of its own for that copy. Lines without a copy stay as they are."""
    allp = list(p.lines.all())
    own = {l.leave_type_id for l in allp}
    out = []
    for l in (allp if lines is None else lines):
        tw = twin_type(l.leave_type_id, getattr(emp, 'emp_branch_id_id', None))
        if tw is None:
            out.append(l)
        elif tw not in own:
            c = copy.copy(l)
            c._twin_of = l.leave_type_id
            c.leave_type_id = tw
            out.append(c)
    return out


def _run_maps():
    """v1.26.0 – policies, overrides and lines read once for a whole accrual run (policy_for ran 2–3 queries and
    p.lines.all() one more for every employee: 1,001 lines took ~29 s to preview and ~90 s to credit)."""
    Policy, _, EmpPolicy, _ = _models()
    pols = list(Policy.objects.filter(active=True).prefetch_related('lines').order_by('id'))
    by_id = {p.pk: p for p in pols}
    over = {}
    for o in EmpPolicy.objects.filter(policy__active=True).select_related('policy'):
        over.setdefault(o.employee_id, by_id.get(o.policy_id, o.policy))
    by_cat = {}
    for p in pols:
        for c in (p.category_ids or []):
            by_cat.setdefault(c, p)
    default = next((p for p in pols if p.is_default), None)
    return {'over': over, 'by_cat': by_cat, 'default': default}


def policy_for(emp):
    Policy, _, EmpPolicy, _ = _models()
    if emp is None:
        return None
    maps = getattr(_run, 'maps', None)
    if maps is not None:
        return maps['over'].get(emp.pk) or (maps['by_cat'].get(emp.emp_ctgry_id_id) if emp.emp_ctgry_id_id else None) or maps['default']
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
    line = p.lines.filter(leave_type_id=leave_type_id).first()
    if line is None:   # v1.62.1: the employee's branch copy of a type the policy has
        line = next((l for l in emp_lines(emp, p) if l.leave_type_id == leave_type_id), None)
    return line


def months_of_service(emp, on):
    j = emp.emp_joined_date
    if not j or on < j:
        return 0
    m = (on.year - j.year) * 12 + (on.month - j.month)
    # v1.62.4 (item 66): the last day of a short month completes the month for a joiner on the 29th–31st (joined 31/03: 6 months
    # on 30/09) – before, 30/09 gave 5 and 31/10 gave 7, so the 6-month point (12-day first-year lump) was never reached
    import calendar as _cal
    if on.day < min(j.day, _cal.monthrange(on.year, on.month)[1]):
        m -= 1
    return max(m, 0)


def leave_year(policy, d):
    start_month = getattr(policy, 'leave_year_start', 1) or 1
    y = d.year if d.month >= start_month else d.year - 1
    start = date(y, start_month, 1)
    end = date(y + 1, start_month, 1) - timedelta(days=1)
    return start, end


# ------------------------------------------------------------------ counting days
def _weekend_days(emp, day=None):
    # v1.62.6 (item 22): the one weekend rule (calendars/workdays.py – most specific assignment wins, category counted)
    from calendars.workdays import weekend_names
    return {n.lower() for n in weekend_names(emp, day)}


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
    shift information fall back to the weekend calendar + holidays inside the planner.
    v1.62.6 (item 22): always the one resolver calendars/workdays.py (also without the shift planner: weekend calendar with
    the most specific assignment, then public holidays) – never None."""
    from calendars.workdays import working_days as _wd
    return set(_wd(emp, a, b))


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
        b._lp_ledger = True   # v1.62.4 (item 79): the ledger line is written – the balance guard (signals.py) adds nothing
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
    else:   # v1.67.5 (bugs) L5: the gender rule of the type (policy line, or maternity = women) also without a policy line for the employee
        from calendars.utils import leave_gender
        g = leave_gender(lt)
        if g and (emp.emp_gender or '').upper()[:1] != g:
            out.append(tr('{0} is only for {1} employees.', lt.name, tr('female') if g == "F" else tr('male')))
    return out


def validate_request(req):
    """Raise ValidationError with every rule the request breaks. Runs when a request is created or its dates / type change."""
    errors = []
    emp, lt = req.employee, req.leave_type
    if req.end_date < req.start_date:
        raise ValidationError(tr('The end date is before the start date.'))
    LR = M('calendars', 'employee_leave_request')
    clash = LR.objects.filter(employee=emp, status__in=ACTIVE_STATUSES, start_date__lte=req.end_date, end_date__gte=req.start_date)
    if req.pk:
        clash = clash.exclude(pk=req.pk)
    c = clash.select_related('leave_type').first()
    if c:
        errors.append(tr('{0} already has {1} {2} from {3:%d/%m/%Y} to {4:%d/%m/%Y} ({5}).', emp.emp_first_name, c.leave_type.name, c.document_number or "", c.start_date, c.end_date, c.status))
    errors += leave_type_problems(req)
    try:   # v1.51.0 blackout periods that block leave
        from .conflicts import blackout_errors
        errors += blackout_errors(req)
    except Exception:
        pass
    errors += request_day_problems(req)   # v1.62.4 (items 71, 77, 81): last working day, no working day, short leave in hours
    line = line_for(emp, lt.pk)
    try:   # v1.65.0 (items 82, 91, 92, 97): birth date and window, HR-confirmed eligibility, document above N days, notice period
        _d = float(req.applied_days or 0) if is_hourly(lt) else float(req.calculate_leave_days())
        errors += _lr().request_problems(req, line, _d)
    except ValidationError:
        raise
    except Exception:
        log.exception('v1.65.0 leave rules')
    if line is None:
        if errors:
            raise ValidationError(errors)
        return
    policy = line.policy
    today = date.today()
    hourly = is_hourly(lt)   # v1.62.4 (item 81): a type counted in hours – the request's hours, not days
    days = float(req.applied_days or 0) if hourly else leave_days(emp, req.start_date, req.end_date, line.count_days, bool(req.dis_half_day))
    _sp = None if hourly else _lr().special_days(emp, lt, line, req.start_date, req.end_date, bool(req.dis_half_day))
    if _sp is not None:   # v1.65.0 (items 86, 87): sandwich rule / calendar days except public holidays
        days = float(len(_sp))
    if line.gender != 'B' and (emp.emp_gender or '') != line.gender:
        _gm = tr('{0} is only for {1} employees.', lt.name, tr('female') if line.gender == "F" else tr('male'))   # v1.67.5 (bugs): the word translated too
        if _gm not in errors:   # v1.67.5 (bugs): not twice when the type's own rule said it already
            errors.append(_gm)
    svc = months_of_service(emp, req.start_date)
    if line.min_service_months and svc < line.min_service_months:
        errors.append(tr('{0} needs {1} months of service; {2} will have {3} on {4:%d/%m/%Y}.', lt.name, line.min_service_months, emp.emp_first_name, svc, req.start_date))
    if line.first_year_uae and svc < 6:
        errors.append(tr('{0} can be taken after 6 months of service (UAE Labour Law); {1} will have {2} months.', lt.name, emp.emp_first_name, svc))
    conf = emp.emp_date_of_confirmation
    if line.after_probation and conf and req.start_date < conf:
        errors.append(tr('{0} is available after probation ({1:%d/%m/%Y}). During probation use unpaid leave.', lt.name, conf))
    # v1.18.0: the notice period is for the employee's own requests; HR entering leave on the employee's behalf (emergency
    # leave, leave taken before it was recorded) was refused with "must be requested 14 days in advance"
    by_hr = bool(getattr(req, 'created_by_id', None)) and req.created_by_id != getattr(emp, 'users_id', None)
    # v1.62.4 (item 70): the notice applies when the start date is moved too – editing the dates of a request made in time
    # (or of an old one) to start sooner skipped the check, which ran on new requests only
    was_start = LR.objects.filter(pk=req.pk).values_list('start_date', flat=True).first() if req.pk else None
    fresh = req.pk is None or was_start is None or was_start != req.start_date
    if fresh and _lr().is_extension(req) and _lr().settings().extension_skips_notice:
        fresh = False   # v1.65.0 (item 84): an extension of an approved leave is asked while away – no notice
    if line.notice_days and fresh and not by_hr and (req.start_date - today).days < line.notice_days:
        errors.append(tr('{0} must be requested {1} days in advance (from {2:%d/%m/%Y}).', lt.name, line.notice_days, today + timedelta(days=line.notice_days)))
    if line.max_per_request and days > line.max_per_request:
        errors.append(tr('{0} is at most {1:g} days per request; this request is {2:g} days.', lt.name, line.max_per_request, days))
    y0, y1 = leave_year(policy, req.start_date)
    if line.max_per_year and not _lr().skip_year_limit(req):   # v1.65.0 (item 82): with the birth rule the days are per birth
        used = used_in(emp.pk, lt.pk, y0, y1, exclude_id=req.pk)
        if used + days > line.max_per_year:
            errors.append(tr('{0} is at most {1:g} days a leave year; {2:g} already taken or requested, {3:g} left.', lt.name, line.max_per_year, used, max(line.max_per_year - used, 0)))
    if line.max_times_in_service:
        n = LR.objects.filter(employee=emp, leave_type=lt, status__in=ACTIVE_STATUSES).exclude(pk=req.pk).count()
        if n >= line.max_times_in_service:
            errors.append(tr('{0} can be taken {1} time(s) in service; already taken.', lt.name, line.max_times_in_service))
    if _lr().document_needed(req, line, days) and not req.lv_document:   # v1.65.0 (item 92): only above N days when the type says so
        errors.append(tr('{0} needs a supporting document (e.g. medical report, certificate) – attach it to the request.', lt.name))
    if line.accrual in ('monthly', 'yearly', 'earned') and lt.type != 'unpaid' and not (line.allow_negative or lt.negative):
        bal = float(balance_row(emp.pk, lt.pk).balance or 0)
        if _lr().allow_projected(lt) and not hourly:   # v1.65.0 (item 88): the days earned before the leave starts count
            bal = round(bal + _lr().projected_extra(emp, line, req.start_date), 2)
        pending = used_in(emp.pk, lt.pk, date(1900, 1, 1), date(2999, 12, 31), exclude_id=req.pk, statuses=('pending',))
        if hourly and days > bal - pending + 1e-9:   # v1.62.4 (item 81): the balance of an hourly type is in hours
            errors.append(tr('Not enough {0}: {1:g} hours left (balance {2:g} hours, {3:g} hours waiting for approval), this request {4:g} hours.',
                             lt.name, max(bal - pending, 0), bal, pending, days))
        elif days > bal - pending + 1e-9:
            errors.append(tr('Not enough {0}: balance {1:g} days', lt.name, bal) + (tr(' (projected to {0:%d/%m/%Y})', req.start_date) if _lr().allow_projected(lt) else '') + (tr(', {0:g} days waiting for approval', pending) if pending else '') + tr(', this request {0:g} days.', days))
    if errors:
        raise ValidationError(errors)


# ------------------------------------------------------------------ v1.62.4 leave request checks (items 71, 77, 81)
SHORT_LEAVE_MAX_HOURS = 8.0


def is_hourly(lt):
    """v1.62.4 (item 81): a leave type counted in hours (Short Leave (hours)): its balance, request and ledger are hours."""
    return getattr(lt, 'unit', 'days') == 'hours'


def request_day_problems(req):
    """v1.62.4: rules on the days of a request, for every leave type (with or without a policy line).
    * item 71 – no leave after the employee's last working day (resignation / termination not rejected): the days after it are
      not employment days (the final settlement pays the unused balance instead); before, such leave was approved and the
      settlement gave back only leave that started after the last day.
    * item 77 – a request with no working day in it (a half day / days on a weekend or public holiday) was accepted with 0 days.
    * item 81 – short leave in hours: one day, a working day, more than 0 and at most 8 hours."""
    out, emp, lt = [], req.employee, req.leave_type
    lwd = exit_date(emp)
    if lwd and req.end_date > lwd:
        out.append(tr("{0}'s last working day is {1:%d/%m/%Y}: leave cannot go past it (unused leave is paid in the final settlement).",
                      emp.emp_first_name, lwd))
    if is_hourly(lt):
        hrs = float(req.applied_days or 0)
        if req.start_date != req.end_date:
            out.append(tr('Short leave in hours is for one day: the start and end dates must be the same.'))
        elif hrs <= 0 or hrs > SHORT_LEAVE_MAX_HOURS:
            out.append(tr('Give the hours of the short leave: more than 0 and at most {0:g}.', SHORT_LEAVE_MAX_HOURS))
        elif not scheduled_days(emp, req.start_date, req.start_date):
            out.append(tr('{0:%d/%m/%Y} is not a working day: short leave is taken on a working day.', req.start_date))
        return out
    if req.dis_half_day and not scheduled_days(emp, req.start_date, req.start_date):   # half a day off on a day off: nothing to take
        out.append(tr('{0:%d/%m/%Y} is not a working day (weekend / public holiday): a half day is taken on a working day.', req.start_date))
        return out
    try:
        n = float(req.calculate_leave_days())   # the days the request takes, counted as the model counts them
    except Exception:
        n = None
    if n is not None and n <= 0:
        out.append(tr('There is no working day from {0:%d/%m/%Y} to {1:%d/%m/%Y} (weekend / public holiday): the request would take 0 days.',
                      req.start_date, req.end_date))
    return out


# ------------------------------------------------------------------ pay slabs → attendance calendar → payroll
def slabs_for(emp, lt):
    line = line_for(emp, lt.pk)
    if line and line.pay_slabs:
        return [(float(d), float(p)) for d, p in line.pay_slabs]
    if getattr(lt, 'enable_leave_pay_rule', False):
        return [(float(r.days), float(r.pay_percentage)) for r in lt.pay_rules.all().order_by('sequence')]
    return []


# v1.62.2 (item 49): sick / maternity pay steps from all approved days of the leave year in date order, split at the year end
def _req_days(req, line=None):
    """The days of a request that count (working days only when the policy / leave type counts working days)."""
    emp, lt = req.employee, req.leave_type
    _sp = _lr().special_days(emp, lt, line, req.start_date, req.end_date, bool(req.dis_half_day))
    if _sp is not None:   # v1.65.0 (items 86, 87)
        return _sp
    mode = line.count_days if line else ('calendar' if lt.include_weekend else 'working')
    wk, hol = (_weekend_days(emp), _holidays(emp)) if mode == 'working' else (set(), set())
    sd = _shift_days(emp, req.start_date, req.end_date) if mode == 'working' else None
    days = []
    d = req.start_date
    while d <= req.end_date:
        if mode != 'working' or (d in sd if sd is not None else (d.strftime('%A').lower() not in wk and d not in hol)):
            days.append(d)
        d += timedelta(days=1)
    return days


def _year_of(policy, d):
    return leave_year(policy, d) if policy else (date(d.year, 1, 1), date(d.year, 12, 31))


def year_split(emp, lt, y0, y1, extra=None):
    """v1.62.2 (item 49): {(request id, day): (paid fraction, unit)} for every approved day of this leave type in the leave year
    y0–y1, the pay slabs given in date order over ALL approved days of the year – before, each request counted only the
    requests that started before it when it was approved (out-of-order approvals, cancellations and requests crossing the
    leave year gave the wrong steps). `extra` = a request not approved yet (preview / being approved) counted with the others."""
    line = line_for(emp, lt.pk)
    slabs = slabs_for(emp, lt)
    LR = M('calendars', 'employee_leave_request')
    reqs = {r.pk: r for r in LR.objects.filter(employee=emp, leave_type=lt, status='approved', start_date__lte=y1, end_date__gte=y0)}
    if extra is not None:
        reqs[extra.pk] = extra
    items = []
    for r in reqs.values():
        unit = 0.5 if r.dis_half_day else 1.0
        for d in approved_and_rest(r, _req_days(r, line))[0]:   # v1.62.4 (item 67): days not approved take no pay step
            if y0 <= d <= y1:
                items.append((d, r.pk or 0, unit))
    items.sort()
    out, used = {}, 0.0
    for d, pk, unit in items:
        if lt.type == 'unpaid':
            pct = 0.0
        elif not slabs:
            pct = 100.0
        else:
            pct, edge = 0.0, 0.0
            for n, p in slabs:
                edge += n
                if used < edge:
                    pct = p
                    break
        out[(pk, d)] = (pct / 100.0, unit)
        used += unit
    return out


def paid_split(req):
    """[(day, paid fraction of the day, unit)] for an approved request: unpaid types 0 %, pay slabs by the days taken this leave year.
    v1.62.2 (item 49): the steps come from all approved days of the leave year in date order (year_split), and a request
    crossing the leave-year end is split at the boundary (the days of the new year start the new year's steps)."""
    emp, lt = req.employee, req.leave_type
    if is_hourly(lt):   # v1.62.4 (item 81): short leave in hours is not a leave day (AttendancePlus covers its late / early minutes)
        return []
    line = line_for(emp, lt.pk)
    days, rest = approved_and_rest(req, _req_days(req, line))   # v1.62.4 (item 67)
    unit = 0.5 if req.dis_half_day else 1.0
    tail = [(d, 0.0, unit) for d in rest]   # v1.62.4 (item 67): days the approver did not approve – unpaid
    if lt.type == 'unpaid':
        return [(d, 0.0, unit) for d in days] + tail
    slabs = slabs_for(emp, lt)
    if not slabs:
        return [(d, 1.0, unit) for d in days] + tail
    policy = line.policy if line else None
    out, years = [], {}
    for d in days:
        y = _year_of(policy, d)
        if y not in years:
            years[y] = year_split(emp, lt, y[0], y[1], extra=req)
        paid, u = years[y].get((req.pk or 0, d), (1.0, unit))
        out.append((d, paid, u))
    return out + tail


def approved_and_rest(req, days):
    """v1.62.4 (item 67): (days approved, days not approved) of a request, in date order. The approver may approve fewer days
    than asked (Leave approvals › approved days): the balance and the leave ledger take the approved days, so the first approved
    days of the request are leave and the rest of its days are written to the attendance calendar as unpaid (100 % unpaid) –
    before, all days were leave with full pay (5 days paid for 3 taken from the balance)."""
    if req.status != 'approved' or is_hourly(getattr(req, 'leave_type', None)):
        return list(days), []
    ok = float(req.approved_days or 0)
    asked = float(req.applied_days or req.number_of_days or 0)
    if ok <= 0 or ok >= asked - 1e-9:
        return list(days), []
    unit = 0.5 if req.dis_half_day else 1.0
    keep, n = [], 0.0
    for d in days:
        if n + unit > ok + 1e-9:
            break
        keep.append(d)
        n += unit
    return keep, list(days)[len(keep):]


def resync_year(req, user_id=None):
    """v1.62.2 (item 49): after a request with pay slabs is approved or cancelled, the other approved requests of the same
    leave type in the same leave year(s) are written again into the attendance calendar – their steps may have moved."""
    emp, lt = req.employee, req.leave_type
    if lt.type == 'unpaid' or not slabs_for(emp, lt):
        return 0
    line = line_for(emp, lt.pk)
    policy = line.policy if line else None
    a, b = _year_of(policy, req.start_date), _year_of(policy, req.end_date)
    LR = M('calendars', 'employee_leave_request')
    n = 0
    for r in LR.objects.filter(employee=emp, leave_type=lt, status='approved', start_date__lte=b[1], end_date__gte=a[0]).exclude(pk=req.pk):
        sync_calendar(r, approved=True)
        n += 1
    return n


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
    exits = getattr(_run, 'exits', None)
    if exits is not None:
        lwd = exits.get(emp.pk)
    else:
        try:
            R = M('EmpManagement', 'EmployeeResignation')
            r = R.objects.filter(employee=emp).exclude(status__iexact='rejected').exclude(status__iexact='cancelled').order_by('-last_working_date').first()
            lwd = r.last_working_date if r else None
        except Exception:
            return None
    # v1.62.4 (item 72): a resignation of an earlier employment (last working day before the current joining date – re-hired)
    # does not end this employment; before, a re-hired employee earned nothing (0 instead of 2.5 a month)
    if lwd and emp.emp_joined_date and lwd < emp.emp_joined_date:
        return None
    return lwd


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
    # v1.65.0 (item 89): the company setting makes unpaid leave (and, wider, absent days) reduce annual leave earned
    unpaid = unpaid_days(emp, a, b) if (line.prorate_unpaid or _lr().reduce_unpaid(line)) else 0.0
    unpaid += _lr().absent_days(emp, a, b, line)
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
        parts.append(f'{unpaid:g} unpaid days')   # v1.65.0 (item 89): absent days counted here too when the company counts them
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


def _month_days(rate, frac, m_end, y0):
    """v1.62.4 (item 76): days of a monthly accrual, rounded so that the months of a leave year add up to the yearly days
    (22 a year: 1.83, 1.84, 1.83 … = 22.00; before every month was rounded on its own: 12 × 1.83 = 21.96, 26 → 26.04).
    A full month gets round(rate × k / 12) − round(rate × (k − 1) / 12), k = the month's place in the leave year;
    a part month (joined / left / unpaid days) its own amount, rounded."""
    if frac >= 1 and y0 is not None:
        k = (m_end.year - y0.year) * 12 + (m_end.month - y0.month) + 1
        if 1 <= k <= 12:
            return round(round(rate * k / 12, 2) - round(rate * (k - 1) / 12, 2), 2)
    return round(rate / 12 * frac, 2)


def monthly_amount(line, emp, m_start, m_end, left=None, y0=None, projected=False):
    """v1.62.4 (items 69, 76) – (days earned in the month m_start..m_end, explanation) of a monthly policy line; None when the
    employee is not eligible yet (minimum service). Used by the monthly accrual and by "to earn by year end" (item 74).
    `projected`: a month not over yet – days worked are not known, so a full month (part month only for joining / leaving)."""
    svc = months_of_service(emp, m_end)
    if projected and line.prorate == 'worked':
        line = copy.copy(line)
        line.prorate = 'calendar'
    if line.first_year_uae:
        if svc < 6:
            return 0.0, ''
        if svc == 6:
            return 12.0, '6 months of service: 2 days × 6 months'   # 2 days for each of the first 6 months, once completed
        if svc <= 12:
            # months 7–12 of the first year: 2 days a month (24 days in year one) – v1.62.4 (item 69): by the line's prorate
            # method like any other month (left on the 3rd: 0.2, unpaid days when the line counts them); before always 2.0
            frac, why = month_fraction(line, emp, m_start, m_end, left)
            return round(2.0 * frac, 2), why
    elif line.min_service_months and svc < line.min_service_months:
        return None
    frac, why = month_fraction(line, emp, m_start, m_end, left)
    rate, step = blended_days(line, emp, m_start, m_end)
    return _month_days(rate, frac, m_end, y0), ' · '.join(x for x in (step, why) if x)


def to_earn_by_year_end(emp, line, today=None, ye=None):
    """v1.62.4 (item 74): days of a monthly line still to be credited up to the leave-year end `ye` – each month not credited
    yet (last month while the accrual has not run, this month and the months after), with the first-year rule, joining /
    leaving and the rounding of the accrual. Before: days a year × (months to the year end, this month not counted) / 12
    (2.5 on 2 November instead of 5; 2.5 instead of 14 for a joiner reaching 6 months in November)."""
    if line.accrual != 'monthly':
        return 0.0
    today = today or date.today()
    p = line.policy
    y0, y1 = leave_year(p, today)
    ye = ye or y1
    j = emp.emp_joined_date
    if not j:
        return 0.0
    if line.gender != 'B' and (emp.emp_gender or '') != line.gender:
        return 0.0
    left = exit_date(emp)
    _, _, _, Ledger = _models()
    types = [line.leave_type_id] + ([line._twin_of] if getattr(line, '_twin_of', None) else [])
    notes = list(Ledger.objects.filter(employee_id=emp.pk, leave_type_id__in=types, kind__in=('accrual', 'reset'),
                                       date__gte=y0 - timedelta(days=31), date__lte=ye + timedelta(days=31)).values_list('note', flat=True))
    done = {n[:7] for n in notes if n}
    m_start = max((today.replace(day=1) - timedelta(days=1)).replace(day=1), y0)   # last month (if not credited yet) onwards
    total = 0.0
    while m_start <= ye:
        m_end = (m_start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if f'{m_end:%Y-%m}' not in done and j <= m_end and not (left and left < m_start):
            got = monthly_amount(line, emp, m_start, m_end, left, y0, projected=m_end >= today)
            if got:
                total += got[0]
        m_start = m_end + timedelta(days=1)
    return round(total, 2)


def run_accrual(on=None, user_id=None, preview=False):
    """Credit the leave earned for the month that ends on/before `on` (default: last month). Safe to run again.
    v1.26.0: look-ups are read once per run and no field-by-field history is written (one summary note instead)."""
    _run.maps = _run_maps()
    _run.types = _type_index()   # v1.62.1 branch copies of the leave types, read once for the run
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        ex = {}
        for eid, lwd in R.objects.exclude(status__iexact='rejected').exclude(status__iexact='cancelled').exclude(last_working_date__isnull=True) \
                .values_list('employee_id', 'last_working_date'):
            if eid not in ex or lwd > ex[eid]:
                ex[eid] = lwd
        _run.exits = ex
    except Exception:
        _run.exits = None
    try:
        from Chatter.tracking import paused
    except Exception:   # pragma: no cover
        from contextlib import nullcontext as paused
    try:
        with paused():
            res = _run_accrual(on, user_id, preview)
    finally:
        _run.maps = None
        _run.exits = None
        _run.types = None
    if not preview and res.get('count'):
        try:
            from Chatter.models import Message
            Message.objects.create(model='LeavePolicy.leavepolicy', object_id='accrual', author_id=user_id,
                                   body=f"Monthly accrual {res['month']}: {res['count']} balance line(s) credited.")
        except Exception:
            pass
    return res


def _run_accrual(on=None, user_id=None, preview=False):
    _, Line, _, Ledger = _models()
    on = on or (date.today().replace(day=1) - timedelta(days=1))
    m_end = (on.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    m_start = m_end.replace(day=1)
    tag = f'{m_end:%Y-%m}'
    rows = []
    # v1.26.0: what was credited already, read once (was two EXISTS queries per employee and line)
    done_tag = set(Ledger.objects.filter(kind__in=('accrual', 'reset'), note__startswith=tag).values_list('employee_id', 'leave_type_id'))
    near = defaultdict(list)
    for eid, lt, d in Ledger.objects.filter(kind__in=('accrual', 'reset'), date__gte=m_end - timedelta(days=740), date__lte=m_end + timedelta(days=370)) \
            .values_list('employee_id', 'leave_type_id', 'date'):
        near[(eid, lt)].append(d)
    for emp in _accrual_employees(m_start):
        p = policy_for(emp)
        if p is None or not emp.emp_joined_date or emp.emp_joined_date > m_end:
            continue
        left = exit_date(emp)
        if left and left < m_start:
            continue
        y0, y1 = leave_year(p, m_end)
        for line in emp_lines(emp, p):   # v1.62.1: on the employee's branch copy of the leave type
            if line.accrual not in ('monthly', 'yearly'):
                continue
            if line.gender != 'B' and (emp.emp_gender or '') != line.gender:
                continue
            was = getattr(line, '_twin_of', None)   # credited on the line's own type before 1.62.1 – never twice for a month
            done = (emp.pk, line.leave_type_id) in done_tag or (was is not None and (emp.pk, was) in done_tag)
            if done:
                continue
            svc = months_of_service(emp, m_end)
            amt, kind, note = 0.0, 'accrual', tag
            if line.accrual == 'monthly':
                got = monthly_amount(line, emp, m_start, m_end, left, y0)   # v1.62.4 (items 69, 76)
                if got is None:
                    continue
                amt, why = got
                if why:
                    note = f'{tag} · {why}'
            else:   # yearly: the year's entitlement once per leave year, in the first month the employee is eligible
                if line.min_service_months and svc < line.min_service_months:
                    continue
                conf = emp.emp_date_of_confirmation
                if line.after_probation and conf and conf > m_end:
                    continue
                # v1.65.0 (item 96): a grant of an earlier employment (before the re-hire date = joining date) does not count
                if any(y0 <= d <= y1 and d >= emp.emp_joined_date.replace(day=1) for d in list(near.get((emp.pk, line.leave_type_id), []))
                       + list(near.get((emp.pk, was), []) if was is not None else [])):
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
        for line in [l for l in emp_lines(emp, p) if (l.carry_forward_expiry_months or 0) > 0]:   # v1.26.0: prefetched; v1.62.1 branch copies
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


def balance_on(emp_id, lt_id, d):
    """v1.62.2 (item 58): the balance at the end of day d – today's balance less every ledger movement dated after d (leave of
    the new year taken, cancelled or earned, adjustments). The year end was closed on today's balance: run after the new year
    had started, the January accrual was carried / lapsed / encashed and the leave already taken in January was not."""
    _, _, _, Ledger = _models()
    now = float(balance_row(emp_id, lt_id).balance or 0)
    after = float(Ledger.objects.filter(employee_id=emp_id, leave_type_id=lt_id, date__gt=d).aggregate(s=Sum('days'))['s'] or 0)
    return round(now - after, 2)


def ledger_mismatches(employee_ids=None):
    """v1.62.4 (item 79): balances that differ from the sum of their ledger lines – [(employee id, leave type id, balance,
    ledger sum)]. Should be empty: every balance change writes a ledger line (engine.post, leave requests, the balance guard
    in signals.py). Shown by `manage.py run_leave_jobs --check`."""
    _, _, _, Ledger = _models()
    B = M('calendars', 'emp_leave_balance')
    qs = B.objects.all()
    led = Ledger.objects.all()
    if employee_ids is not None:
        qs, led = qs.filter(employee_id__in=employee_ids), led.filter(employee_id__in=employee_ids)
    sums = {(e, t): float(s or 0) for e, t, s in led.values('employee_id', 'leave_type_id').annotate(s=Sum('days')).values_list('employee_id', 'leave_type_id', 's')}
    out = []
    for e, t, b in qs.values_list('employee_id', 'leave_type_id', 'balance'):
        s = sums.pop((e, t), 0.0)
        if abs(float(b or 0) - s) >= 0.005:
            out.append((e, t, round(float(b or 0), 2), round(s, 2)))
    for (e, t), s in sums.items():     # ledger lines without a balance row
        if abs(s) >= 0.005:
            out.append((e, t, None, round(s, 2)))
    return out


def close_line(emp, line, ye, user_id=None, preview=False):
    """Close one leave type of one employee at the leave-year end `ye`. None when already closed."""
    _, _, _, Ledger = _models()
    if Ledger.objects.filter(employee_id=emp.pk, leave_type_id=line.leave_type_id, kind='carry_forward', date=ye).exists():
        return None
    bal = balance_on(emp.pk, line.leave_type_id, ye)   # v1.62.2 (item 58): the balance on the year-end date, not today's
    lim = cf_limit(line, emp, ye)
    carry = bal if lim is None or bal <= 0 else min(bal, lim)
    excess = round(max(bal - carry, 0), 2)
    action = line.excess_action if excess > 0 else 'none'
    # v1.62.4 (item 68): encash only what the policy lets be encashed (encashable, at most the encash limit a leave year less what
    # is encashed / waiting already); the rest lapses. Before, the whole excess went into one encashment request that the
    # approval refused (over the limit) – the days were neither encashed nor lapsed (27.5 days stuck above the limit).
    enc_days = excess if action == 'encash' else 0
    if action == 'encash':
        room = encash_room(emp, line)
        if room is not None:
            enc_days = round(min(excess, max(room, 0)), 2)
    over = round(excess - enc_days, 2) if action == 'encash' else 0
    row = {'employee_id': emp.pk, 'employee': f'{emp.emp_first_name} ({emp.emp_code})', 'leave_type_id': line.leave_type_id,
           'balance': bal, 'carry_forward': round(carry if action != 'keep' else bal, 2), 'excess': excess, 'action': action,
           'lapsed': excess if action == 'lapse' else over, 'encash': enc_days}
    if preview:
        return row
    post(emp.pk, line.leave_type_id, ye, 'carry_forward', 0, note=f'{row["carry_forward"]:g} days carried forward', user_id=user_id)
    if action == 'lapse':
        post(emp.pk, line.leave_type_id, ye, 'lapsed', -excess, note='Above the carry-forward limit', user_id=user_id, move_balance=True)
    elif action == 'encash':
        if enc_days > 0:
            enc = encashment_request(emp, line.leave_type_id, enc_days, remarks=f'Leave year end {ye:%d/%m/%Y}: above the carry-forward limit')
            row['encashment_id'] = enc.pk if enc else None
        if over > 0:   # v1.62.4 (item 68)
            post(emp.pk, line.leave_type_id, ye, 'lapsed', -over, user_id=user_id, move_balance=True,
                 note=f'Above the carry-forward limit and the encashment limit ({enc_days:g} of {excess:g} days encashed)')
    return row


def encash_room(emp, line):
    """v1.62.4 (item 68): days that may still be encashed this leave year by the policy line (None = no limit): 0 when the line
    is not encashable; else the encash limit less the encashments approved this leave year and those waiting – the same count as
    check_encashment, which approves or refuses the request."""
    if not line.encashable:
        return 0.0
    if not line.encash_max_per_year:
        return None
    LE = M('PayrollManagement', 'LeaveEncashment')
    y0, y1 = leave_year(line.policy, date.today())
    qs = LE.objects.filter(employee=emp, leave_type_id=line.leave_type_id)
    done = sum(float(x.encashment_days or 0) for x in qs.filter(status__in=('approved', 'processed'), approved_at__date__gte=y0, approved_at__date__lte=y1))
    done += sum(float(x.encashment_days or 0) for x in qs.filter(status__in=('draft', 'pending')))
    return round(float(line.encash_max_per_year) - done, 2)


def run_year_end(year_end=None, user_id=None, preview=False, policy_ids=None):
    """Leave-year end: days above the carry-forward limit are kept (flagged), sent for encashment or lapse, per policy line."""
    rows = []
    for emp in _active_employees():
        p = policy_for(emp)
        if p is None or (policy_ids is not None and p.pk not in policy_ids):
            continue
        ye = year_end or (leave_year(p, date.today())[0] - timedelta(days=1))
        for line in emp_lines(emp, p, p.lines.filter(accrual__in=('monthly', 'yearly'))):   # v1.62.1 branch copies
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
        errs.append(tr('Days to encash must be more than 0.'))
    bal = float(balance_row(emp.pk, lt_id).balance or 0)
    if days > bal + 1e-9:
        errs.append(tr('Only {0:g} days in the balance.', bal))
    else:   # v1.18.0: two open requests could each pass the balance check and encash the same days twice
        LE = M('PayrollManagement', 'LeaveEncashment')
        others = LE.objects.filter(employee=emp, leave_type_id=lt_id, status__in=('draft', 'pending'))
        if enc.pk:
            others = others.exclude(pk=enc.pk)
        open_days = sum(float(x.encashment_days or 0) for x in others)
        if open_days and days + open_days > bal + 1e-9:
            errs.append(tr('{0:g} days are already in another encashment request waiting for approval; only {1:g} days are left to encash.',
                           open_days, max(bal - open_days, 0)))
    line = line_for(emp, lt_id)
    if line is not None:
        if not line.encashable:
            errs.append(tr('The policy {0} does not allow encashing this leave type.', line.policy.name))
        if line.encash_max_per_year:
            LE = M('PayrollManagement', 'LeaveEncashment')
            y0, y1 = leave_year(line.policy, date.today())
            done = sum(float(x.encashment_days) for x in LE.objects.filter(employee=emp, leave_type_id=lt_id, status__in=('approved', 'processed'),
                                                                          approved_at__date__gte=y0, approved_at__date__lte=y1).exclude(pk=enc.pk))
            # v1.18.0: requests still waiting for approval count too (two open requests could each stay under the limit)
            waiting = LE.objects.filter(employee=emp, leave_type_id=lt_id, status__in=('draft', 'pending'))
            done += sum(float(x.encashment_days) for x in (waiting.exclude(pk=enc.pk) if enc.pk else waiting))
            if done + days > line.encash_max_per_year:
                errs.append(tr('At most {0:g} days can be encashed a leave year; {1:g} already encashed.', line.encash_max_per_year, done))
    return errs


# ------------------------------------------------------------------ v1.62.4 end of service: the leave balance on the last working day
def accrual_to(emp, lt_id, lwd):
    """v1.62.4 (item 75): (days, [notes]) of the monthly accrual not credited yet up to the last working day – the months after the
    last month credited, the month of the last day prorated to it (the accrual run's own calculation, preview only: nothing is
    written). Before, the end of service used today's balance: the part month to the last day (1.61 days) was missing."""
    p = policy_for(emp)
    if p is None or not lwd:
        return 0.0, []
    line = next((l for l in emp_lines(emp, p) if l.leave_type_id == lt_id), None)
    if line is None or line.accrual != 'monthly':
        return 0.0, []
    _, _, _, Ledger = _models()
    ids = [lt_id] + ([line._twin_of] if getattr(line, '_twin_of', None) else [])
    last = Ledger.objects.filter(employee_id=emp.pk, leave_type_id__in=ids, kind__in=('accrual', 'reset')).order_by('-date') \
        .values_list('date', flat=True).first()
    m = (last.replace(day=28) + timedelta(days=4)).replace(day=1) if last else lwd.replace(day=1)
    if emp.emp_joined_date and m < emp.emp_joined_date.replace(day=1):
        m = emp.emp_joined_date.replace(day=1)
    stop = lwd.replace(day=1)
    if (stop.year - m.year) * 12 + stop.month - m.month > 12:   # at most the last 13 months
        m = date(stop.year - 1, stop.month, 1)
    total, notes = 0.0, []
    while m <= stop:
        m_end = (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        res = run_accrual(on=m_end, preview=True)
        for r in res.get('lines', []):
            if r.get('employee_id') == emp.pk and r.get('leave_type_id') == lt_id and r.get('kind') == 'accrual':
                total += float(r.get('days') or 0)
                notes.append(f"{r.get('note') or m_end.strftime('%Y-%m')}: {float(r.get('days') or 0):g}")
        m = m_end + timedelta(days=1)
    return round(total, 2), notes


def balance_at_exit(emp, lt_id, lwd):
    """v1.62.4 (items 71, 75): the leave balance on the last working day, as the end of service pays (or recovers) it.
    = today's balance
      − accruals / lapses / resets dated after the last day (nothing is earned or lapses after the employment ends)
      + the approved leave days after the last day that were taken from the balance (leave starting after it, and the part
        after it of leave crossing it – before, only leave that started after the last day was given back)
      + the accrual not credited yet up to the last day (accrual_to).
    The result may be negative (leave taken in advance): the settlement recovers it."""
    _, _, _, Ledger = _models()
    now = float(balance_row(emp.pk, lt_id).balance or 0)
    later = float(Ledger.objects.filter(employee_id=emp.pk, leave_type_id=lt_id, date__gt=lwd, kind__in=('accrual', 'reset', 'lapsed'))
                  .aggregate(s=Sum('days'))['s'] or 0)
    LR = M('calendars', 'employee_leave_request')
    line = line_for(emp, lt_id)
    back, back_rows = 0.0, []
    for r in LR.objects.filter(employee=emp, leave_type_id=lt_id, status='approved', end_date__gt=lwd).select_related('leave_type', 'employee'):
        unit = 0.5 if r.dis_half_day else 1.0
        taken = approved_and_rest(r, _req_days(r, line))[0]
        n = min(sum(unit for d in taken if d > lwd), float(r.approved_days or r.number_of_days or 0))
        if n > 0:
            back += n
            back_rows.append((r.document_number, round(n, 2)))
    due, notes = accrual_to(emp, lt_id, lwd)
    u6, u6_note = _lr().under_6m_days(emp, line, lwd)   # v1.65.0 (item 90): company setting, to be confirmed (default nothing)
    return {'today': round(now, 2), 'after_last_day': round(-later, 2), 'given_back': round(back, 2), 'given_back_rows': back_rows,
            'accrual_due': due, 'accrual_notes': notes, 'under_6m': u6, 'under_6m_note': u6_note, 'balance': round(now - later + back + due + u6, 2)}
