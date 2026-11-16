"""v1.67.13 (support planner) – plan support jobs by day and person.

* Support managers (assign_ticket) see every person's plan and allocate: a new job creates a ticket (customer, site,
  equipment, kind, priority) assigned to the person; an open ticket can be allocated too. The person is notified
  (e-mail and To-do, as the "Ticket assigned to me" notification setting says) with the date, time and instructions.
* The allocated person sees their own jobs and opens a job page with everything they need – customer and contacts,
  site (address, GPS, access notes, working hours), equipment (warranty, earlier tickets), AMC coverage and the ticket
  history – even without customer / AMC rights, until the ticket is resolved or closed. After that the job is history.
"""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.utils.timezone import now
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import rows as RW
from . import services as S
from .models import (DONE_STAGES, OPEN_STAGES, CustomerEquipment, CustomerSite, Customer, SupportAllocation, SupportTeam, Ticket)
from .views import Base, In, guard

PAGE = '/main-sidebar/zeo-projects/my-support-jobs'   # the allocated person's page (no planner rights needed)


def _emp(pk):
    from EmpManagement.models import emp_master
    return emp_master.objects.filter(pk=pk).first()


def people():
    """Who can be allocated: members and leads of support teams, and employees with a login already assigned tickets."""
    from EmpManagement.models import emp_master
    ids = set()
    for t in SupportTeam.objects.filter(is_active=True).prefetch_related('members'):
        ids |= {m.pk for m in t.members.all()}
        if t.lead_id:
            ids.add(t.lead_id)
    ids |= set(Ticket.objects.exclude(assigned_to=None).values_list('assigned_to', flat=True).distinct()[:500])
    out = []
    for e in emp_master.objects.filter(pk__in=ids).order_by('emp_first_name'):
        out.append({'id': e.pk, 'name': S.emp_name(e), 'has_login': bool(getattr(e, 'users_id', None))})
    return out


def sync_status(a):
    """The job follows its ticket: in progress / done / cancelled; access ends when the ticket is resolved or closed."""
    t = a.ticket
    new = a.status
    if t.stage in DONE_STAGES:
        new = 'done'
    elif t.stage == 'cancelled':
        new = 'cancelled'
    elif t.stage == 'in_progress':
        new = 'in_progress'
    if new != a.status or (new in ('done', 'cancelled') and a.access_until is None):
        a.status = new
        if new in ('done', 'cancelled') and a.access_until is None:
            a.access_until = now()
        a.save(update_fields=['status', 'access_until'])
    return a


def alloc_row(a):
    t = a.ticket
    return {'id': a.pk, 'ticket': t.pk, 'ticket_number': t.number, 'subject': t.subject, 'stage': t.stage, 'stage_label': t.get_stage_display(),
            'priority': t.priority, 'priority_label': t.get_priority_display(), 'kind': t.ticket_kind, 'kind_label': t.get_ticket_kind_display(),
            'customer_name': t.customer.name if t.customer_id else 'Internal', 'site_name': t.site.name if t.site_id else '',
            'site_city': t.site.city if t.site_id else '', 'equipment_name': t.equipment.name if t.equipment_id else '',
            'employee': a.employee_id, 'employee_name': S.emp_name(a.employee), 'planned_date': str(a.planned_date),
            'start_time': a.start_time.strftime('%H:%M') if a.start_time else '', 'hours': str(a.hours), 'instructions': a.instructions,
            'status': a.status, 'status_label': a.get_status_display(), 'open': a.access_until is None,
            'coverage_kind': t.coverage_kind, 'notified_at': RW.d(a.notified_at)}


def _parse_time(v):
    if not v:
        return None
    try:
        h, m = str(v).split(':')[:2]
        from datetime import time
        return time(int(h), int(m))
    except (ValueError, TypeError):
        raise S.ProjectsError('Use HH:MM for the start time.', 'start_time')


def _hours(v):
    try:
        h = Decimal(str(v if v not in (None, '') else '2'))
    except (InvalidOperation, ValueError):
        raise S.ProjectsError('Hours must be a number.', 'hours')
    if h <= 0 or h > 24:
        raise S.ProjectsError('Hours must be between 0 and 24.', 'hours')
    return h


def _notify(a, user, again=False):
    t = a.ticket
    when = a.planned_date.strftime('%d/%m/%Y') + (f' {a.start_time:%H:%M}' if a.start_time else '')
    site = ''
    if t.site_id:
        s = t.site
        site = f'\nSite: {s.name}, {", ".join(x for x in (s.address, s.area, s.city) if x)}' + (f'\nAccess: {s.access_notes}' if s.access_notes else '')
    text = (f'Job planned for you on {when} ({a.hours} h).\n{S.ticket_text(t)}{site}'
            + (f'\n\nInstructions: {a.instructions}' if a.instructions else '')
            + f'\n\nOpen the job: {PAGE}?job={a.pk}')
    S.close_todos(t, f'Support job {t.number}')
    S.notify_emp(a.employee, f'[{t.number}] Support job {"changed" if again else "planned for you"} – {when}', text, t, f'{PAGE}?job={a.pk}',
                 f'Support job {t.number} – {when}', event='ticket_assigned')
    try:   # the to-do is due on the job's day
        from Chatter.models import Activity
        Activity.objects.filter(model='ZeoProjects.ticket', object_id=str(t.pk), state='open', summary__startswith=f'Support job {t.number}') \
            .update(due_date=a.planned_date)
    except Exception:   # noqa: BLE001
        pass
    a.notified_at = now()
    a.save(update_fields=['notified_at'])


@transaction.atomic
def allocate(user, d):
    e = _emp(d.get('employee'))
    if e is None:
        raise S.ProjectsError('Choose the person.', 'employee')
    if not d.get('planned_date'):
        raise S.ProjectsError('Choose the date.', 'planned_date')
    day = date.fromisoformat(str(d['planned_date'])[:10])
    if d.get('ticket'):
        t = Ticket.objects.filter(pk=d['ticket']).first()
        if t is None:
            raise S.ProjectsError('Ticket not found.', 'ticket')
        if t.stage in DONE_STAGES or t.stage == 'cancelled':
            raise S.ProjectsError('This ticket is closed.', 'ticket')
    else:
        cust = Customer.objects.filter(pk=d.get('customer')).first() if d.get('customer') else None
        site = CustomerSite.objects.filter(pk=d.get('site')).first() if d.get('site') else None
        eq = CustomerEquipment.objects.filter(pk=d.get('equipment')).first() if d.get('equipment') else None
        if cust is None and site is not None:
            cust = site.customer
        if cust is None and eq is not None:
            cust = eq.customer
        t = S.create_ticket(subject=d.get('subject') or '', description=d.get('description') or '', user=user, customer=cust,
                            priority=d.get('priority') or 'medium', site=site, equipment=eq, ticket_kind=d.get('kind') or 'visit',
                            channel='manual')
    a = SupportAllocation.objects.create(ticket=t, employee=e, planned_date=day, start_time=_parse_time(d.get('start_time')),
                                         hours=_hours(d.get('hours')), instructions=(d.get('instructions') or '').strip(),
                                         created_by=user if getattr(user, 'pk', None) else None)
    if t.assigned_to_id != e.pk:
        S.assign(t, e, user=user, notify=False)
    S.event(t, 'note', f'Support job planned for {S.emp_name(e)} on {day:%d/%m/%Y}' + (f' {a.start_time:%H:%M}' if a.start_time else ''), user, public=False)
    _notify(a, user)
    return a


def update(a, user, d):
    changed = False
    if d.get('employee') and int(d['employee']) != a.employee_id:
        e = _emp(d['employee'])
        if e is None:
            raise S.ProjectsError('Choose the person.', 'employee')
        S.close_todos(a.ticket, f'Support job {a.ticket.number}')
        a.employee = e
        S.assign(a.ticket, e, user=user, notify=False)
        changed = True
    if d.get('planned_date'):
        nd = date.fromisoformat(str(d['planned_date'])[:10])
        changed = changed or nd != a.planned_date
        a.planned_date = nd
    if 'start_time' in d:
        a.start_time = _parse_time(d.get('start_time'))
        changed = True
    if 'hours' in d:
        a.hours = _hours(d.get('hours'))
    if 'instructions' in d:
        a.instructions = (d.get('instructions') or '').strip()
    if d.get('status') == 'cancelled':
        a.status = 'cancelled'
        a.access_until = now()
        S.close_todos(a.ticket, f'Support job {a.ticket.number}')
    a.save()
    if changed and a.status not in ('done', 'cancelled'):
        _notify(a, user, again=True)
    return a


def can_open_job(request, a):
    """Managers always; the allocated person while the job is open (and afterwards read-only history of their own job)."""
    if A.can(request, 'ticket_manage'):
        return True
    e = A.my_emp(request)
    return e is not None and a.employee_id == e.pk


def job_pack(a):
    """Everything the allocated person needs for the job."""
    t = a.ticket
    r = {'allocation': alloc_row(a), 'ticket': RW.ticket_row(t)}
    r['ticket']['description'] = t.description
    c = t.customer
    if c is not None:
        r['customer'] = {'id': c.pk, 'name': c.name, 'phone': c.phone, 'email': c.email, 'address': c.address, 'city': c.city,
                         'contacts': [{'name': x.name, 'phone': getattr(x, 'phone', ''), 'email': x.email, 'role': x.designation}
                                      for x in c.contacts.filter(is_active=True)[:20]]}
    if t.site_id:
        r['site'] = RW.site_row(t.site)
    if t.equipment_id:
        r['equipment'] = RW.equipment_row(t.equipment)
        r['equipment_history'] = [{'number': x.number, 'subject': x.subject, 'stage_label': x.get_stage_display(), 'created_at': RW.d(x.created_at)}
                                  for x in Ticket.objects.filter(equipment_id=t.equipment_id).exclude(pk=t.pk).order_by('-created_at')[:15]]
    if t.contract_id:
        k = t.contract
        r['contract'] = {'number': k.number, 'title': getattr(k, 'title', ''), 'status': k.status, 'end_date': str(k.end_date),
                         'type': k.amc_type.name if getattr(k, 'amc_type_id', None) else k.get_contract_type_display() if hasattr(k, 'get_contract_type_display') else ''}
    r['coverage'] = {'kind': t.coverage_kind, 'note': t.coverage_note, 'billable': t.get_billable_status_display(),
                     'parts_chargeable': getattr(t, 'parts_chargeable', False)}
    r['events'] = [{'kind': ev.kind, 'text': ev.text, 'user': ev.user_name, 'at': RW.d(ev.at)}
                   for ev in t.events.order_by('-id')[:50]] if hasattr(t, 'events') else []
    r['open_jobs_same_site'] = [alloc_row(x) for x in SupportAllocation.objects.filter(ticket__site_id=t.site_id, access_until__isnull=True)
                                .exclude(pk=a.pk).select_related('ticket', 'employee')[:10]] if t.site_id else []
    r['read_only'] = a.access_until is not None
    return r


# ------------------------------------------------------------------ views
class PlannerView(Base):
    """GET ?from=&to=&employee=&mine=1 → people, allocations, unallocated open tickets, hours per person per day.
    POST {employee, planned_date, start_time, hours, instructions, ticket | customer, site, equipment, subject, kind, priority}"""

    @guard
    def get(self, request):
        q = request.query_params
        manager = A.can(request, 'ticket_manage')
        me = A.my_emp(request)
        if not manager and me is None:
            raise A.Denied('The support planner is for support staff.')
        start = date.fromisoformat(q['from']) if q.get('from') else S.local_today() - timedelta(days=S.local_today().weekday())
        end = date.fromisoformat(q['to']) if q.get('to') else start + timedelta(days=6)
        if (end - start).days > 62:
            raise S.ProjectsError('Show at most two months.', 'to')
        qs = SupportAllocation.objects.select_related('ticket', 'ticket__customer', 'ticket__site', 'ticket__equipment', 'employee') \
            .filter(planned_date__gte=start, planned_date__lte=end)
        if not manager or q.get('mine'):
            qs = qs.filter(employee=me)
        elif q.get('employee'):
            qs = qs.filter(employee_id=q['employee'])
        allocs = [alloc_row(sync_status(a)) for a in qs[:2000]]
        load = {}
        for x in allocs:
            if x['status'] != 'cancelled':
                k = f"{x['employee']}|{x['planned_date']}"
                load[k] = str(Decimal(load.get(k, '0')) + Decimal(x['hours']))
        out = {'from': str(start), 'to': str(end), 'allocations': allocs, 'load': load, 'manager': manager,
               'me': me.pk if me else None}
        if manager:
            out['people'] = people()
            planned = SupportAllocation.objects.filter(access_until__isnull=True).exclude(status='cancelled').values_list('ticket_id', flat=True)
            un = A.ticket_scope(request, Ticket.objects.filter(stage__in=OPEN_STAGES)).exclude(pk__in=planned) \
                .select_related('customer', 'site', 'assigned_to').order_by('resolution_due', 'id')[:200]
            out['unallocated'] = [{'id': t.pk, 'number': t.number, 'subject': t.subject, 'customer_name': t.customer.name if t.customer_id else 'Internal',
                                   'site_name': t.site.name if t.site_id else '', 'priority': t.priority, 'priority_label': t.get_priority_display(),
                                   'assigned_name': S.emp_name(t.assigned_to) if t.assigned_to_id else '', 'stage_label': t.get_stage_display(),
                                   'resolution_due': RW.d(t.resolution_due)} for t in un]
        else:
            out['people'] = [{'id': me.pk, 'name': S.emp_name(me), 'has_login': True}]
        return Response(out)

    @guard
    def post(self, request):
        A.need(request, 'ticket_manage', 'Only support managers allocate jobs.')
        a = allocate(request.user, dict(In(request).d))
        return Response(alloc_row(a), status=status.HTTP_201_CREATED)


def _alloc_related(pk):
    """v1.67.14: a new job's ticket gets the data workbook's @date too."""
    a = SupportAllocation.objects.filter(pk=pk).first()
    return [(SupportAllocation, a.pk), (Ticket, a.ticket_id)] if a else []


class AllocationView(Base):
    zeo_related = staticmethod(_alloc_related)   # v1.67.14
    @guard
    def patch(self, request, pk):
        A.need(request, 'ticket_manage', 'Only support managers change the plan.')
        a = SupportAllocation.objects.select_related('ticket', 'employee').filter(pk=pk).first()
        if a is None:
            raise Http404
        update(a, request.user, dict(In(request).d))
        return Response(alloc_row(a))


class JobView(Base):
    """GET support-planner/<pk>/job/ – the job page of the allocated person (or a manager)."""

    @guard
    def get(self, request, pk):
        a = SupportAllocation.objects.select_related('ticket', 'employee').filter(pk=pk).first()
        if a is None or not can_open_job(request, a):
            raise Http404
        sync_status(a)
        return Response(job_pack(a))

