from rest_framework.permissions import BasePermission
from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions

class IsOwnerOrReadOnly(BasePermission):
    """
    Allows access only to the owner of the object or for read-only actions.
    """

    def has_object_permission(self, request, view, obj):
        # Allow read-only access for any user.
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return True
        # Otherwise, require ownership of the object.
        return obj.owner == request.user
    
class BranchPermission(permissions.BasePermission):
    """
    Custom permission to only allow users with specific object-level permissions.
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
        required_permissions = ['view_brnch_mstr', 'delete_brnch_mstr', 'add_brnch_mstr', 'change_brnch_mstr']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False



        return False
class DepartmentPermission(permissions.BasePermission):
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
        required_permissions = ['view_dept_master', 'delete_dept_master', 'add_dept_master', 'change_dept_master']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

    


class DesignationPermission(BasePermission):
    """
    Custom permission to only allow users with specific permissions or superuser status to access the company API.
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
        required_permissions = ['view_desgntn_master', 'delete_desgntn_master', 'add_desgntn_master', 'change_desgntn_master']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
    
class CategoryPermission(permissions.BasePermission):
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
        required_permissions = ['view_ctgry_master', 'delete_ctgry_master', 'add_ctgry_master', 'change_ctgry_master']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
class FiscalYearPermission(permissions.BasePermission):
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
        required_permissions = ['view_fiscalyear', 'delete_view_fiscalyear', 'add_view_fiscalyear', 'change_view_fiscalyear']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
    
class DocumentNumberingPermission(permissions.BasePermission):
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
        required_permissions = ['view_document_numbering', 'delete_view_document_numbering', 'add_view_document_numbering', 'change_document_numbering']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class CompanyPolicyPermission(permissions.BasePermission):
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
        required_permissions = ['view_companypolicy', 'delete_view_companypolicy', 'add_view_companypolicy', 'change_companypolicy']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class AssetMasterPermission(permissions.BasePermission):
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
        required_permissions = ['view_assetmaster', 'delete_view_assetmaster', 'add_view_assetmaster', 'change_assetmaster']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class Asset_CustomFieldValuePermission(permissions.BasePermission):
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
        required_permissions = ['view_asset_customfieldvalue', 'delete_view_asset_customfieldvalue', 'add_view_asset_customfieldvalue', 'change_asset_customfieldvalue']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
class AssetTransactionPermission(permissions.BasePermission):
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
        required_permissions = ['view_assettransaction', 'delete_view_assettransaction', 'add_view_assettransaction', 'change_assettransaction']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
