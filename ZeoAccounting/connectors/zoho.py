"""Zoho Books connector (REST API v3).

OAuth 2: a self client / server client in the Zoho API console (scope ZohoBooks.fullaccess.all or
ZohoBooks.accountants.ALL,ZohoBooks.settings.ALL,ZohoBooks.contacts.ALL), its client id, secret and a refresh token. The
access token (1 hour) is fetched from {accounts domain}/oauth/v2/token and kept encrypted until it expires. Every call carries
organization_id. Use the data centre of the organisation: zohoapis.com / .eu / .in / .com.au / .sa / .ae … and the matching
accounts.zoho.* domain.

Department / employee / branch are reporting tags ("Department", "Employee", "Branch"); their options are the analytic
accounts. A journal is POST /journals with line_items (account_id, debit_or_credit, amount, tags); "Posted" publishes it.
reference_number = the ZEO journal number: before creating, the connector looks for it so a retry never makes two journals."""
import time

import requests

from .. import secure
from . import ConnectorError

TAG_FIELDS = {'department': 'zoho_dept_tag_id', 'employee': 'zoho_emp_tag_id', 'branch': 'zoho_branch_tag_id'}
TAG_NAMES = {'department': 'Department', 'employee': 'Employee', 'branch': 'Branch'}


class Zoho:
    def __init__(self, st):
        self.st = st
        self.api = (st.zoho_api_domain or 'https://www.zohoapis.com').rstrip('/')
        self.accounts_url = (st.zoho_accounts_domain or 'https://accounts.zoho.com').rstrip('/')
        self.org = st.zoho_org_id
        self._acc = None
        if not (self.org and st.zoho_client_id and secure.get(st, 'zoho_refresh_token')):
            raise ConnectorError('Zoho Books is not set up: organisation id, client id, client secret and refresh token are needed.')

    # -- auth
    def token(self, fresh=False):
        tok = secure.get(self.st, 'zoho_access_token')
        if tok and not fresh:
            t, _, exp = tok.partition('|')
            if exp and float(exp) > time.time() + 60:
                return t
        try:
            r = requests.post(f'{self.accounts_url}/oauth/v2/token', params={
                'refresh_token': secure.get(self.st, 'zoho_refresh_token'), 'client_id': self.st.zoho_client_id,
                'client_secret': secure.get(self.st, 'zoho_client_secret'), 'grant_type': 'refresh_token'}, timeout=30)
        except requests.RequestException as ex:
            raise ConnectorError(f'Zoho accounts cannot be reached: {ex}')
        d = r.json() if r.content else {}
        if r.status_code >= 400 or 'access_token' not in d:
            raise ConnectorError(f"Zoho refused the refresh token: {d.get('error') or r.text[:200]}")
        if d.get('api_domain') and d['api_domain'].rstrip('/') != self.api:
            self.api = d['api_domain'].rstrip('/')
            self.st.zoho_api_domain = self.api
        secure.put(self.st, 'zoho_access_token', f"{d['access_token']}|{time.time() + int(d.get('expires_in', 3600))}")
        self.st.save(update_fields=['secrets', 'zoho_api_domain'])
        return d['access_token']

    def req(self, method, path, body=None, params=None, again=True):
        p = {'organization_id': self.org, **(params or {})}
        try:
            r = requests.request(method, f'{self.api}/books/v3{path}', params=p, json=body,
                                 headers={'Authorization': f'Zoho-oauthtoken {self.token()}'}, timeout=60)
        except requests.RequestException as ex:
            raise ConnectorError(f'Zoho Books cannot be reached: {ex}')
        if r.status_code == 401 and again:
            self.token(fresh=True)
            return self.req(method, path, body, params, again=False)
        if r.status_code == 429:
            raise ConnectorError('Zoho Books: too many requests today / this minute – it will be tried again later.')
        d = r.json() if r.content else {}
        if r.status_code >= 400 or d.get('code', 0) not in (0, None):
            raise ConnectorError(f"Zoho Books {r.status_code}: {d.get('message') or r.text[:300]}")
        return d

    def pages(self, path, key, params=None):
        out, page = [], 1
        while True:
            d = self.req('GET', path, params={**(params or {}), 'page': page, 'per_page': 200})
            out += d.get(key, [])
            if not (d.get('page_context') or {}).get('has_more_page'):
                return out
            page += 1

    # -- reading
    def test(self):
        d = self.req('GET', f'/organizations/{self.org}')
        o = d.get('organization') or {}
        return {'ok': True, 'detail': f"Connected – {o.get('name', self.org)} ({o.get('currency_code', '')})"}

    def accounts(self):
        if self._acc is None:
            rows = self.pages('/chartofaccounts', 'chartofaccounts')
            self._acc = [{'id': r['account_id'], 'code': r.get('account_code') or '', 'name': r.get('account_name', ''),
                          'type': r.get('account_type', '')} for r in rows]
        return self._acc

    def tag(self, kind, create=True):
        tid = getattr(self.st, TAG_FIELDS[kind])
        if tid:
            return tid
        for t in self.req('GET', '/reportingtags').get('reporting_tags', []):
            if t.get('tag_name', '').lower() == TAG_NAMES[kind].lower():
                tid = t['tag_id']
                break
        if not tid and create:
            d = self.req('POST', '/reportingtags', {'tag_name': TAG_NAMES[kind], 'description': 'ZEO HR – posted by payroll',
                                                    'entities': [], 'multi_preference_entities': {'preference': 'line_item', 'entities': []}})
            tid = (d.get('tag') or d.get('reporting_tag') or {}).get('tag_id')
        if tid:
            setattr(self.st, TAG_FIELDS[kind], str(tid))
            self.st.save(update_fields=[TAG_FIELDS[kind]])
        return tid

    @staticmethod
    def _options(d):
        for k in ('tag_options', 'options', 'reporting_tag_options'):
            if isinstance(d.get(k), list):
                return d[k]
        for v in d.values():
            if isinstance(v, dict):
                o = Zoho._options(v)
                if o:
                    return o
        return []

    def analytics(self, kind):
        tid = self.tag(kind, create=False)
        if not tid:
            return []
        rows = self._options(self.req('GET', f'/reportingtags/{tid}/options/all'))
        return [{'id': r.get('tag_option_id'), 'name': r.get('tag_option_name', ''), 'code': ''} for r in rows]

    # -- making
    def create_analytic(self, kind, name, code):
        tid = self.tag(kind)
        have = [{'tag_option_id': o['id'], 'tag_option_name': o['name']} for o in self.analytics(kind)]
        label = f'{code} {name}'.strip() if kind != 'employee' else name
        d = self.req('PUT', f'/reportingtags/{tid}/options', {'tag_options': have + [{'tag_option_name': label[:100]}]})
        for o in self._options(d) or []:
            if o.get('tag_option_name') == label[:100]:
                return {'id': o['tag_option_id'], 'name': label, 'code': code}
        for o in self.analytics(kind):
            if o['name'] == label[:100]:
                return o
        raise ConnectorError(f'Zoho Books did not return the new option "{label}".')

    def create_account(self, code, name, parent):
        d = self.req('POST', '/chartofaccounts', {'account_name': name, 'account_code': code, 'account_type': parent.get('type') or 'expense'})
        a = d.get('chart_of_account') or d.get('account') or {}
        self._acc = None
        return {'id': a.get('account_id'), 'code': code, 'name': name}

    def partner(self, name, code, create=False, is_company=False, vat='', email='', customer=False):
        d = self.req('GET', '/contacts', params={'search_text': code})
        for c in d.get('contacts', []):
            if code in (c.get('contact_number'), c.get('contact_name'), c.get('company_name')) or code in (c.get('contact_name') or ''):
                return c['contact_id'], False
        if not create:
            return '', False
        body = {'contact_name': f'{name} ({code})', 'contact_type': 'customer' if customer else 'vendor', 'contact_number': code}
        if is_company:
            body['company_name'] = name
        if email:
            body['email'] = email
        d = self.req('POST', '/contacts', body)
        return (d.get('contact') or {}).get('contact_id', ''), True

    def post(self, batch):
        found = self.req('GET', '/journals', params={'reference_number': batch.number}).get('journals', [])
        found = [j for j in found if j.get('reference_number') == batch.number]
        if found:
            jid, num = found[0]['journal_id'], found[0].get('entry_number', '')
        else:
            acc = {a['code']: a['id'] for a in self.accounts()}
            missing = sorted({l.account for l in batch.lines.all() if l.account not in acc})
            if missing:
                raise ConnectorError(f'Accounts not in Zoho Books: {", ".join(missing)} – create them or fix the mapping.')
            tags = {k: self.tag(k, create=False) for k in ('department', 'employee', 'branch')}
            items = []
            for l in batch.lines.all():
                amt, side = (l.debit, 'debit') if l.debit else (l.credit, 'credit')
                it = {'account_id': acc[l.account], 'debit_or_credit': side, 'amount': float(amt),
                      'description': (l.label + (f' – {l.department}' if l.department else '') + (f' – {l.employee}' if l.employee else ''))[:500]}
                t = [{'tag_id': tags[k], 'tag_option_id': v} for k, v in (l.analytics or {}).items() if v and tags.get(k)]
                if t:
                    it['tags'] = t
                if l.partner_ref:
                    it['customer_id'] = l.partner_ref
                items.append(it)
            body = {'journal_date': batch.date.isoformat(), 'reference_number': batch.number, 'notes': f'ZEO {batch.source_label}'[:500],
                    'journal_type': 'both', 'line_items': items, 'status': 'published' if self.st.post_as == 'posted' else 'draft'}
            d = self.req('POST', '/journals', body)
            j = d.get('journal') or {}
            jid, num = j.get('journal_id'), j.get('entry_number', '')
        return jid, num or batch.number

    # -- v1.62.0: invoices, credit notes, payments and bills as documents (Posting rules › Send documents as)
    PARTY_ROLES = ('receivable', 'receivable_owner', 'vendor_payable')
    VAT_ROLES = ('vat_output', 'vat_input')
    DOC_PATH = {'invoice': ('/invoices', 'invoices', 'invoice', 'invoice_id', 'invoice_number'),
                'credit_note': ('/creditnotes', 'creditnotes', 'creditnote', 'creditnote_id', 'creditnote_number'),
                'bill': ('/bills', 'bills', 'bill', 'bill_id', 'bill_number'),
                'payment': ('/customerpayments', 'customerpayments', 'payment', 'payment_id', 'payment_number')}

    def _split(self, batch):
        roles = ((batch.rules or {}).get('doc') or {}).get('roles') or {}
        party, vat, other = [], [], []
        for l in batch.lines.all():
            r = roles.get(l.base_account) or roles.get(l.account) or ''
            (party if r in self.PARTY_ROLES else vat if r in self.VAT_ROLES else other).append((l, r))
        return party, vat, other

    def _party(self, party_lines, what):
        for l, _r in party_lines:
            if l.partner_ref:
                return str(l.partner_ref)
        raise ConnectorError(f'The {what} has no contact in Zoho Books – Accounting › Business partners › Match / create, then post again.')

    def _tax(self, rate, ref=''):
        if ref:
            return str(ref)
        rows = self.req('GET', '/settings/taxes').get('taxes', [])
        for t in rows:
            if abs(float(t.get('tax_percentage') or 0) - rate) < 0.01 and t.get('tax_type', 'tax') != 'tax_group':
                return t['tax_id']
        raise ConnectorError(f'Zoho Books has no {rate:g}% tax – add it under Settings › Taxes, or enter its tax id in ZEO Posting rules.')

    def _find(self, kind, batch):
        path, key, _one, idk, numk = self.DOC_PATH[kind]
        marker = f'ZEO {batch.number}'
        rows = self.req('GET', path, params={'search_text': batch.number}).get(key, [])
        for r in rows:
            if (r.get('reference_number') or '').startswith(marker):
                return r[idk], r.get(numk) or ''
        return None

    def post_document(self, batch, kind):
        doc = (batch.rules or {}).get('doc') or {}
        found = self._find(kind, batch)
        if found:
            return found[0], found[1] or batch.number
        if kind == 'payment':
            return self._post_payment(batch, doc)
        path, _key, one, idk, numk = self.DOC_PATH[kind]
        acc = {a['code']: a['id'] for a in self.accounts()}
        party, vat, other = self._split(batch)
        sale = kind != 'bill'
        contact = self._party(party, 'vendor bill' if kind == 'bill' else 'invoice')
        missing = sorted({l.account for l, _r in party + vat + other if l.account not in acc})
        if missing:
            raise ConnectorError(f'Accounts not in Zoho Books: {", ".join(missing)} – create them or fix the mapping.')
        if kind == 'credit_note':
            net = lambda l: float(l.debit - l.credit)
        elif sale:
            net = lambda l: float(l.credit - l.debit)
        else:
            net = lambda l: float(l.debit - l.credit)
        base = sum(net(l) for l, _r in other)
        vat_amt = sum(abs(float(l.credit - l.debit)) for l, _r in vat)
        tax_id = ''
        if vat_amt and base:
            tax_id = self._tax(round(vat_amt * 100 / base, 2), self.st.sale_tax_ref if sale else self.st.purchase_tax_ref)
        items = []
        for l, _r in other:
            it = {'account_id': acc[l.account], 'name': (l.label or doc.get('description') or batch.reference)[:100],
                  'description': (doc.get('description') or l.label or '')[:500], 'rate': round(net(l), 2), 'quantity': 1}
            if tax_id:
                it['tax_id'] = tax_id
            items.append(it)
        ref = f'ZEO {batch.number} {doc.get("invoice_no") if kind == "bill" else batch.reference}'[:100]
        date = doc.get('date') or batch.date.isoformat()
        body = {'date': date, 'reference_number': ref, 'line_items': items, 'notes': (doc.get('description') or '')[:500]}
        if kind == 'bill':
            body.update(vendor_id=contact, bill_number=(doc.get('invoice_no') or batch.reference)[:100], due_date=doc.get('due') or date)
        else:
            body['customer_id'] = contact
            if kind == 'invoice':
                body['due_date'] = doc.get('due') or date
        d = self.req('POST', path, body)
        rec = d.get(one) or {}
        did = rec.get(idk)
        if not did:
            raise ConnectorError(f'Zoho Books did not return the new {kind.replace("_", " ")}.')
        if doc.get('total') and rec.get('total') is not None and abs(abs(float(rec['total'])) - abs(float(doc['total']))) > 0.05:
            raise ConnectorError(f'Zoho Books calculated {rec["total"]} but ZEO has {doc["total"]} – check the tax in Zoho Books (it was left as a draft).')
        if self.st.post_as == 'posted':
            st = {'invoice': 'sent', 'credit_note': 'open', 'bill': 'open'}[kind]
            try:
                self.req('POST', f'{path}/{did}/status/{st}')
            except ConnectorError:
                if kind == 'invoice':
                    raise   # a credit note / bill is usually open already when it is created
        return did, rec.get(numk) or batch.number

    def _post_payment(self, batch, doc):
        acc = {a['code']: a['id'] for a in self.accounts()}
        party, _vat, other = self._split(batch)
        contact = self._party(party, 'receipt')
        bank = [l for l, _r in other if l.debit]
        if not bank or bank[0].account not in acc:
            raise ConnectorError('The bank / cash account of the receipt is not in Zoho Books – fix the mapping.')
        amount = round(float(doc.get('amount') or batch.total_debit), 2)
        applied, left = [], amount
        for x in doc.get('bills') or []:
            if x.get('invoice_id') and left > 0:
                a = round(min(float(x.get('amount') or 0), left), 2)
                if a > 0:
                    applied.append({'invoice_id': str(x['invoice_id']), 'amount_applied': a})
                    left -= a
        body = {'customer_id': contact, 'payment_mode': 'cash' if doc.get('method') == 'cash' else 'banktransfer',
                'amount': amount, 'date': doc.get('date') or batch.date.isoformat(), 'account_id': acc[bank[0].account],
                'reference_number': f'ZEO {batch.number} {batch.reference}'[:100], 'description': (doc.get('description') or '')[:500],
                'invoices': applied}
        d = self.req('POST', '/customerpayments', body)
        p = d.get('payment') or {}
        return p.get('payment_id'), p.get('payment_number') or batch.number

    def reverse_document(self, orig, rev):
        """Void the invoice / credit note / bill; delete the payment (Zoho Books has no void for customer payments)."""
        kind = (orig.rules or {}).get('doc_kind')
        path, *_ = self.DOC_PATH[kind]
        if kind == 'payment':
            self.req('DELETE', f'{path}/{orig.external_id}')
            return orig.external_id, f'{orig.external_ref} deleted'
        self.req('POST', f'{path}/{orig.external_id}/status/void')
        return orig.external_id, f'{orig.external_ref} voided'
