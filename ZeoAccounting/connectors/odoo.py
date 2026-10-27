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
            if method == 'create' and 'vals_list' in kw:   # v1.62.0 fix: XML-RPC create needs the records as args[0]
                args = [kw.pop('vals_list')] + args
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

    def partner(self, name, code, create=False, is_company=False, vat='', email='', customer=True):
        rows = self.call('res.partner', 'search_read', domain=[['ref', '=', code]], fields=['id'], limit=1)
        if not rows and vat:   # v1.44.0: the same company by its tax number
            rows = self.call('res.partner', 'search_read', domain=[['vat', '=', vat]], fields=['id'], limit=1)
        if rows:
            return rows[0]['id'], False
        if not create:
            return '', False
        vals = {'name': name, 'ref': code, 'is_company': bool(is_company)}
        if vat:
            vals['vat'] = vat
        if email:
            vals['email'] = email
        return self._id(self.call('res.partner', 'create', vals_list=[vals])), True

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


    # ------------------------------------------------------------------ v1.62.0 documents instead of journal vouchers
    PARTY_ROLES = ('receivable', 'receivable_owner', 'vendor_payable')
    VAT_ROLES = ('vat_output', 'vat_input')

    def _fields(self, model):
        cache = self.__dict__.setdefault('_fcache', {})
        if model not in cache:
            cache[model] = set(self.call(model, 'fields_get', attributes=['type']).keys())
        return cache[model]

    def _journal_of(self, code, jtype):
        rows = self.call('account.journal', 'search_read', domain=[['code', '=', code or '?'], ['type', '=', jtype]], fields=['id'], limit=1) if code else []
        if not rows:
            rows = self.call('account.journal', 'search_read', domain=[['type', '=', jtype]], fields=['id'], limit=1)
        if not rows:
            raise ConnectorError(f'Odoo has no {jtype} journal – create one or set its code in Posting rules.')
        return rows[0]['id']

    def _tax(self, rate, use, ref=''):
        if ref:
            if not str(ref).strip().isdigit():
                raise ConnectorError(f'The Odoo tax id "{ref}" in Settings must be a number (the id of the tax in Odoo) – or leave it empty.')
            return int(ref)
        rows = self.call('account.tax', 'search_read', domain=[['type_tax_use', '=', use], ['amount_type', '=', 'percent'],
                                                              ['amount', '>=', rate - 0.01], ['amount', '<=', rate + 0.01], ['price_include', '=', False]],
                         fields=['id', 'name'], limit=1)
        if not rows:
            raise ConnectorError(f'Odoo has no {"sales" if use == "sale" else "purchase"} tax of {rate:g} % (tax excluded) – create it in Odoo or '
                                 f'set its id in Posting rules.')
        return rows[0]['id']

    def _x(self, vals):
        return [0, 0, vals] if self.mode == 'json2' else (0, 0, vals)

    def _split(self, batch):
        """(party lines, VAT lines, other lines) of a Property journal, by the role ZEO gave each account."""
        roles = ((batch.rules or {}).get('doc') or {}).get('roles') or {}
        party, vat, other = [], [], []
        for l in batch.lines.all():
            r = roles.get(l.base_account) or roles.get(l.account) or ''
            (party if r in self.PARTY_ROLES else vat if r in self.VAT_ROLES else other).append((l, r))
        return party, vat, other

    def _party(self, party_lines, what):
        for l, _r in party_lines:
            if l.partner_ref:
                return int(l.partner_ref)
        raise ConnectorError(f'The {what} has no partner in Odoo – Accounting › Business partners › Match / create, then post again.')

    def _find_move(self, batch):
        rows = self.call('account.move', 'search_read', domain=[['ref', '=like', f'ZEO {batch.number} %']], fields=['id', 'name', 'state'], limit=1)
        return rows[0] if rows else None

    def post_document(self, batch, kind):
        doc = (batch.rules or {}).get('doc') or {}
        if kind == 'payment':
            return self._post_payment(batch, doc)
        found = self._find_move(batch)
        if found:
            mid = found['id']
        else:
            acc = {a['code']: a for a in self.accounts()}
            party, vat, other = self._split(batch)
            partner = self._party(party, 'invoice' if kind != 'bill' else 'vendor bill')
            sale = kind != 'bill'
            missing = sorted({l.account for l, _r in party + vat + other if l.account not in acc})
            if missing:
                raise ConnectorError(f'Accounts not in Odoo: {", ".join(missing)} – create them or fix the mapping.')
            # the partner's receivable / payable account = the one mapped in ZEO, so balances match the JV setup
            if party:
                pa = acc[party[0][0].account]
                fld = 'property_account_receivable_id' if sale else 'property_account_payable_id'
                if pa.get('type') in ('asset_receivable', 'liability_payable'):
                    self.call('res.partner', 'write', ids=[partner], vals={fld: pa['id']})
            net = lambda l: float(l.credit - l.debit) if sale else float(l.debit - l.credit)
            if kind == 'credit_note':
                net = lambda l: float(l.debit - l.credit)
            base = sum(net(l) for l, _r in other)
            vat_amt = sum(abs(float(l.credit - l.debit)) for l, _r in vat)
            tax_ids = []
            if vat_amt and base:
                rate = round(vat_amt * 100 / base, 2)
                tax_ids = [self._tax(rate, 'sale' if sale else 'purchase', self.st.sale_tax_ref if sale else self.st.purchase_tax_ref)]
            lines = []
            for l, _r in other:
                v = {'name': (l.label or doc.get('description') or batch.reference)[:250], 'quantity': 1, 'price_unit': round(net(l), 2),
                     'account_id': acc[l.account]['id']}
                if tax_ids:
                    v['tax_ids'] = [[6, 0, tax_ids]] if self.mode == 'json2' else [(6, 0, tax_ids)]
                refs = [str(x) for x in l.analytics.values() if x]
                if refs:
                    v['analytic_distribution'] = {','.join(refs): 100} if self.mode == 'json2' else {r: 100 for r in refs}
                lines.append(self._x(v))
            mtype = {'invoice': 'out_invoice', 'credit_note': 'out_refund', 'bill': 'in_invoice'}[kind]
            ref = f'ZEO {batch.number} {doc.get("invoice_no") if kind == "bill" else batch.reference}'[:200]
            vals = {'move_type': mtype, 'partner_id': partner, 'invoice_date': doc.get('date') or batch.date.isoformat(),
                    'invoice_date_due': doc.get('due') or doc.get('date') or batch.date.isoformat(), 'ref': ref,
                    'journal_id': self._journal_of(self.st.purchase_journal_code if kind == 'bill' else self.st.sales_journal_code,
                                                   'purchase' if kind == 'bill' else 'sale'),
                    'invoice_line_ids': lines}
            if 'invoice_origin' in self._fields('account.move'):
                vals['invoice_origin'] = (doc.get('number') or batch.reference)[:60]
            mid = self._id(self.call('account.move', 'create', vals_list=[vals]))
        # the totals are compared BEFORE posting, so a document Odoo calculates differently really stays a draft
        rec = self.call('account.move', 'read', ids=[mid], fields=['name', 'amount_total', 'state'])
        if rec and doc.get('total') and rec[0].get('state') == 'draft' and abs(abs(rec[0].get('amount_total') or 0) - abs(doc['total'])) > 0.05:
            raise ConnectorError(f'Odoo calculated {rec[0].get("amount_total")} but ZEO has {doc["total"]} – check the tax in Odoo (it was left as a draft).')
        if self.st.post_as == 'posted' and rec and rec[0].get('state') == 'draft':
            self.call('account.move', 'action_post', ids=[mid])
            rec = self.call('account.move', 'read', ids=[mid], fields=['name', 'amount_total', 'state'])
        return mid, (rec[0].get('name') if rec else '') or str(mid)

    def _post_payment(self, batch, doc):
        marker = f'ZEO {batch.number} {batch.reference}'[:200]
        pf = self._fields('account.payment')
        memo = 'memo' if 'memo' in pf else 'ref'
        found = self.call('account.payment', 'search_read', domain=[[memo, '=like', f'ZEO {batch.number} %']], fields=['id', 'name'], limit=1)
        if found:
            return found[0]['id'], found[0].get('name') or str(found[0]['id'])
        party, _vat, _other = self._split(batch)
        partner = self._party(party, 'receipt')
        journal = self._journal_of(self.st.bank_journal_code, 'bank') if doc.get('method') != 'cash' else self._journal_of('', 'cash')
        amount = round(float(doc.get('amount') or batch.total_debit), 2)
        inv = [int(x['invoice_id']) for x in doc.get('bills') or [] if x.get('invoice_id')]
        if inv:
            open_inv = [r['id'] for r in self.call('account.move', 'read', ids=inv, fields=['state', 'amount_residual'])
                        if r.get('state') == 'posted' and (r.get('amount_residual') or 0) > 0]
        else:
            open_inv = []
        pid = None
        if open_inv:   # Odoo's own "Register payment": the invoices are marked paid / partly paid
            ctx = {'active_model': 'account.move', 'active_ids': open_inv}
            if self.st.odoo_company_id:
                ctx['allowed_company_ids'] = [self.st.odoo_company_id]
            wiz = self._id(self.call('account.payment.register', 'create', context=ctx, vals_list=[{
                'payment_date': doc.get('date') or batch.date.isoformat(), 'journal_id': journal, 'amount': amount,
                'communication': marker, 'group_payment': True}]))
            res = self.call('account.payment.register', 'action_create_payments', ids=[wiz], context=ctx)
            if isinstance(res, dict) and res.get('res_id'):
                pid = res['res_id']
            if not pid:
                rows = self.call('account.payment', 'search_read', domain=[[memo, '=', marker]], fields=['id'], limit=1)
                pid = rows[0]['id'] if rows else None
        if not pid:   # an advance, or the invoices went as JVs: a payment on account
            pid = self._id(self.call('account.payment', 'create', vals_list=[{
                'payment_type': 'inbound', 'partner_type': 'customer', 'partner_id': partner, 'amount': amount,
                'date': doc.get('date') or batch.date.isoformat(), 'journal_id': journal, memo: marker}]))
            if self.st.post_as == 'posted':
                self.call('account.payment', 'action_post', ids=[pid])
        rec = self.call('account.payment', 'read', ids=[pid], fields=['name'])
        return pid, (rec[0].get('name') if rec else '') or str(pid)

    def reverse_document(self, orig, rev):
        """A credit note for an invoice / credit note / vendor bill; a cancelled payment for a receipt."""
        kind = (orig.rules or {}).get('doc_kind')
        ext = int(orig.external_id)
        if kind == 'payment':
            st = self.call('account.payment', 'read', ids=[ext], fields=['state', 'name'])
            if st and st[0].get('state') not in ('draft', 'cancel', 'canceled'):
                self.call('account.payment', 'action_draft', ids=[ext])
            self.call('account.payment', 'action_cancel', ids=[ext])
            return ext, f'{(st[0].get("name") if st else ext)} cancelled'
        ctx = {'active_model': 'account.move', 'active_ids': [ext]}
        wf = self._fields('account.move.reversal')
        vals = {'date': rev.date.isoformat(), 'reason': f'ZEO {rev.number} {rev.reference}'[:200]}
        if 'move_ids' in wf:
            vals['move_ids'] = [[6, 0, [ext]]] if self.mode == 'json2' else [(6, 0, [ext])]
        if 'journal_id' in wf:
            j = self.call('account.move', 'read', ids=[ext], fields=['journal_id'])
            if j and j[0].get('journal_id'):
                vals['journal_id'] = j[0]['journal_id'][0] if isinstance(j[0]['journal_id'], list) else j[0]['journal_id']
        wiz = self._id(self.call('account.move.reversal', 'create', context=ctx, vals_list=[vals]))
        res = None
        for m in ('refund_moves', 'reverse_moves'):
            try:
                res = self.call('account.move.reversal', m, ids=[wiz], context=ctx)
                break
            except ConnectorError as ex:
                if 'refund_moves' not in str(ex) and 'reverse_moves' not in str(ex) and 'does not exist' not in str(ex):
                    raise
        new = res.get('res_id') if isinstance(res, dict) else None
        if not new:
            rows = self.call('account.move', 'search_read', domain=[['reversed_entry_id', '=', ext]], fields=['id'], order='id desc', limit=1)
            new = rows[0]['id'] if rows else None
        if not new:
            raise ConnectorError('Odoo did not make the credit note.')
        st = self.call('account.move', 'read', ids=[new], fields=['state', 'name'])
        if st and st[0].get('state') == 'draft':
            self.call('account.move', 'action_post', ids=[new])
            st = self.call('account.move', 'read', ids=[new], fields=['name'])
        return new, (st[0].get('name') if st else '') or str(new)
