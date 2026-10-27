"""ZEO Property on the app kit: portfolio, reservations, leases, sales, billing records, receipts, cheques, commissions,
move-outs, owner statements; links to CRM and Maintenance; tenant / owner / buyer portals (portal.py)."""
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, F, Q, Sum

from zeo.appkit.engine import (in_branches, dash_period, Act, App, AppError, Reg, add_months, call, d, emp_name, linked, link_label, now, today, user_name)

from .models import (Agreement, Allocation, Bill, Building, ChargeType, ChecklistItem, Cheque, Commission, Lease, MoveOut, Owner,
                     OwnerStatement, Party, PropertyEvent, PropertyFile, PropertySequence, PropertySettings, ROLES, Receipt,
                     Reservation, SaleContract, Unit, UnitType)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-property/'
ZERO = Decimal('0')
C2 = Decimal('0.01')

app = App(
    label='ZeoProperty', slug='zeo-property', title='Property', settings_model=PropertySettings, sequence_model=PropertySequence,
    event_model=PropertyEvent, file_model=PropertyFile, roles=ROLES, role_prefix='prop_', manager_role='prop_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'PM': 'property_management'},
    pack_models={'unit_type': (UnitType, 'code'), 'charge': (ChargeType, 'code'), 'checklist': (ChecklistItem, 'code')},
    links=(('ZeoCRM', 'CRM', 'Won deals become reservations here; tenants and buyers are kept as one CRM contact; renewal '
            'opportunities open before leases end; listings follow the unit status.'),
           ('ZeoMaintenance', 'Maintenance', 'Move-in, move-out, unit-ready and snag inspections; tenants and buyers raise requests in the '
            'portal; the owner approves costly jobs; maintenance costs come back as billing records.')),
    page=PAGE,
)
app.settings_fields = ('currency', 'reservation_days', 'renewal_alert_days', 'deposit_percent', 'commercial_vat_percent', 'commission_percent',
                       'maintenance_approval_limit', 'portal_url', 'portal_tenant', 'portal_owner', 'portal_buyer')

MGR = ('prop_manager',)
LEASING = ('prop_manager', 'prop_leasing', 'prop_contracts')
SALES = ('prop_manager', 'prop_sales', 'prop_contracts')
DEALS = ('prop_manager', 'prop_leasing', 'prop_sales', 'prop_contracts')
FIN = ('prop_manager', 'prop_finance')
VIEW = ('prop_viewer',)
ALL_STAFF = tuple(k for k, _ in ROLES)


def money(v):
    return Decimal(v or 0).quantize(C2, ROUND_HALF_UP)


def cur():
    return app.settings().currency


def can(*roles):
    return lambda o, r: app.any(r, *roles)


def when(states, *roles):
    return lambda o, r: o.status in states and app.any(r, *roles)


# ------------------------------------------------------------------ options for this app and the others
def unit_label(u):
    return f'{u.building.name} {u.unit_no}' + (f' · {u.get_status_display()}' if u.status != 'available' else '')


app.option('units', lambda: Unit.objects.select_related('building'), unit_label, ('unit_no', 'building__name', 'number'), Unit)
app.option('available-units', lambda: Unit.objects.filter(status__in=('available', 'notice')).select_related('building'), unit_label,
           ('unit_no', 'building__name', 'number'))
app.option('buildings', lambda: Building.objects.filter(is_active=True), lambda x: x.name, ('name', 'number'), Building)
app.option('owners', lambda: Owner.objects.filter(is_active=True), lambda x: x.name, ('name', 'email', 'number'), Owner)
app.option('parties', lambda: Party.objects.filter(is_active=True), lambda x: f'{x.name} ({x.number})', ('name', 'email', 'phone', 'number'), Party)
app.option('leases', lambda: Lease.objects.select_related('unit__building', 'party'), lambda x: f'{x.number} {x.unit.label} – {x.party.name}',
           ('number', 'party__name', 'unit__unit_no'), Lease)
app.option('reservations', lambda: Reservation.objects.select_related('unit__building', 'party'), lambda x: f'{x.number} {x.unit.label} – {x.party.name}',
           ('number', 'party__name'), Reservation)
app.option('sales', lambda: SaleContract.objects.select_related('unit__building', 'party'), lambda x: f'{x.number} {x.unit.label} – {x.party.name}',
           ('number', 'party__name'), SaleContract)
app.option('bills', lambda: Bill.objects.exclude(status__in=('paid', 'void')), lambda x: f'{x.number} {x.description or x.charge.name} {x.total}', ('number', 'description'), Bill)
app.option('cheques', lambda: Cheque.objects.all(), lambda x: f'{x.number} #{x.cheque_no} {x.amount}', ('number', 'cheque_no'), Cheque)
app.option('unit-types', lambda: UnitType.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), UnitType)
app.option('charges', lambda: ChargeType.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), ChargeType)
app.option('agreements', lambda: Agreement.objects.select_related('owner'), lambda x: f'{x.number} {x.owner.name}', ('number', 'owner__name'), Agreement)
app.option('moveouts', lambda: MoveOut.objects.all(), lambda x: x.number, ('number',), MoveOut)
app.option('statements', lambda: OwnerStatement.objects.all(), lambda x: x.number, ('number',), OwnerStatement)
app.option('commissions', lambda: Commission.objects.all(), lambda x: x.number, ('number',), Commission)
app.option('receipts', lambda: Receipt.objects.all(), lambda x: x.number, ('number',), Receipt)
app.option('checklist', lambda: ChecklistItem.objects.all(), lambda x: x.name, ('name',), ChecklistItem)

app.provide('property.units', lambda: Unit.objects.select_related('building'), lambda u: f'{u.building.name} {u.unit_no} ({u.get_status_display()})',
            ('unit_no', 'building__name', 'number'))
app.provide('property.buildings', lambda: Building.objects.filter(is_active=True), lambda x: x.name, ('name',))
app.provide('property.parties', lambda: Party.objects.all(), lambda x: f'{x.name} ({x.number})', ('name', 'email', 'number'))
app.provide('property.leases', lambda: Lease.objects.select_related('unit__building', 'party'), lambda x: f'{x.number} {x.unit.label} – {x.party.name}',
            ('number', 'party__name', 'unit__unit_no'))
app.provide('property.reservations', lambda: Reservation.objects.select_related('unit__building'), lambda x: f'{x.number} {x.unit.label}', ('number',))
app.provide('property.bills', lambda: Bill.objects.all(), lambda x: f'{x.number} {x.total}', ('number',))
app.provide('property.moveouts', lambda: MoveOut.objects.all(), lambda x: x.number, ('number',))


# ------------------------------------------------------------------ unit status (one place, so CRM listings follow)
def set_unit(u, status_, why, user=None):
    if u.status == status_:
        return
    old = u.get_status_display()
    u.status = status_
    u.save(update_fields=['status'])
    app.event('unit', u.pk, f'Status {old} → {u.get_status_display()}: {why}', user)
    call(app, 'ZeoCRM', 'unit_status', u.pk, status_)


def active_lease(u):
    return u.leases.filter(status__in=('active', 'notice')).order_by('-start').first()


# ------------------------------------------------------------------ party ↔ CRM contact
def party_dict(p):
    return {'name': p.name, 'email': p.email, 'phone': p.phone, 'party_id': p.pk, 'crm_contact_id': p.crm_contact_id,
            'type': 'buyer' if p.kind == 'buyer' else 'tenant', 'partner_id': p.partner_id}   # v1.41.0 partner


def party_saver(o, request, creating, old):
    o.save()
    if linked(app, 'ZeoCRM') and (creating or not o.crm_contact_id):
        cid = call(app, 'ZeoCRM', 'party_saved', party_dict(o))
        if cid and cid != o.crm_contact_id:
            Party.objects.filter(pk=o.pk).update(crm_contact_id=cid)
            o.crm_contact_id = cid
    return o


def party_from(contact):
    p = None
    if contact.get('party_id'):
        p = Party.objects.filter(pk=contact['party_id']).first()
    if p is None and contact.get('crm_contact_id'):
        p = Party.objects.filter(crm_contact_id=contact['crm_contact_id']).first()
    if p is None and contact.get('partner_id'):   # v1.41.0: the same business partner is the same person
        p = Party.objects.filter(partner_id=contact['partner_id']).order_by('pk').first()
    if p is None and contact.get('email') and not contact.get('partner_id'):
        p = Party.objects.filter(email__iexact=contact['email']).first()
    if p is None:
        p = Party.objects.create(number=app.next_number('party', 'TB'), name=contact.get('name') or 'Customer', email=contact.get('email') or '',
                                 phone=contact.get('phone') or '', company_name=contact.get('company') or '', crm_contact_id=contact.get('crm_contact_id'),
                                 kind=contact.get('kind') or 'tenant', partner_id=contact.get('partner_id'))
        app.event('party', p.pk, 'Created from CRM', None)
    elif contact.get('crm_contact_id') and not p.crm_contact_id:
        p.crm_contact_id = contact['crm_contact_id']
        p.save(update_fields=['crm_contact_id'])
    return p


# ------------------------------------------------------------------ portfolio rules
def unit_defaults(o, request):
    if not o.owner_id and o.building.owner_id:
        o.owner = o.building.owner


def unit_saver(o, request, creating, old):
    if not o.number:
        o.number = f'{o.building.number}-{o.unit_no}'[:30]
    if Unit.objects.filter(number=o.number).exclude(pk=o.pk).exists():
        raise AppError('Another unit already has this code.', 'unit_no')
    o.save()
    if old and old.status != o.status:
        call(app, 'ZeoCRM', 'unit_status', o.pk, o.status)
    return o


def unit_extra(o, request):
    lease = active_lease(o)
    out = {'current': None, 'work_orders': []}
    if lease:
        out['current'] = {'lease': lease.number, 'lease_id': lease.pk, 'tenant': lease.party.name, 'end': d(lease.end), 'rent': float(lease.annual_rent)}
    if linked(app, 'ZeoMaintenance'):
        out['work_orders'] = call(app, 'ZeoMaintenance', 'unit_work_orders', o.pk) or []
    return out


# ------------------------------------------------------------------ reservations
def res_defaults(o, request):
    o.reserved_on = o.reserved_on or today()
    if not o.expires_on:
        o.expires_on = o.reserved_on + timedelta(days=app.settings().reservation_days or 7)
    if not o.agent_id:
        o.agent = app.my_emp(request)


def res_check(o, request, old):
    if old is None:
        if o.unit.status not in ('available', 'notice'):
            raise AppError(f'{o.unit.label} is {o.unit.get_status_display().lower()} – it cannot be reserved.', 'unit')
        if Reservation.objects.filter(unit=o.unit, status='active').exists():
            raise AppError(f'{o.unit.label} already has an active reservation.', 'unit')
        if o.kind == 'sale' and o.party.kind == 'tenant':
            o.party.kind = 'both'
            o.party.save(update_fields=['kind'])
    if o.expires_on < o.reserved_on:
        raise AppError('The reservation cannot expire before it starts.', 'expires_on')


def res_saver(o, request, creating, old):
    o.save()
    if creating:
        set_unit(o.unit, 'reserved', f'reservation {o.number}', request.user if request else None)
    return o


def end_reservation(o, status_, why, user=None):
    o.status = status_
    o.save(update_fields=['status'])
    app.event('reservation', o.pk, why, user)
    if status_ in ('expired', 'cancelled'):
        if o.unit.status == 'reserved':
            set_unit(o.unit, 'notice' if active_lease(o.unit) else 'available', f'reservation {o.number} {status_}', user)
        if o.crm_opportunity_id:
            call(app, 'ZeoCRM', 'deal_reopen', o.crm_opportunity_id, f'Reservation {o.number} {status_}', user)


def a_res_lease(o, request, f):
    if o.kind != 'lease':
        raise AppError('This reservation is for a sale.')
    s = app.settings()
    start = f.dt('start') or today()
    rent = f.n('annual_rent') or o.unit.rent or ZERO
    lease = Lease.objects.create(number=app.next_number('lease', 'LSE'), unit=o.unit, party=o.party, reservation=o, start=start,
                                 end=add_months(start, 12) - timedelta(days=1), annual_rent=rent,
                                 deposit=money(rent * (s.deposit_percent or 0) / 100), payments=f.i('payments') or 4,
                                 agent=o.agent, commission_percent=s.commission_percent, crm_opportunity_id=o.crm_opportunity_id)
    app.event('lease', lease.pk, f'Drafted from reservation {o.number} by {user_name(request.user)}', request.user)
    o.status = 'converted'
    o.save(update_fields=['status'])
    app.event('reservation', o.pk, f'Converted to lease {lease.number}', request.user)
    return lease


def a_res_sale(o, request, f):
    if o.kind != 'sale':
        raise AppError('This reservation is for a lease.')
    s = app.settings()
    price = f.n('price') or o.unit.price or ZERO
    sale = SaleContract.objects.create(number=app.next_number('sale', 'SPA'), unit=o.unit, party=o.party, reservation=o, price=price,
                                       agent=o.agent, commission_percent=s.commission_percent, crm_opportunity_id=o.crm_opportunity_id,
                                       plan=[{'label': 'Reservation deposit', 'amount': float(o.amount), 'due': d(o.reserved_on), 'paid': True}] if o.amount else [])
    app.event('sale', sale.pk, f'Drafted from reservation {o.number} by {user_name(request.user)}', request.user)
    o.status = 'converted'
    o.save(update_fields=['status'])
    app.event('reservation', o.pk, f'Converted to sale contract {sale.number}', request.user)
    return sale


# ------------------------------------------------------------------ leases
def lease_defaults(o, request):
    s = app.settings()
    if o.annual_rent is None and o.unit.rent:
        o.annual_rent = o.unit.rent
    if not o.deposit and o.annual_rent:
        o.deposit = money(o.annual_rent * (s.deposit_percent or 0) / 100)
    if o.commission_percent is None:
        o.commission_percent = s.commission_percent
    if not o.end and o.start:
        o.end = add_months(o.start, 12) - timedelta(days=1)


def lease_check(o, request, old):
    if o.end <= o.start:
        raise AppError('The lease must end after it starts.', 'end')
    if not 1 <= (o.payments or 0) <= 12:
        raise AppError('Number of payments must be 1 to 12.', 'payments')
    clash = Lease.objects.filter(unit=o.unit, status__in=('active', 'notice'), start__lte=o.end, end__gte=o.start).exclude(pk=o.pk)
    if o.previous_id:
        clash = clash.exclude(pk=o.previous_id)
    if clash.exists():
        raise AppError(f'{o.unit.label} is let to {clash.first().party.name} in this period.', 'unit')
    if o.party.kind == 'buyer':
        o.party.kind = 'both'
        o.party.save(update_fields=['kind'])


def is_commercial(u):
    return bool(u.unit_type_id and u.unit_type.category == 'commercial')


def charge(kind, code=None):
    qs = ChargeType.objects.filter(is_active=True)
    return (qs.filter(code=code).first() if code else None) or qs.filter(kind=kind).order_by('id').first() or \
        ChargeType.objects.create(code=kind.upper()[:20], name=dict(ChargeType._meta.get_field('kind').choices)[kind], kind=kind,
                                  review_status='reviewed')


def make_bill(*, party=None, owner=None, unit=None, lease=None, sale=None, ch, amount, due, desc='', pfrom=None, pto=None, source='manual',
              vat_percent=None, status_='issued', wo=None):
    amount = money(amount)
    vp = ch.vat_percent if vat_percent is None else vat_percent
    vat = money(amount * (vp or 0) / 100)
    b = Bill.objects.create(number=app.next_number('bill', 'BIL'), party=party, owner=owner, unit=unit, lease=lease, sale=sale, charge=ch,
                            description=desc[:200], period_from=pfrom, period_to=pto, due_date=due, amount=amount, vat=vat, total=amount + vat,
                            status=status_, source=source, mnt_work_order_id=wo)
    app.event('bill', b.pk, f'Created ({dict(Bill._meta.get_field("source").choices)[source]})', None)
    return b


def lease_schedule(o):
    """Rent split into equal payments over the lease period (last one takes the rounding); deposit as its own bill."""
    s = app.settings()
    rent = charge('rent')
    vat = s.commercial_vat_percent if is_commercial(o.unit) else ZERO
    n = o.payments
    months = max(1, round(((o.end - o.start).days + 1) / 30.4))
    total = money(o.annual_rent * Decimal(months) / 12)
    part = money(total / n)
    bills = []
    for i in range(n):
        frm = add_months(o.start, i * months // n)
        to = (add_months(o.start, (i + 1) * months // n) - timedelta(days=1)) if i < n - 1 else o.end
        amt = part if i < n - 1 else total - part * (n - 1)
        bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=rent, amount=amt, due=frm, desc=f'Rent {i + 1}/{n} {o.unit.label}',
                               pfrom=frm, pto=to, source='lease', vat_percent=vat))
    if o.deposit:
        bills.append(make_bill(party=o.party, unit=o.unit, lease=o, ch=charge('deposit'), amount=o.deposit, due=o.start,
                               desc=f'Security deposit {o.unit.label}', source='lease', vat_percent=ZERO))
    return bills


def make_commission(agent, base, pct, lease=None, sale=None, user=None):
    if not agent or not pct:
        return None
    c = Commission.objects.create(number=app.next_number('commission', 'COM'), agent=agent, lease=lease, sale=sale, base=money(base),
                                  percent=pct, amount=money(base * pct / 100))
    app.event('commission', c.pk, 'Created – waiting for approval', user)
    app.tell(app.users_with_role('prop_commission'), f'Commission {c.number} to approve', f'{emp_name(agent)}: {cur()} {c.amount}',
             'commission', c.pk, c.number, PAGE + f'r/commission/{c.pk}', summary=f'Approve commission {c.number}')
    return c


def checklist(applies):
    return [{'item': x.name, 'done': False} for x in ChecklistItem.objects.filter(is_active=True, applies_to__in=(applies, 'both'))
            .exclude(review_status='to_review')]


def a_lease_activate(o, request, f):
    if o.status != 'draft':
        raise AppError('Only a draft lease can be activated.')
    if o.unit.status in ('let', 'sold') and not (o.previous_id and o.previous.unit_id == o.unit_id):
        raise AppError(f'{o.unit.label} is {o.unit.get_status_display().lower()}.')
    o.registration_ref = f.s('registration_ref') or o.registration_ref
    o.status = 'active'
    o.handover = o.handover or checklist('lease')
    o.save()
    bills = lease_schedule(o)
    if o.previous_id:
        prev = o.previous
        prev.status, prev.renewal_status = 'renewed', 'renewing'
        prev.save(update_fields=['status', 'renewal_status'])
        app.event('lease', prev.pk, f'Renewed by {o.number}', request.user)
        call(app, 'ZeoCRM', 'renewal_closed', prev.pk, True, o.number, request.user)
    else:
        make_commission(o.agent, o.annual_rent, o.commission_percent, lease=o, user=request.user)
        if linked(app, 'ZeoMaintenance'):
            r = call(app, 'ZeoMaintenance', 'inspection', kind='move_in', unit_id=o.unit_id, lease_id=o.pk, date=o.start,
                     ref=f'Lease {o.number}', note=f'Move-in of {o.party.name}')
            if r:
                o.mnt_inspection_id = r['id']
                o.save(update_fields=['mnt_inspection_id'])
    set_unit(o.unit, 'let', f'lease {o.number}', request.user)
    if o.reservation_id and o.reservation.status == 'active':
        o.reservation.status = 'converted'
        o.reservation.save(update_fields=['status'])
    if o.crm_opportunity_id and not o.previous_id:
        call(app, 'ZeoCRM', 'deal_won', o.crm_opportunity_id, f'Lease {o.number}', request.user)
    app.event('lease', o.pk, f'Activated – {len(bills)} billing records' + (f', registration {o.registration_ref}' if o.registration_ref else ''), request.user)
    return {'message': f'Lease active. {len(bills)} billing records made' + ('; move-in inspection sent to Maintenance.' if o.mnt_inspection_id else '.')}


def a_lease_cheques(o, request, f):
    """Record the post-dated cheques: one per rent billing record, numbered from the first cheque number."""
    first = f.s('first_cheque_no')
    if not first.isdigit():
        raise AppError('Enter the first cheque number (digits).', 'first_cheque_no')
    bills = list(o.bills.filter(charge__kind='rent').exclude(status='void').order_by('due_date'))
    have = set(o.cheques.exclude(status__in=('returned', 'replaced')).values_list('bill_id', flat=True))
    n = 0
    for i, b in enumerate(x for x in bills if x.pk not in have):
        no = str(int(first) + i).zfill(len(first))
        Cheque.objects.create(number=app.next_number('cheque', 'CHQ'), lease=o, party=o.party, bill=b, cheque_no=no, bank=f.s('bank'),
                              amount=b.total, cheque_date=b.due_date)
        n += 1
    app.event('lease', o.pk, f'{n} cheque(s) recorded ({f.s("bank")})', request.user)
    return {'message': f'{n} cheque(s) recorded.'}


def a_lease_notice(o, request, f):
    o.notice_given_on = f.dt('notice_given_on') or today()
    o.move_out_on = f.dt('move_out_on', True, 'Move-out date')
    o.status, o.renewal_status = 'notice', 'leaving'
    o.save()
    set_unit(o.unit, 'notice', f'tenant leaving {d(o.move_out_on)}', request.user)
    call(app, 'ZeoCRM', 'renewal_closed', o.pk, False, '', request.user)
    app.event('lease', o.pk, f'Notice given – moving out {d(o.move_out_on)}', request.user)
    return o


def a_lease_renew(o, request, f):
    if o.next_versions.exclude(status='cancelled').exists():
        raise AppError('A renewal is already drafted for this lease.')
    start = o.end + timedelta(days=1)
    new = Lease.objects.create(number=app.next_number('lease', 'LSE'), unit=o.unit, party=o.party, start=start, end=add_months(start, 12) - timedelta(days=1),
                               annual_rent=f.n('annual_rent') or o.annual_rent, deposit=o.deposit, payments=f.i('payments') or o.payments,
                               method=o.method, version=o.version + 1, previous=o, agent=o.agent, commission_percent=o.commission_percent)
    o.renewal_status = 'renewing'
    o.save(update_fields=['renewal_status'])
    app.event('lease', o.pk, f'Renewal drafted: {new.number}', request.user)
    app.event('lease', new.pk, f'Drafted as renewal of {o.number} (version {new.version})', request.user)
    return new


def a_lease_moveout(o, request, f):
    if o.moveouts.exclude(status='cancelled').exists():
        raise AppError('A move-out is already planned.')
    m = MoveOut.objects.create(number=app.next_number('moveout', 'MO'), lease=o, move_out_on=f.dt('move_out_on') or o.move_out_on or o.end,
                               inspection_on=f.dt('inspection_on'))
    app.event('moveout', m.pk, f'Planned for lease {o.number}', request.user)
    if linked(app, 'ZeoMaintenance'):
        r = call(app, 'ZeoMaintenance', 'inspection', kind='move_out', unit_id=o.unit_id, lease_id=o.pk, date=m.inspection_on or m.move_out_on,
                 ref=f'Move-out {m.number}', note=f'Move-out of {o.party.name}', moveout_id=m.pk)
        if r:
            m.mnt_inspection_id = r['id']
            m.save(update_fields=['mnt_inspection_id'])
    return m


def a_lease_terminate(o, request, f):
    o.status = 'terminated'
    o.move_out_on = f.dt('move_out_on') or today()
    o.notes = (o.notes + '\n' if o.notes else '') + f'Terminated: {f.s("reason")}'
    o.save()
    for b in o.bills.filter(status='issued', period_from__gt=o.move_out_on):
        b.status = 'void'
        b.save(update_fields=['status'])
    set_unit(o.unit, 'available', f'lease {o.number} terminated', request.user)
    app.event('lease', o.pk, f'Terminated – {f.s("reason")}', request.user)
    return o


def a_handover(o, request, f):
    """Tick handover checklist lines: items = indexes done."""
    items = f.ids('done')
    lst = list(o.handover or [])
    for i, x in enumerate(lst):
        x['done'] = i in items
    o.handover = lst
    o.save(update_fields=['handover'])
    app.event(MODEL_OF[type(o)], o.pk, f'Handover checklist: {sum(1 for x in lst if x["done"])}/{len(lst)} done', request.user)
    return o


MODEL_OF = {Lease: 'lease', SaleContract: 'sale'}


def lease_extra(o, request):
    bills = o.bills.exclude(status='void')
    return {'money': {'billed': float(bills.aggregate(s=Sum('total'))['s'] or 0), 'paid': float(bills.aggregate(s=Sum('paid'))['s'] or 0),
                      'overdue': float(sum(b.balance for b in bills.filter(due_date__lt=today(), status__in=('issued', 'part_paid'))))},
            'checklist': o.handover, 'versions': [{'id': x.pk, 'number': x.number, 'status': x.get_status_display(), 'start': d(x.start)}
                                                  for x in Lease.objects.filter(Q(previous=o) | Q(pk=o.previous_id))],
            'links': {'inspection': link_label('maintenance.inspections', o.mnt_inspection_id) if o.mnt_inspection_id else '',
                      'crm': link_label('crm.opportunities', o.crm_opportunity_id) if o.crm_opportunity_id else ''}}


# ------------------------------------------------------------------ sales
def a_sale_sign(o, request, f):
    if o.unit.status not in ('available', 'reserved', 'notice'):
        raise AppError(f'{o.unit.label} is {o.unit.get_status_display().lower()}.')
    o.signed_on = f.dt('signed_on') or today()
    o.status = 'signed'
    o.handover = o.handover or checklist('sale')
    o.save()
    ch = charge('sale')
    n = 0
    for i, p in enumerate(o.plan or []):
        amt = Decimal(str(p.get('amount') or 0)) or money(o.price * Decimal(str(p.get('percent') or 0)) / 100)
        if not amt or p.get('paid'):
            continue
        make_bill(party=o.party, unit=o.unit, sale=o, ch=ch, amount=amt, due=(p.get('due') and date.fromisoformat(str(p['due'])[:10])) or o.signed_on,
                  desc=f'{p.get("label") or "Instalment " + str(i + 1)} – {o.unit.label}', source='sale', vat_percent=ZERO)
        n += 1
    if not o.plan:
        make_bill(party=o.party, unit=o.unit, sale=o, ch=ch, amount=o.price, due=o.signed_on, desc=f'Sale price {o.unit.label}', source='sale', vat_percent=ZERO)
        n = 1
    set_unit(o.unit, 'sold', f'sale contract {o.number}', request.user)
    if o.reservation_id and o.reservation.status == 'active':
        o.reservation.status = 'converted'
        o.reservation.save(update_fields=['status'])
    make_commission(o.agent, o.price, o.commission_percent, sale=o, user=request.user)
    if o.crm_opportunity_id:
        call(app, 'ZeoCRM', 'deal_won', o.crm_opportunity_id, f'Sale contract {o.number}', request.user)
    app.event('sale', o.pk, f'Signed – {n} instalment billing record(s)', request.user)
    return {'message': f'Contract signed; {n} billing record(s) made.'}


def a_sale_handover(o, request, f):
    open_items = [x['item'] for x in (o.handover or []) if not x.get('done')]
    if open_items and not f.b('force'):
        raise AppError('Handover checklist not complete: ' + ', '.join(open_items[:5]), 'force')
    o.handed_over_on = f.dt('handed_over_on') or today()
    o.status = 'handed_over'
    o.save()
    if linked(app, 'ZeoMaintenance'):
        r = call(app, 'ZeoMaintenance', 'inspection', kind='snag', unit_id=o.unit_id, lease_id=None, date=o.handed_over_on,
                 ref=f'Sale {o.number}', note=f'Snag list with {o.party.name}')
        if r:
            o.mnt_inspection_id = r['id']
            o.save(update_fields=['mnt_inspection_id'])
    app.event('sale', o.pk, 'Handed over to the buyer', request.user)
    return o


def a_sale_cancel(o, request, f):
    o.status = 'cancelled'
    o.save(update_fields=['status'])
    for b in o.bills.filter(status='issued', paid=0):
        b.status = 'void'
        b.save(update_fields=['status'])
    if o.unit.status in ('sold', 'reserved'):
        set_unit(o.unit, 'available', f'sale {o.number} cancelled', request.user)
    app.event('sale', o.pk, f'Cancelled – {f.s("reason")}', request.user)
    return o


# ------------------------------------------------------------------ billing, receipts, cheques
def bill_defaults(o, request):
    if o.lease_id and not o.party_id:
        o.party = o.lease.party
    if o.lease_id and not o.unit_id:
        o.unit = o.lease.unit


def bill_saver(o, request, creating, old):
    if not o.party_id and not o.owner_id:
        raise AppError('Choose who pays: a tenant / buyer or an owner.', 'party')
    o.amount = money(o.amount)
    if creating or (old and (old.amount != o.amount or old.charge_id != o.charge_id)):
        vp = o.charge.vat_percent
        if o.unit_id and is_commercial(o.unit) and o.charge.kind == 'rent':
            vp = app.settings().commercial_vat_percent
        o.vat = money(o.amount * (vp or 0) / 100)
    o.total = o.amount + o.vat
    o.save()
    return o


def refresh_bill(b):
    b.paid = money(b.allocations.filter(receipt__status='posted').aggregate(s=Sum('amount'))['s'] or 0)
    if b.status != 'void':
        b.status = 'paid' if b.paid >= b.total and b.total > 0 else ('part_paid' if b.paid > 0 else ('issued' if b.status != 'draft' else 'draft'))
    b.save(update_fields=['paid', 'status'])


def post_receipt(r, user, bill_ids=None):
    if r.status != 'draft':
        raise AppError('Only a draft receipt can be posted.')
    left = money(r.amount)
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
    r.allocated = money(r.amount) - left
    r.status = 'posted'
    r.save(update_fields=['allocated', 'status'])
    for b in Bill.objects.filter(allocations__receipt=r).distinct():
        refresh_bill(b)
    app.event('receipt', r.pk, f'Posted – {cur()} {r.allocated} allocated' + (f', {left} unallocated' if left else ''), user)
    return r


def a_receipt_post(o, request, f):
    post_receipt(o, request.user, f.ids('bills'))
    return o


def a_receipt_void(o, request, f):
    bills = list(Bill.objects.filter(allocations__receipt=o).distinct())
    o.status = 'void'
    o.save(update_fields=['status'])
    for b in bills:
        refresh_bill(b)
    app.event('receipt', o.pk, f'Voided – {f.s("reason")}', request.user)
    return o


def receipt_check(o, request, old):
    if not o.party_id and not o.owner_id:
        raise AppError('Choose who paid.', 'party')
    if o.amount is None or o.amount <= 0:
        raise AppError('Amount must be more than zero.', 'amount')


def a_cheque_status(new, text):
    def run(o, request, f):
        o.status = new
        if new == 'deposited':
            o.deposited_on = f.dt('deposited_on') or today()
        o.note = f.s('note') or o.note
        o.save()
        app.event('cheque', o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        if new == 'cleared':
            # v1.62.0 the receipt is dated on the clearing date entered (was always today – owner statements of past months missed it)
            r = Receipt.objects.create(number=app.next_number('receipt', 'RCT'), party=o.party, received_on=f.dt('cleared_on') or today(), amount=o.amount, method='cheque',
                                       reference=f'Cheque {o.cheque_no} {o.bank}', cheque=o)
            post_receipt(r, request.user, [o.bill_id] if o.bill_id else None)
            return {'message': f'Receipt {r.number} posted.'}
        if new == 'bounced':
            users = app.users_with_role('prop_finance') + app.users_with_role('prop_leasing')
            app.tell(users, f'Cheque bounced: {o.cheque_no}', f'{o.party.name} – {cur()} {o.amount} ({o.bank}). {f.s("note")}', 'cheque', o.pk,
                     o.number, PAGE + f'r/cheque/{o.pk}', summary=f'Bounced cheque {o.cheque_no} – {o.party.name}')
            if o.party.email:
                app.send_mail([o.party.email], 'Cheque returned unpaid',
                              f'Dear {o.party.name},\n\nYour cheque {o.cheque_no} ({o.bank}) for {cur()} {o.amount} was returned unpaid. '
                              'Please contact us to replace it.\n')
        return o
    return run


# ------------------------------------------------------------------ commissions, statements, move-outs, agreements
def a_comm(new, text):
    def run(o, request, f):
        o.status = new
        o.external_ref = f.s('external_ref') or o.external_ref
        o.note = f.s('note') or o.note
        o.save()
        app.event('commission', o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos('commission', o.pk)
        if o.agent_id and o.agent.users_id:   # v1.62.0 a broker commission has no agent (approve crashed)
            app.tell([o.agent.users], f'Commission {o.number} {o.get_status_display().lower()}', f'{cur()} {o.amount}', 'commission', o.pk, o.number,
                     PAGE + f'r/commission/{o.pk}')
        return o
    return run


def statement_saver(o, request, creating, old):
    if o.period_to < o.period_from:
        raise AppError('The period ends before it starts.', 'period_to')
    if creating or o.status == 'draft':
        build_statement(o)
    o.save()
    return o


def build_statement(o):
    units = list(Unit.objects.filter(owner=o.owner).select_related('building'))
    ag = o.owner.agreements.filter(status='active').order_by('-start').first()
    fee_pct = ag.fee_percent if ag else ZERO
    lines, collected, fees = [], ZERO, ZERO
    allocs = Allocation.objects.filter(receipt__status='posted', receipt__received_on__range=(o.period_from, o.period_to), bill__unit__in=units,
                                       bill__charge__kind__in=('rent', 'service')).select_related('bill__unit__building', 'receipt')
    for a in allocs:
        collected += a.amount
        lines.append({'date': d(a.receipt.received_on), 'unit': a.bill.unit.label, 'text': f'{a.bill.description or a.bill.charge.name} ({a.receipt.number})',
                      'amount': float(a.amount)})
    fees = money(collected * fee_pct / 100)
    if fees:
        lines.append({'date': d(o.period_to), 'unit': '', 'text': f'Management fee {fee_pct}%', 'amount': -float(fees)})
    exp = ZERO
    for b in Bill.objects.filter(owner=o.owner, due_date__range=(o.period_from, o.period_to)).exclude(status='void'):
        exp += b.total
        lines.append({'date': d(b.due_date), 'unit': b.unit.label if b.unit_id else '', 'text': f'{b.description or b.charge.name} ({b.number})', 'amount': -float(b.total)})
    o.collected, o.fees, o.expenses = money(collected), fees, money(exp)
    o.net = o.collected - o.fees - o.expenses
    o.lines = sorted(lines, key=lambda x: x['date'] or '')


def a_statement_issue(o, request, f):
    build_statement(o)
    o.status = 'issued'
    o.save()
    if o.owner.email:
        app.send_mail([o.owner.email], f'Owner statement {o.number}',
                      f'Dear {o.owner.name},\n\nYour statement for {d(o.period_from)} – {d(o.period_to)} is ready in the owner portal.\n'
                      f'Collected {cur()} {o.collected}, expenses {o.expenses}, fees {o.fees}, net {o.net}.\n')
    app.event('statement', o.pk, 'Issued to the owner', request.user)
    return o


def moveout_inspected(m, deductions, note, user=None, source='manual'):
    m.deductions = money(deductions)
    m.deduction_note = note or m.deduction_note
    m.status = 'inspected'
    m.save()
    app.event('moveout', m.pk, f'Inspected ({source}) – deductions {cur()} {m.deductions}', user)
    app.tell(app.users_with_role('prop_finance'), f'Move-out {m.number} ready to settle', f'Lease {m.lease.number}: deductions {m.deductions}',
             'moveout', m.pk, m.number, PAGE + f'r/moveout/{m.pk}', summary=f'Settle deposit {m.number}')


def a_moveout_inspected(o, request, f):
    moveout_inspected(o, f.n('deductions', ZERO), f.s('deduction_note'), request.user)
    return o


def a_moveout_settle(o, request, f):
    lease = o.lease
    # v1.62.0 only the tenant's bills: the owner's leasing-fee bill of the lease was deducted from the tenant's deposit (no refund)
    unpaid = sum((b.balance for b in lease.bills.filter(party=lease.party, owner__isnull=True, status__in=('issued', 'part_paid'), charge__kind__in=('rent', 'service', 'utility', 'maintenance', 'fee'),
                                                         due_date__lte=o.move_out_on)), ZERO)
    dep_paid = sum((b.paid for b in lease.bills.filter(charge__kind='deposit').exclude(status='void')), ZERO)
    o.unpaid = money(unpaid)
    o.refund = max(ZERO, money(dep_paid - o.deductions - o.unpaid))
    short = money(o.deductions + o.unpaid - dep_paid)
    o.status, o.settled_on, o.keys_returned = 'settled', today(), f.b('keys_returned') or o.keys_returned
    o.save()
    if short > 0 and o.deductions:
        make_bill(party=lease.party, unit=lease.unit, lease=lease, ch=charge('fee'), amount=min(short, o.deductions), due=today(),
                  desc=f'Move-out deductions {o.number}', source='moveout', vat_percent=ZERO)
    for b in lease.bills.filter(status='issued', period_from__gt=o.move_out_on):
        b.status = 'void'
        b.save(update_fields=['status'])
    lease.status, lease.move_out_on = 'ended', o.move_out_on
    lease.save(update_fields=['status', 'move_out_on'])
    for c in lease.cheques.filter(status='received', cheque_date__gt=o.move_out_on):
        c.status = 'returned'
        c.save(update_fields=['status'])
    if linked(app, 'ZeoMaintenance'):
        set_unit(lease.unit, 'maintenance', f'move-out {o.number}: unit-ready works', request.user)
        call(app, 'ZeoMaintenance', 'inspection', kind='unit_ready', unit_id=lease.unit_id, lease_id=None, date=today(), ref=f'Move-out {o.number}',
             note='Make the unit ready for the next tenant')
    else:
        set_unit(lease.unit, 'available', f'move-out {o.number}', request.user)
    app.event('moveout', o.pk, f'Settled – refund {cur()} {o.refund}, unpaid {o.unpaid}, deductions {o.deductions}', request.user)
    return {'message': f'Deposit settled: refund {cur()} {o.refund}.'}


def a_agreement(new):
    def run(o, request, f):
        o.status = new
        if new == 'ended':
            o.end = f.dt('end') or today()
        o.save()
        app.event('agreement', o.pk, f'{o.get_status_display()}', request.user)
        return o
    return run


# ------------------------------------------------------------------ registers
def invite_act(o, request, f):
    from .portal import invite_action
    return invite_action(o, request, f)


def own_agent(field='agent'):
    return ((field, 'emp'),)


R = app.reg
R(Reg('building', Building, 'Building', 'Buildings', 'Portfolio', cols=('number', 'name', 'kind', 'city', 'owner', 'manager', 'is_active'),
      form=('number', 'name', 'kind', 'address', 'city', 'owner', 'floors', 'manager', 'notes', 'is_active'), manage=MGR + ('prop_leasing',),
      view=ALL_STAFF, search=('name', 'number', 'city'), filters=('kind', 'is_active'), title='name', subtitle='city',
      children=(('Units', 'unit', 'building'),), help='Buildings, towers and communities.'))
R(Reg('unit', Unit, 'Unit', 'Units', 'Portfolio',
      cols=('number', 'building', 'unit_no', 'unit_type', 'bedrooms', 'area', 'owner', 'status', 'rent', 'price'),
      form=('building', 'unit_no', 'floor', 'unit_type', 'bedrooms', 'bathrooms', 'area', 'owner', 'rent', 'price', 'furnishing', 'parking', 'status', 'notes'),
      manage=MGR + ('prop_leasing',), view=ALL_STAFF, search=('number', 'unit_no', 'building__name'), filters=('building', 'status', 'unit_type', 'owner'),
      title='number', subtitle='building', defaults=unit_defaults, saver=unit_saver, child_of='building', extra=unit_extra,
      children=(('Leases', 'lease', 'unit'), ('Reservations', 'reservation', 'unit')),
      help='Every unit with its status: available → reserved → let / sold; under notice; under maintenance.'))
R(Reg('owner', Owner, 'Owner', 'Owners', 'Portfolio', cols=('number', 'name', 'kind', 'email', 'phone', 'is_active'),
      form=('kind', 'name', 'email', 'phone', 'address', 'bank_details', 'is_active'), number=('owner', 'OWN'), manage=MGR, view=ALL_STAFF,
      restricted=('bank_details',), can_restricted=lambda r, o: app.any(r, *FIN), search=('name', 'email', 'phone', 'number'), filters=('is_active',),
      title='name', children=(('Units', 'unit', 'owner'), ('Agreements', 'agreement', 'owner'), ('Statements', 'statement', 'owner')),
      actions=[Act('portal', 'Send portal login', invite_act,
                   lambda o, r: app.any(r, *MGR) and bool(o.email), fields=(('reset', 'Reset the password if a login exists', 'bool', False),))],
      help='Landlords whose units you manage or sell.'))
R(Reg('agreement', Agreement, 'Management agreement', 'Management agreements', 'Portfolio',
      cols=('number', 'owner', 'start', 'end', 'fee_percent', 'approval_limit', 'status'),
      form=('owner', 'units', 'start', 'end', 'fee_percent', 'leasing_fee', 'approval_limit', 'notes'), number=('agreement', 'MA'), manage=MGR,
      view=ALL_STAFF, search=('number', 'owner__name'), filters=('status', 'owner'), subtitle='owner', child_of='owner',
      edit_when=lambda o: o.status == 'draft',
      actions=[Act('activate', 'Activate', a_agreement('active'), when(('draft',), *MGR), style='primary'),
               Act('end', 'End', a_agreement('ended'), when(('active',), *MGR), fields=(('end', 'End date', 'date', False),))],
      help='Fee and maintenance approval limit agreed with each owner.'))
R(Reg('party', Party, 'Tenant / buyer', 'Tenants and buyers', 'Customers',
      cols=('number', 'name', 'kind', 'company_name', 'email', 'phone', 'is_active'),
      form=('kind', 'party_type', 'name', 'company_name', 'email', 'phone', 'id_document', 'address', 'crm_contact_id', 'is_active'),
      number=('party', 'TB'), manage=DEALS, view=ALL_STAFF, restricted=('id_document',), can_restricted=lambda r, o: app.any(r, *DEALS + FIN),
      search=('name', 'email', 'phone', 'number', 'company_name'), filters=('kind', 'is_active'), title='name', subtitle='company_name',
      saver=party_saver, links={'crm_contact_id': 'crm.contacts'},
      children=(('Leases', 'lease', 'party'), ('Purchases', 'sale', 'party'), ('Billing records', 'bill', 'party')),
      actions=[Act('portal', 'Send portal login', invite_act,
                   lambda o, r: app.any(r, *DEALS) and bool(o.email), fields=(('reset', 'Reset the password if a login exists', 'bool', False),))],
      help='Tenants and buyers (one record per person or company).'))
R(Reg('reservation', Reservation, 'Reservation', 'Reservations', 'Deals',
      cols=('number', 'unit', 'party', 'kind', 'amount', 'reserved_on', 'expires_on', 'status'),
      form=('unit', 'party', 'kind', 'amount', 'reserved_on', 'expires_on', 'agent', 'crm_opportunity_id', 'notes'), number=('reservation', 'RSV'),
      manage=DEALS, view=ALL_STAFF, search=('number', 'party__name', 'unit__unit_no'), filters=('status', 'kind'), subtitle='party',
      defaults=res_defaults, check=res_check, saver=res_saver, edit_when=lambda o: o.status == 'active', links={'crm_opportunity_id': 'crm.opportunities'},
      actions=[Act('to_lease', 'Make the lease', a_res_lease, lambda o, r: o.status == 'active' and o.kind == 'lease' and app.any(r, *LEASING), style='primary',
                   fields=(('start', 'Lease start', 'date', True), ('annual_rent', 'Annual rent', 'decimal', False), ('payments', 'Number of payments', 'int', False))),
               Act('to_sale', 'Make the sale contract', a_res_sale, lambda o, r: o.status == 'active' and o.kind == 'sale' and app.any(r, *SALES), style='primary',
                   fields=(('price', 'Sale price', 'decimal', False),)),
               Act('extend', 'Extend', lambda o, r, f: (setattr(o, 'expires_on', f.dt('expires_on', True)), o.save(), app.event('reservation', o.pk, f'Extended to {d(o.expires_on)}', r.user), o)[-1],
                   when(('active',), *DEALS), fields=(('expires_on', 'New expiry date', 'date', True),)),
               Act('cancel', 'Cancel', lambda o, r, f: (end_reservation(o, 'cancelled', f'Cancelled – {f.s("reason")}', r.user), o)[-1],
                   when(('active',), *DEALS), style='danger', fields=(('reason', 'Reason', 'text', True),))],
      help='A unit held for a tenant or buyer until it expires.'))
R(Reg('lease', Lease, 'Lease', 'Leases', 'Deals',
      cols=('number', 'unit', 'party', 'start', 'end', 'annual_rent', 'payments', 'status', 'renewal_status'),
      form=('unit', 'party', 'start', 'end', 'annual_rent', 'deposit', 'payments', 'method', 'registration_ref', 'agent', 'commission_percent',
            'crm_opportunity_id', 'notes'),
      number=('lease', 'LSE'), manage=LEASING, view=ALL_STAFF + ('prop_finance',), search=('number', 'party__name', 'unit__unit_no', 'unit__building__name'),
      filters=('status', 'renewal_status', 'unit', 'party'), subtitle='party', defaults=lease_defaults, check=lease_check,
      edit_when=lambda o: o.status == 'draft', extra=lease_extra, links={'crm_opportunity_id': 'crm.opportunities', 'mnt_inspection_id': 'maintenance.inspections'},
      info=('version', 'previous', 'reservation', 'notice_given_on', 'move_out_on', 'mnt_inspection_id'),
      children=(('Billing records', 'bill', 'lease'), ('Cheques', 'cheque', 'lease'), ('Move-out', 'moveout', 'lease')),
      actions=[Act('activate', 'Activate (sign)', a_lease_activate, when(('draft',), *LEASING), style='primary',
                   fields=(('registration_ref', 'Government registration ref.', 'text', False),)),
               Act('cheques', 'Record cheques', a_lease_cheques, lambda o, r: o.status in ('active', 'notice') and o.method == 'cheque' and app.any(r, *LEASING + FIN),
                   fields=(('first_cheque_no', 'First cheque no.', 'text', True), ('bank', 'Bank', 'text', True))),
               Act('handover', 'Move-in checklist', a_handover, lambda o, r: o.status == 'active' and bool(o.handover) and app.any(r, *LEASING),
                   fields=(('done', 'Done items', 'checklist', False),)),
               Act('renew', 'Draft renewal', a_lease_renew, lambda o, r: o.status in ('active',) and app.any(r, *LEASING),
                   fields=(('annual_rent', 'New annual rent', 'decimal', False), ('payments', 'Number of payments', 'int', False))),
               Act('notice', 'Tenant gives notice', a_lease_notice, when(('active',), *LEASING),
                   fields=(('notice_given_on', 'Notice date', 'date', False), ('move_out_on', 'Move-out date', 'date', True))),
               Act('moveout', 'Plan move-out', a_lease_moveout, when(('active', 'notice'), *LEASING),
                   fields=(('move_out_on', 'Move-out date', 'date', False), ('inspection_on', 'Inspection date', 'date', False))),
               Act('terminate', 'Terminate early', a_lease_terminate, when(('active', 'notice'), *MGR), style='danger',
                   fields=(('move_out_on', 'Ends on', 'date', True), ('reason', 'Reason', 'text', True))),
               Act('cancel', 'Cancel draft', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), app.event('lease', o.pk, 'Cancelled', r.user), o)[-1],
                   when(('draft',), *LEASING), style='danger')],
      help='Lease contracts; activating one makes the rent schedule and the deposit as billing records.'))
R(Reg('sale', SaleContract, 'Sale contract', 'Sale contracts', 'Deals',
      cols=('number', 'unit', 'party', 'price', 'signed_on', 'handover_due', 'status'),
      form=('unit', 'party', 'price', 'plan', 'handover_due', 'agent', 'commission_percent', 'crm_opportunity_id', 'notes'), number=('sale', 'SPA'),
      manage=SALES, view=ALL_STAFF + ('prop_finance',), search=('number', 'party__name', 'unit__unit_no'), filters=('status',), subtitle='party',
      edit_when=lambda o: o.status == 'draft', links={'crm_opportunity_id': 'crm.opportunities', 'mnt_inspection_id': 'maintenance.inspections'},
      info=('handed_over_on', 'reservation', 'mnt_inspection_id'),
      extra=lambda o, r: {'checklist': o.handover, 'links': {'inspection': link_label('maintenance.inspections', o.mnt_inspection_id) if o.mnt_inspection_id else ''}},
      children=(('Billing records', 'bill', 'sale'),),
      actions=[Act('sign', 'Signed', a_sale_sign, when(('draft',), *SALES), style='primary', fields=(('signed_on', 'Signed on', 'date', False),)),
               Act('handover', 'Handover checklist', a_handover, lambda o, r: o.status == 'signed' and bool(o.handover) and app.any(r, *SALES),
                   fields=(('done', 'Done items', 'checklist', False),)),
               Act('handed_over', 'Handed over', a_sale_handover, when(('signed',), *SALES),
                   fields=(('handed_over_on', 'Handover date', 'date', False), ('force', 'Hand over with open checklist items', 'bool', False))),
               Act('cancel', 'Cancel', a_sale_cancel, when(('draft', 'signed'), *MGR), style='danger', fields=(('reason', 'Reason', 'text', True),))],
      help='Unit sales: payment plan, signing, handover and snags.'))
R(Reg('bill', Bill, 'Billing record', 'Billing records', 'Finance',
      cols=('number', 'party', 'owner', 'unit', 'charge', 'description', 'due_date', 'total', 'paid', 'status', 'external_ref', 'external_status'),
      form=('party', 'owner', 'unit', 'lease', 'sale', 'charge', 'description', 'period_from', 'period_to', 'due_date', 'amount'),
      number=('bill', 'BIL'), manage=FIN, view=ALL_STAFF, search=('number', 'description', 'party__name', 'owner__name', 'external_ref'),
      filters=('status', 'external_status', 'charge', 'source', 'party', 'owner', 'lease'), subtitle='party', defaults=bill_defaults, saver=bill_saver,
      edit_when=lambda o: o.status == 'draft', export_extra=('vat', 'amount', 'period_from', 'period_to', 'lease', 'sale', 'source', 'mnt_work_order_id'),
      links={'mnt_work_order_id': 'maintenance.work_orders'}, info=('vat', 'source', 'mnt_work_order_id', 'created_at'),
      actions=[Act('issue', 'Issue', lambda o, r, f: (setattr(o, 'status', 'issued'), o.save(), app.event('bill', o.pk, 'Issued', r.user), o)[-1],
                   when(('draft',), *FIN), style='primary'),
               Act('accounting', 'Accounting reference', lambda o, r, f: (setattr(o, 'external_ref', f.s('external_ref')), setattr(o, 'external_status', f.choice('external_status', Bill._meta.get_field('external_status').choices, 'posted')),
                                                                          o.save(), app.event('bill', o.pk, f'Accounting: {o.external_ref} ({o.get_external_status_display()})', r.user), o)[-1],
                   lambda o, r: o.status != 'void' and app.any(r, *FIN),
                   fields=(('external_ref', 'Invoice no. in accounting', 'text', True), ('external_status', 'Status', 'choice:exported=Exported|posted=Posted', True))),
               Act('void', 'Void', lambda o, r, f: (setattr(o, 'status', 'void'), o.save(), app.event('bill', o.pk, f'Voided – {f.s("reason")}', r.user), o)[-1],
                   lambda o, r: o.status in ('draft', 'issued') and o.paid == 0 and app.any(r, *FIN), style='danger', fields=(('reason', 'Reason', 'text', True),))],
      help='What tenants, buyers and owners owe. Invoices are made in your accounting system – export this list to Excel and record the invoice number here.'))
R(Reg('receipt', Receipt, 'Receipt', 'Receipts', 'Finance',
      cols=('number', 'party', 'owner', 'received_on', 'amount', 'allocated', 'method', 'reference', 'status', 'external_ref'),
      form=('party', 'owner', 'received_on', 'amount', 'method', 'reference', 'external_ref', 'note'), number=('receipt', 'RCT'), manage=FIN,
      view=ALL_STAFF, search=('number', 'reference', 'party__name', 'owner__name'), filters=('status', 'method'), subtitle='party', check=receipt_check,
      edit_when=lambda o: o.status == 'draft', defaults=lambda o, r: setattr(o, 'received_on', o.received_on or today()),
      actions=[Act('post', 'Post (allocate to bills)', a_receipt_post, when(('draft',), *FIN), style='primary', fields=(('bills', 'Only these billing records', 'opt-multi:bills', False),)),
               Act('void', 'Void', a_receipt_void, when(('posted',), *FIN), style='danger', fields=(('reason', 'Reason', 'text', True),))],
      help='Money received; posting allocates it to the oldest open billing records.'))
R(Reg('cheque', Cheque, 'Cheque', 'Cheques', 'Finance', cols=('number', 'party', 'lease', 'cheque_no', 'bank', 'amount', 'cheque_date', 'status'),
      form=('lease', 'party', 'bill', 'cheque_no', 'bank', 'amount', 'cheque_date', 'note'), number=('cheque', 'CHQ'), manage=FIN + ('prop_leasing',),
      view=ALL_STAFF, search=('number', 'cheque_no', 'party__name', 'bank'), filters=('status', 'lease'), title='cheque_no', subtitle='party', child_of='lease',
      edit_when=lambda o: o.status == 'received',
      actions=[Act('deposit', 'Deposited', a_cheque_status('deposited', 'Deposited in the bank'), when(('received',), *FIN), fields=(('deposited_on', 'Deposited on', 'date', False),)),
               Act('clear', 'Cleared', a_cheque_status('cleared', 'Cleared'), when(('deposited',), *FIN), style='primary'),
               Act('bounce', 'Bounced', a_cheque_status('bounced', 'Bounced'), when(('deposited',), *FIN), style='danger', fields=(('note', 'Reason from the bank', 'text', True),)),
               Act('replace', 'Replaced', a_cheque_status('replaced', 'Replaced by the tenant'), when(('bounced', 'received'), *FIN), fields=(('note', 'New cheque / payment', 'text', True),))],
      help='Post-dated cheques: in hand → deposited → cleared (a receipt is posted) or bounced.'))
R(Reg('commission', Commission, 'Commission', 'Commissions', 'Finance', cols=('number', 'agent', 'lease', 'sale', 'base', 'percent', 'amount', 'status', 'external_ref'),
      form=('agent', 'lease', 'sale', 'base', 'percent', 'amount', 'note'), number=('commission', 'COM'), manage=('prop_manager', 'prop_commission'),
      view=VIEW + ('prop_finance',), own=own_agent(), search=('number', 'agent__emp_first_name'), filters=('status', 'agent'), subtitle='agent',
      edit_when=lambda o: o.status == 'pending',
      actions=[Act('approve', 'Approve', a_comm('approved', 'Approved'), when(('pending',), 'prop_manager', 'prop_commission'), style='primary'),
               Act('reject', 'Reject', a_comm('rejected', 'Rejected'), when(('pending',), 'prop_manager', 'prop_commission'), style='danger', fields=(('note', 'Reason', 'text', True),)),
               Act('paid', 'Paid', a_comm('paid', 'Paid'), when(('approved',), *FIN), fields=(('external_ref', 'Payment ref.', 'text', True),))],
      help='Agent commissions from leases and sales.'))
R(Reg('moveout', MoveOut, 'Move-out', 'Move-outs', 'Deals', cols=('number', 'lease', 'move_out_on', 'inspection_on', 'deductions', 'refund', 'status'),
      form=('lease', 'move_out_on', 'inspection_on', 'keys_returned'), number=('moveout', 'MO'), manage=LEASING + FIN, view=ALL_STAFF,
      search=('number', 'lease__number', 'lease__party__name'), filters=('status',), subtitle='lease', child_of='lease', edit_when=lambda o: o.status == 'planned',
      info=('deductions', 'deduction_note', 'unpaid', 'refund', 'settled_on', 'mnt_inspection_id'), links={'mnt_inspection_id': 'maintenance.inspections'},
      extra=lambda o, r: {'links': {'inspection': link_label('maintenance.inspections', o.mnt_inspection_id) if o.mnt_inspection_id else ''}},
      actions=[Act('inspected', 'Inspection result', a_moveout_inspected, lambda o, r: o.status in ('planned', 'inspected') and app.any(r, *LEASING),
                   fields=(('deductions', 'Deductions', 'decimal', True), ('deduction_note', 'Details', 'textarea', False))),
               Act('settle', 'Settle deposit', a_moveout_settle, when(('inspected',), *FIN), style='primary', fields=(('keys_returned', 'Keys returned', 'bool', False),)),
               Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), o)[-1], when(('planned',), *LEASING), style='danger')],
      help='Move-out inspection and deposit settlement.'))
R(Reg('statement', OwnerStatement, 'Owner statement', 'Owner statements', 'Finance',
      cols=('number', 'owner', 'period_from', 'period_to', 'collected', 'expenses', 'fees', 'net', 'status'),
      form=('owner', 'period_from', 'period_to', 'external_ref'), number=('statement', 'OS'), manage=FIN, view=MGR + VIEW,
      search=('number', 'owner__name'), filters=('status', 'owner'), subtitle='owner', saver=statement_saver, child_of='owner',
      edit_when=lambda o: o.status == 'draft', extra=lambda o, r: {'lines': o.lines},
      actions=[Act('issue', 'Issue to owner', a_statement_issue, when(('draft',), *FIN), style='primary'),
               Act('paid', 'Paid out', lambda o, r, f: (setattr(o, 'status', 'paid'), setattr(o, 'external_ref', f.s('external_ref')), o.save(),
                                                       app.event('statement', o.pk, f'Paid out – {o.external_ref}', r.user), o)[-1],
                   when(('issued',), *FIN), fields=(('external_ref', 'Payout ref.', 'text', True),))],
      help='Rent collected less maintenance and fees, per owner and period.'))

for key, model, label, plural, cols in (('unit_type', UnitType, 'Unit type', 'Unit types', ('code', 'name', 'category', 'is_active')),
                                        ('charge', ChargeType, 'Charge type', 'Charge types', ('code', 'name', 'kind', 'vat_percent', 'refundable', 'is_active')),
                                        ('checklist', ChecklistItem, 'Checklist item', 'Handover checklist', ('code', 'name', 'applies_to', 'order', 'is_active'))):
    R(Reg(key, model, label, plural, 'Setup', cols=cols + ('review_status',), form=cols, manage=MGR, view=ALL_STAFF, search=('code', 'name'),
          filters=('is_active', 'review_status'), title='name', review=True, deletable=True, files=False))


# ------------------------------------------------------------------ hooks other apps call (only while linked)
def h_unit_info(unit_id):
    u = Unit.objects.filter(pk=unit_id).select_related('building', 'unit_type', 'owner').first()
    if u is None:
        return None
    lease = active_lease(u)
    return {'id': u.pk, 'label': u.label, 'building': u.building.name, 'building_id': u.building_id, 'status': u.status,
            'status_label': u.get_status_display(), 'rent': float(u.rent or 0), 'price': float(u.price or 0), 'bedrooms': u.bedrooms,
            'area': float(u.area) if u.area else None, 'owner': u.owner.name if u.owner_id else '', 'owner_id': u.owner_id,
            'tenant': lease.party.name if lease else '', 'lease_id': lease.pk if lease else None, 'page': PAGE + f'r/unit/{u.pk}'}


def h_reserve(unit_id, kind, contact, amount, opportunity_id, user=None, note='', agent_id=None):
    u = Unit.objects.filter(pk=unit_id).first()
    if u is None:
        raise AppError('The unit no longer exists in Property.')
    p = party_from(dict(contact, kind='buyer' if kind == 'sale' else 'tenant'))
    r = Reservation(number=app.next_number('reservation', 'RSV'), unit=u, party=p, kind=kind, amount=amount or ZERO, reserved_on=today(),
                    crm_opportunity_id=opportunity_id, notes=note, agent_id=agent_id)
    r.expires_on = r.reserved_on + timedelta(days=app.settings().reservation_days or 7)
    res_check(r, None, None)
    r.save()
    set_unit(u, 'reserved', f'reservation {r.number} (from CRM)', user)
    app.event('reservation', r.pk, f'Made from CRM by {user_name(user)}', user)
    app.tell(app.users_with_role('prop_leasing' if kind == 'lease' else 'prop_sales'), f'New reservation {r.number}',
             f'{u.label} for {p.name} until {d(r.expires_on)}', 'reservation', r.pk, r.number, PAGE + f'r/reservation/{r.pk}',
             summary=f'Reservation {r.number}: make the {"lease" if kind == "lease" else "sale contract"}')
    return {'id': r.pk, 'number': r.number, 'party_id': p.pk, 'page': PAGE + f'r/reservation/{r.pk}'}


def h_tenant_of_unit(unit_id):
    u = Unit.objects.filter(pk=unit_id).first()
    lease = u and active_lease(u)
    if not lease:
        return None
    p = lease.party
    return {'lease_id': lease.pk, 'lease': lease.number, 'party_id': p.pk, 'name': p.name, 'email': p.email, 'phone': p.phone}


def h_owner_approval(unit_id):
    """Owner, e-mail and the approval limit for maintenance on this unit (agreement limit, else the Property setting)."""
    u = Unit.objects.filter(pk=unit_id).select_related('owner').first()
    if u is None or not u.owner_id:
        return None
    ag = u.owner.agreements.filter(status='active').filter(Q(units=u) | Q(units__isnull=True)).order_by('-start').first()
    limit = ag.approval_limit if ag and ag.approval_limit is not None else app.settings().maintenance_approval_limit
    return {'owner_id': u.owner_id, 'name': u.owner.name, 'email': u.owner.email, 'limit': float(limit or 0), 'portal': bool(u.owner.portal_user_id)}


def h_recharge(*, unit_id, who, amount, description, work_order_id, number, user=None):
    """Maintenance cost charged on: who = tenant (billing record to the tenant) or owner (owner bill → owner statement)."""
    u = Unit.objects.filter(pk=unit_id).first()
    if u is None or not amount:
        return None
    ch = charge('maintenance')
    if who == 'tenant':
        lease = active_lease(u)
        if lease is None:
            raise AppError('The unit has no active lease to charge the tenant.')
        b = make_bill(party=lease.party, unit=u, lease=lease, ch=ch, amount=Decimal(str(amount)), due=today() + timedelta(days=14),
                      desc=f'Maintenance {number}: {description}', source='maintenance', wo=work_order_id)
    else:
        if not u.owner_id:
            raise AppError('The unit has no owner.')
        b = make_bill(owner=u.owner, unit=u, ch=ch, amount=Decimal(str(amount)), due=today(), desc=f'Maintenance {number}: {description}',
                      source='maintenance', wo=work_order_id)
    app.event('bill', b.pk, f'From Maintenance work order {number}', user)
    return {'id': b.pk, 'number': b.number, 'page': PAGE + f'r/bill/{b.pk}'}


def h_inspection_done(*, kind, unit_id, moveout_id=None, deductions=0, findings=0, note='', user=None):
    u = Unit.objects.filter(pk=unit_id).first()
    if kind == 'move_out' and moveout_id:
        m = MoveOut.objects.filter(pk=moveout_id, status__in=('planned', 'inspected')).first()
        if m:
            moveout_inspected(m, Decimal(str(deductions or 0)), note, user, 'Maintenance inspection')
    elif kind == 'unit_ready' and u and u.status == 'maintenance':
        set_unit(u, 'available', 'unit-ready inspection passed in Maintenance', user)
    return True


def h_unit_blocked(unit_id, blocked, why, user=None):
    """Maintenance: a vacant unit is taken out of / put back into use."""
    u = Unit.objects.filter(pk=unit_id).first()
    if u is None:
        return None
    if blocked and u.status == 'available':
        set_unit(u, 'maintenance', why, user)
    elif not blocked and u.status == 'maintenance':
        set_unit(u, 'available', why, user)
    return u.status


def h_units_of_owner(owner_id):
    return list(Unit.objects.filter(owner_id=owner_id).values_list('pk', flat=True))


app.hooks.update({'unit_info': h_unit_info, 'reserve': h_reserve, 'tenant_of_unit': h_tenant_of_unit, 'owner_approval': h_owner_approval,
                  'recharge': h_recharge, 'inspection_done': h_inspection_done, 'unit_blocked': h_unit_blocked, 'units_of_owner': h_units_of_owner})


# ------------------------------------------------------------------ dashboard, my work, daily
def dashboard(request):
    t = today()
    units = in_branches(Unit.objects.all(), request).all()
    by = dict(units.values_list('status').annotate(n=Count('id')))
    total = units.count() or 1
    let_ = by.get('let', 0) + by.get('notice', 0)
    bills = in_branches(Bill.objects.all(), request).filter(status__in=('issued', 'part_paid'))
    overdue = bills.filter(due_date__lt=t)
    exp = in_branches(Lease.objects.all(), request).filter(status='active', end__range=(t, t + timedelta(days=90)))
    frm, to = dash_period(request)   # v1.22.0 period of the dashboard
    month = in_branches(Bill.objects.all(), request).filter(due_date__range=(frm, to)).exclude(status='void')
    c = cur()
    show_money = app.any(request, *FIN + MGR + VIEW)
    cards = [{'label': 'Units', 'value': units.count(), 'link': 'r/unit'},
             {'label': 'Occupancy', 'value': f'{round(100 * let_ / total)}%', 'hint': f'{let_} let · {by.get("available", 0)} available'},
             {'label': 'Leases ending in 90 days', 'value': exp.count(), 'link': 'r/lease?status=active', 'tone': 'warn' if exp.exists() else 'ok'},
             {'label': 'Active reservations', 'value': in_branches(Reservation.objects.all(), request).filter(status='active').count(), 'link': 'r/reservation?status=active'}]
    if show_money:
        cards += [{'label': f'Overdue ({c})', 'value': f'{float(sum((b.balance for b in overdue), ZERO)):,.0f}', 'tone': 'bad' if overdue.exists() else 'ok',
                   'link': 'r/bill?status=issued'},
                  {'label': f'Billed in period ({c})', 'value': f'{float(month.aggregate(s=Sum("total"))["s"] or 0):,.0f}',
                   'link': f'r/bill?date_field=due_date&from={frm}&to={to}',
                   'hint': f'collected {float(month.aggregate(s=Sum("paid"))["s"] or 0):,.0f}'},
                  {'label': 'Cheques to deposit (7 days)', 'value': in_branches(Cheque.objects.all(), request).filter(status='received', cheque_date__lte=t + timedelta(days=7)).count(),
                   'link': 'r/cheque?status=received'}]
    return {'cards': cards,
            'bars': [{'title': 'Units by status', 'items': [{'label': lbl, 'value': by.get(k, 0)} for k, lbl in Unit._meta.get_field('status').choices]}],
            'lists': [{'title': 'Leases ending soon', 'kind': 'lease', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.unit.label}', 'sub': x.party.name, 'badge': d(x.end)}
                                                                            for x in exp.select_related('unit__building', 'party').order_by('end')[:10]]},
                      {'title': 'Reservations expiring', 'kind': 'reservation', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.unit.label}', 'sub': x.party.name, 'badge': d(x.expires_on)}
                                                                                        for x in in_branches(Reservation.objects.all(), request).filter(status='active').select_related('unit__building', 'party').order_by('expires_on')[:10]]}]}


def my_work(request):
    me = app.my_emp(request)
    out = []
    if app.any(request, *LEASING):
        out.append({'title': 'Draft leases', 'kind': 'lease', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.unit.label}', 'sub': x.party.name, 'badge': 'draft'}
                                                                       for x in in_branches(Lease.objects.all(), request).filter(status='draft').select_related('unit__building', 'party')[:20]]})
    if app.any(request, *FIN):
        out.append({'title': 'Move-outs to settle', 'kind': 'moveout', 'rows': [{'id': x.pk, 'title': x.number, 'sub': x.lease.party.name, 'badge': d(x.move_out_on)}
                                                                                for x in in_branches(MoveOut.objects.all(), request).filter(status='inspected').select_related('lease__party')[:20]]})
    if me is not None:
        out.append({'title': 'My commissions', 'kind': 'commission', 'rows': [{'id': x.pk, 'title': x.number, 'sub': f'{cur()} {x.amount}', 'badge': x.get_status_display()}
                                                                              for x in in_branches(Commission.objects.all(), request).filter(agent=me)[:20]]})
    return out


def daily():
    t = today()
    s = app.settings()
    out = {'reservations_expired': 0, 'renewal_alerts': 0, 'bills_overdue': 0, 'cheques_due': 0}
    for r in Reservation.objects.filter(status='active', expires_on__lt=t):
        end_reservation(r, 'expired', 'Expired (not converted in time)')
        out['reservations_expired'] += 1
    days = sorted(set(int(x) for x in (s.renewal_alert_days or [90, 60, 30, 7])), reverse=True)
    for lease in Lease.objects.filter(status='active', end__gte=t, end__lte=t + timedelta(days=max(days or [90]))).select_related('unit__building', 'party'):
        left = (lease.end - t).days
        due = [x for x in days if left <= x and x not in (lease.renewal_alerts or [])]
        if not due:
            continue
        lease.renewal_alerts = list(lease.renewal_alerts or []) + due
        if not lease.renewal_status:
            lease.renewal_status = 'due'
        lease.save(update_fields=['renewal_alerts', 'renewal_status'])
        app.tell(app.users_with_role('prop_leasing'), f'Lease {lease.number} ends in {left} days', f'{lease.unit.label} – {lease.party.name}, ends {d(lease.end)}',
                 'lease', lease.pk, lease.number, PAGE + f'r/lease/{lease.pk}', summary=f'Renewal: {lease.number} ends {d(lease.end)}')
        if lease.renewal_status in ('due', 'offered') and lease.party.email and min(due) in (90, 60, 30):
            app.send_mail([lease.party.email], 'Your lease renewal', f'Dear {lease.party.name},\n\nYour lease of {lease.unit.label} ends on {d(lease.end)}. '
                          'Please tell us whether you would like to renew – you can reply in the tenant portal.\n')
        call(app, 'ZeoCRM', 'renewal', {'id': lease.pk, 'number': lease.number, 'unit_id': lease.unit_id, 'unit_label': lease.unit.label,
                                        'rent': float(lease.annual_rent), 'end': lease.end, 'party': party_dict(lease.party)})
        out['renewal_alerts'] += 1
    out['bills_overdue'] = Bill.objects.filter(status__in=('issued', 'part_paid'), due_date__lt=t).count()
    due_cheques = Cheque.objects.filter(status='received', cheque_date__lte=t + timedelta(days=3)).count()
    if due_cheques:
        for u in app.users_with_role('prop_finance'):
            app.todo(u, f'{due_cheques} cheque(s) to deposit', 'cheque', 0, 'Cheques', PAGE + 'r/cheque?status=received')
    out['cheques_due'] = due_cheques
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily

from . import complete  # noqa: E402,F401  v1.40.0 complete property management (registers, rules, hooks on top of this module)


# v1.41.0 business partners: partner picker, shared-partner lock and duplicate warning on tenants, owners, brokers, vendors
from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)


# v1.43.0 item master: item picker on charge types
from ZeoMasters.item_exts import attach as _attach_items  # noqa: E402
_attach_items(app)
