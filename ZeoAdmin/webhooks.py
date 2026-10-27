"""v1.52.0 – outgoing webhooks: a record of a chosen type is created / updated / deleted → POST JSON to the company's URL.

Body: {"event": "calendars.employee_leave_request.created", "model": …, "id": …, "at": …, "company": schema, "data": {fields}}
Headers: X-ZEO-Event, X-ZEO-Delivery, X-ZEO-Signature: sha256=<HMAC-SHA256 of the body with the webhook secret>.
Sent after the transaction commits, in a thread; a failed delivery is tried again (beat 'zeoadmin-webhooks', every 5 minutes)
up to 5 times (after 1, 5, 30 minutes, 2 and 6 hours). A webhook that fails 20 times in a row is switched off.
"""
import base64
import datetime
import decimal
import hashlib
import hmac
import json
import logging
import threading
import time
import uuid

import requests
from django.apps import apps
from django.conf import settings as dj
from django.db import connection, transaction
from django.utils import timezone

log = logging.getLogger(__name__)
TIMEOUT = 10
BACKOFF = [60, 300, 1800, 7200, 21600]
MAX_FAILURES = 20
# the record types offered on the screen (any app_label.model also works)
CHOICES = [
    ('EmpManagement.emp_master', 'Employee'), ('calendars.employee_leave_request', 'Leave request'),
    ('PayrollManagement.Payslip', 'Payslip'), ('PayrollManagement.PayrollRun', 'Payroll run'),
    ('PayrollManagement.LoanApplication', 'Loan'), ('AttendancePlus.Punch', 'Attendance punch'),
    ('AttendancePlus.CorrectionRequest', 'Attendance correction'), ('EmpManagement.EmployeeResignation', 'Resignation'),
    ('ExpenseManagement.ExpenseReport', 'Expense report'), ('ExpenseManagement.Trip', 'Trip'),
    ('ZeoHR.HRTicket', 'HR ticket'), ('ZeoHR.DisciplinaryCase', 'Disciplinary case'), ('RecruitmentManagement.Candidate', 'Candidate'),
    ('ZeoCRM.Opportunity', 'CRM opportunity'), ('ZeoProperty.Lease', 'Lease'), ('ZeoProjects.Ticket', 'Support ticket'),
]
EVENTS = ('created', 'updated', 'deleted')
_cache = {}   # schema → (until, {model key: [webhook ids]})


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('zeo-webhooks:' + dj.SECRET_KEY).encode()).digest()))


def put_secret(v):
    return _fernet().encrypt(v.encode()).decode() if v else ''


def get_secret(v):
    try:
        return _fernet().decrypt(v.encode()).decode() if v else ''
    except Exception:
        return ''


def model_label(key):
    d = dict(CHOICES)
    if key in d:
        return d[key]
    try:
        return str(apps.get_model(*key.split('.'))._meta.verbose_name).capitalize()
    except Exception:
        return key


def valid_model(key):
    try:
        apps.get_model(*str(key).split('.'))
        return True
    except Exception:
        return False


def forget():
    _cache.pop(getattr(connection, 'schema_name', ''), None)


def hooks_for(key):
    schema = getattr(connection, 'schema_name', '') or ''
    if schema in ('', 'public'):
        return []
    c = _cache.get(schema)
    if c is None or c[0] < time.time():
        from .models import Webhook
        m = {}
        try:
            for h in Webhook.objects.filter(active=True).only('id', 'models_list', 'events'):
                for k in h.models_list or []:
                    m.setdefault(k.lower(), []).append((h.pk, tuple(h.events or EVENTS)))
        except Exception:
            m = {}
        c = _cache[schema] = (time.time() + 60, m)
    return c[1].get(key.lower(), [])


def _json(v):
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if hasattr(v, 'url') and hasattr(v, 'name'):   # a file
        return v.name or ''
    return v


def record_data(obj):
    out = {}
    for f in obj._meta.concrete_fields:
        if f.get_internal_type() in ('BinaryField',) or 'password' in f.name or f.name in ('otp', 'pin_hash', 'key_hash', 'secret'):
            continue
        try:
            v = getattr(obj, f.attname)
            json.dumps(_json(v))
            out[f.attname] = _json(v)
        except Exception:
            continue
    return out


def fire(obj, event):
    key = f'{obj._meta.app_label}.{obj._meta.model_name}'
    hooks = [hid for hid, evs in hooks_for(key) if event in evs]
    if not hooks:
        return
    from .models import WebhookDelivery
    payload = {'event': f'{key.lower()}.{event}', 'model': key, 'id': obj.pk, 'at': timezone.now().isoformat(),
               'company': getattr(connection, 'schema_name', ''), 'data': record_data(obj)}
    schema = getattr(connection, 'schema_name', '')
    ids = [WebhookDelivery.objects.create(webhook_id=hid, event=payload['event'], payload=payload).pk for hid in hooks]
    transaction.on_commit(lambda: threading.Thread(target=_thread, args=(schema, ids), daemon=True).start())


def _thread(schema, ids):
    from django.db import close_old_connections
    close_old_connections()
    try:
        from django_tenants.utils import schema_context
        with schema_context(schema):
            for i in ids:
                deliver(i)
    except Exception:
        log.exception('webhook thread')
    finally:
        close_old_connections()


def deliver(delivery_id):
    from .models import WebhookDelivery
    d = WebhookDelivery.objects.select_related('webhook').filter(pk=delivery_id).first()
    if d is None or d.status == 'sent':
        return d
    h = d.webhook
    body = json.dumps(d.payload, separators=(',', ':'), default=str).encode()
    sig = hmac.new((get_secret(h.secret) or '').encode(), body, hashlib.sha256).hexdigest()
    d.tries += 1
    try:
        r = requests.post(h.url, data=body, timeout=TIMEOUT, headers={'Content-Type': 'application/json', 'User-Agent': 'ZEO-Webhook/1.52',
                                                                     'X-ZEO-Event': d.event, 'X-ZEO-Delivery': str(d.pk),
                                                                     'X-ZEO-Signature': f'sha256={sig}'})
        ok = 200 <= r.status_code < 300
        d.code, d.response = r.status_code, (r.text or '')[:255]
    except Exception as e:
        ok, d.code, d.response = False, None, str(e)[:255]
    now = timezone.now()
    if ok:
        d.status, d.next_try = 'sent', None
        h.failures = 0
    else:
        d.status = 'failed'
        d.next_try = now + datetime.timedelta(seconds=BACKOFF[d.tries - 1]) if d.tries <= len(BACKOFF) else None
        h.failures = (h.failures or 0) + 1
        if h.failures >= MAX_FAILURES:
            h.active = False
            forget()
    d.save(update_fields=['tries', 'code', 'response', 'status', 'next_try'])
    h.last_status, h.last_at = (f'{d.code or "error"} {"sent" if ok else "failed"}')[:120], now
    h.save(update_fields=['last_status', 'last_at', 'failures', 'active'])
    return d


def retry_due():
    from .models import WebhookDelivery
    n = 0
    for d in WebhookDelivery.objects.filter(status='failed', next_try__isnull=False, next_try__lte=timezone.now(), webhook__active=True)[:200]:
        deliver(d.pk)
        n += 1
    return n


def test(h):
    """A test delivery now (inline)."""
    from .models import WebhookDelivery
    d = WebhookDelivery.objects.create(webhook=h, event='zeo.test', payload={'event': 'zeo.test', 'at': timezone.now().isoformat(),
                                                                           'company': getattr(connection, 'schema_name', ''), 'data': {'hello': 'ZEO'}})
    return deliver(d.pk)


def verify_signature(secret, body, header):
    """For the receiving side / tests: header 'sha256=<hex>'."""
    exp = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(exp, header or '')
