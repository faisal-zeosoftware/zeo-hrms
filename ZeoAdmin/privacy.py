"""v1.53.0 – data retention and privacy (roadmap 68): anonymise employees who left, and export all the data about a person.

Anonymise (Settings → Data retention and privacy; or the daily job when *automatic* is on):
  * the employee record keeps its code, branch, department, designation, dates of joining / leaving and the links of the
    payroll, leave and attendance records (kept for the law and the accounts), but loses the name ("Former employee"),
    date of birth, e-mails, phone numbers, addresses, parents' names, blood group, marital status, photo, face template,
    badge / person numbers;
  * family members, bank accounts, qualifications, skills and the extra (form designer) fields are deleted; with
    *delete documents* the documents (passport, visa …) and their files too; with *delete attachments* the photos and files
    on leave, attendance, requests and payslip PDFs;
  * the login of the employee (when it belongs to this company only) is switched off and its name and e-mail removed.
  The change history is paused while this runs, so the removed values are not copied into it.

Export (PDPL / GDPR access request): every record linked to the employee, as one CSV per record type and one JSON file, with
the employee's own files, in a zip.
"""
import csv
import io
import json
import logging
import os
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

PERSONAL_RECORDS = ['EmpManagement.emp_family', 'EmpManagement.Fam_CustomFieldValue', 'EmpManagement.EmployeeBankDetail',
                    'EmpManagement.EmpQualification', 'EmpManagement.Qualification_CustomFieldValue', 'EmpManagement.EmployeeMarketingSkill',
                    'EmpManagement.EmployeeProgramSkill', 'EmpManagement.EmployeeLangSkill', 'EmpManagement.Emp_CustomFieldValue']
DOCUMENT_RECORDS = ['EmpManagement.Doc_CustomFieldValue', 'EmpManagement.Emp_Documents']
EMP_CLEAR = {
    'emp_middle_name': '', 'emp_date_of_birth': None, 'emp_personal_email': '', 'emp_company_email': '', 'emp_mobile_number_1': '',
    'emp_mobile_number_2': '', 'emp_city': '', 'emp_permenent_address': '', 'emp_present_address': '', 'emp_blood_group': '',
    'emp_marital_status': '', 'emp_father_name': '', 'emp_mother_name': '', 'person_id': '', 'barcode_number': '', 'face_encoding': None,
}
# record types that name the employee but are about someone else (not exported)
EXPORT_SKIP_APPS = ('RecruitmentManagement',)
EXPORT_SKIP = {'ZeoAssets.Location', 'ZeoPeople.Reviewer', 'ZeoProjects.TicketFile'}
SECRET_WORDS = ('password', 'secret', 'token', 'otp', 'key_hash', 'face_encoding', 'pin_hash', 'totp')
MAX_FILES_BYTES = 150 * 1024 * 1024


def M(label):
    try:
        return apps.get_model(label)
    except LookupError:
        return None


def policy():
    from .models import RetentionPolicy
    p = RetentionPolicy.objects.order_by('id').first()
    return p or RetentionPolicy.objects.create()


def left_on(emp):
    """The last working day of an employee who left (resignation / end of service), else the day the record was closed."""
    R = M('EmpManagement.EmployeeResignation')
    if R is not None:
        r = R.objects.filter(employee=emp).exclude(status__iexact='rejected').order_by('-id').first()
        if r is not None:
            d = getattr(r, 'last_working_date', None) or getattr(r, 'resigned_on', None)
            if d:
                return d if isinstance(d, date) and not isinstance(d, datetime) else d.date()
    u = getattr(emp, 'updated_at', None)
    return u.date() if u else None


def due(p=None, today=None):
    """Employees who left at least N years ago and are not anonymised yet: [(emp, left_on)]."""
    from .models import AnonymisedEmployee
    p = p or policy()
    today = today or timezone.localdate()
    if not p.anonymise_after_years:
        return []
    E = M('EmpManagement.emp_master')
    done = set(AnonymisedEmployee.objects.values_list('employee_id', flat=True))
    from .legalhold import held_ids   # v1.61.0 held employees are not due
    done |= held_ids()
    out = []
    for e in E.objects.filter(is_active=False).exclude(pk__in=done).order_by('emp_code')[:5000]:
        d = left_on(e)
        if d is None:
            continue
        try:
            limit = d.replace(year=d.year + p.anonymise_after_years)
        except ValueError:   # 29 February
            limit = d.replace(year=d.year + p.anonymise_after_years, day=28)
        if limit <= today:
            out.append((e, d))
    return out


def _file_fields(model):
    return [f for f in model._meta.concrete_fields if f.get_internal_type() in ('FileField', 'ImageField')]


def _delete_file(obj, f):
    v = getattr(obj, f.attname, None)
    if v:
        try:
            getattr(obj, f.name).delete(save=False)
        except Exception:
            log.warning('file of %s %s not deleted', obj._meta.label, obj.pk)
        setattr(obj, f.attname, None if f.null else '')
        return 1
    return 0


def _attachment_models():
    from AccessControl.access import emp_path
    out = []
    for m in apps.get_models():
        if m._meta.label in DOCUMENT_RECORDS or m._meta.label == 'EmpManagement.emp_master':
            continue
        ep = emp_path(m)
        if ep and _file_fields(m):
            out.append((m, ep))
    return out


def anonymise(emp, by=None, p=None, automatic=False):
    """Remove the personal data of one employee who left. Returns what was removed."""
    from Chatter.tracking import paused
    from .models import AnonymisedEmployee
    p = p or policy()
    if emp.is_active:
        raise ValueError('Only employees who left can be anonymised – close the employee first.')
    if AnonymisedEmployee.objects.filter(employee_id=emp.pk).exists():
        raise ValueError('This employee is anonymised already.')
    from .legalhold import active, message   # v1.61.0
    h = active(emp.pk)
    if h is not None:
        raise ValueError(message(h))
    removed = {'fields': [], 'records': {}, 'files': 0, 'login': ''}
    d = left_on(emp)
    with paused(), transaction.atomic():
        fields = {f.name: f for f in emp._meta.concrete_fields}
        for k, v in EMP_CLEAR.items():
            f = fields.get(k)
            if f is None:
                continue
            if v is None and not f.null:
                v = '' if f.get_internal_type() in ('CharField', 'TextField') else getattr(emp, f.attname)
            if getattr(emp, f.attname) not in (None, '') and getattr(emp, f.attname) != v:
                removed['fields'].append(k)
            setattr(emp, f.attname, v)
        emp.emp_first_name = 'Former employee'
        emp.emp_last_name = emp.emp_code or ''
        removed['files'] += _delete_file(emp, fields['emp_profile_pic']) if 'emp_profile_pic' in fields else 0
        changed = {f.attname: getattr(emp, f.attname) for k, f in fields.items() if k in EMP_CLEAR or k in ('emp_first_name', 'emp_last_name', 'emp_profile_pic')}
        type(emp).objects.filter(pk=emp.pk).update(**changed)   # no save signals (user sync, notifications)
        labels = PERSONAL_RECORDS + (DOCUMENT_RECORDS if p.delete_documents else [])
        from AccessControl.access import emp_path
        for label in labels:
            m = M(label)
            if m is None:
                continue
            ep = emp_path(m)
            if not ep:
                continue
            qs = m.objects.filter(**{ep: emp})
            for obj in qs:
                for f in _file_fields(m):
                    removed['files'] += _delete_file(obj, f)
            n = qs.count()
            if n:
                qs.delete()
                removed['records'][str(m._meta.verbose_name_plural)] = n
        if p.delete_attachments:
            for m, ep in _attachment_models():
                ff = _file_fields(m)
                for obj in m.objects.filter(**{ep: emp})[:20000]:
                    n = sum(_delete_file(obj, f) for f in ff)
                    if n:
                        m.objects.filter(pk=obj.pk).update(**{f.attname: getattr(obj, f.attname) for f in ff})   # no signals
                        removed['files'] += n
        u = getattr(emp, 'users', None)
        if u is not None:
            from django.db import connection
            schema = getattr(connection, 'schema_name', '')
            if not u.is_superuser and not u.tenants.exclude(schema_name=schema).exists():
                u.is_active = False
                u.email = f'removed-{u.pk}@removed.invalid'
                u.username = f'removed_{u.pk}'
                for k in ('first_name', 'last_name'):
                    if hasattr(u, k):
                        setattr(u, k, '')
                u.set_unusable_password()
                u.save()
                removed['login'] = 'switched off and removed'
            else:
                removed['login'] = 'kept (the login is used in another company)'
        AnonymisedEmployee.objects.create(employee_id=emp.pk, emp_code=emp.emp_code or '', left_on=d, removed=removed, automatic=automatic, by=by)
    return removed


def run_due(by=None, automatic=True):
    p = policy()
    n, failed = 0, 0
    for e, _d in due(p):
        try:
            anonymise(e, by, p, automatic)
            n += 1
        except Exception:
            failed += 1
            log.exception('anonymise %s', e.pk)
    p.last_run = timezone.now()
    p.last_result = f'{n} anonymised' + (f', {failed} failed' if failed else '')
    p.save(update_fields=['last_run', 'last_result'])
    return p.last_result


def daily():
    p = policy()
    if not (p.automatic and p.anonymise_after_years):
        return 'off'
    return run_due(None, True)


# ------------------------------------------------------------------ export
def _plain(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dict, list, int, float, bool)) or v is None:
        return v
    return str(v)


def export(emp, by=None, self_service=False):
    """(zip bytes, file name, records, files)."""
    from AccessControl.access import emp_path
    from .models import PrivacyExport
    buf = io.BytesIO()
    allj, nrec, nfiles, size = {}, 0, 0, 0
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for m in apps.get_models():
            ep = emp_path(m)
            if ep is None or m._meta.app_label in EXPORT_SKIP_APPS or m._meta.label in EXPORT_SKIP:
                continue
            try:
                qs = m.objects.filter(pk=emp.pk) if ep == '' else m.objects.filter(**{ep: emp})
                objs = list(qs[:5000])
            except Exception:
                log.exception('export %s', m._meta.label)
                continue
            if not objs:
                continue
            cols = [f for f in m._meta.concrete_fields if not any(w in f.name.lower() for w in SECRET_WORDS)]
            rows = []
            for o in objs:
                r = {}
                for f in cols:
                    v = getattr(o, f.attname)
                    if f.is_relation and v is not None:
                        try:
                            r[f.name] = str(getattr(o, f.name))
                        except Exception:
                            r[f.name] = v
                    else:
                        r[f.name] = _plain(v.name if hasattr(v, 'name') and f.get_internal_type() in ('FileField', 'ImageField') else v)
                rows.append(r)
                if '__' not in ep:   # the employee's own files
                    for f in _file_fields(m):
                        fv = getattr(o, f.name, None)
                        if fv and fv.name:
                            try:
                                with fv.open('rb') as fh:
                                    data = fh.read()
                                if size + len(data) <= MAX_FILES_BYTES:
                                    z.writestr(f'files/{m._meta.label}/{o.pk}_{os.path.basename(fv.name)}', data)
                                    size += len(data)
                                    nfiles += 1
                            except Exception:
                                pass
            label = f'{m._meta.label} ({str(m._meta.verbose_name_plural)})'
            allj[label] = rows
            nrec += len(rows)
            s = io.StringIO()
            w = csv.DictWriter(s, fieldnames=[f.name for f in cols])
            w.writeheader()
            for r in rows:
                w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
            z.writestr(f'csv/{m._meta.label}.csv', '﻿' + s.getvalue())
        z.writestr('all-data.json', json.dumps(allj, ensure_ascii=False, indent=1))
        z.writestr('README.txt', (
            f'All the data ZEO HRMS keeps about employee {emp.emp_code}, exported on {timezone.localdate():%d/%m/%Y}.\n\n'
            f'all-data.json – every record, by record type ({nrec} records)\ncsv/ – the same records, one CSV file per record type\n'
            f'files/ – the documents and photos of the employee ({nfiles} files)\n\n'
            'Secret values (passwords, codes, face templates) are never exported. Records about other people that only name the '
            'employee (for example candidates the employee referred) are not included.\n'))
    PrivacyExport.objects.create(employee_id=emp.pk, emp_code=emp.emp_code or '', records=nrec, files=nfiles, by=by, self_service=self_service)
    return buf.getvalue(), f'zeo-data-{emp.emp_code or emp.pk}-{timezone.localdate().isoformat()}.zip', nrec, nfiles
