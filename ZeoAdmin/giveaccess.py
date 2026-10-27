"""v1.62.0 – Give access: logins for employees, with and without e-mail (README-UPDATE 83.5; proposal section 3.3).

Many GCC employees have no e-mail address. HR gives access to one employee or to many at once and chooses how the password is
set – never one shared password that anybody who knows an employee code could use first:

  link      Send set-password link   e-mail users  one-time link by e-mail (company setting, default 24 hours); the employee chooses
  existing  Use existing login       e-mail users  the e-mail already has a ZEO login of another company: this company is added
  generate  Generate password        everybody     a strong random password shown once to HR (copy / print / send); changed at first sign-in
  code      Code by WhatsApp / SMS   no e-mail     a 6-digit code to the mobile on file (the company's own WhatsApp / SMS account);
                                                   the employee opens /activate, types the code and chooses the password
  slip      Printed activation slip  no e-mail     a one-time code + QR code, valid 7 days, handed over and signed for (PDF, English
                                                   and Arabic, a picture guide on the back); a new slip cancels the old one
  kiosk     Kiosk only               no e-mail     no login: the kiosk with employee code and a start PIN that the employee replaces
                                                   with their own at the first kiosk use
  typed     HR types a password      everybody     only when the company switched it on; changed at first sign-in; password rules apply
  default   Company default password self service only when the company switched it on (Sign-in security): never "Admin" or a
                                                   common password, expires after N days unused, the first sign-in asks the last 4
                                                   digits of the Emirates ID and the date of birth, 3 wrong tries lock the account
                                                   until HR unlocks it
  auto      Bulk rule                e-mail link where there is an e-mail, a WhatsApp / SMS code where there is a mobile and
                                     the company's messaging is on, a slip for the rest

Rules for every choice: links, codes, slips and generated passwords work once and expire; a new one cancels the old one;
administrators and HR / payroll users only get the e-mail link (and the authenticator app); an e-mail domain with single sign-on
gets no password (the employee uses the company's Microsoft / Google sign-in); the company's Sign-in ID rule (e-mail required /
optional, per employee category) decides who may sign in without an e-mail; everything is in the audit log.
"""
import hashlib
import io
import logging
import secrets
import string
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from django_tenants.utils import schema_context

from Translations.i18n import tr

log = logging.getLogger(__name__)
U = get_user_model()
SLIP_DAYS = 7
CODE_TRIES = 5
METHODS = ('auto', 'link', 'existing', 'generate', 'code', 'slip', 'kiosk', 'typed', 'default')
NO_EMAIL_OK = ('generate', 'code', 'slip', 'kiosk', 'typed', 'default')


class Refused(Exception):
    pass


def _hash(v):
    return hashlib.sha256(str(v).encode()).hexdigest()


def schema():
    return getattr(connection, 'schema_name', 'public')


def policy():
    from ZeoCloud.models import SecurityPolicy
    sch = schema()
    with schema_context('public'):
        p, _ = SecurityPolicy.objects.get_or_create(schema=sch)
    return p


def emp_model():
    return apps.get_model('EmpManagement', 'emp_master')


def email_of(e):
    return (e.emp_personal_email or e.emp_company_email or '').strip()


def mobile_of(e):
    from Chatter.messaging import employee_phone, settings as msettings
    try:
        s = msettings()
        return employee_phone(e, s.country_code or '971')
    except Exception:
        return ''


def messaging_on():
    try:
        from Chatter.messaging import problems, settings as msettings
        s = msettings()
        return bool(s.enabled and not problems(s))
    except Exception:
        return False


def signin_rule(e, p=None):
    """'email' (e-mail required) or 'optional' for this employee (company rule, per employee category)."""
    p = p or policy()
    cat = str(getattr(e, 'emp_ctgry_id_id', '') or '')
    return (p.signin_id_categories or {}).get(cat) or p.signin_id or 'optional'


def is_staff_login(user):
    """Administrators and HR / payroll users: e-mail link + authenticator only."""
    if user is None:
        return False
    if user.is_superuser or not getattr(user, 'is_ess', False):
        return True
    from AccessControl.access import ctx

    class _R:
        def __init__(s, u):
            s.user, s.GET, s.META, s.path, s.method = u, {}, {}, '', 'GET'
    try:
        c = ctx(_R(user))
        return bool(c.admin or c.codes)
    except Exception:
        return False


def sso_for(email):
    if not email or '@' not in email:
        return None
    try:
        from ZeoCloud import sso
        p = sso.provider_for(email) if hasattr(sso, 'provider_for') else None
        if p is not None and getattr(p, 'enforce', False) and schema() in (p.schemas or []):
            return p
    except Exception:
        log.info('sso lookup', exc_info=True)
    return None


def audit(e, method, detail, by, user_id=None):
    from .models import AccessGrant
    g = AccessGrant.objects.create(employee_id=e.pk, emp_code=e.emp_code or '', user_id=user_id, method=method, detail=detail[:300], by=by)
    try:
        from Chatter.models import RecordLog
        RecordLog.objects.create(model='EmpManagement.emp_master', object_id=str(e.pk), record_label=(e.emp_code or '')[:255], action='updated',
                                 changes={'access': {'label': 'Access', 'old': '', 'new': f'{dict(AccessGrant.METHODS).get(method, method)}: {detail}'[:500]}},
                                 user=by, user_name=((by.get_full_name() or by.username) if by else 'ZEO')[:150])
    except Exception:
        log.exception('access audit line')
    try:
        from ZeoCloud.security import event
        event('access_given', user=U.objects.filter(pk=user_id).first() if user_id else None, by=by, schema=schema(),
              detail=f'{e.emp_code}: {dict(AccessGrant.METHODS).get(method, method)} – {detail}'[:400], notify=False)
    except Exception:
        pass
    return g


def strong_password(n=12):
    """Letters, digits and a symbol; no look-alike characters (O 0 l 1 I)."""
    letters = ''.join(c for c in string.ascii_letters if c not in 'OlI')
    digits = '23456789'
    sym = '#@%+=!'
    pw = [secrets.choice(string.ascii_uppercase.replace('O', '').replace('I', '')), secrets.choice(string.ascii_lowercase.replace('l', '')),
          secrets.choice(digits), secrets.choice(sym)]
    pw += [secrets.choice(letters + digits) for _ in range(n - 4)]
    secrets.SystemRandom().shuffle(pw)
    return ''.join(pw)


def slip_code():
    alpha = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
    raw = ''.join(secrets.choice(alpha) for _ in range(8))
    return f'{raw[:4]}-{raw[4:]}'


# ------------------------------------------------------------------ the login
def ensure_login(e):
    """The employee's self-service login (made without any e-mail – the chosen way follows). Returns the user."""
    if e.users_id:
        u = U.objects.filter(pk=e.users_id).first()
        if u is not None:
            if not u.is_active:
                u.is_active = True
                u.save(update_fields=['is_active'])
            return u
    from . import invites
    E = emp_model()
    if e.is_ess:
        E.objects.filter(pk=e.pk).update(is_ess=False)
    e.is_ess = True
    invites._quiet.on = True
    try:
        with transaction.atomic():
            e.save()
    finally:
        invites._quiet.on = False
    e.refresh_from_db()
    if not e.users_id:
        raise Refused(tr('The login could not be made for {0}.', e.emp_code))
    return U.objects.get(pk=e.users_id)


def _invite(user, e, kind, by, hours, secret=None, code=None, email=''):
    from .models import SignInInvite
    SignInInvite.objects.filter(user_id=user.pk, used_at__isnull=True).update(expires_at=timezone.now())   # a new one cancels the old
    secret = secret or secrets.token_urlsafe(32)
    return SignInInvite.objects.create(user_id=user.pk, employee_id=e.pk, token_hash=_hash(secret), email=email, kind=kind,
                                       code_hash=_hash(code.replace('-', '').upper()) if code else '',
                                       expires_at=timezone.now() + timedelta(hours=hours), by_id=getattr(by, 'pk', None)), secret


def app_url():
    from .invites import app_url as _a
    return _a()


def _name(e):
    return ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x) or e.emp_code


def _cname():
    C = apps.get_model('UserManagement', 'company')
    sch = schema()
    with schema_context('public'):
        return getattr(C.objects.filter(schema_name=sch).first(), 'name', 'ZEO')


# ------------------------------------------------------------------ the ways
def do_link(e, user, by, p):
    email = email_of(e)
    if not email:
        raise Refused(tr('{0} has no e-mail address.', e.emp_code))
    inv, secret = _invite(user, e, 'link', by, p.link_hours or 24, email=email)
    url = f'{app_url()}/set-password?t={schema()}.{secret}'
    body = tr('Dear {0},\n\nYour ZEO login for {1} is ready. Your user name is {2}.\n\nChoose your password here (the link works once, for {3} hours):\n{4}\n\n'
              'Then sign in on the web or in the ZEO mobile app.', _name(e), _cname(), user.username, p.link_hours or 24, url)
    from django.core.mail import send_mail
    send_mail(tr('Your ZEO login – choose your password'), body, getattr(settings, 'DEFAULT_FROM_EMAIL', None), [email], fail_silently=False)
    audit(e, 'link', f'set-password link e-mailed to {email}, valid {p.link_hours or 24} hours', by, user.pk)
    return {'detail': tr('Link sent to {0}.', email)}


def do_existing(e, by):
    email = email_of(e)
    if not email:
        raise Refused(tr('{0} has no e-mail address.', e.emp_code))
    other = U.objects.filter(email__iexact=email, is_active=True).exclude(pk=e.users_id or 0).first()
    if other is None:
        raise Refused(tr('No other ZEO login uses {0}.', email))
    C = apps.get_model('UserManagement', 'company')
    sch = schema()
    with schema_context('public'):
        comp = C.objects.filter(schema_name=sch).first()
        other.tenants.add(comp)
    E = emp_model()
    E.objects.filter(pk=e.pk).update(users=other, is_ess=True)
    audit(e, 'existing', f'the login {other.email} of another company now opens this company too', by, other.pk)
    return {'detail': tr('{0} now signs in with their existing login {1}.', e.emp_code, other.email)}


def _set_pw(user, pw, must_change=True):
    old = user.password
    user.set_password(pw)
    user.must_change_password = must_change
    user.is_active = True
    user.save()
    try:
        from ZeoCloud.models import LoginSecurity
        LoginSecurity.objects.filter(user=user).update(failed=0, locked_until=None)
        from ZeoCloud.security import password_changed
        password_changed(user, old)
    except Exception:
        pass


def do_generate(e, user, by):
    pw = strong_password()
    _set_pw(user, pw)
    audit(e, 'generate', 'a password was generated and shown once to HR', by, user.pk)
    return {'detail': tr('Password generated – give it to the employee; it must be changed at the first sign-in.'), 'password': pw,
            'username': user.username}


def send_code(user, e, by=None, kind='code'):
    """A 6-digit code to the employee's mobile (company WhatsApp / SMS). The message log keeps the text without the code."""
    phone = mobile_of(e)
    if not phone:
        raise Refused(tr('{0} has no mobile number on file.', e.emp_code))
    if not messaging_on():
        raise Refused(tr('WhatsApp / SMS is not set up for the company (Settings › WhatsApp and SMS).'))
    code = f'{secrets.randbelow(1000000):06d}'
    p = policy()
    inv, _ = _invite(user, e, 'code', by, min(24, p.link_hours or 24), code=code)
    from Chatter.messaging import deliver, settings as msettings
    from Chatter.models import MessageLog
    s = msettings()
    text = tr('ZEO {0}: your activation code is {1}. Open {2}/activate, type your user name {3} and the code, then choose your password. '
              'Do not share the code.', _cname(), code, app_url() or 'the ZEO app', user.username)
    try:
        ok, res = deliver(s, phone, text)
    except Exception as ex:
        ok, res = False, str(ex)[:200]
    MessageLog.objects.create(user_id=user.pk, employee_id=e.pk, to=phone, channel=s.channel, kind='activation', text=text.replace(code, '••••••'),
                              status='sent' if ok else 'failed', result=res[:255])
    if not ok:
        raise Refused(tr('The code could not be sent: {0}', res))
    return phone


def do_code(e, user, by):
    phone = send_code(user, e, by)
    audit(e, 'code', f'activation code sent to {phone[:-4] + "••••"}', by, user.pk)
    return {'detail': tr('Code sent to the mobile ending {0}.', phone[-4:])}


def do_slip(e, user, by):
    code = slip_code()
    inv, secret = _invite(user, e, 'slip', by, SLIP_DAYS * 24, code=code)
    audit(e, 'slip', f'activation slip printed (valid {SLIP_DAYS} days)', by, user.pk)
    return {'detail': tr('Slip ready – print it and hand it over.'), 'slip': {'employee_id': e.pk, 'name': _name(e), 'code': e.emp_code,
                                                                             'username': user.username, 'activation': code,
                                                                             'until': (timezone.localtime() + timedelta(days=SLIP_DAYS)).date().isoformat()}}


def do_kiosk(e, by):
    if e.users_id:
        u = U.objects.filter(pk=e.users_id).first()
        if u is not None and is_staff_login(u):
            raise Refused(tr('{0} is an HR / administrator user – kiosk only is for employees without a login.', e.emp_code))
    K = apps.get_model('AttendancePlus', 'EmployeeKey')
    pin = f'{secrets.randbelow(900000) + 100000}'
    from django.contrib.auth.hashers import make_password
    key, _ = K.objects.get_or_create(employee_id=e.pk)
    key.pin_hash, key.pin_must_change, key.failed_pins, key.locked_until = make_password(pin), True, 0, None
    key.save()
    audit(e, 'kiosk', 'kiosk only – a start PIN was printed; the employee chooses their own PIN at the first kiosk use', by)
    return {'detail': tr('Start PIN made – the employee chooses their own PIN at the kiosk.'), 'pin': pin,
            'slip': {'employee_id': e.pk, 'name': _name(e), 'code': e.emp_code, 'username': '', 'kiosk_pin': pin, 'activation': '',
                     'until': ''}}


def do_typed(e, user, by, pw, p):
    if not p.hr_types_password:
        raise Refused(tr('HR may not type passwords in this company (Settings › Sign-in security).'))
    from ZeoCloud.security import password_problems
    prob = password_problems(user, pw)
    if prob:
        raise Refused(tr('The password needs {0}.', ', '.join(prob)))
    _set_pw(user, pw)
    audit(e, 'typed', 'a password typed by HR (must be changed at the first sign-in)', by, user.pk)
    return {'detail': tr('Password set – it must be changed at the first sign-in.')}


def do_default(e, user, by, p):
    if not (p.default_password_on and p.default_password_hash):
        raise Refused(tr('The company default password is not switched on.'))
    if is_staff_login(user):
        raise Refused(tr('HR, payroll and administrator users never get the default password.'))
    user.password = p.default_password_hash
    user.must_change_password = True
    user.is_active = True
    user.save()
    from ZeoCloud.models import LoginSecurity
    LoginSecurity.objects.filter(user=user).update(failed=0, locked_until=None)
    audit(e, 'default', f'the company default password (works {p.default_password_days} days)', by, user.pk)
    return {'detail': tr('The company default password works for {0} days.', p.default_password_days)}


def give(e, method, by, password='', p=None):
    """Give one employee access. Returns {ok, method, detail, ...}; Refused for "missed" (with the reason)."""
    p = p or policy()
    if method not in METHODS:
        raise Refused(tr('Unknown way.'))
    email = email_of(e)
    if method == 'auto':
        method = 'link' if email else ('code' if mobile_of(e) and messaging_on() else 'slip')
    if method == 'existing':
        return {'method': 'existing', **do_existing(e, by)}
    if method == 'kiosk':
        return {'method': 'kiosk', **do_kiosk(e, by)}
    rule = signin_rule(e, p)
    if not email and rule == 'email':
        raise Refused(tr('{0} has no e-mail and the company rule says "e-mail required" for this employee.', e.emp_code))
    if email:
        prov = sso_for(email)
        if prov is not None:
            user = ensure_login(e)
            user.set_unusable_password()
            user.must_change_password = False
            user.save()
            audit(e, 'sso', f'signs in with {prov.name} – no ZEO password', by, user.pk)
            return {'method': 'sso', 'detail': tr('{0} signs in with "{1}" – no password is set.', e.emp_code, prov.name)}
    user = ensure_login(e)
    if is_staff_login(user) and method != 'link':
        raise Refused(tr('{0} is an HR / payroll / administrator user – only the e-mail link (and the authenticator app).', e.emp_code))
    if method == 'link':
        return {'method': 'link', **do_link(e, user, by, p)}
    if method == 'generate':
        return {'method': 'generate', **do_generate(e, user, by)}
    if method == 'code':
        return {'method': 'code', **do_code(e, user, by)}
    if method == 'slip':
        return {'method': 'slip', **do_slip(e, user, by)}
    if method == 'typed':
        return {'method': 'typed', **do_typed(e, user, by, password, p)}
    if method == 'default':
        return {'method': 'default', **do_default(e, user, by, p)}
    raise Refused(tr('Unknown way.'))


def give_many(emp_ids, method, by, password=''):
    E = emp_model()
    p = policy()
    done, missed = [], []
    for e in E.objects.filter(pk__in=emp_ids).order_by('emp_code'):
        try:
            with transaction.atomic():
                r = give(e, method, by, password, p)
            done.append({'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), **r})
        except Refused as ex:
            missed.append({'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), 'why': str(ex)})
            try:
                audit(e, 'missed', str(ex), by)
            except Exception:
                pass
        except Exception as ex:
            log.exception('give access %s', e.emp_code)
            missed.append({'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), 'why': str(ex)[:200]})
    slips = [d['slip'] for d in done if d.get('slip')]
    out = {'done': done, 'missed': missed}
    if slips:
        key = secrets.token_urlsafe(16)
        cache.set(f'zeo:slips:{key}', {'schema': schema(), 'slips': slips, 'by': getattr(by, 'pk', None)}, 900)
        out['slip_pdf'] = key
    return out


# ------------------------------------------------------------------ list
def rows(flt='', q='', limit=500):
    E = emp_model()
    qs = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).select_related('users', 'emp_ctgry_id')
    if q:
        qs = qs.filter(Q(emp_code__icontains=q) | Q(emp_first_name__icontains=q) | Q(emp_last_name__icontains=q))
    if flt == 'no_login':
        qs = qs.filter(users__isnull=True)
    elif flt == 'not_activated':
        qs = qs.filter(Q(users__isnull=True) | Q(users__last_login__isnull=True))
    p = policy()
    from .models import AccessGrant
    last = {}
    for g in AccessGrant.objects.filter(employee_id__in=qs.values('pk')).order_by('at'):
        last[g.employee_id] = g
    K = None
    try:
        K = apps.get_model('AttendancePlus', 'EmployeeKey')
        pins = set(K.objects.filter(employee_id__in=qs.values('pk')).exclude(pin_hash='').values_list('employee_id', flat=True))
    except Exception:
        pins = set()
    mon = messaging_on()
    out = []
    for e in qs.order_by('emp_code')[:limit]:
        u = e.users
        g = last.get(e.pk)
        out.append({'id': e.pk, 'code': e.emp_code, 'name': _name(e), 'email': email_of(e), 'mobile': bool(mobile_of(e)) if mon else bool(e.emp_mobile_number_1),
                    'category': getattr(e.emp_ctgry_id, 'ctgry_title', '') if e.emp_ctgry_id_id else '', 'rule': signin_rule(e, p),
                    'login': u.username if u else '', 'active': bool(u and u.is_active), 'activated': bool(u and u.last_login),
                    'last_login': u.last_login if u else None, 'kiosk_pin': e.pk in pins,
                    'last_way': g.get_method_display() if g else '', 'last_at': g.at if g else None, 'last_detail': g.detail if g else ''})
    return out


# ------------------------------------------------------------------ the slip (PDF)
def slip_pdf(slips):
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas
    from Translations.pdf import fonts, shape
    reg, bold = fonts()
    buf = io.BytesIO()
    w, h = A5
    c = canvas.Canvas(buf, pagesize=A5)
    cname = _cname()
    base = app_url() or ''
    ink, soft = HexColor('#1B1640'), HexColor('#5B6170')
    for s in slips:
        # front
        c.setFillColor(ink)
        c.setFont(bold, 15)
        c.drawString(28, h - 46, 'ZEO – ' + (cname if not any('؀' <= ch <= 'ۿ' for ch in cname) else shape(cname)))
        c.setFont(reg, 10)
        c.setFillColor(soft)
        c.drawString(28, h - 62, 'Your ZEO login' if s.get('username') else 'Your kiosk PIN')
        c.drawRightString(w - 28, h - 62, shape('دخولك إلى زيو') if s.get('username') else shape('الرقم السري للكشك'))
        y = h - 100
        c.setFillColor(ink)
        rows_ = [('Employee', 'الموظف', f'{s["name"]} ({s["code"]})')]
        if s.get('username'):
            rows_ += [('User name', 'اسم المستخدم', s['username']), ('Activation code', 'رمز التفعيل', s['activation']),
                      ('Valid until', 'صالح حتى', s.get('until', ''))]
        else:
            rows_ += [('Start PIN', 'الرقم السري المؤقت', s.get('kiosk_pin', ''))]
        for en, ar, v in rows_:
            c.setFont(reg, 9)
            c.setFillColor(soft)
            c.drawString(28, y, en)
            c.drawRightString(w - 28, y, shape(ar))
            c.setFont(bold, 14 if en in ('Activation code', 'Start PIN') else 11)
            c.setFillColor(ink)
            c.drawString(28, y - 17, str(v))
            y -= 44
        if s.get('username') and base:
            url = f'{base}/activate?u={s["username"]}&c={s["activation"]}'
            qr = QrCodeWidget(url)
            b = qr.getBounds()
            size = 120
            d = Drawing(size, size, transform=[size / (b[2] - b[0]), 0, 0, size / (b[3] - b[1]), 0, 0])
            d.add(qr)
            renderPDF.draw(d, c, w - 28 - size, y - size + 30)
            c.setFont(reg, 8)
            c.setFillColor(soft)
            c.drawString(28, y + 10, f'Open {base}/activate')
        y -= 110
        c.setFont(reg, 9)
        c.setFillColor(soft)
        steps = ([('1. Open the link or scan the QR code.', '١. افتح الرابط أو امسح الرمز.'),
                  ('2. Type your user name and the activation code.', '٢. اكتب اسم المستخدم ورمز التفعيل.'),
                  ('3. Choose your own password. Keep it secret.', '٣. اختر كلمة المرور الخاصة بك واحفظها سراً.')]
                 if s.get('username') else
                 [('1. At the kiosk type your employee code and the start PIN.', '١. في الكشك اكتب رقمك الوظيفي والرقم السري المؤقت.'),
                  ('2. Choose your own PIN (4–6 digits). Keep it secret.', '٢. اختر رقمك السري (٤–٦ أرقام) واحفظه سراً.')])
        for en, ar in steps:
            c.drawString(28, y, en)
            c.drawRightString(w - 28, y - 13, shape(ar))
            y -= 32
        c.setStrokeColor(soft)
        c.line(28, 70, 190, 70)
        c.line(w - 190, 70, w - 28, 70)
        c.setFont(reg, 8)
        c.drawString(28, 58, 'Received by (signature) / ' + shape('استلمه (التوقيع)'))
        c.drawString(w - 190, 58, f'Given by / date  {timezone.localtime():%d %b %Y}')
        c.drawString(28, 36, 'This slip works once. A new slip cancels this one. HR never asks for your password.')
        c.showPage()
        # back – the picture guide
        c.setFillColor(ink)
        c.setFont(bold, 13)
        c.drawString(28, h - 46, 'How to start  /  ' + shape('كيف تبدأ'))
        boxes = [('Scan', 'امسح', 'QR'), ('Type', 'اكتب', '••••-••••'), ('Choose', 'اختر', '******'), ('Done', 'تم', '✓')] if s.get('username') else \
                [('Kiosk', 'الكشك', '[#]'), ('Code + PIN', 'الرقم والرمز', '1234'), ('New PIN', 'رمز جديد', '****'), ('Done', 'تم', '✓')]
        y = h - 120
        for i, (en, ar, icon) in enumerate(boxes):
            x = 28 + (i % 2) * ((w - 56) / 2)
            yy = y - (i // 2) * 170
            c.setStrokeColor(HexColor('#DCDFEA'))
            c.setFillColor(HexColor('#F4F5FA'))
            c.roundRect(x, yy - 130, (w - 56) / 2 - 10, 140, 10, fill=1)
            c.setFillColor(ink)
            c.setFont(bold, 30)
            c.drawCentredString(x + ((w - 56) / 2 - 10) / 2, yy - 50, icon if icon != '✓' else 'OK')
            c.setFont(bold, 12)
            c.drawCentredString(x + ((w - 56) / 2 - 10) / 2, yy - 90, f'{i + 1}. {en}')
            c.setFont(reg, 12)
            c.drawCentredString(x + ((w - 56) / 2 - 10) / 2, yy - 110, shape(ar))
        c.showPage()
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------ the employee's side (/activate, no login)
def _find_user(username):
    username = str(username or '').strip()
    if not username:
        return None
    u = U.objects.filter(username__iexact=username, is_active=True).first()
    return u


def _invites_of(user, kinds):
    """(schema, invite) of the user's open invites of these kinds (the user may be in several companies)."""
    out = []
    for t in user.tenants.all():
        try:
            with schema_context(t.schema_name):
                I = apps.get_model('ZeoAdmin', 'SignInInvite')
                for inv in I.objects.filter(user_id=user.pk, used_at__isnull=True, expires_at__gt=timezone.now(), kind__in=kinds)[:3]:
                    out.append((t.schema_name, inv))
        except Exception:
            continue
    return out


def activate_send(username):
    """The employee asks for a code (first sign-in by WhatsApp / SMS, or forgot password without e-mail). Never tells whether
    the user exists."""
    u = _find_user(username)
    if u is None:
        return
    for t in u.tenants.all():
        with schema_context(t.schema_name):
            E = emp_model()
            e = E.objects.filter(users=u).first()
            if e is None or not mobile_of(e) or not messaging_on():
                continue
            if is_staff_login(u):
                continue
            k = f'zeo:actsend:{u.pk}'
            if cache.get(k):
                return
            cache.set(k, 1, 60)
            try:
                send_code(u, e, None)
                audit(e, 'code', 'activation / reset code sent at the employee\'s request', None, u.pk)
            except Refused:
                pass
            return


def activate(username, code, password):
    """Code from a slip or a message + the new password. Returns the user; ValueError with the reason."""
    u = _find_user(username)
    clean = str(code or '').replace('-', '').replace(' ', '').upper()
    if u is None or not clean:
        raise ValueError(tr('The user name or the code is not correct.'))
    hits = _invites_of(u, ('slip', 'code'))
    ok = None
    for sch, inv in hits:
        if inv.tries >= CODE_TRIES:
            continue
        if secrets.compare_digest(inv.code_hash, _hash(clean)):
            ok = (sch, inv)
            break
    if ok is None:
        for sch, inv in hits:
            with schema_context(sch):
                type(inv).objects.filter(pk=inv.pk).update(tries=inv.tries + 1)
        raise ValueError(tr('The user name or the code is not correct.'))
    from ZeoCloud.security import password_problems
    prob = password_problems(u, password)
    if prob:
        raise ValueError(tr('The password needs {0}.', ', '.join(prob)))
    _set_pw(u, password, must_change=False)
    sch, inv = ok
    with schema_context(sch):
        type(inv).objects.filter(pk=inv.pk).update(used_at=timezone.now())
    return u


# ------------------------------------------------------------------ the company default password at sign-in
def default_state(user):
    """None, or {'schema', 'expired': bool} when the user still has the company default password."""
    if not user or not user.password:
        return None
    from ZeoCloud.models import SecurityPolicy
    for p in SecurityPolicy.objects.filter(schema__in=user.tenants.values('schema_name'), default_password_on=True).exclude(default_password_hash=''):
        if user.password == p.default_password_hash:
            with schema_context(p.schema):
                from .models import AccessGrant
                g = AccessGrant.objects.filter(user_id=user.pk, method='default').order_by('-at').first()
            given = g.at if g else p.default_password_set_at
            expired = bool(given and given < timezone.now() - timedelta(days=p.default_password_days or 7))
            return {'schema': p.schema, 'expired': expired}
    return None


def first_check(user, schema_name, eid_last4, dob):
    """The extra check of the first sign-in with the default password: last 4 digits of the Emirates ID and the date of birth."""
    with schema_context(schema_name):
        e = emp_model().objects.filter(users=user).first()
        if e is None:
            return False
        dob_ok = bool(e.emp_date_of_birth and str(e.emp_date_of_birth) == str(dob or '').strip())
        eid = ''
        try:
            I = apps.get_model('EmployeeProfile', 'EmployeeIdentity')
            eid = (I.objects.filter(employee_id=e.pk).values_list('emirates_id', flat=True).first() or '')
        except Exception:
            pass
        digits = ''.join(ch for ch in eid if ch.isdigit())
        eid_ok = bool(digits) and digits[-4:] == ''.join(ch for ch in str(eid_last4 or '') if ch.isdigit())[-4:]
        if not digits:   # no Emirates ID on file: the date of birth alone
            return dob_ok
        return dob_ok and eid_ok


def email_required(user):
    """True when every company of this self-service user says "e-mail required" for them (they sign in with the e-mail)."""
    if user is None or not getattr(user, 'is_ess', False) or not user.email:
        return False
    from ZeoCloud.models import SecurityPolicy
    pols = {p.schema: p for p in SecurityPolicy.objects.filter(schema__in=user.tenants.values('schema_name'))}
    if not pols:
        return False
    for t in user.tenants.all():
        p = pols.get(t.schema_name)
        if p is None:
            return False
        try:
            with schema_context(t.schema_name):
                e = emp_model().objects.filter(users=user).first()
                if e is None or signin_rule(e, p) != 'email':
                    return False
        except Exception:
            return False
    return True
