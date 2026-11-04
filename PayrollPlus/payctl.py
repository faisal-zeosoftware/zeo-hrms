# v1.62.11 (items 228, 231): new module – payroll controls (group payctl)
"""v1.62.11 (items 228, 231) – payroll controls.

Hold / release salary (228)
  A month's salary of an employee is held in full, by an amount or by a percentage of the net pay (SalaryHold). The payslip keeps
  the salary earned (gross, deductions and net pay unchanged – the journal still books the cost and "Salaries payable"); the held
  part is simply not paid that month:
      held_amount(payslip)    the part of this payslip's net pay that is held (0 when nothing is held)
      payable_amount(payslip) what is paid now = net pay − held (never below 0) – the WPS file (SIF) and the bank list use it;
                              a payslip with payable 0 is left out of the file.
  Released, the held amount is paid in the next payroll run (or the run of a chosen later month) as the addition line
  "Held salary released" (component HOLDREL, made once per branch); the journal books that line Dr Salaries payable (the liability
  of the held month), not as a second salary cost. A payslip deleted (run recalculated / deleted) gives its holds and releases back.

Period locking (231)
  A closed payroll period (all runs of the month processed / approved / paid) can be locked, for every branch or one branch
  (PeriodLock, log PeriodLockEvent). While it is locked nothing may change it: no payroll run / payslip / payslip line of the
  month is made, changed or deleted, no attendance punch or overtime entry on its days, no leave on its days (LeavePolicy/
  paid_period.check – the "Add to next salary" choice is not offered in a locked period), no salary change effective inside it.
  HR with the right "Can unlock a locked payroll period" unlocks it with a reason; each step is logged.

Revise salary with approval (228)
  New amounts per component from an effective date (SalaryRevisionRequest). The approval is the general request workflow of the
  request type "Salary revision" (made by ZEO; levels in Requests › Approval levels – the 1.62.10 editor): a GeneralRequest is
  recorded and goes through its levels like any request; when it is approved the amounts are saved with the effective date
  (PayrollManagement/salary_dates.py applies them from that day) and, for an effective date in the past, the arrears of the
  payslips already made are written (PayrollPlus/cycles.calculate) for the next payroll. Rejected → nothing changes.

Automatic pay-date shift (231)
  Company setting: a payment date on a weekend or public holiday of the run's branch moves to the working day before (the branch's
  weekend calendar and holiday calendar – calendars/workdays.py WeekendRules / HolidayRules).

Loan limit by gratuity (231)
  Company setting (off by default) with an optional percentage: a new loan (or a larger amount) is refused when it – together with
  what the employee still owes on other open loans – is more than the gratuity earned to date (Reports › Gratuity calculation,
  DashboardManagement.reports.r_gratuity, the same figure as the month-end provision) × the percentage.

Re-hire a leaver (228)
  A leaver comes back: the old service period is closed (joining – last working day, its final settlement stays), a new one starts
  on the re-hire date: the joining date becomes the re-hire date (gratuity and leave count from it – 1.62.4 leave accrual for
  re-hired employees keeps working), or – when the company chooses continuity – the gratuity service counts from the first joining
  date (GoLiveEmployee.gratuity_from, read by the gratuity report and the final settlement).

Salary revision history import (231)
  An Excel / CSV file (employee code, component code, effective date, old amount, new amount, remarks) → SalaryRevisionHistory
  revisions with their effective dates; a dry run lists every row with its errors, apply writes the rows without errors.
"""
import calendar
import io
import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from types import SimpleNamespace

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
Q2 = Decimal('0.01')
CLOSED = ('processed', 'approved', 'paid')
RELEASE_CODE, RELEASE_NAME = 'HOLDREL', 'Held salary released'
OPTION_KEY = 'v16211_payctl'
REQUEST_TYPE = 'Salary revision'
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


class PeriodLocked(ValidationError):
    """v1.62.11 (item 231): a change inside a locked payroll period (HTTP 400 through zeo/exceptions.py)."""
    def __init__(self, message):
        super().__init__(message, code='period_locked')


def q2(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0)).quantize(Q2, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return ZERO


def first_of(d):
    return date(d.year, d.month, 1)


def month_end(d):
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def month_label(d):
    return f'{tr(MONTHS[d.month - 1])} {d.year}'


def run_month(run):
    return date(int(run.year), int(run.month), 1)


def _user_name(user):
    if user is None:
        return ''
    try:
        return (user.get_full_name() or user.username or '')[:150]
    except Exception:
        return str(getattr(user, 'username', '') or '')[:150]


def _note(obj, action, user=None, note=''):
    obj.history = list(obj.history or []) + [{'action': action, 'by': _user_name(user), 'by_id': getattr(user, 'pk', None),
                                              'at': timezone.now().isoformat(timespec='seconds'), 'note': str(note or '')[:255]}]


def _emp(emp_id):
    return M('EmpManagement', 'emp_master').objects.filter(pk=emp_id).select_related('emp_branch_id').first()


def emp_label(e):
    if e is None:
        return ''
    n = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
    return f'{n} ({e.emp_code})' if n else str(e.emp_code)


# ------------------------------------------------------------------ company settings (CompanyOption, no new column)
DEFAULTS = {'pay_date_shift': False, 'loan_gratuity_limit': False, 'loan_gratuity_pct': None, 'loan_include_open': True}


def settings():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=OPTION_KEY).first()
        v = dict(o.value or {}) if o is not None else {}
    except Exception:
        v = {}
    out = dict(DEFAULTS)
    out.update({k: v[k] for k in DEFAULTS if k in v})
    return out


def save_settings(data, user=None):
    cur = settings()
    b = lambda x: str(x).lower() in ('1', 'true', 'yes', 'on')
    if 'pay_date_shift' in data:
        cur['pay_date_shift'] = b(data['pay_date_shift'])
    if 'loan_gratuity_limit' in data:
        cur['loan_gratuity_limit'] = b(data['loan_gratuity_limit'])
    if 'loan_include_open' in data:
        cur['loan_include_open'] = b(data['loan_include_open'])
    if 'loan_gratuity_pct' in data:
        p = data.get('loan_gratuity_pct')
        if p in (None, '', 'null'):
            cur['loan_gratuity_pct'] = None
        else:
            try:
                pv = Decimal(str(p))
            except (InvalidOperation, ValueError):
                raise Refused(tr('Enter a percentage from 1 to 100.'), problems={'loan_gratuity_pct': tr('Enter a percentage from 1 to 100.')})
            if not Decimal('0') < pv <= Decimal('100'):
                raise Refused(tr('Enter a percentage from 1 to 100.'), problems={'loan_gratuity_pct': tr('Enter a percentage from 1 to 100.')})
            cur['loan_gratuity_pct'] = str(pv.normalize())
    cur['updated_by'] = _user_name(user)
    cur['updated_at'] = timezone.now().isoformat(timespec='seconds')
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=OPTION_KEY, defaults={'value': cur})
    return settings()


# ================================================================== hold / release (item 228)
def _run_kind(run):
    try:
        from .cycles import _run_info
        return _run_info(run)
    except Exception:
        return 'month', None


def _release_component(branch_id):
    SC = M('PayrollManagement', 'SalaryComponent')
    c = SC.objects.filter(code=RELEASE_CODE, branch_id=branch_id).first() or SC.objects.filter(code=RELEASE_CODE, branch__isnull=True).first()
    if c is None:
        name, n = RELEASE_NAME, 1
        while SC.objects.filter(name=name, branch_id=branch_id).exists():
            n += 1
            name = f'{RELEASE_NAME} {n}'
        c = SC.objects.create(name=name, code=RELEASE_CODE, branch_id=branch_id, component_type='addition', payroll_category='other_allowance',
                              component_value_type='variable', formula='', show_in_payslip=True,
                              description='v1.62.11: salary of an earlier month that was held and is now released (made by ZEO)')
    return c


def release_component_ids():
    return set(M('PayrollManagement', 'SalaryComponent').objects.filter(code=RELEASE_CODE).values_list('pk', flat=True))


def _compute_hold(h, net):
    net = max(q2(net), ZERO)
    if h.kind == 'full':
        return net
    if h.kind == 'percent':
        return min(net, (net * q2(h.value) / Decimal('100')).quantize(Q2, rounding=ROUND_HALF_UP))
    return min(net, q2(h.value))


def held_amount(payslip):
    """v1.62.11 (item 228) – the part of the payslip's net pay that is held (not paid with this payslip). For the WPS file / bank list."""
    from .models import SalaryHold
    pid = getattr(payslip, 'pk', payslip)
    return q2(SalaryHold.objects.filter(payslip_id=pid, status__in=('held', 'to_release', 'released')).aggregate(t=Sum('held_amount'))['t'])


def payable_amount(payslip):
    """v1.62.11 (item 228) – what is paid with this payslip now: net pay − held_amount, never below 0. The WPS file (SIF) and the bank
    list pay this amount and leave out a payslip whose payable amount is 0 because it is held."""
    P = M('PayrollManagement', 'Payslip')
    p = payslip if hasattr(payslip, 'net_salary') else P.objects.filter(pk=payslip).first()
    if p is None:
        return ZERO
    return max(q2(p.net_salary) - held_amount(p), ZERO)


def held_map(payslip_ids):
    """{payslip id: held amount} for many payslips at once (one query)."""
    from .models import SalaryHold
    out = {}
    for pid, amt in SalaryHold.objects.filter(payslip_id__in=list(payslip_ids), status__in=('held', 'to_release', 'released')).values_list('payslip_id', 'held_amount'):
        out[pid] = q2(out.get(pid, ZERO) + q2(amt))
    return out


def released_on(payslip):
    """The held salary of earlier months paid on this payslip (line "Held salary released")."""
    from .models import SalaryHold
    return q2(SalaryHold.objects.filter(release_payslip_id=getattr(payslip, 'pk', payslip)).aggregate(t=Sum('released_amount'))['t'])


def on_payslip(run, payslip, employee, skip_events=False):
    """Called by _process_payroll after the payslip totals: releases due are put on the payslip (totals again), the month's holds are
    worked out on the net pay. Returns {'released': x, 'held': y}."""
    from .models import SalaryHold
    kind, _cr = _run_kind(run)
    if kind == 'offcycle' or skip_events:
        return {'released': ZERO, 'held': ZERO}
    qs = SalaryHold.objects.filter(employee_id=employee.pk).exclude(status='cancelled')
    if not qs.exists():
        return {'released': ZERO, 'held': ZERO}
    m = run_month(run)
    released = ZERO
    due = [h for h in qs.filter(status='to_release', release_payslip_id__isnull=True, month__lt=m) if not h.release_from or h.release_from <= m]
    if due:
        PC = M('PayrollManagement', 'PayslipComponent')
        comp = _release_component(employee.emp_branch_id_id)
        total = q2(sum((q2(h.held_amount) for h in due), ZERO))
        if total > 0:
            pc, made = PC.objects.get_or_create(payslip=payslip, component=comp, defaults={'amount': total})
            if not made:
                pc.amount = q2(pc.amount) + total
                pc.save(update_fields=['amount'])
        for h in due:
            h.release_payslip_id, h.released_amount, h.status = payslip.pk, q2(h.held_amount), 'released'
            _note(h, 'released', None, tr('Paid in the {0} payroll', month_label(m)))
            h.save(update_fields=['release_payslip_id', 'released_amount', 'status', 'history', 'updated_at'])
        released = total
        from PayrollManagement.signals import calculate_payslip_totals
        calculate_payslip_totals(payslip)
    held = ZERO
    base = q2(payslip.net_salary) - released
    for h in qs.filter(month=m, payslip_id__isnull=True).order_by('pk'):
        if h.status == 'pending':
            amt = min(_compute_hold(h, base), max(base, ZERO))
            h.held_amount, h.status, h.payslip_id = amt, 'held', payslip.pk
            _note(h, 'held', None, tr('{0} held on the {1} payslip', f'{amt:,.2f}', month_label(m)))
        else:   # recalculated after the release was already set / paid: the same amount stays held
            h.payslip_id = payslip.pk
            h.held_amount = min(q2(h.held_amount), max(base, ZERO)) if h.status != 'released' else q2(h.held_amount)
        h.save(update_fields=['held_amount', 'status', 'payslip_id', 'history', 'updated_at'])
        base -= q2(h.held_amount)
        held += q2(h.held_amount)
    return {'released': released, 'held': held}


def payslip_deleted(payslip):
    """A deleted payslip (run deleted / recalculated) held and released nothing: its holds wait for the next calculation of the month,
    its releases are paid again by the next run."""
    from .models import SalaryHold
    n = 0
    for h in SalaryHold.objects.filter(payslip_id=payslip.pk):
        h.payslip_id = None
        if h.status == 'held':
            h.status, h.held_amount = 'pending', ZERO
        h.save(update_fields=['payslip_id', 'status', 'held_amount', 'updated_at'])
        n += 1
    for h in SalaryHold.objects.filter(release_payslip_id=payslip.pk):
        h.release_payslip_id, h.released_amount = None, ZERO
        if h.status == 'released':
            h.status = 'to_release'
        h.save(update_fields=['release_payslip_id', 'released_amount', 'status', 'updated_at'])
        n += 1
    return n


def _month_payslip(emp_id, m):
    """The employee's payslip of the month in a regular run (not off-cycle), newest first."""
    P = M('PayrollManagement', 'Payslip')
    for p in P.objects.filter(employee_id=emp_id, payroll_run__year=m.year, payroll_run__month=m.month).exclude(status__iexact='rejected') \
            .select_related('payroll_run').order_by('-pk'):
        if _run_kind(p.payroll_run)[0] != 'offcycle':
            return p
    return None


def hold_row(h, emps=None):
    e = (emps or {}).get(h.employee_id) or _emp(h.employee_id)
    P = M('PayrollManagement', 'Payslip')
    rel = P.objects.filter(pk=h.release_payslip_id).select_related('payroll_run').first() if h.release_payslip_id else None
    return {'id': h.pk, 'employee_id': h.employee_id, 'employee': emp_label(e), 'emp_code': getattr(e, 'emp_code', ''),
            'branch_id': getattr(e, 'emp_branch_id_id', None),
            'month': h.month.isoformat(), 'month_label': month_label(h.month), 'kind': h.kind, 'kind_label': tr(dict(h.KINDS).get(h.kind, h.kind)),
            'value': str(h.value) if h.value is not None else None, 'reason': h.reason, 'status': h.status,
            'status_label': tr(dict(h.STATUS).get(h.status, h.status)), 'held_amount': str(q2(h.held_amount)), 'payslip_id': h.payslip_id,
            'release_from': h.release_from.isoformat() if h.release_from else None,
            'release_label': month_label(rel.payroll_run and run_month(rel.payroll_run)) if rel else (month_label(h.release_from) if h.release_from else ''),
            'released_amount': str(q2(h.released_amount)), 'release_payslip_id': h.release_payslip_id, 'history': h.history or [],
            'created_at': h.created_at.isoformat() if h.created_at else None}


@transaction.atomic
def create_hold(data, user=None):
    from .models import SalaryHold
    problems = {}
    try:
        emp_id = int(data.get('employee'))
    except (TypeError, ValueError):
        emp_id = None
    e = _emp(emp_id) if emp_id else None
    if e is None:
        problems['employee'] = tr('Choose the employee.')
    m = _parse_month(data.get('month'))
    if m is None:
        problems['month'] = tr('Choose the month.')
    kind = str(data.get('kind') or 'full')
    if kind not in ('full', 'amount', 'percent'):
        problems['kind'] = tr('Choose what is held.')
    value = None
    if kind in ('amount', 'percent'):
        value = q2(data.get('value'))
        if value <= 0 or (kind == 'percent' and value > 100):
            problems['value'] = tr('Enter a percentage from 1 to 100.') if kind == 'percent' else tr('Enter an amount more than 0.')
    reason = str(data.get('reason') or '').strip()[:255]
    if not reason:
        problems['reason'] = tr('Enter the reason.')
    if problems:
        raise Refused(tr('Check the marked fields.'), problems=problems)
    lk = locked_month(m, e.emp_branch_id_id)
    if lk is not None:
        raise Refused(lock_text(lk), problems={'month': lock_text(lk)})
    if SalaryHold.objects.filter(employee_id=e.pk, month=m).exclude(status='cancelled').exists():
        raise Refused(tr('This salary month is already held for the employee – change or cancel that hold.'), problems={'month': tr('Already held.')})
    ps = _month_payslip(e.pk, m)
    if ps is not None and str(ps.payroll_run.status).lower() == 'paid':
        raise Refused(tr('The {0} salary is already paid – it cannot be held.', month_label(m)), problems={'month': tr('Already paid.')})
    h = SalaryHold(employee_id=e.pk, month=m, kind=kind, value=value, reason=reason, created_by_id=getattr(user, 'pk', None))
    _note(h, 'created', user, reason)
    if ps is not None and str(ps.payroll_run.status).lower() in CLOSED:   # the month is calculated already: held now
        base = q2(ps.net_salary) - released_on(ps) - held_amount(ps)
        h.held_amount, h.status, h.payslip_id = min(_compute_hold(h, base), max(base, ZERO)), 'held', ps.pk
        _note(h, 'held', user, tr('{0} held on the {1} payslip', f'{h.held_amount:,.2f}', month_label(m)))
    h.save()
    return h


@transaction.atomic
def release_hold(h, data, user=None):
    if h.status not in ('held', 'to_release'):
        raise Refused(tr('Only a held salary can be released.'))
    rf = _parse_month(data.get('release_from')) if data.get('release_from') else None
    if rf is not None and rf <= h.month:
        raise Refused(tr('Release it in a later month than the held month.'), problems={'release_from': tr('Choose a later month.')})
    if rf is not None:
        e = _emp(h.employee_id)
        lk = locked_month(rf, getattr(e, 'emp_branch_id_id', None))
        if lk is not None:
            raise Refused(lock_text(lk), problems={'release_from': lock_text(lk)})
    h.status, h.release_from = 'to_release', rf
    _note(h, 'release', user, (tr('Release in the {0} payroll', month_label(rf)) if rf else tr('Release in the next payroll run')) +
          (f' – {data.get("note")}' if data.get('note') else ''))
    h.save()
    return h


@transaction.atomic
def cancel_hold(h, user=None, note=''):
    if h.status == 'released' or h.release_payslip_id:
        raise Refused(tr('The held salary is already paid – it cannot be cancelled.'))
    if h.status in ('held', 'to_release') and h.payslip_id:
        P = M('PayrollManagement', 'Payslip')
        ps = P.objects.filter(pk=h.payslip_id).select_related('payroll_run').first()
        if ps is not None and str(ps.payroll_run.status).lower() == 'paid':
            raise Refused(tr('The {0} payroll is paid without the held part – release it in a later run instead.', month_label(h.month)))
        e = _emp(h.employee_id)
        lk = locked_month(h.month, getattr(e, 'emp_branch_id_id', None))
        if lk is not None:
            raise Refused(lock_text(lk))
    h.status, h.payslip_id, h.held_amount = 'cancelled', None, ZERO
    _note(h, 'cancelled', user, note)
    h.save()
    return h


def _parse_month(v):
    s = str(v or '').strip()
    if not s:
        return None
    try:
        if len(s) == 7:
            y, mm = s.split('-')
            return date(int(y), int(mm), 1)
        return first_of(date.fromisoformat(s[:10]))
    except (ValueError, TypeError):
        return None


def _parse_day(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


# ================================================================== period locking (item 231)
_cache = {}
_local = threading.local()


def forget_locks():
    _cache.pop(getattr(connection, 'schema_name', ''), None)


def active_locks():
    """[(month, branch_id, start, end, id)] of the locked periods – read at most every 5 seconds per process."""
    key = getattr(connection, 'schema_name', '')
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 5:
        return hit[1]
    try:
        from .models import PeriodLock
        val = list(PeriodLock.objects.filter(status='locked').values_list('month', 'branch_id', 'start', 'end', 'id'))
    except Exception:
        val = []
    if len(_cache) > 300:
        _cache.clear()
    _cache[key] = (time.time(), val)
    return val


def _skip():
    return getattr(_local, 'skip', False)


def locked_month(m, branch_id=None):
    """The lock of the salary month for the branch (a lock of every branch counts), or None."""
    if _skip():
        return None
    m = first_of(m)
    for mo, br, a, b, pk in active_locks():
        if mo == m and (br is None or br == branch_id):
            return SimpleNamespace(id=pk, month=mo, branch_id=br, start=a, end=b)
    return None


def locked_company_month(m):
    m = first_of(m)
    for mo, br, a, b, pk in active_locks():
        if mo == m and br is None:
            return SimpleNamespace(id=pk, month=mo, branch_id=br, start=a, end=b)
    return None


def locked_day(d, branch_id=None):
    if _skip() or d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    for mo, br, a, b, pk in active_locks():
        if a <= d <= b and (br is None or br == branch_id):
            return SimpleNamespace(id=pk, month=mo, branch_id=br, start=a, end=b)
    return None


def locked_days(days, branch_id=None):
    for d in days:
        lk = locked_day(d, branch_id)
        if lk is not None:
            return lk, d
    return None, None


def lock_text(lk):
    return tr('The {0} payroll period is locked ({1} – {2}). Ask HR to unlock it first.', month_label(lk.month),
              f'{lk.start:%d/%m/%Y}', f'{lk.end:%d/%m/%Y}')


def _branch_name(bid):
    if bid is None:
        return tr('All branches')
    b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=bid).first()
    return b.branch_name if b else str(bid)


def month_runs(m, branch_id=None):
    """The regular (not off-cycle) runs of the salary month – of the branch when one is given."""
    PR = M('PayrollManagement', 'PayrollRun')
    qs = PR.objects.filter(year=m.year, month=m.month)
    if branch_id:
        PRB = M('PayrollManagement', 'PayrollRunBranch')
        ids = set(PRB.objects.filter(branch_id=branch_id).values_list('payroll_run_id', flat=True))
        ids |= set(M('PayrollManagement', 'Payslip').objects.filter(employee__emp_branch_id=branch_id, payroll_run__year=m.year,
                                                                      payroll_run__month=m.month).values_list('payroll_run_id', flat=True))
        qs = qs.filter(pk__in=ids)
    return [r for r in qs.order_by('pk') if _run_kind(r)[0] != 'offcycle']


def lock_row(lk):
    from .models import PeriodLockEvent
    U = M('UserManagement', 'CustomUser')
    by = U.objects.filter(pk=lk.locked_by_id).first() if lk.locked_by_id else None
    return {'id': lk.pk, 'month': lk.month.isoformat(), 'month_label': month_label(lk.month), 'branch_id': lk.branch_id,
            'branch': _branch_name(lk.branch_id), 'start': lk.start.isoformat(), 'end': lk.end.isoformat(), 'status': lk.status,
            'status_label': tr(dict(lk.STATUS).get(lk.status, lk.status)), 'note': lk.note, 'locked_by': _user_name(by),
            'locked_at': lk.locked_at.isoformat() if lk.locked_at else None,
            'events': [{'action': x.action, 'action_label': tr({'locked': 'Locked', 'unlocked': 'Unlocked', 'relocked': 'Locked again'}.get(x.action, x.action)),
                        'reason': x.reason, 'by': x.user_name, 'at': x.at.isoformat()} for x in PeriodLockEvent.objects.filter(lock=lk)]}


@transaction.atomic
def lock_period(data, user=None):
    from .models import PeriodLock, PeriodLockEvent
    m = _parse_month(data.get('month'))
    if m is None:
        raise Refused(tr('Choose the month.'), problems={'month': tr('Choose the month.')})
    bid = data.get('branch') or None
    try:
        bid = int(bid) if bid not in (None, '', 'null', 0, '0') else None
    except (TypeError, ValueError):
        raise Refused(tr('Choose the branch.'), problems={'branch': tr('Choose the branch.')})
    runs = month_runs(m, bid)
    if not runs:
        raise Refused(tr('There is no payroll run of {0} to lock.', month_label(m)), problems={'month': tr('No payroll run of this month.')})
    open_runs = [r for r in runs if str(r.status).lower() not in CLOSED]
    if open_runs:
        raise Refused(tr('The {0} payroll is not closed yet – process the run first ({1}).', month_label(m),
                         ', '.join(r.name or f'#{r.pk}' for r in open_runs)), problems={'month': tr('A run of this month is still pending.')})
    starts = [r.attendance_start_date for r in runs if r.attendance_start_date] or [m]
    ends = [r.attendance_end_date for r in runs if r.attendance_end_date] or [month_end(m)]
    a, b = min(starts), max(ends)
    lk = PeriodLock.objects.select_for_update().filter(month=m, branch_id=bid).first()
    if lk is not None and lk.status == 'locked':
        raise Refused(tr('This period is already locked.'))
    note = str(data.get('note') or '').strip()[:255]
    if lk is None:
        lk = PeriodLock.objects.create(month=m, branch_id=bid, start=a, end=b, status='locked', locked_by_id=getattr(user, 'pk', None), note=note)
        act = 'locked'
    else:
        lk.start, lk.end, lk.status, lk.locked_by_id, lk.note = a, b, 'locked', getattr(user, 'pk', None), note or lk.note
        lk.save()
        act = 'relocked'
    PeriodLockEvent.objects.create(lock=lk, action=act, reason=note, user_id=getattr(user, 'pk', None), user_name=_user_name(user))
    forget_locks()
    return lk


@transaction.atomic
def unlock_period(lk, reason, user=None):
    from .models import PeriodLockEvent
    reason = str(reason or '').strip()[:255]
    if not reason:
        raise Refused(tr('Enter the reason for unlocking.'), problems={'reason': tr('Enter the reason for unlocking.')})
    if lk.status != 'locked':
        raise Refused(tr('This period is not locked.'))
    lk.status = 'unlocked'
    lk.save(update_fields=['status', 'updated_at'])
    PeriodLockEvent.objects.create(lock=lk, action='unlocked', reason=reason, user_id=getattr(user, 'pk', None), user_name=_user_name(user))
    forget_locks()
    return lk


def guard_payslip(payslip, action='change'):
    """Refuse a payslip change in a locked period (PeriodLocked)."""
    if not active_locks():
        return
    try:
        run = payslip.payroll_run
        emp_branch = M('EmpManagement', 'emp_master').objects.filter(pk=payslip.employee_id).values_list('emp_branch_id', flat=True).first()
    except Exception:
        return
    if _run_kind(run)[0] == 'offcycle':
        # an off-cycle run pays on its own date: locked when that date is in a locked period
        lk = locked_day(run.payment_date, emp_branch) if run.payment_date else None
    else:
        lk = locked_month(run_month(run), emp_branch)
    if lk is not None:
        raise PeriodLocked(lock_text(lk))


def guard_day(employee_id, d):
    if not active_locks() or d is None:
        return
    br = M('EmpManagement', 'emp_master').objects.filter(pk=employee_id).values_list('emp_branch_id', flat=True).first()
    lk = locked_day(d, br)
    if lk is not None:
        raise PeriodLocked(lock_text(lk))


def guard_leave(req):
    """Called by LeavePolicy/paid_period.check: any day of the request (and of its earlier dates) in a locked period → refused."""
    if not active_locks() or not getattr(req, 'start_date', None):
        return
    LR = type(req)
    prev = LR.objects.filter(pk=req.pk).values('status', 'start_date', 'end_date').first() if req.pk else None
    if prev is not None and (prev['status'], prev['start_date'], prev['end_date']) == (req.status, req.start_date, req.end_date):
        return   # nothing about the days changes (a note, an attachment …)
    days = []
    for a, b in ((req.start_date, req.end_date or req.start_date), ((prev or {}).get('start_date'), (prev or {}).get('end_date'))):
        if a and b:
            d = a
            while d <= b:
                days.append(d)
                d += timedelta(days=1)
    lk, d = locked_days(days, getattr(req.employee, 'emp_branch_id_id', None))
    if lk is not None:
        raise PeriodLocked(lock_text(lk))


def guard_salary_change(instance):
    """A salary change effective inside a locked period is refused (an undated correction changes no closed payslip)."""
    if not active_locks():
        return
    eff = getattr(instance, '_effective_period', None)
    day = None
    if eff:
        from PayrollManagement.salary_dates import parse_effective
        day = parse_effective(eff)
    elif instance.pk and instance.valid_from:
        ESS = type(instance)
        old = ESS.objects.filter(pk=instance.pk).values('valid_from', 'amount').first()
        if old and old['valid_from'] != instance.valid_from:
            day = instance.valid_from
    if day is None:
        return
    br = M('EmpManagement', 'emp_master').objects.filter(pk=instance.employee_id).values_list('emp_branch_id', flat=True).first()
    lk = locked_day(day, br) or locked_month(day, br)
    if lk is not None:
        raise PeriodLocked(lock_text(lk))


# ================================================================== automatic pay-date shift (item 231)
def _branch_off(branch_id, d, wr, hr):
    """(off, why) – the branch's weekend calendar and holiday calendar (calendars/workdays.py) on the day."""
    from calendars.workdays import model_days
    fake = SimpleNamespace(pk=None, emp_branch_id_id=branch_id, emp_dept_id_id=None, emp_ctgry_id_id=None, emp_desgntn_id_id=None)
    if d in hr.dates(fake):
        return True, 'holiday'
    w, _lvl, _a = wr.pick(fake, d)
    if d.strftime('%A') in model_days(w, d):
        return True, 'weekend'
    return False, ''


def shifted_pay_date(d, branch_ids):
    """(date, [(day, why)]) – the working day on or before d for every branch (at most 14 days back)."""
    if d is None:
        return d, []
    from calendars.workdays import HolidayRules, WeekendRules
    wr, hr = WeekendRules(), HolidayRules()
    brs = [b for b in branch_ids if b] or [None]
    moved, x = [], d
    for _ in range(15):
        why = ''
        for b in brs:
            off, w = _branch_off(b, x, wr, hr) if b is not None else (False, '')
            if off:
                why = w
                break
        if not why:
            return x, moved
        moved.append((x, why))
        x -= timedelta(days=1)
    return d, []


def run_branch_ids(run):
    PRB = M('PayrollManagement', 'PayrollRunBranch')
    ids = list(PRB.objects.filter(payroll_run_id=run.pk).values_list('branch_id', flat=True)) if run.pk else []
    return ids


def apply_pay_date_shift(run, save=True):
    """When the company setting is on: the run's payment date moved to the working day before. Returns the note or ''."""
    if not run.payment_date or not settings().get('pay_date_shift'):
        return ''
    brs = run_branch_ids(run)
    if not brs:
        return ''
    new, moved = shifted_pay_date(run.payment_date, brs)
    if new == run.payment_date:
        return ''
    why = {'weekend': tr('weekend'), 'holiday': tr('public holiday')}
    note = tr('Payment date moved from {0} ({1}) to {2}, the working day before.', f'{run.payment_date:%d/%m/%Y}',
              why.get(moved[0][1], moved[0][1]), f'{new:%d/%m/%Y}')
    run.payment_date = new
    if save:
        type(run).objects.filter(pk=run.pk).update(payment_date=new)
    _shift_log(run.pk, note)
    return note


def _shift_log(run_id, note):
    try:
        CO = M('ZeoApps', 'CompanyOption')
        o = CO.objects.filter(key=OPTION_KEY + '_shifts').first()
        v = dict(o.value or {}) if o else {}
        v[str(run_id)] = {'note': note, 'at': timezone.now().isoformat(timespec='seconds')}
        CO.objects.update_or_create(key=OPTION_KEY + '_shifts', defaults={'value': v})
    except Exception:
        log.exception('pay date shift note not kept')


def shift_note(run_id):
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=OPTION_KEY + '_shifts').first()
        return ((o.value or {}).get(str(run_id)) or {}).get('note', '') if o else ''
    except Exception:
        return ''


# ================================================================== loan limit by gratuity (item 231)
def gratuity_earned(emp, as_of=None):
    """The gratuity earned to date – the same figure as Reports › Gratuity (DashboardManagement.reports.r_gratuity) and the month-end
    provision. Returns (amount, row)."""
    from DashboardManagement import reports as R
    as_of = as_of or timezone.localdate()

    class _Run:
        pass
    r = _Run()
    r.today, r.date_from, r.date_to, r.branches = as_of, as_of, as_of, None
    r.employees = M('EmpManagement', 'emp_master').objects.filter(pk=emp.pk).select_related(
        'emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id', 'emp_nationality', 'emp_reporting_manager')
    try:
        _cols, rows = R.r_gratuity(r)
    except Exception:
        log.exception('gratuity of %s not worked out', emp.pk)
        return ZERO, {}
    row = rows[0] if rows else {}
    return q2(row.get('amount')), row


LOAN_OPEN = ('Pending', 'Approved', 'Disbursed', 'Paused', 'In Progress')


def loan_limit(emp, exclude_loan_id=None):
    """{on, gratuity, pct, limit, owed, available} for the employee."""
    st = settings()
    if not st.get('loan_gratuity_limit'):
        return {'on': False}
    g, row = gratuity_earned(emp)
    pct = Decimal(str(st.get('loan_gratuity_pct') or '100'))
    limit = (g * pct / Decimal('100')).quantize(Q2, rounding=ROUND_HALF_UP)
    owed = ZERO
    if st.get('loan_include_open', True):
        LA = M('PayrollManagement', 'LoanApplication')
        for l in LA.objects.filter(employee_id=emp.pk, status__in=LOAN_OPEN).exclude(pk=exclude_loan_id):
            owed += q2(l.remaining_balance if l.remaining_balance is not None else l.amount_requested)
    return {'on': True, 'gratuity': g, 'pct': pct, 'limit': limit, 'owed': owed, 'available': max(limit - owed, ZERO),
            'years': row.get('years'), 'basis': row.get('basis', '')}


def check_loan(loan):
    """Called before a loan is saved: a new loan or a larger amount above the limit is refused (ValidationError)."""
    st = settings()
    if not st.get('loan_gratuity_limit'):
        return
    if loan.status not in LOAN_OPEN:
        return
    LA = type(loan)
    old = LA.objects.filter(pk=loan.pk).values('amount_requested', 'status').first() if loan.pk else None
    amount = q2(loan.amount_requested)
    if old is not None and q2(old['amount_requested']) >= amount and old['status'] in LOAN_OPEN:
        return   # not larger – balance updates by payroll, approvals, pauses …
    emp = M('EmpManagement', 'emp_master').objects.filter(pk=loan.employee_id).first()
    if emp is None:
        return
    lim = loan_limit(emp, exclude_loan_id=loan.pk)
    if amount > lim['available']:
        raise ValidationError({'amount_requested': [tr(
            'The loan is more than the limit: gratuity earned to date {0} × {1} % = {2}{3} – at most {4} can be lent.',
            f"{lim['gratuity']:,.2f}", '{0:f}'.format(lim['pct'].normalize()), f"{lim['limit']:,.2f}",
            (tr(', less {0} still owed on other loans', f"{lim['owed']:,.2f}") if lim['owed'] else ''), f"{lim['available']:,.2f}")]})


# ================================================================== revise salary with approval (item 228)
def request_type(user=None):
    RT = M('EmpManagement', 'RequestType')
    rt = RT.objects.filter(name=REQUEST_TYPE).first()
    if rt is None:
        U = M('UserManagement', 'CustomUser')
        owner = user if getattr(user, 'pk', None) else U.objects.filter(is_superuser=True).order_by('pk').first()
        rt = RT.objects.create(name=REQUEST_TYPE, description='v1.62.11: salary revisions (Payroll › Revise salary) – set the approval levels here',
                               created_by=owner)
    return rt


def _doc_number(emp, rev):
    GR = M('EmpManagement', 'GeneralRequest')
    DN = M('OrganisationManager', 'DocumentNumbering')
    br = emp.emp_branch_id or getattr(emp, 'work_location', None)
    cfg = DN.objects.filter(branch_id=br, type='general_request').first() if br else None
    if cfg is not None:
        try:
            return cfg.next_unused(GR)
        except Exception:
            log.exception('general request number for salary revision %s', rev.pk)
    n, num = 0, f'SALREV-{rev.pk:05d}'
    while GR.objects.filter(document_number=num).exists():
        n += 1
        num = f'SALREV-{rev.pk:05d}-{n}'
    return num


MARK = 'SALREV:'


def rev_row(r, emps=None, user=None):
    e = (emps or {}).get(r.employee_id) or _emp(r.employee_id)
    GR = M('EmpManagement', 'GeneralRequest')
    gr = GR.objects.filter(pk=r.general_request_id).first() if r.general_request_id else None
    steps, mine = [], None
    if gr is not None:
        for a in gr.approvals.select_related('approver').order_by('level', 'pk'):
            steps.append({'level': a.level, 'role': a.role or '', 'approver': _user_name(a.approver), 'status': a.status,
                          'status_label': tr(a.status), 'note': a.note or ''})
            if user is not None and a.status == 'Pending' and (a.approver_id == getattr(user, 'pk', None) or getattr(a, 'deligate_to_id', None) == getattr(user, 'pk', None)):
                mine = a.pk
    return {'id': r.pk, 'employee_id': r.employee_id, 'employee': emp_label(e), 'emp_code': getattr(e, 'emp_code', ''),
            'branch_id': getattr(e, 'emp_branch_id_id', None), 'effective_from': r.effective_from.isoformat(), 'lines': r.lines,
            'total_old': str(q2(sum((q2(x.get('old')) for x in r.lines or []), ZERO))),
            'total_new': str(q2(sum((q2(x.get('new')) for x in r.lines or []), ZERO))),
            'reason': r.reason, 'status': r.status, 'status_label': tr(dict(r.STATUS).get(r.status, r.status)),
            'request_number': gr.document_number if gr else '', 'request_status': gr.status if gr else '', 'steps': steps, 'my_approval': mine,
            'arrears': r.arrears or {}, 'result': r.result, 'history': r.history or [],
            'created_at': r.created_at.isoformat() if r.created_at else None, 'applied_at': r.applied_at.isoformat() if r.applied_at else None}


def current_salary(emp):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    return [{'component_id': x.component_id, 'code': x.component.code or '', 'name': x.component.name, 'type': x.component.component_type,
             'value_type': x.component.component_value_type, 'amount': str(q2(x.amount))}
            for x in ESS.objects.filter(employee=emp, is_active=True).select_related('component').order_by('component__component_type', 'component__name')]


def create_revision(data, user=None):
    from .models import SalaryRevisionRequest
    problems = {}
    try:
        e = _emp(int(data.get('employee')))
    except (TypeError, ValueError):
        e = None
    if e is None:
        problems['employee'] = tr('Choose the employee.')
    eff = _parse_day(data.get('effective_from'))
    if eff is None:
        problems['effective_from'] = tr('Enter the effective date.')
    reason = str(data.get('reason') or '').strip()[:255]
    if not reason:
        problems['reason'] = tr('Enter the reason.')
    if problems:
        raise Refused(tr('Check the marked fields.'), problems=problems)
    if e.emp_joined_date and eff < e.emp_joined_date:
        raise Refused(tr('The effective date is before the joining date.'), problems={'effective_from': tr('Before the joining date.')})
    lk = locked_day(eff, e.emp_branch_id_id) or locked_month(eff, e.emp_branch_id_id)
    if lk is not None:
        raise Refused(lock_text(lk), problems={'effective_from': lock_text(lk)})
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    SC = M('PayrollManagement', 'SalaryComponent')
    rows = {x.component_id: x for x in ESS.objects.filter(employee=e).select_related('component')}
    lines, bad = [], {}
    for i, ln in enumerate(data.get('lines') or []):
        try:
            cid = int(ln.get('component_id'))
        except (TypeError, ValueError):
            bad[f'lines.{i}'] = tr('Choose the component.')
            continue
        c = rows[cid].component if cid in rows else SC.objects.filter(pk=cid).first()
        if c is None:
            bad[f'lines.{i}'] = tr('Choose the component.')
            continue
        if c.component_value_type != 'fixed':
            bad[f'lines.{i}'] = tr('{0} is worked out by its formula – revise the fixed components it uses.', c.name)
            continue
        try:
            new = Decimal(str(ln.get('new')))
        except (InvalidOperation, ValueError, TypeError):
            bad[f'lines.{i}'] = tr('Enter the new amount.')
            continue
        if new < 0:
            bad[f'lines.{i}'] = tr('Enter the new amount.')
            continue
        old = q2(rows[cid].amount) if cid in rows and rows[cid].is_active else ZERO
        if q2(new) == old:
            continue
        if any(x['component_id'] == cid for x in lines):
            bad[f'lines.{i}'] = tr('{0} is in the list twice.', c.name)
            continue
        lines.append({'component_id': cid, 'code': c.code or '', 'name': c.name, 'old': str(old), 'new': str(q2(new))})
    if bad:
        raise Refused(tr('Check the marked fields.'), problems=bad)
    if not lines:
        raise Refused(tr('Change at least one amount.'), problems={'lines': tr('Change at least one amount.')})
    if SalaryRevisionRequest.objects.filter(employee_id=e.pk, status='pending').exists():
        raise Refused(tr('A salary revision of this employee is already waiting for approval.'))
    with transaction.atomic():
        r = SalaryRevisionRequest.objects.create(employee_id=e.pk, effective_from=eff, lines=lines, reason=reason, created_by_id=getattr(user, 'pk', None))
        _note(r, 'created', user, reason)
        r.save(update_fields=['history'])
        GR = M('EmpManagement', 'GeneralRequest')
        rt = request_type(user)
        num = _doc_number(e, r)
        branch = e.emp_branch_id or getattr(e, 'work_location', None)
        if branch is None:
            raise Refused(tr('The employee has no branch.'))
        # the general request carries no amounts (the employee may see their own requests): Payroll › Revise salary shows them
        gr = GR(document_number=num, reason=tr('Salary revision from {0}', f'{eff:%d/%m/%Y}')[:200], branch=branch, request_type=rt,
                employee=e, status='Pending', remarks=f'{MARK}{r.pk}', created_by=user if getattr(user, 'pk', None) else None)
        gr.save()   # the workflow's first level (or the automatic approval) is made by EmpManagement's signal → on_request_saved
        SalaryRevisionRequest.objects.filter(pk=r.pk).update(general_request_id=gr.pk)
    r.refresh_from_db()
    gr.refresh_from_db()
    if gr.status == 'Approved' and r.status == 'pending':   # approved at once (no approval workflow)
        apply_revision(r, user)
        r.refresh_from_db()
    return r


def on_request_saved(gr):
    """post_save of a GeneralRequest: a "Salary revision" request approved → applied; rejected → closed."""
    rem = str(getattr(gr, 'remarks', '') or '')
    if not rem.startswith(MARK):
        return
    from .models import SalaryRevisionRequest
    try:
        r = SalaryRevisionRequest.objects.filter(pk=int(rem[len(MARK):])).first()
    except ValueError:
        return
    if r is None or r.status != 'pending':
        return
    if r.general_request_id is None:
        SalaryRevisionRequest.objects.filter(pk=r.pk).update(general_request_id=gr.pk)
        r.general_request_id = gr.pk
    st = str(gr.status or '').lower()
    if st == 'approved':
        apply_revision(r, getattr(gr, 'created_by', None))
    elif st == 'rejected':
        r.status = 'rejected'
        _note(r, 'rejected', None, '')
        r.save(update_fields=['status', 'history'])


def apply_revision(r, user=None):
    """Save the new amounts from the effective date; arrears of payslips already made from that date."""
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    e = _emp(r.employee_id)
    eff = r.effective_from
    try:
        with transaction.atomic():
            lk = locked_day(eff, e.emp_branch_id_id) or locked_month(eff, e.emp_branch_id_id)
            if lk is not None:
                raise Refused(lock_text(lk))
            for ln in r.lines:
                row = ESS.objects.filter(employee=e, component_id=ln['component_id']).first()
                new = q2(ln['new'])
                if row is None:
                    row = ESS(employee=e, component_id=ln['component_id'], amount=new, is_active=True, valid_from=eff)
                    row.save()
                    continue
                row._effective_period = eff.isoformat()
                row._revised_by = user if getattr(user, 'pk', None) else None
                row._remarks = f'Salary revision #{r.pk}: {r.reason}'[:250]
                if not row.is_active:
                    row.is_active = True
                    row.valid_from = eff
                row.amount = new
                row.save()
            arrears = {}
            from .cycles import _payslip_period
            P = M('PayrollManagement', 'Payslip')
            made = [p for p in P.objects.filter(employee=e).exclude(payroll_run__status='pending').select_related('payroll_run')
                    if _payslip_period(p)[1] >= eff]
            if made:   # payslips already made from the effective date: their arrears go to the next payroll
                from . import cycles
                res = cycles.calculate([e.pk], eff, component_ids=[ln['component_id'] for ln in r.lines], user=user, save=True)
                arrears = {'lines': res.get('lines', 0), 'total': res.get('total', '0.00')}
            r.status, r.applied_at, r.arrears = 'applied', timezone.now(), arrears
            r.result = (tr('Applied from {0}; arrears {1} for the next payroll.', f'{eff:%d/%m/%Y}', arrears.get('total')) if arrears.get('lines')
                        else tr('Applied from {0}.', f'{eff:%d/%m/%Y}'))[:255]
            _note(r, 'applied', user, r.result)
            r.save(update_fields=['status', 'applied_at', 'arrears', 'result', 'history'])
    except Exception as ex:
        log.exception('salary revision %s not applied', r.pk)
        msg = getattr(ex, 'message', None) or '; '.join(getattr(ex, 'messages', []) or []) or str(ex)
        from .models import SalaryRevisionRequest
        SalaryRevisionRequest.objects.filter(pk=r.pk).update(status='failed', result=str(msg)[:255])
        r.status, r.result = 'failed', str(msg)[:255]
    return r


def decide_revision(r, approve, user, note=''):
    """The approver's step from the Revise salary screen – the same Approval.approve / reject of the general request."""
    A = M('EmpManagement', 'Approval')
    if r.status != 'pending' or not r.general_request_id:
        raise Refused(tr('This salary revision is not waiting for approval.'))
    a = A.objects.filter(general_request_id=r.general_request_id, status='Pending').filter(Q(approver=user) | Q(deligate_to=user)).order_by('level').first()
    if a is None:
        raise Refused(tr('This step is not yours to approve.'), status=403)
    if approve:
        a.approve(note=note or None)
    else:
        if not str(note or '').strip():
            raise Refused(tr('Enter the reason for rejecting.'), problems={'note': tr('Enter the reason for rejecting.')})
        a.reject(note=note)
    r.refresh_from_db()
    if r.status == 'pending':   # the receiver did not run (e.g. signal disconnected): read the request
        on_request_saved(M('EmpManagement', 'GeneralRequest').objects.get(pk=r.general_request_id))
        r.refresh_from_db()
    return r


def cancel_revision(r, user=None):
    if r.status != 'pending':
        raise Refused(tr('Only a revision waiting for approval can be cancelled.'))
    GR = M('EmpManagement', 'GeneralRequest')
    GR.objects.filter(pk=r.general_request_id).update(status='Cancelled')
    M('EmpManagement', 'Approval').objects.filter(general_request_id=r.general_request_id, status='Pending').delete()
    r.status = 'cancelled'
    _note(r, 'cancelled', user, '')
    r.save(update_fields=['status', 'history'])
    return r


# ================================================================== re-hire (item 228)
def _settlement_of(emp_id):
    X = M('PayrollPlus', 'ExitSettlement')
    return X.objects.filter(employee_id=emp_id).exclude(status='cancelled').order_by('-last_working_date').first()


def leaver_info(emp):
    from PayrollManagement.signals import last_working_day
    lwd = last_working_day(emp)
    st = _settlement_of(emp.pk)
    if lwd is None and st is not None:
        lwd = st.last_working_date
    if lwd and emp.emp_joined_date and lwd < emp.emp_joined_date:   # an earlier employment – re-hired already
        return None, st
    return lwd, st


def periods(emp_id):
    from .models import ServicePeriod
    return [{'start': p.start.isoformat(), 'end': p.end.isoformat() if p.end else None, 'settlement_id': p.settlement_id,
             'gratuity_continuity': p.gratuity_continuity, 'note': p.note} for p in ServicePeriod.objects.filter(employee_id=emp_id)]


@transaction.atomic
def rehire(emp, data, user=None):
    from .models import GoLiveEmployee, ServicePeriod
    d = _parse_day(data.get('rehire_date'))
    if d is None:
        raise Refused(tr('Enter the re-hire date.'), problems={'rehire_date': tr('Enter the re-hire date.')})
    lwd, st = leaver_info(emp)
    if lwd is None:
        raise Refused(tr('This employee has not left – only a leaver can be re-hired.'))
    if d <= lwd:
        raise Refused(tr('The re-hire date must be after the last working day ({0}).', f'{lwd:%d/%m/%Y}'), problems={'rehire_date': tr('After the last working day.')})
    cont = str(data.get('gratuity_continuity')).lower() in ('1', 'true', 'yes', 'on')
    old_start = emp.emp_joined_date
    first = ServicePeriod.objects.filter(employee_id=emp.pk).order_by('start').first()
    first_start = first.start if first else old_start
    if not ServicePeriod.objects.filter(employee_id=emp.pk, start=old_start).exists():
        ServicePeriod.objects.create(employee_id=emp.pk, start=old_start or lwd, end=lwd, settlement_id=getattr(st, 'pk', None),
                                     note=tr('Closed at re-hire'), created_by_id=getattr(user, 'pk', None))
    else:
        ServicePeriod.objects.filter(employee_id=emp.pk, start=old_start).update(end=lwd, settlement_id=getattr(st, 'pk', None))
    ServicePeriod.objects.create(employee_id=emp.pk, start=d, gratuity_continuity=cont, note=str(data.get('note') or '')[:255],
                                 created_by_id=getattr(user, 'pk', None))
    leave_closed = None
    try:   # v1.65.0 (item 96): the leave balances of the old employment end on its last day (paid in the settlement); the new one earns from d
        from LeavePolicy.leave_rules import close_at_rehire
        leave_closed = close_at_rehire(emp, lwd, st, getattr(user, 'pk', None))
    except ImportError:
        pass
    E = type(emp)
    E.objects.filter(pk=emp.pk).update(emp_joined_date=d, is_active=True)
    try:
        EI = M('EmployeeProfile', 'EmploymentInfo')
        EI.objects.filter(employee_id=emp.pk).update(status='active', date_of_leaving=None)
    except LookupError:
        pass
    g = GoLiveEmployee.objects.filter(employee_id=emp.pk).first()
    if cont:
        if g is None:
            g = GoLiveEmployee(employee_id=emp.pk)
        g.gratuity_from = first_start
        g.note = (tr('Re-hired {0}: gratuity service from the first joining date', f'{d:%d/%m/%Y}'))[:200]
        g.save()
        # the days between the two periods are not service: kept as unpaid days before go-live (Employee › Employment), which the
        # gratuity report and the final settlement already take off the service
        gap = (d - lwd).days - 1
        if gap > 0:
            try:
                EI = M('EmployeeProfile', 'EmploymentInfo')
                ei, _ = EI.objects.get_or_create(employee_id=emp.pk)
                ei.unpaid_days_before_golive = Decimal(str(ei.unpaid_days_before_golive or 0)) + Decimal(gap)
                ei.save(update_fields=['unpaid_days_before_golive'])
            except Exception:
                log.exception('gap days of the re-hire of %s not kept', emp.pk)
    elif g is not None and g.gratuity_from and g.gratuity_from < d:
        g.gratuity_from = None
        g.note = (tr('Re-hired {0}: gratuity service from the re-hire date', f'{d:%d/%m/%Y}'))[:200]
        g.save()
    try:
        from .golive import forget
        forget()
    except Exception:
        pass
    emp.refresh_from_db()
    return {'employee_id': emp.pk, 'employee': emp_label(emp), 'rehire_date': d.isoformat(), 'old_joined': old_start.isoformat() if old_start else None,
            'last_working_day': lwd.isoformat(), 'settlement': getattr(st, 'number', '') or (st.pk if st else None),
            'gratuity_continuity': cont, 'periods': periods(emp.pk), 'leave': leave_closed}   # v1.65.0 (item 96)


def leavers():
    """Employees who left (inactive with a last working day / final settlement) – candidates for re-hire."""
    E = M('EmpManagement', 'emp_master')
    X = M('PayrollPlus', 'ExitSettlement')
    ids = set(X.objects.exclude(status='cancelled').values_list('employee_id', flat=True))
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        ids |= set(R.objects.filter(status__iexact='approved').values_list('employee_id', flat=True))
    except LookupError:
        pass
    out = []
    for e in E.objects.filter(pk__in=ids).order_by('emp_code'):
        lwd, st = leaver_info(e)
        if lwd is None:
            continue
        out.append({'id': e.pk, 'employee': emp_label(e), 'emp_code': e.emp_code, 'branch_id': e.emp_branch_id_id, 'active': e.is_active,
                    'joined': e.emp_joined_date.isoformat() if e.emp_joined_date else None, 'last_working_day': lwd.isoformat(),
                    'settlement': (st.number or f'#{st.pk}') if st else '', 'settlement_status': st.status if st else '',
                    'periods': periods(e.pk)})
    return out


# ================================================================== salary revision history import (item 231)
COLUMNS = ['employee_code', 'component_code', 'effective_date', 'old_amount', 'new_amount', 'remarks']


def template_csv():
    return ('employee_code,component_code,effective_date,old_amount,new_amount,remarks\n'
            'E001,BASIC,2025-01-01,4000.00,4500.00,Annual increase 2025\n')


def _read_rows(f, name=''):
    name = (name or getattr(f, 'name', '') or '').lower()
    raw = f.read() if hasattr(f, 'read') else f
    rows = []
    if name.endswith(('.xlsx', '.xlsm')):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = None
        for vals in it:
            if head is None:
                if vals and any(v not in (None, '') for v in vals):
                    head = [str(v or '').strip().lower().replace(' ', '_') for v in vals]
                continue
            if vals is None or all(v in (None, '') for v in vals):
                continue
            rows.append({head[i]: v for i, v in enumerate(vals) if i < len(head)})
        return rows
    import csv
    text = raw.decode('utf-8-sig') if isinstance(raw, bytes) else str(raw)
    rd = csv.DictReader(io.StringIO(text))
    for r in rd:
        r = {str(k or '').strip().lower().replace(' ', '_'): v for k, v in r.items()}
        if all(str(v or '').strip() == '' for v in r.values()):
            continue
        rows.append(r)
    return rows


def import_history(f, name='', apply=False, user=None, allowed=None):
    """Dry run (apply=False) or apply. Returns {rows: [{row, employee_code, component_code, effective_date, old, new, errors, warnings,
    ok}], ok, errors, applied}."""
    E = M('EmpManagement', 'emp_master')
    SC = M('PayrollManagement', 'SalaryComponent')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    H = M('PayrollManagement', 'SalaryRevisionHistory')
    try:
        rows = _read_rows(f, name)
    except Exception as ex:
        raise Refused(tr('The file could not be read – use the template (CSV or Excel).'), problems={'file': str(ex)[:200]})
    if not rows:
        raise Refused(tr('The file has no rows.'), problems={'file': tr('The file has no rows.')})
    missing = [c for c in ('employee_code', 'component_code', 'effective_date', 'new_amount') if c not in rows[0]]
    if missing:
        raise Refused(tr('Columns missing: {0}', ', '.join(missing)), problems={'file': tr('Columns missing: {0}', ', '.join(missing))})
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):
        errs, warns = [], []
        code = str(r.get('employee_code') or '').strip()
        if isinstance(r.get('employee_code'), float) and r['employee_code'].is_integer():
            code = str(int(r['employee_code']))
        ccode = str(r.get('component_code') or '').strip()
        e = E.objects.filter(emp_code__iexact=code).first() if code else None
        if e is None:
            errs.append(tr('Employee {0} not found.', code or '–'))
        elif allowed is not None and e.pk not in allowed:
            errs.append(tr('This employee is not in your branches.'))
            e = None
        c = None
        if ccode:
            cs = list(SC.objects.filter(code__iexact=ccode))
            if e is not None and len(cs) > 1:
                c = next((x for x in cs if x.branch_id == e.emp_branch_id_id), None) or next((x for x in cs if x.branch_id is None), None) or cs[0]
            else:
                c = cs[0] if cs else None
        if c is None:
            errs.append(tr('Component {0} not found.', ccode or '–'))
        eff = _parse_day(r.get('effective_date'))
        if eff is None:
            errs.append(tr('Effective date missing or not a date (use YYYY-MM-DD or DD/MM/YYYY).'))
        try:
            new = Decimal(str(r.get('new_amount')).replace(',', '').strip())
            if new < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            new = None
            errs.append(tr('New amount missing or not a number.'))
        old = None
        if str(r.get('old_amount') or '').strip() != '':
            try:
                old = Decimal(str(r.get('old_amount')).replace(',', '').strip())
            except (InvalidOperation, ValueError):
                errs.append(tr('Old amount is not a number.'))
        if e is not None and eff is not None and e.emp_joined_date and eff < e.emp_joined_date:
            errs.append(tr('The effective date is before the joining date ({0}).', f'{e.emp_joined_date:%d/%m/%Y}'))
        if eff is not None and eff > timezone.localdate():
            errs.append(tr('The effective date is in the future – use Revise salary for a coming change.'))
        if e is not None and c is not None and eff is not None:
            k = (e.pk, c.pk, eff)
            if k in seen:
                errs.append(tr('The same employee, component and date are in the file twice.'))
            seen.add(k)
            h = H.objects.filter(employee=e, component=c).first()
            if h is not None and any(str(x.get('effective_period') or '')[:10] == eff.isoformat() for x in h.revisions or []):
                errs.append(tr('This revision is already in the history.'))
            if not ESS.objects.filter(employee=e, component=c).exists():
                warns.append(tr('The employee does not have this component now.'))
        out.append({'row': i, 'employee_code': code, 'employee': emp_label(e) if e else '', 'component_code': ccode,
                    'component': c.name if c else '', 'effective_date': eff.isoformat() if eff else str(r.get('effective_date') or ''),
                    'old': str(q2(old)) if old is not None else '', 'new': str(q2(new)) if new is not None else '',
                    'remarks': str(r.get('remarks') or '')[:200], 'errors': errs, 'warnings': warns, 'ok': not errs,
                    '_k': (e.pk if e else None, c.pk if c else None, eff, old, new)})
    applied = 0
    if apply:
        with transaction.atomic():
            groups = {}
            for x in out:
                if x['ok']:
                    groups.setdefault((x['_k'][0], x['_k'][1]), []).append(x)
            for (eid, cid), items in groups.items():
                h, _ = H.objects.get_or_create(employee_id=eid, component_id=cid)
                items.sort(key=lambda x: x['_k'][2])
                new_revs = []
                for x in items:
                    _e, _c, eff, old, new = x['_k']
                    new_revs.append({'old_amount': str(q2(old)) if old is not None else None, 'new_amount': str(q2(new)),
                                     'revised_by': _user_name(user) or None, 'revised_by_id': getattr(user, 'pk', None),
                                     'revised_on': timezone.now().isoformat(), 'effective_period': eff.isoformat(),
                                     'remarks': x['remarks'] or 'Imported salary history', 'source': 'import v1.62.11'})
                # older history first: the imported revisions go before the ones ZEO recorded itself (salary_dates reads them in order)
                h.revisions = new_revs + list(h.revisions or [])
                h.save(update_fields=['revisions', 'date_updated'])
                applied += len(items)
    for x in out:
        x.pop('_k', None)
    return {'rows': out, 'ok': sum(1 for x in out if x['ok']), 'errors': sum(1 for x in out if not x['ok']), 'applied': applied, 'dry_run': not apply}
