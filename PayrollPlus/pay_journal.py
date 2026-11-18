# v1.67.22 (pending C3): salary payments posted to accounting
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
"""v1.67.22 (pending C3) – the journal of salary payments.

The payroll journal (ZeoAccounting.build_payroll, made when a run is approved) books the cost and credits "Salaries payable".
Paying the salaries is a second entry, made from the paid marks of Payroll run › Payments:

    Dr Salaries payable (the payable account of the employee, as in the payroll journal)
    Cr the paying account of the method – WPS, bank transfer and cheque: the bank; cash: cash on hand; a company method: its own

One journal per "Mark paid" (date = the date paid, reference = the payment reference), source 'payroll_pay'. A split salary
(PayrollPlus/paysplit.py) has a line per part, each from its own method's account. Unmarking a part that was journaled makes the
opposite journal ("payment undone"). When the whole run is set to Paid, the parts not marked yet are journaled on the run's
payment date with its payment reference (RunControl.payment_ref).

Accounts: Accounting › Accounts › Salary payments (kind 'payable', key 'paid_<method>', by branch too); without a mapping the bank
account of Payroll › Loan and advance settings (1010 by default) or cash 1000. Built only with Accounting › Settings › "build the
journal when a payroll run is approved" on (the same switch as the payroll journal); posted / exported like every other journal.
What was journaled is kept in the run's payment store ('journaled': {"<payslip>:<method>": batch id}) – no new table.
"""
from decimal import Decimal

from django.apps import apps
from django.utils import timezone

from Translations.i18n import tr

M = apps.get_model
ZERO = Decimal('0.00')
CASH_DEFAULT = '1000'


def _bank_default():
    try:
        from .lending import settings as _ls
        return _ls()['accounts'].get('bank') or '1010'
    except Exception:
        return '1010'


def method_default(method):
    return CASH_DEFAULT if method == 'cash' else _bank_default()


def account_items():
    """the Salary payments rows of Accounting › Accounts (one per payment method)"""
    from .run_payments import all_methods
    return [{'kind': 'payable', 'key': f"paid_{m['code']}", 'label': tr('Paid by {0}', tr(m['name'])), 'group': 'Salary payments', 'side': 'credit',
             'default_credit': method_default(m['code'])} for m in all_methods()]


def enabled():
    try:
        from ZeoAccounting.services import settings as _st
        return bool(_st().auto_build)
    except Exception:
        return False


def _emps(ids):
    return {e.pk: e for e in M('EmpManagement', 'emp_master').objects.filter(pk__in=ids)}


def _build(run, lines, jdate, ref, undo=False, user=None, label=''):
    """lines: [{payslip, employee_id, method, amount}] → JournalBatch (or None when nothing to post)"""
    from ZeoAccounting.services import Builder, Refused, q2
    from ZeoAccounting.models import JournalBatch
    lines = [l for l in lines if q2(l['amount']) > 0]
    if not lines:
        return None
    n = JournalBatch.objects.filter(source='payroll_pay', source_id__startswith=f'{run.pk}-').count() + 1
    period = f'{run.get_month_display()} {run.year}' + (f' – {run.name}' if run.name else '')
    lbl = label or (tr('Salary payment undone – {0}', period) if undo else tr('Salaries paid – {0}', period))
    b = Builder('payroll_pay', f'{run.pk}-{n}', lbl, jdate, (ref or f'Salaries {run.year}-{run.month:02d}')[:120])
    st = b.st
    emps = _emps({l['employee_id'] for l in lines})
    names = {}
    try:
        from .run_payments import all_methods
        names = {m['code']: tr(m['name']) for m in all_methods()}
    except Exception:
        pass
    pay_side, bank_side = ('credit', 'debit') if undo else ('debit', 'credit')
    for l in lines:
        e = emps.get(l['employee_id'])
        if e is None:
            continue
        b.dims.emps[e.pk] = e
        dept, branch = e.emp_dept_id_id, e.emp_branch_id_id
        amt = q2(l['amount'])
        acct, lvl = b.accts.find('payable', 'net', 'debit', e.pk, dept, branch, st.payable_account)
        b.add(acct, tr('Salaries payable'), amt, pay_side, e.pk, dept, branch, cost=False, level=lvl, group='net')
        meth = l['method']
        pa, pl = b.accts.find('payable', f'paid_{meth}', 'credit', e.pk, dept, branch, method_default(meth))
        b.add(pa, tr('Paid by {0}', names.get(meth, meth)), amt, bank_side, e.pk, dept, branch, cost=False, level=pl, group=f'm_{meth}')
    b.extra = {'payment_lines': len(lines), 'run': run.pk, 'undo': undo}
    try:
        bt = b.save(user)
    except Refused:
        return None
    try:
        from ZeoAccounting.services import hooks_fire
        hooks_fire('journal.ready', bt) if bt.status == 'ready' else None
    except Exception:
        pass
    return bt


def _journaled(run_id):
    from .run_payments import store
    return dict(store(run_id).get('journaled') or {})


def _save_journaled(run_id, add=None, remove=()):
    from .run_payments import save_store

    def f(v):
        j = dict(v.get('journaled') or {})
        for k in remove:
            j.pop(k, None)
        j.update(add or {})
        v['journaled'] = j
    save_store(run_id, f)


def on_marked(run, lines, paid_on, ref, user=None, unmark=False):
    """After Payroll run › Payments › Mark paid / Not paid. lines: the payment lines (run_payments.lines) of the marked parts.
    → the journal made (or None)."""
    if not enabled():
        return None
    done = _journaled(run.pk)
    if unmark:
        mine = [l for l in lines if f"{l['payslip']}:{l['method']}" in done]
        if not mine:
            return None
        bt = _build(run, [dict(l, amount=done[f"{l['payslip']}:{l['method']}"].get('amount', l['amount'])) for l in mine],
                    _zt_today(), ref or tr('Payment undone'), undo=True, user=user)
        _save_journaled(run.pk, remove=[f"{l['payslip']}:{l['method']}" for l in mine])
        return bt
    new = [l for l in lines if f"{l['payslip']}:{l['method']}" not in done]
    if not new:
        return None
    import datetime
    try:
        jdate = datetime.date.fromisoformat(str(paid_on)[:10])
    except ValueError:
        jdate = _zt_today()
    bt = _build(run, new, jdate, ref, user=user)
    if bt is not None:
        _save_journaled(run.pk, add={f"{l['payslip']}:{l['method']}": {'batch': bt.pk, 'amount': str(l['amount'])} for l in new})
    return bt


def on_run_paid(run, user=None):
    """The whole run set to Paid: journal every part not journaled yet (date = the run's payment date, else today)."""
    if not enabled():
        return None
    try:
        from .run_payments import lines as _lines
        ls = [l for l in _lines(run) if l['state'] != 'held']
    except Exception:
        return None
    ref = ''
    try:
        c = M('PayrollPlus', 'RunControl').objects.filter(run_id=run.pk).first()
        ref = (c.payment_ref or '') if c else ''
    except Exception:
        pass
    jdate = run.payment_date or _zt_today()
    return on_marked(run, ls, jdate.isoformat(), ref or f'Salaries {run.year}-{run.month:02d}', user=user)
