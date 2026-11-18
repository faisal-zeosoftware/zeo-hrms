"""v1.73.5 – "+ Create new …" in every drop-down that lists a master.

The screens of every module fill their drop-downs from the list API of the master (departments, leave types, customers,
projects …). The frontend remembers which API each list came from and asks here, with that API's path, whether the user may
add a record there and with which fields:

    GET zeo-masters/api/quick/meta/?src=/organisation/api/Department/

The answer describes a small form: the fields the master's own API requires (with their pick lists), the address to POST to
(the master's own API – its validation, rights, numbering and signals stay exactly as on the master screen) and how to read
the new record back in the shape of the drop-down's list. Nothing is created here.

Two kinds of source are understood:
  * a DRF list API (ViewSet / ListCreateAPIView) – the form comes from its serializer; the right is the API's own permission
    check, asked for a POST by the same user;
  * the options list of a kit app (zeo-crm/api/options/accounts/, …) – the form comes from the register of that model and the
    record is created through the register (api/r/<kind>/), as on the register screen.

Not offered: people and logins (employees, users), companies and branches (licence, numbering and access depend on them),
filters and any API whose required fields are more than a quick form should ask (QUICK_MAX_FIELDS) – those are opened on
their own screen.
"""
import copy
import logging
import re

from django.urls import Resolver404, resolve
from rest_framework import serializers as S
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

log = logging.getLogger(__name__)

QUICK_MAX_FIELDS = 8
NOT_QUICK = {'emp_master', 'company', 'cmpny_mstr', 'CustomUser', 'User', 'Company', 'Client', 'Domain', 'brnch_mstr', 'cmpny_mastr', 'UserTenantPermissions',
             'Group', 'Permission', 'Tenant'}
AUTO_USER = {'created_by', 'updated_by', 'owner_user', 'created_user'}
TEXT_LIKE = (S.CharField, S.EmailField, S.URLField, S.SlugField, S.RegexField)
CHOICES_MAX = 300
SKIP_OPTIONAL = {'id', 'created_at', 'updated_at', 'is_active', 'status', 'created_by', 'updated_by'}


MASTER_WORDS = {'type', 'types', 'category', 'categories', 'catogary', 'master', 'mstr', 'group', 'policy', 'calendar', 'component', 'structure',
                'template', 'grade', 'level', 'stage', 'reason', 'bank', 'currency', 'country', 'state', 'nationality', 'religion', 'site',
                'area', 'team', 'shift', 'pattern', 'skill', 'course', 'location', 'unit', 'uom', 'tax', 'code', 'term', 'terms', 'zone',
                'division', 'section', 'room', 'warehouse', 'store', 'vendor', 'supplier', 'customer', 'account', 'partner', 'contact',
                'project', 'product', 'item', 'asset', 'tag', 'source', 'channel', 'industry', 'territory', 'competitor', 'campaign',
                'pricebook', 'language', 'center', 'centre', 'provider', 'trainer', 'venue', 'module', 'pool', 'folder', 'year', 'kpi',
                'cycle', 'contractor', 'equipment', 'brand', 'model', 'make'}
NOT_MASTER = re.compile(r'(request|run|log|approval|application|transaction|punch|payslip|notification|notify|history|audit|resignation|claim|'
                        r'encash|settlement|allocation|attendance(?!policy|validation|calendar)|record|entry|event|job|message|comment|file|upload|'
                        r'session|token|otp|repayment|instalment|installment|accrual|ledger|balance|movement|snapshot|import|export|draft|'
                        r'report|candidate|interview|offer|requisition|sheet|improvement|family|qualification|yearly|announcement|^action$|endofservice|'
                        r'advance|incident|permit|assessment|ticket|^trip$|need|^task$|^document$|contract|^workflow$|'
                        r'lease|^sale|inspection|^bill|receipt|cheque|reservation|moveout|order|quot|^count|^assetcount|visit|booking|'
                        r'invoice|payment$|statement|refund|commission$|reading|stockmove|^case$|^drill|^audit$|hygiene|waste|'
                        r'assignment|handover|interview|^plan$|opportunit|^lead$|^enquir|^activity$|issue$|stockcount|deposit|send$|'
                        r'conversation|crmdocument|^hsedocument|budget|^checklistitem$|^pricebookitem$|^partnerbank$)', re.I)
_FK_TARGETS = None


def _words(name):
    return [w.lower() for w in re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+', name.replace('_', ' '))]


def is_master(model):
    """a record other records point at (department, leave type, customer …) and not a transaction (request, run, log …) or a
    record of one employee (family, documents, salary of an employee …)"""
    global _FK_TARGETS
    if _FK_TARGETS is None:
        from django.apps import apps
        t = set()
        for m in apps.get_models():
            for f in m._meta.get_fields():
                rm = getattr(f, 'related_model', None)
                if rm is not None and (getattr(f, 'many_to_one', False) or getattr(f, 'many_to_many', False)) and not f.auto_created:
                    t.add(rm)
        _FK_TARGETS = t
    n = model.__name__
    w = _words(n)
    if w and w[0] in ('emp', 'employee') and len(w) > 1:
        return False
    if n in ('ChecklistItem', 'PriceBookItem', 'PartnerBank', 'ServiceChargeBudget', 'DepositSlip', 'CampaignSend'):
        return False   # lines of another record
    if w and w[-1] in MASTER_WORDS:   # leave type, request type, attendance policy, salary component …
        return True
    if NOT_MASTER.search(n):
        return False
    return model in _FK_TARGETS


def _no(reason):
    return Response({'ok': False, 'reason': reason})


def _path(src):
    src = str(src or '').strip()
    if '://' in src:
        src = '/' + src.split('://', 1)[1].split('/', 1)[-1]
    src = src.split('?', 1)[0]
    if not src.startswith('/'):
        src = '/' + src
    if not src.endswith('/'):
        src += '/'
    return src


def _label(obj):
    for k in ('name', 'title', 'type_name', 'dept_name', 'desgntn_job_title', 'ctgry_title', 'label', 'code'):
        v = getattr(obj, k, None)
        if v:
            return str(v)
    return str(obj)


def _choices_of_qs(qs):
    try:
        return [{'value': o.pk, 'label': _label(o)} for o in qs.all()[:CHOICES_MAX]]
    except Exception:   # noqa: BLE001
        return []


TITLES = {'cntry_mstr': 'Country', 'crncy_mstr': 'Currency', 'state_mstr': 'State', 'LvRejectionReason': 'Leave rejection reason',
          'LvEmailTemplate': 'Leave e-mail template', 'LatinEarlyoutEmailTemplate': 'Late in / early out e-mail template', 'KPI': 'KPI',
          'dept_master': 'Department', 'desgntn_master': 'Designation', 'ctgry_master': 'Category', 'brnch_mstr': 'Branch',
          'LeaveType': 'Leave type', 'leave_type': 'Leave type', 'Document_type': 'Document type', 'DocRequestType': 'Document request type',
          'RequestType': 'Request type', 'LoanType': 'Loan type', 'AssetType': 'Asset type', 'Asset_type': 'Asset type'}


def _title_of(model):
    """the master's name in words: Department, not Dept master"""
    n = model.__name__
    if n in TITLES:
        return TITLES[n]
    v = str(model._meta.verbose_name or n)
    v = re.sub(r'([a-z])([A-Z])', r'\1 \2', v).replace('_', ' ').strip()
    v = re.sub(r'\s+(master|mstr|mastr)$', '', v, flags=re.I).strip()
    return v[:1].upper() + v[1:].lower() if v else n


# ------------------------------------------------------------------ DRF list APIs
def _drf_meta(request, match, path):
    func = match.func
    cls = getattr(func, 'cls', None) or getattr(func, 'view_class', None)
    if cls is None:
        return None
    actions = getattr(func, 'actions', None)
    if actions is not None and actions.get('post') != 'create':
        return _no('This list cannot be added to.')
    if actions is None and not hasattr(cls, 'post'):
        return _no('This list cannot be added to.')
    # the same user asking for a POST on that API
    dj = copy.copy(request._request)
    dj.method = 'POST'
    from rest_framework.request import Request
    req = Request(dj, authenticators=request.authenticators, parsers=request.parsers)
    req._user, req._auth = request.user, request.auth
    view = cls(**(getattr(func, 'initkwargs', None) or {}))
    view.request, view.args, view.kwargs, view.format_kwarg = req, match.args, match.kwargs, None
    view.action = 'create'
    view.action_map = {'post': 'create'}
    view.headers = {}
    if actions is None and not hasattr(cls, 'get_serializer'):   # a hand-written API (post with its own reading of the fields)
        return _model_meta(request, _model_of_view(cls, match), path, view, req)
    try:
        for p in view.get_permissions():
            if not p.has_permission(req, view):
                return _no(getattr(p, 'message', '') or 'You may not add records here.')
    except Exception as e:   # noqa: BLE001
        return _no(str(e)[:200] or 'You may not add records here.')
    try:
        ser = view.get_serializer()
    except Exception:   # noqa: BLE001
        log.info('quick create: no serializer for %s', path, exc_info=True)
        return _no('This list cannot be added to here.')
    model = getattr(getattr(ser, 'Meta', None), 'model', None)
    if model is None:
        return _no('This list cannot be added to here.')
    if model.__name__ in NOT_QUICK or not is_master(model):
        return _no(f'{_title_of(model)} records are added on their own screen.')
    fields, auto = [], {}
    from django.db import models as M
    for name, f in ser.fields.items():
        if f.read_only:
            continue
        mf = None
        try:
            mf = model._meta.get_field(getattr(f, 'source', None) or name)
        except Exception:   # noqa: BLE001
            mf = None
        if name in AUTO_USER:
            auto[name] = 'user'
            continue
        required = bool(f.required)
        is_code = name in ('code', 'short_code') or name.endswith('_code')
        if not required and not is_code and name not in ('description', 'remarks', 'notes'):
            continue
        if name in SKIP_OPTIONAL and not required:
            continue
        d = {'name': name, 'label': str(f.label or name.replace('_', ' ')).strip().capitalize(), 'required': required,
             'help': str(f.help_text or '')}
        if isinstance(f, S.ManyRelatedField):
            d['type'] = 'm2m'
            d['choices'] = _choices_of_qs(f.child_relation.queryset) if getattr(f.child_relation, 'queryset', None) is not None else []
        elif isinstance(f, S.RelatedField):
            if getattr(f, 'queryset', None) is None:
                continue
            rm = f.queryset.model
            if rm.__name__ in ('CustomUser', 'User') and not required:
                continue
            d['type'] = 'fk'
            d['choices'] = _choices_of_qs(f.queryset)
            if rm.__name__ in ('brnch_mstr',):
                d['branch'] = True
        elif isinstance(f, S.BooleanField):
            d['type'] = 'bool'
        elif isinstance(f, S.ChoiceField):
            d['type'] = 'choice'
            d['choices'] = [{'value': k, 'label': str(v)} for k, v in f.choices.items() if k not in ('', None)]
        elif isinstance(f, (S.IntegerField,)):
            d['type'] = 'int'
        elif isinstance(f, (S.DecimalField, S.FloatField)):
            d['type'] = 'decimal'
        elif isinstance(f, S.DateTimeField):
            d['type'] = 'datetime'
        elif isinstance(f, S.DateField):
            d['type'] = 'date'
        elif isinstance(f, S.TimeField):
            d['type'] = 'time'
        elif isinstance(f, (S.FileField, S.ImageField, S.JSONField, S.ListField, S.DictField)) or isinstance(f, S.BaseSerializer):
            if required:
                return _no('This list is added to on its own screen (it needs a file or a table).')
            continue
        elif isinstance(f, TEXT_LIKE):
            d['type'] = 'textarea' if (mf is not None and isinstance(mf, M.TextField)) else ('email' if isinstance(f, S.EmailField) else 'text')
            if getattr(f, 'max_length', None):
                d['max'] = f.max_length
        else:
            if required:
                d['type'] = 'text'
            else:
                continue
        dflt = getattr(f, 'default', S.empty)
        if dflt is not S.empty and not callable(dflt):
            d['default'] = dflt
        elif mf is not None and getattr(mf, 'default', M.NOT_PROVIDED) is not M.NOT_PROVIDED and not callable(mf.default):
            d['default'] = mf.default
        fields.append(d)
    if not any(f['required'] for f in fields):
        # nothing required by the API (Nationality: N_name may be empty) – the name of the record is asked for, as on its screen
        for name, f in ser.fields.items():
            if f.read_only or name in AUTO_USER or not isinstance(f, TEXT_LIKE) or name in [x['name'] for x in fields]:
                continue
            fields.insert(0, {'name': name, 'label': str(f.label or name.replace('_', ' ')).strip().capitalize(), 'required': True,
                              'help': str(f.help_text or ''), 'type': 'text', **({'max': f.max_length} if getattr(f, 'max_length', None) else {})})
            break
        for x in fields:
            if x['name'].lower() in ('n_name', 'religion') or x['name'].lower().endswith('_name'):
                x['label'] = _title_of(model)
    if not fields:
        return _no('This list cannot be added to here.')
    if sum(1 for f in fields if f['required']) > QUICK_MAX_FIELDS:
        return _no(f'A new {_title_of(model).lower()} needs more than a quick form – add it on its own screen.')
    return Response({'ok': True, 'kind': 'api', 'title': _title_of(model), 'model': model.__name__, 'post': path, 'fields': fields,
                     'auto': auto, 'read': path + '{id}/'})


def _core_meta(request, model, read):
    """a kit app's picker of an HRMS master (department, designation, project …): created through the master's own API, read
    back from the picker's list"""
    path = next((p for k, p in aliases().items() if k == _norm(model.__name__) or k == _norm(_title_of(model))), None)
    if not path:
        return _no('This list cannot be added to here.')
    out = _meta_for(request, path)
    if out.data.get('ok'):
        out.data['kind'] = 'kit'
        out.data['read'] = read
    return out


# ------------------------------------------------------------------ hand-written list APIs (Projects, …): the form from the model
def _model_of_view(cls, match):
    m = getattr(cls, 'zeo_model', None) or getattr(cls, 'model', None)
    if m is not None:
        return m
    import sys
    mod = sys.modules.get(cls.__module__)
    kind = match.kwargs.get('kind')
    reg = getattr(mod, 'MASTERS', None) or getattr(mod, 'MASTER', None)
    if kind and isinstance(reg, dict) and kind in reg:
        v = reg[kind]
        return v[0] if isinstance(v, (tuple, list)) else v
    return None


def _model_meta(request, model, path, view, req):
    from django.db import models as M
    if model is None:
        return _no('This list cannot be added to here.')
    if model.__name__ in NOT_QUICK or not is_master(model):
        return _no(f'{_title_of(model)} records are added on their own screen.')
    try:
        for p in view.get_permissions():
            if not p.has_permission(req, view):
                return _no(getattr(p, 'message', '') or 'You may not add records here.')
    except Exception:   # noqa: BLE001
        pass
    fields = []
    for f in model._meta.get_fields():
        if f.auto_created or not getattr(f, 'concrete', False) or not getattr(f, 'editable', True) or isinstance(f, M.AutoField):
            continue
        name = f.name
        rm = getattr(f, 'related_model', None)
        required = not f.blank and f.default is M.NOT_PROVIDED and not getattr(f, 'auto_now', False) and not getattr(f, 'auto_now_add', False)
        if name in AUTO_USER or (rm is not None and rm.__name__ in ('CustomUser', 'User')):
            continue
        is_code = name in ('code', 'short_code') or name.endswith('_code')
        if not required and not is_code and name not in ('email', 'phone', 'description'):
            continue
        d = {'name': name, 'label': str(f.verbose_name or name).replace('_', ' ').strip().capitalize(), 'required': required,
             'help': str(f.help_text or '')}
        if isinstance(f, M.ManyToManyField):
            continue
        if rm is not None:
            if rm.__name__ in ('brnch_mstr',):
                d['branch'] = True
            d['type'] = 'fk'
            d['choices'] = _choices_of_qs(rm.objects)
        elif f.choices:
            d['type'] = 'choice'
            d['choices'] = [{'value': k, 'label': str(v)} for k, v in f.choices if k not in ('', None)]
        elif isinstance(f, M.BooleanField):
            d['type'] = 'bool'
        elif isinstance(f, (M.IntegerField,)):
            d['type'] = 'int'
        elif isinstance(f, (M.DecimalField, M.FloatField)):
            d['type'] = 'decimal'
        elif isinstance(f, M.DateTimeField):
            d['type'] = 'datetime'
        elif isinstance(f, M.DateField):
            d['type'] = 'date'
        elif isinstance(f, M.TimeField):
            d['type'] = 'time'
        elif isinstance(f, (M.FileField, M.JSONField)):
            if required:
                return _no('This list is added to on its own screen (it needs a file or a table).')
            continue
        elif isinstance(f, M.TextField):
            d['type'] = 'textarea'
        elif isinstance(f, M.EmailField):
            d['type'] = 'email'
        else:
            d['type'] = 'text'
        if getattr(f, 'max_length', None) and d['type'] in ('text', 'email'):
            d['max'] = f.max_length
        if f.default is not M.NOT_PROVIDED and not callable(f.default) and f.default not in (None, ''):
            d['default'] = f.default
        fields.append(d)
    if sum(1 for f in fields if f['required']) > QUICK_MAX_FIELDS:
        return _no(f'A new {_title_of(model).lower()} needs more than a quick form – add it on its own screen.')
    return Response({'ok': True, 'kind': 'api', 'title': _title_of(model), 'model': model.__name__, 'post': path, 'fields': fields,
                     'auto': {}, 'read': ''})


# ------------------------------------------------------------------ kit apps (CRM, Property, Maintenance, Projects, Assets, People, HSE …)
def _reg_form(request, reg, fmeta_fn, post, options_base, read):
    if not is_master(reg.model):   # a lease, a work order, an inspection … is made on its own screen
        return _no(f'{reg.plural} are added on their own screen.')
    if not (reg.can_create(request) and getattr(reg, 'create', 'manage') != 'none'):
        return _no(f'You cannot add {reg.plural.lower()}.')
    fields = []
    for n in reg.form:
        try:
            m = fmeta_fn(reg, n)
        except Exception:   # noqa: BLE001
            continue
        if m.get('readonly') or m['type'] in ('json',):
            if m.get('required') and m['type'] == 'json':
                return _no(f'A new {reg.label.lower()} is added on its own screen.')
            continue
        if not m.get('required') and n not in ('branch', 'code', 'phone', 'email', 'mobile', 'description'):
            continue
        d = {'name': n, 'label': m['label'], 'required': bool(m.get('required')), 'help': m.get('help', ''), 'type': m['type']}
        if d['type'] in ('bool3',):
            d['type'] = 'bool'
        if d['type'] not in ('text', 'textarea', 'int', 'decimal', 'date', 'datetime', 'time', 'email', 'bool', 'choice', 'fk', 'm2m'):
            if d['required']:
                d['type'] = 'text'
            else:
                continue
        if m['type'] in ('fk', 'm2m'):
            opt = m.get('options') or ''
            if not opt:
                if d['required']:
                    return _no(f'A new {reg.label.lower()} is added on its own screen.')
                continue
            d['options'] = opt
            if opt == 'branches':
                d['branch'] = True
        if m['type'] == 'choice':
            d['choices'] = m.get('choices', [])
        if 'default' in m:
            d['default'] = m['default']
        if m.get('max'):
            d['max'] = m['max']
        fields.append(d)
    if sum(1 for f in fields if f['required']) > QUICK_MAX_FIELDS:
        return _no(f'A new {reg.label.lower()} needs more than a quick form – add it on its own screen.')
    return Response({'ok': True, 'kind': 'kit', 'title': reg.label, 'model': reg.model.__name__, 'post': post, 'fields': fields,
                     'auto': {}, 'options_base': options_base, 'read': read})


def _masters_reg(model):
    """a record kind kept by another kit app (business partners in HSE, …)"""
    from zeo.appkit.engine import REGISTRY, fmeta
    for a in REGISTRY.values():
        for r in a.regs.values():
            if r.model is model:
                return a, r, fmeta
    return None, None, None


def _kit_meta(request, match, path):
    app = (getattr(match.func, 'view_initkwargs', None) or {}).get('app')
    name = match.kwargs.get('name')
    if app is None or not name:
        return None
    if name in ('employees', 'users', 'branches') or name.startswith('link:'):
        return _no('These records are added on their own screen.')
    if name not in app.options:   # a list another kit app keeps (payment terms on the partner form: CRM)
        from zeo.appkit.engine import REGISTRY
        other = next((a for a in REGISTRY.values() if name in a.options), None)
        if other is None:
            return _no('This list cannot be added to here.')
        app = other
        path = f'/{_slug_of(app)}/api/options/{name}/'   # read back (and its field lists) from the app that keeps it
    try:
        model = app.options[name][0]().model
    except Exception:   # noqa: BLE001
        return _no('This list cannot be added to here.')
    from zeo.appkit.engine import fmeta
    reg = next((r for r in app.regs.values() if r.model is model), None)
    owner = app
    if reg is None:
        owner, reg, _f = _masters_reg(model)
    base = path.split('/api/options/', 1)[0]
    if reg is None:
        return _core_meta(request, model, f'{base}/api/options/{name}/?ids={{id}}')
    post_base = base if owner is app else '/' + _slug_of(owner)
    return _reg_form(request, reg, fmeta, f'{post_base}/api/r/{reg.kind}/', f'{post_base}/api/options/', f'{base}/api/options/{name}/?ids={{id}}')


def _slug_of(app):
    """URL prefix of a kit app (zeo-masters, zeo-crm …) from the project's URL list"""
    from django.urls import get_resolver
    for p in get_resolver().url_patterns:
        try:
            for sub in getattr(p, 'url_patterns', []):
                cb = getattr(sub, 'callback', None)
                if cb is not None and (getattr(cb, 'view_initkwargs', None) or {}).get('app') is app:
                    return str(p.pattern).strip('^/').rstrip('/')
        except Exception:   # noqa: BLE001
            continue
    return app.slug


def _hse_meta(request, match, path):
    name = match.kwargs.get('name')
    from ZeoHSE import registers as H
    if name in ('employees', 'files') or name in getattr(H.L, 'OPTION_LINK', {}) or name not in H.OPTIONS:
        return _no('These records are added on their own screen.')
    try:
        model = H.OPTIONS[name][0]().model
    except Exception:   # noqa: BLE001
        return _no('This list cannot be added to here.')
    base = path.split('/api/options/', 1)[0]
    reg = next((r for r in H.REGS.values() if r.model is model), None)
    if reg is not None:
        return _reg_form(request, reg, H.fmeta, f'{base}/api/r/{reg.kind}/', f'{base}/api/options/', f'{base}/api/options/{name}/?ids={{id}}')
    owner, reg, fm = _masters_reg(model)
    if reg is None:   # an HSE set-up list (sites, contractors, checklists …): its own masters API
        try:
            from ZeoHSE import views as HV
            kind = next((k for k, v in HV.MASTER.items() if v[0] is model), None)
        except Exception:   # noqa: BLE001
            kind = None
        if kind:
            return _meta_for(request, f'{base}/api/masters/{kind}/')
        return _core_meta(request, model, f'{base}/api/options/{name}/?ids={{id}}')
    return _reg_form(request, reg, fm, f'/{_slug_of(owner)}/api/r/{reg.kind}/', f'/{_slug_of(owner)}/api/options/', f'{base}/api/options/{name}/?ids={{id}}')


# ------------------------------------------------------------------ names of the lists (bundles of lists, lists the screens re-shaped)
_ALIASES = None
_ALL = {}   # name → every list API of that name (Site: HSE sites, Projects customer sites)


def _norm(x):
    x = re.sub(r'[^a-z]', '', str(x or '').lower())
    for suf in ('master', 'mstr', 'mastr'):
        if x.endswith(suf) and len(x) > len(suf) + 2:
            x = x[:-len(suf)]
    if x.endswith('ies'):
        x = x[:-3] + 'y'
    elif x.endswith('ses') or x.endswith('xes'):
        x = x[:-2]
    elif x.endswith('s') and not x.endswith('ss'):
        x = x[:-1]
    return x


def aliases():
    """{normalised name: list API} of every master of the HRMS core and the other DRF apps (department → organisation/api/Department/)"""
    global _ALIASES
    if _ALIASES is not None:
        return _ALIASES
    from django.urls import URLPattern, URLResolver, get_resolver
    found = []

    def walk(pats, prefix=''):
        for p in pats:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, prefix + str(p.pattern))
                continue
            if not isinstance(p, URLPattern):
                continue
            path = (prefix + str(p.pattern)).replace('^', '').replace('$', '')
            cb = p.callback
            cls = getattr(cb, 'cls', None) or getattr(cb, 'view_class', None)
            if path.endswith('<str:kind>/') and path.count('<') == 1 and cls is not None and hasattr(cls, 'post'):
                import sys   # set-up lists of one view (Projects: products, teams, SLA policies; HSE: sites, contractors …)
                mod = sys.modules.get(cls.__module__)
                reg = getattr(mod, 'MASTERS', None) or getattr(mod, 'MASTER', None)
                if isinstance(reg, dict):
                    for k, v in reg.items():
                        m = v[0] if isinstance(v, (tuple, list)) else v
                        if m is not None and m.__name__ not in NOT_QUICK:
                            found.append(('/' + path.replace('<str:kind>', k).lstrip('/'), m))
                continue
            if '<' in path or '(?P' in path or 'bulk' in path.lower():
                continue
            acts = getattr(cb, 'actions', None)
            if cls is None or (acts is not None and acts.get('post') != 'create') or (acts is None and not hasattr(cls, 'post')):
                continue
            sc = getattr(cls, 'serializer_class', None)
            model = getattr(getattr(sc, 'Meta', None), 'model', None) if sc is not None else getattr(cls, 'zeo_model', None)
            if model is None or model.__name__ in NOT_QUICK or not is_master(model):
                continue
            found.append(('/' + path.lstrip('/'), model))

    walk(get_resolver().url_patterns)
    out = {}
    core = ('/organisation/', '/calendars/', '/employee/', '/core/', '/payroll/')   # a bare "Category" is the HR category
    global _ALL
    _ALL = {}
    for path, model in sorted(found, key=lambda x: (not x[0].startswith(core), len(x[0]))):
        last = [x for x in path.strip('/').split('/') if x][-1]
        for k in {_norm(_title_of(model)), _norm(model.__name__), _norm(last), _norm(model._meta.verbose_name_plural)}:
            if k and len(k) > 2:
                out.setdefault(k, path)
                if path not in _ALL.setdefault(k, []):
                    _ALL[k].append(path)
    out.setdefault('dept', out.get('department', ''))
    out.setdefault('desig', out.get('designation', ''))
    out.setdefault('cat', out.get('category', ''))
    out.setdefault('catogory', out.get('category', ''))
    _ALIASES = {k: v for k, v in out.items() if v}
    return _ALIASES


def _texts_in(model_name, texts):
    """do the drop-down's records (their shown names) belong to this master? (a "Provider" list of SSO providers is not the
    training providers' list)"""
    from django.apps import apps
    from django.db import models as M
    from django.db.models import Q
    model = next((m for m in apps.get_models() if m.__name__ == model_name), None)
    if model is None:
        return True
    cond = Q()
    names = [f.name for f in model._meta.get_fields() if isinstance(f, M.CharField) and not f.choices]
    for t in texts:
        t = t.strip()
        for n in names:
            cond |= Q(**{n + '__iexact': t})
            if len(t) > 3:
                cond |= Q(**{n + '__iexact': t.split(' – ')[-1].split(' (')[0].strip()})
    if not names:
        return True
    try:
        return model.objects.filter(cond).exists()
    except Exception:   # noqa: BLE001
        return True


def _meta_for(request, path):
    try:
        match = resolve(path)
    except Resolver404:
        return _no('Unknown list.')
    try:
        vc = getattr(match.func, 'view_class', None)
        if getattr(vc, '__name__', '') == 'OptionsView' and vc.__module__.startswith('ZeoHSE'):
            out = _hse_meta(request, match, path)
        elif getattr(vc, '__name__', '') == 'OptionsView':
            out = _kit_meta(request, match, path)
        else:
            out = _drf_meta(request, match, path)
    except Exception:   # noqa: BLE001
        log.exception('quick create meta %s', path)
        return _no('This list cannot be added to here.')
    return out if out is not None else _no('This list cannot be added to here.')


class QuickMetaView(APIView):
    """src – the list API the drop-down's list came from (may end with #key: one list of a bundle such as record-lookups);
    alias – names of the list (the screen's list, the field's label) when the list cannot be traced to its API."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        src = str(request.query_params.get('src') or '')
        names = [x for x in str(request.query_params.get('alias') or '').split(',') if x.strip()]
        prefix = ''
        if '#' in src:
            src, key = src.split('#', 1)
            names = [key] + names
            prefix = '/' + _path(src).strip('/').split('/')[0] + '/'
            src = ''
        page = str(request.query_params.get('page') or '')   # the screen: /main-sidebar/zeo-hse/… → the HSE lists first
        m = re.match(r'^/main-sidebar/(zeo-[a-z-]+)/', page)
        if not prefix and m:
            prefix = f'/{m.group(1)}/'
        seen = [x for x in str(request.query_params.get('seen') or '').split('|') if x.strip()][:6]
        if src:
            out = _meta_for(request, _path(src))
            if out.data.get('ok') or not names:
                return out
        tried = set()
        more = []
        for n in names:   # "New weekend calendar", "Default shift" … name the master after the first word
            w = str(n).strip().split()
            if len(w) > 1 and w[0].lower() in ('new', 'default', 'select', 'choose', 'the', 'a'):
                more.append(' '.join(w[1:]))
        for n in names + more:
            k = _norm(n)
            path = aliases().get(k)
            if prefix and _ALL.get(k):
                path = next((p for p in _ALL[k] if p.startswith(prefix)), path)
            if path and path not in tried:
                tried.add(path)
                out = _meta_for(request, path)
                if out.data.get('ok') and seen and not _texts_in(out.data.get('model'), seen):
                    return _no('The drop-down lists other records than the ' + str(out.data.get('title', '')).lower() + ' list.')
                out.data['via'] = n
                return out
        return _no('Unknown list.')


class QuickNamesView(APIView):
    """the names the server knows a master by (normalised: lower case, letters only, singular) – the screens ask for the form of a
    list they cannot trace to its API only when its name is one of these (one call per screen, not one per drop-down)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'names': sorted(aliases().keys())})
