"""Project plan templates and task notifications (v1.15.0).

* A PlanTemplate holds phases and tasks with a start day and duration in WORKING days, planned hours, the usual
  role and optional milestones (% of the contract value). Templates belong to a product (Odoo, Sage 300 …) and / or
  a project type, so the right ones are offered when a project is created.
* apply_template() turns a template into ZEO project stages, tasks (with planned hours) and milestones, dated from the
  project start on the company working calendar. Everything stays editable afterwards.
* template_from_project() saves a finished project's plan as a new template.
* Task assignment and overdue tasks notify the people on the task (e-mail + My To-do), as the notification settings allow.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction

from . import services as S
from . import sla
from .models import Milestone, PlanTemplate, PlanTemplateLine, ProjectProfile, TaskDetail

D0 = Decimal('0')


def _pm():
    from ProjectManagement.models import Project, ProjectStage, Task, TimeSheet
    return Project, ProjectStage, Task, TimeSheet


# ------------------------------------------------------------------ working days
def next_work_day(d, cal):
    n = 0
    while not cal.is_work_day(d) and n < 60:
        d += timedelta(days=1)
        n += 1
    return d


def add_work_days(start, days, cal=None):
    """start (moved to a working day) plus N working days."""
    cal = cal or sla.WorkCalendar()
    d = next_work_day(start, cal)
    n = 0
    while n < days:
        d = next_work_day(d + timedelta(days=1), cal)
        n += 1
    return d


def work_days_between(a, b, cal=None):
    """Working days from a to b (b after a), 0 when b <= a."""
    cal = cal or sla.WorkCalendar()
    if not a or not b or b <= a:
        return 0
    n, d = 0, a
    while d < b and n < 2000:
        d += timedelta(days=1)
        if cal.is_work_day(d):
            n += 1
    return n


# ------------------------------------------------------------------ templates
def template_row(t, lines=True):
    ls = list(t.lines.all()) if lines else []
    r = {'id': t.pk, 'name': t.name, 'product': t.product_id, 'product_name': t.product.name if t.product_id else '',
         'project_type': t.project_type, 'project_type_label': t.get_project_type_display() if t.project_type else '',
         'description': t.description, 'is_active': t.is_active,
         'line_count': len(ls) if lines else t.lines.count()}
    if lines:
        r['lines'] = [{'id': x.pk, 'sequence': x.sequence, 'phase': x.phase, 'title': x.title, 'description': x.description,
                       'start_day': x.start_day, 'duration_days': x.duration_days, 'hours': float(x.hours), 'role': x.role,
                       'is_milestone': x.is_milestone, 'milestone_pct': float(x.milestone_pct), 'customer_approval': x.customer_approval,
                       'is_subtask': x.is_subtask}
                      for x in ls]
        r['total_hours'] = float(sum((x.hours for x in ls), D0))
        r['total_days'] = max([x.start_day + x.duration_days for x in ls] or [0])
        r['phases'] = list(dict.fromkeys(x.phase for x in ls if x.phase))
        r['roles'] = sorted({x.role for x in ls if x.role})
        r['milestone_pct'] = float(sum((x.milestone_pct for x in ls if x.is_milestone), D0))
    return r


def save_template(t, data):
    """data: name, product, project_type, description, is_active, lines[] (the whole list replaces the old one)."""
    from .models import PROJECT_TYPES, ServiceProduct
    name = (data.get('name') or '').strip()
    if not name:
        raise S.ProjectsError('Template name is required.', 'name')
    if PlanTemplate.objects.filter(name__iexact=name).exclude(pk=t.pk).exists():
        raise S.ProjectsError('A template with this name already exists.', 'name')
    t.name = name[:150]
    if 'product' in data:
        pid = data.get('product')
        t.product = ServiceProduct.objects.filter(pk=pid).first() if pid else None
    if 'project_type' in data:
        pt = data.get('project_type') or ''
        if pt and pt not in dict(PROJECT_TYPES):
            raise S.ProjectsError('Unknown project type.', 'project_type')
        t.project_type = pt
    if 'description' in data:
        t.description = data.get('description') or ''
    if 'is_active' in data:
        t.is_active = bool(data.get('is_active'))
    lines = data.get('lines')
    with transaction.atomic():
        t.save()
        if lines is not None:
            if not isinstance(lines, list):
                raise S.ProjectsError('Lines must be a list.', 'lines')
            clean = []
            for i, x in enumerate(lines, 1):
                title = (x.get('title') or '').strip()
                if not title:
                    raise S.ProjectsError(f'Line {i}: the task name is required.', 'lines')
                try:
                    start = int(x.get('start_day') or 0)
                    dur = int(x.get('duration_days') or 1)
                    hours = S.q2(Decimal(str(x.get('hours') or 0)))
                    pct = S.q2(Decimal(str(x.get('milestone_pct') or 0)))
                except Exception:
                    raise S.ProjectsError(f'Line {i}: days, hours and % must be numbers.', 'lines')
                if start < 0 or dur < 1 or dur > 999 or start > 2000 or hours < 0 or pct < 0 or pct > 100:
                    raise S.ProjectsError(f'Line {i}: start ≥ 0, duration 1–999 days, hours ≥ 0, milestone 0–100 %.', 'lines')
                clean.append(PlanTemplateLine(template=t, sequence=i, phase=(x.get('phase') or '').strip()[:60], title=title[:200],
                                              description=x.get('description') or '', start_day=start, duration_days=dur, hours=hours,
                                              role=(x.get('role') or '').strip()[:60], is_milestone=bool(x.get('is_milestone')),
                                              milestone_pct=pct if x.get('is_milestone') else D0,
                                              customer_approval=bool(x.get('customer_approval')),
                                              is_subtask=bool(x.get('is_subtask')) and bool(clean)))
            main = None
            for c in clean:
                if not c.is_subtask:
                    main = c
                elif main is not None and (c.start_day < main.start_day or c.start_day + c.duration_days > main.start_day + main.duration_days):
                    raise S.ProjectsError(f'Line {c.sequence}: a sub-task must lie within its main task "{main.title}" '
                                          f'(day {main.start_day} for {main.duration_days} days).', 'lines')
            if sum((c.milestone_pct for c in clean), D0) > 100:
                raise S.ProjectsError('The milestones add up to more than 100 % of the contract value.', 'lines')
            t.lines.all().delete()
            PlanTemplateLine.objects.bulk_create(clean)
    return t


def apply_template(project, template, start=None, roles=None, user=None):
    """Create stages, tasks and milestones from the template. Returns counts. Existing tasks are kept."""
    Project, ProjectStage, Task, _ = _pm()
    from EmpManagement.models import emp_master
    lines = list(template.lines.all())
    if not lines:
        raise S.ProjectsError('This template has no lines.', 'template')
    start = start or project.start_date or S.local_today()
    roles = roles or {}
    people = {r: emp_master.objects.filter(pk=pk).first() for r, pk in roles.items() if pk}
    cal = sla.WorkCalendar()
    fin = S.finance(project.pk)
    contract = Decimal(fin.contract_value or 0) if fin else D0
    counts = {'stages': 0, 'tasks': 0, 'milestones': 0}
    with transaction.atomic():
        stages = {s.title.lower(): s for s in ProjectStage.objects.filter(project=project)}
        seq = (ProjectStage.objects.filter(project=project).order_by('-sequence').values_list('sequence', flat=True).first() or 0)
        phases = list(dict.fromkeys(x.phase for x in lines if x.phase)) or ['Plan']
        for ph in phases:
            if ph.lower() not in stages:
                seq += 1
                stages[ph.lower()] = ProjectStage.objects.create(project=project, title=ph[:100], sequence=seq,
                                                                 is_end_stage=(ph == phases[-1]))
                counts['stages'] += 1
        tseq = (Task.objects.filter(project=project).order_by('-sequence').values_list('sequence', flat=True).first() or 0)
        ms_seq = Milestone.objects.filter(project_id=project.pk).count()
        last_end = None
        main_task = None
        for x in lines:
            t_start = add_work_days(start, x.start_day, cal)
            t_end = add_work_days(t_start, max(0, x.duration_days - 1), cal)
            last_end = max(last_end or t_end, t_end)
            stage = stages.get((x.phase or phases[0]).lower()) or S.first_stage(project)
            tseq += 1
            task = Task.objects.create(title=x.title[:200], project=project, stage=stage, start_date=t_start, end_date=t_end,
                                       description=x.description or x.title, sequence=tseq)
            who = people.get(x.role)
            if who is not None:
                task.task_members.set([who])
                project.members.add(who)
            d, _ = TaskDetail.objects.get_or_create(task_id=task.pk)
            d.phase, d.estimated_hours, d.customer_approval = x.phase or '', x.hours, x.customer_approval
            if x.is_subtask and main_task is not None:
                d.parent_task_id = main_task.pk
            else:
                main_task = task
            d.save()
            try:
                from ProjectControl.models import TaskPlan
                TaskPlan.objects.update_or_create(task_id=task.pk, defaults={'planned_hours': x.hours})
            except Exception:
                pass
            counts['tasks'] += 1
            if who is not None:
                notify_task_assigned(task, [who], user)
            if x.is_milestone:
                ms_seq += 1
                m = Milestone.objects.create(project_id=project.pk, sequence=ms_seq, name=x.title[:200], planned_date=t_end,
                                             amount=S.q2(contract * x.milestone_pct / 100) if contract else D0,
                                             description=x.description or '')
                d.milestone = m
                d.save(update_fields=['milestone'])
                counts['milestones'] += 1
        if last_end and (not project.end_date or project.end_date < last_end):
            project.end_date = last_end
            project.save(update_fields=['end_date'])
        prof = S.ensure_profile(project)
        prof.plan_template = template
        if template.product_id and not prof.product_id:
            prof.product = template.product
        prof.save()
    return counts


def template_from_project(project, name, product=None, project_type=''):
    Project, ProjectStage, Task, _ = _pm()
    tasks = list(Task.objects.filter(project=project).select_related('stage').order_by('start_date', 'sequence', 'id'))
    if not tasks:
        raise S.ProjectsError('The project has no tasks to save.', 'project')
    det = {d.task_id: d for d in TaskDetail.objects.filter(task_id__in=[t.pk for t in tasks]).select_related('milestone')}
    ids = {t.pk for t in tasks}
    parent_of = {t.pk: det[t.pk].parent_task_id for t in tasks if t.pk in det and det[t.pk].parent_task_id in ids}
    ordered = []
    for t in tasks:
        if t.pk in parent_of:
            continue
        ordered.append(t)
        ordered += [c for c in tasks if parent_of.get(c.pk) == t.pk]
    tasks = ordered
    try:
        from ProjectControl.models import TaskPlan
        plans = dict(TaskPlan.objects.filter(task_id__in=[t.pk for t in tasks]).values_list('task_id', 'planned_hours'))
    except Exception:
        plans = {}
    fin = S.finance(project.pk)
    contract = Decimal(fin.contract_value or 0) if fin else D0
    cal = sla.WorkCalendar()
    base = project.start_date or min((t.start_date for t in tasks if t.start_date), default=S.local_today())
    lines = []
    for t in tasks:
        d = det.get(t.pk)
        st = t.start_date or base
        en = t.end_date or st
        ms = d.milestone if d and d.milestone_id else None
        lines.append({'phase': (d.phase if d and d.phase else (t.stage.title if t.stage_id else '')), 'title': t.title,
                      'description': t.description if t.description != t.title else '',
                      'start_day': work_days_between(base, st, cal), 'duration_days': max(1, work_days_between(st, en, cal) + 1),
                      'hours': float(plans.get(t.pk) or (d.estimated_hours if d else 0) or 0),
                      'role': '',
                      'is_milestone': ms is not None, 'milestone_pct': float(ms.amount * 100 / contract) if ms is not None and contract else 0,
                      'customer_approval': bool(d and d.customer_approval), 'is_subtask': t.pk in parent_of})
    total_pct = sum(x['milestone_pct'] for x in lines)
    if total_pct > 100:
        for x in lines:
            x['milestone_pct'] = round(x['milestone_pct'] * 100 / total_pct, 2)
    t = PlanTemplate()
    save_template(t, {'name': name, 'product': getattr(product, 'pk', product), 'project_type': project_type,
                      'description': f'Saved from project {project.title}', 'is_active': True, 'lines': lines})
    return t


# ------------------------------------------------------------------ task notifications
def task_page(task):
    return f'/main-sidebar/zeo-projects/projects/{task.project_id}'


def task_text(task):
    return (f'Project: {task.project.title}\nTask: {task.title}\nStart: {task.start_date or "-"}  ·  Due: {task.end_date or "-"}'
            f'\n\n{(task.description or "")[:1500]}')


def notify_task_assigned(task, members, user=None):
    for e in members:
        S.notify_emp(e, f'{task.project.title}: task "{task.title}" assigned to you', task_text(task), task, task_page(task),
                     f'Work on task {task.title}'[:255], event='task_assigned', customer=_customer(task.project_id))


def _customer(project_id):
    return ProjectProfile.objects.filter(project_id=project_id).values_list('customer_id', flat=True).first()


def overdue_tasks(today=None, every_days=7):
    """Tasks past their end date and not completed: the people on the task and the project managers get an e-mail and a
    To-do (once, then again every `every_days` days while it stays overdue)."""
    Project, _, Task, _ = _pm()
    today = today or S.local_today()
    qs = Task.objects.filter(end_date__lt=today).exclude(status='completed') \
        .exclude(project__status__in=('completed', 'cancelled', 'on_hold')).select_related('project')
    sent = 0
    for task in qs:
        d, _ = TaskDetail.objects.get_or_create(task_id=task.pk)
        if d.overdue_notified_on and (today - d.overdue_notified_on).days < every_days:
            continue
        late = (today - task.end_date).days
        text = f'This task is {late} day(s) overdue.\n\n' + task_text(task)
        cust = _customer(task.project_id)
        people = list(task.task_members.all())
        for e in people:
            S.notify_emp(e, f'Overdue: {task.title} ({task.project.title})', text, task, task_page(task),
                         f'Overdue task {task.title}'[:255], event='task_overdue', customer=cust)
        for e in task.project.managers.all():
            if e not in people:
                S.notify_emp(e, f'Overdue task in {task.project.title}: {task.title}', text, task, task_page(task),
                             f'Overdue task {task.title}'[:255], event='task_overdue', customer=cust)
        d.overdue_notified_on = today
        d.save(update_fields=['overdue_notified_on'])
        sent += 1
    return sent


# ------------------------------------------------------------------ default templates (v1.15.0)
# (phase, task, start working day, working days, hours, role, milestone % of the value, customer approval[, sub-task])
IT_IMPLEMENTATION = [
    ('System study', 'Kick-off meeting and project charter', 0, 1, 6, 'Project manager', 0, True),
    ('System study', 'Study current processes – department interviews', 1, 5, 0, 'Functional consultant', 0, False),
    ('System study', 'Finance and accounts', 1, 2, 12, 'Functional consultant', 0, False, True),
    ('System study', 'Sales and purchase', 2, 2, 10, 'Functional consultant', 0, False, True),
    ('System study', 'Inventory and warehouse', 3, 2, 10, 'Functional consultant', 0, False, True),
    ('System study', 'HR, admin and management reports', 5, 1, 8, 'Functional consultant', 0, False, True),
    ('System study', 'Collect sample documents, reports and master data', 2, 4, 12, 'Functional consultant', 0, False),
    ('System study', 'Gap analysis (requirements vs standard system)', 6, 3, 20, 'Functional consultant', 0, False),
    ('System study', 'System study report (SRS) – customer sign-off', 9, 2, 10, 'Project manager', 10, True),
    ('Solution design', 'Solution design / business blueprint', 11, 4, 24, 'Functional consultant', 0, False),
    ('Solution design', 'Customisation and report specifications', 13, 3, 16, 'Technical consultant', 0, False),
    ('Solution design', 'Integration and data migration plan', 14, 2, 8, 'Technical consultant', 0, False),
    ('Solution design', 'Solution design – customer sign-off', 16, 1, 4, 'Project manager', 15, True),
    ('Configuration', 'Server / cloud environment setup', 11, 2, 8, 'Infrastructure', 0, False),
    ('Configuration', 'Company, branches, users and access rights', 17, 2, 8, 'Functional consultant', 0, False),
    ('Configuration', 'Configure modules', 17, 10, 0, 'Functional consultant', 0, False),
    ('Configuration', 'Finance, chart of accounts and UAE VAT', 17, 4, 24, 'Functional consultant', 0, False, True),
    ('Configuration', 'Sales and CRM', 19, 3, 12, 'Functional consultant', 0, False, True),
    ('Configuration', 'Purchase', 21, 2, 8, 'Functional consultant', 0, False, True),
    ('Configuration', 'Inventory and warehouses', 22, 5, 16, 'Functional consultant', 0, False, True),
    ('Configuration', 'Conference room pilot (CRP) with key users', 27, 2, 12, 'Functional consultant', 20, True),
    ('Development', 'Customisations development', 17, 15, 90, 'Developer', 0, False),
    ('Development', 'Reports and document layouts (invoice, LPO, delivery note …)', 20, 8, 40, 'Developer', 0, False),
    ('Development', 'Integrations with other systems', 22, 8, 32, 'Developer', 0, False),
    ('Development', 'Internal testing (QA) of developments', 32, 3, 16, 'Technical consultant', 0, False),
    ('Data migration', 'Send master data templates to the customer', 17, 1, 4, 'Data migration', 0, False),
    ('Data migration', 'Data collection and cleansing (customer)', 18, 10, 0, 'Customer', 0, False),
    ('Data migration', 'Trial import of masters', 28, 3, 16, 'Data migration', 0, False),
    ('Data migration', 'Opening balances and open transactions – trial import', 35, 3, 16, 'Data migration', 0, False),
    ('Testing', 'UAT test scripts', 33, 2, 10, 'Functional consultant', 0, False),
    ('Testing', 'User acceptance testing (UAT) with key users', 36, 6, 36, 'Functional consultant', 0, False),
    ('Testing', 'Fix UAT issues', 38, 5, 24, 'Developer', 0, False),
    ('Testing', 'UAT – customer sign-off', 43, 1, 4, 'Project manager', 25, True),
    ('Training', 'Key-user training', 36, 4, 24, 'Trainer', 0, False),
    ('Training', 'End-user training and user manuals', 41, 4, 24, 'Trainer', 0, False),
    ('Go-live', 'Go-live readiness check and cut-over plan', 44, 1, 6, 'Project manager', 0, True),
    ('Go-live', 'Final data migration and cut-over', 45, 2, 16, 'Data migration', 0, False),
    ('Go-live', 'Go-live', 47, 1, 8, 'Project manager', 20, True),
    ('Hyper-care', 'Post go-live support (hyper-care)', 48, 10, 40, 'Functional consultant', 0, False),
    ('Hyper-care', 'Project closure and handover to support / AMC', 58, 1, 4, 'Project manager', 10, True),
]
DEFAULT_TEMPLATES = [
    ('IT implementation – System study to Go-live', 'implementation',
     'Standard plan for an ERP / business software implementation: system study, solution design, configuration, '
     'development, data migration, UAT, training, go-live and hyper-care. Milestones: study 10 %, design 15 %, '
     'CRP 20 %, UAT 25 %, go-live 20 %, closure 10 %. About 12 weeks – change it to each project.', IT_IMPLEMENTATION),
]


def install_default_templates():
    """Create the default templates that do not exist yet (by name). Returns the names created."""
    made = []
    for name, ptype, desc, lines in DEFAULT_TEMPLATES:
        if PlanTemplate.objects.filter(name__iexact=name).exists():
            continue
        save_template(PlanTemplate(), {
            'name': name, 'project_type': ptype, 'description': desc, 'is_active': True,
            'lines': [{'phase': x[0], 'title': x[1], 'start_day': x[2], 'duration_days': x[3], 'hours': x[4], 'role': x[5],
                       'is_milestone': bool(x[6]), 'milestone_pct': x[6], 'customer_approval': x[7],
                       'is_subtask': len(x) > 8 and x[8]} for x in lines]})
        made.append(name)
    return made
