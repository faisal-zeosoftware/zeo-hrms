from celery import shared_task
from .utils import send_payslip_email 
from .models import Payslip 
from django_tenants.utils import schema_context 
from django_tenants.utils import get_tenant_model                                 
from django.utils import timezone 
import logging
from django.core.exceptions import ValidationError
from datetime import timedelta
from .models import AirTicketPolicy,AirTicketAllocation
from EmpManagement.models import emp_master
logger = logging.getLogger(__name__)

# @shared_task
# def send_payslip_email_task(payslip_id, tenant_schema_name):
#     try:
#         with schema_context(tenant_schema_name):
#             payslip = Payslip.objects.get(id=payslip_id)
#             return send_payslip_email(payslip)
#     except Payslip.DoesNotExist:
#         logger.warning(f"Payslip ID {payslip_id} not found in tenant {tenant_schema_name}")
#     except Exception as e:
#         logger.exception(f"Error sending payslip email for tenant {tenant_schema_name}, payslip {payslip_id}: {str(e)}")
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
    tenants = get_all_tenant_schemas()  # Fetch all tenant schemas
    today = timezone.now().date()

    for tenant_schema_name in tenants:
        try:
            with schema_context(tenant_schema_name):
                logger.info(f"Air ticket allocation task started on {today} for tenant {tenant_schema_name}")

                policies = AirTicketPolicy.objects.filter(is_active=True)
                employees = emp_master.objects.filter(is_active=True)

                for employee in employees:
                    for policy in policies:
                        # Check eligibility
                        is_eligible = True
                        if policy.eligible_departments.exists() and employee.department not in policy.eligible_departments.all():
                            is_eligible = False
                        if policy.eligible_categories.exists() and employee.emp_category not in policy.eligible_categories.all():
                            is_eligible = False
                        if employee.country != policy.country:  # Assuming emp_master has a country field
                            is_eligible = False

                        # Check probation status
                        branch = employee.branch  # Assuming emp_master has a ForeignKey to brnch_mstr
                        if branch and branch.probation_period_days > 0:
                            probation_end_date = employee.emp_joined_date + timedelta(days=branch.probation_period_days)
                            is_on_probation = today <= probation_end_date
                            if is_on_probation and not policy.allowed_in_probation:
                                is_eligible = False

                        # Check last allocation
                        last_allocation = AirTicketAllocation.objects.filter(
                            employee=employee, status='APPROVED', is_active=True
                        ).order_by('-allocated_date').first()
                        if last_allocation:
                            min_date = last_allocation.allocated_date + timedelta(days=policy.frequency_years * 365)
                            if today < min_date:
                                is_eligible = False

                        if is_eligible:
                            allocation = AirTicketAllocation(
                                employee=employee,
                                policy=policy,
                                amount=policy.amount,
                                remaining_amount=policy.amount,
                                status='APPROVED',  # Auto-approved for automatic allocation
                                allocation_type='AUTO',
                                expiry_date=today + timedelta(days=365),  # Example: 1-year expiry
                            )
                            try:
                                allocation.save()
                                logger.info(f"Allocated air ticket for Employee {employee.id} - Policy {policy.id}")
                            except ValidationError as e:
                                logger.error(f"Validation error for Employee {employee.id} - Policy {policy.id}: {str(e)}")

                logger.info(f"Air ticket allocation task completed for tenant {tenant_schema_name}")

        except Exception as e:
            logger.error(f"Error processing tenant {tenant_schema_name}: {str(e)}")

    return "Air ticket allocation task completed for all tenants"


def get_all_tenant_schemas(): 
    # Implement this function based on your tenant management system 
    TenantModel = get_tenant_model() 
    return TenantModel.objects.values_list('schema_name', flat=True)
