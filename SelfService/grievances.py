"""v1.13.0 – complaints / grievances.

* Confidential: a case is visible to its assigned grievance officers and to users holding `handle_grievance`
  for the employee's branch – never to the employee's reporting manager unless that manager is assigned.
* Anonymous: the employee's identity is not stored on the case (employee_id = 0, no author on messages);
  the employee follows the case with the token shown once at submission.
* Status flow: submitted → acknowledged → investigating → resolved → closed (resolved can be reopened),
  with SLA days per category and escalation when a case is not acknowledged / resolved in time.
"""
import hashlib
import secrets
from datetime import date, timedelta

from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import profile as P
from . import services as S
from .models import GRIEVANCE_CATEGORIES, Grievance, GrievanceMessage, GrievanceSetting

M = apps.get_model
FLOW = {
    'submitted': ('acknowledged', 'investigating', 'closed'),
    'acknowledged': ('investigating', 'resolved', 'closed'),
    'investigating': ('resolved', 'closed'),
    'resolved': ('closed', 'investigating'),
    'closed': (),
}
CATS = dict(GRIEVANCE_CATEGORIES)


def token_hash(token):
    return hashlib.sha256((token or '').strip().encode()).hexdigest()


def settings_for(category):
    s = GrievanceSetting.objects.filter(category=category).first()
    return s or GrievanceSetting(category=category)


def manager_user_id(case):
    if not case.employee_id:
        return None
    e = S.employee(case.employee_id)
    return e.emp_reporting_manager_id if e else None


def is_handler(request, case):
    c = S.ctx(request)
    uid = request.user.pk
    if uid in (case.assigned_user_ids or []):
        return True
    if c.admin:
        return True
    if 'handle_grievance' not in c.codes:
        return False
    if uid == manager_user_id(case):
        return False  # never the employee's own manager unless assigned
    if case.employee_id and S.employee(case.employee_id) and S.employee(case.employee_id).users_id == uid:
        return False
    return c.branches is None or (case.branch_id in c.branches)


def handler_cases(request):
    c = S.ctx(request)
    uid = request.user.pk
    qs = Grievance.objects.all()
    if c.admin:
        return qs
    ids = [g.pk for g in qs if is_handler(request, g)]
    return Grievance.objects.filter(pk__in=ids)


def submit(request, emp, data, files=()):
    cat = data.get('category')
    if cat not in CATS:
        raise ValidationError({'category': 'Choose a category.'})
    subject = (data.get('subject') or '').strip()
    desc = (data.get('description') or '').strip()
    if not subject:
        raise ValidationError({'subject': 'Give your complaint a short subject.'})
    if len(desc) < 10:
        raise ValidationError({'description': 'Describe what happened (at least a sentence), so it can be looked into.'})
    anon = str(data.get('anonymous', '')).lower() in ('1', 'true', 'yes', 'on')
    conf = str(data.get('confidential', 'true')).lower() not in ('0', 'false', 'no', 'off')
    st = settings_for(cat)
    mgr = emp.emp_reporting_manager_id
    assigned = [u for u in (st.officer_user_ids or []) if u != mgr and u != request.user.pk]
    token = secrets.token_urlsafe(18) if anon else ''
    case = Grievance.objects.create(
        number=S.next_number(Grievance, 'GRV'), employee_id=0 if anon else emp.pk, branch_id=emp.emp_branch_id_id, category=cat,
        subject=subject[:200], description=desc, against=(data.get('against') or '')[:200],
        incident_date=data.get('incident_date') or None, confidential=conf or anon, anonymous=anon, token_hash=token_hash(token) if anon else '',
        assigned_user_ids=assigned, sla_days=st.sla_days, due_date=date.today() + timedelta(days=st.sla_days or 10))
    GrievanceMessage.objects.create(grievance=case, author_user_id=None if anon else request.user.pk, from_employee=True, event='submitted',
                                    text='Complaint submitted.' + (' (anonymous)' if anon else ''))
    for f in files or ():
        GrievanceMessage.objects.create(grievance=case, author_user_id=None if anon else request.user.pk, from_employee=True, attachment=f, text=getattr(f, 'name', ''))
    for u in recipients(case):
        S.notify(user=u, title='New complaint to handle', message=f'A {CATS[cat].lower()} complaint {case.number} was submitted. It is due by {case.due_date:%d %b %Y}.')
    return case, token


def recipients(case):
    U = M('UserManagement', 'CustomUser')
    if case.assigned_user_ids:
        return list(U.objects.filter(pk__in=case.assigned_user_ids, is_active=True))
    mgr = manager_user_id(case)
    return [u for u in S.users_with(case.branch_id, ('handle_grievance',)) if u.pk != mgr]


def transition(request, case, new_status, note='', outcome=''):
    if new_status not in FLOW.get(case.status, ()):
        raise ValidationError({'status': f'A {case.status} case cannot move to {new_status}. Next steps: {", ".join(FLOW.get(case.status, ())) or "none"}.'})
    if new_status in ('resolved', 'closed') and not (outcome or case.outcome):
        raise ValidationError({'outcome': 'Write the outcome before resolving or closing the case.'})
    now = timezone.now()
    old = case.status
    case.status = new_status
    if outcome:
        case.outcome = outcome
    if new_status == 'acknowledged':
        case.acknowledged_at = now
    elif new_status == 'resolved':
        case.resolved_at = now
    elif new_status == 'closed':
        case.closed_at = now
    elif new_status == 'investigating' and not case.acknowledged_at:
        case.acknowledged_at = now
    case.save()
    GrievanceMessage.objects.create(grievance=case, author_user_id=request.user.pk, event=new_status,
                                    text=f'Status changed from {old} to {new_status}.' + (f' {note}' if note else ''))
    _tell_employee(case, f'Your complaint {case.number} is now {new_status}.' + (f' Outcome: {case.outcome}' if new_status in ('resolved', 'closed') else ''))
    return case


def _tell_employee(case, message):
    if case.anonymous or not case.employee_id:
        return  # the employee follows an anonymous case with the token
    e = S.employee(case.employee_id)
    if e:
        S.notify(employee=e, title='Update on your complaint', message=message)


def add_message(case, text, user=None, from_employee=False, internal=False, attachment=None):
    if not (text or '').strip() and attachment is None:
        raise ValidationError({'text': 'Write a message or attach a file.'})
    if case.status == 'closed':
        raise ValidationError({'status': 'This case is closed – messages can no longer be added.'})
    m = GrievanceMessage.objects.create(grievance=case, author_user_id=None if (from_employee and case.anonymous) else getattr(user, 'pk', None),
                                        from_employee=from_employee, internal=bool(internal) and not from_employee, text=(text or '').strip(), attachment=attachment)
    if from_employee:
        for u in recipients(case):
            S.notify(user=u, title='New message on a complaint', message=f'The employee replied on complaint {case.number}.')
    elif not m.internal:
        _tell_employee(case, f'HR replied on your complaint {case.number}.')
    return m


def feedback(case, rating, text=''):
    if case.status not in ('resolved', 'closed'):
        raise ValidationError({'status': 'You can give feedback once the case is resolved.'})
    try:
        r = int(rating)
    except (TypeError, ValueError):
        r = 0
    if not 1 <= r <= 5:
        raise ValidationError({'feedback_rating': 'Rate the handling from 1 (poor) to 5 (very good).'})
    case.feedback_rating, case.feedback_text = r, (text or '')[:1000]
    case.save(update_fields=['feedback_rating', 'feedback_text', 'updated_at'])
    GrievanceMessage.objects.create(grievance=case, from_employee=True, event='feedback', text=f'Feedback: {r}/5. {case.feedback_text}'.strip())
    return case


def escalate_overdue(today=None):
    """Escalate cases not acknowledged within acknowledge_days or not resolved by the due date. Returns the number escalated."""
    today = today or date.today()
    n = 0
    for case in Grievance.objects.exclude(status__in=('resolved', 'closed')):
        st = settings_for(case.category)
        late_ack = case.status == 'submitted' and (today - timezone.localtime(case.created_at).date()).days > (st.acknowledge_days or 2)
        late = case.due_date and today > case.due_date
        level = 2 if late else (1 if late_ack else 0)
        if level <= case.escalation_level:
            continue
        case.escalated, case.escalation_level, case.escalated_at = True, level, timezone.now()
        extra = [u for u in (st.escalate_to_user_ids or []) if u not in (case.assigned_user_ids or []) and u != manager_user_id(case)]
        if extra:
            case.assigned_user_ids = list(case.assigned_user_ids or []) + extra
        case.save()
        GrievanceMessage.objects.create(grievance=case, event='escalated', internal=True,
                                        text='Escalated: ' + ('not resolved by the due date.' if late else 'not acknowledged in time.'))
        for u in recipients(case):
            S.notify(user=u, title='Complaint escalated', message=f'Complaint {case.number} is overdue ({"past due date" if late else "not acknowledged"}).')
        n += 1
    return n


def case_json(case, request=None, handler=False):
    msgs = case.messages.all()
    if not handler:
        msgs = [m for m in msgs if not m.internal]
    emp = S.employee(case.employee_id) if (handler and case.employee_id and not case.anonymous) else None
    overdue = bool(case.due_date and case.status not in ('resolved', 'closed') and date.today() > case.due_date)
    out = {'id': case.id, 'number': case.number, 'category': case.category, 'category_label': CATS.get(case.category, case.category),
           'subject': case.subject, 'description': case.description, 'against': case.against, 'incident_date': case.incident_date,
           'confidential': case.confidential, 'anonymous': case.anonymous, 'status': case.status, 'next_steps': list(FLOW.get(case.status, ())),
           'sla_days': case.sla_days, 'due_date': case.due_date, 'overdue': overdue, 'escalated': case.escalated,
           'escalation_level': case.escalation_level, 'outcome': case.outcome, 'acknowledged_at': case.acknowledged_at,
           'resolved_at': case.resolved_at, 'closed_at': case.closed_at, 'feedback_rating': case.feedback_rating,
           'feedback_text': case.feedback_text, 'created_at': case.created_at,
           'messages': [{'id': m.id, 'from_employee': m.from_employee, 'internal': m.internal, 'event': m.event, 'text': m.text,
                         'author': ('Employee' if m.from_employee else (S.uname(m.author_user_id) if handler else 'HR')) if (m.from_employee or m.author_user_id) else ('Employee' if m.from_employee else 'System'),
                         'attachment': (m.attachment.name.rsplit('/', 1)[-1] if m.attachment else None), 'created_at': m.created_at} for m in msgs]}
    if handler:
        out['employee'] = P.person(emp) if emp else ('Anonymous' if case.anonymous else None)
        out['branch_id'] = case.branch_id
        out['assigned'] = [{'id': u, 'name': S.uname(u)} for u in case.assigned_user_ids or []]
    return out
