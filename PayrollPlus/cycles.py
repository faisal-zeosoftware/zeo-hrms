"""
v1.55.0 – pay calendars, off-cycle runs and arrears.

Pay calendars (weekly, every two weeks, twice a month)
  A period run is a normal PayrollRun with the period's dates, made and calculated by the usual payroll engine
  (PayrollManagement.signals.process_payroll); the engine asks this module through four hooks:
    period_of(run)            the dates and days of a period run (instead of the month / pay structure cycle)
    excluded(run, employee)   an employee on a calendar is left out of monthly runs, and a period run only pays its members
    skip_events(run)          loans and salary advances are deducted once a month – in the last period that ends in the month
    adjust_payslip(...)       the monthly amounts of the salary structure are prorated to the period (× 12 / periods a year;
                              the engine already took off unpaid days of the period), and waiting arrears are added
  Overtime, attendance, general requests, air tickets and encashments are what the engine calculates for the period's dates.

Off-cycle runs
  A PayrollRun outside the schedule (bonus, commission, correction, final settlement, advance, arrears) with only the given
  amounts – no engine run; it is approved, paid and exported (WPS) like any run.

Arrears
  After a salary change with an earlier effective date, each payslip already made from that date is compared with the new
  salary structure: (new monthly amount × paid days ratio × period factor) − what the payslip paid, per component. The lines
  wait and are added to the employee's next payslip as "Salary arrears" (or "Salary arrears recovery" when negative), or are
  paid at once in an off-cycle run. Deleting / recalculating that payslip puts them back to waiting.
"""
import calendar
import logging
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
PER_YEAR = {'weekly': 52, 'biweekly': 26, 'semimonthly': 24}
EVENTS = {'overtime', 'loan', 'advance_salary', 'air_ticket', 'gratuity', 'leave_encashment', 'bonus', 'commission'}
PERIOD_WORDS = re.compile(r'days|hours|ot_|_ot|overtime|encash|recovery|reimbursement|air_ticket|worked|present', re.I)
ARREARS_CODE, RECOVERY_CODE = 'ARREARS', 'ARREARSREC'
Q2 = Decimal('0.01')


class CycleError(Exception):
    pass


def money(v):
    return Decimal(str(v or 0)).quantize(Q2, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ periods
def factor(freq):
    return Decimal(12) / Decimal(PER_YEAR[freq])


def period_containing(cal, d):
    if cal.frequency == 'semimonthly':
        last = calendar.monthrange(d.year, d.month)[1]
        return (date(d.year, d.month, 1), date(d.year, d.month, 15)) if d.day <= 15 else (date(d.year, d.month, 16), date(d.year, d.month, last))
    step = 7 if cal.frequency == 'weekly' else 14
    k = (d - cal.first_start).days // step
    s = cal.first_start + timedelta(days=k * step)
    return s, s + timedelta(days=step - 1)


def next_period(cal, start):
    s, e = period_containing(cal, start)
    return period_containing(cal, e + timedelta(days=1))


def closes_month(cal, start, end):
    """The last period that ends in its month (loans and advances are deducted in it)."""
    ns, ne = period_containing(cal, end + timedelta(days=1))
    return (ne.year, ne.month) != (end.year, end.month)


def periods(cal, around=None, before=4, after=6):
    around = around or date.today()
    s, e = period_containing(cal, around)
    out = []
    cur = s
    for _ in range(before):
        cur = period_containing(cal, cur - timedelta(days=1))[0]
    for _ in range(before + after + 1):
        a, b = period_containing(cal, cur)
        if cal.frequency != 'semimonthly' and a < cal.first_start:
            cur = b + timedelta(days=1)
            continue
        out.append((a, b))
        cur = b + timedelta(days=1)
    return out


# ------------------------------------------------------------------ members
def member_ids(cal):
    E = M('EmpManagement', 'emp_master')
    from django.db.models import Q
    q = Q(pk__in=[int(x) for x in cal.employee_ids or []])
    if cal.category_ids:
        q |= Q(emp_ctgry_id__in=[int(x) for x in cal.category_ids])
    return list(E.objects.filter(q).filter(Q(is_active=True) | Q(is_active__isnull=True)).values_list('pk', flat=True))


def calendar_of_employee():
    """{employee id: calendar id} of the active calendars (an employee is on one calendar: the first by name)."""
    from .models import PayCalendar
    out = {}
    from .models import PayrollPlusSettings
    on = PayrollPlusSettings.get().pay_frequencies()   # v1.55.0: a frequency switched off in Payroll controls does not apply
    for cal in PayCalendar.objects.filter(active=True, frequency__in=on).order_by('name', 'pk'):
        for e in member_ids(cal):
            out.setdefault(e, cal.pk)
    return out


# ------------------------------------------------------------------ engine hooks
_cache = {}


def _run_info(run):
    """('calendar', CalendarRun) | ('offcycle', OffCycleRun) | ('monthly', None) – cached per run id while processing."""
    from .models import CalendarRun, OffCycleRun
    from django.db import connection as _conn
    key = (getattr(_conn, 'schema_name', ''), getattr(run, 'pk', None))   # v1.56.0: run ids repeat between companies
    if key in _cache:
        return _cache[key]
    cr = CalendarRun.objects.select_related('calendar').filter(payroll_run_id=key[1]).first()
    info = ('calendar', cr) if cr else (('offcycle', None) if OffCycleRun.objects.filter(payroll_run_id=key[1]).exists() else ('monthly', None))
    _cache[key] = info
    if len(_cache) > 200:
        _cache.clear()
    return info


def forget(run_id):
    from django.db import connection as _conn
    _cache.pop((getattr(_conn, 'schema_name', ''), run_id), None)
    _members.pop((getattr(_conn, 'schema_name', ''), run_id), None)


_members = {}


def _calendar_map(run):
    """calendar_of_employee() once per run (it was read again for every employee), for at most a minute."""
    import time
    from django.db import connection as _conn
    key = (getattr(_conn, 'schema_name', ''), getattr(run, 'pk', None))
    hit = _members.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    m = calendar_of_employee()
    if len(_members) > 200:
        _members.clear()
    _members[key] = (time.time(), m)
    return m


def period_of(run):
    kind, cr = _run_info(run)
    if kind != 'calendar':
        return None
    return cr.period_start, cr.period_end, Decimal((cr.period_end - cr.period_start).days + 1)


def excluded(run, employee):
    kind, cr = _run_info(run)
    if kind == 'offcycle':
        return True
    try:   # v1.56.0 mid-year go-live: a period already paid outside ZEO
        from .golive import paid_outside
        if paid_outside(run, employee, cr.period_end if kind == 'calendar' else None):
            return True
    except ImportError:
        pass
    on = _calendar_map(run).get(employee.pk)
    if kind == 'calendar':
        return on != cr.calendar_id
    return on is not None


def skip_events(run):
    kind, cr = _run_info(run)
    return kind == 'calendar' and not cr.closes_month


def scalable(comp):
    if comp.payroll_category in EVENTS or comp.code in (ARREARS_CODE, RECOVERY_CODE):
        return False
    if comp.component_value_type == 'fixed':
        return True
    f = comp.formula or ''
    return bool(f.strip()) and not PERIOD_WORDS.search(f)


def adjust_payslip(run, payslip, employee):
    """Called by the engine before the payslip totals: prorate to the period, add waiting arrears."""
    kind, cr = _run_info(run)
    if kind == 'offcycle':
        return
    PC = M('PayrollManagement', 'PayslipComponent')
    if kind == 'calendar' and payslip.pk not in (cr.scaled or []):
        mine = set(M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee=employee, is_active=True).values_list('component_id', flat=True))
        for pc in PC.objects.filter(payslip=payslip).select_related('component'):
            if pc.component_id in mine and scalable(pc.component):
                pc.amount = money(Decimal(str(pc.amount or 0)) * cr.factor)
                pc.save(update_fields=['amount'])
        cr.scaled = list(cr.scaled or []) + [payslip.pk]
        type(cr).objects.filter(pk=cr.pk).update(scaled=cr.scaled)
    include_arrears(payslip, employee)


# ------------------------------------------------------------------ arrears
def _component(code, name, ctype, branch_id):
    SC = M('PayrollManagement', 'SalaryComponent')
    c = SC.objects.filter(code=code, branch_id=branch_id).first() or SC.objects.filter(code=code, branch__isnull=True).first()
    if c is None:
        c = SC.objects.create(name=name, code=code, branch_id=branch_id, component_type=ctype, payroll_category='other_allowance',
                              component_value_type='variable', show_in_payslip=True,
                              description='v1.55.0 – salary differences of earlier months (salary change with an earlier effective date)')
    return c


def release_orphans(employee_id=None):
    """Lines 'paid' in a payslip that no longer exists (run deleted or recalculated) wait again."""
    from .models import ArrearLine
    P = M('PayrollManagement', 'Payslip')
    qs = ArrearLine.objects.filter(status='included')
    if employee_id:
        qs = qs.filter(employee_id=employee_id)
    gone = [x.pk for x in qs if not P.objects.filter(pk=x.included_payslip_id).exists()]
    if gone:
        ArrearLine.objects.filter(pk__in=gone).update(status='pending', included_payslip_id=None)
    return len(gone)


def include_arrears(payslip, employee):
    from .models import ArrearLine
    release_orphans(employee.pk)
    lines = list(ArrearLine.objects.filter(employee_id=employee.pk, status='pending'))
    if not lines:
        return Decimal('0')
    total = money(sum(Decimal(str(x.amount)) for x in lines))
    PC = M('PayrollManagement', 'PayslipComponent')
    if total > 0:
        PC.objects.update_or_create(payslip=payslip, component=_component(ARREARS_CODE, 'Salary arrears', 'addition', employee.emp_branch_id_id), defaults={'amount': total})
    elif total < 0:
        PC.objects.update_or_create(payslip=payslip, component=_component(RECOVERY_CODE, 'Salary arrears recovery', 'deduction', employee.emp_branch_id_id), defaults={'amount': -total})
    M('PayrollManagement', 'Payslip').objects.filter(pk=payslip.pk).update(arrears=total)
    ArrearLine.objects.filter(pk__in=[x.pk for x in lines]).update(status='included', included_payslip_id=payslip.pk)
    return total


def _payslip_period(p):
    run = p.payroll_run
    kind, cr = _run_info(run)
    if kind == 'calendar':
        return cr.period_start, cr.period_end, cr.factor
    s = run.attendance_start_date or date(run.year, run.month, 1)
    e = run.attendance_end_date or date(run.year, run.month, calendar.monthrange(run.year, run.month)[1])
    return s, e, Decimal(1)


def revised_components(employee_id, since):
    """Components of the employee changed (revision recorded) on or after the date."""
    H = M('PayrollManagement', 'SalaryRevisionHistory')
    out = []
    for h in H.objects.filter(employee_id=employee_id).select_related('component'):
        for r in h.revisions or []:
            try:
                on = date.fromisoformat(str(r.get('revised_on') or '')[:10])
            except ValueError:
                continue
            if on >= since:
                out.append(h.component_id)
                break
    return out


def calculate(employee_ids, effective_from, component_ids=None, user=None, save=True):
    """Arrear lines for the payslips made from the effective date. Returns {employees, lines, total, rows}."""
    from .models import ArrearLine
    E = M('EmpManagement', 'emp_master')
    P = M('PayrollManagement', 'Payslip')
    PC = M('PayrollManagement', 'PayslipComponent')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    SC = M('PayrollManagement', 'SalaryComponent')
    off_runs = set(M('PayrollPlus', 'OffCycleRun').objects.values_list('payroll_run_id', flat=True))
    rows, made = [], 0
    for emp in E.objects.filter(pk__in=employee_ids):
        comps = [int(c) for c in component_ids] if component_ids else revised_components(emp.pk, effective_from)
        comps = [c for c in SC.objects.filter(pk__in=comps) if scalable(c) or c.component_value_type == 'fixed']
        if not comps:
            rows.append({'employee_id': emp.pk, 'employee': f'{emp.emp_code} {emp.emp_first_name or ""}'.strip(), 'lines': [], 'total': '0.00',
                         'note': tr('No salary change on or after this date.')})
            continue
        now = {x.component_id: Decimal(str(x.amount or 0)) for x in ESS.objects.filter(employee=emp, is_active=True, component__in=comps)}
        done = set(ArrearLine.objects.filter(employee_id=emp.pk, status__in=('pending', 'included')).values_list('source_payslip_id', 'component_id'))
        lines = []
        for p in P.objects.filter(employee=emp).exclude(payroll_run_id__in=off_runs).select_related('payroll_run').order_by('payroll_run__year', 'payroll_run__month', 'pk'):
            s, e, f = _payslip_period(p)
            if e < effective_from or str(p.payroll_run.status).lower() == 'pending':
                continue
            paid = {x.component_id: Decimal(str(x.amount or 0)) for x in PC.objects.filter(payslip=p, component__in=comps)}
            twd = Decimal(str(p.total_working_days or 0))
            ratio = (Decimal(str(p.days_worked or 0)) / twd) if twd > 0 else Decimal(1)
            # a period that started before the effective date changes only for the days from that date
            share = Decimal((e - effective_from).days + 1) / Decimal((e - s).days + 1) if s < effective_from <= e else Decimal(1)
            for c in comps:
                if (p.pk, c.pk) in done:
                    continue
                r = ratio if c.deduct_leave else Decimal(1)
                raw_new = now.get(c.pk, Decimal(0)) * r * f * share
                raw_old = paid.get(c.pk, Decimal(0)) * share
                new, old = money(raw_new), money(raw_old)
                diff = money(raw_new - raw_old)   # rounded once
                if diff == 0:
                    continue
                lines.append({'month': s, 'payslip': p.pk, 'component_id': c.pk, 'component': c.name, 'old': old, 'new': new, 'ratio': r,
                              'amount': -diff if c.component_type == 'deduction' else diff})
        total = money(sum(x['amount'] for x in lines))
        if save and lines:
            with transaction.atomic():
                for x in lines:
                    ArrearLine.objects.create(employee_id=emp.pk, component_id=x['component_id'], month=x['month'], source_payslip_id=x['payslip'],
                                              old_amount=x['old'], new_amount=x['new'], paid_ratio=x['ratio'].quantize(Decimal('0.000001')), amount=x['amount'],
                                              effective_from=effective_from, created_by_id=getattr(user, 'pk', None))
            made += len(lines)
        rows.append({'employee_id': emp.pk, 'employee': f'{emp.emp_code} {emp.emp_first_name or ""} {emp.emp_last_name or ""}'.strip(),
                     'total': str(total), 'lines': [dict(x, month=x['month'].isoformat(), old=str(x['old']), new=str(x['new']), amount=str(x['amount']),
                                                         ratio=str(x['ratio'].quantize(Decimal('0.0001')))) for x in lines]})
    return {'rows': rows, 'lines': made, 'total': str(money(sum(Decimal(r['total']) for r in rows)))}


# ------------------------------------------------------------------ runs
def _number(run, branches):
    """One document number per branch, as the payroll screen does (DocumentNumbering 'payroll_run')."""
    DN = M('OrganisationManager', 'DocumentNumbering')
    PRB = M('PayrollManagement', 'PayrollRunBranch')
    for b in branches:
        cfg = DN.objects.filter(branch_id=b.pk, type='payroll_run').first()
        if cfg is None:
            raise CycleError(tr('Set up the document numbering of payroll runs for the branch {0} first.', b.branch_name))
        PRB.objects.create(payroll_run=run, branch=b, document_number=cfg.get_next_number())


def _branches(emp_ids):
    B = M('OrganisationManager', 'brnch_mstr')
    E = M('EmpManagement', 'emp_master')
    return list(B.objects.filter(pk__in=E.objects.filter(pk__in=emp_ids).values('emp_branch_id')).order_by('pk'))


def run_period(cal, start, user=None, employee_ids=None):
    """Make and calculate the run of the period that starts on (or contains) the date. Returns the CalendarRun."""
    from .models import CalendarRun
    a, b = period_containing(cal, start)
    if CalendarRun.objects.filter(calendar=cal, period_start=a).exists():
        raise CycleError(tr('This period already has a payroll run.'))
    members = member_ids(cal)
    if employee_ids:
        members = [e for e in members if e in set(int(x) for x in employee_ids)]
    if not members:
        raise CycleError(tr('Nobody is paid on this calendar. Add employees or categories to it.'))
    PR = M('PayrollManagement', 'PayrollRun')
    E = M('EmpManagement', 'emp_master')
    with transaction.atomic():
        run = PR.objects.create(name=f'{cal.name} · {a:%d %b} – {b:%d %b %Y}'[:100], month=b.month, year=b.year, attendance_start_date=a,
                                attendance_end_date=b, payment_date=b + timedelta(days=cal.pay_days_after), status='pending')
        cr = CalendarRun.objects.create(calendar=cal, period_start=a, period_end=b, pay_date=b + timedelta(days=cal.pay_days_after),
                                        payroll_run_id=run.pk, closes_month=closes_month(cal, a, b), factor=factor(cal.frequency).quantize(Decimal('0.000001')),
                                        created_by_id=getattr(user, 'pk', None))
        forget(run.pk)
        _number(run, _branches(members))
        try:
            from .services import record_prepared
            record_prepared(run, user)
        except Exception:
            pass
        run.employees.set(E.objects.filter(pk__in=members))   # the engine calculates the payslips now (m2m signal)
    return cr


def off_cycle(kind, title, pay_date, lines, user=None):
    """lines: [{employee_id, component_id, amount, note}] → an off-cycle run with one payslip per employee."""
    from .models import ArrearLine, OffCycleRun
    PR = M('PayrollManagement', 'PayrollRun')
    P = M('PayrollManagement', 'Payslip')
    PC = M('PayrollManagement', 'PayslipComponent')
    SC = M('PayrollManagement', 'SalaryComponent')
    E = M('EmpManagement', 'emp_master')
    from PayrollManagement.signals import calculate_payslip_totals
    clean, by_emp = [], {}
    for x in lines or []:
        try:
            eid, cid, amt = int(x['employee_id']), int(x['component_id']), money(x['amount'])
        except (KeyError, TypeError, ValueError):
            raise CycleError(tr('Each line needs an employee, a component and an amount.'))
        if amt <= 0:
            raise CycleError(tr('Amounts must be more than zero (use a deduction component to take money back).'))
        if not E.objects.filter(pk=eid).exists() or not SC.objects.filter(pk=cid).exists():
            raise CycleError(tr('Each line needs an employee, a component and an amount.'))
        clean.append({'employee_id': eid, 'component_id': cid, 'amount': str(amt), 'note': str(x.get('note') or '')[:120]})
        by_emp.setdefault(eid, []).append((cid, amt))
    if kind == 'arrears' and not clean:
        release_orphans()
        for a in ArrearLine.objects.filter(status='pending'):
            by_emp.setdefault(a.employee_id, [])
    if not by_emp:
        raise CycleError(tr('Add at least one line.'))
    with transaction.atomic():
        run = PR.objects.create(name=title[:100], month=pay_date.month, year=pay_date.year, payment_date=pay_date, attendance_start_date=pay_date,
                                attendance_end_date=pay_date, status='processed')
        oc = OffCycleRun.objects.create(kind=kind, title=title[:120], pay_date=pay_date, payroll_run_id=run.pk, lines=clean, created_by_id=getattr(user, 'pk', None))
        forget(run.pk)
        _number(run, _branches(list(by_emp)))
        run.employees.set(E.objects.filter(pk__in=list(by_emp)))   # processed already: the engine does not run
        for eid, items in by_emp.items():
            emp = E.objects.get(pk=eid)
            p = P.objects.create(payroll_run=run, employee=emp, total_working_days=0, days_worked=0)
            for cid, amt in items:
                pc, made = PC.objects.get_or_create(payslip=p, component_id=cid, defaults={'amount': amt})
                if not made:
                    pc.amount = money(Decimal(str(pc.amount)) + amt)
                    pc.save(update_fields=['amount'])
            if kind == 'arrears':
                include_arrears(p, emp)
            calculate_payslip_totals(p)
        try:
            from .services import record_prepared
            record_prepared(run, user)
        except Exception:
            pass
    return oc
