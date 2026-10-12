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
