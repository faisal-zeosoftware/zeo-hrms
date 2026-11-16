"""v1.67.14 (service release 2): content of the 18 service-industry packs, shared by Projects (equipment, AMC types, ticket
categories), CRM (pipeline, sources, lost reasons) and Maintenance (trades with response / fix times, checklists).
One source so the three apps install the same industry the same way. Content only – no feature is switched on or off by industry.
Codes are the pack's natural keys (get_or_create by code: installing twice adds nothing, nothing is renumbered)."""


def F(key, label, typ='text', options=None):
    d = {'key': key, 'label': label, 'type': typ}
    if options:
        d['options'] = options
    return d


# shared extra fields
SERIAL_MAKE = [F('make', 'Make'), F('model', 'Model')]
REFRIG = F('refrigerant', 'Refrigerant', 'choice', ['R32', 'R410A', 'R134a', 'R407C', 'R22', 'Other'])
CAP_TR = F('capacity_tr', 'Capacity (TR)', 'number')
KVA = F('kva', 'Rating (kVA)', 'number')
CERT_EXP = F('cert_expiry', 'Certificate expiry', 'date')
CIVIL_DEF = F('civil_defence_ref', 'Civil Defence approval ref.')


def _std_pipeline(survey='Site survey', quote='Quotation', award='Awarded (AMC / project)'):
    """Service sales: enquiry → survey → quotation → negotiation → awarded / lost."""
    return [('ENQUIRY', 'Enquiry', 10), ('SURVEY', survey, 25), ('QUOTE', quote, 50), ('NEGOTIATION', 'Negotiation', 75), ('WON', award, 100), ('LOST', 'Lost', 0)]


def _amc(prefix, visits, extra=()):
    """The four usual AMC types of a service industry + the industry's own."""
    return [(f'{prefix}-COMP', 'Comprehensive (parts and labour)', 'included', True, visits, None),
            (f'{prefix}-NONCOMP', 'Non-comprehensive (labour only)', 'none', True, visits, None),
            (f'{prefix}-PARTSLIM', 'Parts up to a limit', 'limit', True, visits, None),
            (f'{prefix}-ONCALL', 'On-call (per visit)', 'none', True, 0, 0)] + list(extra)


SERVICE_SOURCES = [('TENDER', 'Tender portal'), ('CONSULTANT', 'Consultant / MEP designer'), ('FMCO', 'Facility management company'),
                   ('EXISTING', 'Existing AMC client')]
SERVICE_LOST = [('BUDGET', 'Budget not approved'), ('INHOUSE', 'Done in-house'), ('INCUMBENT', 'Stayed with current contractor')]

INDUSTRIES = {
    'hvac': {
        'label': 'HVAC & Air Conditioning', 'prefix': 'HVAC',
        'crm': {'stages': _std_pipeline('Site survey / load check'), 'sources': [('DEVELOPER', 'Developer / handover')], 'lost': [('BRAND', 'Brand preference')]},
        'equipment': [('HVAC-SPLIT', 'Split / ducted split unit', 3, SERIAL_MAKE + [CAP_TR, REFRIG]),
                      ('HVAC-PKG', 'Package unit', 3, SERIAL_MAKE + [CAP_TR, REFRIG]),
                      ('HVAC-CHILLER', 'Chiller', 1, SERIAL_MAKE + [CAP_TR, REFRIG, F('compressors', 'Compressors', 'number')]),
                      ('HVAC-AHU', 'Air handling unit', 3, SERIAL_MAKE + [F('airflow_cfm', 'Airflow (CFM)', 'number'), F('filter_size', 'Filter size')]),
                      ('HVAC-FCU', 'Fan coil unit', 3, SERIAL_MAKE + [F('location', 'Ceiling / room')]),
                      ('HVAC-VRF', 'VRF outdoor unit', 3, SERIAL_MAKE + [F('capacity_hp', 'Capacity (HP)', 'number'), REFRIG]),
                      ('HVAC-CT', 'Cooling tower', 1, SERIAL_MAKE + [F('legionella_test', 'Last legionella test', 'date')]),
                      ('HVAC-EXH', 'Exhaust / fresh air fan', 6, SERIAL_MAKE)],
        'amc': _amc('HVAC', 4, [('HVAC-CHILLER-PM', 'Chiller PPM (monthly visits)', 'limit', True, 12, 4)]),
        'issues': ['Not cooling', 'Water leakage', 'Noise / vibration', 'Bad smell', 'Thermostat / controls', 'Filter / coil cleaning'],
        'maint': [('AC-BREAK', 'AC breakdown', 'AC technician', 'urgent', 2, 12), ('AC-LEAK', 'Water leak from AC', 'AC technician', 'high', 4, 24),
                  ('CHILLER', 'Chiller / plant fault', 'Chiller technician', 'urgent', 1, 8), ('AC-PPM', 'Preventive service', 'AC technician', 'normal', 24, 72),
                  ('DUCT', 'Duct cleaning', 'Duct cleaner', 'low', 48, 168)],
        'checklists': [('HVAC-PPM', 'AC preventive service', ['Clean / replace filters', 'Clean evaporator coil', 'Clean condenser coil', 'Check refrigerant pressure',
                                                               'Check drain line and tray', 'Check fan motor and belt', 'Measure supply / return temperature',
                                                               'Check electrical connections', 'Check thermostat']),
                       ('HVAC-CHILLER', 'Chiller monthly check', ['Suction / discharge pressure', 'Oil level and pressure', 'Chilled water in / out temperature',
                                                                  'Condenser approach', 'Compressor current', 'Leak test', 'Alarm history'])],
    },
    'mep': {
        'label': 'MEP Contractors', 'prefix': 'MEP',
        'crm': {'stages': _std_pipeline('Drawing review / site visit', 'BOQ / quotation', 'LOI / contract awarded'), 'sources': [('MAINCON', 'Main contractor')],
                'lost': [('SPEC', 'Specification not met')]},
        'equipment': [('MEP-DB', 'Distribution board', 12, SERIAL_MAKE + [F('amps', 'Rating (A)', 'number')]),
                      ('MEP-PANEL', 'MDB / SMDB panel', 6, SERIAL_MAKE + [F('amps', 'Rating (A)', 'number')]),
                      ('MEP-PUMP', 'Pump set (water / booster)', 3, SERIAL_MAKE + [F('kw', 'Motor (kW)', 'number')]),
                      ('MEP-TANK', 'Water tank', 6, [F('capacity_l', 'Capacity (litres)', 'number'), F('material', 'Material', 'choice', ['GRP', 'Concrete', 'Steel', 'Polyethylene'])]),
                      ('MEP-HEATER', 'Water heater / calorifier', 6, SERIAL_MAKE + [F('capacity_l', 'Capacity (litres)', 'number')]),
                      ('MEP-GEN', 'Generator', 1, SERIAL_MAKE + [KVA, F('run_hours', 'Running hours', 'number')]),
                      ('MEP-LIGHT', 'Lighting circuit', 12, [F('area', 'Area')]),
                      ('MEP-DRAIN', 'Drainage / sewage line', 12, [F('size_mm', 'Pipe size (mm)', 'number')])],
        'amc': _amc('MEP', 4),
        'issues': ['Power trip', 'No water', 'Low water pressure', 'Drain blockage', 'Leak', 'Lighting fault'],
        'maint': [('ELEC', 'Electrical fault', 'Electrician', 'high', 4, 24), ('PLUMB', 'Plumbing', 'Plumber', 'normal', 8, 48),
                  ('PUMP', 'Pump / pressure', 'Plumber', 'high', 4, 24), ('GEN', 'Generator', 'Electrician', 'urgent', 2, 12), ('DRAIN', 'Drain blockage', 'Plumber', 'high', 4, 24)],
        'checklists': [('MEP-ELEC', 'Electrical panel check', ['Thermal scan', 'Tighten terminations', 'Breaker operation', 'Earth leakage test', 'Panel labels', 'Clean panel']),
                       ('MEP-PUMP', 'Pump room check', ['Pressure set points', 'Leaks', 'Vibration', 'Motor current', 'Pressure vessel charge', 'Valves'])],
    },
    'fire': {
        'label': 'Fire & Safety', 'prefix': 'FIRE',
        'crm': {'stages': _std_pipeline('Site survey / Civil Defence check', 'Quotation', 'AMC / installation awarded'), 'sources': [('CIVILDEF', 'Civil Defence notice')],
                'lost': [('APPROVAL', 'Civil Defence approval timing')]},
        'equipment': [('FIRE-PANEL', 'Fire alarm panel', 3, SERIAL_MAKE + [F('zones', 'Zones / loops', 'number'), CIVIL_DEF]),
                      ('FIRE-DET', 'Smoke / heat detector', 6, [F('type', 'Type', 'choice', ['Smoke', 'Heat', 'Multi-sensor', 'Beam']), F('zone', 'Zone')]),
                      ('FIRE-EXT', 'Fire extinguisher', 12, [F('type', 'Type', 'choice', ['CO2', 'DCP', 'Foam', 'Water', 'Wet chemical']), F('kg', 'Weight (kg)', 'number'),
                                                             F('refill_due', 'Refill / hydro test due', 'date')]),
                      ('FIRE-SPR', 'Sprinkler system', 6, [F('heads', 'Heads', 'number'), F('valve_set', 'Valve set')]),
                      ('FIRE-PUMP', 'Fire pump set', 1, SERIAL_MAKE + [F('kind', 'Pumps', 'choice', ['Electric + diesel + jockey', 'Electric + jockey', 'Diesel + jockey'])]),
                      ('FIRE-SUPP', 'Kitchen / FM200 suppression', 6, SERIAL_MAKE + [F('agent', 'Agent', 'choice', ['Wet chemical', 'FM200', 'Novec', 'CO2'])]),
                      ('FIRE-EML', 'Emergency light / exit sign', 6, [F('duration_min', 'Battery duration (min)', 'number')]),
                      ('FIRE-HOSE', 'Hose reel / landing valve', 12, [F('floor', 'Floor')])],
        'amc': _amc('FIRE', 4, [('FIRE-CDREQ', 'Civil Defence compliance (quarterly + certificate)', 'limit', True, 4, 2)]),
        'issues': ['False alarm', 'Panel fault', 'Extinguisher refill', 'Sprinkler leak', 'Pump not starting', 'Civil Defence observation'],
        'maint': [('FALARM', 'Fire alarm fault', 'Fire technician', 'urgent', 1, 8), ('SPRINK', 'Sprinkler / pump', 'Fire technician', 'urgent', 1, 12),
                  ('EXT', 'Extinguisher refill', 'Fire technician', 'normal', 24, 72), ('EML', 'Emergency lights', 'Electrician', 'high', 8, 48)],
        'checklists': [('FIRE-QTR', 'Fire alarm quarterly test', ['Panel batteries', 'Detector test (sample)', 'Call points', 'Sounders and strobes', 'Interface to lifts / AHU',
                                                                  'Event log', 'Sign Civil Defence log book']),
                       ('FIRE-EXT', 'Extinguisher inspection', ['Pressure gauge', 'Seal and pin', 'Hose and nozzle', 'Label and tag', 'Mounted and accessible', 'Refill due date']),
                       ('FIRE-PUMP', 'Fire pump weekly run', ['Jockey cut-in / cut-out', 'Electric pump start', 'Diesel pump run 30 min', 'Fuel level', 'Battery', 'Pressure'])],
    },
    'cctv': {
        'label': 'CCTV & Security Systems', 'prefix': 'SEC',
        'crm': {'stages': _std_pipeline('Site survey / camera layout'), 'sources': [('SIRA', 'SIRA / police requirement')], 'lost': [('BRAND', 'Brand preference')]},
        'equipment': [('SEC-CAM', 'Camera', 6, SERIAL_MAKE + [F('type', 'Type', 'choice', ['Dome', 'Bullet', 'PTZ', 'Fisheye']), F('ip', 'IP address')]),
                      ('SEC-NVR', 'NVR / DVR', 6, SERIAL_MAKE + [F('channels', 'Channels', 'number'), F('storage_tb', 'Storage (TB)', 'number'), F('retention_days', 'Retention (days)', 'number')]),
                      ('SEC-ACCESS', 'Access control door', 6, SERIAL_MAKE + [F('reader', 'Reader', 'choice', ['Card', 'Fingerprint', 'Face', 'PIN'])]),
                      ('SEC-BARRIER', 'Barrier / gate', 3, SERIAL_MAKE),
                      ('SEC-ALARM', 'Intrusion alarm', 6, SERIAL_MAKE + [F('zones', 'Zones', 'number')]),
                      ('SEC-INTERCOM', 'Intercom / video door phone', 12, SERIAL_MAKE)],
        'amc': _amc('SEC', 2, [('SEC-SIRA', 'SIRA compliant (quarterly)', 'limit', True, 4, None)]),
        'issues': ['Camera offline', 'No recording', 'Door not opening', 'Barrier stuck', 'Video retrieval request', 'Password reset'],
        'maint': [('CAMDOWN', 'Camera / recording down', 'Security technician', 'high', 4, 24), ('ACCESS', 'Access control fault', 'Security technician', 'urgent', 2, 12),
                  ('BARRIER', 'Barrier / gate', 'Security technician', 'high', 4, 24)],
        'checklists': [('SEC-PPM', 'CCTV preventive check', ['Camera image and focus', 'Clean lenses', 'Night vision', 'Recording and retention days', 'HDD health', 'Time sync', 'Backup power'])],
    },
    'lift': {
        'label': 'Elevator & Escalator', 'prefix': 'LIFT',
        'crm': {'stages': _std_pipeline('Lift survey', 'Quotation', 'AMC / modernisation awarded'), 'sources': [('DEVELOPER', 'Developer / handover')], 'lost': [('OEM', 'Stayed with the OEM')]},
        'equipment': [('LIFT-PASS', 'Passenger lift', 1, SERIAL_MAKE + [F('capacity_kg', 'Capacity (kg)', 'number'), F('stops', 'Stops', 'number'), CERT_EXP]),
                      ('LIFT-GOODS', 'Goods / service lift', 1, SERIAL_MAKE + [F('capacity_kg', 'Capacity (kg)', 'number'), F('stops', 'Stops', 'number'), CERT_EXP]),
                      ('LIFT-ESC', 'Escalator / travelator', 1, SERIAL_MAKE + [F('rise_m', 'Rise (m)', 'number'), CERT_EXP]),
                      ('LIFT-DUMB', 'Dumbwaiter', 3, SERIAL_MAKE),
                      ('LIFT-HOIST', 'Car / platform hoist', 3, SERIAL_MAKE + [CERT_EXP])],
        'amc': _amc('LIFT', 12, [('LIFT-FULL', 'Full maintenance (monthly + 24/7 call-outs)', 'included', True, 12, None)]),
        'issues': ['Entrapment', 'Lift stopped', 'Door fault', 'Levelling / jerk', 'Noise', 'Third-party inspection'],
        'maint': [('ENTRAP', 'Passenger entrapment', 'Lift technician', 'urgent', 1, 2), ('LIFTDOWN', 'Lift out of service', 'Lift technician', 'urgent', 2, 12),
                  ('LIFTPPM', 'Monthly service', 'Lift technician', 'normal', 24, 72)],
        'checklists': [('LIFT-MONTH', 'Lift monthly service', ['Car and landing doors', 'Door safety edge / light curtain', 'Levelling', 'Alarm and intercom', 'Emergency light in car',
                                                               'Brake', 'Ropes and sheaves', 'Pit and machine room clean', 'Log book signed'])],
    },
    'medical': {
        'label': 'Medical Equipment', 'prefix': 'MED',
        'crm': {'stages': _std_pipeline('Demo / site assessment', 'Quotation', 'PO / service contract'), 'sources': [('TENDERHC', 'Hospital tender')], 'lost': [('REG', 'Registration / approval')]},
        'equipment': [('MED-IMAG', 'Imaging (X-ray, CT, MRI, ultrasound)', 6, SERIAL_MAKE + [F('modality', 'Modality', 'choice', ['X-ray', 'CT', 'MRI', 'Ultrasound', 'Mammography']), CERT_EXP]),
                      ('MED-MON', 'Patient monitor', 6, SERIAL_MAKE),
                      ('MED-VENT', 'Ventilator / anaesthesia', 6, SERIAL_MAKE),
                      ('MED-LAB', 'Laboratory analyser', 3, SERIAL_MAKE),
                      ('MED-DENT', 'Dental chair', 6, SERIAL_MAKE),
                      ('MED-STER', 'Autoclave / steriliser', 3, SERIAL_MAKE + [F('validation_due', 'Validation due', 'date')]),
                      ('MED-INF', 'Infusion / syringe pump', 12, SERIAL_MAKE)],
        'amc': _amc('MED', 2, [('MED-UPTIME', 'Uptime guarantee (95 %)', 'included', True, 4, None)]),
        'issues': ['Device error', 'Calibration due', 'Image quality', 'Accessory replacement', 'Electrical safety test'],
        'maint': [('BIOMED', 'Device breakdown', 'Biomedical engineer', 'urgent', 2, 24), ('CALIB', 'Calibration', 'Biomedical engineer', 'normal', 24, 120)],
        'checklists': [('MED-PPM', 'Medical device PPM', ['Visual inspection', 'Electrical safety test', 'Function test', 'Calibration', 'Alarms', 'Accessories', 'Label next due date'])],
    },
    'industrial': {
        'label': 'Industrial Machinery', 'prefix': 'IND',
        'crm': {'stages': _std_pipeline('Machine assessment'), 'sources': [('OEMREF', 'OEM referral')], 'lost': [('LEADTIME', 'Lead time')]},
        'equipment': [('IND-COMP', 'Air compressor', 3, SERIAL_MAKE + [F('kw', 'Motor (kW)', 'number'), F('run_hours', 'Running hours', 'number')]),
                      ('IND-FORK', 'Forklift', 3, SERIAL_MAKE + [F('capacity_t', 'Capacity (t)', 'number'), F('fuel', 'Fuel', 'choice', ['Diesel', 'Electric', 'LPG'])]),
                      ('IND-CNC', 'CNC / machine tool', 6, SERIAL_MAKE),
                      ('IND-BOILER', 'Boiler', 3, SERIAL_MAKE + [CERT_EXP]),
                      ('IND-CONV', 'Conveyor', 3, [F('length_m', 'Length (m)', 'number')]),
                      ('IND-GEN', 'Generator', 3, SERIAL_MAKE + [KVA, F('run_hours', 'Running hours', 'number')])],
        'amc': _amc('IND', 4, [('IND-HOURS', 'By running hours (service every 500 h)', 'limit', True, 0, None)]),
        'issues': ['Machine breakdown', 'Oil leak', 'Overheating', 'Abnormal noise', 'Service by hours'],
        'maint': [('BREAK', 'Machine breakdown', 'Mechanical technician', 'urgent', 2, 24), ('PPM', 'Service by hours', 'Mechanical technician', 'normal', 24, 72)],
        'checklists': [('IND-PPM', 'Machine PPM', ['Oil and filters', 'Belts and couplings', 'Bearings / grease', 'Leaks', 'Safety guards', 'Running hours recorded'])],
    },
    'solar': {
        'label': 'Solar Energy', 'prefix': 'SOL',
        'crm': {'stages': _std_pipeline('Roof survey / yield study', 'Proposal', 'Contract (EPC / O&M) signed'), 'sources': [('SHAMS', 'DEWA Shams / utility programme')],
                'lost': [('PAYBACK', 'Payback too long')]},
        'equipment': [('SOL-ARRAY', 'PV array', 3, [F('kwp', 'Capacity (kWp)', 'number'), F('panels', 'Panels', 'number')]),
                      ('SOL-INV', 'Inverter', 6, SERIAL_MAKE + [F('kw', 'Rating (kW)', 'number')]),
                      ('SOL-BATT', 'Battery storage', 6, SERIAL_MAKE + [F('kwh', 'Capacity (kWh)', 'number')]),
                      ('SOL-METER', 'Net meter', 12, [F('meter_no', 'Meter number')]),
                      ('SOL-HEATER', 'Solar water heater', 6, SERIAL_MAKE)],
        'amc': _amc('SOL', 4, [('SOL-OM', 'O&M with cleaning (monthly)', 'limit', True, 12, None)]),
        'issues': ['Low generation', 'Inverter fault', 'Panel cleaning', 'Damaged panel', 'Monitoring offline'],
        'maint': [('INVERTER', 'Inverter fault', 'Solar technician', 'high', 8, 48), ('CLEAN', 'Panel cleaning', 'Cleaner', 'normal', 48, 120)],
        'checklists': [('SOL-PPM', 'Solar O&M visit', ['Panel cleaning', 'Visual damage / hot spots', 'String voltages', 'Inverter alarms', 'Earthing', 'Generation vs expected'])],
    },
    'fm': {
        'label': 'Facility Management', 'prefix': 'FM',
        'crm': {'stages': _std_pipeline('Site survey / scope', 'Proposal', 'FM contract awarded'), 'sources': [('OWNERSASSOC', "Owners' association")], 'lost': [('SCOPE', 'Scope reduced')]},
        'equipment': [('FM-AC', 'AC unit', 3, SERIAL_MAKE + [CAP_TR]), ('FM-PUMP', 'Pump', 3, SERIAL_MAKE), ('FM-LIFT', 'Lift', 1, SERIAL_MAKE + [CERT_EXP]),
                      ('FM-FIRE', 'Fire system', 3, [CIVIL_DEF]), ('FM-GEN', 'Generator', 1, SERIAL_MAKE + [KVA]), ('FM-BMS', 'BMS controller', 6, SERIAL_MAKE)],
        'amc': _amc('FM', 12, [('FM-HARD', 'Hard services (MEP)', 'limit', True, 12, None), ('FM-SOFT', 'Soft services (cleaning, pest, landscaping)', 'none', True, 52, None)]),
        'issues': ['Reactive repair', 'Planned maintenance', 'Cleaning request', 'Pest sighting', 'Access / security'],
        'maint': [('REACT', 'Reactive repair', 'Multi-skilled technician', 'high', 4, 24), ('SOFT', 'Soft services request', 'Supervisor', 'normal', 24, 48)],
        'checklists': [('FM-WEEK', 'Building weekly round', ['Plant rooms', 'Lifts', 'Fire panel', 'Water tanks', 'Common lighting', 'Cleanliness', 'Pest signs', 'Car park'])],
    },
    'cleaning': {
        'label': 'Cleaning Services', 'prefix': 'CLN',
        'crm': {'stages': _std_pipeline('Site visit / area measurement', 'Proposal', 'Contract awarded'), 'sources': [('ONEOFF', 'One-off job enquiry')], 'lost': [('PRICE2', 'Price per head / hour')]},
        'equipment': [('CLN-SCRUB', 'Floor scrubber', 3, SERIAL_MAKE), ('CLN-VAC', 'Vacuum / wet-dry machine', 6, SERIAL_MAKE),
                      ('CLN-BMU', 'Facade / BMU access', 6, SERIAL_MAKE + [CERT_EXP]), ('CLN-AREA', 'Cleaning area / zone', 1, [F('sqm', 'Area (m²)', 'number'), F('frequency', 'Frequency')])],
        'amc': [('CLN-DAILY', 'Daily cleaning (manpower)', 'none', True, 260, None), ('CLN-DEEP', 'Deep cleaning (quarterly)', 'none', True, 4, None),
                ('CLN-FACADE', 'Facade cleaning', 'none', True, 4, None), ('CLN-ONCALL', 'On-call (per job)', 'none', True, 0, 0)],
        'issues': ['Missed cleaning', 'Spill / urgent cleaning', 'Deep cleaning request', 'Consumables refill', 'Complaint'],
        'maint': [('SPILL', 'Urgent cleaning', 'Cleaner', 'high', 1, 4), ('DEEP', 'Deep cleaning', 'Cleaning supervisor', 'normal', 48, 120)],
        'checklists': [('CLN-AUDIT', 'Cleaning quality audit', ['Floors', 'Washrooms', 'Glass and mirrors', 'Bins emptied', 'Consumables stocked', 'Pantry', 'Staff uniform and PPE'])],
    },
    'pest': {
        'label': 'Pest Control', 'prefix': 'PEST',
        'crm': {'stages': _std_pipeline('Inspection', 'Treatment proposal', 'Contract awarded'), 'sources': [('MUNICIPALITY', 'Municipality notice')], 'lost': [('ONEOFF2', 'One-off only')]},
        'equipment': [('PEST-BAIT', 'Bait station', 1, [F('station_no', 'Station number'), F('location', 'Location')]),
                      ('PEST-ILT', 'Insect light trap', 1, [F('location', 'Location')]),
                      ('PEST-AREA', 'Treated area', 1, [F('sqm', 'Area (m²)', 'number'), F('pest', 'Target pest', 'choice', ['Cockroach', 'Rodent', 'Bed bug', 'Termite', 'Flying insect', 'General'])])],
        'amc': [('PEST-MONTH', 'Monthly treatment', 'none', True, 12, 2), ('PEST-QTR', 'Quarterly treatment', 'none', True, 4, 1),
                ('PEST-TERM', 'Termite protection (yearly warranty)', 'included', True, 1, None), ('PEST-ONCALL', 'On-call (per job)', 'none', True, 0, 0)],
        'issues': ['Cockroaches', 'Rodents', 'Bed bugs', 'Termites', 'Flying insects', 'Re-treatment (warranty)'],
        'maint': [('PEST', 'Pest sighting', 'Pest control technician', 'high', 24, 72)],
        'checklists': [('PEST-VISIT', 'Treatment visit report', ['Areas inspected', 'Activity found', 'Chemical and dosage', 'Bait stations checked', 'Light traps cleaned',
                                                                 'Recommendations to client', 'Re-entry time told'])],
    },
    'telecom': {
        'label': 'Telecom & Networking', 'prefix': 'TEL',
        'crm': {'stages': _std_pipeline('Site survey / cabling plan'), 'sources': [('ISP', 'ISP / carrier referral')], 'lost': [('BRAND', 'Brand preference')]},
        'equipment': [('TEL-SW', 'Network switch', 6, SERIAL_MAKE + [F('ports', 'Ports', 'number'), F('ip', 'IP address')]),
                      ('TEL-AP', 'Wi-Fi access point', 12, SERIAL_MAKE + [F('ip', 'IP address')]),
                      ('TEL-RACK', 'Rack / patch panel', 12, [F('location', 'Room')]),
                      ('TEL-PBX', 'PBX / IP telephony', 6, SERIAL_MAKE + [F('extensions', 'Extensions', 'number')]),
                      ('TEL-FIBRE', 'Fibre link', 12, [F('cores', 'Cores', 'number')]),
                      ('TEL-TOWER', 'Antenna / tower', 6, [F('height_m', 'Height (m)', 'number')])],
        'amc': _amc('TEL', 2, [('TEL-NOC', '24/7 monitoring (NOC)', 'limit', True, 4, None)]),
        'issues': ['No internet', 'Slow network', 'Wi-Fi coverage', 'Phone extension down', 'New point / cabling'],
        'maint': [('NETDOWN', 'Network down', 'Network engineer', 'urgent', 1, 8), ('CABLING', 'New cabling point', 'Cabling technician', 'normal', 24, 72)],
        'checklists': [('TEL-PPM', 'Network preventive check', ['Switch logs and uptime', 'Firmware', 'Rack temperature', 'UPS', 'Cable labels', 'Wi-Fi survey sample', 'Backup of configs'])],
    },
    'kitchen': {
        'label': 'Commercial Kitchen Equipment', 'prefix': 'KIT',
        'crm': {'stages': _std_pipeline('Kitchen survey / layout'), 'sources': [('CONSULTFB', 'F&B consultant')], 'lost': [('BRAND', 'Brand preference')]},
        'equipment': [('KIT-COLD', 'Cold room / walk-in', 3, SERIAL_MAKE + [REFRIG, F('temp_set', 'Set temperature (°C)', 'number')]),
                      ('KIT-FRIDGE', 'Reach-in fridge / freezer', 3, SERIAL_MAKE + [REFRIG]),
                      ('KIT-OVEN', 'Oven / combi oven', 6, SERIAL_MAKE + [F('fuel', 'Fuel', 'choice', ['Gas', 'Electric'])]),
                      ('KIT-FRYER', 'Fryer / range', 6, SERIAL_MAKE),
                      ('KIT-DISH', 'Dishwasher', 3, SERIAL_MAKE),
                      ('KIT-HOOD', 'Exhaust hood / duct', 3, [F('last_degrease', 'Last degreasing', 'date')]),
                      ('KIT-ICE', 'Ice machine', 3, SERIAL_MAKE)],
        'amc': _amc('KIT', 4),
        'issues': ['Not cooling', 'Oven not heating', 'Gas smell', 'Dishwasher fault', 'Hood cleaning'],
        'maint': [('COLD', 'Cold room / fridge fault', 'Refrigeration technician', 'urgent', 2, 12), ('COOK', 'Cooking equipment', 'Kitchen technician', 'high', 4, 24),
                  ('GAS', 'Gas leak', 'Gas technician', 'urgent', 1, 4)],
        'checklists': [('KIT-PPM', 'Kitchen equipment PPM', ['Fridge / freezer temperatures', 'Door gaskets', 'Condenser cleaning', 'Gas connections and leak test', 'Burners and thermostats',
                                                             'Dishwasher wash / rinse temperature', 'Hood filters'])],
    },
    'water': {
        'label': 'Water Treatment', 'prefix': 'WTR',
        'crm': {'stages': _std_pipeline('Water test / site survey'), 'sources': [('MUNICIPALITY', 'Municipality / health inspection')], 'lost': [('CHEMICAL', 'Chemical price')]},
        'equipment': [('WTR-RO', 'RO plant', 3, SERIAL_MAKE + [F('capacity_gpd', 'Capacity (GPD)', 'number'), F('membranes', 'Membranes', 'number')]),
                      ('WTR-SOFT', 'Water softener', 3, SERIAL_MAKE),
                      ('WTR-FILTER', 'Filter / UV unit', 3, SERIAL_MAKE),
                      ('WTR-STP', 'Sewage treatment plant (STP)', 1, SERIAL_MAKE + [F('capacity_m3', 'Capacity (m³/day)', 'number')]),
                      ('WTR-DOSING', 'Chemical dosing', 1, SERIAL_MAKE),
                      ('WTR-TANK', 'Water tank', 6, [F('capacity_l', 'Capacity (litres)', 'number')])],
        'amc': _amc('WTR', 12, [('WTR-CHEM', 'With chemicals and lab tests', 'limit', True, 12, None)]),
        'issues': ['Low water output', 'Bad taste / smell', 'High TDS', 'Membrane change', 'Lab test request'],
        'maint': [('ROFAULT', 'Plant fault', 'Water treatment technician', 'high', 4, 24), ('TANK', 'Tank cleaning', 'Cleaner', 'normal', 48, 120)],
        'checklists': [('WTR-VISIT', 'Water treatment visit', ['Feed / product TDS', 'Pressures', 'Flow rates', 'Chemical levels', 'Filter change', 'UV lamp hours', 'Sample for lab'])],
    },
    'pool': {
        'label': 'Swimming Pool & Landscaping', 'prefix': 'POOL',
        'crm': {'stages': _std_pipeline('Site visit'), 'sources': [('VILLAS', 'Villa community')], 'lost': [('SEASON', 'Seasonal only')]},
        'equipment': [('POOL-POOL', 'Swimming pool', 1, [F('volume_m3', 'Volume (m³)', 'number'), F('type', 'Type', 'choice', ['Chlorine', 'Salt', 'Ozone'])]),
                      ('POOL-PUMP', 'Pool pump / filter', 3, SERIAL_MAKE),
                      ('POOL-HEAT', 'Pool heater / chiller', 6, SERIAL_MAKE),
                      ('POOL-IRR', 'Irrigation system', 3, [F('zones', 'Zones', 'number')]),
                      ('POOL-LAWN', 'Lawn / garden area', 1, [F('sqm', 'Area (m²)', 'number')])],
        'amc': [('POOL-WEEK', 'Weekly pool maintenance', 'none', True, 52, 2), ('POOL-GARDEN', 'Garden maintenance (weekly)', 'none', True, 52, None),
                ('POOL-COMP', 'Pool comprehensive (with chemicals and parts)', 'included', True, 52, None), ('POOL-ONCALL', 'On-call (per visit)', 'none', True, 0, 0)],
        'issues': ['Green / cloudy water', 'Pump fault', 'Leak', 'Irrigation leak', 'Garden request'],
        'maint': [('POOLWATER', 'Pool water quality', 'Pool technician', 'high', 8, 24), ('IRR', 'Irrigation fault', 'Gardener', 'normal', 24, 72)],
        'checklists': [('POOL-WEEK', 'Pool weekly visit', ['pH', 'Free chlorine', 'Skim and vacuum', 'Backwash filter', 'Pump basket', 'Water level', 'Safety signs'])],
    },
    'fitout': {
        'label': 'Construction & Fit-out', 'prefix': 'FIT',
        'crm': {'stages': _std_pipeline('Site visit / design brief', 'BOQ / quotation', 'Contract awarded'), 'sources': [('INTERIOR', 'Interior designer')], 'lost': [('APPROVALS', 'Authority approvals')]},
        'equipment': [('FIT-UNIT', 'Fitted-out unit / office', 12, [F('sqm', 'Area (m²)', 'number'), F('handover', 'Handover date', 'date')]),
                      ('FIT-JOIN', 'Joinery / furniture', 12, []), ('FIT-CEIL', 'Ceiling / partitions', 12, []),
                      ('FIT-FLOOR', 'Flooring', 12, [F('material', 'Material')])],
        'amc': [('FIT-DLP', 'Defects liability period (12 months)', 'included', True, 2, None), ('FIT-MAINT', 'Post-handover maintenance', 'limit', True, 4, None),
                ('FIT-ONCALL', 'On-call (per job)', 'none', True, 0, 0)],
        'issues': ['Snag', 'Defect in DLP', 'Variation request', 'Authority inspection'],
        'maint': [('SNAG', 'Snag / defect', 'Site supervisor', 'normal', 48, 168), ('CARP', 'Joinery repair', 'Carpenter', 'low', 48, 168)],
        'checklists': [('FIT-HANDOVER', 'Handover snag list', ['Paint finish', 'Ceiling', 'Flooring', 'Joinery', 'Doors and ironmongery', 'Electrical points', 'AC diffusers', 'Cleaning'])],
    },
}


def crm_pipeline(code):
    d = INDUSTRIES[code]
    key = d['prefix']
    return ({'code': key, 'name': f"{d['label']} – service sales", 'kind': 'sales', 'order': 1},
            [{'pipeline': f'@pipeline:{key}', 'code': f'{key}_{c}', 'name': n, 'order': i + 1, 'probability': p, 'is_won': c == 'WON', 'is_lost': c == 'LOST'}
             for i, (c, n, p) in enumerate(d['crm']['stages'])])


# company master industry (Settings › Company › Industry) → service pack, for CRM and Maintenance (IT, PM and CON keep their own packs)
FROM_COMPANY = {'HVAC': 'hvac', 'MEP': 'mep', 'FIRE': 'fire', 'CCTV': 'cctv', 'LIFT': 'lift', 'MEDEQ': 'medical', 'INDM': 'industrial', 'SOLAR': 'solar',
                'FM': 'fm', 'CLEAN': 'cleaning', 'PEST': 'pest', 'TEL': 'telecom', 'KITCH': 'kitchen', 'WATER': 'water', 'POOL': 'pool', 'FITOUT': 'fitout'}
