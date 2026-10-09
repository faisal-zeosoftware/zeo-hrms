"""AssetPlus business rules (v1.12.0). Views call these; every change writes one AssetEvent."""
import contextvars
import logging
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from .models import (ZERO, AllocationExtra, AssetDamage, AssetDisposal, AssetEvent, AssetLoss, AssetPlusSettings, AssetProfile,
                     AssetReturn, AssetTransfer, AssetTypeConfig, ClearanceWaiver, MaintenanceRecord, MaintenanceSchedule,
                     RecoveryInstalment, SharedAllocation)

log = logging.getLogger(__name__)
CENT = Decimal('0.01')


class AssetError(Exception):
    def __init__(self, message, field=None, status=400):
        super().__init__(message)
        self.message, self.field, self.status = message, field, status


def M(app, name):
    return apps.get_model(app, name)


def Asset():
    return M('OrganisationManager', 'Asset')


def Allocation():
    return M('OrganisationManager', 'AssetAllocation')


def Emp():
    return M('EmpManagement', 'emp_master')


def money(v):
    return Decimal(str(v or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def today():
    return timezone.localdate()


def person(e):
    if e is None:
        return ''
    name = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x and str(x).strip())
    return f'{name} ({e.emp_code})' if name else e.emp_code


def emp(emp_id):
    if not emp_id:
        return None
    return Emp().objects.filter(pk=emp_id).select_related('emp_branch_id', 'emp_dept_id').first()


def emp_name(emp_id):
    return person(emp(emp_id))


def user_label(user_id):
    if not user_id:
        return ''
    u = M('UserManagement', 'CustomUser').objects.filter(pk=user_id).first()
    return (u.get_full_name() or u.username) if u else ''


def branch_name(bid):
    if not bid:
        return ''
    b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=bid).first()
    return b.branch_name if b else ''


def dept_name(did):
    if not did:
        return ''
    d = M('OrganisationManager', 'dept_master').objects.filter(pk=did).first()
    return d.dept_name if d else ''


# ------------------------------------------------------------------ history
_quiet = contextvars.ContextVar('assetplus_quiet', default=False)


@contextmanager
def quiet():
    """Inside: the base-table signals do not write events (the service writes a richer one itself)."""
    tok = _quiet.set(True)
    try:
        yield
    finally:
        _quiet.reset(tok)


def is_quiet():
    return _quiet.get()


def _current_user():
    try:
        from Chatter.tracking import current_request
        r = current_request()
        u = getattr(r, 'user', None) if r is not None else None
        return u if u is not None and getattr(u, 'is_authenticated', False) else None
    except Exception:
        return None


def log_event(asset_id, event, summary, user=None, employee_id=None, ref=None, changes=None):
    u = user or _current_user()
    return AssetEvent.objects.create(
        asset_id=asset_id, event=event, summary=summary[:400], changes=changes or {}, employee_id=employee_id,
        ref_type=(ref[0] if ref else ''), ref_id=(ref[1] if ref else None),
        user_id=getattr(u, 'pk', None), user_name=((u.get_full_name() or u.username) if u else 'System'))


# ------------------------------------------------------------------ numbering, profile, codes
def next_number(model, prefix):
    year = today().year
    base = f'{prefix}-{year}-'
    last = model.objects.filter(number__startswith=base).order_by('-number').values_list('number', flat=True).first()
    n = int(last.rsplit('-', 1)[1]) + 1 if last and last.rsplit('-', 1)[1].isdigit() else 1
    while model.objects.filter(number=f'{base}{n:04d}').exists():
        n += 1
    return f'{base}{n:04d}'


def type_config(asset_type_id, lock=False):
    qs = AssetTypeConfig.objects.select_for_update() if lock else AssetTypeConfig.objects
    cfg = qs.filter(asset_type_id=asset_type_id).first()
    if cfg:
        return cfg
    at = M('OrganisationManager', 'AssetType').objects.filter(pk=asset_type_id).first()
    letters = ''.join(ch for ch in (at.name if at else 'AST') if ch.isalnum()).upper()[:3] or 'AST'
    cfg, _ = AssetTypeConfig.objects.get_or_create(asset_type_id=asset_type_id, defaults={'code_prefix': letters})
    return cfg


@transaction.atomic
def next_asset_code(asset_type_id):
    cfg = type_config(asset_type_id, lock=True)
    cfg = AssetTypeConfig.objects.select_for_update().get(pk=cfg.pk)
    pad = AssetPlusSettings.get().code_padding or 4
    prefix = (cfg.code_prefix or 'AST').strip().upper()
    n = cfg.next_number or 1
    while AssetProfile.objects.filter(asset_code=f'{prefix}-{n:0{pad}d}').exists():
        n += 1
    cfg.next_number = n + 1
    cfg.save(update_fields=['next_number'])
    return f'{prefix}-{n:0{pad}d}'


def ensure_profile(asset, user=None):
    p = AssetProfile.objects.filter(asset_id=asset.pk).first()
    if p:
        return p
    cfg = type_config(asset.asset_type_id)
    code = next_asset_code(asset.asset_type_id)
    # branch of an existing asset: where it is now (holder's branch), else the type's branch when the type has only one
    a = open_allocation(asset.pk)
    s = None if a else open_shared(asset.pk)
    branch = a.employee.emp_branch_id_id if a else (s.branch_id if s else None)
    if branch is None and asset.asset_type_id:
        type_branches = list(asset.asset_type.branch.values_list('id', flat=True))
        branch = type_branches[0] if len(type_branches) == 1 else None
    p = AssetProfile.objects.create(
        asset_id=asset.pk, asset_code=code, barcode=code, currency=AssetPlusSettings.get().currency, branch_id=branch,
        depreciation_method=cfg.depreciation_method, useful_life_months=cfg.useful_life_months or 36,
        created_by_id=getattr(user, 'pk', None))
    return p


def ensure_profiles(assets):
    have = set(AssetProfile.objects.filter(asset_id__in=[a.pk for a in assets]).values_list('asset_id', flat=True))
    for a in assets:
        if a.pk not in have:
            ensure_profile(a)


def effective_status(asset, profile=None):
    profile = profile if profile is not None else AssetProfile.objects.filter(asset_id=asset.pk).first()
    if profile and profile.extra_status == 'lost' and asset.status != 'disposed':
        return 'lost'
    return asset.status


STATUS_LABEL = {'available': 'Available', 'assigned': 'Assigned', 'maintenance': 'Under maintenance', 'disposed': 'Disposed', 'lost': 'Lost / stolen'}


def open_allocation(asset_id):
    return Allocation().objects.filter(asset_id=asset_id, returned_date__isnull=True).select_related('employee').order_by('-id').first()


def open_shared(asset_id):
    return SharedAllocation.objects.filter(asset_id=asset_id, returned_date__isnull=True).order_by('-id').first()


def holder(asset_id):
    a = open_allocation(asset_id)
    if a:
        x = AllocationExtra.objects.filter(allocation_id=a.pk).first()
        return {'type': 'employee', 'employee_id': a.employee_id, 'name': person(a.employee), 'since': a.assigned_date,
                'allocation_id': a.pk, 'branch_id': a.employee.emp_branch_id_id, 'department_id': a.employee.emp_dept_id_id,
                'expected_return_date': x.expected_return_date if x else None, 'ack_status': x.ack_status if x else 'not_required'}
    s = open_shared(asset_id)
    if s:
        label = s.location or (dept_name(s.department_id) if s.target_type == 'department' else branch_name(s.branch_id))
        return {'type': s.target_type, 'shared_id': s.pk, 'name': label or s.get_target_type_display(), 'since': s.assigned_date,
                'branch_id': s.branch_id, 'department_id': s.department_id, 'location': s.location,
                'custodian_employee_id': s.custodian_employee_id, 'custodian': emp_name(s.custodian_employee_id),
                'expected_return_date': s.expected_return_date}
    return None


def asset_branch(asset, profile=None, hold=None):
    profile = profile if profile is not None else AssetProfile.objects.filter(asset_id=asset.pk).first()
    if profile and profile.branch_id:
        return profile.branch_id
    hold = hold if hold is not None else holder(asset.pk)
    return hold.get('branch_id') if hold else None


# ------------------------------------------------------------------ depreciation
def months_between(start, end):
    if not start or not end or end < start:
        return 0
    m = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        m -= 1
    return max(m, 0)


def book_value(profile, asset, on=None):
    """Book value on a date: cost less depreciation (never below the salvage value)."""
    on = on or today()
    cost = money(profile.purchase_cost)
    salvage = min(money(profile.salvage_value), cost)
    start = profile.depreciation_start or asset.purchase_date
    life = profile.useful_life_months or 0
    method = profile.depreciation_method
    if method == 'none' or life <= 0 or cost <= 0 or not start:
        return cost
    months = months_between(start, on)
    if months >= life:
        return salvage
    if method == 'straight_line':
        dep = (cost - salvage) * Decimal(months) / Decimal(life)
        return money(cost - dep)
    rate = (Decimal(profile.declining_rate) / 100) if profile.declining_rate else (Decimal(2) * Decimal(12) / Decimal(life))
    rate = min(rate, Decimal(1))
    bv = Decimal(float(cost) * (1 - float(rate)) ** (months / 12.0))
    return money(max(bv, salvage))


def depreciation_schedule(profile, asset):
    start = profile.depreciation_start or asset.purchase_date
    rows = []
    if not start or profile.depreciation_method == 'none' or not profile.useful_life_months:
        return rows
    years = (profile.useful_life_months + 11) // 12
    opening = money(profile.purchase_cost)
    for y in range(1, years + 1):
        end = _add_months(start, 12 * y)
        closing = book_value(profile, asset, end)
        rows.append({'year': y, 'until': end, 'opening': opening, 'depreciation': money(opening - closing), 'closing': closing})
        opening = closing
    return rows


def _add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def first_of_month(d):
    return d.replace(day=1)


# ------------------------------------------------------------------ allocation rules
BLOCK_MSG = {
    'assigned': 'This asset is already given to someone. Return or transfer it first.',
    'maintenance': 'This asset is under maintenance. Complete the maintenance record before giving it out.',
    'disposed': 'This asset has been disposed and cannot be given out.',
    'lost': 'This asset is reported lost / stolen. Mark it as found before giving it out.',
}


def allocation_block(asset):
    """Why the asset cannot be given out now (None = it can)."""
    st = effective_status(asset)
    if st != 'available':
        return BLOCK_MSG.get(st, f'This asset is {st} and cannot be given out.')
    if open_allocation(asset.pk) or open_shared(asset.pk):
        return BLOCK_MSG['assigned']
    return None


@transaction.atomic
def allocate(asset, user, *, employee_id=None, target_type='employee', branch_id=None, location='', department_id=None,
             custodian_employee_id=None, assigned_date=None, expected_return_date=None, accessories='', handover_condition='',
             handover_note='', handover_file=None, transfer=None, skip_check=False):
    asset = Asset().objects.select_for_update().get(pk=asset.pk)
    if not skip_check:
        why = allocation_block(asset)
        if why:
            raise AssetError(why, 'asset')
    assigned_date = assigned_date or today()
    if expected_return_date and expected_return_date < assigned_date:
        raise AssetError('The expected return date is before the allocation date.', 'expected_return_date')
    settings = AssetPlusSettings.get()
    if target_type == 'employee':
        e = emp(employee_id)
        if e is None:
            raise AssetError('Choose the employee who receives the asset.', 'employee')
        if e.is_active is False:
            raise AssetError(f'{person(e)} is not an active employee.', 'employee')
        with quiet():
            alloc = Allocation()(asset=asset, employee=e, assigned_date=assigned_date)
            alloc.save()
        x, _ = AllocationExtra.objects.update_or_create(allocation_id=alloc.pk, defaults=dict(
            asset_id=asset.pk, employee_id=e.pk, expected_return_date=expected_return_date, accessories=accessories or '',
            handover_condition=handover_condition or asset.condition, handover_note=handover_note or '', issued_by_id=getattr(user, 'pk', None),
            ack_status='pending' if settings.require_acknowledgement else 'not_required', transfer_id=getattr(transfer, 'pk', None)))
        if handover_file:
            x.handover_file = handover_file
            x.save(update_fields=['handover_file'])
        if not transfer:
            log_event(asset.pk, 'allocated', f'Given to {person(e)} on {assigned_date:%d %b %Y}'
                      + (f', expected back {expected_return_date:%d %b %Y}' if expected_return_date else ''),
                      user, e.pk, ('allocation', alloc.pk))
        notify(e, f'{asset.name} has been allocated to you. Please confirm you received it in My assets.' if settings.require_acknowledgement
               else f'{asset.name} has been allocated to you.')
        return alloc
    if target_type not in ('location', 'department'):
        raise AssetError('Allocate to an employee, a location or a department.', 'target_type')
    if target_type == 'location' and not (branch_id or location):
        raise AssetError('Choose the branch or type the location.', 'location')
    if target_type == 'department' and not department_id:
        raise AssetError('Choose the department.', 'department_id')
    if custodian_employee_id and not emp(custodian_employee_id):
        raise AssetError('The custodian employee does not exist.', 'custodian_employee_id')
    s = SharedAllocation.objects.create(asset_id=asset.pk, target_type=target_type, branch_id=branch_id or None, location=location or '',
                                        department_id=department_id or None, custodian_employee_id=custodian_employee_id or None,
                                        assigned_date=assigned_date, expected_return_date=expected_return_date, note=handover_note or '',
                                        issued_by_id=getattr(user, 'pk', None), transfer_id=getattr(transfer, 'pk', None))
    asset.status = 'assigned'
    with quiet():
        asset.save(update_fields=['status'])
    if branch_id:
        AssetProfile.objects.filter(asset_id=asset.pk).update(branch_id=branch_id, **({'location': location} if location else {}))
    if not transfer:
        h = holder(asset.pk)
        log_event(asset.pk, 'allocated', f'Given to {s.get_target_type_display().lower()} {h["name"] if h else ""} on {assigned_date:%d %b %Y}',
                  user, custodian_employee_id, ('shared', s.pk))
    return s


@transaction.atomic
def acknowledge(extra, user, accept=True, notes=''):
    if extra.ack_status in ('accepted', 'disputed'):
        raise AssetError('You have already confirmed this asset.')
    a = Allocation().objects.filter(pk=extra.allocation_id).first()
    if not a or a.returned_date:
        raise AssetError('This allocation is closed.')
    extra.ack_status = 'accepted' if accept and not notes else 'disputed'
    extra.ack_at = timezone.now()
    extra.ack_condition_notes = notes or ''
    extra.ack_by_id = getattr(user, 'pk', None)
    extra.save()
    log_event(extra.asset_id, 'acknowledged', 'Receipt confirmed by the employee' + (f' – remarks: {notes}' if notes else ''), user,
              extra.employee_id, ('allocation', extra.allocation_id))
    if notes:
        notify_hr(asset_branch(a.asset), f'{person(a.employee)} confirmed receiving {a.asset.name} with remarks: {notes[:120]}')
    return extra


# ------------------------------------------------------------------ return
@transaction.atomic
def request_return(asset, user, employee_id, note='', preferred_date=None):
    a = open_allocation(asset.pk)
    if not a or a.employee_id != employee_id:
        raise AssetError('This asset is not with you.')
    if AssetReturn.objects.filter(allocation_id=a.pk, status='requested').exists():
        raise AssetError('A return is already requested for this asset.')
    r = AssetReturn.objects.create(number=next_number(AssetReturn, 'RET'), asset_id=asset.pk, allocation_id=a.pk, employee_id=employee_id,
                                   requested_by_id=getattr(user, 'pk', None), request_note=note or '', preferred_date=preferred_date)
    log_event(asset.pk, 'return_requested', f'Return requested by {person(a.employee)}' + (f': {note}' if note else ''), user, employee_id, ('return', r.pk))
    notify_hr(asset_branch(asset), f'{person(a.employee)} wants to return {asset.name} ({r.number}).')
    return r


@transaction.atomic
def complete_return(asset, user, *, return_date=None, condition='healthy', accessories_returned=True, missing_items='', damage_found=False,
                    damage_description='', damage_estimate=0, data_wiped=False, notes='', return_request=None):
    asset = Asset().objects.select_for_update().get(pk=asset.pk)
    if condition not in ('healthy', 'minor_damage', 'major_damage'):
        raise AssetError('Choose the condition: healthy, minor damage or major damage.', 'condition')
    if not accessories_returned and not missing_items:
        raise AssetError('List the items that were not returned.', 'missing_items')
    if damage_found and not damage_description:
        raise AssetError('Describe the damage found.', 'damage_description')
    if condition != 'healthy' and not damage_found:
        damage_found = True
        damage_description = damage_description or f'Returned with {condition.replace("_", " ")}.'
    a = open_allocation(asset.pk)
    s = None if a else open_shared(asset.pk)
    if not a and not s:
        raise AssetError('This asset is not allocated to anyone.')
    return_date = return_date or today()
    since = a.assigned_date if a else s.assigned_date
    if since and return_date < since:
        raise AssetError('The return date is before the allocation date.', 'return_date')
    r = return_request or AssetReturn.objects.filter(asset_id=asset.pk, status='requested').first()
    if r is None:
        r = AssetReturn(number=next_number(AssetReturn, 'RET'), asset_id=asset.pk, requested_by_id=getattr(user, 'pk', None))
    r.allocation_id = a.pk if a else None
    r.shared_allocation_id = s.pk if s else None
    r.employee_id = a.employee_id if a else s.custodian_employee_id
    r.status, r.return_date, r.condition = 'completed', return_date, condition
    r.accessories_returned, r.missing_items = bool(accessories_returned), missing_items or ''
    r.damage_found, r.damage_description, r.damage_estimate = bool(damage_found), damage_description or '', money(damage_estimate)
    r.data_wiped, r.notes, r.received_by_id = bool(data_wiped), notes or '', getattr(user, 'pk', None)
    r.save()
    with quiet():
        if a:
            a.return_asset(condition, return_date)
        else:
            s.returned_date, s.return_condition = return_date, condition
            s.save(update_fields=['returned_date', 'return_condition'])
            asset.status, asset.condition = 'available', condition
            asset.save(update_fields=['status', 'condition'])
    who = person(a.employee) if a else (holder_label_shared(s))
    bits = [f'Returned by {who} on {return_date:%d %b %Y}', f'condition {condition.replace("_", " ")}']
    if not accessories_returned:
        bits.append(f'missing: {missing_items}')
    log_event(asset.pk, 'returned', ', '.join(bits), user, r.employee_id, ('return', r.pk))
    if damage_found:
        d = report_damage(asset, user, employee_id=r.employee_id, allocation_id=r.allocation_id, damage_date=return_date,
                          description=damage_description, severity='major' if condition == 'major_damage' else 'minor',
                          repair_cost=damage_estimate, source='return', return_id=r.pk)
        r.damage_id = d.pk
        r.save(update_fields=['damage_id'])
    return r


def holder_label_shared(s):
    return s.location or dept_name(s.department_id) or branch_name(s.branch_id) or 'shared location'


# ------------------------------------------------------------------ transfer
def _party(prefix, obj):
    t = getattr(obj, f'{prefix}_type')
    if t == 'employee':
        return emp_name(getattr(obj, f'{prefix}_employee_id'))
    if t == 'store':
        return 'store'
    if t == 'department':
        return dept_name(getattr(obj, f'{prefix}_department_id'))
    return getattr(obj, f'{prefix}_location') or branch_name(getattr(obj, f'{prefix}_branch_id'))


def party_label(obj, prefix):
    return _party(prefix, obj)


@transaction.atomic
def request_transfer(asset, user, *, to_type, to_employee_id=None, to_branch_id=None, to_location='', to_department_id=None,
                     transfer_date=None, reason='', requested_by_employee_id=None):
    if not (reason or '').strip():
        raise AssetError('Give the reason for the transfer.', 'reason')
    st = effective_status(asset)
    if st in ('disposed', 'lost', 'maintenance'):
        raise AssetError(BLOCK_MSG[st].replace('given out', 'transferred'), 'asset')
    if AssetTransfer.objects.filter(asset_id=asset.pk, status='pending').exists():
        raise AssetError('A transfer of this asset is already waiting for approval.', 'asset')
    h = holder(asset.pk)
    if not h:
        raise AssetError('This asset is in the store. Use Allocate to give it out.', 'asset')
    t = AssetTransfer(asset_id=asset.pk, transfer_date=transfer_date or today(), reason=reason.strip(), requested_by_id=getattr(user, 'pk', None),
                      requested_by_employee_id=requested_by_employee_id)
    t.from_type = h['type']
    t.from_employee_id = h.get('employee_id')
    t.from_branch_id = h.get('branch_id')
    t.from_location = h.get('location') or ''
    t.from_department_id = h.get('department_id') if h['type'] == 'department' else None
    if to_type not in ('employee', 'location', 'department', 'store'):
        raise AssetError('Transfer to an employee, a location, a department or back to the store.', 'to_type')
    t.to_type = to_type
    if to_type == 'employee':
        e = emp(to_employee_id)
        if not e:
            raise AssetError('Choose the employee who receives the asset.', 'to_employee_id')
        if e.is_active is False:
            raise AssetError(f'{person(e)} is not an active employee.', 'to_employee_id')
        if h['type'] == 'employee' and e.pk == h.get('employee_id'):
            raise AssetError('The asset is already with this employee.', 'to_employee_id')
        t.to_employee_id, t.to_branch_id = e.pk, e.emp_branch_id_id
    elif to_type == 'location':
        if not (to_branch_id or to_location):
            raise AssetError('Choose the branch or type the location.', 'to_location')
        t.to_branch_id, t.to_location = to_branch_id or None, to_location or ''
    elif to_type == 'department':
        if not to_department_id:
            raise AssetError('Choose the department.', 'to_department_id')
        t.to_department_id, t.to_branch_id = to_department_id, to_branch_id or None
    if h.get('since') and t.transfer_date < h['since']:
        raise AssetError('The transfer date is before the current allocation date.', 'transfer_date')
    t.number = next_number(AssetTransfer, 'TRF')
    t.save()
    log_event(asset.pk, 'transfer_requested', f'Transfer {t.number} requested: {_party("from", t)} → {_party("to", t)} – {t.reason}', user,
              t.from_employee_id, ('transfer', t.pk))
    notify_hr(asset_branch(asset), f'Transfer {t.number} of {asset.name} waits for approval.')
    return t


@transaction.atomic
def decide_transfer(t, user, approve, note=''):
    t = AssetTransfer.objects.select_for_update().get(pk=t.pk)
    if t.status != 'pending':
        raise AssetError('This transfer is not waiting for approval.')
    t.decided_by_id, t.decided_at, t.decision_note = getattr(user, 'pk', None), timezone.now(), note or ''
    asset = Asset().objects.get(pk=t.asset_id)
    if not approve:
        t.status = 'rejected'
        t.save()
        log_event(asset.pk, 'transfer_rejected', f'Transfer {t.number} rejected' + (f': {note}' if note else ''), user, t.from_employee_id, ('transfer', t.pk))
        if t.from_employee_id:
            notify(emp(t.from_employee_id), f'Transfer {t.number} of {asset.name} was rejected.')
        return t
    execute_transfer(t, asset, user)
    return t


def execute_transfer(t, asset, user):
    h = holder(asset.pk)
    if not h:
        raise AssetError('The asset is no longer allocated – the transfer cannot be done.')
    if h['type'] != t.from_type or (t.from_employee_id and h.get('employee_id') != t.from_employee_id):
        raise AssetError('The asset has changed hands since the transfer was requested. Reject this transfer and request a new one.')
    st = effective_status(asset)
    if st in ('maintenance', 'disposed', 'lost'):
        raise AssetError(BLOCK_MSG[st].replace('given out', 'transferred'))
    with quiet():
        if h['type'] == 'employee':
            Allocation().objects.get(pk=h['allocation_id']).return_asset(asset.condition, t.transfer_date)
        else:
            s = SharedAllocation.objects.get(pk=h['shared_id'])
            s.returned_date, s.return_condition = t.transfer_date, asset.condition
            s.save(update_fields=['returned_date', 'return_condition'])
            asset.status = 'available'
            asset.save(update_fields=['status'])
    asset.refresh_from_db()
    if t.to_type == 'employee':
        allocate(asset, user, employee_id=t.to_employee_id, assigned_date=t.transfer_date, transfer=t, skip_check=True,
                 handover_note=f'Transfer {t.number}')
    elif t.to_type in ('location', 'department'):
        allocate(asset, user, target_type=t.to_type, branch_id=t.to_branch_id, location=t.to_location, department_id=t.to_department_id,
                 assigned_date=t.transfer_date, transfer=t, skip_check=True, handover_note=f'Transfer {t.number}')
    if t.to_branch_id:
        AssetProfile.objects.filter(asset_id=asset.pk).update(branch_id=t.to_branch_id)
    t.status = 'completed'
    t.completed_at = timezone.now()
    t.save()
    log_event(asset.pk, 'transferred', f'Transferred {_party("from", t)} → {_party("to", t)} on {t.transfer_date:%d %b %Y} ({t.number})', user,
              t.to_employee_id or t.from_employee_id, ('transfer', t.pk))
    for eid in {t.from_employee_id, t.to_employee_id} - {None}:
        notify(emp(eid), f'{asset.name} has been transferred ({t.number}).')


# ------------------------------------------------------------------ maintenance
def compute_next_due(s, profile=None):
    if s.interval_type == 'days':
        base = s.last_done_date or today()
        s.next_due_date = base + timedelta(days=int(s.interval_value or 0))
        s.next_due_reading = None
    else:
        base = s.last_done_reading
        if base is None and profile is not None:
            base = profile.meter_reading
        s.next_due_reading = (Decimal(base or 0) + Decimal(s.interval_value or 0))
        s.next_due_date = None
    return s


def schedule_due(s, profile=None, on=None):
    """'overdue' / 'due' / '' for a schedule."""
    on = on or today()
    if not s.active:
        return ''
    if s.interval_type == 'days' and s.next_due_date:
        if s.next_due_date < on:
            return 'overdue'
        if s.next_due_date <= on + timedelta(days=s.remind_days_before or 0):
            return 'due'
        return ''
    if s.next_due_reading is not None and profile is not None and profile.meter_reading is not None:
        left = Decimal(s.next_due_reading) - Decimal(profile.meter_reading)
        if left < 0:
            return 'overdue'
        if left <= Decimal(s.interval_value or 0) * Decimal('0.1'):
            return 'due'
    return ''


@transaction.atomic
def start_maintenance(asset, user, *, kind='breakdown', description='', vendor='', start_date=None, schedule_id=None, damage_id=None,
                      cost=0, reading=None):
    asset = Asset().objects.select_for_update().get(pk=asset.pk)
    st = effective_status(asset)
    if st in ('disposed', 'lost'):
        raise AssetError(f'A {STATUS_LABEL[st].lower()} asset cannot be sent for maintenance.', 'asset')
    if MaintenanceRecord.objects.filter(asset_id=asset.pk, status='open').exists():
        raise AssetError('This asset is already in maintenance. Complete the open record first.', 'asset')
    if not (description or '').strip():
        raise AssetError('Describe the work to be done.', 'description')
    if kind not in dict(MaintenanceRecord.KIND):
        raise AssetError('Choose preventive, breakdown or inspection.', 'kind')
    if schedule_id and not MaintenanceSchedule.objects.filter(pk=schedule_id, asset_id=asset.pk).exists():
        raise AssetError('The maintenance plan does not belong to this asset.', 'schedule')
    if money(cost) < 0:
        raise AssetError('The cost cannot be negative.', 'cost')
    r = MaintenanceRecord.objects.create(number=next_number(MaintenanceRecord, 'MNT'), asset_id=asset.pk, schedule_id=schedule_id or None,
                                         damage_id=damage_id or None, kind=kind, vendor=vendor or '', description=description.strip(),
                                         start_date=start_date or today(), cost=money(cost), reading=reading, status_before=asset.status,
                                         created_by_id=getattr(user, 'pk', None))
    asset.status = 'maintenance'
    with quiet():
        asset.save(update_fields=['status'])
    if reading is not None:
        AssetProfile.objects.filter(asset_id=asset.pk).update(meter_reading=reading)
    log_event(asset.pk, 'maintenance_started', f'{r.get_kind_display()} maintenance {r.number} started' + (f' at {vendor}' if vendor else '')
              + f': {r.description}', user, None, ('maintenance', r.pk))
    return r


@transaction.atomic
def close_maintenance(r, user, *, end_date=None, cost=None, downtime_hours=None, result='fixed', result_notes='', reading=None, cancel=False):
    r = MaintenanceRecord.objects.select_for_update().get(pk=r.pk)
    if r.status != 'open':
        raise AssetError('This maintenance record is already closed.')
    asset = Asset().objects.select_for_update().get(pk=r.asset_id)
    end_date = end_date or today()
    if end_date < r.start_date:
        raise AssetError('The end date is before the start date.', 'end_date')
    if cost is not None:
        if money(cost) < 0:
            raise AssetError('The cost cannot be negative.', 'cost')
        r.cost = money(cost)
    if result and result not in dict(MaintenanceRecord.RESULT):
        raise AssetError('Choose the result: fixed, could not be fixed, part replaced or no fault found.', 'result')
    r.end_date = end_date
    r.downtime_hours = Decimal(str(downtime_hours)) if downtime_hours not in (None, '') else Decimal((end_date - r.start_date).days * 24)
    r.result, r.result_notes = ('' if cancel else (result or 'fixed')), result_notes or ''
    if reading is not None:
        r.reading = reading
    r.status = 'cancelled' if cancel else 'closed'
    r.closed_by_id = getattr(user, 'pk', None)
    r.save()
    back = 'assigned' if (open_allocation(asset.pk) or open_shared(asset.pk)) else 'available'
    asset.status = back
    fields = ['status']
    if not cancel and r.result in ('fixed', 'replaced', 'ok') and asset.condition != 'healthy':
        asset.condition = 'healthy'
        fields.append('condition')
    with quiet():
        asset.save(update_fields=fields)
    prof = AssetProfile.objects.filter(asset_id=asset.pk).first()
    if reading is not None and prof:
        prof.meter_reading = reading
        prof.save(update_fields=['meter_reading'])
    if r.schedule_id and not cancel:
        s = MaintenanceSchedule.objects.filter(pk=r.schedule_id).first()
        if s:
            s.last_done_date = end_date
            if r.reading is not None:
                s.last_done_reading = r.reading
            compute_next_due(s, prof)
            s.reminded_for = ''
            s.save()
    log_event(asset.pk, 'maintenance_cancelled' if cancel else 'maintenance_done',
              (f'Maintenance {r.number} cancelled' if cancel else
               f'Maintenance {r.number} completed on {end_date:%d %b %Y}: {r.get_result_display()}, cost {money(r.cost)}, downtime {r.downtime_hours} h'),
              user, None, ('maintenance', r.pk))
    return r


# ------------------------------------------------------------------ damage, loss, recovery
def _validate_recovery(responsibility, recovery_amount, method, instalments, employee_id, first_month, cap=None):
    s = AssetPlusSettings.get()
    amt = money(recovery_amount)
    if responsibility not in dict(AssetDamage._meta.get_field('responsibility').choices):
        raise AssetError('Choose who is responsible: company, employee, shared or third party.', 'responsibility')
    if method not in dict(AssetDamage._meta.get_field('recovery_method').choices):
        raise AssetError('Choose how it is recovered: none, payroll, cash or waived.', 'recovery_method')
    if amt < 0:
        raise AssetError('The recovery amount cannot be negative.', 'recovery_amount')
    if responsibility in ('company', 'third_party') and amt > 0 and method in ('payroll', 'cash'):
        raise AssetError('Only an employee can be charged. Set the responsibility to employee or shared, or the amount to 0.', 'recovery_amount')
    if cap is not None and cap > 0 and amt > cap:
        raise AssetError(f'The recovery amount is more than the cost / value ({cap}).', 'recovery_amount')
    if amt > 0 and method in ('payroll', 'cash') and not employee_id:
        raise AssetError('Choose the employee to recover from.', 'employee')
    if method == 'payroll' and amt > 0:
        n = int(instalments or 0)
        if n < 1 or n > (s.max_instalments or 12):
            raise AssetError(f'Instalments must be between 1 and {s.max_instalments or 12}.', 'instalments')
        if not first_month:
            raise AssetError('Choose the first payroll month of the deduction.', 'first_deduction_month')
    return amt


def _plan(obj, kind):
    """Create the monthly instalments (last one takes the rounding difference)."""
    RecoveryInstalment.objects.filter(source_type=kind, source_id=obj.pk, status='pending').delete()
    n = max(int(obj.instalments or 1), 1)
    amt = money(obj.recovery_amount)
    each = (amt / n).quantize(CENT, rounding=ROUND_HALF_UP)
    month = first_of_month(obj.first_deduction_month)
    rows = []
    for i in range(n):
        a = each if i < n - 1 else amt - each * (n - 1)
        rows.append(RecoveryInstalment(source_type=kind, source_id=obj.pk, employee_id=obj.employee_id, month=_add_months(month, i), amount=a))
    RecoveryInstalment.objects.bulk_create(rows)
    return rows


@transaction.atomic
def report_damage(asset, user, *, employee_id=None, allocation_id=None, damage_date=None, description='', severity='minor', repair_cost=0,
                  source='hr', return_id=None):
    if not (description or '').strip():
        raise AssetError('Describe the damage.', 'description')
    if severity not in dict(AssetDamage.SEVERITY):
        raise AssetError('Choose the severity: minor, major or beyond repair.', 'severity')
    if effective_status(asset) == 'disposed':
        raise AssetError('This asset has been disposed.', 'asset')
    if money(repair_cost) < 0:
        raise AssetError('The repair cost cannot be negative.', 'repair_cost')
    damage_date = damage_date or today()
    if damage_date > today():
        raise AssetError('The damage date cannot be in the future.', 'damage_date')
    if employee_id is None and allocation_id is None:
        a = open_allocation(asset.pk)
        if a:
            employee_id, allocation_id = a.employee_id, a.pk
    d = AssetDamage.objects.create(number=next_number(AssetDamage, 'DMG'), asset_id=asset.pk, employee_id=employee_id, allocation_id=allocation_id,
                                   damage_date=damage_date, description=description.strip(), severity=severity, repair_cost=money(repair_cost),
                                   source=source, return_id=return_id, reported_by_id=getattr(user, 'pk', None),
                                   responsibility='employee' if source in ('ess', 'return') and employee_id else 'company')
    log_event(asset.pk, 'damaged', f'Damage {d.number} reported ({d.get_severity_display().lower()}): {d.description}', user, employee_id, ('damage', d.pk))
    notify_hr(asset_branch(asset), f'Damage {d.number} on {asset.name} waits for approval.')
    return d


def _decide_incident(obj, kind, user, approve, data):
    if obj.status != 'reported':
        raise AssetError('This record is not waiting for approval.')
    obj.decided_by_id, obj.decided_at, obj.decision_note = getattr(user, 'pk', None), timezone.now(), (data.get('note') or '')
    if not approve:
        obj.status = 'rejected'
        return False
    for f in ('responsibility', 'recovery_method', 'instalments'):
        if data.get(f) not in (None, ''):
            setattr(obj, f, data[f])
    if data.get('recovery_amount') not in (None, ''):
        obj.recovery_amount = data['recovery_amount']
    if data.get('first_deduction_month'):
        obj.first_deduction_month = first_of_month(data['first_deduction_month'])
    if data.get('employee_id'):
        obj.employee_id = data['employee_id']
    cap = None
    if kind == 'damage':
        if data.get('repair_cost') not in (None, ''):
            if money(data['repair_cost']) < 0:
                raise AssetError('The repair cost cannot be negative.', 'repair_cost')
            obj.repair_cost = money(data['repair_cost'])
        cap = money(obj.repair_cost) if obj.repair_cost else None
    obj.recovery_amount = _validate_recovery(obj.responsibility, obj.recovery_amount, obj.recovery_method, obj.instalments, obj.employee_id,
                                             obj.first_deduction_month, cap)
    if obj.recovery_method != 'payroll':
        obj.instalments = 1
    if obj.recovery_amount > 0 and obj.recovery_method == 'payroll':
        obj.status = 'recovering'
    else:
        obj.status = 'closed'
        if obj.recovery_method == 'cash':
            obj.recovered_amount = obj.recovery_amount
    return True


@transaction.atomic
def decide_damage(d, user, approve, data):
    d = AssetDamage.objects.select_for_update().get(pk=d.pk)
    ok = _decide_incident(d, 'damage', user, approve, data)
    d.save()
    asset = Asset().objects.get(pk=d.asset_id)
    if not ok:
        log_event(asset.pk, 'damage_rejected', f'Damage {d.number} rejected' + (f': {d.decision_note}' if d.decision_note else ''), user, d.employee_id, ('damage', d.pk))
        return d
    cond = 'minor_damage' if d.severity == 'minor' else 'major_damage'
    if asset.condition != cond and not MaintenanceRecord.objects.filter(damage_id=d.pk, status='closed').exists():
        asset.condition = cond
        with quiet():
            asset.save(update_fields=['condition'])
    if d.status == 'recovering':
        _plan(d, 'damage')
    log_event(asset.pk, 'damage_approved', f'Damage {d.number} approved – responsibility {d.get_responsibility_display().lower()}'
              + (f', recover {d.recovery_amount} by {d.get_recovery_method_display().lower()}' + (f' in {d.instalments} instalment(s) from {d.first_deduction_month:%b %Y}' if d.status == 'recovering' else '')
                 if d.recovery_amount else ''), user, d.employee_id, ('damage', d.pk))
    if d.employee_id and d.recovery_amount:
        notify(emp(d.employee_id), f'Damage {d.number} on {asset.name}: {d.recovery_amount} will be recovered'
               + (f' from your salary in {d.instalments} instalment(s).' if d.status == 'recovering' else '.'))
    return d


@transaction.atomic
def report_loss(asset, user, *, employee_id=None, loss_date=None, kind='lost', description='', place='', police_report_no='', police_report_file=None,
                source='hr'):
    asset = Asset().objects.select_for_update().get(pk=asset.pk)
    st = effective_status(asset)
    if st in ('disposed', 'lost'):
        raise AssetError(f'This asset is already {STATUS_LABEL[st].lower()}.', 'asset')
    if kind not in dict(AssetLoss.KIND):
        raise AssetError('Choose lost or stolen.', 'kind')
    if not (description or '').strip():
        raise AssetError('Describe what happened.', 'description')
    loss_date = loss_date or today()
    if loss_date > today():
        raise AssetError('The date cannot be in the future.', 'loss_date')
    if MaintenanceRecord.objects.filter(asset_id=asset.pk, status='open').exists():
        raise AssetError('The asset is in maintenance. Complete or cancel the maintenance record first.', 'asset')
    a = open_allocation(asset.pk)
    s = None if a else open_shared(asset.pk)
    if employee_id is None:
        employee_id = a.employee_id if a else (s.custodian_employee_id if s else None)
    prof = ensure_profile(asset)
    l = AssetLoss.objects.create(number=next_number(AssetLoss, 'LOS'), asset_id=asset.pk, employee_id=employee_id, allocation_id=a.pk if a else None,
                                 loss_date=loss_date, kind=kind, description=description.strip(), place=place or '', police_report_no=police_report_no or '',
                                 source=source, reported_by_id=getattr(user, 'pk', None), status_before=asset.status,
                                 book_value=book_value(prof, asset, loss_date),
                                 responsibility='employee' if source == 'ess' and employee_id else 'company')
    if police_report_file:
        l.police_report_file = police_report_file
        l.save(update_fields=['police_report_file'])
    with quiet():
        if a:
            a.return_asset(asset.condition, max(loss_date, a.assigned_date or loss_date))
        elif s:
            s.returned_date = max(loss_date, s.assigned_date)
            s.save(update_fields=['returned_date'])
            asset.status = 'available'
            asset.save(update_fields=['status'])
    prof.extra_status = 'lost'
    prof.save(update_fields=['extra_status'])
    log_event(asset.pk, 'lost', f'{l.get_kind_display()} on {loss_date:%d %b %Y} ({l.number})' + (f' at {place}' if place else '')
              + (f', police report {police_report_no}' if police_report_no else '') + f': {l.description}', user, employee_id, ('loss', l.pk))
    notify_hr(asset_branch(asset, prof), f'{asset.name} reported {kind} ({l.number}) – waits for approval.')
    return l


@transaction.atomic
def decide_loss(l, user, approve, data):
    l = AssetLoss.objects.select_for_update().get(pk=l.pk)
    if data.get('investigation_status'):
        l.investigation_status = data['investigation_status']
    ok = _decide_incident(l, 'loss', user, approve, data)
    if ok and l.investigation_status == 'open':
        l.investigation_status = 'investigating'
    l.save()
    asset = Asset().objects.get(pk=l.asset_id)
    if not ok:
        AssetProfile.objects.filter(asset_id=asset.pk).update(extra_status='')
        log_event(asset.pk, 'loss_rejected', f'Loss report {l.number} rejected – asset back in the store' + (f': {l.decision_note}' if l.decision_note else ''),
                  user, l.employee_id, ('loss', l.pk))
        return l
    if l.status == 'recovering':
        _plan(l, 'loss')
    log_event(asset.pk, 'loss_approved', f'Loss {l.number} approved – responsibility {l.get_responsibility_display().lower()}'
              + (f', recover {l.recovery_amount} by {l.get_recovery_method_display().lower()}' if l.recovery_amount else ''), user, l.employee_id, ('loss', l.pk))
    if l.employee_id and l.recovery_amount:
        notify(emp(l.employee_id), f'Loss {l.number} of {asset.name}: {l.recovery_amount} will be recovered'
               + (f' from your salary in {l.instalments} instalment(s).' if l.status == 'recovering' else '.'))
    return l


@transaction.atomic
def mark_found(l, user, found_on=None, note=''):
    l = AssetLoss.objects.select_for_update().get(pk=l.pk)
    if l.status == 'rejected' or l.found_on:
        raise AssetError('This loss record is already closed.')
    asset = Asset().objects.select_for_update().get(pk=l.asset_id)
    if asset.status == 'disposed':
        raise AssetError('The asset was written off. Reverse the disposal first.')
    l.found_on = found_on or today()
    l.investigation_status = 'closed_found'
    l.investigation_notes = ((l.investigation_notes + '\n') if l.investigation_notes else '') + (note or 'Asset found.')
    cancelled = RecoveryInstalment.objects.filter(source_type='loss', source_id=l.pk, status='pending').update(status='cancelled')
    l.status = 'closed'
    l.save()
    AssetProfile.objects.filter(asset_id=asset.pk).update(extra_status='')
    if asset.status != 'available' and not open_allocation(asset.pk):
        asset.status = 'available'
        with quiet():
            asset.save(update_fields=['status'])
    log_event(asset.pk, 'found', f'Found on {l.found_on:%d %b %Y} ({l.number})' + (f', {cancelled} pending deduction(s) cancelled' if cancelled else '')
              + (f': {note}' if note else ''), user, l.employee_id, ('loss', l.pk))
    return l


def asset_recovery_amount(employee, start_date=None, end_date=None):
    """Payroll formula variable: asset damage / loss instalments due up to the end of the period and not deducted yet."""
    emp_id = getattr(employee, 'pk', employee)
    from datetime import datetime
    end = end_date or today()
    if isinstance(end, datetime):
        end = end.date()
    total = RecoveryInstalment.objects.filter(employee_id=emp_id, status='pending', month__lte=end).aggregate(s=Sum('amount'))['s']
    return money(total or ZERO)


def _close_if_recovered(kind, source_id, user=None):
    model = AssetDamage if kind == 'damage' else AssetLoss
    obj = model.objects.filter(pk=source_id).first()
    if not obj:
        return
    done = RecoveryInstalment.objects.filter(source_type=kind, source_id=source_id, status__in=['deducted', 'final_settlement'])
    obj.recovered_amount = money(done.aggregate(s=Sum('amount'))['s'] or 0)
    open_left = RecoveryInstalment.objects.filter(source_type=kind, source_id=source_id, status='pending').exists()
    if not open_left and obj.status == 'recovering':
        obj.status = 'closed'
        obj.save(update_fields=['recovered_amount', 'status'])
        log_event(obj.asset_id, 'recovered', f'{obj.number}: {obj.recovered_amount} fully recovered', user, obj.employee_id, (kind, obj.pk))
    elif open_left and obj.status == 'closed':
        obj.status = 'recovering'
        obj.save(update_fields=['recovered_amount', 'status'])
    else:
        obj.save(update_fields=['recovered_amount'])


def mark_deducted(payslip, amount):
    """A payslip deducted `amount` with the asset_recovery_amount formula: the due instalments are marked deducted."""
    run = payslip.payroll_run
    import calendar
    end = getattr(run, 'attendance_end_date', None) or date(run.year, run.month, calendar.monthrange(run.year, run.month)[1])
    end = max(end, date(run.year, run.month, calendar.monthrange(run.year, run.month)[1]))
    rows = list(RecoveryInstalment.objects.filter(employee_id=payslip.employee_id, status='pending', month__lte=end).order_by('month', 'id'))
    already = RecoveryInstalment.objects.filter(payslip_id=payslip.pk, status='deducted').aggregate(s=Sum('amount'))['s'] or ZERO
    left = money(amount) - money(already)
    touched = set()
    for r in rows:
        if r.amount > left:
            break
        left -= r.amount
        r.status, r.payroll_run_id, r.payslip_id, r.done_on = 'deducted', run.pk, payslip.pk, getattr(run, 'payment_date', None) or today()
        r.save()
        touched.add((r.source_type, r.source_id))
    for k, sid in touched:
        _close_if_recovered(k, sid)
    return len(touched)


def unmark_payslip(payslip_id):
    rows = list(RecoveryInstalment.objects.filter(payslip_id=payslip_id, status='deducted'))
    for r in rows:
        r.status, r.payroll_run_id, r.payslip_id, r.done_on = 'pending', None, None, None
        r.save()
    for k, sid in {(r.source_type, r.source_id) for r in rows}:
        _close_if_recovered(k, sid)
    return len(rows)


# ------------------------------------------------------------------ disposal
@transaction.atomic
def request_disposal(asset, user, *, method, disposal_date=None, value=0, buyer='', reason=''):
    st = effective_status(asset)
    if st == 'disposed':
        raise AssetError('This asset is already disposed.', 'asset')
    if open_allocation(asset.pk) or open_shared(asset.pk):
        raise AssetError('The asset is still allocated. Return it before disposing of it.', 'asset')
    if MaintenanceRecord.objects.filter(asset_id=asset.pk, status='open').exists():
        raise AssetError('The asset is in maintenance. Complete or cancel the maintenance record first.', 'asset')
    if AssetDisposal.objects.filter(asset_id=asset.pk, status='pending').exists():
        raise AssetError('A disposal of this asset is already waiting for approval.', 'asset')
    if method not in dict(AssetDisposal.METHOD):
        raise AssetError('Choose sale, scrap, donation or write-off.', 'method')
    if not (reason or '').strip():
        raise AssetError('Give the reason for the disposal.', 'reason')
    value = money(value)
    if value < 0:
        raise AssetError('The value cannot be negative.', 'value')
    if method in ('donate', 'write_off') and value > 0:
        raise AssetError('A donation or write-off has no sale value – set the value to 0.', 'value')
    if method == 'sale' and value <= 0:
        raise AssetError('Enter the sale price.', 'value')
    if method == 'sale' and not (buyer or '').strip():
        raise AssetError('Enter the buyer.', 'buyer')
    disposal_date = disposal_date or today()
    if disposal_date < asset.purchase_date:
        raise AssetError('The disposal date is before the purchase date.', 'disposal_date')
    prof = ensure_profile(asset)
    bv = book_value(prof, asset, disposal_date)
    d = AssetDisposal.objects.create(number=next_number(AssetDisposal, 'DSP'), asset_id=asset.pk, method=method, disposal_date=disposal_date,
                                     value=value, buyer=buyer or '', reason=reason.strip(), book_value=bv, gain_loss=value - bv,
                                     requested_by_id=getattr(user, 'pk', None))
    log_event(asset.pk, 'disposal_requested', f'Disposal {d.number} requested ({d.get_method_display().lower()}, value {value}, book value {bv}): {d.reason}',
              user, None, ('disposal', d.pk))
    notify_hr(asset_branch(asset, prof), f'Disposal {d.number} of {asset.name} waits for approval.')
    return d


@transaction.atomic
def decide_disposal(d, user, approve, note=''):
    d = AssetDisposal.objects.select_for_update().get(pk=d.pk)
    if d.status != 'pending':
        raise AssetError('This disposal is not waiting for approval.')
    asset = Asset().objects.select_for_update().get(pk=d.asset_id)
    d.decided_by_id, d.decided_at, d.decision_note = getattr(user, 'pk', None), timezone.now(), note or ''
    if not approve:
        d.status = 'rejected'
        d.save()
        log_event(asset.pk, 'disposal_rejected', f'Disposal {d.number} rejected' + (f': {note}' if note else ''), user, None, ('disposal', d.pk))
        return d
    if open_allocation(asset.pk) or open_shared(asset.pk):
        raise AssetError('The asset was allocated again. Return it before approving the disposal.')
    prof = ensure_profile(asset)
    d.book_value = book_value(prof, asset, d.disposal_date)
    d.gain_loss = money(d.value) - d.book_value
    d.status_before = effective_status(asset, prof)
    d.status = 'approved'
    d.save()
    asset.status = 'disposed'
    with quiet():
        asset.save(update_fields=['status'])
    MaintenanceSchedule.objects.filter(asset_id=asset.pk).update(active=False)
    word = 'gain' if d.gain_loss > 0 else ('loss' if d.gain_loss < 0 else 'no gain or loss')
    log_event(asset.pk, 'disposed', f'Disposed by {d.get_method_display().lower()} on {d.disposal_date:%d %b %Y} ({d.number}): value {d.value}, '
              f'book value {d.book_value}, {word} {abs(d.gain_loss) if d.gain_loss else ""}'.rstrip(), user, None, ('disposal', d.pk))
    return d


# ------------------------------------------------------------------ exit clearance
def clearance(employee_id):
    """What is still open for an employee who leaves: assets with them, undecided incidents, deductions not yet taken."""
    items = []
    for a in Allocation().objects.filter(employee_id=employee_id, returned_date__isnull=True).select_related('asset'):
        p = AssetProfile.objects.filter(asset_id=a.asset_id).first()
        items.append({'kind': 'asset', 'asset_id': a.asset_id, 'asset': a.asset.name, 'code': p.asset_code if p else '',
                      'since': a.assigned_date, 'text': f'{a.asset.name} is still with the employee (since {a.assigned_date:%d %b %Y})' if a.assigned_date else f'{a.asset.name} is still with the employee'})
    for s in SharedAllocation.objects.filter(custodian_employee_id=employee_id, returned_date__isnull=True):
        a = Asset().objects.filter(pk=s.asset_id).first()
        items.append({'kind': 'custody', 'asset_id': s.asset_id, 'asset': a.name if a else '', 'since': s.assigned_date,
                      'text': f'Custodian of shared asset {a.name if a else s.asset_id} – hand the custody to someone else'})
    for model, kind in ((AssetDamage, 'damage'), (AssetLoss, 'loss')):
        for o in model.objects.filter(employee_id=employee_id, status='reported'):
            items.append({'kind': f'{kind}_pending', 'asset_id': o.asset_id, 'number': o.number, 'text': f'{o.number} waits for approval'})
    pending = RecoveryInstalment.objects.filter(employee_id=employee_id, status='pending')
    due = money(pending.aggregate(s=Sum('amount'))['s'] or 0)
    if due > 0:
        items.append({'kind': 'recovery', 'amount': due, 'text': f'{due} asset recovery not yet deducted – deduct it in the final settlement'})
    waiver = ClearanceWaiver.objects.filter(employee_id=employee_id, active=True).order_by('-id').first()
    return {'employee_id': employee_id, 'items': items, 'recovery_due': due, 'open': bool(items),
            'waived': bool(waiver), 'waiver': ({'reason': waiver.reason, 'by': user_label(waiver.waived_by_id), 'at': waiver.waived_at} if waiver else None),
            'blocked': bool(items) and not waiver}


@transaction.atomic
def settle_in_final_settlement(employee_id, user):
    rows = list(RecoveryInstalment.objects.filter(employee_id=employee_id, status='pending'))
    total = money(sum((r.amount for r in rows), ZERO))
    for r in rows:
        r.status, r.done_on = 'final_settlement', today()
        r.save(update_fields=['status', 'done_on'])
    for k, sid in {(r.source_type, r.source_id) for r in rows}:
        _close_if_recovered(k, sid, user)
        model = AssetDamage if k == 'damage' else AssetLoss
        o = model.objects.filter(pk=sid).first()
        if o:
            log_event(o.asset_id, 'recovered', f'{o.number}: remaining recovery moved to the final settlement', user, employee_id, (k, sid))
    return total


def eos_guard(eos, old_status):
    """Raise when a final settlement is processed / paid while the employee still has assets (see signals)."""
    if not AssetPlusSettings.get().block_final_settlement:
        return
    if eos.status not in ('processed', 'paid') or old_status in ('processed', 'paid'):
        return
    emp_id = eos.resignation.employee_id
    c = clearance(emp_id)
    if c['blocked']:
        from rest_framework.exceptions import ValidationError
        raise ValidationError({'status': 'Final settlement is blocked by the asset clearance: ' + '; '.join(i['text'] for i in c['items'])
                               + '. Return or recover them in Assets → Exit clearance, or record a waiver there.'})


# ------------------------------------------------------------------ notifications
def notify(e, message):
    if e is None:
        return
    try:
        N = M('OrganisationManager', 'AssetNotification')
        N.objects.create(recipient_employee=e, recipient_user=e.users if e.users_id else None, message=message[:255])
    except Exception:
        log.exception('asset notification failed')


def hr_users(branch_id=None):
    """Users of the company who manage assets (change_asset) for the branch (or all branches)."""
    from tenant_users.tenants.models import UserTenantPermissions
    ids = set()
    for utp in UserTenantPermissions.objects.filter(groups__permissions__codename='change_asset').distinct().select_related('profile'):
        ids.add(utp.profile_id)
    if branch_id:
        UBA = M('OrganisationManager', 'UserBranchAccess')
        restricted = {u for u in ids if UBA.objects.filter(user_id=u).exists()}
        allowed = set(UBA.objects.filter(user_id__in=restricted, branch__id=branch_id).values_list('user_id', flat=True))
        ids = (ids - restricted) | allowed
    return list(M('UserManagement', 'CustomUser').objects.filter(pk__in=ids))


def notify_hr(branch_id, message):
    try:
        N = M('OrganisationManager', 'AssetNotification')
        for u in hr_users(branch_id):
            N.objects.create(recipient_user=u, message=message[:255])
    except Exception:
        log.exception('asset HR notification failed')


# ------------------------------------------------------------------ scheduled jobs (Celery beat, once a day)
def daily_reminders(on=None):
    on = on or today()
    out = {'maintenance': 0, 'warranty': 0, 'insurance': 0, 'overdue_returns': 0}
    settings = AssetPlusSettings.get()
    A = Asset()
    for s in MaintenanceSchedule.objects.filter(active=True):
        a = A.objects.filter(pk=s.asset_id).exclude(status='disposed').first()
        if not a:
            continue
        p = AssetProfile.objects.filter(asset_id=a.pk).first()
        due = schedule_due(s, p, on)
        key = f'{s.next_due_date or s.next_due_reading}'
        if due and s.reminded_for != key:
            when = f'on {s.next_due_date:%d %b %Y}' if s.next_due_date else f'at {s.next_due_reading} {s.get_interval_type_display().lower()}'
            notify_hr(asset_branch(a, p), f'Maintenance "{s.title}" of {a.name} is {"overdue" if due == "overdue" else "due"} ({when}).')
            s.reminded_for = key
            s.save(update_fields=['reminded_for'])
            out['maintenance'] += 1
    for p in AssetProfile.objects.filter(warranty_end__isnull=False):
        days = p.warranty_reminder_days if p.warranty_reminder_days is not None else settings.warranty_reminder_days
        if on <= p.warranty_end <= on + timedelta(days=days) and p.warranty_reminded_for != p.warranty_end:
            a = A.objects.filter(pk=p.asset_id).exclude(status='disposed').first()
            if a:
                notify_hr(asset_branch(a, p), f'Warranty of {a.name} ({p.asset_code}) ends on {p.warranty_end:%d %b %Y}.')
                out['warranty'] += 1
            p.warranty_reminded_for = p.warranty_end
            p.save(update_fields=['warranty_reminded_for'])
    for p in AssetProfile.objects.filter(insurance_expiry__isnull=False):
        if on <= p.insurance_expiry <= on + timedelta(days=settings.warranty_reminder_days) and p.insurance_reminded_for != p.insurance_expiry:
            a = A.objects.filter(pk=p.asset_id).exclude(status='disposed').first()
            if a:
                notify_hr(asset_branch(a, p), f'Insurance of {a.name} ({p.asset_code}) expires on {p.insurance_expiry:%d %b %Y}.')
                out['insurance'] += 1
            p.insurance_reminded_for = p.insurance_expiry
            p.save(update_fields=['insurance_reminded_for'])
    for x in AllocationExtra.objects.filter(expected_return_date__lt=on, return_reminded_on__isnull=True):
        a = Allocation().objects.filter(pk=x.allocation_id, returned_date__isnull=True).select_related('asset', 'employee').first()
        if a:
            notify(a.employee, f'{a.asset.name} was due back on {x.expected_return_date:%d %b %Y}. Please return it.')
            notify_hr(asset_branch(a.asset), f'{a.asset.name} is overdue from {person(a.employee)} (due {x.expected_return_date:%d %b %Y}).')
            out['overdue_returns'] += 1
        x.return_reminded_on = on
        x.save(update_fields=['return_reminded_on'])
    return out
