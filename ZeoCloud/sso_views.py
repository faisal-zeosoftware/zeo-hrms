"""v1.54.0 – single sign-on API (/users/sso/…). The flow and the safety rules are in sso.py."""
import logging

from django.http import HttpResponse, HttpResponseRedirect
from django_tenants.utils import schema_context
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import sso as O
from .models import SSOProvider
from .security_views import login_payload

log = logging.getLogger(__name__)


def bad(msg, code=400):
    return Response({'detail': tr(msg)}, status=code)


class Open(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []


# ------------------------------------------------------------------ sign-in (no login yet)
class DiscoverView(Open):
    """GET ?email= → the sign-in of the e-mail's domain, and whether the password still works."""
    def get(self, request):
        email = (request.query_params.get('email') or '').strip()
        p = O.provider_for(email)
        if p is None:
            return Response({'sso': False})
        if p.kind == 'ldap':   # Active Directory: the password is typed here and checked by the domain controller
            return Response({'sso': False, 'directory': {'name': p.name}, 'password': True})
        return Response({'sso': True, 'provider': {'id': p.pk, 'name': p.name, 'kind': p.kind}, 'password': not p.enforce})


class StartView(Open):
    """GET ?app=<origin>&back=<path>&email= → {url} of the provider's sign-in page."""
    def get(self, request, pk):
        p = SSOProvider.objects.filter(pk=pk, enabled=True).first()
        if p is None:
            return bad('This sign-in is switched off.', 404)
        email = (request.query_params.get('email') or '').strip()
        device = request.query_params.get('device') in ('1', 'true')
        if device:   # the phone app: the sign-in happens in the phone's browser, the app asks for the result
            app, back = O.api_base(request), O.DEVICE
        else:
            app = O.allowed_app(request.query_params.get('app'), request)
            if app is None:
                return bad('This address may not use the sign-in. Ask the administrator to add it to ZEO_APP_URL.', 400)
            back = request.query_params.get('back') or '/login'
        try:
            url = O.saml_start(p, request, app, back, email) if p.kind == 'saml' else O.oidc_start(p, request, app, back, email)
        except O.SSOError as e:
            O.note_error(p, e)
            return bad(str(e), 502)
        out = {'url': url}
        if device:
            out.update(O.device_new(url))
        return Response(out)


def _finish(p, email, st, request=None):
    """Redirect to the app with a ticket, or with the reason (the phone app: a page that hands the sign-in to the app)."""
    try:
        user = O.user_for(p, email)
    except O.SSOError as e:
        log.info('sso refused %s via %s: %s', email, p.pk, e)
        return _fail(st, e)
    t = O.ticket(user, p)
    if st.get('back') == O.DEVICE:
        return _device_confirm(t, st, request)
    return HttpResponseRedirect(O.back_to(st['app'], st.get('back'), sso=t))


def _fail(st, msg):
    if st.get('back') == O.DEVICE:
        return _page(tr('Not signed in'), str(msg))
    return HttpResponseRedirect(O.back_to(st['app'], st.get('back'), sso_error=str(msg)))


def _page(title, text, body=''):
    from django.utils.html import escape
    html = (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)}</title>'
            '<style>body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#f4f5fa;color:#1d2033;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}'
            'main{background:#fff;border:1px solid #e3e5ef;border-radius:14px;padding:26px 24px;max-width:380px;margin:16px;text-align:center}'
            'h1{font-size:20px;margin:0 0 10px}p{color:#4a4f63;line-height:1.5}.code{font-size:30px;letter-spacing:6px;font-weight:700;color:#5b2087;margin:12px 0}'
            'button{height:46px;border:0;border-radius:8px;background:#5b2087;color:#fff;font-size:15px;padding:0 20px;cursor:pointer;width:100%}</style></head>'
            f'<body><main><h1>{escape(title)}</h1><p>{escape(text)}</p>{body}</main></body></html>')
    return HttpResponse(html, content_type='text/html; charset=utf-8')


def _device_confirm(ticket, st, request):
    """The phone's browser: the person checks that the code matches the one in the ZEO app, then the app gets the sign-in."""
    from django.core import signing
    from django.utils.html import escape
    d = O.device_get(st.get('device_url'))
    if d is None:
        return _page(tr('Not signed in'), tr('The sign-in took too long or was already used. Start again in the ZEO app.'))
    token = signing.dumps({'t': ticket, 'poll': d['poll']}, salt='zeo-sso-device')
    form = (f'<div class="code">{escape(d["code"])}</div><form method="post" action="{escape(O.api_base(request))}/users/sso/device/confirm/">'
            f'<input type="hidden" name="token" value="{escape(token)}"><button type="submit">{escape(tr("Yes – sign in the ZEO app"))}</button></form>')
    return _page(tr('Sign in the ZEO app'), tr('Does the ZEO app on your phone show this code? Continue only if you started the sign-in yourself.'), form)


class DeviceConfirmView(Open):
    def post(self, request):
        from django.core import signing
        try:
            d = signing.loads(request.data.get('token') or '', salt='zeo-sso-device', max_age=300)
        except signing.BadSignature:
            return _page(tr('Not signed in'), tr('The sign-in took too long or was already used. Start again in the ZEO app.'))
        O.device_done(d['poll'], d['t'])
        return _page(tr('Signed in'), tr('Go back to the ZEO app – it opens by itself in a moment.'))


class DevicePollView(Open):
    """GET → 200 {ticket} once the person confirmed in the browser, 202 while waiting, 404 when expired."""
    def get(self, request, poll):
        st, t = O.device_poll(poll)
        if st is None:
            return bad('The sign-in took too long. Start again.', 404)
        if not t:
            return Response({'waiting': True}, status=202)
        return Response({'ticket': t})


class OidcCallbackView(Open):
    def get(self, request):
        q = request.query_params
        st_peek = None
        from django.core.cache import cache
        st_peek = cache.get('zeo-sso-state:' + (q.get('state') or ''))
        if q.get('error'):
            if st_peek:
                cache.delete('zeo-sso-state:' + q.get('state'))
                return _fail(st_peek, q.get('error_description') or q.get('error'))
            return HttpResponse(tr('The sign-in was cancelled.'), status=400, content_type='text/plain; charset=utf-8')
        try:
            p, email, st = O.oidc_finish(q.get('state'), q.get('code'))
        except O.SSOError as e:
            if st_peek:
                O.note_error(SSOProvider.objects.filter(pk=st_peek['p']).first(), e)
                return _fail(st_peek, e)
            return HttpResponse(str(e), status=400, content_type='text/plain; charset=utf-8')
        return _finish(p, email, st, request)


class SamlAcsView(Open):
    def post(self, request, pk):
        from django.core.cache import cache
        p = SSOProvider.objects.filter(pk=pk, enabled=True).first()
        relay = request.data.get('RelayState') or ''
        st_peek = cache.get('zeo-sso-saml:' + relay)
        if p is None:
            return HttpResponse(tr('This sign-in is switched off.'), status=404, content_type='text/plain; charset=utf-8')
        try:
            email, st = O.saml_finish(p, request.data.get('SAMLResponse'), relay)
        except O.SSOError as e:
            O.note_error(p, e)
            if st_peek:
                return _fail(st_peek, e)
            return HttpResponse(str(e), status=400, content_type='text/plain; charset=utf-8')
        return _finish(p, email, st, request)


class SamlMetadataView(Open):
    def get(self, request, pk):
        p = SSOProvider.objects.filter(pk=pk, kind='saml').first()
        if p is None:
            return HttpResponse(status=404)
        return HttpResponse(O.sp_metadata(request, p), content_type='application/samlmetadata+xml')


class ExchangeView(Open):
    """POST {ticket} → the usual sign-in answer."""
    def post(self, request):
        user, p = O.redeem(request.data.get('ticket'))
        if user is None:
            return bad('The sign-in took too long or was already used. Start again.', 401)
        ok, why = O.may_use(p, user) if p else (False, 'This sign-in is switched off.')
        if not ok:
            return bad(why, 403)
        d = login_payload(user, request, 'sso')
        d['setup_2fa'] = False          # the identity provider asks for its own second step
        d['sso'] = p.name
        return Response(d)


# ------------------------------------------------------------------ administration
def _company_groups(schemas):
    """v1.62.0 isolation: {group id: name} of the permission groups of these companies (every group of the server was offered
    and accepted – a directory group could be mapped to another company's group)."""
    from django.contrib.auth.models import Group
    from ZeoApps import groupscope
    out = {}
    for s in schemas:
        try:
            with schema_context(s):
                for g in Group.objects.filter(pk__in=groupscope.visible_ids() | set(groupscope.rows())):
                    out.setdefault(g.pk, groupscope.label(g))
        except Exception:
            continue
    return out


def _admin_schemas(request):
    """Companies of which the user is an administrator."""
    from AccessControl.access import ctx
    out = []
    for t in request.user.tenants.all():
        try:
            with schema_context(t.schema_name):
                request._zeo_ctx = None
                c = ctx(request)
                if c and c.admin:
                    out.append({'schema': t.schema_name, 'name': t.name})
        except Exception:
            continue
    request._zeo_ctx = None
    return out


def provider_json(p, request, can_edit):
    return {'id': p.pk, 'name': p.name, 'kind': p.kind, 'kind_label': tr(p.get_kind_display()), 'enabled': p.enabled, 'domains': p.domains,
            'schemas': p.schemas, 'tenant': p.tenant, 'discovery_url': p.discovery_url, 'client_id': p.client_id,
            'secret_set': bool(p.client_secret), 'idp_entity_id': p.idp_entity_id, 'idp_sso_url': p.idp_sso_url,
            'cert_set': bool(p.idp_cert), 'email_attribute': p.email_attribute, 'enforce': p.enforce, 'create_users': p.create_users,
            'last_used': p.last_used, 'last_error': p.last_error, 'can_edit': can_edit,
            'redirect_uri': O.redirect_uri(request), 'acs_url': O.acs_url(request, p), 'entity_id': O.sp_entity_id(request, p),
            'metadata_url': O.sp_entity_id(request, p),
            # v1.54.0 Active Directory / LDAP and provisioning
            'ldap_url': p.ldap_url, 'ldap_start_tls': p.ldap_start_tls, 'ca_set': bool(p.ldap_ca_cert), 'ldap_bind_dn': p.ldap_bind_dn,
            'bind_password_set': bool(p.ldap_bind_password), 'ldap_base_dn': p.ldap_base_dn, 'ldap_user_filter': p.ldap_user_filter,
            'ldap_sync': p.ldap_sync, 'ldap_deactivate_missing': p.ldap_deactivate_missing, 'last_sync': p.last_sync, 'sync_report': p.sync_report,
            'group_map': p.group_map or {}, 'scim_on': bool(p.scim_token_hash), 'scim_token_hint': p.scim_token_hint,
            'scim_url': O.api_base(request) + '/scim/v2', 'scim_users': p.scim_users.count(), 'scim_groups': p.scim_groups.count()}


class ProvidersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        mine = _admin_schemas(request)
        if not mine:
            return bad('Only company administrators set up single sign-on.', 403)
        ms = {x['schema'] for x in mine}
        rows = [provider_json(p, request, set(p.schemas or []) <= ms) for p in SSOProvider.objects.all() if set(p.schemas or []) & ms]
        from django.contrib.auth.models import Group
        return Response({'providers': rows, 'companies': mine, 'redirect_uri': O.redirect_uri(request), 'kinds': [
            {'key': k, 'label': tr(v)} for k, v in SSOProvider.KINDS], 'groups': sorted(({'id': i, 'name': n} for i, n in _company_groups(ms).items()), key=lambda r: r['name'].lower())})   # v1.62.0 isolation

    def post(self, request):
        mine = {x['schema'] for x in _admin_schemas(request)}
        if not mine:
            return bad('Only company administrators set up single sign-on.', 403)
        d = request.data
        p = SSOProvider.objects.filter(pk=d.get('id')).first() if d.get('id') else SSOProvider(created_by=request.user)
        if d.get('id') and p is None:
            return bad('Not found.', 404)
        if p.pk and not set(p.schemas or []) <= mine:
            return bad('Only an administrator of every company of this sign-in can change it.', 403)
        kind = d.get('kind') or p.kind
        if kind not in dict(SSOProvider.KINDS):
            return bad('Choose the kind of sign-in.')
        schemas = [s for s in (d.get('schemas') if isinstance(d.get('schemas'), list) else p.schemas or []) if s]
        if not schemas:
            return bad('Choose the companies whose users sign in with it.')
        if not set(schemas) <= mine:
            return bad('You can only choose companies of which you are an administrator.', 403)
        domains, err = O.clean_domains(d.get('domains') if 'domains' in d else p.domains, p)
        if err:
            return Response({'detail': tr(err)}, status=400)
        p.kind, p.schemas, p.domains = kind, schemas, domains
        p.name = (d.get('name') or p.name or {'microsoft': 'Sign in with Microsoft', 'google': 'Sign in with Google'}.get(kind, 'Company sign-in')).strip()[:80]
        for f in ('tenant', 'discovery_url', 'client_id', 'idp_entity_id', 'idp_sso_url', 'email_attribute', 'ldap_url', 'ldap_bind_dn',
                  'ldap_base_dn', 'ldap_user_filter'):
            if f in d:
                setattr(p, f, str(d.get(f) or '').strip()[:500])
        for f in ('enabled', 'enforce', 'create_users', 'ldap_start_tls', 'ldap_sync', 'ldap_deactivate_missing'):
            if f in d:
                setattr(p, f, bool(d.get(f)))
        if d.get('ldap_bind_password'):
            p.ldap_bind_password = O.enc(str(d['ldap_bind_password']))
        if d.get('ldap_ca_cert'):
            if not O.cert_ok(d['ldap_ca_cert']):
                return bad('The CA certificate could not be read. Paste it as PEM.')
            p.ldap_ca_cert = O.pem(d['ldap_ca_cert'])
        if isinstance(d.get('group_map'), dict):
            from django.contrib.auth.models import Group
            gm = {}
            allowed = set(_company_groups(mine))   # v1.62.0 isolation: groups of the administrator's companies only
            for k, v in d['group_map'].items():
                if str(k).strip() and str(v).isdigit() and int(v) in allowed and Group.objects.filter(pk=int(v)).exists():
                    gm[str(k).strip()[:200]] = int(v)
            p.group_map = gm
        if d.get('client_secret'):
            p.client_secret = O.enc(str(d['client_secret']).strip())
        if d.get('idp_cert'):
            if not O.cert_ok(d['idp_cert']):
                return bad('The certificate could not be read. Paste the certificate (PEM or Base64) of the identity provider.')
            p.idp_cert = O.pem(d['idp_cert'])
        if kind in ('microsoft', 'google', 'oidc'):
            if not p.client_id:
                return bad('Enter the application (client) ID.')
            if not p.client_secret:
                return bad('Enter the client secret.')
            if kind == 'microsoft' and not p.tenant:
                return bad('Enter the directory (tenant) ID of your Microsoft Entra ID.')
            if kind == 'oidc' and not p.discovery_url:
                return bad('Enter the discovery address of the provider (…/.well-known/openid-configuration).')
        elif kind == 'ldap':
            import re as _re
            if not p.ldap_url or not all(_re.match(r'^ldaps?://[^\s/]+(:\d+)?/?$', u, _re.I) for u in p.ldap_url.split()):
                return bad('Enter the address of the domain controller (ldaps://dc1.company.local:636).')
            if not p.ldap_base_dn:
                return bad('Enter the base DN of the users (DC=company,DC=local).')
            if not (p.ldap_bind_dn and p.ldap_bind_password):
                return bad('Enter the service account and its password (it only reads the directory).')
        else:
            if not p.idp_sso_url:
                return bad('Enter the sign-in (SSO) address of the identity provider.')
            if not p.idp_cert:
                return bad('Paste the signing certificate of the identity provider.')
        p.save()
        return Response(provider_json(p, request, True), status=201)


class ProviderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        """{action: test | sync | scim_token | scim_off}."""
        import hashlib
        import secrets
        mine = {x['schema'] for x in _admin_schemas(request)}
        p = SSOProvider.objects.filter(pk=pk).first()
        if p is None or not mine or not set(p.schemas or []) <= mine:
            return bad('Not found.', 404)
        act = request.data.get('action')
        if act == 'scim_token':
            t = 'zeo_scim_' + secrets.token_urlsafe(36)
            p.scim_token_hash, p.scim_token_hint = hashlib.sha256(t.encode()).hexdigest(), t[-6:]
            p.save(update_fields=['scim_token_hash', 'scim_token_hint', 'updated_at'])
            return Response({'token': t, 'scim_url': O.api_base(request) + '/scim/v2', 'note': tr('Copy the token now – it is not shown again.')})
        if act == 'scim_off':
            p.scim_token_hash, p.scim_token_hint = '', ''
            p.save(update_fields=['scim_token_hash', 'scim_token_hint', 'updated_at'])
            return Response({'ok': True})
        if act == 'test':
            if p.kind == 'ldap':
                from . import directory as D
                try:
                    server, c = D._service(p)
                    c.search(p.ldap_base_dn, D.SYNC_FILTER, attributes=['mail'], size_limit=5)
                    n = len([e for e in c.response or [] if e.get('type') == 'searchResEntry'])
                    c.unbind()
                except D.DirectoryError as e:
                    O.note_error(p, e)
                    return bad(str(e), 502)
                email = (request.data.get('email') or '').strip()
                out = {'ok': True, 'message': tr('The service account signed in to the directory and found users.') if n else
                       tr('The service account signed in, but no users were found under the base DN.')}
                if email:
                    try:
                        _s, e = D.find(p, email)
                    except D.DirectoryError as ex:
                        return bad(str(ex), 502)
                    out['user'] = None if e is None else {'email': e['email'], 'disabled': e['disabled'], 'groups': [g.split(',', 1)[0][3:] for g in e['groups']],
                                                          'zeo_groups': sorted(D.mapped_groups(p, e['groups']))}
                return Response(out)
            try:
                O.oidc_config(p) if p.kind != 'saml' else None
            except O.SSOError as e:
                return bad(str(e), 502)
            return Response({'ok': True, 'message': tr('The provider answered.')})
        if act == 'sync':
            if p.kind != 'ldap':
                return bad('Only an Active Directory / LDAP sign-in can be synchronised from here.')
            from . import directory as D
            try:
                return Response(D.sync(p, dry=bool(request.data.get('dry'))))
            except D.DirectoryError as e:
                O.note_error(p, e)
                return bad(str(e), 502)
        return bad('Unknown action.')

    def delete(self, request, pk):
        mine = {x['schema'] for x in _admin_schemas(request)}
        p = SSOProvider.objects.filter(pk=pk).first()
        if p is None or not set(p.schemas or []) <= mine or not mine:
            return bad('Not found.', 404)
        p.delete()
        return Response(status=204)
