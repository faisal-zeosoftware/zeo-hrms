"""v1.26.0 – inviting an e-mail that already has a ZEO login.

One login (e-mail / user name) is shared by every company on the server: the same person can work for several
companies – and, in ZEO Cloud, for several customer accounts – with one password, and picks the company on the company
list after sign-in. Before 1.26.0 "Add user" always made a new login and failed with "already exists".

Now, when the e-mail (or user name) belongs to an existing login, the company invites that person instead:
- an invitation per company is stored (ZeoCloud.UserInvite, public schema) and an e-mail is sent;
- the person sees it on their company list and accepts or declines – nothing is shared before they accept;
- on accept the company is added to their login with the rights chosen when inviting (staff / administrator); groups,
  branches and the employee link are set afterwards in Users as for any user;
- in ZEO Cloud the person then counts as a user of the inviting account (the plan's user limit is checked on accept).
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

log = logging.getLogger(__name__)


def existing_login(email, username=''):
    """The login that already uses this e-mail or user name (case does not matter), or None."""
    from .models import CustomUser
    email = (email or '').strip()
    username = (username or '').strip()
    q = Q()
    if email:
        q |= Q(email__iexact=email) | Q(username__iexact=email)
    if username:
        q |= Q(username__iexact=username)
    if not q:
        return None
    return CustomUser.objects.filter(q).order_by('id').first()


def may_manage(user, comp):
    """The inviter must be an administrator of the company (or hold the right to add users there), or a server
    administrator. v1.26.0 review: was "works in the company" – any user of the company could invite."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    try:
        from ZeoCloud.services import is_platform_admin
        if is_platform_admin(user):
            return True
    except Exception:
        pass
    if not user.tenants.filter(pk=comp.pk).exists():
        return False
    from django_tenants.utils import schema_context
    from tenant_users.tenants.models import UserTenantPermissions
    with schema_context(comp.schema_name):   # the loaded record carries this company's rights (ZeoCloud/rights.py)
        utp = UserTenantPermissions.objects.filter(profile=user).first()
        if utp is None:
            return False
        if utp.is_superuser:
            return True
        return any(p.codename == 'add_customuser' for g in utp.groups.all() for p in g.permissions.all())


def may_make_admin(user, comp):
    if user.is_superuser:
        return True
    from django_tenants.utils import schema_context
    from ZeoCloud.rights import is_admin
    with schema_context(comp.schema_name):
        return is_admin(user, comp.schema_name)


def invite_json(inv, for_inviter=False):
    out = {'id': inv.id, 'company': inv.company.name, 'company_id': inv.company_id, 'schema_name': inv.company.schema_name,
           'state': inv.state, 'is_staff': inv.is_staff, 'is_superuser': inv.is_superuser,
           'invited_by': (inv.invited_by.email or inv.invited_by.username) if inv.invited_by else '',
           'created_at': inv.created_at.isoformat(), 'answered_at': inv.answered_at.isoformat() if inv.answered_at else None}
    if for_inviter:
        out.update(email=inv.email or inv.user.email, username=inv.user.username, user_id=inv.user_id)
    try:   # the customer account the company belongs to (ZEO Cloud)
        acc = inv.company.cloud.account
        out['account'] = acc.name
    except Exception:
        out['account'] = ''
    return out


def create(target, companies, by, is_staff=False, is_superuser=False):
    """Invite `target` (an existing login) to each company. Returns (invites, already, refused):
    invites – new or still pending invitations; already – companies the login has already; refused – not allowed."""
    from ZeoCloud.models import UserInvite
    invites, already, refused = [], [], []
    for comp in companies:
        if not may_manage(by, comp) or (is_superuser and not may_make_admin(by, comp)):
            refused.append(comp)
            continue
        if target.tenants.filter(pk=comp.pk).exists():
            already.append(comp)
            continue
        inv = UserInvite.objects.filter(user=target, company=comp, state='pending').first()
        if inv is None:
            inv = UserInvite.objects.create(user=target, company=comp, email=target.email or '', invited_by=by if getattr(by, 'pk', None) else None,
                                            is_staff=bool(is_staff), is_superuser=bool(is_superuser))
            _mail(inv)
        invites.append(inv)
    return invites, already, refused


def _mail(inv):
    to = inv.user.email or (inv.user.username if '@' in (inv.user.username or '') else '')
    if not to:
        return
    try:
        from django.core.mail import send_mail
        who = (inv.invited_by.email or inv.invited_by.username) if inv.invited_by else 'ZEO'
        send_mail(f'Invitation to {inv.company.name} in ZEO',
                  f'{who} invited you to work in {inv.company.name} in ZEO HRMS.\n\n'
                  f'Sign in with your usual ZEO login ({inv.user.username}) – the invitation is at the top of your company list. '
                  f'Press Accept to add {inv.company.name} to your companies, or Decline.\n\n'
                  f'Nothing is shared with {inv.company.name} before you accept.',
                  None, [to], fail_silently=True)
    except Exception:
        log.exception('invite mail')


def accept(inv, user):
    """Add the company to the login with the rights of the invitation. Raises ValueError with a message."""
    from tenant_users.tenants.models import UserTenantPermissions
    if inv.user_id != user.pk:
        raise ValueError('This invitation is for another login.')
    if inv.state != 'pending':
        raise ValueError('This invitation is no longer open.')
    try:
        with transaction.atomic():
            user.tenants.add(inv.company)           # ZEO Cloud: the account's user limit and e-mail domain are checked here
            # v1.26.0 review: the login's rights record is shared by all its companies – it is never changed here (that
            # took away, or gave, administrator rights in the person's own account). An empty one is made only if the
            # login has none; the rights for this company go to CompanyAccess (ZeoCloud/rights.py).
            from ZeoCloud.models import CompanyAccess
            UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_staff': True, 'is_superuser': False})
            CompanyAccess.objects.update_or_create(user=user, company=inv.company,
                                                   defaults={'admin': bool(inv.is_superuser), 'groups': [], 'invite': inv})
            inv.state, inv.answered_at = 'accepted', timezone.now()
            inv.save(update_fields=['state', 'answered_at'])
    except Exception as e:
        detail = getattr(e, 'detail', None)
        if isinstance(detail, dict):
            msg = detail.get('detail')
            msg = msg[0] if isinstance(msg, list) and msg else msg
            raise ValueError(str(msg or e))
        raise ValueError(str(e)[:300])
    try:
        from ZeoCloud.services import forget
        forget(inv.company.schema_name)
        from ZeoCloud import rights
        rights.forget()
    except Exception:
        pass
    return inv


def answer(inv, user, state):
    """decline (the invited person) or cancel (someone of the company)."""
    if inv.state != 'pending':
        raise ValueError('This invitation is no longer open.')
    if state == 'declined' and inv.user_id != user.pk:
        raise ValueError('This invitation is for another login.')
    if state == 'cancelled' and not may_manage(user, inv.company):
        raise ValueError('Only an administrator of the company can cancel its invitations.')
    inv.state, inv.answered_at = state, timezone.now()
    inv.save(update_fields=['state', 'answered_at'])
    return inv
