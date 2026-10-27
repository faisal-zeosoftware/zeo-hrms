"""v1.25.0 – what sends a push to the phone:

- every in-app notification (UserNotificationInbox) to its user;
- a new approval waiting for a manager (leave, late in / early out, general and document requests, assets, loans,
  advance salary, air tickets, resignations) to the approver (or the person it was delegated to);
- the decision on a request (approved / rejected) to the employee who asked;
- a payslip released to the employee.

Texts are in the language of the person who gets them. Nothing is sent (or logged) for users without a phone.
"""
import logging

from django.db import connection
from django.db.models.signals import post_save, pre_save

log = logging.getLogger(__name__)
_done = False


def _lang_t(user_id, schema, text, *args):
    try:
        from LanguagePack.services import t, user_language
        return t(text, user_language(user_id, schema), list(args) if args else None)
    except Exception:
        try:
            return text.format(*args)
        except Exception:
            return text


def _schema():
    return getattr(connection, 'schema_name', '') or ''


def _has_phone(uid):
    if not uid:
        return False
    from .models import MobileDevice
    try:
        return MobileDevice.objects.filter(user_id=uid, active=True).exists()
    except Exception:
        return False


def _push(uid, kind, title, body, route, schema, *targs):
    from . import push
    text = _lang_t(uid, schema, title, *targs)
    if _has_phone(uid):
        push.send(uid, text, body, {'route': route, 'kind': kind}, kind=kind, schema=schema)
    try:   # v1.51.0 WhatsApp / SMS through the company's gateway, when switched on for this kind
        from Chatter.messaging import send_event
        send_event(uid, kind, text, body, schema)
    except Exception:
        log.debug('messaging', exc_info=True)


# ---------------------------------------------------------------- in-app notifications
def inbox_saved(sender, instance, created, raw=False, **kw):
    if not created or raw:
        return
    try:
        from . import push
        uid = instance.user_id
        if (instance.notification_type or 'general') == 'general':   # v1.51.0 other notifications by WhatsApp / SMS when chosen
            try:
                from Chatter.messaging import send_event
                send_event(uid, 'notification', instance.title or 'ZEO', (instance.message or '')[:400], instance.schema_name or '')
            except Exception:
                pass
        if not _has_phone(uid):
            return
        if (instance.notification_type or 'general') != 'general':
            # the same event already went out as an approval / decision push with a clearer text: no second buzz
            from datetime import timedelta
            from django.utils import timezone
            from .models import PushLog
            if PushLog.objects.filter(user_id=uid, kind__in=('approval', 'decision'),
                                      created_at__gte=timezone.now() - timedelta(seconds=30)).exists():
                return
        push.send(uid, instance.title or 'ZEO', (instance.message or '')[:400], {'route': '/m/home', 'kind': 'notification'},
                  kind='notification', schema=instance.schema_name or '')
    except Exception:
        log.exception('push inbox')


# ---------------------------------------------------------------- approvals and decisions
def _registry():
    from DashboardManagement.services import REQUESTS
    return REQUESTS


def approval_saved(sender, instance, created, raw=False, **kw):
    if not created or raw:
        return
    try:
        if str(getattr(instance, 'status', '')).lower() != 'pending':
            return
        uid = getattr(instance, 'approver_id', None)
        if getattr(instance, 'is_deligate', False) and getattr(instance, 'deligate_to_id', None):
            uid = instance.deligate_to_id
        if not uid:
            return
        for key, label, rm, am, fk, dfield, summ in _registry():
            if am.split('.')[1].lower() == sender.__name__.lower():
                r = getattr(instance, fk, None)
                e = getattr(r, 'employee', None) if r is not None else None
                name = ' '.join(x for x in [getattr(e, 'emp_first_name', '') or '', getattr(e, 'emp_last_name', '') or ''] if x).strip()
                try:
                    text = summ(r) if r is not None else ''
                except Exception:
                    text = ''
                schema = _schema()
                _push(uid, 'approval', '{0} to approve', f'{name} · {text}'.strip(' ·'), '/m/approvals', schema,
                      _lang_t(uid, schema, label))
                return
    except Exception:
        log.exception('push approval')


def request_pre_save(sender, instance, raw=False, **kw):
    if raw or not instance.pk:
        return
    try:
        old = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
        instance._zeo_old_status = old
    except Exception:
        instance._zeo_old_status = None


def request_saved(sender, instance, created, raw=False, **kw):
    if created or raw:
        return
    old = str(getattr(instance, '_zeo_old_status', '') or '').lower()
    new = str(getattr(instance, 'status', '') or '').lower()
    if old == new or new not in ('approved', 'rejected'):
        return
    try:
        e = getattr(instance, 'employee', None)
        uid = getattr(e, 'users_id', None) if e is not None else None
        if not uid:
            return
        for key, label, rm, am, fk, dfield, summ in _registry():
            if rm.split('.')[1].lower() == sender.__name__.lower():
                try:
                    text = summ(instance)
                except Exception:
                    text = ''
                schema = _schema()
                title = 'Your {0} request was approved' if new == 'approved' else 'Your {0} request was rejected'
                _push(uid, 'decision', title, text, '/m/requests', schema, _lang_t(uid, schema, label).lower())
                return
    except Exception:
        log.exception('push decision')


# ---------------------------------------------------------------- payslips
RELEASED = ('approved', 'paid')


def payslip_pre_save(sender, instance, raw=False, **kw):
    request_pre_save(sender, instance, raw=raw)


def payslip_saved(sender, instance, created, raw=False, **kw):
    if raw:
        return
    old = str(getattr(instance, '_zeo_old_status', '') or '').lower()
    new = str(getattr(instance, 'status', '') or '').lower()
    if new not in RELEASED or old in RELEASED:
        return
    try:
        e = getattr(instance, 'employee', None)
        uid = getattr(e, 'users_id', None) if e is not None else None
        if not uid:
            return
        r = getattr(instance, 'payroll_run', None)
        period = f'{r.get_month_display()} {r.year}' if r is not None else ''
        _push(uid, 'payslip', 'Your payslip for {0} is ready', '', '/m/payslips', _schema(), period)
    except Exception:
        log.exception('push payslip')


def connect():
    global _done
    if _done:
        return
    _done = True
    from django.apps import apps
    try:
        Inbox = apps.get_model('UserManagement', 'UserNotificationInbox')
        post_save.connect(inbox_saved, sender=Inbox, dispatch_uid='zeomobile_inbox')
    except Exception:
        log.exception('inbox push signal')
    try:
        for key, label, rm, am, fk, dfield, summ in _registry():
            try:
                AM = apps.get_model(*am.split('.'))
                post_save.connect(approval_saved, sender=AM, dispatch_uid=f'zeomobile_appr_{am}')
                RM = apps.get_model(*rm.split('.'))
                pre_save.connect(request_pre_save, sender=RM, dispatch_uid=f'zeomobile_reqpre_{rm}')
                post_save.connect(request_saved, sender=RM, dispatch_uid=f'zeomobile_req_{rm}')
            except LookupError:
                continue
    except Exception:
        log.exception('approval push signals')
    try:
        PS = apps.get_model('PayrollManagement', 'Payslip')
        pre_save.connect(payslip_pre_save, sender=PS, dispatch_uid='zeomobile_payslip_pre')
        post_save.connect(payslip_saved, sender=PS, dispatch_uid='zeomobile_payslip')
    except Exception:
        log.exception('payslip push signal')
