"""Business rules of Projects: numbering, tickets and their SLA clock, AMC coverage and hours, renewals and billing,
development requests, project milestones / change requests, notifications (e-mail + ZEO To-do)."""
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from . import sla
from .models import (DONE_STAGES, OPEN_STAGES, PAUSE_STAGES, PRIORITY_ORDER, ZERO, AMCBilling, AMCContract, ChangeRequest,
                     Contact, Customer, DevRequest, IssueCategory, Milestone, ProjectExpense, ProjectProfile, ProjectsSettings,
                     Sequence, ServiceProduct, SLAPolicy, SLATarget, SupportTeam, Ticket, TicketEvent, TicketFile,
                     TicketMessage, WorkLog)

log = logging.getLogger(__name__)
D0 = Decimal('0')
STAGE_LABEL = dict(Ticket._meta.get_field('stage').choices)


class ProjectsError(Exception):
    def __init__(self, message, field=None, status=400):
        super().__init__(message)
        self.message = message
        self.field = field
        self.status = status


def q2(v):
    return Decimal(v or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def now():
    return timezone.now()


def settings():
    return ProjectsSettings.get()


def local_today():
    try:
        from zoneinfo import ZoneInfo
        return timezone.now().astimezone(ZoneInfo(settings().timezone or 'Asia/Dubai')).date()
    except Exception:
        return date.today()


# ------------------------------------------------------------------ numbering
def next_number(kind):
    """TKT-00001 style numbers; kind = ticket / amc / dev / cr / project / customer."""
    s = settings()
    prefix = (getattr(s, f'{kind}_prefix', '') or kind.upper()).strip()
    with transaction.atomic():
        seq, _ = Sequence.objects.select_for_update().get_or_create(key=kind)
        n = seq.next_number
        seq.next_number = n + 1
        seq.save(update_fields=['next_number'])
    return f'{prefix}-{str(n).zfill(s.number_padding or 5)}'


# ------------------------------------------------------------------ people
def emp_name(e):
    if e is None:
        return ''
    name = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x and str(x).strip())
    return name or e.emp_code


def emp_label(e):
    if e is None:
        return ''
    n = emp_name(e)
    return f'{n} ({e.emp_code})' if n != e.emp_code else n


def emp_email(e):
    return (e.emp_company_email or e.emp_personal_email or '') if e else ''


def user_name(u):
    if u is None:
        return ''
    try:
        from EmpManagement.models import emp_master
        e = emp_master.objects.filter(users=u).first()
        if e:
            return emp_name(e)
    except Exception:
        pass
    full = ' '.join(x for x in [getattr(u, 'first_name', ''), getattr(u, 'last_name', '')] if x)
    return full or u.username


def emp_of_user(u):
    from EmpManagement.models import emp_master
    return emp_master.objects.filter(users=u).first() if u else None


def manager_users():
    """Users holding assign_ticket (support managers) in this company."""
    from django.contrib.auth.models import Group
    from tenant_users.tenants.models import UserTenantPermissions
    groups = Group.objects.filter(permissions__codename='assign_ticket', permissions__content_type__app_label='ZeoProjects')
    users = []
    for utp in _company_perms().filter(Q(groups__in=groups) | Q(is_superuser=True)).select_related('profile').distinct():
        if utp.profile and utp.profile.is_active and not hasattr_contact(utp.profile):
            users.append(utp.profile)
    return users


def hasattr_contact(u):
    return Contact.objects.filter(portal_user=u).exists()


# ------------------------------------------------------------------ notifications
def send_mail(to, subject, body, reply_to=None, attachments=()):
    """Send with the company's active mail settings (Settings → E-mail configuration), else the server default.
    Never raises: a failed e-mail must not stop the work."""
    to = [x for x in dict.fromkeys(t.strip() for t in (to or []) if t and '@' in t)]
    if not to:
        return False
    try:
        from django.core.mail import EmailMessage, get_connection
        from django.conf import settings as dj
        conn, sender = None, dj.DEFAULT_FROM_EMAIL
        try:
            from EmpManagement.models import EmailConfiguration
            cfg = EmailConfiguration.objects.filter(is_active=True).first()
            if cfg and cfg.email_host_user:
                conn = get_connection(host=cfg.email_host, port=cfg.email_port, username=cfg.email_host_user,
                                      password=cfg.email_host_password, use_tls=cfg.email_use_tls, fail_silently=False)
                sender = cfg.email_host_user
        except Exception:
            conn = None
        msg = EmailMessage(subject=subject[:200], body=body, from_email=sender, to=to, connection=conn,
                           reply_to=[reply_to] if reply_to else None)
        for name, content, mime in attachments or ():
            msg.attach(name, content, mime)
        msg.send(fail_silently=False)
        return True
    except Exception:
        log.exception('Projects e-mail to %s failed', to)
        return False


def todo(user, summary, obj, page='', note='', due=None, kind='todo'):
    """Put an item in the user's ZEO To-do (Chatter activity) once per record and summary."""
    if user is None:
        return None
    try:
        from Chatter.models import Activity
        key = f'{obj._meta.app_label}.{obj._meta.model_name}'
        if Activity.objects.filter(model=key, object_id=str(obj.pk), assigned_to=user, summary=summary[:255], state='open').exists():
            return None
        return Activity.objects.create(model=key, object_id=str(obj.pk), record_label=str(obj)[:255], page=page[:255],
                                       activity_type=kind, summary=summary[:255], note=note, due_date=due or local_today(),
                                       assigned_to=user)
    except Exception:
        log.exception('Projects to-do failed')
        return None


def close_todos(obj, summary_startswith=None):
    try:
        from Chatter.models import Activity
        key = f'{obj._meta.app_label}.{obj._meta.model_name}'
        qs = Activity.objects.filter(model=key, object_id=str(obj.pk), state='open')
        if summary_startswith:
            qs = qs.filter(summary__startswith=summary_startswith)
        qs.update(state='done', done_at=now())
    except Exception:
        log.exception('Projects to-do close failed')


def _customer_of(obj):
    if obj is None:
        return None
    if isinstance(obj, Customer):
        return obj.pk
    return getattr(obj, 'customer_id', None)


def notify_emp(e, subject, text, obj=None, page='', todo_summary=None, event=None, customer=None):
    """E-mail an employee and (optionally) put an item in their To-do – as the notification settings allow
    (user > customer > company, see notify.py)."""
    if e is None:
        return
    from .notify import wants
    cust = customer if customer is not None else _customer_of(obj)
    user = e.users if e.users_id else None
    if wants(event, 'email', user, cust):
        send_mail([emp_email(e)], subject, text)
    if todo_summary and obj is not None and user is not None and wants(event, 'dashboard', user, cust):
        todo(user, todo_summary, obj, page=page, note=text)


def notify_users(users, subject, text, obj=None, page='', todo_summary=None, event=None, customer=None):
    from .notify import wants
    cust = customer if customer is not None else _customer_of(obj)
    seen = set()
    for u in users:
        if u is None or u.pk in seen:
            continue
        seen.add(u.pk)
        if wants(event, 'email', u, cust):
            send_mail([u.email], subject, text)
        if todo_summary and obj is not None and wants(event, 'dashboard', u, cust):
            todo(u, todo_summary, obj, page=page, note=text)


def ticket_page(t):
    return f'/main-sidebar/zeo-projects/tickets/{t.pk}'


def customer_emails(t):
    out = []
    if t.contact and t.contact.email:
        out.append(t.contact.email)
    elif t.requester_email:
        out.append(t.requester_email)
    elif t.requester_employee:
        out.append(emp_email(t.requester_employee))
    return out


def notify_customer(t, subject, text, event='ticket_update'):
    from .notify import wants
    if not settings().notify_customer or not wants(event, 'email', customer=t.customer_id):
        return
    send_mail(customer_emails(t), f'[{t.number}] {subject}', text)


# ------------------------------------------------------------------ AMC coverage and hours
def active_contract(customer, on=None, product=None):
    """The customer's contract in force on the date that covers the product (any product when the contract lists none)."""
    if customer is None:
        return None
    on = on or local_today()
    qs = AMCContract.objects.filter(customer=customer, start_date__lte=on, end_date__gte=on,
                                    status__in=('active', 'expiring', 'renewed')).order_by('-start_date')
    fallback = None
    for c in qs.prefetch_related('products'):
        ids = {p.pk for p in c.products.all()}
        if not ids or (product is not None and product.pk in ids):
            return c
        fallback = fallback or c
    return fallback


def contract_usage(c):
    """Hours and money of a contract: included / used / remaining, onsite, chargeable work outside, cost and margin."""
    logs = WorkLog.objects.filter(ticket__contract=c).exclude(status='rejected')
    amc_logs = logs.filter(counts_against_amc=True)
    used = q2(amc_logs.aggregate(s=Sum('hours'))['s'])
    onsite = q2(amc_logs.filter(onsite=True).aggregate(s=Sum('hours'))['s'])
    remote = q2(used - onsite)
    out_logs = logs.filter(Q(billable=True) | Q(counts_against_amc=False))
    outside_hours = q2(out_logs.aggregate(s=Sum('hours'))['s'])
    charges = q2(logs.aggregate(s=Sum('travel_cost'))['s']) + q2(logs.aggregate(s=Sum('other_charges'))['s'])
    outside_value = q2(outside_hours * (c.hourly_rate_outside or 0)) + charges
    cost = q2(logs.aggregate(s=Sum('cost_amount'))['s'])
    billed = q2(c.billing.filter(status__in=('invoiced', 'paid')).aggregate(s=Sum('amount'))['s'])
    paid = q2(c.billing.filter(status='paid').aggregate(s=Sum('amount'))['s'])
    included = q2(c.included_hours)
    remaining = None if c.unlimited_hours else q2(included - used)
    revenue = q2(c.value) + outside_value
    return {
        'unlimited': c.unlimited_hours, 'included_hours': included, 'used_hours': used, 'remote_hours': remote,
        'onsite_hours': onsite, 'onsite_limit': q2(c.onsite_hours), 'remaining_hours': remaining,
        'used_pct': None if c.unlimited_hours or not included else float(q2(used * 100 / included)),
        'outside_hours': outside_hours, 'outside_value': outside_value, 'charges': charges,
        'tickets': c.tickets.count(), 'open_tickets': c.tickets.filter(stage__in=OPEN_STAGES).count(),
        'value': q2(c.value), 'billed': billed, 'paid': paid, 'outstanding': q2(billed - paid),
        'cost': cost, 'revenue': revenue, 'margin': q2(revenue - cost),
        'margin_pct': float(q2((revenue - cost) * 100 / revenue)) if revenue else None,
    }


def apply_coverage(t):
    """Link the active AMC and decide AMC covered / chargeable / internal."""
    if t.customer_id is None:
        t.contract = None
        t.covered = False
        t.billable_status = 'internal'
        t.coverage_note = 'Internal helpdesk ticket'
        return
    on = local_today()
    if t.contract_id is None or t.contract.customer_id != t.customer_id:
        t.contract = active_contract(t.customer, on, t.product)
    c = t.contract
    cat = t.category
    if c is None:
        t.covered = False
        t.billable_status = (cat.default_billable if cat and cat.default_billable else 'chargeable')
        t.coverage_note = 'No active AMC – chargeable work'
        return
    ids = set(c.products.values_list('pk', flat=True))
    reasons = []
    if ids and t.product_id and t.product_id not in ids:
        reasons.append(f'{t.product.name} is not covered by {c.number}')
    if cat and cat.needs_development:
        reasons.append('new development is excluded from the AMC')
    if not c.unlimited_hours:
        u = contract_usage(c)
        if u['remaining_hours'] is not None and u['remaining_hours'] <= 0:
            reasons.append('the AMC hours are used up')
    if reasons:
        t.covered = False
        t.billable_status = 'chargeable'
        t.coverage_note = ('Chargeable: ' + '; '.join(reasons))[:255]
    else:
        t.covered = True
        t.billable_status = cat.default_billable if (cat and cat.default_billable) else 'amc'
        t.coverage_note = f'Covered by {c.number}'


# ------------------------------------------------------------------ v1.15.0 work approval
def needs_cover_approval(t):
    return bool(settings().approval_not_covered and t.customer_id and t.billable_status == 'chargeable')


def refresh_approval(t, user=None, notify=True):
    """Covered by an AMC → anyone may allocate and work. Not covered → a manager approves before work starts."""
    if needs_cover_approval(t):
        if t.approval_status == 'none':
            t.approval_status, t.approval_reason = 'pending', 'not_covered'
            t.approval_requested_hours, t.approval_at, t.approval_by = None, None, None
            t.save()
            event(t, 'approval', 'Waiting for approval: not covered by an AMC (chargeable work)', user, public=False)
            if notify:
                notify_users(manager_users(), f'[{t.number}] Approve chargeable ticket',
                             f'{t.coverage_note or "Not covered by an AMC"}.\nApprove (and assign) or reject the work.\n\n' + ticket_text(t),
                             t, ticket_page(t), f'Approve ticket {t.number}', event='work_approval')
    elif t.approval_status in ('pending', 'rejected') and t.approval_reason == 'not_covered':
        t.approval_status, t.approval_reason = 'none', ''
        t.save()
        close_todos(t, f'Approve ticket {t.number}')
        event(t, 'approval', 'Approval no longer needed (covered by the AMC)', user, public=False)


def hours_limit(t):
    lim = q2(settings().approval_hours_limit or 0)
    return None if lim <= 0 else lim + q2(t.approved_extra_hours)


def logged_hours(t):
    return q2(t.worklogs.exclude(status='rejected').aggregate(s=Sum('hours'))['s'])


def work_allowed(t, manager=False):
    if manager:
        return
    if t.approval_reason == 'not_covered' and t.approval_status == 'pending':
        raise ProjectsError('This ticket is not covered by an AMC. A support manager must approve the work first.', 'approval')
    if t.approval_reason == 'not_covered' and t.approval_status == 'rejected':
        raise ProjectsError('The work on this ticket was not approved.', 'approval')


def request_more_time(t, user, hours, note=''):
    try:
        hours = q2(Decimal(str(hours)))
    except Exception:
        raise ProjectsError('Hours must be a number.', 'hours')
    if hours <= 0 or hours > 200:
        raise ProjectsError('Ask for more than 0 and at most 200 hours.', 'hours')
    if not (note or '').strip():
        raise ProjectsError('Say why more time is needed.', 'note')
    if t.approval_status == 'pending':
        raise ProjectsError('An approval is already waiting on this ticket.', 'approval')
    t.approval_status, t.approval_reason, t.approval_requested_hours = 'pending', 'over_hours', hours
    t.approval_note, t.approval_by, t.approval_at = note.strip(), None, None
    t.save()
    who = user_name(user)
    event(t, 'approval', f'{who} asked for {hours} more hours: {note.strip()}', user, public=False)
    notify_users(manager_users(), f'[{t.number}] More time requested ({hours} h)',
                 f'{who} asks for {hours} more hours.\nReason: {note.strip()}\nLogged so far: {logged_hours(t)} h, '
                 f'limit {hours_limit(t)} h.\n\n' + ticket_text(t), t, ticket_page(t), f'Approve ticket {t.number}', event='work_approval')
    return t


def decide_approval(t, user, approve, hours=None, note='', assign_to=None):
    if t.approval_status != 'pending':
        raise ProjectsError('Nothing is waiting for approval on this ticket.', 'approval')
    who = user_name(user)
    t.approval_by, t.approval_at = user, now()
    if approve:
        t.approval_status = 'approved'
        if t.approval_reason == 'over_hours':
            extra = q2(Decimal(str(hours))) if hours not in (None, '') else q2(t.approval_requested_hours)
            if extra <= 0:
                raise ProjectsError('Approve more than 0 hours.', 'hours')
            t.approved_extra_hours = q2(t.approved_extra_hours) + extra
            text = f'{who} approved {extra} more hours.'
        else:
            text = f'{who} approved the chargeable work.'
    else:
        if not (note or '').strip():
            raise ProjectsError('Give a reason for the rejection.', 'note')
        t.approval_status = 'rejected'
        text = f'{who} rejected: {note.strip()}'
    if note and approve:
        text += f' {note.strip()}'
    t.save()
    close_todos(t, f'Approve ticket {t.number}')
    event(t, 'approval', text, user, public=False)
    if approve and t.approval_reason == 'not_covered' and t.assigned_to_id is None:
        target = assign_to or (least_busy_member(t.team) if t.team_id and t.team.auto_assign else None)
        if target is not None:
            assign(t, target, t.team, user)
    elif t.assigned_to_id:
        notify_emp(t.assigned_to, f'[{t.number}] Approval {"given" if approve else "refused"}', text + '\n\n' + ticket_text(t),
                   t, ticket_page(t), f'Approval on ticket {t.number}' if not approve else None, event='work_approval')
    return t


# ------------------------------------------------------------------ SLA
def sla_policy_for(t):
    for p in (t.contract.sla_policy if t.contract_id else None,
              t.customer.default_sla if t.customer_id else None,
              settings().default_sla):
        if p is not None and p.is_active:
            return p
    return SLAPolicy.objects.filter(is_active=True, is_default=True).first() or SLAPolicy.objects.filter(is_active=True).first()


def apply_sla(t, keep_start=True):
    """Set the response / resolution targets and due times from the policy and priority (from the ticket's creation,
    plus the time it waited for the customer)."""
    p = sla_policy_for(t)
    t.sla_policy = p
    target = SLATarget.objects.filter(policy=p, priority=t.priority).first() if p else None
    if target is None:
        t.response_minutes = t.resolution_minutes = None
        t.response_due = t.resolution_due = None
        return
    business = target.business_hours and not (t.contract_id and t.contract.support_timing == '24x7')
    t.sla_business_hours = business
    t.response_minutes, t.resolution_minutes = target.response_minutes, target.resolution_minutes
    start = t.created_at or now()
    cal = sla.WorkCalendar()
    t.response_due = sla.add_minutes(start, target.response_minutes, business, cal)
    t.resolution_due = sla.add_minutes(start, target.resolution_minutes + (t.paused_minutes or 0), business, cal)


def sla_state(t, at=None):
    """'met' / 'breached' / 'at_risk' / 'ok' / 'paused' / 'none' for the resolution target, with minutes left."""
    at = at or now()
    if not t.resolution_due:
        return {'state': 'none', 'minutes_left': None}
    if t.stage in DONE_STAGES:
        ok = t.resolved_at and t.resolved_at <= t.resolution_due and not t.resolution_breached
        return {'state': 'met' if ok else 'breached', 'minutes_left': None}
    if t.paused_at:
        return {'state': 'paused', 'minutes_left': None}
    if t.resolution_breached or at > t.resolution_due:
        return {'state': 'breached', 'minutes_left': -sla.minutes_between(t.resolution_due, at, t.sla_business_hours)}
    left = sla.minutes_between(at, t.resolution_due, t.sla_business_hours)
    total = t.resolution_minutes or 1
    return {'state': 'at_risk' if left <= max(30, total * 0.2) else 'ok', 'minutes_left': left}


# ------------------------------------------------------------------ tickets
def event(t, kind, text, user=None, public=True):
    TicketEvent.objects.create(ticket=t, kind=kind, text=text[:500], public=public, user=user, user_name=user_name(user) if user else 'System')


def pick_team(t):
    for team in (t.category.default_team if t.category_id else None,
                 t.product.default_team if t.product_id else None,
                 settings().default_team):
        if team is not None and team.is_active:
            return team
    return None


def least_busy_member(team):
    members = list(team.members.filter(is_active=True))
    if not members:
        return team.lead
    loads = {m.pk: Ticket.objects.filter(assigned_to=m, stage__in=OPEN_STAGES).count() for m in members}
    return min(members, key=lambda m: (loads[m.pk], m.pk))


def create_ticket(*, subject, description='', user=None, channel='manual', customer=None, contact=None, employee=None,
                  product=None, category=None, priority='medium', team=None, assigned_to=None, contract=None,
                  billable_status=None, requester_email='', requester_name='', files=(), message_id='', approved_by=None):
    subject = (subject or '').strip()
    if not subject:
        raise ProjectsError('Subject is required.', 'subject')
    if priority not in PRIORITY_ORDER:
        raise ProjectsError('Priority must be low, medium, high or critical.', 'priority')
    if contact is not None and customer is None:
        customer = contact.customer
    if contact is not None and customer is not None and contact.customer_id != customer.pk:
        raise ProjectsError('The contact does not belong to this customer.', 'contact')
    if contract is not None and customer is not None and contract.customer_id != customer.pk:
        raise ProjectsError('The contract belongs to another customer.', 'contract')
    with transaction.atomic():
        t = Ticket(number=next_number('ticket'), subject=subject[:255], description=description or '', customer=customer,
                   contact=contact, requester_employee=employee, product=product, category=category, priority=priority,
                   channel=channel, contract=contract, created_by=user if (user and user.is_authenticated) else None,
                   requester_email=requester_email or '', requester_name=requester_name or '', email_message_id=message_id or '')
        t.save()
        apply_coverage(t)
        if billable_status:
            t.billable_status = billable_status
        t.team = team or pick_team(t)
        cover_approval = needs_cover_approval(t)
        if cover_approval and approved_by is not None:
            t.approval_status, t.approval_reason, t.approval_by, t.approval_at = 'approved', 'not_covered', approved_by, now()
            cover_approval = False
        if assigned_to is None and t.team and t.team.auto_assign and not cover_approval:
            assigned_to = least_busy_member(t.team)
        apply_sla(t)
        t.save()
        who = contact.name if contact else (emp_name(employee) if employee else (requester_name or user_name(user)))
        event(t, 'created', f'Ticket created by {who or "support"} ({dict(Ticket._meta.get_field("channel").choices).get(channel, channel)}).', user)
        if t.coverage_note:
            event(t, 'coverage', t.coverage_note, user, public=False)
        for f in files or ():
            add_file(t, f, user)
        if cover_approval:
            refresh_approval(t, user)
        if assigned_to is not None:
            assign(t, assigned_to, t.team, user, notify=True, initial=True)
        elif not cover_approval:
            notify_users(manager_users(), f'[{t.number}] New unassigned ticket', ticket_text(t), t, ticket_page(t),
                         f'Assign ticket {t.number}', event='ticket_unassigned')
    notify_customer(t, 'We have received your request',
                    f'Dear {who or "customer"},\n\nYour request "{t.subject}" is registered as {t.number} '
                    f'with {t.get_priority_display()} priority.' +
                    (f'\nWe aim to respond by {fmt(t.response_due)} and resolve it by {fmt(t.resolution_due)}.' if t.response_due else '') +
                    '\n\nYou can follow it in the customer portal.\n', event='ticket_new')
    if t.customer_id and t.customer.account_manager_id and t.customer.account_manager_id != t.assigned_to_id:
        notify_emp(t.customer.account_manager, f'[{t.number}] New ticket from {t.customer.name}', ticket_text(t), t, ticket_page(t),
                   event='ticket_new_consultant')
    return t


def fmt(dt):
    if not dt:
        return ''
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(settings().timezone or 'Asia/Dubai')).strftime('%d %b %Y %H:%M')
    except Exception:
        return dt.strftime('%d %b %Y %H:%M')


def ticket_text(t):
    return (f'{t.number}: {t.subject}\nCustomer: {t.customer.name if t.customer_id else "Internal"}'
            f'\nPriority: {t.get_priority_display()}\nStage: {t.get_stage_display()}'
            f'\nResolve by: {fmt(t.resolution_due) or "-"}\n\n{(t.description or "")[:1500]}')


def assign(t, emp, team=None, user=None, notify=True, initial=False):
    old = t.assigned_to
    if team is not None:
        t.team = team
    t.assigned_to = emp
    if emp is not None and t.stage in ('new', 'reopened'):
        t.stage = 'assigned'
    t.save()
    if emp is not None and (old is None or old.pk != emp.pk):
        event(t, 'assigned', f'Assigned to {emp_name(emp)}' + (f' ({t.team.name})' if t.team_id else ''), user)
        if old is not None:
            close_todos(t, f'Work on ticket {t.number}')
        if notify:
            notify_emp(emp, f'[{t.number}] Ticket assigned to you', ticket_text(t), t, ticket_page(t), f'Work on ticket {t.number}',
                       event='ticket_assigned')
    return t


def mark_first_response(t, at=None):
    if t.first_response_at:
        return
    t.first_response_at = at or now()
    if t.response_due and t.first_response_at > t.response_due:
        t.response_breached = True


ALLOWED = {
    'new': ('assigned', 'in_progress', 'waiting_customer', 'waiting_internal', 'resolved', 'closed'),
    'assigned': ('in_progress', 'waiting_customer', 'waiting_internal', 'resolved', 'closed'),
    'in_progress': ('waiting_customer', 'waiting_internal', 'testing', 'resolved', 'closed'),
    'waiting_customer': ('in_progress', 'waiting_internal', 'resolved', 'closed'),
    'waiting_internal': ('in_progress', 'waiting_customer', 'resolved', 'closed'),
    'pending_dev_approval': ('in_progress', 'waiting_customer', 'under_development', 'closed'),
    'under_development': ('testing', 'in_progress', 'waiting_customer', 'resolved'),
    'testing': ('in_progress', 'under_development', 'waiting_customer', 'resolved'),
    'resolved': ('closed', 'reopened'),
    'closed': ('reopened',),
    'reopened': ('assigned', 'in_progress', 'waiting_customer', 'waiting_internal', 'resolved', 'closed'),
}


def set_stage(t, stage, user=None, note='', by_customer=False, force=False):
    if stage not in STAGE_LABEL:
        raise ProjectsError('Unknown stage.', 'stage')
    if stage == t.stage:
        return t
    if not force and stage not in ALLOWED.get(t.stage, ()):
        raise ProjectsError(f'A ticket cannot go from "{STAGE_LABEL[t.stage]}" to "{STAGE_LABEL[stage]}".', 'stage')
    if stage == 'resolved' and not (note or t.resolution_note):
        raise ProjectsError('Write the resolution notes before resolving.', 'resolution_note')
    old = t.stage
    at = now()
    # SLA pause / resume
    if old in PAUSE_STAGES and stage not in PAUSE_STAGES and t.paused_at:
        paused = sla.minutes_between(t.paused_at, at, t.sla_business_hours)
        t.paused_minutes = (t.paused_minutes or 0) + paused
        if t.resolution_due:
            t.resolution_due = sla.add_minutes(t.resolution_due, paused, t.sla_business_hours)
        t.paused_at = None
    if stage in PAUSE_STAGES:
        t.paused_at = at
        t.waiting_since = at
        t.waiting_reminded_at = None
    else:
        t.waiting_since = None
    if stage in ('in_progress', 'waiting_customer', 'resolved') and not by_customer:
        mark_first_response(t, at)
    if stage == 'resolved':
        t.resolved_at = at
        if note:
            t.resolution_note = note
        if t.resolution_due and at > t.resolution_due:
            t.resolution_breached = True
    if stage == 'closed':
        t.closed_at = at
        if not t.resolved_at:
            t.resolved_at = at
    if stage == 'reopened':
        t.reopen_count = (t.reopen_count or 0) + 1
        t.resolved_at = None
        t.closed_at = None
    t.stage = stage
    t.save()
    who = 'customer' if by_customer else (user_name(user) if user else 'system')
    event(t, 'stage', f'{STAGE_LABEL[old]} → {STAGE_LABEL[stage]} by {who}' + (f': {note[:300]}' if note and stage != 'resolved' else ''), user)
    if stage in DONE_STAGES or stage == 'reopened':
        close_todos(t)
    if stage == 'reopened' and t.assigned_to_id:
        notify_emp(t.assigned_to, f'[{t.number}] Ticket reopened', ticket_text(t), t, ticket_page(t), f'Work on ticket {t.number}', event='ticket_reply')
    if not by_customer:
        texts = {
            'resolved': f'Your request "{t.subject}" has been resolved.\n\nResolution:\n{t.resolution_note}\n\n'
                        'If the problem is not solved, reply in the portal to reopen it.',
            'closed': f'Your request "{t.subject}" is now closed. Thank you.',
            'waiting_customer': f'We need some information from you to continue with "{t.subject}".' + (f'\n\n{note}' if note else ''),
            'in_progress': f'We are working on your request "{t.subject}".',
            'under_development': f'Your request "{t.subject}" is now with our development team.',
            'testing': f'The fix for "{t.subject}" is being tested.',
        }
        if stage in texts:
            notify_customer(t, STAGE_LABEL[stage], texts[stage])
    return t


def add_file(t, f, user=None, internal=False):
    if f is None:
        return None
    if getattr(f, 'size', 0) > 20 * 1024 * 1024:
        raise ProjectsError(f'{f.name} is larger than 20 MB.', 'files')
    return TicketFile.objects.create(ticket=t, file=f, name=(f.name or 'file')[:255], size=getattr(f, 'size', 0) or 0,
                                     is_internal=internal, uploaded_by=user if (user and user.is_authenticated) else None)


def add_message(t, body, *, user=None, contact=None, internal=False, via='staff', files=(), author_name=''):
    body = (body or '').strip()
    if not body and not files:
        raise ProjectsError('Write a message or attach a file.', 'body')
    if contact is not None:
        internal = False
    m = TicketMessage.objects.create(ticket=t, body=body or '(file attached)', is_internal=internal, via=via,
                                     author_user=user if (user and user.is_authenticated) else None, author_contact=contact,
                                     author_name=(author_name or (contact.name if contact else user_name(user)))[:150])
    for f in files or ():
        tf = add_file(t, f, user, internal)
        if tf:
            m.files.add(tf)
    if contact is not None or (via == 'email' and user is None):
        # the customer answered
        if t.stage == 'waiting_customer':
            set_stage(t, 'in_progress', None, by_customer=True)
        elif t.stage in DONE_STAGES:
            set_stage(t, 'reopened', None, 'Customer replied after resolution', by_customer=True)
        if t.assigned_to_id:
            notify_emp(t.assigned_to, f'[{t.number}] Customer replied', f'{m.author_name} wrote:\n\n{body[:2000]}', t, ticket_page(t),
                       f'Reply on ticket {t.number}', event='ticket_reply')
        event(t, 'message', f'{m.author_name} replied', None)
    elif not internal:
        mark_first_response(t)
        t.save()
        event(t, 'message', f'{m.author_name} replied to the customer', user)
        notify_customer(t, 'New reply on your request', f'{m.author_name} wrote:\n\n{body[:4000]}\n\nReply in the customer portal.')
    else:
        event(t, 'note', f'{m.author_name} added an internal note', user, public=False)
    return m


def update_ticket(t, data, user):
    """Change priority / category / product / team / billing; SLA and coverage follow."""
    changed = []
    for f in ('subject', 'description', 'resolution_note'):
        if f in data and data[f] is not None and getattr(t, f) != data[f]:
            setattr(t, f, data[f])
            changed.append(f)
    for f, model in (('product', ServiceProduct), ('category', IssueCategory), ('contract', AMCContract), ('contact', Contact)):
        if f in data:
            v = data[f]
            obj = model.objects.filter(pk=v).first() if v not in (None, '', 0) else None
            if v not in (None, '', 0) and obj is None:
                raise ProjectsError(f'{f.capitalize()} not found.', f)
            if f == 'contract' and obj is not None and obj.customer_id != t.customer_id:
                raise ProjectsError('The contract belongs to another customer.', 'contract')
            if f == 'contact' and obj is not None and obj.customer_id != t.customer_id:
                raise ProjectsError('The contact belongs to another customer.', 'contact')
            if getattr(t, f'{f}_id') != (obj.pk if obj else None):
                setattr(t, f, obj)
                changed.append(f)
    if 'priority' in data and data['priority'] and data['priority'] != t.priority:
        if data['priority'] not in PRIORITY_ORDER:
            raise ProjectsError('Unknown priority.', 'priority')
        event(t, 'priority', f'Priority {t.get_priority_display()} → {dict(Ticket._meta.get_field("priority").choices)[data["priority"]]}', user)
        t.priority = data['priority']
        changed.append('priority')
    if any(f in changed for f in ('product', 'category', 'contract')):
        apply_coverage(t)
    if 'billable_status' in data and data['billable_status'] and data['billable_status'] != t.billable_status:
        t.billable_status = data['billable_status']
        event(t, 'billing', f'Billing set to {t.get_billable_status_display()}', user, public=False)
    if any(f in changed for f in ('priority', 'contract')):
        apply_sla(t)
    t.save()
    refresh_approval(t, user)
    return t


def log_work(t, emp, *, hours, day=None, description='', onsite=False, billable=None, counts_against_amc=None,
             travel_cost=0, other_charges=0, user=None, auto_approve=False, manager=False):
    try:
        hours = Decimal(str(hours))
    except Exception:
        raise ProjectsError('Hours must be a number.', 'hours')
    if hours <= 0 or hours > 24:
        raise ProjectsError('Hours must be more than 0 and at most 24.', 'hours')
    day = day or local_today()
    if day > local_today():
        raise ProjectsError('Work cannot be logged for a future date.', 'date')
    if emp is None:
        raise ProjectsError('Your user is not linked to an employee.', 'employee')
    work_allowed(t, manager)
    lim = hours_limit(t)
    if lim is not None and not manager:
        used = logged_hours(t)
        if used + hours > lim:
            raise ProjectsError(f'Time on this ticket beyond {lim} h needs approval ({used} h logged). '
                                'Use "Ask for more time".', 'approval')
    if billable is None:
        billable = t.billable_status == 'chargeable'
    if counts_against_amc is None:
        counts_against_amc = t.billable_status == 'amc' and t.contract_id is not None
    rate = cost_rate(emp, day)
    w = WorkLog.objects.create(ticket=t, employee=emp, date=day, hours=hours, description=description or '', onsite=bool(onsite),
                               billable=bool(billable), counts_against_amc=bool(counts_against_amc),
                               travel_cost=q2(travel_cost), other_charges=q2(other_charges),
                               status='approved' if auto_approve else 'submitted', cost_rate=rate, cost_amount=q2(rate * hours),
                               approved_by=user if auto_approve else None, approved_at=now() if auto_approve else None)
    event(t, 'worklog', f'{emp_name(emp)} logged {hours} h' + (' onsite' if onsite else ''), user, public=False)
    if t.contract_id and not t.contract.unlimited_hours and counts_against_amc:
        u = contract_usage(t.contract)
        if u['remaining_hours'] is not None and u['remaining_hours'] <= 0:
            event(t, 'coverage', f'AMC hours of {t.contract.number} are used up – further work is chargeable', None, public=False)
            mgr = t.contract.account_manager
            if mgr:
                notify_emp(mgr, f'{t.contract.number}: AMC hours used up',
                           f'{t.contract.customer.name} has used all {t.contract.included_hours} hours of {t.contract.number}. '
                           'Further work is chargeable – agree it with the customer.', t.contract,
                           '/main-sidebar/zeo-projects/contracts/' + str(t.contract_id), f'AMC hours used up {t.contract.number}')
    return w


def cost_rate(emp, day=None):
    try:
        from ProjectControl.services import salary_hour_rate
        return salary_hour_rate(emp, day)
    except Exception:
        return D0


def decide_worklogs(ids, user, approve, reason=''):
    qs = WorkLog.objects.filter(pk__in=ids, status='submitted')
    n = 0
    for w in qs:
        w.status = 'approved' if approve else 'rejected'
        w.approved_by, w.approved_at = user, now()
        w.rejection_reason = '' if approve else (reason or '')[:255]
        w.save()
        n += 1
        if not approve:
            notify_emp(w.employee, f'Work log rejected ({w.ticket.number})', f'{w.hours} h on {w.date}: {reason}', event='work_approval')
    return n


# ------------------------------------------------------------------ scheduled checks
def sla_check(at=None):
    """Mark breaches, escalate (assignee → team lead → support managers), remind customers who keep us waiting,
    close resolved tickets after the set number of days. Run every 15 minutes."""
    at = at or now()
    s = settings()
    out = {'response_breached': 0, 'resolution_breached': 0, 'escalated': 0, 'reminded': 0, 'closed': 0, 'unassigned_critical': 0}
    managers = None
    for t in Ticket.objects.filter(stage__in=OPEN_STAGES).select_related('team', 'assigned_to', 'customer'):
        changed = False
        if not t.first_response_at and t.response_due and at > t.response_due and not t.response_breached:
            t.response_breached = True
            changed = True
            out['response_breached'] += 1
            event(t, 'sla', 'First-response target missed', None, public=False)
        breached_now = (t.resolution_due and not t.paused_at and at > t.resolution_due)
        if breached_now and not t.resolution_breached:
            t.resolution_breached = True
            changed = True
            out['resolution_breached'] += 1
            event(t, 'sla', 'Resolution target missed', None, public=False)
        if (t.response_breached and not t.first_response_at) or breached_now:
            due_again = not t.last_escalated_at or (at - t.last_escalated_at) >= timedelta(minutes=max(15, s.escalation_minutes or 60))
            if due_again and t.escalation_level < 3:
                t.escalation_level += 1
                t.last_escalated_at = at
                changed = True
                out['escalated'] += 1
                text = f'SLA breached – escalation level {t.escalation_level}\n\n' + ticket_text(t)
                if t.escalation_level == 1 and t.assigned_to_id:
                    # v1.15.0: the person working on it hears first (e-mail + My To-do / My work)
                    notify_emp(t.assigned_to, f'[{t.number}] Not solved in time', text, t, ticket_page(t),
                               f'Overdue ticket {t.number}', event='sla_breach')
                if t.escalation_level == 1 and t.team_id and t.team.lead_id and t.team.lead_id != t.assigned_to_id:
                    notify_emp(t.team.lead, f'[{t.number}] SLA breached', text, t, ticket_page(t), f'Escalated ticket {t.number}', event='sla_breach')
                else:
                    managers = managers if managers is not None else manager_users()
                    notify_users(managers, f'[{t.number}] SLA breached – escalation {t.escalation_level}', text, t, ticket_page(t),
                                 f'Escalated ticket {t.number}', event='sla_breach')
                event(t, 'escalation', f'Escalated (level {t.escalation_level})', None, public=False)
        if t.priority == 'critical' and t.assigned_to_id is None and (at - t.created_at) >= timedelta(minutes=15) \
                and not t.last_escalated_at:
            managers = managers if managers is not None else manager_users()
            notify_users(managers, f'[{t.number}] Critical ticket not assigned', ticket_text(t), t, ticket_page(t), f'Assign ticket {t.number}', event='ticket_unassigned')
            t.last_escalated_at = at
            changed = True
            out['unassigned_critical'] += 1
        if t.stage == 'waiting_customer' and t.waiting_since and s.waiting_customer_reminder_days:
            last = t.waiting_reminded_at or t.waiting_since
            if (at - last) >= timedelta(days=s.waiting_customer_reminder_days):
                notify_customer(t, 'Waiting for your reply', f'We are still waiting for your reply on "{t.subject}". '
                                'Please answer in the customer portal so we can continue.', event='ticket_waiting')
                t.waiting_reminded_at = at
                changed = True
                out['reminded'] += 1
        if changed:
            t.save()
    if s.auto_close_resolved_days:
        limit = at - timedelta(days=s.auto_close_resolved_days)
        for t in Ticket.objects.filter(stage='resolved', resolved_at__lt=limit):
            set_stage(t, 'closed', None, 'Closed automatically after resolution', force=True)
            out['closed'] += 1
    return out


# ------------------------------------------------------------------ AMC contracts
def refresh_contract_status(c, today=None, save=True):
    today = today or local_today()
    if c.status in ('draft', 'cancelled'):
        return c.status
    window = settings().expiring_window_days or 30
    if c.renewals.filter(status__in=('active', 'expiring')).exists() and c.end_date < today:
        st = 'renewed'
    elif c.end_date < today:
        st = 'expired'
    elif c.start_date <= today and c.end_date <= today + timedelta(days=window):
        st = 'expiring'
    elif c.start_date <= today:
        st = 'active'
    else:
        st = 'active' if c.status == 'active' else c.status
    if st != c.status and save:
        c.status = st
        c.save(update_fields=['status'])
    return st


def activate_contract(c, user=None):
    if c.status != 'draft':
        raise ProjectsError('Only a draft contract can be activated.')
    if c.end_date < c.start_date:
        raise ProjectsError('The end date is before the start date.', 'end_date')
    c.status = 'active'
    c.save(update_fields=['status'])
    refresh_contract_status(c)
    if not c.billing.exists():
        build_billing(c)
    return c


FREQ_MONTHS = {'annual': 12, 'half_yearly': 6, 'quarterly': 3, 'monthly': 1}


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    import calendar
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def build_billing(c):
    """Instalments over the contract term by billing frequency (the last one takes the rounding difference)."""
    c.billing.filter(status='planned').delete()
    step = FREQ_MONTHS.get(c.billing_frequency, 12)
    dates = []
    d = c.start_date
    while d <= c.end_date and len(dates) < 120:
        dates.append(d)
        d = add_months(d, step)
    if not dates:
        return []
    fixed = c.billing.exclude(status='planned').count()
    dates = dates[fixed:]
    left = q2(c.value) - q2(c.billing.exclude(status='planned').aggregate(s=Sum('amount'))['s'])
    if not dates or left <= 0:
        return []
    part = q2(left / len(dates))
    rows = []
    for i, d in enumerate(dates):
        amt = part if i < len(dates) - 1 else q2(left - part * (len(dates) - 1))
        end = min(add_months(d, step) - timedelta(days=1), c.end_date)
        rows.append(AMCBilling(contract=c, due_date=d, amount=amt, period_label=f'{d:%d %b %Y} – {end:%d %b %Y}'))
    return AMCBilling.objects.bulk_create(rows)


def renew_contract(c, user=None, *, start_date=None, end_date=None, value=None, included_hours=None, keep_number=False):
    if c.renewals.exclude(status='cancelled').exists():
        raise ProjectsError('This contract has already been renewed.')
    start = start_date or (c.end_date + timedelta(days=1))
    if end_date is None:
        months = max(1, (c.end_date.year - c.start_date.year) * 12 + c.end_date.month - c.start_date.month + (1 if c.end_date.day >= c.start_date.day - 1 else 0))
        end_date = add_months(start, months) - timedelta(days=1)
    if end_date < start:
        raise ProjectsError('The end date is before the start date.', 'end_date')
    with transaction.atomic():
        n = AMCContract.objects.create(
            number=next_number('amc'), customer=c.customer, title=c.title, contract_type=c.contract_type, start_date=start,
            end_date=end_date, value=q2(value if value is not None else (c.renewal_value or c.value)), currency=c.currency,
            billing_frequency=c.billing_frequency, unlimited_hours=c.unlimited_hours,
            included_hours=included_hours if included_hours is not None else c.included_hours, onsite_hours=c.onsite_hours,
            hourly_rate_outside=c.hourly_rate_outside, sla_policy=c.sla_policy, support_timing=c.support_timing,
            exclusions=c.exclusions, account_manager=c.account_manager, status='draft', previous=c, version=(c.version or 1) + 1,
            created_by=user)
        n.products.set(c.products.all())
        c.renewal_status = 'won'
        c.save(update_fields=['renewal_status'])
    close_todos(c)
    return n


def renewal_reminders(today=None):
    """90 / 60 / 30 / 0 days before expiry and every N days after: e-mail + To-do for the renewal owner."""
    today = today or local_today()
    s = settings()
    days = sorted({int(x) for x in (s.renewal_reminder_days or [90, 60, 30, 0]) if str(x).lstrip('-').isdigit()}, reverse=True)
    sent = 0
    for c in AMCContract.objects.exclude(status__in=('draft', 'cancelled')).select_related('customer', 'account_manager', 'renewal_owner'):
        refresh_contract_status(c, today)
        if c.renewal_status in ('won', 'lost') or c.renewals.exclude(status='cancelled').exists():
            continue
        left = (c.end_date - today).days
        done = set(c.reminders_sent or [])
        key, mark = None, []
        applicable = [d for d in days if left <= d]          # thresholds already reached
        if applicable and left >= 0:
            nearest = min(applicable)                        # the most relevant one (no burst of old reminders)
            if str(nearest) not in done:
                key, mark = str(nearest), [str(d) for d in applicable]
        if key is None and left < 0 and s.after_expiry_reminder_days:
            since = -left
            if since % s.after_expiry_reminder_days == 0:
                k = f'after:{today.isoformat()}'
                if k not in (c.reminders_sent or []):
                    key = k
        if key is None:
            continue
        owner = c.renewal_owner or c.account_manager
        text = (f'{c.number} of {c.customer.name} ' + (f'expires in {left} day(s) on {c.end_date:%d %b %Y}.' if left > 0 else
                (f'expires today.' if left == 0 else f'expired {-left} day(s) ago on {c.end_date:%d %b %Y}.')) +
                f'\nValue: {c.currency} {q2(c.value)}. Follow up the renewal quotation.')
        if owner is not None:
            notify_emp(owner, f'AMC renewal: {c.number} – {c.customer.name}', text, c, f'/main-sidebar/zeo-projects/contracts/{c.pk}',
                       f'Renew {c.number}', event='renewal')
        else:
            notify_users(manager_users(), f'AMC renewal: {c.number} – {c.customer.name}', text, c,
                         f'/main-sidebar/zeo-projects/contracts/{c.pk}', f'Renew {c.number}', event='renewal')
        if c.renewal_status == 'none':
            c.renewal_status = 'in_progress'
        c.renewal_owner = owner
        c.reminders_sent = sorted(done | set(mark) | {key})
        c.save(update_fields=['reminders_sent', 'renewal_status', 'renewal_owner'])
        sent += 1
    return sent


# ------------------------------------------------------------------ development requests
def raise_dev_request(t, user, *, title, issue, proposed_solution='', estimated_hours=0, estimated_cost=0, chargeable=None,
                      needs_customer_approval=None):
    if not (title or '').strip():
        raise ProjectsError('Title is required.', 'title')
    if not (issue or '').strip():
        raise ProjectsError('Describe the issue.', 'issue')
    if t is not None and t.dev_requests.exclude(status__in=('rejected', 'cancelled', 'done')).exists():
        raise ProjectsError('This ticket already has an open development request.')
    if chargeable is None:
        chargeable = not (t and t.billable_status in ('amc', 'warranty', 'internal'))
    if needs_customer_approval is None:
        needs_customer_approval = bool(chargeable and t and t.customer_id)
    d = DevRequest.objects.create(number=next_number('dev'), ticket=t, customer=t.customer if t else None, title=title.strip()[:255],
                                  issue=issue, proposed_solution=proposed_solution or '', estimated_hours=q2(estimated_hours),
                                  estimated_cost=q2(estimated_cost), chargeable=bool(chargeable),
                                  needs_customer_approval=bool(needs_customer_approval), requested_by=user)
    if t is not None:
        if t.stage not in DONE_STAGES:
            set_stage(t, 'pending_dev_approval', user, f'Development request {d.number} raised', force=True)
        event(t, 'dev', f'Development request {d.number} raised: {d.title}', user)
    reviewers = users_with('approve_tech_devrequest')
    notify_users(reviewers, f'{d.number}: technical review needed', dev_text(d), d, f'/main-sidebar/zeo-projects/dev-requests/{d.pk}',
                 f'Review development request {d.number}', event='project_updates')
    return d


def users_with(codename, admins_if_none=True):
    """Active users of this company holding the right (company administrators when nobody holds it)."""
    from django.contrib.auth.models import Group
    from tenant_users.tenants.models import UserTenantPermissions
    groups = Group.objects.filter(permissions__codename=codename)
    out = [u.profile for u in _company_perms().filter(groups__in=groups).select_related('profile').distinct()
           if u.profile and u.profile.is_active]
    if not out and admins_if_none:
        out = [u.profile for u in _company_perms().filter(is_superuser=True).select_related('profile')
               if u.profile and u.profile.is_active and not hasattr_contact(u.profile)]
    return out


def dev_text(d):
    return (f'{d.number}: {d.title}\nCustomer: {d.customer.name if d.customer_id else "-"}\nTicket: {d.ticket.number if d.ticket_id else "-"}'
            f'\nEstimate: {d.estimated_hours} h / {q2(d.estimated_cost)}\nChargeable: {"yes" if d.chargeable else "no"}'
            f'\n\nIssue:\n{d.issue}\n\nProposed solution:\n{d.proposed_solution}')


def decide_dev(d, user, step, approve, note='', quoted_amount=None, customer_name=''):
    """step = tech / commercial / customer."""
    at = now()
    page = f'/main-sidebar/zeo-projects/dev-requests/{d.pk}'
    if step == 'tech':
        if d.status != 'submitted':
            raise ProjectsError('This request is not waiting for technical review.')
        d.tech_by, d.tech_at, d.tech_note = user, at, note or ''
        if not approve:
            return reject_dev(d, user, note or 'Rejected in technical review')
        d.status = 'tech_approved' if d.chargeable else ('commercial_approved' if d.needs_customer_approval else 'approved')
    elif step == 'commercial':
        if d.status != 'tech_approved':
            raise ProjectsError('This request is not waiting for commercial approval.')
        d.commercial_by, d.commercial_at, d.commercial_note = user, at, note or ''
        if quoted_amount not in (None, ''):
            d.quoted_amount = q2(quoted_amount)
        if not approve:
            return reject_dev(d, user, note or 'Rejected in commercial approval')
        d.status = 'commercial_approved' if d.needs_customer_approval else 'approved'
    elif step == 'customer':
        if d.status != 'commercial_approved':
            raise ProjectsError('This request is not waiting for the customer.')
        d.customer_decision_by = (customer_name or user_name(user))[:150]
        d.customer_decision_at, d.customer_note = at, note or ''
        if not approve:
            return reject_dev(d, user, note or 'Rejected by the customer', by_customer=True)
        d.status = 'approved'
    else:
        raise ProjectsError('Unknown step.')
    d.save()
    close_todos(d)
    if d.ticket_id:
        event(d.ticket, 'dev', f'{d.number}: {d.get_status_display()}', user)
    if d.status == 'tech_approved':
        notify_users(users_with('approve_commercial_devrequest'), f'{d.number}: commercial approval needed', dev_text(d), d, page,
                     f'Commercial approval {d.number}', event='project_updates')
    elif d.status == 'commercial_approved' and d.ticket_id:
        amount = d.quoted_amount if d.quoted_amount is not None else d.estimated_cost
        notify_customer(d.ticket, f'Approval needed: {d.title}',
                        f'We propose the following development for "{d.ticket.subject}":\n\n{d.proposed_solution}\n\n'
                        f'Estimate: {d.estimated_hours} hours' + (f', {settings().currency} {q2(amount)}' if d.chargeable else '') +
                        '\n\nPlease approve or reject it in the customer portal.', event='customer_approval')
    if d.status == 'approved':
        create_dev_task(d, user)
    return d


def reject_dev(d, user, reason, by_customer=False):
    d.status = 'rejected'
    d.rejected_reason = reason
    d.save()
    close_todos(d)
    if d.ticket_id:
        t = d.ticket
        event(t, 'dev', f'{d.number} rejected: {reason[:200]}', user)
        if t.stage == 'pending_dev_approval':
            set_stage(t, 'in_progress', user, f'{d.number} rejected', force=True, by_customer=by_customer)
    return d


def support_project(customer, user=None):
    """The ZEO project that holds development tasks raised from support (one per customer, or one internal)."""
    from ProjectManagement.models import Project
    title = f'Support development – {customer.name} ({customer.code})' if customer else 'Support development – internal'
    p = Project.objects.filter(title=title[:200]).first()
    if p is None:
        p = Project.objects.create(title=title[:200], start_date=local_today(), status='in_progress',
                                   description='Development work approved from support tickets.')
        ensure_profile(p, customer=customer, project_type='customization', billing_method='time_and_material')
    return p


def first_stage(project):
    from ProjectManagement.models import ProjectStage
    st = ProjectStage.objects.filter(project=project).order_by('sequence', 'id').first()
    return st or ProjectStage.objects.create(project=project, title='To do', sequence=1)


def create_dev_task(d, user=None):
    from ProjectManagement.models import Project, Task
    project = Project.objects.filter(pk=d.project_id).first() if d.project_id else None
    if project is None and d.ticket_id and d.ticket.project_id:
        project = Project.objects.filter(pk=d.ticket.project_id).first()
    project = project or support_project(d.customer, user)
    task = Task.objects.create(title=f'{d.number} {d.title}'[:200], project=project, stage=first_stage(project), status='to_do',
                               start_date=local_today(), description=f'{d.issue}\n\nSolution:\n{d.proposed_solution}'.strip())
    if d.assigned_to_id:
        task.task_members.add(d.assigned_to)
        project.members.add(d.assigned_to)
    try:
        from ProjectControl.models import TaskPlan
        TaskPlan.objects.update_or_create(task_id=task.pk, defaults={'planned_hours': d.estimated_hours, 'billable': d.chargeable})
    except Exception:
        log.exception('task plan for %s failed', d.number)
    from .models import TaskDetail
    TaskDetail.objects.update_or_create(task_id=task.pk, defaults={'estimated_hours': d.estimated_hours, 'phase': 'Development',
                                                                    'acceptance_criteria': f'Fixes ticket {d.ticket.number}' if d.ticket_id else ''})
    d.project_id, d.task_id = project.pk, task.pk
    d.save(update_fields=['project_id', 'task_id'])
    if d.ticket_id:
        t = d.ticket
        t.project_id, t.task_id = project.pk, task.pk
        t.save(update_fields=['project_id', 'task_id'])
        if t.stage not in DONE_STAGES:
            set_stage(t, 'under_development', user, f'Task created in {project.title}', force=True)
    if d.assigned_to_id:
        notify_emp(d.assigned_to, f'{d.number}: development task', dev_text(d), d, f'/main-sidebar/zeo-projects/dev-requests/{d.pk}',
                   f'Develop {d.number}', event='project_updates')
    return task


DEV_FLOW = {'approved': ('in_progress',), 'in_progress': ('testing',), 'testing': ('in_progress', 'deployed'),
            'deployed': ('done', 'in_progress')}


def dev_progress(d, user, status, note=''):
    if status not in DEV_FLOW.get(d.status, ()):
        raise ProjectsError(f'A request that is "{d.get_status_display()}" cannot move to "{dict(DevRequest._meta.get_field("status").choices).get(status, status)}".')
    d.status = status
    if status == 'deployed':
        d.deployed_at = now()
    if status == 'done':
        d.confirmed_at = now()
    d.save()
    try:
        from ProjectManagement.models import Task
        task = Task.objects.filter(pk=d.task_id).first()
        if task:
            task.status = {'in_progress': 'in_progress', 'testing': 'in_progress', 'deployed': 'completed', 'done': 'completed'}[status]
            task.save(update_fields=['status'])
    except Exception:
        log.exception('task status for %s', d.number)
    if d.ticket_id:
        t = d.ticket
        target = {'in_progress': 'under_development', 'testing': 'testing', 'deployed': 'testing', 'done': None}[status]
        if status == 'done' and t.stage not in DONE_STAGES:
            t.resolution_note = t.resolution_note or f'Fixed by development {d.number}. {note}'.strip()
            set_stage(t, 'resolved', user, t.resolution_note, force=True)
        elif target and t.stage != target and t.stage not in DONE_STAGES:
            set_stage(t, target, user, note or f'{d.number}: {d.get_status_display()}', force=True)
        if status == 'deployed':
            notify_customer(t, 'Fix deployed – please confirm', f'The change for "{t.subject}" has been deployed. '
                            'Please check it and confirm in the customer portal.')
    return d


# ------------------------------------------------------------------ projects
FIN_TYPE = {'fixed': 'fixed', 'milestone': 'fixed', 'advance': 'fixed', 'time_and_material': 'time_and_material',
            'non_billable': 'non_billable'}


def ensure_profile(project, customer=None, project_type=None, billing_method=None):
    p = ProjectProfile.objects.filter(project_id=project.pk).first()
    if p is None:
        p = ProjectProfile.objects.create(project_id=project.pk, number=next_number('project'), customer=customer,
                                          project_type=project_type or 'implementation', billing_method=billing_method or 'milestone')
        sync_finance(p)
    return p


def sync_finance(p):
    try:
        from ProjectControl.models import ProjectFinance
        f, _ = ProjectFinance.objects.get_or_create(project_id=p.project_id)
        f.billing_type = FIN_TYPE.get(p.billing_method, 'time_and_material')
        if p.customer_id:
            f.customer_name = p.customer.name[:200]
        f.save()
    except Exception:
        log.exception('project finance sync failed')


def finance(project_id):
    try:
        from ProjectControl.models import ProjectFinance
        return ProjectFinance.objects.filter(project_id=project_id).first()
    except Exception:
        return None


def project_health(project, milestones=None, summary=None):
    """on_track / at_risk / delayed / done from dates, overdue milestones and hours used."""
    if project.status in ('completed',):
        return 'done'
    if project.status in ('cancelled',):
        return 'cancelled'
    today = local_today()
    milestones = milestones if milestones is not None else list(Milestone.objects.filter(project_id=project.pk))
    overdue = [m for m in milestones if m.planned_date and m.planned_date < today and m.status not in ('accepted',)]
    if project.end_date and project.end_date < today:
        return 'delayed'
    if overdue:
        return 'delayed' if len(overdue) > 1 else 'at_risk'
    if summary:
        hp = summary.get('hours_used_pct')
        if hp is not None and hp > 100:
            return 'at_risk'
        bp = summary.get('budget_used_pct')
        if bp is not None and bp > 100:
            return 'at_risk'
    if project.status == 'on_hold':
        return 'at_risk'
    return 'on_track'


def project_profit(project):
    """Contract value, invoiced, employee cost (approved timesheets at cost rate), expenses, budget, estimate to complete,
    expected margin and change-request revenue."""
    try:
        from ProjectControl.services import project_summary
        s = project_summary(project)
    except Exception:
        log.exception('project summary failed')
        s = {'actual_hours': D0, 'cost': D0, 'budget_hours': D0, 'planned_hours': D0, 'contract_value': D0, 'budget_amount': D0,
             'billed': D0, 'hours_used_pct': None, 'budget_used_pct': None, 'currency': 'AED'}
    ms = list(Milestone.objects.filter(project_id=project.pk))
    expenses = q2(ProjectExpense.objects.filter(project_id=project.pk).aggregate(s=Sum('amount'))['s'])
    cr = ChangeRequest.objects.filter(project_id=project.pk, status__in=('customer_approved', 'done'))
    cr_revenue = q2(cr.aggregate(s=Sum('extra_amount'))['s'])
    cr_hours = q2(cr.aggregate(s=Sum('extra_hours'))['s'])
    prof = ProjectProfile.objects.filter(project_id=project.pk).first()
    contract = q2(s.get('contract_value') or 0)
    invoiced = q2(sum((m.amount for m in ms if m.invoice_status in ('invoiced', 'paid')), D0)) + q2(prof.invoiced_amount if prof else 0)
    paid = q2(sum((m.amount for m in ms if m.invoice_status == 'paid'), D0))
    cost = q2(s.get('cost') or 0)
    hours = q2(s.get('actual_hours') or 0)
    budget_hours = q2(s.get('budget_hours') or 0) or q2(s.get('planned_hours') or 0)
    budget_hours_total = budget_hours   # approved change requests are already added to the finance budget hours
    rate = (cost / hours) if hours else D0
    remaining_hours = max(D0, budget_hours_total - hours)
    etc = q2(remaining_hours * rate)
    revenue = contract + cr_revenue
    if prof and prof.billing_method == 'time_and_material':
        revenue = q2(s.get('billed') or 0) + cr_revenue
    total_cost = cost + expenses + etc
    margin = q2(revenue - total_cost)
    return {
        'currency': s.get('currency') or 'AED', 'contract_value': contract, 'change_request_revenue': cr_revenue,
        'revenue': q2(revenue), 'invoiced': invoiced, 'paid': paid, 'to_invoice': q2(max(D0, revenue - invoiced)),
        'employee_cost': cost, 'expenses': expenses, 'estimate_to_complete': etc, 'expected_total_cost': q2(total_cost),
        'expected_margin': margin, 'margin_pct': float(q2(margin * 100 / revenue)) if revenue else None,
        'budget_hours': budget_hours_total, 'actual_hours': hours, 'remaining_hours': q2(remaining_hours),
        'hours_used_pct': float(q2(hours * 100 / budget_hours_total)) if budget_hours_total else None,
        'budget_amount': q2(s.get('budget_amount') or 0), 'budget_used_pct': s.get('budget_used_pct'),
        'billable_hours': s.get('billable_hours'), 'by_employee': s.get('by_employee', []), 'by_task': s.get('by_task', []),
        '_summary': s,
    }


def milestone_status(m, user, status, note='', customer_name=''):
    allowed = {'planned': ('in_progress', 'ready'), 'in_progress': ('ready', 'planned'), 'ready': ('accepted', 'rejected', 'in_progress'),
               'rejected': ('in_progress', 'ready'), 'accepted': ()}
    if status not in allowed.get(m.status, ()):
        raise ProjectsError(f'A milestone that is "{m.get_status_display()}" cannot become "{dict(Milestone._meta.get_field("status").choices).get(status, status)}".')
    m.status = status
    if status == 'ready':
        m.completed_at = now()
    if status in ('accepted', 'rejected'):
        m.accepted_by = (customer_name or user_name(user))[:150]
        m.accepted_at = now()
        m.acceptance_note = note or ''
        if status == 'accepted' and m.amount and m.invoice_status == 'planned':
            m.invoice_status = 'to_invoice'
    m.save()
    from ProjectManagement.models import Project
    project = Project.objects.filter(pk=m.project_id).first()
    if status == 'ready' and project:
        prof = ProjectProfile.objects.filter(project_id=project.pk).first()
        from .notify import wants
        if prof and prof.customer_id and wants('customer_approval', 'email', customer=prof.customer_id):
            for c in prof.customer.contacts.filter(is_active=True, portal_can_approve=True):
                send_mail([c.email], f'{project.title}: "{m.name}" is ready for your acceptance',
                          f'Dear {c.name},\n\nThe milestone "{m.name}" of {project.title} is ready. Please review it and accept '
                          'or reject it in the customer portal.')
    if status in ('accepted', 'rejected') and project:
        for e in project.managers.all():
            notify_emp(e, f'{project.title}: milestone {m.get_status_display().lower()}', f'"{m.name}" was {status} by {m.accepted_by}. {note}', event='project_updates')
        if status == 'accepted' and m.amount:
            notify_users(users_with('change_amcbilling'), f'{project.title}: invoice milestone "{m.name}"',
                         f'Milestone accepted – invoice {settings().currency} {q2(m.amount)}.', m,
                         f'/main-sidebar/zeo-projects/billing', f'Invoice milestone {m.name}', event='project_updates')
    return m


def decide_change_request(cr, user, step, approve, note='', customer_name=''):
    if step == 'internal':
        if cr.status != 'submitted':
            raise ProjectsError('This change request is not waiting for approval.')
        cr.decided_by, cr.decided_at, cr.decision_note = user, now(), note or ''
        cr.status = 'approved' if approve else 'rejected'
    elif step == 'customer':
        if cr.status != 'approved':
            raise ProjectsError('This change request is not waiting for the customer.')
        cr.customer_decision_by = (customer_name or user_name(user))[:150]
        cr.customer_decision_at = now()
        cr.status = 'customer_approved' if approve else 'rejected'
        if note:
            cr.decision_note = (cr.decision_note + '\nCustomer: ' + note).strip()
    elif step == 'done':
        if cr.status != 'customer_approved':
            raise ProjectsError('Only a change approved by the customer can be marked done.')
        cr.status = 'done'
    else:
        raise ProjectsError('Unknown step.')
    cr.save()
    if cr.status == 'customer_approved':
        try:
            from ProjectControl.models import ProjectFinance
            f = ProjectFinance.objects.filter(project_id=cr.project_id).first()
            if f and cr.extra_hours:
                f.budget_hours = q2(f.budget_hours) + q2(cr.extra_hours)
                f.save(update_fields=['budget_hours'])
        except Exception:
            log.exception('change request budget update failed')
    return cr



def _company_perms():   # v1.22.0: only the users of the open company (see AccessControl.access.company_perms)
    from AccessControl.access import company_perms
    return company_perms()
