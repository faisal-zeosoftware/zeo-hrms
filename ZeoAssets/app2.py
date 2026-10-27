"""Asset Management release 2 (v1.34.0): preventive maintenance and work orders, helpdesk, spare parts and stores.
Registered on the same app as release 1 (imported at the end of app.py)."""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum

from zeo.appkit.engine import Act, AppError, Reg, d, emp_name, in_branches, linked, now, user_name

from . import maint as MT
from . import services as SV
from .app import COORD, FIN, MGR, PAGE, STAFF, VIEW, app, coded, cur, when
from .models import (ActivityType, AssetCard, Location, MaintenancePlan, Part, PartRequest, PartRequestLine, SlaRule, Stock, StockCount,
                     StockCountLine, StockMove, Team, Ticket, WorkOrder)

def qn(v):
    """Quantity without trailing zeros: 2.00 → 2, 2.50 → 2.5."""
    try:
        return f'{Decimal(str(v)).normalize():f}'
    except Exception:
        return str(v)


PLAN = ('ast_manager', 'ast_maint_planner')
TECH = ('ast_technician',)
DESK = ('ast_manager', 'ast_helpdesk')
STORE = ('ast_manager', 'ast_storekeeper')
R2_STAFF = STAFF + ('ast_maint_planner', 'ast_technician', 'ast_helpdesk', 'ast_storekeeper')
PRI = 'choice:low=Low|normal=Normal|high=High|urgent=Urgent'

app.settings_fields = tuple(app.settings_fields) + ('handover_work_orders', 'wo_approval_limit', 'labour_rate', 'ticket_prefix', 'mail_enabled',
                                                   'mail_host', 'mail_port', 'mail_user', 'mail_ssl')

app.option('teams', lambda: Team.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Team)
app.option('activities', lambda: ActivityType.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), ActivityType)
app.option('parts', lambda: Part.objects.filter(is_active=True), lambda x: f'{x.code} {x.name} ({qn(x.on_hand)} {x.unit})', ('code', 'name', 'manufacturer_no'), Part)
app.option('stores', lambda: Location.objects.filter(is_active=True, kind__in=('store', 'site', 'building', 'room', 'vehicle')),
           lambda x: x.path() + f' ({x.code})', ('code', 'name'))
app.option('work-orders', lambda: WorkOrder.objects.all(), lambda x: f'{x.number} {x.title}', ('number', 'title'), WorkOrder)
app.option('tickets', lambda: Ticket.objects.all(), lambda x: f'{x.number} {x.subject}', ('number', 'subject'), Ticket)
app.option('plans', lambda: MaintenancePlan.objects.all(), lambda x: f'{x.number} {x.name}', ('number', 'name'), MaintenancePlan)
app.option('part-requests', lambda: PartRequest.objects.all(), lambda x: x.number, ('number',), PartRequest)
app.option('stock-counts', lambda: StockCount.objects.all(), lambda x: x.number, ('number',), StockCount)
app.provide('assets.work_orders', lambda: WorkOrder.objects.all(), lambda x: f'{x.number} {x.title} ({x.get_status_display()})', ('number', 'title'))


def me(r):
    return app.my_emp(r)


def is_tech(o, r):
    e = me(r)
    return e is not None and (o.technician_id == e.pk or (o.team_id and (o.team.leader_id == e.pk or o.team.members.filter(pk=e.pk).exists())))


def wo_can(states, *roles):
    return lambda o, r: o.status in states and (app.any(r, *roles) or is_tech(o, r))


# ================================================================== work orders
def wo_check(o, request, old):
    if o.card.status in ('disposed',):
        raise AppError('This asset is disposed.', 'card')
    if o.due_date and o.scheduled_on and o.due_date < o.scheduled_on:
        raise AppError('"Finish by" is before the planned date.', 'due_date')


def wo_saver(o, request, creating, old):
    o.save()
    if creating:
        MT.route(o, request.user)
    else:
        MT.wo_costs(o)
    return o


def a_wo_approve(o, request, f):
    o.status = 'assigned' if o.technician_id else 'open'
    o.approved_by, o.approval_note = request.user, f.s('note')[:255]
    o.save()
    app.event('work_order', o.pk, f'Approved by {user_name(request.user)}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('work_order', o.pk, 'Approve')
    MT.tell_tech(o)
    return o


def a_wo_reject(o, request, f):
    o.status, o.approved_by, o.approval_note = 'rejected', request.user, f.s('note')[:255]
    o.save()
    app.event('work_order', o.pk, f'Rejected: {f.s("note")}', request.user)
    app.close_todos('work_order', o.pk, 'Approve')
    return o


def a_wo_assign(o, request, f):
    o.team = f.obj('team', Team, False, 'Team') or o.team
    o.technician = f.emp('technician') or o.technician
    if not (o.team_id or o.technician_id):
        raise AppError('Choose a team or a technician.', 'technician')
    o.scheduled_on = f.dt('scheduled_on') or o.scheduled_on
    o.status = 'assigned' if o.technician_id else 'open'
    o.save()
    app.event('work_order', o.pk, 'Assigned to ' + ', '.join(x for x in (o.team.name if o.team_id else '', emp_name(o.technician) if o.technician_id else '') if x), request.user)
    MT.tell_tech(o)
    return o


def a_wo_start(o, request, f):
    o.status, o.started_at = 'in_progress', o.started_at or now()
    o.meter_reading = f.n('meter_reading', o.meter_reading)
    o.save()
    MT.take_out_of_service(o, request.user)
    app.event('work_order', o.pk, 'Work started' + (' – asset out of service' if o.out_of_service else ''), request.user)
    if o.ticket_id and o.ticket.status in ('new', 'assigned'):
        Ticket.objects.filter(pk=o.ticket_id).update(status='in_progress', responded_at=o.ticket.responded_at or now())
    return o


def a_wo_hold(o, request, f):
    o.status = 'on_hold'
    o.save(update_fields=['status'])
    app.event('work_order', o.pk, f'On hold: {f.s("note")}', request.user)
    return o


def a_wo_checklist(o, request, f):
    done = set(f.ids('checklist'))
    o.checklist = [dict(c, done=i in done) for i, c in enumerate(o.checklist or [])]
    o.save(update_fields=['checklist'])
    app.event('work_order', o.pk, f'Checklist: {len(done)} of {len(o.checklist)} done', request.user)
    return o


def a_wo_parts(o, request, f):
    part = f.obj('part', Part, True, 'Part')
    store = f.obj('store', Location, True, 'Store')
    m = StockMove(kind='issue', part=part, qty=f.n('qty'), from_location=store, work_order=o, card=o.card, branch_id=o.branch_id,
                  note=f'Used on {o.number}', date=MT.today())
    MT.post_move(m, request.user)
    app.event('work_order', o.pk, f'Parts used: {qn(m.qty)} {part.unit} {part.code} {part.name} ({cur()} {m.value})', request.user)
    return WorkOrder.objects.get(pk=o.pk)


def a_wo_done(o, request, f):
    if any(not c.get('done') for c in (o.checklist or [])) and not f.b('skip_checklist'):
        raise AppError('Tick every checklist line first (Checklist), or tick "Close with open checklist lines".')
    o.work_note = f.s('work_note') or o.work_note
    o.labour_hours = f.n('labour_hours', o.labour_hours) or 0
    o.other_cost = f.n('other_cost', o.other_cost) or 0
    o.downtime_hours = f.n('downtime_hours', o.downtime_hours) or 0
    o.meter_reading = f.n('meter_reading', o.meter_reading)
    o.status, o.done_at = 'done', now()
    o.save()
    o = MT.wo_costs(o)
    MT.back_in_service(o, request.user)
    MT.after_done(o, request.user)
    app.event('work_order', o.pk, f'Work done – cost {cur()} {o.total_cost:,.2f} (labour {o.labour_cost:,.2f}, parts {o.parts_cost:,.2f}, other {o.other_cost:,.2f})', request.user)
    app.close_todos('work_order', o.pk)
    return o


def a_wo_close(o, request, f):
    o.status, o.closed_at = 'closed', now()
    o.save(update_fields=['status', 'closed_at'])
    app.event('work_order', o.pk, 'Closed' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_wo_cancel(o, request, f):
    MT.back_in_service(o, request.user)
    o.status = 'cancelled'
    o.save(update_fields=['status'])
    app.event('work_order', o.pk, f'Cancelled: {f.s("note")}', request.user)
    app.close_todos('work_order', o.pk)
    return o


def a_wo_handover(o, request, f):
    if not MT.hand_over(o, request.user):
        raise AppError('The Maintenance app is not active or linked, or "hand work orders over" is off in Settings.')
    return WorkOrder.objects.get(pk=o.pk)


def wo_extra(o, request):
    out = {'checklist': o.checklist or [], 'cost_total': float(o.total_cost), 'place': o.card.location.path() if o.card_id and o.card.location_id else ''}
    if o.mnt_work_order_id and linked(app, 'ZeoMaintenance'):
        from zeo.appkit.engine import call
        info = call(app, 'ZeoMaintenance', 'work_order_status', o.mnt_work_order_id)
        if info:
            out['work_orders'] = [info]
    return out


R = app.reg
R(Reg('work_order', WorkOrder, 'Work order', 'Work orders', 'Maintenance',
      cols=('number', 'title', 'card', 'kind', 'priority', 'scheduled_on', 'technician', 'status', 'total_cost'),
      form=('title', 'card', 'kind', 'activity', 'priority', 'description', 'team', 'technician', 'vendor', 'scheduled_on', 'due_date', 'estimate',
            'out_of_service', 'labour_rate'),
      number=('work_order', 'AWO'), manage=PLAN + ('ast_coordinator',), view=VIEW + ('ast_storekeeper', 'ast_helpdesk'), create=PLAN + ('ast_coordinator', 'ast_helpdesk'),
      own=(('technician', 'emp'),), search=('number', 'title', 'card__code', 'card__name', 'description'),
      filters=('status', 'kind', 'priority', 'card', 'technician', 'team', 'plan'), title='title', subtitle='card',
      defaults=MT.wo_defaults, check=wo_check, saver=wo_saver, edit_when=lambda o: o.status in ('approval', 'open', 'assigned', 'on_hold'),
      links={'mnt_work_order_id': 'maintenance.work_orders'}, extra=wo_extra,
      info=('plan', 'ticket', 'started_at', 'done_at', 'closed_at', 'work_note', 'meter_reading', 'labour_hours', 'labour_cost', 'parts_cost',
            'other_cost', 'downtime_hours', 'approved_by', 'approval_note', 'mnt_work_order_id'),
      children=(('Parts used', 'stock_move', 'work_order'), ('Part requests', 'part_request', 'work_order')),
      actions=[Act('approve', 'Approve', a_wo_approve, when(('approval',), *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('reject', 'Reject', a_wo_reject, when(('approval',), *MGR), style='danger', fields=(('note', 'Reason', 'text', True),)),
               Act('assign', 'Assign', a_wo_assign, when(('open', 'assigned', 'on_hold'), *PLAN, 'ast_coordinator'), style='primary',
                   fields=(('team', 'Team', 'opt:teams', False), ('technician', 'Technician', 'opt:employees', False), ('scheduled_on', 'Planned date', 'date', False))),
               Act('handover', 'Hand over to Maintenance', a_wo_handover,
                   lambda o, r: o.status in ('open', 'assigned') and linked(app, 'ZeoMaintenance') and app.settings().handover_work_orders and app.any(r, *PLAN)),
               Act('start', 'Start work', a_wo_start, wo_can(('open', 'assigned', 'on_hold'), *PLAN), style='primary',
                   fields=(('meter_reading', 'Meter reading (km / hours)', 'decimal', False),)),
               Act('checklist', 'Checklist', a_wo_checklist, lambda o, r: bool(o.checklist) and wo_can(('assigned', 'in_progress'), *PLAN)(o, r),
                   fields=(('checklist', 'Done', 'checklist', False),)),
               Act('parts', 'Use parts', a_wo_parts, wo_can(('assigned', 'in_progress', 'on_hold'), *PLAN, *STORE),
                   fields=(('part', 'Part', 'opt:parts', True), ('qty', 'Quantity', 'decimal', True), ('store', 'From store', 'opt:stores', True))),
               Act('hold', 'On hold', a_wo_hold, wo_can(('assigned', 'in_progress'), *PLAN), fields=(('note', 'Why', 'text', True),)),
               Act('done', 'Work done', a_wo_done, wo_can(('in_progress', 'assigned'), *PLAN), style='primary',
                   fields=(('work_note', 'What was done', 'textarea', True), ('labour_hours', 'Labour hours', 'decimal', False),
                           ('other_cost', 'Vendor / other cost', 'decimal', False), ('downtime_hours', 'Downtime (hours)', 'decimal', False),
                           ('meter_reading', 'Meter reading', 'decimal', False), ('skip_checklist', 'Close with open checklist lines', 'bool', False))),
               Act('close', 'Close', a_wo_close, when(('done',), *PLAN, 'ast_coordinator'), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('cancel', 'Cancel', a_wo_cancel, when(('approval', 'open', 'assigned', 'on_hold'), *PLAN), style='danger', fields=(('note', 'Reason', 'text', True),))],
      help='Preventive, corrective and breakdown work on an asset: assign, start, checklist, parts, work done, close.'))


def plan_check(o, request, old):
    if not (o.card_id or o.category_id):
        raise AppError('Choose the asset or a category.', 'card')
    if o.unit == 'meter' and o.next_meter is None:
        raise AppError('Enter the meter reading of the next service.', 'next_meter')
    if o.unit != 'meter' and not o.next_due:
        raise AppError('Enter the next due date.', 'next_due')


def a_plan_now(o, request, f):
    n = MT.run_plans(user=request.user)
    return {'message': f'{n} work order(s) created from the plans that are due.'}


R(Reg('plan', MaintenancePlan, 'Maintenance plan', 'Maintenance plans', 'Maintenance',
      cols=('number', 'name', 'card', 'category', 'activity', 'every', 'unit', 'next_due', 'next_meter', 'technician', 'is_active'),
      form=('name', 'card', 'category', 'activity', 'every', 'unit', 'next_due', 'next_meter', 'lead_days', 'team', 'technician', 'vendor', 'estimate', 'is_active'),
      number=('plan', 'APM'), manage=PLAN, view=VIEW + TECH, search=('number', 'name', 'card__code'), filters=('is_active', 'unit', 'activity', 'card', 'category'),
      title='name', check=plan_check, files=False, children=(('Work orders', 'work_order', 'plan'),),
      actions=[Act('run', 'Create due work orders now', a_plan_now, lambda o, r: app.any(r, *PLAN))],
      help='Recurring maintenance by days, weeks, months or meter (km / hours); work orders are made before each due date.'))


# ================================================================== helpdesk
def t_defaults(o, request):
    e = me(request)
    if not o.reporter_id and not o.reporter_email:
        o.reporter = e
    if not app.any(request, *DESK, *COORD) and e is not None:
        o.source = 'self'
    if o.card_id and not o.location_id:
        o.location = o.card.location
    o.number = f'{app.settings().ticket_prefix or "AHD"}-' + o.number.split('-', 1)[-1] if o.number else o.number


def t_saver(o, request, creating, old):
    if creating:
        o.save()
        MT.set_sla(o)
        if o.team_id or o.assignee_id:
            o.status = 'assigned'
        o.save()
        MT.announce_ticket(o, request.user)
    else:
        if old and old.priority != o.priority:
            MT.set_sla(o)
        o.save()
    return o


def t_take(o, request, f):
    o.assignee = f.emp('assignee') or me(request) or o.assignee
    o.team = f.obj('team', Team, False, 'Team') or o.team
    o.status = 'assigned' if o.status == 'new' else o.status
    o.responded_at = o.responded_at or now()
    o.save()
    app.event('ticket', o.pk, f'Assigned to {emp_name(o.assignee) if o.assignee_id else o.team}', request.user)
    app.close_todos('ticket', o.pk, 'Take')
    if o.assignee_id:
        app.tell_emp(o.assignee, f'Ticket {o.number} for you', o.subject, 'ticket', o.pk, f'{o.number} {o.subject}', PAGE + f'r/ticket/{o.pk}',
                     summary=f'Work on ticket {o.number}')
    MT.notify_reporter(o, f'Your ticket {o.number} is being handled by {emp_name(o.assignee) if o.assignee_id else o.team}.')
    return o


def t_status(new, text, tell=None):
    def run(o, request, f):
        o.status = new
        o.responded_at = o.responded_at or now()
        if new == 'resolved':
            o.resolved_at, o.resolution = now(), f.s('resolution') or o.resolution
            o.repair_cost = f.n('repair_cost', o.repair_cost) or 0
        o.save()
        app.event('ticket', o.pk, text + (f': {f.s("note") or f.s("resolution")}' if (f.s('note') or f.s('resolution')) else ''), request.user)
        if tell:
            MT.notify_reporter(o, tell.format(o=o, note=f.s('note') or f.s('resolution')))
        return o
    return run


def t_confirm(o, request, f):
    o.status, o.closed_at = 'closed', now()
    if f.i('rating'):
        o.rating = max(1, min(5, f.i('rating')))
    o.feedback = f.s('feedback')[:255]
    o.save()
    app.event('ticket', o.pk, f'Confirmed fixed by {user_name(request.user)}' + (f' – rating {o.rating}/5' if o.rating else '') + (f': {o.feedback}' if o.feedback else ''), request.user)
    app.close_todos('ticket', o.pk)
    return o


def t_reopen(o, request, f):
    o.status, o.resolved_at, o.escalation = 'in_progress', None, 0
    o.save()
    app.event('ticket', o.pk, f'Reopened: {f.s("note")}', request.user)
    who = [o.assignee.users] if o.assignee_id and o.assignee.users_id else app.users_with_role('ast_helpdesk')
    app.tell(who, f'Ticket {o.number} reopened', f.s('note'), 'ticket', o.pk, f'{o.number} {o.subject}', PAGE + f'r/ticket/{o.pk}', summary=f'Reopened: {o.number}')
    return o


def t_make_wo(o, request, f):
    if not o.card_id:
        raise AppError('Choose the asset of the ticket first (Edit).')
    w = MT.make_wo(card=o.card, title=o.subject, kind='breakdown' if o.kind == 'breakdown' else 'corrective', ticket=o, priority=o.priority,
                   description=o.description, team=f.obj('team', Team, False, 'Team') or o.team, technician=f.emp('technician') or o.assignee,
                   estimate=f.n('estimate'), user=request.user)
    o.status = 'in_progress' if o.status in ('new', 'assigned') else o.status
    o.responded_at = o.responded_at or now()
    o.save()
    app.event('ticket', o.pk, f'Work order {w.number} created', request.user)
    return w


def own_ticket(o, r):
    e = me(r)
    return e is not None and o.reporter_id == e.pk


def t_extra(o, request):
    late = bool(o.resolve_due and o.status in MT.OPEN_T and now() > o.resolve_due)
    return {'sla': {'response_due': d(o.response_due), 'resolve_due': d(o.resolve_due), 'responded_at': d(o.responded_at), 'done_at': d(o.resolved_at),
                    'response_late': bool(o.response_due and (o.responded_at or now()) > o.response_due), 'resolve_late': late},
            'cost_total': float(sum((w.total_cost for w in o.work_orders.all()), Decimal(0)) or o.repair_cost)}


R(Reg('ticket', Ticket, 'Ticket', 'Helpdesk tickets', 'Helpdesk',
      cols=('number', 'subject', 'card', 'kind', 'priority', 'reporter', 'assignee', 'status', 'resolve_due'),
      form=('subject', 'card', 'kind', 'priority', 'description', 'location', 'reporter', 'reporter_email', 'team', 'assignee', 'source'),
      number=('ticket', 'AHD'), manage=DESK + ('ast_coordinator', 'ast_maint_planner'), view=VIEW + TECH, create='all',
      own=(('reporter', 'emp'), ('assignee', 'emp')), search=('number', 'subject', 'description', 'card__code', 'reporter_email'),
      filters=('status', 'priority', 'kind', 'team', 'assignee', 'card', 'source'), title='subject', subtitle='card',
      defaults=t_defaults, saver=t_saver, edit_when=lambda o: o.status in ('new', 'assigned', 'in_progress', 'waiting'), extra=t_extra,
      info=('response_due', 'resolve_due', 'responded_at', 'resolved_at', 'closed_at', 'escalation', 'resolution', 'repair_cost', 'rating', 'feedback', 'latitude', 'longitude'),
      children=(('Work orders', 'work_order', 'ticket'),),
      actions=[Act('take', 'Assign', t_take, lambda o, r: o.status in ('new', 'assigned', 'waiting') and app.any(r, *DESK, 'ast_maint_planner'), style='primary',
                   fields=(('team', 'Team', 'opt:teams', False), ('assignee', 'Assign to', 'opt:employees', False))),
               Act('work_order', 'Create work order', t_make_wo, lambda o, r: o.status in MT.OPEN_T and bool(o.card_id) and app.any(r, *DESK, *PLAN),
                   fields=(('team', 'Team', 'opt:teams', False), ('technician', 'Technician', 'opt:employees', False), ('estimate', 'Estimated cost', 'decimal', False))),
               Act('wait', 'Ask the user', t_status('waiting', 'Waiting for the user', 'About your ticket {o.number}: {note}'),
                   lambda o, r: o.status in ('assigned', 'in_progress') and (app.any(r, *DESK) or getattr(me(r), 'pk', 0) == o.assignee_id), fields=(('note', 'Question', 'textarea', True),)),
               Act('resolve', 'Resolve', t_status('resolved', 'Resolved', 'Your ticket {o.number} is resolved: {note}. Please confirm, or reopen it.'),
                   lambda o, r: o.status in ('new', 'assigned', 'in_progress', 'waiting') and (app.any(r, *DESK) or getattr(me(r), 'pk', 0) == o.assignee_id), style='primary',
                   fields=(('resolution', 'Resolution', 'textarea', True), ('repair_cost', 'Repair cost', 'decimal', False))),
               Act('confirm', 'Confirm fixed', t_confirm, lambda o, r: o.status == 'resolved' and (own_ticket(o, r) or app.any(r, *DESK)), style='primary',
                   fields=(('rating', 'Rating (1–5)', 'choice:5=5 – excellent|4=4|3=3|2=2|1=1 – poor', False), ('feedback', 'Feedback', 'text', False))),
               Act('reopen', 'Reopen', t_reopen, lambda o, r: o.status == 'resolved' and (own_ticket(o, r) or app.any(r, *DESK)), fields=(('note', 'What is still wrong', 'text', True),)),
               Act('cancel', 'Cancel', t_status('cancelled', 'Cancelled', 'Your ticket {o.number} was cancelled: {note}'),
                   lambda o, r: o.status in ('new', 'assigned', 'waiting') and (own_ticket(o, r) or app.any(r, *DESK)), style='danger', fields=(('note', 'Reason', 'text', False),))],
      help='Complaints, breakdowns and incidents about assets – from employees, scans, mobile or e-mail – with SLA, escalation and feedback.'))

R(Reg('team', Team, 'Team', 'Teams', 'Setup', cols=('code', 'name', 'leader', 'email', 'is_active'), form=('code', 'name', 'leader', 'members', 'email', 'is_active'),
      manage=DESK + PLAN, view=R2_STAFF, search=('code', 'name'), filters=('is_active',), title='name', subtitle='code', files=False, saver=coded('team', 'TM'),
      help='Support and maintenance teams; tickets and work orders go to a team, then to a technician.'))
for key, model, label, plural, cols, form in (
        ('activity', ActivityType, 'Activity type', 'Activity types', ('code', 'name', 'kind', 'hours', 'out_of_service', 'is_active'),
         ('code', 'name', 'kind', 'checklist', 'hours', 'out_of_service', 'is_active')),
        ('sla', SlaRule, 'SLA rule', 'SLA rules', ('code', 'priority', 'response_hours', 'resolve_hours', 'escalate_hours', 'is_active'),
         ('code', 'priority', 'response_hours', 'resolve_hours', 'escalate_hours', 'is_active'))):
    R(Reg(key, model, label, plural, 'Setup', cols=cols + ('review_status',), form=form, manage=PLAN + DESK, view=R2_STAFF, search=('code',),
          filters=('is_active', 'review_status'), title='code', review=True, deletable=True, files=False))


# ================================================================== spare parts
def part_saver(o, request, creating, old):
    if not o.code:
        o.code = app.next_number('part', 'SP')
    if Part.objects.filter(code__iexact=o.code).exclude(pk=o.pk).exists():
        raise AppError('This part code is already used.', 'code')
    o.save()
    return o


def part_extra(o, request):
    return {'stock_by_store': [{'store': s.location.path(), 'qty': float(s.qty), 'bin': s.bin} for s in o.stock.select_related('location') if s.qty],
            'low': bool(o.min_qty and o.on_hand <= o.min_qty)}


R(Reg('part', Part, 'Spare part', 'Spare parts', 'Spare parts',
      cols=('code', 'name', 'group', 'unit', 'on_hand', 'min_qty', 'unit_cost', 'is_active'),
      form=('code', 'name', 'group', 'unit', 'manufacturer_no', 'supplier', 'min_qty', 'reorder_qty', 'is_consumable', 'categories', 'cards', 'is_active'),
      manage=STORE, view=R2_STAFF, search=('code', 'name', 'manufacturer_no', 'group'), filters=('group', 'is_consumable', 'is_active'),
      title='name', subtitle='code', saver=part_saver, extra=part_extra, restricted=('unit_cost',), can_restricted=lambda r, o: app.is_admin(r) or app.any(r, *STORE, *FIN),
      info=('unit_cost', 'on_hand'), children=(('Stock by store', 'stock', 'part'), ('Movements', 'stock_move', 'part')),
      help='Spare parts and consumables with minimum stock; stock is kept per store (stores are locations).'))
R(Reg('stock', Stock, 'Stock', 'Stock by store', 'Spare parts', cols=('part', 'location', 'qty', 'bin'), form=('part', 'location', 'bin'),
      manage=STORE, view=R2_STAFF, create='none', child_of='part', search=('part__code', 'part__name'), filters=('location', 'part'), title='part', subtitle='location',
      files=False, help='Quantity of each part in each store (changed only by stock movements).'))


def move_saver(o, request, creating, old):
    if not creating:
        raise AppError('A posted movement cannot be changed – post a correcting movement.')
    return MT.post_move(o, request.user)


R(Reg('stock_move', StockMove, 'Stock movement', 'Stock movements', 'Spare parts',
      cols=('number', 'date', 'kind', 'part', 'qty', 'from_location', 'to_location', 'value', 'work_order'),
      form=('date', 'kind', 'part', 'qty', 'from_location', 'to_location', 'unit_cost', 'work_order', 'card', 'reference', 'note'),
      manage=STORE, view=R2_STAFF, search=('number', 'part__code', 'part__name', 'reference', 'note'), filters=('kind', 'part', 'from_location', 'to_location', 'work_order'),
      title='number', subtitle='part', saver=move_saver, edit_when=lambda o: False, files=True, restricted=('unit_cost', 'value'),
      can_restricted=lambda r, o: app.is_admin(r) or app.any(r, *STORE, *FIN),
      help='Receipts from suppliers, issues to work orders, transfers between stores, returns and count adjustments.'))


def pr_defaults(o, request):
    if not o.requested_by_id:
        o.requested_by = me(request)


def pr_issue(o, request, f):
    with transaction.atomic():
        for ln in o.lines.select_related('part'):
            left = Decimal(ln.qty) - Decimal(ln.issued)
            if left <= 0:
                continue
            m = StockMove(kind='issue', part=ln.part, qty=left, from_location=o.store, work_order=o.work_order, card=o.work_order.card if o.work_order_id else None,
                          branch_id=o.branch_id, note=f'Part request {o.number}', date=MT.today())
            MT.post_move(m, request.user)
            ln.issued = ln.qty
            ln.save(update_fields=['issued'])
        o.status = 'issued'
        o.save(update_fields=['status'])
    app.event('part_request', o.pk, 'Issued from the store', request.user)
    if o.requested_by_id:
        app.tell_emp(o.requested_by, f'Parts ready: {o.number}', 'Your parts were issued from the store.', 'part_request', o.pk, o.number, PAGE + f'r/part_request/{o.pk}')
    return o


def pr_status(new, text):
    def run(o, request, f):
        if new == 'approved' and not o.lines.exists():
            raise AppError('Add the parts first.')
        o.status = new
        o.note = f.s('note') or o.note
        o.save()
        app.event('part_request', o.pk, text + (f': {f.s("note")}' if f.s('note') else ''), request.user)
        if new == 'requested':
            app.tell(app.users_with_role('ast_storekeeper'), f'Part request {o.number}', o.note, 'part_request', o.pk, o.number, PAGE + f'r/part_request/{o.pk}',
                     summary=f'Approve part request {o.number}')
        return o
    return run


R(Reg('part_request', PartRequest, 'Part request', 'Part requests', 'Spare parts',
      cols=('number', 'requested_by', 'work_order', 'store', 'needed_by', 'status'), form=('work_order', 'store', 'needed_by', 'note'),
      number=('part_request', 'PRQ'), manage=STORE, view=VIEW + PLAN, create=PLAN + TECH + STORE + ('ast_coordinator',), own=(('requested_by', 'emp'),), own_edit=True,
      search=('number', 'note'), filters=('status', 'store', 'work_order'), defaults=pr_defaults, edit_when=lambda o: o.status == 'requested', files=False,
      children=(('Parts', 'part_request_line', 'request'),),
      actions=[Act('approve', 'Approve', pr_status('approved', 'Approved'), when(('requested',), *STORE), style='primary'),
               Act('issue', 'Issue from store', pr_issue, when(('approved',), *STORE), style='primary'),
               Act('reject', 'Reject', pr_status('rejected', 'Rejected'), when(('requested', 'approved'), *STORE), style='danger', fields=(('note', 'Reason', 'text', True),))],
      help='Technicians ask the store for parts (for a work order); the storekeeper approves and issues them.'))
R(Reg('part_request_line', PartRequestLine, 'Requested part', 'Requested parts', 'Spare parts', cols=('part', 'qty', 'issued'), form=('request', 'part', 'qty'),
      manage=STORE, view=VIEW + PLAN + TECH, create=PLAN + TECH + STORE, child_of='request', search=('part__code',), filters=('part',), title='part', subtitle='qty',
      files=False, deletable=True, edit_when=lambda o: o.request.status == 'requested'))


def sc_start(o, request, f):
    rows = [StockCountLine(count=o, part=s.part, expected=s.qty) for s in Stock.objects.filter(location=o.store).select_related('part')]
    StockCountLine.objects.bulk_create(rows, ignore_conflicts=True)
    o.status = 'counting'
    o.save(update_fields=['status'])
    app.event('stock_count', o.pk, f'Counting started: {len(rows)} part(s) expected', request.user)
    return o


def sc_close(o, request, f):
    diffs, value = 0, Decimal(0)
    with transaction.atomic():
        for ln in o.lines.select_related('part'):
            if ln.counted is None:
                continue
            cur_qty = Stock.objects.filter(part=ln.part, location=o.store).values_list('qty', flat=True).first() or 0
            diff = Decimal(ln.counted) - Decimal(cur_qty)
            ln.difference = diff
            ln.save(update_fields=['difference'])
            if diff:
                m = MT.post_move(StockMove(kind='adjust', part=ln.part, qty=diff, to_location=o.store, branch_id=o.branch_id, note=f'Stock count {o.number}',
                                           date=MT.today()), request.user)
                diffs += 1
                value += (m.value if diff > 0 else -m.value)
        o.status, o.closed_at, o.differences, o.value_difference = 'closed', now(), diffs, value
        o.lines_counted = o.lines.exclude(counted__isnull=True).count()
        o.save()
    app.event('stock_count', o.pk, f'Closed: {diffs} difference(s), value {cur()} {value:,.2f}', request.user)
    return o


R(Reg('stock_count', StockCount, 'Stock count', 'Stock counts', 'Spare parts',
      cols=('number', 'store', 'status', 'lines_counted', 'differences', 'value_difference', 'closed_at'), form=('store', 'note'),
      number=('stock_count', 'SC'), manage=STORE, view=VIEW + ('ast_auditor',), search=('number',), filters=('status', 'store'), edit_when=lambda o: o.status == 'draft',
      files=False, children=(('Lines', 'stock_count_line', 'count'),),
      actions=[Act('start', 'Start counting', sc_start, when(('draft',), *STORE, 'ast_auditor'), style='primary'),
               Act('close', 'Close and adjust stock', sc_close, when(('counting',), *STORE), style='primary', confirm='The stock is set to the counted quantities.'),
               Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), o)[-1], when(('draft', 'counting'), *STORE), style='danger')],
      help='Physical stock count of a store; closing posts the differences as adjustments.'))
R(Reg('stock_count_line', StockCountLine, 'Count line', 'Count lines', 'Spare parts', cols=('part', 'expected', 'counted', 'difference'), form=('count', 'part', 'counted'),
      manage=STORE + ('ast_auditor',), view=VIEW, create=STORE + ('ast_auditor',), child_of='count', search=('part__code', 'part__name'), filters=('part',), title='part',
      files=False, edit_when=lambda o: o.count.status == 'counting'))


# ================================================================== hooks, dashboard, my work, daily
def h_request(*, title, description='', card_id=None, reporter_id=None, email='', priority='normal', source='staff', user=None):
    t = Ticket(subject=title[:200], description=description, card_id=card_id, reporter_id=reporter_id, reporter_email=email or '', priority=priority, source=source)
    t.number = f'{app.settings().ticket_prefix or "AHD"}-{app.next_number("ticket", "AHD").split("-", 1)[-1]}'
    t.save()
    MT.set_sla(t)
    t.save()
    app.event('ticket', t.pk, f'Received from {t.get_source_display()}', user)
    MT.announce_ticket(t, user)
    return {'id': t.pk, 'number': t.number}


app.hooks.update({'ticket': h_request})

_dash1 = app.dashboard
_my1 = app.my_work
_daily1 = app.daily


def dashboard(request):
    out = _dash1(request)
    wos = app.regs['work_order'].scope(request)
    open_wo = wos.filter(status__in=MT.OPEN_WO)
    t = MT.today()
    overdue_wo = open_wo.filter(due_date__lt=t)
    tickets = app.regs['ticket'].scope(request).filter(status__in=MT.OPEN_T)
    late_t = tickets.filter(resolve_due__lt=now())
    parts = in_branches(Part.objects.filter(is_active=True, min_qty__gt=0, on_hand__lte=F('min_qty')), request)
    out['cards'] += [{'label': 'Open work orders', 'value': open_wo.count(), 'link': 'r/work_order?open=1'},
                     {'label': 'Work orders overdue', 'value': overdue_wo.count(), 'tone': 'bad' if overdue_wo.exists() else 'ok', 'link': 'r/work_order?open=1'},
                     {'label': 'Open tickets', 'value': tickets.count(), 'link': 'r/ticket?open=1', 'hint': f'{late_t.count()} past SLA'},
                     {'label': 'Parts below minimum', 'value': parts.count(), 'tone': 'bad' if parts.exists() else '', 'link': 'r/part'}]
    out['lists'] = [{'title': 'Tickets past SLA', 'kind': 'ticket', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.subject}', 'sub': str(x.card or ''), 'badge': x.get_priority_display()}
                                                                          for x in late_t.order_by('resolve_due')[:8]]},
                    {'title': 'Maintenance due in 14 days', 'kind': 'work_order',
                     'rows': [{'id': x.pk, 'title': f'{x.number} {x.title}', 'sub': emp_name(x.technician) if x.technician_id else 'not assigned', 'badge': d(x.scheduled_on)}
                              for x in open_wo.filter(scheduled_on__lte=t + timedelta(days=14)).select_related('technician').order_by('scheduled_on')[:8]]},
                    {'title': 'Parts below minimum', 'kind': 'part', 'rows': [{'id': x.pk, 'title': f'{x.code} {x.name}', 'sub': f'minimum {qn(x.min_qty)}', 'badge': f'{qn(x.on_hand)} {x.unit}'}
                                                                             for x in parts[:8]]}] + out['lists']
    return out


def my_work(request):
    out = _my1(request)
    e = me(request)
    if e is not None:
        out.insert(0, {'title': 'My work orders', 'kind': 'work_order', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.title}', 'sub': f'{x.card.code} {x.card.name}', 'badge': x.get_status_display()}
                                                                                 for x in WorkOrder.objects.filter(technician=e, status__in=MT.OPEN_WO).select_related('card')[:30]]})
        out.insert(1, {'title': 'My tickets', 'kind': 'ticket', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.subject}', 'sub': x.get_status_display(), 'badge': x.get_priority_display()}
                                                                         for x in Ticket.objects.filter(Q(reporter=e) | Q(assignee=e)).exclude(status__in=('closed', 'cancelled'))[:30]]})
    if app.any(request, *DESK):
        out.append({'title': 'New tickets to assign', 'kind': 'ticket', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.subject}', 'sub': str(x.card or ''), 'badge': x.get_priority_display()}
                                                                                for x in in_branches(Ticket.objects.filter(status='new'), request)[:30]]})
    if app.any(request, *STORE):
        out.append({'title': 'Part requests to issue', 'kind': 'part_request', 'rows': [{'id': x.pk, 'title': x.number, 'sub': emp_name(x.requested_by) if x.requested_by_id else '', 'badge': x.get_status_display()}
                                                                                       for x in in_branches(PartRequest.objects.filter(status__in=('requested', 'approved')), request)[:30]]})
    return out


def daily():
    out = _daily1()
    out['plans'] = MT.run_plans()
    out['handed_over'] = MT.sync_handed_over()
    out['escalated'] = MT.escalate()
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily


# ================================================================== release 2 on the asset record
def a_card_ticket(o, request, f):
    res = h_request(title=f.s('subject'), description=f.s('description'), card_id=o.pk, reporter_id=getattr(me(request), 'pk', None),
                    priority=f.choice('priority', (('low', ''), ('normal', ''), ('high', ''), ('urgent', '')), 'normal'), source='staff', user=request.user)
    app.event('asset', o.pk, f'Ticket {res["number"]} raised', request.user)
    return {'message': f'Ticket {res["number"]} raised.'}


def a_card_wo(o, request, f):
    w = MT.make_wo(card=o, title=f.s('title'), kind=f.choice('kind', WorkOrder._meta.get_field('kind').choices, 'corrective'),
                   activity=f.obj('activity', ActivityType, False, 'Activity'), priority=f.choice('priority', WorkOrder._meta.get_field('priority').choices, 'normal'),
                   description=f.s('description'), team=f.obj('team', Team, False, 'Team'), technician=f.emp('technician'), estimate=f.n('estimate'),
                   scheduled_on=f.dt('scheduled_on'), user=request.user)
    app.event('asset', o.pk, f'Work order {w.number} created', request.user)
    return w


_card = app.regs['asset']
_card.children = tuple(_card.children) + (('Work orders', 'work_order', 'card'), ('Tickets', 'ticket', 'card'), ('Maintenance plans', 'plan', 'card'))
_card.actions += [
    Act('ticket', 'Raise a ticket', a_card_ticket, lambda o, r: o.status != 'disposed',
        fields=(('subject', 'What is wrong', 'text', True), ('priority', 'Priority', PRI, False), ('description', 'Details', 'textarea', False))),
    Act('work_order', 'New work order', a_card_wo, lambda o, r: o.status not in ('disposed', 'cwip') and app.any(r, *PLAN, 'ast_coordinator'),
        fields=(('title', 'Work to do', 'text', True), ('kind', 'Kind', 'choice:corrective=Corrective|breakdown=Breakdown|preventive=Preventive|inspection=Inspection|calibration=Calibration', False),
                ('activity', 'Activity', 'opt:activities', False), ('priority', 'Priority', PRI, False), ('team', 'Team', 'opt:teams', False),
                ('technician', 'Technician', 'opt:employees', False), ('scheduled_on', 'Planned date', 'date', False), ('estimate', 'Estimated cost', 'decimal', False),
                ('description', 'Details', 'textarea', False))),
]
# the asset "Report a fault" of release 1 (Maintenance app) stays for companies that use the Maintenance app

_t_defaults = t_defaults


def t_defaults2(o, request):
    _t_defaults(o, request)
    data = getattr(request, 'data', {}) or {}
    for k in ('latitude', 'longitude'):
        v = data.get(k)
        try:
            if v not in (None, ''):
                setattr(o, k, Decimal(str(v)).quantize(Decimal('0.000001')))
        except Exception:
            pass
    if data.get('source') in ('scan', 'mobile'):
        o.source = data['source']


app.regs['ticket'].defaults = t_defaults2


# v1.42.0 business partners: supplier / vendor pickers (business partners) next to the typed names
from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)


# v1.43.0 item master: item picker on spare parts / asset model on assets
from ZeoMasters.item_exts import attach as _attach_items  # noqa: E402
_attach_items(app)
