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
