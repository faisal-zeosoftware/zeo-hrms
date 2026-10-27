"""v1.56.0 – sign-in links: the employee chooses their own password from an e-mail link (setup guide, step 12).

The link carries the company and a random secret (only its hash is stored); it works once and for 7 days.
API (no login): GET /users/sign-in-link/?t=… → who it is for; POST {t, password} → password set, link used.
"""
import hashlib
import logging
import secrets
import threading
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django_tenants.utils import schema_context

from Translations.i18n import tr

log = logging.getLogger(__name__)
DAYS = 7


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def app_url(base_url=''):
    return ((getattr(settings, 'ZEO_APP_URL', '') or base_url or '').rstrip('/'))


def make(user, employee=None, by=None, base_url=''):
    """A new link for the user (earlier unused links of the user stop working). Returns (url, invite)."""
    I = apps.get_model('ZeoAdmin', 'SignInInvite')
    I.objects.filter(user_id=user.pk, used_at__isnull=True).update(expires_at=timezone.now())
    secret = secrets.token_urlsafe(32)
    token = f'{connection.schema_name}.{secret}'
    email = (getattr(employee, 'emp_personal_email', '') or getattr(employee, 'emp_company_email', '') or user.email or '') if employee else (user.email or '')
    inv = I.objects.create(user_id=user.pk, employee_id=getattr(employee, 'pk', None), token_hash=_hash(secret), email=email,
                           expires_at=timezone.now() + timedelta(days=DAYS), by_id=getattr(by, 'pk', None))
    return f'{app_url(base_url)}/set-password?t={token}', inv


def send(user, employee=None, by=None, base_url=''):
    url, inv = make(user, employee, by, base_url)
    if not inv.email:
        return False
    C = apps.get_model('UserManagement', 'company')
    cname = getattr(C.objects.filter(schema_name=connection.schema_name).first(), 'name', 'ZEO')
    name = ' '.join(x for x in [getattr(employee, 'emp_first_name', ''), getattr(employee, 'emp_last_name', '')] if x) or user.username
    body = tr('Dear {0},\n\nYour ZEO login for {1} is ready. Your user name is {2}.\n\nChoose your password here (the link works once, for {3} days):\n{4}\n\n'
              'Then sign in on the web or in the ZEO mobile app.', name, cname, user.username, DAYS, url)
    try:
        send_mail(tr('Your ZEO login – choose your password'), body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [inv.email], fail_silently=False)
    except Exception:
        log.exception('sign-in link to %s', inv.email)
        return False
    return True


_quiet = threading.local()


def welcome(user, employee):
    """Called when a self-service login is made: True when the employee gets (or will get) a link instead of a password."""
    if getattr(_quiet, 'on', False):
        return True
    if not app_url():
        return False
    return send(user, employee)


def invite_employees(by, base_url=''):
    """Links for the employees without a login (a self-service login is made first) and for employees whose login was
    never used. Returns (sent, skipped without e-mail)."""
    E = apps.get_model('EmpManagement', 'emp_master')
    emps = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True))
    sent = skipped = 0
    for e in emps.filter(Q(users__isnull=True) | Q(users__last_login__isnull=True)).select_related('users')[:5000]:
        if not (e.emp_personal_email or e.emp_company_email):
            skipped += 1
            continue
        if e.users_id is None:
            if e.is_ess:   # self service on but no login: switch it off and on so that emp_master.save makes the login
                E.objects.filter(pk=e.pk).update(is_ess=False)
            e.is_ess = True
            _quiet.on = True
            try:
                with transaction.atomic():
                    e.save()   # emp_master.save makes the self-service login (no e-mail: the link follows)
            except Exception:
                log.exception('self-service login for %s', e.emp_code)
            finally:
                _quiet.on = False
            e.refresh_from_db()
        if e.users_id is None:
            skipped += 1
            continue
        if send(e.users, e, by, base_url):
            sent += 1
    return sent, skipped


def _find(token):
    """(schema, invite) for a token, or (None, None)."""
    if not token or '.' not in token:
        return None, None
    schema, secret = token.split('.', 1)
    C = apps.get_model('UserManagement', 'company')
    with schema_context('public'):
        if not C.objects.filter(schema_name=schema).exists():
            return None, None
    with schema_context(schema):
        I = apps.get_model('ZeoAdmin', 'SignInInvite')
        inv = I.objects.filter(token_hash=_hash(secret)).first()
    return schema, inv


def check(token):
    schema, inv = _find(token)
    if inv is None or inv.used_at or inv.expires_at < timezone.now():
        return None
    U = get_user_model()
    u = U.objects.filter(pk=inv.user_id, is_active=True).first()
    return (schema, inv, u) if u else None


def use(token, password):
    hit = check(token)
    if hit is None:
        raise ValueError(tr('This link has expired or was already used – ask HR for a new one.'))
    schema, inv, u = hit
    try:
        from ZeoCloud.security import password_problems
        p = password_problems(u, password)
    except Exception:
        p = [] if len(password or '') >= 8 else ['at least 8 characters']
    if p:
        raise ValueError(tr('The password needs {0}.', ', '.join(p)))
    old = u.password
    u.set_password(password)
    if hasattr(u, 'must_change_password'):
        u.must_change_password = False
    u.save()
    try:
        from ZeoCloud.security import password_changed
        password_changed(u, old)
    except Exception:
        pass
    with schema_context(schema):
        inv.used_at = timezone.now()
        inv.save(update_fields=['used_at'])
    return u
