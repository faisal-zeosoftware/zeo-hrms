"""Oil and gas pack – high-risk permits, isolations, simultaneous operations, confined space entry, gas testing,
process safety interfaces, contractor controls and emergency response. Starting values: review before use."""
from ._util import Q, steps

GAS = {'O2': [19.5, 23.5], 'LEL': [0, 10], 'H2S': [0, 5], 'CO': [0, 25]}

PACK = {
    'terms': {'site': 'Facility / field', 'area': 'Unit / module', 'permit': 'Permit to work (PTW)', 'supervisor': 'Performing authority',
              'contractor': 'Contractor'},
    'incident_categories': [
        ('LOC', 'Loss of primary containment', 'property', False), ('PSE', 'Process safety event', 'property', False),
        ('H2S', 'H₂S release / exposure', 'occupational', True), ('DROPPED', 'Dropped object', 'near_miss', False),
        ('WELL', 'Well control incident', 'property', False), ('SIMOPS', 'SIMOPS conflict', 'near_miss', False),
    ],
    'approvals': [
        ('risk', 'high', steps(('area_authority', 'Area authority'), ('site_manager', 'Installation manager'), ('hse_manager', 'HSE manager')), 'High residual risk'),
    ],
    'permit_types': [
        {'code': 'HOT-OG', 'name': 'Hot work permit (red)', 'color': '#dc2626', 'max_hours': 12, 'requires_gas_test': True, 'requires_isolation': True,
         'requires_method_statement': True, 'gas_limits': {'O2': [19.5, 23.5], 'LEL': [0, 0], 'H2S': [0, 5], 'CO': [0, 25]}, 'gas_retest_hours': 2,
         'description': 'Hot work in hazardous areas – continuous gas monitoring, isolations, fire watch.',
         'checklist': 'HOT-PTW', 'simops': ['CSE-OG', 'COLD-OG'],
         'approval_steps': steps(('gas_tester', 'Authorised gas tester'), ('area_authority', 'Area authority'), ('permit_issuer', 'Issuing authority'), ('site_manager', 'Installation manager'))},
        {'code': 'COLD-OG', 'name': 'Cold work permit (blue)', 'color': '#2563eb', 'max_hours': 12,
         'description': 'Non-spark work on process plant: line breaking, valve work, mechanical maintenance.',
         'checklist': 'GEN-PTW', 'approval_steps': steps(('area_authority', 'Area authority'), ('permit_issuer', 'Issuing authority'))},
        {'code': 'CSE-OG', 'name': 'Confined space entry (vessel entry)', 'color': '#7c3aed', 'max_hours': 8, 'requires_gas_test': True,
         'requires_isolation': True, 'requires_method_statement': True, 'gas_limits': GAS, 'gas_retest_hours': 2,
         'description': 'Vessels, tanks, columns – positive isolation (blinds / spades), purge, gas test, standby man and rescue team.',
         'checklist': 'CSE-PTW', 'simops': ['HOT-OG'],
         'approval_steps': steps(('gas_tester', 'Authorised gas tester'), ('area_authority', 'Area authority'), ('permit_issuer', 'Issuing authority'), ('hse_officer', 'HSE officer'))},
        {'code': 'ISOL', 'name': 'Isolation certificate', 'color': '#b45309', 'max_hours': 72, 'requires_isolation': True, 'requires_risk_assessment': False,
         'description': 'Process, mechanical and electrical isolations under a certificate linked to the work permits.',
         'checklist': 'ISOL-CERT', 'approval_steps': steps(('area_authority', 'Area authority'), ('permit_issuer', 'Isolating authority'))},
        {'code': 'RADIO', 'name': 'Radiography', 'color': '#a21caf', 'max_hours': 8,
         'description': 'Industrial radiography – licensed operators, controlled area, dose monitoring.',
         'checklist': 'RADIO-PTW', 'approval_steps': steps(('area_authority', 'Area authority'), ('hse_manager', 'Radiation protection / HSE manager'), ('permit_issuer', 'Issuing authority'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'ISOL-CERT', 'name': 'Isolation certificate checklist', 'questions': [
            Q('Plan', 'Isolation scheme drawn on the P&ID and checked', critical=True),
            Q('Isolation', 'Positive isolation (blind / spade / double block and bleed) as required', critical=True),
            Q('Isolation', 'Every isolation point locked and tagged', critical=True), Q('Verify', 'Pressure released, drained and vented, verified zero', critical=True),
            Q('Electrical', 'Electrical isolation proven dead by an authorised person', critical=True),
        ]},
        {'kind': 'permit_checklist', 'code': 'RADIO-PTW', 'name': 'Radiography checklist', 'questions': [
            Q('Licence', 'Radiography licence and source certificate valid', critical=True), Q('Area', 'Controlled area barricaded and signed', critical=True),
            Q('Monitoring', 'Survey meter calibrated, personal dosimeters worn', critical=True), Q('Notification', 'Area announced / PA notice given'),
        ]},
        {'kind': 'inspection', 'code': 'INS-SIMOPS', 'name': 'SIMOPS review', 'frequency': 'Daily permit meeting', 'questions': [
            Q('SIMOPS', 'All live permits in the area reviewed together', critical=True),
            Q('SIMOPS', 'No hot work near open process / venting', critical=True), Q('SIMOPS', 'Crane lifts over live plant controlled'),
            Q('SIMOPS', 'Communication between work parties agreed'),
        ]},
        {'kind': 'inspection', 'code': 'INS-GASDET', 'name': 'Fixed gas detection and portable monitor check', 'frequency': 'Weekly', 'questions': [
            Q('Fixed', 'Fixed detectors without faults in the control system', critical=True), Q('Portable', 'Portable monitors bump-tested today', critical=True),
            Q('Portable', 'Calibration within date'),
        ]},
        {'kind': 'prequalification', 'code': 'PQ-OG', 'name': 'Contractor HSE pre-qualification (oil and gas)', 'questions': [
            Q('Company', 'HSE management system certified / documented', critical=True), Q('Training', 'H₂S, BOSIET / site-specific training valid', critical=True),
            Q('Performance', 'LTI frequency for the last 3 years', type='text'), Q('Plan', 'Contractor HSE plan for the scope'),
            Q('Bridging', 'HSE bridging document agreed'),
        ]},
        {'kind': 'drill', 'code': 'DRILL-H2S', 'name': 'H₂S release / muster drill', 'frequency': 'Quarterly', 'questions': [
            Q('Alarm', 'Alarm recognised and wind direction checked', critical=True), Q('Muster', 'Muster at upwind point, POB count complete', critical=True),
            Q('Response', 'Rescue team with breathing apparatus deployed'), Q('Time', 'Muster time (minutes)', type='number'),
        ]},
        {'kind': 'drill', 'code': 'DRILL-SPILL', 'name': 'Oil spill response drill', 'frequency': 'Yearly', 'questions': [
            Q('Response', 'Spill reported and tier assessed'), Q('Equipment', 'Booms / absorbents deployed'), Q('Notification', 'Authorities notification practised'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-H2S', 'name': 'H₂S awareness – recognise, react, escape', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-PTW', 'name': 'Permit to work and isolation discipline', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-SWA', 'name': 'Stop work authority', 'questions': []},
    ],
    'hazards': [
        ('Process safety', 'Breaking containment on hydrocarbon lines', 'Release of hydrocarbons', 'Fire, explosion, multiple fatalities', 'Isolation certificate, drain/purge, gas test, MOC', 2, 5),
        ('Toxic gas', 'Work in sour service areas', 'H₂S exposure', 'Fatality', 'Gas detection, BA, H₂S training, buddy system, wind socks', 2, 5),
        ('Confined space', 'Vessel entry', 'Oxygen deficiency / toxic atmosphere', 'Fatality', 'Positive isolation, purge, continuous monitoring, standby, rescue', 2, 5),
        ('Hot work', 'Welding in hazardous area', 'Ignition of flammable atmosphere', 'Explosion', 'Hot work permit, 0 % LEL, habitat / continuous monitoring', 2, 5),
        ('SIMOPS', 'Concurrent drilling / production / construction', 'Conflicting activities', 'Major accident', 'SIMOPS plan, permit coordination meeting, area authority', 2, 5),
        ('Lifting', 'Lifts over live plant', 'Dropped load on process equipment', 'Loss of containment', 'Lift plan, no-fly zones, exclusion zones', 2, 5),
    ],
}
