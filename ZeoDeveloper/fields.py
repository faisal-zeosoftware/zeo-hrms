"""ZEO Developer – the building blocks added to the fields of custom modules (D1). ZeoFlow.custom calls these hooks; a module that
uses none of the new types works exactly as in 1.67.26.

  formula   calculated field – options: the formula (level 2, see formulas.py); "result": number | money | date | text
  lookup    a value from a rate table – options: "<rate table>.<column>(<key field>)", e.g. fuel_norm.litres_100km(vehicle_type)
  table     child table – options: "<key>:<Label>:<type>, …" e.g. "litres:Litres:number, cost:Cost:money, day:Day:date" (the label may be left out)
  link      a link to another record – options: employee | branch | department | designation | asset | project | custom:<module key>
  qr        QR code – options: the text it holds, with {field} places (default "{number}")
  barcode   Code 128 barcode – the same
  Conditions on any field: "show_if" / "required_if" – a formula; the field is shown / required when it is not 0.
Values are computed when a record is saved (and by scheduled calculations); a value sent for a computed field is ignored.
"""
import re
from decimal import Decimal, InvalidOperation

from . import formulas as F

NEW_TYPES = ('formula', 'lookup', 'table', 'link', 'qr', 'barcode')
COMPUTED = ('formula', 'lookup', 'qr', 'barcode')
CHILD_TYPES = ('text', 'number', 'money', 'date', 'choice', 'yesno', 'employee')
LINK_TARGETS = ('employee', 'branch', 'department', 'designation', 'asset', 'project')
KEY_RE = re.compile(r'^[a-z][a-z0-9_]{0,39}$')
MAX_ROWS = 500


class FieldError(Exception):
    def __init__(self, msg, field=None):
        super().__init__(msg)
        self.field = field


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


# ------------------------------------------------------------------ design
def parse_columns(opts):
    cols, seen = [], set()
    for part in [p.strip() for p in str(opts or '').split(',') if p.strip()]:
        # key:type or key:Label:type (the label is shown as the column heading)
        bits = [x.strip() for x in part.split(':')]
        if len(bits) >= 3:
            k, label, t = bits[0], bits[1], ':'.join(bits[2:])
        else:   # key or key:type (a second part that is not a type is refused below, as before)
            k, label, t = bits[0], '', (bits[1] if len(bits) == 2 else '')
        k, t = k.lower(), (t.lower() or 'text')
        label = (label or k.replace('_', ' ').capitalize())[:60]
        choice = ''
        if t.startswith('choice'):
            choice = t[6:].strip(' ()[]')
            t = 'choice'
        if not KEY_RE.match(k) or k in seen or t not in CHILD_TYPES:
            return None
        seen.add(k)
        cols.append({'key': k, 'label': label, 'type': t, 'options': choice.replace('/', '|')})
    return cols or None


def parse_lookup(opts):
    m = re.fullmatch(r'\s*([a-z][a-z0-9_]*)\.([A-Za-z0-9_ ]+)\(\s*([a-z][a-z0-9_]*)\s*\)\s*', str(opts or ''))
    return {'table': m.group(1), 'column': m.group(2).strip(), 'key_field': m.group(3)} if m else None


def clean_extra(f, i, raw, all_keys):
    """Called by ZeoFlow.custom.clean_fields for every field: checks the new types and the conditions. Returns the extra keys kept."""
    t, opts, extra = f['type'], f.get('options') or '', {}
    if t == 'formula':
        if not opts.strip():
            raise FieldError(_tr('Field {0}: write the formula (e.g. quantity * unit_price).', i), 'fields')
        extra['result'] = raw.get('result') if raw.get('result') in ('number', 'money', 'date', 'text') else 'number'
    elif t == 'lookup':
        if parse_lookup(opts) is None:
            raise FieldError(_tr('Field {0}: write the lookup as table.column(key field), e.g. fuel_norm.litres_100km(vehicle_type).', i), 'fields')
    elif t == 'table':
        cols = parse_columns(opts)
        if cols is None:
            raise FieldError(_tr('Field {0}: list the columns as key:Label:type, e.g. litres:Litres:number, cost:Cost:money, station:Station:text.', i), 'fields')
        extra['columns'] = cols
    elif t == 'link':
        o = opts.strip().lower()
        if o.startswith('model:'):
            from .appdata import known, registry
            mm = known(opts.strip()[6:])
            if mm is None:
                raise FieldError(_tr('Field {0}: "{1}" is not a record type of the active apps.', i, opts.strip()[6:]), 'fields')
            f['options'] = 'model:' + registry().model_key(mm)
        elif o not in LINK_TARGETS and not re.fullmatch(r'custom:[a-z][a-z0-9_]{0,39}', o):
            raise FieldError(_tr('Field {0}: choose what it links to: employee, branch, department, designation, asset, project, custom:<module key> or model:<record type of any app>.', i), 'fields')
    for c in ('show_if', 'required_if'):
        v = str(raw.get(c) or '').strip()
        if v:
            extra[c] = v[:300]
    return extra


def check_module(fields):
    """Formulas of the module refer to its own fields only (run after every field is cleaned). Returns the problems."""
    keys = {f['key'] for f in fields} | {'number', 'state'}
    tables = {f['key'] for f in fields if f['type'] == 'table'}
    out = []
    for f in fields:
        forms = []
        if f['type'] == 'formula':
            forms.append(('', f['options']))
        for c in ('show_if', 'required_if'):
            if f.get(c):
                forms.append((c, f[c]))
        for kind, fx in forms:
            for p in F.problems(fx, keys | tables):
                out.append(f'{f["label"]}{" (" + kind + ")" if kind else ""}: {p}')
            names, _ = F.names_used(fx)
            if f['key'] in {n.lower() for n in names}:
                out.append(f'{f["label"]}: the formula uses the field itself.')
        if f['type'] == 'lookup':
            lk = parse_lookup(f['options'])
            if lk and lk['key_field'] not in keys:
                out.append(f'{f["label"]}: "{lk["key_field"]}" is not a field of the module.')
    # computed fields that use each other must not go round in a circle
    order, cycle = order_fields(fields)
    if cycle:
        out.append('These calculated fields use each other in a circle: ' + ', '.join(f['label'] for f in cycle) + '.')
    return out


def order_fields(fields):
    comp = [f for f in fields if f['type'] in COMPUTED]
    keys = {f['key'] for f in comp}
    deps = {}
    for f in comp:
        if f['type'] == 'formula':
            names, _ = F.names_used(f['options'])
            deps[f['key']] = {n.lower() for n in names} & keys - {f['key']}
        elif f['type'] == 'lookup':
            lk = parse_lookup(f['options']) or {}
            deps[f['key']] = {lk.get('key_field')} & keys
        else:
            deps[f['key']] = set(re.findall(r'\{([a-z][a-z0-9_]*)\}', f['options'] or '')) & keys
    done, order, pending = set(), [], list(comp)
    while pending:
        ready = [f for f in pending if deps[f['key']] <= done]
        if not ready:
            return order, pending
        for f in ready:
            order.append(f)
            done.add(f['key'])
        pending = [f for f in pending if f['key'] not in done]
    return order, []


# ------------------------------------------------------------------ values
def value(f, v):
    """Value of a new-type field as sent by the screen / a script (None = handled by ZeoFlow)."""
    t = f['type']
    if t in COMPUTED:
        return None
    if t == 'table':
        if v in (None, ''):
            return []
        if not isinstance(v, list):
            raise FieldError(_tr('{0}: the rows are not valid.', f['label']), f['key'])
        if len(v) > MAX_ROWS:
            raise FieldError(_tr('{0}: at most {1} rows.', f['label'], MAX_ROWS), f['key'])
        cols = f.get('columns') or parse_columns(f.get('options')) or []
        out = []
        for n, row in enumerate(v, 1):
            if not isinstance(row, dict):
                raise FieldError(_tr('{0}, row {1}: not valid.', f['label'], n), f['key'])
            r = {}
            for c in cols:
                r[c['key']] = _child_value(f, c, row.get(c['key']), n)
            if any(x not in (None, '', False) for x in r.values()):
                out.append(r)
        return out
    if t == 'link':
        if v in (None, ''):
            return None
        try:
            pk = int(str(v))
        except ValueError:
            raise FieldError(_tr('{0}: choose a record.', f['label']), f['key'])
        if link_label(f['options'], pk) is None:
            raise FieldError(_tr('{0}: the record chosen does not exist.', f['label']), f['key'])
        return pk
    return None


def _child_value(f, c, v, n):
    if v in (None, ''):
        return None
    t, label = c['type'], f'{f["label"]}, row {n}, {c["key"]}'
    s = str(v).strip()
    if t in ('number', 'money'):
        try:
            d = Decimal(s.replace(',', ''))
        except (InvalidOperation, ValueError):
            raise FieldError(_tr('{0}: enter a number.', label), f['key'])
        return str(d.quantize(Decimal('0.01'))) if t == 'money' else format(d.normalize(), 'f')
    if t == 'date':
        import datetime
        try:
            return datetime.date.fromisoformat(s[:10]).isoformat()
        except ValueError:
            raise FieldError(_tr('{0}: enter a date.', label), f['key'])
    if t == 'yesno':
        return str(v).lower() in ('1', 'true', 'yes', 'on')
    if t == 'choice':
        opts = [o.strip() for o in (c.get('options') or '').split('|') if o.strip()]
        if opts and s not in opts:
            raise FieldError(_tr('{0}: choose one of the list.', label), f['key'])
        return s
    if t == 'employee':
        if link_label('employee', s) is None:
            raise FieldError(_tr('{0}: choose an employee.', label), f['key'])
        return int(s)
    return s[:300]


def variables_of(m, data, number='', state=''):
    """Field values as formula names: numbers → Decimal, yes / no → 1 / 0, dates → date, text / choice → text."""
    import datetime
    out = {'number': number or '', 'state': state or ''}
    for f in m.fields:
        v = data.get(f['key'])
        t = f['type']
        if t == 'table':
            continue
        res = f.get('result') if t == 'formula' else None
        if t in ('number', 'money', 'employee', 'link') or res in ('number', 'money'):
            try:
                out[f['key']] = Decimal(str(v)) if v not in (None, '') else Decimal('0')
            except (InvalidOperation, ValueError):
                out[f['key']] = Decimal('0')
        elif t == 'yesno':
            out[f['key']] = Decimal(1 if v else 0)
        elif t == 'lookup':   # a rate value: a number when it is one
            try:
                out[f['key']] = Decimal(str(v)) if v not in (None, '') else Decimal('0')
            except (InvalidOperation, ValueError):
                out[f['key']] = '' if v is None else str(v)
        elif t == 'date' or res == 'date':
            try:
                out[f['key']] = datetime.date.fromisoformat(str(v)[:10]) if v else None
            except ValueError:
                out[f['key']] = None
        else:
            out[f['key']] = '' if v is None else str(v)
    return out


def lookup_value(table, key, column):
    from .models import RateTable
    t = RateTable.objects.filter(key=table).first()
    if t is None:
        raise ValueError(f'there is no rate table "{table}"')
    cols = t.columns or []
    if not cols:
        raise ValueError(f'the rate table "{table}" has no columns')
    if column not in cols:
        raise ValueError(f'the rate table "{table}" has no column "{column}"')
    k0 = cols[0]
    ks = F._text(key) if not isinstance(key, str) else key
    for r in t.rows or []:
        rv = r.get(k0)
        if str(rv).strip().lower() == str(ks).strip().lower():
            return _num_or_text(r.get(column))
        try:   # numeric keys: 12 == 12.00
            if Decimal(str(rv)) == Decimal(str(ks)):
                return _num_or_text(r.get(column))
        except (InvalidOperation, ValueError):
            pass
    raise ValueError(f'"{ks}" is not in the rate table "{table}"')


def _num_or_text(v):
    try:
        return Decimal(str(v).replace(',', ''))
    except (InvalidOperation, ValueError):
        return '' if v is None else str(v)


def compute(m, data, number='', state=''):
    """Fill the computed fields of `data` (in place) and check the conditions; raises FieldError with the field."""
    if not any(f['type'] in NEW_TYPES or f.get('show_if') or f.get('required_if') for f in m.fields):
        return data
    children = {f['key']: data.get(f['key']) or [] for f in m.fields if f['type'] == 'table'}
    order, cycle = order_fields(m.fields)
    if cycle:
        raise FieldError(_tr('Calculated fields use each other in a circle: {0}.', ', '.join(f['label'] for f in cycle)), cycle[0]['key'])
    for f in order:
        vars_ = variables_of(m, data, number, state)
        try:
            if f['type'] == 'formula':
                v = F.evaluate(f['options'], vars_, children, lookup_value)
                data[f['key']] = F.as_result(v, f.get('result') or 'number')
            elif f['type'] == 'lookup':
                lk = parse_lookup(f['options'])
                key = data.get(lk['key_field'])
                if key in (None, ''):
                    data[f['key']] = None
                else:
                    v = lookup_value(lk['table'], key, lk['column'])
                    data[f['key']] = F._text(v) if isinstance(v, Decimal) else v
            else:   # qr / barcode – text with {field} places
                tpl = f['options'] or '{number}'
                data[f['key']] = re.sub(r'\{([a-z][a-z0-9_]*)\}', lambda mm: str(data.get(mm.group(1), number if mm.group(1) == 'number' else '') or ''), tpl)[:300]
        except (F.FormulaError, ValueError) as e:
            raise FieldError(f'{f["label"]}: {e}', f['key'])
    vars_ = variables_of(m, data, number, state)
    for f in m.fields:
        if f.get('show_if'):
            try:
                shown = bool(F.evaluate(f['show_if'], vars_, children, lookup_value))
            except (F.FormulaError, ValueError):
                shown = True
            if not shown and f['type'] not in COMPUTED:
                data[f['key']] = None if f['type'] != 'table' else []
        if f.get('required_if') and data.get(f['key']) in (None, '', []):
            try:
                need = bool(F.evaluate(f['required_if'], vars_, children, lookup_value))
            except (F.FormulaError, ValueError):
                need = False
            if need:
                raise FieldError(_tr('{0} is required.', f['label']), f['key'])
    return data


# ------------------------------------------------------------------ links
def _link_qs(target):
    from django.apps import apps
    if target == 'employee':
        return apps.get_model('EmpManagement', 'emp_master').objects.all(), lambda e: f'{" ".join(x for x in (e.emp_first_name, e.emp_last_name) if x)} ({e.emp_code})'
    if target == 'branch':
        M = apps.get_model('OrganisationManager', 'brnch_mstr')
        return M.objects.all(), lambda b: getattr(b, 'branch_name', None) or str(b)
    if target == 'department':
        M = apps.get_model('OrganisationManager', 'dept_master')
        return M.objects.all(), lambda d: getattr(d, 'dept_name', None) or str(d)
    if target == 'designation':
        M = apps.get_model('OrganisationManager', 'desgntn_master')
        return M.objects.all(), lambda d: getattr(d, 'job_title', None) or getattr(d, 'desgntn_job_title', None) or str(d)
    if target == 'asset':
        # v1.73.4: the asset register of the Assets app (AssetCard), else the HR assets (OrganisationManager.Asset) – the names looked
        # for before (ZeoAssets.Asset …) do not exist, so a link to an asset found nothing
        for app, model in (('ZeoAssets', 'AssetCard'), ('OrganisationManager', 'Asset'), ('AssetPlus', 'Asset')):
            try:
                M = apps.get_model(app, model)
                return M.objects.all(), lambda a: ' – '.join(str(x) for x in (getattr(a, 'code', '') or getattr(a, 'asset_code', ''), getattr(a, 'name', '') or str(a)) if x)
            except LookupError:
                continue
        return None, None
    if target == 'project':
        for app, model in (('ZeoProjects', 'Project'), ('ProjectManagement', 'Project')):
            try:
                M = apps.get_model(app, model)
                return M.objects.all(), lambda p: ' – '.join(str(x) for x in (getattr(p, 'code', ''), getattr(p, 'name', '') or str(p)) if x)
            except LookupError:
                continue
        return None, None
    if target.startswith('model:'):   # any app's record type (all-apps extension)
        from .appdata import link_qs
        return link_qs(target)
    if target.startswith('custom:'):
        from ZeoFlow.models import CustomRecord
        key = target[7:]

        def lbl(r):
            # the first text field in the module's own order (the stored JSON keeps no order: jsonb sorts its keys)
            data = r.data or {}
            fs = [f for f in (r.module.fields or []) if data.get(f.get('key')) not in (None, '')]
            pick = next((f for f in fs if f.get('type') in ('text', 'choice')), None) or next(
                (f for f in fs if f.get('type') not in DEV_COMPUTED_OR_LIST), None)
            first = str(data.get(pick['key'])) if pick else ''
            return f'{r.number} {first}'.strip()
        return CustomRecord.objects.filter(module__key=key).select_related('module'), lbl
    return None, None


DEV_COMPUTED_OR_LIST = ('table', 'formula', 'lookup', 'qr', 'barcode', 'link', 'employee', 'yesno')


def link_label(target, pk):
    t = str(target or '')
    qs, lbl = _link_qs(t if t.startswith(('custom:', 'model:')) else t.lower())
    if qs is None:
        return None
    try:
        o = qs.filter(pk=int(pk)).first()
    except (ValueError, TypeError):
        return None
    return lbl(o) if o is not None else None


def link_options(target, q='', limit=50):
    qs, lbl = _link_qs(target)
    if qs is None:
        return []
    out = []
    ql = (q or '').strip().lower()
    for o in qs.order_by('-pk')[:2000] if ql else qs.order_by('-pk')[:limit]:
        label = lbl(o)
        if ql and ql not in label.lower():
            continue
        out.append({'id': o.pk, 'label': label})
        if len(out) >= limit:
            break
    return out


def displays(m, data):
    """Labels of link fields for the screen."""
    out = {}
    for f in m.fields:
        if f['type'] == 'link' and data.get(f['key']):
            out[f['key']] = link_label(f['options'], data.get(f['key'])) or ''
    return out
