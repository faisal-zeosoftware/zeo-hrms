"""v1.58.0 – registers of concurrent assignments and matrix reporting (see matrix.py for the rules).

Rights
  manage  the HR team (HR operations manager / HR officer, or users who may change employees)
  view    HR operations viewers and users who may see employees
  branch  HR users see the assignments of their branches – the employee's home branch and the host branch of the assignment
  own     the employee sees their own assignments and functional manager; the manager of an assignment sees it;
          a functional manager sees and gives their functional reviews
"""
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from zeo.appkit.engine import Act, AppError, Reg, branch_ids, d, emp_name, now, user_name

from Translations.i18n import tr

from . import matrix as MX
from .app import MANAGE, VIEW, app, hr, link
from .models import Assignment, FunctionalReview, MatrixLink


def _model(app_label, name):
    from django.apps import apps
    return apps.get_model(app_label, name)


app.option('departments', lambda: _model('OrganisationManager', 'dept_master').objects.filter(dept_is_active=True).order_by('dept_name'),
           lambda x: x.dept_name, ('dept_name', 'dept_code'), _model('OrganisationManager', 'dept_master'))
app.option('designations', lambda: _model('OrganisationManager', 'desgntn_master').objects.filter(desgntn_is_active=True).order_by('desgntn_job_title'),
           lambda x: x.desgntn_job_title, ('desgntn_job_title', 'desgntn_code'), _model('OrganisationManager', 'desgntn_master'))
app.option('projects', lambda: _model('ProjectManagement', 'Project').objects.filter(is_active=True).order_by('title'),
           lambda x: x.title, ('title',), _model('ProjectManagement', 'Project'))
app.option('goal-sheets', lambda: _model('PerformanceManagement', 'GoalSheet').objects.filter(cycle__status__in=('active', 'calibration')).select_related('employee', 'cycle'),
           lambda x: f'{x.cycle.name} – {emp_name(x.employee)}', ('employee__emp_first_name', 'employee__emp_last_name', 'cycle__name'),
           _model('PerformanceManagement', 'GoalSheet'))


# ================================================================== assignments
def asg_scope(qs, request):
    """HR users of a branch see the assignments of employees of that branch and the assignments hosted by it."""
    b = branch_ids(request)
    if b is None:
        return qs
    return qs.filter(Q(host_branch_id__in=b) | Q(host_branch__isnull=True, employee__emp_branch_id__in=b) | Q(employee__emp_branch_id__in=b))


def asg_defaults(o, request):
    o.created_by = request.user
    if not o.start_date:
        o.start_date = timezone.localdate()


def asg_check(o, request, old):
    if o.percent is None or not (Decimal('1') <= Decimal(o.percent) <= Decimal('100')):
        raise AppError('Give the share of time between 1 and 100 %.', 'percent')
    if o.end_date and o.start_date and o.end_date < o.start_date:
        raise AppError('The end date is before the start date.', 'end_date')
    if not (o.host_branch_id or o.department_id or o.project_id or o.designation_id):
        raise AppError('Say where the assignment is: a branch, a department, a project or a role.', 'department')
    if o.kind == 'project' and not o.project_id:
        raise AppError('Choose the project of the project assignment.', 'project')
    e = o.employee
    if o.manager_id and e is not None and getattr(e, 'users_id', None) == o.manager_id:
        raise AppError('The employee cannot be their own manager on the assignment.', 'manager')
    if o.status != 'cancelled':
        peak, others = MX.peak_share(o.employee_id, o.start_date, o.end_date, o.percent, exclude=o.pk)
        if peak > 100:
            used = ', '.join(f'{x.number} {x.percent.normalize():f}%' for x in others)
            raise AppError(tr('The employee would work {0}% of the time on assignments (already: {1}). Lower the share or end the other assignment first.',
                              MX.pct(peak), used), 'percent')
        same = [x for x in others if x.department_id == o.department_id and x.project_id == o.project_id and x.host_branch_id == o.host_branch_id]
        if same:
            raise AppError(tr('{0} already places the employee there for these dates.', same[0].number), 'department')
        o.status = MX.status_for(o)


def asg_after(o, request, old):
    app.event('assignment', o.pk, f'{o.get_kind_display()} – {o.percent.normalize():f}% from {d(o.start_date)}' + (f' to {d(o.end_date)}' if o.end_date else ''), request.user)
    if o.manager_id and (old is None or old.manager_id != o.manager_id):
        app.todo(o.manager, f'You manage {emp_name(o.employee)} on {o.number}', 'assignment', o.pk, o.number, link('assignment', o.pk),
                 f'{o.get_kind_display()}, {o.percent.normalize():f}% from {d(o.start_date)}')
    if o.manager_approves and o.manager_id:
        MX.open_reviews([o.employee_id])


def a_end(o, request, f):
    end = f.dt('date') or timezone.localdate()
    if end < o.start_date:
        raise AppError('The end date is before the start date.')
    o.end_date = end
    o.status = MX.status_for(o)
    if o.status == 'ended':
        o.ended_at = now()
    o.save()
    app.event('assignment', o.pk, f'Ends on {d(end)}', request.user)
    return o


def a_cancel(o, request, f):
    o.status = 'cancelled'
    o.save(update_fields=['status'])
    app.event('assignment', o.pk, 'Cancelled' + (f' – {f.s("reason")}' if f.s('reason') else ''), request.user)
    app.close_todos('assignment', o.pk)
    return o


def asg_extra(o, r):
    return {'main_share': MX.pct(MX.main_share(o.employee_id)), 'home': {
        'branch': str(o.employee.emp_branch_id) if o.employee.emp_branch_id_id else '',
        'department': str(o.employee.emp_dept_id) if o.employee.emp_dept_id_id else '',
        'designation': str(o.employee.emp_desgntn_id) if o.employee.emp_desgntn_id_id else '',
        'reporting_manager': user_name(o.employee.emp_reporting_manager) if o.employee.emp_reporting_manager_id else ''}}


app.reg(Reg('assignment', Assignment, 'Assignment', 'Assignments and secondments', 'Assignments',
            cols=('number', 'employee', 'kind', 'host_branch', 'department', 'project', 'manager', 'percent', 'start_date', 'end_date', 'status'),
            form=('employee', 'kind', 'host_branch', 'department', 'designation', 'project', 'manager', 'percent', 'start_date', 'end_date',
                  'cost_split', 'manager_approves', 'reason', 'notes'),
            number=('assignment', 'ASG'), manage=MANAGE, view=VIEW, own=(('employee', 'emp'), ('manager', 'user')), scope_fn=asg_scope,
            search=('number', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code', 'reason'),
            filters=('status', 'kind', 'host_branch', 'department', 'employee'), title='number', subtitle='employee', defaults=asg_defaults,
            check=asg_check, saver=lambda o, rq, creating, old: (o.save(), asg_after(o, rq, old))[0] or o, edit_when=lambda o: o.status in ('planned', 'active'), info=('status', 'ended_at'), extra=asg_extra,
            actions=[Act('end', 'End the assignment', a_end, lambda o, r: o.status in ('planned', 'active') and hr(r), style='primary',
                         fields=(('date', 'Last day', 'date', True),)),
                     Act('cancel', 'Cancel', a_cancel, lambda o, r: o.status == 'planned' and hr(r), style='danger',
                         fields=(('reason', 'Why', 'text', False),))],
            help='A second place where the employee works at the same time – a secondment to another branch or department, a second role, '
                 'a project or an acting role – with a share of the time, a manager and dates. The rest of the time is the main job on the '
                 'employee record. The share of the salary cost goes to the department or project in the payroll cost allocation; the '
                 'manager can approve as functional manager.'))


# ================================================================== functional (dotted-line) managers
def ml_defaults(o, request):
    o.created_by = request.user


def ml_check(o, request, old):
    e = o.employee
    if e is not None and o.functional_manager_id and o.functional_manager_id == getattr(e, 'emp_reporting_manager_id', None):
        raise AppError('The functional manager is the reporting manager already – choose another person.', 'functional_manager')
    if e is not None and getattr(e, 'users_id', None) == o.functional_manager_id:
        raise AppError('The employee cannot be their own functional manager.', 'functional_manager')
    if o.review_weight is None or not (Decimal('0') <= Decimal(o.review_weight) <= Decimal('50')):
        raise AppError('Give the appraisal weight between 0 and 50 %.', 'review_weight')
    if o.valid_until and o.valid_from and o.valid_until < o.valid_from:
        raise AppError('"Until" is before "from".', 'valid_until')
    dup = MatrixLink.objects.filter(employee_id=o.employee_id).exclude(pk=o.pk).first()
    if dup is not None:
        raise AppError(tr('{0} has a functional manager already ({1}) – change that one.', emp_name(e), user_name(dup.functional_manager)), 'employee')


def ml_after(o, request, old):
    app.event('matrix', o.pk, f'Functional manager {user_name(o.functional_manager)}', request.user)
    MX.open_reviews([o.employee_id])


def ml_extra(o, r):
    e = o.employee
    return {'reporting_manager': user_name(e.emp_reporting_manager) if e.emp_reporting_manager_id else '',
            'department': str(e.emp_dept_id) if e.emp_dept_id_id else ''}


app.reg(Reg('matrix', MatrixLink, 'Functional manager', 'Functional (dotted-line) managers', 'Assignments',
            cols=('employee', 'functional_manager', 'area', 'review_weight', 'valid_from', 'valid_until'),
            form=('employee', 'functional_manager', 'area', 'review_weight', 'valid_from', 'valid_until', 'notes'),
            manage=MANAGE, view=VIEW, own=(('employee', 'emp'), ('functional_manager', 'user')),
            search=('employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code', 'area'), filters=('functional_manager', 'employee'),
            title='employee', subtitle='functional_manager', defaults=ml_defaults, check=ml_check, extra=ml_extra, deletable=True,
            saver=lambda o, rq, creating, old: (o.save(), ml_after(o, rq, old))[0] or o,
            help='A second, dotted-line manager besides the reporting manager – e.g. the finance head for a site accountant. The functional '
                 'manager can approve requests (approval level "Functional manager"), sees the person under My team › Dotted line and gives '
                 'input on the appraisal with the weight set here; the reporting manager stays the final reviewer.'))


# ================================================================== functional reviews (appraisal input)
def fr_check(o, request, old):
    if o.rating is not None and not 1 <= int(o.rating) <= 5:
        raise AppError('Give a rating from 1 to 5.', 'rating')
    if o.sheet_id and not o.employee_id:
        o.employee_id = o.sheet.employee_id


def a_fr_submit(o, request, f):
    try:
        r = int(f.s('rating'))
    except (TypeError, ValueError):
        r = None
    if r is None or not 1 <= r <= 5:
        raise AppError('Give a rating from 1 to 5.')
    o.rating, o.strengths, o.improvements = r, f.s('strengths')[:5000], f.s('improvements')[:5000]
    o.status, o.submitted_at = 'submitted', now()
    o.save()
    app.event('functional_review', o.pk, f'Rating {r} given', request.user)
    app.close_todos('functional_review', o.pk)
    if o.sheet.manager_id:
        app.tell([o.sheet.manager], f'Functional input on {emp_name(o.employee)}', f'{user_name(o.reviewer)} rated {r}/5 – it shows in your manager review.',
                 'functional_review', o.pk, emp_name(o.employee), link('functional_review', o.pk))
    return o


def a_fr_skip(o, request, f):
    o.status = 'skipped'
    o.save(update_fields=['status'])
    app.close_todos('functional_review', o.pk)
    return o


def fr_mine(r):
    return Q(reviewer=r.user)


def fr_extra(o, r):
    s = o.sheet
    goals = [{'title': g.title, 'weight': str(g.weight), 'target': g.target or '', 'progress': g.progress_percent, 'self_rating': g.self_rating}
             for g in s.goals.all()]
    return {'cycle': s.cycle.name, 'sheet_status': s.get_status_display(), 'goals': goals, 'self_score': str(s.self_score) if s.self_score is not None else '',
            'can_give': o.status == 'pending' and (r.user.pk == o.reviewer_id or hr(r))}


app.reg(Reg('functional_review', FunctionalReview, 'Functional review', 'Functional reviews', 'Assignments',
            cols=('employee', 'sheet', 'reviewer', 'rating', 'weight', 'status', 'submitted_at'),
            form=('sheet', 'reviewer', 'weight'), create='manage',
            manage=MANAGE, view=VIEW, public=fr_mine,   # not the employee: input before the appraisal is final
            search=('employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'), filters=('status', 'reviewer'),
            title='employee', subtitle='sheet', check=fr_check, extra=fr_extra, info=('rating', 'strengths', 'improvements', 'status', 'submitted_at'),
            edit_when=lambda o: o.status == 'pending',
            actions=[Act('give', 'Give my input', a_fr_submit, lambda o, r: o.status == 'pending' and (r.user.pk == o.reviewer_id or hr(r)), style='primary',
                         fields=(('rating', 'Rating (1–5)', 'choice:1=1 – Needs improvement|2=2|3=3 – Meets expectations|4=4|5=5 – Exceptional', True),
                                 ('strengths', 'Strengths', 'textarea', False), ('improvements', 'To improve', 'textarea', False))),
                     Act('skip', 'Skip', a_fr_skip, lambda o, r: o.status == 'pending' and (r.user.pk == o.reviewer_id or hr(r)))],
            help="The functional manager's input on an appraisal. It is shown to the reporting manager with a blended score "
                 '(the weight of the functional manager link); the reporting manager stays the final reviewer.'))
