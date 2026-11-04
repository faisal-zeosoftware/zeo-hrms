# v1.62.11 (item 227): new module – bank salary file layouts (WPS SIF and the banks' / exchange houses' own layouts, KSA Mudad)
"""v1.62.11 (item 227) – pluggable bank salary files.

The base is the Central Bank (CBUAE) / Ministry WPS Salary Information File (SIF): one EDR line per employee and one SCR line
at the end, comma separated, CRLF, ASCII – exactly the file the existing Payroll › WPS file screen makes (the EDR rows come from
the same PayrollManagement.serializer.SIFSerializer.generate_sif_data, the SCR line is built the same way as SIFDataView, and the
text is joined with the same rules as wps.component.ts downloadSIFFile – a value that is empty or the number 0 is written empty).

On top of it a registry of layouts (LAYOUTS) and of the banks / exchange houses (AGENTS) an employer pays through. A layout is
"verified" only when it was checked against the bank's own published spec or sample (SOURCES, checked 03/11/2026); every other
bank uses the CBUAE SIF and is shown "own layout to be confirmed – send us the bank's spec or a sample file". No layout here is
invented: where a source leaves a point open it is listed in the layout's `open` notes.

A company keeps its bank file set-ups (which bank / layout pays which branches, employer reference, KSA establishment data) in
ZeoApps.CompanyOption key 'bank_file_profiles' (no new table), and a short log of the files made in key 'bank_file_log'.

Sources (all read 03/11/2026 – see SOURCES for the addresses):
 * MOL / CBUAE "Wages Protection System (WPS) Companies Responsibility Guide" WPS-CRG01 v3.0.0 (21/01/2010), as published by
   Habib Bank AG Zurich: EDR / SCR fields, ASCII, comma separated, CRLF after each record, file name = 13-digit employer ID +
   YYMMDDHHMMSS + .SIF, "CBUAE, in consultation with MOL, shall define the standards and specifications".
 * Mashreq "Guidelines to WPS File Format"; Dubai Islamic Bank "WPS File Format Reference Guide v1.1"; Emirates Islamic
   "businessONLINE Salary WPS file creation user guide": the same EDR / SCR layout (DIB and EI also describe the optional EVP
   record); ADIB "WPS MOHRE registration" V1 April 2026: two more EDR columns (justification, resident Yes / No);
   Commercial Bank of Dubai "WPS – user guide" (File Format Instructions – WPS Salary): CBD's own 13-column Excel template.
 * KSA: HRSD "WPS Wages File Specification" (bank wages file, proposed at the banks' workshop 14/05/2012) and the HRSD service
   page "Wage protection file upload service" (Mudad Business). The template Mudad itself offers is only inside the Mudad
   portal (not public) – the KSA layout is built from the HRSD field list and stays "to be confirmed".
"""
import calendar
import datetime
import io
import re
from decimal import Decimal

from django.apps import apps
from django.db import connection, transaction
from django.db.models import Q

from Translations.i18n import tr

M = apps.get_model
CHECKED = '2026-11-03'

# ------------------------------------------------------------------ sources (read 03/11/2026)
SOURCES = {
    'crg': ('MOL / CBUAE – WPS Companies Responsibility Guide WPS-CRG01 v3.0.0 (21/01/2010), published by Habib Bank AG Zurich',
            'https://www.habibbank.com/uae/downloads/HBZwps_crg.pdf'),
    'mashreq': ('Mashreq – Guidelines to WPS File Format', 'https://netbanking.mashreqbank.com/B001/WPS_File_Format_eng.html'),
    'dib': ('Dubai Islamic Bank – WPS File Format Reference Guide v1.1',
            'https://www.dib.ae/docs/default-source/wps-guidelines/wps-file-format-reference-guide_v1-1.pdf'),
    'ei': ('Emirates Islamic – businessONLINE Salary WPS file creation user guide',
           'https://www.emiratesislamic.ae/-/media/ei/pdfs/business-online/salary-and-utility-payments/ei_businessonline_salary_wps_file_creation_user_guide.pdf'),
    'adib': ('ADIB – WPS MOHRE registration guide V1 (April 2026)',
             'https://www.adib.ae/-/media/project/adib/adibsite/docs/corporate/infographics/wps-mohre-registration-guide.pdf'),
    'cbd': ('Commercial Bank of Dubai – WPS user guide (File Format Instructions – WPS Salary)',
            'https://cbd.ae/docs/librariesprovider2/payments_downloads/wps---user-guide'),
    'cbd_iserve': ('CBD iServe – Payroll file format and descriptions (file names, format list)',
                   'https://iserve.cbd.ae/support/solutions/articles/80000952792-payroll-file-format-and-descriptions'),
    'cbd_nonwps': ('CBD iServe – How to fill out a non-WPS salary file (CBD account holders)',
                   'https://iserve.cbd.ae/support/solutions/articles/80000942055-how-to-fill-out-a-non-wps-salary-file-cbd-account-holders-'),
    'denoms': ('United Arab Emirates dirham – banknotes 5 to 1000, coins 25 / 50 fils and 1 dirham (Wikipedia; PropertyFinder UAE coins guide)',
               'https://en.wikipedia.org/wiki/United_Arab_Emirates_dirham'),
    'enbd': ('Emirates NBD – Salary and pension payments leaflet (SIF "as mandated by the Central Bank", no field list)',
             'https://emiratesnbd.com/-/media/enbd/files/cib/leafts/salary_pension_payments_info.pdf'),
    'adcb': ('ADCB – Salary payments (SIF generation and validation, no field list)',
             'https://www.adcb.com/en/business/products-solutions/cash-management/salary-payments'),
    'fab': ('FAB – iPortal WPS upload demo (no field list)',
            'https://ibanking.bankfab.com/iportalweb/iportal/animationdemo/DemoContent/WPSUploadSummary.html'),
    'ansari': ('Al Ansari Exchange – WPS booklet Nov 2025 (SIF "in the format specified by Al Ansari Exchange", not published)',
               'https://alansariexchange.com/wp-content/uploads/2019/10/Updated%20WPS%20BOOKLET%20NOV-2025.pdf'),
    'lulu': ('LuLu Exchange – Wage / salary administrator (SIF uploaded on the My Pay portal, no field list)',
             'https://luluexchange.com/services/wage-salary-administrator/'),
    'hadi': ('Hadi Express Exchange – WPS registration booklet V8 (SIF "in accordance with the LRA prescribed format")',
             'https://hadi-exchange.webc.in/wp/wp-content/uploads/2022/07/WPS-Registration-Booklet-V8-optn-1.pdf'),
    'zoho_agents': ('Zoho Payroll – WPS-compliant banks in the UAE ("The SIF file format is standardised across all of them") – adviser, not official',
                    'https://www.zoho.com/en-ae/payroll/academy/compliance/wps-compliant-banks-in-uae.html'),
    'mol_agents': ('MOL – Wages Protection System guideline (agents "approved and authorized by the Central Bank of the UAE")',
                   'https://webapps.ilo.org/dyn/migpractice/docs/351/MoL_UAE_Guidelines_WPS.pdf'),
    'hrsd_spec': ('HRSD – WPS Wages File Specification (banks workshop 14/05/2012)',
                  'https://www.hrsd.gov.sa/sites/default/files/2017-06/WPS%20Wages%20File%20Technical%20Specification.pdf'),
    'hrsd_service': ('HRSD – Wage protection file upload service (Mudad Business, released 08/01/2024)',
                     'https://hrsd.gov.sa/en/ministry-services/services/wage-protection-file-upload-service'),
}


def src(*keys):
    return [{'key': k, 'title': SOURCES[k][0], 'url': SOURCES[k][1], 'checked': CHECKED} for k in keys]


# ------------------------------------------------------------------ layouts
EDR_FIELDS = ['Record type (EDR)', 'Employee unique ID (MOHRE person ID, 14)', 'Routing code of the agent (9)',
              'Employee account (IBAN 23 or salary card 16)', 'Pay start date (YYYY-MM-DD)', 'Pay end date (YYYY-MM-DD)',
              'Days in period', 'Income fixed component', 'Income variable component', 'Days on leave']
SCR_FIELDS = ['Record type (SCR)', 'Employer unique ID (13)', 'Routing code of the employer bank (9)', 'File creation date (YYYY-MM-DD)',
              'File creation time (HHMM)', 'Salary month (MMYYYY)', 'EDR count', 'Total salary (fixed + variable of all EDR)',
              'Payment currency (AED)', 'Employer reference (optional)']
EVP_FIELDS = ['Record type (EVP)', 'Employee unique ID (as in the EDR)', 'Routing code of the agent (as in the EDR)', 'Housing allowance',
              'Conveyance allowance', 'Medical allowance', 'Annual passage allowance', 'Overtime allowance', 'All other allowances',
              'Leave encashment']
CBD_COLUMNS = ['EmpId', 'Employee Name', 'Employee_Type', 'Salary', 'VariablePay', 'AccountNo', 'AGENT_BANK-RTN_CODE', 'MOL_PERSONID',
               'Sal_Month', 'Sal_Year', 'FromDate', 'ToDate', 'Leave']
KSA_HEADER = ['[DEST-ID] Establishment bank (SARIE bank ID, 4 letters)', '[ESTB-ID] Establishment ID (10 digits)',
              '[BANK-ACC] Establishment bank account', '[32A-CCY] Currency (SAR)', '[32A-VAL] Value date (YYYYMMDD)',
              '[32A-AMT] Total amount (sum of 32B-AMT)', '[D-DATE] Debit date (optional)', '[FILE-REF] File reference (unique)',
              '[FILE-REJCDE] Rejection code (bank use – empty)', '[MOL-ESTBID] MoL establishment ID (labour office + number)']
KSA_LINE = ['[32B-AMT] Net amount', '[59-ACC] Beneficiary IBAN', '[59-NAME] Beneficiary name', '[57-BANK] Beneficiary bank code',
            '[70-DET] Payment description', '[RET-CODE] Return code (bank use – empty)', '[MOL-BAS] Basic salary', '[MOL-HAL] Housing allowance',
            '[MOL-OEA] Other earnings', '[MOL-DED] Deductions', '[MOL-ID] Iqama / national ID (10 digits)', '[TRN-REF] (bank use – empty)',
            '[TRN-STATUS] (bank use – empty)', '[TRN-DATE] (bank use – empty)']

SIF_NAME = '13-digit employer ID + YYMMDDHHMMSS + .SIF (e.g. 0000001084512261103143000.SIF)'
LAYOUTS = {
    'cbuae_sif': {
        'name': 'Central Bank (CBUAE) WPS SIF', 'country': 'AE', 'state': 'verified', 'format': 'SIF (text)',
        'summary': 'One EDR line per employee, one SCR line at the end; comma separated, one record per line (CRLF). The same file as '
                   'Payroll › WPS file.',
        'records': [{'type': 'EDR', 'fields': EDR_FIELDS, 'per': 'employee'}, {'type': 'SCR', 'fields': SCR_FIELDS, 'per': 'file (last line)'}],
        'separator': ',', 'line_end': 'CRLF', 'file_name': SIF_NAME, 'header': 'none', 'trailer': 'SCR (control record): EDR count and total salary',
        'sources': src('crg', 'mashreq', 'dib', 'ei'),
        'open': ['The Employer reference (SCR field 10) is optional (CRG) – left empty unless entered in the set-up.'],
    },
    'sif_ref_none': {
        'name': 'CBUAE WPS SIF – employer reference "NONE"', 'country': 'AE', 'state': 'verified', 'format': 'SIF (text)', 'base': 'cbuae_sif',
        'summary': 'The CBUAE SIF; the Employer reference (SCR field 10) is "NONE" when no reference is entered, as Mashreq asks.',
        'records': [{'type': 'EDR', 'fields': EDR_FIELDS, 'per': 'employee'}, {'type': 'SCR', 'fields': SCR_FIELDS, 'per': 'file (last line)'}],
        'separator': ',', 'line_end': 'CRLF', 'file_name': SIF_NAME, 'header': 'none', 'trailer': 'SCR', 'sources': src('mashreq', 'crg'),
        'open': ['Mashreq’s page gives the employer ID once as 16 digits and twice as 13 – the 13 digits of the CRG are used.'],
    },
    'sif_evp': {
        'name': 'CBUAE WPS SIF with EVP lines', 'country': 'AE', 'state': 'verified', 'format': 'SIF (text)', 'base': 'cbuae_sif',
        'summary': 'The CBUAE SIF; when "EVP lines" is on, an EVP line (variable pay broken down) follows the EDR of each employee with '
                   'variable pay.',
        'records': [{'type': 'EDR', 'fields': EDR_FIELDS, 'per': 'employee'},
                    {'type': 'EVP', 'fields': EVP_FIELDS, 'per': 'employee with variable pay (optional)'},
                    {'type': 'SCR', 'fields': SCR_FIELDS, 'per': 'file (last line)'}],
        'separator': ',', 'line_end': 'CRLF', 'file_name': SIF_NAME, 'header': 'none', 'trailer': 'SCR', 'sources': src('dib', 'ei', 'crg'),
        'open': ['Whether the bank wants the EVP lines is a choice of the bank (Emirates Islamic: "only if variable amount is provided") – '
                 'they are off until switched on in the set-up.',
                 'Variable pay is split by the components’ payroll category (housing, transport / travel, medical, air ticket, overtime, '
                 'leave encashment); the rest is “All other allowances”.'],
    },
    'adib_mohre': {
        'name': 'ADIB WPS SIF – MOHRE registered (2 more EDR columns)', 'country': 'AE', 'state': 'verified', 'format': 'SIF (text)', 'base': 'cbuae_sif',
        'summary': 'The CBUAE SIF; each EDR line ends with two more columns: the justification of a salary change and "Yes" / "No" '
                   '(UAE resident) – for ADIB customers registered with MOHRE (ADIB, April 2026).',
        'records': [{'type': 'EDR', 'fields': EDR_FIELDS + ['Justification for any change in salary', 'Resident (Yes / No)'], 'per': 'employee'},
                    {'type': 'SCR', 'fields': SCR_FIELDS, 'per': 'file (last line)'}],
        'separator': ',', 'line_end': 'CRLF', 'file_name': SIF_NAME, 'header': 'none', 'trailer': 'SCR', 'sources': src('adib', 'crg'),
        'open': ['ADIB’s guide shows the two columns on the EDR line only – the SCR line is kept as in the CBUAE SIF.',
                 'A justification is asked for employees whose pay changed from their previous payslip; commas are removed from it '
                 '(the file has no quotes).'],
    },
    'cbd_xlsx': {
        'name': 'Commercial Bank of Dubai – WPS salary Excel', 'country': 'AE', 'state': 'verified', 'format': 'Excel (.xlsx), sheet "Sheet1"',
        'summary': 'CBD’s own template: one heading row and one row per employee in 13 columns; CBD makes the SIF from it.',
        'records': [{'type': 'Heading row', 'fields': CBD_COLUMNS, 'per': 'file (first row)'}, {'type': 'Employee row', 'fields': CBD_COLUMNS, 'per': 'employee'}],
        'separator': 'Excel cells', 'line_end': '–', 'file_name': 'not given by CBD – CBD_WPS_<employer ID>_<MMYYYY>.xlsx', 'header': 'heading row (must not be changed)',
        'trailer': 'none', 'sources': src('cbd', 'cbd_iserve'),
        'open': ['The guide names the sheet "Sheet1" but not the file type; .xlsx is made.',
                 'Employee_Type: N = first file through CBD-WPS, E = sent before, C = details changed since the last salary – worked out from the '
                 'files ZEO made for this set-up (the first file from ZEO marks everybody N).',
                 'Salary is the net pay including the variable pay (CBD).'],
    },
    'mudad_wages': {
        'name': 'KSA wage protection file (Mudad / HRSD)', 'country': 'SA', 'state': 'to_confirm', 'format': 'Text (.txt), TAB separated',
        'summary': 'The HRSD wages file fields: one establishment line, one line per employee, a "-" line at the end; amounts with a '
                   'decimal comma. The template Mudad offers inside its portal is not public – to be confirmed against it.',
        'records': [{'type': 'Establishment (header)', 'fields': KSA_HEADER, 'per': 'file (first line)'},
                    {'type': 'Employee', 'fields': KSA_LINE, 'per': 'employee'}, {'type': 'End of file', 'fields': ['-'], 'per': 'file (last line)'}],
        'separator': 'TAB', 'line_end': 'CRLF', 'file_name': 'no rule (HRSD: "no restriction on the file name") – MUDAD_<establishment>_<YYYYMM>.txt',
        'header': 'establishment line', 'trailer': '"-" line', 'sources': src('hrsd_spec', 'hrsd_service'),
        'open': ['The HRSD spec does not show whether the field tags are written in the file or how the header is laid out – one line of '
                 'values is made; to be confirmed with the Mudad template or the bank.',
                 'Mudad’s own upload template (Mudad Business › Upload files) is only inside the portal – send us a sample to verify.',
                 'Beneficiary bank code: the employee’s WPS agent field (Bank / WPS) when it holds the 4-letter SARIE code.'],
    },
}

FALLBACK_NOTE = ('Own layout to be confirmed – send us the bank’s spec or a sample file. Until then the file is the Central Bank WPS '
                 'SIF, the format MOHRE / CBUAE mandate for WPS (CRG).')

# ------------------------------------------------------------------ banks and exchange houses (the employer's WPS agent)
def _a(code, name, kind, layout='cbuae_sif', state='to_confirm', sources=(), note=''):
    return {'code': code, 'name': name, 'kind': kind, 'layout': layout, 'state': state, 'sources': src(*sources), 'note': note}


AGENTS = [
    _a('cbuae', 'Central Bank WPS SIF (any agent)', 'standard', 'cbuae_sif', 'verified', ('crg', 'mashreq', 'dib', 'ei'),
       'The base file of the WPS (MOHRE / CBUAE).'),
    _a('mashreq', 'Mashreq', 'bank', 'sif_ref_none', 'verified', ('mashreq',), 'Mashreq publishes the CBUAE SIF with "NONE" as employer reference.'),
    _a('dib', 'Dubai Islamic Bank (DIB)', 'bank', 'sif_evp', 'verified', ('dib',), 'DIB publishes the CBUAE SIF with the optional EVP record.'),
    _a('ei', 'Emirates Islamic', 'bank', 'sif_evp', 'verified', ('ei',), 'Emirates Islamic publishes the CBUAE SIF; EVP only with variable pay.'),
    _a('adib', 'Abu Dhabi Islamic Bank (ADIB)', 'bank', 'adib_mohre', 'verified', ('adib',),
       'ADIB (April 2026): two more EDR columns for customers registered with MOHRE.'),
    _a('cbd', 'Commercial Bank of Dubai (CBD)', 'bank', 'cbd_xlsx', 'verified', ('cbd', 'cbd_iserve'), 'CBD’s own Excel template (13 columns).'),
    _a('hbz', 'Habib Bank AG Zurich', 'bank', 'cbuae_sif', 'verified', ('crg',), 'Publishes the MOL / CBUAE guide (CRG) itself.'),
    _a('enbd', 'Emirates NBD', 'bank', sources=('enbd',), note='Says it takes the SIF "as mandated by the Central Bank" – no field list published.'),
    _a('fab', 'First Abu Dhabi Bank (FAB)', 'bank', sources=('fab',), note='WPS upload on FAB iPortal – no field list published.'),
    _a('adcb', 'Abu Dhabi Commercial Bank (ADCB)', 'bank', sources=('adcb',), note='Makes and validates the SIF – no field list published.'),
    _a('rakbank', 'RAKBANK', 'bank'),
    _a('nbf', 'National Bank of Fujairah (NBF)', 'bank'),
    _a('cbi', 'Commercial Bank International (CBI)', 'bank'),
    _a('uab', 'United Arab Bank', 'bank'),
    _a('nbq', 'National Bank of Umm Al Qaiwain', 'bank'),
    _a('ajman', 'Ajman Bank', 'bank'),
    _a('sib', 'Sharjah Islamic Bank', 'bank'),
    _a('bos', 'Bank of Sharjah', 'bank'),
    _a('almasraf', 'Al Masraf', 'bank'),
    _a('invest', 'InvestBank', 'bank'),
    _a('eib', 'Emirates Investment Bank', 'bank'),
    _a('hsbc', 'HSBC Bank Middle East', 'bank'),
    _a('citi', 'Citibank UAE', 'bank'),
    _a('scb', 'Standard Chartered UAE', 'bank'),
    _a('wio', 'Wio Bank', 'bank'),
    _a('other_bank', 'Other bank', 'bank'),
    _a('alansari', 'Al Ansari Exchange', 'exchange', sources=('ansari',),
       note='Processes the SIF "in the format specified by Al Ansari Exchange" (given to the employer, not published).'),
    _a('alfardan', 'Al Fardan Exchange', 'exchange'),
    _a('lulu', 'LuLu Exchange', 'exchange', sources=('lulu',), note='SIF uploaded on the My Pay portal – no field list published.'),
    _a('wizz', 'Wizz (formerly UAE Exchange)', 'exchange'),
    _a('hadi', 'Hadi Express Exchange', 'exchange', sources=('hadi',), note='SIF "in accordance with the LRA prescribed format"; its copy is given to the employer.'),
    _a('other_exchange', 'Other exchange house / WPS agent', 'exchange'),
    _a('mudad', 'Mudad (Saudi Arabia – wage protection)', 'platform', 'mudad_wages', 'to_confirm', ('hrsd_spec', 'hrsd_service'),
       'Mudad’s own template is only inside the portal – fields from the HRSD wages file spec.'),
]
AGENT = {a['code']: a for a in AGENTS}
ALL_AGENTS_NOTE = {
    'text': ('Checked: the CRG says the employer makes the salary file "in the mandated format" and its bank sends it into the WPS; an '
             'adviser (Zoho) says the SIF is standardised across agents. Not every agent takes an uploaded SIF as it is: CBD wants its own '
             'Excel, ADIB two more columns, Al Ansari and Hadi give the employer their own copy of the file. So the CBUAE SIF is the base '
             'every bank can be served with, and each bank’s own layout is confirmed with its spec or a sample.'),
    'sources': src('crg', 'mol_agents', 'zoho_agents', 'cbd', 'adib', 'ansari'),
}


def layout_public(code):
    L = dict(LAYOUTS[code]); L['code'] = code
    return L


def agent_public(a):
    x = dict(a)
    x['layout_name'] = LAYOUTS[a['layout']]['name']
    x['state_text'] = 'Verified' if a['state'] == 'verified' else 'Own layout to be confirmed'
    if a['state'] != 'verified' and a['layout'] == 'cbuae_sif':
        x['fallback'] = FALLBACK_NOTE
    return x


# ------------------------------------------------------------------ set-ups (CompanyOption)
KEY, LOG = 'bank_file_profiles', 'bank_file_log'


def _opt(key, lock=False):
    CO = M('ZeoApps', 'CompanyOption')
    qs = CO.objects.select_for_update() if lock else CO.objects
    o = qs.filter(key=key).first()
    return o or CO(key=key, value={})


def profiles():
    return list((_opt(KEY).value or {}).get('profiles') or [])


def save_profiles(fn):
    """fn(list) → (list, result) under a row lock (two HR users saving at the same time do not lose a set-up)."""
    with transaction.atomic():
        o = _opt(KEY, lock=True)
        v = dict(o.value or {})
        rows, res = fn(list(v.get('profiles') or []), v)
        v['profiles'] = rows
        o.value = v
        o.save()
    return res


def log_file(entry):
    with transaction.atomic():
        o = _opt(LOG, lock=True)
        v = dict(o.value or {})
        files = list(v.get('files') or [])
        files.append(entry)
        v['files'] = files[-500:]
        o.value = v
        o.save()


def file_log():
    return list((_opt(LOG).value or {}).get('files') or [])


def profile_for_branch(branch_id):
    rows = [p for p in profiles() if p.get('active', True)]
    for p in rows:
        if branch_id in (p.get('branch_ids') or []):
            return p
    return next((p for p in rows if not p.get('branch_ids')), None)


# ------------------------------------------------------------------ data of a run (UAE SIF family)
class FileError(Exception):
    def __init__(self, message, problems=None):
        super().__init__(message)
        self.message, self.problems = message, problems


def _tenant():
    from django_tenants.utils import get_tenant_model
    return get_tenant_model().objects.filter(schema_name=connection.schema_name).first()


def _now(tenant):
    import pytz
    tzn = 'UTC'
    if tenant and getattr(tenant, 'country', None) and getattr(tenant.country, 'timezone', None):
        tzn = tenant.country.timezone
    try:
        tz = pytz.timezone(tzn)
    except pytz.UnknownTimeZoneError:
        tz = pytz.UTC
    return datetime.datetime.now(tz=tz)


def sif_data(run, branch_ids=None, now=None, employee_ids=None):
    """The data of the WPS SIF of a run – the same rows and SCR as PayrollManagement.views.SIFDataView (v1.26.0 rules)."""
    from rest_framework import serializers as drf
    from PayrollManagement.serializer import SIFSerializer
    body = {'payroll_run_id': run.pk, 'branch_ids': list(branch_ids or [])}
    if employee_ids is not None:   # Payroll run › Payments: only the employees paid by WPS
        body['employee_ids'] = list(employee_ids) or [0]
    ser = SIFSerializer(data=body)
    if not ser.is_valid():
        raise FileError('Invalid payroll run.', ser.errors)
    try:
        edr, total, skipped = ser.generate_sif_data()
    except drf.ValidationError as e:
        raise FileError(str(e))
    tenant = _tenant()
    now = now or _now(tenant)
    employer = tenant.employer_unique_id.zfill(13) if tenant and tenant.employer_unique_id else '0' * 13
    routing = tenant.bank_routing_code if tenant and tenant.bank_routing_code else '0' * 9
    br = list(branch_ids or [])
    if len(br) == 1:   # as SIFDataView: a file for one branch with its own establishment uses its employer ID and bank
        bw = M('PayrollPlus', 'BranchWPS').objects.filter(branch_id=br[0]).first()
        if bw and bw.employer_unique_id:
            employer = bw.employer_unique_id.zfill(13)
        if bw and bw.bank_routing_code:
            routing = bw.bank_routing_code
    value = float(total)
    scr = {'Type': 'SCR', 'Person ID': employer, 'Routing Code': routing, 'IBAN Number': now.strftime('%Y-%m-%d'),
           'Pay Start Date': now.strftime('%H%M'), 'Pay End Date': f'{run.month:02d}{run.year}', 'Number of Days': len(edr),
           'Fixed Income': f'{value:.2f}', 'Variable Income': 'AED', 'Days on Leave': ''}
    warnings = []
    if employer == '0' * 13:
        warnings.append(tr('The WPS employer ID (MoHRE establishment number, 13 digits) is missing – enter it in Settings → Company (edit) '
                           'or per branch in Payroll → Setup → Payroll controls.'))
    if routing == '0' * 9:
        warnings.append(tr("The employer's bank routing code (9 digits) is missing – Settings → Company (edit)."))
    if skipped:
        warnings.append(tr('{0} employee(s) are not in the file – see the list below. Add the missing details in the employee file '
                           '(Bank / WPS, MoHRE person ID) or import them with the employee upload.', len(skipped)))
    # the payslip behind each EDR (EVP split, CBD columns, ADIB pay change)
    E = M('EmpManagement', 'emp_master')
    by_pid = {e.person_id: e for e in E.objects.filter(person_id__in=[r['Person ID'] for r in edr])}
    slips = {p.employee_id: p for p in run.payslips.filter(employee__in=by_pid.values()).prefetch_related('components__component')}
    extra = []
    for r in edr:
        e = by_pid.get(r['Person ID'])
        extra.append({'employee': e, 'payslip': slips.get(e.pk) if e else None})
    return {'edr': edr, 'scr': scr, 'skipped': skipped, 'warnings': warnings, 'extra': extra, 'now': now, 'employer': employer,
            'routing': routing, 'total': value}


# ------------------------------------------------------------------ renderers
def _js(v):
    """the value as wps.component.ts writes it: `x || ''` – empty for '', None and the number 0"""
    if v is None or v == '' or (isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and v == 0):
        return ''
    return str(v)


ROW_KEYS = ['Person ID', 'Routing Code', 'IBAN Number', 'Pay Start Date', 'Pay End Date', 'Number of Days', 'Fixed Income',
            'Variable Income', 'Days on Leave']


def _line(kind, r, more=()):
    return ','.join([kind] + [_js(r.get(k)) for k in ROW_KEYS] + list(more))


def _clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'[,\r\n"]+', ' ', str(s or ''))).strip()


EVP_MAP = {'housing_allowance': 0, 'hra': 0, 'transport_allowance': 1, 'travel_allowance': 1, 'medical_allowance': 2, 'air_ticket': 3,
           'overtime': 4, 'leave_encashment': 6}


def evp_split(ps, variable):
    """the variable pay of a payslip in the 7 EVP amounts (same components as the EDR variable income)"""
    amt = [Decimal('0')] * 7
    if ps is not None:
        for pc in ps.components.all():
            c = pc.component
            if c.show_in_payslip and c.component_type == 'addition' and c.component_value_type == 'variable':
                amt[EVP_MAP.get(c.payroll_category, 5)] += Decimal(str(pc.amount or 0))
    v = Decimal(str(variable))
    if sum(amt) != v:   # capped at the net pay (EDR rule) – the whole variable pay as "other"
        amt = [Decimal('0')] * 7
        amt[5] = v
    return [f'{x:.2f}' for x in amt]


def _scr(d, ref):
    s = dict(d['scr'])
    s['Days on Leave'] = ref
    return _line('SCR', s)


def render_sif(d, layout, prof):
    prof = prof or {}
    opts = prof.get('options') or {}
    ref = _clean(prof.get('employer_reference'))[:35]
    if layout == 'sif_ref_none' and not ref:
        ref = 'NONE'
    lines = []
    for r, x in zip(d['edr'], d['extra']):
        if layout == 'adib_mohre' and opts.get('mohre_columns', True):
            e = x['employee']
            just = _clean((d.get('justifications') or {}).get(getattr(e, 'emp_code', ''), ''))
            res = 'No' if getattr(e, 'emp_code', None) in set(opts.get('non_residents') or []) else (opts.get('resident_default') or 'Yes')
            lines.append(_line('EDR', r, [just, res]))
        else:
            lines.append(_line('EDR', r))
        if layout == 'sif_evp' and opts.get('evp') and Decimal(r['Variable Income']) > 0:
            lines.append(','.join(['EVP', _js(r['Person ID']), _js(r['Routing Code'])] + evp_split(x['payslip'], r['Variable Income'])))
    lines.append(_scr(d, ref))
    return '\r\n'.join(lines)


def sif_file_name(d):
    return f"{d['employer']}{d['now']:%y%m%d%H%M%S}.SIF"


def pay_changed(run, d):
    """ADIB: employees whose net pay differs from their previous payslip (they need a justification)"""
    P = M('PayrollManagement', 'Payslip')
    out = []
    for r, x in zip(d['edr'], d['extra']):
        e = x['employee']
        if e is None:
            continue
        prev = (P.objects.filter(employee=e).exclude(payroll_run=run).exclude(status__iexact='rejected')
                .filter(Q(payroll_run__year__lt=run.year) | Q(payroll_run__year=run.year, payroll_run__month__lt=run.month))
                .order_by('-payroll_run__year', '-payroll_run__month', '-pk').first())
        now_pay = Decimal(r['Fixed Income']) + Decimal(r['Variable Income'])
        if prev is not None and Decimal(str(prev.net_salary or 0)).quantize(Decimal('0.01')) != now_pay:
            out.append({'emp_code': e.emp_code, 'name': _ename(e), 'before': f'{Decimal(str(prev.net_salary or 0)):.2f}', 'now': f'{now_pay:.2f}'})
    return out


def _ename(e):
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code


def cbd_types(prof, d):
    """N / E / C from the files ZEO made for this set-up: N never sent, C account or routing code changed, E sent before"""
    last = {}
    for f in file_log():
        if f.get('profile') == (prof or {}).get('id') and f.get('layout') == 'cbd_xlsx':
            for emp, acct in (f.get('accounts') or {}).items():
                last[emp] = acct
    out = []
    for r, x in zip(d['edr'], d['extra']):
        code = getattr(x['employee'], 'emp_code', '')
        acct = f"{r['Routing Code']}|{r['IBAN Number']}"
        out.append('N' if code not in last else ('C' if last[code] != acct else 'E'))
    return out


def cbd_rows(run, d, prof):
    types = cbd_types(prof, d)
    rows = []
    for r, x, t in zip(d['edr'], d['extra'], types):
        e = x['employee']
        sal = Decimal(r['Fixed Income']) + Decimal(r['Variable Income'])
        var = Decimal(r['Variable Income'])
        dmy = lambda s: f'{s[8:10]}/{s[5:7]}/{s[0:4]}'
        rows.append([getattr(e, 'emp_code', ''), _ename(e) if e else '', t, f'{sal:.2f}', f'{var:.2f}' if var else '', r['IBAN Number'],
                     r['Routing Code'], r['Person ID'], f'{run.month:02d}', str(run.year), dmy(r['Pay Start Date']), dmy(r['Pay End Date']),
                     r['Days on Leave'] if r['Days on Leave'] not in ('0', '') else ''])
    return rows


def render_cbd(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(CBD_COLUMNS)
    for r in rows:
        ws.append(r)
        for c in ws[ws.max_row]:
            c.number_format = '@'
            c.data_type = 's'
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


# ------------------------------------------------------------------ KSA (Mudad / HRSD wages file)
def _sar(x):
    return f'{Decimal(str(x)).quantize(Decimal("0.01")):.2f}'.replace('.', ',')


def ksa_data(run, branch_ids, prof, now=None):
    tenant = _tenant()
    now = now or _now(tenant)
    opts = (prof or {}).get('options') or {}
    slips = run.payslips.select_related('employee').prefetch_related('components__component').order_by('employee__emp_code')
    if branch_ids:
        slips = slips.filter(employee__emp_branch_id__in=branch_ids)
    EI = M('EmployeeProfile', 'EmployeeIdentity')
    BX = M('EmployeeProfile', 'BankExtra')
    ids = {i.employee_id: i for i in EI.objects.filter(employee_id__in=slips.values('employee_id'))}
    rows, skipped, total = [], [], Decimal('0')
    for ps in slips:
        e = ps.employee
        if (ps.status or '').lower() == 'rejected':
            skipped.append({'emp_code': e.emp_code, 'reason': 'Payslip was rejected'}); continue
        if ps.approvals.filter(status='Pending').exists():
            skipped.append({'emp_code': e.emp_code, 'reason': 'Payslip is waiting for approval'}); continue
        bd = e.bank_details.filter(is_active=True).first()
        iban = (bd.iban_number or '').upper() if bd else ''
        if not (iban.startswith('SA') and len(iban) == 24):
            skipped.append({'emp_code': e.emp_code, 'reason': 'Missing or invalid Saudi IBAN (SA + 22 characters)'}); continue
        mol = re.sub(r'\D', '', getattr(ids.get(e.pk), 'emirates_id', '') or '')
        if len(mol) != 10 or mol[0] not in '12':
            skipped.append({'emp_code': e.emp_code, 'reason': 'Missing or invalid national ID / iqama number (10 digits, starting 1 or 2)'}); continue
        bas = hal = ded = Decimal('0')
        for pc in ps.components.all():
            c = pc.component
            a = Decimal(str(pc.amount or 0))
            if c.component_type == 'deduction':
                ded += a
            elif c.component_type == 'addition' and c.payroll_category == 'basic':
                bas += a
            elif c.component_type == 'addition' and c.payroll_category in ('housing_allowance', 'hra'):
                hal += a
        full = max(Decimal(str(ps.net_salary or 0)), Decimal('0')).quantize(Decimal('0.01'))
        oea = full - bas - hal + ded   # HRSD: 32B-AMT = MOL-BAS + MOL-HAL + MOL-OEA − MOL-DED
        # v1.62.11 (item 227 / 228): pay net − held (payctl.payable_amount); the held part shows as a deduction so 32B-AMT still adds up
        from .payctl import payable_amount
        net = payable_amount(ps).quantize(Decimal('0.01'))
        if net <= 0 and full > 0:
            skipped.append({'emp_code': e.emp_code, 'reason': 'Salary held (Payroll › Hold / release salary)'}); continue
        ded += full - net
        bx = BX.objects.filter(bank_detail_id=bd.pk).first()
        code = (getattr(bx, 'wps_agent', '') or '').strip().upper()
        bank = code if re.fullmatch(r'[A-Z]{4}', code) else ''
        name = re.sub(r'[\t\r\n\[\]]+', ' ', _ename(e))[:140]
        rows.append({'seq': len(rows) + 1, 'emp_code': e.emp_code, 'amount': net, 'net': net, 'iban': iban, 'account': iban, 'name': name, 'sar_bank': bank,
                     'payment_ref': f'Salary {run.month:02d}/{run.year}', 'basic': bas, 'housing': hal, 'other_earnings': max(oea, Decimal('0')),
                     'deductions': ded, 'iqama': mol, 'branch': getattr(e.emp_branch_id, 'branch_name', '') if e.emp_branch_id_id else ''})
        total += net
    pay = run.payment_date or now.date()
    header = [(opts.get('dest_bank') or '').upper(), opts.get('estb_id') or '', (prof or {}).get('account') or '', 'SAR', f'{pay:%Y%m%d}',
              _sar(total), '', f'{run.pk:06d}{now:%y%m%d%H%M}'[:16], '', opts.get('mol_estb_id') or '']
    warnings = []
    for k, label in (('dest_bank', 'Enter the establishment bank (SARIE bank ID, 4 letters) in the bank file set-up.'),
                     ('estb_id', 'Enter the establishment ID in the bank file set-up.'), ('mol_estb_id', 'Enter the MoL establishment ID in the bank file set-up.')):
        if not opts.get(k):
            warnings.append(tr(label))
    if not (prof or {}).get('account'):
        warnings.append(tr('Enter the establishment bank account (IBAN) in the bank file set-up.'))
    if any(not r['sar_bank'] for r in rows):
        warnings.append(tr('Some employees have no 4-letter SARIE bank code in Bank / WPS → WPS agent – the bank code column is empty for them.'))
    if skipped:
        warnings.append(tr('{0} employee(s) are not in the file – see the list below.', len(skipped)))
    return {'header': header, 'rows': rows, 'skipped': skipped, 'warnings': warnings, 'total': total, 'now': now}


def render_ksa(k):
    return '\r\n'.join(['\t'.join(k['header'])] + ['\t'.join(r) for r in k['rows']] + ['-'])


# ------------------------------------------------------------------ checks of a made file (structure against the layout)
def check_sif(text, layout, evp=False):
    lines = text.split('\r\n')
    edr = [l.split(',') for l in lines if l.startswith('EDR,')]
    evps = [l.split(',') for l in lines if l.startswith('EVP,')]
    scr = lines[-1].split(',') if lines else []
    n_edr = 12 if layout == 'adib_mohre' else 10
    total = sum(Decimal(x[7] or '0') + Decimal(x[8] or '0') for x in edr) if edr else Decimal('0')
    months = {f'{x[4][5:7]}{x[4][0:4]}' for x in edr} | {f'{x[5][5:7]}{x[5][0:4]}' for x in edr}
    out = [
        (tr('Every EDR line has {0} fields', n_edr), all(len(x) == n_edr for x in edr)),
        (tr('Every EVP line has 10 fields and follows its EDR'), all(len(x) == 10 for x in evps)
         and all(lines[i - 1].split(',')[1] == lines[i].split(',')[1] for i in range(len(lines)) if lines[i].startswith('EVP,'))),
        (tr('The SCR line is the last line and has 10 fields'), bool(scr) and scr[0] == 'SCR' and len(scr) == 10
         and sum(1 for l in lines if l.startswith('SCR,')) == 1),
        (tr('SCR EDR count = number of EDR lines'), bool(scr) and len(scr) > 6 and (scr[6] or '0') == str(len(edr))),
        (tr('SCR total salary = sum of fixed + variable of the EDR lines'), bool(scr) and len(scr) > 7 and Decimal(scr[7] or '0') == total),
        (tr('SCR salary month = month of every EDR pay period'), not edr or (bool(scr) and len(scr) > 5 and months == {scr[5]})),
        (tr('No other record types, CRLF line ends'), all(l[:4] in ('EDR,', 'SCR,', 'EVP,') for l in lines) and '\n' not in text.replace('\r\n', '')),
    ]
    if not evp:
        out[1] = (tr('No EVP lines'), not evps)
    return [{'check': c, 'ok': bool(ok)} for c, ok in out]


def check_cbd(content, n):
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(content)).active
    head = [c.value for c in ws[1]]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    return [{'check': tr('Sheet "Sheet1"'), 'ok': ws.title == 'Sheet1'},
            {'check': tr('Heading row = the 13 CBD columns in order'), 'ok': head == CBD_COLUMNS},
            {'check': tr('One row per employee ({0})', n), 'ok': len(rows) == n},
            {'check': tr('Employee_Type is N, E or C'), 'ok': all(r[2] in ('N', 'E', 'C') for r in rows)},
            {'check': tr('Dates dd/mm/yyyy, month 2 digits, year 4 digits'), 'ok': all(re.fullmatch(r'\d\d/\d\d/\d{4}', r[10] or '') and re.fullmatch(r'\d\d/\d\d/\d{4}', r[11] or '')
                                                                           and re.fullmatch(r'\d\d', r[8] or '') and re.fullmatch(r'\d{4}', r[9] or '') for r in rows)},
            {'check': tr('No negative variable pay'), 'ok': all(not r[4] or Decimal(r[4]) >= 0 for r in rows)}]


def check_ksa(text):
    lines = text.split('\r\n')
    head = lines[0].split('\t') if lines else []
    body = [l.split('\t') for l in lines[1:-1]]
    tot = sum(Decimal(r[0].replace(',', '.')) for r in body) if body else Decimal('0')
    return [{'check': tr('Establishment line has 10 fields, currency SAR'), 'ok': len(head) == 10 and head[3] == 'SAR'},
            {'check': tr('Every employee line has 14 fields'), 'ok': all(len(r) == 14 for r in body)},
            {'check': tr('Total amount = sum of the net amounts'), 'ok': len(head) > 5 and Decimal(head[5].replace(',', '.')) == tot},
            {'check': tr('Net = basic + housing + other earnings − deductions'), 'ok': all(
                Decimal(r[0].replace(',', '.')) == Decimal(r[6].replace(',', '.')) + Decimal(r[7].replace(',', '.')) + Decimal(r[8].replace(',', '.')) - Decimal(r[9].replace(',', '.'))
                for r in body)},
            {'check': tr('Amounts with a decimal comma, no dot'), 'ok': all('.' not in x for r in body for x in (r[0], r[6], r[7], r[8], r[9]))},
            {'check': tr('Last line "-" (end of file)'), 'ok': bool(lines) and lines[-1] == '-'}]


# ------------------------------------------------------------------ make a file
def branch_country(branch_id):
    try:
        from CountryPack.packs import iso2_of
        b = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch_id).select_related('br_country').first()
        return iso2_of(getattr(b, 'br_country', None)) if b else ''
    except Exception:
        return ''


def company_country():
    try:
        from CountryPack.services import pack_code
        return (pack_code() or '').upper()[:2]
    except Exception:
        return ''


def wps_contexts(run, d, prof):
    """per employee values of the WPS data for the layouts (layouts.CATALOGUE)"""
    opts = (prof or {}).get('options') or {}
    types = cbd_types(prof, d)
    nonres = set(opts.get('non_residents') or [])
    EI = M('EmployeeProfile', 'EmployeeIdentity')
    ids = {i.employee_id: i for i in EI.objects.filter(employee_id__in=[x['employee'].pk for x in d['extra'] if x['employee']])}
    out = []
    for i, (r, x, t) in enumerate(zip(d['edr'], d['extra'], types)):
        e = x['employee']
        fixed, var = Decimal(r['Fixed Income']), Decimal(r['Variable Income'])
        ev = evp_split(x['payslip'], r['Variable Income'])
        idn = ids.get(getattr(e, 'pk', None))
        code = getattr(e, 'emp_code', '')
        out.append({
            'seq': i + 1, 'emp_code': code, 'name': _ename(e) if e else '', 'person_id': r['Person ID'], 'routing': r['Routing Code'],
            'account': r['IBAN Number'], 'iban': r['IBAN Number'], 'pay_start': datetime.date.fromisoformat(r['Pay Start Date']),
            'pay_end': datetime.date.fromisoformat(r['Pay End Date']), 'days': r['Number of Days'], 'fixed': fixed, 'variable': var,
            'net': fixed + var, 'amount': fixed + var, 'leave_days': r['Days on Leave'], 'branch': getattr(getattr(e, 'emp_branch_id', None), 'branch_name', '') if e else '',
            'emirates_id': getattr(idn, 'emirates_id', '') or '', 'labour_card': getattr(idn, 'labour_card_no', '') or '',
            'evp_housing': Decimal(ev[0]), 'evp_conveyance': Decimal(ev[1]), 'evp_medical': Decimal(ev[2]), 'evp_passage': Decimal(ev[3]),
            'evp_overtime': Decimal(ev[4]), 'evp_other': Decimal(ev[5]), 'evp_leave': Decimal(ev[6]),
            'justification': _clean((d.get('justifications') or {}).get(code, '')),
            'resident': 'No' if code in nonres else (opts.get('resident_default') or 'Yes'), 'cbd_type': t,
            'payment_ref': f'Salary {run.month:02d}/{run.year}', 'payslip': getattr(x['payslip'], 'pk', None)})
    return out


def file_context(run, prof, now, count, total, extra=None):
    from .services import amount_words
    t = _tenant()
    opts = (prof or {}).get('options') or {}
    a = AGENT.get((prof or {}).get('agent') or '') or {}
    total = Decimal(str(total)).quantize(Decimal('0.01'))
    x = {'company': getattr(t, 'name', '') or '', 'company_code': (connection.schema_name or '').upper(), 'now': now, 'today': now.date(),
         'salary_month': datetime.date(run.year, run.month, 1), 'payment_date': run.payment_date or now.date(), 'count': count, 'total': total,
         'total_words_en': amount_words(total, 'en'), 'total_words_ar': amount_words(total, 'ar'), 'currency': 'AED', 'run_name': run.name or '',
         'run': run.pk, 'month': f'{run.month:02d}', 'year': str(run.year), 'mmyyyy': f'{run.month:02d}{run.year}', 'yyyymm': f'{run.year}{run.month:02d}',
         'date': now.date(), 'employer_reference': _clean((prof or {}).get('employer_reference'))[:35], 'bank': a.get('name', ''),
         'company_account': (prof or {}).get('account') or '', 'account': re.sub(r'\W+', '', (prof or {}).get('account') or (prof or {}).get('name') or 'ACCOUNT')[:24] or 'ACCOUNT',
         'estb_id': opts.get('estb_id') or '', 'mol_estb_id': opts.get('mol_estb_id') or '', 'dest_bank': (opts.get('dest_bank') or '').upper(),
         'file_ref': f'{run.pk:06d}{now:%y%m%d%H%M}'[:16], 'branch': 'ALL', 'method': 'WPS'}
    x.update(extra or {})
    return x


SIF_CODES = ('cbuae_sif', 'sif_ref_none', 'sif_evp', 'adib_mohre')


def layout_for(prof, agent):
    """the layout of a set-up: its own choice (a built-in or the company's), else the bank's verified one, else the CBUAE SIF"""
    from . import layouts as LY
    code = (prof or {}).get('layout') or agent['layout']
    lay = LY.get(code) or LY.get(agent['layout'])
    return code if LY.get(code) else agent['layout'], lay


def make(run, prof=None, layout=None, branch_ids=None, justifications=None, now=None, employee_ids=None):
    """→ {layout, state, file_name, content (bytes|str), mime, preview_lines, summary, warnings, skipped, checks, note, changed}"""
    import copy
    from . import layouts as LY
    prof = prof or {}
    agent = AGENT.get(prof.get('agent') or 'cbuae') or AGENT['cbuae']
    if isinstance(layout, dict):   # a layout being designed (File layouts › Preview with this run)
        code, lay = layout.get('code') or 'draft', layout
    else:
        code, lay = (layout, LY.get(layout)) if layout and LY.get(layout) else layout_for(prof, agent)
    lay = copy.deepcopy(lay)
    opts = prof.get('options') or {}
    if code == 'sif_evp' and not opts.get('evp'):
        lay['records'] = [r for r in lay['records'] if r['name'] != 'EVP']
    if code == 'adib_mohre' and not opts.get('mohre_columns', True):
        lay = copy.deepcopy(LY.BUILTIN['cbuae_sif'])
    meta = LAYOUTS.get(code)
    if branch_ids is None:
        branch_ids = list(prof.get('branch_ids') or [])
    state = (agent['state'] if code == agent['layout'] else meta['state']) if meta else 'company'
    out = {'layout': code, 'layout_name': lay['name'], 'state': state, 'agent': agent['code'], 'agent_name': agent['name'], 'note': '', 'changed': [],
           'file_type': lay.get('file_type')}
    if meta is None:
        out['note'] = tr('Company layout – check it against the bank’s sample file (File layouts › Check against a sample).')
    elif agent['state'] != 'verified' and code == 'cbuae_sif':
        out['note'] = tr(FALLBACK_NOTE)
    elif meta['state'] != 'verified':
        out['note'] = tr('Layout to be confirmed') + ' – ' + tr(meta['summary'])
    src = lay.get('source') or 'wps'
    if src == 'ksa':
        k = ksa_data(run, branch_ids, prof, now)
        fctx = file_context(run, prof, k['now'], len(k['rows']), k['total'])
        name, content, mime, prev = LY.render(lay, k['rows'], fctx)
        rows = LY.rows_of(lay, k['rows'], fctx)
        text = content.decode('utf-8')
        out.update(file_name=name, content=text, mime=mime, preview_lines=text.split('\r\n') if '\r\n' in text else prev, warnings=k['warnings'],
                   skipped=k['skipped'], checks=check_ksa(text) if code == 'mudad_wages' else LY.check_made(lay, rows),
                   summary={'employees': len(k['rows']), 'total': f"{k['total']:.2f}", 'currency': 'SAR', 'file_date': k['now'].strftime('%Y-%m-%d'),
                            'skipped': len(k['skipped'])}, people=[r['iqama'] for r in k['rows']])
        return out
    d = sif_data(run, branch_ids, now, employee_ids=employee_ids)
    d['justifications'] = justifications or {}
    summary = {'employer_id': d['employer'], 'routing_code': d['routing'], 'file_date': d['now'].strftime('%Y-%m-%d'),
               'file_time': d['now'].strftime('%H%M'), 'salary_month': f'{run.month:02d}{run.year}', 'employees': len(d['edr']),
               'total': f"{d['total']:.2f}", 'currency': 'AED', 'skipped': len(d['skipped'])}
    out.update(summary=summary, warnings=list(d['warnings']), skipped=d['skipped'], people=[getattr(x['employee'], 'emp_code', '') for x in d['extra']])
    ctxs = wps_contexts(run, d, prof)
    fctx = file_context(run, prof, d['now'], len(d['edr']), Decimal(f"{d['total']:.2f}"),
                        {'employer_id': d['employer'], 'employer_routing': d['routing'],
                         'total_fixed': sum((c['fixed'] for c in ctxs), Decimal('0')), 'total_variable': sum((c['variable'] for c in ctxs), Decimal('0'))})
    name, content, mime, prev = LY.render(lay, ctxs, fctx)
    rows = LY.rows_of(lay, ctxs, fctx)
    if lay.get('file_type') == 'xlsx':
        out.update(file_name=name, content=content, mime=mime, preview_lines=prev,
                   checks=check_cbd(content, len(ctxs)) if code == 'cbd_xlsx' else LY.check_made(lay, rows),
                   accounts={c['emp_code']: f"{c['routing']}|{c['account']}" for c in ctxs})
        return out
    text = content.decode('utf-8-sig') if lay.get('file_type') != 'pdf' else ''
    based = code if code in SIF_CODES else lay.get('based_on')
    if based in SIF_CODES and lay.get('file_type') == 'delimited' and lay.get('separator') == ',':
        checks = check_sif(text, 'adib_mohre' if len(next(r for r in lay['records'] if r['name'] == 'EDR')['fields']) == 12 else 'cbuae_sif',
                           evp=any(r['name'] == 'EVP' for r in lay['records']))
    else:
        checks = LY.check_made(lay, rows)
    if code == 'adib_mohre' and opts.get('mohre_columns', True):
        out['changed'] = pay_changed(run, d)
        miss = [c['emp_code'] for c in out['changed'] if not _clean((justifications or {}).get(c['emp_code']))]
        if miss:
            out['warnings'].append(tr('{0} employee(s) have a different pay from their previous payslip – enter the justification '
                                      'for them (ADIB, MOHRE registered).', len(miss)))
    out.update(file_name=name, content=text if lay.get('file_type') != 'pdf' else content, mime=mime, preview_lines=prev, checks=checks)
    return out


def month_range(run):
    return datetime.date(run.year, run.month, 1), datetime.date(run.year, run.month, calendar.monthrange(run.year, run.month)[1])
