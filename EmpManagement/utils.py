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


# def get_current_schema_from_domain(request):
#     # Assuming the schema name is the first part of the domain name
#     domain = request.get_host()
#     schema_name = domain.split('.')[0]
#     return schema_name

# from django.core.mail import EmailMultiAlternatives, get_connection
# from django.template import Context, Template
# from django.utils.html import strip_tags
# from django.conf import settings
# from EmpManagement .models import EmailConfiguration

# def send_email(to_email, subject, body, context):
#     """
#     Utility function to send emails.
#     """
#     try:
#         # Try to retrieve the active email configuration
#         try:
#             email_config = EmailConfiguration.objects.get(is_active=True)
#             use_custom_config = True
#         except EmailConfiguration.DoesNotExist:
#             use_custom_config = False
#             default_email = settings.EMAIL_HOST_USER

#         # Use custom or default email configuration
#         if use_custom_config:
#             default_email = email_config.email_host_user
#             connection = get_connection(
#                 host=email_config.email_host,
#                 port=email_config.email_port,
#                 username=email_config.email_host_user,
#                 password=email_config.email_host_password,
#                 use_tls=email_config.email_use_tls,
#             )
#         else:
#             connection = get_connection(
#                 host=settings.EMAIL_HOST,
#                 port=settings.EMAIL_PORT,
#                 username=settings.EMAIL_HOST_USER,
#                 password=settings.EMAIL_HOST_PASSWORD,
#                 use_tls=settings.EMAIL_USE_TLS,
#             )

#         # Render email template
#         template = Template(body)
#         html_message = template.render(Context(context))
#         plain_message = strip_tags(html_message)

#         # Send email
#         email = EmailMultiAlternatives(
#             subject=subject,
#             body=plain_message,
#             from_email=default_email,
#             to=[to_email],
#             connection=connection,
#             headers={'From': 'zeosoftware@abc.com'}
#         )
#         email.attach_alternative(html_message, "text/html")
#         email.send(fail_silently=False)

#         return {"status": "success", "message": "Email sent successfully."}

#     except Exception as e:
#         return {"status": "error", "message": str(e)}

# import smtplib
# from email.mime.multipart import MIMEMultipart
# from email.mime.text import MIMEText
# from django.template.loader import render_to_string
# from django.utils.html import strip_tags

# def send_dynamic_email(subject, template_name, context, from_email, to_email, smtp_server, smtp_port, smtp_user, smtp_password):
#     html_message = render_to_string(template_name, context)
#     plain_message = strip_tags(html_message)

#     msg = MIMEMultipart('alternative')
#     msg['Subject'] = subject
#     msg['From'] = from_email
#     msg['To'] = to_email

#     part1 = MIMEText(plain_message, 'plain')
#     part2 = MIMEText(html_message, 'html')

#     msg.attach(part1)
#     msg.attach(part2)

#     server = smtplib.SMTP(smtp_server, smtp_port)
#     server.starttls()
#     server.login(smtp_user, smtp_password)
#     server.sendmail(from_email, to_email, msg.as_string())
#     server.quit()

