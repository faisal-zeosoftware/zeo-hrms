"""v1.31.0 – Statutory payroll schemes per country.

`seed(pack, user)`        loads the pack's schemes (CountryPack.packs.STATUTORY) that the company does not have yet
`apply_components(user)`  makes a salary component per active scheme: the employee deduction (shown on the payslip) and the
                          employer contribution (hidden, for cost reports), with formulas stat_<code>_emp / stat_<code>_er
`variables(employee)`     the per-employee figures for the payroll formula engine
`variable_names()`        names + descriptions for the formula editor
"""
import logging
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps

logger = logging.getLogger(__name__)
M = apps.get_model
Q2 = Decimal('0.01')
BASE_CATEGORIES = {
    'basic': ('basic',),
    'basic_housing_transport': ('basic', 'hra', 'housing_allowance', 'transport_allowance'),
    'basic_da': ('basic', 'other_allowance'),   # dearness allowance is usually kept as "other allowance" named DA
    'gross': None,                               # every addition
}


def _d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:
        return Decimal('0')


def _rows(employee):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    return list(ESS.objects.filter(employee=employee, is_active=True).select_related('component'))


def base_amount(rows, base):
    cats = BASE_CATEGORIES.get(base)
    tot = Decimal('0')
    for r in rows:
        c = r.component
        if c.component_type != 'addition':
            continue
        if base == 'basic_da':
            name = (c.name or '').strip().lower()
            if not (c.payroll_category == 'basic' or name in ('da', 'dearness allowance') or 'dearness' in name):
                continue
        elif cats is not None and c.payroll_category not in cats:
            continue
        tot += _d(r.amount)
    return tot


def is_national(employee):
    try:
        from CountryPack import services as cp
        return bool(cp.is_national(str(getattr(employee, 'emp_nationality', '') or '')))
    except Exception:
        return False


def _slab_amounts(scheme, base):
    emp = er = Decimal('0')
    for s in scheme.slabs or []:
        to = s.get('to')
        if to in (None, '') or base <= _d(to):
            emp, er = _d(s.get('employee')), _d(s.get('employer'))
            break
    return emp, er


def _annual_tax(scheme, monthly_base):
    """Yearly income tax on 12 × the monthly base after the standard deduction; rebate below the limit; cess on top."""
    income = monthly_base * 12 - _d(scheme.standard_deduction)
    if income <= 0:
        return Decimal('0')
    if _d(scheme.rebate_income_limit) and income <= _d(scheme.rebate_income_limit):
        return Decimal('0')
    tax = Decimal('0')
    lower = Decimal('0')
    for s in scheme.slabs or []:
        to = s.get('to')
        rate = _d(s.get('rate'))
        upper = income if to in (None, '') else min(income, _d(to))
        if upper > lower:
            tax += (upper - lower) * rate / 100
        if to in (None, '') or income <= _d(to):
            break
        lower = _d(to)
    tax += tax * _d(scheme.cess_rate) / 100
    return tax


def compute(scheme, employee, rows=None, national=None):
    """(base, employee_amount, employer_amount) for one employee, all monthly."""
    rows = _rows(employee) if rows is None else rows
    nat = is_national(employee) if national is None else national
    if scheme.applies_to == 'nationals' and not nat or scheme.applies_to == 'expats' and nat:
        return Decimal('0'), Decimal('0'), Decimal('0')
    gross = base_amount(rows, 'gross')
    if scheme.eligible_gross_max is not None and gross > _d(scheme.eligible_gross_max):
        return Decimal('0'), Decimal('0'), Decimal('0')
    base = base_amount(rows, scheme.base)
    if base <= 0 and scheme.kind != 'fixed':
        return Decimal('0'), Decimal('0'), Decimal('0')
    if scheme.min_base is not None and base < _d(scheme.min_base):
        base = _d(scheme.min_base)
    if scheme.max_base is not None and base > _d(scheme.max_base):
        base = _d(scheme.max_base)
    if scheme.kind == 'percent':
        emp = base * _d(scheme.employee_rate) / 100
        er = base * _d(scheme.employer_rate) / 100
    elif scheme.kind == 'fixed':
        emp, er = _d(scheme.employee_fixed), _d(scheme.employer_fixed)
    elif scheme.kind == 'slab':
        emp, er = _slab_amounts(scheme, base)
    elif scheme.kind == 'annual_slab':
        emp, er = (_annual_tax(scheme, base) / 12), Decimal('0')
    else:
        emp = er = Decimal('0')
    return base.quantize(Q2), emp.quantize(Q2, ROUND_HALF_UP), er.quantize(Q2, ROUND_HALF_UP)


def active_schemes():
    try:
        return list(M('PayrollPlus', 'StatutoryScheme').objects.filter(active=True))
    except Exception:
        return []


def variables(employee, rows=None):
    """stat_<code>_base / _emp / _er per active scheme + statutory_employee_total / statutory_employer_total."""
    out = {'statutory_employee_total': Decimal('0.00'), 'statutory_employer_total': Decimal('0.00')}
    schemes = active_schemes()
    if not schemes:
        return out
    rows = _rows(employee) if rows is None else rows
    nat = is_national(employee)
    for s in schemes:
        try:
            base, emp, er = compute(s, employee, rows, nat)
        except Exception:
            logger.exception('statutory %s', s.code)
            base = emp = er = Decimal('0.00')
        out[f'stat_{s.code}_base'] = base
        out[f'stat_{s.code}_emp'] = emp
        out[f'stat_{s.code}_er'] = er
        out['statutory_employee_total'] += emp
        out['statutory_employer_total'] += er
    return out


def variable_names():
    out = {'statutory_employee_total': 'Statutory deductions of the employee this month (all schemes)',
           'statutory_employer_total': 'Statutory employer contributions this month (all schemes)'}
    for s in active_schemes():
        out[f'stat_{s.code}_base'] = f'{s.name}: wage base'
        out[f'stat_{s.code}_emp'] = f'{s.name}: employee share'
        out[f'stat_{s.code}_er'] = f'{s.name}: employer share'
    return out


# ------------------------------------------------------------------ seeding and components
def seed(pack, user=None):
    """Adds the pack's schemes that are missing. Returns (added names, kept names)."""
    from CountryPack.packs import statutory_for, VERIFY
    SS = M('PayrollPlus', 'StatutoryScheme')
    added, kept = [], []
    # the standard schemes of another country are switched off (a company that moved from the UAE pack to India must not
    # deduct GPSSA and EPF at once); custom schemes are left alone
    for other in SS.objects.filter(is_standard=True, active=True).exclude(country=pack).exclude(country=''):
        other.active = False
        other.save(update_fields=['active'])
        kept.append(f'{other.name} – switched off (pack {other.country})')
    for d in statutory_for(pack):
        row = SS.objects.filter(code=d['code']).first()
        if row is not None and row.is_standard and not row.active and row.country == pack:
            row.active = True
            row.save(update_fields=['active'])
            added.append(d['name'] + ' (switched on again)')
            continue
        if SS.objects.filter(code=d['code']).exists():
            kept.append(d['name'])
            continue
        row = SS(country=pack, is_standard=True, updated_by_id=getattr(user, 'pk', None), notes=(d.get('notes', '') + ('\n' if d.get('notes') else '') + VERIFY))
        for k, v in d.items():
            if k != 'notes' and hasattr(row, k):
                setattr(row, k, v)
        row.save()
        added.append(d['name'])
    return added, kept


def _code(prefix, scheme_code):
    return (prefix + scheme_code.upper())[:20]


def apply_components(user=None):
    """A deduction component (employee share) and a hidden 'others' component (employer share) per active scheme."""
    SC = M('PayrollManagement', 'SalaryComponent')
    added, kept = [], []
    cat_of = {'epf': 'pf', 'esi': 'esi', 'tds': 'tax', 'pt': 'tax'}
    for s in active_schemes():
        emp_code = s.employee_component or _code('ST_', s.code)
        er_code = s.employer_component or _code('STE_', s.code)
        changed = False
        has_emp = s.kind != 'fixed' and (s.employee_rate or s.kind in ('slab', 'annual_slab')) or (s.kind == 'fixed' and s.employee_fixed)
        has_er = (s.kind == 'percent' and s.employer_rate) or (s.kind == 'fixed' and s.employer_fixed) or (s.kind == 'slab' and any(_d(x.get('employer')) for x in s.slabs or []))
        if has_emp:
            if SC.objects.filter(code=emp_code).exists():
                kept.append(f'{s.name} (employee)')
            else:
                SC.objects.create(name=f'{s.name} – employee'[:100], code=emp_code, component_type='deduction', payroll_category=cat_of.get(s.code, 'other_allowance'),
                                  component_value_type='variable', formula=f'stat_{s.code}_emp', deduct_leave=False, show_in_payslip=True,
                                  description=f'Employee share of {s.name} ({s.authority}). Figures from Payroll → Statutory schemes.'[:500])
                added.append(f'{s.name} (employee)')
            if s.employee_component != emp_code:
                s.employee_component = emp_code; changed = True
        if has_er:
            if SC.objects.filter(code=er_code).exists():
                kept.append(f'{s.name} (employer)')
            else:
                SC.objects.create(name=f'{s.name} – employer'[:100], code=er_code, component_type='others', payroll_category='other_allowance',
                                  component_value_type='variable', formula=f'stat_{s.code}_er', deduct_leave=False, show_in_payslip=False,
                                  description=f'Employer contribution to {s.name} ({s.authority}) – not on the payslip; for cost reports.'[:500])
                added.append(f'{s.name} (employer)')
            if s.employer_component != er_code:
                s.employer_component = er_code; changed = True
        if changed:
            s.save(update_fields=['employee_component', 'employer_component'])
    return added, kept


def preview(employee_id):
    """For the screen: every active scheme with base / employee / employer for one employee."""
    Emp = M('EmpManagement', 'emp_master')
    e = Emp.objects.filter(pk=employee_id).first()
    if e is None:
        return []
    rows = _rows(e)
    nat = is_national(e)
    out = []
    for s in active_schemes():
        base, emp, er = compute(s, e, rows, nat)
        out.append({'code': s.code, 'name': s.name, 'base': str(base), 'employee': str(emp), 'employer': str(er), 'applies': bool(base or emp or er)})
    return {'employee': f'{e.emp_code} – {e.emp_first_name}', 'national': nat, 'gross': str(base_amount(rows, 'gross')), 'schemes': out}
