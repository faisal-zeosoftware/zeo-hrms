"""v1.67.27 (HSE scope): 15 more industry packs – mining, warehousing and logistics, hotels and restaurants, facilities
management, agriculture, food processing, marine and ports, aviation, telecom, electrical and utilities, waste, cleaning
and pest control, laboratories and pharmaceuticals, shipyards, data centres.

Same shape as the other packs (terms, incident categories, permit types with their checklists, inspection / drill /
toolbox forms, hazard library) plus their extras (competencies, the competencies each permit needs, KPIs, an audit).
Starting values only: an HSE manager reviews them for the company's real hazards, procedures and the legal requirements
that apply before they can be used.
"""
from ._util import Q, steps

AA, PI, SM, HM, GT = 'area_authority', 'permit_issuer', 'site_manager', 'hse_manager', 'gas_tester'


def ptw(code, name, color, desc, checklist, approvals, hours=12, **kw):
    d = {'code': code, 'name': name, 'color': color, 'max_hours': hours, 'description': desc, 'checklist': checklist, 'approval_steps': steps(*approvals)}
    d.update(kw)
    return d


def form(kind, code, name, questions, frequency=''):
    return {'kind': kind, 'code': code, 'name': name, 'frequency': frequency, 'questions': questions}


def tbt(code, name):
    return {'kind': 'toolbox_talk', 'code': code, 'name': name, 'questions': []}


def audit(code, name, questions, frequency='Quarterly'):
    return {'kind': 'audit', 'code': code, 'name': name, 'frequency': frequency, 'questions': questions}


PACKS = {
    # ------------------------------------------------------------------ 04 mining, quarrying and cement
    'mining': {
        'terms': {'site': 'Mine / quarry', 'area': 'Pit / bench / zone', 'supervisor': 'Shift foreman'},
        'incident_categories': [('MIN-GF', 'Ground fall / slope failure', 'injury', False), ('MIN-BL', 'Blasting incident / flyrock', 'injury', False),
                                ('MIN-HV', 'Heavy vehicle / haul road incident', 'property', False), ('MIN-CV', 'Conveyor entanglement', 'injury', False),
                                ('MIN-DU', 'Dust / silica overexposure', 'occupational', True)],
        'permit_types': [
            ptw('BLAST', 'Blasting authorisation', '#b91c1c', 'Charging and firing: exclusion zone, sentries, misfire procedure, licensed shot-firer.',
                'BLAST-PTW', ((SM, 'Mine manager'), (PI, 'Licensed shot-firer')), hours=8),
            ptw('CONV', 'Conveyor / crusher maintenance', '#2563eb', 'Work on conveyors and crushers: isolation, lock-out at the pull-wire and drive.',
                'CONV-PTW', ((AA, 'Plant supervisor'), (PI, 'Maintenance supervisor')), requires_isolation=True),
        ],
        'forms': [
            form('permit_checklist', 'BLAST-PTW', 'Blasting checklist', [
                Q('Zone', 'Exclusion zone marked and sentries posted', critical=True), Q('People', 'Licensed shot-firer in charge', critical=True),
                Q('Warning', 'Warning signals given and area cleared', critical=True), Q('Misfire', 'Misfire procedure briefed'),
                Q('Storage', 'Explosives transported and stored per licence', critical=True)]),
            form('permit_checklist', 'CONV-PTW', 'Conveyor / crusher isolation checklist', [
                Q('Isolation', 'Drive isolated, locked and tested for zero energy', critical=True), Q('Guards', 'Guards removed only under the permit'),
                Q('Material', 'Belt cleared and blocked against movement', critical=True)]),
            form('inspection', 'INS-HAUL', 'Haul road and bench inspection', [
                Q('Road', 'Berms at least half wheel height', critical=True), Q('Road', 'Road width and gradient within the design'),
                Q('Dust', 'Dust suppression working'), Q('Bench', 'No overhangs or loose rock on the face', critical=True), Q('Signs', 'Speed and right-of-way signs in place')], 'Each shift'),
            form('inspection', 'INS-PRESTART-HV', 'Heavy vehicle pre-start check', [
                Q('Brakes', 'Service and park brakes tested', critical=True), Q('Visibility', 'Mirrors, cameras and horn working'),
                Q('Fire', 'Fire suppression system armed'), Q('Tyres', 'Tyres and rims undamaged')], 'Each shift'),
            tbt('TBT-SILICA', 'Silica dust and how to control it'), tbt('TBT-HAULROAD', 'Haul road rules and blind spots'),
        ],
        'hazards': [
            ('Ground', 'Working near faces and benches', 'Rock fall / slope failure', 'Fatality', 'Geotechnical inspections, scaling, exclusion zones', 2, 5),
            ('Explosives', 'Blasting', 'Flyrock / premature detonation', 'Fatality', 'Licensed shot-firer, exclusion zone, misfire procedure', 2, 5),
            ('Vehicles', 'Haul trucks and light vehicles', 'Collision / roll-over', 'Fatality', 'Segregation, berms, proximity detection, pre-starts', 3, 5),
            ('Dust', 'Crushing, drilling, transfer points', 'Respirable crystalline silica', 'Silicosis', 'Water sprays, enclosures, RPE, monitoring', 4, 4),
            ('Machinery', 'Conveyors', 'Entanglement', 'Amputation / fatality', 'Guards, pull-wires, LOTO', 2, 5),
            ('Noise', 'Crushers and drills', 'Noise', 'Hearing loss', 'Enclosures, hearing protection, audiometry', 4, 3),
        ],
    },
    # ------------------------------------------------------------------ 05 warehousing, logistics and transport
    'logistics': {
        'terms': {'site': 'Warehouse / depot', 'area': 'Zone / aisle / dock', 'supervisor': 'Shift supervisor'},
        'incident_categories': [('LOG-FK', 'Forklift / MHE incident', 'property', False), ('LOG-RACK', 'Racking damage / collapse', 'property', False),
                                ('LOG-RTA', 'Road traffic accident', 'injury', False), ('LOG-DG', 'Dangerous goods release', 'environment', False),
                                ('LOG-LOAD', 'Load shift / fall from vehicle', 'injury', False)],
        'permit_types': [
            ptw('DG-HAND', 'Dangerous goods handling', '#dc2626', 'Receiving, storing or loading classified dangerous goods.', 'DG-PTW',
                ((AA, 'Warehouse manager'), (PI, 'DG safety adviser'))),
            ptw('RACK-WORK', 'Racking repair / work at height in racking', '#ea580c', 'Repair of damaged racking or work at height in aisles.', 'RACK-PTW',
                ((AA, 'Warehouse manager'), (PI, 'HSE officer'))),
        ],
        'forms': [
            form('permit_checklist', 'DG-PTW', 'Dangerous goods checklist', [
                Q('Documents', 'DG declaration and SDS available', critical=True), Q('Segregation', 'Segregation table followed', critical=True),
                Q('Spill', 'Spill kit at the work area'), Q('People', 'Handlers trained in DG')]),
            form('permit_checklist', 'RACK-PTW', 'Racking work checklist', [
                Q('Area', 'Aisle closed and barriered', critical=True), Q('Load', 'Bays unloaded before repair', critical=True),
                Q('Height', 'MEWP / harness in place for work at height', critical=True)]),
            form('inspection', 'INS-RACK', 'Racking inspection (SEMA style)', [
                Q('Uprights', 'No uprights damaged beyond tolerance', critical=True), Q('Beams', 'Beam locks fitted on every beam', critical=True),
                Q('Loads', 'Load notices displayed and not exceeded'), Q('Base', 'Base plates fixed, column guards fitted')], 'Weekly / yearly expert'),
            form('inspection', 'INS-VEH-DAILY', 'Daily vehicle check', [
                Q('Brakes', 'Brakes, lights and indicators working', critical=True), Q('Tyres', 'Tyres in good condition'),
                Q('Load', 'Load secured with straps / bars', critical=True), Q('Documents', 'Licence and registration valid')], 'Daily'),
            form('inspection', 'INS-DOCK', 'Loading dock inspection', [
                Q('Restraint', 'Vehicle restraint / wheel chocks used', critical=True), Q('Leveller', 'Dock leveller working'),
                Q('Traffic', 'Pedestrian routes separated from MHE'), Q('Lighting', 'Dock lighting adequate')], 'Weekly'),
            tbt('TBT-FORKLIFT', 'Forklift and pedestrian safety'), tbt('TBT-FATIGUE', 'Driver fatigue and journey planning'),
        ],
        'hazards': [
            ('MHE', 'Forklift operation', 'Pedestrian struck / tip-over', 'Fatality', 'Segregation, licensed drivers, speed limits, seat belts', 3, 5),
            ('Storage', 'High-bay racking', 'Collapse / falling load', 'Serious injury', 'Racking inspections, load notices, column guards', 2, 5),
            ('Driving', 'Deliveries', 'Road traffic accident / fatigue', 'Fatality', 'Journey management, rest breaks, telematics', 3, 5),
            ('Manual handling', 'Picking and loading', 'Back injury', 'Musculoskeletal disorder', 'Lifting aids, team lifts, training', 4, 2),
            ('Dangerous goods', 'DG storage', 'Release / fire', 'Injury, environmental damage', 'Segregation, SDS, spill kits', 2, 4),
            ('Cold store', 'Cold room work', 'Cold stress / entrapment', 'Hypothermia', 'Thermal PPE, door release, time limits', 2, 4),
        ],
    },
    # ------------------------------------------------------------------ 07 hotels and restaurants
    'hospitality': {
        'terms': {'site': 'Property / outlet', 'area': 'Department / floor', 'supervisor': 'Duty manager'},
        'incident_categories': [('HOS-GUEST', 'Guest accident', 'injury', False), ('HOS-BURN', 'Burn / scald', 'injury', False),
                                ('HOS-FOOD', 'Food safety complaint / illness', 'other', False), ('HOS-POOL', 'Pool incident', 'injury', False),
                                ('HOS-SLIP', 'Slip, trip or fall', 'injury', False)],
        'permit_types': [
            ptw('HOS-HOT', 'Hot work in the property', '#dc2626', 'Welding, cutting, grinding in hotel areas: fire watch, detector isolation.', 'HOS-HOT-PTW',
                ((AA, 'Chief engineer'), (PI, 'Security / fire manager')), hours=8),
        ],
        'forms': [
            form('permit_checklist', 'HOS-HOT-PTW', 'Hot work checklist (hotel)', [
                Q('Fire', 'Detectors isolated only for the area and time, recorded', critical=True), Q('Fire', 'Fire watch for 60 minutes after', critical=True),
                Q('Guests', 'Guests kept away and informed')]),
            form('inspection', 'INS-KITCHEN', 'Kitchen safety and hygiene inspection', [
                Q('Fire', 'Hood suppression system in date, filters clean', critical=True), Q('Food', 'Fridges and freezers at temperature (log)', critical=True),
                Q('Floors', 'Floors dry, anti-slip mats in place'), Q('Gas', 'Gas leak detection and shut-off working', critical=True),
                Q('Chemicals', 'Cleaning chemicals labelled and stored away from food')], 'Daily'),
            form('inspection', 'INS-POOL', 'Swimming pool safety check', [
                Q('Water', 'Chlorine and pH within range (log)', critical=True), Q('Rescue', 'Lifeguard or rescue equipment in place', critical=True),
                Q('Signs', 'Depth markings and rules displayed'), Q('Plant', 'Plant room locked, chemicals segregated')], 'Each day'),
            form('inspection', 'INS-ROOM', 'Guest room safety check', [
                Q('Fire', 'Evacuation plan on the door, detector working'), Q('Electrical', 'No damaged sockets or cables'),
                Q('Balcony', 'Balcony door restrictor and rail secure', critical=True)], 'Monthly sample'),
            tbt('TBT-KNIFE', 'Knife and burn safety in the kitchen'), tbt('TBT-CHEM-HK', 'Housekeeping chemicals'),
        ],
        'hazards': [
            ('Kitchen', 'Cooking and frying', 'Burns / scalds / fat fire', 'Serious injury', 'Suppression system, training, PPE', 3, 3),
            ('Kitchen', 'Knife work', 'Cuts', 'Injury', 'Cut-resistant gloves, knife training', 4, 2),
            ('Housekeeping', 'Room cleaning', 'Chemicals / manual handling', 'Dermatitis, strain', 'Dilution systems, trolleys, training', 3, 2),
            ('Pool', 'Swimming pool', 'Drowning', 'Fatality', 'Lifeguard / rescue kit, signage, depth markings', 2, 5),
            ('Guests', 'Public areas', 'Slip, trip or fall', 'Injury, claim', 'Cleaning signs, mats, lighting', 3, 3),
            ('Food', 'Food storage and service', 'Food poisoning', 'Illness, outbreak', 'HACCP, temperature logs, training', 2, 4),
        ],
    },
    # ------------------------------------------------------------------ 08 facilities management
    'facilities': {
        'terms': {'site': 'Building / property', 'area': 'Floor / plant room', 'supervisor': 'FM supervisor', 'contractor': 'Service provider'},
        'incident_categories': [('FM-LIFT', 'Lift / escalator incident', 'injury', False), ('FM-ELEC', 'Electrical incident', 'injury', False),
                                ('FM-WATER', 'Water leak / flood', 'property', False), ('FM-FALL', 'Fall from height', 'injury', False)],
        'permit_types': [
            ptw('FM-ELEC', 'Electrical maintenance (isolation)', '#2563eb', 'Work on panels, DBs and building services with LOTO.', 'FM-ELEC-PTW',
                ((AA, 'FM manager'), (PI, 'Authorised electrical person')), requires_isolation=True),
            ptw('FM-TANK', 'Water tank / plant room confined space', '#7c3aed', 'Entry into water tanks, sumps and pits.', 'FM-TANK-PTW',
                ((AA, 'FM manager'), (GT, 'Gas tester'), (PI, 'HSE officer')), requires_gas_test=True, gas_retest_hours=2,
                gas_limits={'o2_min': 19.5, 'o2_max': 23.5, 'lel_max': 10, 'h2s_max': 5, 'co_max': 25}),
            ptw('FM-ROOF', 'Roof and facade work (height)', '#ea580c', 'Roof access, BMU, rope access, facade cleaning.', 'FM-ROOF-PTW',
                ((AA, 'FM manager'), (PI, 'HSE officer'))),
        ],
        'forms': [
            form('permit_checklist', 'FM-ELEC-PTW', 'Electrical isolation checklist', [
                Q('Isolation', 'Isolated, locked, tagged and proved dead', critical=True), Q('People', 'Authorised electrical person', critical=True),
                Q('Tenants', 'Tenants informed of the shutdown')]),
            form('permit_checklist', 'FM-TANK-PTW', 'Tank entry checklist', [
                Q('Gas', 'Atmosphere tested and within limits', critical=True), Q('Rescue', 'Rescue plan, tripod and standby person', critical=True),
                Q('Isolation', 'Inlet and pumps isolated', critical=True)]),
            form('permit_checklist', 'FM-ROOF-PTW', 'Roof / facade checklist', [
                Q('Anchors', 'Anchor points certified', critical=True), Q('Edge', 'Edge protection or fall arrest in use', critical=True),
                Q('Weather', 'Wind speed within the limit')]),
            form('inspection', 'INS-PLANTROOM', 'Plant room inspection', [
                Q('Access', 'Plant room locked, access controlled'), Q('Leaks', 'No water, oil or refrigerant leaks', critical=True),
                Q('Fire', 'Extinguishers and detectors in date'), Q('Housekeeping', 'No storage in plant rooms')], 'Monthly'),
            form('inspection', 'INS-FIRE-SYS', 'Fire system inspection', [
                Q('Panel', 'Fire alarm panel normal, no faults', critical=True), Q('Pumps', 'Fire pumps run test done', critical=True),
                Q('Exits', 'Exit doors and routes clear'), Q('Lights', 'Emergency lights tested')], 'Weekly / monthly'),
            tbt('TBT-LOTO-FM', 'Isolation before maintenance'), tbt('TBT-LADDER', 'Safe use of ladders'),
        ],
        'hazards': [
            ('Electrical', 'Maintenance on live systems', 'Electric shock / arc flash', 'Fatality', 'LOTO, authorised persons, insulated tools', 2, 5),
            ('Height', 'Roof and ceiling work', 'Fall from height', 'Fatality', 'MEWPs, harness, edge protection, permits', 3, 5),
            ('Confined space', 'Water tanks and pits', 'Oxygen deficiency', 'Fatality', 'Permit, gas test, rescue plan', 2, 5),
            ('Lifts', 'Lift maintenance', 'Crush / fall in shaft', 'Fatality', 'Lift company only, LOTO, shaft protection', 2, 5),
            ('Biological', 'Cooling towers / water systems', 'Legionella', 'Illness', 'Water treatment, temperature control, sampling', 2, 4),
            ('Contractors', 'Multiple service providers', 'Uncontrolled work', 'Injury', 'Permits, induction, supervision', 3, 3),
        ],
    },
    # ------------------------------------------------------------------ 10 agriculture
    'agriculture': {
        'terms': {'site': 'Farm', 'area': 'Field / block / shed', 'supervisor': 'Farm supervisor'},
        'incident_categories': [('AGR-MACH', 'Tractor / machinery incident', 'injury', False), ('AGR-PEST', 'Pesticide exposure', 'occupational', True),
                                ('AGR-ANIM', 'Animal-related injury', 'injury', False), ('AGR-HEAT', 'Heat illness', 'occupational', True)],
        'permit_types': [
            ptw('PEST-APP', 'Pesticide application', '#16a34a', 'Spraying of registered pesticides: re-entry interval, PPE, drift control.', 'PEST-APP-PTW',
                ((AA, 'Farm manager'), (PI, 'HSE officer'))),
        ],
        'forms': [
            form('permit_checklist', 'PEST-APP-PTW', 'Pesticide application checklist', [
                Q('Product', 'Registered product, SDS and label read', critical=True), Q('PPE', 'Respirator, gloves and coveralls worn', critical=True),
                Q('Weather', 'Wind speed suitable, no drift to neighbours'), Q('Re-entry', 'Re-entry interval posted', critical=True)]),
            form('inspection', 'INS-TRACTOR', 'Tractor and implement check', [
                Q('ROPS', 'Roll-over protection and seat belt fitted', critical=True), Q('PTO', 'PTO guard in place', critical=True),
                Q('Brakes', 'Brakes and lights working')], 'Before use'),
            form('inspection', 'INS-CHEMSTORE-AGR', 'Agrochemical store inspection', [
                Q('Store', 'Store locked, ventilated, bunded', critical=True), Q('Stock', 'Expired products separated for disposal'),
                Q('Emergency', 'Wash facilities and spill kit available')], 'Monthly'),
            tbt('TBT-HEAT-AGR', 'Heat stress in the field'), tbt('TBT-PESTICIDE', 'Safe pesticide handling'),
        ],
        'hazards': [
            ('Machinery', 'Tractors and implements', 'Roll-over / entanglement', 'Fatality', 'ROPS, seat belts, PTO guards, training', 2, 5),
            ('Chemical', 'Pesticide spraying', 'Poisoning', 'Acute / chronic illness', 'PPE, licensed products, re-entry intervals', 3, 4),
            ('Heat', 'Field work in summer', 'Heat stroke', 'Serious illness', 'Midday break, water, shade, acclimatisation', 4, 4),
            ('Animals', 'Livestock handling', 'Kick / crush', 'Injury', 'Handling facilities, training, two-person work', 3, 3),
            ('Biological', 'Animal waste, soil', 'Zoonotic infection', 'Illness', 'Hygiene, gloves, vaccination where advised', 2, 3),
        ],
    },
    # ------------------------------------------------------------------ 11 food processing
    'food': {
        'terms': {'site': 'Plant', 'area': 'Production line / room', 'supervisor': 'Line supervisor'},
        'incident_categories': [('FD-CONT', 'Product contamination', 'other', False), ('FD-COLD', 'Cold store incident', 'injury', False),
                                ('FD-MACH', 'Machine injury (slicer, mixer)', 'injury', False), ('FD-AMM', 'Ammonia / refrigerant release', 'environment', False)],
        'permit_types': [
            ptw('FD-CIP', 'Hygiene shutdown / CIP chemicals', '#0891b2', 'Cleaning-in-place and deep cleaning with caustic or acid chemicals.', 'FD-CIP-PTW',
                ((AA, 'Production manager'), (PI, 'QA / HSE')), requires_isolation=True),
            ptw('FD-AMM', 'Ammonia refrigeration work', '#7c3aed', 'Work on ammonia plant: isolation, detection, emergency response.', 'FD-AMM-PTW',
                ((AA, 'Engineering manager'), (GT, 'Gas tester'), (PI, 'HSE officer')), requires_gas_test=True, requires_isolation=True),
        ],
        'forms': [
            form('permit_checklist', 'FD-CIP-PTW', 'CIP / deep clean checklist', [
                Q('Isolation', 'Machine isolated before cleaning', critical=True), Q('Chemicals', 'Face shield, gloves and apron worn', critical=True),
                Q('Product', 'Line cleared of product and packaging')]),
            form('permit_checklist', 'FD-AMM-PTW', 'Ammonia work checklist', [
                Q('Detection', 'Ammonia detectors working', critical=True), Q('PPE', 'Escape sets and full-face respirators available', critical=True),
                Q('Isolation', 'Section isolated and pumped out', critical=True)]),
            form('inspection', 'INS-HYGIENE', 'Production hygiene and safety inspection', [
                Q('Hygiene', 'Hand wash and hair nets enforced', critical=True), Q('Foreign body', 'Glass and hard plastic register checked'),
                Q('Guards', 'Guards on slicers, mixers and conveyors', critical=True), Q('Floors', 'Floors dry and drains clear')], 'Daily'),
            form('inspection', 'INS-COLDSTORE', 'Cold store inspection', [
                Q('Door', 'Inside release and alarm working', critical=True), Q('Temperature', 'Temperature log in range'),
                Q('Racking', 'Racking and stacking safe'), Q('Ice', 'No ice build-up on floors')], 'Weekly'),
            tbt('TBT-HACCP', 'Food safety hazards and our CCPs'), tbt('TBT-AMMONIA', 'Ammonia leak response'),
        ],
        'hazards': [
            ('Machinery', 'Slicers, mixers, fillers', 'Amputation / entanglement', 'Serious injury', 'Guards, interlocks, LOTO for cleaning', 3, 4),
            ('Chemical', 'CIP and sanitation', 'Caustic / acid burns', 'Serious injury', 'Dosing systems, face shields, eye wash', 3, 4),
            ('Refrigerant', 'Ammonia plant', 'Toxic release', 'Fatality', 'Detection, alarms, emergency plan, trained staff', 2, 5),
            ('Food safety', 'Production', 'Physical / microbial contamination', 'Recall, illness', 'HACCP, metal detection, hygiene', 3, 4),
            ('Cold', 'Cold store work', 'Cold stress / slips', 'Injury', 'Thermal clothing, time limits, anti-slip', 3, 2),
        ],
    },
    # ------------------------------------------------------------------ 12 marine, shipping and ports
    'marine': {
        'terms': {'site': 'Vessel / terminal', 'area': 'Deck / hold / berth', 'supervisor': 'Officer of the watch'},
        'incident_categories': [('MAR-MOB', 'Man overboard', 'injury', False), ('MAR-MOOR', 'Mooring line incident', 'injury', False),
                                ('MAR-SPILL', 'Oil spill / pollution', 'environment', False), ('MAR-CARGO', 'Cargo handling incident', 'property', False)],
        'permit_types': [
            ptw('MAR-ENCL', 'Enclosed space entry (vessel)', '#7c3aed', 'Tanks, holds, void spaces: ventilation, gas test, rescue team.', 'MAR-ENCL-PTW',
                ((AA, 'Master / chief officer'), (GT, 'Gas tester')), requires_gas_test=True, gas_retest_hours=1,
                gas_limits={'o2_min': 20.8, 'o2_max': 21.0, 'lel_max': 1, 'h2s_max': 1, 'co_max': 25}),
            ptw('MAR-HOT', 'Hot work on board', '#dc2626', 'Hot work on deck or in the engine room.', 'MAR-HOT-PTW',
                ((AA, 'Chief engineer'), (PI, 'Master')), requires_gas_test=True),
        ],
        'forms': [
            form('permit_checklist', 'MAR-ENCL-PTW', 'Enclosed space entry checklist', [
                Q('Atmosphere', 'Ventilated and tested: O₂, flammable, toxic', critical=True), Q('Rescue', 'Rescue team and equipment at the entrance', critical=True),
                Q('Communication', 'Communication with the bridge agreed')]),
            form('permit_checklist', 'MAR-HOT-PTW', 'Hot work on board checklist', [
                Q('Fire', 'Fire watch on both sides of bulkheads', critical=True), Q('Tanks', 'Adjacent tanks gas-free', critical=True)]),
            form('inspection', 'INS-DECK', 'Deck and mooring inspection', [
                Q('Mooring', 'Mooring lines and snap-back zones marked', critical=True), Q('Access', 'Gangway safe with net'),
                Q('LSA', 'Lifebuoys and life jackets in place', critical=True), Q('Housekeeping', 'Deck clear of trip hazards')], 'Daily'),
            form('drill', 'DRILL-MOB', 'Man overboard drill', [
                Q('Response', 'Alarm and lifebuoy within the target time', critical=True), Q('Response', 'Rescue boat launched safely'),
                Q('Response', 'Time to recover (minutes)', type='number')], 'Quarterly'),
            tbt('TBT-SNAPBACK', 'Snap-back zones in mooring'), tbt('TBT-ENCLOSED', 'Enclosed spaces kill – never enter without a permit'),
        ],
        'hazards': [
            ('Mooring', 'Mooring operations', 'Line snap-back', 'Fatality', 'Snap-back zones, line inspection, trained crew', 2, 5),
            ('Enclosed space', 'Tank / hold entry', 'Oxygen deficiency / toxic gas', 'Fatality', 'Permit, gas test, rescue team', 2, 5),
            ('Water', 'Work over the side', 'Man overboard', 'Drowning', 'Life jackets, harness, rescue boat', 2, 5),
            ('Cargo', 'Cargo handling', 'Struck by load', 'Serious injury', 'Banksman, exclusion zones, certified gear', 3, 4),
            ('Pollution', 'Bunkering', 'Oil spill', 'Environmental damage, fines', 'Checklist, scuppers plugged, SOPEP kit', 2, 4),
        ],
    },
    # ------------------------------------------------------------------ 13 aviation and airports
    'aviation': {
        'terms': {'site': 'Airport / hangar', 'area': 'Apron / stand / bay', 'supervisor': 'Ramp supervisor'},
        'incident_categories': [('AV-FOD', 'FOD found / FOD damage', 'property', False), ('AV-GSE', 'Ground support equipment collision', 'property', False),
                                ('AV-FUEL', 'Fuel spill', 'environment', False), ('AV-JET', 'Jet blast / ingestion incident', 'injury', False)],
        'permit_types': [
            ptw('AV-AIRSIDE', 'Airside works', '#ea580c', 'Works on the apron or near stands: NOTAM, escorts, FOD control.', 'AV-AIRSIDE-PTW',
                ((AA, 'Airside operations'), (PI, 'Safety manager'))),
            ptw('AV-FUEL', 'Aircraft fuel system work', '#dc2626', 'Opening fuel tanks or lines in the hangar.', 'AV-FUEL-PTW',
                ((AA, 'Maintenance manager'), (GT, 'Gas tester')), requires_gas_test=True),
        ],
        'forms': [
            form('permit_checklist', 'AV-AIRSIDE-PTW', 'Airside works checklist', [
                Q('Coordination', 'Approved by airside operations / NOTAM issued', critical=True), Q('FOD', 'FOD control and sweep after work', critical=True),
                Q('Vehicles', 'Vehicles permitted and escorted')]),
            form('permit_checklist', 'AV-FUEL-PTW', 'Fuel system work checklist', [
                Q('Atmosphere', 'Tank ventilated and LEL tested', critical=True), Q('Ignition', 'Bonding and earthing in place', critical=True)]),
            form('inspection', 'INS-FOD', 'FOD walk', [
                Q('Apron', 'Apron and stands free of FOD', critical=True), Q('Bins', 'FOD bins available and emptied'),
                Q('Reporting', 'FOD found recorded with location')], 'Each shift'),
            form('inspection', 'INS-GSE', 'GSE pre-use check', [
                Q('Brakes', 'Brakes and steering working', critical=True), Q('Bumpers', 'Protective bumpers intact'),
                Q('Lights', 'Beacon and lights working')], 'Before use'),
            tbt('TBT-JETBLAST', 'Jet blast and engine ingestion zones'), tbt('TBT-AIRSIDE-DRIVE', 'Airside driving rules'),
        ],
        'hazards': [
            ('Ramp', 'Aircraft turnaround', 'Struck by GSE / aircraft', 'Fatality', 'Marshalling, stand rules, vehicle speed limits', 2, 5),
            ('Engines', 'Engine runs', 'Jet blast / ingestion', 'Fatality', 'Exclusion zones, beacon rules', 2, 5),
            ('FOD', 'Apron', 'Foreign object debris', 'Aircraft damage', 'FOD walks, bins, sweepers', 3, 4),
            ('Fuel', 'Refuelling', 'Fuel spill / fire', 'Fire, environmental damage', 'Bonding, spill kits, fire cover', 2, 5),
            ('Noise', 'Apron work', 'Aircraft noise', 'Hearing loss', 'Hearing protection, audiometry', 4, 3),
            ('Baggage', 'Baggage handling', 'Manual handling', 'Back injury', 'Lifting aids, belt loaders, training', 4, 2),
        ],
    },
    # ------------------------------------------------------------------ 15 telecom and network infrastructure
    'telecom': {
        'terms': {'site': 'Tower / site', 'area': 'Tower section / shelter / rooftop', 'supervisor': 'Field supervisor', 'contractor': 'Field contractor'},
        'incident_categories': [('TEL-FALL', 'Fall from tower / rooftop', 'injury', False), ('TEL-RF', 'RF overexposure', 'occupational', True),
                                ('TEL-DROP', 'Dropped object from height', 'injury', False), ('TEL-BATT', 'Battery / rectifier incident', 'injury', False)],
        'permit_types': [
            ptw('TOWER', 'Tower climb / work at height', '#ea580c', 'Climbing towers and masts: certified climbers, rescue plan, weather limits.', 'TOWER-PTW',
                ((AA, 'Field supervisor'), (PI, 'HSE officer')), hours=10),
            ptw('RF-WORK', 'Work in RF exposure zones', '#9333ea', 'Work near live antennas: power-down or exclusion distance agreed with the NOC.', 'RF-PTW',
                ((AA, 'NOC / RF engineer'), (PI, 'Field supervisor'))),
        ],
        'forms': [
            form('permit_checklist', 'TOWER-PTW', 'Tower climb checklist', [
                Q('People', 'Climbers hold valid tower climbing and rescue certificates', critical=True), Q('Equipment', 'Harness, lanyards and fall arrest inspected', critical=True),
                Q('Rescue', 'Rescue kit and trained rescuer on site', critical=True), Q('Weather', 'Wind speed below the limit, no lightning')]),
            form('permit_checklist', 'RF-PTW', 'RF safety checklist', [
                Q('RF', 'Sectors powered down or exclusion distance kept (NOC confirmation)', critical=True), Q('Monitor', 'Personal RF monitor worn')]),
            form('inspection', 'INS-TOWER', 'Tower site inspection', [
                Q('Structure', 'No corrosion, missing bolts or damaged members', critical=True), Q('Fall arrest', 'Safety rail / climbing system certified', critical=True),
                Q('Signs', 'RF warning signs at the access'), Q('Shelter', 'Battery room ventilated, eye wash available')], 'Quarterly'),
            tbt('TBT-RF', 'RF exposure and safe distances'), tbt('TBT-DROPPED', 'Dropped objects – tool tethering'),
        ],
        'hazards': [
            ('Height', 'Tower climbing', 'Fall from height', 'Fatality', 'Certified climbers, 100 % tie-off, rescue plan', 2, 5),
            ('RF', 'Work near antennas', 'RF overexposure', 'Burns / health effects', 'Power-down, RF monitors, signage', 2, 4),
            ('Dropped objects', 'Work at height', 'Falling tools', 'Serious injury below', 'Tool tethers, exclusion zone', 3, 4),
            ('Electrical', 'Rectifiers and batteries', 'Shock / battery explosion', 'Injury', 'LOTO, insulated tools, ventilation', 2, 4),
            ('Heat', 'Remote rooftop work', 'Heat stress', 'Heat stroke', 'Work-rest cycles, water, buddy system', 3, 4),
            ('Driving', 'Remote site visits', 'Road accident', 'Fatality', 'Journey plans, 4×4 training, check-in', 2, 5),
        ],
    },
    # ------------------------------------------------------------------ 16 electrical, power and utilities
    'utilities': {
        'terms': {'site': 'Substation / plant', 'area': 'Bay / switchroom / circuit', 'supervisor': 'Senior authorised person'},
        'incident_categories': [('UT-ARC', 'Arc flash', 'injury', False), ('UT-SHOCK', 'Electric shock', 'injury', False),
                                ('UT-SWITCH', 'Switching error', 'property', False), ('UT-CABLE', 'Underground cable strike', 'property', False)],
        'permit_types': [
            ptw('HV-PTW', 'HV permit to work', '#1d4ed8', 'Work on high-voltage equipment: switching programme, isolation, earthing, proving dead.', 'HV-PTW-CL',
                ((AA, 'Control engineer'), (PI, 'Senior authorised person')), requires_isolation=True, hours=12),
            ptw('LIVE', 'Energised work authorisation', '#b91c1c', 'Only when de-energising is not possible: arc-flash study, justification, PPE category.', 'LIVE-CL',
                ((SM, 'Operations manager'), (HM, 'HSE manager')), hours=4),
            ptw('EXC-CABLE', 'Excavation near buried services', '#a16207', 'Digging near cables or pipes: drawings, CAT scan, hand-dig zone.', 'EXC-CABLE-CL',
                ((AA, 'Utility owner'), (PI, 'Site supervisor'))),
        ],
        'forms': [
            form('permit_checklist', 'HV-PTW-CL', 'HV permit checklist', [
                Q('Programme', 'Switching programme approved and followed', critical=True), Q('Isolation', 'Points of isolation locked', critical=True),
                Q('Earthing', 'Circuit main earths applied', critical=True), Q('Proving', 'Proved dead at the point of work', critical=True)]),
            form('permit_checklist', 'LIVE-CL', 'Energised work checklist', [
                Q('Justification', 'Written reason why it cannot be made dead', critical=True), Q('Arc flash', 'Arc-flash boundary and PPE category set', critical=True),
                Q('People', 'Two competent persons, one as safety observer', critical=True)]),
            form('permit_checklist', 'EXC-CABLE-CL', 'Buried services checklist', [
                Q('Drawings', 'Utility drawings checked', critical=True), Q('Scan', 'CAT and genny scan done', critical=True), Q('Dig', 'Hand-dig within 0.5 m of services')]),
            form('inspection', 'INS-SUBSTATION', 'Substation inspection', [
                Q('Access', 'Substation locked, danger signs displayed', critical=True), Q('Equipment', 'No oil leaks, abnormal noise or hot spots'),
                Q('Safety', 'Rubber mats, earthing sticks and HV gloves in date'), Q('Fire', 'Fire suppression / extinguishers in date')], 'Monthly'),
            tbt('TBT-ARCFLASH', 'Arc flash – boundaries and PPE'), tbt('TBT-PROVEDEAD', 'Test before touch'),
        ],
        'hazards': [
            ('Electrical', 'HV switching and maintenance', 'Electric shock / arc flash', 'Fatality', 'Switching programmes, isolation, earthing, arc-flash PPE', 2, 5),
            ('Electrical', 'Energised work', 'Arc flash', 'Severe burns', 'Avoid live work, arc-flash study, category PPE', 2, 5),
            ('Excavation', 'Cable trenches', 'Cable strike', 'Electrocution', 'Drawings, scanning, hand digging', 3, 5),
            ('Height', 'Overhead lines and towers', 'Fall from height', 'Fatality', 'Fall arrest, rescue plan', 2, 5),
            ('Fire', 'Transformers', 'Oil fire', 'Loss, injury', 'Bunds, suppression, inspections', 2, 4),
        ],
    },
    # ------------------------------------------------------------------ 17 waste management and recycling
    'waste': {
        'terms': {'site': 'Facility / landfill / MRF', 'area': 'Tipping area / line / cell', 'supervisor': 'Shift supervisor'},
        'incident_categories': [('WST-COL', 'Collection vehicle incident', 'injury', False), ('WST-FIRE', 'Waste fire', 'property', False),
                                ('WST-SHARP', 'Needle / sharps found in waste', 'injury', True), ('WST-HAZ', 'Hazardous waste release', 'environment', False)],
        'permit_types': [
            ptw('WST-MACH', 'Baler / shredder maintenance', '#2563eb', 'Clearing jams and maintaining balers, shredders and conveyors.', 'WST-MACH-PTW',
                ((AA, 'Plant supervisor'), (PI, 'Maintenance lead')), requires_isolation=True),
        ],
        'forms': [
            form('permit_checklist', 'WST-MACH-PTW', 'Baler / shredder isolation checklist', [
                Q('Isolation', 'Machine isolated, locked and stored energy released', critical=True), Q('Access', 'Never enter the chamber without isolation', critical=True)]),
            form('inspection', 'INS-MRF', 'Recycling facility inspection', [
                Q('Traffic', 'Pedestrians separated from loaders and trucks', critical=True), Q('Fire', 'Stockpile size and spacing within the fire plan', critical=True),
                Q('Guards', 'Conveyor guards and pull-wires working'), Q('Dust', 'Dust suppression working')], 'Weekly'),
            form('inspection', 'INS-COLLECTION', 'Collection vehicle and crew check', [
                Q('Vehicle', 'Reversing camera and alarm working', critical=True), Q('Lift', 'Bin lift safety bars working', critical=True),
                Q('PPE', 'Crew wearing hi-vis, gloves and safety shoes')], 'Daily'),
            tbt('TBT-REVERSING', 'Reversing vehicles and banksmen'), tbt('TBT-SHARPS-WST', 'Sharps and hazardous items in waste'),
        ],
        'hazards': [
            ('Vehicles', 'Collection rounds', 'Struck by reversing vehicle', 'Fatality', 'Banksman, cameras, route risk assessment', 3, 5),
            ('Machinery', 'Balers and shredders', 'Entanglement / crush', 'Fatality', 'Interlocks, LOTO, no entry during operation', 2, 5),
            ('Fire', 'Waste stockpiles', 'Spontaneous combustion', 'Fire, smoke', 'Stockpile limits, thermal monitoring, hydrants', 3, 4),
            ('Biological', 'Mixed waste', 'Sharps / bio-hazards', 'Infection', 'Gloves, hepatitis B vaccination, sharps procedure', 3, 3),
            ('Dust', 'Sorting and handling', 'Bioaerosols / dust', 'Respiratory illness', 'Ventilation, RPE, monitoring', 3, 3),
        ],
    },
    # ------------------------------------------------------------------ 18 cleaning and pest control
    'cleaning': {
        'terms': {'site': 'Client site', 'area': 'Floor / area', 'supervisor': 'Cleaning supervisor'},
        'incident_categories': [('CLN-CHEM', 'Chemical splash / inhalation', 'injury', False), ('CLN-SLIP', 'Slip on wet floor', 'injury', False),
                                ('CLN-PEST', 'Pesticide exposure', 'occupational', True), ('CLN-CLIENT', 'Client property damage', 'property', False)],
        'permit_types': [
            ptw('FUMIG', 'Fumigation / pest control treatment', '#16a34a', 'Fumigation or spraying at a client site: notice, ventilation and re-entry.', 'FUMIG-PTW',
                ((AA, 'Client representative'), (PI, 'Licensed pest control supervisor'))),
            ptw('CLN-HEIGHT', 'High-level / glass cleaning', '#ea580c', 'Window, atrium and facade cleaning at height.', 'CLN-HEIGHT-PTW',
                ((AA, 'Client facilities'), (PI, 'Cleaning supervisor'))),
        ],
        'forms': [
            form('permit_checklist', 'FUMIG-PTW', 'Fumigation checklist', [
                Q('Licence', 'Licensed operator and registered products', critical=True), Q('Notice', 'Occupants notified and area evacuated', critical=True),
                Q('Re-entry', 'Ventilation and re-entry time posted', critical=True)]),
            form('permit_checklist', 'CLN-HEIGHT-PTW', 'High-level cleaning checklist', [
                Q('Access', 'MEWP, cradle or rope access certified', critical=True), Q('Below', 'Area below barriered', critical=True)]),
            form('inspection', 'INS-CLEAN-SITE', 'Client site cleaning inspection', [
                Q('Chemicals', 'Chemicals labelled, diluted per instructions, SDS on site', critical=True), Q('Signs', 'Wet floor signs used'),
                Q('Equipment', 'Machines in good condition, cables undamaged'), Q('PPE', 'Gloves and goggles worn when mixing')], 'Monthly per site'),
            tbt('TBT-DILUTION', 'Never mix chemicals – dilution rules'), tbt('TBT-WETFLOOR', 'Wet floors and signage'),
        ],
        'hazards': [
            ('Chemical', 'Mixing and dilution', 'Toxic gas (bleach + acid)', 'Respiratory injury', 'Dosing systems, training, no mixing', 2, 4),
            ('Slips', 'Wet floor cleaning', 'Slip, trip or fall', 'Injury to staff / public', 'Signs, dry zones, footwear', 4, 2),
            ('Pesticides', 'Pest control', 'Pesticide exposure', 'Poisoning', 'Licensed products, PPE, re-entry times', 2, 4),
            ('Height', 'Glass and high-level cleaning', 'Fall from height', 'Fatality', 'MEWPs, rope access, permits', 2, 5),
            ('Biological', 'Toilet and clinical area cleaning', 'Infection', 'Illness', 'Gloves, hygiene, colour coding', 3, 2),
        ],
    },
    # ------------------------------------------------------------------ 19 laboratories and pharmaceuticals
    'laboratory': {
        'terms': {'site': 'Laboratory / plant', 'area': 'Lab / cleanroom / suite', 'supervisor': 'Lab supervisor'},
        'incident_categories': [('LAB-SPILL', 'Chemical spill in the lab', 'environment', False), ('LAB-BIO', 'Biological exposure', 'occupational', True),
                                ('LAB-RAD', 'Radiation exposure / source incident', 'occupational', True), ('LAB-GLASS', 'Glassware / sharps injury', 'injury', False)],
        'permit_types': [
            ptw('BSC-DECON', 'Biosafety cabinet / lab decontamination', '#0d9488', 'Decontamination before maintenance of BSCs, autoclaves or labs.', 'BSC-PTW',
                ((AA, 'Biosafety officer'), (PI, 'Lab manager'))),
            ptw('CLEANROOM', 'Cleanroom maintenance entry', '#0891b2', 'Engineers entering classified areas: gowning, materials and re-qualification.', 'CR-PTW',
                ((AA, 'QA'), (PI, 'Production manager'))),
        ],
        'forms': [
            form('permit_checklist', 'BSC-PTW', 'Decontamination checklist', [
                Q('Decon', 'Decontamination done and recorded', critical=True), Q('Agents', 'Biological agents removed or secured', critical=True)]),
            form('permit_checklist', 'CR-PTW', 'Cleanroom entry checklist', [
                Q('Gowning', 'Gowning procedure followed', critical=True), Q('Materials', 'Tools cleaned and logged in'),
                Q('Requalification', 'Requalification after work planned')]),
            form('inspection', 'INS-LAB', 'Laboratory safety inspection', [
                Q('Fume hood', 'Fume hoods tested within 12 months, sash at the mark', critical=True), Q('Chemicals', 'Chemicals segregated, labelled, in date', critical=True),
                Q('Emergency', 'Eye wash and shower tested weekly'), Q('Waste', 'Waste segregated and labelled'), Q('PPE', 'Lab coats and eye protection worn')], 'Monthly'),
            form('inspection', 'INS-BSC', 'Biosafety cabinet check', [
                Q('Airflow', 'Airflow alarm working, certified within 12 months', critical=True), Q('Clutter', 'Grille not blocked')], 'Monthly'),
            tbt('TBT-FUMEHOOD', 'Using a fume hood correctly'), tbt('TBT-SPILL-LAB', 'Chemical and biological spill response'),
        ],
        'hazards': [
            ('Chemical', 'Handling reagents and solvents', 'Exposure / fire', 'Injury, illness', 'Fume hoods, substitution, PPE', 3, 4),
            ('Biological', 'Work with agents', 'Infection', 'Illness', 'Containment level, BSC, vaccination', 2, 4),
            ('Radiation', 'Sources and X-ray', 'Exposure', 'Radiation injury', 'Licensing, shielding, dosimetry', 2, 4),
            ('Pharma', 'Potent compounds (API)', 'Sensitisation / toxicity', 'Illness', 'Containment, isolators, RPE', 2, 4),
            ('Cryogens', 'Liquid nitrogen', 'Asphyxiation / cold burns', 'Fatality', 'Oxygen monitors, ventilation, PPE', 2, 5),
        ],
    },
    # ------------------------------------------------------------------ 20 marine construction and shipyards
    'shipyard': {
        'terms': {'site': 'Shipyard / dock', 'area': 'Dock / block / tank', 'supervisor': 'Foreman'},
        'incident_categories': [('SY-WELD', 'Welding / cutting incident', 'injury', False), ('SY-FALL', 'Fall into dock / from staging', 'injury', False),
                                ('SY-DIVE', 'Diving incident', 'injury', False), ('SY-PAINT', 'Paint / solvent exposure', 'occupational', True)],
        'permit_types': [
            ptw('SY-HOT', 'Hot work in tanks and blocks', '#dc2626', 'Welding and cutting in or near tanks: gas-free certificate, fire watch.', 'SY-HOT-PTW',
                ((AA, 'Yard superintendent'), (GT, 'Marine chemist / gas tester')), requires_gas_test=True, gas_retest_hours=4,
                gas_limits={'o2_min': 19.5, 'o2_max': 23.5, 'lel_max': 1, 'h2s_max': 5, 'co_max': 25}),
            ptw('DIVE', 'Diving operation', '#0369a1', 'Commercial diving: dive plan, isolation of intakes and propellers, standby diver.', 'DIVE-PTW',
                ((AA, 'Dock master'), (PI, 'Diving supervisor')), requires_isolation=True, hours=8),
            ptw('SY-PAINT', 'Painting / blasting in enclosed areas', '#7c3aed', 'Spray painting or grit blasting in tanks: ventilation, LEL, air-fed RPE.', 'SY-PAINT-PTW',
                ((AA, 'Yard superintendent'), (GT, 'Gas tester')), requires_gas_test=True),
        ],
        'forms': [
            form('permit_checklist', 'SY-HOT-PTW', 'Shipyard hot work checklist', [
                Q('Gas-free', 'Gas-free certificate valid for the space', critical=True), Q('Fire', 'Fire watch both sides of the plate', critical=True),
                Q('Ventilation', 'Ventilation running')]),
            form('permit_checklist', 'DIVE-PTW', 'Diving checklist', [
                Q('Isolation', 'Propellers, thrusters and sea intakes isolated and tagged', critical=True), Q('Team', 'Standby diver dressed and ready', critical=True),
                Q('Plan', 'Dive plan and decompression schedule agreed')]),
            form('permit_checklist', 'SY-PAINT-PTW', 'Painting / blasting checklist', [
                Q('Atmosphere', 'LEL monitored continuously', critical=True), Q('RPE', 'Air-fed respirators in use', critical=True),
                Q('Ignition', 'Only intrinsically safe lights and tools', critical=True)]),
            form('inspection', 'INS-DOCK-SY', 'Dry dock inspection', [
                Q('Edges', 'Dock edges guarded', critical=True), Q('Staging', 'Staging tagged and inspected', critical=True),
                Q('Gas lines', 'Gas and oxygen hoses with flashback arrestors'), Q('Housekeeping', 'Walkways clear of cables and slag')], 'Daily'),
            tbt('TBT-GASFREE', 'Gas-free certificates and hot work'), tbt('TBT-HOSES', 'Gas hoses, flashback and leaks'),
        ],
        'hazards': [
            ('Hot work', 'Welding in tanks', 'Explosion / fire', 'Fatality', 'Gas-free certificate, fire watch, ventilation', 2, 5),
            ('Height', 'Staging and dock edges', 'Fall from height', 'Fatality', 'Tagged staging, edge protection, harness', 3, 5),
            ('Diving', 'Hull and dock diving', 'Diver entrapment / differential pressure', 'Fatality', 'Isolations, dive plan, standby diver', 2, 5),
            ('Chemical', 'Painting', 'Solvent exposure / fire', 'Illness, fire', 'Ventilation, air-fed RPE, LEL monitoring', 3, 4),
            ('Lifting', 'Block lifts', 'Dropped load', 'Fatality', 'Lift plans, certified gear, exclusion zones', 2, 5),
        ],
    },
    # ------------------------------------------------------------------ 21 data centres and critical infrastructure
    'data_center': {
        'terms': {'site': 'Data centre', 'area': 'Data hall / room / rack row', 'supervisor': 'Shift / facility engineer', 'contractor': 'Vendor'},
        'incident_categories': [('DC-ELEC', 'Electrical incident / arc flash', 'injury', False), ('DC-BATT', 'Battery / UPS incident (thermal runaway)', 'property', False),
                                ('DC-FUEL', 'Diesel / fuel spill', 'environment', False), ('DC-SUPP', 'Accidental gas suppression discharge', 'property', False),
                                ('DC-REFR', 'Refrigerant release', 'environment', False), ('DC-OUT', 'Unplanned outage caused by work', 'property', False)],
        'permit_types': [
            ptw('DC-ELEC', 'Critical electrical maintenance', '#1d4ed8', 'Work on switchgear, PDUs, busbars and UPS: MOP, isolation, LOTO, zero-energy verification.',
                'DC-ELEC-PTW', ((AA, 'Facility manager'), (PI, 'Authorised electrical person')), requires_isolation=True, hours=8),
            ptw('DC-BATT', 'UPS / battery room work', '#7c3aed', 'Battery replacement or maintenance: ventilation, hydrogen / thermal runaway controls.', 'DC-BATT-PTW',
                ((AA, 'Facility manager'), (PI, 'Critical systems engineer')), requires_isolation=True),
            ptw('DC-GEN', 'Generator and fuel system work', '#a16207', 'Generators, day tanks, bulk fuel: spill control, exhaust, emergency shutdown.', 'DC-GEN-PTW',
                ((AA, 'Facility manager'), (PI, 'Mechanical engineer'))),
            ptw('DC-SUPP', 'Work affecting fire detection / suppression', '#dc2626', 'Isolating detection or gas suppression in a data hall: fire watch, inhibit and reinstate.',
                'DC-SUPP-PTW', ((AA, 'Facility manager'), (PI, 'Fire / security manager')), hours=8),
        ],
        'forms': [
            form('permit_checklist', 'DC-ELEC-PTW', 'Critical electrical work checklist', [
                Q('MOP', 'Method of procedure (MOP) approved and change ticket open', critical=True), Q('Isolation', 'Isolated, locked and tagged at every point', critical=True),
                Q('Zero energy', 'Zero energy verified with a proved tester', critical=True), Q('Arc flash', 'Arc-flash boundary marked, category PPE worn', critical=True),
                Q('Redundancy', 'Redundant path confirmed live before work (N+1 kept)')]),
            form('permit_checklist', 'DC-BATT-PTW', 'UPS / battery room checklist', [
                Q('Ventilation', 'Battery room ventilation running', critical=True), Q('Chemistry', 'Battery chemistry known (VRLA / Li-ion) and its procedure followed', critical=True),
                Q('Thermal', 'Thermal camera check before work'), Q('Emergency', 'Eye wash and spill kit in the room')]),
            form('permit_checklist', 'DC-GEN-PTW', 'Generator / fuel checklist', [
                Q('Spill', 'Drip trays and spill kit at the work', critical=True), Q('Ignition', 'No ignition sources near fuel', critical=True),
                Q('Start', 'Generator auto-start inhibited and tagged', critical=True)]),
            form('permit_checklist', 'DC-SUPP-PTW', 'Detection / suppression isolation checklist', [
                Q('Inhibit', 'Only the zones needed inhibited, recorded with times', critical=True), Q('Watch', 'Fire watch in place while inhibited', critical=True),
                Q('Reinstate', 'Reinstated and panel normal at the end', critical=True)]),
            form('inspection', 'INS-UPS', 'UPS and battery room inspection', [
                Q('Temperature', 'Room temperature within the battery range', critical=True), Q('Batteries', 'No swelling, leaks or hot spots', critical=True),
                Q('Ventilation', 'Extract ventilation working'), Q('Fire', 'Detection and suppression normal')], 'Weekly'),
            form('inspection', 'INS-GEN', 'Generator and fuel inspection', [
                Q('Fuel', 'No leaks; bund clean and dry', critical=True), Q('Levels', 'Fuel level above the minimum hours'),
                Q('Exhaust', 'Exhaust and ventilation clear'), Q('Test', 'Last load test within the schedule')], 'Weekly'),
            form('inspection', 'INS-DATAHALL', 'Data hall safety walk', [
                Q('Cables', 'No trailing cables; floor tiles all in place', critical=True), Q('Access', 'Doors closed, access control working'),
                Q('Housekeeping', 'No cardboard or combustibles in the hall'), Q('Cooling', 'No water leaks under the raised floor')], 'Daily'),
            form('drill', 'DRILL-DC', 'Data centre emergency drill', [
                Q('Response', 'Emergency power-off procedure known (not operated)'), Q('Response', 'Suppression pre-discharge evacuation achieved', critical=True),
                Q('Response', 'Evacuation time (minutes)', type='number')], 'Twice a year'),
            tbt('TBT-LIION', 'Lithium-ion batteries and thermal runaway'), tbt('TBT-MOP', 'Working to the MOP – no surprises in a live data centre'),
        ],
        'hazards': [
            ('Electrical', 'Switchgear and PDU maintenance', 'Arc flash / electric shock', 'Fatality', 'MOP, LOTO, zero-energy verification, arc-flash PPE', 2, 5),
            ('Batteries', 'UPS battery rooms', 'Thermal runaway / hydrogen', 'Fire, explosion', 'Ventilation, monitoring, chemistry-specific procedures', 2, 5),
            ('Fuel', 'Generators and bulk fuel', 'Fuel spill / fire', 'Fire, environmental damage', 'Bunds, spill kits, inspections', 2, 4),
            ('Suppression', 'Gas suppression in halls', 'Discharge with people inside', 'Asphyxiation, injury', 'Pre-discharge alarm, evacuation, inhibit control', 2, 5),
            ('Cooling', 'Chillers and CRAH units', 'Refrigerant release', 'Asphyxiation, environmental', 'Leak detection, certified technicians', 2, 4),
            ('Noise', 'Generator and plant rooms', 'Noise', 'Hearing loss', 'Hearing protection, time limits', 4, 2),
            ('Manual handling', 'Server and battery handling', 'Strain / crush', 'Injury', 'Server lifts, team lifting', 3, 2),
        ],
    },
}

LABELS = {'mining': 'Mining, quarrying and cement', 'logistics': 'Warehousing, logistics and transport', 'hospitality': 'Hotels and restaurants',
          'facilities': 'Facilities management', 'agriculture': 'Agriculture and farming', 'food': 'Food processing', 'marine': 'Marine, shipping and ports',
          'aviation': 'Aviation and airports', 'telecom': 'Telecom and network infrastructure', 'utilities': 'Electrical, power and utilities',
          'waste': 'Waste management and recycling', 'cleaning': 'Cleaning and pest control', 'laboratory': 'Laboratories and pharmaceuticals',
          'shipyard': 'Marine construction and shipyards', 'data_center': 'Data centres and critical infrastructure'}

DESCRIPTIONS = {
    'mining': 'Blasting authorisation, conveyors and crushers, haul roads, ground stability, heavy vehicles, silica dust and noise.',
    'logistics': 'Forklifts and MHE, racking inspection, loading docks, fleet and driver checks, dangerous goods and cold stores.',
    'hospitality': 'Kitchens and food safety, swimming pools, housekeeping chemicals, guest accidents and hot work in occupied buildings.',
    'facilities': 'Electrical isolation, water tanks and plant rooms, roof and facade work, lifts, fire systems and service providers.',
    'agriculture': 'Tractors and implements, pesticide application, agrochemical stores, heat stress and animal handling.',
    'food': 'HACCP-related hazards, hygiene inspections, CIP chemicals, ammonia refrigeration, cold stores and machine guarding.',
    'marine': 'Enclosed space entry on board, hot work, mooring and snap-back, man overboard drills and oil spills.',
    'aviation': 'Airside works, FOD walks, ground support equipment, fuel systems, jet blast and ramp safety.',
    'telecom': 'Tower climbing, RF exposure, dropped objects, battery shelters and remote site travel.',
    'utilities': 'HV permits, switching programmes, energised work authorisation, arc flash, substations and buried cables.',
    'waste': 'Collection vehicles, balers and shredders, stockpile fires, sharps and bioaerosols.',
    'cleaning': 'Chemical dilution, wet floors, fumigation and pest control, high-level cleaning at client sites.',
    'laboratory': 'Fume hoods, biosafety cabinets, decontamination, cleanroom entry, radiation and cryogens.',
    'shipyard': 'Hot work with gas-free certificates, diving operations, painting and blasting, staging and dock edges.',
    'data_center': 'Critical electrical work with MOPs and LOTO, UPS and lithium-ion batteries, generators and fuel, gas suppression, '
                   'cooling and refrigerants, data hall walks and vendor control.',
}

EXTRAS = {
    'mining': {'competencies': [('SHOT', 'Licensed shot-firer', 36), ('HV-OP', 'Heavy vehicle / haul truck operator', 24), ('GEO', 'Ground control inspection', 36)],
               'permit_competencies': {'BLAST': ['IND', 'SHOT'], 'CONV': ['IND', 'LOTO']},
               'kpis': [('DUST', 'Dust samples above the action level', 'manual', 'samples', 0, False)],
               'forms': [audit('AUD-MINE', 'Mine / quarry safety audit', [Q('Ground', 'Geotechnical inspections up to date', critical=True),
                                                                       Q('Blasting', 'Explosives register reconciled', critical=True), Q('Vehicles', 'Traffic management plan followed'),
                                                                       Q('Dust', 'Dust monitoring done and controls working')])],
               'limits': []},
    'logistics': {'competencies': [('FORK', 'Forklift operator', 36), ('DG', 'Dangerous goods handling', 24), ('DRV', 'Defensive driving', 36), ('RACK-I', 'Racking inspector', 36)],
                  'permit_competencies': {'DG-HAND': ['IND', 'DG'], 'RACK-WORK': ['IND', 'WAH']},
                  'kpis': [('RTA', 'Road traffic accidents', 'manual', 'accidents', 0, False), ('FKI', 'Forklift incidents', 'manual', 'incidents', 0, False)],
                  'forms': [audit('AUD-WH', 'Warehouse and fleet audit', [Q('Racking', 'Racking inspections done and damage repaired', critical=True),
                                                                          Q('MHE', 'Operators licensed and pre-use checks done', critical=True),
                                                                          Q('Fleet', 'Driver hours and vehicle checks recorded'), Q('DG', 'DG segregation and documents correct')])],
                  'limits': []},
    'hospitality': {'competencies': [('FOOD-H', 'Food hygiene', 24), ('LIFEG', 'Pool lifeguard', 24), ('FA', 'First aid', 36)],
                    'permit_competencies': {'HOS-HOT': ['IND', 'HOT']},
                    'kpis': [('GUEST', 'Guest accidents', 'manual', 'accidents', 0, False)],
                    'forms': [audit('AUD-HOTEL', 'Hotel HSE and food safety audit', [Q('Food', 'HACCP records complete', critical=True),
                                                                                    Q('Fire', 'Fire systems tested, drills held', critical=True),
                                                                                    Q('Pool', 'Pool water and rescue arrangements correct'), Q('Chemicals', 'Chemical training and SDS in place')])],
                    'limits': []},
    'facilities': {'competencies': [('AEP', 'Authorised electrical person', 24), ('MEWP', 'MEWP operator', 36), ('ROPE', 'Rope access (IRATA / SPRAT)', 36)],
                   'permit_competencies': {'FM-ELEC': ['IND', 'AEP', 'LOTO'], 'FM-TANK': ['IND', 'CSE'], 'FM-ROOF': ['IND', 'WAH']},
                   'kpis': [('PPM', 'Planned maintenance done on time', 'manual', '%', 95, True)],
                   'forms': [audit('AUD-FM', 'Facilities HSE audit', [Q('Permits', 'Maintenance under permits and isolations', critical=True),
                                                                     Q('Fire', 'Fire system maintenance current', critical=True), Q('Water', 'Legionella controls in place'),
                                                                     Q('Contractors', 'Service providers inducted and supervised')])],
                   'limits': []},
    'agriculture': {'competencies': [('PEST-L', 'Pesticide applicator', 36), ('TRAC', 'Tractor operator', 36)],
                    'permit_competencies': {'PEST-APP': ['PEST-L']},
                    'kpis': [('HEATC', 'Heat illness cases', 'manual', 'cases', 0, False)], 'forms': [], 'limits': []},
    'food': {'competencies': [('HACCP', 'HACCP / food safety', 24), ('AMMO', 'Ammonia plant operator', 24), ('FOOD-H', 'Food hygiene', 24)],
             'permit_competencies': {'FD-AMM': ['IND', 'AMMO'], 'FD-CIP': ['IND', 'LOTO']},
             'kpis': [('CONT', 'Contamination incidents', 'manual', 'incidents', 0, False)],
             'forms': [audit('AUD-FOOD', 'Food plant safety and hygiene audit', [Q('HACCP', 'CCP records complete and verified', critical=True),
                                                                               Q('Guards', 'Machine guards in place', critical=True), Q('Ammonia', 'Ammonia detectors tested'),
                                                                               Q('Hygiene', 'Hygiene rules followed')])],
             'limits': [('Ammonia', 'chemical', 'ppm', 25, 12.5, 'twa8')]},
    'marine': {'competencies': [('STCW', 'STCW basic safety training', 60), ('ENCL', 'Enclosed space entry and rescue', 24), ('MOOR', 'Mooring operations', 36)],
               'permit_competencies': {'MAR-ENCL': ['ENCL'], 'MAR-HOT': ['HOT']},
               'kpis': [('SPILL', 'Oil spills', 'manual', 'spills', 0, False)], 'forms': [], 'limits': []},
    'aviation': {'competencies': [('ADP', 'Airside driving permit', 24), ('GSE', 'GSE operator', 24), ('FUELA', 'Aircraft fuelling', 24)],
                 'permit_competencies': {'AV-AIRSIDE': ['IND', 'ADP'], 'AV-FUEL': ['IND', 'FUELA']},
                 'kpis': [('FODN', 'FOD items found', 'manual', 'items', 0, False)], 'forms': [], 'limits': []},
    'telecom': {'competencies': [('CLIMB', 'Tower climbing and rescue', 24), ('RFS', 'RF safety awareness', 36), ('WAH', 'Working at height', 24)],
                'permit_competencies': {'TOWER': ['IND', 'CLIMB'], 'RF-WORK': ['RFS']},
                'kpis': [('CLMB', 'Climbs without a valid permit', 'manual', 'climbs', 0, False)], 'forms': [], 'limits': []},
    'utilities': {'competencies': [('SAP', 'Senior authorised person (HV)', 24), ('AEP', 'Authorised electrical person', 24), ('ARC', 'Arc flash awareness', 36),
                                   ('CAT', 'Cable avoidance (CAT and genny)', 36)],
                  'permit_competencies': {'HV-PTW': ['SAP'], 'LIVE': ['AEP', 'ARC'], 'EXC-CABLE': ['CAT']},
                  'kpis': [('SWERR', 'Switching errors', 'manual', 'errors', 0, False)],
                  'forms': [audit('AUD-HV', 'HV safety rules audit', [Q('Rules', 'Switching programmes and permits per the safety rules', critical=True),
                                                                      Q('People', 'Authorisations current', critical=True), Q('Equipment', 'HV PPE and test equipment in date')])],
                  'limits': []},
    'waste': {'competencies': [('BANK', 'Banksman / reversing assistant', 36), ('BALER', 'Baler / shredder operator', 24)],
              'permit_competencies': {'WST-MACH': ['IND', 'LOTO']},
              'kpis': [('WFIRE', 'Waste fires', 'manual', 'fires', 0, False)], 'forms': [], 'limits': []},
    'cleaning': {'competencies': [('CHEM-C', 'Cleaning chemicals', 24), ('PEST-L', 'Pesticide applicator', 36), ('MEWP', 'MEWP operator', 36)],
                 'permit_competencies': {'FUMIG': ['PEST-L'], 'CLN-HEIGHT': ['WAH']},
                 'kpis': [('CSITE', 'Client site inspection score', 'manual', '%', 90, True)], 'forms': [], 'limits': []},
    'laboratory': {'competencies': [('BIOS', 'Biosafety', 24), ('RADW', 'Radiation worker', 24), ('GMP', 'GMP / cleanroom behaviour', 12)],
                   'permit_competencies': {'BSC-DECON': ['BIOS'], 'CLEANROOM': ['GMP']},
                   'kpis': [('LABSP', 'Lab spills', 'manual', 'spills', 0, False)],
                   'forms': [audit('AUD-LAB', 'Laboratory safety audit', [Q('Hoods', 'Fume hoods and BSCs certified', critical=True),
                                                                         Q('Chemicals', 'Inventory current, incompatibles separated', critical=True),
                                                                         Q('Waste', 'Waste streams labelled and removed'), Q('Training', 'Lab workers trained for their agents')])],
                   'limits': [('Formaldehyde', 'chemical', 'ppm', 0.1, 0.05, 'twa8')]},
    'shipyard': {'competencies': [('WELD', 'Welder (coded)', 24), ('DIVER', 'Commercial diver', 12), ('MCHEM', 'Marine chemist / gas-free certifier', 24)],
                 'permit_competencies': {'SY-HOT': ['IND', 'HOT', 'WELD'], 'DIVE': ['DIVER'], 'SY-PAINT': ['IND', 'CSE']},
                 'kpis': [('GASF', 'Hot work without gas-free certificate', 'manual', 'cases', 0, False)], 'forms': [], 'limits': []},
    'data_center': {'competencies': [('AEP', 'Authorised electrical person', 24), ('ARC', 'Arc flash awareness', 36), ('LIION', 'Lithium-ion battery safety', 24),
                                     ('CSE-DC', 'Critical systems engineer (data centre)', 24), ('REFR', 'Refrigerant handling (certified)', 36)],
                    'permit_competencies': {'DC-ELEC': ['IND', 'AEP', 'ARC', 'LOTO'], 'DC-BATT': ['IND', 'LIION'], 'DC-GEN': ['IND'], 'DC-SUPP': ['IND', 'FIRE']},
                    'kpis': [('DCOUT', 'Outages caused by maintenance', 'manual', 'outages', 0, False), ('MOPC', 'Critical work under an approved MOP', 'manual', '%', 100, True)],
                    'forms': [audit('AUD-DC', 'Data centre HSE audit', [Q('MOP', 'Critical work done to approved MOPs and permits', critical=True),
                                                                       Q('Electrical', 'LOTO and zero-energy verification in practice', critical=True),
                                                                       Q('Batteries', 'Battery rooms monitored and ventilated', critical=True),
                                                                       Q('Fuel', 'Fuel storage bunded and inspected'), Q('Fire', 'Suppression inhibit records complete'),
                                                                       Q('Vendors', 'Vendors inducted and escorted')])],
                    'limits': [('Noise (generator rooms)', 'noise', 'dB(A)', 85, 80, 'twa8')]},
}
