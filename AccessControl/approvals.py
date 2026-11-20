"""
One set of rules for every approval step in every module (v1.7.0).

Approval records (LeaveApproval, Approval, DocumentApproval, LoanApproval, AssetApproval, PayslipApproval,
AdvanceSalaryApproval, AirticketApproval, ResignationApproval, LateinEarlyoutApproval …) all have an
`approver` and a `status`. Several approve / reject actions had their checks commented out, so:
  * anyone who could open a step could approve it, also after it was rejected or already approved;
  * an employee could approve their own request (some modules even make the requester the approver);
  * a PATCH of `status` on the step skipped the next approval level.
Rules now (company admins keep full rights except approving their own request):
  * approve / reject / delegate only while the step is pending (or escalated);
  * only the step's approver or the person it was delegated to;
  * never on your own request;
  * the step's status cannot be edited directly – use Approve / Reject.
"""
from django.db import models

ACTIONS = {'approve', 'reject', 'delegate', 'deligate'}
OPEN = ('pend', 'escalat')


def is_approval_step(model):
    names = {f.name for f in model._meta.concrete_fields}
    return 'approver' in names and 'status' in names and model._meta.model_name.endswith(('approval', 'approvals'))


def _requester_users(obj):
    """Users who raised the request this step belongs to."""
    from .access import emp_path
    users = set()
    for f in obj._meta.concrete_fields:
        if not isinstance(f, models.ForeignKey) or f.name in ('approver', 'deligate_to', 'delegate_to', 'delegate', 'created_by', 'updated_by'):
            continue
        target = getattr(obj, f.name, None)
        if target is None:
            continue
        ep = emp_path(type(target))
        if ep is None:
            continue
        emp = target
        try:
            for part in [p for p in ep.split('__') if p]:
                emp = getattr(emp, part, None)
        except Exception:
            emp = None
        uid = getattr(emp, 'users_id', None) if emp is not None else None
        if uid:
            users.add(uid)
    return users


def _delegate_problem(request):
    """v1.73.19 (item 5): a step may be delegated only to an active login of this company (any user id of the platform was taken)."""
    data = request.data if hasattr(request, 'data') else {}
    uid = None
    for k in ('deligate_to', 'delegate_to', 'delegate', 'delegate_user', 'delegate_id'):
        if isinstance(data, dict) and data.get(k) not in (None, ''):
            uid = data.get(k)
            break
    if uid is None:
        return ''
    from django.db import connection
    from UserManagement.models import CustomUser
    try:
        ok = CustomUser.objects.filter(pk=int(uid), is_active=True, tenants__schema_name=connection.schema_name).exists()
    except (TypeError, ValueError):
        ok = False
    return '' if ok else 'Delegate to an active user of this company.'


def check(request, view, obj, c):
    """Error message when the user may not act on this approval step, else ''."""
    model = type(obj)
    if not is_approval_step(model):
        return ''
    action = (getattr(view, 'action', '') or '').lower()
    status = str(getattr(obj, 'status', '') or '').lower()
    if action in ('update', 'partial_update'):
        data = request.data if hasattr(request, 'data') else {}
        if not c.admin and isinstance(data, dict) and 'status' in data and str(data.get('status', '')).lower() != status:
            return 'Use Approve or Reject: the status of an approval step cannot be edited directly.'
        return ''
    if action not in ACTIONS:
        return ''
    if not status.startswith(OPEN):
        return f'This approval step is already {getattr(obj, "status", "closed")}.'
    if getattr(obj, 'escalated', False):
        # v1.73.19 (item 5): an escalated step was handed to someone else (a new step was made for them); deciding the old one as well
        # moved the request on while the new step kept waiting
        return 'This approval step was escalated – the person it was escalated to decides now.'
    if action in ('delegate', 'deligate'):
        msg = _delegate_problem(request)
        if msg:
            return msg
    uid = request.user.id
    if uid in _requester_users(obj):
        return 'You cannot approve or reject your own request.'
    if c.admin:
        return ''
    actors = {getattr(obj, f, None) for f in ('approver_id', 'deligate_to_id', 'delegate_to_id', 'delegate_id')}
    if uid not in actors:
        return 'Only the approver of this step (or the person it was delegated to) can act on it.'
    return ''


# ------------------------------------------------------------------ v1.73.19 (item 5) the approver of a new step
STEP_MODELS = {'OrganisationManager.AssetApproval', 'EmpManagement.Approval', 'EmpManagement.DocumentApproval', 'EmpManagement.ResignationApproval',
               'calendars.LateinEarlyoutApproval', 'PayrollManagement.LoanApproval', 'PayrollManagement.AdvanceSalaryApproval',
               'PayrollManagement.AirticketApproval'}
SKIP_FK = ('approver', 'deligate_to', 'delegate_to', 'delegate', 'created_by', 'updated_by', 'escalate_to')
_QUIET = {}


def _parent(obj):
    """(fk name, request, employee) of an approval step."""
    from .access import emp_path, _models
    Emp = _models()[0]
    best = None
    for f in obj._meta.concrete_fields:
        if not isinstance(f, models.ForeignKey) or f.name in SKIP_FK:
            continue
        target = getattr(obj, f.name, None)
        if target is None:
            continue
        ep = emp_path(type(target))
        if ep is None:
            continue
        emp = target
        for part in [p for p in ep.split('__') if p]:
            emp = getattr(emp, part, None)
        if emp is None:
            continue
        if type(target) is not Emp:
            return f.name, target, emp
        best = best or (None, None, emp)
    return best or (None, None, None)


def quiet(user_id):
    """True once when a "waiting for your approval" notice to this user belongs to a step approved with his earlier decision."""
    import time
    t = _QUIET.pop(user_id, None)
    return bool(t and time.time() - t < 10)


def step_pre_save(sender, instance, **kwargs):
    """A new waiting step goes to someone who can decide it: the named approver while active in this company and not the employee
    himself, else branch HR → company HR → company administrator (LeavePolicy.approvers.pick). A person who already approved an
    earlier level of the same request is not asked again: the step is saved approved with his earlier decision."""
    if instance.pk or sender._meta.label not in STEP_MODELS:
        return
    if not str(getattr(instance, 'status', '') or '').lower().startswith('pend'):
        return
    try:
        fk, parent, emp = _parent(instance)
        if emp is None:
            return
        from LeavePolicy.approvers import pick
        u, how = pick(getattr(instance, 'approver_id', None), emp)
        if u is not None and u.pk != getattr(instance, 'approver_id', None):
            instance.approver = u
        if not fk or getattr(instance, 'is_escalation', False) or not instance.approver_id:
            return
        prev = sender.objects.filter(**{fk: parent, 'approver_id': instance.approver_id, 'status__iexact': 'approved'})
        lvl = getattr(instance, 'level', None)
        if lvl is not None:
            prev = prev.filter(level__lt=lvl)
        p = prev.order_by('level').first()
        if p is None:
            return
        import time
        instance.status = getattr(sender, 'APPROVED', 'Approved')
        instance.note = f'Approved at level {p.level} by the same person'
        instance._zt_same = parent
        _QUIET[instance.approver_id] = time.time()
    except Exception:   # noqa: BLE001
        import logging
        logging.getLogger(__name__).exception('approval step approver check failed')


def step_post_save(sender, instance, created, **kwargs):
    parent = getattr(instance, '_zt_same', None)
    if not created or parent is None:
        return
    instance._zt_same = None
    nxt = getattr(parent, 'move_to_next_level', None)
    if callable(nxt):
        nxt()


def install_steps():
    from django.db.models.signals import pre_save, post_save
    from django.apps import apps
    for lab in STEP_MODELS:
        try:
            m = apps.get_model(lab)
        except LookupError:
            continue
        pre_save.connect(step_pre_save, sender=m, dispatch_uid=f'v17319_step_pre_{lab}')
        post_save.connect(step_post_save, sender=m, dispatch_uid=f'v17319_step_post_{lab}')
