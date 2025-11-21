from celery import shared_task
from django_tenants.utils import schema_context
from django.utils import timezone
from .models import AssetApproval,AssetApprovalLevel,AssetEmailTemplate
from EmpManagement .models import RequestNotification
import logging
from django_tenants.utils import schema_context
logger = logging.getLogger(__name__)
from EmpManagement .utils import get_employee_context,send_notification_email

@shared_task
def asset_escalate_approval_task(approval_id, schema_name):
    """
    Automatically escalates a pending approval when its escalation time expires.
    """
    from django.db import connection

    with schema_context(schema_name):
        try:
            approval = AssetApproval.objects.get(id=approval_id)

            # Skip if approval already handled
            if approval.status != AssetApproval.PENDING or approval.escalated:
                return

            # Find escalation rule
            level_rule = AssetApprovalLevel.objects.filter(
                asset_type=approval.asset_request.asset_type,
                level=approval.level
            ).first()

            if not level_rule or not level_rule.escalate_to:
                return  # No escalation rule defined

            old_approver = approval.approver
            new_approver = level_rule.escalate_to

            # 🔥 Mark current approval as escalated
            approval.status = AssetApproval.ESCALATED
            approval.note = f"Escalated to {new_approver.username}"
            approval.escalated = True
            approval.escalated_at = timezone.now()
            approval.save()

            # 🔥 Create a new approval entry for the escalated user
            new_approval = AssetApproval.objects.create(
                asset_request=approval.asset_request,
                approver=new_approver,
                role=approval.role,
                level=approval.level,
                status=AssetApproval.PENDING,
                note=f"Escalated from {old_approver.username}",
                is_escalation=True,
                created_by=old_approver,
            )

            # Send escalation notification email
            send_notification_email(
                user=new_approver,
                employee=None,
                message=f"This request has been escalated to you for approval: {approval.asset_request.asset_type.name}",
                template_type="request_created",
                context={
                    **get_employee_context(approval.asset_request.employee),
                    'asset_type': approval.asset_request.asset_type.name
                },
                email_template_model=AssetEmailTemplate,
                notification_model=RequestNotification
            )

            print(f"⚡ Escalation triggered for {approval.asset_request.asset_type} → {new_approver.username}")

        except AssetApproval.DoesNotExist:
            print(f"⚠️ Approval {approval_id} not found for escalation.")
