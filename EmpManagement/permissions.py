from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions
class IsSuperUserOrHasGeneralRequestPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        # Allow superusers full access
        if request.user.is_superuser:
            return True

        # Non-superusers: Check specific permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions
        required_permissions = [
            'view_GeneralRequest',
            'delete_GeneralRequest',
            'add_GeneralRequest',
            'change_GeneralRequest'
        ]

        # Check if the user has the necessary permissions
        for permission in required_permissions:
            if permission in [p.codename for p in user_permissions.group.permissions.all()]:
                return True

        return False

    def has_object_permission(self, request, view, obj):
        # Allow superusers full access
        if request.user.is_superuser:
            return True

        # Check if user is associated with the request (is_ess = True)
        if request.user.is_ess and request.user.username == obj.employee.emp_code:
            return True

        return False
    
class IsSuperUserOrInSameBranch(permissions.BasePermission):
    def has_permission(self, request, view):
        # Allow access to superusers
        if request.user.is_superuser:
            return True
        
        
        # Allow access to authenticated users
        if request.user.is_authenticated:
            return True
        # Deny access to unauthenticated users
        return False

    #     # Non-superusers: Check specific permissions
    #     try:
    #         user_permissions = UserTenantPermissions.objects.get(profile=request.user)
    #     except UserTenantPermissions.DoesNotExist:
    #         return False

    #     # Define required permissions
    #     required_permissions = [
    #         'view_report',
    #         'delete_report',
    #         'add_report',
    #         'change_report',
    #         'export_report'
    #     ]

    #     # Check if the user has the necessary permissions
    #     for permission in required_permissions:
    #         if permission in [p.codename for p in user_permissions.group.permissions.all()]:
    #             return True

    #     return False


    # def has_object_permission(self, request, view, obj):
    #     # Allow access to superusers
    #     if request.user.is_superuser:
    #         return True
        
    #     # Allow access to authenticated users in the same branch
    #     if request.user.is_authenticated:
    #         user_branch_id = request.user.branches
    #         return user_branch_id == obj.branches
        
    #     # Deny access to unauthenticated users
    #     return False


from rest_framework import permissions
# from .models import UserTenantPermissions  # Update this with the correct import path

class EmpCustomFieldPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Emp_CustomField.
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

        # Define required permissions for Emp_CustomField model actions
        required_permissions = [
            'view_emp_customfield',
            'add_emp_customfield',
            'change_emp_customfield',
            'delete_emp_customfield'
        ]

        # Retrieve user's permissions from their groups
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all()
            for p in group.permissions.all()
        ]

        # Check if any required permission is in user's group permissions
        return any(permission in user_group_permissions for permission in required_permissions)

class EmpCustomFieldValuePermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Emp_CustomFieldValue.
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
            'view_emp_customfieldvalue',
            'add_emp_customfieldvalue',
            'change_emp_customfieldvalue',
            'delete_emp_customfieldvalue'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all()
            for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class EmpFamilyCustomFieldPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for EmpFamily_CustomField.
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
            'view_empfamily_customfield',
            'add_empfamily_customfield',
            'change_empfamily_customfield',
            'delete_empfamily_customfield'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all()
            for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class EmpJobHistoryCustomFieldPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for EmpJobHistory_CustomField.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Retrieve user permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions for EmpJobHistory_CustomField model actions
        required_permissions = [
            'view_empjobhistory_customfield',
            'add_empjobhistory_customfield',
            'change_empjobhistory_customfield',
            'delete_empjobhistory_customfield',
        ]

        # Check if the user has any of the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Return True if any of the required permissions match
        return any(permission in user_group_permissions for permission in required_permissions)



class EmpQualificationCustomFieldPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for EmpQualification_CustomField.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Retrieve user permissions (adjust according to your UserTenantPermissions model)
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define the required permissions for EmpQualification_CustomField model actions
        required_permissions = [
            'view_empqualification_customfield',
            'add_empqualification_customfield',
            'change_empqualification_customfield',
            'delete_empqualification_customfield',
        ]

        # Retrieve the permissions for the user’s groups
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if any of the required permissions are present in the user's permissions
        return any(permission in user_group_permissions for permission in required_permissions)


# from rest_framework import permissions

class ReportPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the Report API.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Retrieve user permissions from UserTenantPermissions (modify if your model is different)
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Grant access if the user is a superuser
        if user_permissions.is_superuser:
            return True

        # Define the required permissions for the Report model
        required_permissions = ['view_report', 'add_report', 'change_report', 'delete_report', 'export_report']

        # Check if any of the user's group permissions match the required permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)

class DocReportPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the Doc_Report API.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = ['view_doc_report', 'add_doc_report', 'change_doc_report', 'delete_doc_report', 'export_document_report']

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)

class GeneralRequestReportPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the GeneralRequestReport API.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = ['view_generalrequestreport', 'add_generalrequestreport', 'change_generalrequestreport', 'delete_generalrequestreport', 'export_general_request_report']

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class NotificationPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the notification model.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = [
            'view_notification', 'add_notification', 'change_notification', 'delete_notification'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)



class EmployeeSkillPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the EmployeeSkill model.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = [
            'view_employeeskill', 'add_employeeskill', 'change_employeeskill', 'delete_employeeskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


from rest_framework import permissions

class EmployeeMarketingSkillPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions to access the EmployeeMarketingSkill model.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = [
            'view_employeemarketingskill', 'add_employeemarketingskill',
            'change_employeemarketingskill', 'delete_employeemarketingskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class EmployeeProgramSkillPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions to access the EmployeeProgramSkill model.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = [
            'view_employeeprogramskill', 'add_employeeprogramskill',
            'change_employeeprogramskill', 'delete_employeeprogramskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class EmployeeLangSkillPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions to access the EmployeeLangSkill model.
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

        if user_permissions.is_superuser:
            return True

        required_permissions = [
            'view_employeelangskill', 'add_employeelangskill',
            'change_employeelangskill', 'delete_employeelangskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)

class RequestTypePermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access RequestType API.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False

        # Grant access if the user is a superuser in the user model
        if request.user.is_superuser:
            return True

        # Grant access if the user has is_ess=True in the user model
        if hasattr(request.user, 'is_ess') and request.user.is_ess:
            return True

        # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Check if the user's group has any of the necessary permissions for RequestType
        required_permissions = ['view_requesttype', 'delete_requesttype', 'add_requesttype', 'change_requesttype']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class EmployeePermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access company API.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False

        # Grant access if the user is a superuser in the user model
        if request.user.is_superuser:
            return True

        # Grant access if the user has is_ess=True in the user model
        if hasattr(request.user, 'is_ess') and request.user.is_ess:
            return True

        # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Check if the user's group has any of the necessary permissions
        required_permissions = ['view_emp_master', 'delete_emp_master', 'add_emp_master', 'change_emp_master']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

class ApprovalLevelPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access company API.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False

        # Grant access if the user is a superuser in the user model
        if request.user.is_superuser:
            return True

        # Grant access if the user has is_ess=True in the user model
        if hasattr(request.user, 'is_ess') and request.user.is_ess:
            return True

        # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Check if the user's group has any of the necessary permissions
        required_permissions = ['view_approvallevel', 'delete_approvallevel', 'add_approvallevel', 'change_approvallevel']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

class EmployeeListPermission(permissions.BasePermission):
    """
    Custom permission to allow users with specific permissions for Emp_CustomField.
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

        # Define required permissions for Emp_CustomField model actions
        required_permissions = [
            'view_emp_customfield',
            'add_emp_customfield',
            'change_emp_customfield',
            'delete_emp_customfield'
        ]

        # Retrieve user's permissions from their groups
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all()
            for p in group.permissions.all()
        ]

        # Check if any required permission is in user's group permissions
        return any(permission in user_group_permissions for permission in required_permissions)