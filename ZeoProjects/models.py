"""
ZEO Projects (v1.14.0) – IT service management on top of ZEO: customers, support tickets with SLA,
AMC contracts, development requests, project delivery extras (milestones, change requests, risks) and a
customer portal.

Existing ZEO tables are not changed. Projects, tasks and timesheets stay in ProjectManagement / ProjectControl;
everything new lives here. Rows of other apps are referenced by integer id where the other app has no committed
migrations of its own (project_id, task_id), and by foreign key for employees, users and branches (the same way
ProjectManagement does).

Tables
* ProjectsSettings     – one row: numbering, business hours, reminders, portal and mailbox settings
* Customer / Contact   – customer companies and their people (a contact may get a portal login)
* ServiceProduct       – what is supported: Odoo, Sage, Microsoft 365, hosting, network …
* SupportTeam          – ERP, Development, Infrastructure, HR/Payroll … with members and a lead
* IssueCategory        – bug, configuration, training, development, infrastructure …
* SLAPolicy / SLATarget – response and resolution targets per priority
* AMCContract          – maintenance agreement: coverage, hours, SLA, billing, renewal (versions via previous)
* AMCBilling           – planned / invoiced / paid instalments of a contract
* Ticket               – support ticket with stage, SLA clock, AMC coverage and billing status
* TicketMessage / TicketFile – conversation (public or internal) and files
* TicketEvent          – readable history of a ticket
* WorkLog              – time spent on a ticket (remote / onsite, billable, travel and other charges, approval)
* DevRequest           – development request raised from a ticket: technical → commercial → customer approval → task
* ProjectProfile       – customer, type, references, scope and deliverables of a ZEO project
* Milestone            – deliverable, planned date, customer acceptance (UAT sign-off) and invoicing
* ChangeRequest        – scope additions with internal and customer approval
* ProjectRisk          – risks and issues
* ProjectExpense       – third-party and other project costs (for profitability)
* TaskDetail           – task extras: acceptance criteria, reviewer, priority, blockers, dependencies, progress
* PlanTemplate / PlanTemplateLine – reusable project plans per product or project type (v1.15.0)
* TaskFile             – attachments on project tasks and sub-tasks (v1.15.0)
* NotificationSetting  – which e-mails and to-dos are sent: company default, per customer, per user (v1.15.0)
"""
from decimal import Decimal

from django.conf import settings
from django.db import models

ZERO = Decimal('0.00')
USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'
BRANCH = 'OrganisationManager.brnch_mstr'

PRIORITIES = (('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'))
PRIORITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}

TICKET_STAGES = (
    ('new', 'New'),
    ('assigned', 'Assigned'),
    ('in_progress', 'In Progress'),
    ('waiting_customer', 'Waiting for Customer'),
    ('waiting_internal', 'Waiting for Internal Team'),
    ('pending_dev_approval', 'Pending Development Approval'),
    ('under_development', 'Under Development'),
    ('testing', 'Testing'),
    ('resolved', 'Resolved'),
    ('closed', 'Closed'),
    ('reopened', 'Reopened'),
)
OPEN_STAGES = ('new', 'assigned', 'in_progress', 'waiting_customer', 'waiting_internal', 'pending_dev_approval',
               'under_development', 'testing', 'reopened')
DONE_STAGES = ('resolved', 'closed')
# stages that pause the resolution clock (the ball is with the customer)
PAUSE_STAGES = ('waiting_customer',)

CHANNELS = (('portal', 'Customer portal'), ('internal', 'Employee helpdesk'), ('email', 'E-mail'),
            ('manual', 'Entered by support'), ('web', 'Website form'), ('api', 'API'), ('phone', 'Phone'),
            ('whatsapp', 'WhatsApp'), ('walk_in', 'Walk-in / visit'), ('teams', 'Teams / video call'), ('chat', 'Live chat'))
# v1.15.0 work approval: tickets not covered by an AMC, and time beyond the hours limit
APPROVAL_STATUS = (('none', 'Not needed'), ('pending', 'Waiting for approval'), ('approved', 'Approved'), ('rejected', 'Rejected'))
APPROVAL_REASONS = (('not_covered', 'Not covered by an AMC'), ('over_hours', 'Extra hours'))
BILLABLE = (('amc', 'AMC covered'), ('chargeable', 'Chargeable'), ('warranty', 'Warranty'), ('internal', 'Internal'))
CONTRACT_TYPES = (('comprehensive', 'Comprehensive'), ('non_comprehensive', 'Non-comprehensive'),
                  ('remote', 'Remote only'), ('onsite', 'Onsite'))
BILLING_FREQ = (('annual', 'Annual'), ('half_yearly', 'Half-yearly'), ('quarterly', 'Quarterly'), ('monthly', 'Monthly'))
CONTRACT_STATUS = (('draft', 'Draft'), ('active', 'Active'), ('expiring', 'Expiring'), ('renewed', 'Renewed'),
                   ('expired', 'Expired'), ('cancelled', 'Cancelled'))
RENEWAL_STATUS = (('none', 'Not started'), ('in_progress', 'Follow-up'), ('quoted', 'Quotation sent'),
                  ('won', 'Renewed'), ('lost', 'Lost'))
SUPPORT_TIMING = (('business', 'Business hours'), ('24x7', '24 / 7'))
INVOICE_STATUS = (('planned', 'Planned'), ('to_invoice', 'Ready to invoice'), ('invoiced', 'Invoiced'), ('paid', 'Paid'),
                  ('cancelled', 'Cancelled'))
PROJECT_TYPES = (('implementation', 'Implementation'), ('customization', 'Customization'), ('migration', 'Migration'),
                 ('integration', 'Integration'), ('infrastructure', 'Infrastructure'), ('consulting', 'Consulting'),
                 ('internal', 'Internal product development'))
BILLING_METHODS = (('fixed', 'Fixed price'), ('milestone', 'Milestone based'), ('time_and_material', 'Time and material'),
                   ('advance', 'Advance + milestones'), ('non_billable', 'Not billable'))
MILESTONE_STATUS = (('planned', 'Planned'), ('in_progress', 'In progress'), ('ready', 'Ready for acceptance'),
                    ('accepted', 'Accepted'), ('rejected', 'Rejected by customer'))
CR_STATUS = (('draft', 'Draft'), ('submitted', 'Waiting for approval'), ('approved', 'Approved – waiting for customer'),
             ('customer_approved', 'Approved by customer'), ('rejected', 'Rejected'), ('done', 'Done'))
DEV_STATUS = (
    ('submitted', 'Waiting for technical review'),
    ('tech_approved', 'Waiting for commercial approval'),
    ('commercial_approved', 'Waiting for customer approval'),
    ('approved', 'Approved – task created'),
    ('in_progress', 'Under development'),
    ('testing', 'Testing'),
    ('deployed', 'Deployed – waiting for customer confirmation'),
    ('done', 'Done'),
    ('rejected', 'Rejected'),
    ('cancelled', 'Cancelled'),
)
WORKLOG_STATUS = (('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'), ('rejected', 'Rejected'))
RISK_KIND = (('risk', 'Risk'), ('issue', 'Issue'))
LEVEL = (('low', 'Low'), ('medium', 'Medium'), ('high', 'High'))
RISK_STATUS = (('open', 'Open'), ('mitigating', 'Being handled'), ('closed', 'Closed'))


class ProjectsSettings(models.Model):
    ticket_prefix = models.CharField(max_length=10, default='TKT')
    amc_prefix = models.CharField(max_length=10, default='AMC')
    dev_prefix = models.CharField(max_length=10, default='DR')
    cr_prefix = models.CharField(max_length=10, default='CR')
    project_prefix = models.CharField(max_length=10, default='PRJ')
    customer_prefix = models.CharField(max_length=10, default='CUS')
    number_padding = models.PositiveSmallIntegerField(default=5)
    currency = models.CharField(max_length=3, default='AED')
    timezone = models.CharField(max_length=50, default='Asia/Dubai')
    work_days = models.JSONField(default=list, blank=True, help_text='Weekdays 0=Mon … 6=Sun; empty = Mon–Fri')
    work_start = models.TimeField(default='09:00')
    work_end = models.TimeField(default='18:00')
    holiday_calendar_ids = models.JSONField(default=list, blank=True, help_text='ZEO holiday calendars; empty = all')
    renewal_reminder_days = models.JSONField(default=list, blank=True, help_text='Days before expiry; empty = 90, 60, 30, 0')
    after_expiry_reminder_days = models.PositiveSmallIntegerField(default=7, help_text='Remind again every N days after expiry (0 = never)')
    expiring_window_days = models.PositiveSmallIntegerField(default=30)
    waiting_customer_reminder_days = models.PositiveSmallIntegerField(default=3)
    auto_close_resolved_days = models.PositiveSmallIntegerField(default=7, help_text='0 = never close automatically')
    escalation_minutes = models.PositiveIntegerField(default=60, help_text='Escalate again every N minutes while breached')
    approval_not_covered = models.BooleanField(default=True, help_text='Tickets not covered by an AMC need a manager approval before work starts')
    approval_hours_limit = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal('1.00'),
                                               help_text='Time on one ticket beyond this needs approval (0 = no limit)')
    weekly_report_enabled = models.BooleanField(default=True, help_text='E-mail a weekly summary to managers every Monday')
    weekly_report_emails = models.CharField(max_length=500, blank=True, help_text='Comma separated; empty = support managers and management')
    notify_customer = models.BooleanField(default=True)
    portal_enabled = models.BooleanField(default=True)
    portal_url = models.CharField(max_length=255, blank=True, help_text='Address of the portal login page, used in invitation e-mails')
    portal_show_hours = models.BooleanField(default=True, help_text='Customers see used / remaining AMC hours')
    portal_show_invoices = models.BooleanField(default=True)
    default_team = models.ForeignKey('SupportTeam', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    default_sla = models.ForeignKey('SLAPolicy', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    # e-mail to ticket (IMAP, read by the scheduled job; no paid service)
    mailbox_enabled = models.BooleanField(default=False)
    mailbox_host = models.CharField(max_length=200, blank=True)
    mailbox_port = models.PositiveIntegerField(default=993)
    mailbox_ssl = models.BooleanField(default=True)
    mailbox_user = models.CharField(max_length=200, blank=True)
    mailbox_password = models.CharField(max_length=200, blank=True)
    mailbox_folder = models.CharField(max_length=100, default='INBOX')
    mailbox_last_check = models.DateTimeField(null=True, blank=True)
    mailbox_last_error = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Projects settings'

    @classmethod
    def get(cls):
        obj = cls.objects.order_by('id').first()
        return obj or cls.objects.create()


class Sequence(models.Model):
    """Next number per prefix (TKT, AMC, DR …), locked while a number is taken."""
    key = models.CharField(max_length=20, unique=True)
    next_number = models.PositiveIntegerField(default=1)


class Customer(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=200)
    trn = models.CharField('Tax registration number', max_length=30, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    website = models.CharField(max_length=200, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default='United Arab Emirates')
    email_domains = models.CharField(max_length=300, blank=True, help_text='Comma separated, used to match incoming e-mails')
    account_manager = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_accounts')
    branch = models.ForeignKey(BRANCH, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_customers')
    default_sla = models.ForeignKey('SLAPolicy', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Contact(models.Model):
    PORTAL_ROLES = (('user', 'Own tickets'), ('manager', 'All company tickets'))
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    portal_user = models.OneToOneField(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_contact')
    portal_role = models.CharField(max_length=10, choices=PORTAL_ROLES, default='user')
    portal_can_approve = models.BooleanField(default=False, help_text='May approve milestones, change and development requests')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['customer__name', '-is_primary', 'name']

    def __str__(self):
        return f'{self.name} ({self.customer.name})'


class ServiceProduct(models.Model):
    CATEGORIES = (('erp', 'ERP / business software'), ('microsoft', 'Microsoft'), ('cloud', 'Cloud hosting'),
                  ('network', 'Network'), ('hardware', 'Hardware'), ('custom', 'Custom development'), ('other', 'Other'))
    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=20, choices=CATEGORIES, default='erp')
    default_team = models.ForeignKey('SupportTeam', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SupportTeam(models.Model):
    name = models.CharField(max_length=100, unique=True)
    lead = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_led_teams')
    members = models.ManyToManyField(EMP, blank=True, related_name='zp_teams')
    email_alias = models.CharField(max_length=200, blank=True, help_text='Incoming e-mails to this address go to the team')
    auto_assign = models.BooleanField(default=True, help_text='Give new tickets to the member with the fewest open tickets')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class IssueCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    default_team = models.ForeignKey(SupportTeam, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    needs_development = models.BooleanField(default=False)
    default_billable = models.CharField(max_length=12, choices=BILLABLE, blank=True,
                                        help_text='Empty = AMC covered when the customer has an active AMC')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Issue categories'

    def __str__(self):
        return self.name


class SLAPolicy(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'SLA policy'
        verbose_name_plural = 'SLA policies'

    def __str__(self):
        return self.name


class SLATarget(models.Model):
    policy = models.ForeignKey(SLAPolicy, on_delete=models.CASCADE, related_name='targets')
    priority = models.CharField(max_length=10, choices=PRIORITIES)
    response_minutes = models.PositiveIntegerField()
    resolution_minutes = models.PositiveIntegerField()
    business_hours = models.BooleanField(default=True, help_text='Count only working hours (24/7 contracts always count all hours)')

    class Meta:
        unique_together = ('policy', 'priority')


class AMCContract(models.Model):
    number = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='contracts')
    title = models.CharField(max_length=200)
    contract_type = models.CharField(max_length=20, choices=CONTRACT_TYPES, default='comprehensive')
    products = models.ManyToManyField(ServiceProduct, blank=True, related_name='contracts')
    start_date = models.DateField()
    end_date = models.DateField()
    value = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, help_text='Contract amount for the whole term')
    currency = models.CharField(max_length=3, default='AED')
    billing_frequency = models.CharField(max_length=12, choices=BILLING_FREQ, default='annual')
    unlimited_hours = models.BooleanField(default=False)
    included_hours = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    onsite_hours = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO, help_text='Part of the included hours that may be onsite (0 = no limit)')
    hourly_rate_outside = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO, help_text='Rate for chargeable work outside the contract')
    sla_policy = models.ForeignKey(SLAPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name='contracts')
    support_timing = models.CharField(max_length=10, choices=SUPPORT_TIMING, default='business')
    exclusions = models.TextField(blank=True, default='New development, new implementations, hardware replacement, third-party services')
    account_manager = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_contracts')
    status = models.CharField(max_length=12, choices=CONTRACT_STATUS, default='draft')
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='renewals')
    version = models.PositiveSmallIntegerField(default=1)
    renewal_status = models.CharField(max_length=12, choices=RENEWAL_STATUS, default='none')
    renewal_owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_renewals')
    renewal_quote_ref = models.CharField(max_length=60, blank=True)
    renewal_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    renewal_note = models.TextField(blank=True)
    lost_reason = models.CharField(max_length=255, blank=True)
    reminders_sent = models.JSONField(default=list, blank=True, help_text='Reminder keys already sent, e.g. "90", "30", "after:2026-10-01"')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-start_date', '-id']
        verbose_name = 'AMC contract'
        permissions = [('renew_amccontract', 'Can renew AMC contracts')]

    def __str__(self):
        return f'{self.number} – {self.customer.name}'


class AMCBilling(models.Model):
    contract = models.ForeignKey(AMCContract, on_delete=models.CASCADE, related_name='billing')
    due_date = models.DateField()
    period_label = models.CharField(max_length=60, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=12, choices=INVOICE_STATUS, default='planned')
    invoice_no = models.CharField(max_length=60, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['due_date', 'id']


class Ticket(models.Model):
    number = models.CharField(max_length=30, unique=True)
    subject = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.PROTECT, related_name='tickets',
                                 help_text='Empty = internal helpdesk ticket')
    contact = models.ForeignKey(Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    requester_employee = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_requested_tickets')
    requester_email = models.EmailField(blank=True)
    requester_name = models.CharField(max_length=150, blank=True)
    product = models.ForeignKey(ServiceProduct, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    contract = models.ForeignKey(AMCContract, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    category = models.ForeignKey(IssueCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    priority = models.CharField(max_length=10, choices=PRIORITIES, default='medium')
    channel = models.CharField(max_length=10, choices=CHANNELS, default='manual')
    team = models.ForeignKey(SupportTeam, null=True, blank=True, on_delete=models.SET_NULL, related_name='tickets')
    assigned_to = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_tickets')
    stage = models.CharField(max_length=24, choices=TICKET_STAGES, default='new', db_index=True)
    billable_status = models.CharField(max_length=12, choices=BILLABLE, default='amc')
    covered = models.BooleanField(default=False, help_text='Product and category are covered by the linked AMC')
    coverage_note = models.CharField(max_length=255, blank=True)
    # SLA clock
    sla_policy = models.ForeignKey(SLAPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    sla_business_hours = models.BooleanField(default=True)
    response_minutes = models.PositiveIntegerField(null=True, blank=True)
    resolution_minutes = models.PositiveIntegerField(null=True, blank=True)
    response_due = models.DateTimeField(null=True, blank=True, db_index=True)
    resolution_due = models.DateTimeField(null=True, blank=True, db_index=True)
    first_response_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    paused_minutes = models.PositiveIntegerField(default=0)
    response_breached = models.BooleanField(default=False)
    resolution_breached = models.BooleanField(default=False, db_index=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)
    last_escalated_at = models.DateTimeField(null=True, blank=True)
    waiting_since = models.DateTimeField(null=True, blank=True)
    waiting_reminded_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)
    reopen_count = models.PositiveSmallIntegerField(default=0)
    approval_status = models.CharField(max_length=10, choices=APPROVAL_STATUS, default='none', db_index=True)
    approval_reason = models.CharField(max_length=12, choices=APPROVAL_REASONS, blank=True)
    approval_requested_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    approved_extra_hours = models.DecimalField(max_digits=7, decimal_places=2, default=ZERO,
                                               help_text='Hours approved beyond the company limit')
    approval_note = models.TextField(blank=True)
    approval_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approval_at = models.DateTimeField(null=True, blank=True)
    satisfaction = models.PositiveSmallIntegerField(null=True, blank=True, help_text='1 – 5')
    satisfaction_note = models.TextField(blank=True)
    project_id = models.IntegerField(null=True, blank=True, db_index=True)
    task_id = models.IntegerField(null=True, blank=True, db_index=True)
    email_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-id']
        permissions = [
            ('assign_ticket', 'Can assign, escalate and close any ticket (support manager)'),
            ('work_ticket', 'Can work on tickets of own teams (support engineer)'),
        ]

    def __str__(self):
        return f'{self.number} {self.subject}'

    @property
    def is_open(self):
        return self.stage in OPEN_STAGES


class TicketFile(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='files')
    file = models.FileField(upload_to='zeo_projects/tickets/%Y/%m/')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    is_internal = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)


class TicketMessage(models.Model):
    VIA = (('staff', 'Support'), ('portal', 'Customer portal'), ('email', 'E-mail'), ('system', 'System'))
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='messages')
    body = models.TextField()
    is_internal = models.BooleanField(default=False, help_text='Internal note: never shown to the customer')
    via = models.CharField(max_length=10, choices=VIA, default='staff')
    author_user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    author_contact = models.ForeignKey(Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    author_name = models.CharField(max_length=150, blank=True)
    files = models.ManyToManyField(TicketFile, blank=True, related_name='messages')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']


class TicketEvent(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='events')
    kind = models.CharField(max_length=30)
    text = models.CharField(max_length=500)
    public = models.BooleanField(default=True, help_text='Shown to the customer in the portal')
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    user_name = models.CharField(max_length=150, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-at', '-id']


class WorkLog(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='worklogs')
    employee = models.ForeignKey(EMP, on_delete=models.CASCADE, related_name='zp_worklogs')
    date = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2)
    description = models.TextField(blank=True)
    onsite = models.BooleanField(default=False)
    billable = models.BooleanField(default=False, help_text='Chargeable outside the AMC')
    counts_against_amc = models.BooleanField(default=True)
    travel_cost = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    other_charges = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=10, choices=WORKLOG_STATUS, default='submitted', db_index=True)
    cost_rate = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    cost_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    approved_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        permissions = [('approve_worklog', 'Can approve support work logs')]


class DevRequest(models.Model):
    number = models.CharField(max_length=30, unique=True)
    ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.SET_NULL, related_name='dev_requests')
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name='dev_requests')
    title = models.CharField(max_length=255)
    issue = models.TextField()
    proposed_solution = models.TextField(blank=True)
    estimated_hours = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    estimated_cost = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    chargeable = models.BooleanField(default=True)
    needs_customer_approval = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=DEV_STATUS, default='submitted', db_index=True)
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    requested_at = models.DateTimeField(auto_now_add=True)
    tech_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    tech_at = models.DateTimeField(null=True, blank=True)
    tech_note = models.TextField(blank=True)
    commercial_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    commercial_at = models.DateTimeField(null=True, blank=True)
    commercial_note = models.TextField(blank=True)
    quoted_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    customer_decision_by = models.CharField(max_length=150, blank=True)
    customer_decision_at = models.DateTimeField(null=True, blank=True)
    customer_note = models.TextField(blank=True)
    project_id = models.IntegerField(null=True, blank=True)
    task_id = models.IntegerField(null=True, blank=True)
    assigned_to = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_dev_requests')
    deployed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    rejected_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-requested_at', '-id']
        permissions = [
            ('approve_tech_devrequest', 'Can give technical approval to development requests'),
            ('approve_commercial_devrequest', 'Can give commercial approval to development requests'),
        ]


class ProjectProfile(models.Model):
    project_id = models.IntegerField(unique=True)
    number = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name='projects')
    project_type = models.CharField(max_length=20, choices=PROJECT_TYPES, default='implementation')
    billing_method = models.CharField(max_length=20, choices=BILLING_METHODS, default='milestone')
    contract_ref = models.CharField(max_length=100, blank=True, help_text='Contract / sales order / LPO reference')
    scope = models.TextField(blank=True)
    deliverables = models.TextField(blank=True)
    invoiced_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO, help_text='Invoiced outside milestones (advance, T&M)')
    portal_visible = models.BooleanField(default=True)
    product = models.ForeignKey(ServiceProduct, null=True, blank=True, on_delete=models.SET_NULL, related_name='projects')
    plan_template = models.ForeignKey('PlanTemplate', null=True, blank=True, on_delete=models.SET_NULL, related_name='projects')
    updated_at = models.DateTimeField(auto_now=True)


class Milestone(models.Model):
    project_id = models.IntegerField(db_index=True)
    sequence = models.PositiveSmallIntegerField(default=1)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    planned_date = models.DateField(null=True, blank=True)
    depends_on = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=12, choices=MILESTONE_STATUS, default='planned')
    completed_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.CharField(max_length=150, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    acceptance_note = models.TextField(blank=True)
    invoice_status = models.CharField(max_length=12, choices=INVOICE_STATUS, default='planned')
    invoice_no = models.CharField(max_length=60, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    paid_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['project_id', 'sequence', 'id']


class ChangeRequest(models.Model):
    number = models.CharField(max_length=30, unique=True)
    project_id = models.IntegerField(db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    extra_hours = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    extra_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    extra_days = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=CR_STATUS, default='submitted')
    requested_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    customer_decision_by = models.CharField(max_length=150, blank=True)
    customer_decision_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-requested_at', '-id']
        permissions = [('approve_changerequest', 'Can approve project change requests')]


class ProjectRisk(models.Model):
    project_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=6, choices=RISK_KIND, default='risk')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    impact = models.CharField(max_length=6, choices=LEVEL, default='medium')
    probability = models.CharField(max_length=6, choices=LEVEL, default='medium')
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_risks')
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=RISK_STATUS, default='open')
    mitigation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', '-id']


class ProjectExpense(models.Model):
    CATEGORIES = (('third_party', 'Third-party service'), ('license', 'Licence'), ('travel', 'Travel'),
                  ('hardware', 'Hardware'), ('other', 'Other'))
    project_id = models.IntegerField(db_index=True)
    date = models.DateField()
    category = models.CharField(max_length=12, choices=CATEGORIES, default='third_party')
    description = models.CharField(max_length=255)
    vendor = models.CharField(max_length=150, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-date', '-id']


class TaskDetail(models.Model):
    task_id = models.IntegerField(unique=True)
    parent_task_id = models.IntegerField(null=True, blank=True, db_index=True, help_text='Set for a sub-task')
    priority = models.CharField(max_length=10, choices=PRIORITIES, default='medium')
    phase = models.CharField(max_length=60, blank=True, help_text='Analysis, Configuration, Development, Migration, Testing, Deployment')
    acceptance_criteria = models.TextField(blank=True)
    reviewer = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='zp_reviews')
    depends_on = models.JSONField(default=list, blank=True, help_text='Task ids that must be finished first')
    blockers = models.TextField(blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    estimated_hours = models.DecimalField(max_digits=8, decimal_places=2, default=ZERO)
    customer_approval = models.BooleanField(default=False, help_text='The customer must approve this task')
    customer_approved_by = models.CharField(max_length=150, blank=True)
    customer_approved_at = models.DateTimeField(null=True, blank=True)
    milestone = models.ForeignKey(Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name='tasks')
    overdue_notified_on = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True, help_text='Set when the task is started')
    actual_end = models.DateField(null=True, blank=True, help_text='Set when the task is completed')



# ------------------------------------------------------------------ v1.15.0 plan templates
class PlanTemplate(models.Model):
    """A reusable project plan, e.g. "Odoo implementation – standard". Picked when a project is created (or later)."""
    name = models.CharField(max_length=150, unique=True)
    product = models.ForeignKey(ServiceProduct, null=True, blank=True, on_delete=models.SET_NULL, related_name='plan_templates')
    project_type = models.CharField(max_length=20, choices=PROJECT_TYPES, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class PlanTemplateLine(models.Model):
    template = models.ForeignKey(PlanTemplate, on_delete=models.CASCADE, related_name='lines')
    sequence = models.PositiveSmallIntegerField(default=1)
    phase = models.CharField(max_length=60, blank=True, help_text='Becomes a project stage')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_day = models.PositiveSmallIntegerField(default=0, help_text='Working days after the project start')
    duration_days = models.PositiveSmallIntegerField(default=1, help_text='Working days')
    hours = models.DecimalField(max_digits=7, decimal_places=2, default=ZERO)
    role = models.CharField(max_length=60, blank=True, help_text='Who usually does it: consultant, developer, PM …')
    is_milestone = models.BooleanField(default=False, help_text='Also create a milestone ending with this line')
    milestone_pct = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO, help_text='% of the contract value')
    customer_approval = models.BooleanField(default=False)
    is_subtask = models.BooleanField(default=False, help_text='Sub-task of the nearest main task above it')

    class Meta:
        ordering = ['template', 'sequence', 'id']


# ------------------------------------------------------------------ v1.15.0 notification settings
class NotificationSetting(models.Model):
    """One row per event and scope. Company default: customer and user empty. A customer row overrides the company
    for that customer's records; a user row overrides both for that user."""
    event = models.CharField(max_length=40, db_index=True)
    customer = models.ForeignKey(Customer, null=True, blank=True, on_delete=models.CASCADE, related_name='notification_settings')
    user = models.ForeignKey(USER, null=True, blank=True, on_delete=models.CASCADE, related_name='zp_notification_settings')
    email = models.BooleanField(default=True)
    dashboard = models.BooleanField(default=True, help_text='Item in My To-do / My work')

    class Meta:
        unique_together = (('event', 'customer', 'user'),)



class TaskFile(models.Model):
    """v1.15.0 attachment on a project task or sub-task (ZEO Task id)."""
    task_id = models.IntegerField(db_index=True)
    file = models.FileField(upload_to='zeo_projects/tasks/%Y/%m/')
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(USER, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at', '-id']
