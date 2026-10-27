"""Office pack (v1.38.0) – IT, finance, services and property-management head offices: display screen work, ergonomics,
electrical, fire and evacuation, contractor works, driving for work, lone working. Starting values: review before use."""
from ._util import Q, steps

PACK = {
    'terms': {'site': 'Office', 'area': 'Floor / department', 'supervisor': 'Line manager', 'contractor': 'Contractor / vendor'},
    'incident_categories': [
        ('ERGO', 'Ergonomic / display screen injury', 'occupational', False), ('SLIP', 'Slip, trip or fall', 'injury', False), ('ELEC', 'Electrical incident', 'injury', False),
        ('DRIVE', 'Driving for work incident', 'injury', False), ('SEC', 'Security incident', 'injury', False), ('ILL', 'Ill health at work', 'occupational', True),
    ],
    'approvals': [],
    'permit_types': [
        {'code': 'OFFICEWORK', 'name': 'Contractor works in an occupied office', 'color': '#ea580c', 'max_hours': 10,
         'description': 'Fit-out, cabling or maintenance while staff are present: segregation, out-of-hours noisy work, fire precautions.',
         'checklist': 'OFFICE-PTW', 'approval_steps': steps(('area_authority', 'Facilities manager'), ('permit_issuer', 'HSE coordinator'))},
        {'code': 'SERVERROOM', 'name': 'Server room / electrical work', 'color': '#2563eb', 'max_hours': 8, 'requires_isolation': True,
         'description': 'Work on UPS, distribution boards or in the server room – isolation, authorised persons only.',
         'checklist': 'SERVER-PTW', 'approval_steps': steps(('area_authority', 'IT manager'), ('permit_issuer', 'Facilities manager'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'OFFICE-PTW', 'name': 'Office works checklist', 'questions': [
            Q('Area', 'Work area segregated and signed', critical=True), Q('Fire', 'Fire exits, alarms and extinguishers unaffected', critical=True),
            Q('Timing', 'Noisy / dusty work out of hours'), Q('People', 'Contractor inducted, badge issued'),
        ]},
        {'kind': 'permit_checklist', 'code': 'SERVER-PTW', 'name': 'Server room / electrical checklist', 'questions': [
            Q('Isolation', 'Circuit isolated, locked and tagged', critical=True), Q('Competence', 'Authorised electrician / engineer', critical=True),
            Q('Fire', 'Fire suppression inhibited and reinstated (recorded)'), Q('Backup', 'IT informed, backups and UPS checked'),
        ]},
        {'kind': 'inspection', 'code': 'INS-OFFICE', 'name': 'Office safety inspection', 'frequency': 'Quarterly', 'questions': [
            Q('Fire', 'Exits clear, signs lit, extinguishers in date', critical=True), Q('Electrical', 'No damaged cables, no daisy-chained extensions'),
            Q('Housekeeping', 'Walkways clear, cables managed'), Q('Storage', 'Heavy items stored low, cabinets stable'), Q('First aid', 'First aid kit stocked, first aider named'),
            Q('Ergonomics', 'Workstation assessments done for new staff'),
        ]},
        {'kind': 'inspection', 'code': 'INS-DSE', 'name': 'Workstation (display screen) assessment', 'frequency': 'On joining and yearly', 'questions': [
            Q('Chair', 'Chair adjustable, lumbar support'), Q('Screen', 'Screen at eye level, arm length away'), Q('Keyboard', 'Keyboard and mouse at elbow height'),
            Q('Breaks', 'Regular screen breaks taken'), Q('Lighting', 'No glare on the screen'),
        ]},
        {'kind': 'drill', 'code': 'DRILL-EVAC', 'name': 'Office evacuation drill', 'frequency': 'Twice a year', 'questions': [
            Q('Response', 'Fire wardens swept the floors'), Q('Response', 'Roll call completed', critical=True), Q('Response', 'Evacuation time (minutes)', type='number'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-DSE', 'name': 'Working comfortably at a screen', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-DRIVING', 'name': 'Driving for work', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-WELLBEING', 'name': 'Stress and wellbeing at work', 'questions': []},
    ],
    'hazards': [
        ('Ergonomics', 'Display screen work', 'Poor posture / long hours', 'Musculoskeletal disorders, eye strain', 'DSE assessments, adjustable furniture, breaks', 4, 2),
        ('Fire', 'Office building', 'Fire', 'Injury, loss', 'Alarms, drills, wardens, housekeeping', 2, 4),
        ('Electrical', 'Overloaded sockets and extension leads', 'Electric shock / fire', 'Injury', 'PAT testing, no daisy chains, inspections', 2, 3),
        ('Driving', 'Client visits and site trips', 'Road accident', 'Serious injury', 'Journey planning, no phones, vehicle checks', 2, 5),
        ('Lone working', 'Late working / visits', 'Medical emergency / aggression unnoticed', 'Injury', 'Check-in procedure, building security', 2, 3),
        ('Wellbeing', 'Deadlines and workload', 'Stress', 'Ill health', 'Workload reviews, support, flexible hours', 3, 2),
    ],
}
