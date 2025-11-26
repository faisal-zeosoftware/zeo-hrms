from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions
class IsSuperUserOrHasGeneralRequestPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        # Allow superusers full access
        if request.user.is_superuser:
            return True
        if request.user.is_ess:
            return True
        # Non-superusers: Check specific permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Define required permissions
        required_permissions = [
            'view_generalrequest',
            'delete_generalrequest',
            'add_generalrequest',
            'change_generalrequest'
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



class NotificationPermission(permissions.BasePermission):
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


class RequestTypePermission(permissions.BasePermission):
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
class EmployeeResignationPermission(permissions.BasePermission):
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
            'list': 'view_employeeresignation',
            'retrieve': 'view_employeeresignation',
            'create': 'add_employeeresignation',
            'update': 'change_employeeresignation',
            'partial_update': 'change_employeeresignation',
            'destroy': 'delete_employeeresignation',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

class CanViewApprovedResignations(permissions.BasePermission):
    def has_permission(self, request, view):
        # logic to check “view_approved_resignations” permission
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_perms = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False
        for g in user_perms.groups.all():
            if g.permissions.filter(codename='view_approved_resignations').exists():
                return True
        return False
class CanCreateEOS(permissions.BasePermission):
    def has_permission(self, request, view):
        # logic to check “add_create_eos_for_resignation” permission
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        try:
            user_perms = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False
        for g in user_perms.groups.all():
            if g.permissions.filter(codename='add_create_eos_for_resignation').exists():
                return True
        return False
