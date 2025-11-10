# EmpManagement/tasks.py
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
    try:
        tenants = get_tenant_model().objects.exclude(schema_name='public')
        print(f"[TENANT TASK] Found {tenants.count()} tenants")

        for tenant in tenants:
            try:
                print(f"[TENANT TASK] Processing tenant: {tenant.schema_name}")
                with schema_context(tenant.schema_name):
                    today = date.today()

                    # Document notifications for employees
                    documents = Emp_Documents.objects.all()
                    employee_docs = []

                    for doc in documents:
                        settings = NotificationSettings.objects.filter(branch=doc.emp_id.emp_branch_id).first()
                        if not settings:
                            continue

                        days_before = settings.days_before_expiry
                        days_after = settings.days_after_expiry
                        expiry_window_start = today - timedelta(days=days_after)
                        expiry_window_end = today + timedelta(days=days_before)

                        if expiry_window_start <= doc.emp_doc_expiry_date <= expiry_window_end:
                            status = "expiring" if doc.emp_doc_expiry_date >= today else "expired"
                            send_document_notification(doc, doc.emp_doc_expiry_date, status)
                            employee_docs.append((doc, status))

                    # ESS user notifications
                    send_ess_user_notifications(employee_docs)

            except Exception as e:
                print(f"[ERROR] Error processing tenant {tenant.schema_name}: {e}")
                traceback.print_exc()

    except Exception as e:
        print(f"[FATAL ERROR] Error in tenant loop: {e}")
        traceback.print_exc()


def send_document_notification(document, expiry_date, status):
    try:
        employee = document.emp_id

        message = (
            f"The document '{document.document_type}' of employee "
            f"{employee.emp_first_name} {employee.emp_last_name} is {status} on {expiry_date}."
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

        # Send individual email
        send_template_email('Employee Notification', employee.emp_personal_email, context)

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

            selected_employees = settings.notify_users.all()

            # Compose document summary
            doc_lines = []
            for doc, status in docs:
                emp = doc.emp_id
                line = f"{emp.emp_first_name} {emp.emp_last_name} - {doc.document_type} is {status} on {doc.emp_doc_expiry_date}"
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
    try:
        template_obj = DocExpEmailTemplate.objects.get(template_name=template_name)
        subject = template_obj.subject
        template = Template(template_obj.body)
        html_message = template.render(Context(context))
    except DocExpEmailTemplate.DoesNotExist:
        if template_name == 'ESS User Notification':
            subject = "All Document Expiry Notifications"
            html_message = (
                f"Dear {context.get('ess_user_first_name', 'User')},\n\n"
                f"The following documents are expiring or have expired:\n\n"
                f"{context.get('documents', '')}\n\nRegards,\nYour Company"
            )
        else:
            subject = "Document Expiry Notification"
            html_message = (
                f"Dear {context.get('employee_first_name', 'Employee')},\n\n"
                f"Your document '{context.get('document_type', '')}' is {context.get('status', '')} "
                f"on {context.get('expiry_date', '')}.\n\nRegards,\nYour Company"
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
                request_type=approval.general_request.request_type,
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
