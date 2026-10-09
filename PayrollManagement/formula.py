"""
Salary component formulas (v1.11.0): one place that checks and calculates them.

Before, a formula with an unknown name, a function the writer offered but the engine did not have (ABS, INT, WORKHOURS),
AND / OR in capitals, ROUND(x, 2), a code with a digit (OT1) or a division by zero was silently paid as 0.
Now the same rules are used to check a formula when it is saved (with a clear message) and to calculate it in payroll.
"""
import ast
import difflib
import logging
import re
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, InvalidOperation

from simpleeval import SimpleEval, NameNotDefined, FunctionNotDefined

log = logging.getLogger(__name__)
ZERO = Decimal('0.00')

# name → what it is (shown in the formula writer)
VARIABLES = {
    'basic_salary': 'Basic salary of the employee (component in payroll category Basic)',
    'calendar_days': 'Days in the payroll period',
    'fixed_days': 'Fixed days a month from the pay structure (default 30)',
    'working_days': 'Days present in the attendance calendar for the period (same as present_days)',
    'present_days': 'Days present in the attendance calendar for the period',
    'scheduled_working_days': 'Working days in the period: calendar days without weekends and public holidays',
    'unpaid_leave_days': 'Unpaid leave days in the period (incl. the unpaid part of sick / maternity leave)',
    'standard_hours': 'Planned working hours in the period from the shifts (net of unpaid breaks); 160 when no shift is planned',
    'worked_hours': 'Hours worked in the period (attendance)',
    'ot_hours': 'All approved overtime hours in the period (attendance OT after approval + OT entered by HR)',
    'normal_ot_hours': 'Approved normal overtime hours',
    'weekend_ot_hours': 'Approved weekend / off-day overtime hours',
    'holiday_ot_hours': 'Approved public holiday overtime hours',
    'ot_normal_rate': 'Normal overtime rate (multiplier)',
    'ot_weekend_rate': 'Weekend overtime rate (multiplier)',
    'ot_holiday_rate': 'Holiday overtime rate (multiplier)',
    'weekend_ot_days': 'Weekend days with attendance',
    'holiday_ot_days': 'Public holidays with attendance',
    'holiday_weekend_ot_days': 'Weekend + holiday days with attendance',
    'years_of_service': 'Years of service at the end of the period',
    'air_ticket_encashment': 'Approved air-ticket encashment in the period',
    'encashed_days': 'Leave encashed at the leave reset (old rules)',
    'leave_encashment_amount': 'Approved leave encashment not paid yet (Leave encashment approvals)',
    'expense_reimbursement_amount': 'Approved expense reports to be reimbursed with payroll',
    'asset_recovery_amount': 'Asset damage / loss recovery instalments due (deduct with a deduction component)',
    'daily_wage': 'Basic salary ÷ 30',
    'per_year_days': 'Gratuity days per year of service (gratuity table)',
    'gratuity_days': 'Gratuity days earned',
    'monthly_gratuity_accrual': 'Gratuity accrued this month',
    'total_gratuity_liability': 'Total gratuity to date (max 2 years of basic)',
    'max_gratuity': 'Gratuity cap (24 months of basic)',
    # v1.12.0 attendance (Attendance Plus daily results)
    'late_count': 'Days late in the period (penalty-waived days not counted)',
    'late_minutes': 'Minutes late in the period',
    'early_count': 'Days left early in the period',
    'early_minutes': 'Minutes left early in the period',
    'absent_days': 'Working days marked absent (no punch, or below the minimum hours / tolerance rules)',
    'missing_punch_days': 'Days with a clock-in but no clock-out (not corrected yet)',
    'half_day_count': 'Days counted as half day by the attendance rules',
    'break_minutes': 'Break / lunch minutes taken in the period',
    'net_worked_hours': 'Hours worked after unpaid breaks (attendance rules)',
    'ot_weighted_hours': 'Approved overtime hours × their rate (e.g. 2 h at 1.25 = 2.5)',
    'normal_ot_weighted_hours': 'Approved normal overtime hours × rate',
    'weekend_ot_weighted_hours': 'Approved weekend overtime hours × rate',
    'holiday_ot_weighted_hours': 'Approved holiday overtime hours × rate',
    'ot_pending_hours': 'Attendance overtime still waiting for approval (not paid)',
    'late_penalty_days': 'Late / early penalty days recorded without a leave type to deduct from',
    'scheduled_shift_days': 'Days with a planned shift in the period',
}

# v1.12.0: names set by AttendancePlus.payroll.variables (0 when the app is not installed)
ATTENDANCE_VARIABLE_NAMES = ['late_count', 'late_minutes', 'early_count', 'early_minutes', 'absent_days',
                             'missing_punch_days', 'half_day_count', 'break_minutes', 'net_worked_hours',
                             'ot_weighted_hours', 'normal_ot_weighted_hours',
                             'weekend_ot_weighted_hours', 'holiday_ot_weighted_hours', 'ot_pending_hours',
                             'late_penalty_days', 'scheduled_shift_days']

FUNCTIONS = {
    'IF': 'IF(condition, value_if_true, value_if_false) – also IF(c1, v1, c2, v2, …, default)',
    'MAX': 'MAX(a, b, …) – the largest',
    'MIN': 'MIN(a, b, …) – the smallest',
    'SUM': 'SUM(a, b, …)',
    'AVG': 'AVG(a, b, …) – the average',
    'ROUND': 'ROUND(value, digits) – digits default 2',
    'ABS': 'ABS(value) – without the minus sign',
    'INT': 'INT(value) – whole number, rounded down',
    'FLOOR': 'FLOOR(value) – rounded down',
    'CEIL': 'CEIL(value) – rounded up',
    'WORKHOURS': 'WORKHOURS() – hours worked in the period (same as worked_hours)',
}
OPERATORS = ['+', '-', '*', '/', '%', '(', ')', '<', '>', '<=', '>=', '==', '!=', 'and', 'or', 'not']

_NUM = re.compile(r'(?<![A-Za-z_0-9.])(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![A-Za-z_0-9])')
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def safe_name(text):
    """Component / leave code → formula name (letters, digits, _)."""
    s = re.sub(r'[^a-zA-Z0-9_]', '_', str(text or '').strip())
    return re.sub(r'_+', '_', s).strip('_')


def _prepare(formula):
    f = (formula or '').strip().strip("'").strip('"').strip()
    f = re.sub(r'\bAND\b', ' and ', f, flags=re.I)
    f = re.sub(r'\bOR\b', ' or ', f, flags=re.I)
    f = re.sub(r'\bNOT\b', ' not ', f, flags=re.I)
    f = f.replace('<>', '!=')
    f = re.sub(r'(?<![<>=!])=(?!=)', '==', f)          # a single = in a condition means "equals"
    return f


def _dec(x):
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool):
        return Decimal(int(x))
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        raise ValueError(f'"{x}" is not a number')


def _engine(variables, warnings):
    lower = {}
    for k in variables:
        lower.setdefault(k.lower(), k)

    def name(node):
        n = node.id
        if n in variables:
            return variables[n]
        if n.lower() in lower:
            return variables[lower[n.lower()]]
        raise NameNotDefined(n, '')

    def div(a, b):
        b = _dec(b)
        if b == 0:
            warnings.append('division by zero – taken as 0')
            return Decimal('0')
        return _dec(a) / b

    def mod(a, b):
        b = _dec(b)
        if b == 0:
            warnings.append('division by zero – taken as 0')
            return Decimal('0')
        return _dec(a) % b

    def IF(*args):
        if len(args) < 3 or len(args) % 2 == 0:
            raise ValueError('IF needs (condition, value if true, value if false)')
        for i in range(0, len(args) - 1, 2):
            if args[i]:
                return args[i + 1]
        return args[-1]

    def ROUND(v, digits=2):
        d = int(_dec(digits))
        q = Decimal(1).scaleb(-d) if d > 0 else Decimal(1)
        return _dec(v).quantize(q, rounding=ROUND_HALF_UP)

    def nums(args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = args[0]
        return [_dec(a) for a in args]

    s = SimpleEval()
    s.names = name
    s.operators = dict(s.operators)
    s.operators[ast.Div] = div
    s.operators[ast.FloorDiv] = div
    s.operators[ast.Mod] = mod
    s.functions = {
        'Decimal': Decimal, 'IF': IF, 'ROUND': ROUND,
        'MAX': lambda *a: max(nums(a)), 'MIN': lambda *a: min(nums(a)), 'SUM': lambda *a: sum(nums(a), Decimal('0')),
        'AVG': lambda *a: (sum(nums(a), Decimal('0')) / len(nums(a))) if a else Decimal('0'),
        'ABS': lambda v: abs(_dec(v)), 'INT': lambda v: _dec(v).to_integral_value(rounding=ROUND_FLOOR),
        'FLOOR': lambda v: _dec(v).to_integral_value(rounding=ROUND_FLOOR), 'CEIL': lambda v: _dec(v).to_integral_value(rounding=ROUND_CEILING),
        'WORKHOURS': lambda: _dec(variables.get('worked_hours', 0)),
    }
    for k in list(s.functions):          # functions in any case: round(), Max() …
        s.functions[k.lower()] = s.functions[k]
        s.functions[k.capitalize()] = s.functions[k]
    return s


def calculate(formula, variables):
    """(value, error, warnings). value is a Decimal with 2 decimals (0.00 when there is an error)."""
    warnings = []
    f = _prepare(formula)
    if not f:
        return ZERO, 'The formula is empty.', warnings
    expr = _NUM.sub(r'Decimal("\1")', f)
    try:
        v = _engine(variables, warnings).eval(expr)
        if isinstance(v, bool):
            v = Decimal(int(v))
        return _dec(v).quantize(ZERO, rounding=ROUND_HALF_UP), None, warnings
    except NameNotDefined as e:
        return ZERO, f'Unknown name "{getattr(e, "name", e)}".', warnings
    except FunctionNotDefined as e:
        return ZERO, f'Unknown function "{getattr(e, "func_name", e)}".', warnings
    except SyntaxError:
        return ZERO, 'The formula is not complete or has a typing error (check brackets, commas and operators).', warnings
    except Exception as e:
        return ZERO, f'{e}', warnings


def names_in(formula):
    """Names and functions used by a formula (after AND/OR are normalised)."""
    f = _prepare(formula)
    try:
        tree = ast.parse(_NUM.sub('0', f), mode='eval')
    except SyntaxError:
        return set(), set(), 'The formula is not complete or has a typing error (check brackets, commas and operators).'
    names, funcs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            funcs.add(node.func.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            return names, funcs, 'Use names without dots (e.g. BAS, basic_salary).'
    names -= funcs
    return names, funcs, None


def known_names():
    """{name: description} the formulas of this company may use."""
    from django.apps import apps
    out = dict(VARIABLES)
    SC = apps.get_model('PayrollManagement', 'SalaryComponent')
    for c in SC.objects.all():
        if c.code:
            out.setdefault(c.code, f'Component: {c.name}')
        n = safe_name(c.name).lower()
        if n:
            out.setdefault(n, f'Component: {c.name}')
    LT = apps.get_model('calendars', 'leave_type')
    for t in LT.objects.all():
        code = safe_name(t.code or t.name).lower()
        if code:
            out.setdefault(f'leave_balance_{code}', f'Leave balance: {t.name}')
    return out


def problems(formula, own_code=None, own_name=None, known=None):
    """Messages that stop the formula from being saved; [] when it is fine."""
    known = known if known is not None else known_names()
    names, funcs, err = names_in(formula)
    if err:
        return [err]
    out = []
    kl = {k.lower(): k for k in known}
    fl = {k.lower() for k in FUNCTIONS} | {'decimal'}
    for f in sorted(funcs):
        if f.lower() not in fl:
            hint = difflib.get_close_matches(f.upper(), list(FUNCTIONS), n=1)
            out.append(f'Unknown function {f}()' + (f' – did you mean {hint[0]}()?' if hint else '.'))
    for n in sorted(names):
        if n.lower() in ('true', 'false'):
            continue
        if own_code and n.lower() == own_code.lower() or own_name and n.lower() == safe_name(own_name).lower():
            out.append(f'The formula uses the component itself ({n}).')
            continue
        if n.lower() not in kl:
            hint = difflib.get_close_matches(n, list(known), n=1, cutoff=0.6) or difflib.get_close_matches(n.lower(), list(kl), n=1, cutoff=0.6)
            out.append(f'Unknown name "{n}"' + (f' – did you mean {hint[0]}?' if hint else '. Use a component code or a variable from the list.'))
    if not out:   # trial run with every name = 1, to catch wrong function use (e.g. IF with 2 arguments)
        v, e, _ = calculate(formula, {k: Decimal('1') for k in known} | {k.lower(): Decimal('1') for k in known})
        if e:
            out.append(e)
    return out


def order_components(comps):
    """Formula components in an order where each comes after the components it uses; cycles are reported."""
    by_key = {}
    for c in comps:
        for k in filter(None, [c.code, safe_name(c.name).lower()]):
            by_key[k.lower()] = c
    deps = {}
    for c in comps:
        names, _, _ = names_in(c.formula or '')
        deps[c.pk] = {by_key[n.lower()].pk for n in names if n.lower() in by_key and by_key[n.lower()].pk != c.pk}
    done, order, cycle = set(), [], []
    pending = list(comps)
    while pending:
        ready = [c for c in pending if deps[c.pk] <= done]
        if not ready:
            cycle = pending
            break
        for c in ready:
            order.append(c)
            done.add(c.pk)
        pending = [c for c in pending if c.pk not in done]
    return order, cycle
