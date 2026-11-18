"""v1.67.27 (pending item 1.62.13, items 238 – 242) – the reports of the report centre still missing.

Payroll
  social-security     Social security summary   GPSSA / GOSI / the other statutory schemes: contributory salary, employee and employer
                                                share per payslip (from the payslip lines of the scheme's components)
  lop-summary         Loss of pay summary       per payslip: payroll days, paid days, days without pay (unpaid leave, absence, before
                                                joining / after leaving) and the pay lost on the components that deduct leave
  payroll-inputs      Data for payroll          per employee for the payroll month: the figures the payroll formulas read (days
                                                without pay, absence, late, overtime, shifts, on-call) and the monthly inputs –
                                                the sheet HR checks before running the payroll
  payroll-liability   Payroll liability summary what the payroll runs of the period owe: net salaries payable (paid / not paid),
                                                held salaries, every statutory scheme (employee + employer share) – each to whom
  payroll-journal     Payroll journal           the accounting lines of the payroll journals (and salary payment journals) of the runs
  salary-holds        Salary withhold           every salary hold of the period: held, released, still held
  dews / adpf         DIFC DEWS / ADPF          the contributions of the year by employee and month (DEWS: + the gratuity still owed for
                                                the service before DEWS)
Leave & attendance
  leave-on-day        Daily leave status        who is on leave on a day (approved and waiting), back on, by department
  expected-hours      Expected vs worked hours  per employee: planned hours of the shifts against the hours worked, the difference
  office-remote       Office vs remote          per employee: days in the office, from home (approved), on duty outside, outside the
                                                geofence
HR operations
  helpdesk-sla        HR helpdesk SLA           every question: first answer and solution against the category's hours
  helpdesk-trend      HR helpdesk trends        per month: received, solved, on time %, average hours, rating
Talent
  nine-box            Nine-box grid             performance (final rating of the latest appraisal) × potential (HR / manager)
  salary-bands        Salary bands              the monthly salary against the grade's minimum / maximum, compa-ratio
  lms-progress        Learning progress         online courses: progress %, score, status
  training-ratings    Training ratings          classroom sessions: attendance, pass rate, feedback and trainer rating
Read-only; the only new table is PerformanceManagement.TalentPotential (the potential of the nine-box).
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q

M = apps.get_model
D0 = Decimal('0')


def _R():
    from . import reports as R
    return R


def _d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:   # noqa: BLE001
        return D0


def _m(v):
    return round(float(v or 0), 2)


def _emps(run):
    return {e.id: e for e in run.all_employees}


def _period_label(pr):
    return date(pr.year, pr.month, 1).strftime('%b %Y')


def _runs(run):
    """payroll runs whose month touches the period"""
    R = _R()
    out = []
    a, b = run.date_from or date(1900, 1, 1), run.date_to or date(2999, 12, 31)
    for pr in M('PayrollManagement', 'PayrollRun').objects.all().order_by('year', 'month', 'id'):
        st = date(pr.year, pr.month, 1)
        if R.month_end(st) >= a and st <= b:
            out.append(pr)
    return out


# ================================================================== payroll
def _schemes():
    try:
        return list(M('PayrollPlus', 'StatutoryScheme').objects.all())
    except LookupError:
        return []


def r_social_security(run):
    R = _R()
    from PayrollPlus.statutory import BASE_CATEGORIES
    sch = [s for s in _schemes() if s.employee_component or s.employer_component]
    by_code = {}
    for s in sch:
        if s.employee_component:
            by_code[s.employee_component] = (s, 'employee')
        if s.employer_component:
            by_code[s.employer_component] = (s, 'employer')
    rows = []
    for p in R._payslips(run, components=True):
        if (p.status or '').lower() == 'rejected':
            continue
        per = {}
        lines = list(p.components.all())
        for pc in lines:
            hit = by_code.get(pc.component.code or '')
            if not hit:
                continue
            s, side = hit
            r = per.setdefault(s.pk, {'s': s, 'employee': D0, 'employer': D0})
            r[side] += _d(pc.amount)
        for v in per.values():
            s = v['s']
            if not (v['employee'] or v['employer']):
                continue   # the scheme's lines are on the payslip but it does not apply to this employee
            cats = BASE_CATEGORIES.get(s.base)
            base = sum((_d(pc.amount) for pc in lines if pc.component.component_type == 'addition' and pc.component.show_in_payslip
                        and (cats is None or pc.component.payroll_category in cats)), D0)
            if s.max_base:
                base = min(base, _d(s.max_base))
            e, pr = p.employee, p.payroll_run
            rows.append({**R.emp_cols(e), 'nationality': str(e.emp_nationality or ''), 'scheme': s.name, 'authority': s.authority or '',
                         'period': _period_label(pr), 'run': pr.name or _period_label(pr), 'base': _m(base), 'employee_share': _m(v['employee']),
                         'employer_share': _m(v['employer']), 'total': _m(v['employee'] + v['employer']), **R.src(p)})
    rows.sort(key=lambda r: (r['scheme'], r['period'], r['employee']))
    cols = R.EMP_COLS + [('nationality', 'Nationality', 'text'), ('scheme', 'Scheme', 'text'), ('authority', 'Authority', 'text'),
                         ('period', 'Period', 'text'), ('run', 'Payroll run', 'text'), ('base', 'Contributory salary (AED)', 'number'),
                         ('employee_share', 'Employee share (AED)', 'number'), ('employer_share', 'Employer share (AED)', 'number'),
                         ('total', 'Total to pay (AED)', 'number')]
    return cols, rows, {'notes': ['From the payslip lines of each scheme\'s employee and employer components (Payroll › Statutory schemes). '
                                  'Contributory salary = the payslip\'s earnings of the scheme\'s base (basic; basic + housing + transport; gross), '
                                  'at most the wage ceiling. Group by Scheme for the totals to pay to each authority.']}


def _window(emp, pr):
    try:
        from PayrollPlus.monthly_inputs import payroll_window
        return payroll_window(date(pr.year, pr.month, 1), emp)
    except Exception:   # noqa: BLE001
        st = date(pr.year, pr.month, 1)
        return st, _R().month_end(st)


def r_lop_summary(run):
    R = _R()
    AC = M('calendars', 'AttendanceCalendar')
    rows = []
    for p in R._payslips(run, components=True):
        if (p.status or '').lower() == 'rejected':
            continue
        total = _d(p.total_working_days)
        paid = _d(p.days_worked)
        lop = max(D0, total - paid)
        e, pr = p.employee, p.payroll_run
        a, b = _window(e, pr)
        absent = unpaid = D0
        for x in AC.objects.filter(employee=e, date__range=(a, b), unpaid_fraction__gt=0).select_related('leave_type'):
            if x.status == 'Absent':
                absent += _d(x.unpaid_fraction)
            else:
                unpaid += _d(x.unpaid_fraction)
        other = max(D0, lop - absent - unpaid)
        lost = D0
        if lop > 0 and total > 0:
            for pc in p.components.all():
                c = pc.component
                if c.component_type == 'addition' and getattr(c, 'deduct_leave', False) and c.show_in_payslip:
                    amt = _d(pc.amount)
                    full = amt * total / paid if paid > 0 else _d(_full_amount(e, c))
                    lost += max(D0, full - amt)
        rows.append({**R.emp_cols(e), 'period': _period_label(pr), 'run': pr.name or _period_label(pr), 'window': f'{a:%d/%m/%Y} – {b:%d/%m/%Y}',
                     'payroll_days': float(total), 'paid_days': float(paid), 'lop_days': float(lop), 'unpaid_leave': float(unpaid),
                     'absent': float(absent), 'not_employed': float(other), 'lop_amount': _m(lost), **R.src(p),
                     '_cell': {'absent': R.drill('attendance', {'employee': R.full_name(e), 'status': 'Absent'}, **{'from': a, 'to': b}),
                               'unpaid_leave': R.drill('leave', {'employee': R.full_name(e)}, **{'from': a, 'to': b})}})
    cols = R.EMP_COLS + [('period', 'Period', 'text'), ('run', 'Payroll run', 'text'), ('window', 'Attendance period', 'text'),
                         ('payroll_days', 'Payroll days', 'number'), ('paid_days', 'Paid days', 'number'), ('lop_days', 'Days without pay', 'number'),
                         ('unpaid_leave', 'Unpaid leave days', 'number'), ('absent', 'Absent days', 'number'),
                         ('not_employed', 'Before joining / after leaving', 'number'), ('lop_amount', 'Pay lost (AED)', 'number')]
    return cols, rows, {'notes': ['Days without pay = payroll days − paid days of the payslip. Pay lost = the components that deduct leave '
                                  '(Payroll › Salary components › "Deduct leave"), full month against what the payslip paid.']}


def _full_amount(e, c):
    r = M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee=e, component=c, is_active=True).first()
    return r.amount if r else 0


INPUT_VARS = [('days_without_pay', 'Days without pay'), ('unpaid_leave_days', 'Unpaid leave days'), ('absent_days', 'Absent days'),
              ('late_count', 'Days late'), ('late_minutes', 'Minutes late'), ('early_count', 'Days left early'), ('early_minutes', 'Minutes early'),
              ('missing_punch_days', 'Missing punch days'), ('net_worked_hours', 'Hours worked'), ('ot_weighted_hours', 'Approved overtime (weighted h)'),
              ('ot_pending_hours', 'Overtime waiting approval (h)'), ('shift_days', 'Shift days'), ('night_shift_days', 'Night shift days'),
              ('on_call_days', 'On-call days')]


def r_payroll_inputs(run):
    """the figures of one payroll month (the month of the period's start)"""
    R = _R()
    from PayrollManagement.signals import get_formula_variables
    from PayrollPlus import monthly_inputs as MI
    m = (run.date_from or _zt_today()).replace(day=1)
    emps = [e for e in run.employees if not (e.emp_joined_date and e.emp_joined_date > R.month_end(m))]
    inputs_by_branch = {}
    codes, rows = [], []
    for e in emps:
        a, b = MI.payroll_window(m, e)
        try:
            v = get_formula_variables(e, a, b)
        except Exception as ex:   # noqa: BLE001
            v = {'_error': str(ex)[:120]}
        r = {**R.emp_cols(e), 'window': f'{a:%d/%m/%Y} – {b:%d/%m/%Y}'}
        for k, _l in INPUT_VARS:
            r[k] = float(_d(v.get(k)))
        bid = e.emp_branch_id_id
        if bid not in inputs_by_branch:
            try:
                g = MI.grid(m, bid)
                inputs_by_branch[bid] = ({x['employee_id']: x['values'] for x in g['rows']}, [(c['code'], c['name']) for c in g['columns']])
            except Exception:   # noqa: BLE001
                inputs_by_branch[bid] = ({}, [])
        vals, cs = inputs_by_branch[bid]
        for code, name in cs:
            if code not in [c for c, _n in codes]:
                codes.append((code, name))
            r['mi_' + code] = float(_d((vals.get(e.pk) or {}).get(code)))
        r['note'] = v.get('_error', '')
        r.update(R.src(e))
        rows.append(r)
    for r in rows:
        for code, _n in codes:
            r.setdefault('mi_' + code, 0)
    cols = R.EMP_COLS + [('window', 'Attendance period', 'text')] + [(k, l, 'number') for k, l in INPUT_VARS] + \
        [('mi_' + c, f'{n} (monthly input)', 'number') for c, n in codes] + [('note', 'Note', 'text')]
    from Translations.i18n import tr
    return cols, rows, {'notes': [tr('The payroll month {0} (the month of "From"): the figures the payroll formulas will read, per employee for the '
                                     'employee\'s own attendance period, and the monthly inputs entered. Check them before you run the payroll.', m.strftime('%m/%Y'))]}


def r_payroll_liability(run):
    R = _R()
    from PayrollPlus.payctl import held_map
    from PayrollPlus import run_payments as RP
    sch = [s for s in _schemes() if s.employee_component or s.employer_component]
    comp_side = {}
    for s in sch:
        if s.employee_component:
            comp_side[s.employee_component] = (s, 'employee')
        if s.employer_component:
            comp_side[s.employer_component] = (s, 'employer')
    allowed = set(run.all_employees.values_list('id', flat=True))
    rows = []
    for pr in _runs(run):
        if pr.status in ('rejected', 'cancelled'):
            continue
        slips = [p for p in pr.payslips.exclude(status='rejected').prefetch_related('components__component') if p.employee_id in allowed]
        if not slips:
            continue
        held = held_map([p.pk for p in slips])
        net = sum((max(_d(p.net_salary), D0) for p in slips), D0)
        hold = sum((min(held.get(p.pk, D0), max(_d(p.net_salary), D0)) for p in slips), D0)
        try:
            ls = [l for l in RP.lines(pr) if l['employee_id'] in allowed]
            paid = sum((_d(l['amount']) for l in ls if l['paid']), D0)
        except Exception:   # noqa: BLE001
            paid = D0
        if str(pr.status).lower() == 'paid':
            paid = net - hold
        base = {'period': _period_label(pr), 'run': pr.name or _period_label(pr), 'run_status': R.nice(pr.status), 'employees': len(slips)}
        rows.append({**base, 'item': 'Net salaries payable', 'payable_to': 'Employees (WPS / bank / cash)', 'amount': _m(net - hold),
                     'paid': _m(paid), 'outstanding': _m(net - hold - paid), **R.src(pr)})
        if hold:
            rows.append({**base, 'item': 'Salaries held', 'payable_to': 'Employees – when released', 'amount': _m(hold), 'paid': 0,
                         'outstanding': _m(hold), **R.src(pr)})
        per = {}
        for p in slips:
            for pc in p.components.all():
                hit = comp_side.get(pc.component.code or '')
                if hit:
                    s, side = hit
                    x = per.setdefault(s.pk, {'s': s, 'employee': D0, 'employer': D0})
                    x[side] += _d(pc.amount)
        for x in per.values():
            s = x['s']
            amt = x['employee'] + x['employer']
            rows.append({**base, 'item': f"{s.name} (employee {_m(x['employee']):,.2f} + employer {_m(x['employer']):,.2f})",
                         'payable_to': s.authority or s.name, 'amount': _m(amt), 'paid': None, 'outstanding': None, **R.src(pr)})
    cols = [('period', 'Period', 'text'), ('run', 'Payroll run', 'text'), ('run_status', 'Run status', 'text'), ('employees', 'Employees', 'number'),
            ('item', 'Liability', 'text'), ('payable_to', 'Payable to', 'text'), ('amount', 'Amount (AED)', 'number'),
            ('paid', 'Paid (AED)', 'number'), ('outstanding', 'Not paid yet (AED)', 'number')]
    return cols, rows, {'notes': ['Net salaries payable = net pay − salaries held; paid = the paid marks of Payroll run › Payments (all, once the '
                                  'run is Paid). Statutory schemes: what the payslips withheld plus the employer share – paid to the authority '
                                  'outside ZEO, so no paid column.']}


def r_payroll_journal(run):
    R = _R()
    try:
        JB = M('ZeoAccounting', 'JournalBatch')
    except LookupError:
        return [('note', 'Note', 'text')], [{'note': 'The Accounting app is not installed.'}]
    runs = {str(pr.pk): pr for pr in _runs(run)}
    rows = []
    batches = list(JB.objects.filter(Q(source='payroll', source_id__in=list(runs)) |
                                     Q(source='payroll_pay', source_id__regex=r'^(%s)-' % '|'.join(runs or ['0']))).prefetch_related('lines'))
    done = set()
    allowed_b = run.branches
    for bt in sorted(batches, key=lambda x: (x.date, x.pk)):
        rid = bt.source_id.split('-')[0]
        pr = runs.get(rid)
        done.add(rid)
        for ln in bt.lines.all().order_by('seq', 'pk'):
            if allowed_b is not None and ln.branch_id and ln.branch_id not in allowed_b:
                continue
            rows.append({'period': _period_label(pr) if pr else '', 'run': (pr.name or _period_label(pr)) if pr else rid,
                         'journal': bt.number or f'#{bt.pk}', 'kind': 'Payroll' if bt.source == 'payroll' else 'Salary payment', 'date': bt.date,
                         'status': R.nice(bt.status), 'account': ln.account, 'label': ln.label, 'debit': _m(ln.debit), 'credit': _m(ln.credit),
                         'branch': ln.branch, 'department': ln.department, 'employee': ln.employee, '_m': 'ZeoAccounting.JournalBatch', '_id': bt.pk})
    for rid, pr in runs.items():
        if rid not in done and pr.status in ('approved', 'paid'):
            rows.append({'period': _period_label(pr), 'run': pr.name or _period_label(pr), 'journal': '', 'kind': 'Payroll', 'date': None,
                         'status': 'No journal yet', 'account': '', 'label': 'Accounting › Journals › Build from payroll (or switch on "build the journal '
                         'when a payroll run is approved")', 'debit': 0, 'credit': 0, 'branch': '', 'department': '', 'employee': '', **R.src(pr)})
    cols = [('period', 'Period', 'text'), ('run', 'Payroll run', 'text'), ('journal', 'Journal', 'text'), ('kind', 'Kind', 'text'),
            ('date', 'Date', 'date'), ('status', 'Status', 'text'), ('account', 'Account', 'text'), ('label', 'Line', 'text'),
            ('debit', 'Debit (AED)', 'number'), ('credit', 'Credit (AED)', 'number'), ('branch', 'Branch', 'text'),
            ('department', 'Department', 'text'), ('employee', 'Employee', 'text')]
    return cols, rows, {'notes': ['The lines of the payroll journals (cost and Salaries payable) and salary payment journals of the runs in the period, '
                                  'as built in Accounting. Totals of Debit and Credit are equal for every journal.']}


def r_salary_holds(run):
    R = _R()
    try:
        SH = M('PayrollPlus', 'SalaryHold')
    except LookupError:
        return [('note', 'Note', 'text')], []
    emps = _emps(run)
    qs = SH.objects.filter(employee_id__in=list(emps))
    if run.date_from:
        qs = qs.filter(month__gte=run.date_from.replace(day=1))
    if run.date_to:
        qs = qs.filter(month__lte=run.date_to)
    rows = []
    for h in qs.order_by('month', 'employee_id'):
        e = emps.get(h.employee_id)
        kind = dict(h.KINDS).get(h.kind, h.kind)
        if h.kind != 'full' and h.value is not None:
            kind += f' ({h.value}{" %" if h.kind == "percent" else " AED"})'
        last = (h.history or [{}])[-1] if h.history else {}
        rows.append({**R.emp_cols(e), 'month': h.month.strftime('%b %Y'), 'kind': kind, 'reason': h.reason, 'status': dict(h.STATUS).get(h.status, h.status),
                     'held': _m(h.held_amount), 'released': _m(h.released_amount), 'still_held': _m(max(D0, _d(h.held_amount) - _d(h.released_amount)))
                     if h.status not in ('cancelled',) else 0, 'release_from': h.release_from, 'last_action': last.get('action', ''),
                     'by': last.get('by', ''), '_m': 'PayrollPlus.SalaryHold', '_id': h.pk})
    cols = R.EMP_COLS + [('month', 'Salary month', 'text'), ('kind', 'Held', 'text'), ('reason', 'Reason', 'text'), ('status', 'Status', 'text'),
                         ('held', 'Held (AED)', 'number'), ('released', 'Released (AED)', 'number'), ('still_held', 'Still held (AED)', 'number'),
                         ('release_from', 'Release in the run of', 'date'), ('last_action', 'Last action', 'text'), ('by', 'By', 'text')]
    return cols, rows


def _scheme_report(code):
    def fn(run):
        R = _R()
        from PayrollPlus import branch_schemes as BS
        years = sorted({(run.date_from or _zt_today()).year, (run.date_to or _zt_today()).year})
        emps = _emps(run)
        rows = []
        for y in years:
            d = BS.report(y, code, run.c)
            for x in d['rows']:
                e = emps.get(x['employee_id'])
                if e is None:
                    continue
                r = {**R.emp_cols(e), 'year': y, 'employee_total': x['employee_total'], 'employer_total': x['employer_total'],
                     'total': round(x['employee_total'] + x['employer_total'], 2), **R.src(e)}
                for mth in range(1, 13):
                    cell = x['months'].get(str(mth)) or {}
                    r[f'm{mth}'] = round((cell.get('employee') or 0) + (cell.get('employer') or 0), 2)
                if code == 'dews':
                    r['pre_dews_years'] = x.get('pre_dews_years', 0)
                    r['pre_dews_gratuity'] = x.get('pre_dews_gratuity', 0)
                rows.append(r)
        cols = R.EMP_COLS + [('year', 'Year', 'number')] + [(f'm{i}', date(2000, i, 1).strftime('%b') + ' (AED)', 'number') for i in range(1, 13)] + \
            [('employee_total', 'Employee share (AED)', 'number'), ('employer_total', 'Employer contribution (AED)', 'number'),
             ('total', 'Total (AED)', 'number')]
        if code == 'dews':
            cols += [('pre_dews_years', 'Service before DEWS (years)', 'number'), ('pre_dews_gratuity', 'Gratuity owed for that service (AED)', 'number')]
        return cols, rows, {'notes': ['From the payslips (not rejected) of the year, the branches set to the scheme in Payroll › Branch schemes. '
                                      'Months = employee share + employer contribution.' + (' The gratuity for the service before DEWS is as of today, '
                                                                                             'on today\'s basic.' if code == 'dews' else '')]}
    return fn


# ================================================================== leave & attendance
def r_leave_on_day(run):
    from Translations.i18n import tr as _tr
    R = _R()
    day = run.date_from or run.today
    LR = M('calendars', 'employee_leave_request')
    qs = LR.objects.filter(run.emp_q(), start_date__lte=day, end_date__gte=day, status__in=('approved', 'pending')).select_related(
        'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'leave_type')
    rows = []
    for lr in qs.order_by('employee__emp_dept_id__dept_name', 'employee__emp_code'):
        back = lr.end_date + timedelta(days=1)
        rows.append({**R.emp_cols(lr.employee), 'on': day, 'leave_type': lr.leave_type.name if lr.leave_type_id else '', 'from': lr.start_date, 'to': lr.end_date,
                     'days': lr.number_of_days, 'half_day': R.nice(bool(lr.dis_half_day)), 'status': 'Approved' if lr.status == 'approved' else 'Waiting for approval',
                     'back_on': back, 'days_left': (lr.end_date - day).days + 1, **R.src(lr)})
    n_all = run.employees.count()
    return R.EMP_COLS + [('on', 'On', 'date'), ('leave_type', 'Leave type', 'text'), ('from', 'From', 'date'), ('to', 'To', 'date'), ('days', 'Days', 'number'),
                         ('half_day', 'Half day', 'text'), ('status', 'Status', 'text'), ('back_on', 'Back on', 'date'),
                         ('days_left', 'Days left', 'number')], rows, \
        {'notes': [_tr('{0} of {1} employees are on approved leave on {2}; {3} more asked and are waiting. "From" picks the day; group by Department.',
                       len([r for r in rows if r['status'] == 'Approved']), n_all, day.strftime('%d/%m/%Y'), len([r for r in rows if r['status'] != 'Approved']))]}


def _shift_break(emp, day, cache):
    from AttendancePlus import engine as EN
    try:
        sh = EN.shift_info(emp, day)
    except Exception:   # noqa: BLE001
        sh = None
    return int((sh or {}).get('break_minutes') or 0)


def r_expected_hours(run):
    R = _R()
    try:
        AD = M('AttendancePlus', 'AttendanceDay')
    except LookupError:
        return [('note', 'Note', 'text')], []
    emps = _emps(run)
    per = defaultdict(lambda: {'planned_days': 0, 'expected': 0, 'worked': 0, 'ot': 0, 'late': 0, 'early': 0, 'absent': 0, 'leave': 0})
    brk = {}
    qs = AD.objects.filter(employee_id__in=list(emps), date__gte=run.date_from, date__lte=run.date_to)
    for d in qs.order_by('employee_id', 'date'):
        x = per[d.employee_id]
        if d.status == 'leave':
            x['leave'] += 1
            continue
        if d.status in ('holiday', 'weekly_off') or not (d.shift_start and d.shift_end):
            x['worked'] += d.worked_minutes or 0
            x['ot'] += (d.ot_minutes or 0) + (d.period_ot_minutes or 0)
            continue
        span = int((d.shift_end - d.shift_start).total_seconds() // 60)
        key = (d.employee_id, d.shift_id)
        if key not in brk:
            brk[key] = _shift_break(emps[d.employee_id], d.date, brk)
        exp = max(0, span - brk[key])
        if d.status == 'half_day':
            exp = exp // 2
        x['planned_days'] += 1
        x['expected'] += exp
        x['worked'] += d.worked_minutes or 0
        x['ot'] += (d.ot_minutes or 0) + (d.period_ot_minutes or 0)
        x['late'] += d.late_minutes or 0
        x['early'] += d.early_minutes or 0
        if d.status == 'absent':
            x['absent'] += 1
    rows = []
    for eid, x in per.items():
        e = emps[eid]
        h = lambda v: round(v / 60, 2)   # noqa: E731
        diff = x['worked'] - x['expected']
        rows.append({**R.emp_cols(e), 'planned_days': x['planned_days'], 'expected': h(x['expected']), 'worked': h(x['worked']),
                     'difference': h(diff), 'percent': round(x['worked'] * 100 / x['expected'], 1) if x['expected'] else None, 'ot': h(x['ot']),
                     'late': h(x['late']), 'early': h(x['early']), 'absent': x['absent'], 'leave': x['leave'], **R.src(e),
                     '_cell': {'worked': R.drill('attendance', {'employee': R.full_name(e)}, **{'from': run.date_from, 'to': run.date_to})}})
    rows.sort(key=lambda r: r['difference'])
    cols = R.EMP_COLS + [('planned_days', 'Planned shift days', 'number'), ('expected', 'Expected hours', 'number'), ('worked', 'Worked hours', 'number'),
                         ('difference', 'Difference (h)', 'number'), ('percent', 'Worked %', 'number'), ('ot', 'Overtime (h)', 'number'),
                         ('late', 'Late (h)', 'number'), ('early', 'Left early (h)', 'number'), ('absent', 'Absent days', 'number'),
                         ('leave', 'Leave days', 'number')]
    return cols, rows, {'notes': ['Expected = the planned shift hours less the shift break (half on a half day); leave days, holidays and weekly '
                                  'offs expect nothing – hours worked on them count as worked. From the daily attendance results.']}


def r_office_remote(run):
    R = _R()
    try:
        AD, PU, CR = M('AttendancePlus', 'AttendanceDay'), M('AttendancePlus', 'Punch'), M('AttendancePlus', 'CorrectionRequest')
    except LookupError:
        return [('note', 'Note', 'text')], []
    emps = _emps(run)
    ids = list(emps)
    days = defaultdict(set)
    for d in AD.objects.filter(employee_id__in=ids, date__gte=run.date_from, date__lte=run.date_to, status__in=('present', 'half_day', 'missing_punch')):
        days[d.employee_id].add(d.date)
    kind_of = {}
    for c in CR.objects.filter(employee_id__in=ids, date__gte=run.date_from, date__lte=run.date_to, status='approved', kind__in=('wfh', 'on_duty')):
        kind_of[(c.employee_id, c.date)] = c.kind
    office_src = {'biometric', 'kiosk', 'device', 'qr'}
    pmap = defaultdict(list)
    for p in PU.objects.filter(employee_id__in=ids, work_date__gte=run.date_from, work_date__lte=run.date_to, is_void=False).only(
            'employee_id', 'work_date', 'source', 'geofence_ok'):
        pmap[(p.employee_id, p.work_date)].append(p)
    rows = []
    for eid, ds in days.items():
        office = wfh = duty = outside = unknown = 0
        for dt in ds:
            k = kind_of.get((eid, dt))
            ps = pmap.get((eid, dt), [])
            if k == 'wfh':
                wfh += 1
            elif k == 'on_duty':
                duty += 1
            elif any(p.source in office_src or p.geofence_ok is True for p in ps):
                office += 1
            elif any(p.geofence_ok is False for p in ps):
                outside += 1
            else:
                unknown += 1
        n = len(ds)
        e = emps[eid]
        rows.append({**R.emp_cols(e), 'days': n, 'office': office, 'wfh': wfh, 'on_duty': duty, 'outside': outside, 'unknown': unknown,
                     'remote_pct': round((wfh + duty) * 100 / n, 1) if n else 0, **R.src(e)})
    rows.sort(key=lambda r: -r['remote_pct'])
    cols = R.EMP_COLS + [('days', 'Days worked', 'number'), ('office', 'In the office', 'number'), ('wfh', 'From home (approved)', 'number'),
                         ('on_duty', 'On duty outside', 'number'), ('outside', 'Punched outside the geofence', 'number'),
                         ('unknown', 'Not known (web / mobile, no location)', 'number'), ('remote_pct', 'Remote %', 'number')]
    return cols, rows, {'notes': ['Office = a punch at a device, kiosk, face terminal or QR, or inside the geofence; from home / on duty = an approved '
                                  'attendance correction of that kind; outside the geofence = a mobile / web punch outside every geofence.']}


# ================================================================== HR helpdesk
def _hours(a, b):
    return round((b - a).total_seconds() / 3600, 1) if a and b else None


def _tickets(run):
    T = M('ZeoHR', 'HRTicket')
    qs = T.objects.filter(run.emp_q()).select_related('category', 'employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id',
                                                      'employee__emp_ctgry_id', 'assigned_to')
    if run.date_from:
        qs = qs.filter(created_at__date__gte=run.date_from)
    if run.date_to:
        qs = qs.filter(created_at__date__lte=run.date_to)
    return qs.order_by('created_at')


def _on_time(done, due, now):
    if not due:
        return ''
    if done:
        return 'On time' if done <= due else 'Late'
    return 'Overdue' if now > due else 'Open – in time'


def r_helpdesk_sla(run):
    R = _R()
    from django.utils import timezone
    now = timezone.now()
    rows = []
    for t in _tickets(run):
        rows.append({**R.emp_cols(t.employee), 'number': t.number, 'category': t.category.name if t.category_id else '', 'priority': t.get_priority_display(),
                     'status': t.get_status_display(), 'assigned': (t.assigned_to.get_full_name() or t.assigned_to.username) if t.assigned_to_id else '',
                     'created': timezone.localtime(t.created_at).strftime('%d/%m/%Y %H:%M'), 'first_answer_h': _hours(t.created_at, t.first_response_at),
                     'answer_sla': _on_time(t.first_response_at, t.response_due, now), 'solved_h': _hours(t.created_at, t.resolved_at),
                     'solve_sla': _on_time(t.resolved_at, t.resolve_due, now), 'rating': t.rating, **R.src(t)})
    cols = [('number', 'Question', 'text')] + R.EMP_COLS + [('category', 'Category', 'text'), ('priority', 'Priority', 'text'), ('status', 'Status', 'text'),
                                                             ('assigned', 'Assigned to', 'text'), ('created', 'Asked', 'text'),
                                                             ('first_answer_h', 'First answer after (h)', 'number'), ('answer_sla', 'First answer', 'text'),
                                                             ('solved_h', 'Solved after (h)', 'number'), ('solve_sla', 'Solution', 'text'),
                                                             ('rating', 'Rating (1–5)', 'number')]
    return cols, rows, {'notes': ['Due times come from the category (first answer within / solved within, hours). Group by Category or Assigned to.']}


def r_helpdesk_trend(run):
    from django.utils import timezone
    now = timezone.now()
    per = defaultdict(lambda: {'received': 0, 'solved': 0, 'ans_on': 0, 'ans_n': 0, 'sol_on': 0, 'sol_n': 0, 'ans_h': [], 'sol_h': [], 'ratings': [],
                               'open': 0})
    for t in _tickets(run):
        k = timezone.localtime(t.created_at).strftime('%Y-%m')
        x = per[k]
        x['received'] += 1
        if t.resolved_at:
            x['solved'] += 1
            x['sol_h'].append(_hours(t.created_at, t.resolved_at))
        elif t.status not in ('cancelled',):
            x['open'] += 1
        if t.first_response_at:
            x['ans_h'].append(_hours(t.created_at, t.first_response_at))
        a = _on_time(t.first_response_at, t.response_due, now)
        if a in ('On time', 'Late', 'Overdue'):
            x['ans_n'] += 1
            x['ans_on'] += a == 'On time'
        s = _on_time(t.resolved_at, t.resolve_due, now)
        if s in ('On time', 'Late', 'Overdue'):
            x['sol_n'] += 1
            x['sol_on'] += s == 'On time'
        if t.rating:
            x['ratings'].append(t.rating)
    avg = lambda v: round(sum(v) / len(v), 1) if v else None   # noqa: E731
    rows = [{'month': datetime.strptime(k, '%Y-%m').strftime('%b %Y'), '_k': k, 'received': x['received'], 'solved': x['solved'], 'open': x['open'],
             'answer_on_time': round(x['ans_on'] * 100 / x['ans_n'], 1) if x['ans_n'] else None,
             'solved_on_time': round(x['sol_on'] * 100 / x['sol_n'], 1) if x['sol_n'] else None, 'avg_answer_h': avg(x['ans_h']),
             'avg_solve_h': avg(x['sol_h']), 'rating': avg(x['ratings'])} for k, x in sorted(per.items())]
    for r in rows:
        r.pop('_k')
    cols = [('month', 'Month', 'text'), ('received', 'Received', 'number'), ('solved', 'Solved', 'number'), ('open', 'Still open', 'number'),
            ('answer_on_time', 'First answer on time %', 'number'), ('solved_on_time', 'Solved on time %', 'number'),
            ('avg_answer_h', 'Average first answer (h)', 'number'), ('avg_solve_h', 'Average solution (h)', 'number'), ('rating', 'Average rating', 'number')]
    return cols, rows


# ================================================================== talent
BOX = {(3, 3): (9, 'Star'), (3, 2): (8, 'High performer'), (3, 1): (6, 'Solid professional'), (2, 3): (7, 'Future star'), (2, 2): (5, 'Core player'),
       (2, 1): (3, 'Effective'), (1, 3): (4, 'Rough diamond'), (1, 2): (2, 'Inconsistent'), (1, 1): (1, 'Talent risk')}


def perf_level(rating):
    """final rating 1 – 5 → 1 low (1 – 2), 2 medium (3), 3 high (4 – 5)"""
    if rating is None:
        return None
    return 1 if rating <= 2 else 2 if rating == 3 else 3


def nine_box_rows(emps):
    GS = M('PerformanceManagement', 'GoalSheet')
    TP = M('PerformanceManagement', 'TalentPotential')
    latest = {}
    for s in GS.objects.filter(employee_id__in=[e.id for e in emps], final_rating__isnull=False).select_related('cycle').order_by('employee_id', '-cycle__id'):
        latest.setdefault(s.employee_id, s)
    pot = {p.employee_id: p for p in TP.objects.filter(employee_id__in=[e.id for e in emps])}
    out = []
    for e in emps:
        s, p = latest.get(e.id), pot.get(e.id)
        pl = perf_level(s.final_rating) if s else None
        po = p.potential if p else None
        box = BOX.get((pl, po)) if pl and po else None
        out.append({'e': e, 'rating': s.final_rating if s else None, 'cycle': str(s.cycle) if s else '', 'performance': pl, 'potential': po,
                    'box': box[0] if box else None, 'box_label': box[1] if box else ('Rate the potential' if pl else 'No final rating yet'),
                    'note': p.note if p else '', 'set_on': p.set_on if p else None})
    return out


def r_nine_box(run):
    R = _R()
    L = {1: 'Low', 2: 'Medium', 3: 'High', None: ''}
    rows = [{**R.emp_cols(x['e']), 'cycle': x['cycle'], 'rating': x['rating'], 'performance': L[x['performance']], 'potential': L[x['potential']],
             'box': x['box'], 'box_label': x['box_label'], 'note': x['note'], 'set_on': x['set_on'], **R.src(x['e'])} for x in nine_box_rows(list(run.employees))]
    rows.sort(key=lambda r: -(r['box'] or 0))
    cols = R.EMP_COLS + [('cycle', 'Appraisal cycle', 'text'), ('rating', 'Final rating (1–5)', 'number'), ('performance', 'Performance', 'text'),
                         ('potential', 'Potential', 'text'), ('box', 'Box (1–9)', 'number'), ('box_label', 'Box', 'text'), ('note', 'Potential note', 'text'),
                         ('set_on', 'Potential set on', 'date')]
    return cols, rows, {'notes': ['Performance = the final rating of the latest appraisal (1–2 low, 3 medium, 4–5 high); potential = set by HR or the '
                                  'line manager on Performance › Nine-box grid. Box 9 = high performance and high potential.']}


def _fixed_salary(emp_ids):
    tot, basic = defaultdict(float), defaultdict(float)
    for r in M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee_id__in=emp_ids, is_active=True).select_related('component'):
        c = r.component
        if c.component_type != 'addition' or not c.show_in_payslip or c.component_value_type != 'fixed':
            continue
        tot[r.employee_id] += float(r.amount or 0)
    basic.update(_R().basic_by_employee(emp_ids))
    return tot, basic


def r_salary_bands(run):
    R = _R()
    emps = list(run.employees)
    ids = [e.id for e in emps]
    try:
        EO = M('OrgStructure', 'EmployeeOrg')
        org = {o.employee_id: o for o in EO.objects.filter(employee_id__in=ids).select_related('grade', 'job_position__grade')}
    except LookupError:
        org = {}
    tot, basic = _fixed_salary(ids)
    rows = []
    for e in emps:
        o = org.get(e.id)
        g = (o.grade if o and o.grade_id else None) or (o.job_position.grade if o and o.job_position_id and o.job_position.grade_id else None)
        lo, hi = (float(g.salary_min) if g and g.salary_min is not None else None), (float(g.salary_max) if g and g.salary_max is not None else None)
        sal = round(tot.get(e.id, 0), 2)
        mid = (lo + hi) / 2 if lo is not None and hi is not None else None
        pos = '' if g is None else ('No band on the grade' if lo is None and hi is None else
                                    'Below the band' if lo is not None and sal < lo else 'Above the band' if hi is not None and sal > hi else 'Within the band')
        pen = round((sal - lo) * 100 / (hi - lo), 1) if lo is not None and hi is not None and hi > lo else None
        rows.append({**R.emp_cols(e), 'grade': f'{g.code} – {g.name}' if g else 'No grade', 'band_min': lo, 'band_mid': round(mid, 2) if mid is not None else None,
                     'band_max': hi, 'basic': round(basic.get(e.id, 0), 2), 'salary': sal, 'compa': round(sal / mid, 2) if mid else None,
                     'penetration': pen, 'position': pos, **R.src(e)})
    cols = R.EMP_COLS + [('grade', 'Grade', 'text'), ('band_min', 'Band minimum (AED)', 'number'), ('band_mid', 'Band midpoint (AED)', 'number'),
                         ('band_max', 'Band maximum (AED)', 'number'), ('basic', 'Basic (AED)', 'number'), ('salary', 'Monthly salary (AED)', 'number'),
                         ('compa', 'Compa-ratio', 'number'), ('penetration', 'Range penetration %', 'number'), ('position', 'Position', 'text')]
    return cols, rows, {'notes': ['Monthly salary = the fixed earnings of the salary structure (as the grade\'s minimum and maximum, Settings › Grades). '
                                  'Compa-ratio = salary ÷ band midpoint (1.00 = the middle); range penetration = where the salary sits from minimum (0 %) '
                                  'to maximum (100 %). The grade comes from the employee\'s organisation details or job position.']}


def r_lms_progress(run):
    R = _R()
    try:
        OE, CM, MP = M('LearningPlus', 'OnlineEnrolment'), M('LearningPlus', 'CourseModule'), M('LearningPlus', 'ModuleProgress')
    except LookupError:
        return [('note', 'Note', 'text')], []
    Course = M('LearningManagement', 'Course')
    emps = _emps(run)
    qs = OE.objects.filter(employee_id__in=list(emps))
    if run.date_from:
        qs = qs.filter(enrolled_at__date__lte=run.date_to or _zt_today()).filter(Q(completed_at__isnull=True) | Q(completed_at__date__gte=run.date_from))
    enr = list(qs)
    courses = {c.pk: c for c in Course.objects.filter(pk__in={x.course_id for x in enr})}
    mods = defaultdict(list)
    for m in CM.objects.filter(course_id__in=list(courses), is_active=True):
        mods[m.course_id].append(m.pk)
    prog = {(p.employee_id, p.module_id): p for p in MP.objects.filter(employee_id__in=list(emps), module_id__in=[i for v in mods.values() for i in v])}
    rows = []
    for x in enr:
        e, c = emps.get(x.employee_id), courses.get(x.course_id)
        ms = mods.get(x.course_id, [])
        done = sum(1 for m in ms if getattr(prog.get((x.employee_id, m)), 'status', '') == 'completed')
        pct = 100 if x.status == 'completed' else (round(sum((getattr(prog.get((x.employee_id, m)), 'progress', 0) or 0) for m in ms) / len(ms), 1) if ms else 0)
        rows.append({**R.emp_cols(e), 'course': c.title if c else f'#{x.course_id}', 'type': R.nice(getattr(c, 'course_type', '')), 'enrolled': x.enrolled_at.date(),
                     'modules': len(ms), 'modules_done': done, 'progress': pct, 'score': x.score, 'pass_mark': getattr(c, 'pass_mark', None),
                     'status': dict(OE.STATUS).get(x.status, x.status), 'completed': x.completed_at.date() if x.completed_at else None,
                     '_m': 'LearningPlus.OnlineEnrolment', '_id': x.pk})
    cols = R.EMP_COLS + [('course', 'Course', 'text'), ('type', 'Type', 'text'), ('enrolled', 'Enrolled', 'date'), ('modules', 'Modules', 'number'),
                         ('modules_done', 'Modules done', 'number'), ('progress', 'Progress %', 'number'), ('score', 'Score', 'number'),
                         ('pass_mark', 'Pass mark', 'number'), ('status', 'Status', 'text'), ('completed', 'Completed', 'date')]
    return cols, rows


def r_training_ratings(run):
    R = _R()
    TS, PR = M('LearningManagement', 'TrainingSession'), M('LearningManagement', 'ParticipantResult')
    ids = set(run.all_employees.values_list('id', flat=True))
    sess = TS.objects.all()
    fld = 'start_date' if any(f.name == 'start_date' for f in TS._meta.fields) else None
    if fld and run.date_from:
        sess = sess.filter(**{f'{fld}__gte': run.date_from, f'{fld}__lte': run.date_to})
    rows = []
    for s in sess.select_related('course'):
        res = [r for r in PR.objects.filter(nomination__session=s).select_related('nomination') if r.nomination.employee_id in ids]
        if not res:
            continue
        fb = [r.feedback_rating for r in res if r.feedback_rating]
        tr_ = [r.trainer_rating for r in res if r.trainer_rating]
        passed = [r for r in res if r.passed is True]
        graded = [r for r in res if r.passed is not None]
        avg = lambda v: round(sum(v) / len(v), 2) if v else None   # noqa: E731
        rows.append({'session': str(s), 'course': s.course.title if getattr(s, 'course_id', None) else '', 'date': getattr(s, fld) if fld else None,
                     'participants': len(res), 'attended': sum(1 for r in res if r.attended), 'attendance': avg([r.attendance_percent for r in res]),
                     'pass_rate': round(len(passed) * 100 / len(graded), 1) if graded else None, 'pre': avg([r.pre_test_score for r in res if r.pre_test_score is not None]),
                     'post': avg([r.post_test_score for r in res if r.post_test_score is not None]), 'feedback': avg(fb), 'trainer': avg(tr_),
                     'answers': len(fb), **R.src(s)})
    cols = [('session', 'Session', 'text'), ('course', 'Course', 'text'), ('date', 'Starts', 'date'), ('participants', 'Participants', 'number'),
            ('attended', 'Attended', 'number'), ('attendance', 'Average attendance %', 'number'), ('pass_rate', 'Pass rate %', 'number'),
            ('pre', 'Average pre-test', 'number'), ('post', 'Average post-test', 'number'), ('feedback', 'Feedback rating (1–5)', 'number'),
            ('trainer', 'Trainer rating (1–5)', 'number'), ('answers', 'Feedback answers', 'number')]
    return cols, rows


def register(REPORTS, REPORT_MODELS):
    PAY = ['view_payslip', 'view_payrollrun']
    REPORTS.update({
        'social-security': ('Social security summary', 'Payroll', 'GPSSA, GOSI and the other statutory schemes: contributory salary, employee and employer share '
                            'per employee and month – what to pay each authority.', r_social_security, 'month', PAY),
        'lop-summary': ('Loss of pay summary', 'Payroll', 'Per payslip: payroll days, paid days, days without pay (unpaid leave, absence, before joining / after '
                        'leaving) and the pay lost.', r_lop_summary, 'month', PAY),
        'payroll-inputs': ('Data for payroll', 'Payroll', 'Before the payroll run: per employee the days without pay, absence, late, overtime, shifts and the '
                           'monthly inputs the payroll formulas will read for the month.', r_payroll_inputs, 'month', PAY),
        'payroll-liability': ('Payroll liability summary', 'Payroll', 'What the payroll runs owe: net salaries payable (paid / not paid), salaries held and '
                              'each statutory scheme – and to whom.', r_payroll_liability, 'month', PAY),
        'payroll-journal': ('Payroll journal', 'Payroll', 'The accounting lines (account, debit, credit, department) of the payroll and salary payment journals '
                            'of the runs.', r_payroll_journal, 'month', PAY),
        'salary-holds': ('Salary withhold', 'Payroll', 'Every salary hold of the period: how much was held, released and is still held, and why.',
                         r_salary_holds, 'year', PAY),
        'dews': ('DIFC DEWS contributions', 'Payroll', 'DEWS contributions of the year by employee and month, with the gratuity still owed for the service '
                 'before DEWS.', _scheme_report('dews'), 'year', PAY),
        'adpf': ('ADPF pension contributions', 'Payroll', 'Abu Dhabi Pension Fund contributions of the year by employee and month (employee share and employer '
                 'contribution).', _scheme_report('adpf'), 'year', PAY),
        'leave-on-day': ('Daily leave status', 'Leave & attendance', 'Who is on leave on a day – approved and waiting – with the leave type and the day back.',
                         r_leave_on_day, None, ['view_employee_leave_request']),
        'expected-hours': ('Expected vs worked hours', 'Leave & attendance', 'Per employee: the planned shift hours against the hours worked, the difference, '
                           'overtime, late and absence.', r_expected_hours, 'month', ['view_attendancereport']),
        'office-remote': ('Office vs remote', 'Leave & attendance', 'Per employee: days in the office, from home (approved), on duty outside and outside the '
                          'geofence.', r_office_remote, 'month', ['view_attendancereport']),
        'helpdesk-sla': ('HR helpdesk SLA', 'HR operations', 'Every HR helpdesk question with its first answer and solution against the category\'s hours.',
                         r_helpdesk_sla, 'month', ['view_hrticket']),
        'helpdesk-trend': ('HR helpdesk trends', 'HR operations', 'Per month: questions received and solved, answered and solved on time, average hours and '
                           'rating.', r_helpdesk_trend, 'year', ['view_hrticket']),
        'nine-box': ('Nine-box grid', 'Talent & projects', 'Performance (latest final rating) against potential, box 1 – 9 per employee.', r_nine_box, None,
                     ['view_goalsheet']),
        'salary-bands': ('Salary bands', 'Payroll', 'The monthly salary of each employee against the grade\'s minimum and maximum: compa-ratio and position in '
                         'the band.', r_salary_bands, None, PAY),
        'lms-progress': ('Learning progress', 'Talent & projects', 'Online courses per employee: modules done, progress %, score and status.', r_lms_progress,
                         'year', ['view_course']),
        'training-ratings': ('Training ratings', 'Talent & projects', 'Classroom sessions: attendance, pass rate, pre / post test and the feedback and trainer '
                             'ratings.', r_training_ratings, 'year', ['view_course']),
    })
    REPORT_MODELS.update({k: set() for k in ('social-security', 'lop-summary', 'payroll-inputs', 'payroll-liability', 'payroll-journal', 'salary-holds', 'dews',
                                              'adpf', 'leave-on-day', 'expected-hours', 'office-remote', 'helpdesk-sla', 'helpdesk-trend', 'nine-box',
                                              'salary-bands', 'lms-progress', 'training-ratings')})
