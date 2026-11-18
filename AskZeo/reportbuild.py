"""Ask ZEO 1.73.2 – reports and dashboards built from a sentence, on the report builder (ZeoInsights, 1.53.0).

    "Create a report of opportunities by agent with the total expected value"
    "Build a dashboard of the sales pipeline value by salesperson"
    "Report of employee documents expiring this year by document type"

Without the company's AI the sentence is read by rules: the record type from its words (with a few business words – pipeline /
deals → opportunities, staff / headcount → employees, tickets, rent → leases …), the grouping after "by" / "per", totals of the
number fields named (value, amount, hours, rent, cost …) and "this month / this year" on the record's date. With the AI the record
type and the fields are chosen by the AI from the company's own list of record types and fields – never anything else.

Either way the report builder's own check (ZeoInsights.builder.clean) runs, the first rows are shown as the preview (run as the
user: rights, branches), and Confirm saves it with the report builder's API (POST /insights/api/reports/ – it opens in the report
centre under "Ask ZEO"); a dashboard adds a dashboard with a chart card of that report (POST /insights/api/dashboards/). Undo deletes
both. Staff users only (the report builder's own rule).
"""
import re

from Translations.i18n import tr

from . import changes
from .words import STOP, stem

MS = '/main-sidebar/'
LINK = MS + 'report-options/builder'
DLINK = MS + 'report-options/dashboards'
SYN = {'pipeline': 'opportunity', 'deal': 'opportunity', 'deals': 'opportunity', 'opportunities': 'opportunity', 'staff': 'employee',
       'headcount': 'employee', 'people': 'employee', 'workforce': 'employee', 'rent': 'lease', 'tenancy': 'lease', 'tenancies': 'lease',
       'salesperson': 'agent', 'salespeople': 'agent', 'salesman': 'agent', 'rep': 'agent', 'owner': 'agent', 'customer': 'account',
       'customers': 'account', 'helpdesk': 'ticket', 'visa': 'document', 'passport': 'document', 'expiring': 'expiry', 'expire': 'expiry',
       'ot': 'overtime', 'payslips': 'payslip', 'wo': 'work order', 'payroll': 'payslip', 'salary': 'payslip', 'salaries': 'payslip',
       'net': 'payslip', 'gross': 'payslip', 'deductions': 'payslip', 'pay': 'payslip'}   # v1.73.2: "net pay and deductions by …" 
EXTRA_STOP = {'report', 'reports', 'dashboard', 'dashboards', 'chart', 'graph', 'create', 'make', 'build', 'design', 'new', 'list', 'showing',
              'total', 'totals', 'number', 'count', 'sum', 'value', 'month', 'year', 'this', 'last', 'each', 'per', 'with', 'want', 'need', 'card'}
WANTS_DASH = re.compile(r'\b(dashboard|chart|graph|bar chart|line chart|kpi)\b', re.I)


def _t(s):
    out = set()
    for w in re.findall(r'[a-z0-9]+', str(s).lower()):
        w = SYN.get(w, w)
        for x in w.split():
            if x not in STOP and x not in EXTRA_STOP and len(x) > 1:
                out.add(stem(x))
    return out


def _subject(q):
    m = re.search(r'\b(?:report|dashboard|chart|graph|list)\s+(?:of|on|for|showing|about|with)\s+(?:the\s+|all\s+|my\s+)?(.+?)(?=\s+(?:by|per|grouped|for each|with)\b|[,.]|$)', q, re.I)
    if m:
        return m.group(1)
    m = re.search(r'\b(?:create|make|build|design)\s+(?:a|an|the)?\s*(?:new\s+)?(.+?)\s+(?:report|dashboard|chart)\b', q, re.I)
    return m.group(1) if m else q


def _active_module():
    try:
        from . import help as H
        act = H.active_apps()
        return lambda mod: mod in ('', 'hrms', 'settings', 'core') or act(mod)
    except Exception:   # noqa: BLE001
        return lambda mod: True


def candidates(c, question, n=12):
    from ZeoInsights import builder as B
    subj, qt = _t(_subject(question)), _t(question)
    on = _active_module()
    scored = []
    for m in B.models_for(c):
        lt = _t(m['label'])
        if not lt:
            continue
        s = 4 * len(subj & lt) + len(qt & lt) - 0.2 * len(lt - qt) + (0.5 if on(m.get('module') or '') else -2)
        if subj & lt:
            scored.append((s, m))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:n]]


def _field_by_words(fields, phrase, types=None):
    pt = _t(phrase)
    best, bs = None, 0
    for f in fields:
        if types and f['type'] not in types:
            continue
        lt = _t(f['label']) | _t(f['key'].replace('_', ' '))
        s = len(pt & lt) - 0.1 * len(lt - pt)
        if s > bs:
            best, bs = f, s
    return best if bs > 0 else None


def rule_spec(c, question):
    """The report definition read by rules, or None."""
    from ZeoInsights import builder as B
    ms = candidates(c, question, 3)
    if not ms:
        return None
    m = ms[0]
    fields = [f for f in B.fields_for(m['key']) if '›' not in f['label'] and f['key'] not in ('id', 'extra')]
    q = ' '.join(question.split())
    gm = re.search(r'\b(?:by|per|for each|grouped by)\s+(.+?)(?=\s+(?:and|with|for|this|last|in|from|showing)\b|[,.]|$)', q, re.I)
    group = _field_by_words(fields, gm.group(1), ('link', 'choice', 'text', 'bool')) if gm else None
    missing_group = gm.group(1) if gm and not group else ''
    nums = [f for f in fields if f['type'] == 'number' and not re.search(r'(^|_)id$|_id$', f['key'])]
    want = _t(q) | ({'value', 'amount'} if re.search(r'\bvalue|amount|total\b', q, re.I) else set())
    sums = [f for f in nums if (_t(f['label']) | _t(f['key'].replace('_', ' '))) & want][:3]
    title = next((f for f in fields if f['key'] in ('name', 'title', 'number', 'emp_code', 'subject', 'unit_no')), fields[0] if fields else None)
    dates = [f for f in fields if f['type'] in ('date', 'datetime')]
    cols = []
    for f in ([group] if group else []) + ([title] if title else []) + sums:
        if f and f['key'] not in [x['field'] for x in cols]:
            cols.append({'field': f['key'], 'label': f['label']})
    for f in fields:   # a few more readable columns when there is no grouping
        if len(cols) >= 6 or group:
            break
        if f['type'] in ('link', 'choice', 'date') and f['key'] not in [x['field'] for x in cols]:
            cols.append({'field': f['key'], 'label': f['label']})
    filters = []
    date_f = None
    if re.search(r'\b(this|current) (month|year)|\blast \d+ days|\bnext \d+ days|expir', q, re.I) and dates:
        date_f = next((f for f in dates if re.search(r'expir|due|close|end|date', f['key'])), dates[0]) if re.search(r'expir|due|clos', q, re.I) else dates[0]
        if re.search(r'this year|current year', q, re.I):
            filters.append({'field': date_f['key'], 'op': 'this_year', 'value': ''})
        elif re.search(r'this month|current month', q, re.I):
            filters.append({'field': date_f['key'], 'op': 'this_month', 'value': ''})
        else:
            nd = re.search(r'\bnext (\d+) days', q, re.I)
            filters.append({'field': date_f['key'], 'op': 'next_days', 'value': nd.group(1) if nd else '90'})
    aggs = [{'fn': 'count', 'field': ''}] + [{'fn': 'sum', 'field': f['key']} for f in sums] if group else []
    name = re.sub(r'^(please\s+)?(create|make|build|design|add)\s+(me\s+)?(a|an|the)?\s*(new\s+)?', '', q, flags=re.I).strip(' .?')
    name = name[:1].upper() + name[1:100]
    return {'model': m['key'], 'model_label': m['label'], 'name': name or m['label'], 'columns': cols, 'group_by': group['key'] if group else '',
            'aggregates': aggs, 'filters': filters, 'sort': '', 'limit': 0, 'date_field': '', 'missing_group': missing_group}


def ai_spec(request, c, question):
    """The AI chooses the record type and fields from the company's own lists (JSON)."""
    import json
    from ZeoInsights import builder as B
    from . import ai
    from .aiwork import _shield
    ms = candidates(c, question, 10)
    if not ms:
        from ZeoInsights import builder as B2
        ms = B2.models_for(c)[:0]
    if not ms:
        return None
    catalogue = []
    for m in ms[:6]:
        fl = [f for f in B.fields_for(m['key']) if f['key'] not in ('extra',)][:70]
        catalogue.append({'model': m['key'], 'label': m['label'], 'fields': [[f['key'], f['label'], f['type']] for f in fl]})
    meter, sh = ai.Meter('report'), _shield(__import__('AskZeo.scope', fromlist=['ctx']).ctx(request))
    system = ('You design a report for the report builder of an HR / ERP system. Choose ONE record type from the catalogue and answer ONLY with JSON: '
              '{"model": key, "name": short report title, "columns": [field keys, 2-8], "group_by": field key or "", "aggregates": [{"fn": count|sum|avg|min|max, '
              '"field": number field key or ""}], "filters": [{"field": key, "op": one of eq|ne|contains|gt|lt|gte|lte|empty|not_empty|true|false|this_month|'
              'this_year|last_days|next_days|date_overdue, "value": text}], "sort": field key or "-field" or "", "chart": bar|line|number|table}. '
              'Use only keys from the catalogue. sum / avg / min / max only on number fields. When grouping, put the grouping field first in columns. '
              'If a grouping or field the request asks for is not in the catalogue, add "missing": "what is missing" instead of using another field. No other text.')
    user = 'Request: ' + sh.text(question) + '\n\nCatalogue:\n' + json.dumps(catalogue, ensure_ascii=False)
    r = ai.call(request, meter, system, [{'role': 'user', 'content': user}], max_tokens=1200, small=True)
    meter.save(request.user)
    p = ai.json_of(r)
    if not isinstance(p, dict) or p.get('model') not in [m['key'] for m in ms]:
        return None
    fmap = {f['key']: f for f in B.fields_for(p['model'])}
    cols = [{'field': k, 'label': fmap[k]['label']} for k in (p.get('columns') or []) if k in fmap][:12]
    g = p.get('group_by') if p.get('group_by') in fmap else ''
    if g and g not in [x['field'] for x in cols]:
        cols.insert(0, {'field': g, 'label': fmap[g]['label']})
    aggs = [a for a in (p.get('aggregates') or []) if isinstance(a, dict) and a.get('fn') in ('count', 'sum', 'avg', 'min', 'max')
            and (a['fn'] == 'count' or fmap.get(a.get('field'), {}).get('type') == 'number')][:4] if g else []
    flt = [f for f in (p.get('filters') or []) if isinstance(f, dict) and f.get('field') in fmap][:5]
    srt = str(p.get('sort') or '')
    return {'model': p['model'], 'model_label': next(m['label'] for m in ms if m['key'] == p['model']), 'name': str(p.get('name') or '')[:100] or 'Report',
            'columns': cols, 'group_by': g, 'aggregates': [{'fn': a['fn'], 'field': a.get('field') or ''} for a in aggs],
            'filters': [{'field': f['field'], 'op': f.get('op'), 'value': str(f.get('value') or '')} for f in flt],
            'missing_group': str(p.get('missing') or '')[:120],
            'sort': srt if srt.lstrip('-') in fmap else '', 'limit': 0, 'date_field': '', 'chart': p.get('chart') if p.get('chart') in ('bar', 'line', 'number', 'table') else ''}


def build(request, c, conv, question, lang):
    from ZeoInsights import builder as B
    L = lang
    if not c.backoffice:
        return {'text': tr('Reports are for HR and other staff users.', lang=L), 'source': 'check', 'intent': 'build_report'}
    from . import ai
    mode, spec = 'builtin', None
    if ai.ready(request):
        try:
            spec = ai_spec(request, c, question)
            mode = 'ai' if spec else 'builtin'
        except ai.Unavailable:
            spec = None
    if not spec:
        spec = rule_spec(c, question)
    if not spec or not spec.get('columns'):
        return {'text': tr('Say what the report should show, for example: "Create a report of opportunities by agent with the total expected value". '
                           'You can also build it yourself in the Report builder.', lang=L),
                'source': 'help', 'intent': 'build_report', 'links': [{'label': tr('Report builder', lang=L), 'url': LINK}]}
    _, err = B.clean(spec, c)
    if err:
        return {'text': tr('The report could not be made: {0}', tr(err, lang=L), lang=L), 'source': 'check', 'intent': 'build_report',
                'links': [{'label': tr('Report builder', lang=L), 'url': LINK}]}
    try:
        cols, rows, _x = B.run(spec, request, limit=200)
    except ValueError as e:
        return {'text': tr('The report could not be made: {0}', str(e), lang=L), 'source': 'check', 'intent': 'build_report'}
    dash = bool(WANTS_DASH.search(question))
    body = {k: spec[k] for k in ('model', 'columns', 'filters', 'group_by', 'aggregates', 'sort', 'limit', 'date_field')}
    body.update({'name': spec['name'], 'description': ('Made by Ask ZEO: ' + question)[:255], 'section': 'Ask ZEO', 'shared': 'me'})
    steps = [{'method': 'post', 'path': '/insights/api/reports/', 'data': body, 'label': tr('Save the report {0}', spec['name'], lang=L),
              'show': f"{spec['model_label']}: {len(spec['columns'])} columns" + (f", grouped" if spec['group_by'] else ''),
              'undo': {'method': 'delete', 'path': '/insights/api/reports/{id0}/'}}]
    view = spec.get('chart') or ('bar' if spec['group_by'] else 'table')
    if dash:
        num = next((k for k, _l, t in cols[1:] if t == 'number' and not k.startswith('count')), None) if spec['group_by'] else None
        card = {'report': 'c-{id0}', 'title': spec['name'], 'view': view if spec['group_by'] or view == 'table' else 'table', 'size': 'w',
                'group': 'group' if spec['group_by'] else '', 'value': f'sum:{num}' if num else ('sum:count' if spec['group_by'] else 'count'), 'limit': 12}
        steps.append({'method': 'post', 'path': '/insights/api/dashboards/', 'data': {'name': spec['name'], 'cards': [card], 'shared': 'me'},
                      'label': tr('Make the dashboard {0}', spec['name'], lang=L), 'show': tr('one {0} chart', view, lang=L),
                      'undo': {'method': 'delete', 'path': '/insights/api/dashboards/{id1}/'}})
    ch = changes.preview(request, conv, 'report', tr('New report: {0}', spec['name'], lang=L), {'api': steps, 'atomic': True},
                         tr('Saved in the report centre under "Ask ZEO" (only for you until you share it). Undo deletes it.', lang=L), group='setup')
    head = [lab for _k, lab, _t2 in cols]
    sample = [[('' if r.get(k) is None else str(r.get(k)))[:40] for k, _l, _t2 in cols] for r in rows[:10]]
    text = tr('I designed the report **{0}** on {1}: {2} row(s) now. Check the preview – nothing is saved before you confirm.', spec['name'],
              tr(spec['model_label'], lang=L), len(rows), lang=L)
    if dash:
        text += ' ' + tr('Confirm also makes a dashboard with its chart.', lang=L)
    if spec.get('missing_group'):   # v1.73.2: say what the report builder cannot group by instead of grouping by something else silently
        text += '\n\n' + tr('Not available on this record type: {0}. The report shows what is available – add the grouping in the Report builder if '
                             'your version has it.', spec['missing_group'], lang=L)
    return {'text': text, 'source': 'check', 'intent': 'build_report', 'mode': mode, 'usage_logged': mode == 'ai',
            'cards': [{'type': 'table', 'title': tr('Preview', lang=L), 'columns': head, 'rows': sample}, changes.card(ch, L)],
            'links': [{'label': tr('Report builder', lang=L), 'url': LINK}] + ([{'label': tr('Dashboards', lang=L), 'url': DLINK}] if dash else [])}
