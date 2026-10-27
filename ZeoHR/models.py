"""ZEO HR operations (v1.48.0) – the HR desk work that needs a workflow, on the ZEO app kit, inside the HRMS suite:

* DisciplinaryCase  – disciplinary and grievance cases: report, investigation, show-cause, hearing, decision (warning, fine,
                      suspension, termination …), appeal; a decision with a warning adds the warning to the employee timeline
* ExitInterview     – an exit interview per leaver: questions answered by the employee, reason for leaving, rehire, notes
* DocumentRenewal   – renewal of an expiring employee document (passport, visa, Emirates ID / iqama, labour card, insurance …)
* ProService        – PRO / government services: visas, IDs, labour cards, medicals, company licences – steps, fees, results
* AssetHandover     – issue / return of company assets to an employee, signed electronically
Always on (no activation): HR officers of the company use it with their existing rights; the roles below add finer rights.
"""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, SequenceBase

from .catalogue import service_choices

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
BR = dict(null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
MONEY = dict(max_digits=12, decimal_places=2)
BRANCH = dict(null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='Branch of the record; empty = every branch')

ROLES = (('hro_manager', 'HR operations manager'), ('hro_hr', 'HR officer (cases, exit interviews, handovers)'),
         ('hro_pro', 'PRO / government relations officer'), ('hro_viewer', 'HR operations viewer (read only)'))


class HRSettings(AppSettingsBase):
    renewal_days = models.PositiveSmallIntegerField('start a renewal this many days before expiry', default=60)
    renewal_auto = models.BooleanField('start renewals automatically (daily)', default=True)
    renewal_pro = models.ForeignKey(USER, verbose_name='renewals go to (PRO)', **BR)
    fine_max_days = models.DecimalField("fine at most (days' wage a month)", max_digits=5, decimal_places=2, default=5,
                                        help_text="UAE (Decree-Law 33/2021) and KSA: at most 5 days' wage a month")
    suspension_max_days = models.PositiveSmallIntegerField('suspension at most (days)', default=14)
    show_cause_days = models.PositiveSmallIntegerField('days to answer a show-cause notice', default=3)
    exit_auto = models.BooleanField('send the exit interview when a resignation is approved', default=True)
    exit_days_before = models.PositiveSmallIntegerField('send it this many days before the last working day', default=7)
    handover_signature = models.BooleanField('the employee signs every asset handover', default=True)
    ticket_ai = models.CharField('ticket category and priority', max_length=8, default='suggest',
                                 choices=(('off', 'Off'), ('suggest', 'Suggest only (HR decides)'), ('apply', 'Suggest and apply when sure')),
                                 help_text='v1.61.0 read new HR tickets and suggest the category and priority')
    ticket_ai_min = models.PositiveSmallIntegerField('apply when at least this sure (%)', default=70)

    class Meta:
        permissions = [(k, f'HR operations role: {v}') for k, v in ROLES]
        verbose_name = 'HR operations settings'

    @classmethod
    def get(cls):
        from django.utils import timezone
        s = cls.objects.order_by('id').first() or cls.objects.create()
        if not s.activated_at:   # always on, like the shared masters
            s.activated_at = timezone.now()
            s.save(update_fields=['activated_at'])
        return s


class HRSequence(SequenceBase):
    pass


class HREvent(EventBase):
    pass


class HRFile(FileBase):
    pass


# ====================================================================== disciplinary and grievance cases
CASE_CATEGORIES = (('misconduct', 'Misconduct'), ('attendance', 'Attendance / absence'), ('performance', 'Performance'), ('policy', 'Policy breach'),
                   ('harassment', 'Harassment / bullying'), ('safety', 'Safety breach'), ('fraud', 'Fraud / dishonesty'), ('grievance', 'Grievance (complaint)'),
                   ('other', 'Other'))
CASE_STATUS = (('reported', 'Reported'), ('investigation', 'Investigation'), ('show_cause', 'Show-cause sent'), ('hearing', 'Hearing'),
               ('decided', 'Decided'), ('appeal', 'Appeal'), ('closed', 'Closed'), ('withdrawn', 'Withdrawn'))
DECISIONS = (('', '–'), ('no_action', 'No action'), ('counselling', 'Counselling'), ('verbal', 'Verbal warning'), ('written', 'Written warning'),
             ('final', 'Final warning'), ('fine', 'Fine (deduction)'), ('suspension', 'Suspension without pay'), ('increment_hold', 'Increment / promotion held'),
             ('demotion', 'Demotion'), ('termination', 'Termination'))


class DisciplinaryCase(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='hr_cases', verbose_name='employee concerned')
    category = models.CharField(max_length=12, choices=CASE_CATEGORIES, default='misconduct')
    title = models.CharField(max_length=150)
    description = models.TextField('what happened')
    incident_date = models.DateField(null=True, blank=True)
    reported_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=120, blank=True)
    reported_by = models.CharField(max_length=120, blank=True)
    witnesses = models.TextField(blank=True)
    grievance = models.ForeignKey('SelfService.Grievance', verbose_name='from complaint', **BR)
    investigator = models.ForeignKey(USER, verbose_name='investigator', **BR)
    status = models.CharField(max_length=14, choices=CASE_STATUS, default='reported')
    show_cause_text = models.TextField('show-cause notice', blank=True)
    show_cause_sent = models.DateField(null=True, blank=True)
    response_due = models.DateField(null=True, blank=True)
    employee_response = models.TextField(blank=True)
    response_received = models.DateField(null=True, blank=True)
    hearing_at = models.DateTimeField(null=True, blank=True)
    hearing_place = models.CharField(max_length=120, blank=True)
    hearing_panel = models.CharField(max_length=250, blank=True)
    hearing_notes = models.TextField(blank=True)
    findings = models.TextField(blank=True)
    decision = models.CharField(max_length=15, choices=DECISIONS, blank=True, default='')
    decision_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    fine_days = models.DecimalField("fine (days' wage)", max_digits=5, decimal_places=2, null=True, blank=True)
    suspension_days = models.PositiveSmallIntegerField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)
    warning_id = models.IntegerField(null=True, blank=True)
    appeal_text = models.TextField(blank=True)
    appeal_date = models.DateField(null=True, blank=True)
    appeal_outcome = models.CharField(max_length=10, blank=True, choices=(('', '–'), ('upheld', 'Decision upheld'), ('reduced', 'Penalty reduced'),
                                                                        ('overturned', 'Decision overturned')))
    appeal_notes = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.title}'


# ====================================================================== exit interviews
LEAVING_REASONS = (('', '–'), ('pay', 'Better pay / benefits'), ('career', 'Career growth'), ('manager', 'Manager / leadership'), ('workload', 'Workload / hours'),
                   ('culture', 'Culture / team'), ('relocation', 'Relocation'), ('family', 'Personal / family'), ('studies', 'Studies'), ('health', 'Health'),
                   ('retirement', 'Retirement'), ('contract', 'Contract ended'), ('dismissed', 'Dismissed'), ('other', 'Other'))
EXIT_STATUS = (('planned', 'Planned'), ('sent', 'Sent to the employee'), ('answered', 'Answered'), ('interviewed', 'Interview held'),
               ('closed', 'Closed'), ('declined', 'Declined'))


class ExitInterview(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='exit_interviews')
    resignation = models.ForeignKey('EmpManagement.EmployeeResignation', **BR)
    last_working_date = models.DateField(null=True, blank=True)
    interviewer = models.ForeignKey(USER, verbose_name='interviewer', **BR)
    interview_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=EXIT_STATUS, default='planned')
    questions = models.JSONField(default=list, blank=True)
    answers = models.JSONField(default=list, blank=True)
    reason = models.CharField('main reason for leaving', max_length=12, choices=LEAVING_REASONS, blank=True, default='')
    rehire = models.CharField('would rehire', max_length=6, blank=True, default='', choices=(('', '–'), ('yes', 'Yes'), ('maybe', 'Maybe'), ('no', 'No')))
    recommend = models.PositiveSmallIntegerField('would recommend us (0–10)', null=True, blank=True)
    notes = models.TextField('interviewer notes', blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== document renewals and PRO services
RENEWAL_STATUS = (('due', 'Due'), ('in_progress', 'In progress'), ('submitted', 'Submitted'), ('renewed', 'Renewed'), ('cancelled', 'Cancelled'))


class DocumentRenewal(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='document_renewals')
    document = models.CharField('document', max_length=60)
    doc_number = models.CharField('number', max_length=60, blank=True)
    expiry = models.DateField('current expiry', null=True, blank=True)
    source = models.CharField(max_length=10, default='document', choices=(('document', 'Employee document'), ('identity', 'Identity'), ('manual', 'Typed')))
    source_id = models.IntegerField(null=True, blank=True)
    source_field = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=12, choices=RENEWAL_STATUS, default='due')
    assigned_to = models.ForeignKey(USER, verbose_name='handled by', **BR)
    pro_service = models.ForeignKey('ProService', verbose_name='PRO service', null=True, blank=True, on_delete=models.SET_NULL, related_name='renewals')
    new_number = models.CharField(max_length=60, blank=True)
    new_expiry = models.DateField(null=True, blank=True)
    cost = models.DecimalField(**MONEY, null=True, blank=True)
    notes = models.TextField(blank=True)
    renewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiry', 'id']


PRO_STATUS = (('requested', 'Requested'), ('documents', 'Collecting documents'), ('submitted', 'Submitted'), ('in_process', 'In process'),
              ('approved', 'Approved'), ('completed', 'Completed'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled'))


class ProService(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    service = models.CharField('service', max_length=40, choices=service_choices())
    title = models.CharField(max_length=150, blank=True)
    country = models.CharField(max_length=2, blank=True)
    authority = models.CharField('government body', max_length=80, blank=True)
    for_company = models.BooleanField('for the company (not an employee)', default=False)
    employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.CASCADE, related_name='pro_services')
    dependant = models.CharField('dependant (name)', max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=PRO_STATUS, default='requested')
    priority = models.CharField(max_length=8, default='normal', choices=(('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent')))
    application_no = models.CharField('application / transaction no.', max_length=60, blank=True)
    requested_date = models.DateField(null=True, blank=True)
    submitted_date = models.DateField(null=True, blank=True)
    expected_date = models.DateField(null=True, blank=True)
    completed_date = models.DateField(null=True, blank=True)
    gov_fee = models.DecimalField('government fee', **MONEY, default=0)
    service_fee = models.DecimalField('typing / service fee', **MONEY, default=0)
    other_fee = models.DecimalField('other costs', **MONEY, default=0)
    total = models.DecimalField(**MONEY, default=0)
    paid_by = models.CharField(max_length=10, default='company', choices=(('company', 'Company'), ('employee', 'Employee')))
    steps = models.JSONField(default=list, blank=True)
    assigned_to = models.ForeignKey(USER, verbose_name='PRO', **BR)
    result_number = models.CharField('issued number', max_length=60, blank=True)
    result_expiry = models.DateField('issued valid until', null=True, blank=True)
    rejection_reason = models.CharField(max_length=250, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== asset handover
HANDOVER_STATUS = (('draft', 'Draft'), ('sent', 'Waiting for signature'), ('signed', 'Signed'), ('cancelled', 'Cancelled'))


class AssetHandover(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=6, default='issue', choices=(('issue', 'Issue to the employee'), ('return', 'Return by the employee')))
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='asset_handovers')
    date = models.DateField(null=True, blank=True)
    items = models.JSONField(default=list, blank=True)
    terms = models.TextField('terms the employee accepts', blank=True)
    notes = models.TextField(blank=True)
    damage_charge = models.DecimalField('charge for damage / loss', **MONEY, default=0)
    status = models.CharField(max_length=10, choices=HANDOVER_STATUS, default='draft')
    signature = models.TextField(blank=True)
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_ip = models.CharField(max_length=60, blank=True)
    signed_by_name = models.CharField(max_length=120, blank=True)
    witnessed_by = models.ForeignKey(USER, verbose_name='signed in front of', **BR)
    signed_document = models.FileField(upload_to='zeo_hr/handover/%Y/%m/', null=True, blank=True)
    audit = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== v1.51.0 HR helpdesk
TICKET_STATUS = (('new', 'New'), ('assigned', 'Assigned'), ('waiting', 'Waiting for the employee'), ('resolved', 'Resolved'), ('closed', 'Closed'),
                 ('cancelled', 'Cancelled'))
PRIORITIES = (('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent'))
CHANNELS = (('self_service', 'Self service'), ('kiosk', 'Kiosk'), ('hr', 'Entered by HR'), ('email', 'E-mail'), ('phone', 'Phone / walk-in'))


class HelpdeskCategory(models.Model):
    """What employees ask HR about, with the time HR answers and solves it in and who gets it."""
    name = models.CharField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    response_hours = models.PositiveSmallIntegerField('first answer within (hours)', default=8)
    resolve_hours = models.PositiveSmallIntegerField('solved within (hours)', default=48)
    assignee = models.ForeignKey(USER, verbose_name='goes to', **BR)
    redirect = models.CharField('use instead', max_length=12, blank=True, default='', choices=(('', '–'), ('complaint', 'Confidential complaint'),
                                                                                             ('letter', 'Letter request')),
                                help_text='the employee is sent to the complaint or letter form instead of a ticket')
    order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    keywords = models.TextField(blank=True, default='', help_text='v1.61.0 words and phrases that point to this category (comma separated, '
                                                                   'any language) – used to suggest the category of new tickets')
    catch_all = models.BooleanField('general category', default=False,
                                    help_text='v1.61.0 tickets in this category are moved to the suggested one (when the suggestion is sure enough)')

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class HRTicket(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRANCH)
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='hr_tickets')
    category = models.ForeignKey(HelpdeskCategory, on_delete=models.PROTECT, related_name='tickets')
    subject = models.CharField(max_length=150)
    description = models.TextField('question')
    priority = models.CharField(max_length=8, choices=PRIORITIES, default='normal')
    channel = models.CharField(max_length=14, choices=CHANNELS, default='self_service')
    status = models.CharField(max_length=10, choices=TICKET_STATUS, default='new')
    assigned_to = models.ForeignKey(USER, verbose_name='assigned to', **BR)
    response_due = models.DateTimeField('first answer due', null=True, blank=True)
    resolve_due = models.DateTimeField('solution due', null=True, blank=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField('answer / solution', blank=True)
    messages = models.JSONField(default=list, blank=True)
    rating = models.PositiveSmallIntegerField('rating (1–5)', null=True, blank=True)
    rating_comment = models.CharField(max_length=255, blank=True)
    # v1.61.0 the suggested category and priority (built-in rules or the external AI)
    ai_category = models.ForeignKey(HelpdeskCategory, verbose_name='suggested category', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    ai_priority = models.CharField('suggested priority', max_length=8, blank=True, default='', choices=(('', '–'),) + PRIORITIES)
    ai_confidence = models.PositiveSmallIntegerField('how sure (%)', null=True, blank=True)
    ai_source = models.CharField('suggested by', max_length=10, blank=True, default='', choices=(('', '–'), ('builtin', 'Built-in rules'), ('external', 'External AI')))
    ai_reason = models.CharField('why', max_length=300, blank=True, default='')
    ai_applied = models.BooleanField('suggestion used', default=False)
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.number} {self.subject}'


# ====================================================================== v1.58.0 concurrent assignments and matrix reporting
ASSIGNMENT_KINDS = (('secondment', 'Secondment'), ('second_role', 'Second role'), ('project', 'Project assignment'), ('acting', 'Acting role'))
ASSIGNMENT_STATUS = (('planned', 'Planned'), ('active', 'Active'), ('ended', 'Ended'), ('cancelled', 'Cancelled'))


class Assignment(models.Model):
    """A second place where an employee works at the same time as the main job: a secondment to another branch or department,
    a second role, a project or an acting role – with a share of the time, a manager and dates. The main job stays on the
    employee record; the share not on assignments is the main job."""
    number = models.CharField(max_length=20, unique=True, blank=True)
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='hr_assignments')
    kind = models.CharField(max_length=12, choices=ASSIGNMENT_KINDS, default='secondment')
    host_branch = models.ForeignKey('OrganisationManager.brnch_mstr', verbose_name='branch', **BR)
    department = models.ForeignKey('OrganisationManager.dept_master', **BR)
    designation = models.ForeignKey('OrganisationManager.desgntn_master', verbose_name='role / designation', **BR)
    project = models.ForeignKey('ProjectManagement.Project', **BR)
    manager = models.ForeignKey(USER, verbose_name='manager on this assignment', **BR)
    percent = models.DecimalField('share of time %', max_digits=5, decimal_places=2, default=50)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    cost_split = models.BooleanField('split the salary cost', default=True,
                                     help_text='The share of the gross pay goes to this department / project in the payroll cost allocation')
    manager_approves = models.BooleanField('this manager approves as functional manager', default=True)
    status = models.CharField(max_length=10, choices=ASSIGNMENT_STATUS, default='planned')
    reason = models.CharField(max_length=250, blank=True)
    notes = models.TextField(blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-start_date', '-id']

    def __str__(self):
        return f'{self.number} {self.employee}'


class MatrixLink(models.Model):
    """The functional (dotted-line) manager of an employee, besides the reporting manager."""
    employee = models.OneToOneField(EMP, on_delete=models.CASCADE, related_name='hr_matrix')
    functional_manager = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+', verbose_name='functional (dotted-line) manager')
    review_weight = models.DecimalField('weight in the appraisal %', max_digits=5, decimal_places=2, default=30,
                                        help_text="Share of the functional manager's rating in the blended score shown to the reporting manager (0 = input only)")
    area = models.CharField('for', max_length=150, blank=True, help_text='e.g. Finance, IT, a client account')
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(USER, **BR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['employee__emp_code']

    def __str__(self):
        return f'{self.employee} → {self.functional_manager}'


class FunctionalReview(models.Model):
    """The functional manager's input on an appraisal (goal sheet); the reporting manager stays the final reviewer."""
    STATUS = (('pending', 'To give'), ('submitted', 'Given'), ('skipped', 'Skipped'))
    sheet = models.ForeignKey('PerformanceManagement.GoalSheet', on_delete=models.CASCADE, related_name='functional_reviews')
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='+')
    reviewer = models.ForeignKey(USER, on_delete=models.CASCADE, related_name='+', verbose_name='functional manager')
    weight = models.DecimalField(max_digits=5, decimal_places=2, default=30)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, choices=[(i, str(i)) for i in range(1, 6)])
    strengths = models.TextField(blank=True)
    improvements = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('sheet', 'reviewer')
        ordering = ['status', '-created_at']
