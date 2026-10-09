"""v1.12.0 – shift notices: a ShiftNotice row (shown in My schedule), the company in-app inbox and an e-mail. Never raises."""
import logging

from django.conf import settings
from django.core.mail import send_mail

from .models import ShiftNotice

log = logging.getLogger(__name__)


def _user_of(emp):
    return getattr(emp, 'users', None) if emp is not None else None


def send(*, employee=None, user=None, kind='general', title='', message='', ref=''):
    """Notify an employee (their login) or a user. `ref` makes the notice unique (reminders)."""
    try:
        if user is None:
            user = _user_of(employee)
        if ref and ShiftNotice.objects.filter(ref=ref, user_id=getattr(user, 'pk', None), employee_id=getattr(employee, 'pk', None)).exists():
            return None
        n = ShiftNotice.objects.create(user_id=getattr(user, 'pk', None), employee_id=getattr(employee, 'pk', None), kind=kind,
                                       title=title[:120], message=message, ref=ref[:60])
    except Exception:
        log.exception('shift notice not stored')
        return None
    try:  # company in-app inbox (bell)
        from zeo.module_helpers import notify
        notify(user=user, employee=employee, title=title[:100], message=message, notification_type='shift')
    except Exception:
        log.debug('in-app shift notice skipped', exc_info=True)
    to = [x for x in {getattr(user, 'email', None), getattr(employee, 'emp_company_email', None)} if x]
    if to:
        try:
            send_mail(title, message, getattr(settings, 'DEFAULT_FROM_EMAIL', None) or getattr(settings, 'EMAIL_HOST_USER', None) or 'noreply@zeo-hrms.local',
                      sorted(to), fail_silently=False)
            n.emailed = True
            n.save(update_fields=['emailed'])
        except Exception:
            log.debug('shift e-mail not sent', exc_info=True)
    return n
