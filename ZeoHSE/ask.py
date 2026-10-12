"""Ask ZEO AI – HSE data (v1.16.1).

Tools that DashboardManagement/assistant.py offers to the AI (and uses in basic mode). Everything runs AS THE ASKING USER
with the same scopes as the HSE screens: employees see only their own records, medical and claim details stay hidden from
anyone without the right, and only what is returned here is sent to the AI provider (at most 100 rows, long texts cut).

    summary(request, date_from, date_to)          the HSE dashboard figures (TRIR, LTIFR, incidents, actions, permits …)
    query(request, kind, …)                       list / filter / group the records of one register
    my_work(request)                              what waits for the user: approvals, actions, permits, investigations
"""
import re
from datetime import date, datetime
from decimal import Decimal

PAGE = '/main-sidebar/zeo-hse/'
MAX_ROWS = 100

CORE = {   # kind → (label, list page, record page pattern, main date field)
    'incidents': ('Incidents and near misses', 'incidents', 'incidents/{id}', 'occurred_at'),
    'permits': ('Permits to work', 'permits', 'permits/{id}', 'valid_from'),
    'risks': ('Risk assessments', 'risks', 'risks/{id}', 'created_at'),
    'actions': ('Corrective actions (CAPA)', 'actions', 'actions?open={id}', 'due_date'),
    'inspections': ('Inspections', 'inspections', 'inspections/{id}', 'date'),
    'observations': ('BBS observations', 'observations', 'observations', 'reported_at'),
    'toolbox': ('Toolbox talks', 'toolbox', 'toolbox', 'date'),
}


def available():
    try:
        from django.apps import apps
        if not apps.is_installed('ZeoHSE'):
            return False
        from .models import HSESettings   # v1.17.0 – only once HSE is activated in this company
        return HSESettings.activated()
    except Exception:
        return False


def _plain(v):
    if isinstance(v, dict):
        for k in ('name', 'number', 'label'):
            if k in v and v[k] not in (None, ''):
                return v[k]
        return None
    if isinstance(v, (list, tuple)):
        return ', '.join(str(_plain(x)) for x in v if _plain(x) not in (None, ''))[:200]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()[:16]
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        return v[:300]
    return v


def _flat(row):
    out = {}
    for k, v in row.items():
        if k.startswith('_') or k in ('can', 'steps', 'history', 'files', 'prerequisites', 'simops', 'band', 'initial_band', 'gas_tests',
                                      'isolations', 'lines', 'checklist', 'checklist_answers', 'answers', 'questions', 'actions'):
            continue
        if k.endswith('_label'):
            continue
        lab = row.get(k + '_label')
        out[k] = _plain(lab if lab not in (None, '') else v)
    if isinstance(row.get('band'), dict):
        out['residual_band'] = row['band'].get('label')
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _match(row, cond):
    col, op, val = cond.get('column'), cond.get('op') or 'eq', cond.get('value')
    v = row.get(col)
    s = '' if v is None else str(v).lower()
    vals = [str(x).lower() for x in (val if isinstance(val, list) else [val])]
    if op == 'empty':
        return v in (None, '')
    if op == 'not_empty':
        return v not in (None, '')
    if op in ('eq', 'in'):
        return s in vals
    if op in ('ne', 'not_in'):
        return s not in vals
    if op == 'contains':
        return any(x in s for x in vals)
    if op == 'not_contains':
        return not any(x in s for x in vals)
    if op == 'starts':
        return any(s.startswith(x) for x in vals)
    a, b = _num(v), _num(val)
    if a is None or b is None:   # dates compare as text (YYYY-MM-DD)
        a, b = s[:10], str(val).lower()[:10]
    return {'gt': a > b, 'gte': a >= b, 'lt': a < b, 'lte': a <= b}.get(op, False)


def kinds(request):
    """The registers this user may ask about."""
    from .registers import REGS
    out = [{'kind': k, 'title': v[0]} for k, v in CORE.items()]
    return out + [{'kind': r.kind, 'title': r.plural} for r in REGS.values() if r.can_open(request) and not r.child_of]


def _source(request, kind):
    """(rows as dicts, list page, record pattern, date field, title)"""
    from . import access as A
    from . import rows as RW
    from . import views as V
    from .models import Inspection, Observation, ToolboxTalk
    if kind in CORE:
        title, lst, rec, dfield = CORE[kind]
        if kind == 'incidents':
            qs, fn = V.incident_scope(request), lambda x: RW.incident(x, False, request)
        elif kind == 'permits':
            qs, fn = V.permit_scope(request), RW.permit
        elif kind == 'risks':
            qs, fn = V.risk_scope(request).prefetch_related('lines'), RW.risk
        elif kind == 'actions':
            qs, fn = V.action_scope(request), RW.action
        elif kind == 'inspections':
            qs, fn = V.staff_or_own(request, Inspection.objects.select_related('template', 'site', 'area', 'inspector'), 'inspector'), RW.inspection
        elif kind == 'observations':
            qs, fn = V.staff_or_own(request, Observation.objects.select_related('site', 'area', 'reported_by'), 'reported_by'), lambda o: RW.observation(o, request)
        else:
            qs, fn = V.staff_or_own(request, ToolboxTalk.objects.select_related('site', 'presenter'), 'presenter'), RW.talk
        return qs, fn, lst, rec, dfield, title
    from .registers import REGS, ser
    r = REGS.get(kind)
    if r is None:
        raise KeyError(kind)
    A.need(r.can_open(request), f'You have no access to {r.plural.lower()}.')
    dfield = next((c for c in r.cols if c in ('due_date', 'planned_date', 'assessed_on', 'generated_on', 'sampled_on', 'planned_on', 'date',
                                              'effective_date', 'issued_on', 'start_date', 'period')), None)
    return r.scope(request), (lambda x: ser(r, x, request, tuple(dict.fromkeys(r.cols + r.form)))), f'r/{kind}', f'r/{kind}/{{id}}', dfield, r.plural


def query(request, kind, status=None, open_only=False, overdue=False, search=None, date_from=None, date_to=None, where=None,
          group_by=None, columns=None, sort_by=None, descending=False, limit=20, **_):
    from . import access as A
    from . import services as S
    try:
        qs, fn, lst, rec, dfield, title = _source(request, kind)
    except KeyError:
        return {'error': f'Unknown kind "{kind}". Kinds: ' + ', '.join(k['kind'] for k in kinds(request))}
    except A.Denied as e:
        return {'error': e.message}
    rows = []
    for x in qs[:3000]:
        r = fn(x)
        f = _flat(r)
        f['id'] = r.get('id')
        f['open'] = PAGE + rec.format(id=r.get('id'))
        rows.append(f)
    closed = ('closed', 'cancelled', 'archived', 'superseded', 'rejected', 'disposed', 'expired', 'settled', 'completed')
    if open_only:
        rows = [r for r in rows if str(r.get('status') or '').lower() not in closed and
                not any(str(r.get('status') or '').lower().startswith(c) for c in ('closed', 'verified', 'cancel', 'archived', 'superseded', 'disposal'))]
    if status:
        st = [str(s).lower() for s in (status if isinstance(status, list) else [status])]
        rows = [r for r in rows if str(r.get('status') or '').lower() in st or any(s in str(r.get('status') or '').lower() for s in st)]
    if overdue:
        t = S.today().isoformat()
        rows = [r for r in rows if r.get('overdue') is True or (r.get('due_date') and str(r['due_date'])[:10] < t and
                                                                 not str(r.get('status') or '').lower().startswith(('closed', 'verified', 'cancel', 'done')))]
    if search:
        q = str(search).lower()
        rows = [r for r in rows if any(q in str(v).lower() for v in r.values() if isinstance(v, str))]
    if dfield and (date_from or date_to):
        rows = [r for r in rows if r.get(dfield) and (not date_from or str(r[dfield])[:10] >= str(date_from)) and (not date_to or str(r[dfield])[:10] <= str(date_to))]
    keys = set().union(*[r.keys() for r in rows]) if rows else set()
    bad = [c.get('column') for c in (where or []) if rows and c.get('column') not in keys]
    if bad:
        return {'error': f'Unknown column(s) {bad}. Columns: {sorted(keys)}'}
    rows = [r for r in rows if all(_match(r, c) for c in (where or []))]
    if sort_by and sort_by in keys:
        rows.sort(key=lambda r: (r.get(sort_by) is None, str(r.get(sort_by)) if _num(r.get(sort_by)) is None else _num(r.get(sort_by))), reverse=bool(descending))
    out = {'register': title, 'kind': kind, 'rows_matching': len(rows), 'date_field': dfield, 'columns': sorted(keys - {'open'}),
           'list_link': PAGE + lst}
    if group_by:
        gb = [(g + '_name' if g not in keys and g + '_name' in keys else g) for g in (group_by if isinstance(group_by, list) else [group_by])]
        gb = [g for g in gb if g in keys or g == 'month']
        groups = {}
        for r in rows:
            k = tuple(str((str(r.get(dfield) or '')[:7] if g == 'month' else r.get(g)) or '(empty)') for g in gb)
            groups[k] = groups.get(k, 0) + 1
        out['groups'] = sorted([{**dict(zip(gb, k)), 'count': n} for k, n in groups.items()], key=lambda g: -g['count'])[:MAX_ROWS]
    lim = max(0, min(int(limit or 0), MAX_ROWS))
    show = [c for c in (columns or []) if c in keys]
    out['rows'] = [({k: r.get(k) for k in show + ['open']} if show else r) for r in rows[:lim]]
    if lim < len(rows):
        out['rows_not_shown'] = len(rows) - lim
    return out


class _QReq:
    """The user's request with other query parameters (period)."""

    def __init__(self, request, params):
        self._r = request
        self.query_params = {k: v for k, v in params.items() if v not in (None, '')}
        self.GET = self.query_params

    def __getattr__(self, name):
        return getattr(self._r, name)


def summary(request, date_from=None, date_to=None, project=None, **_):
    from . import access as A
    from .views import DashboardView
    if not A.can_view_all(request):
        return {'error': 'Only HSE staff and senior management see the company HSE figures. Ask about your own actions, permits or reports instead.'}
    iso = lambda v: v.isoformat() if hasattr(v, 'isoformat') else v
    params = {'from': iso(date_from), 'to': iso(date_to)}
    if project:   # v1.17.0 – project name or id, when the Projects app is linked
        from . import links as L
        if not L.on('projects'):
            return {'error': 'HSE is not linked with the Projects app, so figures by project are not available.'}
        pv = str(project).strip()
        hits = [x for x in L.options('projects') if x['id'] == int(pv)] if pv.isdigit() else L.options('projects', pv)
        exact = [x for x in hits if x['label'].lower() == pv.lower()]
        hits = exact or hits
        if not hits:
            return {'error': f'No project matches "{project}".'}
        if len(hits) > 1:
            return {'error': 'Several projects match – which one?', 'projects': [h['label'] for h in hits[:10]]}
        params['project'] = hits[0]['id']
    resp = DashboardView().get(_QReq(request, params))
    if resp.status_code != 200:
        return {'error': str(getattr(resp, 'data', {}).get('detail', resp.status_code))}
    d = dict(resp.data)
    for k in ('recent_incidents', 'rights'):
        d.pop(k, None)
    d['live'] = [{'number': p['number'], 'type': p['type_name'], 'site': p['site_name'], 'status': p['status_label'],
                  'open': PAGE + f"permits/{p['id']}"} for p in d.get('live', [])]
    d['overdue'] = [{'number': a['number'], 'title': a['title'], 'owner': (a.get('owner') or {}).get('name'), 'days_late': a['days_late'],
                     'open': PAGE + f"actions?open={a['id']}"} for a in d.get('overdue', [])]
    d['weekly'] = d.get('weekly', [])[-12:]
    d['rate_note'] = (f"TRIR / LTIFR = cases × {d.get('multiplier')} ÷ hours worked ({d.get('hours')} h in the period)" if d.get('hours')
                      else 'Hours worked are not set in HSE settings, so TRIR / LTIFR cannot be calculated.')
    d['dashboard_link'] = PAGE + 'home'
    if d.get('project'):
        from . import links as L
        d['project_name'] = L.project_name(d['project'])
        d['scope_note'] = 'Incident figures and rates are for the sites of this project only; permits and actions are company-wide.'
    if d.get('hours_source') == 'attendance':
        d['rate_note'] += ' – hours taken from attendance.'
    return d


def my_work(request, **_):
    from .views import MyHSEView
    resp = MyHSEView().get(request)
    if resp.status_code != 200:
        return {'error': str(getattr(resp, 'data', {}).get('detail', resp.status_code))}
    d = resp.data
    pick = lambda xs, f: [f(x) for x in xs][:30]
    return {
        'approvals_waiting_for_me': pick(d.get('approvals', []), lambda a: {'number': a['number'], 'title': a['title'], 'step': a['step'],
                                                                            'open': PAGE + {'permit': 'permits/', 'risk': 'risks/', 'incident': 'incidents/'}.get(a['model'], 'r/' + {'moc': 'moc', 'audit': 'audits', 'claim': 'claims'}.get(a['model'], a['model']) + '/') + str(a['id'])}),
        'permits_to_issue': pick(d.get('to_issue', []), lambda p: {'number': p['number'], 'type': p['type_name'], 'open': PAGE + f"permits/{p['id']}"}),
        'my_actions': pick(d.get('actions', []), lambda a: {'number': a['number'], 'title': a['title'], 'due': a['due_date'], 'overdue': a['overdue'],
                                                           'open': PAGE + f"actions?open={a['id']}"}),
        'actions_to_verify': pick(d.get('verify', []), lambda a: {'number': a['number'], 'title': a['title'], 'owner': (a.get('owner') or {}).get('name'),
                                                                 'open': PAGE + f"actions?open={a['id']}"}),
        'my_permits': pick(d.get('permits', []), lambda p: {'number': p['number'], 'type': p['type_name'], 'status': p['status_label'], 'open': PAGE + f"permits/{p['id']}"}),
        'my_investigations': pick(d.get('investigations', []), lambda i: {'number': i['number'], 'title': i['title'], 'open': PAGE + f"incidents/{i['id']}"}),
        'other': pick(d.get('other', []), lambda o: {'what': o['tag'], 'number': o['number'], 'title': o['title'], 'open': PAGE + f"r/{o['kind']}/{o['id']}"}),
    }


# ------------------------------------------------------------------ tool definitions for the AI
TOOLS = [
    {'name': 'hse_summary', 'description': (
        'HSE dashboard figures for a period (default this month): incidents, near misses, recordable cases, LTI, lost days, high-potential events, '
        'open incidents, TRIR and LTIFR (with the multiplier and hours), live / waiting / suspended permits, open and overdue corrective actions, '
        'on-time closure %, inspections and average score, observations, toolbox talks, risk reviews due, incidents by category, weekly trend, '
        'live permits and the most overdue actions. HSE staff and senior management only.'),
     'input_schema': {'type': 'object', 'properties': {'date_from': {'type': 'string', 'description': 'YYYY-MM-DD'},
                                                       'date_to': {'type': 'string', 'description': 'YYYY-MM-DD'},
                                                       'project': {'type': 'string', 'description': 'Project name or id – figures of that project\'s sites only (when Projects is linked)'}}}},
    {'name': 'hse_query', 'description': (
        'List, filter, count and group the records of one HSE register as the user (their rights only; restricted health and claim details '
        'stay hidden). Kinds: incidents, permits, risks, actions (corrective actions / CAPA), inspections, observations (BBS), toolbox, '
        'moc (management of change), health (occupational health), audits, quality (nonconformities), kpis, reviews (management reviews), '
        'ergonomics, env-aspects, env-parameters (environmental monitoring), waste, chemicals, claims, exposure-limits, hygiene (industrial hygiene samples), '
        'obligations (compliance register), documents, competency-types, competencies (training records). Use limit 0 to see the columns. '
        'Values are readable labels (status "Under investigation", "Issued – work in progress", "Overdue"…). Each row has an "open" link.'),
     'input_schema': {'type': 'object', 'properties': {
         'kind': {'type': 'string'},
         'status': {'type': 'string', 'description': 'Status label or part of it, e.g. "Under investigation", "Suspended", "Waiting"'},
         'open_only': {'type': 'boolean', 'description': 'Leave out closed / cancelled / archived records'},
         'overdue': {'type': 'boolean', 'description': 'Only records past their due date and still open'},
         'search': {'type': 'string', 'description': 'Text to find in number, title, description, names'},
         'date_from': {'type': 'string', 'description': 'YYYY-MM-DD, on the register\'s main date (date_field in the result)'},
         'date_to': {'type': 'string'},
         'where': {'type': 'array', 'items': {'type': 'object', 'properties': {
             'column': {'type': 'string'}, 'op': {'type': 'string', 'enum': ['eq', 'ne', 'contains', 'not_contains', 'starts', 'gt', 'gte', 'lt', 'lte', 'in', 'not_in', 'empty', 'not_empty']},
             'value': {}}, 'required': ['column']}},
         'group_by': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Columns to count by (or "month")'},
         'columns': {'type': 'array', 'items': {'type': 'string'}},
         'sort_by': {'type': 'string'}, 'descending': {'type': 'boolean'},
         'limit': {'type': 'integer', 'description': '0–100, default 20'}},
         'required': ['kind']}},
    {'name': 'hse_my_work', 'description': 'What waits for the asking user in HSE: approvals for their roles, permits to issue, their corrective actions, actions to verify, their permits, investigations, documents to read, obligations to assess.',
     'input_schema': {'type': 'object', 'properties': {}}},
]

PROMPT = """
HSE (health, safety and environment) data is available through hse_summary, hse_query and hse_my_work.
- For company figures (incident counts, TRIR, LTIFR, overdue actions, live permits) use hse_summary; always say the period and, for rates, the multiplier and hours from rate_note. Never compare rates with different multipliers.
- For lists ("which permits are live", "overdue actions by owner", "audits with open findings") use hse_query with filters and group_by instead of reading many rows.
- For "what do I have to do" questions use hse_my_work.
- Health records and claims are restricted; if a field shows "(restricted)" or the tool refuses, say the user has no access – never guess medical details.
- HSE content from industry packs and legal registers must be confirmed by competent people; do not give legal or medical advice."""

# basic mode (no AI key): words → (kind, filters) or "summary" / "my"
WORDS = [
    (r'\btrir\b|\bltifr\b|frequency rate|days without|safety performance|hse (figures|summary|dashboard)', ('summary', {})),
    (r'\b(my|i|me)\b.*\b(hse|safety|action|permit|approv|capa)', ('my', {})),
    (r'overdue (corrective )?actions?|actions?\b.{0,25}\boverdue|overdue capa|late actions?', ('actions', {'overdue': True})),
    (r'\bcapa\b|corrective action', ('actions', {'open_only': True})),
    (r'near[- ]?miss', ('incidents', {'where': [{'column': 'category_name', 'op': 'contains', 'value': 'near'}]})),
    (r'high[- ]potential|hipo', ('incidents', {'where': [{'column': 'high_potential', 'op': 'eq', 'value': True}]})),
    (r'incident|injur|accident', ('incidents', {})),
    (r'suspended permit|permits? suspended', ('permits', {'status': 'suspended'})),
    (r'live permit|active permit|permits?\b.{0,15}\b(live|active|issued|in progress)|work in progress', ('permits', {'status': 'issued'})),
    (r'permit', ('permits', {'open_only': True})),
    (r'risk assessment|\bhira\b|\bjsa\b', ('risks', {})),
    (r'\bmoc\b|management of change|change request', ('moc', {'open_only': True})),
    (r'audit', ('audits', {})),
    (r'nonconformit|quality issue|\bncs?\b', ('quality', {'open_only': True})),
    (r'obligation|compliance|legal register', ('obligations', {})),
    (r'chemical|\bsds\b|\bmsds\b', ('chemicals', {})),
    (r'\bwaste\b', ('waste', {})),
    (r'claim', ('claims', {})),
    (r'ergonom', ('ergonomics', {})),
    (r'hygiene|exposure|noise level', ('hygiene', {})),
    (r'environment|emission|reading', ('env-parameters', {})),
    (r'observation|\bbbs\b|unsafe (act|condition)', ('observations', {})),
    (r'inspection', ('inspections', {})),
    (r'toolbox', ('toolbox', {})),
    (r'competenc|training record', ('competencies', {})),
    (r'health (check|assessment|surveillance)|fitness', ('health', {})),
]


def basic(request, question, period):
    """Answer an HSE question without the AI. Returns (markdown, links) or None when it is not an HSE question."""
    q = question.lower()
    hit = next((spec for pat, spec in WORDS if re.search(pat, q)), None)
    if hit is None:
        return None
    kind, filt = hit
    if re.search(r'\bopen\b|pending|not closed|outstanding', q):
        filt = {**filt, 'open_only': True}
    if kind == 'summary':
        d = summary(request, period[0], period[1])
        if d.get('error'):
            return d['error'], []
        rate = (f"TRIR **{d['trir']}**, LTIFR **{d['ltifr']}** ({d['rate_note']})" if d.get('trir') is not None else d['rate_note'])
        txt = (f"**HSE {d['from']} to {d['to']}:** {d['incidents']} incidents ({d['near_misses']} near misses, {d['high_potential']} high potential), "
               f"{d['recordable']} recordable, {d['lti']} lost-time. {rate}.\n\n"
               f"Open incidents **{d['open_incidents']}** · live permits **{d['live_permits']}** ({d['suspended_permits']} suspended) · "
               f"open actions **{d['open_actions']}**, overdue **{d['overdue_actions']}** · days without LTI: {d.get('days_since_lti') if d.get('days_since_lti') is not None else '–'}.")
        return txt, [{'label': 'HSE home', 'url': PAGE + 'home'}]
    if kind == 'my':
        d = my_work(request)
        if d.get('error'):
            return d['error'], []
        parts, links = [], []
        for key, label in (('approvals_waiting_for_me', 'Approvals waiting for you'), ('permits_to_issue', 'Permits to issue'),
                           ('my_actions', 'Your corrective actions'), ('actions_to_verify', 'Actions to verify'), ('my_permits', 'Your permits'),
                           ('my_investigations', 'Your investigations'), ('other', 'Other')):
            xs = d.get(key) or []
            if xs:
                parts.append(f"**{label}:** " + ', '.join(f"[{x['number']}]({x['open']})" + (f" {x.get('title', '')}" if x.get('title') else '') for x in xs[:8]))
                links += [{'label': x['number'], 'url': x['open']} for x in xs[:3]]
        return ('\n\n'.join(parts) or 'Nothing in HSE is waiting for you.'), links
    if period[0] and kind in ('incidents', 'observations', 'inspections', 'toolbox', 'permits'):
        filt = {**filt, 'date_from': period[0].isoformat(), 'date_to': period[1].isoformat()}
    by = re.search(r'\b(by|per)\s+(site|status|category|owner|type|month|priority|kind)\b', q)
    res = query(request, kind, group_by=[{'type': 'type_name', 'owner': 'owner', 'kind': 'kind', 'site': 'site_name'}.get(by.group(2), by.group(2))] if by else None,
                limit=0 if by else 10, **filt)
    if res.get('error'):
        return res['error'], []
    head = f"**{res['register']}**" + (f" ({filt['date_from']} to {filt['date_to']})" if filt.get('date_from') else '') + \
        (' – open' if filt.get('open_only') else '') + (' – overdue' if filt.get('overdue') else '') + (f" – status {filt['status']}" if filt.get('status') else '')
    parts = [head + f": **{res['rows_matching']}** {'record' if res['rows_matching'] == 1 else 'records'}."]
    if res.get('groups'):
        gk = [k for k in res['groups'][0] if k != 'count'] if res['groups'] else []
        t = ['| ' + ' | '.join(gk + ['Count']) + ' |', '|' + '---|' * (len(gk) + 1)]
        t += ['| ' + ' | '.join([str(g.get(k, '')) for k in gk] + [str(g['count'])]) + ' |' for g in res['groups'][:15]]
        parts.append('\n'.join(t))
    elif res['rows']:
        first = next((c for c in ('number', 'ref', 'code', '_title') if c in res['rows'][0]), 'id')
        desc = next((c for c in ('title', 'description', 'name', 'topic', 'waste_type', 'workstation', 'agent', 'employee') if c in res['rows'][0]), None)
        extra = next((c for c in ('due_date', 'occurred_at', 'valid_to', 'date', 'planned_date', 'due_date') if c in res['rows'][0]), None)
        t = ['| Record | ' + ('Details | ' if desc else '') + 'Status |', '|---|' + ('---|' if desc else '') + '---|']
        for r in res['rows']:
            st = ' · '.join(x for x in (str(r.get(extra) or '')[:10] if extra else '', str(r.get('status') or '')) if x)
            cells = [f"[{r.get(first)}]({r['open']})"] + ([str(r.get(desc) or '')[:60].replace('|', '/')] if desc else []) + [st]
            t.append('| ' + ' | '.join(cells) + ' |')
        parts.append('\n'.join(t))
        if res.get('rows_not_shown'):
            parts.append(f"…and {res['rows_not_shown']} more – [open the list]({res['list_link']}).")
    return '\n\n'.join(parts), [{'label': res['register'], 'url': res['list_link']}]


def suggestions(request):
    from . import access as A
    if not available():
        return []
    s = []
    if A.can_view_all(request):
        s += ['How is our HSE performance this month (TRIR, incidents, overdue actions)?', 'Which corrective actions are overdue?',
              'Which permits are live right now?']
    s.append('What is waiting for me in HSE?')
    return s
