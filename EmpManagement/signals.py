# EmpManagement/signals.py
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
from decimal import Decimal
from django.db.models import Q
from .models import EmployeeResignation, EndOfService
from OrganisationManager.models import GratuityTable
from PayrollManagement.models import EmployeeSalaryStructure
logger = logging.getLogger(__name__)

@receiver(post_save, sender=EmployeeResignation)
def create_end_of_service(sender, instance, created, **kwargs):
    if created:  # Only trigger on creation of EmployeeResignation
        try:
            with transaction.atomic():
                # Calculate years_of_service upfront to avoid null
                start_date = instance.employee.emp_joined_date
                end_date = instance.last_working_date
                if not start_date or not end_date:
                    logger.error(f"Invalid dates for employee {instance.employee.emp_code}: start_date={start_date}, end_date={end_date}")
                    raise ValueError("Invalid joining or last working date")
                total_days = (end_date - start_date).days
                years_of_service = total_days / 365.0

                # Create EndOfService record with calculated years_of_service
                eos = EndOfService.objects.create(
                    resignation=instance,
                    date_of_joining=start_date,
                    date_of_resignation_termination=instance.resigned_on,
                    last_working_date=end_date,
                    years_of_service=years_of_service,  # Set immediately
                )
                # Calculate settlement
                calculate_settlement(eos)
        except Exception as e:
            logger.error(f"Error creating EndOfService for resignation {instance.id}: {str(e)}")
            raise

def calculate_settlement(eos):
    try:
        resignation = eos.resignation
        employee = resignation.employee
        start_date = employee.emp_joined_date
        end_date = resignation.last_working_date

        # Recalculate years_of_service for consistency
        total_days = (end_date - start_date).days
        eos.years_of_service = total_days / 365.0
        eos.total_service_days = total_days
        eos.net_number_of_days_worked = total_days - eos.leave_days_without_pay
        eos.date_of_joining = start_date
        eos.date_of_resignation_termination = resignation.resigned_on
        eos.last_working_date = end_date
        eos.notice_period_days = resignation.notice_period or 0

        # Get basic salary (component with is_gratuity=True)
        salary_component = EmployeeSalaryStructure.objects.filter(
            employee=employee,
            component__is_gratuity=True,
            is_active=True
        ).order_by('-date_updated').first()

        if not salary_component or not salary_component.amount:
            logger.warning(f"No active gratuity salary component for employee {employee.emp_code}")
            eos.gratuity_amount = Decimal('0.00')
            eos.last_month_salary = Decimal('0.00')
            eos.gratuity_days = 0
            eos.notice_pay = Decimal('0.00')
            eos.save()
            return

        basic_salary = salary_component.amount
        daily_wage = basic_salary / 30
        eos.last_month_salary = basic_salary

        # Get gratuity rule, converting years_of_service to Decimal for comparison
        years_of_service_decimal = Decimal(str(eos.years_of_service))
        gratuity_rule = GratuityTable.objects.filter(
            Q(minimum_value__lte=years_of_service_decimal) &
            (Q(maximum_value__gte=years_of_service_decimal) | Q(maximum_value__isnull=True)) &
            Q(is_active=True)
        ).first()

        if not gratuity_rule or eos.years_of_service < 1:
            logger.warning(f"No gratuity rule or insufficient service years ({eos.years_of_service}) for employee {employee.emp_code}")
            eos.gratuity_days = 0
            eos.gratuity_amount = Decimal('0.00')
        else:
            if resignation.termination_type in ['termination', 'retirement', 'death_or_disablement']:
                eos.gratuity_days = grat;align:gratuity_rule.termination_days * eos.years_of_service
            else:  # resignation
                eos.gratuity_days = gratuity_rule.resignation_days * eos.years_of_service

            eos.gratuity_amount = Decimal(eos.gratuity_days) * daily_wage
            max_gratuity = basic_salary * 24
            eos.gratuity_amount = min(eos.gratuity_amount, max_gratuity)

        # Additional settlement components
        eos.notice_pay = Decimal('0.00') if eos.notice_period_days == 0 else daily_wage * eos.notice_period_days
        # eos.leave_salary = Decimal('0.00')  # Placeholder for future use

        eos.save()
    except Exception as e:
        logger.error(f"Error in calculate_settlement for employee {eos.resignation.employee.emp_code}: {str(e)}")
        raise