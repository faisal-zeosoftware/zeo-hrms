"""v1.73.22 (pending item 153) – India payroll by the state of the employee's branch.

Professional tax (PT) and the Labour Welfare Fund (LWF) are state laws: the branch's state (Settings › Branches › State) picks the
slabs. ZEO ships the states whose figures two public sources gave alike (sources in law_reference); for any other state – or
when a state changes its figures – edit the rows in Payroll › Statutory schemes (the scheme's "slabs" list) or add a version
from a date.

Each row of a state-wise scheme's slabs:
  {"state": "MH", "to": 7500, "employee": 0, "employer": 0,           – monthly salary up to "to" (null = above)
   "gender": "F",                                                    – only for women (Maharashtra)
   "last_month": 300, "last_month_no": 2,                            – a different amount in one month (Maharashtra February)
   "cycle": "half",  "months": [9, 3]}                               – deducted only in these months, on 6 × the monthly salary
                                                                       (cycle "half") or 12 × (cycle "year"); default monthly
Statutory bonus (Code on Wages 2019 / Payment of Bonus Act 1965): employees whose basic + DA is at most ₹ 21,000 a month earn
8.33 % (minimum) to 20 % of min(basic + DA, the bonus ceiling ₹ 7,000 or the state minimum wage if higher); ZEO books it each
month as an employer provision (hidden on the payslip) at the scheme's employer rate. The yearly statement (Reports › Statutory
bonus) adds the days worked (at least 30 in the year) and the amount to pay within 8 months of the year end.
Minimum wage: Payroll › Legal rates › key "in_min_wage:<STATE>" (monthly, from a date) – the bonus ceiling uses it, and the
report "Minimum wage check" lists every employee whose basic + DA is below the minimum of his branch's state.
"""
import re
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps

M = apps.get_model
Q2 = Decimal('0.01')

STATES = {
    'AN': 'Andaman and Nicobar Islands', 'AP': 'Andhra Pradesh', 'AR': 'Arunachal Pradesh', 'AS': 'Assam', 'BR': 'Bihar', 'CH': 'Chandigarh',
    'CG': 'Chhattisgarh', 'DH': 'Dadra and Nagar Haveli and Daman and Diu', 'DL': 'Delhi', 'GA': 'Goa', 'GJ': 'Gujarat', 'HR': 'Haryana',
    'HP': 'Himachal Pradesh', 'JK': 'Jammu and Kashmir', 'JH': 'Jharkhand', 'KA': 'Karnataka', 'KL': 'Kerala', 'LA': 'Ladakh', 'LD': 'Lakshadweep',
    'MP': 'Madhya Pradesh', 'MH': 'Maharashtra', 'MN': 'Manipur', 'ML': 'Meghalaya', 'MZ': 'Mizoram', 'NL': 'Nagaland', 'OD': 'Odisha',
    'PY': 'Puducherry', 'PB': 'Punjab', 'RJ': 'Rajasthan', 'SK': 'Sikkim', 'TN': 'Tamil Nadu', 'TS': 'Telangana', 'TR': 'Tripura',
    'UP': 'Uttar Pradesh', 'UK': 'Uttarakhand', 'WB': 'West Bengal'}
ALIASES = {'ORISSA': 'OD', 'OR': 'OD', 'TG': 'TS', 'UTTARANCHAL': 'UK', 'UA': 'UK', 'PONDICHERRY': 'PY', 'NCT OF DELHI': 'DL', 'NEW DELHI': 'DL',
           'BOMBAY': 'MH', 'MUMBAI': 'MH', 'BANGALORE': 'KA', 'BENGALURU': 'KA', 'CHENNAI': 'TN', 'MADRAS': 'TN', 'HYDERABAD': 'TS', 'KOLKATA': 'WB'}
BY_NAME = {v.upper(): k for k, v in STATES.items()}


def state_code(text):
    """'Maharashtra' / 'MH' / 'maharashtra state' → 'MH'; '' when not an Indian state."""
    t = re.sub(r'\s+', ' ', str(text or '')).strip().upper().replace('&', 'AND')
    if not t:
        return ''
    if t in STATES:
        return t
    if t in BY_NAME:
        return BY_NAME[t]
    if t in ALIASES:
        return ALIASES[t]
    t2 = re.sub(r'\b(STATE|UNION TERRITORY|UT|OF)\b', ' ', t)
    t2 = re.sub(r'\s+', ' ', t2).strip()
    return BY_NAME.get(t2) or ALIASES.get(t2) or ''


def employee_state(employee):
    br = getattr(employee, 'emp_branch_id', None)
    try:
        st = getattr(br, 'br_state_id', None)
        return state_code(getattr(st, 'state_name', '') if st is not None else '')
    except Exception:   # noqa: BLE001
        return ''


def _d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:   # noqa: BLE001
        return Decimal('0')


def _female(employee):
    g = str(getattr(employee, 'emp_gender', '') or '').strip().upper()
    return g in ('F', 'FEMALE', 'W', 'WOMAN')


def _cycle_factor(cycle):
    return {'half': 6, 'year': 12}.get(cycle or '', 1)


def state_rows(scheme, st):
    return [r for r in (scheme.slabs or []) if str(r.get('state', '')).upper() == st]


def is_state_wise(scheme):
    return any(isinstance(r, dict) and r.get('state') for r in (scheme.slabs or []))


def slab_for(scheme, employee, monthly, on):
    """(employee, employer) of a state-wise scheme in the month of `on` – 0 outside the deduction months."""
    st = employee_state(employee)
    rows = state_rows(scheme, st)
    if not rows:
        return Decimal('0'), Decimal('0')
    fem = _female(employee)
    g_rows = [r for r in rows if str(r.get('gender', '')).upper() == ('F' if fem else 'M')]
    if g_rows:
        rows = g_rows
    else:
        rows = [r for r in rows if not r.get('gender')]
    if not rows:
        return Decimal('0'), Decimal('0')
    months = rows[0].get('months')
    if months and on.month not in [int(m) for m in months]:
        return Decimal('0'), Decimal('0')
    base = monthly * _cycle_factor(rows[0].get('cycle'))
    for r in rows:
        to = r.get('to')
        if to in (None, '') or base <= _d(to):
            emp, er = _d(r.get('employee')), _d(r.get('employer'))
            if r.get('last_month') not in (None, '') and int(r.get('last_month_no') or 0) == on.month:
                emp = _d(r.get('last_month'))
            return emp, er
    return Decimal('0'), Decimal('0')


def min_wage(st, on):
    """the monthly minimum wage of the state in force on `on` (Payroll › Legal rates, key in_min_wage:<STATE>), or None"""
    if not st:
        return None
    try:
        row = M('PayrollPlus', 'LegalRate').objects.filter(key=f'in_min_wage:{st}', effective_from__lte=on).order_by('-effective_from').first()
        return _d(row.value) if row else None
    except Exception:   # noqa: BLE001
        return None


BONUS_ELIGIBLE = Decimal('21000')
BONUS_CEILING = Decimal('7000')


def bonus_month(scheme, employee, basic_da, on):
    """(base, employee 0, employer provision) of the statutory bonus for one month."""
    if basic_da <= 0 or basic_da > _d(scheme.eligible_gross_max or BONUS_ELIGIBLE):
        return Decimal('0'), Decimal('0'), Decimal('0')
    mw = min_wage(employee_state(employee), on)
    ceiling = max(_d(scheme.max_base or BONUS_CEILING), mw or Decimal('0'))
    base = min(basic_da, ceiling)
    rate = _d(scheme.employer_rate) or Decimal('8.33')
    return base.quantize(Q2), Decimal('0.00'), (base * rate / 100).quantize(Q2, ROUND_HALF_UP)


def clean_rows(rows):
    """v1.73.22 the slab rows as saved: a state name or code → its code; gender M / F; months 1–12; cycle half / year.
    Raises ValueError (the message for the screen) on a row that cannot work."""
    out = []
    for i, r in enumerate(rows or [], 1):
        r = {k: v for k, v in dict(r).items() if not str(k).startswith('_') and (v not in ('', None) or k == 'to')}
        if r.get('to') == '':
            r['to'] = None
        if r.get('state'):
            st = state_code(r['state'])
            if not st:
                raise ValueError(f'Row {i}: "{r["state"]}" is not a state of India (use its name or code, e.g. MH).')
            r['state'] = st
        if r.get('gender') not in (None, 'M', 'F'):
            raise ValueError(f'Row {i}: "for" is everyone, men or women.')
        if r.get('months') is not None:
            ms = r['months'] if isinstance(r['months'], list) else re.split(r'[^0-9]+', str(r['months']))
            try:
                ms = sorted({int(m) for m in ms if str(m).strip()})
            except ValueError:
                raise ValueError(f'Row {i}: months are numbers 1 – 12.')
            if any(m < 1 or m > 12 for m in ms):
                raise ValueError(f'Row {i}: months are numbers 1 – 12.')
            r['months'] = ms or None
            if not ms:
                r.pop('months')
        if r.get('cycle') not in (None, 'half', 'year'):
            raise ValueError(f'Row {i}: the salary of the month, 6 months or 12 months.')
        if r.get('last_month_no') is not None and not (1 <= int(r['last_month_no']) <= 12):
            raise ValueError(f'Row {i}: the month of the other amount is 1 – 12.')
        for k in ('to', 'employee', 'employer', 'rate', 'last_month'):
            if r.get(k) not in (None, '') and _d(r[k]) < 0:
                raise ValueError(f'Row {i}: amounts cannot be negative.')
        out.append(r)
    return out


# ------------------------------------------------------------------ reports (Reports › Payroll, India companies)
def _fy(today):
    """the Indian accounting year (1 April – 31 March) that holds `today`"""
    import datetime
    y = today.year if today.month >= 4 else today.year - 1
    return datetime.date(y, 4, 1), datetime.date(y + 1, 3, 31)


def report_bonus(run):
    """Statutory bonus statement: per employee of the accounting year (default: April – March of today): the months paid, days
    worked, the bonus wage of each month (basic + DA up to the ceiling, months above ₹ 21,000 left out), the bonus at the
    company's rate (the "Statutory bonus provision" scheme, 8.33 % – 20 %) and at the 8.33 % minimum, and the last day to pay
    (8 months after the year end). Fewer than 30 days worked: not eligible."""
    import datetime
    from collections import defaultdict
    from DashboardManagement.reports import EMP_COLS, emp_cols
    frm, to = (run.date_from, run.date_to) if run.date_from and run.date_to else _fy(run.today)
    sc = M('PayrollPlus', 'StatutoryScheme').objects.filter(code='bonus').first()
    rate = _d(getattr(sc, 'employer_rate', None)) or Decimal('8.33')
    elig_max = _d(getattr(sc, 'eligible_gross_max', None) or BONUS_ELIGIBLE)
    ceiling0 = _d(getattr(sc, 'max_base', None) or BONUS_CEILING)
    PS, PC = M('PayrollManagement', 'Payslip'), M('PayrollManagement', 'PayslipComponent')
    months = []
    d = frm.replace(day=1)
    while d <= to:
        months.append((d.year, d.month))
        d = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    slips = PS.objects.filter(run.emp_q()).exclude(status='rejected').select_related('payroll_run')
    slips = [p for p in slips if (p.payroll_run.year, p.payroll_run.month) in months]
    wage = defaultdict(lambda: defaultdict(Decimal))
    days = defaultdict(int)
    for p in slips:
        days[p.employee_id] += int(p.days_worked or 0)
    ids = [p.pk for p in slips]
    by_slip = {p.pk: p for p in slips}
    for c in PC.objects.filter(payslip_id__in=ids).select_related('component'):
        comp = c.component
        if comp.component_type != 'addition':
            continue
        name = (comp.name or '').strip().lower()
        if comp.payroll_category == 'basic' or name in ('da', 'dearness allowance') or 'dearness' in name:
            p = by_slip[c.payslip_id]
            wage[p.employee_id][(p.payroll_run.year, p.payroll_run.month)] += _d(c.amount)
    rows = []
    due = (to + datetime.timedelta(days=1)).replace(day=1)
    for _ in range(8):
        due = (due.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    due = due - datetime.timedelta(days=1)
    emps = {e.pk: e for e in run.all_employees.filter(pk__in=list(wage.keys()) + list(days.keys()))}
    for eid, e in emps.items():
        st = employee_state(e)
        bw, elig_months = Decimal('0'), 0
        for (y, m), amt in sorted(wage[eid].items()):
            if amt <= 0 or amt > elig_max:
                continue
            mw = min_wage(st, datetime.date(y, m, 1))
            bw += min(amt, max(ceiling0, mw or Decimal('0')))
            elig_months += 1
        ok = days[eid] >= 30 and elig_months > 0
        rows.append({**emp_cols(e), 'state': STATES.get(st, ''), 'months': elig_months, 'days': days[eid], 'bonus_wage': float(bw.quantize(Q2)),
                     'rate': float(rate), 'bonus': float((bw * rate / 100).quantize(Q2, ROUND_HALF_UP)) if ok else 0.0,
                     'minimum': float(max(bw * Decimal('8.33') / 100, Decimal('100')).quantize(Q2, ROUND_HALF_UP)) if ok else 0.0,
                     'eligible': 'Yes' if ok else ('No – under 30 days' if elig_months else 'No – above ₹ 21,000'), 'pay_by': due})
    cols = EMP_COLS + [('state', 'State', 'text'), ('months', 'Eligible months', 'number'), ('days', 'Days worked', 'number'),
                       ('bonus_wage', 'Bonus wage (₹)', 'number'), ('rate', 'Rate %', 'number'), ('bonus', 'Bonus (₹)', 'number'),
                       ('minimum', 'Minimum 8.33 % (₹)', 'number'), ('eligible', 'Eligible', 'text'), ('pay_by', 'Pay by', 'date')]
    return cols, rows, {'no_totals': ['rate', 'months'], 'notes': [
        f'Accounting year {frm:%d/%m/%Y} – {to:%d/%m/%Y}: basic + DA from the payslips; months above ₹ {elig_max:,.0f} are not bonus wages; '
        f'each month counts up to ₹ {ceiling0:,.0f} or the state minimum wage if higher (Payroll › Legal rates, in_min_wage:<state>). '
        'At least 30 days worked in the year. Pay within 8 months of the year end (Code on Wages 2019, was the Payment of Bonus Act 1965).']}


def report_min_wage(run):
    """Every active employee whose basic + DA is below the monthly minimum wage of his branch's state (Payroll › Legal rates,
    key in_min_wage:<STATE>, in force today); employees of states without a minimum entered are listed as "no minimum entered"."""
    from DashboardManagement.reports import EMP_COLS, emp_cols
    from PayrollPlus.statutory import _rows, base_amount
    rows = []
    for e in run.employees:
        st = employee_state(e)
        mw = min_wage(st, run.today)
        bd = base_amount(_rows(e), 'basic_da')
        if mw is not None and bd >= mw:
            continue
        rows.append({**emp_cols(e), 'state': STATES.get(st, '') or '–', 'basic_da': float(bd), 'minimum': float(mw) if mw is not None else None,
                     'short': float(mw - bd) if mw is not None else None,
                     'status': ('Below the minimum wage' if mw is not None else ('No minimum entered for the state' if st else 'The branch has no state'))})
    cols = EMP_COLS + [('state', 'State', 'text'), ('basic_da', 'Basic + DA (₹)', 'number'), ('minimum', 'Minimum wage (₹)', 'number'),
                       ('short', 'Short by (₹)', 'number'), ('status', 'Check', 'text')]
    return cols, rows, {'no_totals': ['minimum'], 'notes': [
        'The monthly minimum wage of each state: Payroll › Legal rates, key in_min_wage:<state code> (e.g. in_min_wage:KA) from the date it applies. '
        'Only employees below it – or whose state has none entered – are listed.']}
