
from datetime import datetime, timedelta
from django.utils import timezone
from .models import leave_entitlement, assign_weekend,assign_holiday,AttendancePolicy

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
def _attplus_day(employee, day):
    """v1.12.0: the AttendancePlus daily result (None when the app is not installed / not calculated)."""
    try:
        from django.apps import apps
        if not apps.is_installed('AttendancePlus'):
            return None
        from AttendancePlus.models import AttendanceDay
        return AttendanceDay.objects.filter(employee_id=employee.id, date=day).first()
    except Exception:
        return None


def _absent_marking_on(employee):
    """v1.12.0: is a working day without any punch marked Absent for this employee?
    AttendancePlus rule "Mark absent if no punch" (+ exemptions) when installed, otherwise
    settings.ZEO_MARK_ABSENT_WITHOUT_PUNCH (default True)."""
    try:
        from AttendancePlus.engine import rule_for, _absence_exempt
        return not _absence_exempt(employee, rule_for(employee))
    except Exception:
        from django.conf import settings
        return bool(getattr(settings, 'ZEO_MARK_ABSENT_WITHOUT_PUNCH', True))


def _shift_off_day(employee, day):
    """v1.12.0: off day from the shift planner (rotations) when installed."""
    try:
        from ShiftPlanner.resolver import off_day
        return bool(off_day(employee, day))
    except Exception:
        return False


def _local_today():
    try:
        from AttendancePlus.engine import today
        return today()
    except Exception:
        return timezone.localdate()


def sync_attendance_calendar(employee, start_date, end_date):
    """
    Synchronize the AttendanceCalendar for an employee within a date range.

    v1.12.0: a past working day (not leave / holiday / weekend / shift off day) without any punch is
    marked Absent with unpaid_fraction 1 (payroll days_worked = days − unpaid fractions), instead of
    Present. Switch: AttendancePlus rule "Mark absent if no punch" with exemptions (manual-attendance
    employees, categories, employees) or settings.ZEO_MARK_ABSENT_WITHOUT_PUNCH. Today and future
    days keep the old behaviour (Present). AttendancePlus half days → Present, half day, unpaid 0.5.
    """
    from .models import AttendanceCalendar, Attendance, employee_leave_request
    
    weekend_days = get_employee_weekend_days(employee)
    holiday_dates = get_employee_holidays(employee, start_date, end_date)
    
    # Fetch existing attendances and leaves for bulk check
    attendances = set(Attendance.objects.filter(
        employee=employee, 
        date__range=(start_date, end_date),
        check_in_time__isnull=False,
    ).values_list('date', flat=True))
    
    leaves = employee_leave_request.objects.filter(
        employee=employee,
        status='approved',
        start_date__lte=end_date,
        end_date__gte=start_date
    ).select_related('leave_type')
    today = _local_today()
    mark_absent = None

    for day in daterange(start_date, end_date):
        # Skip if manual override exists
        entry = AttendanceCalendar.objects.filter(employee=employee, date=day).first()
        if entry and entry.is_manual:
            continue
        if getattr(employee, 'emp_joined_date', None) and day < employee.emp_joined_date:
            continue
            
        weekday = day.strftime("%A")
        status = "Absent"
        leave_obj = None
        is_half_day = False
        half_day_period = None
        unpaid_fraction = 0.0
        remarks = "Auto-synced"
        
        # 1. Check Leaves (priority)
        current_leave = None
        for l in leaves:
            if l.start_date <= day <= l.end_date:
                current_leave = l
                break
        
        if current_leave:
            status = "Leave"
            leave_obj = current_leave.leave_type
            is_half_day = current_leave.dis_half_day
            half_day_period = current_leave.half_day_period
            
            # Calculate unpaid fraction
            duration = 0.5 if is_half_day else 1.0
            if getattr(leave_obj, 'type', '') == 'unpaid':
                unpaid_fraction = duration
            else:
                unpaid_fraction = 0.0
            
        elif day in holiday_dates:
            status = "Holiday"
            unpaid_fraction = 0.0
        elif weekday in weekend_days or _shift_off_day(employee, day):
            status = "Weekend"
            unpaid_fraction = 0.0
        else:
            apd = _attplus_day(employee, day)
            apd_status = apd.status if apd else None
            if apd_status == 'weekly_off':
                status = "Weekend"
            elif apd_status == 'holiday':
                status = "Holiday"
            elif apd_status == 'absent':
                status, unpaid_fraction, remarks = "Absent", 1.0, "Absent (attendance rules)"
            elif apd_status == 'half_day':
                status, is_half_day, remarks = "Present", True, "Half day (attendance rules)"
                try:
                    from AttendancePlus.engine import rule_for
                    unpaid_fraction = float(rule_for(employee).half_day_unpaid_fraction)
                except Exception:
                    unpaid_fraction = 0.5
            elif apd_status in ('present', 'missing_punch') or day in attendances:
                status = "Present"
                if apd_status == 'missing_punch':
                    remarks = "Missing punch – correction needed"
            elif day < today:
                if mark_absent is None:
                    mark_absent = _absent_marking_on(employee)
                if mark_absent:
                    status, unpaid_fraction, remarks = "Absent", 1.0, "No punch"
                else:
                    status, remarks = "Present", "Present without punch (exempt)"
            else:
                # today / future: unchanged behaviour (expected present)
                status = "Present"
            unpaid_fraction = unpaid_fraction or 0.0

        AttendanceCalendar.objects.update_or_create(
            employee=employee,
            date=day,
            defaults={
                'status': status,
                'leave_type': leave_obj,
                'is_half_day': is_half_day,
                'half_day_period': half_day_period,
                'unpaid_fraction': unpaid_fraction,
                'remarks': remarks
            }
        )
        
def schedule_escalation(approval, level_rule):
    from django.db import connection
    if level_rule is None:  # no escalation rule configured for this level
        return
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

from datetime import timedelta
from decimal import Decimal
from django.db.models import Q
from calendars.models import EmployeeOvertime, OvertimePolicy
def calculate_employee_overtime(attendance):
    """
    v1.12.0: the ONE path that writes attendance overtime (calendars.Attendance.save calls only this).

    * With AttendancePlus installed the daily result engine does it (shift / rule thresholds, break
      deducted, weekly / monthly thresholds, rate per rule, approval kept).
    * Otherwise: one EmployeeOvertime row per employee / day / OT type (the table allows only one –
      the old code created a second "EXT" row and failed). OT = hours worked − break − shift hours
      (weekend / holiday: all hours), limited by the DAILY slabs of a matching OvertimePolicy when it
      has rules; without a policy all extra hours count (as before). Approval is kept unless the hours
      went up. Manual OT rows are never touched.
    """
    try:
        from django.apps import apps
        if apps.is_installed('AttendancePlus'):
            from AttendancePlus.engine import recompute_from_attendance
            return recompute_from_attendance(attendance)
    except ImportError:
        pass

    employee = attendance.employee
    rows = EmployeeOvertime.objects.filter(employee=employee, date=attendance.date).exclude(source='MANUAL')

    if not employee.emp_ot_applicable or not attendance.total_hours:
        rows.delete()
        return

    worked = attendance.total_hours

    # -------------------------
    # Determine OT TYPE
    # -------------------------
    if attendance.is_holiday():
        ot_type = 'HOLIDAY'
        base_duration = timedelta(0)

    elif attendance.is_weekend():
        ot_type = 'WEEKEND'
        base_duration = timedelta(0)

    else:
        ot_type = 'NORMAL'
        if not attendance.shift:
            rows.delete()
            return
        base_duration = attendance.get_shift_duration()
        brk = attendance.shift.break_duration or timedelta(0)
        worked = worked - brk            # unpaid break is not work
        base_duration = base_duration - brk

    # -------------------------
    # Fetch Policy
    # -------------------------
    policy = (
        OvertimePolicy.objects
        .filter(ot_type=ot_type, is_active=True)
        .filter(
            Q(branch__isnull=True) | Q(branch=employee.emp_branch_id),
            Q(department__isnull=True) | Q(department=employee.emp_dept_id),
            Q(designation__isnull=True) | Q(designation=employee.emp_desgntn_id),
            Q(category__isnull=True) | Q(category=employee.emp_ctgry_id),
        )
        .distinct()
        .first()
    )

    remaining = worked - base_duration
    total = timedelta(0)
    slab = 'OT'
    rules = list(policy.rules.filter(rule_type='DAILY', is_active=True)) if policy else []
    if rules:
        for rule in rules:
            if remaining <= timedelta(0):
                break
            part = min(remaining, rule.threshold_hours)
            total += part
            if rule.is_extended:
                slab = 'EXT'
            remaining -= part
    else:
        total = max(remaining, timedelta(0))

    rows.exclude(ot_type=ot_type).delete()
    if total <= timedelta(0):
        rows.filter(ot_type=ot_type).delete()
        return
    hours = (Decimal(total.total_seconds()) / Decimal(3600)).quantize(Decimal("0.01"))
    row = rows.filter(ot_type=ot_type).first()
    if row is None:
        if EmployeeOvertime.objects.filter(employee=employee, date=attendance.date, ot_type=ot_type).exists():
            return   # a manual OT row exists for this day / type
        EmployeeOvertime.objects.create(employee=employee, date=attendance.date, ot_type=ot_type, slab=slab,
                                        hours=hours, approved=False, created_by=attendance.created_by)
        return
    if row.hours != hours or row.slab != slab:
        if hours > row.hours:
            row.approved = False
        row.hours, row.slab = hours, slab
        row.save(update_fields=['hours', 'slab', 'approved'])



import math
from django.db.models import Q
from OrganisationManager.models import BranchGeoFence


def calculate_distance(lat1, lon1, lat2, lon2):

    R = 6371000

    lat1 = float(lat1)
    lon1 = float(lon1)
    lat2 = float(lat2)
    lon2 = float(lon2)

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def bounding_box(lat, lng, radius):

    lat = float(lat)
    lng = float(lng)

    lat_change = radius / 111111
    lng_change = radius / (111111 * math.cos(math.radians(lat)))

    return {
        "min_lat": lat - lat_change,
        "max_lat": lat + lat_change,
        "min_lng": lng - lng_change,
        "max_lng": lng + lng_change,
    }


def validate_employee_geofence(employee, lat, lng):

    if not lat or not lng:
        return False

    lat = float(lat)
    lng = float(lng)

    locations = BranchGeoFence.objects.filter(
        is_active=True
    ).filter(
        Q(employee__in=[employee]) | Q(branch=employee.emp_branch_id)
    ).distinct()
    print("GEOFENCE LOCATIONS FOUND:", locations.count(), list(locations.values_list('id', 'location_name')))
    if not locations.exists():
        return True  # No restriction → allow

    for loc in locations:

        box = bounding_box(loc.latitude, loc.longitude, loc.radius)

        if not (
            box["min_lat"] <= lat <= box["max_lat"]
            and box["min_lng"] <= lng <= box["max_lng"]
        ):
            continue

        distance = calculate_distance(
            loc.latitude,
            loc.longitude,
            lat,
            lng
        )

        if distance <= loc.radius:
            return True

    return False
def get_employee_attendance_policy(employee):
    """
    Return the AttendancePolicy that applies to this employee,
    resolved through AttendancePolicyAssignment by priority:
    employee → designation → category → department → branch.
    Falls back to the old branch-based AttendancePolicy lookup.
    """
    from .models import AttendancePolicyAssignment

    # 1. Employee-level
    assignment = AttendancePolicyAssignment.objects.filter(
        related_to='employee',
        employee=employee
    ).select_related('attendance_policy').first()
    if assignment:
        return assignment.attendance_policy

    # 2. Designation-level
    if employee.emp_desgntn_id:
        assignment = AttendancePolicyAssignment.objects.filter(
            related_to='designation',
            designation=employee.emp_desgntn_id
        ).select_related('attendance_policy').first()
        if assignment:
            return assignment.attendance_policy

    # 3. Category-level
    if employee.emp_ctgry_id:
        assignment = AttendancePolicyAssignment.objects.filter(
            related_to='category',
            category=employee.emp_ctgry_id
        ).select_related('attendance_policy').first()
        if assignment:
            return assignment.attendance_policy

    # 4. Department-level
    if employee.emp_dept_id:
        assignment = AttendancePolicyAssignment.objects.filter(
            related_to='department',
            department=employee.emp_dept_id
        ).select_related('attendance_policy').first()
        if assignment:
            return assignment.attendance_policy

    # 5. Branch-level (via assignment or direct policy)
    if employee.emp_branch_id:
        assignment = AttendancePolicyAssignment.objects.filter(
            related_to='branch',
            branch=employee.emp_branch_id
        ).select_related('attendance_policy').first()
        if assignment:
            return assignment.attendance_policy

    # 6. Fallback: direct branch-based AttendancePolicy
    return

#attendance policy
def get_active_policy(employee):
    return AttendancePolicy.objects.filter(
        branch=employee.emp_branch_id,
        is_active=True
    ).first()


def _shift_window(day, shift, check_in_time=None):
    """v1.12.0: shift start / end datetimes for a day (end on the next day for cross-midnight shifts)."""
    from datetime import date as _d
    day = day or _d.today()
    start = datetime.combine(day, shift.start_time)
    end = datetime.combine(day, shift.end_time)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def apply_check_in_policy(employee, check_in_time, shift=None, day=None):
    """
    Apply attendance policy rules for check-in.
    - Rounds time if round_off is enabled.
    - Returns (adjusted_time, is_late). v1.12.0: is_late compares with the shift start + the late
      check-in minutes of the policy (before, it was True for every check-in whenever the policy had
      late rules on, without looking at the shift).
    """
    from datetime import date as _d
    policy = get_employee_attendance_policy(employee)

    dt = datetime.combine(day or _d.today(), check_in_time)

    if policy and policy.round_off:
        minutes = (dt.minute // 5) * 5
        dt = dt.replace(minute=minutes, second=0)

    is_late = False
    if shift is not None and getattr(shift, 'start_time', None) and getattr(shift, 'end_time', None):
        grace = policy.late_check_in_minutes if (policy and policy.late_check_in) else 0
        start, _ = _shift_window(dt.date(), shift)
        is_late = dt > start + timedelta(minutes=grace)

    return dt.time(), is_late


def apply_check_out_policy(employee, check_out_time, shift=None, day=None, check_in_time=None):
    """
    Apply attendance policy rules for check-out.
    - Rounds time if round_off is enabled.
    - Returns (adjusted_time, is_early). v1.12.0: compares with the shift end − early check-out
      minutes, with the end on the next day for a cross-midnight shift.
    """
    from datetime import date as _d
    policy = get_employee_attendance_policy(employee)

    base = day or _d.today()
    dt = datetime.combine(base, check_out_time)
    if check_in_time and check_out_time < check_in_time:
        dt += timedelta(days=1)       # out after midnight belongs to the shift start day

    if policy and policy.round_off:
        minutes = (dt.minute // 5) * 5
        dt = dt.replace(minute=minutes, second=0)

    is_early = False
    if shift is not None and getattr(shift, 'start_time', None) and getattr(shift, 'end_time', None):
        grace = policy.early_check_out_minutes if (policy and policy.early_check_out) else 0
        _, end = _shift_window(base, shift)
        is_early = dt < end - timedelta(minutes=grace)

    return dt.time(), is_early

def get_employee_attendance_validation_policy(employee):
    """
    Retrieves the active AttendanceValidationPolicy for the employee
    based on priority: employee > category > department > branch > company.
    v1.12.0: the most specific one wins (before, each step overwrote the previous one, so only
    the company-wide policy was ever used).
    """
    from .models import AttendanceValidationPolicy
    qs = AttendanceValidationPolicy.objects.filter(is_active=True)
    for field, value in (('employee', employee), ('designation', employee.emp_desgntn_id),
                         ('category', employee.emp_ctgry_id), ('department', employee.emp_dept_id),
                         ('branch', employee.emp_branch_id)):
        if value:
            policy = qs.filter(**{field: value}).first()
            if policy:
                return policy

    # Company
    return qs.filter(
        employee__isnull=True,
        designation__isnull=True,
        category__isnull=True,
        department__isnull=True,
        branch__isnull=True,
    ).first()


def _penalty_policy(model, employee, att_policy):
    qs = model.objects.filter(enabled=True)
    for field, value in (('employee', employee), ('designation', employee.emp_desgntn_id),
                         ('category', employee.emp_ctgry_id), ('department', employee.emp_dept_id),
                         ('branch', employee.emp_branch_id)):
        if value:
            p = qs.filter(**{field: value}).first()
            if p:
                return p
    if att_policy:
        return qs.filter(attendance_policy=att_policy).first()
    return None


def _violation_days(employee, start, end, kind, grace_minutes):
    """v1.12.0: dates late (kind='late') / early in [start, end]. AttendancePlus daily results when
    installed (corrections that waive the penalty are skipped), else the Attendance rows vs shift."""
    from calendars.models import Attendance
    try:
        from django.apps import apps
        if apps.is_installed('AttendancePlus'):
            from AttendancePlus.models import AttendanceDay
            flt = {'is_late': True} if kind == 'late' else {'is_early': True}
            return set(AttendanceDay.objects.filter(employee_id=employee.id, date__range=(start, end),
                                                    penalty_waived=False, **flt).values_list('date', flat=True))
    except ImportError:
        pass
    days = set()
    for att in Attendance.objects.filter(employee=employee, date__range=(start, end)).select_related('shift'):
        sh = att.shift
        if not (sh and sh.start_time and sh.end_time):
            continue
        s, e = _shift_window(att.date, sh)
        if kind == 'late' and att.check_in_time:
            if datetime.combine(att.date, att.check_in_time) > s + timedelta(minutes=grace_minutes):
                days.add(att.date)
        elif kind == 'early' and att.check_out_time and att.check_in_time:
            out = datetime.combine(att.date, att.check_out_time)
            if att.check_out_time < att.check_in_time:
                out += timedelta(days=1)
            if out < e - timedelta(minutes=grace_minutes):
                days.add(att.date)
    return days


def apply_late_early_penalties(attendance):
    """
    Evaluates the given Attendance record against LateComingPolicy and EarlyExitPolicy.
    If violations reach the threshold for the evaluation period, deducts leaves.

    v1.12.0: uses the real fields (late_occurrence_limit / occurrence_limit – the old code read
    threshold_count and failed with AttributeError on every check-out when a policy matched),
    compares date-times (cross-midnight shifts), finds the policy by employee / designation /
    category / department / branch, and skips days whose penalty was waived by a correction.
    """
    from calendars.models import LateComingPolicy, EarlyExitPolicy

    employee = attendance.employee
    att_policy = get_active_policy(employee)
    day = attendance.date
    start_of_month = day.replace(day=1)

    for kind, model, limit_field in (('late', LateComingPolicy, 'late_occurrence_limit'),
                                     ('early', EarlyExitPolicy, 'occurrence_limit')):
        policy = _penalty_policy(model, employee, att_policy)
        if not policy:
            continue
        base = att_policy or policy.attendance_policy
        if kind == 'late':
            grace = base.late_check_in_minutes if (base and base.late_check_in) else 0
        else:
            grace = base.early_check_out_minutes if (base and base.early_check_out) else 0
        start = start_of_month if getattr(policy, 'reset_monthly', True) else day.replace(month=1, day=1)
        days = _violation_days(employee, start, day, kind, grace)
        if day not in days:
            continue
        limit = getattr(policy, limit_field, 0) or 0
        count = len(days)
        if limit and count % limit == 0:
            label = 'Late Coming' if kind == 'late' else 'Early Exit'
            apply_penalty(employee, policy, day, f"{label} Penalty ({count} occurrences)", kind=kind)


def apply_penalty(employee, policy, date, reason, kind='late'):
    """
    Creates an auto-approved leave request to deduct leave balance as penalty.
    v1.12.0: the leave type comes from the employee attendance policy (late / early "deduct from
    leave type"); LateComingPolicy / EarlyExitPolicy have no such field (the old code failed).
    Without a leave type the penalty days are recorded on the AttendancePlus daily result
    (payroll variable late_penalty_days) instead.
    """
    from calendars.models import employee_leave_request

    leave_type = getattr(policy, 'deduct_from_leave_type', None)
    if not leave_type:
        emp_policy = get_employee_attendance_policy(employee)
        if emp_policy:
            leave_type = getattr(emp_policy, f'{kind}_deduct_from_leave_type', None)
    days_to_deduct = float(policy.leave_days_to_deduct or 0)
    if getattr(policy, 'penalty_type', '') == 'full_day':
        days_to_deduct = max(days_to_deduct, 1.0)
    if not days_to_deduct:
        return
    if not leave_type:
        try:
            from AttendancePlus.models import AttendanceDay
            d = AttendanceDay.objects.filter(employee_id=employee.id, date=date).first()
            if d:
                d.flags = dict(d.flags or {}, **{f'{kind}_penalty': days_to_deduct, 'penalty_reason': reason})
                if kind == 'late':
                    d.flags['late_penalty'] = days_to_deduct
                d.save(update_fields=['flags'])
        except Exception:
            pass
        return

    is_half_day = days_to_deduct <= 0.5
    
    # Avoid duplicating penalties for the exact same date and reason
    if employee_leave_request.objects.filter(employee=employee, start_date=date, reason=reason).exists():
        return
    
    employee_leave_request.objects.create(
        employee=employee,
        branch=employee.emp_branch_id,
        leave_type=leave_type,
        start_date=date,
        end_date=date,
        reason=reason,
        status='approved',
        dis_half_day=is_half_day,
        half_day_period='first_half' if is_half_day else None,
        number_of_days=days_to_deduct,
        applied_days=days_to_deduct,
        approved_days=days_to_deduct
    )

from .models import (
    emp_leave_balance,
    leave_type,
    applicablity_critirea,
)


def is_leave_type_applicable(employee, leave_type_instance):
    """
    Check whether a leave type is applicable to an employee
    based on applicability criteria.

    Empty criteria means there is no restriction.

    Returns:
        True  -> applicable
        False -> not applicable
    """

    criteria_list = (
        applicablity_critirea.objects
        .filter(leave_type=leave_type_instance)
        .prefetch_related(
            'branch',
            'department',
            'designation',
            'role'
        )
    )

    # ------------------------------------------
    # No criteria = applicable to everyone
    # ------------------------------------------

    if not criteria_list.exists():
        return True

    # ------------------------------------------
    # Employee branch
    # ------------------------------------------

    employee_branch = employee.emp_branch_id

    if employee_branch:
        employee_branch_pk = (
            employee_branch.pk
            if hasattr(employee_branch, 'pk')
            else employee_branch
        )
    else:
        employee_branch_pk = None

    # ------------------------------------------
    # Employee department
    # ------------------------------------------

    employee_department = employee.emp_dept_id

    if employee_department:
        employee_department_pk = (
            employee_department.pk
            if hasattr(employee_department, 'pk')
            else employee_department
        )
    else:
        employee_department_pk = None

    # ------------------------------------------
    # Employee designation
    # ------------------------------------------

    employee_designation = employee.emp_desgntn_id

    if employee_designation:
        employee_designation_pk = (
            employee_designation.pk
            if hasattr(employee_designation, 'pk')
            else employee_designation
        )
    else:
        employee_designation_pk = None

    # ------------------------------------------
    # Employee role/category
    # ------------------------------------------

    employee_role = employee.emp_ctgry_id

    if employee_role:
        employee_role_pk = (
            employee_role.pk
            if hasattr(employee_role, 'pk')
            else employee_role
        )
    else:
        employee_role_pk = None

    # ==========================================
    # CHECK EACH CRITERIA
    # ==========================================

    for criteria in criteria_list:

        # ======================================
        # GENDER
        # ======================================

        if criteria.gender:

            if criteria.gender == "B":

                gender_match = True

            else:

                gender_match = (
                    employee.emp_gender == criteria.gender
                )

            if not gender_match:
                continue

        # ======================================
        # BRANCH
        # ======================================

        branches = criteria.branch.all()

        if branches.exists():

            if employee_branch_pk is None:
                continue

            if not branches.filter(
                pk=employee_branch_pk
            ).exists():
                continue

        # ======================================
        # DEPARTMENT
        # ======================================

        departments = criteria.department.all()

        if departments.exists():

            if employee_department_pk is None:
                continue

            if not departments.filter(
                pk=employee_department_pk
            ).exists():
                continue

        # ======================================
        # DESIGNATION
        # ======================================

        designations = criteria.designation.all()

        if designations.exists():

            if employee_designation_pk is None:
                continue

            if not designations.filter(
                pk=employee_designation_pk
            ).exists():
                continue

        # ======================================
        # ROLE
        # ======================================

        roles = criteria.role.all()

        if roles.exists():

            if employee_role_pk is None:
                continue

            if not roles.filter(
                pk=employee_role_pk
            ).exists():
                continue

        # ======================================
        # ALL CONDITIONS MATCH
        # ======================================

        return True

    return False


def get_or_create_applicable_leave_balance(
    employee,
    leave_type_instance
):
    """
    Get existing leave balance.

    If the employee is applicable but the balance
    does not exist, create it.
    """

    # ------------------------------------------
    # CHECK APPLICABILITY
    # ------------------------------------------

    applicable = is_leave_type_applicable(
        employee,
        leave_type_instance
    )

    if not applicable:
        return None, False

    # ------------------------------------------
    # GET OR CREATE
    # ------------------------------------------

    leave_balance, created = (
        emp_leave_balance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type_instance,
            defaults={
                'balance': 0,
                'openings': 0
            }
        )
    )

    return leave_balance, created




def get_or_create_applicable_leave_balance(
    employee,
    leave_type_instance
):
    """
    Get an existing leave balance.

    If it does not exist but the leave is applicable,
    create the leave balance.

    Returns:
        (leave_balance, created)

    If leave is not applicable:
        (None, False)
    """

    # ==========================================
    # CHECK APPLICABILITY
    # ==========================================

    applicable = is_leave_type_applicable(
        employee,
        leave_type_instance
    )

    if not applicable:
        return None, False

    # ==========================================
    # GET OR CREATE BALANCE
    # ==========================================

    leave_balance, created = (
        emp_leave_balance.objects.get_or_create(
            employee=employee,
            leave_type=leave_type_instance,
            defaults={
                'balance': 0,
                'openings': 0
            }
        )
    )

    return leave_balance, created