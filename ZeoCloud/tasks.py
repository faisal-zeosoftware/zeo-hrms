"""v1.23.0 – daily ZEO Cloud reminders to account owners: 30, 14 and 7 days before the renewal (or the trial end), the first
day of the grace period, and the day the account becomes read-only."""
import logging

from celery import shared_task

log = logging.getLogger(__name__)


def run_daily(today=None):
    from django.core.mail import send_mail
    from django.utils import timezone
    from .models import Account
    from .services import cloud_on, event, status_of, tr
    if not cloud_on():
        return 0
    d = today or timezone.localdate()
    sent = 0
    for acc in Account.objects.exclude(state='cancelled').select_related('plan'):
        st = status_of(acc, d)
        msg = None
        if st['status'] in ('trial', 'active') and st['days_left'] in (30, 14, 7, 1):
            what = tr('Your ZEO free trial ends on {0}.', st['until']) if st['status'] == 'trial' else tr('Your ZEO subscription renews on {0}.', st['until'])
            msg = (tr('ZEO – {0} days left', st['days_left']), what + ' ' + tr('Adept sends the invoice; once it is paid nothing changes for your users.'))
        elif st['status'] == 'grace' and st['days_left'] == acc.grace_days - 1:
            msg = (tr('ZEO – grace period started'), tr('The renewal date has passed. Everything still works for {0} days; after that ZEO becomes read-only until the invoice is paid.', st['days_left']))
        elif st['status'] == 'suspended' and st.get('expired') and st['until'] == (d - __import__('datetime').timedelta(days=1)).isoformat():
            msg = (tr('ZEO – read-only'), tr('Your ZEO account {0} is read-only: users can view, export and print. Nothing is deleted. Pay the invoice to continue.', acc.name))
        if msg and acc.owner_email:
            send_mail(msg[0], msg[1], None, [acc.owner_email], fail_silently=True)
            event(acc, 'reminder', f'E-mail to the owner: {msg[0]}')
            sent += 1
    # v1.24.0: old copies of moved companies are removed after 7 days; shared rows refreshed in own databases
    try:
        from . import owndb
        from .models import DatabaseServer
        owndb.drop_old_copies(d)
        owndb.drop_retired(d)
        for srv in DatabaseServer.objects.filter(state='ready'):
            owndb.sync_shared(srv.key)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('own databases daily')
    return sent


@shared_task
def daily():
    try:
        return run_daily()
    except Exception:
        log.exception('ZEO Cloud daily')
