"""Notification settings (v1.15.0): which events send an e-mail and/or put an item in the user's To-do / My work.

Three levels, the most specific wins:  user  >  customer  >  company default  >  built-in default (EVENTS).
* Company: Projects → Setup → Notifications.
* Customer: the customer page → Notifications (e.g. a client who wants no e-mails about stage changes).
* User: My work → My notifications (each person decides for themselves).

Customer-facing events (audience 'customer') go to the customer's contacts; they have no user level and no to-do.
"""
from .models import NotificationSetting

# key: (label, audience, default e-mail, default to-do, help)
EVENTS = {
    'ticket_new': ('New ticket – acknowledgement to the customer', 'customer', True, False,
                   'Ticket number, priority and the SLA times, sent when a ticket is registered.'),
    'ticket_update': ('Ticket replies and stage changes', 'customer', True, False, 'Our replies, resolved, closed.'),
    'ticket_waiting': ('Reminder when we wait for the customer', 'customer', True, False, ''),
    'customer_approval': ('Approvals needed from the customer', 'customer', True, False,
                          'Development quotations, milestones ready for acceptance, change requests.'),
    'ticket_assigned': ('Ticket assigned to me', 'staff', True, True, ''),
    'ticket_new_consultant': ('New ticket for my customer (account manager)', 'staff', True, False, ''),
    'ticket_unassigned': ('New ticket nobody is assigned to (managers)', 'staff', True, True, ''),
    'ticket_reply': ('Customer replied or reopened my ticket', 'staff', True, True, ''),
    'sla_breach': ('Ticket not solved in time', 'staff', True, True, 'The engineer, the team lead and then the support managers.'),
    'work_approval': ('Work approval needed or decided', 'staff', True, True,
                      'Tickets not covered by an AMC and time beyond the hours limit.'),
    'task_assigned': ('Project task assigned to me', 'staff', True, True, ''),
    'task_overdue': ('Project task not completed on time', 'staff', True, True, 'The people on the task and the project managers.'),
    'project_updates': ('Milestones, change requests and development steps', 'staff', True, True, ''),
    'renewal': ('AMC renewal reminders', 'staff', True, True, ''),
    'weekly_summary': ('Weekly summary e-mail (Mondays)', 'staff', True, False, ''),
}


def _row(event, customer_id=None, user_id=None):
    return NotificationSetting.objects.filter(event=event, customer_id=customer_id, user_id=user_id).first()


def wants(event, channel='email', user=None, customer=None):
    """True when this event should go out on this channel ('email' or 'dashboard') to this user / for this customer."""
    if not event or event not in EVENTS:
        return True
    label, audience, d_email, d_todo, _ = EVENTS[event]
    default = d_email if channel == 'email' else d_todo
    cid = getattr(customer, 'pk', customer) if customer else None
    uid = getattr(user, 'pk', user) if user else None
    levels = []
    if uid and audience == 'staff':
        levels.append((None, uid))
    if cid:
        levels.append((cid, None))
    levels.append((None, None))
    for c, u in levels:
        r = _row(event, c, u)
        if r:
            return bool(getattr(r, channel))
    return default


def matrix(customer=None, user=None):
    """Rows for the settings screen: the value at this level (or None = inherited) and the effective value."""
    cid = getattr(customer, 'pk', None)
    uid = getattr(user, 'pk', None)
    out = []
    for key, (label, audience, d_email, d_todo, help_) in EVENTS.items():
        if user is not None and audience != 'staff':
            continue
        own = _row(key, cid if user is None else None, uid)
        parent_email, parent_todo = d_email, d_todo
        comp = _row(key)
        if comp:
            parent_email, parent_todo = comp.email, comp.dashboard
        if user is None and customer is None:
            parent_email, parent_todo = d_email, d_todo
        out.append({'event': key, 'label': label, 'audience': audience, 'help': help_,
                    'custom': own is not None,
                    'email': own.email if own else parent_email, 'dashboard': own.dashboard if own else parent_todo,
                    'inherited_email': parent_email, 'inherited_dashboard': parent_todo,
                    'has_dashboard': audience == 'staff'})
    return out


def save(rows, customer=None, user=None):
    """rows: [{event, custom, email, dashboard}] – custom False removes this level's row (back to inherited)."""
    cid = getattr(customer, 'pk', None)
    uid = getattr(user, 'pk', None)
    for r in rows or []:
        key = r.get('event')
        if key not in EVENTS:
            continue
        if user is not None and EVENTS[key][1] != 'staff':
            continue
        qs = NotificationSetting.objects.filter(event=key, customer_id=cid if user is None else None, user_id=uid)
        if not r.get('custom', True) and (customer is not None or user is not None):
            qs.delete()
            continue
        obj = qs.first() or NotificationSetting(event=key, customer_id=cid if user is None else None, user_id=uid)
        obj.email = bool(r.get('email'))
        obj.dashboard = bool(r.get('dashboard')) if EVENTS[key][1] == 'staff' else False
        obj.save()
    return matrix(customer, user)
