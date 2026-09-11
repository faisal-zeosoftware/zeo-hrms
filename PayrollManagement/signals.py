

from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import PayslipComponent, LoanRepayment,SalaryStructure, EmployeeSalaryStructure,SalaryComponent,AdvanceSalaryRequest
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
from .utils import get_ot_rate,evaluate_formula,get_gratuity_variables
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

from datetime import date
from decimal import Decimal
from calendar import monthrange
import logging

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db.models import Sum, Q, Count
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


# ============================================================
# PAYROLL DATE / DAYS CALCULATION
# ============================================================

def get_payroll_dates_and_days(instance, branch=None):
    PayStructure = apps.get_model(
        "PayrollManagement",
        "PayStructure"
    )

    total_days_in_month = monthrange(
        instance.year,
        instance.month
    )[1]

    start_date = date(
        instance.year,
        instance.month,
        1
    )

    end_date = date(
        instance.year,
        instance.month,
        total_days_in_month
    )

    total_days = Decimal(
        str(total_days_in_month)
    )

    pay_structure = None

    # IMPORTANT:
    # PayrollRun.branch is ManyToMany.
    # Therefore a specific branch must be passed here.
    if branch:
        pay_structure = (
            PayStructure.objects
            .filter(branch=branch)
            .first()
        )

    if not pay_structure:
        return start_date, end_date, total_days

    # ========================================================
    # CUSTOM ATTENDANCE CYCLE
    # ========================================================

    if pay_structure.attendance_cycle_type == "CUSTOM":
        cutoff_day = (
            pay_structure.cycle_end_day or 26
        )

        # Current payroll month cutoff
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

        # Previous month cutoff + 1
        prev_month = (
            end_date -
            relativedelta(months=1)
        )

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

        start_date = (
            previous_cutoff +
            relativedelta(days=1)
        )

    # ========================================================
    # SALARY CALCULATION
    # ========================================================

    calc_type = pay_structure.salary_calculation_type

    if calc_type == "FIXED_DAYS":

        total_days = Decimal(
            str(
                pay_structure.fixed_working_days or 30
            )
        )

    elif calc_type == "ORGANIZATION_DAYS":

        config_working_days = (
            pay_structure.working_days or []
        )

        config_working_days = [
            str(day).upper()
            for day in config_working_days
        ]

        org_days_count = 0
        current = start_date

        while current <= end_date:
            if (
                current.strftime("%a").upper()
                in config_working_days
            ):
                org_days_count += 1

            current += relativedelta(days=1)

        total_days = Decimal(
            str(org_days_count)
        )

    elif calc_type == "CALENDAR_DAYS":

        total_days = Decimal(
            str(
                (end_date - start_date).days + 1
            )
        )

    return start_date, end_date, total_days


# ============================================================
# PAYSLIP TOTALS
# ============================================================

def calculate_payslip_totals(payslip):
    """
    Calculate payslip totals using ONLY components where
    SalaryComponent.show_in_payslip=True.

    Hidden components:
        - remain stored in PayslipComponent
        - available for reports
        - do not affect additions
        - do not affect deductions
        - do not affect gross
        - do not affect net
    """

    components = (
        payslip.components
        .select_related("component")
        .all()
    )

    total_additions = Decimal("0.00")
    total_deductions = Decimal("0.00")

    for payslip_component in components:

        component = payslip_component.component

        amount = Decimal(
            str(
                payslip_component.amount or "0.00"
            )
        )

        if not component.show_in_payslip:
            continue

        if component.component_type == "addition":
            total_additions += amount

        elif component.component_type == "deduction":
            total_deductions += amount

    gross_salary = total_additions

    net_salary = (
        total_additions -
        total_deductions
    )

    payslip.total_additions = total_additions
    payslip.total_deductions = total_deductions
    payslip.gross_salary = gross_salary
    payslip.net_salary = net_salary

    payslip.save(
        update_fields=[
            "total_additions",
            "total_deductions",
            "gross_salary",
            "net_salary",
        ]
    )

    return {
        "total_additions": total_additions,
        "total_deductions": total_deductions,
        "gross_salary": gross_salary,
        "net_salary": net_salary,
    }


# ============================================================
# PROCESS COMPLETE PAYROLL RUN
# ============================================================

def process_payroll_run(instance):
    """
    Process a PayrollRun branch-by-branch.

    Each branch gets:
        - its own PayStructure
        - its own payroll dates
        - its own total days
        - its own employees

    The PayrollRun is marked processed only after all
    branches have been processed successfully.
    """

    if instance.status != "pending":
        logger.info(
            f"PayrollRun {instance.id} status is "
            f"{instance.status}; skipping."
        )
        return

    branches = list(
        instance.branch.all()
    )

    if not branches:
        logger.warning(
            f"No branches assigned to PayrollRun "
            f"{instance.id}"
        )
        return

    processed_any = False

    for branch in branches:

        logger.info(
            f"Starting PayrollRun {instance.id} "
            f"for branch {branch.id} "
            f"({branch.branch_name})"
        )

        start_date, end_date, total_days = (
            get_payroll_dates_and_days(
                instance,
                branch=branch
            )
        )

        employees_qs = (
            instance
            .get_employees()
            .filter(
                is_active=True,
                emp_branch=branch
            )
            .distinct()
        )

        employee_count = employees_qs.count()

        if employee_count == 0:
            logger.warning(
                f"No employees found for PayrollRun "
                f"{instance.id}, branch {branch.id}"
            )
            continue

        logger.info(
            f"PayrollRun {instance.id} | "
            f"Branch={branch.branch_name} | "
            f"Employees={employee_count} | "
            f"Start={start_date} | "
            f"End={end_date} | "
            f"Days={total_days}"
        )

        process_payroll(
            instance,
            employees_qs,
            start_date,
            end_date,
            total_days
        )

        processed_any = True

    if processed_any:
        instance.status = "processed"

        instance.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        logger.info(
            f"PayrollRun {instance.id} "
            f"marked as processed."
        )


# ============================================================
# PAYROLL RUN POST SAVE
# ============================================================

@receiver(
    post_save,
    sender="PayrollManagement.PayrollRun"
)
def payrollrun_post_save(
    sender,
    instance,
    created,
    **kwargs
):
    """
    PayrollRun creation signal.

    IMPORTANT:
    Do NOT process payroll here because M2M fields such as
    branch/employees are not guaranteed to be available yet.

    Payroll processing is triggered from the ViewSet after
    all M2M relationships and branch documents are created.
    """

    if not created:
        return

    if instance.status != "pending":
        return

    logger.info(
        f"PayrollRun {instance.id} created. "
        f"Waiting for M2M assignments before processing."
    )



logger = logging.getLogger(__name__)

from django.db.models.signals import m2m_changed
from django.dispatch import receiver
@receiver(m2m_changed, sender=SalaryStructure.employees.through)
def sync_employee_components_on_employee_change(sender, instance, action, pk_set, **kwargs):
    """
    Keep EmployeeSalaryStructure rows in sync when employees are
    added to or removed from a SalaryStructure.
    """
    if action == "post_add":
        components = instance.components.all()
        for emp_id in pk_set:
            for component in components:
                obj, created = EmployeeSalaryStructure.objects.get_or_create(
                    employee_id=emp_id,
                    component=component,
                    defaults={'amount': 0.00, 'is_active': True}
                )
                if not created and not obj.is_active:
                    # Employee was previously detached — reactivate instead of erroring
                    obj.is_active = True
                    obj.save(update_fields=['is_active'])

    elif action in ("post_remove", "pre_clear"):
        # pk_set is None on pre_clear, so resolve it from the relation first
        emp_ids = pk_set if pk_set is not None else set(instance.employees.values_list('id', flat=True))
        components = instance.components.all()
        EmployeeSalaryStructure.objects.filter(
            employee_id__in=emp_ids,
            component__in=components
        ).update(is_active=False)  # soft-deactivate, don't hard delete (keeps payroll history intact)


@receiver(m2m_changed, sender=SalaryStructure.components.through)
def sync_employee_components_on_component_change(sender, instance, action, pk_set, **kwargs):
    """
    Keep EmployeeSalaryStructure rows in sync when components are
    added to or removed from a SalaryStructure that already has employees.
    """
    if action == "post_add":
        employees = instance.employees.all()
        components = SalaryComponent.objects.filter(pk__in=pk_set)
        for component in components:
            for emp in employees:
                obj, created = EmployeeSalaryStructure.objects.get_or_create(
                    employee=emp,
                    component=component,
                    defaults={'amount': 0.00, 'is_active': True}
                )
                if not created and not obj.is_active:
                    obj.is_active = True
                    obj.save(update_fields=['is_active'])

    elif action in ("post_remove", "pre_clear"):
        comp_ids = pk_set if pk_set is not None else set(instance.components.values_list('id', flat=True))
        employees = instance.employees.all()
        EmployeeSalaryStructure.objects.filter(
            employee__in=employees,
            component_id__in=comp_ids
        ).update(is_active=False)


@receiver(post_save, sender=SalaryComponent)
def update_employee_salary_structure(sender, instance, created, **kwargs):
    # if not instance.is_fixed and instance.formula:
    if instance.component_value_type == "variable" and instance.formula:
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
    EmpLeaveBalance = apps.get_model('calendars','emp_leave_balance')

    Attendance = apps.get_model('calendars', 'Attendance')
    EmployeeOvertime = apps.get_model('calendars', 'EmployeeOvertime')
    EmployeeSalaryStructure = apps.get_model(
        'PayrollManagement',
        'EmployeeSalaryStructure'
    )
    AirTicketRequest = apps.get_model(
        'PayrollManagement',
        'AirTicketRequest'
    )
    LeaveEncashmentTransaction = apps.get_model(
        'calendars',
        'LeaveEncashmentTransaction'
    )

    if not start_date or not end_date:
        today = datetime.today().date()
        start_date = today.replace(day=1)
        end_date = today.replace(
            day=monthrange(today.year, today.month)[1]
        )

    PayStructure = apps.get_model(
        "PayrollManagement",
        "PayStructure"
    )

    pay_structure = PayStructure.objects.filter(
        branch=employee.emp_branch_id
    ).first()

    variables = {
        'calendar_days': Decimal(
            str((end_date - start_date).days + 1)
        ),
        'fixed_days': Decimal(
            str(
                pay_structure.fixed_working_days
                if pay_structure
                and pay_structure.fixed_working_days
                else '30.0'
            )
        ),
        'standard_hours': Decimal('160.0'),
    }

    # -----------------------------------------------------
    # OVERTIME
    # -----------------------------------------------------
    ot_filter = {
        'employee': employee,
        'date__range': (start_date, end_date)
    }
    
    if pay_structure:
        if pay_structure.overtime_source == 'ATTENDANCE':
            ot_filter['source'] = 'ATTENDANCE'
        elif pay_structure.overtime_source == 'MANUAL':
            ot_filter['source'] = 'MANUAL'

    variables['ot_hours'] = (
        EmployeeOvertime.objects.filter(**ot_filter).aggregate(
            total_hours=Sum('hours')
        )['total_hours']
        or Decimal('0.00')
    )
    # variables['ot_hours'] = (
    #     EmployeeOvertime.objects.filter(
    #         employee=employee,
    #         date__range=(start_date, end_date)
    #     ).aggregate(
    #         total_hours=Sum('hours')
    #     )['total_hours']
    #     or Decimal('0.00')
    # )

    # -----------------------------------------------------
    # AIR TICKET ENCASHMENT
    # -----------------------------------------------------

    variables['air_ticket_encashment'] = (
        AirTicketRequest.objects.filter(
            employee=employee,
            request_type='ENCASHMENT',
            status='APPROVED',
            request_date__range=(start_date, end_date)
        ).aggregate(
            total_encashment=Sum('allocation__amount')
        )['total_encashment']
        or Decimal('0.00')
    )

    # -----------------------------------------------------
    # WEEKEND / HOLIDAY DAYS
    # -----------------------------------------------------

    weekend_days = get_employee_weekend_days(employee)
    holiday_dates = get_employee_holidays(
        employee,
        start_date,
        end_date
    )

    weekend_ot_days = 0
    holiday_ot_days = 0

    for single_date in daterange(start_date, end_date):

        weekday = single_date.strftime("%A")

        is_weekend = weekday in weekend_days
        is_holiday = single_date in holiday_dates

        attended = Attendance.objects.filter(
            employee=employee,
            date=single_date
        ).exists()

        if is_weekend and attended:
            weekend_ot_days += 1

        elif is_holiday and attended:
            holiday_ot_days += 1

    variables['weekend_ot_days'] = Decimal(weekend_ot_days)
    variables['holiday_ot_days'] = Decimal(holiday_ot_days)
    variables['holiday_weekend_ot_days'] = Decimal(
        weekend_ot_days + holiday_ot_days
    )

    working_days = get_working_days(
        employee,
        start_date,
        end_date
    )

    variables['working_days'] = float(working_days)

    # -----------------------------------------------------
    # EMPLOYEE VARIABLES
    # -----------------------------------------------------

    variables['employee.grade'] = str(
        getattr(employee, 'grade', '')
    )

    variables['employee.employee_type'] = str(
        getattr(employee, 'employee_type', '')
    )

    variables['employee.emp_joined_date'] = (
        employee.emp_joined_date.strftime('%Y-%m-%d')
        if getattr(employee, 'emp_joined_date', None)
        else ''
    )

    # -----------------------------------------------------
    # YEARS OF SERVICE
    # -----------------------------------------------------

    years_of_service = Decimal('0.00')

    if employee.emp_joined_date:

        service_days = (
            end_date - employee.emp_joined_date
        ).days

        if service_days > 0:
            years_of_service = (
                Decimal(str(service_days))
                / Decimal('365')
            )

    variables['years_of_service'] = years_of_service

    # -----------------------------------------------------
    # LEAVE ENCASHMENT
    # -----------------------------------------------------

    encashment_amount = (
        LeaveEncashmentTransaction.objects.filter(
            employee=employee,
            reset_date__range=(start_date, end_date)
        ).aggregate(
            total_encashment=Sum('encashment_amount')
        )['total_encashment']
        or Decimal('0.00')
    )

    variables['encashed_days'] = encashment_amount

    # -----------------------------------------------------
    # OVERTIME BREAKDOWN
    # -----------------------------------------------------

    # overtimes = EmployeeOvertime.objects.filter(
    #     employee=employee,
    #     date__range=(start_date, end_date),
    # )
    overtimes = EmployeeOvertime.objects.filter(**ot_filter)

    variables['normal_ot_hours'] = (
        overtimes.filter(
            ot_type='NORMAL'
        ).aggregate(
            s=Sum('hours')
        )['s']
        or Decimal('0.00')
    )

    variables['weekend_ot_hours'] = (
        overtimes.filter(
            ot_type='WEEKEND'
        ).aggregate(
            s=Sum('hours')
        )['s']
        or Decimal('0.00')
    )

    variables['holiday_ot_hours'] = (
        overtimes.filter(
            ot_type='HOLIDAY'
        ).aggregate(
            s=Sum('hours')
        )['s']
        or Decimal('0.00')
    )

    variables['ot_normal_rate'] = get_ot_rate(
        employee,
        'NORMAL'
    )

    variables['ot_weekend_rate'] = get_ot_rate(
        employee,
        'WEEKEND'
    )

    variables['ot_holiday_rate'] = get_ot_rate(
        employee,
        'HOLIDAY'
    )

    # -----------------------------------------------------
    # SALARY STRUCTURE
    # -----------------------------------------------------

    salary_structs = EmployeeSalaryStructure.objects.filter(
        employee=employee,
        is_active=True
    )

    # Fixed Components First
    for sc in salary_structs:

        if (
            sc.component.component_value_type == 'fixed'
            and sc.amount is not None
        ):
            variables[sc.component.code] = Decimal(
                str(sc.amount)
            )

    # -----------------------------------------------------
    # BASIC SALARY
    # -----------------------------------------------------

    basic_salary = Decimal('0.00')

    basic_component = salary_structs.filter(
        component__payroll_category='basic'
    ).first()

    if basic_component and basic_component.amount:
        basic_salary = Decimal(
            str(basic_component.amount)
        )

    variables['basic_salary'] = basic_salary

    # -----------------------------------------------------
    # GRATUITY VARIABLES
    # -----------------------------------------------------

    gratuity_vars = get_gratuity_variables(
        employee=employee,
        years_of_service=years_of_service,
        gratuity_type='resignation',
        basic_salary=basic_salary
    )

    variables.update(gratuity_vars)

    # -----------------------------------------------------
    # FORMULA COMPONENTS
    # -----------------------------------------------------

    for sc in salary_structs:

        comp = sc.component

        if (
            comp.component_value_type != 'fixed'
            and comp.formula
        ):
            try:

                value = evaluate_formula(
                    comp.formula,
                    variables,
                    employee,
                    comp
                )

                variables[comp.code] = Decimal(
                    str(value)
                )

            except Exception as e:

                logger.error(
                    f"Formula error for "
                    f"{comp.name} : {e}"
                )

                variables[comp.code] = Decimal('0.00')
        # ---------------------------------------------------------

    EmpLeaveBalance = apps.get_model(
    'calendars',
    'emp_leave_balance'
    )

    leave_balances = EmpLeaveBalance.objects.filter(
        employee=employee
    ).select_related('leave_type')

    for lb in leave_balances:

        balance = Decimal(
            str(lb.balance or 0)
        )

        leave_type = lb.leave_type

        # Get leave type code
        leave_code = getattr(
            leave_type,
            'code',
            None
        )

        # If your leave_type model uses leave_type field
        if not leave_code:
            leave_code = getattr(
                leave_type,
                'leave_type',
                ''
            )

        # Make code safe for Python formula
        safe_leave_code = str(leave_code).strip()

        safe_leave_code = (
            safe_leave_code
            .replace('-', '_')
            .replace(' ', '_')
            .replace('/', '_')
            .replace('.', '_')
        )

        # Example:
        # AL-TRA -> al_tra
        # Annual Leave -> annual_leave

        safe_leave_code = safe_leave_code.lower()

        variable_name = f'leave_balance_{safe_leave_code}'

        variables[variable_name] = balance

        logger.debug(
            f"Leave balance variable created: "
            f"{variable_name} = {balance}"
        )
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



from decimal import Decimal
from django.apps import apps
from django.db.models import Sum, Q, Count
import logging

logger = logging.getLogger(__name__)





def process_payroll(
    instance,
    employees_qs,
    start_date,
    end_date,
    total_days
):
    """
    Create payslips for employees in a PayrollRun.

    Rules
    -----
    1. Every salary component is calculated and stored in
       PayslipComponent.

    2. show_in_payslip=True:
           - shown in normal payslip
           - included in additions/deductions
           - included in gross/net

    3. show_in_payslip=False:
           - stored in PayslipComponent
           - available in detailed reports
           - NOT included in additions
           - NOT included in deductions
           - NOT included in gross/net

    4. payroll_category is used to identify special components.
       Example:
           gratuity
           leave_encashment
           air_ticket
           loan
           advance_salary

    5. Payslip totals are calculated only once at the end
       from PayslipComponent records.

    6. Idempotent:
       Existing payslip for the same PayrollRun + Employee
       is skipped.
    """

    # ========================================================
    # RESOLVE MODELS
    # ========================================================

    SalaryComponent = apps.get_model(
        "PayrollManagement",
        "SalaryComponent"
    )

    EmployeeSalaryStructure = apps.get_model(
        "PayrollManagement",
        "EmployeeSalaryStructure"
    )

    Payslip = apps.get_model(
        "PayrollManagement",
        "Payslip"
    )

    PayslipComponent = apps.get_model(
        "PayrollManagement",
        "PayslipComponent"
    )

    PayslipLeave = apps.get_model(
        "PayrollManagement",
        "PayslipLeave"
    )

    GeneralRequest = apps.get_model(
        "EmpManagement",
        "GeneralRequest"
    )

    LoanRequest = apps.get_model(
        "PayrollManagement",
        "LoanApplication"
    )

    LoanRepayment = apps.get_model(
        "PayrollManagement",
        "LoanRepayment"
    )

    AirTicketRequest = apps.get_model(
        "PayrollManagement",
        "AirTicketRequest"
    )

    AdvanceSalaryRequest = apps.get_model(
        "PayrollManagement",
        "AdvanceSalaryRequest"
    )

    AttendanceCalendar = apps.get_model(
        "calendars",
        "AttendanceCalendar"
    )

    # ========================================================
    # PROCESS EACH EMPLOYEE
    # ========================================================

    for employee in employees_qs:

        logger.info(
            f"Processing payroll for employee "
            f"{employee.emp_code} "
            f"PayrollRun={instance.id}"
        )

        # ====================================================
        # IDEMPOTENCY
        # ====================================================

        existing_payslip = Payslip.objects.filter(
            payroll_run=instance,
            employee=employee
        ).first()

        if existing_payslip:

            logger.info(
                f"Payslip already exists for "
                f"{employee.emp_code} "
                f"PayrollRun={instance.id}. "
                f"Skipping."
            )

            continue

        try:

            # =================================================
            # FORMULA VARIABLES
            # =================================================

            variables = get_formula_variables(
                employee,
                start_date,
                end_date
            )

        except Exception as e:

            logger.exception(
                f"Error getting formula variables "
                f"for {employee.emp_code}: {e}"
            )

            variables = {}

        # ====================================================
        # UNPAID LEAVE
        # ====================================================

        unpaid_leave_days_val = (
            AttendanceCalendar.objects
            .filter(
                employee=employee,
                date__range=(start_date, end_date)
            )
            .aggregate(
                total=Sum("unpaid_fraction")
            )["total"]
            or Decimal("0.00")
        )

        unpaid_leave_days = Decimal(
            str(unpaid_leave_days_val)
        )

        # ====================================================
        # DAYS WORKED
        # ====================================================

        days_worked = (
            Decimal(str(total_days))
            - unpaid_leave_days
        )

        if days_worked < 0:

            days_worked = Decimal("0.00")

        # ====================================================
        # CREATE PAYSLIP
        # ====================================================

        payslip = Payslip.objects.create(
            payroll_run=instance,
            employee=employee,
            total_working_days=total_days,
            days_worked=days_worked,
        )

        logger.info(
            f"Created Payslip {payslip.id} "
            f"for {employee.emp_code}"
        )

        # ====================================================
        # LEAVE DETAILS
        # ====================================================

        leave_entries = (
            AttendanceCalendar.objects
            .filter(
                employee=employee,
                date__range=(start_date, end_date),
                status="Leave"
            )
            .values("leave_type")
            .annotate(
                total_days=Count("id")
            )
        )

        for entry in leave_entries:

            if not entry["leave_type"]:
                continue

            leave_type_id = entry["leave_type"]

            # -----------------------------------------------
            # Calculate precise leave days
            # -----------------------------------------------

            precise_days = Decimal("0.00")

            day_records = (
                AttendanceCalendar.objects
                .filter(
                    employee=employee,
                    date__range=(start_date, end_date),
                    status="Leave",
                    leave_type_id=leave_type_id
                )
            )

            for record in day_records:

                if record.is_half_day:

                    precise_days += Decimal("0.5")

                else:

                    precise_days += Decimal("1.0")

            PayslipLeave.objects.update_or_create(
                payslip=payslip,
                leave_type_id=leave_type_id,
                defaults={
                    "days": precise_days
                }
            )

        # ====================================================
        # SALARY STRUCTURE COMPONENTS
        # ====================================================

        salary_structs = (
            EmployeeSalaryStructure.objects
            .filter(
                employee=employee,
                is_active=True
            )
            .select_related("component")
        )

        for salary_structure in salary_structs:

            comp = salary_structure.component

            amount = Decimal("0.00")

            # -----------------------------------------------
            # Calculate component
            # -----------------------------------------------

            try:

                # ============================================
                # FIXED COMPONENT
                # ============================================

                if comp.component_value_type == "fixed":

                    amount = Decimal(
                        str(
                            salary_structure.amount
                            or "0.00"
                        )
                    )

                # ============================================
                # VARIABLE + FORMULA COMPONENT
                # ============================================

                elif (
                    comp.component_value_type == "variable"
                    and comp.formula
                ):

                    amount = Decimal(
                        str(
                            evaluate_formula(
                                comp.formula,
                                variables,
                                employee,
                                comp
                            )
                        )
                    )

                # ============================================
                # VARIABLE WITHOUT FORMULA
                # ============================================

                else:

                    amount = Decimal(
                        str(
                            salary_structure.amount
                            or "0.00"
                        )
                    )

            except Exception as e:

                logger.exception(
                    f"Error calculating component "
                    f"{comp.name} "
                    f"(category={comp.payroll_category}) "
                    f"for employee "
                    f"{employee.emp_code}: {e}"
                )

                amount = Decimal("0.00")

            # -----------------------------------------------
            # Deduct unpaid leave if configured
            # -----------------------------------------------

            if (
                getattr(comp, "deduct_leave", False)
                and unpaid_leave_days > 0
                and total_days > 0
            ):

                per_day = (
                    amount /
                    Decimal(str(total_days))
                )

                amount -= (
                    per_day *
                    unpaid_leave_days
                )

                if amount < 0:

                    amount = Decimal("0.00")

            # -----------------------------------------------
            # ALWAYS STORE COMPONENT
            #
            # Even if show_in_payslip=False
            # -----------------------------------------------

            PayslipComponent.objects.update_or_create(
                payslip=payslip,
                component=comp,
                defaults={
                    "amount": amount
                }
            )

            logger.debug(
                f"Component calculated: "
                f"{employee.emp_code} | "
                f"{comp.name} | "
                f"category={comp.payroll_category} | "
                f"amount={amount} | "
                f"show_in_payslip="
                f"{comp.show_in_payslip}"
            )

        # ====================================================
        # GENERAL REQUESTS
        # ====================================================
        
        approved_requests = (
            GeneralRequest.objects
            .filter(
                employee=employee,
                status="Approved",
                is_processed=False,
                request_type__salary_component__isnull=False,
            )
            .select_related(
                "request_type__salary_component"
            )
        )

        for request in approved_requests:

            comp = (
                request.request_type.salary_component
            )

            if not comp:
                continue

            if request.total is None:
                continue

            amount = Decimal(
                str(request.total)
            )

            # -----------------------------------------------
            # Store component
            # -----------------------------------------------

            PayslipComponent.objects.update_or_create(
                payslip=payslip,
                component=comp,
                defaults={
                    "amount": amount
                }
            )

            # -----------------------------------------------
            # Mark request processed
            # -----------------------------------------------

            request.is_processed = True

            request.save(
                update_fields=[
                    "is_processed"
                ]
            )

        # ====================================================
        # LOANS
        # ====================================================

        active_loans = (
            LoanRequest.objects
            .filter(
                employee=employee,
                status="Approved"
            )
        )

        for loan in active_loans:

            repayment_count = (
                LoanRepayment.objects
                .filter(
                    loan=loan
                )
                .count()
            )

            if repayment_count >= loan.repayment_period:

                continue

            emi_amount = Decimal(
                str(
                    loan.emi_amount
                    or "0.00"
                )
            )

            # -----------------------------------------------
            # Find loan component using payroll_category
            # -----------------------------------------------

            loan_component = (
                SalaryComponent.objects
                .filter(
                    payroll_category="loan",
                    branch=employee.emp_branch_id
                )
                .first()
            )

            # Fallback if branch-specific component
            # doesn't exist
            if not loan_component:

                loan_component = (
                    SalaryComponent.objects
                    .filter(
                        payroll_category="loan"
                    )
                    .first()
                )

            if loan_component:

                PayslipComponent.objects.update_or_create(
                    payslip=payslip,
                    component=loan_component,
                    defaults={
                        "amount": emi_amount
                    }
                )

                # -------------------------------------------
                # Calculate remaining balance
                # -------------------------------------------

                total_paid = (
                    LoanRepayment.objects
                    .filter(
                        loan=loan
                    )
                    .aggregate(
                        total=Sum("amount_paid")
                    )["total"]
                    or Decimal("0.00")
                )

                remaining_balance = (
                    Decimal(
                        str(
                            loan.amount_requested
                            or "0.00"
                        )
                    )
                    - Decimal(str(total_paid))
                    - emi_amount
                )

                # -------------------------------------------
                # Create repayment
                # -------------------------------------------

                LoanRepayment.objects.create(
                    loan=loan,
                    payslip=payslip,
                    repayment_date=instance.payment_date,
                    amount_paid=emi_amount,
                    remaining_balance=remaining_balance,
                )

                loan.remaining_balance = (
                    remaining_balance
                )

                loan.save(
                    update_fields=[
                        "remaining_balance"
                    ]
                )

                if remaining_balance <= 0:

                    loan.status = "Closed"

                    loan.save(
                        update_fields=[
                            "status"
                        ]
                    )

        # ====================================================
        # ADVANCE SALARY
        # ====================================================

        advance_component = (
            SalaryComponent.objects
            .filter(
                payroll_category="advance_salary",
                branch=employee.emp_branch_id
            )
            .first()
        )

        if not advance_component:

            advance_component = (
                SalaryComponent.objects
                .filter(
                    payroll_category="advance_salary"
                )
                .first()
            )

        approved_advances = (
            AdvanceSalaryRequest.objects
            .filter(
                employee=employee,
                status="Approved"
            )
        )

        for advance in approved_advances:

            requested_amount = Decimal(
                str(
                    advance.requested_amount
                    or "0.00"
                )
            )

            if (
                not advance_component
                or requested_amount <= 0
            ):

                continue

            PayslipComponent.objects.update_or_create(
                payslip=payslip,
                component=advance_component,
                defaults={
                    "amount": requested_amount
                }
            )

            advance.status = "Deducted"

            advance.save(
                update_fields=[
                    "status"
                ]
            )

        # ====================================================
        # AIR TICKET ENCASHMENT
        # ====================================================

        air_ticket_component = (
            SalaryComponent.objects
            .filter(
                payroll_category="air_ticket",
                branch=employee.emp_branch_id
            )
            .first()
        )

        if not air_ticket_component:

            air_ticket_component = (
                SalaryComponent.objects
                .filter(
                    payroll_category="air_ticket"
                )
                .first()
            )

        approved_tickets = (
            AirTicketRequest.objects
            .filter(
                employee=employee,
                status="APPROVED",
                request_type="ENCASHMENT"
            )
        )

        for ticket in approved_tickets:

            allocation = getattr(
                ticket,
                "allocation",
                None
            )

            if (
                not air_ticket_component
                or not allocation
            ):

                continue

            amount = Decimal(
                str(
                    allocation.amount
                    or "0.00"
                )
            )

            PayslipComponent.objects.update_or_create(
                payslip=payslip,
                component=air_ticket_component,
                defaults={
                    "amount": amount
                }
            )

            ticket.status = "PROCESSED"

            ticket.save(
                update_fields=[
                    "status"
                ]
            )

        # ====================================================
        # RESET MANUAL VARIABLE COMPONENTS
        # ====================================================

        (
            EmployeeSalaryStructure.objects
            .filter(
                employee=employee,
                is_active=True,
                component__component_value_type="variable",
            )
            .filter(
                Q(
                    component__formula__isnull=True
                )
                |
                Q(
                    component__formula__exact=""
                )
            )
            .update(
                amount=Decimal("0.00")
            )
        )

        # ====================================================
        # FINALIZE PAYSLIP TOTALS
        #
        # IMPORTANT:
        # Do this ONLY after ALL PayslipComponents have
        # been created.
        #
        # calculate_payslip_totals() checks:
        #
        #     component.show_in_payslip
        #
        # Therefore hidden components such as:
        #
        #     Gratuity
        #     Leave Salary
        #     Air Ticket
        #
        # can still be stored and reported without affecting
        # net salary.
        # ====================================================

        totals = calculate_payslip_totals(
            payslip
        )

        logger.info(
            f"Payroll completed for "
            f"{employee.emp_code}: "
            f"Additions={totals['total_additions']}, "
            f"Deductions={totals['total_deductions']}, "
            f"Gross={totals['gross_salary']}, "
            f"Net={totals['net_salary']}"
        )

    # ========================================================
    # MARK PAYROLL RUN PROCESSED
    # ========================================================

    instance.status = "processed"

    instance.save(
        update_fields=[
            "status"
        ]
    )

    logger.info(
        f"PayrollRun {instance.id} "
        f"marked as processed."
    )
    totals = calculate_payslip_totals(
            payslip
        )

    logger.info(
        f"Payroll completed for "
        f"{employee.emp_code}: "
        f"Additions={totals['total_additions']}, "
        f"Deductions={totals['total_deductions']}, "
        f"Gross={totals['gross_salary']}, "
        f"Net={totals['net_salary']}"
    )



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
    # if not instance.component.is_fixed:
    #     return
    if instance.component.component_value_type != "fixed":
        return
    SalaryComponent = apps.get_model("PayrollManagement", "SalaryComponent")
    EmployeeSalaryStructure = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")

    # formula_components = SalaryComponent.objects.filter(is_fixed=False, formula__isnull=False)
    formula_components = SalaryComponent.objects.filter(
        component_value_type="variable",
        formula__isnull=False
    ).exclude(
        formula=""
    )

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



















# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from .models import PayslipComponent, LoanRepayment,SalaryStructure,EmployeeSalaryStructure,SalaryComponent,AdvanceSalaryRequest
# from calendars.models import Attendance,LeaveEncashmentTransaction
# from django.db.models import Q
# import logging
# from datetime import datetime
# from datetime import timedelta
# from calendar import monthrange
# import re
# logger = logging.getLogger(__name__)
# from django.db.models import Sum
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.apps import apps
# from decimal import Decimal
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.apps import apps
# from decimal import Decimal
# from django.db.models import Count
# from django.core.exceptions import ValidationError
# from EmpManagement.models import emp_master
# from django.db.models.signals import m2m_changed

# from datetime import date
# import logging
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.apps import apps
# from django.db.models import Sum
# from datetime import datetime
# from dateutil.relativedelta import relativedelta
# from simpleeval import SimpleEval, NameNotDefined, FunctionNotDefined
# from calendars .utils import get_employee_holidays,get_employee_weekend_days
# from .utils import get_ot_rate,evaluate_formula

# @receiver(m2m_changed, sender=SalaryStructure.employees.through)
# def generate_employee_salary_components(sender, instance, action, pk_set, **kwargs):
#     """
#     When employees are added to a SalaryStructure, automatically create
#     EmployeeSalaryStructure records for each component with amount 0.00
#     """
#     if action == "post_add":
#         # Get all components assigned to this structure
#         components = instance.components.all()
        
#         for emp_id in pk_set:
#             for component in components:
#                 # Create the individual records with amount 0.00
#                 EmployeeSalaryStructure.objects.get_or_create(
#                     employee_id=emp_id,
#                     component=component,
#                     defaults={'amount': 0.00, 'is_active': True}
#                 )
# @receiver(post_save, sender=SalaryComponent)
# def update_employee_salary_structure(sender, instance, created, **kwargs):
#     # if not instance.is_fixed and instance.formula:
#     if instance.component_value_type == "variable" and instance.formula:
#         EmpMaster = apps.get_model('EmpManagement', 'emp_master')
#         EmployeeSalaryStructure = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
        
#         employees = EmpMaster.objects.all()

#         for employee in employees:
#             # Get variables including fixed components, calendar_days, ot_hours etc.
#             variables = get_formula_variables(employee)

#             try:
#                 amount = evaluate_formula(instance.formula, variables, employee, instance)
#             except Exception as e:
#                 logger.error(f"Formula evaluation error for {employee}: {e}")
#                 amount = Decimal('0.00')

#             logger.info(f"Calculated amount for {instance.name} ({instance.code}) for employee {employee}: {amount}")

#             EmployeeSalaryStructure.objects.update_or_create(
#                 employee=employee,
#                 component=instance,
#                 defaults={'amount': amount, 'is_active': True}
#             )
#             logger.info(f"Updated EmployeeSalaryStructure for {employee} with component {instance.name} - Amount: {amount}")

# def get_formula_variables(employee, start_date=None, end_date=None):
#     Attendance = apps.get_model('calendars', 'Attendance')
#     EmployeeOvertime = apps.get_model('calendars', 'EmployeeOvertime')
#     EmployeeSalaryStructure = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
#     AirTicketRequest = apps.get_model('PayrollManagement', 'AirTicketRequest')
#     AirTicketAllocation = apps.get_model('PayrollManagement', 'AirTicketAllocation')
#     EmployeeOvertime = apps.get_model('calendars', 'EmployeeOvertime')

#     if not start_date or not end_date:
#         today = datetime.today().date()
#         start_date = today.replace(day=1)
#         end_date = today.replace(day=monthrange(today.year, today.month)[1])
    
#     # Fetch PayStructure for branch-specific defaults
#     PayStructure = apps.get_model("PayrollManagement", "PayStructure")
#     pay_structure = PayStructure.objects.filter(branch=employee.emp_branch_id).first()

#     variables = {
#         'calendar_days': Decimal(str((end_date - start_date).days + 1)),
#         'fixed_days': Decimal(str(pay_structure.fixed_working_days if pay_structure and pay_structure.fixed_working_days else '30.0')),
#         'standard_hours': Decimal('160.0'),
#     }

#     variables['ot_hours'] = EmployeeOvertime.objects.filter(
#         employee=employee, date__range=(start_date, end_date)
#     ).aggregate(total_hours=Sum('hours'))['total_hours'] or Decimal('0.00')
    
#     # Air ticket encashment amount
#     variables['air_ticket_encashment'] = AirTicketRequest.objects.filter(
#         employee=employee,
#         request_type='ENCASHMENT',
#         status='APPROVED',
#         request_date__range=(start_date, end_date)
#     ).aggregate(total_encashment=Sum('allocation__amount'))['total_encashment'] or Decimal('0.00')
#     weekend_days = get_employee_weekend_days(employee)
#     holiday_dates = get_employee_holidays(employee, start_date, end_date)

#     weekend_ot_days = 0
#     holiday_ot_days = 0

#     for single_date in daterange(start_date, end_date):
#         weekday = single_date.strftime("%A")
#         is_weekend = weekday in weekend_days
#         is_holiday = single_date in holiday_dates
#         attended = Attendance.objects.filter(employee=employee, date=single_date).exists()

#         if is_weekend and attended:
#             weekend_ot_days += 1
#         elif is_holiday and attended:
#             holiday_ot_days += 1

#     variables['weekend_ot_days'] = Decimal(weekend_ot_days)
#     variables['holiday_ot_days'] = Decimal(holiday_ot_days)
#     variables['holiday_weekend_ot_days'] = Decimal(weekend_ot_days + holiday_ot_days)
#     # variables['holiday_weekend_days_worked'] = Decimal(str(
#     #     get_holiday_weekend_days_worked(employee, start_date, end_date)
#     # ))
#     working_days = get_working_days(employee, start_date, end_date)
#     variables['working_days'] = float(working_days)
    
#     variables['employee.grade'] = str(getattr(employee, 'grade', ''))
#     variables['employee.employee_type'] = str(getattr(employee, 'employee_type', ''))
#     variables['employee.joining_date'] = (
#         employee.emp_joined_date.strftime('%Y-%m-%d') if employee.emp_joined_date else ''
#     )
    
#     # if getattr(employee, 'joining_date', None):
#     if employee.emp_joined_date:
#         # delta = relativedelta(end_date, employee.joining_date)
#         delta = relativedelta(end_date, employee.emp_joined_date)
#         variables['years_of_service'] = round(delta.years + delta.months / 12.0, 2)
#     else:
#         variables['years_of_service'] = 0.0
#     # Add encashed_days from LeaveEncashmentTransaction
#     encashment_amount = LeaveEncashmentTransaction.objects.filter(
#         employee=employee,
#         reset_date__range=(start_date, end_date)
#     ).aggregate(total_encashment=Sum('encashment_amount'))['total_encashment'] or Decimal('0.00')
#     variables['encashed_days'] = encashment_amount
#     overtimes = EmployeeOvertime.objects.filter(
#     employee=employee,
#     date__range=(start_date, end_date),
#     # approved=True
#     )

#     variables['normal_ot_hours'] = (
#         overtimes.filter(ot_type='NORMAL')
#         .aggregate(s=Sum('hours'))['s'] or Decimal('0.00')
#     )

#     variables['weekend_ot_hours'] = (
#         overtimes.filter(ot_type='WEEKEND')
#         .aggregate(s=Sum('hours'))['s'] or Decimal('0.00')
#     )

#     variables['holiday_ot_hours'] = (
#         overtimes.filter(ot_type='HOLIDAY')
#         .aggregate(s=Sum('hours'))['s'] or Decimal('0.00')
#     )

#     # OT rate variables (Zoho-style)
#     variables['ot_normal_rate'] = get_ot_rate(employee, 'NORMAL')
#     variables['ot_weekend_rate'] = get_ot_rate(employee, 'WEEKEND')
#     variables['ot_holiday_rate'] = get_ot_rate(employee, 'HOLIDAY')
#     # salary_components = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)
#     # for sc in salary_components:
#     #     if sc.component and sc.amount is not None:
#     #         variables[sc.component.code] = Decimal(str(sc.amount))
#     salary_structs = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)

#     # First add fixed components
#     # for sc in salary_structs:
#     #     if sc.component.is_fixed and sc.amount is not None:
#     #         variables[sc.component.code] = Decimal(sc.amount)
#     for sc in salary_structs:
#         if (
#             sc.component.component_value_type == "fixed"
#             and sc.amount is not None
#         ):
#             variables[sc.component.code] = Decimal(str(sc.amount))
#     # Then evaluate formula-based components and add them too
#     for sc in salary_structs:
#         comp = sc.component
#         # if not comp.is_fixed and comp.formula:
#         if (
#             comp.component_value_type == "variable"
#             and comp.formula
#         ):
#             try:
#                 val = evaluate_formula(comp.formula, variables, employee, comp)
#                 # Ensure it's always Decimal
#                 variables[comp.code] = Decimal(str(val))
#             except Exception as e:
#                 logger.error(f"Formula error for {comp.name} in get_formula_variables: {e}")
#                 variables[comp.code] = Decimal("0.00")
#     ###
#     EmpLeaveBalance = apps.get_model(
#     'calendars',
#     'emp_leave_balance'
#     )

#     leave_balances = EmpLeaveBalance.objects.filter(
#         employee=employee
#     ).select_related('leave_type')

#     for lb in leave_balances:

#         balance = Decimal(
#             str(lb.balance or 0)
#         )

#         leave_type = lb.leave_type

#         # Get leave type code
#         leave_code = getattr(
#             leave_type,
#             'code',
#             None
#         )

#         # If your leave_type model uses leave_type field
#         if not leave_code:
#             leave_code = getattr(
#                 leave_type,
#                 'leave_type',
#                 ''
#             )

#         # Make code safe for Python formula
#         safe_leave_code = str(leave_code).strip()

#         safe_leave_code = (
#             safe_leave_code
#             .replace('-', '_')
#             .replace(' ', '_')
#             .replace('/', '_')
#             .replace('.', '_')
#         )

#         # Example:
#         # AL-TRA -> al_tra
#         # Annual Leave -> annual_leave

#         safe_leave_code = safe_leave_code.lower()

#         variable_name = f'leave_balance_{safe_leave_code}'

#         variables[variable_name] = balance

#         logger.debug(
#             f"Leave balance variable created: "
#             f"{variable_name} = {balance}"
#         )
#     return variables

# def daterange(start_date, end_date):
#     for n in range(int((end_date - start_date).days) + 1):
#         yield start_date + timedelta(n)

# def get_working_days(employee, start_date, end_date):
#     AttendanceCalendar = apps.get_model("calendars", "AttendanceCalendar")
#     return AttendanceCalendar.objects.filter(
#         employee=employee,
#         date__range=(start_date, end_date),
#         status='Present'
#     ).count()
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.apps import apps
# from calendar import monthrange
# from datetime import datetime
# from decimal import Decimal
# import logging
# from django.db.models.signals import m2m_changed
# logger = logging.getLogger(__name__)

# # PayrollManagement/signals.py
# import logging
# from decimal import Decimal
# from calendar import monthrange
# from datetime import datetime
# from django.db.models import Sum, Q
# from django.db.models.signals import post_save, m2m_changed,pre_save
# from django.dispatch import receiver
# from django.apps import apps

# logger = logging.getLogger(__name__)
# def get_payroll_dates_and_days(instance):

#     PayStructure = apps.get_model("PayrollManagement", "PayStructure")

#     total_days_in_month = monthrange(instance.year, instance.month)[1]

#     start_date = date(instance.year, instance.month, 1)
#     end_date = date(instance.year, instance.month, total_days_in_month)

#     total_days = Decimal(str(total_days_in_month))

#     pay_structure = None

#     if instance.branch:
#         pay_structure = PayStructure.objects.filter(
#             branch=instance.branch
#         ).first()

#     if pay_structure:

#         # ==========================================
#         # CUSTOM ATTENDANCE CYCLE
#         # ==========================================
#         if pay_structure.attendance_cycle_type == 'CUSTOM':

#             cutoff_day = pay_structure.cycle_end_day or 26

#             # Current payroll month cutoff date
#             try:
#                 end_date = date(
#                     instance.year,
#                     instance.month,
#                     cutoff_day
#                 )
#             except ValueError:
#                 last_day = monthrange(
#                     instance.year,
#                     instance.month
#                 )[1]

#                 end_date = date(
#                     instance.year,
#                     instance.month,
#                     last_day
#                 )

#             # Previous cutoff + 1 day
#             prev_month = end_date - relativedelta(months=1)

#             try:
#                 previous_cutoff = date(
#                     prev_month.year,
#                     prev_month.month,
#                     cutoff_day
#                 )
#             except ValueError:
#                 last_day_prev = monthrange(
#                     prev_month.year,
#                     prev_month.month
#                 )[1]

#                 previous_cutoff = date(
#                     prev_month.year,
#                     prev_month.month,
#                     last_day_prev
#                 )

#             start_date = previous_cutoff + relativedelta(days=1)

#         # ==========================================
#         # SALARY CALCULATION
#         # ==========================================
#         calc_type = pay_structure.salary_calculation_type

#         if calc_type == 'FIXED_DAYS':

#             total_days = Decimal(
#                 str(pay_structure.fixed_working_days or 30)
#             )

#         elif calc_type == 'ORGANIZATION_DAYS':

#             config_working_days = (
#                 pay_structure.working_days or []
#             )

#             config_working_days = [
#                 d.upper() for d in config_working_days
#             ]

#             org_days_count = 0

#             current = start_date

#             while current <= end_date:

#                 if current.strftime('%a').upper() in config_working_days:
#                     org_days_count += 1

#                 current += relativedelta(days=1)

#             total_days = Decimal(str(org_days_count))

#         elif calc_type == 'CALENDAR_DAYS':

#             total_days = Decimal(
#                 str((end_date - start_date).days + 1)
#             )

#     return start_date, end_date, total_days
# def process_payroll(instance, employees_qs, start_date, end_date, total_days):
#     """
#     Create payslips for employees_qs for the given PayrollRun instance.
#     Idempotent: skips employee if Payslip already exists for payroll_run+employee.
#     """
#     # Resolve models
#     SalaryComponent = apps.get_model("PayrollManagement", "SalaryComponent")
#     EmployeeSalaryStructure = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")
#     Payslip = apps.get_model("PayrollManagement", "Payslip")
#     PayslipComponent = apps.get_model("PayrollManagement", "PayslipComponent")

#     GeneralRequest = apps.get_model("EmpManagement", "GeneralRequest")
#     LoanRequest = apps.get_model("PayrollManagement", "LoanApplication")
#     LoanRepayment = apps.get_model("PayrollManagement", "LoanRepayment")
#     AirTicketRequest = apps.get_model("PayrollManagement", "AirTicketRequest")
#     AdvanceSalaryRequest = apps.get_model("PayrollManagement", "AdvanceSalaryRequest")
#     employee_leave_request = apps.get_model("calendars", "employee_leave_request")

#     for employee in employees_qs:
#         # Skip if payslip already exists for this run+employee (idempotency)
#         if Payslip.objects.filter(payroll_run=instance, employee=employee).exists():
#             logger.info(f"Payslip exists, skipping employee {employee} for PayrollRun {instance.id}")
#             continue

#         try:
#             variables = get_formula_variables(employee, start_date, end_date)
#         except Exception as e:
#             logger.exception(f"Error getting formula variables for {employee}: {e}")
#             variables = {}

#         # Unpaid leave calculation: Source of Truth is AttendanceCalendar
#         AttendanceCalendar = apps.get_model("calendars", "AttendanceCalendar")
        
#         # Aggregate unpaid_fraction from the calendar for the payroll period
#         unpaid_leave_days_val = AttendanceCalendar.objects.filter(
#             employee=employee,
#             date__range=(start_date, end_date)
#         ).aggregate(total=Sum('unpaid_fraction'))['total'] or 0
        
#         unpaid_leave_days = Decimal(str(unpaid_leave_days_val))

#         days_worked = Decimal(total_days) - unpaid_leave_days
#         if days_worked < 0:
#             days_worked = Decimal("0.00")

#         # Create payslip
#         payslip = Payslip.objects.create(
#             payroll_run=instance,
#             employee=employee,
#             total_working_days=total_days,
#             days_worked=days_worked,
#         )
#         # Record Leave Details from AttendanceCalendar
#         leave_entries = AttendanceCalendar.objects.filter(
#             employee=employee,
#             date__range=(start_date, end_date),
#             status='Leave'
#         ).values('leave_type').annotate(total_days=Count('id'))

#         PayslipLeave = apps.get_model("PayrollManagement", "PayslipLeave")
#         for entry in leave_entries:
#             if entry['leave_type']:
#                 lt_id = entry['leave_type']
#                 # Calculate actual days (handling half days if they exist in calendar)
#                 # But Wait, AttendanceCalendar has is_half_day. Let's do a more precise calculation.
#                 precise_days = Decimal("0.00")
#                 day_records = AttendanceCalendar.objects.filter(
#                     employee=employee,
#                     date__range=(start_date, end_date),
#                     status='Leave',
#                     leave_type_id=lt_id
#                 )
#                 for rec in day_records:
#                     if rec.is_half_day:
#                         precise_days += Decimal("0.5")
#                     else:
#                         precise_days += Decimal("1.0")
                
#                 PayslipLeave.objects.update_or_create(
#                     payslip=payslip,
#                     leave_type_id=lt_id,
#                     defaults={'days': precise_days}
#                 )
#         total_additions = Decimal("0.00")
#         total_deductions = Decimal("0.00")

#         # Salary structure processing
#         salary_structs = EmployeeSalaryStructure.objects.filter(employee=employee, is_active=True)
#         for sc in salary_structs:
#             comp = sc.component
#             amount = Decimal("0.00")
#             try:
#                 if comp.component_value_type == "fixed":
#                 # if comp.is_fixed:
#                     amount = Decimal(str(sc.amount or "0.00"))
#                 elif comp.formula:
#                     amount = Decimal(str(evaluate_formula(comp.formula, variables, employee, comp)))
#                 else:
#                     amount = Decimal(str(sc.amount or "0.00"))
#             except Exception as e:
#                 logger.exception(f"Error calculating component {comp} for {employee}: {e}")
#                 amount = Decimal("0.00")

#             # Deduct unpaid leave only from components flagged for it
#             if getattr(comp, "deduct_leave", False) and unpaid_leave_days > 0 and total_days > 0:
#                 per_day = amount / Decimal(total_days)
#                 amount -= per_day * unpaid_leave_days

#             PayslipComponent.objects.update_or_create(
#                 payslip=payslip, component=comp, defaults={"amount": amount}
#             )

#             if getattr(comp, "component_type", "") == "addition":
#                 total_additions += amount
#             elif getattr(comp, "component_type", "") == "deduction":
#                 total_deductions += amount

#         # GeneralRequests that affect salary
#         approved_requests = GeneralRequest.objects.filter(
#             employee=employee,
#             status="Approved",
#             is_processed=False,
#             request_type__salary_component__isnull=False,
#         ).select_related("request_type__salary_component")

#         for request in approved_requests:
#             comp = request.request_type.salary_component
#             if comp and request.total is not None:
#                 amount = Decimal(str(request.total))
#                 PayslipComponent.objects.update_or_create(
#                     payslip=payslip, component=comp, defaults={"amount": amount}
#                 )
#                 if getattr(comp, "component_type", "") == "addition":
#                     total_additions += amount
#                 else:
#                     total_deductions += amount
#                 request.is_processed = True
#                 request.save(update_fields=["is_processed"])

#         # Loans
#         active_loans = LoanRequest.objects.filter(employee=employee, status="Approved")
#         for loan in active_loans:
#             repayment_count = LoanRepayment.objects.filter(loan=loan).count()
#             if repayment_count < loan.repayment_period:
#                 emi_amount = loan.emi_amount
#                 # loan_component = SalaryComponent.objects.filter(is_loan_component=True).first()
#                 loan_component = SalaryComponent.objects.filter(payroll_category='loan').first()
#                 if loan_component:
#                     PayslipComponent.objects.update_or_create(
#                         payslip=payslip, component=loan_component, defaults={"amount": emi_amount}
#                     )
#                     total_deductions += emi_amount

#                     total_paid = LoanRepayment.objects.filter(loan=loan).aggregate(
#                         total=Sum("amount_paid")
#                     )["total"] or Decimal("0.00")
#                     remaining_balance = loan.amount_requested - total_paid - emi_amount

#                     LoanRepayment.objects.create(
#                         loan=loan,
#                         payslip=payslip,
#                         repayment_date=instance.payment_date,
#                         amount_paid=emi_amount,
#                         remaining_balance=remaining_balance,
#                     )
#                     loan.remaining_balance = remaining_balance
#                     loan.save(update_fields=["remaining_balance"])
#                     if remaining_balance <= 0:
#                         loan.status = "Closed"
#                         loan.save()

#         # Advance Salary
#         # advance_component = SalaryComponent.objects.filter(is_advance_salary=True).first()
#         advance_component = SalaryComponent.objects.filter(payroll_category='advance_salary').first()
#         approved_advances = AdvanceSalaryRequest.objects.filter(employee=employee, status="Approved")
#         for advance in approved_advances:
#             if advance_component and advance.requested_amount > 0:
#                 amount = Decimal(str(advance.requested_amount))
#                 PayslipComponent.objects.update_or_create(
#                     payslip=payslip, component=advance_component, defaults={"amount": amount}
#                 )
#                 total_deductions += amount
#                 advance.status = "Deducted"
#                 advance.save(update_fields=["status"])

#         # Air tickets
#         # air_ticket_component = SalaryComponent.objects.filter(is_air_ticket=True).first()
#         air_ticket_component = SalaryComponent.objects.filter(payroll_category='air_ticket').first()
#         approved_tickets = AirTicketRequest.objects.filter(
#             employee=employee, status="APPROVED", request_type="ENCASHMENT"
#         )
#         for ticket in approved_tickets:
#             if air_ticket_component and getattr(ticket, "allocation", None):
#                 amount = Decimal(str(ticket.allocation.amount))
#                 PayslipComponent.objects.update_or_create(
#                     payslip=payslip, component=air_ticket_component, defaults={"amount": amount}
#                 )
#                 total_additions += amount
#                 ticket.status = "PROCESSED"
#                 ticket.save(update_fields=["status"])

#         # Reset manual variable components
#         EmployeeSalaryStructure.objects.filter(
#             employee=employee,
#             is_active=True,
#             component__component_value_type='variable',
#         ).filter(
#             Q(component__formula__isnull=True) | Q(component__formula__exact="")
#         ).update(amount=Decimal("0.00"))

#         # Finalize payslip totals
#         payslip.total_additions = total_additions
#         payslip.total_deductions = total_deductions
#         payslip.gross_salary = total_additions
#         payslip.net_salary = total_additions - total_deductions
#         payslip.save()

#     # Mark run processed (caller may prefer to control this; keep as you had)
#     instance.status = "processed"
#     instance.save(update_fields=["status"])


# # ---------- post_save handler: for runs that are NOT employee-wise (no M2M provided) ----------
# @receiver(post_save, sender="PayrollManagement.PayrollRun")
# def payrollrun_post_save(sender, instance, created, **kwargs):
#     """
#     Trigger payroll when a PayrollRun is created and employees M2M is not used.
#     If employees are later added via M2M, m2m_changed handler will handle that case.
#     """
#     if not created:
#         return

#     # Only trigger if status is pending (your original check)
#     if instance.status != "pending":
#         return

#     EmpMaster = apps.get_model("EmpManagement", "emp_master")

#     # Calculate dates and total days based on PayStructure
#     try:
#         start_date, end_date, total_days = get_payroll_dates_and_days(instance)
#     except Exception as e:
#         logger.exception(f"Invalid date for PayrollRun {getattr(instance, 'id', None)}: {e}")
#         return

#     # If employees were set already (unlikely in post_save because M2M isn't saved yet),
#     # prefer employee list. Otherwise use branch/department/all approach.
#     if hasattr(instance, "employees") and instance.employees.exists():
#         employees_qs = instance.employees.all()
#     elif getattr(instance, "branch", None):
#         EmpMaster = apps.get_model("EmpManagement", "emp_master")
#         employees_qs = EmpMaster.objects.filter(is_active=True, emp_branch_id=instance.branch_id)
#     elif getattr(instance, "department", None):
#         EmpMaster = apps.get_model("EmpManagement", "emp_master")
#         employees_qs = EmpMaster.objects.filter(is_active=True, emp_dept_id=instance.department_id)
#     else:
#         EmpMaster = apps.get_model("EmpManagement", "emp_master")
#         employees_qs = EmpMaster.objects.filter(is_active=True)

#     if not employees_qs.exists():
#         logger.warning(f"No employees found for PayrollRun {instance.id} in post_save path")
#         return

#     # Process payroll for the chosen set
#     process_payroll(instance, employees_qs, start_date, end_date, total_days)


# # ---------- m2m_changed handler: fires after employees are added to M2M ----------
# def payrollrun_m2m_changed(sender, instance, action, pk_set, **kwargs):
#     """
#     Triggered when M2M 'employees' changes. We only act on post_add,
#     i.e. after employees have been attached to a PayrollRun.
#     """
#     if action != "post_add":
#         return

#     # Only process pending runs
#     if instance.status != "pending":
#         logger.info(f"PayrollRun {instance.id} status is {instance.status}; skipping m2m processing.")
#         return

#     # Calculate dates and total days based on PayStructure
#     try:
#         start_date, end_date, total_days = get_payroll_dates_and_days(instance)
#     except Exception as e:
#         logger.exception(f"Invalid date for PayrollRun {getattr(instance,'id', None)} in m2m handler: {e}")
#         return

#     # employees have been added; process only those employees attached to instance
#     employees_qs = instance.employees.all()
#     if not employees_qs.exists():
#         logger.warning(f"No employees in PayrollRun {instance.id} after m2m post_add")
#         return

#     process_payroll(instance, employees_qs, start_date, end_date, total_days)


# # Connect m2m handler to the through model. We fetch PayrollRun model and connect here.
# try:
#     PayrollRun = apps.get_model("PayrollManagement", "PayrollRun")
#     # connect handler to the through model for the employees m2m
#     m2m_changed.connect(payrollrun_m2m_changed, sender=PayrollRun.employees.through)
# except Exception as e:
#     # When this file is imported earlier than app registry ready, apps.get_model might fail
#     # but your apps.py should import signals in ready() so this normally won't happen.
#     logger.exception(f"Could not connect m2m_changed for PayrollRun.employees: {e}")

# @receiver(post_save, sender="PayrollManagement.EmployeeSalaryStructure")
# def update_dependents_on_fixed_change(sender, instance, **kwargs):
#     """
#     If a fixed component changes (e.g. Basic), recalc dependent formula components for that employee.
#     """
#     # if not instance.component.is_fixed:
#     #     return
#     if instance.component.component_value_type != "fixed":
#         return
#     SalaryComponent = apps.get_model("PayrollManagement", "SalaryComponent")
#     EmployeeSalaryStructure = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")

#     # formula_components = SalaryComponent.objects.filter(is_fixed=False, formula__isnull=False)
#     formula_components = SalaryComponent.objects.filter(
#         component_value_type="variable",
#         formula__isnull=False
#     ).exclude(
#         formula=""
#     )

#     for comp in formula_components:
#         if comp.formula and instance.component.code in comp.formula:
#             try:
#                 emp_struct, _ = EmployeeSalaryStructure.objects.get_or_create(
#                     employee=instance.employee, component=comp
#                 )
#                 emp_struct.amount = evaluate_formula(
#                     comp.formula, get_formula_variables(instance.employee, datetime.today(), datetime.today()), instance.employee, comp
#                 )
#                 emp_struct.save(update_fields=["amount"])
#             except Exception as e:
#                 logger.error(f"Error updating dependent component {comp.name} for {instance.employee}: {e}")

