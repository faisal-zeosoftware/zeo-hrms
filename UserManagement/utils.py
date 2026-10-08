from rest_framework_simplejwt.tokens import RefreshToken

def get_tokens_for_user(user, tenant_schema):
    refresh = RefreshToken.for_user(user)
    refresh['tenant'] = tenant_schema

    return {
        'refresh': str(refresh),
        'access': str(refresh.access_token),
    }

import random
from django.utils import timezone
from datetime import timedelta

def generate_otp():
    return str(random.randint(100000, 999999))

def is_otp_valid(user):
    if not user.otp_created_at:
        return False
    return timezone.now() <= user.otp_created_at + timedelta(minutes=5)

from django.contrib.auth.models import Group, Permission
from django_tenants.utils import schema_context
from tenant_users.tenants.models import UserTenantPermissions


def grant_admin_access(user, tenant):
    """Make `user` a full admin of one company (tenant schema)."""
    with schema_context(tenant.schema_name):
        perm, _ = UserTenantPermissions.objects.get_or_create(profile=user)
        perm.is_superuser = True
        perm.is_staff = True
        perm.save()
        perm.groups.set(Group.objects.all())
        perm.user_permissions.set(Permission.objects.all())