"""v1.23.0 – ZEO Cloud: customer accounts, plans, app licences and the registry of where each company lives.

Public schema (SHARED_APPS). A company that is not linked to an account (AccountCompany) has no limits: an own
installation, or a company made before ZEO Cloud, works exactly as before.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

APP_KEYS = ['payroll', 'projects', 'crm', 'property', 'maintenance', 'hse']      # paid apps
FREE_APPS = ['hrms']                                                             # HR: free with any app
HOSTING = [('shared', 'Shared cloud'), ('own', 'Own database'), ('enterprise', 'Own servers')]


class Plan(models.Model):
    code = models.SlugField(max_length=20, unique=True)
    name = models.CharField(max_length=60)
    order = models.PositiveSmallIntegerField(default=0)
    included_companies = models.PositiveIntegerField(default=1)
    max_companies = models.PositiveIntegerField(null=True, blank=True, help_text='Empty = unlimited. Advance: cap with extra companies')
    extra_companies = models.BooleanField(default=False, help_text='Extra companies can be bought above the included ones')
    max_employees = models.PositiveIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    storage_gb = models.PositiveIntegerField(null=True, blank=True)
    group_features = models.BooleanField(default=False)
    hosting = models.CharField(max_length=12, choices=HOSTING, default='shared')
    price_year = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    extra_company_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    app_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Price of one paid app per company per year')
    database_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True,
                                         help_text='v1.26.0 Premium: price per year of each extra own database (no limit; 14-day trial each)')
    active = models.BooleanField(default=True, help_text='Offered on the sign-up page')

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.name


class Account(models.Model):
    STATES = [('trial', 'Trial'), ('active', 'Active'), ('suspended', 'Suspended'), ('cancelled', 'Cancelled')]
    name = models.CharField(max_length=150)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='cloud_accounts')
    owner_email = models.EmailField()
    owner_name = models.CharField(max_length=150, blank=True, default='')
    trn = models.CharField(max_length=20, blank=True, default='')
    country = models.CharField(max_length=60, blank=True, default='United Arab Emirates')
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name='accounts')
    state = models.CharField(max_length=12, choices=STATES, default='trial')
    trial_ends = models.DateField(null=True, blank=True)
    renews_on = models.DateField(null=True, blank=True)
    grace_days = models.PositiveSmallIntegerField(default=15)
    # limits: empty = the plan's
    max_employees = models.PositiveIntegerField(null=True, blank=True)
    max_users = models.PositiveIntegerField(null=True, blank=True)
    storage_gb = models.PositiveIntegerField(null=True, blank=True)
    extra_companies = models.PositiveIntegerField(default=0, help_text='Extra companies paid for (Advance)')
    allowed_email_domain = models.CharField(max_length=120, blank=True, default='', help_text='Only e-mails ending in this domain for new users')
    hosting = models.CharField(max_length=12, choices=HOSTING, default='shared')
    db_alias = models.CharField(max_length=60, blank=True, default='', help_text='Own database (Premium); empty = shared')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def limit(self, name):
        v = getattr(self, name)
        return v if v is not None else getattr(self.plan, name)


class AccountCompany(models.Model):
    """The registry: which account a company belongs to and where its data lives."""
    company = models.OneToOneField('UserManagement.company', on_delete=models.CASCADE, related_name='cloud')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='companies')
    extra = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    db_alias = models.CharField(max_length=60, blank=True, default='', help_text='Database the schema lives in; empty = the main database')
    moving = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.company} → {self.account}'


class AppLicence(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='apps')
    app = models.CharField(max_length=20)
    licensed = models.BooleanField(default=True)
    trial = models.BooleanField(default=False)
    companies = models.PositiveIntegerField(null=True, blank=True, help_text='Number of companies; empty = all')
    users = models.PositiveIntegerField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        unique_together = [('account', 'app')]


class PlatformAdmin(models.Model):
    """Adept staff who may open the control console."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='zeo_platform_admin')
    added_at = models.DateTimeField(auto_now_add=True)


class Invoice(models.Model):
    STATES = [('unpaid', 'Unpaid'), ('paid', 'Paid'), ('void', 'Void')]
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='invoices')
    number = models.CharField(max_length=40)
    issued_on = models.DateField(default=timezone.localdate)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default='AED')
    state = models.CharField(max_length=8, choices=STATES, default='unpaid')
    paid_on = models.DateField(null=True, blank=True)
    odoo_ref = models.CharField(max_length=60, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    # v1.28.0 Odoo link: the invoice in Adept's Odoo, its payment state and the customer's online payment page
    odoo_id = models.IntegerField(null=True, blank=True)
    odoo_state = models.CharField(max_length=20, blank=True, default='')
    pay_url = models.CharField(max_length=500, blank=True, default='')
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-issued_on', '-id']


class AccountEvent(models.Model):
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='events')
    at = models.DateTimeField(auto_now_add=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    kind = models.CharField(max_length=30)
    text = models.CharField(max_length=500)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-at', '-id']


class AccessNotice(models.Model):
    """Shown on the company list: an account removed this person's access."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cloud_notices')
    text = models.CharField(max_length=300)
    at = models.DateTimeField(auto_now_add=True)
    seen = models.BooleanField(default=False)


def today():
    return timezone.localdate()


def plus_days(d, n):
    return d + timedelta(days=n)


# ---------------------------------------------------------------- v1.24.0 own databases
class DatabaseServer(models.Model):
    """A PostgreSQL database that holds companies – the main one ('control') is not listed. v1.26.0: a Premium account may
    have several (Account.db_alias is the default one, where new companies go).

    The password is never stored here: `password_env` names an environment variable; empty = the main database's.
    """
    STATES = [('creating', 'Creating'), ('ready', 'Ready'), ('failed', 'Failed'), ('retired', 'Retired')]
    key = models.SlugField(max_length=40, unique=True, help_text='Connection name, e.g. acc12')
    label = models.CharField(max_length=120, blank=True, default='')
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name='databases')
    host = models.CharField(max_length=200, blank=True, default='', help_text='Empty = the main database server')
    port = models.CharField(max_length=6, blank=True, default='')
    name = models.CharField(max_length=63)
    user = models.CharField(max_length=63, blank=True, default='')
    password_env = models.CharField(max_length=80, blank=True, default='', help_text='Environment variable with the password')
    state = models.CharField(max_length=10, choices=STATES, default='creating')
    note = models.TextField(blank=True, default='')
    # v1.26.0 the first database comes with Premium; every further one is an extra database with its own subscription:
    # a 14-day trial, then paid per year (paid_until) – unpaid after the trial, its companies are read-only
    extra = models.BooleanField(default=False)
    trial_ends = models.DateField(null=True, blank=True)
    paid_until = models.DateField(null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text='Price per year (empty = the plan\'s)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['key']

    def __str__(self):
        return self.key


class MoveJob(models.Model):
    """One company moved between the shared cloud and an own database (either way), with its checks."""
    STATES = [('waiting', 'Waiting'), ('running', 'Running'), ('done', 'Done'), ('failed', 'Failed'), ('rolled_back', 'Moved back')]
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name='moves')
    company = models.ForeignKey('UserManagement.company', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    schema = models.CharField(max_length=63)
    source = models.CharField(max_length=40, help_text="'' = main database")
    target = models.CharField(max_length=40, help_text="'' = main database")
    state = models.CharField(max_length=12, choices=STATES, default='waiting')
    step = models.CharField(max_length=120, blank=True, default='')
    old_copy = models.CharField(max_length=63, blank=True, default='', help_text='Renamed schema kept in the source database')
    keep_until = models.DateField(null=True, blank=True)
    tables = models.PositiveIntegerField(default=0)
    rows = models.BigIntegerField(default=0)
    log = models.TextField(blank=True, default='')
    error = models.TextField(blank=True, default='')
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-id']


# ---------------------------------------------------------------- v1.26.0 invitations of an existing login
class UserInvite(models.Model):
    """One login (e-mail) is shared by every ZEO company. When a company adds an e-mail that already has a login, the
    person is invited instead: the company is added to their login only after they accept (company list)."""
    STATES = [('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined'), ('cancelled', 'Cancelled')]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='zeo_invites')
    company = models.ForeignKey('UserManagement.company', on_delete=models.CASCADE, related_name='+')
    email = models.EmailField(blank=True, default='')
    is_staff = models.BooleanField(default=False)
    is_superuser = models.BooleanField(default=False)
    state = models.CharField(max_length=10, choices=STATES, default='pending')
    note = models.CharField(max_length=300, blank=True, default='')
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-id']

    def __str__(self):
        return f'{self.email} → {self.company} ({self.state})'


class CompanyAccess(models.Model):
    """v1.26.0 – rights of an invited login in one company.
    ZEO keeps one rights record per login (UserTenantPermissions: administrator flag and permission groups, in the shared
    schema and so the same in all its companies). For a login invited from another account (UserInvite) the rights for
    the inviting company are kept here and replace that record while the company is open (ZeoCloud/rights.py) – an
    invitation, and rights given in Users → Assign permissions for an invited login, never change what the person may
    do in their own account."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='zeo_company_access')
    company = models.ForeignKey('UserManagement.company', on_delete=models.CASCADE, related_name='+')
    admin = models.BooleanField(default=False)
    groups = models.JSONField(default=list, blank=True, help_text='Permission group names in this company')
    invite = models.ForeignKey(UserInvite, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('user', 'company')]

    def __str__(self):
        return f'{self.user_id} @ {self.company_id}'
