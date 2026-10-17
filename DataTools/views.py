"""
Generic import for every list screen.

GET  /tools/api/fields/?endpoint=/organisation/api/Department/&schema=...
     Writable fields of that screen's API (label, required, type, choices,
     linked list + example values) so the screen can build an Excel / CSV
     template.
POST /tools/api/import/?schema=...
     {"endpoint": "/organisation/api/Department/", "rows": [{field: value}], "dry_run": true}
     dry_run=true only validates (no records, no e-mails). dry_run=false
     creates each row through the screen's own API, so the same rights,
     branch rules, workflows and duplicate checks apply. Linked fields
     (branch, department, employee ...) accept the id, code or name.
"""
import datetime
import re
from django.db import transaction
from django.db.models import Q
from django.urls import resolve, Resolver404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.views import APIView
from Translations.i18n import tr  # v1.15.0 language packs: messages in the user's language

MAX_ROWS = 5000
NATURAL_KEYS = ('emp_code', 'branch_code', 'dept_code', 'desgntn_code', 'ctgry_code', 'code', 'username', 'email',
                'branch_name', 'dept_name', 'desgntn_job_title', 'ctgry_title', 'name', 'title', 'type_name',
                'loan_type', 'schedule_name', 'calendar_title', 'calendar_code', 'serial_number', 'document_number',
                # v1.26.0: masters that only had their id (country / state / nationality / religion / currency by name)
                'country_name', 'country_code', 'state_name', 'state_code', 'N_name', 'religion', 'currency_code', 'currency_name')


def _view_for(endpoint):
    path = '/' + endpoint.split('?')[0].strip('/') + '/'
    try:
        match = resolve(path)
    except Resolver404:
        return None, None, path
    func = match.func
    cls = getattr(func, 'cls', None)
    actions = getattr(func, 'actions', None) or {}
    if cls is None or actions.get('post') != 'create':
        return None, None, path
    return func, cls, path


def _serializer(cls, request, data=None):
    view = cls()
    view.request = request
    view.format_kwarg = None
    view.action = 'create'
    view.kwargs = {}
    view.args = ()
    return view.get_serializer(data=data) if data is not None else view.get_serializer()


def _natural_fields(model):
    names = {f.name for f in model._meta.concrete_fields}
    keys = [k for k in NATURAL_KEYS if k in names]
    if model._meta.model_name == 'emp_master':
        keys = ['emp_code'] + [k for k in keys if k != 'emp_code']
    return keys


def _example(model, request, limit=8):
    keys = _natural_fields(model)
    if not keys:
        return []
    try:
        qs = model._default_manager.all()[:limit]
        out = []
        for o in qs:
            v = getattr(o, keys[0], None)
            if model._meta.model_name == 'emp_master':
                v = f"{o.emp_code}"
            if v not in (None, ''):
                out.append(str(v))
        return out
    except Exception:
        return []


def _describe(field, name, request):
    many = isinstance(field, serializers.ManyRelatedField)
    rel = field.child_relation if many else field
    from .duplicates import pretty
    lbl = str(field.label or '')
    d = {'key': name, 'label': pretty(name) if (not lbl or lbl.lower().replace(' ', '_') == name.lower()) else lbl,
         'required': bool(field.required), 'many': many, 'type': type(field).__name__.replace('Field', '').lower(),
         'help': str(field.help_text or '')}
    if isinstance(rel, serializers.RelatedField) and getattr(rel, 'queryset', None) is not None:
        m = rel.queryset.model
        d.update(type='link', link=str(m._meta.verbose_name).capitalize(), match_by=_natural_fields(m) + ['id'],
                 examples=_example(m, request))
    elif isinstance(field, serializers.ChoiceField):
        d.update(type='choice', choices=[{'value': k, 'label': str(v)} for k, v in list(field.choices.items())[:60]])
    elif isinstance(field, serializers.BooleanField):
        d.update(type='yes/no')
    elif isinstance(field, serializers.DateTimeField):
        d.update(type='datetime')
    elif isinstance(field, serializers.DateField):
        d.update(type='date')
    elif isinstance(field, (serializers.IntegerField, serializers.DecimalField, serializers.FloatField)):
        d.update(type='number')
    elif isinstance(field, (serializers.FileField, serializers.ImageField)):
        d.update(type='file')
    return d


def writable_fields(cls, request):
    ser = _serializer(cls, request)
    out = []
    for name, f in ser.fields.items():
        if f.read_only or isinstance(f, serializers.HiddenField):
            continue
        if name == 'id' or any(k in name for k in ('created', 'updated', 'modified')):
            continue
        out.append(_describe(f, name, request))
    out.sort(key=lambda d: (not d['required'], 0))
    return out


def _resolve_link(model, raw):
    """id / code / name -> pk (None when not found)."""
    s = str(raw).strip()
    if not s:
        return None
    q = Q()
    if s.isdigit():
        q |= Q(pk=int(s))
    for k in _natural_fields(model):
        q |= Q(**{f'{k}__iexact': s})
    if model._meta.model_name == 'emp_master':
        parts = s.split()
        if len(parts) >= 2:
            q |= Q(emp_first_name__iexact=parts[0], emp_last_name__iexact=' '.join(parts[1:]))
    if model._meta.model_name == 'cntry_mstr':   # v1.26.0: "UAE", "ARE", "971", "United Arab Emirates" -> the same row
        try:
            from CountryPack.packs import iso2
            iso = iso2(s, s)
            if iso:
                q |= Q(country_code__iexact=iso)
        except Exception:
            pass
    hits = list(model._default_manager.filter(q).values_list('pk', flat=True)[:2])
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1 and model._meta.model_name == 'cntry_mstr':
        return hits[0]
    if s.isdigit() and int(s) in hits:
        return int(s)
    return None


def _date(v):
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # Excel serial date
        return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).isoformat()
    s = str(v).strip()
    m = re.match(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    return s[:10] if re.match(r'^\d{4}-\d{2}-\d{2}', s) else s


def convert_row(ser, row):
    data, problems = {}, []
    for key, raw in row.items():
        f = ser.fields.get(key)
        if f is None or f.read_only:
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        many = isinstance(f, serializers.ManyRelatedField)
        rel = f.child_relation if many else f
        if isinstance(rel, serializers.RelatedField) and getattr(rel, 'queryset', None) is not None:
            model = rel.queryset.model
            vals = [x for x in re.split(r'[,;|]', str(raw)) if x.strip()] if many else [raw]
            ids = []
            for v in vals:
                pk = _resolve_link(model, v)
                if pk is None:
                    problems.append(tr('{0}: “{1}” not found', f.label or key, str(v).strip()))
                else:
                    ids.append(pk)
            data[key] = ids if many else (ids[0] if ids else None)
        elif isinstance(f, serializers.BooleanField):
            data[key] = str(raw).strip().lower() in ('1', 'true', 'yes', 'y', 'active')
        elif isinstance(f, serializers.DateTimeField):
            data[key] = _date(raw) if isinstance(raw, (int, float)) else str(raw).strip()
        elif isinstance(f, serializers.DateField):
            data[key] = _date(raw)
        elif isinstance(f, serializers.ChoiceField):
            s = str(raw).strip()
            match = next((k for k, lbl in f.choices.items() if s.lower() in (str(k).lower(), str(lbl).lower())), s)
            data[key] = match
        else:
            data[key] = raw if isinstance(raw, (int, float)) else str(raw).strip()
    return data, problems


def _flatten(errors):
    if isinstance(errors, dict):
        out = []
        from .duplicates import pretty
        for k, v in errors.items():
            for m in _flatten(v):
                lbl = pretty(k)
                out.append(m if k in ('non_field_errors', 'detail') or m.startswith(lbl) else f'{lbl}: {m}')
        return out
    if isinstance(errors, (list, tuple)):
        return [m for e in errors for m in _flatten(e)]
    return [str(errors)]


# ---- extra fields from the form designers (Chatter.FieldDef, employee custom fields) in templates and imports
X_TYPES = {'integer': 'number', 'decimal': 'number', 'currency': 'number', 'percent': 'number', 'rating': 'number',
           'date': 'date', 'datetime': 'datetime', 'checkbox': 'yes/no', 'file': 'file'}


def extra_fields(model):
    """Template columns for the designer fields of a model: keys 'x:<name>' (any screen) and 'ecf:<name>' (employee)."""
    out = []
    if model is None:
        return out
    try:
        from Chatter.models import FieldDef
        from Chatter.tracking import model_key
        for d in FieldDef.objects.filter(model=model_key(model), active=True):
            f = {'key': f'x:{d.name}', 'label': d.label, 'required': d.required, 'many': d.field_type == 'multiselect',
                 'type': X_TYPES.get(d.field_type, 'text'), 'help': d.help_text, 'designer': True}
            if d.field_type in ('dropdown', 'radio', 'multiselect'):
                f.update(type='choice', choices=[{'value': o, 'label': o} for o in d.options or []])
            elif d.field_type == 'employee':
                f.update(type='link', link='Employee', match_by=['emp_code', 'id'], examples=[])
            out.append(f)
    except Exception:
        pass
    if model._meta.model_name == 'emp_master':
        try:
            from EmpManagement.models import Emp_CustomField
            from .forms import _mandatory_map, field_meta
            req = _mandatory_map('employee')
            meta = field_meta('employee')
            for c in Emp_CustomField.objects.all():
                m = meta.get(c.emp_custom_field, {})
                if m.get('alias_of'):   # v1.16.0: merged into a built-in field – imported through that column only
                    continue
                f = {'key': f'ecf:{c.emp_custom_field}', 'label': c.emp_custom_field, 'required': bool(req.get(c.emp_custom_field)),
                     'many': False, 'type': X_TYPES.get(c.data_type or 'text', 'text'), 'help': m.get('help_text', ''), 'designer': True,
                     'example': (m.get('rules') or {}).get('example', '')}
                opts = c.dropdown_values or c.radio_values or (c.checkbox_values if c.data_type in ('multiselect',) else None)
                if c.data_type in ('dropdown', 'radio', 'multiselect') and opts:
                    f.update(type='choice', choices=[{'value': o, 'label': o} for o in opts])
                out.append(f)
        except Exception:
            pass
    if model._meta.label == 'OrganisationManager.Asset':
        # v1.16.0: asset custom fields (the asset form's one designer): 'acf:<name>'
        try:
            from OrganisationManager.models import AssetCustomField
            from .onedesign import asset_meta, asset_field_json
            meta = asset_meta()
            for c in AssetCustomField.objects.all():
                j = asset_field_json(c, meta)
                if j['alias_of']:
                    continue
                f = {'key': f'acf:{c.custom_field}', 'label': c.custom_field, 'required': j['required'], 'many': j['type'] == 'multiselect',
                     'type': X_TYPES.get(j['type'], 'text'), 'help': j['help_text'], 'designer': True, 'example': j['example']}
                if j['type'] in ('dropdown', 'radio', 'multiselect') and j['options']:
                    f.update(type='choice', choices=[{'value': o, 'label': o} for o in j['options']])
                out.append(f)
        except Exception:
            pass
    return out


def split_extras(row):
    base, x, ecf = {}, {}, {}
    for k, v in (row or {}).items():
        if str(k).startswith('x:'):
            x[k[2:]] = v
        elif str(k).startswith('ecf:'):
            ecf[k[4:]] = v
        elif str(k).startswith('acf:'):   # v1.16.0: asset custom fields travel with the employee-style extras
            ecf['acf:' + k[4:]] = v
        else:
            base[k] = v
    return base, x, ecf


def check_extras(model, x, ecf):
    errors = []
    if x:
        from Chatter.models import FieldDef
        from Chatter.tracking import model_key
        from Chatter.views import clean_value
        defs = {d.name: d for d in FieldDef.objects.filter(model=model_key(model), active=True)}
        for name, d in defs.items():
            v = x.get(name)
            _, err = clean_value(d, v)
            if err:
                errors.append(err)
    acf = {k[4:]: v for k, v in (ecf or {}).items() if str(k).startswith('acf:')}
    ecf = {k: v for k, v in (ecf or {}).items() if not str(k).startswith('acf:')}
    if acf and model._meta.label == 'OrganisationManager.Asset':
        from OrganisationManager.models import AssetCustomField
        from .onedesign import asset_typed
        by = {c.custom_field.lower(): c for c in AssetCustomField.objects.all()}
        for name, v in acf.items():
            c = by.get(str(name).strip().lower())
            if c is None:
                errors.append(tr('There is no custom field called “{0}”.', name))
                continue
            try:
                asset_typed(c, v)
            except ValueError as e:
                errors.append(str(e))
    if ecf and model._meta.model_name == 'emp_master':
        # v1.12.0: the same typed checks as the employee screens (type, options, rules, mandatory)
        from .forms import models_for, check_row
        D, V, fk = models_for('employee')
        lower = {n.lower(): n for n in D.objects.values_list('emp_custom_field', flat=True)}
        given = {lower.get(str(k).strip().lower(), k): v for k, v in (ecf or {}).items()}
        for name, v in given.items():
            if name not in lower.values():
                errors.append(tr('There is no custom field called “{0}”.', name))
                continue
            try:
                check_row(V, name, v, current=dict(given))
            except Exception as e:
                from .forms import _msg
                errors.append(_msg(e) if hasattr(e, 'detail') else str(e))
    return errors


def save_extras(request, model, obj_id, x, ecf):
    if x:
        from Chatter.tracking import model_key
        from Chatter.views import save_values
        save_values(request, model_key(model), obj_id, x, partial=True)
    acf = {k[4:]: v for k, v in (ecf or {}).items() if str(k).startswith('acf:')}
    ecf = {k: v for k, v in (ecf or {}).items() if not str(k).startswith('acf:')}
    if acf and model._meta.label == 'OrganisationManager.Asset':
        from OrganisationManager.models import AssetCustomField, AssetCustomFieldValue
        from .onedesign import asset_typed
        by = {c.custom_field.lower(): c for c in AssetCustomField.objects.all()}
        for name, v in acf.items():
            c = by.get(str(name).strip().lower())
            if c is not None and v not in (None, ''):
                AssetCustomFieldValue.objects.update_or_create(asset_id=obj_id, custom_field=c, defaults={'field_value': asset_typed(c, v)})
    if ecf:
        from EmpManagement.models import Emp_CustomFieldValue, emp_master
        emp = emp_master.objects.filter(pk=obj_id).first()
        for name, v in ecf.items():
            if emp is not None and v not in (None, ''):
                # the model checks and stores the value in its normal form (v1.12.0)
                Emp_CustomFieldValue(emp_custom_field=name, field_value=v, emp_master=emp, created_by=request.user).save()


class _check_access:
    @staticmethod
    def can_add(request, cls):
        from AccessControl.access import ctx, view_model
        c = ctx(request)
        if c.admin:
            return True
        model = view_model(cls())
        return bool(model) and f'add_{model._meta.model_name}' in c.codes


# v1.28.1: template columns per list that differ from the serializer – the company import had Schema name, Employer unique
# ID and Bank routing code (not asked when a company is created) and no way to say which apps the company uses.
TEMPLATE_OVERRIDES = {
    '/users/api/company/': {
        'hide': ['schema_name', 'employer_unique_id', 'bank_routing_code', 'paid_until', 'logo', 'cloud', 'db_alias', 'timezone'],
        'extra': [{'key': 'apps', 'label': 'Apps for this company', 'required': False, 'many': True, 'type': 'text',
                   'help': 'Comma-separated app keys: hrms, payroll, projects, crm, property, maintenance, hse (default hrms, payroll, projects)'}],
        'passthrough': ['apps'],
    },
}


def _overrides(path):
    return TEMPLATE_OVERRIDES.get(path, {})


class FieldsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        endpoint = request.query_params.get('endpoint', '')
        func, cls, path = _view_for(endpoint)
        if not cls:
            return Response({'detail': tr('This list cannot be imported.')}, status=status.HTTP_400_BAD_REQUEST)
        if not _check_access.can_add(request, cls):
            return Response({'detail': tr('You do not have permission to add these records.')}, status=status.HTTP_403_FORBIDDEN)
        from AccessControl.access import view_model
        ov = _overrides(path)
        fields = [f for f in writable_fields(cls, request) if f['key'] not in set(ov.get('hide', []))] + list(ov.get('extra', []))
        return Response({'endpoint': path, 'fields': fields + extra_fields(view_model(cls()))})


class ImportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        endpoint = request.data.get('endpoint', '')
        rows = request.data.get('rows') or []
        dry = bool(request.data.get('dry_run', True))
        func, cls, path = _view_for(endpoint)
        if not cls:
            return Response({'detail': tr('This list cannot be imported.')}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(rows, list) or not rows:
            return Response({'detail': tr('The file has no rows.')}, status=status.HTTP_400_BAD_REQUEST)
        if len(rows) > MAX_ROWS:
            return Response({'detail': tr('Import up to {0} rows at a time.', MAX_ROWS)}, status=status.HTTP_400_BAD_REQUEST)
        if not _check_access.can_add(request, cls):
            return Response({'detail': tr('You do not have permission to add these records.')}, status=status.HTTP_403_FORBIDDEN)
        schema = request.query_params.get('schema', '')
        factory = APIRequestFactory()
        seen = {}
        from .duplicates import rules_for
        from AccessControl.access import view_model
        model = view_model(cls())
        dup_fields = [f for f, _ in (rules_for(model) or [])]
        ok = 0
        # v1.26.0: no field-by-field history per imported row (≈3 extra queries a row); one summary note at the end
        from Chatter.tracking import paused
        _pause = paused()
        _pause.__enter__()
        try:
            res = self._rows(request, rows, dry, cls, func, path, schema, factory, model, dup_fields, seen)
        finally:
            _pause.__exit__(None, None, None)
        if not dry and res['ok']:
            try:
                from Chatter.models import Message
                from Chatter.tracking import model_key
                Message.objects.create(model=model_key(model), object_id='import', author=request.user,
                                       body=f"Imported {res['ok']} of {res['total']} rows from a file.")
            except Exception:
                pass
        return Response(res)

    def _rows(self, request, rows, dry, cls, func, path, schema, factory, model, dup_fields, seen):
        results, ok = [], 0
        for i, row in enumerate(rows, start=1):
            ser = _serializer(cls, request)
            base, xvals, ecf = split_extras(row if isinstance(row, dict) else {})
            data, problems = convert_row(ser, base)
            for k in _overrides(path).get('passthrough', []):   # v1.28.1: columns the create view reads itself (company apps)
                if base.get(k) not in (None, ''):
                    data[k] = str(base[k]).strip()
            problems += check_extras(model, xvals, ecf)
            for f in dup_fields:  # same value twice in the file
                v = str(data.get(f, '')).strip().lower()
                if v:
                    if (f, v) in seen:
                        from .duplicates import pretty
                        problems.append(tr('{0}: “{1}” is also on row {2}', pretty(f), data[f], seen[(f, v)]))
                    else:
                        seen[(f, v)] = i
            if problems:
                results.append({'row': i, 'ok': False, 'errors': problems})
                continue
            if dry:
                vs = _serializer(cls, request, data=data)
                if vs.is_valid():
                    results.append({'row': i, 'ok': True})
                    ok += 1
                else:
                    results.append({'row': i, 'ok': False, 'errors': _flatten(vs.errors)})
                continue
            sub = factory.post(f'{path}?schema={schema}', data, format='json')
            force_authenticate(sub, user=request.user)
            sub.tenant = getattr(request, 'tenant', None)
            try:
                with transaction.atomic():
                    resp = func(sub)
                    if resp.status_code >= 400:
                        raise _Rollback(resp)
                    new_id = (resp.data or {}).get('id') if isinstance(resp.data, dict) else None
                    if new_id and (xvals or ecf):
                        save_extras(request, model, new_id, xvals, ecf)
                results.append({'row': i, 'ok': True, 'id': (resp.data or {}).get('id') if isinstance(resp.data, dict) else None})
                ok += 1
            except _Rollback as rb:
                results.append({'row': i, 'ok': False, 'errors': _flatten(getattr(rb.resp, 'data', None) or 'Rejected')})
            except Exception as exc:  # keep going with the next row
                results.append({'row': i, 'ok': False, 'errors': [str(exc)[:200]]})
        return {'dry_run': dry, 'total': len(rows), 'ok': ok, 'failed': len(rows) - ok, 'results': results}


class _Rollback(Exception):
    def __init__(self, resp):
        self.resp = resp


class DirectoryView(APIView):
    """Light employee list (code, name, branch, department, designation, category) for list filters and
    'assign to employees' pickers. Rows follow the same access rules as the employee list."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from EmpManagement.models import emp_master
        from AccessControl.access import scope_queryset
        qs = emp_master.objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id')
        qs = scope_queryset(request, self, qs)
        if request.query_params.get('active', '1') == '1':
            qs = qs.filter(Q(is_active=True) | Q(is_active__isnull=True))
        out = []
        for e in qs.order_by('emp_code'):
            name = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
            out.append({
                'id': e.id, 'code': e.emp_code, 'name': name,
                'branch_id': e.emp_branch_id_id, 'branch': str(e.emp_branch_id or ''),
                'department_id': e.emp_dept_id_id, 'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
                'designation_id': e.emp_desgntn_id_id, 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '',
                'category_id': e.emp_ctgry_id_id, 'category': getattr(e.emp_ctgry_id, 'ctgry_title', '') or '',
            })
        try:  # v1.12.0: location / division / section / cost centre / grade / position / employment type when switched on
            from django.apps import apps as _apps
            if _apps.is_installed('OrgStructure'):
                from OrgStructure.hooks import directory_extend
                out = directory_extend(out)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('directory org fields failed')
        return Response(out)


DESIGNER_KEY = r'(FieldName|Mandatory|FieldHidden|DataType|DropdownValues|^dropdownValues|^selectedDataType)'


class FormSettingsView(APIView):
    """GET / PUT the form designer settings of the company.
    GET  /tools/api/form-settings/?form=employee        -> {"form": "employee", "data": {...}}
    PUT  /tools/api/form-settings/?form=employee  {"data": {...}}  (company admin or custom-field rights)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import FormSetting
        form = request.query_params.get('form', 'employee')[:60]
        row = FormSetting.objects.filter(form=form).first()
        return Response({'form': form, 'data': row.data if row else {}, 'updated_at': row.updated_at if row else None})

    def put(self, request):
        import re as _re
        from .models import FormSetting
        from AccessControl.access import ctx
        c = ctx(request)
        if not (c.admin or c.codes & {'change_emp_customfield', 'add_emp_customfield'}):
            return Response({'detail': tr('Only HR administrators can change the form designer.')}, status=status.HTTP_403_FORBIDDEN)
        form = request.query_params.get('form', 'employee')[:60]
        data = request.data.get('data') if isinstance(request.data, dict) else None
        if not isinstance(data, dict):
            return Response({'detail': tr('Send {"data": {...}}.')}, status=status.HTTP_400_BAD_REQUEST)
        if form == 'employee':
            data = {str(k)[:80]: (v if v is None else str(v)[:2000]) for k, v in data.items() if _re.search(DESIGNER_KEY, str(k))}
        row, _ = FormSetting.objects.get_or_create(form=form)
        row.data = data
        row.updated_by = request.user
        row.save()
        return Response({'form': form, 'data': row.data, 'updated_at': row.updated_at})
