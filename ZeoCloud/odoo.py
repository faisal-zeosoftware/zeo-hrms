"""v1.28.0 – link to Adept's Odoo for ZEO Cloud invoices, and online payment through Odoo.

Odoo stays the accounting system: ZEO sends the invoice there (customer, lines, period), Odoo posts it and gives the
customer's portal link, where the owner pays online with the payment providers set up in Odoo (cards through a UAE
gateway, bank transfer …). ZEO never sees card data. A sync reads the payment state: paid in Odoo → paid in ZEO
(renewal date moves, read-only ends).

.env (Adept's cloud server only; the API key never in code or packages):
  ZEO_ODOO_URL=https://adept.odoo.com   ZEO_ODOO_DB=adept   ZEO_ODOO_LOGIN=billing@adeptbs.ae   ZEO_ODOO_API_KEY=…
  optional ZEO_ODOO_PRODUCT_ID (product for the lines), ZEO_ODOO_TAX_ID (e.g. VAT 5 %), ZEO_ODOO_JOURNAL_ID
Sync: python manage.py cloud_odoo_sync (Celery beat runs it every hour when Odoo is set), or console → invoice → Check payment.
"""
import logging
import xmlrpc.client

from django.conf import settings
from django.utils import timezone

log = logging.getLogger(__name__)
PAID_STATES = ('paid', 'in_payment')


class OdooError(Exception):
    pass


def configured():
    return all(getattr(settings, k, '') for k in ('ZEO_ODOO_URL', 'ZEO_ODOO_DB', 'ZEO_ODOO_LOGIN', 'ZEO_ODOO_API_KEY'))


class Odoo:
    def __init__(self):
        if not configured():
            raise OdooError('Odoo is not set up: ZEO_ODOO_URL, ZEO_ODOO_DB, ZEO_ODOO_LOGIN and ZEO_ODOO_API_KEY in .env.')
        self.url = settings.ZEO_ODOO_URL.rstrip('/')
        self.db, self.key = settings.ZEO_ODOO_DB, settings.ZEO_ODOO_API_KEY
        try:
            common = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/common', allow_none=True)
            self.uid = common.authenticate(self.db, settings.ZEO_ODOO_LOGIN, self.key, {})
        except Exception as e:
            raise OdooError(f'Odoo cannot be reached: {e}')
        if not self.uid:
            raise OdooError('Odoo refused the login (ZEO_ODOO_LOGIN / ZEO_ODOO_API_KEY).')
        self.obj = xmlrpc.client.ServerProxy(f'{self.url}/xmlrpc/2/object', allow_none=True)

    def call(self, model, method, *args, **kw):
        try:
            return self.obj.execute_kw(self.db, self.uid, self.key, model, method, list(args), kw)
        except xmlrpc.client.Fault as e:
            raise OdooError(f'Odoo: {e.faultString.splitlines()[-1] if e.faultString else e}')

    # ------------------------------------------------------------ customer
    def partner_for(self, acc):
        email = (acc.owner_email or '').strip()
        dom = [['ref', '=', f'ZEO-ACC-{acc.pk}']]
        ids = self.call('res.partner', 'search', dom, limit=1)
        if not ids and email:
            ids = self.call('res.partner', 'search', [['email', '=ilike', email], ['is_company', '=', True]], limit=1)
        vals = {'name': acc.name, 'email': email, 'is_company': True, 'ref': f'ZEO-ACC-{acc.pk}'}
        if acc.trn:
            vals['vat'] = acc.trn
        if ids:
            self.call('res.partner', 'write', ids, vals)
            return ids[0]
        return self.call('res.partner', 'create', vals)

    # ------------------------------------------------------------ invoice
    def push(self, inv):
        acc = inv.account
        period = f' {inv.period_from:%d/%m/%Y} – {inv.period_to:%d/%m/%Y}' if inv.period_from and inv.period_to else ''
        line = {'name': (inv.note or f'ZEO Cloud – {acc.plan.name if acc.plan_id else "subscription"}') + period, 'quantity': 1,
                'price_unit': float(inv.amount or 0)}
        if getattr(settings, 'ZEO_ODOO_PRODUCT_ID', ''):
            line['product_id'] = int(settings.ZEO_ODOO_PRODUCT_ID)
        if getattr(settings, 'ZEO_ODOO_TAX_ID', ''):
            line['tax_ids'] = [(6, 0, [int(settings.ZEO_ODOO_TAX_ID)])]
        vals = {'move_type': 'out_invoice', 'partner_id': self.partner_for(acc), 'ref': f'ZEO {inv.number}',
                'invoice_date': inv.issued_on.isoformat(), 'invoice_line_ids': [(0, 0, line)]}
        if getattr(settings, 'ZEO_ODOO_JOURNAL_ID', ''):
            vals['journal_id'] = int(settings.ZEO_ODOO_JOURNAL_ID)
        move_id = self.call('account.move', 'create', vals)
        self.call('account.move', 'action_post', [move_id])
        return move_id

    def read(self, move_id=None, name=None):
        dom = [['id', '=', move_id]] if move_id else [['name', '=', name], ['move_type', '=', 'out_invoice']]
        rows = self.call('account.move', 'search_read', dom, fields=['id', 'name', 'state', 'payment_state', 'amount_total', 'currency_id'], limit=1)
        if not rows:
            raise OdooError('The invoice was not found in Odoo.')
        r = rows[0]
        try:
            r['portal'] = self.call('account.move', 'get_portal_url', [r['id']])
        except OdooError:
            r['portal'] = ''
        return r


def _abs(odoo, path):
    if not path:
        return ''
    return path if path.startswith('http') else f'{odoo.url}{path}'


def push_invoice(inv, by=None):
    """Create and post the invoice in Odoo; keep its number, state and payment link."""
    from . import services as S
    o = Odoo()
    if inv.odoo_id:
        return sync_invoice(inv, by, odoo=o)
    mid = o.push(inv)
    r = o.read(move_id=mid)
    inv.odoo_id, inv.odoo_ref, inv.odoo_state = r['id'], (r.get('name') or '')[:60], r.get('payment_state') or r.get('state') or ''
    inv.pay_url, inv.synced_at = _abs(o, r.get('portal'))[:500], timezone.now()
    inv.save(update_fields=['odoo_id', 'odoo_ref', 'odoo_state', 'pay_url', 'synced_at'])
    S.event(inv.account, 'invoice', f'Invoice {inv.number} sent to Odoo ({inv.odoo_ref})', by)
    return inv


def sync_invoice(inv, by=None, odoo=None):
    """Read the payment state from Odoo; paid there → paid here."""
    from . import services as S
    o = odoo or Odoo()
    r = o.read(move_id=inv.odoo_id) if inv.odoo_id else o.read(name=inv.odoo_ref)
    inv.odoo_id, inv.odoo_state = r['id'], r.get('payment_state') or r.get('state') or ''
    if r.get('portal'):
        inv.pay_url = _abs(o, r['portal'])[:500]
    inv.synced_at = timezone.now()
    inv.save(update_fields=['odoo_id', 'odoo_state', 'pay_url', 'synced_at'])
    if inv.state == 'unpaid' and inv.odoo_state in PAID_STATES:
        S.mark_paid(inv, by)
        for l in S.company_links(inv.account, include_archived=True):
            S.forget(l.company.schema_name)
    return inv


def sync_all():
    from .models import Invoice
    done, errors = 0, []
    if not configured():
        return done, ['Odoo is not set up.']
    o = Odoo()
    for inv in Invoice.objects.select_related('account').filter(state='unpaid').exclude(odoo_id__isnull=True, odoo_ref=''):
        try:
            before = inv.state
            sync_invoice(inv, odoo=o)
            done += int(before != inv.state)
        except Exception as e:
            errors.append(f'{inv.number}: {e}')
    return done, errors
