"""v1.62.1 – a record already used in transactions can't be deleted; it is made inactive instead.

* install(): every DRF delete (DestroyModelMixin.destroy – the ModelViewSets of every screen) first checks the record's use:
  related rows of transaction tables (payroll, payslips, leave, attendance, loans, air tickets, end of service, approvals,
  documents, assets, expenses …) that the delete would remove, block or leave without their link. Used → 409
  {"detail": "<Name> is already used in <n> transactions (e.g. payslips, leave requests). It can't be deleted – make it
  inactive instead.", "used": n, "kinds": [...], "active_field": "is_active" | null}.
* GET  /tools/api/usage/?endpoint=<list api>&ids=1,2,3  → {"1": {"used": 4, "kinds": [...], "active": true, "active_field": "is_active"}, …}
* POST /tools/api/active/ {"endpoint": "<list api>", "ids": [1, 2], "active": false} → the active flag through the screen's own API
  (PATCH, so its rights, branch scope and history apply); records without an active flag are reported, nothing else changes.
"""
import logging
import re

from django.db.models import BooleanField
from django.urls import Resolver404, resolve
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView

log = logging.getLogger(__name__)

# a related row of one of these tables is a transaction (word inside the model name)
TX_WORDS = ('emp_master', 'employee', 'assign', 'payslip', 'payroll', 'request', 'balance', 'attendance', 'punch', 'loan', 'ticket', 'settlement', 'gratuity',
            'approval', 'document', 'asset', 'expense', 'overtime', 'transaction', 'allocation', 'encash', 'salary', 'leave',
            'repayment', 'advance', 'resignation', 'endofservice', 'eos', 'shift', 'roster', 'timesheet', 'claim', 'trip',
            'journal', 'invoice', 'bill', 'receipt', 'payment', 'rejoin', 'compensat', 'enrol', 'appraisal', 'review',
            'transfer', 'handover', 'disposal', 'maintenance', 'case', 'notice', 'letter')
# … unless it is set-up data that hangs off the record (levels, escalation, rules, templates, translations, history)
CONFIG_WORDS = ('level', 'escalation', 'rule', 'template', 'setting', 'policy', 'config', 'translation', 'history', 'log',
                'audit', 'note', 'message', 'attachment', 'tag', 'value', 'field', 'layout', 'view', 'permission', 'access',
                'notification', 'type', 'category', 'workflow', 'step')
# v1.62.1: set-up tables whose names look like transactions (a new branch's own numbering and copied salary set-up blocked its delete)
SETUP_MODELS = ('documentnumbering', 'salarycomponent', 'salarystructure', 'paystructure')
ACTIVE_NAMES = ('is_active', 'active', 'br_is_active', 'is_enabled', 'enabled')
# v1.67.10 (regression): documents whose delete is a business action with its own rules, not a master that is "in use":
# a payroll run (Recalculate / Delete on the run page – refused when paid or in a locked period; what its payslips used is
# given back, 1.62.2 item 44) and a payslip. Their own payslips / lines / approvals counted as "transactions", so every
# DELETE of a calculated run answered 409 "is in use … cannot be deleted" and Recalculate never worked.
OWN_RULES = ('PayrollManagement.PayrollRun', 'PayrollManagement.Payslip')


def _is_tx(model):
    if model._meta.auto_created:   # a many-to-many link table is not a transaction
        return False
    n = model._meta.model_name.lower()
    if n in SETUP_MODELS:
        return False
    if any(w in n for w in TX_WORDS):
        return not (any(w in n for w in CONFIG_WORDS) and not any(w in n for w in ('request', 'payslip', 'balance', 'transaction')))
    return False


# v1.67.8 (fixes) item 1: masters whose active flag is kept outside their table (core tables, no new column):
# label → (field name in the API, module with inactive_ids() / used_message(obj))
VIRTUAL_ACTIVE = {'calendars.holiday_calendar': ('is_active', 'calendars.holiday_active')}


def _virtual(model):
    v = VIRTUAL_ACTIVE.get(getattr(model, '_meta', None) and model._meta.label)
    if not v:
        # v1.67.9 (screens) item 17: the other masters without an active column (leave types, loan types, salary
        # components, shifts …) – flag kept in ZeoApps.CompanyOption, see DataTools/virtual_active.py
        from DataTools import virtual_active as VA
        if getattr(model, '_meta', None) is not None and VA.handles(model):
            return 'is_active', VA.Adapter(model)
        return None, None
    import importlib
    return v[0], importlib.import_module(v[1])


def active_field(model):
    vf, _m = _virtual(model)   # v1.67.8 (fixes)
    if vf:
        return vf
    for name in ACTIVE_NAMES:
        try:
            f = model._meta.get_field(name)
        except Exception:
            continue
        if isinstance(f, BooleanField):
            return name
    for f in model._meta.get_fields():   # e.g. dept_is_active, ctgry_is_active
        if isinstance(f, BooleanField) and re.search(r'(^|_)(is_)?active$', f.name):
            return f.name
    return None


def usage(obj):
    """(n, kinds): the transaction rows that refer to obj (deleted with it, blocking it or losing their link)."""
    counts = usage_counts(obj)
    kinds = sorted(counts, key=lambda k: -counts[k])
    return sum(counts.values()), kinds


def usage_counts(obj):
    """v1.67.9 (screens): {kind: n} – where obj is used (the rows behind usage())."""
    from django.db import router
    from django.db.models.deletion import Collector, ProtectedError, RestrictedError
    counts = {}

    def add(model, n):
        if n and model is not type(obj) and _is_tx(model):
            k = _kind(model)   # v1.67.9 (screens)
            counts[k] = counts.get(k, 0) + n
    if type(obj)._meta.label in OWN_RULES:   # v1.67.10 (regression)
        return {}
    c = Collector(using=router.db_for_write(type(obj), instance=obj))
    try:
        c.collect([obj])
    except ProtectedError as e:
        for o in e.protected_objects:
            add(type(o), 1)
    except RestrictedError as e:
        for o in e.restricted_objects:
            add(type(o), 1)
    except Exception:
        log.exception('usage collect')
        return {}
    for model, objs in c.data.items():
        add(model, len(objs))
    for qs in c.fast_deletes:
        try:
            add(qs.model, qs.count())
        except Exception:
            pass
    for key, objs in getattr(c, 'field_updates', {}).items():   # on_delete=SET_NULL / SET_DEFAULT: rows that would lose the link
        for o in (objs if isinstance(objs, (list, tuple, set)) else [objs]):
            try:
                if hasattr(o, 'model') and hasattr(o, 'count'):
                    add(o.model, o.count())
                else:
                    add(type(o), 1)
            except Exception:
                pass
    return counts


def _kind(model):
    k = re.sub(r'\s+', ' ', str(model._meta.verbose_name_plural).replace('_', ' ')).strip().lower()
    k = {'emp masters': 'employees', 'emp leave balances': 'leave balances'}.get(k, k)
    return _plural(k)


def _plural(k):
    """v1.67.9 (screens): readable English kind names in the "in use" message (Django's automatic plurals: "punchs",
    "asset losss", "emp documentss", "employee identitys", "lv approval notifys")."""
    k = re.sub(r'\bemp\b', 'employee', k)
    k = re.sub(r'\blv\b', 'leave', k)
    k = k.replace('latein earlyout', 'late in / early out').replace('latein early', 'late in / early out').replace('airticket', 'air ticket')
    k = re.sub(r'notifys$', 'notifications', k)
    k = re.sub(r'(ss)s$', r'\1es', k)              # losss → losses
    k = re.sub(r'(s)s$', r'\1', k) if k.endswith('tss') else k   # documentss → documents
    k = re.sub(r'(ch|sh)s$', r'\1es', k)           # punchs → punches, batchs → batches
    k = re.sub(r'([^aeiou])ys$', r'\1ies', k)       # summarys → summaries, identitys → identities
    return k


def usage_many(model, objs):
    """v1.62.1 ui – {pk: (n, kinds)} for a whole list: the direct references from transaction tables, counted per relation
    for all rows in one grouped query; rows with no direct reference fall back to usage() (cascades further down)."""
    from django.db.models import Count
    pks = [o.pk for o in objs]
    counts = {pk: {} for pk in pks}
    for rel in model._meta.related_objects:
        rm = rel.related_model
        if rm is model or not _is_tx(rm) or not getattr(rel, 'field', None):
            continue
        name = rel.field.name
        try:
            for pk, n in rm._base_manager.filter(**{f'{name}__in': pks}).values_list(name).annotate(n=Count('pk')).values_list(name, 'n'):
                if pk in counts and n:
                    counts[pk][_kind(rm)] = counts[pk].get(_kind(rm), 0) + n
        except Exception:
            log.exception('usage_many %s.%s', rm._meta.label, name)
    out = {}
    for o in objs:
        c = counts[o.pk] or usage_counts(o)
        _COUNTS[(type(o), o.pk)] = c   # v1.67.9 (screens): used_message names the places with their counts
        out[o.pk] = (sum(c.values()), sorted(c, key=lambda k: -c[k]))
    return out


_COUNTS = {}


def used_message(obj, n, kinds):
    from Translations.i18n import tr
    _vf, vm = _virtual(type(obj))   # v1.67.8 (fixes) item 1: a message that says what the record is used by
    if vm is not None and hasattr(vm, 'used_message'):
        try:
            m = vm.used_message(obj)
            if m:
                return m
        except Exception:   # noqa: BLE001
            log.exception('used_message %s', type(obj))
    # v1.67.9 (screens) item 17: the 1.67.8 holiday-calendar wording for every record – what it is, where it is used (with
    # the counts), that it cannot be deleted, and what "Make inactive" does
    counts = _COUNTS.pop((type(obj), obj.pk), None) or usage_counts(obj)
    order = sorted(counts, key=lambda k: -counts[k]) or list(kinds)
    where = '، ' if _lang_ar() else ', '
    what = where.join(tr('{0} ({1})', _kind_text(k), counts.get(k, 1)) for k in order[:4])
    if len(order) > 4:
        what += where + tr('{0} more', len(order) - 4)
    nm = _name(obj)
    if active_field(type(obj)):
        return tr('"{0}" is in use – it is used in {1}. It cannot be deleted. Make it inactive instead: it is then no longer offered '
                  'for new records, and the records that already use it keep working.', nm, what)
    return tr('"{0}" is in use – it is used in {1}. It cannot be deleted.', nm, what)


def _kind_text(k):
    """The kind in the reader's language; a kind without a term reads "records" (no English inside an Arabic sentence)."""
    from Translations.i18n import tr
    t = tr(k)
    if t == k and _lang_ar():
        return tr('other records')
    return t


def _lang_ar():
    try:
        from Translations.i18n import current_lang
        return (current_lang() or '').startswith('ar')
    except Exception:   # noqa: BLE001
        return False


def _name(obj):
    from DataTools import virtual_active as VA
    if VA.handles(type(obj)):
        return VA.name_of(obj)
    return str(obj)[:80]


class UsedError(Exception):
    pass


def install():
    from rest_framework import mixins, generics
    from rest_framework.exceptions import APIException
    if getattr(mixins.DestroyModelMixin, '_zeo_usage', False):
        return

    class Used(APIException):
        status_code = 409
        default_code = 'used'

    # every DELETE of one record through a DRF view (also views with their own destroy / delete): checked before the view runs
    orig_initial = generics.GenericAPIView.initial

    def initial(self, request, *args, **kwargs):
        orig_initial(self, request, *args, **kwargs)
        if request.method != 'DELETE' or not (kwargs.get(getattr(self, 'lookup_url_kwarg', None) or getattr(self, 'lookup_field', 'pk')) or kwargs.get('pk')):
            return
        try:
            obj = self.get_object()
        except Exception:
            return
        try:
            n, kinds = usage(obj)
        except Exception:
            n, kinds = 0, []
        if n:
            e = Used()
            e.detail = {'detail': used_message(obj, n, kinds), 'used': n, 'kinds': kinds, 'active_field': active_field(type(obj)), 'code': 'used'}
            raise e
    generics.GenericAPIView.initial = initial
    orig = mixins.DestroyModelMixin.destroy

    def destroy(self, request, *args, **kwargs):
        try:
            obj = self.get_object()
        except Exception:
            return orig(self, request, *args, **kwargs)
        try:
            n, kinds = usage(obj)
        except Exception:
            n, kinds = 0, []
        if n:
            return Response({'detail': used_message(obj, n, kinds), 'used': n, 'kinds': kinds,
                             'active_field': active_field(type(obj)), 'code': 'used'}, status=status.HTTP_409_CONFLICT)
        resp = orig(self, request, *args, **kwargs)
        if getattr(resp, 'status_code', 500) < 400:   # v1.67.9 (screens): a deleted record leaves the inactive store
            from DataTools import virtual_active as VA
            if VA.handles(type(obj)):
                VA.forget(type(obj), obj.pk)
        return resp
    mixins.DestroyModelMixin.destroy = destroy
    mixins.DestroyModelMixin._zeo_usage = True


def _view(endpoint):
    path = '/' + str(endpoint or '').split('?')[0].strip('/') + '/'
    try:
        m = resolve(path)
    except Resolver404:
        return None, None, path
    cls = getattr(m.func, 'cls', None)
    return m.func, cls, path


def _model_qs(request, cls, path):
    """The screen's own queryset (rights and branch scope of its list)."""
    v = cls()
    v.request = request
    v.format_kwarg = None
    v.action = 'list'
    v.kwargs = {}
    v.args = ()
    from rest_framework.request import Request
    v.request = request if isinstance(request, Request) else Request(request)
    return v.get_queryset()


def _kit(path, request):
    """v1.67.9 (screens): the app-kit register behind a list API (/zeo-crm/api/r/lead/ …) – (register, app) or (None, None)."""
    try:
        m = resolve(path)
    except Resolver404:
        return None, None
    app = (getattr(m.func, 'view_initkwargs', None) or {}).get('app')
    kind = m.kwargs.get('kind')
    if app is None or not kind or not hasattr(app, 'regs'):
        return None, None
    r = app.regs.get(kind)
    if r is None or not r.can_open(request):
        return None, None
    return r, app


class UsageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        func, cls, path = _view(request.query_params.get('endpoint'))
        ids = [x for x in re.findall(r'\d+', request.query_params.get('ids', ''))][:1000]
        if cls is None or not ids:
            return Response({})
        reg, _app = _kit(path, request)   # v1.67.9 (screens) item 17: the app-kit registers (CRM, Property, masters …) too
        try:
            qs = reg.scope(request) if reg is not None else _model_qs(request, cls, path)
        except Exception:
            return Response({})
        af = active_field(qs.model)
        out = {}
        _vf, vm = _virtual(qs.model)   # v1.67.8 (fixes) item 1: the flag kept outside the table
        if vm is not None:
            off = vm.inactive_ids()
            if request.query_params.get('active_only') == '1':
                for pk in qs.filter(pk__in=ids).values_list('pk', flat=True):
                    out[str(pk)] = {'active_field': af, 'active': pk not in off}
                return Response(out)
            objs = list(qs.filter(pk__in=ids))
            many = usage_many(qs.model, objs)
            for o in objs:
                n, kinds = many[o.pk]
                out[str(o.pk)] = {'used': n, 'kinds': kinds[:3], 'active_field': af, 'active': o.pk not in off,
                                  'message': used_message(o, n, kinds) if n else ''}
            return Response(out)
        if request.query_params.get('active_only') == '1':   # the Active / Inactive filter: flags only (fast)
            if af:
                for pk, v in qs.filter(pk__in=ids).values_list('pk', af):
                    out[str(pk)] = {'active_field': af, 'active': bool(v)}
            return Response(out)
        objs = list(qs.filter(pk__in=ids))
        many = usage_many(qs.model, objs)   # v1.62.1 ui – grouped counts (a 46-row list took 8.7 s with one Collector per row)
        for o in objs:
            n, kinds = many[o.pk]
            out[str(o.pk)] = {'used': n, 'kinds': kinds[:3], 'active_field': af, 'active': bool(getattr(o, af)) if af else None,
                              'message': used_message(o, n, kinds) if n else ''}
        return Response(out)


class ActiveView(APIView):
    """Make records inactive / active again through the screen's own API (PATCH {active_field: value})."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from Translations.i18n import tr
        d = request.data if isinstance(request.data, dict) else {}
        func, cls, path = _view(d.get('endpoint'))
        if cls is None:
            return Response({'detail': tr('This list cannot be changed here.')}, status=status.HTTP_400_BAD_REQUEST)
        reg, app = _kit(path, request)
        if reg is not None:   # v1.67.9 (screens) item 17: an app-kit register – its own edit right, the flag set on the record
            return self._kit(request, reg, app, d)
        from AccessControl.access import view_model
        model = view_model(cls())
        af = active_field(model) if model is not None else None
        if not af:
            return Response({'detail': tr('These records have no active / inactive setting.'), 'no_active_field': True}, status=status.HTTP_400_BAD_REQUEST)
        value = bool(d.get('active'))
        factory = APIRequestFactory()
        schema = request.query_params.get('schema', '')
        done, failed = [], []
        _vf, vm = _virtual(model)
        if vm is not None and VIRTUAL_ACTIVE.get(model._meta.label) is None:
            # v1.67.9 (screens) item 17: a flag kept outside the table – the screen's own checks (change right, the record in
            # the user's scope), then the store; the record itself is not saved again
            for pk in [int(x) for x in d.get('ids') or [] if str(x).isdigit()][:300]:
                why = _may_change(request, cls, path, pk)
                if why:
                    failed.append({'id': pk, 'reason': why}); continue
                try:
                    vm.set_active(pk, value); done.append(pk)
                except Exception as e:   # noqa: BLE001
                    failed.append({'id': pk, 'reason': str(e)[:160]})
            return Response({'done': done, 'failed': failed, 'active': value, 'field': af})
        for pk in [int(x) for x in d.get('ids') or [] if str(x).isdigit()][:300]:
            sub = factory.patch(f'{path}{pk}/?schema={schema}', {af: value}, format='json')
            force_authenticate(sub, user=request.user)
            sub.tenant = getattr(request, 'tenant', None)
            for h in ('HTTP_X_LANGUAGE',):
                if h in request.META:
                    sub.META[h] = request.META[h]
            try:
                dm = resolve(f'{path}{pk}/')
                r = dm.func(sub, *dm.args, **dm.kwargs)
            except Exception as e:
                failed.append({'id': pk, 'reason': str(e)[:160]}); continue
            if r.status_code < 400:
                done.append(pk)
            else:
                dd = getattr(r, 'data', None)
                failed.append({'id': pk, 'reason': (dd.get('detail') if isinstance(dd, dict) else str(dd))[:200] if dd else f'Error {r.status_code}'})
        return Response({'done': done, 'failed': failed, 'active': value, 'field': af})

    def _kit(self, request, reg, app, d):
        from Translations.i18n import tr
        af = active_field(reg.model)
        if not af:
            return Response({'detail': tr('These records have no active / inactive setting.'), 'no_active_field': True}, status=status.HTTP_400_BAD_REQUEST)
        value = bool(d.get('active'))
        done, failed = [], []
        for pk in [int(x) for x in d.get('ids') or [] if str(x).isdigit()][:300]:
            obj = reg.scope(request).filter(pk=pk).first()
            if obj is None:
                failed.append({'id': pk, 'reason': tr('Not found or not yours.')}); continue
            try:
                allowed = reg.can_edit(obj, request)
            except Exception:   # noqa: BLE001
                allowed = False
            if not allowed:
                failed.append({'id': pk, 'reason': tr('This record cannot be changed.')}); continue
            reg.model._default_manager.filter(pk=pk).update(**{af: value})
            try:
                from zeo.appkit.engine import user_name
                app.event(reg.kind, pk, f'{"Made active" if value else "Made inactive"} by {user_name(request.user)}', request.user)
            except Exception:   # noqa: BLE001
                pass
            done.append(pk)
        return Response({'done': done, 'failed': failed, 'active': value, 'field': af})


def _may_change(request, cls, path, pk):
    """v1.67.9 (screens): '' when the user may change record pk through the screen's API (rights + scope), else why not."""
    from Translations.i18n import tr
    from rest_framework.request import Request
    try:
        dm = resolve(f'{path}{pk}/')
    except Resolver404:
        return tr('This list cannot be changed here.')
    v = cls()
    v.request = request if isinstance(request, Request) else Request(request)
    v.format_kwarg = None
    v.action = 'partial_update'
    v.args, v.kwargs = dm.args, dm.kwargs
    req = v.request
    orig = req._request.method
    try:
        req._request.method = 'PATCH'   # the permission classes look at the method (change right)
        v.check_permissions(req)
        v.get_object()                  # the record must be in the user's scope (object permissions too)
    except Exception as e:   # noqa: BLE001
        d = getattr(e, 'detail', None)
        return str(d if d else e)[:200] or tr('Not allowed.')
    finally:
        req._request.method = orig
    return ''
