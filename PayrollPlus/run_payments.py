# v1.62.11 (item 227, scope addition 03/11/2026): new module – salary payments of a payroll run by payment method
"""v1.62.11 (item 227) – Payroll run › Payments: the run's net pay split by payment method, the file / list of each method and the
paid marks.

Method of an employee: a company method the employee is assigned to (Payments › Methods), else the payment mode of the employee's
primary bank account (EmployeeProfile.BankExtra.payment_mode: wps / bank_transfer / cash / cheque), else WPS (the model default).
Held salaries (PayrollPlus.payctl.held_amount, item 228) are taken off; a fully held salary is listed as held, not paid.

Paying account: the bank file set-ups (bankfiles.profiles) of that method – WPS set-ups (CBUAE SIF or the bank's / exchange house's
own WPS layout) and bank transfer set-ups (the paying company bank account and its bulk transfer layout) – by branch, else the
company default set-up of the method.

Files:  WPS            bankfiles.make (one file per paying set-up; the existing WPS download is not touched)
        bank transfer  ZEO generic bulk transfer CSV (IBAN, name, amount, reference) – no bank's own layout is verified yet
        cheque         cheque list (payee, amount in figures and in words in English and Arabic, cheque numbers from the range HR
                       enters) – PDF and Excel; no cheque printing layout (Adept Cheque Print is a separate product)
        cash           cash payment sheet per branch (employee, amount, signature, optional note / coin count) – PDF and Excel
        other          a simple Excel list per company method
Paid marks (date, reference) per line are kept in ZeoApps.CompanyOption 'run_pay:<run id>' – no new table.

1.63.0 item 27 (one salary split over two methods, e.g. part bank, part cash): `portions()` returns the parts of a payslip – the
main method and, with the employee's split (PayrollPlus/paysplit.py), the second method; held salary comes off the main part first.
"""
import csv
import datetime
import io
import re
from decimal import Decimal

from django.apps import apps
from django.db import transaction

from Translations.i18n import tr

from . import bankfiles as B

M = apps.get_model
ZERO = Decimal('0.00')
BUILTIN = [('wps', 'WPS (salary transfer)'), ('bank_transfer', 'Bank transfer (non-WPS)'), ('cheque', 'Cheque'), ('cash', 'Cash')]
# AED banknotes 1000 … 5 and coins 1, 0.50, 0.25 (CBUAE notes in circulation; coins 1, 5, 10 fils exist but are rarely used) –
# sources: en.wikipedia.org/wiki/United_Arab_Emirates_dirham, propertyfinder.ae/blog/uae-oins (read 03/11/2026)
DENOMS = [Decimal(x) for x in ('1000', '500', '200', '100', '50', '20', '10', '5', '1', '0.50', '0.25')]

# bulk transfer (non-WPS) layouts: the ZEO generic CSV is the base; a bank's own layout only when checked against its published
# spec or sample – none is verified yet (CBD publishes its non-WPS field names but the template itself is only an image)
TRANSFER_COLUMNS = ['Employee code', 'Beneficiary name', 'IBAN / account', 'Bank', 'Routing code', 'Amount', 'Currency', 'Payment reference', 'Value date']
TRANSFER_LAYOUTS = {
    'generic_csv': {
        'name': 'ZEO bulk transfer CSV (generic)', 'state': 'to_confirm', 'format': 'CSV (UTF-8), comma separated, heading row',
        'fields': TRANSFER_COLUMNS, 'file_name': 'TRANSFER_<account>_<MMYYYY>.csv',
        'summary': 'One heading row and one row per employee: IBAN, name, amount, reference. Most banks’ bulk payment upload takes these '
                   'fields, but each bank has its own template – send us the bank’s template to have it verified.',
        'sources': [], 'open': ['No bank’s own bulk transfer layout has been checked yet.'],
    },
}
TRANSFER_NOTES = {
    'cbd': ('CBD publishes the fields of its non-WPS salary file (Company, Pay Date, Remarks, Contact, Emp ID, Name, Bank Acct, Amount; '
            'CBD accounts only) – the template itself is only an image: to be confirmed with the template.',
            B.src('cbd_nonwps')),
}
TRANSFER_TBC = 'Bulk transfer layout to be confirmed – send us the bank’s template. Until then the file is the ZEO generic CSV.'


def _opt(key, lock=False):
    return B._opt(key, lock)


# ------------------------------------------------------------------ company methods ("other")
def methods():
    rows = list((_opt('payment_methods').value or {}).get('methods') or [])
    return rows


def save_methods(fn):
    with transaction.atomic():
        o = _opt('payment_methods', lock=True)
        v = dict(o.value or {})
        rows, res = fn(list(v.get('methods') or []), v)
        v['methods'] = rows
        o.value = v
        o.save()
    return res


def all_methods():
    out = [{'code': c, 'name': n, 'kind': c, 'builtin': True} for c, n in BUILTIN]
    out += [{'code': m['code'], 'name': m['name'], 'kind': 'other', 'builtin': False, 'employees': m.get('employees') or [], 'active': m.get('active', True)}
            for m in methods()]
    return out


# ------------------------------------------------------------------ paid marks
def store(run_id):
    return dict(_opt(f'run_pay:{run_id}').value or {})


def save_store(run_id, fn):
    with transaction.atomic():
        o = _opt(f'run_pay:{run_id}', lock=True)
        v = dict(o.value or {})
        res = fn(v)
        o.value = v
        o.save()
    return res


# ------------------------------------------------------------------ the split
def portions(ps, method, payable, net=None, held=None, split=False):
    """v1.63.0 item 27 hook: the parts of one payslip as [(method, amount, extra)].
    v1.63.0 (item 27): with the employee's salary split (PayrollPlus.paysplit) two parts – the main method and the second method
    (second bank account / cash / cheque / own method); held salary comes off the main part first. extra: held, kind, iban …"""
    from . import paysplit as PS
    if split is False:
        split = PS.split_on(ps.employee_id, PS.payslip_day(ps))
    if net is None:
        net, held = payable, ZERO
    out = []
    for p in PS.parts(net, held or ZERO, split, method):
        if p['kind'] == 'second' and p['gross'] <= 0:
            continue
        out.append((p['method'], p['amount'], p))
    return out


def _ename(e):
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code


def lines(run, branches=None):
    """every payslip of the run as payment lines: method, amount (net − held), account, branch, paid mark"""
    from .payctl import held_map
    slips = run.payslips.select_related('employee', 'employee__emp_branch_id').order_by('employee__emp_code')
    if branches is not None:
        slips = slips.filter(employee__emp_branch_id__in=branches)
    slips = list(slips)
    BD = M('EmpManagement', 'EmployeeBankDetail')
    BX = M('EmployeeProfile', 'BankExtra')
    emp_ids = [p.employee_id for p in slips]
    bds = {}
    for b in BD.objects.filter(employee_id__in=emp_ids, is_active=True).order_by('pk'):
        bds.setdefault(b.employee_id, []).append(b)
    bx = {x.bank_detail_id: x for x in BX.objects.filter(employee_id__in=emp_ids)}
    assign = {}
    for m in methods():
        if m.get('active', True):
            for e in m.get('employees') or []:
                assign[int(e)] = m['code']
    held = held_map([p.pk for p in slips])
    from . import paysplit as _PS   # v1.63.0 (item 27): the splits of the run's month
    _splits = _PS.splits_on(emp_ids, _PS.month_end(run.year, run.month))
    pending = set(M('PayrollManagement', 'PayslipApproval').objects.filter(request__payroll_run=run, status='Pending')
                  .values_list('request__employee_id', flat=True)) if _has_approval() else set()
    marks = store(run.pk).get('paid') or {}
    cheques = store(run.pk).get('cheques') or {}
    out = []
    for ps in slips:
        e = ps.employee
        if (ps.status or '').lower() == 'rejected':
            continue
        acc = bds.get(e.pk) or []
        prim = next((b for b in acc if getattr(bx.get(b.pk), 'is_primary', False)), acc[0] if acc else None)
        x = bx.get(prim.pk) if prim else None
        method = assign.get(e.pk) or (getattr(x, 'payment_mode', '') or 'wps')
        net = max(Decimal(str(ps.net_salary or 0)), ZERO).quantize(Decimal('0.01'))
        h = min(held.get(ps.pk, ZERO), net)
        payable = net - h
        state = 'ready'
        if ps.approvals.filter(status='Pending').exists() if not pending else e.pk in pending:
            state = 'not_ready'
        if payable <= 0 and h > 0:
            state = 'held'
        _parts = portions(ps, method, payable, net=net, held=h, split=_splits.get(e.pk))   # v1.63.0 (item 27)
        for meth, amt, px in _parts:
            mk = marks.get(f'{ps.pk}:{meth}') or (marks.get(str(ps.pk)) if len(_parts) == 1 else None) or None
            sec = px.get('kind') == 'second'   # v1.63.0 (item 27): the second part is paid to the split's own account
            pstate = state
            if pstate == 'held' and amt > 0:
                pstate = 'ready'
            elif pstate == 'ready' and amt <= 0 and px.get('held', ZERO) > 0:
                pstate = 'held'
            out.append({'payslip': ps.pk, 'employee_id': e.pk, 'emp_code': e.emp_code, 'name': _ename(e), 'method': meth,
                        'branch_id': e.emp_branch_id_id, 'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
                        'net': f'{net:.2f}', 'held': f"{px.get('held', h):.2f}", 'amount': f'{amt:.2f}', 'state': pstate,
                        'iban': (px.get('iban') or '') if sec else ((prim.iban_number or prim.account_number or '') if prim else ''),
                        'bank': (px.get('bank') or '') if sec else ((prim.bank_name or '') if prim else ''),
                        'routing': (px.get('routing') or '') if sec else ((prim.route_code or '') if prim else ''),
                        'agent': '' if sec else (getattr(x, 'wps_agent', '') if x else ''),
                        'split': px.get('kind') if len(_parts) > 1 else '', 'part_of': f'{px.get("gross", amt):.2f}',
                        'paid': bool(mk), 'paid_on': (mk or {}).get('on'), 'paid_ref': (mk or {}).get('ref'), 'cheque_no': cheques.get(str(ps.pk), '')})
    return out


def _has_approval():
    try:
        M('PayrollManagement', 'PayslipApproval')._meta.get_field('request')
        return True
    except Exception:
        return False


def account_for(method, branch_id):
    """the paying set-up of a method for a branch: the branch's own, else the method's company default (None = no set-up)"""
    rows = [p for p in B.profiles() if p.get('active', True) and (p.get('method') or 'wps') == method]
    for p in rows:
        if branch_id in (p.get('branch_ids') or []):
            return p
    return next((p for p in rows if not p.get('branch_ids')), None)


def summary(run, branches=None):
    ls = lines(run, branches)
    names = {m['code']: m['name'] for m in all_methods()}
    by_m, by_a = {}, {}
    for l in ls:
        k = l['method']
        g = by_m.setdefault(k, {'method': k, 'name': names.get(k, k), 'employees': 0, 'amount': ZERO, 'held': ZERO, 'paid': 0, 'paid_amount': ZERO,
                                'not_ready': 0, 'held_only': 0})
        g['employees'] += 1
        g['amount'] += Decimal(l['amount']); g['held'] += Decimal(l['held'])
        if l['paid']:
            g['paid'] += 1; g['paid_amount'] += Decimal(l['amount'])
        if l['state'] == 'not_ready':
            g['not_ready'] += 1
        if l['state'] == 'held':
            g['held_only'] += 1
        if k in ('wps', 'bank_transfer'):
            p = account_for(k, l['branch_id'])
            key = f"{k}:{p['id'] if p else ''}"
            a = by_a.setdefault(key, {'method': k, 'profile': p['id'] if p else None,
                                      'name': p['name'] if p else ('Central Bank WPS SIF (no set-up)' if k == 'wps' else 'No paying account set up'),
                                      'agent': (B.AGENT.get(p.get('agent')) or {}).get('name', '') if p else '', 'employees': 0, 'amount': ZERO,
                                      'branch_ids': set()})
            a['employees'] += 1; a['amount'] += Decimal(l['amount']); a['branch_ids'].add(l['branch_id'])
            l['account'] = a['profile']; l['account_name'] = a['name']
    order = [c for c, _n in BUILTIN] + [m['code'] for m in methods()]
    meth = sorted(by_m.values(), key=lambda g: order.index(g['method']) if g['method'] in order else 99)
    for g in meth:
        g['not_paid'] = g['employees'] - g['paid']
        g['state'] = 'paid' if g['employees'] and g['paid'] == g['employees'] else 'part' if g['paid'] else 'not_paid'
        for k in ('amount', 'held', 'paid_amount'):
            g[k] = f'{g[k]:.2f}'
    accs = []
    for a in by_a.values():
        a['amount'] = f"{a['amount']:.2f}"; a['branch_ids'] = sorted(b for b in a['branch_ids'] if b)
        accs.append(a)
    tot = sum((Decimal(l['amount']) for l in ls), ZERO)
    return {'lines': ls, 'methods': meth, 'accounts': accs, 'total': f'{tot:.2f}', 'employees': len({l['payslip'] for l in ls}),   # v1.63.0 (item 27): a split salary is one employee
            'parts': len(ls), 'split': sum(1 for l in ls if l.get('split') == 'second'), 'paid': sum(1 for l in ls if l['paid'])}


# ------------------------------------------------------------------ files
def _xlsx(title, head, rows, total_col=None, extra=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = title[:31]
    for r in extra or []:
        ws.append(r)
    ws.append(head)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
    if total_col is not None and rows:
        t = [''] * len(head); t[0] = tr('Total'); t[total_col] = float(sum(Decimal(str(r[total_col])) for r in rows))
        ws.append(t)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
    for i, col in enumerate(ws.columns):
        ws.column_dimensions[col[0].column_letter].width = 14 if i else 12
    b = io.BytesIO(); wb.save(b)
    return b.getvalue()


def _pdf(title, sub, head, rows, widths, landscape_=True, foot=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from Translations.pdf import fonts, has_arabic, shape
    ar_reg, ar_bold = fonts()

    def P(t, bold=False, size=8, align=0):
        t = '' if t is None else str(t)
        ar = has_arabic(t)
        st = ParagraphStyle('x', fontName=(ar_bold if bold else ar_reg) if ar else ('Helvetica-Bold' if bold else 'Helvetica'), fontSize=size,
                            leading=size + 3, alignment=2 if ar else align)
        t = shape(t) if ar else t
        return Paragraph(t.replace('&', '&amp;').replace('<', '&lt;'), st)
    b = io.BytesIO()
    size = landscape(A4) if landscape_ else A4
    doc = SimpleDocTemplate(b, pagesize=size, leftMargin=28, rightMargin=28, topMargin=28, bottomMargin=28, title=title)
    data = [[P(h, True) for h in head]] + [[P(c, align=2 if isinstance(c, str) and re.fullmatch(r'-?[\d,]+\.\d\d', c) else 0) for c in r] for r in rows]
    if foot:
        data.append([P(c, True) for c in foot])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#c9ccd8')), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef0ff')),
                           ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    doc.build([P(title, True, 13), Spacer(1, 4), P(sub, size=9), Spacer(1, 10), t])
    return b.getvalue()


def _company():
    t = B._tenant()
    return getattr(t, 'name', '') or ''


def _period(run):
    return f'{run.month:02d}/{run.year}'


def method_lines(run, method, branches=None, account=None, branch=None, only_unpaid=False):
    s = summary(run, branches)
    ls = [l for l in s['lines'] if l['method'] == method and l['state'] == 'ready']
    if account is not None and method in ('wps', 'bank_transfer'):
        ls = [l for l in ls if (l.get('account') or None) == (account or None)]
    if branch:
        ls = [l for l in ls if l['branch_id'] == branch]
    if only_unpaid:
        ls = [l for l in ls if not l['paid']]
    return ls


def cheque_numbers(run, ls, start=None, end=None):
    """cheque numbers from the range HR enters (kept, so a second print shows the same numbers); without a range the kept ones"""
    if start in (None, ''):
        return {l['payslip']: l.get('cheque_no') or '' for l in ls}
    s = str(start).strip()
    if not s.isdigit():
        raise B.FileError('Enter the first cheque number (digits).', {'cheque_start': tr('Digits only.')})
    width, first = len(s), int(s)
    if end not in (None, ''):
        e = str(end).strip()
        if not e.isdigit() or int(e) < first:
            raise B.FileError('The last cheque number must be a number after the first.', {'cheque_end': tr('Invalid.')})
        if int(e) - first + 1 < len(ls):
            raise B.FileError(tr('The cheque range has {0} cheques – {1} are needed.', int(e) - first + 1, len(ls)), {'cheque_end': tr('Too few cheques.')})
    nums = {l['payslip']: str(first + i).zfill(width) for i, l in enumerate(ls)}

    def keep(v):
        c = dict(v.get('cheques') or {})
        for k, n in nums.items():
            c[str(k)] = n
        v['cheques'] = c
    save_store(run.pk, keep)
    return nums


def denominations(amount):
    a, out = Decimal(amount), []
    for d in DENOMS:
        n = int(a // d)
        out.append(n)
        a -= d * n
    return out, a.quantize(Decimal('0.01'))


DKEYS = ['d_1000', 'd_500', 'd_200', 'd_100', 'd_50', 'd_20', 'd_10', 'd_5', 'd_1', 'd_050', 'd_025']


def payment_file(run, method, ls, prof=None, layout=None, branch_name='', method_name='', nums=None, kind=None):
    """the file of a payment method from its layout (built-in or the company's) → (file name, bytes, mime, preview lines, checks)"""
    import copy
    from . import layouts as LY
    from .services import amount_words
    code = (layout if isinstance(layout, str) else None) or (prof or {}).get('layout') or LY.DEFAULT_FOR.get(method if method in LY.DEFAULT_FOR else 'other')
    lay = layout if isinstance(layout, dict) else (LY.get(code) or LY.get(LY.DEFAULT_FOR.get(method, 'other_list')))
    lay = copy.deepcopy(lay)
    if kind == 'xlsx' and lay.get('file_type') == 'pdf':   # the same list as Excel
        lay['file_type'] = 'xlsx'; lay['file_name'] = re.sub(r'\.pdf$', '.xlsx', lay.get('file_name') or 'LIST.xlsx')
        lay['records'] = [{'kind': 'header', 'name': 'Heading row', 'fields': [{'source': 'const', 'label': f['label'], 'value': tr(f['label'])}
                                                                              for f in next(r for r in lay['records'] if r['kind'] == 'detail')['fields']]}] + lay['records']
    nums = nums or {}
    ctxs, tot = [], {k: 0 for k in DKEYS}
    rest_t = ZERO
    for i, l in enumerate(ls):
        amt = Decimal(l['amount'])
        n, rest = denominations(amt)
        c = {'seq': i + 1, 'emp_code': l['emp_code'], 'name': l['name'], 'iban': l['iban'], 'account': l['iban'], 'bank_name': l['bank'],
             'routing': l['routing'], 'agent_code': l['agent'], 'branch': l['branch'], 'amount': amt, 'net': Decimal(l['net']), 'held': Decimal(l['held']),
             'payment_ref': method_name if lay.get('code') == 'other_list' or lay.get('based_on') == 'other_list' else f'Salary {_period(run)}',
             'cheque_no': nums.get(l['payslip'], l.get('cheque_no') or ''), 'words_en': amount_words(amt, 'en'), 'words_ar': amount_words(amt, 'ar'),
             'paid_on': l.get('paid_on') or '', 'paid_ref': l.get('paid_ref') or '', 'd_rest': rest}
        for k, x in zip(DKEYS, n):
            c[k] = x; tot[k] += x
        rest_t += rest
        ctxs.append(c)
    total = sum((c['amount'] for c in ctxs), ZERO)
    extra = {'branch': branch_name or tr('All branches'), 'method': re.sub(r'[^A-Za-z0-9]+', '_', method_name or method).strip('_')[:30] or 'LIST', 't_rest': rest_t}
    extra.update({'t_' + k[2:]: v for k, v in tot.items()})
    fctx = B.file_context(run, prof, B._now(B._tenant()), len(ctxs), total, extra)
    name, content, mime, prev = LY.render(lay, ctxs, fctx)
    rows = LY.rows_of(lay, ctxs, fctx)
    return name, content, mime, prev, LY.check_made(lay, rows), lay


def mark(run, keys, paid_on, ref, user, unmark=False):
    """keys: [(payslip id, method)] → paid (date, reference) or not paid"""
    def f(v):
        p = dict(v.get('paid') or {})
        n = 0
        for pid, meth in keys:
            k = f'{pid}:{meth}'
            if unmark:
                n += 1 if p.pop(k, None) is not None else 0
            else:
                p[k] = {'on': paid_on, 'ref': ref, 'by': getattr(user, 'username', ''), 'at': datetime.datetime.now().isoformat(timespec='seconds')}
                n += 1
        v['paid'] = p
        return n
    return save_store(run.pk, f)
