"""
Role-based leave approvers and escalation (v1.11.0).

A leave approval level can name a role instead of a fixed user: reporting manager, the reporting manager's manager,
the branch HR manager, the department head or the company HR manager. Who holds a role is set once in
Leave policies → Approvers. When the person for a level is missing (left the company, user deleted, the employee
would approve their own leave), the request goes to the next one in line – branch HR, company HR, then a company
admin – instead of being approved automatically as before.
"""
import logging
from datetime import timedelta

from django.apps import apps
from django.utils import timezone

log = logging.getLogger(__name__)
M = apps.get_model


def _user(uid):
    U = M('UserManagement', 'CustomUser')
    return U.objects.filter(pk=uid, is_active=True).first() if uid else None


def _manager_user(emp):
    """(manager's employee record, manager's active user). The reporting manager is stored as a user."""
    u = getattr(emp, 'emp_reporting_manager', None) if emp else None
    if u is None:
        return None, None
    E = M('EmpManagement', 'emp_master')
    m = E.objects.filter(users=u).first() or E.objects.filter(emp_code=u.username).first()
    return m, (u if u.is_active else None)


def holder(role, emp):
    """Active user holding `role` for the employee, or None."""
    from .models import OrgRole
    if emp is None:
        return None
    if role == 'reporting_manager':
        return _manager_user(emp)[1]
    if role == 'manager_of_manager':
        m, _ = _manager_user(emp)
        return _manager_user(m)[1] if m else None
    if role == 'branch_hr':
        r = OrgRole.objects.filter(role='branch_hr', branch_id=emp.emp_branch_id_id).first()
        return _user(r.user_id) if r else None
    if role == 'department_head':
        r = (OrgRole.objects.filter(role='department_head', department_id=emp.emp_dept_id_id, branch_id=emp.emp_branch_id_id).first()
             or OrgRole.objects.filter(role='department_head', department_id=emp.emp_dept_id_id, branch_id__isnull=True).first())
        return _user(r.user_id) if r else None
    if role == 'company_hr':
        r = OrgRole.objects.filter(role='company_hr').first()
        return _user(r.user_id) if r else None
    return None


def _own_user_ids(emp):
    ids = set()
    if getattr(emp, 'users_id', None):
        ids.add(emp.users_id)
    U = M('UserManagement', 'CustomUser')
    ids |= set(U.objects.filter(username=emp.emp_code).values_list('pk', flat=True))
    return ids


def fallback_chain(emp):
    yield 'branch_hr', holder('branch_hr', emp)
    yield 'company_hr', holder('company_hr', emp)
    U = M('UserManagement', 'CustomUser')
    yield 'admin', U.objects.filter(is_active=True, is_superuser=True).order_by('pk').first()


def resolve(level, emp, exclude_ids=()):
    """(user, how) for an approval level; `level` None = reporting-manager workflow. Never the employee themselves."""
    from .models import LevelRole
    own = _own_user_ids(emp) | set(exclude_ids)
    tries = []
    lr = LevelRole.objects.filter(level_id=level.pk).first() if level is not None else None
    if level is None:
        tries.append(('reporting_manager', holder('reporting_manager', emp)))
    elif lr and lr.role != 'user':
        tries.append((lr.role, holder(lr.role, emp)))
    else:
        tries.append(('user', _user(getattr(level, 'approver_id', None))))   # fresh: the user may have been deactivated
    tries += list(fallback_chain(emp))
    for how, u in tries:
        if u is not None and u.pk not in own:
            return u, how
    return None, 'none'


# ------------------------------------------------------------------ escalation
def _level_for(approval):
    req = approval.leave_request
    if req is None:
        return None
    L = M('calendars', 'LeaveApprovalLevels')
    return L.objects.filter(workflow__request_type=req.leave_type, workflow__branch=req.employee.emp_branch_id, level=approval.level).first()


def escalate_due(now=None, dry=False):
    """Escalate pending leave approvals whose level's waiting time has passed. Returns what was (or would be) done."""
    from .models import ApprovalClock, LevelRole
    LA = M('calendars', 'LeaveApproval')
    now = now or timezone.now()
    out = []
    for a in LA.objects.filter(status=LA.PENDING, escalated=False, is_escalation=False, leave_request__isnull=False,
                               leave_request__status='pending').select_related('leave_request', 'leave_request__employee', 'approver'):
        lvl = _level_for(a)
        if lvl is None:
            continue
        wait = lvl.get_escalation_timedelta()
        if wait <= timedelta(0):
            continue
        clock = ApprovalClock.objects.filter(approval_id=a.pk).first()
        started = clock.started_at if clock else timezone.make_aware(timezone.datetime.combine(a.created_at, timezone.datetime.min.time()))
        if started + wait > now:
            continue
        emp = a.leave_request.employee
        lr = LevelRole.objects.filter(level_id=lvl.pk).first()
        target, how = (lvl.escalate_to, 'escalate_to') if lvl.escalate_to_id else (None, '')
        if lr and lr.escalate_role:
            target, how = holder(lr.escalate_role, emp), lr.escalate_role
        if target is None or target.pk == a.approver_id or target.pk in _own_user_ids(emp):
            target, how = None, 'none'
            for h, u in fallback_chain(emp):
                if u is not None and u.pk != a.approver_id and u.pk not in _own_user_ids(emp):
                    target, how = u, h
                    break
        row = {'approval_id': a.pk, 'document': a.leave_request.document_number, 'from': a.approver.username,
               'to': target.username if target else None, 'how': how, 'waited_hours': round((now - started).total_seconds() / 3600, 1)}
        out.append(row)
        if dry or target is None:
            continue
        old = a.approver
        a.status, a.escalated, a.escalated_at = LA.ESCALATED, True, now
        a.note = f'Escalated to {target.username} after {row["waited_hours"]:g} h'
        a.save()
        new = LA.objects.create(leave_request=a.leave_request, approver=target, level=a.level, employee_id=a.employee_id,
                                status=LA.PENDING, note=f'Escalated from {old.username}', is_escalation=True, created_by=old)
        ApprovalClock.objects.update_or_create(approval_id=a.pk, defaults={'started_at': started, 'escalated_at': now})
        try:
            from EmpManagement.utils import send_notification_email, get_employee_context
            send_notification_email(user=target, employee=None, branch=emp.emp_branch_id, title='Request Escalated', notification_type='lv_request',
                                    message=f'Leave request {a.leave_request.document_number} ({a.leave_request.leave_type.name}) of {emp.emp_first_name} '
                                            f'was escalated to you – waiting since {started:%d/%m/%Y %H:%M}.',
                                    template_type='request_created', context={**get_employee_context(emp), 'request_type': a.leave_request.leave_type.name},
                                    email_template_model=M('calendars', 'LvEmailTemplate'), notification_model=M('calendars', 'LvApprovalNotify'))
        except Exception:
            log.exception('escalation notice failed for approval %s', new.pk)
    return out
