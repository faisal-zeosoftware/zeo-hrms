"""v1.67.26 (pending item 1.62.12, item 233) – candidate pre-onboarding portal.

HR (offer page › Pre-onboarding) sends an accepted offer's candidate a private link: /welcome/<company>/<token> (no login).
The candidate fills the details HR needs before the first day and uploads the documents, then submits. HR reviews: accept or
return with a note (the candidate corrects and submits again). When the offer becomes an employee (Create employee), the accepted
answers go to the employee record: date of birth, gender, marital status, personal e-mail and mobile, address, the salary account
(bank details), the emergency contact, and the uploaded files as the employee's documents (passport, visa, Emirates ID with their
numbers and expiry dates).

HR API (rights of offers):  GET/POST recruitment/api/offers/<id>/preonboarding/   (POST: send / send again – {message, documents, days})
                            POST recruitment/api/offers/<id>/preonboarding/review/ {decision: accept|return, note}
                            GET  recruitment/api/preonboarding-files/<file id>/    (download)
Public API (token):         GET/POST recruitment/api/welcome/<token>/?schema=<company>  (POST {details, submit: true|false})
                            POST recruitment/api/welcome/<token>/upload/?schema=…  (multipart: key, file)
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import secrets
from datetime import date, timedelta

from django.db.models import Q
from django.db import transaction
from django.utils import timezone

from .models import Offer, PreOnboarding, PreOnboardingFile


class PreError(Exception):
    def __init__(self, msg, status=400, field=None):
        super().__init__(msg)
        self.status, self.field = status, field


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


DEFAULT_DOCS = [
    {'key': 'passport', 'label': 'Passport (photo page)', 'required': True},
    {'key': 'photo', 'label': 'Passport-size photo (white background)', 'required': True},
    {'key': 'education', 'label': 'Highest education certificate', 'required': True},
    {'key': 'visa', 'label': 'Current UAE visa (if you are in the UAE)', 'required': False},
    {'key': 'emirates_id', 'label': 'Emirates ID (front and back, if you have one)', 'required': False},
    {'key': 'experience', 'label': 'Experience letters of previous employers', 'required': False},
]
# the fields of the form: key, label, kind, required
FIELDS = [
    ('date_of_birth', 'Date of birth', 'date', True), ('gender', 'Gender', 'choice:M=Male|F=Female', True),
    ('marital_status', 'Marital status', 'choice:single=Single|married=Married|divorced=Divorced|widowed=Widowed', True),
    ('personal_email', 'Personal e-mail', 'email', True), ('mobile', 'Mobile number', 'phone', True),
    ('home_address', 'Home-country address', 'text', True), ('uae_address', 'Address in the UAE (if any)', 'text', False),
    ('passport_no', 'Passport number', 'text', True), ('passport_issue', 'Passport issue date', 'date', True),
    ('passport_expiry', 'Passport expiry date', 'date', True), ('emirates_id', 'Emirates ID number (if any)', 'text', False),
    ('emergency_name', 'Emergency contact – name', 'text', True), ('emergency_relation', 'Emergency contact – relation', 'text', True),
    ('emergency_phone', 'Emergency contact – phone', 'phone', True),
    ('bank_name', 'Bank of your salary account (if you have one in the UAE)', 'text', False), ('iban', 'IBAN of that account', 'iban', False),
]
REQ = [k for k, _l, _t, r in FIELDS if r]
MAX_FILE = 8 * 1024 * 1024
EXT = ('.pdf', '.jpg', '.jpeg', '.png')


def link(pre):
    from django.db import connection
    from .careers import app_url
    return f'{app_url()}/welcome/{connection.schema_name}/{pre.token}'


def send(offer, user, message='', documents=None, days=21):
    """Make (or renew) the link and e-mail it to the candidate. Only an accepted offer that is not an employee yet."""
    if offer.status not in ('accepted',) or offer.employee_id:
        raise PreError(_tr('Pre-onboarding is for an accepted offer that is not an employee yet.'), 409)
    cand = offer.application.candidate
    if not cand.email:
        raise PreError(_tr('The candidate has no e-mail address – add it on the candidate first.'), 400, 'email')
    try:
        days = int(days or 21)
    except (TypeError, ValueError):
        days = 0
    if not 3 <= days <= 90:
        raise PreError(_tr('Days the link stays open: a whole number from 3 to 90.'), 400, 'days')
    docs = documents if isinstance(documents, list) and documents else DEFAULT_DOCS
    clean = []
    for d in docs:
        if not isinstance(d, dict) or not str(d.get('label') or '').strip():
            raise PreError(_tr('Each document needs a name.'), 400, 'documents')
        k = str(d.get('key') or d['label']).strip().lower().replace(' ', '_')[:40]
        clean.append({'key': k, 'label': str(d['label']).strip()[:120], 'required': bool(d.get('required'))})
    with transaction.atomic():
        pre, made = PreOnboarding.objects.get_or_create(offer=offer, defaults={'token': secrets.token_urlsafe(32), 'created_by': user})
        if not made and pre.status in ('accepted', 'closed'):
            raise PreError(_tr('HR already accepted these details.'), 409)
        if not made:
            pre.token = secrets.token_urlsafe(32)   # a new link: the old one stops working
        pre.message = (message or '').strip()[:2000]
        pre.documents = clean
        pre.expires_on = _zt_today() + timedelta(days=days)
        pre.sent_at = timezone.now()
        if pre.status not in ('submitted', 'returned'):
            pre.status = 'sent'
        pre.save()
    from .careers import send_mail, company
    co = getattr(company(), 'name', '') or ''
    body = _tr('Dear {0},\n\nWelcome to {1}! Before your first day ({2}), please fill in your details and upload your documents here:\n{3}\n\n'
               'The link is personal and stays open until {4}.\n\n{5}', cand.first_name, co, offer.joining_date.strftime('%d/%m/%Y'), link(pre),
               pre.expires_on.strftime('%d/%m/%Y'), pre.message)
    sent = send_mail(cand.email, _tr('Welcome to {0} – your details before joining', co), body)
    return pre, bool(sent)


def hr_view(pre):
    if pre is None:
        return {'status': None}
    return {'id': pre.pk, 'status': pre.status, 'status_label': dict(PreOnboarding.STATUS).get(pre.status, pre.status), 'link': link(pre),
            'expires_on': pre.expires_on, 'sent_at': pre.sent_at, 'opened_at': pre.opened_at, 'submitted_at': pre.submitted_at,
            'reviewed_at': pre.reviewed_at, 'applied_at': pre.applied_at, 'hr_note': pre.hr_note, 'message': pre.message,
            'details': pre.details, 'fields': [{'key': k, 'label': l, 'kind': t, 'required': r} for k, l, t, r in FIELDS],
            'documents': [{**d, 'files': [{'id': f.pk, 'name': f.name, 'uploaded_at': f.uploaded_at} for f in pre.files.filter(key=d['key'])]}
                          for d in pre.documents],
            'missing': missing(pre), 'warnings': _warnings(pre)}


def _warnings(pre):
    c = iban_clash(pre) if pre.status in ('submitted', 'accepted') else None
    return [_tr('The IBAN {0} is already the salary account of {1}.', c[0], c[1])] if c else []


def iban_clash(pre):
    """(iban, 'Name (code)') when the IBAN the candidate typed is already another employee's salary account, else None."""
    iban = ((pre.details or {}).get('iban') or '').replace(' ', '').upper()
    if not iban:
        return None
    from django.apps import apps
    BD = apps.get_model('EmpManagement', 'EmployeeBankDetail')
    b = BD.objects.filter(Q(iban_number__iexact=iban) | Q(account_number=iban) | Q(account_number=iban[-13:])).exclude(
        employee_id=pre.offer.employee_id or 0).select_related('employee').first()
    if b is None:
        return None
    e = b.employee
    return iban, f'{e.emp_first_name or ""} {e.emp_last_name or ""} ({e.emp_code})'.replace('  ', ' ').strip()


def review(pre, decision, note, user):
    if pre.status != 'submitted':
        raise PreError(_tr('Only submitted details can be reviewed.'), 409)
    if decision == 'return':
        if not (note or '').strip():
            raise PreError(_tr('Say what the candidate should correct.'), 400, 'note')
        pre.status, pre.hr_note = 'returned', note.strip()[:500]
        from .careers import send_mail
        cand = pre.offer.application.candidate
        send_mail(cand.email, _tr('Please correct your details'), _tr('Dear {0},\n\nHR asks you to correct your details: {1}\n\n{2}', cand.first_name, pre.hr_note, link(pre)))
    elif decision == 'accept':
        clash = iban_clash(pre)
        if clash:   # v1.67.26: the salary account must be the new joiner's own – before, it was skipped silently when the employee was made
            raise PreError(_tr('The IBAN {0} is already the salary account of {1}. Return the details to the candidate to check it.', clash[0], clash[1]), 409, 'iban')
        pre.status, pre.hr_note = 'accepted', ''
    else:
        raise PreError(_tr('Decision: accept or return.'), 400, 'decision')
    pre.reviewed_at, pre.reviewed_by = timezone.now(), user
    pre.save()
    if pre.status == 'accepted' and pre.offer.employee_id:
        apply_to_employee(pre)
    return pre


# ------------------------------------------------------------------ the candidate's side
def by_token(token):
    pre = PreOnboarding.objects.select_related('offer__application__candidate', 'offer__branch').filter(token=token).first()
    if pre is None or pre.status == 'closed':
        raise PreError(_tr('This link is not valid. Ask HR for a new one.'), 404)
    if pre.expires_on and pre.expires_on < _zt_today() and pre.status in ('sent', 'opened', 'returned'):
        raise PreError(_tr('This link has expired. Ask HR for a new one.'), 410)
    return pre


def missing(pre):
    out = [k for k in REQ if not str((pre.details or {}).get(k) or '').strip()]
    have = set(pre.files.values_list('key', flat=True))
    out += [d['key'] for d in pre.documents if d.get('required') and d['key'] not in have]
    return out


def public_view(pre):
    if pre.status == 'sent':
        pre.status, pre.opened_at = 'opened', timezone.now()
        pre.save(update_fields=['status', 'opened_at'])
    o, c = pre.offer, pre.offer.application.candidate
    from .careers import company
    co = company()
    return {'company': getattr(co, 'name', ''), 'candidate': c.full_name, 'position': o.position_title, 'joining_date': o.joining_date,
            'branch': getattr(o.branch, 'branch_name', '') if o.branch_id else '', 'status': pre.status,
            'editable': pre.status in ('sent', 'opened', 'returned'), 'message': pre.message, 'hr_note': pre.hr_note if pre.status == 'returned' else '',
            'expires_on': pre.expires_on, 'details': {**_prefill(c), **(pre.details or {})},
            'fields': [{'key': k, 'label': l, 'kind': t, 'required': r} for k, l, t, r in FIELDS],
            'documents': [{**d, 'files': [{'id': f.pk, 'name': f.name} for f in pre.files.filter(key=d['key'])]} for d in pre.documents],
            'missing': missing(pre)}


def _prefill(c):
    return {k: v for k, v in {'date_of_birth': c.date_of_birth.isoformat() if c.date_of_birth else '', 'gender': c.gender if c.gender in ('M', 'F') else '',
                               'personal_email': c.email or '', 'mobile': c.phone or ''}.items() if v}


def _check(k, v):
    import re
    kind = next((t for kk, _l, t, _r in FIELDS if kk == k), None)
    if kind is None:
        raise PreError(_tr('Unknown field {0}.', k), 400, k)
    v = str(v or '').strip()
    if not v:
        return ''
    label = next(l for kk, l, _t, _r in FIELDS if kk == k)
    if kind == 'date':
        try:
            d = date.fromisoformat(v[:10])
        except ValueError:
            raise PreError(_tr('{0}: enter a date.', _tr(label)), 400, k)
        if k == 'date_of_birth' and not (date(1940, 1, 1) <= d <= _zt_today() - timedelta(days=18 * 365)):
            raise PreError(_tr('{0}: you must be at least 18 years old.', _tr(label)), 400, k)
        if k == 'passport_expiry' and d <= _zt_today():
            raise PreError(_tr('{0}: the passport has expired – renew it before joining.', _tr(label)), 400, k)
        if k == 'passport_issue' and d > _zt_today():
            raise PreError(_tr('{0}: the date is in the future.', _tr(label)), 400, k)
        return d.isoformat()
    if kind.startswith('choice:'):
        ok = [x.split('=')[0] for x in kind[7:].split('|')]
        if v not in ok:
            raise PreError(_tr('{0}: choose one of the list.', _tr(label)), 400, k)
        return v
    if kind == 'email' and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', v):
        raise PreError(_tr('{0}: enter a valid e-mail address.', _tr(label)), 400, k)
    if kind == 'phone' and not re.fullmatch(r'\+?[0-9 ()-]{7,20}', v):
        raise PreError(_tr('{0}: enter a phone number (digits, +, spaces).', _tr(label)), 400, k)
    if kind == 'iban':
        iban = v.replace(' ', '').upper()
        if not re.fullmatch(r'AE\d{21}', iban) or int(''.join(str(int(ch, 36)) for ch in iban[4:] + iban[:4])) % 97 != 1:
            raise PreError(_tr('{0}: a UAE IBAN has 23 characters (AE and 21 digits) – check it.', _tr(label)), 400, k)
        return iban
    if k == 'emirates_id' and not re.fullmatch(r'784-?\d{4}-?\d{7}-?\d', v):
        raise PreError(_tr('{0}: 784-YYYY-NNNNNNN-N.', _tr(label)), 400, k)
    return v[:300]


def save_details(pre, data, submit=False):
    if pre.status not in ('sent', 'opened', 'returned'):
        raise PreError(_tr('Your details were sent to HR – they cannot be changed now.'), 409)
    det = dict(pre.details or {})
    for k, v in (data or {}).items():
        det[k] = _check(k, v)
    if det.get('passport_issue') and det.get('passport_expiry') and det['passport_issue'] >= det['passport_expiry']:
        raise PreError(_tr('Passport expiry date: after the issue date.'), 400, 'passport_expiry')
    pre.details = det
    pre.save(update_fields=['details'])
    if submit:
        miss = missing(pre)
        if miss:
            labels = {k: l for k, l, _t, _r in FIELDS}
            labels.update({d['key']: d['label'] for d in pre.documents})
            raise PreError(_tr('Still missing: {0}.', ', '.join(_tr(labels.get(m, m)) for m in miss)), 400, miss[0])
        pre.status, pre.submitted_at, pre.hr_note = 'submitted', timezone.now(), ''
        pre.save(update_fields=['status', 'submitted_at', 'hr_note'])
        try:
            from .services import notify
            o = pre.offer
            notify(user=o.created_by, title=_tr('Pre-onboarding submitted'), notification_type='recruitment',
                   message=_tr('{0} sent the details before joining – review them on the offer {1}.', o.application.candidate.full_name, o.document_number))
        except Exception:   # noqa: BLE001
            pass
    return pre


def upload(pre, key, f):
    if pre.status not in ('sent', 'opened', 'returned'):
        raise PreError(_tr('Your details were sent to HR – files cannot be added now.'), 409)
    doc = next((d for d in pre.documents if d['key'] == key), None)
    if doc is None:
        raise PreError(_tr('Unknown document.'), 400, 'key')
    if f is None:
        raise PreError(_tr('Choose a file.'), 400, 'file')
    name = str(getattr(f, 'name', '') or '')
    if not name.lower().endswith(EXT):
        raise PreError(_tr('A PDF, JPG or PNG file, please.'), 400, 'file')
    if getattr(f, 'size', 0) > MAX_FILE:
        raise PreError(_tr('The file is larger than 8 MB.'), 400, 'file')
    head = f.read(8)
    f.seek(0)
    if not (head.startswith(b'%PDF') or head.startswith(b'\xff\xd8') or head.startswith(b'\x89PNG')):
        raise PreError(_tr('The file is not a real PDF, JPG or PNG.'), 400, 'file')
    return PreOnboardingFile.objects.create(pre=pre, key=key, label=doc['label'], file=f, name=name[:200])


def remove_file(pre, file_id):
    if pre.status not in ('sent', 'opened', 'returned'):
        raise PreError(_tr('Your details were sent to HR – files cannot be removed now.'), 409)
    n, _ = PreOnboardingFile.objects.filter(pre=pre, pk=file_id).delete()
    if not n:
        raise PreError(_tr('Not found.'), 404)


# ------------------------------------------------------------------ to the employee record
DOC_TYPES = {'passport': 'Passport', 'visa': 'Residence Visa', 'emirates_id': 'Emirates ID', 'education': 'Education certificate',
             'photo': 'Photo', 'experience': 'Experience letter'}


def apply_to_employee(pre):
    """The accepted details go to the employee made from the offer (fields that are empty there – HR's own entries win)."""
    emp = pre.offer.employee
    if emp is None or pre.status != 'accepted' or pre.applied_at:
        return False
    from django.apps import apps
    M = apps.get_model
    d = pre.details or {}
    upd = []
    for fld, key, conv in (('emp_date_of_birth', 'date_of_birth', lambda v: date.fromisoformat(v)), ('emp_gender', 'gender', str),
                           ('emp_marital_status', 'marital_status', lambda v: {'single': 'S', 'married': 'M', 'divorced': 'D', 'widowed': 'W'}[v]), ('emp_personal_email', 'personal_email', str),
                           ('emp_mobile_number_1', 'mobile', str), ('emp_permenent_address', 'home_address', str), ('emp_present_address', 'uae_address', str)):
        if d.get(key) and hasattr(emp, fld) and not getattr(emp, fld, None):
            try:
                setattr(emp, fld, conv(d[key]))
                upd.append(fld)
            except Exception:   # noqa: BLE001
                pass
    if upd:
        type(emp).objects.filter(pk=emp.pk).update(**{f: getattr(emp, f) for f in upd})
    if d.get('iban'):
        BD = M('EmpManagement', 'EmployeeBankDetail')
        if not BD.objects.filter(employee=emp).exists():
            try:
                iban = d['iban'].replace(' ', '').upper()
                BD.objects.create(employee=emp, bank_name=d.get('bank_name') or '', iban_number=iban, account_number=iban, is_active=True)   # v1.67.26: the whole IBAN (unique) – the last 13 digits could clash
            except Exception:   # noqa: BLE001
                import logging
                logging.getLogger(__name__).exception('bank details from pre-onboarding')
    if d.get('emergency_name'):
        EC = M('EmployeeProfile', 'EmergencyContact')
        if not EC.objects.filter(employee_id=emp.pk).exists():
            EC.objects.create(employee_id=emp.pk, name=d['emergency_name'][:120], relation=(d.get('emergency_relation') or '')[:40],
                              mobile=(d.get('emergency_phone') or '')[:24], is_primary=True)
    DT, ED = M('EmpManagement', 'document_type'), M('EmpManagement', 'Emp_Documents')
    for f in pre.files.all():
        tname = DOC_TYPES.get(f.key, f.label)
        dt = DT.objects.filter(type_name__iexact=tname).first() or DT.objects.create(type_name=tname, description=tname)
        no = {'passport': d.get('passport_no'), 'emirates_id': d.get('emirates_id')}.get(f.key) or ''
        exp = d.get('passport_expiry') if f.key == 'passport' else None
        iss = d.get('passport_issue') if f.key == 'passport' else None
        try:
            from django.core.files.base import ContentFile
            doc = ED(emp_id=emp, document_type=dt, emp_doc_number=no or f'{tname} {f.pk}', emp_doc_issued_date=iss or _zt_today(),
                     emp_doc_expiry_date=exp or (_zt_today() + timedelta(days=3650)))
            doc.emp_doc_document.save(f.name or f'{f.key}.pdf', ContentFile(f.file.open('rb').read()), save=False)
            doc.save()
        except Exception:   # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception('document from pre-onboarding')
    pre.applied_at = timezone.now()
    pre.save(update_fields=['applied_at'])
    return True
