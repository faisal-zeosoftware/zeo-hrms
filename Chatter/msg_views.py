"""v1.51.0 – Settings → WhatsApp / SMS and Settings → Mobile push (company administrators)."""
import json

from django.db import connection
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import messaging as G
from .views import is_admin


def bad(msg, code=400):
    return Response({'detail': msg}, status=code)


class MessagingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        return Response(G.settings_json(G.settings()))

    def put(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        s = G.settings()
        d = request.data
        if 'enabled' in d:
            s.enabled = str(d['enabled']).lower() in ('1', 'true')
        if d.get('provider') in dict(s.PROVIDERS):
            s.provider = d['provider']
        if d.get('channel') in dict(s.CHANNELS):
            s.channel = d['channel']
        for f in ('account_id', 'sender', 'url', 'template', 'template_lang', 'country_code'):
            if f in d:
                setattr(s, f, str(d[f] or '').strip()[:300])
        if 'events' in d:
            s.events = [e for e in (d.get('events') or []) if e in dict(G.EVENTS)]
        if d.get('secret'):
            v = str(d['secret']).strip()
            s.secret, s.secret_last4 = G.put_secret(v), v[-4:]
        elif d.get('clear_secret'):
            s.secret, s.secret_last4 = '', ''
        p = G.problems(s)
        if p:
            return bad(p)
        s.save()
        return Response(G.settings_json(s))


class MessagingTestView(APIView):
    """POST {to, text?} – sends one message now and keeps the result."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        s = G.settings()
        if G.problems(s) or not (s.secret or s.provider == 'http'):
            return bad(G.problems(s) or 'Save the account and the token first.')
        to = G.normalise(request.data.get('to'), s.country_code)
        if not to:
            return bad('Give the mobile number to send the test to.')
        m = G.queue(to, (request.data.get('text') or 'ZEO test message – WhatsApp / SMS works.')[:500], 'test', request.user.pk, inline=True)
        s.last_test_at, s.last_test_result = timezone.now(), f'{m.status}: {m.result}'[:255]
        s.save(update_fields=['last_test_at', 'last_test_result'])
        return Response({'status': m.status, 'result': m.result, 'to': to})


class MessageLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        from .models import MessageLog
        return Response([{'id': m.pk, 'at': m.at, 'to': m.to, 'channel': m.channel, 'kind': m.kind, 'text': m.text[:200], 'status': m.status,
                          'result': m.result} for m in MessageLog.objects.all()[:200]])


def _push_json(p):
    from ZeoMobile import push
    from ZeoMobile.models import MobileDevice, PushLog
    schema = getattr(connection, 'schema_name', '') or ''
    own = bool(p and p.key)
    devices = MobileDevice.objects.filter(schema=schema, active=True)
    return {'enabled': p.enabled if p else True, 'own_project': own, 'project_id': p.project_id if p else '', 'client_email': p.client_email if p else '',
            'events': (p.events if p else []) or [], 'server_configured': push.configured(''), 'configured': push.configured(schema),
            'last_test_at': p.last_test_at if p else None, 'last_test_result': p.last_test_result if p else '',
            'devices': devices.count(), 'devices_with_token': devices.exclude(push_token='').count(),
            'recent': [{'at': x.created_at, 'kind': x.kind, 'title': x.title, 'devices': x.devices, 'sent': x.sent, 'result': x.result}
                       for x in PushLog.objects.filter(schema=schema).order_by('-id')[:15]],
            'all_events': [{'value': k, 'label': v} for k, v in G.EVENTS if k != 'helpdesk']}


class PushSettingsView(APIView):
    """GET / PUT {enabled, events, key_json (the Firebase service-account file content), clear_key}."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        return Response(_push_json(G.push_settings()))

    def put(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        from .models import PushSettings
        p = G.push_settings() or PushSettings()
        d = request.data
        if 'enabled' in d:
            p.enabled = str(d['enabled']).lower() in ('1', 'true')
        if 'events' in d:
            p.events = [e for e in (d.get('events') or []) if e in dict(G.EVENTS)]
        if d.get('key_json'):
            try:
                c = json.loads(d['key_json'])
            except (TypeError, ValueError):
                return bad('This is not the service-account JSON file of Firebase.')
            if not (c.get('private_key') and c.get('client_email') and c.get('project_id')):
                return bad('The file has no private key, client e-mail or project ID – download it again (Project settings → Service accounts).')
            p.key, p.project_id, p.client_email = G.put_secret(json.dumps(c)), c['project_id'][:120], c['client_email'][:200]
        elif d.get('clear_key'):
            p.key, p.project_id, p.client_email = '', '', ''
        p.save()
        return Response(_push_json(p))


class PushTestView(APIView):
    """POST – a test push to the phones of the administrator, sent now."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not is_admin(request):
            return bad('Administrators only.', 403)
        from ZeoMobile import push
        from ZeoMobile.models import MobileDevice, PushLog
        schema = getattr(connection, 'schema_name', '') or ''
        if not MobileDevice.objects.filter(user_id=request.user.pk, active=True).exclude(push_token='').exists():
            return bad('Your account has no phone with the ZEO app and notifications allowed – sign in on the app first.')
        e = PushLog.objects.create(user_id=request.user.pk, title='ZEO test', body='Push notifications work.', data={'route': '/m/home', 'kind': 'test'},
                                   kind='test', schema=schema)
        push._safe(e.pk)
        e.refresh_from_db()
        p = G.push_settings()
        if p is not None:
            p.last_test_at, p.last_test_result = timezone.now(), e.result[:255]
            p.save(update_fields=['last_test_at', 'last_test_result'])
        return Response({'result': e.result, 'sent': e.sent, 'devices': e.devices})
