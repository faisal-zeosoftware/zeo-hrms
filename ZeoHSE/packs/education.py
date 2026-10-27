"""Education pack (v1.38.0) – schools and campuses: laboratories, playgrounds and sports, school transport, safeguarding-related
safety (not child protection itself), events and trips. Starting values: review before use."""
from ._util import Q, steps

PACK = {
    'terms': {'site': 'Campus', 'area': 'Block / building', 'supervisor': 'Head of section', 'contractor': 'Contractor / vendor'},
    'incident_categories': [
        ('STUD', 'Student injury', 'injury', False), ('PLAY', 'Playground / sports injury', 'injury', False), ('LABI', 'Laboratory incident', 'occupational', True),
        ('BUS', 'School transport incident', 'injury', False), ('TRIP', 'Trip / off-site incident', 'injury', False), ('ALLERG', 'Allergic reaction / medical emergency', 'occupational', False),
    ],
    'approvals': [],
    'permit_types': [
        {'code': 'LABWORK', 'name': 'Hazardous laboratory activity', 'color': '#7c3aed', 'max_hours': 8,
         'description': 'Experiments with open flames, pressurised gas or hazardous chemicals in a teaching laboratory.',
         'checklist': 'LAB-PTW', 'approval_steps': steps(('area_authority', 'Head of science'), ('permit_issuer', 'HSE coordinator'))},
        {'code': 'TRIP', 'name': 'Educational trip / off-site activity', 'color': '#0891b2', 'max_hours': 48,
         'description': 'Risk assessment, ratios, consent, transport and medical arrangements for a trip.',
         'checklist': 'TRIP-PTW', 'approval_steps': steps(('area_authority', 'Head of section'), ('permit_issuer', 'Principal'))},
        {'code': 'CAMPUSWORK', 'name': 'Contractor works during school hours', 'color': '#ea580c', 'max_hours': 10, 'requires_isolation': True,
         'description': 'Maintenance or construction while students are on campus: segregation, supervision, no hot work near classes.',
         'checklist': 'CAMPUS-PTW', 'approval_steps': steps(('area_authority', 'Facilities manager'), ('permit_issuer', 'HSE coordinator'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'LAB-PTW', 'name': 'Laboratory activity checklist', 'questions': [
            Q('Risk', 'Experiment risk assessment on file', critical=True), Q('PPE', 'Goggles, lab coats and gloves for every student', critical=True),
            Q('Equipment', 'Fume cupboard working and used where required', critical=True), Q('Emergency', 'Eye wash, shower and extinguisher checked'),
            Q('Supervision', 'Teacher ratio adequate for the class size'),
        ]},
        {'kind': 'permit_checklist', 'code': 'TRIP-PTW', 'name': 'Trip checklist', 'questions': [
            Q('Planning', 'Venue risk assessment done', critical=True), Q('Consent', 'Parental consent for every student', critical=True),
            Q('Ratios', 'Staff-to-student ratio meets the policy', critical=True), Q('Medical', 'Medical conditions and medication list carried'),
            Q('Transport', 'Transport provider licensed, seat belts in every seat', critical=True), Q('Contacts', 'Emergency contacts and first aider named'),
        ]},
        {'kind': 'permit_checklist', 'code': 'CAMPUS-PTW', 'name': 'Contractor works on campus checklist', 'questions': [
            Q('Segregation', 'Work area fenced and signed, separate route for materials', critical=True), Q('Timing', 'Noisy / dusty work outside lesson times'),
            Q('People', 'Contractor staff inducted and badged', critical=True), Q('Hot work', 'No hot work while students are nearby', critical=True),
        ]},
        {'kind': 'inspection', 'code': 'INS-PLAY', 'name': 'Playground and sports equipment inspection', 'frequency': 'Weekly', 'questions': [
            Q('Surface', 'Impact-absorbing surface intact', critical=True), Q('Equipment', 'No loose, broken or sharp parts', critical=True),
            Q('Shade', 'Shade structures secure'), Q('Fencing', 'Fencing and gates in order'), Q('Pool', 'Pool fenced, lifeguard on duty, water tested'),
        ]},
        {'kind': 'inspection', 'code': 'INS-LAB', 'name': 'Laboratory and chemical store inspection', 'frequency': 'Monthly', 'questions': [
            Q('Storage', 'Chemicals stored by compatibility, locked', critical=True), Q('Labels', 'Every container labelled'), Q('Fume', 'Fume cupboard flow checked'),
            Q('Gas', 'Gas taps and shut-off working', critical=True), Q('Emergency', 'Eye wash and shower tested'),
        ]},
        {'kind': 'inspection', 'code': 'INS-BUS', 'name': 'School bus inspection', 'frequency': 'Daily', 'questions': [
            Q('Vehicle', 'Seat belts in every seat', critical=True), Q('Vehicle', 'Emergency exits and extinguisher'), Q('Driver', 'Driver licence and permit valid'),
            Q('Supervisor', 'Bus supervisor on board'), Q('Check', 'Bus checked empty at the end of the route', critical=True),
        ]},
        {'kind': 'drill', 'code': 'DRILL-EVAC', 'name': 'Campus evacuation drill', 'frequency': 'Each term', 'questions': [
            Q('Response', 'All classes at assembly point'), Q('Response', 'Registers taken', critical=True), Q('Response', 'Evacuation time (minutes)', type='number'),
        ]},
        {'kind': 'drill', 'code': 'DRILL-LOCKDOWN', 'name': 'Lockdown drill', 'frequency': 'Yearly', 'questions': [
            Q('Response', 'Doors locked, lights off, students away from windows'), Q('Response', 'Communication to parents tested'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-LAB', 'name': 'Laboratory safety rules', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-SUPERVISION', 'name': 'Supervision duty and playground safety', 'questions': []},
    ],
    'hazards': [
        ('Students', 'Playground supervision', 'Falls from equipment', 'Fractures', 'Impact surfaces, weekly inspections, supervision ratios', 3, 3),
        ('Laboratory', 'Chemistry practicals', 'Chemical burns / fumes', 'Injury', 'Risk assessment per experiment, PPE, fume cupboard', 2, 4),
        ('Transport', 'School buses', 'Road accident / child left on bus', 'Serious injury', 'Licensed drivers, supervisors, end-of-route check', 2, 5),
        ('Heat', 'Outdoor sports in summer', 'Heat stress', 'Heat illness', 'Shade, hydration, no outdoor activity above the limit', 3, 3),
        ('Medical', 'Allergies and medical conditions', 'Anaphylaxis', 'Death', 'Medical register, EpiPens, trained staff', 2, 5),
        ('Events', 'Assemblies and events', 'Crowd / stage incidents', 'Injury', 'Capacity limits, exits clear, event risk assessment', 2, 3),
    ],
}
