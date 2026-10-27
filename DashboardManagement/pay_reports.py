"""
v1.16.0 – payroll reports of the report centre.

  payroll-register     Payroll register            one row per payslip, every salary component as a column, gross, deductions, net
  payroll-department   Payroll by department       branch / department subtotals of the register (each line opens its payslips)
  payroll-components   Payroll summary by component  component totals per month with the number of employees
  bank-transfer        Bank / WPS transfer list    net pay per employee with bank, IBAN, routing code; cash when no bank account
  payroll-mom          Month-over-month variance   per employee: gross and net this month against the month before, and why
  payslip-status       Payslip status              every payslip with its status, confirmation, e-mail and PDF

Net pay everywhere = the payslip's net salary, or additions − deductions where the net was never stored (same rule as the
dashboard payroll card, v1.15.1). Filters: period, branch (top bar), department, category, employee, status / run.
"""
from collections import defaultdict
from datetime import date

from .reports import EMP_COLS, M, _payslips, drill, emp_cols, full_name, money, month_end, nice, src

FILTERS = ['department', 'category', 'employee', 'run', 'run_status', 'status']


def net_of(p):
    """The payslip's net pay (net_salary, or additions − deductions when no net was stored)."""
    if p.net_salary is not None:
        return money(p.net_salary)
    return round(money(p.total_additions) - money(p.total_deductions), 2)


def _period(pr):
    return date(pr.year, pr.month, 1).strftime('%b %Y')


def _lines(p):
    """[(name, type, category, amount)] of a payslip (deductions as positive amounts)."""
    out = []
    for c in p.components.all():
        comp = c.component
        # v1.62.0 payroll (audit P1-3): a component hidden from the payslip is not in its totals (calculate_payslip_totals) –
        # it is reported as an employer-side line ("others"), never as pay
        t = comp.component_type if comp.show_in_payslip else 'others'
        out.append((comp.name, t, comp.payroll_category or '', money(c.amount)))
    return out


def _order(names, kinds):
    adds = sorted([n for n in names if kinds[n] not in ('deduction', 'others')], key=lambda n: (n.lower() not in ('basic', 'basic salary'), n.lower()))
    deds = sorted([n for n in names if kinds[n] == 'deduction'], key=str.lower)
    return adds, deds


def _employer(names, kinds):
    """v1.62.0 payroll (audit P1-3): employer contributions (type "others", e.g. GPSSA employer share) are not pay – shown after net."""
    return sorted([n for n in names if kinds[n] == 'others'], key=str.lower)


def r_payroll_register(run):
    slips = _payslips(run, components=True)
    kinds, names = {}, []
    per_slip = []
    for p in slips:
        lines = _lines(p)
        for n, t, _c, _a in lines:
            if n not in kinds:
                kinds[n] = t
                names.append(n)
        per_slip.append((p, lines))
    adds, deds = _order(names, kinds)
    rows = []
    for p, lines in per_slip:
        pr = p.payroll_run
        r = {**emp_cols(p.employee), 'run': pr.name or _period(pr), 'period': _period(pr), 'run_status': nice(pr.status), 'status': nice(p.status),
             'working_days': p.total_working_days, 'days_worked': p.days_worked}
        for n in names:   # v1.62.0 payroll: also the employer contribution columns
            r['c_' + n] = 0
        for n, t, _c, a in lines:
            r['c_' + n] = round(r['c_' + n] + a, 2)
        r.update({'gross': money(p.gross_salary) if p.gross_salary is not None else money(p.total_additions), 'additions': money(p.total_additions),
                  'deductions': money(p.total_deductions), 'arrears': money(p.arrears), 'net': net_of(p), **src(p),
                  '_cell': {'run': src(pr), 'period': src(pr)}})
        rows.append(r)
    cols = EMP_COLS + [('run', 'Payroll run', 'text'), ('period', 'Period', 'text'), ('run_status', 'Run status', 'text'), ('status', 'Payslip status', 'text'),
                       ('working_days', 'Working days', 'number'), ('days_worked', 'Days worked', 'number')] + \
        [('c_' + n, f'{n} (AED)', 'number') for n in adds] + [('gross', 'Gross (AED)', 'number')] + \
        [('c_' + n, f'{n} – deduction (AED)', 'number') for n in deds] + \
        [('deductions', 'Total deductions (AED)', 'number'), ('arrears', 'Arrears (AED)', 'number'), ('net', 'Net pay (AED)', 'number')] + \
        [('c_' + n, f'{n} – employer, not in net pay (AED)', 'number') for n in _employer(names, kinds)]   # v1.62.0 payroll (P1-3)
    for r in rows:
        for n in _employer(names, kinds):
            r.setdefault('c_' + n, 0)
    return cols, rows, {'filters': FILTERS, 'notes': ['Group by Branch or Department in the list bar for subtotals, or open "Payroll by department". '
                                                      'Net pay = net salary of the payslip (additions − deductions where no net was stored).']}


def r_payroll_department(run):
    slips = _payslips(run, components=True)
    kinds, names, groups = {}, [], {}
    for p in slips:
        pr = p.payroll_run
        e = p.employee
        k = (date(pr.year, pr.month, 1), e.emp_branch_id.branch_name if e.emp_branch_id_id else '', e.emp_dept_id.dept_name if e.emp_dept_id_id else '')
        g = groups.setdefault(k, {'emps': set(), 'c': defaultdict(float), 'gross': 0.0, 'deductions': 0.0, 'net': 0.0})
        g['emps'].add(p.employee_id)
        for n, t, _c, a in _lines(p):
            if n not in kinds:
                kinds[n] = t
                names.append(n)
            g['c'][n] += a
        g['gross'] += money(p.gross_salary) if p.gross_salary is not None else money(p.total_additions)
        g['deductions'] += money(p.total_deductions)
        g['net'] += net_of(p)
    adds, deds = _order(names, kinds)
    rows = []
    for k in sorted(groups):
        g = groups[k]
        per = {'from': k[0], 'to': month_end(k[0])}
        f = {'branch': k[1], 'department': k[2]}   # the month is the drill's period (a month name is translated in the rows)
        r = {'period': k[0].strftime('%b %Y'), 'branch': k[1], 'department': k[2], 'employees': len(g['emps']),
             'gross': round(g['gross'], 2), 'deductions': round(g['deductions'], 2), 'net': round(g['net'], 2),
             '_drill': drill('payroll-register', f, **per), '_cell': {}}
        for n in adds + deds:
            r['c_' + n] = round(g['c'].get(n, 0), 2)
            r['_cell']['c_' + n] = dict(drill('payroll-register', f, **per), nz='c_' + n)
        rows.append(r)
    cols = [('period', 'Period', 'text'), ('branch', 'Branch', 'text'), ('department', 'Department', 'text'), ('employees', 'Employees paid', 'number')] + \
        [('c_' + n, f'{n} (AED)', 'number') for n in adds] + [('gross', 'Gross (AED)', 'number')] + \
        [('c_' + n, f'{n} – deduction (AED)', 'number') for n in deds] + [('deductions', 'Total deductions (AED)', 'number'), ('net', 'Net pay (AED)', 'number')] + \
        [('c_' + n, f'{n} – employer, not in net pay (AED)', 'number') for n in _employer(names, kinds)]   # v1.62.0 payroll (P1-3)
    for r in rows:
        for n in _employer(names, kinds):
            r.setdefault('c_' + n, 0)
    return cols, rows, {'filters': ['branch', 'department', 'period'], 'notes': ['One line per month, branch and department; the totals row is the payroll total. Select a line for its payslips.']}


def r_payroll_components(run):
    slips = _payslips(run, components=True)
    g = {}
    for p in slips:
        pr = p.payroll_run
        m0 = date(pr.year, pr.month, 1)
        for n, t, c, a in _lines(p):
            k = (m0, n)
            x = g.setdefault(k, {'type': t, 'category': c, 'emps': set(), 'amount': 0.0, 'lines': 0})
            x['emps'].add(p.employee_id)
            x['amount'] += a
            x['lines'] += 1
    rows = []
    for (m0, n), x in sorted(g.items(), key=lambda i: (i[0][0], i[1]['type'] == 'deduction', i[0][1].lower())):
        rows.append({'period': m0.strftime('%b %Y'), 'component': n, 'type': 'Employer contribution' if x['type'] == 'others' else nice(x['type']), 'category': nice(x['category']),
                     'employees': len(x['emps']), 'amount': round(x['amount'], 2), 'signed': round(0 if x['type'] == 'others' else -x['amount'] if x['type'] == 'deduction' else x['amount'], 2),   # v1.62.0 payroll (P1-3)
                     '_drill': dict(drill('payroll-register', None, **{'from': m0, 'to': month_end(m0)}), nz='c_' + n)})
    return [('period', 'Period', 'text'), ('component', 'Component', 'text'), ('type', 'Type', 'text'), ('category', 'Payroll category', 'text'),
            ('employees', 'Employees', 'number'), ('amount', 'Amount (AED)', 'number'), ('signed', 'Effect on net pay (AED)', 'number')], rows, \
        {'filters': ['period', 'type', 'component'], 'no_totals': ['amount', 'employees'],
         'notes': ['Effect on net pay: additions positive, deductions negative, employer contributions 0 – its total is the net pay of the period. Select a line for the payslips with that component.']}


def r_bank_transfer(run):
    banks = {}
    for b in M('EmpManagement', 'EmployeeBankDetail').objects.filter(run.emp_q(), is_active=True).order_by('id'):
        banks[b.employee_id] = b
    rows = []
    for p in _payslips(run):
        pr = p.payroll_run
        if pr.status == 'pending' or (p.status or '').lower() == 'rejected':
            continue
        e, b = p.employee, banks.get(p.employee_id)
        net = net_of(p)
        if net <= 0:
            continue
        method = 'Bank transfer' if b and (b.iban_number or b.account_number) else 'Cash / cheque'
        check = 'OK'
        if method == 'Bank transfer' and not b.iban_number:
            check = 'Missing IBAN'
        elif method == 'Bank transfer' and not b.route_code:
            check = 'Missing routing code'
        elif method == 'Bank transfer' and not e.person_id:
            check = 'Missing Person ID (WPS)'
        rows.append({**emp_cols(e), 'run': pr.name or _period(pr), 'period': _period(pr), 'payment_date': pr.payment_date or '',
                     'method': method, 'bank': (b.bank_name if b else '') or '', 'account': (b.account_number if b else '') or '',
                     'iban': (b.iban_number if b else '') or '', 'routing': (b.route_code if b else '') or '', 'person_id': e.person_id or '',
                     'amount': net, 'check': check, **src(p), '_cell': {k: src(b) for k in ('bank', 'account', 'iban', 'routing')} if b else {}})
    return EMP_COLS + [('run', 'Payroll run', 'text'), ('period', 'Period', 'text'), ('payment_date', 'Payment date', 'date'), ('method', 'Payment method', 'text'),
                       ('bank', 'Bank', 'text'), ('account', 'Account number', 'text'), ('iban', 'IBAN', 'text'), ('routing', 'Routing code', 'text'),
                       ('person_id', 'Person ID (MOHRE)', 'text'), ('amount', 'Net pay to transfer (AED)', 'number'), ('check', 'Check', 'text')], rows, \
        {'filters': ['department', 'category', 'employee', 'run', 'method', 'check'],
         'notes': ['Processed, approved and paid runs only; rejected payslips and payslips with no net pay are left out. Group by Bank for the bank totals.']}


def r_payroll_mom(run):
    """Per employee: gross and net of the last month of the period against the month before."""
    import copy
    wide = copy.copy(run)
    wide.date_from, wide.date_to = date(1900, 1, 1), run.date_to
    by = defaultdict(dict)
    emps = {}
    for p in _payslips(wide, components=True):
        if p.payroll_run.status == 'pending':
            continue
        k = (p.payroll_run.year, p.payroll_run.month)
        emps[p.employee_id] = p.employee
        by[k][p.employee_id] = p
    periods = sorted(k for k in by if date(k[0], k[1], 1) <= run.date_to)
    cur = next((k for k in reversed(periods) if month_end(date(k[0], k[1], 1)) >= run.date_from), None)
    if cur is None:
        return EMP_COLS, [], {'notes': ['No processed payroll in the period.']}
    prev = next((k for k in reversed(periods) if k < cur), None)
    cl = date(cur[0], cur[1], 1).strftime('%b %Y')
    pl = date(prev[0], prev[1], 1).strftime('%b %Y') if prev else '—'
    rows = []
    ids = set(by[cur]) | (set(by[prev]) if prev else set())
    for eid in sorted(ids, key=lambda i: emps[i].emp_code):
        a, b = (by[prev].get(eid) if prev else None), by[cur].get(eid)
        ga, gb = (money(a.gross_salary) if a else 0), (money(b.gross_salary) if b else 0)
        na, nb = (net_of(a) if a else 0), (net_of(b) if b else 0)
        why = []
        if a is None:
            why.append('First payroll')
        elif b is None:
            why.append('Not paid this month')
        else:
            la = defaultdict(float)
            lb = defaultdict(float)
            for n, t, _c, x in _lines(a):
                la[n] += x if t != 'deduction' else -x
            for n, t, _c, x in _lines(b):
                lb[n] += x if t != 'deduction' else -x
            for n in sorted(set(la) | set(lb)):
                d = round(lb.get(n, 0) - la.get(n, 0), 2)
                if d:
                    why.append(f'{n} {d:+,.2f}')
            if b.days_worked != a.days_worked:
                why.append(f'days worked {a.days_worked} → {b.days_worked}')
        rows.append({**emp_cols(emps[eid]), 'prev_gross': ga, 'gross': gb, 'gross_change': round(gb - ga, 2), 'prev_net': na, 'net': nb,
                     'net_change': round(nb - na, 2), 'pct': round(100 * (nb - na) / na, 1) if na else '', 'changed': 'Yes' if round(nb - na, 2) or round(gb - ga, 2) else 'No',
                     'why': '; '.join(why), **src(b or a), '_cell': {'prev_gross': src(a), 'prev_net': src(a), 'gross': src(b), 'net': src(b)} if a and b else {}})
    return EMP_COLS + [('prev_gross', f'Gross {pl} (AED)', 'number'), ('gross', f'Gross {cl} (AED)', 'number'), ('gross_change', 'Gross change (AED)', 'number'),
                       ('prev_net', f'Net {pl} (AED)', 'number'), ('net', f'Net {cl} (AED)', 'number'), ('net_change', 'Net change (AED)', 'number'),
                       ('pct', 'Net change %', 'number'), ('changed', 'Changed', 'text'), ('why', 'What changed', 'text')], rows, \
        {'filters': SUM_FILTERS + ['changed'], 'notes': [f'{cl} compared with {pl} (processed, approved and paid runs). Per component: Payroll variance.']}


SUM_FILTERS = ['department', 'category', 'employee']


def r_payslip_status(run):
    rows = []
    for p in _payslips(run):
        pr = p.payroll_run
        rows.append({**emp_cols(p.employee), 'run': pr.name or _period(pr), 'period': _period(pr), 'run_status': nice(pr.status), 'status': nice(p.status),
                     'confirmed': nice(bool(p.confirm_status)), 'trial': nice(bool(p.trial_status)), 'emailed': nice(bool(p.send_email)),
                     'pdf': 'Yes' if p.payslip_pdf else 'No', 'gross': money(p.gross_salary), 'net': net_of(p),
                     'zero': 'Yes' if net_of(p) <= 0 else 'No', 'reason': p.rejection_reason or '', **src(p), '_cell': {'run': src(pr)}})
    return EMP_COLS + [('run', 'Payroll run', 'text'), ('period', 'Period', 'text'), ('run_status', 'Run status', 'text'), ('status', 'Payslip status', 'text'),
                       ('confirmed', 'Confirmed', 'text'), ('trial', 'Trial', 'text'), ('emailed', 'E-mailed', 'text'), ('pdf', 'PDF ready', 'text'),
                       ('gross', 'Gross (AED)', 'number'), ('net', 'Net pay (AED)', 'number'), ('zero', 'No net pay', 'text'), ('reason', 'Rejection reason', 'text')], rows, \
        {'filters': FILTERS + ['zero']}


REPORTS = {
    'payroll-register': ('Payroll register', 'Payroll', 'One row per payslip of the payroll runs in the period: every salary component as a column, gross, deductions and net pay, with run and payslip status.', r_payroll_register, 'month', ['view_payslip', 'view_payrollrun']),
    'payroll-department': ('Payroll by department', 'Payroll', 'Payroll register subtotals by month, branch and department: every component, gross, deductions and net pay.', r_payroll_department, 'month', ['view_payslip', 'view_payrollrun']),
    'payroll-components': ('Payroll summary by component', 'Payroll', 'Total of every salary component per month with the number of employees paid.', r_payroll_components, 'month', ['view_payslip', 'view_payrollrun']),
    'bank-transfer': ('Bank / WPS transfer list', 'Payroll', 'Net pay per employee with bank, account, IBAN, routing code and Person ID – the list for the bank or WPS agent, with a check column.', r_bank_transfer, 'month', ['view_payslip', 'view_payrollrun']),
    'payroll-mom': ('Payroll month over month', 'Payroll', 'Per employee: gross and net pay this month against the month before, the change and what changed.', r_payroll_mom, 'month', ['view_payslip', 'view_payrollrun']),
    'payslip-status': ('Payslip status', 'Payroll', 'Every payslip of the period with run status, payslip status, confirmation, e-mail and PDF.', r_payslip_status, 'month', ['view_payslip', 'view_payrollrun']),
}
REPORT_MODELS = {
    'payroll-register': {'PayrollManagement.Payslip', 'PayrollManagement.PayrollRun', 'PayrollManagement.PayslipComponent'},
    'payroll-department': set(), 'payroll-components': set(),
    'bank-transfer': {'PayrollManagement.Payslip', 'EmpManagement.EmployeeBankDetail'},
    'payroll-mom': {'PayrollManagement.Payslip'}, 'payslip-status': {'PayrollManagement.Payslip', 'PayrollManagement.PayrollRun'},
}
