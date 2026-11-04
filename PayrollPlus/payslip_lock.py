"""v1.62.11 (item 230) – password-protected payslips.

When the company switches it on (Payroll › Payroll controls › Payslip password), the payslip PDF that is e-mailed to the employee
and the one the employee downloads in self service is encrypted (AES-256, pypdf – already installed, no outside service). The
password follows one company rule:

    dob             date of birth as DDMMYYYY                                 e.g. 05031990
    code_passport4  employee code + the last 4 characters of the passport    e.g. GD-00057K21
    code_eid4       employee code + the last 4 digits of the Emirates ID      e.g. GD-00054321

A rule never uses a full ID number (passport or Emirates ID): only the last 4 characters, always together with the employee code,
so the password is not a number printed in full on documents the employee hands out. When the data for the rule is missing (no
date of birth, no passport / Emirates ID on the employee) the payslip is NOT sent or given unprotected: the e-mail is skipped (logged)
and the download answers with what HR must complete. HR's own payslip screens (payroll run, payslip page) are not encrypted.
Setting: ZeoApps.CompanyOption 'payslip_password' {enabled, rule, on_email, on_download} – no migration.
"""
import io
import logging
import re
import secrets

from django.apps import apps

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
KEY = 'payslip_password'
RULES = [('dob', 'Date of birth as DDMMYYYY'),
         ('code_passport4', 'Employee code + last 4 characters of the passport number'),
         ('code_eid4', 'Employee code + last 4 digits of the Emirates ID')]
RULE_KEYS = {k for k, _ in RULES}
HINT = {'dob': 'The password is your date of birth as DDMMYYYY (for example 05031990 for 5 March 1990).',
        'code_passport4': 'The password is your employee code followed by the last 4 characters of your passport number (for example GD-00057K21).',
        'code_eid4': 'The password is your employee code followed by the last 4 digits of your Emirates ID (for example GD-00054321).'}
DEFAULT = {'enabled': False, 'rule': 'dob', 'on_email': True, 'on_download': True}


class NoPassword(Exception):
    """The employee lacks the data the rule needs – the payslip must not go out unprotected."""


def settings():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        v = dict(o.value or {}) if o else {}
    except Exception:
        v = {}
    out = {**DEFAULT, **{k: v[k] for k in DEFAULT if k in v}}
    if out['rule'] not in RULE_KEYS:
        out['rule'] = 'dob'
    out['enabled'] = bool(out['enabled'])
    out['on_email'] = bool(out['on_email'])
    out['on_download'] = bool(out['on_download'])
    return out


def save(data):
    """Validates and stores the setting; returns the new setting. Raises ValueError(field, message)."""
    cur = settings()
    if 'rule' in data:
        if data['rule'] not in RULE_KEYS:
            raise ValueError('payslip_password_rule', tr('Choose the password rule.'))
        cur['rule'] = data['rule']
    for k_in, k in (('enabled', 'enabled'), ('on_email', 'on_email'), ('on_download', 'on_download')):
        if k_in in data:
            cur[k] = str(data[k_in]).lower() in ('1', 'true', 'yes', 'on')
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    return cur


def public(s=None):
    s = s or settings()
    return {'payslip_password': s['enabled'], 'payslip_password_rule': s['rule'], 'payslip_password_email': s['on_email'],
            'payslip_password_download': s['on_download'], 'payslip_password_rules': [{'key': k, 'label': tr(v)} for k, v in RULES],
            'payslip_password_hint': tr(HINT[s['rule']])}


def _identity(emp):
    try:
        return M('EmployeeProfile', 'EmployeeIdentity').objects.filter(employee_id=emp.pk).first()
    except LookupError:
        return None


def password_for(emp, rule=None):
    """The payslip password of the employee by the rule; NoPassword(message) when the data is missing."""
    rule = rule or settings()['rule']
    code = re.sub(r'\s+', '', str(emp.emp_code or '')).upper()
    if rule == 'dob':
        dob = getattr(emp, 'emp_date_of_birth', None)
        if not dob:
            raise NoPassword(tr('The date of birth of {0} is missing – the payslip password cannot be made.', emp.emp_code))
        return dob.strftime('%d%m%Y')
    ident = _identity(emp)
    if rule == 'code_passport4':
        p = re.sub(r'[^0-9A-Za-z]', '', getattr(ident, 'passport_no', '') or '').upper()
        if len(p) < 4 or not code:
            raise NoPassword(tr('The passport number of {0} is missing – the payslip password cannot be made.', emp.emp_code))
        return code + p[-4:]
    if rule == 'code_eid4':
        e = re.sub(r'\D', '', getattr(ident, 'emirates_id', '') or '')
        if len(e) < 4 or not code:
            raise NoPassword(tr('The Emirates ID of {0} is missing – the payslip password cannot be made.', emp.emp_code))
        return code + e[-4:]
    raise NoPassword(tr('Choose the password rule.'))


def encrypt(data, password):
    """The PDF bytes encrypted with the user password (AES-256); the owner password is random (nobody can change the file)."""
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(io.BytesIO(data))
    w = PdfWriter(clone_from=r)
    w.encrypt(user_password=password, owner_password=secrets.token_urlsafe(24), algorithm='AES-256')
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def for_email(payslip):
    """True when e-mailed payslips must be protected."""
    s = settings()
    return s['enabled'] and s['on_email']


def for_download(payslip, user=None):
    """True when the payslip the employee downloads must be protected (the employee's own payslip only – HR screens stay plain)."""
    s = settings()
    if not (s['enabled'] and s['on_download']):
        return False
    if user is None:
        return True
    emp_user = getattr(payslip.employee, 'users_id', None)
    return bool(emp_user and emp_user == getattr(user, 'pk', None))


def protect(payslip, data):
    """Encrypted bytes of the payslip PDF for its employee (NoPassword when the rule cannot be applied)."""
    return encrypt(data, password_for(payslip.employee))


def protect_response(payslip, resp):
    """An HttpResponse with the PDF encrypted; a 409 answer with the reason when the password cannot be made."""
    from django.http import HttpResponse, JsonResponse
    try:
        body = resp.content if hasattr(resp, 'content') else b''.join(resp.streaming_content)
        if not body.startswith(b'%PDF'):
            return resp
        enc = protect(payslip, body)
    except NoPassword as e:
        return JsonResponse({'detail': str(e) + ' ' + tr('Ask HR to complete it.')}, status=409)
    out = HttpResponse(enc, content_type='application/pdf')
    for h in ('Content-Disposition',):
        if resp.has_header(h):
            out[h] = resp[h]
    out['X-Payslip-Protected'] = '1'
    return out


def email_note(payslip):
    """The line added to the payslip e-mail when it is protected."""
    return tr(HINT[settings()['rule']])
