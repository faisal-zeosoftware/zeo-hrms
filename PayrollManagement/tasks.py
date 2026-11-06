from ZeoApps.scheduling import payroll_off as _zeo_off   # v1.67.1 (companies); v1.67.3 (apps) also the companies without Payroll
from celery import shared_task
from .utils import send_payslip_email 
from .models import (Payslip,AdvanceSalaryApproval,AdvanceSalaryEmailTemplate,AdvanceSalaryNotification,AdvanceCommonWorkflow,
                     LoanApprovalLevels,LoanApproval,LoanNotification,LoanEmailTemplate,AirticketApproval,AirticketWorkflow,AirticketEmailTemplate)
from django_tenants.utils import schema_context 
from django_tenants.utils import get_tenant_model                                 
from django.utils import timezone 
import logging
from django.core.exceptions import ValidationError
from datetime import timedelta
from .models import AirTicketPolicy,AirTicketAllocation
from EmpManagement.models import emp_master,RequestNotification
from EmpManagement .utils import send_notification_email,get_employee_context
logger = logging.getLogger(__name__)

@shared_task
def send_payslip_email_task(payslip_id, tenant_schema_name):
    try:
        with schema_context(tenant_schema_name):
            from .models import Payslip  # Ensure models are imported inside context if needed
            from .utils import send_payslip_email

            payslip = Payslip.objects.get(id=payslip_id)
            success = send_payslip_email(payslip)

            if success:
                payslip.status = 'processed'
                payslip.save(update_fields=['status'])

            return success

    except Payslip.DoesNotExist:
        logger.warning(f"Payslip ID {payslip_id} not found in tenant {tenant_schema_name}")
    except Exception as e:
        logger.exception(f"Error sending payslip email for tenant {tenant_schema_name}, payslip {payslip_id}: {str(e)}")
@shared_task
def schedule_all_payslip_emails():
    TenantModel = get_tenant_model()
    tenant_schemas = TenantModel.objects.values_list('schema_name', flat=True)

    for schema_name in tenant_schemas:
        with schema_context(schema_name):
            payslips = Payslip.objects.filter(
                send_email=True,
                payslip_pdf__isnull=False,
                status='Approved'  # or whatever condition you want
            )
            for payslip in payslips:
                send_payslip_email_task.delay(payslip.id, schema_name)

logger = logging.getLogger(__name__)
@shared_task
def accrue_air_tickets():
    tenants = get_all_tenant_schemas()  # List of tenant schema names
    today = timezone.now().date()

    for tenant_schema_name in tenants:
        try:
            with schema_context(tenant_schema_name):
                logger.info(f"🎫 Air ticket allocation started on {today} for tenant: {tenant_schema_name}")
                count = allocate_air_tickets(today)   # v1.62.3 (items 105, 124)
                logger.info(f"{count} air ticket(s) allocated for tenant: {tenant_schema_name}")

        except Exception as e:
            logger.error(f" Error processing tenant {tenant_schema_name}: {str(e)}")

    return " Air ticket allocation task completed for all tenants."


def allocate_air_tickets(today=None):
    """v1.62.3 (items 105, 124): the air ticket allocations of the current schema on `today`.
    * item 105 – the policy frequency is followed: the next allocation of a policy only when "every N years" has passed since the
      employee's last allocation of it (before: one every calendar year – 26 extra allocations on 01/01/2027 for "every 2 years").
    * item 124 – the employee's country is not required: the policy country is matched on the employee country, else the
      nationality's country, and the branch country (a policy for the country of work); department / designation / category are
      only needed when the policy limits them, and the policy's branches are respected. Employees who left or are leaving
      (inactive, approved resignation / termination, leaving status) get no new allocation.
    Returns the number of allocations made."""
    from PayrollPlus import air_rules as AR
    from PayrollPlus import air_policy as AP   # v1.66.0 (items 119–123)
    from EmpManagement.utils import service_years
    today = today or timezone.now().date()
    policies = list(AirTicketPolicy.objects.filter(is_active=True).prefetch_related(
        'rules', 'eligible_departments', 'eligible_designations', 'eligible_categories', 'branch').order_by('id'))
    count = 0
    for employee in emp_master.objects.filter(is_active=True).select_related('emp_branch_id', 'emp_nationality'):
        if not employee.emp_joined_date or employee.emp_joined_date > today:
            continue
        if AR.employee_left(employee, today):
            logger.info(f"Air ticket: {employee.emp_code} left / is leaving – no new allocation")
            continue
        countries = AR.policy_countries(employee)
        years = service_years(employee.emp_joined_date, today)
        # v1.66.0 (item 123): the overlap setting – every matching policy (today) or only the one that wins (most specific)
        _v1660_match = [p for p in policies if p.country_id in countries and AR.policy_matches(p, employee)]
        for policy in AP.policies_to_allocate(employee, _v1660_match):
            if not [r for r in policy.rules.all() if r.required_service_years <= years]:
                continue
            if AP.probation_blocks(employee, policy, today):   # v1.66.0 (item 119): "Can be used during probation" off, still in probation
                continue
            if AP.category_change(employee, policy, today) is not None:   # v1.66.0 (item 123): old policy closed pro-rata, new cycle starts
                count += 1
                continue
            if not AP.allocation_due(employee, policy, today):   # 1.62.3 frequency + v1.66.0 policy change day
                continue
            _amt = AP.allocation_amount(employee, policy, today)   # v1.66.0 (items 119, 120): fare / class / family / one-way (default: policy amount)
            a = AirTicketAllocation.objects.create(employee=employee, policy=policy, amount=_amt,
                                                   remaining_amount=_amt, status='PENDING', is_active=True,
                                                   expiry_date=AP.allocation_expiry(policy, today))   # v1.66.0 (item 121): lapse
            if a.allocated_date != today:   # the job run for another day (catch-up / test): the allocation carries that day
                AirTicketAllocation.objects.filter(pk=a.pk).update(allocated_date=today)
            count += 1
    AP.apply_lapse(today)   # v1.66.0 (item 121): open allocations of a lapsing policy get their expiry
    return count


def get_all_tenant_schemas(): 
    # Implement this function based on your tenant management system 
    TenantModel = get_tenant_model() 
    # v1.56.1 not the public schema (it has no HR tables – every run logged "Error processing tenant public")
    return TenantModel.objects.exclude(schema_name__in=_zeo_off()).values_list('schema_name', flat=True)

@shared_task
def advance_salary_escalate_approval_task(approval_id, schema_name):
    """
    Automatically escalates a pending approval when its escalation time expires.
    """
    from django.db import connection

    with schema_context(schema_name):
        try:
            approval = AdvanceSalaryApproval.objects.get(id=approval_id)

            # Skip if approval already handled
            if approval.status != AdvanceSalaryApproval.PENDING or approval.escalated:
                return

            # Find escalation rule
            level_rule = AdvanceCommonWorkflow.objects.filter(
                level=approval.level
            ).first()

            if not level_rule or not level_rule.escalate_to:
                return  # No escalation rule defined

            old_approver = approval.approver
            new_approver = level_rule.escalate_to

            # 🔥 Mark current approval as escalated
            approval.status = AdvanceSalaryApproval.ESCALATED
            approval.note = f"Escalated to {new_approver.username}"
            approval.escalated = True
            approval.escalated_at = timezone.now()
            approval.save()

            # 🔥 Create a new approval entry for the escalated user
            new_approval = AdvanceSalaryApproval.objects.create(
                request=approval.request,
                employee=approval.request.employee,
                approver=new_approver,
                role=approval.role,
                level=approval.level,
                status=AdvanceSalaryApproval.PENDING,
                note=f"Escalated from {old_approver.username}",
                is_escalation=True,
                # created_by=old_approver,
            )

            # Send escalation notification email
            send_notification_email(
                user=new_approver,
                employee=None,
                message=f"This request has been escalated to you for approval: {approval.request.document_number}",
                template_type="request_created",
                context={
                    **get_employee_context(approval.request.employee),
                    'doc_number': approval.request.document_number,
                },
                email_template_model=AdvanceSalaryEmailTemplate,
                notification_model=AdvanceSalaryNotification
            )

            print(f"⚡ Escalation triggered for {approval.request.document_number} → {new_approver.username}")

        except AdvanceSalaryApproval.DoesNotExist:
            print(f"⚠️ Approval {approval_id} not found for escalation.")
@shared_task
def loan_escalate_approval_task(approval_id, schema_name):
    """
    Automatically escalates a pending approval when its escalation time expires.
    """
    from django.db import connection

    with schema_context(schema_name):
        try:
            approval = LoanApproval.objects.get(id=approval_id)

            # Skip if approval already handled
            if approval.status != LoanApproval.PENDING or approval.escalated:
                return

            # Find escalation rule
            level_rule = LoanApprovalLevels.objects.filter(
                workflow__loan_type=approval.loan_request.loan_type,  # v1.7.0: was loan_type= (FieldError)
                workflow__branch=approval.loan_request.employee.emp_branch_id,
                level=approval.level
            ).first()

            if not level_rule or not level_rule.escalate_to:
                return  # No escalation rule defined

            old_approver = approval.approver
            new_approver = level_rule.escalate_to

            # 🔥 Mark current approval as escalated
            approval.status = LoanApproval.ESCALATED
            approval.note = f"Escalated to {new_approver.username}"
            approval.escalated = True
            approval.escalated_at = timezone.now()
            approval.save()

            # 🔥 Create a new approval entry for the escalated user
            new_approval = LoanApproval.objects.create(
                loan_request=approval.loan_request,
                approver=new_approver,
                role=approval.role,
                level=approval.level,
                status=LoanApproval.PENDING,
                note=f"Escalated from {old_approver.username}",
                is_escalation=True,
                # created_by=old_approver,
            )

            # Send escalation notification email
            send_notification_email(
                user=new_approver,
                employee=None,
                message=f"This request has been escalated to you for approval: {approval.loan_request.loan_type.loan_type}",
                template_type="request_created",
                context={
                    **get_employee_context(approval.loan_request.employee),
                    'doc_number': approval.loan_request.document_number,
                    'loan_type': approval.loan_request.loan_type.loan_type,
                    'amount_requested': approval.loan_request.amount_requested,
                    'repayment_period': approval.loan_request.repayment_period,
                    'emi_amount': approval.loan_request.emi_amount,
                    'remaining_balance': approval.loan_request.remaining_balance,
                    'status': approval.loan_request.status
                },
                email_template_model=LoanEmailTemplate,
                notification_model=LoanNotification
            )

            print(f"⚡ Escalation triggered for {approval.loan_request.document_number} → {new_approver.username}")

        except LoanApproval.DoesNotExist:
            print(f"⚠️ Approval {approval_id} not found for escalation.")

@shared_task
def airticket_escalate_approval_task(approval_id, schema_name):
    """
    Automatically escalates a pending approval when its escalation time expires.
    """
    from django.db import connection

    with schema_context(schema_name):
        try:
            approval = AirticketApproval.objects.get(id=approval_id)

            # Skip if approval already handled
            if approval.status != AirticketApproval.PENDING or approval.escalated:
                return

            # Find escalation rule
            level_rule = AirticketWorkflow.objects.filter(
                level=approval.level
            ).first()

            if not level_rule or not level_rule.escalate_to:
                return  # No escalation rule defined

            old_approver = approval.approver
            new_approver = level_rule.escalate_to

            # 🔥 Mark current approval as escalated
            approval.status = AirticketApproval.ESCALATED
            approval.note = f"Escalated to {new_approver.username}"
            approval.escalated = True
            approval.escalated_at = timezone.now()
            approval.save()

            # 🔥 Create a new approval entry for the escalated user
            new_approval = AirticketApproval.objects.create(
                request=approval.request,
                employee=approval.request.employee,
                approver=new_approver,
                role=approval.role,
                level=approval.level,
                status=AirticketApproval.PENDING,
                note=f"Escalated from {old_approver.username}",
                is_escalation=True,
                # created_by=old_approver,
            )

            # Send escalation notification email
            send_notification_email(
                user=new_approver,
                employee=None,
                message=f"This request has been escalated to you for approval: {approval.request.document_number}",
                template_type="request_created",
                context={
                    **get_employee_context(approval.request.employee),
                    'document_number': approval.request.document_number,
                },
                email_template_model=AirticketEmailTemplate,
                notification_model=RequestNotification
            )

            print(f"⚡ Escalation triggered for {approval.request.document_number} → {new_approver.username}")

        except AdvanceSalaryApproval.DoesNotExist:
            print(f"⚠️ Approval {approval_id} not found for escalation.")