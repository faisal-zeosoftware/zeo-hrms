"""ZEO Developer 1.74.0 (pending A15 / A16, D3) – the script sandbox.

Scripts are basic Python, but they are never given to Python's own compiler / exec. This module parses the text with `ast.parse`
and walks the tree itself (an allow-list interpreter):

* only the statements and expressions listed in `_STMT` / `_EXPR` exist – `import`, `class`, `with`, `global`, `lambda`,
  `yield`, `async`, decorators … are refused before the script starts (with the line number);
* names starting with `_` and every attribute starting with `_` are refused (`__class__`, `__mro__`, `__globals__` …);
* the only callables are the allowed built-ins below, the script's own functions and the methods of the ZEO script API objects
  (records, employees, actions, http …) – `getattr`, `eval`, `exec`, `open`, `type`, `__import__`, `compile` simply do not exist;
* attributes of plain values are an allow-list per type (e.g. str.upper, list.append, dict.get, date.year) – `str.format`
  is not on it (format strings can reach attributes);
* limits: steps (every node counts), wall time, the size of every list / text / dict made, the total number of items made,
  and the depth of calls. A script that loops for ever is stopped by the step / time limit, one that builds a 10-million-item
  list by the size limit.

The ZEO objects given to a script are wrappers (see api.py) – a script never holds a Django model, a queryset, a request or a
connection, so it cannot reach SQL, another company's schema, files or the network except through the API.
"""
import ast
import datetime as _dt
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ------------------------------------------------------------------ limits (DeveloperSettings may lower / raise them)
DEFAULT_LIMITS = {
    'steps': 2_000_000,       # nodes evaluated
    'seconds': 5.0,           # wall time
    'items': 200_000,         # largest single list / dict / set / text a script may make
    'total_items': 2_000_000, # all items made during one run
    'depth': 40,              # nested calls of the script's own functions
}


class ScriptError(Exception):
    """A plain-word message for the person writing the script, with the line."""

    def __init__(self, message, line=None, kind='error'):
        super().__init__(message)
        self.message, self.line, self.kind = message, line, kind

    def __str__(self):
        return f'Line {self.line}: {self.message}' if self.line else self.message


class LimitError(ScriptError):
    pass


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class UserRaised(Exception):
    """raise / stop() inside the script."""


# ------------------------------------------------------------------ what a script can touch
SAFE_TYPES = (type(None), bool, int, float, Decimal, str, list, tuple, dict, set, _dt.date, _dt.datetime, _dt.time, _dt.timedelta)

ATTRS = {
    str: {'lower', 'upper', 'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'join', 'replace', 'startswith', 'endswith', 'title',
          'capitalize', 'isdigit', 'isalpha', 'isalnum', 'isspace', 'count', 'find', 'splitlines', 'casefold'},
    list: {'append', 'extend', 'pop', 'insert', 'remove', 'index', 'count', 'reverse', 'sort', 'copy', 'clear'},
    tuple: {'index', 'count'},
    dict: {'get', 'keys', 'values', 'items', 'update', 'pop', 'setdefault', 'copy', 'clear'},
    set: {'add', 'discard', 'remove', 'union', 'intersection', 'difference', 'copy', 'clear', 'issubset', 'issuperset'},
    Decimal: {'quantize', 'is_zero'},
    _dt.date: {'year', 'month', 'day', 'isoformat', 'weekday', 'isoweekday', 'replace'},
    _dt.datetime: {'year', 'month', 'day', 'hour', 'minute', 'second', 'isoformat', 'weekday', 'date', 'replace'},
    _dt.time: {'hour', 'minute', 'second', 'isoformat'},
    _dt.timedelta: {'days', 'seconds', 'total_seconds'},
    int: {'bit_length'},
    float: {'is_integer'},
}
GROWING = {('list', 'append'), ('list', 'extend'), ('list', 'insert'), ('dict', 'update'), ('dict', 'setdefault'), ('set', 'add')}


def _tname(v):
    if v is None:
        return 'nothing (None)'
    return {bool: 'yes/no', int: 'number', float: 'number', Decimal: 'number', str: 'text', list: 'list', tuple: 'list', dict: 'dictionary',
            set: 'set', _dt.date: 'date', _dt.datetime: 'date and time'}.get(type(v), getattr(type(v), 'zeo_label', 'value'))


class SafeObject:
    """Base of every ZEO object given to scripts. Only the names in `zeo_public` can be read / called."""
    zeo_public = frozenset()
    zeo_label = 'ZEO object'


class SafeFunction:
    """A callable given to scripts (built-in or API)."""

    def __init__(self, fn, name, grows=False):
        self.fn, self.name, self.grows = fn, name, grows

    def __call__(self, *a, **k):
        return self.fn(*a, **k)


# ------------------------------------------------------------------ the interpreter
_BINOPS = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b, ast.Pow: lambda a, b: a ** b,
}
_CMPOPS = {
    ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b, ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b, ast.IsNot: lambda a, b: a is not b,
}
_REFUSED = {
    ast.Import: 'import is not allowed in scripts – everything a script may use is already there (see Help).',
    ast.ImportFrom: 'import is not allowed in scripts – everything a script may use is already there (see Help).',
    ast.ClassDef: 'classes are not allowed in scripts – use functions and dictionaries.',
    ast.With: '"with" is not allowed in scripts.',
    ast.AsyncWith: '"async" is not allowed in scripts.',
    ast.AsyncFor: '"async" is not allowed in scripts.',
    ast.AsyncFunctionDef: '"async" is not allowed in scripts.',
    ast.Global: '"global" is not allowed in scripts – return the value from the function instead.',
    ast.Nonlocal: '"nonlocal" is not allowed in scripts.',
    ast.Lambda: '"lambda" is not allowed in scripts – write a small function with def.',
    ast.Yield: '"yield" is not allowed in scripts.',
    ast.YieldFrom: '"yield" is not allowed in scripts.',
    ast.Await: '"await" is not allowed in scripts.',
    ast.GeneratorExp: 'use a list [ ... for ... ] instead of ( ... for ... ).',
    ast.Starred: '* and ** in calls are not allowed in scripts.',
    ast.NamedExpr: 'the := operator is not allowed in scripts.',
}
try:
    _REFUSED[ast.TryStar] = '"except*" is not allowed in scripts.'
except AttributeError:   # pragma: no cover
    pass
try:
    _REFUSED[ast.Match] = '"match" is not allowed in scripts – use if / elif.'
except AttributeError:   # pragma: no cover
    pass


def check(code):
    """Parse and check a script without running it. Returns the tree; raises ScriptError with the line."""
    if not isinstance(code, str):
        raise ScriptError('The script is empty.')
    if len(code) > 100_000:
        raise ScriptError('The script is too long (more than 100,000 characters).')
    try:
        tree = ast.parse(code, mode='exec')
    except SyntaxError as e:
        raise ScriptError(_syntax_text(e), e.lineno, 'syntax')
    for node in ast.walk(tree):
        line = getattr(node, 'lineno', None)
        for bad, msg in _REFUSED.items():
            if isinstance(node, bad):
                raise ScriptError(msg, line, 'refused')
        if isinstance(node, ast.Name) and node.id.startswith('_'):
            raise ScriptError(f'names starting with "_" are not allowed ({node.id}).', line, 'refused')
        if isinstance(node, ast.Attribute) and node.attr.startswith('_'):
            raise ScriptError(f'attributes starting with "_" are not allowed (.{node.attr}).', line, 'refused')
        if isinstance(node, (ast.FunctionDef,)):
            if node.decorator_list:
                raise ScriptError('decorators (@...) are not allowed in scripts.', line, 'refused')
            if node.name.startswith('_'):
                raise ScriptError(f'function names starting with "_" are not allowed ({node.name}).', line, 'refused')
            a = node.args
            if a.vararg or a.kwarg or a.kwonlyargs or getattr(a, 'posonlyargs', None):
                raise ScriptError('functions in scripts take plain arguments only (no *args / **kwargs).', line, 'refused')
        if isinstance(node, ast.arg) and node.arg.startswith('_'):
            raise ScriptError(f'names starting with "_" are not allowed ({node.arg}).', line, 'refused')
        if isinstance(node, ast.keyword) and node.arg is None:
            raise ScriptError('** in calls is not allowed in scripts.', line, 'refused')
        if isinstance(node, ast.ExceptHandler) and node.name and node.name.startswith('_'):
            raise ScriptError('names starting with "_" are not allowed.', line, 'refused')
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            for sub in ast.walk(node.format_spec):
                if isinstance(sub, ast.FormattedValue):
                    raise ScriptError('nested { } in an f-string format is not allowed.', line, 'refused')
    return tree


def _syntax_text(e):
    msg = (e.msg or '').lower()
    if 'indent' in msg:
        return 'the indentation is wrong here – the lines of one block must start at the same column.'
    if 'never closed' in msg or 'was never closed' in msg or 'unexpected eof' in msg:
        return 'a bracket or a quote is not closed.'
    if "expected ':'" in msg:
        return 'a ":" is missing at the end of this line (after if / for / def / else …).'
    if 'invalid syntax' in msg:
        return 'this line is not valid Python – check brackets, commas, quotes and ":".'
    return f'this line is not valid Python ({e.msg}).'


class _Func:
    """A function the script defined with def."""

    def __init__(self, node, interp):
        self.node, self.interp = node, interp

    zeo_label = 'function'


class Interpreter:
    def __init__(self, globals_=None, limits=None, log=None):
        self.limits = dict(DEFAULT_LIMITS)
        self.limits.update({k: v for k, v in (limits or {}).items() if v})
        self.steps = 0
        self.items = 0
        self.depth = 0
        self.start = time.monotonic()
        self.globals = dict(globals_ or {})
        self.line = None
        self.log = log if log is not None else []
        self.builtins = self._builtins()

    # -------------------------------------------------- limits
    def _tick(self, node):
        self.steps += 1
        ln = getattr(node, 'lineno', None)
        if ln:
            self.line = ln
        if self.steps > self.limits['steps']:
            raise LimitError(f'the script was stopped: it ran more than {self.limits["steps"]:,} steps (an endless loop?).', self.line, 'limit')
        if self.steps % 512 == 0 and time.monotonic() - self.start > self.limits['seconds']:
            raise LimitError(f'the script was stopped: it ran longer than {self.limits["seconds"]:g} seconds.', self.line, 'limit')

    def _grow(self, n):
        if n > self.limits['items']:
            raise LimitError(f'the script was stopped: a list, text or table of {n:,} items is more than the limit of {self.limits["items"]:,} (memory).',
                             self.line, 'limit')
        self.items += n
        if self.items > self.limits['total_items']:
            raise LimitError(f'the script was stopped: it made more than {self.limits["total_items"]:,} items in total (memory).', self.line, 'limit')

    def _size(self, v):
        if isinstance(v, (str, list, tuple, dict, set)):
            self._grow(len(v))
        return v

    # -------------------------------------------------- built-ins
    def _builtins(self):
        it = self

        def _range(*a):
            a = [int(x) for x in a]
            r = range(*a)
            if len(r) > it.limits['items']:
                it._grow(len(r))
            return r

        def _print(*a):
            it.log.append(' '.join(_text(x) for x in a)[:2000])
            if len(it.log) > 2000:
                raise LimitError('the script was stopped: more than 2,000 log lines.', it.line, 'limit')

        def _round(v, d=0):
            try:
                q = Decimal(1).scaleb(-int(d)) if int(d) > 0 else Decimal(1)
                return Decimal(str(v)).quantize(q, rounding=ROUND_HALF_UP) if int(d) > 0 else int(Decimal(str(v)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
            except (InvalidOperation, ValueError):
                raise ScriptError(f'round(): "{v}" is not a number.', it.line)

        def _decimal(v):
            try:
                d = Decimal(str(v).replace(',', '')) if v not in (None, '') else Decimal('0')
            except (InvalidOperation, ValueError):
                raise ScriptError(f'number(): "{_text(v)[:40]}" is not a number.', it.line)
            if not d.is_finite() or abs(d.adjusted()) > 30:
                raise ScriptError(f'number(): "{_text(v)[:40]}" is too large or not a number.', it.line)
            return d

        def _sum(seq, start=0):
            total, ints = _decimal(start), isinstance(start, int)
            for x in seq:
                if isinstance(x, bool) or not isinstance(x, (int, float, Decimal)):
                    raise ScriptError(f'sum(): "{_text(x)[:40]}" is not a number.', it.line)
                ints = ints and isinstance(x, int)
                total += _num(x)
            return int(total) if ints else total

        def _int(v=0):
            if isinstance(v, int):
                return v
            d = _decimal(v)
            return int(d.to_integral_value(rounding='ROUND_DOWN'))

        def _sorted(seq, key=None, reverse=False):
            seq = list(seq)
            it._grow(len(seq))
            if key is not None:
                return sorted(seq, key=lambda x: it.call(key, [x], {}), reverse=bool(reverse))
            return sorted(seq, reverse=bool(reverse))

        def _str(v=''):
            return it._size(_text(v))

        def _list(v=()):
            return it._size(list(v))

        def _dict(v=None, **k):
            d = dict(v or {}, **k)
            return it._size(d)

        def _stop(message='stopped by the script'):
            raise UserRaised(str(message))

        b = {
            'len': len, 'range': _range, 'min': min, 'max': max, 'sum': _sum, 'abs': abs, 'round': _round,
            'int': _int, 'float': lambda v=0: float(_decimal(v)), 'str': _str, 'bool': bool,
            'list': _list, 'dict': _dict, 'set': lambda v=(): it._size(set(v)), 'tuple': lambda v=(): it._size(tuple(v)),
            'sorted': _sorted, 'enumerate': enumerate, 'zip': zip, 'any': any, 'all': all, 'reversed': lambda s: list(reversed(list(s))),
            'print': _print, 'log': _print, 'number': _decimal, 'decimal': _decimal, 'stop': _stop,
            'date': _dt.date, 'datetime': _dt.datetime, 'timedelta': lambda days=0, hours=0, minutes=0: _dt.timedelta(days=days, hours=hours, minutes=minutes),
            'True': True, 'False': False, 'None': None,
        }
        return {k: (v if not callable(v) or isinstance(v, (bool,)) else SafeFunction(v, k)) for k, v in b.items()}

    # -------------------------------------------------- running
    def run(self, tree, env=None):
        env = env if env is not None else {}
        try:
            self._block(tree.body, env)
        except _Return:
            pass
        except (_Break, _Continue):
            raise ScriptError('"break" / "continue" outside a loop.', self.line)
        return env

    def _block(self, body, env):
        for st in body:
            self._stmt(st, env)

    def _stmt(self, node, env):
        self._tick(node)
        t = type(node)
        if t is ast.Expr:
            self.eval(node.value, env)
        elif t is ast.Assign:
            v = self.eval(node.value, env)
            for tg in node.targets:
                self._assign(tg, v, env)
        elif t is ast.AugAssign:
            cur = self.eval(_as_load(node.target), env)
            v = self._binop(node.op, cur, self.eval(node.value, env), node)
            self._assign(node.target, v, env)
        elif t is ast.AnnAssign:
            if node.value is not None:
                self._assign(node.target, self.eval(node.value, env), env)
        elif t is ast.If:
            self._block(node.body if self._truth(self.eval(node.test, env)) else node.orelse, env)
        elif t is ast.For:
            seq = self.eval(node.iter, env)
            self._iterable(seq)
            broke = False
            for x in seq:
                self._tick(node)
                self._assign(node.target, x, env)
                try:
                    self._block(node.body, env)
                except _Break:
                    broke = True
                    break
                except _Continue:
                    continue
            if not broke:
                self._block(node.orelse, env)
        elif t is ast.While:
            broke = False
            while self._truth(self.eval(node.test, env)):
                self._tick(node)
                try:
                    self._block(node.body, env)
                except _Break:
                    broke = True
                    break
                except _Continue:
                    continue
            if not broke:
                self._block(node.orelse, env)
        elif t is ast.Break:
            raise _Break()
        elif t is ast.Continue:
            raise _Continue()
        elif t is ast.Pass:
            pass
        elif t is ast.FunctionDef:
            env[node.name] = _Func(node, self)
        elif t is ast.Return:
            raise _Return(self.eval(node.value, env) if node.value is not None else None)
        elif t is ast.Delete:
            for tg in node.targets:
                if isinstance(tg, ast.Name):
                    env.pop(tg.id, None)
                elif isinstance(tg, ast.Subscript):
                    obj = self.eval(tg.value, env)
                    if not isinstance(obj, (list, dict)):
                        raise ScriptError('del works on lists and dictionaries only.', self.line)
                    del obj[self.eval(tg.slice, env)]
                else:
                    raise ScriptError('del works on names, list items and dictionary keys only.', self.line)
        elif t is ast.Raise:
            msg = self.eval(node.exc, env) if node.exc is not None else 'stopped by the script'
            raise UserRaised(_text(msg))
        elif t is ast.Try:
            self._try(node, env)
        elif t is ast.Assert:
            if not self._truth(self.eval(node.test, env)):
                raise UserRaised(_text(self.eval(node.msg, env)) if node.msg is not None else 'a check (assert) failed')
        else:
            raise ScriptError(f'"{type(node).__name__}" is not allowed in scripts.', self.line, 'refused')

    def _try(self, node, env):
        try:
            self._block(node.body, env)
        except (_Return, _Break, _Continue, LimitError):
            raise
        except (ScriptError, UserRaised, ArithmeticError, ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
            if not node.handlers:
                raise
            h = node.handlers[0]
            if h.name:
                env[h.name] = _text(getattr(e, 'message', None) or e)
            self._block(h.body, env)
        else:
            self._block(node.orelse, env)
        finally:
            self._block(node.finalbody, env)

    def _iterable(self, seq):
        if not isinstance(seq, (list, tuple, dict, set, str, range)) and not isinstance(seq, (enumerate, zip, type(reversed([])))):
            if isinstance(seq, SafeObject) and hasattr(seq, '__iter__'):
                return
            raise ScriptError(f'a {_tname(seq)} cannot be looped over.', self.line)

    def _truth(self, v):
        return bool(v)

    def _assign(self, tg, v, env):
        if isinstance(tg, ast.Name):
            if tg.id in self.builtins or tg.id in self.globals:
                raise ScriptError(f'"{tg.id}" is a name of ZEO – choose another name for your variable.', self.line)
            env[tg.id] = v
        elif isinstance(tg, (ast.Tuple, ast.List)):
            vals = list(v)
            if len(vals) != len(tg.elts):
                raise ScriptError(f'{len(tg.elts)} names but {len(vals)} values.', self.line)
            for t2, v2 in zip(tg.elts, vals):
                self._assign(t2, v2, env)
        elif isinstance(tg, ast.Subscript):
            obj = self.eval(tg.value, env)
            key = self.eval(tg.slice, env)
            if isinstance(obj, (list, dict)):
                if isinstance(obj, dict) and key not in obj:
                    self._grow(1)
                obj[key] = v
            elif isinstance(obj, SafeObject) and hasattr(obj, 'zeo_setitem'):
                obj.zeo_setitem(key, v)
            else:
                raise ScriptError(f'cannot set an item of a {_tname(obj)}.', self.line)
        elif isinstance(tg, ast.Attribute):
            obj = self.eval(tg.value, env)
            if isinstance(obj, SafeObject) and hasattr(obj, 'zeo_setattr'):
                obj.zeo_setattr(tg.attr, v)
            else:
                raise ScriptError(f'cannot set .{tg.attr} of a {_tname(obj)}.', self.line)
        else:
            raise ScriptError('this kind of assignment is not allowed in scripts.', self.line)

    # -------------------------------------------------- expressions
    def eval(self, node, env):
        self._tick(node)
        t = type(node)
        if t is ast.Constant:
            v = node.value
            if not isinstance(v, (type(None), bool, int, float, str)):
                raise ScriptError('this kind of value is not allowed in scripts.', self.line)
            if isinstance(v, float):
                return Decimal(repr(v))   # 0.1 + 0.2 == 0.3 in scripts, like money
            return v
        if t is ast.Name:
            n = node.id
            if n in env:
                return env[n]
            if n in self.globals:
                return self.globals[n]
            if n in self.builtins:
                return self.builtins[n]
            raise ScriptError(f'the name "{n}" is not known' + (' – this is not allowed in scripts.' if n in ('open', 'eval', 'exec', 'getattr', 'setattr', 'globals', 'locals', 'vars', 'type', 'compile', 'input', 'dir', 'object', 'super', 'breakpoint', 'help', 'memoryview', 'bytes', 'bytearray', 'classmethod', 'staticmethod', 'property', 'hasattr', 'delattr', 'id', 'hash', 'iter', 'next', 'isinstance', 'issubclass', 'callable') else '.'), self.line)
        if t is ast.BinOp:
            return self._binop(node.op, self.eval(node.left, env), self.eval(node.right, env), node)
        if t is ast.UnaryOp:
            v = self.eval(node.operand, env)
            if isinstance(node.op, ast.Not):
                return not v
            if isinstance(node.op, ast.USub):
                return -v
            if isinstance(node.op, ast.UAdd):
                return +v
            raise ScriptError('this operator is not allowed in scripts.', self.line)
        if t is ast.BoolOp:
            if isinstance(node.op, ast.And):
                v = True
                for x in node.values:
                    v = self.eval(x, env)
                    if not v:
                        return v
                return v
            v = False
            for x in node.values:
                v = self.eval(x, env)
                if v:
                    return v
            return v
        if t is ast.Compare:
            left = self.eval(node.left, env)
            for op, comp in zip(node.ops, node.comparators):
                right = self.eval(comp, env)
                try:
                    ok = _CMPOPS[type(op)](_num(left), _num(right))
                except TypeError:
                    raise ScriptError(f'a {_tname(left)} cannot be compared with a {_tname(right)}.', self.line)
                if not ok:
                    return False
                left = right
            return True
        if t is ast.IfExp:
            return self.eval(node.body, env) if self._truth(self.eval(node.test, env)) else self.eval(node.orelse, env)
        if t is ast.Call:
            return self._call_node(node, env)
        if t is ast.Attribute:
            return self._attr(self.eval(node.value, env), node.attr)
        if t is ast.Subscript:
            obj = self.eval(node.value, env)
            if isinstance(node.slice, ast.Slice):
                s = node.slice
                lo = self.eval(s.lower, env) if s.lower is not None else None
                hi = self.eval(s.upper, env) if s.upper is not None else None
                st = self.eval(s.step, env) if s.step is not None else None
                if not isinstance(obj, (list, tuple, str)):
                    raise ScriptError(f'a {_tname(obj)} cannot be cut with [a:b].', self.line)
                return self._size(obj[slice(lo, hi, st)])
            key = self.eval(node.slice, env)
            try:
                if isinstance(obj, (list, tuple, str, dict)):
                    return obj[key]
                if isinstance(obj, SafeObject) and hasattr(obj, 'zeo_getitem'):
                    return obj.zeo_getitem(key)
            except KeyError:
                raise ScriptError(f'the key {key!r} is not in the dictionary – use .get({key!r}) when it may be missing.', self.line)
            except IndexError:
                raise ScriptError(f'item {key} is past the end of the list.', self.line)
            except TypeError:
                raise ScriptError(f'a {_tname(obj)} cannot be read with [{_tname(key)}].', self.line)
            raise ScriptError(f'a {_tname(obj)} cannot be read with [ ].', self.line)
        if t is ast.List:
            return self._size([self.eval(e, env) for e in node.elts])
        if t is ast.Tuple:
            return self._size(tuple(self.eval(e, env) for e in node.elts))
        if t is ast.Set:
            return self._size({self.eval(e, env) for e in node.elts})
        if t is ast.Dict:
            if any(k is None for k in node.keys):
                raise ScriptError('** in a dictionary is not allowed in scripts.', self.line)
            return self._size({self.eval(k, env): self.eval(v, env) for k, v in zip(node.keys, node.values)})
        if t in (ast.ListComp, ast.SetComp, ast.DictComp):
            return self._comp(node, env)
        if t is ast.JoinedStr:
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                elif isinstance(v, ast.FormattedValue):
                    val = self.eval(v.value, env)
                    spec = ''
                    if v.format_spec is not None:
                        spec = ''.join(str(x.value) for x in v.format_spec.values if isinstance(x, ast.Constant))
                    parts.append(_fmt(val, spec, self.line))
            return self._size(''.join(parts))
        raise ScriptError(f'"{type(node).__name__}" is not allowed in scripts.', self.line, 'refused')

    def _binop(self, op, a, b, node):
        f = _BINOPS.get(type(op))
        if f is None:
            raise ScriptError('this operator (bit operations, @) is not allowed in scripts.', self.line)
        if isinstance(op, ast.Mult):   # "x" * 10**8 / [0] * 10**7 – checked before anything is made
            for seq, n in ((a, b), (b, a)):
                if isinstance(seq, (str, list, tuple)) and isinstance(n, int):
                    self._grow(len(seq) * max(n, 0))
        if isinstance(op, ast.Pow):
            if isinstance(b, (int, Decimal, float)) and abs(b) > 1000:
                raise LimitError('the script was stopped: a power above 1000 is too large.', self.line, 'limit')
        if isinstance(op, ast.Add) and isinstance(a, (str, list, tuple)) and isinstance(b, (str, list, tuple)):
            self._grow(len(a) + len(b))
        try:
            a2, b2 = _num(a), _num(b)
            r = f(a2, b2)
        except ZeroDivisionError:
            raise ScriptError('division by zero.', self.line)
        except (TypeError, InvalidOperation):
            raise ScriptError(f'a {_tname(a)} and a {_tname(b)} cannot be used with this operator.', self.line)
        if isinstance(r, int) and not isinstance(r, bool) and abs(r) > 10 ** 30:
            raise LimitError('the script was stopped: a number became too large.', self.line, 'limit')
        return r

    def _comp(self, node, env):
        local = dict(env)
        out_list, out_dict, out_set = [], {}, set()

        def gen(i):
            if i == len(node.generators):
                if isinstance(node, ast.ListComp):
                    out_list.append(self.eval(node.elt, local))
                    self._grow(1)
                elif isinstance(node, ast.SetComp):
                    out_set.add(self.eval(node.elt, local))
                    self._grow(1)
                else:
                    out_dict[self.eval(node.key, local)] = self.eval(node.value, local)
                    self._grow(1)
                return
            g = node.generators[i]
            if g.is_async:
                raise ScriptError('"async" is not allowed in scripts.', self.line)
            seq = self.eval(g.iter, local)
            self._iterable(seq)
            for x in seq:
                self._tick(node)
                self._assign(g.target, x, local)
                if all(self._truth(self.eval(c, local)) for c in g.ifs):
                    gen(i + 1)

        gen(0)
        if isinstance(node, ast.ListComp):
            return out_list
        if isinstance(node, ast.SetComp):
            return out_set
        return out_dict

    def _call_node(self, node, env):
        fn = self.eval(node.func, env)
        args = [self.eval(a, env) for a in node.args]
        kwargs = {k.arg: self.eval(k.value, env) for k in node.keywords}
        return self.call(fn, args, kwargs)

    def call(self, fn, args, kwargs):
        if isinstance(fn, _Func):
            return self._call_func(fn, args, kwargs)
        if isinstance(fn, SafeFunction) or isinstance(fn, _BoundMethod):
            try:
                r = fn(*args, **kwargs)
            except (ScriptError, UserRaised, _Return, _Break, _Continue):
                raise
            except TypeError as e:
                raise ScriptError(f'{getattr(fn, "name", "this function")}(): {_plain_type_error(e)}', self.line)
            except (ValueError, ArithmeticError, KeyError, IndexError) as e:
                raise ScriptError(f'{getattr(fn, "name", "this function")}(): {e}', self.line)
            if isinstance(fn, _BoundMethod) and fn.grows and isinstance(fn.obj, (list, dict, set)):
                self._grow(0)
                if len(fn.obj) > self.limits['items']:
                    self._grow(len(fn.obj))
                self.items += 1
            return self._check_value(r)
        if fn in (_dt.date, _dt.datetime):
            try:
                return fn(*[int(a) for a in args])
            except (TypeError, ValueError) as e:
                raise ScriptError(f'date(): {e}', self.line)
        raise ScriptError(f'a {_tname(fn)} cannot be called.', self.line)

    def _check_value(self, v):
        if isinstance(v, SAFE_TYPES) or isinstance(v, (SafeObject, SafeFunction, _BoundMethod, range, enumerate, zip)) or type(v).__name__ == 'list_reverseiterator':
            return v
        if isinstance(v, (dict.keys().__class__, dict.values().__class__, dict.items().__class__)):
            return self._size(list(v))
        raise ScriptError('a function gave back a value scripts may not hold.', self.line)

    def _call_func(self, fn, args, kwargs):
        node = fn.node
        params = [a.arg for a in node.args.args]
        defaults = node.args.defaults
        local = {}
        if len(args) > len(params):
            raise ScriptError(f'{node.name}() takes {len(params)} value(s) but got {len(args)}.', self.line)
        for p, v in zip(params, args):
            local[p] = v
        for k, v in kwargs.items():
            if k not in params:
                raise ScriptError(f'{node.name}() has no argument "{k}".', self.line)
            local[k] = v
        first_default = len(params) - len(defaults)
        for i, p in enumerate(params):
            if p not in local:
                if i >= first_default:
                    local[p] = fn.interp.eval(defaults[i - first_default], {})
                else:
                    raise ScriptError(f'{node.name}() needs a value for "{p}".', self.line)
        self.depth += 1
        if self.depth > self.limits['depth']:
            raise LimitError(f'the script was stopped: functions called each other more than {self.limits["depth"]} deep.', self.line, 'limit')
        caller_line = self.line
        try:
            env = dict(self._module_env) if hasattr(self, '_module_env') else {}
            env.update(local)
            self._block(node.body, env)
            return None
        except _Return as r:
            return r.value
        finally:
            self.depth -= 1
            self.line = caller_line

    def _attr(self, obj, name):
        if name.startswith('_'):
            raise ScriptError(f'attributes starting with "_" are not allowed (.{name}).', self.line, 'refused')
        if isinstance(obj, SafeObject):
            if name not in obj.zeo_public:
                raise ScriptError(f'{getattr(obj, "zeo_label", "this object")} has no "{name}" (see Help for what it has).', self.line)
            v = getattr(obj, name)
            if callable(v) and not isinstance(v, (SafeObject, SafeFunction)):
                return _BoundMethod(v, f'{obj.zeo_label}.{name}')
            return v
        for typ, allowed in ATTRS.items():
            if type(obj) is typ or (typ is _dt.date and type(obj) is _dt.date):
                if name not in allowed:
                    break
                v = getattr(obj, name)
                if callable(v):
                    return _BoundMethod(v, f'{_tname(obj)}.{name}', grows=(typ.__name__, name) in GROWING, obj=obj, interp=self)
                return v
        raise ScriptError(f'a {_tname(obj)} has no "{name}" that scripts may use.', self.line)

    def run_text(self, code, env=None):
        tree = check(code)
        env = env if env is not None else {}
        self._module_env = env
        return self.run(tree, env)


class _BoundMethod:
    def __init__(self, fn, name, grows=False, obj=None, interp=None):
        self.fn, self.name, self.grows, self.obj, self.interp = fn, name, grows, obj, interp

    def __call__(self, *a, **k):
        if self.name.endswith('.sort') and 'key' in k and k['key'] is not None and self.interp is not None:
            key = k.pop('key')
            interp = self.interp
            return self.fn(key=lambda x: interp.call(key, [x], {}), **k)
        if self.interp is not None and isinstance(self.obj, str) and self.name.endswith('.replace') and len(a) >= 2:
            old, new = str(a[0]), str(a[1])   # "a"*1000 .replace("a", "a"*1000) – size checked before it is made
            n = self.obj.count(old) if old else len(self.obj) + 1
            self.interp._grow(len(self.obj) + n * max(len(new) - len(old), 0))
        if self.interp is not None and isinstance(self.obj, str) and self.name.endswith('.join') and a:
            parts = list(a[0])
            self.interp._grow(sum(len(str(x)) for x in parts) + len(self.obj) * max(len(parts) - 1, 0))
            a = (parts,) + tuple(a[1:])
        if self.name.endswith('.extend') and self.interp is not None and a:
            try:
                self.interp._grow(len(self.obj) + len(a[0]))
            except TypeError:
                pass
        return self.fn(*a, **k)

    zeo_label = 'function'


def _as_load(target):
    if isinstance(target, ast.Name):
        return ast.copy_location(ast.Name(id=target.id, ctx=ast.Load()), target)
    if isinstance(target, ast.Subscript):
        return ast.copy_location(ast.Subscript(value=target.value, slice=target.slice, ctx=ast.Load()), target)
    if isinstance(target, ast.Attribute):
        return ast.copy_location(ast.Attribute(value=target.value, attr=target.attr, ctx=ast.Load()), target)
    raise ScriptError('this kind of assignment is not allowed in scripts.', getattr(target, 'lineno', None))


def _num(v):
    """float and Decimal can be mixed in scripts (Decimal wins)."""
    if isinstance(v, float):
        return Decimal(repr(v))
    return v


def _text(v):
    if isinstance(v, Decimal):
        return format(v, 'f') if v.is_finite() and abs(v.adjusted()) <= 30 else str(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, SafeObject):
        return getattr(v, 'zeo_text', lambda: v.zeo_label)()
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(_text(x) for x in v) + ']'
    if isinstance(v, dict):
        return '{' + ', '.join(f'{_text(k)}: {_text(x)}' for k, x in v.items()) + '}'
    return str(v)


def _fmt(val, spec, line):
    if not spec:
        return _text(val)
    import re
    if not re.fullmatch(r'[<>^]?[0-9]{0,3}(,)?(\.[0-9]{1,2})?[fd%]?', spec):
        raise ScriptError(f'the format ":{spec}" is not allowed in scripts (use e.g. :.2f or :,.2f).', line)
    try:
        if isinstance(val, Decimal) and spec.endswith('d'):
            val = int(val)
        return format(val, spec)
    except (ValueError, TypeError):
        raise ScriptError(f'the format ":{spec}" does not fit a {_tname(val)}.', line)


def _plain_type_error(e):
    s = str(e)
    if 'positional argument' in s or 'required' in s:
        return 'the number of values given is not right.'
    if 'unexpected keyword' in s:
        return 'one of the names= given is not known.'
    if 'not supported between' in s or 'unsupported operand' in s:
        return 'these values cannot be used together.'
    return s.split('(')[0][:200]
