"""v1.67.13 (billing) – proforma invoices (PI), tax invoices, payments and collection follow-ups for project milestones
and AMC billing lines.

Billing stages of an item (Milestone.invoice_status / AMCBilling.status):
  planned → pi_to_create → pi_created → pi_sent → to_invoice (invoice to create) → invoiced → partly_paid → paid
  (cancelled aside). Stages move automatically where a document does it (PI made, PI sent, invoice made, payments) and
  can be set by hand; every change is logged in the record's history (Chatter) when the history app is there.

A PI or invoice covers one or more items of ONE customer. Payments are booked against a tax invoice (VAT included) and
shared over its lines in line order, so each item knows how much of it was received.
Follow-ups (PI, invoice, payment) put a to-do in the assigned user's To-do list; the daily job adds to-dos for items
waiting too long, PIs sent without an answer, overdue invoices and missed promised dates.
Invoices can be made in ZEO or in an external accounting system (Settings: invoices made in) – then only the number is
kept. v1.67.14: tax invoices and payments go to ZEO Accounting (and on to Odoo / Zoho Books) – ZeoAccounting/projects_billing.py.
"""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Q

from . import services as S
from .models import (AMCBilling, BillDocument, BillFollowUp, BillLine, BillPayment, Customer, Milestone, ProjectProfile)

ZERO = Decimal('0')
C2 = Decimal('0.01')
STAGE_ORDER = ['planned', 'pi_to_create', 'pi_created', 'pi_sent', 'to_invoice', 'invoiced', 'partly_paid', 'paid']
OPEN_STAGES = ('pi_to_create', 'pi_created', 'pi_sent', 'to_invoice', 'invoiced', 'partly_paid')
STAGE_LABEL = {'planned': 'Planned', 'pi_to_create': 'PI to create', 'pi_created': 'PI created', 'pi_sent': 'PI sent',
               'to_invoice': 'Invoice to create', 'invoiced': 'Invoiced', 'partly_paid': 'Partly paid', 'paid': 'Paid',
               'cancelled': 'Cancelled'}
PAGE = '/main-sidebar/zeo-projects/billing'


def r2(v):
    return Decimal(v or 0).quantize(C2, rounding=ROUND_HALF_UP)


class BillingError(S.ProjectsError):
    pass


# ------------------------------------------------------------------ items (milestone / AMC billing line)
def item_get(kind, pk):
    m = Milestone if kind == 'milestone' else AMCBilling if kind == 'amc' else None
    if m is None:
        raise BillingError('Unknown item.')
    obj = m.objects.filter(pk=pk).first()
    if obj is None:
        raise BillingError('Item not found.')
    return obj


def item_kind(obj):
    return 'milestone' if isinstance(obj, Milestone) else 'amc'


def stage_of(obj):
    return obj.invoice_status if isinstance(obj, Milestone) else obj.status


def _set_stage(obj, stage, user=None, note=''):
    old = stage_of(obj)
    if old == stage:
        return False
    if isinstance(obj, Milestone):
        obj.invoice_status = stage
        obj.save(update_fields=['invoice_status'])
    else:
        obj.status = stage
        obj.save(update_fields=['status'])
    _log(obj, f'Billing stage: {STAGE_LABEL.get(old, old)} → {STAGE_LABEL.get(stage, stage)}' + (f' – {note}' if note else ''), user)
    return True


def _log(obj, text, user=None):
    try:
        from Chatter.models import Message
        Message.objects.create(model=f'{obj._meta.app_label}.{obj._meta.model_name}', object_id=str(obj.pk), body=text,
                               author=user if getattr(user, 'pk', None) else None, kind='note')
    except Exception:   # noqa: BLE001 – the log is a convenience
        pass


def customer_of(obj):
    if isinstance(obj, AMCBilling):
        return obj.contract.customer
    p = ProjectProfile.objects.filter(project_id=obj.project_id).select_related('customer').first()
    return p.customer if p else None


def project_title(pid):
    from ProjectManagement.models import Project
    return Project.objects.filter(pk=pid).values_list('title', flat=True).first() or ''


def item_label(obj):
    if isinstance(obj, Milestone):
        return f'{project_title(obj.project_id)} – {obj.name}'
    return f'{obj.contract.number} – AMC {obj.period_label or obj.due_date}'


def responsible(obj):
    """Who gets the billing to-dos of an item: Settings › billing user, else the project managers (milestone) /
    the customer's account manager (AMC), else the support managers."""
    s = S.settings()
    if s.billing_user_id:
        return [s.billing_user]
    users = []
    if isinstance(obj, Milestone):
        from ProjectManagement.models import Project
        p = Project.objects.filter(pk=obj.project_id).first()
        if p is not None:
            users = [e.users for e in p.managers.all() if getattr(e, 'users_id', None)]
    else:
        am = obj.contract.customer.account_manager
        if am is not None and getattr(am, 'users_id', None):
            users = [am.users]
    return users or S.manager_users()[:3]


def _todo_all(obj, summary, due=None, note='', kind='todo'):
    for u in responsible(obj):
        S.todo(u, summary, obj, page=PAGE, note=note, due=due, kind=kind)


def set_stage(obj, stage, user=None):
    """By hand. Stages a document drives (PI created / invoiced / paid) need the document."""
    if stage not in STAGE_LABEL:
        raise BillingError('Unknown billing stage.', 'stage')
    if stage in ('pi_created', 'invoiced', 'partly_paid'):
        raise BillingError('This stage is set by creating the PI, the invoice or a payment.', 'stage')
    old = stage_of(obj)
    if old in ('invoiced', 'partly_paid', 'paid') and stage in ('planned', 'pi_to_create', 'pi_created', 'pi_sent', 'to_invoice'):
        raise BillingError('The item is already invoiced – cancel its invoice first.', 'stage')
    if stage == 'paid' and old not in ('invoiced', 'partly_paid'):
        raise BillingError('Only an invoiced item can be marked paid.', 'stage')
    _set_stage(obj, stage, user)
    S.close_todos(obj, 'Billing:')
    if stage == 'pi_to_create':
        _todo_all(obj, f'Billing: create the PI – {item_label(obj)}')
    elif stage == 'to_invoice':
        _todo_all(obj, f'Billing: create the invoice – {item_label(obj)}')
    elif stage == 'paid':
        obj.paid_date = obj.paid_date or S.local_today()
        obj.save(update_fields=['paid_date'])
        _close_followups(obj)
        _plan_refresh(obj)
    return obj


# ------------------------------------------------------------------ documents
def _line_amounts(ln):
    ln.amount = r2(Decimal(ln.quantity or 0) * Decimal(ln.unit_price or 0))
    if ln.vat_type != 'standard':
        ln.vat_rate = ZERO
    ln.vat_amount = r2(ln.amount * Decimal(ln.vat_rate or 0) / 100)


def recalc(doc):
    sub = vat = ZERO
    for ln in doc.lines.all():
        sub += ln.amount
        vat += ln.vat_amount
    doc.subtotal, doc.vat_amount, doc.total = r2(sub), r2(vat), r2(sub + vat)
    doc.paid_amount = r2(sum((p.amount for p in doc.payments.all()), ZERO)) if doc.pk else ZERO
    doc.save(update_fields=['subtotal', 'vat_amount', 'total', 'paid_amount'])


def _items_from(refs):
    """refs = [{'kind': 'milestone'|'amc', 'id': n}] → objects (all of one customer)."""
    objs = []
    for r in refs or []:
        objs.append(item_get(r.get('kind'), r.get('id')))
    if not objs:
        return objs, None
    custs = {getattr(customer_of(o), 'pk', None) for o in objs}
    if None in custs:
        raise BillingError('A milestone of a project without a customer cannot be billed – set the project customer first.')
    if len(custs) > 1:
        raise BillingError('A PI or invoice is for one customer – pick items of the same customer.')
    return objs, customer_of(objs[0])


def _write_lines(doc, lines, rate_default):
    doc.lines.all().delete()
    for i, x in enumerate(lines, 1):
        desc = (x.get('description') or '').strip()
        if not desc:
            raise BillingError(f'Line {i}: enter a description.', 'lines')
        ln = BillLine(document=doc, sequence=i, description=desc[:500],
                      quantity=Decimal(str(x.get('quantity') if x.get('quantity') not in (None, '') else 1)),
                      unit_price=Decimal(str(x.get('unit_price') or 0)),
                      vat_type=x.get('vat_type') if x.get('vat_type') in ('standard', 'zero', 'exempt') else 'standard',
                      vat_rate=Decimal(str(x.get('vat_rate'))) if x.get('vat_rate') not in (None, '') else rate_default)
        if ln.quantity <= 0:
            raise BillingError(f'Line {i}: the quantity must be above 0.', 'lines')
        if ln.unit_price < 0:
            raise BillingError(f'Line {i}: the price cannot be negative.', 'lines')
        if x.get('milestone'):
            ln.milestone = Milestone.objects.filter(pk=x['milestone']).first()
        if x.get('amc_billing'):
            ln.amc_billing = AMCBilling.objects.filter(pk=x['amc_billing']).first()
        _line_amounts(ln)
        ln.save()


def default_lines(objs):
    rate = S.settings().default_vat_rate
    out = []
    for o in objs:
        out.append({'description': item_label(o), 'quantity': 1, 'unit_price': str(o.amount), 'vat_type': 'standard', 'vat_rate': str(rate),
                    'milestone': o.pk if isinstance(o, Milestone) else None, 'amc_billing': o.pk if isinstance(o, AMCBilling) else None})
    return out


def _items_of(doc):
    seen, out = set(), []
    for ln in doc.lines.select_related('milestone', 'amc_billing'):
        for o in (ln.milestone, ln.amc_billing):
            if o is not None and (type(o), o.pk) not in seen:
                seen.add((type(o), o.pk))
                out.append(o)
    return out


def _open_doc_for(obj, kind):
    q = BillDocument.objects.filter(kind=kind).exclude(status='cancelled')
    if kind == 'pi':
        q = q.exclude(status='converted')
    q = q.filter(lines__milestone=obj) if isinstance(obj, Milestone) else q.filter(lines__amc_billing=obj)
    return q.distinct().first()


@transaction.atomic
def create_pi(refs, user=None, lines=None, date=None, client_ref='', notes='', currency=None, exchange_rate=None):
    objs, cust = _items_from(refs)
    if not objs and not lines:
        raise BillingError('Pick the milestones or AMC lines to bill.')
    if cust is None:
        raise BillingError('Choose the customer.')
    for o in objs:
        if stage_of(o) in ('invoiced', 'partly_paid', 'paid', 'cancelled'):
            raise BillingError(f'{item_label(o)} is already {STAGE_LABEL[stage_of(o)].lower()}.')
        d = _open_doc_for(o, 'pi')
        if d is not None:
            raise BillingError(f'{item_label(o)} is already on PI {d.number}.')
    s = S.settings()
    doc = BillDocument.objects.create(kind='pi', number=S.next_number('pi'), customer=cust, date=date or S.local_today(),
                                      client_ref=client_ref[:100], notes=notes, terms=s.invoice_terms, currency=currency or s.currency,
                                      exchange_rate=exchange_rate or Decimal('1'), created_by=user if getattr(user, 'pk', None) else None)
    _write_lines(doc, lines if lines else default_lines(objs), s.default_vat_rate)
    recalc(doc)
    for o in _items_of(doc):
        S.close_todos(o, 'Billing:')
        _set_stage(o, 'pi_created', user, doc.number)
        _todo_all(o, f'Billing: send PI {doc.number} to the customer – {item_label(o)}')
    return doc


def pi_create_customer(customer_id, user=None, lines=None, **kw):
    """A PI with free lines only (no milestone / AMC line)."""
    cust = Customer.objects.filter(pk=customer_id).first()
    if cust is None:
        raise BillingError('Choose the customer.', 'customer')
    s = S.settings()
    with transaction.atomic():
        doc = BillDocument.objects.create(kind=kw.get('kind', 'pi'), number=S.next_number('pi' if kw.get('kind', 'pi') == 'pi' else 'inv'), customer=cust,
                                          date=kw.get('date') or S.local_today(), client_ref=(kw.get('client_ref') or '')[:100],
                                          notes=kw.get('notes') or '', terms=s.invoice_terms, currency=kw.get('currency') or s.currency,
                                          created_by=user if getattr(user, 'pk', None) else None)
        _write_lines(doc, lines or [], s.default_vat_rate)
        recalc(doc)
    return doc


def edit_document(doc, user=None, lines=None, **kw):
    if doc.status in ('converted', 'cancelled'):
        raise BillingError('A converted or cancelled document cannot be changed.')
    if doc.kind == 'invoice' and (doc.status != 'draft' or doc.paid_amount):
        raise BillingError('An issued tax invoice cannot be changed – cancel it and make a new one.')
    for k in ('client_ref', 'notes', 'terms'):
        if k in kw and kw[k] is not None:
            setattr(doc, k, kw[k] if k != 'client_ref' else kw[k][:100])
    for k in ('date', 'supply_date', 'due_date'):
        if kw.get(k):
            setattr(doc, k, kw[k])
    doc.save()
    if lines is not None:
        before = {(type(o), o.pk) for o in _items_of(doc)}
        _write_lines(doc, lines, S.settings().default_vat_rate)
        recalc(doc)
        after = {(type(o), o.pk) for o in _items_of(doc)}
        for t, pk in before - after:   # an item taken off the PI goes back to "PI to create"
            o = t.objects.get(pk=pk)
            if stage_of(o) in ('pi_created', 'pi_sent', 'to_invoice'):
                _set_stage(o, 'pi_to_create', user, f'removed from {doc.number}')
    return doc


def mark_sent(doc, user=None, to=''):
    if doc.status in ('cancelled', 'converted'):
        raise BillingError('This document is closed.')
    from django.utils.timezone import now
    doc.sent_at, doc.sent_to = now(), (to or doc.sent_to)[:300]
    if doc.status == 'draft':
        doc.status = 'sent' if doc.kind == 'pi' else 'issued'
    doc.save(update_fields=['sent_at', 'sent_to', 'status'])
    s = S.settings()
    for o in _items_of(doc):
        S.close_todos(o, 'Billing:')
        if doc.kind == 'pi':
            if stage_of(o) == 'pi_created':
                _set_stage(o, 'pi_sent', user, doc.number)
            _todo_all(o, f'Billing: follow up PI {doc.number} – {item_label(o)}', due=S.local_today() + timedelta(days=s.pi_followup_days or 3),
                      kind='follow_up')
    return doc


def send_document(doc, to, message='', user=None):
    to = [x.strip() for x in (to or doc.customer.email or '').replace(';', ',').split(',') if x.strip()]
    if not to:
        raise BillingError('Enter the e-mail address of the customer.', 'to')
    from .billing_pdf import build
    data = build(doc)
    title = 'Proforma invoice' if doc.kind == 'pi' else 'Tax invoice'
    text = message or f'Dear {doc.customer.name},\n\nPlease find attached {title.lower()} {doc.number} for {doc.currency} {doc.total:,.2f}.\n\nRegards'
    ok = S.send_mail(to, f'{title} {doc.number}', text, attachments=[(f'{doc.number}.pdf', data, 'application/pdf')])
    if not ok:
        raise BillingError('The e-mail could not be sent – check the company mail server (the PDF can still be downloaded).')
    return mark_sent(doc, user, ', '.join(to))


def accept_pi(doc, user=None, client_ref=''):
    if doc.kind != 'pi' or doc.status not in ('draft', 'sent'):
        raise BillingError('Only an open PI can be accepted.')
    doc.status = 'accepted'
    if client_ref:
        doc.client_ref = client_ref[:100]
    doc.save(update_fields=['status', 'client_ref'])
    for o in _items_of(doc):
        S.close_todos(o, 'Billing:')
        _close_followups(o, 'pi')
        _set_stage(o, 'to_invoice', user, f'PI {doc.number} accepted')
        _todo_all(o, f'Billing: create the invoice – {item_label(o)}')
    return doc


def _due(cust, date):
    s = S.settings()
    days = cust.payment_days if cust.payment_days is not None else s.default_payment_days
    return date + timedelta(days=days or 0)


@transaction.atomic
def create_invoice(user=None, pi=None, refs=None, lines=None, date=None, supply_date=None, external_no='', client_ref='', notes=''):
    """From a PI (its lines, or the lines given) or straight from items. external_no = invoice made in the accounting
    system (Settings: invoices made in = external), only its number is kept."""
    s = S.settings()
    if s.invoices_made_in == 'external' and not external_no:
        raise BillingError('Invoices are made in your accounting system – enter its invoice number.', 'external_no')
    external_no = (external_no or '').strip()
    if external_no and BillDocument.objects.filter(number=external_no).exists():
        raise BillingError(f'Number {external_no} is already used.', 'external_no')
    if pi is not None:
        if pi.kind != 'pi' or pi.status in ('converted', 'cancelled'):
            raise BillingError('This PI is closed.')
        cust = pi.customer
        src = lines if lines is not None else [{'description': ln.description, 'quantity': str(ln.quantity), 'unit_price': str(ln.unit_price),
                                                'vat_type': ln.vat_type, 'vat_rate': str(ln.vat_rate), 'milestone': ln.milestone_id,
                                                'amc_billing': ln.amc_billing_id} for ln in pi.lines.all()]
    else:
        objs, cust = _items_from(refs)
        if cust is None:
            raise BillingError('Pick the milestones or AMC lines to invoice.')
        for o in objs:
            if stage_of(o) in ('invoiced', 'partly_paid', 'paid', 'cancelled'):
                raise BillingError(f'{item_label(o)} is already {STAGE_LABEL[stage_of(o)].lower()}.')
        src = lines if lines is not None else default_lines(objs)
    d = date or S.local_today()
    doc = BillDocument.objects.create(kind='invoice', number=external_no or S.next_number('inv'), external=bool(external_no), customer=cust,
                                      date=d, supply_date=supply_date, due_date=_due(cust, d), status='issued',
                                      client_ref=(client_ref or (pi.client_ref if pi else ''))[:100], notes=notes or (pi.notes if pi else ''),
                                      terms=s.invoice_terms, currency=pi.currency if pi else s.currency,
                                      exchange_rate=pi.exchange_rate if pi else Decimal('1'), from_pi=pi,
                                      created_by=user if getattr(user, 'pk', None) else None)
    _write_lines(doc, src, s.default_vat_rate)
    recalc(doc)
    for o in _items_of(doc):
        if stage_of(o) in ('invoiced', 'partly_paid', 'paid'):
            raise BillingError(f'{item_label(o)} is already invoiced.')
    if pi is not None:
        pi.status = 'converted'
        pi.save(update_fields=['status'])
    for o in _items_of(doc):
        S.close_todos(o, 'Billing:')
        _close_followups(o, 'pi')
        o.invoice_no, o.invoice_date = doc.number[:60], doc.date   # the existing screens and the portal keep showing them
        o.save(update_fields=['invoice_no', 'invoice_date'])
        _set_stage(o, 'invoiced', user, doc.number)
        _todo_all(o, f'Billing: payment follow-up – invoice {doc.number}, due {doc.due_date:%d/%m/%Y}', due=doc.due_date, kind='follow_up')
    if not doc.external:
        _acc('on_invoice', doc.pk)   # v1.67.14 (billing connectors): journal in ZEO Accounting, posted to Odoo / Zoho when connected
    return doc


def send_to_accounting(doc, user=None):
    """v1.67.14 (billing connectors): the invoice and its payments – make (or remake) their journals and post them when Odoo / Zoho
    Books is connected. Refused for a PI, a cancelled invoice or one made in the accounting system."""
    if doc.kind != 'invoice' or doc.status == 'cancelled':
        raise BillingError('Only an issued tax invoice goes to accounting.')
    if doc.external:
        raise BillingError('This invoice was made in your accounting system – it is already there.')
    try:
        from ZeoAccounting import projects_billing as PB, services as AS
    except ImportError:
        raise BillingError('ZEO Accounting is not installed.')
    st = AS.settings()
    jobs = []
    try:
        for src, pk in [('prj_invoice', doc.pk)] + [('prj_payment', p.pk) for p in doc.payments.all()]:
            have = PB.batch_of(src, pk)
            if have is not None and have.status in AS.DONE + ('posting',):
                continue   # already in accounting
            jobs.append(PB.build(src, pk, user))
    except AS.Refused as ex:
        raise BillingError(ex.message)
    errors = []
    for b in jobs:
        if b.status in AS.DONE:
            continue
        if st.connector in ('odoo', 'zoho') and b.status in ('ready', 'failed'):
            try:
                AS.post(b, user)
            except AS.Refused as ex:
                errors.append(ex.message)
        PB.posted(type(b).objects.get(pk=b.pk))
    if errors:
        raise BillingError('; '.join(errors)[:500])
    return doc


def _acc(hook, *args):
    """v1.67.14 (billing connectors): tell ZEO Accounting (after the commit; never blocks billing)."""
    try:
        from ZeoAccounting import projects_billing as PB
        getattr(PB, hook)(*args)
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('accounting hook %s', hook)


@transaction.atomic
def cancel_document(doc, user=None, reason=''):
    if doc.status in ('cancelled', 'converted'):
        raise BillingError('This document is already closed.')
    if doc.payments.exists():
        raise BillingError('Delete its payments first.')
    if not reason:
        raise BillingError('Give the reason.', 'reason')
    doc.status, doc.cancel_reason = 'cancelled', reason[:255]
    doc.save(update_fields=['status', 'cancel_reason'])
    if doc.kind == 'invoice':
        _acc('on_invoice_cancelled', doc.pk, user)   # v1.67.14: reverse / cancel its journal
    back = 'pi_to_create' if doc.kind == 'pi' else 'to_invoice'
    for o in _items_of(doc):
        S.close_todos(o, 'Billing:')
        if doc.kind == 'invoice':
            o.invoice_no, o.invoice_date = '', None
            o.save(update_fields=['invoice_no', 'invoice_date'])
        _set_stage(o, back, user, f'{doc.number} cancelled: {reason}')
        _plan_refresh(o)
    return doc


# ------------------------------------------------------------------ payments
def received_by_item(doc):
    """{(model, pk): amount} – the invoice's payments shared over its lines in line order (VAT included)."""
    left = Decimal(doc.paid_amount or 0)
    out = {}
    for ln in doc.lines.all():
        due = ln.amount + ln.vat_amount
        take = min(left, due) if left > 0 else ZERO
        left -= take
        for o in (ln.milestone_id and ('m', ln.milestone_id), ln.amc_billing_id and ('a', ln.amc_billing_id)):
            if o:
                k = o
                e = out.setdefault(k, [ZERO, ZERO])
                e[0] += take
                e[1] += due
    return out


def _refresh_items(doc, user=None):
    shares = received_by_item(doc)
    for (t, pk), (got, due) in shares.items():
        o = (Milestone if t == 'm' else AMCBilling).objects.filter(pk=pk).first()
        if o is None:
            continue
        if got >= due and due > 0:
            if stage_of(o) != 'paid':
                last = doc.payments.order_by('-date').first()
                o.paid_date = last.date if last else S.local_today()
                o.save(update_fields=['paid_date'])
                _set_stage(o, 'paid', user, doc.number)
                S.close_todos(o, 'Billing:')
                _close_followups(o)
        elif got > 0:
            _set_stage(o, 'partly_paid', user, doc.number)
            if o.paid_date:
                o.paid_date = None
                o.save(update_fields=['paid_date'])
        else:
            _set_stage(o, 'invoiced', user, doc.number)
        _plan_refresh(o)


def _plan_refresh(o):
    """v1.67.13 (collections): the collection planner lines of the item follow the payments."""
    try:
        from .collection_plan import refresh_item
        refresh_item(o)
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('collection planner refresh')


@transaction.atomic
def add_payment(doc, amount, date=None, method='transfer', reference='', note='', user=None):
    if doc.kind != 'invoice' or doc.status == 'cancelled':
        raise BillingError('Payments are booked against a tax invoice.')
    amount = r2(amount)
    if amount <= 0:
        raise BillingError('Enter the amount received.', 'amount')
    recalc(doc)
    if amount > doc.balance:
        raise BillingError(f'More than the balance ({doc.currency} {doc.balance:,.2f}).', 'amount')
    p = BillPayment.objects.create(document=doc, amount=amount, date=date or S.local_today(), method=method if method in dict(
        BillPayment._meta.get_field('method').choices) else 'other', reference=(reference or '')[:100], note=(note or '')[:255],
        created_by=user if getattr(user, 'pk', None) else None)
    recalc(doc)
    _refresh_items(doc, user)
    _acc('on_payment', p.pk)   # v1.67.14 (billing connectors)
    return p


@transaction.atomic
def delete_payment(p, user=None):
    doc = p.document
    _acc('on_payment_deleted', p.pk, user)   # v1.67.14: reverse / cancel its journal (runs after the commit)
    p.delete()
    recalc(doc)
    _refresh_items(doc, user)
    return doc


# ------------------------------------------------------------------ follow-ups
def _close_followups(obj, kind=None):
    q = BillFollowUp.objects.filter(is_open=True)
    q = q.filter(milestone=obj) if isinstance(obj, Milestone) else q.filter(amc_billing=obj)
    if kind:
        q = q.filter(kind=kind)
    ids = [str(x) for x in q.values_list('pk', flat=True)]   # before the update (the query re-runs afterwards)
    q.update(is_open=False)
    try:
        from Chatter.models import Activity
        from django.utils.timezone import now
        if ids:
            Activity.objects.filter(model='ZeoProjects.billfollowup', object_id__in=ids, state='open').update(state='done', done_at=now())
    except Exception:   # noqa: BLE001
        pass


def _fu_key_qs(obj):
    q = BillFollowUp.objects.all()
    return q.filter(milestone=obj) if isinstance(obj, Milestone) else q.filter(amc_billing=obj)


@transaction.atomic
def add_followup(obj, user=None, kind=None, date=None, method='call', contact='', note='', promised_amount=None, promised_date=None,
                 next_date=None, assigned_to=None, payment=None):
    st = stage_of(obj)
    if st in ('paid', 'cancelled'):
        raise BillingError('The item is paid – no follow-up needed.')
    kind = kind or ('pi' if st in ('pi_created', 'pi_sent') else 'invoice' if st == 'to_invoice' else 'payment')
    if not note:
        raise BillingError('Write what was discussed.', 'note')
    doc = None
    if kind == 'pi':
        doc = _open_doc_for(obj, 'pi')
    elif kind in ('invoice', 'payment'):
        doc = _open_doc_for(obj, 'invoice')
    # the earlier open follow-up of the item is closed: one open follow-up per item
    old = list(_fu_key_qs(obj).filter(is_open=True).values_list('pk', flat=True))
    _fu_key_qs(obj).filter(is_open=True).update(is_open=False)
    try:
        from Chatter.models import Activity
        from django.utils.timezone import now
        if old:
            Activity.objects.filter(model='ZeoProjects.billfollowup', object_id__in=[str(x) for x in old], state='open').update(state='done', done_at=now())
    except Exception:   # noqa: BLE001
        pass
    S.close_todos(obj, 'Billing:')
    who = assigned_to or (user if getattr(user, 'pk', None) else None)
    f = BillFollowUp.objects.create(kind=kind, milestone=obj if isinstance(obj, Milestone) else None,
                                    amc_billing=obj if isinstance(obj, AMCBilling) else None, document=doc, date=date or S.local_today(),
                                    method=method or 'call', contact=(contact or '')[:150], note=note,
                                    promised_amount=r2(promised_amount) if promised_amount not in (None, '') else None,
                                    promised_date=promised_date, next_date=next_date, assigned_to=who,
                                    created_by=user if getattr(user, 'pk', None) else None)
    _log(obj, f'{f.get_kind_display()}: {note}' + (f' – promised {promised_date:%d/%m/%Y}' if promised_date else ''), user)
    if payment and doc is not None and payment.get('amount'):
        add_payment(doc, payment['amount'], payment.get('date') or f.date, payment.get('method') or 'transfer', payment.get('reference', ''),
                    f'From follow-up: {note[:200]}', user)
    if stage_of(obj) not in ('paid', 'cancelled'):
        due = next_date or promised_date
        if due and who is not None:
            S.todo(who, f'{f.get_kind_display()}: {item_label(obj)}'[:255], f, page=PAGE, note=note, due=due, kind='follow_up')
    else:
        f.is_open = False
        f.save(update_fields=['is_open'])
    return f


# ------------------------------------------------------------------ collections
def item_row(o, docs_cache=None):
    st = stage_of(o)
    cust = customer_of(o)
    pi = inv = None
    for ln in o.bill_lines.select_related('document').order_by('-document__date', '-document__id'):
        d = ln.document
        if d.status == 'cancelled':
            continue
        if d.kind == 'pi' and pi is None:
            pi = d
        if d.kind == 'invoice' and inv is None:
            inv = d
    invoiced = received = ZERO
    if inv is not None:
        sh = received_by_item(inv).get(('m' if isinstance(o, Milestone) else 'a', o.pk))
        if sh:
            received, invoiced = sh
    fu = _fu_key_qs(o).order_by('-date', '-id').first()
    today = S.local_today()
    due = inv.due_date if inv else None
    bal = r2(invoiced - received)
    is_m = isinstance(o, Milestone)
    return {'kind': 'milestone' if is_m else 'amc', 'id': o.pk, 'key': f"{'milestone' if is_m else 'amc'}-{o.pk}",
            'label': item_label(o), 'customer': cust.pk if cust else None, 'customer_name': cust.name if cust else '',
            'project_id': o.project_id if is_m else None, 'contract': None if is_m else o.contract_id,
            'work_status': o.status if is_m else '', 'work_status_label': o.get_status_display() if is_m else '',
            'amount': str(o.amount), 'stage': st, 'stage_label': STAGE_LABEL.get(st, st),
            'planned_date': str((o.planned_date if is_m else o.due_date) or ''),
            'pi': {'id': pi.id, 'number': pi.number, 'status': pi.status, 'total': str(pi.total)} if pi else None,
            'invoice': {'id': inv.id, 'number': inv.number, 'date': str(inv.date), 'due_date': str(inv.due_date or ''), 'total': str(inv.total),
                        'external': inv.external} if inv else None,
            'legacy_invoice_no': o.invoice_no if inv is None else '',
            'invoiced': str(r2(invoiced)), 'received': str(r2(received)), 'balance': str(bal),
            'due_date': str(due or ''), 'overdue_days': (today - due).days if due and due < today and bal > 0 else 0,
            'promised_date': str(fu.promised_date or '') if fu and fu.is_open else '',
            'promised_amount': str(fu.promised_amount or '') if fu and fu.is_open else '',
            'next_followup': str(fu.next_date or '') if fu and fu.is_open else '',
            'last_note': fu.note[:200] if fu else '', 'last_followup': str(fu.date) if fu else ''}


def _items_qs(request, A):
    ms = Milestone.objects.filter(project_id__in=A.visible_project_ids(request)).filter(Q(amount__gt=0) | Q(bill_lines__isnull=False)).distinct()
    amc = A.in_branches(AMCBilling.objects.all(), request).select_related('contract', 'contract__customer') \
        .exclude(contract__status__in=('draft', 'cancelled'))
    return ms, amc


def collections(request, A, q):
    ms, amc = _items_qs(request, A)
    stage = q.get('stage') or ''
    if q.get('project'):
        ms = ms.filter(project_id=q['project'])
        amc = amc.none()
    if q.get('contract'):
        amc = amc.filter(contract_id=q['contract'])
        ms = ms.none()
    if q.get('customer'):
        pids = list(ProjectProfile.objects.filter(customer_id=q['customer']).values_list('project_id', flat=True))
        ms = ms.filter(project_id__in=pids)
        amc = amc.filter(contract__customer_id=q['customer'])
    if q.get('kind') == 'milestone':
        amc = amc.none()
    elif q.get('kind') == 'amc':
        ms = ms.none()
    # finished work only (and AMC lines due within the window) unless "all"
    if stage != 'all':
        ms = ms.exclude(invoice_status__in=('paid', 'cancelled')).filter(Q(status='accepted') | ~Q(invoice_status='planned'))
        horizon = S.local_today() + timedelta(days=int(q.get('days') or 30))
        amc = amc.exclude(status__in=('paid', 'cancelled')).filter(Q(due_date__lte=horizon) | ~Q(status='planned'))
    rows = [item_row(o) for o in list(ms[:1000]) + list(amc[:1000])]
    if q.get('q'):
        t = q['q'].lower()
        rows = [r for r in rows if t in r['label'].lower() or t in r['customer_name'].lower()
                or t in ((r['invoice'] or {}).get('number') or '').lower() or t in ((r['pi'] or {}).get('number') or '').lower()]
    today = S.local_today()
    totals = {k: {'count': 0, 'amount': ZERO} for k in ('pi_to_create', 'pi_sent', 'to_invoice', 'outstanding', 'overdue', 'week', 'month', 'followup_today')}
    week_end, month_end = today + timedelta(days=7 - today.weekday()), (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    for r in rows:
        amt, bal = Decimal(r['amount']), Decimal(r['balance'])
        if r['stage'] in ('planned', 'pi_to_create'):
            totals['pi_to_create']['count'] += 1
            totals['pi_to_create']['amount'] += amt
        elif r['stage'] in ('pi_created', 'pi_sent'):
            totals['pi_sent']['count'] += 1
            totals['pi_sent']['amount'] += amt
        elif r['stage'] == 'to_invoice':
            totals['to_invoice']['count'] += 1
            totals['to_invoice']['amount'] += amt
        if bal > 0:
            totals['outstanding']['count'] += 1
            totals['outstanding']['amount'] += bal
            if r['overdue_days']:
                totals['overdue']['count'] += 1
                totals['overdue']['amount'] += bal
            exp = r['promised_date'] or r['due_date']
            if exp:
                from datetime import date as _d
                e = _d.fromisoformat(exp)
                if e <= week_end:
                    totals['week']['count'] += 1
                    totals['week']['amount'] += bal
                if e <= month_end:
                    totals['month']['count'] += 1
                    totals['month']['amount'] += bal
        if r['next_followup'] and r['next_followup'] <= str(today):
            totals['followup_today']['count'] += 1
    if stage and stage != 'all':
        if stage == 'outstanding':
            rows = [r for r in rows if Decimal(r['balance']) > 0]
        elif stage == 'overdue':
            rows = [r for r in rows if r['overdue_days']]
        elif stage == 'followup_today':
            rows = [r for r in rows if r['next_followup'] and r['next_followup'] <= str(today)]
        elif stage == 'pi_to_create':
            rows = [r for r in rows if r['stage'] in ('planned', 'pi_to_create')]
        elif stage == 'pi_sent':
            rows = [r for r in rows if r['stage'] in ('pi_created', 'pi_sent')]
        else:
            rows = [r for r in rows if r['stage'] in stage.split(',')]
    rows.sort(key=lambda r: (-(r['overdue_days'] or 0), r['next_followup'] or '9999', r['due_date'] or '9999', r['label']))
    return {'totals': {k: {'count': v['count'], 'amount': str(r2(v['amount']))} for k, v in totals.items()}, 'items': rows,
            'currency': S.settings().currency, 'invoices_made_in': S.settings().invoices_made_in}


# ------------------------------------------------------------------ daily job
def reminders():
    """To-dos for: items waiting for their PI / invoice too long, PIs sent without an answer, overdue invoices without an
    open follow-up, promised dates passed. Once a day; S.todo never doubles an open to-do."""
    s = S.settings()
    today = S.local_today()
    n = 0
    wait = today - timedelta(days=s.pi_to_create_days or 2)
    for o in list(Milestone.objects.filter(invoice_status__in=('pi_to_create', 'to_invoice'))) + \
            list(AMCBilling.objects.filter(status__in=('pi_to_create', 'to_invoice')).select_related('contract', 'contract__customer')):
        what = 'create the PI' if stage_of(o) == 'pi_to_create' else 'create the invoice'
        last = _last_change(o)
        if last is None or last <= wait:
            _todo_all(o, f'Billing: {what} – {item_label(o)} (waiting)')
            n += 1
    for doc in BillDocument.objects.filter(kind='pi', status='sent', sent_at__isnull=False):
        if doc.sent_at.date() <= today - timedelta(days=s.pi_followup_days or 3):
            for o in _items_of(doc):
                if not _fu_key_qs(o).filter(is_open=True).exists():
                    _todo_all(o, f'Billing: follow up PI {doc.number} – {item_label(o)}', kind='follow_up')
                    n += 1
    for doc in BillDocument.objects.filter(kind='invoice', status='issued', due_date__lt=today):
        if doc.balance <= 0:
            continue
        for o in _items_of(doc):
            if stage_of(o) == 'paid':
                continue
            fu = _fu_key_qs(o).filter(is_open=True).first()
            if fu is None:
                _todo_all(o, f'Billing: invoice {doc.number} overdue – payment follow-up – {item_label(o)}', kind='follow_up')
                n += 1
            elif fu.promised_date and fu.promised_date < today:
                _todo_all(o, f'Billing: promise missed ({fu.promised_date:%d/%m/%Y}) – {item_label(o)}', kind='follow_up')
                n += 1
    return n


def _last_change(o):
    try:
        from Chatter.models import Message
        m = Message.objects.filter(model=f'{o._meta.app_label}.{o._meta.model_name}', object_id=str(o.pk),
                                   body__startswith='Billing stage:').order_by('-id').first()
        return m.created_at.date() if m else None
    except Exception:   # noqa: BLE001
        return None
