"""ZEO People (v1.36.0) – people engagement on the ZEO app kit, inside the HRMS suite.

Benefits (plans, enrolment, claims → payroll), e-signature (letters, offers, policies, forms – OTP + drawn / typed signature,
stamped PDF, audit trail), form builder with submissions and approval, surveys (anonymous option, results), recognition
(awards, points, wall), employee chat, and 360° reviews with competencies and development plans next to Performance.
"""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
BR = dict(null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
MONEY = dict(max_digits=12, decimal_places=2)
BRANCH = dict(null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='Branch of the record; empty = every branch')

ROLES = (('ppl_manager', 'People manager'), ('ppl_benefits', 'Benefits administrator'), ('ppl_hr', 'HR officer (forms, e-signature, surveys)'),
         ('ppl_viewer', 'People viewer (read only)'))


class PeopleSettings(AppSettingsBase):
    points_currency = models.CharField('points name', max_length=20, default='points')
    points_value = models.DecimalField('value of one point (for rewards)', max_digits=8, decimal_places=2, default=0)
    recognition_needs_approval = models.BooleanField('recognition needs the People manager\'s approval before it is published', default=False)
    esign_otp = models.BooleanField('e-signature asks for a one-time code sent by e-mail', default=True)
    esign_expiry_days = models.PositiveSmallIntegerField('signature requests expire after (days)', default=14)
    chat_retention_days = models.PositiveSmallIntegerField('keep chat messages for (days; 0 = forever)', default=0)
    claim_component = models.CharField('salary component code for approved claims', max_length=20, blank=True, default='BENCLAIM',
                                       help_text='An addition component with this code is made when the first claim is paid in payroll')
    review_relations = models.JSONField('360° reviewer weights', default=dict, blank=True,
                                        help_text='{"self": 10, "manager": 40, "peer": 25, "subordinate": 15, "customer": 10}')

    class Meta:
        permissions = [(k, f'People role: {v}') for k, v in ROLES]
        verbose_name = 'People settings'


class PeopleSequence(SequenceBase):
    pass


class PeopleEvent(EventBase):
    class Meta(EventBase.Meta):
        pass


class PeopleFile(FileBase):
    pass


# ====================================================================== benefits
PLAN_KINDS = (('medical', 'Medical insurance'), ('life', 'Life insurance'), ('pension', 'Pension / savings'), ('allowance', 'Allowance (phone, fuel, education…)'),
              ('ticket', 'Air ticket'), ('wellness', 'Wellness / gym'), ('other', 'Other'))


class BenefitPlan(PackItem):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=PLAN_KINDS, default='medical')
    provider = models.CharField('provider / insurer', max_length=120, blank=True)
    policy_no = models.CharField('policy no.', max_length=60, blank=True)
    description = models.TextField(blank=True)
    employee_cost = models.DecimalField('employee share per month', default=0, **MONEY)
    employer_cost = models.DecimalField('employer cost per month', default=0, **MONEY)
    eligible_after_months = models.PositiveSmallIntegerField('eligible after (months of service)', default=0)
    eligible_categories = models.CharField('eligible categories / grades', max_length=200, blank=True, help_text='Comma separated; empty = everyone')
    covers_dependants = models.BooleanField(default=False)
    max_dependants = models.PositiveSmallIntegerField(default=0)
    claim_limit_year = models.DecimalField('claim limit per year', null=True, blank=True, **MONEY)
    deduction_component = models.CharField('salary component for the employee share', max_length=20, blank=True,
                                           help_text='Deduction component code; made on first enrolment when empty (BEN-<plan code>)')
    needs_approval = models.BooleanField('enrolment needs the benefits administrator\'s approval', default=True)
    renewal_date = models.DateField(null=True, blank=True)
    documents_note = models.CharField('documents the employee must give', max_length=200, blank=True)

    class Meta:
        ordering = ['kind', 'name']

    def __str__(self):
        return self.name


ENROL_STATUS = (('requested', 'Requested'), ('active', 'Active'), ('rejected', 'Rejected'), ('ended', 'Ended'))


class Enrolment(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    plan = models.ForeignKey(BenefitPlan, on_delete=models.PROTECT, related_name='enrolments')
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='benefit_enrolments')
    start = models.DateField()
    end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=ENROL_STATUS, default='requested')
    dependants = models.JSONField(default=list, blank=True, help_text='One per line: name – relation – date of birth')
    member_no = models.CharField('member / card no.', max_length=60, blank=True)
    employee_cost = models.DecimalField('employee share per month', default=0, **MONEY)
    note = models.TextField(blank=True)
    decided_by = models.ForeignKey(USER, **BR)
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.plan.name}'


CLAIM_STATUS = (('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('paid', 'Paid'))


class BenefitClaim(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    enrolment = models.ForeignKey(Enrolment, on_delete=models.PROTECT, related_name='claims')
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='benefit_claims')
    date = models.DateField('date of expense')
    description = models.CharField(max_length=200)
    amount = models.DecimalField('claimed', **MONEY)
    approved_amount = models.DecimalField(null=True, blank=True, **MONEY)
    status = models.CharField(max_length=10, choices=CLAIM_STATUS, default='submitted')
    decision_note = models.CharField(max_length=200, blank=True)
    paid_month = models.DateField('paid in payroll month', null=True, blank=True)
    decided_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== e-signature
SIGN_KINDS = (('letter', 'HR letter'), ('offer', 'Offer letter'), ('contract', 'Contract / amendment'), ('policy', 'Policy acknowledgement'),
              ('form', 'Form'), ('other', 'Other'))
SIGN_STATUS = (('draft', 'Draft'), ('sent', 'Sent – waiting'), ('signed', 'Signed'), ('declined', 'Declined'), ('expired', 'Expired'), ('cancelled', 'Cancelled'))


class SignRequest(models.Model):
    """One document for one signer: an employee (signs in Self service) or an outside person by e-mail link (candidate)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=SIGN_KINDS, default='letter')
    document = models.FileField('document (PDF)', upload_to='zeo_people/sign/%Y/%m/', null=True, blank=True)
    text = models.TextField('text to sign (when there is no PDF)', blank=True)
    employee = models.ForeignKey(EMP, **BR, verbose_name='signer (employee)')
    signer_name = models.CharField('signer name (outside person)', max_length=120, blank=True)
    signer_email = models.EmailField('signer e-mail (outside person)', blank=True)
    message = models.TextField('message to the signer', blank=True)
    status = models.CharField(max_length=10, choices=SIGN_STATUS, default='draft')
    token = models.CharField(max_length=48, blank=True, db_index=True)
    otp = models.CharField(max_length=8, blank=True)
    otp_sent_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signature = models.TextField('signature image (data URL)', blank=True)
    signed_document = models.FileField(upload_to='zeo_people/signed/%Y/%m/', null=True, blank=True)
    signed_ip = models.CharField(max_length=60, blank=True)
    signed_agent = models.CharField(max_length=200, blank=True)
    decline_reason = models.CharField(max_length=200, blank=True)
    audit = models.JSONField(default=list, blank=True)
    source_model = models.CharField(max_length=60, blank=True)   # e.g. SelfService.LetterRequest / RecruitmentManagement.Offer
    source_id = models.IntegerField(null=True, blank=True)
    requested_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.title}'


# ====================================================================== forms
class FormTemplate(PackItem):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, help_text='Shown above the form')
    fields = models.JSONField(default=list, blank=True, help_text='[{"name": "reason", "label": "Reason", "type": "text|textarea|number|date|choice|yesno|file", "required": true, "options": "A|B"}]')
    audience = models.CharField(max_length=10, default='all', choices=(('all', 'Every employee'), ('managers', 'Managers only'), ('hr', 'HR only')))
    needs_approval = models.BooleanField('submissions need approval', default=False)
    approver = models.CharField(max_length=10, default='manager', choices=(('manager', 'Reporting manager'), ('hr', 'HR officer / People manager')))
    sign = models.BooleanField('the employee signs the submission (e-signature)', default=False)
    notify_email = models.EmailField('e-mail each submission to', blank=True)
    once = models.BooleanField('one submission per employee', default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


SUB_STATUS = (('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'))


class FormSubmission(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    template = models.ForeignKey(FormTemplate, on_delete=models.PROTECT, related_name='submissions', verbose_name='form')
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='form_submissions')
    data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=SUB_STATUS, default='submitted')
    decision_note = models.CharField(max_length=200, blank=True)
    decided_by = models.ForeignKey(USER, **BR)
    decided_at = models.DateTimeField(null=True, blank=True)
    sign_request = models.ForeignKey(SignRequest, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== surveys
SURVEY_STATUS = (('draft', 'Draft'), ('open', 'Open'), ('closed', 'Closed'))


class Survey(PackItem):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=12, default='engagement', choices=(('engagement', 'Engagement'), ('pulse', 'Pulse (short)'), ('onboarding', 'Onboarding'),
                                                                           ('exit', 'Exit'), ('training', 'Training feedback'), ('other', 'Other')))
    anonymous = models.BooleanField('answers are anonymous', default=True)
    questions = models.JSONField(default=list, blank=True, help_text='[{"text": "…", "type": "rating|yesno|choice|text", "options": "A|B|C", "required": true}]')
    audience = models.CharField(max_length=10, default='all', choices=(('all', 'Every employee'), ('branch', 'My branches'), ('managers', 'Managers only')))
    departments = models.CharField('departments (codes, comma separated; empty = all)', max_length=200, blank=True)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=SURVEY_STATUS, default='draft')
    reminder_days = models.PositiveSmallIntegerField('remind non-responders every (days)', default=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class SurveyResponse(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='responses')
    employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')   # None when anonymous
    employee_key = models.CharField(max_length=64, blank=True, db_index=True)                               # hash: one answer per employee, no name
    department = models.CharField(max_length=80, blank=True)                                               # kept for grouping even when anonymous
    answers = models.JSONField(default=list, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-submitted_at']


# ====================================================================== recognition
class Award(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    icon = models.CharField(max_length=30, default='emoji_events', help_text='Material icon name')
    points = models.PositiveSmallIntegerField(default=10)
    description = models.CharField(max_length=200, blank=True)
    who_can_give = models.CharField(max_length=10, default='all', choices=(('all', 'Anyone'), ('managers', 'Managers'), ('hr', 'People manager / HR')))
    monthly_limit = models.PositiveSmallIntegerField('a person may give it per month (0 = no limit)', default=0)

    class Meta:
        ordering = ['-points', 'name']

    def __str__(self):
        return self.name


REC_STATUS = (('pending', 'Waiting for approval'), ('published', 'Published'), ('rejected', 'Rejected'))


class Recognition(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='recognitions', verbose_name='recognised')
    given_by = models.ForeignKey(EMP, **BR, verbose_name='given by')
    award = models.ForeignKey(Award, on_delete=models.PROTECT)
    message = models.TextField()
    points = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, choices=REC_STATUS, default='published')
    likes = models.JSONField(default=list, blank=True)   # employee ids
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Reward(models.Model):
    """Points redeemed (gift, voucher, day off…) – approved by the People manager."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='rewards')
    points = models.PositiveIntegerField()
    item = models.CharField('reward', max_length=150)
    status = models.CharField(max_length=10, default='requested', choices=(('requested', 'Requested'), ('approved', 'Approved'), ('given', 'Given'), ('rejected', 'Rejected')))
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== chat
class Conversation(models.Model):
    kind = models.CharField(max_length=8, default='direct', choices=(('direct', 'Direct'), ('group', 'Group'), ('channel', 'Channel')))
    name = models.CharField(max_length=100, blank=True)
    members = models.ManyToManyField(USER, related_name='zeo_conversations')
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)
    last_at = models.DateTimeField(null=True, blank=True)
    key = models.CharField(max_length=64, blank=True, db_index=True)   # direct: "u<min>-u<max>" so a pair has one conversation

    class Meta:
        ordering = ['-last_at', '-id']


class ChatMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(USER, **BR)
    text = models.TextField(blank=True)
    file = models.FileField(upload_to='zeo_people/chat/%Y/%m/', null=True, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    at = models.DateTimeField(auto_now_add=True)
    read_by = models.JSONField(default=list, blank=True)   # user ids

    class Meta:
        ordering = ['at', 'id']


# ====================================================================== 360° reviews, competencies, development plans
class Competency(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    group = models.CharField(max_length=10, default='core', choices=(('core', 'Core'), ('leadership', 'Leadership'), ('functional', 'Functional'), ('values', 'Company values')))
    description = models.TextField(blank=True)
    levels = models.JSONField(default=list, blank=True, help_text='Descriptions of levels 1–5, one per line')
    for_managers = models.BooleanField('managers only', default=False)

    class Meta:
        ordering = ['group', 'name']

    def __str__(self):
        return self.name


RELATIONS = (('self', 'Self'), ('manager', 'Manager'), ('peer', 'Peer'), ('subordinate', 'Direct report'), ('customer', 'Internal customer / other'))
R360_STATUS = (('draft', 'Draft'), ('open', 'Open – collecting'), ('closed', 'Closed'), ('shared', 'Shared with the employee'))


class Review360(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='reviews_360')
    cycle_name = models.CharField('cycle', max_length=100, blank=True, help_text='e.g. Annual 2026 (matches the Performance cycle name)')
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    competencies = models.ManyToManyField(Competency, blank=True)
    due = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=8, choices=R360_STATUS, default='draft')
    anonymous = models.BooleanField('reviewers stay anonymous to the employee', default=True)
    open_questions = models.JSONField(default=list, blank=True, help_text='Open questions, one per line (e.g. What should this person keep doing?)')
    summary = models.JSONField(default=dict, blank=True)   # computed: per competency per relation, overall
    overall = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    hr_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.employee}'


class Reviewer(models.Model):
    review = models.ForeignKey(Review360, on_delete=models.CASCADE, related_name='reviewers')
    reviewer = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='reviews_to_give')
    relation = models.CharField(max_length=12, choices=RELATIONS, default='peer')
    status = models.CharField(max_length=8, default='pending', choices=(('pending', 'Pending'), ('done', 'Done'), ('declined', 'Declined')))
    ratings = models.JSONField(default=dict, blank=True)   # competency id → 1..5
    answers = models.JSONField(default=list, blank=True)
    strengths = models.TextField(blank=True)
    improve = models.TextField(blank=True)
    overall = models.PositiveSmallIntegerField(null=True, blank=True)
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['relation', 'id']
        unique_together = [('review', 'reviewer')]


class DevelopmentPlan(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='development_plans')
    review = models.ForeignKey(Review360, **BR, verbose_name='from 360° review')
    title = models.CharField(max_length=150)
    mentor = models.ForeignKey(EMP, **BR, verbose_name='mentor / coach')
    start = models.DateField(null=True, blank=True)
    target_date = models.DateField(null=True, blank=True)
    steps = models.JSONField('actions', default=list, blank=True, help_text='[{"area": "…", "action": "…", "kind": "training|coaching|project|reading|other", "due": "2026-12-31", "done": false}]')
    status = models.CharField(max_length=8, default='draft', choices=(('draft', 'Draft'), ('active', 'Active'), ('done', 'Completed'), ('dropped', 'Dropped')))
    progress = models.PositiveSmallIntegerField('progress %', default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
