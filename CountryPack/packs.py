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
    'IN': {   # v1.31.0
        'name': 'India', 'country_code': 'IN', 'currency': 'INR',
        'labels': _l(national_id='Aadhaar', national_id_no='Aadhaar number', national_id_expiry='Aadhaar (no expiry)',
                     work_permit='PAN', work_permit_no='PAN', work_permit_expiry='PAN (no expiry)',
                     labour_contract='Appointment letter', labour_authority='Labour Commissioner', residence_visa='Employment visa (foreign nationals)',
                     visa_uid='FRRO registration no.', visa_file_no='Visa number', wps='Salary bank transfer', wps_file='Bank salary file',
                     employer_id='EPFO establishment code', routing_code='IFSC code', gratuity='Gratuity (Payment of Gratuity Act)',
                     end_of_service='Gratuity (15 days per year after 5 years)', nationals='Indian', nationals_plural='Indian nationals',
                     state='State', states='States / union territories', insurance_card='ESIC / health card', tax_id='GSTIN'),
        'features': {'national_id': True, 'work_permit': True, 'labour_contract': False, 'residence_visa': False, 'wps': False,
                     'wps_sif': False, 'gratuity': True, 'nationals_reports': False, 'states': True},
        'nationality_words': ['INDIA'], 'nationality_exact': ['IND'],
        'document_types': ['Aadhaar', 'PAN card', 'Passport', 'UAN / PF', 'ESIC card'],
        'id_format': 'aadhaar',
    },
    'GEN': {
        'name': 'Other country (neutral)', 'country_code': '', 'currency': '',
        'labels': dict(NEUTRAL_LABELS), 'features': dict(GEN_FEATURES),
        'nationality_words': [], 'nationality_exact': [],
        'document_types': ['National ID', 'Passport'],
        'id_format': None,
    },
}
PACK_ORDER = ['AE', 'SA', 'QA', 'OM', 'BH', 'KW', 'IN', 'GEN']

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


# v1.26.0 – the company country is stored as free text (Core.cntry_mstr.country_code) and live data holds
# "UAE", "ARE", "971" or an empty code with the name "United Arab Emirates". Taking the first two letters
# gave "UA" / "AR" / "97" -> GEN, so UAE companies lost WPS, gratuity, visas and holidays. Resolve ISO-2
# from the code (ISO-2, ISO-3, dialling code, common short forms) and fall back to the country name.
_ISO_ALIASES = {
    'AE': ('AE', 'ARE', 'UAE', 'U.A.E', 'U.A.E.', '971', '+971', 'UNITED ARAB EMIRATES', 'EMIRATES', 'THE UNITED ARAB EMIRATES',
           'دولة الإمارات العربية المتحدة', 'الإمارات العربية المتحدة', 'الإمارات'),
    'SA': ('SA', 'SAU', 'KSA', '966', '+966', 'SAUDI ARABIA', 'KINGDOM OF SAUDI ARABIA', 'SAUDI', 'المملكة العربية السعودية', 'السعودية'),
    'QA': ('QA', 'QAT', '974', '+974', 'QATAR', 'STATE OF QATAR', 'قطر'),
    'OM': ('OM', 'OMN', '968', '+968', 'OMAN', 'SULTANATE OF OMAN', 'عمان', 'سلطنة عمان'),
    'BH': ('BH', 'BHR', '973', '+973', 'BAHRAIN', 'KINGDOM OF BAHRAIN', 'البحرين'),
    'KW': ('KW', 'KWT', '965', '+965', 'KUWAIT', 'STATE OF KUWAIT', 'الكويت'),
    'IN': ('IN', 'IND', '91', '+91', 'INDIA', 'REPUBLIC OF INDIA', 'BHARAT', 'भारत'),
}
_ALIAS_TO_ISO = {a: iso for iso, names in _ISO_ALIASES.items() for a in names}


def iso2(code=None, name=None):
    """ISO-2 code of a country from its stored code and / or name ('' when unknown)."""
    for raw in (code, name):
        v = ' '.join(str(raw or '').replace('_', ' ').split()).upper()
        if not v:
            continue
        if v in _ALIAS_TO_ISO:
            return _ALIAS_TO_ISO[v]
        if v.startswith('UNITED ARAB EMIRATES'):
            return 'AE'
    v = str(code or '').strip().upper()
    return v if len(v) == 2 and v.isalpha() else ''


def iso2_of(country):
    """ISO-2 code of a Core.cntry_mstr row (or None)."""
    if country is None:
        return ''
    return iso2(getattr(country, 'country_code', ''), getattr(country, 'country_name', ''))


def pack_for_code(country_code, country_name=None):
    cc = iso2(country_code, country_name)
    return cc if cc in PACKS and cc != 'GEN' else 'GEN'


def pack_for_country(country):
    return pack_for_code(getattr(country, 'country_code', '') if country is not None else '',
                         getattr(country, 'country_name', '') if country is not None else '')


# ---------------------------------------------------------------- v1.31.0 company / branch registration captions per pack
# keys = CompanyProfile fields; 'extra' = country-specific registrations kept in CompanyProfile.extra_regs
REG_NEUTRAL = {
    'licence_no': 'Business registration / licence no.', 'authority': 'Registering authority', 'establishment_no': 'Labour-authority establishment no.',
    'tax_id': 'Tax ID', 'corporate_tax_no': 'Corporate income tax no.', 'pension_reg_no': 'Social security employer no.',
    'insurance_reg_no': 'Other scheme registration', 'chamber_no': 'Chamber of commerce no.', 'extra': [],
}
REGISTRATIONS = {
    'AE': {'licence_no': 'Trade licence no.', 'authority': 'Licensing authority (DED / free zone)', 'establishment_no': 'Establishment card no. (MoHRE / immigration)',
           'tax_id': 'TRN (VAT)', 'corporate_tax_no': 'Corporate tax registration no. (FTA)', 'pension_reg_no': 'GPSSA employer registration no.',
           'insurance_reg_no': 'ILOE (unemployment insurance) group policy no.', 'chamber_no': 'Chamber of commerce membership no.',
           'extra': [{'key': 'immigration_file', 'label': 'Immigration establishment file no.'}, {'key': 'mohre_company_no', 'label': 'MoHRE company number'}]},
    'SA': {'licence_no': 'Commercial registration (CR) no.', 'authority': 'Ministry of Commerce / MISA licence', 'establishment_no': 'MHRSD establishment no. (Qiwa)',
           'tax_id': 'VAT registration no. (ZATCA)', 'corporate_tax_no': 'Zakat / income tax no. (ZATCA)', 'pension_reg_no': 'GOSI subscription no.',
           'insurance_reg_no': 'Medical insurance policy no. (CCHI)', 'chamber_no': 'Chamber of commerce no.',
           'extra': [{'key': 'unified_national_no', 'label': 'Unified national number (700…)'}, {'key': 'muqeem', 'label': 'Muqeem / Absher establishment no.'}]},
    'QA': {'licence_no': 'Commercial registration (CR) no.', 'authority': 'Ministry of Commerce and Industry / QFC', 'establishment_no': 'Establishment card (computer card) no.',
           'tax_id': 'Tax identification no. (TIN)', 'corporate_tax_no': 'Corporate income tax no. (GTA)', 'pension_reg_no': 'GRSIA employer no.',
           'insurance_reg_no': 'Health insurance policy no.', 'chamber_no': 'Qatar Chamber no.',
           'extra': [{'key': 'trade_licence', 'label': 'Trade licence no. (municipality)'}, {'key': 'labour_file', 'label': 'Ministry of Labour establishment ID'}]},
    'OM': {'licence_no': 'Commercial registration (CR) no.', 'authority': 'Ministry of Commerce, Industry & Investment Promotion', 'establishment_no': 'Ministry of Labour establishment no.',
           'tax_id': 'VAT registration no. (OTA)', 'corporate_tax_no': 'Tax card no.', 'pension_reg_no': 'PASI / Social Protection Fund employer no.',
           'insurance_reg_no': 'Health insurance policy no.', 'chamber_no': 'OCCI membership no.',
           'extra': [{'key': 'municipality_licence', 'label': 'Municipality licence no.'}]},
    'BH': {'licence_no': 'Commercial registration (CR) no.', 'authority': 'Ministry of Industry and Commerce (Sijilat)', 'establishment_no': 'LMRA establishment no.',
           'tax_id': 'VAT account no. (NBR)', 'corporate_tax_no': '–', 'pension_reg_no': 'SIO employer no.',
           'insurance_reg_no': 'Health insurance policy no.', 'chamber_no': 'Bahrain Chamber no.', 'extra': []},
    'KW': {'licence_no': 'Commercial licence no.', 'authority': 'Ministry of Commerce and Industry', 'establishment_no': 'PAM establishment file no.',
           'tax_id': '– (no VAT)', 'corporate_tax_no': 'Tax card no. (foreign companies)', 'pension_reg_no': 'PIFSS employer no.',
           'insurance_reg_no': 'Health insurance policy no.', 'chamber_no': 'Kuwait Chamber no.',
           'extra': [{'key': 'civil_id_company', 'label': 'Company civil ID (PACI)'}]},
    'IN': {'licence_no': 'CIN (Companies Act) / registration no.', 'authority': 'Registrar of Companies (MCA) / Shops & Establishments', 'establishment_no': 'Shops & Establishments registration no.',
           'tax_id': 'GSTIN', 'corporate_tax_no': 'PAN', 'pension_reg_no': 'EPFO establishment code',
           'insurance_reg_no': 'ESIC employer code', 'chamber_no': 'Udyam / MSME registration no.',
           'extra': [{'key': 'tan', 'label': 'TAN (TDS)'}, {'key': 'pt_registration', 'label': 'Professional tax registration (PTRC / PTEC)'},
                     {'key': 'lin', 'label': 'Labour Identification Number (Shram Suvidha)'}, {'key': 'lwf', 'label': 'Labour Welfare Fund registration'},
                     {'key': 'factory_licence', 'label': 'Factory licence no.'}]},
    'GEN': dict(REG_NEUTRAL),
}


def registrations_for(pack):
    d = dict(REG_NEUTRAL)
    d.update(REGISTRATIONS.get(pack, {}))
    return d


# ---------------------------------------------------------------- v1.31.0 statutory payroll schemes per pack (PayrollPlus.StatutoryScheme)
# Rates as published at the time of writing – every value can be changed in Payroll → Statutory schemes. Verify before the first run.
VERIFY = 'Statutory rate as published – verify against the current law before the first payroll run.'
STATUTORY = {
    'AE': [
        {'code': 'gpssa', 'name': 'GPSSA pension (UAE nationals)', 'authority': 'GPSSA', 'kind': 'percent', 'applies_to': 'nationals', 'base': 'basic_housing_transport',
         'min_base': 3000, 'max_base': 70000, 'employee_rate': 11, 'employer_rate': 12.5,
         'law_reference': 'Federal Decree-Law 57/2023 (employees registered from 31 Oct 2023: 11 % employee, 15 % employer of which 2.5 % paid by the government); older registrations 5 % / 12.5 %',
         'notes': 'The formula variable pension_employee_rate already applies 5 % to employees registered before 31 Oct 2023.'},
        {'code': 'iloe', 'name': 'ILOE unemployment insurance', 'authority': 'ILOE', 'kind': 'slab', 'applies_to': 'all', 'base': 'basic',
         'slabs': [{'to': 16000, 'employee': 5, 'employer': 0}, {'to': None, 'employee': 10, 'employer': 0}],
         'law_reference': 'Federal Decree-Law 13/2022: AED 5 / month (basic ≤ 16,000) or AED 10 / month; paid by the employee (often by the employer on their behalf)'},
    ],
    'SA': [
        {'code': 'gosi', 'name': 'GOSI annuities (Saudi nationals)', 'authority': 'GOSI', 'kind': 'percent', 'applies_to': 'nationals', 'base': 'basic_housing_transport',
         'min_base': 1500, 'max_base': 45000, 'employee_rate': 9, 'employer_rate': 9,
         'law_reference': 'Social Insurance Law; new entrants from 3 July 2024 move in 0.5 % steps to 11 % / 11 % (Royal Decree M/273)'},
        {'code': 'gosi_oh', 'name': 'GOSI occupational hazards', 'authority': 'GOSI', 'kind': 'percent', 'applies_to': 'all', 'base': 'basic_housing_transport',
         'min_base': 400, 'max_base': 45000, 'employee_rate': 0, 'employer_rate': 2, 'law_reference': 'Occupational hazards branch: employer 2 % for Saudis and expatriates'},
        {'code': 'saned', 'name': 'SANED unemployment insurance (Saudi nationals)', 'authority': 'GOSI', 'kind': 'percent', 'applies_to': 'nationals', 'base': 'basic_housing_transport',
         'min_base': 1500, 'max_base': 45000, 'employee_rate': 0.75, 'employer_rate': 0.75, 'law_reference': 'SANED: 0.75 % employee + 0.75 % employer'},
    ],
    'QA': [
        {'code': 'grsia', 'name': 'GRSIA pension (Qatari nationals)', 'authority': 'GRSIA', 'kind': 'percent', 'applies_to': 'nationals', 'base': 'basic_housing_transport',
         'employee_rate': 7, 'employer_rate': 14, 'law_reference': 'Law No. 1 of 2022 on social insurance: employee 7 %, employer 14 % of the pension salary (basic + social + housing allowance)'},
    ],
    'OM': [
        {'code': 'pasi', 'name': 'Social Protection Fund – insurance (Omani nationals)', 'authority': 'Social Protection Fund (ex-PASI)', 'kind': 'percent', 'applies_to': 'nationals',
         'base': 'basic_housing_transport', 'min_base': 325, 'max_base': 3000, 'employee_rate': 7.5, 'employer_rate': 11.5,
         'law_reference': 'Social Protection Law (RD 52/2023): employee 7 % pension + 0.5 % job security; employer 10.5 % pension + 1 % occupational injury (job security 1 %)'},
    ],
    'BH': [
        {'code': 'sio', 'name': 'SIO social insurance (Bahraini nationals)', 'authority': 'SIO', 'kind': 'percent', 'applies_to': 'nationals', 'base': 'basic_housing_transport',
         'employee_rate': 7, 'employer_rate': 15, 'law_reference': 'Law 32/2022: employer share rises 1 % a year from 2023 to 20 % in 2028; unemployment insurance 1 % each included in the SIO return'},
        {'code': 'sio_exp', 'name': 'SIO work injury (expatriates)', 'authority': 'SIO', 'kind': 'percent', 'applies_to': 'expats', 'base': 'basic_housing_transport',
         'employee_rate': 1, 'employer_rate': 3, 'law_reference': 'Expatriates: employer 3 % work injury; employee 1 % unemployment insurance'},
    ],
    'KW': [
        {'code': 'pifss', 'name': 'PIFSS social security (Kuwaiti nationals)', 'authority': 'PIFSS', 'kind': 'percent', 'applies_to': 'nationals', 'base': 'basic_housing_transport',
         'max_base': 2750, 'employee_rate': 8, 'employer_rate': 11.5, 'law_reference': 'Law 61/1976 as amended: employee 8 % (incl. 0.5 % unemployment), employer 11.5 %, salary ceiling KWD 2,750'},
    ],
    'IN': [
        {'code': 'epf', 'name': 'Employees’ Provident Fund (EPF)', 'authority': 'EPFO', 'kind': 'percent', 'applies_to': 'all', 'base': 'basic_da',
         'max_base': 15000, 'employee_rate': 12, 'employer_rate': 12,
         'law_reference': 'EPF & MP Act 1952: 12 % employee, 12 % employer (8.33 % EPS + 3.67 % EPF) on basic + DA up to ₹ 15,000 (higher by option); employer also pays EDLI 0.5 % and admin 0.5 %',
         'notes': 'Remove the wage ceiling (max base) if the company contributes on the full basic.'},
        {'code': 'epf_admin', 'name': 'EPF admin + EDLI charges (employer)', 'authority': 'EPFO', 'kind': 'percent', 'applies_to': 'all', 'base': 'basic_da',
         'max_base': 15000, 'employee_rate': 0, 'employer_rate': 1, 'law_reference': 'EDLI 0.5 % + administrative charges 0.5 % (min ₹ 500 / month per establishment)'},
        {'code': 'esi', 'name': 'Employees’ State Insurance (ESI)', 'authority': 'ESIC', 'kind': 'percent', 'applies_to': 'all', 'base': 'gross',
         'eligible_gross_max': 21000, 'employee_rate': 0.75, 'employer_rate': 3.25, 'law_reference': 'ESI Act 1948: gross wage ≤ ₹ 21,000 (₹ 25,000 for disabled): employee 0.75 %, employer 3.25 %'},
        {'code': 'pt', 'name': 'Professional tax (state slabs – Maharashtra default)', 'authority': 'State government', 'kind': 'slab', 'applies_to': 'all', 'base': 'gross',
         'slabs': [{'to': 7500, 'employee': 0, 'employer': 0}, {'to': 10000, 'employee': 175, 'employer': 0}, {'to': None, 'employee': 200, 'employer': 0}],
         'law_reference': 'Maharashtra State Tax on Professions Act (₹ 300 in February; women earning ≤ ₹ 25,000 exempt). Replace the slabs with your state’s.'},
        {'code': 'tds', 'name': 'Income tax (TDS – new regime FY 2025-26)', 'authority': 'Income Tax Department', 'kind': 'annual_slab', 'applies_to': 'all', 'base': 'gross',
         'standard_deduction': 75000, 'rebate_income_limit': 1200000, 'cess_rate': 4,
         'slabs': [{'to': 400000, 'rate': 0}, {'to': 800000, 'rate': 5}, {'to': 1200000, 'rate': 10}, {'to': 1600000, 'rate': 15}, {'to': 2000000, 'rate': 20}, {'to': 2400000, 'rate': 25}, {'to': None, 'rate': 30}],
         'law_reference': 'Finance Act 2025, section 115BAC: standard deduction ₹ 75,000, rebate u/s 87A up to ₹ 12,00,000 taxable income, 4 % health & education cess. Monthly TDS = yearly tax ÷ 12 on the annualised salary; declarations and other income are not considered.'},
    ],
    'GEN': [],
}


def statutory_for(pack):
    return [dict(x) for x in STATUTORY.get(pack, [])]
