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
            if permission in [p.codename for p in user_permissions.groups.permissions.all()]:
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

class EmpCustomFieldPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_emp_customfield',
            'retrieve': 'view_emp_customfield',
            'create': 'add_emp_customfield',
            'update': 'change_emp_customfield',
            'partial_update': 'change_emp_customfield',
            'destroy': 'delete_emp_customfield',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False


class EmpCustomFieldValuePermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_emp_customfieldvalue',
            'retrieve': 'view_emp_customfieldvalue',
            'create': 'add_emp_customfieldvalue',
            'update': 'change_emp_customfieldvalue',
            'partial_update': 'change_emp_customfieldvalue',
            'destroy': 'delete_emp_customfieldvalue',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

class EmpFamilyCustomFieldPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_empfamily_customfield',
            'retrieve': 'view_empfamily_customfield',
            'create': 'add_empfamily_customfield',
            'update': 'change_empfamily_customfield',
            'partial_update': 'change_empfamily_customfield',
            'destroy': 'delete_empfamily_customfield',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmpJobHistoryCustomFieldPermission(permissions.BasePermission):
#     """
#     Custom permission to allow users with specific permissions for EmpJobHistory_CustomField.
#     """

#     def has_permission(self, request, view):
#         # Check if the user is authenticated
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         # Retrieve user permissions
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         # Define required permissions for EmpJobHistory_CustomField model actions
#         required_permissions = [
#             'view_empjobhistory_customfield',
#             'add_empjobhistory_customfield',
#             'change_empjobhistory_customfield',
#             'delete_empjobhistory_customfield',
#         ]

#         # Check if the user has any of the required permissions
#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         # Return True if any of the required permissions match
#         return any(permission in user_group_permissions for permission in required_permissions)


class EmpJobHistoryCustomFieldPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_empjobhistory_customfield',
            'retrieve': 'view_empjobhistory_customfield',
            'create': 'add_empjobhistory_customfield',
            'update': 'change_empjobhistory_customfield',
            'partial_update': 'change_empjobhistory_customfield',
            'destroy': 'delete_empjobhistory_customfield',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmpQualificationCustomFieldPermission(permissions.BasePermission):
#     """
#     Custom permission to allow users with specific permissions for EmpQualification_CustomField.
#     """

#     def has_permission(self, request, view):
#         # Check if the user is authenticated
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         # Retrieve user permissions (adjust according to your UserTenantPermissions model)
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         # Define the required permissions for EmpQualification_CustomField model actions
#         required_permissions = [
#             'view_empqualification_customfield',
#             'add_empqualification_customfield',
#             'change_empqualification_customfield',
#             'delete_empqualification_customfield',
#         ]

#         # Retrieve the permissions for the user’s groups
#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         # Check if any of the required permissions are present in the user's permissions
#         return any(permission in user_group_permissions for permission in required_permissions)


# from rest_framework import permissions
class EmpQualificationCustomFieldPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_empqualification_customfield',
            'retrieve': 'view_empqualification_customfield',
            'create': 'add_empqualification_customfield',
            'update': 'change_empqualification_customfield',
            'partial_update': 'change_empqualification_customfield',
            'destroy': 'delete_empqualification_customfield',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class ReportPermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access the Report API.
#     """

#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         # Retrieve user permissions from UserTenantPermissions (modify if your model is different)
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         # Grant access if the user is a superuser
#         if user_permissions.is_superuser:
#             return True

#         # Define the required permissions for the Report model
#         required_permissions = ['view_report', 'add_report', 'change_report', 'delete_report', 'export_report']

#         # Check if any of the user's group permissions match the required permissions
#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]
#         return any(permission in user_group_permissions for permission in required_permissions)
class ReportPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_report',
            'retrieve': 'view_report',
            'create': 'add_report',
            'update': 'change_report',
            'partial_update': 'change_report',
            'destroy': 'delete_report',
            'export': 'export_report'
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class DocReportPermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access the Doc_Report API.
#     """

#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = ['view_doc_report', 'add_doc_report', 'change_doc_report', 'delete_doc_report', 'export_document_report']

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)
class DocReportPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_doc_report',
            'retrieve': 'view_doc_report',
            'create': 'add_doc_report',
            'update': 'change_doc_report',
            'partial_update': 'change_doc_report',
            'destroy': 'delete_doc_report',
            'export': 'export_report'
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class GeneralRequestReportPermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access the GeneralRequestReport API.
#     """

#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = ['view_generalrequestreport', 'add_generalrequestreport', 'change_generalrequestreport', 'delete_generalrequestreport', 'export_general_request_report']

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)

class GeneralRequestReportPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_generalrequestreport',
            'retrieve': 'view_generalrequestreport',
            'create': 'add_generalrequestreport',
            'update': 'change_generalrequestreport',
            'partial_update': 'change_generalrequestreport',
            'destroy': 'delete_generalrequestreport',
            'export': 'export_report'
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class NotificationPermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access the notification model.
#     """
#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = [
#             'view_notification', 'add_notification', 'change_notification', 'delete_notification'
#         ]

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)


class NotificationPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_notification',
            'retrieve': 'view_notification',
            'create': 'add_notification',
            'update': 'change_notification',
            'partial_update': 'change_notification',
            'destroy': 'delete_notification',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmployeeSkillPermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access the EmployeeSkill model.
#     """
#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = [
#             'view_employeeskill', 'add_employeeskill', 'change_employeeskill', 'delete_employeeskill'
#         ]

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)


class EmployeeSkillPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_employeeskill',
            'retrieve': 'view_employeeskill',
            'create': 'add_employeeskill',
            'update': 'change_employeeskill',
            'partial_update': 'change_employeeskill',
            'destroy': 'delete_employeeskill',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmployeeMarketingSkillPermission(permissions.BasePermission):
#     """
#     Custom permission to allow users with specific permissions to access the EmployeeMarketingSkill model.
#     """
#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = [
#             'view_employeemarketingskill', 'add_employeemarketingskill',
#             'change_employeemarketingskill', 'delete_employeemarketingskill'
#         ]

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)

class EmployeeMarketingSkillPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_employeemarketingskill',
            'retrieve': 'view_employeemarketingskill',
            'create': 'add_employeemarketingskill',
            'update': 'change_employeemarketingskill',
            'partial_update': 'change_employeemarketingskill',
            'destroy': 'delete_employeemarketingskill',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmployeeProgramSkillPermission(permissions.BasePermission):
#     """
#     Custom permission to allow users with specific permissions to access the EmployeeProgramSkill model.
#     """
#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = [
#             'view_employeeprogramskill', 'add_employeeprogramskill',
#             'change_employeeprogramskill', 'delete_employeeprogramskill'
#         ]

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)

class EmployeeProgramSkillPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_employeeprogramskill',
            'retrieve': 'view_employeeprogramskill',
            'create': 'add_employeeprogramskill',
            'update': 'change_employeeprogramskill',
            'partial_update': 'change_employeeprogramskill',
            'destroy': 'delete_employeeprogramskill',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmployeeLangSkillPermission(permissions.BasePermission):
#     """
#     Custom permission to allow users with specific permissions to access the EmployeeLangSkill model.
#     """
#     def has_permission(self, request, view):
#         if not request.user.is_authenticated:
#             return False
#         if request.user.is_superuser:
#             return True
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         if user_permissions.is_superuser:
#             return True

#         required_permissions = [
#             'view_employeelangskill', 'add_employeelangskill',
#             'change_employeelangskill', 'delete_employeelangskill'
#         ]

#         user_group_permissions = [
#             p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
#         ]

#         return any(permission in user_group_permissions for permission in required_permissions)
class EmployeeLangSkillPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_employeelangskill',
            'retrieve': 'view_employeelangskill',
            'create': 'add_employeelangskill',
            'update': 'change_employeelangskill',
            'partial_update': 'change_employeelangskill',
            'destroy': 'delete_employeelangskill',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class RequestTypePermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access RequestType API.
#     """

#     def has_permission(self, request, view):
#         # Check if the user is authenticated
#         if not request.user.is_authenticated:
#             return False

#         # Grant access if the user is a superuser in the user model
#         if request.user.is_superuser:
#             return True

#         # Grant access if the user has is_ess=True in the user model
#         if hasattr(request.user, 'is_ess') and request.user.is_ess:
#             return True

#         # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         # Check if the user's group has any of the necessary permissions for RequestType
#         required_permissions = ['view_requesttype', 'delete_requesttype', 'add_requesttype', 'change_requesttype']
#         for group in user_permissions.groups.all():  # Access all related groups
#             for permission in group.permissions.all():  # Access permissions of each group
#                 if permission.codename in required_permissions:
#                     return True

#         return False
class RequestTypePermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_requesttype',
            'retrieve': 'view_requesttype',
            'create': 'add_requesttype',
            'update': 'change_requesttype',
            'partial_update': 'change_requesttype',
            'destroy': 'delete_requesttype',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class EmployeePermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access company API.
#     """

#     def has_permission(self, request, view):
#         # Check if the user is authenticated
#         if not request.user.is_authenticated:
#             return False

#         # Grant access if the user is a superuser in the user model
#         if request.user.is_superuser:
#             return True

#         # Grant access if the user has is_ess=True in the user model
#         if hasattr(request.user, 'is_ess') and request.user.is_ess:
#             return True

#         # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         # Check if the user's group has any of the necessary permissions
#         required_permissions = ['view_emp_master', 'delete_emp_master', 'add_emp_master', 'change_emp_master']
#         for group in user_permissions.groups.all():  # Access all related groups
#             for permission in group.permissions.all():  # Access permissions of each group
#                 if permission.codename in required_permissions:
#                     return True
class EmployeePermission(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        if request.user.is_ess:
            return True
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        if user_permissions.is_superuser:
            return True

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_emp_master',
            'retrieve': 'view_emp_master',
            'create': 'add_emp_master',
            'update': 'change_emp_master',
            'partial_update': 'change_emp_master',
            'destroy': 'delete_emp_master',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

# class ApprovalLevelPermission(permissions.BasePermission):
#     """
#     Custom permission to only allow users with specific permissions to access company API.
#     """

#     def has_permission(self, request, view):
#         # Check if the user is authenticated
#         if not request.user.is_authenticated:
#             return False

#         # Grant access if the user is a superuser in the user model
#         if request.user.is_superuser:
#             return True

#         # Grant access if the user has is_ess=True in the user model
#         if hasattr(request.user, 'is_ess') and request.user.is_ess:
#             return True

#         # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
#         try:
#             user_permissions = UserTenantPermissions.objects.get(profile=request.user)
#         except UserTenantPermissions.DoesNotExist:
#             return False

#         # Check if the user's group has any of the necessary permissions
#         required_permissions = ['view_approvallevel', 'delete_approvallevel', 'add_approvallevel', 'change_approvallevel']
#         for group in user_permissions.groups.all():  # Access all related groups
#             for permission in group.permissions.all():  # Access permissions of each group
#                 if permission.codename in required_permissions:
#                     return True
class ApprovalLevelPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_approvallevel',
            'retrieve': 'view_approvallevel',
            'create': 'add_approvallevel',
            'update': 'change_approvallevel',
            'partial_update': 'change_approvallevel',
            'destroy': 'delete_approvallevel',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

class ApprovalPermission(permissions.BasePermission):
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

        # Map view actions to required permissions
        action_permissions = {
            'list': 'view_approval',
            'retrieve': 'view_approval',
            'create': 'add_approval',
            'update': 'change_approval',
            'partial_update': 'change_approval',
            'destroy': 'delete_approval',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

