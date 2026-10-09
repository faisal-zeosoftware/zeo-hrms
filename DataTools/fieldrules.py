"""
v1.12.0 – field rules shared by both form designers
(employee custom fields and the extra fields of any screen).

A field's rules are a small dict:
    default        value put in a new record when nothing is entered
    min / max      numbers, ratings, percentages, amounts: lowest / highest value
                   date / date and time / time: earliest / latest (YYYY-MM-DD, YYYY-MM-DDTHH:MM, HH:MM)
                   multi-select: fewest / most choices
    min_length / max_length   text, long text, e-mail, phone, web link: number of characters
    regex / regex_message     pattern the text must match, and the message shown when it does not
    visible_on     where the field shows: any of create, edit, view, list, export, ess (default: all)
    show_if        {"field": <other field>, "op": eq|ne|in|filled|empty, "value": ...} – the field is only
                   shown (and only required) when the condition holds
    ess_read_only  employees (self service) see the value but cannot change it
"""
import datetime
import re

RULE_KEYS = ('default', 'min', 'max', 'min_length', 'max_length', 'regex', 'regex_message', 'visible_on', 'show_if', 'ess_read_only')
VISIBLE = ('create', 'edit', 'view', 'list', 'export', 'ess')
SHOW_OPS = ('eq', 'ne', 'in', 'filled', 'empty')
NUMERIC = ('integer', 'decimal', 'currency', 'percent', 'rating')
TEXTUAL = ('text', 'textarea', 'email', 'phone', 'url')
TRUE = ('1', 'true', 'yes', 'y', 'on')
FALSE = ('0', 'false', 'no', 'n', 'off')


def _num(v):
    if v in (None, ''):
        return None
    return float(str(v).replace(',', '').replace('%', '').strip())


def clean_rules(raw, field_type='text'):
    """Validated rules from designer input. Returns (rules, error message or None)."""
    if raw in (None, ''):
        return {}, None
    if not isinstance(raw, dict):
        return {}, 'Field rules must be an object.'
    out = {}
    try:
        for k in ('min', 'max'):
            v = raw.get(k)
            if v in (None, ''):
                continue
            if field_type in NUMERIC or field_type == 'multiselect':
                out[k] = _num(v)
            elif field_type == 'date':
                out[k] = datetime.date.fromisoformat(str(v)[:10]).isoformat()
            elif field_type == 'datetime':
                out[k] = datetime.datetime.fromisoformat(str(v).replace(' ', 'T')[:16]).strftime('%Y-%m-%dT%H:%M')
            elif field_type == 'time':
                out[k] = datetime.datetime.strptime(str(v).strip()[:5], '%H:%M').strftime('%H:%M')
            else:
                return {}, f'A lowest / highest value does not apply to this field type.'
    except (TypeError, ValueError):
        return {}, 'Enter the lowest and highest value in the format of the field (numbers, YYYY-MM-DD dates or HH:MM times).'
    if 'min' in out and 'max' in out and out['min'] > out['max']:
        return {}, 'The lowest value must not be above the highest value.'
    for k in ('min_length', 'max_length'):
        v = raw.get(k)
        if v in (None, ''):
            continue
        if not str(v).strip().isdigit():
            return {}, 'Enter the shortest and longest length as whole numbers.'
        out[k] = int(str(v).strip())
    if out.get('min_length') is not None and out.get('max_length') is not None and out['min_length'] > out['max_length']:
        return {}, 'The shortest length must not be above the longest length.'
    rx = str(raw.get('regex') or '').strip()
    if rx:
        try:
            re.compile(rx)
        except re.error:
            return {}, 'The pattern is not a valid regular expression.'
        out['regex'] = rx[:300]
        out['regex_message'] = str(raw.get('regex_message') or '').strip()[:200]
    if raw.get('default') not in (None, ''):
        d = raw.get('default')
        out['default'] = d if isinstance(d, (list, bool, int, float)) else str(d)[:255]
    vis = raw.get('visible_on')
    if vis not in (None, ''):
        if isinstance(vis, str):
            vis = [x.strip() for x in vis.split(',') if x.strip()]
        if not isinstance(vis, list) or any(x not in VISIBLE for x in vis):
            return {}, f'“Show on” can only list: {", ".join(VISIBLE)}.'
        out['visible_on'] = [x for x in VISIBLE if x in vis]
    si = raw.get('show_if')
    if si not in (None, '', {}):
        if not isinstance(si, dict) or not str(si.get('field') or '').strip():
            return {}, '“Show only if” needs the other field.'
        op = si.get('op') or 'eq'
        if op not in SHOW_OPS:
            return {}, f'“Show only if” can only use: {", ".join(SHOW_OPS)}.'
        out['show_if'] = {'field': str(si['field']).strip()[:100], 'op': op, 'value': si.get('value')}
    if raw.get('ess_read_only') is not None:
        out['ess_read_only'] = str(raw.get('ess_read_only')).strip().lower() in TRUE
    return out, None


def is_empty(v):
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, tuple)) and not v)


def show_if_ok(rules, values):
    """True when the field is shown for these values ({field name or label: value})."""
    si = (rules or {}).get('show_if')
    if not si:
        return True
    name = si.get('field')
    lower = {str(k).lower(): v for k, v in (values or {}).items()}
    v = values.get(name) if name in (values or {}) else lower.get(str(name).lower())
    op, want = si.get('op') or 'eq', si.get('value')
    if op == 'filled':
        return not is_empty(v) and str(v).strip().lower() not in FALSE
    if op == 'empty':
        return is_empty(v) or str(v).strip().lower() in FALSE
    have = [str(x).strip().lower() for x in v] if isinstance(v, (list, tuple)) else [x.strip().lower() for x in str('' if v is None else v).split(',')]
    if isinstance(v, bool):
        have = ['yes' if v else 'no']
    wants = [str(x).strip().lower() for x in want] if isinstance(want, (list, tuple)) else [str('' if want is None else want).strip().lower()]
    norm = lambda s: {'true': 'yes', '1': 'yes', 'false': 'no', '0': 'no'}.get(s, s)
    have, wants = [norm(x) for x in have], [norm(x) for x in wants]
    hit = any(w in have for w in wants)
    return not hit if op == 'ne' else hit


def visible_on(rules, where):
    vis = (rules or {}).get('visible_on')
    return True if not vis else where in vis


def check_rules(label, field_type, value, rules):
    """Error message when a (normalised, non-empty) value breaks the field's rules, else None."""
    if not rules or is_empty(value):
        return None
    lo, hi = rules.get('min'), rules.get('max')
    try:
        if field_type in NUMERIC and (lo is not None or hi is not None):
            n = _num(value)
            if lo is not None and n < lo:
                return f'{label}: enter {_fmt(lo)} or more.'
            if hi is not None and n > hi:
                return f'{label}: enter {_fmt(hi)} or less.'
        elif field_type == 'multiselect' and (lo is not None or hi is not None):
            n = len(value if isinstance(value, list) else [x for x in str(value).split(',') if x.strip()])
            if lo is not None and n < lo:
                return f'{label}: choose at least {_fmt(lo)}.'
            if hi is not None and n > hi:
                return f'{label}: choose at most {_fmt(hi)}.'
        elif field_type in ('date', 'datetime', 'time') and (lo is not None or hi is not None):
            s = _iso(field_type, value)
            if lo is not None and s < str(lo):
                return f'{label}: enter {lo} or later.'
            if hi is not None and s > str(hi):
                return f'{label}: enter {hi} or earlier.'
    except (TypeError, ValueError):
        return None
    if field_type in TEXTUAL:
        s = str(value)
        if rules.get('min_length') is not None and len(s) < rules['min_length']:
            return f'{label}: enter at least {rules["min_length"]} characters.'
        if rules.get('max_length') is not None and len(s) > rules['max_length']:
            return f'{label}: enter at most {rules["max_length"]} characters.'
        if rules.get('regex'):
            try:
                ok = re.fullmatch(rules['regex'], s) is not None
            except re.error:
                ok = True
            if not ok:
                return f'{label}: {rules.get("regex_message") or "the value is not in the expected format."}'
    return None


def _fmt(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def _iso(field_type, v):
    """Sortable text of a date / date-time / time value (accepts DD-MM-YYYY too)."""
    s = str(v).strip()
    if field_type == 'date':
        m = re.match(r'^(\d{2})[-/](\d{2})[-/](\d{4})', s)
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}' if m else s[:10]
    if field_type == 'datetime':
        return s.replace(' ', 'T')[:16]
    return s[:5]
