"""v1.62.0 EOS – the final settlement statement as a PDF on the company letterhead (reportlab), in the reader's language (Arabic is
shaped and laid out right to left). Signature lines: Prepared by HR, Checked by Finance, Approved by, Employee acknowledgement."""
import io
import os
from datetime import date, datetime

from django.apps import apps
from django.utils import timezone

from Translations.i18n import tr, current_lang

M = apps.get_model


def _font(rtl):
    if not rtl:
        return ('Helvetica', 'Helvetica-Bold')
    try:
        from Translations.pdf import fonts
        return fonts()
    except Exception:
        return ('Helvetica', 'Helvetica-Bold')


def _sh(t, rtl):
    t = '' if t is None else str(t)
    if not rtl:
        return t
    try:
        from Translations.pdf import shape
        return shape(t)
    except Exception:
        return t


def _img(f):
    try:
        if f and f.name and os.path.exists(f.path):
            return f.path
    except Exception:
        pass
    return None


def _d(v):
    if not v:
        return ''
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError:
            return v[:10]
    if isinstance(v, datetime):
        v = timezone.localtime(v).date() if timezone.is_aware(v) else v.date()
    return v.strftime('%d/%m/%Y') if isinstance(v, date) else str(v)


def ymd(svc):
    y, m, d = svc.get('years', 0), svc.get('months', 0), svc.get('days', 0)
    parts = [tr('1 year') if y == 1 else tr('{0} years', y), tr('1 month') if m == 1 else tr('{0} months', m), tr('1 day') if d == 1 else tr('{0} days', d)]
    return ', '.join(parts)


def money(v):
    return f'{float(v or 0):,.2f}'


def statement_context(case):
    """v1.62.0 EOS – everything a final settlement statement shows, in one dict (the PDF below renders it; a print template service can
    render the same dict):
      company      {name, name_local, po_box, phone, email, website, tax_id, letterhead_header, letterhead_footer, logo_path, stamp_path, signature_path}
      document     {title, number, status, calc_date, due_date, paid_on, payment_ref, currency}
      employee     {name, code, designation, department, branch, joined, last_working_date, notice_date, notice_days, exit_type, exit_type_label,
                    reason, photo, basic, wage, unpaid_days}
      service      {years, months, days, text}
      earnings     [{key, label, basis, calc: [steps], amount, by (manual lines)}]      deductions  [same]      notes  [{label, basis}] (info / block)
      totals       {earnings, deductions, net, net_words}
      approvals    {prepared: {by, at}, checked: {by, at}, approved: {by, at}, paid: {by, at}} and history [{action, label, by, at, note}]
      case         the full Leavers case (leavers.case) it was made from"""
    st = case.get('settlement') or {}
    try:
        from OrgStructure.models import CompanyProfile
        prof = CompanyProfile.objects.order_by('id').first()
    except Exception:
        prof = None
    try:
        from SelfService.letters import company
        comp = company()
    except Exception:
        comp = None
    g = lambda o, f: getattr(o, f, '') or ''  # noqa: E731
    hist = {}
    for h in st.get('history') or []:
        hist[h.get('action')] = h
    pick = lambda *a: next(({'by': hist[x].get('by'), 'at': hist[x].get('at')} for x in a if x in hist), {})  # noqa: E731
    lines = st.get('lines') or []
    return {
        'company': {'name': g(prof, 'legal_name') or g(comp, 'name') or case.get('company') or '', 'name_local': g(prof, 'name_local'),
                    'po_box': g(prof, 'po_box'), 'phone': g(prof, 'phone'), 'email': g(prof, 'email'), 'website': g(prof, 'website'),
                    'tax_id': g(prof, 'tax_id'), 'letterhead_header': g(prof, 'letterhead_header'), 'letterhead_footer': g(prof, 'letterhead_footer'),
                    'logo_path': _img(getattr(comp, 'logo', None)), 'stamp_path': _img(getattr(prof, 'stamp', None)),
                    'signature_path': _img(getattr(prof, 'signature', None))},
        'document': {'title': tr('Final settlement statement'), 'number': st.get('number') or '', 'status': case.get('stage_label', ''),
                     'calc_date': st.get('calc_date'), 'due_date': case.get('due_date'), 'paid_on': st.get('paid_on'),
                     'payment_ref': st.get('payment_ref') or '', 'currency': 'AED'},
        'employee': {'name': case.get('employee'), 'code': case.get('employee_code'), 'designation': case.get('designation', ''),
                     'department': case.get('department', ''), 'branch': case.get('branch', ''), 'joined': case.get('joined'),
                     'last_working_date': case.get('last_working_date'), 'notice_date': case.get('notice_date'), 'notice_days': case.get('notice_days'),
                     'exit_type': case.get('exit_type'), 'exit_type_label': case.get('exit_type_label', ''), 'reason': case.get('reason', ''),
                     'photo': case.get('photo', ''), 'basic': case.get('basic'), 'wage': case.get('wage'), 'unpaid_days': case.get('unpaid_days')},
        'service': {**(case.get('service') or {}), 'text': ymd(case.get('service') or {})},
        'earnings': [l for l in lines if l.get('kind') == 'earning'],
        'deductions': [l for l in lines if l.get('kind') == 'deduction'],
        'notes': [l for l in lines if l.get('kind') in ('info', 'block')],
        'totals': {'earnings': st.get('total_earnings'), 'deductions': st.get('total_deductions'), 'net': st.get('net'), 'net_words': st.get('net_words', '')},
        'approvals': {'prepared': pick('submit', 'calculated'), 'checked': pick('validate'), 'approved': pick('approve'), 'paid': pick('pay')},
        'history': st.get('history') or [],
        'case': case,
    }


def build(case):
    """case: leavers.case(...) of a leaver with a settlement → PDF bytes (statement_context → render_pdf)."""
    return render_pdf(statement_context(case))


def render_pdf(ctx):
    """The statement dict of statement_context() → PDF bytes on the letterhead."""
    case = ctx['case']
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    st = case['settlement']
    rtl = str(current_lang() or 'en').startswith('ar')
    reg_f, bold_f = _font(rtl)
    NAVY, MUTED, LINE, SOFT = '#1b1640', '#5b6170', '#d9dce6', '#f5f4fd'
    A_START, A_END = (2, 0) if rtl else (0, 2)

    def P(t, size=9, bold=False, color='#1f2430', align=None):
        return Paragraph(_sh(t, rtl).replace('\n', '<br/>'), ParagraphStyle('x', fontName=bold_f if bold else reg_f, fontSize=size, leading=size * 1.35,
                                                                         textColor=colors.HexColor(color), alignment=A_START if align is None else align))

    def row_(cells):
        return list(reversed(cells)) if rtl else cells

    def widths(ws):
        return list(reversed(ws)) if rtl else ws

    try:
        from OrgStructure.models import CompanyProfile
        prof = CompanyProfile.objects.order_by('id').first()
    except Exception:
        prof = None
    try:
        from SelfService.letters import company
        comp = company()
    except Exception:
        comp = None
    name = getattr(prof, 'legal_name', '') or getattr(comp, 'name', '') or case.get('company') or ''
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=13 * mm, bottomMargin=14 * mm,
                            title=f"{tr('Final settlement')} {st.get('number') or ''} – {case['employee']}")
    story = []
    # ---- letterhead
    left = [P(name, 14, True, NAVY)]
    if rtl and getattr(prof, 'name_local', ''):
        left.append(P(prof.name_local, 11, True, NAVY))
    for line in ((getattr(prof, 'po_box', '') and f'P.O. Box {prof.po_box}'), getattr(prof, 'phone', ''), getattr(prof, 'email', ''),
                 getattr(prof, 'website', ''), (getattr(prof, 'tax_id', '') and f'TRN {prof.tax_id}')):
        if line:
            left.append(P(line, 8, color=MUTED))
    if getattr(prof, 'letterhead_header', ''):
        left.append(P(prof.letterhead_header, 8, color=MUTED))
    right = [P(tr('Final settlement statement'), 13, True, NAVY, A_END), P(st.get('number') or '', 10, True, align=A_END),
             P(tr('Status: {0}', tr(case['stage_label'])), 8, color=MUTED, align=A_END)]
    logo = _img(getattr(comp, 'logo', None))
    if logo:
        right.insert(0, Image(logo, width=36 * mm, height=16 * mm, kind='proportional', hAlign='LEFT' if rtl else 'RIGHT'))
    t = Table([row_([left, right])], colWidths=widths([100 * mm, 78 * mm]))
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), 1.2, colors.HexColor(NAVY)), ('BOTTOMPADDING', (0, 0), (-1, 0), 8)]))
    story += [t, Spacer(1, 5 * mm)]
    # ---- employee and exit
    svc = case.get('service') or {}
    facts = [(tr('Employee'), f"{case['employee']} ({case['employee_code']})"), (tr('Designation'), case.get('designation', '')),
             (tr('Department'), case.get('department', '')), (tr('Branch'), case.get('branch', '')),
             (tr('Joined'), _d(case.get('joined'))), (tr('Last working day'), _d(case.get('last_working_date'))),
             (tr('Exit type'), case.get('exit_type_label', '')), (tr('Total service'), ymd(svc)),
             (tr('Unpaid days'), f"{float(case.get('unpaid_days') or 0):g}"), (tr('Basic salary'), 'AED ' + money(case.get('basic'))),
             (tr('Monthly wage'), 'AED ' + money(case.get('wage'))), (tr('Payment due by'), _d(case.get('due_date')))]
    cells = []
    for i in range(0, len(facts), 2):
        r = []
        for k, v in facts[i:i + 2]:
            r += [P(k, 7.5, color=MUTED), P(v, 9, True)]
        cells.append(row_(r))
    ft = Table(cells, colWidths=widths([28 * mm, 61 * mm, 28 * mm, 61 * mm]))
    ft.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(SOFT)), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3), ('BOX', (0, 0), (-1, -1), 0.4, colors.HexColor(LINE))]))
    story += [ft, Spacer(1, 5 * mm)]

    # ---- earnings and deductions
    def section(title, kind):
        ls = [l for l in (st.get('lines') or []) if l.get('kind') == kind]
        if not ls:
            return []
        rows = [row_([P(title, 9, True, '#ffffff'), P(tr('Basis'), 8, True, '#ffffff'), P(tr('Amount (AED)'), 8, True, '#ffffff', A_END)])]
        for l in ls:
            basis = l.get('basis') or ''
            if l.get('calc'):
                basis = (basis + '\n' if basis else '') + '\n'.join('· ' + str(c) for c in l['calc'])
            if l.get('manual') and l.get('by'):
                basis = (basis + '\n' if basis else '') + tr('Added by {0}', l['by'])
            rows.append(row_([P(l.get('label', ''), 8.5), P(basis, 7.5, color=MUTED), P(money(l.get('amount')), 9, align=A_END)]))
        total = st.get('total_earnings') if kind == 'earning' else st.get('total_deductions')
        rows.append(row_([P(tr('Total earnings') if kind == 'earning' else tr('Total deductions'), 9, True), P('', 8),
                          P(money(total), 9.5, True, align=A_END)]))
        tb = Table(rows, colWidths=widths([62 * mm, 86 * mm, 30 * mm]), repeatRows=1)
        tb.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(NAVY if kind == 'earning' else '#7a2a2a')),
                                ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 1), (-1, -2), 0.3, colors.HexColor(LINE)),
                                ('LINEABOVE', (0, -1), (-1, -1), 0.8, colors.HexColor(NAVY)), ('TOPPADDING', (0, 0), (-1, -1), 3),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        return [tb, Spacer(1, 4 * mm)]

    story += section(tr('Earnings'), 'earning')
    story += section(tr('Deductions'), 'deduction')
    notes = [l for l in (st.get('lines') or []) if l.get('kind') in ('info', 'block')]
    if notes:
        story.append(P(tr('Notes'), 8, True, MUTED))
        for l in notes:
            story.append(P('· ' + l.get('label', '') + (f" – {l['basis']}" if l.get('basis') else ''), 7.5, color=MUTED))
        story.append(Spacer(1, 3 * mm))
    # ---- net payable
    net = Table([row_([[P(tr('Net payable'), 10, True, '#ffffff'), P(st.get('net_words') or '', 8.5, color='#e8e6fb')],
                       P('AED ' + money(st.get('net')), 16, True, '#ffffff', A_END)])], colWidths=widths([128 * mm, 50 * mm]))
    net.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(NAVY)), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                             ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (-1, -1), 8),
                             ('RIGHTPADDING', (0, 0), (-1, -1), 8)]))
    story += [net, Spacer(1, 3 * mm)]
    if st.get('paid_on'):
        story.append(P(tr('Paid on {0}, reference {1}', _d(st['paid_on']), st.get('payment_ref') or ''), 8.5, True, '#1d6b37'))
    else:
        story.append(P(tr('To be paid by {0} (UAE Labour Law: within 14 days of the last working day).', _d(case.get('due_date'))), 8, color=MUTED))
    story.append(Spacer(1, 8 * mm))
    # ---- signatures
    hist = {h.get('action'): h for h in (st.get('history') or [])}
    heads = [tr('Prepared by HR'), tr('Checked by Finance'), tr('Approved by'), tr('Employee acknowledgement')]
    who = [hist.get('submit') or hist.get('calculated'), hist.get('validate'), hist.get('approve'), {'by': case['employee']}]
    sub = ['', '', '', tr('I received the amount above in full and final settlement.')]
    sg = Table([row_([P(h, 8.5, True, NAVY) for h in heads]), row_([P(x, 6.8, color=MUTED) for x in sub]),
                row_([P(tr('Signature'), 7, color=MUTED) for _ in heads]),
                row_([P((w or {}).get('by') or '', 8.5, True) for w in who]),
                row_([P(_d((w or {}).get('at')) if (w or {}).get('at') else tr('Date: ____________'), 7.5, color=MUTED) for w in who])],
               colWidths=[44.5 * mm] * 4, rowHeights=[None, 9 * mm, 12 * mm, None, None])
    sg.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.4, colors.HexColor(LINE)),
                            ('LINEAFTER', (0, 0), (-2, -1), 0.4, colors.HexColor(LINE)), ('VALIGN', (0, 2), (-1, 2), 'BOTTOM'),
                            ('LINEBELOW', (0, 2), (-1, 2), 0.6, colors.HexColor('#9aa0b5')), ('TOPPADDING', (0, 0), (-1, -1), 4),
                            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(SOFT))]))
    story.append(KeepTogether([sg]))
    footer = getattr(prof, 'letterhead_footer', '') or ''

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont(reg_f, 7)
        canvas.setFillColor(colors.HexColor('#8a90a0'))
        txt = _sh((footer + '   ·   ' if footer else '') + tr('Page {0}', d.page), rtl)
        canvas.drawCentredString(A4[0] / 2, 8 * mm, txt)
        canvas.restoreState()
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
