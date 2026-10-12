"""Industry packs: starting configuration for the company's industry.

install(industry) installs the general pack and the industry pack. It only ADDS what is missing (by code / key), so it
is safe to run again and never overwrites what the company changed. Everything it adds is marked "to review" (when
HSESettings.require_review is on) – permit types and forms are offered only after an HSE manager reviewed them.
"""
from django.db import transaction
from django.utils import timezone

from . import construction, general, healthcare, manufacturing, oil_gas
from .extras import EXTRAS, SAMPLE

PACKS = {'general': general.PACK, 'construction': construction.PACK, 'manufacturing': manufacturing.PACK,
         'healthcare': healthcare.PACK, 'oil_gas': oil_gas.PACK}
LABELS = {'general': 'General', 'construction': 'Construction and engineering', 'manufacturing': 'Manufacturing',
          'healthcare': 'Hospitals and healthcare', 'oil_gas': 'Oil and gas'}
DESCRIPTIONS = {
    'construction': 'Work permits, method statements, task risk assessments, subcontractors, lifting operations, excavation, '
                    'working at height, site inspections and toolbox talks.',
    'manufacturing': 'Machine guarding, LOTO, chemicals, process hazards, equipment inspections, industrial hygiene, '
                     'environmental controls and quality nonconformities.',
    'healthcare': 'Occupational exposures, sharps injuries, biological hazards, chemical handling, facility permits, '
                  'infection-control interfaces, waste and emergency preparedness.',
    'oil_gas': 'High-risk permits, isolations, simultaneous operations, confined space entry, gas testing, process safety '
               'interfaces, contractor controls and emergency response.',
    'general': 'Risk matrix, approval matrix, general permits, workplace inspection, evacuation drill and common hazards.',
}


def _form_def(code):
    for p in list(PACKS.values()) + list(EXTRAS.values()):
        for f in p.get('forms', []):
            if f['code'] == code:
                return f
    return None


def summary(code):
    p = PACKS[code]
    return {'code': code, 'label': LABELS[code], 'description': DESCRIPTIONS.get(code, ''),
            'permit_types': [x['name'] for x in p.get('permit_types', [])],
            'forms': len(p.get('forms', [])), 'hazards': len(p.get('hazards', [])),
            'incident_categories': len(p.get('incident_categories', [])), 'terms': len(p.get('terms', {})),
            'competencies': [x[1] for x in EXTRAS.get(code, {}).get('competencies', [])],
            'kpis': len(EXTRAS.get(code, {}).get('kpis', [])), 'audits': [x['name'] for x in EXTRAS.get(code, {}).get('forms', [])],
            'exposure_limits': len(EXTRAS.get(code, {}).get('limits', []))}


def install(industry, user=None, primary=True):
    """Install general + the industry pack. Returns counts of what was added. primary: it is the company's own industry."""
    from ..models import (KPI, ApprovalRule, CompetencyType, ExposureLimit, FormTemplate, HazardLibrary, HSESettings, IncidentCategory,
                          PermitType, Term)
    from decimal import Decimal
    s = HSESettings.get()
    review = 'to_review' if s.require_review else 'reviewed'
    codes = ['general'] + ([industry] if industry in PACKS and industry != 'general' else [])
    added = {'terms': 0, 'forms': 0, 'permit_types': 0, 'approvals': 0, 'hazards': 0, 'incident_categories': 0, 'competencies': 0, 'kpis': 0,
             'exposure_limits': 0}
    with transaction.atomic():
        if not s.risk_bands:
            m = general.RISK_MATRIX
            s.likelihood_labels, s.severity_labels, s.risk_bands = m['likelihood'], m['severity'], m['bands']
        for code in codes:
            p = PACKS[code]
            common = {'pack': code, 'review_status': review}
            for k, v in p.get('terms', {}).items():
                # an industry term replaces the general wording unless the company changed it
                t = Term.objects.filter(key=k).first()
                if t is None:
                    Term.objects.create(key=k, label=v, **common)
                    added['terms'] += 1
                elif t.pack == 'general' and code != 'general' and t.label != v:
                    t.label, t.pack = v, code
                    t.save()
            def ensure_form(f):
                obj = FormTemplate.objects.filter(code=f['code']).first()
                if obj is None:
                    obj = FormTemplate.objects.create(kind=f['kind'], code=f['code'], name=f['name'], description=f.get('description', ''),
                                                      questions=f.get('questions', []), frequency=f.get('frequency', ''), **common)
                    added['forms'] += 1
                return obj
            for f in p.get('forms', []):
                ensure_form(f)
            for x in p.get('permit_types', []):
                if PermitType.objects.filter(code=x['code']).exists():
                    continue
                cl = None
                if x.get('checklist'):
                    fd = _form_def(x['checklist'])
                    cl = ensure_form(fd) if fd else None
                PermitType.objects.create(
                    code=x['code'], name=x['name'], description=x.get('description', ''), color=x.get('color', '#5b4fe0'),
                    max_hours=x.get('max_hours', 12), requires_risk_assessment=x.get('requires_risk_assessment', True),
                    requires_method_statement=x.get('requires_method_statement', False), requires_gas_test=x.get('requires_gas_test', False),
                    requires_isolation=x.get('requires_isolation', False), requires_contractor_approval=x.get('requires_contractor_approval', True),
                    gas_limits=x.get('gas_limits', {}), gas_retest_hours=x.get('gas_retest_hours', 0), checklist=cl,
                    approval_steps=x.get('approval_steps', []), simops_conflicts=x.get('simops', []), **common)
                added['permit_types'] += 1
            for subject, key, stp, note in p.get('approvals', []):
                r = ApprovalRule.objects.filter(subject=subject, key=key).first()
                if r is None:
                    ApprovalRule.objects.create(subject=subject, key=key, steps=stp, note=note, **common)
                    added['approvals'] += 1
                elif r.pack == 'general' and code != 'general' and r.review_status == 'to_review':
                    r.steps, r.note, r.pack = stp, note, code      # industry matrix replaces the untouched general one
                    r.save()
            for cat, act, hz, cons, ctl, lk, sv in p.get('hazards', []):
                if not HazardLibrary.objects.filter(activity=act, hazard=hz).exists():
                    HazardLibrary.objects.create(category=cat, activity=act, hazard=hz, consequence=cons, controls=ctl,
                                                 likelihood=lk, severity=sv, **common)
                    added['hazards'] += 1
            for c, name, kind, restricted in p.get('incident_categories', []):
                if not IncidentCategory.objects.filter(code=c).exists():
                    IncidentCategory.objects.create(code=c, name=name, kind=kind, restricted=restricted, **common)
                    added['incident_categories'] += 1
            ex = EXTRAS.get(code, {})
            for c, name, months in ex.get('competencies', []):
                if not CompetencyType.objects.filter(code=c).exists():
                    CompetencyType.objects.create(code=c, name=name, validity_months=months, description=f'From the {LABELS[code]} pack')
                    added['competencies'] += 1
            for f in ex.get('forms', []):
                ensure_form(f)
            for pt_code, req in ex.get('permit_competencies', {}).items():
                pt = PermitType.objects.filter(code=pt_code).first()
                if pt is not None and not pt.required_competencies:
                    pt.required_competencies = req
                    pt.save(update_fields=['required_competencies'])
            for kc, name, formula, unit, target, higher in ex.get('kpis', []):
                if not KPI.objects.filter(code=kc).exists():
                    KPI.objects.create(code=kc, name=name, formula=formula, unit=unit, target=Decimal(str(target)), higher_is_better=higher)
                    added['kpis'] += 1
            for agent, cat, unit, lim, act, avg in ex.get('limits', []):
                if not ExposureLimit.objects.filter(agent=agent, averaging=avg).exists():
                    ExposureLimit.objects.create(agent=agent, category=cat, unit=unit, limit_value=Decimal(str(lim)),
                                                 action_level=Decimal(str(act)) if act is not None else None, averaging=avg, jurisdiction=SAMPLE,
                                                 is_active=False)
                    added['exposure_limits'] += 1
        s.packs_installed = sorted(set((s.packs_installed or []) + codes))
        if industry in PACKS and (primary or not s.packs_installed or s.industry == 'general'):
            s.industry = industry
        s.pack_installed_at = timezone.now()
        s.save()
    return added
