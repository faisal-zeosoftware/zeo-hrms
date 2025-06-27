from rest_framework import permissions
# 

from .models import CustomUser
from django.contrib.auth.models import Group
from django.contrib.auth.models import User
from rest_framework.permissions import BasePermission
from tenant_users.tenants.models import UserTenantPermissions
class UserPermission(permissions.BasePermission):

    def has_permission(self, request, view):
        if view.action == 'list':
            return request.user.is_authenticated() and request.user.is_admin
        elif view.action == 'create':
            return True
        elif view.action in ['retrieve', 'update', 'partial_update', 'destroy']:
            return True
        else:
            return False
                                                                                                
    def has_object_permission(self, request, view, obj):
        # Deny actions on objects if the user is not authenticated
        if not request.user.is_authenticated():
            return False

        if view.action == 'retrieve':
            return obj == request.user or request.user.is_admin
        elif view.action in ['update', 'partial_update']:
            return obj == request.user or request.user.is_admin
        elif view.action == 'destroy':
            return request.user.is_admin
        else:
            return False

class IsEssUserOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow is_ess users to view their own data.
    """

    def has_object_permission(self, request, view, obj):
        # Allow read permissions for all users
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed for is_ess users editing their own data
        return obj.is_ess and obj == request.user

# class IsSuperuserOrReadOnly(permissions.BasePermission):
#     """
#     Custom permission to allow superusers to edit, delete any instance,
#     but allow read-only access to other users.
#     """

#     def has_permission(self, request, view):
#         # Allow read-only permissions for non-superusers
#         if request.method in permissions.SAFE_METHODS:
#             return True
        
#         # Allow superusers to perform any action
#         return request.user and request.user.is_superuser
        
#superuser class for permission
#important
from rest_framework.permissions import IsAdminUser
class IsSuperUser(IsAdminUser):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)



class IsOwnerOrReadOnly(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.user == request.user





