# notifications/utils.py

from django.utils.html import strip_tags
from django.template import Template, Context
from django.core.mail import EmailMultiAlternatives, get_connection
from django.conf import settings
import logging
from django_tenants.utils import connection





def get_employee_context(employee):
    """
    Builds a dictionary of employee attributes to be used in email templates.
    """
    return {
        'emp_first_name': employee.emp_first_name,
        'emp_gender': employee.emp_gender,
        'emp_date_of_birth': employee.emp_date_of_birth,
        'emp_personal_email': employee.emp_personal_email,
        'emp_company_email': employee.emp_company_email,
        'emp_branch_name': employee.emp_branch_id,
        'emp_department_name': employee.emp_dept_id,
        'emp_designation_name': employee.emp_desgntn_id,
        'emp_joined_date': getattr(employee, 'emp_joined_date', None),
    }

logger = logging.getLogger(__name__)

def send_notification_email(
    
    *,
    user=None,
    employee=None,
    message="",
    template_type="",
    context=None,
    email_template_model=None,
    notification_model=None,
    branch=None,
    notification_type="general",
    title="",

    
):
    """
    Generic utility to send email and create in-app notification.
    """
    if context is None:
        context = {}

    if not email_template_model or not notification_model:
        return {"status": "error", "message": "Template and Notification models are required."}

    try:
        # Create notification
        created_notification = notification_model.objects.create(
            recipient_user=user,
            recipient_employee=employee,
            message=message
        )
    except Exception as e:
        logger.warning(f"Notification creation failed: {e}")
    #usernotification tenant wise
    try:

        if user and created_notification:

            from UserManagement.models import UserNotificationInbox

            current_schema = db_connection.schema_name

            with schema_context('public'):

                UserNotificationInbox.objects.create(
                    user=user,

                    branch_id=branch.id if branch else None,

                    branch_name=branch.branch_name if branch else None,

                    schema_name=current_schema,

                    notification_type=notification_type,

                    title=title,

                    message=message,

                    source_model=notification_model.__name__,

                    source_id=created_notification.id
                )

    except Exception as e:

        logger.warning(f"Global inbox creation failed: {e}")
    #email setup
    try:
        email_template = email_template_model.objects.get(template_type=template_type)
    except email_template_model.DoesNotExist:
        return {"status": "warning", "message": f"No template found for '{template_type}'."}
    except email_template_model.MultipleObjectsReturned:
        return {"status": "error", "message": f"Multiple templates found for '{template_type}'."}

    subject = email_template.subject
    template = Template(email_template.body)
    recipient_name = user.username if user else (employee.emp_first_name if employee else "")
    context.update({'recipient_name': recipient_name})
    html_message = template.render(Context(context))
    plain_message = strip_tags(html_message)

    try:
        from .models import EmailConfiguration  # update if needed
        email_config = EmailConfiguration.objects.get(is_active=True)
        default_email = email_config.email_host_user
        connection = get_connection(
            host=email_config.email_host,
            port=email_config.email_port,
            username=email_config.email_host_user,
            password=email_config.email_host_password,
            use_tls=email_config.email_use_tls,
        )
    except Exception as e:
        logger.warning(f"Using fallback email config: {e}")
        default_email = settings.EMAIL_HOST_USER
        connection = get_connection(
            host=settings.EMAIL_HOST,
            port=settings.EMAIL_PORT,
            username=settings.EMAIL_HOST_USER,
            password=settings.EMAIL_HOST_PASSWORD,
            use_tls=settings.EMAIL_USE_TLS,
        )

    to_email = user.email if user and user.email else (
        employee.emp_personal_email if employee and employee.emp_personal_email else None
    )

    if to_email:
        try:
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=default_email,
                to=[to_email],
                connection=connection,
            )
            email.attach_alternative(html_message, "text/html")
            email.send(fail_silently=False)
            return {"status": "success", "message": f"Email sent to {to_email}"}
        except Exception as e:
            logger.error(f"Email sending failed: {e}")
            return {"status": "error", "message": str(e)}

    return {"status": "error", "message": "No recipient email found."}

from decimal import Decimal
from django.db.models import Q

# def get_final_salary(employee, last_working_date):
#             from PayrollManagement.models import Payslip
#             payslip = Payslip.objects.filter(
#                 employee=employee,
#                 payroll_run__month=last_working_date.month,
#                 payroll_run__year=last_working_date.year,
#                 confirm_status=True,
#                 status__in=['paid', 'Approved']
#             ).order_by('-created_at').first()

#             return payslip.net_salary if payslip else Decimal('0.00')
def calculate_progressive_gratuity(years_of_service, daily_wage, termination_type):
    from OrganisationManager.models import GratuityTable

    total_gratuity_days = Decimal('0.00')
    remaining_years = Decimal(str(years_of_service))

    rules = GratuityTable.objects.filter(
        is_active=True
    ).order_by('minimum_value')

    for rule in rules:
        min_year = Decimal(rule.minimum_value)

        if remaining_years <= min_year:
            continue

        if rule.maximum_value:
            max_year = Decimal(rule.maximum_value)
            slab_years = min(remaining_years, max_year) - min_year
        else:
            slab_years = remaining_years - min_year

        if slab_years <= 0:
            continue

        if termination_type in ['termination', 'retirement', 'death_or_disablement']:
            per_year_days = Decimal(str(rule.termination_days))
        else:
            per_year_days = Decimal(str(rule.resignation_days))

        total_gratuity_days += slab_years * per_year_days

    gratuity_amount = total_gratuity_days * daily_wage

    return (
        total_gratuity_days.quantize(Decimal('0.01')),
        gratuity_amount.quantize(Decimal('0.01'))
    )
def get_final_salary(employee, last_working_date):
    from PayrollManagement.models import Payslip
    s=payslip = Payslip.objects.filter(
        employee=employee,
        payroll_run__payment_date__lte=last_working_date,
        confirm_status=True,
        status__in=['paid', 'Approved']
    ).order_by('-payroll_run').first()
    print("pa",s)
    return payslip.net_salary if payslip else Decimal('0.00')
def calculate_settlement(eos):
    from PayrollManagement.models import EmployeeSalaryStructure
    from OrganisationManager.models import GratuityTable
    from PayrollManagement.models import AirTicketAllocation

    try:
        resignation = eos.resignation
        employee = resignation.employee

        start_date = employee.emp_joined_date
        end_date = resignation.last_working_date

        # -------------------------------
        # SERVICE CALCULATION
        # -------------------------------
        total_days = (end_date - start_date).days
        eos.total_service_days = total_days
        eos.years_of_service = total_days / 365
        eos.net_number_of_days_worked = total_days - eos.leave_days_without_pay
        eos.date_of_joining = start_date
        eos.date_of_resignation_termination = resignation.resigned_on
        eos.last_working_date = end_date
        eos.notice_period_days = resignation.notice_period or 0

        # -------------------------------
        # GET BASIC SALARY
        # -------------------------------
        salary_component = EmployeeSalaryStructure.objects.filter(
            employee=employee,
            component__is_gratuity=True,
            is_active=True
        ).order_by('-date_updated').first()

        if salary_component and salary_component.amount:
            basic_salary = Decimal(salary_component.amount)
        else:
            # fallback to payslip basic
            payslip = employee.payslips.filter(status='Approved').order_by('-created_at').first()
            if payslip:
                basic_comp = payslip.components.filter(
                    component__name__iexact='basic'
                ).first()
                basic_salary = Decimal(basic_comp.amount) if basic_comp else Decimal('0.00')
            else:
                basic_salary = Decimal('0.00')

        eos.last_month_salary = basic_salary
        daily_wage = basic_salary / Decimal('30')

        # -------------------------------
        # GRATUITY RULE
        # -------------------------------
        net_days = eos.total_service_days - (eos.leave_days_without_pay or 0)
        years = Decimal(net_days) / Decimal('365')

        gratuity_days, gratuity_amount = calculate_progressive_gratuity(
            years_of_service=years,
            daily_wage=daily_wage,
            termination_type=resignation.termination_type
        )

        eos.gratuity_days = gratuity_days
        eos.gratuity_amount = gratuity_amount

        max_gratuity = basic_salary * Decimal('24')
        if eos.gratuity_amount > max_gratuity:
            eos.gratuity_amount = max_gratuity
        # -------------------------------
        # NOTICE PAY
        # -------------------------------
        if eos.notice_period_days:
            eos.notice_pay = daily_wage * Decimal(eos.notice_period_days)
        else:
            eos.notice_pay = Decimal('0.00')

        # -------------------------------
        # AIR TICKET
        # -------------------------------
        ticket = AirTicketAllocation.objects.filter(
            employee=employee,
            status='APPROVED',
            is_active=True
        ).order_by('-allocated_date').first()

        eos.air_ticket = ticket.amount if ticket else Decimal('0.00')

        eos.save()
        eos.refresh_from_db()

    except Exception as e:
        logger.error(
            f"Error in calculate_settlement for employee "
            f"{eos.resignation.employee.emp_code}: {str(e)}"
        )
        raise

def schedule_escalation(approval, level_rule):
    from .tasks import escalate_approval_task
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
        escalate_approval_task.apply_async((approval.id, schema_name), countdown=total_seconds)
        print(f"🕒 Escalation task scheduled for approval {approval.id} after {total_seconds} seconds.")
