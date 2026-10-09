"""v1.12.0 – shift reminders: the day before a published shift every employee gets a notice (in-app + e-mail).
Celery beat entry (zeo/settings.py, see INTEGRATION.md): 'shift-reminders-daily' → ShiftPlanner.tasks.shift_reminders."""
import logging
from datetime import date, timedelta

from celery import shared_task
from django_tenants.utils import get_tenant_model, schema_context

log = logging.getLogger(__name__)


def send_reminders(on=None):
    """Remind everybody who works a published-roster shift on `on` (default tomorrow). Returns how many were sent."""
    from . import notify, services as S
    from .models import RosterEntry
    on = on or (date.today() + timedelta(days=1))
    shifts = S.shifts_by_id()
    sent = 0
    seen = set()
    for e in RosterEntry.objects.filter(date=on, period__status='published', off=False, shift_id__isnull=False).order_by('-period__published_at'):
        if e.employee_id in seen:
            continue
        seen.add(e.employee_id)
        emp = S.emp_by_id(e.employee_id)
        if emp is None or S.leave_on(emp.pk, on):
            continue
        n = notify.send(employee=emp, kind='reminder', ref=f'reminder:{on.isoformat()}', title='Shift reminder',
                        message=f'Reminder: tomorrow ({on:%a %d %b}) you work {S.shift_label(shifts.get(e.shift_id))}.')
        sent += 1 if n is not None else 0
    return sent


@shared_task
def shift_reminders():
    out = {}
    for t in get_tenant_model().objects.exclude(schema_name='public').values_list('schema_name', flat=True):
        try:
            with schema_context(t):
                out[t] = send_reminders()
        except Exception:
            log.exception('shift reminders failed for %s', t)
    return out
