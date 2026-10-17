"""v1.27.0 – group roles. Group HR head: every company of the group with the rights of the group "HR Manager" there
(kept per company, ZeoCloud CompanyAccess – the person's rights in other accounts do not change). Group viewer: the
group dashboard and group reports only, no company access."""
from django.db import transaction

HR_GROUP = 'HR Manager'


def _tr(t, *a):
    from Translations.i18n import tr
    return tr(t, *a)


def members(group):
    """People who may get a group role: users of at least one company of the group."""
    from UserManagement.models import CustomUser
    ids = [c.id for c in group['companies']]
    return CustomUser.objects.filter(tenants__id__in=ids, is_active=True, is_ess=False).distinct().order_by('email')


def role_rows(group):
    from .models import GroupRole
    qs = GroupRole.objects.select_related('user', 'created_by').filter(account_id=group.get('account_id'))
    return [{'id': r.id, 'user_id': r.user_id, 'email': r.user.email or r.user.username, 'role': r.role,
             'role_label': _tr(dict(GroupRole.ROLES)[r.role]), 'by': getattr(r.created_by, 'email', '') or '',
             'created_at': r.created_at.isoformat()} for r in qs]


def _grant(role, group):
    """Group HR head: access to every company of the group it does not have yet."""
    from ZeoCloud.models import CompanyAccess
    from tenant_users.tenants.models import UserTenantPermissions
    if role.role != 'hr_head':
        return
    user = role.user
    have = set(user.tenants.values_list('id', flat=True))
    granted = list(role.granted or [])
    for c in group['companies']:
        if c.id in have:
            continue
        user.tenants.add(c)
        UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_staff': True, 'is_superuser': False})
        CompanyAccess.objects.update_or_create(user=user, company=c, defaults={'admin': False, 'groups': [HR_GROUP]})
        granted.append(c.id)
    if granted != (role.granted or []):
        role.granted = granted
        role.save(update_fields=['granted'])


def _take_back(role):
    from UserManagement.models import company as Company
    ids = list(role.granted or [])
    if ids:
        role.user.tenants.remove(*Company.objects.filter(id__in=ids))   # their CompanyAccess goes with it (signal)


def set_role(group, by, user_id, role):
    from .models import GroupRole
    if role not in ('hr_head', 'viewer'):
        raise ValueError(_tr('Choose Group HR head or Group viewer.'))
    u = members(group).filter(pk=user_id).first()
    if u is None:
        raise ValueError(_tr('Only users of the group\'s companies can get a group role.'))
    if group.get('account_id'):
        from ZeoCloud.models import Account
        if Account.objects.filter(pk=group['account_id'], owner_id=u.pk).exists():
            raise ValueError(_tr('The owner has every group right already.'))
    with transaction.atomic():
        r, made = GroupRole.objects.get_or_create(account_id=group.get('account_id'), user=u, defaults={'role': role, 'created_by': by})
        if not made and r.role != role:
            if r.role == 'hr_head':
                _take_back(r)
                r.granted = []
            r.role = role
            r.save(update_fields=['role', 'granted'])
        _grant(r, group)
    return r


def remove_role(group, role_id):
    from .models import GroupRole
    r = GroupRole.objects.filter(pk=role_id, account_id=group.get('account_id')).first()
    if r is None:
        raise ValueError(_tr('Not found.'))
    with transaction.atomic():
        _take_back(r)
        r.delete()


def sync(group):
    """A company added to the group later: Group HR heads get it too."""
    from .models import GroupRole
    for r in GroupRole.objects.select_related('user').filter(account_id=group.get('account_id'), role='hr_head'):
        _grant(r, group)
