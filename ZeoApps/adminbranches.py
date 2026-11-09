"""v1.67.6 (setup) item 12 – company administrators get every branch, also the branches added later.

Before: lists built on Core.mixins.BranchAccessMixin (Document numbering, many HR registers …) and the branch list of the
top bar showed a company administrator only the branches mapped to the login in Settings › Branch permissions – without a
mapping an administrator saw nothing, and every new branch had to be mapped by hand.

Now one rule, `every_branch(user)`: a system administrator, a company administrator of the open company, or a member of the
company's permission group "Company administrators" (made or reused by `ensure_group`, flagged "every branch" in
ZeoApps.CompanyOption 'all_branch_groups') works in every branch, whatever is mapped. The group holds every right and is
linked to the company's administrators by default (company creation, administrator rights given, the Permission groups
screen); adding another user to it gives that user every branch too. Nothing has to be mapped when a branch is added.
"""
import logging

from django.db import connection

log = logging.getLogger(__name__)
GROUP = 'Company administrators'
OPTION = 'all_branch_groups'


def _option_ids():
    try:
        from .models import CompanyOption
        o = CompanyOption.objects.filter(key=OPTION).first()
        return [int(x) for x in ((o.value or {}).get('ids') or [])] if o else []
    except Exception:
        return []


def every_branch(user):
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    sch = getattr(connection, 'schema_name', 'public')
    if sch in ('', 'public'):
        return False
    try:
        from ZeoCloud.rights import is_admin
        if is_admin(user):
            return True
    except Exception:
        pass
    ids = _option_ids()
    if not ids:
        return False
    from tenant_users.permissions.models import UserTenantPermissions as UTP
    return UTP.objects.filter(profile=user, groups__id__in=ids).exists()


def company_admins():
    # v1.67.7 (admin): the administrators of THIS company (ZeoCloud/companyadmin.py) – was the shared flag (every company)
    from ZeoCloud.companyadmin import admin_users
    return admin_users(connection.schema_name)


def ensure_group(link_admins=True):
    """Make (or reuse) this company's "Company administrators" group with every right, flag it "every branch" and link the
    company administrators to it. Returns (group, made, linked)."""
    from django.contrib.auth.models import Group, Permission
    from . import groupscope as GS
    from .models import CompanyGroup, CompanyOption
    made = False
    g = None
    for gid in _option_ids():
        g = Group.objects.filter(pk=gid).first()
        if g is not None:
            break
    if g is None:
        g = next((x for x in Group.objects.filter(pk__in=GS.visible_ids()) if GS.label(x) == GROUP), None)
    if g is None:
        name = GS.unique_name(GROUP)
        g = Group.objects.create(name=name)
        CompanyGroup.objects.update_or_create(group_id=g.pk, defaults={'display_name': GROUP if name != GROUP else ''})
        made = True
    if made:   # a reused group keeps the rights the company gave it
        g.permissions.set(Permission.objects.all())
    CompanyOption.objects.update_or_create(key=OPTION, defaults={'value': {'ids': [g.pk]}})
    linked = 0
    if link_admins:
        from tenant_users.permissions.models import UserTenantPermissions as UTP
        for u in company_admins():
            utp = UTP.objects.filter(profile=u).first()
            if utp is not None and not utp.groups.filter(pk=g.pk).exists():
                utp.groups.add(g)
                linked += 1
    try:
        GS.forget()
    except Exception:
        pass
    return g, made, linked
