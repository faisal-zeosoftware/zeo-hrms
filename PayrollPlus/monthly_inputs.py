# v1.62.11 (item 18b): new module – monthly inputs grid (days / hours per employee for the payroll month)
"""v1.62.11 (item 18b) – Payroll › Monthly inputs.

A monthly input is a salary component of value type "variable" without a formula (e.g. on-call days, shift days, overtime hours):
HR enters the figure of the month per employee and the formulas of other components use it by its code (V27OC = V27OCD * 9.85).
Payroll reads the figure from the employee's salary row (EmployeeSalaryStructure.amount) and sets it back to 0 after the payslip
(PayrollManagement/signals.py "RESET MANUAL VARIABLE COMPONENTS", v1.62.2 item 44 – a deleted / recalculated payslip puts the figure
back when the row is still 0). This module does not change that: the grid and the import write exactly that amount.

Which components are columns
  every variable component without a formula whose code (or name alias) is used by a formula of another component, plus the ones
  HR marks as "Monthly input" (company setting CompanyOption 'v16211_monthly_inputs' = {"on": [CODE], "off": [CODE]} – no new column
  in the core table); "off" hides an auto-found one. Per code: the branch copy of the employee's branch (or the all-branch copy).

Which month can be entered
  The figure is used by the employee's next payslip, so the grid refuses
    * a locked period (v1.62.11 item 231 – PayrollPlus.payctl.locked_month) and a month whose payroll run of the branch is paid;
    * per employee: a month that already has the employee's payslip (shown read-only with the figures that payslip used – delete /
      recalculate the run to change them), a month before a later payslip, and a month after a payroll month that has not been
      run yet (that run would use the figures).

Preview: the amounts of the formula components that use the inputs, worked out with the payroll formula engine
(get_formula_variables for the month + the typed figures), e.g. 10 on-call days → 98.50.
"""
import io
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from django.db.models import Q

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
Q2 = Decimal('0.01')
OPTION_KEY = 'v16211_monthly_inputs'
MAX_VALUE = Decimal('99999999.99')   # EmployeeSalaryStructure.amount max_digits=10, decimal_places=2
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


def q2(v):
    return Decimal(str(v or 0)).quantize(Q2)


def first_of(d):
    return date(d.year, d.month, 1)


def next_month(m):
    return date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def month_end(m):
    return next_month(m) - timedelta(days=1)


def month_label(m):
    return f'{tr(MONTHS[m.month - 1])} {m.year}'


def parse_month(v):
    s = str(v or '').strip()
    try:
        if len(s) == 7:
            y, mm = s.split('-')
            return date(int(y), int(mm), 1)
        return first_of(date.fromisoformat(s[:10]))
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------------ which components are monthly inputs
def _flags():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=OPTION_KEY).first()
        v = dict(o.value or {}) if o is not None else {}
    except Exception:
        v = {}
    return {'on': {str(x).upper() for x in v.get('on') or []}, 'off': {str(x).upper() for x in v.get('off') or []}}


SYSTEM_CATEGORIES = ('loan', 'advance_salary', 'air_ticket', 'gratuity', 'pf', 'esi', 'tax')   # lines ZEO writes itself
SYSTEM_CODES = ('ARREARS', 'HOLDREL', 'ATENC')


def _system_ids():
    """Components ZEO fills itself (accrual payout lines) – never a monthly input."""
    try:
        from .accruals import payout_ids
        return payout_ids()
    except Exception:
        return set()


def _is_plain_input(c, system=None):
    if c.component_value_type != 'variable' or (c.formula or '').strip():
        return False
    if c.payroll_category in SYSTEM_CATEGORIES or (c.code or '').strip().upper() in SYSTEM_CODES:
        return False
    return c.pk not in (system if system is not None else _system_ids())


def _used_names():
    """Names (upper case) used by the formulas of the company's components."""
    from PayrollManagement.formula import names_in
    out = set()
    SC = M('PayrollManagement', 'SalaryComponent')
    for f in SC.objects.exclude(formula__isnull=True).exclude(formula='').values_list('formula', flat=True).distinct():
        names, _f, _e = names_in(f)
        out |= {n.upper() for n in names}
    return out


def _aliases(c):
    from PayrollManagement.formula import safe_name
    return {x.upper() for x in ((c.code or '').strip(), safe_name(c.name or '').lower()) if x}


def component_flag(c, used=None, flags=None, system=None):
    """{'eligible', 'auto' (used by a formula), 'marked' (company setting), 'monthly_input' (column on the grid)}"""
    flags = flags if flags is not None else _flags()
    if not _is_plain_input(c, system) or not (c.code or '').strip():
        return {'eligible': False, 'auto': False, 'marked': None, 'monthly_input': False}
    used = used if used is not None else _used_names()
    code = c.code.strip().upper()
    auto = bool(_aliases(c) & used)
    marked = True if code in flags['on'] else (False if code in flags['off'] else None)
    return {'eligible': True, 'auto': auto, 'marked': marked, 'monthly_input': marked if marked is not None else auto}


def save_flag(c, on, user=None):
    """Mark the component (all branch copies – same code) as a monthly input (True), not one (False) or automatic (None)."""
    if not _is_plain_input(c) or not (c.code or '').strip():
        raise Refused(tr('Only a variable component without a formula (and with a code) can be a monthly input.'))
    code = c.code.strip().upper()
    f = _flags()
    f['on'].discard(code)
    f['off'].discard(code)
    if on is True:
        f['on'].add(code)
    elif on is False:
        f['off'].add(code)
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=OPTION_KEY, defaults={'value': {
        'on': sorted(f['on']), 'off': sorted(f['off']), 'updated_by': getattr(user, 'pk', None)}})
    return component_flag(c)


def input_components(branch_id=None):
    """[{code, name, ids: {branch_id: component id}, auto, marked}] – the grid's columns, by code (branch copies together)."""
    SC = M('PayrollManagement', 'SalaryComponent')
    used, flags, system = _used_names(), _flags(), _system_ids()
    groups = {}
    qs = SC.objects.filter(component_value_type='variable').filter(Q(formula__isnull=True) | Q(formula='')).exclude(code__isnull=True).exclude(code='')
    for c in qs.order_by('name', 'pk'):
        if branch_id is not None and c.branch_id not in (None, branch_id):
            continue
        fl = component_flag(c, used, flags, system)
        if not fl['monthly_input']:
            continue
        k = c.code.strip().upper()
        g = groups.setdefault(k, {'code': c.code.strip(), 'name': c.name, 'ids': {}, 'auto': fl['auto'], 'marked': fl['marked'],
                                  'type': c.component_type, 'show_in_payslip': c.show_in_payslip})
        g['ids'][c.branch_id] = c.pk
        if c.branch_id == branch_id:
            g['name'] = c.name
    return sorted(groups.values(), key=lambda g: g['name'].lower())


def candidates():
    """Every component that could be a monthly input, with its flag (the column setup of the grid page)."""
    SC = M('PayrollManagement', 'SalaryComponent')
    used, flags, system = _used_names(), _flags(), _system_ids()
    out = {}
    qs = SC.objects.filter(component_value_type='variable').filter(Q(formula__isnull=True) | Q(formula='')).exclude(code__isnull=True).exclude(code='')
    for c in qs.select_related('branch').order_by('name', 'pk'):
        if not _is_plain_input(c, system):   # ZEO's own lines (loan, advance, arrears, payout lines …) are never offered
            continue
        k = c.code.strip().upper()
        r = out.get(k)
        if r is None:
            r = out[k] = {'component_id': c.pk, 'code': c.code.strip(), 'name': c.name, **component_flag(c, used, flags, system), 'branches': []}
        r['branches'].append(c.branch.branch_name if c.branch_id else tr('All branches'))
    return sorted(out.values(), key=lambda r: (not r['monthly_input'], r['name'].lower()))


# ------------------------------------------------------------------ which month / employee can be entered
def _locked(m, branch_id):
    try:
        from . import payctl
    except ImportError:
        return None
    fn = getattr(payctl, 'locked_month', None)
    if fn is None:
        return None
    lk = fn(m, branch_id)
    if lk is None:
        return None
    try:
        return payctl.lock_text(lk)
    except Exception:
        return tr('The {0} payroll period is locked.', month_label(m))


def _runs_of_branch(branch_id):
    """Payroll runs that cover the branch (the branch is on the run, or the run has no branch = every branch)."""
    PR = M('PayrollManagement', 'PayrollRun')
    return PR.objects.filter(Q(branch__id=branch_id) | Q(branch__isnull=True)).distinct()


def month_state(m, branch_id):
    """{'locked': text|None, 'paid': bool, 'runs': [...], 'editable': bool, 'reason': text}"""
    runs = [{'id': r.pk, 'name': r.name or f'{r.month:02d}/{r.year}', 'status': r.status}
            for r in _runs_of_branch(branch_id).filter(year=m.year, month=m.month).order_by('pk')]
    locked = _locked(m, branch_id)
    paid = any(r['status'] == 'paid' for r in runs)
    reason = locked or (tr('The {0} payroll is paid – its monthly inputs cannot be changed.', month_label(m)) if paid else '')
    return {'month': m.isoformat()[:7], 'label': month_label(m), 'locked': locked, 'paid': paid, 'runs': runs,
            'editable': not reason, 'reason': reason}


def _done_months(branch_id):
    """Months whose payroll of the branch has been run (a run processed / approved / paid)."""
    return {(y, mm) for y, mm in _runs_of_branch(branch_id).filter(status__in=('processed', 'approved', 'paid')).values_list('year', 'month')}


def _payslip_months(emp_ids):
    """{employee id: {(year, month): payslip id}} – the payslips that count (not rejected)."""
    P = M('PayrollManagement', 'Payslip')
    out = {}
    for pk, e, y, mm in (P.objects.filter(employee_id__in=list(emp_ids)).exclude(status__iexact='rejected')
                         .values_list('pk', 'employee_id', 'payroll_run__year', 'payroll_run__month')):
        out.setdefault(e, {})[(y, mm)] = pk
    return out


def row_state(m, slips, done):
    """('open'|'in_run'|'later'|'earlier_open', text, payslip id)"""
    key = (m.year, m.month)
    if key in slips:
        return 'in_run', tr('Already in the {0} payroll – delete or recalculate the run to change the figures.', month_label(m)), slips[key]
    if any(k > key for k in slips):
        last = max(slips)
        return 'later', tr('The {0} payroll of this employee is already made.', month_label(date(last[0], last[1], 1))), None
    if slips:
        x = next_month(date(*max(slips), 1))
        while x < m:
            if (x.year, x.month) not in done:   # that payroll comes first and would use the figures
                return 'earlier_open', tr('The {0} payroll comes first – it would use these figures. Enter them for {0}.', month_label(x)), None
            x = next_month(x)
    return 'open', '', None


def _consumed(payslip_id):
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=f'v1622_payslip_consumed:{payslip_id}').first()   # v1.62.2 (item 44)
        return {int(i['id']): str(q2(i.get('amount'))) for i in ((o.value or {}).get('inputs') or [])} if o else {}
    except Exception:
        return {}


# ------------------------------------------------------------------ the grid
def _employees(branch_id, allowed=None, m=None):
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.filter(is_active=True, emp_branch_id=branch_id)
    if m is not None:
        qs = qs.filter(Q(emp_joined_date__isnull=True) | Q(emp_joined_date__lte=month_end(m)))
    if allowed is not None:
        qs = qs.filter(pk__in=list(allowed))
    return list(qs.order_by('emp_code', 'pk'))


def emp_label(e):
    n = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
    return f'{n} ({e.emp_code})' if e.emp_code else n


def grid(m, branch_id, allowed=None):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    cols = input_components(branch_id)
    st = month_state(m, branch_id)
    emps = _employees(branch_id, allowed, m)
    slips = _payslip_months([e.pk for e in emps])
    done = _done_months(branch_id)
    comp_ids = {pk for c in cols for pk in c['ids'].values()}
    ess = {}
    for r in ESS.objects.filter(employee_id__in=[e.pk for e in emps], component_id__in=list(comp_ids)).order_by('-is_active', 'pk'):
        ess.setdefault((r.employee_id, r.component_id), r)
    rows = []
    for e in emps:
        state, note, ps = row_state(m, slips.get(e.pk, {}), done)
        used = _consumed(ps) if ps else {}
        vals, has = {}, {}
        for c in cols:
            cid = c['ids'].get(branch_id) or c['ids'].get(None)
            r = None
            for bid, pk in c['ids'].items():
                r = r or ess.get((e.pk, pk))
            has[c['code']] = cid is not None
            if state == 'in_run':
                vals[c['code']] = used.get(r.pk, '0.00') if r is not None else '0.00'
            else:
                vals[c['code']] = str(q2(r.amount)) if r is not None and r.is_active and r.amount is not None else '0.00'
        rows.append({'employee_id': e.pk, 'emp_code': e.emp_code or '', 'employee': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                     'state': state, 'note': note, 'editable': st['editable'] and state == 'open', 'values': vals, 'has': has})
    return {'month': st, 'columns': [{'code': c['code'], 'name': c['name'], 'auto': c['auto'], 'marked': c['marked']} for c in cols], 'rows': rows}


def _number(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return ZERO, None
    try:
        d = Decimal(str(v).replace(',', '').strip())
    except (InvalidOperation, ValueError, TypeError):
        return None, tr('Not a number.')
    if not d.is_finite():
        return None, tr('Not a number.')
    if d < 0:
        return None, tr('Enter 0 or more.')
    if d > MAX_VALUE:
        return None, tr('The number is too large.')
    if d != d.quantize(Q2):
        return None, tr('Use at most 2 decimals.')
    return q2(d), None


def _write(emp, comp_id, value):
    """The figure the payroll reads for the employee's next payslip (an .update – a monthly input is not a salary revision)."""
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    r = ESS.objects.filter(employee_id=emp.pk, component_id=comp_id).first()
    if r is None:
        if not value:
            return False
        ESS.objects.create(employee_id=emp.pk, component_id=comp_id, amount=value, is_active=True)
        return True
    if q2(r.amount) == value and r.is_active:
        return False
    ESS.objects.filter(pk=r.pk).update(amount=value, is_active=True)
    return True


def _check_rows(m, items, allowed=None, branch_id=None):
    """items: [{'key', 'employee': emp|None, 'code', 'value'}] → per item errors; returns (out, cols_by_branch)."""
    states, cols_b, slips_cache, done_b = {}, {}, {}, {}
    emp_ids = {it['employee'].pk for it in items if it.get('employee') is not None}
    slips = _payslip_months(emp_ids)
    seen = set()
    for it in items:
        errs = list(it.get('errors') or [])
        e = it.get('employee')
        if e is not None:
            b = e.emp_branch_id_id
            if allowed is not None and e.pk not in allowed:
                errs.append(tr('This employee is not in your branches.'))
            elif branch_id is not None and b != branch_id:
                errs.append(tr('The employee is not in the branch chosen.'))
            elif not e.is_active:
                errs.append(tr('The employee is not active.'))
            else:
                if b not in states:
                    states[b] = month_state(m, b)
                    cols_b[b] = {c['code'].upper(): c for c in input_components(b)}
                    done_b[b] = _done_months(b)
                if not states[b]['editable']:
                    errs.append(states[b]['reason'])
                else:
                    state, note, _ps = row_state(m, slips.get(e.pk, {}), done_b[b])
                    if state != 'open':
                        errs.append(note)
                code = str(it.get('code') or '').strip().upper()
                c = cols_b[b].get(code)
                if c is None:
                    errs.append(tr('{0} is not a monthly input.', it.get('code') or '–'))
                else:
                    cid = c['ids'].get(b) or c['ids'].get(None)
                    if cid is None:
                        errs.append(tr('{0} is not set up for the employee’s branch.', c['code']))
                    it['component_id'] = cid
                    k = (e.pk, code)
                    if k in seen:
                        errs.append(tr('The same employee and input are in the file twice.'))
                    seen.add(k)
        val, verr = _number(it.get('value'))
        if verr:
            errs.append(verr)
        it['amount'] = val
        it['errors'] = [x for x in errs if x]
    return items


@transaction.atomic
def save_grid(m, branch_id, rows, allowed=None, user=None):
    """rows: [{employee, values: {CODE: number}}] – all or nothing; returns {'saved': n, 'changed': n}."""
    E = M('EmpManagement', 'emp_master')
    st = month_state(m, branch_id)
    if not st['editable']:
        raise Refused(st['reason'], status=409)
    emps = {e.pk: e for e in E.objects.filter(pk__in=[int(r.get('employee') or 0) for r in rows if str(r.get('employee') or '').isdigit()])}
    items = []
    for i, r in enumerate(rows):
        e = emps.get(int(r.get('employee'))) if str(r.get('employee') or '').isdigit() else None
        base = {'row': i, 'employee': e, 'errors': [] if e else [tr('Employee not found.')]}
        for code, v in (r.get('values') or {}).items():
            items.append({**base, 'errors': list(base['errors']), 'code': code, 'value': v})
    _check_rows(m, items, allowed, branch_id)
    problems = {}
    for it in items:
        if it['errors']:
            k = str(it['employee'].pk) if it.get('employee') else f"row{it['row']}"
            problems.setdefault(k, {})[str(it.get('code'))] = ' '.join(it['errors'])
    if problems:
        raise Refused(tr('Some figures were not saved – check the marked cells.'), problems=problems)
    changed = sum(1 for it in items if _write(it['employee'], it['component_id'], it['amount']))
    return {'saved': len(items), 'changed': changed, 'month': st['month']}


# ------------------------------------------------------------------ preview (formula engine)
def preview(m, emp, values):
    """The formula components of the employee that use the inputs, worked out with the typed figures:
    {'lines': [{code, name, amount, accrued}], 'total': x}"""
    from PayrollManagement.formula import names_in, order_components, safe_name
    from PayrollManagement.signals import get_formula_variables
    from PayrollManagement.utils import evaluate_formula
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    cols = {c['code'].upper(): c for c in input_components(emp.emp_branch_id_id)}
    v = dict(get_formula_variables(emp, m, month_end(m)))
    given = set()
    for code, val in (values or {}).items():
        c = cols.get(str(code).upper())
        if c is None:
            continue
        d, err = _number(val)
        if err:
            continue
        v[c['code']] = d
        n = safe_name(c['name']).lower()
        if n:
            v[n] = d
        given |= {c['code'].upper(), n.upper()}
    comps = [r.component for r in ESS.objects.filter(employee=emp, is_active=True, component__component_value_type='variable')
             .exclude(component__formula__isnull=True).exclude(component__formula='').select_related('component')]
    order, _cycle = order_components(comps)
    try:
        from . import accruals as A
        acc = {i for i, s in A.all_settings().items() if s.enabled}
    except Exception:
        acc = set()
    uses = set(given)
    lines, total = [], ZERO
    for c in order:
        names, _f, _e = names_in(c.formula or '')
        amt = q2(evaluate_formula(c.formula, dict(v), emp, c) or 0)
        for k in filter(None, [c.code, safe_name(c.name).lower()]):
            v[k] = amt
        if {n.upper() for n in names} & uses:
            uses |= {x.upper() for x in filter(None, [c.code, safe_name(c.name).lower()])}
            lines.append({'code': c.code or '', 'name': c.name, 'formula': c.formula, 'amount': str(amt), 'accrued': c.pk in acc})
            total += amt
    return {'lines': lines, 'total': str(q2(total))}


# ------------------------------------------------------------------ Excel template + import
def template_xlsx(m, branch_id, allowed=None):
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    g = grid(m, branch_id, allowed)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Monthly inputs'
    ws.append(['employee_code', 'input_code', 'value', 'employee_name', 'input_name'])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill('solid', fgColor='EEF0FF')
    names = {c['code']: c['name'] for c in g['columns']}
    for r in g['rows']:
        if not r['editable']:
            continue
        for code in names:
            if r['has'].get(code):
                ws.append([r['emp_code'], code, float(r['values'].get(code) or 0), r['employee'], names[code]])
    for col, w in zip('ABCDE', (16, 14, 10, 30, 30)):
        ws.column_dimensions[col].width = w
    info = wb.create_sheet('Read me')
    for line in (f'Monthly inputs for {g["month"]["label"]} – one row per employee and input.',
                 'employee_code and input_code must exist; value = days or hours (0 or more, at most 2 decimals).',
                 'employee_name and input_name are for reading only.',
                 'Check the file first (dry run) – nothing is saved; then apply the rows without errors.'):
        info.append([line])
    info.column_dimensions['A'].width = 100
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _read_rows(f, name=''):
    try:
        from .payctl import _read_rows as rd   # v1.62.11 (item 231) CSV / Excel reader
        return rd(f, name)
    except ImportError:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
        it = wb.worksheets[0].iter_rows(values_only=True)
        head = [str(x or '').strip().lower().replace(' ', '_') for x in next(it)]
        return [{head[i]: v for i, v in enumerate(vals) if i < len(head)} for vals in it if vals and any(v not in (None, '') for v in vals)]


def _code(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v if v is not None else '').strip()


def import_file(f, name, m, branch_id=None, apply=False, allowed=None, user=None):
    """Dry run (apply=False) or apply the rows without errors. Returns {rows: [...], ok, errors, applied, dry_run}."""
    E = M('EmpManagement', 'emp_master')
    try:
        rows = _read_rows(f, name)
    except Exception as ex:
        raise Refused(tr('The file could not be read – use the template (Excel or CSV).'), problems={'file': str(ex)[:200]})
    if not rows:
        raise Refused(tr('The file has no rows.'), problems={'file': tr('The file has no rows.')})
    head = set(rows[0])
    if 'days' in head and 'value' not in head:
        head.add('value')
    for alias in ('days', 'hours', 'days_hours', 'days/hours'):
        for r in rows:
            if alias in r and 'value' not in r:
                r['value'] = r[alias]
    missing = [c for c in ('employee_code', 'input_code', 'value') if c not in rows[0]]
    if missing:
        raise Refused(tr('Columns missing: {0}', ', '.join(missing)), problems={'file': tr('Columns missing: {0}', ', '.join(missing))})
    items = []
    for i, r in enumerate(rows, start=2):
        code = _code(r.get('employee_code'))
        e = E.objects.filter(emp_code__iexact=code).first() if code else None
        items.append({'row': i, 'emp_code': code, 'employee': e, 'code': _code(r.get('input_code')), 'value': r.get('value'),
                      'errors': [] if e else [tr('Employee {0} not found.', code or '–')]})
    with transaction.atomic():
        _check_rows(m, items, allowed, branch_id)
        out = [{'row': it['row'], 'employee_code': it['emp_code'], 'employee': emp_label(it['employee']) if it.get('employee') else '',
                'input_code': it['code'], 'value': str(it['amount']) if it.get('amount') is not None else str(it.get('value') if it.get('value') is not None else ''),
                'errors': it['errors'], 'ok': not it['errors']} for it in items]
        applied = 0
        if apply:
            for it in items:
                if not it['errors']:
                    _write(it['employee'], it['component_id'], it['amount'])
                    applied += 1
    ok = sum(1 for x in out if x['ok'])
    return {'rows': out, 'ok': ok, 'errors': len(out) - ok, 'applied': applied, 'dry_run': not apply, 'month': m.isoformat()[:7]}


def months_offered(branch_id):
    """The months to choose: the open runs (not paid) and the month after the last run of the branch."""
    out = {}
    runs = list(_runs_of_branch(branch_id).order_by('-year', '-month')[:24])
    for r in runs:
        if r.status != 'paid':
            m = date(int(r.year), int(r.month), 1)
            out.setdefault(m, tr('{0} – run {1} ({2})', month_label(m), r.name or f'{r.month:02d}/{r.year}', tr(r.get_status_display())))
    last = max((date(int(r.year), int(r.month), 1) for r in runs), default=None)
    nxt = next_month(last) if last else first_of(date.today())
    out.setdefault(nxt, tr('{0} – next payroll', month_label(nxt)))
    pending = [date(int(r.year), int(r.month), 1) for r in runs if r.status == 'pending']
    default = min(pending) if pending else nxt   # a run not processed yet comes first
    return [{'value': m.isoformat()[:7], 'label': lbl} for m, lbl in sorted(out.items())], default.isoformat()[:7]
