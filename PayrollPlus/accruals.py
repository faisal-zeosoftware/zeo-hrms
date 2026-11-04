# v1.62.7 (item 18): new module – accrue now, pay later
"""v1.62.7 (item 18) – "Accrue now – pay later" salary components (asked by a client for on-call / shift allowance).

A salary component with this setting is worked out by payroll every month as usual (fixed amount, formula, monthly input …), but the
amount is ACCRUED – the component is of type Others, so it is not in the month's gross or net pay – and kept as a per-employee
balance (AccrualEntry). The balance is paid
  * in the payroll run of the payout month (e.g. January – the run's own salary month, whatever its attendance cut-off): an
    automatic addition line "<component> – payout" (a component made by ZEO), and / or
  * with the final settlement: a line "<component> – accrued, paid at exit" = balance + the amount of the days after the last payroll.
An opening balance per employee can be entered for the go-live. A deleted / recalculated payroll run takes its accruals and payouts
back (the rows of its payslips are deleted with them); a rejected payslip's rows do not count.

Journals (ZeoAccounting): the monthly payroll journal posts the accrued amount Dr expense / Cr the accrual account (default 224005 –
"others" components were already posted Dr expense / Cr liability); the payout line Dr accrual account / Cr salaries payable; the
final settlement Dr accrual account for the balance part, Dr expense for the days after the last payroll.

The per-day rate / formula is the company's own: the components use ZEO's normal formula and monthly-input machinery.

Settings and balances live in PayrollPlus (AccrualSetting, AccrualEntry – PayrollPlus migration 0015), not in the core tables.
"""
import logging
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Sum

from Translations.i18n import tr

from .models import AccrualEntry, AccrualSetting

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
Q2 = Decimal('0.01')
DEFAULT_ACCOUNT = '224005'
NOT_ACCRUABLE = ('basic', 'loan', 'advance_salary', 'gratuity', 'pf', 'esi', 'tax')   # v1.62.7 (item 18)


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


def q2(v):
    return Decimal(str(v or 0)).quantize(Q2)


def month_of(run):
    """The salary month of a payroll run (its own month – a run with a 29th–28th cut-off is still the January run)."""
    return date(int(run.year), int(run.month), 1)


def _gkey(c):
    return (c.code or '').strip().upper() or f'#{c.pk}'


def twins(comp):
    """The component and its copies in other branches (same code)."""
    SC = M('PayrollManagement', 'SalaryComponent')
    if not (comp.code or '').strip():
        return [comp]
    return list(SC.objects.filter(code__iexact=comp.code.strip()).order_by('pk'))


def all_settings():
    """{component id: AccrualSetting} – every component that is (or was) accrued."""
    return {s.component_id: s for s in AccrualSetting.objects.all()}


def payout_ids():
    return set(AccrualSetting.objects.exclude(payout_component_id__isnull=True).values_list('payout_component_id', flat=True))


def _rejected():
    return M('PayrollManagement', 'Payslip').objects.filter(status__iexact='rejected').values('pk')


def entries(emp_id=None, comp_ids=None):
    """The entries that count: those of a rejected payslip are left out (it paid nothing)."""
    qs = AccrualEntry.objects.exclude(payslip_id__in=_rejected())
    if emp_id is not None:
        qs = qs.filter(employee_id=emp_id)
    if comp_ids is not None:
        qs = qs.filter(component_id__in=list(comp_ids))
    return qs


def balance(emp_id, comp_ids, upto=None, exclude_payslip=None):
    qs = entries(emp_id, comp_ids)
    if upto is not None:
        qs = qs.filter(month__lte=upto)
    if exclude_payslip is not None:
        qs = qs.exclude(payslip_id=exclude_payslip)
    return q2(qs.aggregate(t=Sum('amount'))['t'])


# ------------------------------------------------------------------ the setting of a component
def setting_row(comp, s=None):
    s = s if s is not None else AccrualSetting.objects.filter(component_id=comp.pk).first()
    SC = M('PayrollManagement', 'SalaryComponent')
    po = SC.objects.filter(pk=s.payout_component_id).first() if s and s.payout_component_id else None
    return {'component_id': comp.pk, 'code': comp.code or '', 'name': comp.name, 'type': comp.component_type,
            'enabled': bool(s and s.enabled), 'payout_month': s.payout_month if s else None,
            'include_payout_month': s.include_payout_month if s else True, 'pay_at_exit': s.pay_at_exit if s else True,
            'accrual_account': (s.accrual_account if s else '') or DEFAULT_ACCOUNT, 'previous_type': (s.previous_type if s else '') or '',
            'payout_component': {'id': po.pk, 'name': po.name, 'code': po.code} if po else None, 'known': s is not None,
            'updated_at': s.updated_at if s else None}


def _payout_component(comp):
    """The addition component of the payout line, made once per branch copy (like Air Ticket Encashment)."""
    SC = M('PayrollManagement', 'SalaryComponent')
    base = ((comp.code or f'ACR{comp.pk}').strip()[:15] + '_PAY').upper()
    name = f'{comp.name} – payout'[:100]
    found = SC.objects.filter(code=base, branch_id=comp.branch_id).first()
    if found is not None and found.component_type == 'addition':
        return found
    code, n = base, 1
    while SC.objects.filter(code=code, branch_id=comp.branch_id).exists() or SC.objects.filter(name=name, branch_id=comp.branch_id).exists():
        n += 1
        code = f'{base[:17]}{n}'
        name = f'{comp.name} – payout {n}'[:100]
    return SC.objects.create(name=name, code=code, branch_id=comp.branch_id, component_type='addition', payroll_category='other_allowance',
                             component_value_type='variable', formula='', show_in_payslip=True,
                             description=f'v1.62.7: payout of the accrued {comp.name} (made by ZEO – accrue now, pay later)')


@transaction.atomic
def save_setting(comp, data, user=None):
    """Turn "Accrue now – pay later" on / off for the component and all its branch copies (same code)."""
    enabled = str(data.get('enabled')).lower() in ('1', 'true', 'yes', 'on')
    pm = data.get('payout_month')
    try:
        pm = int(pm) if pm not in (None, '', 'null', 0, '0') else None
    except (TypeError, ValueError):
        raise Refused(tr('Choose the payout month.'), problems={'payout_month': tr('Choose a month.')})
    if pm is not None and not 1 <= pm <= 12:
        raise Refused(tr('Choose the payout month.'), problems={'payout_month': tr('Choose a month.')})
    at_exit = str(data.get('pay_at_exit', True)).lower() in ('1', 'true', 'yes', 'on')
    inc = str(data.get('include_payout_month', True)).lower() in ('1', 'true', 'yes', 'on')
    acct = str(data.get('accrual_account') or '').strip()[:40] or DEFAULT_ACCOUNT
    cur = AccrualSetting.objects.filter(component_id=comp.pk).first()
    prev = (cur.previous_type if cur else '') or comp.component_type
    if enabled:
        if prev == 'deduction':
            raise Refused(tr('A deduction cannot be accrued – only an earning (addition or others).'))
        if comp.payroll_category in NOT_ACCRUABLE:
            raise Refused(tr('This payroll category cannot be accrued.'))
        if comp.pk in payout_ids():
            raise Refused(tr('This is the payout line of an accrued component.'))
        if pm is None and not at_exit:
            raise Refused(tr('Choose a payout month or pay the balance at the final settlement (or both).'),
                          problems={'payout_month': tr('Choose a month.')})
    SC = M('PayrollManagement', 'SalaryComponent')
    out = []
    for c in twins(comp):
        s = AccrualSetting.objects.filter(component_id=c.pk).first()
        if s is None:
            if not enabled:
                continue
            s = AccrualSetting(component_id=c.pk)
        if enabled:
            if c.component_type != 'others':
                s.previous_type = c.component_type
                SC.objects.filter(pk=c.pk).update(component_type='others')   # no save(): the formula is not worked out again
            if pm is not None and not s.payout_component_id:
                s.payout_component_id = _payout_component(c).pk
        else:
            if c.component_type == 'others' and s.previous_type:   # paid monthly again from the next run; the balance stays
                SC.objects.filter(pk=c.pk).update(component_type=s.previous_type)
        s.enabled = enabled
        s.payout_month = pm
        s.include_payout_month = inc
        s.pay_at_exit = at_exit
        s.accrual_account = acct
        s.updated_by_id = getattr(user, 'pk', None)
        s.save()
        out.append(s)
    comp.refresh_from_db()
    return setting_row(comp)


# ------------------------------------------------------------------ payroll
def on_payslip(run, payslip, employee, skip_events=False):
    """Called by _process_payroll before the payslip totals: the month's accrued amounts are noted; in the payout month the balance
    is put on the payslip as the payout line. Returns {'accrued': x, 'paid': y}."""
    sets = all_settings()
    if not sets:
        return {'accrued': ZERO, 'paid': ZERO}
    PC = M('PayrollManagement', 'PayslipComponent')
    SC = M('PayrollManagement', 'SalaryComponent')
    month = month_of(run)
    acc_total, paid_total = ZERO, ZERO
    on = [i for i, s in sets.items() if s.enabled]
    for pc in PC.objects.filter(payslip=payslip, component_id__in=on).select_related('component'):
        amt = q2(pc.amount)
        if not amt:
            continue
        AccrualEntry.objects.create(employee_id=employee.pk, component_id=pc.component_id, kind='accrual', month=month, amount=amt,
                                    payslip_id=payslip.pk, run_id=run.pk, note=f'{pc.component.name} {month:%m/%Y}'[:255])
        acc_total += amt
    if skip_events:   # a second run of the month (pay calendar / off-cycle) never pays the balance
        return {'accrued': acc_total, 'paid': paid_total}
    due = [s for s in sets.values() if s.payout_month == int(run.month)]
    if not due:
        return {'accrued': acc_total, 'paid': paid_total}
    comps = {c.pk: c for c in SC.objects.filter(pk__in=[s.component_id for s in due])}
    groups = {}
    for s in due:
        c = comps.get(s.component_id)
        if c is None:
            continue
        rank = 0 if c.branch_id == employee.emp_branch_id_id else (1 if c.branch_id is None else 2)
        g = groups.setdefault(_gkey(c), {'ids': [], 'pick': None})
        g['ids'].append(c.pk)
        if g['pick'] is None or rank < g['pick'][0]:
            g['pick'] = (rank, s, c)
    for key, g in groups.items():
        _r, s, c = g['pick']
        bal = balance(employee.pk, g['ids'], exclude_payslip=None if s.include_payout_month else payslip.pk)
        if bal <= 0:
            continue
        if not s.payout_component_id:
            s.payout_component_id = _payout_component(c).pk
            s.save(update_fields=['payout_component_id'])
        po = SC.objects.filter(pk=s.payout_component_id).first()
        if po is None:
            log.warning('accrual payout component %s of %s missing', s.payout_component_id, c.pk)
            continue
        pc, made = PC.objects.get_or_create(payslip=payslip, component=po, defaults={'amount': bal})
        if not made:
            pc.amount = q2(pc.amount) + bal
            pc.save(update_fields=['amount'])
        AccrualEntry.objects.create(employee_id=employee.pk, component_id=c.pk, kind='payout', month=month, amount=-bal,
                                    payslip_id=payslip.pk, run_id=run.pk, note=f'{po.name} {month:%m/%Y}'[:255])
        paid_total += bal
    return {'accrued': acc_total, 'paid': paid_total}


def payslip_deleted(payslip):
    """A deleted payslip (run deleted / recalculated) accrued and paid nothing."""
    return AccrualEntry.objects.filter(payslip_id=payslip.pk).delete()[0]


# ------------------------------------------------------------------ final settlement
def _period_amount(emp, comp, row, a, b, ai=None):
    """The component's amount for the days after the last payroll (as payroll works it out): a fixed amount for the salary days
    a–b, a formula / monthly input for the days after the last payroll's attendance cut-off ai–b."""
    ai = ai or a
    if row is None or not b:
        return ZERO, ''
    if comp.component_value_type == 'fixed':
        if not a or b < a:
            return ZERO, ''
        from .services import _salary_for
        amt, days = _salary_for(q2(row.amount), a, b)
        return q2(amt), tr('{0:,.2f} a month for {1} days on the 30-day basis', q2(row.amount), days)
    if not ai or b < ai:
        return ZERO, ''
    if (comp.formula or '').strip():
        from PayrollManagement.signals import get_formula_variables
        from PayrollManagement.utils import evaluate_formula
        v = get_formula_variables(emp, ai, b)
        return q2(evaluate_formula(comp.formula, dict(v), emp, comp) or 0), tr('{0}: {1} (as in payroll)', comp.name, comp.formula)
    return q2(row.amount), tr('monthly input {0:,.2f}', q2(row.amount))


def exit_lines(st, emp, a, b, add, info, ai=None):
    """Lines of the final settlement: per accrued component the balance + the amount of the days after the last payroll."""
    sets = all_settings()
    if not sets:
        return
    SC = M('PayrollManagement', 'SalaryComponent')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    comps = {c.pk: c for c in SC.objects.filter(pk__in=list(sets))}
    rows = {r.component_id: r for r in ESS.objects.filter(employee=emp, is_active=True, component_id__in=list(sets))}
    have = set(entries(emp.pk, list(sets)).values_list('component_id', flat=True))
    groups = {}
    for cid, c in comps.items():
        g = groups.setdefault(_gkey(c), {'ids': [], 'pick': None})
        g['ids'].append(cid)
        mine = cid in rows
        rank = (0 if mine else 2) + (0 if c.branch_id == emp.emp_branch_id_id else 1)
        if g['pick'] is None or rank < g['pick'][0]:
            g['pick'] = (rank, cid)
    for key, g in groups.items():
        cid = g['pick'][1]
        c, s = comps[cid], sets[cid]
        if not (set(g['ids']) & (have | set(rows))):
            continue
        bal = balance(emp.pk, g['ids'])
        cur, how = ZERO, ''
        if s.enabled and cid in rows:
            try:
                cur, how = _period_amount(emp, c, rows[cid], a, b, ai)
            except Exception:
                log.exception('accrued component %s for the days after the last payroll', cid)
        total = q2(bal + cur)
        calc = [tr('accrued balance {0:,.2f}', bal)]
        if cur:
            calc.append(tr('+ {0:%d/%m/%Y} – {1:%d/%m/%Y}: {2:,.2f} ({3})', ai or a, b, cur, how))
        calc.append(tr('= {0:,.2f}', total))
        if not total:
            continue
        if s.pay_at_exit:
            add(f'accrual_{cid}', tr('{0} – accrued, paid at exit', c.name), 'earning', total,
                tr('balance {0:,.2f} + days after the last payroll {1:,.2f}', bal, cur), 'accrual', calc,
                component_id=cid, from_balance=float(bal), this_period=float(cur), account=s.accrual_account or DEFAULT_ACCOUNT)
        else:
            info(f'accrual_{cid}', tr('{0} – accrued {1:,.2f}, not paid at exit (component setting)', c.name, total), '', 'accrual')


def exit_paid(st, user=None):
    """The paid settlement takes the balance to 0: the days after the last payroll are accrued, the line amount paid."""
    AccrualEntry.objects.filter(settlement_id=st.pk).delete()
    lwd = st.last_working_date or date.today()
    m = date(lwd.year, lwd.month, 1)
    n = 0
    for ln in (st.lines or []):
        if ln.get('kind') != 'earning' or not str(ln.get('key') or '').startswith('accrual_') or not ln.get('component_id'):
            continue
        cid = int(ln['component_id'])
        cur = q2(ln.get('this_period'))
        note = tr('Final settlement {0}', st.number or st.pk)[:255]
        if cur:
            AccrualEntry.objects.create(employee_id=st.employee_id, component_id=cid, kind='accrual', month=m, amount=cur,
                                        settlement_id=st.pk, note=note, created_by_id=getattr(user, 'pk', None))
        AccrualEntry.objects.create(employee_id=st.employee_id, component_id=cid, kind='exit', month=m, amount=-q2(ln.get('amount')),
                                    settlement_id=st.pk, note=note, created_by_id=getattr(user, 'pk', None))
        n += 1
    return n


# ------------------------------------------------------------------ opening balances, balances, report
@transaction.atomic
def set_opening(emp_id, comp, amount, as_of, note='', user=None):
    if comp.pk not in all_settings():
        raise Refused(tr('Turn on "Accrue now – pay later" for the component first.'))
    ids = [c.pk for c in twins(comp)]
    AccrualEntry.objects.filter(employee_id=emp_id, component_id__in=ids, kind='opening').delete()
    amount = q2(amount)
    if not amount:
        return None
    return AccrualEntry.objects.create(employee_id=emp_id, component_id=comp.pk, kind='opening', month=as_of, amount=amount,
                                       note=(note or tr('Opening balance'))[:255], created_by_id=getattr(user, 'pk', None))


def summary(date_from=None, date_to=None, emp_ids=None):
    """[{employee_id, component_id (group's first), group, opening, accrued, paid, paid_exit, balance}] – opening = balance before
    date_from plus the go-live opening balances inside the period."""
    SC = M('PayrollManagement', 'SalaryComponent')
    sets = all_settings()
    comps = {c.pk: c for c in SC.objects.filter(pk__in=list(sets) + list(AccrualEntry.objects.values_list('component_id', flat=True).distinct()))}
    qs = entries()
    if emp_ids is not None:
        qs = qs.filter(employee_id__in=list(emp_ids))
    if date_to:
        qs = qs.filter(month__lte=date_to)
    out = {}
    for e in qs.values('employee_id', 'component_id', 'kind', 'month', 'amount'):
        c = comps.get(e['component_id'])
        gk = _gkey(c) if c else f"#{e['component_id']}"
        r = out.get((e['employee_id'], gk))
        if r is None:
            r = out[(e['employee_id'], gk)] = {'employee_id': e['employee_id'], 'component_id': e['component_id'], 'group': gk,
                                               'component': c.name if c else f"#{e['component_id']}", 'code': (c.code if c else '') or '',
                                               'opening': ZERO, 'accrued': ZERO, 'paid': ZERO, 'paid_exit': ZERO, 'adjust': ZERO, 'balance': ZERO}
        amt = Decimal(str(e['amount']))
        before = date_from and e['month'] < date_from
        if before or e['kind'] == 'opening':
            r['opening'] += amt
        elif e['kind'] == 'accrual':
            r['accrued'] += amt
        elif e['kind'] == 'payout':
            r['paid'] -= amt
        elif e['kind'] == 'exit':
            r['paid_exit'] -= amt
        else:
            r['adjust'] += amt
        r['balance'] += amt
    return list(out.values())
