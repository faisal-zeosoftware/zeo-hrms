"""Property industry packs. General = a few unit types and charges for any company that lets or sells space;
property management = the full set used by property managers and developers."""


def ut(rows):
    return [{'code': c, 'name': n, 'category': k} for c, n, k in rows]


def ch(rows):
    return [{'code': c, 'name': n, 'kind': k, 'vat_percent': v, 'refundable': r} for c, n, k, v, r in rows]


def cl(rows):
    return [{'code': c, 'name': n, 'applies_to': a, 'order': i + 1} for i, (c, n, a) in enumerate(rows)]


PACKS = {
    'general': {
        'label': 'General', 'description': 'Basic unit types (office, shop, warehouse, apartment) and charges (rent, deposit, service charge, fee).',
        'highlights': ['4 unit types', '5 charge types', 'Short handover checklist'],
        'rows': {
            'unit_type': ut([('APT', 'Apartment', 'residential'), ('OFFICE', 'Office', 'commercial'), ('SHOP', 'Shop', 'commercial'),
                             ('WAREHOUSE', 'Warehouse', 'commercial')]),
            'charge': ch([('RENT', 'Rent', 'rent', 0, False), ('DEPOSIT', 'Security deposit', 'deposit', 0, True), ('SERVICE', 'Service charge', 'service', 5, False),
                          ('ADMIN', 'Administration fee', 'fee', 5, False), ('MAINT', 'Maintenance recharge', 'maintenance', 5, False)]),
            'checklist': cl([('KEYS', 'Keys handed over', 'both'), ('METERS', 'Meter readings recorded', 'both'), ('PHOTOS', 'Condition photos taken', 'both')]),
        }},
    'property_management': {
        'label': 'Property management', 'description': 'Residential and commercial unit types, the usual lease and sale charges (deposit, fees, parking, '
                                                       'chiller, bounced-cheque fee …) and move-in / handover checklists. Works with ZEO CRM and Maintenance.',
        'highlights': ['13 unit types', '14 charge types', 'Move-in and sale handover checklists'],
        'terms': {'party': 'Tenant / buyer'},
        'rows': {
            'unit_type': ut([('STUDIO', 'Studio', 'residential'), ('APT', 'Apartment', 'residential'), ('PENTHOUSE', 'Penthouse', 'residential'),
                             ('TOWNHOUSE', 'Townhouse', 'residential'), ('VILLA', 'Villa', 'residential'), ('STAFF', 'Staff accommodation', 'residential'),
                             ('OFFICE', 'Office', 'commercial'), ('SHOP', 'Shop / retail', 'commercial'), ('SHOWROOM', 'Showroom', 'commercial'),
                             ('WAREHOUSE', 'Warehouse', 'commercial'), ('LABOUR', 'Labour camp room', 'commercial'), ('PARKING', 'Parking bay', 'other'),
                             ('LAND', 'Land / plot', 'other')]),
            'charge': ch([('RENT', 'Rent', 'rent', 0, False), ('DEPOSIT', 'Security deposit', 'deposit', 0, True), ('ADMIN', 'Administration fee', 'fee', 5, False),
                          ('AGENCY', 'Agency fee', 'fee', 5, False), ('RENEWAL', 'Renewal fee', 'fee', 5, False), ('REGISTRATION', 'Lease registration fee', 'fee', 0, False),
                          ('SERVICE', 'Service charge', 'service', 5, False), ('CHILLER', 'Chiller / AC charges', 'utility', 5, False),
                          ('PARKING', 'Parking', 'other', 5, False), ('LATE', 'Late payment fee', 'fee', 0, False), ('BOUNCE', 'Bounced cheque fee', 'fee', 0, False),
                          ('MAINT', 'Maintenance recharge', 'maintenance', 5, False), ('INSTALMENT', 'Sale instalment', 'sale', 0, False),
                          ('TRANSFER', 'Title transfer fee', 'fee', 0, False)]),
            'checklist': cl([('KEYS', 'Keys and access cards handed over', 'both'), ('METERS', 'Water and electricity meter readings recorded', 'both'),
                             ('UTIL', 'Utility account moved to the new occupant', 'both'), ('PHOTOS', 'Condition photos taken', 'both'),
                             ('RULES', 'Building rules and emergency contacts given', 'both'), ('PARK', 'Parking bay allocated', 'both'),
                             ('REG', 'Lease registered with the authority', 'lease'), ('CHEQUES', 'All post-dated cheques received', 'lease'),
                             ('FINALPAY', 'Final payment received', 'sale'), ('NOC', 'Developer no-objection certificate', 'sale'),
                             ('TITLE', 'Title deed transferred', 'sale'), ('SNAG', 'Snag walk-through done with the buyer', 'sale')]),
        }},
}
