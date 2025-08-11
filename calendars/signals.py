from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import (applicablity_critirea, emp_leave_balance, LvApprovalNotify,Attendance,employee_leave_request,leave_entitlement,
                     LvCommonWorkflow,LeaveApproval,LeaveApprovalLevels)
from EmpManagement.models import emp_master
from django.db import models  # Ensure models import is included
from .models import EmployeeYearlyCalendar
from datetime import timedelta
from datetime import date
from dateutil.relativedelta import relativedelta
from django.utils import timezone

import logging

logger = logging.getLogger(__name__)

@receiver(post_save, sender=applicablity_critirea)
def update_emp_leave_balance(sender, instance, created, **kwargs):
    leave_type_instance = instance.leave_type
    criteria = instance

    # Start with all employees
    employees = emp_master.objects.all()

    # Apply gender filter if specified
    if criteria.gender:
        employees = employees.filter(emp_gender=criteria.gender)

    # Apply branch filter if specified
    if criteria.branch.exists():
        employees = employees.filter(emp_branch_id__in=criteria.branch.all())

    # Apply department filter if specified
    if criteria.department.exists():
        employees = employees.filter(emp_dept_id__in=criteria.department.all())

    # Apply designation filter if specified
    if criteria.designation.exists():
        employees = employees.filter(emp_desgntn_id__in=criteria.designation.all())

    # Apply role filter if specified
    if criteria.role.exists():
        employees = employees.filter(emp_ctgry_id__in=criteria.role.all())

    # Create emp_leave_balance entries for each matching employee only if they don't already exist
    for employee in employees:
        emp_leave_balance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type_instance,
            defaults={'balance': 0, 'openings': 0}
        )

    print(f"Leave balance checked for employees: {', '.join([emp.emp_code for emp in employees])}")


@receiver(post_save, sender=emp_master)
def update_emp_leave_balance_on_employee_create(sender, instance, created, **kwargs):
    if created:
        logger.info(f"New employee created: {instance.emp_code}")

        # Get all applicable applicability criteria based on the new employee's attributes
        applicable_criteria = applicablity_critirea.objects.filter(
            models.Q(gender=instance.emp_gender) | models.Q(gender="B") | models.Q(gender=None),  # Include "Both" or None
            models.Q(branch=instance.emp_branch_id) | models.Q(branch=None),
            models.Q(department=instance.emp_dept_id) | models.Q(department=None),
            models.Q(designation=instance.emp_desgntn_id) | models.Q(designation=None),
            models.Q(role=instance.emp_ctgry_id) | models.Q(role=None),
        ).distinct()

        # Create emp_leave_balance entries for each applicable criteria only if they don't already exist
        for criteria in applicable_criteria:
            emp_leave_balance.objects.get_or_create(
                employee=instance,
                leave_type=criteria.leave_type,
                defaults={'balance': 0, 'openings': 0}
            )

            logger.info(f"Checked leave balance for employee {instance.emp_code} based on criteria: {criteria}")

        logger.info(f"Leave balance update completed for employee {instance.emp_code}")

@receiver(post_save, sender=Attendance)
def update_employee_yearly_calendar(sender, instance, **kwargs):
    # Extract information from the attendance instance
    employee = instance.employee
    attendance_date = instance.date
    year = attendance_date.year

    # Determine the status based on check-in/check-out times
    if instance.check_in_time and instance.check_out_time:
        status = "Present"
    else:
        status = "Absent"

    # Get or create the EmployeeYearlyCalendar for the given employee and year
    employee_calendar, created = EmployeeYearlyCalendar.objects.get_or_create(
        emp=employee,
        year=year,
        defaults={'daily_data': {}}
    )

    # Update the daily_data with the attendance status
    date_str = attendance_date.strftime("%Y-%m-%d")
    employee_calendar.daily_data[date_str] = {
        "status": status,
        "remarks": "Attended" if status == "Present" else "No attendance record"
    }

    # Save the updated employee calendar
    employee_calendar.save()


@receiver(post_save, sender=employee_leave_request)
def update_employee_calendar_for_approved_leave(sender, instance, **kwargs):
    # Check if the leave request is approved
    if instance.status == 'approved':
        employee = instance.employee
        leave_type_name = instance.leave_type.name
        start_date = instance.start_date
        end_date = instance.end_date
        year = start_date.year

        # Get the EmployeeYearlyCalendar for the given employee and year if it exists
        try:
            employee_calendar = EmployeeYearlyCalendar.objects.get(emp=employee, year=year)
            is_new = False
        except EmployeeYearlyCalendar.DoesNotExist:
            # Create a new EmployeeYearlyCalendar if not found
            employee_calendar = EmployeeYearlyCalendar(emp=employee, year=year, daily_data={})
            is_new = True

        # Update the daily_data with the leave type for each day in the date range
        date = start_date
        while date <= end_date:
            date_str = date.strftime("%Y-%m-%d")

            # Update or add new information to the daily_data dictionary
            employee_calendar.daily_data[date_str] = {
                "status": "Leave",
                "leave_type": leave_type_name,
                "remarks": f"{leave_type_name} leave approved"
            }
            date += timedelta(days=1)

        # Save the updated or newly created employee calendar
        employee_calendar.save()

@receiver(post_save, sender=emp_master)
def update_leave_balance(sender, instance, created, **kwargs):
    """
    Updates or creates prorated leave balance in emp_leave_balance when a new employee is created.
    Uses the same logic as accrue_leaves to determine entitlement.
    """
    if not created:  # Run only when a new employee is created
        return

    today = timezone.now().date()
    join_date = instance.emp_joined_date

    # Fetch entitlements where accrual and proration are enabled
    entitlements = leave_entitlement.objects.filter(accrual=True, prorate_accrual=True).order_by("leave_type", "min_experience")

    leave_type_entitlements = {}  # Store best entitlements per leave type

    for entitlement in entitlements:
        leave_type = entitlement.leave_type

        # Determine base date (Joining Date or Confirmation Date)
        base_date = (
            instance.emp_joined_date
            if entitlement.effective_after_from == "date_of_joining"
            else instance.emp_date_of_confirmation
        )

        if not base_date:
            continue  # Skip if no base date

        # Calculate experience in months
        experience = relativedelta(today, base_date)
        experience_in_months = experience.years * 12 + experience.months

        # Convert min_experience to months
        min_experience_months = (
            entitlement.min_experience * 12
            if entitlement.effective_after_unit == "years"
            else entitlement.min_experience
        )

        # Select the best entitlement for this leave type
        if (
            experience_in_months >= min_experience_months and
            (leave_type not in leave_type_entitlements or min_experience_months > leave_type_entitlements[leave_type]['experience'])
        ):
            leave_type_entitlements[leave_type] = {
                "entitlement": entitlement,
                "experience": min_experience_months
            }

    # Process proration for each selected entitlement
    for leave_type, data in leave_type_entitlements.items():
        best_entitlement = data["entitlement"]
        accrual_rate = best_entitlement.accrual_rate

        # Calculate Prorated Accrual
        if best_entitlement.accrual_frequency == "months":
            # Monthly accrual: Prorate based on remaining days in the month
            total_days_in_month = (date(join_date.year, join_date.month + 1, 1) - date(join_date.year, join_date.month, 1)).days
            remaining_days = total_days_in_month - join_date.day + 1
            prorated_accrual = (accrual_rate * remaining_days) / total_days_in_month

        elif best_entitlement.accrual_frequency == "years":
            # Yearly accrual: Prorate based on remaining months in the year
            remaining_months = 12 - join_date.month + 1
            prorated_accrual = (accrual_rate * remaining_months) / 12

        else:
            prorated_accrual = 0  # No proration for other frequencies

        prorated_accrual = round(prorated_accrual, 2)  # Round to 2 decimals

        # Update or create leave balance (Opening balance remains default 0)
        balance_obj, created = emp_leave_balance.objects.get_or_create(
            employee=instance,
            leave_type=leave_type,
            defaults={'balance': prorated_accrual}
        )

        if not created:
            balance_obj.balance = prorated_accrual
            balance_obj.save()

@receiver(post_save, sender=employee_leave_request)
def create_initial_approval(sender, instance, created, **kwargs):
    import logging
    logging.warning(f"Signal triggered for: {instance} created={created}")
    if created:
        if instance.leave_type.use_common_workflow:
            first_level = LvCommonWorkflow.objects.order_by('level').first()
        else:
        # Select the first approval level
            first_level = LeaveApprovalLevels.objects.filter(request_type=instance.leave_type).order_by('level').first()
            first_level = LeaveApprovalLevels.objects.filter(
                request_type=instance.leave_type,
                branch__id=instance.employee.emp_branch_id.id).order_by('level').first()
        if first_level:
            # Prevent duplicate creation of approvals at the same level
            if not instance.approvals.filter(level=first_level.level).exists():
                LeaveApproval.objects.create(
                    leave_request=instance,
                    approver=first_level.approver,
                    # role=first_level.role,
                    level=first_level.level,
                    status=LeaveApproval.PENDING,
                    employee_id=instance.employee_id
                )
            # Notify first approver
            notification = LvApprovalNotify.objects.create(
                recipient_user=first_level.approver,
                message=f"New request for approval: {instance.leave_type}, employee: {instance.employee}"
            )
            notification.send_email_notification('request_created', {
                'leave_type': instance.leave_type,
                'start_date':instance.start_date,
                'end_date':instance.end_date,
                'status':instance.status,
                'document_number':instance.document_number,
                'employee_name': instance.employee.emp_first_name,
                'reason': instance.reason,
                'emp_gender':instance.employee.emp_gender,
                'emp_date_of_birth':instance.employee.emp_date_of_birth,
                'emp_personal_email':instance.employee.emp_personal_email,
                'emp_branch_name':instance.employee.emp_branch_id,
                'emp_department_name':instance.employee.emp_dept_id,
                'emp_designation_name':instance.employee.emp_desgntn_id,
            }) 
