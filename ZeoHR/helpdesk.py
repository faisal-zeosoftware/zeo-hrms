"""v1.51.0 – HR helpdesk on the HR operations app: employees ask HR (a payslip query, a letter, a document, a policy
question …) and HR answers within the times of the category (first answer and solution). Confidential complaints keep
their own form (Self service → Complaints); letters keep the letter request – a category can send the employee there.

Ticket: new → assigned → (waiting for the employee) → resolved → closed (by the employee's rating or after 7 days);
the employee can reopen a resolved ticket. The conversation is kept on the ticket (internal notes are HR only).
"""
from datetime import timedelta

from django.db.models import Avg, Count, F, Q

from zeo.appkit.engine import Act, AppError, Reg, d, emp_name, now, user_name

from .app import MANAGE, PAGE, VIEW, app, hr, me
from .models import HelpdeskCategory, HRTicket

OPEN = ('new', 'assigned', 'waiting')
DEFAULT_CATEGORIES = [
    ('Payslip or salary question', 'A deduction, an allowance, overtime, the payslip', 8, 48),
    ('Leave and attendance', 'Balance, a request, a punch, the attendance of a day', 8, 48),
    ('Letter or certificate', 'Salary certificate, NOC, experience letter', 8, 24),
    ('Documents, visa and ID', 'Passport, visa, Emirates ID / iqama, labour card', 8, 72),
    ('Benefits and insurance', 'Medical insurance, air ticket, allowances', 8, 72),
    ('Policy question', 'What the company policy says', 24, 72),
    ('Access to ZEO', 'Login, password, the mobile app', 4, 24),
    ('Complaint (confidential)', 'Goes to the confidential complaint form', 8, 240),
    ('Other', '', 24, 96),
]


def seed():
    if HelpdeskCategory.objects.exists():
        return
    for i, (n, desc, rh, sh) in enumerate(DEFAULT_CATEGORIES):
        HelpdeskCategory.objects.create(name=n, description=desc, response_hours=rh, resolve_hours=sh, order=i,
                                        redirect='complaint' if n.startswith('Complaint') else '', catch_all=(n == 'Other'))


def categories(active=True):
    seed()
    qs = HelpdeskCategory.objects.all()
    return qs.filter(active=True) if active else qs


def msg(o, text, user=None, from_employee=False, internal=False, kind='message'):
    o.messages = list(o.messages or []) + [{'at': now().isoformat(), 'by': user_name(user) if user and getattr(user, 'pk', None) else (emp_name(o.employee) if from_employee else ''),
                                             'from_employee': from_employee, 'internal': internal, 'kind': kind, 'text': text[:10000]}]


def handlers(o):
    if o.assigned_to_id:
        return [o.assigned_to]
    if o.category.assignee_id:
        return [o.category.assignee]
    return app.users_with_role('hro_hr')[:3]


def open_ticket(emp, category, subject, description, user=None, channel='self_service', priority='normal', request=None):
    if category.redirect:
        raise AppError('Use the {0} form for this.'.format('confidential complaint' if category.redirect == 'complaint' else 'letter request'), 'category')
    if not (subject or '').strip() or not (description or '').strip():
        raise AppError('Write the subject and your question.')
    t = now()
    o = HRTicket(number=app.next_number('ticket', 'HD'), employee=emp, category=category, subject=subject.strip()[:150], description=description.strip()[:10000],
                 channel=channel, priority=priority if priority in dict(HRTicket._meta.get_field('priority').choices) else 'normal',
                 branch_id=emp.emp_branch_id_id, created_by=user if user and getattr(user, 'pk', None) else None)
    try:   # v1.61.0 suggested category and priority (applied to a general category when sure enough)
        from .ticket_ai import suggest
        suggest(o, request)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('ticket suggestion')
    o.response_due = t + timedelta(hours=o.category.response_hours or 8)
    o.resolve_due = t + timedelta(hours=o.category.resolve_hours or 48)
    if o.category.assignee_id:
        o.assigned_to, o.status = o.category.assignee, 'assigned'
    o.save()
    app.event('ticket', o.pk, f'Opened ({o.get_channel_display()})', user)
    if o.ai_applied:
        app.event('ticket', o.pk, f'Suggestion applied: {o.category.name}, priority {o.priority} ({o.ai_confidence} % sure – {o.ai_reason})', None)
    for u in handlers(o):
        app.todo(u, f'HR question {o.number}: {o.subject}', 'ticket', o.pk, o.number, PAGE + f'r/ticket/{o.pk}',
                 f'{emp_name(emp)} · answer by {o.response_due:%d/%m %H:%M}')
    return o


def answered(o, user):
    if not o.first_response_at:
        o.first_response_at = now()
    app.close_todos('ticket', o.pk)


def tell(o, subject, text):
    app.tell_emp(o.employee, subject, text, 'ticket', o.pk, o.number, '/main-sidebar/self-service/hr-help', summary=subject)
    try:   # WhatsApp / SMS when the company switched it on for helpdesk answers
        from Chatter.messaging import send_event
        if o.employee.users_id:
            send_event(o.employee.users_id, 'helpdesk', subject, text[:300])
    except Exception:
        pass


# ------------------------------------------------------------------ HR actions
def a_classify(o, request, f):
    """v1.61.0 read the ticket again and show the suggestion (nothing changes)."""
    from .ticket_ai import suggest
    from .models import HRSettings
    s = HRSettings.get()
    if s.ticket_ai == 'off':
        raise AppError('Ticket suggestions are off in HR operations settings.')
    suggest(o, request, s, apply=False)
    o.save(update_fields=['ai_category', 'ai_priority', 'ai_confidence', 'ai_source', 'ai_reason', 'ai_applied', 'updated_at'])
    app.event('ticket', o.pk, f'Suggestion: {o.ai_category.name if o.ai_category_id else "no category"}, priority {o.ai_priority} ({o.ai_confidence} % sure)', request.user)
    return o


def a_use_ai(o, request, f):
    """v1.61.0 HR takes the suggested category and priority."""
    if not o.ai_category_id and not o.ai_priority:
        raise AppError('There is no suggestion – use "Suggest category" first.')
    changes = []
    if o.ai_category_id and o.ai_category_id != o.category_id:
        if o.ai_category.redirect:
            raise AppError('The suggested category goes to another form – choose the category yourself.')
        o.category = o.ai_category
        t = now()
        o.response_due = o.created_at + timedelta(hours=o.category.response_hours or 8) if o.created_at else t
        o.resolve_due = o.created_at + timedelta(hours=o.category.resolve_hours or 48) if o.created_at else t
        changes.append(f'category {o.category.name}')
    if o.ai_priority and o.ai_priority != o.priority:
        o.priority = o.ai_priority
        changes.append(f'priority {o.priority}')
    if not changes:
        raise AppError('The ticket already has the suggested category and priority.')
    o.ai_applied = True
    o.save()
    app.event('ticket', o.pk, 'Suggestion used: ' + ', '.join(changes), request.user)
    return o


def a_assign(o, request, f):
    from django.contrib.auth import get_user_model
    U = get_user_model()
    uid = f.i('user')
    u = U.objects.filter(pk=uid).first() if uid else request.user
    if u is None:
        raise AppError('Choose the person.', 'user')
    o.assigned_to = u
    if o.status == 'new':
        o.status = 'assigned'
    o.save()
    app.event('ticket', o.pk, f'Assigned to {user_name(u)}', request.user)
    app.close_todos('ticket', o.pk)
    app.todo(u, f'HR question {o.number}: {o.subject}', 'ticket', o.pk, o.number, PAGE + f'r/ticket/{o.pk}', f'{emp_name(o.employee)}')
    return o


def a_reply(o, request, f):
    text = f.s('text')
    if not text:
        raise AppError('Write the answer.', 'text')
    msg(o, text, request.user)
    answered(o, request.user)
    if f.s('wait') == 'yes':
        o.status = 'waiting'
    elif o.status in ('new', 'waiting'):
        o.status = 'assigned'
    if not o.assigned_to_id:
        o.assigned_to = request.user
    o.save()
    app.event('ticket', o.pk, 'Answer to the employee: ' + text[:300], request.user)
    tell(o, f'HR answered {o.number}', text)
    return o


def a_note(o, request, f):
    text = f.s('text')
    if not text:
        raise AppError('Write the note.', 'text')
    msg(o, text, request.user, internal=True, kind='note')
    o.save()
    app.event('ticket', o.pk, 'Internal note: ' + text[:300], request.user)
    return o


def a_resolve(o, request, f):
    text = f.s('resolution')
    if not text:
        raise AppError('Write the answer / solution.', 'resolution')
    o.resolution, o.status, o.resolved_at = text, 'resolved', now()
    if not o.assigned_to_id:
        o.assigned_to = request.user
    answered(o, request.user)
    msg(o, text, request.user, kind='resolved')
    o.save()
    app.event('ticket', o.pk, 'Resolved: ' + text[:300], request.user)
    tell(o, f'Your question {o.number} is answered', text + '\n\nPlease rate the answer in Self service → HR help (or reopen it if it is not solved).')
    return o


def a_reopen(o, request, f):
    o.status, o.resolved_at = 'assigned' if o.assigned_to_id else 'new', None
    o.resolve_due = now() + timedelta(hours=o.category.resolve_hours or 48)
    o.save()
    app.event('ticket', o.pk, 'Reopened' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def a_close(o, request, f):
    o.status, o.closed_at = 'closed', now()
    o.save()
    app.event('ticket', o.pk, 'Closed', request.user)
    app.close_todos('ticket', o.pk)
    return o


def a_cancel(o, request, f):
    o.status, o.closed_at = 'cancelled', now()
    o.save()
    app.event('ticket', o.pk, 'Cancelled' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('ticket', o.pk)
    return o


def ticket_defaults(o, request):
    if o.pk is None and o.category_id and o.subject and o.description and not o.ai_source:   # v1.61.0 entered by HR: suggest too
        try:
            from .ticket_ai import suggest
            suggest(o, request)
        except Exception:
            pass
    if o.category_id and not o.response_due:
        t = now()
        o.response_due = t + timedelta(hours=o.category.response_hours or 8)
        o.resolve_due = t + timedelta(hours=o.category.resolve_hours or 48)
    if not o.channel or o.channel == 'self_service':
        o.channel = 'hr'
    if o.employee_id and not o.branch_id:
        o.branch_id = o.employee.emp_branch_id_id


def ticket_check(o, request, old):
    if o.category_id and o.category.redirect and old is None:
        raise AppError('This category goes to the {0} form.'.format('confidential complaint' if o.category.redirect == 'complaint' else 'letter request'), 'category')


def sla_state(o, t=None):
    t = t or now()
    if o.status in ('resolved', 'closed', 'cancelled'):
        late = bool(o.resolve_due and o.resolved_at and o.resolved_at > o.resolve_due)
        return 'late' if late else 'met'
    if o.resolve_due and t > o.resolve_due:
        return 'late'
    if not o.first_response_at and o.response_due and t > o.response_due:
        return 'answer late'
    return 'on time'


def ticket_extra(o, r):
    show_internal = hr(r)
    t = now()
    return {'sla': {'response_due': o.response_due.isoformat() if o.response_due else '', 'resolve_due': o.resolve_due.isoformat() if o.resolve_due else '',
                    'response_late': bool(o.response_due and (o.first_response_at or t) > o.response_due),
                    'resolve_late': bool(o.resolve_due and (o.resolved_at or t) > o.resolve_due), 'state': sla_state(o, t)},
            'conversation': [m for m in (o.messages or []) if show_internal or not m.get('internal')]}


app.option('hd_categories', lambda: categories(), lambda x: x.name, ('name',), model=HelpdeskCategory)
app.reg(Reg('hd_category', HelpdeskCategory, 'Helpdesk category', 'Helpdesk categories', 'Helpdesk',
            cols=('name', 'response_hours', 'resolve_hours', 'assignee', 'redirect', 'active'),
            form=('name', 'description', 'response_hours', 'resolve_hours', 'assignee', 'redirect', 'order', 'active', 'keywords', 'catch_all'),
            manage=('hro_manager', 'hro_hr'), view=VIEW, search=('name',), filters=('active',), title='name', subtitle='description',
            help='What employees ask HR about: the time HR answers (first answer) and solves it in, and who gets it. '
                 'A category can send the employee to the confidential complaint or the letter request form instead.'))

app.reg(Reg('ticket', HRTicket, 'HR ticket', 'HR helpdesk', 'Helpdesk',
            cols=('number', 'employee', 'category', 'subject', 'priority', 'assigned_to', 'resolve_due', 'status'),
            form=('employee', 'category', 'subject', 'description', 'priority', 'channel', 'assigned_to'),
            number=('ticket', 'HD'), manage=MANAGE, view=VIEW, public=lambda r: Q(assigned_to=r.user),
            search=('number', 'subject', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'),
            filters=('status', 'category', 'priority', 'assigned_to', 'employee'), title='number', subtitle='subject',
            defaults=ticket_defaults, check=ticket_check, edit_when=lambda o: o.status in OPEN,
            info=('status', 'response_due', 'first_response_at', 'resolve_due', 'resolved_at', 'resolution', 'rating', 'rating_comment', 'channel',
                  'ai_category', 'ai_priority', 'ai_confidence', 'ai_source', 'ai_reason', 'ai_applied'),
            extra=ticket_extra,
            actions=[Act('assign', 'Assign', a_assign, lambda o, r: o.status in OPEN and hr(r), fields=(('user', 'Assign to (empty = me)', 'opt:users', False),)),
                     Act('use_ai', 'Use the suggestion', a_use_ai, lambda o, r: o.status in OPEN and hr(r) and bool(o.ai_category_id or o.ai_priority) and not o.ai_applied
                         and (o.ai_category_id not in (None, o.category_id) or (o.ai_priority and o.ai_priority != o.priority))),
                     Act('classify', 'Suggest category', a_classify, lambda o, r: o.status in OPEN and hr(r)),
                     Act('reply', 'Answer the employee', a_reply, lambda o, r: o.status in OPEN and (hr(r) or o.assigned_to_id == r.user.pk), style='primary',
                         fields=(('text', 'Answer', 'textarea', True), ('wait', 'Wait for the employee', 'choice:no=No|yes=Yes – the employee must answer', False))),
                     Act('note', 'Internal note', a_note, lambda o, r: o.status in OPEN and (hr(r) or o.assigned_to_id == r.user.pk),
                         fields=(('text', 'Note (HR only)', 'textarea', True),)),
                     Act('resolve', 'Resolve', a_resolve, lambda o, r: o.status in OPEN and (hr(r) or o.assigned_to_id == r.user.pk), style='primary',
                         fields=(('resolution', 'Answer / solution', 'textarea', True),)),
                     Act('reopen', 'Reopen', a_reopen, lambda o, r: o.status in ('resolved', 'closed') and hr(r), fields=(('note', 'Why', 'text', False),)),
                     Act('close', 'Close', a_close, lambda o, r: o.status == 'resolved' and hr(r)),
                     Act('cancel', 'Cancel', a_cancel, lambda o, r: o.status in OPEN and hr(r), style='danger', fields=(('note', 'Why', 'text', True),))],
            help='Questions employees ask HR (Self service → HR help, the kiosk, or entered by HR): the first answer and the solution are due by the '
                 'times of the category; the conversation is kept on the ticket and internal notes stay with HR.'))


# ------------------------------------------------------------------ the employee (Self service → HR help)
def my_json(o):
    return {'id': o.pk, 'number': o.number, 'category': o.category.name, 'category_id': o.category_id, 'subject': o.subject, 'description': o.description,
            'status': o.status, 'status_label': o.get_status_display(), 'created_at': o.created_at, 'resolve_due': o.resolve_due,
            'assigned_to': user_name(o.assigned_to) if o.assigned_to_id else '', 'resolution': o.resolution, 'rating': o.rating,
            'messages': [m for m in (o.messages or []) if not m.get('internal')],
            'can_reply': o.status in OPEN, 'can_rate': o.status == 'resolved' and not o.rating,
            'can_reopen': o.status == 'resolved' and bool(o.resolved_at and now() - o.resolved_at < timedelta(days=7))}


def employee_post(emp, data, user=None, channel='self_service'):
    """create {category, subject, description} | reply {id, text} | rate {id, rating, comment} | reopen {id, text}"""
    if data.get('id'):
        o = HRTicket.objects.filter(pk=data.get('id'), employee=emp).first()
        if o is None:
            raise AppError('Ticket not found.')
        act = data.get('action') or ('rate' if data.get('rating') else 'reply')
        text = (data.get('text') or '').strip()
        if act == 'reply':
            if o.status not in OPEN or not text:
                raise AppError('Write your message.' if not text else 'This ticket is not open.')
            msg(o, text, user, from_employee=True)
            if o.status == 'waiting':
                o.status = 'assigned' if o.assigned_to_id else 'new'
            o.save()
            app.event('ticket', o.pk, 'The employee wrote: ' + text[:300], user)
            for u in handlers(o):
                app.todo(u, f'Reply on {o.number}: {o.subject}', 'ticket', o.pk, o.number, PAGE + f'r/ticket/{o.pk}', text[:120])
        elif act == 'rate':
            try:
                n = int(data.get('rating'))
            except (TypeError, ValueError):
                n = 0
            if o.status != 'resolved' or not 1 <= n <= 5:
                raise AppError('Rate a resolved ticket from 1 to 5.')
            o.rating, o.rating_comment, o.status, o.closed_at = n, (data.get('comment') or '')[:255], 'closed', now()
            o.save()
            app.event('ticket', o.pk, f'Rated {n}/5 by the employee and closed', user)
        elif act == 'reopen':
            if not my_json(o)['can_reopen']:
                raise AppError('This ticket cannot be reopened – open a new one.')
            msg(o, text or 'Reopened: not solved.', user, from_employee=True)
            o.status, o.resolved_at = 'assigned' if o.assigned_to_id else 'new', None
            o.resolve_due = now() + timedelta(hours=o.category.resolve_hours or 48)
            o.save()
            app.event('ticket', o.pk, 'Reopened by the employee', user)
            for u in handlers(o):
                app.todo(u, f'Reopened {o.number}: {o.subject}', 'ticket', o.pk, o.number, PAGE + f'r/ticket/{o.pk}')
        else:
            raise AppError('Unknown action.')
        return o
    cat = categories().filter(pk=data.get('category')).first() if data.get('category') else None
    if cat is None and not data.get('category'):   # v1.61.0 "not sure" → the general category; the suggestion sorts it
        from .models import HRSettings
        if HRSettings.get().ticket_ai != 'off':
            cat = categories().filter(catch_all=True, redirect='').first()
    if cat is None:
        raise AppError('Choose what it is about.', 'category')
    return open_ticket(emp, cat, data.get('subject') or '', data.get('description') or '', user, channel)


# ------------------------------------------------------------------ dashboard and daily
def dashboard_part(request):
    if not (hr(request) or app.any(request, *VIEW)):
        return {}
    qs = app.regs['ticket'].scope(request)
    op = qs.filter(status__in=OPEN)
    t = now()
    late = op.filter(Q(resolve_due__lt=t) | Q(first_response_at__isnull=True, response_due__lt=t)).count()
    done = qs.filter(resolved_at__isnull=False, resolved_at__gte=t - timedelta(days=30))
    met = done.filter(resolved_at__lte=F('resolve_due')).count()
    rating = qs.filter(rating__isnull=False).aggregate(a=Avg('rating'))['a']
    cards = [{'label': 'Open HR tickets', 'value': op.count(), 'link': 'r/ticket?open=1'},
             {'label': 'HR tickets late', 'value': late, 'tone': 'bad' if late else 'ok', 'link': 'r/ticket?open=1'},
             {'label': 'Solved on time (30 days)', 'value': f'{round(met * 100 / done.count())} %' if done.count() else '–', 'link': 'r/ticket'},
             {'label': 'Employee rating', 'value': f'{rating:.1f} / 5' if rating else '–', 'link': 'r/ticket'}]
    bars = [{'title': 'Open HR tickets by category', 'items': [{'label': x['category__name'], 'value': x['n']}
                                                               for x in op.values('category__name').annotate(n=Count('id')).order_by('-n')[:8]]}]
    lists = [{'title': 'HR tickets due first', 'kind': 'ticket', 'rows': [{'id': o.pk, 'title': f'{o.number} {o.subject}', 'sub': emp_name(o.employee),
                                                                         'badge': sla_state(o, t)} for o in op.select_related('employee').order_by('resolve_due')[:8]]}]
    return {'cards': cards, 'bars': [b for b in bars if b['items']], 'lists': lists}


def daily_part():
    """Late tickets go to the HR operations manager; resolved tickets close after 7 days without a rating."""
    t = now()
    out = {'tickets_late': 0, 'tickets_closed': 0}
    for o in HRTicket.objects.filter(status__in=OPEN, resolve_due__lt=t).select_related('employee'):
        for u in app.users_with_role('hro_manager')[:2] or handlers(o):
            app.todo(u, f'HR ticket late: {o.number}', 'ticket', o.pk, o.number, PAGE + f'r/ticket/{o.pk}', f'Due {o.resolve_due:%d/%m %H:%M}')
        out['tickets_late'] += 1
    for o in HRTicket.objects.filter(status='resolved', resolved_at__lt=t - timedelta(days=7)):
        o.status, o.closed_at = 'closed', t
        o.save(update_fields=['status', 'closed_at'])
        out['tickets_closed'] += 1
    return out


_dash, _daily = app.dashboard, app.daily


def dashboard(request):
    out = _dash(request)
    part = dashboard_part(request)
    for k in ('cards', 'bars', 'lists'):
        out[k] = list(out.get(k) or []) + list(part.get(k) or [])
    return out


def daily():
    out = _daily() or {}
    out.update(daily_part())
    return out


app.dashboard, app.daily = dashboard, daily
