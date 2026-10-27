"""ZEO Maintenance on the app kit: work orders with SLA, quotes and approvals, costs and recharges, preventive plans,
inspections; links to Property (units, portal requests, owner approval, recharges, inspection results) and CRM."""
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, F, Q, Sum

from zeo.appkit.engine import (in_branches, dash_period, Act, App, AppError, Reg, add_months, call, d, emp_name, linked, link_label, now, today, user_name)

from .models import (ROLES, Asset, Category, Checklist, CostLine, Inspection, Location, MaintEvent, MaintFile, MaintSequence, MaintSettings,
                     PreventivePlan, Quote, Vendor, WorkOrder)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-maintenance/'
ZERO = Decimal('0')
C2 = Decimal('0.01')

app = App(
    label='ZeoMaintenance', slug='zeo-maintenance', title='Maintenance', settings_model=MaintSettings, sequence_model=MaintSequence,
    event_model=MaintEvent, file_model=MaintFile, roles=ROLES, role_prefix='mnt_', manager_role='mnt_manager', packs=PACKS,
    pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
    industry_from_company={'PM': 'property_management', 'CON': 'construction', 'OG': 'oil_gas', 'HC': 'healthcare', 'EDU': 'education', 'MFG': 'manufacturing', 'RET': 'retail', 'IT': 'it', 'FIN': 'finance'},   # v1.38.0
    pack_models={'category': (Category, 'code'), 'checklist': (Checklist, 'code')},
    links=(('ZeoProperty', 'Property', 'Work on Property units; tenant and buyer requests from the portal; owner approval above the '
            'agreement limit; costs charged to the tenant or the owner as billing records; move-in / move-out / unit-ready / snag inspections.'),
           ('ZeoCRM', 'CRM', 'Service requests and complaints sent from CRM contacts and opportunities.'),
           ('ZeoAssets', 'Asset Management', 'v1.33.0: assets registered in Asset Management are sent here (critical ones or all); '
            '"Report a fault" on an asset opens a work order on it.')),
    page=PAGE,
)
app.settings_fields = ('approval_limit', 'markup_percent', 'currency', 'verify_by_requester')

MGR = ('mnt_manager',)
COORD = ('mnt_manager', 'mnt_coordinator')
VIEW = ('mnt_viewer',)
STAFF = ('mnt_manager', 'mnt_coordinator', 'mnt_technician', 'mnt_viewer')
OPEN = ('new', 'assigned', 'quoting', 'approval', 'in_progress', 'on_hold')


def money(v):
    return Decimal(v or 0).quantize(C2, ROUND_HALF_UP)


def cur():
    return app.settings().currency


def is_tech(o, request):
    me = app.my_emp(request)
    return me is not None and o.technician_id == me.pk


def coord_or_tech(states):
    return lambda o, r: o.status in states and (app.any(r, *COORD) or is_tech(o, r))


def when(states, *roles):
    return lambda o, r: o.status in states and app.any(r, *roles)


# ------------------------------------------------------------------ options
app.option('categories', lambda: Category.objects.filter(is_active=True), lambda x: x.name, ('name', 'code', 'trade'), Category)
app.option('locations', lambda: Location.objects.filter(is_active=True), lambda x: f'{x.code} {x.name}', ('code', 'name'), Location)
app.option('vendors', lambda: Vendor.objects.filter(is_active=True), lambda x: x.name + (f' · {x.trade}' if x.trade else ''), ('name', 'trade'), Vendor)
app.option('assets', lambda: Asset.objects.exclude(status='retired'), lambda x: f'{x.code} {x.name}', ('code', 'name', 'serial'), Asset)
app.option('checklists', lambda: Checklist.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), Checklist)
app.option('work-orders', lambda: WorkOrder.objects.all(), lambda x: f'{x.number} {x.title}', ('number', 'title'), WorkOrder)
app.option('quotes', lambda: Quote.objects.all(), lambda x: f'{x.number} {x.vendor.name} {x.amount}', ('number',), Quote)
app.option('inspections', lambda: Inspection.objects.all(), lambda x: f'{x.number} {x.get_kind_display()}', ('number', 'reference'), Inspection)
app.option('plans', lambda: PreventivePlan.objects.all(), lambda x: f'{x.number} {x.name}', ('number', 'name'), PreventivePlan)
app.provide('maintenance.work_orders', lambda: WorkOrder.objects.all(), lambda x: f'{x.number} {x.title} ({x.get_status_display()})', ('number', 'title'))
app.provide('maintenance.inspections', lambda: Inspection.objects.all(), lambda x: f'{x.number} {x.get_kind_display()} ({x.get_status_display()})', ('number',))
app.provide('maintenance.assets', lambda: Asset.objects.all(), lambda x: f'{x.code} {x.name}', ('code', 'name', 'serial'))
app.provide('maintenance.locations', lambda: Location.objects.filter(is_active=True), lambda x: f'{x.code} {x.name}', ('code', 'name'))


def place(o):
    if getattr(o, 'property_unit_id', None):
        return link_label('property.units', o.property_unit_id)
    return str(o.location) if getattr(o, 'location_id', None) else ''


# ------------------------------------------------------------------ work orders
def sla(o):
    c = o.category
    base = now()
    hrs_r, hrs_f = (c.response_hours, c.resolve_hours) if c else (24, 72)
    if o.priority == 'urgent':
        hrs_r, hrs_f = min(hrs_r, 2), min(hrs_f, 24)
    elif o.priority == 'high':
        hrs_r, hrs_f = min(hrs_r, 8), min(hrs_f, 48)
    o.response_due, o.resolve_due = base + timedelta(hours=hrs_r), base + timedelta(hours=hrs_f)


def wo_defaults(o, request):
    if o.category_id:
        if o.priority == 'normal' and o.category.priority != 'normal':
            o.priority = o.category.priority
        if o.who_pays == 'company' and o.category.who_pays not in ('decide', 'company') \
                and (o.category.who_pays not in ('tenant', 'owner') or o.property_unit_id):
            # v1.62.0: a tenant / owner default of the category only for a job on a unit – a common-area job (no unit) stayed
            # "tenant pays", was closed and never billed to anybody
            o.who_pays = o.category.who_pays
    if not o.requester:
        o.requester = user_name(request.user) if request else ''
    if o.asset_id and not o.location_id and not o.property_unit_id:
        o.location, o.property_unit_id = o.asset.location, o.asset.property_unit_id
    sla(o)


def wo_check(o, request, old):
    if not o.location_id and not o.property_unit_id and not o.asset_id:
        raise AppError('Say where: choose a unit, a location or an asset.', 'location')


def wo_saver(o, request, creating, old):
    o.save()
    if creating:
        announce(o)
    return o


def announce(o):
    app.tell(app.users_with_role('mnt_coordinator'), f'New work order {o.number} ({o.get_priority_display()})', f'{o.title}\n{place(o)}\n{o.description}',
             'work_order', o.pk, f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}', summary=f'Assign {o.number}: {o.title}')
    if o.requester_email:
        app.send_mail([o.requester_email], f'We received your request {o.number}',
                      f'Dear {o.requester or "customer"},\n\nYour request "{o.title}" was registered as {o.number}. We will contact you to arrange the work.\n')


def tell_requester(o, text):
    if o.requester_email:
        app.send_mail([o.requester_email], f'Your request {o.number}: {o.get_status_display()}', f'Dear {o.requester or "customer"},\n\n{text}\n')


def total_cost(o):
    return money(o.costs.aggregate(s=Sum('amount'))['s'] or 0)


def approval_needed(o):
    """Owner approval when the owner pays and the estimate is above the owner's limit (Property); else manager approval above the setting."""
    est = o.estimate or ZERO
    if o.who_pays == 'owner' and o.property_unit_id:
        info = call(app, 'ZeoProperty', 'owner_approval', o.property_unit_id)
        if info and est > Decimal(str(info['limit'])):
            return 'owner', info
    if est > (app.settings().approval_limit or 0):
        return 'manager', None
    return '', None


def ask_approval(o, request):
    who, info = approval_needed(o)
    if not who:
        return False
    o.status, o.approval_by, o.approved_at = 'approval', who, None
    o.save()
    if who == 'owner':
        app.event('work_order', o.pk, f'Waiting for the owner ({info["name"]}) to approve {cur()} {o.estimate} (limit {info["limit"]:,.0f})', request.user if request else None)
        if info.get('email'):
            app.send_mail([info['email']], f'Approval needed: {o.number}',
                          f'Dear {info["name"]},\n\nWork "{o.title}" at {place(o)} is estimated at {cur()} {o.estimate}, above your approval limit. '
                          + ('Please approve or decline it in the owner portal.\n' if info.get('portal') else 'Please reply to approve or decline.\n'))
    else:
        app.event('work_order', o.pk, f'Waiting for the maintenance manager to approve {cur()} {o.estimate}', request.user if request else None)
        app.tell(app.users_with_role('mnt_manager'), f'Approve {o.number}: {cur()} {o.estimate}', f'{o.title} – {place(o)}', 'work_order', o.pk,
                 f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}', summary=f'Approve {o.number} ({cur()} {o.estimate})')
    return True


def a_assign(o, request, f):
    tech = f.emp('technician')
    vendor = f.obj('vendor', Vendor, False, 'Vendor')
    if tech is None and vendor is None:
        raise AppError('Choose a technician or a vendor.', 'technician')
    o.technician, o.vendor = tech or o.technician, vendor or o.vendor
    o.estimate = f.n('estimate', o.estimate)
    if f.s('who_pays'):
        o.who_pays = f.choice('who_pays', WorkOrder._meta.get_field('who_pays').choices, o.who_pays)
    o.responded_at = o.responded_at or now()
    o.status = 'assigned'
    o.save()
    app.event('work_order', o.pk, 'Assigned to ' + ', '.join(x for x in (emp_name(tech), vendor.name if vendor else '') if x), request.user)
    app.close_todos('work_order', o.pk, 'Assign')
    if tech and tech.users_id:
        app.tell([tech.users], f'Work order {o.number} for you', f'{o.title}\n{place(o)}\nAccess: {o.access or "-"}', 'work_order', o.pk,
                 f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}', summary=f'Do {o.number}: {o.title}')
    if vendor and vendor.email:
        app.send_mail([vendor.email], f'Work order {o.number}', f'Please attend: {o.title}\nWhere: {place(o)}\nAccess: {o.access or "-"}\n{o.description}')
    if o.estimate:
        ask_approval(o, request)
    tell_requester(o, 'Your request is assigned. We will contact you to arrange access.')
    return o


def a_quotes(o, request, f):
    o.status = 'quoting'
    o.responded_at = o.responded_at or now()
    o.save()
    app.event('work_order', o.pk, 'Waiting for vendor quotes', request.user)
    return o


def a_select_quote(o, request, f):
    q = f.obj('quote', Quote, True, 'Quote')
    if q.work_order_id != o.pk:
        raise AppError('Choose a quote of this work order.', 'quote')
    o.quotes.exclude(pk=q.pk).update(status='rejected')
    q.status = 'selected'
    q.save(update_fields=['status'])
    o.vendor, o.estimate, o.status = q.vendor, q.amount, 'assigned'
    o.save()
    app.event('work_order', o.pk, f'Quote {q.number} selected: {q.vendor.name} {cur()} {q.amount}', request.user)
    if not ask_approval(o, request) and q.vendor.email:
        app.send_mail([q.vendor.email], f'Quote accepted – {o.number}', f'Your quote {cur()} {q.amount} for "{o.title}" is accepted. Please schedule the work.')
    return o


def a_estimate(o, request, f):
    o.estimate = f.n('estimate')
    if f.s('who_pays'):
        o.who_pays = f.choice('who_pays', WorkOrder._meta.get_field('who_pays').choices, o.who_pays)
    o.save()
    app.event('work_order', o.pk, f'Estimate {cur()} {o.estimate}, paid by {o.get_who_pays_display()}', request.user)
    ask_approval(o, request)
    return o


def decide(o, approve, note, by, user=None):
    o.approval_note = (f'{by}: ' + (note or ''))[:255]
    if approve:
        o.approved_at = now()
        o.status = 'assigned'
        o.save()
        app.event('work_order', o.pk, f'Approved by {by}' + (f' – {note}' if note else ''), user)
        if o.vendor_id and o.vendor.email:
            app.send_mail([o.vendor.email], f'Approved – {o.number}', f'The work "{o.title}" is approved. Please go ahead.')
        if o.technician_id and o.technician.users_id:
            app.todo(o.technician.users, f'Approved – do {o.number}', 'work_order', o.pk, f'{o.number} {o.title}', PAGE + f'r/work_order/{o.pk}')
    else:
        o.status = 'on_hold'
        o.save()
        app.event('work_order', o.pk, f'Declined by {by}' + (f' – {note}' if note else ''), user)
        app.tell(app.users_with_role('mnt_coordinator'), f'{o.number} declined by {by}', note or '', 'work_order', o.pk, f'{o.number} {o.title}',
                 PAGE + f'r/work_order/{o.pk}', summary=f'Declined: {o.number}')
    app.close_todos('work_order', o.pk, 'Approve')
    return o.status


def a_approve(o, request, f):
    by = user_name(request.user) + (' on behalf of the owner' if o.approval_by == 'owner' else '')
    decide(o, True, f.s('note'), by, request.user)
    return o


def a_decline(o, request, f):
    decide(o, False, f.s('note'), user_name(request.user), request.user)
    return o


def a_status(new, text):
    def run(o, request, f):
        o.status = new
        o.responded_at = o.responded_at or now()
        o.save()
        app.event('work_order', o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        return o
    return run


def a_done(o, request, f):
    o.work_note = f.s('work_note') or o.work_note
    o.status, o.done_at = 'done', now()
    o.cost = total_cost(o)
    o.save()
    if o.asset_id and o.asset.status == 'faulty':
        o.asset.status = 'working'
        o.asset.save(update_fields=['status'])
    app.event('work_order', o.pk, f'Work done – cost {cur()} {o.cost}', request.user)
    app.close_todos('work_order', o.pk)
    tell_requester(o, f'The work on "{o.title}" is done. {o.work_note}')
    app.tell(app.users_with_role('mnt_coordinator'), f'{o.number} done – please close', o.work_note, 'work_order', o.pk, f'{o.number} {o.title}',
             PAGE + f'r/work_order/{o.pk}', summary=f'Check and close {o.number}')
    return o


def a_close(o, request, f):
    o.cost = total_cost(o)
    msg = ''
    if o.who_pays in ('tenant', 'owner') and o.cost > 0 and o.property_unit_id and not o.recharge_bill_id and not f.b('no_recharge'):
        if not linked(app, 'ZeoProperty'):
            msg = ' (Property is not linked – charge the cost manually)'
        else:
            amt = money(o.cost * (1 + Decimal(str(app.settings().markup_percent or 0)) / 100))
            res = call(app, 'ZeoProperty', 'recharge', unit_id=o.property_unit_id, who=o.who_pays, amount=amt, description=o.title,
                       work_order_id=o.pk, number=o.number, user=request.user)
            if res:
                o.recharge_bill_id = res['id']
                msg = f' – billing record {res["number"]} made in Property ({cur()} {amt})'
    o.status, o.closed_at = 'closed', now()
    if f.i('rating'):
        o.rating = max(1, min(5, f.i('rating')))
    o.save()
    app.event('work_order', o.pk, 'Closed' + msg, request.user)
    app.close_todos('work_order', o.pk)
    after_inspection_job(o, request.user)
    return {'message': 'Closed' + msg}


def after_inspection_job(o, user=None):
    """Unit-ready / snag jobs: when the last job of an inspection is closed, Property puts the unit back into use."""
    ins = o.inspection
    if ins and ins.kind == 'unit_ready' and ins.status == 'done' and not ins.work_orders.filter(status__in=OPEN + ('done',)).exists():
        call(app, 'ZeoProperty', 'unit_blocked', ins.property_unit_id, False, f'unit-ready works finished ({ins.number})', user)


def wo_extra(o, request):
    return {'place': place(o), 'cost_total': float(total_cost(o)),
            'sla': {'response_due': d(o.response_due), 'resolve_due': d(o.resolve_due), 'responded_at': d(o.responded_at), 'done_at': d(o.done_at),
                    'response_late': bool(o.response_due and (o.responded_at or now()) > o.response_due),
                    'resolve_late': bool(o.resolve_due and (o.done_at or now()) > o.resolve_due)},
            'links': {'unit': link_label('property.units', o.property_unit_id) if o.property_unit_id else '',
                      'bill': link_label('property.bills', o.recharge_bill_id) if o.recharge_bill_id else ''},
            'tenant': call(app, 'ZeoProperty', 'tenant_of_unit', o.property_unit_id) if o.property_unit_id else None}


def cost_saver(o, request, creating, old):
    o.amount = money((o.qty or 0) * (o.rate or 0))
    o.save()
    wo = o.work_order
    wo.cost = total_cost(wo)
    wo.save(update_fields=['cost'])
    return o


def quote_saver(o, request, creating, old):
    o.save()
    if creating and o.work_order.status == 'new':
        o.work_order.status = 'quoting'
        o.work_order.save(update_fields=['status'])
    return o


# ------------------------------------------------------------------ inspections
def ins_defaults(o, request):
    if not o.checklist_id:
        o.checklist = Checklist.objects.filter(kind=o.kind, is_active=True).exclude(review_status='to_review').first()
    if o.checklist_id and not o.results:
        o.results = [{'item': x, 'ok': None, 'note': '', 'cost': 0} for x in (o.checklist.items or [])]
    if not o.inspector_id:
        o.inspector = app.my_emp(request) if request else None


def ins_saver(o, request, creating, old):
    if old and old.checklist_id != o.checklist_id and o.checklist_id:
        o.results = [{'item': x, 'ok': None, 'note': '', 'cost': 0} for x in (o.checklist.items or [])]
    o.save()
    return o


def a_record(o, request, f):
    """results: [{item, ok, note, cost}] – the inspector's answers (saved; can be changed until completed)."""
    rows = f.j('results') or []
    if not isinstance(rows, list):
        raise AppError('Results are not valid.', 'results')
    clean = []
    for r in rows:
        try:
            cost = float(r.get('cost') or 0)
        except (TypeError, ValueError):
            cost = 0
        clean.append({'item': str(r.get('item', ''))[:200], 'ok': r.get('ok') if r.get('ok') in (True, False) else None,
                      'note': str(r.get('note', ''))[:300], 'cost': cost})
    o.results = clean
    o.save(update_fields=['results'])
    app.event('inspection', o.pk, f'Results recorded: {sum(1 for x in clean if x["ok"] is False)} finding(s)', request.user)
    return o


def a_complete(o, request, f):
    if any(x.get('ok') is None for x in o.results or []):
        raise AppError('Answer every checklist line (OK or not OK) before completing.')
    bad = [x for x in (o.results or []) if x.get('ok') is False]
    o.findings = len(bad)
    o.deductions = money(sum(Decimal(str(x.get('cost') or 0)) for x in bad)) if o.kind == 'move_out' else ZERO
    o.status, o.done_at = 'done', now()
    o.note = f.s('note') or o.note
    o.save()
    made = []
    if f.b('create_jobs') or o.kind in ('unit_ready', 'snag'):
        for x in bad:
            wo = WorkOrder(number=app.next_number('work_order', 'WO'), title=f'{o.get_kind_display()}: {x["item"]}', description=x.get('note', ''),
                           property_unit_id=o.property_unit_id, location=o.location, source='inspection', inspection=o,
                           who_pays='warranty' if o.kind == 'snag' else ('tenant' if o.kind == 'move_out' else 'company'),
                           estimate=Decimal(str(x.get('cost') or 0)) or None, requester=user_name(request.user))
            sla(wo)
            wo.save()
            app.event('work_order', wo.pk, f'Created from inspection {o.number}', request.user)
            made.append(wo.number)
        if made:
            app.tell(app.users_with_role('mnt_coordinator'), f'{len(made)} job(s) from inspection {o.number}', ', '.join(made), 'inspection', o.pk,
                     o.number, PAGE + f'r/inspection/{o.pk}', summary=f'Assign jobs from {o.number}')
    app.event('inspection', o.pk, f'Completed – {o.findings} finding(s)' + (f', deductions {cur()} {o.deductions}' if o.deductions else '')
              + (f', jobs {", ".join(made)}' if made else ''), request.user)
    if o.property_unit_id:
        if o.kind == 'unit_ready' and not made:
            call(app, 'ZeoProperty', 'inspection_done', kind='unit_ready', unit_id=o.property_unit_id, user=request.user)
        elif o.kind == 'move_out':
            text = '; '.join(f'{x["item"]}: {x.get("note") or "not OK"} ({x.get("cost") or 0})' for x in bad)
            call(app, 'ZeoProperty', 'inspection_done', kind='move_out', unit_id=o.property_unit_id, moveout_id=o.property_moveout_id,
                 deductions=o.deductions, findings=o.findings, note=text, user=request.user)
    return {'message': f'Inspection completed: {o.findings} finding(s)' + (f'; {len(made)} work order(s) created.' if made else '.')}


def coded(kind, prefix):
    def saver(o, request, creating, old):
        if not o.code:
            o.code = app.next_number(kind, prefix)
        if type(o).objects.filter(code=o.code).exclude(pk=o.pk).exists():
            raise AppError('This code is already used.', 'code')
        o.save()
        return o
    return saver


# ------------------------------------------------------------------ registers
R = app.reg
R(Reg('work_order', WorkOrder, 'Work order', 'Work orders', 'Work',
      cols=('number', 'title', 'category', 'priority', 'property_unit_id', 'location', 'technician', 'vendor', 'status', 'resolve_due'),
      form=('title', 'description', 'category', 'priority', 'property_unit_id', 'location', 'asset', 'source', 'requester', 'requester_email',
            'requester_phone', 'access', 'who_pays'),
      number=('work_order', 'WO'), manage=COORD, view=VIEW, create='all', own=(('technician', 'emp'),),
      search=('number', 'title', 'requester', 'description'), filters=('status', 'priority', 'category', 'technician', 'vendor', 'source', 'who_pays'),
      title='title', subtitle='category', defaults=wo_defaults, check=wo_check, saver=wo_saver, edit_when=lambda o: o.status in ('new', 'assigned', 'quoting'),
      links={'property_unit_id': 'property.units', 'recharge_bill_id': 'property.bills'}, extra=wo_extra,
      info=('estimate', 'approval_by', 'approval_note', 'approved_at', 'cost', 'responded_at', 'done_at', 'closed_at', 'work_note', 'rating',
            'recharge_bill_id', 'inspection', 'plan'),
      children=(('Quotes', 'quote', 'work_order'), ('Costs', 'cost', 'work_order')),
      actions=[Act('assign', 'Assign', a_assign, when(('new', 'assigned', 'quoting', 'on_hold'), *COORD), style='primary',
                   fields=(('technician', 'Technician', 'opt:employees', False), ('vendor', 'Vendor', 'opt:vendors', False),
                           ('estimate', 'Estimated cost', 'decimal', False),
                           ('who_pays', 'Who pays', 'choice:company=Company|owner=Owner|tenant=Tenant|warranty=Warranty / developer', False))),
               Act('quotes', 'Ask vendors for quotes', a_quotes, when(('new', 'assigned'), *COORD)),
               Act('select_quote', 'Select quote', a_select_quote, lambda o, r: o.status in ('quoting', 'assigned') and o.quotes.exists() and app.any(r, *COORD),
                   fields=(('quote', 'Quote', 'opt:quotes', True),)),
               Act('estimate', 'Estimate / who pays', a_estimate, when(('new', 'assigned', 'on_hold'), *COORD),
                   fields=(('estimate', 'Estimated cost', 'decimal', True),
                           ('who_pays', 'Who pays', 'choice:company=Company|owner=Owner|tenant=Tenant|warranty=Warranty / developer', False))),
               Act('approve', 'Approve', a_approve, when(('approval',), *MGR), style='primary', fields=(('note', 'Note', 'text', False),)),
               Act('decline', 'Decline', a_decline, when(('approval',), *MGR), style='danger', fields=(('note', 'Reason', 'text', True),)),
               Act('start', 'Start work', a_status('in_progress', 'Work started'), coord_or_tech(('assigned', 'on_hold'))),
               Act('hold', 'On hold', a_status('on_hold', 'On hold'), coord_or_tech(('assigned', 'in_progress')), fields=(('note', 'Why', 'text', True),)),
               Act('done', 'Work done', a_done, coord_or_tech(('assigned', 'in_progress')), style='primary', fields=(('work_note', 'What was done', 'textarea', True),)),
               Act('close', 'Close', a_close, when(('done',), *COORD), style='primary',
                   fields=(('rating', 'Requester rating (1–5)', 'int', False), ('no_recharge', 'Do not charge the tenant / owner', 'bool', False))),
               Act('reopen', 'Reopen', a_status('in_progress', 'Reopened'), when(('done',), *COORD), fields=(('note', 'Why', 'text', True),)),
               Act('cancel', 'Cancel', a_status('cancelled', 'Cancelled'), when(OPEN, *COORD), style='danger', fields=(('note', 'Reason', 'text', True),))],
      help='Every repair or job: assign, quote, approve, do, close. Anyone may report a job; technicians see theirs.'))
R(Reg('quote', Quote, 'Quote', 'Quotes', 'Work', cols=('number', 'work_order', 'vendor', 'amount', 'days', 'status'),
      form=('work_order', 'vendor', 'amount', 'days', 'note'), number=('quote', 'QT'), manage=COORD, view=VIEW, child_of='work_order',
      search=('number', 'vendor__name', 'work_order__number'), filters=('status', 'vendor'), subtitle='vendor', saver=quote_saver,
      edit_when=lambda o: o.status == 'received'))
R(Reg('cost', CostLine, 'Cost line', 'Cost lines', 'Work', cols=('work_order', 'kind', 'description', 'qty', 'rate', 'amount', 'vendor_invoice'),
      form=('work_order', 'kind', 'description', 'qty', 'rate', 'vendor_invoice'), manage=COORD, view=VIEW + ('mnt_technician',), create=('mnt_technician',),
      child_of='work_order', search=('description',), filters=('kind',), title='description', subtitle='work_order', saver=cost_saver, files=False,
      edit_when=lambda o: o.work_order.status not in ('closed', 'cancelled')))
R(Reg('inspection', Inspection, 'Inspection', 'Inspections', 'Work',
      cols=('number', 'kind', 'property_unit_id', 'location', 'scheduled_on', 'inspector', 'status', 'findings', 'deductions'),
      form=('kind', 'property_unit_id', 'location', 'property_lease_id', 'reference', 'scheduled_on', 'inspector', 'checklist', 'note'),
      number=('inspection', 'INS'), manage=COORD, view=VIEW, own=(('inspector', 'emp'),), search=('number', 'reference'), filters=('kind', 'status', 'inspector'),
      subtitle='kind', defaults=ins_defaults, saver=ins_saver, edit_when=lambda o: o.status == 'planned',
      links={'property_unit_id': 'property.units', 'property_lease_id': 'property.leases'},
      extra=lambda o, r: {'results': o.results, 'place': place(o)}, info=('done_at', 'property_moveout_id'),
      children=(('Work orders', 'work_order', 'inspection'),),
      actions=[Act('record', 'Record results', a_record, lambda o, r: o.status == 'planned' and (app.any(r, *COORD) or getattr(app.my_emp(r), 'pk', 0) == o.inspector_id),
                   fields=(('results', 'Checklist', 'results', True),)),
               Act('complete', 'Complete', a_complete, lambda o, r: o.status == 'planned' and (app.any(r, *COORD) or getattr(app.my_emp(r), 'pk', 0) == o.inspector_id),
                   style='primary', fields=(('create_jobs', 'Create work orders for the findings', 'bool', False), ('note', 'Summary', 'textarea', False))),
               Act('cancel', 'Cancel', lambda o, r, f: (setattr(o, 'status', 'cancelled'), o.save(), o)[-1], when(('planned',), *COORD), style='danger')],
      help='Move-in, move-out, unit-ready, snag and routine inspections with a checklist.'))
R(Reg('plan', PreventivePlan, 'Preventive plan', 'Preventive plans', 'Planning',
      cols=('number', 'name', 'asset', 'location', 'property_unit_id', 'every', 'unit', 'next_due', 'technician', 'is_active'),
      form=('name', 'category', 'asset', 'location', 'property_unit_id', 'every', 'unit', 'next_due', 'lead_days', 'technician', 'vendor', 'checklist', 'is_active'),
      number=('plan', 'PM'), manage=COORD, view=VIEW + ('mnt_technician',), search=('number', 'name'), filters=('is_active', 'technician'), title='name',
      links={'property_unit_id': 'property.units'}, children=(('Work orders', 'work_order', 'plan'),),
      help='Jobs that repeat (AC service, fire alarm test …). A work order is created automatically before each due date.'))
R(Reg('asset', Asset, 'Asset', 'Assets', 'Planning', cols=('code', 'name', 'category', 'location', 'property_unit_id', 'warranty_until', 'status'),
      form=('code', 'name', 'category', 'location', 'property_unit_id', 'make', 'model_no', 'serial', 'installed_on', 'warranty_until', 'status'),
      manage=COORD, view=STAFF, search=('code', 'name', 'serial'), filters=('status', 'category', 'location'), title='name', subtitle='code',
      links={'property_unit_id': 'property.units'}, children=(('Work orders', 'work_order', 'asset'),),
      help='Equipment you maintain (AC units, pumps, lifts, generators …).'))
R(Reg('location', Location, 'Location', 'Locations', 'Planning', cols=('code', 'name', 'parent', 'property_unit_id', 'property_building_id', 'is_active'),
      form=('code', 'name', 'parent', 'property_building_id', 'property_unit_id', 'notes', 'is_active'), manage=COORD, view=STAFF,
      search=('code', 'name'), filters=('is_active',), title='name', subtitle='code', saver=coded('location', 'LOC'), links={'property_unit_id': 'property.units', 'property_building_id': 'property.buildings'},
      help='Places to maintain. With ZEO Property, work orders can use the Property units directly.'))
R(Reg('vendor', Vendor, 'Vendor', 'Vendors', 'Planning', cols=('code', 'name', 'trade', 'phone', 'email', 'licence_expiry', 'rating', 'is_active'),
      form=('code', 'name', 'trade', 'email', 'phone', 'licence_expiry', 'rating', 'is_active'), manage=COORD, view=STAFF,
      search=('code', 'name', 'trade'), filters=('is_active', 'trade'), title='name', subtitle='trade', saver=coded('vendor', 'VEN'),
      help='Contractors and suppliers.'))
for key, model, label, plural, cols in (('category', Category, 'Category', 'Categories', ('code', 'name', 'trade', 'priority', 'response_hours', 'resolve_hours', 'who_pays', 'is_active')),
                                        ('checklist', Checklist, 'Checklist', 'Checklists', ('code', 'name', 'kind', 'items', 'is_active'))):
    R(Reg(key, model, label, plural, 'Setup', cols=cols + ('review_status',), form=cols, manage=MGR, view=STAFF, search=('code', 'name'),
          filters=('is_active', 'review_status'), title='name', review=True, deletable=True, files=False))


# ------------------------------------------------------------------ hooks other apps call (only while linked)
def h_request(*, title, description='', unit_id=None, requester='', email='', phone='', source='staff', ref='', user=None, files=(), snag=False, access='', branch_id=None,
              asset_id=None):
    cat = None
    if snag:
        cat = Category.objects.filter(code='SNAG', is_active=True).first()
    wo = WorkOrder(number=app.next_number('work_order', 'WO'), title=title[:200], description=(description + (f'\n\nRef: {ref}' if ref else '')).strip(),
                   property_unit_id=unit_id, requester=requester[:150], requester_email=email or '', requester_phone=phone or '', source=source,
                   category=cat, access=access[:200], who_pays='warranty' if snag else 'company')
    if cat:
        wo.priority = cat.priority
    if asset_id:   # v1.33.0: fault reported from Asset Management
        a = Asset.objects.filter(pk=asset_id).first()
        if a is not None:
            wo.asset, wo.location = a, a.location
            wo.property_unit_id = wo.property_unit_id or a.property_unit_id
            if a.status == 'working':
                a.status = 'faulty'
                a.save(update_fields=['status'])
    if branch_id:   # v1.22.0: the branch of the record that asked (otherwise taken from the unit by the pre-save signal)
        wo.branch_id = branch_id
    sla(wo)
    wo.save()
    for x in files or []:
        MaintFile.objects.create(model='work_order', object_id=wo.pk, file=x, name=x.name[:255], size=x.size or 0, uploaded_by=user, portal=True)
    app.event('work_order', wo.pk, f'Received from {dict(WorkOrder._meta.get_field("source").choices).get(source, source)}' + (f' ({ref})' if ref else ''), user)
    announce(wo)
    return {'id': wo.pk, 'number': wo.number}


def wo_public(o):
    return {'id': o.pk, 'number': o.number, 'title': o.title, 'status': o.get_status_display(), 'status_code': o.status, 'created': d(o.created_at),
            'done': d(o.done_at), 'unit_id': o.property_unit_id, 'place': place(o), 'estimate': float(o.estimate) if o.estimate else None,
            'who_pays': o.get_who_pays_display(), 'approval_by': o.approval_by}


def h_requests_for(unit_ids=(), email=''):
    qs = WorkOrder.objects.filter(property_unit_id__in=list(unit_ids or []))
    if email:
        qs = qs | WorkOrder.objects.filter(requester_email__iexact=email)
    return [wo_public(o) for o in qs.distinct().order_by('-created_at')[:50]]


def h_owner_pending(unit_ids=()):
    return [dict(wo_public(o), description=o.description, vendor=o.vendor.name if o.vendor_id else '')
            for o in WorkOrder.objects.filter(property_unit_id__in=list(unit_ids or []), status='approval', approval_by='owner')]


def h_owner_decision(*, work_order_id, approve, note, by, unit_ids):
    o = WorkOrder.objects.filter(pk=work_order_id, property_unit_id__in=list(unit_ids), status='approval', approval_by='owner').first()
    if o is None:
        raise AppError('This job is not waiting for your approval.')
    return decide(o, approve, note, by)


def h_inspection(*, kind, unit_id, lease_id=None, date=None, ref='', note='', moveout_id=None):
    o = Inspection(number=app.next_number('inspection', 'INS'), kind=kind, property_unit_id=unit_id, property_lease_id=lease_id,
                   property_moveout_id=moveout_id, scheduled_on=date or today(), reference=ref[:80], note=note)
    ins_defaults(o, None)
    o.save()
    app.event('inspection', o.pk, f'Requested by Property ({ref})', None)
    app.tell(app.users_with_role('mnt_coordinator'), f'{o.get_kind_display()} inspection {o.number}', f'{place(o)} on {d(o.scheduled_on)}. {note}',
             'inspection', o.pk, o.number, PAGE + f'r/inspection/{o.pk}', summary=f'Plan {o.get_kind_display().lower()} inspection {o.number}')
    return {'id': o.pk, 'number': o.number}


def h_unit_work_orders(unit_id):
    return [wo_public(o) for o in WorkOrder.objects.filter(property_unit_id=unit_id).order_by('-created_at')[:20]]


def h_asset_upsert(*, mnt_id=None, code='', title='', serial='', make='', model_no='', warranty_until=None, status='working', branch_id=None,
                   property_unit_id=None, location=''):
    """v1.33.0: Asset Management keeps its assets here (same code); returns {'id'}."""
    a = Asset.objects.filter(pk=mnt_id).first() if mnt_id else None
    a = a or (Asset.objects.filter(code=code).first() if code else None) or Asset(code=code or app.next_number('asset', 'AS'))
    a.name, a.serial, a.make, a.model_no = title[:150], (serial or '')[:80], (make or '')[:80], (model_no or '')[:80]
    a.warranty_until = warranty_until
    if status == 'retired' or a.status == 'retired' or not a.pk:
        a.status = status
    if property_unit_id:
        a.property_unit_id = property_unit_id
    if branch_id:
        a.branch_id = branch_id
    if location and not a.location_id:
        a.location = Location.objects.filter(name__iexact=location.split(' › ')[-1]).first()
    a.save()
    return {'id': a.pk, 'code': a.code}


def h_asset_work_orders(asset_id):
    return [wo_public(o) for o in WorkOrder.objects.filter(asset_id=asset_id).order_by('-created_at')[:20]]


def h_work_order_status(work_order_id):
    """v1.34.0: Asset Management follows the work orders it handed over."""
    o = WorkOrder.objects.filter(pk=work_order_id).first()
    if o is None:
        return None
    return dict(wo_public(o), cost=float(total_cost(o) or o.cost or 0), work_note=o.work_note or '')


app.hooks.update({'asset_upsert': h_asset_upsert, 'asset_work_orders': h_asset_work_orders, 'work_order_status': h_work_order_status})
app.hooks.update({'request': h_request, 'requests_for': h_requests_for, 'owner_pending': h_owner_pending, 'owner_decision': h_owner_decision,
                  'inspection': h_inspection, 'unit_work_orders': h_unit_work_orders})


# ------------------------------------------------------------------ dashboard, my work, daily
def dashboard(request):
    wos = app.regs['work_order'].scope(request)
    open_ = wos.filter(status__in=OPEN)
    t = today()
    late = open_.filter(resolve_due__lt=now())
    frm, to = dash_period(request)   # v1.22.0 period of the dashboard
    month = wos.filter(closed_at__date__gte=frm, closed_at__date__lte=to)
    ontime = month.filter(done_at__lte=F('resolve_due')).count() if month.exists() else 0
    by_status = dict(open_.values_list('status').annotate(n=Count('id')))
    cats = open_.values('category__name').annotate(n=Count('id')).order_by('-n')[:8]
    return {'cards': [{'label': 'Open work orders', 'value': open_.count(), 'link': 'r/work_order?open=1'},
                      {'label': 'Past fix time', 'value': late.count(), 'tone': 'bad' if late.exists() else 'ok'},
                      {'label': 'Waiting for approval', 'value': by_status.get('approval', 0), 'link': 'r/work_order?status=approval'},
                      {'label': 'Closed in period', 'value': month.count(), 'link': f'r/work_order?date_field=closed_at&from={frm}&to={to}', 'hint': f'{round(100 * ontime / month.count()) if month.count() else 0}% on time'},
                      {'label': f'Cost in period ({cur()})', 'value': f'{float(month.aggregate(s=Sum("cost"))["s"] or 0):,.0f}'},
                      {'label': 'Inspections planned', 'value': in_branches(Inspection.objects.all(), request).filter(status='planned').count(), 'link': 'r/inspection?status=planned'}],
            'bars': [{'title': 'Open by status', 'items': [{'label': lbl, 'value': by_status.get(k, 0)} for k, lbl in WorkOrder._meta.get_field('status').choices if k in OPEN]},
                     {'title': 'Open by category', 'items': [{'label': x['category__name'] or 'No category', 'value': x['n']} for x in cats]}],
            'lists': [{'title': 'Past fix time', 'kind': 'work_order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.title}', 'sub': place(o), 'badge': o.get_priority_display()}
                                                                             for o in late.order_by('resolve_due')[:10]]},
                      {'title': 'Preventive jobs due', 'kind': 'plan', 'rows': [{'id': p.pk, 'title': f'{p.number} {p.name}', 'sub': emp_name(p.technician), 'badge': d(p.next_due)}
                                                                                for p in in_branches(PreventivePlan.objects.all(), request).filter(is_active=True, next_due__lte=t + timedelta(days=14))[:10]]}]}


def my_work(request):
    me = app.my_emp(request)
    out = []
    if me is not None:
        out.append({'title': 'My jobs', 'kind': 'work_order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.title}', 'sub': place(o), 'badge': o.get_status_display()}
                                                                        for o in in_branches(WorkOrder.objects.all(), request).filter(technician=me, status__in=OPEN)[:30]]})
        out.append({'title': 'My inspections', 'kind': 'inspection', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.get_kind_display()}', 'sub': place(o), 'badge': d(o.scheduled_on)}
                                                                              for o in in_branches(Inspection.objects.all(), request).filter(inspector=me, status='planned')[:30]]})
    if app.any(request, *COORD):
        out.append({'title': 'New – to assign', 'kind': 'work_order', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.title}', 'sub': place(o), 'badge': o.get_priority_display()}
                                                                               for o in in_branches(WorkOrder.objects.all(), request).filter(status='new')[:30]]})
    return out


def step(d0, every, unit):
    if unit == 'days':
        return d0 + timedelta(days=every)
    if unit == 'weeks':
        return d0 + timedelta(weeks=every)
    return add_months(d0, every)


def daily():
    t = today()
    out = {'preventive_jobs': 0, 'late': 0}
    for p in PreventivePlan.objects.filter(is_active=True, next_due__lte=t + timedelta(days=60)):
        guard_n = 0
        while p.next_due - timedelta(days=p.lead_days) <= t and guard_n < 12:
            guard_n += 1
            if not p.work_orders.filter(status__in=OPEN, title__endswith=d(p.next_due)).exists():
                wo = WorkOrder(number=app.next_number('work_order', 'WO'), title=f'{p.name} – due {d(p.next_due)}', category=p.category, asset=p.asset,
                               location=p.location, property_unit_id=p.property_unit_id, source='preventive', plan=p, technician=p.technician, vendor=p.vendor,
                               status='assigned' if (p.technician_id or p.vendor_id) else 'new', requester='Preventive plan')
                sla(wo)
                wo.resolve_due = max(wo.resolve_due, now().replace(year=p.next_due.year, month=p.next_due.month, day=p.next_due.day))
                wo.save()
                app.event('work_order', wo.pk, f'Created by preventive plan {p.number}', None)
                if p.technician_id and p.technician.users_id:
                    app.todo(p.technician.users, f'Preventive: {p.name}', 'work_order', wo.pk, f'{wo.number} {wo.title}', PAGE + f'r/work_order/{wo.pk}')
                out['preventive_jobs'] += 1
            p.next_due = step(p.next_due, p.every, p.unit)
            p.save(update_fields=['next_due'])
    late = WorkOrder.objects.filter(status__in=OPEN, resolve_due__lt=now())
    out['late'] = late.count()
    if out['late']:
        for u in app.users_with_role('mnt_coordinator'):
            app.todo(u, f'{out["late"]} work order(s) past fix time', 'work_order', 0, 'Work orders', PAGE + 'r/work_order?open=1')
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily


# v1.42.0 business partners: partner picker and checks on vendors
from ZeoMasters.extensions import attach as _attach_partners  # noqa: E402
_attach_partners(app)
