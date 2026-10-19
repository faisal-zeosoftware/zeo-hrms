"""v1.33.0 – Quotation / sales order PDF (reportlab), with the company's letterhead, signatory and stamp from Company profile
(Organisation → Company) when they are filled in. Arabic names are shaped (Translations.pdf)."""
import io
import os

from django.http import HttpResponse

from zeo.appkit.engine import emp_name
from zeo.appkit.views import Base, guard, get_reg

from .models import Quotation, SalesOrder


def _company():
    try:
        from SelfService.letters import company
        return company()
    except Exception:
        return None


def _profile():
    try:
        from OrgStructure.models import CompanyProfile
        return CompanyProfile.objects.order_by('id').first()
    except Exception:
        return None


def _img(f):
    try:
        if f and f.name and os.path.exists(f.path):
            return f.path
    except Exception:
        pass
    return None


def _font():
    try:
        from Translations.pdf import fonts
        return fonts()
    except Exception:
        return ('Helvetica', 'Helvetica-Bold')


def _sh(t):
    try:
        from Translations.pdf import shape
        return shape(t)
    except Exception:
        return str(t or '')


def build(doc, kind, app):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    reg_f, bold_f = _font()
    comp, prof = _company(), _profile()
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm, title=f'{kind} {doc.number}')
    P = lambda t, size=9.5, bold=False, color='#1f2430', align=0: Paragraph(_sh(t).replace('\n', '<br/>'), ParagraphStyle('x', fontName=bold_f if bold else reg_f, fontSize=size, leading=size * 1.3, textColor=colors.HexColor(color), alignment=align))  # noqa: E731
    story = []
    name = getattr(prof, 'legal_name', '') or getattr(comp, 'name', '') or 'Company'
    logo = _img(getattr(comp, 'logo', None))
    head_left = [P(name, 14, True)]
    for line in ((getattr(prof, 'po_box', '') and f'P.O. Box {prof.po_box}'), getattr(prof, 'phone', ''), getattr(prof, 'email', ''), getattr(prof, 'website', ''),
                 (getattr(prof, 'tax_id', '') and f'TRN {prof.tax_id}')):
        if line:
            head_left.append(P(line, 8.5, color='#5b6170'))
    if getattr(prof, 'letterhead_header', ''):
        head_left.append(P(prof.letterhead_header, 8.5, color='#5b6170'))
    right = [P(kind.upper(), 18, True, '#0f4c81', 2), P(doc.number, 12, True, align=2)]
    if logo:
        right.insert(0, Image(logo, width=38 * mm, height=18 * mm, kind='proportional', hAlign='RIGHT'))
    t = Table([[head_left, right]], colWidths=[108 * mm, 70 * mm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, 0), 0.8, colors.HexColor('#0f4c81')), ('BOTTOMPADDING', (0, 0), (-1, 0), 8)]))
    story += [t, Spacer(1, 6 * mm)]

    cust = doc.account.name if doc.account_id else (doc.contact.name if doc.contact_id else '')
    cl = [P('To', 8, color='#5b6170'), P(cust, 11, True)]
    if doc.account_id:
        a = doc.account
        for line in (a.legal_name if a.legal_name and a.legal_name != a.name else '', a.billing_address, ' '.join(x for x in (a.city, a.country) if x), (a.tax_id and f'TRN {a.tax_id}')):
            if line:
                cl.append(P(line, 9))
    if doc.contact_id:
        c = doc.contact
        cl.append(P(f'Attn: {c.name}' + (f' · {c.job_title}' if c.job_title else ''), 9))
        cl.append(P(' · '.join(x for x in (c.email, c.phone) if x), 8.5, color='#5b6170'))
    meta = [('Date', str(doc.date)), ('Currency', doc.currency)]
    if isinstance(doc, Quotation):
        meta += [('Valid until', str(doc.valid_until or '')), ('Revision', str(doc.revision))]
    else:
        meta += [('Delivery', str(doc.delivery_date or '')), ('Quotation', doc.quotation.number if doc.quotation_id else '')]
    meta += [('Your ref.', doc.reference), ('Payment', doc.payment_term.name if doc.payment_term_id else ''), ('Sales', emp_name(doc.owner))]
    mt = Table([[P(k, 8, color='#5b6170'), P(v, 9)] for k, v in meta if v], colWidths=[22 * mm, 48 * mm])
    mt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 1), ('BOTTOMPADDING', (0, 0), (-1, -1), 1)]))
    t = Table([[cl, mt]], colWidths=[108 * mm, 70 * mm])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story += [t, Spacer(1, 6 * mm)]

    money = lambda v: f'{float(v or 0):,.2f}'  # noqa: E731
    rows = [[P('#', 8, True, '#ffffff'), P('Description', 8, True, '#ffffff'), P('Qty', 8, True, '#ffffff', 2), P('Unit', 8, True, '#ffffff'),
             P('Unit price', 8, True, '#ffffff', 2), P('Disc %', 8, True, '#ffffff', 2), P('Tax %', 8, True, '#ffffff', 2), P('Net', 8, True, '#ffffff', 2)]]
    for i, it in enumerate(doc.items or []):
        desc = it.get('product_label') or ''
        if it.get('description') and it['description'] != desc:
            desc = f'<b>{desc}</b><br/>{it["description"]}' if desc else it['description']
        rows.append([P(str(i + 1), 8.5), P(desc, 8.5), P(f'{float(it["qty"]):g}', 8.5, align=2), P(it.get('uom', ''), 8.5), P(money(it['unit_price']), 8.5, align=2),
                     P(f'{float(it.get("discount_pct") or 0):g}' if it.get('discount_pct') else '', 8.5, align=2), P(f'{float(it.get("tax_rate") or 0):g}', 8.5, align=2),
                     P(money(it['net']), 8.5, align=2)])
    lt = Table(rows, colWidths=[8 * mm, 72 * mm, 14 * mm, 14 * mm, 24 * mm, 14 * mm, 12 * mm, 20 * mm], repeatRows=1)
    lt.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0f4c81')), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6fb')]), ('LINEBELOW', (0, -1), (-1, -1), 0.6, colors.HexColor('#c9cfdd')),
                            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
    story += [lt, Spacer(1, 3 * mm)]
    tot = [('Subtotal', money(doc.subtotal))]
    if doc.discount_total:
        tot.append(('Discount' + (f' ({float(doc.discount_pct):g}% overall)' if doc.discount_pct else ''), '-' + money(doc.discount_total)))
    tot += [('Tax', money(doc.tax_total)), (f'Total {doc.currency}', money(doc.total))]
    tt = Table([[P(k, 9, k.startswith('Total'), align=2), P(v, 9, k.startswith('Total'), align=2)] for k, v in tot], colWidths=[50 * mm, 30 * mm], hAlign='RIGHT')
    tt.setStyle(TableStyle([('LINEABOVE', (0, -1), (-1, -1), 0.8, colors.HexColor('#0f4c81')), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
    story += [tt, Spacer(1, 6 * mm)]
    if doc.notes:
        story += [P('Notes', 8, True, '#5b6170'), P(doc.notes, 9), Spacer(1, 3 * mm)]
    if doc.terms:
        story += [P('Terms and conditions', 8, True, '#5b6170'), P(doc.terms, 8.5, color='#3a4050'), Spacer(1, 6 * mm)]
    sig = [P('For ' + name, 9, True)]
    stamp = _img(getattr(prof, 'stamp', None))
    sign = _img(getattr(prof, 'signature', None))
    imgs = [Image(x, width=30 * mm, height=18 * mm, kind='proportional', hAlign='LEFT') for x in (sign, stamp) if x]
    if imgs:
        sig.append(Table([imgs], hAlign='LEFT'))
    sig.append(P(getattr(prof, 'signatory_name', '') or emp_name(doc.owner), 9))
    if getattr(prof, 'signatory_designation', ''):
        sig.append(P(prof.signatory_designation, 8.5, color='#5b6170'))
    story.append(Table([[sig, [P('Accepted by the customer', 9, True), Spacer(1, 14 * mm), P('Name / signature / date', 8, color='#5b6170')]]], colWidths=[90 * mm, 88 * mm],
                       style=TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')])))
    if getattr(prof, 'letterhead_footer', ''):
        story += [Spacer(1, 6 * mm), P(prof.letterhead_footer, 7.5, color='#8a90a0', align=1)]
    pdf.build(story)
    return buf.getvalue()


class DocPdfView(Base):
    """GET /zeo-crm/api/quotation/<pk>/pdf/ or /zeo-crm/api/sales-order/<pk>/pdf/ – the user must be able to open the record."""
    kind = 'quotation'

    @guard
    def get(self, request, pk):
        r = get_reg(self.app, self.kind, request)
        obj = r.scope(request).filter(pk=pk).first()
        if obj is None:
            from django.http import Http404
            raise Http404
        data = build(obj, 'Quotation' if self.kind == 'quotation' else 'Sales order', self.app)
        resp = HttpResponse(data, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{obj.number}.pdf"'
        return resp
