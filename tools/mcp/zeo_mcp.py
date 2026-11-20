#!/usr/bin/env python3
"""ZEO HRMS – MCP connector (v1.73.17). Lets an AI tool that speaks MCP (Model Context Protocol) read ZEO with API v1 and
propose changes with the API for AI tools. Nothing is changed in ZEO by this connector: a proposal is a draft that a
company administrator approves in Settings › Ask ZEO › Waiting for approval.

Python 3.8+ only (no packages). Runs over stdio:

    ZEO_URL=https://hr.example.com  ZEO_API_KEY=zeo_…  ZEO_COMPANY=<company schema>  python3 zeo_mcp.py

The API key is made in Settings › API keys with the scopes the tool may use (employees:read, org:read, … and for proposals
modules:write, reports:write, workflow:write, settings:write). Keep the key in the AI tool's own secret settings, never in a
file shared with others. See deploy/mcp/README.md.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = '1.73.17'
PROTOCOL = '2024-11-05'
URL = os.environ.get('ZEO_URL', '').rstrip('/')
KEY = os.environ.get('ZEO_API_KEY', '')
COMPANY = os.environ.get('ZEO_COMPANY', '')
TIMEOUT = float(os.environ.get('ZEO_TIMEOUT', '120'))
READS = ('employees', 'branches', 'departments', 'designations', 'leave-requests', 'leave-balances', 'attendance-days', 'payslips')
SCOPES = ('modules:write', 'reports:write', 'workflow:write', 'settings:write')

TOOLS = [
    {'name': 'zeo_about', 'description': 'What this ZEO API key may do: its scopes and the endpoints of API v1.',
     'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'zeo_read', 'description': 'Read ZEO data with API v1 (paged, at most 500 a page). what: ' + ', '.join(READS) +
     '. Filters as in the API: e.g. {"active": "1"}, {"from": "2026-11-01", "to": "2026-11-30"}, {"year": "2026", "month": "10"}.',
     'inputSchema': {'type': 'object', 'required': ['what'], 'properties': {
         'what': {'type': 'string', 'enum': list(READS)}, 'id': {'type': 'integer', 'description': 'one employee'},
         'filters': {'type': 'object', 'additionalProperties': {'type': 'string'}},
         'page': {'type': 'integer', 'minimum': 1}, 'page_size': {'type': 'integer', 'minimum': 1, 'maximum': 500}}}},
    {'name': 'zeo_propose_change', 'description':
        'Propose a change to ZEO in plain words. ZEO works it out (as Ask ZEO does) and keeps it as a DRAFT: nothing changes until a '
        'company administrator approves it. scope: modules:write (custom modules and fields), reports:write (reports), workflow:write '
        '(scripts, reminders, automations), settings:write (setup, data fixes, bulk changes). Answer: the draft(s) with before/after.',
     'inputSchema': {'type': 'object', 'required': ['scope', 'request'], 'properties': {
         'scope': {'type': 'string', 'enum': list(SCOPES)},
         'request': {'type': 'string', 'maxLength': 2000, 'description': 'e.g. "Make a vehicle request module with vehicle type (car, van or bus), '
                     'from date, to date and purpose, approved by the line manager then HR"'},
         'language': {'type': 'string', 'enum': ['en', 'ar']}}}},
    {'name': 'zeo_changes', 'description': 'The drafts and changes proposed with this API key, newest first; state: preview (waiting for '
     'approval), applied, undone, cancelled (rejected or withdrawn), failed.',
     'inputSchema': {'type': 'object', 'properties': {'state': {'type': 'string', 'enum': ['preview', 'applied', 'undone', 'cancelled', 'failed']},
                                                       'page': {'type': 'integer', 'minimum': 1}}}},
    {'name': 'zeo_change', 'description': 'One proposed change: its state, who approved or rejected it, and its before / after values.',
     'inputSchema': {'type': 'object', 'required': ['id'], 'properties': {'id': {'type': 'integer'}}}},
    {'name': 'zeo_withdraw_change', 'description': 'Take back a draft that was not approved yet.',
     'inputSchema': {'type': 'object', 'required': ['id'], 'properties': {'id': {'type': 'integer'}}}},
]


class ZeoError(Exception):
    pass


def call(method, path, query=None, body=None):
    if not URL or not KEY or not COMPANY:
        raise ZeoError('Set ZEO_URL, ZEO_API_KEY and ZEO_COMPANY for the connector.')
    q = dict(query or {}, schema=COMPANY)
    url = f'{URL}/api/v1/{path.lstrip("/")}?{urllib.parse.urlencode(q)}'
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Authorization': f'Api-Key {KEY}', 'Accept': 'application/json', 'Content-Type': 'application/json',
                                          'User-Agent': f'zeo-mcp/{VERSION}'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode() or '{}'
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors='replace')
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {'detail': raw[:500]}
    except urllib.error.URLError as e:
        raise ZeoError(f'ZEO could not be reached at {URL}: {e.reason}')


def run_tool(name, a):
    a = a or {}
    if name == 'zeo_about':
        return call('GET', '')
    if name == 'zeo_read':
        what = a.get('what')
        if what not in READS:
            raise ZeoError('what is one of ' + ', '.join(READS))
        q = {k: str(v) for k, v in (a.get('filters') or {}).items()}
        for k in ('page', 'page_size'):
            if a.get(k):
                q[k] = str(a[k])
        path = f'{what}/{int(a["id"])}/' if a.get('id') and what == 'employees' else f'{what}/'
        return call('GET', path, q)
    if name == 'zeo_propose_change':
        body = {'scope': a.get('scope'), 'request': a.get('request')}
        if a.get('language'):
            body['language'] = a['language']
        return call('POST', 'ai/changes/', body=body)
    if name == 'zeo_changes':
        q = {k: str(a[k]) for k in ('state', 'page') if a.get(k)}
        return call('GET', 'ai/changes/', q)
    if name == 'zeo_change':
        return call('GET', f'ai/changes/{int(a["id"])}/')
    if name == 'zeo_withdraw_change':
        return call('POST', f'ai/changes/{int(a["id"])}/withdraw/', body={})
    raise ZeoError(f'Unknown tool {name}')


def handle(msg):
    m, mid, p = msg.get('method'), msg.get('id'), msg.get('params') or {}
    if m == 'initialize':
        return {'protocolVersion': p.get('protocolVersion') or PROTOCOL, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'zeo-hrms', 'version': VERSION},
                'instructions': 'ZEO Books. Read with zeo_read; propose changes with zeo_propose_change – they are drafts until a company '
                                'administrator approves them in ZEO.'}
    if m == 'ping':
        return {}
    if m == 'tools/list':
        return {'tools': TOOLS}
    if m == 'tools/call':
        try:
            status, out = run_tool(p.get('name'), p.get('arguments'))
        except (ZeoError, KeyError, TypeError, ValueError) as e:
            return {'content': [{'type': 'text', 'text': str(e)}], 'isError': True}
        return {'content': [{'type': 'text', 'text': json.dumps(out, ensure_ascii=False, indent=1)}], 'isError': status >= 400,
                'structuredContent': out if isinstance(out, dict) else {'items': out}}
    if mid is None:
        return None   # a notification (e.g. notifications/initialized)
    raise LookupError(m)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        else:
            mid = msg.get('id')
            try:
                res = handle(msg)
                if mid is None:
                    continue
                out = {'jsonrpc': '2.0', 'id': mid, 'result': res}
            except LookupError as e:
                out = {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'Method not found: {e}'}}
            except Exception as e:   # noqa: BLE001
                out = {'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32603, 'message': str(e)[:300]}}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()
