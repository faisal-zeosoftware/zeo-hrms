from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions



class LanguageMasterPermission(permissions.BasePermission):
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
            'list': 'view_languagemaster',
            'retrieve': 'view_languagemaster',
            'create': 'add_languagemaster',
            'update': 'change_languagemaster',
            'partial_update': 'change_languagemaster',
            'destroy': 'delete_languagemaster',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

class CountryPermission(permissions.BasePermission):
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
            'list': 'view_cntry_mstr',
            'retrieve': 'view_cntry_mstr',
            'create': 'add_cntry_mstr',
            'update': 'change_cntry_mstr',
            'partial_update': 'change_cntry_mstr',
            'destroy': 'delete_cntry_mstr',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

class StatePermission(permissions.BasePermission):
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
            'list': 'view_state_mstr',
            'retrieve': 'view_state_mstr',
            'create': 'add_state_mstr',
            'update': 'change_state_mstr',
            'partial_update': 'change_state_mstrr',
            'destroy': 'delete_state_mstr',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False

class DocTypePermission(permissions.BasePermission):
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
            'list': 'view_document_type',
            'retrieve': 'view_document_type',
            'create': 'add_document_type',
            'update': 'change_document_type',
            'partial_update': 'change_document_type',
            'destroy': 'delete_document_type',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False


class LanguageSkillPermission(permissions.BasePermission):
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
            'list': 'view_languageskill',
            'retrieve': 'view_languageskill',
            'create': 'add_languageskill',
            'update': 'change_languageskill',
            'partial_update': 'change_languageskill',
            'destroy': 'delete_languageskill',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False
class MarketingSkillPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the MarketingSkill model.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        if user_permissions.is_superuser:
            return True
        action_permissions = {
            'list': 'view_marketingskill',
            'retrieve': 'view_marketingskill',
            'create': 'add_marketingskill',
            'update': 'change_marketingskill',
            'partial_update': 'change_marketingskill',
            'destroy': 'delete_marketingskill',
        }
        # required_permissions = [
        #     'view_marketingskill', 'add_marketingskill', 'change_marketingskill', 'delete_marketingskill'
        # ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in action_permissions)

class ProgrammingLanguageSkillPermission(permissions.BasePermission):
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
            'list': 'view_programminglanguageskill',
            'retrieve': 'view_programminglanguageskill',
            'create': 'add_programminglanguageskill',
            'update': 'change_programminglanguageskill',
            'partial_update': 'change_programminglanguageskill',
            'destroy': 'delete_programminglanguageskill',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False