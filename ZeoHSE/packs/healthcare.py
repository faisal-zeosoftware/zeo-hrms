"""Hospitals and healthcare pack – occupational exposures, sharps injuries, biological hazards, chemical handling,
facility permits, infection-control interfaces, waste and emergency preparedness. Starting values: review before use.
Health details of staff are restricted (occupational health officer / HSE manager only)."""
from ._util import Q, steps

PACK = {
    'terms': {'site': 'Facility', 'area': 'Department / ward', 'incident': 'Incident / occurrence', 'supervisor': 'Charge nurse / supervisor',
              'contractor': 'Contractor / service provider'},
    'incident_categories': [
        ('SHARPS', 'Needlestick / sharps injury', 'occupational', True), ('BBF', 'Blood / body-fluid exposure', 'occupational', True),
        ('BIO', 'Biological / infectious agent exposure', 'occupational', True), ('VIOL', 'Workplace violence / aggression', 'injury', False),
        ('PATH', 'Patient handling injury', 'injury', False), ('CHEMH', 'Hazardous drug / chemical exposure', 'occupational', True),
        ('WASTE', 'Clinical waste incident', 'environment', False),
    ],
    'approvals': [],
    'permit_types': [
        {'code': 'ICRA', 'name': 'Construction / maintenance in clinical area (ICRA)', 'color': '#0891b2', 'max_hours': 24,
         'description': 'Works in patient areas need an infection control risk assessment: dust barriers, negative pressure, cleaning.',
         'checklist': 'ICRA-PTW', 'approval_steps': steps(('area_authority', 'Department head'), ('infection_control', 'Infection control'), ('permit_issuer', 'Facility manager'))},
        {'code': 'MEDGAS', 'name': 'Medical gas system shutdown', 'color': '#16a34a', 'max_hours': 8, 'requires_isolation': True,
         'description': 'Isolation of oxygen, vacuum, medical air or nitrous oxide lines – clinical departments informed, backup supply ready.',
         'checklist': 'MEDGAS-PTW', 'approval_steps': steps(('area_authority', 'Clinical department head'), ('permit_issuer', 'Facility manager'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'ICRA-PTW', 'name': 'Infection control (ICRA) checklist', 'questions': [
            Q('ICRA', 'ICRA class determined with infection control', critical=True),
            Q('Barriers', 'Dust barriers / hoarding sealed', critical=True), Q('Air', 'Negative pressure or HEPA filtration in place where required', critical=True),
            Q('Cleaning', 'Sticky mats and daily cleaning arranged'), Q('Patients', 'Affected patients relocated or protected'),
            Q('Debris', 'Debris removed in covered carts by agreed route'),
        ]},
        {'kind': 'permit_checklist', 'code': 'MEDGAS-PTW', 'name': 'Medical gas shutdown checklist', 'questions': [
            Q('Clinical', 'Clinical departments informed and agreed the time', critical=True),
            Q('Backup', 'Portable cylinders / backup supply ready at bedside', critical=True),
            Q('Isolation', 'Zone valve identified and isolated, tagged', critical=True), Q('Restore', 'Purity test after reinstatement planned', critical=True),
        ]},
        {'kind': 'inspection', 'code': 'INS-SHARPS', 'name': 'Sharps and clinical waste audit', 'frequency': 'Monthly', 'questions': [
            Q('Sharps', 'Sharps containers at point of use, not over the fill line', critical=True), Q('Sharps', 'Containers labelled and closed when full'),
            Q('Waste', 'Clinical waste segregated by colour code', critical=True), Q('Waste', 'Waste stores locked and clean'),
            Q('Practice', 'No recapping of needles observed', critical=True), Q('Devices', 'Safety-engineered sharps in use'),
        ]},
        {'kind': 'inspection', 'code': 'INS-IPC', 'name': 'Infection prevention walk-round', 'frequency': 'Monthly', 'questions': [
            Q('Hand hygiene', 'Hand-rub available at point of care'), Q('PPE', 'Correct PPE available and used', critical=True),
            Q('Isolation', 'Isolation signage and precautions followed', critical=True), Q('Cleaning', 'Environmental cleaning records complete'),
        ]},
        {'kind': 'inspection', 'code': 'INS-HAZDRUG', 'name': 'Hazardous drugs / chemicals handling', 'frequency': 'Quarterly', 'questions': [
            Q('Storage', 'Cytotoxic and hazardous drugs stored and labelled separately', critical=True),
            Q('Prep', 'Preparation in a biological safety cabinet'), Q('Spill', 'Cytotoxic spill kit available', critical=True),
            Q('Chemicals', 'Formaldehyde / glutaraldehyde exposure controls in place'),
        ]},
        {'kind': 'drill', 'code': 'DRILL-CODE-RED', 'name': 'Fire drill (code red) – clinical area', 'frequency': 'Each department yearly, each shift', 'questions': [
            Q('Response', 'RACE / PASS followed'), Q('Response', 'Horizontal evacuation of patients practised', critical=True),
            Q('Response', 'Oxygen shut-off responsibility known'), Q('Response', 'Response time (minutes)', type='number'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-SHARPS', 'name': 'Sharps safety and what to do after a needlestick', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-PATIENT-HANDLING', 'name': 'Safe patient handling', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-AGGRESSION', 'name': 'Managing aggression and violence', 'questions': []},
    ],
    'hazards': [
        ('Biological', 'Using needles and sharps', 'Needlestick injury', 'Blood-borne infection', 'Safety devices, no recapping, sharps bins at point of use, vaccination, post-exposure protocol', 3, 4),
        ('Biological', 'Care of infectious patients', 'Airborne / contact infection', 'Infection of staff', 'Isolation precautions, PPE, respirator fit testing, vaccination', 3, 4),
        ('Ergonomics', 'Moving and repositioning patients', 'Musculoskeletal strain', 'Back injury', 'Hoists, slide sheets, team handling, training', 4, 3),
        ('Chemicals', 'Disinfectants and sterilants', 'Inhalation / skin contact', 'Respiratory irritation, dermatitis', 'Ventilation, PPE, SDS, substitution', 3, 3),
        ('Chemicals', 'Preparing cytotoxic drugs', 'Exposure to hazardous drugs', 'Long-term health effects', 'Biological safety cabinet, closed systems, PPE, spill kits', 2, 4),
        ('Violence', 'Emergency department', 'Aggression from patients / visitors', 'Injury, stress', 'Security presence, de-escalation training, alarms', 3, 3),
        ('Facility', 'Works in clinical areas', 'Dust and pathogens released', 'Patient infection', 'ICRA, barriers, negative pressure, cleaning', 2, 5),
    ],
}
