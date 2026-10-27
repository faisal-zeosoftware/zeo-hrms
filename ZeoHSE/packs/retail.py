"""Retail and hospitality pack (v1.38.0) – stores, restaurants, warehouses and hotels: manual handling, slips and trips, food safety
interface, kitchens, cash handling and robbery, guests and crowds. Starting values: review before use."""
from ._util import Q, steps

PACK = {
    'terms': {'site': 'Outlet', 'area': 'Department / floor', 'supervisor': 'Store / outlet manager', 'contractor': 'Contractor / supplier'},
    'incident_categories': [
        ('SLIP', 'Slip, trip or fall', 'injury', False), ('CUST', 'Customer / guest injury', 'injury', False), ('FOOD', 'Food safety incident', 'occupational', False),
        ('BURN', 'Burn / scald (kitchen)', 'injury', False), ('ROB', 'Robbery / theft / aggression', 'injury', False), ('MH', 'Manual handling injury', 'injury', False),
    ],
    'approvals': [],
    'permit_types': [
        {'code': 'TRADINGWORK', 'name': 'Works during trading hours', 'color': '#ea580c', 'max_hours': 10,
         'description': 'Maintenance or shop-fit while customers are present: barriers, signage, no dust or fumes on the floor.',
         'checklist': 'TRADE-PTW', 'approval_steps': steps(('area_authority', 'Store manager'), ('permit_issuer', 'Facilities / HSE'))},
        {'code': 'GASKITCHEN', 'name': 'Gas and kitchen equipment work', 'color': '#dc2626', 'max_hours': 8, 'requires_isolation': True,
         'description': 'Work on gas lines, hoods, fryers or extraction – isolation, fire watch, re-commissioning.',
         'checklist': 'KITCHEN-PTW', 'approval_steps': steps(('area_authority', 'Head chef / F&B manager'), ('permit_issuer', 'Facilities / HSE'))},
    ],
    'forms': [
        {'kind': 'permit_checklist', 'code': 'TRADE-PTW', 'name': 'Works during trading checklist', 'questions': [
            Q('Area', 'Work area barriered and signed', critical=True), Q('Customers', 'No dust, fumes or noise reaching customers'), Q('Fire', 'Exits and extinguishers unobstructed', critical=True),
            Q('Timing', 'Hot work only outside trading hours', critical=True),
        ]},
        {'kind': 'permit_checklist', 'code': 'KITCHEN-PTW', 'name': 'Gas / kitchen equipment checklist', 'questions': [
            Q('Isolation', 'Gas isolated and tagged', critical=True), Q('Fire', 'Fire watch and extinguisher at hand', critical=True), Q('Test', 'Leak test before reinstatement', critical=True),
            Q('Hygiene', 'Food removed / covered, area cleaned after work'),
        ]},
        {'kind': 'inspection', 'code': 'INS-FLOOR', 'name': 'Shop floor safety walk', 'frequency': 'Daily', 'questions': [
            Q('Floors', 'Floors dry, spills cleaned, wet-floor signs used', critical=True), Q('Aisles', 'Aisles and exits clear', critical=True),
            Q('Stock', 'Stock stacked safely, nothing above shoulder height without steps'), Q('Electrical', 'Cables tidy, sockets not overloaded'),
            Q('Fire', 'Extinguishers accessible, exit signs lit'),
        ]},
        {'kind': 'inspection', 'code': 'INS-KITCHEN', 'name': 'Kitchen safety and hygiene inspection', 'frequency': 'Weekly', 'questions': [
            Q('Fire', 'Hood and filters clean, suppression system in date', critical=True), Q('Burns', 'Oven gloves, fryer guards, splash protection'),
            Q('Floors', 'Non-slip mats, floors dry'), Q('Cold chain', 'Fridge and freezer temperatures logged', critical=True),
            Q('Knives', 'Knives stored safely, cut-resistant gloves available'), Q('Gas', 'No smell of gas, detectors working', critical=True),
        ]},
        {'kind': 'inspection', 'code': 'INS-WAREHOUSE', 'name': 'Back store / warehouse inspection', 'frequency': 'Monthly', 'questions': [
            Q('Racking', 'Racking undamaged, load notices displayed', critical=True), Q('MHE', 'Pallet trucks / forklifts inspected'), Q('Access', 'Ladders and steps in good condition'),
            Q('Fire', 'Fire exits and sprinklers unobstructed', critical=True),
        ]},
        {'kind': 'drill', 'code': 'DRILL-EVAC', 'name': 'Evacuation drill with customers', 'frequency': 'Twice a year', 'questions': [
            Q('Response', 'Staff swept their zones'), Q('Response', 'Customers guided to exits'), Q('Response', 'Evacuation time (minutes)', type='number'),
        ]},
        {'kind': 'toolbox_talk', 'code': 'TBT-SLIPS', 'name': 'Slips, trips and falls', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-ROBBERY', 'name': 'Robbery and aggressive customers – what to do', 'questions': []},
        {'kind': 'toolbox_talk', 'code': 'TBT-MH', 'name': 'Manual handling and stock replenishment', 'questions': []},
    ],
    'hazards': [
        ('Slips', 'Shop floor and kitchens', 'Wet floors', 'Fractures, sprains', 'Spill procedure, signs, non-slip footwear', 4, 2),
        ('Manual handling', 'Stock replenishment and deliveries', 'Lifting heavy cartons', 'Back injury', 'Trolleys, team lifting, training, weight limits', 4, 3),
        ('Kitchen', 'Cooking and frying', 'Burns and fires', 'Burns, fire', 'Guards, PPE, suppression system, hood cleaning', 3, 4),
        ('Security', 'Cash handling and late shifts', 'Robbery / aggression', 'Injury, trauma', 'Cash limits, CCTV, two staff at close, de-escalation training', 2, 4),
        ('Crowds', 'Promotions and events', 'Crowd crush', 'Injury', 'Capacity control, queue barriers, security', 2, 4),
        ('Heat', 'Outdoor stalls and deliveries', 'Heat stress', 'Heat illness', 'Shade, water, rotation', 3, 3),
    ],
}
