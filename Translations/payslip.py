"""v1.15.0 – payslip PDF in the reader's language (not English). The English payslip stays in
PayrollManagement.utils.generate_payslip_pdf, unchanged; this is the same content with translated labels, translated master
names (department, designation, branch, salary components), the embedded Arabic font and a right-to-left layout."""
import io
from calendar import monthrange
from datetime import datetime

from django.http import HttpResponse

from .i18n import is_rtl, tr
from .pdf import fonts, para_text, shape
from .services import translated

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


def payslip_pdf_bytes(request, payslip, lang):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from PayrollManagement.models import EmployeeSalaryStructure, PayslipComponent

    rtl = is_rtl(lang)
    reg, bold = fonts()
    L = lambda s, *a: tr(s, *a, lang=lang)  # noqa: E731
    S = lambda s: shape(s, rtl)  # noqa: E731
    align = TA_RIGHT if rtl else TA_LEFT
    base = getSampleStyleSheet()
    title_style = ParagraphStyle('zt', parent=base['Heading1'], fontName=bold, alignment=align, leading=28)
    normal_style = ParagraphStyle('zn', parent=base['Normal'], fontName=reg, alignment=align, leading=16)
    heading2_style = ParagraphStyle('zh', parent=base['Heading2'], fontName=bold, alignment=align, leading=22)

    def row(*cells):
        cells = [S(c) for c in cells]
        return list(reversed(cells)) if rtl else cells

    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=letter, title=L('Payslip'), lang=lang)
    elements = []
    emp = payslip.employee
    run = payslip.payroll_run
    company_name = request.tenant.name if hasattr(request, 'tenant') else 'Default Company Name'
    full = f"{emp.emp_first_name} {emp.emp_last_name or ''}".strip()
    period = f"{L(MONTHS[run.month - 1]) if 1 <= run.month <= 12 else run.month} {run.year}"
    elements.append(Paragraph(para_text(L('Company Name: {0}', company_name), rtl), title_style))
    elements.append(Paragraph(para_text(L('Payslip for {0}', full), rtl), normal_style))
    elements.append(Paragraph(para_text(L('Payroll Period: {0}', period), rtl), normal_style))
    elements.append(Paragraph(para_text(L('Working Days: {0}/{1}', payslip.days_worked, payslip.total_working_days), rtl), normal_style))
    ot = PayslipComponent.objects.filter(payslip=payslip, component__code='OT').first()
    if ot:
        from calendars.models import EmployeeOvertime
        start = datetime(run.year, run.month, 1).date()
        end = datetime(run.year, run.month, monthrange(run.year, run.month)[1]).date()
        hours = sum(r.hours for r in EmployeeOvertime.objects.filter(employee=emp, date__gte=start, date__lte=end, approved=True))
        elements.append(Paragraph(para_text(L('Overtime Hours: {0}', hours), rtl), normal_style))
    elements.append(Spacer(1, 12))

    na = L('N/A')
    dept = translated(emp.emp_dept_id, 'dept_name', lang) if emp.emp_dept_id_id else na
    br = translated(emp.emp_branch_id, 'branch_name', lang) if emp.emp_branch_id_id else na
    des = translated(emp.emp_desgntn_id, 'desgntn_job_title', lang) if emp.emp_desgntn_id_id else na
    details = [
        row(L('Employee Code'), emp.emp_code), row(L('First Name'), emp.emp_first_name), row(L('Last Name'), emp.emp_last_name or na),
        row(L('Department'), dept), row(L('Branch'), br), row(L('Designation'), des),
        row(L('Joining Date'), emp.emp_joined_date.strftime('%Y-%m-%d') if emp.emp_joined_date else na),
    ]
    lab = -1 if rtl else 0  # column of the labels
    t = Table(details, colWidths=[200, 200])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), reg), ('BACKGROUND', (0, 0), (-1, 0), colors.grey), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'RIGHT' if rtl else 'LEFT'), ('FONTNAME', (0, 0), (-1, 0), bold), ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12), ('BACKGROUND', (0, 1), (-1, -1), colors.beige), ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 12))

    def comp_table(kind, heading):
        elements.append(Paragraph(para_text(L(heading), rtl), heading2_style))
        data = [row(L('Component'), L('Original Amount'), L('Final Amount'))]
        total = 0
        for c in PayslipComponent.objects.filter(payslip=payslip, component__show_in_payslip=True, component__component_type=kind).select_related('component'):
            orig = EmployeeSalaryStructure.objects.filter(employee=emp, component=c.component, is_active=True).first()
            name = translated(c.component, 'name', lang)
            if c.component.deduct_leave:
                data.append(row(name, f'{orig.amount:.2f}' if orig else na, f'{c.amount:.2f}'))
            else:
                data.append(row(name, f'{c.amount:.2f}', f'{c.amount:.2f}'))
            total += c.amount
        data.append(row(L('Gross Salary'), '', f'{payslip.gross_salary:.2f}'))
        data.append(row(L('Net Salary'), '', f'{payslip.net_salary:.2f}'))
        tb = Table(data, colWidths=[100, 100, 200] if rtl else [200, 100, 100])
        num_cols = ((0, 1), (1, -1)) if rtl else ((1, 1), (-1, -1))
        tb.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), reg), ('BACKGROUND', (0, 0), (-1, 0), colors.grey), ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT' if rtl else 'LEFT'), ('FONTNAME', (0, 0), (-1, 0), bold), ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12), ('BACKGROUND', (0, 1), (-1, -3), colors.white), ('BACKGROUND', (0, -2), (-1, -1), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 1, colors.black), ('ALIGN', num_cols[0], num_cols[1], 'RIGHT'),
            ('FONTNAME', (lab, -2), (lab, -1), bold),
        ]))
        elements.append(tb)
        elements.append(Spacer(1, 12))
        return total

    add_total = comp_table('addition', 'Additions')
    ded_total = comp_table('deduction', 'Deductions')
    elements.append(Paragraph(para_text(L('Summary'), rtl), heading2_style))
    summary = [row(L('Gross Salary'), f'{payslip.gross_salary:.2f}'), row(L('Total Additions'), f'{add_total:.2f}'),
               row(L('Total Deductions'), f'{ded_total:.2f}'), row(L('Net Salary'), f'{payslip.net_salary:.2f}')]
    st = Table(summary, colWidths=[200, 200])
    st.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), reg), ('BACKGROUND', (0, 0), (-1, -1), colors.white), ('ALIGN', (0, 0), (-1, -1), 'RIGHT' if rtl else 'LEFT'),
        ('FONTNAME', (lab, 0), (lab, -1), bold), ('FONTSIZE', (0, 0), (-1, -1), 12), ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(st)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(para_text(L('Generated by ZEO HRMS'), rtl), normal_style))
    pdf.build(elements)
    return buffer.getvalue()


def payslip_pdf_response(request, payslip, lang):
    data = payslip_pdf_bytes(request, payslip, lang)
    run = payslip.payroll_run
    resp = HttpResponse(data, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="payslip_{payslip.employee.emp_code}_{run.year}{run.month:02d}.pdf"'
    resp['Content-Language'] = lang
    return resp
