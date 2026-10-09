"""
AssetPlus signals (v1.12.0) on the existing tables – no change to their models:

* Asset          – a profile with the next asset code for every new asset; "created" / "edited" history (old screens too)
* AssetAllocation – status guard: an asset under maintenance, lost or disposed cannot be allocated (also from the old
                    screens and the asset request approval); "allocated" / "returned" history; acknowledgement row
* AssetRequest   – a lost asset cannot be requested; "requested" / "approved" / "rejected" history
* PayslipComponent / Payslip – instalments of asset_recovery_amount are marked deducted (and back when the payslip is deleted)
* EndOfService   – final settlement cannot be processed / paid while the employee still has assets (exit clearance)
"""
import logging

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

log = logging.getLogger(__name__)

ASSET_FIELDS = {'name': 'Name', 'serial_number': 'Serial number', 'model': 'Model', 'purchase_date': 'Purchase date',
                'status': 'Status', 'condition': 'Condition', 'asset_type_id': 'Asset type'}


def _tenant():
    return connection.schema_name != 'public'


_READY = {}


def _ready():
    """AssetPlus tables exist in this company (checked once per schema, without breaking the open transaction)."""
    sch = connection.schema_name
    if sch not in _READY:
        try:
            with connection.cursor() as cur:
                cur.execute("select to_regclass(%s)", [f'"{sch}"."AssetPlus_assetprofile"'])
                ok = cur.fetchone()[0] is not None
        except Exception:
            return False
        if not ok:
            return False  # not migrated yet: checked again next time
        _READY[sch] = True
    return True


@receiver(pre_save, sender='OrganisationManager.Asset')
def _asset_before(sender, instance, **kwargs):
    instance._ap_old = None
    if instance.pk and _tenant():
        instance._ap_old = sender.objects.filter(pk=instance.pk).values(*ASSET_FIELDS.keys()).first()


@receiver(post_save, sender='OrganisationManager.Asset')
def _asset_after(sender, instance, created, **kwargs):
    if not _tenant() or not _ready():
        return
    from . import services as S
    try:
        if created:
            p = S.ensure_profile(instance)
            S.log_event(instance.pk, 'created', f'Asset created – {instance.name}, serial {instance.serial_number}, code {p.asset_code}')
            return
        old = getattr(instance, '_ap_old', None)
        if not old or S.is_quiet():
            return
        ch = {}
        for f, label in ASSET_FIELDS.items():
            o, n = old.get(f), getattr(instance, f)
            if str(o or '') != str(n or ''):
                ch[f] = {'label': label, 'old': str(o) if o is not None else '', 'new': str(n) if n is not None else ''}
        if ch:
            S.log_event(instance.pk, 'edited', 'Changed ' + ', '.join(f"{v['label'].lower()} {v['old'] or '–'} → {v['new'] or '–'}" for v in ch.values()),
                        changes=ch)
    except Exception:
        log.exception('asset history failed')


@receiver(pre_save, sender='OrganisationManager.AssetAllocation')
def _allocation_before(sender, instance, **kwargs):
    if not _tenant() or not _ready():
        return
    from . import services as S
    instance._ap_old_returned = None
    if instance.pk:
        instance._ap_old_returned = sender.objects.filter(pk=instance.pk).values_list('returned_date', flat=True).first()
        return
    if instance.returned_date:
        return
    asset = type(instance.asset).objects.get(pk=instance.asset_id)
    st = S.effective_status(asset)
    if st in ('maintenance', 'disposed', 'lost'):
        raise ValidationError({'asset': [S.BLOCK_MSG[st]]})
    if S.open_shared(asset.pk):
        raise ValidationError({'asset': ['This asset is allocated to a location / department. Return or transfer it first.']})


@receiver(post_save, sender='OrganisationManager.AssetAllocation')
def _allocation_after(sender, instance, created, **kwargs):
    if not _tenant() or not _ready() :
        return
    from . import services as S
    from .models import AllocationExtra, AssetPlusSettings
    try:
        if created:
            if S.is_quiet():
                return
            AllocationExtra.objects.get_or_create(allocation_id=instance.pk, defaults=dict(
                asset_id=instance.asset_id, employee_id=instance.employee_id, handover_condition=instance.asset.condition,
                ack_status='pending' if AssetPlusSettings.get().require_acknowledgement else 'not_required'))
            S.ensure_profile(instance.asset)
            S.log_event(instance.asset_id, 'allocated', f'Given to {S.person(instance.employee)}'
                        + (f' on {instance.assigned_date:%d %b %Y}' if hasattr(instance.assigned_date, 'strftime') else ''),
                        employee_id=instance.employee_id, ref=('allocation', instance.pk))
            return
        if S.is_quiet():
            return
        if instance.returned_date and not getattr(instance, '_ap_old_returned', None):
            S.log_event(instance.asset_id, 'returned', f'Returned by {S.person(instance.employee)}'
                        + (f', condition {instance.return_condition.replace("_", " ")}' if instance.return_condition else ''),
                        employee_id=instance.employee_id, ref=('allocation', instance.pk))
    except Exception:
        log.exception('allocation history failed')


@receiver(pre_save, sender='OrganisationManager.AssetRequest')
def _request_before(sender, instance, **kwargs):
    if not _tenant() or not _ready():
        return
    from . import services as S
    instance._ap_old_status = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first() if instance.pk else None
    if not instance.pk and instance.requested_asset_id:
        asset = type(instance.requested_asset).objects.get(pk=instance.requested_asset_id)
        st = S.effective_status(asset)
        if st in ('disposed', 'lost', 'maintenance'):
            raise ValidationError({'requested_asset': [S.BLOCK_MSG[st].replace('given out', 'requested')]})


@receiver(post_save, sender='OrganisationManager.AssetRequest')
def _request_after(sender, instance, created, **kwargs):
    if not _tenant() or not _ready() or not instance.requested_asset_id:
        return
    from . import services as S
    try:
        if created:
            S.log_event(instance.requested_asset_id, 'requested', f'Requested by {S.person(instance.employee)}'
                        + (f' ({instance.document_number})' if instance.document_number else '') + f': {instance.reason}',
                        employee_id=instance.employee_id, ref=('request', instance.pk))
            return
        old = (getattr(instance, '_ap_old_status', '') or '').lower()
        new = (instance.status or '').lower()
        if new != old and new in ('approved', 'rejected'):
            S.log_event(instance.requested_asset_id, f'request_{new}', f'Request {instance.document_number or instance.pk} {new}',
                        employee_id=instance.employee_id, ref=('request', instance.pk))
    except Exception:
        log.exception('asset request history failed')


# ------------------------------------------------------------------ payroll recovery
def _is_recovery(component):
    return 'asset_recovery_amount' in (getattr(component, 'formula', None) or '')


@receiver(post_save, sender='PayrollManagement.PayslipComponent')
def _payslip_component_saved(sender, instance, created, **kwargs):
    if not _tenant() or not _ready() or not _is_recovery(instance.component):
        return
    try:
        from . import services as S
        if instance.amount and instance.amount > 0:
            S.mark_deducted(instance.payslip, instance.amount)
    except Exception:
        log.exception('asset recovery marking failed')


@receiver(post_delete, sender='PayrollManagement.PayslipComponent')
def _payslip_component_deleted(sender, instance, **kwargs):
    if not _tenant() or not _ready() or not _is_recovery(getattr(instance, 'component', None)):
        return
    from . import services as S
    S.unmark_payslip(instance.payslip_id)


@receiver(post_delete, sender='PayrollManagement.Payslip')
def _payslip_deleted(sender, instance, **kwargs):
    if not _tenant() or not _ready():
        return
    from . import services as S
    S.unmark_payslip(instance.pk)


# ------------------------------------------------------------------ exit clearance
@receiver(pre_save, sender='EmpManagement.EndOfService')
def _eos_before(sender, instance, **kwargs):
    if not _tenant() or not _ready():
        return
    from . import services as S
    old = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first() if instance.pk else None
    S.eos_guard(instance, old)
