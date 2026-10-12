"""Customer portal API (/zeo-projects/api/portal/...). Only customer contacts with a portal login; every query is
limited to the contact's own customer (and to their own tickets unless they are a portal manager)."""
from decimal import Decimal

from django.db.models import Q
from django.http import FileResponse, Http404
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import rows as RW
from . import services as S
from .models import (AMCBilling, AMCContract, ChangeRequest, DevRequest, Milestone, PRIORITIES, ProjectProfile, ServiceProduct,
                     Ticket, TicketFile, TaskDetail)
from .views import Base, In, get_or_404, guard


class PortalBase(Base):
    permission_classes = [A.PortalMember]

    def contact(self, request):
        return request.zp_contact


def portal_tickets(contact):
    qs = Ticket.objects.filter(customer=contact.customer)
    if contact.portal_role != 'manager':
        qs = qs.filter(contact=contact)
    return qs.select_related('customer', 'contact', 'product', 'category', 'contract', 'team', 'assigned_to', 'requester_employee')


def portal_ticket(contact, pk):
    t = portal_tickets(contact).filter(pk=pk).first()
    if t is None:
        raise Http404
    return t


def need_approver(c):
    if not c.portal_can_approve:
        raise A.Denied('Your login cannot approve. Ask your company\'s main contact.')


def portal_contract_row(c, s):
    r = RW.contract_row(c)
    for k in ('hourly_rate_outside', 'renewal_status', 'renewal_status_label', 'renewal_owner', 'renewal_quote_ref', 'renewal_value',
              'renewal_note', 'lost_reason', 'notes', 'account_manager'):
        r.pop(k, None)
    r['account_manager_name'] = S.emp_name(c.account_manager) if c.account_manager_id else ''
    if s.portal_show_hours:
        u = S.contract_usage(c)
        r['usage'] = {'unlimited': u['unlimited'], 'included_hours': float(u['included_hours']), 'used_hours': float(u['used_hours']),
                      'remaining_hours': RW.num(u['remaining_hours']), 'used_pct': u['used_pct'],
                      'open_tickets': u['open_tickets'], 'tickets': u['tickets']}
    return r


class PortalMeView(PortalBase):
    @guard
    def get(self, request):
        c = self.contact(request)
        s = S.settings()
        from UserManagement.models import company
        comp = company.objects.filter(schema_name=A.schema_of(request)).first()
        return Response({'contact': {'id': c.id, 'name': c.name, 'email': c.email, 'role': c.portal_role, 'can_approve': c.portal_can_approve},
                         'customer': {'id': c.customer_id, 'name': c.customer.name, 'code': c.customer.code},
                         'company': comp.name if comp else '', 'show_hours': s.portal_show_hours, 'show_invoices': s.portal_show_invoices,
                         'products': [{'id': p.id, 'name': p.name} for p in ServiceProduct.objects.filter(is_active=True)],
                         'priorities': [{'value': k, 'label': v} for k, v in PRIORITIES],
                         'counts': {'open': portal_tickets(c).filter(stage__in=S.OPEN_STAGES).count(),
                                    'waiting_you': portal_tickets(c).filter(stage='waiting_customer').count(),
                                    'approvals': approvals_count(c)}})


def approvals_count(c):
    if not c.portal_can_approve:
        return 0
    pids = list(ProjectProfile.objects.filter(customer=c.customer, portal_visible=True).values_list('project_id', flat=True))
    return (Milestone.objects.filter(project_id__in=pids, status='ready').count()
            + ChangeRequest.objects.filter(project_id__in=pids, status='approved').count()
            + DevRequest.objects.filter(customer=c.customer, status='commercial_approved').count())


class PortalTicketListView(PortalBase):
    @guard
    def get(self, request):
        c = self.contact(request)
        qs = portal_tickets(c)
        q = request.query_params
        if q.get('open') == '1':
            qs = qs.filter(stage__in=S.OPEN_STAGES)
        elif q.get('open') == '0':
            qs = qs.exclude(stage__in=S.OPEN_STAGES)
        if q.get('q'):
            qs = qs.filter(Q(number__icontains=q['q']) | Q(subject__icontains=q['q']))
        return Response([RW.ticket_row(t, staff=False) for t in qs.order_by('-created_at')[:300]])

    @guard
    def post(self, request):
        c = self.contact(request)
        f = In(request)
        t = S.create_ticket(subject=f.s('subject'), description=f.s('description'), user=request.user, channel='portal',
                            customer=c.customer, contact=c, product=f.obj('product', ServiceProduct),
                            priority=f.choice('priority', PRIORITIES, 'medium'), files=f.filelist())
        return Response(RW.ticket_detail(t, staff=False, show_internal=False), status=status.HTTP_201_CREATED)


class PortalTicketDetailView(PortalBase):
    @guard
    def get(self, request, pk):
        t = portal_ticket(self.contact(request), pk)
        r = RW.ticket_detail(t, staff=False, show_internal=False)
        r['assigned_name'] = S.emp_name(t.assigned_to) if t.assigned_to_id else ''
        r['can_reopen'] = t.stage in ('resolved', 'closed')
        r['can_confirm'] = t.stage == 'resolved'
        r['can_rate'] = t.stage in ('resolved', 'closed') and t.satisfaction is None
        return Response(r)

    @guard
    def post(self, request, pk):
        """action: reply | reopen | confirm (close a resolved ticket) | rate"""
        c = self.contact(request)
        t = portal_ticket(c, pk)
        f = In(request)
        action = f.s('action') or 'reply'
        if action == 'reply':
            S.add_message(t, f.s('body'), user=request.user, contact=c, via='portal', files=f.filelist())
        elif action == 'reopen':
            if t.stage not in ('resolved', 'closed'):
                raise S.ProjectsError('Only a resolved or closed ticket can be reopened.')
            if f.s('body'):
                S.add_message(t, f.s('body'), user=request.user, contact=c, via='portal')
            if t.stage in ('resolved', 'closed'):
                S.set_stage(t, 'reopened', request.user, f.s('body') or 'Reopened by the customer', by_customer=True)
        elif action == 'confirm':
            if t.stage != 'resolved':
                raise S.ProjectsError('Only a resolved ticket can be confirmed.')
            S.set_stage(t, 'closed', request.user, 'Confirmed by the customer', by_customer=True)
        elif action == 'rate':
            score = f.i('satisfaction', required=True, label='Rating')
            if not 1 <= score <= 5:
                raise S.ProjectsError('Rating must be 1 to 5.', 'satisfaction')
            t.satisfaction = score
            t.satisfaction_note = f.s('note')
            t.save(update_fields=['satisfaction', 'satisfaction_note'])
            S.event(t, 'rating', f'Customer rated {score}/5', request.user)
        else:
            raise S.ProjectsError('Unknown action.')
        return self.get(request, pk)


class PortalFileView(PortalBase):
    @guard
    def get(self, request, pk):
        f = get_or_404(TicketFile, pk)
        portal_ticket(self.contact(request), f.ticket_id)
        if f.is_internal:
            raise Http404
        return FileResponse(f.file.open('rb'), as_attachment=True, filename=f.name)


class PortalContractsView(PortalBase):
    @guard
    def get(self, request):
        c = self.contact(request)
        s = S.settings()
        qs = AMCContract.objects.filter(customer=c.customer).exclude(status__in=('draft', 'cancelled')).select_related(
            'customer', 'sla_policy', 'account_manager', 'previous').prefetch_related('products')
        out = []
        for x in qs:
            S.refresh_contract_status(x)
            out.append(portal_contract_row(x, s))
        return Response(out)


class PortalProjectsView(PortalBase):
    @guard
    def get(self, request):
        c = self.contact(request)
        from ProjectManagement.models import Project, Task
        profs = {p.project_id: p for p in ProjectProfile.objects.filter(customer=c.customer, portal_visible=True)}
        out = []
        for p in Project.objects.filter(pk__in=list(profs.keys())).exclude(status='cancelled').order_by('-start_date'):
            ms = list(Milestone.objects.filter(project_id=p.pk))
            tasks = Task.objects.filter(project=p)
            total, done = tasks.count(), tasks.filter(status='completed').count()
            approvals = TaskDetail.objects.filter(task_id__in=tasks.values('id'), customer_approval=True, customer_approved_at__isnull=True)
            task_titles = dict(tasks.values_list('id', 'title'))
            out.append({
                'id': p.pk, 'number': profs[p.pk].number, 'title': p.title, 'status': p.get_status_display(),
                'start_date': RW.d(p.start_date), 'end_date': RW.d(p.end_date), 'type': profs[p.pk].get_project_type_display(),
                'progress_pct': round(done * 100 / total) if total else 0, 'tasks_total': total, 'tasks_done': done,
                'health': S.project_health(p, ms),
                'milestones': [{k: v for k, v in RW.milestone_row(m).items() if k not in ('invoice_no',) or c.portal_can_approve} for m in ms],
                'change_requests': [RW.cr_row(x) for x in ChangeRequest.objects.filter(project_id=p.pk).exclude(status__in=('draft', 'submitted'))],
                'task_approvals': [{'task_id': d.task_id, 'title': task_titles.get(d.task_id, ''), 'acceptance_criteria': d.acceptance_criteria}
                                   for d in approvals],
            })
        return Response(out)


class PortalApprovalsView(PortalBase):
    """GET → what waits for this contact; POST {kind: milestone|change|dev|task, id, approve, note}."""

    @guard
    def get(self, request):
        c = self.contact(request)
        pids = list(ProjectProfile.objects.filter(customer=c.customer, portal_visible=True).values_list('project_id', flat=True))
        from ProjectManagement.models import Project
        titles = dict(Project.objects.filter(pk__in=pids).values_list('id', 'title'))
        return Response({
            'can_approve': c.portal_can_approve,
            'milestones': [dict(RW.milestone_row(m), project_title=titles.get(m.project_id, '')) for m in Milestone.objects.filter(project_id__in=pids, status='ready')],
            'change_requests': [dict(RW.cr_row(x), project_title=titles.get(x.project_id, '')) for x in ChangeRequest.objects.filter(project_id__in=pids, status='approved')],
            'dev_requests': [{k: v for k, v in RW.dev_row(d).items() if k not in ('tech_note', 'commercial_note', 'requested_by', 'tech_by', 'commercial_by')}
                             for d in DevRequest.objects.filter(customer=c.customer, status='commercial_approved').select_related('ticket', 'customer')],
        })

    @guard
    def post(self, request):
        c = self.contact(request)
        need_approver(c)
        f = In(request)
        kind, pk, approve, note = f.s('kind'), f.i('id', required=True), f.b('approve', True), f.s('note')
        pids = set(ProjectProfile.objects.filter(customer=c.customer, portal_visible=True).values_list('project_id', flat=True))
        who = f'{c.name} ({c.customer.name})'
        if kind == 'milestone':
            m = get_or_404(Milestone, pk)
            if m.project_id not in pids:
                raise Http404
            if not approve and not note:
                raise S.ProjectsError('Tell us why you reject the milestone.', 'note')
            S.milestone_status(m, None, 'accepted' if approve else 'rejected', note, customer_name=who)
        elif kind == 'change':
            x = get_or_404(ChangeRequest, pk)
            if x.project_id not in pids:
                raise Http404
            S.decide_change_request(x, None, 'customer', approve, note, customer_name=who)
        elif kind == 'dev':
            d = get_or_404(DevRequest, pk)
            if d.customer_id != c.customer_id:
                raise Http404
            S.decide_dev(d, None, 'customer', approve, note, customer_name=who)
        elif kind == 'task':
            from ProjectManagement.models import Task
            t = Task.objects.filter(pk=pk, project_id__in=pids).first()
            d = TaskDetail.objects.filter(task_id=pk, customer_approval=True).first()
            if t is None or d is None:
                raise Http404
            if approve:
                d.customer_approved_by, d.customer_approved_at = who[:150], S.now()
                d.save()
            else:
                d.blockers = (d.blockers + f'\nCustomer rejected: {note}').strip()
                d.save()
        else:
            raise S.ProjectsError('Unknown approval type.')
        return self.get(request)


class PortalInvoicesView(PortalBase):
    @guard
    def get(self, request):
        c = self.contact(request)
        if not S.settings().portal_show_invoices:
            raise A.Denied('Invoices are not shown in the portal.')
        amc = AMCBilling.objects.filter(contract__customer=c.customer, status__in=('invoiced', 'paid')).select_related('contract', 'contract__customer')
        pids = list(ProjectProfile.objects.filter(customer=c.customer).values_list('project_id', flat=True))
        from ProjectManagement.models import Project
        titles = dict(Project.objects.filter(pk__in=pids).values_list('id', 'title'))
        ms = Milestone.objects.filter(project_id__in=pids, invoice_status__in=('invoiced', 'paid'))
        rows = [{'kind': 'AMC', 'reference': b.contract.number, 'description': b.period_label, 'invoice_no': b.invoice_no,
                 'invoice_date': RW.d(b.invoice_date), 'amount': RW.num(b.amount), 'currency': b.contract.currency, 'status': b.status,
                 'paid_date': RW.d(b.paid_date)} for b in amc]
        rows += [{'kind': 'Project', 'reference': titles.get(m.project_id, ''), 'description': m.name, 'invoice_no': m.invoice_no,
                  'invoice_date': RW.d(m.invoice_date), 'amount': RW.num(m.amount), 'currency': S.settings().currency, 'status': m.invoice_status,
                  'paid_date': RW.d(m.paid_date)} for m in ms]
        rows.sort(key=lambda r: r['invoice_date'] or '', reverse=True)
        outstanding = sum((Decimal(str(r['amount'] or 0)) for r in rows if r['status'] == 'invoiced'), Decimal(0))
        return Response({'invoices': rows, 'outstanding': float(outstanding)})


class PortalPasswordView(PortalBase):
    @guard
    def post(self, request):
        f = In(request)
        u = request.user
        if not u.check_password(f.s('current')):
            raise S.ProjectsError('The current password is wrong.', 'current')
        new = f.s('new')
        if len(new) < 8 or new.isdigit() or new.isalpha():
            raise S.ProjectsError('Use at least 8 characters with letters and digits.', 'new')
        u.set_password(new)
        u.save()
        return Response({'detail': 'Password changed.'})
