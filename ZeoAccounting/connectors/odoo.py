"""Odoo connector.

JSON-2 API (Odoo 19 and later – the API Odoo keeps):  POST {url}/json/2/{model}/{method}
    Authorization: bearer <API key>, X-Odoo-Database: <db>, body = the method's named arguments (ids, domain, fields, vals_list …)
XML-RPC (Odoo 14 – 18; removed by Odoo in 22 / Online 21.1):  /xmlrpc/2/common authenticate + /xmlrpc/2/object execute_kw,
    with the user's login and the API key used as password.

A journal is an account.move (move_type 'entry') in the journal with the code of the settings; dimensions go in the line's
analytic_distribution (one analytic account per plan – Department, Employee, Branch – each at 100 %), the employee partner
in partner_id. The move's ref is "ZEO <journal number> …"; before creating, the connector looks for that ref so a retry after
a lost answer never makes a second entry."""
import xmlrpc.client

import requests

from .. import secure
from . import ConnectorError

PLAN_NAMES = {'department': 'Department', 'employee': 'Employee', 'branch': 'Branch'}
PLAN_FIELDS = {'department': 'odoo_dept_plan_id', 'employee': 'odoo_emp_plan_id', 'branch': 'odoo_branch_plan_id'}


class Odoo:
    def __init__(self, st):
        self.st = st
        self.url = (st.odoo_url or '').rstrip('/')
        self.db = st.odoo_db
        self.key = secure.get(st, 'odoo_api_key')
        self.mode = st.odoo_api
        self._uid = None
        self._acc = None
        if not self.url or not self.key:
            raise ConnectorError('Odoo is not set up: fill in the URL and the API key.')

    # -- transport
    def call(self, model, method, ids=None, **kw):
        if self.mode == 'json2':
            body = dict(kw)
            if ids is not None:
                body['ids'] = ids
            if self.st.odoo_company_id:
                body.setdefault('context', {})['allowed_company_ids'] = [self.st.odoo_company_id]
            headers = {'Authorization': f'bearer {self.key}', 'Content-Type': 'application/json'}
            if self.db:
                headers['X-Odoo-Database'] = self.db
            try:
                r = requests.post(f'{self.url}/json/2/{model}/{method}', json=body, headers=headers, timeout=60)
            except requests.RequestException as ex:
                raise ConnectorError(f'Odoo cannot be reached: {ex}')
            if r.status_code >= 400:
                try:
                    msg = r.json().get('message') or r.text
                except ValueError:
                    msg = r.text
                raise ConnectorError(f'Odoo {r.status_code}: {str(msg)[:500]}')
            return r.json()
        # XML-RPC
        try:
            if self._uid is None:
                common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common', allow_none=True)
                self._uid = common.authenticate(self.db, self.st.odoo_login, self.key, {})
                if not self._uid:
                    raise ConnectorError('Odoo refused the login / API key.')
            obj = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object', allow_none=True)
            args = [ids] if ids is not None else []
            if self.st.odoo_company_id:
                kw.setdefault('context', {})['allowed_company_ids'] = [self.st.odoo_company_id]
            return obj.execute_kw(self.db, self._uid, self.key, model, method, args, kw)
        except xmlrpc.client.Fault as ex:
            raise ConnectorError(f'Odoo: {ex.faultString[:500]}')
        except (OSError, xmlrpc.client.ProtocolError) as ex:
            raise ConnectorError(f'Odoo cannot be reached: {ex}')

    # -- reading
    def test(self):
        me = self.call('res.users', 'context_get')
        comp = self.call('res.company', 'search_read', domain=[], fields=['name'], limit=5)
        j = self.journal(required=False)
        return {'ok': True, 'detail': f"Connected – companies: {', '.join(c['name'] for c in comp)}; language {me.get('lang', '')}; "
                                      f"journal {self.st.journal_code}: {'found' if j else 'NOT FOUND'}"}

    def journal(self, required=True):
        rows = self.call('account.journal', 'search_read', domain=[['code', '=', self.st.journal_code or 'MISC']], fields=['id', 'name'], limit=1)
        if not rows:
            rows = self.call('account.journal', 'search_read', domain=[['type', '=', 'general']], fields=['id', 'name'], limit=1) if not required else []
        if not rows and required:
            raise ConnectorError(f'Odoo has no journal with the code {self.st.journal_code}.')
        return rows[0]['id'] if rows else None

    def accounts(self):
        if self._acc is None:
            rows = self.call('account.account', 'search_read', domain=[], fields=['id', 'code', 'name', 'account_type'])
            self._acc = [{'id': r['id'], 'code': r.get('code') or '', 'name': r['name'], 'type': r.get('account_type') or ''} for r in rows]
        return self._acc

    def plan(self, kind, create=True):
        pid = getattr(self.st, PLAN_FIELDS[kind])
        if pid:
            return pid
        rows = self.call('account.analytic.plan', 'search_read', domain=[['name', '=', PLAN_NAMES[kind]]], fields=['id'], limit=1)
        if rows:
            pid = rows[0]['id']
        elif create:
            pid = self._id(self.call('account.analytic.plan', 'create', vals_list=[{'name': PLAN_NAMES[kind]}]))
        else:
            return None
        setattr(self.st, PLAN_FIELDS[kind], pid)
        self.st.save(update_fields=[PLAN_FIELDS[kind]])
        return pid

    def analytics(self, kind):
        pid = self.plan(kind, create=False)
        if not pid:
            return []
        rows = self.call('account.analytic.account', 'search_read', domain=[['plan_id', '=', pid]], fields=['id', 'name', 'code'])
        return [{'id': r['id'], 'name': r['name'], 'code': r.get('code') or ''} for r in rows]

    @staticmethod
    def _id(v):
        return v[0] if isinstance(v, list) else v

    # -- making
    def create_analytic(self, kind, name, code):
        pid = self.plan(kind)
        vals = {'name': name, 'plan_id': pid}
        if code:
            vals['code'] = code
        new = self._id(self.call('account.analytic.account', 'create', vals_list=[vals]))
        return {'id': new, 'name': name, 'code': code}

    def create_account(self, code, name, parent):
        vals = {'code': code, 'name': name, 'account_type': parent.get('type') or 'expense'}
        new = self._id(self.call('account.account', 'create', vals_list=[vals]))
        self._acc = None
        return {'id': new, 'code': code, 'name': name}

    def partner(self, name, code, create=False):
        rows = self.call('res.partner', 'search_read', domain=[['ref', '=', code]], fields=['id'], limit=1)
        if rows:
            return rows[0]['id'], False
        if not create:
            return '', False
        return self._id(self.call('res.partner', 'create', vals_list=[{'name': name, 'ref': code, 'is_company': False}])), True

    def post(self, batch):
        ref = f'ZEO {batch.number} {batch.reference}'[:200]
        found = self.call('account.move', 'search_read', domain=[['ref', '=like', f'ZEO {batch.number} %']], fields=['id', 'name', 'state'], limit=1)
        if found:
            mid = found[0]['id']
        else:
            acc = {a['code']: a['id'] for a in self.accounts()}
            missing = sorted({l.account for l in batch.lines.all() if l.account not in acc})
            if missing:
                raise ConnectorError(f'Accounts not in Odoo: {", ".join(missing)} – create them (Accounting › Journals › Missing accounts) or fix the mapping.')
            lines = []
            for l in batch.lines.all():
                v = {'account_id': acc[l.account], 'name': (l.label + (f' – {l.department}' if l.department else '') + (f' – {l.employee}' if l.employee else ''))[:250],
                     'debit': float(l.debit), 'credit': float(l.credit)}
                refs = [str(x) for x in l.analytics.values() if x]
                if refs:
                    v['analytic_distribution'] = {','.join(refs): 100} if self.mode == 'json2' else {r: 100 for r in refs}
                if l.partner_ref:
                    v['partner_id'] = int(l.partner_ref)
                lines.append([0, 0, v] if self.mode == 'json2' else (0, 0, v))
            mid = self._id(self.call('account.move', 'create', vals_list=[{'move_type': 'entry', 'date': batch.date.isoformat(), 'ref': ref,
                                                                           'journal_id': self.journal(), 'line_ids': lines}]))
        if self.st.post_as == 'posted':
            st = self.call('account.move', 'read', ids=[mid], fields=['state'])
            if st and st[0].get('state') == 'draft':
                self.call('account.move', 'action_post', ids=[mid])
        rec = self.call('account.move', 'read', ids=[mid], fields=['name', 'state'])
        return mid, (rec[0].get('name') if rec else '') or str(mid)
