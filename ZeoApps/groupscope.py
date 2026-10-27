"""v1.62.0 isolation – permission groups per company.

auth_group, auth_group_permissions and the user rights (permissions_usertenantpermissions) live in the shared public
schema. Before 1.62.0 every company listed the standard role groups ("HR Manager", "Payroll Manager", "Projects – …",
"CRM – …" …) and could change or delete them – for every company on the server – and a group name could exist only once
on the server.

Rules now:

* The standard groups (and any group another company uses) are shared TEMPLATES. Inside a company they are read-only:
  saving one makes a company-owned copy (stored as "<name> [<schema>]", shown as "<name>"), registered in this company's
  ZeoApps CompanyGroup with `template_id`. This company's own users are moved from the template to the copy; for logins
  that are in other companies too (and for invited logins – ZeoCloud CompanyAccess) the template is replaced by the copy
  in memory while they work in this company (ZeoCloud/rights.py), so their rights elsewhere are untouched.
* Deleting a template from a company is refused. Deleting a company copy puts the company back on the template.
* A company-made group only has to be unique in the company: when the name is taken elsewhere it is stored with the
  company suffix and shown with the name the company gave it (CompanyGroup.display_name).
* Every check by group NAME ("HR Manager", "Projects – …") goes through resolve() / label(), which return the company's
  copy for a template name, so name-based roles keep working.
* Platform administrators keep maintaining the templates from the public schema (or with ?scope=template in a company).
"""
import re
import threading
import time

from django.db import connection, transaction
from django.db.models import Count, Q

STANDARD_GROUP_NAMES = ('HR Manager', 'Payroll Manager')
STANDARD_GROUP_PREFIXES = ('Projects – ', 'CRM – ', 'Property – ', 'Maintenance – ', 'HSE – ', 'Asset Management – ',
                           'Accounting – ', 'People – ', 'HR operations – ', 'Business partners – ')
SUFFIX = re.compile(r' \[([a-z0-9_]+)\]$')
SUFFIX_SQL = r' \[[a-z0-9_]+\]$'
TTL = 10.0
_lock = threading.Lock()
_cache = {}          # schema -> (until, {group id: (template id, display name)})


def _tr(t, *a):
    try:
        from Translations.i18n import tr
        return tr(t, *a)
    except Exception:
        return t.format(*a) if a else t


def schema():
    s = getattr(connection, 'schema_name', 'public')
    return s if s and s != 'public' else None


def is_standard_name(name):
    """A shared standard role group (never a company copy, whose name ends with " [<schema>]")."""
    if not name or SUFFIX.search(name):
        return False
    return name in STANDARD_GROUP_NAMES or name.startswith(STANDARD_GROUP_PREFIXES)


def forget():
    _cache.clear()
    try:
        from ZeoCloud import rights
        rights.forget()
    except Exception:
        pass


def rows():
    """{group id: (template id, display name)} of the open company (existing groups only); {} outside a company."""
    sch = schema()
    if not sch:
        return {}
    now = time.time()
    c = _cache.get(sch)
    if c and c[0] > now:
        return c[1]
    with _lock:
        out = {}
        try:
            from django.contrib.auth.models import Group
            from .models import CompanyGroup
            raw = list(CompanyGroup.objects.values_list('group_id', 'template_id', 'display_name'))
            alive = set(Group.objects.filter(pk__in=[r[0] for r in raw]).values_list('pk', flat=True))
            out = {gid: (tid, dn or '') for gid, tid, dn in raw if gid in alive}
        except Exception:
            out = {}
        _cache[sch] = (now + TTL, out)
        return out


def copy_map():
    """{template id: id of this company's copy}."""
    return {tid: gid for gid, (tid, dn) in rows().items() if tid}


def label(g):
    """The name the open company sees for a group."""
    if g is None:
        return ''
    gid, name = (g, '') if isinstance(g, int) else (g.pk, g.name)
    dn = rows().get(gid, (None, ''))[1]
    if dn:
        return dn
    if not name and isinstance(g, int):
        from django.contrib.auth.models import Group
        name = Group.objects.filter(pk=g).values_list('name', flat=True).first() or ''
    return name


def own_members():
    """Logins of the open company that are in no other company (their rights row is used only here)."""
    from UserManagement.models import CustomUser
    members = CustomUser.objects.filter(tenants__schema_name=schema()).values('pk')
    return CustomUser.objects.filter(pk__in=members).annotate(_n=Count('tenants', distinct=True)).filter(_n=1).values('pk')


def visible_ids():
    """Groups the open company works with: the standard templates (unless it has its own copy), its own groups and the
    groups its own users are in – never another company's copies or groups."""
    from django.contrib.auth.models import Group
    mine = set(rows())
    q = Q(name__in=STANDARD_GROUP_NAMES) | Q(pk__in=mine) | Q(user__profile__in=own_members())
    for p in STANDARD_GROUP_PREFIXES:
        q |= Q(name__startswith=p)
    ids = set(Group.objects.filter(q).exclude(Q(name__regex=SUFFIX_SQL) & ~Q(pk__in=mine)).values_list('pk', flat=True))
    return ids - set(copy_map())


def substitute(ids):
    """Group ids with the company's copies in place of the templates they replace."""
    cm = copy_map()
    out = []
    for i in ids:
        j = cm.get(i, i)
        if j not in out:
            out.append(j)
    return out


def resolve(v):
    """The group the open company uses for a name (or id): its own copy of a template, its own group with that
    display name, or the shared group of that name. None when the name is not a group of this company."""
    from django.contrib.auth.models import Group
    if v in (None, ''):
        return None
    g = None
    if isinstance(v, int) or str(v).isdigit():
        g = Group.objects.filter(pk=int(v)).first()
    else:
        key = str(v).strip().lower()
        for gid, (tid, dn) in rows().items():
            if dn and dn.lower() == key:
                g = Group.objects.filter(pk=gid).first()
                if g:
                    return g
        g = Group.objects.filter(name__iexact=str(v).strip()).first()
        if g is not None and schema() and g.pk not in visible_ids() and g.pk not in copy_map():
            return None
    if g is not None:
        c = copy_map().get(g.pk)
        if c:
            g = Group.objects.filter(pk=c).first() or g
    return g


def unique_name(base, sch=None, suffix=None):
    """A server-wide free group name: `base` itself when free (and no suffix asked), else "base [schema]"."""
    from django.contrib.auth.models import Group
    sch = sch or schema() or 'public'
    base = (base or '').strip()[:120]
    if not suffix and not Group.objects.filter(name=base).exists():
        return base
    n = 1
    while True:
        cand = f'{base}{"" if n == 1 else f" ({n})"} [{sch}]'
        if not Group.objects.filter(name=cand).exists():
            return cand
        n += 1


def name_taken(display, exclude_id=None):
    """Another group of the open company already shows this name."""
    from django.contrib.auth.models import Group
    key = (display or '').strip().lower()
    for g in Group.objects.filter(pk__in=visible_ids() | set(rows())):
        if g.pk != exclude_id and label(g).strip().lower() == key:
            return True
    return False


def _registered_elsewhere(gid):
    """The group is listed in another company's CompanyGroup (same database; a cloned company carries the rows)."""
    sch = schema()
    try:
        with connection.cursor() as cur:
            cur.execute("select table_schema from information_schema.tables where table_name='ZeoApps_companygroup' and table_schema<>%s", [sch])
            schemas = [r[0] for r in cur.fetchall()]
            for s in schemas:
                cur.execute(f'select 1 from "{s}"."ZeoApps_companygroup" where group_id=%s limit 1', [gid])
                if cur.fetchone():
                    return True
    except Exception:
        return False
    return False


def shared_reason(g):
    """None when the group belongs to the open company only (edit / delete in place), else why it is shared."""
    from tenant_users.permissions.models import UserTenantPermissions as UTP
    sch = schema()
    if not sch:
        return None
    mine = rows()
    m = SUFFIX.search(g.name or '')
    if m:
        if m.group(1) == sch and g.pk in mine:
            return None
        return 'other'
    if is_standard_name(g.name):
        return 'standard'
    if g.pk not in mine:
        return 'unregistered'
    # a group made here before 1.62.0 (plain name): shared when logins of other companies hold it (platform
    # administrators – superusers – do not count, they get every group)
    outsiders = (UTP.objects.filter(groups=g, is_superuser=False).exclude(profile__is_superuser=True)
                 .exclude(profile__in=own_members()))
    if outsiders.exists():
        return 'used'
    try:
        from ZeoCloud.models import CompanyAccess
        if CompanyAccess.objects.exclude(company__schema_name=sch).filter(groups__contains=[g.name]).exists():
            return 'used'
    except Exception:
        pass
    if _registered_elsewhere(g.pk):
        return 'other'
    return None


def is_platform_admin(user):
    if getattr(user, 'is_superuser', False):
        return True
    try:
        from ZeoCloud.services import is_platform_admin as pa
        return bool(pa(user))
    except Exception:
        return False


def _move(src, dst):
    """This company's own users (and its invited logins) from group `src` to `dst`."""
    from tenant_users.permissions.models import UserTenantPermissions as UTP
    through = UTP.groups.through
    pks = list(UTP.objects.filter(groups=src, profile__in=own_members()).values_list('pk', flat=True))
    if pks:
        through.objects.filter(group_id=src.pk, usertenantpermissions_id__in=pks).delete()
        have = set(through.objects.filter(group_id=dst.pk, usertenantpermissions_id__in=pks).values_list('usertenantpermissions_id', flat=True))
        through.objects.bulk_create([through(group_id=dst.pk, usertenantpermissions_id=p) for p in pks if p not in have])
    try:
        from ZeoCloud.models import CompanyAccess
        for ca in CompanyAccess.objects.filter(company__schema_name=schema(), groups__contains=[src.name]):
            ca.groups = list(dict.fromkeys(dst.name if n == src.name else n for n in ca.groups or []))
            ca.save(update_fields=['groups'])
    except Exception:
        pass
    return len(pks)


def customise(g, permissions=None, display=None):
    """Make (or update) this company's own copy of a shared group. Returns (copy, users moved)."""
    from django.contrib.auth.models import Group
    from .models import CompanyGroup
    mine = rows()
    cm = copy_map()
    if g.pk in cm:                           # a copy exists already: the change goes to it
        c = Group.objects.get(pk=cm[g.pk])
        if permissions is not None:
            c.permissions.set(permissions)
        if display:
            CompanyGroup.objects.filter(group_id=c.pk).update(display_name=display.strip())
        forget()
        return c, 0
    tid = (mine.get(g.pk) or (None, ''))[0] or g.pk      # the copy of a copy (a cloned company) keeps the original template
    base = (display or label(g)).strip()
    with transaction.atomic():
        c = Group.objects.create(name=unique_name(base, suffix=True))
        c.permissions.set(list(permissions) if permissions is not None else list(g.permissions.all()))
        CompanyGroup.objects.filter(group_id=g.pk).delete()
        CompanyGroup.objects.update_or_create(group_id=c.pk, defaults={'template_id': tid, 'display_name': base})
        n = _move(g, c)
    forget()
    return c, n


def reset(c):
    """Delete this company's copy and put its users back on the template."""
    from django.contrib.auth.models import Group
    from .models import CompanyGroup
    tid = (rows().get(c.pk) or (None, ''))[0]
    t = Group.objects.filter(pk=tid).first() if tid else None
    with transaction.atomic():
        n = _move(c, t) if t is not None else 0
        CompanyGroup.objects.filter(group_id=c.pk).delete()
        c.delete()
    forget()
    return t, n


def refuse_text(reason):
    if reason == 'standard':
        return _tr('This is a standard permission group shared by every company on the server. It cannot be deleted from a company – take it away from your users instead, or edit it to make your own copy.')
    return _tr('This permission group is shared with other companies. It cannot be deleted from this company – take it away from your users instead.')
