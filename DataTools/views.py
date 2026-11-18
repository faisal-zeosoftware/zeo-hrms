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


def _rights_refusal(request, cls, path, schema, data, factory):
    """v1.56.1: the rights check of a row (branch, self-service …) without saving – the check before import said "OK" for rows
    the import then refused (e.g. a branch user's row of another branch)."""
    try:
        from rest_framework.parsers import JSONParser
        from rest_framework.request import Request
        from AccessControl.access import ZeoAccess
        raw = factory.post(f'{path}?schema={schema}', data, format='json')
        raw.tenant = getattr(request, 'tenant', None)
        req = Request(raw, parsers=[JSONParser()])
        req.user = request.user
        view = cls()
        view.request, view.format_kwarg, view.action, view.kwargs, view.args = req, None, 'create', {}, ()
        perm = ZeoAccess()
        if not perm.has_permission(req, view):
            return str(getattr(perm, 'message', '') or 'You may not add this record.')
    except Exception:
        return ''
    return ''


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


def _parts(s):
    return [x.strip() for x in re.split(r'\s*[|]\s*', str(s)) if x.strip()]


def _day(s):
    try:
        d = _date(s)
        from datetime import date as _d
        return _d.fromisoformat(str(d)[:10]) if d else None
    except Exception:   # noqa: BLE001
        return None


def _course_q(prefix, text):
    t = str(text).strip()
    return Q(**{f'{prefix}code__iexact': t}) | Q(**{f'{prefix}title__iexact': t}) | Q(**{f'{prefix}title__istartswith': t + ' '})


def _readable_link(model, s):
    """v1.67.17 (Falcon upload pack): records without a code a person can know before they exist, typed the way the screen shows
    them – "course | dd/mm/yyyy" for a training session, "employee code | course | dd/mm/yyyy" for a nomination, the employee
    code for an air ticket entitlement, the position title for a manpower requisition, "candidate e-mail | job title" for an
    application. Returns a pk, or None to fall back on the usual id / code / name match."""
    key = f'{model._meta.app_label}.{model._meta.model_name}'.lower()
    p = _parts(s)
    try:
        # "… | dd/mm/yyyy | 2" = the 2nd session of that course on that day (two batches the same day), in the order entered
        nth = None
        if len(p) >= 3 and re.fullmatch(r'\d{1,2}', p[-1] or '') and _day(p[-2]):
            nth, p = int(p[-1]), p[:-1]
        if key == 'learningmanagement.trainingsession' and len(p) >= 2 and _day(p[-1]):
            qs = model._default_manager.filter(_course_q('course__', ' | '.join(p[:-1])), start_date=_day(p[-1]))
            if not nth and qs.count() > 1:   # v1.73.4 a cancelled session (e.g. a duplicate) is not the one meant
                qs = qs.exclude(status='cancelled')
            hits = list(qs.order_by('pk').values_list('pk', flat=True)[:20])
            if nth:
                return hits[nth - 1] if 0 < nth <= len(hits) else None
            return hits[0] if len(hits) == 1 else None
        if key == 'learningmanagement.nomination' and len(p) >= 3 and _day(p[-1]):
            S = model._meta.get_field('session').related_model
            sq = S._default_manager.filter(_course_q('course__', ' | '.join(p[1:-1])), start_date=_day(p[-1]))
            if not nth and sq.count() > 1:   # v1.73.4
                sq = sq.exclude(status='cancelled')
            sess = list(sq.order_by('pk').values_list('pk', flat=True)[:20])
            if nth:
                sess = sess[nth - 1:nth]
            hits = list(model._default_manager.filter(employee__emp_code__iexact=p[0], session_id__in=sess).values_list('pk', flat=True)[:2])
            return hits[0] if len(hits) == 1 else None
        if key == 'payrollmanagement.airticketallocation' and p:
            qs = model._default_manager.filter(employee__emp_code__iexact=p[0])
            if len(p) > 1:
                qs = qs.filter(policy__name__iexact=p[1])
            qs = qs.exclude(status='REJECTED').order_by('-is_active', '-id')
            hit = qs.values_list('pk', flat=True).first()
            return hit
        if key == 'recruitmentmanagement.manpowerrequisition' and p:
            hit = model._default_manager.filter(Q(position_title__iexact=p[0]) | Q(document_number__iexact=p[0])).order_by('-id').values_list('pk', flat=True).first()
            return hit
        if key == 'recruitmentmanagement.jobopening' and p:
            hit = model._default_manager.filter(Q(title__iexact=p[0]) | Q(job_code__iexact=p[0])).order_by('-id').values_list('pk', flat=True).first()
            return hit
    except Exception:   # noqa: BLE001
        return None
    return None


def _resolve_link(model, raw, branch=None, many_out=None):
    """id / code / name -> pk (None when not found). v1.62.0: several rows with the same code / name (a salary component copied to
    every branch) → the row of the record's branch; still several → None and many_out gets True (the message says so)."""
    s = str(raw).strip()
    if not s:
        return None
    if not s.isdigit():   # v1.67.17 readable values of records without a code
        hit = _readable_link(model, s)
        if hit is not None:
            return hit
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
    if not q:   # v1.73.4 a record without a code or name (e.g. a nomination): an empty filter matched every row
        return None
    hits = list(model._default_manager.filter(q).values_list('pk', flat=True)[:50])
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1 and branch and not s.isdigit():   # v1.62.0 the same code in several branches: the record's branch
        names = {f.name for f in model._meta.get_fields()}
        for fld in ('branch', 'branch_id'):
            if fld in names:
                try:
                    mine = list(model._default_manager.filter(pk__in=hits, **{fld: branch}).values_list('pk', flat=True)[:2])
                except Exception:
                    mine = []
                if len(mine) == 1:
                    return mine[0]
                try:   # or the rows without a branch (company-wide)
                    common = list(model._default_manager.filter(pk__in=hits, **{f'{fld}__isnull': True}).values_list('pk', flat=True)[:2])
                except Exception:
                    common = []
                if len(common) == 1:
                    return common[0]
    if len(hits) > 1 and many_out is not None and not s.isdigit():
        many_out.append(True)
    if len(hits) > 1 and model._meta.model_name == 'cntry_mstr':
        return hits[0]
    if s.isdigit() and int(s) in hits:
        return int(s)
    return None


def _valid_hint(model, raw, limit=12):
    """v1.62.0 workbook: after "not found" – the closest value and the values that exist (codes / names), so the fix is obvious."""
    try:
        import difflib
        keys = _natural_fields(model)[:2]
        if not keys:
            return ''
        vals = []
        for row in model._default_manager.values_list(*keys)[:400]:
            vals += [str(x) for x in row if x not in (None, '')]
        if not vals:
            return ' – ' + tr('there are none yet: add them first (their own sheet loads earlier)')
        uniq = list(dict.fromkeys(vals))
        near = difflib.get_close_matches(str(raw).strip().lower(), [u.lower() for u in uniq], n=1, cutoff=0.6)
        out = ''
        if near:
            out = ' – ' + tr('did you mean “{0}”?', next(u for u in uniq if u.lower() == near[0]))
        shown = ', '.join(uniq[:limit]) + (' …' if len(uniq) > limit else '')
        return out + ' ' + tr('Valid values: {0}', shown)
    except Exception:   # noqa: BLE001
        return ''


def _many_hint(model, raw):
    """v1.62.0 workbook: which records share the name (their codes / ids)."""
    try:
        keys = _natural_fields(model)
        q = Q()
        for k in keys:
            q |= Q(**{f'{k}__iexact': str(raw).strip()})
        if not q:   # v1.73.4
            return ''
        rows = list(model._default_manager.filter(q).values_list('pk', *keys[:2])[:8])
        return ' (' + '; '.join(f'id {r[0]}' + ''.join(f', {x}' for x in r[1:] if x not in (None, '')) for r in rows) + ')'
    except Exception:   # noqa: BLE001
        return ''


def _date(v):
    if isinstance(v, (int, float)) and 20000 < v < 80000:  # Excel serial date
        return (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(v))).isoformat()
    s = str(v).strip()
    m = re.match(r'^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'
    return s[:10] if re.match(r'^\d{4}-\d{2}-\d{2}', s) else s


def _row_branch(ser, row):
    """v1.62.0: the branch of an imported row – its Branch column, else the branch of its employee (to pick per-branch rows)."""
    for key in ('branch', 'emp_branch_id', 'branch_id'):
        f = ser.fields.get(key)
        raw = row.get(key)
        if f is not None and raw not in (None, ''):
            rel = f.child_relation if isinstance(f, serializers.ManyRelatedField) else f
            if getattr(rel, 'queryset', None) is not None:
                v = str(raw).split(',')[0]
                return _resolve_link(rel.queryset.model, v)
    for key in ('employee', 'emp_id', 'employee_id'):
        f = ser.fields.get(key)
        raw = row.get(key)
        if f is not None and raw not in (None, '') and getattr(f, 'queryset', None) is not None:
            pk = _resolve_link(f.queryset.model, raw)
            if pk:
                try:
                    return f.queryset.model.objects.filter(pk=pk).values_list('emp_branch_id', flat=True).first()
                except Exception:
                    return None
    return None


def _data_uri_file(raw, key):
    """v1.67.17: 'data:<type>;name=<file name>;base64,<…>' -> a file for a FileField (None when not a data: URI)."""
    import base64
    from django.core.files.base import ContentFile
    s = str(raw).strip()
    m = re.match(r'^data:([\w.+/-]*)((?:;[\w-]+=[^;,]*)*);base64,(.*)$', s, re.S)
    if not m:
        return None
    params = dict(p.split('=', 1) for p in m.group(2).split(';') if '=' in p)
    try:
        content = base64.b64decode(m.group(3))
    except Exception:   # noqa: BLE001
        return None
    ext = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg'}.get(m.group(1), '')
    from urllib.parse import unquote
    name = unquote(params.get('name') or '') or f'{key}{ext or ".bin"}'
    return ContentFile(content, name=name)


def convert_row(ser, row):
    data, problems = {}, []
    branch = _row_branch(ser, row)
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
                several = []
                pk = _resolve_link(model, v, branch=branch, many_out=several)
                if pk is None and several:
                    problems.append(tr('{0}: “{1}” matches several records – use the id or a unique code', f.label or key, str(v).strip()) + _many_hint(model, v))   # v1.62.0 workbook
                elif pk is None:
                    problems.append(tr('{0}: “{1}” not found', f.label or key, str(v).strip()) + _valid_hint(model, v))   # v1.62.0 workbook
                else:
                    ids.append(pk)
            data[key] = ids if many else (ids[0] if ids else None)
        elif isinstance(f, serializers.FileField):
            # v1.67.17: a file column carries the file itself as a data: URI – the import window reads the files attached by name
            fobj = _data_uri_file(raw, key)
            if fobj is None:
                problems.append(tr('{0}: “{1}” – attach this file in the import window (Attach files), or leave the cell empty', f.label or key, str(raw).strip()[:80]))
            else:
                data[key] = fobj
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
    if branch is None and any('not found' in p for p in problems):
        # v1.62.0 workbook: "matches several records" only because the branch is unknown (its employee was not found) – said once the employee exists
        problems = [p for p in problems if 'matches several records' not in p] or problems
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
# v1.67.17 (Falcon upload pack): columns kept as a plain id by the screen's serializer that a person fills with a code – the
# template shows them as a link (employee code, project …) and the import turns the code into the id
ID_LINKS = {
    'employee_id': ('EmpManagement', 'emp_master', 'Employee'),
    'project_id': ('ProjectManagement', 'Project', 'Project'),
    'department_id': ('OrganisationManager', 'dept_master', 'Department'),
}

TEMPLATE_OVERRIDES = {
    '/organisation/api/asset-type/': {'hide': ['min_approvals_required']},   # v1.37.1: not asked on the screen either
    '/expense/api/expenses/': {'id_links': ['employee_id', 'project_id', 'department_id']},   # v1.67.17 (Falcon upload pack)
    '/expense/api/reports/': {'id_links': ['employee_id']},
    '/users/api/company/': {
        'hide': ['schema_name', 'employer_unique_id', 'bank_routing_code', 'paid_until', 'logo', 'cloud', 'db_alias', 'timezone'],
        'extra': [{'key': 'apps', 'label': 'Apps for this company', 'required': False, 'many': True, 'type': 'text',
                   'help': 'Comma-separated app keys: hrms, payroll, projects, crm, property, maintenance, hse (default hrms, payroll, projects)'}],
        'passthrough': ['apps'],
    },
}


def _overrides(path):
    return TEMPLATE_OVERRIDES.get(path, {})


def _id_link_model(key):
    from django.apps import apps as _apps
    app, name, _ = ID_LINKS[key]
    try:
        return _apps.get_model(app, name)
    except LookupError:
        return None


def _id_link_field(f, request):
    """v1.67.17: a plain id column (Employee id of an expense) shown as a link – the template asks for the employee code."""
    m = _id_link_model(f['key'])
    if m is None:
        return f
    lbl = ID_LINKS[f['key']][2]
    return dict(f, label=lbl, type='link', link=lbl, match_by=_natural_fields(m) + ['id'], examples=_example(m, request))


def _id_links(path, base):
    """v1.67.17: codes in the id-link columns -> ids (a number stays as it is); problems for codes not found."""
    problems = []
    for key in _overrides(path).get('id_links', []):
        raw = base.get(key)
        if raw in (None, '') or (isinstance(raw, (int, float)) and not isinstance(raw, bool)):
            continue
        m = _id_link_model(key)
        if m is None:
            continue
        several = []
        pk = _resolve_link(m, raw, many_out=several)
        if pk is None:
            problems.append(tr('{0}: “{1}” not found', ID_LINKS[key][2], str(raw).strip()) + ('' if several else _valid_hint(m, raw)))
        else:
            base[key] = pk
    return problems


def _auto_user_fields(ser, request, data):
    """v1.67.17: "created by" / "requested by" user columns are not in the template (the screen fills them with the signed-in
    user); a serializer that still requires one refused every imported row ("Created by: This field is required")."""
    from django.contrib.auth import get_user_model
    U = get_user_model()
    for name, f in ser.fields.items():
        if name in data or f.read_only or not f.required or not any(k in name for k in ('created', 'updated', 'modified')):
            continue
        rel = getattr(f, 'queryset', None)
        if rel is not None and rel.model is U:
            data[name] = request.user.pk


class FieldsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        endpoint = request.query_params.get('endpoint', '')
        func, cls, path = _view_for(endpoint)
        if not cls:
            from . import api_imports   # v1.67.17 lists of the Projects app (not a ViewSet) – their own create, row by row
            if api_imports.spec(path):
                return Response({'endpoint': path, 'fields': api_imports.public_fields(path)})
            return Response({'detail': tr('This list cannot be imported.')}, status=status.HTTP_400_BAD_REQUEST)
        if not _check_access.can_add(request, cls):
            return Response({'detail': tr('You do not have permission to add these records.')}, status=status.HTTP_403_FORBIDDEN)
        from AccessControl.access import view_model
        ov = _overrides(path)
        fields = [f for f in writable_fields(cls, request) if f['key'] not in set(ov.get('hide', []))] + list(ov.get('extra', []))
        fields = [_id_link_field(f, request) if f['key'] in ov.get('id_links', []) else f for f in fields]   # v1.67.17
        return Response({'endpoint': path, 'fields': fields + extra_fields(view_model(cls()))})


class ImportView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        endpoint = request.data.get('endpoint', '')
        rows = request.data.get('rows') or []
        dry = bool(request.data.get('dry_run', True))
        func, cls, path = _view_for(endpoint)
        # v1.67.17: the required setup (setup guide) applies to imports too – the rows are saved by the screens' own views
        # called from here, so SetupGateMiddleware (which looks at the request path /tools/api/import/) did not see them and
        # leave, expenses, loans … could be imported into a new company before its setup was finished
        try:
            from ZeoAdmin import setup_gate as _gate
            if _gate.TX.match(path) and _gate.blocked():
                return _gate.refused()
        except Exception:   # noqa: BLE001 – no setup guide app
            pass
        if not cls:
            from . import api_imports   # v1.67.17 lists of the Projects app: each row through the screen's own create
            if api_imports.spec(path) and isinstance(rows, list) and rows and len(rows) <= MAX_ROWS:
                return Response(api_imports.run(request, path, rows, dry, request.query_params.get('schema', ''), APIRequestFactory()))
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
        # v1.73.4: the rows are saved by calling the screen's own API inside the server, one row after the other. Those calls
        # counted against the user's 600 requests a minute, so a large file (e.g. 366 employee documents) was refused part-way
        # with "Request was throttled" – the import request itself is still rate-limited; only its own inner calls are not.
        from zeo import throttles as _thr
        _was = _thr.internal()
        _thr.INTERNAL.on = True
        # v1.73.4: salary lines – the formula components of each employee are calculated once after the rows (not after every
        # line), and not at all for the check (rolled back); 939 lines took longer than a request may take
        try:
            from PayrollManagement.signals import dependents_skipped, dependents_deferred
            _dep = dependents_skipped() if dry else dependents_deferred()
        except Exception:   # noqa: BLE001
            import contextlib
            _dep = contextlib.nullcontext()
        try:
            with _dep:
                res = self._rows(request, rows, dry, cls, func, path, schema, factory, model, dup_fields, seen)
        finally:
            _thr.INTERNAL.on = _was
            _pause.__exit__(None, None, None)
        if not dry:   # v1.52.0 the import is kept (created records → undo) and its column mapping remembered
            try:
                from ZeoAdmin.imports import remember
                res['batch'] = remember(request, path, model, res, request.data)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('import batch')
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
        # v1.67.17: the check of a file whose rows point at earlier rows of the same file (employees and their "reports to"
        # manager, a parent department …) said "not found – add them first" for every such row, though the import (row by
        # row) finds them. In the check, a link to a record an earlier row of the file makes counts as found.
        self_links, in_file = [], set()
        if dry and model is not None:
            try:
                self_links = [f.name for f in model._meta.concrete_fields if f.is_relation and f.related_model is model]
            except Exception:   # noqa: BLE001
                self_links = []
        nat = _natural_fields(model) if (self_links and model is not None) else []
        # v1.73.4: a large file is sent in parts (a request may take at most 300 s – 2,795 documents took longer): the rows of the
        # earlier parts come with the check (prior_rows, without file contents) so links and duplicates across parts are seen,
        # and the rows keep their numbers in the file (row_offset)
        start = 1
        try:
            start = 1 + max(0, int((request.data or {}).get('row_offset') or 0))
        except Exception:   # noqa: BLE001
            start = 1
        prior = (request.data or {}).get('prior_rows') if dry else None
        if isinstance(prior, list) and prior:
            _has_branch = False
            try:
                _has_branch = model is not None and model._meta.get_field('branch').many_to_one
            except Exception:   # noqa: BLE001
                _has_branch = False
            for j, prow in enumerate(prior, start=1):
                if not isinstance(prow, dict):
                    continue
                for k in nat:
                    if prow.get(k) not in (None, ''):
                        in_file.add(str(prow[k]).strip().lower())
                if not _has_branch:
                    for f in dup_fields:
                        v = str(prow.get(f, '') or '').strip().lower()
                        if v and (f, v) not in seen:
                            seen[(f, v)] = j
        for i, row in enumerate(rows, start=start):
            ser = _serializer(cls, request)
            base, xvals, ecf = split_extras(row if isinstance(row, dict) else {})
            for k in self_links:
                v = base.get(k)
                if v not in (None, '') and str(v).strip().lower() in in_file:
                    base.pop(k)
            for k in nat:
                if base.get(k) not in (None, ''):
                    in_file.add(str(base[k]).strip().lower())
            link_problems = _id_links(path, base)   # v1.67.17 employee code in an id column
            data, problems = convert_row(ser, base)
            problems = link_problems + problems
            _auto_user_fields(ser, request, data)   # v1.67.17 created_by = the user importing
            for k in _overrides(path).get('passthrough', []):   # v1.28.1: columns the create view reads itself (company apps)
                if base.get(k) not in (None, ''):
                    data[k] = str(base[k]).strip()
            problems += check_extras(model, xvals, ecf)
            # v1.67.15 a model kept per branch (salary components: the same code in each branch) – the same value in two rows of
            # different branches is not a duplicate; before, the second branch's row was refused "Code: … is also on row 2"
            _scope = ''
            try:
                _bf = model._meta.get_field('branch') if model is not None else None
                if _bf is not None and _bf.many_to_one:
                    _scope = '|' + str(data.get('branch') or '')
            except Exception:   # noqa: BLE001 – no branch field
                _scope = ''
            for f in dup_fields:  # same value twice in the file
                v = str(data.get(f, '')).strip().lower()
                if v:
                    v = v + _scope
                    if (f, v) in seen:
                        from .duplicates import pretty
                        problems.append(tr('{0}: “{1}” is also on row {2}', pretty(f), data[f], seen[(f, v)]))
                    else:
                        seen[(f, v)] = i
            if problems:
                # v1.62.0 workbook: the row's other mistakes too (an empty mandatory field …) – one round of corrections, not one per check
                try:
                    unresolved = {k for k, v in data.items() if v is None or v == []}
                    vs = _serializer(cls, request, data={k: v for k, v in data.items() if k not in unresolved})
                    if not vs.is_valid():
                        problems += [m for m in _flatten({k: e for k, e in vs.errors.items() if k not in unresolved}) if m not in problems]
                except Exception:   # noqa: BLE001
                    pass
                results.append({'row': i, 'ok': False, 'errors': problems})
                continue
            if dry:
                vs = _serializer(cls, request, data=data)
                refused = _rights_refusal(request, cls, path, schema, data, factory)   # v1.56.1 the check refuses what the import refuses
                if refused:
                    results.append({'row': i, 'ok': False, 'errors': [refused]})
                elif not vs.is_valid():
                    results.append({'row': i, 'ok': False, 'errors': _flatten(vs.errors)})
                else:
                    # v1.67.17: the check also runs the screen's own save (rolled back) – the rules of the save (sick leave
                    # needs its certificate, overlapping leave, balances …) were only found by Import, after "N rows ready"
                    from django.core.files.base import File as _F
                    for _v in data.values():
                        if isinstance(_v, _F):
                            _v.seek(0)
                    trial = _trial_save(request, func, path, schema, factory, data)
                    if trial:
                        results.append({'row': i, 'ok': False, 'errors': trial})
                    else:
                        results.append({'row': i, 'ok': True})
                        ok += 1
                continue
            sub = _sub_request(factory, path, schema, data, request)
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
        return {'dry_run': dry, 'total': len(rows), 'ok': ok, 'failed': len(rows) - ok, 'results': results}   # (per part)


class _Rollback(Exception):
    def __init__(self, resp):
        self.resp = resp


def _has_files(data):
    from django.core.files.base import File
    return any(isinstance(v, File) for v in data.values())


def _sub_request(factory, path, schema, data, request=None):
    """v1.67.17: rows with an attached file (file columns) go as multipart, the others as JSON as before."""
    from django.core.files.base import File
    host = {'HTTP_HOST': request.get_host()} if request is not None else {}
    if _has_files(data):
        flat = {}
        for k, v in data.items():
            if v is None:
                continue
            if isinstance(v, File):
                v.seek(0)   # the check before read it
            flat[k] = [str(x) for x in v] if isinstance(v, (list, tuple)) else v
        sub = factory.post(f'{path}?schema={schema}', flat, format='multipart', **host)
        for v in data.values():
            if isinstance(v, File):
                v.seek(0)
        return sub
    return factory.post(f'{path}?schema={schema}', data, format='json', **host)


class _Trial(Exception):
    pass


def _trial_save(request, func, path, schema, factory, data):
    """v1.67.17: the row saved by the screen's own view in a transaction that is always rolled back; [] when it would be saved,
    else the view's messages. E-mails and background jobs of the trial are not sent."""
    sub = _sub_request(factory, path, schema, data, request)
    force_authenticate(sub, user=request.user)
    sub.tenant = getattr(request, 'tenant', None)
    try:
        from zeo import mailguard
        guard = mailguard.suppressed()
    except Exception:   # noqa: BLE001
        import contextlib
        guard = contextlib.nullcontext()
    errors = []
    try:
        with guard, transaction.atomic():
            try:
                resp = func(sub)
                if resp.status_code >= 400:
                    errors = _flatten(getattr(resp, 'data', None) or 'Rejected')
            finally:
                transaction.set_rollback(True)
    except Exception as exc:   # noqa: BLE001
        errors = [str(exc)[:200]]
    return errors


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
