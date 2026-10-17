"""v1.25.0 – API of the ZEO mobile app (version 1).

Public (/mobile/api/…, no company):
  GET  info/                 – what this server is: product, version, API level, cloud, push, oldest app version allowed
  GET  devices/              – my phones;  POST devices/ – register / update this phone (push token, language, biometric)
  DELETE devices/<id>/       – sign a phone out (it can no longer unlock with the fingerprint)
  POST unlock/               – fingerprint / face unlock: the phone sends its device id and its unlock key → a new login

Company (/mobile-co/api/…?schema=<company>):
  GET  home/                 – everything the home screen shows, in one call
  GET  approvals/            – what waits for my decision;  POST approvals/<module>/<id>/<approve|reject>/
  GET  requests/             – my own requests and their state

The app talks only to these and to the existing self-service, leave, payslip and attendance APIs, so the server can
change its screens without breaking phones that have not updated yet. API_LEVEL rises only when that changes.
"""
import inspect
import logging
import os

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MobileDevice

log = logging.getLogger(__name__)
API_LEVEL = 1
MIN_APP = '1.25.0'


def tr(text, *args):
    try:
        from Translations.i18n import tr as _tr
        return _tr(text, *args)
    except Exception:
        return text.format(*args) if args else text


def server_version():
    try:
        with open(os.path.join(settings.BASE_DIR, 'VERSION')) as f:
            return f.read().strip()
    except Exception:
        return ''


def _cloud():
    try:
        from ZeoCloud.services import cloud_on
        return cloud_on()
    except Exception:
        return False


def _tokens(user, device=None):
    from rest_framework_simplejwt.tokens import RefreshToken
    r = RefreshToken.for_user(user)
    r['otp'] = True                     # same as after the e-mail / SMS code: the phone was set up after it
    if device is not None:
        r['dev'] = device.pk
    return {'access': str(r.access_token), 'refresh': str(r), 'user_id': user.pk, 'username': user.username,
            'email': user.email, 'is_ess': bool(user.is_ess), 'is_superuser': bool(user.is_superuser)}


def _verified(request):
    """A phone may be registered only by a login that passed the full sign-in (code, or no code needed)."""
    u = request.user
    if u.is_superuser or getattr(u, 'is_ess', False):
        return True
    auth = getattr(request, 'auth', None)
    try:
        return bool(auth and auth.get('otp'))
    except Exception:
        return False


def device_json(d, me=None):
    return {'id': d.pk, 'platform': d.platform, 'model': d.model, 'os_version': d.os_version, 'app_version': d.app_version,
            'biometric': d.biometric, 'push': bool(d.push_token), 'active': d.active, 'lang': d.lang, 'schema': d.schema,
            'created_at': d.created_at.isoformat(), 'last_seen': d.last_seen.isoformat(), 'this': bool(me and me == d.pk)}


# ---------------------------------------------------------------- public
class InfoView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from . import push
        return Response({'product': 'ZEO', 'server_version': server_version(), 'api': API_LEVEL, 'min_app': MIN_APP,
                         'cloud': _cloud(), 'push': push.configured(), 'name': getattr(settings, 'ZEO_SERVER_NAME', '') or '',
                         'features': ['checkin', 'leave', 'payslips', 'approvals', 'requests', 'biometric', 'push'],
                         'time': timezone.now().isoformat()})


class DevicesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        me = getattr(request, 'auth', None)
        me = me.get('dev') if me else None
        qs = MobileDevice.objects.filter(user=request.user, active=True)
        return Response([device_json(d, me) for d in qs])

    def post(self, request):
        d = request.data
        uid = str(d.get('uid') or '').strip()[:80]
        if len(uid) < 8:
            return Response({'detail': tr('The app did not send its device id.')}, status=400)
        if not _verified(request):
            return Response({'detail': tr('Sign in with your code first, then turn on fingerprint sign-in.')}, status=403)
        dev, created = MobileDevice.objects.get_or_create(user=request.user, uid=uid)
        for f, n in (('platform', 10), ('model', 120), ('os_version', 40), ('app_version', 20), ('lang', 10), ('schema', 63)):
            if f in d and d[f] is not None:
                setattr(dev, f, str(d[f])[:n])
        if dev.platform not in dict(MobileDevice.PLATFORMS):
            dev.platform = 'android'
        if 'push_token' in d:
            tok = str(d.get('push_token') or '')[:4000]
            if tok:   # one phone, one owner: a token moved to another login is removed from the old one
                MobileDevice.objects.filter(push_token=tok).exclude(pk=dev.pk).update(push_token='')
            dev.push_token = tok
        key = None
        if d.get('biometric') is not None:
            dev.biometric = bool(d.get('biometric'))
            if dev.biometric and (d.get('new_key') or not dev.key_hash):
                key = dev.new_key()
            if not dev.biometric:
                dev.key_hash = ''
        dev.active, dev.revoked_at, dev.last_seen = True, None, timezone.now()
        dev.save()
        out = device_json(dev, dev.pk)
        if key:
            out['key'] = key          # shown once; the app keeps it in the phone keystore behind the fingerprint
        return Response(out, status=201 if created else 200)


class DeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        dev = MobileDevice.objects.filter(pk=pk, user=request.user).first()
        if dev is None:
            return Response({'detail': tr('Not found.')}, status=404)
        dev.active, dev.key_hash, dev.push_token, dev.biometric, dev.revoked_at = False, '', '', False, timezone.now()
        dev.save()
        return Response({'detail': tr('This phone was signed out.')})


class UnlockView(APIView):
    """Fingerprint / face unlock. The key never leaves the phone keystore except to this call."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        uid = str(request.data.get('uid') or '')[:80]
        key = str(request.data.get('key') or '')
        did = request.data.get('device')
        fails_key = f'zeo:mobile:unlock:{uid}'
        if cache.get(fails_key, 0) >= 10:
            return Response({'detail': tr('Too many tries. Sign in with your password.')}, status=429)
        dev = MobileDevice.objects.select_related('user').filter(pk=did, uid=uid, active=True, biometric=True).first() if str(did).isdigit() else None
        ok = dev is not None and dev.key_ok(key) and dev.user.is_active
        max_idle = int(getattr(settings, 'ZEO_MOBILE_IDLE_DAYS', 90))
        if ok and (timezone.now() - dev.last_seen).days > max_idle:
            dev.key_hash, dev.biometric = '', False
            dev.save(update_fields=['key_hash', 'biometric'])
            return Response({'detail': tr('Fingerprint sign-in was not used for {0} days. Sign in with your password.', max_idle)}, status=401)
        if not ok:
            cache.set(fails_key, cache.get(fails_key, 0) + 1, 3600)
            return Response({'detail': tr('This phone is no longer signed in. Sign in with your password.')}, status=401)
        cache.delete(fails_key)
        dev.last_seen = timezone.now()
        dev.save(update_fields=['last_seen'])
        out = _tokens(dev.user, dev)
        out['device'] = device_json(dev, dev.pk)
        return Response(out)


# ---------------------------------------------------------------- company
def _ess(request):
    from DashboardManagement import services as S
    return S


class HomeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        S = _ess(request)
        user = request.user
        data = S.ess_summary(user)
        emp = data.get('employee')
        out = {'today': data.get('today'), 'employee': emp, 'has_team': data.get('has_team'), 'is_admin': data.get('is_admin'),
               'message': data.get('message', ''),
               'leave_balances': [b for b in (data.get('leave_balances') or []) if b.get('openings') or b.get('balance')][:4],
               'attendance': (data.get('attendance') or {}).get('today'),
               'month': {k: (data.get('attendance') or {}).get(k) for k in ('month_label', 'present', 'late', 'absent', 'leave', 'avg_hours')},
               'next_leave': data.get('next_leave'), 'payslip': data.get('payslip'),
               'tasks': {k: (data.get('tasks') or {}).get(k) for k in ('open', 'overdue', 'due_week')},
               'holidays': (data.get('holidays') or [])[:3], 'announcements': (data.get('announcements') or [])[:3]}
        try:
            out['approvals'] = len(S.pending_approvals(user, request))
        except Exception:
            out['approvals'] = 0
        try:   # self-service counts (letters ready, unread announcements, documents expiring, next payslip)
            from SelfService.views import DashboardView
            d = DashboardView().get(request).data
            out['ess'] = {k: d.get(k) for k in ('letters_ready', 'announcements_unread', 'documents_expiring', 'next_payslip', 'last_payslip',
                                                 'change_requests_pending', 'complaints_open')}
        except Exception:
            out['ess'] = {}
        try:   # attendance state for the check-in button
            from AttendancePlus.views import PunchViewSet
            v = PunchViewSet()
            v.request, v.format_kwarg, v.kwargs, v.action = request, None, {}, 'my_status'
            st = v.my_status(request).data
            out['punch'] = {k: st.get(k) for k in ('next', 'clocked_in', 'on_break', 'shift', 'methods', 'require_gps', 'require_selfie', 'work_date')}
            out['punch']['punches'] = [{'kind': p.get('resolved_kind') or p.get('kind'), 'ts': p.get('ts'), 'local': p.get('local_time') or p.get('time')}
                                       for p in (st.get('punches') or [])][-6:]
        except Exception as e:
            out['punch'] = {'error': str(getattr(e, 'detail', e))[:200]}
        try:
            from ZeoApps.services import is_active
            out['apps'] = {k: bool(is_active(k)) for k in ('hrms', 'payroll', 'projects')}
        except Exception:
            out['apps'] = {}
        return Response(out)


def _approval_model(module):
    from django.apps import apps
    from DashboardManagement.services import REQ
    r = REQ.get(module)
    if not r:
        return None, None
    return apps.get_model(*r[3].split('.')), r


class ApprovalsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        S = _ess(request)
        from DashboardManagement.services import REQ
        try:
            from DashboardManagement.dash171 import LINKS
        except Exception:
            LINKS = {}
        rows = []
        for a in S.pending_approvals(request.user, request):
            m = a.get('module')
            can = m in REQ
            rows.append({'module': m, 'label': tr(a.get('module_label') or m), 'approval_id': a.get('approval_id'), 'request_id': a.get('request_id'),
                         'level': a.get('level'), 'document_number': a.get('document_number'), 'summary': a.get('summary'),
                         'date': a.get('date'), 'employee': a.get('employee'), 'employee_code': a.get('employee_code'),
                         'in_app': can, 'needs_reason': can and m in ('leave', 'loan', 'advance', 'airticket'),
                         'link': (LINKS.get(m) or {}).get('approval', '') if not can else ''})
        return Response(rows)


class ApprovalActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, module, pk, act):
        if act not in ('approve', 'reject'):
            return Response({'detail': tr('Unknown action.')}, status=400)
        AM, reg = _approval_model(module)
        if AM is None:
            return Response({'detail': tr('Open this request in ZEO to decide.')}, status=400)
        a = AM.objects.filter(pk=pk).first()
        if a is None:
            return Response({'detail': tr('Not found.')}, status=404)
        u = request.user
        delegate_ok = bool(getattr(a, 'is_deligate', False) and getattr(a, 'deligate_to_id', None) == u.pk)
        if getattr(a, 'approver_id', None) != u.pk and not delegate_ok and not u.is_superuser:
            return Response({'detail': tr('This request is not waiting for you.')}, status=403)
        if str(getattr(a, 'status', '')).lower() != 'pending':
            return Response({'detail': tr('This request was already decided.')}, status=409)
        note = str(request.data.get('note') or '')[:1000] or None
        reason = str(request.data.get('reason') or request.data.get('rejection_reason') or '').strip()
        fn = getattr(a, act)
        params = inspect.signature(fn).parameters
        try:
            if act == 'approve':
                fn(note=note) if 'note' in params else fn()
            else:
                if 'rejection_reason' in params:
                    if not reason:
                        return Response({'detail': tr('Give the reason for rejecting.')}, status=400)
                    fn(rejection_reason=reason, note=note)
                else:
                    fn(note=note or reason or None) if 'note' in params else fn()
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        except Exception as e:
            log.exception('mobile approval')
            return Response({'detail': tr('It could not be saved: {0}', str(e)[:200])}, status=400)
        return Response({'detail': tr('Approved.') if act == 'approve' else tr('Rejected.')})


class RequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        S = _ess(request)
        emp = S.my_employee(request.user)
        if emp is None:
            return Response([])
        rows = S.my_requests(emp, limit=60)
        out = []
        for r in rows:
            out.append({'module': r.get('module'), 'label': tr(r.get('module_label') or r.get('module') or ''), 'id': r.get('id'),
                        'document_number': r.get('document_number'), 'summary': r.get('summary'), 'date': r.get('date'),
                        'status': r.get('status_label') or r.get('status'), 'status_key': r.get('status_key')})
        return Response(out)
