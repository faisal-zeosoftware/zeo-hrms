"""v1.67.26 (pending item 1.62.12, item 234) – mail merge: one letter template, many employees.

HR letters › Mail merge (HR with the right to issue letters): choose a letter template (any type – salary certificate, increment,
a general letter whose text is typed once with placeholders), the employees (by branch / department / category or one by one) and
whether each employee gets theirs by e-mail. ZEO issues one letter per employee – its own reference number, verification code and
PDF, kept with the employee's letters – and gives all of them as one ZIP file.

The compensation cycle uses it for the increment letters (with the old / new salary of each employee as extra values).
"""
import io
import logging
import zipfile

from django.apps import apps
from django.db import transaction

from . import letters as L
from .models import LetterRequest

log = logging.getLogger(__name__)
M = apps.get_model
MAX = 500


class MergeError(Exception):
    pass


def _tr(s, *a):
    try:
        from Translations.i18n import tr
        return tr(s, *a)
    except Exception:   # noqa: BLE001
        return s.format(*a)


def employees(ids=None, branch=None, department=None, category=None):
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.filter(is_active=True).select_related('emp_branch_id', 'emp_desgntn_id', 'emp_dept_id', 'emp_nationality')
    if ids:
        qs = qs.filter(pk__in=[int(x) for x in ids])
    if branch:
        qs = qs.filter(emp_branch_id__in=branch)
    if department:
        qs = qs.filter(emp_dept_id__in=department)
    if category:
        qs = qs.filter(emp_ctgry_id__in=category)
    return qs.order_by('emp_code')


def run(letter_type, language, emps, user, purpose='', extras=None, email=False, addressee=''):
    """→ the issued letters (LetterRequest). Each letter in its own transaction: one employee's problem does not stop the others."""
    if letter_type not in L.TYPE_LABEL:
        raise MergeError(_tr('Choose the type of letter.'))
    if language not in ('en', 'ar'):
        raise MergeError(_tr('Choose English or Arabic.'))
    emps = list(emps)
    if not emps:
        raise MergeError(_tr('Choose at least one employee.'))
    if len(emps) > MAX:
        raise MergeError(_tr('At most {0} employees at a time.', MAX))
    if letter_type == 'general' and not (purpose or '').strip():
        raise MergeError(_tr('Write the text of the letter (it goes where the template says {purpose}).'))
    out, failed = [], []
    for e in emps:
        try:
            with transaction.atomic():
                lr = LetterRequest(employee_id=e.pk, branch_id=e.emp_branch_id_id, letter_type=letter_type, language=language,
                                   purpose=(purpose or '')[:500], addressee=(addressee or '')[:300], status='pending', issued_directly=True,
                                   created_by_id=getattr(user, 'pk', None))
                from . import services as S
                lr.number = S.next_number(LetterRequest, 'LTR')
                lr._extra = (extras or {}).get(e.pk) or {}
                lr.save()
                L.issue(lr, user, _tr('Mail merge'))
                out.append(lr)
        except Exception as ex:   # noqa: BLE001
            msg = getattr(ex, 'message_dict', None) or str(ex)
            failed.append({'employee': e.emp_code, 'error': str(msg)[:200]})
            log.warning('mail merge: %s %s', e.emp_code, ex)
    sent = 0
    if email:
        for lr in out:
            sent += 1 if mail(lr) else 0
    return out, failed, sent


def mail(lr):
    """E-mail the letter's PDF to the employee (company e-mail, else personal). Best effort."""
    try:
        e = M('EmpManagement', 'emp_master').objects.get(pk=lr.employee_id)
        to = e.emp_company_email or e.emp_personal_email
        if not to:
            return False
        from django.core.mail import EmailMessage
        from ZeoAdmin.schedules import mail_connection
        conn, sender = mail_connection()
        tpl = L.template_for(lr.letter_type, lr.language, lr.branch_id)
        name = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
        msg = EmailMessage(subject=f'{tpl.title if tpl else "Letter"} – {lr.reference_no}',
                           body=_tr('Dear {0},\n\nPlease find your letter attached (reference {1}).\n\n{2}', name, lr.reference_no, L.company_name()),
                           to=[to], connection=conn, from_email=sender)
        msg.attach(f'{lr.reference_no}.pdf', lr.pdf.open('rb').read(), 'application/pdf')
        msg.send(fail_silently=False)
        return True
    except Exception:   # noqa: BLE001
        log.exception('mail merge e-mail of %s', lr.pk)
        return False


def zip_of(letter_ids):
    E = M('EmpManagement', 'emp_master')
    names = {e.pk: e.emp_code for e in E.objects.filter(pk__in=LetterRequest.objects.filter(pk__in=letter_ids).values('employee_id'))}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for lr in LetterRequest.objects.filter(pk__in=letter_ids, status='issued').exclude(pdf=''):
            try:
                z.writestr(f'{names.get(lr.employee_id, lr.employee_id)}_{lr.reference_no}.pdf', lr.pdf.open('rb').read())
            except Exception:   # noqa: BLE001
                log.exception('zip letter %s', lr.pk)
    return buf.getvalue()
