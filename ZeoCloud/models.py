"""v1.23.0 – ZEO Cloud: customer accounts, plans, app licences and the registry of where each company lives.

Public schema (SHARED_APPS). A company that is not linked to an account (AccountCompany) has no limits: an own
installation, or a company made before ZEO Cloud, works exactly as before.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

APP_KEYS = ['payroll', 'projects', 'crm', 'property', 'maintenance', 'assets', 'hse']      # paid apps
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


# ------------------------------------------------------------------ v1.52.0 sign-in security (public schema: users are shared)
class LoginSecurity(models.Model):
    """Per user: the authenticator app (TOTP, secret encrypted), recovery codes (hashed), wrong-password / wrong-code lock."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='login_security')
    totp_secret = models.TextField(blank=True)
    totp_on = models.BooleanField(default=False)
    totp_since = models.DateTimeField(null=True, blank=True)
    last_step = models.BigIntegerField(default=0, help_text='last accepted 30-second step (a code works once)')
    recovery = models.JSONField(default=list, blank=True, help_text='sha256 of the unused recovery codes')
    failed = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    old_passwords = models.JSONField(default=list, blank=True, help_text='hashes of the last passwords (no reuse)')
    updated_at = models.DateTimeField(auto_now=True)


class LoginSession(models.Model):
    """A sign-in (one per refresh token chain): where, when, last seen; signed out → its tokens are refused."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='login_sessions')
    sid = models.CharField(max_length=32, unique=True)
    ip = models.CharField(max_length=60, blank=True)
    agent = models.CharField(max_length=255, blank=True)
    how = models.CharField(max_length=20, blank=True, help_text='password, e-mail code, authenticator, phone, impersonation')
    impersonator = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    schema = models.CharField(max_length=63, blank=True)
    read_only = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=120, blank=True)
    # v1.62.0 refresh rotation (a used refresh token cannot be used again), device binding, admin sessions, country
    rjti = models.CharField(max_length=64, blank=True, default='', help_text='the one refresh token of the session that may still be used')
    refreshed = models.PositiveIntegerField(default=0)
    rotated_at = models.DateTimeField(null=True, blank=True, help_text='last refresh: older refresh tokens of the session are spent')
    device_hash = models.CharField(max_length=64, blank=True, default='', help_text='sha256 of the device (browser / app) that signed in')
    admin = models.BooleanField(default=False, help_text='a company administrator – the shorter admin session rules apply')
    country = models.CharField(max_length=2, blank=True, default='')
    step_up_at = models.DateTimeField(null=True, blank=True, help_text='password / code asked again at (sensitive actions)')

    class Meta:
        ordering = ['-created_at']


class SecurityPolicy(models.Model):
    """Sign-in rules of a company (schema). A user of several companies gets the strictest rules of them."""
    schema = models.CharField(max_length=63, unique=True)
    min_length = models.PositiveSmallIntegerField(default=8)
    need_upper = models.BooleanField(default=False)
    need_lower = models.BooleanField(default=False)
    need_digit = models.BooleanField(default=False)
    need_symbol = models.BooleanField(default=False)
    expiry_days = models.PositiveSmallIntegerField(default=0, help_text='0 = never')
    history = models.PositiveSmallIntegerField(default=0, help_text='the last N passwords cannot be used again')
    require_2fa = models.CharField(max_length=8, default='off', choices=(('off', 'Not required'), ('admins', 'Administrators'),
                                                                       ('staff', 'Everybody except self-service employees'), ('all', 'Everybody')))
    max_failed = models.PositiveSmallIntegerField(default=5, help_text='wrong passwords / codes in a row before the account is locked')
    lock_minutes = models.PositiveSmallIntegerField(default=15)
    allow_impersonation = models.BooleanField(default=True)
    # v1.61.0 networks and sign-in alerts
    ip_allowlist = models.TextField(blank=True, default='', help_text='one IP address or network (CIDR) per line, e.g. 94.200.10.0/24')
    ip_scope = models.CharField(max_length=6, default='off', choices=(('off', 'Off'), ('staff', 'Staff users (not self-service employees)'),
                                                                      ('all', 'Everybody')))
    alert_new_signin = models.BooleanField(default=True, help_text='e-mail the user when they sign in from a new network and a new device')
    alert_lock = models.BooleanField(default=True, help_text='e-mail the user when wrong passwords lock the account')
    # v1.62.0 sessions
    idle_minutes = models.PositiveIntegerField(default=0, help_text='sign out after this many minutes without use (0 = off)')
    admin_session_hours = models.PositiveIntegerField(default=0, help_text='administrators sign in again after this many hours (0 = like everybody)')
    admin_idle_minutes = models.PositiveIntegerField(default=0, help_text='administrators: sign out after this many idle minutes (0 = like everybody)')
    bind_device = models.BooleanField(default=False, help_text='a sign-in only works on the device that signed in')
    # v1.62.0 countries (needs a country database on the server)
    country_scope = models.CharField(max_length=6, default='off', choices=(('off', 'Off'), ('staff', 'Staff users'), ('all', 'Everybody')))
    country_allow = models.CharField(max_length=400, blank=True, default='', help_text='ISO country codes, e.g. AE, SA, OM')
    # v1.62.0 alerts to administrators
    alert_admins = models.BooleanField(default=True, help_text='e-mail the company administrators about security events')
    # v1.62.0 step-up: the password / authenticator is asked again before sensitive actions
    step_up = models.BooleanField(default=False)
    step_up_minutes = models.PositiveSmallIntegerField(default=10)
    step_up_actions = models.JSONField(default=list, blank=True, help_text='payroll_approve, wps, bank, rights, export (empty = all)')
    # v1.62.0 data protection
    mask_sensitive = models.BooleanField(default=False, help_text='IBAN, Emirates ID, passport and salary shown as ••••1234 without the right')
    export_control = models.BooleanField(default=False, help_text='exports need the right "Can export lists"')
    export_row_limit = models.PositiveIntegerField(default=0, help_text='largest export without the right "Can export large lists" (0 = no limit)')
    export_watermark = models.BooleanField(default=False, help_text='PDFs carry the user and the time')
    bank_two_person = models.BooleanField(default=False, help_text='a bank-detail change by HR needs a second person')
    upload_scan = models.BooleanField(default=True, help_text='scan uploads with ClamAV when it is installed on the server')
    # v1.62.0 giving access
    signin_id = models.CharField(max_length=8, default='optional', choices=(('optional', 'E-mail optional (e-mail or employee code)'),
                                                                           ('email', 'E-mail required')))
    signin_id_categories = models.JSONField(default=dict, blank=True, help_text='{employee category id: "email" | "optional"}')
    link_hours = models.PositiveSmallIntegerField(default=24, help_text='set-password links of Give access work for this many hours')
    hr_types_password = models.BooleanField(default=False, help_text='HR may type a password for an employee')
    default_password_on = models.BooleanField(default=False)
    default_password_hash = models.CharField(max_length=128, blank=True, default='')
    default_password_days = models.PositiveSmallIntegerField(default=7)
    default_password_set_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class SSOProvider(models.Model):
    """v1.54.0 single sign-on: a company's (or group's) identity provider – Microsoft Entra ID, Google Workspace, any OpenID Connect
    provider or SAML 2.0. Users of the e-mail domains sign in there; ZEO trusts the provider only for users whose companies are all
    covered by it. Public schema: users are shared."""
    KINDS = (('microsoft', 'Microsoft Entra ID (Office 365)'), ('google', 'Google Workspace'), ('oidc', 'Other OpenID Connect'),
             ('saml', 'SAML 2.0 (ADFS, Okta, OneLogin …)'), ('ldap', 'Active Directory / LDAP (password checked by the domain controller)'))
    name = models.CharField(max_length=80, help_text='button text, e.g. "Sign in with Microsoft"')
    kind = models.CharField(max_length=10, choices=KINDS)
    enabled = models.BooleanField(default=True)
    domains = models.JSONField(default=list, help_text='e-mail domains that sign in with it, e.g. ["company.ae"]')
    schemas = models.JSONField(default=list, help_text='companies (schemas) whose users may sign in with it')
    # OpenID Connect
    tenant = models.CharField(max_length=120, blank=True, help_text='Microsoft: directory (tenant) ID')
    discovery_url = models.URLField(max_length=300, blank=True, help_text='other OIDC: …/.well-known/openid-configuration')
    client_id = models.CharField(max_length=200, blank=True)
    client_secret = models.TextField(blank=True, help_text='encrypted')
    # SAML 2.0
    idp_entity_id = models.CharField(max_length=300, blank=True)
    idp_sso_url = models.URLField(max_length=500, blank=True)
    idp_cert = models.TextField(blank=True, help_text='the signing certificate of the identity provider (PEM)')
    email_attribute = models.CharField(max_length=200, blank=True, help_text='SAML attribute with the e-mail (empty = NameID)')
    # Active Directory / LDAP (on-premises): the password typed in ZEO is checked by the domain controller
    ldap_url = models.CharField(max_length=300, blank=True, help_text='ldaps://dc1.company.local:636 (several: space between)')
    ldap_start_tls = models.BooleanField(default=False)
    ldap_ca_cert = models.TextField(blank=True, help_text='CA certificate (PEM) of the domain controller, when it is not public')
    ldap_bind_dn = models.CharField(max_length=300, blank=True, help_text='service account, e.g. CN=zeo-svc,OU=Service,DC=company,DC=local or zeo-svc@company.local')
    ldap_bind_password = models.TextField(blank=True, help_text='encrypted')
    ldap_base_dn = models.CharField(max_length=300, blank=True, help_text='DC=company,DC=local')
    ldap_user_filter = models.CharField(max_length=300, blank=True, help_text='empty = (&(objectClass=user)(|(mail={email})(userPrincipalName={email})))')
    ldap_sync = models.BooleanField(default=False, help_text='every night: new users, changed groups, disabled accounts')
    ldap_deactivate_missing = models.BooleanField(default=False, help_text='nightly sync: a ZEO login of the domain that is no longer in the directory is switched off')
    last_sync = models.DateTimeField(null=True, blank=True)
    sync_report = models.JSONField(default=dict, blank=True)
    # provisioning (all kinds): directory groups → ZEO permission groups, SCIM 2.0 from Entra ID / Okta
    group_map = models.JSONField(default=dict, blank=True, help_text='{"directory group name": ZEO permission group id}')
    scim_token_hash = models.CharField(max_length=64, blank=True, db_index=True, help_text='sha256 of the SCIM bearer token')
    scim_token_hint = models.CharField(max_length=8, blank=True)
    # rules
    enforce = models.BooleanField(default=False, help_text='users of the domains cannot sign in with a password')
    create_users = models.BooleanField(default=False, help_text='an employee with this company e-mail and no login gets a self-service login')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_used = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SCIMGroup(models.Model):
    """v1.54.0 a group sent by the identity provider over SCIM (its members get the ZEO permission group mapped to its name)."""
    provider = models.ForeignKey(SSOProvider, on_delete=models.CASCADE, related_name='scim_groups')
    display_name = models.CharField(max_length=200)
    external_id = models.CharField(max_length=200, blank=True)
    members = models.JSONField(default=list, blank=True, help_text='ZEO user ids')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_name']


class SCIMUser(models.Model):
    """v1.54.0 a login provisioned by an identity provider over SCIM (its externalId)."""
    provider = models.ForeignKey(SSOProvider, on_delete=models.CASCADE, related_name='scim_users')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    external_id = models.CharField(max_length=200, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('provider', 'user')]


class SignInAlert(models.Model):
    """v1.61.0 – a sign-in worth a look: a new network and a new device, the account locked by wrong passwords, or a sign-in
    refused because the network is not on the company's allow-list. The user answers "this was me" / "this was not me"."""
    KINDS = (('new_signin', 'Sign-in from a new network and device'), ('locked', 'Locked after wrong passwords'),
             ('blocked_ip', 'Refused: network not allowed'))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='signin_alerts')
    kind = models.CharField(max_length=12, choices=KINDS)
    ip = models.CharField(max_length=60, blank=True)
    device = models.CharField(max_length=80, blank=True)
    agent = models.CharField(max_length=255, blank=True)
    sid = models.CharField(max_length=32, blank=True)
    schema = models.CharField(max_length=63, blank=True)
    emailed = models.BooleanField(default=False)
    status = models.CharField(max_length=8, default='open', choices=(('open', 'Open'), ('was_me', 'It was me'), ('not_me', 'It was not me')))
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class SecurityEvent(models.Model):
    """v1.62.0 – what the security dashboard and the administrators' e-mails are made of."""
    KINDS = (('failed_password', 'Wrong password'), ('failed_code', 'Wrong authenticator code'), ('locked', 'Account locked'),
             ('admin_granted', 'Administrator rights given'), ('rights_changed', 'Permission group changed'),
             ('api_key', 'API key created'), ('sso_changed', 'Single sign-on changed'), ('2fa_reset', 'Authenticator reset'),
             ('export', 'Export'), ('export_refused', 'Export refused'), ('refresh_reuse', 'A used sign-in token came back (possible theft)'),
             ('device_changed', 'A sign-in token was used on another device'), ('password_changed', 'Password changed'),
             ('step_up_failed', 'Wrong password when asked again'), ('country_refused', 'Sign-in refused: country'),
             ('upload_refused', 'Upload refused'), ('access_given', 'Access given'), ('bank_change', 'Bank-detail change'),
             ('history_cleaned', 'History cleaned'), ('failed_burst', 'Many wrong passwords'))
    kind = models.CharField(max_length=20, choices=KINDS, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    schema = models.CharField(max_length=63, blank=True, db_index=True)
    ip = models.CharField(max_length=60, blank=True)
    detail = models.CharField(max_length=400, blank=True)
    data = models.JSONField(default=dict, blank=True)
    emailed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']


class MediaFile(models.Model):
    """v1.62.0 – which company and record an uploaded file belongs to: /media/ opens a file only for a user of that company who
    may open the record (public schema – the media folder is shared by all companies)."""
    path = models.CharField(max_length=500, db_index=True)
    schema = models.CharField(max_length=63)
    model = models.CharField(max_length=120, help_text='app_label.ModelName')
    object_id = models.CharField(max_length=64)
    field = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('path', 'schema', 'model', 'object_id', 'field')]


class SharedRowOwner(models.Model):
    """v1.62.0 isolation – a row of a shared master list (Core: countries, states, currencies, nationalities, religions,
    languages, skills, tax systems – public schema) that a company added from its own screens.

    It is listed only in that company and only that company may change or delete it. Rows without an owner are the
    platform's list: every company sees them and only a platform administrator changes them."""
    model = models.CharField(max_length=80)
    object_id = models.BigIntegerField()
    schema_name = models.CharField(max_length=63, db_index=True)
    created_by = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('model', 'object_id')]
