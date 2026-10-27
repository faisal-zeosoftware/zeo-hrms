from django.db.models.signals import pre_delete   # v1.62.0 payroll
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
import re
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

def get_payroll_dates_and_days(instance, branch=None, structure=None):
    try:   # v1.55.0 a period of a pay calendar (weekly, every two weeks, twice a month) has its own dates
        from PayrollPlus.cycles import period_of as _pp_period
        _p = _pp_period(instance)
        if _p:
            return _p
    except ImportError:
        pass
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

    pay_structure = structure

    # IMPORTANT:
    # PayrollRun.branch is ManyToMany.
    # Therefore a specific branch must be passed here.
    if pay_structure is None and branch:
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



def structure_for(employee):
    """v1.18.0 – the Payroll settings (pay structure) of an employee: one of the employee's branch (or for every branch) whose
    categories / departments / designations, when set, include the employee's; the most specific wins. Before, the first
    structure of the branch was used for everyone – two payroll groups (office staff cut off on the 20th, hospital staff on
    the 26th) in one branch could not be run."""
    PayStructure = apps.get_model("PayrollManagement", "PayStructure")
    best, score = None, -1
    for ps in PayStructure.objects.prefetch_related('branch', 'department', 'category', 'designation'):
        br = {b.pk for b in ps.branch.all()}
        if br and employee.emp_branch_id_id not in br:
            continue
        sc = 1 if br else 0
        ok = True
        for rel, val in (('category', employee.emp_ctgry_id_id), ('department', employee.emp_dept_id_id), ('designation', employee.emp_desgntn_id_id)):
            ids = {x.pk for x in getattr(ps, rel).all()}
            if ids:
                if val not in ids:
                    ok = False
                    break
                sc += 2
        if ok and sc > score:
            best, score = ps, sc
    return best


def last_working_day(employee):
    """v1.18.0 – the employee's last working day when leaving (approved resignation / termination or Employment info)."""
    days = []
    try:
        R = apps.get_model("EmpManagement", "EmployeeResignation")
        r = R.objects.filter(employee=employee, status__iexact='approved').order_by('-last_working_date').first()
        if r and r.last_working_date:
            days.append(r.last_working_date)
    except Exception:
        pass
    try:
        EI = apps.get_model("EmployeeProfile", "EmploymentInfo")
        i = EI.objects.filter(employee_id=employee.pk).first()
        if i and i.date_of_leaving and i.status in ('on_notice', 'left', 'terminated', 'absconded', 'retired'):
            days.append(i.date_of_leaving)
    except Exception:
        pass
    return min(days) if days else None



def _settlement_pays_salary(employee, period_start):
    """v1.62.0 EOS – a leaver whose final settlement (not cancelled) already pays the salary of this period up to the last working day
    gets no payslip in the run (the salary would be paid twice)."""
    try:
        X = apps.get_model('PayrollPlus', 'ExitSettlement')
    except LookupError:
        return False
    for st in X.objects.filter(employee_id=employee.pk).exclude(status='cancelled').only('salary_from', 'lines'):
        if st.salary_from and st.salary_from <= period_start and any(l.get('key') == 'salary' and l.get('amount') for l in (st.lines or [])):
            return True
    return False


def _store_window(instance, windows):
    """v1.18.0: the run shows the attendance window it used (when all its employees share one)."""
    try:
        if len(windows) == 1:
            a, b = next(iter(windows))
            a = a.date() if isinstance(a, datetime) else a
            b = b.date() if isinstance(b, datetime) else b
            if (instance.attendance_start_date, instance.attendance_end_date) != (a, b):
                instance.attendance_start_date, instance.attendance_end_date = a, b
                instance.save(update_fields=['attendance_start_date', 'attendance_end_date'])
    except Exception:
        logger.debug('window not stored', exc_info=True)



PENSION_CATEGORIES = ('basic', 'hra', 'housing_allowance', 'transport_allowance')
GPSSA_NEW_LAW = date(2023, 10, 31)     # Federal Decree-Law 57/2023: 11 % employee share for those registered from this date


def is_uae_national(employee):
    """v1.18.0 – an Emirati employee of a UAE company (GPSSA pension, no Labour Law gratuity)."""
    try:
        from CountryPack import services as _cp
        return bool(_cp.is_ae() and _cp.is_national(str(getattr(employee, 'emp_nationality', '') or '')))
    except Exception:
        n = str(getattr(employee, 'emp_nationality', '') or '').upper()
        return 'EMIRAT' in n or n in ('UAE', 'UNITED ARAB EMIRATES')


def pension_variables(employee, start_date=None, end_date=None):
    """v1.18.0 – GPSSA figures for payroll formulas:
       is_national            1 for an Emirati employee of a UAE company, else 0
       pension_salary         contribution salary: basic + housing + transport of the active salary (min 3,000, max 70,000)
       pension_employee_rate  0.11 when the employee joined on / after 31 Oct 2023 (new law), else 0.05
       pension_employer_rate  0.125 (employer share net of the government's 2.5 %)"""
    nat = is_uae_national(employee)
    out = {'is_national': Decimal('1' if nat else '0'), 'pension_salary': Decimal('0.00'),
           'pension_employee_rate': Decimal('0.00'), 'pension_employer_rate': Decimal('0.00')}
    if not nat:
        return out
    ESS = apps.get_model("PayrollManagement", "EmployeeSalaryStructure")
    sal = sum((Decimal(str(r.amount or 0)) for r in ESS.objects.filter(employee=employee, is_active=True,
                                                                         component__payroll_category__in=PENSION_CATEGORIES,
                                                                         component__component_type='addition')), Decimal('0'))
    # v1.59.0: the rates and limits in force on the first day of the pay period (Payroll › Legal rates; built-in values otherwise)
    try:
        from PayrollPlus.legal import rate as _rate
    except ImportError:
        _rate = lambda k, on=None, default=None: Decimal(str(default))
    on = None       # the rule date: the 1st of the pay month in a payroll run
    if sal:
        sal = min(max(sal, _rate('ae_gpssa_salary_min', on, '3000')), _rate('ae_gpssa_salary_max', on, '70000'))
    joined = getattr(employee, 'emp_joined_date', None)
    out['pension_salary'] = sal.quantize(Decimal('0.01'))
    out['pension_employee_rate'] = _rate('ae_gpssa_employee_new', on, '0.11') if (joined and joined >= GPSSA_NEW_LAW) else _rate('ae_gpssa_employee_old', on, '0.05')
    out['pension_employer_rate'] = _rate('ae_gpssa_employer', on, '0.125')
    return out


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

        employees_all = (
            instance
            .get_employees()
            .filter(
                is_active=True,
                emp_branch=branch
            )
            .distinct()
        )
        # v1.18.0: one pass per pay structure (payroll group) of the branch
        groups = {}
        for e in employees_all:
            ps = structure_for(e)
            groups.setdefault(ps.pk if ps else None, (ps, []))[1].append(e.pk)
        for _ps_id, (_ps, _ids) in groups.items():
            start_date, end_date, total_days = get_payroll_dates_and_days(instance, branch=branch, structure=_ps)
            employees_qs = employees_all.filter(pk__in=_ids)
            from .perf import payroll_cache as _pc
            with _pc():
                process_payroll(instance, employees_qs, start_date, end_date, total_days)
            processed_any = True
            _store_window(instance, {(start_date, end_date)})
        continue

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

        from .perf import payroll_cache   # v1.16.0 (D15)
        with payroll_cache():
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
        
        # v1.16.0 (D15): branch / department / designation / category read with the employees (used by every lookup)
        employees = EmpMaster.objects.all().select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id')

        from .perf import payroll_cache   # v1.16.0 (D15): one shift plan / rule / calendar read for all employees
        from django.db import transaction
        # v1.16.0 (D15): one transaction for all employees (was one commit per employee, ~15 ms each); same rows
        with payroll_cache(), transaction.atomic():
            _recalculate_component_for(instance, employees)


def _recalculate_component_for(instance, employees):
    """v1.16.0: body of update_employee_salary_structure (same calculation), run inside payroll_cache()."""
    EmployeeSalaryStructure = apps.get_model('PayrollManagement', 'EmployeeSalaryStructure')
    for employee in employees:
        # Get variables including fixed components, calendar_days, ot_hours etc.
        variables = get_formula_variables(employee)

        try:
            amount = evaluate_formula(instance.formula, variables, employee, instance)
        except Exception as e:
            logger.error(f"Formula evaluation error for {employee}: {e}")
            amount = Decimal('0.00')

        logger.info(f"Calculated amount for {instance.name} ({instance.code}) for employee {employee}: {amount}")

        # v1.20.2: a row HR switched off (component not for this employee) stays off when the component is edited – before,
        # every save of the component switched it on again for everybody (e.g. overtime for staff not entitled to it)
        _row = EmployeeSalaryStructure.objects.filter(employee=employee, component=instance).first()
        if _row is not None:
            if _row.amount != amount:
                EmployeeSalaryStructure.objects.filter(pk=_row.pk).update(amount=amount)
        else:
            EmployeeSalaryStructure.objects.create(employee=employee, component=instance, amount=amount, is_active=True)
        logger.info(f"Updated EmployeeSalaryStructure for {employee} with component {instance.name} - Amount: {amount}")


def _roster_days(employee, start_date, end_date):
    """v1.18.0: {date: is rest day} for the days a published roster (or a day change) plans for the employee."""
    out = {}
    try:
        from ShiftPlanner.resolver import Book
        a = start_date.date() if isinstance(start_date, datetime) else start_date
        b = end_date.date() if isinstance(end_date, datetime) else end_date
        book = Book(a, b, [employee.pk])
        d = a
        while d <= b:
            r = book.get(employee, d, sources=('roster', 'override'))
            if r is not None:
                out[d] = bool(r.get('off'))
            d += timedelta(days=1)
    except Exception:
        logger.debug('roster days not available', exc_info=True)
    return out


def get_formula_variables(employee, start_date=None, end_date=None):
    """v1.59.0: every legal rule (rule sets, statutory rates, legal rates) as in force on the first day of the pay month
    (process_payroll sets it from the run's month; a direct call uses start_date)."""
    try:
        from CountryPack.services import rules_on, rule_date_set
    except ImportError:
        return _get_formula_variables(employee, start_date, end_date)
    if rule_date_set():
        return _get_formula_variables(employee, start_date, end_date)
    with rules_on(start_date):
        return _get_formula_variables(employee, start_date, end_date)


def _get_formula_variables(employee, start_date=None, end_date=None):
    EmpLeaveBalance = apps.get_model("calendars", "emp_leave_balance")
    Attendance = apps.get_model("calendars", "Attendance")
    EmployeeOvertime = apps.get_model("calendars", "EmployeeOvertime")
    EmployeeSalaryStructure = apps.get_model(
        "PayrollManagement",
        "EmployeeSalaryStructure"
    )
    AirTicketRequest = apps.get_model(
        "PayrollManagement",
        "AirTicketRequest"
    )
    LeaveEncashmentTransaction = apps.get_model(
        "calendars",
        "LeaveEncashmentTransaction"
    )
    PayStructure = apps.get_model(
        "PayrollManagement",
        "PayStructure"
    )

    if not start_date or not end_date:
        today = datetime.today().date()
        start_date = today.replace(day=1)
        end_date = today.replace(
            day=monthrange(today.year, today.month)[1]
        )

    # v1.16.0 (D15): the branch's payroll settings once per run (PayrollManagement.perf)
    from .perf import memo as _memo
    pay_structure = _memo(('pay_structure', employee.emp_branch_id_id), lambda: (
        PayStructure.objects
        .filter(branch=employee.emp_branch_id)
        .first()
    ))

    fixed_days = (
        pay_structure.fixed_working_days
        if pay_structure
        and pay_structure.fixed_working_days
        else 30
    )

    variables = {
        "calendar_days": Decimal(
            str((end_date - start_date).days + 1)
        ),
        "fixed_days": Decimal(str(fixed_days)),
        "standard_hours": Decimal("160.0"),
        "paid_days": Decimal(str(fixed_days)),   # v1.20.2: set to the payslip's paid days when payroll runs
    }

    # v1.20.2: days of the period the employee was employed (joining date / last working day inside the period) – for
    # pro-rating accruals (gratuity, leave salary, air ticket provision) of joiners and leavers
    try:
        _a0 = start_date.date() if isinstance(start_date, datetime) else start_date
        _b0 = end_date.date() if isinstance(end_date, datetime) else end_date
        _j0 = getattr(employee, 'emp_joined_date', None)
        _l0 = last_working_day(employee)
        _f0 = max(_a0, _j0) if _j0 else _a0
        _t0 = min(_b0, _l0) if _l0 else _b0
        variables["employed_days"] = Decimal(max(0, (_t0 - _f0).days + 1))
    except Exception:
        variables["employed_days"] = variables["calendar_days"]

    # ========================================================
    # OVERTIME
    # ========================================================

    ot_filter = {
        "employee": employee,
        "date__range": (start_date, end_date),
    }

    # overtime_source is optional because older PayStructure
    # records/models may not contain this field.
    overtime_source = getattr(
        pay_structure,
        "overtime_source",
        None
    )

    if overtime_source in ("ATTENDANCE", "MANUAL"):
        ot_filter["source"] = overtime_source

    # v1.12.0: only approved overtime is paid (attendance OT waits for approval; OT entered / imported
    # by HR – source MANUAL – counts as approved)
    from django.db.models import Q as _Q
    ot_filter["pk__in"] = EmployeeOvertime.objects.filter(
        _Q(approved=True) | _Q(source="MANUAL"), employee=employee, date__range=(start_date, end_date)
    ).values("pk")

    # v1.16.0 (D15): total and the normal / weekend / holiday split in one query (were four) – same sums
    _ot = (
        EmployeeOvertime.objects
        .filter(**ot_filter)
        .aggregate(
            total_hours=Sum("hours"),
            normal=Sum("hours", filter=Q(ot_type="NORMAL")),
            weekend=Sum("hours", filter=Q(ot_type="WEEKEND")),
            holiday=Sum("hours", filter=Q(ot_type="HOLIDAY")),
            night=Sum("hours", filter=Q(ot_type="NIGHT")),   # v1.20.2
        )
    )
    variables["ot_hours"] = (
        _ot["total_hours"]
        or Decimal("0.00")
    )

    # ========================================================
    # AIR TICKET ENCASHMENT
    # ========================================================

    variables["air_ticket_encashment"] = (
        AirTicketRequest.objects
        .filter(
            employee=employee,
            request_type="ENCASHMENT",
            status="APPROVED",
            request_date__range=(start_date, end_date),
        )
        .aggregate(
            total_encashment=Sum("allocation__amount")
        )["total_encashment"]
        or Decimal("0.00")
    )

    # ========================================================
    # WEEKEND / HOLIDAY DAYS
    # ========================================================

    # v1.16.0 (D15): weekend / holiday calendars once per employee and run (PayrollManagement.perf), attendance days in
    # one query instead of one query per day of the period – same values as before
    from .perf import memo as _memo
    from .perf import employee_weekend_days as _wk_days, employee_holidays as _hol_days
    weekend_days = _memo(('weekend_days', employee.pk), lambda: _wk_days(employee))
    holiday_dates = _memo(('holidays', employee.pk, start_date, end_date), lambda: _hol_days(
        employee,
        start_date,
        end_date
    ))
    _att_dates = set(
        Attendance.objects.filter(employee=employee, date__range=(start_date, end_date)).values_list("date", flat=True)
    )

    weekend_ot_days = 0
    holiday_ot_days = 0
    holiday_weekend_ot_days = 0

    # v1.18.0: employees on a published roster (hospital shifts) have their rest days in the roster, not on Saturday / Sunday –
    # a rostered weekend shift is a working day and a rostered day off is their rest day
    planned = _memo(('roster_days', employee.pk, start_date, end_date), lambda: _roster_days(employee, start_date, end_date))

    def _rest_day(d):
        d0 = d.date() if isinstance(d, datetime) else d
        if d0 in planned:
            return planned[d0]
        return d.strftime("%A") in weekend_days

    for single_date in daterange(start_date, end_date):

        is_weekend = _rest_day(single_date)
        is_holiday = single_date in holiday_dates

        attended = (single_date.date() if isinstance(single_date, datetime) else single_date) in _att_dates

        if is_weekend and attended:
            weekend_ot_days += 1

        elif is_holiday and attended:
            holiday_ot_days += 1

    holiday_weekend_ot_days = (
        weekend_ot_days + holiday_ot_days
    )

    variables["weekend_ot_days"] = Decimal(
        weekend_ot_days
    )

    variables["holiday_ot_days"] = Decimal(
        holiday_ot_days
    )

    variables["holiday_weekend_ot_days"] = Decimal(
        holiday_weekend_ot_days
    )

    # ========================================================
    # WORKING DAYS
    # ========================================================

    working_days = get_working_days(
        employee,
        start_date,
        end_date
    )

    variables["working_days"] = Decimal(
        str(working_days)
    )

    # v1.11.0: clearer day counts – working_days stays "days present in the attendance calendar" for existing formulas
    try:
        _wk = set(weekend_days)    # v1.16.0 (D15): same calendars as above (were read again)
        _hol = set(holiday_dates)
        variables["scheduled_working_days"] = Decimal(sum(1 for d in daterange(start_date, end_date) if not _rest_day(d) and d not in _hol))
    except Exception:
        variables["scheduled_working_days"] = Decimal("0")
    variables["present_days"] = Decimal(str(working_days))
    try:
        _AC = apps.get_model("calendars", "AttendanceCalendar")
        variables["unpaid_leave_days"] = Decimal(str(_AC.objects.filter(employee=employee, date__range=(start_date, end_date))
                                                     .aggregate(t=Sum("unpaid_fraction"))["t"] or 0))
    except Exception:
        variables["unpaid_leave_days"] = Decimal("0")
    try:   # v1.20.2: days without pay only – unpaid leave and absence (a day at half pay, e.g. maternity, is not one)
        variables["days_without_pay"] = Decimal(str(_AC.objects.filter(employee=employee, date__range=(start_date, end_date)).filter(
            Q(status="Absent") | Q(leave_type__type="unpaid")).aggregate(t=Sum("unpaid_fraction"))["t"] or 0))
    except Exception:
        variables["days_without_pay"] = variables["unpaid_leave_days"]

    # v1.11.0: hours worked in the period (WORKHOURS() / worked_hours in formulas)
    try:
        _wh = Attendance.objects.filter(employee=employee, date__range=(start_date, end_date), total_hours__isnull=False) \
            .aggregate(t=Sum("total_hours"))["t"]
        variables["worked_hours"] = (Decimal(str(round(_wh.total_seconds() / 3600, 2))) if _wh else Decimal("0.00"))
    except Exception:
        variables["worked_hours"] = Decimal("0.00")

    # ========================================================
    # EMPLOYEE VARIABLES
    # ========================================================

    variables["employee.grade"] = str(
        getattr(employee, "grade", "")
    )

    variables["employee.employee_type"] = str(
        getattr(employee, "employee_type", "")
    )

    variables["employee.emp_joined_date"] = (
        employee.emp_joined_date.strftime("%Y-%m-%d")
        if getattr(employee, "emp_joined_date", None)
        else ""
    )

    # ========================================================
    # YEARS OF SERVICE
    # ========================================================

    years_of_service = Decimal("0.00")

    if employee.emp_joined_date:

        # v1.20.2: the joining day counts as a day of service (joined 2 Oct 2021 → 5.00 years on 30 Sep 2026, not 4.997)
        _end = end_date.date() if isinstance(end_date, datetime) else end_date
        _lwd = last_working_day(employee)
        if _lwd and _lwd < _end:
            _end = _lwd
        service_days = (
            _end - employee.emp_joined_date
        ).days + 1

        if service_days > 0:

            years_of_service = (
                Decimal(str(service_days))
                / Decimal("365")
            )

    variables["years_of_service"] = years_of_service

    # ========================================================
    # LEAVE ENCASHMENT
    # ========================================================

    encashment_amount = (
        LeaveEncashmentTransaction.objects
        .filter(
            employee=employee,
            reset_date__range=(start_date, end_date)
        )
        .aggregate(
            total_encashment=Sum("encashment_amount")
        )["total_encashment"]
        or Decimal("0.00")
    )

    variables["encashed_days"] = Decimal(
        str(encashment_amount)
    )

    # v1.10.0: approved leave encashments not paid yet → use in a "Leave Encashment" component
    # (payroll category leave_encashment, formula: leave_encashment_amount)
    try:
        LeaveEncashmentRequest = apps.get_model("PayrollManagement", "LeaveEncashment")
        variables["leave_encashment_amount"] = (
            LeaveEncashmentRequest.objects.filter(
                employee=employee, status="approved", payroll_run__isnull=True,
                approved_at__date__lte=end_date,
            ).aggregate(total=Sum("encashment_amount"))["total"]
            or Decimal("0.00")
        )
    except Exception:
        variables["leave_encashment_amount"] = Decimal("0.00")

    # v1.11.0: approved expense reports to be paid with payroll (component formula: expense_reimbursement_amount)
    try:
        from ExpenseManagement.services import expense_reimbursement_amount
        variables["expense_reimbursement_amount"] = expense_reimbursement_amount(employee, start_date, end_date)
    except Exception:
        variables["expense_reimbursement_amount"] = Decimal("0.00")

    # v1.12.0: asset damage / loss recovery instalments due (component formula: asset_recovery_amount)
    try:
        from AssetPlus.services import asset_recovery_amount
        variables["asset_recovery_amount"] = asset_recovery_amount(employee, start_date, end_date)
    except Exception:
        variables["asset_recovery_amount"] = Decimal("0.00")

    # ========================================================
    # OVERTIME BREAKDOWN
    # ========================================================

    variables["normal_ot_hours"] = (
        _ot["normal"]
        or Decimal("0.00")
    )

    variables["weekend_ot_hours"] = (
        _ot["weekend"]
        or Decimal("0.00")
    )

    variables["holiday_ot_hours"] = (
        _ot["holiday"]
        or Decimal("0.00")
    )

    # v1.20.2: night overtime (22:00–04:00, Decree-Law 33/2021 Art. 19 – at least 50% for non-shift workers)
    variables["night_ot_hours"] = (
        _ot.get("night")
        or Decimal("0.00")
    )

    variables["ot_normal_rate"] = get_ot_rate(
        employee,
        "NORMAL"
    )

    variables["ot_weekend_rate"] = get_ot_rate(
        employee,
        "WEEKEND"
    )
    variables["ot_night_rate"] = get_ot_rate(employee, "NIGHT")   # v1.20.2

    variables["ot_holiday_rate"] = get_ot_rate(
        employee,
        "HOLIDAY"
    )

    # ========================================================
    # v1.12.0 ATTENDANCE: late / early / absent / missing punch / half day / breaks,
    # rate-weighted approved OT and standard hours from the planned shifts (AttendancePlus),
    # plus the shift planner variables (night / shift allowance ...) when installed.
    # ========================================================
    try:
        from AttendancePlus.payroll import variables as _att_vars
        variables.update(_att_vars(employee, start_date, end_date, overtime_ids=ot_filter["pk__in"]))
    except ImportError:
        from .formula import ATTENDANCE_VARIABLE_NAMES
        for _n in ATTENDANCE_VARIABLE_NAMES:
            variables.setdefault(_n, Decimal("0"))
    except Exception:
        logger.exception("attendance payroll variables failed for %s", employee.pk)
    try:
        from ShiftPlanner.payroll import variables as _shift_vars
        variables.update(_shift_vars(employee, start_date, end_date) or {})
    except ImportError:
        pass
    except Exception:
        logger.exception("shift planner payroll variables failed for %s", employee.pk)

    # ========================================================
    # SALARY STRUCTURE
    # ========================================================

    salary_structs = (
        EmployeeSalaryStructure.objects
        .filter(
            employee=employee,
            is_active=True
        )
        .select_related("component")
    )

    # ========================================================
    # FIXED COMPONENTS
    #
    # Primary variable:
    #     BAS
    #
    # Also create a safe name alias:
    #     basic_salary
    #
    # Example:
    #     Basic Salary -> BAS
    # ========================================================

    for sc in salary_structs:

        comp = sc.component

        # v1.20.2: a variable component without a formula is a monthly input (entered or imported for the month, set back
        # to 0 after the run) – its amount can now be used in formulas too, e.g. NSHIFT (night shifts worked this month)
        # in the formula NSHIFT * 15
        is_input = (comp.component_value_type == "variable" and not (comp.formula or "").strip())
        if (
            (comp.component_value_type == "fixed" or is_input)
            and sc.amount is not None
        ):

            value = Decimal(str(sc.amount))

            # Primary formula variable = component code
            if comp.code:
                variables[comp.code] = value

            # Human-readable component name alias
            component_name = str(
                comp.name or ""
            ).strip()

            if component_name:

                safe_name = re.sub(
                    r"[^a-zA-Z0-9_]",
                    "_",
                    component_name
                )

                safe_name = re.sub(
                    r"_+",
                    "_",
                    safe_name
                ).strip("_").lower()

                if safe_name:
                    variables[safe_name] = value

    # ========================================================
    # BASIC SALARY
    # ========================================================

    basic_salary = Decimal("0.00")

    # v1.16.0 (D15): from the rows already read (was a second query; same row: the first by id, rows are unique per component)
    _basic_rows = [sc for sc in salary_structs if sc.component.payroll_category == "basic"]
    basic_component = min(_basic_rows, key=lambda sc: sc.pk) if _basic_rows else None

    if basic_component and basic_component.amount is not None:

        basic_salary = Decimal(
            str(basic_component.amount)
        )

        if basic_component.component.code:

            variables[
                basic_component.component.code
            ] = basic_salary

    variables["basic_salary"] = basic_salary

    # ========================================================
    # LEAVE BALANCES
    #
    # Example:
    # AL-UAE -> leave_balance_al_uae
    # SL-UAE -> leave_balance_sl_uae
    # ========================================================

    leave_balances = (
        EmpLeaveBalance.objects
        .filter(employee=employee)
        .select_related("leave_type")
    )

    for lb in leave_balances:

        balance = Decimal(
            str(lb.balance or "0.00")
        )

        leave_type = lb.leave_type

        leave_code = getattr(
            leave_type,
            "code",
            None
        )

        if not leave_code:

            leave_code = getattr(
                leave_type,
                "leave_type",
                ""
            )

        safe_leave_code = str(
            leave_code or ""
        ).strip()

        safe_leave_code = re.sub(
            r"[^a-zA-Z0-9_]",
            "_",
            safe_leave_code
        )

        safe_leave_code = re.sub(
            r"_+",
            "_",
            safe_leave_code
        ).strip("_").lower()

        if not safe_leave_code:
            continue

        variable_name = (
            f"leave_balance_{safe_leave_code}"
        )

        variables[variable_name] = balance

        logger.debug(
            f"Leave balance variable created: "
            f"{variable_name} = {balance}"
        )

    # ========================================================
    # GRATUITY VARIABLES
    # ========================================================

    gratuity_vars = get_gratuity_variables(
        employee=employee,
        years_of_service=years_of_service,
        gratuity_type="resignation",
        basic_salary=basic_salary
    )

    variables.update(gratuity_vars)

    # v1.31.0: statutory schemes of the company country (GOSI, PASI, EPF, ESI, TDS …)
    try:
        from PayrollPlus.statutory import variables as _stat_vars
        variables.update(_stat_vars(employee))
    except Exception:
        logger.exception("statutory variables failed for %s", employee.pk)

    # v1.18.0: UAE nationals – GPSSA pension instead of the Labour Law gratuity
    try:
        variables.update(pension_variables(employee, start_date, end_date))
        if variables.get('is_national') == Decimal('1'):
            for _k in ('gratuity_days', 'monthly_gratuity_accrual', 'total_gratuity_liability'):
                if _k in variables:
                    variables[_k] = Decimal('0.00')
    except Exception:
        logger.exception("pension variables failed for %s", employee.pk)

    # ========================================================
    # FORMULA COMPONENTS
    #
    # Evaluate these AFTER:
    #   - fixed salary components
    #   - leave balances
    #   - gratuity variables
    #
    # Therefore formulas can use:
    #   BAS
    #   basic_salary
    #   fixed_days
    #   leave_balance_al_uae
    #   years_of_service
    #   etc.
    # ========================================================

    # v1.11.0: a formula may use another formula component – calculate them in dependency order
    from .formula import order_components
    _formula_scs = [sc for sc in salary_structs if sc.component.component_value_type != "fixed" and sc.component.formula]
    _order, _cycle = order_components([sc.component for sc in _formula_scs])
    if _cycle:
        logger.error("Formula components refer to each other (circle): %s – they are paid as 0",
                     ", ".join(c.code or c.name for c in _cycle))
    _rank = {c.pk: i for i, c in enumerate(_order)}
    _ordered_scs = sorted((sc for sc in _formula_scs if sc.component.pk in _rank), key=lambda sc: _rank[sc.component.pk])

    for sc in _ordered_scs:

        comp = sc.component

        if (
            comp.component_value_type != "fixed"
            and comp.formula
        ):

            try:

                value = evaluate_formula(
                    comp.formula,
                    variables,
                    employee,
                    comp
                )

                value = Decimal(str(value))

                if comp.code:
                    variables[comp.code] = value

                # Also make the calculated component
                # available using its safe name.
                component_name = str(
                    comp.name or ""
                ).strip()

                if component_name:

                    safe_name = re.sub(
                        r"[^a-zA-Z0-9_]",
                        "_",
                        component_name
                    )

                    safe_name = re.sub(
                        r"_+",
                        "_",
                        safe_name
                    ).strip("_").lower()

                    if safe_name:
                        variables[safe_name] = value

            except Exception as e:

                logger.error(
                    f"Formula error for "
                    f"{comp.name} "
                    f"(code={comp.code}): {e}"
                )

                if comp.code:
                    variables[comp.code] = Decimal("0.00")

    return variables


# def get_formula_variables(employee, start_date=None, end_date=None):
#     EmpLeaveBalance = apps.get_model('calendars','emp_leave_balance')

#     Attendance = apps.get_model('calendars', 'Attendance')
#     EmployeeOvertime = apps.get_model('calendars', 'EmployeeOvertime')
#     EmployeeSalaryStructure = apps.get_model(
#         'PayrollManagement',
#         'EmployeeSalaryStructure'
#     )
#     AirTicketRequest = apps.get_model(
#         'PayrollManagement',
#         'AirTicketRequest'
#     )
#     LeaveEncashmentTransaction = apps.get_model(
#         'calendars',
#         'LeaveEncashmentTransaction'
#     )

#     if not start_date or not end_date:
#         today = datetime.today().date()
#         start_date = today.replace(day=1)
#         end_date = today.replace(
#             day=monthrange(today.year, today.month)[1]
#         )

#     PayStructure = apps.get_model(
#         "PayrollManagement",
#         "PayStructure"
#     )

#     pay_structure = PayStructure.objects.filter(
#         branch=employee.emp_branch_id
#     ).first()

#     variables = {
#         'calendar_days': Decimal(
#             str((end_date - start_date).days + 1)
#         ),
#         'fixed_days': Decimal(
#             str(
#                 pay_structure.fixed_working_days
#                 if pay_structure
#                 and pay_structure.fixed_working_days
#                 else '30.0'
#             )
#         ),
#         'standard_hours': Decimal('160.0'),
#     }

#     # -----------------------------------------------------
#     # OVERTIME
#     # -----------------------------------------------------
#     ot_filter = {
#         'employee': employee,
#         'date__range': (start_date, end_date)
#     }
    
#     if pay_structure:
#         if pay_structure.overtime_source == 'ATTENDANCE':
#             ot_filter['source'] = 'ATTENDANCE'
#         elif pay_structure.overtime_source == 'MANUAL':
#             ot_filter['source'] = 'MANUAL'

#     variables['ot_hours'] = (
#         EmployeeOvertime.objects.filter(**ot_filter).aggregate(
#             total_hours=Sum('hours')
#         )['total_hours']
#         or Decimal('0.00')
#     )
#     # variables['ot_hours'] = (
#     #     EmployeeOvertime.objects.filter(
#     #         employee=employee,
#     #         date__range=(start_date, end_date)
#     #     ).aggregate(
#     #         total_hours=Sum('hours')
#     #     )['total_hours']
#     #     or Decimal('0.00')
#     # )

#     # -----------------------------------------------------
#     # AIR TICKET ENCASHMENT
#     # -----------------------------------------------------

#     variables['air_ticket_encashment'] = (
#         AirTicketRequest.objects.filter(
#             employee=employee,
#             request_type='ENCASHMENT',
#             status='APPROVED',
#             request_date__range=(start_date, end_date)
#         ).aggregate(
#             total_encashment=Sum('allocation__amount')
#         )['total_encashment']
#         or Decimal('0.00')
#     )

#     # -----------------------------------------------------
#     # WEEKEND / HOLIDAY DAYS
#     # -----------------------------------------------------

#     weekend_days = get_employee_weekend_days(employee)
#     holiday_dates = get_employee_holidays(
#         employee,
#         start_date,
#         end_date
#     )

#     weekend_ot_days = 0
#     holiday_ot_days = 0

#     for single_date in daterange(start_date, end_date):

#         weekday = single_date.strftime("%A")

#         is_weekend = weekday in weekend_days
#         is_holiday = single_date in holiday_dates

#         attended = Attendance.objects.filter(
#             employee=employee,
#             date=single_date
#         ).exists()

#         if is_weekend and attended:
#             weekend_ot_days += 1

#         elif is_holiday and attended:
#             holiday_ot_days += 1

#     variables['weekend_ot_days'] = Decimal(weekend_ot_days)
#     variables['holiday_ot_days'] = Decimal(holiday_ot_days)
#     variables['holiday_weekend_ot_days'] = Decimal(
#         weekend_ot_days + holiday_ot_days
#     )

#     working_days = get_working_days(
#         employee,
#         start_date,
#         end_date
#     )

#     variables['working_days'] = float(working_days)

#     # -----------------------------------------------------
#     # EMPLOYEE VARIABLES
#     # -----------------------------------------------------

#     variables['employee.grade'] = str(
#         getattr(employee, 'grade', '')
#     )

#     variables['employee.employee_type'] = str(
#         getattr(employee, 'employee_type', '')
#     )

#     variables['employee.emp_joined_date'] = (
#         employee.emp_joined_date.strftime('%Y-%m-%d')
#         if getattr(employee, 'emp_joined_date', None)
#         else ''
#     )

#     # -----------------------------------------------------
#     # YEARS OF SERVICE
#     # -----------------------------------------------------

#     years_of_service = Decimal('0.00')

#     if employee.emp_joined_date:

#         service_days = (
#             end_date - employee.emp_joined_date
#         ).days

#         if service_days > 0:
#             years_of_service = (
#                 Decimal(str(service_days))
#                 / Decimal('365')
#             )

#     variables['years_of_service'] = years_of_service

#     # -----------------------------------------------------
#     # LEAVE ENCASHMENT
#     # -----------------------------------------------------

#     encashment_amount = (
#         LeaveEncashmentTransaction.objects.filter(
#             employee=employee,
#             reset_date__range=(start_date, end_date)
#         ).aggregate(
#             total_encashment=Sum('encashment_amount')
#         )['total_encashment']
#         or Decimal('0.00')
#     )

#     variables['encashed_days'] = encashment_amount

#     # -----------------------------------------------------
#     # OVERTIME BREAKDOWN
#     # -----------------------------------------------------

#     # overtimes = EmployeeOvertime.objects.filter(
#     #     employee=employee,
#     #     date__range=(start_date, end_date),
#     # )
#     overtimes = EmployeeOvertime.objects.filter(**ot_filter)

#     variables['normal_ot_hours'] = (
#         overtimes.filter(
#             ot_type='NORMAL'
#         ).aggregate(
#             s=Sum('hours')
#         )['s']
#         or Decimal('0.00')
#     )

#     variables['weekend_ot_hours'] = (
#         overtimes.filter(
#             ot_type='WEEKEND'
#         ).aggregate(
#             s=Sum('hours')
#         )['s']
#         or Decimal('0.00')
#     )

#     variables['holiday_ot_hours'] = (
#         overtimes.filter(
#             ot_type='HOLIDAY'
#         ).aggregate(
#             s=Sum('hours')
#         )['s']
#         or Decimal('0.00')
#     )

#     variables['ot_normal_rate'] = get_ot_rate(
#         employee,
#         'NORMAL'
#     )

#     variables['ot_weekend_rate'] = get_ot_rate(
#         employee,
#         'WEEKEND'
#     )

#     variables['ot_holiday_rate'] = get_ot_rate(
#         employee,
#         'HOLIDAY'
#     )

#     # -----------------------------------------------------
#     # SALARY STRUCTURE
#     # -----------------------------------------------------

#     salary_structs = EmployeeSalaryStructure.objects.filter(
#         employee=employee,
#         is_active=True
#     )

#     # Fixed Components First
#     for sc in salary_structs:

#         if (
#             sc.component.component_value_type == 'fixed'
#             and sc.amount is not None
#         ):
#             variables[sc.component.code] = Decimal(
#                 str(sc.amount)
#             )

#     # -----------------------------------------------------
#     # BASIC SALARY
#     # -----------------------------------------------------

#     basic_salary = Decimal('0.00')

#     basic_component = salary_structs.filter(
#         component__payroll_category='basic'
#     ).first()

#     if basic_component and basic_component.amount:
#         basic_salary = Decimal(
#             str(basic_component.amount)
#         )

#     variables['basic_salary'] = basic_salary

#     # -----------------------------------------------------
#     # GRATUITY VARIABLES
#     # -----------------------------------------------------

#     gratuity_vars = get_gratuity_variables(
#         employee=employee,
#         years_of_service=years_of_service,
#         gratuity_type='resignation',
#         basic_salary=basic_salary
#     )

#     variables.update(gratuity_vars)

#     # -----------------------------------------------------
#     # FORMULA COMPONENTS
#     # -----------------------------------------------------

#     for sc in salary_structs:

#         comp = sc.component

#         if (
#             comp.component_value_type != 'fixed'
#             and comp.formula
#         ):
#             try:

#                 value = evaluate_formula(
#                     comp.formula,
#                     variables,
#                     employee,
#                     comp
#                 )

#                 variables[comp.code] = Decimal(
#                     str(value)
#                 )

#             except Exception as e:

#                 logger.error(
#                     f"Formula error for "
#                     f"{comp.name} : {e}"
#                 )

#                 variables[comp.code] = Decimal('0.00')
#         # ---------------------------------------------------------

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






def _ensure_deduction_component(SalaryComponent, category, name, code, branch):
    """Return (creating if needed) the standard deduction component for loans / advance salary."""
    comp = SalaryComponent.objects.filter(code=code, branch=branch).first() or SalaryComponent.objects.filter(name=name, branch=branch).first()
    if comp:
        if comp.payroll_category != category:
            return None  # a different component already uses this code/name - leave it to HR
        return comp
    return SalaryComponent.objects.create(name=name, code=code, branch=branch, component_type='deduction',
                                          payroll_category=category, component_value_type='variable',
                                          description=f'Default {name} component', show_in_payslip=True)


def process_payroll(instance, employees_qs, start_date, end_date, total_days):
    """v1.59.0: the legal rules in force on the 1st of the run's month (a payroll period from the 26th of the month before
    still takes the rules of the pay month), so a re-run of a past month keeps that month's rates."""
    on = None
    try:
        from datetime import date as _dd
        on = _dd(int(instance.year), int(instance.month), 1)
    except Exception:
        on = start_date
    try:
        from CountryPack.services import rules_on
    except ImportError:
        return _process_payroll(instance, employees_qs, start_date, end_date, total_days)
    with rules_on(on):
        return _process_payroll(instance, employees_qs, start_date, end_date, total_days)


def _process_payroll(
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

    # v1.55.0 pay calendars: who belongs to this run, and whether loans / advances are deducted in it
    try:
        from PayrollPlus import cycles as _pp_cycles
        _pp_skip_events = _pp_cycles.skip_events(instance)
    except ImportError:
        _pp_cycles, _pp_skip_events = None, False

    # v1.16.0 (D15): branch / department / designation / category read with the employees (used by every lookup)
    for employee in employees_qs.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id'):
        if _pp_cycles is not None and _pp_cycles.excluded(instance, employee):
            continue

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

        # v1.18.0: joiners and leavers – days of the period before the joining date or after the last working day are not
        # paid (before, a new joiner was paid the whole month, an employee joining next month got a full payslip, and a
        # leaver was paid to the end of the month)
        _a = start_date.date() if isinstance(start_date, datetime) else start_date
        _b = end_date.date() if isinstance(end_date, datetime) else end_date
        _joined = employee.emp_joined_date
        _lwd = last_working_day(employee)
        if (_joined and _joined > _b) or (_lwd and _lwd < _a):
            logger.info(f"{employee.emp_code} not employed in {_a}..{_b} – no payslip")
            continue
        if _lwd and _lwd <= _b and _settlement_pays_salary(employee, _a):   # v1.62.0 EOS
            logger.info(f"{employee.emp_code}: the final settlement pays the salary to {_lwd} – no payslip")
            continue
        _not_employed = 0
        if _joined and _joined > _a:
            _not_employed += (_joined - _a).days
        if _lwd and _lwd < _b:
            _not_employed += (_b - _lwd).days
        if _not_employed:
            _window = Decimal((_b - _a).days + 1)
            # v1.20.2: Payroll controls → "Part months": share of the calendar month (default) or days employed (at most the
            # fixed days – the policy "monthly salary ÷ 30, a full month always pays 30 days")
            _pm = 'share'
            try:
                from PayrollPlus.models import PayrollPlusSettings as _PPS
                _pm = _PPS.get().part_month
            except Exception:
                pass
            if _pm == 'days':
                _employed = _window - Decimal(_not_employed)
                unpaid_leave_days += max(Decimal('0'), Decimal(str(total_days)) - min(_employed, Decimal(str(total_days))))
            else:
                unpaid_leave_days += (Decimal(_not_employed) * Decimal(str(total_days)) / _window).quantize(Decimal('0.01'))

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
        variables["paid_days"] = days_worked   # v1.20.2: the payslip's paid days (after unpaid leave, absence, joining / leaving)

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
        # v1.62.0: a company with one component set per branch (BAS, GTY, GPSSA … in each branch) gave every employee the formula
        # components of every branch – each was paid / deducted once per branch. When the employee has the same component code
        # more than once, only one counts: the one of the employee's branch, else the company-wide one, else the first. A component
        # of another branch assigned on its own (no twin) still counts, as before.
        _by_code = {}
        for _s in salary_structs:
            _k = (getattr(_s.component, 'code', None) or f'#{_s.component_id}').strip().upper()
            _rank = 0 if _s.component.branch_id == employee.emp_branch_id_id else (1 if _s.component.branch_id is None else 2)
            if _k not in _by_code or _rank < _by_code[_k][0]:
                _by_code[_k] = (_rank, _s)
        salary_structs = [v[1] for v in _by_code.values()]

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
        if _pp_skip_events:   # v1.55.0 once a month: in the last period of the month
            active_loans = LoanRequest.objects.none()

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

            # No component with this payroll category -> create the standard one instead of silently
            # leaving the loan deduction out of the payslip
            if not loan_component:
                loan_component = _ensure_deduction_component(SalaryComponent, 'loan', 'Loan Deduction', 'LOAN', employee.emp_branch_id)

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

        # No component with this payroll category -> create the standard one instead of silently
        # leaving the advance salary deduction out of the payslip
        if not advance_component:
            advance_component = _ensure_deduction_component(SalaryComponent, 'advance_salary', 'Advance Salary Deduction', 'ADV', employee.emp_branch_id)

        approved_advances = (
            AdvanceSalaryRequest.objects
            .filter(
                employee=employee,
                status="Approved"
            )
        )
        if _pp_skip_events:   # v1.55.0 once a month: in the last period of the month
            approved_advances = AdvanceSalaryRequest.objects.none()

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

            # pay what is left on the allocation (not the full allocation every time) and use it up,
            # otherwise the same allocation could be encashed again in the next payroll
            remaining = allocation.remaining_amount
            already_paid = AirTicketRequest.objects.filter(
                allocation=allocation, request_type="ENCASHMENT", status="PROCESSED"
            ).exclude(pk=ticket.pk).exists()
            if already_paid:
                amount = Decimal("0.00")
            else:
                amount = Decimal(str((remaining if remaining and remaining > 0 else allocation.amount) or "0.00"))
            if amount <= 0:
                ticket.status = "PROCESSED"
                ticket.save(update_fields=["status"])
                continue

            PayslipComponent.objects.update_or_create(
                payslip=payslip,
                component=air_ticket_component,
                defaults={
                    "amount": amount
                }
            )

            allocation.remaining_amount = Decimal("0.00")
            allocation.save(update_fields=["remaining_amount"])

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

        if _pp_cycles is not None:   # v1.55.0 prorated to a pay calendar's period; arrears of earlier months added
            try:
                _pp_cycles.adjust_payslip(instance, payslip, employee)
            except Exception:
                logger.exception('pay calendar / arrears adjustment failed for payslip %s', payslip.pk)

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
    # v1.15.1: the totals of the last payslip were calculated a second time here – it failed (name not defined)
    # when every employee of the call already had a payslip



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

    # employees have been added; process only those employees attached to instance
    employees_qs = instance.employees.all()
    if not employees_qs.exists():
        logger.warning(f"No employees in PayrollRun {instance.id} after m2m post_add")
        return

    # v1.15.1: dates and days per branch from the branch's pay structure (Payroll settings: fixed 30 days, custom cycle …);
    # the run used to be calculated on calendar days for every branch because no branch was passed
    Branch = apps.get_model("OrganisationManager", "brnch_mstr")
    # v1.18.0: grouped by branch AND pay structure (payroll group by category / department / designation)
    by_group, structs = {}, {}
    for e in employees_qs.select_related('emp_branch_id'):
        ps = structure_for(e)
        structs[ps.pk if ps else None] = ps
        by_group.setdefault((e.emp_branch_id_id, ps.pk if ps else None), []).append(e.pk)
    windows = set()
    from .perf import payroll_cache   # v1.16.0 (D15): shift plan / rules / calendars read once for the whole run
    with payroll_cache():
        for (br_id, ps_id), ids in by_group.items():
            try:
                start_date, end_date, total_days = get_payroll_dates_and_days(instance, branch=Branch.objects.filter(pk=br_id).first() if br_id else None,
                                                                              structure=structs.get(ps_id))
            except Exception as e:
                logger.exception(f"Invalid date for PayrollRun {getattr(instance,'id', None)} in m2m handler: {e}")
                return
            windows.add((start_date, end_date))
            process_payroll(instance, employees_qs.filter(pk__in=ids), start_date, end_date, total_days)
    _store_window(instance, windows)


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

    # v1.15.1: formula components (overtime, leave encashment, expense reimbursement …) are given to every employee when
    # the component is saved; an employee whose salary is entered later got none of them and was paid 0 for them.
    # Add the missing ones (inactive rows that HR switched off stay off); payroll calculates the amount every run.
    have = set(EmployeeSalaryStructure.objects.filter(employee=instance.employee).values_list('component_id', flat=True))
    for comp in formula_components:
        if comp.pk not in have:
            EmployeeSalaryStructure.objects.get_or_create(employee=instance.employee, component=comp,
                                                          defaults={'amount': Decimal('0.00'), 'is_active': True})

    # v1.16.0 (D15): the variables were calculated again for every dependent component (same values: they read the fixed
    # components only, and the rows were completed above) – calculated once
    _vars = None
    for comp in formula_components:
        if comp.formula and instance.component.code in comp.formula:
            try:
                emp_struct, _ = EmployeeSalaryStructure.objects.get_or_create(
                    employee=instance.employee, component=comp
                )
                if _vars is None:
                    _vars = get_formula_variables(instance.employee, date.today(), date.today())   # v1.18.0: dates, not date-times (the preview failed)
                emp_struct.amount = evaluate_formula(
                    comp.formula, dict(_vars), instance.employee, comp
                )
                emp_struct.save(update_fields=["amount"])
            except Exception as e:
                logger.error(f"Error updating dependent component {comp.name} for {instance.employee}: {e}")


# ============================================================
# v1.16.0 (D17): LOAN REPAYMENTS FOLLOW THE PAYSLIP
# A loan instalment is recorded (LoanRepayment) when the payslip is processed. When the payslip is rejected the
# instalment is not paid: the repayment is reversed (loan balance back, a loan closed by it is open again). When a
# rejected payslip is approved again later, the instalment is recorded again (same rule as payroll processing).
# ============================================================

_REJECTED = ('rejected',)
_PAID_AGAIN = ('approved', 'paid', 'processed', 'pending')


@receiver(pre_save, sender="PayrollManagement.Payslip")
def _payslip_status_before(sender, instance, update_fields=None, **kwargs):
    instance._v116_old_status = None
    if instance.pk is None or (update_fields is not None and 'status' not in update_fields):
        return
    from .perf import active as _run_active
    if _run_active():          # payroll processing never rejects / re-approves a payslip – no extra read per save
        return
    instance._v116_old_status = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()


@receiver(post_save, sender="PayrollManagement.Payslip")
def _payslip_status_loans(sender, instance, created, update_fields=None, **kwargs):
    old = getattr(instance, '_v116_old_status', None)
    if created or old is None:
        return
    was, now = str(old).lower(), str(instance.status or '').lower()
    if was == now:
        return
    try:
        if now in _REJECTED and was not in _REJECTED:
            reverse_loan_repayments(instance)
        elif was in _REJECTED and now in _PAID_AGAIN:
            reapply_loan_repayments(instance)
    except Exception:
        logger.exception("loan repayment update failed for payslip %s", instance.pk)


# v1.62.0 payroll: a payslip that is deleted (its payroll run deleted or recalculated) paid nothing – its loan instalments are
# taken back. Before, the instalments stayed (payslip link emptied) and a recalculated run took the instalment a second time,
# closing loans too early.
@receiver(pre_delete, sender="PayrollManagement.Payslip")
def _payslip_deleted_loans(sender, instance, **kwargs):
    try:
        reverse_loan_repayments(instance)
    except Exception:
        logger.exception("loan repayment reversal failed for deleted payslip %s", instance.pk)


def _loan_balance(loan):
    LoanRepayment = apps.get_model("PayrollManagement", "LoanRepayment")
    paid = LoanRepayment.objects.filter(loan=loan).aggregate(t=Sum("amount_paid"))["t"] or Decimal("0.00")
    return Decimal(str(loan.amount_requested or "0.00")) - Decimal(str(paid))


def reverse_loan_repayments(payslip):
    """The instalments recorded with this payslip are taken back (the payslip was rejected – nothing was paid)."""
    LoanRepayment = apps.get_model("PayrollManagement", "LoanRepayment")
    reps = list(LoanRepayment.objects.filter(payslip=payslip).select_related("loan"))
    for rep in reps:
        loan = rep.loan
        rep.delete()
        balance = _loan_balance(loan)
        loan.remaining_balance = balance if balance > 0 else Decimal("0.00")
        fields = ["remaining_balance"]
        if str(loan.status) == "Closed" and balance > 0:
            loan.status = "Approved"
            fields.append("status")
        loan.save(update_fields=fields)
        logger.info("Loan %s: instalment %s of payslip %s reversed (payslip rejected)", loan.pk, rep.amount_paid, payslip.pk)
    return len(reps)


def reapply_loan_repayments(payslip):
    """A rejected payslip is approved again: record the instalments of the loans it deducts (same rule as processing)."""
    LoanApplication = apps.get_model("PayrollManagement", "LoanApplication")
    LoanRepayment = apps.get_model("PayrollManagement", "LoanRepayment")
    PayslipComponent = apps.get_model("PayrollManagement", "PayslipComponent")
    if not PayslipComponent.objects.filter(payslip=payslip, component__payroll_category="loan").exists():
        return 0
    loans = LoanApplication.objects.filter(employee_id=payslip.employee_id, status="Approved")
    if payslip.created_at:
        loans = loans.filter(Q(approved_on__isnull=True) | Q(approved_on__lte=payslip.created_at))
    n = 0
    for loan in loans.order_by("pk"):
        if LoanRepayment.objects.filter(loan=loan, payslip=payslip).exists():
            continue
        if LoanRepayment.objects.filter(loan=loan).count() >= loan.repayment_period:
            continue
        emi = Decimal(str(loan.emi_amount or "0.00"))
        remaining = _loan_balance(loan) - emi
        LoanRepayment.objects.create(loan=loan, payslip=payslip, repayment_date=payslip.payroll_run.payment_date,
                                     amount_paid=emi, remaining_balance=remaining)
        loan.remaining_balance = remaining
        loan.save(update_fields=["remaining_balance"])
        if remaining <= 0:
            loan.status = "Closed"
            loan.save(update_fields=["status"])
        n += 1
    return n



















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

