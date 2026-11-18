"""ZEO CRM (v1.18.0) – contacts, pipelines, opportunities, activities, viewings, offers and listings.
Works alone; with ZEO Property installed and activated it uses Property's units and turns won deals into reservations."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'

ROLES = (
    ('crm_manager', 'CRM manager'), ('crm_team_lead', 'Sales team lead'), ('crm_agent', 'Sales agent'), ('crm_viewer', 'CRM viewer (read only)'),
    # v1.67.28 the roles of the CRM scope (part 4)
    ('crm_director', 'Sales director / management'), ('crm_presales', 'Presales / technical consultant / estimation engineer'),
    ('crm_marketing', 'Marketing manager / executive'), ('crm_account', 'Account / customer relationship manager'),
    ('crm_service', 'Customer service executive / support manager'), ('crm_leadgen', 'Lead generation executive'),
)


class CRMSettings(AppSettingsBase):
    first_contact_hours = models.PositiveSmallIntegerField('first contact within (hours)', default=2,
                                                           help_text='A new opportunity must get its first call / message within this time')
    duplicate_check = models.BooleanField('warn about duplicate contacts', default=True)
    auto_assign = models.CharField('assign new opportunities', max_length=12, default='round_robin',
                                   choices=(('none', 'Do not assign'), ('round_robin', 'Round robin to sales agents')))
    last_assigned = models.PositiveIntegerField(default=0)
    renewal_days = models.PositiveSmallIntegerField('open renewal opportunities (days before lease end)', default=90)
    currency = models.CharField(max_length=3, default='AED')
    # v1.33.0
    discount_limit_pct = models.DecimalField('discount % an agent may give without approval', max_digits=5, decimal_places=2, default=10)
    qualify_score = models.PositiveSmallIntegerField('lead score that marks a lead as qualified', default=50)
    quote_validity_days = models.PositiveSmallIntegerField('quotations valid for (days)', default=30)
    quote_terms = models.TextField('standard terms on quotations', blank=True, default='Prices exclude delivery unless stated. Payment as per the agreed terms. Quotation valid until the date shown.')
    capture_key = models.CharField('web form key', max_length=40, blank=True, help_text='Key your website form posts with (POST /zeo-crm/api/capture/<key>/). Empty = capture off')
    assign_leads = models.CharField('assign new leads', max_length=12, default='rules', choices=(('none', 'Do not assign'), ('rules', 'Assignment rules, then round robin'), ('round_robin', 'Round robin to sales agents')))
    # v1.67.28 general CRM (release 2)
    enquiry_approval = models.BooleanField('enquiries need approval before quoting', default=True)
    auto_onboarding = models.BooleanField('start customer onboarding when a deal is won', default=True)
    contract_renewal_opps = models.BooleanField('open renewal opportunities before contracts end', default=True)
    complaint_sla_urgent = models.PositiveSmallIntegerField('complaint resolution – urgent (hours)', default=4)
    complaint_sla_high = models.PositiveSmallIntegerField('complaint resolution – high (hours)', default=24)
    complaint_sla_medium = models.PositiveSmallIntegerField('complaint resolution – medium (hours)', default=72)
    complaint_sla_low = models.PositiveSmallIntegerField('complaint resolution – low (hours)', default=120)
    followup_escalate_hours = models.PositiveSmallIntegerField('escalate missed follow-ups after (hours)', default=24)
    ai_enabled = models.BooleanField('CRM AI assistant', default=True, help_text='v1.67.30: scores, forecasts, suggestions. The external AI is used only when '
                                                                                 'Settings › AI settings allows it for the CRM assistant')
    churn_days = models.PositiveSmallIntegerField('customer at churn risk after (days without business)', default=180)
    # v1.67.29 communication and marketing (release 3)
    public_api_url = models.CharField('public address of the ZEO server', max_length=200, blank=True,
                                      help_text='e.g. https://hr.yourcompany.ae – used for web-hook addresses, open / click tracking and unsubscribe links')
    marketing_footer = models.TextField('footer of marketing messages', blank=True, default='You receive this because you agreed to hear from us.')
    sender_name = models.CharField('sender name for marketing e-mails', max_length=100, blank=True)
    reply_to = models.EmailField('reply-to for marketing e-mails', blank=True)

    class Meta:
        permissions = [(k, f'CRM role: {v}') for k, v in ROLES]
        verbose_name = 'CRM settings'


class CRMSequence(SequenceBase):
    pass


class CRMEvent(EventBase):
    class Meta(EventBase.Meta):
        pass


class CRMFile(FileBase):
    pass


class Source(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


PIPE_KINDS = (('sales', 'Sales'), ('leasing', 'Leasing'), ('renewal', 'Renewal'), ('service', 'Service'), ('general', 'General'))


class Pipeline(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=PIPE_KINDS, default='general')
    order = models.PositiveSmallIntegerField(default=1)
    industry = models.CharField(max_length=30, blank=True, help_text='v1.67.27: industry of the pipeline – its opportunities get the industry fields. Empty = every industry')

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class Stage(PackItem):
    pipeline = models.ForeignKey(Pipeline, on_delete=models.CASCADE, related_name='stages')
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=80)
    order = models.PositiveSmallIntegerField(default=1)
    probability = models.PositiveSmallIntegerField('probability %', default=10)
    is_won = models.BooleanField('won stage', default=False)
    is_lost = models.BooleanField('lost stage', default=False)

    class Meta:
        ordering = ['pipeline__order', 'order']

    def __str__(self):
        return f'{self.pipeline.name} › {self.name}'


class LostReason(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Contact(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=10, choices=(('person', 'Person'), ('company', 'Company')), default='person')
    name = models.CharField(max_length=150)
    company_name = models.CharField('company', max_length=150, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='account owner')
    contact_type = models.CharField('type', max_length=12, default='prospect',
                                    choices=(('prospect', 'Prospect'), ('customer', 'Customer'), ('tenant', 'Tenant'), ('owner', 'Owner'),
                                             ('buyer', 'Buyer'), ('partner', 'Partner / broker'), ('other', 'Other')))
    consent = models.BooleanField('agrees to marketing', default=False)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    property_party_id = models.IntegerField('tenant / buyer in Property', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.33.0
    account = models.ForeignKey('ZeoMasters.Account', null=True, blank=True, on_delete=models.SET_NULL, related_name='contacts')
    job_title = models.CharField(max_length=80, blank=True)
    role = models.CharField('role in the account', max_length=12, blank=True,
                            choices=(('decision', 'Decision maker'), ('influencer', 'Influencer'), ('user', 'User'), ('finance', 'Finance / payables'),
                                     ('procurement', 'Procurement'), ('technical', 'Technical'), ('purchase', 'Purchase manager'),   # v1.67.28
                                     ('signatory', 'Authorised signatory'), ('general', 'General contact'), ('other', 'Other')))
    tags = models.JSONField(default=list, blank=True, help_text='e.g. VIP, Newsletter, Reseller')
    territory = models.ForeignKey('Territory', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    merged_into = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='crm_contacts', verbose_name='business partner')   # v1.41.0
    email_opt_out = models.BooleanField('no marketing e-mails', default=False)   # v1.67.29
    sms_opt_out = models.BooleanField('no marketing SMS', default=False)
    whatsapp_opt_out = models.BooleanField('no marketing WhatsApp', default=False)
    opted_out_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Listing(models.Model):
    """Something offered: a property unit (from ZEO Property when it is installed) or any product / service."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=150)
    kind = models.CharField('offered for', max_length=8, choices=(('rent', 'Rent'), ('sale', 'Sale'), ('service', 'Service')), default='sale')
    unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    location = models.CharField('location / building', max_length=150, blank=True)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    area = models.DecimalField('area (sq ft)', max_digits=10, decimal_places=2, null=True, blank=True)
    price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, default='available',
                              choices=(('available', 'Available'), ('reserved', 'Reserved'), ('let', 'Let'), ('sold', 'Sold'), ('withdrawn', 'Withdrawn')))
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.title}'


INTEREST = (('rent', 'Rent'), ('buy', 'Buy'), ('service', 'Service'), ('other', 'Other'))


class Opportunity(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=150)
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name='opportunities')
    pipeline = models.ForeignKey(Pipeline, on_delete=models.PROTECT)
    stage = models.ForeignKey(Stage, null=True, blank=True, on_delete=models.PROTECT)
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='agent')
    status = models.CharField(max_length=6, choices=(('open', 'Open'), ('won', 'Won'), ('lost', 'Lost')), default='open')
    interest = models.CharField('looking to', max_length=8, choices=INTEREST, default='other')
    value = models.DecimalField('expected value', max_digits=14, decimal_places=2, default=0)
    expected_close = models.DateField(null=True, blank=True)
    budget_max = models.DecimalField('budget up to', max_digits=14, decimal_places=2, null=True, blank=True)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    requirements = models.TextField(blank=True)
    source = models.ForeignKey(Source, null=True, blank=True, on_delete=models.SET_NULL)
    listing = models.ForeignKey(Listing, null=True, blank=True, on_delete=models.SET_NULL)
    unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    lost_reason = models.ForeignKey(LostReason, null=True, blank=True, on_delete=models.SET_NULL)
    lost_note = models.TextField(blank=True)
    first_contact_due = models.DateTimeField(null=True, blank=True)
    first_contacted_at = models.DateTimeField(null=True, blank=True)
    reservation_id = models.IntegerField('reservation (Property)', null=True, blank=True)
    renewal_lease_id = models.IntegerField('lease to renew (Property)', null=True, blank=True)
    won_ref = models.CharField('won as', max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    # v1.33.0
    account = models.ForeignKey('ZeoMasters.Account', null=True, blank=True, on_delete=models.SET_NULL, related_name='opportunities')
    campaign = models.ForeignKey('Campaign', null=True, blank=True, on_delete=models.SET_NULL, related_name='opportunities')
    territory = models.ForeignKey('Territory', null=True, blank=True, on_delete=models.SET_NULL, related_name='opportunities')
    competitors = models.ManyToManyField('Competitor', blank=True, related_name='opportunities')
    competitor_note = models.CharField('competitor position', max_length=200, blank=True)
    lead = models.ForeignKey('Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='converted from lead')
    industry = models.CharField(max_length=30, blank=True, help_text='v1.67.27: set from the pipeline (or chosen) – decides the industry fields')
    decision_makers = models.ManyToManyField(Contact, blank=True, related_name='decides_on', verbose_name='decision makers')   # v1.67.28
    strategy = models.TextField('sales strategy', blank=True)
    technical_requirements = models.TextField(blank=True)
    next_step = models.CharField('next step', max_length=200, blank=True)
    ai_probability = models.PositiveSmallIntegerField('AI win chance %', null=True, blank=True)   # v1.67.30
    ai_reason = models.CharField('AI win chance – why', max_length=300, blank=True)
    extra = models.JSONField(default=dict, blank=True, help_text='v1.67.27: values of the industry fields {code: value}')

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'opportunities'

    def __str__(self):
        return f'{self.number} {self.title}'


ACT_KINDS = (('call', 'Call'), ('email', 'E-mail'), ('message', 'Message / WhatsApp'), ('meeting', 'Meeting'), ('task', 'Task'), ('note', 'Note'),
             # v1.67.28
             ('site_visit', 'Site visit'), ('online', 'Online meeting'), ('demo', 'Product demonstration'), ('presentation', 'Customer presentation'),
             ('internal', 'Internal activity'))


class Activity(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=12, choices=ACT_KINDS, default='call')
    subject = models.CharField(max_length=200)
    contact = models.ForeignKey(Contact, null=True, blank=True, on_delete=models.CASCADE, related_name='activities')
    opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.CASCADE, related_name='activities')
    owner = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+', verbose_name='done by')
    due_at = models.DateTimeField('due', null=True, blank=True)
    status = models.CharField(max_length=10, choices=(('open', 'To do'), ('done', 'Done'), ('cancelled', 'Cancelled')), default='open')
    done_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # v1.33.0 recurring activities (next one is made when this one is done) + lead link
    repeat = models.CharField(max_length=10, default='', blank=True, choices=(('', 'No repeat'), ('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly')))
    repeat_until = models.DateField(null=True, blank=True)
    lead = models.ForeignKey('Lead', null=True, blank=True, on_delete=models.CASCADE, related_name='activities')
    duration_min = models.PositiveSmallIntegerField('duration (minutes)', default=30)
    priority = models.CharField(max_length=6, default='normal', choices=(('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent')))   # v1.67.28
    escalated = models.PositiveSmallIntegerField('escalation level', default=0, help_text='v1.67.28: 1 = owner reminded, 2 = team lead told')
    direction = models.CharField(max_length=3, blank=True, choices=(('in', 'Incoming'), ('out', 'Outgoing')))   # v1.67.29 telephony / messages
    phone = models.CharField(max_length=30, blank=True)
    duration_sec = models.PositiveIntegerField('duration (seconds)', null=True, blank=True)
    recording_url = models.CharField('call recording', max_length=300, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'activities'


class Viewing(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name='viewings')
    listing = models.ForeignKey(Listing, null=True, blank=True, on_delete=models.SET_NULL)
    unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    scheduled_at = models.DateTimeField('date and time')
    agent = models.ForeignKey(EMP, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    status = models.CharField(max_length=10, default='scheduled',
                              choices=(('scheduled', 'Scheduled'), ('done', 'Done'), ('no_show', 'No show'), ('cancelled', 'Cancelled')))
    interest_level = models.CharField(max_length=6, blank=True, choices=(('high', 'High'), ('medium', 'Medium'), ('low', 'Low')))
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ['-scheduled_at']


class Offer(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name='offers')
    kind = models.CharField(max_length=6, choices=(('rent', 'Rent (per year)'), ('sale', 'Sale'), ('other', 'Other')), default='rent')
    listing = models.ForeignKey(Listing, null=True, blank=True, on_delete=models.SET_NULL)
    unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    payments = models.PositiveSmallIntegerField('number of payments / cheques', default=1)
    terms = models.TextField(blank=True)
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default='draft',
                              choices=(('draft', 'Draft'), ('sent', 'Sent'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('expired', 'Expired')))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


# ====================================================================== v1.33.0 CRM sales management
BR = dict(null=True, blank=True, on_delete=models.SET_NULL, related_name='+')


class Campaign(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, default='other', choices=(('email', 'E-mail'), ('social', 'Social media'), ('event', 'Event / exhibition'),
                                                                     ('ads', 'Paid ads'), ('webinar', 'Webinar'), ('referral', 'Referral drive'), ('other', 'Other')))
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    owner = models.ForeignKey(EMP, verbose_name='owner', **BR)
    spent = models.DecimalField('actual cost', max_digits=14, decimal_places=2, default=0)   # v1.67.29
    target_audience = models.TextField(blank=True)
    segment = models.ForeignKey('Segment', **BR)
    landing_url = models.CharField('landing page', max_length=300, blank=True)
    external_ref = models.CharField('ad campaign / form ID (Meta, Google, LinkedIn)', max_length=120, blank=True)
    expected_leads = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-start', 'name']

    def __str__(self):
        return self.name


class Territory(PackItem):
    """A sales area: country / emirate / city list, with a manager and members. Rules assign leads by territory."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch that works this territory')
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    areas = models.TextField('cities / areas', blank=True, help_text='One per line, e.g. Dubai, Sharjah. Used to match leads by city')
    manager = models.ForeignKey(EMP, verbose_name='territory manager', **BR)
    members = models.ManyToManyField(EMP, blank=True, related_name='crm_territories', verbose_name='sales agents')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'territories'

    def __str__(self):
        return self.name

    def area_list(self):
        return [a.strip().lower() for a in (self.areas or '').replace(',', '\n').split('\n') if a.strip()]


class Competitor(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    website = models.CharField(max_length=120, blank=True)
    strengths = models.TextField(blank=True)
    weaknesses = models.TextField(blank=True)
    pricing_note = models.CharField('typical pricing', max_length=200, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


SCORE_WHEN = (('has_email', 'Has an e-mail'), ('has_phone', 'Has a phone'), ('has_company', 'Company given'), ('source', 'Lead source is (value = code)'),
              ('campaign', 'Campaign is (value = code)'), ('value_min', 'Estimated value at least (value)'), ('keyword', 'Requirements contain (value)'),
              ('activity', 'Had a call / meeting'), ('city', 'City contains (value)'), ('job_title', 'Job title contains (value)'),
              # v1.33.1 property
              ('has_budget', 'Budget given'), ('has_bedrooms', 'Bedrooms given'), ('has_area', 'Community / building given'), ('area', 'Community / building contains (value)'),
              ('interest', 'Looking to (value = rent / buy / service)'), ('meeting', 'Had a meeting / site visit'),
              # v1.67.27 industry fields and industry
              ('field', 'Industry field (value = code, code=value, code>=number or code<=number)'), ('industry', 'Industry is (value = pack code)'))


class LeadScoreRule(PackItem):
    name = models.CharField(max_length=100)
    when = models.CharField(max_length=12, choices=SCORE_WHEN, default='has_email')
    value = models.CharField(max_length=100, blank=True)
    points = models.SmallIntegerField(default=10, help_text='Negative points lower the score')
    order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.name


class AssignmentRule(PackItem):
    """First matching rule gives the owner of a new lead / opportunity; without a match the round robin of the settings applies."""
    name = models.CharField(max_length=100)
    order = models.PositiveSmallIntegerField(default=1)
    source = models.ForeignKey(Source, **BR, verbose_name='when source is')
    territory = models.ForeignKey(Territory, **BR, verbose_name='when territory is')
    campaign = models.ForeignKey(Campaign, **BR, verbose_name='when campaign is')
    min_value = models.DecimalField('when value at least', max_digits=14, decimal_places=2, null=True, blank=True)
    keyword = models.CharField('when requirements contain', max_length=100, blank=True)
    area = models.CharField('when community / building contains', max_length=100, blank=True)   # v1.33.1
    interest = models.CharField('when looking to', max_length=8, blank=True, choices=(('', '–'),) + INTEREST)
    assign_to = models.ForeignKey(EMP, **BR, verbose_name='assign to agent')
    round_robin_territory = models.BooleanField('or round robin among the territory agents', default=False)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.name


LEAD_STATUS = (('new', 'New'), ('working', 'Working'), ('nurture', 'Nurturing'), ('qualified', 'Qualified'), ('converted', 'Converted'), ('disqualified', 'Disqualified'))


class Lead(models.Model):
    """An unqualified enquiry. Qualify → convert to a contact (+ account) and an opportunity. Kept apart from contacts so the
    contact list stays clean (Odoo / Zoho lead → contact + deal)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    name = models.CharField('person', max_length=150)
    company_name = models.CharField('company', max_length=150, blank=True)
    job_title = models.CharField(max_length=80, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    city = models.CharField(max_length=60, blank=True)
    country = models.CharField(max_length=60, blank=True)
    area = models.CharField('community / building', max_length=100, blank=True)   # v1.33.1 property
    interest_kind = models.CharField('looking to', max_length=8, choices=INTEREST, default='other')
    budget_max = models.DecimalField('budget up to', max_digits=14, decimal_places=2, null=True, blank=True)
    bedrooms = models.PositiveSmallIntegerField(null=True, blank=True)
    unit_id = models.IntegerField('unit (Property)', null=True, blank=True)
    source = models.ForeignKey(Source, **BR)
    campaign = models.ForeignKey(Campaign, **BR)
    channel = models.CharField('came in by', max_length=8, default='manual', choices=(('manual', 'Entered by hand'), ('web', 'Web form'), ('email', 'E-mail'),
                                                                                       ('phone', 'Phone call'), ('import', 'Import'), ('api', 'API'),
                                                                                       # v1.67.29
                                                                                       ('whatsapp', 'WhatsApp'), ('facebook', 'Facebook / Instagram'), ('google', 'Google Ads'),
                                                                                       ('linkedin', 'LinkedIn'), ('walkin', 'Walk-in'), ('referral', 'Referral')))
    territory = models.ForeignKey(Territory, **BR)
    owner = models.ForeignKey(EMP, **BR, verbose_name='agent')
    status = models.CharField(max_length=12, choices=LEAD_STATUS, default='new')
    score = models.SmallIntegerField(default=0)
    rating = models.CharField(max_length=4, default='', blank=True, choices=(('', '–'), ('hot', 'Hot'), ('warm', 'Warm'), ('cold', 'Cold')))
    interest = models.CharField('interested in', max_length=150, blank=True)
    requirements = models.TextField(blank=True)
    value = models.DecimalField('estimated value', max_digits=14, decimal_places=2, default=0)
    consent = models.BooleanField('agrees to marketing', default=False)
    disqualify_reason = models.ForeignKey(LostReason, **BR)
    contact = models.ForeignKey(Contact, **BR, verbose_name='converted to contact')
    account = models.ForeignKey('ZeoMasters.Account', **BR, verbose_name='converted to account')
    opportunity = models.ForeignKey(Opportunity, **BR, verbose_name='converted to opportunity')
    first_contact_due = models.DateTimeField(null=True, blank=True)
    first_contacted_at = models.DateTimeField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    industry = models.CharField(max_length=30, blank=True, help_text='v1.67.27: decides the industry fields; converted leads give it to the opportunity')
    priority = models.CharField(max_length=6, default='medium', choices=(('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent')))   # v1.67.28
    next_follow_up = models.DateField('next follow-up', null=True, blank=True)
    expected_close = models.DateField('expected closing date', null=True, blank=True)
    reject_note = models.CharField('rejection note', max_length=200, blank=True)
    email_opt_out = models.BooleanField('no marketing e-mails', default=False)   # v1.67.29
    sms_opt_out = models.BooleanField('no marketing SMS', default=False)
    whatsapp_opt_out = models.BooleanField('no marketing WhatsApp', default=False)
    external_ref = models.CharField('lead ID at the source (Meta / Google / LinkedIn)', max_length=80, blank=True, db_index=True)
    ai_score = models.PositiveSmallIntegerField('AI score (chance to convert %)', null=True, blank=True)   # v1.67.30
    ai_reason = models.CharField('AI score – why', max_length=300, blank=True)
    extra = models.JSONField(default=dict, blank=True, help_text='v1.67.27: values of the industry fields {code: value}')

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.number} {self.name}'


QUOTE_STATUS = (('draft', 'Draft'), ('approval', 'Needs approval'), ('approved', 'Approved'), ('sent', 'Sent'), ('accepted', 'Accepted'),
                ('rejected', 'Rejected'), ('expired', 'Expired'), ('cancelled', 'Cancelled'))
MONEY = dict(max_digits=14, decimal_places=2)


class DocBase(models.Model):
    """What a quotation and a sales order share: the customer, the lines (items JSON, edited on screen) and the totals."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    account = models.ForeignKey('ZeoMasters.Account', **BR, verbose_name='customer (account)')
    contact = models.ForeignKey(Contact, **BR, verbose_name='contact person')
    opportunity = models.ForeignKey(Opportunity, **BR)
    date = models.DateField()
    owner = models.ForeignKey(EMP, **BR, verbose_name='sales agent')
    currency = models.CharField(max_length=3, default='AED')
    price_book = models.ForeignKey('ZeoMasters.PriceBook', **BR)
    payment_term = models.ForeignKey('ZeoMasters.PaymentTerm', **BR)
    reference = models.CharField('customer reference', max_length=60, blank=True)
    items = models.JSONField(default=list, blank=True, help_text='Lines: product, description, qty, uom, unit price, discount %, tax')
    discount_pct = models.DecimalField('overall discount %', max_digits=5, decimal_places=2, default=0)
    subtotal = models.DecimalField(default=0, **MONEY)
    discount_total = models.DecimalField(default=0, **MONEY)
    tax_total = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    notes = models.TextField('notes to the customer', blank=True)
    terms = models.TextField('terms and conditions', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    industry = models.CharField(max_length=30, blank=True, help_text='v1.67.27: from the opportunity (or chosen) – decides the industry fields')
    extra = models.JSONField(default=dict, blank=True, help_text='v1.67.27: values of the industry fields {code: value}')

    class Meta:
        abstract = True


class Quotation(DocBase):
    valid_until = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=QUOTE_STATUS, default='draft')
    revision = models.PositiveSmallIntegerField(default=1)
    previous = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='revisions', verbose_name='revised from')
    approval_note = models.CharField(max_length=200, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, **BR)
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField('accepted / rejected on', null=True, blank=True)
    lost_reason = models.ForeignKey(LostReason, **BR)
    sales_order = models.ForeignKey('SalesOrder', **BR)
    quote_kind = models.CharField('type', max_length=12, default='product', choices=(('product', 'Products'), ('service', 'Services'), ('project', 'Project'),
                                                                                    ('subscription', 'Recurring subscription')))   # v1.67.28
    billing = models.CharField('billing (subscriptions)', max_length=10, default='once', choices=(('once', 'One-off'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
                                                                                                  ('yearly', 'Yearly')))
    delivery_terms = models.TextField('delivery terms', blank=True)
    scope_of_work = models.TextField('scope of work', blank=True)
    template = models.ForeignKey('QuotationTemplate', **BR)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number


class QuotationLine(models.Model):
    """Relational copy of the items (for reports and the later Sales app); rewritten on every save of the quotation."""
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='lines')
    order = models.PositiveSmallIntegerField(default=1)
    product = models.ForeignKey('ZeoMasters.Product', **BR)
    description = models.CharField(max_length=300, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=10, blank=True)
    unit_price = models.DecimalField(default=0, **MONEY)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax = models.ForeignKey('ZeoMasters.TaxCode', **BR)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    net = models.DecimalField(default=0, **MONEY)
    tax_amount = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)

    class Meta:
        ordering = ['quotation_id', 'order']


SO_STATUS = (('confirmed', 'Confirmed'), ('progress', 'In progress'), ('delivered', 'Delivered'), ('invoiced', 'Invoiced'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))


class SalesOrder(DocBase):
    quotation = models.ForeignKey(Quotation, **BR, verbose_name='from quotation')
    delivery_date = models.DateField('promised delivery', null=True, blank=True)
    status = models.CharField(max_length=10, choices=SO_STATUS, default='confirmed')
    delivery_ref = models.CharField('delivery note / project ref.', max_length=60, blank=True)
    invoice_ref = models.CharField('invoice ref.', max_length=60, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return self.number


class SalesOrderLine(models.Model):
    sales_order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    order = models.PositiveSmallIntegerField(default=1)
    product = models.ForeignKey('ZeoMasters.Product', **BR)
    description = models.CharField(max_length=300, blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    uom = models.CharField(max_length=10, blank=True)
    unit_price = models.DecimalField(default=0, **MONEY)
    discount_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax = models.ForeignKey('ZeoMasters.TaxCode', **BR)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    net = models.DecimalField(default=0, **MONEY)
    tax_amount = models.DecimalField(default=0, **MONEY)
    total = models.DecimalField(default=0, **MONEY)
    delivered_qty = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ['sales_order_id', 'order']


class SalesTarget(models.Model):
    """Target per agent or territory per period (month / quarter / year). Attainment = won opportunity value (or sales orders) in the period."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    name = models.CharField(max_length=100)
    owner = models.ForeignKey(EMP, **BR, verbose_name='agent')
    territory = models.ForeignKey(Territory, **BR)
    period_start = models.DateField('from')
    period_end = models.DateField('to')
    measure = models.CharField(max_length=12, default='won_value', choices=(('won_value', 'Won opportunity value'), ('won_count', 'Won opportunities'),
                                                                            ('order_value', 'Sales order value'), ('new_leads', 'New leads')))
    target = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-period_start', 'name']

    def __str__(self):
        return self.name


# ------------------------------------------------------------------ v1.67.27 industry fields and industry reports
FIELD_TYPES = (('text', 'Text'), ('textarea', 'Long text'), ('number', 'Number'), ('money', 'Amount'), ('date', 'Date'), ('choice', 'Pick list'),
               ('yesno', 'Yes / no'))


class IndustryField(PackItem):
    """A field the company adds to leads, opportunities, quotations and sales orders – per industry, no programming.
    Values are kept in the record's `extra` (JSON) under `code`; the same code on a lead, its opportunity and quotation is carried over."""
    code = models.CharField(max_length=40, unique=True, help_text='Key of the value: small letters, digits and _ (e.g. dc_rack_qty). Do not change it once used')
    label = models.CharField(max_length=100)
    industry = models.CharField(max_length=30, blank=True, help_text='Industry pack the field belongs to; empty = every industry')
    field_type = models.CharField('type', max_length=10, choices=FIELD_TYPES, default='text')
    choices = models.TextField('pick-list values', blank=True, help_text='One value per line (type Pick list)')
    unit = models.CharField(max_length=20, blank=True, help_text='Shown after the label, e.g. kW, sq ft, MT')
    section = models.CharField(max_length=60, blank=True, help_text='Heading the field is shown under on the form')
    order = models.PositiveSmallIntegerField(default=10)
    on_lead = models.BooleanField('on leads', default=True)
    on_opportunity = models.BooleanField('on opportunities', default=True)
    on_quotation = models.BooleanField('on quotations', default=False)
    on_order = models.BooleanField('on sales orders', default=False)
    on_enquiry = models.BooleanField('on enquiries', default=True)   # v1.67.28
    required = models.BooleanField('always required', default=False)
    required_from = models.CharField('required from stage', max_length=40, blank=True,
                                     help_text='Stage code (e.g. DC_QUOTE): from this stage on – and to mark the deal won – the opportunity needs the field')
    in_list = models.BooleanField('show in lists', default=False)
    in_filter = models.BooleanField('filter lists by it', default=False)
    on_pdf = models.BooleanField('print on quotations', default=False)
    help = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ['industry', 'order', 'id']

    def __str__(self):
        return self.label

    def choice_list(self):
        return [x.strip() for x in (self.choices or '').replace('|', '\n').splitlines() if x.strip()]


REPORT_SOURCES = (('lead', 'Leads'), ('opportunity', 'Opportunities'), ('quotation', 'Quotations'))
REPORT_STATUS = (('all', 'All'), ('open', 'Open only'), ('won', 'Won / accepted only'), ('lost', 'Lost / rejected only'))
REPORT_DATES = (('created', 'Created in the period'), ('expected_close', 'Expected close in the period'), ('closed', 'Closed in the period'), ('none', 'No period'))


class IndustryReport(PackItem):
    """An analysis report of one industry, in the report centre (Reports › CRM – industry) and, when marked, on the CRM home."""
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    industry = models.CharField(max_length=30, blank=True, help_text='Only the records of this industry; empty = every record')
    description = models.TextField(blank=True)
    source = models.CharField('records', max_length=12, choices=REPORT_SOURCES, default='opportunity')
    group_by = models.CharField(max_length=60, default='stage',
                                help_text='stage, pipeline, agent, source, status, territory, campaign, account, month, or field:<field code>')
    measure = models.CharField('add up', max_length=60, blank=True, help_text='field:<field code> of a number / amount field – its total per group')
    status = models.CharField('records', max_length=6, choices=REPORT_STATUS, default='all')
    date_field = models.CharField('period on', max_length=16, choices=REPORT_DATES, default='created')
    on_dashboard = models.BooleanField('show on the CRM home', default=False)
    order = models.PositiveSmallIntegerField(default=10)

    class Meta:
        ordering = ['industry', 'order', 'id']

    def __str__(self):
        return self.name


# ================================================================== v1.67.28 general CRM (release 2)
BRN = dict(null=True, blank=True, on_delete=models.SET_NULL)

class CustomerProfile(models.Model):
    """CRM view of an account: risk, segment, status (blacklisting needs a manager), preferences and the figures worked out daily
    (customer 360°: lifetime value, relationship score, churn risk)."""
    account = models.OneToOneField('ZeoMasters.Account', on_delete=models.CASCADE, related_name='crm_profile')
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    category = models.CharField('customer category', max_length=12, default='standard',
                                choices=(('key', 'Key account'), ('standard', 'Standard'), ('small', 'Small'), ('government', 'Government'), ('partner', 'Partner')))
    segment = models.CharField(max_length=2, blank=True, choices=(('A', 'A'), ('B', 'B'), ('C', 'C')), help_text='Worked out from the lifetime value (top 20 % = A)')
    risk = models.CharField('risk classification', max_length=6, default='low', choices=(('low', 'Low'), ('medium', 'Medium'), ('high', 'High')))
    status = models.CharField(max_length=12, default='active', choices=(('active', 'Active'), ('inactive', 'Inactive'), ('blocked', 'Blocked'),
                                                                        ('blacklisted', 'Blacklisted')))
    block_reason = models.CharField(max_length=200, blank=True)
    blocked_by = models.ForeignKey(USER, **BRN, related_name='+')
    blocked_at = models.DateTimeField(null=True, blank=True)
    account_manager = models.ForeignKey(EMP, **BRN, related_name='+')
    preferences = models.TextField(blank=True, help_text='How and when the customer likes to be contacted, language, delivery preferences')
    lifetime_value = models.DecimalField(default=0, **MONEY)
    won_count = models.PositiveIntegerField(default=0)
    open_value = models.DecimalField('open pipeline', default=0, **MONEY)
    last_won_at = models.DateField(null=True, blank=True)
    last_activity_at = models.DateField(null=True, blank=True)
    open_complaints = models.PositiveSmallIntegerField(default=0)
    active_contracts = models.PositiveSmallIntegerField(default=0)
    relationship_score = models.PositiveSmallIntegerField(default=0, help_text='0–100: recency, frequency, value, contracts, complaints')
    satisfaction = models.DecimalField('satisfaction (1–5)', max_digits=3, decimal_places=2, null=True, blank=True)
    churn_risk = models.CharField(max_length=6, default='low', choices=(('low', 'Low'), ('medium', 'Medium'), ('high', 'High')))
    churn_reason = models.CharField(max_length=200, blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    churn_probability = models.PositiveSmallIntegerField('AI churn probability %', null=True, blank=True)   # v1.67.30
    sentiment = models.CharField('AI customer sentiment', max_length=8, blank=True, choices=(('positive', 'Positive'), ('neutral', 'Neutral'), ('negative', 'Negative')))

    def __str__(self):
        return str(self.account)


class CustomerSite(models.Model):
    """Branches, locations, billing and delivery addresses of a customer."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    account = models.ForeignKey('ZeoMasters.Account', on_delete=models.CASCADE, related_name='crm_sites')
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, default='branch', choices=(('branch', 'Branch / location'), ('billing', 'Billing address'), ('delivery', 'Delivery address'),
                                                                     ('site', 'Project / service site'), ('hq', 'Head office')))
    address = models.TextField(blank=True)
    city = models.CharField(max_length=60, blank=True)
    country = models.CharField(max_length=60, blank=True)
    contact = models.ForeignKey(Contact, **BR)
    phone = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['account', 'name']

    def __str__(self):
        return f'{self.account} – {self.name}'


ENQ_STATUS = (('draft', 'Draft'), ('evaluation', 'Technical evaluation'), ('clarification', 'Clarification asked'), ('approval', 'Waiting for approval'),
              ('approved', 'Approved'), ('quoted', 'Quoted'), ('closed', 'Closed'), ('cancelled', 'Cancelled'))


class Enquiry(models.Model):
    """A customer requirement before the quotation: products / services, specifications, site visit, technical evaluation, approval."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    date = models.DateField()
    title = models.CharField(max_length=150)
    account = models.ForeignKey('ZeoMasters.Account', **BRN, verbose_name='customer (account)', related_name='enquiries')
    contact = models.ForeignKey(Contact, **BR)
    lead = models.ForeignKey(Lead, **BRN, related_name='enquiries')
    opportunity = models.ForeignKey(Opportunity, **BRN, related_name='enquiries')
    classification = models.CharField(max_length=12, default='product', choices=(('product', 'Product'), ('service', 'Service'), ('project', 'Project'),
                                                                                  ('rental', 'Rental'), ('subscription', 'Subscription'), ('other', 'Other')))
    items = models.JSONField('products / services', default=list, blank=True)
    requirement = models.TextField('requirement description', blank=True)
    technical_specs = models.TextField('technical specifications', blank=True)
    quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    estimated_value = models.DecimalField(default=0, **MONEY)
    budget = models.DecimalField('customer budget', null=True, blank=True, **MONEY)
    expected_date = models.DateField('expected delivery / completion', null=True, blank=True)
    site_visit = models.BooleanField('site visit needed', default=False)
    site_visit_date = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(EMP, **BRN, verbose_name='sales person', related_name='+')
    technical_owner = models.ForeignKey(EMP, **BRN, verbose_name='technical / presales person', related_name='+')
    evaluation = models.CharField('technical evaluation', max_length=16, default='pending',
                                  choices=(('pending', 'Pending'), ('feasible', 'Feasible'), ('feasible_changes', 'Feasible with changes'), ('not_feasible', 'Not feasible')))
    evaluation_notes = models.TextField(blank=True)
    clarification = models.TextField('clarifications', blank=True)
    revision = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=14, choices=ENQ_STATUS, default='draft')
    approved_by = models.ForeignKey(USER, **BRN, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    quotation = models.ForeignKey('Quotation', **BRN, related_name='+')
    close_reason = models.CharField(max_length=200, blank=True)
    industry = models.CharField(max_length=30, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.number or self.title


class OnboardingTemplate(PackItem):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    industry = models.CharField(max_length=30, blank=True, help_text='Used for won deals of this industry; empty = every industry')
    items = models.TextField('checklist', help_text='One step per line')
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class Onboarding(models.Model):
    """Customer onboarding after a won deal: checklist, KYC, contract, credit, activation, training, welcome, handover."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    account = models.ForeignKey('ZeoMasters.Account', **BRN, related_name='onboardings')
    contact = models.ForeignKey(Contact, **BR)
    opportunity = models.ForeignKey(Opportunity, **BRN, related_name='onboardings')
    sales_order = models.ForeignKey('SalesOrder', **BRN, related_name='+')
    template = models.ForeignKey(OnboardingTemplate, **BR)
    owner = models.ForeignKey(EMP, **BRN, verbose_name='account manager', related_name='+')
    start = models.DateField()
    target_date = models.DateField('to finish by', null=True, blank=True)
    checklist = models.JSONField(default=list, blank=True)
    kyc_done = models.BooleanField('KYC documents received', default=False)
    contract_uploaded = models.BooleanField('contract / agreement uploaded', default=False)
    credit_approved = models.BooleanField(default=False)
    account_activated = models.BooleanField('customer account activated', default=False)
    service_activated = models.BooleanField('product / service activated', default=False)
    handover_done = models.BooleanField('implementation handover done', default=False)
    training_done = models.BooleanField('customer training done', default=False)
    welcome_sent = models.BooleanField('welcome message sent', default=False)
    status = models.CharField(max_length=12, default='open', choices=(('open', 'Open'), ('in_progress', 'In progress'), ('completed', 'Completed'), ('cancelled', 'Cancelled')))
    completed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


CONTRACT_KINDS = (('sales', 'Sales contract'), ('service', 'Service contract'), ('amc', 'AMC'), ('subscription', 'Subscription'), ('sla', 'SLA agreement'),
                  ('rental', 'Rental'), ('framework', 'Framework / rate agreement'), ('nda', 'NDA'), ('other', 'Other'))
CONTRACT_STATUS = (('draft', 'Draft'), ('approval', 'Waiting for approval'), ('active', 'Active'), ('expired', 'Expired'), ('renewed', 'Renewed'),
                   ('superseded', 'Replaced by a revision'), ('terminated', 'Terminated'), ('cancelled', 'Cancelled'))


class Contract(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=150)
    kind = models.CharField('contract type', max_length=12, choices=CONTRACT_KINDS, default='service')
    account = models.ForeignKey('ZeoMasters.Account', on_delete=models.PROTECT, related_name='crm_contracts', verbose_name='customer (account)')
    contact = models.ForeignKey(Contact, **BR)
    opportunity = models.ForeignKey(Opportunity, **BRN, related_name='contracts')
    sales_order = models.ForeignKey('SalesOrder', **BRN, related_name='+')
    quotation = models.ForeignKey('Quotation', **BRN, related_name='+')
    owner = models.ForeignKey(EMP, **BRN, verbose_name='account manager', related_name='+')
    value = models.DecimalField('contract value', default=0, **MONEY)
    currency = models.CharField(max_length=3, default='AED')
    billing = models.CharField(max_length=10, default='once', choices=(('once', 'One-off'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')))
    start = models.DateField()
    end = models.DateField(null=True, blank=True)
    auto_renew = models.BooleanField(default=False)
    notice_days = models.PositiveSmallIntegerField('renewal notice (days)', default=60)
    payment_terms = models.TextField('payment conditions', blank=True)
    sla_terms = models.TextField('service-level terms', blank=True)
    terms = models.TextField('terms and conditions', blank=True)
    status = models.CharField(max_length=12, choices=CONTRACT_STATUS, default='draft')
    revision = models.PositiveSmallIntegerField(default=1)
    previous = models.ForeignKey('self', **BRN, related_name='next_versions', verbose_name='previous version / contract')
    approved_by = models.ForeignKey(USER, **BRN, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    renewal_opportunity = models.ForeignKey(Opportunity, **BRN, related_name='+')
    performance_notes = models.TextField('contract performance', blank=True)
    end_reason = models.CharField('termination / cancellation reason', max_length=200, blank=True)
    reminded = models.JSONField(default=list, blank=True, help_text='Expiry reminders already sent (days before the end)')
    industry = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.number} {self.title}'


COMPLAINT_CATS = (('product', 'Product'), ('service', 'Service'), ('delivery', 'Delivery'), ('billing', 'Billing'), ('warranty', 'Warranty'), ('staff', 'Staff / behaviour'),
                  ('other', 'Other'))
COMPLAINT_STATUS = (('new', 'New'), ('assigned', 'Assigned'), ('investigating', 'Investigating'), ('resolved', 'Resolved'), ('closed', 'Closed'),
                    ('cancelled', 'Cancelled'))


class Complaint(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    subject = models.CharField(max_length=200)
    account = models.ForeignKey('ZeoMasters.Account', **BRN, related_name='crm_complaints', verbose_name='customer (account)')
    contact = models.ForeignKey(Contact, **BRN, related_name='complaints')
    sales_order = models.ForeignKey('SalesOrder', **BRN, related_name='+')
    contract = models.ForeignKey(Contract, **BRN, related_name='complaints')
    category = models.CharField(max_length=10, choices=COMPLAINT_CATS, default='service')
    priority = models.CharField(max_length=6, default='medium', choices=(('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent')))
    channel = models.CharField(max_length=10, default='phone', choices=(('phone', 'Phone'), ('email', 'E-mail'), ('whatsapp', 'WhatsApp'), ('portal', 'Portal / web'),
                                                                       ('visit', 'Visit'), ('other', 'Other')))
    description = models.TextField(blank=True)
    owner = models.ForeignKey(EMP, **BRN, verbose_name='assigned to', related_name='+')
    due_at = models.DateTimeField('resolve by (SLA)', null=True, blank=True)
    status = models.CharField(max_length=14, choices=COMPLAINT_STATUS, default='new')
    investigation = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    escalated = models.PositiveSmallIntegerField('escalation level', default=0)
    sla_breached = models.BooleanField(default=False)
    rating = models.PositiveSmallIntegerField('customer rating (1–5)', null=True, blank=True)
    feedback = models.TextField('customer feedback', blank=True)
    confirmed = models.BooleanField('customer confirmed the closure', default=False)
    maintenance_ref = models.CharField('work order (Maintenance)', max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.number} {self.subject}'


class CommissionPlan(PackItem):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=100)
    basis = models.CharField(max_length=12, default='won_value', choices=(('won_value', 'Won opportunity value'), ('order_value', 'Sales order value (net)'),
                                                                         ('accepted', 'Accepted quotation value (net)')))
    rate = models.DecimalField('rate %', max_digits=6, decimal_places=3, default=0)
    agent = models.ForeignKey(EMP, **BRN, verbose_name='only for agent', related_name='+')
    territory = models.ForeignKey(Territory, **BR, verbose_name='only for territory')
    min_amount = models.DecimalField('only deals of at least', null=True, blank=True, **MONEY)
    target_bonus_pct = models.DecimalField('extra % above 100 % target', max_digits=6, decimal_places=3, default=0)

    def __str__(self):
        return self.name


class Commission(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    plan = models.ForeignKey(CommissionPlan, on_delete=models.PROTECT, related_name='commissions')
    agent = models.ForeignKey(EMP, on_delete=models.PROTECT, related_name='+')
    source = models.CharField(max_length=12, default='opportunity')
    source_id = models.IntegerField()
    source_ref = models.CharField('for', max_length=80, blank=True)
    date = models.DateField()
    basis_amount = models.DecimalField(default=0, **MONEY)
    rate = models.DecimalField('rate %', max_digits=6, decimal_places=3, default=0)
    amount = models.DecimalField(default=0, **MONEY)
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'To approve'), ('approved', 'Approved'), ('paid', 'Paid'), ('cancelled', 'Cancelled')))
    approved_by = models.ForeignKey(USER, **BRN, related_name='+')
    paid_ref = models.CharField('paid with (payroll / voucher ref.)', max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']
        unique_together = (('plan', 'source', 'source_id'),)


DOC_KINDS = (('customer', 'Customer document'), ('enquiry', 'Enquiry attachment'), ('specification', 'Technical specification'), ('drawing', 'Drawing / design'),
             ('quotation', 'Quotation'), ('proposal', 'Proposal'), ('contract', 'Contract'), ('po', 'Purchase order'), ('correspondence', 'Correspondence'),
             ('catalogue', 'Product catalogue'), ('price_list', 'Price list'), ('kyc', 'KYC document'), ('other', 'Other'))


class CRMDocument(models.Model):
    """A controlled document: versions, approval, expiry. The file itself is attached to the record (Files)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BR, help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    title = models.CharField(max_length=150)
    kind = models.CharField('document type', max_length=14, choices=DOC_KINDS, default='other')
    account = models.ForeignKey('ZeoMasters.Account', **BRN, related_name='crm_documents')
    opportunity = models.ForeignKey(Opportunity, **BRN, related_name='documents')
    enquiry = models.ForeignKey(Enquiry, **BRN, related_name='documents')
    contract = models.ForeignKey(Contract, **BRN, related_name='documents')
    version = models.PositiveSmallIntegerField(default=1)
    previous = models.ForeignKey('self', **BRN, related_name='+', verbose_name='previous version')
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'Draft'), ('approval', 'Waiting for approval'), ('approved', 'Approved'),
                                                                      ('superseded', 'Replaced by a new version'), ('expired', 'Expired')))
    approved_by = models.ForeignKey(USER, **BRN, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    owner = models.ForeignKey(EMP, **BRN, related_name='+')
    notes = models.TextField(blank=True)
    reminded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class QuotationTemplate(PackItem):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    quote_kind = models.CharField('type', max_length=12, default='product', choices=(('product', 'Products'), ('service', 'Services'), ('project', 'Project'),
                                                                                    ('subscription', 'Recurring subscription')))
    industry = models.CharField(max_length=30, blank=True)
    billing = models.CharField(max_length=10, default='once', choices=(('once', 'One-off'), ('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')))
    validity_days = models.PositiveSmallIntegerField(null=True, blank=True)
    notes = models.TextField('introduction / notes to the customer', blank=True)
    scope_of_work = models.TextField(blank=True)
    payment_terms_text = models.TextField('payment terms', blank=True)
    delivery_terms = models.TextField(blank=True)
    terms = models.TextField('terms and conditions', blank=True)
    items = models.JSONField('standard lines', default=list, blank=True)

    def __str__(self):
        return self.name


# ================================================================== v1.67.29 communication and marketing (release 3)
CHANNELS = (('email', 'E-mail'), ('whatsapp', 'WhatsApp'), ('sms', 'SMS'))


class MessageTemplate(PackItem):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    channel = models.CharField(max_length=10, choices=CHANNELS, default='email')
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(help_text='Placeholders: {name} {first_name} {company} {agent} {agent_phone} {number} {title} {value} {campaign} {unsubscribe}')
    use_for = models.CharField('used for', max_length=12, default='any', choices=(('any', 'Anything'), ('lead', 'Leads'), ('contact', 'Contacts'), ('opportunity', 'Opportunities'),
                                                                                ('quotation', 'Quotations'), ('campaign', 'Campaigns'), ('complaint', 'Complaints')))

    def __str__(self):
        return self.name


class Segment(models.Model):
    """A target audience: contacts or leads chosen by rules. Only people who agreed to marketing and did not opt out are messaged."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRN, related_name='+', help_text='Branch of the record; empty = every branch')
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    target = models.CharField(max_length=8, default='contacts', choices=(('contacts', 'Contacts'), ('leads', 'Leads')))
    contact_type = models.CharField(max_length=12, blank=True, help_text='Contacts: prospect, customer, tenant, owner …')
    tags = models.CharField('tag', max_length=60, blank=True, help_text='Contacts with this tag')
    source = models.ForeignKey(Source, **BRN, related_name='+')
    territory = models.ForeignKey(Territory, **BRN, related_name='+')
    campaign = models.ForeignKey(Campaign, **BRN, related_name='+', verbose_name='leads of campaign')
    industry = models.CharField(max_length=30, blank=True)
    city = models.CharField('city contains', max_length=60, blank=True)
    lead_status = models.CharField(max_length=12, blank=True, help_text='Leads: new, working, nurture, qualified …')
    min_score = models.SmallIntegerField('lead score at least', null=True, blank=True)
    customers_only = models.BooleanField('only customers with a won deal', default=False)
    created_from = models.DateField('created on or after', null=True, blank=True)
    consent_only = models.BooleanField('only people who agreed to marketing', default=True)
    size = models.PositiveIntegerField('people (last count)', default=0)
    counted_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CampaignSend(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRN, related_name='+', help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name='sends')
    segment = models.ForeignKey(Segment, on_delete=models.PROTECT, related_name='sends')
    channel = models.CharField(max_length=10, choices=CHANNELS, default='email')
    template = models.ForeignKey(MessageTemplate, **BRN, related_name='+')
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True, help_text='Empty = the template. Placeholders as in the templates')
    status = models.CharField(max_length=10, default='draft', choices=(('draft', 'Draft'), ('scheduled', 'Scheduled'), ('sending', 'Sending'), ('sent', 'Sent'),
                                                                      ('cancelled', 'Cancelled')))
    scheduled_at = models.DateTimeField('send at', null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    total = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField('skipped (no consent / opted out / no address)', default=0)
    opened = models.PositiveIntegerField(default=0)
    clicked = models.PositiveIntegerField(default=0)
    unsubscribed = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']


class CampaignRecipient(models.Model):
    send = models.ForeignKey(CampaignSend, on_delete=models.CASCADE, related_name='recipients')
    contact = models.ForeignKey(Contact, **BRN, related_name='+')
    lead = models.ForeignKey(Lead, **BRN, related_name='+')
    name = models.CharField(max_length=150, blank=True)
    to = models.CharField(max_length=150)
    status = models.CharField(max_length=10, default='queued', choices=(('queued', 'Queued'), ('sent', 'Sent'), ('failed', 'Failed'), ('skipped', 'Skipped')))
    token = models.CharField(max_length=40, unique=True)
    error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']


class Conversation(models.Model):
    """One thread with a person on WhatsApp, SMS or e-mail – the CRM inbox. Assigned to an agent."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRN, related_name='+', help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    channel = models.CharField(max_length=10, choices=CHANNELS, default='whatsapp')
    address = models.CharField('phone / e-mail', max_length=150, db_index=True)
    name = models.CharField(max_length=150, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    contact = models.ForeignKey(Contact, **BRN, related_name='conversations')
    lead = models.ForeignKey(Lead, **BRN, related_name='conversations')
    opportunity = models.ForeignKey(Opportunity, **BRN, related_name='conversations')
    owner = models.ForeignKey(EMP, **BRN, related_name='+', verbose_name='assigned to')
    status = models.CharField(max_length=8, default='open', choices=(('open', 'Open'), ('waiting', 'Waiting for the customer'), ('closed', 'Closed')))
    unread = models.PositiveSmallIntegerField(default=0)
    last_message = models.CharField(max_length=200, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-last_message_at', '-id']

    def __str__(self):
        return f'{self.number} {self.name or self.address}'


class ConversationMessage(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    direction = models.CharField(max_length=3, choices=(('in', 'From the customer'), ('out', 'From us')))
    text = models.TextField()
    at = models.DateTimeField(auto_now_add=True)
    by = models.ForeignKey(USER, **BRN, related_name='+')
    status = models.CharField(max_length=10, default='received', choices=(('received', 'Received'), ('sent', 'Sent'), ('failed', 'Failed')))
    result = models.CharField(max_length=200, blank=True)
    external_id = models.CharField(max_length=120, blank=True, db_index=True)

    class Meta:
        ordering = ['at', 'id']


CONNECTOR_KINDS = (('meta', 'Facebook / Instagram lead ads (Meta)'), ('google', 'Google Ads lead forms'), ('linkedin', 'LinkedIn lead gen forms'),
                   ('whatsapp', 'WhatsApp inbox (incoming messages)'), ('telephony', 'Telephony – call logging'), ('webform', 'Website form / landing page'))


class LeadConnector(models.Model):
    """A channel that brings leads or messages in: its web-hook address carries a secret key."""
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=CONNECTOR_KINDS)
    key = models.CharField(max_length=40, unique=True, blank=True, help_text='Made by ZEO – part of the web-hook address')
    verify_token = models.CharField('verify token (Meta / WhatsApp)', max_length=80, blank=True)
    token = models.CharField('access token / key (stored encrypted)', max_length=500, blank=True, help_text='Meta page token, Google key … – kept encrypted, never shown again')
    secret = models.TextField(blank=True)
    secret_last4 = models.CharField(max_length=4, blank=True)
    campaign = models.ForeignKey(Campaign, **BRN, related_name='+', verbose_name='campaign of the leads')
    source = models.ForeignKey(Source, **BRN, related_name='+', verbose_name='lead source')
    owner = models.ForeignKey(EMP, **BRN, related_name='+', verbose_name='give leads / chats to (empty = assignment rules)')
    industry = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    received = models.PositiveIntegerField(default=0)
    last_received_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class MailInbox(models.Model):
    """An e-mail box read by ZEO (IMAP): e-mails from contacts / leads become activities and conversations; others become leads."""
    name = models.CharField(max_length=100)
    host = models.CharField('IMAP server', max_length=120)
    port = models.PositiveIntegerField(default=993)
    use_ssl = models.BooleanField(default=True)
    username = models.CharField(max_length=150)
    password = models.CharField('password (stored encrypted)', max_length=200, blank=True)
    secret = models.TextField(blank=True)
    folder = models.CharField(max_length=60, default='INBOX')
    create_leads = models.BooleanField('unknown senders become leads', default=True)
    owner = models.ForeignKey(EMP, **BRN, related_name='+', verbose_name='give new leads to (empty = assignment rules)')
    last_uid = models.PositiveIntegerField(default=0)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_result = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# ================================================================== v1.67.30 CRM AI (release 4)
AI_KINDS = (('score', 'Lead scoring'), ('enrich', 'Lead enrichment'), ('qualify', 'Lead qualification'), ('next_action', 'Next action'), ('email', 'E-mail draft'),
            ('summary', 'Meeting / conversation summary'), ('forecast', 'Win forecast'), ('sentiment', 'Customer sentiment'), ('lost', 'Lost deal analysis'),
            ('quote', 'Quotation assistant'), ('upsell', 'Upselling / cross-selling'), ('churn', 'Churn risk'), ('campaign', 'Campaign optimisation'),
            ('coaching', 'Sales coaching'), ('voice', 'Voice note to CRM'), ('industry', 'Industry assistant'))


class AISuggestion(models.Model):
    """A suggestion of the CRM AI. Nothing changes until an authorised person accepts it (scope part 5)."""
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', **BRN, related_name='+', help_text='Branch of the record; empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=12, choices=AI_KINDS)
    key = models.CharField(max_length=120, blank=True, db_index=True, help_text='The same suggestion is not made twice while open')
    title = models.CharField(max_length=200)
    text = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True, help_text='What accepting does')
    confidence = models.PositiveSmallIntegerField('confidence %', null=True, blank=True)
    engine = models.CharField(max_length=40, default='built-in')
    lead = models.ForeignKey(Lead, **BRN, related_name='ai_suggestions')
    opportunity = models.ForeignKey(Opportunity, **BRN, related_name='ai_suggestions')
    contact = models.ForeignKey(Contact, **BRN, related_name='ai_suggestions')
    account = models.ForeignKey('ZeoMasters.Account', **BRN, related_name='crm_ai_suggestions')
    conversation = models.ForeignKey('Conversation', **BRN, related_name='ai_suggestions')
    campaign = models.ForeignKey(Campaign, **BRN, related_name='ai_suggestions')
    owner = models.ForeignKey(EMP, **BRN, related_name='+', verbose_name='for')
    status = models.CharField(max_length=10, default='new', choices=(('new', 'To review'), ('accepted', 'Accepted'), ('dismissed', 'Dismissed')))
    decided_by = models.ForeignKey(USER, **BRN, related_name='+')
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return self.title
