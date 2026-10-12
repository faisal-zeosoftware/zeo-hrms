"""
Ask ZEO AI – questions about the company's data in plain language (v1.9.0).

POST /dashboard/api/assistant/   {"messages": [{"role": "user"|"assistant", "content": "..."}], "branch": "1,2"}
  → {"answer": markdown, "links": [{"label", "url"}], "mode": "ai"|"basic"}
GET  /dashboard/api/assistant/   → {"enabled", "mode", "suggestions": [...]}

Safety
* The model never sees the database and never writes SQL. It can only call the tools below, which run the
  report centre (DashboardManagement/reports.py) and the record view (records.py) *as the asking user*:
  the same rights, the same branches, ESS users only their own data.
* Only what the tools return is sent to the AI provider (Anthropic Claude API), never whole tables:
  at most 100 rows per call, long texts cut.
* v1.15.1: by default NO external AI service is ever called. The built-in, free "basic" mode answers from the company's
  own reports on this server. The external provider is used only when BOTH of these are set (owner's explicit choice):
    ZEO_AI_EXTERNAL     true to allow calls to the external AI provider (default false – no paid AI, no data leaves the server)
    ANTHROPIC_API_KEY   key of the company's Anthropic account (ignored unless ZEO_AI_EXTERNAL=true)
* Server settings (environment variables / .env, nothing in settings.py):
    ZEO_AI_ENABLED      0 to switch Ask ZEO AI off
* v1.16.0: the external provider, address, model and key are chosen per company in Settings → AI settings
  (AISettings app; key encrypted). External is used only when ZEO_AI_EXTERNAL=true (server) AND the company chose
  "External AI" with the Ask ZEO switch on AND the monthly call limit is not reached. Every call is logged
  (AISettings.AICallLog); on any error the answer falls back to the built-in mode. ANTHROPIC_API_KEY,
  ZEO_AI_MODEL and ZEO_AI_URL in .env are no longer used.
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.http import QueryDict
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr  # v1.16.0

log = logging.getLogger(__name__)

MAX_ROUNDS = 8          # tool calls per question
MAX_ROWS = 100
RATE = (40, 600)        # questions per user per 10 minutes
M_ = '/main-sidebar/'


def setting(name, default=''):
    from django.conf import settings
    return os.environ.get(name) or getattr(settings, name, None) or default


def enabled():
    return str(setting('ZEO_AI_ENABLED', '1')).lower() not in ('0', 'false', 'no', 'off')


def external_allowed():
    """v1.15.1: the owner must opt in explicitly (ZEO_AI_EXTERNAL=true); an API key alone is not enough."""
    return str(setting('ZEO_AI_EXTERNAL', 'false')).strip().lower() in ('1', 'true', 'yes', 'on')


def ai_ready():
    """v1.16.0: True when Ask ZEO should use the company's external AI now (server switch + Settings → AI settings)."""
    if not external_allowed():
        return False
    try:
        from AISettings.service import feature_mode
        return feature_mode('ask')[0] == 'external'
    except Exception:   # AISettings not installed / not migrated → built-in mode
        log.debug('AI settings not available', exc_info=True)
        return False


# ------------------------------------------------------------------ running the reports as the user
class _Req:
    """The user's request with other query parameters (period, branch) – so rights and branches stay the user's."""

    def __init__(self, request, params):
        self._r = request
        q = request.query_params.copy() if hasattr(request.query_params, 'copy') else QueryDict(mutable=True)
        q._mutable = True
        for k in ('from', 'to', 'branch'):
            q.pop(k, None)
        for k, v in params.items():
            if v not in (None, ''):
                q[k] = str(v)
        self.query_params = q
        self.GET = q

    def __getattr__(self, name):
        return getattr(self._r, name)


def plain(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:16] if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _cmp(a, b):
    """Compare a cell with a value the model gave: numbers as numbers, the rest as lower-case text."""
    a, b = plain(a), plain(b)
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        try:
            return float(a), float(b)
        except (TypeError, ValueError):
            pass
    return str(a if a is not None else '').strip().lower(), str(b if b is not None else '').strip().lower()


def _match(row, cond):
    col, op, val = cond.get('column'), (cond.get('op') or 'eq').lower(), cond.get('value')
    cell = row.get(col)
    if op in ('in', 'not_in'):
        vals = val if isinstance(val, list) else [val]
        hit = any(_cmp(cell, v)[0] == _cmp(cell, v)[1] for v in vals)
        return hit if op == 'in' else not hit
    if op == 'empty':
        return cell in (None, '')
    if op == 'not_empty':
        return cell not in (None, '')
    a, b = _cmp(cell, val)
    try:
        return {'eq': a == b, 'ne': a != b, 'contains': str(b) in str(a), 'not_contains': str(b) not in str(a),
                'gt': a > b, 'gte': a >= b, 'lt': a < b, 'lte': a <= b, 'starts': str(a).startswith(str(b))}.get(op, a == b)
    except TypeError:
        return False


def report_url(key, params, where):
    """Link to the report screen, drilled down where the conditions are simple equalities."""
    from urllib.parse import urlencode
    q = {k: v for k, v in params.items() if k in ('from', 'to') and v}
    for c in where or []:
        if (c.get('op') or 'eq') == 'eq' and c.get('column'):
            q['f_' + c['column']] = str(c.get('value'))
        elif (c.get('op') or '') == 'in' and isinstance(c.get('value'), list):
            q['f_' + c['column']] = '|'.join(str(v) for v in c['value'])
    return f'{M_}report-options/r/{key}' + ('?' + urlencode(q) if q else '')


def record_url(m, i):
    if m == 'EmpManagement.emp_master':
        return f'{M_}sub-sidebar/employee-details/{i}/details'
    if m == 'PayrollManagement.PayrollRun':
        return f'{M_}salary-options/payroll-details/{i}'
    return f'{M_}report-options/rec/{m}/{i}'


# ------------------------------------------------------------------ v1.20.1: the Projects app
PJ = 'pj:'
PJ_NUM = ('number', 'hours', 'money', 'pct', 'minutes')
PJ_TICKETS = ('tickets', 'Support', 'Support tickets – list', 'Every support / helpdesk ticket the user may see: number, subject, customer, priority, '
              'stage, engineer, team, SLA due and breached. Filter with where (e.g. stage, priority, customer, assigned_to, sla_breached).')


def _pj_url(link):
    from urllib.parse import urlencode
    if not isinstance(link, dict) or not link.get('page'):
        return None
    u = f"{M_}zeo-projects/{link['page']}" + (f"/{link['id']}" if link.get('id') else '')
    q = {k: v for k, v in (link.get('query') or {}).items() if v not in (None, '')}
    return u + ('?' + urlencode(q) if q else '')


def pj_catalogue(request):
    """The Projects reports this user may run, as list_reports entries (key pj:<key>)."""
    try:
        from ZeoProjects import access as A
        from ZeoProjects import reports as PR
    except Exception:   # the Projects app is not installed
        return []
    out = []
    try:
        if A.ctx(request) is not None:
            out.append({'key': PJ + PJ_TICKETS[0], 'title': PJ_TICKETS[2], 'section': 'Projects – ' + PJ_TICKETS[1], 'description': PJ_TICKETS[3],
                        'has_period': False, 'default_period': 'none'})
        for r in PR.catalogue(request):
            per = 'period' in r['filters']
            out.append({'key': PJ + r['key'], 'title': r['title'], 'section': 'Projects – ' + r['group'], 'description': r['description'],
                        'has_period': per, 'default_period': 'last 30 days' if per else 'none'})
    except Exception:
        log.exception('assistant: projects catalogue')
    return out


def pj_tickets(request):
    from ZeoProjects import access as A
    from ZeoProjects.models import OPEN_STAGES, Ticket
    from django.utils import timezone
    qs = A.ticket_scope(request, Ticket.objects.all()).select_related('customer', 'assigned_to', 'team', 'product').order_by('-created_at')[:2000]
    stages, prios = dict(Ticket._meta.get_field('stage').choices), dict(Ticket._meta.get_field('priority').choices)
    now = timezone.now()
    rows = []
    for t in qs:
        late = bool(t.resolution_breached or t.response_breached or (t.stage in OPEN_STAGES and t.resolution_due and not t.paused_at and t.resolution_due < now))
        who = ' '.join(x for x in [getattr(t.assigned_to, 'emp_first_name', ''), getattr(t.assigned_to, 'emp_last_name', '')] if x) if t.assigned_to else 'Not assigned'
        rows.append({'number': t.number, 'subject': t.subject, 'customer': t.customer.name if t.customer else 'Internal (helpdesk)',
                     'product': t.product.name if t.product else '', 'priority': prios.get(t.priority, t.priority), 'stage': stages.get(t.stage, t.stage),
                     'open': 'Yes' if t.stage in OPEN_STAGES else 'No', 'assigned_to': who, 'team': t.team.name if t.team else '',
                     'created': timezone.localtime(t.created_at).date() if t.created_at else None,
                     'resolution_due': timezone.localtime(t.resolution_due) if t.resolution_due else None, 'sla_breached': 'Yes' if late else 'No',
                     '_url': f'{M_}zeo-projects/tickets/{t.id}'})
    cols = [('number', 'Ticket'), ('subject', 'Subject'), ('customer', 'Customer'), ('product', 'Product'), ('priority', 'Priority'), ('stage', 'Stage'),
            ('open', 'Open'), ('assigned_to', 'Engineer'), ('team', 'Team'), ('created', 'Created'), ('resolution_due', 'Resolution due'), ('sla_breached', 'SLA breached')]
    return {'title': PJ_TICKETS[2], 'columns': [{'key': k, 'label': l, 'type': 'date' if k in ('created', 'resolution_due') else 'text'} for k, l in cols],
            'rows': rows, 'count': len(rows), 'totals': {}, 'period': None, '_link': f'{M_}zeo-projects/tickets'}


def pj_report(request, key, date_from=None, date_to=None):
    """Run a Projects report as the user, in the shape of the report centre."""
    key = str(key)[len(PJ):]
    try:
        from ZeoProjects import access as A
        from ZeoProjects import reports as PR
        from ZeoProjects import services as S
    except Exception:
        return {'error': 'The Projects app is not installed.'}
    try:
        if key == PJ_TICKETS[0]:
            return pj_tickets(request)
        d = PR.run(_Req(request, {'from': date_from, 'to': date_to}), key)
    except A.Denied as exc:
        return {'error': f'You may not run this report: {exc}'}
    except S.ProjectsError as exc:
        return {'error': str(exc)}
    cols = [{'key': c['key'], 'label': c['label'], 'type': 'number' if c.get('type') in PJ_NUM else 'text'} for c in d.get('columns', [])]
    num = {c['key'] for c in cols if c['type'] == 'number'}
    rows = []
    for r in d.get('rows', []):
        row = {k: v for k, v in r.items() if k != 'link_to' and not isinstance(v, (dict, list))}
        u = _pj_url(r.get('link_to'))
        if u:
            row['_url'] = u
        rows.append(row)
    totals = {k: v for k, v in (d.get('totals') or {}).items() if k in num and isinstance(v, (int, float, Decimal))}
    from urllib.parse import urlencode
    link = f"{M_}zeo-projects/reports/{key}" + ('?' + urlencode({'from': date_from, 'to': date_to}) if date_from and date_to else '')
    per = 'period' in (d.get('filters') or [])
    return {'title': d.get('title', key), 'columns': cols, 'rows': rows, 'count': len(rows), 'totals': totals,
            'period': {'from': d.get('from'), 'to': d.get('to')} if per else None, '_link': link}


class Tools:
    def __init__(self, request, branch):
        self.request = request
        self.branch = branch or ''
        self.links = []

    def _link(self, label, url):
        if url and not any(l['url'] == url for l in self.links):
            self.links.append({'label': label[:90], 'url': url})

    # -------------------------------------------------- tools
    def list_reports(self, **_):
        from AccessControl.access import ctx
        from .reports import REPORTS, _allowed, _apps_hidden
        c = ctx(self.request)
        hidden = _apps_hidden()   # v1.22.0: reports of apps that are not active; pj- keys come from pj_catalogue below
        out = [{'key': k, 'title': v[0], 'section': v[1], 'description': v[2], 'has_period': v[4] is not None,
                'default_period': v[4] or 'none'} for k, v in REPORTS.items() if _allowed(c, v[5]) and k not in hidden and not k.startswith('pj-')]
        out += pj_catalogue(self.request)   # v1.20.1: the Projects app (support tickets, AMC, projects)
        return {'reports': out, 'note': 'Call run_report with limit 0 to see a report\'s columns.' if out else
                'This user may not run any report; use my_summary for the user\'s own data.'}

    def run_report(self, key, date_from=None, date_to=None, where=None, group_by=None, sum=None, columns=None,
                   sort_by=None, descending=False, limit=20, **_):
        if bool(date_from) != bool(date_to):
            return {'error': 'Give both date_from and date_to, or neither (the report then uses its default period).'}
        d = self.fetch(key, date_from, date_to)
        if d.get('error'):
            return d
        cols = d['columns']
        keys = {c['key'] for c in cols}
        bad = [c.get('column') for c in (where or []) if c.get('column') not in keys and c.get('column') not in ('cycle_name', 'held', 'open', 'starting', 'checked_in', 'reviewed', 'waiting')]
        if bad:
            return {'error': f'Unknown column(s) {bad}. Columns: {[c["key"] for c in cols]}'}
        rows = [r for r in d['rows'] if all(_match(r, c) for c in (where or []))]
        if sort_by in keys:
            rows.sort(key=lambda r: (r.get(sort_by) in (None, ''), _cmp(r.get(sort_by), 0)[0]), reverse=bool(descending))
            if descending:   # keep empty values last
                rows.sort(key=lambda r: r.get(sort_by) in (None, ''))
        num = [c['key'] for c in cols if c['type'] == 'number']
        sums = [s for s in (sum or []) if s in num] or [k for k in num if k in d.get('totals', {})]
        out = {'report': d['title'], 'key': key, 'period': {k: plain(v) for k, v in (d.get('period') or {}).items()} or 'no period (all records)',
               'columns': [{'key': c['key'], 'label': c['label'], 'type': c['type']} for c in cols],
               'rows_matching': len(rows), 'rows_in_report': d['count'],
               'totals': {k: round(_sum(r.get(k) for r in rows), 2) for k in sums}}
        if group_by:
            gb = [g for g in (group_by if isinstance(group_by, list) else [group_by]) if g in keys or g in ('month',)]
            groups = {}
            for r in rows:
                gk = tuple(str(plain(r.get(g)) or '(empty)') for g in gb)
                g = groups.setdefault(gk, {'count': 0, **{s: 0.0 for s in sums}})
                g['count'] += 1
                for s in sums:
                    v = r.get(s)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        g[s] += v
            out['groups'] = sorted([{**dict(zip(gb, k)), **{x: (round(y, 2) if isinstance(y, float) else y) for x, y in v.items()}} for k, v in groups.items()],
                                   key=lambda g: -g['count'])[:MAX_ROWS]
        lim = max(0, min(int(limit or 0), MAX_ROWS))
        show = [c for c in (columns or []) if c in keys] or [c['key'] for c in cols]
        out['rows'] = []
        for r in rows[:lim]:
            item = {k: (plain(r.get(k))[:80] if isinstance(plain(r.get(k)), str) else plain(r.get(k))) for k in show}
            if r.get('_url'):
                item['open'] = r['_url']
            elif r.get('_m'):
                item['open'] = record_url(r['_m'], r['_id'])
            elif r.get('_emp'):
                item['open'] = record_url('EmpManagement.emp_master', r['_emp'])
            out['rows'].append(item)
        if lim < len(rows):
            out['rows_not_shown'] = len(rows) - lim
        url = d.get('_link') or report_url(key, {'from': plain(d['period']['from']) if d.get('period') else None, 'to': plain(d['period']['to']) if d.get('period') else None}, where)
        out['report_link'] = url
        ops = {'eq': '=', 'ne': '≠', 'gt': '>', 'gte': '≥', 'lt': '<', 'lte': '≤', 'in': '=', 'not_in': '≠', 'contains': 'contains', 'not_contains': 'does not contain',
               'starts': 'starts with', 'empty': 'is empty', 'not_empty': 'is set'}
        lab = {c['key']: c['label'] for c in cols}
        val = lambda v: ' or '.join(map(str, v)) if isinstance(v, list) else ('' if v is None else str(v))
        label = d['title'] + (' – ' + ', '.join(f"{lab.get(c.get('column'), c.get('column'))} {ops.get(c.get('op') or 'eq', '=')} {val(c.get('value'))}".strip() for c in where) if where else '') \
            + f" ({len(rows)} {'row' if len(rows) == 1 else 'rows'})"
        self._link(label, url)
        return out

    def fetch(self, key, date_from=None, date_to=None):
        """The report's data as the user: {title, columns, rows, count, totals, period} or {'error'}."""
        if str(key).startswith(PJ):
            return pj_report(self.request, key, date_from, date_to)
        from .reports import ReportView
        resp = ReportView().get(_Req(self.request, {'from': date_from, 'to': date_to, 'branch': self.branch}), key)
        if resp.status_code != 200:
            return {'error': str(resp.data.get('detail') if hasattr(resp, 'data') else resp.status_code)}
        return resp.data

    def get_record(self, model, id, **_):
        from .records import RecordView
        r = RecordView().get(self.request, model, id)
        if r.status_code != 200:
            return {'error': str(r.data.get('detail'))}
        d = r.data
        url = d.get('page') or record_url(d['model'], d['id'])
        self._link(f"{d['kind']}: {d['title']}", url)
        return {'kind': d['kind'], 'title': d['title'], 'open': url, 'employee': d.get('employee'),
                'fields': [{'label': f['label'], 'value': f['value']} for f in d['fields'] if f['value'] != ''],
                'lines': [{'title': c['title'], 'columns': c['columns'], 'rows': [[x['value'] for x in row['cells']] for row in c['rows'][:30]]} for c in d['children']]}

    def my_summary(self, **_):
        from .services import ess_summary
        d = ess_summary(self.request.user)
        if not d.get('employee'):
            return {'error': 'This user is not linked to an employee.'}
        keep = ('employee', 'leave_balances', 'next_leave', 'attendance', 'payslip', 'loans', 'air_ticket', 'assets', 'documents', 'performance', 'training')
        out = {k: d.get(k) for k in keep}
        out['requests'] = (d.get('requests') or {}).get('recent', [])[:8]
        self._link('My dashboard', f'{M_}my-dashboard')
        return json.loads(json.dumps(out, default=str))

    # -------------------------------------------------- v1.21.0 HSE app (add-on 1.17.0 merged) – ZeoHSE/ask.py, run as the user
    def _hse_links(self, out):
        if isinstance(out, dict):
            for r in (out.get('rows') or [])[:5]:
                if r.get('open'):
                    self._link(str(r.get('number') or r.get('ref') or r.get('code') or r.get('id')), r['open'])
            for k in ('list_link', 'dashboard_link'):
                if out.get(k):
                    self._link(out.get('register') or 'HSE home', out[k])
        return out

    def hse_summary(self, **kw):
        from ZeoHSE import ask
        return self._hse_links(ask.summary(self.request, **kw))

    def hse_query(self, **kw):
        from ZeoHSE import ask
        return self._hse_links(ask.query(self.request, **kw))

    def hse_my_work(self, **kw):
        from ZeoHSE import ask
        return ask.my_work(self.request)

    def run(self, name, args):
        fn = {'list_reports': self.list_reports, 'run_report': self.run_report, 'get_record': self.get_record, 'my_summary': self.my_summary,
              'hse_summary': self.hse_summary, 'hse_query': self.hse_query, 'hse_my_work': self.hse_my_work}.get(name)
        if fn is None:
            return {'error': f'Unknown tool {name}'}
        try:
            return fn(**(args or {}))
        except TypeError as exc:
            return {'error': f'Bad arguments: {exc}'}
        except Exception as exc:   # never break the conversation
            log.exception('assistant tool %s failed', name)
            return {'error': f'The tool failed: {exc.__class__.__name__}'}


def _sum(vals):
    t = 0.0
    for v in vals:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            t += v
    return t


TOOLS = [
    {'name': 'list_reports', 'description': 'List the reports this user may run (key, title, section, description, default period). Call this first when unsure which report has the data.',
     'input_schema': {'type': 'object', 'properties': {}}},
    {'name': 'run_report', 'description': (
        'Run one report as the user (only their branches and rights). Filter rows with "where", group and add up with "group_by" / "sum", '
        'and get up to 100 rows. Every report has the columns employee ("Full name (code)"), branch, department, designation, category unless it is about '
        'something else (assets, jobs, departments). Text matching is case-insensitive. Values are readable (e.g. status "Approved", "Pending", "Expired"). '
        'Dates are YYYY-MM-DD. Use limit 0 to only see the columns and totals. Reports with a period need date_from and date_to, else they use their default '
        '(this month or this year). The result has report_link and, per row, open: links to show the user.'),
     'input_schema': {'type': 'object', 'properties': {
         'key': {'type': 'string', 'description': 'Report key from list_reports'},
         'date_from': {'type': 'string', 'description': 'YYYY-MM-DD'},
         'date_to': {'type': 'string', 'description': 'YYYY-MM-DD'},
         'where': {'type': 'array', 'items': {'type': 'object', 'properties': {
             'column': {'type': 'string'}, 'op': {'type': 'string', 'enum': ['eq', 'ne', 'contains', 'not_contains', 'starts', 'gt', 'gte', 'lt', 'lte', 'in', 'not_in', 'empty', 'not_empty']},
             'value': {}}, 'required': ['column']}},
         'group_by': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Columns to group by; each group gets a count and the sums'},
         'sum': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Number columns to add up (default: the report totals)'},
         'columns': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Columns to return in rows (default all)'},
         'sort_by': {'type': 'string'}, 'descending': {'type': 'boolean'},
         'limit': {'type': 'integer', 'description': 'Rows to return, 0–100, default 20'}},
         'required': ['key']}},
    {'name': 'get_record', 'description': 'Open one record (from a row\'s open link /report-options/rec/<model>/<id> or an employee id with model EmpManagement.emp_master): all its fields and lines.',
     'input_schema': {'type': 'object', 'properties': {'model': {'type': 'string'}, 'id': {'type': 'integer'}}, 'required': ['model', 'id']}},
    {'name': 'my_summary', 'description': 'The asking user\'s own employee data: leave balances, next leave, attendance this month, last payslip, loans, air ticket, assets, documents, requests, appraisal, training.',
     'input_schema': {'type': 'object', 'properties': {}}},
]


def hse_on():
    """v1.21.0: HSE questions only when the HSE app is active in the company (Settings → Apps)."""
    try:
        from ZeoHSE import ask
        return ask.available()
    except Exception:
        return False


def hse_prompt():
    if hse_on():
        from ZeoHSE import ask
        return ask.PROMPT
    return ''


def all_tools():
    if hse_on():
        from ZeoHSE import ask
        return TOOLS + ask.TOOLS
    return TOOLS


KIT_SUITES = {'crm': 'CRM', 'property': 'Property', 'maintenance': 'Maintenance'}


def system_prompt(request, suite=''):
    from AccessControl.access import ctx
    c = ctx(request)
    today = date.today()
    who = getattr(request.user, 'username', 'user')
    emp = c.emp
    name = ' '.join(x for x in [getattr(emp, 'emp_first_name', ''), getattr(emp, 'emp_last_name', '')] if x) if emp else ''
    scope = 'company admin (all branches)' if c.admin else ('HR / back office for branches ' + ', '.join(map(str, c.branches or [])) if c.backoffice else 'employee (self service – own data only)')
    app = {'projects': 'the Projects app (support tickets, customers and AMC contracts, project delivery)', 'settings': 'the Settings app',
           'hse': 'the HSE app (risk, permits to work, incidents, inspections, corrective actions)', 'crm': 'the CRM app',
           'property': 'the Property app', 'maintenance': 'the Maintenance app'}.get(suite, 'the HRMS app')
    return f"""You are "Ask ZEO AI", the assistant inside ZEO, an HR, payroll and projects system used by companies in the UAE.
Today is {today:%A %d %B %Y} ({today.isoformat()}). The user is {who}{' – ' + name if name else ''}, {scope}. They are working in {app}.

How to answer
- Answer only from what the tools return. Never guess or invent names, numbers or dates. If the tools have no data for the question, say so and say which report or screen would have it.
- Pick the report with list_reports when unsure, then run_report with filters, grouping and sums instead of reading many rows. Check spelling of values (department, leave type, status) against the rows when a filter returns nothing.
- Money is AED. Show amounts with thousands separators. Say which period the numbers cover.
- Keep answers short: one or two sentences with the result, then a small markdown table when there are several rows (at most 15 rows; say how many more there are).
- Link every person or record you mention with the "open" link from the tool result, as a markdown link [text](link). Only use links the tools gave you. Mention the report_link for "see all".
- The user can only see their own branches and rights; if a tool returns an error about rights, tell them they do not have access.
- Answer in the user's language (English or Arabic).
- Reports whose key starts with "pj:" are the Projects app: support / helpdesk tickets (pj:tickets lists them one by one), SLA, AMC contracts, renewals,
  projects, milestones, tasks, work logs and utilisation. In the Projects app "ticket" means a support ticket; air tickets are an HR benefit.
- v1.22.0: reports whose key starts with "crm-" are the CRM app (contacts, opportunities, pipeline by stage, won/lost by agent, lead sources),
  "prop-" the Property app (buildings, units, leases, rent roll, occupancy, billing, cheques, receipts, aged receivables, commissions) and
  "mnt-" the Maintenance app (work orders, SLA by category, technician workload, preventive plans, assets, vendors). Use them for questions in those apps.
- Do not give legal, medical or financial advice; for labour-law calculations say the figures come from the report and should be checked by HR.""" + hse_prompt()


def call_claude(system, messages, tools, request=None):
    # v1.15.1: hard stop – never contact an outside service unless the owner switched it on (ZEO_AI_EXTERNAL=true).
    if not external_allowed():
        raise RuntimeError('External AI is switched off on this server.')
    # v1.16.0: the company's provider / key / limit (Settings → AI settings); logged; raises RuntimeError on any problem
    from AISettings.service import chat
    return chat(request, 'ask', system, messages, tools, 2000)


def ask_ai(request, history, branch, suite=''):
    tools = Tools(request, branch)
    system = system_prompt(request, suite)
    msgs = [{'role': m['role'], 'content': m['content']} for m in history]
    for _ in range(MAX_ROUNDS):
        r = call_claude(system, msgs, all_tools(), request)
        blocks = r.get('content') or []
        uses = [b for b in blocks if b.get('type') == 'tool_use']
        if r.get('stop_reason') != 'tool_use' or not uses:
            text = '\n'.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
            return text or 'I could not find an answer.', tools.links
        msgs.append({'role': 'assistant', 'content': blocks})
        results = []
        for u in uses:
            out = tools.run(u.get('name'), u.get('input') or {})
            results.append({'type': 'tool_result', 'tool_use_id': u['id'], 'content': json.dumps(out, default=str)[:60000],
                            **({'is_error': True} if isinstance(out, dict) and out.get('error') else {})})
        msgs.append({'role': 'user', 'content': results})
    return 'This question needed too many steps. Try asking it in smaller parts.', tools.links


# ------------------------------------------------------------------ basic mode (no AI key)
PERIODS = [
    (r'\btoday\b', lambda t: (t, t)),
    (r'\bthis week\b', lambda t: (t - timedelta(days=t.weekday()), t - timedelta(days=t.weekday()) + timedelta(days=6))),
    (r'\blast month\b', lambda t: ((t.replace(day=1) - timedelta(days=1)).replace(day=1), t.replace(day=1) - timedelta(days=1))),
    (r'\bnext month\b', lambda t: ((t.replace(day=28) + timedelta(days=4)).replace(day=1), ((t.replace(day=28) + timedelta(days=4)).replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))),
    (r'\bthis month\b', lambda t: (t.replace(day=1), (t.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1))),
    (r'\blast year\b', lambda t: (date(t.year - 1, 1, 1), date(t.year - 1, 12, 31))),
    (r'\bthis year\b', lambda t: (date(t.year, 1, 1), date(t.year, 12, 31))),
]
WORDS = {   # words → report key (checked in order)
    'wps': 'wps', 'sif': 'wps', 'gratuity': 'gratuity', 'liability': 'leave-liability', 'loan': 'loans', 'advance': 'advances',
    'air ticket': 'air-tickets', 'ticket': 'air-tickets', 'payslip': 'payroll-register', 'net pay': 'payroll-register', 'payroll': 'payroll-register',
    'salary revision': 'salary-revisions', 'increment': 'salary-revisions', 'salary': 'salary', 'overtime': 'overtime', 'late': 'late-early', 'early': 'late-early',
    'absent': 'attendance-summary', 'attendance': 'attendance-summary', 'present': 'attendance-summary', 'balance': 'leave-balance',
    'approval': 'leave-approvals', 'leave': 'leave', 'vacation': 'leave', 'document': 'documents', 'passport': 'documents', 'visa': 'documents',
    'emirates id': 'documents', 'expir': 'documents', 'probation': 'probation', 'confirmation': 'probation', 'birthday': 'birthdays', 'anniversar': 'birthdays',
    'joiner': 'joiners-leavers', 'joined': 'joiners-leavers', 'leaver': 'joiners-leavers', 'resign': 'exits', 'end of service': 'exits', 'headcount': 'headcount',
    'turnover': 'headcount', 'asset': 'assets', 'laptop': 'assets', 'request': 'general-requests', 'appraisal': 'appraisals', 'rating': 'appraisals',
    'recruit': 'recruitment', 'job': 'recruitment', 'vacanc': 'recruitment', 'training': 'training', 'course': 'training', 'certificate': 'certificates',
    'timesheet': 'timesheets', 'project': 'timesheets', 'department': 'departments', 'designation': 'designations', 'employee': 'employees', 'staff': 'employees',
}


# v1.20.1: Projects app words. STRONG ones always mean Projects; the others only in the Projects app (or when no HR word matches).
PJ_STRONG = [
    (r'expir\w*.*\b(contract|amc)|\b(contract|amc)\w*.*expir|renew', 'pj:amc-expiring'), (r'\bsla\b|breach', 'pj:sla-breaches'),
    (r'\bamc\b|maintenance contract', 'pj:amc-profit'), (r'milestone', 'pj:milestones-overdue'), (r'work ?logs?\b', 'pj:worklogs-pending'),
    (r'utili[sz]ation|workload', 'pj:utilisation'), (r'support ticket|helpdesk|help desk', 'pj:tickets'), (r'response time|resolution time', 'pj:response-times'),
    (r'reopen|satisfaction|csat', 'pj:reopened-csat'), (r'received.*resolved|resolved.*received|backlog', 'pj:received-resolved'),
]
PJ_WEAK = [(r'\btickets?\b', 'pj:tickets'), (r'\btasks?\b', 'pj:tasks-by-employee'), (r'\bprojects?\b', 'pj:project-status'),
           (r'\bcontracts?\b', 'pj:amc-profit'), (r'\bcustomers?\b', 'pj:tickets')]


# v1.22.0: CRM / Property / Maintenance words – used in those apps (and anywhere for the unmistakable ones)
KIT_WORDS = {
    'crm': [(r'pipeline|stage', 'crm-pipeline'), (r'\bagents?\b|won|lost|win rate', 'crm-agents'), (r'source|campaign|channel', 'crm-sources'),
            (r'listing', 'crm-listing'), (r'opportunit|deal|lead', 'crm-opportunity'), (r'contact|client|customer', 'crm-contact')],
    'property': [(r'occupan|vacan|empty unit', 'prop-occupancy'), (r'rent roll|\brent\b', 'prop-rent-roll'), (r'aged|overdue|outstanding|arrears|receivable', 'prop-aged'),
                 (r'cheque|bill|invoice|due', 'prop-bill'), (r'receipt|paid|collection', 'prop-receipt'), (r'commission', 'prop-commission'),
                 (r'reservation|booking', 'prop-reservation'), (r'sale', 'prop-sale'), (r'lease|tenancy|renewal|expir', 'prop-lease'),
                 (r'tenant|buyer', 'prop-party'), (r'owner|landlord', 'prop-owner'), (r'building|unit', 'prop-occupancy')],
    'maintenance': [(r'\bsla\b|breach|category', 'mnt-sla'), (r'technician|workload', 'mnt-technicians'), (r'preventive|\bplans?\b|\bppm\b', 'mnt-plan'),
                    (r'inspection', 'mnt-inspection'), (r'vendor|contractor|supplier', 'mnt-vendor'), (r'asset|equipment', 'mnt-asset'),
                    (r'location', 'mnt-location'), (r'work ?orders?|job|repair|request|open|overdue', 'mnt-work-order')],
}
KIT_STRONG = [(r'rent roll', 'prop-rent-roll'), (r'occupancy', 'prop-occupancy'), (r'work ?orders?', 'mnt-work-order'), (r'sales pipeline|opportunit', 'crm-pipeline')]


def pick_report(q, suite):
    if suite in KIT_WORDS:
        k = next((k for pat, k in KIT_WORDS[suite] if re.search(pat, q)), None)
        if k:
            return k
    kit = next((k for pat, k in KIT_STRONG if re.search(pat, q)), None)
    if kit:
        return kit
    strong = next((k for pat, k in PJ_STRONG if re.search(pat, q)), None)
    if strong:
        return strong
    weak = next((k for pat, k in PJ_WEAK if re.search(pat, q)), None)
    if suite == 'projects' and weak:
        return weak
    hr = next((k for w, k in WORDS.items() if w in q), None)
    return hr or weak


def ask_basic(request, question, branch, suite=''):
    """Without an AI key: find the report by its words, the period and values named in the question; give the count and totals."""
    from .reports import REPORTS
    t = date.today()
    q = question.lower()
    key = pick_report(q, suite)
    pj = bool(key and key.startswith(PJ))
    hrms_off = False
    if key and not pj:   # v1.22.0: reports of apps that are switched off (HRMS too) are not used
        from .reports import _apps_hidden
        if key in _apps_hidden():
            key = None
    try:
        from ZeoApps.services import is_active
        hrms_off = not is_active('hrms')
    except Exception:
        pass
    if not pj and not hrms_off and re.search(r'\b(my|i|me)\b', q) and re.search(r'leave|balance|payslip|salary|document|asset|loan', q):
        tools = Tools(request, branch)
        d = tools.my_summary()
        if d.get('error'):
            return d['error'], []
        bal = ', '.join(f"{b['name']} {b['balance']:g}" for b in d.get('leave_balances') or []) or 'none'
        ps = d.get('payslip')
        return (f"**Your leave balance:** {bal}.\n\n" + (f"**Last payslip:** {ps['period']} – net AED {float(ps['net']):,.0f}.\n\n" if ps else '')
                + f"Documents: {len(d.get('documents') or [])} · assets with you: {len(d.get('assets') or [])}."), tools.links
    if not pj and hse_on() and (suite == 'hse' or not key):   # v1.21.0 HSE questions (incidents, permits, actions, audits…)
        from ZeoHSE import ask
        hp = next(((a, b) for pat, f in PERIODS if re.search(pat, q) for a, b in [f(t)]), (None, None))
        got = ask.basic(request, question, hp)
        if got is not None:
            return got
    if pj and key not in {r['key'] for r in pj_catalogue(request)}:
        return ('You do not have access to that part of Projects. Ask your administrator for the Projects rights, '
                'or ask about your own tickets, for example *Show my tickets*.'), []
    if key is None or (key not in REPORTS and not pj):
        if suite in KIT_SUITES:
            return ({'crm': 'I can answer questions about contacts, opportunities, the sales pipeline by stage, won and lost deals by agent and lead sources. '
                            'Try for example: *Pipeline by stage this month*.',
                     'property': 'I can answer questions about occupancy, the rent roll, leases and renewals, bills and cheques, receipts, aged receivables, '
                                 'reservations, sales and commissions. Try for example: *Occupancy by building*.',
                     'maintenance': 'I can answer questions about work orders, SLA by category, technician workload, preventive plans, inspections, '
                                    'assets and vendors. Try for example: *Open work orders by priority*.'}[suite]), []
        if suite == 'hse':
            return ('I can answer questions about incidents, near misses, permits to work, risk assessments, inspections, observations, '
                    'corrective actions, audits and the other HSE registers. Try for example: *How many open corrective actions are overdue?*'), []
        if suite == 'projects':
            return ('I can answer questions about support tickets, SLA breaches, AMC contracts and renewals, projects, milestones, tasks, work logs '
                    'and utilisation. Try for example: *Which tickets are open by priority?*'), []
        if hrms_off:
            return ('HRMS is not active in this company, so I cannot answer questions about employees, leave or payroll. '
                    'Open one of your apps and ask about its records.'), []
        return ('I can answer questions about employees, leave, attendance, payroll, loans, documents, assets, requests, exits, appraisals, '
                'recruitment, training, timesheets and – in the Projects app – support tickets, AMC contracts and projects. '
                'Try for example: *How many leave requests are pending?*'), []
    tools = Tools(request, branch)
    period = next(((a, b) for pat, f in PERIODS if re.search(pat, q) for a, b in [f(t)]), (None, None))
    probe = tools.run_report(key, date_from=period[0], date_to=period[1], limit=0)
    if probe.get('error'):
        if 'may not' in probe['error']:
            return ('You do not have access to that report. You can ask about your own data, for example *What is my leave balance?*'), []
        return probe['error'], []
    # values named in the question (status, department, leave type, employee …)
    d = tools.fetch(key, period[0], period[1])
    where = []
    for col in [c['key'] for c in d['columns'] if c['type'] == 'text' and c['key'] not in ('subject', 'open', 'sla_breached', 'description')]:
        vals = {str(r.get(col)).strip() for r in d['rows'] if r.get(col) not in (None, '')}
        if len(vals) > 400:
            continue
        hits = [v for v in vals if len(v) > 2 and re.search(r'(?<![\w])' + re.escape(v.lower().split(' (')[0]) + r'(?![\w])', q)]
        if hits:
            where.append({'column': col, 'op': 'in', 'value': sorted(hits, key=len, reverse=True)[:5]})
    if re.search(r'pending|waiting', q) and not any(w['column'] == 'status' for w in where) and 'status' in {c['key'] for c in d['columns']}:
        where.append({'column': 'status', 'op': 'eq', 'value': 'Pending'})
    if re.search(r'expired', q) and key == 'documents':
        where = [w for w in where if w['column'] != 'status'] + [{'column': 'status', 'op': 'eq', 'value': 'Expired'}]
    elif re.search(r'expir', q) and key == 'documents':
        where = [w for w in where if w['column'] != 'status'] + [{'column': 'status', 'op': 'in', 'value': ['Expiring in 30 days', 'Expiring in 90 days']}]
    if key == PJ + 'tickets':
        if re.search(r'\bopen\b|pending|unresolved|not (yet )?(resolved|closed)', q):
            where.append({'column': 'open', 'op': 'eq', 'value': 'Yes'})
        from ZeoProjects import access as PA
        if re.search(r'\b(my|me|i)\b', q) and PA.can(request, 'ticket_all'):   # others only see their own tickets anyway
            from AccessControl.access import ctx
            e = ctx(request).emp
            if e is not None:
                where.append({'column': 'assigned_to', 'op': 'eq', 'value': ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)})
    labels0 = {c['key']: c['label'].lower() for c in d['columns']}
    gb = [k for k, l in labels0.items() if re.search(r'\b(by|per)\s+(' + re.escape(l.split(' (')[0]) + '|' + re.escape(k.replace('_', ' ')) + r')\b', q)]
    tools.links = []   # only the answer's own link
    res = tools.run_report(key, date_from=period[0], date_to=period[1], where=where, group_by=gb or None, limit=0 if gb else 10)
    labels_all = {c['key']: c['label'] for c in res['columns']}
    head = f"**{res['report']}**" + (f" ({res['period']['from']} to {res['period']['to']})" if isinstance(res['period'], dict) else '') + \
           (' – ' + ', '.join(f"{labels_all.get(w['column'], w['column'])} = {' / '.join(w['value']) if isinstance(w['value'], list) else w['value']}" for w in where) if where else '')
    labels = {c['key']: c['label'] for c in res['columns']}
    parts = [head + f": **{res['rows_matching']}** {'row' if res['rows_matching'] == 1 else 'rows'}."]
    if res.get('totals'):
        parts.append(' · '.join(f"{labels[k]}: **{v:,.2f}**".replace('.00**', '**') for k, v in res['totals'].items()))
    if res.get('groups'):
        named = [k for k in (res.get('totals') or {}) if re.search(r'\b' + re.escape(labels[k].split(' (')[0].lower()) + r'\b', q)]
        sums = (named or list(res.get('totals') or {}))[:3]
        t = ['| ' + ' | '.join([labels[g] for g in gb] + ['Rows'] + [labels[s] for s in sums]) + ' |', '|' + '---|' * (len(gb) + 1 + len(sums))]
        for g in res['groups'][:15]:
            t.append('| ' + ' | '.join([str(g.get(x, '')) for x in gb] + [str(g['count'])] + [f"{g.get(s, 0):,.2f}".replace('.00', '') for s in sums]) + ' |')
        parts.append('\n'.join(t))
    elif res['rows']:
        org = {'branch', 'department', 'designation', 'category'}
        cols = [c['key'] for c in res['columns'] if c['key'] not in org][:6]
        t = ['| ' + ' | '.join(labels[c] for c in cols) + ' |', '|' + '---|' * len(cols)]
        for r in res['rows']:
            cells = [(f"{r[c]:,.2f}".replace('.00', '') if isinstance(r.get(c), float) else str(r.get(c, '') if r.get(c) is not None else '')).replace('|', '/') for c in cols]
            if r.get('open'):
                cells[0] = f"[{cells[0]}]({r['open']})"
            t.append('| ' + ' | '.join(cells) + ' |')
        parts.append('\n'.join(t))
        if res.get('rows_not_shown'):
            parts.append(f"…and {res['rows_not_shown']} more – [open the report]({res['report_link']}).")
    return '\n\n'.join(parts), tools.links


# ------------------------------------------------------------------ API
def _throttled(user):
    k = f'zeo-ask:{user.pk}'
    hits = [h for h in (cache.get(k) or []) if h > time.time() - RATE[1]]
    if len(hits) >= RATE[0]:
        return True
    cache.set(k, hits + [time.time()], RATE[1])
    return False


PJ_SUGGEST = [('pj:tickets', 'Which tickets are open, by priority?'), ('pj:sla-breaches', 'Which tickets breached the SLA this month?'),
              ('pj:amc-expiring', 'AMC contracts expiring in the next 90 days'), ('pj:milestones-overdue', 'Which milestones are overdue?'),
              ('pj:tasks-by-employee', 'Pending tasks by employee'), ('pj:worklogs-pending', 'Work logs waiting for approval')]


KIT_SUGGEST = {
    'crm': [('crm-pipeline', 'Sales pipeline by stage'), ('crm-agents', 'Won and lost deals by agent this month'),
            ('crm-sources', 'Which lead sources bring the most deals?'), ('crm-opportunity', 'Open opportunities by stage')],
    'property': [('prop-occupancy', 'Occupancy by building'), ('prop-rent-roll', 'Rent roll this month'), ('prop-aged', 'Aged receivables by tenant'),
                 ('prop-lease', 'Leases expiring in the next 90 days'), ('prop-bill', 'Bills due this month')],
    'maintenance': [('mnt-work-order', 'Open work orders by priority'), ('mnt-sla', 'Work orders by category and SLA this month'),
                    ('mnt-technicians', 'Technician workload'), ('mnt-plan', 'Preventive plans due this month')],
}


def suggestions(request, suite=''):
    from AccessControl.access import ctx
    from .reports import REPORTS, _allowed
    c = ctx(request)
    if suite == 'projects':
        pjk = {r['key'] for r in pj_catalogue(request)}
        return [t for k, t in PJ_SUGGEST if k in pjk][:6]
    if suite == 'hse':   # v1.21.0
        if not hse_on():
            return []
        from ZeoHSE import ask
        return ask.suggestions(request)[:6]
    if suite in KIT_SUITES:   # v1.22.0
        from .reports import _apps_hidden
        hidden = _apps_hidden()
        can = {k for k, v in REPORTS.items() if _allowed(c, v[5]) and k not in hidden}
        return [t for k, t in KIT_SUGGEST.get(suite, []) if k in can][:6]
    from .reports import _apps_hidden
    hidden = _apps_hidden()   # v1.22.0: nothing from a switched-off HRMS
    can = {k for k, v in REPORTS.items() if _allowed(c, v[5]) and k not in hidden}
    s = []
    for key, text in [('documents', 'Whose documents expire in the next 30 days?'), ('leave', 'How many leave requests are pending?'),
                      ('payroll-register', 'Total net pay by department for last month'), ('headcount', 'Headcount by branch and department'),
                      ('attendance-summary', 'Who was absent most this month?'), ('loans', 'Which loans are still outstanding?'),
                      ('gratuity', 'Total gratuity accrued by department'), ('probation', 'Whose probation ends this month?')]:
        if key in can:
            s.append(text)
    if c.emp is not None and 'leave-balance' not in hidden:
        s.append('What is my leave balance?')
    return s[:6]


class AssistantView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        suite = str(request.query_params.get('suite') or '')[:20]
        return Response({'enabled': enabled(), 'mode': 'ai' if ai_ready() else 'basic', 'external': ai_ready(), 'suggestions': suggestions(request, suite) if enabled() else []})

    def post(self, request):
        if not enabled():
            return Response({'detail': 'Ask ZEO is switched off on this server.'}, status=status.HTTP_403_FORBIDDEN)
        msgs = [m for m in (request.data.get('messages') or []) if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and str(m.get('content') or '').strip()]
        msgs = [{'role': m['role'], 'content': str(m['content'])[:4000]} for m in msgs][-12:]
        while msgs and msgs[0]['role'] != 'user':
            msgs.pop(0)
        if not msgs or msgs[-1]['role'] != 'user':
            return Response({'detail': 'Ask a question.'}, status=status.HTTP_400_BAD_REQUEST)
        if _throttled(request.user):
            return Response({'detail': 'You have asked many questions in a short time. Wait a few minutes.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        branch = ','.join(re.findall(r'\d+', str(request.data.get('branch') or '')))
        suite = str(request.data.get('suite') or '')[:20]   # v1.20.1: the app the user is in (hrms / projects / settings)
        started = time.time()
        mode = 'ai' if ai_ready() else 'basic'
        fallback = ''
        try:
            if mode == 'ai':
                try:
                    answer, links = ask_ai(request, msgs, branch, suite)
                except RuntimeError as exc:   # v1.16.0: external AI failed / limit reached → answer in the built-in mode
                    fallback, mode = str(exc), 'basic'
                    answer, links = ask_basic(request, msgs[-1]['content'], branch, suite)
                    answer = f"_{tr('The external AI could not be used ({0}). This answer comes from the built-in mode.', fallback)}_\n\n" + answer
            else:
                answer, links = ask_basic(request, msgs[-1]['content'], branch, suite)
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        # v1.16.0: the question is not logged when it went to an external AI's log already – only its length
        log.info('ask-zeo-ai user=%s mode=%s secs=%.1f chars=%d', request.user.pk, mode, time.time() - started, len(msgs[-1]['content']))
        return Response({'answer': answer, 'links': links[:8], 'mode': mode, **({'fallback': fallback} if fallback else {})})
