"""
v1.13.0 – Employee master services.

* rights helpers (HR in their branches, employee reads own);
* structured identity documents kept in step with EmpManagement.Emp_Documents (expiry alerts / Documents report);
* employee code numbering per branch (select_for_update);
* employment status and probation (confirm / extend / fail, leaving from resignation / end of service);
* history timeline and data completeness;
* contract helpers for the SelfService app: profile_groups(), apply_change(), validate_change().
"""
import logging
import re
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from . import validators as V
from .models import (BankExtra, DependentExtra, EmergencyContact, EmployeeCodeSetting, EmployeeIdentity, EmploymentInfo,
                     ProbationExtension, QualificationExtra)
from .serializers import (BankExtraSerializer, DependentExtraSerializer, EmergencySerializer, IdentitySerializer,
                          QualificationExtraSerializer, emp_label)

log = logging.getLogger(__name__)
M = apps.get_model
UAE_MAX_PROBATION_MONTHS = 6     # Federal Decree-Law 33/2021, Art. 9
PROBATION_NOTICE_DAYS = 14       # employer ending employment during probation gives 14 days' written notice


def Emp():
    return M('EmpManagement', 'emp_master')


# ------------------------------------------------------------------ access
def ctx(request):
    from AccessControl.access import ctx as _ctx
    return _ctx(request)


def has(request, *codes):
    try:
        c = ctx(request)
    except Exception:
        return False
    return c.admin or any(x in c.codes for x in codes)


def is_hr(request, write=False):
    if write:
        return has(request, 'change_emp_master', 'add_emp_master')
    return has(request, 'view_emp_master', 'change_emp_master', 'add_emp_master')


def user_branches(request):
    """None = every branch."""
    try:
        c = ctx(request)
        return None if c.admin else (c.branches or [])
    except Exception:
        return []


def my_employee(request):
    try:
        return ctx(request).emp
    except Exception:
        return None


def visible_emp(request, emp_id, write=False):
    """(employee, None) or (None, (message, http status))."""
    emp = Emp().objects.select_related('emp_branch_id', 'emp_reporting_manager').filter(pk=emp_id).first() if str(emp_id).isdigit() else None
    if emp is None:
        return None, ('Employee not found.', 404)
    if is_hr(request, write=write):
        allowed = user_branches(request)
        if allowed is None or emp.emp_branch_id_id in allowed:
            return emp, None
        return None, ('This employee is in a branch you do not manage.', 403)
    me = my_employee(request)
    if not write and me is not None and me.pk == emp.pk:
        return emp, None
    if write and me is not None and me.pk == emp.pk:
        return None, ('Ask HR to change these details, or send a change request from your profile.', 403)
    return None, ('You may only see your own details.' if not write else 'You may not change employee details.', 403)


def emp_qs(request):
    qs = Emp().objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_reporting_manager')
    allowed = user_branches(request)
    if allowed is not None:
        qs = qs.filter(emp_branch_id__in=allowed)
    return qs


def full_name(e):
    return ' '.join(x for x in [e.emp_first_name, e.emp_middle_name, e.emp_last_name] if x) if e else ''


def note(emp, body, user=None):
    """Chatter log note on the employee record."""
    try:
        M('Chatter', 'Message').objects.create(model='EmpManagement.emp_master', object_id=str(emp.pk), body=body,
                                               author_id=getattr(user, 'pk', None))
    except Exception:
        log.exception('chatter note failed')


def table_ready(name='EmployeeProfile_employmentinfo'):
    """True when this company schema has the EmployeeProfile tables (migrated)."""
    if connection.schema_name == 'public':
        return False
    key = (connection.schema_name, name)
    if key in _READY:
        return True
    try:
        ok = name in connection.introspection.table_names()
    except Exception:
        return False
    if ok:
        _READY.add(key)
    return ok


_READY = set()


# ------------------------------------------------------------------ identity ↔ Emp_Documents
DOC_KINDS = [
    # kind, document type name, number field, issue field, expiry field, other names the type may have
    ('passport', 'Passport', 'passport_no', 'passport_issue_date', 'passport_expiry_date', ('passport',)),
    ('visa', 'Visa', 'visa_number', 'visa_issue_date', 'visa_expiry_date', ('visa', 'residencevisa', 'residence', 'residency', 'residencepermit', 'employmentvisa')),
    ('emirates_id', 'Emirates ID', 'emirates_id', 'emirates_id_issue_date', 'emirates_id_expiry_date', ('emiratesid', 'eid', 'emiratesidcard', 'emiratesidentitycard')),
    ('labour_card', 'Labour Card', 'labour_card_no', 'labour_card_issue_date', 'labour_card_expiry_date', ('labourcard', 'laborcard', 'workpermit', 'labourpermit')),
    ('driving_licence', 'Driving Licence', 'driving_licence_no', 'driving_licence_issue_date', 'driving_licence_expiry_date', ('drivinglicence', 'drivinglicense', 'licence', 'license', 'dl')),
]
KIND_LABEL = {k[0]: k[1] for k in DOC_KINDS}


def _norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower())


def doc_types_for(kind):
    """document_type rows that mean this kind (by name)."""
    names = next(k[5] for k in DOC_KINDS if k[0] == kind)
    return [t for t in M('EmpManagement', 'document_type').objects.all() if _norm(t.type_name) in names]


def doc_type(kind, create=True):
    DT = M('EmpManagement', 'document_type')
    name = KIND_LABEL[kind]
    t = DT.objects.filter(type_name__iexact=name).first()
    if t is None:
        found = doc_types_for(kind)
        t = found[0] if found else None
    if t is None and create:
        t = DT.objects.create(type_name=name, description=f'{name} – kept in step with the employee’s UAE identity details')
    return t


def sync_documents(ident, emp, user=None):
    """Keep one Emp_Documents row per identity document (passport, visa, Emirates ID, labour card) so the existing
    expiry alerts, the daily expiry job and the Documents report keep working. Idempotent; never raises – a number
    that is already on another employee's document is reported instead."""
    Doc = M('EmpManagement', 'Emp_Documents')
    links = dict(ident.doc_links or {})
    problems, results = {}, {}
    for kind, tname, nf, isf, exf, _ in DOC_KINDS:
        num, iss, exp = getattr(ident, nf), getattr(ident, isf), getattr(ident, exf)
        if not num:
            results[kind] = {'status': 'skipped', 'message': f'No {tname.lower()} number.'}
            continue
        if not iss or not exp:
            msg = f'{tname}: enter the issue and expiry dates so the document list (and its expiry alert) can be updated.'
            problems[kind] = msg
            results[kind] = {'status': 'problem', 'message': msg}
            continue
        try:
            t = doc_type(kind)
            types = [t] + [x for x in doc_types_for(kind) if x.pk != t.pk]
            doc = Doc.objects.filter(pk=links.get(kind), emp_id_id=emp.pk).first() if links.get(kind) else None
            if doc is not None and doc.emp_doc_number.upper() != num.upper():
                doc = None   # renewed: a new number gets its own document, the old one is kept (inactive)
            if doc is None:
                doc = Doc.objects.filter(emp_id_id=emp.pk, emp_doc_number__iexact=num, document_type__in=types).first()
            clash = Doc.objects.filter(emp_doc_number__iexact=num).exclude(pk=doc.pk if doc else None).select_related('emp_id', 'document_type').first()
            if clash is not None:
                if clash.emp_id_id != emp.pk:
                    msg = f'{tname} number {num} is already on a document of employee {emp_label(clash.emp_id)} – the document list was not updated. Check the number.'
                else:
                    msg = f'{tname} number {num} is already on this employee’s "{clash.document_type or "other"}" document – the document list was not updated.'
                problems[kind] = msg
                results[kind] = {'status': 'problem', 'message': msg}
                continue
            with transaction.atomic():
                if doc is None:
                    old = Doc.objects.filter(pk=links.get(kind), emp_id_id=emp.pk).first() if links.get(kind) else None
                    doc = Doc(emp_id=emp, document_type=t, emp_doc_number=num, emp_doc_issued_date=iss, emp_doc_expiry_date=exp,
                              created_by_id=getattr(user, 'pk', None), updated_by_id=getattr(user, 'pk', None))
                    doc.save()
                    if old is not None and old.is_active:
                        Doc.objects.filter(pk=old.pk).update(is_active=False)
                    results[kind] = {'status': 'created', 'document_id': doc.pk}
                else:
                    changed = []
                    for f, v in (('emp_doc_number', num), ('emp_doc_issued_date', iss), ('emp_doc_expiry_date', exp), ('is_active', True)):
                        if getattr(doc, f) != v:
                            setattr(doc, f, v)
                            changed.append(f)
                    if changed:
                        doc.updated_by_id = getattr(user, 'pk', None)
                        doc.save()
                    results[kind] = {'status': 'updated' if changed else 'unchanged', 'document_id': doc.pk}
            links[kind] = doc.pk
        except IntegrityError:
            msg = f'{tname} number {num} could not be saved in the document list (the number is already used).'
            problems[kind] = msg
            results[kind] = {'status': 'problem', 'message': msg}
        except Exception as e:   # never block the identity save
            log.exception('document sync failed')
            msg = f'{tname}: the document list could not be updated ({e}).'
            problems[kind] = msg
            results[kind] = {'status': 'problem', 'message': msg}
    if links != (ident.doc_links or {}) or problems != (ident.doc_problems or {}):
        EmployeeIdentity.objects.filter(pk=ident.pk).update(doc_links=links, doc_problems=problems)
        ident.doc_links, ident.doc_problems = links, problems
    return results


def identity_for(emp_id, create=False):
    o = EmployeeIdentity.objects.filter(employee_id=emp_id).first()
    if o is None and create:
        o, _ = EmployeeIdentity.objects.get_or_create(employee_id=emp_id)
    return o


def save_identity(emp, data, user=None, partial=True):
    """Validate + save + sync documents. Raises ValidationError (field dict)."""
    inst = identity_for(emp.pk)
    ser = IdentitySerializer(inst, data=data, partial=partial, context={'employee_id': emp.pk})
    ser.is_valid(raise_exception=True)
    with transaction.atomic():
        obj = ser.save(employee_id=emp.pk, updated_by_id=getattr(user, 'pk', None))
    sync = sync_documents(obj, emp, user)
    return obj, sync


# ------------------------------------------------------------------ employee code numbering
def code_setting_for(branch_id):
    s = EmployeeCodeSetting.objects.filter(branch_id=branch_id).first() if branch_id else None
    return s or EmployeeCodeSetting.objects.filter(branch_id__isnull=True).first()


def auto_code_enabled(branch_id):
    if not table_ready('EmployeeProfile_employeecodesetting'):
        return False
    s = code_setting_for(branch_id)
    return bool(s and s.enabled)


def preview_code(branch_id):
    s = code_setting_for(branch_id)
    if not s or not s.enabled:
        return None
    n = s.next_number
    E = Emp()
    while E.objects.filter(emp_code__iexact=s.format(n)).exists():
        n += 1
    return s.format(n)


def allocate_code(branch_id):
    """Next free code for the branch (its rule, else the company default). Locks the rule row until the caller's
    transaction ends, so two saves never get the same number. None when numbering is off."""
    s = code_setting_for(branch_id)
    if not s or not s.enabled:
        return None
    with transaction.atomic():
        s = EmployeeCodeSetting.objects.select_for_update().get(pk=s.pk)
        n = s.next_number
        E = Emp()
        for _ in range(100000):
            if not E.objects.filter(emp_code__iexact=s.format(n)).exists():
                break
            n += 1
        code = s.format(n)
        s.next_number = n + 1
        s.save(update_fields=['next_number', 'updated_at'])
    return code


# ------------------------------------------------------------------ employment / probation
LEAVING_STATUSES = ('on_notice', 'left', 'terminated', 'absconded', 'retired')
TERM_STATUS = {'resignation': 'left', 'termination': 'terminated', 'retirement': 'retired', 'death_or_disablement': 'left'}


def probation_plan(emp):
    """(days, source, end date): employment type's probation days, else the branch's probation days."""
    days, source = None, ''
    if apps.is_installed('OrgStructure'):
        try:
            eo = M('OrgStructure', 'EmployeeOrg').objects.select_related('employment_type').filter(employee_id=emp.pk).first()
            if eo and eo.employment_type_id and eo.employment_type.probation_days:
                days, source = int(eo.employment_type.probation_days), 'employment_type'
        except Exception:
            pass
    if days is None and emp.emp_branch_id_id and (emp.emp_branch_id.probation_period_days or 0) > 0:
        days, source = int(emp.emp_branch_id.probation_period_days), 'branch'
    end = emp.emp_joined_date + timedelta(days=days) if days and emp.emp_joined_date else None
    if end and emp.emp_joined_date:
        cap = emp.emp_joined_date + relativedelta(months=UAE_MAX_PROBATION_MONTHS)
        end = min(end, cap)
    return days, source, end


def _final_status(info):
    src = info.leaving_source or ''
    if src.startswith('resignation:') or src.startswith('eos:'):
        try:
            rid = int(src.split(':')[1])
            R = M('EmpManagement', 'EmployeeResignation')
            res = R.objects.filter(pk=rid).first() if src.startswith('resignation:') else getattr(M('EmpManagement', 'EndOfService').objects.filter(pk=rid).first(), 'resignation', None)
            if res is not None:
                return TERM_STATUS.get(res.termination_type, 'left')
        except Exception:
            pass
    if src == 'probation':
        return 'terminated'
    return 'left'


def ensure_info(emp, today=None):
    """EmploymentInfo of the employee (created from what is known on first use)."""
    today = today or date.today()
    info = EmploymentInfo.objects.filter(employee_id=emp.pk).first()
    if info is not None:
        return refresh_probation(emp, info, today)
    days, source, end = probation_plan(emp)
    info = EmploymentInfo(employee_id=emp.pk, probation_days=days, probation_source=source, probation_end_date=end)
    conf = emp.emp_date_of_confirmation
    if end and end > today and not (conf and conf <= today and conf != end):
        info.probation_status, info.status = 'on_probation', 'probation'
    elif end or conf:
        info.probation_status = 'confirmed'
        info.confirmed_on = conf if conf and conf <= today else end
        info.status = 'active'
    else:
        info.probation_status, info.status = '', 'active'
    if emp.is_active is False:
        info.status, info.leaving_reason, info.leaving_source = 'left', 'Marked inactive', 'manual'
    try:
        with transaction.atomic():
            info.save()
    except IntegrityError:
        return EmploymentInfo.objects.get(employee_id=emp.pk)
    # leaving already recorded through a resignation / end of service
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        for r in R.objects.filter(employee_id=emp.pk, status__iexact='approved').order_by('id'):
            sync_from_resignation(r, info=info)
        for e in M('EmpManagement', 'EndOfService').objects.filter(resignation__employee_id=emp.pk).order_by('id'):
            sync_from_eos(e, info=info)
    except Exception:
        log.exception('leaving sync on create failed')
    return info


def refresh_probation(emp, info, today=None):
    """Undecided probation that was never extended follows the joining date / employment type; on-notice
    employees whose last day has passed get their final status."""
    today = today or date.today()
    changed = []
    if info.probation_status == 'on_probation' and info.probation_source in ('employment_type', 'branch', '') \
            and not ProbationExtension.objects.filter(employee_id=emp.pk).exists():
        days, source, end = probation_plan(emp)
        if end and (end, days, source) != (info.probation_end_date, info.probation_days, info.probation_source):
            info.probation_end_date, info.probation_days, info.probation_source = end, days, source
            changed += ['probation_end_date', 'probation_days', 'probation_source']
    # v1.13.0: a probation "confirmed" only because the planned end date had passed (no one decided it, no extension) follows
    # a changed plan too – e.g. a back-dated hire saved first with the branch's days, then given an employment type with a
    # longer probation on the same form. If the new end is still ahead, the employee is on probation again.
    elif info.probation_status == 'confirmed' and not info.decided_by_id and info.confirmed_on and \
            info.confirmed_on == info.probation_end_date and info.status in ('active', 'probation') and \
            not ProbationExtension.objects.filter(employee_id=emp.pk).exists():
        days, source, end = probation_plan(emp)
        if end and end != info.probation_end_date:
            info.probation_end_date, info.probation_days, info.probation_source = end, days, source
            changed += ['probation_end_date', 'probation_days', 'probation_source']
            if end > today:
                info.probation_status, info.confirmed_on, info.status = 'on_probation', None, 'probation'
                _set_confirmation(emp, end)
            else:
                info.confirmed_on = end
            changed += ['probation_status', 'confirmed_on', 'status']
    if info.status == 'on_notice' and info.date_of_leaving and info.date_of_leaving < today:
        info.status = _final_status(info)
        changed.append('status')
    if changed:
        info.save(update_fields=changed + ['updated_at'])
    return info


def _set_confirmation(emp, d):
    if emp.emp_date_of_confirmation != d:
        emp.emp_date_of_confirmation = d
        emp.save(update_fields=['emp_date_of_confirmation', 'updated_at'])


def _problem(field, msg):
    raise ValidationError({field: msg})


def confirm_probation(emp, user, on=None, note_text=''):
    info = ensure_info(emp)
    if info.probation_status == 'confirmed':
        _problem('detail', 'This employee is already confirmed.')
    if info.probation_status == 'failed':
        _problem('detail', 'The probation was marked as not passed – it cannot be confirmed now.')
    if info.status in LEAVING_STATUSES:
        _problem('detail', 'This employee is leaving or has left – the probation cannot be confirmed.')
    on = on or date.today()
    if emp.emp_joined_date and on < emp.emp_joined_date:
        _problem('date', 'The confirmation date cannot be before the joining date.')
    if on > date.today() + timedelta(days=31):
        _problem('date', 'Confirm the probation at most a month ahead.')
    info.probation_status, info.confirmed_on, info.decided_by_id = 'confirmed', on, getattr(user, 'pk', None)
    info.decision_note = (note_text or '')[:255]
    info.probation_end_date = info.probation_end_date or on
    if info.status == 'probation':
        info.status = 'active'
    info.updated_by_id = getattr(user, 'pk', None)
    info.save()
    _set_confirmation(emp, on)
    note(emp, f'Probation confirmed on {on:%d/%m/%Y}.' + (f' Note: {note_text}' if note_text else ''), user)
    return info


def extend_probation(emp, user, to_date, reason):
    info = ensure_info(emp)
    if info.probation_status not in ('on_probation', 'extended'):
        _problem('detail', 'Only an open probation can be extended.')
    if not (reason or '').strip():
        _problem('reason', 'Enter the reason for the extension.')
    if not to_date:
        _problem('to_date', 'Enter the new probation end date.')
    current = info.probation_end_date or date.today()
    if to_date <= current:
        _problem('to_date', f'The new end date must be after the current end date ({current:%d/%m/%Y}).')
    if emp.emp_joined_date:
        cap = emp.emp_joined_date + relativedelta(months=UAE_MAX_PROBATION_MONTHS)
        if to_date > cap:
            _problem('to_date', f'UAE law allows at most {UAE_MAX_PROBATION_MONTHS} months of probation in total – the latest end date is {cap:%d/%m/%Y}.')
    ProbationExtension.objects.create(employee_id=emp.pk, from_date=current, to_date=to_date, reason=reason.strip()[:255],
                                      decided_by=getattr(user, 'pk', None))
    info.probation_end_date, info.probation_status, info.status = to_date, 'extended', 'probation'
    info.probation_source = 'manual'
    info.decided_by_id = getattr(user, 'pk', None)
    info.decision_note = reason.strip()[:255]
    info.updated_by_id = getattr(user, 'pk', None)
    info.save()
    _set_confirmation(emp, to_date)
    note(emp, f'Probation extended from {current:%d/%m/%Y} to {to_date:%d/%m/%Y}. Reason: {reason.strip()}', user)
    return info


def fail_probation(emp, user, last_day=None, reason=''):
    info = ensure_info(emp)
    if info.probation_status not in ('on_probation', 'extended'):
        _problem('detail', 'Only an open probation can be marked as not passed.')
    if not (reason or '').strip():
        _problem('reason', 'Enter the reason.')
    today = date.today()
    last_day = last_day or today + timedelta(days=PROBATION_NOTICE_DAYS)
    if emp.emp_joined_date and last_day < emp.emp_joined_date:
        _problem('last_day', 'The last working day cannot be before the joining date.')
    info.probation_status, info.failed_on, info.decided_by_id = 'failed', today, getattr(user, 'pk', None)
    info.decision_note = reason.strip()[:255]
    info.date_of_leaving = last_day
    info.leaving_reason = f'Probation not passed: {reason.strip()}'[:255]
    info.leaving_source = 'probation'
    info.status = 'on_notice' if last_day > today else 'terminated'
    info.updated_by_id = getattr(user, 'pk', None)
    info.save()
    short = (last_day - today).days < PROBATION_NOTICE_DAYS
    note(emp, f'Probation not passed. Last working day {last_day:%d/%m/%Y}. Reason: {reason.strip()}'
              + (f' (less than the {PROBATION_NOTICE_DAYS} days’ notice UAE law asks for)' if short else ''), user)
    return info, short


def sync_from_resignation(res, info=None):
    """Approved resignation / termination → on notice (or left) with the last working day; rejected → back."""
    emp = res.employee
    info = info or ensure_info(emp)
    st = (res.status or '').strip().lower()
    src = f'resignation:{res.pk}'
    today = date.today()
    if st == 'approved':
        if info.leaving_source.startswith('eos:'):
            return info
        final = TERM_STATUS.get(res.termination_type, 'left')
        lwd = res.last_working_date or res.resigned_on
        info.status = 'on_notice' if lwd and lwd >= today else final
        info.date_of_leaving = lwd
        reason = dict(M('EmpManagement', 'EmployeeResignation').TERMINATION_TYPE_CHOICES).get(res.termination_type, 'Resignation')
        info.leaving_reason = (reason + (f': {res.reason_for_leaving}' if res.reason_for_leaving else ''))[:255]
        info.leaving_source = src
    elif st in ('rejected', 'cancelled', 'withdrawn') and info.leaving_source == src:
        info.status = 'probation' if info.probation_status in ('on_probation', 'extended') else 'active'
        info.date_of_leaving, info.leaving_reason, info.leaving_source = None, '', ''
    else:
        return info
    info.save()
    return info


def sync_from_eos(eos, info=None):
    res = eos.resignation
    if (eos.status or '').lower() not in ('processed', 'paid'):
        return info
    info = info or ensure_info(res.employee)
    info.status = TERM_STATUS.get(res.termination_type, 'left')
    info.date_of_leaving = eos.last_working_date or res.last_working_date
    if not info.leaving_reason:
        info.leaving_reason = dict(M('EmpManagement', 'EmployeeResignation').TERMINATION_TYPE_CHOICES).get(res.termination_type, 'Resignation')
    info.leaving_source = f'eos:{eos.pk}'
    info.save()
    return info


def employment_json(emp, info=None):
    info = info or ensure_info(emp)
    today = date.today()
    ext = list(ProbationExtension.objects.filter(employee_id=emp.pk).values('id', 'from_date', 'to_date', 'reason', 'decided_by', 'created_at'))
    users = dict(M('UserManagement', 'CustomUser').objects.filter(pk__in=[e['decided_by'] for e in ext if e['decided_by']] + ([info.decided_by_id] if info.decided_by_id else [])).values_list('id', 'username'))
    for e in ext:
        e['decided_by_name'] = users.get(e['decided_by'], '')
    used = None
    if emp.emp_joined_date:
        cap = emp.emp_joined_date + relativedelta(months=UAE_MAX_PROBATION_MONTHS)
        used = {'max_end_date': cap}
    return {
        'employee_id': emp.pk, 'employee_code': emp.emp_code, 'joined_date': emp.emp_joined_date,
        'date_of_confirmation': emp.emp_date_of_confirmation,
        'status': info.status, 'status_label': dict(EmploymentInfo.STATUS).get(info.status, info.status),
        'probation_status': info.probation_status, 'probation_status_label': dict(EmploymentInfo.PROBATION).get(info.probation_status, 'No probation'),
        'probation_end_date': info.probation_end_date, 'probation_days': info.probation_days, 'probation_source': info.probation_source,
        'days_left': (info.probation_end_date - today).days if info.probation_end_date and info.probation_status in ('on_probation', 'extended') else None,
        'confirmed_on': info.confirmed_on, 'failed_on': info.failed_on, 'decision_note': info.decision_note,
        'decided_by_name': users.get(info.decided_by_id, ''),
        'date_of_leaving': info.date_of_leaving, 'leaving_reason': info.leaving_reason, 'leaving_source': info.leaving_source,
        'is_active': bool(emp.is_active), 'extensions': ext, 'limits': used,
        'can_decide': info.probation_status in ('on_probation', 'extended') and info.status not in LEAVING_STATUSES,
    }


def probation_due(qs, days=30, today=None):
    today = today or date.today()
    ids = list(qs.values_list('id', flat=True))
    for e in qs.filter(id__in=ids).exclude(id__in=EmploymentInfo.objects.values('employee_id')):
        ensure_info(e)
    infos = EmploymentInfo.objects.filter(employee_id__in=ids, probation_status__in=('on_probation', 'extended'),
                                          probation_end_date__isnull=False, probation_end_date__lte=today + timedelta(days=days))
    return infos


# ------------------------------------------------------------------ skills
def skills_json(emp):
    out = {'learning': [], 'marketing': [], 'programming': [], 'language': []}
    if apps.is_installed('LearningPlus'):
        try:
            for s in M('LearningPlus', 'EmployeeSkill').objects.filter(employee_id=emp.pk).select_related('skill'):
                out['learning'].append({'id': s.pk, 'skill_id': s.skill_id, 'skill': s.skill.name, 'category': s.skill.category or '', 'level': s.level,
                                        'source': s.get_source_display(), 'date': s.date, 'verified': bool(s.verified_by)})
        except Exception:
            log.exception('learning skills')
    for key, rel in (('marketing', 'emp_market_skills'), ('programming', 'emp_prgrm_skills'), ('language', 'emp_lang_skills')):
        try:
            for s in getattr(emp, rel).all():
                out[key].append({'id': s.pk, 'skill': s.value or '', 'percentage': float(s.percentage) if s.percentage is not None else None})
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ history timeline
HISTORY_TYPES = [('joined', 'Joined'), ('probation', 'Probation & confirmation'), ('transfer', 'Transfers & promotions'),
                 ('salary', 'Salary revisions'), ('org', 'Organisation changes'), ('field', 'Field changes'), ('exit', 'Resignation & exit')]


def _names(model, ids, attr):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    try:
        return dict(M(*model.split('.')).objects.filter(pk__in=ids).values_list('id', attr))
    except Exception:
        return {}


def timeline(emp, types=None, date_from=None, date_to=None):
    types = set(types or [t for t, _ in HISTORY_TYPES])
    ev = []

    def add(kind, d, title, detail='', by='', ref=None):
        if d is None:
            return
        dd = d.date() if hasattr(d, 'date') and callable(d.date) else d
        if date_from and dd < date_from:
            return
        if date_to and dd > date_to:
            return
        ev.append({'type': kind, 'date': dd, 'at': d.isoformat() if hasattr(d, 'isoformat') else str(d), 'title': title, 'detail': detail, 'by': by, 'ref': ref})

    info = ensure_info(emp)
    if 'joined' in types and emp.emp_joined_date:
        add('joined', emp.emp_joined_date, 'Joined', f'Branch {emp.emp_branch_id.branch_name if emp.emp_branch_id_id else "–"}'
            + (f', {emp.emp_desgntn_id.desgntn_job_title}' if emp.emp_desgntn_id_id else ''))
    if 'probation' in types:
        if info.probation_end_date and info.probation_status in ('on_probation', 'extended'):
            add('probation', info.probation_end_date, 'Probation ends', f'{info.probation_days or ""} days' if info.probation_days else '')
        for x in ProbationExtension.objects.filter(employee_id=emp.pk):
            add('probation', x.created_at, 'Probation extended', f'{x.from_date:%d/%m/%Y} → {x.to_date:%d/%m/%Y}. {x.reason}', ref={'model': 'ProbationExtension', 'id': x.pk})
        if info.probation_status == 'confirmed' and info.confirmed_on:
            add('probation', info.confirmed_on, 'Confirmed', info.decision_note)
        elif info.probation_status == '' and emp.emp_date_of_confirmation:
            add('probation', emp.emp_date_of_confirmation, 'Confirmation date', '')
        if info.failed_on:
            add('probation', info.failed_on, 'Probation not passed', info.decision_note)
    if 'transfer' in types and apps.is_installed('HRActions'):
        for t in M('HRActions', 'EmployeeTransfer').objects.filter(employee_id=emp.pk).exclude(status='cancelled'):
            br = _names('OrganisationManager.brnch_mstr', [t.from_branch_id, t.to_branch_id], 'branch_name')
            dp = _names('OrganisationManager.dept_master', [t.from_department_id, t.to_department_id], 'dept_name')
            dg = _names('OrganisationManager.desgntn_master', [t.from_designation_id, t.to_designation_id], 'desgntn_job_title')
            parts = []
            if t.to_branch_id and t.to_branch_id != t.from_branch_id:
                parts.append(f'Branch {br.get(t.from_branch_id, "–")} → {br.get(t.to_branch_id, "–")}')
            if t.to_department_id and t.to_department_id != t.from_department_id:
                parts.append(f'Department {dp.get(t.from_department_id, "–")} → {dp.get(t.to_department_id, "–")}')
            promo = bool(t.to_designation_id and t.to_designation_id != t.from_designation_id)
            if promo:
                parts.append(f'Designation {dg.get(t.from_designation_id, "–")} → {dg.get(t.to_designation_id, "–")}')
            if t.salary_changes:
                parts.append('salary changed')
            title = ('Promotion / designation change' if promo and not (t.to_branch_id and t.to_branch_id != t.from_branch_id) else 'Transfer')
            if t.status == 'scheduled':
                title += ' (scheduled)'
            add('transfer', t.effective_date, title, '; '.join(parts) + (f'. {t.reason}' if t.reason else ''), ref={'model': 'EmployeeTransfer', 'id': t.pk})
    if 'salary' in types:
        try:
            from django.utils.dateparse import parse_datetime
            for h in M('PayrollManagement', 'SalaryRevisionHistory').objects.filter(employee_id=emp.pk).select_related('component'):
                for r in (h.revisions or []):
                    d = parse_datetime(r.get('revised_on') or '') or h.revised_on
                    add('salary', d, f'Salary revised – {h.component.name}', f'{r.get("old_amount") or 0} → {r.get("new_amount") or 0}'
                        + (f' from {r["effective_period"]}' if r.get('effective_period') else '') + (f'. {r["remarks"]}' if r.get('remarks') else ''),
                        r.get('revised_by') or '')
        except Exception:
            log.exception('salary history')
    RL = M('Chatter', 'RecordLog') if apps.is_installed('Chatter') else None
    if RL is not None and 'org' in types and apps.is_installed('OrgStructure'):
        eo = M('OrgStructure', 'EmployeeOrg').objects.filter(employee_id=emp.pk).first()
        if eo:
            for lg in RL.objects.filter(model='OrgStructure.employeeorg', object_id=str(eo.pk)).exclude(action='deleted'):
                ch = lg.changes or {}
                desc = '; '.join(f'{v.get("label", k)}: {v.get("old") or "–"} → {v.get("new") or "–"}' for k, v in ch.items() if isinstance(v, dict))
                add('org', lg.at, 'Organisation details changed' if lg.action == 'updated' else 'Organisation details set', desc, lg.user_name)
    if RL is not None and 'field' in types:
        keys = [('EmpManagement.emp_master', str(emp.pk), 'Employee')]
        ident = identity_for(emp.pk)
        if ident:
            keys.append(('EmployeeProfile.employeeidentity', str(ident.pk), 'UAE identity'))
        keys.append(('EmployeeProfile.employmentinfo', str(info.pk), 'Employment'))
        for model, oid, label in keys:
            for lg in RL.objects.filter(model=model, object_id=oid, action='updated'):
                ch = {k: v for k, v in (lg.changes or {}).items() if isinstance(v, dict)}
                if not ch:
                    continue
                desc = '; '.join(f'{v.get("label", k)}: {v.get("old") if v.get("old") not in (None, "") else "–"} → {v.get("new") if v.get("new") not in (None, "") else "–"}' for k, v in ch.items())
                add('field', lg.at, f'{label} details changed', desc[:600], lg.user_name)
    if 'exit' in types:
        for r in M('EmpManagement', 'EmployeeResignation').objects.filter(employee_id=emp.pk):
            kind = dict(r.TERMINATION_TYPE_CHOICES).get(r.termination_type, 'Resignation')
            add('exit', r.document_date or r.resigned_on, f'{kind} submitted', f'Status {r.status}; last working day {r.last_working_date:%d/%m/%Y}' if r.last_working_date else f'Status {r.status}', ref={'model': 'EmployeeResignation', 'id': r.pk})
        for e in M('EmpManagement', 'EndOfService').objects.filter(resignation__employee_id=emp.pk):
            add('exit', e.processed_date, 'End of service', f'Status {e.status}; gratuity {e.gratuity_amount} AED; {e.years_of_service:.2f} years', ref={'model': 'EndOfService', 'id': e.pk})
        if info.date_of_leaving and info.status in LEAVING_STATUSES:
            add('exit', info.date_of_leaving, 'Last working day', f'{dict(EmploymentInfo.STATUS).get(info.status)} – {info.leaving_reason}')
    ev.sort(key=lambda x: (str(x['date']), x['at']), reverse=True)
    return ev


# ------------------------------------------------------------------ completeness
def _is_national(emp, ident):
    if ident is not None and ident.visa_type == 'national':
        return True
    n = (str(emp.emp_nationality or '')).lower()
    return 'emirati' in n or n in ('uae', 'united arab emirates')


def completeness(emp, ident=None, has_contact=None, has_bank=None, cache=None):
    """[(label, ok)] of the fields a UAE employee file needs, plus the % filled."""
    ident = ident if ident is not None else identity_for(emp.pk)
    national = _is_national(emp, ident)
    g = (lambda f: getattr(ident, f, None) if ident is not None else None)
    if has_contact is None:
        has_contact = EmergencyContact.objects.filter(employee_id=emp.pk).exists()
    if has_bank is None:
        has_bank = M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee_id=emp.pk, is_active=True).exclude(iban_number__isnull=True).exclude(iban_number='').exists()
    checks = [
        ('First name', emp.emp_first_name), ('Last name', emp.emp_last_name), ('Gender', emp.emp_gender), ('Date of birth', emp.emp_date_of_birth),
        ('Nationality', emp.emp_nationality_id), ('Marital status', emp.emp_marital_status), ('Mobile number', emp.emp_mobile_number_1),
        ('E-mail', emp.emp_personal_email or emp.emp_company_email), ('Address', emp.emp_present_address or emp.emp_permenent_address),
        ('Department', emp.emp_dept_id_id), ('Designation', emp.emp_desgntn_id_id), ('Reporting manager', emp.emp_reporting_manager_id),
        ('Joining date', emp.emp_joined_date), ('Arabic name', g('arabic_name')),
        ('Passport number', g('passport_no')), ('Passport expiry', g('passport_expiry_date')),
        ('Emirates ID', g('emirates_id')), ('Emirates ID expiry', g('emirates_id_expiry_date')),
    ]
    if not national:
        checks += [('Visa number', g('visa_number')), ('Visa expiry', g('visa_expiry_date')), ('Labour card / work permit', g('labour_card_no') or g('work_permit_no')),
                   ('MOHRE person ID (WPS)', emp.person_id)]
    checks += [('Medical insurance card', g('insurance_card_no')), ('Emergency contact', has_contact), ('Bank IBAN (WPS)', has_bank)]
    ok = [c for c in checks if c[1] not in (None, '', False)]
    missing = [c[0] for c in checks if c[1] in (None, '', False)]
    return {'score': round(100 * len(ok) / len(checks)), 'filled': len(ok), 'total': len(checks), 'missing': missing}


# ------------------------------------------------------------------ list columns
def employee_columns(emps):
    ids = [e.pk for e in emps]
    idn = {i.employee_id: i for i in EmployeeIdentity.objects.filter(employee_id__in=ids)}
    have = set(EmploymentInfo.objects.filter(employee_id__in=ids).values_list('employee_id', flat=True))
    for e in emps:
        if e.pk not in have:
            ensure_info(e)
    infos = {i.employee_id: i for i in EmploymentInfo.objects.filter(employee_id__in=ids)}
    out = []
    for e in emps:
        i, f = idn.get(e.pk), infos.get(e.pk)
        m = e.emp_reporting_manager
        out.append({
            'employee_id': e.pk, 'arabic_name': i.arabic_name if i else '', 'passport_no': i.passport_no if i else '',
            'passport_expiry': i.passport_expiry_date if i else None, 'visa_expiry': i.visa_expiry_date if i else None,
            'emirates_id': i.emirates_id if i else '', 'eid_expiry': i.emirates_id_expiry_date if i else None,
            'labour_card_expiry': i.labour_card_expiry_date if i else None,
            'status': f.status if f else '', 'status_label': dict(EmploymentInfo.STATUS).get(f.status, '') if f else '',
            'probation_end': f.probation_end_date if f and f.probation_status in ('on_probation', 'extended') else None,
            'probation_status': f.probation_status if f else '', 'date_of_leaving': f.date_of_leaving if f else None,
            'manager': ((m.get_full_name() if hasattr(m, 'get_full_name') else '') or m.username) if m else '',
        })
    return out


# ================================================================== contract for SelfService
def _choices(model, field):
    return [{'value': v, 'label': l} for v, l in model._meta.get_field(field).choices]


def _f(key, label, type_='text', **kw):
    d = {'key': key, 'label': label, 'type': type_}
    d.update(kw)
    return d


def profile_groups():
    """Ordered groups of the employee profile with their fields ({key, label, type[, options, source]})."""
    Emp_ = Emp()
    groups = [
        {'key': 'personal', 'label': 'Personal information', 'model': 'EmpManagement.emp_master', 'many': False, 'fields': [
            _f('emp_first_name', 'First name'), _f('emp_middle_name', 'Middle name'), _f('emp_last_name', 'Last name'),
            _f('emp_gender', 'Gender', 'select', options=_choices(Emp_, 'emp_gender')),
            _f('emp_date_of_birth', 'Date of birth', 'date'),
            _f('emp_marital_status', 'Marital status', 'select', options=[{'value': 'S', 'label': 'Single'}, {'value': 'M', 'label': 'Married'}, {'value': 'D', 'label': 'Divorced'}, {'value': 'W', 'label': 'Widowed'}]),
            _f('emp_nationality', 'Nationality', 'fk', source='Core.Nationality'), _f('emp_relegion', 'Religion', 'fk', source='Core.ReligionMaster'),
            _f('emp_blood_group', 'Blood group', 'select', options=[{'value': b, 'label': b} for b in ('A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-')]),
            _f('emp_father_name', 'Father’s name'), _f('emp_mother_name', 'Mother’s name')]},
        {'key': 'contact', 'label': 'Contact details', 'model': 'EmpManagement.emp_master', 'many': False, 'fields': [
            _f('emp_personal_email', 'Personal e-mail', 'email'), _f('emp_company_email', 'Company e-mail', 'email'),
            _f('emp_mobile_number_1', 'Mobile number', 'phone'), _f('emp_mobile_number_2', 'Other number', 'phone')]},
        {'key': 'address', 'label': 'Address', 'model': 'EmpManagement.emp_master', 'many': False, 'fields': [
            _f('emp_present_address', 'Present address'), _f('emp_permenent_address', 'Permanent address'), _f('emp_city', 'City'),
            _f('emp_country_id', 'Country', 'fk', source='Core.cntry_mstr'), _f('emp_state_id', 'State / emirate', 'fk', source='Core.state_mstr')]},
        {'key': 'identity', 'label': 'UAE identity', 'model': 'EmployeeProfile.EmployeeIdentity', 'many': False, 'fields': [
            _f('arabic_name', 'Name in Arabic', 'text', dir='rtl'), _f('title', 'Title', 'select', options=_choices(EmployeeIdentity, 'title')),
            _f('preferred_name', 'Preferred name'), _f('place_of_birth', 'Place of birth'),
            _f('passport_no', 'Passport number'), _f('passport_issue_date', 'Passport issue date', 'date'), _f('passport_expiry_date', 'Passport expiry date', 'date'),
            _f('passport_place_of_issue', 'Passport place of issue'), _f('passport_country_id', 'Passport country', 'fk', source='Core.cntry_mstr'),
            _f('visa_type', 'Visa type', 'select', options=_choices(EmployeeIdentity, 'visa_type')), _f('visa_number', 'Visa / residence number'),
            _f('visa_uid', 'UID number'), _f('visa_file_no', 'Visa file number'), _f('visa_issue_date', 'Visa issue date', 'date'),
            _f('visa_expiry_date', 'Visa expiry date', 'date'), _f('visa_sponsor', 'Visa sponsor'),
            _f('emirates_id', 'Emirates ID', 'text', placeholder='784-YYYY-NNNNNNN-N'), _f('emirates_id_issue_date', 'Emirates ID issue date', 'date'),
            _f('emirates_id_expiry_date', 'Emirates ID expiry date', 'date'),
            _f('labour_card_no', 'Labour card number'), _f('labour_card_issue_date', 'Labour card issue date', 'date'),
            _f('labour_card_expiry_date', 'Labour card expiry date', 'date'), _f('work_permit_no', 'Work permit number'),
            _f('work_permit_expiry_date', 'Work permit expiry date', 'date'),
            _f('mohre_contract_type', 'MOHRE contract type', 'select', options=_choices(EmployeeIdentity, 'mohre_contract_type')),
            _f('mohre_contract_no', 'MOHRE contract number'), _f('mohre_contract_start', 'Contract start', 'date'), _f('mohre_contract_end', 'Contract end', 'date'),
            _f('driving_licence_no', 'Driving licence number'), _f('driving_licence_issue_date', 'Driving licence issue date', 'date'),
            _f('driving_licence_expiry_date', 'Driving licence expiry date', 'date'),
            _f('driving_licence_emirate', 'Driving licence emirate', 'select', options=_choices(EmployeeIdentity, 'driving_licence_emirate')),
            _f('driving_licence_categories', 'Driving licence categories', 'text', placeholder='Light vehicle, Motorcycle'),
            _f('insurance_provider', 'Insurance provider'), _f('insurance_card_no', 'Insurance card number'), _f('insurance_expiry_date', 'Insurance expiry date', 'date')]},
        {'key': 'emergency', 'label': 'Emergency contacts', 'model': 'EmployeeProfile.EmergencyContact', 'many': True, 'fields': [
            _f('name', 'Name'), _f('relation', 'Relation'), _f('mobile', 'Mobile', 'phone'), _f('alt_phone', 'Other phone', 'phone'),
            _f('email', 'E-mail', 'email'), _f('address', 'Address'), _f('is_primary', 'Primary contact', 'bool')]},
        {'key': 'family', 'label': 'Family / dependants', 'model': 'EmpManagement.emp_family', 'many': True, 'extra_model': 'EmployeeProfile.DependentExtra', 'fields': [
            _f('ef_member_name', 'Name'), _f('emp_relation', 'Relation'), _f('ef_date_of_birth', 'Date of birth', 'date'),
            _f('ef_company_expence', 'Company expense', 'number'),
            _f('gender', 'Gender', 'select', options=_choices(DependentExtra, 'gender')), _f('nationality_id', 'Nationality', 'fk', source='Core.Nationality'),
            _f('passport_no', 'Passport number'), _f('passport_expiry_date', 'Passport expiry', 'date'),
            _f('emirates_id', 'Emirates ID'), _f('emirates_id_expiry_date', 'Emirates ID expiry', 'date'),
            _f('visa_number', 'Visa number'), _f('visa_expiry_date', 'Visa expiry', 'date'),
            _f('insured', 'Covered by medical insurance', 'bool'), _f('visa_sponsored', 'Visa sponsored by the employee', 'bool')]},
        {'key': 'bank', 'label': 'Bank details', 'model': 'EmpManagement.EmployeeBankDetail', 'many': True, 'extra_model': 'EmployeeProfile.BankExtra', 'fields': [
            _f('bank_name', 'Bank'), _f('branch_name', 'Bank branch'), _f('account_number', 'Account number'), _f('iban_number', 'IBAN', 'text', placeholder='AE + 21 digits'),
            _f('route_code', 'Routing code (9 digits)'), _f('bank_address', 'Bank address'), _f('is_active', 'Active', 'bool'),
            _f('payment_mode', 'Payment mode', 'select', options=_choices(BankExtra, 'payment_mode')), _f('wps_agent', 'WPS agent ID'),
            _f('is_primary', 'Salary account', 'bool'), _f('effective_from', 'Effective from', 'date')]},
        {'key': 'qualifications', 'label': 'Qualifications', 'model': 'EmpManagement.EmpQualification', 'many': True, 'extra_model': 'EmployeeProfile.QualificationExtra', 'fields': [
            _f('emp_qualification', 'Qualification'), _f('emp_qf_instituition', 'Institution'), _f('emp_qf_year', 'Completed on', 'date'),
            _f('emp_qf_subject', 'Subject'), _f('grade', 'Grade / result'), _f('country_id', 'Country', 'fk', source='Core.cntry_mstr'),
            _f('attested', 'Attested (UAE MOFA)', 'bool'), _f('attestation_date', 'Attestation date', 'date'),
            _f('equivalency', 'Equivalency certificate', 'bool'), _f('attachment', 'Attested copy', 'file')]},
        {'key': 'experience', 'label': 'Experience', 'model': 'EmpManagement.EmpJobHistory', 'many': True, 'fields': [
            _f('emp_jh_company_name', 'Company'), _f('emp_jh_designation', 'Designation'), _f('emp_jh_from_date', 'From', 'date'),
            _f('emp_jh_end_date', 'To', 'date'), _f('emp_jh_leaving_salary_permonth', 'Last salary per month', 'number'),
            _f('emp_jh_reason', 'Reason for leaving'), _f('emp_jh_years_experiance', 'Years', 'number')]},
        {'key': 'skills', 'label': 'Skills', 'model': 'LearningPlus.EmployeeSkill', 'many': True, 'fields': [
            _f('skill', 'Skill', 'fk', source='LearningPlus.Skill'), _f('level', 'Level (1-5)', 'number')]},
        {'key': 'documents', 'label': 'Documents', 'model': 'EmpManagement.Emp_Documents', 'many': True, 'fields': [
            _f('document_type', 'Document type', 'fk', source='EmpManagement.document_type'), _f('emp_doc_number', 'Document number'),
            _f('emp_doc_issued_date', 'Issue date', 'date'), _f('emp_doc_expiry_date', 'Expiry date', 'date'), _f('emp_doc_document', 'File', 'file')]},
    ]
    if not apps.is_installed('LearningPlus'):
        groups = [g for g in groups if g['key'] != 'skills']
    return groups


GROUP_KEYS = {g: None for g in ('personal', 'contact', 'address', 'identity', 'emergency', 'family', 'bank', 'qualifications', 'experience', 'skills', 'documents')}


def group_fields(group):
    for g in profile_groups():
        if g['key'] == group:
            return [f['key'] for f in g['fields']]
    raise ValidationError({'group': f'Unknown group "{group}".'})


# ---- core serializers for the existing tables (same checks as the HR screens)
class _FamilyCore(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = ['ef_member_name', 'emp_relation', 'ef_company_expence', 'ef_date_of_birth']

    def validate_ef_date_of_birth(self, v):
        if v and v > date.today():
            raise serializers.ValidationError('The date of birth cannot be in the future.')
        return v

    def validate_ef_company_expence(self, v):
        if v is not None and v < 0:
            raise serializers.ValidationError('The amount cannot be negative.')
        return v


class _BankCore(serializers.ModelSerializer):
    iban_number = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=40)

    class Meta:
        model = None
        fields = ['bank_name', 'branch_name', 'account_number', 'bank_address', 'route_code', 'iban_number', 'is_active']

    def validate_iban_number(self, v):
        try:
            return V.clean_iban(v) or None
        except ValueError as e:
            raise serializers.ValidationError(str(e))


class _QualCore(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = ['emp_qualification', 'emp_qf_instituition', 'emp_qf_year', 'emp_qf_subject']

    def validate_emp_qf_year(self, v):
        if v and v > date.today():
            raise serializers.ValidationError('The completion date cannot be in the future.')
        return v


class _JobCore(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = ['emp_jh_from_date', 'emp_jh_end_date', 'emp_jh_company_name', 'emp_jh_designation', 'emp_jh_leaving_salary_permonth',
                  'emp_jh_reason', 'emp_jh_years_experiance']

    def validate(self, attrs):
        inst = self.instance
        f = attrs.get('emp_jh_from_date', getattr(inst, 'emp_jh_from_date', None))
        t = attrs.get('emp_jh_end_date', getattr(inst, 'emp_jh_end_date', None))
        if f and t and t < f:
            raise serializers.ValidationError({'emp_jh_end_date': 'The end date must be after the start date.'})
        if t and t > date.today():
            raise serializers.ValidationError({'emp_jh_end_date': 'Previous employment cannot end in the future.'})
        return attrs


class _DocCore(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = ['document_type', 'emp_doc_number', 'emp_doc_issued_date', 'emp_doc_expiry_date', 'emp_doc_document']

    def validate(self, attrs):
        inst = self.instance
        i = attrs.get('emp_doc_issued_date', getattr(inst, 'emp_doc_issued_date', None))
        e = attrs.get('emp_doc_expiry_date', getattr(inst, 'emp_doc_expiry_date', None))
        errors = {}
        V.check_dates(i, e, 'document', errors, 'emp_doc_issued_date', 'emp_doc_expiry_date')
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class _SkillCore(serializers.ModelSerializer):
    class Meta:
        model = None
        fields = ['skill', 'level']

    def validate_level(self, v):
        if v is None or not 1 <= int(v) <= 5:
            raise serializers.ValidationError('Level must be 1 to 5.')
        return v


def _core(cls, model):
    meta = type('Meta', (cls.Meta,), {'model': model})
    return type(cls.__name__ + model.__name__, (cls,), {'Meta': meta})


EMP_GROUPS = ('personal', 'contact', 'address')
MULTI = {
    # group: (app.model, fk field to emp_master, core serializer base, extra model, extra key, extra serializer)
    'family': ('EmpManagement.emp_family', 'emp_id', _FamilyCore, DependentExtra, 'family_id', DependentExtraSerializer),
    'bank': ('EmpManagement.EmployeeBankDetail', 'employee', _BankCore, BankExtra, 'bank_detail_id', BankExtraSerializer),
    'qualifications': ('EmpManagement.EmpQualification', 'emp_id', _QualCore, QualificationExtra, 'qualification_id', QualificationExtraSerializer),
    'experience': ('EmpManagement.EmpJobHistory', 'emp_id', _JobCore, None, None, None),
    'documents': ('EmpManagement.Emp_Documents', 'emp_id', _DocCore, None, None, None),
}


def _plain(data):
    if hasattr(data, 'getlist'):
        out = {}
        for k in data.keys():
            v = data.getlist(k)
            out[k] = v[0] if len(v) == 1 else v
        return out
    return dict(data or {})


def _prepare(employee, group, record_id, data, action=None, user=None):
    """Validate everything first; returns a function that saves (and returns the saved record as a dict)."""
    data = _plain(data)
    action = action or (data.pop('_action', None)) or ('delete' if data.pop('_delete', False) else None)
    allowed = set(group_fields(group))
    unknown = [k for k in data if k not in allowed]
    if unknown and action != 'delete':
        raise ValidationError({k: 'This field cannot be changed here.' for k in unknown})
    uid = getattr(user, 'pk', None)

    if group in EMP_GROUPS:
        if action == 'delete':
            raise ValidationError({'detail': 'These details cannot be deleted.'})
        from EmpManagement.serializer import EmpSerializer
        ser = EmpSerializer(employee, data=data, partial=True, context={})
        ser.is_valid(raise_exception=True)

        def run():
            ser.save()
            employee.refresh_from_db()
            return {k: getattr(employee, k + '_id', None) if k in ('emp_nationality', 'emp_relegion', 'emp_country_id', 'emp_state_id') else getattr(employee, k) for k in allowed}
        return run

    if group == 'identity':
        if action == 'delete':
            raise ValidationError({'detail': 'Identity details cannot be deleted – clear the fields instead.'})
        inst = identity_for(employee.pk)
        ser = IdentitySerializer(inst, data=data, partial=True, context={'employee_id': employee.pk})
        ser.is_valid(raise_exception=True)

        def run():
            obj = ser.save(employee_id=employee.pk, updated_by_id=uid)
            sync = sync_documents(obj, employee, user)
            d = IdentitySerializer(obj).data
            d['document_sync'] = sync
            return d
        return run

    if group == 'emergency':
        inst = None
        if record_id:
            inst = EmergencyContact.objects.filter(pk=record_id, employee_id=employee.pk).first()
            if inst is None:
                raise ValidationError({'detail': 'Emergency contact not found.'})
        if action == 'delete':
            if inst is None:
                raise ValidationError({'detail': 'Choose the contact to delete.'})
            return lambda: (inst.delete(), {'deleted': True})[1]
        ser = EmergencySerializer(inst, data=data, partial=inst is not None)
        ser.is_valid(raise_exception=True)

        def run():
            with transaction.atomic():
                first = not EmergencyContact.objects.filter(employee_id=employee.pk).exclude(pk=getattr(inst, 'pk', None)).exists()
                obj = ser.save(employee_id=employee.pk, **({'is_primary': True} if first else {}))
                if obj.is_primary:
                    EmergencyContact.objects.filter(employee_id=employee.pk, is_primary=True).exclude(pk=obj.pk).update(is_primary=False)
            return EmergencySerializer(obj).data
        return run

    if group == 'skills':
        if not apps.is_installed('LearningPlus'):
            raise ValidationError({'group': 'Skills are not available.'})
        ES = M('LearningPlus', 'EmployeeSkill')
        inst = None
        if record_id:
            inst = ES.objects.filter(pk=record_id, employee_id=employee.pk).first()
            if inst is None:
                raise ValidationError({'detail': 'Skill not found.'})
        if action == 'delete':
            if inst is None:
                raise ValidationError({'detail': 'Choose the skill to delete.'})
            return lambda: (inst.delete(), {'deleted': True})[1]
        ser = _core(_SkillCore, ES)(inst, data=data, partial=inst is not None)
        ser.is_valid(raise_exception=True)
        sk = ser.validated_data.get('skill')
        if sk is not None and ES.objects.filter(employee_id=employee.pk, skill=sk).exclude(pk=getattr(inst, 'pk', None)).exists():
            raise ValidationError({'skill': 'This skill is already on the profile – change its level instead.'})

        def run():
            obj = ser.save(employee_id=employee.pk, **({} if inst else {'source': 'manual', 'date': date.today()}))
            return {'id': obj.pk, 'skill': obj.skill_id, 'skill_name': obj.skill.name, 'level': obj.level}
        return run

    if group in MULTI:
        label, fk, core_cls, extra_model, extra_key, extra_ser = MULTI[group]
        Model = M(*label.split('.'))
        inst = None
        if record_id:
            inst = Model.objects.filter(pk=record_id, **{fk: employee}).first()
            if inst is None:
                raise ValidationError({'detail': 'Record not found for this employee.'})
        if action == 'delete':
            if inst is None:
                raise ValidationError({'detail': 'Choose the record to delete.'})

            def run_del():
                with transaction.atomic():
                    if extra_model is not None:
                        extra_model.objects.filter(**{extra_key: inst.pk}).delete()
                    inst.delete()
                return {'deleted': True}
            return run_del
        core_ser = _core(core_cls, Model)
        core_keys = set(core_ser.Meta.fields)
        core_data = {k: v for k, v in data.items() if k in core_keys}
        extra_data = {k: v for k, v in data.items() if k not in core_keys}
        if group == 'family' and inst is None:
            core_data.setdefault('ef_company_expence', 0)
        cs = core_ser(inst, data=core_data, partial=inst is not None)
        es = None
        errors = {}
        if not cs.is_valid():
            errors.update(cs.errors)
        if extra_model is not None and extra_data:
            ex_inst = extra_model.objects.filter(**{extra_key: inst.pk}).first() if inst is not None else None
            es = extra_ser(ex_inst, data=extra_data, partial=True)
            if not es.is_valid():
                errors.update(es.errors)
        elif extra_data:
            errors.update({k: 'This field cannot be changed here.' for k in extra_data})
        if errors:
            raise ValidationError(errors)

        def run():
            with transaction.atomic():
                kw = {fk: employee}
                if inst is None and hasattr(Model, 'created_by_id'):
                    kw['created_by_id'] = uid
                if hasattr(Model, 'updated_by_id'):
                    kw['updated_by_id'] = uid
                obj = cs.save(**kw)
                out = {'id': obj.pk, **{k: getattr(obj, k + '_id', None) if k == 'document_type' else getattr(obj, k) for k in core_keys if k != 'emp_doc_document'}}
                if es is not None:
                    ex = es.save(**{extra_key: obj.pk, 'employee_id': employee.pk})
                    if group == 'bank' and ex.is_primary:
                        BankExtra.objects.filter(employee_id=employee.pk, is_primary=True).exclude(pk=ex.pk).update(is_primary=False)
                    out.update({k: v for k, v in extra_ser(ex).data.items() if k not in ('id',)})
            return out
        return run
    raise ValidationError({'group': f'Unknown group "{group}".'})


def validate_change(employee, group, record_id, data, action=None):
    """Same checks as apply_change, nothing saved. Raises ValidationError (field dict)."""
    _prepare(employee, group, record_id, data, action=action)
    return True


def apply_change(employee, group, record_id, data, user, action=None):
    """Write a validated change for any profile group (create when record_id is empty, delete with action='delete').
    Returns the saved record as a dict; raises ValidationError with a field dict."""
    run = _prepare(employee, group, record_id, data, action=action, user=user)
    return run()
