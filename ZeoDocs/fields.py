"""v1.70.7 – Document Center fields like the form designer.

Three places hold field definitions, all in one shape:
  {key, label, field_type, options, required, section, order, help_text, placeholder, default, hidden, read_only, rules}
* a category's own fields (DocCategory.fields, JSON) – documents of that category;
* fields of every document (DocField scope 'document') – every document, whatever its category;
* folder fields (DocField scope 'folder') – every folder.
The 20 field types and the value checks are the form designer's (Chatter.views.clean_value, DataTools.fieldrules).
Values: Document.fields / DocFolder.fields {key: value} – dates as yyyy-mm-dd, Yes / No as true / false, multi-select as a list,
employee as {id, code, name}, file as {id, name} (a Chatter attachment of the document / folder).
"""
import re

from Translations.i18n import tr

TYPES = ('text', 'textarea', 'integer', 'decimal', 'currency', 'percent', 'date', 'datetime', 'time', 'checkbox', 'dropdown', 'multiselect',
         'radio', 'email', 'phone', 'url', 'file', 'employee', 'rating', 'color')
OPTION_TYPES = ('dropdown', 'multiselect', 'radio')
OLD = {'number': 'decimal', 'select': 'dropdown'}   # the 1.70.1 category field types
EXACT = ('integer', 'decimal', 'currency', 'percent', 'rating', 'date', 'checkbox', 'dropdown', 'radio', 'color', 'time')


class Refused(Exception):
    def __init__(self, msg, problems=None):
        super().__init__(msg)
        self.problems = problems or {}


def norm(f, i=0):
    """a stored definition (any version) in today's shape"""
    t = f.get('field_type') or OLD.get(f.get('type') or 'text', f.get('type') or 'text')
    return {'key': f.get('key'), 'label': f.get('label') or f.get('key'), 'field_type': t if t in TYPES else 'text',
            'options': list(f.get('options') or []), 'required': bool(f.get('required')), 'section': f.get('section') or '',
            'order': f.get('order') if isinstance(f.get('order'), int) else (i + 1) * 10, 'help_text': f.get('help_text') or '',
            'placeholder': f.get('placeholder') or '', 'default': '' if f.get('default') is None else str(f.get('default')),
            'hidden': bool(f.get('hidden')), 'read_only': bool(f.get('read_only')), 'rules': dict(f.get('rules') or {})}


def keyify(label):
    return ''.join(ch if ch.isalnum() else '_' for ch in str(label).lower()).strip('_')[:40]


class _F:
    """the shape Chatter.views.clean_value reads"""

    def __init__(self, d, required=None):
        self.label, self.field_type, self.options = d['label'], d['field_type'], d.get('options') or []
        self.required = d['required'] if required is None else required
        self.rules = d.get('rules') or {}


def clean_def(raw, i=0, taken=()):
    """a definition from the designer → today's shape; raises Refused with the field's problem"""
    from DataTools.fieldrules import clean_rules
    lab = ' '.join(str(raw.get('label') or '').split())[:80]
    if not lab:
        raise Refused(tr('Enter the label.'), {'label': tr('Required.')})
    t = raw.get('field_type') or OLD.get(raw.get('type') or 'text', raw.get('type') or 'text')
    if t not in TYPES:
        raise Refused(tr('Choose the field type.'), {'field_type': tr('Invalid.')})
    key = keyify(raw.get('key') or lab) or f'f{i + 1}'
    if key in taken:
        raise Refused(tr('Two fields have the same name "{0}".', lab), {'label': tr('Already used.')})
    opts = []
    if t in OPTION_TYPES:
        seen = set()
        for o in raw.get('options') or []:
            o = ' '.join(str(o).split())[:80]
            if o and o.lower() not in seen:
                seen.add(o.lower())
                opts.append(o)
        if not opts:
            raise Refused(tr('The list field "{0}" needs its choices.', lab), {'options': tr('Required.')})
        opts = opts[:100]
    rules, err = clean_rules(raw.get('rules') or {}, t)
    if err:
        raise Refused(err, {'rules': err})
    rules.pop('default', None)
    d = {'key': key, 'label': lab, 'field_type': t, 'options': opts, 'required': bool(raw.get('required')),
         'section': ' '.join(str(raw.get('section') or '').split())[:80], 'order': int(raw.get('order') or (i + 1) * 10),
         'help_text': str(raw.get('help_text') or '')[:255], 'placeholder': str(raw.get('placeholder') or '')[:120],
         'default': '', 'hidden': _true(raw.get('hidden')), 'read_only': _true(raw.get('read_only')), 'rules': rules}
    dv = raw.get('default')
    if dv in (None, '') and isinstance(raw.get('rules'), dict):
        dv = raw['rules'].get('default')
    if dv not in (None, '') and t not in ('file', 'employee'):
        from Chatter.views import clean_value
        v, e = clean_value(_F(d, required=False), dv)
        if e:
            raise Refused(tr('Default value: {0}', e), {'default': e})
        d['default'] = ', '.join(v) if isinstance(v, list) else ('Yes' if v is True else 'No' if v is False else str(v))
    if d['hidden'] and d['required'] and not d['default']:
        raise Refused(tr('A hidden field cannot be required without a default value.'), {'required': tr('Invalid.')})
    if d['read_only'] and d['required'] and not d['default']:
        raise Refused(tr('A read-only field that is required needs a default value.'), {'default': tr('Required.')})
    return d


def _true(v):
    return v is True or str(v).lower() in ('1', 'true', 'yes', 'on')


def check(defs, given, current=None, admin=False, create=False):
    """values of `defs` from `given` (a partial dict merges into `current`); returns the stored dict; raises Refused.
    Hidden fields keep their value (or the default on a new record); read-only ones too, except for a Document Center administrator."""
    from Chatter.views import clean_value
    from DataTools.fieldrules import show_if_ok
    current = dict(current or {})
    given = dict(given or {})
    out, problems = {}, {}
    labels = {d['label'].lower(): d for d in defs}
    for d in defs:
        k = d['key']
        if k not in given and d['label'] in given:   # a client may send labels (imports, Ask ZEO)
            given[k] = given[d['label']]
    view = {d['label']: given.get(d['key'], current.get(d['key'])) for d in defs}
    for d in defs:
        k = d['key']
        fixed = d['hidden'] or (d['read_only'] and not admin)
        if fixed or k not in given:
            v = current.get(k)
            if v in (None, '', []) and create and d['default'] not in ('', None):
                v, e = clean_value(_F(d, required=False), d['default'])
            if v not in (None, '', []):
                out[k] = v
            elif d['required'] and create and not fixed and d['field_type'] != 'file' and show_if_ok(d.get('rules'), view):   # a file comes right after the record is made
                problems[f'fields.{k}'] = tr('Required.')
                problems.setdefault('_first', tr('Fill in "{0}".', d['label']))
            continue
        raw = given.get(k)
        if raw in (None, '', []) and create and d['default'] not in ('', None):
            raw = d['default']
        req = d['required'] and show_if_ok(d.get('rules'), view) and not (create and d['field_type'] == 'file')
        v, e = clean_value(_F(d, required=req), raw)
        if e:
            problems[f'fields.{k}'] = e
            problems.setdefault('_first', e)
            continue
        if v not in (None, '', []):
            out[k] = v
    if problems:
        first = problems.pop('_first')
        raise Refused(first, problems)
    del labels
    return out


def text(d, v):
    """a stored value as text (exports, lists)"""
    if v in (None, '', []):
        return ''
    t = d['field_type']
    if isinstance(v, bool):
        return tr('Yes') if v else tr('No')
    if isinstance(v, list):
        return ', '.join(str(x) for x in v)
    if isinstance(v, dict):
        return v.get('name') if t == 'file' else ' – '.join(x for x in (v.get('code'), v.get('name')) if x)
    if t == 'date' and re.match(r'^\d{4}-\d{2}-\d{2}$', str(v)):
        y, m, dd = str(v).split('-')
        return f'{dd}/{m}/{y}'
    if t == 'datetime':
        s = str(v).replace('T', ' ')
        if re.match(r'^\d{4}-\d{2}-\d{2}', s):
            return f'{s[8:10]}/{s[5:7]}/{s[:4]}{s[10:16]}'
    return str(v)


def matches(d, v, want):
    """search filter: does the stored value match the searched one? (exact for numbers, dates, lists and Yes / No; words for text)"""
    want = str(want).strip()
    if want == '':
        return True
    if v in (None, '', []):
        return False
    t = d['field_type']
    if t == 'checkbox':
        return bool(v) == _true(want)
    if t in ('integer', 'decimal', 'currency', 'percent', 'rating'):
        try:
            return abs(float(v) - float(want.replace(',', ''))) < 0.005
        except ValueError:
            return False
    if t == 'multiselect':
        return want.lower() in [str(x).lower() for x in (v if isinstance(v, list) else [v])]
    if t in EXACT:
        return str(v).lower() == want.lower()
    return want.lower() in text(d, v).lower()


def visible_in(d, where):
    """the designer's "Show on" (create, edit, view, list, export): empty = everywhere"""
    from DataTools.fieldrules import visible_on
    return not d.get('hidden') and visible_on(d.get('rules') or {}, where)


# ------------------------------------------------------------------ the definitions in force
def doc_fields():
    from .models import DocField
    return [field_out(f) for f in DocField.objects.filter(scope='document')]


def folder_fields():
    from .models import DocField
    return [field_out(f) for f in DocField.objects.filter(scope='folder')]


def field_out(f):
    return {'id': f.id, 'scope': f.scope, 'key': f.key, 'label': f.label, 'field_type': f.field_type, 'options': f.options, 'required': f.required,
            'section': f.section, 'order': f.order, 'help_text': f.help_text, 'placeholder': f.placeholder, 'default': f.default,
            'hidden': f.hidden, 'read_only': f.read_only, 'rules': f.rules}


def cat_fields(cat):
    return [dict(norm(f, i), scope='category') for i, f in enumerate((cat.fields if cat else None) or [])]


def for_document(cat):
    """the document's fields: its category's own fields, then the fields of every document"""
    return cat_fields(cat) + doc_fields()
