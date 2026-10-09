"""Small entry points other apps call (all optional: they do nothing when OrgStructure is not installed)."""
import logging

from django.apps import apps

log = logging.getLogger(__name__)
M = apps.get_model

TRANSFER_KEYS = {'to_location': 'location', 'to_division': 'division', 'to_section': 'section', 'to_cost_center': 'cost_center',
                 'to_grade': 'grade', 'to_job_position': 'job_position', 'to_employment_type': 'employment_type'}


def directory_extend(out):
    """DataTools directory: add the switched-on org fields (name + id) to every row, one query."""
    from .models import OrgSettings
    from .services import active_emp_fields, org_rows
    s = OrgSettings.get()
    fields = active_emp_fields(s)
    if not s.use_employee_categories:
        for r in out:
            r['category'] = ''
            r['category_id'] = None
    if not fields:
        return out
    orgs = org_rows([r['id'] for r in out])
    for r in out:
        o = orgs.get(r['id'])
        for f in fields:
            m = getattr(o, f, None) if o is not None else None
            r[f + '_id'] = m.pk if m else None
            r[f] = m.name if m else ''
    return out


def transfer_options(data):
    """HRActions transfer request body → {'org': {field: id}} for EmployeeTransfer.options."""
    org = {}
    for k, f in TRANSFER_KEYS.items():
        v = (data or {}).get(k)
        if v not in (None, '', 0, '0'):
            try:
                org[f] = int(v)
            except (TypeError, ValueError):
                org[f] = v
    return {'org': org} if org else {}


def transfer_plan(t, emp, out):
    """Adds the org changes of a transfer to its plan; returns True when something changes. Bad values become blockers."""
    from .models import EmployeeOrg, OrgSettings
    from .services import FIELD_MODEL, FIELD_SETTING, Problem, validate_assignment
    org = (t.options or {}).get('org') or {}
    if not org:
        return False
    s = OrgSettings.get()
    cur = EmployeeOrg.objects.filter(employee_id=emp.pk).first()
    # check against the employee as they will be after the transfer
    E = M('EmpManagement', 'emp_master')
    after = E(pk=emp.pk, emp_branch_id_id=t.to_branch_id or emp.emp_branch_id_id, emp_dept_id_id=t.to_department_id or emp.emp_dept_id_id,
              emp_desgntn_id_id=t.to_designation_id or emp.emp_desgntn_id_id)
    data = dict(org)
    try:
        vals = validate_assignment(after, data, cur, settings=s)
    except Problem as p:
        out['blockers'].extend(f'{s.label(FIELD_SETTING.get(k, k)) if k in FIELD_SETTING else k}: {v}' for k, v in p.errors.items())
        return True
    moved = False
    for f in org:
        old = getattr(cur, f, None) if cur else None
        new = vals.get(f)
        if getattr(old, 'pk', None) != getattr(new, 'pk', None):
            out['changes'].append({'area': 'Employee', 'what': s.label(FIELD_SETTING[f]), 'from': getattr(old, 'name', '–') or '–', 'to': getattr(new, 'name', '–') or '–'})
            moved = True
    return moved


def transfer_apply(t, emp, user=None):
    """Saves the org part of a transfer; returns the lines for the transfer result."""
    from .models import EmployeeOrg
    from .services import save_assignment, org_values
    org = (t.options or {}).get('org') or {}
    if not org:
        return []
    cur = EmployeeOrg.objects.filter(employee_id=emp.pk).first()
    before = {f: getattr(cur, f + '_id', None) if cur else None for f in org}
    save_assignment(emp, dict(org), user_id=getattr(user, 'pk', None))
    opts = dict(t.options or {})
    opts['org_from'] = before
    t.options = opts
    return ['Organisation: ' + ', '.join(f.replace('_', ' ') for f in org)]
