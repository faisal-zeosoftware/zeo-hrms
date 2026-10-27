"""
v1.54.0 – Active Directory / LDAP and user provisioning.

* LDAP sign-in: for an e-mail domain of a provider of kind "ldap", the password typed in ZEO is checked by the domain
  controller (service account search → bind as the user). With "password off" (enforce) only the directory decides; without it
  the ZEO password still works when the domain controller cannot be reached.
* Provisioning, used by SCIM 2.0 (Entra ID, Okta …) and by the nightly LDAP sync:
    - a login is made for a directory user of the provider's domains in the provider's companies (linked to the employee
      with the same e-mail when there is one, as a self-service login; otherwise a staff login with the mapped groups);
    - a disabled directory account switches the ZEO login off and signs it out;
    - directory groups → ZEO permission groups (group_map) in every company of the provider.
* Only logins whose companies are all covered by the provider are ever changed (sso.may_use).
"""
import logging
import secrets
import ssl

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from django.utils import timezone
from django_tenants.utils import schema_context

from . import sso as O
from .models import SSOProvider

log = logging.getLogger(__name__)
U = get_user_model()
DEFAULT_FILTER = '(&(objectClass=user)(|(mail={email})(userPrincipalName={email})))'
SYNC_FILTER = '(&(objectClass=user)(|(mail=*)(userPrincipalName=*)))'
ATTRS = ['mail', 'userPrincipalName', 'givenName', 'sn', 'displayName', 'memberOf', 'userAccountControl', 'employeeID']


class DirectoryError(Exception):
    pass


# ------------------------------------------------------------------ LDAP
def _servers(p):
    from ldap3 import Server, ServerPool, Tls, ROUND_ROBIN
    urls = [u for u in (p.ldap_url or '').split() if u]
    if not urls:
        raise DirectoryError('Enter the address of the domain controller (ldaps://dc1.company.local:636).')
    tls = None
    if any(u.lower().startswith('ldaps://') for u in urls) or p.ldap_start_tls:
        kw = {'validate': ssl.CERT_REQUIRED, 'version': ssl.PROTOCOL_TLS_CLIENT}
        if p.ldap_ca_cert:
            kw['ca_certs_data'] = p.ldap_ca_cert
        tls = Tls(**kw)
    servers = [Server(u, use_ssl=u.lower().startswith('ldaps://'), tls=tls, connect_timeout=6, get_info='NO_INFO') for u in urls]
    return servers[0] if len(servers) == 1 else ServerPool(servers, ROUND_ROBIN, active=1, exhaust=False)


def _bind(p, server, user, password):
    from ldap3 import Connection
    c = Connection(server, user=user, password=password, receive_timeout=10, raise_exceptions=False, auto_bind=False)
    if p.ldap_start_tls:
        c.open()
        c.start_tls()
    ok = c.bind()
    return c, ok


def _service(p):
    from ldap3.core.exceptions import LDAPException
    try:
        server = _servers(p)
        c, ok = _bind(p, server, p.ldap_bind_dn, O.dec(p.ldap_bind_password))
    except LDAPException as e:
        raise DirectoryError(f'The domain controller could not be reached ({str(e)[:120]}).')
    if not ok:
        raise DirectoryError('The service account could not sign in to the directory: ' + str(c.result.get('description') or c.result)[:120])
    return server, c


def _esc(v):
    from ldap3.utils.conv import escape_filter_chars
    return escape_filter_chars(v or '')


def _entry(e):
    a = e['attributes']
    one = lambda k: (a.get(k)[0] if isinstance(a.get(k), list) and a.get(k) else a.get(k)) or ''
    uac = one('userAccountControl')
    try:
        disabled = bool(int(uac) & 2)
    except (TypeError, ValueError):
        disabled = False
    groups = [g for g in (a.get('memberOf') or []) if g]
    return {'dn': e['dn'], 'email': (one('mail') or one('userPrincipalName')).strip().lower(), 'first': one('givenName'), 'last': one('sn'),
            'name': one('displayName'), 'groups': groups, 'disabled': disabled}


def find(p, email):
    """The directory entry of the e-mail (or None)."""
    server, c = _service(p)
    try:
        flt = (p.ldap_user_filter or DEFAULT_FILTER).replace('{email}', _esc(email)).replace('{user}', _esc(email.split('@')[0]))
        c.search(p.ldap_base_dn, flt, attributes=ATTRS, size_limit=2)
        es = [e for e in c.response or [] if e.get('type') == 'searchResEntry']
        return (server, _entry(es[0])) if len(es) == 1 else (server, None)
    finally:
        c.unbind()


def check_password(p, email, password):
    """True / False from the domain controller; raises DirectoryError when it cannot be asked."""
    if not password:
        return False, None
    server, e = find(p, email)
    if e is None or e['disabled']:
        return False, e
    from ldap3.core.exceptions import LDAPException
    try:
        c, ok = _bind(p, server, e['dn'], password)
        c.unbind()
    except LDAPException as ex:
        raise DirectoryError(f'The domain controller could not be reached ({str(ex)[:120]}).')
    return bool(ok), e


def provider_ldap(email):
    p = O.provider_for(email)
    return p if p is not None and p.kind == 'ldap' else None


def password_ok(user, password, login=None):
    """The password check of the sign-in: the domain controller for a directory domain, else ZEO's own password.
    Returns (ok, how) – how is 'directory' or 'password'."""
    p = provider_ldap(user.email) if user is not None and not user.is_superuser else None
    if p is not None and O.may_use(p, user)[0]:
        try:
            ok, e = check_password(p, user.email, password)
            if ok:
                apply_groups(p, user, e['groups'])
                SSOProvider.objects.filter(pk=p.pk).update(last_used=timezone.now(), last_error='')
            return ok, 'directory'
        except DirectoryError as ex:
            O.note_error(p, ex)
            log.warning('ldap sign-in %s: %s', user.pk, ex)
            if p.enforce:
                return False, 'directory'
    return user.check_password(password), 'password'


def first_login(username, password):
    """A directory user with no ZEO login yet (create logins on): checked by the domain controller, then given a login."""
    if '@' not in (username or ''):
        return None
    p = provider_ldap(username)
    if p is None or not p.create_users:
        return None
    try:
        ok, e = check_password(p, username.strip().lower(), password)
    except DirectoryError as ex:
        O.note_error(p, ex)
        return None
    if not ok:
        return None
    return provision(p, e['email'] or username.strip().lower(), first=e['first'], last=e['last'], groups=e['groups'])


# ------------------------------------------------------------------ provisioning
def companies(p):
    from UserManagement.models import company as Company
    return list(Company.objects.filter(schema_name__in=p.schemas or []))


def mapped_groups(p, names):
    """ZEO permission groups of the directory groups (names or DNs; 'CN=HR Team,OU=…' matches "HR Team")."""
    m = {str(k).strip().lower(): v for k, v in (p.group_map or {}).items()}
    out = set()
    for g in names or []:
        g = str(g).strip()
        cn = g.split(',', 1)[0][3:] if g.upper().startswith('CN=') else g
        for key in (g.lower(), cn.lower()):
            if key in m:
                out.add(int(m[key]))
    return out


def apply_groups(p, user, names):
    """The mapped ZEO groups of the user follow the directory: mapped ones the user is no longer in are removed, others kept."""
    if not p.group_map:
        return
    want = mapped_groups(p, names)
    every = {int(v) for v in (p.group_map or {}).values()}
    from tenant_users.permissions.models import UserTenantPermissions
    for comp in companies(p):
        if not user.tenants.filter(pk=comp.pk).exists():
            continue
        with schema_context(comp.schema_name):
            perm, _ = UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_superuser': False, 'is_staff': False})
            have = set(perm.groups.values_list('id', flat=True))
            add = [g for g in Group.objects.filter(pk__in=want - have)]
            if add:
                perm.groups.add(*add)
            drop = (have & every) - want
            if drop:
                perm.groups.remove(*Group.objects.filter(pk__in=drop))


def _employee(p, email):
    """(company, employee) with this company / personal e-mail in a company of the provider."""
    for comp in companies(p):
        with schema_context(comp.schema_name):
            from EmpManagement.models import emp_master
            e = emp_master.objects.filter(Q(emp_company_email__iexact=email) | Q(emp_personal_email__iexact=email)).filter(is_active=True).first()
            if e is not None:
                return comp, e
    return None, None


def provision(p, email, first='', last='', groups=None, active=True, external_id=''):
    """Make (or find) the login of a directory user. Returns the user. Raises sso.SSOError."""
    email = (email or '').strip().lower()
    if O.domain_of(email) not in [d.lower() for d in p.domains or []]:
        raise O.SSOError(f'{email} is not an e-mail of the domains of "{p.name}".')
    user = U.objects.filter(email__iexact=email).order_by('-is_active', 'pk').first()
    if user is not None:
        ok, why = O.may_use(p, user)
        if not ok and user.is_active:
            raise O.SSOError(why)
    else:
        comp, emp = _employee(p, email)
        mapped = mapped_groups(p, groups or [])
        name = (emp.emp_code if emp is not None else email)[:140]
        base, n = name, 2
        while U.objects.filter(username=name).exists():
            name, n = f'{base}.{n}', n + 1
        user = U.objects.create_user(username=name, email=email, password=secrets.token_urlsafe(24), is_ess=emp is not None and not mapped)
        names = {f.name for f in U._meta.get_fields()}
        if 'first_name' in names:
            U.objects.filter(pk=user.pk).update(first_name=(first or '')[:150], last_name=(last or '')[:150])
        from tenant_users.permissions.models import UserTenantPermissions
        targets = [comp] if emp is not None and not mapped else companies(p)
        for c in targets:
            user.tenants.add(c)
            with schema_context(c.schema_name):
                UserTenantPermissions.objects.get_or_create(profile=user, defaults={'is_superuser': False, 'is_staff': False})
        if emp is not None:
            with schema_context(comp.schema_name):
                from EmpManagement.models import emp_master
                emp_master.objects.filter(pk=emp.pk, users__isnull=True).update(users=user)
        log.info('directory: login %s made for %s (%s)', user.username, email, p.name)
    set_active(user, active)
    if groups is not None:
        apply_groups(p, user, groups)
    return user


def set_active(user, active):
    if bool(user.is_active) == bool(active):
        return
    U.objects.filter(pk=user.pk).update(is_active=bool(active))
    user.is_active = bool(active)
    if not active:
        from .models import LoginSession
        from .security import revoke
        revoke(LoginSession.objects.filter(user=user), 'switched off in the directory')


# ------------------------------------------------------------------ nightly LDAP sync
def sync(p, dry=False):
    """New users (create logins on), groups and disabled accounts from the directory. Returns the report."""
    rep = {'at': timezone.now().isoformat(timespec='seconds'), 'seen': 0, 'created': 0, 'updated': 0, 'disabled': 0, 'skipped': 0,
           'missing_off': 0, 'errors': []}
    server, c = _service(p)
    seen = set()
    try:
        flt = SYNC_FILTER
        gen = c.extend.standard.paged_search(p.ldap_base_dn, flt, attributes=ATTRS, paged_size=500, generator=True)
        for raw in gen:
            if raw.get('type') != 'searchResEntry':
                continue
            e = _entry(raw)
            if O.domain_of(e['email']) not in [d.lower() for d in p.domains or []]:
                continue
            rep['seen'] += 1
            seen.add(e['email'])
            user = U.objects.filter(email__iexact=e['email']).first()
            if user is None and (e['disabled'] or not p.create_users):
                rep['skipped'] += 1
                continue
            if user is not None and not O.may_use(p, user)[0] and user.is_active:
                rep['skipped'] += 1
                continue
            if dry:
                continue
            try:
                was = None if user is None else user.is_active
                u = provision(p, e['email'], e['first'], e['last'], e['groups'], active=not e['disabled'])
                if was is None:
                    rep['created'] += 1
                elif was and not u.is_active:
                    rep['disabled'] += 1
                else:
                    rep['updated'] += 1
            except O.SSOError as ex:
                rep['errors'].append(f"{e['email']}: {ex}")
    finally:
        c.unbind()
    if p.ldap_deactivate_missing and not dry and rep['seen']:
        for u in U.objects.filter(is_active=True, is_superuser=False):
            if O.domain_of(u.email) in [d.lower() for d in p.domains or []] and u.email.lower() not in seen and O.may_use(p, u)[0]:
                set_active(u, False)
                rep['missing_off'] += 1
    rep['errors'] = rep['errors'][:50]
    SSOProvider.objects.filter(pk=p.pk).update(last_sync=timezone.now(), sync_report=rep)
    return rep


def nightly():
    out = {}
    for p in SSOProvider.objects.filter(enabled=True, kind='ldap', ldap_sync=True):
        try:
            out[p.pk] = sync(p)
        except DirectoryError as e:
            O.note_error(p, e)
            out[p.pk] = {'error': str(e)}
    return out
