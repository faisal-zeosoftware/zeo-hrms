"""Maintenance industry packs: categories with response / fix times and who pays, and inspection checklists."""


def cat(rows):
    return [{'code': c, 'name': n, 'trade': t, 'priority': p, 'response_hours': r, 'resolve_hours': f, 'who_pays': w} for c, n, t, p, r, f, w in rows]


def chk(rows):
    return [{'code': c, 'name': n, 'kind': k, 'items': i} for c, n, k, i in rows]


GEN_ROWS = [('ELEC', 'Electrical', 'Electrician', 'normal', 8, 48, 'company'), ('PLUMB', 'Plumbing', 'Plumber', 'normal', 8, 48, 'company'),
            ('AC', 'Air conditioning', 'AC technician', 'high', 4, 24, 'company')]
ROOM = ['Walls and paint', 'Floors', 'Doors and locks', 'Windows', 'Lights and switches', 'Power sockets', 'AC', 'Ceiling']
WET = ['Taps and mixers', 'Drains', 'WC and flush', 'Water heater', 'Tiles and grout', 'Leaks']
KITCHEN = ['Cabinets', 'Hob / cooker', 'Extractor', 'Sink']

PACKS = {
    'general': {
        'label': 'General', 'description': 'Common trades (electrical, plumbing, AC, carpentry, cleaning, IT) with response and fix times, and a routine checklist.',
        'highlights': ['8 categories with SLA', 'Routine inspection checklist'],
        'rows': {
            'category': cat([('ELEC', 'Electrical', 'Electrician', 'normal', 8, 48, 'company'), ('PLUMB', 'Plumbing', 'Plumber', 'normal', 8, 48, 'company'),
                             ('AC', 'Air conditioning', 'AC technician', 'high', 4, 24, 'company'), ('CARP', 'Carpentry', 'Carpenter', 'low', 24, 120, 'company'),
                             ('PAINT', 'Painting', 'Painter', 'low', 48, 168, 'company'), ('CLEAN', 'Cleaning', 'Cleaner', 'normal', 24, 48, 'company'),
                             ('IT', 'IT / low current', 'IT technician', 'normal', 8, 48, 'company'), ('GEN', 'General repair', 'Handyman', 'normal', 24, 72, 'company')]),
            'checklist': chk([('ROUTINE', 'Routine inspection', 'routine', ROOM + ['Fire extinguisher', 'Emergency lights', 'First aid kit'])]),
        }},
    'property_management': {
        'label': 'Property management', 'description': 'Building and unit trades with emergency response, who pays (owner, tenant, developer warranty), '
                                                       'and move-in, move-out, unit-ready and snag checklists. Works with ZEO Property.',
        'highlights': ['14 categories', 'Emergency 1-hour response', 'Move-in / move-out / unit-ready / snag checklists'],
        'rows': {
            'category': cat([('EMERG', 'Emergency (water leak, power cut, lift entrapment)', 'Duty technician', 'urgent', 1, 6, 'decide'),
                             ('AC', 'Air conditioning', 'AC technician', 'high', 4, 24, 'owner'), ('ELEC', 'Electrical', 'Electrician', 'normal', 8, 48, 'owner'),
                             ('PLUMB', 'Plumbing', 'Plumber', 'normal', 8, 48, 'owner'), ('BLOCK', 'Blockage / drains (tenant use)', 'Plumber', 'normal', 8, 24, 'tenant'),
                             ('APPL', 'Appliances', 'Appliance technician', 'normal', 24, 72, 'owner'), ('CARP', 'Doors, locks and carpentry', 'Carpenter', 'normal', 24, 72, 'owner'),
                             ('PAINT', 'Painting', 'Painter', 'low', 48, 168, 'owner'), ('PEST', 'Pest control', 'Pest control', 'normal', 24, 72, 'tenant'),
                             ('LIFT', 'Lifts', 'Lift contractor', 'high', 2, 24, 'company'), ('FIRE', 'Fire and life safety', 'Fire contractor', 'high', 4, 24, 'company'),
                             ('COMMON', 'Common areas', 'Handyman', 'normal', 24, 72, 'company'), ('CLEAN', 'Deep cleaning', 'Cleaner', 'normal', 24, 72, 'tenant'),
                             ('SNAG', 'Snag (new handover)', 'Developer contractor', 'normal', 48, 336, 'warranty')]),
            'checklist': chk([('MOVEIN', 'Move-in inspection', 'move_in', ['Keys and access cards'] + ROOM + WET + KITCHEN + ['Meter readings']),
                              ('MOVEOUT', 'Move-out inspection', 'move_out', ['Keys and access cards returned'] + ROOM + WET + KITCHEN + ['Final meter readings', 'Cleanliness']),
                              ('READY', 'Unit ready for letting', 'unit_ready', ['Painting done', 'Deep cleaning done', 'AC serviced', 'All lights working', 'No leaks', 'Pest control done']),
                              ('SNAG', 'Snag list (new unit)', 'snag', ROOM + WET + KITCHEN + ['Balcony', 'Built-in wardrobes', 'Intercom']),
                              ('ROUTINE', 'Routine building inspection', 'routine', ['Lobby and corridors', 'Lifts', 'Fire alarm panel', 'Fire extinguishers',
                                                                                       'Emergency lights', 'Pump room', 'Roof and water tanks', 'Car park', 'Garbage room'])]),
        }},
    # v1.38.0: one pack per business type of the company master
    'construction': {
        'label': 'Construction and engineering', 'description': 'Site plant and equipment (excavators, cranes, generators, scaffolding), site facilities and tower cranes with safety inspections.',
        'highlights': ['Plant and equipment categories', 'Daily plant check and scaffold inspection checklists'],
        'rows': {'category': cat([('PLANT', 'Heavy plant (excavator, loader, roller)', 'Plant mechanic', 'high', 2, 24, 'company'), ('CRANE', 'Cranes and hoists', 'Crane technician', 'urgent', 1, 12, 'company'),
                                  ('GENSET', 'Generators and power', 'Electrician', 'urgent', 2, 12, 'company'), ('SCAFF', 'Scaffolding and formwork', 'Scaffolder', 'high', 4, 24, 'company'),
                                  ('VEHICLE', 'Site vehicles', 'Mechanic', 'normal', 8, 48, 'company'), ('CAMP', 'Labour camp and site offices', 'Handyman', 'normal', 24, 72, 'company'),
                                  ('TOOLS', 'Power tools', 'Store technician', 'low', 24, 120, 'company')] + [GEN_ROWS[0], GEN_ROWS[1], GEN_ROWS[2]]),
                 'checklist': chk([('PLANTDAILY', 'Daily plant check', 'routine', ['Fluid levels', 'Tyres / tracks', 'Brakes', 'Lights and horn', 'Reversing alarm', 'Hydraulic leaks', 'Operator cabin', 'Fire extinguisher']),
                                   ('SCAFFOLD', 'Scaffold inspection (7-day)', 'routine', ['Base plates and sole boards', 'Standards and ledgers', 'Bracing', 'Ties', 'Platforms and toe boards', 'Guard rails', 'Ladders and access', 'Scaff tag']),
                                   ('CRANE', 'Crane pre-use', 'routine', ['Load chart', 'Wire ropes', 'Hook and latch', 'Limit switches', 'Outriggers', 'Third-party certificate valid'])])}},
    'oil_gas': {
        'label': 'Oil and gas', 'description': 'Rotating equipment, static equipment, instrumentation, pipelines and safety systems with permit-based response times.',
        'highlights': ['Rotating / static / instrumentation categories', 'Pressure safety valve and fire system checklists'],
        'rows': {'category': cat([('ROT', 'Rotating equipment (pumps, compressors)', 'Mechanical technician', 'high', 2, 24, 'company'), ('STATIC', 'Static equipment (vessels, exchangers)', 'Mechanical technician', 'normal', 8, 72, 'company'),
                                  ('INST', 'Instrumentation and control', 'Instrument technician', 'high', 2, 24, 'company'), ('ELEC', 'Electrical (MV / LV)', 'Electrician', 'high', 4, 24, 'company'),
                                  ('PIPE', 'Piping and valves', 'Pipefitter', 'normal', 8, 48, 'company'), ('SAFETY', 'Fire and gas / safety systems', 'Safety technician', 'urgent', 1, 12, 'company'),
                                  ('HVAC', 'HVAC', 'AC technician', 'normal', 8, 48, 'company'), ('CIVIL', 'Civil and structures', 'Civil technician', 'low', 48, 168, 'company')]),
                 'checklist': chk([('PSV', 'Pressure safety valve test', 'routine', ['Tag and set pressure', 'Pop test', 'Reseat', 'Seal and certificate', 'Record in register']),
                                   ('FIREGAS', 'Fire and gas system check', 'routine', ['Detectors clean', 'Panel healthy', 'Deluge valves', 'Extinguishers', 'Alarm test', 'Emergency lighting']),
                                   ('ROUTINE', 'Area walk-down', 'routine', ['Leaks', 'Vibration / noise', 'Housekeeping', 'Guards in place', 'Labels and tags', 'Lighting'])])}},
    'healthcare': {
        'label': 'Healthcare', 'description': 'Biomedical equipment, medical gases, sterilisation, HVAC with isolation rooms and patient-area repairs with infection-control checklists.',
        'highlights': ['Biomedical and medical gas categories', 'Medical gas, autoclave and ward checklists'],
        'rows': {'category': cat([('BIOMED', 'Biomedical equipment', 'Biomedical engineer', 'urgent', 1, 12, 'company'), ('MEDGAS', 'Medical gases and vacuum', 'Medical gas technician', 'urgent', 1, 8, 'company'),
                                  ('STERIL', 'Sterilisation / CSSD', 'Biomedical engineer', 'high', 2, 24, 'company'), ('HVAC', 'HVAC and isolation rooms', 'AC technician', 'high', 2, 24, 'company'),
                                  ('ELEC', 'Electrical and UPS', 'Electrician', 'high', 2, 24, 'company'), ('PLUMB', 'Plumbing and water quality', 'Plumber', 'normal', 8, 48, 'company'),
                                  ('LIFT', 'Lifts and patient hoists', 'Lift contractor', 'high', 2, 24, 'company'), ('WARD', 'Ward and room repairs', 'Handyman', 'normal', 24, 72, 'company'),
                                  ('IT', 'IT and nurse call', 'IT technician', 'high', 4, 24, 'company')]),
                 'checklist': chk([('MEDGAS', 'Medical gas outlet check', 'routine', ['Outlet pressure', 'Flow', 'Alarm panel', 'Manifold cylinders', 'Leak test']),
                                   ('AUTOCLAVE', 'Autoclave validation', 'routine', ['Bowie-Dick test', 'Biological indicator', 'Door seal', 'Printer / log', 'Safety valve']),
                                   ('WARD', 'Ward inspection', 'routine', ['Bed and rails', 'Nurse call', 'Oxygen outlet', 'Lighting', 'AC', 'Bathroom and grab rails', 'Hand-wash points'])])}},
    'education': {
        'label': 'Education', 'description': 'Classrooms, laboratories, sports facilities, school transport and playgrounds with term-break maintenance checklists.',
        'highlights': ['Lab, sports and transport categories', 'Playground and lab safety checklists'],
        'rows': {'category': cat([('CLASS', 'Classrooms and furniture', 'Handyman', 'normal', 24, 72, 'company'), ('LAB', 'Laboratories', 'Lab technician', 'high', 8, 48, 'company'),
                                  ('SPORT', 'Sports and swimming pool', 'Facilities technician', 'normal', 24, 72, 'company'), ('PLAY', 'Playground equipment', 'Facilities technician', 'high', 8, 48, 'company'),
                                  ('BUS', 'School transport', 'Mechanic', 'high', 4, 24, 'company'), ('IT', 'IT, smart boards and Wi-Fi', 'IT technician', 'high', 4, 24, 'company'),
                                  ('AC', 'Air conditioning', 'AC technician', 'high', 4, 24, 'company'), ('ELEC', 'Electrical', 'Electrician', 'normal', 8, 48, 'company'),
                                  ('PLUMB', 'Plumbing and washrooms', 'Plumber', 'normal', 8, 48, 'company'), ('CLEAN', 'Cleaning', 'Cleaner', 'normal', 24, 48, 'company')]),
                 'checklist': chk([('PLAYGROUND', 'Playground safety', 'routine', ['Surfacing', 'Bolts and fixings', 'Sharp edges', 'Shade structures', 'Fencing and gates']),
                                   ('LAB', 'Laboratory safety', 'routine', ['Fume cupboard', 'Eye wash and shower', 'Chemical store', 'Gas taps', 'Fire extinguisher', 'First aid kit']),
                                   ('TERMBREAK', 'Term-break maintenance', 'routine', ['Paint touch-ups', 'AC service', 'Deep cleaning', 'Furniture repairs', 'Lighting', 'Water tanks'])])}},
    'manufacturing': {
        'label': 'Manufacturing', 'description': 'Production lines, utilities (compressed air, boilers, chillers), material handling and tooling with line-stoppage response times.',
        'highlights': ['Production line and utilities categories', 'Line start-up and forklift checklists'],
        'rows': {'category': cat([('LINE', 'Production line / machine breakdown', 'Maintenance technician', 'urgent', 1, 8, 'company'), ('UTIL', 'Utilities (air, steam, chillers)', 'Utilities technician', 'high', 2, 24, 'company'),
                                  ('MHE', 'Forklifts and material handling', 'Mechanic', 'high', 4, 24, 'company'), ('TOOL', 'Tooling and moulds', 'Toolmaker', 'normal', 8, 72, 'company'),
                                  ('ELEC', 'Electrical and drives', 'Electrician', 'high', 2, 24, 'company'), ('INST', 'Instrumentation / PLC', 'Automation technician', 'high', 2, 24, 'company'),
                                  ('BUILD', 'Building and warehouse', 'Handyman', 'normal', 24, 72, 'company'), ('HVAC', 'HVAC', 'AC technician', 'normal', 8, 48, 'company')]),
                 'checklist': chk([('STARTUP', 'Line start-up check', 'routine', ['Guards and interlocks', 'Emergency stops', 'Lubrication', 'Belts and chains', 'Sensors', 'Housekeeping']),
                                   ('FORKLIFT', 'Forklift daily check', 'routine', ['Forks and chains', 'Hydraulics', 'Brakes', 'Horn and lights', 'Tyres', 'Battery / fuel', 'Seat belt']),
                                   ('ROUTINE', 'Utilities round', 'routine', ['Compressor pressure', 'Dryer', 'Chiller temperatures', 'Boiler water level', 'Leaks', 'Filters'])])}},
    'retail': {
        'label': 'Retail and hospitality', 'description': 'Store and outlet repairs, refrigeration, kitchen equipment, POS / IT, signage and shop-fit with opening-hours response.',
        'highlights': ['Refrigeration and kitchen categories', 'Store opening and kitchen hygiene checklists'],
        'rows': {'category': cat([('REFRIG', 'Refrigeration and cold rooms', 'Refrigeration technician', 'urgent', 1, 8, 'company'), ('KITCHEN', 'Kitchen equipment', 'Kitchen technician', 'high', 2, 24, 'company'),
                                  ('POS', 'POS, IT and CCTV', 'IT technician', 'high', 2, 12, 'company'), ('AC', 'Air conditioning', 'AC technician', 'high', 4, 24, 'company'),
                                  ('SIGN', 'Signage and lighting', 'Electrician', 'normal', 24, 72, 'company'), ('FIT', 'Shop-fit and fixtures', 'Carpenter', 'normal', 24, 120, 'company'),
                                  ('PLUMB', 'Plumbing and washrooms', 'Plumber', 'normal', 8, 48, 'company'), ('CLEAN', 'Cleaning and pest control', 'Cleaner', 'normal', 24, 48, 'company'),
                                  ('DOOR', 'Doors, shutters and locks', 'Handyman', 'high', 4, 24, 'company')]),
                 'checklist': chk([('OPENING', 'Store opening check', 'routine', ['Shutters and doors', 'Lighting and signage', 'AC temperature', 'POS working', 'Fridges at temperature', 'Washrooms', 'Fire exits clear']),
                                   ('HYGIENE', 'Kitchen hygiene and equipment', 'routine', ['Fridge / freezer temperatures', 'Extraction hood and filters', 'Grease trap', 'Gas connections', 'Fire suppression', 'Hand-wash sink'])])}},
    'it': {
        'label': 'Information technology', 'description': 'Office IT, server room and network, data-centre power and cooling, access control and office facilities.',
        'highlights': ['Server room / network categories', 'Server room and UPS checklists'],
        'rows': {'category': cat([('NET', 'Network and connectivity', 'Network engineer', 'urgent', 1, 8, 'company'), ('SERVER', 'Servers and storage', 'System engineer', 'urgent', 1, 12, 'company'),
                                  ('ENDUSER', 'Laptops, desktops and printers', 'IT technician', 'normal', 4, 24, 'company'), ('POWER', 'UPS and power', 'Electrician', 'urgent', 1, 12, 'company'),
                                  ('COOL', 'Server room cooling', 'AC technician', 'urgent', 1, 12, 'company'), ('ACCESS', 'Access control and CCTV', 'Security technician', 'high', 4, 24, 'company'),
                                  ('OFFICE', 'Office facilities', 'Handyman', 'normal', 24, 72, 'company'), ('AC', 'Office air conditioning', 'AC technician', 'normal', 8, 48, 'company')]),
                 'checklist': chk([('SERVERROOM', 'Server room check', 'routine', ['Temperature and humidity', 'UPS status', 'Cable management', 'Fire suppression', 'Access log', 'Water leak sensor']),
                                   ('UPS', 'UPS and generator test', 'routine', ['Battery health', 'Load test', 'Transfer switch', 'Fuel level', 'Alarm log'])])}},
    'finance': {
        'label': 'Finance', 'description': 'Office facilities, branch security (safes, CCTV, ATMs), IT and client-area upkeep with next-business-day response.',
        'highlights': ['Branch security and ATM categories', 'Branch opening checklist'],
        'rows': {'category': cat([('SECURITY', 'Safes, CCTV and alarms', 'Security technician', 'urgent', 1, 12, 'company'), ('ATM', 'ATMs and kiosks', 'ATM vendor', 'high', 2, 24, 'company'),
                                  ('IT', 'IT and network', 'IT technician', 'high', 2, 24, 'company'), ('OFFICE', 'Office and client area', 'Handyman', 'normal', 24, 72, 'company'),
                                  ('AC', 'Air conditioning', 'AC technician', 'high', 4, 24, 'company'), ('ELEC', 'Electrical and UPS', 'Electrician', 'high', 4, 24, 'company'),
                                  ('CLEAN', 'Cleaning', 'Cleaner', 'normal', 24, 48, 'company'), ('SIGN', 'Signage and branding', 'Electrician', 'low', 48, 168, 'company')]),
                 'checklist': chk([('BRANCH', 'Branch opening check', 'routine', ['Alarm and CCTV', 'Safe and vault', 'ATM online', 'Lighting and signage', 'Queue system', 'Client area tidy', 'Fire exits clear'])])}},
}
