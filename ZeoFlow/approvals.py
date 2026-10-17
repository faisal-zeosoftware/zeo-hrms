"""The approval engine: levels, approver by user / manager / role / department head / a user field, sequential or
parallel levels, amount-based routing (a level only above an amount), delegation, deadlines, reminders and escalation,
rejection with comment, changes requested and resubmission. Used by the 'Submit for approval' action or on its own
(POST /flow/api/approvals/submit/)."""
import datetime
import decimal
import logging

from django.db import transaction
from django.utils import timezone

from . import registry as R

log = logging.getLogger(__name__)


class Refused(Exception):
    pass


def _users_for(ap, obj, flow, submitter):
    """{type: user|manager|role|department_head|field|creator_manager|submitter_manager, user, group, field} → users."""
    from . import engine as E
    from UserManagement.models import CustomUser
    t = ap.get('type')
    ctx = {'record': obj}
    if t == 'user' and ap.get('user'):
        u = CustomUser.objects.filter(pk=ap['user'], is_active=True).first()
        return [u] if u else []
    if t == 'role' and ap.get('group'):
        return E._group_users(ap['group'])
    if t == 'manager':
        u = E._manager_user(obj)
        return [u] if u else []
    if t == 'submitter_manager' and submitter is not None:
        from EmpManagement.models import emp_master
        emp = emp_master.objects.filter(users=submitter).first()
        mgr = getattr(emp, 'emp_reporting_manager', None) if emp else None
        return [mgr] if mgr else []
    if t == 'department_head':
        emp = E._employee_of(obj)
        dept = getattr(emp, 'emp_dept_id', None) if emp else None
        head = getattr(dept, 'dept_head', None) or getattr(dept, 'head', None) if dept is not None else None
        if head is not None:
            return [getattr(head, 'users', head)] if getattr(head, 'users', None) or isinstance(head, CustomUser) else []
        return []
    if t == 'field' and ap.get('field'):
        users, _ = E.resolve_recipients([{'type': 'field', 'value': ap['field']}], ctx)
        return users
    if t == 'owner':
        u = E._owner_user(obj)
        return [u] if u else []
    return []


def _amount(flow, step, obj):
    f = step.get('amount_field') or ''
    if not f:
        return None
    from . import engine as E
    try:
        return decimal.Decimal(str(E.get_value(obj, f) or 0))
    except Exception:
        return None


def _set_status(req, value):
    flow = req.flow
    if not flow.status_field or not value:
        return
    model = R.get_model(req.model)
    obj = model._default_manager.filter(pk=req.object_id).first() if model else None
    if obj is None:
        return
    from . import engine as E
    try:
        setattr(obj, flow.status_field, E._coerce(model, flow.status_field, value, {'record': obj}))
        E._save_quiet(obj, [flow.status_field])
    except Exception:
        log.exception('approval status field %s', flow.status_field)


def _notify(users, title, message, req):
    from .models import FlowNotification
    for u in users:
        if u is None:
            continue
        FlowNotification.objects.create(recipient=u, title=title[:200], message=message[:2000], link=req.record_link or R.record_link(req.model, req.object_id),
                                        model=req.model, object_id=str(req.object_id))
        try:
            if u.email:
                from django.core.mail import send_mail
                send_mail(title, message + ('\n\n' + req.record_link if req.record_link else ''), None, [u.email], fail_silently=True)
        except Exception:
            pass


def _activate_level(req, level):
    """Make the steps of a level pending; skip a level whose amount threshold is not reached or that has no approver."""
    from .models import ApprovalStep
    steps = list(req.steps.filter(level=level))
    if not steps:
        return False
    now = timezone.now()
    for s in steps:
        s.status, s.activated_at = 'pending', now
        s.save(update_fields=['status', 'activated_at'])
    _notify([s.delegate or s.approver for s in steps], f'Approval needed: {req.record_label}',
            f'{req.record_label} is waiting for your approval (level {level}: {steps[0].level_name or ""}). {req.note or ""}'.strip(), req)
    return True


def submit(flow_id, obj, user=None, note='', callbacks=None, run=None, resubmitted_from=None):
    from .models import ApprovalFlow, ApprovalRequest, ApprovalStep
    flow = ApprovalFlow.objects.filter(pk=flow_id, active=True).first()
    if flow is None:
        raise Refused('Approval flow not found or inactive.')
    key = R.model_key(type(obj))
    if flow.model and flow.model != key:
        raise Refused(f'The approval flow "{flow.name}" is for {flow.model}, not {key}.')
    if ApprovalRequest.objects.filter(flow=flow, model=key, object_id=str(obj.pk), status='pending').exists():
        raise Refused('This record is already waiting for approval.')
    with transaction.atomic():
        req = ApprovalRequest.objects.create(flow=flow, model=key, object_id=str(obj.pk), record_label=R.label_of(obj)[:255],
                                             record_link=R.record_link(key, obj.pk), submitted_by=user, note=note or '', callbacks=callbacks or {},
                                             run=run, resubmitted_from=resubmitted_from, correlation_id=(run.correlation_id if run else ''))
        made = 0
        for i, st in enumerate(flow.steps or [], start=1):
            level = int(st.get('level') or i)
            amt = _amount(flow, st, obj)
            if st.get('min_amount') not in (None, '') and amt is not None and amt < decimal.Decimal(str(st['min_amount'])):
                continue   # amount-based routing: this level only above the threshold
            if req.amount is None and amt is not None:
                req.amount = amt
            users = []
            for ap in st.get('approvers') or []:
                users += _users_for(ap, obj, flow, user)
            seen = set()
            users = [u for u in users if u and not (u.pk in seen or seen.add(u.pk))]
            if not flow.allow_self_approval and user is not None:
                users = [u for u in users if u.pk != user.pk]
            if not users:
                esc = st.get('escalate_to')
                if esc:
                    from UserManagement.models import CustomUser
                    e = CustomUser.objects.filter(pk=esc).first()
                    users = [e] if e else []
            if not users:
                continue
            due = timezone.now() + datetime.timedelta(hours=int(st.get('deadline_hours') or 0)) if st.get('deadline_hours') else None
            for u in users:
                ApprovalStep.objects.create(request=req, level=level, level_name=st.get('name') or f'Level {level}', mode=st.get('mode') or 'sequential',
                                            approver=u, due_at=due, status='waiting')
                made += 1
        if not made:
            req.status, req.decided_at = 'approved', timezone.now()
            req.save()
            _set_status(req, flow.approved_value)
            _after_decision(req)
            return req
        req.current_level = req.steps.order_by('level').first().level
        req.save()
        _set_status(req, flow.pending_value)
        _activate_level(req, req.current_level)
    return req


def _after_decision(req):
    """Callbacks of the rule that submitted the record (on_approved / on_rejected actions)."""
    cb = req.callbacks or {}
    acts = cb.get('on_approved') if req.status == 'approved' else (cb.get('on_rejected') if req.status == 'rejected' else None)
    if not acts:
        return
    from . import engine as E
    from .models import Workflow, WorkflowRun
    wf = Workflow.objects.filter(pk=cb.get('workflow')).first()
    model = R.get_model(req.model)
    obj = model._default_manager.filter(pk=req.object_id).first() if model else None
    if wf is None or obj is None:
        return
    run = WorkflowRun.objects.create(workflow=wf, version=wf.version, model=req.model, object_id=str(obj.pk), record_label=req.record_label,
                                     trigger='approval', initiator='approval', parent=req.run, correlation_id=req.correlation_id, day=E.local_today())
    ctx = E.context_for(obj, None, None, run)
    status = E.run_actions(list(acts), ctx, run)
    E._finish(run, status)


def _advance(req):
    """After a decision on a step: level done? → next level or final decision."""
    steps = list(req.steps.all())
    cur = [s for s in steps if s.level == req.current_level]
    if any(s.status == 'rejected' for s in cur):
        req.status, req.decided_at = 'rejected', timezone.now()
        for s in steps:
            if s.status in ('waiting', 'pending'):
                s.status = 'skipped'
                s.save(update_fields=['status'])
        req.save()
        _set_status(req, req.flow.rejected_value)
        _notify([req.submitted_by], f'Rejected: {req.record_label}', f'{req.record_label} was rejected. ' + '; '.join(s.comment for s in cur if s.comment), req)
        _after_decision(req)
        return
    if any(s.status == 'changes' for s in cur):
        req.status, req.decided_at = 'changes', timezone.now()
        for s in steps:
            if s.status in ('waiting', 'pending'):
                s.status = 'skipped'
                s.save(update_fields=['status'])
        req.save()
        _notify([req.submitted_by], f'Changes requested: {req.record_label}', '; '.join(s.comment for s in cur if s.comment) or 'Please change and submit again.', req)
        return
    mode = cur[0].mode if cur else 'sequential'
    done = all(s.status == 'approved' for s in cur) if mode == 'parallel_all' else any(s.status == 'approved' for s in cur)
    if not done:
        return
    for s in cur:   # the other approvers of a "first one wins" level
        if s.status == 'pending':
            s.status = 'skipped'
            s.save(update_fields=['status'])
    nxt = sorted({s.level for s in steps if s.level > req.current_level})
    if nxt:
        req.current_level = nxt[0]
        req.save(update_fields=['current_level'])
        _activate_level(req, nxt[0])
        return
    req.status, req.decided_at = 'approved', timezone.now()
    req.save()
    _set_status(req, req.flow.approved_value)
    _notify([req.submitted_by], f'Approved: {req.record_label}', f'{req.record_label} is approved.', req)
    _after_decision(req)


def can_act(step, user):
    if user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return step.approver_id == user.pk or step.delegate_id == user.pk or step.escalated_to_id == user.pk


def decide(step, user, decision, comment='', attachment=None):
    """decision: approved | rejected | changes."""
    if step.status != 'pending':
        raise Refused('This step was already decided or is not active yet.')
    if not can_act(step, user):
        raise Refused('You are not the approver of this step.')
    req = step.request
    if decision in ('rejected', 'changes') and req.flow.require_comment_on_reject and not (comment or '').strip():
        raise Refused('A comment is required to reject or ask for changes.')
    if not req.flow.allow_self_approval and req.submitted_by_id == user.pk and not getattr(user, 'is_superuser', False):
        raise Refused('You cannot approve your own request.')
    with transaction.atomic():
        step.status, step.comment, step.decided_by, step.decided_at = decision, comment or '', user, timezone.now()
        if attachment is not None:
            step.attachment = attachment
        step.save()
        _advance(req)
    return req


def delegate(step, user, to_user, comment=''):
    if step.status not in ('pending', 'waiting'):
        raise Refused('This step is already decided.')
    if not can_act(step, user):
        raise Refused('Only the approver can delegate this step.')
    if to_user is None or to_user.pk == step.approver_id:
        raise Refused('Choose another user to delegate to.')
    step.delegate = to_user
    step.comment = (step.comment + '\n' if step.comment else '') + f'Delegated to {to_user.username} by {user.username}. {comment or ""}'.strip()
    step.save(update_fields=['delegate', 'comment'])
    _notify([to_user], f'Approval delegated to you: {step.request.record_label}', comment or f'{user.username} asked you to decide on {step.request.record_label}.', step.request)
    return step


def cancel(req, user, note=''):
    if req.status != 'pending':
        raise Refused('Only a pending request can be cancelled.')
    if req.submitted_by_id != user.pk and not getattr(user, 'is_superuser', False):
        raise Refused('Only the person who submitted it (or an administrator) can cancel it.')
    req.status, req.decided_at = 'cancelled', timezone.now()
    req.note = (req.note + '\n' if req.note else '') + (note or '')
    req.save()
    req.steps.filter(status__in=('waiting', 'pending')).update(status='skipped')
    return req


def resubmit(req, user, note=''):
    """A rejected / changes-requested request goes through the flow again."""
    if req.status not in ('rejected', 'changes', 'cancelled'):
        raise Refused('Only a rejected or returned request can be submitted again.')
    model = R.get_model(req.model)
    obj = model._default_manager.filter(pk=req.object_id).first() if model else None
    if obj is None:
        raise Refused('The record no longer exists.')
    return submit(req.flow_id, obj, user=user, note=note, callbacks=req.callbacks, resubmitted_from=req)


def sweep():
    """Reminders when a step is over its deadline; escalation to the configured user; one reminder a day."""
    from .models import ApprovalStep
    from UserManagement.models import CustomUser
    now = timezone.now()
    n = 0
    for s in ApprovalStep.objects.filter(status='pending', due_at__lt=now).select_related('request', 'request__flow', 'approver', 'delegate'):
        if s.reminded_at and (now - s.reminded_at) < datetime.timedelta(hours=23):
            continue
        req = s.request
        st = next((x for x in (req.flow.steps or []) if int(x.get('level') or 0) == s.level), {}) or {}
        esc_id = st.get('escalate_to')
        if esc_id and not s.escalated_to_id:
            e = CustomUser.objects.filter(pk=esc_id).first()
            if e is not None:
                s.escalated_to, s.escalated_at = e, now
                _notify([e], f'Escalated to you: {req.record_label}', f'{req.record_label} has waited past its deadline at level {s.level}. You may decide on it.', req)
        _notify([s.delegate or s.approver], f'Reminder: {req.record_label} is waiting for your approval',
                f'The deadline of level {s.level} ({s.level_name}) was {timezone.localtime(s.due_at):%d/%m/%Y %H:%M}.', req)
        s.reminded_at = now
        s.save(update_fields=['reminded_at', 'escalated_to', 'escalated_at'])
        n += 1
    return n
