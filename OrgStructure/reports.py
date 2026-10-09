"""Report hooks (v1.12.0): extra employee columns in every report and headcount by the new org units.

DashboardManagement.reports calls:
  * extend_report(request, key, cols, rows)  – after a report ran: adds Location / Division / … columns next to the
    standard employee columns when the setting is on (one EmployeeOrg query per run) and drops Category when
    employee categories are switched off;
  * hidden_reports()                         – report keys to leave out of the list (switched-off units);
  * REPORT_SPECS                             – the headcount reports, merged into REPORTS.
"""
from collections import defaultdict

from .models import OrgSettings
from .services import FIELD_SETTING, ORG_FIELDS, active_emp_fields, org_rows

COL_KEY = 'org_{}'
HEADCOUNT_KEYS = {'location': 'headcount-location-unit', 'division': 'headcount-division', 'section': 'headcount-section',
                  'cost_center': 'headcount-cost-center', 'grade': 'headcount-grade', 'job_position': 'headcount-position',
                  'employment_type': 'headcount-employment-type'}


def extend_report(request, key, cols, rows):
    try:
        s = OrgSettings.get()
    except Exception:
        return cols, rows
    keys = [c[0] for c in cols]
    is_emp_report = 'department' in keys and 'designation' in keys and 'employee' in keys
    if not is_emp_report:
        return cols, rows
    cols = list(cols)
    if not s.use_employee_categories and 'category' in keys:
        cols = [c for c in cols if c[0] != 'category']
        for r in rows:
            r.pop('category', None)
    fields = active_emp_fields(s)
    if not fields or not rows:
        if fields:
            cols = _insert_cols(cols, s, fields)
        return cols, rows
    ids = {r.get('_emp') for r in rows if r.get('_emp')}
    orgs = org_rows(ids) if ids else {}
    for r in rows:
        o = orgs.get(r.get('_emp'))
        for f in fields:
            m = getattr(o, f, None) if o is not None else None
            r[COL_KEY.format(f)] = m.name if m else ''
    return _insert_cols(cols, s, fields), rows


def _insert_cols(cols, s, fields):
    keys = [c[0] for c in cols]
    at = (keys.index('category') if 'category' in keys else keys.index('designation')) + 1
    new = [(COL_KEY.format(f), s.label(FIELD_SETTING[f]), 'text') for f in fields]
    return cols[:at] + new + cols[at:]


def hidden_reports():
    try:
        s = OrgSettings.get()
    except Exception:
        return set()
    out = {k for f, k in HEADCOUNT_KEYS.items() if not s.is_on(FIELD_SETTING[f])}
    if not s.use_employee_categories:
        out.add('categories')
    return out


def headcount_rows(emps, group, date_from=None, date_to=None, left=None):
    """emps: employee queryset (already limited to the user's branches)."""
    s = OrgSettings.get()
    if group not in FIELD_SETTING:
        raise ValueError('Group by one of: ' + ', '.join(FIELD_SETTING) + '.')
    if not s.is_on(FIELD_SETTING[group]):
        raise ValueError(f'{s.label(FIELD_SETTING[group])} is switched off in Organisation settings.')
    label = s.label(FIELD_SETTING[group])
    emps = list(emps.select_related('emp_branch_id'))
    orgs = org_rows([e.pk for e in emps])
    budget = {}
    if group == 'job_position':
        from .models import JobPosition
        budget = dict(JobPosition.objects.values_list('name', 'headcount_budget'))
    groups = {}
    for e in emps:
        o = orgs.get(e.pk)
        m = getattr(o, group, None) if o else None
        k = (e.emp_branch_id.branch_name if e.emp_branch_id_id else '(no branch)', m.name if m else f'(no {label.lower()})')
        g = groups.setdefault(k, {'branch': k[0], 'unit': k[1], 'code': m.code if m else '', 'active': 0, 'male': 0, 'female': 0, 'joined': 0, 'left': 0})
        if e.is_active:
            g['active'] += 1
            if e.emp_gender == 'M':
                g['male'] += 1
            elif e.emp_gender == 'F':
                g['female'] += 1
        if date_from and date_to and e.emp_joined_date and date_from <= e.emp_joined_date <= date_to:
            g['joined'] += 1
        lv = (left or {}).get(e.pk)
        if date_from and date_to and lv and lv[0] and date_from <= lv[0] <= date_to:
            g['left'] += 1
    rows = sorted(groups.values(), key=lambda g: (g['branch'], g['unit']))
    total = sum(g['active'] for g in rows) or 0
    for g in rows:
        g['share'] = round(100 * g['active'] / total, 1) if total else 0
        if group == 'job_position':
            g['budget'] = budget.get(g['unit'], '')
            g['vacant'] = max(0, g['budget'] - g['active']) if isinstance(g['budget'], int) else ''
    cols = [('branch', 'Branch', 'text'), ('unit', label, 'text'), ('code', 'Code', 'text'), ('active', 'Active employees', 'number'),
            ('male', 'Male', 'number'), ('female', 'Female', 'number')]
    if date_from and date_to:
        cols += [('joined', 'Joined in period', 'number'), ('left', 'Left in period', 'number')]
    cols += [('share', 'Share of headcount %', 'number')]
    if group == 'job_position':
        cols += [('budget', 'Headcount budget', 'number'), ('vacant', 'Vacant', 'number')]
    return cols, rows


def _report(group):
    def fn(run):
        from DashboardManagement.reports import _left
        cols, rows = headcount_rows(run.all_employees, group, run.date_from, run.date_to, _left(run))
        from DashboardManagement.reports import drill
        for g in rows:
            g['_drill'] = drill('employees', {'branch': g['branch'], COL_KEY.format(group): g['unit'], 'status': 'Active'})
        return cols, rows
    fn.__name__ = f'r_headcount_{group}'
    return fn


_TITLES = {'location': 'location (site)', 'division': 'division', 'section': 'section', 'cost_center': 'cost centre', 'grade': 'grade',
           'job_position': 'job position', 'employment_type': 'employment type'}
REPORT_SPECS = {
    HEADCOUNT_KEYS[f]: (f'Headcount by {_TITLES[f]}', 'People',
                        f'Active employees per branch and {_TITLES[f]} with gender, joiners and leavers in the period and share of headcount'
                        + (' – with the headcount budget and vacancies.' if f == 'job_position' else '.'),
                        _report(f), 'year', ['view_report', 'view_emp_master'])
    for f, _, _ in ORG_FIELDS
}
