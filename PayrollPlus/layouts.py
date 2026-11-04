# v1.62.11 (item 227, follow-up 03/11/2026): new module – designable payment file layouts (templates)
"""v1.62.11 (item 227) – every payment output is a template: WPS SIF and the banks' / exchange houses' WPS layouts, the KSA wages
file, bulk bank transfer, cheque list, cash sheet and the lists of the company's own methods.

A layout = {code, name, method, source, file_type, separator, line_end, file_name, title, records: [{kind, name, when, fields}]}
  method     wps | bank_transfer | cheque | cash | other
  source     wps (the SIF data of the run: person ID, routing code, IBAN … – bankfiles.sif_data), ksa (Saudi wages data),
             payments (the run's payment lines of the method – run_payments.lines)
  file_type  delimited (separator), fixed (field widths), csv (comma, quoted when needed), xlsx, pdf (cheque / cash lists)
  records    header (once, first) · detail (one per employee; `when: variable` = only employees with variable pay) · trailer (once, last)
  field      {source, label, value (constant), format: {date, decimals, dec_sep, thousands, blank_zero, width, pad, align, upper, max}}
Built-in layouts (BUILTIN) are read-only – "Make a copy" gives the company an editable one. The company's layouts are kept in
ZeoApps.CompanyOption 'payment_layouts' (no new table). The built-in CBUAE SIF gives the file of Payroll › WPS file byte for byte.
"""
import csv
import datetime
import io
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction

from Translations.i18n import tr

# ------------------------------------------------------------------ field catalogue
D, H = 'detail', 'file'   # where a field can be used: per employee, or header / trailer (file level)
CATALOGUE = [
    # per employee
    ('emp_code', 'Employee code', D, 'text'), ('name', 'Employee name', D, 'text'), ('seq', 'Row number', D, 'int'),
    ('person_id', 'MOHRE person ID (14)', D, 'text'), ('emirates_id', 'Emirates ID / national ID', D, 'text'), ('labour_card', 'Labour card no.', D, 'text'),
    ('iqama', 'Iqama / national ID (KSA, 10 digits)', D, 'text'),
    ('iban', 'IBAN', D, 'text'), ('account', 'Account (IBAN or salary card)', D, 'text'), ('bank_name', 'Bank name', D, 'text'),
    ('routing', 'Routing code of the agent / bank', D, 'text'), ('agent_code', 'WPS agent code (Bank / WPS)', D, 'text'),
    ('branch', 'Branch', D, 'text'),
    ('pay_start', 'Pay start date', D, 'date'), ('pay_end', 'Pay end date', D, 'date'), ('days', 'Days paid', D, 'int'),
    ('fixed', 'Fixed pay', D, 'amount'), ('variable', 'Variable pay', D, 'amount'), ('net', 'Net pay', D, 'amount'),
    ('amount', 'Amount paid (net − held)', D, 'amount'), ('held', 'Held amount', D, 'amount'), ('leave_days', 'Leave days', D, 'number'),
    ('basic', 'Basic salary', D, 'amount'), ('housing', 'Housing allowance', D, 'amount'), ('other_earnings', 'Other earnings', D, 'amount'),
    ('deductions', 'Deductions', D, 'amount'),
    ('evp_housing', 'EVP housing allowance', D, 'amount'), ('evp_conveyance', 'EVP conveyance allowance', D, 'amount'),
    ('evp_medical', 'EVP medical allowance', D, 'amount'), ('evp_passage', 'EVP annual passage allowance', D, 'amount'),
    ('evp_overtime', 'EVP overtime allowance', D, 'amount'), ('evp_other', 'EVP all other allowances', D, 'amount'),
    ('evp_leave', 'EVP leave encashment', D, 'amount'),
    ('justification', 'Justification of a salary change', D, 'text'), ('resident', 'Resident (Yes / No)', D, 'text'),
    ('cbd_type', 'CBD employee type (N / E / C)', D, 'text'), ('sar_bank', 'Beneficiary bank code (SARIE, 4 letters)', D, 'text'),
    ('payment_ref', 'Payment reference', D, 'text'), ('cheque_no', 'Cheque number', D, 'text'),
    ('words_en', 'Amount in words (English)', D, 'text'), ('words_ar', 'Amount in words (Arabic)', D, 'text'),
    ('d_1000', 'Notes of 1000', D, 'int'), ('d_500', 'Notes of 500', D, 'int'), ('d_200', 'Notes of 200', D, 'int'), ('d_100', 'Notes of 100', D, 'int'),
    ('d_50', 'Notes of 50', D, 'int'), ('d_20', 'Notes of 20', D, 'int'), ('d_10', 'Notes of 10', D, 'int'), ('d_5', 'Notes of 5', D, 'int'),
    ('d_1', 'Coins of 1', D, 'int'), ('d_050', 'Coins of 50 fils', D, 'int'), ('d_025', 'Coins of 25 fils', D, 'int'), ('d_rest', 'Rest (below 25 fils)', D, 'amount'),
    ('paid_on', 'Paid on', D, 'text'), ('paid_ref', 'Paid – reference', D, 'text'),
    # file level (header / trailer) – also usable per employee
    ('employer_id', 'Employer unique ID (13)', H, 'text'), ('employer_routing', 'Routing code of the employer bank', H, 'text'),
    ('employer_reference', 'Employer reference', H, 'text'), ('company', 'Company name', H, 'text'), ('company_code', 'Company code', H, 'text'),
    ('company_account', 'Company bank account (paying)', H, 'text'), ('bank', 'Paying bank', H, 'text'),
    ('estb_id', 'Establishment ID (KSA)', H, 'text'), ('mol_estb_id', 'MoL establishment ID (KSA)', H, 'text'), ('dest_bank', 'Establishment bank (SARIE)', H, 'text'),
    ('count', 'Number of employees', H, 'int'), ('total', 'Total amount', H, 'amount'), ('total_fixed', 'Total fixed pay', H, 'amount'),
    ('total_variable', 'Total variable pay', H, 'amount'), ('total_words_en', 'Total in words (English)', H, 'text'), ('total_words_ar', 'Total in words (Arabic)', H, 'text'),
    ('salary_month', 'Salary month (first day)', H, 'date'), ('now', 'File date and time', H, 'datetime'), ('today', 'Today', H, 'date'),
    ('payment_date', 'Payment date of the run', H, 'date'), ('file_ref', 'File reference (unique)', H, 'text'), ('run_name', 'Payroll run', H, 'text'),
    ('currency', 'Currency', H, 'text'),
    ('const', 'Constant text', 'any', 'text'), ('blank', 'Blank filler', 'any', 'text'),
]
CAT = {c[0]: c for c in CATALOGUE}
METHODS = ['wps', 'bank_transfer', 'cheque', 'cash', 'other']
FILE_TYPES = ['delimited', 'fixed', 'csv', 'xlsx', 'pdf']
SOURCES_ = ['wps', 'ksa', 'payments']


def _f(src, label='', value=None, **fmt):
    x = {'source': src, 'label': label or (CAT[src][1] if src in CAT else src)}
    if value is not None:
        x['value'] = value
    if fmt:
        x['format'] = fmt
    return x


def _c(v, label=''):
    return _f('const', label or 'Record type', value=v)


DATE = {'date': '%Y-%m-%d'}
A2 = {'decimals': 2}
SIF_EDR = [_c('EDR'), _f('person_id', 'Employee unique ID'), _f('routing', 'Routing code of the agent'), _f('account', 'Employee account'),
           _f('pay_start', **DATE), _f('pay_end', **DATE), _f('days', blank_zero=True), _f('fixed', 'Income fixed component', **A2),
           _f('variable', 'Income variable component', **A2), _f('leave_days', 'Days on leave')]
SIF_SCR = [_c('SCR'), _f('employer_id'), _f('employer_routing'), _f('now', 'File creation date', date='%Y-%m-%d'), _f('now', 'File creation time', date='%H%M'),
           _f('salary_month', 'Salary month', date='%m%Y'), _f('count', 'EDR count', blank_zero=True), _f('total', 'Total salary', **A2),
           _c('AED', 'Payment currency'), _f('employer_reference')]
SIF_NAME = '{employer_id}{now:%y%m%d%H%M%S}.SIF'


def _sif(code, name, detail=None, trailer=None, extra=None):
    recs = [{'kind': 'detail', 'name': 'EDR', 'fields': detail or SIF_EDR}]
    recs += extra or []
    recs.append({'kind': 'trailer', 'name': 'SCR', 'fields': trailer or SIF_SCR})
    return {'code': code, 'name': name, 'method': 'wps', 'source': 'wps', 'file_type': 'delimited', 'separator': ',', 'line_end': 'CRLF',
            'file_name': SIF_NAME, 'records': recs, 'builtin': True}


EVP = {'kind': 'detail', 'name': 'EVP', 'when': 'variable', 'fields': [_c('EVP'), _f('person_id'), _f('routing')] + [_f(k, **A2) for k in (
    'evp_housing', 'evp_conveyance', 'evp_medical', 'evp_passage', 'evp_overtime', 'evp_other', 'evp_leave')]}
DMY = {'date': '%d/%m/%Y'}
CBD_FIELDS = [_f('emp_code', 'EmpId'), _f('name', 'Employee Name'), _f('cbd_type', 'Employee_Type'), _f('net', 'Salary', **A2),
              _f('variable', 'VariablePay', decimals=2, blank_zero=True), _f('account', 'AccountNo'), _f('routing', 'AGENT_BANK-RTN_CODE'),
              _f('person_id', 'MOL_PERSONID'), _f('salary_month', 'Sal_Month', date='%m'), _f('salary_month', 'Sal_Year', date='%Y'),
              _f('pay_start', 'FromDate', **DMY), _f('pay_end', 'ToDate', **DMY), _f('leave_days', 'Leave', blank_zero=True)]
SAR = {'decimals': 2, 'dec_sep': ','}
BUILTIN = {
    'cbuae_sif': _sif('cbuae_sif', 'Central Bank (CBUAE) WPS SIF'),
    'sif_ref_none': _sif('sif_ref_none', 'CBUAE WPS SIF – employer reference "NONE"',
                         trailer=SIF_SCR[:9] + [_f('employer_reference', default='NONE')]),
    'sif_evp': _sif('sif_evp', 'CBUAE WPS SIF with EVP lines', extra=[EVP]),
    'adib_mohre': _sif('adib_mohre', 'ADIB WPS SIF – MOHRE registered (2 more EDR columns)', detail=SIF_EDR + [_f('justification'), _f('resident')]),
    'cbd_xlsx': {'code': 'cbd_xlsx', 'name': 'Commercial Bank of Dubai – WPS salary Excel', 'method': 'wps', 'source': 'wps', 'file_type': 'xlsx',
                 'sheet': 'Sheet1', 'file_name': 'CBD_WPS_{employer_id}_{mmyyyy}.xlsx', 'builtin': True, 'text_cells': True,
                 'records': [{'kind': 'header', 'name': 'Heading row', 'fields': [_c(f['label'], f['label']) for f in CBD_FIELDS]},
                             {'kind': 'detail', 'name': 'Employee row', 'fields': CBD_FIELDS}]},
    'mudad_wages': {'code': 'mudad_wages', 'name': 'KSA wage protection file (Mudad / HRSD)', 'method': 'wps', 'source': 'ksa', 'file_type': 'delimited',
                    'separator': '\t', 'line_end': 'CRLF', 'file_name': 'MUDAD_{estb_id}_{yyyymm}.txt', 'builtin': True,
                    'records': [{'kind': 'header', 'name': 'Establishment', 'fields': [
                        _f('dest_bank', upper=True), _f('estb_id'), _f('company_account'), _c('SAR', 'Currency'),
                        _f('payment_date', 'Value date', date='%Y%m%d'), _f('total', **SAR), _f('blank', 'Debit date'), _f('file_ref'),
                        _f('blank', 'Rejection code'), _f('mol_estb_id')]},
                        {'kind': 'detail', 'name': 'Employee', 'fields': [
                            _f('amount', 'Net amount', **SAR), _f('iban'), _f('name', max=140), _f('sar_bank'), _f('payment_ref', 'Payment description'),
                            _f('blank', 'Return code'), _f('basic', **SAR), _f('housing', **SAR), _f('other_earnings', **SAR), _f('deductions', **SAR),
                            _f('iqama'), _f('blank', 'TRN-REF'), _f('blank', 'TRN-STATUS'), _f('blank', 'TRN-DATE')]},
                        {'kind': 'trailer', 'name': 'End of file', 'fields': [_c('-', 'End of file')]}]},
    'generic_csv': {'code': 'generic_csv', 'name': 'ZEO bulk transfer CSV (generic)', 'method': 'bank_transfer', 'source': 'payments', 'file_type': 'csv',
                    'line_end': 'CRLF', 'bom': True, 'file_name': 'TRANSFER_{account}_{mmyyyy}.csv', 'builtin': True,
                    'records': [{'kind': 'header', 'name': 'Heading row', 'fields': [_c(x, x) for x in (
                        'Employee code', 'Beneficiary name', 'IBAN / account', 'Bank', 'Routing code', 'Amount', 'Currency', 'Payment reference', 'Value date')]},
                        {'kind': 'detail', 'name': 'Payment', 'fields': [_f('emp_code'), _f('name'), _f('account'), _f('bank_name'), _f('routing'),
                                                                        _f('amount', **A2), _c('AED', 'Currency'), _f('payment_ref'), _f('payment_date', **DMY)]}]},
    'cheque_list': {'code': 'cheque_list', 'name': 'Cheque list', 'method': 'cheque', 'source': 'payments', 'file_type': 'pdf', 'builtin': True,
                    'title': 'Cheque list – salaries {month}/{year}', 'file_name': 'CHEQUES_{yyyymm}.pdf',
                    'records': [{'kind': 'detail', 'name': 'Cheque', 'fields': [
                        _f('seq', 'No.', width=24), _f('cheque_no', 'Cheque no.', width=62), _f('name', 'Payee', width=120), _f('emp_code', width=60),
                        _f('amount', 'Amount (AED)', decimals=2, thousands=True, width=66), _f('words_en', width=230), _f('words_ar', width=220)]},
                        {'kind': 'trailer', 'name': 'Total', 'fields': [_f('blank'), _f('blank'), _c('Total', 'Total'), _f('blank'),
                                                                       _f('total', decimals=2, thousands=True), _f('total_words_en'), _f('total_words_ar')]}]},
    'cash_sheet': {'code': 'cash_sheet', 'name': 'Cash payment sheet', 'method': 'cash', 'source': 'payments', 'file_type': 'pdf', 'builtin': True,
                   'title': 'Cash payment sheet – salaries {month}/{year} – {branch}', 'file_name': 'CASH_{branch}_{yyyymm}.pdf',
                   'records': [{'kind': 'detail', 'name': 'Employee', 'fields': [
                       _f('seq', 'No.', width=30), _f('emp_code', width=70), _f('name', 'Employee', width=220),
                       _f('amount', 'Amount (AED)', decimals=2, thousands=True, width=90), _f('blank', 'Received by (signature)', width=260), _f('blank', 'Date', width=110)]},
                       {'kind': 'trailer', 'name': 'Total', 'fields': [_f('blank'), _f('blank'), _c('Total', 'Total'), _f('total', decimals=2, thousands=True),
                                                                      _f('blank'), _f('blank')]}]},
    'other_list': {'code': 'other_list', 'name': 'Payment list (Excel)', 'method': 'other', 'source': 'payments', 'file_type': 'xlsx', 'builtin': True,
                   'sheet': 'Payments', 'file_name': '{method}_{yyyymm}.xlsx',
                   'records': [{'kind': 'header', 'name': 'Heading row', 'fields': [_c(x, x) for x in (
                       'No.', 'Employee code', 'Employee', 'Branch', 'Amount (AED)', 'Payment method', 'Paid on', 'Reference')]},
                       {'kind': 'detail', 'name': 'Employee', 'fields': [_f('seq'), _f('emp_code'), _f('name'), _f('branch'), _f('amount', **A2),
                                                                        _f('payment_ref', 'Payment method'), _f('paid_on'), _f('paid_ref')]},
                       {'kind': 'trailer', 'name': 'Total', 'fields': [_c('Total'), _f('blank'), _f('blank'), _f('blank'), _f('total', **A2)]}]},
}
_notes = [_f(k, k.replace('d_', '× ').replace('050', '50 fils').replace('025', '25 fils'), width=30) for k in
          ('d_1000', 'd_500', 'd_200', 'd_100', 'd_50', 'd_20', 'd_10', 'd_5', 'd_1', 'd_050', 'd_025')]
BUILTIN['cash_sheet_notes'] = dict(BUILTIN['cash_sheet'], code='cash_sheet_notes', name='Cash payment sheet with note count', records=[
    {'kind': 'detail', 'name': 'Employee', 'fields': [_f('seq', 'No.', width=22), _f('emp_code', width=52), _f('name', 'Employee', width=110),
                                                       _f('amount', 'Amount (AED)', decimals=2, thousands=True, width=58)] + _notes +
     [_f('d_rest', 'Rest', decimals=2, blank_zero=True, width=34), _f('blank', 'Received by (signature)', width=110), _f('blank', 'Date', width=50)]},
    {'kind': 'trailer', 'name': 'Total', 'fields': [_f('blank'), _f('blank'), _c('Total', 'Total'), _f('total', decimals=2, thousands=True)] +
     [_f('t_' + k[2:], blank_zero=False) for k in ('d_1000', 'd_500', 'd_200', 'd_100', 'd_50', 'd_20', 'd_10', 'd_5', 'd_1', 'd_050', 'd_025')] +
     [_f('t_rest', decimals=2, blank_zero=True), _f('blank'), _f('blank')]}])
for _k in ('d_1000', 'd_500', 'd_200', 'd_100', 'd_50', 'd_20', 'd_10', 'd_5', 'd_1', 'd_050', 'd_025', 'd_rest'):
    CAT['t_' + _k[2:]] = ('t_' + _k[2:], 'Total – ' + CAT[_k][1], H, 'int' if _k != 'd_rest' else 'amount')
    CATALOGUE.append(CAT['t_' + _k[2:]])
for _r in BUILTIN['cash_sheet_notes']['records']:   # the totals fields were made before their catalogue entries
    for _x in _r['fields']:
        if _x['source'] in CAT and _x['label'] == _x['source']:
            _x['label'] = CAT[_x['source']][1]
DEFAULT_FOR = {'wps': 'cbuae_sif', 'bank_transfer': 'generic_csv', 'cheque': 'cheque_list', 'cash': 'cash_sheet', 'other': 'other_list'}


# ------------------------------------------------------------------ company layouts (CompanyOption 'payment_layouts')
def _opt(lock=False):
    from . import bankfiles as B
    return B._opt('payment_layouts', lock)


def company_layouts():
    return list((_opt().value or {}).get('layouts') or [])


def save_layouts(fn):
    with transaction.atomic():
        o = _opt(lock=True)
        v = dict(o.value or {})
        rows, res = fn(list(v.get('layouts') or []), v)
        v['layouts'] = rows
        o.value = v
        o.save()
    return res


def get(code):
    if code in BUILTIN:
        return BUILTIN[code]
    return next((l for l in company_layouts() if l.get('code') == code), None)


def all_layouts():
    return list(BUILTIN.values()) + company_layouts()


def copy_of(code, name=None):
    import copy
    base = get(code)
    if base is None:
        return None
    x = copy.deepcopy(base)
    x.pop('builtin', None)
    x['based_on'] = code
    x['name'] = name or (base['name'] + ' (copy)')
    return x


# ------------------------------------------------------------------ validation of a designed layout
def clean(d, old=None):
    errs = {}
    v = dict(old or {})
    for k in ('name', 'method', 'source', 'file_type', 'separator', 'line_end', 'file_name', 'title', 'sheet', 'based_on', 'bom', 'text_cells', 'quote'):
        if k in d:
            v[k] = d[k]
    v['name'] = str(v.get('name') or '').strip()[:80]
    if not v['name']:
        errs['name'] = tr('Enter a name.')
    if v.get('method') not in METHODS:
        errs['method'] = tr('Choose the payment method.')
    if v.get('source') not in SOURCES_:
        v['source'] = 'wps' if v.get('method') == 'wps' else 'payments'
    if v.get('source') in ('wps', 'ksa') and v.get('method') != 'wps':
        errs['source'] = tr('Only a WPS layout can use the WPS / KSA data.')
    if v.get('file_type') not in FILE_TYPES:
        errs['file_type'] = tr('Choose the file type.')
    if v.get('file_type') == 'delimited':
        sep = v.get('separator')
        sep = {'TAB': '\t', 'tab': '\t', '\\t': '\t'}.get(sep, sep)
        if not sep or len(sep) > 3 or sep in ('\r', '\n'):
            errs['separator'] = tr('Enter the separator (e.g. , ; | or TAB).')
        v['separator'] = sep
    if v.get('file_type') == 'pdf' and v.get('method') not in ('cheque', 'cash', 'other'):
        errs['file_type'] = tr('PDF is for cheque, cash and other lists.')
    v['line_end'] = 'LF' if v.get('line_end') == 'LF' else 'CRLF'
    v['file_name'] = re.sub(r'[\\/:*?"<>|\r\n]+', '_', str(v.get('file_name') or '').strip())[:120]
    if not v['file_name']:
        errs['file_name'] = tr('Enter the file name rule.')
    recs = d.get('records', v.get('records'))
    if not isinstance(recs, list) or not recs:
        errs['records'] = tr('Add at least the record of the employees (detail).')
        recs = []
    out = []
    kinds = [r.get('kind') for r in recs if isinstance(r, dict)]
    if 'detail' not in kinds:
        errs['records'] = tr('Add at least the record of the employees (detail).')
    for i, r in enumerate(recs):
        if not isinstance(r, dict) or r.get('kind') not in ('header', 'detail', 'trailer'):
            errs['records'] = tr('Record {0}: choose header, detail or trailer.', i + 1); continue
        fs = []
        for j, f in enumerate(r.get('fields') or []):
            src = str((f or {}).get('source') or '')
            if src not in CAT:
                errs['records'] = tr('Record {0}, field {1}: choose a field from the list.', i + 1, j + 1); continue
            if CAT[src][2] == D and r['kind'] != 'detail':
                errs['records'] = tr('Record {0}, field {1}: "{2}" is a field of an employee – only in the detail record.', i + 1, j + 1, tr(CAT[src][1])); continue
            fm = {}
            for k, val in ((f.get('format') or {}).items()):
                if k in ('width', 'max', 'decimals') and val not in (None, ''):
                    try:
                        val = int(val)
                        if val < 0 or val > 400:
                            raise ValueError
                    except (TypeError, ValueError):
                        errs['records'] = tr('Record {0}, field {1}: width, length and decimals are whole numbers.', i + 1, j + 1); continue
                if k in ('date', 'dec_sep', 'pad', 'align', 'default') and val not in (None, ''):
                    val = str(val)[:20]
                if k in ('thousands', 'blank_zero', 'upper'):
                    val = bool(val)
                if val not in (None, ''):
                    fm[k] = val
            if v.get('file_type') == 'fixed' and not fm.get('width'):
                errs['records'] = tr('Record {0}, field {1}: a fixed-width file needs the width of every field.', i + 1, j + 1)
            x = {'source': src, 'label': str(f.get('label') or CAT[src][1])[:60]}
            if src == 'const':
                x['value'] = str(f.get('value') or '')[:120]
            if fm:
                x['format'] = fm
            fs.append(x)
        if not fs:
            errs['records'] = tr('Record {0} has no fields.', i + 1)
        out.append({'kind': r['kind'], 'name': str(r.get('name') or r['kind'])[:40], 'when': 'variable' if r.get('when') == 'variable' else 'always', 'fields': fs})
    v['records'] = out
    return v, errs


# ------------------------------------------------------------------ rendering
def _num(v):
    if v in (None, ''):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def fmt_value(f, ctx, pad_width=True):
    src = f['source']
    fm = f.get('format') or {}
    if src == 'const':
        v = f.get('value', '')
    elif src == 'blank':
        v = ''
    else:
        v = ctx.get(src)
    typ = CAT.get(src, (src, '', '', 'text'))[3]
    if isinstance(v, (datetime.date, datetime.datetime)):
        v = v.strftime(fm.get('date') or ('%Y-%m-%d %H:%M' if isinstance(v, datetime.datetime) else '%Y-%m-%d'))
    elif typ in ('amount', 'number', 'int') and v is not None and not isinstance(v, str):
        n = _num(v)
        if n is not None:
            if fm.get('blank_zero') and n == 0:
                v = ''
            else:
                dec = fm.get('decimals')
                if dec is None:
                    s = f'{n:f}' if typ != 'int' else str(int(n))
                    if typ == 'number' and '.' in s:
                        s = s.rstrip('0').rstrip('.')
                else:
                    s = f'{n:,.{dec}f}' if fm.get('thousands') else f'{n:.{dec}f}'
                if fm.get('dec_sep') and fm['dec_sep'] != '.':
                    s = s.replace(',', '\x00').replace('.', fm['dec_sep']).replace('\x00', '.' if fm['dec_sep'] == ',' else ',')
                v = s
    elif fm.get('blank_zero') and (v in (0, '0') or (isinstance(v, str) and _num(v) == 0)):
        v = ''
    v = '' if v is None else str(v)
    if not v and fm.get('default'):
        v = fm['default']
    if fm.get('upper'):
        v = v.upper()
    if fm.get('max'):
        v = v[:int(fm['max'])]
    w = fm.get('width') if pad_width else None   # in Excel / PDF the width is the column width, not padding
    if w:
        pad = (fm.get('pad') or ' ')[:1]
        al = fm.get('align') or ('right' if typ in ('amount', 'int', 'number') else 'left')
        v = v[:int(w)]
        v = v.rjust(int(w), pad) if al == 'right' else v.ljust(int(w), pad)
    return v


def file_name(lay, fctx):
    def rep(m):
        k, pat = m.group(1), m.group(2)
        val = fctx.get(k, '')
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.strftime(pat or '%Y%m%d')
        return re.sub(r'[\\/:*?"<>|\s]+', '_', str(val))
    return re.sub(r'\{(\w+)(?::([^}]*))?\}', rep, lay.get('file_name') or 'FILE.txt')


def rows_of(lay, details, fctx):
    """→ [(record name, kind, [values])] in file order"""
    out = []
    pw = lay.get('file_type') in ('fixed', 'delimited', 'csv')
    for r in lay['records']:
        if r['kind'] == 'header':
            out.append((r['name'], 'header', [fmt_value(f, fctx, pw) for f in r['fields']]))
    dets = [r for r in lay['records'] if r['kind'] == 'detail']
    for ctx in details:
        c = dict(fctx, **ctx)
        for r in dets:
            if r.get('when') == 'variable' and not (_num(ctx.get('variable')) or 0) > 0:
                continue
            out.append((r['name'], 'detail', [fmt_value(f, c, pw) for f in r['fields']]))
    for r in lay['records']:
        if r['kind'] == 'trailer':
            out.append((r['name'], 'trailer', [fmt_value(f, fctx, pw) for f in r['fields']]))
    return out


def render(lay, details, fctx):
    """→ (file name, content bytes, mime, preview lines)"""
    rows = rows_of(lay, details, fctx)
    name = file_name(lay, fctx)
    ft = lay.get('file_type')
    if ft in ('delimited', 'fixed', 'csv'):
        le = '\n' if lay.get('line_end') == 'LF' else '\r\n'
        if ft == 'csv':
            b = io.StringIO()
            w = csv.writer(b, lineterminator=le)
            for _n, _k, vals in rows:
                w.writerow(vals)
            text = b.getvalue()
            if text.endswith(le):
                text = text[:-len(le)] + le   # csv files end with a line end
        else:
            sep = '' if ft == 'fixed' else lay.get('separator', ',')
            text = le.join(sep.join(vals) for _n, _k, vals in rows)
        data = (('﻿' if lay.get('bom') else '') + text).encode('utf-8')
        return name, data, ('text/csv' if ft == 'csv' else 'text/plain') + '; charset=utf-8', text.replace('\r\n', '\n').split('\n') if text else []
    if ft == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook(); ws = wb.active; ws.title = (lay.get('sheet') or 'Sheet1')[:31]
        for n, k, vals in rows:
            cells = []
            for v, f in zip(vals, _fields_of(lay, n, k)):
                typ = CAT.get(f['source'], ('', '', '', 'text'))[3]
                if not lay.get('text_cells') and typ in ('amount', 'int', 'number') and _num(v.replace(',', '')) is not None and v:
                    cells.append(float(_num(v.replace(',', ''))))
                else:
                    cells.append(v)
            ws.append(cells)
            if k != 'detail' and not lay.get('text_cells'):
                for c in ws[ws.max_row]:
                    c.font = Font(bold=True)
            if lay.get('text_cells'):
                for c in ws[ws.max_row]:
                    c.number_format = '@'
        b = io.BytesIO(); wb.save(b)
        return name, b.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', ['\t'.join(v) for _n, _k, v in rows]
    # pdf
    from .run_payments import _pdf
    det = next(r for r in lay['records'] if r['kind'] == 'detail')
    head = [tr(f['label']) for f in det['fields']]
    widths = [(f.get('format') or {}).get('width') or 60 for f in det['fields']]
    total_w = sum(widths)
    widths = [w * 786.0 / total_w for w in widths] if total_w > 786 or total_w < 500 else widths
    body = [v for _n, k, v in rows if k == 'detail']
    foot = next((v for _n, k, v in rows if k == 'trailer'), None)
    foot = [tr(x) if x == 'Total' else x for x in foot] if foot else None
    title = file_name({'file_name': lay.get('title') or lay['name']}, fctx)
    sub = f"{fctx.get('company', '')} · {fctx.get('run_name', '')} · {fctx.get('count', 0)} · {tr('Total')} {fctx.get('total', 0):,.2f} {fctx.get('currency', 'AED')}"
    return name, _pdf(title, sub, head, body, widths, foot=foot + [''] * (len(head) - len(foot)) if foot else None), 'application/pdf', \
        ['\t'.join(head)] + ['\t'.join(v) for v in body] + (['\t'.join(foot)] if foot else [])


def _fields_of(lay, name, kind):
    return next((r['fields'] for r in lay['records'] if r['name'] == name and r['kind'] == kind), [])


# ------------------------------------------------------------------ checks: structure of a made file / a bank's sample file
def split_text(lay, text):
    lines = [l for l in text.replace('\r\n', '\n').split('\n')]
    while lines and lines[-1] == '':
        lines.pop()
    if lay.get('file_type') == 'csv':
        return [r for r in csv.reader(lines)], lines
    if lay.get('file_type') == 'fixed':
        return [[l] for l in lines], lines
    return [l.split(lay.get('separator', ',')) for l in lines], lines


def expected(lay):
    """[(kind, name, field count, width or None, first value or None)] of the layout's records"""
    out = []
    for r in lay['records']:
        w = sum(int((f.get('format') or {}).get('width') or 0) for f in r['fields']) if lay.get('file_type') == 'fixed' else None
        first = r['fields'][0].get('value') if r['fields'] and r['fields'][0]['source'] == 'const' else None
        out.append((r['kind'], r['name'], len(r['fields']), w, first))
    return out


def classify(lay, recs, lines):
    """each line of a file → the layout record it matches (by its record type / position)"""
    exp = expected(lay)
    hdr = [e for e in exp if e[0] == 'header']
    trl = [e for e in exp if e[0] == 'trailer']
    det = [e for e in exp if e[0] == 'detail']
    out = []
    n = len(recs)
    for i, r in enumerate(recs):
        first = r[0] if r else ''
        typed = [e for e in exp if e[4] is not None and lay.get('file_type') != 'fixed' and e[4] == first]
        if lay.get('file_type') == 'fixed':
            typed = [e for e in exp if e[4] is not None and lines[i].startswith(e[4])]
        if typed:
            out.append(typed[0]); continue
        if i < len(hdr):
            out.append(hdr[i]); continue
        if i >= n - len(trl):
            out.append(trl[i - (n - len(trl))]); continue
        out.append(next((e for e in det if e[4] is None), det[0] if det else ('detail', '?', 0, None, None)))
    return out


def compare(lay, content, sample_name=''):
    """the bank's sample file against the layout: record count / kinds, field count per record, widths (fixed) – the differences"""
    diffs, info = [], {}
    if lay.get('file_type') == 'xlsx':
        from openpyxl import load_workbook
        try:
            ws = load_workbook(io.BytesIO(content), read_only=True).active
        except Exception:
            return {'ok': False, 'differences': [tr('The sample is not an Excel file.')], 'records': 0}
        rows = [[c for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c not in (None, '') for c in r)]
        hdr = next((r for r in lay['records'] if r['kind'] == 'header'), None)
        det = next(r for r in lay['records'] if r['kind'] == 'detail')
        if hdr and rows:
            want = [f.get('value') if f['source'] == 'const' else f['label'] for f in hdr['fields']]
            got = [str(c or '') for c in rows[0]][:max(len(want), len([c for c in rows[0] if c not in (None, '')]))]
            got = [g for g in got]
            while got and got[-1] == '':
                got.pop()
            if got != want:
                diffs.append(tr('Heading row: the sample has {0} columns ({1}), the layout {2} ({3}).', len(got), ', '.join(got), len(want), ', '.join(want)))
        body = rows[1:] if hdr else rows
        for i, r in enumerate(body[:200]):
            n = len([c for c in r]) if r else 0
            while n and r[n - 1] in (None, ''):
                n -= 1
            if n > len(det['fields']):
                diffs.append(tr('Row {0}: {1} columns, the layout has {2}.', i + 2 if hdr else i + 1, n, len(det['fields'])))
        info['records'] = len(rows)
        return {'ok': not diffs, 'differences': diffs[:60], 'records': len(rows), 'sheet': ws.title if hasattr(ws, 'title') else ''}
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')
    recs, lines = split_text(lay, text)
    if not recs:
        return {'ok': False, 'differences': [tr('The sample file is empty.')], 'records': 0}
    if lay.get('line_end', 'CRLF') == 'CRLF' and '\n' in text and '\r\n' not in text:
        diffs.append(tr('Line ends: the sample uses LF, the layout CRLF.'))
    if lay.get('line_end') == 'LF' and '\r\n' in text:
        diffs.append(tr('Line ends: the sample uses CRLF, the layout LF.'))
    if lay.get('file_type') == 'delimited':
        sep = lay.get('separator', ',')
        if sep not in text:
            for s in (',', '\t', ';', '|'):
                if s in text and s != sep:
                    diffs.append(tr('Separator: the sample uses "{0}", the layout "{1}".', 'TAB' if s == '\t' else s, 'TAB' if sep == '\t' else sep)); break
    kinds = classify(lay, recs, lines)
    counts = {}
    for i, (r, e) in enumerate(zip(recs, kinds)):
        counts[e[1]] = counts.get(e[1], 0) + 1
        if lay.get('file_type') == 'fixed':
            if e[3] and len(lines[i]) != e[3]:
                diffs.append(tr('Line {0} ({1}): {2} characters, the layout {3}.', i + 1, e[1], len(lines[i]), e[3]))
        elif len(r) != e[2]:
            diffs.append(tr('Line {0} ({1}): {2} fields, the layout has {3}.', i + 1, e[1], len(r), e[2]))
    for e in expected(lay):
        if e[0] in ('header', 'trailer') and counts.get(e[1], 0) != 1:
            diffs.append(tr('Record "{0}": the sample has {1}, the layout 1.', e[1], counts.get(e[1], 0)))
        if e[0] == 'detail' and not counts.get(e[1]) and not any(x.get('when') == 'variable' for x in lay['records'] if x['name'] == e[1]):
            diffs.append(tr('Record "{0}": none in the sample.', e[1]))
    return {'ok': not diffs, 'differences': diffs[:60], 'records': len(recs), 'by_record': counts}


def check_made(lay, rows):
    """generic checks of a file made from a (company) layout"""
    exp = {(r['kind'], r['name']): len(r['fields']) for r in lay['records']}
    det = [v for n, k, v in rows if k == 'detail']
    out = [{'check': tr('Every record has the fields of the layout'), 'ok': all(len(v) == exp[(k, n)] for n, k, v in rows)}]
    if lay.get('file_type') == 'fixed':
        w = {(r['kind'], r['name']): sum(int((f.get('format') or {}).get('width') or 0) for f in r['fields']) for r in lay['records']}
        out.append({'check': tr('Every line has the width of its record'), 'ok': all(len(''.join(v)) == w[(k, n)] for n, k, v in rows)})
    if lay.get('file_type') in ('delimited',):
        sep = lay.get('separator', ',')
        out.append({'check': tr('No value contains the separator'), 'ok': all(sep not in x for _n, _k, v in rows for x in v)})
    out.append({'check': tr('One header / trailer record each'), 'ok': all(sum(1 for n, k, _v in rows if k == kk and n == nn) == 1
                                                                          for kk, nn in exp if kk in ('header', 'trailer'))})
    return out
