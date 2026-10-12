"""v1.15.0 – report centre and CEO dashboard in the request language. Runs AFTER the country-pack step (a translated
caption is the country caption translated): titles, descriptions, sections, notes and column labels through the language
packs; department / branch / designation … names in the rows through the translated master data; drill-down filters
translated the same way so a drill still finds its rows."""
import re

from . import registry as R
from .hooks import _tr_leaf
from .i18n import SOURCE_LANG, current_lang, tr

MONTH_RE = re.compile(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})$')


def _lang(request):
    from .services import is_raw
    if request is not None and is_raw(request):
        return None
    lang = current_lang(getattr(request, '_request', request) if request is not None else None)
    return lang if lang and lang != SOURCE_LANG else None


def _vmap(label, lang, request):
    from .services import table_ready, value_map
    return value_map(label, lang, request) if table_ready() else {}


def _name(key, value, lang, request):
    label = R.VALUE_COLUMNS.get(key)
    if not label or not isinstance(value, str) or not value:
        return value
    return _vmap(label, lang, request).get(value.strip(), value)


def _month(s, lang):
    m = MONTH_RE.match(s) if isinstance(s, str) else None
    return f'{tr(m.group(1), lang=lang)} {m.group(2)}' if m else s


def _drill(d, lang, request):
    if isinstance(d, dict) and isinstance(d.get('f'), dict):
        d['f'] = {k: ('|'.join(_name(k, x, lang, request) for x in v.split('|')) if isinstance(v, str) else v) for k, v in d['f'].items()}
    return d


def localize_list(out, request):
    lang = _lang(request)
    if not lang:
        return out
    for r in out:
        for k in ('title', 'description', 'section'):
            if isinstance(r.get(k), str):
                r[k] = _tr_leaf(r[k], lang)
    return out


def localize_report(data, request):
    lang = _lang(request)
    if not lang or not isinstance(data, dict):
        return data
    for k in ('title', 'description', 'section'):
        if isinstance(data.get(k), str):
            data[k] = _tr_leaf(data[k], lang)
    if data.get('notes'):
        data['notes'] = [_tr_leaf(n, lang) for n in data['notes']]
    for c in data.get('columns') or []:
        if isinstance(c.get('label'), str):
            c['label'] = _tr_leaf(c['label'], lang)
    for row in data.get('rows') or []:
        if not isinstance(row, dict):
            continue
        own = row.get('_m') if R.fields_for(row.get('_m') or '') else None   # the row IS a master record (departments report …)
        for k, v in list(row.items()):
            if own and k in ('name', 'title') and isinstance(v, str):
                row[k] = _vmap(own, lang, request).get(v.strip(), v)
            elif k in R.VALUE_COLUMNS and isinstance(v, str):
                row[k] = _name(k, v, lang, request)
            elif k == '_drill':
                _drill(v, lang, request)
            elif k == '_cell' and isinstance(v, dict):
                for d in v.values():
                    _drill(d, lang, request)
            elif isinstance(v, str) and MONTH_RE.match(v):
                row[k] = _month(v, lang)
    return data


def localize_ceo(data, request):
    lang = _lang(request)
    if not lang or not isinstance(data, dict):
        return data

    def walk(x, parent=None, depth=0):
        if depth > 6:
            return
        if isinstance(x, list):
            for y in x:
                walk(y, parent, depth + 1)
        elif isinstance(x, dict):
            for k, v in list(x.items()):
                if k in ('drill', 'drill_open', 'drill_hires', 'drill_gratuity', 'drill_leave'):
                    _drill(v, lang, request)
                elif k == 'label' and isinstance(v, str):
                    if parent == 'by_branch':
                        x[k] = _name('branch', v, lang, request)
                    elif parent == 'by_department':
                        x[k] = _name('department', v, lang, request)
                    else:
                        x[k] = _month(_tr_leaf(v, lang), lang)
                elif k == 'name' and parent == 'branches' and isinstance(v, str):
                    x[k] = _name('branch', v, lang, request)
                elif isinstance(v, (list, dict)):
                    walk(v, k, depth + 1)
    walk(data)
    return data
