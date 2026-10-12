"""Projects API – delivery side: ZEO projects with customer, type, milestones, change requests, risks, expenses,
task details and profitability (/zeo-projects/api/projects/...)."""
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response

from . import access as A
from . import rows as RW
from . import services as S
from .models import (BILLING_METHODS, LEVEL, PRIORITIES, PROJECT_TYPES, RISK_KIND, RISK_STATUS, ChangeRequest, Customer,
                     DevRequest, Milestone, PlanTemplate, TaskFile, ProjectExpense, ProjectProfile, ProjectRisk, ServiceProduct, TaskDetail, Ticket,
                     INVOICE_STATUS)
from . import plans as PL
from .views import Base, In, emp, get_or_404, guard


def _pm():
    from ProjectManagement.models import Project, ProjectStage, Task, TimeSheet
    return Project, ProjectStage, Task, TimeSheet


def project_for(request, pk):
    Project = _pm()[0]
    if int(pk) not in set(A.visible_project_ids(request)):
        raise Http404
    p = Project.objects.filter(pk=pk).first()
    if p is None:
        raise Http404
    return p


def can_finance(request, p):
    try:
        from ProjectControl.services import can_see_finance
        return can_see_finance(request, p) or A.can(request, 'finance')
    except Exception:
        return A.is_admin(request)


def can_edit_finance(request, p):
    try:
        from ProjectControl.services import can_edit_finance as cef
        return cef(request, p) or A.can(request, 'billing_edit')
    except Exception:
        return A.is_admin(request)


def need_manage(request, p):
    if not A.manages_project(request, p.pk):
        raise A.Denied('Only the project manager can change this.')


def is_member(request, p):
    e = A.my_emp(request)
    return bool(e and (p.members.filter(pk=e.pk).exists() or p.managers.filter(pk=e.pk).exists()))


def task_rows(p, by_task=None):
    _, _, Task, _ = _pm()
    tasks = list(Task.objects.filter(project=p).select_related('stage').prefetch_related('task_members', 'task_managers').order_by('sequence', 'id'))
    det = {d.task_id: d for d in TaskDetail.objects.filter(task_id__in=[t.pk for t in tasks]).select_related('reviewer', 'milestone')}
    hours = {r['task_id']: r for r in (by_task or [])}
    try:
        from ProjectControl.models import TaskPlan
        plans = {x.task_id: x for x in TaskPlan.objects.filter(task_id__in=[t.pk for t in tasks])}
    except Exception:
        plans = {}
    today = S.local_today()
    from django.db.models import Count
    files = dict(TaskFile.objects.filter(task_id__in=[t.pk for t in tasks]).values_list('task_id').annotate(n=Count('id')))
    out = []
    for t in tasks:
        d = det.get(t.pk)
        h = hours.get(t.pk) or {}
        planned = plans[t.pk].planned_hours if t.pk in plans else (d.estimated_hours if d else Decimal(0))
        actual = h.get('hours') or Decimal(0)
        out.append({
            'id': t.pk, 'title': t.title, 'description': t.description, 'stage': t.stage.title if t.stage_id else '',
            'stage_id': t.stage_id, 'status': t.status, 'start_date': RW.d(t.start_date), 'end_date': RW.d(t.end_date),
            'members': [RW.emp_ref(m) for m in t.task_members.all()], 'managers': [RW.emp_ref(m) for m in t.task_managers.all()],
            'overdue': bool(t.end_date and t.end_date < today and t.status not in ('completed',)),
            'planned_hours': RW.num(planned), 'actual_hours': RW.num(actual),
            'priority': d.priority if d else 'medium', 'phase': d.phase if d else '', 'progress': d.progress if d else (100 if t.status == 'completed' else 0),
            'acceptance_criteria': d.acceptance_criteria if d else '', 'reviewer': RW.emp_ref(d.reviewer) if d else None,
            'depends_on': d.depends_on if d else [], 'blockers': d.blockers if d else '', 'parent_task_id': d.parent_task_id if d else None,
            'customer_approval': d.customer_approval if d else False, 'customer_approved_by': d.customer_approved_by if d else '',
            'customer_approved_at': RW.d(d.customer_approved_at) if d else None, 'milestone': d.milestone_id if d else None,
            'actual_start': RW.d(d.actual_start) if d else None, 'actual_end': RW.d(d.actual_end) if d else None,
            'file_count': files.get(t.pk, 0),
        })
    subs = {}
    for r in out:
        if r['parent_task_id']:
            subs[r['parent_task_id']] = subs.get(r['parent_task_id'], 0) + 1
    for r in out:
        r['subtask_count'] = subs.get(r['id'], 0)
    done = {r['id'] for r in out if r['status'] == 'completed'}
    for r in out:
        r['blocked'] = bool([x for x in (r['depends_on'] or []) if x not in done]) or bool(r['blockers'])
    return out


def project_row(p, prof=None, fin=None, milestones=None, light=True):
    prof = prof if prof is not None else ProjectProfile.objects.filter(project_id=p.pk).select_related('customer').first()
    fin = fin if fin is not None else S.finance(p.pk)
    ms = milestones if milestones is not None else list(Milestone.objects.filter(project_id=p.pk))
    accepted = len([m for m in ms if m.status == 'accepted'])
    return {
        'id': p.pk, 'title': p.title, 'status': p.status, 'start_date': RW.d(p.start_date), 'end_date': RW.d(p.end_date),
        'description': p.description, 'number': prof.number if prof else '', 'customer': prof.customer_id if prof else None,
        'customer_name': prof.customer.name if prof and prof.customer_id else (fin.customer_name if fin else ''),
        'branch_name': ', '.join(b.branch_name for b in p.branches.all()),   # v1.22.0 Branch column / filter
        'project_type': prof.project_type if prof else '', 'project_type_label': prof.get_project_type_display() if prof else '',
        'billing_method': prof.billing_method if prof else '', 'contract_ref': prof.contract_ref if prof else '',
        'contract_value': RW.num(fin.contract_value) if fin else 0, 'currency': fin.currency if fin else S.settings().currency,
        'budget_hours': RW.num(fin.budget_hours) if fin else 0,
        'milestones_total': len(ms), 'milestones_accepted': accepted,
        'milestones_overdue': len([m for m in ms if m.planned_date and m.planned_date < S.local_today() and m.status != 'accepted']),
        'health': S.project_health(p, ms),
        'product': prof.product_id if prof else None, 'product_name': prof.product.name if prof and prof.product_id else '',
        'plan_template': prof.plan_template_id if prof else None,
        'plan_template_name': prof.plan_template.name if prof and prof.plan_template_id else '',
        'managers': [RW.emp_ref(m) for m in p.managers.all()],
    }


class ProjectListView(Base):
    zeo_model_for = staticmethod(lambda kwargs: __import__('ProjectManagement.models', fromlist=['Project']).Project)   # v1.22.0 extra fields
    @guard
    def get(self, request):
        Project = _pm()[0]
        q = request.query_params
        qs = Project.objects.filter(pk__in=A.visible_project_ids(request)).prefetch_related('managers').order_by('-start_date', '-id')
        if q.get('q'):
            qs = qs.filter(Q(title__icontains=q['q']))
        if q.get('status'):
            qs = qs.filter(status__in=q['status'].split(','))
        profs = {x.project_id: x for x in ProjectProfile.objects.select_related('customer')}
        if q.get('customer'):
            qs = qs.filter(pk__in=[k for k, v in profs.items() if str(v.customer_id) == q['customer']])
        try:
            from ProjectControl.models import ProjectFinance
            fins = {f.project_id: f for f in ProjectFinance.objects.all()}
        except Exception:
            fins = {}
        ms = {}
        for m in Milestone.objects.all():
            ms.setdefault(m.project_id, []).append(m)
        rows = [project_row(p, profs.get(p.pk) or False, fins.get(p.pk) or False, ms.get(p.pk, [])) for p in qs[:300]]
        if q.get('health'):
            rows = [r for r in rows if r['health'] in q['health'].split(',')]
        return Response(rows)

    @guard
    def post(self, request):
        A.need(request, 'project_edit', 'You need the right to create projects.')
        Project = _pm()[0]
        f = In(request)
        title = f.s('title')
        if not title:
            raise S.ProjectsError('Project name is required.', 'title')
        if Project.objects.filter(title__iexact=title).exists():
            raise S.ProjectsError('A project with this name already exists.', 'title')
        start = f.dt('start_date', required=True)
        end = f.dt('end_date')
        if end is None and not f.has('plan_template'):
            raise S.ProjectsError('The expected end date is required.', 'end_date')
        if end and end < start:
            raise S.ProjectsError('The expected end date is before the start.', 'end_date')
        customer = f.obj('customer', Customer)
        from EmpManagement.models import emp_master
        with transaction.atomic():
            p = Project.objects.create(title=title, start_date=start, end_date=end, description=f.s('description') or title,
                                       status='new')
            p.managers.set(emp_master.objects.filter(pk__in=f.ids('managers')))
            p.members.set(emp_master.objects.filter(pk__in=f.ids('members')))
            from OrganisationManager.models import brnch_mstr
            if f.has('branches'):
                bids = f.ids('branches')
                allowed = A.branch_ids(request)
                if allowed is not None and any(b not in allowed for b in bids):
                    raise S.ProjectsError('Choose one of your branches.', 'branches')
                p.branches.set(brnch_mstr.objects.filter(pk__in=bids))
            else:   # v1.22.0: the customer's branch, else the user's branch – so branch users keep seeing their projects
                from zeo.appkit.engine import default_branch
                bid = (customer.branch_id if customer is not None else None) or default_branch(request)
                if bid:
                    p.branches.set(brnch_mstr.objects.filter(pk=bid))
            prof = S.ensure_profile(p, customer=customer, project_type=f.choice('project_type', PROJECT_TYPES, 'implementation'),
                                    billing_method=f.choice('billing_method', BILLING_METHODS, 'milestone'))
            for k in ('contract_ref', 'scope', 'deliverables'):
                if f.has(k):
                    setattr(prof, k, f.s(k))
            prof.save()
            S.sync_finance(prof)
            fin = S.finance(p.pk)
            if fin:
                for k in ('contract_value', 'budget_hours', 'budget_amount', 'default_bill_rate'):
                    if f.has(k):
                        setattr(fin, k, f.n(k, Decimal(0)))
                if f.has('currency'):
                    fin.currency = f.s('currency') or fin.currency
                fin.save()
            tpl = f.obj('plan_template', PlanTemplate) if f.has('plan_template') else None
            if f.has('product'):
                prof.product = f.obj('product', ServiceProduct)
                prof.save(update_fields=['product'])
            if tpl is not None:
                roles = f.d.get('roles') if isinstance(f.d.get('roles'), dict) else {}
                PL.apply_template(p, tpl, start, roles, request.user)
            elif f.b('template_phases'):
                ProjectStage = _pm()[1]
                for i, name in enumerate(('Analysis', 'Configuration', 'Development', 'Migration', 'Testing', 'Deployment'), 1):
                    ProjectStage.objects.create(project=p, title=name, sequence=i, is_end_stage=(name == 'Deployment'))
        return Response(project_row(p), status=status.HTTP_201_CREATED)


class ProjectDetailView(Base):
    zeo_model_for = staticmethod(lambda kwargs: __import__('ProjectManagement.models', fromlist=['Project']).Project)   # v1.22.0 record panel
    @guard
    def get(self, request, pk):
        p = project_for(request, pk)
        prof = S.ensure_profile(p)
        fin = S.finance(p.pk)
        ms = list(Milestone.objects.filter(project_id=p.pk))
        r = project_row(p, prof, fin, ms)
        r.update({'scope': prof.scope, 'deliverables': prof.deliverables, 'portal_visible': prof.portal_visible,
                  'invoiced_amount': RW.num(prof.invoiced_amount),
                  'members': [RW.emp_ref(m) for m in p.members.all()],
                  'stages': [{'id': s.id, 'title': s.title, 'sequence': s.sequence} for s in p.stages.order_by('sequence', 'id')],
                  'milestones': [RW.milestone_row(m) for m in ms],
                  'change_requests': [RW.cr_row(c) for c in ChangeRequest.objects.filter(project_id=p.pk)],
                  'risks': [RW.risk_row(x) for x in ProjectRisk.objects.filter(project_id=p.pk).select_related('owner')],
                  'tickets': [RW.ticket_row(t) for t in Ticket.objects.filter(project_id=p.pk).select_related('customer', 'contact', 'product',
                                                                                                            'category', 'contract', 'team', 'assigned_to', 'requester_employee')[:50]],
                  'dev_requests': [RW.dev_row(d) for d in DevRequest.objects.filter(project_id=p.pk)],
                  'can_manage': A.manages_project(request, p.pk), 'can_finance': can_finance(request, p),
                  'can_edit_finance': can_edit_finance(request, p), 'is_member': is_member(request, p),
                  'can_approve_cr': A.can(request, 'cr_approve') or A.is_admin(request)})
        if r['can_finance']:
            prof_data = S.project_profit(p)
            r['profit'] = RW.jsonable(prof_data)
            r['health'] = S.project_health(p, ms, prof_data.get('_summary'))
            r['expenses'] = [RW.expense_row(x) for x in ProjectExpense.objects.filter(project_id=p.pk)]
            r['finance'] = {'contract_value': RW.num(fin.contract_value), 'budget_hours': RW.num(fin.budget_hours),
                            'budget_amount': RW.num(fin.budget_amount), 'currency': fin.currency,
                            'default_bill_rate': RW.num(fin.default_bill_rate)} if fin else None
            r['tasks'] = task_rows(p, prof_data.get('by_task'))
        else:
            r['tasks'] = task_rows(p)
        return Response(r)

    @guard
    def patch(self, request, pk):
        p = project_for(request, pk)
        need_manage(request, p)
        f = In(request)
        prof = S.ensure_profile(p)
        from EmpManagement.models import emp_master
        with transaction.atomic():
            if f.has('title'):
                Project = _pm()[0]
                t = f.s('title')
                if not t:
                    raise S.ProjectsError('Project name is required.', 'title')
                if Project.objects.filter(title__iexact=t).exclude(pk=p.pk).exists():
                    raise S.ProjectsError('A project with this name already exists.', 'title')
                p.title = t
            if f.has('status'):
                p.status = f.choice('status', p.PROJECT_STATUS, p.status)
            for k in ('start_date', 'end_date'):
                if f.has(k):
                    setattr(p, k, f.dt(k, required=True))
            if p.end_date and p.start_date and p.end_date < p.start_date:
                raise S.ProjectsError('The expected end date is before the start.', 'end_date')
            if f.has('description'):
                p.description = f.s('description') or p.title
            p.save()
            if f.has('managers'):
                p.managers.set(emp_master.objects.filter(pk__in=f.ids('managers')))
            if f.has('members'):
                p.members.set(emp_master.objects.filter(pk__in=f.ids('members')))
            for k in ('contract_ref', 'scope', 'deliverables'):
                if f.has(k):
                    setattr(prof, k, f.s(k))
            if f.has('customer'):
                prof.customer = f.obj('customer', Customer)
            if f.has('project_type'):
                prof.project_type = f.choice('project_type', PROJECT_TYPES, prof.project_type)
            if f.has('billing_method'):
                prof.billing_method = f.choice('billing_method', BILLING_METHODS, prof.billing_method)
            if f.has('portal_visible'):
                prof.portal_visible = f.b('portal_visible', True)
            prof.save()
            S.sync_finance(prof)
            if any(f.has(k) for k in ('contract_value', 'budget_hours', 'budget_amount', 'default_bill_rate', 'currency', 'invoiced_amount')):
                if not can_edit_finance(request, p):
                    raise A.Denied('You cannot change the project finance.')
                fin = S.finance(p.pk)
                for k in ('contract_value', 'budget_hours', 'budget_amount', 'default_bill_rate'):
                    if f.has(k):
                        setattr(fin, k, f.n(k, Decimal(0)))
                if f.has('currency'):
                    fin.currency = f.s('currency') or fin.currency
                fin.save()
                if f.has('invoiced_amount'):
                    prof.invoiced_amount = f.n('invoiced_amount', Decimal(0))
                    prof.save(update_fields=['invoiced_amount'])
        return self.get(request, pk)


# ------------------------------------------------------------------ tasks
class ProjectTaskView(Base):
    """POST → new task (or sub-task) with planning details."""

    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        need_manage(request, p)
        _, ProjectStage, Task, _ = _pm()
        f = In(request)
        title = f.s('title')
        if not title:
            raise S.ProjectsError('Task name is required.', 'title')
        stage = ProjectStage.objects.filter(project=p, pk=f.i('stage')).first() if f.i('stage') else None
        start, end = f.dt('start_date'), f.dt('end_date')
        if start is None:
            raise S.ProjectsError('The expected start date is required.', 'start_date')
        if end is None:
            raise S.ProjectsError('The expected end date is required.', 'end_date')
        if end < start:
            raise S.ProjectsError('The expected end date is before the start date.', 'end_date')
        parent = None
        if f.i('parent_task_id'):
            parent = Task.objects.filter(pk=f.i('parent_task_id'), project=p).first()
            if parent is None:
                raise S.ProjectsError('The main task is not in this project.', 'parent_task_id')
            check_within_parent(parent, start, end)
            stage = stage or parent.stage
        stage = stage or S.first_stage(p)
        from EmpManagement.models import emp_master
        with transaction.atomic():
            t = Task.objects.create(title=title[:200], project=p, stage=stage, start_date=start, end_date=end,
                                    description=f.s('description') or title, sequence=f.i('sequence') or 0)
            members = emp_master.objects.filter(pk__in=f.ids('members'))
            t.task_members.set(members)
            p.members.add(*members)
            save_task_detail(t, f, p)
        PL.notify_task_assigned(t, list(members), request.user)
        return Response([r for r in task_rows(p) if r['id'] == t.pk][0], status=status.HTTP_201_CREATED)


def check_within_parent(parent, start, end):
    if parent.start_date and start and start < parent.start_date or parent.end_date and end and end > parent.end_date:
        raise S.ProjectsError(f'A sub-task must lie within its main task ({parent.start_date:%d %b %Y} – {parent.end_date:%d %b %Y}). '
                              'Change the main task dates first.', 'end_date')


def save_task_detail(t, f, p):
    d, _ = TaskDetail.objects.get_or_create(task_id=t.pk)
    for k in ('phase', 'acceptance_criteria', 'blockers'):
        if f.has(k):
            setattr(d, k, f.s(k))
    if f.has('priority'):
        d.priority = f.choice('priority', PRIORITIES, 'medium')
    if f.has('progress'):
        d.progress = max(0, min(100, f.i('progress') or 0))
    if f.has('estimated_hours'):
        d.estimated_hours = f.n('estimated_hours', Decimal(0))
        try:
            from ProjectControl.models import TaskPlan
            TaskPlan.objects.update_or_create(task_id=t.pk, defaults={'planned_hours': d.estimated_hours})
        except Exception:
            pass
    if f.has('reviewer'):
        d.reviewer = emp(f.i('reviewer'))
    if f.has('depends_on'):
        ids = [x for x in f.ids('depends_on') if x != t.pk]
        _, _, Task, _ = _pm()
        if Task.objects.filter(pk__in=ids).exclude(project=p).exists():
            raise S.ProjectsError('Dependencies must be tasks of the same project.', 'depends_on')
        d.depends_on = ids
    if f.has('parent_task_id'):
        parent = f.i('parent_task_id')
        if parent == t.pk:
            raise S.ProjectsError('A task cannot be its own parent.', 'parent_task_id')
        if parent:
            _, _, Task, _ = _pm()
            if not Task.objects.filter(pk=parent, project=p).exists():
                raise S.ProjectsError('The main task is not in this project.', 'parent_task_id')
            if TaskDetail.objects.filter(task_id=parent, parent_task_id__isnull=False).exists():
                raise S.ProjectsError('A sub-task cannot have its own sub-tasks – pick a main task.', 'parent_task_id')
            if TaskDetail.objects.filter(parent_task_id=t.pk).exists():
                raise S.ProjectsError('This task has sub-tasks, so it cannot become a sub-task.', 'parent_task_id')
        d.parent_task_id = parent
    if f.has('customer_approval'):
        d.customer_approval = f.b('customer_approval')
    if f.has('milestone'):
        mid = f.i('milestone')
        d.milestone = Milestone.objects.filter(pk=mid, project_id=p.pk).first() if mid else None
    d.save()
    return d


class TaskDetailView(Base):
    @guard
    def patch(self, request, task_id):
        _, ProjectStage, Task, _ = _pm()
        t = Task.objects.filter(pk=task_id).select_related('project').first()
        if t is None:
            raise Http404
        p = project_for(request, t.project_id)
        e = A.my_emp(request)
        on_task = bool(e and (t.task_members.filter(pk=e.pk).exists() or t.task_managers.filter(pk=e.pk).exists()))
        manager = A.manages_project(request, p.pk)
        if not (manager or on_task):
            raise A.Denied('Only the project manager or people on the task can change it.')
        f = In(request)
        if f.has('status'):
            st = f.choice('status', Task.TASK_STATUS, t.status)
            if st == 'completed':
                open_subs = TaskDetail.objects.filter(parent_task_id=t.pk).values_list('task_id', flat=True)
                if Task.objects.filter(pk__in=list(open_subs)).exclude(status='completed').exists():
                    raise S.ProjectsError('Complete the sub-tasks first.', 'status')
                d = TaskDetail.objects.filter(task_id=t.pk).first()
                if d and d.depends_on and Task.objects.filter(pk__in=d.depends_on).exclude(status='completed').exists():
                    raise S.ProjectsError('Finish the tasks this one depends on first.', 'status')
                if d and d.customer_approval and not d.customer_approved_at:
                    raise S.ProjectsError('This task needs the customer approval before it is completed.', 'status')
            t.status = st
        if f.has('stage'):
            st = ProjectStage.objects.filter(project=p, pk=f.i('stage')).first()
            if st:
                t.stage = st
        if manager:
            for k in ('title', 'description'):
                if f.has(k) and f.s(k):
                    setattr(t, k, f.s(k))
            for k in ('start_date', 'end_date'):
                if f.has(k):
                    v = f.dt(k)
                    if v is None:
                        raise S.ProjectsError('The expected start and end dates are required.', k)
                    setattr(t, k, v)
            if t.start_date and t.end_date and t.end_date < t.start_date:
                raise S.ProjectsError('The expected end date is before the start date.', 'end_date')
            pd = TaskDetail.objects.filter(task_id=t.pk).first()
            if pd and pd.parent_task_id and (f.has('start_date') or f.has('end_date')):
                parent = Task.objects.filter(pk=pd.parent_task_id).first()
                if parent:
                    check_within_parent(parent, t.start_date, t.end_date)
            if f.has('members'):
                from EmpManagement.models import emp_master
                members = emp_master.objects.filter(pk__in=f.ids('members'))
                before = set(t.task_members.values_list('pk', flat=True))
                t.task_members.set(members)
                p.members.add(*members)
                PL.notify_task_assigned(t, [m for m in members if m.pk not in before], request.user)
        t.save()
        if t.status == 'completed':
            S.close_todos(t)
        allowed = In.__new__(In)
        allowed.d = {k: v for k, v in f.d.items() if manager or k in ('progress', 'blockers')}
        allowed.files = {}
        d = save_task_detail(t, allowed, p)
        if t.status == 'completed' and d.progress < 100:
            d.progress = 100
            d.save(update_fields=['progress'])
        today = S.local_today()
        if t.status in ('in_progress', 'completed') and not d.actual_start:
            d.actual_start = today
            d.save(update_fields=['actual_start'])
        if t.status == 'completed' and not d.actual_end:
            d.actual_end = today
            d.save(update_fields=['actual_end'])
        if t.status != 'completed' and d.actual_end:
            d.actual_end = None
            d.save(update_fields=['actual_end'])
        if f.has('customer_approved') and manager and f.b('customer_approved'):
            d.customer_approved_by = (f.s('customer_name') or 'Recorded by project manager')[:150]
            d.customer_approved_at = S.now()
            d.save()
        return Response([r for r in task_rows(p) if r['id'] == t.pk][0])


# ------------------------------------------------------------------ milestones, change requests, risks, expenses
class MilestoneView(Base):
    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        need_manage(request, p)
        f = In(request)
        if not f.s('name'):
            raise S.ProjectsError('Milestone name is required.', 'name')
        seq = f.i('sequence') or (Milestone.objects.filter(project_id=p.pk).count() + 1)
        m = Milestone.objects.create(project_id=p.pk, sequence=seq, name=f.s('name')[:200], description=f.s('description'),
                                     planned_date=f.dt('planned_date'), amount=f.n('amount', Decimal(0)),
                                     depends_on=Milestone.objects.filter(pk=f.i('depends_on'), project_id=p.pk).first() if f.i('depends_on') else None)
        return Response(RW.milestone_row(m), status=status.HTTP_201_CREATED)


class MilestoneDetailView(Base):
    @guard
    def patch(self, request, pk):
        m = get_or_404(Milestone, pk)
        p = project_for(request, m.project_id)
        f = In(request)
        invoice_keys = ('invoice_status', 'invoice_no', 'invoice_date', 'paid_date')
        if any(f.has(k) for k in invoice_keys):
            if not (A.can(request, 'billing_edit') or can_edit_finance(request, p)):
                raise A.Denied('Only finance can record invoices.')
            if f.has('invoice_status'):
                m.invoice_status = f.choice('invoice_status', INVOICE_STATUS, m.invoice_status)
            if f.has('invoice_no'):
                m.invoice_no = f.s('invoice_no')
            for k in ('invoice_date', 'paid_date'):
                if f.has(k):
                    setattr(m, k, f.dt(k))
            if m.invoice_status in ('invoiced', 'paid') and not m.invoice_no:
                raise S.ProjectsError('Enter the invoice number.', 'invoice_no')
            if m.invoice_status == 'paid' and not m.paid_date:
                m.paid_date = S.local_today()
        plan_keys = ('name', 'description', 'planned_date', 'amount', 'sequence', 'depends_on')
        if any(f.has(k) for k in plan_keys):
            need_manage(request, p)
            if m.status == 'accepted' and (f.has('amount') or f.has('name')):
                raise S.ProjectsError('An accepted milestone cannot be changed.')
            if f.has('name'):
                m.name = f.s('name') or m.name
            if f.has('description'):
                m.description = f.s('description')
            if f.has('planned_date'):
                m.planned_date = f.dt('planned_date')
            if f.has('amount'):
                m.amount = f.n('amount', Decimal(0))
            if f.has('sequence'):
                m.sequence = f.i('sequence') or m.sequence
            if f.has('depends_on'):
                dep = f.i('depends_on')
                m.depends_on = Milestone.objects.filter(pk=dep, project_id=p.pk).exclude(pk=m.pk).first() if dep else None
        m.save()
        return Response(RW.milestone_row(m))

    @guard
    def delete(self, request, pk):
        m = get_or_404(Milestone, pk)
        p = project_for(request, m.project_id)
        need_manage(request, p)
        if m.status == 'accepted' or m.invoice_status in ('invoiced', 'paid'):
            raise S.ProjectsError('An accepted or invoiced milestone cannot be deleted.')
        m.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MilestoneStatusView(Base):
    @guard
    def post(self, request, pk):
        m = get_or_404(Milestone, pk)
        p = project_for(request, m.project_id)
        need_manage(request, p)
        f = In(request)
        st = f.s('status')
        if st in ('ready', 'accepted') and m.depends_on_id and m.depends_on.status != 'accepted':
            raise S.ProjectsError(f'"{m.depends_on.name}" must be accepted first.')
        S.milestone_status(m, request.user, st, f.s('note'), f.s('customer_name'))
        return Response(RW.milestone_row(m))


class ChangeRequestView(Base):
    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        if not (A.manages_project(request, p.pk) or is_member(request, p)):
            raise A.Denied('Only the project team can raise change requests.')
        f = In(request)
        if not f.s('title'):
            raise S.ProjectsError('Title is required.', 'title')
        c = ChangeRequest.objects.create(number=S.next_number('cr'), project_id=p.pk, title=f.s('title')[:200],
                                         description=f.s('description'), extra_hours=f.n('extra_hours', Decimal(0)),
                                         extra_amount=f.n('extra_amount', Decimal(0)), extra_days=f.i('extra_days') or 0,
                                         requested_by=request.user, status='submitted')
        S.notify_users(S.users_with('approve_changerequest'), f'{p.title}: change request {c.number}',
                       f'{c.title}\n\n{c.description}\nExtra: {c.extra_hours} h, {S.settings().currency} {S.q2(c.extra_amount)}', c,
                       f'/main-sidebar/zeo-projects/projects/{p.pk}', f'Approve change request {c.number}')
        return Response(RW.cr_row(c), status=status.HTTP_201_CREATED)


class ChangeRequestDecideView(Base):
    @guard
    def post(self, request, pk):
        c = get_or_404(ChangeRequest, pk)
        p = project_for(request, c.project_id)
        f = In(request)
        step = f.s('step') or 'internal'
        if step == 'internal' and not (A.can(request, 'cr_approve') or A.is_admin(request)):
            raise A.Denied('You cannot approve change requests.')
        if step in ('customer', 'done'):
            need_manage(request, p)
        S.decide_change_request(c, request.user, step, f.b('approve', True), f.s('note'), f.s('customer_name'))
        S.close_todos(c)
        return Response(RW.cr_row(c))


class RiskView(Base):
    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        if not (A.manages_project(request, p.pk) or is_member(request, p)):
            raise A.Denied('Only the project team can log risks and issues.')
        f = In(request)
        if not f.s('title'):
            raise S.ProjectsError('Title is required.', 'title')
        r = ProjectRisk.objects.create(project_id=p.pk, kind=f.choice('kind', RISK_KIND, 'risk'), title=f.s('title')[:200],
                                       description=f.s('description'), impact=f.choice('impact', LEVEL, 'medium'),
                                       probability=f.choice('probability', LEVEL, 'medium'), owner=emp(f.i('owner')),
                                       due_date=f.dt('due_date'), mitigation=f.s('mitigation'))
        return Response(RW.risk_row(r), status=status.HTTP_201_CREATED)


class RiskDetailView(Base):
    @guard
    def patch(self, request, pk):
        r = get_or_404(ProjectRisk, pk)
        p = project_for(request, r.project_id)
        e = A.my_emp(request)
        if not (A.manages_project(request, p.pk) or (e and r.owner_id == e.pk)):
            raise A.Denied('Only the project manager or the owner can change this.')
        f = In(request)
        for k in ('title', 'description', 'mitigation'):
            if f.has(k):
                setattr(r, k, f.s(k))
        for k, ch in (('kind', RISK_KIND), ('impact', LEVEL), ('probability', LEVEL), ('status', RISK_STATUS)):
            if f.has(k):
                setattr(r, k, f.choice(k, ch, getattr(r, k)))
        if f.has('owner'):
            r.owner = emp(f.i('owner'))
        if f.has('due_date'):
            r.due_date = f.dt('due_date')
        r.save()
        return Response(RW.risk_row(r))

    @guard
    def delete(self, request, pk):
        r = get_or_404(ProjectRisk, pk)
        need_manage(request, project_for(request, r.project_id))
        r.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExpenseView(Base):
    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        if not (A.manages_project(request, p.pk) or can_edit_finance(request, p)):
            raise A.Denied()
        f = In(request)
        if not f.s('description'):
            raise S.ProjectsError('Description is required.', 'description')
        amount = f.n('amount', label='Amount')
        if not amount:
            raise S.ProjectsError('Amount is required.', 'amount')
        x = ProjectExpense.objects.create(project_id=p.pk, date=f.dt('date') or S.local_today(),
                                          category=f.choice('category', ProjectExpense.CATEGORIES, 'third_party'),
                                          description=f.s('description')[:255], vendor=f.s('vendor'), amount=amount, created_by=request.user)
        return Response(RW.expense_row(x), status=status.HTTP_201_CREATED)


class ExpenseDetailView(Base):
    @guard
    def delete(self, request, pk):
        x = get_or_404(ProjectExpense, pk)
        p = project_for(request, x.project_id)
        if not (A.manages_project(request, p.pk) or can_edit_finance(request, p)):
            raise A.Denied()
        x.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



# ------------------------------------------------------------------ v1.15.0 task delete, plan templates
class TaskDeleteView(Base):
    @guard
    def delete(self, request, task_id):
        _, _, Task, _ = _pm()
        t = Task.objects.filter(pk=task_id).first()
        if t is None:
            raise Http404
        p = project_for(request, t.project_id)
        need_manage(request, p)
        if t.timesheets.exists():
            raise S.ProjectsError('Time is already logged on this task – set it to completed instead of deleting it.')
        if TaskDetail.objects.filter(parent_task_id=t.pk).exists():
            raise S.ProjectsError('Delete or move its sub-tasks first.')
        with transaction.atomic():
            TaskDetail.objects.filter(task_id=t.pk).delete()
            for x in TaskFile.objects.filter(task_id=t.pk):
                x.file.delete(save=False)
                x.delete()
            try:
                from ProjectControl.models import TaskPlan
                TaskPlan.objects.filter(task_id=t.pk).delete()
            except Exception:
                pass
            S.close_todos(t)
            t.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def need_templates(request):
    if not (A.can(request, 'project_edit') or A.can(request, 'setup') or A.is_admin(request)):
        raise A.Denied('You need the right to change projects to edit plan templates.')


class PlanTemplateListView(Base):
    """GET ?product=&project_type=&active=1 → templates (without lines); POST → new template with lines."""

    @guard
    def get(self, request):
        q = request.query_params
        qs = PlanTemplate.objects.select_related('product')
        if q.get('active', '1') == '1':
            qs = qs.filter(is_active=True)
        if q.get('product'):
            qs = qs.filter(Q(product_id=q['product']) | Q(product__isnull=True))
        if q.get('project_type'):
            qs = qs.filter(Q(project_type=q['project_type']) | Q(project_type=''))
        return Response([PL.template_row(t) for t in qs.prefetch_related('lines')])

    @guard
    def post(self, request):
        need_templates(request)
        t = PL.save_template(PlanTemplate(), request.data)
        return Response(PL.template_row(t), status=status.HTTP_201_CREATED)


class PlanTemplateDefaultsView(Base):
    """POST → add the default templates that are missing (e.g. "IT implementation – System study to Go-live")."""

    @guard
    def post(self, request):
        need_templates(request)
        made = PL.install_default_templates()
        return Response({'created': made})


class PlanTemplateDetailView(Base):
    @guard
    def get(self, request, pk):
        t = PlanTemplate.objects.filter(pk=pk).select_related('product').first()
        if t is None:
            raise Http404
        return Response(PL.template_row(t))

    @guard
    def put(self, request, pk):
        need_templates(request)
        t = PlanTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        return Response(PL.template_row(PL.save_template(t, request.data)))

    patch = put

    @guard
    def delete(self, request, pk):
        need_templates(request)
        t = PlanTemplate.objects.filter(pk=pk).first()
        if t is None:
            raise Http404
        t.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProjectApplyTemplateView(Base):
    """POST {template, start_date, roles: {role: employee id}} → stages, tasks and milestones added to the project."""

    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        need_manage(request, p)
        f = In(request)
        tpl = f.obj('template', PlanTemplate)
        if tpl is None:
            raise S.ProjectsError('Pick a template.', 'template')
        roles = f.d.get('roles') if isinstance(f.d.get('roles'), dict) else {}
        counts = PL.apply_template(p, tpl, f.dt('start_date') or p.start_date, roles, request.user)
        return Response(counts)


class ProjectSaveTemplateView(Base):
    """POST {name, product, project_type} → the project's tasks and milestones saved as a new template."""

    @guard
    def post(self, request, pk):
        p = project_for(request, pk)
        need_templates(request)
        f = In(request)
        prof = S.ensure_profile(p)
        product = f.obj('product', ServiceProduct) if f.has('product') else prof.product
        t = PL.template_from_project(p, f.s('name'), product, f.s('project_type') or prof.project_type)
        return Response(PL.template_row(t), status=status.HTTP_201_CREATED)



# ------------------------------------------------------------------ v1.15.0 task attachments
def task_and_project(request, task_id):
    _, _, Task, _ = _pm()
    t = Task.objects.filter(pk=task_id).select_related('project').first()
    if t is None:
        raise Http404
    return t, project_for(request, t.project_id)


def can_touch_task(request, t, p):
    e = A.my_emp(request)
    return A.manages_project(request, p.pk) or bool(e and (t.task_members.filter(pk=e.pk).exists() or p.members.filter(pk=e.pk).exists()
                                                         or t.task_managers.filter(pk=e.pk).exists()))


def file_row(x):
    return {'id': x.pk, 'name': x.name, 'size': x.size, 'note': x.note, 'uploaded_at': RW.d(x.uploaded_at),
            'uploaded_by': S.user_name(x.uploaded_by) if x.uploaded_by_id else ''}


class TaskFilesView(Base):
    """GET → files of a task; POST (multipart: files[], note) → upload (20 MB each)."""

    @guard
    def get(self, request, task_id):
        t, p = task_and_project(request, task_id)
        return Response([file_row(x) for x in TaskFile.objects.filter(task_id=t.pk).select_related('uploaded_by')])

    @guard
    def post(self, request, task_id):
        t, p = task_and_project(request, task_id)
        if not can_touch_task(request, t, p):
            raise A.Denied('Only the project team can add files to this task.')
        f = In(request)
        files = f.filelist()
        if not files:
            raise S.ProjectsError('Choose a file.', 'files')
        for x in files:
            if getattr(x, 'size', 0) > 20 * 1024 * 1024:
                raise S.ProjectsError(f'{x.name} is larger than 20 MB.', 'files')
        user = request.user if request.user.is_authenticated else None
        made = [TaskFile.objects.create(task_id=t.pk, file=x, name=(x.name or 'file')[:255], size=getattr(x, 'size', 0) or 0,
                                        note=(f.s('note') or '')[:255], uploaded_by=user) for x in files]
        return Response([file_row(x) for x in made], status=status.HTTP_201_CREATED)


class TaskFileView(Base):
    """GET → download; DELETE → remove (who uploaded it or the project manager)."""

    @guard
    def get(self, request, pk):
        from django.http import FileResponse
        x = TaskFile.objects.filter(pk=pk).first()
        if x is None:
            raise Http404
        task_and_project(request, x.task_id)
        return FileResponse(x.file.open('rb'), as_attachment=True, filename=x.name)

    @guard
    def delete(self, request, pk):
        x = TaskFile.objects.filter(pk=pk).first()
        if x is None:
            raise Http404
        t, p = task_and_project(request, x.task_id)
        if not (A.manages_project(request, p.pk) or x.uploaded_by_id == request.user.pk):
            raise A.Denied('Only who added the file or the project manager can remove it.')
        x.file.delete(save=False)
        x.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


try:   # v1.22.0 record panel (Chatter): projects the user may see
    from Chatter.views import register_scope as _rs
    from ProjectManagement.models import Project as _P
    _rs(_P, lambda request, qs: qs.filter(pk__in=A.visible_project_ids(request)))
except Exception:
    pass
