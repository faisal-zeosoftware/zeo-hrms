"""v1.62.2 payroll guards (items 41, 50, 51) used by PayrollManagement.signals._process_payroll.

item 41  one source per statutory contribution: a pension formula component (formula with pension_employee_rate /
         pension_employer_rate, e.g. GPSSA "pension_salary * pension_employee_rate * is_national") is not deducted when the
         employee also has the component of the CountryPack statutory scheme for the same contribution (ST_GPSSA, formula
         stat_gpssa_emp). The statutory scheme wins (rates by date: Payroll › Statutory schemes / Legal rates); the pension line
         is left off the payslip and noted as replaced. The rates themselves are not changed.
item 50  formula components in dependency order; a formula that uses another formula component sees that component's amount
         on this payslip (after its pro-rating) – it saw the first-pass full-month value (joiner: 1,890 instead of 945). A
         formula that is itself pro-rated ("Deduct leave") keeps the full-month values, so it is pro-rated once.
item 51  Payroll controls › net pay (company setting, off by default): "Keep net pay at or above 0 – carry the rest of the
         deductions to next month" and an optional maximum of the deductions as % of gross (empty = no limit). When on,
         deductions that do not fit are taken in the order statutory → loans → advances → others; the rest is carried to the
         next payslip on the line "Deductions carried over" (DEDCF), never lost. Off: nothing changes; a negative net pay is
         shown as a warning on the payslip and in the payroll register.

Notes of a payslip (replaced components, carried deductions, negative net) and the carry-overs are kept in the company's
CompanyOption store (JSON) – no new column in the core apps.
"""
import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps

logger = logging.getLogger(__name__)
M = apps.get_model
Q2 = Decimal('0.01')
SETTING_KEY = 'payroll_net_pay_controls'
CARRY_CODE = 'DEDCF'
CARRY_NAME = 'Deductions carried over'
_NOTES = 'v1622_payslip_notes:{}'
_CARRY = 'v1622_carry:{}'


def _d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:
        return Decimal('0')


def _opt(key, default=None):
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=key).first()
        return o.value if o is not None else default
    except Exception:
        return default


def _set_opt(key, value):
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=key, defaults={'value': value})


# ------------------------------------------------------------------ settings (item 51)
def net_settings():
    """{'keep_net_positive': bool, 'max_deduction_pct': Decimal | None} – off / no limit unless the company set them."""
    v = _opt(SETTING_KEY, {}) or {}
    pct = v.get('max_deduction_pct')
    try:
        pct = Decimal(str(pct)) if pct not in (None, '') else None
    except Exception:
        pct = None
    return {'keep_net_positive': bool(v.get('keep_net_positive')), 'max_deduction_pct': pct}


def save_net_settings(keep=None, pct='__keep__'):
    cur = _opt(SETTING_KEY, {}) or {}
    if keep is not None:
        cur['keep_net_positive'] = bool(keep)
    if pct != '__keep__':
        cur['max_deduction_pct'] = None if pct in (None, '') else str(Decimal(str(pct)).quantize(Q2))
    _set_opt(SETTING_KEY, cur)
    return net_settings()


# ------------------------------------------------------------------ notes of a payslip
def notes_of(payslip_id):
    return _opt(_NOTES.format(payslip_id), {}) or {}


def _add_note(payslip_id, kind, item):
    n = notes_of(payslip_id)
    n.setdefault(kind, [])
    if item not in n[kind]:
        n[kind].append(item)
    _set_opt(_NOTES.format(payslip_id), n)


def warnings_of(payslip):
    """Texts for the payslip / register: negative net, replaced components, carried deductions (English; the screen translates)."""
    from Translations.i18n import tr
    out = []
    net = _d(getattr(payslip, 'net_salary', 0))
    if net < 0:
        out.append(tr('Net pay is negative ({0}): the deductions are more than the pay. It cannot be paid like this – '
                      'reduce or move a deduction, or switch on "Keep net pay at or above 0" in Payroll controls.', f'{net:,.2f}'))
    n = notes_of(payslip.pk)
    for r in n.get('replaced', []):
        out.append(tr('{0} was not deducted: {1} (statutory scheme) already takes this contribution.', r.get('name', ''), r.get('by', '')))
    for c in n.get('carried', []):
        out.append(tr('Deductions of {0} did not fit and are carried to the next payslip.', f"{_d(c.get('amount')):,.2f}"))
    for r in n.get('requests', []):   # v1.62.11 (item 257): one line pays several requests of the same type
        out.append(tr('{0} pays {1} requests: {2}.', r.get('name', ''), len(r.get('refs') or []), ', '.join(r.get('refs') or [])))
    for c in n.get('loan_carry', []):   # v1.62.11 (item 259): a loan balance left after the last planned instalment
        out.append(tr('Loan balance carried on: {0} left', f"{_d(c.get('left')):,.2f}") + f" ({c.get('loan', '')})")
    for r in n.get('recoveries', []):   # v1.63.0 (item 28): each recovery instalment and the balance left
        out.append(tr('Recovery {0} ({1}): {2} deducted, {3} left.', r.get('ref', ''), r.get('name', ''), f"{_d(r.get('amount')):,.2f}",
                      f"{_d(r.get('left')):,.2f}"))
        if _d(r.get('carried')) > 0:
            out.append(tr('Recovery {0}: {1} did not fit this month ({2}) and is carried to next month.', r.get('ref', ''),
                          f"{_d(r.get('carried')):,.2f}", tr('monthly cap') if 'cap' in (r.get('why') or []) and 'net' not in (r.get('why') or [])
                          else tr('net pay controls')))
    return out


# ------------------------------------------------------------------ item 41: one source per contribution
def _stat_schemes():
    try:
        from PayrollPlus.statutory import active_schemes
        return [s for s in active_schemes() if (s.code or '').lower() == 'gpssa' or (s.authority or '').strip().upper() == 'GPSSA']
    except Exception:
        return []


def pension_duplicates(rows):
    """{component pk of a pension formula row: statutory component it duplicates} for the employee's rows."""
    schemes = _stat_schemes()
    if not schemes:
        return {}
    out = {}
    formula_of = {r.component_id: (r.component.formula or '') for r in rows}
    for s in schemes:
        emp_st = next((r.component for r in rows if re.search(rf'\bstat_{re.escape(s.code)}_emp\b', formula_of[r.component_id])), None)
        er_st = next((r.component for r in rows if re.search(rf'\bstat_{re.escape(s.code)}_er\b', formula_of[r.component_id])), None)
        for r in rows:
            f = formula_of[r.component_id]
            if emp_st is not None and r.component_id != emp_st.pk and re.search(r'\bpension_employee_rate\b', f):
                out[r.component_id] = emp_st
            elif er_st is not None and r.component_id != er_st.pk and re.search(r'\bpension_employer_rate\b', f):
                out[r.component_id] = er_st
    return out


def note_replaced(payslip, comp, by):
    _add_note(payslip.pk, 'replaced', {'component_id': comp.pk, 'name': comp.name, 'code': comp.code or '', 'by': by.name, 'by_code': by.code or ''})


def duplicate_setup():
    """[(pension component, statutory component)] of the company – for the payroll audit / setup guide warning."""
    schemes = _stat_schemes()
    if not schemes:
        return []
    SC = M('PayrollManagement', 'SalaryComponent')
    out = []
    pens = [c for c in SC.objects.exclude(formula__isnull=True).exclude(formula='') if re.search(r'\bpension_employ(ee|er)_rate\b', c.formula or '')]
    for s in schemes:
        st = SC.objects.filter(formula__regex=rf'stat_{s.code}_emp').first()
        if st is not None:
            out += [(c, st) for c in pens if 'pension_employee_rate' in (c.formula or '')]
    return out


# ------------------------------------------------------------------ item 50: dependency order
def payroll_order(rows):
    """Fixed / input rows first (as stored), then the formula rows in dependency order (circles last, as before paid as 0)."""
    from .formula import order_components
    plain = [r for r in rows if not (r.component.component_value_type != 'fixed' and (r.component.formula or '').strip())]
    frm = [r for r in rows if r not in plain]
    order, cycle = order_components([r.component for r in frm])
    rank = {c.pk: i for i, c in enumerate(order)}
    frm.sort(key=lambda r: rank.get(r.component_id, len(rank)))
    return plain + frm


def names_of(comp):
    out = []
    if comp.code:
        out.append(comp.code)
    s = re.sub(r'_+', '_', re.sub(r'[^a-zA-Z0-9_]', '_', str(comp.name or '').strip())).strip('_').lower()
    if s:
        out.append(s)
    return out


# ------------------------------------------------------------------ item 51: net pay floor and carry-over
def _kind(comp):
    f = comp.formula or ''
    cat = comp.payroll_category
    if cat in ('pf', 'esi', 'tax') or re.search(r'\b(stat_\w+_emp|statutory_employee_total|pension_\w+)\b', f):
        return 0, 'statutory'
    if cat == 'loan':
        return 1, 'loan'
    if cat == 'advance_salary':
        return 2, 'advance'
    return 3, 'other'


def _carry_component(employee):
    SC = M('PayrollManagement', 'SalaryComponent')
    br = employee.emp_branch_id_id
    c = SC.objects.filter(code=CARRY_CODE, branch_id=br).first() or SC.objects.filter(code=CARRY_CODE, branch__isnull=True).first()
    if c is None:
        c = SC.objects.create(name=CARRY_NAME, code=CARRY_CODE, branch_id=br, component_type='deduction', payroll_category='other_allowance',
                              component_value_type='variable', show_in_payslip=True, deduct_leave=False,
                              description='v1.62.2 – deductions that did not fit in an earlier payslip (Payroll controls › net pay)')
    return c


def _carries(employee_id):
    """The carry-overs of the employee, tidied: those made by a payslip that no longer exists are dropped (that payslip will be
    calculated again); those taken by a payslip that no longer exists wait again."""
    P = M('PayrollManagement', 'Payslip')
    v = _opt(_CARRY.format(employee_id), {}) or {}
    items = v.get('items', [])
    ids = {x.get('from_payslip') for x in items} | {x.get('included_payslip') for x in items if x.get('included_payslip')}
    alive = set(P.objects.filter(pk__in=[i for i in ids if i]).values_list('pk', flat=True))
    out = []
    for x in items:
        if x.get('from_payslip') not in alive:
            continue
        if x.get('status') == 'included' and x.get('included_payslip') not in alive:
            x['status'], x['included_payslip'] = 'pending', None
        out.append(x)
    return out


def apply(payslip, employee):
    """Before the totals: take the waiting carry-overs, then keep the deductions inside the limit when the setting is on."""
    PC = M('PayrollManagement', 'PayslipComponent')
    st = net_settings()
    items = _carries(employee.pk)
    pending = [x for x in items if x.get('status') == 'pending' and x.get('from_payslip') != payslip.pk]
    if pending:
        amt = sum((_d(x.get('amount')) for x in pending), Decimal('0')).quantize(Q2)
        if amt > 0:
            comp = _carry_component(employee)
            pc, made = PC.objects.get_or_create(payslip=payslip, component=comp, defaults={'amount': amt})
            if not made:
                pc.amount = _d(pc.amount) + amt
                pc.save(update_fields=['amount'])
            for x in pending:
                x['status'], x['included_payslip'] = 'included', payslip.pk
    carried = None
    if st['keep_net_positive']:
        lines = list(PC.objects.filter(payslip=payslip).select_related('component'))
        gross = sum((_d(x.amount) for x in lines if x.component.show_in_payslip and x.component.component_type == 'addition'), Decimal('0'))
        ded = [x for x in lines if x.component.show_in_payslip and x.component.component_type == 'deduction' and _d(x.amount) > 0]
        total = sum((_d(x.amount) for x in ded), Decimal('0'))
        room = max(gross, Decimal('0'))
        if st['max_deduction_pct'] is not None:
            room = min(room, (gross * st['max_deduction_pct'] / 100).quantize(Q2, ROUND_HALF_UP))
        if total > room:
            ded.sort(key=lambda x: (_kind(x.component)[0], x.pk))
            left, out_lines = room, []
            for x in ded:
                a = _d(x.amount)
                take = min(a, max(left, Decimal('0')))
                left -= take
                if take < a:
                    out_lines.append({'component_id': x.component_id, 'name': x.component.name, 'kind': _kind(x.component)[1],
                                      'amount': str((a - take).quantize(Q2))})
                    x.amount = take.quantize(Q2)
                    x.save(update_fields=['amount'])
            amt = sum((_d(o['amount']) for o in out_lines), Decimal('0')).quantize(Q2)
            if amt > 0:
                run = payslip.payroll_run
                carried = {'from_payslip': payslip.pk, 'month': f'{run.year}-{int(run.month):02d}', 'amount': str(amt), 'lines': out_lines,
                           'status': 'pending', 'included_payslip': None}
                items.append(carried)
                _add_note(payslip.pk, 'carried', {'amount': str(amt), 'lines': out_lines})
    if items or _opt(_CARRY.format(employee.pk)) is not None:   # no row for employees without carry-overs
        _set_opt(_CARRY.format(employee.pk), {'items': items})
    return carried
