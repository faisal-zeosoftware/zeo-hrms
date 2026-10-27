"""
v1.54.0 – SCIM 2.0 (RFC 7643 / 7644) for user provisioning from Microsoft Entra ID (Azure AD), Okta, OneLogin, JumpCloud …

    /scim/v2/ServiceProviderConfig, /ResourceTypes, /Schemas
    /scim/v2/Users          GET (filter userName / externalId / emails eq, startIndex, count), POST
    /scim/v2/Users/<id>     GET, PUT, PATCH (active, name, emails, userName, externalId), DELETE (= switch the login off)
    /scim/v2/Groups         GET (filter displayName eq), POST
    /scim/v2/Groups/<id>    GET, PUT, PATCH (members add / remove / replace, displayName), DELETE

Authorization: Bearer <the SCIM token of the sign-in> (Settings → Single sign-on → SCIM). The token is stored hashed. Only users
of the sign-in's domains whose companies are all covered by it are listed or changed. Members of a group get the ZEO permission
group mapped to its name.
"""
import hashlib
import json
import logging
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import HttpResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from . import directory as D
from . import sso as O
from .models import SCIMGroup, SCIMUser, SSOProvider

log = logging.getLogger(__name__)
U = get_user_model()
USER = 'urn:ietf:params:scim:schemas:core:2.0:User'
GROUP = 'urn:ietf:params:scim:schemas:core:2.0:Group'
LIST = 'urn:ietf:params:scim:api:messages:2.0:ListResponse'
ERR = 'urn:ietf:params:scim:api:messages:2.0:Error'
PATCH = 'urn:ietf:params:scim:api:messages:2.0:PatchOp'


def out(data, status=200):
    r = HttpResponse(json.dumps(data, default=str), status=status, content_type='application/scim+json')
    return r


def err(status, detail, scim_type=None):
    d = {'schemas': [ERR], 'status': str(status), 'detail': detail}
    if scim_type:
        d['scimType'] = scim_type
    return out(d, status)


def token_hash(t):
    return hashlib.sha256((t or '').encode()).hexdigest()


def _ts(d):
    return d.isoformat() if d else None


def _bool(v):
    if isinstance(v, str):
        return v.strip().lower() == 'true'
    return bool(v)


@method_decorator(csrf_exempt, name='dispatch')
class Base(View):
    """Bearer-token authentication against the providers' SCIM tokens; JSON in and out."""
    def dispatch(self, request, *a, **k):
        h = request.META.get('HTTP_AUTHORIZATION', '')
        t = h[7:].strip() if h.lower().startswith('bearer ') else ''
        self.p = SSOProvider.objects.filter(scim_token_hash=token_hash(t), enabled=True).first() if t else None
        if self.p is None:
            return err(401, 'A valid SCIM token is required.')
        self.base = O.api_base(request) + '/scim/v2'
        try:
            self.body = json.loads(request.body or b'{}') if request.method in ('POST', 'PUT', 'PATCH') else {}
        except ValueError:
            return err(400, 'The request body is not JSON.', 'invalidSyntax')
        try:
            return super().dispatch(request, *a, **k)
        except O.SSOError as e:
            return err(409 if 'already' in str(e) else 400, str(e))

    # ------------------------------------------------------------ users
    def in_scope(self, u):
        return O.domain_of(u.email) in [d.lower() for d in self.p.domains or []] and (
            O.may_use(self.p, u)[0] or SCIMUser.objects.filter(provider=self.p, user=u).exists()) and not u.is_superuser

    def user_json(self, u):
        link = SCIMUser.objects.filter(provider=self.p, user=u).first()
        first = getattr(u, 'first_name', '') or ''
        last = getattr(u, 'last_name', '') or ''
        d = {'schemas': [USER], 'id': str(u.pk), 'userName': u.email or u.username, 'active': bool(u.is_active),
             'emails': [{'value': u.email, 'type': 'work', 'primary': True}] if u.email else [],
             'name': {'givenName': first, 'familyName': last, 'formatted': f'{first} {last}'.strip()},
             'displayName': (f'{first} {last}'.strip() or u.username),
             'meta': {'resourceType': 'User', 'created': _ts(getattr(u, 'date_joined', None) or (link.created_at if link else None)),
                      'lastModified': _ts(link.updated_at if link else None), 'location': f'{self.base}/Users/{u.pk}'}}
        if link and link.external_id:
            d['externalId'] = link.external_id
        return d

    def get_user(self, pk):
        u = U.objects.filter(pk=pk).first() if str(pk).isdigit() else None
        return u if u is not None and self.in_scope(u) else None

    def groups_of(self, body):
        """Group names in a user body (Entra may send them; usually groups come through /Groups)."""
        return [g.get('display') or g.get('value') for g in body.get('groups') or [] if isinstance(g, dict)] or None


def _email_of(body):
    for e in body.get('emails') or []:
        if isinstance(e, dict) and e.get('value') and (e.get('primary') in (True, 'true', 'True') or e.get('type') == 'work'):
            return e['value']
    es = [e.get('value') for e in body.get('emails') or [] if isinstance(e, dict) and e.get('value')]
    return es[0] if es else body.get('userName')


FILTER_RE = re.compile(r'^\s*([\w.\[\]" ]+?)\s+eq\s+"([^"]*)"\s*$', re.I)


def _page(request, items):
    try:
        start = max(int(request.GET.get('startIndex') or 1), 1)
        count = max(min(int(request.GET.get('count') or 100), 500), 0)
    except ValueError:
        start, count = 1, 100
    sl = items[start - 1:start - 1 + count]
    return {'schemas': [LIST], 'totalResults': len(items), 'startIndex': start, 'itemsPerPage': len(sl), 'Resources': sl}


class ServiceProviderConfigView(Base):
    def get(self, request):
        return out({'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'], 'patch': {'supported': True},
                    'bulk': {'supported': False, 'maxOperations': 0, 'maxPayloadSize': 0}, 'filter': {'supported': True, 'maxResults': 500},
                    'changePassword': {'supported': False}, 'sort': {'supported': False}, 'etag': {'supported': False},
                    'authenticationSchemes': [{'type': 'oauthbearertoken', 'name': 'OAuth Bearer Token', 'description': 'The SCIM token of the ZEO sign-in'}]})


class ResourceTypesView(Base):
    def get(self, request):
        rt = [{'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ResourceType'], 'id': 'User', 'name': 'User', 'endpoint': '/Users', 'schema': USER},
              {'schemas': ['urn:ietf:params:scim:schemas:core:2.0:ResourceType'], 'id': 'Group', 'name': 'Group', 'endpoint': '/Groups', 'schema': GROUP}]
        return out({'schemas': [LIST], 'totalResults': 2, 'startIndex': 1, 'itemsPerPage': 2, 'Resources': rt})


class SchemasView(Base):
    def get(self, request):
        s = [{'id': USER, 'name': 'User'}, {'id': GROUP, 'name': 'Group'}]
        return out({'schemas': [LIST], 'totalResults': 2, 'startIndex': 1, 'itemsPerPage': 2, 'Resources': s})


class UsersView(Base):
    def get(self, request):
        f = request.GET.get('filter') or ''
        qs = U.objects.filter(is_superuser=False).order_by('pk')
        if f:
            m = FILTER_RE.match(f)
            if not m:
                return err(400, 'Only "attribute eq \\"value\\"" filters are supported.', 'invalidFilter')
            attr, val = m.group(1).strip().lower(), m.group(2)
            if attr in ('username', 'emails.value', 'emails[type eq "work"].value'.lower()):
                qs = qs.filter(email__iexact=val)
            elif attr == 'externalid':
                qs = qs.filter(pk__in=SCIMUser.objects.filter(provider=self.p, external_id=val).values('user_id'))
            elif attr == 'id':
                qs = qs.filter(pk=val if val.isdigit() else 0)
            else:
                return err(400, f'Filtering on {attr} is not supported.', 'invalidFilter')
        doms = [d.lower() for d in self.p.domains or []]
        qs = qs.filter(email__iregex=r'@(' + '|'.join(re.escape(d) for d in doms) + r')$') if doms else qs.none()
        items = [self.user_json(u) for u in qs[:5000] if self.in_scope(u)]
        return out(_page(request, items))

    def post(self, request):
        b = self.body
        email = (_email_of(b) or '').strip().lower()
        if not email or '@' not in email:
            return err(400, 'userName or emails must hold the e-mail of the user.', 'invalidValue')
        existing = U.objects.filter(email__iexact=email).first()
        if existing is not None and SCIMUser.objects.filter(provider=self.p, user=existing).exists():
            return err(409, f'{email} already exists.', 'uniqueness')
        name = b.get('name') or {}
        with transaction.atomic():
            u = D.provision(self.p, email, name.get('givenName', ''), name.get('familyName', ''), self.groups_of(b),
                            active=_bool(b.get('active', True)))
            SCIMUser.objects.update_or_create(provider=self.p, user=u, defaults={'external_id': str(b.get('externalId') or '')[:200]})
        log.info('scim: %s provisioned %s', self.p.name, email)
        return out(self.user_json(u), 201)


class UserView(Base):
    def get(self, request, pk):
        u = self.get_user(pk)
        return out(self.user_json(u)) if u else err(404, 'User not found.')

    def put(self, request, pk):
        u = self.get_user(pk)
        if u is None:
            return err(404, 'User not found.')
        b = self.body
        email = (_email_of(b) or u.email).strip().lower()
        self._set_email(u, email)
        if 'active' in b:
            D.set_active(u, _bool(b['active']))
        if b.get('externalId') is not None:
            SCIMUser.objects.update_or_create(provider=self.p, user=u, defaults={'external_id': str(b['externalId'])[:200]})
        if self.groups_of(b) is not None:
            D.apply_groups(self.p, u, self.groups_of(b))
        u.refresh_from_db()
        return out(self.user_json(u))

    def _set_email(self, u, email):
        if email and email != (u.email or '').lower():
            if O.domain_of(email) not in [d.lower() for d in self.p.domains or []]:
                raise O.SSOError(f'{email} is not an e-mail of the domains of "{self.p.name}".')
            if U.objects.filter(email__iexact=email).exclude(pk=u.pk).exists():
                raise O.SSOError(f'{email} already belongs to another login.')
            U.objects.filter(pk=u.pk).update(email=email)

    def patch(self, request, pk):
        u = self.get_user(pk)
        if u is None:
            return err(404, 'User not found.')
        for op in self.body.get('Operations') or []:
            kind = str(op.get('op') or '').lower()
            path = str(op.get('path') or '')
            val = op.get('value')
            if kind not in ('add', 'replace', 'remove'):
                return err(400, f'Unknown operation {op.get("op")}.', 'invalidSyntax')
            items = val.items() if isinstance(val, dict) and not path else [(path, val)]
            for k, v in items:
                kl = k.lower()
                if kl == 'active':
                    D.set_active(u, False if kind == 'remove' else _bool(v))
                elif kl in ('username', 'emails', 'emails[type eq "work"].value'):
                    if kind != 'remove':
                        e = v if isinstance(v, str) else _email_of({'emails': v if isinstance(v, list) else [v]})
                        self._set_email(u, (e or '').strip().lower())
                elif kl == 'externalid':
                    SCIMUser.objects.update_or_create(provider=self.p, user=u, defaults={'external_id': '' if kind == 'remove' else str(v)[:200]})
                elif kl.startswith('name') or kl in ('displayname', 'title', 'preferredlanguage') or kl.startswith('urn:') or kl.startswith('addresses') or kl.startswith('phonenumbers'):
                    pass   # names and other details come from the employee record in ZEO
        u.refresh_from_db()
        return out(self.user_json(u))

    def delete(self, request, pk):
        u = self.get_user(pk)
        if u is None:
            return err(404, 'User not found.')
        D.set_active(u, False)
        SCIMUser.objects.filter(provider=self.p, user=u).delete()
        return HttpResponse(status=204)


# ------------------------------------------------------------------ groups
def group_json(g, base):
    users = {u.pk: u for u in U.objects.filter(pk__in=g.members or [])}
    return {'schemas': [GROUP], 'id': str(g.pk), 'displayName': g.display_name, 'externalId': g.external_id or None,
            'members': [{'value': str(i), 'display': users[i].email, '$ref': f'{base}/Users/{i}'} for i in g.members or [] if i in users],
            'meta': {'resourceType': 'Group', 'created': _ts(g.created_at), 'lastModified': _ts(g.updated_at), 'location': f'{base}/Groups/{g.pk}'}}


class GroupsView(Base):
    def get(self, request):
        qs = SCIMGroup.objects.filter(provider=self.p)
        f = request.GET.get('filter') or ''
        if f:
            m = FILTER_RE.match(f)
            if not m or m.group(1).strip().lower() not in ('displayname', 'externalid', 'id'):
                return err(400, 'Only displayName, externalId or id "eq" filters are supported.', 'invalidFilter')
            a, v = m.group(1).strip().lower(), m.group(2)
            qs = qs.filter(display_name__iexact=v) if a == 'displayname' else qs.filter(external_id=v) if a == 'externalid' else qs.filter(pk=v if v.isdigit() else 0)
        excl = request.GET.get('excludedAttributes') == 'members'
        items = []
        for g in qs:
            d = group_json(g, self.base)
            if excl:
                d.pop('members')
            items.append(d)
        return out(_page(request, items))

    def post(self, request):
        b = self.body
        name = str(b.get('displayName') or '').strip()
        if not name:
            return err(400, 'displayName is required.', 'invalidValue')
        if SCIMGroup.objects.filter(provider=self.p, display_name__iexact=name).exists():
            return err(409, f'The group {name} already exists.', 'uniqueness')
        g = SCIMGroup.objects.create(provider=self.p, display_name=name[:200], external_id=str(b.get('externalId') or '')[:200])
        _set_members(self, g, [m.get('value') for m in b.get('members') or [] if isinstance(m, dict)])
        return out(group_json(g, self.base), 201)


def _refresh_user_groups(p, user_ids):
    """Every member's mapped ZEO groups follow the SCIM groups they are in."""
    for u in U.objects.filter(pk__in=user_ids):
        names = list(SCIMGroup.objects.filter(provider=p, members__contains=[u.pk]).values_list('display_name', flat=True))
        D.apply_groups(p, u, names)


def _set_members(view, g, ids, mode='replace'):
    valid = []
    for i in ids or []:
        u = view.get_user(i)
        if u is not None:
            valid.append(u.pk)
    before = set(g.members or [])
    if mode == 'add':
        new = list(dict.fromkeys(list(g.members or []) + valid))
    elif mode == 'remove':
        new = [x for x in g.members or [] if x not in set(valid)]
    else:
        new = list(dict.fromkeys(valid))
    g.members = new
    g.save(update_fields=['members', 'updated_at'])
    _refresh_user_groups(view.p, before | set(new))


class GroupView(Base):
    def _g(self, pk):
        return SCIMGroup.objects.filter(provider=self.p, pk=pk).first() if str(pk).isdigit() else None

    def get(self, request, pk):
        g = self._g(pk)
        return out(group_json(g, self.base)) if g else err(404, 'Group not found.')

    def put(self, request, pk):
        g = self._g(pk)
        if g is None:
            return err(404, 'Group not found.')
        if self.body.get('displayName'):
            g.display_name = str(self.body['displayName'])[:200]
            g.save(update_fields=['display_name', 'updated_at'])
        _set_members(self, g, [m.get('value') for m in self.body.get('members') or [] if isinstance(m, dict)])
        return out(group_json(g, self.base))

    def patch(self, request, pk):
        g = self._g(pk)
        if g is None:
            return err(404, 'Group not found.')
        for op in self.body.get('Operations') or []:
            kind = str(op.get('op') or '').lower()
            path = str(op.get('path') or '')
            val = op.get('value')
            m = re.match(r'members\[value eq "([^"]+)"\]', path, re.I)
            if m:
                _set_members(self, g, [m.group(1)], 'remove')
            elif path.lower() == 'members' or (not path and isinstance(val, dict) and 'members' in val):
                vals = val if isinstance(val, list) else (val.get('members') if isinstance(val, dict) else [])
                ids = [x.get('value') for x in vals or [] if isinstance(x, dict)]
                _set_members(self, g, ids, {'add': 'add', 'remove': 'remove'}.get(kind, 'replace') if ids or kind != 'remove' else 'replace')
            elif path.lower() == 'displayname' or (not path and isinstance(val, dict) and 'displayName' in val):
                name = val if isinstance(val, str) else val.get('displayName')
                g.display_name = str(name)[:200]
                g.save(update_fields=['display_name', 'updated_at'])
                _refresh_user_groups(self.p, set(g.members or []))
            elif path.lower() == 'externalid':
                g.external_id = str(val or '')[:200]
                g.save(update_fields=['external_id', 'updated_at'])
        return out(group_json(g, self.base))

    def delete(self, request, pk):
        g = self._g(pk)
        if g is None:
            return err(404, 'Group not found.')
        members = set(g.members or [])
        g.delete()
        _refresh_user_groups(self.p, members)
        return HttpResponse(status=204)
