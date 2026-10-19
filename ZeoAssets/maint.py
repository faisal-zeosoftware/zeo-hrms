"""Asset Management release 2 (v1.34.0) rules: work orders, preventive plans, helpdesk SLA and escalation, spare parts stock."""
import calendar
import logging
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from zeo.appkit.engine import AppError, call, emp_name, linked, now

from . import services as SV
from .models import AssetCard, MaintenancePlan, Part, SlaRule, Stock, StockMove, Ticket, WorkOrder

log = logging.getLogger(__name__)


def qn(v):
    try:
        return f'{Decimal(str(v)).normalize():f}'
    except Exception:
        return str(v)

C2 = Decimal('0.01')
C4 = Decimal('0.0001')
ZERO = Decimal('0')
OPEN_WO = ('approval', 'open', 'assigned', 'in_progress', 'on_hold', 'handed_over')
OPEN_T = ('new', 'assigned', 'in_progress', 'waiting')


def money(v):
    return Decimal(str(v or 0)).quantize(C2, ROUND_HALF_UP)


def today():
    return timezone.localdate()


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, calendar.monthrange(d.year + y, m + 1)[1]))


def step(d0, every, unit):
    if unit == 'days':
        return d0 + timedelta(days=every)
    if unit == 'weeks':
        return d0 + timedelta(weeks=every)
    return add_months(d0, every)


# ------------------------------------------------------------------ stock
def stock_row(part, loc, lock=False):
    qs = Stock.objects.select_for_update() if lock else Stock.objects
    row = qs.filter(part=part, location=loc).first()
    return row or Stock.objects.create(part=part, location=loc, qty=0)


def refresh_on_hand(part):
    part.on_hand = Stock.objects.filter(part=part).aggregate(s=Sum('qty'))['s'] or 0
    Part.objects.filter(pk=part.pk).update(on_hand=part.on_hand)


@transaction.atomic
def post_move(m, user=None):
    """Post a stock movement (sets the stock and the average cost). m is an unsaved StockMove with kind, part, qty, stores."""
    part = Part.objects.select_for_update().get(pk=m.part_id)
    qty = Decimal(m.qty or 0)
    if m.kind != 'adjust' and qty <= 0:
        raise AppError('The quantity must be more than zero.', 'qty')
    if m.kind in ('receipt', 'return') and not m.to_location_id:
        raise AppError('Choose the store it goes into.', 'to_location')
    if m.kind == 'issue' and not m.from_location_id:
        raise AppError('Choose the store it comes from.', 'from_location')
    if m.kind == 'transfer':
        if not (m.from_location_id and m.to_location_id):
            raise AppError('Choose both stores.', 'to_location')
        if m.from_location_id == m.to_location_id:
            raise AppError('The two stores are the same.', 'to_location')
    if m.kind == 'adjust' and not m.to_location_id:
        raise AppError('Choose the store.', 'to_location')
    avg = Decimal(part.unit_cost or 0)
    if m.kind == 'receipt':
        cost = Decimal(m.unit_cost or 0) or avg
        on_hand = Decimal(part.on_hand or 0)
        new_avg = ((on_hand * avg + qty * cost) / (on_hand + qty)) if on_hand + qty > 0 else cost
        Part.objects.filter(pk=part.pk).update(unit_cost=new_avg.quantize(C4))
        m.unit_cost = cost
    else:
        m.unit_cost = avg
    if m.kind in ('issue', 'transfer'):
        src = stock_row(part, m.from_location, True)
        if Decimal(src.qty) < qty:
            raise AppError(f'Only {qn(src.qty)} {part.unit} of {part.code} in {m.from_location.name}.', 'qty')
        src.qty = Decimal(src.qty) - qty
        src.save(update_fields=['qty'])
    if m.kind in ('receipt', 'transfer', 'return', 'adjust'):
        dst = stock_row(part, m.to_location, True)
        if m.kind == 'adjust' and Decimal(dst.qty) + qty < 0:
            raise AppError('The stock cannot go below zero.', 'qty')
        dst.qty = Decimal(dst.qty) + qty
        dst.save(update_fields=['qty'])
    m.value = money(abs(qty) * Decimal(m.unit_cost))
    if not m.date:
        m.date = today()
    m.by = user if (user and getattr(user, 'is_authenticated', False)) else None
    if not m.number:
        from .app import app
        m.number = app.next_number('stock_move', 'SM')
    m.save()
    refresh_on_hand(part)
    if m.work_order_id:
        wo_costs(m.work_order)
    low_stock_check(part)
    return m


def low_stock_check(part):
    part.refresh_from_db()
    if part.min_qty and Decimal(part.on_hand) <= Decimal(part.min_qty):
        from .app import app, PAGE
        for u in app.users_with_role('ast_storekeeper'):
            app.todo(u, f'Low stock: {part.code} {part.name} ({qn(part.on_hand)} {part.unit} left)', 'part', part.pk, f'{part.code} {part.name}',
                     PAGE + f'r/part/{part.pk}')


# ------------------------------------------------------------------ work orders
def wo_costs(o):
    o = WorkOrder.objects.get(pk=o.pk)
    parts = sum((Decimal(x.value) if x.kind == 'issue' else -Decimal(x.value)) for x in StockMove.objects.filter(work_order=o, kind__in=('issue', 'return')))
    o.parts_cost = money(max(parts, ZERO))
    o.labour_cost = money(Decimal(o.labour_hours or 0) * Decimal(o.labour_rate or 0))
    o.total_cost = money(o.parts_cost + o.labour_cost + Decimal(o.other_cost or 0))
    WorkOrder.objects.filter(pk=o.pk).update(parts_cost=o.parts_cost, labour_cost=o.labour_cost, total_cost=o.total_cost)
    return o


def checklist_of(activity):
    return [{'item': str(x)[:200], 'done': False} for x in (activity.checklist or [])] if activity else []


def hand_over(o, user=None):
    """Send the work order to the Maintenance app (when linked and switched on in Settings)."""
    from .app import app, send_to_maintenance
    if not (app.settings().handover_work_orders and linked(app, 'ZeoMaintenance')):
        return False
    send_to_maintenance(o.card, force=True)
    o.card.refresh_from_db()
    res = call(app, 'ZeoMaintenance', 'request', title=f'{o.card.code} {o.title}'[:200], description=o.description, requester=emp_name(o.technician) or 'Asset Management',
               source='staff', ref=o.number, user=user, asset_id=o.card.mnt_asset_id, branch_id=o.branch_id)
    if not res:
        return False
    o.mnt_work_order_id, o.status = res['id'], 'handed_over'
    WorkOrder.objects.filter(pk=o.pk).update(mnt_work_order_id=o.mnt_work_order_id, status='handed_over')
    app.event('work_order', o.pk, f'Handed over to the Maintenance app as {res["number"]}', user)
    return True


def sync_handed_over():
    """Status of handed-over work orders comes back from the Maintenance app."""
    from .app import app
    n = 0
    for o in WorkOrder.objects.filter(status='handed_over', mnt_work_order_id__isnull=False):
        info = call(app, 'ZeoMaintenance', 'work_order_status', o.mnt_work_order_id)
        if not info:
            continue
        if info['status_code'] in ('done', 'closed'):
            o.status = 'closed' if info['status_code'] == 'closed' else 'done'
            o.done_at = o.done_at or now()
            o.closed_at = now() if o.status == 'closed' else None
            o.other_cost = money(info.get('cost') or 0)
            o.work_note = info.get('work_note') or o.work_note
            o.save()
            wo_costs(o)
            app.event('work_order', o.pk, f'Maintenance app: {info["status"]} – cost {info.get("cost") or 0}', None)
            after_done(o, None)
            n += 1
        elif info['status_code'] == 'cancelled':
            o.status = 'cancelled'
            o.save(update_fields=['status'])
            n += 1
    return n


def take_out_of_service(o, user):
    if not o.out_of_service or o.hr_maintenance_id:
        return
    with SV.hr_errors():
        r = SV.S().start_maintenance(o.card.hr_asset, user, kind='preventive' if o.kind == 'preventive' else ('inspection' if o.kind in ('inspection', 'calibration') else 'breakdown'),
                                     description=f'{o.number} {o.title}', vendor=o.vendor, reading=o.meter_reading)
    WorkOrder.objects.filter(pk=o.pk).update(hr_maintenance_id=r.pk)
    o.hr_maintenance_id = r.pk
    SV.refresh(o.card)


def back_in_service(o, user):
    if not o.hr_maintenance_id:
        return
    from django.apps import apps
    R = apps.get_model('AssetPlus', 'MaintenanceRecord')
    r = R.objects.filter(pk=o.hr_maintenance_id, status='open').first()
    if r is not None:
        with SV.hr_errors():
            SV.S().close_maintenance(r, user, cost=o.total_cost, downtime_hours=o.downtime_hours or None, result='fixed',
                                     result_notes=o.work_note[:500], reading=o.meter_reading)
    SV.refresh(o.card)


def after_done(o, user):
    """Plan moves on; meter reading kept; tickets of the work order resolved."""
    if o.plan_id:
        p = o.plan
        if p.unit == 'meter' and o.meter_reading is not None:
            p.next_meter = Decimal(o.meter_reading) + Decimal(p.every)
            p.save(update_fields=['next_meter'])
    if o.meter_reading is not None and o.card.hr_asset_id:
        SV.Profile().objects.filter(asset_id=o.card.hr_asset_id).update(meter_reading=o.meter_reading)
    if o.ticket_id and o.ticket.status in OPEN_T:
        t = o.ticket
        t.status, t.resolved_at = 'resolved', now()
        t.resolution = (t.resolution + '\n' if t.resolution else '') + f'Work order {o.number}: {o.work_note}'.strip()
        t.repair_cost = o.total_cost
        t.save()
        from .app import app
        app.event('ticket', t.pk, f'Resolved by work order {o.number} – please confirm', user)
        notify_reporter(t, f'Your ticket {t.number} is resolved: {o.work_note}. Please confirm it is fixed, or reopen it.')


def make_wo(*, card, title, kind='corrective', activity=None, plan=None, ticket=None, priority='normal', description='', team=None, technician=None,
            vendor='', scheduled_on=None, due_date=None, estimate=None, user=None):
    from .app import app
    o = WorkOrder(card=card, title=title[:200], kind=kind, activity=activity, plan=plan, ticket=ticket, priority=priority, description=description,
                  team=team, technician=technician, vendor=vendor, scheduled_on=scheduled_on, due_date=due_date, estimate=estimate,
                  branch_id=card.branch_id)
    wo_defaults(o, None)
    o.number = app.next_number('work_order', 'AWO')
    o.save()
    app.event('work_order', o.pk, 'Created' + (f' from plan {plan.number}' if plan else f' from ticket {ticket.number}' if ticket else ''), user)
    route(o, user)
    return o


def wo_defaults(o, request):
    from .app import app
    if o.activity_id:
        a = o.activity
        if not o.checklist:
            o.checklist = checklist_of(a)
        if o.kind == 'corrective' and a.kind != 'corrective':
            o.kind = a.kind
        o.out_of_service = o.out_of_service or a.out_of_service
    if o.kind == 'breakdown':
        o.out_of_service = True
    if not o.labour_rate:
        o.labour_rate = app.settings().labour_rate or 0
    if not o.scheduled_on:
        o.scheduled_on = today()


def route(o, user):
    """After creation: hand over, ask approval above the limit, or tell the technician / team."""
    from .app import app, PAGE
    if hand_over(o, user):
        return
    limit = Decimal(app.settings().wo_approval_limit or 0)
    if o.estimate and limit and Decimal(o.estimate) > limit:
        WorkOrder.objects.filter(pk=o.pk).update(status='approval')
        o.status = 'approval'
        app.tell(app.users_with_role('ast_manager'), f'Approve work order {o.number}', f'{o.title} – estimate {o.estimate}', 'work_order', o.pk,
                 f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}', summary=f'Approve work order {o.number}')
        return
    if o.technician_id:
        WorkOrder.objects.filter(pk=o.pk).update(status='assigned')
        o.status = 'assigned'
        tell_tech(o)
    elif o.team_id:
        for e in [o.team.leader] + list(o.team.members.all()):
            if e is not None and e.users_id:
                app.todo(e.users, f'Work order {o.number} for your team', 'work_order', o.pk, f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}')


def tell_tech(o):
    from .app import app, PAGE
    if o.technician_id:
        app.tell_emp(o.technician, f'Work order {o.number}: {o.title}', f'{o.card.code} {o.card.name}\n{o.card.location.path() if o.card.location_id else ""}\n{o.description}',
                     'work_order', o.pk, f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}', summary=f'Do {o.number}: {o.title}')


def plan_cards(p):
    if p.card_id:
        return [p.card] if p.card.status not in ('disposed', 'cwip') else []
    if p.category_id:
        return list(AssetCard.objects.filter(category=p.category).exclude(status__in=('disposed', 'cwip')))
    return []


def run_plans(on=None, user=None):
    """Create the work orders of preventive plans that are due (date plans: lead days ahead; meter plans: reading reached)."""
    on = on or today()
    made = 0
    for p in MaintenancePlan.objects.filter(is_active=True).select_related('activity', 'card', 'category'):
        if p.unit == 'meter':
            if p.next_meter is None:
                continue
            for c in plan_cards(p):
                prof = SV.Profile().objects.filter(asset_id=c.hr_asset_id).first()
                reading = prof.meter_reading if prof else None
                if reading is not None and Decimal(reading) >= Decimal(p.next_meter) and not WorkOrder.objects.filter(plan=p, card=c, status__in=OPEN_WO).exists():
                    make_wo(card=c, title=f'{p.name} – at {qn(p.next_meter)}', kind=p.activity.kind, activity=p.activity, plan=p, team=p.team,
                            technician=p.technician, vendor=p.vendor, estimate=p.estimate, user=user)
                    made += 1
            continue
        guard = 0
        while p.next_due and p.next_due - timedelta(days=p.lead_days) <= on and guard < 24:
            guard += 1
            for c in plan_cards(p):
                title = f'{p.name} – due {p.next_due:%d %b %Y}'
                if not WorkOrder.objects.filter(plan=p, card=c, title=title).exists():
                    make_wo(card=c, title=title, kind=p.activity.kind, activity=p.activity, plan=p, team=p.team, technician=p.technician,
                            vendor=p.vendor, scheduled_on=p.next_due, due_date=p.next_due, estimate=p.estimate, user=user)
                    made += 1
            p.next_due = step(p.next_due, p.every, p.unit)
        p.save(update_fields=['next_due'])
    return made


def calendar_items(frm, to, wo_qs, plan_qs):
    """Work orders by planned date and the plan dates still to come, for the maintenance calendar."""
    out = []
    for o in wo_qs.filter(scheduled_on__gte=frm, scheduled_on__lte=to).select_related('card', 'technician'):
        out.append({'date': o.scheduled_on, 'kind': 'work_order', 'id': o.pk, 'title': f'{o.number} {o.title}', 'asset': f'{o.card.code} {o.card.name}',
                    'status': o.status, 'status_label': o.get_status_display(), 'who': emp_name(o.technician) if o.technician_id else '',
                    'late': bool(o.due_date and o.due_date < today() and o.status in OPEN_WO)})
    for p in plan_qs.filter(is_active=True).exclude(unit='meter').select_related('card', 'category'):
        d, guard = p.next_due, 0
        while d and d <= to and guard < 60:
            guard += 1
            if d >= frm:
                out.append({'date': d, 'kind': 'plan', 'id': p.pk, 'title': f'{p.number} {p.name}', 'status': 'planned', 'status_label': 'Planned',
                            'asset': (f'{p.card.code} {p.card.name}' if p.card_id else f'every {p.category.name}' if p.category_id else ''),
                            'who': emp_name(p.technician) if p.technician_id else '', 'late': False})
            d = step(d, p.every, p.unit)
    return sorted(out, key=lambda x: (x['date'], x['title']))


# ------------------------------------------------------------------ helpdesk
def sla_for(priority):
    r = SlaRule.objects.filter(priority=priority, is_active=True).first()
    return (Decimal(r.response_hours), Decimal(r.resolve_hours), Decimal(r.escalate_hours)) if r else (Decimal(4), Decimal(24), Decimal(4))


def set_sla(t):
    resp, fix, _ = sla_for(t.priority)
    base = t.created_at or now()
    t.response_due = base + timedelta(hours=float(resp))
    t.resolve_due = base + timedelta(hours=float(fix))


def notify_reporter(t, text):
    from .app import app, PAGE
    if t.reporter_id:
        app.tell_emp(t.reporter, f'Ticket {t.number}: {t.get_status_display()}', text, 'ticket', t.pk, f'{t.number} {t.subject}', PAGE + f'r/ticket/{t.pk}',
                     summary=f'Ticket {t.number}: {t.get_status_display()}' if t.status == 'resolved' else None)
    elif t.reporter_email:
        app.send_mail([t.reporter_email], f'[{t.number}] {t.subject} – {t.get_status_display()}', text)


def announce_ticket(t, user=None):
    from .app import app, PAGE
    who = []
    if t.team_id:
        who = [e.users for e in [t.team.leader] + list(t.team.members.all()) if e is not None and e.users_id]
    if not who:
        who = app.users_with_role('ast_helpdesk')
    app.tell(who, f'New ticket {t.number} ({t.get_priority_display()})', f'{t.subject}\n{t.card or ""}\n{t.description}', 'ticket', t.pk,
             f'{t.number} {t.subject}', PAGE + f'r/ticket/{t.pk}', summary=f'Take ticket {t.number}: {t.subject}')
    if t.reporter_email and not t.reporter_id:
        app.send_mail([t.reporter_email], f'[{t.number}] {t.subject}', f'We received your request and registered it as {t.number}. '
                      'Reply to this e-mail to add information.')


def escalate(at=None):
    """Tickets past their response or fix time: level 1 tells the team leader, level 2 the asset managers."""
    from .app import app, PAGE
    at = at or now()
    n = 0
    for t in Ticket.objects.filter(status__in=OPEN_T).select_related('team__leader'):
        _, _, esc = sla_for(t.priority)
        late_resp = t.response_due and not t.responded_at and at > t.response_due
        late_fix = t.resolve_due and at > t.resolve_due
        if not (late_resp or late_fix):
            continue
        due = t.resolve_due if late_fix else t.response_due
        level = 2 if at > due + timedelta(hours=float(esc)) else 1
        if level <= t.escalation:
            continue
        t.escalation = level
        t.save(update_fields=['escalation'])
        what = 'fix' if late_fix else 'first response'
        if level == 1 and t.team_id and t.team.leader and t.team.leader.users_id:
            users = [t.team.leader.users]
        else:
            users = app.users_with_role('ast_manager')
        app.tell(users, f'Escalated: {t.number} past its {what} time', f'{t.subject} – {t.get_priority_display()}', 'ticket', t.pk,
                 f'{t.number} {t.subject}', PAGE + f'r/ticket/{t.pk}', summary=f'Escalated {t.number} (level {level})')
        app.event('ticket', t.pk, f'Escalated to level {level}: past its {what} time', None)
        n += 1
    return n


def day_start(d):
    return timezone.make_aware(datetime.combine(d, time.min))
