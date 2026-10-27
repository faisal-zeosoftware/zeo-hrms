"""v1.40.0 – complete property management (Property Management SRS, phases 1 and 2), on top of app.py:
hierarchy (projects, floors, sub-units, co-owners), unit generator, lease rent rules (rent-free, escalation, stepped rent, custom
schedules, extra charges, amendments, joint tenants), commercial terms (several units, reviews, break options, turnover rent,
fit-out, allocations), sale statuses (approval, pending completion, completed, co-buyers, cancellation refunds), reservation
approval and waiting list, refunds and deposit control, post-dated cheque improvements and deposit slips, collections
(reminders, escalation, promises, disputes, late fees, settlement plans), recurring charges and credit notes, document control
and printed documents, service charges / CAM budgets, meters and utility billing, listings with a public page and portal feeds,
commission rules and brokers, inspections with room-wise findings and a key register, vendors and purchase orders, approvals,
announcements and portal requests, messaging templates (e-mail / SMS / WhatsApp), country behaviour (AE, KW, OM, QA, SA, IN).
Everything works standalone; CRM, Maintenance, Accounting and Automation are used when linked."""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum

from zeo.appkit.engine import Act, AppError, Reg, add_months, call, d, emp_name, in_branches, linked, today, user_name

from . import app as A
from .app import (ALL_STAFF, DEALS, FIN, LEASING, MGR, PAGE, SALES, VIEW, ZERO, active_lease, app, charge, cur, make_bill, money, post_receipt,
                  refresh_bill, set_unit, when)
from .models import (Announcement, Bill, Broker, Building, ChargeType, Cheque, Commission, CommissionRule, DepositSlip, DocTemplate, Document, Floor,
                     Inspection, InspectionTemplate, KeyItem, Lease, Listing, MessageTemplate, Meter, MeterReading, MoveOut, Owner, OwnerStatement,
                     Party, PortalRequest, Project, PurchaseOrder, Receipt, RecurringCharge, Refund, Reservation, SaleContract, ServiceChargeBudget,
                     SettlementPlan, Unit, Vendor, WaitingList)

R = app.reg
regs = app.regs

# ------------------------------------------------------------------ country behaviour
COUNTRY = {
    'AE': {'name': 'United Arab Emirates', 'registration': 'Ejari / Tawtheeq no.', 'ids': ['Emirates ID', 'Passport', 'Trade licence'], 'vat': 5, 'currency': 'AED',
           'notice_days': 90, 'deposit_percent': 5, 'late_fee_percent': 0, 'listing_permit': 'RERA permit no.'},
    'KW': {'name': 'Kuwait', 'registration': 'Municipality contract no.', 'ids': ['Civil ID', 'Passport', 'Commercial licence'], 'vat': 0, 'currency': 'KWD',
           'notice_days': 90, 'deposit_percent': 8.33, 'late_fee_percent': 0, 'listing_permit': ''},
    'OM': {'name': 'Oman', 'registration': 'Municipality lease registration no.', 'ids': ['Resident card', 'Passport', 'Commercial registration'], 'vat': 5, 'currency': 'OMR',
           'notice_days': 90, 'deposit_percent': 8.33, 'late_fee_percent': 0, 'listing_permit': ''},
    'QA': {'name': 'Qatar', 'registration': 'Lease registration no.', 'ids': ['QID', 'Passport', 'Commercial registration'], 'vat': 0, 'currency': 'QAR',
           'notice_days': 60, 'deposit_percent': 8.33, 'late_fee_percent': 0, 'listing_permit': ''},
    'SA': {'name': 'Saudi Arabia', 'registration': 'Ejar contract no.', 'ids': ['National ID / Iqama', 'Passport', 'Commercial registration'], 'vat': 15, 'currency': 'SAR',
           'notice_days': 60, 'deposit_percent': 8.33, 'late_fee_percent': 0, 'listing_permit': 'REGA licence no.'},
    'IN': {'name': 'India', 'registration': 'Registered lease deed no.', 'ids': ['Aadhaar', 'PAN', 'Passport', 'GSTIN'], 'vat': 18, 'currency': 'INR',
           'notice_days': 30, 'deposit_percent': 16.67, 'late_fee_percent': 0, 'listing_permit': 'RERA registration no.'},
}


def country():
    s = app.settings()
    c = (s.country or '').upper()
    if c not in COUNTRY:
        try:
            from ZeoMasters.seed import country_code
            c = country_code()
        except Exception:
            c = 'AE'
    return c if c in COUNTRY else 'AE'


def cinfo():
    return COUNTRY[country()]


def party_people(p):
    """Users to notify in the portal for a tenant / buyer."""
    return [p.portal_user] if p.portal_user_id and p.portal_user.is_active else []


def tell_party(p, event, subject, body, ctx=None, model='', obj_id=0, label='', page=''):
    from .messaging import notify
    notify(event, p, subject, body, ctx or {}, model, obj_id, label, page)


def managers():
    return app.users_with_role('prop_manager')


def approval_todo(title, text, model, pk, number, roles=MGR):
    users = []
    for r in roles:
        users += app.users_with_role(r)
    app.tell(list({u.pk: u for u in users}.values()), title, text, model, pk, number, PAGE + f'r/{model}/{pk}', summary=title)


def pct_below(asking, agreed):
    if not asking or agreed is None:
        return ZERO
    return money((Decimal(asking) - Decimal(agreed)) * 100 / Decimal(asking))


# ------------------------------------------------------------------ options
app.option('projects', lambda: Project.objects.filter(is_active=True), lambda x: x.name, ('name', 'number'), Project)
app.option('floors', lambda: Floor.objects.select_related('building'), lambda x: f'{x.building.name} – {x.name or "Floor " + str(x.no)}', ('name', 'building__name'), Floor)
app.option('brokers', lambda: Broker.objects.filter(is_active=True), lambda x: x.name + (f' ({x.company_name})' if x.company_name else ''), ('name', 'company_name'), Broker)
app.option('commission-rules', lambda: CommissionRule.objects.filter(is_active=True), lambda x: x.name, ('name',), CommissionRule)
app.option('vendors', lambda: Vendor.objects.exclude(status='inactive'), lambda x: x.name + (' · BLOCKED' if x.status == 'blocked' else ''), ('name', 'trade'), Vendor)
app.option('meters', lambda: Meter.objects.filter(is_active=True).select_related('unit__building', 'building'),
           lambda x: f'{x.get_kind_display()} {x.meter_no} – ' + (x.unit.label if x.unit_id else (x.building.name + ' (shared)' if x.building_id else '')), ('meter_no',), Meter)
app.option('inspection-templates', lambda: InspectionTemplate.objects.filter(is_active=True), lambda x: x.name, ('name',), InspectionTemplate)
app.option('inspections', lambda: Inspection.objects.select_related('unit__building'), lambda x: f'{x.number} {x.get_kind_display()} {x.unit.label} {d(x.scheduled_on)}', ('number',), Inspection)
app.option('budgets', lambda: ServiceChargeBudget.objects.select_related('building'), lambda x: f'{x.number} {x.building.name} {x.year}', ('number',), ServiceChargeBudget)
app.option('cheques-in-hand', lambda: Cheque.objects.filter(status='received').select_related('party'), lambda x: f'#{x.cheque_no} {x.party.name} {x.amount} ({d(x.cheque_date)})', ('cheque_no',))
app.option('doc-templates', lambda: DocTemplate.objects.filter(is_active=True), lambda x: x.name, ('name',), DocTemplate)
app.option('settlements', lambda: SettlementPlan.objects.all(), lambda x: x.number, ('number',), SettlementPlan)
app.option('readings', lambda: MeterReading.objects.select_related('meter'), lambda x: f'{x.meter.meter_no} {d(x.read_on)} {x.reading}', ('meter__meter_no',), MeterReading)
app.option('recurring', lambda: RecurringCharge.objects.all(), lambda x: f'{x.number} {x.description}', ('number',), RecurringCharge)
app.option('refunds', lambda: Refund.objects.all(), lambda x: x.number, ('number',), Refund)
app.option('listings', lambda: Listing.objects.select_related('unit__building'), lambda x: f'{x.number} {x.title}', ('number', 'title'), Listing)
app.option('slips', lambda: DepositSlip.objects.all(), lambda x: x.number, ('number',), DepositSlip)
app.option('purchase-orders', lambda: PurchaseOrder.objects.all(), lambda x: f'{x.number} {x.description}', ('number',), PurchaseOrder)
app.option('all-parties', lambda: Party.objects.all(), lambda x: f'{x.name} ({x.number})', ('name', 'number'))

app.settings_fields = app.settings_fields + (
    'country', 'reservation_approval', 'reservation_fee_refundable', 'lease_approval_discount', 'sale_approval_discount', 'deduction_approval_above',
    'refund_approval_above', 'statement_approval', 'reminder_days_before', 'reminder_days_after', 'escalate_after_days', 'late_fee_percent', 'late_fee_grace_days',
    'bounce_fee', 'document_alert_days', 'service_charge_instalments', 'payment_link', 'messaging_url', 'listing_site', 'listing_agency', 'listing_feed_key')
app.pack_models.update({'doc_template': (DocTemplate, 'code'), 'message_template': (MessageTemplate, 'code'), 'inspection_template': (InspectionTemplate, 'code')})

# ------------------------------------------------------------------ 1–3  hierarchy, unit generator, property master
MONEY_T = 'number'
regs['building'].form = ('number', 'name', 'project', 'block', 'kind', 'subtype', 'address', 'city', 'postcode', 'plot_no', 'gps', 'owner', 'floors', 'year_built', 'built_up_area',
                         'manager', 'service_charge_rate', 'valuation', 'valuation_date', 'insurance_expiry', 'permit_expiry', 'external_ref', 'amenities', 'notes', 'is_active')
regs['building'].cols = ('number', 'name', 'project', 'kind', 'subtype', 'city', 'owner', 'manager', 'is_active')
regs['building'].filters = ('project', 'kind', 'subtype', 'is_active')
regs['building'].children = (('Floors', 'floor', 'building'), ('Units', 'unit', 'building'), ('Meters', 'meter', 'building'), ('Service charge budgets', 'budget', 'building'),
                             ('Documents', 'document', 'building'))
regs['unit'].form = ('building', 'unit_no', 'floor', 'level', 'parent', 'unit_type', 'bedrooms', 'bathrooms', 'area', 'built_up_area', 'rentable_area', 'carpet_area', 'view',
                     'owner', 'co_owners', 'rent', 'price', 'furnishing', 'parking', 'status', 'next_available', 'plot_no', 'gps', 'valuation', 'valuation_date',
                     'service_charge_rate', 'meters', 'amenities', 'external_ref', 'notes')
regs['unit'].tables = {'co_owners': [('owner', 'Co-owner', 'opt', 'owners'), ('percent', 'Share %', 'number')],
                       'meters': [('kind', 'Meter', 'choice', 'Electricity|Water|Gas|Chiller|Other'), ('number', 'Meter / account no.', 'text')]}
regs['unit'].filters = ('building', 'status', 'unit_type', 'owner', 'furnishing')
regs['unit'].children = (('Leases', 'lease', 'unit'), ('Reservations', 'reservation', 'unit'), ('Sub-units', 'unit', 'parent'), ('Inspections', 'inspection', 'unit'),
                         ('Keys and cards', 'key', 'unit'), ('Meters', 'meter', 'unit'), ('Listings', 'listing', 'unit'), ('Documents', 'document', 'unit'))
regs['owner'].form = ('kind', 'name', 'email', 'phone', 'phones', 'address', 'id_type', 'id_number', 'id_expiry', 'nationality', 'tax_id', 'bank_details', 'is_active')
regs['owner'].restricted = ('bank_details', 'id_number')
regs['owner'].tables = {'phones': [('label', 'Label', 'text'), ('number', 'Number', 'text')]}
regs['owner'].children = regs['owner'].children + (('Documents', 'document', 'owner'), ('Requests', 'portal_request', 'owner'))
regs['party'].form = ('kind', 'party_type', 'name', 'company_name', 'email', 'phone', 'phones', 'address', 'addresses', 'nationality', 'id_type', 'id_document', 'id_expiry',
                      'emergency_contact', 'broker', 'consent', 'consent_on', 'retain_until', 'crm_contact_id', 'is_active')
regs['party'].tables = {'phones': [('label', 'Label', 'text'), ('number', 'Number', 'text')],
                        'addresses': [('label', 'Label', 'text'), ('address', 'Address', 'text'), ('country', 'Country', 'text')]}
regs['party'].children = regs['party'].children + (('Documents', 'document', 'party'), ('Refunds', 'refund', 'party'), ('Requests', 'portal_request', 'party'))

R(Reg('project', Project, 'Project / portfolio', 'Projects and portfolios', 'Portfolio', cols=('number', 'name', 'kind', 'country', 'city', 'developer', 'manager', 'is_active'),
      form=('number', 'name', 'kind', 'country', 'city', 'developer', 'manager', 'notes', 'is_active'), number=('project', 'PRJ'), manage=MGR, view=ALL_STAFF,
      search=('name', 'number', 'city'), filters=('kind', 'country', 'is_active'), title='name', subtitle='city', children=(('Buildings', 'building', 'project'),),
      help='The level above buildings: a community, a tower cluster, a portfolio of an owner or a land bank.'))
R(Reg('floor', Floor, 'Floor', 'Floors', 'Portfolio', cols=('building', 'no', 'name', 'use', 'rentable_area'), form=('building', 'no', 'name', 'use', 'rentable_area'),
      manage=MGR + ('prop_leasing',), view=ALL_STAFF, search=('name', 'building__name'), filters=('building', 'use'), title='name', subtitle='building', child_of='building',
      children=(('Units', 'unit', 'level'),), deletable=True, files=False, help='Floors of a building; the unit generator makes them.'))


def a_generate_units(o, request, f):
    """Building → Generate units: floors × units per floor with a numbering pattern."""
    f1, f2, per = f.i('from_floor', True), f.i('to_floor', True), f.i('per_floor', True)
    if f2 < f1 or per < 1 or per > 60 or (f2 - f1) > 150:
        raise AppError('Check the floors and the units per floor.', 'per_floor')
    pattern = f.s('pattern') or '{floor}{nn}'
    ut = f.i('unit_type')
    made, skipped = 0, 0
    for fl in range(f1, f2 + 1):
        floor, _ = Floor.objects.get_or_create(building=o, no=fl, defaults={'name': f.s('floor_name').replace('{floor}', str(fl)) if f.s('floor_name') else ''})
        for i in range(1, per + 1):
            no = pattern.replace('{floor}', str(fl)).replace('{nn}', f'{i:02d}').replace('{n}', str(i)).replace('{floor2}', f'{fl:02d}')
            if Unit.objects.filter(building=o, unit_no=no).exists():
                skipped += 1
                continue
            Unit.objects.create(building=o, unit_no=no, floor=str(fl), level=floor, unit_type_id=ut, bedrooms=f.i('bedrooms'), area=f.n('area'), rentable_area=f.n('area'),
                                rent=f.n('rent'), price=f.n('price'), owner=o.owner, number=f'{o.number}-{no}'[:30], status='available')
            made += 1
    if o.floors is None or o.floors < f2:
        o.floors = f2
        o.save(update_fields=['floors'])
    app.event('building', o.pk, f'{made} unit(s) generated (floors {f1}–{f2}, {per} per floor, pattern {pattern})' + (f', {skipped} existed' if skipped else ''), request.user)
    return {'message': f'{made} unit(s) made' + (f'; {skipped} already existed.' if skipped else '.')}


regs['building'].actions.append(Act('generate', 'Generate units', a_generate_units, lambda o, r: app.any(r, *MGR + ('prop_leasing',)), style='primary',
                                    fields=(('from_floor', 'From floor', 'int', True), ('to_floor', 'To floor', 'int', True), ('per_floor', 'Units per floor', 'int', True),
                                            ('pattern', 'Numbering pattern ({floor}{nn} → 101, 102…)', 'text', False), ('unit_type', 'Unit type', 'opt:unit-types', False),
                                            ('bedrooms', 'Bedrooms', 'int', False), ('area', 'Area (sq ft)', 'decimal', False), ('rent', 'Asking rent / year', 'decimal', False),
                                            ('price', 'Asking sale price', 'decimal', False), ('floor_name', 'Floor name ({floor})', 'text', False))))


def a_subdivide(o, request, f):
    n = f.i('count', True)
    if n < 2 or n > 50:
        raise AppError('Make 2 to 50 sub-units.', 'count')
    if active_lease(o):
        raise AppError('The unit is let – end the lease first.')
    area = (o.rentable_area or o.area or ZERO) / n
    made = []
    for i in range(1, n + 1):
        no = f'{o.unit_no}-{i}'
        if Unit.objects.filter(building=o.building, unit_no=no).exists():
            continue
        made.append(Unit.objects.create(building=o.building, unit_no=no, floor=o.floor, level=o.level, unit_type=o.unit_type, parent=o, owner=o.owner, area=money(area) if area else None,
                                        rentable_area=money(area) if area else None, rent=money((o.rent or ZERO) / n) if o.rent else None, number=f'{o.number}-{i}'[:30]))
    set_unit(o, 'unavailable', f'subdivided into {len(made)} sub-units', request.user)
    app.event('unit', o.pk, f'Subdivided into {", ".join(u.unit_no for u in made)}', request.user)
    return {'message': f'{len(made)} sub-unit(s) made; this unit is now "Not available" (the sub-units are let instead).'}


def a_merge(o, request, f):
    ids = f.ids('units')
    others = list(Unit.objects.filter(pk__in=ids, building=o.building).exclude(pk=o.pk))
    if not others:
        raise AppError('Choose the units of the same building to merge into this one.', 'units')
    for u in others:
        if active_lease(u) or u.status in ('reserved', 'sold', 'sold_pending'):
            raise AppError(f'{u.label} is {u.get_status_display().lower()} – it cannot be merged.')
    for u in others:
        u.parent = o
        u.save(update_fields=['parent'])
        set_unit(u, 'unavailable', f'merged into {o.unit_no}', request.user)
        for fld in ('area', 'rentable_area', 'built_up_area', 'carpet_area'):
            if getattr(u, fld):
                setattr(o, fld, (getattr(o, fld) or ZERO) + getattr(u, fld))
    o.save()
    app.event('unit', o.pk, f'Merged with {", ".join(u.unit_no for u in others)} – areas added', request.user)
    return {'message': f'{len(others)} unit(s) merged into {o.unit_no}.'}


def a_transfer_owner(o, request, f):
    new = Owner.objects.filter(pk=f.i('owner', True)).first()
    if new is None:
        raise AppError('Choose the new owner.', 'owner')
    old = o.owner.name if o.owner_id else '–'
    o.owner, o.co_owners = new, []
    o.save(update_fields=['owner', 'co_owners'])
    app.event('unit', o.pk, f'Ownership transferred {old} → {new.name} on {d(f.dt("on") or today())}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


regs['unit'].actions += [
    Act('subdivide', 'Subdivide', a_subdivide, lambda o, r: o.status in ('available', 'unavailable') and app.any(r, *MGR), fields=(('count', 'Number of sub-units', 'int', True),)),
    Act('merge', 'Merge units into this one', a_merge, lambda o, r: o.status in ('available', 'unavailable') and app.any(r, *MGR), fields=(('units', 'Units to merge', 'opt-multi:units', True),)),
    Act('transfer', 'Transfer ownership', a_transfer_owner, lambda o, r: app.any(r, *MGR),
        fields=(('owner', 'New owner', 'opt:owners', True), ('on', 'Transfer date', 'date', False), ('note', 'Note (title deed, price…)', 'text', False))),
]

# ------------------------------------------------------------------ 6–7  reservations: approval, refundable fee, refund, waiting list
_res_saver = regs['reservation'].saver


def res_saver2(o, request, creating, old):
    s = app.settings()
    if creating:
        o.fee_refundable = s.reservation_fee_refundable if not request or not request.data.get('fee_refundable') else o.fee_refundable
        if s.reservation_approval and request and not app.any(request, *MGR):
            o.status = 'pending'
    o = _res_saver(o, request, creating, old)
    if creating and o.status == 'pending':
        approval_todo(f'Reservation {o.number} to approve', f'{o.unit.label} for {o.party.name} – {cur()} {o.amount}', 'reservation', o.pk, o.number)
    WaitingList.objects.filter(party=o.party, status__in=('waiting', 'offered')).update(status='reserved')
    return o


regs['reservation'].saver = res_saver2
regs['reservation'].form = regs['reservation'].form + ('fee_refundable',)
regs['reservation'].info = ('refunded',)
regs['reservation'].edit_when = lambda o: o.status in ('active', 'pending')


def make_refund(party, kind, amount, reason, user, **links):
    if not amount or amount <= 0:
        return None
    s = app.settings()
    r = Refund.objects.create(number=app.next_number('refund', 'RFD'), kind=kind, party=party, amount=money(amount), reason=reason[:200], **links)
    if s.refund_approval_above and r.amount <= s.refund_approval_above:
        r.status, r.approved_by = 'approved', user if (user and getattr(user, 'is_authenticated', False)) else None
        r.save(update_fields=['status', 'approved_by'])
    app.event('refund', r.pk, f'{r.get_kind_display()} refund requested – {reason}', user)
    if r.status == 'requested':
        approval_todo(f'Refund {r.number} to approve', f'{party.name}: {cur()} {r.amount} – {reason}', 'refund', r.pk, r.number)
    else:
        app.tell(app.users_with_role('prop_finance'), f'Refund {r.number} to pay', f'{party.name}: {cur()} {r.amount}', 'refund', r.pk, r.number, PAGE + f'r/refund/{r.pk}',
                 summary=f'Pay refund {r.number}')
    return r


def a_res_approve(o, request, f):
    o.status = 'active'
    o.save(update_fields=['status'])
    app.close_todos('reservation', o.pk)
    app.event('reservation', o.pk, 'Approved', request.user)
    tell_party(o.party, 'reservation', f'Reservation {o.number} confirmed', f'Dear {o.party.name},\n\n{o.unit.label} is reserved for you until {d(o.expires_on)}.\n',
               {'unit': o.unit.label, 'number': o.number, 'due': d(o.expires_on)}, 'reservation', o.pk, o.number, PAGE + f'r/reservation/{o.pk}')
    return o


def a_res_reject(o, request, f):
    A.end_reservation(o, 'cancelled', f'Rejected – {f.s("reason")}', request.user)
    app.close_todos('reservation', o.pk)
    return o


def a_res_refund(o, request, f):
    amt = f.n('amount') or (o.amount - o.refunded)
    if amt <= 0 or amt > o.amount - o.refunded:
        raise AppError(f'Up to {cur()} {o.amount - o.refunded} can be refunded.', 'amount')
    r = make_refund(o.party, 'reservation', amt, f.s('reason') or f'Reservation {o.number} not converted', request.user, reservation=o)
    o.refunded += money(amt)
    o.save(update_fields=['refunded'])
    return {'message': f'Refund {r.number} requested.'}


regs['reservation'].actions += [
    Act('approve', 'Approve', a_res_approve, when(('pending',), *MGR), style='primary'),
    Act('reject', 'Reject', a_res_reject, when(('pending',), *MGR), style='danger', fields=(('reason', 'Reason', 'text', True),)),
    Act('refund', 'Refund the fee', a_res_refund, lambda o, r: o.status in ('expired', 'cancelled') and o.amount > o.refunded and app.any(r, *FIN + MGR),
        fields=(('amount', 'Amount (empty = all)', 'decimal', False), ('reason', 'Reason', 'text', False))),
]


def a_wait_offer(o, request, f):
    u = Unit.objects.filter(pk=f.i('unit', True)).first()
    if u is None:
        raise AppError('Choose the unit to offer.', 'unit')
    o.status, o.unit = 'offered', u
    o.save(update_fields=['status', 'unit'])
    app.event('waiting', o.pk, f'{u.label} offered', request.user)
    tell_party(o.party, 'reservation', f'A unit is available: {u.label}', f'Dear {o.party.name},\n\n{u.label} is now available' + (f' at {cur()} {u.rent} per year' if u.rent else '') +
               f'. Please tell us within {app.settings().reservation_days} days if you would like to take it.\n' + (f'\n{f.s("note")}\n' if f.s('note') else ''),
               {'unit': u.label, 'number': o.number}, 'waiting', o.pk, o.number, PAGE + f'r/waiting/{o.pk}')
    return o


def a_wait_reserve(o, request, f):
    u = o.unit or Unit.objects.filter(pk=f.i('unit')).first()
    if u is None:
        raise AppError('Choose the unit.', 'unit')
    r = Reservation(number=app.next_number('reservation', 'RSV'), unit=u, party=o.party, kind=o.kind, amount=f.n('amount') or ZERO, reserved_on=today(), agent=o.agent or app.my_emp(request))
    r.expires_on = today() + timedelta(days=app.settings().reservation_days or 7)
    A.res_check(r, request, None)
    res_saver2(r, request, True, None)
    o.status = 'reserved'
    o.save(update_fields=['status'])
    app.event('waiting', o.pk, f'Reserved: {r.number}', request.user)
    return r


R(Reg('waiting', WaitingList, 'Waiting list entry', 'Waiting list', 'Deals', cols=('number', 'party', 'kind', 'unit', 'building', 'unit_type', 'budget', 'needed_from', 'status'),
      form=('party', 'kind', 'unit', 'building', 'unit_type', 'budget', 'needed_from', 'agent', 'notes'), number=('waiting', 'WL'), manage=DEALS, view=ALL_STAFF,
      search=('number', 'party__name'), filters=('status', 'kind', 'building', 'unit_type'), subtitle='party', edit_when=lambda o: o.status in ('waiting', 'offered'),
      actions=[Act('offer', 'Offer a unit', a_wait_offer, when(('waiting', 'offered'), *DEALS), style='primary', fields=(('unit', 'Unit', 'opt:available-units', True), ('note', 'Message', 'textarea', False))),
               Act('reserve', 'Reserve', a_wait_reserve, when(('waiting', 'offered'), *DEALS), fields=(('unit', 'Unit (empty = the offered one)', 'opt:available-units', False), ('amount', 'Reservation fee received', 'decimal', False))),
               Act('close', 'Close', lambda o, r, f: (setattr(o, 'status', 'closed'), o.save(update_fields=['status']), app.event('waiting', o.pk, f'Closed – {f.s("reason")}', r.user), o)[-1],
                   when(('waiting', 'offered'), *DEALS), style='danger', fields=(('reason', 'Reason', 'text', False),))],
      help='Prospects waiting for a unit; when a unit becomes available the daily job tells the leasing team who is waiting for it.'))


# ------------------------------------------------------------------ 4–5, 16  leases: rent rules, parties, commercial terms, approval, amendments
regs['lease'].form = ('unit', 'units', 'party', 'parties', 'occupants', 'room', 'start', 'end', 'annual_rent', 'rent_free_days', 'deposit', 'payments', 'schedule', 'method',
                      'charges', 'service_charge', 'rent_steps', 'escalation_percent', 'review_date', 'review_kind', 'review_percent', 'break_date', 'break_notice_days',
                      'turnover_percent', 'fitout_days', 'allocations', 'late_fee', 'registration_ref', 'agent', 'commission_percent', 'crm_opportunity_id', 'notes')
regs['lease'].cols = ('number', 'unit', 'party', 'change', 'start', 'end', 'annual_rent', 'payments', 'status', 'approval', 'renewal_status')
regs['lease'].filters = ('status', 'change', 'approval', 'renewal_status', 'unit', 'party', 'service_charge')
regs['lease'].info = regs['lease'].info + ('change', 'approval', 'approval_note', 'accepted_on')
regs['lease'].tables = {
    'parties': [('party', 'Person / company', 'opt', 'all-parties'), ('role', 'Role', 'choice', 'joint=Joint tenant|guarantor=Guarantor|occupant=Occupant|contact=Company contact'), ('share', 'Rent share %', 'number')],
    'rent_steps': [('from', 'From', 'date'), ('annual_rent', 'Annual rent', 'number')],
    'schedule': [('due', 'Due', 'date'), ('amount', 'Amount', 'number'), ('label', 'Label', 'text')],
    'charges': [('charge', 'Charge', 'opt', 'charges'), ('amount', 'Amount', 'number'), ('per', 'Per', 'choice', 'month=Month|quarter=Quarter|year=Year|once=Once')],
    'allocations': [('kind', 'Kind', 'choice', 'parking=Parking|storage=Storage|signage=Signage|other=Other'), ('detail', 'Detail', 'text'), ('amount', 'Amount per year', 'number')],
}
regs['lease'].pdf = 'Lease'
regs['lease'].children = regs['lease'].children + (('Inspections', 'inspection', 'lease'), ('Documents', 'document', 'lease'), ('Refunds', 'refund', 'lease'),
                                                   ('Recurring charges', 'recurring', 'lease'))
_lease_check = regs['lease'].check


def lease_check2(o, request, old):
    _lease_check(o, request, old)
    s = app.settings()
    below = pct_below(o.unit.rent, o.annual_rent)
    if o.status == 'draft' and o.approval != 'approved':
        o.approval = 'needed' if below > (s.lease_approval_discount or 0) else ''
        if o.approval == 'needed':
            o.approval_note = f'Rent is {below}% below the asking rent of {cur()} {o.unit.rent}'
    for p in (o.parties or []):
        if not p.get('party'):
            raise AppError('Choose the person for every party line.', 'parties')
    tot = sum(Decimal(str(x.get('amount') or 0)) for x in (o.schedule or []))
    if o.schedule and o.annual_rent and abs(tot - rent_for_period(o)) > 1:
        raise AppError(f'The custom schedule adds up to {cur()} {tot}; the rent for the period is {rent_for_period(o)}.', 'schedule')


regs['lease'].check = lease_check2


def rent_for_period(o):
    """Rent for the whole lease: months × annual rent / 12 (days when shorter than a month), minus rent-free days, with stepped rent when set."""
    days = (o.end - o.start).days + 1
    months = round(days / 30.4)
    if months < 1:
        return money(o.annual_rent * Decimal(days) / 365)
    if o.rent_steps:
        total = ZERO
        steps = sorted([x for x in o.rent_steps if x.get('from') and x.get('annual_rent')], key=lambda x: x['from'])
        cursor = o.start
        for i in range(months):
            frm = add_months(o.start, i)
            rate = o.annual_rent
            for st in steps:
                if date.fromisoformat(str(st['from'])[:10]) <= frm:
                    rate = Decimal(str(st['annual_rent']))
            total += Decimal(rate) / 12
            cursor = frm
        total = money(total)
    else:
        total = money(o.annual_rent * Decimal(months) / 12)
    if o.rent_free_days:
        total -= money(o.annual_rent * Decimal(o.rent_free_days) / 365)
    return max(ZERO, money(total))


def lease_schedule2(o):
    """Billing records of a lease: custom schedule or equal payments (stepped rent and rent-free days considered), extra charges, deposit."""
    s = app.settings()
    rent = charge('rent')
    vat = s.commercial_vat_percent if A.is_commercial(o.unit) else ZERO
    bills = []
    total = rent_for_period(o)
    if o.schedule:
        for i, x in enumerate(o.schedule):
            due = date.fromisoformat(str(x['due'])[:10])
            bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=rent, amount=Decimal(str(x['amount'])), due=due, desc=f'{x.get("label") or "Rent " + str(i + 1)} {o.unit.label}',
                                   pfrom=due, pto=None, source='lease', vat_percent=vat))
    else:
        n = o.payments
        months = max(1, round(((o.end - o.start).days + 1) / 30.4))
        part = money(total / n)
        for i in range(n):
            frm = add_months(o.start, i * months // n)
            to = (add_months(o.start, (i + 1) * months // n) - timedelta(days=1)) if i < n - 1 else o.end
            amt = part if i < n - 1 else total - part * (n - 1)
            due = frm + timedelta(days=o.rent_free_days) if i == 0 and o.rent_free_days else frm
            bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=rent, amount=amt, due=due, desc=f'Rent {i + 1}/{n} {o.unit.label}', pfrom=frm, pto=to, source='lease', vat_percent=vat))
    for x in (o.charges or []):
        ch = ChargeType.objects.filter(pk=x.get('charge')).first()
        amt = Decimal(str(x.get('amount') or 0))
        if ch is None or not amt:
            continue
        per = x.get('per') or 'month'
        if per == 'once':
            bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=ch, amount=amt, due=o.start, desc=f'{ch.name} {o.unit.label}', source='lease'))
        else:
            step = {'month': 1, 'quarter': 3, 'year': 12}[per]
            i = 0
            while add_months(o.start, i * step) <= o.end:
                frm = add_months(o.start, i * step)
                to = min(o.end, add_months(o.start, (i + 1) * step) - timedelta(days=1))
                bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=ch, amount=amt, due=frm, desc=f'{ch.name} {o.unit.label}', pfrom=frm, pto=to, source='lease'))
                i += 1
    for x in (o.allocations or []):
        amt = Decimal(str(x.get('amount') or 0))
        if amt:
            bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=charge('fee'), amount=amt, due=o.start, desc=f'{x.get("kind", "").capitalize()} {x.get("detail", "")} {o.unit.label}'.strip(), source='lease'))
    if o.deposit and not o.previous_id:   # v1.62.0 a renewal / amendment keeps the deposit already held – it was billed a second time
        bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=charge('deposit'), amount=o.deposit, due=o.start, desc=f'Security deposit {o.unit.label}', source='lease', vat_percent=ZERO))
    return bills


A.lease_schedule = lease_schedule2
_lease_activate = A.a_lease_activate


def a_lease_activate2(o, request, f):
    if o.approval in ('needed', 'requested', 'rejected'):
        raise AppError('This lease needs the manager\'s approval before it is activated (rent below the asking rent).')
    missing = []
    if not Document.objects.filter(party=o.party, category__in=('id', 'licence'), status__in=('valid', 'expiring')).exists():
        missing.append('ID / trade licence of the tenant')
    r = _lease_activate(o, request, f)
    o.refresh_from_db()
    # amendment: the previous version is closed from the amendment start
    if o.previous_id and o.change == 'amendment':
        prev = o.previous
        prev.status = 'amended'
        prev.save(update_fields=['status'])
        for b in prev.bills.filter(status__in=('issued', 'draft'), period_from__gte=o.start):
            b.status = 'void'
            b.save(update_fields=['status'])
        app.event('lease', prev.pk, f'Amended by {o.number} from {d(o.start)}', request.user)
    # leasing fee to the owner (management agreement) → owner statement
    ag = o.unit.owner.agreements.filter(status='active').order_by('-start').first() if o.unit.owner_id else None
    if ag and ag.leasing_fee and not o.previous_id:
        make_bill(owner=o.unit.owner, unit=o.unit, lease=o, ch=charge('fee', 'LEASING_FEE'), amount=ag.leasing_fee, due=o.start, desc=f'Leasing fee – lease {o.number}', source='lease', vat_percent=ZERO)
    # Property inspection record (standalone move-in inspection with the room-wise template)
    if not o.previous_id and not o.inspections.filter(kind='move_in').exists():
        make_inspection('move_in', o.unit, lease=o, on=o.start, user=request.user)
    # leases of several units: the other units follow
    for u in o.units.all():
        set_unit(u, 'let', f'lease {o.number} (several units)', request.user)
    # other units' keys and the tenant: messages
    tell_party(o.party, 'lease_active', f'Your lease {o.number} is active', f'Dear {o.party.name},\n\nYour lease of {o.unit.label} from {d(o.start)} to {d(o.end)} is active. '
               f'Rent {cur()} {o.annual_rent} per year in {o.payments} payment(s). You can see the bills and pay in the tenant portal.\n',
               {'unit': o.unit.label, 'number': o.number, 'amount': str(o.annual_rent)}, 'lease', o.pk, o.number, PAGE + f'r/lease/{o.pk}')
    if isinstance(r, dict) and missing:
        r['message'] += ' Missing documents: ' + ', '.join(missing) + '.'
    return r


for a in regs['lease'].actions:
    if a.name == 'activate':
        a.run = a_lease_activate2
    if a.name == 'renew':
        _renew = a.run

        def a_lease_renew2(o, request, f, _r=_renew):
            if not f.n('annual_rent') and o.escalation_percent:
                f.d['annual_rent'] = str(money(o.annual_rent * (1 + o.escalation_percent / 100)))
            new = _r(o, request, f)
            if isinstance(new, Lease):
                new.change, new.escalation_percent, new.charges, new.service_charge, new.late_fee, new.parties = 'renewal', o.escalation_percent, o.charges, o.service_charge, o.late_fee, o.parties
                new.save()
                call(app, 'ZeoCRM', 'renewal_closed', o.pk, True, new.number, request.user)
            return new
        a.run = a_lease_renew2
    if a.name == 'notice':
        _notice = a.run

        def a_notice2(o, request, f, _r=_notice):
            r = _r(o, request, f)
            Unit.objects.filter(pk=o.unit_id).update(next_available=o.move_out_on)
            wl = WaitingList.objects.filter(status='waiting').filter(Q(unit=o.unit) | Q(building=o.unit.building, unit_type=o.unit.unit_type) | Q(building=o.unit.building, unit_type__isnull=True))
            for w in wl[:10]:
                app.tell(app.users_with_role('prop_leasing'), f'{o.unit.label} becomes available {d(o.move_out_on)}', f'{w.party.name} is on the waiting list ({w.number})',
                         'waiting', w.pk, w.number, PAGE + f'r/waiting/{w.pk}', summary=f'Offer {o.unit.label} to {w.party.name}')
            return r
        a.run = a_notice2


def a_lease_request_approval(o, request, f):
    o.approval = 'requested'
    o.save(update_fields=['approval'])
    approval_todo(f'Lease {o.number} to approve', f'{o.unit.label} – {o.party.name}: {o.approval_note}', 'lease', o.pk, o.number)
    app.event('lease', o.pk, f'Approval requested – {o.approval_note}', request.user)
    return o


def a_lease_approve(ok):
    def run(o, request, f):
        o.approval = 'approved' if ok else 'rejected'
        o.approval_note = (o.approval_note + ' · ' if o.approval_note else '') + (f.s('note') or ('Approved' if ok else 'Rejected'))
        o.save(update_fields=['approval', 'approval_note'])
        app.close_todos('lease', o.pk)
        app.event('lease', o.pk, ('Approved' if ok else 'Rejected') + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o
    return run


def a_lease_amend(o, request, f):
    start = f.dt('start', True, 'Amendment from')
    if not (o.start < start <= o.end):
        raise AppError('The amendment must start inside the lease period.', 'start')
    if o.next_versions.exclude(status='cancelled').exists():
        raise AppError('A renewal or amendment is already drafted for this lease.')
    new = Lease.objects.create(number=app.next_number('lease', 'LSE'), unit=o.unit, party=o.party, start=start, end=f.dt('end') or o.end, annual_rent=f.n('annual_rent') or o.annual_rent,
                               deposit=o.deposit, payments=f.i('payments') or max(1, round(o.payments * ((o.end - start).days + 1) / max(1, (o.end - o.start).days + 1))),
                               method=o.method, version=o.version + 1, previous=o, agent=o.agent, commission_percent=ZERO, change='amendment', parties=o.parties, occupants=o.occupants,
                               charges=o.charges, service_charge=o.service_charge, late_fee=o.late_fee, registration_ref=o.registration_ref, allocations=o.allocations,
                               notes=f'Amendment of {o.number}: {f.s("reason")}')
    new.units.set(o.units.all())
    app.event('lease', o.pk, f'Amendment drafted: {new.number} from {d(start)} – {f.s("reason")}', request.user)
    app.event('lease', new.pk, f'Drafted as amendment of {o.number} (version {new.version})', request.user)
    return new


def a_lease_break(o, request, f):
    on = f.dt('on') or o.break_date or today()
    f.d['move_out_on'] = on.isoformat()
    f.d['notice_given_on'] = today().isoformat()
    for a in regs['lease'].actions:
        if a.name == 'notice':
            r = a.run(o, request, f)
    app.event('lease', o.pk, f'Break option exercised – lease ends {d(on)}', request.user)
    return r


def a_lease_review(o, request, f):
    pct = f.n('percent') if f.n('percent') is not None else (o.review_percent or ZERO)
    new_rent = f.n('annual_rent') or money(o.annual_rent * (1 + pct / 100))
    o.notes = (o.notes + '\n' if o.notes else '') + f'Rent review {d(today())}: {o.annual_rent} → {new_rent} ({o.get_review_kind_display()})'
    o.review_date = add_months(o.review_date or today(), 12)
    o.save(update_fields=['notes', 'review_date'])
    app.event('lease', o.pk, f'Rent review: new rent {cur()} {new_rent} from the next schedule / amendment; next review {d(o.review_date)}', request.user)
    return {'message': f'Review recorded – make an amendment or a renewal at {cur()} {new_rent}.'}


regs['lease'].actions += [
    Act('request_approval', 'Request approval', a_lease_request_approval, lambda o, r: o.status == 'draft' and o.approval in ('needed', 'rejected') and app.any(r, *LEASING), style='primary'),
    Act('approve', 'Approve', a_lease_approve(True), lambda o, r: o.status == 'draft' and o.approval in ('needed', 'requested') and app.any(r, *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
    Act('reject', 'Reject', a_lease_approve(False), lambda o, r: o.status == 'draft' and o.approval in ('needed', 'requested') and app.any(r, *MGR), style='danger', fields=(('note', 'Reason', 'text', True),)),
    Act('amend', 'Amend (new version)', a_lease_amend, when(('active',), *LEASING),
        fields=(('start', 'Amendment from', 'date', True), ('annual_rent', 'New annual rent', 'decimal', False), ('end', 'New end date', 'date', False), ('payments', 'Payments left', 'int', False), ('reason', 'What changes', 'text', True))),
    Act('break', 'Exercise break option', a_lease_break, lambda o, r: o.status == 'active' and bool(o.break_date) and app.any(r, *LEASING), fields=(('on', 'Lease ends on', 'date', False),)),
    Act('review', 'Rent review', a_lease_review, lambda o, r: o.status == 'active' and bool(o.review_kind) and app.any(r, *LEASING),
        fields=(('percent', 'Increase %', 'decimal', False), ('annual_rent', 'or new annual rent', 'decimal', False))),
]


# ------------------------------------------------------------------ 7  sales: offers, discount approval, pending completion, co-buyers, cancellation refund
regs['sale'].form = ('unit', 'party', 'co_buyers', 'price', 'offers', 'plan', 'handover_due', 'agent', 'commission_percent', 'crm_opportunity_id', 'amendments', 'notes')
regs['sale'].cols = ('number', 'unit', 'party', 'price', 'discount_percent', 'signed_on', 'completion_on', 'handover_due', 'status')
regs['sale'].info = regs['sale'].info + ('discount_percent', 'approval_note', 'completion_on', 'cancellation_fee')
regs['sale'].tables = {'co_buyers': [('party', 'Co-buyer', 'opt', 'all-parties'), ('share', 'Share %', 'number')],
                       'offers': [('date', 'Date', 'date'), ('price', 'Offered price', 'number'), ('note', 'Note', 'text')],
                       'amendments': [('date', 'Date', 'date'), ('text', 'Amendment', 'text')]}
regs['sale'].pdf = 'Sale agreement'
regs['sale'].edit_when = lambda o: o.status in ('draft', 'approval')
regs['sale'].children = regs['sale'].children + (('Inspections', 'inspection', 'sale'), ('Documents', 'document', 'sale'), ('Refunds', 'refund', 'sale'))


def sale_check(o, request, old):
    o.discount_percent = pct_below(o.unit.price, o.price)
    s = app.settings()
    if o.status in ('draft', 'approval'):
        if o.discount_percent > (s.sale_approval_discount or 0):
            if o.status == 'draft' and not (old and old.status == 'approval'):
                o.status = 'approval'
                o.approval_note = f'Price is {o.discount_percent}% below the asking price of {cur()} {o.unit.price}'
        else:
            o.status, o.approval_note = 'draft', ''


regs['sale'].check = sale_check
_sale_saver = regs['sale'].saver


def sale_saver(o, request, creating, old):
    o.save()
    if o.status == 'approval' and (creating or (old and old.status != 'approval')):
        approval_todo(f'Sale {o.number} to approve', f'{o.unit.label} – {o.party.name}: {o.approval_note}', 'sale', o.pk, o.number)
    return o


regs['sale'].saver = sale_saver
_sale_sign = A.a_sale_sign


def a_sale_sign2(o, request, f):
    r = _sale_sign(o, request, f)
    o.refresh_from_db()
    set_unit(o.unit, 'sold_pending', f'sale contract {o.number} signed – pending completion', request.user)
    return r


def a_sale_complete(o, request, f):
    o.completion_on = f.dt('completion_on') or today()
    o.status = 'completed'
    o.save(update_fields=['completion_on', 'status'])
    set_unit(o.unit, 'sold', f'sale {o.number} completed (title transferred)', request.user)
    # the buyer becomes the owner of the unit
    if f.b('transfer_owner'):
        ow = Owner.objects.filter(email__iexact=o.party.email).first() if o.party.email else None
        if ow is None:
            ow = Owner.objects.create(number=app.next_number('owner', 'OWN'), name=o.party.name, email=o.party.email, phone=o.party.phone, kind='company' if o.party.party_type == 'company' else 'person')
        o.unit.owner, o.unit.co_owners = ow, []
        o.unit.save(update_fields=['owner', 'co_owners'])
        app.event('unit', o.unit_id, f'Owner is now the buyer {ow.name} (sale {o.number})', request.user)
    app.event('sale', o.pk, f'Completed on {d(o.completion_on)}', request.user)
    return o


_sale_cancel = A.a_sale_cancel


def a_sale_cancel2(o, request, f):
    paid = sum((b.paid for b in o.bills.exclude(status='void')), ZERO)
    fee = money(f.n('fee') or ZERO)
    r = _sale_cancel(o, request, f)
    o.cancellation_fee = fee
    o.save(update_fields=['cancellation_fee'])
    if fee:
        b = make_bill(party=o.party, unit=o.unit, sale=o, ch=charge('fee'), amount=fee, due=today(), desc=f'Cancellation fee – sale {o.number}', source='cancellation', vat_percent=ZERO,
                      status_='paid' if paid >= fee else 'issued')
        if paid >= fee:
            b.paid = b.total
            b.save(update_fields=['paid'])
    back = money(paid - fee)
    if back > 0:
        make_refund(o.party, 'sale', back, f'Sale {o.number} cancelled – {f.s("reason")}', request.user, sale=o)
    for c in Commission.objects.filter(sale=o).exclude(status__in=('rejected', 'reversed')):
        c.status, c.note = 'reversed', (c.note + ' · ' if c.note else '') + f'Sale {o.number} cancelled' + (' – was paid: recover from the agent' if c.status == 'paid' else '')
        c.save(update_fields=['status', 'note'])
        app.event('commission', c.pk, 'Reversed – sale cancelled', request.user)
    app.event('sale', o.pk, f'Cancelled: paid {cur()} {paid}, fee kept {fee}, refund {back}', request.user)
    return r


for a in regs['sale'].actions:
    if a.name == 'sign':
        a.run, a.allowed = a_sale_sign2, when(('draft',), *SALES)
    if a.name == 'cancel':
        a.run = a_sale_cancel2
        a.fields = (('reason', 'Reason', 'text', True), ('fee', 'Cancellation fee kept', 'decimal', False))
        a.allowed = when(('draft', 'signed', 'completed'), *MGR)
    if a.name == 'handed_over':
        a.allowed = when(('signed', 'completed'), *SALES)
    if a.name == 'handover':
        a.allowed = lambda o, r: o.status in ('signed', 'completed') and bool(o.handover) and app.any(r, *SALES)


def a_sale_approve(ok):
    def run(o, request, f):
        if ok:
            o.status, o.approval_note = 'draft', (o.approval_note + ' · approved' + (f' – {f.s("note")}' if f.s('note') else ''))
            o.discount_percent = pct_below(o.unit.price, o.price)
        else:
            o.status, o.approval_note = 'cancelled', o.approval_note + f' · rejected – {f.s("note")}'
        o.save()
        app.close_todos('sale', o.pk)
        app.event('sale', o.pk, ('Discount approved' if ok else 'Rejected') + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o
    return run


regs['sale'].actions += [
    Act('approve', 'Approve the discount', a_sale_approve(True), when(('approval',), *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
    Act('reject', 'Reject', a_sale_approve(False), when(('approval',), *MGR), style='danger', fields=(('note', 'Reason', 'text', True),)),
    Act('complete', 'Completed (title transferred)', a_sale_complete, when(('signed',), *SALES), style='primary',
        fields=(('completion_on', 'Completion date', 'date', False), ('transfer_owner', 'Make the buyer the owner of the unit', 'bool', False))),
]
# sale sign: the approval and the discount
for a in regs['sale'].actions:
    if a.name == 'sign':
        a.allowed = when(('draft',), *SALES)


# ------------------------------------------------------------------ 15  commission rules, splits, brokers, trigger on collection
def rule_amount(rule, base):
    base = Decimal(base or 0)
    if rule.kind == 'fixed':
        amt = Decimal(rule.fixed or 0)
    elif rule.kind == 'tiered':
        amt, prev = ZERO, ZERO
        for t in sorted((x for x in (rule.tiers or []) if x.get('percent') is not None), key=lambda x: (x.get('upto') is None, x.get('upto') or 0)):
            upto = Decimal(str(t['upto'])) if t.get('upto') is not None else base
            slab = max(ZERO, min(base, upto) - prev)
            amt += slab * Decimal(str(t['percent'])) / 100
            prev = upto
            if prev >= base:
                break
    else:
        amt = base * Decimal(rule.percent or 0) / 100
    return max(money(amt), money(rule.min_amount or 0))


def make_commission2(agent, base, pct, lease=None, sale=None, user=None):
    """Commission by the active rule (fixed / % / tiered, split between agents, external broker share, trigger) – falls back to the deal %."""
    kind = 'lease' if lease else 'sale'
    deal = lease or sale
    rule = CommissionRule.objects.filter(is_active=True).filter(Q(applies_to=kind) | Q(applies_to='both')).order_by('id').first()
    party = deal.party
    out = []
    if rule is None:
        c = _plain_commission(agent, base, pct, lease=lease, sale=sale, user=user) if pct else None   # v1.56.1: the original, not this function (endless loop)
        if c and party.broker_id:
            bshare = money(c.amount * (party.broker.commission_percent or 0) / 100)
            if bshare:
                b = Commission.objects.create(number=app.next_number('commission', 'COM'), broker=party.broker, lease=lease, sale=sale, base=money(base), percent=pct, amount=bshare,
                                              share_percent=party.broker.commission_percent, note=f'External broker share of {c.number}')
                app.event('commission', b.pk, f'Broker share – waiting for approval', user)
        return c
    total = rule_amount(rule, base)
    broker_amt = ZERO
    if party.broker_id:
        broker_amt = money(total * (rule.broker_percent or 0) / 100)
    agents_amt = total - broker_amt
    split = [x for x in (rule.split or []) if x.get('agent') and x.get('percent')] or ([{'agent': agent.pk, 'percent': 100}] if agent else [])
    status = 'waiting' if rule.trigger == 'collection' else 'pending'
    for x in split:
        amt = money(agents_amt * Decimal(str(x['percent'])) / 100)
        if amt <= 0:
            continue
        c = Commission.objects.create(number=app.next_number('commission', 'COM'), agent_id=x['agent'], lease=lease, sale=sale, base=money(base), percent=rule.percent or ZERO, amount=amt,
                                      rule=rule, share_percent=Decimal(str(x['percent'])), trigger=rule.trigger, status=status, note=f'Rule {rule.name}')
        app.event('commission', c.pk, f'Created by rule {rule.name} ({c.get_status_display().lower()})', user)
        out.append(c)
    if broker_amt > 0:
        b = Commission.objects.create(number=app.next_number('commission', 'COM'), broker=party.broker, lease=lease, sale=sale, base=money(base), percent=rule.broker_percent, amount=broker_amt,
                                      rule=rule, share_percent=rule.broker_percent, trigger=rule.trigger, status=status, note=f'External broker {party.broker.name}')
        out.append(b)
    for c in out:
        if c.status == 'pending':
            app.tell(app.users_with_role('prop_commission'), f'Commission {c.number} to approve', f'{emp_name(c.agent) if c.agent_id else c.broker.name}: {cur()} {c.amount}',
                     'commission', c.pk, c.number, PAGE + f'r/commission/{c.pk}', summary=f'Approve commission {c.number}')
    return out[0] if out else None


# v1.56.1: keep the original – make_commission2 calls it when no commission rule is set up (it called A.make_commission, which is
# make_commission2 itself after this line: activating a lease or signing a sale without a commission rule ended in RecursionError)
_plain_commission = getattr(A, '_plain_make_commission', None) or A.make_commission
A._plain_make_commission = _plain_commission
A.make_commission = make_commission2
regs['commission'].form = ('agent', 'broker', 'lease', 'sale', 'base', 'percent', 'amount', 'trigger', 'note')
regs['commission'].cols = ('number', 'agent', 'broker', 'lease', 'sale', 'base', 'percent', 'share_percent', 'amount', 'trigger', 'status', 'external_ref')
regs['commission'].filters = ('status', 'agent', 'broker', 'trigger')
regs['commission'].info = ('rule', 'share_percent', 'created_at')
regs['commission'].edit_when = lambda o: o.status in ('pending', 'waiting')
regs['commission'].subtitle = ''

R(Reg('broker', Broker, 'External broker', 'External brokers', 'Setup', cols=('number', 'name', 'company_name', 'licence_no', 'phone', 'email', 'commission_percent', 'is_active'),
      form=('name', 'company_name', 'licence_no', 'email', 'phone', 'commission_percent', 'bank_details', 'is_active'), number=('broker', 'BRK'), manage=MGR, view=ALL_STAFF,
      restricted=('bank_details',), search=('name', 'company_name', 'licence_no'), filters=('is_active',), title='name', subtitle='company_name',
      children=(('Introduced', 'party', 'broker'), ('Commissions', 'commission', 'broker')), help='Outside agencies that bring tenants and buyers and receive a share of the commission.'))
R(Reg('commission_rule', CommissionRule, 'Commission rule', 'Commission rules', 'Setup', cols=('name', 'applies_to', 'kind', 'percent', 'fixed', 'trigger', 'broker_percent', 'is_active'),
      form=('name', 'applies_to', 'kind', 'percent', 'fixed', 'tiers', 'min_amount', 'trigger', 'split', 'broker_percent', 'is_active'), manage=MGR, view=ALL_STAFF, search=('name',),
      filters=('applies_to', 'kind', 'trigger', 'is_active'), title='name', deletable=True, files=False,
      tables={'tiers': [('upto', 'Up to (rent / price)', 'number'), ('percent', '%', 'number')], 'split': [('agent', 'Agent', 'opt', 'employees'), ('percent', 'Share %', 'number')]},
      help='The first active rule for leases / sales decides the commission: fixed, % or tiered, split between agents, broker share, paid on contract or on first collection.'))


def release_commissions(lease=None, sale=None, user=None):
    """First collection on a lease / sale releases the commissions that wait for collection."""
    qs = Commission.objects.filter(status='waiting', trigger='collection')
    qs = qs.filter(lease=lease) if lease else qs.filter(sale=sale)
    for c in qs:
        c.status = 'pending'
        c.save(update_fields=['status'])
        app.event('commission', c.pk, 'First collection received – waiting for approval', user)
        app.tell(app.users_with_role('prop_commission'), f'Commission {c.number} to approve', f'{emp_name(c.agent) if c.agent_id else c.broker.name}: {cur()} {c.amount}',
                 'commission', c.pk, c.number, PAGE + f'r/commission/{c.pk}', summary=f'Approve commission {c.number}')


# ------------------------------------------------------------------ 8, 10  refunds and deposit control
def a_refund_approve(ok):
    def run(o, request, f):
        o.status = 'approved' if ok else 'rejected'
        o.approved_by = request.user
        o.reason = (o.reason + ' · ' if o.reason and f.s('note') else o.reason) + (f.s('note') or '')
        o.save()
        app.close_todos('refund', o.pk)
        app.event('refund', o.pk, ('Approved' if ok else 'Rejected') + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        if ok:
            app.tell(app.users_with_role('prop_finance'), f'Refund {o.number} to pay', f'{o.party.name}: {cur()} {o.amount}', 'refund', o.pk, o.number, PAGE + f'r/refund/{o.pk}', summary=f'Pay refund {o.number}')
        return o
    return run


def a_refund_pay(o, request, f):
    o.status, o.paid_on = 'paid', f.dt('paid_on') or today()
    o.method = f.choice('method', regs['refund'].model._meta.get_field('method').choices, o.method)
    o.reference, o.external_ref = f.s('reference') or o.reference, f.s('external_ref') or o.external_ref
    o.save()
    app.close_todos('refund', o.pk)
    app.event('refund', o.pk, f'Paid {d(o.paid_on)} – {o.get_method_display()} {o.reference}', request.user)
    tell_party(o.party, 'refund', f'Refund {o.number} paid', f'Dear {o.party.name},\n\nYour refund of {cur()} {o.amount} ({o.get_kind_display().lower()}) was paid on {d(o.paid_on)} – {o.get_method_display()} {o.reference}.\n',
               {'amount': str(o.amount), 'number': o.number}, 'refund', o.pk, o.number, PAGE + f'r/refund/{o.pk}')
    call(app, 'ZeoAccounting', 'property_refund', o.pk)
    return o


R(Reg('refund', Refund, 'Refund', 'Refunds', 'Finance', cols=('number', 'kind', 'party', 'lease', 'sale', 'amount', 'status', 'paid_on', 'reference', 'external_ref'),
      form=('kind', 'party', 'lease', 'sale', 'reservation', 'moveout', 'amount', 'reason', 'method'), number=('refund', 'RFD'), manage=FIN, view=ALL_STAFF,
      search=('number', 'party__name', 'reference'), filters=('status', 'kind', 'party'), subtitle='party', edit_when=lambda o: o.status == 'requested', info=('approved_by', 'created_at', 'method'),
      actions=[Act('approve', 'Approve', a_refund_approve(True), when(('requested',), *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('reject', 'Reject', a_refund_approve(False), when(('requested',), *MGR), style='danger', fields=(('note', 'Reason', 'text', True),)),
               Act('pay', 'Paid', a_refund_pay, lambda o, r: (o.status == 'approved' or (o.status == 'requested' and not app.settings().refund_approval_above)) and app.any(r, *FIN), style='primary',
                   fields=(('paid_on', 'Paid on', 'date', False), ('method', 'Method', 'choice:transfer=Bank transfer|cheque=Cheque|cash=Cash|card=Card', False),
                           ('reference', 'Payment ref.', 'text', True), ('external_ref', 'Voucher no. (accounting)', 'text', False)))],
      help='Deposit, reservation fee, cancelled sale and overpayment refunds: requested → approved (above the limit in Settings) → paid.'))

regs['moveout'].form = ('lease', 'move_out_on', 'inspection_on', 'inspection', 'deduction_lines', 'keys_returned')
regs['moveout'].edit_when = lambda o: o.status in ('planned', 'inspected')
regs['moveout'].tables = {'deduction_lines': [('item', 'Item', 'text'), ('amount', 'Amount', 'number')]}
regs['moveout'].info = regs['moveout'].info + ('approval', 'approval_note')
regs['moveout'].cols = ('number', 'lease', 'move_out_on', 'inspection_on', 'deductions', 'unpaid', 'refund', 'approval', 'status')
regs['moveout'].pdf = 'Deposit statement'
regs['moveout'].children = (('Refunds', 'refund', 'moveout'),)
_mo_inspected = A.moveout_inspected


def moveout_inspected2(m, deductions, note, user=None, source='manual', lines=None):
    if lines:
        m.deduction_lines = lines
        deductions = sum(Decimal(str(x.get('amount') or 0)) for x in lines)
    _mo_inspected(m, deductions, note, user, source)
    s = app.settings()
    if m.deductions and s.deduction_approval_above is not None and m.deductions > s.deduction_approval_above and m.approval != 'approved':
        m.status, m.approval = 'approval', 'needed'
        m.save(update_fields=['status', 'approval'])
        approval_todo(f'Deductions on {m.number} to approve', f'Lease {m.lease.number}: deductions {cur()} {m.deductions} – {m.deduction_note}', 'moveout', m.pk, m.number)


A.moveout_inspected = moveout_inspected2


def a_mo_inspected2(o, request, f):
    lines = f.j('deduction_lines') or o.deduction_lines
    moveout_inspected2(o, f.n('deductions', ZERO), f.s('deduction_note'), request.user, lines=lines if isinstance(lines, list) and lines and not f.n('deductions') else None)
    return o


def a_mo_approve(ok):
    def run(o, request, f):
        o.approval, o.approval_note = ('approved' if ok else 'rejected'), f.s('note')
        o.status = 'inspected'
        if not ok:
            o.deductions, o.deduction_lines = ZERO, []
        o.save()
        app.close_todos('moveout', o.pk)
        app.event('moveout', o.pk, ('Deductions approved' if ok else 'Deductions rejected – set to zero') + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o
    return run


_mo_settle = A.a_moveout_settle


def a_mo_settle2(o, request, f):
    if o.approval == 'needed':
        raise AppError('The deductions need the manager\'s approval first.')
    r = _mo_settle(o, request, f)
    o.refresh_from_db()
    if o.refund > 0:
        make_refund(o.lease.party, 'deposit', o.refund, f'Deposit refund – move-out {o.number}', request.user, lease=o.lease, moveout=o)
    Unit.objects.filter(pk=o.lease.unit_id).update(next_available=None)
    tell_party(o.lease.party, 'moveout', f'Move-out {o.number} settled', f'Dear {o.lease.party.name},\n\nYour deposit settlement: deductions {cur()} {o.deductions}, unpaid bills {o.unpaid}, refund {o.refund}. '
               'The deposit statement is in the portal.\n', {'amount': str(o.refund), 'number': o.number}, 'moveout', o.pk, o.number, PAGE + f'r/moveout/{o.pk}')
    return r


for a in regs['moveout'].actions:
    if a.name == 'inspected':
        a.run = a_mo_inspected2
        a.fields = (('deductions', 'Deductions (total, when no lines)', 'decimal', False), ('deduction_note', 'Details', 'textarea', False))
        a.allowed = lambda o, r: o.status in ('planned', 'inspected') and app.any(r, *LEASING)
    if a.name == 'settle':
        a.run = a_mo_settle2
regs['moveout'].actions += [Act('approve', 'Approve deductions', a_mo_approve(True), when(('approval',), *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
                            Act('reject', 'Reject deductions', a_mo_approve(False), when(('approval',), *MGR), style='danger', fields=(('note', 'Reason', 'text', True),))]
_mo_plan = A.a_lease_moveout


def a_lease_moveout2(o, request, f):
    m = _mo_plan(o, request, f)
    if isinstance(m, MoveOut):
        mi = o.inspections.filter(kind='move_in').order_by('-id').first()
        ins = make_inspection('move_out', o.unit, lease=o, on=m.inspection_on or m.move_out_on, user=request.user, compare_to=mi)
        m.inspection = ins
        m.save(update_fields=['inspection'])
        Unit.objects.filter(pk=o.unit_id).update(next_available=m.move_out_on + timedelta(days=7))
    return m


for a in regs['lease'].actions:
    if a.name == 'moveout':
        a.run = a_lease_moveout2


# ------------------------------------------------------------------ 9  post-dated cheques: drawer, clearance, bounce fee, replacement, deposit slips
regs['cheque'].form = ('lease', 'party', 'bill', 'cheque_no', 'bank', 'drawer_name', 'beneficiary', 'amount', 'cheque_date', 'note')
regs['cheque'].cols = ('number', 'party', 'lease', 'cheque_no', 'bank', 'drawer_name', 'amount', 'cheque_date', 'status', 'slip')
regs['cheque'].info = ('deposited_on', 'cleared_on', 'replaced_by', 'bounce_fee_bill', 'slip')
regs['cheque'].filters = ('status', 'lease', 'slip', 'bank')
_cheque_status = A.a_cheque_status


def a_cheque_status2(new, text):
    base = _cheque_status(new, text)

    def run(o, request, f):
        r = base(o, request, f)
        o.refresh_from_db()
        if new == 'cleared':
            o.cleared_on = f.dt('cleared_on') or today()
            o.save(update_fields=['cleared_on'])
            if o.lease_id:
                release_commissions(lease=o.lease, user=request.user)
        if new == 'bounced':
            s = app.settings()
            if s.bounce_fee and not o.bounce_fee_bill_id:
                b = make_bill(party=o.party, unit=o.lease.unit if o.lease_id else None, lease=o.lease, ch=charge('fee', 'BOUNCE_FEE'), amount=s.bounce_fee, due=today() + timedelta(days=7),
                              desc=f'Bounced cheque fee – cheque {o.cheque_no}', source='bounce', vat_percent=ZERO)
                o.bounce_fee_bill = b
                o.save(update_fields=['bounce_fee_bill'])
            tell_party(o.party, 'cheque_bounced', 'Cheque returned unpaid', f'Dear {o.party.name},\n\nYour cheque {o.cheque_no} ({o.bank}) for {cur()} {o.amount} was returned unpaid. '
                       + (f'A fee of {cur()} {s.bounce_fee} applies. ' if s.bounce_fee else '') + 'Please contact us to replace it.\n',
                       {'amount': str(o.amount), 'number': o.cheque_no}, 'cheque', o.pk, o.number, PAGE + f'r/cheque/{o.pk}')
        return r
    return run


def a_cheque_replace2(o, request, f):
    no = f.s('cheque_no')
    if not no:
        raise AppError('Enter the replacement cheque number.', 'cheque_no')
    new = Cheque.objects.create(number=app.next_number('cheque', 'CHQ'), lease=o.lease, party=o.party, bill=o.bill, cheque_no=no, bank=f.s('bank') or o.bank, amount=f.n('amount') or o.amount,
                                cheque_date=f.dt('cheque_date') or today(), drawer_name=o.drawer_name, beneficiary=o.beneficiary, note=f'Replaces cheque {o.cheque_no}')
    o.status, o.replaced_by, o.note = 'replaced', new, (o.note + ' · ' if o.note else '') + f'Replaced by {no}'
    o.save(update_fields=['status', 'replaced_by', 'note'])
    app.event('cheque', o.pk, f'Replaced by cheque {no} ({new.number})', request.user)
    app.event('cheque', new.pk, f'Replacement of cheque {o.cheque_no} ({o.number})', request.user)
    return new


for a in regs['cheque'].actions:
    if a.name in ('clear', 'bounce'):
        a.run = a_cheque_status2(a.name == 'clear' and 'cleared' or 'bounced', 'Cleared' if a.name == 'clear' else 'Bounced')
        if a.name == 'clear':
            a.fields = (('cleared_on', 'Cleared on', 'date', False),)
    if a.name == 'replace':
        a.run = a_cheque_replace2
        a.fields = (('cheque_no', 'Replacement cheque no.', 'text', True), ('bank', 'Bank', 'text', False), ('amount', 'Amount', 'decimal', False), ('cheque_date', 'Cheque date', 'date', False))
        a.label = 'Replace'


def a_slip_add(o, request, f):
    ids = f.ids('cheques')
    n = 0
    for c in Cheque.objects.filter(pk__in=ids, status='received'):
        c.status, c.deposited_on, c.slip = 'deposited', o.deposited_on, o
        c.save(update_fields=['status', 'deposited_on', 'slip'])
        app.event('cheque', c.pk, f'Deposited – slip {o.number} ({o.bank})', request.user)
        n += 1
    o.count = o.cheques.count()
    o.total = o.cheques.aggregate(s=Sum('amount'))['s'] or ZERO
    o.save(update_fields=['count', 'total'])
    app.event('slip', o.pk, f'{n} cheque(s) added – total {cur()} {o.total}', request.user)
    return {'message': f'{n} cheque(s) on the slip; total {cur()} {o.total}.'}


R(Reg('slip', DepositSlip, 'Bank deposit slip', 'Bank deposit slips', 'Finance', cols=('number', 'bank', 'deposited_on', 'count', 'total', 'reference', 'status'),
      form=('bank', 'deposited_on', 'reference', 'note'), number=('slip', 'DEP'), manage=FIN, view=FIN + VIEW, search=('number', 'bank', 'reference'), filters=('status', 'bank'),
      subtitle='bank', edit_when=lambda o: o.status == 'open', children=(('Cheques', 'cheque', 'slip'),), pdf='Deposit slip',
      actions=[Act('add', 'Add cheques', a_slip_add, when(('open',), *FIN), style='primary', fields=(('cheques', 'Cheques in hand', 'opt-multi:cheques-in-hand', True),)),
               Act('banked', 'Banked', lambda o, r, f: (setattr(o, 'status', 'banked'), setattr(o, 'reference', f.s('reference') or o.reference), o.save(), app.event('slip', o.pk, f'Banked – {o.reference}', r.user), o)[-1],
                   when(('open',), *FIN), fields=(('reference', 'Bank slip ref.', 'text', False),))],
      help='The cheques taken to the bank on one day; prints the deposit slip.'))


# ------------------------------------------------------------------ 9.1, 9.4  billing: credit notes, disputes, promises, settlement plans, recurring charges
regs['bill'].form = ('party', 'owner', 'unit', 'lease', 'sale', 'charge', 'description', 'period_from', 'period_to', 'due_date', 'amount')
regs['bill'].cols = ('number', 'party', 'owner', 'unit', 'charge', 'description', 'due_date', 'total', 'paid', 'status', 'disputed', 'promise_date', 'external_ref', 'external_status')
regs['bill'].filters = ('status', 'external_status', 'charge', 'source', 'party', 'owner', 'lease', 'disputed', 'is_credit')
regs['bill'].info = regs['bill'].info + ('is_credit', 'credit_of', 'disputed', 'dispute_note', 'promise_date', 'promise_note', 'escalated_on', 'settlement')
regs['bill'].pdf = 'Invoice'


def a_bill_credit(o, request, f):
    amt = money(f.n('amount') or o.balance)
    if amt <= 0 or amt > o.total:
        raise AppError(f'Up to {cur()} {o.total} can be credited.', 'amount')
    cn = Bill.objects.create(number=app.next_number('credit', 'CN'), party=o.party, owner=o.owner, unit=o.unit, lease=o.lease, sale=o.sale, charge=o.charge, description=f'Credit note for {o.number}: {f.s("reason")}'[:200],
                             due_date=today(), amount=amt, vat=ZERO, total=amt, paid=amt, status='paid', source='credit', is_credit=True, credit_of=o)
    rc = Receipt.objects.create(number=app.next_number('receipt', 'RCT'), party=o.party, owner=o.owner, received_on=today(), amount=amt, method='credit', reference=f'Credit note {cn.number}', note=f.s('reason')[:200])
    post_receipt(rc, request.user, [o.pk])
    app.event('bill', o.pk, f'Credit note {cn.number} for {cur()} {amt} – {f.s("reason")}', request.user)
    app.event('bill', cn.pk, f'Credit note for {o.number} – {f.s("reason")}', request.user)
    return cn


def a_bill_dispute(o, request, f):
    o.disputed, o.dispute_note = not o.disputed, f.s('note')[:200]
    o.save(update_fields=['disputed', 'dispute_note'])
    app.event('bill', o.pk, ('Disputed – ' if o.disputed else 'Dispute closed – ') + f.s('note'), request.user)
    return o


def a_bill_promise(o, request, f):
    o.promise_date, o.promise_note = f.dt('promise_date', True, 'Promised date'), f.s('note')[:200]
    o.save(update_fields=['promise_date', 'promise_note'])
    app.event('bill', o.pk, f'Promise to pay on {d(o.promise_date)} – {f.s("note")}', request.user)
    return o


regs['bill'].actions += [
    Act('credit', 'Credit note', a_bill_credit, lambda o, r: o.status in ('issued', 'part_paid', 'paid') and not o.is_credit and app.any(r, *FIN), fields=(('amount', 'Amount (empty = balance)', 'decimal', False), ('reason', 'Reason', 'text', True))),
    Act('dispute', 'Dispute / close dispute', a_bill_dispute, lambda o, r: o.status in ('issued', 'part_paid') and app.any(r, *FIN + LEASING), fields=(('note', 'Note', 'text', True),)),
    Act('promise', 'Promise to pay', a_bill_promise, lambda o, r: o.status in ('issued', 'part_paid') and app.any(r, *FIN + LEASING), fields=(('promise_date', 'Promised date', 'date', True), ('note', 'Note', 'text', False))),
]
PAY_CHOICES = Bill._meta.get_field('status').choices


def a_settle_activate(o, request, f):
    tot = sum(Decimal(str(x.get('amount') or 0)) for x in (o.instalments or []))
    if not o.instalments or abs(tot - o.total) > 1:
        raise AppError(f'The instalments add up to {cur()} {tot}; the plan is for {o.total}.', 'instalments')
    bills = Bill.objects.filter(party=o.party, status__in=('issued', 'part_paid'), due_date__lte=today())
    if o.lease_id:
        bills = bills.filter(lease=o.lease)
    n = bills.update(settlement=o)
    o.status = 'active'
    o.save(update_fields=['status'])
    app.event('settlement', o.pk, f'Active – {n} overdue billing record(s) follow the plan', request.user)
    tell_party(o.party, 'reminder_after', 'Your payment plan', f'Dear {o.party.name},\n\nWe agreed a payment plan of {cur()} {o.total} in {len(o.instalments)} instalment(s): '
               + ', '.join(f'{x.get("amount")} on {d(x.get("due"))}' for x in o.instalments) + '.\n', {'amount': str(o.total), 'number': o.number}, 'settlement', o.pk, o.number, PAGE + f'r/settlement/{o.pk}')
    return o


R(Reg('settlement', SettlementPlan, 'Settlement plan', 'Settlement plans', 'Finance', cols=('number', 'party', 'lease', 'total', 'status', 'created_at'),
      form=('party', 'lease', 'total', 'instalments', 'note'), number=('settlement', 'STL'), manage=FIN + MGR, view=ALL_STAFF, search=('number', 'party__name'), filters=('status', 'party'),
      subtitle='party', edit_when=lambda o: o.status == 'draft', tables={'instalments': [('due', 'Due', 'date'), ('amount', 'Amount', 'number')]}, children=(('Billing records', 'bill', 'settlement'),),
      actions=[Act('activate', 'Activate', a_settle_activate, when(('draft',), *FIN + MGR), style='primary'),
               Act('done', 'Completed', lambda o, r, f: (setattr(o, 'status', 'done'), o.save(update_fields=['status']), o)[-1], when(('active',), *FIN)),
               Act('broken', 'Broken', lambda o, r, f: (setattr(o, 'status', 'broken'), o.save(update_fields=['status']), app.event('settlement', o.pk, f'Broken – {f.s("reason")}', r.user), o)[-1],
                   when(('active',), *FIN), style='danger', fields=(('reason', 'Reason', 'text', False),))],
      help='Overdue amounts split into agreed instalments; reminders follow the plan dates instead of the original due dates.'))


def recurring_saver(o, request, creating, old):
    if not o.party_id and not o.owner_id:
        raise AppError('Choose who pays.', 'party')
    if o.lease_id and not o.party_id:
        o.party = o.lease.party
    if o.lease_id and not o.unit_id:
        o.unit = o.lease.unit
    o.save()
    return o


def run_recurring(limit_date=None):
    t = limit_date or today()
    n = 0
    for rc in RecurringCharge.objects.filter(status='active', next_due__lte=t).select_related('charge'):
        guard = 0
        while rc.next_due <= t and guard < 36:
            if rc.until and rc.next_due > rc.until:
                rc.status = 'ended'
                break
            step = {'month': 1, 'quarter': 3, 'year': 12}[rc.every]
            b = make_bill(party=rc.party, owner=rc.owner, unit=rc.unit, lease=rc.lease, ch=rc.charge, amount=rc.amount, due=rc.next_due, desc=rc.description or rc.charge.name,
                          pfrom=rc.next_due, pto=add_months(rc.next_due, step) - timedelta(days=1), source='recurring')
            b.recurring = rc
            b.save(update_fields=['recurring'])
            rc.next_due = add_months(rc.next_due, step)
            n += 1
            guard += 1
        rc.save(update_fields=['next_due', 'status'])
    return n


R(Reg('recurring', RecurringCharge, 'Recurring charge', 'Recurring charges', 'Finance', cols=('number', 'party', 'owner', 'unit', 'charge', 'description', 'amount', 'every', 'next_due', 'until', 'status'),
      form=('party', 'owner', 'unit', 'lease', 'charge', 'description', 'amount', 'every', 'next_due', 'until', 'status'), number=('recurring', 'RC'), manage=FIN, view=ALL_STAFF,
      search=('number', 'description', 'party__name'), filters=('status', 'every', 'charge'), subtitle='party', saver=recurring_saver, children=(('Billing records', 'bill', 'recurring'),),
      actions=[Act('bill_now', 'Bill the next period now', lambda o, r, f: {'message': f'{run_recurring(o.next_due)} billing record(s) made.'}, when(('active',), *FIN), style='primary')],
      help='Service, parking, utility or chiller charges billed every month, quarter or year by the daily job.'))

_post_receipt = A.post_receipt


def post_receipt2(r, user, bill_ids=None):
    out = _post_receipt(r, user, bill_ids)
    for b in Bill.objects.filter(allocations__receipt=r).distinct():
        if b.lease_id:
            release_commissions(lease=b.lease, user=user)
        if b.sale_id:
            release_commissions(sale=b.sale, user=user)
        if b.settlement_id and b.settlement.status == 'active' and not Bill.objects.filter(settlement=b.settlement).exclude(status__in=('paid', 'void')).exists():
            b.settlement.status = 'done'
            b.settlement.save(update_fields=['status'])
    if r.party_id and r.method != 'credit':
        tell_party(r.party, 'receipt', f'Payment received – {r.number}', f'Dear {r.party.name},\n\nThank you – we received {cur()} {r.amount} on {d(r.received_on)} ({r.get_method_display()} {r.reference}).\n',
                   {'amount': str(r.amount), 'number': r.number}, 'receipt', r.pk, r.number, PAGE + f'r/receipt/{r.pk}')
    return out


A.post_receipt = post_receipt2
regs['receipt'].pdf = 'Receipt'
regs['receipt'].actions.append(Act('reapply', 'Re-apply unallocated money', lambda o, r, f: (reapply(o, r.user, f.ids('bills')), o)[-1],
                                   lambda o, r: o.status == 'posted' and o.allocated < o.amount and app.any(r, *FIN), fields=(('bills', 'Only these billing records', 'opt-multi:bills', False),)))


def reapply(r, user, bill_ids=None):
    from .models import Allocation
    left = money(r.amount - r.allocated)
    qs = Bill.objects.filter(status__in=('issued', 'part_paid'))
    qs = qs.filter(party=r.party) if r.party_id else qs.filter(owner=r.owner)
    if bill_ids:
        qs = qs.filter(pk__in=bill_ids)
    for b in qs.order_by('due_date', 'id'):
        if left <= 0:
            break
        take = min(left, b.balance)
        if take > 0:
            Allocation.objects.create(receipt=r, bill=b, amount=take)
            left -= take
            refresh_bill(b)
    r.allocated = money(r.amount - left)
    r.save(update_fields=['allocated'])
    app.event('receipt', r.pk, f'Re-applied – {cur()} {r.allocated} allocated' + (f', {left} still unallocated' if left else ''), user)


# ------------------------------------------------------------------ 11, 19  document control and templates
def doc_saver(o, request, creating, old):
    if not any((o.party_id, o.owner_id, o.unit_id, o.building_id, o.lease_id, o.sale_id, o.vendor_id)):
        raise AppError('Say what the document belongs to (tenant, owner, unit, building, lease, sale or vendor).', 'party')
    t = today()
    if o.expires_on:
        days = app.settings().document_alert_days or 30
        o.status = 'expired' if o.expires_on < t else ('expiring' if o.expires_on <= t + timedelta(days=days) else 'valid')
    elif o.status in ('expiring', 'expired'):
        o.status = 'valid'
    o.save()
    return o


def a_doc_replace(o, request, f):
    new = Document.objects.create(category=o.category, name=o.name, party=o.party, owner=o.owner, unit=o.unit, building=o.building, lease=o.lease, sale=o.sale, vendor=o.vendor,
                                  issued_on=f.dt('issued_on') or today(), expires_on=f.dt('expires_on'), mandatory=o.mandatory, version=o.version + 1, number=app.next_number('document', 'DOC'))
    doc_saver(new, request, True, None)
    o.status = 'replaced'
    o.save(update_fields=['status'])
    app.event('document', o.pk, f'Replaced by version {new.version} ({new.number})', request.user)
    app.event('document', new.pk, f'Version {new.version} of {o.number} – attach the new file', request.user)
    return new


R(Reg('document', Document, 'Document', 'Documents', 'Portfolio', cols=('number', 'category', 'name', 'party', 'owner', 'unit', 'lease', 'expires_on', 'mandatory', 'version', 'status'),
      form=('category', 'name', 'party', 'owner', 'unit', 'building', 'lease', 'sale', 'vendor', 'issued_on', 'expires_on', 'mandatory', 'note'), number=('document', 'DOC'),
      manage=DEALS + FIN, view=ALL_STAFF, search=('number', 'name', 'party__name', 'owner__name'), filters=('category', 'status', 'mandatory', 'party', 'unit', 'building'), title='name',
      subtitle='category', saver=doc_saver, edit_when=lambda o: o.status != 'replaced', info=('version', 'created_at'),
      actions=[Act('replace', 'New version', a_doc_replace, lambda o, r: o.status != 'replaced' and app.any(r, *DEALS + FIN), style='primary',
                   fields=(('issued_on', 'Issued on', 'date', False), ('expires_on', 'Expires on', 'date', False)))],
      help='Every contract, ID, licence, title deed, NOC, insurance and permit with its expiry date and version. Attach the file under Files. The daily job warns before expiry.'))
R(Reg('doc_template', DocTemplate, 'Document template', 'Document templates', 'Setup', cols=('code', 'name', 'kind', 'country', 'language', 'review_status'),
      form=('code', 'name', 'kind', 'country', 'language', 'body', 'is_active'), manage=MGR, view=ALL_STAFF, search=('code', 'name'), filters=('kind', 'country', 'language', 'review_status'),
      title='name', review=True, deletable=True, files=False, help='Clauses printed on leases, sale agreements, receipts and statements – per country and language; {placeholders} are filled in.'))
R(Reg('message_template', MessageTemplate, 'Message template', 'Message templates', 'Setup', cols=('code', 'event', 'channel', 'language', 'subject', 'review_status'),
      form=('code', 'event', 'channel', 'language', 'subject', 'body', 'is_active'), manage=MGR, view=ALL_STAFF, search=('code', 'subject'), filters=('event', 'channel', 'language', 'review_status'),
      title='subject', review=True, deletable=True, files=False, help='What tenants and owners receive on each event by e-mail, SMS, WhatsApp or portal notification. Set the provider in Settings.'))


# ------------------------------------------------------------------ 16.2  service charges / CAM
def budget_saver(o, request, creating, old):
    o.budget_total = money(sum(Decimal(str(x.get('budget') or 0)) for x in (o.lines or [])))
    o.actual_total = money(sum(Decimal(str(x.get('actual') or 0)) for x in (o.lines or [])))
    o.save()
    return o


def budget_shares(o):
    """{unit: share} of the building's units by the budget basis (only units with an occupant / owner to charge)."""
    units = list(o.building.units.filter(parent__isnull=True))
    rows = []
    for u in units:
        if o.charge_to == 'tenants':
            lease = active_lease(u) or Lease.objects.filter(units=u, status__in=('active', 'notice')).order_by('-start').first()   # a unit on a multi-unit lease
            if not lease or not lease.service_charge:
                continue
            rows.append((u, lease.party, None, u.rentable_area or u.area or ZERO))
        else:
            if not u.owner_id:
                continue
            rows.append((u, None, u.owner, u.rentable_area or u.area or ZERO))
    if not rows:
        return []
    if o.basis == 'area' and sum(r[3] for r in rows) > 0:
        tot = sum(r[3] for r in rows)
        return [(u, p, ow, Decimal(a) / tot) for u, p, ow, a in rows]
    if o.basis == 'share':
        return [(u, p, ow, Decimal(1) / len(rows)) for u, p, ow, a in rows]   # ownership share % lives on co_owners; equal between units
    return [(u, p, ow, Decimal(1) / len(rows)) for u, p, ow, a in rows]


def a_budget_bill(o, request, f):
    n_inst = f.i('instalments') or app.settings().service_charge_instalments or 4
    shares = budget_shares(o)
    if not shares:
        raise AppError('No unit to charge: tenants need a lease with "tenant pays service charges", owners need to be set on the units.')
    ch = charge('service', 'SERVICE')
    made, total = 0, ZERO
    start = date(o.year, 1, 1)
    for u, p, ow, share in shares:
        yearly = money(o.budget_total * share)
        part = money(yearly / n_inst)
        for i in range(n_inst):
            due = add_months(start, i * 12 // n_inst)
            amt = part if i < n_inst - 1 else yearly - part * (n_inst - 1)
            if amt <= 0:
                continue
            b = make_bill(party=p, owner=ow, unit=u, lease=(active_lease(u) or Lease.objects.filter(units=u, status__in=('active', 'notice')).first()) if p else None, ch=ch, amount=amt, due=due, desc=f'Service charge estimate {o.year} {i + 1}/{n_inst} – {u.label}',
                          pfrom=due, pto=add_months(start, (i + 1) * 12 // n_inst) - timedelta(days=1), source='service')
            b.budget = o
            b.save(update_fields=['budget'])
            made += 1
            total += amt
    o.billed_total, o.status = money(o.billed_total + total), 'billed'
    o.save(update_fields=['billed_total', 'status'])
    app.event('budget', o.pk, f'{made} estimate billing record(s) – {cur()} {total} over {len(shares)} unit(s)', request.user)
    return {'message': f'{made} billing record(s) made for {len(shares)} unit(s): {cur()} {total}.'}


def a_budget_reconcile(o, request, f):
    shares = budget_shares(o)
    if o.actual_total <= 0:
        raise AppError('Enter the actual amounts on the budget lines first.', 'lines')
    ch = charge('service', 'SERVICE')
    extra, credit = 0, 0
    for u, p, ow, share in shares:
        billed = sum((b.amount for b in o.bills.filter(unit=u).exclude(status='void').exclude(is_credit=True)), ZERO)   # net of VAT, like the budget
        actual = money(o.actual_total * share)
        diff = money(actual - billed)
        if diff > 1:
            b = make_bill(party=p, owner=ow, unit=u, lease=active_lease(u) if p else None, ch=ch, amount=diff, due=today() + timedelta(days=30), desc=f'Service charge reconciliation {o.year} – {u.label}', source='service')
            b.budget = o
            b.save(update_fields=['budget'])
            extra += 1
        elif diff < -1:
            cn = Bill.objects.create(number=app.next_number('credit', 'CN'), party=p, owner=ow, unit=u, charge=ch, description=f'Service charge reconciliation {o.year} – {u.label} (credit)', due_date=today(),
                                     amount=-diff, total=-diff, paid=-diff, status='paid', source='credit', is_credit=True, budget=o)
            rc = Receipt.objects.create(number=app.next_number('receipt', 'RCT'), party=p, owner=ow, received_on=today(), amount=-diff, method='credit', reference=f'Credit note {cn.number}')
            post_receipt(rc, request.user)
            credit += 1
    o.status = 'reconciled'
    o.save(update_fields=['status'])
    app.event('budget', o.pk, f'Reconciled: actual {cur()} {o.actual_total} vs billed {o.billed_total} – {extra} extra bill(s), {credit} credit note(s)', request.user)
    return {'message': f'Reconciled – {extra} extra billing record(s), {credit} credit note(s).'}


R(Reg('budget', ServiceChargeBudget, 'Service charge budget', 'Service charge budgets', 'Finance', cols=('number', 'building', 'year', 'basis', 'charge_to', 'budget_total', 'billed_total', 'actual_total', 'status'),
      form=('building', 'year', 'basis', 'charge_to', 'lines', 'note'), number=('budget', 'SCB'), manage=FIN + MGR, view=ALL_STAFF, search=('number', 'building__name'),
      filters=('status', 'building', 'year', 'charge_to'), subtitle='building', saver=budget_saver, child_of='building', edit_when=lambda o: o.status in ('draft', 'approved', 'billed'),
      tables={'lines': [('category', 'Category', 'text'), ('budget', 'Budget', 'number'), ('actual', 'Actual', 'number')]}, children=(('Billing records', 'bill', 'budget'),),
      actions=[Act('approve', 'Approve', lambda o, r, f: (setattr(o, 'status', 'approved'), o.save(update_fields=['status']), app.event('budget', o.pk, 'Approved', r.user), o)[-1], when(('draft',), *MGR), style='primary'),
               Act('bill', 'Bill the estimates', a_budget_bill, when(('approved',), *FIN), style='primary', fields=(('instalments', 'Instalments (empty = Settings)', 'int', False),)),
               Act('reconcile', 'Year-end reconciliation', a_budget_reconcile, when(('billed',), *FIN), fields=(), confirm='Compare the actual costs with the estimates billed and make the difference bills / credit notes?')],
      help='CAM: the annual budget of a building allocated to the units by area or equally, billed as estimates in instalments and reconciled against the actual costs at year end.'))


# ------------------------------------------------------------------ 17  meters and utility billing
def meter_saver(o, request, creating, old):
    if o.unit_id and not o.building_id:
        o.building = o.unit.building
    if not o.unit_id and not o.building_id:
        raise AppError('Choose the unit, or the building for a shared meter.', 'unit')
    if not o.unit_id:
        o.shared = True
    o.save()
    return o


def reading_saver(o, request, creating, old):
    m = o.meter
    prev = m.readings.exclude(pk=o.pk).filter(read_on__lte=o.read_on).order_by('-read_on', '-id').first()
    o.previous = prev.reading if prev else (m.last_reading if creating and m.last_read_on and m.last_read_on <= o.read_on else ZERO)
    if o.reading < o.previous:
        raise AppError(f'The reading is below the previous one ({o.previous}).', 'reading')
    o.consumption = o.reading - o.previous
    o.amount = money(o.consumption * (m.tariff or 0))
    last = list(m.readings.exclude(pk=o.pk).order_by('-read_on')[:3])
    avg = (sum(x.consumption for x in last) / len(last)) if last else None
    o.unusual = bool(avg and avg > 0 and o.consumption > avg * Decimal('1.5'))
    if not o.read_by_id and request is not None:
        o.read_by = app.my_emp(request)
    o.save()
    if m.last_read_on is None or o.read_on >= m.last_read_on:
        m.last_reading, m.last_read_on = o.reading, o.read_on
        m.save(update_fields=['last_reading', 'last_read_on'])
    if o.unusual:
        app.tell(app.users_with_role('prop_manager'), f'Unusual consumption on {m}', f'{o.consumption} {m.unit_name} ({(o.read_on - (prev.read_on if prev else o.read_on)).days} days) – average {avg:.0f}',
                 'reading', o.pk, str(o.reading), PAGE + f'r/reading/{o.pk}')
    return o


def a_reading_bill(o, request, f):
    m = o.meter
    if o.amount <= 0:
        raise AppError('Nothing to bill – set the tariff on the meter.')
    ch = charge('utility', 'UTILITY')
    made = []
    if m.unit_id and not m.shared:
        lease = active_lease(m.unit)
        if lease is None:
            raise AppError(f'{m.unit.label} has no active lease to charge.')
        made.append(make_bill(party=lease.party, unit=m.unit, lease=lease, ch=ch, amount=o.amount, due=today() + timedelta(days=14),
                              desc=f'{m.get_kind_display()} {o.consumption} {m.unit_name} ({d(o.read_on)}) – {m.unit.label}', source='utility'))
    else:
        rows = [(u, active_lease(u)) for u in m.building.units.filter(parent__isnull=True)]
        rows = [(u, l) for u, l in rows if l]
        tot = sum(((u.rentable_area or u.area or 1) for u, l in rows), ZERO) or Decimal(len(rows) or 1)
        for u, l in rows:
            share = Decimal(u.rentable_area or u.area or 1) / tot if any((u.rentable_area or u.area) for u, l in rows) else Decimal(1) / len(rows)
            amt = money(o.amount * share)
            if amt > 0:
                made.append(make_bill(party=l.party, unit=u, lease=l, ch=ch, amount=amt, due=today() + timedelta(days=14), desc=f'Shared {m.get_kind_display().lower()} {d(o.read_on)} – {u.label} ({share * 100:.1f}%)', source='utility'))
    for b in made:
        b.meter_reading = o
        b.save(update_fields=['meter_reading'])
    o.status = 'billed'
    o.save(update_fields=['status'])
    app.event('reading', o.pk, f'{len(made)} utility billing record(s) – {cur()} {sum(b.total for b in made)}', request.user)
    return {'message': f'{len(made)} billing record(s) made.'}


R(Reg('meter', Meter, 'Meter', 'Meters', 'Portfolio', cols=('number', 'kind', 'meter_no', 'unit', 'building', 'shared', 'tariff', 'unit_name', 'last_reading', 'last_read_on', 'is_active'),
      form=('kind', 'meter_no', 'unit', 'building', 'shared', 'tariff', 'unit_name', 'is_active'), number=('meter', 'MTR'), manage=FIN + MGR + ('prop_leasing',), view=ALL_STAFF,
      search=('number', 'meter_no', 'unit__unit_no'), filters=('kind', 'building', 'shared', 'is_active'), title='meter_no', subtitle='unit', saver=meter_saver,
      children=(('Readings', 'reading', 'meter'),), help='Electricity, water, gas and chiller meters per unit or shared per building (allocated to the units by area).'))
R(Reg('reading', MeterReading, 'Meter reading', 'Meter readings', 'Finance', cols=('meter', 'read_on', 'previous', 'reading', 'consumption', 'amount', 'unusual', 'status'),
      form=('meter', 'read_on', 'reading', 'read_by', 'note'), manage=FIN + MGR + ('prop_leasing',), view=ALL_STAFF, search=('meter__meter_no',), filters=('status', 'unusual', 'meter'),
      title='reading', subtitle='meter', saver=reading_saver, child_of='meter', edit_when=lambda o: o.status == 'read', children=(('Billing records', 'bill', 'meter_reading'),),
      actions=[Act('bill', 'Bill the consumption', a_reading_bill, when(('read',), *FIN), style='primary'),
               Act('skip', 'Do not bill', lambda o, r, f: (setattr(o, 'status', 'skipped'), o.save(update_fields=['status']), o)[-1], when(('read',), *FIN))],
      help='Consumption = reading – previous; amount = consumption × tariff; unusual when 50% above the average of the last readings.'))


# ------------------------------------------------------------------ 5.2  listings
def listing_saver(o, request, creating, old):
    if creating:
        o.agent = o.agent or app.my_emp(request)
        o.price_history = [{'date': d(today()), 'price': float(o.price)}]
        if not o.channels:
            o.channels = ['website']
    elif old and old.price != o.price:
        o.price_history = list(o.price_history or []) + [{'date': d(today()), 'price': float(o.price)}]
    o.save()
    return o


def a_listing_publish(o, request, f):
    if o.status not in ('approved',) and not app.any(request, *MGR):
        raise AppError('The listing needs approval first.')
    o.status, o.publish_from = 'published', f.dt('publish_from') or o.publish_from or today()
    o.publish_to = f.dt('publish_to') or o.publish_to
    o.save()
    app.event('listing', o.pk, f'Published from {d(o.publish_from)}' + (f' to {d(o.publish_to)}' if o.publish_to else '') + f' on {", ".join(o.channels or [])}', request.user)
    if linked(app, 'ZeoCRM'):
        r = call(app, 'ZeoCRM', 'listing', {'unit_id': o.unit_id, 'title': o.title, 'kind': o.kind, 'price': float(o.price), 'description': o.description, 'listing_id': o.pk})
        if r and isinstance(r, dict) and r.get('id'):
            o.crm_listing_id = r['id']
            o.save(update_fields=['crm_listing_id'])
    return o


R(Reg('listing', Listing, 'Listing', 'Listings', 'Deals', cols=('number', 'unit', 'kind', 'title', 'price', 'status', 'publish_from', 'publish_to', 'agent', 'views', 'enquiries'),
      form=('unit', 'kind', 'title', 'description', 'price', 'channels', 'publish_from', 'publish_to', 'permit_no', 'agent'), number=('listing', 'LST'), manage=DEALS, view=ALL_STAFF,
      search=('number', 'title', 'unit__unit_no', 'unit__building__name'), filters=('status', 'kind', 'unit'), title='title', subtitle='unit', saver=listing_saver,
      edit_when=lambda o: o.status in ('draft', 'approval', 'approved', 'published'), info=('price_history', 'views', 'enquiries', 'crm_listing_id'),
      tables={'price_history': [('date', 'Date', 'date'), ('price', 'Price', 'number')]},
      actions=[Act('submit', 'Submit for approval', lambda o, r, f: (setattr(o, 'status', 'approval'), o.save(update_fields=['status']), approval_todo(f'Listing {o.number} to approve', o.title, 'listing', o.pk, o.number), o)[-1],
                   when(('draft', 'withdrawn'), *DEALS), style='primary'),
               Act('approve', 'Approve', lambda o, r, f: (setattr(o, 'status', 'approved'), o.save(update_fields=['status']), app.close_todos('listing', o.pk), app.event('listing', o.pk, 'Approved', r.user), o)[-1],
                   when(('approval', 'draft'), *MGR), style='primary'),
               Act('publish', 'Publish', a_listing_publish, when(('approved', 'draft'), *DEALS), style='primary', fields=(('publish_from', 'From', 'date', False), ('publish_to', 'Until', 'date', False))),
               Act('withdraw', 'Withdraw', lambda o, r, f: (setattr(o, 'status', 'withdrawn'), o.save(update_fields=['status']), app.event('listing', o.pk, f'Withdrawn – {f.s("reason")}', r.user), o)[-1],
                   when(('published', 'approved'), *DEALS), style='danger', fields=(('reason', 'Reason', 'text', False),))],
      help='Advertise a unit: approval, publication window, price history, channels. Published listings appear on the public page (Settings) and in the Bayut / Property Finder feed; '
           'a unit that is let or sold is withdrawn automatically.'))


# ------------------------------------------------------------------ 12  inspections and keys
def template_for(unit):
    cat = unit.unit_type.category if unit.unit_type_id else 'residential'
    return InspectionTemplate.objects.filter(is_active=True).exclude(review_status='to_review').filter(Q(applies_to=cat) | Q(applies_to='all')).order_by('applies_to').first()


def make_inspection(kind, unit, lease=None, sale=None, on=None, user=None, compare_to=None):
    t = template_for(unit)
    ins = Inspection.objects.create(number=app.next_number('inspection', 'INS'), kind=kind, unit=unit, lease=lease, sale=sale, template=t, scheduled_on=on or today(), compare_to=compare_to,
                                    items=[{'room': r.get('room', ''), 'item': r.get('item', ''), 'condition': 'ok', 'severity': '', 'note': '', 'cost': 0} for r in (t.rooms if t else [])],
                                    meter_readings=[{'meter': f'{m.get("kind", "")} {m.get("number", "")}'.strip(), 'reading': None} for m in (unit.meters or [])],
                                    keys=[{'item': k.get_kind_display() + (f' {k.code}' if k.code else ''), 'qty': k.qty} for k in unit.key_items.all()],
                                    tenant_name=(lease.party.name if lease else (sale.party.name if sale else '')))
    app.event('inspection', ins.pk, f'{ins.get_kind_display()} inspection planned for {d(ins.scheduled_on)}', user)
    # v1.62.0 the lease activation / move-out already asked Maintenance for this inspection – link it instead of making a second one
    have = None
    if lease is not None and kind == 'move_in' and lease.mnt_inspection_id:
        have = lease.mnt_inspection_id
    elif lease is not None and kind == 'move_out':
        have = lease.moveouts.exclude(status='cancelled').exclude(mnt_inspection_id=None).order_by('-id').values_list('mnt_inspection_id', flat=True).first()
    if have:
        ins.mnt_inspection_id = have
        ins.save(update_fields=['mnt_inspection_id'])
    elif linked(app, 'ZeoMaintenance'):
        r = call(app, 'ZeoMaintenance', 'inspection', kind=kind, unit_id=unit.pk, lease_id=lease.pk if lease else None, date=ins.scheduled_on, ref=f'Inspection {ins.number}',
                 note=f'{ins.get_kind_display()} – {unit.label}')
        if r:
            ins.mnt_inspection_id = r['id']
            ins.save(update_fields=['mnt_inspection_id'])
    return ins


def inspection_defaults(o, request):
    if not o.template_id:
        o.template = template_for(o.unit)
    if not o.items and o.template_id:
        o.items = [{'room': r.get('room', ''), 'item': r.get('item', ''), 'condition': 'ok', 'severity': '', 'note': '', 'cost': 0} for r in o.template.rooms]
    if not o.inspector_id:
        o.inspector = app.my_emp(request)


def inspection_saver(o, request, creating, old):
    o.total_cost = money(sum(Decimal(str(x.get('cost') or 0)) for x in (o.items or [])))
    o.save()
    return o


def compare(o):
    """Findings that got worse since the compared (move-in) inspection."""
    if not o.compare_to_id:
        return []
    rank = {'ok': 0, 'fair': 1, 'poor': 2, 'damaged': 3}
    before = {(x.get('room'), x.get('item')): x for x in (o.compare_to.items or [])}
    out = []
    for x in (o.items or []):
        b = before.get((x.get('room'), x.get('item')))
        if b is not None and rank.get(x.get('condition'), 0) > rank.get(b.get('condition'), 0):
            out.append({'room': x.get('room'), 'item': x.get('item'), 'before': b.get('condition'), 'after': x.get('condition'), 'severity': x.get('severity'), 'cost': x.get('cost') or 0, 'note': x.get('note', '')})
    return out


def a_ins_done(o, request, f):
    inspection_saver(o, request, False, o)
    o.status = 'done'
    o.inspector_signed_on = A.now()
    o.save(update_fields=['status', 'inspector_signed_on'])
    bad = [x for x in (o.items or []) if x.get('condition') in ('poor', 'damaged')]
    app.event('inspection', o.pk, f'Done – {len(bad)} finding(s), cost {cur()} {o.total_cost}', request.user)
    if o.kind == 'move_out' and o.lease_id:
        m = o.lease.moveouts.filter(status__in=('planned', 'inspected')).order_by('-id').first()
        if m:
            worse = compare(o) or [{'item': f'{x.get("room")} – {x.get("item")}', 'cost': x.get('cost') or 0} for x in bad]
            lines = [{'item': f'{x.get("room", "")} – {x.get("item", "")}'.strip(' –'), 'amount': x.get('cost') or 0} for x in worse if x.get('cost')]
            moveout_inspected2(m, o.total_cost, '; '.join(f'{x.get("room")} {x.get("item")}: {x.get("note") or x.get("condition")}' for x in bad)[:2000], request.user, 'Property inspection', lines=lines or None)
    if o.kind == 'unit_ready' and not bad and o.unit.status == 'maintenance':
        set_unit(o.unit, 'available', f'unit-ready inspection {o.number} passed', request.user)
    if bad and linked(app, 'ZeoMaintenance') and f.b('work_orders'):
        for x in bad:
            call(app, 'ZeoMaintenance', 'request', unit_id=o.unit_id, title=f'{x.get("room")} – {x.get("item")}: {x.get("condition")}', description=x.get('note', ''), ref=f'Inspection {o.number}')
    return {'message': f'Inspection done – {len(bad)} finding(s).'}


def a_ins_sign(o, request, f):
    o.tenant_name = f.s('tenant_name') or o.tenant_name
    o.tenant_signed_on, o.status = A.now(), 'signed'
    o.save(update_fields=['tenant_name', 'tenant_signed_on', 'status'])
    app.event('inspection', o.pk, f'Signed by {o.tenant_name} and {emp_name(o.inspector) if o.inspector_id else user_name(request.user)}', request.user)
    return o


R(Reg('inspection', Inspection, 'Inspection', 'Inspections', 'Deals', cols=('number', 'kind', 'unit', 'lease', 'scheduled_on', 'inspector', 'total_cost', 'status'),
      form=('kind', 'unit', 'lease', 'sale', 'template', 'scheduled_on', 'inspector', 'items', 'meter_readings', 'keys', 'compare_to', 'tenant_name', 'note'), number=('inspection', 'INS'),
      manage=LEASING + ('prop_sales',), view=ALL_STAFF, search=('number', 'unit__unit_no', 'lease__number'), filters=('kind', 'status', 'unit', 'inspector'), subtitle='unit',
      defaults=inspection_defaults, saver=inspection_saver, edit_when=lambda o: o.status in ('planned', 'done'), info=('tenant_signed_on', 'inspector_signed_on', 'mnt_inspection_id'), pdf='Condition report',
      tables={'items': [('room', 'Room', 'text'), ('item', 'Item', 'text'), ('condition', 'Condition', 'choice', 'ok=OK|fair=Fair|poor=Poor|damaged=Damaged'),
                        ('severity', 'Severity', 'choice', 'low=Low|medium=Medium|high=High'), ('note', 'Note / photo ref.', 'text'), ('cost', 'Cost', 'number')],
              'meter_readings': [('meter', 'Meter', 'text'), ('reading', 'Reading', 'number')], 'keys': [('item', 'Key / card', 'text'), ('qty', 'Qty', 'number')]},
      extra=lambda o, r: {'comparison': compare(o)},
      actions=[Act('done', 'Inspection done', a_ins_done, when(('planned',), *LEASING + ('prop_sales',)), style='primary', fields=(('work_orders', 'Raise work orders in Maintenance for the findings', 'bool', False),)),
               Act('sign', 'Signed by the tenant / buyer', a_ins_sign, when(('done',), *LEASING + ('prop_sales',)), fields=(('tenant_name', 'Name of the person who signed', 'text', True),)),
               Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(update_fields=['status']), o)[-1], when(('planned',), *LEASING), style='danger')],
      help='Room-wise findings with condition, severity, photo reference and cost; meter readings and keys; signatures; move-out compared with move-in; condition report PDF.'))
R(Reg('inspection_template', InspectionTemplate, 'Inspection template', 'Inspection templates', 'Setup', cols=('code', 'name', 'applies_to', 'review_status'),
      form=('code', 'name', 'applies_to', 'rooms', 'is_active'), manage=MGR, view=ALL_STAFF, search=('code', 'name'), filters=('applies_to', 'review_status'), title='name', review=True,
      deletable=True, files=False, tables={'rooms': [('room', 'Room', 'text'), ('item', 'Item to check', 'text')]}, help='Rooms and items checked at move-in, move-out and routine inspections.'))


def a_key_issue(o, request, f):
    o.held_by, o.party_id, o.issued_on, o.returned_on = f.choice('held_by', KeyItem._meta.get_field('held_by').choices, 'tenant'), f.i('party') or o.party_id, f.dt('on') or today(), None
    o.save()
    app.event('key', o.pk, f'Issued to {o.get_held_by_display()}' + (f' – {o.party.name}' if o.party_id else ''), request.user)
    return o


def a_key_return(o, request, f):
    o.held_by, o.returned_on = 'office', f.dt('on') or today()
    o.save(update_fields=['held_by', 'returned_on'])
    app.event('key', o.pk, 'Returned to the office', request.user)
    return o


R(Reg('key', KeyItem, 'Key / access card', 'Keys and access cards', 'Portfolio', cols=('unit', 'kind', 'code', 'qty', 'held_by', 'party', 'issued_on', 'returned_on'),
      form=('unit', 'kind', 'code', 'qty', 'held_by', 'party', 'note'), manage=LEASING, view=ALL_STAFF, search=('code', 'unit__unit_no'), filters=('kind', 'held_by', 'unit'), title='code',
      subtitle='unit', child_of='unit', deletable=True, files=False,
      actions=[Act('issue', 'Issue', a_key_issue, lambda o, r: app.any(r, *LEASING), style='primary',
                   fields=(('held_by', 'To', 'choice:tenant=Tenant / buyer|owner=Owner|vendor=Vendor', True), ('party', 'Tenant / buyer', 'opt:parties', False), ('on', 'Date', 'date', False))),
               Act('return', 'Returned', a_key_return, lambda o, r: o.held_by != 'office' and app.any(r, *LEASING), fields=(('on', 'Date', 'date', False),)),
               Act('lost', 'Lost', lambda o, r, f: (setattr(o, 'held_by', 'lost'), o.save(update_fields=['held_by']), app.event('key', o.pk, f'Lost – {f.s("note")}', r.user), o)[-1], lambda o, r: app.any(r, *LEASING), style='danger', fields=(('note', 'Note', 'text', False),))],
      help='Who holds which keys, access cards and remotes of every unit.'))


# ------------------------------------------------------------------ 13  vendors and purchase orders
def po_saver(o, request, creating, old):
    o.total = money(sum(Decimal(str(x.get('qty') or 0)) * Decimal(str(x.get('price') or 0)) for x in (o.lines or [])))
    if o.vendor.status == 'blocked':
        raise AppError(f'{o.vendor.name} is blocked.', 'vendor')
    if o.unit_id and not o.building_id:
        o.building = o.unit.building
    o.save()
    return o


def a_po_submit(o, request, f):
    o.status = 'approval'
    o.save(update_fields=['status'])
    approval_todo(f'Purchase order {o.number} to approve', f'{o.vendor.name}: {o.description} – {cur()} {o.total}', 'purchase_order', o.pk, o.number)
    return o


def a_po_receive(o, request, f):
    o.status = 'received'
    o.save(update_fields=['status'])
    app.event('purchase_order', o.pk, 'Goods / work received', request.user)
    if o.charge_to in ('owner', 'tenant') and o.unit_id:
        who = 'tenant' if o.charge_to == 'tenant' else 'owner'
        try:
            A.h_recharge(unit_id=o.unit_id, who=who, amount=o.total, description=o.description, work_order_id=None, number=o.number, user=request.user)
        except AppError as e:
            return {'message': f'Received. Recharge not made: {e.args[0] if e.args else e}'}
    if o.charge_to == 'budget' and o.building_id:
        b = ServiceChargeBudget.objects.filter(building=o.building, year=today().year).first()
        if b:
            lines = list(b.lines or [])
            lines.append({'category': f'PO {o.number} {o.description}'[:80], 'budget': 0, 'actual': float(o.total)})
            b.lines = lines
            budget_saver(b, request, False, b)
    return o


def a_po_invoice(o, request, f):
    o.invoice_no, o.invoice_amount = f.s('invoice_no'), f.n('invoice_amount')
    if o.invoice_amount is None:
        raise AppError('Enter the invoice amount.', 'invoice_amount')
    diff = abs(o.invoice_amount - o.total)
    if diff > max(Decimal('1'), o.total * Decimal('0.01')) and not f.b('force'):
        raise AppError(f'The invoice ({o.invoice_amount}) does not match the order ({o.total}). Tick "Accept the difference" to match anyway.', 'force')
    o.status = 'invoiced'
    o.save()
    app.event('purchase_order', o.pk, f'Invoice {o.invoice_no} matched – {cur()} {o.invoice_amount}' + (' (difference accepted)' if diff else ''), request.user)
    call(app, 'ZeoAccounting', 'property_vendor_bill', o.pk)
    return o


R(Reg('vendor', Vendor, 'Vendor', 'Vendors', 'Portfolio', cols=('number', 'name', 'trade', 'phone', 'licence_expiry', 'insurance_expiry', 'sla_hours', 'rating', 'status'),
      form=('name', 'trade', 'contact', 'email', 'phone', 'licence_no', 'licence_expiry', 'insurance_expiry', 'tax_id', 'bank_details', 'sla_hours', 'coverage', 'rating', 'status', 'notes'),
      number=('vendor', 'VND'), manage=MGR + FIN, view=ALL_STAFF, restricted=('bank_details',), search=('number', 'name', 'trade'), filters=('status', 'trade'), title='name', subtitle='trade',
      children=(('Purchase orders', 'purchase_order', 'vendor'), ('Documents', 'document', 'vendor')),
      actions=[Act('block', 'Block', lambda o, r, f: (setattr(o, 'status', 'blocked'), o.save(update_fields=['status']), app.event('vendor', o.pk, f'Blocked – {f.s("reason")}', r.user), o)[-1], when(('active',), *MGR), style='danger', fields=(('reason', 'Reason', 'text', True),)),
               Act('unblock', 'Unblock', lambda o, r, f: (setattr(o, 'status', 'active'), o.save(update_fields=['status']), o)[-1], when(('blocked', 'inactive'), *MGR))],
      help='Cleaning, security, MEP and other vendors with licence, insurance and tax details, SLA, coverage and rating. A blocked vendor gets no purchase orders.'))
R(Reg('purchase_order', PurchaseOrder, 'Purchase order', 'Purchase orders', 'Finance', cols=('number', 'vendor', 'building', 'unit', 'description', 'total', 'needed_by', 'charge_to', 'status', 'invoice_no'),
      form=('vendor', 'building', 'unit', 'description', 'lines', 'ordered_on', 'needed_by', 'charge_to', 'mnt_work_order_id', 'note'), number=('purchase_order', 'PO'), manage=MGR + FIN + ('prop_leasing',),
      view=ALL_STAFF, search=('number', 'description', 'vendor__name'), filters=('status', 'vendor', 'building', 'charge_to'), subtitle='vendor', saver=po_saver, edit_when=lambda o: o.status in ('draft', 'approval'),
      tables={'lines': [('item', 'Item', 'text'), ('qty', 'Qty', 'number'), ('price', 'Unit price', 'number')]}, info=('invoice_no', 'invoice_amount', 'external_ref'), links={'mnt_work_order_id': 'maintenance.work_orders'}, pdf='Purchase order',
      actions=[Act('submit', 'Submit for approval', a_po_submit, when(('draft',), *MGR + FIN + ('prop_leasing',)), style='primary'),
               Act('approve', 'Approve', lambda o, r, f: (setattr(o, 'status', 'approved'), setattr(o, 'ordered_on', o.ordered_on or today()), o.save(), app.close_todos('purchase_order', o.pk), app.event('purchase_order', o.pk, 'Approved', r.user), o)[-1],
                   when(('approval', 'draft'), *MGR), style='primary'),
               Act('receive', 'Received', a_po_receive, when(('approved',), *MGR + FIN + ('prop_leasing',)), style='primary'),
               Act('invoice', 'Match the vendor invoice', a_po_invoice, when(('received',), *FIN), fields=(('invoice_no', 'Vendor invoice no.', 'text', True), ('invoice_amount', 'Invoice amount', 'decimal', True), ('force', 'Accept the difference', 'bool', False))),
               Act('paid', 'Paid', lambda o, r, f: (setattr(o, 'status', 'paid'), setattr(o, 'external_ref', f.s('external_ref')), o.save(), app.event('purchase_order', o.pk, f'Paid – {o.external_ref}', r.user), o)[-1],
                   when(('invoiced',), *FIN), fields=(('external_ref', 'Payment / bill ref.', 'text', True),)),
               Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(update_fields=['status']), o)[-1], when(('draft', 'approval', 'approved'), *MGR), style='danger')],
      help='Orders to vendors: approval, receipt, invoice matching (within 1%), payment; the cost can be recharged to the owner or tenant or added to the service charge budget.'))


# ------------------------------------------------------------------ 20–22  announcements and portal requests
R(Reg('announcement', Announcement, 'Announcement', 'Announcements', 'Customers', cols=('title', 'audience', 'building', 'show_from', 'show_to', 'status'),
      form=('title', 'body', 'audience', 'building', 'show_from', 'show_to', 'send_message'), manage=MGR + LEASING, view=ALL_STAFF, search=('title',), filters=('status', 'audience', 'building'),
      title='title', subtitle='audience', deletable=True, defaults=lambda o, r: setattr(o, 'show_from', o.show_from or today()),
      actions=[Act('publish', 'Publish', lambda o, r, f: publish_announcement(o, r), when(('draft', 'archived'), *MGR + LEASING), style='primary'),
               Act('archive', 'Archive', lambda o, r, f: (setattr(o, 'status', 'archived'), o.save(update_fields=['status']), o)[-1], when(('published',), *MGR + LEASING))],
      help='Notices shown in the tenant, owner and buyer portals (maintenance shutdowns, community news); optionally sent as a message.'))


def publish_announcement(o, request):
    o.status = 'published'
    o.save(update_fields=['status'])
    app.event('announcement', o.pk, 'Published', request.user)
    n = 0
    if o.send_message:
        people = []
        if o.audience in ('all', 'tenants'):
            q = Lease.objects.filter(status__in=('active', 'notice'))
            if o.building_id:
                q = q.filter(unit__building=o.building)
            people += [l.party for l in q.select_related('party')]
        if o.audience in ('all', 'buyers'):
            q = SaleContract.objects.filter(status__in=('signed', 'completed'))
            if o.building_id:
                q = q.filter(unit__building=o.building)
            people += [x.party for x in q.select_related('party')]
        if o.audience in ('all', 'owners'):
            q = Owner.objects.filter(is_active=True)
            if o.building_id:
                q = q.filter(units__building=o.building)
            people += list(q.distinct())
        for p in {x.pk if isinstance(x, Party) else -x.pk: x for x in people}.values():
            tell_party(p, 'announcement', o.title, f'Dear {p.name},\n\n{o.body}\n', {'number': o.title}, 'announcement', o.pk, o.title, PAGE + f'r/announcement/{o.pk}')
            n += 1
    return {'message': f'Published.' + (f' Sent to {n} people.' if n else '')}


def a_req_decide(ok):
    def run(o, request, f):
        o.status, o.answer = ('approved' if ok else 'rejected'), f.s('answer')[:300]
        o.save(update_fields=['status', 'answer'])
        if ok and o.kind == 'profile' and o.party_id and isinstance(o.data, dict):
            for k, v in o.data.items():
                if k in ('name', 'email', 'phone', 'address', 'emergency_contact', 'nationality', 'company_name') and v:
                    setattr(o.party, k, str(v)[:150])
            o.party.save()
            app.event('party', o.party_id, f'Profile updated from portal request {o.number}: ' + ', '.join(o.data.keys()), request.user)
        if ok and o.kind == 'move_out' and o.lease_id and o.lease.status == 'active':
            f.d['move_out_on'] = (o.wanted_on or (today() + timedelta(days=30))).isoformat()
            for a in regs['lease'].actions:
                if a.name == 'notice':
                    a.run(o.lease, request, f)
        app.event('portal_request', o.pk, ('Approved' if ok else 'Rejected') + (f' – {o.answer}' if o.answer else ''), request.user)
        person = o.party or o.owner
        if person:
            tell_party(person, 'announcement', f'Your request {o.number} – {o.get_status_display().lower()}', f'Dear {person.name},\n\nYour {o.get_kind_display().lower()} request was {o.get_status_display().lower()}.' + (f'\n{o.answer}\n' if o.answer else '\n'),
                       {'number': o.number}, 'portal_request', o.pk, o.number, PAGE + f'r/portal_request/{o.pk}')
        return o
    return run


R(Reg('portal_request', PortalRequest, 'Portal request', 'Portal requests', 'Customers', cols=('number', 'kind', 'party', 'owner', 'unit', 'wanted_on', 'status', 'created_at'),
      form=('kind', 'party', 'owner', 'lease', 'unit', 'wanted_on', 'text'), number=('portal_request', 'REQ'), manage=LEASING, view=ALL_STAFF, search=('number', 'party__name', 'text'),
      filters=('status', 'kind'), subtitle='party', edit_when=lambda o: o.status == 'open', info=('data', 'answer'),
      actions=[Act('approve', 'Approve', a_req_decide(True), when(('open',), *LEASING), style='primary', fields=(('answer', 'Answer to the person', 'text', False),)),
               Act('reject', 'Reject', a_req_decide(False), when(('open',), *LEASING), style='danger', fields=(('answer', 'Reason', 'text', True),)),
               Act('done', 'Done', lambda o, r, f: (setattr(o, 'status', 'done'), o.save(update_fields=['status']), o)[-1], when(('approved',), *LEASING))],
      help='Move-in / move-out dates, profile changes, NOC letters and parking requests sent from the portals; approving a move-out gives notice on the lease, approving a profile change updates the record.'))


# ------------------------------------------------------------------ 14  owner statements: co-owners, contributions, leasing fees, per property, approval, payout
regs['statement'].form = ('owner', 'building', 'period_from', 'period_to', 'external_ref')
regs['statement'].cols = ('number', 'owner', 'building', 'period_from', 'period_to', 'collected', 'contributions', 'expenses', 'fees', 'other_fees', 'net', 'status')
regs['statement'].pdf = 'Owner statement'
regs['statement'].edit_when = lambda o: o.status in ('draft', 'approval')


def build_statement2(o):
    from .models import Allocation
    own = Unit.objects.filter(owner=o.owner)
    if o.building_id:
        own = own.filter(building=o.building)
    shares = {u.pk: Decimal(1) for u in own}
    for u in Unit.objects.exclude(owner=o.owner).exclude(co_owners=[]):
        for x in (u.co_owners or []):
            if str(x.get('owner')) == str(o.owner_id) and x.get('percent') and (not o.building_id or u.building_id == o.building_id):
                shares[u.pk] = Decimal(str(x['percent'])) / 100
    for u in own:   # the main owner's share when there are co-owners
        others = sum(Decimal(str(x.get('percent') or 0)) for x in (u.co_owners or []) if str(x.get('owner')) != str(o.owner_id))
        if others:
            shares[u.pk] = max(ZERO, (100 - others) / 100)
    ag = o.owner.agreements.filter(status='active').order_by('-start').first()
    fee_pct = ag.fee_percent if ag else ZERO
    lines, collected = [], ZERO
    allocs = Allocation.objects.filter(receipt__status='posted', receipt__received_on__range=(o.period_from, o.period_to), bill__unit_id__in=list(shares), bill__charge__kind__in=('rent', 'service', 'utility', 'fee'),
                                       bill__is_credit=False).exclude(receipt__method='credit').select_related('bill__unit__building', 'bill__charge', 'receipt')
    for a in allocs:
        amt = money(a.amount * shares[a.bill.unit_id])
        collected += amt
        lines.append({'date': d(a.receipt.received_on), 'unit': a.bill.unit.label, 'text': f'{a.bill.description or a.bill.charge.name} ({a.receipt.number})' + (f' · share {shares[a.bill.unit_id] * 100:.0f}%' if shares[a.bill.unit_id] != 1 else ''), 'amount': float(amt)})
    fees = money(collected * fee_pct / 100)
    if fees:
        lines.append({'date': d(o.period_to), 'unit': '', 'text': f'Management fee {fee_pct}%', 'amount': -float(fees)})
    exp, other = ZERO, ZERO
    bills = Bill.objects.filter(owner=o.owner, due_date__range=(o.period_from, o.period_to), is_credit=False).exclude(status='void').select_related('charge', 'unit')
    if o.building_id:
        bills = bills.filter(Q(unit__building=o.building) | Q(unit__isnull=True))
    for b in bills:
        if b.charge.kind == 'fee':
            other += b.total
        else:
            exp += b.total
        lines.append({'date': d(b.due_date), 'unit': b.unit.label if b.unit_id else '', 'text': f'{b.description or b.charge.name} ({b.number})', 'amount': -float(b.total)})
    contrib = ZERO
    for r in Receipt.objects.filter(owner=o.owner, status='posted', received_on__range=(o.period_from, o.period_to)).exclude(method='credit'):
        contrib += r.amount
        lines.append({'date': d(r.received_on), 'unit': '', 'text': f'Owner contribution ({r.number}) {r.reference}', 'amount': float(r.amount)})
    o.collected, o.fees, o.expenses, o.other_fees, o.contributions = money(collected), fees, money(exp), money(other), money(contrib)
    o.net = o.collected + o.contributions - o.fees - o.expenses - o.other_fees
    o.lines = sorted(lines, key=lambda x: x['date'] or '')


A.build_statement = build_statement2
_st_issue = A.a_statement_issue


def a_statement_issue2(o, request, f):
    s = app.settings()
    if s.statement_approval and o.status != 'approved':
        if o.status == 'draft':
            build_statement2(o)
            o.status = 'approval'
            o.save()
            approval_todo(f'Owner statement {o.number} to approve', f'{o.owner.name}: net {cur()} {o.net}', 'statement', o.pk, o.number)
            return {'message': 'Sent for approval.'}
        raise AppError('The statement needs approval first.')
    r = _st_issue(o, request, f)
    tell_party(o.owner, 'statement', f'Owner statement {o.number}', f'Dear {o.owner.name},\n\nYour statement for {d(o.period_from)} – {d(o.period_to)} is ready in the owner portal: '
               f'collected {cur()} {o.collected}, expenses {o.expenses}, fees {o.fees + o.other_fees}, net {o.net}.\n', {'amount': str(o.net), 'number': o.number}, 'statement', o.pk, o.number, PAGE + f'r/statement/{o.pk}')
    return r


for a in regs['statement'].actions:
    if a.name == 'issue':
        a.run, a.allowed, a.label = a_statement_issue2, when(('draft', 'approved'), *FIN), 'Issue to owner'
    if a.name == 'paid':
        _paid = a.run

        def a_paid2(o, request, f, _r=_paid):
            r = _r(o, request, f)
            o.paid_on = today()
            o.save(update_fields=['paid_on'])
            call(app, 'ZeoAccounting', 'property_payout', o.pk)
            return r
        a.run = a_paid2
regs['statement'].actions += [
    Act('approve', 'Approve', lambda o, r, f: (setattr(o, 'status', 'approved'), o.save(update_fields=['status']), app.close_todos('statement', o.pk), app.event('statement', o.pk, 'Approved', r.user), o)[-1],
        when(('approval',), *MGR), style='primary'),
    Act('back', 'Back to draft', lambda o, r, f: (setattr(o, 'status', 'draft'), o.save(update_fields=['status']), o)[-1], when(('approval', 'approved'), *MGR + FIN)),
]


# ------------------------------------------------------------------ dashboard, my work, daily job
_dashboard, _my_work, _daily = A.dashboard, A.my_work, A.daily


def dashboard2(request):
    out = _dashboard(request)
    t = today()
    show_money = app.any(request, *FIN + MGR + VIEW)
    esc = in_branches(Bill.objects.all(), request).filter(status__in=('issued', 'part_paid'), escalated_on__isnull=False)
    docs = in_branches(Document.objects.all(), request).filter(status__in=('expiring', 'expired'))
    out['cards'] += [{'label': 'Listings published', 'value': in_branches(Listing.objects.all(), request).filter(status='published').count(), 'link': 'r/listing?status=published'},
                     {'label': 'Documents expiring / expired', 'value': docs.count(), 'tone': 'warn' if docs.exists() else 'ok', 'link': 'r/document?status=expiring'},
                     {'label': 'Waiting list', 'value': in_branches(WaitingList.objects.all(), request).filter(status__in=('waiting', 'offered')).count(), 'link': 'r/waiting?status=waiting'}]
    if show_money:
        out['cards'] += [{'label': 'Escalated collections', 'value': esc.count(), 'tone': 'bad' if esc.exists() else 'ok', 'link': 'r/bill?status=issued'},
                         {'label': 'Refunds to pay', 'value': in_branches(Refund.objects.all(), request).filter(status='approved').count(), 'link': 'r/refund?status=approved'},
                         {'label': 'Unusual meter readings', 'value': in_branches(MeterReading.objects.all(), request).filter(unusual=True, status='read').count(), 'link': 'r/reading?unusual=true'}]
    waiting = []
    for kind, qs, lbl in (('reservation', Reservation.objects.filter(status='pending'), 'Reservation'), ('lease', Lease.objects.filter(status='draft', approval='requested'), 'Lease'),
                          ('sale', SaleContract.objects.filter(status='approval'), 'Sale'), ('moveout', MoveOut.objects.filter(status='approval'), 'Move-out deductions'),
                          ('refund', Refund.objects.filter(status='requested'), 'Refund'), ('statement', OwnerStatement.objects.filter(status='approval'), 'Owner statement'),
                          ('purchase_order', PurchaseOrder.objects.filter(status='approval'), 'Purchase order'), ('listing', Listing.objects.filter(status='approval'), 'Listing')):
        for x in in_branches(qs, request)[:5]:
            waiting.append({'id': x.pk, 'kind': kind, 'title': f'{lbl} {x.number}', 'sub': getattr(getattr(x, 'party', None), 'name', '') or getattr(getattr(x, 'owner', None), 'name', '') or '', 'badge': 'approval'})
    if waiting and app.any(request, *MGR):
        out['lists'].insert(0, {'title': 'Waiting for approval', 'kind': 'mixed', 'rows': waiting[:12]})
    return out


def my_work2(request):
    out = _my_work(request)
    if app.any(request, *FIN):
        out.append({'title': 'Refunds to pay', 'kind': 'refund', 'rows': [{'id': x.pk, 'title': x.number, 'sub': f'{x.party.name} · {cur()} {x.amount}', 'badge': x.get_kind_display()}
                                                                        for x in in_branches(Refund.objects.all(), request).filter(status='approved').select_related('party')[:20]]})
        out.append({'title': 'Meter readings to bill', 'kind': 'reading', 'rows': [{'id': x.pk, 'title': f'{x.meter} {d(x.read_on)}', 'sub': f'{x.consumption} {x.meter.unit_name} · {cur()} {x.amount}', 'badge': 'unusual' if x.unusual else ''}
                                                                                for x in in_branches(MeterReading.objects.all(), request).filter(status='read').select_related('meter')[:20]]})
    if app.any(request, *LEASING):
        out.append({'title': 'Portal requests', 'kind': 'portal_request', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.get_kind_display()}', 'sub': (x.party.name if x.party_id else (x.owner.name if x.owner_id else '')), 'badge': d(x.wanted_on) if x.wanted_on else 'open'}
                                                                                for x in in_branches(PortalRequest.objects.all(), request).filter(status='open').select_related('party', 'owner')[:20]]})
        out.append({'title': 'Inspections planned', 'kind': 'inspection', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.get_kind_display()} {x.unit.label}', 'sub': x.tenant_name, 'badge': d(x.scheduled_on)}
                                                                                 for x in in_branches(Inspection.objects.all(), request).filter(status='planned').select_related('unit__building').order_by('scheduled_on')[:20]]})
    return out


def collections(t, s):
    """Payment reminders before and after the due date, escalation to the manager, late fees."""
    out = {'reminded': 0, 'escalated': 0, 'late_fees': 0}
    before = sorted({int(x) for x in (s.reminder_days_before or [])}, reverse=True)
    after = sorted({int(x) for x in (s.reminder_days_after or [])})
    qs = Bill.objects.filter(status__in=('issued', 'part_paid'), party__isnull=False, disputed=False, is_credit=False).select_related('party', 'unit__building', 'lease', 'settlement')
    for b in qs:
        due = b.due_date
        if b.settlement_id and b.settlement.status == 'active':   # the plan's dates replace the original due date
            nxt = [x for x in (b.settlement.instalments or []) if x.get('due') and date.fromisoformat(str(x['due'])[:10]) >= t]
            due = date.fromisoformat(str(nxt[0]['due'])[:10]) if nxt else due
        if b.promise_date and b.promise_date >= t:
            continue
        days = (t - due).days
        sent = list(b.reminders or [])
        key = None
        if days < 0 and before:
            for x in before:
                if -days <= x and f'b{x}' not in sent:
                    key = f'b{x}'
                    break
        elif days > 0 and after:
            for x in after:
                if days >= x and f'a{x}' not in sent:
                    key = f'a{x}'
        if key:
            sent.append(key)
            b.reminders = sent
            b.save(update_fields=['reminders'])
            ev = 'reminder_before' if key.startswith('b') else 'reminder_after'
            tell_party(b.party, ev, ('Payment due soon' if ev == 'reminder_before' else 'Payment overdue') + f' – {b.number}',
                       f'Dear {b.party.name},\n\n{b.description or b.charge.name}: {cur()} {b.balance} ' + ('is due on ' if days < 0 else 'was due on ') + f'{d(due)}.' +
                       ('\nPlease pay in the tenant portal or contact us.\n' if days < 0 else '\nPlease settle it now to avoid a late fee and further action.\n'),
                       {'amount': str(b.balance), 'due': d(due), 'number': b.number, 'unit': b.unit.label if b.unit_id else ''}, 'bill', b.pk, b.number, PAGE + f'r/bill/{b.pk}')
            out['reminded'] += 1
        if days >= (s.escalate_after_days or 30) and not b.escalated_on:
            b.escalated_on = t
            b.save(update_fields=['escalated_on'])
            app.tell(managers(), f'Overdue {days} days: {b.number} {b.party.name}', f'{b.description or b.charge.name} – {cur()} {b.balance}' + (f' ({b.unit.label})' if b.unit_id else ''),
                     'bill', b.pk, b.number, PAGE + f'r/bill/{b.pk}', summary=f'Collection: {b.party.name} {cur()} {b.balance}')
            out['escalated'] += 1
        if s.late_fee_percent and days >= (s.late_fee_grace_days or 15) and not b.late_fee_billed and b.charge.kind == 'rent' and (b.lease_id is None or b.lease.late_fee) and not b.settlement_id:
            fee = money(b.balance * s.late_fee_percent / 100)
            if fee > 0:
                lf = make_bill(party=b.party, unit=b.unit, lease=b.lease, ch=charge('fee', 'LATE_FEE'), amount=fee, due=t + timedelta(days=7), desc=f'Late payment fee – {b.number} ({days} days)', source='late_fee', vat_percent=ZERO)
                b.late_fee_billed = True
                b.save(update_fields=['late_fee_billed'])
                tell_party(b.party, 'late_fee', f'Late fee – {b.number}', f'Dear {b.party.name},\n\nA late payment fee of {cur()} {fee} ({s.late_fee_percent}%) was charged on {b.number}, overdue since {d(due)}.\n',
                           {'amount': str(fee), 'number': lf.number}, 'bill', lf.pk, lf.number, PAGE + f'r/bill/{lf.pk}')
                out['late_fees'] += 1
    return out


def daily2():
    out = _daily()
    t = today()
    s = app.settings()
    out.update(collections(t, s))
    out['recurring_bills'] = run_recurring(t)
    # documents
    days = s.document_alert_days or 30
    n = 0
    for doc in Document.objects.filter(expires_on__isnull=False).exclude(status='replaced').select_related('party', 'owner', 'vendor', 'unit__building'):
        new = 'expired' if doc.expires_on < t else ('expiring' if doc.expires_on <= t + timedelta(days=days) else 'valid')
        if new != doc.status or (new != 'valid' and not doc.alerted):
            doc.status = new
            if new != 'valid' and not doc.alerted:
                doc.alerted = True
                who = doc.party or doc.owner
                what = f'{doc.get_category_display()} "{doc.name}"' + (f' of {who.name}' if who else (f' of {doc.vendor.name}' if doc.vendor_id else (f' – {doc.unit.label}' if doc.unit_id else '')))
                app.tell(app.users_with_role('prop_leasing') + managers(), f'{what} {"expired" if new == "expired" else "expires " + d(doc.expires_on)}', doc.note or '', 'document', doc.pk, doc.number,
                         PAGE + f'r/document/{doc.pk}', summary=f'Renew {what}')
                if who is not None:
                    tell_party(who, 'document_expiry', f'Your {doc.get_category_display().lower()} {"has expired" if new == "expired" else "expires soon"}',
                               f'Dear {who.name},\n\nYour {doc.get_category_display().lower()} "{doc.name}" {"expired on" if new == "expired" else "expires on"} {d(doc.expires_on)}. Please send us the renewed copy.\n',
                               {'number': doc.name, 'due': d(doc.expires_on)}, 'document', doc.pk, doc.number, PAGE + f'r/document/{doc.pk}')
                n += 1
            doc.save(update_fields=['status', 'alerted'])
    for b in Building.objects.filter(Q(insurance_expiry__lte=t + timedelta(days=days)) | Q(permit_expiry__lte=t + timedelta(days=days))):
        for fld, lbl in (('insurance_expiry', 'Building insurance'), ('permit_expiry', 'Permit / civil defence certificate')):
            dt = getattr(b, fld)
            if dt and dt <= t + timedelta(days=days):
                for u in managers():
                    app.todo(u, f'{lbl} of {b.name} {"expired" if dt < t else "expires " + d(dt)}', 'building', b.pk, b.name, PAGE + f'r/building/{b.pk}')
    for v in Vendor.objects.filter(status='active').filter(Q(licence_expiry__lte=t + timedelta(days=days)) | Q(insurance_expiry__lte=t + timedelta(days=days))):
        for u in managers():
            app.todo(u, f'Vendor {v.name}: licence / insurance expiring', 'vendor', v.pk, v.name, PAGE + f'r/vendor/{v.pk}')
    out['document_alerts'] = n
    # listings: expire the window, withdraw let / sold units
    for l in Listing.objects.filter(status='published').select_related('unit'):
        if (l.publish_to and l.publish_to < t) or l.unit.status in ('let', 'sold', 'sold_pending', 'reserved'):
            l.status = 'withdrawn'
            l.save(update_fields=['status'])
            app.event('listing', l.pk, 'Withdrawn automatically – ' + ('publication window ended' if l.publish_to and l.publish_to < t else f'unit is {l.unit.get_status_display().lower()}'), None)
    # rent reviews, break options, fit-out
    for l in Lease.objects.filter(status='active', review_date__lte=t + timedelta(days=60), review_kind__gt='').select_related('unit__building', 'party'):
        for u in app.users_with_role('prop_leasing'):
            app.todo(u, f'Rent review {d(l.review_date)}: {l.number} {l.unit.label}', 'lease', l.pk, l.number, PAGE + f'r/lease/{l.pk}')
    for l in Lease.objects.filter(status='active', break_date__isnull=False).select_related('unit__building', 'party'):
        notice_by = l.break_date - timedelta(days=l.break_notice_days or 0)
        if t <= notice_by <= t + timedelta(days=14):
            for u in app.users_with_role('prop_leasing'):
                app.todo(u, f'Break option {d(l.break_date)} on {l.number} – notice by {d(notice_by)}', 'lease', l.pk, l.number, PAGE + f'r/lease/{l.pk}')
    # settlement plans broken: an instalment date passed and the party still owes more than the remaining instalments
    for p in SettlementPlan.objects.filter(status='active').select_related('party'):
        past = sum(Decimal(str(x.get('amount') or 0)) for x in (p.instalments or []) if x.get('due') and date.fromisoformat(str(x['due'])[:10]) < t - timedelta(days=5))
        owed = sum((b.balance for b in Bill.objects.filter(settlement=p, status__in=('issued', 'part_paid'))), ZERO)
        if past and owed > p.total - past + 1:
            p.status = 'broken'
            p.save(update_fields=['status'])
            app.tell(app.users_with_role('prop_finance'), f'Settlement plan {p.number} broken', f'{p.party.name} still owes {cur()} {owed}', 'settlement', p.pk, p.number, PAGE + f'r/settlement/{p.pk}')
    # inspections due
    for i in Inspection.objects.filter(status='planned', scheduled_on=t).select_related('unit__building'):
        for u in ([i.inspector.users] if i.inspector_id and i.inspector.users_id else app.users_with_role('prop_leasing')):
            app.todo(u, f'Inspection today: {i.get_kind_display()} {i.unit.label}', 'inspection', i.pk, i.number, PAGE + f'r/inspection/{i.pk}')
    return out


app.dashboard, app.my_work, app.daily = dashboard2, my_work2, daily2


# ------------------------------------------------------------------ hooks for the other apps (accounting, CRM, maintenance)
def h_vendor_info(vendor_id):
    v = Vendor.objects.filter(pk=vendor_id).first()
    return v and {'id': v.pk, 'name': v.name, 'trade': v.trade, 'email': v.email, 'phone': v.phone, 'blocked': v.status == 'blocked', 'sla_hours': v.sla_hours}


def h_accounting_items(kind, since=None):
    """Lists for the Accounting connector: billing records / credit notes to invoice, receipts, refunds, owner payouts, commissions, vendor bills."""
    t = since or (today() - timedelta(days=90))
    if kind == 'bills':
        return [{'id': b.pk, 'number': b.number, 'date': b.due_date, 'party': b.party.name if b.party_id else (b.owner.name if b.owner_id else ''), 'party_kind': 'party' if b.party_id else 'owner',
                 'amount': float(b.amount), 'vat': float(b.vat), 'total': float(b.total), 'credit': b.is_credit, 'charge': b.charge.name, 'charge_kind': b.charge.kind, 'unit': b.unit.label if b.unit_id else '',
                 'building': b.unit.building.name if b.unit_id else '', 'branch': b.branch_id, 'external_ref': b.external_ref, 'status': b.external_status, 'description': b.description,
                 'partner_id': (b.party.partner_id if b.party_id else (b.owner.partner_id if b.owner_id else None))}
                for b in Bill.objects.filter(status__in=('issued', 'part_paid', 'paid'), created_at__date__gte=t).select_related('party', 'owner', 'charge', 'unit__building')]
    if kind == 'receipts':
        return [{'id': r.pk, 'number': r.number, 'date': r.received_on, 'party': r.party.name if r.party_id else (r.owner.name if r.owner_id else ''), 'amount': float(r.amount), 'method': r.method,
                 'reference': r.reference, 'external_ref': r.external_ref, 'partner_id': (r.party.partner_id if r.party_id else (r.owner.partner_id if r.owner_id else None)), 'bills': [{'bill': a.bill.number, 'amount': float(a.amount), 'deposit': a.bill.charge.kind == 'deposit'} for a in r.allocations.select_related('bill__charge')]}
                for r in Receipt.objects.filter(status='posted', received_on__gte=t).exclude(method='credit').select_related('party', 'owner')]
    if kind == 'refunds':
        return [{'id': r.pk, 'number': r.number, 'date': r.paid_on, 'party': r.party.name, 'amount': float(r.amount), 'kind': r.kind, 'reference': r.reference, 'external_ref': r.external_ref, 'partner_id': r.party.partner_id}
                for r in Refund.objects.filter(status='paid', paid_on__gte=t).select_related('party')]
    if kind == 'payouts':
        return [{'id': s.pk, 'number': s.number, 'date': s.paid_on or s.period_to, 'owner': s.owner.name, 'net': float(s.net), 'collected': float(s.collected), 'fees': float(s.fees + s.other_fees),
                 'expenses': float(s.expenses), 'external_ref': s.external_ref, 'partner_id': s.owner.partner_id} for s in OwnerStatement.objects.filter(status__in=('issued', 'paid'), period_to__gte=t).select_related('owner')]
    if kind == 'commissions':
        return [{'id': c.pk, 'number': c.number, 'date': c.created_at.date() if c.created_at else today(), 'agent': emp_name(c.agent) if c.agent_id else (c.broker.name if c.broker_id else ''),
                 'employee_id': c.agent_id, 'broker': c.broker_id is not None, 'amount': float(c.amount), 'external_ref': c.external_ref,
                 'partner_id': c.broker.partner_id if c.broker_id else None}
                for c in Commission.objects.filter(status__in=('approved', 'paid')).select_related('agent', 'broker')]
    if kind == 'vendor_bills':
        return [{'id': p.pk, 'number': p.number, 'date': p.ordered_on, 'vendor': p.vendor.name, 'invoice_no': p.invoice_no, 'amount': float(p.invoice_amount or p.total), 'external_ref': p.external_ref,
                 'description': p.description, 'partner_id': p.vendor.partner_id} for p in PurchaseOrder.objects.filter(status__in=('invoiced', 'paid')).select_related('vendor')]
    return []


def h_accounting_posted(kind, pk, ref, status='posted', paid=False):
    """The connector tells Property the document number in accounting (and when the invoice is paid there)."""
    if kind == 'bills':
        b = Bill.objects.filter(pk=pk).first()
        if b:
            b.external_ref, b.external_status = ref or b.external_ref, status
            b.save(update_fields=['external_ref', 'external_status'])
            if paid and b.status in ('issued', 'part_paid') and b.party_id:
                r = Receipt.objects.create(number=app.next_number('receipt', 'RCT'), party=b.party, owner=b.owner, received_on=today(), amount=b.balance, method='transfer', reference=f'Paid in accounting {ref}')
                post_receipt2(r, None, [b.pk])
    elif kind == 'receipts':
        Receipt.objects.filter(pk=pk).update(external_ref=ref)
    elif kind == 'refunds':
        Refund.objects.filter(pk=pk).update(external_ref=ref)
    elif kind == 'payouts':
        OwnerStatement.objects.filter(pk=pk).update(external_ref=ref)
    elif kind == 'commissions':
        Commission.objects.filter(pk=pk).update(external_ref=ref)
    elif kind == 'vendor_bills':
        PurchaseOrder.objects.filter(pk=pk).update(external_ref=ref)
    return True


def h_listings_public():
    return [{'id': l.pk, 'title': l.title, 'kind': l.kind, 'price': float(l.price), 'unit': l.unit.label, 'building': l.unit.building.name, 'city': l.unit.building.city, 'bedrooms': l.unit.bedrooms,
             'area': float(l.unit.area or 0)} for l in Listing.objects.filter(status='published').select_related('unit__building')]


app.hooks.update({'vendor_info': h_vendor_info, 'accounting_items': h_accounting_items, 'accounting_posted': h_accounting_posted, 'listings_public': h_listings_public})


def _set_secret(st, value):
    from .messaging import set_secret
    set_secret(st, value)


app.set_secret = _set_secret
