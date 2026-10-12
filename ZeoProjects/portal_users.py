"""Customer portal logins: one ZEO user per contact, member of this company only, with no group rights.
The portal guard (middleware.py) keeps these users out of every API except the portal API."""
import secrets
import string

from django.core.cache import cache
from django.db import connection, transaction

from . import services as S


def _password():
    alphabet = string.ascii_letters + string.digits
    while True:
        p = ''.join(secrets.choice(alphabet) for _ in range(10)) + secrets.choice('!@#$%*')
        if any(c.isdigit() for c in p) and any(c.isupper() for c in p) and any(c.islower() for c in p):
            return p


def cache_key(schema, user_id):
    return f'zp-portal:{schema}:{user_id}'


def invite(contact, schema, reset=False):
    """Create (or reset) the contact's portal login. Returns (username, password)."""
    from UserManagement.models import CustomUser, company
    from tenant_users.tenants.models import UserTenantPermissions
    if not contact.is_active or not contact.customer.is_active:
        raise S.ProjectsError('The contact or customer is switched off.')
    email = (contact.email or '').strip().lower()
    if not email:
        raise S.ProjectsError('The contact needs an e-mail address to get a portal login.', 'email')
    tenant = company.objects.filter(schema_name=schema).first()
    if tenant is None:
        raise S.ProjectsError('Company not found.')
    password = _password()
    with transaction.atomic():
        user = contact.portal_user
        if user is None:
            clash = CustomUser.objects.filter(username__iexact=email).first() or CustomUser.objects.filter(email__iexact=email).first()
            if clash is not None:
                raise S.ProjectsError('A ZEO login with this e-mail already exists (perhaps an employee). Use another e-mail for the contact.', 'email')
            user = CustomUser.objects.create_user(username=email, email=email, password=password, is_ess=False)
            user.must_change_password = False
            user.save()
            user.tenants.add(tenant)
            contact.portal_user = user
            contact.save(update_fields=['portal_user'])
        else:
            if not reset and user.is_active:
                raise S.ProjectsError('The contact already has a portal login. Use "Reset password".')
            user.set_password(password)
            user.is_active = True
            user.save()
            if not user.tenants.filter(pk=tenant.pk).exists():
                user.tenants.add(tenant)
        UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_superuser': False, 'is_staff': False})
    cache.delete(cache_key(schema, user.pk))
    S.send_mail([email], 'Your customer portal login',
                f'Dear {contact.name},\n\nYou can now follow your support tickets, contracts and projects in our customer portal.\n\n'
                f'Address: {S.settings().portal_url or "the portal address given by your account manager"}\n'
                f'Company code: {schema}\nUser name: {user.username}\nPassword: {password}\n\n'
                'Please keep this password safe.\n')
    return user.username, password


def disable(contact):
    u = contact.portal_user
    if u is not None:
        u.is_active = False
        u.save(update_fields=['is_active'])
        cache.delete(cache_key(connection.schema_name, u.pk))
    return contact


def is_portal_user(schema, user_id):
    """Cached: is this user a customer contact of the company?"""
    key = cache_key(schema, user_id)
    v = cache.get(key)
    if v is None:
        from .models import Contact
        v = Contact.objects.filter(portal_user_id=user_id).exists()
        cache.set(key, v, 300)
    return v
