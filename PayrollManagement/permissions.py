from rest_framework import permissions
from tenant_users.tenants.models import UserTenantPermissions
from rest_framework.permissions import BasePermission


class SalaryComponentPermission(permissions.BasePermission):
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
        required_permissions = ['view_salary_component', 'delete_salary_component', 'add_salary_component', 'change_salary_component']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
    
class EmployeeSalaryStructurePermission(permissions.BasePermission):
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
        required_permissions = ['view_employee_salary_structure', 'delete_employee_salary_structure', 'add_employee_salary_structure', 'change_employee_salary_structure']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class PayrollRunPermission(permissions.BasePermission):
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
        required_permissions = ['view_payroll_run', 'delete_payroll_run', 'add_payroll_run', 'change_payroll_run']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False

class PayslipPermission(permissions.BasePermission):
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
        required_permissions = ['view_payslip', 'delete_payslip', 'add_payslip', 'change_payslip']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
    
class PayslipComponentPermission(permissions.BasePermission):
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
        required_permissions = ['view_payslip_component', 'delete_payslip_component', 'add_payslip_component', 'change_payslip_component']
        for group in user_permissions.groups.all():  # Access all related groups
            for permission in group.permissions.all():  # Access permissions of each group
                if permission.codename in required_permissions:
                    return True

        return False
