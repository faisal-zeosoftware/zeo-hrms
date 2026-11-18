"""v1.67.28 – CRM release 2: the general CRM of the functional scope (part 1) that was missing.

  * Enquiries (4): requirement, products / services, specifications, site visit, sales + technical person, technical evaluation,
    clarifications, revisions, approval, conversion to a quotation, closure.
  * Customers (2, 13): customer profile (category, segment, risk, status – blocking / blacklisting by a manager – account manager,
    preferences, customer 360° figures: lifetime value, relationship score, satisfaction, churn risk), customer sites / addresses.
  * Onboarding (11): checklist per industry, KYC, contract, credit, activation, handover, training, welcome message; started when a
    deal is won.
  * Contracts (12): types, value, dates, approval, revisions, renewal (with a renewal opportunity), expiry reminders, termination.
  * Complaints (10): categories, priority with SLA, assignment, investigation, resolution, escalation of late ones, feedback and
    rating, closure confirmation, Send to Maintenance.
  * Commissions (7): plans (rate on won value / order value / accepted quotations, agent / territory, bonus above target),
    calculation per period, approval and payment.
  * Documents (15): controlled documents with versions, approval and expiry reminders.
  * Quotations (6): types incl. recurring subscriptions (accepted → subscription contract), templates, delivery terms, scope of work,
    version comparison.
  * Leads / opportunities / activities (1, 3, 5): priority, next follow-up, expected close, decision makers, sales strategy,
    technical requirements; site visits, demos, online meetings, presentations; missed follow-up alerts and escalation.
  * Reports and dashboards (14, 16): ageing, win / loss, conversion, acquisition, retention, churn, lifetime value, follow-ups,
    forecasts by period / territory / industry / product, complaints, contracts, commissions, onboarding, enquiries.
"""
import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.signals import post_save, pre_save

from zeo.appkit.engine import Act, AppError, Reg, add_months, d, emp_name, linked, now, today, user_name

from .models import (CONTRACT_KINDS, Activity, Commission, CommissionPlan, Complaint, Contact, Contract, CRMDocument, CustomerProfile, CustomerSite,
                     Enquiry, Lead, LostReason, Onboarding, OnboardingTemplate, Opportunity, Pipeline, Quotation, QuotationTemplate, SalesOrder, SalesTarget,
                     Stage)

log = logging.getLogger(__name__)
CTX = {}
DEFAULT_CHECKLIST = ['Customer details and addresses checked', 'KYC / trade licence / TRN received', 'Contract or agreement signed and uploaded',
                     'Credit terms approved', 'Customer account activated', 'Product / service activated', 'Implementation / operations handover',
                     'Customer training', 'Welcome message sent']


def _d(v):
    try:
        return Decimal(str(v or 0))
    except Exception:   # noqa: BLE001
        return Decimal('0')


def _emp_user(e):
    return getattr(e, 'users', None) if e is not None and getattr(e, 'users_id', None) else None


# ------------------------------------------------------------------ customer status (blocking)
def profile_of(account, create=True):
    if account is None:
        return None
    p = CustomerProfile.objects.filter(account=account).first()
    if p is None and create:
        p = CustomerProfile.objects.create(account=account, branch_id=getattr(account, 'branch_id', None), account_manager_id=getattr(account, 'owner_id', None))
    return p


def blocked(account):
    p = CustomerProfile.objects.filter(account=account).only('status', 'block_reason').first() if account is not None else None
    return p if p is not None and p.status in ('blocked', 'blacklisted') else None


def guard_account(reg):
    """New opportunities, enquiries, quotations, sales orders and contracts are refused for a blocked / blacklisted customer."""
    base = reg.check

    def check(o, request, old):
        acc = getattr(o, 'account', None) if getattr(o, 'account_id', None) else None
        p = blocked(acc)
        if p is not None and old is None:
            raise AppError(f'{acc.name} is {p.get_status_display().lower()}' + (f' ({p.block_reason})' if p.block_reason else '') +
                           ' – a CRM manager must release the customer first (Customer profiles).', 'account')
        if base:
            base(o, request, old)
    reg.check = check


# ------------------------------------------------------------------ customer 360° figures
def recalc_profile(p, settings=None):
    app = CTX['app']
    s = settings or app.settings()
    acc = p.account
    t = today()
    won = Opportunity.objects.filter(account=acc, status='won')
    p.won_count = won.count()
    won_value = _d(won.aggregate(v=Sum('value'))['v'])
    orders = _d(SalesOrder.objects.filter(account=acc).exclude(status='cancelled').aggregate(v=Sum('total'))['v'])
    p.lifetime_value = max(won_value, orders)
    p.open_value = _d(Opportunity.objects.filter(account=acc, status='open').aggregate(v=Sum('value'))['v'])
    lw = won.order_by('-closed_at').values_list('closed_at', flat=True).first()
    p.last_won_at = lw.date() if lw else None
    la = Activity.objects.filter(Q(contact__account=acc) | Q(opportunity__account=acc), status='done').order_by('-done_at').values_list('done_at', flat=True).first()
    p.last_activity_at = la.date() if la else None
    p.open_complaints = Complaint.objects.filter(account=acc).exclude(status__in=('closed', 'cancelled')).count()
    p.active_contracts = Contract.objects.filter(account=acc, status='active').count()
    ratings = Complaint.objects.filter(account=acc, rating__isnull=False).values_list('rating', flat=True)
    p.satisfaction = (Decimal(sum(ratings)) / len(ratings)).quantize(Decimal('0.01')) if ratings else None
    last = max([x for x in (p.last_won_at, p.last_activity_at) if x] or [None]) if (p.last_won_at or p.last_activity_at) else None
    days = (t - last).days if last else 9999
    rec = 40 if days <= 30 else 30 if days <= 90 else 15 if days <= 180 else 5 if days <= 365 else 0
    freq = min(20, p.won_count * 5)
    val = 20 if p.lifetime_value >= 500000 else 15 if p.lifetime_value >= 100000 else 10 if p.lifetime_value >= 20000 else 5 if p.lifetime_value > 0 else 0
    con = min(20, p.active_contracts * 10)
    sat = 0 if p.satisfaction is None else int((float(p.satisfaction) - 3) * 5)
    p.relationship_score = max(0, min(100, rec + freq + val + con + sat - 5 * p.open_complaints))
    why = []
    if p.won_count and days > (s.churn_days or 180):
        why.append(f'no business for {days} days')
    expired = Contract.objects.filter(account=acc, status='expired', end__gte=t - timedelta(days=365)).exclude(next_versions__status__in=('active', 'draft', 'approval')).count()
    if expired:
        why.append(f'{expired} contract(s) ended without renewal')
    if p.open_complaints >= 2:
        why.append(f'{p.open_complaints} open complaints')
    if p.satisfaction is not None and p.satisfaction < 3:
        why.append(f'satisfaction {p.satisfaction}')
    p.churn_risk = 'high' if len(why) >= 2 or (why and p.relationship_score < 25) else 'medium' if why else 'low'
    p.churn_reason = '; '.join(why)[:200]
    p.calculated_at = now()
    p.save()
    return p


def recalc_all():
    from ZeoMasters.models import Account
    s = CTX['app'].settings()
    ids = set(Opportunity.objects.exclude(account=None).values_list('account_id', flat=True)) | set(SalesOrder.objects.exclude(account=None).values_list('account_id', flat=True))
    ids |= set(Contract.objects.values_list('account_id', flat=True)) | set(CustomerProfile.objects.values_list('account_id', flat=True))
    n = 0
    for acc in Account.objects.filter(pk__in=ids):
        recalc_profile(profile_of(acc), s)
        n += 1
    vals = sorted(CustomerProfile.objects.values_list('pk', 'lifetime_value'), key=lambda x: -x[1])
    for i, (pk, v) in enumerate(vals):   # A = top 20 %, B = next 30 %, C = the rest (by lifetime value)
        seg = '' if not v else 'A' if i < max(1, len(vals) * 0.2) else 'B' if i < len(vals) * 0.5 else 'C'
        CustomerProfile.objects.filter(pk=pk).update(segment=seg)
    return n


# ------------------------------------------------------------------ pipelines for renewals
def renewal_pipeline():
    p = Pipeline.objects.filter(is_active=True, kind='renewal', code='CRENEW').first() or Pipeline.objects.filter(is_active=True, kind='renewal').exclude(code='RENEWAL').first()
    if p is None:
        p = Pipeline.objects.create(code='CRENEW', name='Contract renewals', kind='renewal', order=9)
        for i, (c, n, pr, w, l) in enumerate([('DUE', 'Renewal due', 50, False, False), ('OFFER', 'Renewal offer', 65, False, False),
                                               ('NEGOTIATION', 'Negotiation', 80, False, False), ('WON', 'Renewed', 100, True, False), ('LOST', 'Not renewed', 0, False, True)]):
            Stage.objects.create(pipeline=p, code=f'CRENEW_{c}', name=n, order=i + 1, probability=pr, is_won=w, is_lost=l)
    return p


def renewal_opportunity(c, user=None):
    """An open renewal opportunity for a contract (once)."""
    if c.renewal_opportunity_id and Opportunity.objects.filter(pk=c.renewal_opportunity_id, status='open').exists():
        return c.renewal_opportunity
    app = CTX['app']
    contact = c.contact or Contact.objects.filter(account=c.account, is_active=True).first()
    if contact is None:
        contact = Contact.objects.create(number=app.next_number('contact', 'CON'), name=c.account.name, kind='company', company_name=c.account.name,
                                         account=c.account, contact_type='customer', branch_id=c.branch_id)
    p = renewal_pipeline()
    o = Opportunity.objects.create(number=app.next_number('opportunity', 'OPP'), title=f'Renewal – {c.title}'[:150], contact=contact, account=c.account, pipeline=p,
                                   stage=p.stages.filter(is_won=False, is_lost=False).order_by('order').first(), owner=c.owner, value=c.value,
                                   expected_close=c.end, interest='service', branch_id=c.branch_id, industry=c.industry or p.industry,
                                   extra=dict(c.opportunity.extra or {}) if c.opportunity_id else {})
    c.renewal_opportunity = o
    c.save(update_fields=['renewal_opportunity'])
    app.event('opportunity', o.pk, f'Renewal of contract {c.number}', user)
    app.event('contract', c.pk, f'Renewal opportunity {o.number}', user)
    if _emp_user(c.owner):
        app.todo(c.owner.users, f'Contract renewal: {c.title}', 'opportunity', o.pk, f'{o.number} {o.title}', app.page + f'r/opportunity/{o.pk}',
                 f'Contract {c.number} ends on {c.end:%d/%m/%Y}.' if c.end else '')
    return o


# ------------------------------------------------------------------ onboarding / subscription contract from won deals
def start_onboarding(opp=None, order=None, user=None):
    app = CTX['app']
    if opp is not None and Onboarding.objects.filter(opportunity=opp).exists():
        return None
    acc = (opp.account if opp is not None and opp.account_id else None) or (order.account if order is not None and order.account_id else None)
    ind = getattr(opp, 'industry', '') or getattr(order, 'industry', '') or ''
    tpl = OnboardingTemplate.objects.filter(is_active=True, industry=ind).first() if ind else None
    tpl = tpl or OnboardingTemplate.objects.filter(is_active=True, is_default=True).first() or OnboardingTemplate.objects.filter(is_active=True, industry='').first()
    items = [x.strip() for x in (tpl.items if tpl else '\n'.join(DEFAULT_CHECKLIST)).splitlines() if x.strip()]
    o = Onboarding.objects.create(number=app.next_number('onboarding', 'ONB'), account=acc, contact=getattr(opp, 'contact', None) or getattr(order, 'contact', None),
                                  opportunity=opp, sales_order=order, template=tpl, start=today(), target_date=today() + timedelta(days=30),
                                  owner=(getattr(profile_of(acc, False), 'account_manager', None) if acc else None) or getattr(opp, 'owner', None) or getattr(order, 'owner', None),
                                  checklist=[{'item': x, 'done': False, 'note': ''} for x in items], branch_id=getattr(opp, 'branch_id', None) or getattr(order, 'branch_id', None))
    app.event('onboarding', o.pk, 'Started for ' + (f'opportunity {opp.number}' if opp else f'sales order {order.number}'), user)
    if _emp_user(o.owner):
        app.todo(o.owner.users, f'Onboard the customer: {acc.name if acc else o.number}', 'onboarding', o.pk, o.number, app.page + f'r/onboarding/{o.pk}')
    return o


def _pre_save(sender, instance, **kw):
    if instance.pk:
        instance._zeo_old_status = sender.objects.filter(pk=instance.pk).values_list('status', flat=True).first()
    else:
        instance._zeo_old_status = None


def _opp_saved(sender, instance, created, **kw):
    try:
        if instance.status == 'won' and getattr(instance, '_zeo_old_status', None) != 'won' and CTX['app'].settings().auto_onboarding \
                and instance.pipeline.kind != 'renewal':
            start_onboarding(opp=instance)
        if instance.status == 'won' and getattr(instance, '_zeo_old_status', None) != 'won':   # a won renewal renews the contract
            for c in Contract.objects.filter(renewal_opportunity=instance, status__in=('active', 'expired')):
                renew_contract(c, None, activate=True)
    except Exception:   # noqa: BLE001
        log.exception('won opportunity follow-up')
    # v1.67.31 (screen test): the customer 360° is there and up to date right after the win (it waited for the night job)
    if instance.status == 'won' and getattr(instance, '_zeo_old_status', None) != 'won' and instance.account_id:
        from django.db import transaction
        try:
            with transaction.atomic():
                recalc_profile(profile_of(instance.account))
        except Exception:   # noqa: BLE001
            log.exception('customer 360° after a won deal')


def _quote_saved(sender, instance, created, **kw):
    q = instance
    try:
        if q.status == 'accepted' and getattr(q, '_zeo_old_status', None) != 'accepted' and q.quote_kind == 'subscription' and q.account_id \
                and not Contract.objects.filter(quotation=q).exists():
            app = CTX['app']
            months = {'monthly': 1, 'quarterly': 3, 'yearly': 12}.get(q.billing, 12)
            c = Contract.objects.create(number=app.next_number('contract', 'CTR'), title=f'Subscription – {q.account.name}'[:150], kind='subscription', account=q.account,
                                        contact=q.contact, opportunity=q.opportunity, quotation=q, sales_order=q.sales_order, owner=q.owner,
                                        value=q.total * (12 // months if months < 12 else 1), currency=q.currency, billing=q.billing if q.billing != 'once' else 'yearly',
                                        start=today(), end=add_months(today(), 12) - timedelta(days=1), auto_renew=True, terms=q.terms,
                                        branch_id=q.branch_id, industry=q.industry)
            app.event('contract', c.pk, f'Made from accepted subscription quotation {q.number}', None)
    except Exception:   # noqa: BLE001
        log.exception('subscription contract')


# ------------------------------------------------------------------ contracts
def renew_contract(c, user, activate=False):
    app = CTX['app']
    length = ((c.end - c.start).days + 1) if c.end else 365
    start = (c.end + timedelta(days=1)) if c.end else today()
    n = Contract.objects.create(number=app.next_number('contract', 'CTR'), title=c.title, kind=c.kind, account=c.account, contact=c.contact, opportunity=c.renewal_opportunity or c.opportunity,
                                owner=c.owner, value=c.renewal_opportunity.value if c.renewal_opportunity_id and c.renewal_opportunity.value else c.value,
                                currency=c.currency, billing=c.billing, start=start, end=start + timedelta(days=length - 1), auto_renew=c.auto_renew,
                                notice_days=c.notice_days, payment_terms=c.payment_terms, sla_terms=c.sla_terms, terms=c.terms, previous=c,
                                status='active' if activate else 'draft', branch_id=c.branch_id, industry=c.industry)
    if activate:
        c.status = 'renewed'
        c.save(update_fields=['status'])
    app.event('contract', c.pk, f'Renewed as {n.number}', user)
    app.event('contract', n.pk, f'Renewal of {c.number}', user)
    return n


def contract_daily(out):
    app = CTX['app']
    s = app.settings()
    t = today()
    for c in Contract.objects.filter(status='active', end__isnull=False).select_related('owner', 'account'):
        left = (c.end - t).days
        if left < 0:
            if c.auto_renew and not c.next_versions.exclude(status__in=('cancelled',)).exists():
                renew_contract(c, None, activate=True)
                out['contracts_auto_renewed'] += 1
            else:
                c.status = 'expired'
                c.save(update_fields=['status'])
                app.event('contract', c.pk, 'Expired (end date passed)', None)
                out['contracts_expired'] += 1
            continue
        done = list(c.reminded or [])
        for mark in (90, 60, 30, 7):
            if left <= mark and mark not in done and left <= max(mark, c.notice_days):
                done.append(mark)
                for u in [x for x in [_emp_user(c.owner)] if x] or app.users_with_role('crm_manager'):
                    app.todo(u, f'Contract ends in {left} days: {c.title}', 'contract', c.pk, c.number, app.page + f'r/contract/{c.pk}',
                             f'{c.account.name} – ends {c.end:%d/%m/%Y}')
                out['contract_reminders'] += 1
                break
        if done != list(c.reminded or []):
            c.reminded = done
            c.save(update_fields=['reminded'])
        if s.contract_renewal_opps and left <= max(c.notice_days, 1) and not c.renewal_opportunity_id:
            renewal_opportunity(c)
            out['renewal_opportunities'] += 1


# ------------------------------------------------------------------ complaints
def sla_hours(priority):
    s = CTX['app'].settings()
    return {'urgent': s.complaint_sla_urgent, 'high': s.complaint_sla_high, 'medium': s.complaint_sla_medium, 'low': s.complaint_sla_low}.get(priority, 72) or 72


def complaint_daily(out):
    app = CTX['app']
    leads = (app.users_with_role('crm_team_lead', fallback=False) or app.users_with_role('crm_manager')) + app.users_with_role('crm_service')
    for c in Complaint.objects.filter(status__in=('new', 'assigned', 'investigating'), due_at__lt=now()).select_related('owner'):
        c.sla_breached = True
        c.escalated = min(3, c.escalated + 1)
        c.save(update_fields=['sla_breached', 'escalated'])
        who = ([_emp_user(c.owner)] if c.escalated == 1 and _emp_user(c.owner) else []) + (leads if c.escalated >= 2 else []) + \
              (app.users_with_role('crm_manager') if c.escalated >= 3 else [])
        for u in {x.pk: x for x in who if x}.values():
            app.todo(u, f'Complaint past its SLA (level {c.escalated}): {c.subject}', 'complaint', c.pk, c.number, app.page + f'r/complaint/{c.pk}')
        app.event('complaint', c.pk, f'Escalated – level {c.escalated} (SLA {c.due_at:%d/%m/%Y %H:%M} passed)', None)
        out['complaints_escalated'] += 1


# ------------------------------------------------------------------ follow-ups
def activity_daily(out):
    app = CTX['app']
    s = app.settings()
    hrs = s.followup_escalate_hours or 24
    leads = (app.users_with_role('crm_team_lead', fallback=False) or app.users_with_role('crm_manager'))
    for a in Activity.objects.filter(status='open', due_at__lt=now(), escalated__lt=2).select_related('owner'):
        late_h = (now() - a.due_at).total_seconds() / 3600
        lvl = 2 if late_h >= 2 * hrs else 1 if late_h >= 0 else 0
        if lvl <= a.escalated:
            continue
        a.escalated = lvl
        a.save(update_fields=['escalated'])
        target = [_emp_user(a.owner)] if lvl == 1 else leads
        for u in [x for x in target if x]:
            app.todo(u, ('Missed follow-up: ' if lvl == 1 else f'Follow-up {int(late_h)} h late: ') + a.subject, 'activity', a.pk, a.number,
                     app.page + f'r/activity/{a.pk}', emp_name(a.owner) if lvl == 2 else '')
        out['followups_escalated'] += 1


def document_daily(out):
    app = CTX['app']
    t = today()
    for x in CRMDocument.objects.filter(status='approved', expiry_date__isnull=False).select_related('owner'):
        if x.expiry_date < t:
            x.status = 'expired'
            x.save(update_fields=['status'])
            app.event('document', x.pk, 'Expired', None)
            out['documents_expired'] += 1
        elif not x.reminded and (x.expiry_date - t).days <= 30:
            x.reminded = True
            x.save(update_fields=['reminded'])
            for u in [y for y in [_emp_user(x.owner)] if y] or app.users_with_role('crm_manager'):
                app.todo(u, f'Document expires {x.expiry_date:%d/%m/%Y}: {x.title}', 'document', x.pk, x.number, app.page + f'r/document/{x.pk}')
            out['document_reminders'] += 1


# ------------------------------------------------------------------ commissions
def calculate(plan, frm, to, user=None):
    app = CTX['app']
    made = 0
    if plan.basis == 'won_value':
        rows = [(o, o.owner, o.value, o.closed_at.date(), f'{o.number} {o.title}'[:80], 'opportunity')
                for o in Opportunity.objects.filter(status='won', closed_at__date__gte=frm, closed_at__date__lte=to).select_related('owner') if o.owner_id]
    elif plan.basis == 'order_value':
        rows = [(o, o.owner, o.total - o.tax_total, o.date, o.number, 'sales_order')
                for o in SalesOrder.objects.filter(date__gte=frm, date__lte=to).exclude(status='cancelled').select_related('owner') if o.owner_id]
    else:
        rows = [(o, o.owner, o.total - o.tax_total, o.decided_at.date(), o.number, 'quotation')
                for o in Quotation.objects.filter(status='accepted', decided_at__date__gte=frm, decided_at__date__lte=to).select_related('owner') if o.owner_id and o.decided_at]
    att = CTX.get('attainment')
    bonus_agents = set()
    if plan.target_bonus_pct and att:
        for t_ in SalesTarget.objects.filter(is_active=True, period_start__lte=to, period_end__gte=frm, owner__isnull=False):
            try:
                if t_.target and float(att(t_)) >= float(t_.target):
                    bonus_agents.add(t_.owner_id)
            except Exception:   # noqa: BLE001
                pass
    for src, agent, amount, dt, ref, kind in rows:
        if plan.agent_id and agent.pk != plan.agent_id:
            continue
        if plan.territory_id and getattr(src, 'territory_id', None) != plan.territory_id:
            continue
        if plan.min_amount and _d(amount) < plan.min_amount:
            continue
        if Commission.objects.filter(plan=plan, source=kind, source_id=src.pk).exists():
            continue
        rate = _d(plan.rate) + (_d(plan.target_bonus_pct) if agent.pk in bonus_agents else 0)
        Commission.objects.create(number=app.next_number('commission', 'COM'), plan=plan, agent=agent, source=kind, source_id=src.pk, source_ref=ref, date=dt,
                                  basis_amount=_d(amount), rate=rate, amount=(_d(amount) * rate / 100).quantize(Decimal('0.01')), branch_id=getattr(src, 'branch_id', None))
        made += 1
    app.event('commission_plan', plan.pk, f'{made} commission(s) calculated for {frm:%d/%m/%Y} – {to:%d/%m/%Y}', user)
    return made


# ------------------------------------------------------------------ reports (report centre, section CRM)
def reports(a):
    from ZeoApps.reports import SLUG_PAGE, _branch_pick
    codes = [a.code(r) for r, _ in a.roles]
    page = SLUG_PAGE['crm']

    def scope(kind, run):
        return _branch_pick(run, a.regs[kind].scope(run.request), 'branch')

    def bucket(days):
        return '0–7 days' if days <= 7 else '8–30 days' if days <= 30 else '31–60 days' if days <= 60 else '61–90 days' if days <= 90 else 'Over 90 days'

    BUCKETS = ['0–7 days', '8–30 days', '31–60 days', '61–90 days', 'Over 90 days']

    def ageing(kind, open_q):
        def fn(run):
            t = today()
            data = defaultdict(lambda: {b: 0 for b in BUCKETS})
            for o in scope(kind, run).filter(open_q).select_related('owner'):
                data[emp_name(o.owner) or 'Unassigned'][bucket((t - o.created_at.date()).days)] += 1
            rows = [{'agent': k, **{f'b{i}': v[b] for i, b in enumerate(BUCKETS)}, 'total': sum(v.values())} for k, v in data.items()]
            rows.sort(key=lambda r: -r['total'])
            return [('agent', 'Agent', 'text')] + [(f'b{i}', b, 'number') for i, b in enumerate(BUCKETS)] + [('total', 'Open', 'number')], rows
        return fn

    def win_loss(run):
        qs = scope('opportunity', run).filter(closed_at__date__gte=run.date_from, closed_at__date__lte=run.date_to)
        rows = []
        for x in qs.values('pipeline__name').annotate(won=Count('id', filter=Q(status='won')), lost=Count('id', filter=Q(status='lost')),
                                                      wv=Sum('value', filter=Q(status='won')), lv=Sum('value', filter=Q(status='lost'))).order_by('pipeline__name'):
            n = x['won'] + x['lost']
            rows.append({'pipeline': x['pipeline__name'] or '', 'won': x['won'], 'lost': x['lost'], 'win_rate': round(100 * x['won'] / n, 1) if n else 0,
                         'won_value': float(x['wv'] or 0), 'lost_value': float(x['lv'] or 0)})
        return [('pipeline', 'Pipeline', 'text'), ('won', 'Won', 'number'), ('lost', 'Lost', 'number'), ('win_rate', 'Win rate %', 'number'),
                ('won_value', 'Won value', 'number'), ('lost_value', 'Lost value', 'number')], rows

    def monthly(kind, date_f, flags):
        def fn(run):
            qs = scope(kind, run).filter(**{f'{date_f}__gte': run.date_from, f'{date_f}__lte': run.date_to})
            data = defaultdict(lambda: defaultdict(int))
            for o in qs:
                dv = getattr(o, date_f.split('__')[0])
                m = (dv.date() if hasattr(dv, 'date') else dv).strftime('%Y-%m')
                data[m]['total'] += 1
                for k, test in flags.items():
                    if test(o):
                        data[m][k] += 1
            rows = []
            for m in sorted(data):
                r = {'month': m, 'total': data[m]['total']}
                r.update({k: data[m][k] for k in flags})
                first = next(iter(flags))
                r['rate'] = round(100 * r[first] / r['total'], 1) if r['total'] else 0
                rows.append(r)
            return [('month', 'Month', 'text'), ('total', 'Records', 'number')] + [(k, k.replace('_', ' ').capitalize(), 'number') for k in flags] + \
                   [('rate', f'{next(iter(flags)).capitalize()} %', 'number')], rows
        return fn

    def acquisition(run):
        firsts = {}
        for acc_id, dt in Opportunity.objects.filter(status='won').exclude(account=None).values_list('account_id', 'closed_at').order_by('closed_at'):
            firsts.setdefault(acc_id, dt)
        data = defaultdict(lambda: {'new': 0, 'value': 0.0})
        for acc_id, dt in firsts.items():
            if dt and run.date_from <= dt.date() <= run.date_to:
                m = dt.strftime('%Y-%m')
                data[m]['new'] += 1
                data[m]['value'] += float(Opportunity.objects.filter(account_id=acc_id, status='won', closed_at=dt).aggregate(v=Sum('value'))['v'] or 0)
        rows = [{'month': m, 'new_customers': v['new'], 'first_deal_value': round(v['value'], 2)} for m, v in sorted(data.items())]
        return [('month', 'Month', 'text'), ('new_customers', 'New customers', 'number'), ('first_deal_value', 'First deal value', 'number')], rows

    def retention(run):
        a1, a2 = run.date_from, run.date_to
        length = (a2 - a1).days + 1
        p1, p2 = a1 - timedelta(days=length), a1 - timedelta(days=1)
        prev = set(Opportunity.objects.filter(status='won', closed_at__date__gte=p1, closed_at__date__lte=p2).exclude(account=None).values_list('account_id', flat=True))
        cur = set(Opportunity.objects.filter(status='won', closed_at__date__gte=a1, closed_at__date__lte=a2).exclude(account=None).values_list('account_id', flat=True))
        kept = prev & cur
        rows = [{'measure': 'Customers in the previous period', 'value': len(prev)}, {'measure': 'Customers in this period', 'value': len(cur)},
                {'measure': 'Kept (bought in both)', 'value': len(kept)}, {'measure': 'Lost (bought before, not now)', 'value': len(prev - cur)},
                {'measure': 'New in this period', 'value': len(cur - prev)},
                {'measure': 'Retention %', 'value': round(100 * len(kept) / len(prev), 1) if prev else 0},
                {'measure': 'Churn %', 'value': round(100 * len(prev - cur) / len(prev), 1) if prev else 0}]
        return [('measure', 'Measure', 'text'), ('value', 'Value', 'number')], rows, {'no_totals': ['value']}

    def profiles(order, flt=None):
        def fn(run):
            qs = _branch_pick(run, CustomerProfile.objects.select_related('account', 'account_manager'), 'branch')
            if flt:
                qs = qs.filter(**flt)
            rows = [{'customer': p.account.name, 'manager': emp_name(p.account_manager), 'category': p.get_category_display(), 'segment': p.segment,
                     'status': p.get_status_display(), 'ltv': float(p.lifetime_value), 'deals': p.won_count, 'open': float(p.open_value),
                     'score': p.relationship_score, 'satisfaction': float(p.satisfaction) if p.satisfaction is not None else None,
                     'churn': p.get_churn_risk_display(), 'why': p.churn_reason, 'last': d(p.last_won_at or p.last_activity_at),
                     '_url': f'{page}r/customer_profile/{p.pk}'} for p in qs.order_by(*order)[:2000]]
            return [('customer', 'Customer', 'text'), ('manager', 'Account manager', 'text'), ('category', 'Category', 'text'), ('segment', 'Segment', 'text'),
                    ('status', 'Status', 'text'), ('ltv', 'Lifetime value', 'number'), ('deals', 'Won deals', 'number'), ('open', 'Open pipeline', 'number'),
                    ('score', 'Relationship score', 'number'), ('satisfaction', 'Satisfaction (1–5)', 'number'), ('churn', 'Churn risk', 'text'),
                    ('why', 'Why', 'text'), ('last', 'Last business', 'date')], rows, {'filters': ['manager', 'segment', 'churn', 'status'], 'no_totals': ['score']}
        return fn

    def followups(run):
        t = now()
        data = defaultdict(lambda: {'open': 0, 'overdue': 0, 'today': 0, 'week': 0, 'done': 0})
        qs = scope('activity', run)
        for a_ in qs.filter(status='open').select_related('owner'):
            k = emp_name(a_.owner) or 'Unassigned'
            data[k]['open'] += 1
            if a_.due_at and a_.due_at < t:
                data[k]['overdue'] += 1
            elif a_.due_at and a_.due_at.date() == t.date():
                data[k]['today'] += 1
            elif a_.due_at and a_.due_at.date() <= t.date() + timedelta(days=7):
                data[k]['week'] += 1
        for a_ in qs.filter(status='done', done_at__date__gte=run.date_from, done_at__date__lte=run.date_to).select_related('owner'):
            data[emp_name(a_.owner) or 'Unassigned']['done'] += 1
        rows = [{'agent': k, **v} for k, v in data.items()]
        rows.sort(key=lambda r: -r['overdue'])
        return [('agent', 'Agent', 'text'), ('open', 'Open', 'number'), ('overdue', 'Overdue', 'number'), ('today', 'Due today', 'number'),
                ('week', 'Due in 7 days', 'number'), ('done', 'Done in the period', 'number')], rows

    def forecast_by(group):
        def fn(run):
            qs = scope('opportunity', run).filter(status='open').select_related('stage', 'owner', 'territory', 'pipeline')
            data = defaultdict(lambda: {'open': 0, 'value': 0.0, 'weighted': 0.0, 'best': 0.0})
            for o in qs:
                if group == 'quarter':
                    k = f'{o.expected_close.year} Q{(o.expected_close.month - 1) // 3 + 1}' if o.expected_close else 'No close date'
                elif group == 'year':
                    k = str(o.expected_close.year) if o.expected_close else 'No close date'
                elif group == 'territory':
                    k = o.territory.name if o.territory_id else 'No territory'
                elif group == 'industry':
                    from .industry import pack_label
                    k = pack_label(o.industry) or 'No industry'
                else:
                    k = o.pipeline.name
                p = o.stage.probability if o.stage_id else 0
                v = float(o.value or 0)
                data[k]['open'] += 1
                data[k]['value'] += v
                data[k]['weighted'] += v * p / 100
                if p >= 50:
                    data[k]['best'] += v
            rows = [{'group': k, **{x: round(y, 2) for x, y in v.items()}} for k, v in data.items()]
            rows.sort(key=lambda r: r['group'])
            return [('group', group.capitalize(), 'text'), ('open', 'Open opportunities', 'number'), ('value', 'Pipeline value', 'number'),
                    ('weighted', 'Weighted forecast', 'number'), ('best', 'Likely (≥ 50 %)', 'number')], rows
        return fn

    def forecast_product(run):
        from .models import QuotationLine
        qs = QuotationLine.objects.filter(quotation__in=scope('quotation', run).filter(status__in=('draft', 'approval', 'approved', 'sent')),
                                          quotation__opportunity__status='open').select_related('product', 'quotation__opportunity__stage')
        data = defaultdict(lambda: {'qty': 0.0, 'value': 0.0, 'weighted': 0.0})
        for ln in qs:
            k = ln.product.name if ln.product_id else (ln.description or 'Other')[:60]
            p = ln.quotation.opportunity.stage.probability if ln.quotation.opportunity_id and ln.quotation.opportunity.stage_id else 0
            data[k]['qty'] += float(ln.qty or 0)
            data[k]['value'] += float(ln.net or 0)
            data[k]['weighted'] += float(ln.net or 0) * p / 100
        rows = [{'product': k, **{x: round(y, 2) for x, y in v.items()}} for k, v in data.items()]
        rows.sort(key=lambda r: -r['weighted'])
        return [('product', 'Product', 'text'), ('qty', 'Quoted quantity', 'number'), ('value', 'Quoted value', 'number'), ('weighted', 'Weighted forecast', 'number')], rows

    def complaints(run):
        qs = scope('complaint', run).filter(created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
        rows = []
        for x in qs.values('category', 'priority').annotate(n=Count('id'), open=Count('id', filter=~Q(status__in=('closed', 'cancelled', 'resolved'))),
                                                            late=Count('id', filter=Q(sla_breached=True))).order_by('category', 'priority'):
            rating = qs.filter(category=x['category'], priority=x['priority'], rating__isnull=False).values_list('rating', flat=True)
            rows.append({'category': dict(Complaint._meta.get_field('category').choices)[x['category']], 'priority': x['priority'].capitalize(), 'complaints': x['n'],
                         'open': x['open'], 'sla_breached': x['late'], 'sla_pct': round(100 * (x['n'] - x['late']) / x['n'], 1) if x['n'] else 0,
                         'rating': round(sum(rating) / len(rating), 2) if rating else None})
        return [('category', 'Category', 'text'), ('priority', 'Priority', 'text'), ('complaints', 'Complaints', 'number'), ('open', 'Still open', 'number'),
                ('sla_breached', 'Past SLA', 'number'), ('sla_pct', 'Within SLA %', 'number'), ('rating', 'Average rating', 'number')], rows

    def contracts_exp(run):
        t = today()
        rows = []
        for c in scope('contract', run).filter(status='active', end__isnull=False, end__lte=t + timedelta(days=120)).select_related('account', 'owner').order_by('end'):
            rows.append({'number': c.number, 'customer': c.account.name, 'type': c.get_kind_display(), 'value': float(c.value), 'end': d(c.end), 'days': (c.end - t).days,
                         'auto': 'Yes' if c.auto_renew else 'No', 'renewal': c.renewal_opportunity.number if c.renewal_opportunity_id else '', 'manager': emp_name(c.owner),
                         '_url': f'{page}r/contract/{c.pk}'})
        return [('number', 'Contract', 'text'), ('customer', 'Customer', 'text'), ('type', 'Type', 'text'), ('value', 'Value', 'number'), ('end', 'Ends', 'date'),
                ('days', 'Days left', 'number'), ('auto', 'Auto-renew', 'text'), ('renewal', 'Renewal opportunity', 'text'), ('manager', 'Account manager', 'text')], rows, \
            {'no_totals': ['days']}

    def commissions(run):
        qs = scope('commission', run).filter(date__gte=run.date_from, date__lte=run.date_to).select_related('agent', 'plan')
        data = defaultdict(lambda: {'deals': 0, 'basis': 0.0, 'draft': 0.0, 'approved': 0.0, 'paid': 0.0})
        for c in qs.exclude(status='cancelled'):
            k = (emp_name(c.agent), c.plan.name)
            data[k]['deals'] += 1
            data[k]['basis'] += float(c.basis_amount)
            data[k][c.status] += float(c.amount)
        rows = [{'agent': k[0], 'plan': k[1], **{x: round(y, 2) for x, y in v.items()}} for k, v in data.items()]
        return [('agent', 'Agent', 'text'), ('plan', 'Plan', 'text'), ('deals', 'Deals', 'number'), ('basis', 'Sales value', 'number'), ('draft', 'To approve', 'number'),
                ('approved', 'Approved', 'number'), ('paid', 'Paid', 'number')], rows

    def onboarding(run):
        rows = []
        for o in scope('onboarding', run).filter(start__gte=run.date_from, start__lte=run.date_to).select_related('account', 'owner'):
            n = len(o.checklist or [])
            done = sum(1 for x in (o.checklist or []) if x.get('done'))
            rows.append({'number': o.number, 'customer': o.account.name if o.account_id else '', 'manager': emp_name(o.owner), 'start': d(o.start), 'target': d(o.target_date),
                         'status': o.get_status_display(), 'progress': round(100 * done / n) if n else 0,
                         'late': 'Yes' if o.status in ('open', 'in_progress') and o.target_date and o.target_date < today() else '', '_url': f'{page}r/onboarding/{o.pk}'})
        return [('number', 'Onboarding', 'text'), ('customer', 'Customer', 'text'), ('manager', 'Account manager', 'text'), ('start', 'Started', 'date'),
                ('target', 'To finish by', 'date'), ('status', 'Status', 'text'), ('progress', 'Done %', 'number'), ('late', 'Late', 'text')], rows, {'no_totals': ['progress']}

    def enquiries(run):
        qs = scope('enquiry', run).filter(date__gte=run.date_from, date__lte=run.date_to)
        rows = []
        for x in qs.values('classification', 'status').annotate(n=Count('id'), v=Sum('estimated_value')).order_by('classification', 'status'):
            rows.append({'classification': dict(Enquiry._meta.get_field('classification').choices)[x['classification']],
                         'status': dict(Enquiry._meta.get_field('status').choices)[x['status']], 'enquiries': x['n'], 'value': float(x['v'] or 0)})
        return [('classification', 'Classification', 'text'), ('status', 'Status', 'text'), ('enquiries', 'Enquiries', 'number'), ('value', 'Estimated value', 'number')], rows

    def campaign_roi(run):
        from .models import Campaign
        rows = []
        for c in Campaign.objects.filter(is_active=True):
            leads = Lead.objects.filter(campaign=c, created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
            opps = Opportunity.objects.filter(campaign=c, created_at__date__gte=run.date_from, created_at__date__lte=run.date_to)
            won = float(opps.filter(status='won').aggregate(v=Sum('value'))['v'] or 0)
            n_leads = leads.count()
            cost = float(c.budget or 0)
            spent = float(getattr(c, 'spent', 0) or 0) or cost
            rows.append({'campaign': c.name, 'type': c.get_kind_display(), 'budget': cost, 'leads': n_leads, 'converted': leads.filter(status='converted').count(),
                         'opportunities': opps.count(), 'won': opps.filter(status='won').count(), 'won_value': won,
                         'cost_per_lead': round(spent / n_leads, 2) if n_leads and spent else None, 'roi': round(100 * (won - spent) / spent, 1) if spent else None})
        rows.sort(key=lambda r: -(r['won_value'] or 0))
        return [('campaign', 'Campaign', 'text'), ('type', 'Type', 'text'), ('budget', 'Cost', 'number'), ('leads', 'Leads', 'number'), ('converted', 'Converted', 'number'),
                ('opportunities', 'Opportunities', 'number'), ('won', 'Won', 'number'), ('won_value', 'Won value', 'number'), ('cost_per_lead', 'Cost per lead', 'number'),
                ('roi', 'ROI %', 'number')], rows, {'no_totals': ['cost_per_lead', 'roi']}

    lead_open = ~Q(status__in=('converted', 'disqualified'))
    return {
        'crm-lead-ageing': ('Lead ageing', 'CRM', 'Open leads by age (days since created) per agent.', ageing('lead', lead_open), None, codes),
        'crm-opportunity-ageing': ('Opportunity ageing', 'CRM', 'Open opportunities by age per agent.', ageing('opportunity', Q(status='open')), None, codes),
        'crm-win-loss': ('Opportunity win / loss', 'CRM', 'Won and lost per pipeline in the period with win rate and values.', win_loss, 'year', codes),
        'crm-lead-conversion': ('Lead conversion ratio', 'CRM', 'Leads created per month, converted and disqualified, conversion %.',
                                monthly('lead', 'created_at__date', {'converted': lambda o: o.status == 'converted', 'disqualified': lambda o: o.status == 'disqualified'}), 'year', codes),
        'crm-quotation-conversion': ('Quotation conversion', 'CRM', 'Quotations per month, accepted and rejected / expired, acceptance %.',
                                     monthly('quotation', 'date', {'accepted': lambda o: o.status == 'accepted', 'rejected': lambda o: o.status in ('rejected', 'expired', 'cancelled')}), 'year', codes),
        'crm-customer-acquisition': ('Customer acquisition', 'CRM', 'New customers (first won deal) per month and the value of their first deal.', acquisition, 'year', codes),
        'crm-customer-retention': ('Customer retention', 'CRM', 'Customers who bought in the previous period and again in this one: retention and churn %.', retention, 'year', codes),
        'crm-customer-churn': ('Customers at churn risk', 'CRM', 'Customers with a medium or high churn risk and why (Customer profiles, worked out daily).',
                               profiles(('-churn_risk', 'relationship_score'), {'churn_risk__in': ('medium', 'high')}), None, codes),
        'crm-customer-value': ('Customer lifetime value', 'CRM', 'Every customer with lifetime value, won deals, open pipeline, relationship score and segment.',
                               profiles(('-lifetime_value',)), None, codes),
        'crm-followups': ('Follow-up pending', 'CRM', 'Open follow-ups per agent: overdue, due today, due this week; done in the period.', followups, 'month', codes),
        'crm-forecast-quarter': ('Sales forecast by quarter', 'CRM', 'Open pipeline by expected close quarter: value, weighted, likely.', forecast_by('quarter'), None, codes),
        'crm-forecast-year': ('Sales forecast by year', 'CRM', 'Open pipeline by expected close year.', forecast_by('year'), None, codes),
        'crm-forecast-territory': ('Sales forecast by territory', 'CRM', 'Open pipeline by territory.', forecast_by('territory'), None, codes),
        'crm-forecast-industry': ('Sales forecast by industry', 'CRM', 'Open pipeline by industry.', forecast_by('industry'), None, codes),
        'crm-forecast-pipeline': ('Sales forecast by pipeline', 'CRM', 'Open pipeline by pipeline (team / business type).', forecast_by('pipeline'), None, codes),
        'crm-forecast-product': ('Sales forecast by product', 'CRM', 'Products on open quotations of open opportunities, weighted by stage probability.', forecast_product, None, codes),
        'crm-complaints': ('Complaints and SLA', 'CRM', 'Complaints in the period by category and priority: open, past SLA, within SLA %, rating.', complaints, 'year', codes),
        'crm-contracts-expiring': ('Contracts ending (120 days)', 'CRM', 'Active contracts ending in the next 120 days with their renewal opportunity.', contracts_exp, None, codes),
        'crm-commissions': ('Sales commissions', 'CRM', 'Commissions in the period per agent and plan: to approve, approved, paid.', commissions, 'year', codes),
        'crm-onboarding': ('Customer onboarding', 'CRM', 'Onboardings started in the period with progress and lateness.', onboarding, 'year', codes),
        'crm-enquiries': ('Enquiries by status', 'CRM', 'Enquiries in the period by classification and status with estimated value.', enquiries, 'year', codes),
        'crm-campaign-roi': ('Campaign ROI', 'CRM', 'Campaigns: cost, leads, conversions, won value, cost per lead and return on investment.', campaign_roi, 'year', codes),
    }


# ------------------------------------------------------------------ registers and actions
def setup(app, MANAGE, VIEW, can_work, PAGE, SALES):
    CTX['app'] = app
    CTX['attainment'] = SALES.get('attainment')
    ALL_VIEW = VIEW + ('crm_director',)
    SETUP_VIEW = MANAGE + VIEW + ('crm_agent', 'crm_director', 'crm_presales', 'crm_account', 'crm_service', 'crm_marketing')
    ACCT = ('crm_account',)
    regs = app.regs

    def mine(o, request):
        me = app.my_emp(request)
        return app.any(request, *MANAGE, 'crm_director') or (me is not None and getattr(o, 'owner_id', None) == me.pk)

    # ---------------------------------------------------------------- customer profiles and sites
    def a_block(status_):
        def run(o, request, f):
            o.status, o.block_reason, o.blocked_by, o.blocked_at = status_, f.s('reason')[:200], request.user, now()
            o.save()
            app.event('customer_profile', o.pk, f'{o.get_status_display()} by {user_name(request.user)} – {o.block_reason}', request.user)
            return o
        return run

    def a_unblock(o, request, f):
        o.status, o.block_reason = 'active', ''
        o.save()
        app.event('customer_profile', o.pk, f'Released by {user_name(request.user)}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_recalc(o, request, f):
        recalc_profile(o)
        return o

    def boss(o, r):
        return app.any(r, 'crm_manager', 'crm_director')

    app.option('customer-profiles', lambda: CustomerProfile.objects.select_related('account'), lambda x: str(x.account), ('account__name',), CustomerProfile)
    app.reg(Reg('customer_profile', CustomerProfile, 'Customer profile', 'Customer profiles', 'Customers',
                cols=('account', 'category', 'segment', 'risk', 'status', 'account_manager', 'lifetime_value', 'relationship_score', 'churn_risk', 'last_activity_at'),
                form=('account', 'category', 'risk', 'account_manager', 'preferences'),
                manage=MANAGE + ('crm_director',) + ACCT, view=ALL_VIEW + ('crm_agent', 'crm_service', 'crm_presales', 'crm_marketing'), create=('crm_agent',),
                own=(('account_manager', 'emp'),), own_edit=True, search=('account__name', 'account__number'),
                filters=('category', 'segment', 'risk', 'status', 'churn_risk', 'account_manager'), title='account', subtitle='category',
                info=('segment', 'status', 'block_reason', 'blocked_by', 'blocked_at', 'lifetime_value', 'won_count', 'open_value', 'last_won_at', 'last_activity_at',
                      'open_complaints', 'active_contracts', 'relationship_score', 'satisfaction', 'churn_risk', 'churn_reason', 'calculated_at'),
                actions=[Act('recalc', 'Recalculate figures', a_recalc, lambda o, r: True),
                         Act('block', 'Block (no new business)', a_block('blocked'), lambda o, r: o.status != 'blocked' and boss(o, r), style='danger',
                             fields=(('reason', 'Reason', 'text', True),)),
                         Act('blacklist', 'Blacklist', a_block('blacklisted'), lambda o, r: o.status != 'blacklisted' and boss(o, r), style='danger',
                             fields=(('reason', 'Reason', 'text', True),)),
                         Act('release', 'Release (active again)', a_unblock, lambda o, r: o.status in ('blocked', 'blacklisted', 'inactive') and boss(o, r),
                             fields=(('note', 'Note', 'text', False),))],
                extra=lambda o, r: {'links': {   # customer 360°: what the customer has with us
                    'opportunities': Opportunity.objects.filter(account=o.account).count(), 'quotations': Quotation.objects.filter(account=o.account).count(),
                    'sales orders': SalesOrder.objects.filter(account=o.account).count(), 'contracts': Contract.objects.filter(account=o.account).count(),
                    'complaints': Complaint.objects.filter(account=o.account).count(), 'enquiries': Enquiry.objects.filter(account=o.account).count(),
                    'sites': CustomerSite.objects.filter(account=o.account).count()}},
                help='Customer 360°: category, risk, status (only a CRM manager or director blocks or blacklists a customer), account manager, preferences; '
                     'lifetime value, relationship score, satisfaction and churn risk are worked out every night.'))
    app.reg(Reg('customer_site', CustomerSite, 'Customer site', 'Customer sites', 'Customers',
                cols=('account', 'name', 'kind', 'city', 'country', 'contact', 'phone', 'is_active'),
                form=('account', 'name', 'kind', 'address', 'city', 'country', 'contact', 'phone', 'is_active'),
                manage=MANAGE + ACCT, view=SETUP_VIEW, create=('crm_agent', 'crm_service'), search=('name', 'account__name', 'city'), filters=('kind', 'is_active'),
                title='name', subtitle='account', files=False, deletable=True, help='Branches, locations, billing and delivery addresses of a customer.'))

    # ---------------------------------------------------------------- enquiries
    def enq_defaults(o, request):
        o.date = o.date or today()
        if not o.owner_id:
            o.owner = (o.opportunity.owner if o.opportunity_id else None) or (o.lead.owner if o.lead_id else None) or app.my_emp(request)
        if o.opportunity_id:
            o.account = o.account or o.opportunity.account
            o.contact = o.contact or o.opportunity.contact
        if o.lead_id:
            o.contact = o.contact or o.lead.contact
            o.account = o.account or o.lead.account
            o.requirement = o.requirement or o.lead.requirements
            o.estimated_value = o.estimated_value or o.lead.value

    def enq_check(o, request, old):
        if not (o.account_id or o.contact_id or o.lead_id):
            raise AppError('Choose the customer: an account, a contact or a lead.', 'account')

    def a_enq_submit(o, request, f):
        if o.technical_owner_id:
            o.status = 'evaluation'
            if _emp_user(o.technical_owner):
                app.todo(o.technical_owner.users, f'Technical evaluation: {o.title}', 'enquiry', o.pk, o.number, PAGE + f'r/enquiry/{o.pk}')
        else:
            o.status = 'approval' if app.settings().enquiry_approval else 'approved'
            if o.status == 'approval':
                for u in app.users_with_role('crm_manager') + (app.users_with_role('crm_team_lead', fallback=False) or app.users_with_role('crm_manager')):
                    app.todo(u, f'Approve enquiry: {o.title}', 'enquiry', o.pk, o.number, PAGE + f'r/enquiry/{o.pk}')
        o.save()
        app.event('enquiry', o.pk, f'Submitted → {o.get_status_display()}', request.user)
        return o

    def a_enq_evaluate(o, request, f):
        o.evaluation = f.choice('evaluation', Enquiry._meta.get_field('evaluation').choices, 'feasible')
        o.evaluation_notes = (o.evaluation_notes + '\n' if o.evaluation_notes else '') + f'{today():%d/%m/%Y} {user_name(request.user)}: {f.s("notes")}'.strip()
        if o.evaluation == 'not_feasible':
            o.status = 'closed'
            o.close_reason = 'Not technically feasible'
        else:
            o.status = 'approval' if app.settings().enquiry_approval else 'approved'
        o.save()
        app.close_todos('enquiry', o.pk, 'Technical evaluation')
        app.event('enquiry', o.pk, f'Technical evaluation: {o.get_evaluation_display()}' + (f' – {f.s("notes")}' if f.s('notes') else ''), request.user)
        if o.status == 'approval':
            for u in app.users_with_role('crm_manager') + (app.users_with_role('crm_team_lead', fallback=False) or app.users_with_role('crm_manager')):
                app.todo(u, f'Approve enquiry: {o.title}', 'enquiry', o.pk, o.number, PAGE + f'r/enquiry/{o.pk}')
        return o

    def a_enq_ask(o, request, f):
        o.clarification = (o.clarification + '\n' if o.clarification else '') + f'Q {today():%d/%m/%Y} {user_name(request.user)}: {f.s("question")}'
        o.status = 'clarification'
        o.save()
        app.event('enquiry', o.pk, f'Clarification asked: {f.s("question")}', request.user)
        if _emp_user(o.owner):
            app.todo(o.owner.users, f'Clarify enquiry: {o.title}', 'enquiry', o.pk, o.number, PAGE + f'r/enquiry/{o.pk}', f.s('question'))
        return o

    def a_enq_answer(o, request, f):
        o.clarification = (o.clarification + '\n' if o.clarification else '') + f'A {today():%d/%m/%Y} {user_name(request.user)}: {f.s("answer")}'
        o.status = 'evaluation' if o.technical_owner_id else 'draft'
        o.save()
        app.close_todos('enquiry', o.pk, 'Clarify')
        app.event('enquiry', o.pk, f'Clarified: {f.s("answer")}', request.user)
        return o

    def a_enq_approve(o, request, f):
        o.status, o.approved_by, o.approved_at = 'approved', request.user, now()
        o.save()
        app.close_todos('enquiry', o.pk, 'Approve')
        app.event('enquiry', o.pk, 'Approved' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_enq_quote(o, request, f):
        from .industry import carry
        opp = o.opportunity
        if opp is None:
            contact = o.contact or (o.lead.contact if o.lead_id else None)
            if contact is None:
                if not o.account_id:
                    raise AppError('Choose a contact or an account first.', 'contact')
                contact = Contact.objects.filter(account=o.account, is_active=True).first() or Contact.objects.create(
                    number=app.next_number('contact', 'CON'), name=o.account.name, kind='company', company_name=o.account.name, account=o.account, branch_id=o.branch_id)
            pipe = Pipeline.objects.filter(is_active=True, industry=o.industry).order_by('order').first() if o.industry else None
            pipe = pipe or Pipeline.objects.filter(is_active=True, industry='').order_by('order').first() or Pipeline.objects.filter(is_active=True).order_by('order').first()
            opp = Opportunity(number=app.next_number('opportunity', 'OPP'), title=o.title, contact=contact, account=o.account, pipeline=pipe, owner=o.owner,
                              value=o.estimated_value, expected_close=o.expected_date, requirements=o.requirement, technical_requirements=o.technical_specs,
                              branch_id=o.branch_id, industry=pipe.industry or o.industry, lead=o.lead)
            opp.stage = pipe.stages.filter(is_active=True, is_won=False, is_lost=False).order_by('order').first()
            carry(o, opp, 'opportunity')
            opp.save()
            o.opportunity = opp
            app.event('opportunity', opp.pk, f'Made from enquiry {o.number}', request.user)
        from ZeoMasters.models import PaymentTerm, Product
        lines = []
        for it in (o.items or []):
            p = Product.objects.filter(pk=it.get('product')).first() if it.get('product') else None
            lines.append({'product': p.pk if p else None, 'description': it.get('description') or (p.name if p else ''), 'qty': it.get('qty') or 1,
                          'uom': it.get('uom') or '', 'unit_price': None if p else 0, 'discount_pct': 0, 'tax': None})   # price book / list price and tax of the product
        q = Quotation(number=app.next_number('quotation', 'QT'), opportunity=opp, contact=opp.contact, account=o.account or opp.account, owner=o.owner or opp.owner,
                      date=today(), valid_until=today() + timedelta(days=app.settings().quote_validity_days or 30), currency=app.settings().currency,
                      terms=app.settings().quote_terms, branch_id=o.branch_id, reference=o.number, scope_of_work=o.requirement,
                      quote_kind={'service': 'service', 'project': 'project', 'subscription': 'subscription'}.get(o.classification, 'product'),
                      payment_term=PaymentTerm.objects.filter(is_default=True, is_active=True).first(), items=lines)
        carry(o, q, 'quotation')
        qreg = regs['quotation']
        q = qreg.saver(q, request, True, None) or q
        o.quotation, o.status = q, 'quoted'
        o.save()
        app.event('enquiry', o.pk, f'Quotation {q.number} made', request.user)
        app.event('quotation', q.pk, f'Made from enquiry {o.number}', request.user)
        return {'message': f'Quotation {q.number} made – check the prices.', 'created': {'app': 'zeo-crm', 'kind': 'quotation', 'id': q.pk}}

    def a_enq_revise(o, request, f):
        o.revision += 1
        o.status = 'draft'
        o.evaluation = 'pending'
        o.save()
        app.event('enquiry', o.pk, f'Revision {o.revision}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_enq_close(o, request, f):
        o.status, o.close_reason = 'closed', f.s('reason')[:200]
        o.save()
        app.close_todos('enquiry', o.pk)
        app.event('enquiry', o.pk, f'Closed – {o.close_reason}', request.user)
        return o

    EVAL = 'choice:feasible=Feasible|feasible_changes=Feasible with changes|not_feasible=Not feasible'
    is_tech = lambda o, r: app.any(r, *MANAGE, 'crm_presales', 'crm_director') or getattr(app.my_emp(r), 'pk', 0) == o.technical_owner_id   # noqa: E731
    app.option('enquiries', lambda: Enquiry.objects.all(), lambda x: f'{x.number} {x.title}', ('number', 'title'), Enquiry)
    app.reg(Reg('enquiry', Enquiry, 'Enquiry', 'Enquiries', 'Sales',
                cols=('number', 'date', 'title', 'account', 'classification', 'owner', 'technical_owner', 'estimated_value', 'evaluation', 'status'),
                form=('title', 'date', 'account', 'contact', 'lead', 'opportunity', 'classification', 'items', 'requirement', 'technical_specs', 'quantity',
                      'estimated_value', 'budget', 'expected_date', 'site_visit', 'site_visit_date', 'owner', 'technical_owner'),
                number=('enquiry', 'ENQ'), manage=MANAGE + ('crm_director',), view=ALL_VIEW, create=('crm_agent', 'crm_presales', 'crm_leadgen', 'crm_account'),
                own=(('owner', 'emp'), ('technical_owner', 'emp')), own_edit=True, search=('number', 'title', 'account__name', 'contact__name', 'requirement'),
                filters=('status', 'classification', 'evaluation', 'owner', 'technical_owner', 'site_visit'), title='title', subtitle='account',
                defaults=enq_defaults, check=enq_check, edit_when=lambda o: o.status in ('draft', 'evaluation', 'clarification', 'approval'),
                info=('status', 'revision', 'evaluation', 'evaluation_notes', 'clarification', 'approved_by', 'approved_at', 'quotation', 'close_reason'),
                tables={'items': [('product', 'Product / service', 'opt', 'products'), ('description', 'Description', 'text'), ('qty', 'Qty', 'number'),
                                  ('uom', 'Unit', 'text')]},
                actions=[
                    Act('submit', 'Submit (evaluation / approval)', a_enq_submit, lambda o, r: o.status == 'draft' and (mine(o, r) or app.any(r, 'crm_agent')), style='primary'),
                    Act('evaluate', 'Technical evaluation', a_enq_evaluate, lambda o, r: o.status == 'evaluation' and is_tech(o, r), style='primary',
                        fields=(('evaluation', 'Result', EVAL, True), ('notes', 'Notes', 'textarea', False))),
                    Act('ask', 'Ask for clarification', a_enq_ask, lambda o, r: o.status in ('evaluation', 'approval') and (is_tech(o, r) or app.any(r, *MANAGE)),
                        fields=(('question', 'Question', 'textarea', True),)),
                    Act('answer', 'Answer the clarification', a_enq_answer, lambda o, r: o.status == 'clarification' and (mine(o, r) or app.any(r, 'crm_agent')),
                        fields=(('answer', 'Answer', 'textarea', True),)),
                    Act('approve', 'Approve', a_enq_approve, lambda o, r: o.status == 'approval' and app.any(r, *MANAGE, 'crm_director'), style='primary',
                        fields=(('note', 'Note', 'text', False),)),
                    Act('quote', 'Make the quotation', a_enq_quote, lambda o, r: o.status == 'approved' and (mine(o, r) or app.any(r, 'crm_agent', 'crm_presales')), style='primary'),
                    Act('revise', 'Make a revision', a_enq_revise, lambda o, r: o.status in ('approval', 'approved', 'clarification', 'evaluation') and (mine(o, r) or app.any(r, 'crm_agent')),
                        fields=(('note', 'What changes', 'text', False),)),
                    Act('close', 'Close', a_enq_close, lambda o, r: o.status not in ('closed', 'cancelled', 'quoted') and (mine(o, r) or app.any(r, *MANAGE)), style='danger',
                        fields=(('reason', 'Reason', 'text', True),)),
                ],
                help='Customer requirements before quoting: products / services, specifications, site visit, technical evaluation, clarifications, approval '
                     '(CRM setup › "Enquiries need approval"), then the quotation.'))
    for k in ('lead', 'opportunity'):
        r_ = regs[k]
        r_.children = tuple(r_.children) + (('Enquiries', 'enquiry', k),)

    # ---------------------------------------------------------------- onboarding
    def onb_progress(o):
        n = len(o.checklist or [])
        return f'{sum(1 for x in (o.checklist or []) if x.get("done"))} / {n}' if n else '–'

    def onb_saver(o, request, creating, old):
        if o.status == 'open' and any(x.get('done') for x in (o.checklist or [])):
            o.status = 'in_progress'
        o.save()
        return o

    def a_onb_welcome(o, request, f):
        c = o.contact
        if not (c and c.email):
            raise AppError('The contact has no e-mail.', 'contact')
        name = getattr(o.account, 'name', c.name)
        body = f.s('message') or (f'Dear {c.name},\n\nWelcome to us! Thank you for choosing us. {emp_name(o.owner) or "Your account manager"} is your account manager '
                                  f'and will guide you through the next steps.\n\nKind regards')
        app.send_mail([c.email], f.s('subject') or f'Welcome, {name}', body)
        o.welcome_sent = True
        _tick(o, 'welcome')
        o.save()
        app.event('onboarding', o.pk, f'Welcome message sent to {c.email}', request.user)
        return o

    def _tick(o, word):
        for x in o.checklist or []:
            if word in (x.get('item') or '').lower():
                x['done'] = True

    def a_onb_activate(o, request, f):
        if o.account_id:
            from ZeoMasters.models import Account
            Account.objects.filter(pk=o.account_id).update(kind='customer', is_active=True)
            p = profile_of(o.account)
            if p.status == 'inactive':
                p.status = 'active'
                p.save(update_fields=['status'])
        o.account_activated = True
        _tick(o, 'account activated')
        o.save()
        app.event('onboarding', o.pk, 'Customer account activated', request.user)
        return o

    def a_onb_complete(o, request, f):
        left = [x['item'] for x in (o.checklist or []) if not x.get('done')]
        if left and not f.b('force'):
            raise AppError('Still open: ' + '; '.join(left[:5]) + ('…' if len(left) > 5 else '') + ' – tick them or choose "Complete anyway".')
        o.status, o.completed_at = 'completed', now()
        o.save()
        app.close_todos('onboarding', o.pk)
        app.event('onboarding', o.pk, 'Completed' + (' (with open steps)' if left else ''), request.user)
        return o

    app.reg(Reg('onboarding_template', OnboardingTemplate, 'Onboarding checklist', 'Onboarding checklists', 'Setup',
                cols=('code', 'name', 'industry', 'is_default', 'is_active', 'review_status'), form=('code', 'name', 'industry', 'items', 'is_default', 'is_active'),
                manage=('crm_manager',), view=SETUP_VIEW, search=('code', 'name'), filters=('industry', 'is_active'), title='name', review=True, deletable=True, files=False,
                help='The steps of customer onboarding per industry (one per line). The default one is used when no industry checklist exists.'))
    # v1.67.31 (screen test): the pick list of the Template field and filter
    app.option('onboarding_template', lambda: OnboardingTemplate.objects.all(), lambda x: x.name, ('name',), OnboardingTemplate)
    app.reg(Reg('onboarding', Onboarding, 'Onboarding', 'Customer onboarding', 'Customers',
                cols=('number', 'account', 'owner', 'start', 'target_date', 'status'),
                form=('account', 'contact', 'opportunity', 'sales_order', 'template', 'owner', 'start', 'target_date', 'checklist', 'kyc_done', 'contract_uploaded',
                      'credit_approved', 'account_activated', 'service_activated', 'handover_done', 'training_done', 'welcome_sent', 'notes'),
                number=('onboarding', 'ONB'), manage=MANAGE + ('crm_director',) + ACCT, view=ALL_VIEW + ('crm_service',), create=('crm_agent',),
                own=(('owner', 'emp'),), own_edit=True, search=('number', 'account__name'), filters=('status', 'owner'), title='number', subtitle='account',
                defaults=lambda o, r: setattr(o, 'start', o.start or today()), saver=onb_saver, edit_when=lambda o: o.status in ('open', 'in_progress'),
                info=('status', 'completed_at'),
                tables={'checklist': [('item', 'Step', 'text'), ('done', 'Done', 'bool'), ('note', 'Note', 'text')]},
                actions=[Act('welcome', 'Send the welcome e-mail', a_onb_welcome, lambda o, r: o.status in ('open', 'in_progress') and not o.welcome_sent,
                             fields=(('subject', 'Subject', 'text', False), ('message', 'Message (empty = standard)', 'textarea', False))),
                         Act('activate', 'Activate the customer account', a_onb_activate, lambda o, r: o.status in ('open', 'in_progress') and not o.account_activated),
                         Act('complete', 'Complete', a_onb_complete, lambda o, r: o.status in ('open', 'in_progress'), style='primary',
                             fields=(('force', 'Complete anyway (steps still open)', 'bool', False),)),
                         Act('cancel', 'Cancel', lambda o, r, f: _set(o, r, 'onboarding', 'cancelled', 'Cancelled', f), lambda o, r: o.status in ('open', 'in_progress'),
                             style='danger', fields=(('note', 'Why', 'text', False),))],
                extra=lambda o, r: {'links': {'progress': onb_progress(o)}},
                help='After a won deal: the checklist of the industry, KYC, contract, credit, activation, handover, training and the welcome message.'))

    def _set(o, request, key, status_, text, f):
        o.status = status_
        o.save()
        app.close_todos(key, o.pk)
        app.event(key, o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    # ---------------------------------------------------------------- contracts
    def ctr_defaults(o, request):
        o.start = o.start or today()
        if o.opportunity_id:
            o.account_id = o.account_id or o.opportunity.account_id
            o.contact = o.contact or o.opportunity.contact
            o.value = o.value or o.opportunity.value
            o.industry = o.industry or o.opportunity.industry
        o.owner = o.owner or app.my_emp(request)
        o.currency = o.currency or app.settings().currency

    def ctr_check(o, request, old):
        if o.end and o.end < o.start:
            raise AppError('The end date is before the start.', 'end')

    def a_ctr_submit(o, request, f):
        o.status = 'approval'
        o.save()
        for u in app.users_with_role('crm_manager') + app.users_with_role('crm_director'):
            app.todo(u, f'Approve contract: {o.title}', 'contract', o.pk, o.number, PAGE + f'r/contract/{o.pk}')
        app.event('contract', o.pk, 'Sent for approval', request.user)
        return o

    def a_ctr_approve(o, request, f):
        o.status, o.approved_by, o.approved_at = 'active', request.user, now()
        o.save()
        app.close_todos('contract', o.pk, 'Approve')
        if o.previous_id:
            p = o.previous
            if p.status in ('active', 'expired'):
                p.status = 'superseded' if o.revision > p.revision and o.start <= (p.end or o.start) and o.start <= p.start + timedelta(days=1) else 'renewed'
                p.save(update_fields=['status'])
                app.event('contract', p.pk, f'{p.get_status_display()} by {o.number}', request.user)
        app.event('contract', o.pk, 'Approved – active' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o

    def a_ctr_revise(o, request, f):
        n = Contract.objects.create(**{k: getattr(o, k) for k in ('title', 'kind', 'account_id', 'contact_id', 'opportunity_id', 'sales_order_id', 'quotation_id', 'owner_id',
                                                                  'value', 'currency', 'billing', 'start', 'end', 'auto_renew', 'notice_days', 'payment_terms', 'sla_terms',
                                                                  'terms', 'branch_id', 'industry')},
                                    number=app.next_number('contract', 'CTR'), revision=o.revision + 1, previous=o, status='draft')
        app.event('contract', o.pk, f'Revision {n.revision} made: {n.number}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return {'message': f'Revision {n.number} made – change it and send it for approval.', 'created': {'app': 'zeo-crm', 'kind': 'contract', 'id': n.pk}}

    def a_ctr_renew(o, request, f):
        n = renew_contract(o, request.user)
        return {'message': f'Renewal {n.number} made (draft) – check the value and send it for approval.', 'created': {'app': 'zeo-crm', 'kind': 'contract', 'id': n.pk}}

    def a_ctr_renewal_opp(o, request, f):
        opp = renewal_opportunity(o, request.user)
        return {'message': f'Renewal opportunity {opp.number}.', 'created': {'app': 'zeo-crm', 'kind': 'opportunity', 'id': opp.pk}}

    def a_ctr_end(status_):
        def run(o, request, f):
            o.status, o.end_reason = status_, f.s('reason')[:200]
            if status_ == 'terminated' and f.dt('date'):
                o.end = f.dt('date')
            o.save()
            app.close_todos('contract', o.pk)
            app.event('contract', o.pk, f'{o.get_status_display()} – {o.end_reason}', request.user)
            return o
        return run

    app.option('contracts', lambda: Contract.objects.all(), lambda x: f'{x.number} {x.title}', ('number', 'title', 'account__name'), Contract)
    app.reg(Reg('contract', Contract, 'Contract', 'Contracts', 'Customers',
                cols=('number', 'title', 'account', 'kind', 'value', 'start', 'end', 'owner', 'status'),
                form=('title', 'kind', 'account', 'contact', 'opportunity', 'sales_order', 'owner', 'value', 'currency', 'billing', 'start', 'end', 'auto_renew',
                      'notice_days', 'payment_terms', 'sla_terms', 'terms', 'performance_notes'),
                number=('contract', 'CTR'), manage=MANAGE + ('crm_director',) + ACCT, view=ALL_VIEW + ('crm_service',), create=('crm_agent',),
                own=(('owner', 'emp'),), own_edit=True, search=('number', 'title', 'account__name'), filters=('status', 'kind', 'owner', 'auto_renew', 'billing'),
                title='title', subtitle='account', defaults=ctr_defaults, check=ctr_check, edit_when=lambda o: o.status in ('draft', 'approval', 'active'),
                info=('status', 'revision', 'previous', 'approved_by', 'approved_at', 'renewal_opportunity', 'end_reason'),
                actions=[Act('submit', 'Send for approval', a_ctr_submit, lambda o, r: o.status == 'draft' and mine(o, r), style='primary'),
                         Act('approve', 'Approve – make active', a_ctr_approve, lambda o, r: o.status in ('approval', 'draft') and app.any(r, 'crm_manager', 'crm_director'),
                             style='primary', fields=(('note', 'Note', 'text', False),)),
                         Act('revise', 'Make a revision', a_ctr_revise, lambda o, r: o.status in ('active', 'approval') and mine(o, r), fields=(('note', 'What changes', 'text', False),)),
                         Act('renewal_opp', 'Open a renewal opportunity', a_ctr_renewal_opp, lambda o, r: o.status in ('active', 'expired') and not o.renewal_opportunity_id and mine(o, r)),
                         Act('renew', 'Renew (new contract)', a_ctr_renew, lambda o, r: o.status in ('active', 'expired') and mine(o, r), style='primary'),
                         Act('terminate', 'Terminate', a_ctr_end('terminated'), lambda o, r: o.status == 'active' and app.any(r, 'crm_manager', 'crm_director'), style='danger',
                             fields=(('reason', 'Reason', 'text', True), ('date', 'Last day', 'date', False))),
                         Act('cancel', 'Cancel', a_ctr_end('cancelled'), lambda o, r: o.status in ('draft', 'approval') and mine(o, r), style='danger',
                             fields=(('reason', 'Reason', 'text', True),))],
                help='Customer contracts and agreements: approval, revisions, renewal (renewal opportunity before the end), expiry reminders 90 / 60 / 30 / 7 days.'))
    for k in ('opportunity',):
        regs[k].children = tuple(regs[k].children) + (('Contracts', 'contract', 'opportunity'),)

    # ---------------------------------------------------------------- complaints
    def cmp_defaults(o, request):
        if not o.due_at:
            from datetime import timedelta as _td
            o.due_at = now() + _td(hours=sla_hours(o.priority))
        if o.contact_id and not o.account_id:
            o.account = o.contact.account
        if not o.owner_id and o.account_id:
            p = CustomerProfile.objects.filter(account_id=o.account_id).first()
            o.owner = getattr(p, 'account_manager', None)
        if o.owner_id and o.status == 'new':
            o.status = 'assigned'

    def cmp_check(o, request, old):
        if not (o.account_id or o.contact_id):
            raise AppError('Choose the customer: an account or a contact.', 'account')
        if old is not None and old.priority != o.priority and o.status not in ('resolved', 'closed'):
            o.due_at = o.created_at + timedelta(hours=sla_hours(o.priority))

    def a_cmp_assign(o, request, f):
        o.owner = f.emp('owner')
        if o.owner is None:
            raise AppError('Choose who handles it.', 'owner')
        o.status = 'assigned' if o.status == 'new' else o.status
        o.save()
        if _emp_user(o.owner):
            app.todo(o.owner.users, f'Complaint: {o.subject}', 'complaint', o.pk, o.number, PAGE + f'r/complaint/{o.pk}', f'Resolve by {o.due_at:%d/%m/%Y %H:%M}')
        app.event('complaint', o.pk, f'Assigned to {emp_name(o.owner)}', request.user)
        return o

    def a_cmp_investigate(o, request, f):
        o.status = 'investigating'
        o.investigation = (o.investigation + '\n' if o.investigation else '') + f'{today():%d/%m/%Y} {user_name(request.user)}: {f.s("notes")}'
        o.save()
        app.event('complaint', o.pk, 'Investigating' + (f' – {f.s("notes")}' if f.s('notes') else ''), request.user)
        return o

    def a_cmp_resolve(o, request, f):
        o.status, o.resolution, o.resolved_at = 'resolved', f.s('resolution'), now()
        if o.due_at and o.resolved_at > o.due_at:
            o.sla_breached = True
        o.save()
        app.close_todos('complaint', o.pk)
        app.event('complaint', o.pk, f'Resolved – {o.resolution}', request.user)
        return o

    def a_cmp_close(o, request, f):
        r_ = f.i('rating')
        if r_ is not None and not 1 <= r_ <= 5:
            raise AppError('Rating: 1 to 5.', 'rating')
        o.status, o.closed_at, o.rating, o.feedback, o.confirmed = 'closed', now(), r_, f.s('feedback'), f.b('confirmed')
        o.save()
        app.event('complaint', o.pk, 'Closed' + (f' – rating {r_}/5' if r_ else '') + (' – confirmed by the customer' if o.confirmed else ''), request.user)
        if o.account_id:
            recalc_profile(profile_of(o.account))
        return o

    def a_cmp_reopen(o, request, f):
        o.status = 'investigating'
        o.resolved_at = o.closed_at = None
        o.save()
        app.event('complaint', o.pk, f'Reopened – {f.s("note")}', request.user)
        return o

    def a_cmp_escalate(o, request, f):
        o.escalated = min(3, o.escalated + 1)
        o.save(update_fields=['escalated'])
        for u in (app.users_with_role('crm_team_lead', fallback=False) or app.users_with_role('crm_manager')) + app.users_with_role('crm_manager'):
            app.todo(u, f'Escalated complaint: {o.subject}', 'complaint', o.pk, o.number, PAGE + f'r/complaint/{o.pk}', f.s('note'))
        app.event('complaint', o.pk, f'Escalated by {user_name(request.user)} – {f.s("note")}', request.user)
        return o

    def a_cmp_maint(o, request, f):
        from .app import a_service
        c = o.contact or Contact.objects.filter(account=o.account).first()
        if c is None:
            raise AppError('Choose a contact first.', 'contact')
        res = a_service(c, request, f)
        o.maintenance_ref = (res or {}).get('message', '')[:40]
        o.save(update_fields=['maintenance_ref'])
        app.event('complaint', o.pk, o.maintenance_ref, request.user)
        return res

    handler = lambda o, r: app.any(r, *MANAGE, 'crm_service', 'crm_director') or getattr(app.my_emp(r), 'pk', 0) == o.owner_id   # noqa: E731
    app.reg(Reg('complaint', Complaint, 'Complaint', 'Complaints', 'Customers',
                cols=('number', 'subject', 'account', 'category', 'priority', 'owner', 'due_at', 'status'),
                form=('subject', 'account', 'contact', 'sales_order', 'contract', 'category', 'priority', 'channel', 'description', 'owner'),
                number=('complaint', 'CMP'), manage=MANAGE + ('crm_director', 'crm_service'), view=ALL_VIEW + ACCT, create='all',
                own=(('owner', 'emp'),), own_edit=True, search=('number', 'subject', 'account__name', 'contact__name'),
                filters=('status', 'category', 'priority', 'owner', 'sla_breached', 'channel'), title='subject', subtitle='account',
                defaults=cmp_defaults, check=cmp_check, edit_when=lambda o: o.status not in ('closed', 'cancelled'),
                info=('status', 'due_at', 'sla_breached', 'escalated', 'investigation', 'resolution', 'resolved_at', 'closed_at', 'rating', 'feedback', 'confirmed', 'maintenance_ref'),
                actions=[Act('assign', 'Assign', a_cmp_assign, lambda o, r: o.status in ('new', 'assigned', 'investigating') and app.any(r, *MANAGE, 'crm_service'),
                             fields=(('owner', 'Handled by', 'opt:employees', True),)),
                         Act('investigate', 'Investigation note', a_cmp_investigate, lambda o, r: o.status in ('new', 'assigned', 'investigating') and handler(o, r),
                             fields=(('notes', 'Findings', 'textarea', True),)),
                         Act('resolve', 'Resolved', a_cmp_resolve, lambda o, r: o.status in ('new', 'assigned', 'investigating') and handler(o, r), style='primary',
                             fields=(('resolution', 'Resolution', 'textarea', True),)),
                         Act('close', 'Close (customer feedback)', a_cmp_close, lambda o, r: o.status == 'resolved' and handler(o, r), style='primary',
                             fields=(('rating', 'Customer rating 1–5', 'text', False), ('feedback', 'Customer feedback', 'textarea', False),
                                     ('confirmed', 'The customer confirmed the closure', 'bool', False))),
                         Act('reopen', 'Reopen', a_cmp_reopen, lambda o, r: o.status in ('resolved', 'closed') and handler(o, r), fields=(('note', 'Why', 'text', True),)),
                         Act('escalate', 'Escalate', a_cmp_escalate, lambda o, r: o.status in ('new', 'assigned', 'investigating'), fields=(('note', 'Why', 'text', False),)),
                         Act('maintenance', 'Send to Maintenance', a_cmp_maint, lambda o, r: linked(app, 'ZeoMaintenance') and o.status not in ('closed', 'cancelled'),
                             fields=(('title', 'Request', 'text', True), ('description', 'Details', 'textarea', False)))],
                help='Customer complaints with SLA by priority (CRM setup): assignment, investigation, resolution, escalation of late ones, customer rating and confirmation.'))
    regs['contact'].children = tuple(regs['contact'].children) + (('Complaints', 'complaint', 'contact'),)

    # ---------------------------------------------------------------- commissions
    def a_calc(o, request, f):
        frm, to = f.dt('from', True, 'From'), f.dt('to', True, 'To')
        n = calculate(o, frm, to, request.user)
        return {'message': f'{n} commission line(s) made – approve them in Sales › Commissions.'}

    app.reg(Reg('commission_plan', CommissionPlan, 'Commission plan', 'Commission plans', 'Setup',
                cols=('code', 'name', 'basis', 'rate', 'agent', 'territory', 'target_bonus_pct', 'is_active', 'review_status'),
                form=('code', 'name', 'basis', 'rate', 'agent', 'territory', 'min_amount', 'target_bonus_pct', 'is_active'),
                manage=('crm_manager', 'crm_director'), view=SETUP_VIEW, search=('code', 'name'), filters=('basis', 'is_active'), title='name', review=True, deletable=True, files=False,
                actions=[Act('calculate', 'Calculate commissions', a_calc, lambda o, r: o.is_active and app.any(r, 'crm_manager', 'crm_director'), style='primary',
                             fields=(('from', 'From', 'date', True), ('to', 'To', 'date', True)))],
                help='Commission rate on won deals, sales orders or accepted quotations, for every agent or one agent / territory, with an extra % for agents above target.'))

    def a_com(status_, text):
        def run(o, request, f):
            o.status = status_
            if status_ == 'approved':
                o.approved_by = request.user
            if status_ == 'paid':
                o.paid_ref = f.s('ref')[:60]
            o.save()
            app.event('commission', o.pk, text + (f' – {f.s("ref") or f.s("note")}' if (f.s('ref') or f.s('note')) else ''), request.user)
            return o
        return run

    boss2 = lambda o, r: app.any(r, 'crm_manager', 'crm_director')   # noqa: E731
    # v1.67.31 (screen test): the Commissions page asked for options/commission_plan – 404
    app.option('commission_plan', lambda: CommissionPlan.objects.all(), lambda x: f'{x.code} {x.name}'.strip(), ('code', 'name'), CommissionPlan)
    app.reg(Reg('commission', Commission, 'Commission', 'Commissions', 'Sales',
                cols=('number', 'date', 'agent', 'plan', 'source_ref', 'basis_amount', 'rate', 'amount', 'status'),
                form=('agent', 'plan', 'date', 'basis_amount', 'rate', 'amount'), readonly=(), number=('commission', 'COM'),
                manage=('crm_manager', 'crm_director'), view=ALL_VIEW + ('crm_team_lead',), create='none', own=(('agent', 'emp'),),
                search=('number', 'source_ref'), filters=('status', 'agent', 'plan'), title='number', subtitle='agent', edit_when=lambda o: o.status == 'draft',
                info=('source', 'source_ref', 'approved_by', 'paid_ref'),
                actions=[Act('approve', 'Approve', a_com('approved', 'Approved'), lambda o, r: o.status == 'draft' and boss2(o, r), style='primary'),
                         Act('pay', 'Mark paid', a_com('paid', 'Paid'), lambda o, r: o.status == 'approved' and boss2(o, r), fields=(('ref', 'Payroll / voucher ref.', 'text', False),)),
                         Act('cancel', 'Cancel', a_com('cancelled', 'Cancelled'), lambda o, r: o.status in ('draft', 'approved') and boss2(o, r), style='danger',
                             fields=(('note', 'Why', 'text', False),))],
                help='Commission per won deal / order / accepted quotation, made by "Calculate commissions" on a plan. Agents see their own.'))

    # ---------------------------------------------------------------- documents
    def a_doc_submit(o, request, f):
        o.status = 'approval'
        o.save()
        for u in app.users_with_role('crm_manager'):
            app.todo(u, f'Approve document: {o.title}', 'document', o.pk, o.number, PAGE + f'r/document/{o.pk}')
        app.event('document', o.pk, 'Sent for approval', request.user)
        return o

    def a_doc_approve(o, request, f):
        o.status, o.approved_by, o.approved_at = 'approved', request.user, now()
        o.save()
        app.close_todos('document', o.pk)
        if o.previous_id:
            CRMDocument.objects.filter(pk=o.previous_id, status='approved').update(status='superseded')
        app.event('document', o.pk, f'Approved (version {o.version})', request.user)
        return o

    def a_doc_version(o, request, f):
        n = CRMDocument.objects.create(number=app.next_number('document', 'DOC'), title=o.title, kind=o.kind, account=o.account, opportunity=o.opportunity, enquiry=o.enquiry,
                                       contract=o.contract, version=o.version + 1, previous=o, issue_date=today(), expiry_date=o.expiry_date, owner=o.owner, branch_id=o.branch_id,
                                       notes=f.s('note'))
        app.event('document', o.pk, f'New version {n.version}: {n.number}', request.user)
        return {'message': f'Version {n.version} made ({n.number}) – attach the new file.', 'created': {'app': 'zeo-crm', 'kind': 'document', 'id': n.pk}}

    app.reg(Reg('document', CRMDocument, 'Document', 'Documents', 'Customers',
                cols=('number', 'title', 'kind', 'account', 'version', 'issue_date', 'expiry_date', 'status'),
                form=('title', 'kind', 'account', 'opportunity', 'enquiry', 'contract', 'issue_date', 'expiry_date', 'owner', 'notes'),
                number=('document', 'DOC'), manage=MANAGE + ('crm_director',), view=ALL_VIEW + ACCT + ('crm_service', 'crm_presales'),
                create=('crm_agent', 'crm_presales', 'crm_account', 'crm_service', 'crm_marketing'), own=(('owner', 'emp'),), own_edit=True,
                search=('number', 'title', 'account__name'), filters=('kind', 'status'), title='title', subtitle='account',
                defaults=lambda o, r: setattr(o, 'owner', o.owner or app.my_emp(r)), edit_when=lambda o: o.status in ('draft', 'approval'),
                info=('status', 'previous', 'approved_by', 'approved_at'),
                actions=[Act('submit', 'Send for approval', a_doc_submit, lambda o, r: o.status == 'draft'),
                         Act('approve', 'Approve', a_doc_approve, lambda o, r: o.status in ('draft', 'approval') and app.any(r, *MANAGE, 'crm_director'), style='primary'),
                         Act('version', 'New version', a_doc_version, lambda o, r: o.status in ('approved', 'expired'), fields=(('note', 'What changed', 'text', False),))],
                help='Specifications, drawings, contracts, POs, catalogues, price lists … with versions, approval and expiry reminders. Attach the file to the record.'))
    for k, fk in (('opportunity', 'opportunity'), ('enquiry', 'enquiry'), ('contract', 'contract')):
        regs[k].children = tuple(regs[k].children) + (('Documents', 'document', fk),)

    # ---------------------------------------------------------------- quotations: templates, kinds, comparison
    app.reg(Reg('quotation_template', QuotationTemplate, 'Quotation template', 'Quotation templates', 'Setup',
                cols=('code', 'name', 'quote_kind', 'industry', 'billing', 'is_active', 'review_status'),
                form=('code', 'name', 'quote_kind', 'industry', 'billing', 'validity_days', 'notes', 'scope_of_work', 'payment_terms_text', 'delivery_terms', 'terms', 'items'),
                manage=('crm_manager',), view=SETUP_VIEW, search=('code', 'name'), filters=('quote_kind', 'industry', 'is_active'), title='name', review=True, deletable=True,
                files=False, tables={'items': [('product', 'Product / service', 'opt', 'products'), ('description', 'Description', 'text'), ('qty', 'Qty', 'number'),
                                              ('unit_price', 'Unit price', 'number')]},
                help='Standard texts and lines for product, service, project and subscription quotations. Apply one on a draft quotation.'))
    for k in ('quotation_template', 'onboarding_template'):
        from .industry import all_industries
        regs[k].choices_fn = {'industry': all_industries}

    qreg = regs['quotation']
    form = list(qreg.form)
    for n in ('quote_kind', 'billing'):
        if n not in form:
            form.insert(form.index('currency'), n)
    for n in ('scope_of_work', 'delivery_terms'):
        if n not in form:
            form.insert(form.index('terms'), n)
    qreg.form = tuple(form)
    qreg.filters = tuple(qreg.filters) + ('quote_kind',)
    qreg.info = tuple(qreg.info) + ('template',)

    def a_apply(o, request, f):
        t = f.obj('template', QuotationTemplate, True, 'Template')
        old = Quotation.objects.get(pk=o.pk)
        o.template, o.quote_kind, o.billing = t, t.quote_kind, t.billing
        o.notes = t.notes or o.notes
        o.scope_of_work = t.scope_of_work or o.scope_of_work
        o.delivery_terms = t.delivery_terms or o.delivery_terms
        o.terms = '\n\n'.join(x for x in (t.payment_terms_text and f'Payment: {t.payment_terms_text}', t.terms) if x) or o.terms
        if t.validity_days:
            o.valid_until = o.date + timedelta(days=t.validity_days)
        lines = list(o.items or [])
        for it in (t.items or []):
            lines.append({'product': it.get('product'), 'description': it.get('description') or '', 'qty': it.get('qty') or 1,
                          'unit_price': it.get('unit_price') if it.get('unit_price') not in (None, '', 0) else (None if it.get('product') else 0), 'discount_pct': 0})
        o.items = lines
        qreg.saver(o, request, False, old)
        app.event('quotation', o.pk, f'Template "{t.name}" applied', request.user)
        return o

    def a_compare(o, request, f):
        p = o.previous
        if p is None:
            return {'message': 'This is the first version.'}
        def key(it):
            return str(it.get('product') or '') + '|' + (it.get('description') or '')[:40]
        a_, b_ = {key(x): x for x in (p.items or [])}, {key(x): x for x in (o.items or [])}
        ch = []
        for k_ in b_:
            if k_ not in a_:
                ch.append(f'+ {b_[k_].get("description") or b_[k_].get("product_label") or "line"} ({b_[k_].get("qty")} × {b_[k_].get("unit_price")})')
            else:
                x, y = a_[k_], b_[k_]
                for fld, lbl in (('qty', 'qty'), ('unit_price', 'price'), ('discount_pct', 'discount %')):
                    if str(x.get(fld) or 0) != str(y.get(fld) or 0):
                        ch.append(f'~ {y.get("description") or y.get("product_label") or "line"}: {lbl} {x.get(fld) or 0} → {y.get(fld) or 0}')
        for k_ in a_:
            if k_ not in b_:
                ch.append(f'− {a_[k_].get("description") or a_[k_].get("product_label") or "line"}')
        for fld, lbl in (('total', 'Total'), ('discount_pct', 'Overall discount %'), ('valid_until', 'Valid until'), ('payment_term_id', 'Payment term'), ('terms', 'Terms'),
                         ('delivery_terms', 'Delivery terms')):
            if getattr(p, fld) != getattr(o, fld):
                ch.append(f'{lbl}: {getattr(p, fld)} → {getattr(o, fld)}' if fld not in ('terms', 'delivery_terms') else f'{lbl} changed')
        return {'message': f'{p.number} (rev. {p.revision}) → {o.number} (rev. {o.revision}): ' + ('; '.join(ch) if ch else 'no changes')}

    qreg.actions.insert(0, Act('apply_template', 'Apply a template', a_apply, lambda o, r: o.status in ('draft', 'approval') and can_work(o, r),
                               fields=(('template', 'Template', 'opt:quotation-templates', True),)))
    qreg.actions.append(Act('compare', 'Compare with the previous version', a_compare, lambda o, r: bool(o.previous_id)))
    app.option('quotation-templates', lambda: QuotationTemplate.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), QuotationTemplate)

    # ---------------------------------------------------------------- leads, opportunities, activities: more fields
    lreg = regs['lead']
    form = list(lreg.form)
    for n in ('priority', 'next_follow_up', 'expected_close'):
        if n not in form:
            form.insert(form.index('owner') + 1, n)
    lreg.form = tuple(form)
    lreg.filters = tuple(lreg.filters) + ('priority',)
    oreg = regs['opportunity']
    form = list(oreg.form)
    for n in ('next_step', 'technical_requirements', 'strategy', 'decision_makers'):
        if n not in form:
            form.insert(form.index('requirements') + 1, n)
    oreg.form = tuple(form)
    areg = regs['activity']
    if 'priority' not in areg.form:
        areg.form = tuple(areg.form) + ('priority',)
    areg.filters = tuple(areg.filters) + ('priority',)

    def a_new_enquiry(kind):
        def run(o, request, f):
            e = Enquiry(title=f.s('title') or (o.title if kind == 'opportunity' else f'{o.company_name or o.name} – enquiry')[:150], date=today(),
                        **({'opportunity': o, 'account': o.account, 'contact': o.contact} if kind == 'opportunity' else {'lead': o, 'account': o.account, 'contact': o.contact}),
                        requirement=o.requirements, owner=o.owner, estimated_value=o.value, industry=o.industry, number=app.next_number('enquiry', 'ENQ'),
                        branch_id=o.branch_id, extra={})
            from .industry import carry
            carry(o, e, 'enquiry')
            e.save()
            app.event('enquiry', e.pk, f'Made from {kind} {o.number}', request.user)
            return {'message': f'Enquiry {e.number} made.', 'created': {'app': 'zeo-crm', 'kind': 'enquiry', 'id': e.pk}}
        return run
    oreg.actions.insert(3, Act('enquiry', 'New enquiry', a_new_enquiry('opportunity'), lambda o, r: o.status == 'open' and can_work(o, r),
                               fields=(('title', 'Title', 'text', False),)))
    lreg.actions.insert(2, Act('enquiry', 'New enquiry', a_new_enquiry('lead'), lambda o, r: o.status not in ('converted', 'disqualified') and can_work(o, r),
                               fields=(('title', 'Title', 'text', False),)))

    # blocked customers
    for k in ('opportunity', 'quotation', 'sales_order', 'enquiry', 'contract'):
        guard_account(regs[k])

    # ---------------------------------------------------------------- signals
    pre_save.connect(_pre_save, sender=Opportunity, dispatch_uid='zeocrm_opp_pre')
    post_save.connect(_opp_saved, sender=Opportunity, dispatch_uid='zeocrm_opp_post')
    pre_save.connect(_pre_save, sender=Quotation, dispatch_uid='zeocrm_q_pre')
    post_save.connect(_quote_saved, sender=Quotation, dispatch_uid='zeocrm_q_post')

    # ---------------------------------------------------------------- CRM home, my work, daily
    base_dash, base_my, base_daily = app.dashboard, app.my_work, app.daily

    def dashboard(request):
        out = base_dash(request) if base_dash else {'cards': [], 'bars': [], 'lists': []}
        try:
            t = today()
            cmp_ = regs['complaint'].scope(request)
            ctr = regs['contract'].scope(request)
            enq = regs['enquiry'].scope(request)
            out['cards'] = list(out.get('cards') or []) + [
                {'label': 'Open complaints', 'value': cmp_.exclude(status__in=('resolved', 'closed', 'cancelled')).count(), 'link': 'r/complaint?open=1',
                 'hint': f'{cmp_.filter(sla_breached=True).exclude(status__in=("resolved", "closed", "cancelled")).count()} past SLA',
                 'tone': 'bad' if cmp_.filter(sla_breached=True).exclude(status__in=('resolved', 'closed', 'cancelled')).exists() else 'ok'},
                {'label': 'Enquiries to evaluate / approve', 'value': enq.filter(status__in=('evaluation', 'approval', 'clarification')).count(), 'link': 'r/enquiry'},
                {'label': 'Contracts ending in 60 days', 'value': ctr.filter(status='active', end__isnull=False, end__lte=t + timedelta(days=60)).count(), 'link': 'r/contract?status=active'},
                {'label': 'Onboardings open', 'value': regs['onboarding'].scope(request).filter(status__in=('open', 'in_progress')).count(), 'link': 'r/onboarding'},
                {'label': 'Customers at churn risk', 'value': CustomerProfile.objects.filter(churn_risk='high').count(), 'link': 'r/customer_profile?churn_risk=high',
                 'tone': 'bad' if CustomerProfile.objects.filter(churn_risk='high').exists() else 'ok'},
                {'label': 'Overdue follow-ups', 'value': regs['activity'].scope(request).filter(status='open', due_at__lt=now()).count(), 'link': 'r/activity?status=open'},
            ]
            out['lists'] = list(out.get('lists') or []) + [
                {'title': 'Complaints past SLA', 'kind': 'complaint',
                 'rows': [{'id': c.pk, 'title': f'{c.number} {c.subject}', 'sub': f'{c.account.name if c.account_id else ""} · {emp_name(c.owner) or "unassigned"}', 'badge': c.priority}
                          for c in cmp_.filter(sla_breached=True).exclude(status__in=('resolved', 'closed', 'cancelled')).select_related('account', 'owner')[:8]]},
                {'title': 'Contracts ending soon', 'kind': 'contract',
                 'rows': [{'id': c.pk, 'title': f'{c.number} {c.title}', 'sub': f'{c.account.name} · ends {c.end:%d/%m/%Y}', 'badge': f'{(c.end - t).days} d'}
                          for c in ctr.filter(status='active', end__isnull=False, end__lte=t + timedelta(days=60)).select_related('account').order_by('end')[:8]]},
            ]
        except Exception:   # noqa: BLE001
            log.exception('release 2 dashboard')
        return out

    def my_work(request):
        out = list(base_my(request) or []) if base_my else []
        me = app.my_emp(request)
        if me is None:
            return out
        try:
            out.append({'title': 'My complaints', 'rows': [{'kind': 'complaint', 'id': c.pk, 'title': f'{c.number} {c.subject}', 'sub': f'due {c.due_at:%d/%m/%Y %H:%M}' if c.due_at else ''}
                                                          for c in Complaint.objects.filter(owner=me).exclude(status__in=('resolved', 'closed', 'cancelled'))[:20]]})
            out.append({'title': 'Enquiries to evaluate', 'rows': [{'kind': 'enquiry', 'id': e.pk, 'title': f'{e.number} {e.title}', 'sub': e.get_status_display()}
                                                                  for e in Enquiry.objects.filter(technical_owner=me, status__in=('evaluation',))[:20]]})
        except Exception:   # noqa: BLE001
            log.exception('release 2 my work')
        return out

    def daily():
        out = base_daily() if base_daily else {}
        for k in ('contracts_auto_renewed', 'contracts_expired', 'contract_reminders', 'renewal_opportunities', 'complaints_escalated', 'followups_escalated',
                  'documents_expired', 'document_reminders'):
            out.setdefault(k, 0)
        for fn in (contract_daily, complaint_daily, activity_daily, document_daily):
            try:
                fn(out)
            except Exception:   # noqa: BLE001
                log.exception('release 2 daily %s', fn.__name__)
        try:
            out['profiles_recalculated'] = recalc_all()
        except Exception:   # noqa: BLE001
            log.exception('profiles')
        return out

    app.dashboard, app.my_work, app.daily = dashboard, my_work, daily
    app.settings_fields = tuple(app.settings_fields) + ('enquiry_approval', 'auto_onboarding', 'contract_renewal_opps', 'complaint_sla_urgent', 'complaint_sla_high',
                                                        'complaint_sla_medium', 'complaint_sla_low', 'followup_escalate_hours', 'churn_days')
    return {'recalc_all': recalc_all, 'reports': reports, 'renewal_opportunity': renewal_opportunity, 'start_onboarding': start_onboarding, 'calculate': calculate}
