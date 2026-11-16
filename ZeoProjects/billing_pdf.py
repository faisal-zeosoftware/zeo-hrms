"""v1.67.13 (billing) – PDF of a proforma invoice or a tax invoice (reportlab), with the company letterhead, signatory and
stamp from Company profile when filled in.

A tax invoice carries what a UAE tax invoice shows: the words "Tax Invoice", the supplier's name, address and TRN, the
customer's name, address and TRN, a sequential number, the issue date (and the supply date when different), each line
with quantity, unit price, VAT rate and VAT amount, and the totals (VAT also in AED when the currency is not AED, with the
exchange rate). A PI is headed "Proforma Invoice" and says it is not a tax invoice.
"""
import io
import os
from decimal import Decimal


def _company():
    try:
        from SelfService.letters import company
        return company()
    except Exception:   # noqa: BLE001
        return None


def _profile():
    try:
        from OrgStructure.models import CompanyProfile
        return CompanyProfile.objects.order_by('id').first()
    except Exception:   # noqa: BLE001
        return None


def _img(f):
    try:
        if f and f.name and os.path.exists(f.path):
            return f.path
    except Exception:   # noqa: BLE001
        pass
    return None


def _font():
    try:
        from Translations.pdf import fonts
        return fonts()
    except Exception:   # noqa: BLE001
        return ('Helvetica', 'Helvetica-Bold')


def _sh(t):
    try:
        from Translations.pdf import shape
        return shape(t)
    except Exception:   # noqa: BLE001
        return str(t or '')


def _esc(t):
    return str(t or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def build(doc):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    reg_f, bold_f = _font()
    comp, prof = _company(), _profile()
    is_inv = doc.kind == 'invoice'
    title = 'Tax Invoice' if is_inv else 'Proforma Invoice'
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f'{title} {doc.number}')

    def P(t, size=9.5, bold=False, color='#1f2430', align=0):
        return Paragraph(_sh(_esc(t)).replace('\n', '<br/>'), ParagraphStyle('x', fontName=bold_f if bold else reg_f, fontSize=size,
                                                                                leading=size * 1.3, textColor=colors.HexColor(color), alignment=align))

    story = []
    name = getattr(prof, 'legal_name', '') or getattr(comp, 'name', '') or 'Company'
    logo = _img(getattr(comp, 'logo', None))
    addr = ', '.join(x for x in (getattr(comp, 'address_line1', ''), getattr(comp, 'address_line2', ''), getattr(comp, 'city', '')) if x)
    head_left = [P(name, 14, True)]
    for line in (addr, (getattr(prof, 'po_box', '') and f'P.O. Box {prof.po_box}'), getattr(prof, 'phone', ''), getattr(prof, 'email', ''),
                 getattr(prof, 'website', ''), (getattr(prof, 'tax_id', '') and f'TRN {prof.tax_id}')):
        if line:
            head_left.append(P(line, 8.5, color='#5b6170'))
    right = [P(title.upper(), 17, True, '#0f4c81', 2), P(doc.number, 12, True, align=2)]
    if logo:
        right.insert(0, Image(logo, width=38 * mm, height=18 * mm, kind='proportional', hAlign='RIGHT'))
    t = Table([[head_left, right]], colWidths=[108 * mm, 70 * mm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), 0.8, colors.HexColor('#0f4c81')),
                           ('BOTTOMPADDING', (0, 0), (-1, 0), 8)]))
    story += [t, Spacer(1, 6 * mm)]

    c = doc.customer
    cl = [P('Bill to', 8, color='#5b6170'), P(c.name, 11, True)]
    for line in (c.address, ' '.join(x for x in (c.city, c.country) if x), (c.trn and f'TRN {c.trn}'), c.email):
        if line:
            cl.append(P(line, 9))
    meta = [('Date', doc.date.strftime('%d/%m/%Y'))]
    if doc.supply_date and doc.supply_date != doc.date:
        meta.append(('Date of supply', doc.supply_date.strftime('%d/%m/%Y')))
    if is_inv and doc.due_date:
        meta.append(('Due date', doc.due_date.strftime('%d/%m/%Y')))
    meta += [('Currency', doc.currency), ('Your ref.', doc.client_ref)]
    if is_inv and doc.from_pi_id:
        meta.append(('Proforma', doc.from_pi.number))
    mt = Table([[P(k, 8, color='#5b6170'), P(v, 9)] for k, v in meta if v], colWidths=[24 * mm, 46 * mm])
    mt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    t = Table([[cl, mt]], colWidths=[108 * mm, 70 * mm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story += [t, Spacer(1, 6 * mm)]

    money = lambda v: f'{float(v or 0):,.2f}'  # noqa: E731
    W = '#ffffff'
    rows = [[P('#', 8, True, W), P('Description', 8, True, W), P('Qty', 8, True, W, 2), P('Unit price', 8, True, W, 2),
             P('Amount', 8, True, W, 2), P('VAT %', 8, True, W, 2), P('VAT', 8, True, W, 2), P('Total', 8, True, W, 2)]]
    for i, ln in enumerate(doc.lines.all(), 1):
        vat_lbl = f'{float(ln.vat_rate):g}' if ln.vat_type == 'standard' else ('0 (zero-rated)' if ln.vat_type == 'zero' else 'Exempt')
        rows.append([P(str(i), 8.5), P(ln.description, 8.5), P(f'{float(ln.quantity):g}', 8.5, align=2), P(money(ln.unit_price), 8.5, align=2),
                     P(money(ln.amount), 8.5, align=2), P(vat_lbl, 8, align=2), P(money(ln.vat_amount), 8.5, align=2),
                     P(money(ln.amount + ln.vat_amount), 8.5, align=2)])
    lt = Table(rows, colWidths=[8 * mm, 66 * mm, 12 * mm, 20 * mm, 20 * mm, 14 * mm, 18 * mm, 20 * mm], repeatRows=1)
    lt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f4c81')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6fb')]),
                            ('LINEBELOW', (0, -1), (-1, -1), 0.6, colors.HexColor('#c9cfdd')),
                            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    story += [lt, Spacer(1, 3 * mm)]
    tot = [('Total before VAT', money(doc.subtotal)), ('VAT', money(doc.vat_amount)), (f'Total {doc.currency}', money(doc.total))]
    if doc.currency != 'AED':
        rate = Decimal(doc.exchange_rate or 1)
        tot += [(f'Exchange rate (AED per {doc.currency})', f'{float(rate):g}'), ('VAT in AED', money(doc.vat_amount * rate)),
                ('Total in AED', money(doc.total * rate))]
    if is_inv and doc.paid_amount:
        tot += [('Received', money(doc.paid_amount)), ('Balance due', money(doc.balance))]
    tt = Table([[P(k, 9, k.startswith('Total ') or k == 'Balance due', align=2), P(v, 9, k.startswith('Total ') or k == 'Balance due', align=2)]
                for k, v in tot], colWidths=[56 * mm, 30 * mm], hAlign='RIGHT')
    tt.setStyle(TableStyle([('LINEABOVE', (0, 2), (-1, 2), 0.8, colors.HexColor('#0f4c81')), ('TOPPADDING', (0, 0), (-1, -1), 2),
                            ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    story += [tt, Spacer(1, 6 * mm)]
    if not is_inv:
        story += [P('This is a proforma invoice, not a tax invoice. A tax invoice is issued on acceptance.', 8.5, color='#8a4b00'), Spacer(1, 3 * mm)]
    if doc.notes:
        story += [P('Notes', 8, True, '#5b6170'), P(doc.notes, 9), Spacer(1, 3 * mm)]
    if doc.terms:
        story += [P('Terms and payment details', 8, True, '#5b6170'), P(doc.terms, 8.5, color='#3a4050'), Spacer(1, 6 * mm)]
    sig = [P('For ' + name, 9, True)]
    imgs = [Image(x, width=30 * mm, height=18 * mm, kind='proportional', hAlign='LEFT')
            for x in (_img(getattr(prof, 'signature', None)), _img(getattr(prof, 'stamp', None))) if x]
    if imgs:
        sig.append(Table([imgs], hAlign='LEFT'))
    if getattr(prof, 'signatory_name', ''):
        sig.append(P(prof.signatory_name, 9))
    if getattr(prof, 'signatory_designation', ''):
        sig.append(P(prof.signatory_designation, 8.5, color='#5b6170'))
    story.append(Table([[sig, [P('Received by the customer', 9, True), Spacer(1, 14 * mm), P('Name / signature / date', 8, color='#5b6170')]]],
                       colWidths=[90 * mm, 88 * mm], style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')])))
    if getattr(prof, 'letterhead_footer', ''):
        story += [Spacer(1, 6 * mm), P(prof.letterhead_footer, 7.5, color='#8a90a0', align=1)]
    pdf.build(story)
    return buf.getvalue()
