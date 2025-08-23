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
                
                employees = emp_master.objects.filter(is_active=True)
                count = 0

                for employee in employees:
                    # Skip employee if required fields are missing
                    if not all([employee.emp_dept_id, employee.emp_desgntn_id, employee.emp_ctgry_id, employee.emp_country_id]):
                        logger.warning(f"⚠️ Skipping employee {employee.emp_first_name} due to missing profile data.")
                        continue

                    years = (today - employee.emp_joined_date).days // 365

                    # Fetch policies matching by country only first
                    policies = AirTicketPolicy.objects.filter(
                        is_active=True,
                        country=employee.emp_country_id
                    ).distinct()

                    #  Now filter based on flexible department/designation/category
                    filtered_policies = []
                    for policy in policies:
                        matches_department = (
                            not policy.eligible_departments.exists() or
                            policy.eligible_departments.filter(id=employee.emp_dept_id.id).exists()
                        )
                        matches_designation = (
                            not policy.eligible_designations.exists() or
                            policy.eligible_designations.filter(id=employee.emp_desgntn_id.id).exists()
                        )
                        matches_category = (
                            not policy.eligible_categories.exists() or
                            policy.eligible_categories.filter(id=employee.emp_ctgry_id.id).exists()
                        )

                        if matches_department and matches_designation and matches_category:
                            filtered_policies.append(policy)

                    # Process allocation
                    for policy in filtered_policies:
                        already_allocated = AirTicketAllocation.objects.filter(
                            employee=employee,
                            policy=policy,
                            allocated_date__year=today.year
                        ).exists()
                        if already_allocated:
                            continue

                        rules = policy.rules.filter(required_service_years__lte=years).order_by('-required_service_years')
                        if rules.exists():
                            rule = rules.first()
                            AirTicketAllocation.objects.create(
                                employee=employee,
                                policy=policy,
                                amount=policy.amount,
                                remaining_amount=policy.amount,
                                status='PENDING',
                                is_active=True,
                            )
                            count += 1

                logger.info(f"{count} air ticket(s) allocated for tenant: {tenant_schema_name}")

        except Exception as e:
            logger.error(f" Error processing tenant {tenant_schema_name}: {str(e)}")

    return " Air ticket allocation task completed for all tenants."



def get_all_tenant_schemas(): 
    # Implement this function based on your tenant management system 
    TenantModel = get_tenant_model() 
    return TenantModel.objects.values_list('schema_name', flat=True)
