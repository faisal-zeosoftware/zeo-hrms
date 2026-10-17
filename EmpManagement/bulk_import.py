"""v1.26.0 – employee bulk upload, rebuilt after the Falcon Demo test (2,094 employees).

What the test found in the old upload (EmpbulkuploadViewSet.bulk_upload + EmployeeResource):
  * one bad row rejected the whole file (70 errors in 1,280 rows -> nothing saved);
  * 1,280 rows took about 7 minutes inside the request, with no progress, and the result was only a browser alert;
  * the nationality had to be spelt exactly as in the shared list ("FILIPINO" failed, "Philippines" worked);
  * a CSV saved with Excel's "UTF-8" (with BOM) lost the first column, other CSVs turned "–" into "â€“";
  * no column for Reports To, bank / IBAN / salary card, MoHRE person ID, labour card, Emirates ID, passport, visa,
    salary or exit date – so the WPS file was empty, nobody could apply for leave and leavers counted as active.

This importer:
  * reads .xlsx / .xls / .csv (UTF-8 with or without BOM, Windows-1252), any sheet name (EmployeeMaster first);
  * matches headers loosely ("Employee Code*", "Emp ID", "employee code" are the same column; "(DD/MM/YYYY)" hints are
    ignored) and lists the columns it did not understand;
  * looks every master up once (branches, departments per branch, designations, categories, nationalities, countries,
    states, religions, salary components, existing employees) – no queries per cell;
  * checks every row and gives every error with its row number and column;
  * mode "partial" (default): imports the good rows and returns the others with the reason (and an error file);
    mode "all": nothing is saved when any row has an error (the old behaviour); dry_run: check only;
  * runs in the background with progress (processed / total) – the screen polls the job;
  * besides the employee: Reports To (employee code), bank (bank, IBAN, account, routing code, salary card, payment
    mode), identity (Emirates ID, passport, visa, labour card + expiry dates, Arabic name), MoHRE person ID, salary
    (one column per salary component, e.g. "Basic Salary", "Housing Allowance"), status / exit date.
"""
import csv
import io
import logging
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.core.cache import cache
from django.db import connection, transaction

log = logging.getLogger(__name__)
M = apps.get_model

JOB = 'zeo:empimport:{}'
CHUNK = 100

# canonical key -> accepted header spellings (compared after normalising: lower case, no "*", no "(...)", single spaces)
COLUMNS = {
    'code': ['employee code', 'emp code', 'emp id', 'employee id', 'employee no', 'employee number', 'staff no', 'code'],
    'first_name': ['first name', 'firstname', 'given name'],
    'middle_name': ['middle name'],
    'last_name': ['last name', 'lastname', 'surname', 'family name'],
    'full_name': ['full name', 'employee name', 'name'],
    'gender': ['gender', 'sex'],
    'dob': ['date of birth', 'dob', 'birth date'],
    'personal_email': ['personal email id', 'personal email', 'private email'],
    'company_email': ['email', 'company email', 'company email id', 'work email', 'official email'],
    'mobile': ['personal number', 'mobile', 'mobile number', 'phone', 'personal mobile'],
    'company_mobile': ['company number', 'company mobile', 'work mobile'],
    'country': ['country', 'country code', 'home country'],
    'state': ['state', 'emirate'],
    'city': ['city'],
    'permanent_address': ['permanent address', 'home address'],
    'present_address': ['present address', 'address', 'uae address'],
    'joined': ['joining date', 'date of joining', 'doj', 'join date', 'hire date'],
    'confirmed': ['confirmation date', 'confirmaton date', 'probation end date'],
    'nationality': ['nationality'],
    'religion': ['religion'],
    'blood_group': ['blood group'],
    'marital': ['marital status'],
    'father': ['father name'],
    'mother': ['mother name'],
    'branch': ['branch', 'branch name', 'establishment', 'location'],
    'department': ['department', 'dept'],
    'designation': ['designation', 'job title', 'position'],
    'category': ['category', 'employee category', 'grade', 'staff category'],
    'emp_status': ['employee status'],
    'active': ['employee active', 'active'],
    'ot': ['employee ot applicable', 'ot applicable', 'overtime applicable', 'ot eligible'],
    'ess': ['iss ess', 'is ess', 'ess', 'self service', 'ess login'],
    'attendance_source': ['attendance source'],
    'person_id': ['person id', 'mohre person id', 'mol person id', 'labour person id'],
    'reports_to': ['reports to', 'reports to emp id', 'line manager', 'manager code', 'reporting to', 'manager emp id', 'supervisor code'],
    'reporting_manager': ['reporting manager', 'reporting manager user', 'manager user name'],
    'work_location': ['work location'],
    'visa_location': ['visa location'],
    'status': ['status', 'employment status'],
    'exit_date': ['exit date', 'date of leaving', 'last working day', 'leaving date', 'resignation date', 'termination date'],
    'exit_reason': ['exit reason', 'leaving reason', 'reason for leaving'],
    # bank / WPS
    'bank_name': ['bank name', 'bank'],
    'iban': ['iban', 'iban number', 'iban no'],
    'account_no': ['account number', 'account no', 'bank account'],
    'routing_code': ['routing code', 'bank routing code', 'agent id', 'routing no', 'wps routing code'],
    'salary_card': ['salary card', 'salary card number', 'card number', 'wps card'],
    'payment_mode': ['payment mode', 'pay mode', 'salary payment mode'],
    # identity
    'arabic_name': ['arabic name', 'name in arabic'],
    'emirates_id': ['emirates id', 'emirates id no', 'eid', 'eid no', 'national id', 'iqama'],
    'emirates_id_issue': ['emirates id issue date', 'eid issue date'],
    'emirates_id_expiry': ['emirates id expiry', 'emirates id expiry date', 'eid expiry', 'eid expiry date'],
    'passport_no': ['passport no', 'passport number', 'passport'],
    'passport_issue': ['passport issue date'],
    'passport_expiry': ['passport expiry', 'passport expiry date'],
    'visa_no': ['visa no', 'visa number', 'residence visa no', 'visa file no'],
    'visa_uid': ['visa uid', 'uid', 'uid no'],
    'visa_issue': ['visa issue date'],
    'visa_expiry': ['visa expiry', 'visa expiry date', 'residence expiry'],
    'labour_card_no': ['labour card no', 'labour card number', 'labor card no', 'work permit no', 'labour card'],
    'labour_card_issue': ['labour card issue date', 'labor card issue date'],
    'labour_card_expiry': ['labour card expiry', 'labour card expiry date', 'labor card expiry', 'work permit expiry'],
    'salary_from': ['salary effective from', 'salary from'],
}
SALARY_ALIASES = {   # header -> payroll category of the component, when no component has that exact name
    'basic': 'basic', 'basic salary': 'basic', 'basic pay': 'basic',
    'housing': 'housing_allowance', 'housing allowance': 'housing_allowance', 'hra': 'hra', 'house rent allowance': 'hra',
    'transport': 'transport_allowance', 'transport allowance': 'transport_allowance', 'transportation allowance': 'transport_allowance',
    'food allowance': 'food_allowance', 'food': 'food_allowance',
    'other allowance': 'other_allowance', 'other allowances': 'other_allowance',
    'telephone allowance': 'telephone_allowance', 'mobile allowance': 'telephone_allowance',
}
NAT_SYNONYMS = {
    'PHILIPPINES': 'FILIPINO', 'PHILIPPINE': 'FILIPINO', 'FILIPINA': 'FILIPINO', 'PINOY': 'FILIPINO',
    'NEPALI': 'NEPALESE', 'NEPAL': 'NEPALESE', 'UK': 'BRITISH', 'UNITED KINGDOM': 'BRITISH', 'ENGLISH': 'BRITISH',
    'USA': 'AMERICAN', 'US': 'AMERICAN', 'UNITED STATES': 'AMERICAN', 'UAE': 'EMIRATI', 'EMIRATES': 'EMIRATI',
    'KSA': 'SAUDI', 'SRI LANKA': 'SRI LANKAN', 'BANGLADESH': 'BANGLADESHI', 'PAKISTAN': 'PAKISTANI', 'INDIA': 'INDIAN',
    'EGYPT': 'EGYPTIAN', 'JORDAN': 'JORDANIAN', 'LEBANON': 'LEBANESE', 'SYRIA': 'SYRIAN', 'SUDAN': 'SUDANESE',
    'KENYA': 'KENYAN', 'NIGERIA': 'NIGERIAN', 'GHANA': 'GHANAIAN', 'UGANDA': 'UGANDAN', 'ETHIOPIA': 'ETHIOPIAN',
    'CHINA': 'CHINESE', 'MOROCCO': 'MOROCCAN', 'TUNISIA': 'TUNISIAN', 'ALGERIA': 'ALGERIAN', 'IRAQ': 'IRAQI', 'IRAN': 'IRANIAN',
    'YEMEN': 'YEMENI', 'PALESTINE': 'PALESTINIAN', 'AFGHANISTAN': 'AFGHAN', 'INDONESIA': 'INDONESIAN', 'MYANMAR': 'BURMESE',
    'SOUTH AFRICA': 'SOUTH AFRICAN', 'CANADA': 'CANADIAN', 'AUSTRALIA': 'AUSTRALIAN', 'FRANCE': 'FRENCH', 'GERMANY': 'GERMAN',
    'ITALY': 'ITALIAN', 'SPAIN': 'SPANISH', 'RUSSIA': 'RUSSIAN', 'UKRAINE': 'UKRAINIAN', 'TURKEY': 'TURKISH', 'VIETNAM': 'VIETNAMESE',
    'THAILAND': 'THAI', 'MALAYSIA': 'MALAYSIAN', 'SINGAPORE': 'SINGAPOREAN', 'KOREA': 'KOREAN', 'SOUTH KOREA': 'KOREAN', 'JAPAN': 'JAPANESE',
    'SAUDI ARABIA': 'SAUDI', 'OMAN': 'OMANI', 'QATAR': 'QATARI', 'BAHRAIN': 'BAHRAINI', 'KUWAIT': 'KUWAITI',
}
LEFT_WORDS = {'left', 'exited', 'exit', 'resigned', 'terminated', 'inactive', 'absconded', 'retired', 'separated'}


# ------------------------------------------------------------------ job

def _skey(v):
    """letters and digits only, lower-case: 'Ras Al-Khaimah' -> 'rasalkhaimah'."""
    import re as _re
    return _re.sub(r'[^a-z0-9]', '', str(v or '').lower())


# v1.28.1: short codes and spellings people type for the emirates
STATE_SYNONYMS = {'dxb': 'Dubai', 'auh': 'Abu Dhabi', 'abudhabi': 'Abu Dhabi', 'abu dabi': 'Abu Dhabi', 'shj': 'Sharjah', 'ajm': 'Ajman',
                  'uaq': 'Umm Al Quwain', 'ummalquwain': 'Umm Al Quwain', 'rak': 'Ras Al Khaimah', 'rasalkhaima': 'Ras Al Khaimah',
                  'fuj': 'Fujairah', 'fujairah': 'Fujairah', 'alain': 'Abu Dhabi'}

def _job(job, **kw):
    d = cache.get(JOB.format(job)) or {}
    d.update(kw)
    cache.set(JOB.format(job), d, 60 * 60 * 12)


def status(job):
    return cache.get(JOB.format(job))


# ------------------------------------------------------------------ reading
def _norm_header(h):
    h = str(h or '').replace('﻿', '').replace('*', ' ').replace('_', ' ')
    h = re.sub(r'\(.*?\)', ' ', h)
    return ' '.join(h.lower().split())


def read_file(name, data):
    """[(sheet name, [header], [[cells]])] for the file (first sheet, or EmployeeMaster)."""
    name = (name or '').lower()
    if name.endswith('.csv') or name.endswith('.txt'):
        for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect)]
        rows = [r for r in rows if any(str(c).strip() for c in r)]
        return rows[0] if rows else [], rows[1:]
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb['EmployeeMaster'] if 'EmployeeMaster' in wb.sheetnames else wb[wb.sheetnames[0]]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    rows = [r for r in rows if any(c not in (None, '') and str(c).strip() for c in r)]
    # header = the first row that has an "employee code" like cell (sheets often start with a title row)
    for i, r in enumerate(rows[:10]):
        keys = {_norm_header(c) for c in r}
        if keys & set(COLUMNS['code']):
            return [c for c in r], rows[i + 1:]
    return (rows[0] if rows else []), rows[1:]


def _txt(v):
    if v is None:
        return ''
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return ' '.join(str(v).replace('﻿', '').split())


def _date(v):
    if v in (None, ''):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and 1000 < float(v) < 80000:   # Excel serial
        return (datetime(1899, 12, 30) + timedelta(days=float(v))).date()
    s = _txt(v)
    for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%Y', '%d %b %Y', '%d-%b-%Y', '%d %B %Y', '%d/%m/%y', '%d-%m-%y',
                '%Y/%m/%d', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'"{s}" is not a date – use dd/mm/yyyy')


def _bool(v, default):
    s = _txt(v).lower()
    if s == '':
        return default
    if s in ('true', '1', 'yes', 'y', 'active'):
        return True
    if s in ('false', '0', 'no', 'n', 'inactive'):
        return False
    raise ValueError(f'"{_txt(v)}" is not yes / no')


def _amount(v):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(v).replace(',', '').replace('AED', '').strip())
    except InvalidOperation:
        raise ValueError(f'"{_txt(v)}" is not an amount')


# ------------------------------------------------------------------ masters
class Masters:
    def __init__(self):
        E = M('EmpManagement', 'emp_master')
        self.branches = {}
        for b in M('OrganisationManager', 'brnch_mstr').objects.all():
            for k in (b.branch_name, b.branch_code):
                if k:
                    self.branches[k.strip().lower()] = b
        self.depts = {}
        for d in M('OrganisationManager', 'dept_master').objects.prefetch_related('branch'):
            for b in d.branch.all():
                for k in (d.dept_name, getattr(d, 'dept_code', None)):
                    if k:
                        self.depts.setdefault((b.pk, k.strip().lower()), d)
        self.desgs = {}
        for d in M('OrganisationManager', 'desgntn_master').objects.all():
            for k in (d.desgntn_job_title, getattr(d, 'desgntn_code', None)):
                if k:
                    self.desgs.setdefault(k.strip().lower(), d)
        self.cats = {}
        for c in M('OrganisationManager', 'ctgry_master').objects.all():
            for k in (c.ctgry_title, getattr(c, 'ctgry_code', None)):
                if k:
                    self.cats.setdefault(k.strip().lower(), c)
        Nat = M('Core', 'Nationality')
        self.nats = {}
        for n in Nat.objects.all().order_by('id'):
            if n.N_name:
                self.nats.setdefault(n.N_name.strip().upper(), n)
        self.countries = {}
        from CountryPack.packs import iso2_of
        for c in M('Core', 'cntry_mstr').objects.all():
            for k in (c.country_name, c.country_code, iso2_of(c)):
                if k:
                    self.countries.setdefault(str(k).strip().upper(), c)
        self.states = {}
        for s in M('Core', 'state_mstr').objects.all():
            self.states.setdefault(s.state_name.strip().lower(), s)
            self.states.setdefault(_skey(s.state_name), s)   # v1.28.1: "Abu-Dhabi", "RasAlKhaimah", "umm al-quwain" match too
        for code, name in STATE_SYNONYMS.items():
            if _skey(name) in self.states:
                self.states.setdefault(code, self.states[_skey(name)])
        self.religions = {r.religion.strip().lower(): r for r in M('Core', 'ReligionMaster').objects.all() if r.religion}
        self.employees = {e.emp_code.strip().upper(): e for e in E.objects.all().only('id', 'emp_code', 'users_id', 'person_id')}
        self.person_ids = {e.person_id: e.emp_code for e in self.employees.values() if e.person_id}
        U = M('UserManagement', 'CustomUser')
        self.users = {u.username.lower(): u for u in U.objects.filter(tenants__schema_name=connection.schema_name)}
        try:
            from Core.management.commands.load_master_data import COUNTRIES
        except Exception:
            COUNTRIES = []
        self.country_nat = {}
        for row in COUNTRIES:
            word = row[6].upper()
            self.country_nat[row[0].upper()] = word
            self.country_nat[row[1].upper()] = word
            self.country_nat[word] = word
        self.components = {}
        self.components_by_cat = {}
        try:
            for c in M('PayrollManagement', 'SalaryComponent').objects.filter(component_type='addition').order_by('id'):
                self.components.setdefault(c.name.strip().lower(), c)
                if c.code:
                    self.components.setdefault(c.code.strip().lower(), c)
                if c.component_value_type == 'fixed':
                    self.components_by_cat.setdefault(c.payroll_category, c)
        except LookupError:
            pass
        self.added_nationalities = []

    def nationality(self, raw, create=True):
        v = ' '.join(str(raw).split()).upper()
        if v in self.nats:
            return self.nats[v]
        word = NAT_SYNONYMS.get(v) or self.country_nat.get(v)
        if not word and v.endswith('N') and v[:-1] in self.nats:
            word = v[:-1]
        if word and word in self.nats:
            return self.nats[word]
        if word and create:   # a known nationality that is missing from the shared list: add it once
            n = M('Core', 'Nationality').objects.filter(N_name__iexact=word).first() or M('Core', 'Nationality').objects.create(N_name=word)
            self.nats[word] = n
            self.added_nationalities.append(word)
            return n
        return None

    def component(self, header):
        h = header.strip().lower()
        if h in self.components:
            return self.components[h]
        cat = SALARY_ALIASES.get(h)
        return self.components_by_cat.get(cat) if cat else None


# ------------------------------------------------------------------ row check
def map_headers(header, masters):
    """{column index: key}, [salary (index, component)], [unknown headers]"""
    lookup = {}
    for key, names in COLUMNS.items():
        for n in names:
            lookup.setdefault(n, key)
    idx, salary, unknown, seen = {}, [], [], set()
    for i, h in enumerate(header):
        n = _norm_header(h)
        if not n:
            continue
        key = lookup.get(n)
        if key and key not in seen:
            idx[i] = key
            seen.add(key)
            continue
        comp = masters.component(n)
        if comp is not None:
            salary.append((i, comp, _txt(h)))
            continue
        unknown.append(_txt(h))
    return idx, salary, unknown


def check_row(values, masters, codes_in_file):
    """(clean dict, [errors]) for one row (values = {key: raw})."""
    g = lambda k: values.get(k)   # noqa: E731
    t = lambda k: _txt(values.get(k))   # noqa: E731
    err, out = [], {}

    def need(k, label):
        if not t(k):
            err.append(f'{label} is required')
    code = t('code').upper()
    if not code:
        err.append('Employee Code is required')
    elif code in codes_in_file:
        err.append(f'Employee Code {code} is twice in the file (row {codes_in_file[code]})')
    out['code'] = code
    first, last = t('first_name'), t('last_name')
    if not first and t('full_name'):
        parts = t('full_name').split(' ')
        first, last = parts[0], ' '.join(parts[1:]) if not last else last
    if not first:
        err.append('First Name is required')
    out['first_name'], out['last_name'], out['middle_name'] = first[:50], last[:50], t('middle_name')[:50]
    for k, label in (('dob', 'Date of Birth'), ('joined', 'Joining Date'), ('confirmed', 'Confirmation Date'), ('exit_date', 'Exit Date'),
                     ('emirates_id_issue', 'Emirates ID issue date'), ('emirates_id_expiry', 'Emirates ID expiry'),
                     ('passport_issue', 'Passport issue date'), ('passport_expiry', 'Passport expiry'), ('visa_issue', 'Visa issue date'),
                     ('visa_expiry', 'Visa expiry'), ('labour_card_issue', 'Labour card issue date'), ('labour_card_expiry', 'Labour card expiry'),
                     ('salary_from', 'Salary effective from')):
        try:
            out[k] = _date(g(k))
        except ValueError as e:
            err.append(f'{label}: {e}')
            out[k] = None
    if not out.get('dob') and not any(e.startswith('Date of Birth') for e in err):
        err.append('Date of Birth is required')
    if not out.get('joined') and not any(e.startswith('Joining Date') for e in err):
        err.append('Joining Date is required')
    if out.get('dob') and out['dob'] > date.today():
        err.append('Date of Birth is in the future')
    elif out.get('dob') and out.get('joined'):
        j, d = out['joined'], out['dob']
        if (j.year - d.year - ((j.month, j.day) < (d.month, d.day))) < 18:
            err.append('The employee must be at least 18 years old on the joining date')
    # organisation
    br = masters.branches.get(t('branch').lower()) if t('branch') else None
    if not t('branch'):
        err.append('Branch is required')
    elif br is None:
        err.append(f'Branch "{t("branch")}" not found (use the branch name or code)')
    out['branch'] = br
    dept = None
    if t('department'):
        if br is not None:
            dept = masters.depts.get((br.pk, t('department').lower()))
            if dept is None:
                err.append(f'Department "{t("department")}" is not linked to branch "{br.branch_name}"')
    else:
        err.append('Department is required')
    out['department'] = dept
    desg = masters.desgs.get(t('designation').lower()) if t('designation') else None
    if not t('designation'):
        err.append('Designation is required')
    elif desg is None:
        err.append(f'Designation "{t("designation")}" not found')
    out['designation'] = desg
    cat = masters.cats.get(t('category').lower()) if t('category') else None
    if not t('category'):
        err.append('Category is required')
    elif cat is None:
        err.append(f'Category "{t("category")}" not found')
    out['category'] = cat
    for k, label in (('work_location', 'Work Location'), ('visa_location', 'Visa Location')):
        v = t(k)
        out[k] = masters.branches.get(v.lower()) if v else None
        if v and out[k] is None:
            err.append(f'{label} "{v}" is not a branch')
    # nationality / country / state / religion
    nat = None
    if t('nationality'):
        nat = masters.nationality(t('nationality'), create=False) or (masters.nationality(t('nationality')) if (
            NAT_SYNONYMS.get(t('nationality').upper()) or masters.country_nat.get(t('nationality').upper())) else None)
        if nat is None:
            err.append(f'Nationality "{t("nationality")}" not found')
    else:
        err.append('Nationality is required')
    out['nationality'] = nat
    ctry = masters.countries.get(t('country').upper()) if t('country') else None
    if t('country') and ctry is None:
        from CountryPack.packs import iso2
        ctry = masters.countries.get(iso2(t('country'), t('country')))
        if ctry is None:
            err.append(f'Country "{t("country")}" not found')
    out['country'] = ctry
    st = (masters.states.get(t('state').lower()) or masters.states.get(_skey(t('state')))) if t('state') else None
    if t('state') and st is None:
        names = sorted({x.state_name for x in masters.states.values() if ctry is None or x.country_id == ctry.pk})[:12]
        err.append(f'State / emirate "{t("state")}" not found' + (f' – use one of: {", ".join(names)}' if names else ''))
    elif st is not None and ctry is not None and st.country_id != ctry.pk:
        err.append(f'State "{t("state")}" is not in {ctry.country_name}')
    out['state'] = st
    rel = masters.religions.get(t('religion').lower()) if t('religion') else None
    if t('religion') and rel is None:
        err.append(f'Religion "{t("religion")}" not found')
    out['religion'] = rel
    # simple fields
    gmap = {'m': 'M', 'male': 'M', 'f': 'F', 'female': 'F', 'o': 'O', 'other': 'O'}
    gv = t('gender').lower()
    if gv and gv not in gmap:
        err.append(f'Gender "{t("gender")}" – use Male / Female / Other')
    out['gender'] = gmap.get(gv)
    mmap = {'m': 'M', 'married': 'M', 's': 'S', 'single': 'S', 'd': 'D', 'divorced': 'D', 'w': 'W', 'widow': 'W', 'widowed': 'W'}
    mv = t('marital').lower()
    if mv and mv not in mmap:
        err.append(f'Marital Status "{t("marital")}" – use Married / Single / Divorced / Widowed')
    out['marital'] = mmap.get(mv)
    for k in ('personal_email', 'company_email'):
        v = t(k)
        if v and not re.match(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$', v):
            err.append(f'E-mail "{v}" is not valid')
        out[k] = v or None
    for k, default in (('emp_status', True), ('active', True), ('ot', False), ('ess', False)):
        try:
            out[k] = _bool(g(k), default)
        except ValueError as e:
            err.append(f'{k}: {e}')
            out[k] = default
    src = t('attendance_source').lower()
    out['attendance_source'] = src if src in ('manual', 'biometric') else ('manual' if not src else None)
    if out['attendance_source'] is None:
        err.append(f'Attendance Source "{t("attendance_source")}" – use manual / biometric')
    pid = t('person_id').replace(' ', '')
    if pid:
        if not re.fullmatch(r'[A-Za-z0-9-]{14}', pid):
            err.append(f'Person ID "{pid}" must be 14 letters / digits')
        elif masters.person_ids.get(pid) not in (None, code):
            err.append(f'Person ID {pid} belongs to employee {masters.person_ids[pid]}')
    out['person_id'] = pid or None
    for k in ('mobile', 'company_mobile', 'city', 'permanent_address', 'present_address', 'blood_group', 'father', 'mother', 'exit_reason',
              'arabic_name', 'bank_name', 'payment_mode', 'visa_uid'):
        out[k] = t(k)
    # reports to / manager user
    rt = t('reports_to').upper()
    if rt and rt == code:
        err.append('Reports To cannot be the employee')
    elif rt and rt not in masters.employees and rt not in codes_in_file and rt not in values.get('_all_codes', set()):
        err.append(f'Reports To "{rt}" is not an employee code')
    out['reports_to'] = rt or None
    ru = t('reporting_manager').lower()
    out['reporting_manager'] = masters.users.get(ru) if ru else None
    if ru and out['reporting_manager'] is None:
        err.append(f'Reporting Manager user "{t("reporting_manager")}" not found in this company')
    # status / exit
    sv = t('status').lower()
    out['left'] = bool(sv in LEFT_WORDS or (out.get('exit_date') and out['exit_date'] <= date.today()))
    # bank
    iban = t('iban').replace(' ', '').upper()
    if iban and not re.fullmatch(r'[A-Z0-9]{23}', iban):
        err.append(f'IBAN "{iban}" must be 23 letters / digits (UAE: AE + 21)')
    route = t('routing_code').replace(' ', '')
    if route and not re.fullmatch(r'\d{9}', route):
        err.append(f'Routing code "{route}" must be 9 digits')
    card = t('salary_card').replace(' ', '')
    if card and not re.fullmatch(r'\d{16}', card):
        err.append(f'Salary card "{card}" must be 16 digits')
    out['iban'], out['routing_code'], out['salary_card'], out['account_no'] = iban, route, card, t('account_no').replace(' ', '')
    eid = re.sub(r'\D', '', t('emirates_id'))
    if eid and len(eid) != 15:
        err.append(f'Emirates ID "{t("emirates_id")}" must have 15 digits (784-YYYY-NNNNNNN-N)')
    out['emirates_id'] = f'{eid[:3]}-{eid[3:7]}-{eid[7:14]}-{eid[14:]}' if len(eid) == 15 else ''
    out['passport_no'] = t('passport_no').upper()[:12]
    out['visa_no'] = t('visa_no')[:30]
    out['labour_card_no'] = t('labour_card_no')[:20]
    return out, err


# ------------------------------------------------------------------ saving
def _identity(emp, r, user):
    fields = {}
    for k, f in (('emirates_id', 'emirates_id'), ('emirates_id_issue', 'emirates_id_issue_date'), ('emirates_id_expiry', 'emirates_id_expiry_date'),
                 ('passport_no', 'passport_no'), ('passport_issue', 'passport_issue_date'), ('passport_expiry', 'passport_expiry_date'),
                 ('visa_no', 'visa_number'), ('visa_uid', 'visa_uid'), ('visa_issue', 'visa_issue_date'), ('visa_expiry', 'visa_expiry_date'),
                 ('labour_card_no', 'labour_card_no'), ('labour_card_issue', 'labour_card_issue_date'),
                 ('labour_card_expiry', 'labour_card_expiry_date'), ('arabic_name', 'arabic_name')):
        if r.get(k):
            fields[f] = r[k]
    if not fields:
        return
    I = M('EmployeeProfile', 'EmployeeIdentity')
    ident, _ = I.objects.get_or_create(employee_id=emp.pk)
    for f, v in fields.items():
        setattr(ident, f, v)
    ident.updated_by_id = getattr(user, 'pk', None)
    ident.save()
    try:
        from EmployeeProfile.services import sync_documents
        sync_documents(ident, emp, user)
    except Exception:
        log.exception('documents of %s', emp.emp_code)


def _bank(emp, r):
    if not (r.get('iban') or r.get('salary_card') or r.get('account_no') or r.get('routing_code')):
        return
    B = M('EmpManagement', 'EmployeeBankDetail')
    acc = r.get('account_no') or r.get('salary_card') or r.get('iban')
    b = B.objects.filter(employee=emp, is_active=True).order_by('-id').first()
    if b is None:
        if acc and B.objects.filter(account_number=acc).exclude(employee=emp).exists():
            raise ValueError(f'Account / card {acc} is already on another employee')
        b = B(employee=emp, account_number=acc or f'{emp.emp_code}-WPS')
    elif acc:
        if B.objects.filter(account_number=acc).exclude(pk=b.pk).exists():
            raise ValueError(f'Account / card {acc} is already on another employee')
        b.account_number = acc
    if r.get('bank_name'):
        b.bank_name = r['bank_name']
    if r.get('iban'):
        b.iban_number = r['iban']
    if r.get('routing_code'):
        b.route_code = r['routing_code']
    b.is_active = True
    b.save()
    mode = (r.get('payment_mode') or '').lower()
    mode = 'cash' if 'cash' in mode else 'cheque' if 'cheque' in mode else 'bank_transfer' if ('transfer' in mode and 'wps' not in mode) else 'wps'
    try:
        X = M('EmployeeProfile', 'BankExtra')
        X.objects.update_or_create(bank_detail_id=b.pk, defaults={'employee_id': emp.pk, 'payment_mode': mode, 'is_primary': True})
    except LookupError:
        pass


def _salary(emp, salary, raw):
    S = M('PayrollManagement', 'EmployeeSalaryStructure')
    for i, comp, header in salary:
        v = raw.get(('salary', i))
        if v is None:
            continue
        row, created = S.objects.get_or_create(employee=emp, component=comp, defaults={'amount': v, 'is_active': True})
        if not created and (row.amount != v or not row.is_active):
            row.amount, row.is_active = v, True
            row.save()


def _employment(emp, r, user):
    try:
        from EmployeeProfile.services import ensure_info
        info = ensure_info(emp)
    except Exception:
        log.exception('employment info of %s', emp.emp_code)
        return
    if r.get('left'):
        info.status = 'left' if (r.get('status_word') or 'left') not in ('terminated', 'absconded', 'retired') else r['status_word']
        info.date_of_leaving = r.get('exit_date') or info.date_of_leaving
        info.leaving_reason = (r.get('exit_reason') or info.leaving_reason or 'Imported as a leaver')[:255]
        info.leaving_source = 'manual'
        info.updated_by_id = getattr(user, 'pk', None)
        info.save()


def run(job, filename, data, mode='partial', dry_run=False, user_id=None, schema=None, own_thread=True):
    from django.db import close_old_connections
    if own_thread:
        close_old_connections()
    try:
        if schema:
            connection.set_schema(schema)
        U = M('UserManagement', 'CustomUser')
        user = U.objects.filter(pk=user_id).first() if user_id else None
        header, rows = read_file(filename, data)
        _job(job, step='Checking', total=len(rows), processed=0)
        masters = Masters()
        idx, salary, unknown = map_headers(header, masters)
        missing = [lbl for key, lbl in (('code', 'Employee Code'), ('first_name', 'First Name'), ('dob', 'Date of Birth'), ('joined', 'Joining Date'),
                                        ('branch', 'Branch'), ('department', 'Department'), ('designation', 'Designation'), ('category', 'Category'),
                                        ('nationality', 'Nationality')) if key not in idx.values()
                   and not (key == 'first_name' and 'full_name' in idx.values())]
        if missing:
            _job(job, state='failed', error='Missing column(s): ' + ', '.join(missing), unknown_columns=unknown)
            return
        all_codes = set()
        code_col = [i for i, k in idx.items() if k == 'code'][0]
        for r in rows:
            if code_col < len(r):
                all_codes.add(_txt(r[code_col]).upper())
        checked, errors, seen = [], [], {}
        for n, r in enumerate(rows, start=2):
            values = {k: (r[i] if i < len(r) else None) for i, k in idx.items()}
            values['_all_codes'] = all_codes
            clean, err = check_row(values, masters, seen)
            raw_sal = {}
            for i, comp, h in salary:
                try:
                    raw_sal[('salary', i)] = _amount(r[i] if i < len(r) else None)
                except ValueError as e:
                    err.append(f'{h}: {e}')
            clean['status_word'] = _txt(values.get('status')).lower()
            if clean.get('code'):
                seen.setdefault(clean['code'], n)
            if err:
                errors.append({'row': n, 'code': clean.get('code', ''), 'errors': err,
                               'cells': [_txt(c) for c in r]})
            else:
                checked.append((n, clean, raw_sal))
            if n % 200 == 0:
                _job(job, processed=0, checked=n - 1)
        base = dict(header=[_txt(h) for h in header], total=len(rows), valid=len(checked), invalid=len(errors),
                    errors=errors[:2000], unknown_columns=unknown, salary_columns=[h for _, _, h in salary])
        if dry_run or (mode == 'all' and errors):
            _job(job, state='done', step='Checked', processed=0, created=0, updated=0, failed=len(errors), dry_run=dry_run,
                 saved=False, **base)
            return
        _job(job, step='Saving', **base)
        E = M('EmpManagement', 'emp_master')
        created = updated = 0
        save_errors = []
        by_code = {}
        try:
            from Chatter.tracking import paused
        except Exception:   # pragma: no cover
            from contextlib import nullcontext as paused
        with paused():
            for start in range(0, len(checked), CHUNK):
                for n, r, raw_sal in checked[start:start + CHUNK]:
                    try:
                        with transaction.atomic():
                            emp = E.objects.filter(emp_code__iexact=r['code']).first()
                            is_new = emp is None
                            if is_new:
                                emp = E(emp_code=r['code'])
                            emp.emp_first_name, emp.emp_middle_name, emp.emp_last_name = r['first_name'], r['middle_name'] or None, r['last_name'] or None
                            emp.emp_gender = r['gender'] or emp.emp_gender
                            emp.emp_date_of_birth, emp.emp_joined_date = r['dob'], r['joined']
                            if r.get('confirmed'):
                                emp.emp_date_of_confirmation = r['confirmed']
                            emp.emp_personal_email = r['personal_email'] or emp.emp_personal_email
                            emp.emp_company_email = r['company_email'] or emp.emp_company_email
                            emp.emp_mobile_number_1 = r['mobile'] or emp.emp_mobile_number_1
                            emp.emp_mobile_number_2 = r['company_mobile'] or emp.emp_mobile_number_2
                            emp.emp_country_id = r['country'] or emp.emp_country_id
                            emp.emp_state_id = r['state'] or emp.emp_state_id
                            emp.emp_city = r['city'] or emp.emp_city
                            emp.emp_permenent_address = r['permanent_address'] or emp.emp_permenent_address
                            emp.emp_present_address = r['present_address'] or emp.emp_present_address
                            emp.emp_nationality = r['nationality']
                            emp.emp_relegion = r['religion'] or emp.emp_relegion
                            emp.emp_blood_group = r['blood_group'] or emp.emp_blood_group
                            emp.emp_marital_status = r['marital'] or emp.emp_marital_status
                            emp.emp_father_name = r['father'] or emp.emp_father_name
                            emp.emp_mother_name = r['mother'] or emp.emp_mother_name
                            emp.emp_branch_id, emp.emp_dept_id = r['branch'], r['department']
                            emp.emp_desgntn_id, emp.emp_ctgry_id = r['designation'], r['category']
                            emp.work_location = r['work_location'] or emp.work_location
                            emp.visa_location = r['visa_location'] or emp.visa_location
                            emp.emp_ot_applicable = r['ot']
                            emp.is_ess = r['ess'] or (emp.is_ess if not is_new else False)
                            emp.attendance_source = r['attendance_source'] or 'manual'
                            emp.person_id = r['person_id'] or emp.person_id
                            if r.get('reporting_manager'):
                                emp.emp_reporting_manager = r['reporting_manager']
                            left = r.get('left')
                            emp.emp_status = False if left else r['emp_status']
                            if is_new:
                                emp.created_by = user
                                emp.save(authenticated_user=user)
                                if left or not r['active']:   # save() makes every new employee active
                                    E.objects.filter(pk=emp.pk).update(is_active=False)
                                    emp.is_active = False
                            else:
                                emp.is_active = False if left else r['active']
                                emp.updated_by = user
                                emp.save()
                            _employment(emp, r, user)
                            _identity(emp, r, user)
                            _bank(emp, r)
                            _salary(emp, salary, raw_sal)
                        by_code[r['code']] = (emp, r.get('reports_to'))
                        if is_new:
                            created += 1
                        else:
                            updated += 1
                    except Exception as e:   # one row never stops the others
                        log.exception('import row %s', n)
                        msg = '; '.join(f'{k}: {" ".join(map(str, v))}' for k, v in e.message_dict.items()) if hasattr(e, 'message_dict') else str(e)
                        save_errors.append({'row': n, 'code': r.get('code', ''), 'errors': [msg[:300]], 'cells': []})
                _job(job, processed=min(start + CHUNK, len(checked)), created=created, updated=updated)
            # second pass: Reports To (the manager may be further down the file)
            codes = {c.upper(): e for c, e in ((x.emp_code, x) for x in E.objects.filter(
                emp_code__in=[rt for _, rt in by_code.values() if rt]).only('id', 'emp_code'))}
            for code, (emp, rt) in by_code.items():
                if rt and rt.upper() in codes and emp.emp_reports_to_id != codes[rt.upper()].pk:
                    E.objects.filter(pk=emp.pk).update(emp_reports_to=codes[rt.upper()].pk)
        try:   # one history note instead of a log line per field
            from Chatter.models import Message
            from Chatter.tracking import model_key
            Message.objects.create(model=model_key(E), object_id='import', author=user,
                                   body=f'Employee bulk upload {filename}: {created} created, {updated} updated, {len(errors) + len(save_errors)} not imported.')
        except Exception:
            pass
        all_err = errors + save_errors
        _job(job, state='done', step='Done', processed=len(checked), created=created, updated=updated, failed=len(all_err), saved=True,
             nationalities_added=masters.added_nationalities, **dict(base, errors=all_err[:2000], invalid=len(all_err)))
    except Exception as e:
        log.exception('employee import')
        _job(job, state='failed', error=str(e)[:400])
    finally:
        if own_thread:
            try:
                connection.set_schema_to_public()
            except Exception:
                pass
            close_old_connections()


def start(filename, data, mode='partial', dry_run=False, user=None, run_now=False):
    job = uuid.uuid4().hex[:16]
    _job(job, state='running', step='Reading the file', processed=0, total=0, created=0, updated=0, failed=0, filename=filename,
         by=getattr(user, 'pk', None), schema=connection.schema_name)
    args = (job, filename, data, mode, dry_run, getattr(user, 'pk', None), connection.schema_name)
    if run_now:
        run(*args, own_thread=False)
    else:
        threading.Thread(target=run, args=args, kwargs={'own_thread': True}, daemon=True).start()
    return job


def error_csv(st):
    """CSV of the rows that were not imported: the original cells + the reasons."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['Row', 'Reasons'] + list(st.get('header') or []))
    for e in st.get('errors') or []:
        w.writerow([e['row'], ' | '.join(e['errors'])] + list(e.get('cells') or []))
    return '﻿' + out.getvalue()


TEMPLATE_HEADERS = ['Employee Code*', 'First Name*', 'Middle Name', 'Last Name', 'Gender', 'Date of Birth(DD/MM/YYYY)*',
                    'Joining Date(DD/MM/YYYY)*', 'Nationality*', 'Branch*', 'Department*', 'Designation*', 'Category*',
                    'Reports To', 'Personal Email ID', 'Email', 'Personal Number', 'Marital Status', 'Religion', 'Country', 'State',
                    'Employee OT applicable(True/False)', 'Iss ESS (True/False)', 'Attendance Source', 'Person ID',
                    'Bank Name', 'IBAN', 'Account Number', 'Routing Code', 'Salary Card', 'Payment Mode',
                    'Emirates ID', 'Emirates ID Expiry', 'Passport No', 'Passport Expiry', 'Visa No', 'Visa Expiry',
                    'Labour Card No', 'Labour Card Expiry', 'Basic Salary', 'Housing Allowance', 'Transport Allowance', 'Other Allowance',
                    'Status', 'Exit Date']
