import re
from decimal import Decimal
from django.db import transaction
from .models import PayrollRun, Payslip, PayslipComponent, EmployeeSalaryStructure, SalaryComponent
import calendar
import logging
from datetime import datetime
from calendar import monthrange


# Set up logging
logger = logging.getLogger(__name__)

def eval_formula(formula, components_dict):
    """Evaluate the payroll formula, defaulting missing components to 0."""
    import re
    # Extract variables from the formula
    variables = set(re.findall(r'[a-zA-Z_]+', formula))
    
    # Check for missing components
    missing_vars = [var for var in variables if var not in components_dict and var not in ['+', '-', '*', '/', '(', ')']]
    if missing_vars:
        logger.warning(f"Missing components in formula: {missing_vars}. Defaulting to 0.")
        for var in missing_vars:
            components_dict[var] = Decimal('0.00')
    
    # Replace component names with their values
    for name, value in components_dict.items():
        formula = formula.replace(name, str(value))
    
    try:
        return Decimal(str(eval(formula)))
    except Exception as e:
        raise ValueError(f"Error evaluating formula '{formula}': {e}")

# PayrollManagement/utils.py or PayrollManagement/pdf_utils.py
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from django.http import HttpResponse
from .models import Payslip, PayslipComponent
from calendars.models import EmployeeOvertime
import io
def generate_payslip_pdf(request, payslip):
    """Generate a PDF payslip for the given Payslip instance."""
    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(buffer, pagesize=letter)
    elements = []

    styles = getSampleStyleSheet()
    title_style = styles['Heading1']
    normal_style = styles['Normal']
    heading2_style = styles['Heading2']

    # Company and Payslip Header
    company_name = request.tenant.name if hasattr(request, 'tenant') else "Default Company Name"
    elements.append(Paragraph(f"Company Name: {company_name}", title_style))
    elements.append(Paragraph(f"Payslip for {payslip.employee.emp_first_name} {payslip.employee.emp_last_name or ''}", normal_style))
    payroll_period = f"{payslip.payroll_run.get_month_display()} {payslip.payroll_run.year}"
    elements.append(Paragraph(f"Payroll Period: {payroll_period}", normal_style))
    elements.append(Paragraph(f"Working Days: {payslip.days_worked}/{payslip.total_working_days}", normal_style))

    # Add overtime hours if applicable
    overtime_component = PayslipComponent.objects.filter(payslip=payslip, component__code='OT').first()
    if overtime_component:
        start_date = datetime(payslip.payroll_run.year, payslip.payroll_run.month, 1).date()
        _, last_day = monthrange(payslip.payroll_run.year, payslip.payroll_run.month)
        end_date = datetime(payslip.payroll_run.year, payslip.payroll_run.month, last_day).date()
        overtime_records = EmployeeOvertime.objects.filter(
            employee=payslip.employee,
            date__gte=start_date,
            date__lte=end_date,
            approved=True
        )
        total_overtime_hours = sum(record.hours for record in overtime_records)
        elements.append(Paragraph(f"Overtime Hours: {total_overtime_hours}", normal_style))
    elements.append(Spacer(1, 12))

    # Employee Details Table
    employee_details = [
        ["Employee Code", payslip.employee.emp_code],
        ["First Name", payslip.employee.emp_first_name],
        ["Last Name", payslip.employee.emp_last_name or "N/A"],
        ["Department", payslip.employee.emp_dept_id.dept_name if payslip.employee.emp_dept_id else "N/A"],
        ["Branch", payslip.employee.emp_branch_id.branch_name if payslip.employee.emp_branch_id else "N/A"],
        ["Designation", payslip.employee.emp_desgntn_id.desgntn_name if payslip.employee.emp_desgntn_id else "N/A"],
        ["Joining Date", payslip.employee.emp_joined_date.strftime("%Y-%m-%d")],
    ]
    employee_table = Table(employee_details, colWidths=[200, 200])
    employee_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(employee_table)
    elements.append(Spacer(1, 12))

    # Salary Components: Additions
    elements.append(Paragraph("Additions", heading2_style))
    additions_data = [["Component", "Original Amount", "Final Amount"]]
    additions_total = 0
    components = PayslipComponent.objects.filter(
        payslip=payslip,
        component__show_on_payslip=True,
        component__component_type='addition'
    )
    for component in components:
        original_amount = EmployeeSalaryStructure.objects.filter(
            employee=payslip.employee,
            component=component.component,
            is_active=True
        ).first()
        original = f"{original_amount.amount:.2f}" if original_amount else "N/A"
        if component.component.deduct_leave:
            additions_data.append([component.component.name, original, f"{component.amount:.2f}"])
        else:
            additions_data.append([component.component.name, f"{component.amount:.2f}", f"{component.amount:.2f}"])
        additions_total += component.amount
    additions_data.append(["Gross Salary", "", f"{payslip.gross_salary:.2f}"])
    additions_data.append(["Net Salary", "", f"{payslip.net_salary:.2f}"])

    additions_table = Table(additions_data, colWidths=[200, 100, 100])
    additions_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -3), colors.white),
        ('BACKGROUND', (0, -2), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, -2), (0, -1), 'Helvetica-Bold'),
    ]))
    elements.append(additions_table)
    elements.append(Spacer(1, 12))

    # Salary Components: Deductions
    elements.append(Paragraph("Deductions", heading2_style))
    deductions_data = [["Component", "Original Amount", "Final Amount"]]
    deductions_total = 0
    deductions = PayslipComponent.objects.filter(
        payslip=payslip,
        component__show_on_payslip=True,
        component__component_type='deduction'
    )
    for component in deductions:
        original_amount = EmployeeSalaryStructure.objects.filter(
            employee=payslip.employee,
            component=component.component,
            is_active=True
        ).first()
        original = f"{original_amount.amount:.2f}" if original_amount else "N/A"
        if component.component.deduct_leave:
            deductions_data.append([component.component.name, original, f"{component.amount:.2f}"])
        else:
            deductions_data.append([component.component.name, f"{component.amount:.2f}", f"{component.amount:.2f}"])
        deductions_total += component.amount
    deductions_data.append(["Gross Salary", "", f"{payslip.gross_salary:.2f}"])
    deductions_data.append(["Net Salary", "", f"{payslip.net_salary:.2f}"])

    deductions_table = Table(deductions_data, colWidths=[200, 100, 100])
    deductions_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -3), colors.white),
        ('BACKGROUND', (0, -2), (-1, -1), colors.lightgrey),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 1), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, -2), (0, -1), 'Helvetica-Bold'),
    ]))
    elements.append(deductions_table)
    elements.append(Spacer(1, 12))

    # Summary
    elements.append(Paragraph("Summary", heading2_style))
    summary_data = [
        ["Gross Salary", f"{payslip.gross_salary:.2f}"],
        ["Total Additions", f"{additions_total:.2f}"],
        ["Total Deductions", f"{deductions_total:.2f}"],
        ["Net Salary", f"{payslip.net_salary:.2f}"]
    ]
    summary_table = Table(summary_data, colWidths=[200, 200])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 12),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))

    elements.append(Paragraph("Generated by ZEO HRMS", normal_style))
    pdf.build(elements)

    buffer.seek(0)
    pdf_data = buffer.getvalue()
    buffer.close()

    filename = f"payslip_{payslip.employee.emp_code}_{payslip.payroll_run.year}{payslip.payroll_run.month:02d}.pdf"
    response = HttpResponse(pdf_data, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response

from django.core.mail import EmailMessage
from django.conf import settings
from EmpManagement .models import EmailConfiguration

def send_payslip_email(payslip):
    config = EmailConfiguration.objects.filter(is_active=True).first()
    if not config:
        logger.error("No active email configuration found.")
        return False

    employee_email = payslip.employee.emp_personal_email
    if not employee_email:
        logger.warning(f"Skipping email: Employee {payslip.employee.emp_code} has no personal email.")
        return False  # Skip without raising error

    if not payslip.payslip_pdf:
        logger.warning(f"Skipping email: No PDF attached for payslip ID {payslip.id}")
        return False

    try:
        email = EmailMessage(
            subject='Your Payslip',
            body='Please find attached your payslip.',
            from_email=config.email_host_user,
            to=[employee_email],
        )
        email.attach_file(payslip.payslip_pdf.path)
        email.send()
        logger.info(f"Payslip email sent to {employee_email} for employee {payslip.employee.emp_code}")
        return True

    except Exception as e:
        logger.exception(f"Failed to send payslip email to {employee_email}: {str(e)}")
        return False