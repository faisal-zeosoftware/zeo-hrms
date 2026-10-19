"""ZEO accounting integration – builds balanced journals from payroll runs, month-end provisions, final settlements and
expense claims, resolves accounts and dimensions by the company's posting rules, and sends them to the connector.

Posting rules (AccountingSettings):
  department_mode / branch_mode   none | analytic | gl | both
  employee_mode                   none | analytic | gl | both | partner
  dims_on                         cost  – only expense (cost) lines carry department / employee / branch
                                  all   – every line (deductions, payables) carries them too

GL resolution of a line:  AccountMap for the employee › for the department › for the branch › for everyone › the default
account; then, for each dimension in GL mode that the explicit row did not already cover, the pattern
({account}-{dept}, {account}-{emp}, {account}-{branch}) with the dimension's GL code (DimensionMap) or its ZEO code."""
import calendar
import logging
from collections import OrderedDict, defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models import (AccountingSettings, AccountMap, DimensionMap, JournalBatch, JournalLine, ProvisionBalance, SyncLog)

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
TWO = Decimal('0.01')
OPEN = ('draft', 'ready', 'failed', 'posting')
DONE = ('posted', 'exported')


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or []


def q2(v):
    return Decimal(str(v or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


def month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def emp_name(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, getattr(e, 'emp_middle_name', None), e.emp_last_name] if x and str(x).strip())
    return f'{n} ({e.emp_code})' if n else e.emp_code


def settings():
    return AccountingSettings.get()


def logit(batch, action, ok=True, detail='', by=None):
    SyncLog.objects.create(batch=batch, action=action, ok=ok, detail=str(detail)[:4000], by=by if getattr(by, 'pk', None) else None)


# ------------------------------------------------------------------ names of departments, employees, branches
class Dims:
    """Names, codes and mappings of the departments / employees / branches of one build."""

    def __init__(self):
        self.st = settings()
        self.maps = {(d.kind, d.local_id): d for d in DimensionMap.objects.all()}
        self.depts = {d.pk: d for d in M('OrganisationManager', 'dept_master').objects.all()}
        self.branches = {b.pk: b for b in M('OrganisationManager', 'brnch_mstr').objects.all()}
        self.emps = {}

    def emp(self, pk):
        if pk and pk not in self.emps:
            self.emps[pk] = M('EmpManagement', 'emp_master').objects.filter(pk=pk).first()
        return self.emps.get(pk)

    def name(self, kind, pk):
        if not pk:
            return ''
        if kind == 'department':
            d = self.depts.get(pk)
            return d.dept_name if d else f'#{pk}'
        if kind == 'branch':
            b = self.branches.get(pk)
            return b.branch_name if b else f'#{pk}'
        return emp_name(self.emp(pk))

    def code(self, kind, pk):
        """The GL code of the dimension: DimensionMap.gl_code, else the ZEO code (department / branch / employee code)."""
        m = self.maps.get((kind, pk))
        if m and m.gl_code:
            return m.gl_code
        if kind == 'department':
            d = self.depts.get(pk)
            return (d.dept_code or '') if d else ''
        if kind == 'branch':
            b = self.branches.get(pk)
            return (b.branch_code or '') if b else ''
        e = self.emp(pk)
        return e.emp_code if e else ''

    def ref(self, kind, pk):
        m = self.maps.get((kind, pk))
        return (m.analytic_ref if m else '') or ''

    def partner(self, pk):
        m = self.maps.get(('employee', pk))
        return (m.partner_ref if m else '') or ''


# ------------------------------------------------------------------ accounts
class Accounts:
    def __init__(self):
        self.rows = defaultdict(list)
        for a in AccountMap.objects.all():
            self.rows[(a.kind, str(a.key))].append(a)

    def find(self, kind, key, side, emp=None, dept=None, branch=None, default=''):
        """(account, level) – level is 'employee' / 'department' / 'branch' when a specific row gave it."""
        rows = self.rows.get((kind, str(key)), [])
        field = 'debit_account' if side == 'debit' else 'credit_account'
        for level, test in (('employee', lambda r: emp and r.employee_id == emp),
                            ('department', lambda r: dept and r.department_id == dept and not r.employee_id),
                            ('branch', lambda r: branch and r.branch_id == branch and not r.employee_id and not r.department_id),
                            (None, lambda r: not r.employee_id and not r.department_id and not r.branch_id)):
            for r in rows:
                if test(r) and getattr(r, field):
                    return getattr(r, field), level
        return default, None


# ------------------------------------------------------------------ the journal builder
class Builder:
    """Collects entries (one per employee / component) and turns them into the journal lines the rules ask for."""

    def __init__(self, source, source_id, label, jdate, reference=''):
        self.st = settings()
        self.dims = Dims()
        self.accts = Accounts()
        self.source, self.source_id, self.label, self.date, self.reference = source, str(source_id), label, jdate, reference or label
        self.entries = []
        self.problems = []
        self.extra = {}

    def problem(self, code, text, block=True, **kw):
        p = {'code': code, 'text': text, 'block': block, **kw}
        if p not in self.problems:
            self.problems.append(p)

    def add(self, account, label, amount, side, emp=None, dept=None, branch=None, cost=False, level=None, group=None):
        """amount on the debit (side='debit') or credit side; a negative amount goes to the other side."""
        amount = q2(amount)
        if not amount:
            return
        if not account:
            self.problem('account', f'No account for "{label}" – fill it in Accounting › Accounts.', group=group or label)
        self.entries.append({'account': account or '?', 'label': label, 'dr': amount if side == 'debit' else ZERO,
                             'cr': amount if side == 'credit' else ZERO, 'emp': emp, 'dept': dept, 'branch': branch, 'cost': cost,
                             'level': level, 'group': group or label})

    # -- rules
    def keeps(self, e):
        return e['cost'] or self.st.dims_on == 'all'

    def resolve(self, e):
        st, d = self.st, self.dims
        keep = self.keeps(e)
        dept = e['dept'] if keep and st.department_mode != 'none' else None
        emp = e['emp'] if keep and st.employee_mode != 'none' else None
        branch = e['branch'] if keep and st.branch_mode != 'none' else None
        account = e['account']
        lvl = e['level']
        # GL patterns – branch, then department, then employee; a specific AccountMap row already chose the account for its level
        if branch and st.branch_mode in ('gl', 'both') and lvl is None:
            account = self._pattern(st.branch_gl_pattern, account, 'branch', branch, e)
        if dept and st.department_mode in ('gl', 'both') and lvl not in ('department', 'employee'):
            account = self._pattern(st.dept_gl_pattern, account, 'department', dept, e)
        if emp and st.employee_mode in ('gl', 'both') and lvl != 'employee':
            account = self._pattern(st.emp_gl_pattern, account, 'employee', emp, e)
        analytics = {}
        for kind, pk, mode in (('department', dept, st.department_mode), ('employee', emp, st.employee_mode), ('branch', branch, st.branch_mode)):
            if pk and mode in ('analytic', 'both'):
                ref = d.ref(kind, pk)
                if ref:
                    analytics[kind] = ref
                elif st.connector in ('odoo', 'zoho'):
                    self.problem('dimension', f'{kind.title()} "{d.name(kind, pk)}" has no analytic account / tag in the accounting app.',
                                 kind=kind, id=pk)
        partner = ''
        if emp and st.employee_mode == 'partner':
            partner = d.partner(emp)
            if not partner and st.connector in ('odoo', 'zoho'):
                self.problem('dimension', f'Employee "{d.name("employee", emp)}" has no partner / contact in the accounting app.',
                             kind='employee', id=emp)
        return account, dept, emp, branch, analytics, partner

    def _pattern(self, pattern, account, kind, pk, e):
        code = self.dims.code(kind, pk)
        if not code:
            self.problem('gl', f'{kind.title()} "{self.dims.name(kind, pk)}" has no GL code – set it in Accounting › Departments / Employees.',
                         kind=kind, id=pk)
            return account
        vals = {'account': account, 'dept': '', 'emp': '', 'branch': ''}
        vals[{'department': 'dept', 'employee': 'emp', 'branch': 'branch'}[kind]] = code
        if e.get('dept'):
            vals['dept'] = vals['dept'] or self.dims.code('department', e['dept'])
        if e.get('branch'):
            vals['branch'] = vals['branch'] or self.dims.code('branch', e['branch'])
        try:
            return pattern.format(**vals)
        except (KeyError, IndexError, ValueError):
            self.problem('pattern', f'The GL code pattern "{pattern}" is not valid (use {{account}} {{dept}} {{emp}} {{branch}}).')
            return account

    def lines(self):
        groups = OrderedDict()
        for e in self.entries:
            account, dept, emp, branch, analytics, partner = self.resolve(e)
            k = (account, e['group'], dept, emp, branch, tuple(sorted(analytics.items())), partner)
            g = groups.get(k)
            if g is None:
                g = groups[k] = {'account': account, 'base_account': e['account'], 'label': e['label'], 'net': ZERO, 'dept': dept, 'emp': emp,
                                 'branch': branch, 'analytics': analytics, 'partner': partner}
            g['net'] += e['dr'] - e['cr']
        out = []
        for g in groups.values():
            if not g['net']:
                continue
            g['debit'], g['credit'] = (g['net'], ZERO) if g['net'] > 0 else (ZERO, -g['net'])
            out.append(g)
        dr = sum((x['debit'] for x in out), ZERO)
        cr = sum((x['credit'] for x in out), ZERO)
        diff = dr - cr
        if diff:
            if abs(diff) > Decimal('1.00'):
                self.problem('balance', f'The journal is out of balance by {diff:,.2f}.')
            out.append({'account': self.st.rounding_account, 'base_account': self.st.rounding_account, 'label': 'Rounding difference',
                        'debit': -diff if diff < 0 else ZERO, 'credit': diff if diff > 0 else ZERO, 'dept': None, 'emp': None, 'branch': None,
                        'analytics': {}, 'partner': ''})
        return out

    # -- save
    @transaction.atomic
    def save(self, user=None):
        """Makes (or remakes) the open batch of this source. A posted batch is never touched: reverse it first."""
        version = JournalBatch.objects.filter(source=self.source, source_id=self.source_id, status__in=('reversed', 'cancelled')).count()
        key = f'{self.source}:{self.source_id}:{version}'
        b = JournalBatch.objects.select_for_update().filter(idempotency_key=key).first()
        if b and b.status not in ('draft', 'ready', 'failed'):
            raise Refused(f'This journal is already {b.get_status_display().lower()} ({b.number}). Reverse it to build it again.')
        lines = self.lines()
        if not lines:
            raise Refused('There is nothing to post.')
        if b is None:
            b = JournalBatch(idempotency_key=key, source=self.source, source_id=self.source_id, created_by=user if getattr(user, 'pk', None) else None)
        b.source_label, b.date, b.reference, b.connector = self.label, self.date, self.reference[:120], self.st.connector
        b.rules = {'department': self.st.department_mode, 'employee': self.st.employee_mode, 'branch': self.st.branch_mode,
                   'dims_on': self.st.dims_on, **self.extra}
        b.problems = self.problems
        b.status = 'draft' if any(p['block'] for p in self.problems) else 'ready'
        b.total_debit = sum((x['debit'] for x in lines), ZERO)
        b.total_credit = sum((x['credit'] for x in lines), ZERO)
        b.lines_count = len(lines)
        b.last_error = ''
        b.save()
        if not b.number:
            b.number = f'ZJ-{b.date:%Y}-{b.pk:05d}'
            b.save(update_fields=['number'])
        b.lines.all().delete()
        d = self.dims
        JournalLine.objects.bulk_create([JournalLine(batch=b, seq=i + 1, account=x['account'], base_account=x['base_account'], label=x['label'][:255],
                                                     debit=x['debit'], credit=x['credit'], department_id=x['dept'], department=d.name('department', x['dept']),
                                                     employee_id=x['emp'], employee=d.name('employee', x['emp'])[:200], branch_id=x['branch'],
                                                     branch=d.name('branch', x['branch']), analytics=x['analytics'], partner_ref=x['partner'])
                                         for i, x in enumerate(lines)])
        logit(b, 'build', True, f'{len(lines)} lines, {b.total_debit:,.2f}', user)
        return b


# ------------------------------------------------------------------ sources
def build_payroll(run_id, user=None):
    Run = M('PayrollManagement', 'PayrollRun')
    run = Run.objects.filter(pk=run_id).first()
    if run is None:
        raise Refused('Payroll run not found.', 404)
    if run.status not in ('approved', 'paid'):
        raise Refused('Only an approved or paid payroll run can be posted.')
    st = settings()
    jdate = run.payment_date or month_end(date(run.year, run.month, 1))
    label = f'Payroll {run.get_month_display()} {run.year}' + (f' – {run.name}' if run.name else '')
    b = Builder('payroll', run.pk, label, jdate, f'Payroll {run.year}-{run.month:02d}')
    slips = run.payslips.exclude(status='rejected').select_related('employee').prefetch_related('components__component')
    n = 0
    for p in slips:
        e = p.employee
        dept, branch = e.emp_dept_id_id, e.emp_branch_id_id
        b.dims.emps[e.pk] = e
        adds = deds = ZERO
        for pc in p.components.all():
            c = pc.component
            amt = q2(pc.amount)
            if not amt:
                continue
            if c.component_type == 'addition':
                if not c.show_in_payslip:
                    continue
                acct, lvl = b.accts.find('component', c.pk, 'debit', e.pk, dept, branch, st.default_expense_account)
                b.add(acct, c.name, amt, 'debit', e.pk, dept, branch, cost=True, level=lvl, group=f'c{c.pk}')
                adds += amt
            elif c.component_type == 'deduction':
                if not c.show_in_payslip:
                    continue
                acct, lvl = b.accts.find('component', c.pk, 'credit', e.pk, dept, branch, st.default_deduction_account)
                b.add(acct, c.name, amt, 'credit', e.pk, dept, branch, cost=False, level=lvl, group=f'c{c.pk}')
                deds += amt
            else:   # others – employer contribution / cost: Dr expense, Cr liability; does not change the net pay
                acct, lvl = b.accts.find('component', c.pk, 'debit', e.pk, dept, branch, st.default_expense_account)
                lbl = c.name if 'employer' in c.name.lower() else f'{c.name} (employer)'
                b.add(acct, lbl, amt, 'debit', e.pk, dept, branch, cost=True, level=lvl, group=f'c{c.pk}e')
                acct, lvl = b.accts.find('component', c.pk, 'credit', e.pk, dept, branch, st.default_deduction_account)
                b.add(acct, lbl, amt, 'credit', e.pk, dept, branch, cost=False, level=lvl, group=f'c{c.pk}l')
        net = q2(p.net_salary) if p.net_salary is not None else adds - deds
        acct, lvl = b.accts.find('payable', 'net', 'credit', e.pk, dept, branch, st.payable_account)
        b.add(acct, 'Salaries payable', net, 'credit', e.pk, dept, branch, cost=False, level=lvl, group='net')
        n += 1
    if not n:
        raise Refused('The payroll run has no payslips.')
    b.extra = {'payslips': n}
    return b.save(user)


def _report_rows(fn, as_of):
    from DashboardManagement import reports as R

    class FakeRun:
        pass
    r = FakeRun()
    r.today = as_of
    r.date_from = r.date_to = as_of
    r.branches = None
    r.employees = M('EmpManagement', 'emp_master').objects.filter(is_active=True).select_related(
        'emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id', 'emp_nationality', 'emp_reporting_manager')
    cols, rows = getattr(R, fn)(r)
    return rows


def provision_amounts(as_of):
    """{kind: {employee id: amount}} – gratuity accrued and leave salary liability (basic) as of the date."""
    out = {'gratuity': {}, 'leave': {}}
    for r in _report_rows('r_gratuity', as_of):
        if r.get('_emp'):
            out['gratuity'][r['_emp']] = q2(r.get('amount') or 0)
    for r in _report_rows('r_leave_liability', as_of):
        if r.get('_emp'):
            out['leave'][r['_emp']] = q2(r.get('liability') or 0)
    return out


def build_provisions(as_of=None, user=None, amounts=None):
    """Month-end provisions: the change since the last posted provision, per employee (Dr expense / Cr provision; a fall is
    the other way round). Employees no longer active fall to zero only through their final settlement."""
    st = settings()
    as_of = month_end(as_of or date.today())
    amounts = amounts if amounts is not None else provision_amounts(as_of)
    prev = {(p.kind, p.employee_id): p.amount for p in ProvisionBalance.objects.all()}
    b = Builder('provision', as_of.strftime('%Y-%m'), f'Gratuity and leave salary provisions {as_of:%B %Y}', as_of, f'Provisions {as_of:%Y-%m}')
    Emp = M('EmpManagement', 'emp_master')
    emps = {e.pk: e for e in Emp.objects.filter(pk__in={k for v in amounts.values() for k in v})}
    b.dims.emps.update(emps)
    new = {'gratuity': {}, 'leave': {}}
    acc = {'gratuity': (st.gratuity_expense_account, st.gratuity_provision_account, 'Gratuity provision'),
           'leave': (st.leave_expense_account, st.leave_provision_account, 'Leave salary provision')}
    for kind, rows in amounts.items():
        exp_a, prov_a, label = acc[kind]
        for emp_id, amt in rows.items():
            delta = q2(amt) - q2(prev.get((kind, emp_id), 0))
            if not delta:
                continue
            e = emps.get(emp_id)
            dept, branch = (e.emp_dept_id_id, e.emp_branch_id_id) if e else (None, None)
            ea, el = b.accts.find('provision', kind, 'debit', emp_id, dept, branch, exp_a)
            pa, pl = b.accts.find('provision', kind, 'credit', emp_id, dept, branch, prov_a)
            b.add(ea, label, delta, 'debit', emp_id, dept, branch, cost=True, level=el, group=f'{kind}-x')
            b.add(pa, label, delta, 'credit', emp_id, dept, branch, cost=False, level=pl, group=f'{kind}-p')
            new[kind][str(emp_id)] = str(q2(amt))
    b.extra = {'provision': new, 'as_of': as_of.isoformat()}
    if not b.entries:
        raise Refused('The provisions have not changed since the last posting.')
    return b.save(user)


SETTLEMENT_PROVISION = {'gratuity': 'gratuity', 'leave': 'leave'}


def build_settlement(settlement_id, user=None):
    S = M('PayrollPlus', 'ExitSettlement')
    s = S.objects.filter(pk=settlement_id).first()
    if s is None:
        raise Refused('Final settlement not found.', 404)
    if s.status not in ('approved', 'paid'):
        raise Refused('Only an approved or paid final settlement can be posted.')
    st = settings()
    e = M('EmpManagement', 'emp_master').objects.filter(pk=s.employee_id).first()
    dept, branch = (e.emp_dept_id_id, e.emp_branch_id_id) if e else (None, None)
    b = Builder('settlement', s.pk, f'Final settlement {s.number or s.pk} – {emp_name(e)}', s.paid_on or s.last_working_date, s.number or f'Settlement {s.pk}')
    if e:
        b.dims.emps[e.pk] = e
    prov = {p.kind: p.amount for p in ProvisionBalance.objects.filter(employee_id=s.employee_id)}
    used = {}
    for ln in s.lines or []:
        amt = q2(ln.get('amount'))
        if not amt or ln.get('kind') not in ('earning', 'deduction'):
            continue
        key, label = ln.get('key') or 'other', ln.get('label') or ln.get('key')
        if ln['kind'] == 'earning':
            pk = SETTLEMENT_PROVISION.get(key)
            if pk:   # paid from the provision first, the rest is this month's expense
                have = min(q2(prov.get(pk, 0)), amt) if prov.get(pk, 0) > 0 else ZERO
                if have:
                    pa = st.gratuity_provision_account if pk == 'gratuity' else st.leave_provision_account
                    acct, lvl = b.accts.find('provision', pk, 'credit', e and e.pk, dept, branch, pa)
                    b.add(acct, f'{label} (from provision)', have, 'debit', e and e.pk, dept, branch, cost=False, level=lvl, group=f'{key}-p')
                    used[pk] = str(have)
                rest = amt - have
                default = st.gratuity_expense_account if pk == 'gratuity' else st.leave_expense_account
            else:
                rest, default = amt, st.default_expense_account
            if rest:
                acct, lvl = b.accts.find('settlement', key, 'debit', e and e.pk, dept, branch, default)
                b.add(acct, label, rest, 'debit', e and e.pk, dept, branch, cost=True, level=lvl, group=key)
        else:
            acct, lvl = b.accts.find('settlement', key, 'credit', e and e.pk, dept, branch, st.default_deduction_account)
            b.add(acct, label, amt, 'credit', e and e.pk, dept, branch, cost=False, level=lvl, group=key)
    acct, lvl = b.accts.find('payable', 'settlement', 'credit', e and e.pk, dept, branch, st.settlement_payable_account)
    b.add(acct, 'Final settlement payable', q2(s.net), 'credit', e and e.pk, dept, branch, cost=False, level=lvl, group='net')
    b.extra = {'settlement_provision_used': used, 'employee_id': s.employee_id}
    return b.save(user)


def build_expenses(date_from, date_to, user=None):
    """Approved / reimbursed expense reports of the period that are not in another journal yet."""
    Rep = M('ExpenseManagement', 'ExpenseReport')
    ES = M('ExpenseManagement', 'ExpenseSettings')
    est = ES.objects.order_by('id').first()
    done = set()
    for x in JournalBatch.objects.filter(source='expense').exclude(status__in=('reversed', 'cancelled')).values_list('rules', flat=True):
        done.update((x or {}).get('reports', []))
    qs = Rep.objects.filter(status__in=('approved', 'reimbursed'), approved_at__date__gte=date_from, approved_at__date__lte=date_to).exclude(pk__in=done)
    reps = list(qs.order_by('approved_at', 'id'))
    if not reps:
        raise Refused('No approved expense reports in this period that are not posted yet.')
    st = settings()
    b = Builder('expense', f'{date_from:%Y%m%d}-{date_to:%Y%m%d}', f'Expense claims {date_from:%d/%m/%Y} – {date_to:%d/%m/%Y}', date_to,
                f'Expenses {date_from:%Y-%m-%d}..{date_to:%Y-%m-%d}')
    Emp = M('EmpManagement', 'emp_master')
    emps = {e.pk: e for e in Emp.objects.filter(pk__in={r.employee_id for r in reps})}
    b.dims.emps.update(emps)
    exp_default = (est.default_expense_account if est else '') or st.default_expense_account
    for r in reps:
        e = emps.get(r.employee_id)
        branch = r.branch_id or (e.emp_branch_id_id if e else None)
        rdept = r.department_id or (e.emp_dept_id_id if e else None)
        for x in r.expenses.exclude(status='rejected').select_related('category'):
            dept = x.department_id or rdept
            acct, lvl = b.accts.find('expense', x.category_id, 'debit', r.employee_id, dept, branch, x.category.gl_account or exp_default)
            b.add(acct, x.category.name, x.amount_aed, 'debit', r.employee_id, dept, branch, cost=True, level=lvl, group=f'cat{x.category_id}')
        if r.advance_applied:
            b.add(est.advance_account if est else '1450', 'Advances used', r.advance_applied, 'credit', r.employee_id, rdept, branch, group='adv')
        if r.amount_to_reimburse:
            b.add(est.payable_account if est else '2150', 'Reimbursements due', r.amount_to_reimburse, 'credit', r.employee_id, rdept, branch, group='pay')
    b.extra = {'reports': [r.pk for r in reps]}
    return b.save(user)


def recheck(batch, user=None):
    """Build the batch again from its source (after a mapping was filled in)."""
    if batch.status not in ('draft', 'ready', 'failed'):
        raise Refused('Only a draft, ready or failed journal can be checked again.')
    if batch.source == 'payroll':
        return build_payroll(int(batch.source_id), user)
    if batch.source == 'settlement':
        return build_settlement(int(batch.source_id), user)
    if batch.source == 'provision':
        return build_provisions(date.fromisoformat(batch.rules.get('as_of')), user)
    if batch.source == 'expense':
        return _rebuild_expenses(batch, user)
    raise Refused('This journal cannot be rebuilt.')


@transaction.atomic
def _rebuild_expenses(batch, user):
    a, b = batch.source_id.split('-')
    batch.rules = {}
    batch.status = 'cancelled'
    batch.save(update_fields=['rules', 'status'])   # let its reports be picked up again
    return build_expenses(date(int(a[:4]), int(a[4:6]), int(a[6:])), date(int(b[:4]), int(b[4:6]), int(b[6:])), user)


# ------------------------------------------------------------------ after posting / exporting
def _apply_provisions(batch, undo=False):
    r = batch.rules or {}
    if batch.source == 'provision':
        as_of = date.fromisoformat(r.get('as_of'))
        if not undo:
            prev = {}
            for kind, rows in (r.get('provision') or {}).items():
                for emp, amt in rows.items():
                    p, _ = ProvisionBalance.objects.get_or_create(kind=kind, employee_id=int(emp), defaults={'as_of': as_of})
                    prev.setdefault(kind, {})[emp] = str(p.amount)
                    p.amount, p.as_of, p.batch = Decimal(amt), as_of, batch
                    p.save()
            batch.rules = {**r, 'provision_before': prev}
            batch.save(update_fields=['rules'])
        else:
            for kind, rows in (r.get('provision_before') or {}).items():
                for emp, amt in rows.items():
                    ProvisionBalance.objects.filter(kind=kind, employee_id=int(emp)).update(amount=Decimal(amt))
    elif batch.source == 'settlement':
        emp = r.get('employee_id')
        for kind, amt in (r.get('settlement_provision_used') or {}).items():
            p = ProvisionBalance.objects.filter(kind=kind, employee_id=emp).first()
            if p:
                p.amount = p.amount + Decimal(amt) if undo else max(ZERO, p.amount - Decimal(amt))
                p.save(update_fields=['amount'])


def mark_done(batch, status, user=None, external_id='', external_ref=''):
    batch.status = status
    batch.posted_at = timezone.now()
    batch.posted_by = user if getattr(user, 'pk', None) else None
    if external_id:
        batch.external_id = str(external_id)
    if external_ref:
        batch.external_ref = str(external_ref)
    batch.last_error = ''
    batch.save()
    _apply_provisions(batch)
    hooks_fire('journal.posted' if status == 'posted' else 'journal.exported', batch)


# ------------------------------------------------------------------ posting
def connector(st=None):
    st = st or settings()
    if st.connector == 'odoo':
        from .connectors.odoo import Odoo
        return Odoo(st)
    if st.connector == 'zoho':
        from .connectors.zoho import Zoho
        return Zoho(st)
    return None


def post(batch, user=None):
    st = settings()
    if batch.status in DONE:
        return batch
    if batch.status not in ('ready', 'failed'):
        raise Refused('Fix the problems first – the journal is not ready to post.', problems=batch.problems)
    if any(p.get('block') for p in batch.problems or []):
        raise Refused('Fix the problems first.', problems=batch.problems)
    if batch.source == 'reversal' and batch.connector and batch.connector != st.connector:
        raise Refused(f'This reversal belongs to {batch.connector.title()} – enter it there and mark it as exported.')
    con = connector(st)
    if con is None:
        raise Refused('No accounting application is connected – download the journal (Excel / CSV) or let the other application pull it.')
    with transaction.atomic():
        b = JournalBatch.objects.select_for_update().get(pk=batch.pk)
        if b.status in DONE or b.status == 'posting':
            return b
        b.status, b.attempts, b.connector = 'posting', b.attempts + 1, st.connector
        b.save(update_fields=['status', 'attempts', 'connector'])
    try:
        ext_id, ext_ref = con.post(b)
    except Exception as ex:   # noqa: BLE001 – every failure is shown on the journal and retried
        b.status, b.last_error = 'failed', str(ex)[:2000]
        b.save(update_fields=['status', 'last_error'])
        logit(b, 'post', False, ex, user)
        raise Refused(f'The accounting application refused the journal: {ex}', status=502)
    mark_done(b, 'posted', user, ext_id, ext_ref)
    logit(b, 'post', True, f'{st.connector} {ext_ref or ext_id}', user)
    return b


@transaction.atomic
def reverse(batch, user=None, on=None):
    """A posted / exported journal is never edited: a reversing journal (debits and credits swapped) is made and posted, and
    the source can be built again."""
    if batch.status not in DONE:
        raise Refused('Only a posted or exported journal can be reversed.')
    on = on or date.today()
    r = JournalBatch.objects.create(source='reversal', source_id=str(batch.pk), source_label=f'Reversal of {batch.number} – {batch.source_label}'[:200],
                                    date=on, reference=f'Reversal {batch.number}', connector=batch.connector, reversal_of=batch,
                                    total_debit=batch.total_credit, total_credit=batch.total_debit, lines_count=batch.lines_count,
                                    idempotency_key=f'reversal:{batch.pk}', rules=batch.rules, status='ready',
                                    created_by=user if getattr(user, 'pk', None) else None)
    r.number = f'ZJ-{on:%Y}-{r.pk:05d}'
    r.save(update_fields=['number'])
    JournalLine.objects.bulk_create([JournalLine(batch=r, seq=l.seq, account=l.account, base_account=l.base_account, label=f'Reversal – {l.label}'[:255],
                                                 debit=l.credit, credit=l.debit, department_id=l.department_id, department=l.department,
                                                 employee_id=l.employee_id, employee=l.employee, branch_id=l.branch_id, branch=l.branch,
                                                 analytics=l.analytics, partner_ref=l.partner_ref) for l in batch.lines.all()])
    batch.status = 'reversed'
    batch.save(update_fields=['status'])
    _apply_provisions(batch, undo=True)
    logit(batch, 'reverse', True, r.number, user)
    if batch.connector in ('odoo', 'zoho') and batch.external_id:   # posted there – post the reversal there too
        if batch.connector == settings().connector:
            transaction.on_commit(lambda: _post_quiet(r.pk, user))
        else:   # the company has moved to another application since: the reversal is entered there by hand
            r.problems = [{'code': 'connector', 'block': True, 'text': f'{batch.number} was posted to {batch.connector.title()}, which is no longer '
                           f'connected – enter this reversal there by hand (Excel) and mark it as exported.'}]
            r.status = 'draft'
            r.save(update_fields=['problems', 'status'])
    return r


def _post_quiet(pk, user=None):
    try:
        post(JournalBatch.objects.get(pk=pk), user)
    except Refused as ex:
        log.warning('reversal %s not posted: %s', pk, ex.message)


def cancel(batch, user=None):
    if batch.status not in ('draft', 'ready', 'failed'):
        raise Refused('Only a journal that was not posted can be cancelled.')
    batch.status = 'cancelled'
    batch.save(update_fields=['status'])
    logit(batch, 'cancel', True, '', user)
    return batch


# ------------------------------------------------------------------ export (Tally, Sage, QuickBooks, Xero … or any other app)
EXPORT_COLS = ['Journal', 'Date', 'Reference', 'Line', 'Account', 'Base account', 'Description', 'Debit', 'Credit', 'Department', 'Department code',
               'Employee', 'Employee code', 'Branch', 'Analytic department', 'Analytic employee', 'Analytic branch', 'Partner']


def export_rows(batches):
    d = Dims()
    for b in batches:
        for l in b.lines.all():
            yield [b.number, b.date.isoformat(), b.reference, l.seq, l.account, l.base_account, l.label, float(l.debit), float(l.credit),
                   l.department, d.code('department', l.department_id) if l.department_id else '', l.employee,
                   d.code('employee', l.employee_id) if l.employee_id else '', l.branch, l.analytics.get('department', l.department if b.rules.get('department') in ('analytic', 'both') else ''),
                   l.analytics.get('employee', ''), l.analytics.get('branch', ''), l.partner_ref]


def export_csv(batches):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLS)
    for row in export_rows(batches):
        w.writerow(row)
    return buf.getvalue()


def export_xlsx(batches):
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = 'Journal'
    ws.append(EXPORT_COLS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in export_rows(batches):
        ws.append(row)
    for col, w in zip('ABCDEFGHIJKLMNOPQR', (14, 11, 22, 6, 16, 12, 34, 12, 12, 18, 10, 28, 10, 16, 16, 16, 16, 12)):
        ws.column_dimensions[col].width = w
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ------------------------------------------------------------------ mapping helpers (accounting app → ZEO)
def fetch_options():
    """Accounts and analytic accounts / tag options of the connected application, for the mapping screens."""
    con = connector()
    if con is None:
        return {'accounts': [], 'analytics': {}, 'partners': []}
    return {'accounts': con.accounts(), 'analytics': {k: con.analytics(k) for k in ('department', 'employee', 'branch')}}


def auto_map(kind, create=False, user=None):
    """Match the ZEO departments / employees / branches with the analytic accounts (tag options) of the accounting app by code
    or name; with create=True the missing ones are made there."""
    con = connector()
    if con is None:
        raise Refused('Connect Odoo or Zoho Books first.')
    d = Dims()
    if kind == 'department':
        items = [(x.pk, x.dept_name, x.dept_code or '') for x in d.depts.values()]
    elif kind == 'branch':
        items = [(x.pk, x.branch_name, x.branch_code or '') for x in d.branches.values()]
    else:
        items = [(e.pk, emp_name(e), e.emp_code) for e in M('EmpManagement', 'emp_master').objects.filter(is_active=True)]
    have = con.analytics(kind)
    by_code = {(a.get('code') or '').lower(): a for a in have if a.get('code')}
    by_name = {(a.get('name') or '').lower(): a for a in have}
    done = made = 0
    for pk, name, code in items:
        m, _ = DimensionMap.objects.get_or_create(kind=kind, local_id=pk, defaults={'label': name})
        m.label = name
        if not m.analytic_ref:
            a = by_code.get(code.lower()) if code else None
            a = a or by_name.get(name.lower())
            if a is None and create:
                a = con.create_analytic(kind, name, code)
                made += 1
            if a:
                m.analytic_ref, m.analytic_name = str(a['id']), a.get('name', '')
                done += 1
        m.save()
    return {'matched': done, 'created': made, 'total': len(items)}


def map_partners(create=False):
    con = connector()
    if con is None:
        raise Refused('Connect Odoo or Zoho Books first.')
    n = made = 0
    for e in M('EmpManagement', 'emp_master').objects.filter(is_active=True):
        m, _ = DimensionMap.objects.get_or_create(kind='employee', local_id=e.pk, defaults={'label': emp_name(e)})
        if m.partner_ref:
            continue
        ref, was_made = con.partner(emp_name(e).split(' (')[0] or e.emp_code, e.emp_code, create)
        if ref:
            m.partner_ref = str(ref)
            m.save(update_fields=['partner_ref'])
            n += 1
            made += 1 if was_made else 0
    return {'matched': n, 'created': made}


def accounts_check(batch=None):
    """Accounts the journal(s) use that do not exist in the accounting app."""
    con = connector()
    if con is None:
        return []
    have = {a['code'] for a in con.accounts()}
    qs = JournalLine.objects.filter(batch=batch) if batch else JournalLine.objects.filter(batch__status__in=('draft', 'ready', 'failed'))
    need = sorted(set(qs.values_list('account', flat=True)))
    return [{'account': a, 'base': JournalLine.objects.filter(account=a).values_list('base_account', flat=True).first()} for a in need if a not in have]


def create_accounts(codes):
    """Make the missing department / employee GL accounts in the accounting app, copying the type of the base account."""
    con = connector()
    if con is None:
        raise Refused('Connect Odoo or Zoho Books first.')
    have = {a['code']: a for a in con.accounts()}
    made = []
    for code in codes:
        if code in have:
            continue
        base = JournalLine.objects.filter(account=code).values_list('base_account', 'department', 'employee', 'branch', 'label').first()
        if not base or base[0] not in have:
            raise Refused(f'Cannot create {code}: its base account is not in the accounting app.')
        parent = have[base[0]]
        name = f"{parent['name']} – {base[1] or base[2] or base[3] or code}"
        made.append(con.create_account(code, name[:100], parent))
    return made


# ------------------------------------------------------------------ automatic steps (signals / Celery)
def on_payroll_status(run):
    st = settings()
    if run.status != 'approved' or not st.auto_build:
        return
    if JournalBatch.objects.filter(source='payroll', source_id=str(run.pk)).exclude(status__in=('cancelled',)).exists():
        return
    try:
        b = build_payroll(run.pk)
    except Refused as ex:
        log.info('payroll %s not built: %s', run.pk, ex.message)
        return
    hooks_fire('journal.ready', b)
    if st.auto_post and b.status == 'ready' and st.connector in ('odoo', 'zoho'):
        transaction.on_commit(lambda: _post_quiet(b.pk))


def on_settlement_status(s):
    st = settings()
    if s.status != 'approved' or not st.auto_build:
        return
    if JournalBatch.objects.filter(source='settlement', source_id=str(s.pk)).exists():
        return
    try:
        b = build_settlement(s.pk)
    except Refused:
        return
    hooks_fire('journal.ready', b)
    if st.auto_post and b.status == 'ready' and st.connector in ('odoo', 'zoho'):
        transaction.on_commit(lambda: _post_quiet(b.pk))


def retry_failed():
    n = 0
    for b in JournalBatch.objects.filter(status='failed', attempts__lt=5):
        try:
            post(b)
            n += 1
        except Refused:
            pass
    # a "posting" stuck for an hour (server stopped mid-way): try again – the connector finds an entry already made
    stuck = timezone.now() - timezone.timedelta(hours=1)
    for b in JournalBatch.objects.filter(status='posting', attempts__lt=5, created_at__lt=stuck):
        b.status = 'failed'
        b.save(update_fields=['status'])
    return n


def month_end_provisions():
    """1st of the month: the provisions of the last month end (when the company posts provisions)."""
    st = settings()
    if not st.auto_build:
        return None
    last = date.today().replace(day=1) - timezone.timedelta(days=1)
    if JournalBatch.objects.filter(source='provision', source_id=last.strftime('%Y-%m')).exists():
        return None
    try:
        b = build_provisions(last)
    except Refused:
        return None
    if st.auto_post and b.status == 'ready' and st.connector in ('odoo', 'zoho'):
        _post_quiet(b.pk)
    return b


# ------------------------------------------------------------------ permission groups
ROLES = {'Accounting – manager': ('manage_accounting', 'post_accounting', 'view_accounting'),
         'Accounting – accountant': ('post_accounting', 'view_accounting'),
         'Accounting – viewer': ('view_accounting',)}


def make_roles():
    from django.contrib.auth.management import create_permissions
    from django.contrib.auth.models import Group, Permission
    from django_tenants.utils import schema_context
    with schema_context('public'):   # content types and rights are shared (public schema): make them before the groups use them
        from django.contrib.contenttypes.management import create_contenttypes
        create_contenttypes(apps.get_app_config('ZeoAccounting'), verbosity=0)
        create_permissions(apps.get_app_config('ZeoAccounting'), verbosity=0)
    for name, codes in ROLES.items():
        g, _ = Group.objects.get_or_create(name=name)
        g.permissions.add(*Permission.objects.filter(content_type__app_label='ZeoAccounting', codename__in=codes))


# ------------------------------------------------------------------ webhooks (pull API users)
def hooks_fire(event, batch):
    from .models import ApiClientHook
    hooks = [h for h in ApiClientHook.objects.filter(is_active=True) if event in (h.events or [])]
    if not hooks:
        return
    import hashlib
    import hmac
    import json
    from django.db import connection
    body = json.dumps({'event': event, 'company': connection.schema_name, 'journal': {'id': batch.pk, 'number': batch.number,
                       'source': batch.source, 'date': batch.date.isoformat(), 'status': batch.status, 'total': str(batch.total_debit)}}).encode()

    def send():
        import urllib.request
        for h in hooks:
            req = urllib.request.Request(h.url, data=body, headers={'Content-Type': 'application/json', 'X-ZEO-Event': event,
                                                                    'X-ZEO-Signature': hmac.new((h.secret or '').encode(), body, hashlib.sha256).hexdigest()})
            try:
                with urllib.request.urlopen(req, timeout=8) as r:
                    h.last_status = f'{r.status} {timezone.now():%d/%m %H:%M}'
            except Exception as ex:   # noqa: BLE001
                h.last_status = f'error: {str(ex)[:80]}'
            h.save(update_fields=['last_status'])
    transaction.on_commit(send)
