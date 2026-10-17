"""v1.28.0 – licence API (/licence/api/...), public schema.

On the client's server (Enterprise):  GET  /licence/api/            status, installation ID, licence, usage (administrators)
                                      GET  /licence/api/banner/     status line for every signed-in user
                                      POST /licence/api/upload/     the .zeolic file (administrators)
Requests (any server, used on ZEO Cloud): POST /licence/api/enterprise-request/   sign-up page / owners
On Adept's server (platform admins):  GET|PATCH /licence/api/console/requests/[<id>/]
                                      GET|POST  /licence/api/console/licences/   ·   GET …/licences/<id>/download/
"""
import datetime as dt
import uuid

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr
from . import licence as L
from .models import EnterpriseRequest, IssuedLicence, LicenceFile
from .signing import LicenceError, sign

APPS = ['hrms', 'payroll', 'projects', 'crm', 'property', 'maintenance', 'hse']
FEATURES = ['group']


def _is_admin(user):
    if user.is_superuser:
        return True
    try:
        from ZeoCloud.services import is_platform_admin
        return is_platform_admin(user)
    except Exception:
        return False


def _platform_admin(user):
    try:
        from ZeoCloud.services import is_platform_admin
        return is_platform_admin(user)
    except Exception:
        return False


# ------------------------------------------------------------------ client
class StatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _is_admin(request.user):
            return Response({'detail': tr('Only a server administrator can see the licence.')}, status=403)
        st = L.current(fresh=True)
        files = [{'id': f.id, 'client': f.client, 'licence_id': f.licence_id, 'valid_until': f.valid_until.isoformat() if f.valid_until else None,
                  'uploaded_at': f.uploaded_at.isoformat(), 'by': getattr(f.uploaded_by, 'email', '') or ''} for f in LicenceFile.objects.select_related('uploaded_by')[:10]]
        lim = (st.get('licence') or {}).get('limits') or {}
        return Response(dict(st, usage=L.usage(), limits=lim, files=files, adept_email=getattr(settings, 'ZEO_LICENCE_CONTACT', '') or 'support@adeptbs.ae',
                             grace_days=L._days('ZEO_LICENCE_GRACE_DAYS', 15)))


class BannerView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not L.edition_on():
            return Response({'status': 'off'})
        st = L.current()
        show = st['status'] in ('expiring', 'grace', 'expired', 'evaluation', 'missing', 'not_yet')
        return Response({'status': st['status'], 'message': st['message'] if show else '', 'read_only': st['read_only'],
                         'admin': _is_admin(request.user)})


class UploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _is_admin(request.user):
            return Response({'detail': tr('Only a server administrator can upload the licence.')}, status=403)
        f = request.FILES.get('file')
        text = f.read().decode('utf-8', 'replace') if f else str(request.data.get('text') or '')
        if len(text) > 20000:
            return Response({'detail': tr('This is not a ZEO licence file.')}, status=400)
        try:
            p = L.check_text(text)
        except LicenceError as e:
            return Response({'detail': tr(str(e))}, status=400)
        LicenceFile.objects.create(text=text, licence_id=str(p.get('licence_id') or ''), client=str(p.get('client') or '')[:200],
                                   valid_until=L._parse_date(p.get('valid_until')), uploaded_by=request.user)
        L.forget()
        st = L.current(fresh=True)
        return Response(dict(st, detail=tr('Licence for {0} saved – valid until {1}.', p.get('client'), p.get('valid_until'))))


# ------------------------------------------------------------------ Enterprise requests (always through Adept)
class EnterpriseRequestView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        ip = (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip() or None
        key = f'zeo:entreq:{ip}'
        n = cache.get(key) or 0
        if n >= 5:
            return Response({'detail': tr('Too many requests from this address – please try again in an hour.')}, status=429)
        d = request.data
        company, name, email = [str(d.get(k) or '').strip() for k in ('company', 'name', 'email')]
        if len(company) < 2 or len(name) < 2 or '@' not in email:
            return Response({'detail': tr('Give the company, your name and your e-mail.')}, status=400)

        def num(k):
            v = str(d.get(k) or '').strip()
            return int(v) if v.isdigit() else None
        acc = None
        user = getattr(request, 'user', None)
        try:   # an account owner asking from Settings → Subscription (the token is optional here)
            from rest_framework_simplejwt.authentication import JWTAuthentication
            res = JWTAuthentication().authenticate(request)
            if res:
                from ZeoCloud.models import Account
                acc = Account.objects.filter(owner=res[0]).order_by('id').first()
        except Exception:
            acc = None
        r = EnterpriseRequest.objects.create(company=company[:200], name=name[:150], email=email[:254], phone=str(d.get('phone') or '')[:40],
                                             country=str(d.get('country') or '')[:80], employees=num('employees'), companies=num('companies'),
                                             message=str(d.get('message') or '')[:2000], account=acc, ip=ip)
        cache.set(key, n + 1, 3600)
        to = getattr(settings, 'ZEO_CLOUD_ADMIN_EMAIL', '') or ''
        if to:
            from django.core.mail import send_mail
            send_mail(f'ZEO Enterprise request – {r.company}',
                      f'{r.name} <{r.email}> {r.phone}\nCompany: {r.company} ({r.country})\nEmployees: {r.employees or "-"}  Companies: {r.companies or "-"}\n'
                      f'{"ZEO Cloud account: " + acc.name if acc else ""}\n\n{r.message}\n\nConsole → Enterprise.', None, [to], fail_silently=True)
        return Response({'ok': True, 'detail': tr('Thank you – Adept will contact you about ZEO Enterprise.')}, status=201)


def _req_json(r):
    return {'id': r.id, 'company': r.company, 'name': r.name, 'email': r.email, 'phone': r.phone, 'country': r.country,
            'employees': r.employees, 'companies': r.companies, 'message': r.message, 'account': getattr(r.account, 'name', ''),
            'state': r.state, 'note': r.note, 'created_at': r.created_at.isoformat(), 'licences': r.licences.count()}


class ConsoleRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None):
        if not _platform_admin(request.user):
            return Response({'detail': 'Adept only.'}, status=403)
        return Response({'requests': [_req_json(r) for r in EnterpriseRequest.objects.select_related('account')[:200]]})

    def patch(self, request, pk=None):
        if not _platform_admin(request.user):
            return Response({'detail': 'Adept only.'}, status=403)
        r = EnterpriseRequest.objects.filter(pk=pk).first()
        if r is None:
            return Response({'detail': 'Not found.'}, status=404)
        if request.data.get('state') in dict(EnterpriseRequest.STATES):
            r.state = request.data['state']
        if 'note' in request.data:
            r.note = str(request.data.get('note') or '')[:4000]
        r.save()
        return Response(_req_json(r))


# ------------------------------------------------------------------ issuing (Adept)
def _lic_json(x):
    return {'id': x.id, 'licence_id': x.licence_id, 'client': x.client, 'installation_id': x.installation_id,
            'valid_from': x.valid_from.isoformat(), 'valid_until': x.valid_until.isoformat(), 'limits': x.limits, 'apps': x.apps,
            'features': x.features, 'note': x.note, 'request_id': x.request_id, 'issued_by': getattr(x.issued_by, 'email', '') or '',
            'issued_at': x.issued_at.isoformat()}


def signing_state():
    from .signing import key_id_of, load_private
    try:
        return {'can_sign': True, 'key_id': key_id_of(load_private()), 'problem': ''}
    except Exception as e:
        return {'can_sign': False, 'key_id': '', 'problem': str(e)}


class ConsoleLicencesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk=None, what=None):
        if not _platform_admin(request.user):
            return Response({'detail': 'Adept only.'}, status=403)
        if pk and what == 'download':
            x = IssuedLicence.objects.filter(pk=pk).first()
            if x is None:
                return Response({'detail': 'Not found.'}, status=404)
            resp = HttpResponse(x.text, content_type='application/octet-stream')
            safe = ''.join(ch for ch in x.client if ch.isalnum() or ch in ' -_')[:40].strip().replace(' ', '_') or 'client'
            resp['Content-Disposition'] = f'attachment; filename="{safe}_{x.valid_until}.zeolic"'
            return resp
        return Response(dict(signing_state(), apps=APPS, features=FEATURES,
                             licences=[_lic_json(x) for x in IssuedLicence.objects.select_related('issued_by')[:200]]))

    def post(self, request, pk=None, what=None):
        if not _platform_admin(request.user):
            return Response({'detail': 'Adept only.'}, status=403)
        d = request.data
        client, inst = str(d.get('client') or '').strip(), str(d.get('installation_id') or '').strip().upper()
        try:
            vf = dt.date.fromisoformat(str(d.get('valid_from') or timezone.localdate().isoformat())[:10])
            vu = dt.date.fromisoformat(str(d.get('valid_until') or '')[:10])
        except ValueError:
            return Response({'detail': tr('Enter the dates as yyyy-mm-dd.')}, status=400)
        if len(client) < 2 or not inst.startswith('ZEO-'):
            return Response({'detail': tr('Give the client and the installation ID (ZEO-…) shown in their Settings → Licence.')}, status=400)
        if vu <= vf:
            return Response({'detail': tr('The end date must be after the start date.')}, status=400)
        limits = {}
        for k in ('companies', 'employees', 'users'):
            v = str((d.get('limits') or {}).get(k, '') if isinstance(d.get('limits'), dict) else d.get(k) or '').strip()
            if v.isdigit():
                limits[k] = int(v)
        apps = [a for a in (d.get('apps') if d.get('apps') is not None else APPS) if a in APPS]          # [] = HR and Settings only
        feats = [f for f in (d.get('features') if d.get('features') is not None else FEATURES) if f in FEATURES]
        lid = 'LIC-' + uuid.uuid4().hex[:12].upper()
        payload = {'product': 'zeo', 'edition': 'enterprise', 'licence_id': lid, 'client': client, 'installation_id': inst,
                   'valid_from': vf.isoformat(), 'valid_until': vu.isoformat(), 'limits': limits, 'apps': apps, 'features': feats,
                   'issued_at': timezone.now().isoformat(timespec='seconds'), 'issued_by': 'Adept Business Solutions'}
        try:
            text = sign(payload)
        except LicenceError as e:
            return Response({'detail': str(e)}, status=400)
        req = EnterpriseRequest.objects.filter(pk=d.get('request_id')).first() if str(d.get('request_id') or '').isdigit() else None
        x = IssuedLicence.objects.create(licence_id=lid, client=client, installation_id=inst, valid_from=vf, valid_until=vu, limits=limits,
                                         apps=apps, features=feats, text=text, request=req, note=str(d.get('note') or '')[:2000], issued_by=request.user)
        if req and req.state in ('new', 'contacted'):
            req.state = 'licensed'
            req.save(update_fields=['state'])
        return Response(_lic_json(x), status=201)
