"""v1.56.0 – setup guide: a new company from the first screen to the first payroll in 14 steps and one optional step.

Each step reads what the company already has (most of the setup lives in its own screens), shows the main settings with
the country pack's defaults, saves them on Next and lists what is missing in plain words with a link to fix it.
Checks of level 'block' stop Next (and go-live); 'warn' are listed and can be accepted at go-live with a note.
Re-running a step adds what is missing and never deletes data.
"""
import datetime
import logging
from decimal import Decimal

from django.apps import apps
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
MS = '/main-sidebar/'


class GuideError(Exception):
    def __init__(self, msg, problems=None, status=400):
        super().__init__(msg)
        self.problems = problems or {}
        self.status = status


def has(label):
    try:
        M(label)
        return True
    except LookupError:
        return False


def chk(key, ok, text, link=None, level='block'):
    return {'key': key, 'ok': bool(ok), 'level': level, 'text': tr(text) if isinstance(text, str) else text, 'link': link}


def field(key, label, value, kind='text', options=None, help='', readonly=False, purple=False):
    return {'key': key, 'label': tr(label), 'value': value, 'type': kind, 'options': [{'value': v, 'label': tr(l)} for v, l in (options or [])],
            'help': tr(help) if help else '', 'readonly': readonly, 'new': purple}


def link(label, path):
    return {'label': tr(label), 'link': MS + path}


def iso(d):
    return d.isoformat() if d else None


def _date(v):
    if not v:
        return None
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise GuideError(tr('Enter a valid date (YYYY-MM-DD).'))


def _int(v, lo, hi, label):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise GuideError(tr('{0}: enter a whole number.', tr(label)))
    if not lo <= n <= hi:
        raise GuideError(tr('{0}: enter a number from {1} to {2}.', tr(label), lo, hi))
    return n


def company():
    C = M('UserManagement', 'company')
    return C.objects.filter(schema_name=connection.schema_name).first()


def pack():
    try:
        from CountryPack.services import current_pack
        return current_pack()
    except Exception:
        return {'pack': 'GEN', 'country_name': '', 'currency': {'code': ''}}


def is_ae():
    return pack().get('pack') == 'AE'


def active_apps():
    try:
        from ZeoApps import services as S
        return S.active_keys()
    except Exception:
        return ['hrms', 'payroll']


def payroll_on():
    return 'payroll' in active_apps()


def branches():
    return list(M('OrganisationManager', 'brnch_mstr').objects.order_by('id'))


def categories():
    C = M('OrganisationManager', 'ctgry_master')
    return list(C.objects.filter(Q(ctgry_is_active=True) | Q(ctgry_is_active__isnull=True)).order_by('ctgry_title'))


def employees():
    E = M('EmpManagement', 'emp_master')
    return E.objects.filter(Q(is_active=True) | Q(is_active__isnull=True))


def guide():
    return M('ZeoAdmin', 'SetupGuide').get()


def profile():
    if not has('OrgStructure.CompanyProfile'):
        return None
    P = M('OrgStructure', 'CompanyProfile')
    return P.objects.order_by('id').first() or P.objects.create()


def pp_settings():
    return M('PayrollPlus', 'PayrollPlusSettings').get() if has('PayrollPlus.PayrollPlusSettings') else None


def company_paystructure(create=False):
    """The pay structure that applies to the whole company (no branch, department, category or designation)."""
    PS = M('PayrollManagement', 'PayStructure')
    for p in PS.objects.order_by('id'):
        if not p.branch.exists() and not p.department.exists() and not p.category.exists() and not p.designation.exists():
            return p
    if create:
        return PS.objects.create(salary_calculation_type='FIXED_DAYS', fixed_working_days=30, attendance_cycle_type='MONTH', payday=28,
                                 working_days=['MON', 'TUE', 'WED', 'THU', 'FRI'], payroll_start_month=timezone.localdate().replace(day=1))
    return PS.objects.order_by('id').first()


# ====================================================================== steps
STEPS = [
    # key, number, title, stage, can skip, optional
    ('start', 1, 'Country, industry, apps', 'Company', False, False),
    ('company', 2, 'Legal and HR policies', 'Company', False, False),
    ('branches', 3, 'Branches and locations', 'Company', False, False),
    ('numbering', 4, 'Document numbers', 'Company', True, False),
    ('organisation', 5, 'Departments, grades', 'Company', False, False),
    ('time', 6, 'Calendar and attendance', 'Time and leave', False, False),
    ('shifts', 7, 'Shifts and rosters', 'Time and leave', True, False),
    ('leave', 8, 'Leave types and policies', 'Time and leave', False, False),
    ('payroll', 9, 'Components and bank file', 'Pay and approvals', False, False),
    ('approvals', 10, 'Chains and controls', 'Pay and approvals', False, False),
    ('employees', 11, 'Import and go-live data', 'People and go-live', False, False),
    ('users', 12, 'Roles and logins', 'People and go-live', False, False),
    ('modules', 13, 'Optional modules', 'People and go-live', True, True),
    ('communication', 14, 'Letters, policies, e-mail', 'People and go-live', True, False),
    ('golive', 15, 'Readiness check', 'People and go-live', False, False),
]
NAMES = {'start': 'Start', 'company': 'Company', 'branches': 'Branches', 'numbering': 'Numbering', 'organisation': 'Organisation',
         'time': 'Time', 'shifts': 'Shifts', 'leave': 'Leave', 'payroll': 'Payroll', 'approvals': 'Approvals', 'employees': 'Employees',
         'users': 'Users', 'modules': 'More modules', 'communication': 'Communication', 'golive': 'Go live'}
KEYS = [s[0] for s in STEPS]
BY_KEY = {s[0]: s for s in STEPS}


# ---------------------------------------------------------------------- 1 start
def s_start():
    c = company()
    p = pack()
    act = active_apps()
    try:
        from ZeoApps import services as S
        apps_rows = [{'key': a[0], 'title': tr(a[2]), 'active': a[0] in act, 'installed': S.installed(a[0])} for a in S.APPS]
        ind_code, ind_label = S.company_industry()
        industries = S.company_industries()
    except Exception:
        apps_rows, ind_code, ind_label, industries = [], '', '', []
    fy = profile().fiscal_year_start_month if profile() else 1
    months = [(i, datetime.date(2026, i, 1).strftime('%B')) for i in range(1, 13)]
    return {
        'fills': ['Country pack: captions, ID fields, rules, holidays', 'Industry templates for leave, shifts and HSE', 'Apps activated with the industry'],
        'fields': [
            field('country', 'Country', p.get('country_name') or getattr(c, 'country', ''), readonly=True, help='Set when the company was made – Settings › Country settings.'),
            field('industry', 'Industry', ind_code, 'select', [(i.get('code') if isinstance(i, dict) else i[0], i.get('label') if isinstance(i, dict) else i[1]) for i in industries] if industries else [(ind_code, ind_label or ind_code)]),
            field('currency', 'Currency', (p.get('currency') or {}).get('code') or getattr(c, 'currency', ''), readonly=True),
            field('leave_year', 'Leave year starts', fy or 1, 'select', months),
            field('languages', 'Languages', 'English and Arabic', readonly=True, help='Each user chooses English or Arabic.'),
        ],
        'apps': apps_rows,
        'links': [link('Apps', 'settings/apps'), link('Country settings', 'settings/country-settings')],
        'actions': [{'key': 'country_defaults', 'label': tr('Apply the country defaults'), 'help': tr('Adds missing document types, leave types, statutory schemes and the weekend. Safe to run again.')}],
        'checks': [
            chk('country', p.get('pack') and (p.get('pack') != 'GEN' or getattr(c, 'country', None)), 'Choose the country of the company.', MS + 'settings/country-settings'),
            chk('apps', any(a in act for a in ('hrms', 'payroll', 'projects', 'crm', 'property', 'maintenance', 'assets', 'hse')), 'Switch on at least one app.', MS + 'settings/apps'),
            chk('payroll_app', 'payroll' in act, 'Payroll is not switched on – the payroll steps are shown for information.', MS + 'settings/apps', 'warn'),
        ],
    }


def save_start(v, user):
    try:
        from ZeoApps import services as S
        code, _ = S.company_industry()
        if v.get('industry') and v['industry'] != code:
            c = company()
            c.industry_type = v['industry']
            c.save(update_fields=['industry_type'])
    except Exception:
        log.exception('setup guide: industry')
    if v.get('leave_year') and profile() is not None:
        p = profile()
        p.fiscal_year_start_month = _int(v['leave_year'], 1, 12, 'Leave year starts')
        p.save()


def act_country_defaults(v, user):
    from CountryPack.services import apply_defaults
    res = apply_defaults(user)
    added = res.get('added', []) if isinstance(res, dict) else []
    return tr('{0} items added; existing setup kept.', len(added))


# ---------------------------------------------------------------------- 2 company
CONTRACTS = [('unlimited', 'Unlimited'), ('limited', 'Limited (fixed term)')]


def s_company():
    c = company()
    p = profile()
    g = guide()
    pol = g.policies or {}
    brs = branches()
    prob_days = next((b.probation_period_days for b in brs if b.probation_period_days), None) or 180
    ps = company_paystructure()
    salary_days = (ps.fixed_working_days if ps and ps.salary_calculation_type == 'FIXED_DAYS' else 0) if ps else 30
    cp = None
    try:
        from CountryPack.services import company_rule_set
        cp = company_rule_set()
    except Exception:
        pass
    notice_default = ((cp or {}).get('notice_period') or {}).get('min_days', 30) if isinstance((cp or {}).get('notice_period'), dict) else 30
    return {
        'fills': ['Logo, stamp and signatory on the company profile', 'Probation and notice defaults of the country', 'Expiry list for licence and card'],
        'fields': [
            field('legal_name', 'Legal name', (p.legal_name if p else '') or getattr(c, 'name', '')),
            field('licence_no', 'Trade licence', p.licence_no if p else ''),
            field('licence_expiry', 'Licence expiry', iso(p.licence_expiry) if p else None, 'date'),
            field('employer_unique_id', 'MOHRE / WPS employer ID' if is_ae() else 'Employer ID (bank file)', getattr(c, 'employer_unique_id', '') or ''),
            field('tax_id', 'TRN' if is_ae() else 'Tax number', p.tax_id if p else ''),
            field('probation_months', 'Probation', round(prob_days / 30), 'select', [(0, 'No probation'), (1, '1 month')] + [(m, tr('{0} months', m)) for m in range(2, 7)], purple=True,
                  help='Applied to every branch; an employment type can have its own.'),
            field('notice_days', 'Notice period (days)', pol.get('notice_days', notice_default), 'number', purple=True, help='The default notice of a resignation.'),
            field('contract_type', 'Contract type', pol.get('contract_type', 'unlimited'), 'select', CONTRACTS, purple=True,
                  help='The default MOHRE contract type of a new employee.'),
            field('salary_days', 'Salary days per month', salary_days or 0, 'select', [(30, '30 days (fixed)'), (0, 'Calendar days of the month')], purple=True,
                  help='The daily rate: monthly salary ÷ these days.'),
        ],
        'links': [link('Company profile', 'org-structure'), link('Company', 'settings/location-master')],
        'checks': [
            chk('legal_name', (p and p.legal_name) or getattr(c, 'name', ''), 'Enter the legal name.'),
            chk('licence', p and p.licence_no, 'Enter the trade licence number.', level='warn'),
            chk('wps_id', not payroll_on() or getattr(c, 'employer_unique_id', ''), 'Enter the WPS employer ID – the bank file needs it.' if is_ae() else 'Enter the employer ID of the bank file.'),
            chk('policies', pol.get('notice_days') is not None and pol.get('contract_type'), 'Set the probation, notice period and salary days, then save.'),
        ],
    }


def save_company(v, user):
    c = company()
    p = profile()
    if 'legal_name' in v and p is not None:
        p.legal_name = (v.get('legal_name') or '').strip()[:200]
    if p is not None:
        for k in ('licence_no', 'tax_id'):
            if k in v:
                setattr(p, k, (v.get(k) or '').strip()[:60])
        if 'licence_expiry' in v:
            p.licence_expiry = _date(v.get('licence_expiry'))
        if is_ae() and p.tax_id and (not p.tax_id.isdigit() or len(p.tax_id) != 15):
            raise GuideError(tr('TRN must be 15 digits.'), {'tax_id': 'Invalid.'})
        p.save()
    if 'employer_unique_id' in v:
        eid = (v.get('employer_unique_id') or '').strip()
        if eid and is_ae() and (not eid.isdigit() or len(eid) != 13):
            raise GuideError(tr('The WPS employer ID has 13 digits.'), {'employer_unique_id': 'Invalid.'})
        c.employer_unique_id = eid
        c.save(update_fields=['employer_unique_id'])
    g = guide()
    pol = dict(g.policies or {})
    if 'notice_days' in v:
        pol['notice_days'] = _int(v['notice_days'], 0, 365, 'Notice period (days)')
    if 'contract_type' in v:
        if v['contract_type'] not in dict(CONTRACTS):
            raise GuideError(tr('Choose the contract type.'))
        pol['contract_type'] = v['contract_type']
    if 'probation_months' in v:
        m = _int(v['probation_months'], 0, 6, 'Probation')
        pol['probation_months'] = m
        M('OrganisationManager', 'brnch_mstr').objects.update(probation_period_days=m * 30)
    if 'salary_days' in v:
        d = _int(v['salary_days'], 0, 31, 'Salary days per month')
        pol['salary_days'] = d
        ps = company_paystructure(create=True)
        if d:
            ps.salary_calculation_type, ps.fixed_working_days = 'FIXED_DAYS', d
        else:
            ps.salary_calculation_type = 'CALENDAR_DAYS'
        ps.save()
    g.policies = pol
    g.save(update_fields=['policies', 'updated_at'])


# ---------------------------------------------------------------------- 3 branches
def s_branches():
    BW = {x.branch_id: x for x in M('PayrollPlus', 'BranchWPS').objects.all()} if has('PayrollPlus.BranchWPS') else {}
    L = M('OrgStructure', 'Location') if has('OrgStructure.Location') else None
    c = company()
    rows, missing_state, missing_wps = [], [], []
    for b in branches():
        locs = list(L.objects.filter(Q(branch_id=b.pk) | Q(branch_ids__contains=[b.pk])).values_list('name', 'geofence_radius_m')) if L else []
        w = BW.get(b.pk)
        eid = (w.employer_unique_id if w and w.employer_unique_id else '') or getattr(c, 'employer_unique_id', '')
        rows.append([b.branch_name, str(b.br_state_id or '') if b.br_state_id_id else '', eid or '', ', '.join(f'{n} ({r or 0} m)' for n, r in locs) or '–'])
        if not b.br_state_id_id:
            missing_state.append(b.branch_name)
        if not eid:
            missing_wps.append(b.branch_name)
    return {
        'fills': ['Head office from the company', 'GPS geofence radius for each location', 'Bank routing from the company bank'],
        'tables': [{'title': tr('Branches'), 'columns': [tr('Branch'), tr('Emirate') if is_ae() else tr('State'), tr('WPS employer ID') if is_ae() else tr('Employer ID'), tr('Locations')],
                    'rows': rows, 'add': MS + 'settings/branch-master'}],
        'links': [link('Branches', 'settings/branch-master'), link('Locations', 'org-structure'), link('WPS per branch', 'salary-options/payroll-controls')],
        'template': 'branches',
        'checks': [
            chk('branch', rows, 'Add at least one branch.', MS + 'settings/branch-master'),
            chk('state', not missing_state, tr('Give every branch its emirate: {0}.', ', '.join(missing_state)) if is_ae() else tr('Give every branch its state: {0}.', ', '.join(missing_state)), MS + 'settings/branch-master', 'warn'),
            chk('wps', not payroll_on() or not missing_wps, tr('WPS employer ID missing: {0}.', ', '.join(missing_wps)), MS + 'salary-options/payroll-controls', 'warn' if not is_ae() else 'block'),
        ],
    }


# ---------------------------------------------------------------------- 4 numbering
def s_numbering():
    from . import numbering as N
    P = N.active() or M('ZeoAdmin', 'NumberingPattern').get()
    labels = N.type_labels()
    codes = {t: (P.codes or {}).get(t) or c for t, c in N.SUGGESTED.items()}
    EC = M('EmployeeProfile', 'EmployeeCodeSetting') if has('EmployeeProfile.EmployeeCodeSetting') else None
    ec = EC.objects.filter(branch_id__isnull=True).first() if EC else None
    emp_pattern = (f'{ec.prefix}{{SEQ:{ec.padding}}}' if ec else 'E{SEQ:4}')
    try:
        prev = N.preview(P.pattern, codes)
    except Exception:
        prev = []
    problems = N.validate(P.pattern, codes)
    return {
        'fills': ['A suggested code for each of the 28 document types', 'The same pattern on every branch and location', 'The employee code pattern'],
        'fields': [
            field('pattern', 'Pattern', P.pattern, purple=True, help='Tokens: {TYPE} code · {BRANCH} branch code · {LOC} location code · {YYYY} / {YY} year · {MM} month · {SEQ:n} counter of n digits.'),
            field('scope', 'Counter', P.scope, 'select', M('ZeoAdmin', 'NumberingPattern').SCOPE, purple=True),
            field('restart', 'Restart', P.restart, 'select', M('ZeoAdmin', 'NumberingPattern').RESTART, purple=True),
            field('employee_code', 'Employee code', emp_pattern, purple=True, help='E{SEQ:4} for the company, or a prefix per branch in Employee code numbering.'),
            field('active', 'Use this pattern for every document type', bool(P.active), 'check', purple=True,
                  help='Numbers already issued are kept. Switched off, the prefix rows of Document numbering work again.'),
        ],
        'codes': [{'area': tr(a), 'types': [{'type': t, 'label': tr(labels.get(t, t)), 'code': codes.get(t, '')} for t in ts]} for a, ts in N.AREAS],
        'preview': prev + ([ (f"{ec.prefix}{'1'.zfill(ec.padding)}") ] if ec else ['E0001']),
        'links': [link('Document numbering', 'settings/document-numbering'), link('Employee code numbering', 'employee-master-tools/code-numbering')],
        'template': 'numbering',
        'checks': [
            chk('pattern', not problems, ' '.join(problems) or 'Pattern is fine.'),
            chk('employee_code', ec is not None and ec.enabled, 'Set the employee code pattern before the employee import.', MS + 'employee-master-tools/code-numbering', 'warn'),
        ],
    }


def save_numbering(v, user):
    from . import numbering as N
    P = M('ZeoAdmin', 'NumberingPattern').get()
    pattern = (v.get('pattern') or P.pattern or '').strip()
    codes = dict(P.codes or {})
    for t, c in (v.get('codes') or {}).items():
        if t in N.SUGGESTED:
            codes[t] = (c or '').strip().upper()
    full = {t: codes.get(t) or c for t, c in N.SUGGESTED.items()}
    problems = N.validate(pattern, full)
    if problems:
        raise GuideError(' '.join(problems), {'pattern': problems[0]})
    if v.get('scope') in dict(P.SCOPE):
        P.scope = v['scope']
    if v.get('restart') in dict(P.RESTART):
        P.restart = v['restart']
    P.pattern, P.codes = pattern, full
    if 'active' in v:
        on = str(v['active']).lower() in ('1', 'true', 'yes', 'on')
        if on and not P.active:
            P.applied_at, P.applied_by_id = timezone.now(), getattr(user, 'pk', None)
        P.active = on
    P.save()
    ep = (v.get('employee_code') or '').strip()
    if ep and has('EmployeeProfile.EmployeeCodeSetting'):
        import re
        m = re.fullmatch(r'([A-Za-z0-9\-_/]*)\{SEQ:(\d)\}', ep)
        if not m:
            raise GuideError(tr('Employee code: a prefix and {SEQ:n}, e.g. E{SEQ:4}.'), {'employee_code': 'Invalid.'})
        EC = M('EmployeeProfile', 'EmployeeCodeSetting')
        row = EC.objects.filter(branch_id__isnull=True).first() or EC(branch_id=None, next_number=1)
        row.prefix, row.padding, row.enabled, row.updated_by_id = m.group(1), int(m.group(2)), True, getattr(user, 'pk', None)
        E = M('EmpManagement', 'emp_master')
        # the next free number after the codes already used with this prefix
        n = row.next_number or 1
        while E.objects.filter(emp_code__iexact=row.format(n)).exists():
            n += 1
        row.next_number = n
        row.save()


# ---------------------------------------------------------------------- 5 organisation
def s_organisation():
    D, G, C = M('OrganisationManager', 'dept_master'), M('OrganisationManager', 'desgntn_master'), M('OrganisationManager', 'ctgry_master')
    rows = [[tr('Departments'), D.objects.count()], [tr('Designations'), G.objects.count()], [tr('Categories'), C.objects.count()]]
    for lbl, model in (('Grades', 'Grade'), ('Employment types', 'EmploymentType'), ('Cost centres', 'CostCenter'), ('Locations', 'Location')):
        if has(f'OrgStructure.{model}'):
            rows.append([tr(lbl), M('OrgStructure', model).objects.count()])
    dup = []
    for model, f in ((D, 'dept_name'), (G, 'desgntn_job_title'), (C, 'ctgry_title')):
        names = [((x or '').strip().lower()) for x in model.objects.values_list(f, flat=True)]
        dup += sorted({n for n in names if n and names.count(n) > 1})
    return {
        'fills': ['Standard lists ready to load', 'Office and Labour categories', 'Import template for each list'],
        'tables': [{'title': tr('Lists'), 'columns': [tr('List'), tr('Count')], 'rows': rows}],
        'actions': [{'key': 'load_org', 'label': tr('Load the standard lists'), 'help': tr('Adds the standard departments, designations, categories, grades and employment types that are missing.')}],
        'links': [link('Departments', 'sub-sidebar/department-master'), link('Designations', 'sub-sidebar/designation-master'),
                  link('Categories', 'sub-sidebar/category-master'), link('Grades, cost centres, employment types', 'org-structure')],
        'checks': [
            chk('dept', D.objects.exists(), 'Add at least one department.', MS + 'sub-sidebar/department-master'),
            chk('desg', G.objects.exists(), 'Add at least one designation.', MS + 'sub-sidebar/designation-master'),
            chk('cat', C.objects.exists(), 'Add at least one category (e.g. Office, Labour).', MS + 'sub-sidebar/category-master'),
            chk('dup', not dup, tr('Names used twice: {0}.', ', '.join(dup[:8])), None, 'warn'),
        ],
    }


def act_load_org(v, user):
    out = []
    try:
        from OrgStructure.seed import load_standard
        with transaction.atomic():
            r = load_standard(overwrite=False)
        out.append(str(r if not isinstance(r, dict) else sum(x if isinstance(x, int) else len(x) if isinstance(x, (list, tuple)) else 0 for x in r.values())))
    except Exception:
        log.exception('setup guide: org standard lists')
    B = branches()
    D, G, C = M('OrganisationManager', 'dept_master'), M('OrganisationManager', 'desgntn_master'), M('OrganisationManager', 'ctgry_master')
    added = 0
    for model, f, code_f, names in ((D, 'dept_name', 'dept_code', ['Administration', 'Human Resources', 'Finance', 'Operations', 'Sales', 'IT']),
                                    (G, 'desgntn_job_title', 'desgntn_code', ['Manager', 'Supervisor', 'Officer', 'Assistant', 'Driver', 'Labourer']),
                                    (C, 'ctgry_title', 'ctgry_code', ['Office', 'Labour'])):
        for n in names:
            if not model.objects.filter(**{f'{f}__iexact': n}).exists():
                kw = {f: n, code_f: n[:3].upper() + str(model.objects.count() + 1)}
                try:
                    with transaction.atomic():
                        obj = model.objects.create(**kw)
                        br = next((x for x in model._meta.get_fields() if x.name == 'branch'), None)
                        if br is not None and br.many_to_many and B:
                            obj.branch.set(B)
                except Exception:
                    log.exception('setup guide: list %s', n)
                    continue
                added += 1
    return tr('Standard lists loaded: {0} added; existing entries kept.', added)


# ---------------------------------------------------------------------- 6 time
EID_WORDS = ('eid', 'arafat', 'arafah', 'hijri', 'islamic new year', 'prophet', 'mawlid', 'isra', 'ramadan', 'عيد')


def eid_holidays(year):
    H = M('calendars', 'holiday')
    return [h for h in H.objects.filter(start_date__year=year).order_by('start_date')
            if any(w in (h.description or '').lower() for w in EID_WORDS)]


def s_time():
    year = timezone.localdate().year
    W = M('calendars', 'weekend_calendar')
    H = M('calendars', 'holiday')
    wk = W.objects.filter(year=year).first() or W.objects.order_by('-year').first()
    off = [d for d in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday') if wk and getattr(wk, d) == 'leave'] if wk else []
    R = M('AttendancePlus', 'AttendanceRule') if has('AttendancePlus.AttendanceRule') else None
    rule = R.objects.filter(scope='company', is_active=True).order_by('priority').first() if R else None
    Dev = M('AttendancePlus', 'Device') if has('AttendancePlus.Device') else None
    g = guide()
    conf = set((g.eid or {}).get(str(year), {}).get('confirmed', []))
    eids = eid_holidays(year)
    pend = [h for h in eids if h.pk not in conf]
    return {
        'fills': ['Holidays and weekend of the country', 'Moving holidays (Eid) marked to confirm', 'Late, early and overtime rules'],
        'fields': [
            field('weekend', 'Weekend', ' and '.join(tr(d.title()) for d in off) or tr('Not set'), readonly=True, help='Settings › Weekend calendar.'),
            field('start', 'Working hours from', rule.default_start.strftime('%H:%M') if rule and rule.default_start else '09:00', 'time'),
            field('end', 'Working hours to', rule.default_end.strftime('%H:%M') if rule and rule.default_end else '18:00', 'time'),
            field('break', 'Break (minutes)', rule.break_minutes if rule else 60, 'number'),
            field('grace', 'Grace (minutes)', rule.grace_in_minutes if rule else 15, 'number'),
            field('ot_after', 'Overtime after (hours a day)', float(rule.ot_daily_threshold_hours or 9) if rule and rule.ot_daily_threshold_hours else 9, 'number'),
            field('holidays', tr('Public holidays {0}', year), tr('{0} days loaded', H.objects.filter(start_date__year=year).count()), readonly=True),
        ],
        'eid': [{'id': h.pk, 'name': h.description, 'start': iso(h.start_date), 'end': iso(h.end_date), 'confirmed': h.pk in conf} for h in eids],
        'devices': Dev.objects.filter(is_active=True).count() if Dev else 0,
        'actions': [{'key': 'load_time', 'label': tr('Load the holidays and weekend of the country'), 'help': tr('This year and next; holidays already there are kept.')},
                    {'key': 'confirm_eid', 'label': tr('Confirm the Eid dates'), 'help': tr('The dates follow the moon sighting: change them if needed, then confirm. ZEO reminds you before each Eid.')}],
        'links': [link('Weekend calendar', 'settings/weekcalendar'), link('Holiday calendar', 'settings/holiday-calendar'),
                  link('Attendance rules', 'attendance-plus/rules'), link('Devices', 'attendance-plus/devices')],
        'matrix': True,
        'checks': [
            chk('weekend', off, 'Set the weekend.', MS + 'settings/weekcalendar'),
            chk('hours', rule is not None, 'Save the working hours (an attendance rule for the company is made).', MS + 'attendance-plus/rules'),
            chk('holidays', H.objects.filter(start_date__year=year).exists(), tr('Load the public holidays of {0}.', year), MS + 'settings/holiday-calendar'),
            chk('eid', not pend, tr('Confirm the Eid dates of {0}: {1}.', year, ', '.join(h.description for h in pend[:4])), None, 'warn'),
        ],
    }


def _time(v, label):
    try:
        return datetime.time.fromisoformat(str(v)[:5])
    except ValueError:
        raise GuideError(tr('{0}: enter a time (HH:MM).', tr(label)))


def save_time(v, user):
    if not has('AttendancePlus.AttendanceRule'):
        return
    R = M('AttendancePlus', 'AttendanceRule')
    rule = R.objects.filter(scope='company', is_active=True).order_by('priority').first()
    if rule is None:
        rule = R(name='Company working hours', scope='company', is_active=True, priority=100)
    if v.get('start'):
        rule.default_start = _time(v['start'], 'Working hours from')
    if v.get('end'):
        rule.default_end = _time(v['end'], 'Working hours to')
    if 'break' in v:
        rule.break_minutes = _int(v['break'], 0, 240, 'Break (minutes)')
    if 'grace' in v:
        rule.grace_in_minutes = _int(v['grace'], 0, 120, 'Grace (minutes)')
    if v.get('ot_after') not in (None, ''):
        try:
            rule.ot_daily_threshold_hours = Decimal(str(v['ot_after']))
        except Exception:
            raise GuideError(tr('Overtime after: enter hours.'))
        rule.ot_enabled = True
        rule.ot_basis = 'daily'
    rule.save()


WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def act_load_time(v, user):
    """The country's public holidays (this year and next) and, when the company has none, the weekend of the rule set –
    both assigned to every branch when nothing is assigned yet."""
    from CountryPack.holidays import seed_calendar
    year = timezone.localdate().year
    HC, AH = M('calendars', 'holiday_calendar'), M('calendars', 'assign_holiday')
    cal = HC.objects.filter(year=year).order_by('id').first() or HC.objects.create(calendar_title=tr('Public holidays {0}', year)[:100], year=year, created_by=user)
    added, _ = seed_calendar(cal, pack().get('pack') or 'GEN', year, user)
    brs = branches()
    if not AH.objects.exists() and brs:
        a = AH.objects.create(related_to='branch', holiday_model=cal, created_by=user)
        a.branch.set(brs)
    W, AW = M('calendars', 'weekend_calendar'), M('calendars', 'assign_weekend')
    made = False
    if not W.objects.exists():
        days = []
        try:
            from CountryPack.services import company_rule_set
            days = (company_rule_set() or {}).get('weekend_days') or []
        except Exception:
            days = []
        days = [d if isinstance(d, int) else WEEKDAYS.index(str(d).lower()) if str(d).lower() in WEEKDAYS else None for d in days] or [5, 6]
        kw = {d: ('leave' if i in days else 'fullday') for i, d in enumerate(WEEKDAYS)}
        w = W.objects.create(calendar_code='WEEKEND', description=tr('Weekend of the country'), year=year, created_by=user, **kw)
        if brs and not AW.objects.exists():
            a = AW.objects.create(related_to='branch', weekend_model=w, created_by=user)
            a.branch.set(brs)
        made = True
    return tr('{0} holidays added{1}.', len(added), tr(' and the weekend set') if made else '')


def act_confirm_eid(v, user):
    H = M('calendars', 'holiday')
    year = timezone.localdate().year
    ids = []
    for row in v.get('eid') or []:
        h = H.objects.filter(pk=row.get('id')).first()
        if h is None:
            continue
        s, e = _date(row.get('start')) or h.start_date, _date(row.get('end')) or h.end_date
        if e < s:
            raise GuideError(tr('{0}: the last day is before the first.', h.description))
        if (s, e) != (h.start_date, h.end_date):
            h.start_date, h.end_date = s, e
            h.save(update_fields=['start_date', 'end_date'])
        ids.append(h.pk)
    g = guide()
    eid = dict(g.eid or {})
    eid[str(year)] = {'confirmed': ids, 'at': timezone.now().isoformat(), 'by': getattr(user, 'pk', None)}
    g.eid = eid
    g.save(update_fields=['eid', 'updated_at'])
    return tr('{0} Eid holidays confirmed for {1}.', len(ids), year)


# ---------------------------------------------------------------------- 7 shifts
def s_shifts():
    S = M('calendars', 'Shift')
    rows = [[s.name, f"{s.start_time.strftime('%H:%M') if s.start_time else ''} – {s.end_time.strftime('%H:%M') if s.end_time else ''}",
             str(s.break_duration or '')] for s in S.objects.order_by('name')[:30]]
    Sch = M('calendars', 'EmployeeShiftSchedule')
    sch = list(Sch.objects.order_by('-id')[:30])
    R = M('ShiftPlanner', 'RosterPeriod') if has('ShiftPlanner.RosterPeriod') else None
    return {
        'fills': ['Shift templates of the industry', 'Rotation applied to teams or a whole category', 'Skip when everyone works the default hours'],
        'tables': [{'title': tr('Shifts'), 'columns': [tr('Shift'), tr('Time'), tr('Break')], 'rows': rows, 'add': MS + 'shift-options/shift-pattern'},
                   {'title': tr('Rosters (shift schedules)'), 'columns': [tr('Schedule'), tr('Pattern'), tr('From'), tr('Applies to')],
                    'rows': [[x.schedule_name, str(x.shift_pattern or ''), iso(x.start_date),
                              ', '.join([c.ctgry_title for c in x.categories.all()] + [b.branch_name for b in x.branches.all()]) or tr('{0} employees', x.employee.count())] for x in sch]}],
        'links': [link('Shift master (rules)', 'shift-planner/shifts'), link('Shift patterns', 'shift-options/shift-pattern'),
                  link('Employee shifts', 'shift-options/shift-employee'), link('Shift rosters', 'shift-planner/rosters')],
        'matrix': True,
        'checks': [
            chk('rosters', not S.objects.exists() or sch or (R and R.objects.exists()), 'Shifts exist but no roster uses them – give each shift team or category a roster.', MS + 'shift-options/shift-employee', 'warn'),
        ],
    }


# ---------------------------------------------------------------------- 8 leave
def s_leave():
    LT = M('calendars', 'leave_type')
    P = M('LeavePolicy', 'LeavePolicy') if has('LeavePolicy.LeavePolicy') else None
    pols = list(P.objects.filter(active=True)) if P else []
    cats = categories()
    covered = set()
    default = any(p.is_default for p in pols)
    rows = []
    for p in pols:
        names = [c.ctgry_title for c in cats if c.pk in (p.category_ids or [])]
        covered.update(p.category_ids or [])
        annual = p.lines.filter(leave_type_id__in=LT.objects.filter(leave_category='annual').values('id')).first() if hasattr(p, 'lines') else None
        rows.append([p.name, ', '.join(names) or (tr('Every category') if p.is_default else '–'), f'{annual.days_per_year} {tr("days")}' if annual else '–'])
    missing = [c.ctgry_title for c in cats if c.pk not in covered] if not default else []
    Bal = M('calendars', 'emp_leave_balance')
    return {
        'fills': ['Leave types per the Labour Law', 'Ready policies for 6 industries, DIFC and ADGM', 'Encashment component in payroll'],
        'tables': [{'title': tr('Leave policies'), 'columns': [tr('Policy'), tr('Categories'), tr('Annual')], 'rows': rows, 'add': MS + 'leave-options/leave-policies'}],
        'fields': [field('types', 'Leave types', tr('{0} leave types', LT.objects.count()), readonly=True)],
        'actions': [{'key': 'load_leave', 'label': tr('Load the standard leave setup'), 'help': tr('Leave types and policies of the country; existing ones are kept.')}],
        'links': [link('Leave policies', 'leave-options/leave-policies'), link('Leave types', 'leave-options/leave-master')],
        'matrix': True,
        'checks': [
            chk('types', LT.objects.exists(), 'Load the leave types.', MS + 'leave-options/leave-master'),
            chk('policy', pols, 'Add a leave policy.', MS + 'leave-options/leave-policies'),
            chk('categories', not missing, tr('No leave policy for: {0}.', ', '.join(missing)), MS + 'leave-options/leave-policies'),
            chk('opening', Bal.objects.exists() or not employees().exists(), 'Import the opening leave balances (or start them from today) – step 11.', None, 'warn'),
        ],
    }


def act_load_leave(v, user):
    try:
        from LeavePolicy import setup as leave_setup
        res = leave_setup.load(user)
        n = len(res.get('created_leave_types', [])) + len(res.get('created_policies', []) if isinstance(res.get('created_policies'), list) else [])
        return tr('Standard leave setup loaded: {0} added; existing setup kept.', n)
    except Exception as e:
        log.exception('setup guide: leave setup')
        raise GuideError(tr('The standard leave setup could not be loaded: {0}', str(e)[:120]))


# ---------------------------------------------------------------------- 9 payroll
BANK_FILES = {'AE': 'UAE WPS (SIF)', 'SA': 'KSA Mudad / WPS', 'QA': 'Qatar WPS (SIF)', 'OM': 'Oman WPS', 'KW': 'Kuwait bank file', 'BH': 'Bahrain WPS', 'IN': 'NEFT / bank upload'}


def s_payroll():
    SC = M('PayrollManagement', 'SalaryComponent')
    SS = M('PayrollManagement', 'SalaryStructure')
    ps = company_paystructure()
    PS = M('PayrollManagement', 'PayStructure')
    cats = categories()
    has_company_ps = ps is not None and not ps.category.exists() and not ps.branch.exists()
    no_ps = [] if has_company_ps else [c.ctgry_title for c in cats if not PS.objects.filter(category=c).exists()]
    pk = pack().get('pack')
    s = pp_settings()
    stat = M('PayrollPlus', 'StatutoryScheme').objects.count() if has('PayrollPlus.StatutoryScheme') else 0
    c = company()
    E = employees()
    no_struct = E.exclude(pk__in=M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(is_active=True).values('employee_id')).count() if E.exists() else 0
    return {
        'fills': ['Components, formulas and statutory schemes of the country', tr('Bank file of the country: {0}', BANK_FILES.get(pk, 'bank upload')), 'Payslip layout'],
        'fields': [
            field('components', 'Salary components', ' · '.join(SC.objects.order_by('id').values_list('name', flat=True)[:6]) or tr('None yet'), readonly=True),
            field('structures', 'Salary structures', ' · '.join(SS.objects.order_by('id').values_list('name', flat=True)[:6]) or tr('None yet'), readonly=True),
            field('cycle', 'Attendance cut-off', 'month' if not ps or ps.attendance_cycle_type == 'MONTH' else 'custom', 'select', [('month', 'Calendar month (1st – last day)'), ('custom', 'Custom cut-off')]),
            field('cycle_start', 'Cut-off: from day', ps.cycle_start_day if ps and ps.cycle_start_day else 26, 'number'),
            field('payday', 'Pay date (day of the month)', ps.payday if ps and ps.payday else 28, 'number'),
            field('bank_file', 'Bank file', BANK_FILES.get(pk, 'Bank upload'), readonly=True, purple=True),
            field('payslip_language', 'Payslip', s.payslip_language if s else 'reader', 'select', M('PayrollPlus', 'PayrollPlusSettings').PAYSLIP_LANG if s else []),
            field('statutory', 'Statutory schemes', tr('{0} schemes', stat), readonly=True),
        ],
        'links': [link('Salary components', 'salary-options/salary'), link('Salary structures', 'salary-options/salary-structure'),
                  link('Pay structure', 'salary-options/pay-structure'), link('Statutory schemes', 'salary-options/statutory-schemes'),
                  link('Payroll controls', 'salary-options/payroll-controls')],
        'matrix': True,
        'info_only': not payroll_on(),
        'checks': [] if not payroll_on() else [
            chk('components', SC.objects.exists(), 'Add the salary components (Basic, Housing, Transport …).', MS + 'salary-options/salary'),
            chk('paystructure', not no_ps, tr('No pay structure for: {0} – the default rules apply.', ', '.join(no_ps)), MS + 'salary-options/pay-structure', 'warn'),
            chk('employer', getattr(c, 'employer_unique_id', '') or pk not in ('AE', 'QA', 'OM', 'BH'), 'Enter the employer ID of the bank file (step 2).', None),
            chk('salaries', no_struct == 0, tr('{0} employees have no salary yet.', no_struct), MS + 'salary-options/salary-structure', 'warn'),
        ],
    }


def save_payroll(v, user):
    ps = company_paystructure(create=True)
    if v.get('cycle') == 'custom':
        d = _int(v.get('cycle_start') or 26, 1, 31, 'Cut-off: from day')
        ps.attendance_cycle_type, ps.cycle_start_day, ps.cycle_end_day = 'CUSTOM', d, (d - 1) or 31
    elif v.get('cycle') == 'month':
        ps.attendance_cycle_type = 'MONTH'
    if v.get('payday') not in (None, ''):
        ps.payday = _int(v['payday'], 1, 31, 'Pay date (day of the month)')
    ps.save()
    s = pp_settings()
    if s is not None and v.get('payslip_language') in ('reader', 'en', 'en_ar'):
        s.payslip_language = v['payslip_language']
        s.save(update_fields=['payslip_language', 'updated_at'])


# ---------------------------------------------------------------------- 10 approvals
APPROVAL_TYPES = [
    # label, level model, link
    ('Leave, encashment', 'calendars.LeaveApprovalLevels', 'leave-options/leave-approval-level'),
    ('Late in / early out', 'calendars.LateinEarlyoutApprovalLevel', 'attendance-sidebar/latein-earlyout-approval-level'),
    ('Payroll run (payslips)', 'PayrollManagement.PayslipCommonWorkflow', 'salary-options/payroll-approval-level'),
    ('Loans', 'PayrollManagement.LoanCommonWorkflow', 'loan-sidebar/loan-approval-level'),
    ('Advance salary', 'PayrollManagement.AdvanceCommonWorkflow', 'salary-options/advance-salary-approval-level'),
    ('Air tickets', 'PayrollManagement.AirticketWorkflow', 'air-ticket-options/airticket-approval-level'),
    ('General requests', 'EmpManagement.ApprovalLevel', 'general-sidebar/approval-level'),
    ('Document requests', 'EmpManagement.DocumentApprovalLevel', 'general-sidebar/document-request-level'),
    ('Resignation, settlement', 'EmpManagement.ResignationApprovalLevel', 'sub-sidebar/resignation-approval-level'),
    ('Asset requests', 'OrganisationManager.AssetApprovalLevel', 'asset-options/asset-approval-level'),
    ('Manpower requisitions', 'RecruitmentManagement.RequisitionApprovalLevel', 'recruitment-options'),
]


def _approver(lv):
    for f in ('role', 'approver'):
        v = getattr(lv, f, None)
        if v:
            if hasattr(v, 'username'):
                return v.get_full_name() or v.username
            return tr(str(v).replace('_', ' ').capitalize())
    return '–'


def s_approvals():
    rows, missing = [], []
    for label, model, path in APPROVAL_TYPES:
        if not has(model):
            continue
        Mo = M(*model.split('.'))
        order = 'level' if any(f.name == 'level' for f in Mo._meta.concrete_fields) else 'id'
        lv = list(Mo.objects.order_by(order)[:3])
        cells = [_approver(x) for x in lv] + [''] * (3 - len(lv))
        rows.append({'cells': [tr(label)] + cells, 'link': MS + path, 'set': bool(lv)})
        if not lv:
            missing.append(tr(label))
    s = pp_settings()
    try:
        from .approval_defaults import missing as _missing
        refused = [(tr(k), v) for k, v in _missing()]
    except Exception:
        refused = []
    try:   # v1.56.1 the standard chains route requests to the reporting manager
        from EmpManagement.models import emp_master as _E
        no_mgr = _E.objects.filter(is_active=True, emp_reporting_manager__isnull=True).count()
    except Exception:
        no_mgr = 0
    return {
        'fills': ['Suggested chains by company size', 'Delegation while an approver is on leave', 'Escalation days per request'],
        'approvals': rows,
        'fields': [
            field('segregation', 'Payroll: the preparer cannot approve', bool(s.segregation) if s else True, 'check'),
            field('settlement_due_days', 'Final settlement due (days)', s.settlement_due_days if s else 14, 'number'),
            field('wps_late_days', 'Salary paid by (day of the next month)', s.wps_late_days if s else 15, 'number'),
        ],
        'actions': [{'key': 'standard_chains', 'label': tr('Set up the standard chains'),
                     'help': tr('The reporting manager approves every request type that has no chain yet; existing chains are kept.')}],
        'links': [link('Payroll controls', 'salary-options/payroll-controls'), link('Out of office (delegation)', 'settings/out-of-office')],
        'checks': [
            chk('no_chain', not refused, tr('Requests are refused until these have a chain: {0} – use "Set up the standard chains".',
                                            '; '.join(f'{k} ({", ".join(v)})' for k, v in refused)), None),
            chk('chains', not missing, tr('No approval levels yet (the reporting manager approves): {0}.', ', '.join(missing)), None, 'warn'),
            chk('no_manager', not no_mgr, tr('{0} employees have no reporting manager – the reporting manager approves their requests, so their requests are refused until one is set.', no_mgr),
                MS + 'sub-sidebar/employee-master', 'warn'),
            chk('segregation', not payroll_on() or (s and s.segregation), 'Payroll approver is not the preparer: switch on the control.', MS + 'salary-options/payroll-controls', 'warn'),
        ],
    }


def act_standard_chains(v, user):
    from .approval_defaults import ensure
    added = ensure(user=user)
    return tr('Standard chains set up: {0}.', '; '.join(added)) if added else tr('Every request type already has a chain.')


def save_approvals(v, user):
    s = pp_settings()
    if s is None:
        return
    if 'segregation' in v:
        s.segregation = str(v['segregation']).lower() in ('1', 'true', 'yes', 'on')
    for k, label in (('settlement_due_days', 'Final settlement due (days)'), ('wps_late_days', 'Salary paid by (day of the next month)')):
        if v.get(k) not in (None, ''):
            setattr(s, k, _int(v[k], 0, 60, label))
    s.save()


# ---------------------------------------------------------------------- 11 employees
def s_employees():
    E = employees()
    n = E.count()
    no_cat = E.filter(emp_ctgry_id__isnull=True).count() if n else 0
    no_mgr = E.filter(emp_reporting_manager__isnull=True, emp_reports_to__isnull=True).count() if n else 0
    iban = 0
    try:
        BD = M('EmpManagement', 'EmployeeBankDetail')
        if n:
            have = BD.objects.filter(is_active=True).exclude(iban_number__isnull=True).exclude(iban_number='').values('employee_id')
            iban = E.exclude(pk__in=have).count()
    except Exception:
        log.exception('setup guide: iban')
    s = pp_settings()
    G = M('PayrollPlus', 'GoLiveEmployee') if has('PayrollPlus.GoLiveEmployee') else None
    Bal = M('calendars', 'emp_leave_balance')
    loans = M('PayrollManagement', 'LoanApplication').objects.filter(status__in=['approved', 'Approved', 'disbursed']).count()
    golive = s.paid_up_to if s else None
    return {
        'fills': ['Employee codes from the numbering rule', 'Documents with expiry dates', 'Manager from the reporting line'],
        'counts': {'employees': n, 'no_category': no_cat, 'no_manager': no_mgr, 'no_iban': iban},
        'fields': [
            field('paid_up_to', 'Salary paid up to', iso(golive), 'date', purple=True,
                  help='Months up to this date were paid outside ZEO: payroll runs for them leave everybody out. Empty = no salary paid before ZEO.'),
            field('balances', 'Opening leave balances', tr('Imported – {0} employees', Bal.objects.values('employee').distinct().count()) if Bal.objects.exists() else tr('Not imported'), readonly=True),
            field('golive_rows', 'Employee exceptions (paid up to, unpaid days, gratuity start)', tr('{0} employees', G.objects.count()) if G else '0', readonly=True, purple=True),
            field('loans', 'Open loans and advances', tr('Imported – {0} loans', loans) if loans else tr('None'), readonly=True),
        ],
        'links': [link('Employees (import)', 'sub-sidebar/employee-master'), link('Leave balances', 'leave-options/leave-master'),
                  link('Loans', 'salary-options/loans')],
        'template': 'golive',
        'checks': [
            chk('employees', n, 'Import the employees.', MS + 'sub-sidebar/employee-master'),
            chk('category', no_cat == 0, tr('{0} employees have no category.', no_cat), MS + 'sub-sidebar/employee-master'),
            chk('manager', no_mgr == 0, tr('{0} employees have no reporting manager.', no_mgr), MS + 'sub-sidebar/employee-master', 'warn'),
            chk('iban', not payroll_on() or iban == 0, tr('{0} employees without IBAN.', iban), MS + 'sub-sidebar/employee-master', 'warn'),
            chk('paid_up_to', not payroll_on() or (s and s.paid_up_to) or 'paid_none' in (guide().policies or {}),
                'Set "Salary paid up to" – or save it empty when no salary was paid before ZEO.', None, 'warn'),
        ],
    }


def save_employees(v, user):
    s = pp_settings()
    if s is None:
        return
    if 'paid_up_to' in v:
        s.paid_up_to = _date(v.get('paid_up_to'))
        s.save(update_fields=['paid_up_to', 'updated_at'])
        g = guide()
        pol = dict(g.policies or {})
        pol['paid_none'] = s.paid_up_to is None
        g.policies = pol
        g.save(update_fields=['policies', 'updated_at'])
        try:
            from PayrollPlus.golive import forget
            forget()
        except ImportError:
            pass


# ---------------------------------------------------------------------- 12 users
def s_users():
    from django.contrib.auth import get_user_model
    U = get_user_model()
    c = company()
    users = U.objects.filter(tenants=c, is_active=True).distinct() if c else U.objects.none()
    staff = users.filter(is_ess=False)
    E = employees()
    linked = E.exclude(users__isnull=True).count()
    I = M('ZeoAdmin', 'SignInInvite')
    pending = I.objects.filter(used_at__isnull=True, expires_at__gt=timezone.now()).count()
    rows = []
    for u in staff.order_by('username')[:30]:
        groups = ', '.join(u.groups.values_list('name', flat=True)[:3]) if hasattr(u, 'groups') else ''
        rows.append([u.get_full_name() or u.username, groups or ('Administrator' if u.is_superuser else '–'), u.email or ''])
    no_login = E.filter(users__isnull=True).count()
    no_mail = E.filter(users__isnull=True).filter(Q(emp_personal_email__isnull=True) | Q(emp_personal_email=''), Q(emp_company_email__isnull=True) | Q(emp_company_email='')).count()
    return {
        'fills': ['Role groups of every active app', 'Manager rights from the reporting line', 'Mobile app QR code for employees'],
        'tables': [{'title': tr('Users'), 'columns': [tr('Person'), tr('Role'), tr('E-mail')], 'rows': rows, 'add': MS + 'settings/user-master'}],
        'counts': {'employees_with_login': linked, 'without_login': no_login, 'without_email': no_mail, 'invites_pending': pending},
        'actions': [{'key': 'invite', 'label': tr('Send sign-in links to employees without a login'),
                     'help': tr('Each employee gets an e-mail link to choose their own password (valid 7 days) – no default password.')}],
        'links': [link('Users', 'settings/user-master'), link('Permission groups', 'settings/user-grouping-master'), link('Sign-in security', 'settings/sign-in-security')],
        'checks': [
            chk('admin', staff.exists(), 'Add the HR and payroll users.', MS + 'settings/user-master'),
            chk('ess', no_login == 0 or pending, tr('{0} employees have no login yet – send the sign-in links.', no_login), None, 'warn'),
            chk('email', no_mail == 0, tr('{0} employees have no e-mail – add one to send them a link.', no_mail), MS + 'sub-sidebar/employee-master', 'warn'),
        ],
    }


def act_invite(v, user, base_url=''):
    from . import invites
    n, skipped = invites.invite_employees(user, base_url)
    return tr('{0} sign-in links sent; {1} employees without e-mail skipped.', n, skipped)


# ---------------------------------------------------------------------- 13 modules
MODULES = [
    ('expenses', 'Expenses', 'Categories, limits, mileage', 'expense-options', 'ExpenseManagement.ExpenseCategory'),
    ('loans', 'Loans and advances', 'Loan types, maximum, months', 'loan-sidebar/loan-type', 'PayrollManagement.LoanType'),
    ('air_ticket', 'Air ticket', 'Entitlement, class, frequency', 'air-ticket-options/air-ticket-policy', 'PayrollManagement.AirTicketPolicy'),
    ('assets', 'HR assets', 'Asset types and allocation', 'asset-options/asset-type', 'OrganisationManager.AssetType'),
    ('recruitment', 'Recruitment', 'Stages and offer letter', 'recruitment-options', 'RecruitmentManagement.JobOpening'),
    ('performance', 'Performance', 'Annual review cycle', 'performance-options', 'PerformanceManagement.AppraisalCycle'),
    ('training', 'Training', 'Course catalogue', 'learning-options', 'LearningManagement.Course'),
    ('benefits', 'Benefits and engagement', 'Insurance, surveys', 'zeo-people/home', 'ZeoPeople.BenefitPlan'),
    ('projects', 'Projects and timesheets', 'Projects, cost codes', 'project-options/project-stages', 'ProjectManagement.Project'),
]


def s_modules():
    on = set((guide().policies or {}).get('modules', []))
    rows = []
    for key, title, hint, path, model in MODULES:
        n = M(*model.split('.')).objects.count() if has(model) else 0
        rows.append({'key': key, 'title': tr(title), 'hint': tr(hint), 'link': MS + path, 'on': key in on, 'records': n})
    return {
        'fills': ['A quick setup for each module switched on', 'Standard expense categories and loan rules', 'Modules can be switched on later'],
        'modules': rows,
        'checks': [chk(f'mod_{r["key"]}', not r['on'] or r['records'], tr('{0}: open its setup – nothing is set up yet.', r['title']), r['link'], 'warn') for r in rows],
    }


def save_modules(v, user):
    keys = {m[0] for m in MODULES}
    sel = [k for k in (v.get('modules') or []) if k in keys]
    g = guide()
    pol = dict(g.policies or {})
    pol['modules'] = sel
    g.policies = pol
    g.save(update_fields=['policies', 'updated_at'])


# ---------------------------------------------------------------------- 14 communication
def s_communication():
    LT = M('SelfService', 'LetterTemplate') if has('SelfService.LetterTemplate') else None
    letters = list(LT.objects.filter(is_active=True)) if LT else []
    unsigned = [x.title for x in letters if not (x.signatory_name or '').strip()]
    p = profile()
    CP = M('OrganisationManager', 'CompanyPolicy')
    mail_ok = (guide().policies or {}).get('test_mail_ok')
    return {
        'fills': ['Standard letter templates in English and Arabic', 'Document expiry reminders', 'Accounting mapping when connected'],
        'fields': [
            field('letters', 'Letter templates', tr('{0} templates', len(letters)), readonly=True),
            field('signatory', 'Signatory on letters', (p.signatory_name if p else '') or '', help='Company profile; a template can have its own.'),
            field('policies', 'Policies', tr('{0} policies', CP.objects.count()), readonly=True),
            field('test_to', 'Send a test e-mail to', '', help='Checks Settings › E-mail configuration.'),
        ],
        'actions': [{'key': 'test_mail', 'label': tr('Send the test e-mail')}],
        'links': [link('HR letters', 'self-service/hr-letters'), link('Company policy', 'settings/company-policy'),
                  link('Announcements', 'general-sidebar/announcement-master'), link('E-mail configuration', 'settings/email-configuration'),
                  link('Document expiry e-mails', 'settings/doc-exp-emailtemplate')],
        'checks': [
            chk('mail', mail_ok, 'Send a test e-mail and check it arrives.', MS + 'settings/email-configuration', 'warn'),
            chk('signatory', (p and p.signatory_name) or (letters and not unsigned), 'Enter the signatory of the letters.', None, 'warn'),
        ],
    }


def save_communication(v, user):
    p = profile()
    if p is not None and 'signatory' in v:
        p.signatory_name = (v.get('signatory') or '').strip()[:120]
        p.save()


def act_test_mail(v, user):
    to = (v.get('test_to') or getattr(user, 'email', '') or '').strip()
    if '@' not in to:
        raise GuideError(tr('Enter an e-mail address.'))
    from django.core.mail import send_mail
    try:
        from django.conf import settings as dj
        send_mail(tr('ZEO test e-mail'), tr('The e-mail of ZEO works. You can close this message.'), getattr(dj, 'DEFAULT_FROM_EMAIL', None), [to], fail_silently=False)
    except Exception as e:
        raise GuideError(tr('The e-mail could not be sent: {0}', str(e)[:160]))
    g = guide()
    pol = dict(g.policies or {})
    pol['test_mail_ok'] = timezone.now().isoformat()
    g.policies = pol
    g.save(update_fields=['policies', 'updated_at'])
    return tr('Test e-mail sent to {0}.', to)


# ---------------------------------------------------------------------- 15 go live
GAPS = ['Hourly leave is not built (days and half days).', 'Split shifts are not supported.',
        'Server set-up (SMTP, push notifications, backups, SSL, Celery) is done once by Adept.']


def s_golive():
    out, warn = [], []
    for key, no, title, stage, can_skip, optional in STEPS:
        if key == 'golive':
            continue
        try:
            st = STATE[key]()
        except Exception:
            log.exception('setup guide: step %s', key)
            continue
        for c in st.get('checks', []):
            if not c['ok']:
                c = dict(c, step=key, step_no=no)
                (out if c['level'] == 'block' and not (optional or BY_KEY[key][4] and step_status(key) == 'skipped') else warn).append(c)
    s = pp_settings()
    PR = M('PayrollManagement', 'PayrollRun')
    trial = None
    if payroll_on():
        trial = PR.objects.exclude(status='pending').order_by('-year', '-month').first()
    g = guide()
    return {
        'fills': ['Every check with a link to the screen to fix it', 'Trial payroll sheet and bank-file preview', 'Home checklist for warnings'],
        'blocking': out, 'warnings': warn, 'accepted': g.accepted or {},
        'trial': {'id': trial.pk, 'name': trial.name, 'status': trial.status} if trial else None,
        'info': [tr(x) for x in GAPS],
        'live_at': g.live_at.isoformat() if g.live_at else None,
        'links': [link('Payroll runs', 'salary-options/pay-roll'), link('AI payroll auditor', 'zeo-ai/payroll-auditor'), link('Test company', 'settings/test-company')],
        'checks': [chk('trial', not payroll_on() or trial, 'Run a trial payroll for the first month and check it.', MS + 'salary-options/pay-roll', 'warn')],
    }


def go_live(v, user):
    st = s_golive()
    if st['blocking']:
        raise GuideError(tr('{0} items still block go-live.', len(st['blocking'])))
    notes = v.get('accept') or {}
    need = [w['key'] + '@' + w.get('step', 'golive') for w in st['warnings'] + [c for c in st['checks'] if not c['ok']]]
    g = guide()
    acc = dict(g.accepted or {})
    for k in need:
        note = (notes.get(k) or '').strip() if isinstance(notes, dict) else ''
        if k not in acc and note:
            acc[k] = {'note': note[:300], 'by': getattr(user, 'pk', None), 'at': timezone.now().isoformat()}
    g.accepted = acc
    g.live_at, g.live_by_id = timezone.now(), getattr(user, 'pk', None)
    g.save()
    mark(g, 'golive', 'done', user)
    return tr('ZEO is live. Open warnings stay on the Home checklist.')


STATE = {'start': s_start, 'company': s_company, 'branches': s_branches, 'numbering': s_numbering, 'organisation': s_organisation,
         'time': s_time, 'shifts': s_shifts, 'leave': s_leave, 'payroll': s_payroll, 'approvals': s_approvals, 'employees': s_employees,
         'users': s_users, 'modules': s_modules, 'communication': s_communication, 'golive': s_golive}
SAVE = {'start': save_start, 'company': save_company, 'numbering': save_numbering, 'time': save_time, 'payroll': save_payroll,
        'approvals': save_approvals, 'employees': save_employees, 'modules': save_modules, 'communication': save_communication}
ACTIONS = {'standard_chains': act_standard_chains, 'country_defaults': act_country_defaults, 'load_org': act_load_org, 'load_time': act_load_time, 'confirm_eid': act_confirm_eid, 'load_leave': act_load_leave,
           'invite': act_invite, 'test_mail': act_test_mail}


# ====================================================================== progress
def step_status(key):
    return ((guide().steps or {}).get(key) or {}).get('status', '')


def mark(g, key, status, user):
    steps = dict(g.steps or {})
    steps[key] = {'status': status, 'at': timezone.now().isoformat(), 'by': getattr(user, 'pk', None)}
    g.steps = steps
    nxt = next((k for k in KEYS[KEYS.index(key) + 1:] if (steps.get(k) or {}).get('status') != 'done'), key)
    g.current = nxt
    g.save(update_fields=['steps', 'current', 'updated_at'])


def overview(full=False):
    g = guide()
    rows = []
    for key, no, title, stage, can_skip, optional in STEPS:
        r = {'key': key, 'no': no, 'name': tr(NAMES[key]), 'title': tr(title), 'stage': tr(stage), 'can_skip': can_skip, 'optional': optional,
             'status': ((g.steps or {}).get(key) or {}).get('status', '')}
        if full:
            try:
                st = STATE[key]()
                cs = st.get('checks', [])
                r['open'] = sum(1 for c in cs if not c['ok'] and c['level'] == 'block')
                r['warnings'] = sum(1 for c in cs if not c['ok'] and c['level'] == 'warn')
            except Exception:
                log.exception('setup guide: step %s', key)
        rows.append(r)
    done = sum(1 for r in rows if r['status'] == 'done')
    return {'steps': rows, 'current': g.current or 'start', 'done': done, 'total': len(rows), 'live_at': g.live_at.isoformat() if g.live_at else None,
            'opened_at': g.opened_at.isoformat() if g.opened_at else None}


def step(key):
    if key not in STATE:
        raise GuideError(tr('Not found.'), status=404)
    key, no, title, stage, can_skip, optional = BY_KEY[key]
    st = STATE[key]()
    st.update({'key': key, 'no': no, 'name': tr(NAMES[key]), 'title': tr(title), 'stage': tr(stage), 'can_skip': can_skip, 'optional': optional,
               'status': step_status(key), 'fills': [tr(x) for x in st.get('fills', [])]})
    return st


def post(key, data, user, base_url=''):
    """{action: save | skip | <step action> | go_live, values: {...}} → (message, step state)."""
    if key not in STATE:
        raise GuideError(tr('Not found.'), status=404)
    action = data.get('action') or 'save'
    v = data.get('values') or {}
    g = guide()
    msg = ''
    if action == 'skip':
        if not BY_KEY[key][4]:
            raise GuideError(tr('This step is needed before the first payroll and cannot be skipped.'))
        mark(g, key, 'skipped', user)
        return tr('Skipped – the step stays on the checklist.'), step(key)
    if action == 'go_live':
        if key != 'golive':
            raise GuideError(tr('Not found.'), status=404)
        return go_live(v, user), step(key)
    if action in ACTIONS:
        fn = ACTIONS[action]
        if action == 'invite':   # one employee at a time: a login that cannot be made does not undo the others
            msg = fn(v, user, base_url)
        else:
            with transaction.atomic():
                msg = fn(v, user)
        return msg, step(key)
    if action not in ('save', 'next'):
        raise GuideError(tr('Not found.'), status=404)
    with transaction.atomic():
        if key in SAVE:
            SAVE[key](v, user)
    st = step(key)
    if action == 'next':
        blocking = [c for c in st.get('checks', []) if not c['ok'] and c['level'] == 'block']
        if blocking:
            raise GuideError(tr('Fix these before going on: {0}', ' · '.join(c['text'] for c in blocking)), {'checks': blocking})
        mark(g, key, 'done', user)
        st['status'] = 'done'
        msg = tr('Saved.')
    else:
        msg = tr('Saved.')
    return msg, st


def home_items():
    """After go-live: the open warnings for the Home checklist (empty before go-live)."""
    g = guide()
    if not g.live_at:
        return []
    st = s_golive()
    acc = g.accepted or {}
    items = [{'text': c['text'], 'link': c.get('link'), 'step': c.get('step')} for c in st['warnings'] if f"{c['key']}@{c['step']}" not in acc]
    return items[:12]


def eid_reminder():
    """Daily: an Eid holiday starting within 21 days whose dates were not confirmed this year → the company administrators
    are reminded once (dates follow the moon sighting)."""
    today = timezone.localdate()
    g = guide()
    conf = set((g.eid or {}).get(str(today.year), {}).get('confirmed', []))
    sent = set((g.eid or {}).get(str(today.year), {}).get('reminded', []))
    due = [h for h in eid_holidays(today.year) if h.pk not in conf and h.pk not in sent and today <= h.start_date <= today + datetime.timedelta(days=21)]
    if not due:
        return 0
    from django.contrib.auth import get_user_model
    from zeo.module_helpers import notify
    U = get_user_model()
    c = company()
    admins = U.objects.filter(tenants=c, is_active=True, is_ess=False).distinct() if c else U.objects.none()
    try:
        from AccessControl.access import _zeo_is_admin
        admins = [u for u in admins if u.is_superuser or _zeo_is_admin(u)]
    except Exception:
        admins = [u for u in admins if u.is_superuser]
    for u in admins:
        notify(user=u, title=tr('Confirm the Eid dates'),
               message=tr('{0} starts on {1} as expected – confirm or change the dates in Settings › Setup guide › Time.', due[0].description, due[0].start_date.isoformat()))
    eid = dict(g.eid or {})
    y = dict(eid.get(str(today.year), {}))
    y['reminded'] = sorted(sent | {h.pk for h in due})
    eid[str(today.year)] = y
    g.eid = eid
    g.save(update_fields=['eid', 'updated_at'])
    return len(due)
