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
from django.core.exceptions import ValidationError
from EmpManagement.models import emp_master

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
from .utils import get_ot_rate


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
    EmployeeOvertime = apps.get_model('calendars', 'EmployeeOvertime')

    if not start_date or not end_date:
        today = datetime.today().date()
        start_date = today.replace(day=1)
        end_date = today.replace(day=monthrange(today.year, today.month)[1])
    
    # Fetch PayStructure for branch-specific defaults
    PayStructure = apps.get_model("PayrollManagement", "PayStructure")
    pay_structure = PayStructure.objects.filter(branch=employee.emp_branch_id).first()

    variables = {
        'calendar_days': Decimal(str((end_date - start_date).days + 1)),
        'fixed_days': Decimal(str(pay_structure.fixed_working_days if pay_structure and pay_structure.fixed_working_days else '30.0')),
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
    overtimes = EmployeeOvertime.objects.filter(
    employee=employee,
    date__range=(start_date, end_date),
    # approved=True
    )

    variables['normal_ot_hours'] = (
        overtimes.filter(ot_type='NORMAL')
        .aggregate(s=Sum('hours'))['s'] or Decimal('0.00')
    )

    variables['weekend_ot_hours'] = (
        overtimes.filter(ot_type='WEEKEND')
        .aggregate(s=Sum('hours'))['s'] or Decimal('0.00')
    )

    variables['holiday_ot_hours'] = (
        overtimes.filter(ot_type='HOLIDAY')
        .aggregate(s=Sum('hours'))['s'] or Decimal('0.00')
    )

    # OT rate variables (Zoho-style)
    variables['ot_normal_rate'] = get_ot_rate(employee, 'NORMAL')
    variables['ot_weekend_rate'] = get_ot_rate(employee, 'WEEKEND')
    variables['ot_holiday_rate'] = get_ot_rate(employee, 'HOLIDAY')
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
    AttendanceCalendar = apps.get_model("calendars", "AttendanceCalendar")
    return AttendanceCalendar.objects.filter(
        employee=employee,
        date__range=(start_date, end_date),
        status='Present'
    ).count()
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.apps import apps
from calendar import monthrange
from datetime import datetime
from decimal import Decimal
import logging
from django.db.models.signals import m2m_changed
logger = logging.getLogger(__name__)

# PayrollManagement/signals.py
import logging
from decimal import Decimal
from calendar import monthrange
from datetime import datetime
from django.db.models import Sum, Q
from django.db.models.signals import post_save, m2m_changed,pre_save
from django.dispatch import receiver
from django.apps import apps

logger = logging.getLogger(__name__)

def get_payroll_dates_and_days(instance):

    PayStructure = apps.get_model("PayrollManagement", "PayStructure")

    total_days_in_month = monthrange(instance.year, instance.month)[1]

    start_date = date(instance.year, instance.month, 1)
    end_date = date(instance.year, instance.month, total_days_in_month)

    total_days = Decimal(str(total_days_in_month))

    pay_structure = None

    if instance.branch:
        pay_structure = PayStructure.objects.filter(
            branch=instance.branch
        ).first()

    if pay_structure:

        # ==========================================
        # CUSTOM ATTENDANCE CYCLE
        # ==========================================
        if pay_structure.attendance_cycle_type == 'CUSTOM':

            cutoff_day = pay_structure.cycle_end_day or 26

            # Current payroll month cutoff date
            try:
                end_date = date(
                    instance.year,
                    instance.month,
                    cutoff_day
                )
            except ValueError:
                last_day = monthrange(
                    instance.year,
                    instance.month
                )[1]

                end_date = date(
                    instance.year,
                    instance.month,
                    last_day
                )

            # Previous cutoff + 1 day
            prev_month = end_date - relativedelta(months=1)

            try:
                previous_cutoff = date(
                    prev_month.year,
                    prev_month.month,
                    cutoff_day
                )
            except ValueError:
                last_day_prev = monthrange(
                    prev_month.year,
                    prev_month.month
                )[1]

                previous_cutoff = date(
                    prev_month.year,
                    prev_month.month,
                    last_day_prev
                )

            start_date = previous_cutoff + relativedelta(days=1)

        # ==========================================
        # SALARY CALCULATION
        # ==========================================
        calc_type = pay_structure.salary_calculation_type

        if calc_type == 'FIXED_DAYS':

            total_days = Decimal(
                str(pay_structure.fixed_working_days or 30)
            )

        elif calc_type == 'ORGANIZATION_DAYS':

            config_working_days = (
                pay_structure.working_days or []
            )

            config_working_days = [
                d.upper() for d in config_working_days
            ]

            org_days_count = 0

            current = start_date

            while current <= end_date:

                if current.strftime('%a').upper() in config_working_days:
                    org_days_count += 1

                current += relativedelta(days=1)

            total_days = Decimal(str(org_days_count))

        elif calc_type == 'CALENDAR_DAYS':

            total_days = Decimal(
                str((end_date - start_date).days + 1)
            )

    return start_date, end_date, total_days
def process_payroll(instance, employees_qs, start_date, end_date, total_days):
    """
    Create payslips for employees_qs for the given PayrollRun instance.
    Idempotent: skips employee if Payslip already exists for payroll_run+employee.
    """
    # Resolve models
    SalaryComponent = apps.get_model("PayrollManagement", "SalaryComponent")
    EmployeeSalaryStructure = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")
    Payslip = apps.get_model("PayrollManagement", "Payslip")
    PayslipComponent = apps.get_model("PayrollManagement", "PayslipComponent")

    GeneralRequest = apps.get_model("EmpManagement", "GeneralRequest")
    LoanRequest = apps.get_model("PayrollManagement", "LoanApplication")
    LoanRepayment = apps.get_model("PayrollManagement", "LoanRepayment")
    AirTicketRequest = apps.get_model("PayrollManagement", "AirTicketRequest")
    AdvanceSalaryRequest = apps.get_model("PayrollManagement", "AdvanceSalaryRequest")
    employee_leave_request = apps.get_model("calendars", "employee_leave_request")

    for employee in employees_qs:
        # Skip if payslip already exists for this run+employee (idempotency)
        if Payslip.objects.filter(payroll_run=instance, employee=employee).exists():
            logger.info(f"Payslip exists, skipping employee {employee} for PayrollRun {instance.id}")
            continue

        try:
            variables = get_formula_variables(employee, start_date, end_date)
        except Exception as e:
            logger.exception(f"Error getting formula variables for {employee}: {e}")
            variables = {}

        # Unpaid leave calculation: Source of Truth is AttendanceCalendar
        AttendanceCalendar = apps.get_model("calendars", "AttendanceCalendar")
        
        # Aggregate unpaid_fraction from the calendar for the payroll period
        unpaid_leave_days_val = AttendanceCalendar.objects.filter(
            employee=employee,
            date__range=(start_date, end_date)
        ).aggregate(total=Sum('unpaid_fraction'))['total'] or 0
        
        unpaid_leave_days = Decimal(str(unpaid_leave_days_val))

        days_worked = Decimal(total_days) - unpaid_leave_days
        if days_worked < 0:
            days_worked = Decimal("0.00")

        # Create payslip
        payslip = Payslip.objects.create(
            payroll_run=instance,
            employee=employee,
            total_working_days=total_days,
            days_worked=days_worked,
        )
        # Record Leave Details from AttendanceCalendar
        leave_entries = AttendanceCalendar.objects.filter(
            employee=employee,
            date__range=(start_date, end_date),
            status='Leave'
        ).values('leave_type').annotate(total_days=Count('id'))

        PayslipLeave = apps.get_model("PayrollManagement", "PayslipLeave")
        for entry in leave_entries:
            if entry['leave_type']:
                lt_id = entry['leave_type']
                # Calculate actual days (handling half days if they exist in calendar)
                # But Wait, AttendanceCalendar has is_half_day. Let's do a more precise calculation.
                precise_days = Decimal("0.00")
                day_records = AttendanceCalendar.objects.filter(
                    employee=employee,
                    date__range=(start_date, end_date),
                    status='Leave',
                    leave_type_id=lt_id
                )
                for rec in day_records:
                    if rec.is_half_day:
                        precise_days += Decimal("0.5")
                    else:
                        precise_days += Decimal("1.0")
                
                PayslipLeave.objects.update_or_create(
                    payslip=payslip,
                    leave_type_id=lt_id,
                    defaults={'days': precise_days}
                )
        total_additions = Decimal("0.00")
        total_deductions = Decimal("0.00")

        # Salary structure processing
        salary_structs = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)
        for sc in salary_structs:
            comp = sc.component
            amount = Decimal("0.00")
            try:
                if comp.is_fixed:
                    amount = Decimal(str(sc.amount or "0.00"))
                elif comp.formula:
                    amount = Decimal(str(evaluate_formula(comp.formula, variables, employee, comp)))
                else:
                    amount = Decimal(str(sc.amount or "0.00"))
            except Exception as e:
                logger.exception(f"Error calculating component {comp} for {employee}: {e}")
                amount = Decimal("0.00")

            # Deduct unpaid leave only from components flagged for it
            if getattr(comp, "deduct_leave", False) and unpaid_leave_days > 0 and total_days > 0:
                per_day = amount / Decimal(total_days)
                amount -= per_day * unpaid_leave_days

            PayslipComponent.objects.update_or_create(
                payslip=payslip, component=comp, defaults={"amount": amount}
            )

            if getattr(comp, "component_type", "") == "addition":
                total_additions += amount
            elif getattr(comp, "component_type", "") == "deduction":
                total_deductions += amount

        # GeneralRequests that affect salary
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
                if getattr(comp, "component_type", "") == "addition":
                    total_additions += amount
                else:
                    total_deductions += amount
                request.is_processed = True
                request.save(update_fields=["is_processed"])

        # Loans
        active_loans = LoanRequest.objects.filter(employee=employee, status="Approved")
        for loan in active_loans:
            repayment_count = LoanRepayment.objects.filter(loan=loan).count()
            if repayment_count < loan.repayment_period:
                emi_amount = loan.emi_amount
                # loan_component = SalaryComponent.objects.filter(is_loan_component=True).first()
                loan_component = SalaryComponent.objects.filter(special_component_type='loan').first()

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

        # Advance Salary
        # advance_component = SalaryComponent.objects.filter(is_advance_salary=True).first()
        advance_component = SalaryComponent.objects.filter(special_component_type='advance_salary').first()
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

        # Air tickets
        # air_ticket_component = SalaryComponent.objects.filter(is_air_ticket=True).first()
        air_ticket_component = SalaryComponent.objects.filter(special_component_type='air_ticket').first()
        approved_tickets = AirTicketRequest.objects.filter(
            employee=employee, status="APPROVED", request_type="ENCASHMENT"
        )
        for ticket in approved_tickets:
            if air_ticket_component and getattr(ticket, "allocation", None):
                amount = Decimal(str(ticket.allocation.amount))
                PayslipComponent.objects.update_or_create(
                    payslip=payslip, component=air_ticket_component, defaults={"amount": amount}
                )
                total_additions += amount
                ticket.status = "PROCESSED"
                ticket.save(update_fields=["status"])

        # Reset manual variable components
        EmployeeSalaryStructure.objects.filter(
            employee=employee,
            is_active=True,
            component__is_fixed=False,
        ).filter(
            Q(component__formula__isnull=True) | Q(component__formula__exact="")
        ).update(amount=Decimal("0.00"))

        # Finalize payslip totals
        payslip.total_additions = total_additions
        payslip.total_deductions = total_deductions
        payslip.gross_salary = total_additions
        payslip.net_salary = total_additions - total_deductions
        payslip.save()

    # Mark run processed (caller may prefer to control this; keep as you had)
    instance.status = "processed"
    instance.save(update_fields=["status"])


# ---------- post_save handler: for runs that are NOT employee-wise (no M2M provided) ----------
@receiver(post_save, sender="PayrollManagement.PayrollRun")
def payrollrun_post_save(sender, instance, created, **kwargs):
    """
    Trigger payroll when a PayrollRun is created and employees M2M is not used.
    If employees are later added via M2M, m2m_changed handler will handle that case.
    """
    if not created:
        return

    # Only trigger if status is pending (your original check)
    if instance.status != "pending":
        return

    EmpMaster = apps.get_model("EmpManagement", "emp_master")

    # Calculate dates and total days based on PayStructure
    try:
        start_date, end_date, total_days = get_payroll_dates_and_days(instance)
    except Exception as e:
        logger.exception(f"Invalid date for PayrollRun {getattr(instance, 'id', None)}: {e}")
        return

    # If employees were set already (unlikely in post_save because M2M isn't saved yet),
    # prefer employee list. Otherwise use branch/department/all approach.
    if hasattr(instance, "employees") and instance.employees.exists():
        employees_qs = instance.employees.all()
    elif getattr(instance, "branch", None):
        EmpMaster = apps.get_model("EmpManagement", "emp_master")
        employees_qs = EmpMaster.objects.filter(is_active=True, emp_branch_id=instance.branch_id)
    elif getattr(instance, "department", None):
        EmpMaster = apps.get_model("EmpManagement", "emp_master")
        employees_qs = EmpMaster.objects.filter(is_active=True, emp_dept_id=instance.department_id)
    else:
        EmpMaster = apps.get_model("EmpManagement", "emp_master")
        employees_qs = EmpMaster.objects.filter(is_active=True)

    if not employees_qs.exists():
        logger.warning(f"No employees found for PayrollRun {instance.id} in post_save path")
        return

    # Process payroll for the chosen set
    process_payroll(instance, employees_qs, start_date, end_date, total_days)


# ---------- m2m_changed handler: fires after employees are added to M2M ----------
def payrollrun_m2m_changed(sender, instance, action, pk_set, **kwargs):
    """
    Triggered when M2M 'employees' changes. We only act on post_add,
    i.e. after employees have been attached to a PayrollRun.
    """
    if action != "post_add":
        return

    # Only process pending runs
    if instance.status != "pending":
        logger.info(f"PayrollRun {instance.id} status is {instance.status}; skipping m2m processing.")
        return

    # Calculate dates and total days based on PayStructure
    try:
        start_date, end_date, total_days = get_payroll_dates_and_days(instance)
    except Exception as e:
        logger.exception(f"Invalid date for PayrollRun {getattr(instance,'id', None)} in m2m handler: {e}")
        return

    # employees have been added; process only those employees attached to instance
    employees_qs = instance.employees.all()
    if not employees_qs.exists():
        logger.warning(f"No employees in PayrollRun {instance.id} after m2m post_add")
        return

    process_payroll(instance, employees_qs, start_date, end_date, total_days)


# Connect m2m handler to the through model. We fetch PayrollRun model and connect here.
try:
    PayrollRun = apps.get_model("PayrollManagement", "PayrollRun")
    # connect handler to the through model for the employees m2m
    m2m_changed.connect(payrollrun_m2m_changed, sender=PayrollRun.employees.through)
except Exception as e:
    # When this file is imported earlier than app registry ready, apps.get_model might fail
    # but your apps.py should import signals in ready() so this normally won't happen.
    logger.exception(f"Could not connect m2m_changed for PayrollRun.employees: {e}")

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

