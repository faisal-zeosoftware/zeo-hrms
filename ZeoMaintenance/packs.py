"""Maintenance industry packs: categories with response / fix times and who pays, and inspection checklists."""


def cat(rows):
    return [{'code': c, 'name': n, 'trade': t, 'priority': p, 'response_hours': r, 'resolve_hours': f, 'who_pays': w} for c, n, t, p, r, f, w in rows]


def chk(rows):
    return [{'code': c, 'name': n, 'kind': k, 'items': i} for c, n, k, i in rows]


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
}
