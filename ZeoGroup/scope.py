"""v1.27.0 – which groups a person may open, and as whom.

Group = the companies of one ZEO Cloud account whose plan has group features (Premium; Enterprise accounts too), or on a
server of its own (Enterprise, ZEO_CLOUD off) every company of the server.

Who may open it:
  owner          the account owner (ZEO Cloud) – everything
  admin          a server administrator (own server) – everything
  hr_head        Group HR head (GroupRole) – everything except group roles; HR rights in every company
  viewer         Group viewer (GroupRole) – group dashboard and group reports only
An Advance account's owner sees the group view locked ("Available in Premium").
"""
from django.conf import settings

TEMPLATE_SCHEMAS = {'public', 'zeo_template'}

RIGHTS = {
    'owner':   {'dashboard', 'reports', 'inbox', 'copy', 'transfer', 'roles'},
    'admin':   {'dashboard', 'reports', 'inbox', 'copy', 'transfer', 'roles'},
    'hr_head': {'dashboard', 'reports', 'inbox', 'copy', 'transfer'},
    'viewer':  {'dashboard', 'reports'},
}


class GroupError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def cloud_on():
    return bool(getattr(settings, 'ZEO_CLOUD', False))


def _tr(text, *a):
    from Translations.i18n import tr
    return tr(text, *a)


def _server_companies():
    from UserManagement.models import company as Company
    out = []
    for c in Company.objects.exclude(schema_name__in=TEMPLATE_SCHEMAS).order_by('name'):
        out.append(c)
    return out


def server_group_allowed():
    """Own server: group features come with the Enterprise licence (1.28.0 checks the licence file). Without a licence
    file the server is treated as Enterprise (as before 1.28.0)."""
    try:
        from ZeoLicence.licence import feature_on   # v1.28.0: the licence file lists the features
        return feature_on('group')
    except ImportError:
        return True


def groups_for(user):
    """Every group the user may open: [{key, name, role, features, locked, account_id, companies: [company]}]."""
    out = []
    if user is None or not getattr(user, 'is_authenticated', False):
        return out
    from .models import GroupRole
    if cloud_on():
        from ZeoCloud.models import Account
        from ZeoCloud.services import company_links, is_platform_admin
        roles = {r.account_id: r.role for r in GroupRole.objects.filter(user=user, account__isnull=False)}
        accs = Account.objects.select_related('plan').filter(owner=user) | Account.objects.select_related('plan').filter(pk__in=list(roles))
        for acc in accs.distinct().order_by('name'):
            if getattr(acc, 'state', '') == 'cancelled':
                continue
            role = 'owner' if acc.owner_id == user.pk else roles.get(acc.pk)
            if not role:
                continue
            feat = bool(acc.plan.group_features) if acc.plan_id else False
            comps = [l.company for l in company_links(acc)]
            out.append({'key': f'acc{acc.pk}', 'name': acc.name, 'role': role, 'features': feat, 'account_id': acc.pk,
                        'plan': acc.plan.name if acc.plan_id else '', 'companies': comps,
                        'locked': '' if feat else _tr('Available in Premium')})
        return [g for g in out if g['features'] or g['role'] == 'owner']
    # a server of its own (Enterprise)
    role = 'admin' if user.is_superuser else None
    if role is None:
        r = GroupRole.objects.filter(user=user, account__isnull=True).first()
        role = r.role if r else None
    if role:
        feat = server_group_allowed()
        out.append({'key': 'server', 'name': getattr(settings, 'ZEO_SERVER_NAME', '') or _tr('All companies'), 'role': role,
                    'features': feat, 'account_id': None, 'plan': 'Enterprise', 'companies': _server_companies(),
                    'locked': '' if feat else _tr('Not in this licence')})
    return out


def group_of(user, key, need=None):
    """The group with this key, or GroupError. `need` = a right of RIGHTS (dashboard, reports, inbox, …)."""
    for g in groups_for(user):
        if g['key'] == key:
            if not g['features']:
                raise GroupError(_tr('Group features are part of Premium and Enterprise.'), 403)
            if need and need not in RIGHTS.get(g['role'], set()):
                raise GroupError(_tr('Your group role does not include this.'), 403)
            return g
    raise GroupError(_tr('You have no access to this group.'), 404)


def company_in(group, schema):
    for c in group['companies']:
        if c.schema_name == schema:
            return c
    raise GroupError(_tr('This company is not in the group.'), 404)


def acting_user(group, user):
    """Whose rights the group summaries are read with: the account owner (always administrator of every company), or
    the server administrator. Group viewers see only these summaries, never the companies themselves."""
    if group.get('account_id'):
        from ZeoCloud.models import Account
        acc = Account.objects.select_related('owner').filter(pk=group['account_id']).first()
        if acc and acc.owner_id:
            return acc.owner
    if user.is_superuser:
        return user
    from UserManagement.models import CustomUser
    return CustomUser.objects.filter(is_superuser=True, is_active=True).order_by('id').first() or user


def as_json(g):
    return {k: v for k, v in g.items() if k != 'companies'} | {
        'companies': [{'id': c.id, 'name': c.name, 'schema_name': c.schema_name} for c in g['companies']],
        'rights': sorted(RIGHTS.get(g['role'], set()))}
