"""Asset Management industry packs: asset categories with depreciation rules (straight line unless said otherwise).
Useful lives are common IFRS practice in the UAE; change them in Setup › Categories to match your accounting policy.
Every pack also brings the assets already registered in HR → Assets into Asset Management (nothing is copied twice)."""


def cat(rows):
    out = []
    for r in rows:
        code, name, prefix, life, residual, *rest = r
        flags = rest[0] if rest else ''
        out.append({'code': code, 'name': name, 'code_prefix': prefix, 'life_months': life, 'residual_percent': residual,
                    'method': 'none' if life == 0 else 'straight_line', 'capitalise': life != 0,
                    'requires_serial': 's' in flags, 'audit_every_months': 6 if 'a' in flags else 12})
    return out


def import_hr(app, user):
    from .services import import_all
    import_all()


COMMON = [('LAND', 'Land', 'LND', 0, 0), ('BLDG', 'Buildings', 'BLD', 300, 5), ('LHI', 'Leasehold improvements / fit-out', 'FIT', 60, 0),
          ('FURN', 'Furniture and fixtures', 'FUR', 60, 0), ('OFFEQ', 'Office equipment', 'OEQ', 60, 0),
          ('IT', 'Computers and IT equipment', 'IT', 36, 0, 'sa'), ('MOB', 'Mobile phones and tablets', 'MOB', 24, 0, 'sa'),
          ('NET', 'Network and servers', 'NET', 60, 0, 's'), ('VEH', 'Motor vehicles', 'VEH', 60, 10, 's'),
          ('SW', 'Software licences', 'SW', 36, 0), ('LOW', 'Low-value items (expensed)', 'LV', 0, 0)]

# v1.34.0 release 2: maintenance activity types and helpdesk SLA rules (every pack)
def act(rows):
    return [{'code': c, 'name': n, 'kind': k, 'hours': h, 'out_of_service': o, 'checklist': cl} for c, n, k, h, o, cl in rows]


ACTIVITIES = act([
    ('SERVICE', 'General service', 'preventive', 1, False, ['Visual check', 'Clean', 'Check fixings and cables', 'Test operation']),
    ('IT-PM', 'IT preventive maintenance', 'preventive', 1, False, ['Clean dust and vents', 'Windows / OS updates', 'Antivirus up to date', 'Disk health', 'Battery health']),
    ('VEH-SVC', 'Vehicle service', 'preventive', 4, True, ['Engine oil and filter', 'Air filter', 'Brakes', 'Tyres and pressure', 'Lights', 'Coolant', 'Registration and insurance date']),
    ('AC-PM', 'AC preventive maintenance', 'preventive', 2, False, ['Clean filters', 'Check gas pressure', 'Clean coils', 'Check drain', 'Check thermostat']),
    ('GEN-PM', 'Generator service', 'preventive', 3, True, ['Oil and filters', 'Battery', 'Coolant', 'Load test', 'Fuel level']),
    ('INSPECT', 'Safety inspection', 'inspection', 1, False, ['Guards in place', 'No damage', 'Labels readable', 'Safe to use']),
    ('CALIB', 'Calibration', 'calibration', 2, True, ['Reference standard used', 'Readings within tolerance', 'Calibration label updated', 'Certificate attached']),
    ('REPAIR', 'Repair', 'corrective', 2, True, []),
    ('BREAK', 'Breakdown repair', 'breakdown', 3, True, []),
])
SLAS = [{'code': 'URGENT', 'priority': 'urgent', 'response_hours': 1, 'resolve_hours': 4, 'escalate_hours': 1},
        {'code': 'HIGH', 'priority': 'high', 'response_hours': 2, 'resolve_hours': 8, 'escalate_hours': 2},
        {'code': 'NORMAL', 'priority': 'normal', 'response_hours': 4, 'resolve_hours': 24, 'escalate_hours': 4},
        {'code': 'LOW', 'priority': 'low', 'response_hours': 8, 'resolve_hours': 72, 'escalate_hours': 8}]


PACKS = {
    'general': {
        'label': 'General', 'description': 'Office and company assets: buildings, fit-out, furniture, office and IT equipment, phones, vehicles, software.',
        'highlights': ['11 categories with useful life', 'Serial number required for IT, phones and vehicles', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON)}, 'after': import_hr},
    'construction': {
        'label': 'Construction and oil & gas', 'description': 'Plant and heavy equipment, site vehicles, tools issued from the store, '
                                                               'scaffolding and formwork, site cabins, survey and safety equipment.',
        'highlights': ['Plant, tools and site equipment', 'Tools checked out from the store', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('PLANT', 'Plant and heavy equipment', 'PLT', 120, 10, 's'), ('SITEVEH', 'Site vehicles and trucks', 'SVH', 60, 10, 's'),
                                           ('TOOLS', 'Power and hand tools', 'TL', 36, 0, 'a'), ('SCAF', 'Scaffolding and formwork', 'SCF', 60, 0),
                                           ('CABIN', 'Site cabins and containers', 'CAB', 60, 5), ('SURV', 'Survey instruments', 'SRV', 60, 0, 's'),
                                           ('GEN', 'Generators and compressors', 'GEN', 84, 5, 's'), ('SAFE', 'Safety and gas detection equipment', 'SFE', 36, 0, 'sa')])},
        'after': import_hr},
    'healthcare': {
        'label': 'Healthcare', 'description': 'Medical and diagnostic equipment, lab equipment, beds and patient furniture, ambulances.',
        'highlights': ['Medical equipment with serial numbers', 'Six-monthly physical checks', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('MED', 'Medical equipment', 'MED', 84, 0, 'sa'), ('DIAG', 'Imaging and diagnostic equipment', 'DIA', 96, 5, 'sa'),
                                           ('LAB', 'Laboratory equipment', 'LAB', 84, 0, 's'), ('BED', 'Beds and patient furniture', 'BED', 84, 0),
                                           ('AMB', 'Ambulances', 'AMB', 60, 10, 's')])},
        'after': import_hr},
    'manufacturing': {
        'label': 'Manufacturing', 'description': 'Production machinery, moulds and dies, material handling (forklifts), test equipment, tools.',
        'highlights': ['Machinery 10 years', 'Forklifts and material handling', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('MACH', 'Production machinery', 'MCH', 120, 5, 's'), ('MOULD', 'Moulds and dies', 'MLD', 60, 0),
                                           ('MHE', 'Forklifts and material handling', 'MHE', 84, 10, 's'), ('TEST', 'Test and measuring equipment', 'TST', 60, 0, 's'),
                                           ('TOOLS', 'Tools', 'TL', 36, 0, 'a')])},
        'after': import_hr},
    'education': {
        'label': 'Education', 'description': 'Classroom furniture, smart boards and projectors, lab and sports equipment, library, school buses.',
        'highlights': ['Classroom and lab equipment', 'Student laptops and tablets', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('CLASS', 'Classroom furniture', 'CLS', 84, 0), ('AV', 'Smart boards and projectors', 'AV', 60, 0, 's'),
                                           ('SLAB', 'Science lab equipment', 'SLB', 84, 0), ('SPORT', 'Sports equipment', 'SPT', 60, 0),
                                           ('BUS', 'School buses', 'BUS', 120, 10, 's'), ('LIB', 'Library books', 'LIB', 0, 0)])},
        'after': import_hr},
    'property_management': {
        'label': 'Property management', 'description': 'Building systems (AC plant, lifts, pumps, fire systems), furniture and appliances in units. '
                                                       'Works with ZEO Property and Maintenance.',
        'highlights': ['Building systems and appliances in units', 'Assets linked to Property units', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('HVAC', 'AC plant and chillers', 'AC', 120, 5, 's'), ('LIFT', 'Lifts and escalators', 'LFT', 180, 5, 's'),
                                           ('PUMP', 'Pumps and water systems', 'PMP', 120, 0, 's'), ('FIRE', 'Fire and life-safety systems', 'FIR', 120, 0),
                                           ('APPL', 'Appliances in units', 'APL', 60, 0, 's'), ('UNITF', 'Furniture in units', 'UFR', 60, 0)])},
        'after': import_hr},
    'retail': {
        'label': 'Retail', 'description': 'Store fit-out, POS terminals, display fixtures, refrigeration, CCTV and security.',
        'highlights': ['POS and store equipment', 'Store fit-out', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('POS', 'POS terminals and scanners', 'POS', 48, 0, 'sa'), ('DISP', 'Display fixtures and shelving', 'DSP', 60, 0),
                                           ('FRIDGE', 'Refrigeration', 'REF', 84, 5, 's'), ('CCTV', 'CCTV and security', 'CCT', 60, 0)])},
        'after': import_hr},
    # v1.38.0
    'it': {
        'label': 'Information technology', 'description': 'Servers, storage and network, end-user devices, licences and software, data-centre power and cooling.',
        'highlights': ['Server / network / end-user categories', 'Software licences', 'Brings in the HR assets'],
        'rows': {'category': cat(COMMON + [('SRV', 'Servers and storage', 'SRV', 60, 5, 'sa'), ('NET', 'Network equipment', 'NET', 60, 0, 'sa'), ('LIC', 'Software licences', 'LIC', 36, 0),
                                           ('UPS', 'UPS and power', 'UPS', 84, 5), ('DC', 'Data-centre cooling', 'DCC', 120, 5), ('TEST', 'Test devices', 'TST', 36, 0, 's')])},
        'after': import_hr},
}


for _p in PACKS.values():   # v1.34.0: every pack brings the activity types and SLA rules
    _p['rows']['activity'] = ACTIVITIES
    _p['rows']['sla'] = SLAS
