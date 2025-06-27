from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import PayslipComponent, LoanRepayment, EmployeeSalaryStructure,SalaryComponent,LoanApplication
from calendars.models import Attendance,LeaveEncashmentTransaction
from django.db.models import Q
import logging
from datetime import datetime
from datetime import timedelta
from calendar import monthrange

logger = logging.getLogger(__name__)
from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.apps import apps
from decimal import Decimal
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.apps import apps
from decimal import Decimal
from django.db.models import Count
from django.db import transaction

from datetime import date
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.apps import apps
from django.db.models import Sum
from datetime import datetime
from dateutil.relativedelta import relativedelta
from simpleeval import SimpleEval, NameNotDefined, FunctionNotDefined
from calendars .utils import get_employee_holidays,get_employee_weekend_days


# Initialize logger
logger = logging.getLogger('PayrollManagement')

def evaluate_formula(formula, variables, employee, component):
    try:
        logger.debug(f"Evaluating formula: {formula} with variables: {variables} for employee: {employee}")
        formula = formula.strip("'")

        s = SimpleEval()
        s.names = variables  # ✅ Set variables here

        s.operators.update({
            '<': lambda x, y: x < y,
            '>': lambda x, y: x > y,
            '>=': lambda x, y: x >= y,
            '<=': lambda x, y: x <= y,
            '==': lambda x, y: x == y,
            '!=': lambda x, y: x != y,
            'and': lambda x, y: x and y,
            'or': lambda x, y: x or y,
            'not': lambda x: not x,
            '+': lambda x, y: x + y,
            '-': lambda x, y: x - y,
            '*': lambda x, y: x * y,
            '/': lambda x, y: x / y,
            '%': lambda x, y: x % y
        })

        s.functions.update({
            'MAX': max,
            'MIN': min,
            'AVG': lambda *args: sum(args) / len(args) if args else 0,
            'SUM': sum,
            'ROUND': round,
            'IF': lambda cond, true_val, false_val: true_val if cond else false_val,
        })

        result = s.eval(formula)  # ✅ No keyword argument here
        return round(float(result), 2)

    except (NameNotDefined, FunctionNotDefined) as e:
        logger.error(f"Invalid variable or function in formula '{formula}' for employee {employee}: {e}")
        return 0
    except Exception as e:
        logger.error(f"Error evaluating formula '{formula}' for employee {employee}: {e}")
        return 0

@receiver(post_save, sender=SalaryComponent)
def update_employee_salary_structure(sender, instance, created, **kwargs):
    if not instance.is_fixed and instance.formula:
        EmpMaster = apps.get_model('EmpManagement', 'emp_master')
        EmployeeSalaryStructure = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
        employees = EmpMaster.objects.all()

        for employee in employees:
            # Get variables including fixed components, calendar_days, ot_hours etc.
            variables = get_formula_variables(employee)

            try:
                amount = evaluate_formula(instance.formula, variables, employee, instance)
            except Exception as e:
                logger.error(f"Formula evaluation error for {employee}: {e}")
                amount = Decimal('0.00')

            logger.info(f"Calculated amount for {instance.name} ({instance.code}) for employee {employee}: {amount}")

            EmployeeSalaryStructure.objects.update_or_create(
                employee=employee,
                component=instance,
                defaults={'amount': amount, 'is_active': True}
            )
            logger.info(f"Updated EmployeeSalaryStructure for {employee} with component {instance.name} - Amount: {amount}")

def get_formula_variables(employee, start_date=None, end_date=None):
    Attendance = apps.get_model('calendars', 'Attendance')
    EmployeeOvertime = apps.get_model('calendars', 'EmployeeOvertime')
    EmployeeSalaryStructure = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    AirTicketRequest = apps.get_model('PayrollManagement', 'AirTicketRequest')
    

    if not start_date or not end_date:
        today = datetime.today().date()
        start_date = today.replace(day=1)
        end_date = today.replace(day=monthrange(today.year, today.month)[1])
    
    variables = {
        'calendar_days': Decimal(str((end_date - start_date).days + 1)),
        'fixed_days': Decimal('30.0'),
        'standard_hours': Decimal('160.0'),
    }

    variables['ot_hours'] = EmployeeOvertime.objects.filter(
        employee=employee, date__range=(start_date, end_date), approved=True
    ).aggregate(total_hours=Sum('hours'))['total_hours'] or Decimal('0.00')
    # Air ticket encashment amount
    variables['air_ticket_encashment'] = AirTicketRequest.objects.filter(
        employee=employee,
        request_type='ENCASHMENT',
        status='APPROVED',
        request_date__range=(start_date, end_date)
    ).aggregate(total_encashment=Sum('allocation__amount'))['total_encashment'] or Decimal('0.00')
    
    weekend_days = get_employee_weekend_days(employee)
    holiday_dates = get_employee_holidays(employee, start_date, end_date)

    weekend_ot_days = 0
    holiday_ot_days = 0

    for single_date in daterange(start_date, end_date):
        weekday = single_date.strftime("%A")
        is_weekend = weekday in weekend_days
        is_holiday = single_date in holiday_dates
        attended = Attendance.objects.filter(employee=employee, date=single_date).exists()

        if is_weekend and attended:
            weekend_ot_days += 1
        elif is_holiday and attended:
            holiday_ot_days += 1

    variables['weekend_ot_days'] = Decimal(weekend_ot_days)
    variables['holiday_ot_days'] = Decimal(holiday_ot_days)
    variables['holiday_weekend_ot_days'] = Decimal(weekend_ot_days + holiday_ot_days)
    # variables['holiday_weekend_days_worked'] = Decimal(str(
    #     get_holiday_weekend_days_worked(employee, start_date, end_date)
    # ))
    working_days = get_working_days(employee, start_date, end_date)
    variables['working_days'] = float(working_days)
    
    variables['employee.grade'] = str(getattr(employee, 'grade', ''))
    variables['employee.employee_type'] = str(getattr(employee, 'employee_type', ''))
    variables['employee.joining_date'] = (
        employee.joining_date.strftime('%Y-%m-%d') if getattr(employee, 'joining_date', None) else ''
    )

    if getattr(employee, 'joining_date', None):
        delta = relativedelta(end_date, employee.joining_date)
        variables['years_of_service'] = round(delta.years + delta.months / 12.0, 2)
    else:
        variables['years_of_service'] = 0.0
    # Add encashed_days from LeaveEncashmentTransaction
    encashment_amount = LeaveEncashmentTransaction.objects.filter(
        employee=employee,
        reset_date__range=(start_date, end_date)
    ).aggregate(total_encashment=Sum('encashment_amount'))['total_encashment'] or Decimal('0.00')
    variables['encashed_days'] = encashment_amount

    salary_components = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)
    for sc in salary_components:
        if sc.component and sc.amount is not None:
            variables[sc.component.code] = Decimal(str(sc.amount))

    return variables

def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)
def get_working_days(employee, start_date, end_date):
    weekend_days = get_employee_weekend_days(employee)
    holiday_dates = get_employee_holidays(employee, start_date, end_date)

    working_days = 0

    for day in daterange(start_date, end_date):
        weekday_name = day.strftime("%A")
        if weekday_name in weekend_days:
            continue
        if day in holiday_dates:
            continue
        if Attendance.objects.filter(
            employee=employee,
            date=day,
            check_in_time__isnull=False,
            check_out_time__isnull=False
        ).exists():
            working_days += 1

    return working_days
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.apps import apps
from calendar import monthrange
from datetime import datetime
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

@receiver(post_save, sender='PayrollManagement.PayrollRun')
def run_payroll_on_save(sender, instance, created, **kwargs):
    if not created or instance.status != 'pending':
        return

    EmpMaster = apps.get_model('EmpManagement', 'emp_master')
    SalaryComponent = apps.get_model('PayrollManagement', 'SalaryComponent')
    EmployeeSalaryStructure = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    Payslip = apps.get_model('PayrollManagement', 'Payslip')
    PayslipComponent = apps.get_model('PayrollManagement', 'PayslipComponent')
    GeneralRequest = apps.get_model('EmpManagement', 'GeneralRequest')
    LoanRequest = apps.get_model('PayrollManagement', 'LoanApplication')
    LoanRepayment = apps.get_model('PayrollManagement', 'LoanRepayment')
    MonthlyAttendanceSummary = apps.get_model('calendars', 'MonthlyAttendanceSummary')
    AirTicketRequest = apps.get_model('PayrollManagement', 'AirTicketRequest')
    AdvanceSalaryRequest = apps.get_model('PayrollManagement', 'AdvanceSalaryRequest') 

    try:
        total_working_days = monthrange(instance.year, instance.month)[1]
        start_date = datetime(instance.year, instance.month, 1).date()
        end_date = datetime(instance.year, instance.month, total_working_days).date()
    except Exception as e:
        logger.error(f"Invalid date setup for PayrollRun {instance.id}: {e}")
        return

    employees = EmpMaster.objects.filter(is_active=True)

    for employee in employees:
        variables = get_formula_variables(employee, start_date, end_date)

        salary_components = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)
        total_additions = Decimal('0.00')
        total_deductions = Decimal('0.00')

        # Fetch attendance summary for the employee for the given month and year
        try:
            attendance = MonthlyAttendanceSummary.objects.get(
                employee=employee,
                month=instance.month,
                year=instance.year
            )
            days_worked = attendance.total_present
            days_absent = attendance.total_absent
        except MonthlyAttendanceSummary.DoesNotExist:
            logger.warning(f"No attendance summary for {employee} for {instance.month}/{instance.year}")
            days_worked = total_working_days  # Assume full attendance if no record
            days_absent = 0

        payslip = Payslip.objects.create(
            payroll_run=instance,
            employee=employee,
            total_working_days=total_working_days,
            days_worked=days_worked,
        )

        # Process salary components
        for sc in salary_components:
            component = sc.component
            original_amount = sc.amount or Decimal('0.00')

            # Calculate amount, considering formula if applicable
            if not component.is_fixed and component.formula:
                try:
                    original_amount = Decimal(str(evaluate_formula(component.formula, variables, employee, component)))
                except Exception as e:
                    logger.error(f"Formula error for {component.name}: {e}")
                    original_amount = Decimal('0.00')

            # Apply leave deduction if deduct_leave is True and there are absent days
            amount = original_amount
            if component.deduct_leave and days_absent > 0 and total_working_days > 0:
                per_day_amount = original_amount / total_working_days
                leave_deduction = per_day_amount * days_absent
                amount = original_amount - leave_deduction

            # Create PayslipComponent with the adjusted amount
            PayslipComponent.objects.create(
                payslip=payslip,
                component=component,
                amount=amount
            )

            # Update totals based on component type
            if component.component_type == 'addition':
                total_additions += amount
            elif component.component_type == 'deduction':
                total_deductions += amount

        # Process approved general requests
        approved_requests = GeneralRequest.objects.filter(
            employee=employee,
            status='Approved',
            request_type__salary_component__isnull=False
        ).select_related('request_type__salary_component')

        for request in approved_requests:
            component = request.request_type.salary_component
            if component and request.total is not None:
                amount = request.total
                # Apply leave deduction if deduct_leave is True
                if component.deduct_leave and days_absent > 0 and total_working_days > 0:
                    per_day_amount = amount / total_working_days
                    leave_deduction = per_day_amount * days_absent
                    amount = amount - leave_deduction

                PayslipComponent.objects.create(
                    payslip=payslip,
                    component=component,
                    amount=amount
                )
                if component.component_type == 'addition':
                    total_additions += amount
                elif component.component_type == 'deduction':
                    total_deductions += amount

        # Process active loans
        active_loans = LoanRequest.objects.filter(employee=employee, status='Approved')
        for loan in active_loans:
            repayment_count = LoanRepayment.objects.filter(loan=loan).count()
            if repayment_count < loan.repayment_period:
                emi_amount = loan.emi_amount
                loan_component = SalaryComponent.objects.filter(is_loan_component=True).first()
                if loan_component:
                    PayslipComponent.objects.create(
                        payslip=payslip,
                        component=loan_component,
                        amount=emi_amount
                    )
                    total_deductions += emi_amount

                    total_paid = LoanRepayment.objects.filter(loan=loan).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
                    remaining_balance = loan.amount_requested - total_paid - emi_amount

                    LoanRepayment.objects.create(
                        loan=loan,
                        payslip=payslip,
                        repayment_date=instance.payment_date,
                        amount_paid=emi_amount,
                        remaining_balance=remaining_balance
                    )
                    loan.remaining_balance = remaining_balance
                    loan.save(update_fields=['remaining_balance'])
                    if remaining_balance <= 0:
                        loan.status = 'Closed'
                        loan.save()
        # Process approved advance salary requests
        approved_advances = AdvanceSalaryRequest.objects.filter(
            employee=employee,
            status='Approved'
        )

        advance_component = SalaryComponent.objects.filter(is_advance_salary=True).first()

        for advance in approved_advances:
            if advance_component and advance.requested_amount > 0:
                amount = advance.requested_amount

                PayslipComponent.objects.create(
                    payslip=payslip,
                    component=advance_component,
                    amount=amount
                )
                total_deductions += amount

                # Optional: update advance request status or mark it as linked
                advance.status = 'Deducted'  # You can add this status or another field like `is_processed=True`
                advance.save(update_fields=['status'])
        # Process approved and unprocessed air ticket requests
        air_ticket_component = SalaryComponent.objects.filter(is_air_ticket=True).first()

        approved_tickets = AirTicketRequest.objects.filter(
            employee=employee,
            status='APPROVED',
            # is_active=True,
            request_type='ENCASHMENT'  # Only process encashments
        )

        for ticket in approved_tickets:
            if air_ticket_component:
                # You can use a field like 'allocation.amount' or define an amount in the ticket model
                amount = ticket.allocation.amount  # Assuming amount is stored here

                PayslipComponent.objects.create(
                    payslip=payslip,
                    component=air_ticket_component,
                    amount=amount
                )

                total_additions += amount

                # Mark air ticket as processed for future payrolls
                # ticket.is_active = False
                ticket.status = 'PROCESSED'
                ticket.save(update_fields=['status'])
        # Finalize payslip
        gross_salary = total_additions
        net_salary = gross_salary - total_deductions

        payslip.total_additions = total_additions
        payslip.total_deductions = total_deductions
        payslip.gross_salary = gross_salary
        payslip.net_salary = net_salary
        payslip.save()

    instance.status = 'processed'
    instance.save()

@receiver(post_save, sender=EmployeeSalaryStructure)
def update_dependent_salary_components(sender, instance, created, **kwargs):
    """
    When an EmployeeSalaryStructure's amount is updated, recalculate amounts for
    other components that use this component in their formulas.
    """
    # Only proceed if the amount was updated (not created) and the component is fixed
    if created or not instance.component.is_fixed:
        return

    # Get the EmpMaster model dynamically
    EmpMaster = apps.get_model('EmpManagement', 'emp_master')
    
    # Get all employees to update their dependent components
    employees = EmpMaster.objects.all()

    # Find all variable SalaryComponents that might depend on this component
    dependent_components = SalaryComponent.objects.filter(
        is_fixed=False,
        formula__contains=instance.component.code  # Check if the updated component's code appears in the formula
    )

    for employee in employees:
        # Fetch all salary components for this employee to use in formula evaluation
        salary_components = EmployeeSalaryStructure.objects.filter(employee=employee)
        component_amounts = {}

        # Populate fixed component amounts for formula evaluation
        for sc in salary_components:
            if sc.component.is_fixed and sc.amount is not None:
                component_amounts[sc.component.code] = float(sc.amount)
                logger.info(f"Using fixed component {sc.component.name} ({sc.component.code}): {sc.amount}")

        # Recalculate amounts for dependent components
        for component in dependent_components:
            # Calculate the new amount using the formula
            amount = evaluate_formula(component.formula, component_amounts)
            logger.info(f"Calculated amount for {component.name} ({component.code}) for employee {employee}: {amount}")

            # Update or create the EmployeeSalaryStructure entry for the dependent component
            EmployeeSalaryStructure.objects.update_or_create(
                employee=employee,
                component=component,
                defaults={
                    'amount': amount,
                    'is_active': True,
                }
            )
            logger.info(f"Updated EmployeeSalaryStructure for {employee} with component {component.name} - Amount: {amount}")

