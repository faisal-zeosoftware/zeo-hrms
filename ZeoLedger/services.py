"""ZEO Accounting (v1.76.0) – the ledger engine: posting, reversal, balances, invoices / bills, payments and allocation, post-dated
cheques, bank reconciliation, exchange revaluation, year end, VAT 201, corporate tax, the outbox of the other ZEO apps."""
import calendar
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (GROUP_TYPE, Allocation, BankLine, BankStatement, CostCentre, CtReturn, Document, DocumentLine, Entry, EntryLine, FiscalYear,
                     FxRate, GLAccount, Journal, LedgerSettings, MODULES, Payment, Period, PostingRight, Recurring, Tax, VatReturn)
from django.utils.timezone import localdate as today_   # v1.79.3

log = logging.getLogger(__name__)
D0 = Decimal('0')
CENT = Decimal('0.01')

# account roles (default codes of the standard chart; a company changes them in the set-up)
ROLE_CODES = {'receivable': '1200', 'payable': '2100', 'vat_out': '2210', 'vat_in': '1350', 'vat_settle': '2220', 'pdc_in': '1250', 'pdc_out': '2250',
              'retained': '3200', 'opening': '3900', 'fx_gain': '4910', 'fx_loss': '6910', 'rounding': '6990', 'suspense': '1999',
              'ct_payable': '2230', 'ct_expense': '8900', 'revenue': '4000', 'expense': '6000'}
ROLE_LABELS = {'receivable': 'Customer balances (receivable)', 'payable': 'Supplier balances (payable)', 'vat_out': 'VAT output', 'vat_in': 'VAT recoverable (input)',
               'vat_settle': 'VAT payable to the FTA (return)', 'pdc_in': 'Post-dated cheques received', 'pdc_out': 'Post-dated cheques issued',
               'retained': 'Retained earnings', 'opening': 'Opening balance equity', 'fx_gain': 'Exchange gain', 'fx_loss': 'Exchange loss',
               'rounding': 'Rounding difference', 'suspense': 'Suspense', 'ct_payable': 'Corporate tax payable', 'ct_expense': 'Corporate tax expense',
               'revenue': 'Default sales account', 'expense': 'Default purchase account'}
VAT_BOX_LABELS = [
    ('1a', 'Standard rated supplies in Abu Dhabi'), ('1b', 'Standard rated supplies in Dubai'), ('1c', 'Standard rated supplies in Sharjah'),
    ('1d', 'Standard rated supplies in Ajman'), ('1e', 'Standard rated supplies in Umm Al Quwain'), ('1f', 'Standard rated supplies in Ras Al Khaimah'),
    ('1g', 'Standard rated supplies in Fujairah'), ('2', 'Tax refunds provided to tourists'), ('3', 'Supplies subject to the reverse charge provisions'),
    ('4', 'Zero rated supplies'), ('5', 'Exempt supplies'), ('6', 'Goods imported into the UAE'), ('7', 'Adjustments to goods imported into the UAE'),
    ('8', 'Totals'), ('9', 'Standard rated expenses'), ('10', 'Supplies subject to the reverse charge provisions (recoverable)'), ('11', 'Totals'),
    ('12', 'Total value of due tax for the period'), ('13', 'Total value of recoverable tax for the period'), ('14', 'Payable tax for the period'),
]
EMIRATE_BOX = {'AZ': '1a', 'DU': '1b', 'SH': '1c', 'AJ': '1d', 'UQ': '1e', 'RK': '1f', 'FU': '1g'}
CT_RATE, CT_THRESHOLD, SBR_REVENUE, SBR_LAST = Decimal('0.09'), Decimal('375000'), Decimal('3000000'), date(2029, 12, 31)


class LedgerError(Exception):
    def __init__(self, message, field=None):
        super().__init__(message)
        self.message, self.field = message, field


def must(cond, message, field=None):
    if not cond:
        raise LedgerError(message, field)


def r2(x):
    return Decimal(x or 0).quantize(CENT, rounding=ROUND_HALF_UP)


def settings():
    return LedgerSettings.get()


def account(role):
    """the GL account of a role (set-up › Accounts, else the standard code)"""
    code = (settings().accounts or {}).get(role) or ROLE_CODES.get(role)
    a = GLAccount.objects.filter(code=code).first() if code else None
    must(a is not None, f'The account for "{ROLE_LABELS.get(role, role)}" ({code}) is missing – add it to the chart of accounts or choose another in the set-up.')
    return a


def base_currency():
    return (settings().currency or 'AED').upper()


def fx(cur, on):
    """1 unit of cur in the company currency on a date (the latest rate up to the date)"""
    if not cur or cur.upper() == base_currency():
        return Decimal('1')
    r = FxRate.objects.filter(currency=cur.upper(), date__lte=on).order_by('-date').first()
    must(r is not None, f'Enter the exchange rate of {cur.upper()} on or before {on:%d/%m/%Y} (Accounting › Exchange rates).', 'rate')
    return r.rate


# ------------------------------------------------------------------ periods
def fiscal_year_of(on, make=True):
    y = FiscalYear.objects.filter(start__lte=on, end__gte=on).first()
    if y is None and make:
        y = make_year(on)
    return y


def add_months(d0, n):
    y, m = divmod(d0.month - 1 + n, 12)
    y, m = d0.year + y, m + 1
    return date(y, m, min(d0.day, calendar.monthrange(y, m)[1]))


def year_start_of(on):
    """v1.79.3 the start of the financial year holding the date – start month and day (Sage 200 Evolution style)"""
    st = settings()
    m, dd = st.fy_start_month or 1, min(max(st.fy_start_day or 1, 1), 28)
    start = date(on.year, m, dd)
    return start if on >= start else date(on.year - 1, m, dd)


def build_periods(y):
    """twelve periods from the year start; a short first year stops at its end, the last period always ends on the year end"""
    if y.periods.exists():
        return
    for i in range(12):
        ps = add_months(y.start, i)
        if ps > y.end:
            break
        pe = add_months(y.start, i + 1) - timedelta(days=1)
        if i == 11 or pe >= y.end:
            pe = y.end
        Period.objects.create(year=y, number=i + 1, start=ps, end=pe)
        if pe == y.end:
            break


def make_year(on):
    start = year_start_of(on)
    end = add_months(start, 12) - timedelta(days=1)
    sy = start.year
    name = str(sy) if (start.month, start.day) == (1, 1) else f'{sy}-{str(sy + 1)[-2:]}'
    y = FiscalYear.objects.filter(start__lte=on, end__gte=on).first()
    if y is None:
        y, _ = FiscalYear.objects.get_or_create(name=name, defaults={'start': start, 'end': end})
    build_periods(y)
    return y


def module_of(e):
    """v1.79.3 which module an entry belongs to, for module locks and user rights"""
    if e.source == 'invoice':
        return 'ap' if getattr(e.journal, 'kind', '') == 'purchase' else 'ar'
    if e.source in ('payment', 'bank'):
        return 'bank'
    if e.source == 'outbox':
        return 'apps'
    if e.source == 'closing':
        return None      # year end, VAT return, corporate tax: the finance manager's own closing work
    return 'gl'


MODULE_LABEL = dict(MODULES)


def posting_right(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return None
    return PostingRight.objects.filter(user=user, is_active=True).first()


def check_user_right(r, on, module, p):
    if r is None:
        return
    lab = MODULE_LABEL.get(module, '').split(' (')[0].lower()
    if module and getattr(r, f'lock_{module}', False):
        raise LedgerError(f'You may not post in {lab} – ask the finance manager.', 'date')
    if r.date_from and on < r.date_from:
        raise LedgerError(f'You may post from {r.date_from:%d/%m/%Y} only.', 'date')
    if r.date_to and on > r.date_to:
        raise LedgerError(f'You may post up to {r.date_to:%d/%m/%Y} only.', 'date')
    if p is None or (r.periods_back is None and r.periods_ahead is None):
        return
    now = Period.objects.filter(start__lte=today_(), end__gte=today_()).first()
    if now is None:
        return
    if p.start < now.start and r.periods_back is not None:
        n = Period.objects.filter(start__gte=p.start, start__lt=now.start).count()
        must(n <= r.periods_back, f'You may post up to {r.periods_back} period(s) back – {p} is {n} back.', 'date')
    if p.start > now.start and r.periods_ahead is not None:
        n = Period.objects.filter(start__gt=now.start, start__lte=p.start).count()
        must(n <= r.periods_ahead, f'You may post up to {r.periods_ahead} period(s) ahead – {p} is {n} ahead.', 'date')


def check_open(on, manager=False, prior_year=False, module='gl', user=None):
    st = settings()
    if st.lock_date and on <= st.lock_date:
        raise LedgerError(f'The books are locked up to {st.lock_date:%d/%m/%Y} – choose a later date.', 'date')
    y = fiscal_year_of(on)
    if y.status != 'open':   # v1.79.3 prior-year posting (Sage 300 style)
        if not st.prior_year_posting:
            raise LedgerError(f'The financial year {y.name} is closed. To post an adjustment into it, switch on "Allow posting to closed years" '
                              f'in Accounting setup, or reopen the year.', 'date')
        must(prior_year, f'The financial year {y.name} is closed – only the finance manager may post a journal entry into it.', 'date')
    p = y.periods.filter(start__lte=on, end__gte=on).first()
    r = posting_right(user) if module else None
    free = bool(r and r.allow_locked)
    if p is not None:
        must(p.status != 'locked' or free, f'{p} is locked.', 'date')
        if module and getattr(p, f'lock_{module}', False) and not free:
            raise LedgerError(f'{p} is locked for {MODULE_LABEL[module].split(" (")[0].lower()}.', 'date')
        must(p.status == 'open' or manager or free, f'{p} is closed – only the finance manager may post in it.', 'date')
    check_user_right(r, on, module, p)


# ------------------------------------------------------------------ entries
def next_number(prefix):
    from .app import app
    return app.next_number(prefix, prefix)


def make_entry(journal, on, lines, *, narration='', reference='', source='manual', source_ref='', partner=None, branch=None, currency='',
               rate=None, user=None, post=True, manager=False, auto_reverse=None, prior_year=False):
    """lines: [{account, debit, credit, partner, cost_centre, description, amount_currency, tax, tax_base, vat_box, emirate, due_date, doc}]"""
    e = Entry.objects.create(journal=journal, date=on, narration=narration[:255], reference=reference[:80], source=source, source_ref=str(source_ref)[:60],
                             partner=partner, branch=branch, currency=currency or '', rate=rate, created_by=user, auto_reverse=auto_reverse)
    for ln in lines:
        if not ln.get('debit') and not ln.get('credit'):
            continue
        EntryLine.objects.create(entry=e, account=ln['account'], partner=ln.get('partner', partner), cost_centre=ln.get('cost_centre'),
                                 branch=ln.get('branch', branch), description=(ln.get('description') or narration)[:255],
                                 debit=r2(ln.get('debit')), credit=r2(ln.get('credit')), currency=ln.get('currency', currency) or '',
                                 amount_currency=r2(ln.get('amount_currency')), tax=ln.get('tax'), tax_base=ln.get('tax_base'),
                                 vat_box=ln.get('vat_box', ''), emirate=ln.get('emirate', ''), due_date=ln.get('due_date'), doc=ln.get('doc'), date=on)
    if post:
        post_entry(e, user, manager=manager, prior_year=prior_year)
    return e


@transaction.atomic
def post_entry(e, user=None, manager=False, prior_year=False):
    must(e.status == 'draft', 'Only a draft can be posted.')
    check_open(e.date, manager, prior_year, module_of(e), user)
    lines = list(e.lines.select_related('account'))
    must(lines, 'Add the lines of the entry.')
    dr = sum((ln.debit for ln in lines), D0)
    cr = sum((ln.credit for ln in lines), D0)
    diff = r2(dr - cr)
    if diff and abs(diff) <= Decimal('0.05') * max(1, len(lines)) and e.source != 'manual':   # rounding of converted amounts
        ra = account('rounding')
        EntryLine.objects.create(entry=e, account=ra, description='Rounding', debit=-diff if diff < 0 else 0, credit=diff if diff > 0 else 0, date=e.date)
        lines = list(e.lines.select_related('account'))
        dr = sum((ln.debit for ln in lines), D0)
        cr = sum((ln.credit for ln in lines), D0)
    must(r2(dr) == r2(cr), f'The entry does not balance: debit {r2(dr):,.2f}, credit {r2(cr):,.2f} (difference {r2(dr - cr):,.2f}).')
    for ln in lines:
        a = ln.account
        must(not a.is_heading, f'{a} is a heading – post to an account under it.')
        must(a.is_active, f'{a} is not active.')
        must(not (ln.debit and ln.credit), 'A line is either a debit or a credit.')
        must(ln.debit >= 0 and ln.credit >= 0, 'Amounts are positive – use the other column.')
        if a.cost_centre_required:
            must(ln.cost_centre_id, f'{a} needs a cost centre on each line.')
        if a.control in ('receivable', 'payable') and e.source == 'manual':
            must(ln.partner_id, f'{a} is a control account – choose the customer or supplier on the line.')
    if not e.number:
        e.number = next_number(e.journal.code)
    e.status, e.total, e.posted_at, e.posted_by = 'posted', r2(dr), timezone.now(), user
    e.save()
    e.lines.update(posted=True, date=e.date)
    roll_forward(e, user)   # v1.79.3
    return e


@transaction.atomic
def reverse_entry(e, on=None, user=None, narration='', prior_year=False):
    must(e.status == 'posted', 'Only a posted entry can be reversed.')
    on = on or e.date
    r = make_entry(e.journal, on, [{'account': ln.account, 'debit': ln.credit, 'credit': ln.debit, 'partner': ln.partner, 'cost_centre': ln.cost_centre,
                                    'branch': ln.branch, 'description': ln.description, 'currency': ln.currency, 'amount_currency': -ln.amount_currency,
                                    'tax': ln.tax, 'tax_base': -(ln.tax_base or 0) if ln.tax_base is not None else None, 'vat_box': ln.vat_box,
                                    'emirate': ln.emirate, 'doc': ln.doc} for ln in e.lines.all()],
                   narration=narration or f'Reversal of {e.number}', reference=e.number, source=e.source, source_ref=e.source_ref, partner=e.partner,
                   branch=e.branch, currency=e.currency, rate=e.rate, user=user, post=False)
    r.reversal_of = e
    r.save(update_fields=['reversal_of'])
    post_entry(r, user, manager=True, prior_year=prior_year)
    e.status = 'reversed'
    e.save(update_fields=['status'])
    for ln in e.lines.exclude(match=''):
        EntryLine.objects.filter(match=ln.match).update(match='')
    return r


# ------------------------------------------------------------------ balances
def lines(date_from=None, date_to=None, branch=None, cost_centre=None):
    qs = EntryLine.objects.filter(posted=True)
    if date_from:
        qs = qs.filter(date__gte=date_from)
    if date_to:
        qs = qs.filter(date__lte=date_to)
    if branch:
        qs = qs.filter(branch_id=branch)
    if cost_centre:
        qs = qs.filter(cost_centre_id=cost_centre)
    return qs


def balance(acc, date_to=None, date_from=None, **kw):
    a = lines(date_from, date_to, **kw).filter(account=acc).aggregate(d=Sum('debit'), c=Sum('credit'))
    return r2((a['d'] or 0) - (a['c'] or 0))


def trial_balance(date_from, date_to, **kw):
    """per account: opening (before date_from; income / expense from the start of the year), movement, closing"""
    y = fiscal_year_of(date_from)
    rows = []
    op = {r['account']: r for r in lines(None, date_from - timedelta(days=1), **kw).values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    op_pl = {r['account']: r for r in lines(y.start, date_from - timedelta(days=1), **kw).values('account').annotate(d=Sum('debit'), c=Sum('credit'))} if y else {}
    mv = {r['account']: r for r in lines(date_from, date_to, **kw).values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    for a in GLAccount.objects.filter(is_heading=False).order_by('code'):
        src = op_pl if a.type in ('income', 'expense') else op
        o = src.get(a.pk) or {}
        m = mv.get(a.pk) or {}
        opening = r2((o.get('d') or 0) - (o.get('c') or 0))
        md, mc = r2(m.get('d') or 0), r2(m.get('c') or 0)
        closing = opening + md - mc
        if opening or md or mc:
            rows.append({'id': a.pk, 'code': a.code, 'name': a.name, 'type': a.type, 'group': a.group, 'opening': opening, 'debit': md, 'credit': mc,
                         'closing': closing})
    # earlier years' profit not yet closed into retained earnings
    if y:
        prior = lines(None, y.start - timedelta(days=1), **kw).filter(account__type__in=('income', 'expense')).aggregate(d=Sum('debit'), c=Sum('credit'))
        pr = r2((prior['d'] or 0) - (prior['c'] or 0))
        if pr:
            rows.append({'id': None, 'code': '', 'name': 'Profit of earlier years not closed', 'type': 'equity', 'group': 'retained', 'opening': pr,
                         'debit': D0, 'credit': D0, 'closing': pr})
    return rows


def profit_and_loss(date_from, date_to, **kw):
    sec = defaultdict(lambda: D0)
    rows = []
    for r in lines(date_from, date_to, **kw).filter(account__type__in=('income', 'expense')).values('account__code', 'account__name', 'account__group',
                                                                                                  'account__type', 'account_id').annotate(d=Sum('debit'), c=Sum('credit')).order_by('account__code'):
        amt = r2((r['c'] or 0) - (r['d'] or 0)) if r['account__type'] == 'income' else r2((r['d'] or 0) - (r['c'] or 0))
        sec[r['account__group']] += amt
        rows.append({'id': r['account_id'], 'code': r['account__code'], 'name': r['account__name'], 'group': r['account__group'], 'type': r['account__type'], 'amount': amt})
    revenue = sec['revenue']
    cos = sec['cost_of_sales']
    gross = revenue - cos
    opex = sec['expense'] + sec['depreciation']
    operating = gross + sec['other_income'] - opex
    before_tax = operating - sec['finance_cost'] - sec['other_expense']
    net = before_tax - sec['income_tax']
    return {'rows': rows, 'revenue': revenue, 'cost_of_sales': cos, 'gross': gross, 'other_income': sec['other_income'], 'operating_expenses': opex,
            'operating': operating, 'finance_cost': sec['finance_cost'], 'other_expense': sec['other_expense'], 'before_tax': before_tax,
            'income_tax': sec['income_tax'], 'net': net}


def balance_sheet(on, **kw):
    y = fiscal_year_of(on)
    rows = []
    sums = defaultdict(lambda: D0)
    for r in lines(None, on, **kw).filter(account__type__in=('asset', 'liability', 'equity')).values('account_id', 'account__code', 'account__name', 'account__group',
                                                                                                     'account__type').annotate(d=Sum('debit'), c=Sum('credit')).order_by('account__code'):
        amt = r2((r['d'] or 0) - (r['c'] or 0))
        if r['account__type'] != 'asset':
            amt = -amt
        if not amt:
            continue
        sums[r['account__type']] += amt
        rows.append({'id': r['account_id'], 'code': r['account__code'], 'name': r['account__name'], 'group': r['account__group'], 'type': r['account__type'], 'amount': amt})
    pl = lines(None, on, **kw).filter(account__type__in=('income', 'expense')).aggregate(d=Sum('debit'), c=Sum('credit'))
    unclosed = r2((pl['c'] or 0) - (pl['d'] or 0))
    if unclosed:
        rows.append({'id': None, 'code': '', 'name': 'Profit for the year (not yet closed)', 'group': 'retained', 'type': 'equity', 'amount': unclosed})
        sums['equity'] += unclosed
    return {'rows': rows, 'assets': sums['asset'], 'liabilities': sums['liability'], 'equity': sums['equity'],
            'difference': r2(sums['asset'] - sums['liability'] - sums['equity']), 'year': y.name if y else ''}


def cash_flow(date_from, date_to, **kw):
    """indirect method from the movements of the balance-sheet groups"""
    pl = profit_and_loss(date_from, date_to, **kw)
    mv = defaultdict(lambda: D0)
    for r in lines(date_from, date_to, **kw).filter(account__type__in=('asset', 'liability', 'equity')).values('account__group').annotate(d=Sum('debit'), c=Sum('credit')):
        mv[r['account__group']] += r2((r['d'] or 0) - (r['c'] or 0))
    dep = lines(date_from, date_to, **kw).filter(account__group='depreciation').aggregate(d=Sum('debit'), c=Sum('credit'))
    depreciation = r2((dep['d'] or 0) - (dep['c'] or 0))
    working = -(mv['receivable'] + mv['current_asset'] + mv['inventory'] + mv['prepayment']) - (mv['payable'] + mv['current_liability'] + mv['tax'])
    operating = pl['net'] + depreciation + working
    investing = -(mv['fixed_asset'] + mv['non_current_asset']) - mv['accum_depr'] - depreciation
    financing = -(mv['non_current_liability'] + mv['equity'] + mv['retained'])
    cash = mv['cash'] + mv['bank']
    return {'profit': pl['net'], 'depreciation': depreciation, 'working_capital': working, 'operating': operating, 'investing': investing,
            'financing': financing, 'net_change': cash, 'check': r2(operating + investing + financing - cash)}


# ------------------------------------------------------------------ invoices and bills
def doc_sign(doc):
    return -1 if doc.kind in ('credit', 'debit') else 1


def calc_doc(doc):
    net = vat = D0
    for ln in doc.lines.select_related('tax'):
        ln.net = r2(Decimal(ln.qty) * Decimal(ln.price) * (1 - Decimal(ln.discount or 0) / 100))
        t = ln.tax
        ln.vat = r2(ln.net * t.rate / 100) if t is not None and t.treatment in ('SR', 'BL') else D0
        ln.save(update_fields=['net', 'vat'])
        net += ln.net
        vat += ln.vat
    doc.net, doc.vat, doc.total = r2(net), r2(vat), r2(net + vat)
    if doc.status == 'draft':
        doc.balance = doc.total
    doc.save(update_fields=['net', 'vat', 'total', 'balance'])
    return doc


def journal_of(kind):
    k = 'sale' if kind in ('invoice', 'credit') else 'purchase'
    j = Journal.objects.filter(kind=k, is_active=True).order_by('id').first()
    must(j is not None, f'Add a {"sales" if k == "sale" else "purchases"} journal (Accounting › Journals).')
    return j


@transaction.atomic
def post_doc(doc, user=None):
    """invoice: Dr receivable / Cr revenue + VAT output; bill: Dr expense + VAT input / Cr payable; reverse charge: Dr VAT input / Cr VAT output (notional)"""
    must(doc.status == 'draft', 'Only a draft can be posted.')
    calc_doc(doc)
    must(doc.lines.exists() and doc.total, 'Add the lines.')
    sale = doc.sale
    sign = doc_sign(doc)
    st = settings()
    cur = (doc.currency or '').upper()
    rate = Decimal(doc.rate) if doc.rate else fx(cur, doc.date)
    doc.rate = rate if cur and cur != base_currency() else None
    emirate = doc.emirate or st.emirate
    out = []

    def side(amount_base, is_debit_normal):
        """for the document's normal direction (invoice: credit the revenue); a credit note flips it"""
        amount_base = r2(amount_base) * sign
        if is_debit_normal:
            return (amount_base, D0) if amount_base >= 0 else (D0, -amount_base)
        return (D0, amount_base) if amount_base >= 0 else (-amount_base, D0)

    total_base = D0
    for ln in doc.lines.select_related('tax', 'account', 'product'):
        acc = ln.account or (ln.product and _product_account(ln.product, sale)) or account('revenue' if sale else 'expense')
        net_b = r2(ln.net * rate)
        vat_b = r2(ln.vat * rate)
        t = ln.tax
        tr = t.treatment if t else ('OS' if not st.vat_registered else 'SR')
        box = ''
        if sale:
            box = EMIRATE_BOX.get(emirate, '1b') if tr == 'SR' else {'ZR': '4', 'EX': '5'}.get(tr, '')
        else:
            box = {'SR': '9', 'RC': '10', 'IM': '10'}.get(tr, '')
        blocked = (not sale) and tr == 'BL'
        dr, cr = side(net_b + (vat_b if blocked else 0), not sale)
        out.append({'account': acc, 'debit': dr, 'credit': cr, 'cost_centre': ln.cost_centre, 'description': ln.description or str(ln.product or ''),
                    'tax': t, 'tax_base': r2(net_b * sign), 'vat_box': box, 'emirate': emirate if sale else '', 'currency': cur, 'amount_currency': r2(ln.net * sign)})
        total_base += net_b + vat_b
        if vat_b and not blocked:
            va = (t.account if t and t.account_id else None) or account('vat_out' if sale else 'vat_in')
            rec = (t.recoverable if t else Decimal(100)) / 100 if not sale else 1
            vr = r2(vat_b * rec)
            dr, cr = side(vr, not sale)
            out.append({'account': va, 'debit': dr, 'credit': cr, 'description': f'VAT {t.rate:g} %' if t else 'VAT', 'tax': t, 'vat_box': box,
                        'emirate': emirate if sale else ''})
            if vr != vat_b:   # the part not recoverable is a cost
                dr, cr = side(vat_b - vr, True)
                out.append({'account': acc, 'debit': dr, 'credit': cr, 'description': 'VAT not recoverable', 'cost_centre': ln.cost_centre})
        if not sale and tr in ('RC', 'IM') and t is not None:   # reverse charge: the notional VAT both ways
            rc = r2(net_b * t.rate / 100)
            if rc:
                dr, cr = side(rc, True)
                out.append({'account': t.account or account('vat_in'), 'debit': dr, 'credit': cr, 'description': 'VAT reverse charge (recoverable)', 'tax': t, 'vat_box': '10'})
                dr, cr = side(rc, False)
                out.append({'account': t.rc_account or account('vat_out'), 'debit': dr, 'credit': cr, 'description': 'VAT reverse charge (due)', 'tax': t,
                            'vat_box': '3' if tr == 'RC' else '6'})
    ctl = account('receivable' if sale else 'payable')
    dr, cr = side(r2(total_base), sale)
    out.insert(0, {'account': ctl, 'debit': dr, 'credit': cr, 'partner': doc.partner, 'due_date': doc.due_date or doc.date, 'doc': doc,
                   'description': f'{doc.get_kind_display()} {doc.supplier_ref or ""}'.strip(), 'currency': cur, 'amount_currency': r2(doc.total * sign)})
    if not doc.number:
        prefix = {'invoice': 'INV', 'credit': 'CN', 'bill': 'BILL', 'debit': 'DBN'}[doc.kind]
        doc.number = next_number(prefix)
    j = doc.journal or journal_of(doc.kind)
    e = make_entry(j, doc.date, out, narration=f'{doc.get_kind_display()} {doc.number} – {doc.partner}', reference=doc.number, source='invoice',
                   source_ref=doc.pk, partner=doc.partner, branch=doc.branch, currency=cur if cur != base_currency() else '', rate=doc.rate, user=user)
    doc.entry, doc.status, doc.total_base, doc.balance = e, 'posted', r2(total_base), doc.total
    if st.einvoicing and sale:
        doc.einv_type = doc.einv_type or ('381' if doc.kind == 'credit' else '380')
        doc.einv_status = 'ready'
        if not doc.einv_uuid:
            import uuid
            doc.einv_uuid = str(uuid.uuid4())
    doc.save()
    if doc.original_id and doc.kind in ('credit', 'debit'):   # a note settles its original invoice / bill first
        _apply_note(doc)
    return doc


def _product_account(p, sale):
    for attr in (('income_account', 'revenue_account') if sale else ('expense_account', 'cost_account')):
        code = getattr(p, attr, '') or ''
        if code:
            a = GLAccount.objects.filter(code=getattr(code, 'code', code)).first()
            if a:
                return a
    return None


def _apply_note(note):
    o = note.original
    amt = min(note.total, o.balance)
    if amt <= 0:
        return
    o.balance = r2(o.balance - amt)
    o.status = 'paid' if o.balance <= 0 else 'part_paid'
    o.save(update_fields=['balance', 'status'])
    note.balance = r2(note.total - amt)
    note.status = 'paid' if note.balance <= 0 else 'part_paid'
    note.save(update_fields=['balance', 'status'])


@transaction.atomic
def cancel_doc(doc, user=None, on=None):
    must(doc.status in ('posted',), 'Only a posted document that nothing was paid on can be cancelled.')
    must(not doc.allocations.exists(), 'Payments were allocated to it – reverse them first, or issue a credit note.')
    if doc.entry_id:
        reverse_entry(doc.entry, on or doc.date, user, f'Cancelled {doc.number}')
    doc.status, doc.balance = 'cancelled', D0
    doc.save(update_fields=['status', 'balance'])
    return doc


# ------------------------------------------------------------------ payments
@transaction.atomic
def post_payment(p, user=None):
    """receipt: Dr bank (or PDC received) / Cr receivable; payment: Dr payable / Cr bank (or PDC issued); transfer: Dr to-bank / Cr bank.
    Allocations settle the invoices / bills; the exchange difference between the invoice rate and the payment rate is realised."""
    must(p.status == 'draft', 'Only a draft can be posted.')
    must(p.amount and p.amount > 0, 'Enter the amount.', 'amount')
    must(p.journal.account_id, f'{p.journal} has no bank / cash account.')
    cur = (p.currency or p.journal.currency or '').upper()
    rate = Decimal(p.rate) if p.rate else fx(cur, p.date)
    base = r2(p.amount * rate)
    pdc = p.method == 'cheque' and p.cheque_date and p.cheque_date > p.date
    if p.kind == 'transfer':
        must(p.to_journal_id and p.to_journal.account_id, 'Choose the bank / cash account to transfer to.', 'to_journal')
        lines_ = [{'account': p.to_journal.account, 'debit': base, 'description': f'Transfer from {p.journal.code}'},
                  {'account': p.journal.account, 'credit': base, 'description': f'Transfer to {p.to_journal.code}'}]
        e = make_entry(p.journal, p.date, lines_, narration=f'Transfer {p.journal.code} → {p.to_journal.code}', reference=p.reference, source='payment',
                       source_ref=p.pk, user=user, branch=p.branch)
        p.number = p.number or next_number('TRF')
        p.entry, p.status = e, 'posted'
        p.save()
        return p
    must(p.partner_id, 'Choose the customer or supplier.', 'partner')
    sale = p.kind == 'receipt'
    ctl = account('receivable' if sale else 'payable')
    if pdc:
        # v1.79.3 the bank's PDC account is the cheques RECEIVED (an asset); a cheque issued goes to "Post-dated cheques issued" (a liability)
        bank_acc = (p.journal.pdc_account or account('pdc_in')) if sale else account('pdc_out')
    else:
        bank_acc = p.journal.account
    allocated = D0
    fx_total = D0
    ctl_lines = []
    for a in p.allocations.select_related('doc'):
        doc = a.doc
        must(doc.status in ('posted', 'part_paid'), f'{doc} is not open.')
        must(doc.partner_id == p.partner_id, f'{doc} is of another partner.')
        must((doc.kind in ('invoice', 'debit')) == sale or (doc.kind in ('credit', 'bill')) != sale, f'{doc} cannot be settled by a {p.get_kind_display().lower()}.')
        amt = r2(a.amount)
        must(0 < amt <= doc.balance + Decimal('0.005'), f'{doc}: at most {doc.balance:,.2f} is open.')
        doc_rate = Decimal(doc.rate) if doc.rate else Decimal(1)
        at_doc = r2(amt * doc_rate)
        at_pay = r2(amt * rate)
        a.fx_diff = r2(at_pay - at_doc) if sale else r2(at_doc - at_pay)
        a.save(update_fields=['fx_diff'])
        fx_total += a.fx_diff
        ctl_lines.append({'account': ctl, 'debit': D0 if sale else at_doc, 'credit': at_doc if sale else D0, 'partner': p.partner, 'doc': doc,
                          'description': f'{doc.number}', 'currency': cur, 'amount_currency': -amt if sale else amt})
        doc.balance = r2(doc.balance - amt)
        doc.status = 'paid' if doc.balance <= Decimal('0.005') else 'part_paid'
        doc.save(update_fields=['balance', 'status'])
        allocated += amt
    must(allocated <= p.amount + Decimal('0.005'), 'The allocations are more than the amount.')
    rest = r2(p.amount - allocated)
    if rest:   # on account (advance) – stays open on the partner
        ctl_lines.append({'account': ctl, 'debit': D0 if sale else r2(rest * rate), 'credit': r2(rest * rate) if sale else D0, 'partner': p.partner,
                          'description': 'On account', 'currency': cur, 'amount_currency': -rest if sale else rest})
    lines_ = [{'account': bank_acc, 'debit': base if sale else D0, 'credit': D0 if sale else base, 'description': p.reference or p.cheque_no,
               'currency': cur, 'amount_currency': p.amount if sale else -p.amount}] + ctl_lines
    if fx_total:
        if fx_total > 0:
            lines_.append({'account': account('fx_gain'), 'credit': fx_total, 'description': 'Realised exchange gain'})
        else:
            lines_.append({'account': account('fx_loss'), 'debit': -fx_total, 'description': 'Realised exchange loss'})
    p.number = p.number or next_number('RCT' if sale else 'PAY')
    e = make_entry(p.journal, p.date, lines_, narration=f'{p.get_kind_display()} {p.number} – {p.partner}', reference=p.reference or p.cheque_no,
                   source='payment', source_ref=p.pk, partner=p.partner, branch=p.branch, currency=cur if cur != base_currency() else '', rate=rate, user=user)
    p.entry, p.unallocated, p.status = e, rest, ('pdc' if pdc else 'posted')
    p.rate = rate if cur and cur != base_currency() else None
    p.save()
    return p


def _pdc_account_of(p, sale):
    """the PDC account the cheque was booked to (v1.79.3: issued cheques to "Post-dated cheques issued"; a cheque issued before
    1.79.3 that went to the bank's PDC account is cleared from there)"""
    if sale:
        return p.journal.pdc_account or account('pdc_in')
    out = account('pdc_out')
    if p.entry_id and p.journal.pdc_account_id and not p.entry.lines.filter(account=out).exists() and p.entry.lines.filter(account_id=p.journal.pdc_account_id).exists():
        return p.journal.pdc_account
    return out


@transaction.atomic
def clear_pdc(p, on=None, user=None):
    """the cheque is cleared on its date: Dr bank / Cr PDC received (receipt) – Dr PDC issued / Cr bank (payment)"""
    must(p.status == 'pdc', 'Only a held post-dated cheque can be cleared.')
    on = on or p.cheque_date
    base = r2(p.amount * (p.rate or 1))
    sale = p.kind == 'receipt'
    pdc_acc = _pdc_account_of(p, sale)
    lines_ = [{'account': p.journal.account, 'debit': base if sale else D0, 'credit': D0 if sale else base, 'description': f'Cheque {p.cheque_no} cleared'},
              {'account': pdc_acc, 'debit': D0 if sale else base, 'credit': base if sale else D0, 'description': f'Cheque {p.cheque_no} cleared'}]
    e = make_entry(p.journal, on, lines_, narration=f'PDC {p.cheque_no} cleared ({p.number})', reference=p.cheque_no, source='payment', source_ref=p.pk,
                   partner=p.partner, branch=p.branch, user=user)
    p.clear_entry, p.status = e, 'cleared'
    p.save(update_fields=['clear_entry', 'status'])
    return p


@transaction.atomic
def bounce(p, on=None, user=None):
    """a returned cheque: the payment entry is reversed and the invoices are open again"""
    must(p.status in ('pdc', 'posted', 'cleared') and p.method == 'cheque', 'Only a cheque can be returned.')
    on = on or timezone.localdate()
    if p.clear_entry_id:
        reverse_entry(p.clear_entry, on, user, f'Cheque {p.cheque_no} returned')
    reverse_entry(p.entry, on, user, f'Cheque {p.cheque_no} returned')
    _undo_allocations(p)
    p.status = 'bounced'
    p.save(update_fields=['status'])
    return p


def _undo_allocations(p):
    for a in p.allocations.select_related('doc'):
        doc = a.doc
        doc.balance = r2(doc.balance + a.amount)
        doc.status = 'posted' if doc.balance >= doc.total else 'part_paid'
        doc.save(update_fields=['balance', 'status'])


@transaction.atomic
def reverse_payment(p, on=None, user=None):
    must(p.status in ('posted', 'pdc', 'cleared'), 'Only a posted payment can be reversed.')
    on = on or p.date
    if p.clear_entry_id:
        reverse_entry(p.clear_entry, on, user)
    reverse_entry(p.entry, on, user)
    _undo_allocations(p)
    p.status = 'reversed'
    p.save(update_fields=['status'])
    return p


def open_docs(partner, sale=True):
    kinds = ('invoice', 'debit') if sale else ('bill', 'credit')
    return Document.objects.filter(partner=partner, kind__in=kinds, status__in=('posted', 'part_paid')).order_by('due_date', 'date')


def auto_allocate(p):
    """oldest first, up to the amount"""
    left = p.amount - (p.allocations.aggregate(s=Sum('amount'))['s'] or 0)
    for doc in open_docs(p.partner, p.kind == 'receipt'):
        if left <= 0:
            break
        if p.allocations.filter(doc=doc).exists():
            continue
        amt = min(left, doc.balance)
        Allocation.objects.create(payment=p, doc=doc, amount=amt)
        left -= amt
    return p


# ------------------------------------------------------------------ partner balances, ageing, statements
def ageing(on, sale=True, buckets=(30, 60, 90, 120)):
    kinds = ('invoice', 'credit') if sale else ('bill', 'debit')
    rows = defaultdict(lambda: defaultdict(lambda: D0))
    names = {}
    for doc in Document.objects.filter(kind__in=kinds, status__in=('posted', 'part_paid'), date__lte=on).select_related('partner'):
        amt = r2(doc.balance * (doc.rate or 1)) * doc_sign(doc)
        days = (on - (doc.due_date or doc.date)).days
        b = 'current' if days <= 0 else next((f'{lo + 1}-{hi}' for lo, hi in zip((0,) + tuple(buckets), buckets) if days <= hi), f'over {buckets[-1]}')
        rows[doc.partner_id][b] += amt
        rows[doc.partner_id]['total'] += amt
        names[doc.partner_id] = str(doc.partner)
    # receipts / payments on account
    for p in Payment.objects.filter(kind='receipt' if sale else 'payment', status__in=('posted', 'pdc', 'cleared'), unallocated__gt=0, date__lte=on).select_related('partner'):
        amt = -r2(p.unallocated * (p.rate or 1))
        rows[p.partner_id]['on account'] += amt
        rows[p.partner_id]['total'] += amt
        names[p.partner_id] = str(p.partner)
    heads = ['current'] + [f'{lo + 1}-{hi}' for lo, hi in zip((0,) + tuple(buckets), buckets)] + [f'over {buckets[-1]}', 'on account']
    return heads, [{'partner_id': k, 'partner': names[k], **{h: v.get(h, D0) for h in heads}, 'total': v['total']} for k, v in sorted(rows.items(), key=lambda kv: names[kv[0]])]


def statement(partner, date_from, date_to, sale=True):
    ctl = account('receivable' if sale else 'payable')
    qs = EntryLine.objects.filter(posted=True, account=ctl, partner=partner)
    op = qs.filter(date__lt=date_from).aggregate(d=Sum('debit'), c=Sum('credit'))
    bal = r2((op['d'] or 0) - (op['c'] or 0))
    rows = [{'date': date_from, 'number': '', 'description': 'Opening balance', 'debit': None, 'credit': None, 'balance': bal}]
    for ln in qs.filter(date__gte=date_from, date__lte=date_to).select_related('entry').order_by('date', 'id'):
        bal += ln.debit - ln.credit
        rows.append({'date': ln.date, 'number': ln.entry.number, 'description': ln.description or ln.entry.narration, 'debit': ln.debit, 'credit': ln.credit,
                     'balance': r2(bal), 'due': ln.due_date})
    return rows


# ------------------------------------------------------------------ bank reconciliation
def suggest_matches(stmt):
    """each unmatched statement line → the posted bank-account line of the same amount nearest in date (± 7 days)"""
    acc = stmt.journal.account
    out = []
    used = set()
    for bl in stmt.lines.filter(matched=False):
        q = EntryLine.objects.filter(posted=True, account=acc, bank_line__isnull=True, date__gte=bl.date - timedelta(days=7), date__lte=bl.date + timedelta(days=7))
        q = q.filter(debit=bl.amount) if bl.amount > 0 else q.filter(credit=-bl.amount)
        best = None
        for ln in q.select_related('entry'):
            if ln.pk in used:
                continue
            score = abs((ln.date - bl.date).days) - (5 if bl.reference and bl.reference in (ln.entry.reference + ln.description) else 0)
            if best is None or score < best[0]:
                best = (score, ln)
        if best:
            used.add(best[1].pk)
            out.append((bl, best[1]))
    return out


@transaction.atomic
def reconcile(stmt, pairs=None):
    n = 0
    for bl, ln in (pairs if pairs is not None else suggest_matches(stmt)):
        ln.bank_line = bl
        ln.save(update_fields=['bank_line'])
        bl.matched = True
        bl.save(update_fields=['matched'])
        n += 1
    return n


def book_balance(journal, on):
    return balance(journal.account, on)


def reconciliation(journal, on):
    """book balance; cheques / transfers in the books not yet on the bank statement; statement lines not in the books"""
    book = book_balance(journal, on)
    not_bank = EntryLine.objects.filter(posted=True, account=journal.account, date__lte=on, bank_line__isnull=True).exclude(entry__source='opening')
    unmatched = BankLine.objects.filter(statement__journal=journal, date__lte=on, matched=False)
    last = BankStatement.objects.filter(journal=journal, date__lte=on).order_by('-date', '-id').first()
    return {'book': book, 'outstanding_in': r2(not_bank.aggregate(s=Sum('debit'))['s'] or 0), 'outstanding_out': r2(not_bank.aggregate(s=Sum('credit'))['s'] or 0),
            'statement': last.closing if last else None, 'unmatched_statement': r2(unmatched.aggregate(s=Sum('amount'))['s'] or 0),
            'outstanding': list(not_bank.select_related('entry')[:200]), 'unmatched': list(unmatched[:200])}


# ------------------------------------------------------------------ exchange revaluation
@transaction.atomic
def revalue(on, user=None):
    """open foreign-currency invoices / bills and foreign-currency bank accounts at the rate of the date; reversed the next day"""
    out = []
    for doc in Document.objects.filter(status__in=('posted', 'part_paid')).exclude(currency='').exclude(currency=base_currency()).filter(date__lte=on):
        new_rate = fx(doc.currency, on)
        old = r2(doc.balance * (doc.rate or 1))
        new = r2(doc.balance * new_rate)
        diff = (new - old) * doc_sign(doc)
        if not diff:
            continue
        ctl = account('receivable' if doc.sale else 'payable')
        gain = diff > 0 if doc.sale else diff < 0
        amt = abs(diff)
        out.append({'account': ctl, 'debit': amt if diff > 0 else D0, 'credit': amt if diff < 0 else D0, 'partner': doc.partner, 'description': f'Revaluation {doc.number}'})
        out.append({'account': account('fx_gain' if gain else 'fx_loss'), 'debit': D0 if gain else amt, 'credit': amt if gain else D0,
                    'description': f'Revaluation {doc.number} {doc.currency} {new_rate}'})
    for j in Journal.objects.filter(kind__in=('bank', 'cash'), is_active=True).exclude(currency='').exclude(currency=base_currency()):
        fc = lines(None, on).filter(account=j.account).aggregate(s=Sum('amount_currency'))['s'] or 0
        book = balance(j.account, on)
        new = r2(Decimal(fc) * fx(j.currency, on))
        diff = new - book
        if not diff:
            continue
        out.append({'account': j.account, 'debit': diff if diff > 0 else D0, 'credit': -diff if diff < 0 else D0, 'description': f'Revaluation {j.code}'})
        out.append({'account': account('fx_gain' if diff > 0 else 'fx_loss'), 'debit': D0 if diff > 0 else -diff, 'credit': diff if diff > 0 else D0,
                    'description': f'Revaluation {j.code} {j.currency}'})
    if not out:
        return None
    j = Journal.objects.filter(kind='fx').first() or Journal.objects.filter(kind='general').first()
    e = make_entry(j, on, out, narration=f'Unrealised exchange revaluation at {on:%d/%m/%Y}', source='fx', user=user, manager=True)
    r = reverse_entry(e, on + timedelta(days=1), user, f'Reversal of the revaluation at {on:%d/%m/%Y}')
    return e, r


# ------------------------------------------------------------------ year end
@transaction.atomic
def close_year(y, user=None):
    must(y.status == 'open', f'{y} is closed already.')
    out = []
    net = D0
    for r in lines(y.start, y.end).filter(account__type__in=('income', 'expense')).values('account').annotate(d=Sum('debit'), c=Sum('credit')):
        bal = r2((r['d'] or 0) - (r['c'] or 0))
        if not bal:
            continue
        a = GLAccount.objects.get(pk=r['account'])
        out.append({'account': a, 'debit': D0 if bal > 0 else -bal, 'credit': bal if bal > 0 else D0, 'description': 'Year end'})
        net += bal
    if net:
        out.append({'account': account('retained'), 'debit': net if net > 0 else D0, 'credit': -net if net < 0 else D0, 'description': f'Result of {y.name}'})
    e = None
    if out:
        j = Journal.objects.filter(kind='closing').first() or Journal.objects.filter(kind='general').first()
        e = make_entry(j, y.end, out, narration=f'Year end {y.name}: income and expenses to retained earnings', source='closing', user=user, manager=True)
    y.status, y.closing_entry = 'closed', e
    y.save(update_fields=['status', 'closing_entry'])
    y.periods.update(status='closed')
    make_year(y.end + timedelta(days=1))
    return e


YE = 'yearend:'   # source_ref of the retained-earnings adjustments of a closed year


def roll_forward(e, user=None):
    """v1.79.3 an entry posted into a closed year: its income and expense go to retained earnings at once, by an
    adjustment dated the last day of that year, so the closed year and the years after it stay right (Sage 300 style)"""
    if (e.source_ref or '').startswith(YE):
        return None
    y = fiscal_year_of(e.date, make=False)
    if y is None or y.status == 'open':
        return None
    out, net = [], D0
    for ln in e.lines.select_related('account').filter(account__type__in=('income', 'expense')):
        bal = r2(ln.debit - ln.credit)
        if not bal:
            continue
        out.append({'account': ln.account, 'debit': D0 if bal > 0 else -bal, 'credit': bal if bal > 0 else D0, 'cost_centre': ln.cost_centre,
                    'branch': ln.branch, 'description': f'Year end – {e.number}'})
        net += bal
    if not out:
        return None
    if net:
        out.append({'account': account('retained'), 'debit': net if net > 0 else D0, 'credit': -net if net < 0 else D0, 'branch': e.branch,
                    'description': f'Result of {e.number} to retained earnings'})
    j = Journal.objects.filter(kind='closing').first() or Journal.objects.filter(kind='general').first()
    return make_entry(j, y.end, out, narration=f'Year end {y.name} adjustment for {e.number}', reference=e.number, source='closing',
                      source_ref=f'{YE}{y.pk}:{e.pk}', branch=e.branch, user=user, manager=True, prior_year=True)


def late_entries(y):
    """entries posted into a closed year after it was closed (not the year-end entries themselves)"""
    return Entry.objects.filter(source_ref__startswith=f'{YE}{y.pk}:').count()


@transaction.atomic
def reopen_year(y, user=None):
    """v1.79.3 undo a year end: the closing entry and the adjustments of late entries are reversed; income and expense of the
    year show again until it is closed once more. Only the latest closed year can be reopened."""
    must(y.status == 'closed', f'{y} is open already.')
    must(not FiscalYear.objects.filter(start__gt=y.start, status='closed').exists(),
         'A later year is closed – reopen the latest closed year first.')
    y.status = 'open'
    y.save(update_fields=['status'])
    n = 0
    for e in Entry.objects.filter(status='posted').filter(Q(pk=y.closing_entry_id) | Q(source_ref__startswith=f'{YE}{y.pk}:')):
        reverse_entry(e, e.date, user, f'Year {y.name} reopened – reversal of {e.number}')
        n += 1
    y.closing_entry = None
    y.save(update_fields=['closing_entry'])
    y.periods.filter(status='closed').update(status='open')
    return n


def lock_modules(y, upto, modules, lock=True, status=None):
    """v1.79.3 lock (or unlock) modules in every period of the year that ends on or before a date"""
    qs = y.periods.filter(end__lte=upto) if upto else y.periods.all()
    upd = {f'lock_{m}': lock for m in modules}
    if status:
        upd['status'] = status
    return qs.update(**upd) if upd else 0


def change_period_end(p, end):
    """v1.79.3 period dates like Sage 200 Evolution: moving an end moves the next start"""
    nxt = p.year.periods.filter(number=p.number + 1).first()
    must(end >= p.start, 'The end is before the start of the period.', 'end')
    must(end <= p.year.end, f'The end is after the year end ({p.year.end:%d/%m/%Y}).', 'end')
    if nxt is None:
        must(end == p.year.end, 'The last period ends on the year end – change the year end instead.', 'end')
        return
    must(end + timedelta(days=1) <= nxt.end, f'The end leaves no days for {nxt}.', 'end')
    moved = EntryLine.objects.filter(posted=True, date__gt=min(end, p.end), date__lte=max(end, p.end)).exists()
    must(not moved or p.status == 'open' and nxt.status == 'open', 'Postings would move between a closed and an open period.', 'end')
    Period.objects.filter(pk=nxt.pk).update(start=end + timedelta(days=1))


# ------------------------------------------------------------------ VAT 201
def vat_period(on):
    st = settings()
    if st.vat_period == 'monthly':
        return date(on.year, on.month, 1), date(on.year, on.month, calendar.monthrange(on.year, on.month)[1])
    fm = (st.vat_first_month or 1)
    k = ((on.month - fm) % 3)
    sm = on.month - k
    sy = on.year
    if sm < 1:
        sm += 12
        sy -= 1
    em, ey = sm + 2, sy
    if em > 12:
        em -= 12
        ey += 1
    return date(sy, sm, 1), date(ey, em, calendar.monthrange(ey, em)[1])


def vat201(start, end, adjustments=None):
    """the boxes from the posted lines: amounts from the taxable lines (tax_base), VAT from the VAT lines; adjustments column as entered"""
    adj = adjustments or {}
    box = {k: {'amount': D0, 'vat': D0, 'adjustment': D0} for k, _ in VAT_BOX_LABELS}
    qs = lines(start, end).exclude(vat_box='')
    # v1.79.3 added up by the database per box (a quarter of a busy company has hundreds of thousands of VAT lines)
    for r in qs.filter(tax_base__isnull=False).values('vat_box').annotate(s=Sum('tax_base')):
        if r['vat_box'] in box:
            box[r['vat_box']]['amount'] += r['s'] or D0
    for r in qs.filter(tax_base__isnull=True).values('vat_box').annotate(d=Sum('debit'), c=Sum('credit')):
        b = r['vat_box']
        if b in box:
            box[b]['vat'] += ((r['c'] or D0) - (r['d'] or D0)) if b not in ('9', '10') else ((r['d'] or D0) - (r['c'] or D0))
    # reverse charge: the taxable amount of the purchase shows in box 3 (or 6) as well as box 10
    for r in lines(start, end).filter(vat_box='10', tax_base__isnull=False).values('tax__treatment').annotate(s=Sum('tax_base')):
        tr = r['tax__treatment'] or 'RC'
        box['3' if tr == 'RC' else '6']['amount'] += r['s'] or D0
    for k, v in adj.items():
        if k in box:
            box[k]['adjustment'] += Decimal(str(v.get('vat', 0) or 0))
            box[k]['amount'] += Decimal(str(v.get('amount', 0) or 0))
    out_keys = ['1a', '1b', '1c', '1d', '1e', '1f', '1g', '2', '3', '4', '5', '6', '7']
    box['8']['amount'] = sum((box[k]['amount'] for k in out_keys), D0)
    box['8']['vat'] = sum((box[k]['vat'] for k in out_keys if k not in ('4', '5')), D0)
    box['8']['adjustment'] = sum((box[k]['adjustment'] for k in out_keys), D0)
    box['11']['amount'] = box['9']['amount'] + box['10']['amount']
    box['11']['vat'] = box['9']['vat'] + box['10']['vat']
    box['11']['adjustment'] = box['9']['adjustment'] + box['10']['adjustment']
    due = r2(box['8']['vat'] + box['8']['adjustment'])
    rec = r2(box['11']['vat'] + box['11']['adjustment'])
    box['12']['vat'], box['13']['vat'], box['14']['vat'] = due, rec, r2(due - rec)
    for k in box:
        for f in ('amount', 'vat', 'adjustment'):
            box[k][f] = r2(box[k][f])
    return [{'box': k, 'label': lbl, **box[k]} for k, lbl in VAT_BOX_LABELS], r2(due - rec)


@transaction.atomic
def file_vat(ret, user=None, reference=''):
    """the VAT accounts are cleared into "VAT payable to the FTA" at the end of the period"""
    must(ret.status == 'draft', 'This return is filed already.')
    rows, due = vat201(ret.start, ret.end, ret.adjustments)
    ret.boxes = {r['box']: {k: str(r[k]) for k in ('amount', 'vat', 'adjustment')} for r in rows}
    ret.due = due
    out = []
    accs = set(Tax.objects.exclude(account__isnull=True).values_list('account_id', flat=True)) | set(Tax.objects.exclude(rc_account__isnull=True).values_list('rc_account_id', flat=True))
    for role in ('vat_out', 'vat_in'):
        try:
            accs.add(account(role).pk)
        except LedgerError:
            pass
    for a in GLAccount.objects.filter(pk__in=accs):
        bal = balance(a, ret.end)
        if not bal:
            continue
        out.append({'account': a, 'debit': -bal if bal < 0 else D0, 'credit': bal if bal > 0 else D0, 'description': f'VAT return {ret.start:%m/%Y}–{ret.end:%m/%Y}'})
    net = sum((x['debit'] - x['credit'] for x in out), D0)
    if out:
        out.append({'account': account('vat_settle'), 'debit': -net if net < 0 else D0, 'credit': net if net > 0 else D0, 'description': 'VAT payable to the FTA'})
        j = Journal.objects.filter(kind='general').order_by('id').first()
        ret.entry = make_entry(j, ret.end, out, narration=f'VAT return {ret.start:%d/%m/%Y} – {ret.end:%d/%m/%Y}', reference=reference, source='closing', user=user, manager=True)
    ret.status, ret.filed_at, ret.reference = 'filed', timezone.localdate(), reference
    ret.save()
    return ret


# ------------------------------------------------------------------ corporate tax
def corporate_tax(y):
    """accounting profit ± adjustments by the accounts' corporate-tax treatment → taxable income → 9 % above AED 375,000;
    small business relief (revenue up to AED 3 m, periods ending by 31 Dec 2029) when elected"""
    st = settings()
    pl = profit_and_loss(y.start, y.end)
    profit = pl['before_tax']
    adds = []
    for treat, label, factor in (('non_deductible', 'Expenses not deductible', Decimal(1)), ('half', 'Entertainment – 50 % not deductible', Decimal('0.5')),
                                 ('ct_expense', 'Corporate tax booked as expense', Decimal(1))):
        a = lines(y.start, y.end).filter(account__ct_treatment=treat).aggregate(d=Sum('debit'), c=Sum('credit'))
        amt = r2(((a['d'] or 0) - (a['c'] or 0)) * factor)
        if amt and treat != 'ct_expense':
            adds.append({'label': label, 'amount': amt})
    ex = lines(y.start, y.end).filter(account__ct_treatment='exempt_income').aggregate(d=Sum('debit'), c=Sum('credit'))
    exempt = r2((ex['c'] or 0) - (ex['d'] or 0))
    if exempt:
        adds.append({'label': 'Exempt income', 'amount': -exempt})
    taxable = r2(profit + sum((x['amount'] for x in adds), D0))
    revenue = r2(pl['revenue'] + pl['other_income'])
    sbr = bool(st.ct_sbr and revenue <= SBR_REVENUE and y.end <= SBR_LAST)
    if st.ct_qfzp:
        tax = D0
        note = 'Qualifying free zone person: 0 % on qualifying income (check the de minimis and substance conditions with your tax adviser).'
    elif sbr:
        tax = D0
        note = 'Small business relief elected: treated as having no taxable income (revenue up to AED 3 million, tax periods ending by 31 Dec 2029).'
    else:
        tax = r2(max(D0, taxable - CT_THRESHOLD) * CT_RATE)
        note = '0 % up to AED 375,000 of taxable income, 9 % above.'
    return {'year': y.name, 'revenue': revenue, 'profit': profit, 'adjustments': adds, 'taxable': taxable, 'tax': tax, 'sbr': sbr, 'note': note,
            'sbr_eligible': revenue <= SBR_REVENUE and y.end <= SBR_LAST}


# ------------------------------------------------------------------ the outbox of the other ZEO apps
def _guess_group(code):
    c = (code or '9')[0]
    return {'1': 'current_asset', '2': 'current_liability', '3': 'equity', '4': 'revenue', '5': 'cost_of_sales'}.get(c, 'expense')


@transaction.atomic
def import_outbox(batch, user=None, make_missing=True):
    """a journal of Payroll, Inventory, Property … (ZeoAccounting.JournalBatch) becomes a posted entry; the batch is marked posted"""
    must(batch.status in ('ready', 'draft', 'failed'), f'{batch.number or batch.pk} is {batch.get_status_display()}.')
    out, missing = [], []
    for ln in batch.lines.all():
        code = (ln.account or '').strip()
        a = GLAccount.objects.filter(code=code).first() or (GLAccount.objects.filter(code=ln.base_account).first() if ln.base_account else None)
        if a is None:
            if not make_missing:
                missing.append(code)
                continue
            a = GLAccount.objects.create(code=code[:20], name=(ln.label or code)[:150], group=_guess_group(code))
        cc = None
        if ln.department:
            cc = CostCentre.objects.filter(name=ln.department).first() or CostCentre.objects.create(code=f'D{ln.department_id or ""}'[:20] or ln.department[:20], name=ln.department[:120])
        out.append({'account': a, 'debit': ln.debit, 'credit': ln.credit, 'description': ln.label, 'cost_centre': cc, 'branch_id': ln.branch_id})
    must(not missing, 'These accounts are not in the chart of accounts: ' + ', '.join(sorted(set(missing))))
    j = Journal.objects.filter(kind='outbox').first() or Journal.objects.filter(kind='general').first()
    for x in out:
        x.pop('branch_id', None)
    e = make_entry(j, batch.date, out, narration=f'{batch.get_source_display()} – {batch.source_label}'[:255], reference=batch.number or str(batch.pk),
                   source='outbox', source_ref=batch.pk, user=user)
    batch.status, batch.connector, batch.external_id, batch.external_ref, batch.posted_at = 'posted', 'ledger', str(e.pk), e.number, timezone.now()
    batch.save(update_fields=['status', 'connector', 'external_id', 'external_ref', 'posted_at'])
    return e


def outbox_waiting():
    try:
        JB = apps.get_model('ZeoAccounting', 'JournalBatch')
    except LookupError:
        return []
    return list(JB.objects.filter(status='ready').order_by('date', 'id')[:500])


# ------------------------------------------------------------------ recurring, daily
@transaction.atomic
def run_recurring(on=None, user=None):
    on = on or timezone.localdate()
    made = 0
    for r in Recurring.objects.filter(is_active=True, next_date__lte=on):
        while r.next_date <= on and (not r.end_date or r.next_date <= r.end_date):
            ls = [{'account': GLAccount.objects.get(code=x['account']), 'debit': Decimal(str(x.get('debit') or 0)), 'credit': Decimal(str(x.get('credit') or 0)),
                   'description': x.get('description', ''), 'cost_centre': CostCentre.objects.filter(code=x.get('cost_centre') or '').first()} for x in r.lines]
            make_entry(r.journal, r.next_date, ls, narration=r.name, source='recurring', source_ref=r.pk, user=user, post=r.auto_post)
            made += 1
            m = {'month': 1, 'quarter': 3, 'year': 12}[r.every]
            y, mo = divmod(r.next_date.month - 1 + m, 12)
            nd = r.next_date.replace(year=r.next_date.year + y, month=mo + 1, day=min(r.next_date.day, calendar.monthrange(r.next_date.year + y, mo + 1)[1]))
            r.next_date = nd
        r.save(update_fields=['next_date'])
    return made


def daily():
    out = {'recurring': 0, 'pdc_cleared': 0}
    try:
        out['recurring'] = run_recurring()
    except Exception:   # noqa: BLE001
        log.exception('recurring entries')
    for p in Payment.objects.filter(status='pdc', cheque_date__lte=timezone.localdate()):
        try:
            clear_pdc(p)
            out['pdc_cleared'] += 1
        except Exception:   # noqa: BLE001
            log.exception('PDC %s', p.pk)
    for e in Entry.objects.filter(status='posted', auto_reverse__lte=timezone.localdate(), reversals__isnull=True).exclude(auto_reverse__isnull=True):
        try:
            reverse_entry(e, e.auto_reverse)
        except Exception:   # noqa: BLE001
            log.exception('auto reverse %s', e.pk)
    return out


# ------------------------------------------------------------------ e-invoice (PINT AE) data
def einvoice_xml(doc):
    """UBL 2.1 invoice in the PINT AE profile – the data the accredited service provider sends (the company's ASP signs and transmits)"""
    from xml.sax.saxutils import escape as x
    st = settings()
    p = doc.partner
    cur = (doc.currency or base_currency()).upper()
    lines_xml = []
    for i, ln in enumerate(doc.lines.select_related('tax', 'product'), 1):
        t = ln.tax
        cat = {'SR': 'S', 'ZR': 'Z', 'EX': 'E', 'OS': 'O'}.get(t.treatment if t else 'SR', 'S')
        lines_xml.append(
            f'<cac:InvoiceLine><cbc:ID>{i}</cbc:ID><cbc:InvoicedQuantity unitCode="C62">{ln.qty}</cbc:InvoicedQuantity>'
            f'<cbc:LineExtensionAmount currencyID="{cur}">{ln.net}</cbc:LineExtensionAmount>'
            f'<cac:Item><cbc:Name>{x(ln.description or str(ln.product or ""))}</cbc:Name><cac:ClassifiedTaxCategory><cbc:ID>{cat}</cbc:ID>'
            f'<cbc:Percent>{(t.rate if t else 0):g}</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory></cac:Item>'
            f'<cac:Price><cbc:PriceAmount currencyID="{cur}">{ln.price}</cbc:PriceAmount></cac:Price></cac:InvoiceLine>')
    root = 'CreditNote' if doc.kind == 'credit' else 'Invoice'
    return (f'<?xml version="1.0" encoding="UTF-8"?><{root} xmlns="urn:oasis:names:specification:ubl:schema:xsd:{root}-2" '
            f'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
            f'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
            f'<cbc:CustomizationID>urn:peppol:pint:billing-1@ae-1</cbc:CustomizationID><cbc:ProfileID>urn:peppol:bis:billing</cbc:ProfileID>'
            f'<cbc:ID>{x(doc.number)}</cbc:ID><cbc:UUID>{doc.einv_uuid}</cbc:UUID><cbc:IssueDate>{doc.date:%Y-%m-%d}</cbc:IssueDate>'
            f'<cbc:{"CreditNoteTypeCode" if root == "CreditNote" else "InvoiceTypeCode"}>{doc.einv_type or ("381" if root == "CreditNote" else "380")}'
            f'</cbc:{"CreditNoteTypeCode" if root == "CreditNote" else "InvoiceTypeCode"}><cbc:DocumentCurrencyCode>{cur}</cbc:DocumentCurrencyCode>'
            f'<cac:AccountingSupplierParty><cac:Party><cbc:EndpointID schemeID="0235">{x(st.endpoint_id or st.trn)}</cbc:EndpointID>'
            f'<cac:PartyTaxScheme><cbc:CompanyID>{x(st.trn)}</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme></cac:Party></cac:AccountingSupplierParty>'
            f'<cac:AccountingCustomerParty><cac:Party><cac:PartyName><cbc:Name>{x(str(p))}</cbc:Name></cac:PartyName>'
            f'<cac:PartyTaxScheme><cbc:CompanyID>{x(getattr(p, "tax_id", "") or "")}</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme></cac:Party></cac:AccountingCustomerParty>'
            f'<cac:TaxTotal><cbc:TaxAmount currencyID="{cur}">{doc.vat}</cbc:TaxAmount></cac:TaxTotal>'
            f'<cac:LegalMonetaryTotal><cbc:LineExtensionAmount currencyID="{cur}">{doc.net}</cbc:LineExtensionAmount>'
            f'<cbc:TaxExclusiveAmount currencyID="{cur}">{doc.net}</cbc:TaxExclusiveAmount><cbc:TaxInclusiveAmount currencyID="{cur}">{doc.total}</cbc:TaxInclusiveAmount>'
            f'<cbc:PayableAmount currencyID="{cur}">{doc.balance}</cbc:PayableAmount></cac:LegalMonetaryTotal>' + ''.join(lines_xml) + f'</{root}>')


def einvoice_problems(doc):
    st = settings()
    out = []
    if not st.trn:
        out.append('The company TRN is missing (Accounting › Setup).')
    if doc.sale and not getattr(doc.partner, 'tax_id', ''):
        out.append(f'{doc.partner} has no TRN – needed for a B2B e-invoice.')
    if not doc.number:
        out.append('Post the invoice first.')
    return out


# ------------------------------------------------------------------ checks
def integrity():
    """every posted entry balances; the receivable / payable control accounts equal the open documents and payments on account"""
    bad = []
    for e in Entry.objects.filter(status__in=('posted', 'reversed')).annotate(d=Sum('lines__debit'), c=Sum('lines__credit')):
        if r2(e.d or 0) != r2(e.c or 0):
            bad.append(f'{e.number} does not balance')
    tot = EntryLine.objects.filter(posted=True).aggregate(d=Sum('debit'), c=Sum('credit'))
    return {'entries_out_of_balance': bad, 'ledger_balanced': r2(tot['d'] or 0) == r2(tot['c'] or 0), 'debit': r2(tot['d'] or 0), 'credit': r2(tot['c'] or 0)}


# ------------------------------------------------------------------ v1.79.3 customer / supplier ledger and statement of open items
PARTY_KINDS = {True: ('invoice', 'credit'), False: ('bill', 'debit')}


def control_accounts(sale=True):
    """the receivable (or payable) control account of the set-up and every account marked as a customer (supplier) control account"""
    role = 'receivable' if sale else 'payable'
    ids = set(GLAccount.objects.filter(control=role).values_list('pk', flat=True))
    try:
        ids.add(account(role).pk)
    except LedgerError:
        pass
    return ids


def _branch_q(branch, field='branch'):
    return Q(**{f'{field}_id': branch}) if branch else Q()


def partner_ledger(date_from, date_to, sale=True, branch=None, partner=None, include_pdc=True, in_currency=False):
    """every posting on the customer (supplier) control accounts by partner: opening balance, the invoices, credit notes, receipts,
    payments, journals, exchange differences and returned cheques, with the running balance. Customer: + they owe us; supplier: + we owe them.
    include_pdc=False: the post-dated cheques still held at the end date are left out (lines and balances – the balance before the cheques).
    in_currency=False: amounts in the company currency (with the transaction currency and its amount);
    in_currency=True: one ledger per partner and currency, every amount and the balance in that currency (exchange revaluations left out)."""
    accs = control_accounts(sale)
    sgn = 1 if sale else -1
    base = base_currency()
    qs = EntryLine.objects.filter(posted=True, account_id__in=accs, partner__isnull=False, date__lte=date_to)
    if branch:
        qs = qs.filter(Q(branch_id=branch) | Q(branch__isnull=True, entry__branch_id=branch))
    if partner:
        qs = qs.filter(partner_id=partner)
    lines_ = list(qs.select_related('entry', 'doc').order_by('date', 'entry_id', 'id'))
    pay_ids = {int(ln.entry.source_ref) for ln in lines_ if ln.entry.source == 'payment' and str(ln.entry.source_ref).isdigit()}
    pays = {p.pk: p for p in Payment.objects.filter(pk__in=pay_ids).select_related('clear_entry')}
    held = {k for k, p in pays.items() if _held_pdc(p, date_to)}
    Partner = apps.get_model('ZeoMasters', 'Partner')
    groups = {}

    def grp(pid, cur):
        k = (pid, cur if in_currency else '')
        if k not in groups:
            groups[k] = {'partner_id': pid, 'currency': cur if in_currency else base, 'opening': D0, 'bal': D0, 'rows': [], 'debit': D0, 'credit': D0}
        return groups[k]

    for ln in lines_:
        e = ln.entry
        pay = pays.get(int(e.source_ref)) if e.source == 'payment' and str(e.source_ref).isdigit() else None
        if pay is not None and pay.pk in held and not include_pdc:
            continue
        cur = (ln.currency or e.currency or base).upper()
        if in_currency and e.source == 'fx':   # a revaluation changes the company-currency value only
            continue
        amt = r2((ln.debit - ln.credit) * sgn)
        fc = (abs(r2(ln.amount_currency)) * (1 if amt >= 0 else -1)) if ln.currency and ln.amount_currency else amt
        value = fc if in_currency else amt
        g = grp(ln.partner_id, cur)
        if date_from and ln.date < date_from:
            g['opening'] = r2(g['opening'] + value)
            g['bal'] = g['opening']
            continue
        g['bal'] = r2(g['bal'] + value)
        doc = ln.doc
        desc = ln.description or e.narration
        if pay is not None:   # a receipt / payment (its lines carry the document they settle)
            is_pdc = pay.method == 'cheque' and pay.cheque_date and pay.cheque_date > pay.date
            typ = ('Receipt' if pay.kind == 'receipt' else 'Payment') + (' – PDC' if is_pdc else '')
            if is_pdc and not e.reversal_of_id:   # as it stood at the end of the period
                typ += (f' (held, due {pay.cheque_date:%d/%m/%Y})' if pay.pk in held else
                        {'cleared': ' (cleared)', 'bounced': ' (returned)', 'reversed': ' (reversed)'}.get(pay.status, ''))
            if e.reversal_of_id:
                typ = 'Cheque returned' if pay.status == 'bounced' else 'Payment reversed'
            number, ref = pay.number, ' '.join(x for x in (pay.cheque_no and f'Chq {pay.cheque_no}', pay.cheque_bank, pay.reference) if x)
            desc = (f'Against {doc.number}' if doc is not None else (ln.description or 'On account')) if not e.reversal_of_id else (e.narration or desc)
            url = f'r/payment/{pay.pk}'
        elif doc is not None:
            typ, number, ref = doc.get_kind_display(), doc.number, doc.supplier_ref or doc.reference
            if e.reversal_of_id:
                typ = f'{typ} cancelled'
            url = f'r/doc/{doc.pk}'
        else:
            typ = {'fx': 'Exchange revaluation', 'outbox': 'Other ZEO app', 'opening': 'Opening balance'}.get(e.source, 'Journal')
            if e.reversal_of_id:
                typ = 'Reversal'
            number, ref, url = e.number, e.reference, f'r/entry/{e.pk}'
        if in_currency:
            deb, cre = (abs(fc), D0) if ln.debit > 0 else (D0, abs(fc))
        else:
            deb, cre = ln.debit, ln.credit
        g['debit'] += deb
        g['credit'] += cre
        g['rows'].append({'kind': 'line', 'partner_id': ln.partner_id, 'date': ln.date, 'type': typ, 'number': number or e.number, 'entry': e.number,
                          'reference': ref or '', 'description': desc, 'due': ln.due_date, 'currency': cur, 'amount_currency': fc,
                          'debit': deb, 'credit': cre, 'amount': value, 'balance': g['bal'], 'pdc': bool(pay and pay.pk in held), 'url': url})
    partners = {p.pk: p for p in Partner.objects.filter(pk__in={k[0] for k in groups})}
    out = []
    for k in sorted(groups, key=lambda k: (str(partners[k[0]].name).lower() if k[0] in partners else '', k[0], k[1])):
        g = groups[k]
        if not g['rows'] and not g['opening']:
            continue
        g['rows'].insert(0, {'kind': 'opening', 'partner_id': g['partner_id'], 'date': date_from, 'balance': g['opening']})
        out.append({'partner': partners.get(g['partner_id']), 'partner_id': g['partner_id'], 'currency': g['currency'], 'opening': g['opening'],
                    'closing': g['bal'], 'debit': r2(g['debit']), 'credit': r2(g['credit']), 'rows': g['rows']})
    return out


def _held_pdc(p, on):
    """a post-dated cheque still held on the date (not cleared, returned or reversed by then)"""
    if p.method != 'cheque' or not p.cheque_date or p.date > on:
        return False
    if p.status == 'pdc':
        return True
    if p.status == 'cleared':
        return bool(p.clear_entry_id and p.clear_entry.date > on)
    if p.status in ('bounced', 'reversed'):   # held on the date if it was returned / reversed only later
        rev = Entry.objects.filter(reversal_of_id=p.entry_id).order_by('date').first() if p.entry_id else None
        return bool(rev and rev.date > on and p.cheque_date > p.date)
    return False


def _counted(p, on):
    """the payment settled its invoices on the date (posted by then and not returned / reversed by then)"""
    if p.date > on or p.status == 'draft':
        return False
    if p.status in ('bounced', 'reversed'):
        rev = Entry.objects.filter(reversal_of_id=p.entry_id).order_by('date').first() if p.entry_id else None
        return bool(rev and rev.date > on)
    return True


def open_items(on, sale=True, branch=None, partner=None, include_pdc=True, in_currency=False):
    """statement of account: the invoices / bills (and credit / debit notes) still open on the date by partner, before the post-dated cheques held,
    the payments on account, and the post-dated cheques held (received from customers / issued to suppliers) – to show under the open items.
    Open amount = total − payments counted on the date (a held PDC is not counted: it is listed below) − notes applied by the date.
    include_pdc=False: the cheques held are not listed (the open items stay before them – what is owed until the cheques clear).
    in_currency=True: one statement per partner and currency, the amounts in that currency."""
    kinds = PARTY_KINDS[sale]
    docs = Document.objects.filter(kind__in=kinds, date__lte=on).exclude(status__in=('draft', 'cancelled')).filter(_branch_q(branch))
    pays = Payment.objects.filter(kind='receipt' if sale else 'payment', date__lte=on).exclude(status='draft').filter(_branch_q(branch))
    if partner:
        docs, pays = docs.filter(partner_id=partner), pays.filter(partner_id=partner)
    docs = list(docs.select_related('partner', 'original'))
    pays = list(pays.select_related('partner', 'clear_entry'))
    held = {p.pk for p in pays if _held_pdc(p, on)}
    counted = {p.pk for p in pays if p.pk not in held and _counted(p, on)}
    alloc = defaultdict(lambda: D0)
    pdc_alloc = defaultdict(list)
    for a in Allocation.objects.filter(payment_id__in=held | counted).select_related('doc'):
        if a.payment_id in held:
            pdc_alloc[a.payment_id].append(a)
        else:
            alloc[a.doc_id] += a.amount
    applied = defaultdict(lambda: D0)   # credit / debit notes on their original document
    for n in Document.objects.filter(original__isnull=False, kind__in=kinds, date__lte=on).exclude(status__in=('draft', 'cancelled')):
        applied[n.original_id] += r2(n.total - n.balance) if n.status in ('paid', 'part_paid') else D0
    by = defaultdict(lambda: {'items': [], 'pdc': []})
    for d in docs:
        sign = doc_sign(d)
        if sign > 0:
            open_amt = r2(d.total - alloc[d.pk] - applied[d.pk])
        else:   # a note: what was not applied to its original
            open_amt = r2(d.total - (r2(d.total - d.balance) if d.original_id and d.status in ('paid', 'part_paid') else D0) - alloc[d.pk])
        if open_amt <= Decimal('0.005'):
            continue
        rate = d.rate or Decimal(1)
        due = d.due_date or d.date
        by[d.partner_id]['partner'] = d.partner
        by[d.partner_id]['items'].append({'type': d.get_kind_display(), 'number': d.number, 'date': d.date, 'due': due, 'reference': d.supplier_ref or d.reference,
                                          'currency': (d.currency or base_currency()).upper(), 'total': r2(d.total * sign), 'open_currency': r2(open_amt * sign),
                                          'open': r2(open_amt * rate) * sign, 'days': max((on - due).days, 0), 'url': f'r/doc/{d.pk}'})
    for p in pays:
        rate = p.rate or Decimal(1)
        cur = (p.currency or p.journal.currency or base_currency()).upper() if p.journal_id else base_currency()
        if p.pk in held:
            al = pdc_alloc.get(p.pk, [])
            by[p.partner_id]['partner'] = p.partner
            by[p.partner_id]['pdc'].append({'number': p.number, 'date': p.date, 'cheque_no': p.cheque_no, 'cheque_date': p.cheque_date, 'bank': p.cheque_bank,
                                            'currency': cur, 'amount_currency': r2(p.amount),
                                            # in the company currency as the books took it off the open items: the part paid on a document at the
                                            # document's rate, the rest (on account) at the cheque's rate – so the net balance equals the ledger balance
                                            'amount': r2(sum((r2(a.amount * (a.doc.rate or 1)) for a in al), D0) + r2((p.amount - sum((a.amount for a in al), D0)) * rate)),
                                            'against': ', '.join(a.doc.number for a in al) or 'On account', 'url': f'r/payment/{p.pk}'})
        elif p.pk in counted:
            onacc = r2(p.amount - sum((a.amount for a in p.allocations.all()), D0))
            if onacc > Decimal('0.005'):
                by[p.partner_id]['partner'] = p.partner
                by[p.partner_id]['items'].append({'type': 'Payment on account' if not sale else 'Receipt on account', 'number': p.number, 'date': p.date, 'due': p.date,
                                                  'reference': ' '.join(x for x in (p.cheque_no and f'Chq {p.cheque_no}', p.reference) if x),
                                                  'currency': cur, 'total': -r2(p.amount), 'open_currency': -onacc, 'open': -r2(onacc * rate), 'days': 0,
                                                  'url': f'r/payment/{p.pk}'})
    out = []
    for pid, v in by.items():
        if not include_pdc:
            v['pdc'] = []
        keys = sorted({r['currency'] for r in v['items'] + v['pdc']}) if in_currency else [None]
        for cur in keys:
            items = [r for r in v['items'] if cur is None or r['currency'] == cur]
            pdc = [r for r in v['pdc'] if cur is None or r['currency'] == cur]
            items.sort(key=lambda r: (r['date'], r['number'] or ''))
            pdc.sort(key=lambda r: (r['cheque_date'] or r['date'], r['number'] or ''))
            fld_i, fld_p = ('open_currency', 'amount_currency') if cur else ('open', 'amount')
            outstanding = r2(sum((r[fld_i] for r in items), D0))
            pdc_total = r2(sum((r[fld_p] for r in pdc), D0))
            out.append({'partner': v['partner'], 'partner_id': pid, 'currency': cur or base_currency(), 'items': items, 'pdc': pdc,
                        'outstanding': outstanding, 'pdc_total': pdc_total, 'net': r2(outstanding - pdc_total)})
    out.sort(key=lambda x: (str(x['partner'].name).lower(), x['currency']))
    return out
