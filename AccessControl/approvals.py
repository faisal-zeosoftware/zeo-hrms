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
    uid = request.user.id
    if uid in _requester_users(obj):
        return 'You cannot approve or reject your own request.'
    if c.admin:
        return ''
    actors = {getattr(obj, f, None) for f in ('approver_id', 'deligate_to_id', 'delegate_to_id', 'delegate_id')}
    if uid not in actors:
        return 'Only the approver of this step (or the person it was delegated to) can act on it.'
    return ''
