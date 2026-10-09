"""OrgStructure helpers shared by the API, the reports, the directory and the transfer (v1.12.0)."""
import logging

from django.apps import apps
from django.db.models import Q

from .models import (DEFAULT_LABELS, BranchCountryPolicy, CostCenter, CountryPolicy, Division, DivisionDepartment, EmployeeOrg, EmploymentType,
                     Grade, JobPosition, Location, OrgSettings, Section)

log = logging.getLogger(__name__)
M = apps.get_model

# field on EmployeeOrg → (setting key, model, API name of the master)
ORG_FIELDS = [
    ('location', 'locations', Location),
    ('division', 'divisions', Division),
    ('section', 'sections', Section),
    ('cost_center', 'cost_centers', CostCenter),
    ('grade', 'grades', Grade),
    ('job_position', 'job_positions', JobPosition),
    ('employment_type', 'employment_types', EmploymentType),
]
FIELD_SETTING = {f: s for f, s, _ in ORG_FIELDS}
FIELD_MODEL = {f: m for f, _, m in ORG_FIELDS}


# ------------------------------------------------------------------ rights
def ctx(request):
    from AccessControl.access import ctx as _c
    return _c(request)


def has(request, *codes):
    try:
        c = ctx(request)
        return c.admin or any(x in c.codes for x in codes)
    except Exception:
        return False


def is_hr_master(request, model_name, verb):
    """Masters: company admin, the model's own permission, or the matching department permission
    (HR who keep departments keep the other org masters too)."""
    return has(request, f'{verb}_{model_name}', f'{verb}_dept_master')


def is_hr_settings(request):
    return has(request, 'change_orgsettings', 'add_emp_customfield', 'change_emp_customfield')


def is_hr_employees(request, write=False):
    if write:
        return has(request, 'change_employeeorg', 'change_emp_master', 'add_emp_master')
    return has(request, 'view_employeeorg', 'view_emp_master', 'change_emp_master')


def is_hr_policies(request, write=False):
    if write:
        return has(request, 'change_companypolicy', 'add_companypolicy', 'change_policyacknowledgement')
    return has(request, 'view_companypolicy', 'change_companypolicy', 'view_policyacknowledgement')


def is_hr_country(request, write=False):
    if write:
        return has(request, 'change_countrypolicy', 'add_countrypolicy', 'change_leavepolicy')
    return True  # rule sets are reference data for every user of the company


def user_branches(request):
    """None = every branch."""
    try:
        c = ctx(request)
        return None if c.admin else c.branches
    except Exception:
        return []


def my_employee(request):
    try:
        return ctx(request).emp
    except Exception:
        return None


def branch_visible(branch_ids, allowed):
    if allowed is None or not branch_ids:
        return True
    return bool(set(int(b) for b in branch_ids) & set(allowed))


# ------------------------------------------------------------------ settings payload
def settings_payload(s=None):
    s = s or OrgSettings.get()
    from .models import FIELDS
    return {
        'use_' + f: s.is_on(f) for f in FIELDS
    } | {
        'labels': {f: s.label(f) for f in FIELDS},
        'mandatory': {f: bool((s.mandatory or {}).get(f)) for f in FIELDS},
        'active_fields': s.active_fields(),
        'default_labels': DEFAULT_LABELS,
        'updated_at': s.updated_at,
    }


# ------------------------------------------------------------------ names
def emp_name(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x and str(x).strip())
    return f'{n} ({e.emp_code})' if n else e.emp_code


def name_maps():
    """id → name of departments, designations, branches (one query each)."""
    return {
        'department': dict(M('OrganisationManager', 'dept_master').objects.values_list('id', 'dept_name')),
        'designation': dict(M('OrganisationManager', 'desgntn_master').objects.values_list('id', 'desgntn_job_title')),
        'branch': dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('id', 'branch_name')),
    }


def org_rows(emp_ids):
    """employee id → EmployeeOrg with the masters loaded (one query)."""
    qs = EmployeeOrg.objects.select_related(*[f for f, _, _ in ORG_FIELDS])
    if emp_ids is not None:
        qs = qs.filter(employee_id__in=list(emp_ids))
    return {o.employee_id: o for o in qs}


def org_values(o, fields):
    """{field: name, field_id: id, field_code: code} of the active fields."""
    out = {}
    for f in fields:
        m = getattr(o, f, None) if o is not None else None
        out[f + '_id'] = m.pk if m else None
        out[f] = m.name if m else ''
        out[f + '_code'] = m.code if m else ''
    return out


def active_emp_fields(s=None):
    s = s or OrgSettings.get()
    return [f for f, key, _ in ORG_FIELDS if s.is_on(key)]


# ------------------------------------------------------------------ validation of an employee's assignment
class Problem(Exception):
    def __init__(self, errors):
        super().__init__('; '.join(f'{k}: {v}' for k, v in errors.items()))
        self.errors = errors


def _pick(model, value, by_code=False):
    if value in (None, '', 0, '0'):
        return None
    if by_code:
        return model.objects.filter(code__iexact=str(value).strip()).first()
    try:
        return model.objects.filter(pk=int(value)).first()
    except (TypeError, ValueError):
        return None


def validate_assignment(emp, data, current=None, by_code=False, settings=None):
    """data: {field: id (or code with by_code), contract_end_date}. Returns a dict of resolved values to save.
    Only keys present in data change; others keep the current value. Raises Problem."""
    s = settings or OrgSettings.get()
    errors = {}
    out = {}
    for f, key, model in ORG_FIELDS:
        names = [f, f + '_id'] + ([f + '_code'] if by_code else [])
        k = next((n for n in names if n in data), None)
        if k is None:
            out[f] = getattr(current, f) if current else None
            continue
        v = data.get(k)
        obj = _pick(model, v, by_code=by_code or k.endswith('_code'))
        if v not in (None, '', 0, '0') and obj is None:
            errors[f] = f'{s.label(key)} "{v}" does not exist.'
            continue
        if obj is not None and not obj.active and (current is None or getattr(current, f + '_id') != obj.pk):
            errors[f] = f'{s.label(key)} {obj.name} is inactive – choose an active one.'
            continue
        out[f] = obj
    if 'contract_end_date' in data:
        from django.utils.dateparse import parse_date
        v = data.get('contract_end_date')
        try:
            out['contract_end_date'] = parse_date(str(v)) if v else None
        except ValueError:
            out['contract_end_date'] = None
        if v and out['contract_end_date'] is None:
            errors['contract_end_date'] = 'Use the date format YYYY-MM-DD.'
    else:
        out['contract_end_date'] = current.contract_end_date if current else None
    # mandatory fields of active settings
    for f, key, _ in ORG_FIELDS:
        if s.is_on(key) and (s.mandatory or {}).get(key) and out.get(f) is None and f not in errors:
            errors[f] = f'{s.label(key)} is required.'
    branch = emp.emp_branch_id_id
    dept = emp.emp_dept_id_id
    desg = emp.emp_desgntn_id_id
    for f, key, _ in ORG_FIELDS:
        obj = out.get(f)
        if obj is not None and f not in errors and obj.branch_ids and branch and branch not in [int(b) for b in obj.branch_ids]:
            errors[f] = f'{s.label(key)} {obj.name} is not used in the employee’s branch – pick one of the branch or add the branch to it.'
    sec = out.get('section')
    if sec is not None and 'section' not in errors and sec.department_id != dept:
        errors['section'] = f'{s.label("sections")} {sec.name} belongs to another department – choose a {s.label("sections").lower()} of the employee’s department.'
    div = out.get('division')
    if 'division' not in data and 'division_id' not in data and 'division_code' not in data and s.use_divisions and dept:
        link = DivisionDepartment.objects.filter(department_id=dept).select_related('division').first()
        if link and (div is None or (current is not None and current.division_id == div.pk and div.pk != link.division_id)):
            out['division'] = div = link.division  # default from the department
    if div is not None and 'division' not in errors and dept:
        link = DivisionDepartment.objects.filter(department_id=dept).first()
        if link and link.division_id != div.pk:
            errors['division'] = f'The employee’s department belongs to another {s.label("divisions").lower()}.'
    g = out.get('grade')
    if g is not None and 'grade' not in errors and g.designation_ids and desg and desg not in [int(x) for x in g.designation_ids]:
        errors['grade'] = f'{s.label("grades")} {g.name} is not allowed for the employee’s designation.'
    p = out.get('job_position')
    if p is not None and 'job_position' not in errors:
        if p.department_id and dept and p.department_id != dept:
            errors['job_position'] = f'{s.label("job_positions")} {p.name} belongs to another department.'
        elif p.designation_id and desg and p.designation_id != desg:
            errors['job_position'] = f'{s.label("job_positions")} {p.name} is for another designation.'
        elif p.grade_id and g is not None and p.grade_id != g.pk:
            errors['job_position'] = f'{s.label("job_positions")} {p.name} is graded {p.grade.name}, not {g.name}.'
    cc = out.get('cost_center')
    if cc is not None and 'cost_center' not in errors and cc.department_id and dept and cc.department_id != dept:
        errors['cost_center'] = f'{s.label("cost_centers")} {cc.name} belongs to another department.'
    et = out.get('employment_type')
    if et is not None and not et.has_end_date and out.get('contract_end_date') and 'contract_end_date' not in errors and 'contract_end_date' in data:
        pass  # an end date on a permanent type is allowed (kept for information)
    if errors:
        raise Problem(errors)
    return out


def save_assignment(emp, data, user_id=None, by_code=False):
    from django.db import transaction
    with transaction.atomic():
        cur = EmployeeOrg.objects.select_for_update().filter(employee_id=emp.pk).first()
        vals = validate_assignment(emp, data, cur, by_code=by_code)
        changes = []
        if cur is None:
            cur = EmployeeOrg(employee_id=emp.pk)
        for k, v in vals.items():
            old = getattr(cur, k, None)
            if old != v:
                changes.append((k, old, v))
            setattr(cur, k, v)
        cur.updated_by_id = user_id
        cur.save()
    if changes:
        try:
            s = OrgSettings.get()
            lines = []
            for k, old, new in changes:
                lab = s.label(FIELD_SETTING[k]) if k in FIELD_SETTING else 'Contract end date'
                lines.append(f'{lab}: {getattr(old, "name", old) or "–"} → {getattr(new, "name", new) or "–"}')
            M('Chatter', 'Message').objects.create(model='EmpManagement.emp_master', object_id=str(emp.pk), author_id=user_id,
                                                   body='Organisation details changed:\n' + '\n'.join(lines))
        except Exception:
            log.debug('chatter note failed', exc_info=True)
    return cur


def assignment_json(o, emp=None, fields=None, names=None):
    fields = fields if fields is not None else [f for f, _, _ in ORG_FIELDS]
    d = {'employee_id': o.employee_id if o else (emp.pk if emp else None)}
    d.update(org_values(o, fields))
    d['contract_end_date'] = o.contract_end_date if o else None
    if emp is not None:
        d['employee'] = emp_name(emp)
        d['employee_code'] = emp.emp_code
        d['department_id'] = emp.emp_dept_id_id
        d['designation_id'] = emp.emp_desgntn_id_id
        d['branch_id'] = emp.emp_branch_id_id
    return d


# ------------------------------------------------------------------ cost centres ↔ expense cost centres
def sync_expense_cost_center(cc):
    """Keep ExpenseManagement.CostCenter in step (same code) so the expense screens can pick these cost centres."""
    try:
        E = M('ExpenseManagement', 'CostCenter')
    except LookupError:
        return None
    row = E.objects.filter(pk=cc.expense_cost_center_id).first() if cc.expense_cost_center_id else None
    row = row or E.objects.filter(code=cc.code).first()
    if row is None:
        row = E(code=cc.code)
    row.code, row.name, row.department_id, row.active = cc.code, cc.name, cc.department_id, cc.active
    row.save()
    if cc.expense_cost_center_id != row.pk:
        CostCenter.objects.filter(pk=cc.pk).update(expense_cost_center_id=row.pk)
        cc.expense_cost_center_id = row.pk
    return row


def import_expense_cost_centers():
    """First use: copy the cost centres that exist only in Expense management."""
    try:
        E = M('ExpenseManagement', 'CostCenter')
    except LookupError:
        return 0
    n = 0
    have = set(CostCenter.objects.values_list('code', flat=True))
    for r in E.objects.all():
        if r.code not in have:
            CostCenter.objects.create(code=r.code, name=r.name, department_id=r.department_id, active=r.active, expense_cost_center_id=r.pk)
            n += 1
    return n


# ------------------------------------------------------------------ hierarchy
def manager_cycles():
    """Loops in the reporting-manager chain: [[emp ids ...], ...]."""
    E = M('EmpManagement', 'emp_master')
    user_emp = {}
    mgr = {}
    for e in E.objects.filter(is_active=True).values('id', 'users_id', 'emp_reporting_manager_id'):
        if e['users_id']:
            user_emp[e['users_id']] = e['id']
        mgr[e['id']] = e['emp_reporting_manager_id']
    nxt = {eid: user_emp.get(u) for eid, u in mgr.items() if u}
    return _cycles(nxt)


def _cycles(nxt):
    seen, cycles, done = {}, [], set()
    for start in nxt:
        if start in done:
            continue
        path, pos, cur = [], {}, start
        while cur is not None and cur not in done and cur not in pos:
            pos[cur] = len(path)
            path.append(cur)
            cur = nxt.get(cur)
        if cur is not None and cur in pos:
            loop = path[pos[cur]:]
            if loop and sorted(loop) not in [sorted(c) for c in cycles]:
                cycles.append(loop)
        done.update(path)
    return cycles


def would_loop_manager(emp_id, manager_user_id):
    """True when making manager_user_id the reporting manager of emp_id closes a loop."""
    if not manager_user_id:
        return False
    E = M('EmpManagement', 'emp_master')
    emp = E.objects.filter(pk=emp_id).values('users_id').first()
    if emp and emp['users_id'] and int(emp['users_id']) == int(manager_user_id):
        return True
    user_emp = dict(E.objects.filter(users__isnull=False).values_list('users_id', 'id'))
    mgr_of = dict(E.objects.values_list('id', 'emp_reporting_manager_id'))
    cur = user_emp.get(int(manager_user_id))
    steps = 0
    while cur is not None and steps < 10000:
        if cur == emp_id:
            return True
        u = mgr_of.get(cur)
        cur = user_emp.get(u) if u else None
        steps += 1
    return False


def position_cycles():
    nxt = dict(JobPosition.objects.filter(reports_to__isnull=False).values_list('id', 'reports_to_id'))
    return _cycles(nxt)


def would_loop_position(pos_id, parent_id):
    if not parent_id or not pos_id:
        return False
    if int(pos_id) == int(parent_id):
        return True
    parent = dict(JobPosition.objects.values_list('id', 'reports_to_id'))
    cur, steps = int(parent_id), 0
    while cur is not None and steps < 10000:
        if cur == int(pos_id):
            return True
        cur = parent.get(cur)
        steps += 1
    return False


def filled_counts():
    from django.db.models import Count
    E = M('EmpManagement', 'emp_master')
    active = E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True)).values('id')
    return dict(EmployeeOrg.objects.filter(job_position__isnull=False, employee_id__in=active).values('job_position_id')
                .annotate(n=Count('id')).values_list('job_position_id', 'n'))


# ------------------------------------------------------------------ company policies
def policy_applies(p, emp, user_id=None, branch_ids=None, specific=None):
    """branch_ids / specific: prefetched id lists of the policy (optional)."""
    if emp is None:
        return False
    specific = specific if specific is not None else list(p.specific_users.values_list('id', flat=True))
    branch_ids = branch_ids if branch_ids is not None else list(p.branch.values_list('id', flat=True))
    uid = user_id or emp.users_id
    if specific and uid in specific:
        return True
    has_rule = bool(branch_ids or p.department_id or p.category_id or p.designation_id)
    if specific and not has_rule:
        return False
    if branch_ids and emp.emp_branch_id_id not in branch_ids:
        return False
    if p.department_id and p.department_id != emp.emp_dept_id_id:
        return False
    if p.category_id and p.category_id != emp.emp_ctgry_id_id:
        return False
    if p.designation_id and p.designation_id != emp.emp_desgntn_id_id:
        return False
    return True


def policy_version(p):
    """PolicyVersion of a CompanyPolicy, moved to a new version when the file or the policy changed."""
    from django.utils import timezone
    from .models import PolicyVersion
    fp = f'{getattr(p.policy_file, "name", "") or ""}|{p.updated_at.isoformat() if p.updated_at else ""}'[:300]
    v = PolicyVersion.objects.filter(policy_id=p.pk).first()
    if v is None:
        v = PolicyVersion.objects.create(policy_id=p.pk, version=1, fingerprint=fp, version_date=p.updated_at or timezone.now())
    elif v.fingerprint != fp:
        v.version += 1
        v.fingerprint = fp
        v.version_date = p.updated_at or timezone.now()
        v.save(update_fields=['version', 'fingerprint', 'version_date'])
    return v


def applicable_employees(p, qs=None):
    E = M('EmpManagement', 'emp_master')
    qs = qs if qs is not None else E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True))
    b = list(p.branch.values_list('id', flat=True))
    sp = list(p.specific_users.values_list('id', flat=True))
    return [e for e in qs if policy_applies(p, e, branch_ids=b, specific=sp)]


# ------------------------------------------------------------------ country policies
def country_policy_for(branch_id):
    """The labour-law rule set of a branch: the one applied to the branch, else the active standard set of the
    branch's country, else None. For leave / payroll engines (they are not wired to it yet)."""
    if not branch_id:
        return None
    link = BranchCountryPolicy.objects.filter(branch_id=branch_id).select_related('country_policy').first()
    if link:
        return link.country_policy
    b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch_id).select_related('br_country').first()
    c = getattr(b, 'br_country', None) if b else None
    if c is None:
        return None
    code = (c.country_code or '').strip().upper()[:2]
    qs = CountryPolicy.objects.filter(active=True)
    return (qs.filter(country_code=code).order_by('-is_standard', 'id').first() if len(code) == 2 else None) or \
        qs.filter(country_name__iexact=c.country_name).order_by('-is_standard', 'id').first()
