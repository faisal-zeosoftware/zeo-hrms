"""v1.62.0 – bank-detail changes need a second person (README-UPDATE 83.6) – the most common payroll fraud is an IBAN quietly
changed to another account before the salary run.

With Settings › Sign-in security › "Bank-detail changes need a second person" on:
  * an HR user who adds, changes (bank, account number, IBAN, routing code) or removes an employee's bank account does not
    change it: a pending BankChange is written and the screen says it waits for a second person (409 "bank_change_pending");
  * another user – a company administrator or a holder of the right "Can approve bank-detail changes", never the person who
    asked – approves it (it is applied then) or rejects it: Employees › Bank-detail changes;
  * the employee is told by e-mail (and in the ZEO inbox) every time their bank details change.
Not held: a change the employee asked for in self service and HR approved (already two people), an employee's own change
where the company lets employees change their bank details themselves, and scripts / imports run on the server.
"""
import logging
import threading

from django.db import connection
from django.db.models.signals import post_save, pre_delete, pre_save
from django.utils import timezone
from rest_framework.exceptions import APIException

log = logging.getLogger(__name__)
FIELDS = ('bank_name', 'account_number', 'iban_number', 'route_code')
ALL = ('bank_name', 'branch_name', 'account_number', 'bank_address', 'route_code', 'iban_number', 'is_active')
_tl = threading.local()


class BankChangePending(APIException):
    status_code = 409
    default_code = 'bank_change_pending'
    default_detail = 'The bank-detail change was saved for approval by a second person. It applies when it is approved.'


class bypass:
    def __enter__(self):
        _tl.on = getattr(_tl, 'on', 0) + 1

    def __exit__(self, *a):
        _tl.on -= 1


def _on():
    return getattr(_tl, 'on', 0) > 0


def policy_on(schema=None):
    schema = schema or getattr(connection, 'schema_name', 'public')
    if not schema or schema == 'public':
        return False
    from django.core.cache import cache
    k = f'zeo:bankpol:{schema}'
    v = cache.get(k)
    if v is None:
        from django_tenants.utils import schema_context
        from ZeoCloud.models import SecurityPolicy
        with schema_context('public'):
            v = bool(SecurityPolicy.objects.filter(schema=schema).values_list('bank_two_person', flat=True).first())
        cache.set(k, v, 30)
    return v


def forget(schema):
    from django.core.cache import cache
    cache.delete(f'zeo:bankpol:{schema}')


def _request():
    try:
        from Chatter.tracking import current_request
        return current_request()
    except Exception:
        return None


def _actor(r):
    u = getattr(r, 'user', None) if r is not None else None
    if u is not None and getattr(u, 'is_authenticated', False):
        return u
    # the API (JWT) user before DRF has set request.user
    try:
        h = r.META.get('HTTP_AUTHORIZATION', '') if r is not None else ''
        if h.startswith('Bearer '):
            from django.contrib.auth import get_user_model
            from rest_framework_simplejwt.tokens import UntypedToken
            return get_user_model().objects.filter(pk=UntypedToken(h[7:].strip()).get('user_id')).first()
    except Exception:
        pass
    return None


def _vals(obj):
    return {f: (getattr(obj, f, None) if f != 'is_active' else bool(getattr(obj, f, True))) for f in ALL}


def _held(r, actor, emp_id):
    """Should this change wait for a second person?"""
    if _on() or not policy_on() or r is None or actor is None:
        return False
    if (r.path or '').startswith('/self-service/'):
        return False   # an employee's request approved by HR
    from EmpManagement.models import emp_master
    if emp_master.objects.filter(pk=emp_id, users=actor).exists():
        return False   # the employee's own change (company lets them)
    return True


def _pending(instance, action, old, new, actor):
    from EmpManagement.models import emp_master
    from .models import BankChange
    emp = emp_master.objects.filter(pk=instance.employee_id).first()
    same = BankChange.objects.filter(state='pending', employee_id=instance.employee_id, model='EmpManagement.EmployeeBankDetail',
                                     object_id=str(instance.pk or ''), action=action, new=new).first()
    ch = same or BankChange.objects.create(employee_id=instance.employee_id, emp_code=getattr(emp, 'emp_code', '') or '',
                                           model='EmpManagement.EmployeeBankDetail', object_id=str(instance.pk or ''), action=action,
                                           old=old, new=new, requested_by=actor)
    try:
        from ZeoCloud.security import event
        event('bank_change', user=actor, by=actor, schema=connection.schema_name,
              detail=f'Bank-detail change for {getattr(emp, "emp_code", "")} ({action}) waits for a second person.', data={'change': ch.pk})
    except Exception:
        pass
    from Translations.i18n import tr
    raise BankChangePending(tr(BankChangePending.default_detail))


def _jsonable(d):
    return {k: (str(v) if v is not None and not isinstance(v, (bool, int, float, str)) else v) for k, v in d.items()}


def _pre_save(sender, instance, raw=False, **kw):
    if raw:
        return
    r = _request()
    actor = _actor(r)
    if not _held(r, actor, instance.employee_id):
        instance._zeo_bank_old = sender.objects.filter(pk=instance.pk).values(*FIELDS).first() if instance.pk else None
        return
    if instance.pk:
        old_obj = sender.objects.filter(pk=instance.pk).first()
        if old_obj is None:
            return
        old = _vals(old_obj)
        if all(str(old[f] or '') == str(getattr(instance, f, '') or '') for f in FIELDS):
            instance._zeo_bank_old = None
            return   # only the address / branch name changed – not held
        _pending(instance, 'update', _jsonable(old), _jsonable(_vals(instance)), actor)
    else:
        _pending(instance, 'create', {}, _jsonable(_vals(instance)), actor)


def _pre_delete(sender, instance, **kw):
    r = _request()
    actor = _actor(r)
    if _held(r, actor, instance.employee_id):
        _pending(instance, 'delete', _jsonable(_vals(instance)), {}, actor)


def _post_save(sender, instance, created=False, raw=False, **kw):
    if raw or not policy_on():
        return
    old = getattr(instance, '_zeo_bank_old', None)
    instance._zeo_bank_old = None
    if not created and (old is None or all(str(old.get(f) or '') == str(getattr(instance, f, '') or '') for f in FIELDS)):
        return
    tell_employee(instance.employee_id, getattr(instance, 'iban_number', '') or getattr(instance, 'account_number', ''), _actor(_request()))


def tell_employee(emp_id, number, by=None):
    """E-mail (and ZEO inbox) to the employee: their bank details changed."""
    try:
        from django.conf import settings
        from django.core.mail import send_mail
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(pk=emp_id).select_related('users').first()
        if e is None:
            return
        last4 = str(number or '')[-4:]
        who = (by.get_full_name() or by.username) if by is not None else 'HR'
        when = timezone.localtime().strftime('%d %b %Y %H:%M')
        text = (f'Dear {e.emp_first_name or ""},\n\nYour bank details in ZEO were changed on {when} by {who}. Your salary will be paid to '
                f'the account ending {last4 or "(none)"}.\n\nIf you did not ask for this change, contact HR at once.')
        to = e.emp_personal_email or e.emp_company_email
        if to:
            send_mail('ZEO: your bank details were changed', text, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [to], fail_silently=True)
        if e.users_id:
            from Chatter.models import InboxItem
            InboxItem.objects.create(user_id=e.users_id, kind='message', title='Your bank details were changed',
                                     body=text[:2000], from_name=who[:150], page='/self-service/profile')
    except Exception:
        log.exception('bank change notice')


def decide(change, user, approve, note=''):
    """Apply (approve) or reject a pending change. Raises ValueError with a reason."""
    from AccessControl.access import ctx
    if change.state != 'pending':
        raise ValueError('This change was already decided.')
    if change.requested_by_id == user.pk:
        raise ValueError('A second person must decide – not the one who made the change.')

    class _R:
        def __init__(s, u):
            s.user, s.GET, s.META, s.path, s.method = u, {}, {}, '', 'GET'
    c = ctx(_R(user))
    if not (c.admin or 'approve_bank_change' in c.codes):
        raise ValueError('You need the right "Can approve bank-detail changes".')
    from EmpManagement.models import EmployeeBankDetail
    change.decided_by, change.decided_at, change.note = user, timezone.now(), (note or '')[:300]
    if not approve:
        change.state = 'rejected'
        change.save()
        return change
    with bypass():
        new = change.new or {}
        if change.action == 'create':
            obj = EmployeeBankDetail(employee_id=change.employee_id, **{k: v for k, v in new.items() if k in ALL})
            obj.full_clean(exclude=['employee'])
            obj.save()
            change.object_id = str(obj.pk)
        elif change.action == 'update':
            obj = EmployeeBankDetail.objects.filter(pk=change.object_id).first()
            if obj is None:
                raise ValueError('The bank account no longer exists.')
            for k, v in new.items():
                if k in ALL:
                    setattr(obj, k, v)
            obj.full_clean(exclude=['employee'])
            obj.save()
        else:
            EmployeeBankDetail.objects.filter(pk=change.object_id).delete()
    change.state = 'approved'
    change.save()
    if change.action == 'delete':
        tell_employee(change.employee_id, '', change.requested_by)
    else:
        tell_employee(change.employee_id, new.get('iban_number') or new.get('account_number'), change.requested_by)
    return change


def change_json(c):
    def n(u):
        return (u.get_full_name() or u.username) if u is not None else ''
    from AccessControl.respfilter import mask_value
    mask = lambda d: {k: (mask_value(v) if k in ('account_number', 'iban_number') and v else v) for k, v in (d or {}).items()}   # noqa: E731
    return {'id': c.id, 'employee_id': c.employee_id, 'emp_code': c.emp_code, 'action': c.action, 'state': c.state,
            'old': mask(c.old), 'new': mask(c.new), 'old_full': c.old, 'new_full': c.new, 'requested_by': n(c.requested_by),
            'requested_by_id': c.requested_by_id, 'requested_at': c.requested_at, 'decided_by': n(c.decided_by), 'decided_at': c.decided_at,
            'note': c.note}


def install():
    try:
        from EmpManagement.models import EmployeeBankDetail
    except Exception:
        return
    pre_save.connect(_pre_save, sender=EmployeeBankDetail, dispatch_uid='zeo62-bank-pre')
    post_save.connect(_post_save, sender=EmployeeBankDetail, dispatch_uid='zeo62-bank-post')
    pre_delete.connect(_pre_delete, sender=EmployeeBankDetail, dispatch_uid='zeo62-bank-del')
