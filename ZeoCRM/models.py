"""ZEO CRM (v1.18.0) – contacts, pipelines, opportunities, activities, viewings, offers and listings.
Works alone; with ZEO Property installed and activated it uses Property's units and turns won deals into reservations."""
from django.conf import settings
from django.db import models

from zeo.appkit.models import AppSettingsBase, EventBase, FileBase, PackItem, SequenceBase

USER = settings.AUTH_USER_MODEL
EMP = 'EmpManagement.emp_master'

ROLES = (
    ('crm_manager', 'CRM manager'), ('crm_team_lead', 'Sales team lead'), ('crm_agent', 'Sales agent'), ('crm_viewer', 'CRM viewer (read only)'),
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
                                     ('procurement', 'Procurement'), ('technical', 'Technical'), ('other', 'Other')))
    tags = models.JSONField(default=list, blank=True, help_text='e.g. VIP, Newsletter, Reseller')
    territory = models.ForeignKey('Territory', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    merged_into = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    partner = models.ForeignKey('ZeoMasters.Partner', null=True, blank=True, on_delete=models.SET_NULL, related_name='crm_contacts', verbose_name='business partner')   # v1.41.0

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

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'opportunities'

    def __str__(self):
        return f'{self.number} {self.title}'


ACT_KINDS = (('call', 'Call'), ('email', 'E-mail'), ('message', 'Message / WhatsApp'), ('meeting', 'Meeting'), ('task', 'Task'), ('note', 'Note'))


class Activity(models.Model):
    branch = models.ForeignKey('OrganisationManager.brnch_mstr', null=True, blank=True, on_delete=models.SET_NULL, related_name='+', help_text='v1.22.0: branch of the record – users see the records of their branches (Settings › Branch permissions); empty = every branch')
    number = models.CharField(max_length=20, unique=True, blank=True)
    kind = models.CharField(max_length=8, choices=ACT_KINDS, default='call')
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
              ('interest', 'Looking to (value = rent / buy / service)'), ('meeting', 'Had a meeting / site visit'))


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
                                                                                       ('phone', 'Phone call'), ('import', 'Import'), ('api', 'API')))
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
