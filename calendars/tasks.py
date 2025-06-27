from celery import shared_task
from django_tenants.utils import schema_context 
from django_tenants.utils import get_tenant_model                                 
from django.utils import timezone
from django.apps import apps
from datetime import datetime, timedelta
from EmpManagement.models import emp_master
from .models import( leave_entitlement,leave_accrual_transaction,emp_leave_balance,leave_reset_transaction,LeaveResetPolicy,LeaveEncashmentTransaction,
                    LeaveCarryForwardTransaction)
from celery.schedules import crontab
from dateutil.relativedelta import relativedelta
import calendar
from django.db.models import F
from django.db import transaction
import logging
logger = logging.getLogger(__name__)


@shared_task
def accrue_leaves():
    tenants = get_all_tenant_schemas()  # Fetch all tenant schemas
    today = timezone.now().date()

    for tenant_schema_name in tenants:
        try:
            with schema_context(tenant_schema_name):
                logger.info(f"Accrue leaves task started on {today} for tenant {tenant_schema_name}")

                entitlements = leave_entitlement.objects.filter(accrual=True).order_by("leave_type", "min_experience")
                employees = emp_master.objects.all()

                for employee in employees:
                    leave_type_entitlements = {}  # Store best entitlements per leave type

                    for entitlement in entitlements:
                        leave_type = entitlement.leave_type
                        accrual_frequency = entitlement.accrual_frequency
                        accrual_month = entitlement.accrual_month
                        accrual_day = entitlement.accrual_day

                        # Determine base date (Joining Date or Confirmation Date)
                        base_date = (
                            employee.emp_joined_date
                            if entitlement.effective_after_from == "date_of_joining"
                            else employee.emp_date_of_confirmation
                        )

                        if not base_date:
                            continue

                        # Calculate experience in months
                        experience = relativedelta(today, base_date)
                        experience_in_months = experience.years * 12 + experience.months

                        # Convert entitlement's min_experience to months
                        min_experience_months = (
                            entitlement.min_experience * 12
                            if entitlement.effective_after_unit == "years"
                            else entitlement.min_experience
                        )

                        # Select the most specific entitlement for each leave type
                        if (
                            experience_in_months >= min_experience_months and
                            (leave_type not in leave_type_entitlements or min_experience_months > leave_type_entitlements[leave_type]['experience'])
                        ):
                            leave_type_entitlements[leave_type] = {
                                "entitlement": entitlement,
                                "experience": min_experience_months
                            }

                    # Process accrual for each leave type
                    for leave_type, data in leave_type_entitlements.items():
                        best_entitlement = data["entitlement"]
                        accrual_amount = best_entitlement.accrual_rate
                        accrue_today = False  # Default to False

                        # **Daily Accrual**
                        if best_entitlement.accrual_frequency == 'days':
                            accrue_today = True

                        # **Monthly Accrual**
                        elif best_entitlement.accrual_frequency == 'months':
                            if best_entitlement.accrual_day == '1st' and today.day == 1:
                                accrue_today = True
                            elif best_entitlement.accrual_day == 'last':
                                last_day_of_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
                                if today == last_day_of_month:
                                    accrue_today = True

                        # **Yearly Accrual**
                        elif best_entitlement.accrual_frequency == 'years':
                            if best_entitlement.accrual_month and today.month == month_name_to_number(best_entitlement.accrual_month) and today.day == 1:
                                accrue_today = True

                        if accrue_today and accrual_amount > 0:
                            leave_balance, created = emp_leave_balance.objects.get_or_create(
                                employee=employee,
                                leave_type=leave_type,  # Ensure accrual is per leave type
                                defaults={"balance": 0}
                            )
                            leave_balance.balance += accrual_amount
                            leave_balance.save()

                            leave_accrual_transaction.objects.create(
                                employee=employee,
                                leave_type=leave_type,
                                accrual_date=today,
                                amount=accrual_amount,
                                created_at=timezone.now(),
                            )

                            logger.info(f"Accrued {accrual_amount} days for Employee {employee.id} - Leave Type {leave_type.id}")

                logger.info(f"Leave accrual task completed for tenant {tenant_schema_name}")

        except Exception as e:
            logger.error(f"Error processing tenant {tenant_schema_name}: {str(e)}")

    return "Leave accrual task completed for all tenants"


def month_name_to_number(month_name):
    """Helper function to convert month names to month numbers."""
    month_map = {
        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
        'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
    }
    return month_map.get(month_name)    

@shared_task
def reset_leave_balances():
    tenants = get_all_tenant_schemas()
    today = timezone.now().date()
    logger.info(f"Reset leave balances task started on {today}")

    for tenant_schema_name in tenants:
        try:
            with schema_context(tenant_schema_name):
                logger.info(f"Processing leave reset for tenant: {tenant_schema_name}")

                resets = LeaveResetPolicy.objects.filter(reset=True)
                if not resets.exists():
                    logger.info(f"No reset policies found for tenant: {tenant_schema_name}")
                    continue

                for reset in resets:
                    reset_month = month_name_to_number(reset.month)
                    reset_day = 1 if reset.day == '1st' else calendar.monthrange(today.year, today.month)[1]

                    # Check reset condition based on frequency
                    if reset.frequency == 'years':
                        if today.month != reset_month or today.day != reset_day:
                            logger.info(f"Skipping yearly reset for {reset.leave_type}, not the reset date.")
                            continue
                    elif reset.frequency == 'months':
                        if today.day != reset_day:
                            logger.info(f"Skipping monthly reset for {reset.leave_type}, not the reset date.")
                            continue

                    leave_type = reset.leave_type
                    logger.info(f"Resetting leave balances for {leave_type} on {today}")

                    employees = emp_master.objects.all()
                    if not employees.exists():
                        logger.warning(f"No employees found in tenant {tenant_schema_name}")
                        continue

                    for emp in employees:
                        leave_balance = emp_leave_balance.objects.filter(employee=emp, leave_type=leave_type).first()
                        if not leave_balance:
                            logger.warning(f"No leave balance found for employee {emp.emp_code} and leave type {leave_type}")
                            continue

                        initial_balance = leave_balance.balance
                        carry_forward_amount = 0
                        encashment_amount = 0

                        # **Carry Forward Calculation**
                        if reset.allow_cf and initial_balance > 0:
                            if reset.cf_unit_or_percentage == 'percentage':
                                calculated_cf_amount = (initial_balance * reset.cf_value / 100)
                                carry_forward_amount = min(calculated_cf_amount, reset.cf_max_limit if reset.cf_max_limit else calculated_cf_amount)
                            else:
                                carry_forward_amount = min(initial_balance, reset.cf_value)

                            carry_forward_amount = max(carry_forward_amount, 0)  # Ensure no negative values

                        # Deduct Carry Forward from Leave Balance
                        remaining_balance = initial_balance - carry_forward_amount

                        # **Encashment Calculation**
                        if remaining_balance > 0 and reset.allow_encashment:
                            if reset.encashment_unit_or_percentage == 'percentage':
                                encashment_amount = min((remaining_balance * reset.encashment_value / 100), reset.encashment_max_limit or remaining_balance)
                            else:
                                encashment_amount = min(remaining_balance, reset.encashment_value)

                            encashment_amount = max(encashment_amount, 0)  # Ensure no negative values
                        else:
                            encashment_amount = 0

                        # **Store Carry Forward Transaction**
                        if reset.allow_cf and carry_forward_amount > 0:
                            LeaveCarryForwardTransaction.objects.create(
                                employee=emp,
                                leave_type=leave_type,
                                reset_date=today,
                                carried_forward_units=carry_forward_amount if reset.cf_unit_or_percentage == 'unit' else 0,
                                carried_forward_percentage=reset.cf_value if reset.cf_unit_or_percentage == 'percentage' else 0,
                                max_limit=reset.cf_max_limit,
                                final_carry_forward=carry_forward_amount,
                                created_by=None
                            )

                        # **Store Encashment Transaction**
                        if reset.allow_encashment and encashment_amount > 0:
                            LeaveEncashmentTransaction.objects.create(
                                employee=emp,
                                leave_type=leave_type,
                                reset_date=today,
                                encashment_units=encashment_amount if reset.encashment_unit_or_percentage == 'unit' else 0,
                                encashment_percentage=reset.encashment_value if reset.encashment_unit_or_percentage == 'percentage' else 0,
                                max_limit=reset.encashment_max_limit,
                                encashment_amount=encashment_amount,
                                created_by=None
                            )

                        # **Apply Leave Reset (Update Balance)**
                        leave_balance.balance = carry_forward_amount  # Set final balance from carry-forward
                        leave_balance.save()

                        # **Store Reset Transaction**
                        leave_reset_transaction.objects.create(
                            employee=emp,
                            leave_type=leave_type,
                            reset_date=today,
                            initial_balance=initial_balance,
                            carry_forward_amount=carry_forward_amount,
                            encashment_amount=encashment_amount,
                            final_balance=carry_forward_amount,  # Updated balance
                            created_by=None
                        )

                        logger.info(
                            f"Reset leave balance for employee {emp.emp_code}. "
                            f"Carry Forward: {carry_forward_amount}, Encashment: {encashment_amount}, "
                            f"New Balance: {carry_forward_amount}"
                        )

        except Exception as e:
            logger.error(f"Error processing tenant {tenant_schema_name}: {e}")

    logger.info("Reset leave balances task completed.")

@shared_task
def deduct_expired_carry_forward_leaves():
    from django.db import transaction
    from decimal import Decimal

    tenants = get_all_tenant_schemas()
    today = timezone.now().date()
    logger.info(f"Reset leave balances task started on {today}")

    for tenant_schema_name in tenants:
        try:
            with schema_context(tenant_schema_name):
                logger.info(f"Processing expired_carry_forward_leaves: {tenant_schema_name}")

                policies = LeaveResetPolicy.objects.filter(carry_forward_choice='carry_forward_with_expiry')

                for policy in policies:
                    time_unit = policy.cf_time_choice
                    expires_in = policy.cf_expires_in_value

                    # Determine expiry date based on policy settings
                    if time_unit == 'days':
                        expiry_threshold = today - timedelta(days=expires_in)
                    elif time_unit == 'months':
                        expiry_threshold = today.replace(month=today.month - expires_in) if today.month > expires_in else today.replace(year=today.year - 1, month=(12 - expires_in + today.month))
                    elif time_unit == 'years':
                        expiry_threshold = today.replace(year=today.year - expires_in)
                    else:
                        continue  

                    # Fetch expired carry-forward transactions
                    expired_transactions = LeaveCarryForwardTransaction.objects.filter(
                        reset_date__lte=expiry_threshold,
                        final_carry_forward__gt=0
                    )

                    with transaction.atomic():
                        for leave_transaction in expired_transactions:
                            emp_balance = emp_leave_balance.objects.filter(
                                employee=leave_transaction.employee, 
                                leave_type=leave_transaction.leave_type
                            ).first()

                            if emp_balance:
                                # Convert both values to Decimal before subtraction
                                emp_balance.balance = Decimal(str(emp_balance.balance))  
                                leave_transaction.final_carry_forward = Decimal(str(leave_transaction.final_carry_forward))

                                # Deduct expired carry-forward leave from employee leave balance
                                deduction = min(emp_balance.balance, leave_transaction.final_carry_forward)
                                emp_balance.balance -= deduction
                                emp_balance.save()

                                # Set carry-forward amount to 0 since it has expired
                                leave_transaction.final_carry_forward = Decimal("0")
                                leave_transaction.save()
        except Exception as e:
            logger.error(f"Error processing tenant {tenant_schema_name}: {e}")

            
def get_all_tenant_schemas(): 
    # Implement this function based on your tenant management system 
    TenantModel = get_tenant_model() 
    return TenantModel.objects.values_list('schema_name', flat=True)