"""v1.62.9 (pending item 9) – the demo workbook: a complete small company in the ZEO data workbook format, made from the client
template on this server, that loads into a NEW company with 0 failed rows (Settings › Data workbook › Download the demo workbook →
Upload → Check → Load).

"Desert Palm Trading LLC" (fictional): two branches (Dubai head office DPT-DXB, Sharjah warehouse DPT-SHJ), 14 employees with
salaries, bank accounts (valid UAE IBANs), passports, Emirates IDs, labour cards, leave opening balances, family, qualifications and
job history; logins for the four managers; then three months (August – October 2026) worked through the screens' own API:
leave accrual on the 1st, leave requests approved by the line manager, a sales commission entered as a monthly input, the payroll of
each branch made by HR and approved by the Finance Manager (segregation of duties), and the WPS salary file.

Every name, number and address is invented – no real person or company. The rows are the same on every server (no random
numbers), so loading the file twice is safe: the second load skips what the first one made.

    python manage.py make_demo_workbook [--out file.xlsx]
"""
import datetime
import io
import json
import zlib

D = datetime.date


def _eid(year, seven):
    """v1.71.0 (Ask ZEO data quality): a fictional Emirates ID with a right check digit (Luhn) – it was always 1, so the
    numbers of the demo company failed the Emirates ID check."""
    body = f'784{year}{seven}'
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return f'784-{year}-{seven}-{(10 - total % 10) % 10}'
NAME = 'Desert Palm Trading LLC'
FILE_NAME = 'ZEO_Demo_Data_Desert_Palm_Trading.xlsx'
MONTHS = [(2026, 8), (2026, 9), (2026, 10)]
BR = {'DPT-DXB': ('Dubai head office', 'Dubai', 'Office 1804, Bay Square 7, Business Bay'),
      'DPT-SHJ': ('Sharjah warehouse', 'Sharjah', 'Warehouse 12, Industrial Area 15')}
# A new ZEO company already has the usual masters (departments Management, Finance, Human Resources, Sales, Logistics & Warehouse;
# designations General Manager, Accountant, Driver; categories Management, Office Staff): the workbook names them (their codes
# end with a part of the company's own name, so they differ from company to company) and adds only what this company has on top.
DEPTS = [('DPT-SHW', 'Showroom')]
DESIG = [('DPT-FM', 'Finance manager'), ('DPT-HRM', 'HR manager'), ('DPT-WHM', 'Warehouse manager'), ('DPT-HRX', 'HR executive'),
         ('DPT-SEX', 'Sales executive'), ('DPT-STK', 'Storekeeper'), ('DPT-HLP', 'Warehouse helper')]
CATS = [('DPT-WRK', 'Warehouse workers')]
# code, first, last, gender, birth, joined, nationality, branch, department, designation, category, marital, manager, salary
EMPS = [
    ('DPT-001', 'Khalid', 'Rahman', 'Male', D(1978, 3, 14), D(2016, 2, 1), 'Emirati', 'DPT-DXB', 'Management', 'General Manager', 'Management', 'Married', None,
     {'BAS': 25000, 'HOUSING': 10000, 'TRANSPORT': 3000}),
    ('DPT-002', 'Priya', 'Varghese', 'Female', D(1985, 9, 2), D(2018, 5, 6), 'Indian', 'DPT-DXB', 'Finance', 'DPT-FM', 'Office Staff', 'Married', 'DPT-001',
     {'BAS': 16000, 'HOUSING': 6000, 'TRANSPORT': 2000}),
    ('DPT-003', 'Omar', 'Haddad', 'Male', D(1987, 1, 20), D(2019, 3, 10), 'Jordanian', 'DPT-DXB', 'Human Resources', 'DPT-HRM', 'Office Staff', 'Married', 'DPT-001',
     {'BAS': 14000, 'HOUSING': 5000, 'TRANSPORT': 1500}),
    ('DPT-004', 'Ramesh', 'Pillai', 'Male', D(1982, 6, 11), D(2017, 8, 15), 'Indian', 'DPT-SHJ', 'Logistics & Warehouse', 'DPT-WHM', 'Office Staff', 'Married', 'DPT-001',
     {'BAS': 11000, 'HOUSING': 4000, 'TRANSPORT': 1500}),
    ('DPT-005', 'Grace', 'Mendoza', 'Female', D(1991, 11, 5), D(2021, 1, 17), 'Filipino', 'DPT-DXB', 'Finance', 'Accountant', 'Office Staff', 'Single', 'DPT-002',
     {'BAS': 7500, 'HOUSING': 3000, 'TRANSPORT': 800}),
    ('DPT-006', 'Sara', 'Al Hashimi', 'Female', D(1995, 4, 28), D(2023, 9, 3), 'Emirati', 'DPT-DXB', 'Human Resources', 'DPT-HRX', 'Office Staff', 'Single', 'DPT-003',
     {'BAS': 9000, 'HOUSING': 3500, 'TRANSPORT': 1000}),
    ('DPT-007', 'Daniel', 'Okafor', 'Male', D(1990, 7, 9), D(2020, 10, 4), 'Nigerian', 'DPT-DXB', 'Sales', 'DPT-SEX', 'Office Staff', 'Married', 'DPT-001',
     {'BAS': 8000, 'HOUSING': 3000, 'TRANSPORT': 1200}),
    ('DPT-008', 'Ayesha', 'Siddiqui', 'Female', D(1993, 2, 16), D(2022, 4, 11), 'Pakistani', 'DPT-DXB', 'Sales', 'DPT-SEX', 'Office Staff', 'Single', 'DPT-001',
     {'BAS': 7500, 'HOUSING': 3000, 'TRANSPORT': 1200}),
    ('DPT-009', 'Tomas', 'Novak', 'Male', D(1989, 12, 1), D(2024, 2, 18), 'Czech', 'DPT-DXB', 'DPT-SHW', 'DPT-SEX', 'Office Staff', 'Single', 'DPT-001',
     {'BAS': 7000, 'HOUSING': 2800, 'TRANSPORT': 1200}),
    ('DPT-010', 'Suresh', 'Kumar', 'Male', D(1986, 5, 23), D(2018, 11, 25), 'Indian', 'DPT-SHJ', 'Logistics & Warehouse', 'DPT-STK', 'DPT-WRK', 'Married', 'DPT-004',
     {'BAS': 4000, 'HOUSING': 1500, 'TRANSPORT': 500}),
    ('DPT-011', 'Bikash', 'Thapa', 'Male', D(1992, 8, 30), D(2021, 6, 6), 'Nepalese', 'DPT-SHJ', 'Logistics & Warehouse', 'Driver', 'DPT-WRK', 'Married', 'DPT-004',
     {'BAS': 3200, 'HOUSING': 1200, 'TRANSPORT': 300}),
    ('DPT-012', 'Ravi', 'Shankar', 'Male', D(1994, 3, 3), D(2022, 9, 12), 'Indian', 'DPT-SHJ', 'Logistics & Warehouse', 'Driver', 'DPT-WRK', 'Single', 'DPT-004',
     {'BAS': 3000, 'HOUSING': 1200, 'TRANSPORT': 300}),
    ('DPT-013', 'Anwar', 'Hossain', 'Male', D(1996, 10, 14), D(2023, 3, 19), 'Bangladeshi', 'DPT-SHJ', 'Logistics & Warehouse', 'DPT-HLP', 'DPT-WRK', 'Single', 'DPT-004',
     {'BAS': 2000, 'HOUSING': 800, 'TRANSPORT': 200}),
    ('DPT-014', 'Joseph', 'Mwangi', 'Male', D(1997, 1, 25), D(2025, 7, 7), 'Kenyan', 'DPT-SHJ', 'Logistics & Warehouse', 'DPT-HLP', 'DPT-WRK', 'Single', 'DPT-004',
     {'BAS': 2000, 'HOUSING': 800, 'TRANSPORT': 200}),
]
MANAGERS = ('DPT-001', 'DPT-002', 'DPT-003', 'DPT-004')
FINANCE = 'DPT-002'
# month → (employee, leave type, first day, last day)
LEAVES = {8: [('DPT-005', 'Annual Leave', 10, 13), ('DPT-011', 'Annual Leave', 3, 6), ('DPT-008', 'Sick Leave', 20, 21)],
          9: [('DPT-007', 'Annual Leave', 14, 18), ('DPT-010', 'Annual Leave', 21, 24), ('DPT-013', 'Sick Leave', 8, 8)],
          10: [('DPT-012', 'Annual Leave', 5, 8), ('DPT-006', 'Annual Leave', 19, 20), ('DPT-009', 'Sick Leave', 27, 28)]}
COMMISSION = {8: {'DPT-007': 1800, 'DPT-008': 1250, 'DPT-009': 900}, 9: {'DPT-007': 2100, 'DPT-008': 1600, 'DPT-009': 1150},
              10: {'DPT-007': 2400, 'DPT-008': 1350, 'DPT-009': 1700}}


def _n(s, digits):
    """A fixed number of `digits` digits for the text s – the same on every server (not random)."""
    v = zlib.crc32(s.encode()) * 2654435761 % (10 ** digits)
    return f'{v:0{digits}d}'


def _iban(bank, account):
    """A UAE IBAN with valid check digits (ISO 13616 mod 97)."""
    bban = bank + account
    num = ''.join(str(int(ch, 36)) for ch in bban + 'AE00')
    return f'AE{98 - int(num) % 97:02d}{bban}'


def _cert_pdf(text):
    """A one-page PDF (a demo medical certificate) as a data URI – the leave request API takes it as lv_document."""
    import base64
    txt = text.replace('(', '').replace(')', '')
    stream = f'BT /F1 11 Tf 40 780 Td ({txt}) Tj ET'
    objs = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
            f'<< /Length {len(stream)} >>\nstream\n{stream}\nendstream', '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offs = '%PDF-1.4\n', []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f'{i} 0 obj\n{o}\nendobj\n'
    x = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n' + ''.join(f'{o:010d} 00000 n \n' for o in offs)
    out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n'
    return 'data:application/pdf;base64,' + base64.b64encode(out.encode('latin-1')).decode()


def _q(model, **kw):
    return '{q:' + model + '?' + '&'.join(f'{k}={v}' for k, v in kw.items()) + '}'


def _j(o):
    return json.dumps(o, ensure_ascii=False)


def build(template_path):
    """→ the demo workbook as bytes (.xlsx)."""
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill
    wb = load_workbook(template_path)
    head = PatternFill('solid', fgColor='2F5D50')

    def tsheet(name, rows):
        ws = wb[name]
        hdr = [c.value for c in ws[1]]
        ws.delete_rows(2, ws.max_row)
        for r in rows:
            ws.append([r.get(h) for h in hdr])

    ix = wb['Index']
    hdr_ix = [c.value for c in ix[1]]
    order = [300]

    def nsheet(name, how, endpoint, phase, cols, rows, note, screen=''):
        ws = wb.create_sheet(name[:31])
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = head
        for r in rows:
            ws.append([r.get(c) for c in cols])
        ws.freeze_panes = 'A2'
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(60, max(12, len(str(c)) + 4))
        order[0] += 10
        vals = {'Order': order[0], 'Sheet': name[:31], 'App': 'HRMS', 'Screen': screen, 'Endpoint': endpoint, 'How': how, 'Phase': phase,
                'Mandatory': 'No – demo data', 'Hand import': 'No – Data workbook', 'Notes': note}
        ix.append([vals.get(h) for h in hdr_ix])

    emp = lambda c: _q('EmpManagement.emp_master', emp_code=c)   # noqa: E731
    branch = lambda b: _q('OrganisationManager.brnch_mstr', branch_code=b)   # noqa: E731
    by = {e[0]: e for e in EMPS}

    # ------------------------------------------------------------------ setup (the template's own sheets)
    tsheet('Company details', [{'Address line 1': 'Office 1804, Bay Square 7', 'Address line 2': 'Business Bay', 'City': 'Dubai', 'P.O. box': '412233',
                                'MoHRE establishment ID (WPS)': '0000009876543', 'Bank routing code (WPS agent)': '302620122'}])
    tsheet('Branches', [{'Branch code': c, 'Branch name': n, 'City': city, 'Address': a, 'Phone': '+9714' + _n(c, 7),
                         'E-mail': f'{c.lower()}@desertpalm.example', 'Start date': D(2016, 1, 1), 'Probation (days)': 180, 'Active': 'Yes'}
                        for c, (n, city, a) in BR.items()])
    allb = ','.join(BR)
    tsheet('Departments', [{'Department code': c, 'Department name': n, 'Description': n, 'Branches (codes, comma separated)': allb, 'Active': 'Yes'}
                           for c, n in DEPTS])
    tsheet('Designations', [{'Designation code': c, 'Designation': n, 'Description': n, 'Branches (codes, comma separated)': allb, 'Active': 'Yes'}
                            for c, n in DESIG])
    tsheet('Categories', [{'Category code': c, 'Category': n, 'Description': n, 'Branches (codes, comma separated)': allb, 'Active': 'Yes'}
                          for c, n in CATS])
    # a salary component belongs to one branch: one row per branch
    tsheet('Salary components', [{'Code': 'COMM', 'Name': 'Sales commission', 'Component type': 'Addition', 'Payroll category': 'Other Allowance',
                                  'Component value type': 'Variable', 'Deduct leave': 'No', 'Show in payslip': 'Yes',
                                  'Branches (codes, comma separated)': b} for b in BR])
    tsheet('Document types', [])
    emp_rows, sal_rows, bank_rows, doc_rows, open_rows = [], [], [], [], []
    for (c, fn, ln, g, dob, joined, nat, b, dep, des, cat, ms, mgr, sal) in EMPS:
        emp_rows.append({'Employee code': c, 'First name': fn, 'Last name': ln, 'Gender': g, 'Date of birth': dob, 'Joining date': joined,
                         'Nationality': nat, 'Branch': b, 'Department': dep, 'Designation': des, 'Category': cat, 'Marital status': ms,
                         'Personal e-mail': f'{fn.lower()}.{ln.lower().replace(" ", "")}@mail.example', 'Company e-mail': f'{c.lower()}@desertpalm.example',
                         'Mobile': '+97150' + _n(c, 7), 'City': BR[b][1], 'Present address': BR[b][1],
                         'MoHRE person ID (14 digits)': '1001' + _n(c + 'mol', 10), 'Reports to (employee code)': mgr,
                         'Overtime applies': 'Yes' if cat == 'DPT-WRK' else 'No', 'Active': 'Yes', 'Attendance source': 'Manual Attendance'})
        for comp, amt in sal.items():
            sal_rows.append({'Employee code': c, 'Salary component': comp, 'Amount': amt, 'Active': 'Yes'})
        acct = _n(c + 'acct', 16)
        bank_rows.append({'Employee code': c, 'Bank name': 'Emirates NBD', 'Account number': acct[-13:], 'IBAN': _iban('260', acct),
                          'Routing code': '302620122', 'Branch name': 'Business Bay', 'Active': 'Yes'})
        doc_rows.append({'Employee code': c, 'Document type': 'Passport', 'Document number': 'P' + _n(c + 'pp', 7), 'Issue date': D(2021, 6, 1),
                         'Expiry date': D(2031, 5, 31)})
        doc_rows.append({'Employee code': c, 'Document type': 'Emirates ID', 'Document number': _eid(dob.year, _n(c + "eid", 7)),
                         'Issue date': D(2025, 2, 1), 'Expiry date': D(2027, 1, 31) if c != 'DPT-012' else D(2026, 11, 30)})
        open_rows.append({'Employee code': c, 'Leave type': 'Annual Leave', 'Days': 12 if joined < D(2025, 1, 1) else 5, 'As on date': D(2026, 7, 31),
                          'Mode': 'set'})
    tsheet('Employees', emp_rows)
    tsheet('Employee salary', sal_rows)
    tsheet('Bank details', bank_rows)
    tsheet('Employee documents', doc_rows)
    tsheet('Leave opening balances', open_rows)
    tsheet('Family members', [{'Employee code': 'DPT-002', 'Name': 'Thomas Varghese', 'Relation': 'Husband', 'Date of birth': D(1983, 4, 9),
                               'Company pays (amount a year)': 0, 'Active': 'Yes'},
                              {'Employee code': 'DPT-004', 'Name': 'Lakshmi Pillai', 'Relation': 'Wife', 'Date of birth': D(1985, 2, 17),
                               'Company pays (amount a year)': 0, 'Active': 'Yes'}])
    tsheet('Qualifications', [{'Employee code': 'DPT-002', 'Qualification': 'Chartered Accountant', 'Subject': 'Accounting', 'Institution': 'Demo Institute of Accountants',
                               'Completed on': D(2009, 7, 31)},
                              {'Employee code': 'DPT-005', 'Qualification': 'B.Sc.', 'Subject': 'Accountancy', 'Institution': 'Demo University',
                               'Completed on': D(2012, 3, 31)}])
    tsheet('Job history', [{'Employee code': 'DPT-004', 'Company': 'Demo Logistics FZE', 'Designation': 'Storekeeper', 'From': D(2009, 1, 1), 'To': D(2017, 7, 31),
                            'Years': 8, 'Last salary a month': 7000, 'Reason for leaving': 'Better offer'}])

    # ------------------------------------------------------------------ setup: logins, approver
    A = ['@ref', '@as', '@method', '@endpoint', '@body', '@expect', '@note']
    MA = ['@month', '@ref', '@date', '@as', '@method', '@endpoint', '@body', '@expect', '@note']
    nsheet('Logins', 'action', '/zeo-apps/api/user-employee/', 'setup', A,
           [{'@note': f'Login for {c} {by[c][1]} {by[c][2]} (user name = employee code, printed password slip)',
             '@body': _j({'action': 'create_user', 'employee_id': emp(c), 'method': 'slip'})} for c in MANAGERS],
           'Settings › Users: logins for the general manager, the finance manager, the HR manager and the warehouse manager', 'Settings › Users')
    nsheet('Commission input', 'action', '', 'setup', A,
           [{'@method': 'PUT', '@endpoint': '/payroll-plus/api/monthly-inputs/components/' + _q('PayrollManagement.SalaryComponent', code='COMM') + '/',
             '@body': _j({'monthly_input': True}), '@note': 'Payroll › Monthly inputs: the sales commission (COMM) is a column of the monthly grid'}],
           'Payroll › Monthly inputs › Columns: the sales commission is entered each month', 'Payroll › Monthly inputs')
    nsheet('Payroll approver', 'action', '/users/company-admins/', 'setup', A,
           [{'@note': f'{FINANCE} {by[FINANCE][1]} {by[FINANCE][2]} (Finance manager) approves each payroll – company administrator; HR prepares it '
                      '(segregation of duties)', '@body': _j({'user': _q('UserManagement.CustomUser', username=FINANCE), 'admin': 'true'})}],
           'Settings › Users › Administrators: the finance manager approves the payroll', 'Settings › Users')

    # ------------------------------------------------------------------ monthly: accrual, leave, commission, payroll, WPS
    acc, lv_req, lv_app, inputs, pay = [], [], [], [], []
    for y, m in MONTHS:
        mo = f'{y}-{m:02d}'
        acc.append({'@month': mo, '@date': f'{mo}-01', '@endpoint': '/leave-policy/api/accrual/', '@body': _j({'month': mo}),
                    '@note': f'Leave › Accrual of {mo} (run on the 1st)'})
        for (c, lt, a, z) in LEAVES.get(m, []):
            ref = f'LV{c[-3:]}{m:02d}'
            when = D(y, m, max(1, a - 5))
            lv_req.append({'@month': mo, '@ref': ref, '@date': when.isoformat(), 'employee': emp(c),
                           'leave_type': _q('calendars.leave_type', name=lt, branch=branch(by[c][7])),   # one copy of the type per branch
                           'start_date': D(y, m, a).isoformat(), 'end_date': D(y, m, z).isoformat(), 'reason': f'{lt} – demo',
                           'lv_document': _cert_pdf(f'DEMO medical certificate - {by[c][1]} {by[c][2]} - {a:02d}/{m:02d}/{y} to {z:02d}/{m:02d}/{y} - '
                                                    'invented data') if lt == 'Sick Leave' else None})
            mgr = by[c][12]
            lv_app.append({'@month': mo, '@date': when.isoformat(), '@as': mgr if mgr in MANAGERS else None,
                           '@endpoint': '/leave-policy/api/requests/{ref:' + ref + '}/approve/', '@body': _j({'note': 'Approved'}),
                           '@note': f'{mgr} approves the {lt.lower()} of {c} ({a}–{z}/{m:02d})'})
        for b in BR:
            rows = [{'employee': emp(c), 'values': {'COMM': v}} for c, v in COMMISSION.get(m, {}).items() if by[c][7] == b]
            if rows:
                inputs.append({'@month': mo, '@date': f'{mo}-25', '@endpoint': '/payroll-plus/api/monthly-inputs/',
                               '@body': _j({'branch': branch(b), 'month': mo, 'rows': rows}), '@note': f'{b} {mo}: sales commission of the month'})
            ref = f'RUN{b[-3:]}{m:02d}'
            pay.append({'@month': mo, '@ref': ref, '@date': f'{mo}-28', '@endpoint': '/payroll/api/PayrollRun/',
                        '@body': _j({'name': f'{BR[b][0]} payroll {m:02d}/{y}', 'month': m, 'year': y, 'branch_ids': [branch(b)]}),
                        '@note': f'{b}: HR makes the payroll of {m:02d}/{y}'})
            pay.append({'@month': mo, '@date': f'{mo}-28', '@method': 'PATCH', '@endpoint': '/payroll/api/PayrollRun/{ref:' + ref + '}/', '@as': FINANCE,
                        '@body': _j({'status': 'approved'}), '@note': f'{b}: the finance manager approves the payroll of {m:02d}/{y}'})
            pay.append({'@month': mo, '@date': f'{mo}-28', '@endpoint': '/payroll/sif-data/',
                        '@body': _j({'payroll_run_id': '{ref:' + ref + '}', 'branch_ids': [branch(b)]}), '@note': f'{b}: WPS salary file of {m:02d}/{y}'})
    nsheet('Leave accrual', 'action', '', 'monthly', MA, acc, 'Leave › Accrual: run at the start of each month', 'Leave › Accrual')
    nsheet('Leave requests', 'api', '/calendars/api/emp-leave-request/', 'monthly',
           ['@month', '@ref', '@date', 'employee', 'leave_type', 'start_date', 'end_date', 'reason', 'lv_document'], lv_req,
           'Leave › Requests (HR applies for the employee)', 'Leave › Requests')
    nsheet('Leave approvals', 'action', '', 'monthly', MA, lv_app, 'Leave › Approvals: the line manager approves', 'Leave › Approvals')
    nsheet('Monthly inputs', 'action', '', 'monthly', MA, inputs, 'Payroll › Monthly inputs: the sales commission of the month', 'Payroll › Monthly inputs')
    nsheet('Payroll', 'action', '', 'monthly', MA, pay, 'Payroll › Payroll runs: each branch every month, approved by Finance, WPS file', 'Payroll › Runs')

    rm = wb['Read me']
    rm.insert_rows(1, 4)
    rm['A1'] = f'ZEO demo workbook – {NAME} (fictional company, invented data)'
    rm['A1'].font = Font(bold=True, size=14)
    rm['A2'] = ('Load it into a NEW company with HR and Payroll switched on: Settings › Data workbook › Upload → Check → Load. It makes 2 branches, '
                '14 employees with their salaries, bank accounts and documents, logins for the 4 managers (user name = employee code), and '
                'works August – October 2026: leave approved by the managers, a sales commission, the payroll of each branch approved by the '
                'finance manager and the WPS file.')
    rm['A3'] = 'Every name, number and address in this file is invented. Loading it twice is safe – the second load skips what the first one made.'
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
