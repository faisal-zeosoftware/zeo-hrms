# EmpManagement/tasks.py
from ZeoApps.scheduling import hr_off as _zeo_off   # v1.67.1 (companies); v1.67.3 (apps) also the companies without HR
from celery import shared_task
from django.utils import timezone
from datetime import date, timedelta
from datetime import timedelta
from django.core.mail import send_mail
from .models import (Emp_Documents,notification,NotificationSettings,DocExpEmailTemplate,Approval,ApprovalLevel,
                     RequestNotification,EmailTemplate)
import logging
from django_tenants.utils import schema_context
from django_tenants.utils import get_tenant_model
from django.template import Template, Context
from django.template.loader import render_to_string
from django.core.mail import send_mail
from django.utils.html import strip_tags
from celery import group
logger = logging.getLogger(__name__)
from django.contrib.auth import get_user_model
import traceback
from collections import defaultdict
from django.core.mail import EmailMultiAlternatives, get_connection
from .models import EmailConfiguration
from .utils import get_employee_context,send_notification_email


@shared_task
def send_document_expiry_notifications_for_all_tenants():
    """v1.70.1 – the daily document expiry alert (runs every hour; each company once a day from 09:00 of its own time zone).

    Fixed in 1.70.1 (Documents note, open points 1 – 9): the documents are the CURRENT ones of ACTIVE employees – employee document
    rows and the identity details (passport, visa, Emirates ID …), as the expiry hub shows them; an alert applies its branches,
    departments, categories and designations; each alert sends its own summary to its users; the templates "Employee Notification"
    and "User Notification" are used (the old name "ESS User Notification" too); the built-in texts get their values; a document is
    told once when it enters the warning window and once when it expires (not every day)."""
    try:
        tenants = get_tenant_model().objects.exclude(schema_name__in=_zeo_off())
        for tenant in tenants:
            try:
                with schema_context(tenant.schema_name):
                    _expiry_alerts_for_company()
            except Exception:
                logger.exception('document expiry alerts of %s', tenant.schema_name)
    except Exception:
        logger.exception('document expiry alerts')


DOCEXP_KEY = 'v1701_docexp'


def _company_today():
    """(today, hour) in the company's time zone"""
    from zoneinfo import ZoneInfo
    try:
        from zeo.appkit.engine import company_tz
        tz = ZoneInfo(company_tz())
    except Exception:
        tz = ZoneInfo('Asia/Dubai')
    now = timezone.now().astimezone(tz)
    return now.date(), now.hour


def _docexp_state():
    from django.apps import apps as _apps
    CO = _apps.get_model('ZeoApps', 'CompanyOption')
    o = CO.objects.filter(key=DOCEXP_KEY).first()
    return CO, (dict(o.value) if o and isinstance(o.value, dict) else {})


def _alert_applies(alert, emp):
    """the alert's branches / departments / categories / designations (empty = all)"""
    for field, attr in (('branch', 'emp_branch_id_id'), ('Department', 'emp_dept_id_id'), ('Category', 'emp_ctgry_id_id'),
                        ('Designation', 'emp_desgntn_id_id')):
        ids = {x.pk for x in getattr(alert, field).all()}
        if ids and getattr(emp, attr, None) not in ids:
            return False
    return True


def _expiry_alerts_for_company(today=None, force=False):
    """one company: returns {'sent': n, 'users': n} (tests call it with today / force)"""
    from django.apps import apps as _apps
    from DashboardManagement.documents import current_documents, _key
    day, hour = _company_today()
    today = today or day
    CO, st = _docexp_state()
    if not force and (hour < 9 or st.get('last_run') == today.isoformat()):
        return {'sent': 0, 'users': 0, 'skipped': True}
    sent = st.get('sent') or {}
    E = _apps.get_model('EmpManagement', 'emp_master')
    emps = {e.pk: e for e in E.objects.filter(is_active=True).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id')}
    alerts = list(NotificationSettings.objects.select_related('document_type').prefetch_related('branch', 'Department', 'Category',
                                                                                                 'Designation', 'notify_users'))
    docs = current_documents(list(emps), today)
    n_sent, n_users = 0, 0
    for alert in alerts:
        tkey = _key(getattr(alert.document_type, 'type_name', '') or '')
        lines = []
        for d in docs:
            if not d['expiry'] or _key(d['type']) != tkey:
                continue
            e = emps.get(d['employee_id'])
            if e is None or not _alert_applies(alert, e):
                continue
            if not (today - timedelta(days=alert.days_after_expiry) <= d['expiry'] <= today + timedelta(days=alert.days_before_expiry)):
                continue
            status = 'expiring' if d['expiry'] >= today else 'expired'
            k = f"{d['employee_id']}|{_key(d['type'])}|{d['expiry'].isoformat()}"
            lines.append((e, d, status))
            if sent.get(k) == status or (sent.get(k) == 'expired' and status == 'expiring'):
                continue   # already told at this stage
            sent[k] = status
            n_sent += 1
            _notify_employee(e, d, status, alert)
        if lines and alert.send_email:
            n_users += _notify_users(alert, lines)
    # forget the documents that expired more than a year ago
    cut = (today - timedelta(days=400)).isoformat()
    sent = {k: v for k, v in sent.items() if k.rsplit('|', 1)[-1] >= cut}
    CO.objects.update_or_create(key=DOCEXP_KEY, defaults={'value': {'last_run': today.isoformat(), 'sent': sent}})
    return {'sent': n_sent, 'users': n_users}


def _notify_employee(e, d, status, alert):
    """bell notification + the "Employee Notification" e-mail (when the alert sends e-mails)"""
    label = 'is expiring' if status == 'expiring' else 'has expired'
    message = f"The {d['type']} of {e.emp_first_name} {e.emp_last_name or ''} {label} on {_dmy(d['expiry'])}."[:200]
    doc = d.get('doc')
    notification.objects.create(message=message, document_id=doc if doc is not None else None)
    if alert.send_email and (e.emp_personal_email or e.emp_company_email):
        ctx = _doc_context(e, d, status)
        send_template_email('Employee Notification', e.emp_company_email or e.emp_personal_email, ctx)


def _doc_context(e, d, status):
    doc = d.get('doc')
    return {
        'emp_first_name': e.emp_first_name, 'emp_last_name': e.emp_last_name, 'employee_first_name': e.emp_first_name,
        'emp_gender': e.emp_gender, 'emp_date_of_birth': e.emp_date_of_birth, 'emp_personal_email': e.emp_personal_email,
        'emp_company_email': e.emp_company_email, 'emp_branch_name': e.emp_branch_id, 'emp_department_name': e.emp_dept_id,
        'emp_designation_name': e.emp_desgntn_id, 'emp_joined_date': e.emp_joined_date, 'document_type': d['type'],
        'document_number': d.get('number') or '', 'doc_issued_date': getattr(doc, 'emp_doc_issued_date', '') if doc is not None else '',
        'doc_expiry_date': _dmy(d['expiry']), 'expiry_date': _dmy(d['expiry']), 'status': status, 'is_active': True,
    }


def _notify_users(alert, lines):
    """one summary e-mail per alert to its users"""
    summary = '\n'.join(f"{e.emp_first_name} {e.emp_last_name or ''} ({e.emp_code}) – {d['type']} {d.get('number') or ''} "
                         f"{'expires' if s == 'expiring' else 'expired'} on {_dmy(d['expiry'])}" for e, d, s in lines)
    n = 0
    for user in alert.notify_users.all():
        if not user.email:
            continue
        e, d, s = lines[0]
        first = _user_first(user)
        ctx = {**_doc_context(e, d, s), 'user_first_name': first, 'ess_user_first_name': first,
               'documents': summary, 'count': len(lines)}
        send_template_email('User Notification', user.email, ctx)
        n += 1
    return n


def _user_first(user):
    """the first name of a user: their employee record's, else the user name"""
    from django.apps import apps as _apps
    emp = _apps.get_model('EmpManagement', 'emp_master').objects.filter(users=user).only('emp_first_name').first()
    return (emp.emp_first_name if emp and emp.emp_first_name else '') or user.username or user.email


def _dmy(d):
    """v1.67.10 (regression): the date in a message dd/mm/yyyy (1.67.9 item 19) – the expiry notices said "on 2026-11-13"."""
    try:
        return d.strftime('%d/%m/%Y')
    except AttributeError:
        return str(d)


def send_document_notification(document, expiry_date, status, settings):
    try:
        employee = document.emp_id

        message = (
            f"The document '{document.document_type}' of employee "
            f"{employee.emp_first_name} {employee.emp_last_name} is {status} on {_dmy(expiry_date)}."   # v1.67.10 (regression): dd/mm/yyyy
        )

        context = {
            'emp_first_name': employee.emp_first_name,
            'emp_last_name': employee.emp_last_name,
            'emp_gender': employee.emp_gender,
            'emp_date_of_birth':employee.emp_date_of_birth,
            'emp_personal_email':employee.emp_personal_email,
            'emp_company_email':employee.emp_company_email, 
            'emp_branch_name':employee.emp_branch_id,
            'emp_department_name':employee.emp_dept_id ,
            'emp_designation_name':employee.emp_desgntn_id, 
            'emp_joined_date':employee.emp_joined_date, 
            'document_type': document.document_type,
            'document_number':document.emp_doc_number,
            ' is_active ': document.is_active ,
            'expiry_date': expiry_date,
        }

        # Save notification
        notification.objects.create(
            message=message,
            document_id=document
        )

        if settings and settings.send_email:
            send_template_email('Employee Notification',employee.emp_personal_email, context)


    except Exception as e:
        print(f"[ERROR] Failed in send_document_notification: {e}")
        traceback.print_exc()


def send_ess_user_notifications(employee_docs):
    try:
        # Organize document data by branch
        branch_docs = defaultdict(list)
        for doc, status in employee_docs:
            branch_docs[doc.emp_id.emp_branch_id].append((doc, status))

        for branch, docs in branch_docs.items():
            settings = NotificationSettings.objects.filter(branch=branch).first()
            if not settings:
                continue

             # Don't send emails if checkbox is unchecked
            if not settings.send_email:
                continue

            selected_employees = settings.notify_users.all()

            # Compose document summary
            doc_lines = []
            for doc, status in docs:
                emp = doc.emp_id
                line = f"{emp.emp_first_name} {emp.emp_last_name} - {doc.document_type} is {status} on {_dmy(doc.emp_doc_expiry_date)}"   # v1.67.10 (regression): dd/mm/yyyy
                doc_lines.append(line)
            documents_summary = "\n".join(doc_lines)

            for user in selected_employees:
                context = {
                    'user_first_name': user.username, 
                    'documents': documents_summary,
                    'emp_first_name':emp.emp_first_name,
                    'emp_last_name':emp.emp_last_name,
                    'emp_date_of_birth':emp.emp_date_of_birth,
                    'emp_branch_name':emp.emp_branch_id,
                    'emp_department_name':emp.emp_dept_id,
                    'emp_designation_name':emp.emp_desgntn_id,
                    'document_number':doc. emp_doc_number,
                    'doc_issued_date':doc.emp_doc_issued_date,
                    'doc_expiry_date':doc.emp_doc_expiry_date,
                    'is_active':doc.is_active,
                    'expiry_date':doc.emp_doc_expiry_date,
                }
                send_template_email('ESS User Notification', user.email, context)

    except Exception as e:
        print(f"[ERROR] Failed in send_ess_user_notifications: {e}")
        traceback.print_exc()

  
def send_template_email(template_name, recipient_email, context):
    # v1.70.1: the users' template is "User Notification" (the list of Settings › E-mail templates); "ESS User Notification" was
    # looked for and never found, so the company's own text was never used
    names = [template_name] + (['ESS User Notification'] if template_name == 'User Notification' else
                               ['User Notification'] if template_name == 'ESS User Notification' else [])
    template_obj = DocExpEmailTemplate.objects.filter(template_name__in=names).order_by('-id').first()
    try:
        from SelfService.letters import company_name as _cn
        company = _cn() or ''
    except Exception:
        company = ''
    if template_obj is not None:
        subject = template_obj.subject
        html_message = Template(template_obj.body).render(Context(context))
    elif template_name in ('User Notification', 'ESS User Notification'):
        subject = "Document expiry – {0} document(s)".format(context.get('count', ''))
        html_message = (
            f"Dear {context.get('user_first_name') or 'colleague'},\n\n"
            f"These documents are expiring or have expired:\n\n"
            f"{context.get('documents', '')}\n\nRegards,\n{company or 'HR'}"
        )
    else:
        status = 'expires' if context.get('status') == 'expiring' else 'expired'
        subject = f"Your {context.get('document_type', 'document')} {status} on {context.get('expiry_date', '')}"
        html_message = (
            f"Dear {context.get('employee_first_name') or 'colleague'},\n\n"
            f"Your {context.get('document_type', '')} {context.get('document_number', '')} {status} on {context.get('expiry_date', '')}. "
            f"Please arrange the renewal with HR.\n\nRegards,\n{company or 'HR'}"
        )

    plain_message = strip_tags(html_message)

    # Get active email config
    email_config = EmailConfiguration.objects.filter(is_active=True).first()
    if not email_config:
        print("[EMAIL ERROR] No active email configuration found.")
        return

    try:
        connection = get_connection(
            host=email_config.email_host,
            port=email_config.email_port,
            username=email_config.email_host_user,
            password=email_config.email_host_password,
            use_tls=email_config.email_use_tls,
            fail_silently=False
        )

        email = EmailMultiAlternatives(
            subject=subject,
            body=plain_message,
            from_email=email_config.email_host_user,
            to=[recipient_email],
            connection=connection
        )
        email.attach_alternative(html_message, "text/html")
        email.send()
        print(f"[EMAIL] Sent to {recipient_email}")

    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send to {recipient_email}: {e}")
        traceback.print_exc()

def get_all_tenant_schemas():
    TenantModel = get_tenant_model()
    return TenantModel.objects.values_list('schema_name', flat=True)

@shared_task
def escalate_approval_task(approval_id, schema_name):
    """
    Automatically escalates a pending approval when its escalation time expires.
    """
    from django.db import connection

    with schema_context(schema_name):
        try:
            approval = Approval.objects.get(id=approval_id)

            # Skip if approval already handled
            if approval.status != Approval.PENDING or approval.escalated:
                return

            # Find escalation rule
            level_rule = ApprovalLevel.objects.filter(
                workflow__request_type=approval.general_request.request_type,
                workflow__branch=approval.general_request.employee.emp_branch_id,
                level=approval.level
            ).first()

            if not level_rule or not level_rule.escalate_to:
                return  # No escalation rule defined

            old_approver = approval.approver
            new_approver = level_rule.escalate_to

            # 🔥 Mark current approval as escalated
            approval.status = Approval.ESCALATED
            approval.note = f"Escalated to {new_approver.username}"
            approval.escalated = True
            approval.escalated_at = timezone.now()
            approval.save()

            # 🔥 Create a new approval entry for the escalated user
            new_approval = Approval.objects.create(
                general_request=approval.general_request,
                approver=new_approver,
                role=approval.role,
                level=approval.level,
                status=Approval.PENDING,
                note=f"Escalated from {old_approver.username}",
                is_escalation=True,
                created_by=old_approver,
            )

            # Send escalation notification email
            send_notification_email(
                user=new_approver,
                employee=None,
                message=f"This request has been escalated to you for approval: {approval.general_request.request_type.name}",
                template_type="request_created",
                context={
                    **get_employee_context(approval.general_request.employee),
                    'doc_number': approval.general_request.document_number,
                    'request_type': approval.general_request.request_type.name
                },
                email_template_model=EmailTemplate,
                notification_model=RequestNotification
            )

            print(f"⚡ Escalation triggered for {approval.general_request.document_number} → {new_approver.username}")

        except Approval.DoesNotExist:
            print(f"⚠️ Approval {approval_id} not found for escalation.")
