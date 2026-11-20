"""v1.79.4 Financial report designer – a statement laid out to the customer's requirement.

A design (FinReport) has
  lines   – from top to bottom: heading, accounts, formula, text, blank
            accounts: "4000-4999, 5100, 61*, group:revenue, type:expense, !4500, profit"
            formula:  "REV - COS", "(GP - OPEX) * 1", any line reference with + - * / ( )
            sign:     natural (income, liabilities and equity shown positive), debit, credit, negate
            style:    normal, bold, subtotal (ruled above), total (ruled above, double below)
            indent 0–3, detail (each account under the line, then the line as its total)
  columns – from left to right: actual, prior_year, prior_period, ytd, budget, variance (a − b), variance_pct, pct_line (% of a line)

Each active design is the report gl-fr-<id> of the report centre: drill down (a line → its postings, an account → the account),
Excel / CSV export, PDF on the letterhead with the logo."""
import ast
import operator
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

D0 = Decimal('0')
KEY_RE = re.compile(r'^gl-fr-(\d+)$')

LINE_TYPES = 'heading=Heading|accounts=Accounts|formula=Formula|text=Text|blank=Blank line'
SIGNS = 'natural=Natural|debit=Debit +|credit=Credit +|negate=Reversed'
STYLES = 'normal=Normal|bold=Bold|subtotal=Subtotal|total=Total'
COL_KINDS = ('actual=This period|prior_year=Same period last year|prior_period=Previous period|ytd=Year to date|budget=Budget|'
             'variance=Variance (column a − column b)|variance_pct=Variance % (a against b)|pct_line=% of a line (column a)')
LINE_COLS = [('ref', 'Ref', 'text'), ('label', 'Label', 'text'), ('label_ar', 'Label (Arabic)', 'text'), ('type', 'Type', 'choice', LINE_TYPES),
             ('accounts', 'Accounts', 'text'), ('formula', 'Formula', 'text'),
             ('sign', 'Sign', 'choice', SIGNS), ('style', 'Style', 'choice', STYLES), ('indent', 'Indent', 'int'), ('detail', 'Show accounts', 'bool')]
COLUMN_COLS = [('label', 'Heading (empty: automatic)', 'text'), ('kind', 'Column', 'choice', COL_KINDS), ('a', 'Column a', 'int'), ('b', 'Column b', 'int'),
               ('line', 'Line ref (for %)', 'text')]


# ------------------------------------------------------------------ templates a design can start from
def _l(ref, label, typ='accounts', accounts='', formula='', sign='natural', style='normal', indent=0, detail=False, label_ar=''):
    return {'ref': ref, 'label': label, 'label_ar': label_ar, 'type': typ, 'accounts': accounts, 'formula': formula, 'sign': sign, 'style': style,
            'indent': indent, 'detail': detail}


def _c(kind, label='', a=None, b=None, line=''):
    return {'label': label, 'kind': kind, 'a': a, 'b': b, 'line': line}


TEMPLATES = {
    'mgmt_pl': ('Management P&L – actual, last year, budget', 'period', [
        _l('REV', 'Sales', accounts='group:revenue', detail=True, label_ar='المبيعات'),
        _l('COS', 'Cost of sales', accounts='group:cost_of_sales', indent=1, label_ar='تكلفة المبيعات'),
        _l('GP', 'Gross profit', 'formula', formula='REV - COS', style='subtotal', label_ar='إجمالي الربح'),
        _l('GPM', 'Gross margin %', 'formula', formula='GP / REV * 100', indent=1, label_ar='هامش الربح الإجمالي %'),
        _l('', '', 'blank'),
        _l('OPEX', 'Operating expenses', accounts='group:expense, group:depreciation', detail=True, label_ar='المصاريف التشغيلية'),
        _l('OI', 'Other income', accounts='group:other_income', indent=1, label_ar='إيرادات أخرى'),
        _l('EBIT', 'Operating profit', 'formula', formula='GP - OPEX + OI', style='subtotal', label_ar='الربح التشغيلي'),
        _l('FIN', 'Finance and other costs', accounts='group:finance_cost, group:other_expense', indent=1, label_ar='تكاليف التمويل وأخرى'),
        _l('TAX', 'Income tax', accounts='group:income_tax', indent=1, label_ar='ضريبة الدخل'),
        _l('NP', 'Net profit', 'formula', formula='EBIT - FIN - TAX', style='total', label_ar='صافي الربح'),
    ], [_c('actual'), _c('prior_year'), _c('variance', a=1, b=2), _c('budget'), _c('variance', 'Against budget', a=1, b=4), _c('pct_line', '% of sales', a=1, line='REV')]),
    'board_bs': ('Summarised balance sheet – this year and last year', 'position', [
        _l('', 'Assets', 'heading', style='bold', label_ar='الأصول'),
        _l('NCA', 'Non-current assets', accounts='group:fixed_asset, group:accum_depr, group:non_current_asset', indent=1, label_ar='الأصول غير المتداولة'),
        _l('CASH', 'Cash and bank', accounts='group:cash, group:bank', indent=1, label_ar='النقد والبنوك'),
        _l('REC', 'Trade receivables', accounts='group:receivable', indent=1, label_ar='الذمم المدينة'),
        _l('OCA', 'Inventory and other current assets', accounts='group:inventory, group:prepayment, group:current_asset', indent=1, label_ar='المخزون وأصول متداولة أخرى'),
        _l('TA', 'Total assets', 'formula', formula='NCA + CASH + REC + OCA', style='total', label_ar='إجمالي الأصول'),
        _l('', '', 'blank'),
        _l('', 'Equity and liabilities', 'heading', style='bold', label_ar='حقوق الملكية والالتزامات'),
        _l('CAP', 'Share capital and reserves', accounts='group:equity', indent=1, label_ar='رأس المال والاحتياطيات'),
        _l('RE', 'Retained earnings and profit to date', accounts='group:retained, profit', indent=1, label_ar='الأرباح المحتجزة'),
        _l('EQ', 'Total equity', 'formula', formula='CAP + RE', style='subtotal', label_ar='إجمالي حقوق الملكية'),
        _l('PAY', 'Trade payables', accounts='group:payable', indent=1, label_ar='الذمم الدائنة'),
        _l('OCL', 'VAT, tax and other current liabilities', accounts='group:tax, group:current_liability', indent=1, label_ar='الضرائب والتزامات متداولة أخرى'),
        _l('NCL', 'Non-current liabilities', accounts='group:non_current_liability', indent=1, label_ar='التزامات غير متداولة'),
        _l('TL', 'Total liabilities', 'formula', formula='PAY + OCL + NCL', style='subtotal', label_ar='إجمالي الالتزامات'),
        _l('TEL', 'Total equity and liabilities', 'formula', formula='EQ + TL', style='total', label_ar='إجمالي حقوق الملكية والالتزامات'),
        _l('', '', 'blank'),
        _l('WC', 'Working capital (current assets − current liabilities)', 'formula', formula='CASH + REC + OCA - PAY - OCL', style='bold', label_ar='رأس المال العامل'),
        _l('CR', 'Current ratio', 'formula', formula='(CASH + REC + OCA) / (PAY + OCL)', indent=1, label_ar='نسبة التداول'),
    ], [_c('actual'), _c('prior_year'), _c('variance', 'Change', a=1, b=2), _c('variance_pct', 'Change %', a=1, b=2)]),
    'expense_ytd': ('Expense analysis – month and year to date', 'period', [
        _l('EXP', 'Operating expenses', accounts='type:expense, !group:cost_of_sales, !group:income_tax', detail=True),
        _l('TOT', 'Total expenses', 'formula', formula='EXP', style='total'),
    ], [_c('actual', 'This month'), _c('prior_period', 'Previous month'), _c('ytd'), _c('budget')]),
}


def template_choices():
    return '|'.join(f'{k}={v[0]}' for k, v in TEMPLATES.items())


# ------------------------------------------------------------------ accounts of a line
def _accounts():
    from .models import GLAccount
    return list(GLAccount.objects.filter(is_heading=False).values('id', 'code', 'name', 'name_ar', 'group', 'type'))


def _match(tok, a):
    tok = tok.strip()
    if not tok:
        return False
    if tok.startswith('group:'):
        return a['group'] == tok[6:].strip()
    if tok.startswith('type:'):
        return a['type'] == tok[5:].strip()
    if tok.endswith('*'):
        return a['code'].startswith(tok[:-1])
    m = re.match(r'^(\S+)\s*-\s*(\S+)$', tok)
    if m:
        return m.group(1) <= a['code'] <= m.group(2) and len(a['code']) >= min(len(m.group(1)), len(m.group(2)))
    return a['code'] == tok


def select(spec, accounts):
    """(account dicts, profit wanted) for a selector such as "4000-4999, 61*, group:revenue, !4500, profit" """
    toks = [t.strip() for t in str(spec or '').split(',') if t.strip()]
    inc = [t for t in toks if not t.startswith('!') and t.lower() != 'profit']
    exc = [t[1:] for t in toks if t.startswith('!')]
    out = [a for a in accounts if any(_match(t, a) for t in inc) and not any(_match(t, a) for t in exc)]
    return out, any(t.lower() == 'profit' for t in toks)


def check(lines, columns):
    """the problems of a design (shown when it is saved): unknown references, formula errors, columns out of range"""
    errs, refs = [], set()
    for i, ln in enumerate(lines or [], 1):
        r = str(ln.get('ref') or '').strip().upper()
        if r:
            if not re.match(r'^[A-Z][A-Z0-9_]*$', r):
                errs.append(f'Line {i}: the ref "{r}" – letters, digits and _ only, starting with a letter.')
            if r in refs:
                errs.append(f'Line {i}: the ref "{r}" is used twice.')
            refs.add(r)
        t = ln.get('type') or 'accounts'
        if t == 'formula':
            try:
                names = _names(ln.get('formula') or '')
            except Exception:   # noqa: BLE001
                errs.append(f'Line {i}: the formula "{ln.get("formula")}" cannot be read.')
                continue
            for n in names - refs:
                errs.append(f'Line {i}: the formula uses "{n}" – no line above has that ref.')
        elif t == 'accounts' and not str(ln.get('accounts') or '').strip():
            errs.append(f'Line {i}: which accounts? (e.g. 4000-4999 or group:revenue)')
    n = len(columns or [])
    for i, c in enumerate(columns or [], 1):
        k = c.get('kind') or 'actual'
        if k in ('variance', 'variance_pct', 'pct_line'):
            for x in ('a', 'b') if k != 'pct_line' else ('a',):
                v = c.get(x)
                if not isinstance(v, int) or not 1 <= v <= n or v == i:
                    errs.append(f'Column {i}: column {x} must be the number of another column (1–{n}).')
        if k == 'pct_line' and str(c.get('line') or '').strip().upper() not in refs:
            errs.append(f'Column {i}: the line ref "{c.get("line") or ""}" for the % is not a line of the report.')
    return errs


# ------------------------------------------------------------------ formulas (no eval: + - * / ( ) numbers and refs only)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _names(expr):
    tree = ast.parse(str(expr).upper(), mode='eval')
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif not isinstance(n, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load, ast.USub, ast.UAdd) + tuple(_OPS)):
            raise ValueError('not allowed')
    return out


def _calc(expr, vals):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return Decimal(str(n.value))
        if isinstance(n, ast.Name):
            v = vals.get(n.id)
            return D0 if v is None else v
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return None if v is None else (-v if isinstance(n.op, ast.USub) else v)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if a is None or b is None:
                return None
            if isinstance(n.op, ast.Div):
                return None if not b else a / b
            return _OPS[type(n.op)](a, b)
        raise ValueError('not allowed')
    try:
        return ev(ast.parse(str(expr).upper(), mode='eval'))
    except Exception:   # noqa: BLE001
        return None


# ------------------------------------------------------------------ figures
def _shift_year(d, n=1):
    try:
        return d.replace(year=d.year - n)
    except ValueError:   # 29 February
        return d.replace(year=d.year - n, day=28)


def windows(kind, basis, f, t):
    """(date from, date to) of an amount column; position: from is None (the balance at the date)"""
    from . import services as S
    if kind == 'prior_year':
        f, t = _shift_year(f), _shift_year(t)
    elif kind == 'prior_period':
        if basis == 'position':
            return None, f - timedelta(days=1)
        days = (t - f).days + 1
        t = f - timedelta(days=1)
        f = t - timedelta(days=days - 1)
        if f.day != 1 and (t + timedelta(days=1)).day == 1:   # whole months: the same number of months before
            months = round(days / 30.44)
            y, m = t.year, t.month - months + 1
            while m < 1:
                m += 12
                y -= 1
            f = date(y, m, 1)
    elif kind == 'ytd':
        y = S.fiscal_year_of(t, make=False)
        f = y.start if y else date(t.year, 1, 1)
    if basis == 'position':
        return None, t
    return f, t


def balances(f, t, branch=None):
    """{account id: debit − credit} in the window (f None: everything up to t)"""
    from . import services as S
    qs = S.lines(f, t, branch=branch).values('account').annotate(d=Sum('debit'), c=Sum('credit'))
    return {r['account']: (r['d'] or D0) - (r['c'] or D0) for r in qs}


def budgets(f, t):
    """{account id: budget (natural sign: income and expense positive)} for the whole months in the window"""
    from .models import Budget
    out = defaultdict(lambda: D0)
    for b in Budget.objects.select_related('year'):
        if not (b.year.start <= t and b.year.end >= f):
            continue
        months = [Decimal(str(x or 0)) for x in (b.amounts or [])] + [D0] * 12
        for i, p in enumerate(b.year.periods.order_by('number')):
            if p.start >= f and p.end <= t:
                out[b.account_id] += months[i]
    return out


NATURAL_CREDIT = ('income', 'liability', 'equity')


def _signed(raw, typ, sign):
    """raw debit − credit shown with the line's sign"""
    if sign == 'debit':
        return raw
    if sign == 'credit':
        return -raw
    if sign == 'negate':
        return raw if typ in NATURAL_CREDIT else -raw   # the natural sign turned round
    return -raw if typ in NATURAL_CREDIT else raw


def _budget_signed(nat, typ, sign):
    """a budget is kept in the natural sign"""
    if sign in ('natural', '', None):
        return nat
    if sign == 'negate':
        return -nat
    raw = -nat if typ in NATURAL_CREDIT else nat
    return raw if sign == 'debit' else -raw


def heading(c, i, basis, f, t, cols):
    if c.get('label'):
        return c['label']
    k = c.get('kind') or 'actual'
    fmt = lambda d: d.strftime('%d/%m/%Y')   # noqa: E731
    if k in ('actual', 'prior_year', 'prior_period', 'ytd'):
        a, b = windows(k, basis, f, t)
        base = {'actual': 'Actual' if basis == 'period' else 'As at', 'prior_year': 'Last year', 'prior_period': 'Previous period', 'ytd': 'Year to date'}[k]
        if basis == 'position':
            return f'{base} {fmt(b)}'
        return f'{base} {a:%d/%m}–{fmt(b)}' if a.year == b.year else f'{base} {fmt(a)}–{fmt(b)}'
    if k == 'budget':
        return 'Budget'
    if k == 'variance':
        return f'Variance ({c.get("a")}−{c.get("b")})'
    if k == 'variance_pct':
        return f'Variance % ({c.get("a")}/{c.get("b")})'
    if k == 'pct_line':
        return f'% of {c.get("line") or ""}'
    return f'Column {i}'


def compute(fr, f, t, branch=None, lang='en', page='', with_rows=True):
    """(cols, rows, extra) of a design for the report centre"""
    basis = fr.basis or 'period'
    lines = [dict(x) for x in (fr.lines or []) if isinstance(x, dict)]
    cols_def = [dict(x) for x in (fr.columns or []) if isinstance(x, dict)] or [{'kind': 'actual'}]
    accts = _accounts()
    scale = Decimal(fr.scale or '1')
    # the figures every amount column needs (each window once)
    raw = {}
    for c in cols_def:
        k = c.get('kind') or 'actual'
        if k in ('actual', 'prior_year', 'prior_period', 'ytd') and k not in raw:
            a, b = windows(k, basis, f, t)
            raw[k] = balances(a, b, branch)
        if k == 'budget' and 'budget' not in raw:
            raw['budget'] = budgets(f, t) if basis == 'period' else {}
    # position: the profit to the date – every income and expense posting up to it (earlier years not yet closed too)
    typ = {a['id']: a['type'] for a in accts}
    for k, m in raw.items():
        if k != 'budget' and basis == 'position':
            m['profit'] = sum((v for i, v in m.items() if i != 'profit' and typ.get(i) in ('income', 'expense')), D0)
    ncol = len(cols_def)
    vals = {}            # ref → [amount per column]
    out_rows = []        # (label, code, [amounts], style, indent, url, kind)
    gl_url = '/main-sidebar/report-options/r/gl-general-ledger'

    def amounts_of(sel, profit, sign):
        res = []
        for c in cols_def:
            k = c.get('kind') or 'actual'
            if k in raw and k != 'budget':
                m = raw[k]
                v = sum((_signed(m.get(a['id'], D0), a['type'], sign) for a in sel), D0)
                if profit:
                    v += _signed(m.get('profit', D0), 'equity', sign)
                res.append(v)
            elif k == 'budget':
                m = raw.get('budget') or {}
                res.append(sum((_budget_signed(m.get(a['id'], D0), a['type'], sign) for a in sel), D0) if basis == 'period' else None)
            else:
                res.append(None)
        return res

    def derived(res, refvals=None):
        """variance, variance %, % of a line – from the amount columns"""
        for i, c in enumerate(cols_def):
            k = c.get('kind') or 'actual'
            a = (c.get('a') or 0) - 1
            b = (c.get('b') or 0) - 1
            av = res[a] if 0 <= a < ncol else None
            bv = res[b] if 0 <= b < ncol else None
            if k == 'variance':
                res[i] = None if av is None or bv is None else av - bv
            elif k == 'variance_pct':
                res[i] = None if av is None or not bv else (av - bv) / abs(bv) * 100
            elif k == 'pct_line':
                base = (vals.get(str(c.get('line') or '').strip().upper()) or [None] * ncol)
                bvv = base[a] if 0 <= a < ncol else None
                res[i] = None if av is None or not bvv else av / bvv * 100
        return res

    pct_cols = {i for i, c in enumerate(cols_def) if (c.get('kind') in ('variance_pct', 'pct_line'))}
    for ln in lines:
        t_ = ln.get('type') or 'accounts'
        ref = str(ln.get('ref') or '').strip().upper()
        label = (ln.get('label_ar') if lang == 'ar' and ln.get('label_ar') else ln.get('label')) or ''
        if lang == 'both' and ln.get('label_ar'):
            label = f'{ln.get("label") or ""} / {ln["label_ar"]}'
        style = ln.get('style') or 'normal'
        ind = max(0, min(3, int(ln.get('indent') or 0)))
        sign = ln.get('sign') or 'natural'
        if t_ == 'blank':
            out_rows.append(('\u00a0', '', [None] * ncol, 'blank', 0, '', t_))
            continue
        if t_ in ('heading', 'text'):
            out_rows.append((label, '', [None] * ncol, 'bold' if t_ == 'heading' and style == 'normal' else style, ind, '', t_))
            continue
        if t_ == 'formula':
            res = []
            for i in range(ncol):
                if i in pct_cols:
                    res.append(None)
                    continue
                res.append(_calc(ln.get('formula') or '', {k: (v[i] if v else None) for k, v in vals.items()}))
            # a ratio or % line (no amount column of its own): variance of the ratios is still meaningful; % of a line is not
            res = derived(res)
            if '/' in str(ln.get('formula') or ''):   # a ratio or a margin: a % of another line means nothing
                res = [None if cols_def[i].get('kind') == 'pct_line' else v for i, v in enumerate(res)]
            if ref:
                vals[ref] = res
            out_rows.append((label, '', res, style, ind, '', t_))
            continue
        sel, profit = select(ln.get('accounts'), accts)
        res = amounts_of(sel, profit, sign)
        if ref:
            vals[ref] = res          # a % of this very line (sales as 100 %) needs its amounts first
        res = derived(res)
        ids = ','.join(str(a['id']) for a in sel)
        url = f'{gl_url}?from={f.isoformat()}&to={t.isoformat()}&o_accounts={ids}' if ids else ''   # o_: the report page sends it on
        if ln.get('detail') and with_rows:
            out_rows.append((label, '', [None] * ncol, 'bold', ind, '', 'heading'))
            for a in sorted(sel, key=lambda x: x['code']):
                r = derived(amounts_of([a], False, sign))
                if fr.hide_zero and not any(x for x in r if x is not None):
                    continue
                nm = (a['name_ar'] if lang == 'ar' and a.get('name_ar') else a['name'])
                out_rows.append((nm, a['code'], r, 'normal', ind + 1, f'{page}r/account/{a["id"]}' if page else '', 'account'))
            if profit:
                r = derived(amounts_of([], True, sign))
                if any(x for x in r if x is not None):
                    out_rows.append(('Profit to date (not yet closed)', '', r, 'normal', ind + 1, '', 'account'))
            out_rows.append((f'Total {label}' if lang != 'ar' else f'إجمالي {label}', '', res, 'subtotal' if style == 'normal' else style, ind, url, t_))
        else:
            if fr.hide_zero and not any(x for x in res if x is not None) and style == 'normal':
                continue
            out_rows.append((label, '', res, style, ind, url, t_))

    cols = [('line', fr.name_ar if lang == 'ar' and fr.name_ar else 'Particulars', 'text')]
    if fr.show_codes:
        cols.append(('code', 'Code', 'text'))
    for i, c in enumerate(cols_def):
        cols.append((f'c_{i + 1}', heading(c, i + 1, basis, f, t, cols_def), 'number'))
    rows = []
    for label, code, res, style, ind, url, kind in out_rows:
        row = {'line': (' ' * ind) + label, 'code': code, '_i': ind, '_s': style, '_k': kind}
        if not fr.show_codes:
            row.pop('code')
        for i, v in enumerate(res):
            if v is None:
                row[f'c_{i + 1}'] = None
            elif i in pct_cols or (kind == 'formula' and _is_ratio(lines, label)):
                row[f'c_{i + 1}'] = round(float(v), 1) if i in pct_cols else round(float(v), 2)
            else:
                row[f'c_{i + 1}'] = round(float(v / scale), 0 if scale > 1 else 2)
        if url:
            row['_url'] = url
        if style in ('bold', 'subtotal', 'total') or kind == 'heading':
            row['_b'] = True
        if style in ('subtotal', 'total'):
            row['_t'] = style
        rows.append(row)
    notes = []
    if scale > 1:
        notes.append(f'Amounts in {dict((("1000", "thousands"), ("1000000", "millions"))).get(fr.scale)} ({_cur()}).')
    if fr.notes:
        notes.append(fr.notes)
    notes.append('Click a line to see its postings, an account to open it.')
    return cols, rows, {'no_totals': tuple(c[0] for c in cols if c[2] == 'number'), 'notes': notes, 'fin_report': fr.pk, 'subtitle': fr.subtitle, 'basis': fr.basis}


def _is_ratio(lines, label):
    """a formula line that divides (margin %, current ratio) is not scaled to thousands"""
    for ln in lines:
        if (ln.get('label') == label or ln.get('label_ar') == label) and '/' in str(ln.get('formula') or ''):
            return True
    return False


def _cur():
    try:
        from . import services as S
        return S.base_currency()
    except Exception:   # noqa: BLE001
        return ''


# ------------------------------------------------------------------ the report centre (DashboardManagement.reports)
LANG = {'key': 'lang', 'label': 'Labels', 'choices': [{'value': 'en', 'label': 'English'}, {'value': 'ar', 'label': 'العربية'}, {'value': 'both', 'label': 'Both'}]}


def _codes():
    return ['gl_manager', 'gl_accountant', 'gl_ar', 'gl_ap', 'gl_viewer']


def _app_codes():
    try:
        from zeo.appkit.engine import REGISTRY
        import importlib
        importlib.import_module('ZeoLedger.app')
        a = REGISTRY.get('ZeoLedger')
        return [a.code(r) for r in _codes()]
    except Exception:   # noqa: BLE001
        return _codes()


def _live():
    from django.db import connection
    return getattr(connection, 'schema_name', 'public') != 'public'


def spec(key):
    m = KEY_RE.match(str(key or ''))
    if not m or not _live():
        return None
    try:
        from .models import FinReport
        fr = FinReport.objects.filter(pk=int(m.group(1)), is_active=True).first()
    except Exception:   # noqa: BLE001
        return None
    if fr is None:
        return None

    def fn(run):
        q = getattr(getattr(run, 'request', None), 'query_params', {}) or {}
        lang = q.get('lang') if q.get('lang') in ('en', 'ar', 'both') else 'en'
        b = getattr(run, 'branches', None)
        from ZeoApps.reports import SLUG_PAGE
        cols, rows, extra = compute(fr, run.date_from, run.date_to, branch=b[0] if b and len(b) == 1 else None, lang=lang, page=SLUG_PAGE['accounting'])
        if any((ln or {}).get('label_ar') for ln in (fr.lines or [])):
            extra['options'] = [dict(LANG, value=lang)]
        return cols, rows, extra
    desc = fr.subtitle or ('Designed statement – ' + ('movement in the period' if fr.basis == 'period' else 'balances at the date'))
    return (fr.name, fr.section or 'Designed statements', desc, fn, 'year' if fr.basis == 'period' else 'month', _app_codes())


def listing(request, c):
    from .models import FinReport
    codes = _app_codes()
    if not (c.admin or any(x in c.codes for x in codes)):
        return []
    return [{'key': f'gl-fr-{fr.pk}', 'title': fr.name, 'section': fr.section or 'Designed statements',
             'description': fr.subtitle or 'Designed statement', 'period': 'year' if fr.basis == 'period' else 'month', 'perms': codes, 'custom': True}
            for fr in FinReport.objects.filter(is_active=True)]
