"""ZEO Asset Management on the app kit (v1.33.0).

One shared register with HR → Assets: every card is an HR asset (OrganisationManager.Asset + AssetPlus profile).
Custody, transfers, verification and disposal run through the HR asset services, so payroll recovery, exit clearance and
HSE see the same assets. Links: Maintenance (assets, fault reports and their work orders), Property (units)."""
from datetime import timedelta
from decimal import Decimal

from django.apps import apps as djapps
from django.db import transaction
from django.db.models import Count, Q, Sum

from zeo.appkit.engine import Act, App, AppError, Reg, call, d, emp_name, in_branches, link_label, linked, now, today, user_name

from . import services as SV
from .models import (CONDITION, ROLES, ActivityType, SlaRule, AssetCard, AssetEventLog, AssetFileStore, AssetSequence, AssetSettings, Audit, AuditLine, CapitalCost,
                     Category, DepreciationLine, DepreciationRun, Location, Movement, Reservation)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-assets/'

app = App(
    label='ZeoAssets', slug='zeo-assets', title='Asset Management', settings_model=AssetSettings, sequence_model=AssetSequence,
    event_model=AssetEventLog, file_model=AssetFileStore, roles=ROLES, role_prefix='ast_', manager_role='ast_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'CON': 'construction', 'OG': 'construction', 'HC': 'healthcare', 'MFG': 'manufacturing', 'EDU': 'education',
                           'PM': 'property_management', 'RET': 'retail'},
    pack_models={'category': (Category, 'code'), 'activity': (ActivityType, 'code'), 'sla': (SlaRule, 'code')},
    links=(('ZeoMaintenance', 'Maintenance', 'Assets are sent to Maintenance (critical ones or all – see Settings); "Report a fault" opens a '
            'Maintenance work order on the asset, and its work orders show on the asset.'),
           ('ZeoProperty', 'Property', 'Assets and locations can be placed in Property buildings and units.')),
    page=PAGE,
)
app.settings_fields = ('currency', 'fiscal_year_start', 'capitalise_threshold', 'convention', 'label_size', 'label_title', 'checkout_days',
                       'audit_approval', 'maintenance_sync')

MGR = ('ast_manager',)
COORD = ('ast_manager', 'ast_coordinator')
FIN = ('ast_manager', 'ast_finance')
AUD = ('ast_manager', 'ast_coordinator', 'ast_auditor')
VIEW = ('ast_viewer', 'ast_finance', 'ast_auditor')
STAFF = ('ast_manager', 'ast_coordinator', 'ast_finance', 'ast_auditor', 'ast_viewer')
TO = 'choice:employee=Employee|department=Department|location=Location'
COND = 'choice:' + '|'.join(f'{k}={v}' for k, v in CONDITION)


def cur():
    return app.settings().currency


def when(states, *roles):
    return lambda o, r: o.status in states and app.any(r, *roles)


def Transfer():
    return djapps.get_model('AssetPlus', 'AssetTransfer')


def Disposal():
    return djapps.get_model('AssetPlus', 'AssetDisposal')


def Dept():
    return djapps.get_model('OrganisationManager', 'dept_master')


# ------------------------------------------------------------------ options
app.option('categories', lambda: Category.objects.filter(is_active=True), lambda x: f'{x.name} ({x.code})', ('name', 'code'), Category)
app.option('locations', lambda: Location.objects.filter(is_active=True), lambda x: x.path() + f' ({x.code})', ('code', 'name'), Location)
app.option('assets', lambda: AssetCard.objects.exclude(status='disposed'), lambda x: f'{x.code} {x.name}', ('code', 'name', 'serial_number', 'barcode'), AssetCard)
app.option('audits', lambda: Audit.objects.all(), lambda x: f'{x.number} {x.name}', ('number', 'name'), Audit)
app.option('runs', lambda: DepreciationRun.objects.all(), str, ('number',), DepreciationRun)
app.option('departments', lambda: Dept().objects.filter(dept_is_active=True).order_by('dept_name'), lambda x: x.dept_name, ('dept_name', 'dept_code'))
app.provide('assets.cards', lambda: AssetCard.objects.exclude(status='disposed'), lambda x: f'{x.code} {x.name}', ('code', 'name', 'serial_number'))
app.provide('assets.locations', lambda: Location.objects.filter(is_active=True), lambda x: x.path(), ('code', 'name'))


# ------------------------------------------------------------------ asset cards
def card_defaults(o, request):
    data = getattr(request, 'data', {}) or {}
    if not data.get('life_months') and not data.get('method'):
        SV.apply_category_defaults(o)
    if not o.capitalised_on and o.status != 'cwip':
        o.capitalised_on = o.purchase_date
    if Decimal(o.purchase_cost or 0) < Decimal(app.settings().capitalise_threshold or 0):
        o.method = 'none'


def card_check(o, request, old):
    if o.purchase_date and o.purchase_date > today():
        raise AppError('The purchase date is in the future.', 'purchase_date')
    if Decimal(o.residual_value or 0) > Decimal(o.purchase_cost or 0):
        raise AppError('The residual value is more than the cost.', 'residual_value')
    if o.parent_id and (o.parent_id == o.pk or (o.pk and o.parent.parent_id == o.pk)):
        raise AppError('An asset cannot be a part of itself.', 'parent')
    if o.ownership == 'leased' and not o.lessor:
        raise AppError('Enter the lessor of a leased asset.', 'lessor')
    if old is not None and (old.accumulated != o.accumulated or old.depreciated_to != o.depreciated_to) \
            and DepreciationLine.objects.filter(card=o, run__status='posted').exists():
        raise AppError('Depreciation has been posted for this asset. The opening balance can no longer be changed.', 'accumulated')
    if o.rfid_tag and AssetCard.objects.filter(rfid_tag__iexact=o.rfid_tag).exclude(pk=o.pk).exists():
        raise AppError('Another asset has this RFID tag.', 'rfid_tag')
    if o.barcode and AssetCard.objects.filter(barcode__iexact=o.barcode).exclude(pk=o.pk).exists():
        raise AppError('Another asset has this barcode.', 'barcode')


def card_saver(o, request, creating, old):
    with SV.hr_errors(), SV.busy():
        SV.write_through(o, request.user if request else None)
        o.save()
    o = SV.refresh(o)
    send_to_maintenance(o)
    return o


def send_to_maintenance(o, force=False):
    if not linked(app, 'ZeoMaintenance'):
        return None
    mode = app.settings().maintenance_sync
    if not force and (mode == 'none' or (mode == 'critical' and o.criticality not in ('high', 'critical'))):
        return None
    res = call(app, 'ZeoMaintenance', 'asset_upsert', mnt_id=o.mnt_asset_id, code=o.code, title=o.name, serial=o.serial_number, make=o.manufacturer,
               model_no=o.model_no, warranty_until=o.warranty_end, status='retired' if o.status == 'disposed' else 'working',
               branch_id=o.branch_id, property_unit_id=o.property_unit_id, location=o.location.path() if o.location_id else '')
    if res and res.get('id') != o.mnt_asset_id:
        AssetCard.objects.filter(pk=o.pk).update(mnt_asset_id=res['id'])
        o.mnt_asset_id = res['id']
    return res


def pending(model, o):
    return model().objects.filter(asset_id=o.hr_asset_id, status='pending').order_by('-id').first() if o.hr_asset_id else None


def card_extra(o, request):
    s = app.settings()
    p = SV.Profile().objects.filter(asset_id=o.hr_asset_id).first()
    t, dsp = pending(Transfer, o), pending(Disposal, o)
    fin = app.is_admin(request) or app.any(request, *FIN)
    out = {'qr': f'ASSET:{o.code}|SN:{o.serial_number}|ID:{o.hr_asset_id}', 'place': o.location.path() if o.location_id else '',
           'hr_page': f'/main-sidebar/asset-plus/register/{o.hr_asset_id}' if o.hr_asset_id else '',
           'pending_transfer': {'number': t.number, 'to': SV.S().party_label(t, 'to'), 'reason': t.reason} if t else None,
           'pending_disposal': {'number': dsp.number, 'method': dsp.get_method_display(), 'value': float(dsp.value), 'book_value': float(dsp.book_value)} if dsp else None,
           'hr_history': [{'text': e.summary, 'by': e.user_name, 'at': d(e.at)} for e in djapps.get_model('AssetPlus', 'AssetEvent').objects.filter(asset_id=o.hr_asset_id)[:50]]
           if o.hr_asset_id else [],
           'map': {'lat': float(o.last_latitude), 'lng': float(o.last_longitude)} if o.last_latitude is not None and o.last_longitude is not None else None,
           'floor_plan': o.location_id if (o.location_id and o.location.floor_plan) else None, 'currency': s.currency}
    if fin and o.pk:
        out['depreciation'] = [{'run': ln.run.number, 'from': d(ln.run.period_from), 'to': d(ln.run.period_to), 'amount': float(ln.amount),
                                'closing': float(ln.closing), 'status': ln.run.get_status_display()}
                               for ln in DepreciationLine.objects.filter(card=o).select_related('run').order_by('-run__period_to')[:36]]
        out['hr_book_value'] = float(SV.S().book_value(p, o.hr_asset)) if p is not None and o.hr_asset_id else None
    if o.mnt_asset_id and linked(app, 'ZeoMaintenance'):
        out['work_orders'] = call(app, 'ZeoMaintenance', 'asset_work_orders', o.mnt_asset_id) or []
    return out


# card actions
def a_check_out(o, request, f):
    to = f.choice('to', (('employee', ''), ('department', ''), ('location', '')), 'employee')
    loc = f.obj('location', Location, False, 'Location')
    due = f.dt('due_back')
    if to == 'employee' and not due and app.settings().checkout_days and f.b('tool'):
        due = today() + timedelta(days=app.settings().checkout_days)
    SV.check_out(o, request.user, to=to, employee=f.emp('employee'), department_id=f.i('department'), location=loc, due_back=due, note=f.s('note'))
    app.event('asset', o.pk, f'Checked out to {AssetCard.objects.get(pk=o.pk).holder}' + (f', due back {d(due)}' if due else ''), request.user)
    return AssetCard.objects.get(pk=o.pk)


def a_check_in(o, request, f):
    cond = f.choice('condition', CONDITION, 'healthy')
    who = o.holder
    SV.check_in(o, request.user, condition=cond, location=f.obj('location', Location, False, 'Location'), note=f.s('note'), missing_items=f.s('missing_items'))
    app.event('asset', o.pk, f'Checked in from {who} – {dict(CONDITION)[cond].lower()}' + (f', missing: {f.s("missing_items")}' if f.s('missing_items') else ''), request.user)
    return AssetCard.objects.get(pk=o.pk)


def a_move(o, request, f):
    loc = f.obj('location', Location, True, 'Location')
    SV.move(o, request.user, loc, f.s('note'))
    app.event('asset', o.pk, f'Moved to {loc.path()}', request.user)
    return AssetCard.objects.get(pk=o.pk)


def a_transfer(o, request, f):
    to = f.choice('to', (('employee', ''), ('department', ''), ('location', '')), 'employee')
    loc = f.obj('location', Location, False, 'Location')
    e = f.emp('employee')
    with SV.hr_errors():
        t = SV.S().request_transfer(o.hr_asset, request.user, to_type=to, to_employee_id=getattr(e, 'pk', None), to_department_id=f.i('department'),
                                    to_branch_id=(loc.branch_id if loc else None) or o.branch_id, to_location=loc.path() if loc else '',
                                    transfer_date=f.dt('date'), reason=f.s('reason'))
    if to == 'location' and loc:
        AssetCard.objects.filter(pk=o.pk).update(location=loc)
    app.event('asset', o.pk, f'Transfer {t.number} requested – waiting for approval', request.user)
    app.tell(app.users_with_role('ast_manager'), f'Approve transfer {t.number}', f'{o.code} {o.name}: {t.reason}', 'asset', o.pk, f'{o.code} {o.name}',
             PAGE + f'r/asset/{o.pk}', summary=f'Approve transfer {t.number} of {o.code}')
    return {'message': f'Transfer {t.number} sent for approval.'}


def a_decide_transfer(approve):
    def run(o, request, f):
        t = pending(Transfer, o)
        if t is None:
            raise AppError('No transfer is waiting for approval.')
        before = o.holder
        with SV.hr_errors():
            SV.S().decide_transfer(t, request.user, approve, f.s('note'))
        o = SV.refresh(o)
        if approve:
            SV.log_move(o, 'transfer', request.user, frm=before, to=o.holder, note=t.reason, ref=t.number)
        app.event('asset', o.pk, f'Transfer {t.number} {"approved" if approve else "rejected"}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos('asset', o.pk, 'Approve transfer')
        return o
    return run


def a_reserve(o, request, f):
    r = Reservation(card=o, employee=f.emp('employee') or app.my_emp(request), start=f.dt('start', True, 'From'), end=f.dt('end', True, 'Until'),
                    purpose=f.s('purpose') or 'Reserved', status='confirmed', decided_by=request.user)
    if r.employee is None:
        raise AppError('Choose the employee.', 'employee')
    res_check(r, request, None)
    r.number = app.next_number('reservation', 'RSV')
    r.save()
    app.event('reservation', r.pk, f'Reserved by {user_name(request.user)}', request.user)
    app.event('asset', o.pk, f'Reserved {r.number} for {emp_name(r.employee)} {d(r.start)} – {d(r.end)}', request.user)
    return {'message': f'Reservation {r.number} made.'}


def a_verify(o, request, f):
    res = f.choice('result', tuple(SV.S().VERIFY_RESULTS.items()), 'found')
    loc = f.obj('location', Location, False, 'Location')
    with SV.hr_errors():
        SV.S().verify_asset(o.hr_asset, request.user, result=res, location=loc.path() if loc else '', note=f.s('note'))
    if res == 'wrong_location' and loc:
        SV.move(o, request.user, loc, 'found at the physical check')
    AssetCard.objects.filter(pk=o.pk).update(last_audited_on=today())
    app.event('asset', o.pk, f'Physical check: {SV.S().VERIFY_RESULTS[res]}', request.user)
    return SV.refresh(o)


def a_cwip(o, request, f):
    if DepreciationLine.objects.filter(card=o, run__status='posted').exists():
        raise AppError('Depreciation has been posted for this asset – it cannot go back to construction in progress.')
    AssetCard.objects.filter(pk=o.pk).update(status='cwip', capitalised_on=None)
    app.event('asset', o.pk, 'Marked as under construction (CWIP) – no depreciation until capitalised', request.user)
    return AssetCard.objects.get(pk=o.pk)


def uncapitalised(o):
    return o.capital_costs.filter(capitalised=False)


def a_capitalise(o, request, f):
    on = f.dt('date') or today()
    add = SV.money(uncapitalised(o).aggregate(s=Sum('amount'))['s'] or 0)
    with transaction.atomic():
        cost = SV.money(o.purchase_cost) + add
        SV.Profile().objects.filter(asset_id=o.hr_asset_id).update(purchase_cost=cost)
        upd = {'purchase_cost': cost}
        if o.status == 'cwip':
            upd.update(status='available', capitalised_on=on, depreciation_start=f.dt('depreciation_start') or on)
        c = o.category
        if o.method == 'none' and c.capitalise and c.method != 'none' and cost >= SV.money(app.settings().capitalise_threshold):
            # registered at no cost (CWIP): the category's rule applies now that the asset has its cost
            upd.update(method=c.method, life_months=c.life_months, declining_rate=c.declining_rate,
                       residual_value=SV.money(cost * Decimal(c.residual_percent or 0) / 100))
            SV.Profile().objects.filter(asset_id=o.hr_asset_id).update(depreciation_method=c.method, useful_life_months=c.life_months or 36,
                                                                         salvage_value=upd['residual_value'], declining_rate=c.declining_rate,
                                                                         depreciation_start=upd.get('depreciation_start') or o.depreciation_start)
        AssetCard.objects.filter(pk=o.pk).update(**upd)
        uncapitalised(o).update(capitalised=True)
    SV.S().log_event(o.hr_asset_id, 'capitalised', f'Capitalised in Asset Management on {on:%d %b %Y}: cost {cur()} {cost}', request.user)
    app.event('asset', o.pk, f'Capitalised on {d(on)} – cost {cur()} {cost:,.2f}' + (f' (added {cur()} {add:,.2f})' if add else ''), request.user)
    return SV.refresh(AssetCard.objects.get(pk=o.pk))


def a_dispose(o, request, f):
    method = f.choice('method', Disposal().METHOD, None)
    if not method:
        raise AppError('Choose how the asset is disposed of.', 'method')
    with SV.hr_errors():
        x = SV.S().request_disposal(o.hr_asset, request.user, method=method, disposal_date=f.dt('date'), value=f.n('value', 0), buyer=f.s('buyer'), reason=f.s('reason'))
    app.event('asset', o.pk, f'Disposal {x.number} requested ({x.get_method_display().lower()}), book value {cur()} {x.book_value}', request.user)
    app.tell(app.users_with_role('ast_manager'), f'Approve disposal {x.number}', f'{o.code} {o.name}: {x.reason}', 'asset', o.pk, f'{o.code} {o.name}',
             PAGE + f'r/asset/{o.pk}', summary=f'Approve disposal {x.number} of {o.code}')
    return {'message': f'Disposal {x.number} sent for approval.'}


def a_decide_disposal(approve):
    def run(o, request, f):
        x = pending(Disposal, o)
        if x is None:
            raise AppError('No disposal is waiting for approval.')
        with SV.hr_errors():
            SV.S().decide_disposal(x, request.user, approve, f.s('note'))
        o = SV.refresh(o)
        x.refresh_from_db()
        app.event('asset', o.pk, f'Disposal {x.number} ' + (f'approved – gain / loss {cur()} {x.gain_loss}' if approve else 'rejected'), request.user)
        app.close_todos('asset', o.pk, 'Approve disposal')
        if approve:
            send_to_maintenance(o)
            Reservation.objects.filter(card=o, status__in=('requested', 'confirmed')).update(status='cancelled', note='Asset disposed')
        return o
    return run


def a_gps(o, request, f):
    lat, lng = f.n('latitude'), f.n('longitude')
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise AppError('Enter a valid latitude and longitude.', 'latitude')
    AssetCard.objects.filter(pk=o.pk).update(last_latitude=lat, last_longitude=lng, last_seen_at=now())
    SV.log_move(o, 'gps', request.user, to=f'{lat}, {lng}', note=f.s('note'))
    return AssetCard.objects.get(pk=o.pk)


def a_fault(o, request, f):
    if not linked(app, 'ZeoMaintenance'):
        raise AppError('The Maintenance app is not active or the link is switched off.')
    send_to_maintenance(o, force=True)
    o.refresh_from_db()
    res = call(app, 'ZeoMaintenance', 'request', title=f'{o.code} {o.name}: {f.s("title") or "fault"}'[:200], description=f.s('description'),
               requester=user_name(request.user), source='staff', ref=o.code, user=request.user, asset_id=o.mnt_asset_id, branch_id=o.branch_id)
    if not res:
        raise AppError('Maintenance could not take the request. Try again or create the work order in Maintenance.')
    app.event('asset', o.pk, f'Fault reported to Maintenance – work order {res["number"]}', request.user)
    return {'message': f'Work order {res["number"]} created in Maintenance.'}


def a_send_mnt(o, request, f):
    res = send_to_maintenance(o, force=True)
    if not res:
        raise AppError('The Maintenance app is not active or the link is switched off.')
    return AssetCard.objects.get(pk=o.pk)


def card_can(states, *roles):
    return lambda o, r: o.status in states and app.any(r, *roles)


R = app.reg
R(Reg('asset', AssetCard, 'Asset', 'Assets', 'Assets',
      cols=('code', 'name', 'category', 'status', 'holder', 'location', 'condition', 'criticality', 'due_back', 'book_value'),
      form=('name', 'category', 'serial_number', 'model_no', 'manufacturer', 'specification', 'criticality', 'condition', 'location', 'parent',
            'purchase_date', 'purchase_cost', 'supplier', 'invoice_no', 'purchase_order', 'warranty_end', 'ownership', 'lessor', 'lease_end',
            'barcode', 'rfid_tag', 'gps_device', 'method', 'life_months', 'residual_value', 'declining_rate', 'depreciation_start',
            'accumulated', 'depreciated_to', 'mnt_asset_id', 'property_unit_id', 'notes'),
      manage=COORD + ('ast_finance',), view=VIEW, own=(('holder_employee', 'emp'),),
      restricted=('purchase_cost', 'residual_value', 'declining_rate', 'accumulated', 'book_value', 'depreciated_to'),
      can_restricted=lambda r, o: app.is_admin(r) or app.any(r, *FIN),
      search=('code', 'name', 'serial_number', 'barcode', 'rfid_tag', 'holder', 'model_no'),
      filters=('status', 'category', 'location', 'criticality', 'condition', 'ownership', 'holder_employee'),
      title='name', subtitle='code', defaults=card_defaults, check=card_check, saver=card_saver, edit_when=lambda o: o.status != 'disposed',
      links={'mnt_asset_id': 'maintenance.assets', 'property_unit_id': 'property.units'}, extra=card_extra,
      info=('code', 'holder_employee', 'due_back', 'capitalised_on', 'last_audited_on', 'last_latitude', 'last_longitude', 'last_seen_at', 'hr_asset'),
      children=(('Movements', 'movement', 'card'), ('Reservations', 'reservation', 'card'), ('Parts and components', 'asset', 'parent'),
                ('Capital costs', 'capital_cost', 'card')),
      actions=[Act('check_out', 'Check out', a_check_out, card_can(('available',), *COORD), style='primary',
                   fields=(('to', 'Give to', TO, True), ('employee', 'Employee / custodian', 'opt:employees', False), ('department', 'Department', 'opt:departments', False),
                           ('location', 'Location', 'opt:locations', False), ('due_back', 'Due back', 'date', False), ('note', 'Note', 'text', False))),
               Act('check_in', 'Check in', a_check_in, card_can(('assigned',), *COORD), style='primary',
                   fields=(('condition', 'Condition', COND, True), ('location', 'Put it in', 'opt:locations', False),
                           ('missing_items', 'Missing accessories', 'text', False), ('note', 'Note', 'text', False))),
               Act('move', 'Move', a_move, card_can(('available', 'assigned', 'maintenance', 'cwip'), *COORD),
                   fields=(('location', 'New location', 'opt:locations', True), ('note', 'Note', 'text', False))),
               Act('transfer', 'Transfer', a_transfer, lambda o, r: o.status == 'assigned' and pending(Transfer, o) is None and app.any(r, *COORD),
                   fields=(('to', 'Transfer to', TO, True), ('employee', 'Employee', 'opt:employees', False), ('department', 'Department', 'opt:departments', False),
                           ('location', 'Location', 'opt:locations', False), ('date', 'Transfer date', 'date', False), ('reason', 'Reason', 'text', True))),
               Act('approve_transfer', 'Approve transfer', a_decide_transfer(True), lambda o, r: pending(Transfer, o) is not None and app.any(r, *MGR),
                   style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('reject_transfer', 'Reject transfer', a_decide_transfer(False), lambda o, r: pending(Transfer, o) is not None and app.any(r, *MGR),
                   style='danger', fields=(('note', 'Reason', 'text', True),)),
               Act('reserve', 'Reserve', a_reserve, card_can(('available', 'assigned', 'maintenance'), *COORD),
                   fields=(('employee', 'For', 'opt:employees', True), ('start', 'From', 'date', True), ('end', 'Until', 'date', True), ('purpose', 'Purpose', 'text', True))),
               Act('verify', 'Physical check', a_verify, card_can(('available', 'assigned', 'maintenance', 'lost'), *AUD),
                   fields=(('result', 'Result', 'choice:found=Found|damaged=Found damaged|not_found=Not found|wrong_location=Found in another place', True),
                           ('location', 'Where it was found', 'opt:locations', False), ('note', 'Note', 'text', False))),
               Act('fault', 'Report a fault', a_fault, lambda o, r: o.status not in ('disposed', 'cwip') and linked(app, 'ZeoMaintenance') and app.any(r, *STAFF),
                   fields=(('title', 'What is wrong', 'text', True), ('description', 'Details', 'textarea', False))),
               Act('send_mnt', 'Send to Maintenance', a_send_mnt, lambda o, r: not o.mnt_asset_id and o.status != 'disposed' and linked(app, 'ZeoMaintenance') and app.any(r, *COORD)),
               Act('gps', 'Record GPS position', a_gps, card_can(('available', 'assigned', 'maintenance', 'lost'), *COORD),
                   fields=(('latitude', 'Latitude', 'decimal', True), ('longitude', 'Longitude', 'decimal', True), ('note', 'Note', 'text', False))),
               Act('cwip', 'Mark as under construction', a_cwip, lambda o, r: o.status == 'available' and not o.accumulated and app.any(r, *FIN),
                   confirm='The asset will not be depreciated until it is capitalised.'),
               Act('capitalise', 'Capitalise', a_capitalise, lambda o, r: (o.status == 'cwip' or uncapitalised(o).exists()) and app.any(r, *FIN), style='primary',
                   fields=(('date', 'Capitalisation date', 'date', True), ('depreciation_start', 'Depreciation starts', 'date', False))),
               Act('dispose', 'Dispose', a_dispose, lambda o, r: o.status in ('available', 'lost', 'cwip') and pending(Disposal, o) is None and app.any(r, *FIN), style='danger',
                   fields=(('method', 'Method', 'choice:sale=Sale|scrap=Scrap|donate=Donation|write_off=Write-off', True), ('date', 'Disposal date', 'date', True),
                           ('value', 'Sale value', 'decimal', False), ('buyer', 'Buyer', 'text', False), ('reason', 'Reason', 'text', True))),
               Act('approve_disposal', 'Approve disposal', a_decide_disposal(True), lambda o, r: pending(Disposal, o) is not None and app.any(r, *MGR),
                   style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('reject_disposal', 'Reject disposal', a_decide_disposal(False), lambda o, r: pending(Disposal, o) is not None and app.any(r, *MGR),
                   style='danger', fields=(('note', 'Reason', 'text', True),))],
      help='Every asset of the company – the same assets as HR → Assets. Check out, move, transfer, reserve, check, capitalise and dispose.'))

R(Reg('movement', Movement, 'Movement', 'Movements', 'Assets', cols=('on', 'card', 'kind', 'from_text', 'to_text', 'employee', 'due_back', 'condition', 'note', 'by'),
      form=('card', 'kind', 'note'), manage=(), view=STAFF, create='none', child_of='card', search=('card__code', 'card__name', 'from_text', 'to_text', 'note'),
      filters=('kind', 'card', 'employee'), title='kind', subtitle='card', edit_when=lambda o: False, files=False,
      help='Every check-out, check-in, move, transfer and GPS position (read only).'))


# ------------------------------------------------------------------ reservations
def res_defaults(o, request):
    if not o.employee_id:
        o.employee = app.my_emp(request)
    if app.any(request, *COORD):
        o.status, o.decided_by = 'confirmed', request.user


def res_check(o, request, old):
    if o.employee_id is None:
        raise AppError('Choose who the asset is for.', 'employee')
    if o.end < o.start:
        raise AppError('"Until" is before "From".', 'end')
    if o.card.status in ('disposed', 'lost', 'cwip'):
        raise AppError(f'This asset is {o.card.get_status_display().lower()} and cannot be reserved.', 'card')
    clash = Reservation.objects.filter(card=o.card, status__in=('requested', 'confirmed', 'checked_out'), start__lte=o.end, end__gte=o.start).exclude(pk=o.pk).first()
    if clash:
        raise AppError(f'Already reserved {d(clash.start)} – {d(clash.end)} for {emp_name(clash.employee)} ({clash.number}).', 'start')


def res_saver(o, request, creating, old):
    o.save()
    if creating and o.status == 'requested':
        app.tell(app.users_with_role('ast_coordinator'), f'Reservation {o.number} to confirm', f'{o.card} for {emp_name(o.employee)} {d(o.start)} – {d(o.end)}: {o.purpose}',
                 'reservation', o.pk, o.number, PAGE + f'r/reservation/{o.pk}', summary=f'Confirm reservation {o.number}')
    return o


def r_status(new, text):
    def run(o, request, f):
        o.status, o.decided_by = new, request.user
        o.note = f.s('note') or o.note
        o.save()
        app.event('reservation', o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos('reservation', o.pk)
        e = o.employee
        app.tell_emp(e, f'Reservation {o.number}: {o.get_status_display()}', f'{o.card} {d(o.start)} – {d(o.end)}. {o.note}', 'reservation', o.pk, o.number,
                     PAGE + f'r/reservation/{o.pk}')
        return o
    return run


def r_check_out(o, request, f):
    SV.check_out(o.card, request.user, to='employee', employee=o.employee, due_back=o.end, note=f'Reservation {o.number}: {o.purpose}', reservation=o)
    app.event('reservation', o.pk, 'Checked out', request.user)
    return Reservation.objects.get(pk=o.pk)


R(Reg('reservation', Reservation, 'Reservation', 'Reservations', 'Assets', cols=('number', 'card', 'employee', 'start', 'end', 'purpose', 'status'),
      form=('card', 'employee', 'start', 'end', 'purpose'), number=('reservation', 'RSV'), manage=COORD, view=VIEW, create='all', own=(('employee', 'emp'),),
      search=('number', 'card__code', 'card__name', 'purpose'), filters=('status', 'card', 'employee'), subtitle='card', defaults=res_defaults,
      check=res_check, saver=res_saver, edit_when=lambda o: o.status == 'requested', files=False,
      actions=[Act('confirm', 'Confirm', r_status('confirmed', 'Confirmed'), when(('requested',), *COORD), style='primary'),
               Act('reject', 'Reject', r_status('rejected', 'Rejected'), when(('requested',), *COORD), style='danger', fields=(('note', 'Reason', 'text', True),)),
               Act('check_out', 'Check out now', r_check_out, lambda o, r: o.status == 'confirmed' and o.card.status == 'available' and app.any(r, *COORD), style='primary'),
               Act('cancel', 'Cancel', r_status('cancelled', 'Cancelled'),
                   lambda o, r: o.status in ('requested', 'confirmed') and (app.any(r, *COORD) or getattr(app.my_emp(r), 'pk', 0) == o.employee_id),
                   fields=(('note', 'Reason', 'text', False),))],
      help='Book an asset for a period (pool cars, projectors, tools, test equipment). Bookings cannot overlap.'))


# ------------------------------------------------------------------ audits (physical verification)
def audit_scope(o):
    qs = AssetCard.objects.exclude(status__in=('disposed', 'cwip'))
    if o.location_id:
        qs = qs.filter(location_id__in=o.location.descendants())
    elif o.branch_id:
        qs = qs.filter(Q(branch_id=o.branch_id) | Q(branch__isnull=True))
    cats = list(o.categories.values_list('pk', flat=True))
    if cats:
        qs = qs.filter(category_id__in=cats)
    return qs


def au_start(o, request, f):
    cards = list(audit_scope(o).select_related('location'))
    if not cards:
        raise AppError('No assets are in the scope of this audit. Check the location, branch and categories.')
    with transaction.atomic():
        AuditLine.objects.bulk_create([AuditLine(audit=o, card=c, expected_location_id=c.location_id, holder_id=c.holder_employee_id) for c in cards],
                                      ignore_conflicts=True)
        o.status, o.started_at = 'counting', now()
        o.save()
        SV.audit_counts(o)
    link = PAGE + f'audit/{o.pk}/count'
    for e in o.auditors.all():
        app.tell_emp(e, f'Asset count {o.number}: {o.name}', f'Please count the assets by {d(o.due_date) or "the due date"}. Open the count screen and scan each asset.',
                     'audit', o.pk, o.number, link, summary=f'Count assets – {o.number} {o.name}')
    asked = 0
    if o.self_check:
        for ln in AuditLine.objects.filter(audit=o, holder__isnull=False).select_related('holder', 'card'):
            app.tell_emp(ln.holder, f'Please confirm you have {ln.card.code}', f'{ln.card.code} {ln.card.name} is recorded with you. Confirm in My work.',
                         'audit_line', ln.pk, f'{ln.card.code} {ln.card.name}', PAGE + f'r/audit_line/{ln.pk}', summary=f'Confirm you have {ln.card.code} {ln.card.name}')
            asked += 1
    app.event('audit', o.pk, f'Counting started: {len(cards)} asset(s) expected' + (f', {asked} holder(s) asked to confirm' if asked else ''), request.user)
    return o


def au_submit(o, request, f):
    o.lines.filter(result='pending').update(result='missing')
    o.status = 'review'
    o.save()
    SV.audit_counts(o)
    app.event('audit', o.pk, f'Count finished: {o.found} found, {o.missing} missing, {o.misplaced} misplaced, {o.unexpected} not on the list', request.user)
    if not app.settings().audit_approval:
        return au_close(o, request, f)
    app.tell(app.users_with_role('ast_manager'), f'Approve asset count {o.number}', f'{o.found} found, {o.missing} missing, {o.misplaced} misplaced',
             'audit', o.pk, o.number, PAGE + f'r/audit/{o.pk}', summary=f'Approve asset count {o.number}')
    return o


def au_close(o, request, f):
    n = 0
    for ln in o.lines.select_related('card', 'found_location'):
        if ln.result == 'pending':
            continue
        SV.verify_from_audit(ln.card, request.user, ln.result, ln.found_location, f'audit {o.number}' + (f': {ln.note}' if ln.note else ''))
        if ln.result in ('misplaced', 'unexpected') and ln.move_here and ln.found_location_id and ln.found_location_id != ln.card.location_id:
            SV.move(ln.card, request.user, ln.found_location, f'audit {o.number}')
        if ln.condition and ln.condition != ln.card.condition and ln.result != 'missing':
            SV.HRAsset().objects.filter(pk=ln.card.hr_asset_id).update(condition=ln.condition)
        AssetCard.objects.filter(pk=ln.card_id).update(last_audited_on=today())
        SV.refresh(ln.card)
        n += 1
    o.status, o.closed_at, o.closed_by = 'closed', now(), request.user
    o.save()
    app.close_todos('audit', o.pk)
    app.event('audit', o.pk, f'Closed by {user_name(request.user)} – {n} result(s) written to the assets' + (f'. {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def au_reopen(o, request, f):
    o.lines.filter(result='missing', counted_at__isnull=True).update(result='pending')
    o.status = 'counting'
    o.save()
    SV.audit_counts(o)
    app.event('audit', o.pk, 'Counting reopened' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def is_auditor(o, request):
    me = app.my_emp(request)
    return me is not None and o.auditors.filter(pk=me.pk).exists()


def audit_extra(o, request):
    by_loc = list(o.lines.values('expected_location__name').annotate(n=Count('id'), done=Count('id', filter=~Q(result='pending'))).order_by('expected_location__name'))
    return {'count_page': PAGE + f'audit/{o.pk}/count', 'can_count': o.status == 'counting' and (app.any(request, *AUD) or is_auditor(o, request)),
            'progress': [{'location': x['expected_location__name'] or 'No location', 'expected': x['n'], 'counted': x['done']} for x in by_loc]}


R(Reg('audit', Audit, 'Asset count', 'Asset counts', 'Verification',
      cols=('number', 'name', 'location', 'due_date', 'status', 'expected', 'found', 'missing', 'misplaced', 'unexpected'),
      form=('name', 'location', 'categories', 'auditors', 'due_date', 'blind', 'self_check', 'note'), number=('audit', 'AUD'), manage=COORD, view=VIEW,
      search=('number', 'name'), filters=('status', 'location'), title='name', subtitle='number', edit_when=lambda o: o.status == 'draft',
      scope_fn=lambda qs, r: qs if app.any(r, *STAFF) or app.is_admin(r) else qs.filter(auditors=app.my_emp(r)),
      info=('damaged', 'started_at', 'closed_at', 'closed_by'), extra=audit_extra, children=(('Lines', 'audit_line', 'audit'),),
      actions=[Act('start', 'Start counting', au_start, when(('draft',), *COORD), style='primary',
                   confirm='The list of expected assets is taken now. Auditors and holders are told.'),
               Act('submit', 'Finish counting', au_submit, lambda o, r: o.status == 'counting' and (app.any(r, *AUD) or is_auditor(o, r)), style='primary',
                   confirm='Assets not counted yet will be marked missing.'),
               Act('close', 'Approve and close', au_close, when(('review',), *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('reopen', 'Reopen counting', au_reopen, when(('review',), *MGR), fields=(('note', 'Why', 'text', False),)),
               Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), o)[-1], when(('draft', 'counting'), *COORD), style='danger')],
      help='Physical verification: choose the place and categories, start, scan every asset, approve. Results go to the assets and to HR.'))


def line_saver(o, request, creating, old):
    if o.result != 'pending' and (old is None or old.result != o.result):
        o.counted_at, o.counted_by = now(), request.user
    if creating and not o.expected_location_id:
        o.result = o.result if o.result != 'pending' else 'unexpected'
    o.save()
    SV.audit_counts(o.audit)
    return o


def line_confirm(o, request, f):
    o.self_confirmed = True
    if o.result == 'pending':
        o.result, o.counted_at, o.counted_by = 'found', now(), request.user
    o.note = (f.s('note') or o.note)[:255]
    o.save()
    SV.audit_counts(o.audit)
    app.close_todos('audit_line', o.pk)
    app.event('audit', o.audit_id, f'{o.card.code} confirmed by the holder {emp_name(o.holder)}', request.user)
    return o


def line_scope(qs, request):
    if app.any(request, *COORD) or app.is_admin(request):
        return qs
    return qs.exclude(audit__blind=True, result='pending', holder__isnull=True)


R(Reg('audit_line', AuditLine, 'Count line', 'Count lines', 'Verification',
      cols=('card', 'expected_location', 'holder', 'result', 'found_location', 'condition', 'self_confirmed', 'counted_at'),
      form=('audit', 'card', 'result', 'found_location', 'condition', 'move_here', 'note'), manage=AUD, view=VIEW, own=(('holder', 'emp'),), child_of='audit',
      search=('card__code', 'card__name', 'note'), filters=('result', 'audit', 'expected_location'), title='card', subtitle='result', saver=line_saver,
      edit_when=lambda o: o.audit.status == 'counting', files=False, scope_fn=line_scope, info=('counted_by',),
      actions=[Act('confirm', 'I have this asset', line_confirm,
                   lambda o, r: o.audit.status == 'counting' and not o.self_confirmed and getattr(app.my_emp(r), 'pk', 0) == o.holder_id, style='primary',
                   fields=(('note', 'Remarks', 'text', False),))],
      help='One asset in a count: found, misplaced, damaged, missing, or found here but not on the list.'))


# ------------------------------------------------------------------ capital costs (CWIP) and depreciation
R(Reg('capital_cost', CapitalCost, 'Capital cost', 'Capital costs', 'Finance', cols=('card', 'date', 'description', 'supplier', 'invoice_no', 'amount', 'capitalised'),
      form=('card', 'date', 'description', 'supplier', 'invoice_no', 'amount'), manage=FIN, view=VIEW, child_of='card',
      search=('description', 'supplier', 'invoice_no', 'card__code'), filters=('capitalised', 'card'), title='description', subtitle='card',
      edit_when=lambda o: not o.capitalised, deletable=True,
      help='Costs of an asset under construction, or additions to an asset. They join the cost when the asset is capitalised.'))


def run_check(o, request, old):
    if o.period_to < o.period_from:
        raise AppError('"To" is before "From".', 'period_to')
    if o.period_to > SV.month_end(today()):
        raise AppError('The period cannot end after this month.', 'period_to')


def run_cards(o):
    qs = AssetCard.objects.exclude(status__in=('disposed', 'cwip')).exclude(method='none').select_related('category')
    if o.branch_id:
        qs = qs.filter(branch_id=o.branch_id)
    return qs


def dep_calculate(o, request, f):
    s = app.settings()
    lines = []
    for c in run_cards(o):
        months, opening, amount, closing = SV.depreciation_for(c, o.period_from, o.period_to, s)
        if amount <= 0:
            continue
        lines.append(DepreciationLine(run=o, card=c, category=c.category, months=months, opening=opening, amount=amount, closing=closing,
                                      gl_expense=c.category.gl_expense, gl_accumulated=c.category.gl_accumulated))
    with transaction.atomic():
        o.lines.all().delete()
        DepreciationLine.objects.bulk_create(lines)
        o.assets, o.total = len(lines), SV.money(sum((x.amount for x in lines), Decimal(0)))
        o.save()
    app.event('depreciation', o.pk, f'Calculated: {o.assets} asset(s), {cur()} {o.total:,.2f}', request.user)
    return o


def dep_post(o, request, f):
    if not o.lines.exists():
        raise AppError('Calculate the run first – there is nothing to post.')
    with transaction.atomic():
        for ln in o.lines.select_related('card'):
            c = AssetCard.objects.select_for_update().get(pk=ln.card_id)
            if c.depreciated_to and c.depreciated_to >= o.period_from:
                raise AppError(f'{c.code} is already depreciated up to {d(c.depreciated_to)}. Calculate the run again.')
            c.accumulated = SV.money(c.accumulated) + ln.amount
            c.book_value, c.depreciated_to = ln.closing, o.period_to
            c.save(update_fields=['accumulated', 'book_value', 'depreciated_to'])
        o.status, o.posted_at, o.posted_by = 'posted', now(), request.user
        o.save()
    app.event('depreciation', o.pk, f'Posted and locked by {user_name(request.user)}: {cur()} {o.total:,.2f}', request.user)
    return o


def dep_reverse(o, request, f):
    later = DepreciationLine.objects.filter(card_id__in=o.lines.values('card_id'), run__status='posted', run__period_to__gt=o.period_to).exists()
    if later:
        raise AppError('A later run is posted for some of these assets. Reverse the later run first.')
    with transaction.atomic():
        for ln in o.lines.select_related('card'):
            c = AssetCard.objects.select_for_update().get(pk=ln.card_id)
            c.accumulated = max(SV.money(c.accumulated) - ln.amount, Decimal(0))
            c.book_value = SV.money(c.purchase_cost) - c.accumulated
            prev = DepreciationLine.objects.filter(card=c, run__status='posted').exclude(run=o).order_by('-run__period_to').first()
            c.depreciated_to = prev.run.period_to if prev else None
            c.save(update_fields=['accumulated', 'book_value', 'depreciated_to'])
        o.status = 'reversed'
        o.note = (f.s('note') or o.note)[:255]
        o.save()
    app.event('depreciation', o.pk, f'Reversed by {user_name(request.user)}' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def journal(o):
    """Journal lines grouped by account: debit expense, credit accumulated depreciation."""
    rows = {}
    for ln in o.lines.select_related('category'):
        key = (ln.gl_expense or '(expense account not set)', ln.gl_accumulated or '(accumulated account not set)', ln.category.name if ln.category else '')
        rows[key] = rows.get(key, Decimal(0)) + ln.amount
    return [{'debit': k[0], 'credit': k[1], 'category': k[2], 'amount': float(v)} for k, v in sorted(rows.items())]


R(Reg('depreciation', DepreciationRun, 'Depreciation run', 'Depreciation runs', 'Finance',
      cols=('number', 'period_from', 'period_to', 'status', 'assets', 'total', 'posted_at', 'posted_by'),
      form=('period_from', 'period_to', 'note'), number=('depreciation', 'DEP'), manage=FIN, view=('ast_viewer', 'ast_auditor'),
      search=('number', 'note'), filters=('status',), subtitle='status', check=run_check, edit_when=lambda o: o.status == 'draft', files=False,
      extra=lambda o, r: {'journal': journal(o), 'journal_url': f'/zeo-assets/api/depreciation/{o.pk}/journal/'},
      children=(('Assets', 'dep_line', 'run'),),
      actions=[Act('calculate', 'Calculate', dep_calculate, when(('draft',), *FIN), style='primary'),
               Act('post', 'Post and lock', dep_post, lambda o, r: o.status == 'draft' and o.assets > 0 and app.any(r, *FIN), style='primary',
                   confirm='The depreciation is added to the assets and the run is locked.'),
               Act('reverse', 'Reverse', dep_reverse, when(('posted',), *MGR), style='danger', fields=(('note', 'Why', 'text', True),))],
      help='Monthly or yearly depreciation: calculate, check, post (locks the run) and export the journal for the accounts.'))
R(Reg('dep_line', DepreciationLine, 'Depreciation line', 'Depreciation lines', 'Finance',
      cols=('card', 'category', 'months', 'opening', 'amount', 'closing', 'gl_expense', 'gl_accumulated'), form=('run', 'card'), manage=(), view=FIN + VIEW,
      create='none', child_of='run', search=('card__code', 'card__name'), filters=('run', 'category'), title='card', subtitle='run',
      edit_when=lambda o: False, files=False))


# ------------------------------------------------------------------ setup: locations, categories
def coded(kind, prefix):
    def saver(o, request, creating, old):
        if not o.code:
            o.code = app.next_number(kind, prefix)
        if type(o).objects.filter(code__iexact=o.code).exclude(pk=o.pk).exists():
            raise AppError('This code is already used.', 'code')
        if kind == 'location' and o.parent_id and o.pk and o.pk in o.parent.descendants():
            raise AppError('A place cannot be inside itself.', 'parent')
        o.save()
        return o
    return saver


def loc_extra(o, request):
    return {'path': o.path(), 'has_plan': bool(o.floor_plan), 'plan_page': PAGE + f'floor-plan/{o.pk}',
            'assets_here': AssetCard.objects.filter(location_id__in=o.descendants()).exclude(status='disposed').count()}


R(Reg('location', Location, 'Location', 'Locations', 'Setup', cols=('code', 'name', 'kind', 'parent', 'custodian', 'is_active'),
      form=('code', 'name', 'kind', 'parent', 'address', 'latitude', 'longitude', 'custodian', 'property_building_id', 'property_unit_id', 'mnt_location_id', 'is_active'),
      manage=COORD, view=STAFF, search=('code', 'name', 'address'), filters=('kind', 'is_active', 'parent'), title='name', subtitle='code',
      saver=coded('location', 'LOC'), extra=loc_extra,
      links={'property_unit_id': 'property.units', 'property_building_id': 'property.buildings', 'mnt_location_id': 'maintenance.locations'},
      children=(('Places inside', 'location', 'parent'), ('Assets', 'asset', 'location')),
      help='Sites, buildings, floors, rooms and stores. Add a floor plan to place assets on it.'))


def cat_saver(o, request, creating, old):
    o.save()
    with SV.hr_errors():
        SV.ensure_hr_type(o)
    return o


R(Reg('category', Category, 'Category', 'Categories', 'Setup',
      cols=('code', 'name', 'parent', 'method', 'life_months', 'residual_percent', 'capitalise', 'requires_serial', 'is_active', 'review_status'),
      form=('code', 'name', 'parent', 'code_prefix', 'capitalise', 'method', 'life_months', 'residual_percent', 'declining_rate', 'gl_asset', 'gl_accumulated',
            'gl_expense', 'gl_disposal', 'requires_serial', 'audit_every_months', 'is_active'),
      manage=FIN, view=STAFF, search=('code', 'name'), filters=('is_active', 'review_status', 'method'), title='name', subtitle='code', review=True,
      deletable=True, files=False, saver=cat_saver, info=('hr_type',),
      help='Asset categories with their depreciation rule and accounts. Each category is an HR asset type.'))


# ------------------------------------------------------------------ hooks other apps call (only while linked)
def h_card_of(hr_asset_id):
    c = AssetCard.objects.filter(hr_asset_id=hr_asset_id).first()
    return {'id': c.pk, 'code': c.code, 'name': c.name, 'status': c.get_status_display(), 'page': PAGE + f'r/asset/{c.pk}'} if c else None


def h_by_mnt(mnt_asset_id):
    c = AssetCard.objects.filter(mnt_asset_id=mnt_asset_id).first()
    return h_card_of(c.hr_asset_id) if c else None


app.hooks.update({'card_of': h_card_of, 'card_of_maintenance_asset': h_by_mnt})


# ------------------------------------------------------------------ dashboard, my work, daily
def requests_pending():
    try:
        AR = djapps.get_model('OrganisationManager', 'AssetRequest')
        return AR.objects.filter(status='pending')
    except Exception:
        return None


def dashboard(request):
    cards = app.regs['asset'].scope(request)
    live = cards.exclude(status='disposed')
    t = today()
    overdue = live.filter(status='assigned', due_back__lt=t)
    by_status = dict(live.values_list('status').annotate(n=Count('id')))
    cats = live.values('category__name').annotate(n=Count('id')).order_by('-n')[:8]
    fin = app.is_admin(request) or app.any(request, *FIN)
    out_cards = [{'label': 'Assets', 'value': live.count(), 'link': 'r/asset'},
                 {'label': 'Checked out', 'value': by_status.get('assigned', 0), 'link': 'r/asset?status=assigned'},
                 {'label': 'Overdue returns', 'value': overdue.count(), 'tone': 'bad' if overdue.exists() else 'ok', 'link': 'r/asset?status=assigned'},
                 {'label': 'Under maintenance', 'value': by_status.get('maintenance', 0), 'link': 'r/asset?status=maintenance'},
                 {'label': 'Lost / stolen', 'value': by_status.get('lost', 0), 'tone': 'bad' if by_status.get('lost') else '', 'link': 'r/asset?status=lost'},
                 {'label': 'Counts in progress', 'value': in_branches(Audit.objects.all(), request).filter(status__in=('counting', 'review')).count(), 'link': 'r/audit?open=1'}]
    if fin:
        nbv = live.aggregate(c=Sum('purchase_cost'), b=Sum('book_value'))
        out_cards.append({'label': f'Net book value ({cur()})', 'value': f'{float(nbv["b"] or 0):,.0f}', 'hint': f'cost {float(nbv["c"] or 0):,.0f}'})
        out_cards.append({'label': 'Under construction', 'value': by_status.get('cwip', 0), 'link': 'r/asset?status=cwip'})
    reqs = requests_pending()
    lists = [{'title': 'Overdue returns', 'kind': 'asset', 'rows': [{'id': c.pk, 'title': f'{c.code} {c.name}', 'sub': c.holder, 'badge': d(c.due_back)}
                                                                    for c in overdue.order_by('due_back')[:10]]},
             {'title': 'Warranty ending in 30 days', 'kind': 'asset', 'rows': [{'id': c.pk, 'title': f'{c.code} {c.name}', 'sub': c.supplier, 'badge': d(c.warranty_end)}
                                                                               for c in live.filter(warranty_end__gte=t, warranty_end__lte=t + timedelta(days=30)).order_by('warranty_end')[:10]]},
             {'title': 'Leases ending in 60 days', 'kind': 'asset', 'rows': [{'id': c.pk, 'title': f'{c.code} {c.name}', 'sub': c.lessor, 'badge': d(c.lease_end)}
                                                                             for c in live.filter(ownership='leased', lease_end__lte=t + timedelta(days=60)).order_by('lease_end')[:10]]},
             {'title': 'Reservations starting this week', 'kind': 'reservation',
              'rows': [{'id': r.pk, 'title': f'{r.number} {r.card.code} {r.card.name}', 'sub': emp_name(r.employee), 'badge': d(r.start)}
                       for r in in_branches(Reservation.objects.all(), request).filter(status__in=('requested', 'confirmed'), start__lte=t + timedelta(days=7), end__gte=t).select_related('card', 'employee')[:10]]}]
    if reqs is not None:
        out_cards.append({'label': 'Employee requests waiting (HR)', 'value': reqs.count(), 'hint': 'approved in HR → Assets → Requests'})
    return {'cards': out_cards,
            'bars': [{'title': 'Assets by status', 'items': [{'label': lbl, 'value': by_status.get(k, 0)} for k, lbl in AssetCard._meta.get_field('status').choices if k != 'disposed']},
                     {'title': 'Assets by category', 'items': [{'label': x['category__name'] or '-', 'value': x['n']} for x in cats]}],
            'lists': lists}


def my_work(request):
    me = app.my_emp(request)
    out = []
    if me is not None:
        out.append({'title': 'Assets with me', 'kind': 'asset', 'rows': [{'id': c.pk, 'title': f'{c.code} {c.name}', 'sub': c.get_condition_display(), 'badge': d(c.due_back) if c.due_back else ''}
                                                                          for c in AssetCard.objects.filter(holder_employee=me).exclude(status='disposed')[:50]]})
        out.append({'title': 'My reservations', 'kind': 'reservation', 'rows': [{'id': r.pk, 'title': f'{r.number} {r.card.code} {r.card.name}', 'sub': f'{d(r.start)} – {d(r.end)}', 'badge': r.get_status_display()}
                                                                                for r in Reservation.objects.filter(employee=me, status__in=('requested', 'confirmed', 'checked_out')).select_related('card')[:30]]})
        out.append({'title': 'Please confirm you have these', 'kind': 'audit_line',
                    'rows': [{'id': ln.pk, 'title': f'{ln.card.code} {ln.card.name}', 'sub': ln.audit.name, 'badge': d(ln.audit.due_date) if ln.audit.due_date else ''}
                             for ln in AuditLine.objects.filter(holder=me, audit__status='counting', audit__self_check=True, self_confirmed=False).select_related('card', 'audit')[:30]]})
        out.append({'title': 'Counts I do', 'kind': 'audit', 'rows': [{'id': a.pk, 'title': f'{a.number} {a.name}', 'sub': f'{a.found} of {a.expected} counted', 'badge': d(a.due_date) if a.due_date else ''}
                                                                      for a in Audit.objects.filter(auditors=me, status='counting')[:20]]})
    if app.any(request, *COORD):
        out.append({'title': 'Reservations to confirm', 'kind': 'reservation', 'rows': [{'id': r.pk, 'title': f'{r.number} {r.card.code}', 'sub': emp_name(r.employee), 'badge': d(r.start)}
                                                                                       for r in in_branches(Reservation.objects.all(), request).filter(status='requested').select_related('card', 'employee')[:30]]})
    return out


def daily():
    t = today()
    out = {'imported': SV.import_all(), 'overdue': 0}
    overdue = AssetCard.objects.filter(status='assigned', due_back__lt=t)
    out['overdue'] = overdue.count()
    if out['overdue']:
        for u in app.users_with_role('ast_coordinator'):
            app.todo(u, f'{out["overdue"]} asset(s) overdue for return', 'asset', 0, 'Assets', PAGE + 'r/asset?status=assigned')
    for r in Reservation.objects.filter(status='confirmed', end__lt=t):
        r.status, r.note = 'cancelled', 'Not collected'
        r.save(update_fields=['status', 'note'])
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily

from . import app2  # noqa: E402,F401  v1.34.0 release 2: maintenance, helpdesk, spare parts
