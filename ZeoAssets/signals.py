"""Keep the asset cards in step with HR → Assets. Every HR asset change writes an AssetPlus event, so the card is
refreshed from there; new HR assets get their card at once. Nothing happens until Asset Management is activated."""
import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


def _later(asset_id):
    from . import services as SV
    if SV.is_busy() or not asset_id or not SV.ready():
        return

    def run():
        try:
            SV.refresh(asset_id=asset_id)
        except Exception:
            log.exception('asset card refresh failed for HR asset %s', asset_id)
    transaction.on_commit(run)


@receiver(post_save, sender='AssetPlus.AssetEvent', dispatch_uid='zeoassets_hr_event')
def hr_event(sender, instance, created, raw=False, **kw):
    if not raw:
        _later(instance.asset_id)


@receiver(post_save, sender='OrganisationManager.Asset', dispatch_uid='zeoassets_hr_asset')
def hr_asset(sender, instance, raw=False, **kw):
    if not raw:
        _later(instance.pk)


@receiver(post_save, sender='AssetPlus.AssetProfile', dispatch_uid='zeoassets_hr_profile')
def hr_profile(sender, instance, raw=False, **kw):
    if not raw:
        _later(instance.asset_id)


@receiver(post_save, sender='OrganisationManager.AssetAllocation', dispatch_uid='zeoassets_hr_alloc')
def hr_allocation(sender, instance, raw=False, **kw):
    if not raw:
        _later(instance.asset_id)


@receiver(post_save, sender='AssetPlus.SharedAllocation', dispatch_uid='zeoassets_hr_shared')
def hr_shared(sender, instance, raw=False, **kw):
    if not raw:
        _later(instance.asset_id)
