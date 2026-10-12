"""ZEO CRM on the app kit: registers, workflow, packs, links (to Property and Maintenance), dashboard, daily jobs."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from zeo.appkit.engine import (in_branches, dash_period, Act, App, AppError, Reg, call, d, emp_name, linked, link_label, local, now, num, today, user_name)
from zeo.appkit.models import REVIEW  # noqa: F401

from .models import (CRMEvent, CRMFile, CRMSequence, CRMSettings, ROLES, Activity, Contact, Listing, LostReason, Offer, Opportunity, Pipeline,
                     Source, Stage, Viewing)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-crm/'

app = App(
    label='ZeoCRM', slug='zeo-crm', title='CRM', settings_model=CRMSettings, sequence_model=CRMSequence, event_model=CRMEvent,
    file_model=CRMFile, roles=ROLES, role_prefix='crm_', manager_role='crm_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'PM': 'property_management'},
    pack_models={'source': (Source, 'code'), 'pipeline': (Pipeline, 'code'), 'stage': (Stage, 'code'), 'lost_reason': (LostReason, 'code')},
    links=(('ZeoProperty', 'Property', 'Pick Property units on opportunities, viewings and offers; reserve the unit from a won deal; '
            'renewal opportunities before leases end; listing status follows the unit.'),
           ('ZeoMaintenance', 'Maintenance', 'Send a customer complaint or service request to Maintenance as a work order.')),
    page=PAGE,
)
app.settings_fields = ('first_contact_hours', 'duplicate_check', 'auto_assign', 'renewal_days', 'currency')

MANAGE = ('crm_manager', 'crm_team_lead')
VIEW = ('crm_viewer',)


def can_work(o, request):
    """Managers / team leads always; an agent on their own records."""
    if app.any(request, *MANAGE):
        return True
    me = app.my_emp(request)
    return me is not None and getattr(o, 'owner_id', None) == me.pk


def is_open(o, *_):
    return o.status == 'open'


# ------------------------------------------------------------------ options
app.option('sources', lambda: Source.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Source)
app.option('pipelines', lambda: Pipeline.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Pipeline)
app.option('stages', lambda: Stage.objects.filter(is_active=True, pipeline__is_active=True).select_related('pipeline'), lambda x: str(x), ('name', 'pipeline__name'), Stage)
app.option('lost-reasons', lambda: LostReason.objects.filter(is_active=True), lambda x: x.name, ('name',), LostReason)
app.option('contacts', lambda: Contact.objects.filter(is_active=True), lambda x: f'{x.name}{" · " + x.company_name if x.company_name else ""} ({x.number})',
           ('name', 'company_name', 'email', 'phone', 'number'), Contact)
app.option('listings', lambda: Listing.objects.exclude(status='withdrawn'), lambda x: f'{x.number} {x.title}', ('title', 'number', 'location'), Listing)
app.option('opportunities', lambda: Opportunity.objects.all(), lambda x: f'{x.number} {x.title}', ('title', 'number', 'contact__name'), Opportunity)
app.provide('crm.contacts', lambda: Contact.objects.filter(is_active=True), lambda x: f'{x.name} ({x.number})', ('name', 'email', 'phone', 'number'))
app.provide('crm.opportunities', lambda: Opportunity.objects.all(), lambda x: f'{x.number} {x.title}', ('title', 'number'))


# ------------------------------------------------------------------ rules
def norm_phone(p):
    return ''.join(c for c in (p or '') if c.isdigit())[-9:]


def duplicates(c):
    """Same e-mail, or the same phone number written another way (+971 50 … / 050 …)."""
    out = []
    if c.email:
        out += list(Contact.objects.filter(email__iexact=c.email).exclude(pk=c.pk)[:5])
    ph = norm_phone(c.phone)
    if len(ph) >= 7:
        out += [x for x in Contact.objects.filter(phone__endswith=ph[-2:]).exclude(pk=c.pk)[:2000] if norm_phone(x.phone) == ph]
    return out


def contact_check(o, request, old):
    s = app.settings()
    force = str(request.data.get('force_duplicate', '')).lower() in ('1', 'true', 'yes')
    if s.duplicate_check and not force and (old is None or old.email != o.email or old.phone != o.phone):
        dup = next(iter(duplicates(o)), None)
        if dup:
            raise AppError(f'{dup.name} ({dup.number}) already has this e-mail or phone. Open that contact, or save again to keep both.',
                           'email', data={'duplicate': {'id': dup.pk, 'number': dup.number, 'name': dup.name}})


def me_as_owner(o, request):
    if not o.owner_id:
        o.owner = app.my_emp(request)


def agents():
    from EmpManagement.models import emp_master
    users = [u for u in app.users_with_role('crm_agent')]
    return list(emp_master.objects.filter(users__in=users).order_by('pk'))


def auto_assign(o):
    s = app.settings()
    if o.owner_id or s.auto_assign != 'round_robin':
        return
    xs = agents()
    if not xs:
        return
    i = s.last_assigned % len(xs)
    o.owner = xs[i]
    s.last_assigned = i + 1
    s.save(update_fields=['last_assigned'])


def opp_defaults(o, request):
    if not o.owner_id and not app.any(request, *MANAGE):
        o.owner = app.my_emp(request)        # an agent's own enquiry


def opp_check(o, request, old):
    if o.stage_id and o.stage.pipeline_id != o.pipeline_id:
        raise AppError('The stage belongs to another pipeline.', 'stage')


def opp_saver(o, request, creating, old):
    if creating:
        if not o.stage_id:
            o.stage = o.pipeline.stages.filter(is_active=True, is_won=False, is_lost=False).order_by('order').first()
        auto_assign(o)
        hrs = app.settings().first_contact_hours or 0
        if hrs:
            o.first_contact_due = now() + timedelta(hours=hrs)
        if not o.value and o.unit_id:
            u = call(app, 'ZeoProperty', 'unit_info', o.unit_id)
            if u:
                o.value = Decimal(str(u.get('rent') if o.interest == 'rent' else u.get('price') or 0) or 0)
    o.save()
    if creating:
        if o.owner_id and o.owner.users_id:
            app.todo(o.owner.users, f'First contact: {o.title}', 'opportunity', o.pk, f'{o.number} {o.title}', PAGE + f'r/opportunity/{o.pk}',
                     f'Contact {o.contact.name} {o.contact.phone or o.contact.email} before {d(o.first_contact_due) or "soon"}.')
    elif old and old.owner_id != o.owner_id and o.owner_id and o.owner.users_id:
        app.todo(o.owner.users, f'Opportunity assigned: {o.title}', 'opportunity', o.pk, f'{o.number} {o.title}', PAGE + f'r/opportunity/{o.pk}')
    return o


def first_contact(opp, when=None):
    if opp and not opp.first_contacted_at:
        opp.first_contacted_at = when or now()
        opp.save(update_fields=['first_contacted_at'])
        app.close_todos('opportunity', opp.pk, 'First contact')


def act_defaults(o, request):
    if not o.owner_id:
        o.owner = app.my_emp(request)
    if o.opportunity_id and not o.contact_id:
        o.contact = o.opportunity.contact


def act_saver(o, request, creating, old):
    if o.status == 'done' and not o.done_at:
        o.done_at = now()
    if o.kind == 'note' and creating:
        o.status, o.done_at = 'done', now()
    o.save()
    if o.status == 'done' and o.opportunity_id and o.kind in ('call', 'email', 'message', 'meeting'):
        first_contact(o.opportunity, o.done_at)
    return o


def viewing_defaults(o, request):
    if not o.agent_id:
        o.agent = o.opportunity.owner or app.my_emp(request)
    if not o.unit_id and o.opportunity.unit_id:
        o.unit_id = o.opportunity.unit_id


def viewing_saver(o, request, creating, old):
    o.save()
    if creating:
        first_contact(o.opportunity)
        if o.agent_id and o.agent.users_id:
            app.todo(o.agent.users, f'Viewing {o.number}', 'viewing', o.pk, f'{o.number} {o.opportunity.contact.name}', PAGE + f'r/viewing/{o.pk}',
                     f'{o.opportunity.contact.name} on {d(o.scheduled_at)}')
        move_to(o.opportunity, 'viewing', request)
    return o


def move_to(opp, word, request):
    """Move an open opportunity forward to the first stage whose code contains the word (e.g. viewing, offer, reserved)."""
    if opp.status != 'open' or not opp.stage_id:
        return
    st_ = opp.pipeline.stages.filter(is_active=True, code__icontains=word.upper(), order__gt=opp.stage.order).order_by('order').first()
    if st_:
        opp.stage = st_
        opp.save(update_fields=['stage'])
        app.event('opportunity', opp.pk, f'Stage → {st_.name}', request.user if request else None)


def offer_defaults(o, request):
    if not o.unit_id and o.opportunity.unit_id:
        o.unit_id = o.opportunity.unit_id
    if not o.listing_id and o.opportunity.listing_id:
        o.listing = o.opportunity.listing
    if o.kind == 'rent' and o.opportunity.interest == 'buy':
        o.kind = 'sale'


def listing_saver(o, request, creating, old):
    if o.unit_id and (creating or (old and old.unit_id != o.unit_id)):
        u = call(app, 'ZeoProperty', 'unit_info', o.unit_id)
        if u:
            o.location = o.location or u.get('building', '')
            o.bedrooms = o.bedrooms if o.bedrooms is not None else u.get('bedrooms')
            o.area = o.area or (Decimal(str(u['area'])) if u.get('area') else None)
            if not o.price:
                o.price = Decimal(str((u.get('rent') if o.kind == 'rent' else u.get('price')) or 0))
            o.status = LISTING_STATUS.get(u.get('status'), o.status)
    o.save()
    return o


LISTING_STATUS = {'available': 'available', 'reserved': 'reserved', 'let': 'let', 'notice': 'available', 'sold': 'sold',
                  'unavailable': 'withdrawn', 'maintenance': 'withdrawn'}


# ------------------------------------------------------------------ actions
def a_move(o, request, f):
    s_ = f.obj('stage', Stage, True, 'Stage')
    if s_.pipeline_id != o.pipeline_id:
        raise AppError('Choose a stage of this pipeline.', 'stage')
    if s_.is_won:
        return a_won(o, request, f)
    if s_.is_lost:
        raise AppError('Use "Lost" to close the opportunity with a reason.', 'stage')
    o.stage = s_
    o.save()
    app.event('opportunity', o.pk, f'Stage → {s_.name}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_won(o, request, f):
    o.status, o.closed_at = 'won', now()
    o.stage = o.pipeline.stages.filter(is_won=True).order_by('order').first() or o.stage
    o.won_ref = o.won_ref or f.s('note')[:60]
    o.save()
    app.event('opportunity', o.pk, 'Won' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('opportunity', o.pk)
    return o


def a_lost(o, request, f):
    o.lost_reason = f.obj('lost_reason', LostReason, True, 'Reason')
    o.lost_note = f.s('note')
    o.status, o.closed_at = 'lost', now()
    o.stage = o.pipeline.stages.filter(is_lost=True).order_by('order').first() or o.stage
    o.save()
    app.event('opportunity', o.pk, f'Lost – {o.lost_reason.name}' + (f': {o.lost_note}' if o.lost_note else ''), request.user)
    app.close_todos('opportunity', o.pk)
    return o


def a_reopen(o, request, f):
    o.status, o.closed_at = 'open', None
    o.stage = o.pipeline.stages.filter(is_active=True, is_won=False, is_lost=False).order_by('order').first()
    o.save()
    app.event('opportunity', o.pk, 'Reopened' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_log(o, request, f):
    kind = f.choice('kind', [(k, v) for k, v in Activity._meta.get_field('kind').choices], 'call')
    a = Activity.objects.create(number=app.next_number('activity', 'ACT'), kind=kind, subject=f.s('subject') or dict(Activity._meta.get_field('kind').choices)[kind],
                                contact=o.contact, opportunity=o, owner=app.my_emp(request), status='done', done_at=now(), outcome=f.s('outcome'))
    app.event('activity', a.pk, f'Logged by {user_name(request.user)}', request.user)
    if kind != 'note' and kind != 'task':
        first_contact(o)
    return o


def can_reserve(o, request):
    if not (o.status == 'open' and bool(o.unit_id) and not o.reservation_id and o.interest in ('rent', 'buy') and linked(app, 'ZeoProperty') and can_work(o, request)):
        return False
    info = call(app, 'ZeoProperty', 'unit_info', unit_id=o.unit_id)   # v1.22.0: only a unit that is still available
    return not info or info.get('status') in ('available', 'notice')


def a_reserve(o, request, f):
    c = o.contact
    res = call(app, 'ZeoProperty', 'reserve', unit_id=o.unit_id, kind='lease' if o.interest == 'rent' else 'sale',
               contact={'name': c.name, 'email': c.email, 'phone': c.phone, 'company': c.company_name, 'crm_contact_id': c.pk,
                        'party_id': c.property_party_id},
               amount=f.n('amount', Decimal('0')), opportunity_id=o.pk, user=request.user, note=f.s('note'), agent_id=o.owner_id)
    if not res:
        raise AppError('Property could not reserve the unit. Check that the unit is available.')
    o.reservation_id = res['id']
    o.save(update_fields=['reservation_id'])
    if res.get('party_id') and not c.property_party_id:
        c.property_party_id = res['party_id']
        c.save(update_fields=['property_party_id'])
    move_to(o, 'reserv', request)
    app.event('opportunity', o.pk, f'Unit reserved in Property: {res["number"]}', request.user)
    return {'message': f'Reservation {res["number"]} made in Property.', 'link': res.get('page', '')}


def a_service(o, request, f):
    c = o if isinstance(o, Contact) else o.contact
    res = call(app, 'ZeoMaintenance', 'request', title=f.s('title') or 'Service request', description=f.s('description'),
               unit_id=getattr(o, 'unit_id', None), requester=c.name, email=c.email, phone=c.phone, source='crm',
               ref=f'CRM {getattr(o, "number", "")}', user=request.user,
               branch_id=getattr(o, 'branch_id', None) or getattr(c, 'branch_id', None))   # v1.22.0: the work order stays in the record's branch
    if not res:
        raise AppError('Maintenance could not take the request.')
    app.event('contact' if isinstance(o, Contact) else 'opportunity', o.pk, f'Sent to Maintenance as {res["number"]}', request.user)
    return {'message': f'Work order {res["number"]} created in Maintenance.'}


def a_viewing_done(o, request, f):
    o.status = 'done'
    o.interest_level = f.choice('interest_level', Viewing_INTEREST, '')
    o.feedback = f.s('feedback')
    o.save()
    app.event('viewing', o.pk, f'Done – interest {o.get_interest_level_display() or "not given"}', request.user)
    app.close_todos('viewing', o.pk)
    return o


def set_status(model_key, status_, text):
    def run(o, request, f):
        o.status = status_
        o.save()
        app.event(model_key, o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos(model_key, o.pk)
        return o
    return run


def a_offer_accept(o, request, f):
    o.status = 'accepted'
    o.save()
    Offer.objects.filter(opportunity=o.opportunity, status__in=('draft', 'sent')).exclude(pk=o.pk).update(status='rejected')
    opp = o.opportunity
    if o.unit_id and not opp.unit_id:
        opp.unit_id = o.unit_id
    opp.value = o.amount
    opp.save()
    app.event('offer', o.pk, 'Accepted by the customer', request.user)
    move_to(opp, 'offer', request)
    return o


def a_activity_done(o, request, f):
    o.status, o.done_at, o.outcome = 'done', now(), f.s('outcome') or o.outcome
    o.save()
    app.event('activity', o.pk, 'Done', request.user)
    if o.opportunity_id and o.kind in ('call', 'email', 'message', 'meeting'):
        first_contact(o.opportunity)
    return o


Viewing_INTEREST = (('high', 'High'), ('medium', 'Medium'), ('low', 'Low'))
KINDS = 'choice:' + '|'.join(f'{k}={v}' for k, v in Activity._meta.get_field('kind').choices)


# ------------------------------------------------------------------ registers
app.reg(Reg('contact', Contact, 'Contact', 'Contacts', 'Customers',
            cols=('number', 'name', 'company_name', 'contact_type', 'phone', 'email', 'owner', 'source'),
            form=('kind', 'name', 'company_name', 'contact_type', 'email', 'phone', 'source', 'owner', 'consent', 'notes', 'is_active',
                  'property_party_id'),
            number=('contact', 'CON'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
            search=('name', 'company_name', 'email', 'phone', 'number'), filters=('contact_type', 'source', 'owner', 'is_active'),
            title='name', subtitle='company_name', defaults=me_as_owner, check=contact_check,
            children=(('Opportunities', 'opportunity', 'contact'), ('Activities', 'activity', 'contact')),
            links={'property_party_id': 'property.parties'},
            actions=[Act('service', 'Send to Maintenance', a_service, lambda o, r: linked(app, 'ZeoMaintenance') and can_work(o, r),
                         fields=(('title', 'Request', 'text', True), ('description', 'Details', 'textarea', False)))],
            help='People and companies you sell to. Duplicate e-mail / phone numbers are flagged.'))

app.reg(Reg('opportunity', Opportunity, 'Opportunity', 'Opportunities', 'Sales',
            cols=('number', 'title', 'contact', 'pipeline', 'stage', 'owner', 'value', 'expected_close', 'status'),
            form=('title', 'contact', 'pipeline', 'stage', 'owner', 'interest', 'unit_id', 'listing', 'value', 'expected_close', 'budget_max',
                  'bedrooms', 'requirements', 'source'),
            readonly=(), number=('opportunity', 'OPP'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
            search=('number', 'title', 'contact__name', 'contact__phone', 'contact__email'), filters=('status', 'pipeline', 'stage', 'owner', 'interest', 'source'),
            title='title', subtitle='contact', defaults=opp_defaults, check=opp_check, saver=opp_saver,
            edit_when=lambda o: o.status == 'open',
            links={'unit_id': 'property.units', 'reservation_id': 'property.reservations', 'renewal_lease_id': 'property.leases'},
            info=('status', 'won_ref', 'lost_reason', 'lost_note', 'reservation_id', 'renewal_lease_id', 'closed_at'),
            children=(('Activities', 'activity', 'opportunity'), ('Viewings', 'viewing', 'opportunity'), ('Offers', 'offer', 'opportunity')),
            actions=[
                Act('log', 'Log a call / message', a_log, lambda o, r: can_work(o, r),
                    fields=(('kind', 'Type', KINDS, True), ('subject', 'Subject', 'text', False), ('outcome', 'Outcome', 'textarea', False))),
                Act('move', 'Move stage', a_move, lambda o, r: is_open(o) and can_work(o, r),
                    fields=(('stage', 'Stage', 'opt:stages', True), ('note', 'Note', 'text', False))),
                Act('reserve', 'Reserve the unit (Property)', a_reserve, can_reserve, style='primary',
                    fields=(('amount', 'Reservation deposit', 'decimal', False), ('note', 'Note', 'text', False))),
                Act('won', 'Won', a_won, lambda o, r: is_open(o) and can_work(o, r), style='primary', fields=(('note', 'Won as (contract / order ref.)', 'text', False),)),
                Act('lost', 'Lost', a_lost, lambda o, r: is_open(o) and can_work(o, r), style='danger',
                    fields=(('lost_reason', 'Reason', 'opt:lost-reasons', True), ('note', 'Note', 'textarea', False))),
                Act('reopen', 'Reopen', a_reopen, lambda o, r: o.status != 'open' and app.any(r, *MANAGE), fields=(('note', 'Why', 'text', False),)),
                Act('service', 'Send to Maintenance', a_service, lambda o, r: linked(app, 'ZeoMaintenance') and can_work(o, r),
                    fields=(('title', 'Request', 'text', True), ('description', 'Details', 'textarea', False))),
            ],
            extra=lambda o, r: {'links': {
                'unit': link_label('property.units', o.unit_id) if o.unit_id else '',
                'reservation': link_label('property.reservations', o.reservation_id) if o.reservation_id else '',
                'renewal_lease': link_label('property.leases', o.renewal_lease_id) if o.renewal_lease_id else '',
                'first_contact_due': d(o.first_contact_due), 'first_contacted_at': d(o.first_contacted_at),
                'late': bool(o.first_contact_due and not o.first_contacted_at and o.first_contact_due < now())}},
            help='Enquiries and deals moving through a pipeline.'))

app.reg(Reg('activity', Activity, 'Activity', 'Activities', 'Sales',
            cols=('number', 'kind', 'subject', 'contact', 'opportunity', 'owner', 'due_at', 'status'),
            form=('kind', 'subject', 'contact', 'opportunity', 'owner', 'due_at', 'status', 'outcome'),
            number=('activity', 'ACT'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
            search=('subject', 'number', 'contact__name'), filters=('kind', 'status', 'owner'), title='subject', subtitle='contact',
            defaults=act_defaults, saver=act_saver, child_of='opportunity',
            actions=[Act('done', 'Mark done', a_activity_done, lambda o, r: o.status == 'open' and can_work(o, r),
                         fields=(('outcome', 'Outcome', 'textarea', False),))],
            help='Calls, e-mails, meetings and tasks.'))

app.reg(Reg('viewing', Viewing, 'Viewing', 'Viewings', 'Sales',
            cols=('number', 'opportunity', 'unit_id', 'listing', 'scheduled_at', 'agent', 'status', 'interest_level'),
            form=('opportunity', 'listing', 'unit_id', 'scheduled_at', 'agent', 'feedback'),
            number=('viewing', 'VW'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('agent', 'emp'),), own_edit=True,
            search=('number', 'opportunity__contact__name', 'opportunity__title'), filters=('status', 'agent'), title='number', subtitle='opportunity',
            defaults=viewing_defaults, saver=viewing_saver, child_of='opportunity', links={'unit_id': 'property.units'},
            edit_when=lambda o: o.status == 'scheduled',
            actions=[Act('done', 'Viewing done', a_viewing_done, lambda o, r: o.status == 'scheduled' and (app.any(r, *MANAGE) or getattr(app.my_emp(r), 'pk', 0) == o.agent_id),
                         fields=(('interest_level', 'Interest', 'choice:high=High|medium=Medium|low=Low', True), ('feedback', 'Feedback', 'textarea', False))),
                     Act('no_show', 'No show', set_status('viewing', 'no_show', 'No show'), lambda o, r: o.status == 'scheduled'),
                     Act('cancel', 'Cancel', set_status('viewing', 'cancelled', 'Cancelled'), lambda o, r: o.status == 'scheduled', style='danger')],
            help='Property / site visits.'))

app.reg(Reg('offer', Offer, 'Offer', 'Offers', 'Sales',
            cols=('number', 'opportunity', 'kind', 'unit_id', 'amount', 'payments', 'valid_until', 'status'),
            form=('opportunity', 'kind', 'listing', 'unit_id', 'amount', 'payments', 'valid_until', 'terms'),
            number=('offer', 'OFR'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('opportunity__owner', 'emp'),),
            search=('number', 'opportunity__title', 'opportunity__contact__name'), filters=('status', 'kind'), title='number', subtitle='opportunity',
            defaults=offer_defaults, child_of='opportunity', links={'unit_id': 'property.units'}, edit_when=lambda o: o.status == 'draft',
            actions=[Act('send', 'Mark sent', set_status('offer', 'sent', 'Sent to the customer'), lambda o, r: o.status == 'draft' and can_work(o.opportunity, r)),
                     Act('accept', 'Accepted', a_offer_accept, lambda o, r: o.status in ('draft', 'sent') and can_work(o.opportunity, r), style='primary'),
                     Act('reject', 'Rejected', set_status('offer', 'rejected', 'Rejected by the customer'),
                         lambda o, r: o.status in ('draft', 'sent') and can_work(o.opportunity, r), fields=(('note', 'Why', 'text', False),))],
            help='Prices offered to the customer.'))

app.reg(Reg('listing', Listing, 'Listing', 'Listings', 'Sales',
            cols=('number', 'title', 'kind', 'unit_id', 'location', 'bedrooms', 'price', 'status'),
            form=('title', 'kind', 'unit_id', 'location', 'bedrooms', 'area', 'price', 'status', 'description'),
            number=('listing', 'LST'), manage=MANAGE, view=VIEW + ('crm_agent',), search=('title', 'number', 'location'), filters=('kind', 'status'),
            title='title', subtitle='location', saver=listing_saver, links={'unit_id': 'property.units'},
            help='What you offer. With ZEO Property, pick the unit – its status follows Property.'))

for key, model, label, plural, cols in (('pipeline', Pipeline, 'Pipeline', 'Pipelines', ('code', 'name', 'kind', 'order', 'is_active')),
                                        ('stage', Stage, 'Stage', 'Stages', ('pipeline', 'code', 'name', 'order', 'probability', 'is_won', 'is_lost', 'is_active')),
                                        ('source', Source, 'Source', 'Sources', ('code', 'name', 'is_active')),
                                        ('lost_reason', LostReason, 'Lost reason', 'Lost reasons', ('code', 'name', 'is_active'))):
    app.reg(Reg(key, model, label, plural, 'Setup', cols=cols + ('review_status',), form=cols, manage=('crm_manager',),
                view=MANAGE + VIEW + ('crm_agent',), search=('code', 'name'), filters=('is_active', 'review_status'),
                title='name', review=True, deletable=True, files=False,
                children=(('Stages', 'stage', 'pipeline'),) if key == 'pipeline' else ()))


# ------------------------------------------------------------------ hooks other apps call (only while linked)
def h_deal_won(opportunity_id, ref, user=None):
    o = Opportunity.objects.filter(pk=opportunity_id).first()
    if o is None or o.status == 'won':
        return None
    o.status, o.closed_at, o.won_ref = 'won', now(), ref[:60]
    o.stage = o.pipeline.stages.filter(is_won=True).first() or o.stage
    o.save()
    app.event('opportunity', o.pk, f'Won – {ref} (from Property)', user)
    app.close_todos('opportunity', o.pk)
    return o.pk


def h_deal_reopen(opportunity_id, reason, user=None):
    o = Opportunity.objects.filter(pk=opportunity_id).first()
    if o is None:
        return None
    o.reservation_id = None
    o.save(update_fields=['reservation_id'])
    app.event('opportunity', o.pk, f'Reservation ended in Property: {reason}', user)
    if o.owner_id and o.owner.users_id:
        app.todo(o.owner.users, f'Reservation ended: {o.title}', 'opportunity', o.pk, f'{o.number} {o.title}', PAGE + f'r/opportunity/{o.pk}', reason)
    return o.pk


def contact_for(p):
    c = None
    if p.get('crm_contact_id'):
        c = Contact.objects.filter(pk=p['crm_contact_id']).first()
    if c is None and p.get('party_id'):
        c = Contact.objects.filter(property_party_id=p['party_id']).first()
    if c is None and p.get('email'):
        c = Contact.objects.filter(email__iexact=p['email']).first()
    if c is None:
        c = Contact.objects.create(number=app.next_number('contact', 'CON'), name=p.get('name') or 'Customer', email=p.get('email') or '',
                                   phone=p.get('phone') or '', contact_type=p.get('type') or 'tenant', property_party_id=p.get('party_id'))
        app.event('contact', c.pk, 'Created from Property', None)
    elif p.get('party_id') and not c.property_party_id:
        c.property_party_id = p['party_id']
        c.save(update_fields=['property_party_id'])
    return c


def h_renewal(lease):
    """Property: a lease ends soon – open a renewal opportunity (once per lease)."""
    if Opportunity.objects.filter(renewal_lease_id=lease['id']).exists():
        return None
    pipe = Pipeline.objects.filter(kind='renewal', is_active=True).first() or Pipeline.objects.filter(is_active=True).first()
    if pipe is None:
        return None
    c = contact_for(lease['party'])
    o = Opportunity(number=app.next_number('opportunity', 'OPP'), title=f'Renewal {lease["number"]} – {lease["unit_label"]}', contact=c,
                    pipeline=pipe, interest='rent', unit_id=lease['unit_id'], value=Decimal(str(lease.get('rent') or 0)),
                    expected_close=lease.get('end'), renewal_lease_id=lease['id'], owner=c.owner)
    o.stage = pipe.stages.filter(is_active=True, is_won=False, is_lost=False).order_by('order').first()
    auto_assign(o)
    o.save()
    app.event('opportunity', o.pk, f'Opened by Property: lease {lease["number"]} ends {lease.get("end")}', None)
    if o.owner_id and o.owner.users_id:
        app.todo(o.owner.users, f'Renewal: {lease["number"]}', 'opportunity', o.pk, f'{o.number} {o.title}', PAGE + f'r/opportunity/{o.pk}')
    return {'id': o.pk, 'number': o.number}


def h_renewal_closed(lease_id, renewed, ref='', user=None):
    o = Opportunity.objects.filter(renewal_lease_id=lease_id, status='open').first()
    if o is None:
        return None
    o.status, o.closed_at = ('won' if renewed else 'lost'), now()
    o.won_ref = ref[:60] if renewed else ''
    o.stage = o.pipeline.stages.filter(**({'is_won': True} if renewed else {'is_lost': True})).first() or o.stage
    o.save()
    app.event('opportunity', o.pk, ('Renewed – ' + ref) if renewed else 'Tenant is not renewing (from Property)', user)
    return o.pk


def h_unit_status(unit_id, status_):
    n = 0
    new = LISTING_STATUS.get(status_)
    if not new:
        return 0
    for x in Listing.objects.filter(unit_id=unit_id).exclude(status=new):
        x.status = new
        x.save(update_fields=['status'])
        app.event('listing', x.pk, f'Status {x.get_status_display()} (unit in Property)', None)
        n += 1
    return n


def h_contact_info(contact_id):
    c = Contact.objects.filter(pk=contact_id).first()
    return c and {'id': c.pk, 'number': c.number, 'name': c.name, 'email': c.email, 'phone': c.phone, 'page': PAGE + f'r/contact/{c.pk}'}


def h_party_saved(party):
    """Property made / changed a tenant or buyer – keep one CRM contact for that person."""
    return contact_for(party).pk


def h_contact_summary(contact_id):
    c = Contact.objects.filter(pk=contact_id).first()
    if c is None:
        return None
    return {'number': c.number, 'name': c.name, 'opportunities': [{'number': o.number, 'title': o.title, 'status': o.get_status_display()}
                                                                    for o in c.opportunities.all()[:10]],
            'page': PAGE + f'r/contact/{c.pk}'}


app.hooks.update({'deal_won': h_deal_won, 'deal_reopen': h_deal_reopen, 'renewal': h_renewal, 'renewal_closed': h_renewal_closed,
                  'unit_status': h_unit_status, 'contact_info': h_contact_info, 'party_saved': h_party_saved, 'contact_summary': h_contact_summary})


# ------------------------------------------------------------------ dashboard, my work, daily
def dashboard(request):
    reg = app.regs['opportunity']
    opps = reg.scope(request)
    t = today()
    frm, to = dash_period(request)   # v1.22.0 period of the dashboard
    open_ = opps.filter(status='open')
    month = opps.filter(closed_at__date__gte=frm, closed_at__date__lte=to)
    pq = f'&date_field=closed_at&from={frm}&to={to}'
    late = open_.filter(first_contact_due__lt=now(), first_contacted_at__isnull=True)
    acts = app.regs['activity'].scope(request).filter(status='open')
    views = app.regs['viewing'].scope(request).filter(status='scheduled', scheduled_at__date__gte=t, scheduled_at__date__lte=t + timedelta(days=7))
    cur = app.settings().currency
    funnel = []
    for p in in_branches(Pipeline.objects.all(), request).filter(is_active=True):
        items = [{'label': s_.name, 'value': open_.filter(stage=s_).count()} for s_ in p.stages.filter(is_active=True, is_won=False, is_lost=False)]
        if any(i['value'] for i in items) or p.order == 1:
            funnel.append({'title': f'{p.name} pipeline (open)', 'items': items})
    lost = month.filter(status='lost').values('lost_reason__name').annotate(n=Count('id')).order_by('-n')
    return {
        'cards': [
            {'label': 'Open opportunities', 'value': open_.count(), 'link': 'r/opportunity?status=open'},
            {'label': f'Open value ({cur})', 'value': f"{float(open_.aggregate(s=Sum('value'))['s'] or 0):,.0f}"},
            {'label': 'Won in period', 'value': month.filter(status='won').count(), 'link': 'r/opportunity?status=won' + pq, 'hint': f'{cur} {float(month.filter(status="won").aggregate(s=Sum("value"))["s"] or 0):,.0f}'},
            {'label': 'Lost in period', 'value': month.filter(status='lost').count(), 'link': 'r/opportunity?status=lost' + pq},
            {'label': 'New in period', 'value': opps.filter(created_at__date__gte=frm, created_at__date__lte=to).count(),
             'link': f'r/opportunity?date_field=created_at&from={frm}&to={to}'},
            {'label': 'Late first contact', 'value': late.count(), 'tone': 'bad' if late.exists() else 'ok', 'link': 'r/opportunity?status=open'},
            {'label': 'Activities to do', 'value': acts.count(), 'link': 'r/activity?status=open'},
            {'label': 'Viewings next 7 days', 'value': views.count(), 'link': 'r/viewing?status=scheduled'},
        ],
        'bars': funnel + ([{'title': 'Lost in period – reasons', 'items': [{'label': x['lost_reason__name'] or 'No reason', 'value': x['n']} for x in lost]}] if lost else []),
        'lists': [
            {'title': 'First contact overdue', 'kind': 'opportunity', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.title}', 'sub': f'{o.contact.name} · {emp_name(o.owner) or "unassigned"}', 'badge': 'late'} for o in late.select_related('contact', 'owner')[:10]]},
            {'title': 'Upcoming viewings', 'kind': 'viewing', 'rows': [{'id': v.pk, 'title': f'{v.number} {v.opportunity.contact.name}', 'sub': local(v.scheduled_at).strftime('%d %b %Y, %H:%M'), 'badge': emp_name(v.agent)} for v in views.select_related('opportunity__contact', 'agent').order_by('scheduled_at')[:10]]},
        ],
    }


def my_work(request):
    me = app.my_emp(request)
    if me is None:
        return []
    acts = in_branches(Activity.objects.all(), request).filter(owner=me, status='open').order_by('due_at')[:30]
    opps = in_branches(Opportunity.objects.all(), request).filter(owner=me, status='open').select_related('contact', 'stage')[:30]
    return [{'title': 'My activities', 'kind': 'activity', 'rows': [{'id': a.pk, 'title': a.subject, 'sub': a.get_kind_display(), 'badge': (d(a.due_at) or '')[:10]} for a in acts]},
            {'title': 'My opportunities', 'kind': 'opportunity', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.title}', 'sub': o.contact.name, 'badge': o.stage.name if o.stage else ''} for o in opps]}]


def daily():
    out = {'late_first_contact': 0, 'offers_expired': 0}
    leads = app.users_with_role('crm_team_lead')
    for o in Opportunity.objects.filter(status='open', first_contact_due__lt=now(), first_contacted_at__isnull=True).select_related('contact'):
        for u in leads:
            app.todo(u, f'Late first contact: {o.title}', 'opportunity', o.pk, f'{o.number} {o.title}', PAGE + f'r/opportunity/{o.pk}')
        out['late_first_contact'] += 1
    for x in Offer.objects.filter(status__in=('draft', 'sent'), valid_until__lt=today()):
        x.status = 'expired'
        x.save(update_fields=['status'])
        app.event('offer', x.pk, 'Expired (valid-until date passed)', None)
        out['offers_expired'] += 1
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily
