"""v1.27.0 – copy settings to sister companies, and move an employee to a sister company.

Copy settings: leave types, leave policies (with their lines), salary components, letter templates and form designs
(form settings and extra fields) of one company are copied to the others. A row is matched by its code (or name);
"add missing only" (default) never changes what a company already has, "update existing" overwrites the matched rows.
Only company-wide rows are copied (rows of one branch stay in their company).

Transfer: the employee is made in the target company (same personal data, bank account, salary by component code), the
service date is kept if asked, the record in the source company is closed (inactive from the effective date), both
records get a note, and the two are linked in CompanyTransfer. Documents, leave balances and history stay in the
source company (leave openings can be loaded in the target, Leave → Leave policies → Load opening balances).
"""
import logging

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import schema_context

log = logging.getLogger(__name__)
M = apps.get_model

KINDS = [
    ('leave_types', 'Leave types'),
    ('leave_policies', 'Leave policies'),
    ('salary_components', 'Salary components'),
    ('letter_templates', 'Letter templates'),
    ('form_designs', 'Form designs and extra fields'),
    ('numbering', 'Numbering pattern and employee code'),   # v1.56.0 setup guide
]


def _plain(obj, skip=()):
    """Concrete values of a row without its id and relations (relations are mapped by the caller)."""
    d = {}
    for f in obj._meta.concrete_fields:
        if f.primary_key or f.name in skip or f.is_relation:
            continue
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            continue
        d[f.attname] = getattr(obj, f.attname)
    return d


def _ci(v):
    return (v or '').strip().lower()


def _find(qs, code_field, name_field, row):
    code = _ci(row.get(code_field)) if code_field else ''
    if code:
        for o in qs:
            if _ci(getattr(o, code_field, '')) == code:
                return o
    name = _ci(row.get(name_field))
    for o in qs:
        if _ci(getattr(o, name_field, '')) == name:
            return o
    return None


# ------------------------------------------------------------------ read the source company
def _read(kinds):
    out = {}
    if 'leave_types' in kinds or 'leave_policies' in kinds:
        out['leave_types'] = [_plain(t) for t in M('calendars', 'leave_type').objects.filter(branch__isnull=True)]
    if 'leave_policies' in kinds:
        LT = {t.id: (t.code, t.name) for t in M('calendars', 'leave_type').objects.all()}
        CAT = {c.id: c.ctgry_title for c in M('OrganisationManager', 'ctgry_master').objects.all()}
        pols = []
        for p in M('LeavePolicy', 'LeavePolicy').objects.all():
            d = _plain(p)
            d['category_ids'] = [CAT.get(int(i)) for i in (p.category_ids or []) if str(i).isdigit() and int(i) in CAT]
            d['_lines'] = []
            for line in p.lines.all():
                ld = _plain(line, skip=('policy',))
                ld['leave_type_id'] = LT.get(line.leave_type_id)
                d['_lines'].append(ld)
            pols.append(d)
        out['leave_policies'] = pols
    if 'salary_components' in kinds:
        out['salary_components'] = [_plain(c) for c in M('PayrollManagement', 'SalaryComponent').objects.filter(branch__isnull=True)]
    if 'letter_templates' in kinds:
        out['letter_templates'] = [_plain(t, skip=('updated_by_id',)) for t in M('SelfService', 'LetterTemplate').objects.filter(branch_id__isnull=True)]
    if 'numbering' in kinds:   # v1.56.0 – the pattern (with its codes) and the company's employee code rule; counters are not copied
        P = M('ZeoAdmin', 'NumberingPattern').objects.order_by('id').first()
        out['numbering'] = _plain(P, skip=('applied_at', 'applied_by_id')) if P else None
        ec = M('EmployeeProfile', 'EmployeeCodeSetting').objects.filter(branch_id__isnull=True).first()
        out['employee_code'] = {'prefix': ec.prefix, 'padding': ec.padding, 'enabled': ec.enabled} if ec else None
    if 'form_designs' in kinds:
        out['form_settings'] = [_plain(f) for f in M('DataTools', 'FormSetting').objects.all()]
        out['field_defs'] = [_plain(f) for f in M('Chatter', 'FieldDef').objects.all()]
    return out


# ------------------------------------------------------------------ write into a target company
def _upsert(Model, rows, match, overwrite, fix=None, after=None):
    """match(existing_qs, row) -> existing or None. Returns (added, updated, skipped)."""
    added = updated = skipped = 0
    existing = list(Model.objects.all())
    for row in rows:
        data = {k: v for k, v in row.items() if not k.startswith('_')}
        if fix:
            data = fix(data)
            if data is None:
                skipped += 1
                continue
        o = match(existing, data)
        if o is None:
            o = Model(**data)
            o.save()
            existing.append(o)
            added += 1
        elif overwrite:
            for k, v in data.items():
                setattr(o, k, v)
            o.save()
            updated += 1
        else:
            skipped += 1
            if after:
                continue
        if after:
            after(o, row)
    return added, updated, skipped


def _write(data, kinds, overwrite):
    res = {}

    def done(kind, t):
        res[kind] = {'added': t[0], 'updated': t[1], 'kept': t[2]}

    if 'leave_types' in kinds and data.get('leave_types') is not None:
        LT = M('calendars', 'leave_type')
        done('leave_types', _upsert(LT.objects.filter(branch__isnull=True).model, data['leave_types'],
                                    lambda ex, r: _find([e for e in ex if e.branch_id is None], 'code', 'name', r), overwrite))
    if 'leave_policies' in kinds:
        LT = list(M('calendars', 'leave_type').objects.all())
        CAT = {_ci(c.ctgry_title): c.id for c in M('OrganisationManager', 'ctgry_master').objects.all()}
        Pol, Line = M('LeavePolicy', 'LeavePolicy'), M('LeavePolicy', 'LeavePolicyLine')
        missing_types = set()

        def type_id(pair):
            if not pair:
                return None
            code, name = pair
            t = _find(LT, 'code', 'name', {'code': code, 'name': name})
            if t is None:
                missing_types.add(name or code)
            return t.id if t else None

        def fix(d):
            d['category_ids'] = [CAT[_ci(n)] for n in (d.get('category_ids') or []) if n and _ci(n) in CAT]
            return d

        def lines(pol, row):
            if not overwrite and pol.lines.exists():
                return
            pol.lines.all().delete()
            for ld in row.get('_lines') or []:
                ld = dict(ld)
                ld['leave_type_id'] = type_id(ld.get('leave_type_id'))
                if ld['leave_type_id'] is None:
                    continue
                Line.objects.create(policy=pol, **ld)

        t = _upsert(Pol, data.get('leave_policies') or [], lambda ex, r: _find(ex, 'code', 'name', r), overwrite, fix=fix, after=lines)
        done('leave_policies', t)
        if missing_types:
            res['leave_policies']['note'] = 'Leave types not in this company (copy the leave types too): ' + ', '.join(sorted(missing_types))
    if 'salary_components' in kinds:
        SC = M('PayrollManagement', 'SalaryComponent')
        done('salary_components', _upsert(SC, data.get('salary_components') or [],
                                          lambda ex, r: _find([e for e in ex if e.branch_id is None], 'code', 'name', r), overwrite))
    if 'letter_templates' in kinds:
        T = M('SelfService', 'LetterTemplate')
        done('letter_templates', _upsert(T, data.get('letter_templates') or [],
                                         lambda ex, r: next((e for e in ex if e.branch_id is None and e.letter_type == r.get('letter_type') and e.language == r.get('language')), None),
                                         overwrite))
    if 'form_designs' in kinds:
        FS, FD = M('DataTools', 'FormSetting'), M('Chatter', 'FieldDef')
        a = _upsert(FS, data.get('form_settings') or [], lambda ex, r: next((e for e in ex if e.form == r.get('form')), None), overwrite)
        b = _upsert(FD, data.get('field_defs') or [], lambda ex, r: next((e for e in ex if e.model == r.get('model') and e.name == r.get('name')), None), overwrite)
        done('form_designs', (a[0] + b[0], a[1] + b[1], a[2] + b[2]))
    if 'numbering' in kinds and data.get('numbering'):
        P = M('ZeoAdmin', 'NumberingPattern')
        row = P.objects.order_by('id').first()
        if row is None or overwrite or not row.active:
            row = row or P()
            for k, v in data['numbering'].items():
                setattr(row, k, v)
            row.save()
            t = (1, 0, 0) if row.pk else (0, 1, 0)
        else:
            t = (0, 0, 1)
        ecd = data.get('employee_code')
        if ecd:
            EC = M('EmployeeProfile', 'EmployeeCodeSetting')
            ec = EC.objects.filter(branch_id__isnull=True).first()
            if ec is None or overwrite or not ec.enabled:
                ec = ec or EC(branch_id=None, next_number=1)
                ec.prefix, ec.padding, ec.enabled = ecd['prefix'], ecd['padding'], ecd['enabled']
                ec.save()
        done('numbering', t)
    return res


def copy_settings(group, user, source_schema, targets, kinds, overwrite=False):
    from .models import SettingsCopy
    from .scope import company_in
    src = company_in(group, source_schema)
    kinds = [k for k, _ in KINDS if k in (kinds or [])]
    if not kinds:
        raise ValueError('Choose what to copy.')
    tcomps = [company_in(group, t) for t in (targets or []) if t != source_schema]
    if not tcomps:
        raise ValueError('Choose the companies to copy to.')
    with schema_context(src.schema_name):
        data = _read(kinds)
    result = {}
    for comp in tcomps:
        try:
            with schema_context(comp.schema_name):
                with transaction.atomic():
                    result[comp.name] = _write(data, kinds, overwrite)
        except Exception as e:
            log.exception('copy settings to %s', comp.schema_name)
            result[comp.name] = {'error': str(e)[:300]}
    SettingsCopy.objects.create(source_schema=src.schema_name, targets=[c.schema_name for c in tcomps], kinds=kinds,
                                overwrite=bool(overwrite), result=result, by=user)
    return {'source': src.name, 'kinds': kinds, 'overwrite': bool(overwrite), 'result': result}


# ------------------------------------------------------------------ transfer to a sister company
EMP_SKIP = ('id', 'emp_code', 'users', 'emp_reporting_manager', 'emp_reports_to', 'created_by', 'updated_by',
            'emp_branch_id', 'work_location', 'visa_location', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id',
            'emp_weekend_calendar', 'holiday_calendar', 'is_active', 'emp_status', 'is_ess', 'emp_date_of_confirmation')
SHARED_FKS = ('emp_nationality', 'emp_country_id', 'emp_state_id', 'emp_relegion')   # Core tables are shared


def _org_choices():
    O = lambda m, f: [{'id': o.id, 'name': getattr(o, f)} for o in M('OrganisationManager', m).objects.all().order_by(f)]
    return {'branches': O('brnch_mstr', 'branch_name'), 'departments': O('dept_master', 'dept_name'),
            'designations': O('desgntn_master', 'desgntn_job_title'), 'categories': O('ctgry_master', 'ctgry_title')}


def transfer_form(group, source_schema, emp_id, target_schema):
    """Employee summary and the target company's lists, with the matching entries proposed."""
    from .scope import company_in
    src, tgt = company_in(group, source_schema), company_in(group, target_schema)
    with schema_context(src.schema_name):
        e = M('EmpManagement', 'emp_master').objects.select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id', 'emp_ctgry_id').get(pk=emp_id)
        emp = {'id': e.id, 'emp_code': e.emp_code, 'name': ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x),
               'joined': e.emp_joined_date.isoformat() if e.emp_joined_date else None, 'active': bool(e.is_active),
               'branch': getattr(e.emp_branch_id, 'branch_name', ''), 'department': getattr(e.emp_dept_id, 'dept_name', ''),
               'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', ''), 'category': getattr(e.emp_ctgry_id, 'ctgry_title', ''),
               'has_login': bool(e.users_id), 'salary_lines': M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee=e, is_active=True).count(),
               'bank_accounts': M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=e).count()}
    with schema_context(tgt.schema_name):
        lists = _org_choices()
        E = M('EmpManagement', 'emp_master')
        code_free = not E.objects.filter(emp_code=emp['emp_code']).exists()
    prop = {}
    for k, key in (('branch', 'branches'), ('department', 'departments'), ('designation', 'designations'), ('category', 'categories')):
        hit = next((o for o in lists[key] if _ci(o['name']) == _ci(emp[k])), None)
        prop[k] = hit['id'] if hit else (lists[key][0]['id'] if len(lists[key]) == 1 else None)
    return {'employee': emp, 'target': {'company': tgt.name, 'schema_name': tgt.schema_name, **lists},
            'proposed': dict(prop, emp_code=emp['emp_code'] if code_free else f"{emp['emp_code']}-T", code_free=code_free,
                             effective_date=timezone.localdate().isoformat(), keep_service_date=True)}


def transfer(group, user, source_schema, emp_id, target_schema, opts):
    from UserManagement.models import company as Company
    from .models import CompanyTransfer
    from .scope import company_in
    src, tgt = company_in(group, source_schema), company_in(group, target_schema)
    if src.schema_name == tgt.schema_name:
        raise ValueError('Choose another company.')
    eff = opts.get('effective_date') or timezone.localdate().isoformat()
    eff = timezone.datetime.fromisoformat(str(eff)).date()
    keep = bool(opts.get('keep_service_date', True))
    new_code = (opts.get('emp_code') or '').strip()
    E = M('EmpManagement', 'emp_master')
    # read in the source
    with schema_context(src.schema_name):
        e = E.objects.get(pk=emp_id)
        if not e.is_active:
            raise ValueError('This employee is not active in the source company.')
        base = {}
        for f in e._meta.concrete_fields:
            if f.name in EMP_SKIP or f.primary_key:
                continue
            if f.is_relation and f.name not in SHARED_FKS:
                continue
            base[f.attname] = getattr(e, f.attname)
        banks = [_plain(b, skip=('employee',)) for b in M('EmpManagement', 'EmployeeBankDetail').objects.filter(employee=e)]
        salary = [(s.component.code, s.component.name, s.amount, _plain(s.component)) for s in
                  M('PayrollManagement', 'EmployeeSalaryStructure').objects.select_related('component').filter(employee=e, is_active=True)]
        login = e.users
        name = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)
        code = e.emp_code
    new_code = new_code or code
    result = {'salary_lines': 0, 'components_added': [], 'bank_accounts': 0, 'login': ''}
    rec = CompanyTransfer.objects.create(source_schema=src.schema_name, source_company=src.name, source_employee_id=emp_id,
                                         target_schema=tgt.schema_name, target_company=tgt.name, emp_code=code, target_code=new_code,
                                         name=name, effective_date=eff, keep_service_date=keep, options=opts, by=user, state='running')
    try:
        # make the employee in the target
        with schema_context(tgt.schema_name):
            with transaction.atomic():
                if E.objects.filter(emp_code=new_code).exists():
                    raise ValueError(f'The employee code {new_code} is already used in {tgt.name}. Give another code.')
                data = dict(base)
                data['emp_code'] = new_code
                if not keep:
                    data['emp_joined_date'] = eff
                for k, f in (('branch', 'emp_branch_id_id'), ('department', 'emp_dept_id_id'), ('designation', 'emp_desgntn_id_id'), ('category', 'emp_ctgry_id_id')):
                    v = opts.get(k)
                    data[f] = int(v) if str(v or '').isdigit() else None
                if not data.get('emp_branch_id_id'):
                    raise ValueError('Choose the branch in the target company.')
                if data.get('person_id') and E.objects.filter(person_id=data['person_id']).exists():
                    data['person_id'] = None
                if data.get('barcode_number') and E.objects.filter(barcode_number=data['barcode_number']).exists():
                    data['barcode_number'] = None
                new = E(**data)
                new.is_ess = False
                new.save(authenticated_user=user)
                for b in banks:
                    Bank = M('EmpManagement', 'EmployeeBankDetail')
                    if Bank.objects.filter(account_number=b.get('account_number')).exists():
                        continue
                    Bank.objects.create(employee=new, **b)
                    result['bank_accounts'] += 1
                if opts.get('copy_salary', True):
                    SC, ESS = M('PayrollManagement', 'SalaryComponent'), M('PayrollManagement', 'EmployeeSalaryStructure')
                    comps = list(SC.objects.all())
                    for c_code, c_name, amount, c_data in salary:
                        c = _find(comps, 'code', 'name', {'code': c_code, 'name': c_name})
                        if c is None:
                            c = SC.objects.create(**c_data)
                            comps.append(c)
                            result['components_added'].append(c_name)
                        ESS.objects.update_or_create(employee=new, component=c, defaults={'amount': amount, 'is_active': True})   # a new employee may get default lines
                        result['salary_lines'] += 1
                if login is not None:
                    E.objects.filter(pk=new.pk).update(users=login, is_ess=bool(login.is_ess))
                M('Chatter', 'Message').objects.create(model='empmanagement.emp_master', object_id=str(new.pk), author=user,
                                                       body=f'Transferred from {src.name} ({code}) on {eff:%d %b %Y}.'
                                                            + (' Service date kept.' if keep else ''))
                new_id = new.pk
        # close in the source
        with schema_context(src.schema_name):
            E.objects.filter(pk=emp_id).update(is_active=False, emp_status=False, users=None)
            M('Chatter', 'Message').objects.create(model='empmanagement.emp_master', object_id=str(emp_id), author=user,
                                                   body=f'Transferred to {tgt.name} ({new_code}) on {eff:%d %b %Y}.')
        if login is not None:
            tcomp = Company.objects.get(schema_name=tgt.schema_name)
            login.tenants.add(tcomp)
            if login.is_ess:      # a self-service login now opens the new company only
                login.tenants.remove(Company.objects.get(schema_name=src.schema_name))
            result['login'] = login.username
        rec.target_employee_id, rec.result, rec.state = new_id, result, 'done'
        rec.save(update_fields=['target_employee_id', 'result', 'state'])
    except Exception as e:
        rec.state, rec.error = 'failed', str(e)[:500]
        rec.save(update_fields=['state', 'error'])
        raise
    return rec
