"""ZEO Accounting on the app kit (v1.76.0): chart of accounts, journals and entries, customer invoices and credit notes, supplier bills and
debit notes, receipts and payments with allocation and post-dated cheques, bank statements and reconciliation, exchange rates and
revaluation, cost centres, budgets, recurring entries, financial years and periods, VAT 201, corporate tax, e-invoice data, and the
journals of the other ZEO apps (Payroll, Inventory, Property …) posted from the accounting outbox."""
from decimal import Decimal

from django.db.models import Q, Sum

from zeo.appkit.engine import Act, App, AppError, Reg, d, in_branches, today

from . import services as S
from .models import (ROLES, Allocation, BankLine, BankStatement, Budget, CostCentre, CtReturn, Document, DocumentLine, Entry, EntryLine, FiscalYear, PostingRight, FxRate,
                     GLAccount, Journal, LedgerEvent, LedgerFile, LedgerSequence, LedgerSettings, Payment, Period, Recurring, Tax, VatReturn)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-ledger/'

app = App(
    label='ZeoLedger', slug='zeo-ledger', title='Accounting', settings_model=LedgerSettings, sequence_model=LedgerSequence, event_model=LedgerEvent,
    file_model=LedgerFile, roles=ROLES, role_prefix='gl_', manager_role='gl_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={}, pack_models={'account': (GLAccount, 'code'), 'tax': (Tax, 'code'), 'journal': (Journal, 'code')},
    links=(('ZeoInventory', 'Inventory', 'Stock journals use the inventory accounts of this chart (1400, 2150, 5000 …).'),),
    page=PAGE,
)
app.settings_fields = ('currency', 'fy_start_month', 'fy_start_day', 'vat_registered', 'trn', 'vat_period', 'vat_first_month', 'emirate', 'lock_date', 'prior_year_posting', 'ct_trn', 'ct_sbr',
                       'ct_qfzp', 'einvoicing', 'endpoint_id', 'auto_post', 'decimals', 'accounts')

MGR = ('gl_manager',)
ACC = ('gl_manager', 'gl_accountant')
AR = ('gl_manager', 'gl_accountant', 'gl_ar')
AP = ('gl_manager', 'gl_accountant', 'gl_ap')
VIEW = ('gl_accountant', 'gl_ar', 'gl_ap', 'gl_viewer')
ALL = ('gl_manager', 'gl_accountant', 'gl_ar', 'gl_ap', 'gl_viewer')


def run(fn):
    try:
        return fn()
    except S.LedgerError as e:
        raise AppError(e.message, e.field)


def when(states, *roles):
    return lambda o, r: o.status in states and app.any(r, *roles)


def tbl(label, cols, rows):
    return {'name': '_' + label.lower().replace(' ', '_')[:30], 'label': label, 'columns': [{'key': k, 'label': lb, 'type': t} for k, lb, t in cols], 'rows': rows}


def money(x):
    return float(x or 0)


# ------------------------------------------------------------------ options
app.option('accounts', lambda: GLAccount.objects.filter(is_active=True, is_heading=False), lambda x: str(x), ('code', 'name', 'name_ar'), GLAccount)
app.option('headings', lambda: GLAccount.objects.filter(is_heading=True), lambda x: str(x), ('code', 'name'))
app.option('taxes', lambda: Tax.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), Tax)
app.option('journals', lambda: Journal.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), Journal)
app.option('banks', lambda: Journal.objects.filter(is_active=True, kind__in=('bank', 'cash')), lambda x: str(x), ('code', 'name'))
app.option('cost-centres', lambda: CostCentre.objects.filter(is_active=True), lambda x: str(x), ('code', 'name'), CostCentre)
app.option('years', lambda: FiscalYear.objects.all(), lambda x: x.name, ('name',), FiscalYear)
app.option('open-docs', lambda: Document.objects.filter(status__in=('posted', 'part_paid')), lambda x: f'{x.number} {x.partner} – {x.balance} open',
           ('number', 'partner__name', 'supplier_ref'), Document)
app.option('entries', lambda: Entry.objects.filter(status='posted'), lambda x: f'{x.number} {d(x.date)} {x.narration[:40]}', ('number', 'narration', 'reference'), Entry)
app.option('statements', lambda: BankStatement.objects.all(), lambda x: str(x), ('number',), BankStatement)
app.option('payments', lambda: Payment.objects.all(), lambda x: f'{x.number} {x.partner or ""}', ('number', 'reference', 'cheque_no'), Payment)
try:
    from ZeoMasters.models import PaymentTerm as _PT, Product as _Product
    app.option('payment-terms', lambda: _PT.objects.filter(is_active=True), lambda x: x.name, ('code', 'name'), _PT)
    app.option('items', lambda: _Product.objects.filter(is_active=True), lambda x: f'{x.number} {x.name}', ('number', 'name', 'code'), _Product)
except Exception:   # noqa: BLE001
    pass
app.provide('accounting.accounts', lambda: GLAccount.objects.filter(is_active=True, is_heading=False), lambda x: str(x), ('code', 'name'))


# ------------------------------------------------------------------ chart of accounts
def acc_check(o, request, old):
    if GLAccount.objects.filter(code=o.code).exclude(pk=o.pk).exists():
        raise AppError('This code is already used.', 'code')
    if old is not None and old.group != o.group and o.lines.exists() and S.GROUP_TYPE.get(old.group) != S.GROUP_TYPE.get(o.group):
        raise AppError('The account has postings – it cannot move to another type (asset, liability, equity, income, expense).', 'group')
    if o.is_heading and o.pk and o.lines.exists():
        raise AppError('The account has postings – it cannot become a heading.', 'is_heading')


def acc_extra(o, request):
    rows = [{'date': d(x.date), 'number': x.entry.number, 'description': x.description, 'partner': str(x.partner or ''), 'debit': money(x.debit),
             'credit': money(x.credit)} for x in o.lines.filter(posted=True).select_related('entry', 'partner').order_by('-date', '-id')[:100]]
    return {'balance': money(S.balance(o)), 'tables': [tbl('Last postings', [('date', 'Date', 'text'), ('number', 'Entry', 'text'), ('description', 'Description', 'text'),
                                                                                 ('partner', 'Partner', 'text'), ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number')], rows)]}


# ------------------------------------------------------------------ entries
def entry_defaults(o, request):
    if not o.date:
        o.date = today()
    if not o.journal_id:
        o.journal = Journal.objects.filter(kind='general').order_by('id').first()
    o.created_by = o.created_by or request.user


def entry_check(o, request, old):
    if o.status != 'draft':
        raise AppError('A posted entry is not changed – reverse it.')


def entry_extra(o, request):
    ls = list(o.lines.select_related('account', 'partner', 'cost_centre'))
    dr = sum((x.debit for x in ls), Decimal(0))
    cr = sum((x.credit for x in ls), Decimal(0))
    return {'totals': {'debit': money(dr), 'credit': money(cr), 'difference': money(dr - cr)},
            'warnings': [f'Not balanced: debit {dr:,.2f}, credit {cr:,.2f}.'] if dr != cr and o.status == 'draft' else []}


def a_post_entry(o, request, f):
    run(lambda: S.post_entry(o, request.user, manager=app.any(request, *MGR), prior_year=app.any(request, *MGR)))
    app.event('entry', o.pk, f'Posted as {o.number}', request.user)
    return {'message': f'Posted as {o.number}.'}


def a_reverse_entry(o, request, f):
    r = run(lambda: S.reverse_entry(o, f.dt('date') or o.date, request.user, f.s('note'), prior_year=app.any(request, *MGR)))
    app.event('entry', o.pk, f'Reversed by {r.number}', request.user)
    return {'message': f'Reversed by {r.number}.', 'open': f'r/entry/{r.pk}'}


def a_copy_entry(o, request, f):
    n = Entry.objects.create(journal=o.journal, date=today(), narration=o.narration, reference=o.reference, currency=o.currency, rate=o.rate, branch=o.branch,
                             created_by=request.user)
    for ln in o.lines.all():
        EntryLine.objects.create(entry=n, account=ln.account, partner=ln.partner, cost_centre=ln.cost_centre, branch=ln.branch, description=ln.description,
                                 debit=ln.debit, credit=ln.credit, currency=ln.currency, amount_currency=ln.amount_currency, tax=ln.tax)
    return {'message': 'Copied as a draft.', 'open': f'r/entry/{n.pk}'}


def eline_defaults(o, request):
    if o.entry_id and not o.description:
        o.description = o.entry.narration
    if o.account_id and not o.tax_id and o.account.tax_id:
        o.tax = o.account.tax


def eline_check(o, request, old):
    if o.entry.status != 'draft':
        raise AppError('A posted entry is not changed – reverse it.')
    if (o.debit or 0) and (o.credit or 0):
        raise AppError('A line is either a debit or a credit.', 'credit')
    if o.account.is_heading:
        raise AppError(f'{o.account} is a heading – choose an account under it.', 'account')


# ------------------------------------------------------------------ invoices and bills
def doc_defaults(o, request):
    if not o.date:
        o.date = today()
    if not o.kind:
        o.kind = 'invoice'
    st = S.settings()
    if not o.emirate and o.kind in ('invoice', 'credit'):
        o.emirate = st.emirate
    if o.partner_id and not o.payment_term_id and getattr(o.partner, 'payment_term_id', None):
        o.payment_term_id = o.partner.payment_term_id
    o.created_by = o.created_by or request.user


def doc_check(o, request, old):
    if old is not None and old.status != 'draft':
        raise AppError('A posted document is not changed – cancel it or issue a credit / debit note.')
    if not o.due_date:
        days = getattr(o.payment_term, 'days', None) if o.payment_term_id else None
        from datetime import timedelta
        o.due_date = o.date + timedelta(days=days if days is not None else 30)
    if o.kind in ('bill', 'debit') and o.supplier_ref:
        dup = Document.objects.filter(kind=o.kind, partner_id=o.partner_id, supplier_ref=o.supplier_ref).exclude(pk=o.pk).exclude(status='cancelled')
        if dup.exists():
            raise AppError(f'Bill {o.supplier_ref} of this supplier is already entered ({dup.first().number or "draft"}).', 'supplier_ref')
    if o.currency and o.currency.upper() == S.base_currency():
        o.currency = ''
    if o.currency and not o.rate:
        try:
            o.rate = S.fx(o.currency, o.date)
        except S.LedgerError as e:
            raise AppError(e.message, 'rate')


def doc_saver(o, request, creating, old):
    o.save()
    S.calc_doc(o)
    return o


def doc_extra(o, request):
    out = {'totals': {'net': money(o.net), 'vat': money(o.vat), 'total': money(o.total), 'to_pay': money(o.balance)}}
    pays = [{'number': a.payment.number, 'date': d(a.payment.date), 'amount': money(a.amount), 'status': a.payment.get_status_display()}
            for a in o.allocations.select_related('payment')]
    notes = [{'number': n.number or 'draft', 'date': d(n.date), 'total': money(n.total), 'status': n.get_status_display()} for n in o.adjust_notes.all()]
    out['tables'] = [tbl('Payments', [('number', 'Number', 'text'), ('date', 'Date', 'text'), ('amount', 'Amount', 'number'), ('status', 'Status', 'text')], pays)]
    if notes:
        out['tables'].append(tbl('Credit / debit notes', [('number', 'Number', 'text'), ('date', 'Date', 'text'), ('total', 'Total', 'number'), ('status', 'Status', 'text')], notes))
    if o.entry_id:
        out['tables'].append(tbl('Journal entry', [('account', 'Account', 'text'), ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number')],
                                 [{'account': str(x.account), 'debit': money(x.debit), 'credit': money(x.credit)} for x in o.entry.lines.select_related('account')]))
    if S.settings().einvoicing and o.sale and o.status != 'draft':
        out['einvoice'] = {'status': o.get_einv_status_display(), 'problems': S.einvoice_problems(o), 'uuid': o.einv_uuid}
    return out


def a_post_doc(o, request, f):
    run(lambda: S.post_doc(o, request.user))
    app.event('doc', o.pk, f'Posted as {o.number}', request.user)
    return {'message': f'{o.get_kind_display()} {o.number} posted: {o.total:,.2f}.'}


def a_cancel_doc(o, request, f):
    run(lambda: S.cancel_doc(o, request.user, f.dt('date')))
    app.event('doc', o.pk, 'Cancelled' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return {'message': f'{o.number} cancelled (its entry reversed).'}


def a_note(o, request, f):
    kind = 'credit' if o.kind == 'invoice' else 'debit'
    n = Document.objects.create(kind=kind, partner=o.partner, date=today(), original=o, currency=o.currency, rate=o.rate, emirate=o.emirate, branch=o.branch,
                                reference=o.number, created_by=request.user)
    for ln in o.lines.all():
        DocumentLine.objects.create(doc=n, product=ln.product, description=ln.description, account=ln.account, qty=ln.qty, price=ln.price, discount=ln.discount,
                                    tax=ln.tax, cost_centre=ln.cost_centre)
    S.calc_doc(n)
    return {'message': f'{n.get_kind_display()} drafted from {o.number} – change the lines and post it.', 'open': f'r/doc/{n.pk}'}


def a_pay(o, request, f):
    sale = o.sale
    j = f.i('journal') and Journal.objects.filter(pk=f.i('journal')).first() or Journal.objects.filter(kind='bank', is_active=True).first()
    if j is None:
        raise AppError('Add a bank or cash journal first.')
    amt = Decimal(str(f.s('amount') or o.balance))
    p = Payment.objects.create(kind='receipt' if sale else 'payment', partner=o.partner, date=f.dt('date') or today(), journal=j, amount=amt,
                               currency=o.currency, method=f.s('method') or 'transfer', reference=f.s('reference') or o.number, branch=o.branch, created_by=request.user)
    Allocation.objects.create(payment=p, doc=o, amount=min(amt, o.balance))
    run(lambda: S.post_payment(p, request.user))
    return {'message': f'{p.get_kind_display()} {p.number} of {amt:,.2f} posted against {o.number}.'}


def a_einvoice(o, request, f):
    probs = S.einvoice_problems(o)
    if probs:
        raise AppError(' '.join(probs))
    import base64
    xml = S.einvoice_xml(o)
    o.einv_status = 'ready'
    o.save(update_fields=['einv_status'])
    return {'message': 'The PINT AE invoice file is ready – your accredited service provider sends it.',
            'download': {'name': f'{o.number}.xml', 'mime': 'application/xml', 'data': base64.b64encode(xml.encode()).decode()}}


def dline_defaults(o, request):
    if o.product_id:
        p = o.product
        if not o.description:
            o.description = p.name
        if not o.price:
            o.price = getattr(p, 'sale_price' if o.doc.sale else 'cost', 0) or getattr(p, 'price', 0) or 0
    if not o.tax_id:
        st = S.settings()
        if st.vat_registered:
            o.tax = Tax.objects.filter(use='sale' if o.doc.sale else 'purchase', treatment='SR', is_active=True).order_by('id').first()
    if not o.account_id and o.product_id:
        o.account = S._product_account(o.product, o.doc.sale)


def dline_check(o, request, old):
    if o.doc.status != 'draft':
        raise AppError('A posted document is not changed.')
    if not o.account_id and not o.product_id:
        try:
            o.account = S.account('revenue' if o.doc.sale else 'expense')
        except S.LedgerError as e:
            raise AppError(e.message, 'account')


def dline_saver(o, request, creating, old):
    o.save()
    S.calc_doc(o.doc)
    return o


# ------------------------------------------------------------------ payments
def pay_defaults(o, request):
    if not o.date:
        o.date = today()
    if not o.journal_id:
        o.journal = Journal.objects.filter(kind='bank', is_active=True).order_by('id').first()
    o.created_by = o.created_by or request.user


def pay_check(o, request, old):
    if old is not None and old.status != 'draft':
        raise AppError('A posted receipt / payment is not changed – reverse it.')
    if o.method == 'cheque' and not o.cheque_no:
        raise AppError('Enter the cheque number.', 'cheque_no')
    if o.currency and o.currency.upper() == S.base_currency():
        o.currency = ''


def pay_extra(o, request):
    rows = [{'doc': a.doc.number, 'date': d(a.doc.date), 'total': money(a.doc.total), 'amount': money(a.amount), 'fx': money(a.fx_diff)}
            for a in o.allocations.select_related('doc')]
    open_ = []
    if o.status == 'draft' and o.partner_id and o.kind != 'transfer':
        open_ = [{'doc': x.number, 'date': d(x.date), 'due': d(x.due_date), 'open': money(x.balance)} for x in S.open_docs(o.partner, o.kind == 'receipt')[:50]]
    t = [tbl('Allocated', [('doc', 'Document', 'text'), ('date', 'Date', 'text'), ('total', 'Total', 'number'), ('amount', 'Allocated', 'number'), ('fx', 'Exchange difference', 'number')], rows)]
    if open_:
        t.append(tbl('Open documents of the partner', [('doc', 'Document', 'text'), ('date', 'Date', 'text'), ('due', 'Due', 'text'), ('open', 'Open', 'number')], open_))
    return {'tables': t, 'allocated': money(sum((a.amount for a in o.allocations.all()), Decimal(0)))}


def a_auto(o, request, f):
    S.auto_allocate(o)
    return {'message': 'Allocated to the oldest open documents.'}


def a_post_pay(o, request, f):
    run(lambda: S.post_payment(o, request.user))
    app.event('payment', o.pk, f'Posted as {o.number}', request.user)
    return {'message': f'{o.number} posted' + (' – held as a post-dated cheque until its date.' if o.status == 'pdc' else '.')}


def a_clear(o, request, f):
    run(lambda: S.clear_pdc(o, f.dt('date'), request.user))
    return {'message': f'Cheque {o.cheque_no} cleared.'}


def a_bounce(o, request, f):
    run(lambda: S.bounce(o, f.dt('date'), request.user))
    app.event('payment', o.pk, f'Cheque returned: {f.s("note")}', request.user)
    return {'message': f'Cheque {o.cheque_no} returned – the invoices are open again.'}


def a_reverse_pay(o, request, f):
    run(lambda: S.reverse_payment(o, f.dt('date'), request.user))
    return {'message': f'{o.number} reversed.'}


def alloc_check(o, request, old):
    if o.payment.status != 'draft':
        raise AppError('The payment is posted.')
    if o.doc.partner_id != o.payment.partner_id:
        raise AppError('Choose a document of the same partner.', 'doc')
    if o.amount <= 0 or o.amount > o.doc.balance:
        raise AppError(f'At most {o.doc.balance:,.2f} is open on {o.doc.number}.', 'amount')


# ------------------------------------------------------------------ bank
def a_match(o, request, f):
    n = run(lambda: S.reconcile(o))
    left = o.lines.filter(matched=False).count()
    return {'message': f'{n} statement line(s) matched; {left} left to match.'}


def a_done(o, request, f):
    if o.lines.filter(matched=False).exists():
        raise AppError('Match every statement line first (or book the bank charges and interest with a journal).')
    o.status = 'done'
    o.save(update_fields=['status'])
    return {'message': 'Statement reconciled.'}


def a_book(o, request, f):
    """an unmatched statement line booked to an account (bank charges, interest) and matched"""
    bl = o.lines.filter(pk=f.i('line', True)).first()
    if bl is None or bl.matched:
        raise AppError('Choose an unmatched line.', 'line')
    acc = GLAccount.objects.filter(pk=f.i('account', True)).first()
    j = o.journal
    amt = abs(bl.amount)
    e = run(lambda: S.make_entry(j, bl.date, [{'account': j.account, 'debit': amt if bl.amount > 0 else 0, 'credit': amt if bl.amount < 0 else 0, 'description': bl.description},
                                              {'account': acc, 'debit': amt if bl.amount < 0 else 0, 'credit': amt if bl.amount > 0 else 0, 'description': bl.description}],
                                 narration=bl.description or 'Bank', reference=bl.reference, source='bank', user=request.user))
    ln = e.lines.filter(account=j.account).first()
    S.reconcile(o, [(bl, ln)])
    return {'message': f'Booked as {e.number} and matched.'}


def stmt_extra(o, request):
    rows = [{'date': d(x.date), 'description': x.description, 'reference': x.reference, 'amount': money(x.amount), 'matched': 'Yes' if x.matched else ''}
            for x in o.lines.all()]
    rec = S.reconciliation(o.journal, o.date) if o.journal.account_id else None
    out = {'tables': [tbl('Statement lines', [('date', 'Date', 'text'), ('description', 'Description', 'text'), ('reference', 'Reference', 'text'),
                                              ('amount', 'Amount', 'number'), ('matched', 'Matched', 'text')], rows)]}
    if rec:
        out['reconciliation'] = {'book': money(rec['book']), 'statement': money(o.closing), 'outstanding_in': money(rec['outstanding_in']),
                                 'outstanding_out': money(rec['outstanding_out']), 'unmatched_statement': money(rec['unmatched_statement'])}
    return out


# ------------------------------------------------------------------ years, VAT, corporate tax
def a_close_year(o, request, f):
    e = run(lambda: S.close_year(o, request.user))
    return {'message': f'{o.name} closed' + (f' – result to retained earnings by {e.number}.' if e else '.')}


def year_check(o, request, old):   # v1.79.3 years with their own start and end (Sage 200 Evolution)
    if o.end <= o.start:
        raise AppError('The year ends before it starts.', 'end')
    if (o.end - o.start).days > 550:
        raise AppError('A financial year is at most 18 months.', 'end')
    clash = FiscalYear.objects.filter(start__lte=o.end, end__gte=o.start).exclude(pk=o.pk).first()
    if clash:
        raise AppError(f'The dates overlap {clash.name} ({clash.start:%d/%m/%Y} – {clash.end:%d/%m/%Y}).', 'start')
    if old is not None and (old.start != o.start or old.end != o.end):
        if old.status != 'open':
            raise AppError('A closed year keeps its dates.')
        if EntryLine.objects.filter(posted=True, date__gte=min(old.start, o.start), date__lte=max(old.end, o.end)).exclude(
                date__gte=max(old.start, o.start), date__lte=min(old.end, o.end)).exists():
            raise AppError('There are postings in the days that would leave or join the year.')


def year_saver(o, request, creating, old):
    o.save()
    if old is not None and (old.start != o.start or old.end != o.end):
        o.periods.all().delete()
    S.build_periods(o)
    return o


def period_check(o, request, old):
    if old is not None and o.end != old.end:
        run(lambda: S.change_period_end(old, o.end))


def postright_check(o, request, old):
    if PostingRight.objects.filter(user_id=o.user_id).exclude(pk=o.pk).exists():
        raise AppError(f'{o.user} has a line already – open it and change it.', 'user')
    if o.date_from and o.date_to and o.date_to < o.date_from:
        raise AppError('"Up to" is before "from".', 'date_to')


def a_lock_modules(o, request, f):   # v1.79.3
    mods = [m for m, _ in S.MODULES if f.b(m)]
    lock = not f.b('unlock')
    if not mods:
        raise AppError('Tick at least one module.')
    n = S.lock_modules(o, f.dt('upto'), mods, lock)
    names = ', '.join(S.MODULE_LABEL[m].split(' (')[0].lower() for m in mods)
    upto = f.dt('upto') or o.end
    app.event('year', o.pk, f'{"Locked" if lock else "Unlocked"} {names} in {n} period(s) up to {upto:%d/%m/%Y}', request.user)
    return {'message': f'{"Locked" if lock else "Unlocked"} {names} in {n} period(s) up to {upto:%d/%m/%Y}.'}


def a_reopen_year(o, request, f):   # v1.79.3
    n = run(lambda: S.reopen_year(o, request.user))
    app.event('year', o.pk, f'Reopened – {n} year-end entr{"y" if n == 1 else "ies"} reversed. {f.s("note")}'.strip(), request.user)
    return {'message': f'{o.name} reopened – {n} year-end entr{"y" if n == 1 else "ies"} reversed. Close the year again when the adjustments are in.'}


def a_revalue(o, request, f):
    res = run(lambda: S.revalue(f.dt('date', True), request.user))
    return {'message': 'Nothing in foreign currency to revalue.' if not res else f'Revaluation {res[0].number} posted, reversed the next day by {res[1].number}.'}


def a_outbox(o, request, f):
    n, errs = 0, []
    for b in S.outbox_waiting():
        try:
            S.import_outbox(b, request.user, make_missing=f.b('make_missing'))
            n += 1
        except S.LedgerError as e:
            errs.append(f'{b.number or b.pk}: {e.message}')
    return {'message': f'{n} journal(s) of the other apps posted.' + (' Not posted: ' + '; '.join(errs[:5]) if errs else '')}


def vat_defaults(o, request):
    if not o.start or not o.end:
        o.start, o.end = S.vat_period(today())


def vat_extra(o, request):
    rows, due = S.vat201(o.start, o.end, o.adjustments)
    return {'due': money(due), 'tables': [tbl('VAT 201', [('box', 'Box', 'text'), ('label', 'Description', 'text'), ('amount', 'Amount (AED)', 'number'),
                                                          ('vat', 'VAT (AED)', 'number'), ('adjustment', 'Adjustment (AED)', 'number')],
                                               [{**r, 'amount': money(r['amount']), 'vat': money(r['vat']), 'adjustment': money(r['adjustment'])} for r in rows])]}


def a_file_vat(o, request, f):
    run(lambda: S.file_vat(o, request.user, f.s('reference')))
    return {'message': f'Filed: {o.due:,.2f} {"payable" if o.due >= 0 else "refundable"}; the VAT accounts are cleared to "VAT payable to the FTA".'}


def ct_extra(o, request):
    c = S.corporate_tax(o.year)
    rows = [{'line': 'Revenue', 'amount': money(c['revenue'])}, {'line': 'Accounting profit before tax', 'amount': money(c['profit'])}]
    rows += [{'line': a['label'], 'amount': money(a['amount'])} for a in c['adjustments']]
    rows += [{'line': 'Taxable income', 'amount': money(c['taxable'])}, {'line': 'Corporate tax', 'amount': money(c['tax'])}]
    return {'note': c['note'], 'tables': [tbl('Corporate tax computation', [('line', 'Line', 'text'), ('amount', 'AED', 'number')], rows)]}


def a_ct_calc(o, request, f):
    c = S.corporate_tax(o.year)
    o.revenue, o.profit, o.adjustments, o.taxable, o.tax, o.sbr = c['revenue'], c['profit'], [{'label': a['label'], 'amount': str(a['amount'])} for a in c['adjustments']], c['taxable'], c['tax'], c['sbr']
    o.save()
    return {'message': f'Taxable income {c["taxable"]:,.2f}; corporate tax {c["tax"]:,.2f}.'}


def a_ct_book(o, request, f):
    if o.entry_id:
        raise AppError('The tax is booked already.')
    if not o.tax:
        raise AppError('No corporate tax to book.')
    j = Journal.objects.filter(kind='general').order_by('id').first()
    e = run(lambda: S.make_entry(j, o.year.end, [{'account': S.account('ct_expense'), 'debit': o.tax, 'description': f'Corporate tax {o.year}'},
                                                 {'account': S.account('ct_payable'), 'credit': o.tax, 'description': f'Corporate tax {o.year}'}],
                                 narration=f'Corporate tax {o.year}', source='closing', user=request.user, manager=True, prior_year=app.any(request, *MGR)))
    o.entry = e
    o.save(update_fields=['entry'])
    return {'message': f'Booked by {e.number}.'}


# ------------------------------------------------------------------ registers
R = app.reg
R(Reg('account', GLAccount, 'Account', 'Chart of accounts', 'Setup',
      cols=('code', 'name', 'group', 'type', 'parent', 'control', 'currency', 'tax', 'ct_treatment', 'is_heading', 'is_active'),
      form=('code', 'name', 'name_ar', 'group', 'parent', 'is_heading', 'control', 'currency', 'reconcile', 'tax', 'ct_treatment', 'cost_centre_required', 'branch',
            'is_active', 'notes'),
      manage=MGR, view=ALL, search=('code', 'name', 'name_ar'), filters=('group', 'type', 'control', 'ct_treatment', 'is_heading', 'is_active'), title='code', subtitle='name',
      check=acc_check, extra=acc_extra, info=('type',), ordering=('code',),
      help='The accounts of the books, grouped for the balance sheet and the profit and loss. Corporate tax: how each account counts in the computation.'))
R(Reg('tax', Tax, 'Tax', 'Taxes (VAT)', 'Setup', cols=('code', 'name', 'use', 'treatment', 'rate', 'recoverable', 'account', 'is_active'),
      form=('code', 'name', 'use', 'treatment', 'rate', 'recoverable', 'account', 'rc_account', 'is_active'), manage=MGR, view=ALL, search=('code', 'name'),
      filters=('use', 'treatment', 'is_active'), title='code', subtitle='name',
      help='VAT codes: standard rated, zero rated, exempt, out of scope, reverse charge, imported goods, blocked input tax. Each line of an invoice or bill takes one; the VAT 201 boxes follow.'))
R(Reg('journal', Journal, 'Journal', 'Journals and bank accounts', 'Setup', cols=('code', 'name', 'kind', 'account', 'currency', 'iban', 'is_active'),
      form=('code', 'name', 'kind', 'account', 'pdc_account', 'currency', 'iban', 'branch', 'is_active'), manage=MGR, view=ALL, search=('code', 'name', 'iban'),
      filters=('kind', 'is_active'), title='code', subtitle='name',
      actions=[Act('outbox', 'Post the journals of the other apps', a_outbox, lambda o, r: o.kind == 'outbox' and app.any(r, *ACC), style='primary',
                   fields=(('make_missing', 'Add accounts that are not in the chart yet', 'bool', False),))],
      help='Sales, purchases, bank, cash and general journals. A bank or cash journal is the bank account (its GL account, IBAN, currency, post-dated cheques account).'))
R(Reg('costcentre', CostCentre, 'Cost centre', 'Cost centres', 'Setup', cols=('code', 'name', 'parent', 'branch', 'is_active'),
      form=('code', 'name', 'parent', 'branch', 'is_active'), manage=MGR, view=ALL, search=('code', 'name'), filters=('is_active',), title='code', subtitle='name',
      help='Departments, projects or activities the income and costs are analysed by.'))
R(Reg('year', FiscalYear, 'Financial year', 'Financial years', 'Setup', cols=('name', 'start', 'end', 'status'), form=('name', 'start', 'end'),
      manage=MGR, view=ALL, search=('name',), filters=('status',), title='name', children=(('Periods', 'period', 'year'),),
      check=year_check, saver=year_saver,
      actions=[Act('revalue', 'Exchange revaluation', a_revalue, lambda o, r: o.status == 'open' and app.any(r, *MGR), fields=(('date', 'At', 'date', True),)),
               Act('close', 'Close the year', a_close_year, when(('open',), *MGR), style='danger',
                   confirm='Income and expenses go to retained earnings and the periods close. Check the trial balance first.'),
               Act('locks', 'Lock or unlock modules', a_lock_modules, lambda o, r: app.any(r, *MGR),
                   fields=(('upto', 'In periods ending on or before (empty: whole year)', 'date', False),
                           ('gl', 'General ledger', 'bool', False), ('ar', 'Customers', 'bool', False), ('ap', 'Suppliers', 'bool', False),
                           ('bank', 'Bank and cash', 'bool', False), ('apps', 'Other ZEO apps', 'bool', False), ('unlock', 'Unlock instead of lock', 'bool', False))),
               Act('reopen', 'Reopen the year', a_reopen_year, when(('closed',), *MGR), style='danger', fields=(('note', 'Reason', 'text', True),),
                   confirm='The year-end entry and the retained-earnings adjustments are reversed and the periods open again. Only the latest closed year can be reopened.')],
      help='The financial years and their twelve periods; the year end closes the profit and loss into retained earnings. With "Allow posting to closed years" '
           'on (Accounting setup) the finance manager may post a journal entry into a closed year – its profit or loss goes to retained earnings at once. '
           'The latest closed year can also be reopened.'))
R(Reg('period', Period, 'Period', 'Periods', 'Setup', cols=('year', 'number', 'start', 'end', 'status', 'lock_gl', 'lock_ar', 'lock_ap', 'lock_bank', 'lock_apps'),
      form=('end', 'status', 'lock_gl', 'lock_ar', 'lock_ap', 'lock_bank', 'lock_apps'), manage=MGR, view=ALL,
      child_of='year', search=(), filters=('status',), title='start', files=False, check=period_check,
      help='Open: everyone posts. Closed: only the finance manager. Locked: nobody. A locked module (general ledger, customers, suppliers, bank and cash, '
           'other ZEO apps) takes no postings in the period. Users with "may post in locked periods" in Posting rights are not stopped. '
           'Changing a period end moves the start of the next period.'))
R(Reg('postright', PostingRight, 'Posting right', 'Posting rights by user', 'Setup',
      cols=('user', 'allow_locked', 'periods_back', 'periods_ahead', 'date_from', 'date_to', 'is_active'),
      form=('user', 'allow_locked', 'periods_back', 'periods_ahead', 'date_from', 'date_to', 'lock_gl', 'lock_ar', 'lock_ap', 'lock_bank', 'lock_apps', 'is_active', 'notes'),
      manage=MGR, view=ACC, search=('user__username', 'user__first_name', 'user__last_name'), filters=('is_active',), title='user', files=False, deletable=True, check=postright_check,
      help='Per user, like Sage 200 Evolution agents: post in locked periods and modules or not, how many periods back and ahead, a date range, '
           'and modules the user may not post in. Users without a line follow the period and module locks only.'))
R(Reg('fxrate', FxRate, 'Exchange rate', 'Exchange rates', 'Setup', cols=('currency', 'date', 'rate'), form=('currency', 'date', 'rate'), manage=ACC, view=ALL,
      search=('currency',), filters=('currency',), title='currency', subtitle='date', files=False,
      help='1 unit of the currency in the company currency, from a date.'))
R(Reg('budget', Budget, 'Budget', 'Budgets', 'Setup', cols=('name', 'year', 'account', 'cost_centre', 'total'), form=('name', 'year', 'account', 'cost_centre', 'amounts', 'total'),
      manage=MGR, view=ALL, search=('name', 'account__code', 'account__name'), filters=('year', 'cost_centre'), title='name',
      help='Budget per account (and cost centre) and month; Reports › Budget against actual.'))
# v1.79.4 financial report designer: statements laid out to the customer's requirement (ZeoLedger/designer.py)
from . import designer as FR   # noqa: E402
from .models import FinReport   # noqa: E402


def fr_defaults(o, request):
    if not o.lines and not o.columns:   # a new design starts from the template of its basis
        lines, cols = FR.TEMPLATES['board_bs' if o.basis == 'position' else 'mgmt_pl'][2:]
        o.lines, o.columns = [dict(x) for x in lines], [dict(x) for x in cols]


def fr_check(o, request, old):
    for ln in o.lines or []:
        if isinstance(ln, dict) and ln.get('ref'):
            ln['ref'] = str(ln['ref']).strip().upper()
    errs = FR.check(o.lines, o.columns)
    if errs:
        raise AppError(' '.join(errs[:6]))


def fr_extra(o, request):
    """the report link and a preview of this year to date"""
    out = {'open_report': f'/main-sidebar/report-options/r/gl-fr-{o.pk}', 'open_report_label': 'Open the report'}
    try:
        t = today()
        y = S.fiscal_year_of(t, make=False)
        f = y.start if (y and o.basis == 'period') else (y.start if y else t.replace(month=1, day=1))
        cols, rows, extra = FR.compute(o, f, t, page='/main-sidebar/zeo-ledger/')
        out['preview'] = {'columns': [{'key': c[0], 'label': c[1], 'type': c[2]} for c in cols], 'rows': rows[:80],
                          'note': f'Preview {f:%d/%m/%Y} – {t:%d/%m/%Y}' if o.basis == 'period' else f'Preview as at {t:%d/%m/%Y}'}
    except Exception as exc:   # noqa: BLE001 – the design is still being written
        out['preview'] = {'columns': [], 'rows': [], 'note': f'No preview: {str(exc)[:160]}'}
    return out


def a_fr_template(o, request, f):
    k = f.get('template') or ''
    if k not in FR.TEMPLATES:
        raise AppError('Pick a template.')
    title, basis, lines, cols = FR.TEMPLATES[k]
    o.basis, o.lines, o.columns = basis, [dict(x) for x in lines], [dict(x) for x in cols]
    o.save(update_fields=['basis', 'lines', 'columns'])
    return {'message': f'Lines and columns replaced by "{title}". Change them to the customer\'s layout.'}


R(Reg('finreport', FinReport, 'Financial report design', 'Financial report designer', 'Setup',
      cols=('name', 'basis', 'section', 'scale', 'is_active'),
      form=('name', 'name_ar', 'basis', 'section', 'subtitle', 'scale', 'show_codes', 'hide_zero', 'lines', 'columns', 'notes', 'is_active'),
      manage=MGR, view=ALL, search=('name', 'section'), filters=('basis', 'is_active'), title='name', subtitle='subtitle',
      defaults=fr_defaults, check=fr_check, extra=fr_extra, deletable=True, files=False,
      tables={'lines': FR.LINE_COLS, 'columns': FR.COLUMN_COLS},
      actions=[Act('template', 'Start from a template', a_fr_template, lambda o, r: app.any(r, *MGR),
                   fields=(('template', 'Template (replaces the lines and columns)', 'choice:' + FR.template_choices(), True),),
                   confirm='The lines and columns of this design are replaced.')],
      help='Lay out a profit and loss, balance sheet or any statement the way the customer wants it. Lines from top to bottom: Accounts = '
           '4000-4999, 61* (codes starting 61), group:revenue, type:expense, !4500 (leave out), profit (the profit not yet closed, for a '
           'balance sheet); Formula = the refs of lines above, e.g. REV - COS or GP / REV * 100. Sign: natural shows income, liabilities and '
           'equity positive. Columns: this period, last year, previous period, year to date, budget, variance (column a − b), variance %, '
           '% of a line. Each design is a report under Reports with drill down, Excel and PDF on the letterhead.'))
R(Reg('recurring', Recurring, 'Recurring entry', 'Recurring entries', 'Setup', cols=('name', 'journal', 'every', 'next_date', 'end_date', 'auto_post', 'is_active'),
      form=('name', 'journal', 'every', 'next_date', 'end_date', 'lines', 'auto_post', 'is_active'), manage=ACC, view=ALL, search=('name',),
      filters=('every', 'is_active'), title='name',
      tables={'lines': [('account', 'Account code', 'text'), ('debit', 'Debit', 'decimal'), ('credit', 'Credit', 'decimal'), ('description', 'Description', 'text'),
                        ('cost_centre', 'Cost centre code', 'text')]},
      help='Rent, depreciation, accruals made every month, quarter or year on their date (drafts, or posted).'))

R(Reg('entry', Entry, 'Journal entry', 'Journal entries', 'Books',
      cols=('number', 'date', 'journal', 'reference', 'narration', 'partner', 'total', 'source', 'status'),
      form=('journal', 'date', 'reference', 'narration', 'partner', 'currency', 'rate', 'branch', 'auto_reverse'),
      manage=ACC, view=VIEW, search=('number', 'reference', 'narration'), filters=('journal', 'status', 'source', 'partner', 'branch'), title='number', subtitle='narration',
      defaults=entry_defaults, check=entry_check, edit_when=lambda o: o.status == 'draft', extra=entry_extra, deletable=True,
      info=('total', 'source', 'source_ref', 'reversal_of', 'posted_at', 'posted_by'), children=(('Lines', 'eline', 'entry'), ('Reversals', 'entry', 'reversal_of')),
      actions=[Act('post', 'Post', a_post_entry, when(('draft',), *ACC), style='primary', confirm='A posted entry is never changed – it is corrected by reversing it.'),
               Act('reverse', 'Reverse', a_reverse_entry, lambda o, r: o.status == 'posted' and app.any(r, *ACC), style='danger',
                   fields=(('date', 'Reversal date (empty: the entry date)', 'date', False), ('note', 'Why', 'text', True))),
               Act('copy', 'Copy', a_copy_entry, lambda o, r: app.any(r, *ACC))],
      help='Every entry of the books. Post to put it in the ledger; reverse to correct.'))
R(Reg('eline', EntryLine, 'Line', 'Lines', 'Books', cols=('account', 'partner', 'cost_centre', 'description', 'debit', 'credit', 'tax', 'vat_box'),
      form=('entry', 'account', 'partner', 'cost_centre', 'branch', 'description', 'debit', 'credit', 'currency', 'amount_currency', 'tax', 'tax_base', 'vat_box', 'emirate', 'due_date'),
      manage=ACC, view=VIEW, child_of='entry', search=('description', 'account__code', 'account__name'), filters=(), title='account',
      defaults=eline_defaults, check=eline_check, edit_when=lambda o: o.entry.status == 'draft', files=False, deletable=True))

R(Reg('doc', Document, 'Invoice / bill', 'Invoices and bills', 'Sales and purchases',
      cols=('number', 'kind', 'date', 'partner', 'supplier_ref', 'due_date', 'currency', 'net', 'vat', 'total', 'balance', 'status'),
      form=('kind', 'partner', 'date', 'supply_date', 'payment_term', 'due_date', 'supplier_ref', 'reference', 'original', 'currency', 'rate', 'emirate', 'journal',
            'branch', 'notes'),
      manage=ACC, view=VIEW, create=('gl_ar', 'gl_ap'), search=('number', 'supplier_ref', 'reference', 'partner__name'),
      filters=('kind', 'status', 'partner', 'emirate', 'currency', 'einv_status', 'branch'), title='number', subtitle='partner',
      defaults=doc_defaults, check=doc_check, saver=doc_saver, edit_when=lambda o: o.status == 'draft', extra=doc_extra, deletable=True, pdf='Tax invoice',
      info=('net', 'vat', 'total', 'balance', 'total_base', 'entry', 'einv_type', 'einv_uuid', 'einv_status', 'source', 'source_ref'),
      children=(('Lines', 'dline', 'doc'), ('Credit / debit notes', 'doc', 'original')),
      actions=[Act('post', 'Post', a_post_doc, lambda o, r: o.status == 'draft' and app.any(r, *(AR if o.sale else AP)), style='primary',
                   confirm='Posting books the document; it is then corrected by a credit / debit note or by cancelling it.'),
               Act('pay', 'Register payment', a_pay, lambda o, r: o.status in ('posted', 'part_paid') and o.kind in ('invoice', 'bill') and app.any(r, *(AR if o.sale else AP)),
                   fields=(('amount', 'Amount (empty: all open)', 'decimal', False), ('date', 'Date', 'date', False), ('journal', 'Bank / cash', 'opt:banks', False),
                           ('method', 'Method', 'choice:transfer=Bank transfer|cheque=Cheque|cash=Cash|card=Card', False), ('reference', 'Reference', 'text', False))),
               Act('note', 'Credit / debit note', a_note, lambda o, r: o.status in ('posted', 'part_paid', 'paid') and o.kind in ('invoice', 'bill') and app.any(r, *ACC)),
               Act('einvoice', 'E-invoice file (PINT AE)', a_einvoice, lambda o, r: o.sale and o.status != 'draft' and S.settings().einvoicing and app.any(r, *AR)),
               Act('cancel', 'Cancel', a_cancel_doc, lambda o, r: o.status == 'posted' and app.any(r, *ACC), style='danger',
                   fields=(('date', 'Reversal date (empty: the document date)', 'date', False), ('note', 'Why', 'text', True)))],
      help='Customer invoices and credit notes, supplier bills and debit notes, with VAT by line and the place of supply for VAT 201.'))
R(Reg('dline', DocumentLine, 'Line', 'Lines', 'Sales and purchases', cols=('product', 'description', 'account', 'qty', 'price', 'discount', 'tax', 'net', 'vat', 'cost_centre'),
      form=('doc', 'product', 'description', 'account', 'qty', 'price', 'discount', 'tax', 'cost_centre'), manage=ACC, view=VIEW, create=('gl_ar', 'gl_ap'), child_of='doc',
      search=('description', 'product__name'), filters=(), title='description', defaults=dline_defaults, check=dline_check, saver=dline_saver,
      edit_when=lambda o: o.doc.status == 'draft', files=False, deletable=True, info=('net', 'vat')))
R(Reg('payment', Payment, 'Receipt / payment', 'Receipts and payments', 'Bank and cash',
      cols=('number', 'kind', 'date', 'partner', 'journal', 'method', 'amount', 'currency', 'cheque_no', 'cheque_date', 'unallocated', 'status'),
      form=('kind', 'partner', 'date', 'journal', 'to_journal', 'method', 'amount', 'currency', 'rate', 'cheque_no', 'cheque_date', 'cheque_bank', 'reference', 'branch', 'notes'),
      manage=ACC, view=VIEW, create=('gl_ar', 'gl_ap'), search=('number', 'reference', 'cheque_no', 'partner__name'),
      filters=('kind', 'status', 'method', 'journal', 'partner', 'branch'), title='number', subtitle='partner',
      defaults=pay_defaults, check=pay_check, edit_when=lambda o: o.status == 'draft', extra=pay_extra, deletable=True, pdf='Receipt voucher',
      info=('unallocated', 'entry', 'clear_entry'), children=(('Allocations', 'alloc', 'payment'),),
      actions=[Act('auto', 'Allocate to the oldest', a_auto, lambda o, r: o.status == 'draft' and o.kind != 'transfer' and o.partner_id and app.any(r, *ALL[:4])),
               Act('post', 'Post', a_post_pay, lambda o, r: o.status == 'draft' and app.any(r, *ALL[:4]), style='primary',
                   confirm='A cheque dated later is held as a post-dated cheque and goes to the bank on its date.'),
               Act('clear', 'Cheque cleared', a_clear, lambda o, r: o.status == 'pdc' and app.any(r, *ACC), fields=(('date', 'Cleared on (empty: the cheque date)', 'date', False),)),
               Act('bounce', 'Cheque returned', a_bounce, lambda o, r: o.method == 'cheque' and o.status in ('pdc', 'posted', 'cleared') and app.any(r, *ACC), style='danger',
                   fields=(('date', 'Returned on', 'date', False), ('note', 'Reason', 'text', True))),
               Act('reverse', 'Reverse', a_reverse_pay, lambda o, r: o.status in ('posted', 'pdc', 'cleared') and app.any(r, *ACC), style='danger',
                   fields=(('date', 'Reversal date', 'date', False),))],
      help='Money received from customers and paid to suppliers, allocated to their invoices and bills; post-dated cheques held until their date; transfers between bank accounts.'))
R(Reg('alloc', Allocation, 'Allocation', 'Allocations', 'Bank and cash', cols=('doc', 'amount', 'fx_diff'), form=('payment', 'doc', 'amount'), manage=ACC,
      view=VIEW, create=('gl_ar', 'gl_ap'), child_of='payment', search=('doc__number',), filters=(), title='doc', check=alloc_check, files=False, deletable=True,
      edit_when=lambda o: o.payment.status == 'draft', info=('fx_diff',)))
R(Reg('statement', BankStatement, 'Bank statement', 'Bank statements', 'Bank and cash', cols=('journal', 'number', 'date', 'opening', 'closing', 'status'),
      form=('journal', 'number', 'date', 'opening', 'closing'), manage=ACC, view=VIEW, search=('number',), filters=('journal', 'status'), title='number',
      extra=stmt_extra, children=(('Lines', 'bline', 'statement'),),
      actions=[Act('match', 'Match automatically', a_match, when(('open',), *ACC), style='primary'),
               Act('book', 'Book a line (charges, interest)', a_book, when(('open',), *ACC), fields=(('line', 'Statement line id', 'int', True), ('account', 'Account', 'opt:accounts', True))),
               Act('done', 'Reconciled', a_done, when(('open',), *ACC))],
      help='The bank statement lines matched with the book: by amount and date (and reference), charges and interest booked from the line.'))
R(Reg('bline', BankLine, 'Statement line', 'Statement lines', 'Bank and cash', cols=('date', 'description', 'reference', 'amount', 'matched'),
      form=('statement', 'date', 'description', 'reference', 'amount'), manage=ACC, view=VIEW, child_of='statement', search=('description', 'reference'), filters=('matched',),
      title='description', files=False, deletable=True, info=('matched',)))
R(Reg('vat', VatReturn, 'VAT return', 'VAT returns', 'Tax', cols=('start', 'end', 'due', 'status', 'filed_at', 'reference'), form=('start', 'end', 'adjustments'),
      manage=MGR, view=ACC, search=('reference',), filters=('status',), title='start', subtitle='end', defaults=vat_defaults, extra=vat_extra,
      edit_when=lambda o: o.status == 'draft', info=('due', 'entry'),
      actions=[Act('file', 'Filed with the FTA', a_file_vat, when(('draft',), *MGR), style='primary', fields=(('reference', 'FTA reference', 'text', False),),
                   confirm='The VAT accounts are cleared into "VAT payable to the FTA" at the end of the period.')],
      help='VAT 201 from the posted invoices, bills and journals: standard-rated supplies by emirate, reverse charge, zero rated, exempt, imports, expenses. Due 28 days after the period.'))
R(Reg('ct', CtReturn, 'Corporate tax', 'Corporate tax', 'Tax', cols=('year', 'revenue', 'profit', 'taxable', 'tax', 'sbr', 'status'), form=('year',),
      manage=MGR, view=ACC, search=(), filters=('status',), title='year', extra=ct_extra, info=('revenue', 'profit', 'taxable', 'tax', 'sbr', 'entry'),
      actions=[Act('calc', 'Calculate', a_ct_calc, when(('draft',), *MGR), style='primary'), Act('book', 'Book the tax', a_ct_book, when(('draft',), *MGR))],
      help='Accounting profit adjusted by each account\'s corporate-tax treatment; 0 % up to AED 375,000 of taxable income, 9 % above; small business relief when elected.'))


# ------------------------------------------------------------------ dashboard, my work, daily
def dashboard(request):
    st = S.settings()
    t = today()
    y = S.fiscal_year_of(t)
    pl = S.profit_and_loss(y.start, t) if y else None
    cash = sum((S.balance(j.account) for j in Journal.objects.filter(kind__in=('bank', 'cash'), is_active=True).exclude(account__isnull=True)), Decimal(0))
    ar = Document.objects.filter(kind__in=('invoice',), status__in=('posted', 'part_paid')).aggregate(s=Sum('balance'))['s'] or 0
    ap = Document.objects.filter(kind__in=('bill',), status__in=('posted', 'part_paid')).aggregate(s=Sum('balance'))['s'] or 0
    overdue = Document.objects.filter(kind='invoice', status__in=('posted', 'part_paid'), due_date__lt=t)
    pdc = Payment.objects.filter(status='pdc')
    vs, ve = S.vat_period(t)
    _, due = S.vat201(vs, ve)
    drafts = Entry.objects.filter(status='draft').count() + Document.objects.filter(status='draft').count()
    cur = st.currency
    banks = [{'label': str(j), 'value': round(float(S.balance(j.account)), 2)} for j in Journal.objects.filter(kind__in=('bank', 'cash'), is_active=True).exclude(account__isnull=True)]
    return {'cards': [{'label': f'Cash and bank ({cur})', 'value': f'{cash:,.2f}'},
                      {'label': f'Customers owe ({cur})', 'value': f'{ar:,.2f}', 'link': 'r/doc?kind=invoice&status=posted'},
                      {'label': 'Overdue invoices', 'value': overdue.count(), 'tone': 'bad' if overdue.exists() else 'ok', 'link': 'r/doc?kind=invoice&status=posted'},
                      {'label': f'We owe suppliers ({cur})', 'value': f'{ap:,.2f}', 'link': 'r/doc?kind=bill&status=posted'},
                      {'label': 'Post-dated cheques held', 'value': pdc.count(), 'link': 'r/payment?status=pdc'},
                      {'label': f'Profit this year ({cur})', 'value': f'{pl["net"]:,.2f}' if pl else '–'},
                      {'label': f'VAT this period ({vs:%d/%m} – {ve:%d/%m})', 'value': f'{due:,.2f}', 'link': 'r/vat'},
                      {'label': 'Drafts to post', 'value': drafts, 'link': 'r/entry?status=draft'},
                      {'label': 'Journals from other apps waiting', 'value': len(S.outbox_waiting()), 'link': 'r/journal?kind=outbox'}],
            'bars': [{'title': 'Cash and bank', 'items': banks}],
            'lists': [{'title': 'Overdue invoices', 'kind': 'doc', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.partner}', 'sub': f'due {d(x.due_date)}', 'badge': f'{x.balance:,.2f}'}
                                                                         for x in overdue.select_related('partner').order_by('due_date')[:10]]},
                      {'title': 'Cheques to clear this week', 'kind': 'payment', 'rows': [{'id': x.pk, 'title': f'{x.cheque_no} {x.partner}', 'sub': d(x.cheque_date), 'badge': f'{x.amount:,.2f}'}
                                                                                         for x in pdc.filter(cheque_date__lte=t + __import__('datetime').timedelta(days=7)).select_related('partner')[:10]]}]}


def my_work(request):
    out = []
    if app.any(request, *ACC):
        out.append({'title': 'Entries to post', 'kind': 'entry', 'rows': [{'id': x.pk, 'title': x.narration or 'Draft', 'badge': d(x.date)} for x in Entry.objects.filter(status='draft')[:30]]})
    if app.any(request, *AR) or app.any(request, *AP):
        out.append({'title': 'Invoices and bills to post', 'kind': 'doc', 'rows': [{'id': x.pk, 'title': f'{x.get_kind_display()} {x.partner}', 'badge': f'{x.total:,.2f}'}
                                                                                  for x in Document.objects.filter(status='draft').select_related('partner')[:30]]})
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, S.daily

from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)


# ------------------------------------------------------------------ Settings › Features: standard on, the rest when switched on
from zeo.appkit.features import Feature as _F   # noqa: E402


def _n(model, **kw):
    return lambda: model.objects.filter(**kw).count()


app.features = [
    _F('multi_currency', 'Foreign currencies', 'Currencies', 'Invoices, bills, payments and bank accounts in USD, EUR …; exchange rates; realised and unrealised exchange differences.',
       regs=('fxrate',), fields={'doc': ('currency', 'rate'), 'payment': ('currency', 'rate'), 'entry': ('currency', 'rate'), 'journal': ('currency',)},
       in_use=lambda: Document.objects.exclude(currency='').count() + FxRate.objects.count()),
    _F('pdc', 'Post-dated cheques', 'Bank', 'Cheques dated later held in "PDC received / issued" and moved to the bank on their date; returned cheques.',
       fields={'payment': ('cheque_date', 'cheque_bank'), 'journal': ('pdc_account',)}, in_use=_n(Payment, status__in=('pdc', 'cleared', 'bounced'))),
    _F('bank_rec', 'Bank statements and reconciliation', 'Bank', 'Statement lines matched with the books; charges and interest booked from the statement.',
       regs=('statement', 'bline'), in_use=_n(BankStatement)),
    _F('cost_centres', 'Cost centres', 'Analysis', 'Income and costs by department, project or activity.', regs=('costcentre',),
       fields={'eline': ('cost_centre',), 'dline': ('cost_centre',), 'account': ('cost_centre_required',)}, in_use=_n(CostCentre)),
    _F('budgets', 'Budgets', 'Analysis', 'Budget per account and month; budget against actual.', regs=('budget',), in_use=_n(Budget)),
    _F('recurring', 'Recurring entries', 'Books', 'Rent, accruals and other entries made every month, quarter or year.', regs=('recurring',), in_use=_n(Recurring)),
    _F('corporate_tax', 'Corporate tax', 'Tax', 'The corporate tax computation and the tax entry; small business relief and free-zone settings.', regs=('ct',),
       fields={'account': ('ct_treatment',)}, settings=('ct_trn', 'ct_sbr', 'ct_qfzp'), in_use=_n(CtReturn)),
    _F('einvoicing', 'E-invoicing (PINT AE)', 'Tax', 'Invoice data for the accredited service provider: invoice type, UUID, buyer TRN; the UBL file.',
       settings=('einvoicing', 'endpoint_id'), in_use=lambda: Document.objects.exclude(einv_status='').count()),
    _F('branch_books', 'Branches in the books', 'Analysis', 'Branch on every line; reports per branch.', fields={'eline': ('branch',), 'doc': ('branch',), 'payment': ('branch',),
                                                                                                                'journal': ('branch',), 'account': ('branch',), 'entry': ('branch',)}),
]
app.presets = {
    'trading': ('Trading company', ('multi_currency', 'pdc', 'bank_rec', 'corporate_tax')),
    'services': ('Services company', ('pdc', 'bank_rec', 'cost_centres', 'corporate_tax', 'recurring')),
    'group': ('Group with branches', ('multi_currency', 'pdc', 'bank_rec', 'cost_centres', 'budgets', 'branch_books', 'corporate_tax', 'recurring')),
    'all': ('Everything', tuple(f.key for f in app.features)),
}


# ------------------------------------------------------------------ hooks for the other kit apps (Sales 1.77, Purchase 1.78 …)
def h_make_doc(*, kind, partner_id, date=None, lines=(), currency='', reference='', supplier_ref='', emirate='', branch_id=None, source='', source_ref='',
               original_id=None, post=True, user=None):
    """another app makes an invoice / credit note / bill / debit note: lines [{product_id, description, qty, price, discount, tax_code, account_code}]
    → {id, number, total, status}"""
    doc = Document.objects.create(kind=kind, partner_id=partner_id, date=date or today(), currency=currency or '', reference=reference[:60],
                                  supplier_ref=supplier_ref[:60], emirate=emirate or (S.settings().emirate if kind in ('invoice', 'credit') else ''),
                                  branch_id=branch_id, source=source[:30], source_ref=str(source_ref)[:60], original_id=original_id, created_by=user)
    if doc.currency and doc.currency.upper() == S.base_currency():
        doc.currency = ''
    if doc.currency:
        doc.rate = S.fx(doc.currency, doc.date)
    from datetime import timedelta
    pt = getattr(doc.partner, 'payment_term', None)
    doc.due_date = doc.date + timedelta(days=getattr(pt, 'days', 30) if pt else 30)
    doc.save()
    use = 'sale' if kind in ('invoice', 'credit') else 'purchase'
    for x in lines:
        t = Tax.objects.filter(code=x['tax_code']).first() if x.get('tax_code') else \
            Tax.objects.filter(use=use, treatment='SR', is_active=True).order_by('id').first() if S.settings().vat_registered else None
        acc = GLAccount.objects.filter(code=x['account_code']).first() if x.get('account_code') else None
        DocumentLine.objects.create(doc=doc, product_id=x.get('product_id'), description=(x.get('description') or '')[:255], account=acc,
                                    qty=Decimal(str(x.get('qty') or 1)), price=Decimal(str(x.get('price') or 0)), discount=Decimal(str(x.get('discount') or 0)), tax=t)
    for ln in doc.lines.filter(account__isnull=True).select_related('product'):
        ln.account = (ln.product and S._product_account(ln.product, use == 'sale')) or S.account('revenue' if use == 'sale' else 'expense')
        ln.save(update_fields=['account'])
    S.calc_doc(doc)
    if post:
        run(lambda: S.post_doc(doc, user))
    doc.refresh_from_db()
    return {'id': doc.pk, 'number': doc.number, 'total': float(doc.total), 'status': doc.status}


def h_doc_status(*, doc_id):
    d_ = Document.objects.filter(pk=doc_id).first()
    return {'status': d_.status, 'balance': float(d_.balance), 'number': d_.number} if d_ else None


def h_partner_balance(*, partner_id, sale=True):
    """what the partner owes (sale) / is owed – open documents less payments on account, in the company currency; overdue part"""
    kinds = ('invoice', 'credit') if sale else ('bill', 'debit')
    t = today()
    open_ = overdue = Decimal(0)
    for x in Document.objects.filter(partner_id=partner_id, kind__in=kinds, status__in=('posted', 'part_paid')):
        amt = x.balance * (x.rate or 1) * S.doc_sign(x)
        open_ += amt
        if x.due_date and x.due_date < t and amt > 0:
            overdue += amt
    onacc = Payment.objects.filter(partner_id=partner_id, kind='receipt' if sale else 'payment', unallocated__gt=0,
                                   status__in=('posted', 'pdc', 'cleared')).aggregate(s=Sum('unallocated'))['s'] or 0
    oldest = Document.objects.filter(partner_id=partner_id, kind__in=kinds[:1], status__in=('posted', 'part_paid'), due_date__lt=t).order_by('due_date').first()
    return {'open': float(open_ - onacc), 'overdue': float(overdue), 'oldest_overdue_days': (t - oldest.due_date).days if oldest else 0}


app.hooks.update({'make_doc': h_make_doc, 'doc_status': h_doc_status, 'partner_balance': h_partner_balance})
