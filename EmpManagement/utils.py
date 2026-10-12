# notifications/utils.py

from django.utils.html import strip_tags
from django.template import Template, Context
from django.core.mail import EmailMultiAlternatives, get_connection
from django.conf import settings
import logging
from django_tenants.utils import connection
from django_tenants.utils import schema_context
from django.db import connection as db_connection






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
    delegate_user=None,

):
    """
    Generic utility to send email and create in-app notification.
    """

    if context is None:
        context = {}

    # Default models if not passed
    from .models import EmailTemplate, RequestNotification, EmailConfiguration

    email_template_model = email_template_model or EmailTemplate
    notification_model = notification_model or RequestNotification

    # v1.15.0: the reader's language (their own choice, else the company default) for the notification and the e-mail
    reader_lang = 'en'
    try:
        from Translations.i18n import for_lang
        from Translations.services import reader_lang as _reader_lang
        reader_lang = _reader_lang(user, employee)
        if reader_lang != 'en':
            message, title = for_lang(message, reader_lang), for_lang(title, reader_lang)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"Notification language skipped: {e}")

    created_notification = None

    try:

        logger.warning(
            f"Creating notification | user={user} | delegate_user={delegate_user}"
        )

        created_notification = notification_model.objects.create(
            recipient_user=user,
            recipient_employee=employee,
            message=message,
            deligate_user=delegate_user,
            # notification_type=notification_type
        )

        logger.warning(
            f"Notification created successfully | "
            f"id={created_notification.id} | "
            f"deligate_user={created_notification.deligate_user}"
        )

    except Exception as e:

        logger.exception(f"Notification creation failed: {e}")

    # user notification tenant wise
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

    # email setup
    try:

        email_template = email_template_model.objects.get(
            template_type=template_type
        )

    except email_template_model.DoesNotExist:

        return {
            "status": "warning",
            "message": f"No template found for '{template_type}'."
        }

    except email_template_model.MultipleObjectsReturned:

        return {
            "status": "error",
            "message": f"Multiple templates found for '{template_type}'."
        }

    subject = email_template.subject
    body_text = email_template.body
    if reader_lang != 'en':  # v1.15.0: the company's own translation of the template, else the language pack, else English
        try:
            from Translations.services import mail_texts
            subject, body_text = mail_texts(email_template, reader_lang)
        except Exception as e:
            logger.warning(f"E-mail template language skipped: {e}")

    template = Template(body_text)

    recipient_name = (
        user.get_username()
        if user
        else (
            employee.emp_first_name
            if employee
            else ""
        )
    )

    context.update({
        'recipient_name': recipient_name
    })

    html_message = template.render(Context(context))
    plain_message = strip_tags(html_message)

    try:

        email_config = EmailConfiguration.objects.get(
            is_active=True
        )

        default_email = email_config.email_host_user

        connection = get_connection(
            host=email_config.email_host,
            port=email_config.email_port,
            username=email_config.email_host_user,
            password=email_config.email_host_password,
            use_tls=email_config.email_use_tls,
        )

    except Exception as e:

        logger.warning(
            f"Using fallback email config: {e}"
        )

        default_email = settings.EMAIL_HOST_USER

        connection = get_connection(
            host=settings.EMAIL_HOST,
            port=settings.EMAIL_PORT,
            username=settings.EMAIL_HOST_USER,
            password=settings.EMAIL_HOST_PASSWORD,
            use_tls=settings.EMAIL_USE_TLS,
        )

    to_email = (
        user.email
        if user and user.email
        else (
            employee.emp_personal_email
            if employee and employee.emp_personal_email
            else None
        )
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

            email.attach_alternative(
                html_message,
                "text/html"
            )

            email.send(
                fail_silently=False
            )

            return {
                "status": "success",
                "message": f"Email sent to {to_email}"
            }

        except Exception as e:

            logger.error(
                f"Email sending failed: {e}"
            )

            return {
                "status": "error",
                "message": str(e)
            }

    return {
        "status": "success",
        "message": "Notification created successfully. No recipient email found."
    }

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
def service_years(start, end):
    """Years of service from the joining day to the last working day (both counted): whole years by anniversary dates, the rest in
    days ÷ 365. 01/11/2020 – 31/10/2026 = 6 years exactly."""
    from datetime import timedelta as _td
    stop = end + _td(days=1)
    years = stop.year - start.year
    def _anniv(y):
        try:
            return start.replace(year=start.year + y)
        except ValueError:            # 29 February joiner
            return start.replace(year=start.year + y, day=28)
    while years > 0 and _anniv(years) > stop:
        years -= 1
    rest = (stop - _anniv(years)).days
    return Decimal(years) + Decimal(rest) / Decimal('365')


def calculate_progressive_gratuity(years_of_service, daily_wage, termination_type, branch_id=None):
    from OrganisationManager.models import GratuityTable

    total_gratuity_days = Decimal('0.00')
    remaining_years = Decimal(str(years_of_service))

    rules = list(GratuityTable.objects.filter(
        is_active=True
    ).order_by('minimum_value'))

    if not rules:
        # v1.14.0: another country's rule set (branch / company country pack) → its formula (daily wage = basic / 30)
        try:
            from CountryPack import services as _cp
            if not _cp.uses_uae_gratuity(branch_id):
                kind = 'termination' if termination_type in ['termination', 'retirement', 'death_or_disablement'] else 'resignation'
                g = _cp.gratuity_breakdown(remaining_years, Decimal(daily_wage or 0) * Decimal('30'), kind, branch_id)
                return Decimal(str(g['days'])).quantize(Decimal('0.01')), Decimal(str(g['amount'])).quantize(Decimal('0.01'))
        except ImportError:
            pass
        # No gratuity table set up for the company: fall back to UAE Labour Law (Federal Decree-Law
        # 33 of 2021, Art. 51) instead of silently paying zero - nothing below 1 year, 21 days' basic
        # per year for the first 5 years, 30 days per year after that.
        if remaining_years < 1:
            return Decimal('0.00'), Decimal('0.00')
        from types import SimpleNamespace
        rules = [SimpleNamespace(minimum_value=0, maximum_value=5, resignation_days=21, termination_days=21),
                 SimpleNamespace(minimum_value=5, maximum_value=None, resignation_days=30, termination_days=30)]

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
        # v1.15.1: service counts the joining and the last day; days of unpaid leave are not service
        # (UAE Decree-Law 33/2021 Art. 51) – they were never taken off before
        total_days = (end_date - start_date).days + 1
        try:
            from django.apps import apps as _apps
            _LR = _apps.get_model('calendars', 'employee_leave_request')
            _unpaid = 0.0
            for _r in _LR.objects.filter(employee=employee, status='approved', leave_type__type='unpaid', start_date__lte=end_date, end_date__gte=start_date):
                _days = float(_r.approved_days or _r.number_of_days or 0)
                _span = (_r.end_date - _r.start_date).days + 1
                _inside = (min(_r.end_date, end_date) - max(_r.start_date, start_date)).days + 1
                _unpaid += _days * _inside / _span if _span > 0 else 0
            # v1.18.0: days of absence without pay (absent, no leave) are not service either (Art. 51) – only unpaid leave was taken off
            try:
                _AD = _apps.get_model('AttendancePlus', 'AttendanceDay')
                _unpaid += _AD.objects.filter(employee_id=employee.pk, date__range=(start_date, end_date), status='absent').count()
            except LookupError:
                pass
            eos.leave_days_without_pay = round(_unpaid, 2)
        except Exception:
            logger.exception('unpaid leave days for EOS failed')
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
            component__payroll_category='basic',  # UAE gratuity is on basic salary (is_gratuity no longer exists)
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
        # v1.18.0: completed years by the calendar (anniversaries) + the days of the part year ÷ 365, less the unpaid days ÷ 365 –
        # total days ÷ 365 counted every 29 February as extra service (6 years = 2,191 days gave 6.0027 years)
        years = service_years(start_date, end_date) - Decimal(str(eos.leave_days_without_pay or 0)) / Decimal('365')
        try:   # v1.20.2: counted as the country rule set says (anniversary years or all days ÷ 365)
            from CountryPack.services import gratuity_service_years
            years = gratuity_service_years(start_date, end_date, eos.leave_days_without_pay or 0, employee.emp_branch_id_id)
        except ImportError:
            pass
        if years < 0:
            years = Decimal('0')
        eos.years_of_service = float(round(years, 4))

        gratuity_days, gratuity_amount = calculate_progressive_gratuity(
            years_of_service=years,
            daily_wage=daily_wage,
            termination_type=resignation.termination_type,
            branch_id=employee.emp_branch_id_id,
        )

        eos.gratuity_days = gratuity_days
        eos.gratuity_amount = gratuity_amount
        try:   # v1.18.0: UAE nationals are covered by the GPSSA pension – no Labour Law gratuity (Art. 51 is for non-nationals)
            from PayrollManagement.signals import is_uae_national
            if is_uae_national(employee):
                eos.gratuity_days, eos.gratuity_amount = 0, Decimal('0.00')
        except ImportError:
            pass

        max_gratuity = basic_salary * Decimal('24')
        try:   # v1.20.2: rule set "cap_basis": "wage" – two years of the full monthly wage, not of the basic
            from CountryPack.services import rule_set_for_branch
            _g = (rule_set_for_branch(employee.emp_branch_id_id) or {}).get('gratuity') or {}
            if _g.get('cap_basis') == 'wage':
                from DashboardManagement.reports import wage_by_employee
                max_gratuity = Decimal(str(wage_by_employee([employee.pk]).get(employee.pk) or 0)) * 12 * Decimal(str(_g.get('cap_years_of_pay') or 2))
        except Exception:
            pass
        try:  # v1.14.0: the 2-year cap is the UAE one; other rule sets apply their own cap in calculate_progressive_gratuity
            from CountryPack.services import uses_uae_gratuity
            if not uses_uae_gratuity(employee.emp_branch_id_id) and not GratuityTable.objects.filter(is_active=True).exists():
                max_gratuity = eos.gratuity_amount
        except ImportError:
            pass
        if eos.gratuity_amount > max_gratuity:
            eos.gratuity_amount = max_gratuity
        # -------------------------------
        # NOTICE PAY
        # -------------------------------
        # v1.15.1: pay in lieu of notice only for the notice days not worked (resigned on → last working day);
        # before, the whole notice period was added even when the employee worked it
        served = (end_date - resignation.resigned_on).days if resignation.resigned_on else 0
        unserved = max(int(eos.notice_period_days or 0) - max(served, 0), 0)
        if unserved:
            eos.notice_pay = (daily_wage * Decimal(unserved)).quantize(Decimal('0.01'))
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
    if level_rule is None:  # no escalation rule configured for this level
        return
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
