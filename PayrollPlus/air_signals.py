"""v1.66.0 (item 120) – partial use of a family air ticket: when a ticket request with recorded travellers is approved, their fares are
taken off the allocation (the rest stays for the others of the family); a rejection after that gives them back."""
import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


@receiver(post_save, sender='PayrollManagement.AirTicketRequest')
def _v1660_ticket_travel(sender, instance, created=False, **kw):   # v1.66.0 (item 120)
    if instance.request_type != 'TICKET':
        return
    try:
        from . import air_policy as AP
        AP.on_request_status(instance)
    except Exception:   # never stop the approval itself
        log.exception('air ticket travellers of request %s', instance.pk)


# v1.67.12 (effective, item 63): the end of service record shows the unused part of the air ticket – also after a ticket / cash request
# or the allocation changes once the record exists (before, the amount stayed as it was when the record was made)
def _v16712_refresh_eos(emp_id):
    from django.apps import apps
    EOS = apps.get_model('EmpManagement', 'EndOfService')
    from . import air_policy as AP
    for e in EOS.objects.filter(resignation__employee_id=emp_id).exclude(status='paid').select_related('resignation__employee'):
        amt = AP.unused_at_exit(e.resignation.employee, e.last_working_date)[0]
        if e.air_ticket != amt:
            EOS.objects.filter(pk=e.pk).update(air_ticket=amt)


@receiver(post_save, sender='PayrollManagement.AirTicketRequest', dispatch_uid='v16712_eos_ticket_req')
def _v16712_request_saved(sender, instance, **kw):
    try:
        _v16712_refresh_eos(instance.employee_id)
    except Exception:
        log.exception('end of service air ticket after request %s', instance.pk)


@receiver(post_save, sender='PayrollManagement.AirTicketAllocation', dispatch_uid='v16712_eos_ticket_alloc')
def _v16712_allocation_saved(sender, instance, **kw):
    try:
        _v16712_refresh_eos(instance.employee_id)
    except Exception:
        log.exception('end of service air ticket after allocation %s', instance.pk)
