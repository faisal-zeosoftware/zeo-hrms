"""Printable permit to work (A4) with a QR code that opens the live permit – the paper copy at the work place is
checked against the system, so a closed or suspended permit cannot be used from an old print."""
from io import BytesIO

from django.utils import timezone
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import services as S


class QR(Flowable):
    def __init__(self, text, size=30 * mm):
        super().__init__()
        self.text, self.size = text, size

    def wrap(self, *a):
        return self.size, self.size

    def draw(self):
        w = qr.QrCodeWidget(self.text)
        x1, y1, x2, y2 = w.getBounds()
        d = Drawing(self.size, self.size, transform=[self.size / (x2 - x1), 0, 0, self.size / (y2 - y1), 0, 0])
        d.add(w)
        renderPDF.draw(d, self.canv, 0, 0)


def _t(v):
    if not v:
        return '-'
    return timezone.localtime(v).strftime('%d %b %Y %H:%M') if hasattr(v, 'hour') else v.strftime('%d %b %Y')


def link_for(p, request):
    base = (request.META.get('HTTP_ORIGIN') or '') if request else ''
    if not base and request is not None:
        base = request.build_absolute_uri('/').rstrip('/')
    return f'{base}{S.PAGE}permits/{p.pk}'


def permit_pdf(p, request=None):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=p.number)
    ss = getSampleStyleSheet()
    small = ParagraphStyle('s', parent=ss['Normal'], fontSize=8.5, leading=11)
    head = ParagraphStyle('h', parent=ss['Normal'], fontSize=10, leading=13, fontName='Helvetica-Bold', spaceBefore=6, spaceAfter=3)
    col = colors.HexColor(p.permit_type.color or '#5b4fe0')
    P = lambda x, st=small: Paragraph(str(x or '-').replace('\n', '<br/>'), st)
    story = []
    title = Table([[Paragraph(f'<font size=15><b>{p.permit_type.name}</b></font><br/><font size=10>{p.number} · '
                              f'{p.get_status_display()}</font>', ParagraphStyle('t', textColor=colors.white, leading=18)),
                    QR(link_for(p, request), 26 * mm)]], colWidths=[150 * mm, 32 * mm])
    title.setStyle(TableStyle([('BACKGROUND', (0, 0), (0, 0), col), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LEFTPADDING', (0, 0), (0, 0), 8), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    story += [title, P('Scan the code to check the live status before starting work. This print is valid only while the system shows '
                       '"Issued – work in progress".', ParagraphStyle('w', parent=small, textColor=colors.HexColor('#b91c1c'))), Spacer(1, 4)]
    grid = [
        ['Site / area', P(f'{p.site.name}{" / " + p.area.name if p.area_id else ""}'), 'Exact location', P(p.location)],
        ['Valid from', _t(p.valid_from), 'Valid to', _t(p.valid_to)],
        ['Applicant', S.user_name(p.applicant), 'Supervisor in charge', S.emp_name(p.receiver) if p.receiver_id else '-'],
        ['Contractor', P(p.contractor.name if p.contractor_id else 'Own staff'), 'Crew size', str(p.crew_size)],
        ['Equipment', P(p.equipment), 'Risk assessment', p.risk_assessment.number if p.risk_assessment_id else '-'],
        ['Issued', f'{_t(p.issued_at)} · {S.user_name(p.issued_by)}' if p.issued_at else '-', 'Team briefed', 'Yes' if p.team_briefed else 'No'],
    ]
    t = Table(grid, colWidths=[30 * mm, 61 * mm, 32 * mm, 59 * mm])
    t.setStyle(TableStyle([('FONTSIZE', (0, 0), (-1, -1), 8.5), ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                           ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'), ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
                           ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')), ('BACKGROUND', (2, 0), (2, -1), colors.HexColor('#f1f5f9')),
                           ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story += [Paragraph('Work', head), P(p.description), Spacer(1, 4), t]
    if p.precautions:
        story += [Paragraph('Conditions and precautions', head), P(p.precautions)]
    if p.method_statement:
        story += [Paragraph('Method statement', head), P(p.method_statement[:3000])]
    pre = S.prerequisites(p)
    story += [Paragraph('Checks before issue', head)]
    pt = Table([[('✔' if x['ok'] else '✘'), P(x['label']), P(x['detail'])] for x in pre] or [['-', '', '']],
               colWidths=[8 * mm, 90 * mm, 84 * mm])
    pt.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('VALIGN', (0, 0), (-1, -1), 'TOP')]
                           + [('TEXTCOLOR', (0, i), (0, i), colors.HexColor('#15803d' if x['ok'] else '#b91c1c')) for i, x in enumerate(pre)]))
    story.append(pt)
    if p.permit_type.checklist_id:
        ans = p.checklist_answers or {}
        rows = [['Checklist', 'Answer', 'Note']] + [[P(('★ ' if q.get('critical') else '') + q['text']), str((ans.get(q['key']) or {}).get('value', '') or '-'),
                                                     P((ans.get(q['key']) or {}).get('note', ''))] for q in p.permit_type.checklist.questions or []]
        ct = Table(rows, colWidths=[110 * mm, 18 * mm, 54 * mm], repeatRows=1)
        ct.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0')), ('VALIGN', (0, 0), (-1, -1), 'TOP')]))
        story += [Paragraph(p.permit_type.checklist.name + ' (★ = critical)', head), ct]
    gts = list(p.gas_tests.all()[:8])
    if gts:
        g = Table([['Time', 'O₂ %', 'LEL %', 'H₂S ppm', 'CO ppm', 'Result', 'Tester']] +
                  [[_t(x.tested_at), x.o2 if x.o2 is not None else '-', x.lel if x.lel is not None else '-', x.h2s if x.h2s is not None else '-',
                    x.co if x.co is not None else '-', 'Pass' if x.passed else 'FAIL', S.user_name(x.tester)] for x in gts])
        g.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                               ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0'))]))
        story += [Paragraph('Gas tests', head), g]
    isos = list(p.isolations.all())
    if isos:
        it = Table([['Point', 'Type', 'Method', 'Lock', 'Isolated by', 'Verified by']] +
                   [[P(i.point), i.get_kind_display(), P(i.method), i.lock_no or '-', S.user_name(i.isolated_by), S.user_name(i.verified_by) or '-']
                    for i in isos], colWidths=[40 * mm, 24 * mm, 38 * mm, 18 * mm, 31 * mm, 31 * mm])
        it.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0'))]))
        story += [Paragraph('Isolations', head), it]
    from .models import ApprovalStep
    sts = ApprovalStep.objects.filter(model='permit', object_id=p.pk).order_by('sequence')
    if sts:
        at = Table([['Approval', 'Decision', 'By', 'When']] + [[x.label, x.status.capitalize(), S.user_name(x.decided_by) or '-', _t(x.decided_at)]
                                                               for x in sts], colWidths=[62 * mm, 26 * mm, 50 * mm, 44 * mm])
        at.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.3, colors.lightgrey), ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e2e8f0'))]))
        story += [Paragraph('Approvals', head), at]
    sig = Table([['Hand-back: work complete, people out, tools removed, area safe', ''], ['Receiver signature / time', 'Issuer signature / time']],
                colWidths=[91 * mm, 91 * mm], rowHeights=[8 * mm, 16 * mm])
    sig.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.4, colors.grey), ('FONTSIZE', (0, 0), (-1, -1), 8.5), ('SPAN', (0, 0), (1, 0)),
                             ('VALIGN', (0, 1), (-1, -1), 'TOP')]))
    story += [Spacer(1, 6), sig]
    doc.build(story)
    return buf.getvalue()
