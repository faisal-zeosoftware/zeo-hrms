"""ZEO HR operations (v1.48.0) on the app kit: disciplinary and grievance cases, exit interviews, document renewals,
PRO / government services and asset handovers. Always on; the HR officers of the company work here with their HR rights.

Rights
  manage  HR operations manager / HR officer roles, or users who may change employees (change_emp_master) – the HR team
  PRO     the PRO role and the HR team
  view    the viewer role and users who may see employees (view_emp_master)
  own     an employee sees their own exit interview, renewals, PRO services and handovers (never a disciplinary case –
          they get the show-cause notice and the decision in Self service)
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q

from zeo.appkit.engine import Act, App, AppError, Reg, ctx, d, emp_name, now, today, user_name

from . import catalogue as CAT
from .models import (AssetHandover, DisciplinaryCase, DocumentRenewal, ExitInterview, HREvent, HRFile, HRSequence, HRSettings, ProService, ROLES)

PAGE = '/main-sidebar/zeo-hr/'
HR_CODES = {'change_emp_master', 'add_emp_master'}
VIEW_CODES = {'view_emp_master'}


class HRApp(App):
    """The HR team (employee rights) counts as HR officers here, so nothing has to be set up before use."""

    def activated(self):
        """Always on: the settings row is made (and marked active) on first use."""
        try:
            return bool(self.settings_model.get().activated_at)
        except Exception:
            return False

    def has(self, request, role):
        c = ctx(request)
        if not c:
            return False
        if c.admin:
            return True
        code = self.code(role)
        if code in c.codes:
            return True
        if code in ('hro_hr', 'hro_pro') and (c.codes & HR_CODES):
            return True
        if code == 'hro_hr' and 'handle_grievance' in c.codes:
            return True
        if code == 'hro_viewer' and (c.codes & (HR_CODES | VIEW_CODES)):
            return True
        return False


app = HRApp(label='ZeoHR', slug='zeo-hr', title='HR operations', settings_model=HRSettings, sequence_model=HRSequence, event_model=HREvent,
            file_model=HRFile, roles=ROLES, role_prefix='hro_', manager_role='hro_manager',
            packs={'general': {'label': 'General', 'description': 'No pack items'}}, pack_labels={'general': 'General'},
            pack_descriptions={'general': 'No pack items'}, industry_from_company={}, pack_models={}, page=PAGE)
app.settings_fields = ('renewal_days', 'renewal_auto', 'renewal_pro', 'fine_max_days', 'suspension_max_days', 'show_cause_days', 'exit_auto', 'ticket_ai', 'ticket_ai_min',
                       'exit_days_before', 'handover_signature')

MANAGE = ('hro_manager', 'hro_hr')
PRO = ('hro_manager', 'hro_pro', 'hro_hr')
VIEW = ('hro_viewer',)


def me(request):
    return app.my_emp(request)


def hr(request):
    return app.any(request, *MANAGE)


def link(kind, pk):
    return PAGE + f'r/{kind}/{pk}'


# ------------------------------------------------------------------ options
app.option('grievances', lambda: __import__('SelfService.models', fromlist=['Grievance']).Grievance.objects.all(),
           lambda x: f'{x.number or x.pk} {x.subject}', ('number', 'subject'))
app.option('pro-services', lambda: ProService.objects.exclude(status__in=('completed', 'cancelled', 'rejected')),
           lambda x: f'{x.number} {x.get_service_display()}' + (f' – {emp_name(x.employee)}' if x.employee_id else ''), ('number', 'application_no'), ProService)
app.option('resignations', lambda: __import__('EmpManagement.models', fromlist=['EmployeeResignation']).EmployeeResignation.objects.select_related('employee'),
           lambda x: f'{x.document_number or x.pk} {emp_name(x.employee)} – last day {d(x.last_working_date)}', ('document_number', 'employee__emp_first_name'))


# ================================================================== disciplinary and grievance cases
def case_defaults(o, request):
    o.created_by = request.user
    if not o.reported_date:
        o.reported_date = today()
    if o.grievance_id and not o.description:
        o.description = o.grievance.description
        o.title = o.title or o.grievance.subject
        o.category = 'grievance'


def case_check(o, request, old):
    if o.incident_date and o.incident_date > today():
        raise AppError('The incident date is in the future.', 'incident_date')


def _warning(o, level, request):
    from HRActions.models import EmployeeWarning
    w = EmployeeWarning.objects.create(employee_id=o.employee_id, date=o.decision_date or today(), level=level, subject=o.title[:150],
                                       details=(o.decision_notes or o.findings or '')[:4000], issued_by_id=request.user.pk, issued_by_name=user_name(request.user))
    return w.pk


def a_investigate(o, request, f):
    from UserManagement.models import CustomUser
    uid = f.i('investigator')
    if uid:
        o.investigator = CustomUser.objects.filter(pk=uid).first()
    o.status = 'investigation'
    o.save()
    app.event('case', o.pk, f'Investigation started' + (f' – investigator {user_name(o.investigator)}' if o.investigator_id else ''), request.user)
    if o.investigator_id:
        app.todo(o.investigator, f'Investigate {o.number}: {o.title}', 'case', o.pk, o.number, link('case', o.pk), o.description[:500])
    return o


def _day(v):
    return v.strftime('%d %b %Y') if v else ''


def show_cause_text(o):
    return (f'Dear {emp_name(o.employee)},\n\n'
            f'This notice concerns: {o.title} (reported for {_day(o.incident_date or o.reported_date)}).\n\n{o.description}\n\n'
            f'You are asked to explain in writing, by {_day(o.response_due)}, why disciplinary action should not be taken against you. '
            'You may bring any facts, documents or witnesses in your support. If no reply is received by that date, the matter '
            'will be decided on the information available.\n\nHuman Resources')


def a_show_cause(o, request, f):
    s = app.settings()
    o.response_due = f.dt('response_due') or (today() + timedelta(days=s.show_cause_days or 3))
    o.show_cause_text = f.s('text') or show_cause_text(o)
    o.show_cause_sent, o.status = today(), 'show_cause'
    o.save()
    app.event('case', o.pk, f'Show-cause notice sent, reply due {d(o.response_due)}', request.user)
    app.tell_emp(o.employee, f'Show-cause notice {o.number}', o.show_cause_text, 'case', o.pk, o.number, '/main-sidebar/zeo-hr/my-cases',
                 summary=f'Reply to show-cause notice {o.number} by {d(o.response_due)}')
    return o


def a_response(o, request, f):
    o.employee_response = f.s('response') or o.employee_response
    o.response_received = today()
    o.save()
    app.event('case', o.pk, 'Employee response recorded', request.user)
    return o


def a_hearing(o, request, f):
    o.hearing_at = f.dtm('hearing_at', True, 'Hearing date and time')
    o.hearing_place = f.s('hearing_place') or o.hearing_place
    o.hearing_panel = f.s('hearing_panel') or o.hearing_panel
    o.status = 'hearing'
    o.save()
    app.event('case', o.pk, f'Hearing on {d(o.hearing_at)} {o.hearing_place}', request.user)
    app.tell_emp(o.employee, f'Disciplinary hearing {o.number}',
                 f'You are invited to a hearing on {d(o.hearing_at)} at {o.hearing_place or "the HR office"} about: {o.title}. '
                 'You may bring a colleague as a witness.', 'case', o.pk, o.number, '/main-sidebar/zeo-hr/my-cases', summary=f'Hearing {o.number} on {d(o.hearing_at)}')
    return o


def a_decide(o, request, f):
    s = app.settings()
    dec = f.choice('decision', [(k, v) for k, v in DisciplinaryCase._meta.get_field('decision').choices if k], None)
    if not dec:
        raise AppError('Choose the decision.', 'decision')
    o.decision = dec
    o.decision_date = f.dt('decision_date') or today()
    o.effective_date = f.dt('effective_date') or o.decision_date
    o.decision_notes = f.s('notes') or o.decision_notes
    o.findings = f.s('findings') or o.findings
    if dec == 'fine':
        days = f.n('fine_days')
        if not days or days <= 0:
            raise AppError("Give the fine in days' wage.", 'fine_days')
        if s.fine_max_days and Decimal(str(days)) > s.fine_max_days:
            raise AppError(f"A fine can be at most {s.fine_max_days:g} days' wage a month (Settings).", 'fine_days')
        o.fine_days = days
    if dec == 'suspension':
        days = f.i('suspension_days')
        if not days or days <= 0:
            raise AppError('Give the days of suspension.', 'suspension_days')
        if s.suspension_max_days and days > s.suspension_max_days:
            raise AppError(f'A suspension can be at most {s.suspension_max_days} days (Settings).', 'suspension_days')
        o.suspension_days = days
    if dec in ('verbal', 'written', 'final'):
        o.warning_id = _warning(o, dec, request)
    o.status = 'decided'
    o.save()
    app.event('case', o.pk, f'Decision: {o.get_decision_display()}' + (f" ({o.fine_days:g} days' wage)" if dec == 'fine' else '')
              + (f' ({o.suspension_days} days)' if dec == 'suspension' else ''), request.user)
    app.close_todos('case', o.pk)
    app.tell_emp(o.employee, f'Decision on case {o.number}', f'Decision: {o.get_decision_display()}.\n\n{o.decision_notes}\n\nYou may appeal in writing to HR.',
                 'case', o.pk, o.number, '/main-sidebar/zeo-hr/my-cases', summary=f'Decision on {o.number}: {o.get_decision_display()}')
    if dec in ('fine', 'suspension'):   # payroll applies the deduction / unpaid days
        for u in app.users_with_role('hro_manager'):
            app.todo(u, f'Payroll: {o.get_decision_display().lower()} for {emp_name(o.employee)} ({o.number})', 'case', o.pk, o.number, link('case', o.pk),
                     f"{o.fine_days or ''} days' wage" if dec == 'fine' else f'{o.suspension_days} days from {d(o.effective_date)}')
    return o


def a_appeal(o, request, f):
    o.appeal_text = f.s('appeal') or o.appeal_text
    o.appeal_date = f.dt('appeal_date') or today()
    o.status = 'appeal'
    o.save()
    app.event('case', o.pk, 'Appeal received', request.user)
    return o


def a_appeal_decide(o, request, f):
    out = f.choice('outcome', [('upheld', 'Upheld'), ('reduced', 'Reduced'), ('overturned', 'Overturned')], None)
    if not out:
        raise AppError('Choose the outcome of the appeal.', 'outcome')
    o.appeal_outcome, o.appeal_notes, o.status = out, f.s('notes') or o.appeal_notes, 'decided'
    if out == 'overturned' and o.warning_id:
        from HRActions.models import EmployeeWarning
        EmployeeWarning.objects.filter(pk=o.warning_id).update(status='withdrawn')
    o.save()
    app.event('case', o.pk, f'Appeal: {o.get_appeal_outcome_display()}', request.user)
    app.tell_emp(o.employee, f'Your appeal on case {o.number}', f'Outcome: {o.get_appeal_outcome_display()}.\n\n{o.appeal_notes}', 'case', o.pk, o.number,
                 '/main-sidebar/zeo-hr/my-cases')
    return o


def a_close(o, request, f):
    o.status, o.closed_at = 'closed', now()
    o.save()
    app.event('case', o.pk, 'Closed' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('case', o.pk)
    if o.grievance_id and o.grievance.status not in ('resolved', 'closed'):
        g = o.grievance
        g.status, g.outcome = 'resolved', g.outcome or f'Handled in disciplinary case {o.number}: {o.get_decision_display() or "closed"}.'
        g.save(update_fields=['status', 'outcome', 'updated_at'])
    return o


def a_withdraw(o, request, f):
    o.status = 'withdrawn'
    o.save()
    app.event('case', o.pk, 'Withdrawn' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('case', o.pk)
    return o


OPEN_CASE = ('reported', 'investigation', 'show_cause', 'hearing')


def case_extra(o, r):
    others = DisciplinaryCase.objects.filter(employee=o.employee).exclude(pk=o.pk)
    from HRActions.models import EmployeeWarning
    return {'history': {'cases': others.count(), 'open': others.filter(status__in=OPEN_CASE).count(),
                        'warnings': EmployeeWarning.objects.filter(employee_id=o.employee_id, status='issued').count(),
                        'final_warnings': EmployeeWarning.objects.filter(employee_id=o.employee_id, status='issued', level='final').count()},
            'deadline': d(o.response_due) if o.status == 'show_cause' else ''}


DEC_CHOICES = '|'.join(f'{k}={v}' for k, v in DisciplinaryCase._meta.get_field('decision').choices if k)
app.reg(Reg('case', DisciplinaryCase, 'Case', 'Disciplinary and grievance cases', 'Cases',
            cols=('number', 'employee', 'category', 'title', 'reported_date', 'investigator', 'decision', 'status'),
            form=('employee', 'category', 'title', 'description', 'incident_date', 'reported_date', 'location', 'reported_by', 'witnesses', 'grievance'),
            number=('case', 'DC'), manage=MANAGE, view=VIEW, public=lambda r: Q(investigator=r.user),
            search=('number', 'title', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'), filters=('status', 'category', 'decision', 'employee'),
            title='number', subtitle='title', defaults=case_defaults, check=case_check, edit_when=lambda o: o.status in OPEN_CASE,
            info=('status', 'investigator', 'show_cause_sent', 'response_due', 'employee_response', 'hearing_at', 'hearing_place', 'hearing_panel', 'findings',
                  'decision', 'decision_date', 'effective_date', 'fine_days', 'suspension_days', 'decision_notes', 'appeal_text', 'appeal_outcome', 'appeal_notes'),
            restricted=('witnesses', 'findings', 'hearing_notes'), extra=case_extra, pdf='Case letter',
            actions=[Act('investigate', 'Start investigation', a_investigate, lambda o, r: o.status == 'reported' and hr(r), style='primary',
                         fields=(('investigator', 'Investigator', 'opt:users', False),)),
                     Act('show_cause', 'Send show-cause notice', a_show_cause, lambda o, r: o.status in ('reported', 'investigation') and hr(r),
                         fields=(('response_due', 'Reply due (empty = Settings)', 'date', False), ('text', 'Notice (empty = standard text)', 'textarea', False))),
                     Act('response', 'Record the reply', a_response, lambda o, r: o.status in ('show_cause', 'investigation', 'hearing') and hr(r),
                         fields=(('response', "Employee's reply", 'textarea', True),)),
                     Act('hearing', 'Plan the hearing', a_hearing, lambda o, r: o.status in ('investigation', 'show_cause', 'hearing') and hr(r),
                         fields=(('hearing_at', 'Date and time', 'datetime', True), ('hearing_place', 'Place', 'text', False), ('hearing_panel', 'Panel', 'text', False))),
                     Act('decide', 'Decide', a_decide, lambda o, r: o.status in OPEN_CASE and hr(r), style='primary',
                         fields=(('decision', 'Decision', 'choice:' + DEC_CHOICES, True), ('fine_days', "Fine (days' wage)", 'decimal', False),
                                 ('suspension_days', 'Suspension (days)', 'int', False), ('decision_date', 'Decision date', 'date', False),
                                 ('effective_date', 'Effective from', 'date', False), ('findings', 'Findings (restricted)', 'textarea', False),
                                 ('notes', 'Decision told to the employee', 'textarea', False))),
                     Act('appeal', 'Record an appeal', a_appeal, lambda o, r: o.status == 'decided' and not o.appeal_outcome and hr(r),
                         fields=(('appeal', 'Appeal', 'textarea', True), ('appeal_date', 'Received on', 'date', False))),
                     Act('appeal_decide', 'Decide the appeal', a_appeal_decide, lambda o, r: o.status == 'appeal' and app.any(r, 'hro_manager'), style='primary',
                         fields=(('outcome', 'Outcome', 'choice:upheld=Decision upheld|reduced=Penalty reduced|overturned=Decision overturned', True),
                                 ('notes', 'Notes to the employee', 'textarea', False))),
                     Act('close', 'Close', a_close, lambda o, r: o.status == 'decided' and hr(r), fields=(('note', 'Note', 'text', False),)),
                     Act('withdraw', 'Withdraw', a_withdraw, lambda o, r: o.status in OPEN_CASE and hr(r), style='danger', fields=(('note', 'Why', 'text', True),))],
            help='A disciplinary matter or an employee complaint handled as a case: investigation, show-cause notice and the reply, hearing, '
                 'decision (a warning goes to the employee timeline; a fine or a suspension is passed to payroll), appeal. Confidential: HR and the investigator only.'))


# ================================================================== exit interviews
def exit_questions():
    """The company's exit survey (People → Surveys, kind Exit) or the standard questions."""
    try:
        from ZeoPeople.models import Survey
        s = Survey.objects.filter(kind='exit').exclude(status='draft').order_by('-created_at').first() or Survey.objects.filter(kind='exit').order_by('-created_at').first()
        if s and s.questions:
            return [dict(q) for q in s.questions]
    except Exception:
        pass
    return [dict(q) for q in CAT.EXIT_QUESTIONS]


def exit_defaults(o, request):
    if not o.questions:
        o.questions = exit_questions()
    if o.resignation_id and not o.last_working_date:
        o.last_working_date = o.resignation.last_working_date
    if not o.interviewer_id:
        o.interviewer = request.user


def exit_check(o, request, old):
    if old is None and ExitInterview.objects.filter(employee=o.employee).exclude(status__in=('closed', 'declined')).exists():
        raise AppError('This employee already has an open exit interview.', 'employee')


def send_exit(o, user=None):
    o.status, o.sent_at = 'sent', now()
    o.save(update_fields=['status', 'sent_at'])
    app.event('exit_interview', o.pk, 'Sent to the employee', user)
    app.tell_emp(o.employee, 'Your exit interview', 'Before you leave, please tell us about your time with us. Your answers are read by HR only.',
                 'exit_interview', o.pk, o.number, PAGE + f'exit/{o.pk}', summary='Answer your exit interview')


def a_exit_send(o, request, f):
    send_exit(o, request.user)
    return o


def a_exit_held(o, request, f):
    o.interview_at = f.dtm('interview_at') or now()
    o.notes = f.s('notes') or o.notes
    for k in ('reason', 'rehire'):
        if f.s(k):
            setattr(o, k, f.s(k))
    o.status = 'interviewed'
    o.save()
    app.event('exit_interview', o.pk, 'Interview held', request.user)
    app.close_todos('exit_interview', o.pk)
    return o


def a_exit_close(o, request, f):
    o.status = 'closed'
    o.save(update_fields=['status'])
    app.event('exit_interview', o.pk, 'Closed', request.user)
    app.close_todos('exit_interview', o.pk)
    return o


def a_exit_declined(o, request, f):
    o.status = 'declined'
    o.notes = (o.notes + '\n' if o.notes else '') + (f.s('note') or 'The employee declined the exit interview.')
    o.save()
    app.event('exit_interview', o.pk, 'Declined', request.user)
    app.close_todos('exit_interview', o.pk)
    return o


def exit_extra(o, r):
    e = me(r)
    mine = bool(e and o.employee_id == e.pk)
    out = {'take_page': PAGE + f'exit/{o.pk}' if mine and o.status in ('planned', 'sent') else '', 'can_take': mine and o.status in ('planned', 'sent')}
    if hr(r) or app.any(r, *VIEW):
        out['answers'] = [{'label': q.get('text', ''), 'type': q.get('type', 'text'), 'value': (o.answers or [None] * 99)[i] if i < len(o.answers or []) else ''}
                          for i, q in enumerate(o.questions or [])]
    return out


app.reg(Reg('exit_interview', ExitInterview, 'Exit interview', 'Exit interviews', 'Exit interviews',
            cols=('number', 'employee', 'last_working_date', 'reason', 'rehire', 'recommend', 'interviewer', 'status'),
            form=('employee', 'resignation', 'last_working_date', 'interviewer', 'interview_at'),
            number=('exit_interview', 'EXI'), manage=MANAGE, view=VIEW, own=(('employee', 'emp'),),
            search=('number', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'), filters=('status', 'reason', 'rehire'),
            title='number', subtitle='employee', defaults=exit_defaults, check=exit_check, edit_when=lambda o: o.status in ('planned', 'sent', 'answered', 'interviewed'),
            info=('status', 'sent_at', 'answered_at', 'reason', 'rehire', 'recommend', 'notes'), restricted=('notes',), extra=exit_extra, pdf='Summary',
            actions=[Act('send', 'Send to the employee', a_exit_send, lambda o, r: o.status in ('planned', 'sent') and hr(r), style='primary'),
                     Act('held', 'Interview held', a_exit_held, lambda o, r: o.status in ('planned', 'sent', 'answered') and hr(r),
                         fields=(('interview_at', 'When', 'datetime', False), ('reason', 'Main reason', 'choice:' + '|'.join(f'{k}={v}' for k, v in ExitInterview._meta.get_field('reason').choices if k), False),
                                 ('rehire', 'Would rehire', 'choice:yes=Yes|maybe=Maybe|no=No', False), ('notes', 'Interviewer notes (restricted)', 'textarea', False))),
                     Act('close', 'Close', a_exit_close, lambda o, r: o.status in ('answered', 'interviewed') and hr(r)),
                     Act('declined', 'Employee declined', a_exit_declined, lambda o, r: o.status in ('planned', 'sent') and hr(r), fields=(('note', 'Note', 'text', False),))],
            help='One exit interview per leaver: the employee answers the questions in Self service (the company exit survey of People, or the standard '
                 'questions), HR records the interview, the main reason and whether to rehire. Sent automatically when a resignation is approved (Settings).'))


# ================================================================== document renewals
def _pro_user():
    s = app.settings()
    if s.renewal_pro_id:
        return s.renewal_pro
    users = app.users_with_role('hro_pro')
    return users[0] if users else None


def start_renewal(emp_id, doc, user=None, assign=None):
    """doc: a row of DashboardManagement.documents.current_documents. Returns (renewal, created)."""
    from EmpManagement.models import emp_master
    open_ = DocumentRenewal.objects.filter(employee_id=emp_id, document__iexact=doc['type']).exclude(status__in=('renewed', 'cancelled')).first()
    if open_ is not None:
        return open_, False
    e = emp_master.objects.filter(pk=emp_id).first()
    src, sid, sfield = 'manual', None, ''
    if doc.get('source') == 'Employee document':
        src, sid = 'document', doc.get('_id')
    elif doc.get('source') == 'Identity':
        from DashboardManagement.documents import IDENTITY
        src, sid = 'identity', emp_id
        sfield = next((ef for lbl, nf, ef in IDENTITY if lbl == doc['type']), '')
    r = DocumentRenewal.objects.create(number=app.next_number('renewal', 'RNW'), employee=e, document=doc['type'][:60], doc_number=(doc.get('number') or '')[:60],
                                       expiry=doc.get('expiry'), source=src, source_id=sid, source_field=sfield, assigned_to=assign or _pro_user(),
                                       branch_id=getattr(e, 'emp_branch_id_id', None))
    app.event('renewal', r.pk, 'Renewal started' + (' (automatic)' if user is None else ''), user)
    if r.assigned_to_id:
        app.todo(r.assigned_to, f'Renew {r.document} of {emp_name(e)} (expires {d(r.expiry)})', 'renewal', r.pk, r.number, link('renewal', r.pk))
    app.tell_emp(e, f'Your {r.document} expires on {d(r.expiry)}', f'HR has started the renewal of your {r.document}. You may be asked for documents or a photo.',
                 'renewal', r.pk, r.number, link('renewal', r.pk))
    return r, True


def apply_renewal(o):
    """Write the new number / expiry back to the employee's document or identity."""
    if not o.new_expiry:
        return
    if o.source == 'document' and o.source_id:
        from EmpManagement.models import Emp_Documents
        doc = Emp_Documents.objects.filter(pk=o.source_id).first()
        if doc is not None:
            if o.new_number and o.new_number != doc.emp_doc_number:
                Emp_Documents.objects.filter(pk=doc.pk).update(is_active=False)
                Emp_Documents.objects.create(emp_id=doc.emp_id, document_type=doc.document_type, emp_doc_number=o.new_number[:50],
                                             emp_doc_issued_date=today(), emp_doc_expiry_date=o.new_expiry, is_active=True)
            else:
                Emp_Documents.objects.filter(pk=doc.pk).update(emp_doc_expiry_date=o.new_expiry, emp_doc_issued_date=today())
    elif o.source == 'identity' and o.source_field:
        from django.apps import apps as A
        from DashboardManagement.documents import IDENTITY
        ID = A.get_model('EmployeeProfile', 'EmployeeIdentity')
        nf = next((n for lbl, n, ef in IDENTITY if ef == o.source_field), None)
        upd = {o.source_field: o.new_expiry}
        if o.new_number and nf:
            upd[nf] = o.new_number
        if not ID.objects.filter(employee_id=o.employee_id).update(**upd):
            ID.objects.create(employee_id=o.employee_id, **upd)


def a_ren_pro(o, request, f):
    """Open the PRO service that renews the document."""
    country = CAT.company_country()
    svc = CAT.renewal_service(country, o.document) or CAT.find(country, 'other')
    p = ProService.objects.create(number=app.next_number('pro', 'PRO'), service=svc['code'], title=f'{svc["name"]} – {emp_name(o.employee)}', country=country,
                                  authority=svc['authority'], employee=o.employee, status='requested', requested_date=today(),
                                  expected_date=today() + timedelta(days=svc['days']), steps=[{'step': s, 'done': False, 'date': ''} for s in svc['steps']],
                                  assigned_to=o.assigned_to or request.user, created_by=request.user, branch_id=o.branch_id)
    o.pro_service, o.status = p, 'in_progress'
    o.save()
    app.event('renewal', o.pk, f'PRO service {p.number} opened', request.user)
    app.event('pro', p.pk, f'Opened for renewal {o.number}', request.user)
    return {'message': f'PRO service {p.number} opened.', 'created': {'app': 'zeo-hr', 'kind': 'pro', 'id': p.pk}}


def a_ren_progress(o, request, f):
    o.status = f.choice('status', [('in_progress', 'In progress'), ('submitted', 'Submitted')], 'in_progress')
    o.notes = (o.notes + '\n' if o.notes else '') + f.s('note') if f.s('note') else o.notes
    o.save()
    app.event('renewal', o.pk, o.get_status_display(), request.user)
    return o


def a_ren_done(o, request, f):
    o.new_expiry = f.dt('new_expiry', True, 'New expiry date')
    if o.expiry and o.new_expiry <= o.expiry:
        raise AppError('The new expiry must be after the current one.', 'new_expiry')
    o.new_number = f.s('new_number') or o.new_number
    if f.n('cost') is not None:
        o.cost = f.n('cost')
    o.status, o.renewed_at = 'renewed', now()
    o.save()
    apply_renewal(o)
    app.event('renewal', o.pk, f'Renewed until {d(o.new_expiry)}', request.user)
    app.close_todos('renewal', o.pk)
    app.tell_emp(o.employee, f'Your {o.document} was renewed', f'New expiry: {d(o.new_expiry)}' + (f', number {o.new_number}' if o.new_number else '') + '.',
                 'renewal', o.pk, o.number, link('renewal', o.pk))
    return o


def a_ren_cancel(o, request, f):
    o.status = 'cancelled'
    o.notes = (o.notes + '\n' if o.notes else '') + (f.s('note') or 'Cancelled')
    o.save()
    app.event('renewal', o.pk, 'Cancelled' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('renewal', o.pk)
    return o


def ren_extra(o, r):
    return {'days_left': (o.expiry - today()).days if o.expiry else None}


app.reg(Reg('renewal', DocumentRenewal, 'Renewal', 'Document renewals', 'Documents',
            cols=('number', 'employee', 'document', 'doc_number', 'expiry', 'assigned_to', 'pro_service', 'new_expiry', 'status'),
            form=('employee', 'document', 'doc_number', 'expiry', 'assigned_to', 'cost', 'notes'),
            number=('renewal', 'RNW'), manage=PRO, view=VIEW, own=(('employee', 'emp'),), public=lambda r: Q(assigned_to=r.user),
            search=('number', 'document', 'doc_number', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'),
            filters=('status', 'document', 'assigned_to', 'employee'), title='number', subtitle='document',
            edit_when=lambda o: o.status in ('due', 'in_progress', 'submitted'), info=('status', 'new_number', 'new_expiry', 'renewed_at', 'source'), extra=ren_extra,
            actions=[Act('pro', 'Open a PRO service', a_ren_pro, lambda o, r: o.status in ('due', 'in_progress') and not o.pro_service_id and app.any(r, *PRO), style='primary'),
                     Act('progress', 'Update status', a_ren_progress, lambda o, r: o.status in ('due', 'in_progress', 'submitted') and app.any(r, *PRO),
                         fields=(('status', 'Status', 'choice:in_progress=In progress|submitted=Submitted', True), ('note', 'Note', 'text', False))),
                     Act('done', 'Renewed', a_ren_done, lambda o, r: o.status in ('due', 'in_progress', 'submitted') and app.any(r, *PRO), style='primary',
                         fields=(('new_expiry', 'New expiry', 'date', True), ('new_number', 'New number (if it changed)', 'text', False), ('cost', 'Cost', 'decimal', False))),
                     Act('cancel', 'Cancel', a_ren_cancel, lambda o, r: o.status in ('due', 'in_progress', 'submitted') and app.any(r, *PRO), style='danger',
                         fields=(('note', 'Why (e.g. the employee is leaving)', 'text', True),))],
            help="Renewal of an employee's expiring document. Started from the Document expiry hub or automatically before the expiry (Settings); "
                 "Renewed writes the new expiry (and number) back to the employee's document or identity."))


# ================================================================== PRO / government services
def pro_defaults(o, request):
    o.created_by = request.user
    o.country = o.country or CAT.company_country()
    svc = CAT.find(o.country, o.service)
    if svc:
        o.authority = o.authority or svc['authority']
        o.title = o.title or (svc['name'] + (f' – {emp_name(o.employee)}' if o.employee_id else ''))
        if not o.steps:
            o.steps = [{'step': s, 'done': False, 'date': ''} for s in svc['steps']]
        if not o.expected_date:
            o.expected_date = today() + timedelta(days=svc['days'])
        if svc['company'] and not o.employee_id:
            o.for_company = True
    if not o.requested_date:
        o.requested_date = today()
    if not o.assigned_to_id:
        o.assigned_to = _pro_user() or request.user


def pro_check(o, request, old):
    if not o.for_company and not o.employee_id:
        raise AppError('Choose the employee, or tick "for the company".', 'employee')
    o.total = (o.gov_fee or 0) + (o.service_fee or 0) + (o.other_fee or 0)


def pro_set(status_, label, needs=()):
    def run(o, request, f):
        o.status = status_
        if status_ == 'submitted':
            o.submitted_date = f.dt('date') or today()
            o.application_no = f.s('application_no') or o.application_no
        if status_ == 'rejected':
            o.rejection_reason = f.s('reason')
        if status_ in ('approved', 'completed'):
            o.result_number = f.s('result_number') or o.result_number
            o.result_expiry = f.dt('result_expiry') or o.result_expiry
        if status_ == 'completed':
            o.completed_date = f.dt('date') or today()
            o.steps = [dict(s, done=True, date=s.get('date') or str(today())) for s in (o.steps or [])]
        o.save()
        app.event('pro', o.pk, label + (f' – {f.s("reason")}' if f.s('reason') else ''), request.user)
        if status_ == 'completed':
            app.close_todos('pro', o.pk)
            for r in o.renewals.exclude(status__in=('renewed', 'cancelled')):
                if o.result_expiry:
                    r.new_expiry, r.new_number, r.status, r.renewed_at = o.result_expiry, o.result_number or r.new_number, 'renewed', now()
                    r.cost = r.cost or o.total
                    r.save()
                    apply_renewal(r)
                    app.event('renewal', r.pk, f'Renewed by {o.number} until {d(r.new_expiry)}', request.user)
                    app.close_todos('renewal', r.pk)
            if o.employee_id:
                app.tell_emp(o.employee, f'{o.get_service_display()} completed', f'{o.title} is done' + (f' – valid until {d(o.result_expiry)}' if o.result_expiry else '') + '.',
                             'pro', o.pk, o.number, link('pro', o.pk))
        return o
    return run


def pro_extra(o, r):
    st = o.steps or []
    late = bool(o.expected_date and o.expected_date < today() and o.status not in ('completed', 'cancelled', 'rejected'))
    return {'progress': round(100 * sum(1 for s in st if s.get('done')) / len(st)) if st else 0, 'late': late}


app.reg(Reg('pro', ProService, 'PRO service', 'PRO services', 'PRO services',
            cols=('number', 'service', 'employee', 'authority', 'application_no', 'expected_date', 'total', 'assigned_to', 'status'),
            form=('service', 'employee', 'for_company', 'dependant', 'title', 'authority', 'priority', 'application_no', 'requested_date', 'expected_date',
                  'gov_fee', 'service_fee', 'other_fee', 'paid_by', 'assigned_to', 'steps', 'notes'),
            number=('pro', 'PRO'), manage=PRO, view=VIEW, own=(('employee', 'emp'),), public=lambda r: Q(assigned_to=r.user),
            search=('number', 'title', 'application_no', 'result_number', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'),
            filters=('status', 'service', 'assigned_to', 'priority', 'employee'), title='number', subtitle='title', defaults=pro_defaults, check=pro_check,
            edit_when=lambda o: o.status not in ('completed', 'cancelled'), info=('status', 'submitted_date', 'completed_date', 'result_number', 'result_expiry', 'total', 'rejection_reason'),
            restricted=('gov_fee', 'service_fee', 'other_fee', 'total'), extra=pro_extra,
            tables={'steps': [('step', 'Step', 'text'), ('done', 'Done', 'bool'), ('date', 'Date', 'date'), ('note', 'Note', 'text')]},
            actions=[Act('documents', 'Collecting documents', pro_set('documents', 'Collecting documents'), lambda o, r: o.status == 'requested' and app.any(r, *PRO)),
                     Act('submit', 'Submitted', pro_set('submitted', 'Submitted'), lambda o, r: o.status in ('requested', 'documents') and app.any(r, *PRO), style='primary',
                         fields=(('application_no', 'Application / transaction no.', 'text', False), ('date', 'Submitted on', 'date', False))),
                     Act('process', 'In process', pro_set('in_process', 'In process'), lambda o, r: o.status == 'submitted' and app.any(r, *PRO)),
                     Act('approve', 'Approved', pro_set('approved', 'Approved'), lambda o, r: o.status in ('submitted', 'in_process') and app.any(r, *PRO),
                         fields=(('result_number', 'Issued number', 'text', False), ('result_expiry', 'Valid until', 'date', False))),
                     Act('complete', 'Completed', pro_set('completed', 'Completed'), lambda o, r: o.status in ('submitted', 'in_process', 'approved') and app.any(r, *PRO), style='primary',
                         fields=(('result_number', 'Issued number', 'text', False), ('result_expiry', 'Valid until (renews the document)', 'date', False), ('date', 'Completed on', 'date', False))),
                     Act('reject', 'Rejected', pro_set('rejected', 'Rejected'), lambda o, r: o.status in ('submitted', 'in_process') and app.any(r, *PRO), style='danger',
                         fields=(('reason', 'Reason', 'text', True),)),
                     Act('cancel', 'Cancel', pro_set('cancelled', 'Cancelled'), lambda o, r: o.status in ('requested', 'documents') and app.any(r, *PRO), style='danger',
                         fields=(('reason', 'Why', 'text', False),))],
            help='Government services handled by the PRO: visas, IDs (Emirates ID, iqama, QID, civil ID), labour cards, medicals, contracts, cancellations '
                 'and the company licences – the usual steps of the company country, fees, application number, the issued number and expiry. '
                 'Completing a service that renews a document renews it on the employee.'))


# ================================================================== asset handover
CONDITIONS = 'new=New|good=Good|fair=Fair|damaged=Damaged|missing=Missing'
DEFAULT_TERMS = ('I have received the items above in the condition shown. I will use them for company work only, keep them safe and return them '
                 'when asked or when I leave. Loss or damage through my negligence may be charged to me as allowed by law.')
RETURN_TERMS = 'I have returned the items above in the condition shown.'


def ho_defaults(o, request):
    o.created_by = request.user
    if not o.date:
        o.date = today()
    if not o.terms:
        o.terms = DEFAULT_TERMS if o.kind == 'issue' else RETURN_TERMS


def ho_check(o, request, old):
    if old is not None and o.status == 'draft':
        for i, it in enumerate(o.items or []):
            if not str(it.get('asset') or '').strip():
                raise AppError(f'Line {i + 1}: name the asset.', 'items')


def current_assets(emp_id):
    """The employee's assets not returned yet (HR → Assets allocations)."""
    try:
        from OrganisationManager.models import AssetAllocation
    except Exception:
        return []
    out = []
    for a in AssetAllocation.objects.filter(employee_id=emp_id, returned_date__isnull=True).select_related('asset'):
        asset = a.asset
        serial = getattr(asset, 'serial_number', '') or getattr(asset, 'asset_code', '') or getattr(asset, 'code', '') or ''
        out.append({'asset': str(asset)[:120], 'serial': str(serial)[:60], 'condition': 'good', 'accessories': '', 'note': '', 'allocation_id': a.pk})
    return out


def a_ho_fill(o, request, f):
    rows = current_assets(o.employee_id)
    if not rows:
        raise AppError('The employee has no assets on record (HR → Assets).')
    have = {r.get('allocation_id') for r in (o.items or []) if r.get('allocation_id')}
    o.items = (o.items or []) + [r for r in rows if r['allocation_id'] not in have]
    o.save(update_fields=['items'])
    app.event('handover', o.pk, f'{len(rows)} asset(s) added from the employee record', request.user)
    return o


def a_ho_send(o, request, f):
    if not o.items:
        raise AppError('Add the items first.')
    o.status = 'sent'
    o.audit = (o.audit or []) + [{'at': now().isoformat(), 'what': 'sent for signature', 'by': user_name(request.user)}]
    o.save()
    app.event('handover', o.pk, 'Sent for signature', request.user)
    app.tell_emp(o.employee, f'Please sign the asset {"handover" if o.kind == "issue" else "return"} {o.number}', 'Open it in ZEO and sign it.',
                 'handover', o.pk, o.number, PAGE + f'sign/{o.pk}', summary=f'Sign asset {"handover" if o.kind == "issue" else "return"} {o.number}')
    return o


def finish_handover(o):
    """A signed return closes the employee's allocations of the returned items."""
    if o.kind != 'return':
        return
    try:
        from OrganisationManager.models import AssetAllocation
    except Exception:
        return
    cond = {'new': 'healthy', 'good': 'healthy', 'fair': 'minor_damage', 'damaged': 'major_damage', 'missing': 'major_damage'}
    for it in o.items or []:
        if it.get('allocation_id'):
            AssetAllocation.objects.filter(pk=it['allocation_id'], employee_id=o.employee_id, returned_date__isnull=True).update(
                returned_date=o.date or today(), return_condition=cond.get(it.get('condition') or 'good', 'healthy'))
            al = AssetAllocation.objects.filter(pk=it['allocation_id']).select_related('asset').first()
            if al is not None and getattr(al.asset, 'status', None) == 'assigned' and it.get('condition') != 'missing':
                type(al.asset).objects.filter(pk=al.asset_id).update(status='available')


def a_ho_cancel(o, request, f):
    o.status = 'cancelled'
    o.save(update_fields=['status'])
    app.event('handover', o.pk, 'Cancelled', request.user)
    app.close_todos('handover', o.pk)
    return o


def ho_extra(o, r):
    e = me(r)
    mine = bool(e and o.employee_id == e.pk)
    return {'sign_page': PAGE + f'sign/{o.pk}', 'can_sign': o.status == 'sent' and (mine or hr(r)), 'signed_file': bool(o.signed_document), 'audit': o.audit or []}


app.reg(Reg('handover', AssetHandover, 'Asset handover', 'Asset handovers', 'Assets',
            cols=('number', 'kind', 'employee', 'date', 'signed_at', 'status'),
            form=('kind', 'employee', 'date', 'items', 'terms', 'damage_charge', 'notes'),
            number=('handover', 'AHO'), manage=MANAGE, view=VIEW, own=(('employee', 'emp'),),
            search=('number', 'employee__emp_first_name', 'employee__emp_last_name', 'employee__emp_code'), filters=('status', 'kind', 'employee'),
            title='number', subtitle='employee', defaults=ho_defaults, check=ho_check, edit_when=lambda o: o.status == 'draft',
            info=('status', 'signed_at', 'signed_by_name', 'witnessed_by', 'signed_ip'), extra=ho_extra, pdf='Handover',
            tables={'items': [('asset', 'Asset', 'text'), ('serial', 'Serial / tag', 'text'), ('condition', 'Condition', 'choice', CONDITIONS),
                              ('accessories', 'Accessories', 'text'), ('note', 'Note', 'text')]},
            actions=[Act('fill', "Add the employee's assets", a_ho_fill, lambda o, r: o.status == 'draft' and hr(r)),
                     Act('send', 'Send for signature', a_ho_send, lambda o, r: o.status == 'draft' and hr(r), style='primary'),
                     Act('cancel', 'Cancel', a_ho_cancel, lambda o, r: o.status in ('draft', 'sent') and hr(r), style='danger')],
            help='Laptops, phones, vehicles, tools and access cards given to or returned by an employee, signed electronically by the employee '
                 '(in Self service, or on this device in front of HR). A signed return closes the asset allocations; the signed PDF is kept.'))


# ================================================================== document expiry hub
def hub(request, days=90):
    """Every current document of the employees the user may see: expired / ≤30 / ≤60 / ≤90 days, with its open renewal."""
    from DashboardManagement.documents import current_documents
    from EmpManagement.models import emp_master
    from AccessControl.access import scope_queryset

    class _V:
        zeo_scope = True
    emps = scope_queryset(request, _V(), emp_master.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)))
    emps = {e.pk: e for e in emps.select_related('emp_dept_id', 'emp_branch_id')}
    t = today()
    rows = [x for x in current_documents(list(emps), t) if x['expiry'] and x['days'] is not None and x['days'] <= days]
    ren = {}
    for r in DocumentRenewal.objects.filter(employee_id__in=list(emps)).exclude(status='cancelled').select_related('assigned_to', 'pro_service'):
        ren[(r.employee_id, r.document.lower())] = r
    out = []
    for x in rows:
        e = emps[x['employee_id']]
        r = ren.get((x['employee_id'], x['type'].lower()))
        if r is not None and r.status == 'renewed' and r.new_expiry and r.new_expiry > x['expiry']:
            continue
        b = 'expired' if x['days'] < 0 else 'd30' if x['days'] <= 30 else 'd60' if x['days'] <= 60 else 'd90'
        out.append({'employee_id': e.pk, 'employee': emp_name(e), 'code': e.emp_code, 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                    'branch': str(e.emp_branch_id) if e.emp_branch_id_id else '', 'type': x['type'], 'number': x['number'], 'expiry': x['expiry'],
                    'days': x['days'], 'bucket': b, 'source': x['source'], '_id': x.get('_id'),
                    'renewal': {'id': r.pk, 'number': r.number, 'status': r.status, 'status_label': r.get_status_display(),
                                'assigned_to': user_name(r.assigned_to) if r.assigned_to_id else '', 'pro': r.pro_service.number if r.pro_service_id else ''}
                    if r is not None and r.status != 'renewed' else None})
    out.sort(key=lambda r: r['days'])
    counts = {k: sum(1 for r in out if r['bucket'] == k) for k in ('expired', 'd30', 'd60', 'd90')}
    counts['in_renewal'] = sum(1 for r in out if r['renewal'])
    counts['no_renewal'] = sum(1 for r in out if not r['renewal'] and r['days'] <= 60)
    return {'rows': out, 'counts': counts, 'types': sorted({r['type'] for r in out}), 'can_renew': app.any(request, *PRO), 'days': days}


# ================================================================== dashboard, my work, daily
def dashboard(request):
    t = today()
    cards, bars, lists = [], [], []
    if hr(request) or app.any(request, *VIEW):
        cases = app.regs['case'].scope(request).filter(status__in=OPEN_CASE + ('appeal',))
        late = cases.filter(status='show_cause', response_due__lt=t, response_received__isnull=True).count()
        cards.append({'label': 'Open cases', 'value': cases.count(), 'link': 'r/case?open=1'})
        cards.append({'label': 'Show-cause replies overdue', 'value': late, 'tone': 'bad' if late else 'ok', 'link': 'r/case?status=show_cause'})
        exits = app.regs['exit_interview'].scope(request)
        cards.append({'label': 'Exit interviews waiting', 'value': exits.filter(status__in=('planned', 'sent')).count(), 'link': 'r/exit_interview?open=1'})
        labels = dict(ExitInterview._meta.get_field('reason').choices)
        reasons = exits.exclude(reason='').values('reason').annotate(n=Count('id')).order_by('-n')
        bars.append({'title': 'Reasons for leaving', 'items': [{'label': labels.get(r['reason'], r['reason']), 'value': r['n']} for r in reasons]})
        lists.append({'title': 'Open cases', 'kind': 'case', 'rows': [{'id': c.pk, 'title': f'{c.number} {c.title}', 'sub': emp_name(c.employee), 'badge': c.get_status_display()}
                                                                    for c in cases.select_related('employee')[:8]]})
    if app.any(request, *PRO):
        pros = app.regs['pro'].scope(request).exclude(status__in=('completed', 'cancelled', 'rejected'))
        late = pros.filter(expected_date__lt=t).count()
        cards.append({'label': 'Open PRO services', 'value': pros.count(), 'link': 'r/pro?open=1'})
        cards.append({'label': 'PRO services late', 'value': late, 'tone': 'bad' if late else 'ok', 'link': 'r/pro?open=1'})
        ren = app.regs['renewal'].scope(request).filter(status__in=('due', 'in_progress', 'submitted'))
        cards.append({'label': 'Renewals in progress', 'value': ren.count(), 'link': 'r/renewal?open=1'})
        cards.append({'label': 'Handovers to sign', 'value': app.regs['handover'].scope(request).filter(status='sent').count(), 'link': 'r/handover?status=sent'})
        svc = dict(CAT.service_choices())
        bars.append({'title': 'Open PRO services by type', 'items': [{'label': svc.get(x['service'], x['service']), 'value': x['n']}
                                                                    for x in pros.values('service').annotate(n=Count('id')).order_by('-n')[:8]]})
        lists.append({'title': 'PRO services due', 'kind': 'pro', 'rows': [{'id': x.pk, 'title': f'{x.number} {x.title}', 'sub': x.get_status_display(),
                                                                          'badge': d(x.expected_date) if x.expected_date else ''}
                                                                         for x in pros.order_by('expected_date')[:8]]})
    return {'cards': cards, 'bars': [b for b in bars if b['items']], 'lists': lists}


def my_work(request):
    e = me(request)
    out = []
    if e is None:
        return out
    out.append({'title': 'To sign', 'kind': 'handover', 'rows': [{'id': h.pk, 'title': f'Asset {h.get_kind_display().lower()}', 'sub': h.number, 'badge': 'sign'}
                                                               for h in AssetHandover.objects.filter(employee=e, status='sent')[:10]]})
    out.append({'title': 'Exit interview', 'kind': 'exit_interview', 'rows': [{'id': x.pk, 'title': 'Answer your exit interview', 'sub': x.number, 'badge': ''}
                                                                             for x in ExitInterview.objects.filter(employee=e, status__in=('planned', 'sent'))[:3]]})
    out.append({'title': 'My renewals', 'kind': 'renewal', 'rows': [{'id': r.pk, 'title': r.document, 'sub': f'expires {d(r.expiry)}', 'badge': r.get_status_display()}
                                                                   for r in DocumentRenewal.objects.filter(employee=e).exclude(status__in=('renewed', 'cancelled'))[:10]]})
    out.append({'title': 'My government services', 'kind': 'pro', 'rows': [{'id': p.pk, 'title': p.get_service_display(), 'sub': p.number, 'badge': p.get_status_display()}
                                                                           for p in ProService.objects.filter(employee=e).exclude(status__in=('completed', 'cancelled'))[:10]]})
    return out


def daily():
    s = app.settings()
    out = {'renewals_started': 0, 'exit_sent': 0, 'show_cause_overdue': 0}
    t = today()
    if s.renewal_auto:
        from DashboardManagement.documents import current_documents
        from EmpManagement.models import emp_master
        ids = list(emp_master.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).values_list('id', flat=True))
        for x in current_documents(ids, t):
            if x['expiry'] and x['days'] is not None and 0 <= x['days'] <= (s.renewal_days or 60):
                done = DocumentRenewal.objects.filter(employee_id=x['employee_id'], document__iexact=x['type'], status='renewed', new_expiry__gt=x['expiry']).exists()
                if not done:
                    _, made = start_renewal(x['employee_id'], x)
                    out['renewals_started'] += made
    if s.exit_auto:
        try:
            from EmpManagement.models import EmployeeResignation
            for rz in EmployeeResignation.objects.filter(status__iexact='approved', last_working_date__gte=t,
                                                         last_working_date__lte=t + timedelta(days=s.exit_days_before or 7)).select_related('employee'):
                if not ExitInterview.objects.filter(employee=rz.employee).exists():
                    x = ExitInterview.objects.create(number=app.next_number('exit_interview', 'EXI'), employee=rz.employee, resignation=rz,
                                                     last_working_date=rz.last_working_date, questions=exit_questions(), branch_id=rz.employee.emp_branch_id_id)
                    send_exit(x)
                    out['exit_sent'] += 1
        except Exception:
            pass
    try:   # v1.58.0 assignments start / end on their dates; functional reviews for active appraisal cycles
        from . import matrix as MX
        out['assignments_changed'] = MX.refresh_statuses(t)
        out['functional_reviews'] = MX.open_reviews()
    except Exception:
        pass
    for c in DisciplinaryCase.objects.filter(status='show_cause', response_due__lt=t, response_received__isnull=True):
        for u in app.users_with_role('hro_hr'):
            app.todo(u, f'Show-cause reply overdue: {c.number}', 'case', c.pk, c.number, link('case', c.pk), f'Due {d(c.response_due)}')
        out['show_cause_overdue'] += 1
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily

from . import helpdesk  # noqa: E402,F401  v1.51.0 HR helpdesk (registers, dashboard, daily)
from . import matrix_app  # noqa: E402,F401  v1.58.0 assignments, functional managers, functional reviews


# ================================================================== hooks
def h_start_renewal(employee_id, doc_type, number='', expiry=None, user=None):
    r, _ = start_renewal(employee_id, {'type': doc_type, 'number': number, 'expiry': expiry, 'source': 'manual'}, user)
    return {'id': r.pk, 'number': r.number}


app.hooks.update({'start_renewal': h_start_renewal})
