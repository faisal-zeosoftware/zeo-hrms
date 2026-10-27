"""v1.52.0 – out of office: between two dates the approvals of a user go to a colleague.

* the approvals already waiting are handed over when the period starts (when include_pending), the new ones as they are
  made – on every approval type of HRMS (leave, late in / early out, general and document requests, assets, loans,
  advance salary, air tickets, resignations) and the Automation approval steps;
* the approver stays the approver: the colleague acts for them (the existing delegation of each approval), and when the
  period ends the approvals not decided yet stay with the colleague.
"""
import logging
from datetime import date

from django.apps import apps
from django.db.models import Q

log = logging.getLogger(__name__)


def approval_models():
    out = []
    try:
        from DashboardManagement.services import REQUESTS
        for key, label, rm, am, fk, dfield, summ in REQUESTS:
            try:
                out.append((key, label, apps.get_model(*am.split('.'))))
            except LookupError:
                continue
    except Exception:
        log.exception('approval registry')
    return out


def current(user_id, day=None):
    from .models import OutOfOffice
    day = day or date.today()
    return OutOfOffice.objects.filter(user_id=user_id, active=True, start_date__lte=day, end_date__gte=day).select_related('delegate').first()


def _note(o):
    return f'Out of office {o.start_date:%d/%m} – {o.end_date:%d/%m}' + (f': {o.note}' if o.note else '')


def hand_over(o, only_ids=None):
    """Delegate the waiting approvals of o.user to o.delegate. Returns the number handed over."""
    n = 0
    for key, label, M in approval_models():
        try:
            qs = M.objects.filter(approver_id=o.user_id, status__iexact='pending', is_deligate=False)
            if only_ids is not None:
                qs = qs.filter(pk__in=only_ids)
            n += qs.update(deligate_to_id=o.delegate_id, is_deligate=True, deligate_response=_note(o)[:500])
        except Exception:
            log.exception('out of office %s', key)
    try:
        Step = apps.get_model('ZeoFlow', 'ApprovalStep')
        qs = Step.objects.filter(approver_id=o.user_id, status__in=('waiting', 'pending'), delegate__isnull=True)
        if only_ids is None:
            n += qs.update(delegate_id=o.delegate_id)
    except LookupError:
        pass
    except Exception:
        log.exception('out of office flow steps')
    return n


def start(o, user=None):
    """The period starts (today within it): hand over what is waiting and tell the colleague."""
    if not o.active or not (o.start_date <= date.today() <= o.end_date):
        return 0
    n = hand_over(o) if o.include_pending else 0
    if n:
        o.moved = (o.moved or 0) + n
        o.save(update_fields=['moved'])
    try:
        from zeo.module_helpers import notify
        who = o.user.get_full_name() or o.user.username
        notify(user=o.delegate, title=f'You approve for {who}',
               message=f'{who} is out of office from {o.start_date:%d/%m/%Y} to {o.end_date:%d/%m/%Y}; their approvals come to you'
                       + (f' ({n} waiting now).' if n else '.'))
    except Exception:
        pass
    return n


def on_new_approval(instance):
    """post_save of a new approval: its approver is out of office today → the colleague gets it."""
    uid = getattr(instance, 'approver_id', None)
    if not uid or getattr(instance, 'is_deligate', False) or str(getattr(instance, 'status', '')).lower() != 'pending':
        return
    o = current(uid)
    if o is None or o.delegate_id == uid:
        return
    type(instance).objects.filter(pk=instance.pk).update(deligate_to_id=o.delegate_id, is_deligate=True, deligate_response=_note(o)[:500])
    type(o).objects.filter(pk=o.pk).update(moved=(o.moved or 0) + 1)


def on_new_step(instance):
    uid = getattr(instance, 'approver_id', None)
    if not uid or instance.delegate_id or instance.status not in ('waiting', 'pending'):
        return
    o = current(uid)
    if o is not None and o.delegate_id != uid:
        type(instance).objects.filter(pk=instance.pk).update(delegate_id=o.delegate_id)


def daily():
    """Periods starting today hand over what is waiting."""
    from .models import OutOfOffice
    t = date.today()
    n = 0
    for o in OutOfOffice.objects.filter(active=True, start_date=t).select_related('user', 'delegate'):
        n += start(o)
    return n


def check(user, delegate, a, b, exclude_id=None):
    from .models import OutOfOffice
    if delegate is None:
        return 'Choose the colleague who approves for you.'
    if delegate.pk == user.pk:
        return 'Choose someone else.'
    if a is None or b is None or b < a:
        return 'Give the first and the last day (the last on or after the first).'
    clash = OutOfOffice.objects.filter(user=user, active=True, start_date__lte=b, end_date__gte=a).exclude(pk=exclude_id or 0)
    if clash.exists():
        return 'You already have an out-of-office period on these days.'
    loop = OutOfOffice.objects.filter(user=delegate, delegate=user, active=True, start_date__lte=b, end_date__gte=a)
    if loop.exists():
        return 'This colleague is out of office on these days and hands over to you.'
    return ''
