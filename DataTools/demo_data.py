"""v1.73.15 – demo data: 10 fictional companies in the 7 country packs and 10 industries, made when a company is created.

"Do you want demo data?" on Create company (and `setup_zeo --demo simple|heavy`):
  none    start empty
  simple  10 demo companies, about 10 employees each, the last full month and the current month so far
  heavy   10 demo companies, about 20 employees each, the 6 full months before this month and the current month so far

The months always end at the day the demo is made: a demo made in November 2026 covers May – October 2026 (heavy) and
1 – 16 November; one made in December covers June – November. Every record goes in through the screens' own API with the
data workbook engine (DataTools/management/commands/load_workbook.py) – the same checks, rights, numbering and approvals –
and is dated on its business day (@date), so the history looks real.

Every name, number and address is invented – no real person or company. The demo companies are marked (CompanyOption
key "demo") and neutralized: no e-mail leaves ZEO for them and outside AI is off (ZeoApps.duplicate.neutralize_company).
Settings › Demo data removes them, or makes them again with fresh dates ("Refresh demo data").
"""
import datetime
import io
import json
import zlib

D = datetime.date

LEVELS = {
    'simple': {'months': 1, 'size': 'small', 'minutes': '5–8', 'title': 'Simple demo',
               'text': 'About 10 employees a company, every app the industry uses – the last full month and this month so far.'},
    'heavy': {'months': 6, 'size': 'full', 'minutes': '15–25', 'title': 'Heavy demo',
              'text': 'About 20 employees a company, every app the industry uses – the 6 full months before this month and this month so far.'},
}


# ---------------------------------------------------------------------------------------------- numbers (same on every server)
def _n(s, digits):
    """A fixed number of `digits` digits for the text s – the same on every server (not random)."""
    v = zlib.crc32(s.encode()) * 2654435761 % (10 ** digits)
    return f'{v:0{digits}d}'


def _pick(s, items):
    return items[zlib.crc32(s.encode()) % len(items)]


def _chance(s, pct):
    return zlib.crc32(s.encode()) % 100 < pct


def _luhn_digit(body):
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def _eid(year, seven):
    body = f'784{year}{seven}'
    return f'784-{year}-{seven}-{_luhn_digit(body)}'


_VD = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
       [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1], [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
       [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VP = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
       [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1], [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
_VINV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def _aadhaar(seed):
    """12 digits (first 2–9) with a Verhoeff check digit – invented, never a real person's number."""
    body = str(2 + int(_n(seed, 1)) % 8) + _n(seed + 'a', 10)
    c = 0
    for i, ch in enumerate(reversed(body)):
        c = _VD[c][_VP[(i + 1) % 8][int(ch)]]
    full = body + str(_VINV[c])
    return f'{full[:4]} {full[4:8]} {full[8:]}'


def _pan(seed, last):
    letters = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
    a = ''.join(letters[int(_n(seed + str(i), 2)) % len(letters)] for i in range(3))
    return f'{a}P{(last or "X")[0].upper()}{_n(seed + "pan", 4)}{letters[int(_n(seed + "z", 2)) % len(letters)]}'


def _cert_pdf(text):
    """A one-page PDF (a demo medical certificate) as a data URI – the leave request API takes it as lv_document."""
    from .demo_workbook import _cert_pdf as f
    return f(text)


def _iban(cc, bban):
    num = ''.join(str(int(ch, 36)) for ch in bban + cc + '00')
    return f'{cc}{98 - int(num) % 97:02d}{bban}'


# ---------------------------------------------------------------------------------------------- the 7 countries
# bank: (name, branch, routing code, function(seed) → (account, iban))
COUNTRIES = {
    'AE': {'weekend': (5, 6), 'country': 'United Arab Emirates', 'national': 'Emirati', 'phone': '+9714', 'mobile': '+97150', 'fx': 1, 'round': 50,
           'id_doc': 'Emirates ID', 'nationals': 2, 'wps': True,
           'cities': {'Dubai': 'Office 1804, Bay Square 7, Business Bay', 'Abu Dhabi': 'Floor 9, Al Khatem Tower, Al Maryah Island',
                      'Sharjah': 'Warehouse 12, Industrial Area 15'},
           'bank': ('Emirates NBD', 'Business Bay', '302620122', lambda s: (_n(s, 16)[-13:], _iban('AE', '260' + _n(s, 16))))},
    'SA': {'weekend': (4, 5), 'country': 'Saudi Arabia', 'national': 'Saudi', 'phone': '+96611', 'mobile': '+96655', 'fx': 1, 'round': 50,
           'id_doc': 'Iqama / National ID', 'nationals': 6,
           'cities': {'Riyadh': 'Office 702, King Fahd Road, Al Olaya', 'Dammam': 'Building 14, 2nd Industrial City', 'Jeddah': 'Office 305, Tahlia Street, Al Andalus'},
           'bank': ('Saudi National Bank', 'Al Olaya', '', lambda s: (_n(s, 18), _iban('SA', '10' + _n(s, 18))))},
    'QA': {'weekend': (4, 5), 'country': 'Qatar', 'national': 'Qatari', 'phone': '+9744', 'mobile': '+97455', 'fx': 1, 'round': 50,
           'id_doc': 'Qatar ID (QID)', 'nationals': 2,
           'cities': {'Doha': 'Floor 6, Tower 3, West Bay', 'Al Wakrah': 'Building 21, Al Wakrah Main Road'},
           'bank': ('QNB', 'West Bay', '', lambda s: (_n(s, 16), _iban('QA', 'QNBA' + '00000' + _n(s, 16))))},
    'OM': {'weekend': (4, 5), 'country': 'Oman', 'national': 'Omani', 'phone': '+96824', 'mobile': '+96899', 'fx': 0.105, 'round': 5,
           'id_doc': 'National ID', 'nationals': 5,
           'cities': {'Muscat': 'Office 41, Way 3309, Al Khuwair', 'Sohar': 'Plot 118, Sohar Industrial Estate'},
           'bank': ('Bank Muscat', 'Al Khuwair', '', lambda s: (_n(s, 16), _iban('OM', '018' + _n(s, 16))))},
    'BH': {'weekend': (4, 5), 'country': 'Bahrain', 'national': 'Bahraini', 'phone': '+97317', 'mobile': '+97336', 'fx': 0.103, 'round': 5,
           'id_doc': 'CPR card', 'nationals': 5,
           'cities': {'Manama': 'Office 1102, Building 247, Road 1704, Diplomatic Area', 'Muharraq': 'Building 88, Road 1012, Muharraq'},
           'bank': ('National Bank of Bahrain', 'Diplomatic Area', '', lambda s: (_n(s, 14), _iban('BH', 'NBOB' + _n(s, 14))))},
    'KW': {'weekend': (4, 5), 'country': 'Kuwait', 'national': 'Kuwaiti', 'phone': '+9652', 'mobile': '+96550', 'fx': 0.084, 'round': 5,
           'id_doc': 'Civil ID', 'nationals': 3,
           'cities': {'Kuwait City': 'Floor 14, Block 2, Sharq', 'Salmiya': 'Block 10, Salem Al Mubarak Street, Salmiya'},
           'bank': ('National Bank of Kuwait', 'Sharq', '', lambda s: (_n(s, 18), _iban('KW', 'NBOK' + '0000' + _n(s, 18))))},
    'IN': {'weekend': (6,), 'country': 'India', 'national': 'Indian', 'phone': '+9180', 'mobile': '+9198', 'fx': 9, 'round': 500,
           'id_doc': 'Aadhaar', 'nationals': 99,
           'cities': {'Bengaluru': '4th Floor, Prestige Tech Park, Outer Ring Road', 'Pune': 'Office 602, Baner Road, Baner'},
           'bank': ('HDFC Bank', 'Outer Ring Road', 'HDFC0001234', lambda s: (_n(s, 14), ''))},
}
EXPATS = {   # nationalities working in each country (besides its nationals)
    'AE': ['Indian', 'Pakistani', 'Filipino', 'Egyptian', 'Nepalese', 'Bangladeshi', 'Jordanian', 'Sri Lankan'],
    'SA': ['Indian', 'Pakistani', 'Egyptian', 'Filipino', 'Bangladeshi', 'Jordanian', 'Sudanese'],
    'QA': ['Indian', 'Filipino', 'Nepalese', 'Egyptian', 'Sri Lankan', 'Kenyan'],
    'OM': ['Indian', 'Pakistani', 'Bangladeshi', 'Filipino', 'Egyptian'],
    'BH': ['Indian', 'Pakistani', 'Filipino', 'Egyptian', 'Jordanian'],
    'KW': ['Indian', 'Egyptian', 'Filipino', 'Jordanian', 'Lebanese', 'Sri Lankan'],
    'IN': ['Indian'],
}
NAMES = {   # first names (male, female) and last names per nationality – invented combinations
    'Emirati': (['Khalid', 'Saeed', 'Hamdan', 'Rashid', 'Majid'], ['Mariam', 'Fatima', 'Sara', 'Hessa', 'Noora'], ['Al Mansoori', 'Al Hashimi', 'Al Ketbi', 'Al Suwaidi', 'Al Nuaimi']),
    'Saudi': (['Abdullah', 'Faisal', 'Turki', 'Nawaf', 'Saud', 'Yazeed'], ['Reem', 'Lama', 'Nouf', 'Haya', 'Abeer'], ['Al Qahtani', 'Al Harbi', 'Al Otaibi', 'Al Dossary', 'Al Shehri', 'Al Ghamdi']),
    'Qatari': (['Jassim', 'Hamad', 'Tamim', 'Fahad'], ['Al Anoud', 'Dana', 'Maha', 'Shaikha'], ['Al Kuwari', 'Al Marri', 'Al Mohannadi', 'Al Sulaiti']),
    'Omani': (['Said', 'Salim', 'Haitham', 'Mazin', 'Badr'], ['Aisha', 'Zainab', 'Muna', 'Ruqaya', 'Huda'], ['Al Balushi', 'Al Harthy', 'Al Rawahi', 'Al Busaidi', 'Al Hinai']),
    'Bahraini': (['Ali', 'Hussain', 'Yousif', 'Ebrahim', 'Jaffar'], ['Zahra', 'Maryam', 'Amal', 'Fatema', 'Noor'], ['Al Doseri', 'Al Mahmood', 'Al Sayed', 'Jaffar', 'Al Alawi']),
    'Kuwaiti': (['Mubarak', 'Bader', 'Mishal', 'Talal'], ['Dalal', 'Shahad', 'Ghadeer', 'Rawan'], ['Al Ajmi', 'Al Mutairi', 'Al Enezi', 'Al Rashidi']),
    'Indian': (['Rahul', 'Arjun', 'Vikram', 'Suresh', 'Ramesh', 'Anil', 'Karthik', 'Rohan', 'Sanjay', 'Deepak', 'Nikhil', 'Pranav'],
               ['Priya', 'Anjali', 'Divya', 'Kavya', 'Sneha', 'Meera', 'Lakshmi', 'Pooja', 'Shruti', 'Nisha'],
               ['Nair', 'Menon', 'Pillai', 'Sharma', 'Iyer', 'Reddy', 'Kulkarni', 'Patel', 'Rao', 'Varghese', 'Joshi', 'Das']),
    'Pakistani': (['Imran', 'Bilal', 'Usman', 'Zeeshan'], ['Ayesha', 'Sana', 'Hina', 'Mahnoor'], ['Siddiqui', 'Qureshi', 'Malik', 'Chaudhry']),
    'Filipino': (['Mark', 'John Paul', 'Jerome', 'Ramon'], ['Grace', 'Maricel', 'Joy', 'Kristine'], ['Mendoza', 'Santos', 'Reyes', 'Dela Cruz']),
    'Egyptian': (['Ahmed', 'Mahmoud', 'Mostafa', 'Karim'], ['Mona', 'Yasmin', 'Rania', 'Heba'], ['Hassan', 'Abdelrahman', 'Fathy', 'Mansour']),
    'Nepalese': (['Bikash', 'Suman', 'Prakash', 'Ramesh'], ['Sita', 'Anita', 'Sarita', 'Gita'], ['Thapa', 'Gurung', 'Rai', 'Shrestha']),
    'Bangladeshi': (['Anwar', 'Rafiq', 'Shahid', 'Kamal'], ['Nasrin', 'Rumana', 'Taslima', 'Farzana'], ['Hossain', 'Rahman', 'Islam', 'Uddin']),
    'Jordanian': (['Omar', 'Zaid', 'Laith', 'Hani'], ['Rana', 'Lina', 'Dima', 'Ruba'], ['Haddad', 'Khoury', 'Nasser', 'Odeh']),
    'Sri Lankan': (['Nuwan', 'Chaminda', 'Ruwan', 'Kasun'], ['Dilani', 'Nadeesha', 'Chathuri', 'Sanduni'], ['Perera', 'Fernando', 'Silva', 'Jayasuriya']),
    'Sudanese': (['Osman', 'Mohamed', 'Babiker'], ['Amira', 'Hiba', 'Samah'], ['Elamin', 'Abdalla', 'Osman']),
    'Kenyan': (['Joseph', 'Peter', 'Brian'], ['Faith', 'Mercy', 'Esther'], ['Mwangi', 'Otieno', 'Kamau']),
    'Lebanese': (['Georges', 'Rami', 'Elie'], ['Nadine', 'Carla', 'Maya'], ['Haddad', 'Khalil', 'Saad']),
}

# ---------------------------------------------------------------------------------------------- the 10 industries
# a role: (designation, department, category, monthly salary in AED (basic + allowances), count small, count full, flags)
# flags: head (department head – approves leave), fin (approves the payroll), sales (gets commission), ot (overtime)
# Departments, designations and categories a new company already has are named as they are (DEFAULT_*); the rest are made.
DEFAULT_DEPTS = ('Management', 'Finance', 'Human Resources', 'Sales', 'Logistics & Warehouse')
DEFAULT_DESIG = ('General Manager', 'Accountant', 'Driver')
OFFICE, WORKER, MGMT = 'Office Staff', 'Field and site staff', 'Management'

INDUSTRIES = {
    'CON': {'title': 'Construction', 'apps': ['hse', 'projects', 'maintenance', 'assets', 'crm'], 'roles': [
        ('Projects manager', 'Projects', OFFICE, 32000, 1, 1, 'head'), ('Site engineer', 'Projects', OFFICE, 15000, 1, 2, ''),
        ('QHSE officer', 'QHSE', OFFICE, 12000, 1, 1, 'head'), ('Foreman', 'Site Operations', WORKER, 7500, 1, 2, 'head ot'),
        ('Mason', 'Site Operations', WORKER, 2400, 1, 3, 'ot'), ('Electrician', 'Site Operations', WORKER, 3000, 1, 2, 'ot'),
        ('Steel fixer', 'Site Operations', WORKER, 2200, 0, 2, 'ot'), ('Procurement officer', 'Procurement', OFFICE, 9500, 0, 1, 'head'),
        ('Accountant', 'Finance', OFFICE, 10000, 1, 1, 'head fin'), ('HR officer', 'Human Resources', OFFICE, 9000, 1, 1, 'head'),
        ('Driver', 'Logistics & Warehouse', WORKER, 2600, 0, 2, 'ot')]},
    'FM': {'title': 'Facility management', 'apps': ['maintenance', 'hse', 'projects', 'assets', 'crm'], 'roles': [
        ('Operations manager', 'Operations', OFFICE, 26000, 1, 1, 'head'), ('MEP supervisor', 'Technical Services', WORKER, 9000, 1, 1, 'head ot'),
        ('HVAC technician', 'Technical Services', WORKER, 3800, 1, 3, 'ot'), ('Electrician', 'Technical Services', WORKER, 3200, 1, 2, 'ot'),
        ('Plumber', 'Technical Services', WORKER, 3000, 0, 2, 'ot'), ('Cleaning supervisor', 'Soft Services', WORKER, 4500, 1, 1, 'head ot'),
        ('Cleaner', 'Soft Services', WORKER, 1800, 1, 3, 'ot'), ('HSE officer', 'HSE', OFFICE, 11000, 0, 1, 'head'),
        ('Helpdesk coordinator', 'Operations', OFFICE, 6500, 1, 1, ''), ('Accountant', 'Finance', OFFICE, 10000, 1, 1, 'head fin'),
        ('HR executive', 'Human Resources', OFFICE, 8500, 1, 1, 'head')]},
    'PM': {'title': 'Property management', 'apps': ['property', 'crm', 'maintenance', 'assets'], 'roles': [
        ('Leasing manager', 'Leasing', OFFICE, 28000, 1, 1, 'head'), ('Leasing executive', 'Leasing', OFFICE, 9000, 1, 2, 'sales'),
        ('Property manager', 'Property Operations', OFFICE, 22000, 1, 1, 'head'), ('Property coordinator', 'Property Operations', OFFICE, 8000, 1, 2, ''),
        ('Building technician', 'Property Operations', WORKER, 3500, 1, 3, 'ot'), ('Security guard', 'Property Operations', WORKER, 2500, 0, 2, 'ot'),
        ('Sales agent', 'Sales', OFFICE, 8000, 1, 3, 'sales'), ('Sales manager', 'Sales', OFFICE, 24000, 0, 1, 'head'),
        ('Accountant', 'Finance', OFFICE, 11000, 1, 1, 'head fin'), ('HR executive', 'Human Resources', OFFICE, 8500, 1, 1, 'head')]},
    'OG': {'title': 'Oil and gas', 'apps': ['hse', 'maintenance', 'projects', 'assets', 'crm'], 'roles': [
        ('Operations manager', 'Operations', OFFICE, 45000, 1, 1, 'head'), ('Planning engineer', 'Operations', OFFICE, 22000, 0, 1, ''),
        ('Drilling supervisor', 'Drilling Services', WORKER, 26000, 1, 1, 'head ot'), ('Rig technician', 'Drilling Services', WORKER, 9000, 1, 4, 'ot'),
        ('Mechanical technician', 'Maintenance', WORKER, 8000, 1, 2, 'head ot'), ('HSE officer', 'HSE', OFFICE, 18000, 1, 2, 'head'),
        ('Storekeeper', 'Logistics & Warehouse', WORKER, 6000, 0, 1, 'head'), ('Driver', 'Logistics & Warehouse', WORKER, 4000, 1, 2, 'ot'),
        ('Accountant', 'Finance', OFFICE, 14000, 1, 1, 'head fin'), ('HR officer', 'Human Resources', OFFICE, 13000, 1, 1, 'head')]},
    'RET': {'title': 'Retail', 'apps': ['crm', 'assets', 'maintenance'], 'roles': [
        ('Retail operations manager', 'Store Operations', OFFICE, 30000, 1, 1, 'head'), ('Store manager', 'Store Operations', OFFICE, 12000, 1, 2, ''),
        ('Sales associate', 'Sales', WORKER, 4000, 2, 5, 'sales ot'), ('Sales manager', 'Sales', OFFICE, 16000, 0, 1, 'head'),
        ('Cashier', 'Store Operations', WORKER, 3500, 1, 3, 'ot'), ('Merchandiser', 'Merchandising', OFFICE, 6000, 1, 2, 'head'),
        ('Storekeeper', 'Logistics & Warehouse', WORKER, 4000, 0, 1, 'head ot'), ('Accountant', 'Finance', OFFICE, 10000, 1, 1, 'head fin'),
        ('HR executive', 'Human Resources', OFFICE, 9000, 1, 1, 'head')]},
    'HC': {'title': 'Healthcare', 'apps': ['maintenance', 'assets', 'crm', 'hse'], 'roles': [
        ('Medical director', 'Medical', OFFICE, 60000, 1, 1, 'head'), ('General practitioner', 'Medical', OFFICE, 28000, 1, 3, ''),
        ('Paediatrician', 'Medical', OFFICE, 38000, 0, 1, ''), ('Head nurse', 'Nursing', WORKER, 16000, 1, 1, 'head ot'),
        ('Staff nurse', 'Nursing', WORKER, 9000, 2, 5, 'ot'), ('Pharmacist', 'Pharmacy', OFFICE, 14000, 1, 1, 'head'),
        ('Lab technician', 'Pharmacy', WORKER, 8000, 0, 1, 'ot'), ('Receptionist', 'Front Office', OFFICE, 6000, 1, 2, 'head'),
        ('Accountant', 'Finance', OFFICE, 12000, 1, 1, 'head fin'), ('HR officer', 'Human Resources', OFFICE, 11000, 1, 1, 'head')]},
    'MFG': {'title': 'Manufacturing', 'apps': ['maintenance', 'hse', 'assets', 'crm'], 'roles': [
        ('Plant manager', 'Production', OFFICE, 38000, 1, 1, 'head'), ('Production supervisor', 'Production', WORKER, 12000, 1, 2, 'ot'),
        ('Machine operator', 'Production', WORKER, 3500, 2, 6, 'ot'), ('QC inspector', 'Quality Control', OFFICE, 8000, 1, 2, 'head'),
        ('Maintenance technician', 'Maintenance', WORKER, 5500, 1, 2, 'head ot'), ('Storekeeper', 'Logistics & Warehouse', WORKER, 5000, 0, 1, 'head'),
        ('Forklift operator', 'Logistics & Warehouse', WORKER, 3200, 0, 1, 'ot'), ('Accountant', 'Finance', OFFICE, 12000, 1, 1, 'head fin'),
        ('HR officer', 'Human Resources', OFFICE, 11000, 1, 1, 'head')]},
    'FIN': {'title': 'Finance', 'apps': ['crm', 'assets', 'projects'], 'roles': [
        ('Credit manager', 'Credit', OFFICE, 42000, 1, 1, 'head'), ('Credit analyst', 'Credit', OFFICE, 16000, 1, 3, ''),
        ('Operations officer', 'Operations', OFFICE, 11000, 1, 3, 'head'), ('Compliance officer', 'Compliance', OFFICE, 22000, 1, 1, 'head'),
        ('Relationship manager', 'Sales', OFFICE, 20000, 1, 3, 'sales'), ('Sales manager', 'Sales', OFFICE, 30000, 0, 1, 'head'),
        ('Customer service officer', 'Customer Service', OFFICE, 8000, 1, 2, 'head'), ('Financial controller', 'Finance', OFFICE, 35000, 1, 1, 'head fin'),
        ('Accountant', 'Finance', OFFICE, 12000, 0, 1, ''), ('HR manager', 'Human Resources', OFFICE, 24000, 1, 1, 'head')]},
    'EDU': {'title': 'Education', 'apps': ['crm', 'assets', 'maintenance'], 'roles': [
        ('Head of primary', 'Academic', OFFICE, 26000, 1, 1, 'head'), ('Head of secondary', 'Academic', OFFICE, 27000, 0, 1, ''),
        ('Teacher', 'Academic', OFFICE, 12000, 3, 8, ''), ('Teaching assistant', 'Academic', OFFICE, 5000, 0, 2, ''),
        ('Admissions officer', 'Student Services', OFFICE, 9000, 1, 1, 'head'), ('School nurse', 'Student Services', WORKER, 9000, 0, 1, ''),
        ('Librarian', 'Student Services', OFFICE, 8000, 0, 1, ''), ('Bus driver', 'Transport', WORKER, 3500, 1, 2, 'head ot'),
        ('Accountant', 'Finance', OFFICE, 11000, 1, 1, 'head fin'), ('HR officer', 'Human Resources', OFFICE, 10000, 1, 1, 'head')]},
    'IT': {'title': 'IT and software', 'apps': ['projects', 'crm', 'assets'], 'roles': [
        ('Engineering manager', 'Engineering', OFFICE, 32000, 1, 1, 'head'), ('Senior software engineer', 'Engineering', OFFICE, 20000, 1, 3, ''),
        ('Software engineer', 'Engineering', OFFICE, 12000, 2, 4, ''), ('QA engineer', 'Quality Assurance', OFFICE, 10000, 1, 2, 'head'),
        ('DevOps engineer', 'Cloud Operations', OFFICE, 16000, 1, 2, 'head'), ('Product manager', 'Product', OFFICE, 24000, 0, 1, 'head'),
        ('UI / UX designer', 'Product', OFFICE, 12000, 0, 1, ''), ('Sales manager', 'Sales', OFFICE, 22000, 1, 1, 'head sales'),
        ('Accountant', 'Finance', OFFICE, 10000, 1, 1, 'head fin'), ('HR executive', 'Human Resources', OFFICE, 9000, 1, 1, 'head')]},
}

# the 10 demo companies: (key, country, industry, name, code (3 letters), branch cities)
PLAN = [
    ('ae_con', 'AE', 'CON', 'Falcon Ridge Contracting LLC (Demo)', 'FRC', ['Dubai', 'Sharjah']),
    ('ae_fm', 'AE', 'FM', 'Oasis Facility Services LLC (Demo)', 'OFS', ['Abu Dhabi', 'Dubai']),
    ('ae_pm', 'AE', 'PM', 'Palm Crest Properties LLC (Demo)', 'PCP', ['Dubai']),
    ('sa_og', 'SA', 'OG', 'Rimal Energy Services Co. (Demo)', 'RES', ['Dammam', 'Riyadh']),
    ('sa_ret', 'SA', 'RET', 'Sadu Retail Co. (Demo)', 'SRC', ['Riyadh', 'Jeddah']),
    ('qa_hc', 'QA', 'HC', 'Lulu Bay Medical Centre WLL (Demo)', 'LBM', ['Doha']),
    ('om_mfg', 'OM', 'MFG', 'Wadi Precision Industries LLC (Demo)', 'WPI', ['Sohar', 'Muscat']),
    ('bh_fin', 'BH', 'FIN', 'Dilmun Harbour Finance BSC (Demo)', 'DHF', ['Manama']),
    ('kw_edu', 'KW', 'EDU', 'Bayan Learning Academy (Demo)', 'BLA', ['Salmiya', 'Kuwait City']),
    ('in_it', 'IN', 'IT', 'Kaveri Cloudworks Pvt Ltd (Demo)', 'KCW', ['Bengaluru', 'Pune']),
]
BY_KEY = {p[0]: p for p in PLAN}

# "One industry": the demo company of an industry in any of the 7 countries (the plan's company when it is that pair)
ONE_NAME = {'CON': 'Summit Build Contracting', 'FM': 'Clearview Facility Services', 'PM': 'Keystone Property Management',
            'OG': 'Dunefield Energy Services', 'RET': 'Souq Lane Retail', 'HC': 'Crescent Care Clinic', 'MFG': 'Ironpeak Industries',
            'FIN': 'Tidewater Finance', 'EDU': 'Horizon Learning Academy', 'IT': 'Brightbyte Software'}
LEGAL = {'AE': 'LLC', 'SA': 'Co.', 'QA': 'WLL', 'OM': 'LLC', 'BH': 'WLL', 'KW': 'WLL', 'IN': 'Pvt Ltd'}
ONE_CODE = {'CON': 'SB', 'FM': 'CV', 'PM': 'KP', 'OG': 'DE', 'RET': 'SL', 'HC': 'CC', 'MFG': 'IP', 'FIN': 'TF', 'EDU': 'HL', 'IT': 'BB'}


def spec_for(cc, ind):
    """(key, country, industry, name, code, cities) of the demo company of one industry in one country."""
    cc, ind = str(cc or 'AE').upper(), str(ind or 'CON').upper()
    if cc not in COUNTRIES or ind not in INDUSTRIES:
        raise ValueError('unknown country or industry')
    for p in PLAN:
        if p[1] == cc and p[2] == ind:
            return p
    cities = list(COUNTRIES[cc]['cities'])[:2]
    return (f'{cc.lower()}_{ind.lower()}', cc, ind, f'{ONE_NAME[ind]} {LEGAL[cc]} (Demo)', ONE_CODE[ind] + cc[0], cities)


def plan_for(scope, industry=None, country=None):
    """The demo companies to make: scope 'all' – the 10 of PLAN; 'one' – the chosen industry (in the chosen country)."""
    if scope == 'one':
        return [spec_for(country, industry)]
    return list(PLAN)


def estimate(level, scope):
    """Minutes shown on the form (measured on a 2-core test server with the ready template; bigger servers are faster)."""
    return ESTIMATE[(level, 'one' if scope == 'one' else 'all')]


ESTIMATE = {('simple', 'one'): '1–3', ('heavy', 'one'): '4–8', ('simple', 'all'): '10–18', ('heavy', 'all'): '25–40'}


NO_EXPIRY = D(2099, 12, 31)   # Aadhaar and PAN have no expiry; the document screen needs a date
SICK_OPENING = {'SA': 30, 'QA': 14, 'OM': 21, 'BH': 15, 'KW': 15, 'IN': 12}


def months_for(today, n):
    """The n full months before today's month, then today's month (when today is not the 1st) – [(year, month, last day)]."""
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append((y, m))
    out.reverse()
    res = []
    for (yy, mm) in out:
        nxt = D(yy + (mm == 12), mm % 12 + 1, 1)
        res.append((yy, mm, (nxt - datetime.timedelta(days=1)).day))
    if today.day > 1:
        res.append((today.year, today.month, today.day - 1))
    return res


# ---------------------------------------------------------------------------------------------- the people of one company
def _round(v, step):
    return max(step, int(round(v / step)) * step)


def people(spec, size):
    """The employees of a demo company: [{code, first, last, gender, dob, joined, nat, branch, dept, desig, cat, marital, mgr,
    total, flags}]. size 'small' ≈ 10, 'full' ≈ 20."""
    key, cc, ind, name, code, cities = spec
    C, I = COUNTRIES[cc], INDUSTRIES[ind]
    rows = []
    top = max(r[3] for r in I['roles'])
    rows.append(dict(desig='General Manager', dept='Management', cat=MGMT, aed=int(top * 1.5), flags={'head', 'gm'}))
    for (des, dep, cat, aed, n_small, n_full, fl) in I['roles']:
        for i in range(n_small if size == 'small' else n_full):
            f = set(fl.split())
            if i > 0:
                f.discard('head')
                f.discard('fin')
            rows.append(dict(desig=des, dept=dep, cat=cat, aed=aed, flags=f))
    # department heads before the people who report to them (the import checks "Reports to" against the rows above)
    rows = rows[:1] + [r for r in rows[1:] if 'head' in r['flags']] + [r for r in rows[1:] if 'head' not in r['flags']]
    n_nat = len(rows) if C['nationals'] >= 99 else max(1, round(C['nationals'] * len(rows) / 20))
    nat_at = set(range(0, len(rows), max(1, len(rows) // n_nat))) if n_nat < len(rows) else set(range(len(rows)))
    nat_at = set(sorted(nat_at)[:n_nat])
    heads = {}
    used = set()
    for i, r in enumerate(rows):
        r['code'] = f'{code}-{i + 1:03d}'
        s = r['code']
        r['nat'] = C['national'] if i in nat_at else _pick(s + 'nat', EXPATS[cc])
        fem_pct = 80 if r['desig'] in ('Staff nurse', 'Head nurse', 'Receptionist', 'Teaching assistant') else (40 if r['cat'] != WORKER else 10)
        r['gender'] = 'Female' if _chance(s + 'g', fem_pct) else 'Male'
        males, females, lasts = NAMES.get(r['nat'], NAMES['Indian'])
        firsts = females if r['gender'] == 'Female' else males
        f0, l0 = int(_n(s + 'f', 3)), int(_n(s + 'l', 3))
        for k in range(len(firsts) * len(lasts)):   # no two people of one company with the same name
            r['first'], r['last'] = firsts[(f0 + k) % len(firsts)], lasts[(l0 + k // len(firsts)) % len(lasts)]
            if (r['first'], r['last']) not in used:
                break
        used.add((r['first'], r['last']))
        senior = r['aed'] >= 20000
        r['dob'] = D(1968 + int(_n(s + 'y', 2)) % (14 if senior else 26) + (0 if senior else 8), 1 + int(_n(s + 'm', 2)) % 12, 1 + int(_n(s + 'd', 2)) % 28)
        r['joined'] = D(2016 + int(_n(s + 'j', 2)) % 8, 1 + int(_n(s + 'jm', 2)) % 12, 1 + int(_n(s + 'jd', 2)) % 27)
        if r['joined'] < D(r['dob'].year + 21, r['dob'].month, 1):   # at least 21 when joining; still before the demo months
            r['dob'] = D(r['joined'].year - 22 - int(_n(s + 'age', 1)) % 6, r['dob'].month, r['dob'].day)
        r['marital'] = 'Married' if (r['dob'].year < 1992 and not _chance(s + 'ms', 20)) else 'Single'
        r['branch'] = cities[0] if (r['cat'] != WORKER or len(cities) == 1) else cities[i % len(cities)]
        r['total'] = _round(r['aed'] * C['fx'], C['round'])
        if 'head' in r['flags'] and r['dept'] not in heads:
            heads[r['dept']] = r['code']
    gm = rows[0]['code']
    for r in rows[1:]:
        h = heads.get(r['dept'])
        r['mgr'] = gm if (h is None or h == r['code']) else h
    rows[0]['mgr'] = None
    return rows


def managers(rows):
    """Employees who get a login: the general manager, the department heads and the payroll approver."""
    return [r['code'] for r in rows if r['flags'] & {'head', 'gm', 'fin'}]


# ---------------------------------------------------------------------------------------------- the workbook of one company
def _q(model, **kw):
    return '{q:' + model + '?' + '&'.join(f'{k}={v}' for k, v in kw.items()) + '}'


def _j(o):
    return json.dumps(o, ensure_ascii=False, default=str)


class Book:
    """The demo workbook being written: the client template's own sheets (tsheet) and extra API sheets (nsheet)."""

    def __init__(self, template_path):
        from openpyxl import load_workbook
        self.wb = load_workbook(template_path)
        self.ix = self.wb['Index']
        self.hdr_ix = [c.value for c in self.ix[1]]
        self.order = 300

    def tsheet(self, name, rows):
        ws = self.wb[name]
        hdr = [c.value for c in ws[1]]
        ws.delete_rows(2, ws.max_row)
        for r in rows:
            ws.append([r.get(h) for h in hdr])

    def nsheet(self, name, how, endpoint, phase, cols, rows, note, screen='', app='HRMS', method=None):
        if not rows:
            return
        from openpyxl.styles import Font, PatternFill
        ws = self.wb.create_sheet(name[:31])
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='2F5D50')
        for r in rows:
            ws.append([r.get(c) if not isinstance(r.get(c), (dict, list)) else _j(r.get(c)) for c in cols])
        self.order += 10
        vals = {'Order': self.order, 'Sheet': name[:31], 'App': app, 'Screen': screen, 'Endpoint': endpoint, 'How': how, 'Phase': phase,
                'Method': method, 'Mandatory': 'No – demo data', 'Hand import': 'No – Data workbook', 'Notes': note}
        self.ix.append([vals.get(h) for h in self.hdr_ix])

    def bytes(self):
        out = io.BytesIO()
        self.wb.save(out)
        return out.getvalue()


A = ['@ref', '@as', '@method', '@endpoint', '@body', '@expect', '@note']
MA = ['@month', '@ref', '@date', '@as', '@method', '@endpoint', '@body', '@expect', '@note']


def emp(c):
    return _q('EmpManagement.emp_master', emp_code=c)


def branch(b):
    return _q('OrganisationManager.brnch_mstr', branch_code=b)


def build(spec, level, today, ctx, template_path):
    """→ the demo workbook (.xlsx bytes) of one demo company. ctx: what the new company already has (read by demo_jobs.context):
    pay [(component code, share of the monthly total)], leave {'annual': name, 'sick': name}, docs (document types), depts,
    desigs, cats (names), apps (active app keys)."""
    key, cc, ind, name, code, cities = spec
    C, I, L = COUNTRIES[cc], INDUSTRIES[ind], LEVELS[level]
    rows = people(spec, L['size'])
    by = {r['code']: r for r in rows}
    months = months_for(today, L['months'])
    first = D(months[0][0], months[0][1], 1)
    B = Book(template_path)
    br = {f'{code}-{i + 1:02d}': c for i, c in enumerate(cities)}
    br_of = {c: b for b, c in br.items()}
    allb = ','.join(br)
    mgrs = managers(rows)
    fin = next((r['code'] for r in rows if 'fin' in r['flags']), rows[0]['code'])
    domain = code.lower() + '-demo.example'

    # ------------------------------------------------------------------ setup: company, branches, masters
    cd = {'Address line 1': C['cities'][cities[0]].split(',')[0], 'Address line 2': ', '.join(C['cities'][cities[0]].split(',')[1:]).strip(),
          'City': cities[0], 'P.O. box': _n(code + 'po', 5)}
    if C.get('wps'):
        cd.update({'MoHRE establishment ID (WPS)': '000000' + _n(code + 'mol', 7), 'Bank routing code (WPS agent)': C['bank'][2]})
    B.tsheet('Company details', [cd])
    B.tsheet('Branches', [{'Branch code': b, 'Branch name': f'{c} – {I["title"].lower()}' if i else f'{c} head office', 'City': c,
                           'Address': C['cities'][c], 'Phone': C['phone'] + _n(b, 7), 'E-mail': f'{b.lower()}@{domain}',
                           'Start date': D(2015, 1, 1), 'Probation (days)': 180, 'Active': 'Yes'} for i, (b, c) in enumerate(br.items())])
    have = {k: {x.lower() for x in ctx.get(k, [])} for k in ('depts', 'desigs', 'cats')}
    dep_names = sorted({r['dept'] for r in rows if r['dept'].lower() not in have['depts']})
    des_names = sorted({r['desig'] for r in rows if r['desig'].lower() not in have['desigs']})
    cat_names = sorted({r['cat'] for r in rows if r['cat'].lower() not in have['cats']})
    B.tsheet('Departments', [{'Department code': f'{code}-D{i + 1:02d}', 'Department name': n, 'Description': n,
                              'Branches (codes, comma separated)': allb, 'Active': 'Yes'} for i, n in enumerate(dep_names)])
    B.tsheet('Designations', [{'Designation code': f'{code}-T{i + 1:02d}', 'Designation': n, 'Description': n,
                               'Branches (codes, comma separated)': allb, 'Active': 'Yes'} for i, n in enumerate(des_names)])
    B.tsheet('Categories', [{'Category code': f'{code}-C{i + 1:02d}', 'Category': n, 'Description': n,
                             'Branches (codes, comma separated)': allb, 'Active': 'Yes'} for i, n in enumerate(cat_names)])
    sales = [r for r in rows if 'sales' in r['flags']]
    B.tsheet('Salary components', [{'Code': 'COMM', 'Name': 'Sales commission', 'Component type': 'Addition', 'Payroll category': 'commission',
                                    'Component value type': 'Variable', 'Deduct leave': 'No', 'Show in payslip': 'Yes',
                                    'Branches (codes, comma separated)': b} for b in br] if sales else [])
    B.tsheet('Document types', [])

    # ------------------------------------------------------------------ setup: employees and their files
    e_rows, s_rows, b_rows, d_rows, o_rows, fam, qual, job = [], [], [], [], [], [], [], []
    bank, bank_branch, routing, acct_fn = C['bank']
    soon = today + datetime.timedelta(days=25)
    for i, r in enumerate(rows):
        c = r['code']
        b = br_of[r['branch']]
        mail = f"{r['first'].lower().replace(' ', '')}.{r['last'].lower().replace(' ', '')}.{c.lower().replace('-', '')}"
        e = {'Employee code': c, 'First name': r['first'], 'Last name': r['last'], 'Gender': r['gender'], 'Date of birth': r['dob'],
             'Joining date': r['joined'], 'Nationality': r['nat'], 'Branch': b, 'Department': r['dept'], 'Designation': r['desig'],
             'Category': r['cat'], 'Marital status': r['marital'], 'Personal e-mail': f'{mail}@mail.example', 'Company e-mail': f'{c.lower()}@{domain}',
             'Mobile': C['mobile'] + _n(c, 7), 'City': r['branch'], 'Present address': r['branch'], 'Reports to (employee code)': r['mgr'],
             'Overtime applies': 'Yes' if 'ot' in r['flags'] else 'No', 'Active': 'Yes', 'Attendance source': 'Manual Attendance',
             'Confirmation date': r['joined'] + datetime.timedelta(days=180)}
        if C.get('wps'):
            e['MoHRE person ID (14 digits)'] = '1001' + _n(c + 'mol', 10)
        e_rows.append(e)
        left = r['total']
        for j, (comp, share) in enumerate(ctx['pay']):
            amt = left if j == len(ctx['pay']) - 1 else _round(r['total'] * share, max(1, C['round'] // 5))
            left -= amt
            s_rows.append({'Employee code': c, 'Salary component': comp, 'Amount': amt, 'Active': 'Yes'})
        acct, iban = acct_fn(c + 'acct')
        wps_route = routing if (routing and len(routing) <= 9 and routing.isdigit()) else None   # the 9-digit UAE routing code only
        b_rows.append({'Employee code': c, 'Bank name': bank, 'Account number': acct, 'IBAN': iban or None, 'Routing code': wps_route,
                       'Branch name': bank_branch if wps_route or not routing else f'{bank_branch} (IFSC {routing})', 'Active': 'Yes'})
        if r['nat'] != C['national'] or cc == 'IN':
            d_rows.append({'Employee code': c, 'Document type': 'Passport', 'Document number': 'P' + _n(c + 'pp', 7),
                           'Issue date': D(2021, 6, 1), 'Expiry date': soon if i == 3 else D(2031, 5, 31)})
        idn = _national_id(cc, r)
        if idn and C['id_doc'] in ctx['docs']:
            d_rows.append({'Employee code': c, 'Document type': C['id_doc'], 'Document number': idn, 'Issue date': D(2025, 2, 1),
                           'Expiry date': NO_EXPIRY if cc == 'IN' else (soon if i == 5 else D(2027, 1, 31))})
        if cc == 'IN' and 'PAN card' in ctx['docs']:
            d_rows.append({'Employee code': c, 'Document type': 'PAN card', 'Document number': _pan(c, r['last']), 'Issue date': D(2018, 4, 1),
                           'Expiry date': NO_EXPIRY})
        if ctx.get('leave', {}).get('annual'):
            o_rows.append({'Employee code': c, 'Leave type': ctx['leave']['annual'], 'Days': 12 if r['joined'] < first.replace(year=first.year - 1) else 5,
                           'As on date': first - datetime.timedelta(days=1), 'Mode': 'set'})
        if ctx.get('leave', {}).get('sick') and cc in SICK_OPENING:   # countries whose sick leave is a balance (Saudi Arabia …)
            o_rows.append({'Employee code': c, 'Leave type': ctx['leave']['sick'], 'Days': SICK_OPENING[cc], 'As on date': first - datetime.timedelta(days=1),
                           'Mode': 'set'})
        if r['marital'] == 'Married' and i % 3 == 1:
            males, females, _ = NAMES.get(r['nat'], NAMES['Indian'])
            fam.append({'Employee code': c, 'Name': f"{_pick(c + 'sp', females if r['gender'] == 'Male' else males)} {r['last']}",
                        'Relation': 'Wife' if r['gender'] == 'Male' else 'Husband', 'Date of birth': D(r['dob'].year + 2, 3, 9),
                        'Company pays (amount a year)': 0, 'Active': 'Yes'})
        if r['cat'] != WORKER and i % 2 == 0:
            qual.append({'Employee code': c, 'Qualification': 'Master’s degree' if r['total'] >= 20000 * C['fx'] else 'Bachelor’s degree',
                         'Subject': r['dept'], 'Institution': 'Demo University', 'Completed on': D(r['dob'].year + 22, 6, 30)})
        if i % 4 == 2:
            job.append({'Employee code': c, 'Company': 'Demo Holdings Ltd', 'Designation': r['desig'], 'From': D(r['joined'].year - 4, 1, 1),
                        'To': r['joined'] - datetime.timedelta(days=30), 'Years': 4, 'Last salary a month': _round(r['total'] * 0.8, C['round']),
                        'Reason for leaving': 'Better offer'})
    B.tsheet('Employees', e_rows)
    B.tsheet('Employee salary', s_rows)
    B.tsheet('Bank details', b_rows)
    B.tsheet('Employee documents', d_rows)
    B.tsheet('Leave opening balances', o_rows)
    B.tsheet('Family members', fam)
    B.tsheet('Qualifications', qual)
    B.tsheet('Job history', job)

    # ------------------------------------------------------------------ setup: logins, payroll approver, commission column
    B.nsheet('Logins', 'action', '/zeo-apps/api/user-employee/', 'setup', A,
             [{'@note': f"Login for {c} {by[c]['first']} {by[c]['last']} (user name = employee code)",
               '@body': {'action': 'create_user', 'employee_id': emp(c), 'method': 'slip'}} for c in mgrs],
             'Settings › Users: logins for the general manager, the department heads and the finance approver', 'Settings › Users')
    B.nsheet('Payroll approver', 'action', '/users/company-admins/', 'setup', A,
             [{'@note': f'{fin} approves each payroll – HR prepares it (segregation of duties)',
               '@body': {'user': _q('UserManagement.CustomUser', username=fin), 'admin': 'true'}}],
             'Settings › Users › Administrators: the finance approver', 'Settings › Users')
    # attendance rules: office hours for everyone, an earlier shift with overtime for site / field staff
    rules = [{'@endpoint': '/attendance-plus/api/rules/', '@body': {'name': 'Office hours', 'scope': 'company', 'default_start': '08:30', 'default_end': '17:30',
                                                                  'grace_in_minutes': 10, 'break_minutes': 60, 'ot_after_minutes': 30, 'ot_needs_approval': True}}]
    if any(r['cat'] == WORKER for r in rows):
        rules.append({'@endpoint': '/attendance-plus/api/rules/', '@body': {
            'name': 'Site and field shift', 'scope': 'category', 'priority': 5,
            'category_id': _q('OrganisationManager.ctgry_master', ctgry_title=WORKER), 'default_start': '07:00', 'default_end': '16:00',
            'grace_in_minutes': 5, 'break_minutes': 60, 'ot_after_minutes': 15, 'ot_needs_approval': True}})
    B.nsheet('Attendance rules', 'action', '', 'setup', A, rules, 'Attendance › Rules: working hours, grace, breaks and overtime', 'Attendance › Rules')
    if sales:
        B.nsheet('Commission input', 'action', '', 'setup', A,
                 [{'@method': 'PUT', '@endpoint': '/payroll-plus/api/monthly-inputs/components/' + _q('PayrollManagement.SalaryComponent', code='COMM') + '/',
                   '@body': {'monthly_input': True}, '@note': 'Payroll › Monthly inputs: the sales commission is a column of the monthly grid'}],
                 'Payroll › Monthly inputs › Columns', 'Payroll › Monthly inputs')

    # ------------------------------------------------------------------ monthly: accrual, attendance, leave, commission, payroll
    acc, att, lv_req, lv_app, inputs, pay = [], [], [], [], [], []
    staff = [r for r in rows if 'gm' not in r['flags']]
    for k, (y, m, last) in enumerate(months):
        mo = f'{y}-{m:02d}'
        current = (y, m) == (today.year, today.month)
        acc.append({'@month': mo, '@date': f'{mo}-01', '@endpoint': '/leave-policy/api/accrual/', '@body': {'month': mo}, '@note': f'Leave accrual {mo}'})
        att.append({'@month': mo, 'job': 'demo-attendance', 'month': mo, 'until': (today if current else D(y, m, last)).isoformat()})
        n_leave = 2 if L['size'] == 'small' else 3
        for t in range(n_leave + (1 if current else 0)):
            r = staff[(k * 5 + t * 3) % len(staff)]
            sick = (t == 1)
            lt = ctx['leave'].get('sick' if sick else 'annual')
            if not lt:
                continue
            if current:
                a = min(last + 1, 27) if t >= n_leave - 1 else max(2, min(last - 2, 3 + t * 4))
                if a > 28:
                    continue
                z = min(a + (0 if sick else 2), 28)
            else:
                a = 3 + (t * 9 + k * 2) % 20
                z = a + (1 if sick else 3)
            a, z = _workdays(C, y, m, a, z)   # a leave starts and ends on a working day (weekday numbers of the country's weekend)
            if a is None:
                continue
            ref = f'LV{r["code"][-3:]}{y % 100:02d}{m:02d}{t}'
            when = D(y, m, max(1, min(a - 4, last))) if not current else min(today, D(y, m, max(1, a - 4)))
            lv_req.append({'@month': mo, '@ref': ref, '@date': when.isoformat(), 'employee': emp(r['code']),
                           'leave_type': _q('calendars.leave_type', name=lt, branch=branch(br_of[r['branch']])),
                           'start_date': D(y, m, a).isoformat(), 'end_date': D(y, m, z).isoformat(), 'reason': f'{lt} – demo',
                           'lv_document': _cert_pdf(f'DEMO medical certificate - {r["first"]} {r["last"]} - {a:02d}/{m:02d}/{y} - invented data') if sick else None})
            future = current and D(y, m, a) >= today
            if not future and r['mgr'] in mgrs:   # a request for days still to come waits for the manager (shows in Approvals)
                lv_app.append({'@month': mo, '@date': when.isoformat(), '@as': r['mgr'], '@endpoint': '/leave-policy/api/requests/{ref:' + ref + '}/approve/',
                               '@body': {'note': 'Approved'}, '@note': f'{r["mgr"]} approves the leave of {r["code"]}'})
        if current:
            continue
        for b in br:
            if sales:
                rr = [{'employee': emp(r['code']), 'values': {'COMM': _round(r['total'] * (0.08 + 0.04 * ((k + i) % 3)), C['round'])}}
                      for i, r in enumerate(sales) if br_of[r['branch']] == b]
                if rr:
                    inputs.append({'@month': mo, '@date': f'{mo}-25', '@endpoint': '/payroll-plus/api/monthly-inputs/',
                                   '@body': {'branch': branch(b), 'month': mo, 'rows': rr}, '@note': f'{b} {mo}: sales commission'})
            if not any(br_of[r['branch']] == b for r in rows):
                continue
            ref = f'RUN{b[-2:]}{y % 100:02d}{m:02d}'
            pay.append({'@month': mo, '@ref': ref, '@date': f'{mo}-{min(last, 27)}', '@endpoint': '/payroll/api/PayrollRun/',
                        '@body': {'name': f'{br[b]} payroll {m:02d}/{y}', 'month': m, 'year': y, 'branch_ids': [branch(b)]},
                        '@note': f'{b}: HR makes the payroll of {m:02d}/{y}'})
            pay.append({'@month': mo, '@date': f'{mo}-{min(last, 28)}', '@method': 'PATCH', '@endpoint': '/payroll/api/PayrollRun/{ref:' + ref + '}/',
                        '@as': fin, '@body': {'status': 'approved'}, '@note': f'{b}: {fin} approves the payroll of {m:02d}/{y}'})
            if C.get('wps'):
                pay.append({'@month': mo, '@date': f'{mo}-{min(last, 28)}', '@endpoint': '/payroll/sif-data/',
                            '@body': {'payroll_run_id': '{ref:' + ref + '}', 'branch_ids': [branch(b)]}, '@note': f'{b}: WPS salary file {m:02d}/{y}'})
    B.nsheet('Leave accrual', 'action', '', 'monthly', MA, acc, 'Leave › Accrual: run on the 1st of each month', 'Leave › Accrual')
    B.nsheet('Leave requests', 'api', '/calendars/api/emp-leave-request/', 'monthly',
             ['@month', '@ref', '@date', 'employee', 'leave_type', 'start_date', 'end_date', 'reason', 'lv_document'], lv_req, 'Leave › Requests', 'Leave › Requests')
    B.nsheet('Leave approvals', 'action', '', 'monthly', MA, lv_app, 'Leave › Approvals: the line manager approves', 'Leave › Approvals')
    # after the leave of the month is approved (no punches on leave days), before the payroll
    B.nsheet('Attendance', 'job', '', 'monthly', ['@month', 'job', 'month', 'until'], att,
             'Attendance › Daily attendance: in / out punches of every working day (late arrivals, overtime of site staff)', 'Attendance')
    B.nsheet('Monthly inputs', 'action', '', 'monthly', MA, inputs, 'Payroll › Monthly inputs: sales commission', 'Payroll › Monthly inputs')
    B.nsheet('Payroll', 'action', '', 'monthly', MA, pay, 'Payroll › Runs: each branch every month, approved by finance', 'Payroll › Runs')

    from . import demo_apps
    demo_apps.add(B, spec, level, today, ctx, rows, months, br, br_of)

    rm = B.wb['Read me']
    rm.insert_rows(1, 3)
    rm['A1'] = f'ZEO demo data – {name} (fictional company, invented data)'
    rm['A2'] = f'{LEVELS[level]["title"]}: {len(rows)} employees, {months[0][1]:02d}/{months[0][0]} – {months[-1][1]:02d}/{months[-1][0]}.'
    rm['A3'] = 'Every name, number and address in this file is invented.'
    return B.bytes()


def _workdays(C, y, m, a, z):
    """(first, last) day of the month moved off the weekend; (None, None) when no working day is left in the month."""
    wk = C.get('weekend', (5, 6))
    last = (D(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)).day
    while a <= min(z, last) and D(y, m, a).weekday() in wk:
        a += 1
    z = max(a, min(z, last))
    while z > a and D(y, m, z).weekday() in wk:
        z -= 1
    if a > last or D(y, m, a).weekday() in wk:
        return None, None
    return a, z


def _national_id(cc, r):
    s = r['code'] + 'nid'
    if cc == 'AE':
        return _eid(r['dob'].year, _n(s, 7))
    if cc == 'SA':
        return ('1' if r['nat'] == 'Saudi' else '2') + _n(s, 9)
    if cc == 'QA':
        return ('2' if r['nat'] == 'Qatari' else '2') + str(r['dob'].year)[2:] + _n(s, 8)
    if cc == 'OM':
        return _n(s, 8)
    if cc == 'BH':
        return f'{r["dob"].year % 100:02d}{r["dob"].month:02d}' + _n(s, 5)
    if cc == 'KW':
        return ('2' if r['dob'].year < 2000 else '3') + f'{r["dob"].year % 100:02d}{r["dob"].month:02d}{r["dob"].day:02d}' + _n(s, 5)
    if cc == 'IN':
        return _aadhaar(s)
    return ''
