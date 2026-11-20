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
        'emp_last_name': getattr(employee, 'emp_last_name', '') or '',   # v1.73.19 (item 7): offered on the template screens
        'emp_code': getattr(employee, 'emp_code', '') or '',
        'employee_name': ' '.join(x for x in (employee.emp_first_name, getattr(employee, 'emp_last_name', '')) if x),   # the screens' example placeholder
        'emp_gender': employee.emp_gender,
        'emp_date_of_birth': employee.emp_date_of_birth,
        'emp_personal_email': employee.emp_personal_email,
        'emp_company_email': employee.emp_company_email,
        'emp_branch_name': employee.emp_branch_id,
        'emp_department_name': employee.emp_dept_id,
        'emp_designation_name': employee.emp_desgntn_id,
        'emp_joined_date': getattr(employee, 'emp_joined_date', None),
    }


def _person_name(user, employee):
    """The reader's name for {{ recipient_name }}: the user's name, else his employee record, else the login name."""
    if user is not None:
        n = ' '.join(x for x in (getattr(user, 'first_name', ''), getattr(user, 'last_name', '')) if x).strip()
        if not n:
            try:
                from .models import emp_master
                e = emp_master.objects.filter(users=user).first()
                n = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) if e else ''
            except Exception:   # noqa: BLE001
                n = ''
        return n or user.get_username()
    if employee is not None:
        return ' '.join(x for x in (employee.emp_first_name, getattr(employee, 'emp_last_name', '')) if x)
    return ''


def _pick_template(model, template_type, employee):
    """v1.73.19 (item 7): the template of this type for the employee – the one whose branch / department / category / designation
    match best (a template with none of them fits everybody); before, two templates of a type (one per branch) stopped every e-mail
    of that type ("Multiple templates found")."""
    f = 'template_type' if any(x.name == 'template_type' for x in model._meta.fields) else 'template_name'
    rows = list(model.objects.filter(**{f: template_type}).order_by('-id'))
    if not rows:
        return None
    if len(rows) == 1 or employee is None:
        return rows[0]
    want = {'branch': getattr(employee, 'emp_branch_id_id', None), 'Department': getattr(employee, 'emp_dept_id_id', None),
            'Category': getattr(employee, 'emp_ctgry_id_id', None), 'Designation': getattr(employee, 'emp_desgntn_id_id', None)}
    best, score = None, -1
    for t in rows:
        sc, ok = 0, True
        for rel, val in want.items():
            if not hasattr(t, rel):
                continue
            ids = set(getattr(t, rel).values_list('pk', flat=True))
            if ids:
                if val in ids:
                    sc += 1
                else:
                    ok = False
                    break
        if ok and sc > score:
            best, score = t, sc
    return best or rows[0]


def _fill_context(context, employee, message):
    """v1.73.19 (item 7): every placeholder the template screens offer has its value – {{ doc_number }}, {{ request_type }},
    {{ reason }}, {{ emp_last_name }} … were empty in most e-mails (the senders passed other names or nothing)."""
    import re
    if employee is not None:
        for k, v in get_employee_context(employee).items():
            context.setdefault(k, v)
    if not context.get('doc_number'):
        context['doc_number'] = context.get('document_number') or (re.search(r'Document No:?\s*([\w./-]+)', message or '') or [None, ''])[1]
    context.setdefault('document_number', context.get('doc_number'))
    if not context.get('request_type'):
        req = context.get('request')
        context['request_type'] = (str(req).split('-', 1)[1] if isinstance(req, str) and '-' in req else '') or ''
    if context.get('doc_number') and not (context.get('reason') and context.get('request_type')):
        try:   # the request itself (its reason / type), found by its document number
            from django.apps import apps as _apps
            from DashboardManagement.services import REQUESTS
            for key, label, rm, *_ in REQUESTS:
                try:
                    R = _apps.get_model(rm)
                except LookupError:
                    continue
                if not any(x.name == 'document_number' for x in R._meta.fields):
                    continue
                r = R.objects.filter(document_number=context['doc_number']).first()
                if r is None:
                    continue
                context.setdefault('reason', getattr(r, 'reason', None) or getattr(r, 'reason_for_leaving', None) or '')
                if not context.get('reason'):
                    context['reason'] = getattr(r, 'reason', None) or getattr(r, 'reason_for_leaving', None) or ''
                if not context.get('request_type'):
                    t = getattr(r, 'leave_type', None) or getattr(r, 'request_type', None) or getattr(r, 'loan_type', None) or getattr(r, 'asset_type', None)
                    context['request_type'] = getattr(t, 'name', None) or getattr(t, 'type_name', None) or (str(t) if t else label)
                break
        except Exception:   # noqa: BLE001
            pass
    context.setdefault('reason', '')
    context.setdefault('message', message or '')
    return context

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
    if user is None and employee is not None:
        # v1.73.19 (items 5, 11): a notice to the employee (request approved / rejected) of a request he sent in self service had no
        # user (created_by is empty): it reached no bell and no phone – it goes to the employee's own login
        try:
            u = getattr(employee, 'users', None)
            if u is not None and u.is_active:
                user = u
        except Exception:   # noqa: BLE001
            pass
    try:   # v1.73.19 (item 5): no "waiting for your approval" for a step approved with the person's decision on an earlier level
        from AccessControl.approvals import quiet
        if user is not None and title in ('Request Created', '') and 'approv' in (message or '').lower() and quiet(getattr(user, 'pk', None)):
            return {'status': 'skipped', 'message': 'approved on an earlier level by the same person'}
    except Exception:   # noqa: BLE001
        pass

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
    email_template = _pick_template(email_template_model, template_type, employee)   # v1.73.19 (item 7)
    if email_template is None:
        return {
            "status": "warning",
            "message": f"No template found for '{template_type}'."
        }

    subject = email_template.subject
    body_text = email_template.body
    if reader_lang != 'en':  # v1.15.0: the company's own translation of the template, else the language pack, else English
        try:
            from Translations.services import mail_texts
            subject, body_text = mail_texts(email_template, reader_lang)
        except Exception as e:
            logger.warning(f"E-mail template language skipped: {e}")

    _fill_context(context, employee, message)   # v1.73.19 (item 7): the placeholders the template screens offer
    template = Template(body_text)
    try:   # v1.73.19 (item 7): placeholders in the subject were sent as typed ("{{ doc_number }}")
        subject = Template(subject).render(Context({**context, 'recipient_name': _person_name(user, employee)})).replace('\n', ' ').strip()
    except Exception:   # noqa: BLE001
        pass

    recipient_name = _person_name(user, employee)   # v1.73.19 (item 7): the name, not the login name

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


def normalize_gratuity_rules(rules, years_of_service):
    """v1.62.0: a gratuity table often starts with a band "0 to 1 year: 0 days" – it means "no gratuity under one year of
    service" (UAE Decree-Law 33 of 2021, Art. 51), not "the first year is never paid". Read progressively, that band left the
    first year out of every gratuity (4.28 years paid as 3.28 × 21 days). Returns (eligible, rules): below the band's end
    nothing is due; from it on the next band counts from year 0. Tables without such a band are returned unchanged."""
    from types import SimpleNamespace
    rules = list(rules)
    if len(rules) > 1 and Decimal(str(rules[0].minimum_value)) == 0 and not rules[0].resignation_days and not rules[0].termination_days:
        threshold = Decimal(str(rules[0].maximum_value or 0))
        if Decimal(str(years_of_service)) < threshold:
            return False, rules
        nxt = rules[1]
        first = SimpleNamespace(minimum_value=Decimal('0'), maximum_value=nxt.maximum_value, resignation_days=nxt.resignation_days,
                                termination_days=nxt.termination_days, is_active=True)
        return True, [first] + rules[2:]
    return True, rules


def calculate_progressive_gratuity(years_of_service, daily_wage, termination_type, branch_id=None):
    from OrganisationManager.models import GratuityTable

    total_gratuity_days = Decimal('0.00')
    remaining_years = Decimal(str(years_of_service))

    try:   # v1.67.12 (effective, item 64): the gratuity table in force on the rule day (end of service: the last working day)
        from PayrollPlus.effective import gratuity_rows
        rules = gratuity_rows()
    except ImportError:
        rules = list(GratuityTable.objects.filter(is_active=True).order_by('minimum_value'))

    if not rules:
        # v1.14.0: another country's rule set (branch / company country pack) → its formula (daily wage = basic / 30)
        try:
            from CountryPack import services as _cp
            if not _cp.uses_uae_gratuity(branch_id):
                kind = 'termination' if termination_type in ['termination', 'retirement', 'death_or_disablement'] else 'resignation'
                g = _cp.gratuity_breakdown(remaining_years, Decimal(daily_wage or 0) * Decimal('30'), kind, branch_id,
                                           daily=(daily_wage if _cp.uae_version_daily(branch_id) else None))
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
    else:
        eligible, rules = normalize_gratuity_rules(rules, remaining_years)   # v1.62.0 a "0–1 year: 0 days" band = the minimum service
        if not eligible:
            return Decimal('0.00'), Decimal('0.00')

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
def calculate_settlement(eos, opts=None):
    """v1.59.0: the gratuity rules in force on the last working day (dated country-policy versions).
    v1.62.11 (EOS settings): opts – the company's end-of-service rules (PayrollPlus.eos_settings.eos_opts); None = as before."""
    try:
        from CountryPack.services import rules_on
        lwd = getattr(getattr(eos, 'resignation', None), 'last_working_date', None)
    except ImportError:
        return _calculate_settlement(eos, opts)
    with rules_on(lwd):
        return _calculate_settlement(eos, opts)


def _calculate_settlement(eos, opts=None):
    from PayrollManagement.models import EmployeeSalaryStructure
    from OrganisationManager.models import GratuityTable
    from PayrollManagement.models import AirTicketAllocation

    try:
        resignation = eos.resignation
        employee = resignation.employee

        start_date = employee.emp_joined_date
        end_date = resignation.last_working_date
        _golive_unpaid = 0.0
        try:   # v1.56.0 mid-year go-live: gratuity service start and unpaid days before ZEO (setup guide)
            from PayrollPlus.golive import gratuity_start, unpaid_before
            start_date = gratuity_start(employee, start_date) or start_date
            _golive_unpaid = unpaid_before(employee)
        except ImportError:
            pass

        # -------------------------------
        # SERVICE CALCULATION
        # -------------------------------
        # v1.15.1: service counts the joining and the last day; days of unpaid leave are not service
        # (UAE Decree-Law 33/2021 Art. 51) – they were never taken off before
        total_days = (end_date - start_date).days + 1
        opts = opts or {}   # v1.62.11 (EOS settings)
        if opts.get('service_days') == 'difference':   # last working day − joining date (the joining day not counted twice)
            total_days -= 1
        try:
            from django.apps import apps as _apps
            _LR = _apps.get_model('calendars', 'employee_leave_request')
            _unpaid = 0.0
            _lq = _LR.objects.filter(employee=employee, status='approved', start_date__lte=end_date, end_date__gte=start_date)
            # v1.62.11 (EOS settings): the leave types the company counts as unpaid (default: the types of type 'unpaid')
            _lq = _lq.filter(leave_type_id__in=opts['unpaid_types']) if opts.get('unpaid_types') else _lq.filter(leave_type__type='unpaid')
            for _r in _lq:
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
            eos.leave_days_without_pay = round(_unpaid + _golive_unpaid, 2)
        except Exception:
            logger.exception('unpaid leave days for EOS failed')
        opts['computed_unpaid'] = eos.leave_days_without_pay   # v1.62.11 (EOS settings): shown next to HR's override
        if opts.get('unpaid_days') is not None:   # v1.62.11 (EOS settings): HR's override on the draft settlement
            eos.leave_days_without_pay = round(float(opts['unpaid_days']), 2)
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
        from PayrollManagement.basic import base_of   # v1.73.7: the categories ticked for gratuity (default: every Basic line)
        _basic_total = base_of(employee, 'gratuity')

        if _basic_total:
            basic_salary = _basic_total
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
        notice_daily = daily_wage   # v1.62.11 (EOS settings): notice pay stays basic ÷ 30
        if opts.get('gratuity_daily') == 'basic_365':   # v1.62.11 (EOS settings): gratuity daily wage basic × 12 ÷ 365
            daily_wage = basic_salary * 12 / Decimal('365')

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
        if opts.get('service_days') == 'difference':   # v1.62.11 (EOS settings)
            years -= Decimal('1') / Decimal('365')
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
        _r170 = None
        try:   # v1.70.0 (items 142 – 148): a branch of another country – that country's gratuity (wage basis, shares, nationals, funds)
            from PayrollPlus.country170 import gratuity as _g170
            _r170 = _g170(employee, end_date, resignation.termination_type, eos.leave_days_without_pay or 0)
            if _r170 is not None:
                eos.gratuity_days, eos.gratuity_amount = _r170['days'], _r170['amount']
        except ImportError:
            pass
        except Exception:
            logger.exception('country gratuity (1.70.0)')
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
        if _r170 is None and eos.gratuity_amount > max_gratuity:   # v1.70.0: another country's cap is applied by its own rule
            eos.gratuity_amount = max_gratuity
        # -------------------------------
        # NOTICE PAY
        # -------------------------------
        # v1.15.1: pay in lieu of notice only for the notice days not worked (resigned on → last working day);
        # before, the whole notice period was added even when the employee worked it
        served = (end_date - resignation.resigned_on).days if resignation.resigned_on else 0
        unserved = max(int(eos.notice_period_days or 0) - max(served, 0), 0)
        if unserved:
            eos.notice_pay = (notice_daily * Decimal(unserved)).quantize(Decimal('0.01'))
        else:
            eos.notice_pay = Decimal('0.00')

        # -------------------------------
        # AIR TICKET
        # -------------------------------
        # v1.62.3 (item 106): the newest open allocation only – not one whose ticket / cash payout was already taken, used up or
        # expired by the last working day (that one was paid again here); one entitlement, never one per allocation
        from django.db.models import Q as _Q
        ticket = AirTicketAllocation.objects.filter(
            employee=employee,
            status='APPROVED',
            is_active=True
        ).exclude(airticketrequest__status__in=('APPROVED', 'PROCESSED')).filter(
            _Q(expiry_date__isnull=True) | _Q(expiry_date__gte=end_date)
        ).order_by('-allocated_date', '-pk').first()
        _left = (ticket.remaining_amount if ticket.remaining_amount is not None else ticket.amount) if ticket else Decimal('0.00')
        try:   # v1.67.12 (effective, item 63): only the unused part – the allocations still owed, as the final settlement pays them
            from PayrollPlus.air_policy import unused_at_exit
            _left = unused_at_exit(employee, end_date)[0]
        except ImportError:
            pass

        eos.air_ticket = _left if _left and _left > 0 else Decimal('0.00')

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


def template_scope_clash(qs, attrs, instance=None):
    """v1.73.19 (item 7): a second template of one type is allowed for other branches (the template screens let HR choose branches;
    the e-mail takes the template of the employee's branch). It clashes when the branches overlap or both are for every branch."""
    if not qs.exists():
        return False
    new = attrs.get('branch', None)
    if new is None and instance is not None and hasattr(instance, 'branch'):
        new = list(instance.branch.all())
    new_ids = {getattr(b, 'pk', b) for b in (new or [])}
    for t in qs:
        if not hasattr(t, 'branch'):
            return True
        ids = set(t.branch.values_list('pk', flat=True))
        if not ids or not new_ids or ids & new_ids:
            return True
    return False


def push_bell(user, message, title='', branch=None, notification_type='general', source=None):
    """v1.73.19 (item 11): a notice written straight into a module's notification table (e.g. "leave rejected") also goes to the
    person's bell inbox (top bar and phone), as send_notification_email does."""
    if user is None or not getattr(user, 'is_active', True):
        return
    try:
        from UserManagement.models import UserNotificationInbox
        schema = db_connection.schema_name
        with schema_context('public'):
            UserNotificationInbox.objects.create(user=user, branch_id=getattr(branch, 'id', None), branch_name=getattr(branch, 'branch_name', None),
                                                 schema_name=schema, notification_type=notification_type, title=title, message=message,
                                                 source_model=type(source).__name__ if source is not None else '', source_id=getattr(source, 'pk', None))
    except Exception as e:   # noqa: BLE001
        logger.warning(f"bell inbox not written: {e}")


def approver_msg(kind, what, emp, doc):
    """v1.73.21 (item 3) the notice to the person who decides: "A loan request Personal loan of Bikash Thapa (Document No: X) is
    waiting for your approval." – was "Your LoanRequest …" / "A GeneralRequest …(Document No" as if the approver had asked."""
    name = ''
    if emp is not None:
        name = ' '.join(x for x in [str(getattr(emp, 'emp_first_name', '') or ''), str(getattr(emp, 'emp_last_name', '') or '')] if x).strip()
    if kind == 'resignation request' and str(what or '').strip().lower() == 'resignation':
        what = None   # "A resignation request resignation of …"
    out = ('An ' if kind[:1] in 'aeiou' else 'A ') + kind + (f" {what}" if what not in (None, '') else '') + (f" of {name}" if name else '')
    out += (f" (Document No: {doc})" if doc else '') + " is waiting for your approval."
    return out
