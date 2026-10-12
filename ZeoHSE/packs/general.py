"""General pack – installed for every company. Starting values only: review them for the company's real hazards,
procedures and the legal requirements that apply (e.g. federal / emirate regulations, free-zone rules, client rules)."""
from ._util import Q, steps

RISK_MATRIX = {
    'likelihood': ['Rare', 'Unlikely', 'Possible', 'Likely', 'Almost certain'],
    'severity': ['Negligible', 'Minor', 'Moderate', 'Major', 'Catastrophic'],
    # score = likelihood × severity (1–25)
    'bands': [
        {'code': 'low', 'label': 'Low', 'min': 1, 'max': 4, 'color': '#16a34a', 'block': False},
        {'code': 'medium', 'label': 'Medium', 'min': 5, 'max': 9, 'color': '#d97706', 'block': False},
        {'code': 'high', 'label': 'High', 'min': 10, 'max': 16, 'color': '#dc2626', 'block': False},
        {'code': 'extreme', 'label': 'Extreme', 'min': 17, 'max': 25, 'color': '#7f1d1d', 'block': True},
    ],
}

PACK = {
    'terms': {
        'incident': 'Incident', 'near_miss': 'Near miss', 'permit': 'Permit to work', 'risk_assessment': 'Risk assessment',
        'inspection': 'Inspection', 'observation': 'Observation', 'toolbox_talk': 'Toolbox talk', 'action': 'Corrective action',
        'site': 'Site', 'area': 'Area', 'contractor': 'Contractor', 'supervisor': 'Supervisor',
    },
    'incident_categories': [
        ('INJ', 'Injury', 'injury', False), ('NM', 'Near miss', 'near_miss', False), ('UC', 'Unsafe condition', 'other', False),
        ('PD', 'Property / equipment damage', 'property', False), ('FIRE', 'Fire', 'property', False),
        ('ENV', 'Environmental release / spill', 'environment', False), ('OCC', 'Occupational illness', 'occupational', True),
        ('VEH', 'Vehicle incident', 'property', False), ('SEC', 'Security incident', 'security', False),
    ],
    'approvals': [
        # risk assessment approval by the highest residual band
        ('risk', 'low', steps(('supervisor', 'Supervisor')), 'Low residual risk'),
        ('risk', 'medium', steps(('site_manager', 'Site / operations manager')), 'Medium residual risk'),
        ('risk', 'high', steps(('site_manager', 'Site / operations manager'), ('hse_manager', 'HSE manager')), 'High residual risk'),
        ('risk', 'extreme', [], 'Not allowed – reduce the risk before the work is approved'),
        # incident closure by actual / potential severity (highest of both)
        ('incident', '1', steps(('hse_officer', 'HSE officer')), 'Minor events'),
        ('incident', '2', steps(('hse_officer', 'HSE officer')), ''),
        ('incident', '3', steps(('hse_officer', 'HSE officer'), ('site_manager', 'Site / operations manager')), ''),
        ('incident', '4', steps(('hse_manager', 'HSE manager'), ('site_manager', 'Site / operations manager')), 'High potential'),
        ('incident', '5', steps(('hse_manager', 'HSE manager'), ('site_manager', 'Site / operations manager')), 'Senior management review'),
        ('action', '*', steps(('hse_officer', 'HSE officer')), 'Effectiveness verification of corrective actions'),
    ],
    'permit_types': [
        {'code': 'GEN', 'name': 'General work permit', 'color': '#475569', 'max_hours': 12, 'requires_risk_assessment': True,
         'description': 'Non-routine work that is not covered by a specialist permit.',
         'checklist': 'GEN-PTW', 'approval_steps': steps(('supervisor', 'Supervisor'), ('permit_issuer', 'Permit issuer'))},
        {'code': 'HOT', 'name': 'Hot work', 'color': '#dc2626', 'max_hours': 12, 'requires_gas_test': True,
         'gas_limits': {'O2': [19.5, 23.5], 'LEL': [0, 0]}, 'gas_retest_hours': 4,
         'description': 'Welding, cutting, grinding, open flame or any spark-producing work.',
         'checklist': 'HOT-PTW', 'approval_steps': steps(('area_authority', 'Area authority'), ('permit_issuer', 'Permit issuer'))},
        {'code': 'WAH', 'name': 'Work at height', 'color': '#2563eb', 'max_hours': 12,
         'description': 'Work where a person could fall a distance liable to cause injury.',
         'checklist': 'WAH-PTW', 'approval_steps': steps(('supervisor', 'Supervisor'), ('permit_issuer', 'Permit issuer'))},
        {'code': 'ELEC', 'name': 'Electrical work / isolation', 'color': '#ca8a04', 'max_hours': 12, 'requires_isolation': True,
         'description': 'Work on or near electrical systems; the circuit is isolated, locked and proven dead first.',
         'checklist': 'ELEC-PTW', 'approval_steps': steps(('area_authority', 'Area authority'), ('permit_issuer', 'Permit issuer'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'GEN-PTW', 'name': 'General permit checklist', 'questions': [
            Q('Preparation', 'Scope, location and limits of the work are clear to the team', critical=True),
            Q('Preparation', 'Risk assessment reviewed with the work team', critical=True),
            Q('Preparation', 'Required PPE available and in good condition', critical=True),
            Q('Preparation', 'Area barricaded and signs posted'),
            Q('Emergency', 'Emergency contacts, assembly point and first aid known'),
        ]},
        {'kind': 'permit_checklist', 'code': 'HOT-PTW', 'name': 'Hot work checklist', 'questions': [
            Q('Area', 'Combustible materials removed or protected within the specified distance', critical=True),
            Q('Area', 'Gas test carried out and recorded (flammables 0 % LEL)', critical=True),
            Q('Area', 'Drains, openings and cable trays covered', critical=True),
            Q('Fire', 'Fire extinguisher / fire blanket at the work point', critical=True),
            Q('Fire', 'Fire watch assigned for the work and the time after it', critical=True),
            Q('Equipment', 'Welding / cutting equipment inspected, flashback arrestors fitted'),
            Q('Fire', 'Fire watch time after completion (minutes)', type='number'),
        ]},
        {'kind': 'permit_checklist', 'code': 'WAH-PTW', 'name': 'Work at height checklist', 'questions': [
            Q('Access', 'Scaffold / platform inspected and tagged safe', critical=True),
            Q('Fall protection', 'Guardrails or fall-arrest system with anchor point in place', critical=True),
            Q('Fall protection', 'Workers trained and harnesses inspected', critical=True),
            Q('Area', 'Area below barricaded – dropped objects controlled', critical=True),
            Q('Rescue', 'Rescue plan for a suspended worker in place', critical=True),
            Q('Weather', 'Wind / weather checked'),
        ]},
        {'kind': 'permit_checklist', 'code': 'ELEC-PTW', 'name': 'Electrical isolation checklist', 'questions': [
            Q('Isolation', 'Circuit identified on the drawings / single-line diagram', critical=True),
            Q('Isolation', 'Isolated, locked and tagged by an authorised person', critical=True),
            Q('Isolation', 'Proven dead with a tested voltage tester', critical=True),
            Q('Isolation', 'Stored energy (capacitors, UPS) discharged', critical=True),
            Q('PPE', 'Arc-rated PPE and insulated tools available'),
        ]},
        {'kind': 'inspection', 'code': 'INS-WORKPLACE', 'name': 'General workplace inspection', 'frequency': 'Weekly', 'questions': [
            Q('Housekeeping', 'Walkways and exits clear'), Q('Housekeeping', 'Materials stored safely'),
            Q('Fire', 'Fire extinguishers in place, inspected and accessible', critical=True), Q('Fire', 'Emergency exits unlocked and signed', critical=True),
            Q('Electrical', 'No damaged cables, plugs or overloaded sockets'), Q('First aid', 'First-aid kit complete'),
            Q('PPE', 'PPE worn as required'), Q('Welfare', 'Drinking water and rest area available'),
        ]},
        {'kind': 'drill', 'code': 'DRILL-EVAC', 'name': 'Emergency evacuation drill', 'frequency': 'Every 6 months', 'questions': [
            Q('Drill', 'Alarm heard in all areas', critical=True), Q('Drill', 'Time to full evacuation (minutes)', type='number'),
            Q('Drill', 'Head count completed at the assembly point', critical=True), Q('Drill', 'Fire wardens performed their duties'),
            Q('Drill', 'Lessons and improvement points', type='text'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-HOUSEKEEPING', 'name': 'Housekeeping and slips, trips and falls', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-HEAT', 'name': 'Heat stress – water, rest, shade (summer working)', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-REPORTING', 'name': 'Why we report near misses', 'questions': []},
    ],
    'hazards': [
        ('General', 'Outdoor work in summer', 'Heat stress', 'Heat exhaustion, heat stroke', 'Water, rest in shade, work-rest cycles, midday break rules, buddy system', 3, 4),
        ('General', 'Manual handling', 'Lifting heavy or awkward loads', 'Back injury', 'Mechanical aids, team lift, training, load limits', 3, 3),
        ('General', 'Walking around the workplace', 'Slips, trips and falls', 'Fractures, sprains', 'Housekeeping, lighting, signage, non-slip footwear', 3, 2),
        ('General', 'Use of portable electrical tools', 'Electric shock', 'Electrocution, burns', 'Inspected tools, RCD protection, no damaged cables', 2, 5),
        ('General', 'Driving on site', 'Vehicle collision with people', 'Serious injury', 'Traffic plan, speed limits, banksman, segregated walkways', 2, 5),
    ],
}
