"""Manufacturing pack – machine guarding, LOTO, chemicals, process hazards, equipment inspections, industrial hygiene,
environmental controls and quality nonconformities. Starting values: review before use."""
from ._util import Q, steps

PACK = {
    'terms': {'site': 'Plant', 'area': 'Line / department', 'supervisor': 'Shift supervisor', 'permit': 'Permit to work'},
    'incident_categories': [
        ('MACH', 'Machinery entanglement / crush', 'injury', False), ('CHEM', 'Chemical exposure / spill', 'environment', False),
        ('FLT', 'Forklift incident', 'property', False), ('QNC', 'Quality nonconformity', 'quality', False),
        ('PROC', 'Process deviation / loss of containment', 'property', False), ('NOISE', 'Noise-induced hearing loss', 'occupational', True),
    ],
    'approvals': [],
    'permit_types': [
        {'code': 'LOTO', 'name': 'Lockout / tagout (hazardous energy)', 'color': '#b45309', 'max_hours': 12, 'requires_isolation': True,
         'description': 'Maintenance, cleaning or clearing jams on machines – all energy sources isolated, locked and verified (try-out).',
         'checklist': 'LOTO-PTW', 'approval_steps': steps(('supervisor', 'Shift supervisor'), ('area_authority', 'Area authority / maintenance lead'))},
        {'code': 'CHEMH', 'name': 'Chemical handling / line breaking', 'color': '#9333ea', 'max_hours': 8, 'requires_isolation': True,
         'description': 'Opening lines or vessels that held hazardous chemicals.',
         'checklist': 'CHEM-PTW', 'approval_steps': steps(('area_authority', 'Area authority'), ('hse_officer', 'HSE officer'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'LOTO-PTW', 'name': 'Lockout / tagout checklist', 'questions': [
            Q('Energy', 'All energy sources identified (electrical, pneumatic, hydraulic, gravity, stored)', critical=True),
            Q('Isolation', 'Each source isolated with a personal lock and tag', critical=True),
            Q('Verify', 'Zero energy verified – try-out start attempted', critical=True),
            Q('Verify', 'Stored energy released or restrained', critical=True),
            Q('People', 'Affected operators informed'),
        ]},
        {'kind': 'permit_checklist', 'code': 'CHEM-PTW', 'name': 'Chemical line-breaking checklist', 'questions': [
            Q('SDS', 'Safety data sheet reviewed', critical=True), Q('Isolation', 'Line drained, depressurised and isolated', critical=True),
            Q('PPE', 'Chemical PPE (gloves, goggles, apron, respirator) as per SDS', critical=True),
            Q('Emergency', 'Eyewash / safety shower tested and nearby', critical=True), Q('Spill', 'Spill kit at the work point'),
        ]},
        {'kind': 'inspection', 'code': 'INS-GUARDING', 'name': 'Machine guarding inspection', 'frequency': 'Monthly', 'questions': [
            Q('Guards', 'Fixed guards in place and secured', critical=True), Q('Guards', 'Interlocks stop the machine when opened', critical=True),
            Q('Controls', 'Emergency stops tested and working', critical=True), Q('Signs', 'Warning signs and pinch-point labels visible'),
            Q('Training', 'Operators trained on this machine'),
        ]},
        {'kind': 'inspection', 'code': 'INS-FORKLIFT', 'name': 'Forklift pre-use check', 'frequency': 'Each shift', 'questions': [
            Q('Checks', 'Brakes, steering and horn working', critical=True), Q('Checks', 'Forks, chains and mast undamaged', critical=True),
            Q('Checks', 'Lights, alarm and seat belt working'), Q('Checks', 'No hydraulic or fuel leaks'), Q('Driver', 'Driver licence valid', critical=True),
        ]},
        {'kind': 'inspection', 'code': 'INS-PRESSURE', 'name': 'Pressure vessel / compressor inspection', 'frequency': 'Monthly', 'questions': [
            Q('Certificate', 'Statutory inspection certificate valid', critical=True), Q('Safety', 'Relief valve tagged and within test date', critical=True),
            Q('Condition', 'No corrosion, leaks or damage'), Q('Gauge', 'Pressure gauge readable and calibrated'),
        ]},
        {'kind': 'inspection', 'code': 'INS-CHEM-STORE', 'name': 'Chemical store inspection', 'frequency': 'Monthly', 'questions': [
            Q('Storage', 'Incompatible chemicals segregated', critical=True), Q('Storage', 'Containers labelled and closed'),
            Q('SDS', 'Current SDS available for every chemical'), Q('Containment', 'Secondary containment / bunds intact', critical=True),
            Q('Emergency', 'Spill kit and eyewash available'),
        ]},
        {'kind': 'inspection', 'code': 'INS-HYGIENE', 'name': 'Industrial hygiene monitoring', 'frequency': 'As per monitoring plan', 'questions': [
            Q('Noise', 'Noise level at workstation dB(A)', type='number'), Q('Noise', 'Hearing protection zone signed'),
            Q('Dust', 'Dust / fume level measured', type='number', hint='mg/m³'), Q('Controls', 'Local exhaust ventilation working'),
            Q('Lighting', 'Lighting level (lux)', type='number'),
        ]},
        {'kind': 'inspection', 'code': 'INS-ENV', 'name': 'Environmental controls check', 'frequency': 'Weekly', 'questions': [
            Q('Waste', 'Hazardous waste stored, labelled and manifested'), Q('Waste', 'General waste segregated / recycled'),
            Q('Emissions', 'Stack / dust collector working'), Q('Effluent', 'Effluent treatment within limits', critical=True),
            Q('Spills', 'No spills or leaks to ground or drains', critical=True),
        ]},
        {'kind': 'other', 'code': 'QNC-FORM', 'name': 'Quality nonconformity report', 'questions': [
            Q('Product', 'Product / batch', type='text'), Q('Defect', 'Defect description', type='text'),
            Q('Defect', 'Quantity affected', type='number'), Q('Containment', 'Containment (quarantine / rework / scrap)', type='text'),
            Q('Cause', 'Root cause', type='text'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-LOTO', 'name': 'Lockout / tagout – never reach into a running machine', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-CHEM', 'name': 'Reading labels and safety data sheets', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-FORKLIFT', 'name': 'Forklift and pedestrian safety', 'questions': []},
    ],
    'hazards': [
        ('Machinery', 'Operating presses / rollers', 'Entanglement, crushing', 'Amputation', 'Fixed and interlocked guards, E-stops, training', 3, 5),
        ('Machinery', 'Clearing jams', 'Unexpected start-up', 'Amputation, fatality', 'LOTO with try-out, no work on running machines', 3, 5),
        ('Chemicals', 'Handling acids / caustics', 'Splash, burns', 'Eye and skin injury', 'SDS, PPE, eyewash, closed transfer', 3, 4),
        ('Process', 'Pressurised systems', 'Loss of containment', 'Fire, explosion, injury', 'Relief valves, inspections, MOC for changes', 2, 5),
        ('Industrial hygiene', 'Work in noisy areas', 'Noise', 'Hearing loss', 'Engineering control, hearing protection zones, audiometry', 4, 3),
        ('Transport', 'Forklift operations', 'Person struck', 'Serious injury', 'Segregated walkways, trained drivers, speed limits', 3, 4),
        ('Environment', 'Waste and effluent', 'Release to environment', 'Pollution, legal breach', 'Bunds, storage rules, monitoring', 2, 4),
    ],
}
