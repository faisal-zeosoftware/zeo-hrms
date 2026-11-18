"""ZEO Developer – every app of ZEO in scripts (the all-apps extension of 1.74.0).

  data.apps()                         the apps of the company (HR, Payroll, Projects, CRM, Property, Maintenance, Asset Management,
                                      People, HSE, Company & users) – only the active ones
  data.models(app=None)               their record types: [{"key": "ZeoCRM.lead", "label": "Lead", "app": "crm"}]
  data.fields(key)                    the fields of a record type
  data.search(key, filters=None, limit=100)   data.get(key, id)   data.count(key, filters=None)
  data.create(key, values)            data.update(key, id, values)     (record.update(...) on a record read with data.*)

Reading uses the same row rules as the lists on screen (AccessControl.scope_queryset: company administrator → everything; a right
view_<record type> → the user's branches; otherwise only the user's own / team's rows; confidential tables never).

Writing goes through the app's own API (the same view the screen calls, as the running user): its checks, numbering, approvals and
history apply. Never written by a script:
  * ZEO's own code and data – payroll runs, payslips, salary structures, gratuity, WPS / SIF, leave balances, users, rights, login,
    security, cloud, licence, AI keys (PROTECTED_APPS / PROTECTED_WORDS);
  * record types that hold money (invoices, payments, receipts, journals, loans, advances …) – a script's write waits for an
    administrator's approval (safe action "app_write").
Triggers: a script can run when a record of any of these types is created or changed (trigger "app_create" / "app_update").
"""
import datetime
import re
import threading
from decimal import Decimal

from django.apps import apps
from django.db import connection, models

from .sandbox import SafeObject

PROTECTED_APPS = {'UserManagement', 'AccessControl', 'ZeoCloud', 'ZeoLicence', 'ZeoGroup', 'AISettings', 'ZeoAdmin', 'ZeoDeveloper', 'ZeoFlow',
                  'Translations', 'LanguagePack', 'tenant_users', 'auth', 'contenttypes', 'sessions', 'admin', 'django_celery_beat',
                  'django_celery_results', 'token_blacklist', 'PayrollManagement', 'ZeoMobile', 'SavedViews', 'DataTools', 'CountryPack'}
PROTECTED_WORDS = ('payroll', 'payslip', 'salary', 'gratuity', 'wps', 'sif', 'balance', 'accrual', 'permission', 'password', 'token', 'secret',
                   'apikey', 'api_key', 'webhook', 'licence', 'license', 'login', 'session', 'otp', 'statutory', 'pension', 'endofservice',
                   'exitsettlement', 'setting', 'audit')
MONEY_WORDS = ('invoice', 'payment', 'receipt', 'bill', 'journal', 'payout', 'commission', 'loan', 'advance', 'settlement', 'encash', 'expense',
               'refund', 'deposit', 'cheque', 'pdc', 'creditnote', 'quotation', 'salesorder', 'price', 'budget', 'cost')
HIDDEN_FIELDS = {'password', 'otp', 'otp_created_at', 'face_encoding', 'secret', 'token', 'api_key', 'key_hash', 'refresh_token', 'access_token',
                 'client_secret', 'private_key'}
_routes = {}
_lock = threading.Lock()


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


# ------------------------------------------------------------------ record types
def registry():
    from ZeoFlow import registry as R
    return R


def model_of(key):
    m = registry().get_model(str(key or ''))
    if m is None:
        # "zeocrm.lead" / "ZeoCRM.Lead" – any case
        try:
            label, name = str(key).split('.', 1)
        except ValueError:
            return None
        for cfg in apps.get_app_configs():
            if cfg.label.lower() == label.lower():
                for mm in cfg.get_models():
                    if mm._meta.model_name == name.lower():
                        return mm
    return m


def known(key):
    """Is the record type one of the apps' types (the same list as the automation engine)?"""
    m = model_of(key)
    if m is None:
        return None
    k = registry().model_key(m)
    if k == 'ZeoFlow.customrecord':
        return None
    if any(d['key'] == k for d in registry().models_of()):
        return m
    return None


def is_protected(m):
    n = m._meta.model_name.lower()
    return m._meta.app_label in PROTECTED_APPS or any(w in n for w in PROTECTED_WORDS)


def is_money(m):
    n = m._meta.model_name.lower()
    return m._meta.app_label in ('ZeoAccounting', 'PayrollPlus') or any(w in n for w in MONEY_WORDS)


def visible_fields(m):
    out = []
    for f in m._meta.concrete_fields:
        if f.name in HIDDEN_FIELDS or any(w in f.name for w in ('password', 'secret', 'token')):
            continue
        out.append(f)
    return out


# ------------------------------------------------------------------ the API routes of each record type (for writing)
def _join(prefix, p):
    s = str(p)
    s = s.lstrip('^').rstrip('$')
    return prefix + s


def routes():
    """{model key: {'list': '/zeo-crm/api/leads/', 'detail': '/zeo-crm/api/leads/{pk}/'}} found in the URL patterns (router view sets)."""
    with _lock:
        if _routes:
            return _routes
        from django.urls import get_resolver, URLPattern, URLResolver

        def walk(patterns, prefix):
            for p in patterns:
                if isinstance(p, URLResolver):
                    walk(p.url_patterns, _join(prefix, p.pattern))
                elif isinstance(p, URLPattern):
                    cb = p.callback
                    cls = getattr(cb, 'cls', None) or getattr(cb, 'view_class', None)
                    actions = getattr(cb, 'actions', None)
                    if cls is None:
                        continue
                    if cls.__name__ in ('RegisterListView', 'RegisterDetailView') and str(p.pattern).count('<') == (1 if cls.__name__ == 'RegisterListView' else 2):
                        _kit_routes(cb, cls, '/' + _join(prefix, p.pattern))   # CRM, Property, Maintenance, Assets, People … (app kit)
                        continue
                    if actions is None:   # an APIView: the methods it has (Projects, CRM … screens)
                        actions = {mth: mth for mth in ('post', 'patch', 'put') if hasattr(cls, mth)}
                    if not actions:
                        continue
                    m = getattr(cls, 'zeo_model', None)
                    if m is None:
                        try:
                            from AccessControl.access import view_model
                            m = view_model(cls)
                        except Exception:   # noqa: BLE001
                            m = None
                    if m is None:
                        continue
                    k = registry().model_key(m)
                    path = '/' + _join(prefix, p.pattern)
                    if '(?P<format>' in path or '\\.' in path:
                        continue
                    if 'post' in actions and 'pk' not in path and '(?P<' not in path and '<' not in path:
                        _routes.setdefault(k, {}).setdefault('list', path)
                    elif ('patch' in actions or 'put' in actions) and re.search(r'\(\?P<(pk|id)>[^)]*\)|<int:(pk|id)>|<(pk|id)>', path) and path.count('(?P<') + path.count('<int:') + path.count('<str:') <= 1:
                        _routes.setdefault(k, {}).setdefault('detail', re.sub(r'\(\?P<(pk|id)>[^)]*\)|<int:(pk|id)>|<(pk|id)>', '{pk}', path))
                        if 'patch' not in actions:
                            _routes[k]['detail_method'] = 'put'
        walk(get_resolver().url_patterns, '')
        return _routes


_kit = {}   # model key → (kit app, register): the apps built on zeo.appkit keep their own rules of who sees what


def _kit_routes(cb, cls, path):
    """An app-kit app serves every register at api/r/<kind>/ (list, POST = new) and api/r/<kind>/<pk>/ (PATCH = change)."""
    a = (getattr(cb, 'view_initkwargs', None) or {}).get('app')
    if a is None:
        return
    for kind, r in (getattr(a, 'regs', None) or {}).items():
        try:
            k = registry().model_key(r.model)
        except Exception:   # noqa: BLE001
            continue
        if not k:
            continue
        _kit[k] = (a, r)
        if cls.__name__ == 'RegisterListView':
            if getattr(r, 'create', 'manage') != 'none':
                _routes.setdefault(k, {})['list'] = path.replace('<str:kind>', kind)
        else:
            _routes.setdefault(k, {})['detail'] = path.replace('<str:kind>', kind).replace('<int:pk>', '{pk}')
            _routes[k].pop('detail_method', None)


# ------------------------------------------------------------------ reading
class _View:
    action = 'list'
    kwargs = {}
    zeo_scope = True


def scoped(m, ctx):
    from AccessControl.access import scope_queryset
    routes()
    kr = _kit.get(registry().model_key(m))
    if kr is not None:   # the register's own rule (roles, own records, branches) – the same rows as its list on screen
        return kr[1].scope(ctx.request())
    return scope_queryset(ctx.request(), _View(), m._default_manager.all())


def plain(v):
    if isinstance(v, models.Model):
        return v.pk
    if isinstance(v, float):
        return Decimal(repr(v))
    if isinstance(v, (str, int, bool, Decimal, datetime.date, datetime.datetime, datetime.time, type(None))):
        return v
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v][:500]
    if isinstance(v, dict):
        return {str(k): plain(x) for k, x in list(v.items())[:500]}
    if hasattr(v, 'name') and hasattr(v, 'url'):   # a file field
        return getattr(v, 'name', '') or ''
    return str(v)[:500]


def values_of(obj):
    out = {'id': obj.pk}
    for f in visible_fields(type(obj)):
        out[f.name] = plain(getattr(obj, f.attname, None))
    return out


class AppRecord(SafeObject):
    zeo_label = 'record'

    def __init__(self, obj, ctx, key):
        self._obj, self._ctx, self._key = obj, ctx, key
        self._vals = values_of(obj)

    @property
    def zeo_public(self):
        return frozenset(self._vals) | {'id', 'key', 'label', 'data', 'get', 'update', 'link'}

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        if name in self._vals:
            return self._vals[name]
        raise AttributeError(name)

    @property
    def id(self):
        return self._obj.pk

    @property
    def key(self):
        return self._key

    @property
    def label(self):
        try:
            return str(self._obj)[:255]
        except Exception:   # noqa: BLE001
            return f'#{self._obj.pk}'

    @property
    def data(self):
        return dict(self._vals)

    def get(self, name, default=None):
        v = self._vals.get(name)
        return default if v in (None, '') else v

    def zeo_getitem(self, name):
        if name not in self._vals:
            from .sandbox import ScriptError
            raise ScriptError(f'{self._key} has no field "{name}".', self._ctx._line())
        return self._vals[name]

    def link(self, name):
        """The record a link field points to (read with the same rules)."""
        from .sandbox import ScriptError
        f = next((x for x in type(self._obj)._meta.concrete_fields if x.name == name and x.is_relation), None)
        if f is None:
            raise ScriptError(f'"{name}" is not a link field of {self._key}.', self._ctx._line())
        pk = getattr(self._obj, f.attname, None)
        if pk is None:
            return None
        k = registry().model_key(f.related_model)
        return DataAPI(self._ctx).get(k, pk)

    def update(self, values=None, **kw):
        vals = dict(values or {})
        vals.update(kw)
        r = DataAPI(self._ctx).update(self._key, self._obj.pk, vals)
        if isinstance(r, AppRecord):
            self._obj, self._vals = r._obj, r._vals
        return r

    def zeo_text(self):
        return f'{self._key} #{self._obj.pk}'


def _filter_rows(qs, m, filters, ctx):
    """Filters on the database where they are simple (equals / ranges on own fields), the rest checked row by row."""
    from .api import _match
    flt = dict(filters or {})
    names = {f.name: f for f in visible_fields(m)}
    db, rest = {}, {}
    for k, v in flt.items():
        field, _, op = str(k).partition('__')
        op = op or 'eq'
        if field not in names and field != 'id':
            from .sandbox import ScriptError
            raise ScriptError(f'"{field}" is not a field of {registry().model_key(m)} (data.fields shows them).', ctx._line())
        f = names.get(field)
        col = f.attname if f is not None else 'id'
        if isinstance(v, SafeObject) and hasattr(v, 'id'):
            v = v.id
        if op == 'eq' and not isinstance(v, (dict, list)):
            db[col] = v
        elif op in ('gt', 'gte', 'lt', 'lte') and isinstance(v, (int, Decimal, datetime.date, str)):
            db[f'{col}__{op}'] = v
        elif op == 'in' and isinstance(v, (list, tuple)):
            db[f'{col}__in'] = list(v)
        else:
            rest[k] = v
    try:
        qs = qs.filter(**db)
    except Exception as e:   # noqa: BLE001 – a wrong type for the field
        from .sandbox import ScriptError
        raise ScriptError(f'a filter does not fit the field ({str(e)[:120]}).', ctx._line())
    return qs, rest


class DataAPI(SafeObject):
    zeo_label = 'data'
    zeo_public = frozenset({'apps', 'models', 'fields', 'search', 'get', 'count', 'create', 'update'})

    def __init__(self, ctx):
        self._ctx = ctx

    def _model(self, key, write=False):
        from .sandbox import ScriptError
        m = known(key)
        if m is None:
            raise ScriptError(f'"{key}" is not a record type of the active apps (data.models() lists them).', self._ctx._line())
        if write and is_protected(m):
            raise ScriptError(f'{registry().model_key(m)} belongs to ZEO\'s own calculations or security – scripts may read it, never change it.', self._ctx._line())
        return m

    def apps(self):
        return [dict(x) for x in registry().modules()]

    def models(self, app=None):
        r = routes()
        return [{'key': d['key'], 'label': d['label'], 'app': d['module'],
                 'writable': not is_protected(registry().get_model(d['key'])) and bool(r.get(d['key'], {}).get('list')),
                 'approval': is_money(registry().get_model(d['key']))} for d in registry().models_of(app or None)]

    def fields(self, key):
        m = self._model(key)
        vis = {f.name for f in visible_fields(m)}
        return [{'key': d['key'], 'label': d['label'], 'type': d['type'], 'required': d.get('required', False), **({'link': d['link']} if d.get('link') else {}),
                 **({'choices': [c['value'] for c in d['choices']]} if d.get('choices') else {})}
                for d in registry().fields_of(registry().model_key(m), with_related=False) if d['key'] in vis]

    def search(self, key, filters=None, limit=100):
        from .sandbox import ScriptError
        from .api import _match
        if filters is not None and not isinstance(filters, dict):
            raise ScriptError('data.search(key, filters, limit): filters is a dictionary.', self._ctx._line())
        m = self._model(key)
        k = registry().model_key(m)
        limit = max(1, min(int(limit or 100), 1000))
        qs, rest = _filter_rows(scoped(m, self._ctx), m, filters, self._ctx)
        out = []
        for obj in qs.order_by('-pk').iterator(chunk_size=200):
            self._ctx.count_read()
            rec = AppRecord(obj, self._ctx, k)
            if rest:
                class R:
                    pk, state, number = obj.pk, '', ''
                if not _match(R, rec._vals, rest, self._ctx):
                    continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    def count(self, key, filters=None):
        return len(self.search(key, filters, 1000))

    def get(self, key, pk):
        m = self._model(key)
        try:
            obj = scoped(m, self._ctx).filter(pk=int(getattr(pk, 'id', pk))).first()
        except (TypeError, ValueError):
            return None
        if obj is None:
            return None
        self._ctx.count_read()
        return AppRecord(obj, self._ctx, registry().model_key(m))

    def create(self, key, values=None):
        return self._write(key, None, values)

    def update(self, key, pk, values=None):
        return self._write(key, int(getattr(pk, 'id', pk)), values)

    def _write(self, key, pk, values):
        from .sandbox import ScriptError
        from . import actions as A
        from .api import _to_store
        m = self._model(key, write=True)
        k = registry().model_key(m)
        if not isinstance(values, dict) or not values:
            raise ScriptError(f'data.{"update" if pk else "create"}: give the values as a dictionary.', self._ctx._line())
        r = routes().get(k, {})
        if not r.get('detail' if pk else 'list'):
            raise ScriptError(f'{k} cannot be {"changed" if pk else "created"} by scripts (it has no screen API for it).', self._ctx._line())
        vals = _to_store(values)
        bad = [x for x in vals if x in HIDDEN_FIELDS]
        if bad:
            raise ScriptError(f'"{bad[0]}" cannot be set by scripts.', self._ctx._line())
        if pk is not None and not scoped(m, self._ctx).filter(pk=pk).exists():
            raise ScriptError(f'{k} #{pk} was not found (or you may not see it).', self._ctx._line())
        try:
            res = A.request('app_write', {'key': k, 'pk': pk, 'values': vals}, self._ctx)
        except A.ActionRefused as e:
            raise ScriptError(e.message, self._ctx._line())
        self._ctx.count_change({'action': 'update' if pk else 'create', 'record': f'{k} #{pk or res.get("record_id") or "new"}', 'module': k, 'values': vals})
        if res.get('status') == 'done' and res.get('record_id'):
            obj = m._default_manager.filter(pk=res['record_id']).first()
            if obj is not None:
                return AppRecord(obj, self._ctx, k)
        return Planned(res)


class Planned(SafeObject):
    """What data.create / update gives back when the write did not happen yet: in a test run (dry run), or waiting for an
    administrator's approval (a record type that holds money). id is None until it is done."""
    zeo_label = 'planned change'
    zeo_public = frozenset({'id', 'status', 'label', 'needs_approval', 'action_id'})

    def __init__(self, res):
        self.id = res.get('record_id') if res.get('status') == 'done' else None
        self.status = res.get('status') or ''
        self.label = res.get('label') or ''
        self.needs_approval = bool(res.get('needs_approval'))
        self.action_id = res.get('id')

    def zeo_text(self):
        return f'{self.label} ({self.status})'


def write(user, key, pk, values):
    """Runs an app_write safe action: POST / PATCH to the app's API as the user. Returns {'record_id': id}."""
    from .internal import call
    m = known(key)
    r = routes().get(registry().model_key(m), {})
    if pk is None:
        _st, data = call(user, 'post', r['list'], values)
    else:
        _st, data = call(user, r.get('detail_method', 'patch'), r['detail'].replace('{pk}', str(pk)), values)
    rid = (data or {}).get('id') if isinstance(data, dict) else None
    if pk is not None:
        _check_applied(m, pk, values)
    return {'record_id': rid or pk}


def _same(cur, want):
    if isinstance(cur, models.Model):
        cur = cur.pk
    if cur is None or want is None:
        return (cur in (None, '')) == (want in (None, ''))
    if isinstance(cur, bool) or isinstance(want, bool):
        return str(cur).lower() in ('true', '1') if str(want).lower() in ('true', '1') else str(cur).lower() in ('false', '0', '')
    try:
        return Decimal(str(cur)) == Decimal(str(want))
    except Exception:   # noqa: BLE001
        pass
    if isinstance(cur, (datetime.date, datetime.datetime)):
        return str(want)[:len(cur.isoformat()[:10])] == cur.isoformat()[:10] if not isinstance(cur, datetime.datetime) else str(want)[:16].replace(' ', 'T') == cur.isoformat()[:16]
    return str(cur).strip() == str(want).strip()


def _check_applied(m, pk, values):
    """An app's screen API may ignore a field it does not let you change there (e.g. the description of a reported HSE incident).
    The script is told, instead of believing the change was made."""
    from .internal import ApiRefused
    obj = m._default_manager.filter(pk=pk).first()
    if obj is None:
        return
    names = {f.name: f for f in m._meta.concrete_fields}
    names.update({f.attname: f for f in m._meta.concrete_fields})
    missed = []
    for k, v in values.items():
        f = names.get(k)
        if f is None:
            continue
        if not _same(getattr(obj, f.attname), v.pk if isinstance(v, models.Model) else v):
            missed.append(k)
    if missed:
        raise ApiRefused(f'{registry().model_key(m)} #{pk}: the app did not change {", ".join(missed)} – it cannot be changed on its screen '
                          f'at this stage (use the record\'s actions on the {m._meta.app_label} screen).', 409)


# ------------------------------------------------------------------ links to any app's record (field type "link", target model:<key>)
def link_qs(target):
    key = target[6:]
    m = known(key)
    if m is None:
        return None, None
    return m._default_manager.all(), (lambda o: str(o)[:120])


# ------------------------------------------------------------------ triggers on any app's records
_cache = {}


def _mark_key(sch):
    return f'zeodev:app-triggers:{sch}'


def _marker(sch):
    """v1.70.3: the company's change marker in the shared cache (Redis) – every gunicorn worker sees a script made, changed,
    switched on / off or deleted in another worker (the per-process list alone kept a new trigger silent there up to 30 s)."""
    try:
        from django.core.cache import cache
        return cache.get(_mark_key(sch)) or 0
    except Exception:   # noqa: BLE001 – no cache: the 30-second refresh still applies
        return 0


def scripts_for(key):
    from django.utils import timezone
    from .models import Script, DeveloperSettings
    sch = connection.schema_name
    now = timezone.now().timestamp()
    ts, by, mark, checked = _cache.get(sch, (0, None, 0, 0))
    if by is not None:   # the shared marker on every save (one Redis read): a script switched on is used by the very next save
        m = _marker(sch)
        checked = now
        if m != mark:
            by = None
        else:
            _cache[sch] = (ts, by, mark, checked)
    if by is None or now - ts > 30:
        mark = _marker(sch)
        by = {}
        try:
            from django.db import transaction
            with transaction.atomic():   # v1.73.1: a savepoint – a failed query must not leave the caller's transaction aborted
                if DeveloperSettings.get().developer_mode:
                    for s in Script.objects.filter(trigger__in=('app_create', 'app_update'), active=True, stopped=False):
                        by.setdefault(s.model_key.lower(), []).append(s.pk)
        except Exception:   # noqa: BLE001 – before the tables exist
            by = {}
        _cache[sch] = (now, by, mark, now)
    return by.get(str(key).lower(), [])


def invalidate(sch=None):
    sch = sch or connection.schema_name
    _cache.pop(sch, None)
    try:
        import time
        from django.core.cache import cache
        cache.set(_mark_key(sch), time.time_ns(), None)
    except Exception:   # noqa: BLE001
        pass


def on_script_change(sender, instance=None, **kw):
    """v1.70.3: a script or the Developer settings saved / deleted → every worker reloads its list after the commit."""
    from django.db import transaction
    sch = connection.schema_name
    if sch == 'public':
        return
    transaction.on_commit(lambda: invalidate(sch))


_local = threading.local()


def on_save(sender, instance, created=False, raw=False, **kw):
    if raw or connection.schema_name == 'public' or sender._meta.app_label in PROTECTED_APPS or getattr(_local, 'busy', False):
        return
    # v1.73.1: not for the rows that record migrations – in a company still being created, the lookup below ran before the
    # ZeoDeveloper tables existed and left the migration's transaction aborted, so the migration was lost without an error
    # (found by the 1.73.1 test: a new company lacked ZeoCloud 0008; the same cause as the CRM update's fix 1)
    if sender.__module__ == '__fake__' or sender._meta.app_label == 'migrations':
        return
    try:
        key = registry().model_key(sender)
        ids = scripts_for(key)
    except Exception:   # noqa: BLE001
        return
    if not ids:
        return
    from django.db import transaction
    event = 'app_create' if created else 'app_update'
    pk = instance.pk

    def go():
        from .models import Script
        from . import services as S
        user = None
        try:
            from Chatter.tracking import current_request
            req = current_request()
            user = getattr(req, 'user', None) if req is not None else None
            if user is not None and not getattr(user, 'is_authenticated', False):
                user = None
        except Exception:   # noqa: BLE001
            user = None
        user = user or S.run_as_user()
        if user is None or S.in_dry_run():
            return
        obj = sender._default_manager.filter(pk=pk).first()
        if obj is None:
            return
        _local.busy = True   # a script's own writes do not start the triggers again
        try:
            for s in Script.objects.filter(pk__in=ids, trigger=event, active=True, stopped=False):
                try:
                    S.run_code(s.code, user, script=s, trigger=event, record=None, app_record=(key, obj),
                               trigger_info={'event': event, 'key': key, 'id': pk}, record_ref=f'{key} #{pk}')
                except S.DevError:
                    pass
        finally:
            _local.busy = False
    transaction.on_commit(go)
