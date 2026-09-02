import re
from decimal import Decimal
from django.db import transaction
from .models import PayrollRun, Payslip, PayslipComponent, EmployeeSalaryStructure, SalaryComponent
import calendar
import logging
from datetime import datetime
from calendar import monthrange
from django_tenants.utils import connection
from django.apps import apps
from django.db.models import Q
from simpleeval import SimpleEval, NameNotDefined, FunctionNotDefined
from OrganisationManager.models import GratuityTable


def get_gratuity_variables(
    employee,
    years_of_service,
    gratuity_type="resignation",
    basic_salary=Decimal("0.00")
):
    """
    Returns gratuity-related variables for:
    - Payroll monthly accrual
    - EOS settlement

    Uses GratuityTable dynamically.
    """

    years_of_service = Decimal(str(years_of_service))

    daily_wage = (
        basic_salary / Decimal("30")
        if basic_salary
        else Decimal("0.00")
    )

    # ------------------------------------------------
    # CALCULATE TOTAL GRATUITY DAYS BAND-WISE
    # ------------------------------------------------

    gratuity_days = Decimal("0.00")

    rules = GratuityTable.objects.filter(
        is_active=True
    ).order_by("minimum_value")

    current_rule = None

    for rule in rules:

        rule_min = Decimal(str(rule.minimum_value))
        rule_max = Decimal(str(rule.maximum_value))

        # find current rule
        if (
            years_of_service >= rule_min
            and years_of_service <= rule_max
        ):
            current_rule = rule

        # no service in this band
        if years_of_service <= rule_min:
            continue

        service_in_band = (
            min(years_of_service, rule_max)
            - rule_min
        )

        if service_in_band <= 0:
            continue

        if gratuity_type == "termination":
            days_per_year = Decimal(
                str(rule.termination_days)
            )
        else:
            days_per_year = Decimal(
                str(rule.resignation_days)
            )

        gratuity_days += (
            service_in_band
            * days_per_year
        )

    # ------------------------------------------------
    # CURRENT BAND DAYS (for monthly accrual formula)
    # ------------------------------------------------

    per_year_days = Decimal("0.00")

    if current_rule:

        if gratuity_type == "termination":
            per_year_days = Decimal(
                str(current_rule.termination_days)
            )
        else:
            per_year_days = Decimal(
                str(current_rule.resignation_days)
            )

    # ------------------------------------------------
    # MONTHLY GRATUITY ACCRUAL
    # ------------------------------------------------

    monthly_gratuity_accrual = (
        daily_wage
        * per_year_days
    ) / Decimal("12")

    # ------------------------------------------------
    # EOS GRATUITY LIABILITY
    # ------------------------------------------------

    total_gratuity_liability = (
        daily_wage
        * gratuity_days
    )

    max_gratuity = (
        basic_salary
        * Decimal("24")
    )

    if total_gratuity_liability > max_gratuity:
        total_gratuity_liability = max_gratuity

    return {
        "daily_wage": daily_wage,
        "per_year_days": per_year_days,
        "gratuity_days": gratuity_days,
        "monthly_gratuity_accrual": monthly_gratuity_accrual,
        "total_gratuity_liability": total_gratuity_liability,
        "max_gratuity": max_gratuity,
    }
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
    
def evaluate_formula(formula, variables, employee, component):
    try:
        logger.debug(
            f"Evaluating formula: {formula} with variables: {variables} for employee: {employee}"
        )
        formula = formula.strip("'")

        # 🔑 Convert all numbers into Decimal("...")
        formula = re.sub(r'(\d+\.\d+|\d+)', r'Decimal("\1")', formula)

        s = SimpleEval()
        s.names = variables
        s.functions = {"Decimal": Decimal}  # allow Decimal inside eval

        # ✅ Custom operators
        s.operators.update({
            '<': lambda x, y: x < y,
            '>': lambda x, y: x > y,
            '>=': lambda x, y: x >= y,
            '<=': lambda x, y: x <= y,
            '==': lambda x, y: x == y,
            '!=': lambda x, y: x != y,
            'and': lambda x, y: x and y,
            'or': lambda x, y: x or y,
            'not': lambda x: not x,
            '+': lambda x, y: x + y,
            '-': lambda x, y: x - y,
            '*': lambda x, y: x * y,
            '/': lambda x, y: x / y,
            '%': lambda x, y: x % y,
        })

        # ✅ Extended IF (works like CASE WHEN)
        def IF(*args):
            """
            Supports:
            - IF(cond, true_val, false_val)   → normal
            - IF(cond1, val1, cond2, val2, ..., default_val) → CASE-like
            """
            n = len(args)
            if n < 3:
                raise ValueError("Invalid IF usage")
            # Pairwise check (cond, val)
            for i in range(0, n - 1, 2):
                if args[i]:
                    return args[i+1]
            return args[-1]  # default

        # ✅ Custom functions
        s.functions.update({
            "MAX": max,
            "MIN": min,
            "AVG": lambda *args: sum(args) / len(args) if args else Decimal("0.00"),
            "SUM": sum,
            "ROUND": lambda val, ndigits=2: val.quantize(Decimal("1." + "0"*ndigits)) 
                if isinstance(val, Decimal) else round(val, ndigits),
            "IF": IF,
        })

        result = s.eval(formula)

        # Ensure result is Decimal
        if not isinstance(result, Decimal):
            result = Decimal(str(result))

        return result.quantize(Decimal("0.00"))

    except (NameNotDefined, FunctionNotDefined) as e:
        logger.error(
            f"Invalid variable or function in formula '{formula}' for employee {employee}: {e}"
        )
        return Decimal("0.00")
    except Exception as e:
        logger.error(
            f"Error evaluating formula '{formula}' for employee {employee}: {e}"
        )
        return Decimal("0.00")

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

def schedule_escalation(approval, level_rule):
    from .tasks import advance_salary_escalate_approval_task
    """
    Schedule a Celery countdown task for automatic escalation.
    """
    total_seconds = (
        (level_rule.escalate_after_days or 0) * 86400 +
        (level_rule.escalate_after_hours or 0) * 3600 +
        (level_rule.escalate_after_minutes or 0) * 60
    )

    if total_seconds > 0 and level_rule.escalate_to:
        schema_name = connection.schema_name
        advance_salary_escalate_approval_task.apply_async((approval.id, schema_name), countdown=total_seconds)
        print(f"🕒 Escalation task scheduled for approval {approval.id} after {total_seconds} seconds.")
def loan_schedule_escalation(approval, level_rule):
    from .tasks import loan_escalate_approval_task
    """
    Schedule a Celery countdown task for automatic escalation.
    """
    total_seconds = (
        (level_rule.escalate_after_days or 0) * 86400 +
        (level_rule.escalate_after_hours or 0) * 3600 +
        (level_rule.escalate_after_minutes or 0) * 60
    )

    if total_seconds > 0 and level_rule.escalate_to:
        schema_name = connection.schema_name
        loan_escalate_approval_task.apply_async((approval.id, schema_name), countdown=total_seconds)
        print(f"🕒 Escalation task scheduled for approval {approval.id} after {total_seconds} seconds.")
def airticket_schedule_escalation(approval, level_rule):
    from .tasks import airticket_escalate_approval_task
    """
    Schedule a Celery countdown task for automatic escalation.
    """
    total_seconds = (
        (level_rule.escalate_after_days or 0) * 86400 +
        (level_rule.escalate_after_hours or 0) * 3600 +
        (level_rule.escalate_after_minutes or 0) * 60
    )

    if total_seconds > 0 and level_rule.escalate_to:
        schema_name = connection.schema_name
        airticket_escalate_approval_task.apply_async((approval.id, schema_name), countdown=total_seconds)
        print(f"🕒 Escalation task scheduled for approval {approval.id} after {total_seconds} seconds.")

def get_ot_rate(employee, ot_type):
    OvertimePolicy = apps.get_model(
        'calendars', 'OvertimePolicy'
    )

    qs = OvertimePolicy.objects.filter(
        ot_type=ot_type,
        is_active=True
    ).filter(
        Q(branch__isnull=True) | Q(branch=employee.emp_branch_id),
        Q(department__isnull=True) | Q(department=employee.emp_dept_id),
        Q(designation__isnull=True) | Q(designation=employee.emp_desgntn_id),
        Q(category__isnull=True) | Q(category=employee.emp_ctgry_id),
    )

    policy = qs.first()
    return policy.rate_multiplier if policy else Decimal('0.00')

def get_unused_annual_leave_balance(employee, as_of_date):
    from calendars .models import emp_leave_balance
    from decimal import Decimal
    from django.db.models import Sum
    """
    Returns the employee's unused annual leave balance.

    Uses emp_leave_balance.balance for the leave type
    whose leave_category is 'annual'.
    """

    balance = (
        emp_leave_balance.objects
        .filter(
            employee=employee,
            leave_type__leave_category="annual"
        )
        .aggregate(
            total_balance=Sum("balance")
        )
    )

    return Decimal(
        str(balance["total_balance"] or 0)
    )
def get_employee_benefit_liability(employee, as_of_date):
    """
    Returns benefit liability details for one employee.

    Includes:
        - Gratuity liability
        - Unused annual leave liability
    """

    result = {}

    joining_date = employee.emp_joined_date

    service_days = (
        as_of_date - joining_date
    ).days

    years_of_service = (
        Decimal(str(service_days))
        / Decimal("365")
    )

    # --------------------------------
    # Salary Components
    # --------------------------------

    salary_structures = (
        EmployeeSalaryStructure.objects.filter(
            employee=employee,
            is_active=True
        )
        .select_related("component")
    )

    component_values = {}

    total_salary = Decimal("0.00")
    basic_salary = Decimal("0.00")

    for structure in salary_structures:

        amount = Decimal(
            str(structure.amount or 0)
        )

        component_values[
            structure.component.name
        ] = amount

        total_salary += amount

        if (
            structure.component.payroll_category
            == "basic"
        ):
            basic_salary = amount

    # --------------------------------
    # Gratuity
    # --------------------------------

    gratuity_data = get_gratuity_variables(
        employee=employee,
        years_of_service=years_of_service,
        gratuity_type="resignation",
        basic_salary=basic_salary
    )

    # --------------------------------
    # Gratuity Bands
    # --------------------------------

    from OrganisationManager.models import GratuityTable

    gratuity_bands = []

    rules = (
        GratuityTable.objects.filter(
            is_active=True
        )
        .order_by("minimum_value")
    )

    for rule in rules:

        minimum = Decimal(
            str(rule.minimum_value)
        )

        maximum = Decimal(
            str(rule.maximum_value)
        )

        if years_of_service <= minimum:
            continue

        service_in_band = (
            min(years_of_service, maximum)
            - minimum
        )

        if service_in_band <= 0:
            continue

        gratuity_bands.append({
            "from_year": minimum,
            "to_year": maximum,
            "service_years": service_in_band,
            "days_per_year": rule.resignation_days
        })

    # --------------------------------
    # Leave Salary Accrued
    # --------------------------------

    unused_annual_leave_days = (
        get_unused_annual_leave_balance(
            employee=employee,
            as_of_date=as_of_date
        )
    )
    # --------------------------------
    # Dynamic Leave Salary Calculation
    # --------------------------------
    from .models import SalaryComponent, PayStructure

    leave_component = SalaryComponent.objects.filter(
        payroll_category='leave_encashment', 
        branch=employee.emp_branch_id
    ).first()

    pay_structure = PayStructure.objects.filter(branch=employee.emp_branch_id).first()
    fixed_days = Decimal(str(pay_structure.fixed_working_days)) if pay_structure and pay_structure.fixed_working_days else Decimal("30")
    calendar_days = Decimal("30")  # Default to 30, could be updated based on calendar month

    if leave_component and leave_component.formula:
        variables = {
            "basic_salary": basic_salary,
            "total_salary": total_salary,
            "encashment_days": unused_annual_leave_days,
            "leave_balance": unused_annual_leave_days,
            "fixed_days": fixed_days,
            "calendar_days": calendar_days,
        }
        try:
            leave_salary_accrued = evaluate_formula(
                leave_component.formula, 
                variables, 
                employee, 
                leave_component
            )
        except Exception as e:
            logger.error(f"Error evaluating leave encashment formula: {e}")
            leave_salary_accrued = unused_annual_leave_days * (basic_salary / Decimal("30"))
    else:
        ##
        leave_salary_accrued = (
            unused_annual_leave_days
            * (
                basic_salary
                / Decimal("30")
            )
        )

        leave_salary_accrued = (
            leave_salary_accrued.quantize(
                Decimal("0.01")
            )
        )

    # --------------------------------
    # Result
    # --------------------------------

    result.update({

        "employee_id":
            employee.id,

        "employee_code":
            employee.emp_code,

        "employee_name":
            f"{employee.emp_first_name} "
            f"{employee.emp_last_name or ''}",

        "joining_date":
            joining_date,

        "as_of_date":
            as_of_date,

        "service_days":
            service_days,

        "years_of_service":
            years_of_service.quantize(
                Decimal("0.01")
            ),

        "basic_salary":
            basic_salary,

        "total_salary":
            total_salary,

        "salary_components":
            component_values,

        # Gratuity
        "gratuity_days":
            gratuity_data[
                "gratuity_days"
            ],

        "gratuity_amount":
            gratuity_data[
                "total_gratuity_liability"
            ],

        "gratuity_bands":
            gratuity_bands,

        # Leave Salary
        "unused_annual_leave_days":
            unused_annual_leave_days,

        "leave_salary_accrued":
            leave_salary_accrued,
    })

    return result