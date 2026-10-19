"""v1.33.0 – CRM sales management on the app kit.

Part 1  leads (capture → score → qualify → convert), accounts, merge duplicates, assignment rules, territories, campaigns,
        competitors, recurring activities.
Part 2  products / price books / tax codes / payment terms (ZeoMasters), quotations with lines and discount approval,
        quotation PDF, sales orders, sales targets, weighted forecast.

Hooks for the later Sales / Inventory apps: `quote_accepted`, `order_confirmed`, `order_status` (see README 48.9).
"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import F as models_F, Q, Sum

from zeo.appkit.engine import Act, AppError, Reg, add_months, d, emp_name, now, today, user_name

from .models import (INTEREST, Activity, AssignmentRule, Campaign, Competitor, Contact, Lead, LeadScoreRule, LostReason, Opportunity, Pipeline, Quotation,
                     QuotationLine, SalesOrder, SalesOrderLine, SalesTarget, Source, Territory)
from ZeoMasters.models import UOM, Account, PaymentTerm, PriceBook, PriceBookItem, Product, ProductCategory, TaxCode

Q2 = Decimal('0.01')


def _d(v, default='0'):
    try:
        return Decimal(str(v if v not in (None, '') else default))
    except Exception:
        return Decimal(default)


def setup(app, MANAGE, VIEW, can_work, PAGE, auto_assign, first_contact, norm_phone):
    """Registers, actions, options and hooks of the sales management, added to the CRM app."""
    from . import app as crm   # noqa: F401  (the module that owns `app`)

    SETUP_VIEW = MANAGE + VIEW + ('crm_agent',)

    # ------------------------------------------------------------------ options (pickers)
    app.option('accounts', lambda: Account.objects.filter(is_active=True), lambda x: f'{x.name} ({x.number})', ('name', 'number', 'tax_id', 'email', 'phone'), Account)
    app.option('products', lambda: Product.objects.filter(is_active=True).select_related('uom', 'tax'),
               lambda x: f'{x.code + " · " if x.code else ""}{x.name}', ('name', 'code', 'barcode'), Product)
    app.option('product-categories', lambda: ProductCategory.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), ProductCategory)
    app.option('uoms', lambda: UOM.objects.filter(is_active=True), lambda x: f'{x.name} ({x.code})', ('name', 'code'), UOM)
    app.option('tax-codes', lambda: TaxCode.objects.filter(is_active=True), lambda x: str(x), ('name', 'code'), TaxCode)
    app.option('payment-terms', lambda: PaymentTerm.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), PaymentTerm)
    app.option('price-books', lambda: PriceBook.objects.filter(is_active=True), lambda x: f'{x.name} ({x.currency})', ('name', 'code'), PriceBook)
    app.option('territories', lambda: Territory.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Territory)
    app.option('campaigns', lambda: Campaign.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Campaign)
    app.option('competitors', lambda: Competitor.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Competitor)
    app.option('leads', lambda: Lead.objects.exclude(status__in=('converted', 'disqualified')), lambda x: f'{x.number} {x.name}', ('name', 'number', 'email', 'phone', 'company_name'), Lead)
    app.option('quotations', lambda: Quotation.objects.all(), lambda x: f'{x.number} {x.account.name if x.account_id else (x.contact.name if x.contact_id else "")}', ('number', 'account__name', 'contact__name'), Quotation)
    app.option('sales-orders', lambda: SalesOrder.objects.all(), lambda x: x.number, ('number', 'account__name'), SalesOrder)
    app.provide('crm.accounts', lambda: Account.objects.filter(is_active=True), lambda x: f'{x.name} ({x.number})', ('name', 'number'))
    app.provide('crm.products', lambda: Product.objects.filter(is_active=True), lambda x: f'{x.code + " · " if x.code else ""}{x.name}', ('name', 'code'))
    app.provide('crm.quotations', lambda: Quotation.objects.filter(status__in=('accepted', 'sent', 'approved')), lambda x: x.number, ('number',))
    app.provide('crm.sales_orders', lambda: SalesOrder.objects.exclude(status='cancelled'), lambda x: x.number, ('number',))

    # ------------------------------------------------------------------ lead scoring and assignment
    def score_of(lead):
        pts = 0
        done = None
        for r in LeadScoreRule.objects.filter(is_active=True):
            w, v = r.when, (r.value or '').strip().lower()
            hit = False
            if w == 'has_email':
                hit = bool(lead.email)
            elif w == 'has_phone':
                hit = len(norm_phone(lead.phone)) >= 7
            elif w == 'has_company':
                hit = bool(lead.company_name)
            elif w == 'source':
                hit = bool(lead.source_id and lead.source.code.lower() == v)
            elif w == 'campaign':
                hit = bool(lead.campaign_id and lead.campaign.code.lower() == v)
            elif w == 'value_min':
                hit = _d(lead.value) >= _d(v or '0')
            elif w == 'keyword':
                hit = bool(v) and v in f'{lead.requirements} {lead.interest}'.lower()
            elif w == 'city':
                hit = bool(v) and v in (lead.city or '').lower()
            elif w == 'job_title':
                hit = bool(v) and v in (lead.job_title or '').lower()
            elif w == 'activity':
                if done is None:
                    done = lead.pk and lead.activities.filter(status='done', kind__in=('call', 'meeting', 'message', 'email')).exists()
                hit = bool(done)
            # v1.33.1 property
            elif w == 'has_budget':
                hit = bool(lead.budget_max) or _d(lead.value) > 0
            elif w == 'has_bedrooms':
                hit = lead.bedrooms is not None
            elif w == 'has_area':
                hit = bool(lead.area)
            elif w == 'area':
                hit = bool(v) and v in (lead.area or '').lower()
            elif w == 'interest':
                hit = bool(v) and lead.interest_kind == v
            elif w == 'meeting':
                hit = bool(lead.pk and lead.activities.filter(status='done', kind='meeting').exists())
            if hit:
                pts += r.points
        return max(-99, min(999, pts))

    def rating_of(score):
        s = app.settings()
        return 'hot' if score >= s.qualify_score else 'warm' if score >= s.qualify_score // 2 else 'cold'

    def territory_for(city, country='', area=''):
        txt = f'{city} {country} {area}'.lower()   # v1.33.1: a community / building name in the areas list matches too
        if not txt.strip():
            return None
        for t in Territory.objects.filter(is_active=True):
            if any(a and a in txt for a in t.area_list()):
                return t
        return None

    def rule_owner(obj):
        """First matching assignment rule → employee (or a round robin inside the rule's territory)."""
        s = app.settings()
        for r in AssignmentRule.objects.filter(is_active=True).select_related('territory'):
            if r.source_id and r.source_id != getattr(obj, 'source_id', None):
                continue
            if r.territory_id and r.territory_id != getattr(obj, 'territory_id', None):
                continue
            if r.campaign_id and r.campaign_id != getattr(obj, 'campaign_id', None):
                continue
            if r.min_value is not None and _d(getattr(obj, 'value', 0)) < r.min_value:
                continue
            if r.keyword and r.keyword.lower() not in f'{getattr(obj, "requirements", "")} {getattr(obj, "interest", "")} {getattr(obj, "title", "")}'.lower():
                continue
            if r.area and r.area.lower() not in f'{getattr(obj, "area", "")} {getattr(obj, "city", "")}'.lower():   # v1.33.1
                continue
            if r.interest and r.interest != (getattr(obj, 'interest_kind', None) or getattr(obj, 'interest', None)):
                continue
            if r.assign_to_id:
                return r.assign_to
            if r.round_robin_territory and r.territory_id:
                xs = list(r.territory.members.all().order_by('pk'))
                if xs:
                    i = s.last_assigned % len(xs)
                    s.last_assigned = i + 1
                    s.save(update_fields=['last_assigned'])
                    return xs[i]
        return None

    def assign_lead(lead):
        s = app.settings()
        if lead.owner_id or s.assign_leads == 'none':
            return
        if s.assign_leads == 'rules':
            lead.owner = rule_owner(lead)
        if not lead.owner_id:
            auto_assign(lead)   # round robin of the settings (same as opportunities)

    def lead_defaults(o, request):
        if not o.territory_id:
            o.territory = territory_for(o.city, o.country, o.area)
        if not o.owner_id and not app.any(request, *MANAGE):
            o.owner = app.my_emp(request)
        if not o.value and o.budget_max:
            o.value = o.budget_max

    def lead_check(o, request, old):
        s = app.settings()
        force = str(request.data.get('force_duplicate', '')).lower() in ('1', 'true', 'yes')
        if s.duplicate_check and not force and (old is None or old.email != o.email or old.phone != o.phone):
            dup = None
            if o.email:
                dup = Contact.objects.filter(email__iexact=o.email, is_active=True).first() or Lead.objects.filter(email__iexact=o.email).exclude(pk=o.pk).exclude(status='disqualified').first()
            if dup is None and len(norm_phone(o.phone)) >= 7:
                ph = norm_phone(o.phone)
                dup = next((x for x in Lead.objects.filter(phone__endswith=ph[-2:]).exclude(pk=o.pk)[:2000] if norm_phone(x.phone) == ph), None)
            if dup is not None:
                raise AppError(f'{dup.name} ({dup.number}) already has this e-mail or phone ({"contact" if isinstance(dup, Contact) else "lead"}). Open it, or save again to keep both.',
                               'email', data={'duplicate': {'id': dup.pk, 'number': dup.number, 'name': dup.name, 'kind': 'contact' if isinstance(dup, Contact) else 'lead'}})

    def lead_saver(o, request, creating, old):
        o.score = score_of(o)
        if not o.rating or (old and old.score != o.score):
            o.rating = rating_of(o.score)
        if creating:
            assign_lead(o)
            hrs = app.settings().first_contact_hours or 0
            if hrs:
                o.first_contact_due = now() + timedelta(hours=hrs)
        if o.status == 'new' and o.score >= app.settings().qualify_score:
            o.status = 'qualified'
        o.save()
        if creating and o.owner_id and o.owner.users_id:
            app.todo(o.owner.users, f'New lead: {o.name}', 'lead', o.pk, f'{o.number} {o.name}', PAGE + f'r/lead/{o.pk}',
                     f'Contact {o.name} {o.phone or o.email} before {d(o.first_contact_due) or "soon"}.')
        elif old and old.owner_id != o.owner_id and o.owner_id and o.owner.users_id:
            app.todo(o.owner.users, f'Lead assigned: {o.name}', 'lead', o.pk, f'{o.number} {o.name}', PAGE + f'r/lead/{o.pk}')
        return o

    def lead_touch(lead, when=None):
        if lead and not lead.first_contacted_at:
            lead.first_contacted_at = when or now()
            if lead.status == 'new':
                lead.status = 'working'
            lead.save(update_fields=['first_contacted_at', 'status'])
            app.close_todos('lead', lead.pk, 'New lead')

    # ------------------------------------------------------------------ lead actions
    def a_lead_log(o, request, f):
        kind = f.choice('kind', [(k, v) for k, v in Activity._meta.get_field('kind').choices], 'call')
        a = Activity.objects.create(number=app.next_number('activity', 'ACT'), kind=kind, subject=f.s('subject') or dict(Activity._meta.get_field('kind').choices)[kind],
                                    lead=o, owner=app.my_emp(request), status='done', done_at=now(), outcome=f.s('outcome'), branch_id=o.branch_id)
        app.event('activity', a.pk, f'Logged by {user_name(request.user)}', request.user)
        if kind not in ('note', 'task'):
            lead_touch(o)
        o.score = score_of(o)
        o.rating = rating_of(o.score)
        o.save(update_fields=['score', 'rating'])
        return o

    def a_lead_qualify(o, request, f):
        o.status = 'qualified'
        o.rating = f.choice('rating', (('hot', 'Hot'), ('warm', 'Warm'), ('cold', 'Cold')), o.rating or 'warm')
        o.save()
        app.event('lead', o.pk, 'Qualified' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_lead_nurture(o, request, f):
        o.status = 'nurture'
        o.save()
        app.event('lead', o.pk, 'Nurturing' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_lead_disqualify(o, request, f):
        o.disqualify_reason = f.obj('reason', LostReason, True, 'Reason')
        o.status = 'disqualified'
        o.save()
        app.event('lead', o.pk, f'Disqualified – {o.disqualify_reason.name}' + (f': {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos('lead', o.pk)
        return o

    def a_lead_convert(o, request, f):
        """Contact (reuse by e-mail) + account (when a company is given; reuse by name / tax id) + opportunity in the chosen pipeline."""
        pipe = f.obj('pipeline', Pipeline, False, 'Pipeline') or Pipeline.objects.filter(is_active=True).order_by('order').first()
        if pipe is None:
            raise AppError('Set up a pipeline first (Setup → Pipelines and stages).', 'pipeline')
        acc = f.obj('account', Account, False, 'Account')
        if acc is None and o.company_name and not f.b('no_account'):
            acc = Account.objects.filter(name__iexact=o.company_name, is_active=True).first()
            if acc is None:
                acc = Account.objects.create(number=app.next_number('account', 'ACC'), name=o.company_name, kind='prospect', city=o.city, country=o.country,
                                             phone=o.phone, email=o.email, owner=o.owner, branch_id=o.branch_id)
                app.event('account', acc.pk, f'Created from lead {o.number}', request.user)
        c = Contact.objects.filter(email__iexact=o.email, is_active=True).first() if o.email else None
        if c is None:
            c = Contact.objects.create(number=app.next_number('contact', 'CON'), name=o.name, company_name=o.company_name, email=o.email, phone=o.phone,
                                       source=o.source, owner=o.owner, contact_type='prospect', consent=o.consent, account=acc, job_title=o.job_title,
                                       territory=o.territory, branch_id=o.branch_id, notes=o.requirements)
            app.event('contact', c.pk, f'Created from lead {o.number}', request.user)
        elif acc and not c.account_id:
            c.account = acc
            c.save(update_fields=['account'])
        opp = None
        if not f.b('contact_only'):
            opp = Opportunity(number=app.next_number('opportunity', 'OPP'), title=f.s('title') or (o.interest or f'{o.company_name or o.name} – enquiry')[:150],
                              contact=c, account=acc, pipeline=pipe, owner=o.owner, value=o.value, source=o.source, campaign=o.campaign, territory=o.territory,
                              requirements=o.requirements, interest=(o.interest_kind if o.interest_kind != 'other' else ('service' if pipe.kind == 'service' else 'other')),
                              lead=o, branch_id=o.branch_id, expected_close=f.dt('expected_close'), budget_max=o.budget_max, bedrooms=o.bedrooms, unit_id=o.unit_id)
            opp.stage = pipe.stages.filter(is_active=True, is_won=False, is_lost=False).order_by('order').first()
            opp.first_contacted_at = o.first_contacted_at
            opp.save()
            app.event('opportunity', opp.pk, f'Converted from lead {o.number}', request.user)
            o.activities.update(opportunity=opp, contact=c)
        o.contact, o.account, o.opportunity = c, acc, opp
        o.status, o.converted_at = 'converted', now()
        o.save()
        app.event('lead', o.pk, f'Converted → {c.number}' + (f', {acc.number}' if acc else '') + (f', {opp.number}' if opp else ''), request.user)
        app.close_todos('lead', o.pk)
        if opp is not None:
            return {'message': f'Converted: contact {c.number}, opportunity {opp.number}.', 'created': {'app': 'zeo-crm', 'kind': 'opportunity', 'id': opp.pk}}
        return {'message': f'Converted: contact {c.number}.', 'created': {'app': 'zeo-crm', 'kind': 'contact', 'id': c.pk}}

    def a_lead_assign(o, request, f):
        o.owner = f.emp('owner') or o.owner
        if o.owner_id is None:
            raise AppError('Choose an agent.', 'owner')
        o.save(update_fields=['owner'])
        app.event('lead', o.pk, f'Assigned to {emp_name(o.owner)}', request.user)
        if o.owner.users_id:
            app.todo(o.owner.users, f'Lead assigned: {o.name}', 'lead', o.pk, f'{o.number} {o.name}', PAGE + f'r/lead/{o.pk}')
        return o

    def a_rescore(o, request, f):
        o.score = score_of(o)
        o.rating = rating_of(o.score)
        o.save(update_fields=['score', 'rating'])
        app.event('lead', o.pk, f'Score recalculated: {o.score} ({o.rating})', request.user)
        return o

    def lead_open(o, *_):
        return o.status not in ('converted', 'disqualified')

    LEAD_KINDS = 'choice:' + '|'.join(f'{k}={v}' for k, v in Activity._meta.get_field('kind').choices)
    app.reg(Reg('lead', Lead, 'Lead', 'Leads', 'Sales',
                cols=('number', 'name', 'company_name', 'phone', 'email', 'source', 'owner', 'territory', 'score', 'rating', 'status'),
                form=('name', 'company_name', 'job_title', 'email', 'phone', 'city', 'area', 'country', 'source', 'campaign', 'channel', 'territory', 'owner',
                      'interest_kind', 'interest', 'unit_id', 'budget_max', 'bedrooms', 'requirements', 'value', 'consent'),
                links={'unit_id': 'property.units'},
                number=('lead', 'LD'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
                search=('number', 'name', 'company_name', 'email', 'phone', 'city', 'area'), filters=('status', 'rating', 'source', 'campaign', 'territory', 'owner', 'interest_kind'),
                title='name', subtitle='company_name', defaults=lead_defaults, check=lead_check, saver=lead_saver, edit_when=lead_open,
                info=('score', 'rating', 'status', 'contact', 'account', 'opportunity', 'disqualify_reason', 'converted_at'),
                children=(('Activities', 'activity', 'lead'),),
                actions=[
                    Act('log', 'Log a call / message', a_lead_log, lambda o, r: lead_open(o) and can_work(o, r),
                        fields=(('kind', 'Type', LEAD_KINDS, True), ('subject', 'Subject', 'text', False), ('outcome', 'Outcome', 'textarea', False))),
                    Act('qualify', 'Qualify', a_lead_qualify, lambda o, r: o.status in ('new', 'working', 'nurture') and can_work(o, r),
                        fields=(('rating', 'Rating', 'choice:hot=Hot|warm=Warm|cold=Cold', True), ('note', 'Note', 'text', False))),
                    Act('convert', 'Convert to contact + opportunity', a_lead_convert, lambda o, r: lead_open(o) and can_work(o, r), style='primary',
                        fields=(('pipeline', 'Pipeline', 'opt:pipelines', False), ('title', 'Opportunity title', 'text', False), ('expected_close', 'Expected close', 'date', False),
                                ('account', 'Existing account (else made from the company name)', 'opt:accounts', False), ('no_account', 'Do not make an account', 'bool', False),
                                ('contact_only', 'Contact only – no opportunity', 'bool', False))),
                    Act('nurture', 'Nurture (not now)', a_lead_nurture, lambda o, r: o.status in ('new', 'working', 'qualified') and can_work(o, r), fields=(('note', 'Note', 'text', False),)),
                    Act('assign', 'Assign', a_lead_assign, lambda o, r: lead_open(o) and app.any(r, *MANAGE), fields=(('owner', 'Agent', 'opt:employees', True),)),
                    Act('rescore', 'Recalculate score', a_rescore, lambda o, r: lead_open(o) and can_work(o, r)),
                    Act('disqualify', 'Disqualify', a_lead_disqualify, lambda o, r: lead_open(o) and can_work(o, r), style='danger',
                        fields=(('reason', 'Reason', 'opt:lost-reasons', True), ('note', 'Note', 'textarea', False))),
                ],
                extra=lambda o, r: {'links': {'first_contact_due': d(o.first_contact_due), 'first_contacted_at': d(o.first_contacted_at),
                                              'late': bool(o.first_contact_due and not o.first_contacted_at and o.first_contact_due < now())}},
                help='Enquiries that are not qualified yet. Score them, qualify, then convert to a contact, an account and an opportunity.'))

    # ------------------------------------------------------------------ contacts: merge
    def a_merge(o, request, f):
        into = f.obj('into', Contact, True, 'Keep contact')
        if into.pk == o.pk:
            raise AppError('Choose another contact to merge into.', 'into')
        n = 0
        n += Opportunity.objects.filter(contact=o).update(contact=into)
        n += Activity.objects.filter(contact=o).update(contact=into)
        n += Quotation.objects.filter(contact=o).update(contact=into)
        n += SalesOrder.objects.filter(contact=o).update(contact=into)
        n += Lead.objects.filter(contact=o).update(contact=into)
        for fld in ('email', 'phone', 'company_name', 'job_title', 'notes'):
            if not getattr(into, fld) and getattr(o, fld):
                setattr(into, fld, getattr(o, fld))
        if not into.account_id and o.account_id:
            into.account = o.account
        into.tags = list(dict.fromkeys((into.tags or []) + (o.tags or [])))
        into.save()
        o.is_active, o.merged_into = False, into
        o.save(update_fields=['is_active', 'merged_into'])
        app.event('contact', o.pk, f'Merged into {into.number} {into.name} ({n} records moved)', request.user)
        app.event('contact', into.pk, f'{o.number} {o.name} merged into this contact ({n} records moved)', request.user)
        return {'message': f'Merged into {into.number}. {n} records moved.', 'created': {'app': 'zeo-crm', 'kind': 'contact', 'id': into.pk}}

    creg = app.regs['contact']
    creg.form = tuple(creg.form[:4]) + ('account', 'job_title', 'role') + tuple(creg.form[4:]) + ('territory', 'tags')
    creg.cols = ('number', 'name', 'account', 'job_title', 'contact_type', 'phone', 'email', 'owner', 'source', 'branch')
    creg.filters = tuple(creg.filters) + ('account', 'role', 'territory')
    creg.search = tuple(creg.search) + ('account__name', 'job_title')
    creg.children = tuple(creg.children) + (('Quotations', 'quotation', 'contact'),)
    creg.actions.append(Act('merge', 'Merge into another contact', a_merge, lambda o, r: o.is_active and app.any(r, *MANAGE), style='danger',
                            confirm='The other contact keeps everything; this one is closed. Continue?', fields=(('into', 'Keep contact', 'opt:contacts', True),)))

    # ------------------------------------------------------------------ opportunities: account, campaign, territory, competitors
    oreg = app.regs['opportunity']
    oreg.form = ('title', 'contact', 'account', 'pipeline', 'stage', 'owner', 'territory', 'campaign', 'interest', 'unit_id', 'listing', 'value', 'expected_close',
                 'budget_max', 'bedrooms', 'requirements', 'source', 'competitors', 'competitor_note')
    oreg.cols = ('number', 'title', 'contact', 'account', 'pipeline', 'stage', 'owner', 'value', 'expected_close', 'status')
    oreg.filters = tuple(oreg.filters) + ('account', 'territory', 'campaign', 'competitors')
    oreg.children = tuple(oreg.children) + (('Quotations', 'quotation', 'opportunity'), ('Sales orders', 'sales_order', 'opportunity'))

    _opp_defaults = oreg.defaults

    def opp_defaults2(o, request):
        _opp_defaults(o, request)
        if not o.account_id and o.contact_id and o.contact.account_id:
            o.account = o.contact.account
        if not o.territory_id and o.contact_id and o.contact.territory_id:
            o.territory = o.contact.territory
        if not o.owner_id and app.settings().assign_leads == 'rules':
            o.owner = rule_owner(o)
    oreg.defaults = opp_defaults2

    _opp_extra = oreg.extra

    def opp_extra2(o, r):
        out = _opp_extra(o, r) if _opp_extra else {}
        prob = o.stage.probability if o.stage_id else 0
        out['forecast'] = {'probability': prob, 'weighted': float((_d(o.value) * prob / 100).quantize(Q2)), 'currency': app.settings().currency}
        return out
    oreg.extra = opp_extra2

    def a_quote(o, request, f):
        q = Quotation(number=app.next_number('quotation', 'QT'), opportunity=o, contact=o.contact, account=o.account, owner=o.owner, date=today(),
                      valid_until=today() + timedelta(days=app.settings().quote_validity_days or 30), currency=app.settings().currency,
                      terms=app.settings().quote_terms, branch_id=o.branch_id, price_book=(o.account.price_book if o.account_id else None),
                      payment_term=(o.account.payment_term if o.account_id and o.account.payment_term_id else PaymentTerm.objects.filter(is_default=True, is_active=True).first()))
        q.items = []
        compute(q)
        q.save()
        app.event('quotation', q.pk, f'Created from opportunity {o.number}', request.user)
        return {'message': f'Quotation {q.number} created – add the lines.', 'created': {'app': 'zeo-crm', 'kind': 'quotation', 'id': q.pk}}
    oreg.actions.insert(2, Act('quote', 'New quotation', a_quote, lambda o, r: o.status == 'open' and can_work(o, r), fields=()))

    # ------------------------------------------------------------------ activities: lead link + repeat
    areg = app.regs['activity']
    areg.form = ('kind', 'subject', 'contact', 'opportunity', 'lead', 'owner', 'due_at', 'duration_min', 'repeat', 'repeat_until', 'status', 'outcome')
    areg.filters = tuple(areg.filters) + ('lead', 'repeat')
    _act_saver = areg.saver

    def act_saver2(o, request, creating, old):
        was_done = old is not None and old.status == 'done'
        o = _act_saver(o, request, creating, old)
        if o.status == 'done' and o.lead_id and o.kind in ('call', 'email', 'message', 'meeting'):
            lead_touch(o.lead, o.done_at)
        if o.status == 'done' and not was_done:
            next_occurrence(o, request)
        return o
    areg.saver = act_saver2

    def next_occurrence(o, request=None):
        if not o.repeat or not o.due_at:
            return None
        nxt = {'daily': lambda x: x + timedelta(days=1), 'weekly': lambda x: x + timedelta(days=7), 'monthly': lambda x: add_months(x, 1),
               'quarterly': lambda x: add_months(x, 3)}.get(o.repeat)
        if nxt is None:
            return None
        due = nxt(o.due_at)
        if o.repeat_until and due.date() > o.repeat_until:
            return None
        if Activity.objects.filter(subject=o.subject, owner=o.owner, due_at=due, status='open', contact=o.contact, opportunity=o.opportunity, lead=o.lead).exists():
            return None
        n = Activity.objects.create(number=app.next_number('activity', 'ACT'), kind=o.kind, subject=o.subject, contact=o.contact, opportunity=o.opportunity, lead=o.lead,
                                    owner=o.owner, due_at=due, status='open', repeat=o.repeat, repeat_until=o.repeat_until, duration_min=o.duration_min, branch_id=o.branch_id)
        app.event('activity', n.pk, f'Next occurrence of {o.number} ({o.get_repeat_display().lower()})', getattr(request, 'user', None))
        if n.owner_id and n.owner.users_id:
            app.todo(n.owner.users, f'{n.get_kind_display()}: {n.subject}', 'activity', n.pk, f'{n.number} {n.subject}', PAGE + f'r/activity/{n.pk}', f'Due {d(due)}')
        return n

    _act_done = next(a for a in areg.actions if a.name == 'done')
    _run_done = _act_done.run

    def a_done2(o, request, f):
        o = _run_done(o, request, f)
        if o.lead_id and o.kind in ('call', 'email', 'message', 'meeting'):
            lead_touch(o.lead)
        n = next_occurrence(o, request)
        if n is not None:
            app.event('activity', o.pk, f'Next: {n.number} on {d(n.due_at)}', request.user)
        return o
    _act_done.run = a_done2

    # ------------------------------------------------------------------ masters (shared tables, shown in CRM)
    app.reg(Reg('account', Account, 'Account', 'Accounts', 'Customers',
                cols=('number', 'name', 'kind', 'industry', 'city', 'phone', 'email', 'owner', 'payment_term'),
                form=('name', 'legal_name', 'kind', 'industry', 'parent', 'tax_id', 'licence_no', 'phone', 'email', 'website', 'billing_address', 'shipping_address',
                      'city', 'country', 'payment_term', 'price_book', 'currency', 'credit_limit', 'owner', 'tags', 'notes', 'is_active'),
                number=('account', 'ACC'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
                search=('name', 'legal_name', 'number', 'tax_id', 'email', 'phone', 'city'), filters=('kind', 'owner', 'payment_term', 'is_active'),
                title='name', subtitle='industry',
                children=(('Contacts', 'contact', 'account'), ('Opportunities', 'opportunity', 'account'), ('Quotations', 'quotation', 'account'), ('Sales orders', 'sales_order', 'account')),
                defaults=lambda o, r: setattr(o, 'owner', o.owner or app.my_emp(r)),
                help='Companies you sell to (shared with the Sales and Accounting apps). People are contacts inside the account.'))

    def product_defaults(o, request):
        if not o.tax_id:
            o.tax = TaxCode.objects.filter(is_default=True, is_active=True).first()
        if not o.uom_id:
            o.uom = UOM.objects.filter(code='PCS').first() or UOM.objects.filter(is_active=True).first()

    app.reg(Reg('product', Product, 'Product', 'Products', 'Masters',
                cols=('number', 'code', 'name', 'kind', 'category', 'uom', 'sale_price', 'tax', 'is_active'),
                form=('code', 'name', 'kind', 'category', 'uom', 'barcode', 'sale_price', 'cost_price', 'min_price', 'tax', 'track_stock', 'description', 'is_active'),
                number=('product', 'PRD'), manage=MANAGE, view=SETUP_VIEW, search=('name', 'code', 'number', 'barcode'), filters=('kind', 'category', 'is_active'),
                title='name', subtitle='code', defaults=product_defaults, restricted=('cost_price',), deletable=True,
                children=(('Price book prices', 'price_item', 'product'),),
                help='Goods and services you quote and sell (shared with the Sales and Inventory apps). Cost is restricted to managers.'))
    app.reg(Reg('product_category', ProductCategory, 'Product category', 'Product categories', 'Masters', cols=('code', 'name', 'parent', 'is_active'),
                form=('code', 'name', 'parent', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'), filters=('is_active',), title='name', deletable=True, files=False))
    app.reg(Reg('uom', UOM, 'Unit of measure', 'Units of measure', 'Masters', cols=('code', 'name', 'category', 'base', 'factor', 'is_active'),
                form=('code', 'name', 'category', 'base', 'factor', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'), filters=('category', 'is_active'),
                title='name', deletable=True, files=False))
    app.reg(Reg('tax_code', TaxCode, 'Tax code', 'Tax codes', 'Masters', cols=('code', 'name', 'kind', 'rate', 'inclusive', 'is_default', 'is_active'),
                form=('code', 'name', 'kind', 'rate', 'inclusive', 'is_default', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'), filters=('kind', 'is_active'),
                title='name', deletable=True, files=False, help='VAT / GST rates of the country pack. Verify with the tax authority before use.'))
    app.reg(Reg('payment_term', PaymentTerm, 'Payment term', 'Payment terms', 'Masters', cols=('code', 'name', 'kind', 'days', 'instalments', 'is_default', 'is_active'),
                form=('code', 'name', 'kind', 'days', 'instalments', 'is_default', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'), filters=('kind', 'is_active'),
                title='name', deletable=True, files=False))
    app.reg(Reg('price_book', PriceBook, 'Price book', 'Price books', 'Masters', cols=('code', 'name', 'currency', 'kind', 'percent', 'valid_from', 'valid_to', 'is_default', 'is_active'),
                form=('code', 'name', 'currency', 'kind', 'percent', 'valid_from', 'valid_to', 'is_default', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'),
                filters=('kind', 'is_active'), title='name', deletable=True, files=False, children=(('Prices', 'price_item', 'price_book'),),
                help='Special prices per customer group: fixed prices per product (with quantity breaks) or a % on the list price.'))
    app.reg(Reg('price_item', PriceBookItem, 'Price', 'Prices', 'Masters', cols=('price_book', 'product', 'min_qty', 'price'), form=('price_book', 'product', 'min_qty', 'price'),
                manage=MANAGE, view=SETUP_VIEW, search=('product__name', 'price_book__name'), filters=('price_book',), title='product', subtitle='price_book',
                child_of='price_book', deletable=True, files=False))

    # ------------------------------------------------------------------ setup registers of part 1
    app.reg(Reg('territory', Territory, 'Territory', 'Territories', 'Setup', cols=('code', 'name', 'manager', 'areas', 'parent', 'is_active', 'review_status'),
                form=('code', 'name', 'areas', 'manager', 'members', 'parent', 'is_active'), manage=('crm_manager',), view=SETUP_VIEW, search=('code', 'name', 'areas'),
                filters=('is_active', 'review_status'), title='name', review=True, deletable=True, files=False,
                help='Sales areas. A lead whose city matches an area gets the territory; assignment rules can give it to the territory agents.'))
    app.reg(Reg('campaign', Campaign, 'Campaign', 'Campaigns', 'Setup', cols=('code', 'name', 'kind', 'start', 'end', 'budget', 'owner', 'is_active'),
                form=('code', 'name', 'kind', 'start', 'end', 'budget', 'owner', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'),
                filters=('kind', 'is_active'), title='name', deletable=True, files=False, children=(('Leads', 'lead', 'campaign'), ('Opportunities', 'opportunity', 'campaign')),
                help='Marketing campaigns – pick them on leads and opportunities to see what each campaign brought.'))
    app.reg(Reg('competitor', Competitor, 'Competitor', 'Competitors', 'Setup', cols=('code', 'name', 'website', 'pricing_note', 'is_active'),
                form=('code', 'name', 'website', 'strengths', 'weaknesses', 'pricing_note', 'is_active'), manage=MANAGE, view=SETUP_VIEW, search=('code', 'name'),
                filters=('is_active',), title='name', deletable=True, files=False, children=(('Opportunities', 'opportunity', 'competitors'),),
                help='Who you compete with. Pick competitors on an opportunity; the "Lost to competitors" report shows where you lose.'))
    app.reg(Reg('score_rule', LeadScoreRule, 'Lead score rule', 'Lead score rules', 'Setup', cols=('order', 'name', 'when', 'value', 'points', 'is_active', 'review_status'),
                form=('order', 'name', 'when', 'value', 'points', 'is_active'), manage=('crm_manager',), view=SETUP_VIEW, search=('name',), filters=('when', 'is_active', 'review_status'),
                title='name', review=True, deletable=True, files=False,
                help='Points a lead collects. At the qualify score (CRM setup) a new lead becomes Qualified; hot / warm / cold follow the score.'))
    app.reg(Reg('assignment_rule', AssignmentRule, 'Assignment rule', 'Assignment rules', 'Setup',
                cols=('order', 'name', 'source', 'territory', 'campaign', 'area', 'interest', 'min_value', 'assign_to', 'round_robin_territory', 'is_active'),
                form=('order', 'name', 'source', 'territory', 'campaign', 'area', 'interest', 'min_value', 'keyword', 'assign_to', 'round_robin_territory', 'is_active'),
                manage=('crm_manager',), view=SETUP_VIEW, search=('name',), filters=('is_active',), title='name', deletable=True, files=False,
                help='The first rule that matches a new lead / opportunity gives it the agent. No match → round robin to all sales agents.'))

    # ------------------------------------------------------------------ quotations and sales orders
    def price_for(product, book, qty):
        if book is not None:
            p = book.price_of(product, qty)
            if p is not None:
                return p
        return product.sale_price

    def compute(doc):
        """Normalises the items and fills the totals. Line: product, description, qty, uom, unit_price, discount_pct, tax, (computed) net/tax_amount/total."""
        items = []
        sub = disc = tax_total = Decimal('0')
        prods = {p.pk: p for p in Product.objects.filter(pk__in=[int(i.get('product')) for i in (doc.items or []) if str(i.get('product') or '').isdigit()]).select_related('uom', 'tax')}
        taxes = {t.pk: t for t in TaxCode.objects.all()}
        for i, it in enumerate(doc.items or []):
            pid = int(it['product']) if str(it.get('product') or '').isdigit() else None
            p = prods.get(pid)
            qty = _d(it.get('qty'), '1')
            if qty <= 0:
                continue
            price = it.get('unit_price')
            if price in (None, '') and p is not None:
                price = price_for(p, doc.price_book, qty)
            price = _d(price)
            dpct = _d(it.get('discount_pct'))
            tid = int(it['tax']) if str(it.get('tax') or '').isdigit() else (p.tax_id if p is not None and it.get('tax') in (None, '') else None)
            t = taxes.get(tid)
            rate = _d(t.rate) if t else Decimal('0')
            gross = (qty * price).quantize(Q2, ROUND_HALF_UP)
            line_disc = (gross * dpct / 100).quantize(Q2, ROUND_HALF_UP)
            net = gross - line_disc
            if t and t.inclusive and rate:
                net = (net / (1 + rate / 100)).quantize(Q2, ROUND_HALF_UP)
            items.append({'product': pid, 'product_label': (p.name if p else it.get('product_label', '')), 'description': (it.get('description') or (p.description if p else '') or '')[:300],
                          'qty': float(qty), 'uom': (it.get('uom') or (p.uom.code if p is not None and p.uom_id else ''))[:10], 'unit_price': float(price),
                          'discount_pct': float(dpct), 'tax': tid, 'tax_rate': float(rate), 'gross': float(gross), 'net': float(net), 'order': i + 1,
                          'below_min': bool(p is not None and p.min_price is not None and price * (100 - dpct) / 100 < p.min_price)})
            sub += gross
            disc += line_disc
        hd = _d(doc.discount_pct)
        head_disc = ((sub - disc) * hd / 100).quantize(Q2, ROUND_HALF_UP) if hd else Decimal('0')
        factor = (1 - hd / 100) if hd else Decimal('1')
        for it in items:
            net = (_d(it['net']) * factor).quantize(Q2, ROUND_HALF_UP)
            ta = (net * _d(it['tax_rate']) / 100).quantize(Q2, ROUND_HALF_UP)
            it['net'], it['tax_amount'], it['total'] = float(net), float(ta), float(net + ta)
            tax_total += ta
        doc.items = items
        doc.subtotal = sub
        doc.discount_total = disc + head_disc
        doc.tax_total = tax_total
        doc.total = sub - doc.discount_total + tax_total
        return doc

    def sync_lines(doc, model, fk):
        model.objects.filter(**{fk: doc}).delete()
        for it in doc.items:
            model.objects.create(**{fk: doc}, order=it['order'], product_id=it['product'], description=it['description'], qty=_d(it['qty']), uom=it['uom'],
                                 unit_price=_d(it['unit_price']), discount_pct=_d(it['discount_pct']), tax_id=it['tax'], tax_rate=_d(it['tax_rate']),
                                 net=_d(it['net']), tax_amount=_d(it['tax_amount']), total=_d(it['total']))

    def needs_approval(q):
        lim = _d(app.settings().discount_limit_pct)
        if _d(q.discount_pct) > lim:
            return f'overall discount {q.discount_pct}% is above the {lim}% an agent may give'
        for it in q.items:
            if _d(it['discount_pct']) > lim:
                return f'line discount {it["discount_pct"]}% on {it["product_label"] or it["description"]} is above {lim}%'
            if it.get('below_min'):
                return f'{it["product_label"]} is quoted below its minimum price'
        return ''

    def doc_defaults(o, request):
        if not o.owner_id:
            o.owner = (o.opportunity.owner if o.opportunity_id and o.opportunity.owner_id else None) or app.my_emp(request)
        if not o.contact_id and o.opportunity_id:
            o.contact = o.opportunity.contact
        if not o.account_id:
            o.account = (o.opportunity.account if o.opportunity_id else None) or (o.contact.account if o.contact_id else None)
        if o.account_id:
            o.price_book = o.price_book or o.account.price_book
            o.payment_term = o.payment_term or o.account.payment_term
            if o.account.currency and not o.currency:
                o.currency = o.account.currency
        if not o.payment_term_id:
            o.payment_term = PaymentTerm.objects.filter(is_default=True, is_active=True).first()
        if not o.price_book_id:
            o.price_book = PriceBook.objects.filter(is_default=True, is_active=True).first()
        if not o.currency:
            o.currency = app.settings().currency
        if not o.date:
            o.date = today()
        if isinstance(o, Quotation):
            if not o.valid_until:
                o.valid_until = o.date + timedelta(days=app.settings().quote_validity_days or 30)
            if not o.terms:
                o.terms = app.settings().quote_terms

    def doc_check(o, request, old):
        if not o.account_id and not o.contact_id:
            raise AppError('Choose the customer: an account or a contact.', 'account')
        if not isinstance(o.items, list):
            raise AppError('Lines are not valid.', 'items')

    def quote_saver(o, request, creating, old):
        if old is not None and old.status not in ('draft', 'approval') and not app.any(request, *MANAGE):
            raise AppError('A sent quotation cannot be changed – make a revision.', 'status')
        compute(o)
        why = needs_approval(o)
        if o.status in ('draft', 'approval'):
            o.status = 'approval' if why else 'draft'
            o.approval_note = why
        o.save()
        sync_lines(o, QuotationLine, 'quotation')
        if old is not None and old.status == 'draft' and o.status == 'approval' or (creating and why):
            for u in app.users_with_role('crm_manager'):
                app.todo(u, f'Approve discount: {o.number}', 'quotation', o.pk, o.number, PAGE + f'r/quotation/{o.pk}', why)
        if o.opportunity_id and o.opportunity.status == 'open' and o.total and _d(o.opportunity.value) != o.total:
            Opportunity.objects.filter(pk=o.opportunity_id).update(value=o.total)
        return o

    def order_saver(o, request, creating, old):
        if old is not None and old.status in ('invoiced', 'closed', 'cancelled'):
            raise AppError('A closed order cannot be changed.', 'status')
        compute(o)
        o.save()
        sync_lines(o, SalesOrderLine, 'sales_order')
        return o

    def q_open(o, *_):
        return o.status in ('draft', 'approval', 'approved', 'sent')

    def a_q_approve(o, request, f):
        o.status, o.approved_by, o.approved_at = 'approved', request.user, now()
        o.save(update_fields=['status', 'approved_by', 'approved_at'])
        app.event('quotation', o.pk, 'Discount approved' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos('quotation', o.pk, 'Approve discount')
        if o.owner_id and o.owner.users_id:
            app.todo(o.owner.users, f'Quotation approved: {o.number}', 'quotation', o.pk, o.number, PAGE + f'r/quotation/{o.pk}')
        return o

    def a_q_refuse(o, request, f):
        o.status = 'draft'
        o.approval_note = f'Approval refused: {f.s("note")}'[:200]
        o.save(update_fields=['status', 'approval_note'])
        app.event('quotation', o.pk, f'Discount refused – {f.s("note")}', request.user)
        app.close_todos('quotation', o.pk, 'Approve discount')
        if o.owner_id and o.owner.users_id:
            app.todo(o.owner.users, f'Discount refused: {o.number}', 'quotation', o.pk, o.number, PAGE + f'r/quotation/{o.pk}', f.s('note'))
        return o

    def a_q_send(o, request, f):
        if not o.items:
            raise AppError('Add at least one line first.')
        o.status, o.sent_at = 'sent', now()
        o.save(update_fields=['status', 'sent_at'])
        app.event('quotation', o.pk, 'Sent to the customer' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        to = [x for x in ((o.contact.email if o.contact_id else ''), (o.account.email if o.account_id else '')) if x]
        if f.b('email') and to:
            body = (f'Dear {o.contact.name if o.contact_id else "Customer"},\n\nPlease find our quotation {o.number} dated {o.date} for {o.currency} {o.total:,.2f}, valid until {o.valid_until}.\n\n'
                    + '\n'.join(f'- {it["product_label"] or it["description"]}: {it["qty"]:g} {it["uom"]} × {it["unit_price"]:,.2f}' for it in o.items)
                    + f'\n\n{o.notes}\n\nRegards,\n{emp_name(o.owner)}')
            app.send_mail(to[:1], f'Quotation {o.number}', body)
            app.event('quotation', o.pk, f'E-mailed to {to[0]}', request.user)
        if o.opportunity_id:
            crm.move_to(o.opportunity, 'proposal', request)
        return o

    def a_q_accept(o, request, f):
        o.status, o.decided_at = 'accepted', now()
        o.save(update_fields=['status', 'decided_at'])
        Quotation.objects.filter(opportunity=o.opportunity, status__in=('draft', 'approval', 'approved', 'sent')).exclude(pk=o.pk).exclude(opportunity=None).update(status='rejected')
        app.event('quotation', o.pk, 'Accepted by the customer' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        so = None
        if not f.b('no_order'):
            so = make_order(o, request, f.dt('delivery_date'))
        if o.opportunity_id and o.opportunity.status == 'open':
            opp = o.opportunity
            opp.value = o.total
            opp.save(update_fields=['value'])
            if f.b('win'):
                crm.a_won(opp, request, _Fake({'note': so.number if so else o.number}))
            else:
                crm.move_to(opp, 'negotiation', request)
        if o.account_id and o.account.kind == 'prospect':
            Account.objects.filter(pk=o.account_id).update(kind='customer')
        if o.contact_id and o.contact.contact_type == 'prospect':
            Contact.objects.filter(pk=o.contact_id).update(contact_type='customer')
        try:
            from zeo.appkit.engine import REGISTRY
            for label, other in REGISTRY.items():
                fn = other.hooks.get('quote_accepted') if label != 'ZeoCRM' else None
                if fn and crm.linked(app, label):
                    fn(quotation_id=o.pk, sales_order_id=getattr(so, 'pk', None), user=request.user)
        except Exception:
            pass
        if so is not None:
            return {'message': f'Accepted. Sales order {so.number} created.', 'created': {'app': 'zeo-crm', 'kind': 'sales_order', 'id': so.pk}}
        return o

    class _Fake:
        def __init__(self, data):
            self.data = data

        def s(self, k, default=''):
            return str(self.data.get(k, default) or '')

    def make_order(q, request, delivery=None):
        so = SalesOrder(number=app.next_number('sales_order', 'SO'), quotation=q, opportunity=q.opportunity, account=q.account, contact=q.contact, date=today(),
                        owner=q.owner, currency=q.currency, price_book=q.price_book, payment_term=q.payment_term, reference=q.reference, items=q.items,
                        discount_pct=q.discount_pct, notes=q.notes, terms=q.terms, branch_id=q.branch_id, delivery_date=delivery)
        compute(so)
        so.save()
        sync_lines(so, SalesOrderLine, 'sales_order')
        q.sales_order = so
        q.save(update_fields=['sales_order'])
        app.event('sales_order', so.pk, f'Created from quotation {q.number}', request.user)
        return so

    def a_q_reject(o, request, f):
        o.status, o.decided_at = 'rejected', now()
        o.lost_reason = f.obj('reason', LostReason, False, 'Reason')
        o.save(update_fields=['status', 'decided_at', 'lost_reason'])
        app.event('quotation', o.pk, 'Rejected by the customer' + (f' – {o.lost_reason.name}' if o.lost_reason_id else '') + (f': {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_q_revise(o, request, f):
        n = Quotation(number=app.next_number('quotation', 'QT'), opportunity=o.opportunity, contact=o.contact, account=o.account, owner=o.owner, date=today(),
                      valid_until=today() + timedelta(days=app.settings().quote_validity_days or 30), currency=o.currency, price_book=o.price_book,
                      payment_term=o.payment_term, reference=o.reference, items=o.items, discount_pct=o.discount_pct, notes=o.notes, terms=o.terms,
                      branch_id=o.branch_id, revision=o.revision + 1, previous=o)
        compute(n)
        n.status = 'approval' if needs_approval(n) else 'draft'
        n.approval_note = needs_approval(n)
        n.save()
        sync_lines(n, QuotationLine, 'quotation')
        if o.status in ('draft', 'approval', 'approved', 'sent'):
            o.status = 'cancelled'
            o.save(update_fields=['status'])
        app.event('quotation', o.pk, f'Revised as {n.number}', request.user)
        app.event('quotation', n.pk, f'Revision {n.revision} of {o.number}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return {'message': f'Revision {n.number} created.', 'created': {'app': 'zeo-crm', 'kind': 'quotation', 'id': n.pk}}

    def a_q_cancel(o, request, f):
        o.status = 'cancelled'
        o.save(update_fields=['status'])
        app.event('quotation', o.pk, 'Cancelled' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        app.close_todos('quotation', o.pk)
        return o

    def q_extra(o, r):
        return {'totals': {'subtotal': float(o.subtotal), 'discount': float(o.discount_total), 'tax': float(o.tax_total), 'total': float(o.total), 'currency': o.currency},
                'approval_note': o.approval_note, 'pdf': f'/zeo-crm/api/quotation/{o.pk}/pdf/', 'versions': [{'id': x.pk, 'number': x.number, 'status': x.get_status_display()} for x in o.revisions.all()],
                'links': {'previous': o.previous.number if o.previous_id else '', 'sales_order': o.sales_order.number if o.sales_order_id else ''}}

    app.reg(Reg('quotation', Quotation, 'Quotation', 'Quotations', 'Sales',
                cols=('number', 'date', 'account', 'contact', 'opportunity', 'owner', 'total', 'valid_until', 'status'),
                form=('account', 'contact', 'opportunity', 'date', 'valid_until', 'owner', 'currency', 'price_book', 'payment_term', 'reference', 'items', 'discount_pct', 'notes', 'terms'),
                number=('quotation', 'QT'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
                search=('number', 'account__name', 'contact__name', 'reference', 'opportunity__title'), filters=('status', 'account', 'owner', 'opportunity'),
                title='number', subtitle='account', defaults=doc_defaults, check=doc_check, saver=quote_saver, edit_when=lambda o: o.status in ('draft', 'approval', 'approved'),
                info=('subtotal', 'discount_total', 'tax_total', 'total', 'status', 'revision', 'previous', 'approval_note', 'approved_by', 'sent_at', 'decided_at', 'lost_reason', 'sales_order'),
                extra=q_extra,
                actions=[
                    Act('approve', 'Approve discount', a_q_approve, lambda o, r: o.status == 'approval' and app.any(r, 'crm_manager'), style='primary', fields=(('note', 'Note', 'text', False),)),
                    Act('refuse', 'Refuse discount', a_q_refuse, lambda o, r: o.status == 'approval' and app.any(r, 'crm_manager'), style='danger', fields=(('note', 'Why', 'text', True),)),
                    Act('send', 'Mark sent / e-mail', a_q_send, lambda o, r: o.status in ('draft', 'approved') and can_work(o, r), style='primary',
                        fields=(('email', 'E-mail the quotation to the customer', 'bool', False), ('note', 'Note', 'text', False))),
                    Act('accept', 'Accepted', a_q_accept, lambda o, r: o.status in ('sent', 'approved', 'draft') and can_work(o, r), style='primary',
                        fields=(('no_order', 'Do not create a sales order', 'bool', False), ('delivery_date', 'Promised delivery', 'date', False),
                                ('win', 'Mark the opportunity as won', 'bool', False), ('note', 'Note', 'text', False))),
                    Act('reject', 'Rejected', a_q_reject, lambda o, r: o.status in ('sent', 'approved', 'draft') and can_work(o, r),
                        fields=(('reason', 'Reason', 'opt:lost-reasons', False), ('note', 'Note', 'text', False))),
                    Act('revise', 'Make a revision', a_q_revise, lambda o, r: o.status not in ('accepted', 'cancelled') and can_work(o, r), fields=(('note', 'What changes', 'text', False),)),
                    Act('cancel', 'Cancel', a_q_cancel, lambda o, r: q_open(o) and can_work(o, r), style='danger', fields=(('note', 'Why', 'text', False),)),
                ],
                help='Priced offers with product lines. A discount above the limit (CRM setup) waits for the CRM manager. Accepted → sales order.'))

    def so_set(status_, text, close=False):
        def run(o, request, f):
            o.status = status_
            if f.s('ref'):
                if status_ == 'delivered':
                    o.delivery_ref = f.s('ref')[:60]
                elif status_ == 'invoiced':
                    o.invoice_ref = f.s('ref')[:60]
            if close:
                o.closed_at = now()
            if status_ == 'delivered':
                SalesOrderLine.objects.filter(sales_order=o).update(delivered_qty=models_F('qty'))
            o.save()
            app.event('sales_order', o.pk, text + (f' – {f.s("ref")}' if f.s('ref') else '') + (f' ({f.s("note")})' if f.s('note') else ''), request.user)
            if close:
                app.close_todos('sales_order', o.pk)
            try:
                from zeo.appkit.engine import REGISTRY
                for label, other in REGISTRY.items():
                    fn = other.hooks.get('order_status') if label != 'ZeoCRM' else None
                    if fn and crm.linked(app, label):
                        fn(sales_order_id=o.pk, status=status_, user=request.user)
            except Exception:
                pass
            return o
        return run

    def so_extra(o, r):
        return {'totals': {'subtotal': float(o.subtotal), 'discount': float(o.discount_total), 'tax': float(o.tax_total), 'total': float(o.total), 'currency': o.currency},
                'pdf': f'/zeo-crm/api/sales-order/{o.pk}/pdf/', 'links': {'quotation': o.quotation.number if o.quotation_id else ''}}

    app.reg(Reg('sales_order', SalesOrder, 'Sales order', 'Sales orders', 'Sales',
                cols=('number', 'date', 'account', 'contact', 'quotation', 'owner', 'total', 'delivery_date', 'status'),
                form=('account', 'contact', 'opportunity', 'quotation', 'date', 'delivery_date', 'owner', 'currency', 'price_book', 'payment_term', 'reference', 'items', 'discount_pct', 'notes', 'terms'),
                number=('sales_order', 'SO'), manage=MANAGE, view=VIEW, create=('crm_agent',), own=(('owner', 'emp'),), own_edit=True,
                search=('number', 'account__name', 'contact__name', 'reference', 'quotation__number'), filters=('status', 'account', 'owner'),
                title='number', subtitle='account', defaults=doc_defaults, check=doc_check, saver=order_saver, edit_when=lambda o: o.status in ('confirmed', 'progress'),
                info=('subtotal', 'discount_total', 'tax_total', 'total', 'status', 'delivery_ref', 'invoice_ref', 'closed_at'), extra=so_extra,
                actions=[
                    Act('progress', 'In progress', so_set('progress', 'In progress'), lambda o, r: o.status == 'confirmed' and can_work(o, r), fields=(('note', 'Note', 'text', False),)),
                    Act('delivered', 'Delivered', so_set('delivered', 'Delivered'), lambda o, r: o.status in ('confirmed', 'progress') and can_work(o, r), style='primary',
                        fields=(('ref', 'Delivery note / project ref.', 'text', False), ('note', 'Note', 'text', False))),
                    Act('invoiced', 'Invoiced', so_set('invoiced', 'Invoiced'), lambda o, r: o.status in ('delivered', 'progress', 'confirmed') and app.any(r, *MANAGE),
                        fields=(('ref', 'Invoice ref.', 'text', True),)),
                    Act('close', 'Close', so_set('closed', 'Closed', True), lambda o, r: o.status in ('delivered', 'invoiced') and app.any(r, *MANAGE)),
                    Act('cancel', 'Cancel', so_set('cancelled', 'Cancelled', True), lambda o, r: o.status in ('confirmed', 'progress') and app.any(r, *MANAGE), style='danger',
                        fields=(('note', 'Why', 'text', True),)),
                ],
                help='Confirmed orders (from accepted quotations). Tracked until delivered and invoiced; the Sales / Inventory apps take over delivery and invoicing when installed.'))

    # ------------------------------------------------------------------ sales targets
    def attainment(t):
        """Actual figure of a target in its period (branch-aware through the register scope is not needed: targets are managers' tools)."""
        opps = Opportunity.objects.filter(status='won', closed_at__date__gte=t.period_start, closed_at__date__lte=t.period_end)
        orders = SalesOrder.objects.exclude(status='cancelled').filter(date__gte=t.period_start, date__lte=t.period_end)
        leads = Lead.objects.filter(created_at__date__gte=t.period_start, created_at__date__lte=t.period_end)
        if t.owner_id:
            opps, orders, leads = opps.filter(owner=t.owner), orders.filter(owner=t.owner), leads.filter(owner=t.owner)
        if t.territory_id:
            opps, leads = opps.filter(territory=t.territory), leads.filter(territory=t.territory)
            orders = orders.filter(opportunity__territory=t.territory)
        if t.measure == 'won_value':
            return _d(opps.aggregate(s=Sum('value'))['s'])
        if t.measure == 'won_count':
            return Decimal(opps.count())
        if t.measure == 'order_value':
            return _d(orders.aggregate(s=Sum('total'))['s'])
        return Decimal(leads.count())

    def target_extra(o, r):
        a = attainment(o)
        return {'attainment': {'actual': float(a), 'target': float(o.target), 'pct': float((a / o.target * 100).quantize(Q2)) if o.target else 0}}

    app.reg(Reg('target', SalesTarget, 'Sales target', 'Sales targets', 'Sales', cols=('name', 'owner', 'territory', 'period_start', 'period_end', 'measure', 'target', 'is_active'),
                form=('name', 'owner', 'territory', 'period_start', 'period_end', 'measure', 'target', 'is_active'), manage=MANAGE, view=VIEW,
                own=(('owner', 'emp'),), search=('name',), filters=('owner', 'territory', 'measure', 'is_active'), title='name', subtitle='owner', extra=target_extra,
                deletable=True, files=False, help='Targets per agent or territory and period. The "Targets vs actual" report shows attainment.'))

    # ------------------------------------------------------------------ hooks for other apps
    def h_quotation_info(quotation_id):
        q = Quotation.objects.filter(pk=quotation_id).first()
        return q and {'id': q.pk, 'number': q.number, 'status': q.status, 'account': q.account.name if q.account_id else '', 'account_id': q.account_id,
                      'contact': q.contact.name if q.contact_id else '', 'total': float(q.total), 'currency': q.currency, 'items': q.items, 'page': PAGE + f'r/quotation/{q.pk}'}

    def h_order_info(sales_order_id):
        o = SalesOrder.objects.filter(pk=sales_order_id).first()
        return o and {'id': o.pk, 'number': o.number, 'status': o.status, 'account': o.account.name if o.account_id else '', 'account_id': o.account_id,
                      'total': float(o.total), 'currency': o.currency, 'items': o.items, 'delivery_date': o.delivery_date, 'page': PAGE + f'r/sales_order/{o.pk}'}

    def h_order_update(sales_order_id, status=None, delivery_ref='', invoice_ref='', user=None):
        o = SalesOrder.objects.filter(pk=sales_order_id).first()
        if o is None:
            return None
        if delivery_ref:
            o.delivery_ref = delivery_ref[:60]
        if invoice_ref:
            o.invoice_ref = invoice_ref[:60]
        if status in dict(SalesOrder._meta.get_field('status').choices):
            o.status = status
        o.save()
        app.event('sales_order', o.pk, f'Updated by another app: {status or ""} {delivery_ref} {invoice_ref}'.strip(), user)
        return o.pk

    def h_lead_capture(name, email='', phone='', company='', message='', source='WEB', channel='web', city='', campaign='', user=None, area='', interest='', budget=None, bedrooms=None, **kw):
        """Web form / e-mail / API capture: makes a lead (scored, assigned) – returns {id, number}."""
        src = Source.objects.filter(code__iexact=source).first() or Source.objects.filter(is_active=True).first()
        cmp = Campaign.objects.filter(code__iexact=campaign).first() if campaign else None
        lead = Lead(number=app.next_number('lead', 'LD'), name=(name or email or phone or 'Enquiry')[:150], email=email or '', phone=phone or '', company_name=(company or '')[:150],
                    requirements=message or '', source=src, campaign=cmp, channel=channel if channel in dict(Lead._meta.get_field('channel').choices) else 'api', city=(city or '')[:60],
                    area=(area or '')[:100], interest_kind=interest if interest in dict(INTEREST) else 'other', budget_max=_d(budget) if budget not in (None, '') else None,
                    bedrooms=int(bedrooms) if str(bedrooms or '').isdigit() else None)
        lead.value = lead.budget_max or 0
        lead.territory = territory_for(lead.city, '', lead.area)
        lead.score = score_of(lead)
        lead.rating = rating_of(lead.score)
        assign_lead(lead)
        hrs = app.settings().first_contact_hours or 0
        if hrs:
            lead.first_contact_due = now() + timedelta(hours=hrs)
        if lead.score >= app.settings().qualify_score:
            lead.status = 'qualified'
        lead.save()
        app.event('lead', lead.pk, f'Captured ({lead.get_channel_display().lower()})', user)
        if lead.owner_id and lead.owner.users_id:
            app.todo(lead.owner.users, f'New lead: {lead.name}', 'lead', lead.pk, f'{lead.number} {lead.name}', PAGE + f'r/lead/{lead.pk}', f'Contact {lead.name} {lead.phone or lead.email}.')
        return {'id': lead.pk, 'number': lead.number}

    app.hooks.update({'quotation_info': h_quotation_info, 'order_info': h_order_info, 'order_update': h_order_update, 'lead_capture': h_lead_capture})

    # exported helpers (dashboard, reports, pdf)
    return {'compute': compute, 'attainment': attainment, 'score_of': score_of, 'needs_approval': needs_approval, 'next_occurrence': next_occurrence, 'lead_capture': h_lead_capture}
