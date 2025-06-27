from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions


class LanguageMasterPermission(permissions.BasePermission):
    """
    Custom permission to allow only users with specific permissions to access the LanguageMaster model.
    """
    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False

        # Check if the user has tenant-specific permissions
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Allow access to superusers
        if user_permissions.is_superuser:
            return True

        # Define the required permissions
        required_permissions = [
            'view_languagemaster',
            'add_languagemaster',
            'change_languagemaster',
            'delete_languagemaster',
        ]

        # Collect the user's group permissions
        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        # Check if the user has any of the required permissions
        return any(permission in user_group_permissions for permission in required_permissions)

class CountryPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access company API.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Grant access if the user is a superuser
        if user_permissions.is_superuser:
            return True

        # Check if the user's group has any of the necessary permissions
        required_permissions = ['view_cntry_mstr', 'delete_cntry_mstr', 'add_cntry_mstr', 'change_cntry_mstr']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class StatePermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access company API.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Grant access if the user is a superuser
        if user_permissions.is_superuser:
            return True

        # Check if the user's group has any of the necessary permissions
        required_permissions = ['view_state_mstr', 'delete_state_mstr', 'add_state_mstr', 'change_state_mstr']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False


class DocTypePermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access company API.
    """

    def has_permission(self, request, view):
        # Check if the user is authenticated
        if not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        # Retrieve UserTenantPermissions efficiently using get (if unique) or filter
        try:
            user_permissions = UserTenantPermissions.objects.get(profile=request.user)
        except UserTenantPermissions.DoesNotExist:
            return False

        # Grant access if the user is a superuser
        if user_permissions.is_superuser:
            return True

        # Check if the user's group has any of the necessary permissions
        required_permissions = ['view_document_type', 'delete_document_type', 'add_document_type', 'change_document_type']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class LanguageSkillPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the LanguageSkill model.
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

        required_permissions = [
            'view_languageskill', 'add_languageskill', 'change_languageskill', 'delete_languageskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


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

        required_permissions = [
            'view_marketingskill', 'add_marketingskill', 'change_marketingskill', 'delete_marketingskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)


class ProgrammingLanguageSkillPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific permissions to access the ProgrammingLanguageSkill model.
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

        required_permissions = [
            'view_programminglanguageskill', 'add_programminglanguageskill', 'change_programminglanguageskill', 'delete_programminglanguageskill'
        ]

        user_group_permissions = [
            p.codename for group in user_permissions.groups.all() for p in group.permissions.all()
        ]

        return any(permission in user_group_permissions for permission in required_permissions)

