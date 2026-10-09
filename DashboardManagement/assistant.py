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
* Server settings (environment variables, nothing in settings.py):
    ANTHROPIC_API_KEY   key of the company's Anthropic account (without it, "basic" mode answers simple questions)
    ZEO_AI_MODEL        model name, default claude-sonnet-5-5
    ZEO_AI_ENABLED      0 to switch Ask ZEO AI off
    ZEO_AI_URL          API address, default https://api.anthropic.com/v1/messages
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


def ai_ready():
    return bool(setting('ANTHROPIC_API_KEY'))


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
        from .reports import REPORTS, _allowed
        c = ctx(self.request)
        out = [{'key': k, 'title': v[0], 'section': v[1], 'description': v[2], 'has_period': v[4] is not None,
                'default_period': v[4] or 'none'} for k, v in REPORTS.items() if _allowed(c, v[5])]
        return {'reports': out, 'note': 'Call run_report with limit 0 to see a report\'s columns.' if out else
                'This user may not run any report; use my_summary for the user\'s own data.'}

    def run_report(self, key, date_from=None, date_to=None, where=None, group_by=None, sum=None, columns=None,
                   sort_by=None, descending=False, limit=20, **_):
        from .reports import ReportView
        params = {'from': date_from, 'to': date_to, 'branch': self.branch}
        if bool(date_from) != bool(date_to):
            return {'error': 'Give both date_from and date_to, or neither (the report then uses its default period).'}
        resp = ReportView().get(_Req(self.request, params), key)
        if resp.status_code != 200:
            return {'error': str(resp.data.get('detail') if hasattr(resp, 'data') else resp.status_code)}
        d = resp.data
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
            if r.get('_m'):
                item['open'] = record_url(r['_m'], r['_id'])
            elif r.get('_emp'):
                item['open'] = record_url('EmpManagement.emp_master', r['_emp'])
            out['rows'].append(item)
        if lim < len(rows):
            out['rows_not_shown'] = len(rows) - lim
        url = report_url(key, {'from': plain(d['period']['from']) if d.get('period') else None, 'to': plain(d['period']['to']) if d.get('period') else None}, where)
        out['report_link'] = url
        ops = {'eq': '=', 'ne': '≠', 'gt': '>', 'gte': '≥', 'lt': '<', 'lte': '≤', 'in': '=', 'not_in': '≠', 'contains': 'contains', 'not_contains': 'does not contain',
               'starts': 'starts with', 'empty': 'is empty', 'not_empty': 'is set'}
        lab = {c['key']: c['label'] for c in cols}
        val = lambda v: ' or '.join(map(str, v)) if isinstance(v, list) else ('' if v is None else str(v))
        label = d['title'] + (' – ' + ', '.join(f"{lab.get(c.get('column'), c.get('column'))} {ops.get(c.get('op') or 'eq', '=')} {val(c.get('value'))}".strip() for c in where) if where else '') \
            + f" ({len(rows)} {'row' if len(rows) == 1 else 'rows'})"
        self._link(label, url)
        return out

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

    def run(self, name, args):
        fn = {'list_reports': self.list_reports, 'run_report': self.run_report, 'get_record': self.get_record, 'my_summary': self.my_summary}.get(name)
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


def system_prompt(request):
    from AccessControl.access import ctx
    c = ctx(request)
    today = date.today()
    who = getattr(request.user, 'username', 'user')
    emp = c.emp
    name = ' '.join(x for x in [getattr(emp, 'emp_first_name', ''), getattr(emp, 'emp_last_name', '')] if x) if emp else ''
    scope = 'company admin (all branches)' if c.admin else ('HR / back office for branches ' + ', '.join(map(str, c.branches or [])) if c.backoffice else 'employee (self service – own data only)')
    return f"""You are "Ask ZEO AI", the assistant inside ZEO HRMS, an HR and payroll system used by companies in the UAE.
Today is {today:%A %d %B %Y} ({today.isoformat()}). The user is {who}{' – ' + name if name else ''}, {scope}.

How to answer
- Answer only from what the tools return. Never guess or invent names, numbers or dates. If the tools have no data for the question, say so and say which report or screen would have it.
- Pick the report with list_reports when unsure, then run_report with filters, grouping and sums instead of reading many rows. Check spelling of values (department, leave type, status) against the rows when a filter returns nothing.
- Money is AED. Show amounts with thousands separators. Say which period the numbers cover.
- Keep answers short: one or two sentences with the result, then a small markdown table when there are several rows (at most 15 rows; say how many more there are).
- Link every person or record you mention with the "open" link from the tool result, as a markdown link [text](link). Only use links the tools gave you. Mention the report_link for "see all".
- The user can only see their own branches and rights; if a tool returns an error about rights, tell them they do not have access.
- Answer in the user's language (English or Arabic).
- Do not give legal, medical or financial advice; for labour-law calculations say the figures come from the report and should be checked by HR."""


def call_claude(system, messages, tools):
    body = json.dumps({'model': setting('ZEO_AI_MODEL', 'claude-sonnet-5-5'), 'max_tokens': 2000, 'system': system,
                       'messages': messages, 'tools': tools}).encode()
    req = urllib.request.Request(setting('ZEO_AI_URL', 'https://api.anthropic.com/v1/messages'), data=body, method='POST', headers={
        'content-type': 'application/json', 'x-api-key': setting('ANTHROPIC_API_KEY'), 'anthropic-version': '2023-06-01'})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode('utf-8', 'replace')
        log.warning('Claude API %s: %s', e.code, detail)
        raise RuntimeError({401: 'The AI key is not valid. Ask your administrator to check ANTHROPIC_API_KEY.',
                            429: 'The AI service is busy. Try again in a minute.'}.get(e.code, f'The AI service answered {e.code}.'))
    except urllib.error.URLError:
        raise RuntimeError('The AI service cannot be reached from the server.')


def ask_ai(request, history, branch):
    tools = Tools(request, branch)
    system = system_prompt(request)
    msgs = [{'role': m['role'], 'content': m['content']} for m in history]
    for _ in range(MAX_ROUNDS):
        r = call_claude(system, msgs, TOOLS)
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


def ask_basic(request, question, branch):
    """Without an AI key: find the report by its words, the period and values named in the question; give the count and totals."""
    from .reports import REPORTS
    t = date.today()
    q = question.lower()
    if re.search(r'\b(my|i|me)\b', q) and re.search(r'leave|balance|payslip|salary|document|asset|loan', q):
        tools = Tools(request, branch)
        d = tools.my_summary()
        if d.get('error'):
            return d['error'], []
        bal = ', '.join(f"{b['name']} {b['balance']:g}" for b in d.get('leave_balances') or []) or 'none'
        ps = d.get('payslip')
        return (f"**Your leave balance:** {bal}.\n\n" + (f"**Last payslip:** {ps['period']} – net AED {float(ps['net']):,.0f}.\n\n" if ps else '')
                + f"Documents: {len(d.get('documents') or [])} · assets with you: {len(d.get('assets') or [])}."), tools.links
    key = next((k for w, k in WORDS.items() if w in q), None)
    if key is None or key not in REPORTS:
        return ('I can answer questions about employees, leave, attendance, payroll, loans, documents, assets, requests, exits, appraisals, '
                'recruitment, training and timesheets. Try for example: *How many leave requests are pending?*'), []
    tools = Tools(request, branch)
    period = next(((a, b) for pat, f in PERIODS if re.search(pat, q) for a, b in [f(t)]), (None, None))
    probe = tools.run_report(key, date_from=period[0], date_to=period[1], limit=0)
    if probe.get('error'):
        if 'may not' in probe['error']:
            return ('You do not have access to that report. You can ask about your own data, for example *What is my leave balance?*'), []
        return probe['error'], []
    # values named in the question (status, department, leave type, employee …)
    from .reports import ReportView
    d = ReportView().get(_Req(request, {'from': period[0], 'to': period[1], 'branch': branch}), key).data
    where = []
    for col in [c['key'] for c in d['columns'] if c['type'] == 'text']:
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
    labels0 = {c['key']: c['label'].lower() for c in d['columns']}
    gb = [k for k, l in labels0.items() if re.search(r'\b(by|per)\s+' + re.escape(l.split(' (')[0]) + r'\b', q)]
    tools.links = []   # only the answer's own link
    res = tools.run_report(key, date_from=period[0], date_to=period[1], where=where, group_by=gb or None, limit=0 if gb else 10)
    head = f"**{res['report']}**" + (f" ({res['period']['from']} to {res['period']['to']})" if isinstance(res['period'], dict) else '') + \
           (' – ' + ', '.join(f"{w['column']} = {' / '.join(w['value']) if isinstance(w['value'], list) else w['value']}" for w in where) if where else '')
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
            cells = [str(r.get(c, '') if r.get(c) is not None else '').replace('|', '/') for c in cols]
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


def suggestions(request):
    from AccessControl.access import ctx
    from .reports import REPORTS, _allowed
    c = ctx(request)
    can = {k for k, v in REPORTS.items() if _allowed(c, v[5])}
    s = []
    for key, text in [('documents', 'Whose documents expire in the next 30 days?'), ('leave', 'How many leave requests are pending?'),
                      ('payroll-register', 'Total net pay by department for last month'), ('headcount', 'Headcount by branch and department'),
                      ('attendance-summary', 'Who was absent most this month?'), ('loans', 'Which loans are still outstanding?'),
                      ('gratuity', 'Total gratuity accrued by department'), ('probation', 'Whose probation ends this month?')]:
        if key in can:
            s.append(text)
    if c.emp is not None:
        s.append('What is my leave balance?')
    return s[:6]


class AssistantView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'enabled': enabled(), 'mode': 'ai' if ai_ready() else 'basic', 'suggestions': suggestions(request) if enabled() else []})

    def post(self, request):
        if not enabled():
            return Response({'detail': 'Ask ZEO AI is switched off for this server.'}, status=status.HTTP_403_FORBIDDEN)
        msgs = [m for m in (request.data.get('messages') or []) if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and str(m.get('content') or '').strip()]
        msgs = [{'role': m['role'], 'content': str(m['content'])[:4000]} for m in msgs][-12:]
        while msgs and msgs[0]['role'] != 'user':
            msgs.pop(0)
        if not msgs or msgs[-1]['role'] != 'user':
            return Response({'detail': 'Ask a question.'}, status=status.HTTP_400_BAD_REQUEST)
        if _throttled(request.user):
            return Response({'detail': 'You have asked many questions in a short time. Wait a few minutes.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        branch = ','.join(re.findall(r'\d+', str(request.data.get('branch') or '')))
        started = time.time()
        mode = 'ai' if ai_ready() else 'basic'
        try:
            if mode == 'ai':
                answer, links = ask_ai(request, msgs, branch)
            else:
                answer, links = ask_basic(request, msgs[-1]['content'], branch)
        except RuntimeError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        log.info('ask-zeo-ai user=%s mode=%s secs=%.1f q=%r', request.user.pk, mode, time.time() - started, msgs[-1]['content'][:200])
        return Response({'answer': answer, 'links': links[:8], 'mode': mode})
