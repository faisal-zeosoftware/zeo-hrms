"""v1.76.0 Accounting reports in the report centre (v1.79.3: customer / supplier ledger and statement) (ZeoApps/reports.py KIT_EXTRA['accounting']): trial balance, general ledger,
profit and loss, balance sheet, cash flow, customer and supplier ageing, partner statement, VAT 201, VAT audit file, corporate tax,
budget against actual, journal register, bank reconciliation, integrity check."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Sum


def ledger_reports(a, page):
    from . import services as S
    from .models import Budget, Document, EntryLine, GLAccount, Journal
    codes = [a.code(r) for r, _ in a.roles]
    mgr = [a.code('gl_manager'), a.code('gl_accountant')]

    def f(x):
        return float(x or 0)

    def br(run):
        b = getattr(run, 'branches', None)
        return b[0] if b and len(b) == 1 else None

    def tb_list(run):
        rows = [{'code': r['code'], 'account': r['name'], 'type': r['type'], 'opening': f(r['opening']), 'debit': f(r['debit']), 'credit': f(r['credit']),
                 'closing': f(r['closing']), '_url': f'{page}r/account/{r["id"]}' if r['id'] else ''} for r in S.trial_balance(run.date_from, run.date_to, branch=br(run))]
        return ([('code', 'Code', 'text'), ('account', 'Account', 'text'), ('type', 'Type', 'text'), ('opening', 'Opening', 'number'), ('debit', 'Debit', 'number'),
                 ('credit', 'Credit', 'number'), ('closing', 'Closing', 'number')], rows,
                {'notes': ['Debit balances positive, credit balances negative. The closing column totals zero when the books balance.'], 'filters': ['type']})

    def names():
        """v1.79.3 the names of accounts, partners, cost centres once (a million lines: no object per line)"""
        from ZeoMasters.models import Partner
        from .models import CostCentre
        return ({a.pk: str(a) for a in GLAccount.objects.all()}, dict(Partner.objects.values_list('pk', 'name')),
                {c.pk: str(c) for c in CostCentre.objects.all()})

    def gl(run):
        """v1.79.3 every posting (before: the first 20,000 only – a year of a busy company has a million): read as values, a chunk at a
        time; the opening of every account in one query"""
        rows = []
        accs, parts, ccs = names()
        qs = S.lines(run.date_from, run.date_to, branch=br(run))
        q = getattr(getattr(run, 'request', None), 'query_params', {}) or {}
        only = [int(x) for x in str(q.get('accounts') or '').split(',') if x.strip().isdigit()]   # v1.79.4 drill down from a designed statement line
        if only:
            qs = qs.filter(account_id__in=only)
        qs = qs.order_by('account__code', 'date', 'id').values_list(
            'account_id', 'date', 'entry__number', 'description', 'partner_id', 'cost_centre_id', 'debit', 'credit', 'entry_id')
        op = {}
        if run.date_from:
            # v1.79.4 the opening as the trial balance has it: balance-sheet accounts from the beginning, income and expense accounts from the
            # start of the financial year (before: all earlier years too – the running balance of an income account ended above the trial balance)
            before = date.fromordinal(run.date_from.toordinal() - 1)
            y = S.fiscal_year_of(run.date_from) if hasattr(S, 'fiscal_year_of') else None
            oq = S.lines(None, before, branch=br(run)).exclude(account__type__in=('income', 'expense'))
            pq = S.lines(y.start, before, branch=br(run)).filter(account__type__in=('income', 'expense')) if y else S.lines(None, before, branch=br(run)).filter(account__type__in=('income', 'expense'))
            for q_ in (oq, pq):
                if only:
                    q_ = q_.filter(account_id__in=only)
                for r in q_.values('account_id').annotate(d=Sum('debit'), c=Sum('credit')):
                    op[r['account_id']] = (r['d'] or 0) - (r['c'] or 0)
        bal = {}
        for k, dt, num, desc, pid, cid, dr, cr, eid in qs.iterator(chunk_size=20000):
            acc = accs.get(k, '')
            if k not in bal:
                bal[k] = Decimal(op.get(k) or 0)
                rows.append({'account': acc, 'date': None, 'entry': '', 'description': 'Opening balance', 'partner': '', 'debit': None, 'credit': None, 'balance': f(bal[k])})
            bal[k] += dr - cr
            rows.append({'account': acc, 'date': dt, 'entry': num, 'description': desc, 'partner': parts.get(pid, ''), 'cost_centre': ccs.get(cid, ''),
                         'debit': f(dr), 'credit': f(cr), 'balance': f(bal[k]), '_url': f'{page}r/entry/{eid}'})
        return ([('account', 'Account', 'text'), ('date', 'Date', 'date'), ('entry', 'Entry', 'text'), ('description', 'Description', 'text'), ('partner', 'Partner', 'text'),
                 ('cost_centre', 'Cost centre', 'text'), ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number'), ('balance', 'Balance', 'number')], rows,
                {'no_totals': ('balance',), 'filters': ['account', 'partner', 'cost_centre'],
                 **({'notes': [f'Only the {len(only)} account(s) of the statement line you clicked.']} if only else {})})

    def pl_list(run):
        p = S.profit_and_loss(run.date_from, run.date_to, branch=br(run))
        labels = dict(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)
        rows = [{'section': labels.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'], 'amount': f(r['amount']), '_url': f'{page}r/account/{r["id"]}'} for r in p['rows']]
        for k, lbl in (('revenue', 'Revenue'), ('gross', 'Gross profit'), ('operating', 'Operating profit'), ('before_tax', 'Profit before tax'), ('net', 'Net profit')):
            rows.append({'section': 'Total', 'code': '', 'account': lbl, 'amount': f(p[k])})
        return ([('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'), ('amount', 'Amount', 'number')], rows,
                {'no_totals': ('amount',), 'notes': ['Income positive, expenses positive in their own sections; the totals at the end.']})

    def bs_list(run):
        b = S.balance_sheet(run.date_to, branch=br(run))
        labels = dict(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)
        rows = [{'side': r['type'].title(), 'section': labels.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'], 'amount': f(r['amount'])} for r in b['rows']]
        rows += [{'side': 'Total', 'section': '', 'code': '', 'account': k.title(), 'amount': f(b[k])} for k in ('assets', 'liabilities', 'equity')]
        return ([('side', 'Side', 'text'), ('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'), ('amount', 'Amount', 'number')], rows,
                {'no_totals': ('amount',), 'notes': [f'Assets − liabilities − equity = {b["difference"]:,.2f} (0 when the books balance).']})

    # ---------------------------------------------------------------- v1.79.3 T format (two sides: Dr | Cr, liabilities | assets)
    LAYOUT = {'key': 'layout', 'label': 'Layout', 'choices': [{'value': 'list', 'label': 'Statement'}, {'value': 't', 'label': 'T format'}]}

    def layout(run):
        q = getattr(getattr(run, 'request', None), 'query_params', {}) or {}
        return 't' if (q.get('layout') or '').lower() == 't' else 'list'

    def with_layout(fn_list, fn_t):
        def fn(run):
            lay = layout(run)
            res = fn_t(run) if lay == 't' else fn_list(run)
            extra = dict(res[2]) if len(res) > 2 else {}
            extra['options'] = [dict(LAYOUT, value=lay)] + list(extra.get('options') or [])
            return res[0], res[1], extra
        return fn

    def acc_url(i):
        return f'{page}r/account/{i}' if i else ''

    def side(lines_):
        """[(kind, label, amount, account id)] – kind 'head' (a group: its total in the outer column), 'acc' (an account), 'line' (a total
        line such as Gross profit c/d)"""
        out = []
        for kind, label, amt, aid in lines_:
            if kind == 'pad':
                out.append({'p': '', 'a': None, 't': None, 'b': False, 'u': ''})
            elif kind == 'head':
                out.append({'p': label, 'a': None, 't': f(amt), 'b': True, 'u': ''})
            elif kind == 'acc':
                out.append({'p': '    ' + label, 'a': f(amt), 't': None, 'b': False, 'u': acc_url(aid)})
            else:
                out.append({'p': label, 'a': None, 't': f(amt), 'b': True, 'u': ''})
        return out

    def t_table(left, right, lhead, rhead, ltotal, rtotal, notes, filler=True):
        L, R = side(left), side(right)
        rows = []
        for i in range(max(len(L), len(R))):
            l = L[i] if i < len(L) else {}
            r = R[i] if i < len(R) else {}
            row = {'l_p': l.get('p', ''), 'l_a': l.get('a'), 'l_t': l.get('t'), 'r_p': r.get('p', ''), 'r_a': r.get('a'), 'r_t': r.get('t'),
                   '_lb': l.get('b', False), '_rb': r.get('b', False)}   # bold lines (groups, totals) for the printed T format
            cell = {k: v for k, v in (('l_p', l.get('u')), ('r_p', r.get('u'))) if v}
            if cell:
                row['_cell'] = cell
            rows.append(row)
        rows.append({'l_p': 'Total', 'l_t': f(ltotal), 'r_p': 'Total', 'r_t': f(rtotal), '_lb': True, '_rb': True, '_tot': True})
        cur = S.base_currency()
        cols = [('l_p', lhead, 'text'), ('l_a', f'Amount ({cur})', 'number'), ('l_t', f'Total ({cur})', 'number'),
                ('r_p', rhead, 'text'), ('r_a', f'Amount ({cur})', 'number'), ('r_t', f'Total ({cur})', 'number')]
        return cols, rows, {'no_totals': ('l_a', 'l_t', 'r_a', 'r_t'), 'notes': notes, 't_format': True}

    def labels():
        return dict(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)

    def grouped(rows_, groups, sign=1):
        """per group in the order given: ('head', group label, group total) then its accounts"""
        lab = labels()
        out, tot = [], Decimal(0)
        for g in groups:
            acc = [r for r in rows_ if r['group'] == g and r['amount']]
            if not acc:
                continue
            gt = sum((r['amount'] * sign for r in acc), Decimal(0))
            tot += gt
            out.append(('head', lab.get(g, g), gt, None))
            out += [('acc', f"{r['code']} {r['name']}".strip(), r['amount'] * sign, r.get('id')) for r in acc]
        return out, tot

    def tb_t(run):
        dr, cr = [], []
        tdr = tcr = Decimal(0)
        for r in S.trial_balance(run.date_from, run.date_to, branch=br(run)):
            c = r['closing']
            if not c:
                continue
            if c > 0:
                dr.append(('acc', f"{r['code']} {r['name']}".strip(), c, r['id']))
                tdr += c
            else:
                cr.append(('acc', f"{r['code']} {r['name']}".strip(), -c, r['id']))
                tcr += -c
        return t_table(dr, cr, 'Debit balances', 'Credit balances', tdr, tcr,
                       [f'Closing balances at {run.date_to:%d/%m/%Y}: debit balances on the left, credit balances on the right – the two totals are equal when the books balance '
                        f'(difference {tdr - tcr:,.2f}). Click an account to open it.'])

    def pl_t(run):
        p = S.profit_and_loss(run.date_from, run.date_to, branch=br(run))
        rows_ = p['rows']
        lab = labels()

        def accs(g):
            return [('acc', f"{r['code']} {r['name']}".strip(), r['amount'], r['id']) for r in rows_ if r['group'] == g and r['amount']]
        # Trading account: cost of sales | revenue – gross profit / loss carried down
        left, right = [], []
        if p['cost_of_sales']:
            left += [('head', lab['cost_of_sales'], p['cost_of_sales'], None)] + accs('cost_of_sales')
        if p['revenue']:
            right += [('head', lab['revenue'], p['revenue'], None)] + accs('revenue')
        gross = p['gross']
        if gross >= 0:
            left.append(('line', 'Gross profit c/d', gross, None))
        else:
            right.append(('line', 'Gross loss c/d', -gross, None))
        trade_total = p['cost_of_sales'] + max(gross, 0)
        # balance the two sides of the trading part, then the profit and loss part below it
        n = max(len(left), len(right))
        left += [('pad', '', None, None)] * (n - len(left))
        right += [('pad', '', None, None)] * (n - len(right))
        left.append(('line', 'Total (trading)', trade_total, None))
        right.append(('line', 'Total (trading)', trade_total, None))
        if gross >= 0:
            right.append(('line', 'Gross profit b/d', gross, None))
        else:
            left.append(('line', 'Gross loss b/d', -gross, None))
        for g in ('expense', 'depreciation', 'finance_cost', 'other_expense', 'income_tax'):
            a = accs(g)
            if a:
                left += [('head', lab[g], sum((x[2] for x in a), Decimal(0)), None)] + a
        if p['other_income']:
            right += [('head', lab['other_income'], p['other_income'], None)] + accs('other_income')
        net = p['net']
        if net >= 0:
            left.append(('line', 'Net profit', net, None))
        else:
            right.append(('line', 'Net loss', -net, None))
        exp_total = p['operating_expenses'] + p['finance_cost'] + p['other_expense'] + p['income_tax'] + max(-gross, 0) + max(net, 0)
        inc_total = max(gross, 0) + p['other_income'] + max(-net, 0)
        cols, rows, extra = t_table([x for x in left], [x for x in right], 'Expenses (Dr)', 'Income (Cr)', exp_total, inc_total,
                                    [f'Trading and profit and loss account {run.date_from:%d/%m/%Y} – {run.date_to:%d/%m/%Y}. Top: cost of sales against revenue, the gross profit '
                                     f'carried down. Below: the gross profit brought down and other income against the expenses; the net profit balances the two sides '
                                     f'(net profit {net:,.2f}). Click an account to open it.'])
        return cols, rows, extra

    def bs_t(run):
        b = S.balance_sheet(run.date_to, branch=br(run))
        rows_ = b['rows']
        liab, tl = grouped([r for r in rows_ if r['type'] == 'equity'], ('equity', 'retained'))
        l2, t2 = grouped([r for r in rows_ if r['type'] == 'liability'], ('non_current_liability', 'payable', 'current_liability', 'tax'))
        assets, ta = grouped([r for r in rows_ if r['type'] == 'asset'],
                             ('fixed_asset', 'accum_depr', 'non_current_asset', 'inventory', 'receivable', 'prepayment', 'current_asset', 'bank', 'cash'))
        return t_table(liab + l2, assets, 'Capital and liabilities', 'Assets', tl + t2, ta,
                       [f'Balance sheet at {run.date_to:%d/%m/%Y} in T format: capital and liabilities on the left, assets on the right '
                        f'(difference {ta - tl - t2:,.2f}; 0 when the books balance). Click an account to open it.'])

    tb = with_layout(tb_list, tb_t)
    pl = with_layout(pl_list, pl_t)
    bs = with_layout(bs_list, bs_t)

    def cf(run):
        c = S.cash_flow(run.date_from, run.date_to, branch=br(run))
        rows = [{'line': lbl, 'amount': f(c[k])} for k, lbl in (('profit', 'Net profit'), ('depreciation', 'Add back depreciation'), ('working_capital', 'Change in working capital'),
                                                                 ('operating', 'Cash from operating activities'), ('investing', 'Cash from investing activities'),
                                                                 ('financing', 'Cash from financing activities'), ('net_change', 'Net change in cash and bank'))]
        return [('line', 'Line', 'text'), ('amount', 'Amount', 'number')], rows, {'no_totals': ('amount',)}

    def age(sale):
        def fn(run):
            heads, rows = S.ageing(run.date_to, sale)
            out = [{'partner': r['partner'], **{h: f(r[h]) for h in heads}, 'total': f(r['total'])} for r in rows]
            return [('partner', 'Customer' if sale else 'Supplier', 'text')] + [(h, h.title() if not h[0].isdigit() else f'{h} days', 'number') for h in heads] + [('total', 'Total', 'number')], out
        return fn

    def vat(run):
        rows, due = S.vat201(run.date_from, run.date_to)
        return ([('box', 'Box', 'text'), ('label', 'Description', 'text'), ('amount', 'Amount (AED)', 'number'), ('vat', 'VAT (AED)', 'number'), ('adjustment', 'Adjustment', 'number')],
                [{**r, 'amount': f(r['amount']), 'vat': f(r['vat']), 'adjustment': f(r['adjustment'])} for r in rows],
                {'no_totals': ('amount', 'vat', 'adjustment'), 'notes': [f'Payable tax for the period: {due:,.2f} (box 14). The return and payment are due 28 days after the period end.']})

    def vat_audit(run):
        """v1.79.3 read as values (hundreds of thousands of VAT lines)"""
        from ZeoMasters.models import Partner
        from .models import Tax
        trn = dict(Partner.objects.values_list('pk', 'tax_id'))
        pn = dict(Partner.objects.values_list('pk', 'name'))
        tx = dict(Tax.objects.values_list('pk', 'code'))
        rows = []
        qs = S.lines(run.date_from, run.date_to).exclude(vat_box='').order_by('date', 'id').values_list(
            'date', 'entry__number', 'partner_id', 'entry__partner_id', 'vat_box', 'emirate', 'tax_id', 'tax_base', 'debit', 'credit', 'entry_id')
        for dt, num, pid, epid, box, em, tid, base, dr, cr, eid in qs.iterator(chunk_size=20000):
            p = pid or epid
            rows.append({'date': dt, 'entry': num, 'partner': pn.get(p, ''), 'trn': trn.get(p, '') or '', 'box': box, 'emirate': em, 'tax': tx.get(tid, ''),
                         'taxable': f(base) if base is not None else None, 'vat': f(cr - dr) if base is None else None, '_url': f'{page}r/entry/{eid}'})
        return ([('date', 'Date', 'date'), ('entry', 'Entry', 'text'), ('partner', 'Partner', 'text'), ('trn', 'TRN', 'text'), ('box', 'Box', 'text'), ('emirate', 'Emirate', 'text'),
                 ('tax', 'Tax', 'text'), ('taxable', 'Taxable amount', 'number'), ('vat', 'VAT', 'number')], rows, {'filters': ['box', 'tax', 'emirate']})

    def ct(run):
        from .models import FiscalYear
        y = FiscalYear.objects.filter(start__lte=run.date_to, end__gte=run.date_to).first() or S.fiscal_year_of(run.date_to)
        c = S.corporate_tax(y)
        rows = [{'line': 'Revenue', 'amount': f(c['revenue'])}, {'line': 'Accounting profit before tax', 'amount': f(c['profit'])}]
        rows += [{'line': x['label'], 'amount': f(x['amount'])} for x in c['adjustments']]
        rows += [{'line': 'Taxable income', 'amount': f(c['taxable'])}, {'line': 'Corporate tax', 'amount': f(c['tax'])}]
        return [('line', 'Line', 'text'), ('amount', 'AED', 'number')], rows, {'no_totals': ('amount',), 'notes': [c['note']]}

    def budget(run):
        rows = []
        for b in Budget.objects.select_related('account', 'cost_centre', 'year'):
            if not (b.year.start <= run.date_to and b.year.end >= run.date_from):
                continue
            if not _budget_in_branch(b, br(run)):   # v1.79.3: a branch is chosen – the budgets of its cost centres
                continue
            months = [Decimal(str(x or 0)) for x in (b.amounts or [])] + [Decimal(0)] * 12
            plan = Decimal(0)
            for i, p in enumerate(b.year.periods.all()):
                if p.start >= run.date_from and p.end <= run.date_to:
                    plan += months[i]
            actual = S.lines(run.date_from, run.date_to, cost_centre=b.cost_centre_id).filter(account=b.account).aggregate(d=Sum('debit'), c=Sum('credit'))
            act = (actual['d'] or 0) - (actual['c'] or 0)
            if b.account.type == 'income':
                act = -act
            rows.append({'account': str(b.account), 'cost_centre': str(b.cost_centre or ''), 'budget': f(plan), 'actual': f(act), 'variance': f(Decimal(act) - plan),
                         'used': round(float(act) / float(plan) * 100, 1) if plan else None})
        return ([('account', 'Account', 'text'), ('cost_centre', 'Cost centre', 'text'), ('budget', 'Budget', 'number'), ('actual', 'Actual', 'number'),
                 ('variance', 'Variance', 'number'), ('used', '% used', 'number')], rows, {'no_totals': ('used',)})

    def bank(run):
        rows = []
        for j in Journal.objects.filter(kind__in=('bank', 'cash'), is_active=True).exclude(account__isnull=True):
            r = S.reconciliation(j, run.date_to)
            rows.append({'bank': str(j), 'book': f(r['book']), 'statement': f(r['statement']) if r['statement'] is not None else None, 'not_on_statement_in': f(r['outstanding_in']),
                         'not_on_statement_out': f(r['outstanding_out']), 'not_in_books': f(r['unmatched_statement'])})
        return ([('bank', 'Bank / cash', 'text'), ('book', 'Book balance', 'number'), ('statement', 'Last statement', 'number'), ('not_on_statement_in', 'Receipts not on the statement', 'number'),
                 ('not_on_statement_out', 'Payments not on the statement', 'number'), ('not_in_books', 'Statement lines not in the books', 'number')], rows)

    def journals(run):
        """v1.79.3 every line (before: the first 20,000 only)"""
        accs = {a.pk: str(a) for a in GLAccount.objects.all()}
        jn = dict(Journal.objects.values_list('pk', 'code'))
        rows = []
        qs = S.lines(run.date_from, run.date_to).order_by('date', 'entry_id', 'id').values_list(
            'date', 'entry__journal_id', 'entry__number', 'entry__narration', 'account_id', 'debit', 'credit', 'entry_id')
        for dt, jid, num, nar, aid, dr, cr, eid in qs.iterator(chunk_size=20000):
            rows.append({'date': dt, 'journal': jn.get(jid, ''), 'entry': num, 'narration': nar, 'account': accs.get(aid, ''),
                         'debit': f(dr), 'credit': f(cr), '_url': f'{page}r/entry/{eid}'})
        return ([('date', 'Date', 'date'), ('journal', 'Journal', 'text'), ('entry', 'Entry', 'text'), ('narration', 'Narration', 'text'), ('account', 'Account', 'text'),
                 ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number')], rows, {'filters': ['journal']})

    def integrity(run):
        r = S.integrity()
        rows = [{'check': 'Ledger debits = credits', 'result': 'OK' if r['ledger_balanced'] else 'Different', 'detail': f'{r["debit"]:,.2f} / {r["credit"]:,.2f}'},
                {'check': 'Every posted entry balances', 'result': 'OK' if not r['entries_out_of_balance'] else 'Different', 'detail': '; '.join(r['entries_out_of_balance'][:10])}]
        b = S.balance_sheet(run.date_to)
        rows.append({'check': 'Balance sheet balances', 'result': 'OK' if not b['difference'] else 'Different', 'detail': f'{b["difference"]:,.2f}'})
        for role, kinds in (('receivable', ('invoice', 'credit')), ('payable', ('bill', 'debit'))):
            try:
                acc = S.account(role)
            except S.LedgerError:
                continue
            gl = S.balance(acc, run.date_to)
            docs = sum((x.balance * (x.rate or 1) * S.doc_sign(x) for x in Document.objects.filter(kind__in=kinds, status__in=('posted', 'part_paid'), date__lte=run.date_to)), Decimal(0))
            from .models import Payment
            onacc = sum((p.unallocated * (p.rate or 1) for p in Payment.objects.filter(kind='receipt' if role == 'receivable' else 'payment', unallocated__gt=0,
                                                                                       status__in=('posted', 'pdc', 'cleared'), date__lte=run.date_to)), Decimal(0))
            sub = docs - onacc if role == 'receivable' else -(docs - onacc)
            rows.append({'check': f'{acc} = open {"invoices" if role == "receivable" else "bills"}', 'result': 'OK' if abs(gl - S.r2(sub)) < Decimal('0.05') else 'Different',
                         'detail': f'GL {gl:,.2f} – documents {S.r2(sub):,.2f} (other postings to the control account show as a difference)'})
        return [('check', 'Check', 'text'), ('result', 'Result', 'text'), ('detail', 'Detail', 'text')], rows

    def pname(p):
        return f'{p.name} ({p.number})' if p is not None and getattr(p, 'number', '') else str(p or '')

    def opts(run):   # v1.79.3 report options (buttons on the report page): post-dated cheques and foreign currency
        q = getattr(getattr(run, 'request', None), 'query_params', {}) or {}
        pdc = (q.get('pdc') or 'yes').lower() != 'no'
        fc = (q.get('fc') or 'no').lower()
        fc = fc if fc in ('no', 'show', 'only') else 'no'
        return pdc, fc

    def options(pdc, fc, sale):
        return [{'key': 'pdc', 'label': 'Post-dated cheques', 'value': 'yes' if pdc else 'no',
                 'choices': [{'value': 'yes', 'label': 'Include PDC'}, {'value': 'no', 'label': 'Without PDC'}]},
                {'key': 'fc', 'label': 'Foreign currency', 'value': fc,
                 'choices': [{'value': 'no', 'label': f'{S.base_currency()} only'}, {'value': 'show', 'label': 'Show foreign currency'},
                             {'value': 'only', 'label': 'In the transaction currency'}]}]

    def opt_note(pdc, fc, sale):
        word = 'received' if sale else 'issued'
        p = (f'Post-dated cheques: included (a cheque {word} and still held is shown).' if pdc else
             f'Post-dated cheques: left out – the cheques {word} and still held at the end date are not shown and not deducted (balance before the cheques).')
        c = {'no': f'Amounts in {S.base_currency()}.', 'show': f'Amounts in {S.base_currency()}, with the transaction currency and its amount.',
             'only': 'Amounts in the transaction currency: one section per customer and currency, every amount and the balance in that currency.'}[fc]
        return f'Options – {p} {c}'

    def pname(p, cur=None):
        n = f'{p.name} ({p.number})' if p is not None and getattr(p, 'number', '') else str(p or '')
        return f'{n} – {cur}' if cur else n

    def tag(rows, start, g):   # the rows of one partner carry its id and currency (hidden – for the printed statement)
        for r in rows[start:]:
            r['_pid'], r['_cur'] = g['partner_id'], g['currency']

    def party_ledger(sale):   # v1.79.3 every transaction of every customer / supplier with the running balance
        def fn(run):
            pdc, fc = opts(run)
            only = fc == 'only'
            rows = []
            for g in S.partner_ledger(run.date_from, run.date_to, sale, branch=br(run), include_pdc=pdc, in_currency=only):
                start = len(rows)
                who, trn = pname(g['partner'], g['currency'] if only else None), getattr(g['partner'], 'tax_id', '') or ''
                for r in g['rows']:
                    if r['kind'] == 'opening':
                        rows.append({'partner': who, 'trn': trn, 'date': r['date'], 'type': 'Opening balance', 'description': 'Balance brought forward',
                                     'currency': g['currency'], 'balance': f(r['balance'])})
                        continue
                    rows.append({'partner': who, 'trn': trn, 'date': r['date'], 'type': r['type'], 'number': r['number'], 'reference': r['reference'],
                                 'description': r['description'], 'due': r['due'], 'currency': r['currency'], 'amount_currency': f(r['amount_currency']),
                                 'debit': f(r['debit']), 'credit': f(r['credit']), 'balance': f(r['balance']), '_url': page + r['url']})
                rows.append({'partner': who, 'trn': trn, 'date': run.date_to, 'type': 'Closing balance', 'currency': g['currency'],
                             'description': f'Debit {g["debit"]:,.2f} · credit {g["credit"]:,.2f}', 'balance': f(g['closing'])})
                tag(rows, start, g)
            cur = 'currency' if only else S.base_currency()
            cols = [('partner', 'Customer' if sale else 'Supplier', 'text'), ('trn', 'TRN', 'text'), ('date', 'Date', 'date'), ('type', 'Transaction', 'text'),
                    ('number', 'Number', 'text'), ('reference', 'Reference', 'text'), ('description', 'Description', 'text'), ('due', 'Due date', 'date')]
            if fc in ('show', 'only'):
                cols.append(('currency', 'Currency', 'text'))
            if fc == 'show':
                cols.append(('amount_currency', 'Amount in currency', 'number'))
            cols += [('debit', f'Debit ({cur})', 'number'), ('credit', f'Credit ({cur})', 'number'), ('balance', f'Balance ({cur})', 'number')]
            return (cols, rows,
                    {'no_totals': ('amount_currency', 'balance') + (('debit', 'credit') if only else ()), 'filters': ['partner', 'type', 'currency'],
                     'options': options(pdc, fc, sale),
                     'notes': [opt_note(pdc, fc, sale),
                               f'Every posting on the {"customer" if sale else "supplier"} control account{"s" if len(S.control_accounts(sale)) > 1 else ""} from '
                               f'{run.date_from:%d/%m/%Y} to {run.date_to:%d/%m/%Y}: invoices, notes, receipts and payments, journals, '
                               f'exchange differences and returned cheques. Balance: + {"the customer owes us" if sale else "we owe the supplier"}. '
                               f'Click a line to open the document. Choose one {"customer" if sale else "supplier"} in the filter for a ledger to print.']})
        return fn

    def party_statement(sale):   # v1.79.3 open items only, with the post-dated cheques held under them
        def fn(run):
            pdc, fc = opts(run)
            only = fc == 'only'
            rows = []
            on = run.date_to
            pdc_word = 'PDC received' if sale else 'PDC issued'
            for g in S.open_items(on, sale, branch=br(run), include_pdc=pdc, in_currency=only):
                start = len(rows)
                gc = g['currency']
                who, trn = pname(g['partner'], gc if only else None), getattr(g['partner'], 'tax_id', '') or ''
                for r in g['items']:
                    rows.append({'partner': who, 'trn': trn, 'section': 'Open items', 'type': r['type'], 'number': r['number'], 'date': r['date'], 'due': r['due'],
                                 'days': r['days'] or None, 'reference': r['reference'], 'currency': r['currency'], 'amount_currency': f(r['open_currency']),
                                 'outstanding': f(r['open_currency'] if only else r['open']), '_url': page + r['url']})
                for r in g['pdc']:
                    rows.append({'partner': who, 'trn': trn, 'section': pdc_word, 'type': f'Cheque {r["cheque_no"]}'.strip(), 'number': r['number'], 'date': r['date'],
                                 'due': r['cheque_date'], 'reference': ' · '.join(x for x in (r['bank'], f'against {r["against"]}') if x),
                                 'currency': r['currency'], 'amount_currency': f(r['amount_currency']), 'pdc': f(r['amount_currency'] if only else r['amount']),
                                 '_url': page + r['url']})
                rows.append({'partner': who, 'trn': trn, 'section': 'Balance', 'type': 'Net balance after PDC' if g['pdc'] else 'Balance due', 'date': on,
                             'reference': (f'Outstanding {g["outstanding"]:,.2f} − {pdc_word} {g["pdc_total"]:,.2f} = {g["net"]:,.2f}' if g['pdc']
                                           else f'Outstanding {g["outstanding"]:,.2f}') + (f' {gc}' if only else ''),
                             'currency': gc, 'net': f(g['net'])})
                tag(rows, start, g)
            cur = 'currency' if only else S.base_currency()
            cols = [('partner', 'Customer' if sale else 'Supplier', 'text'), ('trn', 'TRN', 'text'), ('section', 'Section', 'text'), ('type', 'Document', 'text'),
                    ('number', 'Number', 'text'), ('date', 'Date', 'date'), ('due', 'Due / cheque date' if pdc else 'Due date', 'date'),
                    ('days', 'Overdue (days)', 'number'), ('reference', 'Reference', 'text')]
            if fc in ('show', 'only'):
                cols.append(('currency', 'Currency', 'text'))
            if fc == 'show':
                cols.append(('amount_currency', 'Open in currency', 'number'))
            cols.append(('outstanding', f'Outstanding ({cur})', 'number'))
            if pdc:
                cols.append(('pdc', f'{pdc_word} ({cur})', 'number'))
            cols.append(('net', f'Net balance ({cur})', 'number'))
            return (cols, rows,
                    {'no_totals': ('days', 'amount_currency') + (('outstanding', 'pdc', 'net') if only else ()), 'filters': ['partner', 'section', 'currency'],
                     'options': options(pdc, fc, sale),
                     'notes': [opt_note(pdc, fc, sale),
                               f'Statement of account at {on:%d/%m/%Y} (the end date of the period): only what is still open – '
                               f'{"invoices and credit notes" if sale else "bills and debit notes"} not yet settled and '
                               f'{"receipts" if sale else "payments"} on account.' + (
                                   f' Outstanding is before the post-dated cheques: a cheque held on the date is listed under "{pdc_word}" with its number, '
                                   f'date, bank and the documents it pays; the last line of each {"customer" if sale else "supplier"} is the net balance after the cheques.'
                                   if pdc else '') + ' Click a line to open the document.']})
        return fn

    # ---------------------------------------------------------------- v1.79.3 period-wise, comparative, budget by month, ratios
    group_label = dict(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)
    PL_TOTALS = (('revenue', 'Revenue'), ('gross', 'Gross profit'), ('operating', 'Operating profit'), ('before_tax', 'Profit before tax'), ('net', 'Net profit'))

    def start_of(run):
        """from date of a period report – the start of the year of the to date when the period has no start"""
        return run.date_from if run.date_from and run.date_from.year > 1900 else run.date_to.replace(month=1, day=1)

    def pl_monthly(run):
        d0, d1 = start_of(run), run.date_to
        months = _months(d0, d1)
        accts, tots = {}, {k: {} for k, _ in PL_TOTALS}
        for ms, me in months:
            p = S.profit_and_loss(ms, me, branch=br(run))
            key = _mkey(ms)
            for r in p['rows']:
                a = accts.setdefault(r['id'], {'section': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'], 'total': 0.0,
                                               '_url': f'{page}r/account/{r["id"]}', '_order': (r['type'] != 'income', r['code'])})
                a[key] = f(r['amount'])
                a['total'] += f(r['amount'])
            for k, _ in PL_TOTALS:
                tots[k][key] = f(p[k])
        rows = sorted(accts.values(), key=lambda a: a.pop('_order'))
        for k, lbl in PL_TOTALS:
            rows.append({'section': 'Total', 'code': '', 'account': lbl, **tots[k], 'total': round(sum(tots[k].values()), 2)})
        for r in rows:
            r['total'] = round(r['total'], 2)
        cols = [(_mkey(ms), ms.strftime('%b %Y'), 'number') for ms, _ in months]
        notes = [f'{len(months)} month(s), {months[0][0]:%d/%m/%Y} – {months[-1][1]:%d/%m/%Y}. The Total column equals Profit and loss for the same period.']
        if _months(d0, d1, cap=None) != months:
            notes.append(f'Only the first {len(months)} months are shown – choose a shorter period for the rest.')
        return ([('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text')] + cols + [('total', 'Total', 'number')], rows,
                {'no_totals': tuple(c[0] for c in cols) + ('total',), 'notes': notes, 'filters': ['section']})

    def pl_compare(run):
        d0, d1 = start_of(run), run.date_to
        p0, p1 = _year_back(d0), _year_back(d1)
        cur, prev = S.profit_and_loss(d0, d1, branch=br(run)), S.profit_and_loss(p0, p1, branch=br(run))
        accts = {}
        for which, p in (('current', cur), ('previous', prev)):
            for r in p['rows']:
                a = accts.setdefault(r['id'], {'section': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'], 'current': 0.0,
                                               'previous': 0.0, '_url': f'{page}r/account/{r["id"]}', '_order': (r['type'] != 'income', r['code'])})
                a[which] = f(r['amount'])
        rows = sorted(accts.values(), key=lambda a: a.pop('_order'))
        rows += [{'section': 'Total', 'code': '', 'account': lbl, 'current': f(cur[k]), 'previous': f(prev[k])} for k, lbl in PL_TOTALS]
        for r in rows:
            _change(r)
        return ([('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'),
                 ('current', f'{d0:%d/%m/%Y} – {d1:%d/%m/%Y}', 'number'), ('previous', f'{p0:%d/%m/%Y} – {p1:%d/%m/%Y}', 'number'),
                 ('change', 'Change', 'number'), ('change_pct', 'Change %', 'number')], rows,
                {'no_totals': ('current', 'previous', 'change', 'change_pct'), 'filters': ['section'],
                 'notes': ['The same period one year earlier. Change % is against last year (blank when last year was zero).']})

    # ---------------------------------------------------------------- v1.79.5 dimensions and account groups
    def dim_names(field):
        """labels of a dimension's values: cost centre 'code name', branch name; None = lines without it"""
        if field == 'cost_centre_id':
            from .models import CostCentre
            return {c.pk: (c.code, f'{c.code} {c.name}') for c in CostCentre.objects.all()}, 'No cost centre'
        from django.apps import apps
        B = apps.get_model('OrganisationManager', 'brnch_mstr')
        return {b.pk: (str(b.branch_name or b.pk), str(b.branch_name or b.pk)) for b in B.objects.all()}, 'No branch'

    def split_scope(run, field):
        """(kw for the ledger lines, the dimension values allowed or None = all incl. lines without it)"""
        if field == 'cost_centre_id':
            return {'branch': br(run)}, None
        b = getattr(run, 'branches', None)
        return {}, (set(b) if b else None)

    def pl_variance_table(run, field=None):
        """this year against last year: a column for each month, quarter and half year of the financial year and the year;
        four lines per account and profit line – per value of a dimension (cost centre / branch) when field is given"""
        d0, d1 = start_of(run), run.date_to
        months = _months(d0, d1, cap=12)
        mk = [_mkey(ms) for ms, _ in months]
        from .models import FiscalYear
        fy = FiscalYear.objects.filter(start__lte=d0, end__gte=d0).first()
        anchor = fy.start if fy else d0.replace(month=1, day=1)   # quarters and halves of the financial year
        idx = [((ms.year - anchor.year) * 12 + ms.month - anchor.month) % 12 for ms, _ in months]
        buckets = []
        for size, pre in ((3, 'q'), (6, 'h')):
            seen = {}
            for k, i in zip(mk, idx):
                seen.setdefault(f'{pre}{i // size + 1}', []).append(k)
            buckets += list(seen.items())
        buckets.append(('year', mk))
        if field:
            kw, allowed = split_scope(run, field)
            names, none_label = dim_names(field)
        lines = {}   # (dimension, key) → {'section', 'code', 'account', '_url', '_order', 'current': {col: v}, 'previous': {col: v}}
        for col, (ms, me) in zip(mk, months):
            for which, (a, b) in (('current', (ms, me)), ('previous', (_year_back(ms), _year_back(me)))):
                parts = _pl_split(S, a, b, field, **kw) if field else {None: S.profit_and_loss(a, b, branch=br(run))}
                for g, p in parts.items():
                    if field and allowed is not None and g not in allowed:
                        continue
                    go = (1, '') if g is None else (0, names.get(g, (str(g),))[0])
                    for r in p['rows']:
                        x = lines.setdefault((g, r['id']), {'section': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'], 'dim': g,
                                                            '_url': f'{page}r/account/{r["id"]}', '_order': (go, 0, r['type'] != 'income', r['code']),
                                                            'current': {}, 'previous': {}})
                        x[which][col] = f(r['amount'])
                    for i, (k, lbl) in enumerate(PL_TOTALS):
                        x = lines.setdefault((g, k), {'section': 'Total', 'code': '', 'account': lbl, 'dim': g, '_order': (go, 1, i, ''), 'current': {}, 'previous': {}})
                        x[which][col] = f(p[k])
        rows = []
        allcols = mk + [b for b, _ in buckets]
        for x in sorted(lines.values(), key=lambda x: x['_order']):
            cur, prev = x['current'], x['previous']
            for b, cols in buckets:
                cur[b] = round(sum(cur.get(c, 0) for c in cols), 2)
                prev[b] = round(sum(prev.get(c, 0) for c in cols), 2)
            base = {'section': x['section'], 'code': x['code'], 'account': x['account'], **({'_url': x['_url']} if '_url' in x else {})}
            if field:
                base = {'dimension': none_label if x['dim'] is None else names.get(x['dim'], (0, str(x['dim'])))[1], **base}
            var = {c: round(cur.get(c, 0) - prev.get(c, 0), 2) for c in allcols}
            pct = {c: (round(var[c] / abs(prev.get(c, 0)) * 100, 1) if prev.get(c) else None) for c in allcols}
            rows += [{**base, 'measure': 'This year', **{c: cur.get(c, 0) for c in allcols}},
                     {**base, 'measure': 'Last year', **{c: prev.get(c, 0) for c in allcols}},
                     {**base, 'measure': 'Variance', **var},
                     {**base, 'measure': 'Variance %', **pct}]
        def span(cols):
            a, b = months[mk.index(cols[0])][0], months[mk.index(cols[-1])][1]
            return f'{a:%b}' if cols[0] == cols[-1] else f'{a:%b}–{b:%b}'
        qcols = [(b, f'{b.upper()} {span(c)}', 'number') for b, c in buckets if b != 'year']
        dim_col = [('dimension', 'Cost centre' if field == 'cost_centre_id' else 'Branch', 'text')] if field else []
        cols = (dim_col + [('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'), ('measure', 'Line', 'text')]
                + [(k, f'{ms:%b %Y}', 'number') for k, (ms, _) in zip(mk, months)] + qcols + [('year', 'Year' if len(mk) == 12 else 'Period total', 'number')])
        notes = [f'This year {months[0][0]:%d/%m/%Y} – {months[-1][1]:%d/%m/%Y} against the same days one year earlier. '
                 'Four lines per account: this year, last year, variance (this year − last year) and variance % (against last year; blank when last year was zero).',
                 f'Quarters and halves of the financial year (starting {anchor:%d %B}); a quarter or half only partly in the period holds its months in the period. '
                 'Filter Line to Variance to see only the variances.']
        if field:
            notes.append(f'One block per {"cost centre" if field == "cost_centre_id" else "branch"} of the posting lines; "{none_label}" holds the lines without one. '
                         'The blocks add up to the company figures.')
        if _months(d0, d1, cap=None) != months:
            notes.append('Only the first 12 months of the period are shown – choose one year.')
        if field == 'branch_id' and allowed is not None:
            notes.append('Only the branches chosen (or that you may see) are shown.')
        elif br(run):
            notes.append('A branch is chosen: the postings of that branch.')
        filters = (['dimension'] if field else []) + ['measure', 'section', 'account']
        return (cols, rows, {'no_totals': tuple(c[0] for c in cols if c[2] == 'number'), 'notes': notes, 'filters': filters})

    def pl_variance(run):
        return pl_variance_table(run)

    def pl_variance_cc(run):
        return pl_variance_table(run, 'cost_centre_id')

    def pl_variance_branch(run):
        return pl_variance_table(run, 'branch_id')

    def pl_by(field):
        def report(run):
            """Profit and loss with a column for each cost centre / branch, the lines without one and the total"""
            d0, d1 = start_of(run), run.date_to
            kw, allowed = split_scope(run, field)
            names, none_label = dim_names(field)
            parts = {g: p for g, p in _pl_split(S, d0, d1, field, **kw).items() if allowed is None or g in allowed}
            order = sorted(parts, key=lambda g: (1, '') if g is None else (0, names.get(g, (str(g),))[0]))
            key = lambda g: 'd_none' if g is None else f'd_{g}'
            accts, tots = {}, {k: {} for k, _ in PL_TOTALS}
            for g in order:
                p = parts[g]
                for r in p['rows']:
                    x = accts.setdefault(r['id'], {'section': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'],
                                                   '_url': f'{page}r/account/{r["id"]}', '_order': (r['type'] != 'income', r['code'])})
                    x[key(g)] = f(r['amount'])
                for k, _ in PL_TOTALS:
                    tots[k][key(g)] = f(p[k])
            rows = sorted(accts.values(), key=lambda x: x.pop('_order'))
            rows += [{'section': 'Total', 'code': '', 'account': lbl, **tots[k]} for k, lbl in PL_TOTALS]
            for r in rows:
                for g in order:
                    r.setdefault(key(g), 0.0)
                r['total'] = round(sum(r[key(g)] for g in order), 2)
            cols = [(key(g), none_label if g is None else names.get(g, (0, str(g)))[1], 'number') for g in order]
            what = 'cost centre' if field == 'cost_centre_id' else 'branch'
            notes = [f'{d0:%d/%m/%Y} – {d1:%d/%m/%Y}: a column for each {what} of the posting lines, "{none_label}" for the lines without one, and the total. '
                     + ('The total equals Profit and loss.' if allowed is None else 'Only the branches chosen (or that you may see) are shown; the total is theirs.')]
            if field == 'cost_centre_id' and br(run):
                notes.append('A branch is chosen: the postings of that branch, by cost centre.')
            return ([('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text')] + cols + [('total', 'Total', 'number')], rows,
                    {'no_totals': tuple(c[0] for c in cols) + ('total',), 'notes': notes, 'filters': ['section']})
        return report

    def tb_group(run):
        rows = []
        order = {g: i for i, (g, _) in enumerate(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)}
        for r in S.trial_balance(run.date_from, run.date_to, branch=br(run)):
            rows.append({'type': r['type'].title(), 'group': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'],
                         'opening': f(r['opening']), 'debit': f(r['debit']), 'credit': f(r['credit']), 'closing': f(r['closing']),
                         '_order': (order.get(r['group'], 99), r['code'] or 'zz'), **({'_url': f'{page}r/account/{r["id"]}'} if r['id'] else {})})
        rows.sort(key=lambda x: x.pop('_order'))
        return ([('type', 'Type', 'text'), ('group', 'Account group', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'), ('opening', 'Opening', 'number'),
                 ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number'), ('closing', 'Closing', 'number')], rows,
                {'filters': ['type', 'group'], 'tree': ['type', 'group'],
                 'notes': ['A tree: Type › Account group › account, with the count and subtotals on each level – open a group to see its accounts. Debit balances '
                           'positive, credit balances negative; the closing column totals zero when the books balance.']})

    def pl_tree(run):
        """v1.79.6 Profit and loss as a tree: Income / Expenses › account group › account"""
        p = S.profit_and_loss(run.date_from, run.date_to, branch=br(run))
        order = {g: i for i, (g, _) in enumerate(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)}
        rows = []
        for r in sorted(p['rows'], key=lambda r: (r['type'] != 'income', order.get(r['group'], 99), r['code'])):
            amt = f(r['amount'])
            rows.append({'part': 'Income' if r['type'] == 'income' else 'Expenses', 'group': group_label.get(r['group'], r['group']), 'code': r['code'],
                         'account': r['name'], 'amount': amt, 'effect': amt if r['type'] == 'income' else -amt, '_url': f'{page}r/account/{r["id"]}'})
        return ([('part', 'Part', 'text'), ('group', 'Account group', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'),
                 ('amount', 'Amount', 'number'), ('effect', 'Effect on profit', 'number')], rows,
                {'no_totals': ('amount',), 'tree': ['part', 'group'], 'filters': ['part', 'group'],
                 'notes': [f'A tree: Income / Expenses › account group › account with subtotals – open a group to see its accounts. Effect on profit: income +, '
                           f'expenses −; its total is the net profit ({f(p["net"]):,.2f}), as in Profit and loss. Gross profit {f(p["gross"]):,.2f}, '
                           f'operating profit {f(p["operating"]):,.2f}.'] + ([BRANCH_NOTE] if br(run) else [])})

    def bs_tree(run):
        """v1.79.6 Balance sheet as a tree: Assets / Liabilities / Equity › account group › account"""
        b = S.balance_sheet(run.date_to, branch=br(run))
        order = {g: i for i, (g, _) in enumerate(__import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS)}
        side = {'asset': (0, 'Assets'), 'liability': (1, 'Liabilities'), 'equity': (2, 'Equity')}
        rows = []
        for r in sorted(b['rows'], key=lambda r: (side[r['type']][0], order.get(r['group'], 99), r['code'] or 'zz')):
            rows.append({'side': side[r['type']][1], 'group': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'],
                         'amount': f(r['amount']), **({'_url': f'{page}r/account/{r["id"]}'} if r['id'] else {})})
        return ([('side', 'Side', 'text'), ('group', 'Account group', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'),
                 ('amount', f'{run.date_to:%d/%m/%Y}', 'number')], rows,
                {'no_totals': ('amount',), 'tree': ['side', 'group'], 'filters': ['side', 'group'],
                 'notes': [f'A tree at {run.date_to:%d/%m/%Y}: Assets / Liabilities / Equity › account group › account with subtotals – open a group to see its accounts. '
                           f'Assets {f(b["assets"]):,.2f}; liabilities {f(b["liabilities"]):,.2f}; equity {f(b["equity"]):,.2f}; '
                           f'assets − liabilities − equity {f(b["difference"]):,.2f} (0 when the books balance).'] + ([BRANCH_NOTE] if br(run) else [])})

    def tb_group_summary(run):
        groups = __import__('ZeoLedger.models', fromlist=['GROUPS']).GROUPS
        agg = {}
        for r in S.trial_balance(run.date_from, run.date_to, branch=br(run)):
            x = agg.setdefault(r['group'], {'type': r['type'].title(), 'group': group_label.get(r['group'], r['group']), 'accounts': 0,
                                            'opening': Decimal(0), 'debit': Decimal(0), 'credit': Decimal(0), 'closing': Decimal(0)})
            x['accounts'] += 1
            for k in ('opening', 'debit', 'credit', 'closing'):
                x[k] += r[k]
        rows = [{**agg[g], **{k: f(agg[g][k]) for k in ('opening', 'debit', 'credit', 'closing')}} for g, _ in groups if g in agg]
        return ([('type', 'Type', 'text'), ('group', 'Account group', 'text'), ('accounts', 'Accounts', 'number'), ('opening', 'Opening', 'number'),
                 ('debit', 'Debit', 'number'), ('credit', 'Credit', 'number'), ('closing', 'Closing', 'number')], rows,
                {'filters': ['type'], 'notes': ['One line per account group: the sum of its accounts in the trial balance. The closing column totals zero when the books balance.']})

    def bs_compare(run):
        d1 = run.date_to
        p1 = _year_back(d1)
        cur, prev = S.balance_sheet(d1, branch=br(run)), S.balance_sheet(p1, branch=br(run))
        accts = {}
        for which, b in (('current', cur), ('previous', prev)):
            for r in b['rows']:
                k = r['id'] or ('unclosed', r['name'])
                a = accts.setdefault(k, {'side': r['type'].title(), 'section': group_label.get(r['group'], r['group']), 'code': r['code'], 'account': r['name'],
                                         'current': 0.0, 'previous': 0.0, '_order': ({'asset': 0, 'liability': 1, 'equity': 2}[r['type']], r['code'] or 'zz')})
                if r['id']:
                    a['_url'] = f'{page}r/account/{r["id"]}'
                a[which] = f(r['amount'])
        rows = sorted(accts.values(), key=lambda a: a.pop('_order'))
        rows += [{'side': 'Total', 'section': '', 'code': '', 'account': k.title(), 'current': f(cur[k]), 'previous': f(prev[k])} for k in ('assets', 'liabilities', 'equity')]
        for r in rows:
            _change(r)
        return ([('side', 'Side', 'text'), ('section', 'Section', 'text'), ('code', 'Code', 'text'), ('account', 'Account', 'text'),
                 ('current', f'{d1:%d/%m/%Y}', 'number'), ('previous', f'{p1:%d/%m/%Y}', 'number'), ('change', 'Change', 'number'), ('change_pct', 'Change %', 'number')], rows,
                {'no_totals': ('current', 'previous', 'change', 'change_pct'), 'filters': ['side', 'section'],
                 'notes': [f'At {d1:%d/%m/%Y} and one year earlier. Assets − liabilities − equity: {cur["difference"]:,.2f} and {prev["difference"]:,.2f} (0 when the books balance).']
                 + ([BRANCH_NOTE] if br(run) else [])})

    def budget_monthly(run):
        d0, d1 = start_of(run), run.date_to
        branch = br(run)
        rows = []
        for b in Budget.objects.select_related('account', 'cost_centre', 'cost_centre__branch', 'year').prefetch_related('year__periods'):
            if not (b.year.start <= d1 and b.year.end >= d0) or not _budget_in_branch(b, branch):
                continue
            months = [Decimal(str(x or 0)) for x in (b.amounts or [])] + [Decimal(0)] * 12
            for i, p in enumerate(sorted(b.year.periods.all(), key=lambda x: x.number)):
                if p.end < d0 or p.start > d1:
                    continue
                plan = months[i]
                a = S.lines(p.start, p.end, cost_centre=b.cost_centre_id).filter(account=b.account).aggregate(d=Sum('debit'), c=Sum('credit'))
                act = Decimal((a['d'] or 0) - (a['c'] or 0))
                if b.account.type == 'income':
                    act = -act
                rows.append({'month': p.start.strftime('%Y-%m'), 'account': str(b.account), 'cost_centre': str(b.cost_centre or ''),
                             'branch': str(getattr(b.cost_centre, 'branch', '') or ''), 'budget': f(plan), 'actual': f(act), 'variance': f(act - plan),
                             'used': round(float(act) / float(plan) * 100, 1) if plan else None, '_url': f'{page}r/account/{b.account_id}'})
        rows.sort(key=lambda r: (r['account'], r['cost_centre'], r['month']))
        notes = ['One row per budget line and month: budget, actual from the posted books, variance and % used. Filter by month, account, cost centre or branch.']
        if branch:
            notes.append('A branch is chosen: the budgets of the cost centres of that branch (company-wide budgets without a cost centre are left out).')
        return ([('month', 'Month', 'text'), ('account', 'Account', 'text'), ('cost_centre', 'Cost centre', 'text'), ('branch', 'Branch', 'text'),
                 ('budget', 'Budget', 'number'), ('actual', 'Actual', 'number'), ('variance', 'Variance', 'number'), ('used', '% used', 'number')], rows,
                {'no_totals': ('used',), 'filters': ['month', 'account', 'cost_centre', 'branch'], 'notes': notes})

    def ratios(run):
        d0, d1 = start_of(run), run.date_to
        cur = _ratio_values(S, d0, d1, br(run))
        prev = _ratio_values(S, _year_back(d0), _year_back(d1), br(run))
        rows = [{'category': cat, 'ratio': name, 'current': cur[key], 'previous': prev[key], 'unit': unit, 'formula': formula}
                for cat, key, name, unit, formula in RATIOS]
        return ([('category', 'Group', 'text'), ('ratio', 'Ratio', 'text'), ('current', f'{d0:%d/%m/%Y} – {d1:%d/%m/%Y}', 'number'),
                 ('previous', 'Same period last year', 'number'), ('unit', 'Unit', 'text'), ('formula', 'How it is worked out', 'text')], rows,
                {'no_totals': ('current', 'previous'), 'filters': ['category'],
                 'notes': [f'Profit and loss figures for the period ({cur["days"]} days); balance sheet figures at the end of the period, averages of the start and the end '
                           'for returns, turnover and days. Blank when a figure needed is zero.'] + ([BRANCH_NOTE] if br(run) else [])})

    return {'gl-trial-balance': ('Trial balance', 'Accounting', 'Opening, debit, credit and closing balance of every account.', tb, 'month', codes),
            'gl-pl-monthly': ('Profit and loss by month', 'Accounting', 'Every income and expense account with a column for each month and the total.', pl_monthly, 'year', codes),
            'gl-pl-compare': ('Profit and loss – compared with last year', 'Accounting', 'The period against the same period last year: change and change %.', pl_compare, 'year', codes),
            'gl-pl-variance': ('Profit and loss – this year against last year by month, quarter and half year', 'Accounting',
                               'Each month, quarter, half year and the year: this year, last year, variance and variance % for every account and the profit lines.',
                               pl_variance, 'year', codes),
            'gl-pl-variance-cc': ('Profit and loss – this year against last year by cost centre', 'Accounting',
                                  'The month, quarter and half-year comparison with variance for each cost centre.', pl_variance_cc, 'year', codes),
            'gl-pl-variance-branch': ('Profit and loss – this year against last year by branch', 'Accounting',
                                      'The month, quarter and half-year comparison with variance for each branch.', pl_variance_branch, 'year', codes),
            'gl-pl-cost-centre': ('Profit and loss by cost centre', 'Accounting', 'Every income and expense account with a column for each cost centre and the total.',
                                  pl_by('cost_centre_id'), 'year', codes),
            'gl-pl-branch': ('Profit and loss by branch', 'Accounting', 'Every income and expense account with a column for each branch and the total.',
                             pl_by('branch_id'), 'year', codes),
            'gl-tb-group': ('Trial balance by account group', 'Accounting', 'A tree: type › account group › account, with opening, debit, credit and closing.', tb_group, 'month', codes),
            'gl-pl-tree': ('Profit and loss – account group tree', 'Accounting', 'Income and expenses as a tree: account group › account, with subtotals.', pl_tree, 'year', codes),
            'gl-bs-tree': ('Balance sheet – account group tree', 'Accounting', 'Assets, liabilities and equity as a tree: account group › account, with subtotals.',
                           bs_tree, 'month', codes),
            'gl-tb-group-summary': ('Trial balance – account group summary', 'Accounting', 'One line per account group: opening, debit, credit and closing.',
                                    tb_group_summary, 'month', codes),
            'gl-bs-compare': ('Balance sheet – compared with last year', 'Accounting', 'Assets, liabilities and equity at a date and one year earlier.', bs_compare, 'month', codes),
            'gl-budget-monthly': ('Budget against actual by month', 'Accounting', 'Budget, actual and variance per account, cost centre and month; by branch through the cost centres.',
                                  budget_monthly, 'year', codes),
            'gl-ratios': ('Financial ratios', 'Accounting', 'Liquidity, profitability, leverage and efficiency ratios against the same period last year.', ratios, 'year', codes),
            'gl-general-ledger': ('General ledger', 'Accounting', 'Every posting of every account with the running balance.', gl, 'month', codes),
            'gl-profit-loss': ('Profit and loss', 'Accounting', 'Revenue, cost of sales, gross profit, expenses, operating profit, profit before and after tax.', pl, 'year', codes),
            'gl-balance-sheet': ('Balance sheet', 'Accounting', 'Assets, liabilities and equity at a date (IFRS presentation).', bs, 'month', codes),
            'gl-cash-flow': ('Cash flow', 'Accounting', 'Operating, investing and financing cash flows (indirect method).', cf, 'year', codes),
            'gl-ar-ageing': ('Customer ageing', 'Accounting', 'What every customer owes by age, with payments on account.', age(True), 'month', codes),
            'gl-ap-ageing': ('Supplier ageing', 'Accounting', 'What we owe every supplier by age.', age(False), 'month', codes),
            'gl-vat201': ('VAT 201', 'Accounting', 'The VAT return boxes from the posted books: supplies by emirate, reverse charge, zero rated, exempt, imports, expenses.', vat, 'quarter', mgr),
            'gl-vat-audit': ('VAT audit file', 'Accounting', 'Every line in the VAT return with partner, TRN, box and emirate.', vat_audit, 'quarter', mgr),
            'gl-corporate-tax': ('Corporate tax computation', 'Accounting', 'Accounting profit, the adjustments of each account, taxable income and the tax.', ct, 'year', mgr),
            'gl-budget': ('Budget against actual', 'Accounting', 'Budget, actual and variance per account and cost centre.', budget, 'year', codes),
            'gl-bank-rec': ('Bank reconciliation', 'Accounting', 'Book balance against the last statement with the items not yet matched.', bank, 'month', mgr),
            'gl-journals': ('Journal register', 'Accounting', 'Every posted entry line by date and journal.', journals, 'month', codes),
            'gl-integrity': ('Books integrity check', 'Accounting', 'Debits = credits, the balance sheet balances, customers and suppliers tie to the control accounts.', integrity, 'month', mgr),
            # v1.79.3 customer / supplier ledger (every transaction) and statement of account (open items, post-dated cheques under them)
            'gl-customer-ledger': ('Customer ledger', 'Accounting', 'Every transaction of every customer with the running balance, in the company currency and the transaction currency.',
                                   party_ledger(True), 'month', codes),
            'gl-supplier-ledger': ('Supplier ledger', 'Accounting', 'Every transaction of every supplier with the running balance, in the company currency and the transaction currency.',
                                   party_ledger(False), 'month', codes),
            'gl-customer-statement': ('Customer statement', 'Accounting', 'Statement of account: the open invoices and credit notes of every customer, the post-dated cheques received under them and the net balance.',
                                      party_statement(True), 'month', codes),
            'gl-supplier-statement': ('Supplier statement', 'Accounting', 'Statement of account: the open bills and debit notes of every supplier, the post-dated cheques issued under them and the net balance.',
                                      party_statement(False), 'month', codes)}


# ---------------------------------------------------------------- v1.79.3 helpers of the period-wise, comparative and ratio reports
def _months(d0, d1, cap=24):
    """the calendar months of a period, clipped to it: [(start, end), …] – at most `cap` months"""
    from calendar import monthrange
    out, m = [], d0.replace(day=1)
    while m <= d1 and (cap is None or len(out) < cap):
        end = m.replace(day=monthrange(m.year, m.month)[1])
        out.append((max(m, d0), min(end, d1)))
        m = end + __import__('datetime').timedelta(days=1)
    return out


def _mkey(d):
    return f'm_{d:%Y_%m}'


def _year_back(d):
    """the same date one year earlier (29 February → 28 February)"""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _change(r):
    r['change'] = round((r.get('current') or 0) - (r.get('previous') or 0), 2)
    p = r.get('previous') or 0
    r['change_pct'] = round(r['change'] / abs(p) * 100, 1) if p else None


def _pl_split(S, d0, d1, field, **kw):
    """v1.79.5 Profit and loss split by a dimension of the posting lines (cost_centre_id / branch_id) in one query:
    {value or None: {'rows', 'revenue', 'gross', 'operating', 'before_tax', 'net', …}} – worked out as services.profit_and_loss"""
    out = {}
    qs = (S.lines(d0, d1, **kw).filter(account__type__in=('income', 'expense'))
          .values(field, 'account_id', 'account__code', 'account__name', 'account__group', 'account__type')
          .annotate(d=Sum('debit'), c=Sum('credit')).order_by('account__code'))
    for r in qs:
        p = out.setdefault(r[field], {'rows': [], 'sec': defaultdict(Decimal)})
        amt = S.r2((r['c'] or 0) - (r['d'] or 0)) if r['account__type'] == 'income' else S.r2((r['d'] or 0) - (r['c'] or 0))
        p['sec'][r['account__group']] += amt
        p['rows'].append({'id': r['account_id'], 'code': r['account__code'], 'name': r['account__name'], 'group': r['account__group'],
                          'type': r['account__type'], 'amount': amt})
    for p in out.values():
        sec = p.pop('sec')
        p['revenue'] = sec['revenue']
        p['gross'] = sec['revenue'] - sec['cost_of_sales']
        p['operating'] = p['gross'] + sec['other_income'] - sec['expense'] - sec['depreciation']
        p['before_tax'] = p['operating'] - sec['finance_cost'] - sec['other_expense']
        p['net'] = p['before_tax'] - sec['income_tax']
    return out


def _budget_in_branch(b, branch):
    """no branch chosen: every budget; a branch chosen: the budgets whose cost centre belongs to that branch"""
    if not branch:
        return True
    cc = b.cost_centre
    return bool(cc and cc.branch_id == branch)


RATIOS = (
    ('Liquidity', 'current_ratio', 'Current ratio', 'times', 'Current assets ÷ current liabilities'),
    ('Liquidity', 'quick_ratio', 'Quick ratio (acid test)', 'times', '(Current assets − inventory − prepayments) ÷ current liabilities'),
    ('Liquidity', 'cash_ratio', 'Cash ratio', 'times', '(Cash + bank) ÷ current liabilities'),
    ('Liquidity', 'working_capital', 'Working capital', 'AED', 'Current assets − current liabilities'),
    ('Profitability', 'gross_margin', 'Gross margin', '%', 'Gross profit ÷ revenue'),
    ('Profitability', 'ebitda_margin', 'EBITDA margin', '%', '(Operating profit + depreciation) ÷ revenue'),
    ('Profitability', 'operating_margin', 'Operating margin', '%', 'Operating profit ÷ revenue'),
    ('Profitability', 'net_margin', 'Net margin', '%', 'Net profit ÷ revenue'),
    ('Profitability', 'roa', 'Return on assets', '%', 'Net profit ÷ average total assets'),
    ('Profitability', 'roe', 'Return on equity', '%', 'Net profit ÷ average equity'),
    ('Leverage', 'debt_equity', 'Debt to equity', 'times', 'Total liabilities ÷ equity'),
    ('Leverage', 'debt_ratio', 'Debt ratio', '%', 'Total liabilities ÷ total assets'),
    ('Leverage', 'equity_ratio', 'Equity ratio', '%', 'Equity ÷ total assets'),
    ('Leverage', 'interest_cover', 'Interest cover', 'times', 'Operating profit ÷ finance cost'),
    ('Efficiency', 'asset_turnover', 'Asset turnover', 'times', 'Revenue ÷ average total assets'),
    ('Efficiency', 'receivable_days', 'Receivable days (DSO)', 'days', 'Average receivables ÷ revenue × days in the period'),
    ('Efficiency', 'inventory_days', 'Inventory days', 'days', 'Average inventory ÷ cost of sales × days in the period'),
    ('Efficiency', 'payable_days', 'Payable days (DPO)', 'days', 'Average payables ÷ cost of sales × days in the period'),
    ('Efficiency', 'cash_cycle', 'Cash conversion cycle', 'days', 'Receivable days + inventory days − payable days'),
)
BRANCH_NOTE = ('A branch is chosen: only entry lines posted to this branch count. Balance-sheet items posted without a branch (often bank, payables, '
               'inventory, loans) are left out, so some ratios can be blank or not comparable with the company figures.')
CURRENT_ASSETS = ('cash', 'bank', 'receivable', 'current_asset', 'inventory', 'prepayment')
CURRENT_LIABILITIES = ('payable', 'current_liability', 'tax')


def _bs_groups(b):
    g = defaultdict(Decimal)
    for r in b['rows']:
        g[r['group']] += r['amount']
    g['_assets'], g['_liabilities'], g['_equity'] = b['assets'], b['liabilities'], b['equity']
    return g


def _ratio_values(S, d0, d1, branch=None):
    from datetime import timedelta
    pl = S.profit_and_loss(d0, d1, branch=branch)
    dep = sum((r['amount'] for r in pl['rows'] if r['group'] == 'depreciation'), Decimal(0))
    close = _bs_groups(S.balance_sheet(d1, branch=branch))
    opening = _bs_groups(S.balance_sheet(d0 - timedelta(days=1), branch=branch))
    days = (d1 - d0).days + 1

    def q(a, b, mul=1):
        return round(float(a) / float(b) * mul, 2) if b else None

    def avg(k):
        return (close[k] + opening[k]) / 2

    ca = sum((close[k] for k in CURRENT_ASSETS), Decimal(0))
    cl = sum((close[k] for k in CURRENT_LIABILITIES), Decimal(0))
    rev, cos = pl['revenue'], pl['cost_of_sales']
    v = {'days': days,
         'current_ratio': q(ca, cl), 'quick_ratio': q(ca - close['inventory'] - close['prepayment'], cl), 'cash_ratio': q(close['cash'] + close['bank'], cl),
         'working_capital': round(float(ca - cl), 2),
         'gross_margin': q(pl['gross'], rev, 100), 'ebitda_margin': q(pl['operating'] + dep, rev, 100), 'operating_margin': q(pl['operating'], rev, 100),
         'net_margin': q(pl['net'], rev, 100), 'roa': q(pl['net'], avg('_assets'), 100), 'roe': q(pl['net'], avg('_equity'), 100),
         'debt_equity': q(close['_liabilities'], close['_equity']), 'debt_ratio': q(close['_liabilities'], close['_assets'], 100),
         'equity_ratio': q(close['_equity'], close['_assets'], 100), 'interest_cover': q(pl['operating'], pl['finance_cost']),
         'asset_turnover': q(rev, avg('_assets')), 'receivable_days': q(avg('receivable'), rev, days),
         'inventory_days': q(avg('inventory'), cos, days), 'payable_days': q(avg('payable'), cos, days)}
    parts = (v['receivable_days'], v['inventory_days'], v['payable_days'])
    v['cash_cycle'] = round((parts[0] or 0) + (parts[1] or 0) - (parts[2] or 0), 2) if parts[0] is not None and parts[2] is not None else None
    return v
