"""v1.67.13 (collections) – Collection planner: what is expected to be collected, when, from whom – opening balances,
CRM opportunities / sales orders, project milestones and AMC billing lines – with follow-up dates and comments
(each follow-up is a to-do in the assigned user's To-do list) and a report: opening + CRM + projects + AMC = total.

* Project / AMC lines: the amount collected comes from Billing (payments booked against the tax invoice of the
  milestone / AMC line, shared over the item's planned lines by expected date); a line of an item marked Paid before
  1.67.13 (no ZEO invoice) counts as collected.
* Opening, CRM and other lines: the collected amount is entered on the line (or with a follow-up note).
Rights: billing_view to see, billing_edit to change.
"""
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import services as S
from .models import AMCBilling, CollectionLine, CollectionNote, Customer, Milestone, ProjectProfile
from .views import Base, In, guard

ZERO = Decimal('0')
PAGE = '/main-sidebar/zeo-projects/collection-planner'
SOURCE_LABEL = {'opening': 'Opening balance', 'crm': 'CRM', 'project': 'Project', 'amc': 'AMC', 'other': 'Other'}


def r2(v):
    return Decimal(v or 0).quantize(Decimal('0.01'))


def _dec(v, field, required=False):
    if v in (None, ''):
        if required:
            raise S.ProjectsError('Enter the amount.', field)
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise S.ProjectsError('Enter a number.', field)
    if d < 0:
        raise S.ProjectsError('The amount cannot be negative.', field)
    return d


# ------------------------------------------------------------------ CRM records
def crm_record(kind, pk):
    """(label, customer name, amount, date) of a CRM opportunity / sales order, or None."""
    try:
        if kind == 'opportunity':
            from ZeoCRM.models import Opportunity
            o = Opportunity.objects.select_related('contact', 'account').filter(pk=pk).first()
            if o is None:
                return None
            who = (o.account.name if o.account_id else '') or (o.contact.name if o.contact_id else '')
            return {'reference': o.number or f'OPP {o.pk}', 'description': o.title, 'customer_name': who, 'amount': o.value,
                    'date': o.expected_close, 'status': o.status}
        if kind == 'sales_order':
            from ZeoCRM.models import SalesOrder
            o = SalesOrder.objects.select_related('account').filter(pk=pk).first()
            if o is None:
                return None
            return {'reference': o.number, 'description': getattr(o, 'title', '') or f'Sales order {o.number}',
                    'customer_name': o.account.name if o.account_id else '', 'amount': o.total, 'date': o.delivery_date or o.date,
                    'status': o.status}
    except Exception:   # noqa: BLE001 – CRM not installed / not active
        return None
    return None


def crm_search(q='', kind=''):
    out = []
    try:
        from ZeoCRM.models import Opportunity, SalesOrder
        if kind in ('', 'opportunity'):
            qs = Opportunity.objects.select_related('contact', 'account').exclude(status='lost')
            if q:
                qs = qs.filter(Q(number__icontains=q) | Q(title__icontains=q) | Q(contact__name__icontains=q) | Q(account__name__icontains=q))
            for o in qs.order_by('-id')[:50]:
                out.append({'kind': 'opportunity', 'id': o.pk, 'reference': o.number, 'description': o.title, 'status': o.status,
                            'customer_name': (o.account.name if o.account_id else '') or (o.contact.name if o.contact_id else ''),
                            'amount': str(o.value), 'date': str(o.expected_close or '')})
        if kind in ('', 'sales_order'):
            qs = SalesOrder.objects.select_related('account').exclude(status='cancelled')
            if q:
                qs = qs.filter(Q(number__icontains=q) | Q(account__name__icontains=q))
            for o in qs.order_by('-id')[:50]:
                out.append({'kind': 'sales_order', 'id': o.pk, 'reference': o.number, 'description': f'Sales order {o.number}', 'status': o.status,
                            'customer_name': o.account.name if o.account_id else '', 'amount': str(o.total), 'date': str(o.delivery_date or o.date or '')})
    except Exception:   # noqa: BLE001
        pass
    return out


# ------------------------------------------------------------------ collected amounts
def _billing_received(obj):
    """Received (VAT included) and invoiced for a milestone / AMC line from Billing; legacy Paid without a ZEO invoice = all."""
    from . import billing as B
    total = got = ZERO
    for ln in obj.bill_lines.select_related('document'):
        d = ln.document
        if d.kind != 'invoice' or d.status == 'cancelled':
            continue
        sh = B.received_by_item(d).get(('m' if isinstance(obj, Milestone) else 'a', obj.pk))
        if sh:
            got += sh[0]
            total += sh[1]
            break   # one live invoice per item
    st = B.stage_of(obj)
    if not total and st == 'paid':
        return None   # paid outside ZEO invoices: every planned line is collected
    return got


def refresh_item(obj):
    """Share what Billing received for the item over its planned lines (earliest expected date first)."""
    lines = list(obj.collection_lines.exclude(status='cancelled').order_by('expected_date', 'id'))
    if not lines:
        return
    got = _billing_received(obj)
    left = None if got is None else Decimal(got)
    last_pay = _last_payment_date(obj)
    for ln in lines:
        take = ln.expected_amount if left is None else min(left, ln.expected_amount)
        if left is not None:
            left -= take
        ln.collected_amount = r2(take)
        ln.collected_date = (last_pay or S.local_today()) if take > 0 else None
        _status(ln)
        ln.save(update_fields=['collected_amount', 'collected_date', 'status'])
        if ln.status == 'collected':
            S.close_todos(ln)


def _last_payment_date(obj):
    from .models import BillPayment
    q = BillPayment.objects.filter(document__lines__milestone=obj) if isinstance(obj, Milestone) else BillPayment.objects.filter(document__lines__amc_billing=obj)
    p = q.exclude(document__status='cancelled').order_by('-date').first()
    return p.date if p else None


def _status(ln):
    if ln.status == 'cancelled':
        return
    if ln.collected_amount >= ln.expected_amount and ln.expected_amount > 0:
        ln.status = 'collected'
    elif ln.collected_amount > 0:
        ln.status = 'partly'
    else:
        ln.status = 'open'


def item_expected(obj):
    """Expected collection of a billing item: its invoice balance, else its amount + VAT."""
    from . import billing as B
    r = B.item_row(obj)
    if r.get('invoice'):
        return Decimal(r['balance'] or 0) + Decimal(r['received'] or 0), (date.fromisoformat(r['due_date']) if r['due_date'] else None)
    rate = S.settings().default_vat_rate or ZERO
    return r2(obj.amount * (1 + rate / 100)), (obj.planned_date if isinstance(obj, Milestone) else obj.due_date)


# ------------------------------------------------------------------ to-dos
def _todo(ln, user=None):
    S.close_todos(ln)
    if ln.status in ('collected', 'cancelled'):
        return
    who = ln.assigned_to or (user if getattr(user, 'pk', None) else None)
    if who is None:
        return
    due = ln.next_followup or ln.expected_date
    S.todo(who, f'Collection: {ln.reference or SOURCE_LABEL[ln.source]} – {ln.customer_name} – {S.settings().currency} {r2(ln.expected_amount - ln.collected_amount):,.2f}'[:255],
           ln, page=f'{PAGE}?line={ln.pk}', note=ln.last_comment, due=due, kind='follow_up')


# ------------------------------------------------------------------ rows
def line_row(ln, today=None):
    today = today or S.local_today()
    bal = r2(ln.expected_amount - ln.collected_amount)
    link = None
    if ln.milestone_id:
        link = {'kind': 'milestone', 'id': ln.milestone_id, 'project_id': ln.milestone.project_id}
    elif ln.amc_billing_id:
        link = {'kind': 'amc', 'id': ln.amc_billing_id, 'contract': ln.amc_billing.contract_id}
    elif ln.crm_id:
        link = {'kind': ln.crm_kind, 'id': ln.crm_id}
    return {'id': ln.pk, 'source': ln.source, 'source_label': SOURCE_LABEL.get(ln.source, ln.source), 'crm_kind': ln.crm_kind, 'crm_id': ln.crm_id,
            'milestone': ln.milestone_id, 'amc_billing': ln.amc_billing_id, 'link': link,
            'customer': ln.customer_id, 'customer_name': ln.customer_name, 'reference': ln.reference, 'description': ln.description,
            'expected_amount': str(ln.expected_amount), 'expected_date': str(ln.expected_date), 'collected_amount': str(ln.collected_amount),
            'collected_date': str(ln.collected_date or ''), 'balance': str(bal), 'status': ln.status, 'status_label': ln.get_status_display(),
            'overdue_days': (today - ln.expected_date).days if bal > 0 and ln.expected_date < today and ln.status != 'cancelled' else 0,
            'next_followup': str(ln.next_followup or ''), 'followup_due': bool(ln.next_followup and ln.next_followup <= today and bal > 0),
            'last_comment': ln.last_comment, 'assigned_to': ln.assigned_to_id,
            'assigned_name': S.user_name(ln.assigned_to) if ln.assigned_to_id else '',
            'auto_collected': ln.source in ('project', 'amc')}


def note_row(n):
    return {'id': n.id, 'date': str(n.date), 'comment': n.comment, 'next_followup': str(n.next_followup or ''),
            'collected_amount': str(n.collected_amount) if n.collected_amount is not None else '',
            'by': S.user_name(n.created_by) if n.created_by_id else ''}


def _qs(q):
    qs = CollectionLine.objects.select_related('assigned_to', 'milestone', 'amc_billing', 'customer')
    if q.get('source'):
        qs = qs.filter(source__in=q['source'].split(','))
    if q.get('status'):
        qs = qs.filter(status__in=q['status'].split(','))
    elif not q.get('all'):
        qs = qs.exclude(status__in=('collected', 'cancelled'))
    if q.get('from'):
        qs = qs.filter(expected_date__gte=q['from'])
    if q.get('to'):
        qs = qs.filter(expected_date__lte=q['to'])
    if q.get('customer'):
        qs = qs.filter(Q(customer_id=q['customer']) | Q(customer_name__iexact=q.get('customer_name') or '-'))
    if q.get('assigned'):
        qs = qs.filter(assigned_to_id=q['assigned'])
    if q.get('line'):
        qs = CollectionLine.objects.filter(pk=q['line'])
    if q.get('q'):
        t = q['q']
        qs = qs.filter(Q(customer_name__icontains=t) | Q(reference__icontains=t) | Q(description__icontains=t) | Q(last_comment__icontains=t))
    return qs


# ------------------------------------------------------------------ report
def report(q):
    """Opening + CRM + projects + AMC (+ other) = total; planned, collected, balance, overdue; by month and by customer."""
    qq = q.dict() if hasattr(q, 'dict') else dict(q)
    qq['all'] = '1'
    rows = list(_qs(qq).exclude(status='cancelled'))
    today = S.local_today()
    keys = ['opening', 'crm', 'project', 'amc', 'other']
    by = {k: {'planned': ZERO, 'collected': ZERO, 'balance': ZERO, 'overdue': ZERO, 'count': 0} for k in keys}
    months = defaultdict(lambda: {k: ZERO for k in keys + ['collected']})
    custs = defaultdict(lambda: {'planned': ZERO, 'collected': ZERO, 'balance': ZERO, 'overdue': ZERO})
    for ln in rows:
        b = ln.expected_amount - ln.collected_amount
        e = by[ln.source]
        e['planned'] += ln.expected_amount
        e['collected'] += ln.collected_amount
        e['balance'] += b
        e['count'] += 1
        if b > 0 and ln.expected_date < today:
            e['overdue'] += b
            custs[ln.customer_name]['overdue'] += b
        mk = ln.expected_date.strftime('%Y-%m')
        months[mk][ln.source] += ln.expected_amount
        months[mk]['collected'] += ln.collected_amount
        c = custs[ln.customer_name]
        c['planned'] += ln.expected_amount
        c['collected'] += ln.collected_amount
        c['balance'] += b
    tot = {k: sum((by[s][k] for s in keys), ZERO) for k in ('planned', 'collected', 'balance', 'overdue')}
    tot['count'] = sum(by[s]['count'] for s in keys)
    fmt = lambda d: {k: (str(r2(v)) if isinstance(v, Decimal) else v) for k, v in d.items()}  # noqa: E731
    return {'by_source': [{'source': k, 'label': SOURCE_LABEL[k], **fmt(by[k])} for k in keys], 'total': fmt(tot),
            'months': [{'month': m, **fmt(v), 'total': str(r2(sum((v[k] for k in keys), ZERO)))} for m, v in sorted(months.items())],
            'customers': sorted(({'customer_name': n, **fmt(v)} for n, v in custs.items()), key=lambda x: -Decimal(x['balance'])),
            'currency': S.settings().currency, 'from': q.get('from') or '', 'to': q.get('to') or ''}


# ------------------------------------------------------------------ create / update
@transaction.atomic
def create_line(user, d):
    src = d.get('source')
    if src not in SOURCE_LABEL:
        raise S.ProjectsError('Choose where the money comes from.', 'source')
    ln = CollectionLine(source=src, created_by=user if getattr(user, 'pk', None) else None)
    exp_amt = _dec(d.get('expected_amount'), 'expected_amount')
    exp_date = d.get('expected_date')
    if src == 'crm':
        kind, pk = d.get('crm_kind'), d.get('crm_id')
        rec = crm_record(kind, pk) if kind in ('opportunity', 'sales_order') and pk else None
        if rec is None:
            raise S.ProjectsError('Choose the CRM opportunity or sales order.', 'crm_id')
        ln.crm_kind, ln.crm_id = kind, int(pk)
        ln.reference, ln.description = rec['reference'][:120], (d.get('description') or rec['description'] or '')[:300]
        ln.customer_name = (d.get('customer_name') or rec['customer_name'] or '–')[:200]
        exp_amt = exp_amt if exp_amt is not None else rec['amount']
        exp_date = exp_date or (str(rec['date']) if rec['date'] else None)
    elif src in ('project', 'amc'):
        obj = (Milestone if src == 'project' else AMCBilling).objects.filter(pk=d.get('item_id')).first()
        if obj is None:
            raise S.ProjectsError('Choose the milestone or AMC billing line.', 'item_id')
        from . import billing as B
        cust = B.customer_of(obj)
        if cust is None:
            raise S.ProjectsError('The project has no customer – set it first.', 'item_id')
        ln.customer, ln.customer_name = cust, cust.name
        if src == 'project':
            ln.milestone = obj
        else:
            ln.amc_billing = obj
        ln.reference = B.item_label(obj)[:120]
        ln.description = (d.get('description') or '')[:300]
        e_amt, e_date = item_expected(obj)
        if exp_amt is None:
            planned = sum((x.expected_amount for x in obj.collection_lines.exclude(status='cancelled')), ZERO)
            exp_amt = max(ZERO, e_amt - planned)
        exp_date = exp_date or (str(e_date) if e_date else None)
    else:
        cust = Customer.objects.filter(pk=d.get('customer')).first() if d.get('customer') else None
        ln.customer = cust
        ln.customer_name = (cust.name if cust else (d.get('customer_name') or '')).strip()[:200]
        if not ln.customer_name:
            raise S.ProjectsError('Enter the customer.', 'customer_name')
        ln.reference = (d.get('reference') or '')[:120]
        ln.description = (d.get('description') or '')[:300]
    if not exp_amt or exp_amt <= 0:
        raise S.ProjectsError('Enter the amount expected.', 'expected_amount')
    ln.expected_amount = r2(exp_amt)
    if not exp_date:
        raise S.ProjectsError('Enter the expected collection date.', 'expected_date')
    ln.expected_date = date.fromisoformat(str(exp_date)[:10])
    ln.next_followup = date.fromisoformat(d['next_followup'][:10]) if d.get('next_followup') else None
    ln.last_comment = (d.get('comment') or '').strip()
    _assign(ln, d.get('assigned_to'), user)
    if src in ('opening', 'crm', 'other') and d.get('collected_amount') not in (None, ''):
        ln.collected_amount = r2(_dec(d.get('collected_amount'), 'collected_amount'))
        ln.collected_date = date.fromisoformat(d['collected_date'][:10]) if d.get('collected_date') else (S.local_today() if ln.collected_amount else None)
    _status(ln)
    ln.save()
    if ln.last_comment:
        CollectionNote.objects.create(line=ln, date=S.local_today(), comment=ln.last_comment, next_followup=ln.next_followup,
                                      created_by=ln.created_by)
    if ln.milestone_id:
        refresh_item(ln.milestone)
    elif ln.amc_billing_id:
        refresh_item(ln.amc_billing)
    ln.refresh_from_db()
    _todo(ln, user)
    return ln


def _assign(ln, uid, user):
    from django.contrib.auth import get_user_model
    if uid:
        u = get_user_model().objects.filter(pk=uid).first()
        if u is None:
            raise S.ProjectsError('Choose a user.', 'assigned_to')
        ln.assigned_to = u
    elif ln.assigned_to_id is None:
        s = S.settings()
        ln.assigned_to = s.billing_user if s.billing_user_id else (user if getattr(user, 'pk', None) else None)


def update_line(ln, user, d):
    if 'expected_amount' in d:
        v = _dec(d.get('expected_amount'), 'expected_amount', True)
        if v <= 0:
            raise S.ProjectsError('Enter the amount expected.', 'expected_amount')
        ln.expected_amount = r2(v)
    if d.get('expected_date'):
        ln.expected_date = date.fromisoformat(str(d['expected_date'])[:10])
    if 'next_followup' in d:
        ln.next_followup = date.fromisoformat(d['next_followup'][:10]) if d.get('next_followup') else None
    for k in ('reference', 'description', 'customer_name'):
        if k in d and (k != 'customer_name' or ln.source in ('opening', 'other')):
            setattr(ln, k, (d.get(k) or '')[:300 if k == 'description' else 200 if k == 'customer_name' else 120])
    if 'assigned_to' in d:
        _assign(ln, d.get('assigned_to'), user)
    if 'collected_amount' in d:
        if ln.source in ('project', 'amc'):
            raise S.ProjectsError('For projects and AMC the amount collected comes from the payments in Billing.', 'collected_amount')
        v = _dec(d.get('collected_amount'), 'collected_amount') or ZERO
        if v > ln.expected_amount:
            raise S.ProjectsError('More than the amount expected – raise the expected amount first.', 'collected_amount')
        ln.collected_amount = r2(v)
        ln.collected_date = date.fromisoformat(d['collected_date'][:10]) if d.get('collected_date') else (S.local_today() if v else None)
    if d.get('status') == 'cancelled':
        ln.status = 'cancelled'
    elif d.get('status') == 'open' and ln.status == 'cancelled':
        ln.status = 'open'
    _status(ln)
    comment = (d.get('comment') or '').strip()
    if comment:
        ln.last_comment = comment
        CollectionNote.objects.create(line=ln, date=S.local_today(), comment=comment, next_followup=ln.next_followup,
                                      collected_amount=r2(_dec(d.get('collected_amount'), 'collected_amount')) if d.get('collected_amount') not in (None, '') else None,
                                      created_by=user if getattr(user, 'pk', None) else None)
    ln.save()
    if ln.milestone_id:
        refresh_item(ln.milestone)
    elif ln.amc_billing_id:
        refresh_item(ln.amc_billing)
    ln.refresh_from_db()
    _todo(ln, user)
    return ln


def plan_open_billing(user):
    """Add a planned line for every milestone / AMC line that is invoiced (or waiting for its PI / invoice) and has
    nothing planned yet."""
    from . import billing as B
    n = 0
    items = list(Milestone.objects.filter(invoice_status__in=('pi_to_create', 'pi_created', 'pi_sent', 'to_invoice', 'invoiced', 'partly_paid'))) + \
        list(AMCBilling.objects.filter(status__in=('pi_to_create', 'pi_created', 'pi_sent', 'to_invoice', 'invoiced', 'partly_paid'))
             .exclude(contract__status__in=('draft', 'cancelled')))
    for o in items:
        if o.collection_lines.exclude(status='cancelled').exists() or B.customer_of(o) is None:
            continue
        e_amt, e_date = item_expected(o)
        rcv = _billing_received(o) or ZERO
        if e_amt - rcv <= 0:
            continue
        create_line(user, {'source': 'project' if isinstance(o, Milestone) else 'amc', 'item_id': o.pk,
                           'expected_date': str(e_date or S.local_today())})
        n += 1
    return n


# ------------------------------------------------------------------ views
class CollectionLinesView(Base):
    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        q = request.query_params
        today = S.local_today()
        qs = _qs(q)
        if q.get('mine'):
            qs = qs.filter(assigned_to=request.user)
        rows = [line_row(x, today) for x in qs[:2000]]
        if q.get('due'):
            rows = [r for r in rows if r['followup_due'] or r['overdue_days']]
        tot = {'expected': str(r2(sum((Decimal(r['expected_amount']) for r in rows), ZERO))),
               'collected': str(r2(sum((Decimal(r['collected_amount']) for r in rows), ZERO))),
               'balance': str(r2(sum((Decimal(r['balance']) for r in rows), ZERO))),
               'overdue': str(r2(sum((Decimal(r['balance']) for r in rows if r['overdue_days']), ZERO))),
               'followup_due': sum(1 for r in rows if r['followup_due'])}
        return Response({'lines': rows, 'totals': tot, 'currency': S.settings().currency, 'rights': {'billing_edit': A.can(request, 'billing_edit')}})

    @guard
    def post(self, request):
        A.need(request, 'billing_edit')
        d = dict(In(request).d)
        if d.get('action') == 'plan-billing':
            return Response({'added': plan_open_billing(request.user)})
        return Response(line_row(create_line(request.user, d)), status=status.HTTP_201_CREATED)


class CollectionLineView(Base):
    @guard
    def get(self, request, pk):
        A.need(request, 'billing_view')
        ln = CollectionLine.objects.filter(pk=pk).first()
        if ln is None:
            raise Http404
        r = line_row(ln)
        r['notes'] = [note_row(n) for n in ln.notes.select_related('created_by')[:200]]
        return Response(r)

    @guard
    def patch(self, request, pk):
        A.need(request, 'billing_edit')
        ln = CollectionLine.objects.filter(pk=pk).first()
        if ln is None:
            raise Http404
        update_line(ln, request.user, dict(In(request).d))
        r = line_row(ln)
        r['notes'] = [note_row(n) for n in ln.notes.select_related('created_by')[:200]]
        return Response(r)

    @guard
    def delete(self, request, pk):
        A.need(request, 'billing_edit')
        ln = CollectionLine.objects.filter(pk=pk).first()
        if ln is None:
            raise Http404
        S.close_todos(ln)
        ln.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CollectionReportView(Base):
    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        return Response(report(request.query_params))


class CollectionSourcesView(Base):
    """Pick lists for a new line: CRM opportunities / sales orders, billing items (milestones and AMC lines)."""

    @guard
    def get(self, request):
        A.need(request, 'billing_view')
        q = request.query_params
        if q.get('kind') == 'crm':
            return Response(crm_search(q.get('q', ''), q.get('crm_kind', '')))
        from . import billing as B
        ms = Milestone.objects.filter(project_id__in=A.visible_project_ids(request), amount__gt=0).exclude(invoice_status__in=('paid', 'cancelled'))
        amc = A.in_branches(AMCBilling.objects.all(), request).exclude(status__in=('paid', 'cancelled')).exclude(contract__status__in=('draft', 'cancelled')) \
            .select_related('contract', 'contract__customer')
        out = []
        t = (q.get('q') or '').lower()
        for o in list(ms[:300]) + list(amc[:300]):
            c = B.customer_of(o)
            if c is None:
                continue
            lab = B.item_label(o)
            if t and t not in lab.lower() and t not in c.name.lower():
                continue
            e_amt, e_date = item_expected(o)
            out.append({'kind': 'project' if isinstance(o, Milestone) else 'amc', 'id': o.pk, 'label': lab, 'customer_name': c.name,
                        'stage_label': B.STAGE_LABEL.get(B.stage_of(o), ''), 'expected': str(e_amt), 'date': str(e_date or ''),
                        'planned': str(sum((x.expected_amount for x in o.collection_lines.exclude(status='cancelled')), ZERO))})
        return Response(out[:200])
