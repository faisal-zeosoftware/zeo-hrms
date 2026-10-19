"""ZEO People on the app kit: benefits, e-signature, forms, surveys, recognition, 360° reviews, development plans.
Chat, survey taking, form filling, signing and the reviewer sheet have their own screens (views.py)."""
import hashlib
import secrets
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from zeo.appkit.engine import Act, App, AppError, Reg, d, emp_name, in_branches, now, today, user_name

from .models import (Award, BenefitClaim, BenefitPlan, ChatMessage, Competency, Conversation, DevelopmentPlan, Enrolment, FormSubmission, FormTemplate,
                     PeopleEvent, PeopleFile, PeopleSequence, PeopleSettings, ROLES, Recognition, Review360, Reviewer, Reward, SignRequest, Survey,
                     SurveyResponse)
from .packs import PACKS

PAGE = '/main-sidebar/zeo-people/'
app = App(label='ZeoPeople', slug='zeo-people', title='People', settings_model=PeopleSettings, sequence_model=PeopleSequence, event_model=PeopleEvent,
          file_model=PeopleFile, roles=ROLES, role_prefix='ppl_', manager_role='ppl_manager', packs=PACKS,
          pack_labels={k: v['label'] for k, v in PACKS.items()}, pack_descriptions={k: v['description'] for k, v in PACKS.items()},
          industry_from_company={'CON': 'construction', 'HC': 'healthcare', 'HOS': 'healthcare', 'RET': 'retail', 'HSP': 'retail'},
          pack_models={'award': (Award, 'code'), 'competency': (Competency, 'code'), 'benefit_plan': (BenefitPlan, 'code'), 'form_template': (FormTemplate, 'code'),
                       'survey': (Survey, 'number')},
          links=(), page=PAGE)
app.settings_fields = ('points_currency', 'points_value', 'recognition_needs_approval', 'esign_otp', 'esign_expiry_days', 'chat_retention_days', 'claim_component', 'review_relations')

MANAGE = ('ppl_manager',)
HR = ('ppl_manager', 'ppl_hr')
BEN = ('ppl_manager', 'ppl_benefits')
VIEW = ('ppl_viewer',)


def me(request):
    return app.my_emp(request)


def is_manager_of(emp, request):
    """The logged-in user is the reporting manager of the employee."""
    return bool(emp is not None and emp.emp_reporting_manager_id and emp.emp_reporting_manager_id == request.user.pk)


def own_or_manager(o, request, field='employee'):
    e = me(request)
    emp = getattr(o, field)
    return (e is not None and getattr(o, field + '_id') == e.pk) or is_manager_of(emp, request)


def reports_q(request):
    """Employees who report to the logged-in user."""
    return Q(employee__emp_reporting_manager=request.user)


# ------------------------------------------------------------------ options
app.option('benefit-plans', lambda: BenefitPlan.objects.filter(is_active=True), lambda x: f'{x.name} ({x.get_kind_display()})', ('name', 'code'), BenefitPlan)
app.option('enrolments', lambda: Enrolment.objects.filter(status='active').select_related('plan', 'employee'), lambda x: f'{x.number} {x.plan.name} – {emp_name(x.employee)}', ('number', 'plan__name'), Enrolment)
app.option('form-templates', lambda: FormTemplate.objects.filter(is_active=True), lambda x: x.name, ('name', 'code'), FormTemplate)
app.option('awards', lambda: Award.objects.filter(is_active=True), lambda x: f'{x.name} (+{x.points})', ('name', 'code'), Award)
app.option('competencies', lambda: Competency.objects.filter(is_active=True), lambda x: f'{x.name} ({x.get_group_display()})', ('name', 'code'), Competency)
app.option('reviews', lambda: Review360.objects.all().select_related('employee'), lambda x: f'{x.number} {emp_name(x.employee)}', ('number', 'employee__emp_first_name'), Review360)
app.option('surveys', lambda: Survey.objects.all(), lambda x: f'{x.number} {x.title}', ('title', 'number'), Survey)
app.option('sign-requests', lambda: SignRequest.objects.all(), lambda x: f'{x.number} {x.title}', ('title', 'number'), SignRequest)


# ================================================================== benefits
def _component(code, name, kind, category):
    from PayrollManagement.models import SalaryComponent
    c = SalaryComponent.objects.filter(code=code).first()
    if c is None:
        c = SalaryComponent.objects.create(name=name[:100], code=code[:20], component_type=kind, payroll_category=category, component_value_type='fixed',
                                           deduct_leave=False, show_in_payslip=True, description='Made by ZEO People (benefits)')
    return c


def _ess_set(employee, component, amount, valid_from=None, valid_until=None, add=False):
    """One EmployeeSalaryStructure row per (employee, component): set or add the amount, keep it active for the period."""
    from PayrollManagement.models import EmployeeSalaryStructure as ESS
    row = ESS.objects.filter(employee=employee, component=component).first()
    if row is None:
        row = ESS(employee=employee, component=component, amount=Decimal('0'))
    row.amount = (Decimal(str(row.amount or 0)) if add and row.is_active and row.valid_until == valid_until else Decimal('0')) + Decimal(str(amount))
    row.is_active = True
    row.valid_from, row.valid_until = valid_from, valid_until
    row.save()
    return row


def _ess_stop(employee, component):
    from PayrollManagement.models import EmployeeSalaryStructure as ESS
    ESS.objects.filter(employee=employee, component=component).update(is_active=False, valid_until=today())


def eligible(plan, employee):
    """'' when eligible, else the reason."""
    j = getattr(employee, 'emp_joined_date', None)
    if plan.eligible_after_months and j and (today() - j).days < plan.eligible_after_months * 30:
        return f'eligible after {plan.eligible_after_months} months of service'
    if plan.eligible_categories:
        cats = [c.strip().lower() for c in plan.eligible_categories.split(',') if c.strip()]
        cat = (getattr(getattr(employee, 'emp_ctgry_id', None), 'ctgry_title', '') or '').lower()
        if cats and cat not in cats:
            return f'only for {plan.eligible_categories}'
    return ''


def enrol_defaults(o, request):
    if not o.employee_id:
        o.employee = me(request)
    if o.employee_id is None:
        raise AppError('Choose the employee.', 'employee')
    if not o.start:
        o.start = today()
    o.employee_cost = o.plan.employee_cost
    if not app.any(request, *BEN) and not o.plan.needs_approval:
        o.status = 'active'


def enrol_check(o, request, old):
    if old is None:
        why = eligible(o.plan, o.employee)
        if why and not app.any(request, *BEN):
            raise AppError(f'{o.plan.name}: {why}.', 'plan')
        if Enrolment.objects.filter(plan=o.plan, employee=o.employee, status__in=('requested', 'active')).exists():
            raise AppError('This employee is already enrolled (or has asked to be) in this plan.', 'plan')
        if o.plan.covers_dependants and o.plan.max_dependants and len(o.dependants or []) > o.plan.max_dependants:
            raise AppError(f'At most {o.plan.max_dependants} dependants.', 'dependants')


def activate_enrolment(o, request):
    o.status, o.decided_by, o.decided_at = 'active', request.user, now()
    o.save()
    if o.employee_cost and o.employee_cost > 0:
        code = o.plan.deduction_component or f'BEN-{o.plan.code}'[:20]
        comp = _component(code, f'{o.plan.name} – employee share', 'deduction', 'other_allowance')
        if not o.plan.deduction_component:
            o.plan.deduction_component = code
            o.plan.save(update_fields=['deduction_component'])
        _ess_set(o.employee, comp, o.employee_cost, o.start, o.end)
    app.event('enrolment', o.pk, 'Active' + (f' – deduction {o.employee_cost}/month in payroll' if o.employee_cost else ''), request.user)
    app.tell_emp(o.employee, f'Benefit enrolment {o.number} approved', f'You are enrolled in {o.plan.name} from {o.start}.', 'enrolment', o.pk, o.number, PAGE + f'r/enrolment/{o.pk}')


def enrol_saver(o, request, creating, old):
    o.save()
    if creating and o.status == 'active':
        activate_enrolment(o, request)
    elif creating:
        for u in app.users_with_role('ppl_benefits'):
            app.todo(u, f'Enrolment to approve: {emp_name(o.employee)} – {o.plan.name}', 'enrolment', o.pk, o.number, PAGE + f'r/enrolment/{o.pk}')
    return o


def a_enrol_approve(o, request, f):
    o.member_no = f.s('member_no') or o.member_no
    activate_enrolment(o, request)
    app.close_todos('enrolment', o.pk)
    return o


def a_enrol_reject(o, request, f):
    o.status, o.decided_by, o.decided_at, o.note = 'rejected', request.user, now(), f.s('note')
    o.save()
    app.event('enrolment', o.pk, f'Rejected – {f.s("note")}', request.user)
    app.close_todos('enrolment', o.pk)
    app.tell_emp(o.employee, f'Benefit enrolment {o.number} not approved', f.s('note'), 'enrolment', o.pk, o.number, PAGE + f'r/enrolment/{o.pk}')
    return o


def a_enrol_end(o, request, f):
    o.status, o.end = 'ended', f.dt('end') or today()
    o.save()
    if o.plan.deduction_component:
        from PayrollManagement.models import SalaryComponent
        c = SalaryComponent.objects.filter(code=o.plan.deduction_component).first()
        if c:
            _ess_stop(o.employee, c)
    app.event('enrolment', o.pk, f'Ended on {o.end}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


def claim_defaults(o, request):
    if not o.enrolment_id:
        raise AppError('Choose the benefit enrolment.', 'enrolment')
    o.employee = o.enrolment.employee
    if not o.date:
        o.date = today()


def claim_check(o, request, old):
    if o.enrolment.status != 'active':
        raise AppError('The enrolment is not active.', 'enrolment')
    if me(request) and o.employee_id != me(request).pk and not app.any(request, *BEN):
        raise AppError('You can only claim on your own enrolment.', 'enrolment')
    lim = o.enrolment.plan.claim_limit_year
    if lim is not None and old is None:
        used = BenefitClaim.objects.filter(enrolment=o.enrolment, status__in=('approved', 'paid'), date__year=o.date.year).aggregate(s=Sum('approved_amount'))['s'] or 0
        if used + o.amount > lim:
            raise AppError(f'Claim limit {lim} per year – {used} already used.', 'amount')


def claim_saver(o, request, creating, old):
    o.save()
    if creating:
        for u in app.users_with_role('ppl_benefits'):
            app.todo(u, f'Claim to approve: {emp_name(o.employee)} {o.amount}', 'claim', o.pk, o.number, PAGE + f'r/claim/{o.pk}')
    return o


def a_claim_approve(o, request, f):
    o.approved_amount = f.n('approved_amount') if f.n('approved_amount') is not None else o.amount
    o.status, o.decided_by, o.decision_note = 'approved', request.user, f.s('note')
    o.save()
    app.event('claim', o.pk, f'Approved {o.approved_amount}' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('claim', o.pk)
    app.tell_emp(o.employee, f'Claim {o.number} approved', f'{o.approved_amount} approved for {o.description}.', 'claim', o.pk, o.number, PAGE + f'r/claim/{o.pk}')
    return o


def a_claim_reject(o, request, f):
    o.status, o.decided_by, o.decision_note = 'rejected', request.user, f.s('note')
    o.save()
    app.event('claim', o.pk, f'Rejected – {f.s("note")}', request.user)
    app.close_todos('claim', o.pk)
    return o


def a_claim_pay(o, request, f):
    """Adds the approved amount to the employee's salary structure for one month (next month unless chosen)."""
    month = f.dt('month') or (today().replace(day=1) + timedelta(days=32)).replace(day=1)
    month = month.replace(day=1)
    code = app.settings().claim_component or 'BENCLAIM'
    comp = _component(code, 'Benefit claims', 'addition', 'medical_allowance')
    last = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    _ess_set(o.employee, comp, o.approved_amount or o.amount, month, last, add=True)
    o.status, o.paid_month = 'paid', month
    o.save()
    app.event('claim', o.pk, f'Paid in payroll {month:%B %Y} (component {code})', request.user)
    return o


def a_claim_paid(o, request, f):
    o.status = 'paid'
    o.save(update_fields=['status'])
    app.event('claim', o.pk, 'Paid outside payroll' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    return o


app.reg(Reg('benefit_plan', BenefitPlan, 'Benefit plan', 'Benefit plans', 'Benefits',
            cols=('code', 'name', 'kind', 'provider', 'employee_cost', 'employer_cost', 'eligible_after_months', 'covers_dependants', 'is_active', 'review_status'),
            form=('code', 'name', 'kind', 'provider', 'policy_no', 'description', 'employee_cost', 'employer_cost', 'eligible_after_months', 'eligible_categories',
                  'covers_dependants', 'max_dependants', 'claim_limit_year', 'deduction_component', 'needs_approval', 'renewal_date', 'documents_note', 'is_active'),
            manage=BEN, view=VIEW + HR + ('ppl_benefits',), public=lambda r: Q(is_active=True), search=('code', 'name', 'provider'), filters=('kind', 'is_active', 'review_status'),
            title='name', subtitle='provider', review=True, deletable=True, children=(('Enrolments', 'enrolment', 'plan'),),
            help='What the company offers. Every employee sees the active plans and can ask to enrol; the employee share is deducted in payroll.'))
app.reg(Reg('enrolment', Enrolment, 'Enrolment', 'Enrolments', 'Benefits',
            cols=('number', 'plan', 'employee', 'start', 'end', 'employee_cost', 'status'), form=('plan', 'employee', 'start', 'end', 'dependants', 'note'),
            number=('enrolment', 'BEN'), manage=BEN, view=VIEW + HR, create='all', own=(('employee', 'emp'),), public=reports_q,
            search=('number', 'plan__name', 'employee__emp_first_name', 'employee__emp_last_name', 'member_no'), filters=('status', 'plan', 'employee'),
            title='number', subtitle='plan', defaults=enrol_defaults, check=enrol_check, saver=enrol_saver, edit_when=lambda o: o.status == 'requested',
            info=('status', 'member_no', 'decided_by', 'decided_at'), children=(('Claims', 'claim', 'enrolment'),),
            actions=[Act('approve', 'Approve', a_enrol_approve, lambda o, r: o.status == 'requested' and app.any(r, *BEN), style='primary', fields=(('member_no', 'Member / card no.', 'text', False),)),
                     Act('reject', 'Reject', a_enrol_reject, lambda o, r: o.status == 'requested' and app.any(r, *BEN), style='danger', fields=(('note', 'Why', 'text', True),)),
                     Act('end', 'End enrolment', a_enrol_end, lambda o, r: o.status == 'active' and app.any(r, *BEN), fields=(('end', 'End date', 'date', False), ('note', 'Note', 'text', False)))],
            help='An employee in a plan. Request → the benefits administrator approves → the employee share goes to payroll.'))
app.reg(Reg('claim', BenefitClaim, 'Claim', 'Claims', 'Benefits',
            cols=('number', 'employee', 'enrolment', 'date', 'description', 'amount', 'approved_amount', 'status'), form=('enrolment', 'date', 'description', 'amount'),
            number=('claim', 'CLM'), manage=BEN, view=VIEW + HR, create='all', own=(('employee', 'emp'),), public=reports_q,
            search=('number', 'description', 'employee__emp_first_name', 'employee__emp_last_name'), filters=('status', 'enrolment'),
            title='number', subtitle='description', defaults=claim_defaults, check=claim_check, saver=claim_saver, edit_when=lambda o: o.status == 'submitted',
            info=('status', 'approved_amount', 'decision_note', 'paid_month'),
            actions=[Act('approve', 'Approve', a_claim_approve, lambda o, r: o.status == 'submitted' and app.any(r, *BEN), style='primary',
                         fields=(('approved_amount', 'Approved amount (empty = as claimed)', 'decimal', False), ('note', 'Note', 'text', False))),
                     Act('reject', 'Reject', a_claim_reject, lambda o, r: o.status == 'submitted' and app.any(r, *BEN), style='danger', fields=(('note', 'Why', 'text', True),)),
                     Act('pay', 'Pay in payroll', a_claim_pay, lambda o, r: o.status == 'approved' and app.any(r, *BEN), style='primary', fields=(('month', 'Payroll month (empty = next month)', 'date', False),)),
                     Act('paid', 'Paid outside payroll', a_claim_paid, lambda o, r: o.status == 'approved' and app.any(r, *BEN), fields=(('note', 'Note', 'text', False),))],
            help='Reimbursement claims on an enrolment (medical bills, gym…). Approved claims can be paid in the next payroll.'))


# ================================================================== e-signature
def sign_defaults(o, request):
    o.requested_by = request.user
    if not o.employee_id and not o.signer_email:
        raise AppError('Choose the employee, or give the outside signer\'s name and e-mail.', 'employee')
    if not o.token:
        o.token = secrets.token_urlsafe(32)


def sign_check(o, request, old):
    if old is not None and not o.document and not o.text and not PeopleFile.objects.filter(model='sign_request', object_id=o.pk, name__iendswith='.pdf').exists():
        raise AppError('Attach the PDF (Files) or write the text to sign.', 'text')


def attach_pdf(o):
    """The first PDF attached to the request becomes the document to sign."""
    if not o.document:
        f = PeopleFile.objects.filter(model='sign_request', object_id=o.pk, name__iendswith='.pdf').order_by('id').first()
        if f is not None:
            o.document = f.file
            o.save(update_fields=['document'])


def send_sign(o, request, remind=False):
    s = app.settings()
    attach_pdf(o)
    if o.status == 'draft' and not o.document and not o.text:
        raise AppError('Attach the PDF (Files) or write the text to sign first.')
    if o.status == 'draft':
        o.status, o.sent_at = 'sent', now()
        o.expires_at = now() + timedelta(days=s.esign_expiry_days or 14)
    o.audit = (o.audit or []) + [{'at': now().isoformat(), 'what': 'reminded' if remind else 'sent', 'by': user_name(request.user)}]
    o.save()
    link = PAGE + f'sign/{o.pk}'
    if o.employee_id:
        app.tell_emp(o.employee, f'Please sign: {o.title}', (o.message or f'Please sign {o.title} in ZEO (Self service → Signatures).') + f'\n{link}',
                     'sign_request', o.pk, o.number, link, summary=f'Sign: {o.title}')
    if o.signer_email:
        app.send_mail([o.signer_email], f'Please sign: {o.title}', (o.message or f'Please open the link below to read and sign {o.title}.') + f'\n\n/sign/{o.token}')
    app.event('sign_request', o.pk, 'Reminder sent' if remind else 'Sent to the signer', request.user)


def a_sign_send(o, request, f):
    o.message = f.s('message') or o.message
    send_sign(o, request)
    return o


def a_sign_remind(o, request, f):
    send_sign(o, request, remind=True)
    return o


def a_sign_cancel(o, request, f):
    o.status = 'cancelled'
    o.audit = (o.audit or []) + [{'at': now().isoformat(), 'what': 'cancelled', 'by': user_name(request.user), 'note': f.s('note')}]
    o.save()
    app.event('sign_request', o.pk, 'Cancelled' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('sign_request', o.pk)
    return o


def sign_extra(o, r):
    return {'sign_page': PAGE + f'sign/{o.pk}', 'public_link': f'/sign/{o.token}' if o.signer_email else '', 'can_sign': o.status == 'sent' and own_or_manager(o, r) and bool(o.employee_id),
            'audit': o.audit, 'has_document': bool(o.document), 'signed_file': bool(o.signed_document)}


app.reg(Reg('sign_request', SignRequest, 'Signature request', 'Signatures', 'E-signature',
            cols=('number', 'title', 'kind', 'employee', 'signer_name', 'sent_at', 'signed_at', 'status'),
            form=('title', 'kind', 'text', 'employee', 'signer_name', 'signer_email', 'message'),
            number=('sign_request', 'SGN'), manage=HR, view=VIEW, own=(('employee', 'emp'),),
            search=('number', 'title', 'employee__emp_first_name', 'employee__emp_last_name', 'signer_name'), filters=('status', 'kind', 'employee'),
            title='title', subtitle='employee', defaults=sign_defaults, check=sign_check, edit_when=lambda o: o.status == 'draft',
            info=('status', 'sent_at', 'expires_at', 'signed_at', 'signed_ip', 'decline_reason'), extra=sign_extra,
            actions=[Act('send', 'Send for signature', a_sign_send, lambda o, r: o.status == 'draft' and app.any(r, *HR), style='primary', fields=(('message', 'Message to the signer', 'textarea', False),)),
                     Act('remind', 'Remind', a_sign_remind, lambda o, r: o.status == 'sent' and app.any(r, *HR)),
                     Act('cancel', 'Cancel', a_sign_cancel, lambda o, r: o.status in ('draft', 'sent') and app.any(r, *HR), style='danger', fields=(('note', 'Why', 'text', False),))],
            help='Letters, offers, contracts, policies and forms signed electronically. Attach the PDF (Attachments / Files) or write the text; the signer '
                 'reads it, gets a one-time code by e-mail, draws or types the signature; ZEO stamps the PDF and keeps the audit trail (who, when, from where).'))


# ================================================================== forms
app.reg(Reg('form_template', FormTemplate, 'Form', 'Form templates', 'Forms',
            cols=('code', 'name', 'audience', 'needs_approval', 'approver', 'sign', 'is_active', 'review_status'),
            form=('code', 'name', 'description', 'fields', 'audience', 'needs_approval', 'approver', 'sign', 'notify_email', 'once', 'is_active'),
            manage=HR, view=VIEW, public=lambda r: Q(is_active=True), search=('code', 'name'), filters=('audience', 'needs_approval', 'is_active', 'review_status'),
            title='name', review=True, deletable=True, children=(('Submissions', 'form_submission', 'template'),),
            help='Build a form: fields (text, number, date, choice, yes/no, file), who may fill it, who approves, whether it is signed. Employees fill it in Self service → Forms.'))


def sub_defaults(o, request):
    if not o.employee_id:
        o.employee = me(request)
    if o.employee_id is None:
        raise AppError('Choose the employee.', 'employee')


def sub_check(o, request, old):
    t = o.template
    if old is None and t.once and FormSubmission.objects.filter(template=t, employee=o.employee).exclude(status='rejected').exists():
        raise AppError('This form has already been submitted.', 'template')
    missing = [f['label'] for f in (t.fields or []) if f.get('required') and (o.data or {}).get(f['name']) in (None, '', [], False) and f.get('type') != 'yesno']
    if missing:
        raise AppError('Required: ' + ', '.join(missing), 'data')
    if old is None and not t.needs_approval:
        o.status = 'approved'


def sub_saver(o, request, creating, old):
    o.save()
    if creating:
        t = o.template
        if t.needs_approval:
            if t.approver == 'manager' and o.employee.emp_reporting_manager_id:
                app.todo(o.employee.emp_reporting_manager, f'Form to approve: {t.name} – {emp_name(o.employee)}', 'form_submission', o.pk, o.number, PAGE + f'r/form_submission/{o.pk}')
            else:
                for u in app.users_with_role('ppl_hr'):
                    app.todo(u, f'Form to approve: {t.name} – {emp_name(o.employee)}', 'form_submission', o.pk, o.number, PAGE + f'r/form_submission/{o.pk}')
        if t.notify_email:
            body = '\n'.join(f'{f["label"]}: {(o.data or {}).get(f["name"], "")}' for f in t.fields or [])
            app.send_mail([t.notify_email], f'{t.name} – {emp_name(o.employee)}', body)
        if t.sign:
            sr = SignRequest.objects.create(number=app.next_number('sign_request', 'SGN'), title=f'{t.name} – {o.number}', kind='form', employee=o.employee,
                                            text=(t.description + '\n\n' if t.description else '') + '\n'.join(f'{f["label"]}: {(o.data or {}).get(f["name"], "")}' for f in t.fields or []),
                                            token=secrets.token_urlsafe(32), requested_by=request.user, source_model='ZeoPeople.FormSubmission', source_id=o.pk, branch_id=o.branch_id)
            o.sign_request = sr
            o.save(update_fields=['sign_request'])
            send_sign(sr, request)
    return o


def a_sub_approve(o, request, f):
    o.status, o.decided_by, o.decided_at, o.decision_note = 'approved', request.user, now(), f.s('note')
    o.save()
    app.event('form_submission', o.pk, 'Approved' + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
    app.close_todos('form_submission', o.pk)
    app.tell_emp(o.employee, f'{o.template.name} approved', f.s('note'), 'form_submission', o.pk, o.number, PAGE + f'r/form_submission/{o.pk}')
    return o


def a_sub_reject(o, request, f):
    o.status, o.decided_by, o.decided_at, o.decision_note = 'rejected', request.user, now(), f.s('note')
    o.save()
    app.event('form_submission', o.pk, f'Rejected – {f.s("note")}', request.user)
    app.close_todos('form_submission', o.pk)
    app.tell_emp(o.employee, f'{o.template.name} not approved', f.s('note'), 'form_submission', o.pk, o.number, PAGE + f'r/form_submission/{o.pk}')
    return o


def can_decide_sub(o, r):
    return o.status == 'submitted' and (app.any(r, *HR) or (o.template.approver == 'manager' and is_manager_of(o.employee, r)))


def sub_extra(o, r):
    return {'answers': [{'label': f.get('label', f.get('name')), 'value': (o.data or {}).get(f.get('name'), ''), 'type': f.get('type', 'text')} for f in (o.template.fields or [])],
            'links': {'sign_request': o.sign_request.number + ' – ' + o.sign_request.get_status_display() if o.sign_request_id else ''}}


app.reg(Reg('form_submission', FormSubmission, 'Submission', 'Form submissions', 'Forms',
            cols=('number', 'template', 'employee', 'created_at', 'status'), form=('template', 'employee', 'data'),
            number=('form_submission', 'FRM'), manage=HR, view=VIEW, create='all', own=(('employee', 'emp'),), public=reports_q,
            search=('number', 'template__name', 'employee__emp_first_name', 'employee__emp_last_name'), filters=('status', 'template', 'employee'),
            title='number', subtitle='template', defaults=sub_defaults, check=sub_check, saver=sub_saver, edit_when=lambda o: False,
            info=('status', 'decision_note', 'decided_by', 'decided_at'), extra=sub_extra,
            actions=[Act('approve', 'Approve', a_sub_approve, can_decide_sub, style='primary', fields=(('note', 'Note', 'text', False),)),
                     Act('reject', 'Reject', a_sub_reject, can_decide_sub, style='danger', fields=(('note', 'Why', 'text', True),))],
            help='Filled forms. Fill a form from Self service → Forms (the screen shows the form\'s own fields).'))


# ================================================================== surveys
def survey_defaults(o, request):
    if not o.start:
        o.start = today()


def a_survey_open(o, request, f):
    if not o.questions:
        raise AppError('Add questions first.', 'questions')
    o.status = 'open'
    if not o.start:
        o.start = today()
    o.save()
    app.event('survey', o.pk, 'Opened', request.user)
    n = 0
    for e in audience(o):
        if e.users_id:
            app.todo(e.users, f'Survey: {o.title}', 'survey', o.pk, o.number, PAGE + f'survey/{o.pk}', 'Takes a few minutes. ' + ('Answers are anonymous.' if o.anonymous else ''))
            n += 1
    return {'message': f'Open – {n} employees invited.'}


def a_survey_close(o, request, f):
    o.status = 'closed'
    o.save(update_fields=['status'])
    app.event('survey', o.pk, 'Closed', request.user)
    app.close_todos('survey', o.pk)
    return o


def a_survey_remind(o, request, f):
    n = 0
    done = set(SurveyResponse.objects.filter(survey=o).values_list('employee_key', flat=True))
    for e in audience(o):
        if e.users_id and emp_key(o, e) not in done:
            app.todo(e.users, f'Survey: {o.title}', 'survey', o.pk, o.number, PAGE + f'survey/{o.pk}', 'Reminder – please answer.')
            n += 1
    app.event('survey', o.pk, f'Reminder to {n} employees', request.user)
    return {'message': f'{n} reminded.'}


def a_survey_copy(o, request, f):
    n = Survey(number=app.next_number('survey', 'SRV'), title=f.s('title') or f'{o.title} (copy)', description=o.description, kind=o.kind, anonymous=o.anonymous,
               questions=o.questions, audience=o.audience, departments=o.departments, status='draft', branch_id=o.branch_id)
    n.save()
    app.event('survey', n.pk, f'Copied from {o.number}', request.user)
    return {'message': f'{n.number} made.', 'created': {'app': 'zeo-people', 'kind': 'survey', 'id': n.pk}}


def emp_key(survey, e):
    return hashlib.sha256(f'{survey.pk}:{e.pk}:zeo-people'.encode()).hexdigest()[:40]


def audience(survey):
    from EmpManagement.models import emp_master
    qs = emp_master.objects.filter(is_active=True) if hasattr(emp_master, 'is_active') else emp_master.objects.all()
    if survey.branch_id:
        qs = qs.filter(emp_branch_id=survey.branch_id)
    if survey.departments:
        codes = [c.strip() for c in survey.departments.split(',') if c.strip()]
        qs = qs.filter(Q(emp_dept_id__dept_code__in=codes) | Q(emp_dept_id__dept_name__in=codes))
    if survey.audience == 'managers':
        qs = qs.filter(users__in=emp_master.objects.exclude(emp_reporting_manager=None).values('emp_reporting_manager'))
    return qs.select_related('users', 'emp_dept_id')


def survey_extra(o, r):
    total = audience(o).count()
    n = o.responses.count()
    return {'take_page': PAGE + f'survey/{o.pk}', 'results_page': PAGE + f'survey/{o.pk}/results', 'responses': n, 'invited': total,
            'rate': round(100 * n / total, 1) if total else 0, 'can_take': o.status == 'open', 'question_count': len(o.questions or [])}


app.reg(Reg('survey', Survey, 'Survey', 'Surveys', 'Surveys',
            cols=('number', 'title', 'kind', 'anonymous', 'start', 'end', 'status', 'review_status'),
            form=('title', 'kind', 'description', 'anonymous', 'questions', 'audience', 'departments', 'start', 'end', 'reminder_days'),
            number=('survey', 'SRV'), manage=HR, view=VIEW, public=lambda r: Q(status='open'), search=('number', 'title'), filters=('status', 'kind', 'anonymous'),
            title='title', subtitle='kind', defaults=survey_defaults, edit_when=lambda o: o.status == 'draft', review=True, deletable=True,
            info=('status',), extra=survey_extra,
            actions=[Act('open', 'Open the survey', a_survey_open, lambda o, r: o.status == 'draft' and app.any(r, *HR), style='primary'),
                     Act('remind', 'Remind non-responders', a_survey_remind, lambda o, r: o.status == 'open' and app.any(r, *HR)),
                     Act('close', 'Close', a_survey_close, lambda o, r: o.status == 'open' and app.any(r, *HR), style='danger'),
                     Act('copy', 'Copy as a new survey', a_survey_copy, lambda o, r: app.any(r, *HR), fields=(('title', 'Title', 'text', False),))],
            help='Engagement, pulse, onboarding, exit and training surveys. Rating (1–5), yes / no, choice and free-text questions; anonymous when ticked.'))


# ================================================================== recognition
def rec_defaults(o, request):
    if not o.given_by_id:
        o.given_by = me(request)
    o.points = o.award.points
    s = app.settings()
    if s.recognition_needs_approval and not app.any(request, *MANAGE):
        o.status = 'pending'


def rec_check(o, request, old):
    if old is not None:
        return
    a = o.award
    if o.given_by_id and o.given_by_id == o.employee_id:
        raise AppError('You cannot recognise yourself.', 'employee')
    if a.who_can_give == 'managers' and not (app.any(request, *HR) or is_manager_of(o.employee, request)):
        raise AppError(f'{a.name} is given by managers.', 'award')
    if a.who_can_give == 'hr' and not app.any(request, *HR):
        raise AppError(f'{a.name} is given by the People manager / HR.', 'award')
    if a.monthly_limit and o.given_by_id:
        t = today()
        n = Recognition.objects.filter(award=a, given_by=o.given_by, created_at__year=t.year, created_at__month=t.month).exclude(status='rejected').count()
        if n >= a.monthly_limit:
            raise AppError(f'{a.name}: you can give it {a.monthly_limit} time(s) a month.', 'award')


def rec_saver(o, request, creating, old):
    o.save()
    if creating:
        if o.status == 'pending':
            for u in app.users_with_role('ppl_manager'):
                app.todo(u, f'Recognition to approve: {emp_name(o.employee)}', 'recognition', o.pk, o.number, PAGE + f'r/recognition/{o.pk}')
        else:
            app.tell_emp(o.employee, f'You were recognised: {o.award.name}', f'{emp_name(o.given_by)}: {o.message}', 'recognition', o.pk, o.number, PAGE + 'wall')
    return o


def a_rec_approve(o, request, f):
    o.status = 'published'
    o.save(update_fields=['status'])
    app.event('recognition', o.pk, 'Published', request.user)
    app.close_todos('recognition', o.pk)
    app.tell_emp(o.employee, f'You were recognised: {o.award.name}', f'{emp_name(o.given_by)}: {o.message}', 'recognition', o.pk, o.number, PAGE + 'wall')
    return o


def a_rec_reject(o, request, f):
    o.status = 'rejected'
    o.save(update_fields=['status'])
    app.event('recognition', o.pk, f'Not published – {f.s("note")}', request.user)
    app.close_todos('recognition', o.pk)
    return o


def points_of(emp_id):
    got = Recognition.objects.filter(employee_id=emp_id, status='published').aggregate(s=Sum('points'))['s'] or 0
    used = Reward.objects.filter(employee_id=emp_id, status__in=('requested', 'approved', 'given')).aggregate(s=Sum('points'))['s'] or 0
    return int(got) - int(used)


app.reg(Reg('award', Award, 'Award', 'Awards', 'Recognition', cols=('code', 'name', 'icon', 'points', 'who_can_give', 'monthly_limit', 'is_active', 'review_status'),
            form=('code', 'name', 'icon', 'points', 'description', 'who_can_give', 'monthly_limit', 'is_active'), manage=MANAGE, view=VIEW + HR,
            public=lambda r: Q(is_active=True), search=('code', 'name'), filters=('who_can_give', 'is_active', 'review_status'), title='name', review=True, deletable=True, files=False,
            help='Awards and their points. Anyone can give a Thank you; managers give the bigger awards.'))
app.reg(Reg('recognition', Recognition, 'Recognition', 'Recognitions', 'Recognition',
            cols=('number', 'employee', 'award', 'given_by', 'points', 'created_at', 'status'), form=('employee', 'award', 'message'),
            number=('recognition', 'REC'), manage=MANAGE, view=VIEW + HR, create='all', own=(('employee', 'emp'), ('given_by', 'emp')), public=lambda r: Q(status='published'),
            search=('number', 'employee__emp_first_name', 'employee__emp_last_name', 'message'), filters=('status', 'award', 'employee'),
            title='number', subtitle='employee', defaults=rec_defaults, check=rec_check, saver=rec_saver, edit_when=lambda o: False, info=('status', 'points'),
            actions=[Act('approve', 'Publish', a_rec_approve, lambda o, r: o.status == 'pending' and app.any(r, *MANAGE), style='primary'),
                     Act('reject', 'Do not publish', a_rec_reject, lambda o, r: o.status == 'pending' and app.any(r, *MANAGE), style='danger', fields=(('note', 'Why', 'text', True),))],
            help='A thank-you or award to a colleague, with points. Published ones show on the Recognition wall.'))


def reward_defaults(o, request):
    if not o.employee_id:
        o.employee = me(request)


def reward_check(o, request, old):
    if old is None and o.points > points_of(o.employee_id):
        raise AppError(f'Only {points_of(o.employee_id)} points available.', 'points')


def reward_set(status_, text):
    def run(o, request, f):
        o.status, o.note = status_, f.s('note') or o.note
        o.save()
        app.event('reward', o.pk, text + (f' – {f.s("note")}' if f.s('note') else ''), request.user)
        if status_ in ('approved', 'given'):
            app.tell_emp(o.employee, f'Reward {text.lower()}: {o.item}', f.s('note'), 'reward', o.pk, o.number, PAGE + f'r/reward/{o.pk}')
        return o
    return run


app.reg(Reg('reward', Reward, 'Reward', 'Rewards', 'Recognition', cols=('number', 'employee', 'points', 'item', 'created_at', 'status'), form=('employee', 'points', 'item', 'note'),
            number=('reward', 'RWD'), manage=MANAGE, view=VIEW + HR, create='all', own=(('employee', 'emp'),), search=('number', 'item', 'employee__emp_first_name'), filters=('status', 'employee'),
            title='item', subtitle='employee', defaults=reward_defaults, check=reward_check, edit_when=lambda o: o.status == 'requested', info=('status', 'note'),
            actions=[Act('approve', 'Approve', reward_set('approved', 'Approved'), lambda o, r: o.status == 'requested' and app.any(r, *MANAGE), style='primary', fields=(('note', 'Note', 'text', False),)),
                     Act('given', 'Given', reward_set('given', 'Given'), lambda o, r: o.status == 'approved' and app.any(r, *MANAGE), fields=(('note', 'Note', 'text', False),)),
                     Act('reject', 'Reject', reward_set('rejected', 'Rejected'), lambda o, r: o.status == 'requested' and app.any(r, *MANAGE), style='danger', fields=(('note', 'Why', 'text', True),))],
            help='Points redeemed for a gift, voucher or a day off. The People manager approves.', extra=lambda o, r: {'points_available': points_of(o.employee_id)}))


# ================================================================== 360° reviews and development plans
app.reg(Reg('competency', Competency, 'Competency', 'Competencies', '360° reviews', cols=('code', 'name', 'group', 'for_managers', 'is_active', 'review_status'),
            form=('code', 'name', 'group', 'description', 'levels', 'for_managers', 'is_active'), manage=HR, view=VIEW + ('ppl_benefits',), public=lambda r: Q(is_active=True),
            search=('code', 'name'), filters=('group', 'is_active', 'review_status'), title='name', subtitle='group', review=True, deletable=True, files=False,
            help='The behaviours rated in 360° reviews, with the meaning of levels 1–5.'))


def r360_defaults(o, request):
    if not o.cycle_name:
        o.cycle_name = f'{today().year}'


def r360_saver(o, request, creating, old):
    o.save()
    if creating and not o.competencies.exists():
        o.competencies.set(Competency.objects.filter(is_active=True, for_managers=False))
    if creating:
        # self + manager reviewers by default
        Reviewer.objects.get_or_create(review=o, reviewer=o.employee, defaults={'relation': 'self'})
        mgr = o.employee.emp_reporting_manager
        if mgr is not None:
            m = mgr.employees.first() if hasattr(mgr, 'employees') else None
            if m is not None and m.pk != o.employee_id:
                Reviewer.objects.get_or_create(review=o, reviewer=m, defaults={'relation': 'manager'})
    return o


def a_r360_add(o, request, f):
    ids = f.ids('reviewers')
    rel = f.choice('relation', [(k, v) for k, v in Reviewer._meta.get_field('relation').choices], 'peer')
    from EmpManagement.models import emp_master
    n = 0
    for e in emp_master.objects.filter(pk__in=ids):
        _, made = Reviewer.objects.get_or_create(review=o, reviewer=e, defaults={'relation': rel})
        n += made
        if o.status == 'open' and made and e.users_id:
            app.todo(e.users, f'360° review of {emp_name(o.employee)}', 'review_360', o.pk, o.number, PAGE + f'review/{o.pk}', f'Due {d(o.due) or "soon"}.')
    app.event('review_360', o.pk, f'{n} reviewer(s) added as {rel}', request.user)
    return o


def a_r360_open(o, request, f):
    if not o.reviewers.exists():
        raise AppError('Add reviewers first.')
    o.status = 'open'
    o.due = f.dt('due') or o.due
    o.save()
    for rv in o.reviewers.filter(status='pending').select_related('reviewer'):
        if rv.reviewer.users_id:
            app.todo(rv.reviewer.users, f'360° review of {emp_name(o.employee)}', 'review_360', o.pk, o.number, PAGE + f'review/{o.pk}', f'Due {d(o.due) or "soon"}.')
    app.event('review_360', o.pk, 'Opened – reviewers invited', request.user)
    return o


def summarise(o):
    """Averages per competency per relation and overall (weights from settings)."""
    weights = app.settings().review_relations or {'self': 10, 'manager': 40, 'peer': 25, 'subordinate': 15, 'customer': 10}
    comps = list(o.competencies.all())
    done = list(o.reviewers.filter(status='done'))
    out = {'competencies': [], 'relations': {}, 'counts': {}, 'strengths': [], 'improve': [], 'answers': []}
    overall_w = overall_s = 0.0
    for c in comps:
        row = {'id': c.pk, 'name': c.name, 'group': c.get_group_display(), 'by_relation': {}, 'avg': None}
        tot = n = 0.0
        for rel, _ in Reviewer._meta.get_field('relation').choices:
            vals = [float(r.ratings.get(str(c.pk)) or 0) for r in done if r.relation == rel and r.ratings.get(str(c.pk))]
            if vals:
                row['by_relation'][rel] = round(sum(vals) / len(vals), 2)
                w = float(weights.get(rel, 10))
                tot += w * sum(vals) / len(vals)
                n += w
        if n:
            row['avg'] = round(tot / n, 2)
            overall_s += row['avg']
            overall_w += 1
        out['competencies'].append(row)
    for rel, label in Reviewer._meta.get_field('relation').choices:
        rs = [r for r in done if r.relation == rel]
        out['counts'][rel] = {'done': len(rs), 'total': o.reviewers.filter(relation=rel).count(), 'label': label}
        ov = [r.overall for r in rs if r.overall]
        if ov:
            out['relations'][rel] = round(sum(ov) / len(ov), 2)
    for r in done:
        who = '' if o.anonymous and r.relation != 'manager' else emp_name(r.reviewer)
        if r.strengths:
            out['strengths'].append({'who': who, 'relation': r.get_relation_display(), 'text': r.strengths})
        if r.improve:
            out['improve'].append({'who': who, 'relation': r.get_relation_display(), 'text': r.improve})
        for i, q in enumerate(o.open_questions or []):
            a = (r.answers or [None] * (i + 1))[i] if i < len(r.answers or []) else ''
            if a:
                out['answers'].append({'q': q, 'who': who, 'relation': r.get_relation_display(), 'text': a})
    o.summary = out
    o.overall = round(overall_s / overall_w, 2) if overall_w else None
    return out


def a_r360_close(o, request, f):
    summarise(o)
    o.status = 'closed'
    o.hr_comment = f.s('hr_comment') or o.hr_comment
    o.save()
    app.event('review_360', o.pk, f'Closed – overall {o.overall}', request.user)
    app.close_todos('review_360', o.pk)
    return o


def a_r360_share(o, request, f):
    if not o.summary:
        summarise(o)
    o.status = 'shared'
    o.save()
    app.event('review_360', o.pk, 'Shared with the employee', request.user)
    app.tell_emp(o.employee, f'Your 360° review {o.cycle_name} is ready', 'Open it in Self service → My reviews.', 'review_360', o.pk, o.number, PAGE + f'r/review_360/{o.pk}', summary='Read your 360° review')
    return o


def a_r360_plan(o, request, f):
    low = sorted([c for c in (o.summary or {}).get('competencies', []) if c.get('avg')], key=lambda c: c['avg'])[:3]
    p = DevelopmentPlan.objects.create(number=app.next_number('development_plan', 'PDP'), employee=o.employee, review=o, title=f.s('title') or f'Development plan {o.cycle_name}',
                                       start=today(), target_date=today() + timedelta(days=180), status='draft', branch_id=o.branch_id,
                                       steps=[{'area': c['name'], 'action': f'Improve {c["name"].lower()} (rated {c["avg"]})', 'kind': 'training', 'due': str(today() + timedelta(days=90)), 'done': False} for c in low])
    app.event('development_plan', p.pk, f'Made from 360° review {o.number}', request.user)
    return {'message': f'Plan {p.number} made with {len(low)} areas.', 'created': {'app': 'zeo-people', 'kind': 'development_plan', 'id': p.pk}}


def r360_extra(o, r):
    e = me(r)
    mine = o.reviewers.filter(reviewer=e).first() if e else None
    show = app.any(r, *HR) or is_manager_of(o.employee, r) or (o.status == 'shared' and e and o.employee_id == e.pk)
    return {'my_sheet': PAGE + f'review/{o.pk}' if mine and mine.status == 'pending' and o.status == 'open' else '', 'reviewers_done': o.reviewers.filter(status='done').count(),
            'reviewers_total': o.reviewers.count(), 'summary': o.summary if show and o.status in ('closed', 'shared') else None, 'overall': float(o.overall) if o.overall and show else None}


app.reg(Reg('review_360', Review360, '360° review', '360° reviews', '360° reviews',
            cols=('number', 'employee', 'cycle_name', 'due', 'overall', 'status'), form=('employee', 'cycle_name', 'period_from', 'period_to', 'competencies', 'open_questions', 'due', 'anonymous'),
            number=('review_360', 'R360'), manage=HR, view=VIEW, own=(('employee', 'emp'),), public=lambda r: Q(reviewers__reviewer__users=r.user) | reports_q(r),
            search=('number', 'employee__emp_first_name', 'employee__emp_last_name', 'cycle_name'), filters=('status', 'cycle_name', 'employee'),
            title='number', subtitle='employee', defaults=r360_defaults, saver=r360_saver, edit_when=lambda o: o.status == 'draft', info=('status', 'overall', 'hr_comment'),
            children=(('Reviewers', 'reviewer', 'review'),), extra=r360_extra, restricted=('hr_comment',), scope_fn=lambda qs, r: qs.distinct(),
            actions=[Act('add', 'Add reviewers', a_r360_add, lambda o, r: o.status in ('draft', 'open') and (app.any(r, *HR) or is_manager_of(o.employee, r)),
                         fields=(('reviewers', 'Reviewers', 'opt-multi:employees', True), ('relation', 'Relation', 'choice:peer=Peer|subordinate=Direct report|customer=Internal customer|manager=Manager', True))),
                     Act('open', 'Open – invite reviewers', a_r360_open, lambda o, r: o.status == 'draft' and (app.any(r, *HR) or is_manager_of(o.employee, r)), style='primary', fields=(('due', 'Due', 'date', False),)),
                     Act('close', 'Close and summarise', a_r360_close, lambda o, r: o.status == 'open' and app.any(r, *HR), fields=(('hr_comment', 'HR comment (restricted)', 'textarea', False),)),
                     Act('share', 'Share with the employee', a_r360_share, lambda o, r: o.status == 'closed' and app.any(r, *HR), style='primary'),
                     Act('plan', 'Make a development plan', a_r360_plan, lambda o, r: o.status in ('closed', 'shared') and (app.any(r, *HR) or is_manager_of(o.employee, r)), fields=(('title', 'Title', 'text', False),))],
            help='Self, manager, peers, direct reports and internal customers rate the competencies and answer the open questions; HR closes and shares the summary.'))
app.reg(Reg('reviewer', Reviewer, 'Reviewer', 'Reviewers', '360° reviews', cols=('review', 'reviewer', 'relation', 'overall', 'done_at', 'status'), form=('review', 'reviewer', 'relation'),
            manage=HR, view=VIEW, own=(('reviewer', 'emp'),), search=('reviewer__emp_first_name', 'review__number'), filters=('relation', 'status'), title='reviewer', subtitle='relation',
            child_of='review', deletable=True, files=False, edit_when=lambda o: o.status == 'pending', restricted=('ratings', 'strengths', 'improve')))


def plan_defaults(o, request):
    if not o.employee_id:
        o.employee = me(request)
    if not o.start:
        o.start = today()


def plan_saver(o, request, creating, old):
    items = o.steps or []
    o.progress = round(100 * sum(1 for i in items if i.get('done')) / len(items)) if items else 0
    if o.status == 'active' and items and o.progress == 100:
        o.status = 'done'
    o.save()
    return o


def plan_set(status_, text):
    def run(o, request, f):
        o.status = status_
        o.save(update_fields=['status'])
        app.event('development_plan', o.pk, text, request.user)
        if status_ == 'active':
            app.tell_emp(o.employee, f'Development plan: {o.title}', 'Your development plan is active – see Self service → My development.', 'development_plan', o.pk, o.number, PAGE + f'r/development_plan/{o.pk}')
        return o
    return run


app.reg(Reg('development_plan', DevelopmentPlan, 'Development plan', 'Development plans', '360° reviews',
            cols=('number', 'employee', 'title', 'mentor', 'target_date', 'progress', 'status'), form=('employee', 'title', 'review', 'mentor', 'start', 'target_date', 'steps', 'notes'),
            number=('development_plan', 'PDP'), manage=HR, view=VIEW, create='all', own=(('employee', 'emp'), ('mentor', 'emp')), own_edit=True, public=reports_q,
            search=('number', 'title', 'employee__emp_first_name', 'employee__emp_last_name'), filters=('status', 'employee', 'mentor'),
            title='title', subtitle='employee', defaults=plan_defaults, saver=plan_saver, edit_when=lambda o: o.status in ('draft', 'active'), info=('status', 'progress'),
            actions=[Act('activate', 'Activate', plan_set('active', 'Activated'), lambda o, r: o.status == 'draft' and (app.any(r, *HR) or is_manager_of(o.employee, r)), style='primary'),
                     Act('done', 'Completed', plan_set('done', 'Completed'), lambda o, r: o.status == 'active' and (app.any(r, *HR) or is_manager_of(o.employee, r))),
                     Act('drop', 'Drop', plan_set('dropped', 'Dropped'), lambda o, r: o.status in ('draft', 'active') and app.any(r, *HR), style='danger')],
            help='Actions an employee takes to grow (training, coaching, projects). Ticking items updates the progress; the employee and the mentor can edit.'))


# ================================================================== dashboard, my work, daily
def dashboard(request):
    t = today()
    enr = app.regs['enrolment'].scope(request)
    claims = app.regs['claim'].scope(request)
    signs = app.regs['sign_request'].scope(request)
    subs = app.regs['form_submission'].scope(request)
    surveys = app.regs['survey'].scope(request)
    recs = app.regs['recognition'].scope(request)
    revs = app.regs['review_360'].scope(request)
    plans = app.regs['development_plan'].scope(request)
    open_surveys = surveys.filter(status='open')
    rates = []
    for s in open_surveys[:5]:
        tot = audience(s).count()
        rates.append({'label': s.title[:40], 'value': round(100 * s.responses.count() / tot) if tot else 0})
    month_recs = recs.filter(status='published', created_at__year=t.year, created_at__month=t.month)
    top = (Recognition.objects.filter(status='published', created_at__gte=t - timedelta(days=90)).values('employee__emp_first_name', 'employee__emp_last_name', 'employee_id')
           .annotate(p=Sum('points')).order_by('-p')[:8])
    return {
        'cards': [
            {'label': 'Active enrolments', 'value': enr.filter(status='active').count(), 'link': 'r/enrolment?status=active'},
            {'label': 'Enrolments to approve', 'value': enr.filter(status='requested').count(), 'tone': 'bad' if enr.filter(status='requested').exists() else 'ok', 'link': 'r/enrolment?status=requested'},
            {'label': 'Claims to approve', 'value': claims.filter(status='submitted').count(), 'link': 'r/claim?status=submitted'},
            {'label': 'Signatures waiting', 'value': signs.filter(status='sent').count(), 'link': 'r/sign_request?status=sent'},
            {'label': 'Forms to approve', 'value': subs.filter(status='submitted').count(), 'link': 'r/form_submission?status=submitted'},
            {'label': 'Open surveys', 'value': open_surveys.count(), 'link': 'r/survey?status=open'},
            {'label': 'Recognitions this month', 'value': month_recs.count(), 'hint': f'{month_recs.aggregate(s=Sum("points"))["s"] or 0} {app.settings().points_currency}', 'link': 'wall'},
            {'label': '360° reviews collecting', 'value': revs.filter(status='open').count(), 'link': 'r/review_360?status=open'},
            {'label': 'Development plans active', 'value': plans.filter(status='active').count(), 'link': 'r/development_plan?status=active'},
        ],
        'bars': ([{'title': 'Survey response %', 'items': rates}] if rates else []) +
                [{'title': 'Enrolments by plan', 'items': [{'label': x['plan__name'], 'value': x['n']} for x in enr.filter(status='active').values('plan__name').annotate(n=Count('id')).order_by('-n')[:8]]}],
        'lists': [
            {'title': 'Top recognised (90 days)', 'kind': 'recognition', 'rows': [{'id': 0, 'title': f'{x["employee__emp_first_name"] or ""} {x["employee__emp_last_name"] or ""}'.strip(), 'sub': '', 'badge': f'{x["p"]} pts'} for x in top]},
            {'title': 'Signatures waiting', 'kind': 'sign_request', 'rows': [{'id': s.pk, 'title': f'{s.number} {s.title}', 'sub': emp_name(s.employee) or s.signer_name, 'badge': d(s.expires_at)[:10] if s.expires_at else ''} for s in signs.filter(status='sent').select_related('employee')[:10]]},
        ],
    }


def my_work(request):
    e = me(request)
    if e is None:
        return []
    out = []
    signs = SignRequest.objects.filter(employee=e, status='sent')
    out.append({'title': 'To sign', 'kind': 'sign_request', 'rows': [{'id': s.pk, 'title': s.title, 'sub': s.get_kind_display(), 'badge': 'sign'} for s in signs[:20]]})
    revs = Reviewer.objects.filter(reviewer=e, status='pending', review__status='open').select_related('review__employee')
    out.append({'title': 'Reviews to give', 'kind': 'review_360', 'rows': [{'id': r.review_id, 'title': f'360° of {emp_name(r.review.employee)}', 'sub': r.get_relation_display(), 'badge': d(r.review.due) or ''} for r in revs[:20]]})
    done = set(SurveyResponse.objects.filter(employee_key__in=[emp_key(s, e) for s in Survey.objects.filter(status='open')]).values_list('survey_id', flat=True))
    out.append({'title': 'Surveys to answer', 'kind': 'survey', 'rows': [{'id': s.pk, 'title': s.title, 'sub': s.get_kind_display(), 'badge': 'anonymous' if s.anonymous else ''} for s in Survey.objects.filter(status='open').exclude(pk__in=done) if e in audience(s)][:10]})
    out.append({'title': 'My benefits', 'kind': 'enrolment', 'rows': [{'id': x.pk, 'title': x.plan.name, 'sub': f'from {x.start}', 'badge': x.get_status_display()} for x in Enrolment.objects.filter(employee=e).exclude(status='rejected').select_related('plan')[:10]]})
    out.append({'title': 'My development plans', 'kind': 'development_plan', 'rows': [{'id': p.pk, 'title': p.title, 'sub': f'{p.progress}%', 'badge': p.get_status_display()} for p in DevelopmentPlan.objects.filter(employee=e).exclude(status='dropped')[:10]]})
    return out


def daily():
    out = {'sign_expired': 0, 'survey_closed': 0, 'survey_reminded': 0, 'review_due': 0, 'chat_purged': 0}
    for s in SignRequest.objects.filter(status='sent', expires_at__lt=now()):
        s.status = 'expired'
        s.save(update_fields=['status'])
        app.event('sign_request', s.pk, 'Expired', None)
        out['sign_expired'] += 1
    for s in Survey.objects.filter(status='open', end__lt=today()):
        s.status = 'closed'
        s.save(update_fields=['status'])
        app.event('survey', s.pk, 'Closed (end date passed)', None)
        out['survey_closed'] += 1
    for s in Survey.objects.filter(status='open'):
        if s.reminder_days and s.start and (today() - s.start).days % s.reminder_days == 0 and today() != s.start:
            done = set(SurveyResponse.objects.filter(survey=s).values_list('employee_key', flat=True))
            for e in audience(s):
                if e.users_id and emp_key(s, e) not in done:
                    app.todo(e.users, f'Survey: {s.title}', 'survey', s.pk, s.number, PAGE + f'survey/{s.pk}', 'Reminder – please answer.')
                    out['survey_reminded'] += 1
    for r in Review360.objects.filter(status='open', due__lt=today()):
        for u in app.users_with_role('ppl_hr'):
            app.todo(u, f'360° review overdue: {emp_name(r.employee)}', 'review_360', r.pk, r.number, PAGE + f'r/review_360/{r.pk}')
        out['review_due'] += 1
    days = app.settings().chat_retention_days
    if days:
        out['chat_purged'] = ChatMessage.objects.filter(at__lt=now() - timedelta(days=days)).delete()[0]
    return out


app.dashboard, app.my_work, app.daily = dashboard, my_work, daily


# ================================================================== hooks for other apps
def h_sign_document(title, employee_id=None, kind='letter', file=None, text='', source_model='', source_id=None, user=None, signer_name='', signer_email='', message=''):
    """Another module asks for a signature (HR letters, offers, policies). Returns {id, number, page}."""
    from EmpManagement.models import emp_master
    emp = emp_master.objects.filter(pk=employee_id).first() if employee_id else None
    s = SignRequest(number=app.next_number('sign_request', 'SGN'), title=title[:150], kind=kind if kind in dict(SignRequest._meta.get_field('kind').choices) else 'other',
                    employee=emp, signer_name=signer_name[:120], signer_email=signer_email, text=text, source_model=source_model, source_id=source_id,
                    token=secrets.token_urlsafe(32), requested_by=user if getattr(user, 'is_authenticated', False) else None, message=message,
                    branch_id=getattr(emp, 'emp_branch_id_id', None))
    if file is not None:
        s.document = file
    s.save()
    app.event('sign_request', s.pk, 'Requested by another module', user)

    class _R:
        pass
    r = _R()
    r.user = user
    send_sign(s, r)
    return {'id': s.pk, 'number': s.number, 'page': PAGE + f'sign/{s.pk}', 'public_link': f'/sign/{s.token}'}


def h_sign_status(sign_request_id):
    s = SignRequest.objects.filter(pk=sign_request_id).first()
    return s and {'status': s.status, 'signed_at': s.signed_at, 'signed_file': s.signed_document.url if s.signed_document else ''}


def h_points(employee_id):
    return points_of(employee_id)


app.hooks.update({'sign_document': h_sign_document, 'sign_status': h_sign_status, 'points': h_points})
