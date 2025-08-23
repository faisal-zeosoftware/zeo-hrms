from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import PayslipComponent, LoanRepayment, EmployeeSalaryStructure,SalaryComponent,AdvanceSalaryRequest
from calendars.models import Attendance,LeaveEncashmentTransaction
from django.db.models import Q
import logging
from datetime import datetime
from datetime import timedelta
from calendar import monthrange
import re
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


def evaluate_formula(formula, variables, employee, component):
    try:
        logger.debug(
            f"Evaluating formula: {formula} with variables: {variables} for employee: {employee}"
        )
        formula = formula.strip("'")

        # 🔑 Convert all numbers into Decimal("...")
        formula = re.sub(r'(\d+\.\d+|\d+)', r'Decimal("\1")', formula)

        s = SimpleEval()
        s.names = variables
        s.functions = {"Decimal": Decimal}  # allow Decimal inside eval

        # ✅ Custom operators
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
            '%': lambda x, y: x % y,
        })

        # ✅ Extended IF (works like CASE WHEN)
        def IF(*args):
            """
            Supports:
            - IF(cond, true_val, false_val)   → normal
            - IF(cond1, val1, cond2, val2, ..., default_val) → CASE-like
            """
            n = len(args)
            if n < 3:
                raise ValueError("Invalid IF usage")
            # Pairwise check (cond, val)
            for i in range(0, n - 1, 2):
                if args[i]:
                    return args[i+1]
            return args[-1]  # default

        # ✅ Custom functions
        s.functions.update({
            "MAX": max,
            "MIN": min,
            "AVG": lambda *args: sum(args) / len(args) if args else Decimal("0.00"),
            "SUM": sum,
            "ROUND": lambda val, ndigits=2: val.quantize(Decimal("1." + "0"*ndigits)) 
                if isinstance(val, Decimal) else round(val, ndigits),
            "IF": IF,
        })

        result = s.eval(formula)

        # Ensure result is Decimal
        if not isinstance(result, Decimal):
            result = Decimal(str(result))

        return result.quantize(Decimal("0.00"))

    except (NameNotDefined, FunctionNotDefined) as e:
        logger.error(
            f"Invalid variable or function in formula '{formula}' for employee {employee}: {e}"
        )
        return Decimal("0.00")
    except Exception as e:
        logger.error(
            f"Error evaluating formula '{formula}' for employee {employee}: {e}"
        )
        return Decimal("0.00")
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
    AirTicketAllocation = apps.get_model('PayrollManagement', 'AirTicketAllocation')

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
        employee=employee, date__range=(start_date, end_date)
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

    # salary_components = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)
    # for sc in salary_components:
    #     if sc.component and sc.amount is not None:
    #         variables[sc.component.code] = Decimal(str(sc.amount))
    salary_structs = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)

    # First add fixed components
    for sc in salary_structs:
        if sc.component.is_fixed and sc.amount is not None:
            variables[sc.component.code] = Decimal(sc.amount)

    # Then evaluate formula-based components and add them too
    for sc in salary_structs:
        comp = sc.component
        if not comp.is_fixed and comp.formula:
            try:
                val = evaluate_formula(comp.formula, variables, employee, comp)
                # Ensure it's always Decimal
                variables[comp.code] = Decimal(str(val))
            except Exception as e:
                logger.error(f"Formula error for {comp.name} in get_formula_variables: {e}")
                variables[comp.code] = Decimal("0.00")
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
@receiver(post_save, sender="PayrollManagement.PayrollRun")
def run_payroll_on_save(sender, instance, created, **kwargs):
    """
    Payroll run logic:
    - Fixed components → EmployeeSalaryStructure.amount
    - Variable formula components → recalculated each payroll
    - Variable manual entry → used once, reset to 0
    - General Requests, Loans, Advance Salary, Air Ticket included
    - Deduct unpaid leaves only from components with deduct_leave=True
    """
    if not created or instance.status != "pending":
        return

    EmpMaster = apps.get_model("EmpManagement", "emp_master")
    SalaryComponent = apps.get_model("PayrollManagement", "SalaryComponent")
    EmployeeSalaryStructure = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")
    Payslip = apps.get_model("PayrollManagement", "Payslip")
    PayslipComponent = apps.get_model("PayrollManagement", "PayslipComponent")
    employee_leave_request = apps.get_model("calendars", "employee_leave_request")

    # Extra models
    GeneralRequest = apps.get_model("EmpManagement", "GeneralRequest")
    LoanRequest = apps.get_model("PayrollManagement", "LoanApplication")
    LoanRepayment = apps.get_model("PayrollManagement", "LoanRepayment")
    AirTicketRequest = apps.get_model("PayrollManagement", "AirTicketRequest")
    AdvanceSalaryRequest = apps.get_model("PayrollManagement", "AdvanceSalaryRequest")

    try:
        total_days = monthrange(instance.year, instance.month)[1]
        start_date = datetime(instance.year, instance.month, 1).date()
        end_date = datetime(instance.year, instance.month, total_days).date()
    except Exception as e:
        logger.error(f"Invalid date setup for PayrollRun {instance.id}: {e}")
        return

    employees = EmpMaster.objects.filter(is_active=True)

    for employee in employees:
        variables = get_formula_variables(employee, start_date, end_date)

        # ===== Unpaid Leave Calculation =====
        approved_unpaid_leaves = employee_leave_request.objects.filter(
            employee=employee,
            status="approved",
            leave_type__type="unpaid",
            start_date__lte=end_date,
            end_date__gte=start_date,
        )

        leave_days = Decimal("0.00")
        for leave in approved_unpaid_leaves:
            if leave.dis_half_day:  
                leave_days += Decimal("0.5")   # ✅ half-day leave
            else:
                leave_days += Decimal(str(leave.number_of_days or 0))

        unpaid_leave_days = leave_days
        days_worked = Decimal(total_days) - unpaid_leave_days
        if days_worked < 0:
            days_worked = Decimal("0.00")
        # ===== Create Payslip =====
        payslip = Payslip.objects.create(
            payroll_run=instance,
            employee=employee,
            total_working_days=total_days,
            days_worked=days_worked,
        )

        total_additions = Decimal("0.00")
        total_deductions = Decimal("0.00")

        # ===== Employee Salary Structure =====
        salary_structs = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)

        for sc in salary_structs:
            comp = sc.component
            amount = Decimal("0.00")

            if comp.is_fixed:
                amount = Decimal(str(sc.amount or "0.00"))

            elif comp.formula:  # formula variable
                amount = Decimal(str(evaluate_formula(comp.formula, variables, employee, comp)))

            else:  # manual variable
                amount = Decimal(str(sc.amount or "0.00"))

            # Deduct unpaid leave only from marked components
            if comp.deduct_leave and unpaid_leave_days > 0 and total_days > 0:
                per_day = amount / Decimal(total_days)
                amount -= per_day * unpaid_leave_days

            PayslipComponent.objects.update_or_create(
                payslip=payslip, component=comp, defaults={"amount": amount}
            )

            if comp.component_type == "addition":
                total_additions += amount
            elif comp.component_type == "deduction":
                total_deductions += amount

        # ===== General Requests =====
        approved_requests = GeneralRequest.objects.filter(
            employee=employee,
            status="Approved",
            is_processed=False,
            request_type__salary_component__isnull=False,
        ).select_related("request_type__salary_component")

        for request in approved_requests:
            comp = request.request_type.salary_component
            if comp and request.total is not None:
                amount = Decimal(str(request.total))

                PayslipComponent.objects.update_or_create(
                    payslip=payslip, component=comp, defaults={"amount": amount}
                )

                if comp.component_type == "addition":
                    total_additions += amount
                else:
                    total_deductions += amount

                request.is_processed = True
                request.save(update_fields=["is_processed"])

        # ===== Loan Requests =====
        active_loans = LoanRequest.objects.filter(employee=employee, status="Approved")
        for loan in active_loans:
            repayment_count = LoanRepayment.objects.filter(loan=loan).count()
            if repayment_count < loan.repayment_period:
                emi_amount = loan.emi_amount
                loan_component = SalaryComponent.objects.filter(is_loan_component=True).first()
                if loan_component:
                    PayslipComponent.objects.update_or_create(
                        payslip=payslip, component=loan_component, defaults={"amount": emi_amount}
                    )
                    total_deductions += emi_amount

                    total_paid = LoanRepayment.objects.filter(loan=loan).aggregate(
                        total=Sum("amount_paid")
                    )["total"] or Decimal("0.00")
                    remaining_balance = loan.amount_requested - total_paid - emi_amount

                    LoanRepayment.objects.create(
                        loan=loan,
                        payslip=payslip,
                        repayment_date=instance.payment_date,
                        amount_paid=emi_amount,
                        remaining_balance=remaining_balance,
                    )
                    loan.remaining_balance = remaining_balance
                    loan.save(update_fields=["remaining_balance"])
                    if remaining_balance <= 0:
                        loan.status = "Closed"
                        loan.save()

        # ===== Advance Salary Requests =====
        advance_component = SalaryComponent.objects.filter(is_advance_salary=True).first()
        approved_advances = AdvanceSalaryRequest.objects.filter(employee=employee, status="Approved")

        for advance in approved_advances:
            if advance_component and advance.requested_amount > 0:
                amount = Decimal(str(advance.requested_amount))
                PayslipComponent.objects.update_or_create(
                    payslip=payslip, component=advance_component, defaults={"amount": amount}
                )
                total_deductions += amount
                advance.status = "Deducted"
                advance.save(update_fields=["status"])

        # ===== Air Ticket Requests =====
        air_ticket_component = SalaryComponent.objects.filter(is_air_ticket=True).first()
        approved_tickets = AirTicketRequest.objects.filter(
            employee=employee, status="APPROVED", request_type="ENCASHMENT"
        )

        for ticket in approved_tickets:
            if air_ticket_component and ticket.allocation:
                amount = Decimal(str(ticket.allocation.amount))
                PayslipComponent.objects.update_or_create(
                    payslip=payslip, component=air_ticket_component, defaults={"amount": amount}
                )
                total_additions += amount
                ticket.status = "PROCESSED"
                ticket.save(update_fields=["status"])

        # ===== Reset Manual Variable Components =====
        EmployeeSalaryStructure.objects.filter(
            employee=employee,
            is_active=True,
            component__is_fixed=False,
        ).filter(
            Q(component__formula__isnull=True) | Q(component__formula__exact="")
        ).update(amount=Decimal("0.00"))

        # ===== Finalize Payslip =====
        payslip.total_additions = total_additions
        payslip.total_deductions = total_deductions
        payslip.gross_salary = total_additions
        payslip.net_salary = total_additions - total_deductions
        payslip.save()

    instance.status = "processed"
    instance.save()

@receiver(post_save, sender="PayrollManagement.EmployeeSalaryStructure")
def update_dependents_on_fixed_change(sender, instance, **kwargs):
    """
    If a fixed component changes (e.g. Basic), recalc dependent formula components for that employee.
    """
    if not instance.component.is_fixed:
        return

    SalaryComponent = apps.get_model("PayrollManagement", "SalaryComponent")
    EmployeeSalaryStructure = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")

    formula_components = SalaryComponent.objects.filter(is_fixed=False, formula__isnull=False)

    for comp in formula_components:
        if comp.formula and instance.component.code in comp.formula:
            try:
                emp_struct, _ = EmployeeSalaryStructure.objects.get_or_create(
                    employee=instance.employee, component=comp
                )
                emp_struct.amount = evaluate_formula(
                    comp.formula, get_formula_variables(instance.employee, datetime.today(), datetime.today()), instance.employee, comp
                )
                emp_struct.save(update_fields=["amount"])
            except Exception as e:
                logger.error(f"Error updating dependent component {comp.name} for {instance.employee}: {e}")
