"""v1.79.3 – the printed customer / supplier statement of account and ledger.

The report centre's "Print" of the four party reports (gl-customer-statement, gl-supplier-statement, gl-customer-ledger,
gl-supplier-ledger) prints a document per customer / supplier instead of the plain table: the company letterhead with the logo
(Print settings › Branding, else the company logo), the customer's name, number, TRN and address, the statement date and options,
the open items (or every transaction) with an ageing summary, the post-dated cheques under them and the net balance due.
The partners printed are the ones the screen shows (its filters); the options (PDC, foreign currency) are the report's.
"""
import csv
import html
import io
from collections import OrderedDict
from datetime import date

E = html.escape
PARTY_KEYS = ('gl-customer-statement', 'gl-supplier-statement', 'gl-customer-ledger', 'gl-supplier-ledger')


def _money(v):
    if v is None or v == '':
        return ''
    try:
        return f'{float(v):,.2f}'
    except (TypeError, ValueError):
        return E(str(v))


def _d(v):
    if isinstance(v, date):
        return v.strftime('%d/%m/%Y')
    s = str(v or '')
    return f'{s[8:10]}/{s[5:7]}/{s[:4]}' if len(s) >= 10 and s[4] == '-' else s


def _shown_partners(shown, label):
    """the partners in the table the screen sent (its filters); None = every partner"""
    if not shown:
        return None
    rows = list(csv.reader(io.StringIO(shown.lstrip('﻿'))))
    if not rows or label not in rows[0]:
        return None
    i = rows[0].index(label)
    return {r[i] for r in rows[1:] if i < len(r) and r[i]}


def _letterhead():
    from PrintTemplates.contexts import branding, company_ctx
    b = branding()
    co = company_ctx(None, 'en', b)
    logo = f'<img class="logo" src="{co["logo"]}" alt="">' if co.get('logo') else ''
    sub = ' · '.join(x for x in [co.get('address') or '', co.get('contact_line') or '', (f'TRN {co["trn"]}' if co.get('trn') else '')] if x)
    return f'<div class="lh">{logo}<div><div class="co">{E(co["name"])}</div><div class="sub">{E(sub)}</div></div></div>', b


CSS = """
.sh {{ display: flex; justify-content: space-between; gap: 12px; margin: 4px 0 8px; }}
.sh h1 {{ margin: 0; }}
.to {{ border: 1px solid #dfe2ea; border-radius: 4px; padding: 6px 9px; min-width: 70mm; line-height: 1.45; }}
.to b {{ font-size: 9.5pt; }}
.inf {{ text-align: end; line-height: 1.6; }}
.inf b {{ color: #1f2430; }}
.box {{ margin-top: 8px; display: flex; justify-content: flex-end; }}
.box table {{ width: auto; min-width: 85mm; }}
.box td {{ border: none; padding: 2px 6px; }}
.box tr.net td {{ border-top: 1.2px solid #1f2430; font-weight: 700; font-size: 9.5pt; }}
.age {{ margin-top: 8px; }}
.age th, .age td {{ text-align: end; }}
.pdc h2 {{ margin-top: 10px; }}
.pb {{ page-break-before: always; }}
tr.sub td {{ background: #f6f4fb; font-weight: 600; }}
.foot {{ margin-top: 10px; color: #4b5060; font-size: 7.2pt; }}
"""


def render(request, key, data, shown):
    """the PDF (bytes) or None when there is nothing to print"""
    from DashboardManagement.report_pdf import _page, _printed_by
    sale = key.startswith('gl-customer')
    ledger = key.endswith('ledger')
    who_label = 'Customer' if sale else 'Supplier'
    pick = _shown_partners(shown, who_label)
    groups = OrderedDict()
    for r in data.get('rows') or []:
        if pick is not None and r.get('partner') not in pick:
            continue
        groups.setdefault(r.get('partner'), []).append(r)
    if not groups:
        return None
    lh, b = _letterhead()
    opts = {o['key']: o['value'] for o in (data.get('options') or [])}
    with_pdc = opts.get('pdc', 'yes') == 'yes'
    fc = opts.get('fc', 'no')
    cols = {c['key']: c['label'] for c in data.get('columns') or []}
    from ZeoLedger import services as S
    from django.apps import apps
    Partner = apps.get_model('ZeoMasters', 'Partner')
    base = S.base_currency()
    per = data.get('period') or {}
    on = per.get('to')
    parts = []
    for n, (label, rows) in enumerate(groups.items()):
        pid = next((r.get('_pid') for r in rows if r.get('_pid')), None)
        p = Partner.objects.filter(pk=pid).first() if pid else None
        cur = next((r.get('_cur') for r in rows if r.get('_cur')), None) if fc == 'only' else base
        cur = cur or base
        addr = '<br>'.join(E(x) for x in [p.address if p else '', ', '.join(x for x in [p.city if p else '', p.country if p else ''] if x)] if x)
        contact = ' · '.join(x for x in [p.phone if p else '', p.email if p else ''] if x)
        to = (f'<div class="to"><b>{E(p.name if p else label)}</b><br>{E(who_label)} no. {E(p.number if p else "")}'
              f'{"<br>TRN " + E(p.tax_id) if p and p.tax_id else ""}{"<br>" + addr if addr else ""}{"<br>" + E(contact) if contact else ""}</div>')
        title = ('Customer ledger' if sale else 'Supplier ledger') if ledger else 'Statement of account'
        when = (f'<b>Period:</b> {_d(per.get("from"))} – {_d(on)}' if ledger else f'<b>Statement date:</b> {_d(on)}')
        optline = ('PDC included' if with_pdc else 'Without PDC') + ' · ' + {'no': f'Amounts in {base}', 'show': f'Amounts in {base} with the transaction currency',
                                                                              'only': f'Amounts in {cur}'}.get(fc, '')
        info = f'<div class="inf">{when}<br><b>Currency:</b> {E(cur)}<br>{E(optline)}<br><span style="color:#6b7080">{E(_printed_by(request))}</span></div>'
        head = f'{"" if n == 0 else "<div class=pb></div>" + lh}<div class="sh"><div><h1>{E(title)}</h1>{to}</div>{info}</div>'
        body = _ledger_body(rows, fc, cur) if ledger else _statement_body(rows, sale, with_pdc, fc, cur, on)
        parts.append(head + body)
    from DashboardManagement.report_pdf import _page as page_
    body_all = '<style>' + CSS.format() + '</style>' + ''.join(parts)
    return page_(f'{who_label} {"ledger" if ledger else "statement"}', '', body_all, landscape=ledger and fc == 'show', small=1 if ledger else 0)


def _statement_body(rows, sale, with_pdc, fc, cur, on):
    show_fc = fc == 'show'
    items = [r for r in rows if r.get('section') == 'Open items']
    pdcs = [r for r in rows if r.get('section') not in ('Open items', 'Balance')]
    th = ('<tr><th>Date</th><th>Document</th><th>Number</th><th>Reference</th><th>Due date</th><th class="n">Overdue (days)</th>'
          + ('<th>Currency</th><th class="n">Open in currency</th>' if show_fc else '') + f'<th class="n">Outstanding ({E(cur)})</th></tr>')
    trs = []
    age = OrderedDict((k, 0.0) for k in ('Not due', '1–30 days', '31–60 days', '61–90 days', 'Over 90 days'))
    tot = 0.0
    for r in items:
        v = float(r.get('outstanding') or 0)
        tot += v
        d = r.get('days') or 0
        k = 'Not due' if d <= 0 else '1–30 days' if d <= 30 else '31–60 days' if d <= 60 else '61–90 days' if d <= 90 else 'Over 90 days'
        age[k] += v
        trs.append(f'<tr><td>{_d(r.get("date"))}</td><td>{E(str(r.get("type") or ""))}</td><td>{E(str(r.get("number") or ""))}</td>'
                   f'<td>{E(str(r.get("reference") or ""))}</td><td>{_d(r.get("due"))}</td><td class="n">{r.get("days") or ""}</td>'
                   + (f'<td>{E(str(r.get("currency") or ""))}</td><td class="n">{_money(r.get("amount_currency"))}</td>' if show_fc else '')
                   + f'<td class="n">{_money(v)}</td></tr>')
    if not trs:
        trs.append(f'<tr><td colspan="{9 if show_fc else 7}">Nothing open on the date.</td></tr>')
    trs.append(f'<tr class="tot"><td colspan="{8 if show_fc else 6}">Total outstanding</td><td class="n">{_money(tot)}</td></tr>')
    out = f'<table><thead>{th}</thead><tbody>{"".join(trs)}</tbody></table>'
    out += ('<table class="age"><thead><tr>' + ''.join(f'<th>{E(k)}</th>' for k in age) + '<th>Total</th></tr></thead><tbody><tr>'
            + ''.join(f'<td>{_money(v)}</td>' for v in age.values()) + f'<td><b>{_money(tot)}</b></td></tr></tbody></table>')
    ptot = 0.0
    if with_pdc and pdcs:
        word = 'Post-dated cheques received' if sale else 'Post-dated cheques issued'
        prs = []
        for r in pdcs:
            v = float(r.get('pdc') or 0)
            ptot += v
            prs.append(f'<tr><td>{E(str(r.get("type") or "").replace("Cheque ", ""))}</td><td>{_d(r.get("due"))}</td><td>{E(str(r.get("reference") or "").split(" · against ")[0])}</td>'
                       f'<td>{E(str(r.get("number") or ""))}</td><td>{E((str(r.get("reference") or "").split(" · against ") + [""])[1] if " · against " in str(r.get("reference") or "") else str(r.get("reference") or "").replace("against ", ""))}</td>'
                       + (f'<td>{E(str(r.get("currency") or ""))}</td><td class="n">{_money(r.get("amount_currency"))}</td>' if show_fc else '')
                       + f'<td class="n">{_money(v)}</td></tr>')
        prs.append(f'<tr class="tot"><td colspan="{7 if show_fc else 5}">Total post-dated cheques</td><td class="n">{_money(ptot)}</td></tr>')
        out += (f'<div class="pdc"><h2>{E(word)}</h2><table><thead><tr><th>Cheque no.</th><th>Cheque date</th><th>Bank</th><th>{"Receipt" if sale else "Payment"}</th><th>Against</th>'
                + ('<th>Currency</th><th class="n">Amount in currency</th>' if show_fc else '') + f'<th class="n">Amount ({E(cur)})</th></tr></thead><tbody>{"".join(prs)}</tbody></table></div>')
    net = tot - ptot
    word = 'Less post-dated cheques received' if sale else 'Less post-dated cheques issued'
    out += ('<div class="box"><table>'
            f'<tr><td>Total outstanding</td><td class="n">{_money(tot)} {E(cur)}</td></tr>'
            + (f'<tr><td>{E(word)}</td><td class="n">− {_money(ptot)} {E(cur)}</td></tr>' if with_pdc and pdcs else '')
            + f'<tr class="net"><td>{"Net balance due" if sale else "Net balance payable"}{" after PDC" if with_pdc and pdcs else ""}</td><td class="n">{_money(net)} {E(cur)}</td></tr>'
            '</table></div>')
    if sale:
        out += '<div class="foot">Please check this statement and let us know of any difference. Post-dated cheques are shown until they are cleared on their date.</div>'
    return out


def _ledger_body(rows, fc, cur):
    show_fc = fc == 'show'
    th = ('<tr><th>Date</th><th>Transaction</th><th>Number</th><th>Reference</th><th>Description</th>'
          + ('<th>Currency</th><th class="n">Amount in currency</th>' if show_fc else '')
          + f'<th class="n">Debit ({E(cur)})</th><th class="n">Credit ({E(cur)})</th><th class="n">Balance ({E(cur)})</th></tr>')
    trs = []
    for r in rows:
        t = str(r.get('type') or '')
        cls = ' class="sub"' if t in ('Opening balance', 'Closing balance') else ''
        trs.append(f'<tr{cls}><td>{_d(r.get("date"))}</td><td>{E(t)}</td><td>{E(str(r.get("number") or ""))}</td><td>{E(str(r.get("reference") or ""))}</td>'
                   f'<td>{E(str(r.get("description") or ""))}</td>'
                   + (f'<td>{E(str(r.get("currency") or "") if r.get("amount_currency") is not None else "")}</td><td class="n">{_money(r.get("amount_currency"))}</td>' if show_fc else '')
                   + f'<td class="n">{_money(r.get("debit"))}</td><td class="n">{_money(r.get("credit"))}</td><td class="n">{_money(r.get("balance"))}</td></tr>')
    return f'<table><thead>{th}</thead><tbody>{"".join(trs)}</tbody></table>'


# ------------------------------------------------------------------ v1.79.3 the T format printed (trial balance, profit and loss, balance sheet)
T_TITLES = {'gl-trial-balance': 'Trial balance', 'gl-profit-loss': 'Trading and profit and loss account', 'gl-balance-sheet': 'Balance sheet'}


def render_t(request, key, data):
    """two sides next to each other, groups and totals in bold, accounts indented, the totals ruled – on the letterhead with the logo"""
    from DashboardManagement.report_pdf import _page, _printed_by
    cols = {c['key']: c['label'] for c in data.get('columns') or []}
    per = data.get('period') or {}
    if key == 'gl-balance-sheet':
        when = f'as at {_d(per.get("to"))}'
    elif key == 'gl-trial-balance':
        when = f'as at {_d(per.get("to"))}'
    else:
        when = f'for the period {_d(per.get("from"))} – {_d(per.get("to"))}'

    def cell(r, side):
        p, a, t, b = r.get(f'{side}_p') or '', r.get(f'{side}_a'), r.get(f'{side}_t'), r.get(f'_{side}b')
        ind = p.startswith('    ')
        cls = ' class="b"' if b else (' class="i"' if ind else '')
        return f'<td{cls}>{E(p.strip())}</td><td class="n">{_money(a)}</td><td class="n{" b" if b else ""}">{_money(t)}</td>'

    trs = []
    for r in data.get('rows') or []:
        trs.append(f'<tr{" class=tt" if r.get("_tot") else ""}>{cell(r, "l")}<td class="mid"></td>{cell(r, "r")}</tr>')
    th = (f'<tr><th>{E(cols.get("l_p", ""))}</th><th class="n">{E(cols.get("l_a", "Amount"))}</th><th class="n">{E(cols.get("l_t", "Total"))}</th><th class="mid"></th>'
          f'<th>{E(cols.get("r_p", ""))}</th><th class="n">{E(cols.get("r_a", "Amount"))}</th><th class="n">{E(cols.get("r_t", "Total"))}</th></tr>')
    css = ('<style>td.b{font-weight:700} td.i{padding-inline-start:14px;color:#2f3442} td.mid,th.mid{width:6px;border-inline-start:1.2px solid #1f2430;padding:0}'
           ' tr.tt td{border-top:1.4px solid #1f2430;border-bottom:2.4px double #1f2430;font-weight:700;background:#fafafa} table{table-layout:fixed}'
           ' th:nth-child(1),th:nth-child(5){width:31%} .meta{margin-bottom:8px}</style>')
    head = (f'{css}<h1>{E(T_TITLES.get(key, data.get("title", "")))}</h1><div class="meta">{E(when)} · '
            f'{E(_printed_by(request))}</div>')
    notes = ''.join(f'<div>{E(str(n))}</div>' for n in (data.get('notes') or []) if not str(n).endswith('Click an account to open it.') or True)
    body = f'<table><thead>{th}</thead><tbody>{"".join(trs)}</tbody></table><div class="notes">{notes.replace(" Click an account to open it.", "")}</div>'
    return _page(T_TITLES.get(key, data.get('title', '')), head, body, landscape=False, small=1)


def render_fr(request, key, data):
    """v1.79.4 a statement of the financial report designer as designed: headings and totals in bold, the lines indented, a rule above a
    subtotal, a double rule under a total, % columns with one decimal – on the letterhead with the logo"""
    from DashboardManagement.report_pdf import _page, _printed_by
    cols = data.get('columns') or []
    per = data.get('period') or {}
    num = [c for c in cols if c.get('type') == 'number']
    txt = [c for c in cols if c.get('type') != 'number']
    pct = {c['key'] for c in num if '%' in (c.get('label') or '')}
    rtl = any('؀' <= ch <= 'ۿ' for r in (data.get('rows') or [])[:40] for ch in str(r.get('line') or ''))

    whole = all(float(r.get(c['key'])).is_integer() for r in (data.get('rows') or []) for c in num
                if c['key'] not in pct and isinstance(r.get(c['key']), (int, float)))   # thousands / millions: no decimals

    def amt(v, k):
        if v is None or v == '':
            return ''
        try:
            v = float(v)
        except (TypeError, ValueError):
            return E(str(v))
        if k in pct:
            return f'{v:,.1f}%'
        s = f'{abs(v):,.0f}' if whole else f'{abs(v):,.2f}'
        return f'({s})' if v < 0 else s   # negatives in brackets, as accountants print them

    trs = []
    for r in data.get('rows') or []:
        st = r.get('_s') or 'normal'
        if st == 'blank' or r.get('_k') == 'blank':
            trs.append(f'<tr class="bl"><td colspan="{len(cols)}">&nbsp;</td></tr>')
            continue
        cls = ' '.join(x for x in ('b' if r.get('_b') else '', st if st in ('subtotal', 'total') else '', 'h' if r.get('_k') == 'heading' else '') if x)
        ind = int(r.get('_i') or 0)
        label = str(r.get('line') or '').lstrip(' ')
        cells = f'<td style="padding-inline-start:{4 + ind * 14}px">{E(label)}</td>'
        cells += ''.join(f'<td>{E(str(r.get(c["key"]) or ""))}</td>' for c in txt[1:])
        cells += ''.join(f'<td class="n">{amt(r.get(c["key"]), c["key"])}</td>' for c in num)
        trs.append(f'<tr class="{cls}">{cells}</tr>')
    th = ''.join(f'<th>{E(c.get("label") or "")}</th>' for c in txt) + ''.join(f'<th class="n">{E(c.get("label") or "")}</th>' for c in num)
    w = max(30, 62 - 7 * len(num))
    css = ('<style>table{table-layout:fixed} th:first-child{width:%d%%} th{border-bottom:1.4px solid #1f2430} td{border:0;padding-top:2.5px;padding-bottom:2.5px}'
           ' tr.b td{font-weight:700} tr.h td{padding-top:7px} tr.subtotal td.n{border-top:1px solid #1f2430}'
           ' tr.total td.n{border-top:1.2px solid #1f2430;border-bottom:2.6px double #1f2430} tr.total td{font-weight:700}'
           ' tr.bl td{padding:2px} th{white-space:normal !important;vertical-align:bottom;word-break:break-word} .sub{color:#4b5060;margin:-2px 0 4px} %s</style>') % (
        w, 'table{direction:rtl} td.n,th.n{text-align:left} h1,.sub,.meta,.notes{text-align:right}' if rtl else '')
    title = data.get('title') or ''
    has_period = data.get('basis') != 'position' and per.get('from')
    when = f'for the period {_d(per.get("from"))} – {_d(per.get("to"))}' if has_period else f'as at {_d(per.get("to"))}'
    sub = f'<div class="sub">{E(data.get("subtitle"))}</div>' if data.get('subtitle') else ''
    head = f'{css}<h1>{E(title)}</h1>{sub}<div class="meta">{E(when)} · {E(_printed_by(request))}</div>'
    notes = ''.join(f'<div>{E(str(n))}</div>' for n in (data.get('notes') or []) if 'Click a line' not in str(n))
    body = f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table><div class="notes">{notes}</div>'
    return _page(title, head, body, landscape=len(num) > 5, small=1 if len(num) > 3 else 0)
