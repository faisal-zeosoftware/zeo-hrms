"""v1.73.11 – Document Center phase 6: industry packs.

A pack is a ready set for a kind of business: document categories (with their code, expiry rule, reminder days, retention and own
fields), checklists (the documents each employee, project, site … must have) and letter templates. "Install" only ADDS what is
missing (categories by code, checklists and templates by name) – it never changes what the company has. Templates come as drafts:
an administrator reads and publishes them. The retention years and reminder days are suggestions to review, not the law.

Every pack builds on "General" (the company's own papers and the employee file); the industry keys are those of the HSE packs.
"""
from django.db import transaction

from Translations.i18n import tr

from . import models as Mo


def C(code, name, expiry=False, years=None, alert=None, fields=None, parent=None):
    return {'code': code, 'name': name, 'expiry': expiry, 'years': years, 'alert': alert or ([90, 60, 30] if expiry else []), 'fields': fields or [],
            'parent': parent}


def F(key, label, ftype='text', options=None):
    return {'key': key, 'label': label, 'field_type': ftype, **({'options': options} if options else {})}


NO = F('doc_no', 'Document number')
AUTH = F('authority', 'Issued by')

GENERAL = {
    'label': 'General – company papers and the employee file',
    'description': 'Trade licence, establishment cards, tax registrations, contracts and insurance; the employee file (passport, visa, '
                   'Emirates ID, labour contract, work permit, certificates) with a joining checklist and the usual HR letters.',
    'categories': [
        C('TL', 'Trade licence', True, 10, [90, 60, 30, 15], [NO, AUTH, F('activities', 'Activities', 'textarea')]),
        C('EST', 'Establishment card', True, 10, [60, 30, 15], [NO, AUTH]),
        C('MOA', 'Memorandum of association', years=10, fields=[F('version_date', 'Version date', 'date')]),
        C('CHMB', 'Chamber of commerce certificate', True, 5, [60, 30], [NO]),
        C('VAT', 'VAT registration certificate', years=10, fields=[F('trn', 'Tax registration number')]),
        C('CT', 'Corporate tax registration', years=10, fields=[F('trn', 'Tax registration number')]),
        C('TEN', 'Tenancy contract (Ejari)', True, 7, [90, 60, 30], [NO, F('premises', 'Premises'), F('rent', 'Yearly rent', 'decimal')]),
        C('INS', 'Insurance policy', True, 7, [60, 30, 15], [NO, F('insurer', 'Insurer'), F('cover', 'Cover', 'dropdown', ['Property', 'Liability', 'Medical', 'Vehicle', 'Other'])]),
        C('CONT', 'Contract with a customer or supplier', True, 7, [90, 30], [F('party', 'Other party'), F('value', 'Value', 'decimal')]),
        C('BG', 'Bank guarantee', True, 7, [60, 30, 15], [NO, F('bank', 'Bank'), F('amount', 'Amount', 'decimal')]),
        C('POA', 'Power of attorney', True, 10, [60, 30], [F('attorney', 'Attorney')]),
        C('PP', 'Passport', True, 5, [180, 90, 30], [NO, F('nationality', 'Nationality')]),
        C('VISA', 'Residence visa', True, 5, [90, 60, 30, 15], [NO, F('sponsor', 'Sponsor')]),
        C('EID', 'Emirates ID', True, 5, [60, 30, 15], [NO]),
        C('LC', 'Labour contract', True, 5, [60, 30], [NO, F('contract_type', 'Contract type', 'dropdown', ['Unlimited', 'Limited'])]),
        C('WP', 'Work permit', True, 5, [60, 30, 15], [NO]),
        C('HIC', 'Health insurance card', True, 5, [30, 15], [NO, F('insurer', 'Insurer')]),
        C('EDU', 'Educational certificate', years=10, fields=[F('degree', 'Degree'), F('attested', 'Attested', 'checkbox')]),
        C('MED', 'Medical fitness certificate', True, 5, [30], []),
        C('OFFER', 'Offer letter', years=10),
        C('HRL', 'HR letter', years=5, fields=[F('purpose', 'Purpose')]),
        C('PHOTO', 'Photo', years=5),
    ],
    'checklists': [
        {'name': 'New employee file', 'provider': 'employee', 'items': [('PP', True, True), ('VISA', True, True), ('EID', True, True), ('LC', True, True),
                                                                        ('WP', True, True), ('HIC', True, True), ('EDU', False, False), ('PHOTO', True, False)]},
        {'name': 'Business partner papers', 'provider': 'partner', 'items': [('TL', True, True), ('VAT', False, False), ('CONT', False, False)]},
    ],
    'templates': [
        {'name': 'Salary certificate', 'category': 'HRL', 'provider': 'employee', 'questions': [('addressed_to', 'Addressed to', 'text', True), ('purpose', 'Purpose', 'text', True)],
         'body': '<h2 style="text-align:center">Salary certificate</h2><p>To: {{ answer.addressed_to }}</p><p>This is to certify that '
                 '<b>{{ employee.name }}</b> (employee no. {{ employee.code }}), {{ employee.nationality }} national, works with {{ company }} '
                 'as {{ employee.designation }} since {{ employee.joined }}.</p><p>This certificate is issued at the request of the employee for '
                 '{{ answer.purpose }}, without any responsibility on the company.</p><p>{{ today }}</p><p>For {{ company }}</p>'},
        {'name': 'Experience certificate', 'category': 'HRL', 'provider': 'employee', 'questions': [('last_day', 'Last working day', 'date', True)],
         'body': '<h2 style="text-align:center">Experience certificate</h2><p>This is to certify that <b>{{ employee.name }}</b> worked with '
                 '{{ company }} as {{ employee.designation }} in {{ employee.department }} from {{ employee.joined }} to {{ answer.last_day }}.</p>'
                 '<p>We wish them every success.</p><p>{{ today }}</p><p>For {{ company }}</p>'},
        {'name': 'No objection letter', 'category': 'HRL', 'provider': 'employee', 'questions': [('addressed_to', 'Addressed to', 'text', True), ('for_what', 'No objection to', 'textarea', True)],
         'body': '<h2 style="text-align:center">No objection letter</h2><p>To: {{ answer.addressed_to }}</p><p>{{ company }} has no objection to '
                 '{{ answer.for_what }} for our employee <b>{{ employee.name }}</b> ({{ employee.designation }}, employee no. {{ employee.code }}).</p>'
                 '<p>{{ today }}</p><p>For {{ company }}</p>'},
        {'name': 'Employment verification', 'category': 'HRL', 'provider': 'employee', 'questions': [('addressed_to', 'Addressed to', 'text', True)],
         'body': '<h2 style="text-align:center">Employment verification</h2><p>To: {{ answer.addressed_to }}</p><p>We confirm that '
                 '<b>{{ employee.name }}</b> is employed by {{ company }} as {{ employee.designation }} since {{ employee.joined }} and works at '
                 '{{ employee.branch }}.</p><p>{{ today }}</p><p>For {{ company }}</p>'},
    ],
}

IND = {
    'construction': ('Construction and engineering', 'Building permits, civil defence approvals, contractor classification, project insurance, '
                     'performance bonds, drawings and method statements; a project document checklist.',
                     [C('BPM', 'Building permit', True, 10, [60, 30], [NO, AUTH, F('plot', 'Plot number')]),
                      C('CDA', 'Civil defence approval', True, 10, [60, 30], [NO]),
                      C('CCL', 'Contractor classification certificate', True, 10, [90, 30], [NO, F('grade', 'Grade')]),
                      C('CAR', 'Contractor all risks insurance', True, 10, [60, 30], [NO, F('insurer', 'Insurer')]),
                      C('PB', 'Performance bond', True, 10, [60, 30, 15], [NO, F('amount', 'Amount', 'decimal')]),
                      C('DWG', 'Drawing', fields=[F('drawing_no', 'Drawing number'), F('revision', 'Revision')], years=10),
                      C('MS', 'Method statement', years=10, fields=[F('activity', 'Activity')])],
                     [{'name': 'Project documents', 'provider': 'project', 'items': [('BPM', True, True), ('CDA', True, True), ('CAR', True, True), ('PB', False, True), ('MS', False, False)]}]),
    'manufacturing': ('Manufacturing', 'Industrial licence, environmental permit, ISO certificates, product certificates, machine certificates and '
                      'material safety data sheets.',
                      [C('IND', 'Industrial licence', True, 10, [90, 30], [NO, AUTH]), C('ENV', 'Environmental permit', True, 10, [60, 30], [NO]),
                       C('ISO', 'ISO certificate', True, 10, [90, 30], [NO, F('standard', 'Standard', 'dropdown', ['ISO 9001', 'ISO 14001', 'ISO 45001', 'ISO 22000', 'Other'])]),
                       C('PCERT', 'Product conformity certificate', True, 10, [60, 30], [NO, F('product', 'Product')]),
                       C('MCERT', 'Machine inspection certificate', True, 5, [30, 15], [NO, F('machine', 'Machine')]),
                       C('SDS', 'Safety data sheet', years=5, fields=[F('chemical', 'Chemical')])],
                      [{'name': 'Asset certificates', 'provider': 'asset', 'items': [('MCERT', True, True)]}]),
    'healthcare': ('Hospitals and healthcare', 'Facility licence, professional licences of the staff, malpractice insurance, life-support '
                   'certificates, credentialing and equipment calibration.',
                   [C('HFL', 'Health facility licence', True, 10, [90, 60, 30], [NO, AUTH]),
                    C('PRL', 'Professional licence', True, 10, [90, 60, 30], [NO, AUTH, F('speciality', 'Speciality')]),
                    C('MAL', 'Malpractice insurance', True, 10, [60, 30], [NO, F('insurer', 'Insurer')]),
                    C('BLS', 'Life support certificate (BLS / ACLS)', True, 5, [60, 30], [F('course', 'Course', 'dropdown', ['BLS', 'ACLS', 'PALS', 'NRP'])]),
                    C('CRED', 'Credentialing and privileging', True, 10, [90, 30], []),
                    C('CAL', 'Equipment calibration certificate', True, 5, [30, 15], [F('equipment', 'Equipment')])],
                   [{'name': 'Clinical staff file', 'provider': 'employee', 'items': [('PRL', True, True), ('MAL', True, True), ('BLS', True, True), ('CRED', False, True)]}]),
    'oil_gas': ('Oil and gas', 'Concession and operating permits, well and plant certificates, third-party inspection reports, contractor '
                'pre-qualification and offshore survival certificates.',
                [C('OPP', 'Operating permit', True, 10, [90, 30], [NO, AUTH]), C('TPI', 'Third-party inspection report', True, 10, [60, 30], [F('equipment', 'Equipment')]),
                 C('PQ', 'Contractor pre-qualification', True, 5, [60, 30], [F('contractor', 'Contractor')]),
                 C('BOSIET', 'Offshore survival certificate (BOSIET / FOET)', True, 5, [60, 30], []),
                 C('H2S', 'H2S safety certificate', True, 5, [60, 30], [])],
                [{'name': 'Site worker certificates', 'provider': 'employee', 'items': [('BOSIET', True, True), ('H2S', True, True)]}]),
    'education': ('Schools and education', 'School permit, teacher licences, attested degrees, child-protection training, police clearance '
                  'and student transport permits.',
                  [C('SCH', 'School permit', True, 10, [90, 30], [NO, AUTH]), C('TL2', 'Teaching licence', True, 10, [90, 30], [NO, F('subject', 'Subject')]),
                   C('CPT', 'Child protection training', True, 5, [60, 30], []), C('PCC', 'Police clearance certificate', True, 5, [60, 30], []),
                   C('BUS', 'School transport permit', True, 5, [60, 30], [NO])],
                  [{'name': 'Teacher file', 'provider': 'employee', 'items': [('TL2', True, True), ('EDU', True, False), ('CPT', True, True), ('PCC', True, True)]}]),
    'retail': ('Retail and hospitality', 'Shop licences, food and health permits, signage permits, mall lease, staff food-handler cards.',
               [C('FHP', 'Food establishment permit', True, 5, [60, 30], [NO, AUTH]), C('SIGN', 'Signboard permit', True, 5, [60, 30], [NO]),
                C('FHC', 'Food handler card', True, 5, [30, 15], []), C('MALL', 'Mall lease agreement', True, 7, [90, 60, 30], [F('unit', 'Unit')])],
               [{'name': 'Food handler file', 'provider': 'employee', 'items': [('FHC', True, True), ('MED', True, True)]}]),
    'office': ('Offices, IT and finance', 'Software licences, data-protection records, audit reports, NDAs and IT service contracts.',
               [C('SWL', 'Software licence', True, 5, [60, 30], [F('product', 'Product'), F('seats', 'Seats', 'decimal')]),
                C('NDA', 'Non-disclosure agreement', True, 7, [30], [F('party', 'Other party')]),
                C('AUD', 'Audit report', years=10, fields=[F('year', 'Financial year', 'decimal')]),
                C('DPA', 'Data processing agreement', True, 7, [60, 30], [F('party', 'Other party')])],
               [{'name': 'Staff confidentiality', 'provider': 'employee', 'items': [('NDA', True, False)]}]),
    'mining': ('Mining and quarrying', 'Mining licence, blasting permits, ground control plans and shot-firer licences.',
               [C('MINL', 'Mining licence', True, 10, [90, 30], [NO, AUTH]), C('BLAST', 'Blasting permit', True, 5, [30, 15], [NO]),
                C('SHOT', 'Shot-firer licence', True, 5, [60, 30], [NO])],
               [{'name': 'Blasting crew', 'provider': 'employee', 'items': [('SHOT', True, True)]}]),
    'logistics': ('Logistics and warehousing', 'Customs and freight licences, vehicle registrations, driving licences, dangerous-goods '
                  'certificates and warehouse permits.',
                  [C('CUS', 'Customs licence', True, 5, [90, 30], [NO]), C('VREG', 'Vehicle registration', True, 5, [30, 15], [F('plate', 'Plate number')]),
                   C('DL', 'Driving licence', True, 5, [60, 30], [NO, F('class', 'Class')]), C('DG', 'Dangerous goods certificate', True, 5, [60, 30], []),
                   C('WHP', 'Warehouse permit', True, 5, [60, 30], [NO])],
                  [{'name': 'Driver file', 'provider': 'employee', 'items': [('DL', True, True), ('DG', False, True)]},
                   {'name': 'Vehicle papers', 'provider': 'asset', 'items': [('VREG', True, True), ('INS', True, True)]}]),
    'hospitality': ('Hotels and hospitality', 'Tourism licence and classification, pool and lifeguard certificates, food permits and '
                    'guest-facing staff training.',
                    [C('TOUR', 'Tourism licence', True, 10, [90, 30], [NO, AUTH]), C('CLASS', 'Hotel classification certificate', True, 5, [90, 30], [F('stars', 'Stars', 'decimal')]),
                     C('LIFE', 'Lifeguard certificate', True, 5, [30, 15], []), C('FHP', 'Food establishment permit', True, 5, [60, 30], [NO, AUTH])],
                    [{'name': 'Pool staff', 'provider': 'employee', 'items': [('LIFE', True, True)]}]),
    'facilities': ('Facilities management', 'Service contracts, maintenance certificates (lifts, fire systems), electrical competency '
                   'and access permits for customer sites.',
                   [C('AMC', 'Maintenance contract', True, 7, [90, 30], [F('client', 'Client'), F('value', 'Value', 'decimal')]),
                    C('LIFT', 'Lift inspection certificate', True, 5, [30, 15], [F('building', 'Building')]),
                    C('FIRE', 'Fire system certificate', True, 5, [30, 15], []), C('AEP', 'Electrical competency certificate', True, 5, [60, 30], [])],
                   [{'name': 'Building certificates', 'provider': 'building', 'items': [('LIFT', True, True), ('FIRE', True, True), ('INS', True, True)]}]),
    'agriculture': ('Agriculture and farming', 'Farm licence, pesticide permits, veterinary and phytosanitary certificates.',
                    [C('FARM', 'Farm licence', True, 10, [90, 30], [NO]), C('PEST', 'Pesticide permit', True, 5, [60, 30], [NO]),
                     C('PHYTO', 'Phytosanitary / veterinary certificate', True, 3, [15], [NO])], []),
    'food': ('Food and beverage production', 'Food safety certification, product registrations, lab test reports and halal certificates.',
             [C('FSC', 'Food safety certificate (HACCP / ISO 22000)', True, 5, [90, 30], [NO]),
              C('PREG', 'Product registration', True, 5, [90, 30], [NO, F('product', 'Product')]),
              C('HALAL', 'Halal certificate', True, 5, [60, 30], [NO]), C('LAB', 'Laboratory test report', years=5, fields=[F('sample', 'Sample')])],
             [{'name': 'Food handler file', 'provider': 'employee', 'items': [('MED', True, True)]}]),
    'marine': ('Marine and ports', 'Vessel registration and class certificates, seafarer certificates and port permits.',
               [C('VSL', 'Vessel registration', True, 10, [90, 30], [NO, F('vessel', 'Vessel')]), C('CLS', 'Class certificate', True, 10, [90, 30], [NO]),
                C('STCW', 'Seafarer certificate (STCW)', True, 5, [90, 30], [NO]), C('PORT', 'Port permit', True, 5, [30], [NO])],
               [{'name': 'Crew file', 'provider': 'employee', 'items': [('STCW', True, True), ('MED', True, True)]}]),
    'aviation': ('Aviation', 'Operator and maintenance approvals, licences of the staff, airside passes and aircraft certificates.',
                 [C('AOC', 'Operator / maintenance approval', True, 10, [90, 30], [NO, AUTH]), C('AML', 'Aviation licence', True, 10, [90, 30], [NO, F('type_rating', 'Type rating')]),
                  C('AIR', 'Airside pass', True, 5, [30, 15], [NO]), C('ACERT', 'Aircraft certificate', True, 10, [90, 30], [F('registration', 'Aircraft registration')])],
                 [{'name': 'Licensed staff', 'provider': 'employee', 'items': [('AML', True, True), ('AIR', True, True)]}]),
    'telecom': ('Telecom', 'Regulator licences, frequency permits, tower and site leases, climbing certificates.',
                [C('TRA', 'Telecom licence', True, 10, [90, 30], [NO, AUTH]), C('FREQ', 'Frequency permit', True, 5, [60, 30], [NO]),
                 C('SITE', 'Site lease', True, 10, [90, 30], [F('site', 'Site')]), C('CLIMB', 'Tower climbing certificate', True, 5, [30], [])],
                [{'name': 'Tower technicians', 'provider': 'employee', 'items': [('CLIMB', True, True)]}]),
    'utilities': ('Utilities and energy', 'Generation and distribution licences, connection agreements, high-voltage authorisations.',
                  [C('GEN', 'Generation / distribution licence', True, 10, [90, 30], [NO, AUTH]), C('CONN', 'Connection agreement', True, 10, [90, 30], []),
                   C('HV', 'High-voltage authorisation', True, 5, [60, 30], [])],
                  [{'name': 'Authorised persons', 'provider': 'employee', 'items': [('HV', True, True)]}]),
    'waste': ('Waste management', 'Waste transport and treatment permits, manifests and vehicle permits.',
              [C('WTP', 'Waste transport / treatment permit', True, 5, [60, 30], [NO, AUTH]), C('MAN', 'Waste manifest', years=5, fields=[F('load', 'Load')]),
               C('VREG', 'Vehicle registration', True, 5, [30, 15], [F('plate', 'Plate number')])],
              [{'name': 'Waste vehicles', 'provider': 'asset', 'items': [('VREG', True, True), ('WTP', True, True)]}]),
    'cleaning': ('Cleaning and pest control', 'Pest control licence, chemical approvals and service contracts.',
                 [C('PCL', 'Pest control licence', True, 5, [60, 30], [NO]), C('CHEM', 'Chemical approval', True, 5, [60, 30], []),
                  C('AMC', 'Maintenance contract', True, 7, [90, 30], [F('client', 'Client'), F('value', 'Value', 'decimal')])],
                 [{'name': 'Pest control staff', 'provider': 'employee', 'items': [('PCL', False, True)]}]),
    'laboratory': ('Laboratories', 'Accreditation (ISO 17025), method validations, instrument calibration and biosafety approvals.',
                   [C('ACC', 'Accreditation certificate (ISO 17025)', True, 5, [90, 30], [NO]), C('VAL', 'Method validation', years=10, fields=[F('method', 'Method')]),
                    C('CAL', 'Equipment calibration certificate', True, 5, [30, 15], [F('equipment', 'Equipment')]),
                    C('BIO', 'Biosafety approval', True, 5, [60, 30], [])],
                   [{'name': 'Instrument certificates', 'provider': 'asset', 'items': [('CAL', True, True)]}]),
    'shipyard': ('Shipyards', 'Yard approvals, dry-dock certificates, hot-work and gas-free certificates, welder qualifications.',
                 [C('YARD', 'Yard approval', True, 10, [90, 30], [NO]), C('GASF', 'Gas-free certificate', True, 1, [1], [F('vessel', 'Vessel')]),
                  C('WQ', 'Welder qualification', True, 5, [60, 30], [F('process', 'Process')])],
                 [{'name': 'Welders', 'provider': 'employee', 'items': [('WQ', True, True)]}]),
    'data_center': ('Data centres', 'Tier and security certifications, customer SLAs, access lists and maintenance contracts.',
                    [C('TIER', 'Tier / security certificate', True, 5, [90, 30], [NO, F('standard', 'Standard')]),
                     C('SLA', 'Service level agreement', True, 7, [90, 30], [F('customer', 'Customer')]),
                     C('AMC', 'Maintenance contract', True, 7, [90, 30], [F('client', 'Client'), F('value', 'Value', 'decimal')])], []),
}


def packs():
    out = {'general': GENERAL}
    for k, (label, desc, cats, cls) in IND.items():
        out[k] = {'label': label, 'description': desc, 'categories': cats, 'checklists': cls, 'templates': []}
    return out


def _fields(fs):
    """the pack's fields in the designer's shape (the 1.70.1 names number/select become decimal/dropdown)"""
    from . import fields as FD
    out, seen = [], {d['key'] for d in FD.doc_fields()}
    for i, f in enumerate(fs):
        try:
            d = FD.clean_def(f, i, seen)
        except FD.Refused:
            continue
        seen.add(d['key'])
        out.append(d)
    return out


def status():
    have_codes = set(Mo.DocCategory.objects.values_list('code', flat=True))
    have_cl = set(Mo.DocChecklist.objects.values_list('name', flat=True))
    have_tpl = set(Mo.DocTemplate.objects.values_list('name', flat=True))
    out = []
    for k, p in packs().items():
        cats = [c['code'] for c in p['categories']]
        n_in = sum(1 for c in cats if c in have_codes) + sum(1 for x in p['checklists'] if x['name'] in have_cl) + sum(1 for t in p['templates'] if t['name'] in have_tpl)
        total = len(cats) + len(p['checklists']) + len(p['templates'])
        out.append({'key': k, 'label': p['label'], 'description': p['description'], 'categories': [c['name'] for c in p['categories']],
                    'checklists': [x['name'] for x in p['checklists']], 'templates': [t['name'] for t in p['templates']],
                    'installed': n_in, 'total': total, 'state': 'installed' if n_in == total else 'partly' if n_in else 'not'})
    return out


@transaction.atomic
def install(who, key):
    from .services import Refused
    all_ = packs()
    if key not in all_:
        raise Refused(tr('Unknown industry pack.'))
    if not who.dms_admin:
        raise Refused(tr('Only a Document Center administrator installs industry packs.'), 403)
    added = {'categories': [], 'checklists': [], 'templates': []}
    keys = ['general'] + ([key] if key != 'general' else [])
    for k in keys:   # every pack builds on General (its checklists name General categories such as Passport)
        p = all_[k]
        for c in p['categories']:
            if Mo.DocCategory.objects.filter(code=c['code']).exists():
                continue
            name = c['name']
            if Mo.DocCategory.objects.filter(parent=None, name=name).exists():   # the company has its own with this name: keep it
                continue
            Mo.DocCategory.objects.create(name=name, code=c['code'], description=f'Industry pack: {p["label"]}'[:255], expiry_required=False,
                                          retention_years=c['years'], alert_days=c['alert'], fields=_fields(c['fields']),
                                          number_pattern='{cat}-{yyyy}-{seq:4}', retention_start='expiry' if c['expiry'] else 'effective')
            added['categories'].append(name)
        codes = dict(Mo.DocCategory.objects.values_list('code', 'pk'))
        for cl in p['checklists']:
            if Mo.DocChecklist.objects.filter(provider=cl['provider'], name=cl['name']).exists():
                continue
            items = []
            for code, req, exp in cl['items']:
                if code in codes:
                    cname = Mo.DocCategory.objects.get(pk=codes[code]).name
                    items.append({'key': code.lower(), 'label': cname, 'category_id': codes[code], 'required': req, 'needs_expiry': exp, 'note': ''})
            if items:
                Mo.DocChecklist.objects.create(name=cl['name'], provider=cl['provider'], items=items, created_by_id=who.uid,
                                               description=f'Industry pack: {p["label"]}'[:255])
                added['checklists'].append(cl['name'])
        for t in p['templates']:
            if Mo.DocTemplate.objects.filter(name=t['name']).exists():
                continue
            qs = [{'key': q[0], 'label': q[1], 'field_type': q[2], 'options': [], 'required': q[3]} for q in t['questions']]
            Mo.DocTemplate.objects.create(name=t['name'], provider=t['provider'], category_id=codes.get(t['category']), body=t['body'], questions=qs,
                                          created_by_id=who.uid)
            added['templates'].append(t['name'])
    from .services import log_act
    log_act(who, 'industry_pack', f'{key}: ' + ', '.join(f'{len(v)} {k}' for k, v in added.items()))
    return added
