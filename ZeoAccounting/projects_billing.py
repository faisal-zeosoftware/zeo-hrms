"""v1.67.14 (billing connectors): Projects tax invoices and their payments into accounting.

One journal per document, the same path as the Property documents (v1.40.0 / v1.62.0):
  * tax invoice   – Dr customer receivable / Cr project or AMC income (per line) / Cr VAT output          source prj_invoice
  * payment       – Dr bank / cash / card (by method) / Cr customer receivable                             source prj_payment
With no outside application connected the journals stay in ZEO Accounting (Accounting › Journals – export to Excel / CSV);
with Odoo or Zoho Books connected they are posted there – as a customer invoice and a customer payment matched to it when
Accounting › Settings › "Send documents as" is "Documents", else as journal vouchers. Failed posts are retried by the hourly
job (ZeoAccounting.retry_failed). Amounts in another currency are posted in AED at the document's exchange rate.
Invoices made in the accounting system (Projects › Settings › Billing: tax invoices made in = external) are not sent – they
already exist there; only their payments are (matched by the invoice number)."""
import logging
from decimal import Decimal

from django.db import transaction

from . import services as S
from .models import JournalBatch

log = logging.getLogger(__name__)

PROJECTS_ACCOUNTS = [   # (key, label, side, default account, group)
    ('receivable', 'Customer receivable (projects and AMC)', 'debit', '1200', 'Projects billing – balances'),
    ('vat_output', 'VAT output (on tax invoices)', 'credit', '2210', 'Projects billing – balances'),
    ('income_project', 'Project income (milestones)', 'credit', '4300', 'Projects billing – income'),
    ('income_amc', 'AMC / service contract income', 'credit', '4310', 'Projects billing – income'),
    ('income_other', 'Other service income (extra lines)', 'credit', '4390', 'Projects billing – income'),
    ('bank', 'Bank (transfers, cheques)', 'debit', '1010', 'Projects billing – cash and bank'),
    ('cash', 'Cash receipts', 'debit', '1000', 'Projects billing – cash and bank'),
    ('card', 'Card / online receipts (clearing)', 'debit', '1015', 'Projects billing – cash and bank'),
]
DEFAULT = {k: a for k, _, _, a, _ in PROJECTS_ACCOUNTS}
METHOD_ACCOUNT = {'cash': 'cash', 'card': 'card', 'online': 'card'}   # transfer, cheque, other → bank
SOURCES = ('prj_invoice', 'prj_payment')
PARTNER_KEYS = ('receivable',)


def projects_on():
    try:
        from ZeoApps.services import is_active
        return is_active('projects')
    except Exception:   # noqa: BLE001
        return False


class PrjBuilder(S.Builder):
    def acct(self, key):
        a, lvl = self.accts.find('projects', key, 'debit', None, None, None, '')
        if not a:
            a, lvl = self.accts.find('projects', key, 'credit', None, None, None, DEFAULT.get(key, ''))
        return a, lvl

    def put(self, key, label, amount, side, group=None, partner=None):
        a, lvl = self.acct(key)
        if a and S.q2(amount):   # which ZEO key each account plays – for invoice / payment documents in Odoo / Zoho
            self.extra.setdefault('doc', {}).setdefault('roles', {})[a] = 'receivable' if key == 'receivable' else (
                'vat_output' if key == 'vat_output' else ('bank' if key in ('bank', 'cash', 'card') else 'income_other'))
        self.add(a, label, amount, side, cost=True, level=lvl, group=group or key, partner=partner if key in PARTNER_KEYS else None)


def _aed(doc, v):
    rate = Decimal(doc.exchange_rate or 1) if (doc.currency or 'AED') != 'AED' else Decimal('1')
    return S.q2(Decimal(v or 0) * rate)


def _doc(pk):
    from ZeoProjects.models import BillDocument
    d = BillDocument.objects.select_related('customer').filter(pk=pk, kind='invoice').first()
    if d is None:
        raise S.Refused(f'Projects tax invoice #{pk} not found.', 404)
    return d


def build_prj_invoice(pk, user=None):
    doc = _doc(pk)
    if doc.status == 'cancelled':
        raise S.Refused(f'{doc.number} is cancelled.')
    if doc.external:
        raise S.Refused(f'{doc.number} was made in the accounting system – it is not sent again.')
    cust = doc.customer
    b = PrjBuilder('prj_invoice', doc.pk, f'Tax invoice {doc.number} – {cust.name}'[:200], doc.date, doc.number)
    b.put('receivable', f'{cust.name} – {doc.number}', _aed(doc, doc.total), 'debit', partner=cust.partner_id)
    rates = {}
    for ln in doc.lines.all().order_by('id'):
        key = 'income_project' if ln.milestone_id else ('income_amc' if ln.amc_billing_id else 'income_other')
        label = (ln.description or doc.number)[:200]
        b.put(key, label, _aed(doc, ln.amount), 'credit', group=f'line-{ln.pk}')
        rates[label] = float(ln.vat_rate if ln.vat_type == 'standard' else 0)   # the connectors give each line its own tax (5 % / zero / exempt)
    b.put('vat_output', f'VAT – {doc.number}', _aed(doc, doc.vat_amount), 'credit')
    b.extra = {'prj_id': doc.pk, 'doc': {**b.extra.get('doc', {}), 'kind': 'invoice', 'number': doc.number, 'date': str(doc.date),
                                         'due': str(doc.due_date or doc.date), 'party': cust.name, 'description': f'Tax invoice {doc.number}',
                                         'amount': float(_aed(doc, doc.subtotal)), 'vat': float(_aed(doc, doc.vat_amount)),
                                         'total': float(_aed(doc, doc.total)), 'customer_trn': getattr(cust, 'trn', '') or '',
                                         'line_rates': rates}}
    return b.save(user)


def build_prj_payment(pk, user=None):
    from ZeoProjects.models import BillPayment
    p = BillPayment.objects.select_related('document', 'document__customer').filter(pk=pk).first()
    if p is None:
        raise S.Refused(f'Projects payment #{pk} not found.', 404)
    doc, cust = p.document, p.document.customer
    b = PrjBuilder('prj_payment', p.pk, f'Payment {doc.number} – {cust.name}'[:200], p.date, f'{doc.number}-P{p.pk}')
    amt = _aed(doc, p.amount)
    b.put(METHOD_ACCOUNT.get(p.method, 'bank'), f'{doc.number} {p.reference or p.method}'.strip(), amt, 'debit')
    b.put('receivable', f'{cust.name} – {doc.number}', amt, 'credit', partner=cust.partner_id)
    b.extra = {'prj_id': p.pk, 'doc': {**b.extra.get('doc', {}), 'kind': 'payment', 'number': f'{doc.number}-P{p.pk}', 'date': str(p.date),
                                       'party': cust.name, 'amount': float(amt), 'method': p.method, 'reference': p.reference or '',
                                       'bills': [{'bill': doc.number, 'amount': float(amt)}]}}
    return b.save(user)


BUILD = {'prj_invoice': build_prj_invoice, 'prj_payment': build_prj_payment}


def build(source, pk, user=None):
    if not projects_on():
        raise S.Refused('The Projects app is not active in this company.')
    return BUILD[source](int(pk), user)


def batch_of(source, pk):
    return JournalBatch.objects.filter(source=source, source_id=str(pk)).exclude(status__in=('cancelled', 'reversed')).order_by('-id').first()


def sync_label(b):
    if b is None:
        return '', ''
    return {'posted': 'synced', 'exported': 'exported', 'failed': 'failed', 'posting': 'sending'}.get(b.status, 'ready' if b.status == 'ready' else 'draft'), \
        (b.external_ref or b.number or '')


def posted(batch, status='posted'):
    """After post / export / failure / reversal: the sync status and reference on the Projects invoice."""
    if batch.source != 'prj_invoice':
        return
    try:
        from ZeoProjects.models import BillDocument
        st, ref = sync_label(batch) if status != 'reversed' else ('reversed', batch.external_ref or batch.number)
        BillDocument.objects.filter(pk=int(batch.source_id)).update(sync_status=st[:12], sync_ref=(ref or '')[:100])
    except Exception:   # noqa: BLE001 – never block the posting
        log.exception('projects posted %s', batch.pk)


def _auto(source, pk):
    st = S.settings()
    if not st.auto_build:
        return None
    try:
        b = build(source, pk)
    except S.Refused as ex:
        log.info('%s %s not built: %s', source, pk, ex.message)
        return None
    posted(b, b.status)
    S.hooks_fire('journal.ready', b) if b.status == 'ready' else None
    if st.auto_post and b.status == 'ready' and st.connector in ('odoo', 'zoho'):
        transaction.on_commit(lambda: _post(b.pk))
    return b.pk


def _post(pk):
    b = JournalBatch.objects.filter(pk=pk).first()
    if b is None:
        return
    try:
        S.post(b)
    except S.Refused as ex:
        log.info('projects journal %s not posted: %s', pk, ex.message)
    posted(JournalBatch.objects.get(pk=pk))


def _undo(source, pk, user=None):
    """The invoice was cancelled / the payment deleted: reverse a posted journal, cancel one not sent yet."""
    b = batch_of(source, pk)
    if b is None:
        return
    try:
        if b.status in S.DONE:
            S.reverse(b, user)
            posted(b, 'reversed')
        else:
            S.cancel(b, user)
            if source == 'prj_invoice':
                from ZeoProjects.models import BillDocument
                BillDocument.objects.filter(pk=int(pk)).update(sync_status='cancelled', sync_ref='')
    except S.Refused as ex:
        log.info('projects journal %s not undone: %s', b.pk, ex.message)


# hooks the Projects billing calls (after commit, never blocking it)
def on_invoice(doc_id):
    transaction.on_commit(lambda: _auto('prj_invoice', doc_id))


def on_payment(pay_id):
    transaction.on_commit(lambda: _auto('prj_payment', pay_id))


def on_invoice_cancelled(doc_id, user=None):
    transaction.on_commit(lambda: _undo('prj_invoice', doc_id, user))


def on_payment_deleted(pay_id, user=None):
    transaction.on_commit(lambda: _undo('prj_payment', pay_id, user))


def candidates(days=120):
    """Projects invoices and payments of the last days, for Accounting › Post (same shape as the Property rows)."""
    from datetime import timedelta
    from ZeoProjects.models import BillDocument, BillPayment
    from ZeoProjects.services import local_today
    since = local_today() - timedelta(days=days)
    have = {(b.source, b.source_id): {'id': b.pk, 'number': b.number, 'status': b.status}
            for b in JournalBatch.objects.filter(source__in=SOURCES).exclude(status__in=('cancelled', 'reversed'))}
    inv = [{'id': d.pk, 'number': d.number, 'date': d.date, 'label': f'Tax invoice {d.number}', 'party': d.customer.name, 'amount': f'{_aed(d, d.total):.2f}',
            'status': d.sync_status or d.status, 'journal': have.get(('prj_invoice', str(d.pk)))}
           for d in BillDocument.objects.select_related('customer').filter(kind='invoice', external=False, date__gte=since).exclude(status='cancelled').order_by('-date', '-id')]
    pay = [{'id': p.pk, 'number': f'{p.document.number}-P{p.pk}', 'date': p.date, 'label': f'Payment {p.document.number} – {p.method}',
            'party': p.document.customer.name, 'amount': f'{_aed(p.document, p.amount):.2f}', 'status': '', 'journal': have.get(('prj_payment', str(p.pk)))}
           for p in BillPayment.objects.select_related('document', 'document__customer').filter(date__gte=since).order_by('-date', '-id')]
    return {'invoices': inv, 'payments': pay}
