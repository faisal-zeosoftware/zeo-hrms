
from datetime import datetime, timedelta
from django.utils import timezone
from .models import leave_entitlement, assign_weekend,assign_holiday

def get_employee_weekend_calendar(employee):
    """
    Return weekend model assigned to employee 
    by priority: employee > branch > department > category.
    """

    # 1. Direct employee-wise assignment
    direct = assign_weekend.objects.filter(
        related_to='employee',
        employee=employee
    ).first()
    if direct:
        return direct.weekend_model

    # 2. Branch-wise assignment
    branch_assign = assign_weekend.objects.filter(
        related_to='branch',
        branch=employee.emp_branch_id
    ).first()
    if branch_assign:
        return branch_assign.weekend_model

    # 3. Department-wise assignment
    dept_assign = assign_weekend.objects.filter(
        related_to='department',
        department=employee.emp_dept_id
    ).first()
    if dept_assign:
        return dept_assign.weekend_model

    # 4. Category-wise assignment
    cat_assign = assign_weekend.objects.filter(
        related_to='category',
        category=employee.emp_ctgry_id
    ).first()
    if cat_assign:
        return cat_assign.weekend_model

    return None
def get_employee_holiday_calendar(employee):
    direct = assign_holiday.objects.filter(related_to='employee', employee=employee).first()
    if direct:
        return direct.holiday_model

    branch_assign = assign_holiday.objects.filter(
        related_to='branch',
        branch=employee.emp_branch_id
    ).first()
    if branch_assign:
        return branch_assign.holiday_model

    dept_assign = assign_holiday.objects.filter(
        related_to='department',
        department=employee.emp_dept_id
    ).first()
    if dept_assign:
        return dept_assign.holiday_model

    cat_assign = assign_holiday.objects.filter(
        related_to='category',
        category=employee.emp_ctgry_id
    ).first()
    if cat_assign:
        return cat_assign.holiday_model

    return None

def calculate_leave_entitlement(employee, leave_type):
    today = timezone.now().date()
    leave_entitlements = leave_entitlement.objects.filter(leave_type=leave_type)

    for entitlement in leave_entitlements:
        # Check the effective date
        if entitlement.effective_after_from == 'date_of_joining':
            effective_date = employee.emp_joined_date
        else:
            effective_date = employee.emp_date_of_confirmation
        
        if today < effective_date:
            return 0

        # Check if the accrual date matches
        if entitlement.accrual:
            if entitlement.accrual_frequency == 'years':
                if today.month == 1 and today.day == 1:
                    # Accrue leave on 1st January
                    return entitlement.effective_after if not entitlement.prorate_accrual else prorated_accrual(entitlement, effective_date)
            else:
                # Handle other frequency cases (e.g., months, days)
                pass

    return 0

def prorated_accrual(entitlement, effective_date):
    today = timezone.now().date()
    total_days = (today - effective_date).days
    if entitlement.effective_after_unit == 'months':
        total_days //= 30
    elif entitlement.effective_after_unit == 'years':
        total_days //= 365
    
    return entitlement.effective_after * (total_days / (365 if entitlement.effective_after_unit == 'years' else 30))

from datetime import timedelta
from django.db.models import Q
from .models import Attendance, employee_leave_request, assign_weekend, assign_holiday


def daterange(start_date, end_date):
    for n in range(int((end_date - start_date).days) + 1):
        yield start_date + timedelta(n)

def get_employee_weekend_days(employee):
    assigned = assign_weekend.objects.filter(
        Q(employee=employee) |
        Q(branch=employee.emp_branch_id) |
        Q(department=employee.emp_dept_id) #d|
        # Q(category=employee.emp_ctgry_id)
    ).first()
    if assigned:
        return set(assigned.weekend_model.get_weekend_days())  # list of days e.g., ["Saturday", "Sunday"]
    return set()

# def get_employee_holidays(employee, start_date, end_date):
#     assigned = assign_holiday.objects.filter(employee=employee).first()
#     if not assigned:
#         return []

#     holidays = assigned.holiday_model.holiday_list.filter(
#         start_date__lte=end_date,
#         end_date__gte=start_date
#     )
#     return holidays
def get_employee_holidays(employee, start_date, end_date):
    assigned = assign_holiday.objects.filter(
        Q(employee=employee) |
        Q(branch=employee.emp_branch_id) |
        Q(department=employee.emp_dept_id)
    ).first()

    if not assigned:
        return set()

    holidays = assigned.holiday_model.holiday_list.filter(
        start_date__lte=end_date,
        end_date__gte=start_date
    )

    holiday_dates = set()
    for holiday in holidays:
        for day in daterange(holiday.start_date, holiday.end_date):
            holiday_dates.add(day)
    return holiday_dates
def get_attendance_summary(employee, start_date, end_date):
    summary = []
    total_present = 0
    total_absent = 0

    weekend_days = get_employee_weekend_days(employee)
    holiday_dates = get_employee_holidays(employee, start_date, end_date)

    for day in daterange(start_date, end_date):
        weekday = day.strftime("%A")
        status = "Absent"
        leave_type = None

        if weekday in weekend_days:
            status = "Weekend"
        elif day in holiday_dates:
            status = "Holiday"
        elif Attendance.objects.filter(employee=employee, date=day).exists():
            status = "Present"
            total_present += 1
        else:
            leave = employee_leave_request.objects.filter(
                employee=employee,
                status='approved',
                start_date__lte=day,
                end_date__gte=day
            ).first()
            if leave:
                status = "On Leave"
                leave_type = leave.leave_type.name
                total_absent += 1
            else:
                total_absent += 1

        summary.append({
            "date": day,
            "status": status,
            "leave_type": leave_type,
        })

    return {
        "summary": summary,
        "total_present": total_present,
        "total_absent": total_absent
    }

def schedule_escalation(approval, level_rule):
    from django.db import connection
    from .tasks import escalate_approval_task
    """
    Schedule a Celery countdown task for automatic escalation.
    """
    total_seconds = (
        (level_rule.escalate_after_days or 0) * 86400 +
        (level_rule.escalate_after_hours or 0) * 3600 +
        (level_rule.escalate_after_minutes or 0) * 60
    )

    if total_seconds > 0 and level_rule.escalate_to:
        schema_name = connection.schema_name
        escalate_approval_task.apply_async((approval.id, schema_name), countdown=total_seconds)
        print(f"🕒 Escalation task scheduled for approval {approval.id} after {total_seconds} seconds.")

def get_employee_group_values(employee):
    """
    Return (designation, department, category) for the employee.
    Adjust attribute names here if your emp_master uses different field names.
    """
    # Common field name guesses — if your emp_master fields differ, edit here:
    desig = getattr(employee, 'designation', None) or getattr(employee, 'desig', None) or getattr(employee, 'emp_designation', None)
    dept  = getattr(employee, 'department', None) or getattr(employee, 'dept', None) or getattr(employee, 'emp_dept', None)
    cat   = getattr(employee, 'category', None) or getattr(employee, 'cat', None) or getattr(employee, 'emp_category', None)
    return desig, dept, cat

def rule_matches_employee(rule_obj, employee):
    """
    rule_obj: leave_entitlement or LeaveResetPolicy instance
    employee: emp_master instance
    Rule fields are nullable; NULL means 'any'.
    """
    emp_desig, emp_dept, emp_cat = get_employee_group_values(employee)

    # Compare by id if both sides are model instances, else direct equality works
    if rule_obj.designation and emp_desig:
        if getattr(rule_obj.designation, 'id', rule_obj.designation) != getattr(emp_desig, 'id', emp_desig):
            return False
    elif rule_obj.designation and not emp_desig:
        # rule requires a designation but employee doesn't have one -> no match
        return False

    if rule_obj.department and emp_dept:
        if getattr(rule_obj.department, 'id', rule_obj.department) != getattr(emp_dept, 'id', emp_dept):
            return False
    elif rule_obj.department and not emp_dept:
        return False

    if rule_obj.category and emp_cat:
        if getattr(rule_obj.category, 'id', rule_obj.category) != getattr(emp_cat, 'id', emp_cat):
            return False
    elif rule_obj.category and not emp_cat:
        return False

    # if no failing condition, it matches
    return True