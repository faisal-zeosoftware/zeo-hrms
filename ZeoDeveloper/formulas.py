"""ZEO Developer – level 2 formulas (Excel-like) for custom modules.

The same safe engine as the payroll formulas (PayrollManagement.formula: simpleeval, AST based, never eval) with the same
functions (IF, MAX, MIN, SUM, AVG, ROUND, ABS, INT, FLOOR, CEIL) plus functions for modules:

  LOOKUP("rate table", key, "column")    a value from a rate table (Developer › Rate tables)
  SUMROWS("child table", "column")       total of a column of a child table;  COUNTROWS("child table")
  AVGROWS / MAXROWS / MINROWS            average / largest / smallest of a column of a child table
  TODAY()  DAYS(from, to)  MONTHS(from, to)  YEARS(from, to)  ADDDAYS(date, n)  ADDMONTHS(date, n)  YEAR(d)  MONTH(d)  DAY(d)
  CONCAT(a, b, …)  TEXT(value)  UPPER(text)  LEFT(text, n)  BLANK(value) – 1 when empty

Names are the keys of the module's fields (number, amount, date, yes / no = 1 / 0, choice / text = the text) and `number`,
`state`. "Text" in double quotes.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import ast
import calendar
import datetime as _dt
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from PayrollManagement import formula as PF

ZERO = Decimal('0')
_STR = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'')

FUNCTIONS = dict(PF.FUNCTIONS)
FUNCTIONS.pop('WORKHOURS', None)
FUNCTIONS.update({
    'LOOKUP': 'LOOKUP("rate table", key, "column") – a value from a rate table',
    'SUMROWS': 'SUMROWS("child table", "column") – total of a column of a child table',
    'COUNTROWS': 'COUNTROWS("child table") – number of rows',
    'AVGROWS': 'AVGROWS("child table", "column")', 'MAXROWS': 'MAXROWS("child table", "column")', 'MINROWS': 'MINROWS("child table", "column")',
    'TODAY': 'TODAY() – today\'s date', 'DAYS': 'DAYS(from, to) – days between two dates',
    'MONTHS': 'MONTHS(from, to) – whole months between two dates', 'YEARS': 'YEARS(from, to) – years between two dates (with decimals)',
    'ADDDAYS': 'ADDDAYS(date, n)', 'ADDMONTHS': 'ADDMONTHS(date, n)', 'YEAR': 'YEAR(date)', 'MONTH': 'MONTH(date)', 'DAY': 'DAY(date)',
    'CONCAT': 'CONCAT(a, b, …) – joins texts', 'TEXT': 'TEXT(value)', 'UPPER': 'UPPER(text)', 'LEFT': 'LEFT(text, n)',
    'BLANK': 'BLANK(value) – 1 when the value is empty, else 0',
})


class FormulaError(Exception):
    pass


def _date(v):
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    if isinstance(v, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}.*', v):
        return _dt.date.fromisoformat(v[:10])
    raise ValueError(f'"{v}" is not a date')


def add_months(d, n):
    d = _date(d)
    m = d.month - 1 + int(n)
    y, m = d.year + m // 12, m % 12 + 1
    return _dt.date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_between(a, b):
    a, b = _date(a), _date(b)
    n = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        n -= 1
    return Decimal(n)


def _protect(formula):
    """"text" → names zstr0, zstr1 … so the number rule of the payroll engine does not touch dates / digits inside texts."""
    texts = []

    def repl(m):
        texts.append(m.group(0)[1:-1])
        return f' zstr{len(texts) - 1} '
    return _STR.sub(repl, formula or ''), texts


def _expr(formula):
    f, texts = _protect(formula)
    f = PF._prepare(f)
    if not f:
        raise FormulaError('The formula is empty.')
    try:
        tree = ast.parse(PF._NUM.sub('0', f), mode='eval')
    except SyntaxError:
        raise FormulaError('The formula is not complete or has a typing error (check brackets, commas, quotes and operators).')
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            raise FormulaError('Use names without dots (field keys like unit_price).')
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.Subscript)):
            raise FormulaError('Only values, operators and functions can be used in a formula.')
    return PF._NUM.sub(r'Decimal("\1")', f), texts, tree


def evaluate(formula, variables, children=None, lookup=None):
    """The raw value (Decimal, date, text or yes/no) of a formula; raises FormulaError with a plain message."""
    expr, texts, _ = _expr(formula)
    variables = dict(variables or {})
    for i, t in enumerate(texts):
        variables[f'zstr{i}'] = t
    children = children or {}
    warnings = []
    s = PF._engine(variables, warnings)

    def rows(name):
        if name not in children:
            raise ValueError(f'"{name}" is not a child table of the module')
        return children[name] or []

    def col(name, c):
        out = []
        for r in rows(name):
            v = r.get(c)
            if v in (None, ''):
                continue
            try:
                out.append(Decimal(str(v)))
            except (InvalidOperation, ValueError):
                raise ValueError(f'{name}.{c}: "{v}" is not a number')
        return out

    def LOOKUP(table, key, column):
        if lookup is None:
            raise ValueError('rate tables are not available here')
        return lookup(str(table), key, str(column))

    def ROUNDQ(v):
        return Decimal(v).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    fns = {
        'LOOKUP': LOOKUP,
        'SUMROWS': lambda t, c: sum(col(t, c), ZERO), 'COUNTROWS': lambda t: Decimal(len(rows(t))),
        'AVGROWS': lambda t, c: (sum(col(t, c), ZERO) / len(col(t, c))) if col(t, c) else ZERO,
        'MAXROWS': lambda t, c: max(col(t, c), default=ZERO), 'MINROWS': lambda t, c: min(col(t, c), default=ZERO),
        # an empty date gives 0 (numbers) or an empty value (dates), so IF(BLANK(d), …, ADDMONTHS(d, 2)) works – IF gets every argument
        'TODAY': lambda: _today(), 'DAYS': _blank0(lambda a, b: Decimal((_date(b) - _date(a)).days)),
        'MONTHS': _blank0(months_between), 'YEARS': _blank0(lambda a, b: ROUNDQ(Decimal((_date(b) - _date(a)).days) / Decimal('365.25'))),
        'ADDDAYS': _blank_none(lambda d, n: _date(d) + _dt.timedelta(days=int(n))), 'ADDMONTHS': _blank_none(add_months),
        'YEAR': _blank0(lambda d: Decimal(_date(d).year)), 'MONTH': _blank0(lambda d: Decimal(_date(d).month)), 'DAY': _blank0(lambda d: Decimal(_date(d).day)),
        'CONCAT': lambda *a: ''.join(_text(x) for x in a), 'TEXT': lambda v: _text(v), 'UPPER': lambda v: _text(v).upper(),
        'LEFT': lambda v, n: _text(v)[:int(n)], 'BLANK': lambda v: Decimal(1 if v in (None, '') else 0),
    }
    for k, fn in fns.items():
        s.functions[k] = fn
        s.functions[k.lower()] = fn
        s.functions[k.capitalize()] = fn
    try:
        v = s.eval(expr)
    except PF.NameNotDefined as e:
        raise FormulaError(f'Unknown name "{getattr(e, "name", e)}" – use the key of a field of the module.')
    except PF.FunctionNotDefined as e:
        raise FormulaError(f'Unknown function "{getattr(e, "func_name", e)}".')
    except SyntaxError:
        raise FormulaError('The formula is not complete or has a typing error.')
    except ZeroDivisionError:
        raise FormulaError('Division by zero.')
    except FormulaError:
        raise
    except Exception as e:   # noqa: BLE001 – simpleeval limits, wrong types …
        raise FormulaError(str(e)[:200] or 'The formula cannot be calculated.')
    if warnings:
        v = v   # division by zero is taken as 0, like payroll formulas
    return v


def _blank0(fn):
    def w(*a):
        return ZERO if any(x in (None, '') for x in a) else fn(*a)
    return w


def _blank_none(fn):
    def w(*a):
        return '' if any(x in (None, '') for x in a) else fn(*a)
    return w


def _today():
    try:
        from ZeoFlow.engine import local_today
        return local_today()
    except Exception:   # noqa: BLE001
        return _zt_today()


def _text(v):
    if v is None:
        return ''
    if isinstance(v, Decimal):
        q = v.quantize(Decimal('0.01')) if v != v.to_integral_value() else v.to_integral_value()
        return format(q, 'f')
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.strftime('%d/%m/%Y')
    return str(v)


def as_result(v, result='number'):
    """Formula value → what is stored in the record (text like the other fields)."""
    if result == 'text':
        return _text(v)[:300]
    if result == 'date':
        try:
            return _date(v).isoformat()
        except ValueError:
            raise FormulaError('The formula does not give a date.')
    if isinstance(v, bool):
        v = Decimal(int(v))
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise FormulaError(f'The formula gives "{_text(v)[:40]}", not a number.')
    if result == 'money':
        return str(d.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
    d = d.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP).normalize()
    return str(d.quantize(Decimal(1))) if d == d.to_integral_value() else format(d, 'f')


def names_used(formula):
    try:
        _, texts, tree = _expr(formula)
    except FormulaError:
        return set(), set()
    names, funcs = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            funcs.add(node.func.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    names -= funcs
    names = {n for n in names if not re.fullmatch(r'zstr\d+', n) and n.lower() not in ('true', 'false')}
    return names, funcs


def problems(formula, known_names):
    """Messages that stop a module formula from being saved."""
    try:
        _expr(formula)
    except FormulaError as e:
        return [str(e)]
    names, funcs = names_used(formula)
    out = []
    fl = {k.lower() for k in FUNCTIONS} | {'decimal'}
    for f in sorted(funcs):
        if f.lower() not in fl:
            import difflib
            hint = difflib.get_close_matches(f.upper(), list(FUNCTIONS), n=1)
            out.append(f'Unknown function {f}()' + (f' – did you mean {hint[0]}()?' if hint else '.'))
    kl = {k.lower() for k in known_names}
    for n in sorted(names):
        if n.lower() not in kl:
            import difflib
            hint = difflib.get_close_matches(n, list(known_names), n=1, cutoff=0.6)
            out.append(f'Unknown name "{n}"' + (f' – did you mean {hint[0]}?' if hint else ' – use the key of a field of the module.'))
    return out
