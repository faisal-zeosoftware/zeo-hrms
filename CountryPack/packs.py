"""v1.14.0 – Country packs: captions, features and defaults per country.

AE is the reference (= the behaviour and captions ZEO HRMS had before country packs). The other packs only carry facts we
are sure of; anything uncertain stays neutral and switched off, and every caption / feature can be changed by the company
administrator (Settings → Country settings). Rules (weekend, gratuity …) come from the OrgStructure country policy rule sets.
"""

LABEL_KEYS = [
    'national_id', 'national_id_no', 'national_id_expiry',
    'work_permit', 'work_permit_no', 'work_permit_expiry',
    'labour_contract', 'labour_authority',
    'residence_visa', 'visa_uid', 'visa_file_no',
    'wps', 'wps_file', 'employer_id', 'routing_code',
    'gratuity', 'end_of_service',
    'nationals', 'nationals_plural', 'state', 'states',
    'insurance_card', 'tax_id',
]

FEATURE_KEYS = ['national_id', 'work_permit', 'labour_contract', 'residence_visa', 'wps', 'wps_sif', 'gratuity', 'nationals_reports', 'states']

# plain-English names of the keys (Country settings screen)
LABEL_TITLES = {
    'national_id': 'National / resident ID', 'national_id_no': 'ID number', 'national_id_expiry': 'ID expiry',
    'work_permit': 'Work permit', 'work_permit_no': 'Work permit number', 'work_permit_expiry': 'Work permit expiry',
    'labour_contract': 'Labour contract', 'labour_authority': 'Labour authority',
    'residence_visa': 'Residence visa', 'visa_uid': 'Visa UID / ID number', 'visa_file_no': 'Visa file number',
    'wps': 'Wage protection', 'wps_file': 'Wage protection file', 'employer_id': 'Employer ID (labour authority)', 'routing_code': 'Bank routing code',
    'gratuity': 'Gratuity', 'end_of_service': 'End of service benefit',
    'nationals': 'National (one person)', 'nationals_plural': 'Nationals (people)', 'state': 'State / region', 'states': 'States / regions',
    'insurance_card': 'Medical insurance card', 'tax_id': 'Tax registration number',
}
FEATURE_TITLES = {
    'national_id': ('National / resident ID', 'ID number, issue and expiry fields on the employee file.'),
    'work_permit': ('Work permit', 'Labour card / work permit number and expiry.'),
    'labour_contract': ('Labour contract', 'Contract fields of the labour authority (for example MOHRE in the UAE).'),
    'residence_visa': ('Residence visa', 'Visa UID, file number and visa expiry.'),
    'wps': ('Wage protection', 'Salary protection fields and reports.'),
    'wps_sif': ('WPS SIF file', 'The UAE WPS SIF salary file, employer unique ID and bank routing code (UAE only).'),
    'gratuity': ('Gratuity', 'End-of-service gratuity is calculated in payroll, reports and final settlement.'),
    'nationals_reports': ('Nationals in reports', 'Nationals / expatriates figures and the nationals share in reports.'),
    'states': ('States / regions', 'State / region pick list on addresses and branches.'),
}

AE_LABELS = {
    'national_id': 'Emirates ID', 'national_id_no': 'Emirates ID no.', 'national_id_expiry': 'Emirates ID expiry',
    'work_permit': 'Labour card', 'work_permit_no': 'Labour card no.', 'work_permit_expiry': 'Labour card expiry',
    'labour_contract': 'MOHRE contract', 'labour_authority': 'MOHRE',
    'residence_visa': 'Residence visa', 'visa_uid': 'UID number', 'visa_file_no': 'Visa file no.',
    'wps': 'WPS', 'wps_file': 'WPS SIF file', 'employer_id': 'Employer unique ID (MOHRE)', 'routing_code': 'Bank routing code',
    'gratuity': 'Gratuity', 'end_of_service': 'End of service gratuity',
    'nationals': 'Emirati', 'nationals_plural': 'Emiratis', 'state': 'Emirate', 'states': 'Emirates',
    'insurance_card': 'Medical insurance card', 'tax_id': 'TRN',
}
AE_FEATURES = {k: True for k in FEATURE_KEYS}

# neutral captions: any country without its own pack, and the base of the GCC packs
NEUTRAL_LABELS = {
    'national_id': 'National ID', 'national_id_no': 'National ID no.', 'national_id_expiry': 'National ID expiry',
    'work_permit': 'Work permit', 'work_permit_no': 'Work permit no.', 'work_permit_expiry': 'Work permit expiry',
    'labour_contract': 'Employment contract', 'labour_authority': 'Labour authority',
    'residence_visa': 'Residence permit', 'visa_uid': 'Visa ID number', 'visa_file_no': 'Visa file no.',
    'wps': 'Salary protection', 'wps_file': 'Salary file', 'employer_id': 'Employer ID', 'routing_code': 'Bank routing code',
    'gratuity': 'Gratuity', 'end_of_service': 'End of service benefit',
    'nationals': 'National', 'nationals_plural': 'Nationals', 'state': 'State / region', 'states': 'States / regions',
    'insurance_card': 'Medical insurance card', 'tax_id': 'Tax ID',
}
GEN_FEATURES = {'national_id': True, 'work_permit': False, 'labour_contract': False, 'residence_visa': False, 'wps': False,
                'wps_sif': False, 'gratuity': False, 'nationals_reports': False, 'states': True}
GCC_FEATURES = {'national_id': True, 'work_permit': True, 'labour_contract': False, 'residence_visa': False, 'wps': True,
                'wps_sif': False, 'gratuity': True, 'nationals_reports': True, 'states': True}


def _l(**kw):
    return dict(NEUTRAL_LABELS, **kw)


PACKS = {
    'AE': {
        'name': 'United Arab Emirates', 'country_code': 'AE', 'currency': 'AED',
        'labels': dict(AE_LABELS), 'features': dict(AE_FEATURES),
        'nationality_words': ['EMIRAT'], 'nationality_exact': ['UAE', 'UNITED ARAB EMIRATES'],
        'document_types': ['Emirates ID', 'Passport'],
        'id_format': 'emirates_id',
    },
    'SA': {
        # Iqama (residents) / national ID (Saudis): 10 digits, 1… for Saudis, 2… for residents; Qiwa contracts; Mudad for wage protection;
        # end-of-service award: Labour Law arts. 84–85 (OrgStructure rule set SA-LL-2005)
        'name': 'Saudi Arabia', 'country_code': 'SA', 'currency': 'SAR',
        'labels': _l(national_id='Iqama / National ID', national_id_no='Iqama / National ID no.', national_id_expiry='Iqama expiry',
                     labour_contract='Qiwa contract', labour_authority='HRSD', wps='Wage protection (Mudad)', wps_file='Mudad salary file',
                     gratuity='End of service award', end_of_service='End of service award (Labour Law arts. 84–85)',
                     nationals='Saudi', nationals_plural='Saudis', state='Region', states='Regions', tax_id='VAT no.'),
        'features': dict(GCC_FEATURES, labour_contract=True),
        'nationality_words': ['SAUDI'], 'nationality_exact': ['KSA'],
        'document_types': ['Iqama / National ID', 'Passport'],
        'id_format': 'iqama',
    },
    'QA': {
        'name': 'Qatar', 'country_code': 'QA', 'currency': 'QAR',
        'labels': _l(national_id='Qatar ID (QID)', national_id_no='QID no.', national_id_expiry='QID expiry', labour_authority='Ministry of Labour',
                     wps='WPS', nationals='Qatari', nationals_plural='Qataris', state='Municipality', states='Municipalities'),
        'features': dict(GCC_FEATURES),
        'nationality_words': ['QATAR'], 'nationality_exact': [],
        'document_types': ['Qatar ID (QID)', 'Passport'],
        'id_format': None,
    },
    'OM': {
        'name': 'Oman', 'country_code': 'OM', 'currency': 'OMR',
        'labels': _l(labour_authority='Ministry of Labour', wps='WPS', nationals='Omani', nationals_plural='Omanis',
                     state='Governorate', states='Governorates', tax_id='VAT no.'),
        'features': dict(GCC_FEATURES),
        'nationality_words': ['OMAN'], 'nationality_exact': [],
        'document_types': ['National ID', 'Passport'],
        'id_format': None,
    },
    'BH': {
        'name': 'Bahrain', 'country_code': 'BH', 'currency': 'BHD',
        'labels': _l(national_id='CPR', national_id_no='CPR no.', national_id_expiry='CPR expiry', labour_authority='LMRA', wps='WPS',
                     nationals='Bahraini', nationals_plural='Bahrainis', state='Governorate', states='Governorates', tax_id='VAT no.'),
        'features': dict(GCC_FEATURES),
        'nationality_words': ['BAHRAIN'], 'nationality_exact': [],
        'document_types': ['CPR card', 'Passport'],
        'id_format': None,
    },
    'KW': {
        'name': 'Kuwait', 'country_code': 'KW', 'currency': 'KWD',
        'labels': _l(national_id='Civil ID', national_id_no='Civil ID no.', national_id_expiry='Civil ID expiry', labour_authority='PAM', wps='WPS',
                     nationals='Kuwaiti', nationals_plural='Kuwaitis', state='Governorate', states='Governorates'),
        'features': dict(GCC_FEATURES),
        'nationality_words': ['KUWAIT'], 'nationality_exact': [],
        'document_types': ['Civil ID', 'Passport'],
        'id_format': None,
    },
    'GEN': {
        'name': 'Other country (neutral)', 'country_code': '', 'currency': '',
        'labels': dict(NEUTRAL_LABELS), 'features': dict(GEN_FEATURES),
        'nationality_words': [], 'nationality_exact': [],
        'document_types': ['National ID', 'Passport'],
        'id_format': None,
    },
}
PACK_ORDER = ['AE', 'SA', 'QA', 'OM', 'BH', 'KW', 'GEN']

# v1.15.1 – public holidays per pack (CountryPack/holidays.py: fixed national days + expected Islamic holidays)
from .holidays import holidays_for as _holidays_for  # noqa: E402
for _code in PACKS:
    PACKS[_code]['holidays'] = _holidays_for(_code)

# ISO 4217 minor units that are not 2 (everything else: 2)
CURRENCY_DECIMALS = {'BHD': 3, 'KWD': 3, 'OMR': 3, 'JOD': 3, 'IQD': 3, 'LYD': 3, 'TND': 3,
                     'JPY': 0, 'KRW': 0, 'VND': 0, 'CLP': 0, 'ISK': 0, 'UGX': 0, 'PYG': 0, 'XOF': 0, 'XAF': 0, 'RWF': 0}

# UAE leave types are loaded by LeavePolicy.setup (Office / Labour policies); other packs get a neutral minimum
NEUTRAL_LEAVE_TYPES = [
    # name, code, category, paid/unpaid, half day, description
    ('Annual Leave', 'AL', 'annual', 'paid', True, 'Paid annual leave – set the days in the leave policy'),
    ('Sick Leave', 'SL', 'sick', 'paid', True, 'Sick leave – set the days and pay in the leave policy'),
    ('Unpaid Leave', 'UL', 'unpaid', 'unpaid', True, 'By agreement with the employer'),
]


def pack_for_code(country_code):
    cc = (country_code or '').strip().upper()[:2]
    return cc if cc in PACKS and cc != 'GEN' else 'GEN'
