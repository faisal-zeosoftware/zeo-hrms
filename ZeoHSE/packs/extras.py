"""Pack content for the v1.16.0 modules: competency types (and the competencies each permit type needs), KPIs,
audit checklists and sample exposure limits. Starting values only – the company reviews them for its own hazards,
procedures and the applicable legal requirements before use (exposure limits are inactive until confirmed)."""
from ._util import Q

SAMPLE = 'Sample value – confirm the applicable limit for your jurisdiction'

EXTRAS = {
    'general': {
        'competencies': [('IND', 'Site HSE induction', 12), ('FA', 'First aid', 36), ('FIRE', 'Fire warden / fire watch', 24),
                         ('WAH', 'Working at height', 24), ('HOT', 'Hot work and fire watch', 24), ('LOTO', 'Lockout / tagout – authorised person', 24),
                         ('PTW-I', 'Permit issuer authorisation', 24), ('GAS', 'Authorised gas tester', 12), ('CSE', 'Confined space entry', 24)],
        'permit_competencies': {'GEN': ['IND'], 'HOT': ['IND', 'HOT'], 'WAH': ['IND', 'WAH'], 'ELEC': ['IND', 'LOTO']},
        'kpis': [('TRIR', 'Total recordable incident rate', 'trir', '', 1.0, False), ('LTIFR', 'Lost time injury frequency rate', 'ltifr', '', 0.5, False),
                 ('NM', 'Near misses reported', 'near_misses', 'reports', 10, True), ('ACT', 'Actions closed on time', 'action_closure', '%', 90, True),
                 ('AUD', 'Audit completion', 'audit_completion', '%', 100, True), ('TRN', 'Training compliance', 'training_compliance', '%', 95, True),
                 ('INS', 'Inspections completed', 'inspection_compliance', 'inspections', 20, True), ('BBS', 'BBS observations', 'observations', 'reports', 30, True),
                 ('ENV', 'Environmental exceedances', 'env_exceedances', 'readings', 0, False), ('QNC', 'Quality nonconformities', 'quality_ncs', 'NCs', 5, False),
                 ('PTW', 'Permits without violation', 'permit_compliance', '%', 100, True)],
        'forms': [
            {'kind': 'audit', 'code': 'AUD-HSEMS', 'name': 'HSE management system audit (ISO 45001 structure)', 'frequency': 'Yearly per site', 'questions': [
                Q('Leadership', 'HSE policy is approved, current and communicated', critical=True),
                Q('Leadership', 'HSE roles and responsibilities are assigned in writing'),
                Q('Planning', 'Hazard identification and risk assessments cover current activities', critical=True),
                Q('Planning', 'Legal and other requirements are identified and evaluated'),
                Q('Planning', 'HSE objectives have targets, owners and are monitored'),
                Q('Support', 'Training and competency records are current for critical tasks', critical=True),
                Q('Support', 'Documents are controlled – current revisions in use'),
                Q('Operation', 'Permit to work system is followed in the field', critical=True),
                Q('Operation', 'Contractors are pre-qualified and supervised'),
                Q('Operation', 'Emergency plans are tested by drills'),
                Q('Performance', 'Incidents are reported, investigated and actions verified', critical=True),
                Q('Performance', 'Inspections are carried out as planned'),
                Q('Improvement', 'Corrective actions are closed on time and verified for effectiveness'),
                Q('Improvement', 'Management review held and decisions tracked'),
            ]},
            {'kind': 'audit', 'code': 'AUD-CONTRACTOR', 'name': 'Contractor HSE audit', 'frequency': 'Per contract / quarterly', 'questions': [
                Q('Documents', 'Insurance, licences and HSE plan valid', critical=True), Q('People', 'Workers inducted and competent for their tasks', critical=True),
                Q('Field', 'Work under valid permits and risk assessments', critical=True), Q('Field', 'PPE worn and in good condition'),
                Q('Equipment', 'Plant and tools inspected and tagged'), Q('Supervision', 'Competent supervisor present at the work'),
            ]},
        ],
        'limits': [('Noise', 'noise', 'dB(A)', 85, 80, 'twa8'), ('Respirable crystalline silica', 'dust', 'mg/m³', 0.025, 0.0125, 'twa8'),
                   ('Carbon monoxide', 'chemical', 'ppm', 25, 12.5, 'twa8'), ('Hydrogen sulphide', 'chemical', 'ppm', 1, 0.5, 'twa8'),
                   ('Illuminance – general work area', 'lighting', 'lux', 300, None, 'minimum'),
                   ('Heat stress (WBGT, heavy work)', 'heat', '°C WBGT', 25, 23, 'instant'),
                   ('Hand-arm vibration', 'vibration', 'm/s² A(8)', 5, 2.5, 'dose')],
    },
    'construction': {
        'competencies': [('RIG', 'Rigger / slinger / banksman', 24), ('CRANE', 'Crane operator (third-party certified)', 24),
                         ('SCAF', 'Scaffold inspector', 36), ('EXC-CP', 'Excavation competent person', 36), ('PLANT', 'Plant / heavy equipment operator', 24)],
        'permit_competencies': {'EXC': ['IND', 'EXC-CP'], 'LIFT': ['IND', 'RIG'], 'CSE': ['IND', 'CSE']},
        'kpis': [('TBT', 'Toolbox talks held', 'manual', 'talks', 20, True)],
        'forms': [{'kind': 'audit', 'code': 'AUD-SITE-CON', 'name': 'Construction site HSE audit', 'frequency': 'Monthly', 'questions': [
            Q('Height', 'Edge protection and scaffolds tagged and inspected', critical=True), Q('Excavation', 'Excavations supported and inspected', critical=True),
            Q('Lifting', 'Lift plans and certified gear for all lifts', critical=True), Q('Traffic', 'Traffic management plan in place'),
            Q('Welfare', 'Welfare facilities, water and rest areas adequate (heat stress)'), Q('Housekeeping', 'Access routes clear, waste segregated'),
        ]}],
        'limits': [('Wood dust (hardwood)', 'dust', 'mg/m³', 2, 1, 'twa8')],
    },
    'manufacturing': {
        'competencies': [('FORK', 'Forklift operator', 36), ('CHEM', 'Chemical handling', 24), ('GUARD', 'Machine guarding / safe machine use', 24)],
        'permit_competencies': {'LOTO': ['LOTO'], 'CHEMH': ['CHEM']},
        'kpis': [('REW', 'Rework / scrap rate', 'manual', '%', 2, False), ('WASTE', 'Hazardous waste', 'waste_hazardous', 'kg', 500, False)],
        'forms': [{'kind': 'audit', 'code': 'AUD-PLANT', 'name': 'Plant safety and quality audit', 'frequency': 'Quarterly', 'questions': [
            Q('Machinery', 'Guards fitted and interlocks working', critical=True), Q('Energy', 'LOTO procedure followed and locks issued', critical=True),
            Q('Chemicals', 'SDS available and chemicals stored by compatibility', critical=True), Q('Quality', 'Nonconforming product identified and segregated'),
            Q('Hygiene', 'Exposure monitoring up to date'), Q('Environment', 'Waste segregated and manifests kept'),
        ]}],
        'limits': [('Toluene', 'chemical', 'ppm', 20, 10, 'twa8'), ('Total inhalable dust', 'dust', 'mg/m³', 10, 5, 'twa8')],
    },
    'healthcare': {
        'competencies': [('BLS', 'Basic life support', 24), ('IPC', 'Infection prevention and control', 12), ('SHARPS', 'Sharps safety', 12),
                         ('CYTO', 'Hazardous drug (cytotoxic) handling', 24), ('MANUAL', 'Patient manual handling', 24)],
        'permit_competencies': {'ICRA': ['IND'], 'MEDGAS': ['IND']},
        'kpis': [('NSI', 'Needlestick / sharps injuries', 'manual', 'injuries', 0, False), ('HH', 'Hand hygiene compliance', 'manual', '%', 90, True)],
        'forms': [{'kind': 'audit', 'code': 'AUD-HOSP', 'name': 'Hospital environment of care audit', 'frequency': 'Quarterly', 'questions': [
            Q('Sharps', 'Sharps containers below fill line and secured', critical=True), Q('Waste', 'Clinical waste segregated by colour code', critical=True),
            Q('IPC', 'Hand hygiene facilities available at point of care'), Q('Fire', 'Fire exits clear and extinguishers inspected', critical=True),
            Q('Chemicals', 'Hazardous drugs stored and labelled correctly'), Q('Medical gas', 'Medical gas cylinders secured and segregated'),
        ]}],
        'limits': [('Formaldehyde', 'chemical', 'ppm', 0.1, 0.05, 'twa8'), ('Glutaraldehyde', 'chemical', 'ppm', 0.05, None, 'ceiling')],
    },
    'oil_gas': {
        'competencies': [('H2S', 'H₂S awareness and escape', 12), ('BOSIET', 'Offshore survival (BOSIET / FOET)', 48), ('RADIO', 'Radiography operator', 36),
                         ('ISO-A', 'Isolating authority', 24)],
        'permit_competencies': {'HOT-OG': ['IND', 'HOT', 'H2S'], 'COLD-OG': ['IND', 'H2S'], 'CSE-OG': ['IND', 'CSE', 'H2S'], 'ISOL': ['ISO-A'],
                                'RADIO': ['RADIO']},
        'kpis': [('PSE', 'Process safety events (Tier 1 + 2)', 'manual', 'events', 0, False), ('LOC', 'Loss of containment events', 'manual', 'events', 0, False)],
        'forms': [{'kind': 'audit', 'code': 'AUD-PTW-OG', 'name': 'Permit to work and isolation audit', 'frequency': 'Monthly', 'questions': [
            Q('Permit', 'Permit displayed at the worksite and matches the work', critical=True), Q('Gas', 'Gas tests recorded at the required interval', critical=True),
            Q('Isolation', 'Isolation points match the certificate, locked and tagged', critical=True), Q('SIMOPS', 'SIMOPS conflicts reviewed in the permit meeting'),
            Q('People', 'Performing authority and crew know the controls'), Q('Hand-back', 'Completed permits handed back correctly'),
        ]}],
        'limits': [('Benzene', 'chemical', 'ppm', 0.5, 0.25, 'twa8'), ('Hydrogen sulphide (STEL)', 'chemical', 'ppm', 5, None, 'stel')],
    },
}
