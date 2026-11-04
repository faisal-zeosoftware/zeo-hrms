# v1.62.11 (item 18b): new module – opening accrued balances in bulk (Excel import, grid of one component)
"""v1.62.11 (item 18b) – opening balances of "Accrue now – pay later" components for many employees at once.

* Excel template (employee_code, component_code, amount, as_of_date) → dry run with the errors of each row → apply the rows without
  errors. Every row goes through accruals.set_opening (the 1.62.7 rules: the component must be accrued; entered again it replaces the
  old opening balance; 0 removes it).
* Grid of one component: every employee of the user's branches who has the component (or an opening balance of it) with the opening
  amount and its date; Save sends only the changed rows, again through set_opening.
"""
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction

from Translations.i18n import tr

from . import accruals as A
from .models import AccrualEntry

M = apps.get_model
COLUMNS = ['employee_code', 'component_code', 'amount', 'as_of_date']


def _day(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _code(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v if v is not None else '').strip()


def _amount(v):
    if v is None or str(v).strip() == '':
        return None, tr('Amount missing.')
    try:
        d = Decimal(str(v).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return None, tr('The amount is not a number.')
    if not d.is_finite():
        return None, tr('The amount is not a number.')
    if d < 0:
        return None, tr('Enter 0 or more (0 removes the opening balance).')
    if d != d.quantize(A.Q2):
        return None, tr('Use at most 2 decimals.')
    if d > Decimal('9999999999.99'):
        return None, tr('The amount is too large.')
    return A.q2(d), None


def accrued_components():
    """{CODE: component} of the components that are (or were) accrued – the group's first copy."""
    SC = M('PayrollManagement', 'SalaryComponent')
    out = {}
    for c in SC.objects.filter(pk__in=list(A.all_settings())).order_by('pk'):
        out.setdefault(A._gkey(c), c)
    return out


def _pick(comp, emp):
    """The branch copy of the employee's branch (same code), else the component itself."""
    for c in A.twins(comp):
        if c.branch_id == emp.emp_branch_id_id and c.pk in A.all_settings():
            return c
    return comp


def template_xlsx():
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Opening balances'
    ws.append(COLUMNS + ['employee_name', 'component_name'])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill('solid', fgColor='EEF0FF')
    E = M('EmpManagement', 'emp_master')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    comps = accrued_components()
    asof = date(date.today().year - 1, 12, 31)
    for key, c in sorted(comps.items()):
        ids = [x.pk for x in A.twins(c)]
        emps = E.objects.filter(is_active=True, salary_structures__component_id__in=ids, salary_structures__is_active=True).distinct().order_by('emp_code')
        for e in emps:
            op = AccrualEntry.objects.filter(employee_id=e.pk, component_id__in=ids, kind='opening').first()
            ws.append([e.emp_code, c.code or '', float(op.amount) if op else 0, (op.month if op else asof).isoformat(),
                       ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x), c.name])
    for col, w in zip('ABCDEF', (16, 16, 12, 14, 30, 30)):
        ws.column_dimensions[col].width = w
    info = wb.create_sheet('Read me')
    for line in ('Opening accrued balances (go-live) – one row per employee and accrued component.',
                 'amount: the balance on as_of_date (YYYY-MM-DD or DD/MM/YYYY); entered again it replaces the old one; 0 removes it.',
                 'Only components with "Accrue now – pay later" switched on are accepted.',
                 'Check the file first (dry run) – nothing is saved; then apply the rows without errors.'):
        info.append([line])
    info.column_dimensions['A'].width = 110
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def import_file(f, name, apply=False, allowed=None, user=None):
    from .monthly_inputs import _read_rows
    E = M('EmpManagement', 'emp_master')
    try:
        rows = _read_rows(f, name)
    except Exception as ex:
        raise A.Refused(tr('The file could not be read – use the template (Excel or CSV).'), problems={'file': str(ex)[:200]})
    if not rows:
        raise A.Refused(tr('The file has no rows.'), problems={'file': tr('The file has no rows.')})
    for r in rows:
        if 'as_of' in r and 'as_of_date' not in r:
            r['as_of_date'] = r['as_of']
    missing = [c for c in COLUMNS if c not in rows[0]]
    if missing:
        raise A.Refused(tr('Columns missing: {0}', ', '.join(missing)), problems={'file': tr('Columns missing: {0}', ', '.join(missing))})
    comps = accrued_components()
    sets = A.all_settings()
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):
        errs = []
        code, ccode = _code(r.get('employee_code')), _code(r.get('component_code'))
        e = E.objects.filter(emp_code__iexact=code).first() if code else None
        if e is None:
            errs.append(tr('Employee {0} not found.', code or '–'))
        elif allowed is not None and e.pk not in allowed:
            errs.append(tr('This employee is not in your branches.'))
            e = None
        c = comps.get(ccode.upper()) if ccode else None
        if c is None:
            SC = M('PayrollManagement', 'SalaryComponent')
            errs.append(tr('Component {0} is not accrued – turn on "Accrue now – pay later" first.', ccode) if ccode and SC.objects.filter(code__iexact=ccode).exists()
                        else tr('Component {0} not found.', ccode or '–'))
        elif not sets[c.pk].enabled and not any(sets.get(x.pk) and sets[x.pk].enabled for x in A.twins(c)):
            errs.append(tr('Component {0} is not accrued – turn on "Accrue now – pay later" first.', ccode))
        amt, aerr = _amount(r.get('amount'))
        if aerr:
            errs.append(aerr)
        d = _day(r.get('as_of_date'))
        if d is None:
            errs.append(tr('The date is missing or not a date (use YYYY-MM-DD or DD/MM/YYYY).'))
        if e is not None and c is not None and not errs:   # a row with errors is not applied – it does not make the next one a duplicate
            k = (e.pk, A._gkey(c))
            if k in seen:
                errs.append(tr('The same employee and component are in the file twice.'))
            seen.add(k)
        old = None
        if e is not None and c is not None:
            op = AccrualEntry.objects.filter(employee_id=e.pk, component_id__in=[x.pk for x in A.twins(c)], kind='opening').first()
            old = str(op.amount) if op else None
        out.append({'row': i, 'employee_code': code, 'employee': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) if e else '',
                    'component_code': ccode, 'component': c.name if c else '', 'amount': str(amt) if amt is not None else str(r.get('amount') or ''),
                    'as_of': d.isoformat() if d else str(r.get('as_of_date') or ''), 'old': old, 'errors': errs, 'ok': not errs,
                    '_k': (e, c, amt, d)})
    applied = 0
    if apply:
        with transaction.atomic():
            for x in out:
                if x['ok']:
                    e, c, amt, d = x['_k']
                    A.set_opening(e.pk, _pick(c, e), amt, d, tr('Opening balance (import)'), user)
                    applied += 1
    for x in out:
        x.pop('_k', None)
    ok = sum(1 for x in out if x['ok'])
    return {'rows': out, 'ok': ok, 'errors': len(out) - ok, 'applied': applied, 'dry_run': not apply}


def grid(comp, allowed=None):
    """Every employee (user's branches) with the component or an opening balance of it: amount and date of the opening balance."""
    E = M('EmpManagement', 'emp_master')
    ids = [c.pk for c in A.twins(comp)]
    ops = {x.employee_id: x for x in AccrualEntry.objects.filter(component_id__in=ids, kind='opening')}
    qs = E.objects.filter(salary_structures__component_id__in=ids, salary_structures__is_active=True, is_active=True)
    emps = {e.pk: e for e in qs.distinct()}
    for e in E.objects.filter(pk__in=list(ops)):
        emps.setdefault(e.pk, e)
    rows = []
    for e in sorted(emps.values(), key=lambda e: (e.emp_code or '', e.pk)):
        if allowed is not None and e.pk not in allowed:
            continue
        op = ops.get(e.pk)
        rows.append({'employee_id': e.pk, 'emp_code': e.emp_code or '', 'employee': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                     'amount': str(op.amount) if op else '', 'as_of': op.month.isoformat() if op else '', 'active': bool(e.is_active)})
    return {'component': {'id': comp.pk, 'code': comp.code or '', 'name': comp.name}, 'rows': rows,
            'default_as_of': date(date.today().year - 1, 12, 31).isoformat()}


@transaction.atomic
def save_grid(comp, rows, allowed=None, user=None):
    """rows: [{employee, amount, as_of}] – all or nothing; an empty amount is skipped, 0 removes the opening balance."""
    E = M('EmpManagement', 'emp_master')
    if comp.pk not in A.all_settings():
        raise A.Refused(tr('Turn on "Accrue now – pay later" for the component first.'))
    problems, todo = {}, []
    for r in rows:
        try:
            e = E.objects.get(pk=int(r.get('employee')))
        except (E.DoesNotExist, TypeError, ValueError):
            problems[str(r.get('employee'))] = tr('Employee not found.')
            continue
        if allowed is not None and e.pk not in allowed:
            problems[str(e.pk)] = tr('This employee is not in your branches.')
            continue
        if r.get('amount') in (None, ''):
            continue
        amt, aerr = _amount(r.get('amount'))
        d = _day(r.get('as_of'))
        if aerr or d is None:
            problems[str(e.pk)] = aerr or tr('Enter the date of the balance.')
            continue
        todo.append((e, amt, d))
    if problems:
        raise A.Refused(tr('Some balances were not saved – check the marked rows.'), problems=problems)
    for e, amt, d in todo:
        A.set_opening(e.pk, _pick(comp, e), amt, d, tr('Opening balance'), user)
    return {'saved': len(todo)}
