"""ZEO Developer – the script API, version v1 (D4). Stable: a later ZEO keeps these names and their meaning; a new name may be added,
nothing is removed or changed within v1.

    records.search(module, filters=None, limit=100, order=None)   records.get(module, id_or_number)   records.count(module, filters=None)
    records.create(module, data)
    record.id  record.number  record.state  record.module  record["field"] / record.field  record.get("field", default)
    record.rows("child table")  record.update({...}) / record.update(field=value)  record.move("transition", note="")
    employees.search(filters=None, limit=100)   employees.get(id_or_code)
    employee.id .code .name .first_name .last_name .gender .category .designation .department .branch .branch_id .email .mobile
             .joining_date .nationality .is_active .user_id
    send_email(to, subject, body)   notify(who, title, message)   today()  now()  log(...) / print(...)
    add_days(d, n)  add_months(d, n)  days_between(a, b)  month_start(d)  month_end(d)  lookup(table, key, column)
    actions.create_task(...) .leave_request(...) .expense_request(...) .document_expiry(...) .payroll_input(...)
           .settlement_deduction(...) .post_journal(...)
    http.get(url, params=None)  http.post(url, json=None)   – only sites an administrator allowed by name
    record / trigger – in a triggered script: the record and {"event", "transition", "old_state", "changed", "old"}

Filters: {"field": value} equals; "field__gt" / "__gte" / "__lt" / "__lte" / "__ne" / "__in" / "__contains" / "__empty"; "state" and
"number" too. Everything runs with the rights of the running user, in the company's own schema.
"""
import datetime
import re
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from .sandbox import SafeObject, SafeFunction, ScriptError, LimitError

API_VERSION = 'v1'


class RunContext:
    def __init__(self, user, access, settings, dry_run=False, script=None, run=None):
        self.user, self.access, self.settings, self.dry_run, self.script, self.run = user, access, settings, dry_run, script, run
        self.read = 0
        self.changed = 0
        self.changes = []
        self.log = []
        self.interp = None

    def count_read(self, n=1):
        self.read += n
        if self.read > self.settings.max_records_read:
            raise LimitError(f'the script was stopped: it read more than {self.settings.max_records_read:,} records.', self._line(), 'limit')

    def count_change(self, what):
        self.changed += 1
        if self.changed > self.settings.max_records_changed:
            raise LimitError(f'the script was stopped: it changed more than {self.settings.max_records_changed:,} records.', self._line(), 'limit')
        self.changes.append(what)

    def _line(self):
        return getattr(self.interp, 'line', None)

    def request(self):
        return _Req(self.user)


class _Req:
    """What ZeoFlow.custom and the access rules need of a request – nothing else (a script never sees it)."""

    def __init__(self, user):
        from django.db import connection
        self.user = user
        self.GET = {'schema': connection.schema_name}
        self.query_params = self.GET
        self.META = {}
        self.method = 'GET'
        self.data = {}


def _err(ctx, msg):
    return ScriptError(msg, ctx._line())


def _plain_value(v):
    """Record values for scripts: numbers as Decimal, dates as date, rows as a list of dicts."""
    return v


# ------------------------------------------------------------------ records
def _module(ctx, key):
    from ZeoFlow.models import CustomModule
    m = CustomModule.objects.filter(key=str(key or '')).first()
    if m is None:
        raise _err(ctx, f'there is no custom module "{key}" in this company.')
    return m


def _typed(m, data):
    out = {}
    types = {f['key']: f for f in m.fields}
    for k, v in (data or {}).items():
        f = types.get(k)
        t = (f or {}).get('type')
        res = (f or {}).get('result')
        if v in (None, ''):
            out[k] = v
        elif t in ('number', 'money') or (t == 'formula' and res in ('number', 'money', None)):
            try:
                out[k] = Decimal(str(v))
            except (InvalidOperation, ValueError):
                out[k] = v
        elif t == 'date' or (t == 'formula' and res == 'date'):
            try:
                out[k] = datetime.date.fromisoformat(str(v)[:10])
            except ValueError:
                out[k] = v
        elif t == 'table':
            out[k] = [_typed_row(f, r) for r in (v or [])]
        elif t == 'lookup':   # a rate value: a number when it is one
            try:
                out[k] = Decimal(str(v))
            except (InvalidOperation, ValueError):
                out[k] = v
        else:
            out[k] = v
    return out


def _typed_row(f, r):
    cols = {c['key']: c['type'] for c in (f.get('columns') or [])}
    out = {}
    for k, v in (r or {}).items():
        t = cols.get(k)
        if v in (None, ''):
            out[k] = v
        elif t in ('number', 'money'):
            try:
                out[k] = Decimal(str(v))
            except (InvalidOperation, ValueError):
                out[k] = v
        elif t == 'date':
            try:
                out[k] = datetime.date.fromisoformat(str(v)[:10])
            except ValueError:
                out[k] = v
        else:
            out[k] = v
    return out


def _to_store(v):
    if isinstance(v, Decimal):
        return format(v, 'f')
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()[:10]
    if isinstance(v, (list, tuple)):
        return [_to_store(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _to_store(x) for k, x in v.items()}
    if isinstance(v, (EmployeeProxy, RecordProxy)):
        return v.id
    return v


class RecordProxy(SafeObject):
    zeo_label = 'record'
    BASE = frozenset({'id', 'number', 'state', 'module', 'data', 'get', 'update', 'move', 'rows', 'owner_id', 'employee_id', 'created_at', 'history'})

    def __init__(self, r, ctx):
        self._r, self._ctx = r, ctx
        self._data = _typed(r.module, r.data)

    @property
    def zeo_public(self):
        return self.BASE | frozenset(f['key'] for f in self._r.module.fields)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in {f['key'] for f in self._r.module.fields}:
            return self._data.get(name)
        raise AttributeError(name)

    @property
    def id(self):
        return self._r.pk

    @property
    def number(self):
        return self._r.number

    @property
    def state(self):
        return self._r.state

    @property
    def module(self):
        return self._r.module.key

    @property
    def owner_id(self):
        return self._r.owner_id

    @property
    def employee_id(self):
        return self._r.employee_id

    @property
    def created_at(self):
        return self._r.created_at

    @property
    def data(self):
        return dict(self._data)

    @property
    def history(self):
        return [dict(h) for h in (self._r.history or [])][-50:]

    def get(self, key, default=None):
        v = self._data.get(key)
        return default if v in (None, '') else v

    def zeo_getitem(self, key):
        if key not in {f['key'] for f in self._r.module.fields}:
            raise _err(self._ctx, f'the module {self._r.module.key} has no field "{key}".')
        return self._data.get(key)

    def zeo_setitem(self, key, value):
        raise _err(self._ctx, 'change a record with record.update({...}) – then ZEO checks the values and keeps the history.')

    def zeo_setattr(self, key, value):
        raise _err(self._ctx, 'change a record with record.update({...}) – then ZEO checks the values and keeps the history.')

    def rows(self, child):
        f = next((x for x in self._r.module.fields if x['key'] == child and x['type'] == 'table'), None)
        if f is None:
            raise _err(self._ctx, f'"{child}" is not a child table of {self._r.module.key}.')
        return [dict(r) for r in (self._data.get(child) or [])]

    def update(self, values=None, **kw):
        from ZeoFlow import custom as C
        data = dict(values or {})
        data.update(kw)
        if not data:
            return self
        data = _to_store(data)
        try:
            self._r = C.update(self._r, data, self._ctx.request())
        except C.CustomError as e:
            raise _err(self._ctx, f'{self._r.number}: {e}')
        self._data = _typed(self._r.module, self._r.data)
        self._ctx.count_change({'action': 'update', 'record': self._r.number, 'module': self._r.module.key, 'values': data})
        return self

    def move(self, transition, note=''):
        from ZeoFlow import custom as C
        try:
            self._r = C.move(self._r, str(transition), str(note or ''), self._ctx.request())
        except C.CustomError as e:
            raise _err(self._ctx, f'{self._r.number}: {e}')
        self._ctx.count_change({'action': 'move', 'record': self._r.number, 'module': self._r.module.key, 'to': self._r.state})
        return self

    def zeo_text(self):
        return f'{self._r.module.key} {self._r.number}'


_OPS = ('gt', 'gte', 'lt', 'lte', 'ne', 'in', 'contains', 'empty', 'eq')


def _match(rec, typed, filters, ctx):
    for k, want in filters.items():
        field, _, op = str(k).partition('__')
        op = op or 'eq'
        if op not in _OPS:
            raise _err(ctx, f'filter "{k}": use one of __gt __gte __lt __lte __ne __in __contains __empty.')
        if field == 'state':
            have = rec.state
        elif field == 'number':
            have = rec.number
        elif field == 'id':
            have = rec.pk
        else:
            have = typed.get(field)
        want_c = _to_cmp(want, have)
        have_c = _to_cmp(have, want_c)
        try:
            if op == 'eq' and not _eq(have_c, want_c):
                return False
            if op == 'ne' and _eq(have_c, want_c):
                return False
            if op == 'in' and not any(_eq(have_c, _to_cmp(w, have)) for w in (want or [])):
                return False
            if op == 'contains' and str(want).lower() not in str(have or '').lower():
                return False
            if op == 'empty' and bool(want) != (have in (None, '', [])):
                return False
            if op in ('gt', 'gte', 'lt', 'lte'):
                if have_c in (None, ''):
                    return False
                if op == 'gt' and not have_c > want_c:
                    return False
                if op == 'gte' and not have_c >= want_c:
                    return False
                if op == 'lt' and not have_c < want_c:
                    return False
                if op == 'lte' and not have_c <= want_c:
                    return False
        except TypeError:
            raise _err(ctx, f'filter "{k}": the value cannot be compared with the field.')
    return True


def _eq(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return a == b


def _to_cmp(v, other):
    if isinstance(v, (RecordProxy, EmployeeProxy)):
        return v.id
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)) or (isinstance(other, Decimal) and isinstance(v, str)):
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            return v
    if isinstance(other, datetime.date) and isinstance(v, str):
        try:
            return datetime.date.fromisoformat(v[:10])
        except ValueError:
            return v
    return v


class RecordsAPI(SafeObject):
    zeo_label = 'records'
    zeo_public = frozenset({'search', 'get', 'count', 'create'})

    def __init__(self, ctx):
        self._ctx = ctx

    def _qs(self, module):
        from ZeoFlow import custom as C
        m = _module(self._ctx, module)
        return m, C.scope(m, self._ctx.request()).select_related('module')

    def search(self, module, filters=None, limit=100, order=None):
        if filters is not None and not isinstance(filters, dict):
            raise _err(self._ctx, 'records.search(module, filters, limit): filters is a dictionary, e.g. {"state": "open"}.')
        limit = max(1, min(int(limit or 100), 1000))
        m, qs = self._qs(module)
        out = []
        for r in qs.order_by('-id').iterator(chunk_size=200):
            self._ctx.count_read()
            typed = _typed(m, r.data)
            if _match(r, typed, filters or {}, self._ctx):
                out.append(RecordProxy(r, self._ctx))
                if len(out) >= limit and not order:
                    break
        if order:
            key, desc = (order[1:], True) if str(order).startswith('-') else (str(order), False)
            out.sort(key=lambda p: (p._data.get(key) is None, p._data.get(key) if key not in ('number', 'id', 'state') else getattr(p, key)), reverse=desc)
            out = out[:limit]
        return out

    def count(self, module, filters=None):
        return len(self.search(module, filters, limit=1000))

    def get(self, module, id_or_number):
        m, qs = self._qs(module)
        s = str(id_or_number)
        r = qs.filter(pk=int(s)).first() if s.isdigit() else qs.filter(number=s).first()
        if r is None:
            return None
        self._ctx.count_read()
        return RecordProxy(r, self._ctx)

    def create(self, module, data=None):
        from ZeoFlow import custom as C
        m = _module(self._ctx, module)
        if data is not None and not isinstance(data, dict):
            raise _err(self._ctx, 'records.create(module, data): data is a dictionary of field keys.')
        try:
            r = C.create(m, _to_store(data or {}), self._ctx.request())
        except C.CustomError as e:
            raise _err(self._ctx, f'{m.key}: {e}')
        self._ctx.count_change({'action': 'create', 'record': r.number, 'module': m.key, 'values': _to_store(data or {})})
        return RecordProxy(r, self._ctx)


# ------------------------------------------------------------------ employees (read only)
GENDER = {'M': 'Male', 'F': 'Female', 'O': 'Other'}


class EmployeeProxy(SafeObject):
    zeo_label = 'employee'
    zeo_public = frozenset({'id', 'code', 'name', 'first_name', 'last_name', 'gender', 'category', 'designation', 'department', 'branch', 'branch_id',
                            'email', 'mobile', 'joining_date', 'nationality', 'is_active', 'user_id'})

    def __init__(self, e):
        self.id = e.pk
        self.code = e.emp_code
        self.first_name = e.emp_first_name or ''
        self.last_name = e.emp_last_name or ''
        self.name = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
        self.gender = GENDER.get(e.emp_gender or '', e.emp_gender or '')
        self.category = getattr(e.emp_ctgry_id, 'ctgry_title', None) or (str(e.emp_ctgry_id) if e.emp_ctgry_id_id else '')
        self.designation = getattr(e.emp_desgntn_id, 'job_title', None) or (str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '')
        self.department = getattr(e.emp_dept_id, 'dept_name', None) or (str(e.emp_dept_id) if e.emp_dept_id_id else '')
        self.branch = getattr(e.emp_branch_id, 'branch_name', None) or (str(e.emp_branch_id) if e.emp_branch_id_id else '')
        self.branch_id = e.emp_branch_id_id
        self.email = e.emp_company_email or e.emp_personal_email or ''
        self.mobile = e.emp_mobile_number_1 or ''
        self.joining_date = e.emp_joined_date
        self.nationality = str(e.emp_nationality) if e.emp_nationality_id else ''
        self.is_active = bool(e.is_active)
        self.user_id = e.users_id

    def zeo_text(self):
        return f'{self.name} ({self.code})'

    def plain(self):
        return {k: getattr(self, k) for k in self.zeo_public}


class EmployeesAPI(SafeObject):
    zeo_label = 'employees'
    zeo_public = frozenset({'search', 'get'})

    def __init__(self, ctx):
        self._ctx = ctx

    def _qs(self):
        from EmpManagement.models import emp_master
        c = self._ctx.access
        qs = emp_master.objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id', 'emp_nationality')
        if c.admin:
            return qs
        if 'view_emp_master' in c.codes or 'change_emp_master' in c.codes:
            return qs if c.branches is None else qs.filter(emp_branch_id__in=c.branches)
        return qs.filter(pk=getattr(c.emp, 'pk', 0))   # self service: only their own record

    def search(self, filters=None, limit=100):
        if filters is not None and not isinstance(filters, dict):
            raise _err(self._ctx, 'employees.search(filters, limit): filters is a dictionary, e.g. {"gender": "Male"}.')
        limit = max(1, min(int(limit or 100), 5000))
        flt = dict(filters or {})
        bad = [k for k in flt if k.partition('__')[0] not in EmployeeProxy.zeo_public]
        if bad:
            raise _err(self._ctx, f'employees.search: "{bad[0]}" is not an employee field (see Help).')
        qs = self._qs()
        if 'is_active' not in flt:
            qs = qs.filter(is_active=True)
        out = []
        for e in qs.order_by('emp_code').iterator(chunk_size=200):
            self._ctx.count_read()
            p = EmployeeProxy(e)
            if _match_plain(p.plain(), flt, self._ctx):
                out.append(p)
                if len(out) >= limit:
                    break
        return out

    def get(self, id_or_code):
        qs = self._qs()
        s = str(getattr(id_or_code, 'id', id_or_code))
        e = qs.filter(pk=int(s)).first() if s.isdigit() else None
        if e is None:
            e = qs.filter(emp_code=s).first()
        if e is None:
            return None
        self._ctx.count_read()
        return EmployeeProxy(e)


class _Obj:
    def __init__(self, d):
        self.pk, self.state, self.number = d.get('id'), '', ''


def _match_plain(d, filters, ctx):
    class R:
        pk, state, number = d.get('id'), '', d.get('code')
    return _match(R, d, filters, ctx)


# ------------------------------------------------------------------ actions, mail, http
class ActionsAPI(SafeObject):
    zeo_label = 'actions'
    zeo_public = frozenset({'create_task', 'leave_request', 'expense_request', 'document_expiry', 'payroll_input', 'settlement_deduction', 'post_journal',
                            'send_email', 'notify'})

    def __init__(self, ctx):
        self._ctx = ctx

    def _do(self, action, **p):
        from . import actions as A
        try:
            return A.request(action, p, self._ctx)
        except A.ActionRefused as e:
            raise _err(self._ctx, e.message)

    def create_task(self, summary, assign_to, due_in_days=0, note='', record=None):
        return self._do('create_task', summary=summary, assign_to=assign_to, due_in_days=due_in_days, note=note,
                        record=getattr(record, 'number', record) or '')

    def leave_request(self, employee, leave_type, start, end=None, reason=''):
        return self._do('leave_request', employee=employee, leave_type=leave_type, start=start, end=end, reason=reason)

    def expense_request(self, employee, category, amount, date=None, description='', merchant=''):
        return self._do('expense_request', employee=employee, category=category, amount=amount, date=date, description=description, merchant=merchant)

    def document_expiry(self, employee, document_type, expiry):
        return self._do('document_expiry', employee=employee, document_type=document_type, expiry=expiry)

    def payroll_input(self, employee, code, value, month=None):
        return self._do('payroll_input', employee=employee, code=code, value=value, month=month)

    def settlement_deduction(self, employee, amount, reason):
        return self._do('settlement_deduction', employee=employee, amount=amount, reason=reason)

    def post_journal(self, lines, date=None, memo='', reference=''):
        return self._do('post_journal', lines=lines, date=date, memo=memo, reference=reference)

    def send_email(self, to, subject, body):
        return self._do('send_email', to=to, subject=subject, body=body)

    def notify(self, who, title, message):
        return self._do('notify', who=who, title=title, message=message)


class HttpAPI(SafeObject):
    zeo_label = 'http'
    zeo_public = frozenset({'get', 'post'})

    def __init__(self, ctx):
        self._ctx = ctx

    def _check(self, url):
        from urllib.parse import urlparse
        u = urlparse(str(url or ''))
        host = (u.hostname or '').lower()
        allowed = [str(h).strip().lower() for h in (self._ctx.settings.allowed_sites or []) if str(h).strip()]
        if u.scheme not in ('https', 'http') or not host:
            raise _err(self._ctx, f'http: "{url}" is not a web address.')
        if host not in allowed:
            raise _err(self._ctx, f'http: the site "{host}" is not allowed – an administrator adds it in Developer › Settings › Allowed sites.')
        if re.fullmatch(r'[0-9.]+|\[?[0-9a-f:]+\]?', host) or host in ('localhost',) or host.endswith('.local') or host.endswith('.internal'):
            raise _err(self._ctx, 'http: addresses inside the server network are never allowed.')
        return u.geturl()

    def _send(self, method, url, params=None, json=None):
        import requests
        url = self._check(url)
        self._ctx.count_read()
        if self._ctx.dry_run and method == 'POST':
            self._ctx.changes.append({'action': 'http_post', 'label': f'POST {url}'})
            return {'status': 0, 'text': '', 'json': None, 'test': True}
        try:
            r = requests.request(method, url, params=params, json=_to_store(json) if json is not None else None, timeout=8, allow_redirects=False, stream=True)
            body = r.raw.read(1_000_001, decode_content=True)
        except requests.RequestException as e:
            raise _err(self._ctx, f'http: the site did not answer ({type(e).__name__}).')
        if len(body) > 1_000_000:
            raise _err(self._ctx, 'http: the answer is larger than 1 MB.')
        text = body.decode(r.encoding or 'utf-8', errors='replace')
        out = {'status': r.status_code, 'text': text}
        try:
            import json as _j
            out['json'] = _j.loads(text, parse_float=Decimal)
        except ValueError:
            out['json'] = None
        return out

    def get(self, url, params=None):
        return self._send('GET', url, params=params)

    def post(self, url, json=None):
        return self._send('POST', url, json=json)


class ApiInfo(SafeObject):
    zeo_label = 'api'
    zeo_public = frozenset({'version', 'dry_run', 'company', 'user_id', 'user_name'})

    def __init__(self, ctx):
        from django.db import connection
        self.version = API_VERSION
        self.dry_run = ctx.dry_run
        self.company = connection.schema_name
        self.user_id = getattr(ctx.user, 'pk', None)
        self.user_name = (ctx.user.get_full_name() or ctx.user.username) if ctx.user else ''


def _local_today():
    from .formulas import _today
    return _today()


def globals_for(ctx, record=None, trigger=None, app_record=None):
    """Everything a script can use, besides the built-ins of the sandbox."""
    from . import formulas as F
    from .fields import lookup_value
    acts = ActionsAPI(ctx)

    def month_end(d):
        import calendar
        d = F._date(d)
        return d.replace(day=calendar.monthrange(d.year, d.month)[1])

    def lookup(table, key, column):
        try:
            return lookup_value(str(table), key, str(column))
        except ValueError as e:
            raise _err(ctx, f'lookup(): {e}.')

    g = {
        'records': RecordsAPI(ctx), 'employees': EmployeesAPI(ctx), 'actions': acts, 'http': HttpAPI(ctx), 'api': ApiInfo(ctx),
        'send_email': SafeFunction(acts.send_email, 'send_email'), 'notify': SafeFunction(acts.notify, 'notify'),
        'today': SafeFunction(_local_today, 'today'), 'now': SafeFunction(lambda: timezone.localtime(), 'now'),
        'add_days': SafeFunction(lambda d, n: F._date(d) + datetime.timedelta(days=int(n)), 'add_days'),
        'add_months': SafeFunction(F.add_months, 'add_months'),
        'days_between': SafeFunction(lambda a, b: (F._date(b) - F._date(a)).days, 'days_between'),
        'month_start': SafeFunction(lambda d: F._date(d).replace(day=1), 'month_start'),
        'month_end': SafeFunction(month_end, 'month_end'), 'lookup': SafeFunction(lookup, 'lookup'),
    }
    from .appdata import DataAPI, AppRecord
    g['data'] = DataAPI(ctx)
    if record is not None:
        g['record'] = RecordProxy(record, ctx)
    elif app_record is not None:
        g['record'] = AppRecord(app_record[1], ctx, app_record[0])
    g['trigger'] = dict(trigger or {})
    return g


# the help page (D4) – one entry per function, with an example
HELP = [
    {'group': 'Records of custom modules', 'items': [
        ('records.search(module, filters=None, limit=100, order=None)', 'Records of a module the running user may see. Filters: {"field": value}, "field__gt", "__gte", "__lt", "__lte", "__ne", "__in", "__contains", "__empty"; also "state" and "number". order="-amount" sorts.',
         'rooms = records.search("camp_room", {"gender": "Male", "free_beds__gt": 0}, limit=10)'),
        ('records.get(module, id_or_number)', 'One record (None when not found or not visible to the user).', 'r = records.get("vehicle", "VEH-00003")'),
        ('records.count(module, filters=None)', 'How many records match.', 'n = records.count("trip", {"flagged": True})'),
        ('records.create(module, data)', 'A new record (checked like the screen; calculated fields are filled).', 'records.create("trip", {"vehicle": v.id, "start_km": 100, "end_km": 180})'),
        ('record.update({...}) / record.update(field=value)', 'Changes fields of the record (checked, kept in its history).', 'record.update({"room": room.id, "bed": 2})'),
        ('record.move(transition, note="")', 'Makes a move of the Blueprint (the user must be allowed to make it).', 'record.move("approve", "checked by the script")'),
        ('record["field"], record.field, record.get("field", default)', 'Values: numbers and amounts as numbers, dates as dates.', 'if record["fuel_litres"] > 50:'),
        ('record.rows("child table")', 'The rows of a child table as a list of dictionaries.', 'total = sum([r["cost"] for r in record.rows("fuel")])'),
        ('record.id, record.number, record.state, record.module', 'The record\'s id, number, state key and module key.', 'log(record.number, record.state)'),
    ]},
    {'group': 'Every app (CRM, Projects, Property, Maintenance, Asset Management, People, HSE, HR, Payroll …)', 'items': [
        ('data.apps(), data.models(app=None), data.fields(key)', 'The active apps, their record types ({"key": "ZeoCRM.lead", "label", "app", "writable", "approval"}) and the fields of a type.', 'for m in data.models("crm"):\n    log(m["key"], m["label"])'),
        ('data.search(key, filters=None, limit=100), data.get(key, id), data.count(key, filters=None)', 'Records of any app the user may see (the same rows as the lists on screen). Values by field name; links as ids; record.link("field") reads the linked record.',
         'open_tickets = data.search("ZeoProjects.ticket", {"status": "open"})'),
        ('data.create(key, values), data.update(key, id, values), record.update({...})', 'Through the app\'s own screen API (its checks, numbering, approvals). Never: payroll, payslips, salary, gratuity, WPS, balances, users, rights, security. Types that hold money (invoices, payments, journals …) wait for an administrator\'s approval. In a test run, and while a change waits for approval, the answer is a planned change: x.status is "dry" or "pending" and x.id is None.',
         'data.create("ZeoCRM.activity", {"subject": "Call back", "lead": lead.id})'),
        ('Trigger "When a record of an app is created / changed"', 'The script gets that record as record (e.g. a CRM lead, a maintenance work order, an HSE incident).', 'if record["priority"] == "high":\n    notify(record["owner"], "High priority", record.label)'),
    ]},
    {'group': 'Employees (read only)', 'items': [
        ('employees.search(filters=None, limit=100)', 'Active employees the user may see (add {"is_active": False} for leavers). Fields: id, code, name, first_name, last_name, gender, category, designation, department, branch, branch_id, email, mobile, joining_date, nationality, is_active, user_id.',
         'drivers = employees.search({"designation__contains": "driver"})'),
        ('employees.get(id_or_code)', 'One employee (None when not found or not visible).', 'e = employees.get("E0012")'),
    ]},
    {'group': 'Messages', 'items': [
        ('send_email(to, subject, body)', 'E-mail to addresses or employees (through the company\'s mail server). Recorded in Developer › Actions.', 'send_email(e, "Fuel above the norm", f"Trip {record.number}: {record[\'fuel_100km\']} l/100 km")'),
        ('notify(who, title, message)', 'A ZEO notification to an employee or user.', 'notify(record.employee_id, "Bed allocated", f"Room {room[\'room_no\']}")'),
    ]},
    {'group': 'Safe actions (D2)', 'items': [
        ('actions.create_task(summary, assign_to, due_in_days=0, note="", record=None)', 'A to-do for an employee / user.', 'actions.create_task("Check the van", e, 2, record=record)'),
        ('actions.leave_request(employee, leave_type, start, end=None, reason="")', 'A leave request (the leave approvals follow, as on screen).', 'actions.leave_request(e, "AL", "2026-12-01", "2026-12-03", "Camp move")'),
        ('actions.expense_request(employee, category, amount, date=None, description="")', 'An expense claim (the employee adds it to a report).', 'actions.expense_request(e, "Fuel", 85.50, today(), "Trip " + record.number)'),
        ('actions.document_expiry(employee, document_type, expiry)', 'Sets the expiry date of an employee document.', 'actions.document_expiry(e, "Passport", "2031-05-30")'),
        ('actions.payroll_input(employee, code, value, month=None)', 'A payroll monthly input – waits for an administrator\'s approval.', 'actions.payroll_input(e, "FUELDED", 120, "2026-11")'),
        ('actions.settlement_deduction(employee, amount, reason)', 'A deduction on the open final settlement – waits for approval.', 'actions.settlement_deduction(e, 300, "Camp key not returned")'),
        ('actions.post_journal(lines, date=None, memo="")', 'A balanced journal to Accounting – waits for approval. lines: [{"account", "debit" or "credit", "label"}].', 'actions.post_journal([{"account": "6100", "debit": 500}, {"account": "1500", "credit": 500}], memo="Depreciation")'),
    ]},
    {'group': 'Dates and helpers', 'items': [
        ('today(), now()', 'Today\'s date / the time in the company\'s time zone.', 'if record["expiry"] < today():'),
        ('add_days(d, n), add_months(d, n), days_between(a, b), month_start(d), month_end(d)', 'Date arithmetic.', 'due = add_days(today(), 14)'),
        ('lookup(table, key, column)', 'A value from a rate table (Developer › Rate tables).', 'norm = lookup("fuel_norm", "Pick-up", "litres_100km")'),
        ('log(...) / print(...)', 'A line in the run log (shown under "Test on sample data").', 'log("allocated", count, "beds")'),
        ('stop(message)', 'Stops the script with a message (the run is marked as failed, nothing it changed is kept).', 'stop("no free bed for " + e.name)'),
        ('http.get(url, params=None), http.post(url, json=None)', 'Only sites an administrator allowed by name (Developer › Settings). Answer: {"status", "text", "json"}.', 'r = http.get("https://api.example.com/rates")'),
        ('api.version, api.dry_run, api.company', 'The script API version (v1), whether this is a test run, the company.', 'if api.dry_run: log("test run")'),
    ]},
    {'group': 'Python you can use', 'items': [
        ('if / elif / else, for, while, break, continue, def, return, try / except, f-strings, lists, dictionaries, sets', 'Plain Python. Not available: import, class, with, lambda, open, eval, getattr, names starting with "_".', 'for r in rooms:\n    if r["free_beds"] > 0:\n        break'),
        ('len, range, min, max, sum, abs, round, int, float, str, bool, list, dict, set, tuple, sorted, enumerate, zip, any, all, reversed, number', 'Built-ins. number("12.5") makes an exact number (money).', 'total = round(sum([number(x) for x in amounts]), 2)'),
    ]},
]
