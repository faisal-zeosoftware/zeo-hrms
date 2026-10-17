"""v1.25.0 – push notifications to the ZEO mobile app through Firebase Cloud Messaging (free, HTTP v1 API).

Setup (once per server): a Firebase project → Project settings → Service accounts → Generate new private key; put the
JSON file on the server (outside the code) and set ZEO_FCM_CREDENTIALS=/path/to/file.json in .env. Nothing is stored in
the database. Without it ZEO still records every push in PushLog (result "not configured") and the app works without
notifications.

send(user, title, body, data) is cheap to call anywhere: it runs after the database transaction is committed and in a
background thread (or a Celery task when Celery is running), and it never raises.
"""
import json
import logging
import threading
import time

import requests
from django.conf import settings
from django.db import transaction

log = logging.getLogger(__name__)
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/firebase.messaging'
_tok = {'value': '', 'until': 0.0}
_lock = threading.Lock()


def _creds():
    path = getattr(settings, 'ZEO_FCM_CREDENTIALS', '') or ''
    if not path:
        return None
    try:
        with open(path) as f:
            c = json.load(f)
        return c if c.get('private_key') and c.get('client_email') and c.get('project_id') else None
    except Exception:
        log.warning('ZEO_FCM_CREDENTIALS %s could not be read', path)
        return None


def configured():
    return _creds() is not None


def _access_token(c):
    with _lock:
        if _tok['value'] and time.time() < _tok['until']:
            return _tok['value']
        import jwt
        now = int(time.time())
        assertion = jwt.encode({'iss': c['client_email'], 'scope': SCOPE, 'aud': TOKEN_URL, 'iat': now, 'exp': now + 3600},
                               c['private_key'], algorithm='RS256')
        r = requests.post(TOKEN_URL, data={'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer', 'assertion': assertion}, timeout=15)
        r.raise_for_status()
        d = r.json()
        _tok.update(value=d['access_token'], until=time.time() + int(d.get('expires_in', 3600)) - 300)
        return _tok['value']


def _deliver(log_id):
    """Send one PushLog entry to every active phone of its user that has a push token."""
    from .models import MobileDevice, PushLog
    entry = PushLog.objects.filter(pk=log_id).first()
    if entry is None or entry.user_id is None:
        return
    devices = list(MobileDevice.objects.filter(user_id=entry.user_id, active=True).exclude(push_token=''))
    entry.devices = len(devices)
    c = _creds()
    if not devices:
        entry.result = 'no phone with notifications'
    elif c is None:
        entry.result = 'not configured'
    else:
        sent, errors = 0, []
        try:
            token = _access_token(c)
            url = f"https://fcm.googleapis.com/v1/projects/{c['project_id']}/messages:send"
            for d in devices:
                data = {str(k): str(v) for k, v in (entry.data or {}).items()}
                msg = {'message': {'token': d.push_token, 'notification': {'title': entry.title, 'body': entry.body},
                                   'data': data, 'android': {'priority': 'high', 'notification': {'channel_id': 'zeo', 'icon': 'ic_stat_zeo'}},
                                   'apns': {'payload': {'aps': {'sound': 'default'}}}}}
                r = requests.post(url, json=msg, headers={'Authorization': f'Bearer {token}'}, timeout=15)
                if r.status_code == 200:
                    sent += 1
                elif r.status_code in (404, 400) and ('UNREGISTERED' in r.text or 'INVALID_ARGUMENT' in r.text):
                    d.push_token = ''          # the app was removed or the token changed
                    d.save(update_fields=['push_token'])
                    errors.append('old token removed')
                else:
                    errors.append(f'{r.status_code}')
        except Exception as e:
            log.warning('push failed: %s', e)
            errors.append(str(e)[:80])
        entry.sent = sent
        entry.result = ('sent' if sent else 'not sent') + (f" ({', '.join(errors)[:150]})" if errors else '')
    entry.save(update_fields=['devices', 'sent', 'result'])


def send(user, title, body='', data=None, kind='', schema=''):
    """Queue a push to all phones of a user. Safe to call inside a transaction and from signals."""
    uid = getattr(user, 'pk', user)
    if not uid:
        return None
    try:
        from .models import MobileDevice, PushLog
        if not MobileDevice.objects.filter(user_id=uid, active=True).exists():
            return None              # no phone registered – nothing to do, nothing logged
        data = dict(data or {})
        if schema:
            data.setdefault('schema', schema)
        entry = PushLog.objects.create(user_id=uid, title=str(title)[:200], body=str(body or '')[:500], data=data, kind=kind[:30], schema=schema or '')
    except Exception:
        log.exception('push log')
        return None

    def go():
        if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False) or getattr(settings, 'ZEO_PUSH_INLINE', False):
            _safe(entry.pk)
        else:
            threading.Thread(target=_thread, args=(entry.pk,), daemon=True).start()
    transaction.on_commit(go)
    return entry


def _safe(log_id):
    try:
        _deliver(log_id)
    except Exception:
        log.exception('push deliver')


def _thread(log_id):
    from django.db import close_old_connections
    close_old_connections()
    _safe(log_id)
    close_old_connections()
