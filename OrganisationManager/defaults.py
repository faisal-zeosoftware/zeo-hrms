"""
Standard departments, designations, categories and religions (v1.7.2).

* New companies get them when the company is created; new branches are linked to them.
* Existing companies load them from the Department / Designation / Category list:
  Import → "Load the standard list" (only the missing ones are added; nothing is changed or removed).

Codes follow the existing pattern <CODE>-<first 3 letters of the company schema>, e.g. HR-ADE.
The first five departments, designations and both categories are the ones every company already had.
"""
from django.apps import apps
from django.db import connection, transaction

DEPARTMENTS = [
    ('HR', 'Human Resources', 'People, recruitment, payroll input and employee relations'),
    ('IT', 'Information Technology', 'Systems, network, software and support'),
    ('FIN', 'Finance', 'Accounts, treasury, budgeting and reporting'),
    ('SAL', 'Sales', 'Customer acquisition and account management'),
    ('OPS', 'Operations', 'Day-to-day operations and service delivery'),
    ('MGT', 'Management', 'Managing director, general manager and executive office'),
    ('ADM', 'Administration', 'Office administration, PRO / government relations and facilities'),
    ('MKT', 'Marketing', 'Brand, digital marketing and communications'),
    ('PRC', 'Procurement', 'Purchasing and supplier management'),
    ('LOG', 'Logistics & Warehouse', 'Stores, inventory, transport and delivery'),
    ('CS', 'Customer Service', 'Customer support and after-sales service'),
    ('HSE', 'Health, Safety & Environment', 'Workplace safety, health and environmental compliance'),
    ('QA', 'Quality', 'Quality assurance and quality control'),
    ('LEG', 'Legal & Compliance', 'Contracts, legal matters and regulatory compliance'),
]

DESIGNATIONS = [
    ('MGR', 'Manager', 'Manages a department or a function'),
    ('TL', 'Team Lead', 'Leads a team within a department'),
    ('EXE', 'Executive', 'Handles the work of a function'),
    ('SE', 'Senior Executive', 'Experienced executive'),
    ('AST', 'Assistant', 'Supports a team or a manager'),
    ('GM', 'General Manager', 'Heads the company or a business unit'),
    ('AM', 'Assistant Manager', 'Deputy to a manager'),
    ('SUP', 'Supervisor', 'Supervises staff on a shift or a site'),
    ('OFF', 'Officer', 'Specialist role, e.g. HR officer or PRO'),
    ('CRD', 'Coordinator', 'Coordinates work between teams'),
    ('ACC', 'Accountant', 'Book-keeping and accounts'),
    ('ENG', 'Engineer', 'Engineering or technical specialist'),
    ('TEC', 'Technician', 'Installs, maintains and repairs'),
    ('DRV', 'Driver', 'Company driver'),
    ('OA', 'Office Assistant', 'Office support and messenger'),
    ('INT', 'Intern', 'Trainee or intern'),
]

CATEGORIES = [
    ('TECH', 'Technical', 'Technical and engineering staff'),
    ('NON-TECH', 'Non-Technical', 'Office and support staff'),
    ('MGMT', 'Management', 'Senior management'),
    ('STAFF', 'Office Staff', 'Office-based employees'),
    ('FIELD', 'Field / Site Staff', 'Employees working at sites or with customers'),
    ('SKL', 'Skilled Worker', 'Skilled labour (MOHRE skill levels 1–3)'),
    ('USK', 'Unskilled Worker', 'Unskilled labour (MOHRE skill levels 4–5)'),
]

RELIGIONS = ['Islam', 'Christianity', 'Hinduism', 'Buddhism', 'Sikhism', 'Judaism', 'Other']

KINDS = {
    'department': ('OrganisationManager.dept_master', 'dept_name', 'dept_code', 'dept_description', DEPARTMENTS),
    'designation': ('OrganisationManager.desgntn_master', 'desgntn_job_title', 'desgntn_code', 'desgntn_description', DESIGNATIONS),
    'category': ('OrganisationManager.ctgry_master', 'ctgry_title', 'ctgry_code', 'ctgry_description', CATEGORIES),
}


def _suffix(schema=None):
    schema = schema or getattr(connection, 'schema_name', '') or ''
    return schema[:3].upper()


def preview(kind, schema=None):
    """[{code, name, description, exists}] for one kind."""
    model_label, name_f, code_f, desc_f, rows = KINDS[kind]
    model = apps.get_model(model_label)
    out = []
    for code, name, desc in rows:
        exists = model.objects.filter(**{f'{name_f}__iexact': name}).exists()
        out.append({'code': f'{code}-{_suffix(schema)}', 'name': name, 'description': desc, 'exists': exists})
    return out


def _free_code(model, code_f, code):
    if not model.objects.filter(**{f'{code_f}__iexact': code}).exists():
        return code
    n = 2
    while model.objects.filter(**{f'{code_f}__iexact': f'{code}{n}'}).exists():
        n += 1
    return f'{code}{n}'


@transaction.atomic
def load(kind, branches, user=None, schema=None, create=True):
    """Add the missing standard records of one kind and link all standard ones to the given branches.
    Returns {'created': [...names], 'linked': n}."""
    model_label, name_f, code_f, desc_f, rows = KINDS[kind]
    model = apps.get_model(model_label)
    prefix = {'department': 'dept', 'designation': 'desgntn', 'category': 'ctgry'}[kind]
    created, linked = [], 0
    for code, name, desc in rows:
        obj = model.objects.filter(**{f'{name_f}__iexact': name}).first()
        if obj is None and not create:
            continue
        if obj is None:
            values = {name_f: name, code_f: _free_code(model, code_f, f'{code}-{_suffix(schema)}'), desc_f: desc}
            if user is not None:
                values[f'{prefix}_created_by'] = user
            obj = model.objects.create(**values)
            created.append(name)
        if branches:
            before = obj.branch.count()
            obj.branch.add(*branches)
            linked += obj.branch.count() - before
    return {'created': created, 'linked': linked}


def load_religions():
    Religion = apps.get_model('Core', 'ReligionMaster')
    added = []
    for name in RELIGIONS:
        if not Religion.objects.filter(religion__iexact=name).exists():
            Religion.objects.create(religion=name)
            added.append(name)
    return added


# v1.40.1: standard lists of the three classic skill masters (Core.LanguageSkill / MarketingSkill / ProgrammingLanguageSkill).
# They were empty in a new company (only the language list is filled by `load_master_data`), so the Language / Skill
# dropdowns of the employee page had nothing to pick. Offered from the employee page as "Load the standard list".
LANGUAGE_SKILLS = ['Arabic', 'English', 'Hindi', 'Urdu', 'Malayalam', 'Tamil', 'Telugu', 'Bengali', 'Tagalog', 'Nepali', 'Sinhala',
                   'French', 'German', 'Spanish', 'Russian', 'Chinese', 'Persian', 'Turkish', 'Punjabi', 'Kannada', 'Marathi', 'Gujarati',
                   'Amharic', 'Swahili', 'Italian', 'Portuguese']
MARKETING_SKILLS = ['Digital marketing', 'Social media marketing', 'Content marketing', 'Search engine optimisation (SEO)', 'Search engine marketing (SEM / PPC)',
                    'E-mail marketing', 'Brand management', 'Market research', 'Product marketing', 'Public relations', 'Event marketing',
                    'Copywriting', 'Graphic design', 'Video production', 'Marketing analytics', 'CRM / lead management', 'Sales / business development',
                    'Telemarketing', 'Customer service', 'Negotiation', 'Presentation skills', 'E-commerce / marketplaces', 'Influencer marketing',
                    'Account management', 'Tender / proposal writing']
PROGRAMMING_SKILLS = ['Python', 'JavaScript', 'TypeScript', 'Java', 'C#', 'C / C++', 'PHP', 'Go', 'Kotlin', 'Swift', 'Dart / Flutter', 'Ruby', 'Rust',
                      'SQL', 'PL/SQL', 'HTML / CSS', 'Angular', 'React', 'Vue', 'Node.js', 'Django', '.NET', 'Spring', 'Laravel', 'Android', 'iOS',
                      'Odoo', 'SAP ABAP', 'Power BI', 'Excel / VBA', 'Linux / shell', 'Docker / Kubernetes', 'AWS', 'Azure', 'Git']
SKILL_KINDS = {
    'language': ('Core.LanguageSkill', 'language', LANGUAGE_SKILLS),
    'marketing': ('Core.MarketingSkill', 'marketing', MARKETING_SKILLS),
    'programming': ('Core.ProgrammingLanguageSkill', 'programming_language', PROGRAMMING_SKILLS),
}


def preview_skills(kind):
    """[{name, exists}] of a skill kind."""
    model_label, name_f, rows = SKILL_KINDS[kind]
    model = apps.get_model(model_label)
    have = {(v or '').strip().lower() for v in model.objects.values_list(name_f, flat=True)}
    return [{'code': '', 'name': n, 'description': '', 'exists': n.lower() in have} for n in rows]


@transaction.atomic
def load_skills(kind):
    """Add the missing standard entries of a skill kind; nothing is changed or removed. Returns {'created': [...], 'linked': 0}."""
    model_label, name_f, rows = SKILL_KINDS[kind]
    model = apps.get_model(model_label)
    have = {(v or '').strip().lower() for v in model.objects.values_list(name_f, flat=True)}
    created = []
    for n in rows:
        if n.lower() not in have:
            model.objects.create(**{name_f: n})
            created.append(n)
    return {'created': created, 'linked': 0}


def load_all(branches, user=None, schema=None, create=True):
    result = {k: load(k, branches, user, schema, create) for k in KINDS}
    if not create:
        return result
    try:
        result['religion'] = {'created': load_religions(), 'linked': 0}
    except Exception:  # religion list is shared; never block the rest
        result['religion'] = {'created': [], 'linked': 0}
    return result
