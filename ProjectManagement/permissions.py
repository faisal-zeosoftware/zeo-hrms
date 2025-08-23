from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions

class ProjectStagePermission(permissions.BasePermission):
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
            'list': 'view_projectstage',
            'retrieve': 'view_projectstage',
            'create': 'add_projectstage',
            'update': 'change_projectstage',
            'partial_update': 'change_projectstage',
            'destroy': 'delete_projectstage',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False
class ProjectPermission(permissions.BasePermission):
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
            'list': 'view_project',
            'retrieve': 'view_project',
            'create': 'add_project',
            'update': 'change_project',
            'partial_update': 'change_project',
            'destroy': 'delete_project',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False
class TaskPermission(permissions.BasePermission):
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
            'list': 'view_task',
            'retrieve': 'view_task',
            'create': 'add_task',
            'update': 'change_task',
            'partial_update': 'change_task',
            'destroy': 'delete_task',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False
class TimeSheetPermission(permissions.BasePermission):
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
            'list': 'view_tasktimesheet',
            'retrieve': 'view_tasktimesheet',
            'create': 'add_tasktimesheet',
            'update': 'change_tasktimesheet',
            'partial_update': 'change_tasktimesheet',
            'destroy': 'delete_tasktimesheet',
        }

        required_perm = action_permissions.get(view.action)

        if not required_perm:
            return False

        # Check if any group contains the required permission
        for group in user_permissions.groups.all():
            if group.permissions.filter(codename=required_perm).exists():
                return True

        return False