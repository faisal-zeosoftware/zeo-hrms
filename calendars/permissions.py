from rest_framework import permissions

from tenant_users.tenants.models import UserTenantPermissions

class WeekendCalendarPermission(permissions.BasePermission):
    """
    Custom permission for the weekend_calendar model.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for weekend_calendar actions
        required_permissions = [
            'view_weekendcalendar',
            'add_weekendcalendar',
            'change_weekendcalendar',
            'delete_weekendcalendar'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class WeekendDetailPermission(permissions.BasePermission):
    """
    Custom permission for the WeekendDetail model.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        required_permissions = [
            'view_weekenddetail',
            'add_weekenddetail',
            'change_weekenddetail',
            'delete_weekenddetail'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class AssignWeekendPermission(permissions.BasePermission):
    """
    Custom permission for the assign_weekend model.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        required_permissions = [
            'view_assignweekend',
            'add_assignweekend',
            'change_assignweekend',
            'delete_assignweekend'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class HolidayPermission(permissions.BasePermission):
    """
    Custom permission for the holiday model.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        required_permissions = [
            'view_holiday',
            'add_holiday',
            'change_holiday',
            'delete_holiday'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class HolidayCalendarPermission(permissions.BasePermission):
    """
    Custom permission for the holiday_calendar model.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        required_permissions = [
            'view_holidaycalendar',
            'add_holidaycalendar',
            'change_holidaycalendar',
            'delete_holidaycalendar'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)

class AssignHolidayPermission(permissions.BasePermission):
    """
    Custom permission for the assign_holiday model.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        required_permissions = [
            'view_assignholiday',
            'add_assignholiday',
            'change_assignholiday',
            'delete_assignholiday'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


#leave
from rest_framework import permissions

from tenant_users.tenants.models import UserTenantPermissions

class LeaveTypePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for leave_type.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for leave_type model actions
        required_permissions = ['view_leave_type', 'add_leave_type', 'change_leave_type', 'delete_leave_type']

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [p.codename for group in user_permissions.groups.all() for p in group.permissions.all()]
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class LeaveEntitlementPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for leave_entitlement.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for leave_entitlement model actions
        required_permissions = [
            'view_leave_entitlement',
            'add_leave_entitlement',
            'change_leave_entitlement',
            'delete_leave_entitlement'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]
        
        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class EmpLeaveBalancePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for emp_leave_balance.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for emp_leave_balance model actions
        required_permissions = [
            'view_emp_leave_balance',
            'add_emp_leave_balance',
            'change_emp_leave_balance',
            'delete_emp_leave_balance'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class ApplicabilityCriteriaPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for applicability_criteria.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for applicability_criteria model actions
        required_permissions = [
            'view_applicability_criteria',
            'add_applicability_criteria',
            'change_applicability_criteria',
            'delete_applicability_criteria'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False



class EmployeeLeaveRequestPermission(permissions.BasePermission):
    """
    Custom permission to allow specific users to access and create leave requests.
    ESS users can only access and create requests for their own employee ID.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        
        # Grant access if the user is a superuser
        if request.user.is_superuser:
            return True

        # Check if the user has is_ess=True
        if hasattr(request.user, 'is_ess') and request.user.is_ess:
            # If accessing or creating, ensure they are limited to their own employee record
            employee_id = request.user.username  # Assuming username represents the employee ID

            # For read actions, ensure user only sees their own employee details
            if view.action in ['retrieve', 'list']:
                requested_employee_id = view.kwargs.get('pk')
                return str(requested_employee_id) == str(employee_id)
            
            # For create actions, ensure the leave request is for the user's own employee ID
            if view.action == 'create':
                # In the viewset, ensure `employee_id` is automatically set to the user's employee ID
                return True

        # Retrieve permissions for other users
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for employee model actions
        required_permissions = ['view_employee_leave_request', 'delete_employee_leave_request', 'add_employee_leave_request', 'change_employee_leave_request']
        user_group_permissions = [p.codename for group in user_permissions.groups.all() for p in group.permissions.all()]

        # Allow access if the user's group has any of the necessary permissions
        return any(permission in user_group_permissions for permission in required_permissions)

class LvEmailTemplatePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvEmailTemplate.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LvEmailTemplate model actions
        required_permissions = [
            'view_lvemailtemplate',
            'add_lvemailtemplate',
            'change_lvemailtemplate',
            'delete_lvemailtemplate'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class LeaveResetTransactionPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvEmailTemplate.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LvEmailTemplate model actions
        required_permissions = [
            'view_leave_reset_transaction',
            'add_leave_reset_transaction',
            'change_leave_reset_transaction',
            'delete_leave_reset_transaction'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class LeaveAccrualTransactionPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvEmailTemplate.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LvEmailTemplate model actions
        required_permissions = [
            'view_leave_accrual_transaction',
            'add_leave_accrual_transaction',
            'change_leave_accrual_transaction',
            'delete_leave_accrual_transaction'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False
    
class LvCommonWorkflowPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvCommonWorkflow.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LvCommonWorkflow model actions
        required_permissions = [
            'view_lvcommonworkflow',
            'add_lvcommonworkflow',
            'change_lvcommonworkflow',
            'delete_lvcommonworkflow'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class LvRejectionReasonPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvRejectionReason.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LvRejectionReason model actions
        required_permissions = [
            'view_lvrejectionreason',
            'add_lvrejectionreason',
            'change_lvrejectionreason',
            'delete_lvrejectionreason'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class LeaveApprovalLevelsPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LeaveApprovalLevels.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LeaveApprovalLevels model actions
        required_permissions = [
            'view_leaveapprovallevels',
            'add_leaveapprovallevels',
            'change_leaveapprovallevels',
            'delete_leaveapprovallevels'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class EmployeeMachineMappingPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for EmployeeMachineMapping.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for EmployeeMachineMapping model actions
        required_permissions = [
            'view_employeemachinemapping',
            'add_employeemachinemapping',
            'change_employeemachinemapping',
            'delete_employeemachinemapping'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class ShiftPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Shift.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for Shift model actions
        required_permissions = [
            'view_shift',
            'add_shift',
            'change_shift',
            'delete_shift'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class ShiftPatternPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Shift.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for Shift model actions
        required_permissions = [
            'view_shiftpattern',
            'add_shiftpattern',
            'change_shiftpattern',
            'delete_shiftpattern'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class ShiftOverridePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Shift.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for Shift model actions
        required_permissions = [
            'view_shiftoverride',
            'add_shiftoverride',
            'change_shiftoverride',
            'delete_shiftoverride'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class EmployeeShiftSchedulePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Shift.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for Shift model actions
        required_permissions = [
            'view_employeeshiftschedule',
            'add_employeeshiftschedule',
            'change_employeeshiftschedule',
            'delete_employeeshiftschedule'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class WeekPatternAssignmentPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for WeeklyShiftSchedule.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for WeeklyShiftSchedule model actions
        required_permissions = [
            'view_weekpatternassignment',
            'add_weekpatternassignment',
            'change_weekpatternassignment',
            'delete_weekpatternassignment'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class AttendancePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Attendance.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for Attendance model actions
        required_permissions = [
            'view_attendance',
            'add_attendance',
            'change_attendance',
            'delete_attendance'
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class LeaveReportPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LeaveReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LeaveReport model actions
        required_permissions = [
            'view_leavereport',
            'add_leavereport',
            'change_leavereport',
            'delete_leavereport',
            # 'export_report'  # Custom permission for exporting reports
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class LeaveApprovalReportPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LeaveApprovalReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for LeaveApprovalReport model actions
        required_permissions = [
            'view_leaveapprovalreport',
            'add_leaveapprovalreport',
            'change_leaveapprovalreport',
            'delete_leaveapprovalreport',
            # 'export_report'  # Custom permission for exporting reports
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class AttendanceReportPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for AttendanceReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for AttendanceReport model actions
        required_permissions = [
            'view_attendancereport',
            'add_attendancereport',
            'change_attendancereport',
            'delete_attendancereport',
            # 'export_report'  # Custom permission for exporting reports
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False


class LvBalanceReportPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvBalanceReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for lvBalanceReport model actions
        required_permissions = [
            'view_lvbalancereport',
            'add_lvbalancereport',
            'change_lvbalancereport',
            'delete_lvbalancereport',
            'export_report'  # Custom permission for exporting reports
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False
class CompensatoryLeaveRequestPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvBalanceReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for lvBalanceReport model actions
        required_permissions = [
            'view_compensatoryleaverequest',
            'add_compensatoryleaverequestt',
            'change_compensatoryleaverequest',
            'delete_compensatoryleaverequest',
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False

class CompensatoryLeaveBalancePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvBalanceReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for lvBalanceReport model actions
        required_permissions = [
            'view_compensatoryleavebalance',
            'add_compensatoryleavebalance',
            'change_compensatoryleavebalance',
            'delete_compensatoryleavebalance',
           
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False
class CompensatoryLeaveTransactionPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvBalanceReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for lvBalanceReport model actions
        required_permissions = [
            'view_compensatoryleavetransaction',
            'add_compensatoryleavetransaction',
            'change_compensatoryleavetransaction',
            'delete_compensatoryleavetransaction',
            
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False
class EmployeeYearlyCalendarPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for LvBalanceReport.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Attempt to retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for lvBalanceReport model actions
        required_permissions = [
            'view_employeeyearlycalendar',
            'add_employeeyearlycalendar',
            'change_employeeyearlycalendar',
            'delete_employeeyearlycalendar',
            
        ]

        # Check if any group the user belongs to has the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if user has at least one of the required permissions
        if any(permission in user_group_permissions for permission in required_permissions):
            return True

        return False