"""v1.67.13 (service) – industry packs for Projects.

A company ticks one or more service industries; one of them is the main industry (AppState 'projects'.industry).
Release 1 ships two packs with content: IT (exactly the setup Projects always had) and General (every feature,
neutral lists). The other SOP industries can already be ticked: they get the General content and are marked
"pack coming in a later release" (ProjectsIndustry.pack_installed = False).

Packs only ADD: a row whose natural key (code / name) exists is left as the company has it.
No feature depends on the industry – a pack is content only.
"""
from django.db import transaction

# code, label, company industry codes (UserManagement company.industry_type) that suggest it
INDUSTRIES = [
    ('general', 'Other / General Services'),
    ('it', 'IT & Software Services'),
    ('hvac', 'HVAC & Air Conditioning'),
    ('mep', 'MEP Contractors'),
    ('fire', 'Fire & Safety'),
    ('cctv', 'CCTV & Security Systems'),
    ('lift', 'Elevator & Escalator'),
    ('medical', 'Medical Equipment'),
    ('industrial', 'Industrial Machinery'),
    ('solar', 'Solar Energy'),
    ('fm', 'Facility Management'),
    ('cleaning', 'Cleaning Services'),
    ('pest', 'Pest Control'),
    ('telecom', 'Telecom & Networking'),
    ('kitchen', 'Commercial Kitchen Equipment'),
    ('water', 'Water Treatment'),
    ('pool', 'Swimming Pool & Landscaping'),
    ('fitout', 'Construction & Fit-out'),
]
LABELS = dict(INDUSTRIES)
WITH_CONTENT = tuple(c for c, _ in INDUSTRIES)   # v1.67.14 (service release 2): every industry has its own pack (was IT and General)

# company master industry → suggested Projects industry
FROM_COMPANY = {'IT': 'it', 'HVAC': 'hvac', 'MEP': 'mep', 'FIRE': 'fire', 'CCTV': 'cctv', 'LIFT': 'lift', 'MEDEQ': 'medical',
                'INDM': 'industrial', 'SOLAR': 'solar', 'FM': 'fm', 'PM': 'fm', 'CLEAN': 'cleaning', 'PEST': 'pest',
                'TEL': 'telecom', 'KITCH': 'kitchen', 'WATER': 'water', 'POOL': 'pool', 'FITOUT': 'fitout', 'CON': 'fitout'}

# ---------------------------------------------------------------- pack content
GENERAL = {
    'teams': ['Service', 'Installation', 'Support'],
    'categories': [('Breakdown', False), ('Preventive', False), ('Installation', False), ('Inspection', False), ('Request', False)],
    'equipment': [('GEN-ELEC', 'Electrical'), ('GEN-PLUMB', 'Plumbing'), ('GEN-HVAC', 'HVAC unit'), ('GEN-FIRE', 'Fire safety device'),
                  ('GEN-IT', 'IT device'), ('GEN-SEC', 'Security device'), ('GEN-MACH', 'Machine'), ('GEN-OTHER', 'Other')],
    'amc_types': [
        # code, name, parts, labour, visits, call-outs, legacy contract type
        ('GEN-COMP', 'Comprehensive (parts and labour)', 'included', True, 4, None, ''),
        ('GEN-NONCOMP', 'Non-comprehensive (labour only)', 'none', True, 4, None, ''),
        ('GEN-PARTSLIM', 'Parts up to a limit', 'limit', True, 4, None, ''),
        ('GEN-ONCALL', 'On-call (per visit)', 'none', True, 0, 0, ''),
    ],
}
IT = {
    'equipment': [('IT-SERVER', 'Server'), ('IT-SWITCH', 'Network switch'), ('IT-FW', 'Firewall'), ('IT-PC', 'Workstation'),
                  ('IT-PRINTER', 'Printer'), ('IT-LICENCE', 'Software licence')],
    # today's four contract types become IT AMC types, so existing contracts keep their type
    'amc_types': [
        ('IT-COMP', 'Comprehensive', 'included', True, 0, None, 'comprehensive'),
        ('IT-NONCOMP', 'Non-comprehensive', 'none', True, 0, None, 'non_comprehensive'),
        ('IT-REMOTE', 'Remote only', 'none', True, 0, None, 'remote'),
        ('IT-ONSITE', 'Onsite', 'none', True, 0, None, 'onsite'),
    ],
}


def _equipment(rows, industry):
    """rows: (code, name) or v1.67.14 (code, name, preventive months, extra fields)."""
    from .models import EquipmentCategory
    made = []
    for r in rows:
        code, name = r[0], r[1]
        d = {'name': name, 'industry': industry}
        if len(r) > 2:
            d.update(pm_months=r[2] or None, extra_fields=list(r[3] or []))
        _, c = EquipmentCategory.objects.get_or_create(code=code, defaults=d)
        if c:
            made.append(f'equipment category {name}')
    return made


def install_service(code):
    """v1.67.14 (service release 2): the industry's own equipment categories (preventive frequency, extra fields), AMC types and
    ticket categories, on top of the General content (zeo/service_industries.py – the same industries as CRM and Maintenance)."""
    from zeo.service_industries import INDUSTRIES as SI
    from .models import IssueCategory
    d = SI.get(code)
    if not d:
        return []
    made = _equipment(d['equipment'], code)
    _checklists(code, [it for _c, _n, items in d['checklists'][:1] for it in items])
    made += _amc_types([(c, n, parts, lab, v, co, '') for c, n, parts, lab, v, co in d['amc']], code)
    for name in d['issues']:
        _, c = IssueCategory.objects.get_or_create(name=name, defaults={'needs_development': False})
        if c:
            made.append(f'category {name}')
    return made


def _amc_types(rows, industry):
    from .models import AMCType
    made = []
    for code, name, parts, labour, visits, callouts, legacy in rows:
        _, c = AMCType.objects.get_or_create(code=code, defaults={
            'name': name, 'industry': industry, 'parts': parts, 'labour_included': labour, 'visits_per_year': visits,
            'callouts_per_year': callouts, 'legacy_contract_type': legacy})
        if c:
            made.append(f'AMC type {name}')
    return made


GENERAL_CHECKLIST = ['Visual inspection', 'Clean the unit', 'Check fixings and covers', 'Check electrical connections', 'Test operation',
                     'Check for leaks / noise', 'Customer informed of findings']


def _checklists(industry, items):
    """v1.67.14: the service checklist of the industry's equipment categories (only where none was set)."""
    from .models import EquipmentCategory
    if items:
        EquipmentCategory.objects.filter(industry=industry, checklist=[]).update(checklist=items)


def install_general():
    from .models import IssueCategory, SupportTeam
    made = []
    for name in GENERAL['teams']:
        _, c = SupportTeam.objects.get_or_create(name=name)
        if c:
            made.append(f'team {name}')
    for name, dev in GENERAL['categories']:
        _, c = IssueCategory.objects.get_or_create(name=name, defaults={'needs_development': dev})
        if c:
            made.append(f'category {name}')
    made += _equipment(GENERAL['equipment'], 'general')
    _checklists('general', GENERAL_CHECKLIST)
    made += _amc_types(GENERAL['amc_types'], 'general')
    from .plans import install_default_templates
    made += [f'plan template {n}' for n in install_default_templates()]
    _sla_and_settings()
    return made


def install_it():
    from .management.commands.zeoprojects_setup import Command
    made = [x for x in (Command().defaults() or '').split(', ') if x and x != 'already set up']
    made += _equipment(IT['equipment'], 'it')
    made += _amc_types(IT['amc_types'], 'it')
    return made


def _sla_and_settings():
    """The Standard SLA policy and the settings row (as the IT set-up makes them)."""
    from .models import SLAPolicy
    if not SLAPolicy.objects.exists():
        from .models import SLATarget, ProjectsSettings
        from .sla import WorkCalendar
        s = ProjectsSettings.get()
        day = WorkCalendar(s).minutes_per_day() or 540
        pol = SLAPolicy.objects.create(name='Standard', description='Sample targets – change them to your contracts', is_default=True)
        for pr, resp, res, bh in (('critical', 30, 240, False), ('high', 60, 480, False), ('medium', 240, 2 * day, True), ('low', day, 5 * day, True)):
            SLATarget.objects.create(policy=pol, priority=pr, response_minutes=resp, resolution_minutes=res, business_hours=bh)
        if s.default_sla_id is None:
            s.default_sla = pol
            s.save(update_fields=['default_sla'])


@transaction.atomic
def install(code, user=None, main=False):
    """Tick an industry for Projects and add its pack content. Returns what was added."""
    from .models import ProjectsIndustry
    if code not in LABELS:
        raise ValueError('Unknown industry.')
    row, _ = ProjectsIndustry.objects.get_or_create(code=code, defaults={'added_by': user if getattr(user, 'pk', None) else None})
    made = install_it() if code == 'it' else install_general() + install_service(code)
    row.pack_installed = code in WITH_CONTENT
    if main:
        ProjectsIndustry.objects.exclude(pk=row.pk).filter(is_main=True).update(is_main=False)
        row.is_main = True
    row.save()
    return made


def remove(code):
    """Untick an industry: its rows stay (records keep them); only the tick goes. The main industry cannot be removed."""
    from .models import ProjectsIndustry
    r = ProjectsIndustry.objects.filter(code=code).first()
    if r is None:
        return False
    if r.is_main:
        raise ValueError('The main industry cannot be removed.')
    r.delete()
    return True


def ticked():
    from .models import ProjectsIndustry
    return [{'code': r.code, 'label': LABELS.get(r.code, r.code), 'is_main': r.is_main, 'pack_installed': r.pack_installed,
             'pack_note': '' if r.pack_installed else 'Pack coming in a later release – General content is used'}
            for r in ProjectsIndustry.objects.all()]


def upgrade(apply=False):
    """Existing companies (Projects used before 1.67.13): main industry IT with the IT pack; contracts get the IT AMC type of
    their contract type. Returns the list of changes (dry run unless apply)."""
    from .models import AMCContract, AMCType, ProjectsIndustry
    out = []
    if not ProjectsIndustry.objects.exists():
        out.append('main industry: IT (IT pack = the current setup)')
        if apply:
            install('it', None, main=True)
            try:
                from ZeoApps.models import AppState
                s = AppState.objects.filter(app='projects').first()
                if s is not None and s.industry not in LABELS:
                    s.industry = 'it'
                    s.save(update_fields=['industry'])
            except Exception:   # noqa: BLE001
                pass
    # v1.67.14 (service release 2): industries ticked in 1.67.13 got General content only ("Pack coming in a later release") – add their own pack
    for r in ProjectsIndustry.objects.filter(pack_installed=False):
        out.append(f'industry {LABELS.get(r.code, r.code)}: add its pack')
        if apply:
            install(r.code, None, main=r.is_main)
    types = {t.legacy_contract_type: t for t in AMCType.objects.exclude(legacy_contract_type='')}
    for c in AMCContract.objects.filter(amc_type__isnull=True):
        t = types.get(c.contract_type)
        label = t.name if t else {'comprehensive': 'Comprehensive', 'non_comprehensive': 'Non-comprehensive',
                                  'remote': 'Remote only', 'onsite': 'Onsite'}.get(c.contract_type, c.contract_type)
        out.append(f'{c.number}: AMC type {label}')
        if apply and t:
            c.amc_type = t
            c.save(update_fields=['amc_type'])
    return out
