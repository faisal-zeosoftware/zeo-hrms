# notifications/utils.py

from django.utils.html import strip_tags
from django.template import Template, Context
from django.core.mail import EmailMultiAlternatives, get_connection
from django.conf import settings
import logging

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
    notification_model=None
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
        notification_model.objects.create(
            recipient_user=user,
            recipient_employee=employee,
            message=message
        )
    except Exception as e:
        logger.warning(f"Notification creation failed: {e}")

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


def calculate_settlement(eos):
    from PayrollManagement.models import EmployeeSalaryStructure
    from OrganisationManager.models import GratuityTable

    try:
        resignation = eos.resignation
        employee = resignation.employee
        start_date = employee.emp_joined_date
        end_date = resignation.last_working_date

        # Recalculate years_of_service for consistency
        total_days = (end_date - start_date).days
        eos.years_of_service = total_days / 365.0
        eos.total_service_days = total_days
        eos.net_number_of_days_worked = total_days - eos.leave_days_without_pay
        eos.date_of_joining = start_date
        eos.date_of_resignation_termination = resignation.resigned_on
        eos.last_working_date = end_date
        eos.notice_period_days = resignation.notice_period or 0

        # Get basic salary (component with is_gratuity=True)
        salary_component = EmployeeSalaryStructure.objects.filter(
            employee=employee,
            component__is_gratuity=True,
            is_active=True
        ).order_by('-date_updated').first()

        if not salary_component or not salary_component.amount:
            logger.warning(f"No active gratuity salary component for employee {employee.emp_code}")
            eos.gratuity_amount = Decimal('0.00')
            eos.last_month_salary = Decimal('0.00')
            eos.gratuity_days = 0
            eos.notice_pay = Decimal('0.00')
            eos.save()
            return

        basic_salary = salary_component.amount
        daily_wage = basic_salary / 30
        eos.last_month_salary = basic_salary

        # Get gratuity rule, converting years_of_service to Decimal for comparison
        years_of_service_decimal = Decimal(str(eos.years_of_service))
        gratuity_rule = GratuityTable.objects.filter(
            Q(minimum_value__lte=years_of_service_decimal) &
            (Q(maximum_value__gte=years_of_service_decimal) | Q(maximum_value__isnull=True)) &
            Q(is_active=True)
        ).first()

        if not gratuity_rule or eos.years_of_service < 1:
            logger.warning(f"No gratuity rule or insufficient service years ({eos.years_of_service}) for employee {employee.emp_code}")
            eos.gratuity_days = 0
            eos.gratuity_amount = Decimal('0.00')
        else:
            if resignation.termination_type in ['termination', 'retirement', 'death_or_disablement']:
                eos.gratuity_days = gratuity_rule.termination_days * eos.years_of_service

            else:  # resignation
                eos.gratuity_days = gratuity_rule.resignation_days * eos.years_of_service

            eos.gratuity_amount = Decimal(eos.gratuity_days) * daily_wage
            max_gratuity = basic_salary * 24
            eos.gratuity_amount = min(eos.gratuity_amount, max_gratuity)

        # Additional settlement components
        eos.notice_pay = Decimal('0.00') if eos.notice_period_days == 0 else daily_wage * eos.notice_period_days
        # eos.leave_salary = Decimal('0.00')  # Placeholder for future use

        eos.save()
    except Exception as e:
        logger.erro
