"""v1.13.0 – the employee profile as self service sees it: groups, fields, ESS policy, reading and applying changes.

EmployeeProfile (built in parallel by "empmaster") provides profile_groups(), validate_change() and apply_change().
They are imported lazily; when they are missing (or do not know a group) the small fallbacks below are used for the
core tables (employee master, family, bank, qualifications, job history, documents, skills).
"""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction

from AccessControl.ess_guard import (DEFAULT_POLICY, HIDDEN, HR, LOCKED, POLICIES, REQUEST, SELF, field_policy,
                                     policy_map)

M = apps.get_model

# ------------------------------------------------------------------------------------------------ group definitions
# field: (key, label, type)   types: text, date, number, bool, email, phone, fk:<label>, file, choice
GROUPS = [
    {'key': 'personal', 'label': 'Personal details', 'model': 'EmpManagement.emp_master', 'single': True, 'fields': [
        ('emp_code', 'Employee code', 'text'), ('emp_first_name', 'First name', 'text'), ('emp_middle_name', 'Middle name', 'text'),
        ('emp_last_name', 'Last name', 'text'), ('emp_gender', 'Gender', 'text'), ('emp_date_of_birth', 'Date of birth', 'date'),
        ('emp_nationality', 'Nationality', 'fk:Core.Nationality'), ('emp_marital_status', 'Marital status', 'text'),
        ('emp_relegion', 'Religion', 'fk'), ('emp_blood_group', 'Blood group', 'text'), ('emp_father_name', "Father's name", 'text'),
        ('emp_mother_name', "Mother's name", 'text'), ('person_id', 'MOHRE person ID', 'text'), ('emp_profile_pic', 'Photo', 'file')]},
    {'key': 'contact', 'label': 'Contact', 'model': 'EmpManagement.emp_master', 'single': True, 'fields': [
        ('emp_personal_email', 'Personal e-mail', 'email'), ('emp_company_email', 'Company e-mail', 'email'),
        ('emp_mobile_number_1', 'Mobile', 'phone'), ('emp_mobile_number_2', 'Other phone', 'phone')]},
    {'key': 'address', 'label': 'Address', 'model': 'EmpManagement.emp_master', 'single': True, 'fields': [
        ('emp_present_address', 'Present address', 'text'), ('emp_permenent_address', 'Permanent address', 'text'),
        ('emp_city', 'City', 'text'), ('emp_state_id', 'State / emirate', 'fk'), ('emp_country_id', 'Country', 'fk')]},
    {'key': 'identity', 'label': 'Identity documents (UAE)', 'model': 'EmployeeProfile.EmployeeIdentity', 'single': True, 'needs': 'EmployeeProfile', 'fields': [
        ('arabic_name', 'Name in Arabic', 'text'), ('title', 'Title', 'text'), ('preferred_name', 'Preferred name', 'text'),
        ('place_of_birth', 'Place of birth', 'text'),   # v1.13.0 (EmployeeProfile 0002)
        ('passport_no', 'Passport number', 'text'), ('passport_issue_date', 'Passport issue date', 'date'),
        ('passport_expiry_date', 'Passport expiry date', 'date'), ('passport_place_of_issue', 'Passport place of issue', 'text'),
        ('passport_country_id', 'Passport country', 'fk'), ('visa_type', 'Visa type', 'text'), ('visa_number', 'Visa number', 'text'),
        ('visa_uid', 'UID number', 'text'), ('visa_file_no', 'Visa file number', 'text'), ('visa_issue_date', 'Visa issue date', 'date'),
        ('visa_expiry_date', 'Visa expiry date', 'date'), ('visa_sponsor', 'Visa sponsor', 'text'),
        ('emirates_id', 'Emirates ID', 'text'), ('emirates_id_issue_date', 'Emirates ID issue date', 'date'),
        ('emirates_id_expiry_date', 'Emirates ID expiry date', 'date'), ('labour_card_no', 'Labour card number', 'text'),
        ('labour_card_expiry_date', 'Labour card expiry date', 'date'), ('work_permit_no', 'Work permit number', 'text'),
        ('work_permit_expiry_date', 'Work permit expiry date', 'date'), ('mohre_contract_type', 'MOHRE contract type', 'choice'),
        ('mohre_contract_no', 'MOHRE contract number', 'text'), ('mohre_contract_start', 'Contract start', 'date'),
        ('mohre_contract_end', 'Contract end', 'date'),
        ('driving_licence_no', 'Driving licence number', 'text'), ('driving_licence_issue_date', 'Driving licence issue date', 'date'),
        ('driving_licence_expiry_date', 'Driving licence expiry date', 'date'), ('driving_licence_emirate', 'Driving licence emirate', 'choice'),
        ('driving_licence_categories', 'Driving licence categories', 'text'),
        ('insurance_card_no', 'Insurance card number', 'text'),
        ('insurance_expiry_date', 'Insurance expiry date', 'date')]},
    {'key': 'emergency', 'label': 'Emergency contacts', 'model': 'EmployeeProfile.EmergencyContact', 'needs': 'EmployeeProfile', 'fields': [
        ('name', 'Name', 'text'), ('relation', 'Relation', 'text'), ('mobile', 'Mobile', 'phone'), ('alt_phone', 'Other phone', 'phone'),
        ('email', 'E-mail', 'email'), ('address', 'Address', 'text'), ('is_primary', 'Primary contact', 'bool')]},
    {'key': 'family', 'label': 'Family and dependents', 'model': 'EmpManagement.emp_family', 'fields': [
        ('ef_member_name', 'Name', 'text'), ('emp_relation', 'Relation', 'text'), ('ef_date_of_birth', 'Date of birth', 'date'),
        ('ef_company_expence', 'Company expense', 'number'),
        ('gender', 'Gender', 'text'), ('nationality_id', 'Nationality', 'fk'), ('passport_no', 'Passport number', 'text'),
        ('passport_expiry_date', 'Passport expiry', 'date'), ('emirates_id', 'Emirates ID', 'text'),
        ('emirates_id_expiry_date', 'Emirates ID expiry', 'date'), ('visa_number', 'Visa number', 'text'),
        ('visa_expiry_date', 'Visa expiry', 'date'), ('insured', 'Insured', 'bool'), ('visa_sponsored', 'Visa sponsored by company', 'bool')],
     'extra': ('EmployeeProfile.DependentExtra', 'family_id')},
    {'key': 'bank', 'label': 'Bank accounts', 'model': 'EmpManagement.EmployeeBankDetail', 'fields': [
        ('bank_name', 'Bank', 'text'), ('branch_name', 'Bank branch', 'text'), ('account_number', 'Account number', 'text'),
        ('iban_number', 'IBAN', 'text'), ('route_code', 'Routing code', 'text'), ('bank_address', 'Bank address', 'text'),
        ('wps_agent', 'WPS agent', 'text'), ('payment_mode', 'Payment mode', 'choice'), ('is_primary', 'Primary account', 'bool'),
        ('effective_from', 'Effective from', 'date')],
     'extra': ('EmployeeProfile.BankExtra', 'bank_detail_id')},
    {'key': 'qualifications', 'label': 'Qualifications', 'model': 'EmpManagement.EmpQualification', 'fields': [
        ('emp_qualification', 'Qualification', 'text'), ('emp_qf_instituition', 'Institution', 'text'), ('emp_qf_year', 'Year', 'date'),
        ('emp_qf_subject', 'Subject', 'text'), ('grade', 'Grade', 'text'), ('country_id', 'Country', 'fk'),
        ('attested', 'Attested', 'bool'), ('equivalency', 'Equivalency certificate', 'bool'), ('attachment', 'Certificate', 'file')],
     'extra': ('EmployeeProfile.QualificationExtra', 'qualification_id')},
    {'key': 'experience', 'label': 'Previous experience', 'model': 'EmpManagement.EmpJobHistory', 'fields': [
        ('emp_jh_company_name', 'Company', 'text'), ('emp_jh_designation', 'Designation', 'text'), ('emp_jh_from_date', 'From', 'date'),
        ('emp_jh_end_date', 'To', 'date'), ('emp_jh_leaving_salary_permonth', 'Last salary per month', 'number'),
        ('emp_jh_reason', 'Reason for leaving', 'text'), ('emp_jh_years_experiance', 'Years', 'number')]},
    {'key': 'skills', 'label': 'Skills', 'model': 'EmpManagement.EmployeeLangSkill', 'fields': [
        ('kind', 'Kind', 'choice'), ('skill', 'Skill', 'fk'), ('percentage', 'Level (%)', 'number')]},
    {'key': 'documents', 'label': 'Documents', 'model': 'EmpManagement.Emp_Documents', 'fields': [
        ('document_type', 'Document type', 'fk'), ('emp_doc_number', 'Number', 'text'), ('emp_doc_issued_date', 'Issued', 'date'),
        ('emp_doc_expiry_date', 'Expires', 'date'), ('emp_doc_document', 'File', 'file')]},
    {'key': 'org', 'label': 'Job and organisation', 'model': 'EmpManagement.emp_master', 'single': True, 'read_only': True, 'fields': [
        ('emp_branch_id', 'Branch', 'fk'), ('emp_dept_id', 'Department', 'fk'), ('emp_desgntn_id', 'Designation', 'fk'),
        ('emp_ctgry_id', 'Category', 'fk'), ('emp_reporting_manager', 'Reporting manager', 'fk'), ('work_location', 'Work location', 'fk'),
        ('visa_location', 'Visa location', 'fk'), ('emp_joined_date', 'Joining date', 'date'),
        ('emp_date_of_confirmation', 'Confirmation date', 'date'), ('emp_status', 'Active', 'bool'),
        ('emp_ot_applicable', 'Overtime applicable', 'bool'), ('emp_weekend_calendar', 'Weekend calendar', 'fk'),
        ('holiday_calendar', 'Holiday calendar', 'fk'), ('attendance_source', 'Attendance source', 'text'),
        ('barcode_number', 'Attendance card', 'text')]},
    {'key': 'employment', 'label': 'Employment status', 'model': 'EmployeeProfile.EmploymentInfo', 'single': True, 'read_only': True, 'fields': [
        ('status', 'Status', 'choice'), ('probation_end_date', 'Probation ends', 'date'), ('probation_status', 'Probation', 'choice'),
        ('date_of_leaving', 'Date of leaving', 'date'), ('leaving_reason', 'Reason for leaving', 'text')]},
    {'key': 'salary', 'label': 'Salary structure', 'model': 'PayrollManagement.EmployeeSalaryStructure', 'read_only': True, 'fields': [
        ('component', 'Component', 'text'), ('type', 'Type', 'text'), ('amount', 'Amount (AED)', 'number')]},
]
GROUP = {g['key']: g for g in GROUPS}
SYSTEM_FIELDS = ('users', 'is_ess', 'face_encoding', 'created_at', 'created_by', 'updated_at', 'updated_by', 'is_active')
RECORD_GROUPS = ('emergency', 'family', 'bank', 'qualifications', 'experience', 'skills', 'documents')
EDITABLE_GROUPS = ('personal', 'contact', 'address', 'identity') + RECORD_GROUPS
SKILL_MODELS = {'language': ('EmployeeLangSkill', 'language_skill', 'LanguageSkill'),
                'marketing': ('EmployeeMarketingSkill', 'marketing_skill', 'MarketingSkill'),
                'programming': ('EmployeeProgramSkill', 'program_skill', 'ProgrammingLanguageSkill')}


SOURCES = {
    'emp_nationality': 'Core.Nationality', 'nationality_id': 'Core.Nationality', 'emp_relegion': 'Core.ReligionMaster',
    'emp_country_id': 'Core.cntry_mstr', 'country_id': 'Core.cntry_mstr', 'passport_country_id': 'Core.cntry_mstr',
    'emp_state_id': 'Core.state_mstr', 'document_type': 'EmpManagement.document_type', 'skill': 'LearningPlus.Skill',
}
OPTION_SOURCES = {  # model label -> label attribute (lookups the self-service forms may list)
    'Core.Nationality': 'N_name', 'Core.ReligionMaster': 'religion', 'Core.cntry_mstr': 'country_name', 'Core.state_mstr': 'state_name',
    'EmpManagement.document_type': 'type_name', 'LearningPlus.Skill': 'name',
}


def field_meta(group, key):
    """{'options': [...]} for choice fields, {'source': 'App.Model'} for look-ups."""
    out = {}
    src = SOURCES.get(key)
    if src and src in OPTION_SOURCES and apps.is_installed(src.split('.')[0]):
        out['source'] = src
    g = GROUP.get(group) or {}
    names = [g.get('model')] + ([g['extra'][0]] if g.get('extra') else [])
    for n in names:
        if not n or not apps.is_installed(n.split('.')[0]):
            continue
        try:
            f = M(*n.split('.'))._meta.get_field(key)
        except Exception:
            continue
        if getattr(f, 'choices', None):
            out['options'] = [{'value': v, 'label': str(l)} for v, l in f.choices]
        break
    return out


def options(source):
    if source not in OPTION_SOURCES or not apps.is_installed(source.split('.')[0]):
        return None
    attr = OPTION_SOURCES[source]
    Model = M(*source.split('.'))
    qs = Model.objects.all()
    if any(f.name == 'is_active' for f in Model._meta.fields):
        qs = qs.filter(is_active=True)
    return [{'value': o.pk, 'label': getattr(o, attr, None) or str(o)} for o in qs.order_by(attr)[:1000]]


def ep_installed():
    return apps.is_installed('EmployeeProfile')


def ep_services():
    """EmployeeProfile.services when it exists, else None."""
    if not ep_installed():
        return None
    try:
        from EmployeeProfile import services
        return services
    except Exception:
        return None


def group_available(key):
    g = GROUP.get(key)
    return bool(g) and (not g.get('needs') or ep_installed())


def groups_definition():
    """Groups with fields; EmployeeProfile's list wins for field names / labels it defines."""
    out = []
    ep_fields = {}
    svc = ep_services()
    if svc and hasattr(svc, 'profile_groups'):
        try:
            for g in svc.profile_groups():
                ep_fields[g.get('key')] = [(f.get('key'), f.get('label') or f.get('key'), f.get('type') or 'text') for f in g.get('fields') or []]
        except Exception:
            ep_fields = {}
    for g in GROUPS:
        if not group_available(g['key']):
            continue
        fields = list(g['fields'])
        if g['key'] == 'skills' and skills_from_learning():
            fields = [('skill', 'Skill', 'fk'), ('level', 'Level (1-5)', 'number')]
        extra = g.get('extra')
        if extra and not ep_installed():
            base = {f.name for f in M(*g['model'].split('.'))._meta.get_fields()}
            fields = [f for f in fields if f[0] in base]
        if g['key'] in ep_fields:
            known = {f[0] for f in fields}
            fields += [f for f in ep_fields[g['key']] if f[0] and f[0] not in known and f[0] not in SYSTEM_FIELDS
                       and f[0] not in ('employee', 'employee_id', 'emp_id', 'family_id', 'bank_detail_id', 'qualification_id', 'id')]
        out.append(dict(g, fields=fields))
    return out


# ------------------------------------------------------------------------------------------------ policy
def policies(pm=None):
    """Full policy table for the settings screen."""
    pm = pm if pm is not None else policy_map()
    rows = []
    for g in groups_definition():
        rec = g['key'] in RECORD_GROUPS
        if rec:
            rows.append({'group': g['key'], 'group_label': g['label'], 'field': '', 'label': 'Add / remove records',
                         'policy': field_policy(g['key'], '', pm), 'default': DEFAULT_POLICY.get((g['key'], ''), HR),
                         'locked': (g['key'], '') in LOCKED or g.get('read_only', False)})
        for k, label, _t in g['fields']:
            pol = 'hr' if g.get('read_only') and field_policy(g['key'], k, pm) in (SELF, REQUEST) else field_policy(g['key'], k, pm)
            rows.append({'group': g['key'], 'group_label': g['label'], 'field': k, 'label': label, 'policy': pol,
                         'default': DEFAULT_POLICY.get((g['key'], k), DEFAULT_POLICY.get((g['key'], ''), HR)),
                         'locked': (g['key'], k) in LOCKED or g.get('read_only', False)})
    return rows


def effective(group, field, pm):
    g = GROUP.get(group) or {}
    pol = field_policy(group, field, pm)
    if g.get('read_only') and pol in (SELF, REQUEST):
        return HR
    return pol


# ------------------------------------------------------------------------------------------------ reading
def _disp(v):
    from django.db.models.fields.files import FieldFile
    if v is None:
        return None
    if isinstance(v, FieldFile):
        return v.name or None
    if hasattr(v, '_meta'):
        if v._meta.model_name == 'customuser':
            e = M('EmpManagement', 'emp_master').objects.filter(users=v).first()
            return person(e) if e else v.username
        if v._meta.model_name == 'emp_master':
            return person(v)
        for a in ('branch_name', 'dept_name', 'desgntn_job_title', 'ctgry_title', 'N_name', 'religion', 'country_name', 'state_name',
                  'type_name', 'language', 'marketing', 'programming_language', 'name', 'calendar_name', 'title'):
            if getattr(v, a, None):
                return getattr(v, a)
        return str(v)
    if isinstance(v, (date,)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def person(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)
    return f'{n} ({e.emp_code})' if n else e.emp_code


def mask(v, keep=4):
    s = re.sub(r'\s+', '', str(v or ''))
    if not s:
        return s
    return '•' * max(len(s) - keep, 0) + s[-keep:]


def raw_value(obj, key):
    """JSON-safe raw value (ids for relations)."""
    try:
        f = obj._meta.get_field(key)
    except Exception:
        return getattr(obj, key, None)
    if f.is_relation and not f.many_to_many:
        return getattr(obj, f.attname, None)
    v = getattr(obj, key, None)
    from django.db.models.fields.files import FieldFile
    if isinstance(v, FieldFile):
        return v.name or None
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _get_extra(g, rec_id):
    extra = g.get('extra')
    if not extra or not ep_installed():
        return None
    try:
        return M(*extra[0].split('.')).objects.filter(**{extra[1]: rec_id}).first()
    except Exception:
        return None


def _field_value(objs, key):
    for o in objs:
        if o is None:
            continue
        try:
            o._meta.get_field(key)
        except Exception:
            continue
        return o, key
    return None, key


def _row(g, objs, pm, masked):
    fields = []
    for key, label, typ in g['fields']:
        pol = effective(g['key'], key, pm)
        if pol == HIDDEN:
            continue
        o, k = _field_value(objs, key)
        if o is None and key not in ('kind', 'skill', 'component', 'type', 'amount'):
            raw, disp = None, None
        elif o is None:
            raw = disp = None
        else:
            raw, disp = raw_value(o, k), _disp(getattr(o, k, None))
            try:
                if o._meta.get_field(k).choices and raw not in (None, ''):
                    disp = getattr(o, f'get_{k}_display')()
            except Exception:
                pass
        if masked and g['key'] == 'bank' and key in ('iban_number', 'account_number') and disp:
            disp = mask(disp)
            raw = disp
        fields.append({'key': key, 'label': label, 'type': typ, 'policy': pol, 'value': raw, 'display': disp, **field_meta(g['key'], key)})
    return fields


def employee_identity(emp):
    if not ep_installed():
        return None
    try:
        return M('EmployeeProfile', 'EmployeeIdentity').objects.filter(employee_id=emp.pk).first()
    except Exception:
        return None


def records(emp, key):
    """Model rows of a record group."""
    if key == 'family':
        return list(M('EmpManagement', 'emp_family').objects.filter(emp_id=emp))
    if key == 'bank':
        return list(M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=emp))
    if key == 'qualifications':
        return list(M('EmpManagement', 'EmpQualification').objects.filter(emp_id=emp))
    if key == 'experience':
        return list(M('EmpManagement', 'EmpJobHistory').objects.filter(emp_id=emp).order_by('-emp_jh_from_date'))
    if key == 'documents':
        return list(M('EmpManagement', 'Emp_Documents').objects.filter(emp_id=emp, is_active=True).select_related('document_type').order_by('emp_doc_expiry_date'))   # v1.13.0: hide replaced copies
    if key == 'emergency' and ep_installed():
        try:
            return list(M('EmployeeProfile', 'EmergencyContact').objects.filter(employee_id=emp.pk).order_by('-is_primary', 'id'))
        except Exception:
            return []
    return []


def skills_from_learning():
    return ep_installed() and apps.is_installed('LearningPlus') and _ep_keys('skills') is not None


def skills(emp):
    out = []
    if skills_from_learning():
        ES = M('LearningPlus', 'EmployeeSkill')
        for s in ES.objects.filter(employee_id=emp.pk).select_related('skill'):
            out.append({'id': s.pk, 'kind': 'skill', 'skill': s.skill_id, 'skill_display': s.skill.name, 'level': s.level, 'percentage': None})
        return out
    for kind, (mname, fk, _m) in SKILL_MODELS.items():
        for s in M('EmpManagement', mname).objects.filter(emp_id=emp):
            out.append({'id': s.pk, 'kind': kind, 'skill': getattr(s, f'{fk}_id'), 'skill_display': s.value or _disp(getattr(s, fk)),
                        'percentage': float(s.percentage) if s.percentage is not None else None})
    return out


def salary_rows(emp):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    today = date.today()
    rows = []
    for s in ESS.objects.filter(employee=emp, is_active=True).select_related('component').order_by('component__component_type', 'component__name'):
        if s.valid_from and s.valid_from > today:
            continue
        if getattr(s, 'valid_until', None) and s.valid_until < today:
            continue
        rows.append({'component': s.component.name, 'type': s.component.get_component_type_display(), 'category': s.component.payroll_category,
                     'value_type': s.component.component_value_type, 'amount': float(s.amount or 0)})
    return rows


def salary_figures(emp):
    rows = salary_rows(emp)
    add = sum(r['amount'] for r in rows if r['type'] == 'Addition' and r['value_type'] == 'fixed')
    ded = sum(r['amount'] for r in rows if r['type'] == 'Deduction' and r['value_type'] == 'fixed')
    basic = sum(r['amount'] for r in rows if r['category'] == 'basic')
    housing = sum(r['amount'] for r in rows if r['category'] in ('hra', 'housing_allowance'))
    transport = sum(r['amount'] for r in rows if r['category'] == 'transport_allowance')
    return {'gross': round(add, 2), 'deductions': round(ded, 2), 'net': round(add - ded, 2), 'basic': round(basic, 2),
            'housing': round(housing, 2), 'transport': round(transport, 2), 'other': round(add - basic - housing - transport, 2), 'rows': rows}


def employment(emp):
    info = None
    if ep_installed():
        try:
            info = M('EmployeeProfile', 'EmploymentInfo').objects.filter(employee_id=emp.pk).first()
        except Exception:
            info = None
    days = getattr(emp.emp_branch_id, 'probation_period_days', 0) if emp.emp_branch_id_id else 0
    prob_end = (emp.emp_joined_date + timedelta(days=days)) if (emp.emp_joined_date and days) else None
    return {'status': getattr(info, 'status', None) or ('active' if emp.is_active else 'left'),
            'probation_end_date': (getattr(info, 'probation_end_date', None) or prob_end),
            'probation_status': getattr(info, 'probation_status', None) or (
                ('confirmed' if emp.emp_date_of_confirmation or (prob_end and prob_end <= date.today()) else 'on_probation') if prob_end else None),
            'date_of_leaving': getattr(info, 'date_of_leaving', None), 'leaving_reason': getattr(info, 'leaving_reason', '') if info else '',
            'joining_date': emp.emp_joined_date, 'confirmation_date': emp.emp_date_of_confirmation}


def read_profile(emp, masked=True):
    """All groups for 'My profile' (employee view: bank numbers masked, hidden fields left out)."""
    pm = policy_map()
    out = {'employee_id': emp.pk, 'employee': person(emp), 'photo': emp.emp_profile_pic.name if emp.emp_profile_pic else None, 'groups': []}
    ident = employee_identity(emp)
    for g in groups_definition():
        key = g['key']
        item = {'key': key, 'label': g['label'], 'read_only': bool(g.get('read_only')), 'single': bool(g.get('single')),
                'record_policy': effective(key, '', pm) if key in RECORD_GROUPS else None}
        if key in ('personal', 'contact', 'address', 'org'):
            objs = [emp]
            if key == 'personal' and ident is not None:
                objs = [emp, ident]
            item['fields'] = _row(g, objs, pm, masked)
            if key == 'org':
                item['fields'] = [f for f in item['fields']]
        elif key == 'identity':
            item['fields'] = _row(g, [ident], pm, masked) if ident is not None else _row(g, [], pm, masked)
        elif key == 'employment':
            e = employment(emp)
            item['fields'] = [{'key': k, 'label': label, 'type': t, 'policy': effective(key, k, pm), 'value': _disp(e.get(k)), 'display': _disp(e.get(k))}
                              for k, label, t in g['fields'] if effective(key, k, pm) != HIDDEN]
            item['fields'] += [{'key': 'joining_date', 'label': 'Joining date', 'type': 'date', 'policy': HR, 'value': _disp(e['joining_date']), 'display': _disp(e['joining_date'])},
                               {'key': 'confirmation_date', 'label': 'Confirmation date', 'type': 'date', 'policy': HR, 'value': _disp(e['confirmation_date']), 'display': _disp(e['confirmation_date'])}]
        elif key == 'salary':
            if effective('salary', '', pm) == HIDDEN:
                continue
            fig = salary_figures(emp)
            item['rows'] = fig['rows']
            item['totals'] = {k: fig[k] for k in ('gross', 'deductions', 'net')}
        elif key == 'skills' and skills_from_learning():
            item['records'] = [{'id': s['id'], 'kind': 'skill', 'fields': [
                {'key': 'skill', 'label': 'Skill', 'type': 'fk', 'policy': effective(key, 'skill', pm), 'value': s['skill'], 'display': s['skill_display']},
                {'key': 'level', 'label': 'Level (1-5)', 'type': 'number', 'policy': effective(key, 'level', pm), 'value': s['level'], 'display': s['level']}]}
                for s in skills(emp)]
        elif key == 'skills':
            item['records'] = [{'id': s['id'], 'kind': s['kind'], 'fields': [
                {'key': 'kind', 'label': 'Kind', 'type': 'choice', 'policy': effective(key, 'kind', pm), 'value': s['kind'], 'display': s['kind'].title()},
                {'key': 'skill', 'label': 'Skill', 'type': 'fk', 'policy': effective(key, 'skill', pm), 'value': s['skill'], 'display': s['skill_display']},
                {'key': 'percentage', 'label': 'Level (%)', 'type': 'number', 'policy': effective(key, 'percentage', pm), 'value': s['percentage'], 'display': s['percentage']}]}
                for s in skills(emp)]
        else:
            recs = []
            for r in records(emp, key):
                ex = _get_extra(g, r.pk)
                row = {'id': r.pk, 'fields': _row(g, [r, ex], pm, masked)}
                if key == 'documents':
                    row['expiry'] = expiry_badge(r.emp_doc_expiry_date)
                    row['has_file'] = bool(r.emp_doc_document)
                recs.append(row)
            item['records'] = recs
        if effective(key, '', pm) == HIDDEN and key not in ('personal',):
            continue
        out['groups'].append(item)
    mgr = emp.emp_reporting_manager
    out['manager'] = _disp(mgr) if mgr else None
    return out


def expiry_badge(d, warn_days=60):
    if not d:
        return {'state': 'none', 'label': 'No expiry', 'days': None}
    days = (d - date.today()).days
    if days < 0:
        return {'state': 'expired', 'label': f'Expired {-days} day{"s" if days != -1 else ""} ago', 'days': days}
    if days <= warn_days:
        return {'state': 'expiring', 'label': f'Expires in {days} day{"s" if days != 1 else ""}', 'days': days}
    return {'state': 'valid', 'label': f'Valid until {d:%d %b %Y}', 'days': days}


# ------------------------------------------------------------------------------------------------ changes
def get_record(emp, group, record_id, kind=None):
    if group in ('personal', 'contact', 'address'):
        return emp
    if group == 'identity':
        return employee_identity(emp)
    if group == 'skills' and skills_from_learning():
        return M('LearningPlus', 'EmployeeSkill').objects.filter(employee_id=emp.pk, pk=record_id).first() if record_id else None
    if group == 'skills':
        mname = SKILL_MODELS.get(kind or 'language', SKILL_MODELS['language'])[0]
        return M('EmpManagement', mname).objects.filter(emp_id=emp, pk=record_id).first() if record_id else None
    if record_id is None:
        return None
    for r in records(emp, group):
        if r.pk == int(record_id):
            return r
    return None


def old_values(emp, group, record_id, keys, kind=None):
    rec = get_record(emp, group, record_id, kind)
    if rec is None:
        return {}
    g = GROUP.get(group, {})
    ex = _get_extra(g, rec.pk) if group not in ('personal', 'contact', 'address', 'identity') else None
    out = {}
    for k in keys:
        o, kk = _field_value([rec, ex] + ([employee_identity(emp)] if group == 'personal' else []), k)
        if o is not None:
            out[k] = raw_value(o, kk)
    return out


def _iban_ok(iban):
    s = re.sub(r'\s+', '', str(iban or '')).upper()
    if not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{10,30}', s):
        return False
    r = s[4:] + s[:4]
    n = ''.join(str(int(ch, 36)) for ch in r)
    return int(n) % 97 == 1


def _fallback_validate(emp, group, record_id, data, action='update'):
    errors = {}
    if group == 'bank':
        iban = data.get('iban_number')
        if iban not in (None, ''):
            s = re.sub(r'\s+', '', str(iban)).upper()
            if s.startswith('AE') and len(s) != 23:
                errors['iban_number'] = 'A UAE IBAN has 23 characters (AE + 21).'
            elif not _iban_ok(s):
                errors['iban_number'] = 'This IBAN is not valid – check the digits (the check digits do not match).'
            else:
                data['iban_number'] = s
        if action == 'create' and not data.get('account_number'):
            errors['account_number'] = 'Enter the account number.'
    if group in ('contact',):
        for k in ('emp_mobile_number_1', 'emp_mobile_number_2'):
            v = (data.get(k) or '').strip() if isinstance(data.get(k), str) else data.get(k)
            if v and (not re.fullmatch(r'\+?[0-9 ()\-]{6,24}', str(v)) or len(re.sub(r'\D', '', str(v))) < 7):
                errors[k] = 'Enter a valid phone number (digits, spaces, + and -).'
        v = data.get('emp_personal_email')
        if v and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', str(v)):
            errors['emp_personal_email'] = 'Enter a valid e-mail address.'
    if group == 'documents':
        a, b = data.get('emp_doc_issued_date'), data.get('emp_doc_expiry_date')
        if a and b and str(b) <= str(a):
            errors['emp_doc_expiry_date'] = 'The expiry date must be after the issue date.'
    if group == 'experience':
        a, b = data.get('emp_jh_from_date'), data.get('emp_jh_end_date')
        if a and b and str(b) < str(a):
            errors['emp_jh_end_date'] = 'The end date cannot be before the start date.'
    if group == 'family':
        d = data.get('ef_date_of_birth')
        if d and str(d) > date.today().isoformat():
            errors['ef_date_of_birth'] = 'The date of birth cannot be in the future.'
    if errors:
        raise ValidationError(errors)


def _set_fields(obj, data):
    from django.db.models import FileField
    for k, v in data.items():
        try:
            f = obj._meta.get_field(k)
        except Exception:
            continue
        if f.is_relation and not f.many_to_many:
            setattr(obj, f.attname, int(v) if v not in (None, '') else None)
        elif isinstance(f, FileField):
            if hasattr(v, 'read'):
                getattr(obj, k).save(getattr(v, 'name', 'file'), v, save=False)
        else:
            setattr(obj, k, None if (v == '' and f.null) else v)


def _fallback_apply(emp, group, record_id, data, user, action='update'):
    data = dict(data)
    _fallback_validate(emp, group, record_id, data, action)
    kind = data.pop('kind', None)
    with transaction.atomic():
        if group in ('personal', 'contact', 'address'):
            _set_fields(emp, data)
            emp.updated_by_id = getattr(user, 'pk', None)
            emp.full_clean(exclude=[f.name for f in emp._meta.fields if f.name not in data])
            emp.save()
            return emp.pk
        if group == 'skills':
            mname, fk, _m = SKILL_MODELS.get(kind or 'language', SKILL_MODELS['language'])
            Model = M('EmpManagement', mname)
            if action == 'delete':
                Model.objects.filter(emp_id=emp, pk=record_id).delete()
                return record_id
            obj = Model.objects.filter(emp_id=emp, pk=record_id).first() if action == 'update' else Model(emp_id=emp, created_by_id=getattr(user, 'pk', None))
            if obj is None:
                raise ValidationError({'record_id': 'This skill no longer exists.'})
            if 'skill' in data:
                setattr(obj, f'{fk}_id', int(data['skill']) if data['skill'] not in (None, '') else None)
            if 'percentage' in data:
                obj.percentage = data['percentage'] if data['percentage'] not in ('', None) else None
            obj.save()
            return obj.pk
        g = GROUP[group]
        app, mname = g['model'].split('.')
        Model = M(app, mname)
        if app == 'EmployeeProfile':
            raise ValidationError({'group': 'This part of the profile needs the Employee profile module.'})
        link = 'employee' if mname == 'EmployeeBankDetail' else 'emp_id'
        if action == 'delete':
            obj = Model.objects.filter(**{link: emp, 'pk': record_id}).first()
            if obj is None:
                raise ValidationError({'record_id': 'This record no longer exists.'})
            obj.delete()
            return record_id
        if action == 'create':
            obj = Model(**{link: emp})
            if hasattr(obj, 'created_by_id'):
                obj.created_by_id = getattr(user, 'pk', None)
        else:
            obj = Model.objects.filter(**{link: emp, 'pk': record_id}).first()
            if obj is None:
                raise ValidationError({'record_id': 'This record no longer exists.'})
        base = {k: v for k, v in data.items() if k in {f.name for f in Model._meta.fields}}
        if group == 'family' and action == 'create':
            base.setdefault('ef_company_expence', 0)
        _set_fields(obj, base)
        if hasattr(obj, 'updated_by_id'):
            obj.updated_by_id = getattr(user, 'pk', None)
        excl = [f.name for f in Model._meta.fields if f.name in ('created_by', 'updated_by', link) or (action == 'update' and f.name not in base)]
        obj.full_clean(exclude=excl)
        obj.save()
        return obj.pk


def _ep_keys(group):
    svc = ep_services()
    if svc is None or not hasattr(svc, 'group_fields'):
        return None
    try:
        return set(svc.group_fields(group))
    except Exception:
        return None


def _to_django(exc):
    from rest_framework.exceptions import ValidationError as DRFValidationError
    if isinstance(exc, DRFValidationError):
        d = exc.detail
        if isinstance(d, dict):
            return ValidationError({k: [str(x) for x in (v if isinstance(v, list) else [v])] for k, v in d.items()})
        return ValidationError({'detail': [str(x) for x in (d if isinstance(d, list) else [d])]})
    return exc


def _split(group, data):
    """(keys EmployeeProfile handles, the rest) – None when EmployeeProfile does not handle the group."""
    if group == 'skills' and not skills_from_learning():
        return None
    keys = _ep_keys(group)
    if keys is None or getattr(ep_services(), 'apply_change', None) is None:
        return None
    ep = {k: v for k, v in data.items() if k in keys}
    rest = {k: v for k, v in data.items() if k not in keys}
    return ep, rest


def validate_change(emp, group, record_id, data, action='update'):
    data = dict(data)
    sp = _split(group, data)
    if sp is None:
        return _fallback_validate(emp, group, record_id, data, action)
    ep, rest = sp
    if rest and group not in ('personal', 'contact', 'address'):
        raise ValidationError({k: ['This field cannot be changed here.'] for k in rest})
    if rest:
        _fallback_validate(emp, group, record_id, rest, action)
    if ep or action in ('create', 'delete'):
        try:
            ep_services().validate_change(emp, group, record_id if action != 'create' else None, ep, action=action if action == 'delete' else None)
        except ValidationError:
            raise
        except Exception as exc:
            raise _to_django(exc)


def apply_change(emp, group, record_id, data, user, action='update'):
    """Write a change for any group (EmployeeProfile helpers when available). Returns the record id."""
    data = dict(data)
    sp = _split(group, data)
    if sp is None:
        return _fallback_apply(emp, group, record_id, data, user, action)
    ep, rest = sp
    with transaction.atomic():
        rid = record_id
        if ep or action in ('create', 'delete'):
            try:
                res = ep_services().apply_change(emp, group, record_id if action != 'create' else None, ep, user,
                                                 action=action if action == 'delete' else None)
            except ValidationError:
                raise
            except Exception as exc:
                raise _to_django(exc)
            if isinstance(res, dict) and isinstance(res.get('id'), int):
                rid = res['id']
        if rest and group in ('personal', 'contact', 'address'):
            emp.refresh_from_db()
            _fallback_apply(emp, group, None, rest, user, 'update')
    return rid
