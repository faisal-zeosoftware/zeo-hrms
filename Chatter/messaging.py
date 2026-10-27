"""v1.51.0 – WhatsApp / SMS through the company's own gateway (optional, off by default) and the company's own Firebase
project for push notifications.

Nothing is sent until an administrator switches it on in Settings → WhatsApp / SMS and enters the account of the
company's provider: Twilio, the WhatsApp Business Cloud API (Meta), or any HTTP gateway. Tokens and keys are kept
encrypted (Fernet, key derived from SECRET_KEY or ZEO_MESSAGING_KEY). Every message is logged (MessageLog).

send_event(user_id, kind, title, body, schema) is called by the mobile push signals (approval waiting, decision,
payslip ready) and by the HR helpdesk; it never raises and sends after the transaction commits, in a thread.
"""
import base64
import hashlib
import json
import logging
import re
import threading

import requests
from django.conf import settings as dj
from django.db import connection, transaction

log = logging.getLogger(__name__)
EVENTS = (('approval', 'Approvals waiting for me'), ('decision', 'Decisions on my requests'), ('payslip', 'Payslip ready'),
          ('helpdesk', 'Answers from the HR helpdesk'), ('notification', 'Every other notification'))
TIMEOUT = 20


# ------------------------------------------------------------------ secrets
def _fernet():
    from cryptography.fernet import Fernet
    seed = getattr(dj, 'ZEO_MESSAGING_KEY', '') or dj.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('zeo-messaging:' + seed).encode()).digest()))


def put_secret(value):
    return _fernet().encrypt(value.encode()).decode() if value else ''


def get_secret(token):
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        return ''


# ------------------------------------------------------------------ settings
def settings():
    from .models import MessagingSettings
    return MessagingSettings.objects.order_by('id').first() or MessagingSettings.objects.create(events=['approval', 'decision', 'payslip', 'helpdesk'])


def settings_json(s):
    return {'enabled': s.enabled, 'provider': s.provider, 'channel': s.channel, 'account_id': s.account_id, 'sender': s.sender,
            'has_secret': bool(s.secret), 'secret_last4': s.secret_last4, 'url': s.url, 'template': s.template, 'template_lang': s.template_lang,
            'country_code': s.country_code, 'events': s.events or [], 'last_test_at': s.last_test_at, 'last_test_result': s.last_test_result,
            'providers': [{'value': k, 'label': v} for k, v in s.PROVIDERS], 'all_events': [{'value': k, 'label': v} for k, v in EVENTS]}


def problems(s):
    if not s.enabled:
        return ''
    if s.provider == 'twilio' and not (s.account_id and s.sender and s.secret):
        return 'Twilio needs the account SID, the auth token and the sender number.'
    if s.provider == 'whatsapp_cloud' and not (s.account_id and s.secret):
        return 'The WhatsApp Cloud API needs the phone number ID and the access token.'
    if s.provider == 'http' and not re.match(r'^https?://', s.url or ''):
        return 'Give the gateway URL (https://…).'
    return ''


def normalise(number, cc='971'):
    """+9715xxxxxxxx from '050 123 4567', '00971…', '+971…'."""
    n = re.sub(r'[^\d+]', '', str(number or ''))
    if not n:
        return ''
    if n.startswith('00'):
        n = '+' + n[2:]
    if n.startswith('+'):
        return n if len(n) >= 9 else ''
    if n.startswith('0'):
        n = n[1:]
    return f'+{cc}{n}' if cc and len(n) >= 7 else ''


def employee_phone(emp, cc):
    return normalise(getattr(emp, 'emp_mobile_number_1', '') or getattr(emp, 'emp_mobile_number_2', ''), cc)


# ------------------------------------------------------------------ providers
def deliver(s, to, text):
    """(ok, result text)."""
    secret = get_secret(s.secret)
    if s.provider == 'twilio':
        frm, dst = s.sender, to
        if s.channel == 'whatsapp':
            frm, dst = ('whatsapp:' + frm if not frm.startswith('whatsapp:') else frm), 'whatsapp:' + to
        r = requests.post(f'https://api.twilio.com/2010-04-01/Accounts/{s.account_id}/Messages.json', data={'From': frm, 'To': dst, 'Body': text},
                          auth=(s.account_id, secret), timeout=TIMEOUT)
        ok = r.status_code in (200, 201)
        return ok, ('sent ' + (r.json().get('sid', '') if ok else '')) if ok else f'{r.status_code} {r.text[:150]}'
    if s.provider == 'whatsapp_cloud':
        body = {'messaging_product': 'whatsapp', 'to': to.lstrip('+')}
        if s.template:
            body.update(type='template', template={'name': s.template, 'language': {'code': s.template_lang or 'en'},
                                                   'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': text[:1000]}]}]})
        else:
            body.update(type='text', text={'body': text[:4000]})
        r = requests.post(f'https://graph.facebook.com/v20.0/{s.account_id}/messages', json=body, headers={'Authorization': f'Bearer {secret}'}, timeout=TIMEOUT)
        ok = r.status_code in (200, 201)
        return ok, 'sent' if ok else f'{r.status_code} {r.text[:150]}'
    # any HTTP gateway: POST JSON {to, text, channel, sender}
    headers = {'Content-Type': 'application/json'}
    if secret:
        headers['Authorization'] = f'Bearer {secret}'
    r = requests.post(s.url, data=json.dumps({'to': to, 'text': text, 'channel': s.channel, 'sender': s.sender}), headers=headers, timeout=TIMEOUT)
    ok = 200 <= r.status_code < 300
    return ok, f'sent ({r.status_code})' if ok else f'{r.status_code} {r.text[:150]}'


def _run(schema, log_id):
    from django.db import close_old_connections
    close_old_connections()
    try:
        from django_tenants.utils import schema_context
        with schema_context(schema or 'public'):
            from .models import MessageLog
            m = MessageLog.objects.filter(pk=log_id).first()
            if m is None:
                return
            s = settings()
            try:
                ok, res = deliver(s, m.to, m.text)
            except Exception as e:
                ok, res = False, str(e)[:200]
            m.status, m.result = ('sent' if ok else 'failed'), res[:255]
            m.save(update_fields=['status', 'result'])
    except Exception:
        log.exception('message deliver')
    finally:
        close_old_connections()


def queue(to, text, kind='', user_id=None, employee_id=None, inline=False):
    from .models import MessageLog
    s = settings()
    m = MessageLog.objects.create(user_id=user_id, employee_id=employee_id, to=to, channel=s.channel, kind=kind[:20], text=text[:2000])
    schema = getattr(connection, 'schema_name', '') or ''
    if inline or getattr(dj, 'ZEO_MESSAGING_INLINE', False):
        _run(schema, m.pk)
        m.refresh_from_db()
        return m
    transaction.on_commit(lambda: threading.Thread(target=_run, args=(schema, m.pk), daemon=True).start())
    return m


def send_event(user_id, kind, title, body='', schema=''):
    """An event for a user – sent by WhatsApp / SMS when the company switched it on for this kind. Never raises."""
    cur = getattr(connection, 'schema_name', '') or ''
    if schema and schema != cur and schema != 'public':
        try:
            from django_tenants.utils import schema_context
            with schema_context(schema):
                return _send_event(user_id, kind, title, body)
        except Exception:
            log.exception('messaging event')
            return None
    if cur in ('', 'public'):
        return None
    return _send_event(user_id, kind, title, body)


def _send_event(user_id, kind, title, body=''):
    try:
        s = settings()
        if not s.enabled or problems(s) or kind not in (s.events or []):
            return None
        E = __import__('django.apps', fromlist=['apps']).apps.get_model('EmpManagement', 'emp_master')
        emp = E.objects.filter(users_id=user_id).first()
        if emp is None:
            return None
        to = employee_phone(emp, s.country_code)
        if not to:
            from .models import MessageLog
            MessageLog.objects.create(user_id=user_id, employee_id=emp.pk, to='', channel=s.channel, kind=kind[:20], text=f'{title} {body}'.strip()[:2000],
                                      status='skipped', result='no mobile number')
            return None
        company = ''
        try:
            company = connection.tenant.name
        except Exception:
            pass
        text = f'{company + ": " if company else ""}{title}' + (f'\n{body}' if body else '')
        return queue(to, text, kind, user_id, emp.pk)
    except Exception:
        log.exception('messaging event')
        return None


# ------------------------------------------------------------------ push settings of the company
def push_settings():
    from .models import PushSettings
    return PushSettings.objects.order_by('id').first()


def push_credentials():
    """The company's Firebase service account (dict) or None."""
    try:
        p = push_settings()
        if p is None or not p.enabled or not p.key:
            return None
        c = json.loads(get_secret(p.key) or '{}')
        return c if c.get('private_key') and c.get('client_email') and c.get('project_id') else None
    except Exception:
        return None


def push_allowed(kind):
    p = push_settings()
    if p is None:
        return True
    if not p.enabled:
        return False
    return not p.events or kind in p.events or kind not in dict(EVENTS)
