"""Property industry packs. General = a few unit types and charges for any company that lets or sells space;
property management = the full set used by property managers and developers."""


def ut(rows):
    return [{'code': c, 'name': n, 'category': k} for c, n, k in rows]


def ch(rows):
    return [{'code': c, 'name': n, 'kind': k, 'vat_percent': v, 'refundable': r} for c, n, k, v, r in rows]


def cl(rows):
    return [{'code': c, 'name': n, 'applies_to': a, 'order': i + 1} for i, (c, n, a) in enumerate(rows)]


# v1.40.0 – inspection templates, document templates (clauses), message templates; charge codes used by the rules
ROOMS_RES = [('Entrance', 'Door, lock and bell'), ('Living room', 'Walls and paint'), ('Living room', 'Floor'), ('Living room', 'Windows and curtains'), ('Living room', 'AC and thermostat'),
             ('Kitchen', 'Cabinets and counter'), ('Kitchen', 'Sink and taps'), ('Kitchen', 'Cooker and hood'), ('Kitchen', 'Fridge / appliances'), ('Bedroom', 'Walls and paint'),
             ('Bedroom', 'Wardrobe'), ('Bedroom', 'AC'), ('Bathroom', 'WC, basin and shower'), ('Bathroom', 'Tiles and sealant'), ('Bathroom', 'Water heater'), ('Balcony', 'Floor and railing'),
             ('General', 'Lights and switches'), ('General', 'Smoke detector'), ('General', 'Keys, cards and remotes')]
ROOMS_COM = [('Entrance', 'Door, lock and signage'), ('Office area', 'Walls and paint'), ('Office area', 'Flooring / carpet'), ('Office area', 'Ceiling and lights'), ('Office area', 'AC and ducts'),
             ('Pantry', 'Sink, cabinets and appliances'), ('Toilets', 'Fixtures and tiles'), ('Services', 'Electrical panel (DB)'), ('Services', 'Data and telephone points'), ('Services', 'Fire alarm and extinguishers'),
             ('General', 'Windows and blinds'), ('General', 'Keys, cards and remotes')]
IT = [{'code': 'RES', 'name': 'Residential unit', 'applies_to': 'residential', 'rooms': [{'room': r, 'item': i} for r, i in ROOMS_RES]},
      {'code': 'COM', 'name': 'Office / shop', 'applies_to': 'commercial', 'rooms': [{'room': r, 'item': i} for r, i in ROOMS_COM]}]
LEASE_EN = ('1. The landlord lets {unit} in {building} to {tenant} from {start} to {end} for an annual rent of {rent}, payable in {payments} payment(s).\n'
            '2. A security deposit of {deposit} is held and refunded after move-out less any deductions for damage beyond fair wear and tear and unpaid amounts.\n'
            '3. The tenant uses the premises for the agreed purpose only, keeps them in good condition and does not sublet without written consent.\n'
            '4. Either party gives {notice_days} days written notice before the end of the lease when it is not renewed. Registration: {registration}.\n'
            '5. Utilities and consumables are paid by the tenant unless stated otherwise; service charges follow the schedule attached.\n'
            '6. Any dispute is settled under the laws of the country of the property.')
LEASE_AR = ('1. يؤجر المالك الوحدة {unit} في {building} إلى {tenant} من {start} إلى {end} بإيجار سنوي قدره {rent} يُدفع على {payments} دفعة.\n'
            '2. يُحتفظ بتأمين قدره {deposit} ويُرد بعد الإخلاء بعد خصم أي أضرار تتجاوز الاستعمال العادي والمبالغ غير المسددة.\n'
            '3. يستعمل المستأجر العين للغرض المتفق عليه فقط ويحافظ عليها ولا يؤجرها من الباطن دون موافقة خطية.\n'
            '4. يُخطر كل طرف الآخر كتابةً قبل {notice_days} يوماً من انتهاء العقد في حال عدم التجديد. التسجيل: {registration}.\n'
            '5. يدفع المستأجر فواتير الخدمات ما لم يُنص على خلاف ذلك.\n6. تُسوّى أي نزاعات وفق قوانين بلد العقار.')
SPA_EN = ('1. The seller sells {unit} in {building} to {buyer} for {price}, paid as per the payment plan.\n2. Ownership passes on completion when the full price is paid and the title is transferred; '
          'handover is planned for {handover}.\n3. The buyer inspects the unit at handover; snags noted are rectified by the seller within 30 days.\n4. Transfer fees are paid as per the law of the country of the property.')
DT = [{'code': 'LEASE_EN', 'name': 'Lease clauses (English)', 'kind': 'lease', 'country': '', 'language': 'en', 'body': LEASE_EN},
      {'code': 'LEASE_AR', 'name': 'Lease clauses (Arabic)', 'kind': 'lease', 'country': '', 'language': 'ar', 'body': LEASE_AR},
      {'code': 'SPA_EN', 'name': 'Sale agreement clauses (English)', 'kind': 'spa', 'country': '', 'language': 'en', 'body': SPA_EN},
      {'code': 'RECEIPT_EN', 'name': 'Receipt note', 'kind': 'receipt', 'country': '', 'language': 'en', 'body': 'Received with thanks from {name} the sum of {amount} on {date}. This receipt is valid subject to clearance of the cheque / transfer.'},
      {'code': 'STATEMENT_EN', 'name': 'Owner statement note', 'kind': 'statement', 'country': '', 'language': 'en', 'body': 'Net amount {net} is transferred to your registered bank account within 5 working days of this statement. Please report any difference within 14 days.'}]
MT = [{'code': 'REM_BEFORE_EMAIL', 'event': 'reminder_before', 'channel': 'email', 'language': 'en', 'subject': 'Payment due on {due} – {number}', 'body': 'Dear {name},\n\nYour payment of {amount} for {unit} is due on {due}. You can pay online in the tenant portal: {link}\n\nThank you,\n{company}'},
      {'code': 'REM_BEFORE_SMS', 'event': 'reminder_before', 'channel': 'sms', 'language': 'en', 'subject': '', 'body': '{company}: {amount} for {unit} is due on {due}. Pay in the portal: {link}'},
      {'code': 'REM_AFTER_EMAIL', 'event': 'reminder_after', 'channel': 'email', 'language': 'en', 'subject': 'Payment overdue – {number}', 'body': 'Dear {name},\n\nYour payment of {amount} for {unit} was due on {due} and is still open. Please settle it now to avoid a late fee.\n\n{company}'},
      {'code': 'REM_AFTER_WA', 'event': 'reminder_after', 'channel': 'whatsapp', 'language': 'en', 'subject': '', 'body': 'Hello {name}, your payment of {amount} for {unit} was due on {due}. Please pay today: {link} – {company}'},
      {'code': 'BOUNCE_EMAIL', 'event': 'cheque_bounced', 'channel': 'email', 'language': 'en', 'subject': 'Cheque {number} returned unpaid', 'body': 'Dear {name},\n\nYour cheque {number} for {amount} was returned by the bank. Please contact us within 3 days to replace it.\n\n{company}'},
      {'code': 'RENEWAL_EMAIL', 'event': 'renewal', 'channel': 'email', 'language': 'en', 'subject': 'Your lease ends on {due}', 'body': 'Dear {name},\n\nYour lease of {unit} ends on {due}. Please tell us in the portal whether you would like to renew.\n\n{company}'},
      {'code': 'RECEIPT_EMAIL', 'event': 'receipt', 'channel': 'email', 'language': 'en', 'subject': 'Payment received – {number}', 'body': 'Dear {name},\n\nThank you, we received {amount}. The receipt is in the portal.\n\n{company}'},
      {'code': 'DOC_EMAIL', 'event': 'document_expiry', 'channel': 'email', 'language': 'en', 'subject': 'Document expiring: {number}', 'body': 'Dear {name},\n\nYour document {number} expires on {due}. Please send us the renewed copy.\n\n{company}'}]


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
            'inspection_template': IT[:1], 'doc_template': DT[:1] + DT[3:], 'message_template': MT[:3],
        }},
    'property_management': {
        'label': 'Property management', 'description': 'Residential and commercial unit types, the usual lease and sale charges (deposit, fees, parking, '
                                                       'chiller, bounced-cheque fee …) and move-in / handover checklists. Works with ZEO CRM and Maintenance.',
        'highlights': ['13 unit types', '19 charge types', 'Move-in and sale handover checklists', 'Inspection templates, lease / SPA clauses (EN / AR), message templates'],
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
                          ('TRANSFER', 'Title transfer fee', 'fee', 0, False), ('LATE_FEE', 'Late payment fee (automatic)', 'fee', 0, False),
                          ('BOUNCE_FEE', 'Bounced cheque fee (automatic)', 'fee', 0, False), ('LEASING_FEE', 'Leasing fee (owner)', 'fee', 5, False),
                          ('UTILITY', 'Utility consumption', 'utility', 5, False), ('CANCEL', 'Cancellation fee', 'fee', 0, False)]),
            'inspection_template': IT, 'doc_template': DT, 'message_template': MT,
            'checklist': cl([('KEYS', 'Keys and access cards handed over', 'both'), ('METERS', 'Water and electricity meter readings recorded', 'both'),
                             ('UTIL', 'Utility account moved to the new occupant', 'both'), ('PHOTOS', 'Condition photos taken', 'both'),
                             ('RULES', 'Building rules and emergency contacts given', 'both'), ('PARK', 'Parking bay allocated', 'both'),
                             ('REG', 'Lease registered with the authority', 'lease'), ('CHEQUES', 'All post-dated cheques received', 'lease'),
                             ('FINALPAY', 'Final payment received', 'sale'), ('NOC', 'Developer no-objection certificate', 'sale'),
                             ('TITLE', 'Title deed transferred', 'sale'), ('SNAG', 'Snag walk-through done with the buyer', 'sale')]),
        }},
}
