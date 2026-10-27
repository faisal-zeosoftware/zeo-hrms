"""v1.44.0 – partner balances, statements and credit control (Odoo: partner ledger, follow-up, credit limit).

ZEO does not keep a general ledger of its own: the invoices, receipts and bills are the apps' records. This module reads them
per business partner and works out

  receivable  what the partner owes us: Property bills (tenant / buyer / owner) less receipts and credit notes, plus refunds paid;
              Projects AMC instalments invoiced and not paid
  payable     what we owe the partner: owner statements issued, vendor invoices matched on purchase orders, broker commissions
              approved – less what was paid
  overdue     the open invoices past their due date (ageing 1-30, 31-60, 61-90, over 90 days)

Balances roll up to the company (commercial partner): a person of a company is part of the company's statement.
Each source is read only when its app's tables are there; an app that is not used simply adds nothing.
"""
import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.apps import apps as django_apps
from django.db import connection
from django.db.models import Q
from django.utils import timezone

log = logging.getLogger(__name__)
ZERO = Decimal('0.00')
OPEN_ORDER = ('confirmed', 'progress', 'delivered')


def M(label, name):
    try:
        m = django_apps.get_model(label, name)
    except LookupError:
        return None
    return m if m._meta.db_table in _tables() else None


_TABLES = {}


def _tables():
    key = connection.schema_name if hasattr(connection, 'schema_name') else ''
    if key not in _TABLES:
        _TABLES[key] = set(connection.introspection.table_names())
    return _TABLES[key]


def d2(v):
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def group_ids(partner):
    """The commercial partner and its people / addresses."""
    Partner = django_apps.get_model('ZeoMasters', 'Partner')
    cp = partner.commercial_partner
    return cp, [cp.pk] + list(Partner.objects.filter(parent_id=cp.pk).values_list('pk', flat=True))


# ------------------------------------------------------------------ the rows of every app
def _row(dt, app, doc, text, debit=ZERO, credit=ZERO, side='receivable', due=None, open_amt=ZERO, url='', partner_id=None, kind=''):
    return {'date': dt, 'due': due, 'app': app, 'doc': doc, 'text': text, 'debit': d2(debit), 'credit': d2(credit), 'side': side,
            'open': d2(open_amt), 'url': url, 'partner_id': partner_id, 'kind': kind}


def property_rows(ids):
    out = []
    Bill, Receipt, Refund = M('ZeoProperty', 'Bill'), M('ZeoProperty', 'Receipt'), M('ZeoProperty', 'Refund')
    if Bill is None:
        return out
    P = '/main-sidebar/zeo-property/r/'
    who = Q(party__partner_id__in=ids) | Q(party__isnull=True, owner__partner_id__in=ids)
    for b in Bill.objects.filter(who, status__in=('issued', 'part_paid', 'paid')).select_related('party', 'owner', 'charge'):
        pid = b.party.partner_id if b.party_id else b.owner.partner_id
        text = (b.description or b.charge.name) + (f' – {b.external_ref}' if b.external_ref else '')
        if b.is_credit:
            out.append(_row(b.due_date, 'Property', b.number, 'Credit note: ' + text, credit=b.total, url=P + f'bill/{b.pk}', partner_id=pid, kind='credit'))
        else:
            out.append(_row(b.due_date, 'Property', b.number, text, debit=b.total, due=b.due_date, open_amt=max(b.total - b.paid, ZERO),
                            url=P + f'bill/{b.pk}', partner_id=pid, kind='invoice'))
    for r in Receipt.objects.filter(who, status='posted').exclude(method='credit').select_related('party', 'owner'):
        pid = r.party.partner_id if r.party_id else r.owner.partner_id
        out.append(_row(r.received_on, 'Property', r.number, f'Receipt ({r.get_method_display()}) {r.reference}'.strip(), credit=r.amount,
                        url=P + f'receipt/{r.pk}', partner_id=pid, kind='payment'))
    if Refund is not None:
        for r in Refund.objects.filter(status='paid', party__partner_id__in=ids).select_related('party'):
            out.append(_row(r.paid_on or r.created_at.date(), 'Property', r.number, f'Refund – {r.get_kind_display()}', debit=r.amount,
                            url=P + f'refund/{r.pk}', partner_id=r.party.partner_id, kind='refund'))
    OS = M('ZeoProperty', 'OwnerStatement')
    if OS is not None:
        for s in OS.objects.filter(status__in=('issued', 'paid'), owner__partner_id__in=ids).select_related('owner'):
            out.append(_row(s.period_to, 'Property', s.number, f'Owner statement {s.period_from:%d %b} – {s.period_to:%d %b %Y}', credit=s.net, side='payable',
                            url=P + f'statement/{s.pk}', partner_id=s.owner.partner_id, kind='bill'))
            if s.status == 'paid':
                out.append(_row(s.paid_on or s.period_to, 'Property', s.number, 'Paid out to the owner', debit=s.net, side='payable',
                                partner_id=s.owner.partner_id, kind='payment'))
    PO = M('ZeoProperty', 'PurchaseOrder')
    if PO is not None:
        for p in PO.objects.filter(status__in=('invoiced', 'paid'), vendor__partner_id__in=ids).select_related('vendor'):
            amt = p.invoice_amount if p.invoice_amount is not None else p.total
            dt = p.ordered_on or p.created_at.date()
            out.append(_row(dt, 'Property', p.invoice_no or p.number, f'Vendor invoice – {p.description}', credit=amt, side='payable',
                            url=P + f'purchase_order/{p.pk}', partner_id=p.vendor.partner_id, kind='bill',
                            open_amt=amt if p.status == 'invoiced' else ZERO, due=dt))
            if p.status == 'paid':
                out.append(_row(dt, 'Property', p.invoice_no or p.number, 'Paid', debit=amt, side='payable', partner_id=p.vendor.partner_id, kind='payment'))
    CM = M('ZeoProperty', 'Commission')
    if CM is not None:
        for c in CM.objects.filter(status__in=('approved', 'paid'), broker__partner_id__in=ids).select_related('broker'):
            dt = c.created_at.date() if c.created_at else date.today()
            out.append(_row(dt, 'Property', c.number, 'Broker commission', credit=c.amount, side='payable', partner_id=c.broker.partner_id, kind='bill', url=P + f'commission/{c.pk}',
                            open_amt=c.amount if c.status == 'approved' else ZERO, due=dt))
            if c.status == 'paid':
                out.append(_row(dt, 'Property', c.number, 'Commission paid', debit=c.amount, side='payable', partner_id=c.broker.partner_id, kind='payment'))
    return out


def projects_rows(ids):
    out = []
    AB = M('ZeoProjects', 'AMCBilling')
    if AB is None:
        return out
    for b in AB.objects.filter(status__in=('invoiced', 'paid'), contract__customer__partner_id__in=ids).select_related('contract__customer'):
        dt = b.invoice_date or b.due_date
        pid = b.contract.customer.partner_id
        out.append(_row(dt, 'Projects', b.invoice_no or b.contract.number, f'AMC {b.contract.number} {b.period_label}'.strip(), debit=b.amount,
                        due=b.due_date, open_amt=b.amount if b.status == 'invoiced' else ZERO, partner_id=pid, kind='invoice',
                        url='/main-sidebar/zeo-projects/billing'))
        if b.status == 'paid':
            out.append(_row(b.paid_date or dt, 'Projects', b.invoice_no or b.contract.number, 'Paid', credit=b.amount, partner_id=pid, kind='payment'))
    return out


SOURCES = [property_rows, projects_rows]


def rows_of(ids):
    out = []
    for fn in SOURCES:
        try:
            out += fn(ids)
        except Exception:   # noqa: BLE001 – one app's problem never hides the others
            log.exception('partner ledger: %s failed', fn.__name__)
    out.sort(key=lambda r: (r['date'] or date.min, r['doc']))
    return out


def open_orders(ids):
    """Sales orders confirmed and not invoiced yet (CRM) – they count towards the credit limit (Odoo: amount to invoice)."""
    SO = M('ZeoCRM', 'SalesOrder')
    if SO is None:
        return ZERO
    return d2(sum((o.total for o in SO.objects.filter(status__in=OPEN_ORDER, account__partner_id__in=ids)), ZERO))


# ------------------------------------------------------------------ balances and statement
def ageing(rows, as_of=None):
    as_of = as_of or date.today()
    b = {'current': ZERO, '1_30': ZERO, '31_60': ZERO, '61_90': ZERO, 'over_90': ZERO}
    oldest = None
    for r in rows:
        if r['side'] != 'receivable' or r['open'] <= 0:
            continue
        days = (as_of - r['due']).days if r['due'] else 0
        k = 'current' if days <= 0 else '1_30' if days <= 30 else '31_60' if days <= 60 else '61_90' if days <= 90 else 'over_90'
        b[k] += r['open']
        if days > 0 and (oldest is None or days > oldest):
            oldest = days
    b['overdue'] = b['1_30'] + b['31_60'] + b['61_90'] + b['over_90']
    b['oldest_days'] = oldest or 0
    return b


def totals(rows):
    rec = sum((r['debit'] - r['credit'] for r in rows if r['side'] == 'receivable'), ZERO)
    pay = sum((r['credit'] - r['debit'] for r in rows if r['side'] == 'payable'), ZERO)
    return d2(rec), d2(pay)


def balances(partner, save=True):
    cp, ids = group_ids(partner)
    rows = rows_of(ids)
    rec, pay = totals(rows)
    ag = ageing(rows)
    out = {'receivable': rec, 'payable': pay, 'net': rec - pay, 'overdue': ag['overdue'], 'ageing': ag, 'open_orders': open_orders(ids),
           'credit_limit': cp.credit_limit, 'credit_hold': cp.credit_hold, 'partner': cp}
    if cp.credit_limit is not None:
        out['available'] = d2(cp.credit_limit - rec - out['open_orders'])
    if save:
        type(cp).objects.filter(pk=cp.pk).update(balance_receivable=rec, balance_payable=pay, balance_overdue=ag['overdue'], balance_at=timezone.now())
    return out


def statement(partner, date_from=None, date_to=None, side='all'):
    cp, ids = group_ids(partner)
    rows = rows_of(ids)
    if side in ('receivable', 'payable'):
        rows = [r for r in rows if r['side'] == side]
    sign = (lambda r: r['debit'] - r['credit'])   # net: + = the partner owes us
    opening = sum((sign(r) for r in rows if date_from and r['date'] and r['date'] < date_from), ZERO)
    lines, run = [], opening
    for r in rows:
        if date_from and r['date'] and r['date'] < date_from:
            continue
        if date_to and r['date'] and r['date'] > date_to:
            continue
        run += sign(r)
        lines.append(dict(r, balance=d2(run)))
    b = balances(partner)
    return {'partner': {'id': cp.pk, 'number': cp.number, 'name': cp.name, 'tax_id': cp.tax_id, 'address': cp.address, 'city': cp.city,
                        'country': cp.country, 'email': cp.email, 'currency': cp.currency, 'credit_limit': cp.credit_limit, 'credit_hold': cp.credit_hold,
                        'people': len(ids) - 1},
            'from': date_from, 'to': date_to, 'side': side, 'opening': d2(opening), 'closing': d2(run), 'lines': lines,
            'debit': d2(sum((l['debit'] for l in lines), ZERO)), 'credit': d2(sum((l['credit'] for l in lines), ZERO)),
            'balances': {k: v for k, v in b.items() if k != 'partner'}}


def refresh_all():
    Partner = django_apps.get_model('ZeoMasters', 'Partner')
    by = defaultdict(lambda: [ZERO, ZERO, ZERO])
    rows = rows_of(list(Partner.objects.values_list('pk', flat=True)))
    parents = dict(Partner.objects.filter(parent__kind='company').values_list('pk', 'parent_id'))
    for r in rows:
        pid = parents.get(r['partner_id'], r['partner_id'])
        if r['side'] == 'receivable':
            by[pid][0] += r['debit'] - r['credit']
        else:
            by[pid][1] += r['credit'] - r['debit']
    for pid, ag in _overdue_by(rows, parents).items():
        by[pid][2] = ag
    now = timezone.now()
    Partner.objects.exclude(pk__in=list(by)).exclude(balance_receivable=0, balance_payable=0, balance_overdue=0).update(
        balance_receivable=0, balance_payable=0, balance_overdue=0, balance_at=now)
    for pid, (rec, pay, od) in by.items():
        Partner.objects.filter(pk=pid).update(balance_receivable=d2(rec), balance_payable=d2(pay), balance_overdue=d2(od), balance_at=now)
    return len(by)


def _overdue_by(rows, parents):
    out = defaultdict(lambda: ZERO)
    today = date.today()
    for r in rows:
        if r['side'] == 'receivable' and r['open'] > 0 and r['due'] and r['due'] < today:
            out[parents.get(r['partner_id'], r['partner_id'])] += r['open']
    return out


# ------------------------------------------------------------------ credit control
def credit_problem(partner, amount=ZERO):
    """None when the partner may get new business on credit, else what stops it."""
    from .models import MasterSettings
    s = MasterSettings.get()
    if partner is None or s.credit_control == 'off':
        return None
    cp = partner.commercial_partner
    if cp.credit_limit is None and not cp.credit_hold and not s.credit_overdue_days:
        return None
    b = balances(cp)
    amount = d2(amount)
    reasons = []
    if cp.credit_hold:
        reasons.append(f'{cp.name} is on credit hold' + (f': {cp.credit_hold_reason}' if cp.credit_hold_reason else '') + '.')
    if cp.credit_limit is not None and b['receivable'] + b['open_orders'] + amount > cp.credit_limit:
        reasons.append(f'Balance {b["receivable"]:,.2f}' + (f' + open orders {b["open_orders"]:,.2f}' if b['open_orders'] else '')
                       + (f' + this {amount:,.2f}' if amount else '') + f' is over the credit limit {cp.credit_limit:,.2f}.')
    if s.credit_overdue_days and b['ageing']['oldest_days'] > s.credit_overdue_days:
        reasons.append(f'An invoice is {b["ageing"]["oldest_days"]} days overdue (limit {s.credit_overdue_days} days); overdue {b["overdue"]:,.2f}.')
    if not reasons:
        return None
    return {'partner': cp.pk, 'number': cp.number, 'name': cp.name, 'limit': cp.credit_limit, 'receivable': b['receivable'],
            'open_orders': b['open_orders'], 'amount': amount, 'overdue': b['overdue'], 'oldest_days': b['ageing']['oldest_days'],
            'hold': cp.credit_hold, 'mode': 'block' if (s.credit_control == 'block' or cp.credit_hold) else 'warn', 'reasons': reasons}
