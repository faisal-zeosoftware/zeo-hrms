"""v1.15.0 – translated master data: lookups, the read swap, the write guard and saving translations."""
import logging

from django.db import connection
from rest_framework import permissions

from . import registry as R
from .i18n import SOURCE_LANG, current_lang, norm

log = logging.getLogger(__name__)
_TABLE_OK = {}


def tenant_schema(request=None):
    """The company schema translations are read from: the active schema, or ?schema= when a shared (public) API such as
    Core's countries / nationalities runs in the public schema."""
    schema = getattr(connection, 'schema_name', 'public') or 'public'
    if schema != 'public':
        return schema
    if request is None:
        from .i18n import _request
        request = _request()
    try:
        q = getattr(request, 'query_params', None) or request.GET
        s = (q.get('schema') or '').strip()
    except Exception:
        s = ''
    return s if s and s != 'public' and s.replace('_', '').isalnum() else None


def table_ready(schema=None):
    """FieldTranslation exists in the company schema (never in public; not before the company is migrated)."""
    schema = schema or getattr(connection, 'schema_name', 'public')
    if not schema or schema == 'public':
        return False
    if schema not in _TABLE_OK:
        try:
            from django_tenants.utils import schema_context
            from .models import FieldTranslation
            with schema_context(schema):
                _TABLE_OK[schema] = FieldTranslation._meta.db_table in connection.introspection.table_names()
        except Exception:
            _TABLE_OK[schema] = False
    return _TABLE_OK[schema]


def _query(fn, request=None):
    """Run a FieldTranslation query in the company schema (also from a public-schema API)."""
    schema = tenant_schema(request)
    if not table_ready(schema):
        return None
    if schema == getattr(connection, 'schema_name', 'public'):
        return fn()
    from django_tenants.utils import schema_context
    with schema_context(schema):
        return fn()


def _store(request):
    base = getattr(request, '_request', request)
    st = getattr(base, '_zeo_ft', None)
    schema = getattr(connection, 'schema_name', 'public')
    if st is None or st.get('_schema') != schema:
        st = {'_schema': schema}
        try:
            base._zeo_ft = st
        except Exception:
            pass
    return st


def is_raw(request):
    if request is None:
        return False
    try:
        q = getattr(request, 'query_params', None) or request.GET
        return str(q.get('raw', '')).lower() in ('1', 'true', 'yes')
    except Exception:
        return False


def read_lang(request):
    """Language to show translated names in, or None (English, a write, ?raw=1, no company schema). Once per request."""
    if request is None:
        return None
    base = getattr(request, '_request', request)
    cached = getattr(base, '_zeo_read_lang', False)
    if cached is not False:
        return cached
    lang = _read_lang(request)
    try:
        base._zeo_read_lang = lang
    except Exception:
        pass
    return lang


def _read_lang(request):
    try:
        if request.method not in permissions.SAFE_METHODS:
            return None
    except Exception:
        return None
    if is_raw(request):
        return None
    lang = current_lang(getattr(request, '_request', request))
    if not lang or lang == SOURCE_LANG or not table_ready(tenant_schema(request)):
        return None
    return lang


def id_map(label, lang, request=None):
    """{(record_id, field): value} of one model in one language (once per request)."""
    st = _store(request) if request is not None else {}
    key = ('id', label, lang)
    if key not in st:
        from .models import FieldTranslation
        try:
            st[key] = _query(lambda: {(r, f): v for r, f, v in FieldTranslation.objects.filter(model=label, lang=lang)
                                      .values_list('record_id', 'field', 'value') if v}, request) or {}
        except Exception:
            log.debug('translation map failed', exc_info=True)
            st[key] = {}
    return st[key]


def value_map(label, lang, request=None):
    """{english name: translated name} of one model (the first translatable field) – for report / list columns."""
    st = _store(request) if request is not None else {}
    key = ('val', label, lang)
    if key not in st:
        out = {}
        model = R.model_for(label)
        fields = R.fields_for(label)
        if model is not None and fields:
            ids = id_map(label, lang, request)
            f = fields[0]
            want = {rid for (rid, ff) in ids if ff == f}
            if want:
                try:
                    for pk, name in model.objects.filter(pk__in=want).values_list('pk', f):
                        if name and (pk, f) in ids:
                            out[str(name).strip()] = ids[(pk, f)]
                except Exception:
                    log.debug('value map failed', exc_info=True)
        st[key] = out
    return st[key]


def translated(obj, field, lang=None, request=None):
    """The value of obj.<field> in `lang` (request language by default); the original when there is no translation."""
    val = getattr(obj, field, None)
    if obj is None or not getattr(obj, 'pk', None):
        return val
    if lang:
        lang = norm(lang)
    else:
        if request is None:
            from .i18n import _request
            request = _request()
        lang = read_lang(request)
    if not lang or lang == SOURCE_LANG or field not in R.fields_for(type(obj)) or not table_ready(tenant_schema(request)):
        return val
    return id_map(R.concrete_label(type(obj)), lang, request).get((obj.pk, field), val)


# ------------------------------------------------------------------------------------------- read swap
_SOURCES = {}


def _dotted_sources(serializer):
    """[(key in the representation, [attr, ...], field)] of serializer fields such as CharField(source='emp_dept_id.dept_name')."""
    cls = type(serializer)
    if cls in _SOURCES:
        return _SOURCES[cls]
    out = []
    try:
        names = {f for spec in R.TRANSLATABLE.values() for f, _ in spec[1]}
        for key, fld in serializer.fields.items():
            src = getattr(fld, 'source', None) or ''
            if '.' in src and src.split('.')[-1] in names and not getattr(fld, 'write_only', False):
                parts = src.split('.')
                out.append((key, parts[:-1], parts[-1]))
    except Exception:
        out = []
    _SOURCES[cls] = out
    return out


def swap_representation(serializer, instance, rep):
    """Called for every ModelSerializer row of a GET: own translatable fields + dotted source fields."""
    if not isinstance(rep, dict) or instance is None:
        return rep
    request = (getattr(serializer, 'context', None) or {}).get('request')
    lang = read_lang(request)
    if not lang:
        return rep
    model = type(instance)
    fields = R.fields_for(model)
    if fields and getattr(instance, 'pk', None) is not None:
        m = id_map(R.concrete_label(model), lang, request)
        if m:
            for f in fields:
                if f in rep and isinstance(rep[f], str) and (instance.pk, f) in m:
                    rep[f] = m[(instance.pk, f)]
    for key, path, f in _dotted_sources(serializer):
        if key not in rep or not isinstance(rep[key], str):
            continue
        obj = instance
        try:
            for a in path:
                obj = getattr(obj, a, None)
                if obj is None:
                    break
        except Exception:
            obj = None
        if obj is not None and getattr(obj, 'pk', None) is not None and f in R.fields_for(type(obj)):
            v = id_map(R.concrete_label(type(obj)), lang, request).get((obj.pk, f))
            if v:
                rep[key] = v
    return rep


def swap_values(data, lang, request=None, depth=0):
    """Name lists such as department "branch": ["Dubai branch"] → translated, for the VALUE_COLUMNS keys (display only)."""
    if depth > 6 or not lang:
        return data
    if isinstance(data, list):
        for x in data:
            swap_values(x, lang, request, depth + 1)
    elif isinstance(data, dict):
        for k, v in list(data.items()):
            label = R.VALUE_COLUMNS.get(k)
            if label and isinstance(v, list) and v and all(isinstance(x, str) for x in v):
                vm = value_map(label, lang, request)
                if vm:
                    data[k] = [vm.get(x.strip(), x) for x in v]
            elif isinstance(v, (list, dict)):
                swap_values(v, lang, request, depth + 1)
    return data


# ------------------------------------------------------------------------------------------- write guard
def guard_write(serializer, data):
    """PUT / PATCH in another language: a translatable field sent back unchanged (the translated value the form was
    loaded with) keeps the original English value – the translation never overwrites the source."""
    inst = getattr(serializer, 'instance', None)
    if inst is None or isinstance(inst, (list, tuple)) or not hasattr(data, 'get') or getattr(serializer, 'parent', None) is not None:
        return data
    request = (getattr(serializer, 'context', None) or {}).get('request')
    if request is None or request.method not in ('PUT', 'PATCH', 'POST'):
        return data
    fields = R.fields_for(type(inst))
    if not fields or not table_ready(tenant_schema(request)):
        return data
    lang = current_lang(getattr(request, '_request', request))
    from .models import FieldTranslation
    rows = _query(lambda: {(f, l): v for f, l, v in FieldTranslation.objects.filter(model=R.concrete_label(type(inst)), record_id=inst.pk, field__in=fields)
                           .values_list('field', 'lang', 'value')}, request) or {}
    if not rows:
        return data
    changed = None
    for f in fields:
        sent = data.get(f)
        if not isinstance(sent, str) or not sent.strip():
            continue
        hit = rows.get((f, lang)) if lang != SOURCE_LANG else None
        if hit is None:  # a value of any language that is not the original
            hit = next((v for (ff, _l), v in rows.items() if ff == f and v.strip() == sent.strip()), None)
        if hit is not None and hit.strip() == sent.strip():
            orig = getattr(inst, f, None)
            if orig is not None and orig != sent:
                if changed is None:
                    try:
                        changed = data.copy()
                    except Exception:
                        changed = dict(data)
                changed[f] = orig
    return changed if changed is not None else data


# ------------------------------------------------------------------------------------------- rights + saving
def can_edit(request, model):
    from AccessControl.access import ctx
    c = ctx(request)
    return bool(c.admin or f'change_{model._meta.model_name}' in c.codes)


def can_view(request, model):
    from AccessControl.access import ctx
    c = ctx(request)
    name = model._meta.model_name
    return bool(c.admin or {f'change_{name}', f'view_{name}', f'add_{name}'} & c.codes)


def record_payload(model, obj, request=None):
    from .i18n import active_languages
    from .models import FieldTranslation
    label = R.concrete_label(model)
    spec = R.TRANSLATABLE.get(label) or ('', [])
    langs = active_languages()
    rows = {(r.field, r.lang): r for r in FieldTranslation.objects.filter(model=label, record_id=obj.pk)}
    fields = []
    for f, flabel in spec[1]:
        orig = getattr(obj, f, '') or ''
        tr = {}
        for lg in langs:
            r = rows.get((f, lg['code']))
            tr[lg['code']] = {'value': r.value if r else '', 'outdated': bool(r and r.source and r.source != orig),
                              'standard': bool(r and r.is_standard), 'id': r.id if r else None}
        fields.append({'field': f, 'label': flabel, 'original': orig, 'translations': tr})
    return {'model': label, 'model_label': spec[0], 'record_id': obj.pk, 'record': str(getattr(obj, spec[1][0][0], '') or obj) if spec[1] else str(obj),
            'languages': langs, 'fields': fields, 'can_edit': can_edit(request, model) if request is not None else False}


def save_translation(model, obj, field, lang, value, user=None, standard=False, force=True):
    """Create / change / delete (empty value) one translation. Returns 'created' | 'updated' | 'deleted' | 'kept' | None."""
    from .models import FieldTranslation
    label = R.concrete_label(model)
    lang = norm(lang)
    value = (value or '').strip()
    src = getattr(obj, field, '') or ''
    row = FieldTranslation.objects.filter(model=label, record_id=obj.pk, field=field, lang=lang).first()
    if not value:
        if row:
            row.delete()
            return 'deleted'
        return None
    if row:
        if not force or row.value == value:
            return 'kept'
        row.value, row.source, row.is_standard = value, src, standard
        row.updated_by_id = getattr(user, 'pk', None)
        row.save()
        return 'updated'
    FieldTranslation.objects.create(model=label, record_id=obj.pk, field=field, lang=lang, value=value, source=src,
                                    is_standard=standard, updated_by_id=getattr(user, 'pk', None))
    return 'created'


def display_name(obj, attr):
    """DisplayFieldsMixin labels (<fk>_display) in the request language."""
    try:
        return translated(obj, attr)
    except Exception:
        return getattr(obj, attr, None)


# ------------------------------------------------------------------------------------------- e-mails / notifications
def mail_texts(template, lang):
    """(subject, body) of an e-mail template for a reader of `lang`: the company's own translation of the template
    (Translate button on the template) → the language pack (exact English text) → the English template."""
    from .i18n import tr
    subject, body = getattr(template, 'subject', '') or '', getattr(template, 'body', '') or ''
    lang = norm(lang)
    if not lang or lang == SOURCE_LANG:
        return subject, body
    own = {}
    if table_ready() and getattr(template, 'pk', None) is not None:
        from .models import FieldTranslation
        own = dict(FieldTranslation.objects.filter(model=R.concrete_label(type(template)), record_id=template.pk, lang=lang,
                                                   field__in=('subject', 'body')).values_list('field', 'value'))
    return (own.get('subject') or tr(subject, lang=lang)), (own.get('body') or tr(body, lang=lang))


def reader_lang(user=None, employee=None):
    from .i18n import lang_for_user
    if user is None and employee is not None:
        user = getattr(employee, 'users', None)
    return lang_for_user(user)
