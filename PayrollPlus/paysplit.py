# v1.63.0 (item 27): new module – one salary paid through two bank accounts or part cash
"""v1.63.0 (item 27) – salary split.

Per employee a dated split (PaySplit): the main account (the employee's payment mode – WPS for the UAE part) plus a second part paid by
a second bank account (bank transfer outside WPS), cash, cheque or a company own method, as a fixed amount or a % of the net pay. The
row with the latest effective date on or before the end of the salary month counts; method 'none' ends the split from its date.

portions(ps, main_method, net, held) is what Payroll run › Payments (run_payments.portions) and the WPS file (PayrollManagement
serializer SIFSerializer) use:
    second = value % × net (2 decimals)  or  min(amount, net)
    main   = net − second
    held salary (payctl, item 228) is taken off the main portion first, the rest of it off the second portion.
A second method equal to the main method is one part (nothing to split).

WPS file (CBUAE SIF) of a split salary – company setting `wps_split_edr` (TO BE CONFIRMED, see the 1.63.0 release notes):
    'portion' (default)  the EDR carries the WPS portion as its income (variable pay first, up to the portion; the rest fixed);
                         pay period, days and days on leave are the full salary month's figures; the SCR total adds the EDRs.
    'full'               the EDR carries the whole salary (net − held) – only if the bank / WPS agent asks for it: the file then
                         asks the bank to transfer the whole salary.
Sources read 03/11/2026: Mashreq WPS file format (EDR income fixed / variable = "Valid Amount in AED", SCR total = "the sum of all
fixed and variable salary components of all employees mentioned in the EDR records") https://netbanking.mashreqbank.com/B001/WPS_File_Format_eng.html;
MOHRE Ministerial Resolution 340 of 2026 (from 1 June 2026, replaces 598/2022: an establishment is compliant when it pays at least 85 %
of the wages due through WPS or an approved system; a worker counts as paid at 85 % when the shortfall is a lawful deduction) –
EY https://www.ey.com/en_gl/technical/tax-alerts/uae-introduces-enhanced-wage-protection-system-effective-1-june-2026, Morgan Lewis
https://morganlewis.com/pubs/2026/05/uae-introduces-new-wage-protection-system-resolution-effective-1-june-2026. None of them says how an
EDR shows a salary paid partly outside WPS – so the setting is marked "to be confirmed".
"""
import calendar
import io
import logging
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
Q2 = Decimal('0.01')
OPTION_KEY = 'v1630_pay_split'
DEFAULTS = {'wps_split_edr': 'portion'}
SECOND = ('bank_transfer', 'cash', 'cheque')
METHOD_NAMES = {'wps': 'WPS (salary transfer)', 'bank_transfer': 'Second bank account (bank transfer)', 'cash': 'Cash', 'cheque': 'Cheque',
                'none': 'No split (end the split)'}


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


def q2(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0)).quantize(Q2, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return ZERO


# ------------------------------------------------------------------ settings (CompanyOption, no new column)
def settings():
    o = M('ZeoApps', 'CompanyOption').objects.filter(key=OPTION_KEY).first()
    v = dict(DEFAULTS)
    v.update((o.value if o else None) or {})
    if v.get('wps_split_edr') not in ('portion', 'full'):
        v['wps_split_edr'] = 'portion'
    return v


def save_settings(data):
    v = settings()
    if 'wps_split_edr' in (data or {}):
        if data['wps_split_edr'] not in ('portion', 'full'):
            raise Refused(tr('Check the marked fields.'), problems={'wps_split_edr': tr('Invalid.')})
        v['wps_split_edr'] = data['wps_split_edr']
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=OPTION_KEY, defaults={'value': v})
    return settings()


# ------------------------------------------------------------------ IBAN check
def iban_problem(v):
    """'' when the IBAN is valid. UAE: AE + 21 characters (2 check digits, 3-digit bank code, 16-digit account – digits only),
    mod-97 check (ISO 13616: the 4 first characters moved to the end, letters as 10–35, the number mod 97 = 1). Another country's
    IBAN: 2 letters, 2 check digits, 11–30 letters / digits, mod-97."""
    s = re.sub(r'\s+', '', str(v or '')).upper()
    if not s:
        return tr('Enter the IBAN.')
    from EmployeeProfile.validators import iban_mod97_ok
    if s.startswith('AE'):
        if len(s) != 23:
            return tr('A UAE IBAN has 23 characters (AE + 21 digits); this one has {0}.', len(s))
        if not s[2:].isdigit():
            return tr('After AE a UAE IBAN has only digits.')
    elif not re.fullmatch(r'[A-Z]{2}\d{2}[A-Z0-9]{11,30}', s):
        return tr('Enter the IBAN as 2 letters (country), 2 check digits and the account (15 to 34 characters, no spaces).')
    if not iban_mod97_ok(s):
        return tr('This IBAN is not valid (the check digits do not match). Copy it again from the bank letter.')
    return ''


# ------------------------------------------------------------------ the split of a month
def month_end(y, m):
    return date(int(y), int(m), calendar.monthrange(int(y), int(m))[1])


def split_on(employee_id, day):
    """The split that counts on `day` (None: no split, or the split was ended)."""
    from .models import PaySplit
    s = PaySplit.objects.filter(employee_id=employee_id, status='active', effective_from__lte=day).order_by('-effective_from', '-id').first()
    return s if s is not None and s.method != 'none' else None


def splits_on(employee_ids, day):
    from .models import PaySplit
    out = {}
    for s in PaySplit.objects.filter(employee_id__in=list(employee_ids), status='active', effective_from__lte=day).order_by('employee_id', '-effective_from', '-id'):
        out.setdefault(s.employee_id, s)
    return {k: v for k, v in out.items() if v.method != 'none'}


def payslip_day(ps):
    run = ps.payroll_run
    return month_end(run.year, run.month)


def main_method(employee_id):
    """The employee's own method, as Payroll run › Payments reads it: a company method the employee is assigned to, else the
    payment mode of the primary active bank account, else WPS."""
    from . import run_payments as RP
    for m in RP.methods():
        if m.get('active', True) and int(employee_id) in {int(e) for e in m.get('employees') or []}:
            return m['code']
    BD = M('EmpManagement', 'EmployeeBankDetail')
    BX = M('EmployeeProfile', 'BankExtra')
    acc = list(BD.objects.filter(employee_id=employee_id, is_active=True).order_by('pk'))
    bx = {x.bank_detail_id: x for x in BX.objects.filter(employee_id=employee_id)}
    prim = next((b for b in acc if getattr(bx.get(b.pk), 'is_primary', False)), acc[0] if acc else None)
    x = bx.get(prim.pk) if prim else None
    return getattr(x, 'payment_mode', '') or 'wps'


def parts(net, held, split, main):
    """[{method, amount, held, gross, kind, split}] – the parts of one net pay (see the module text)."""
    net = max(q2(net), ZERO)
    held = min(max(q2(held), ZERO), net)
    if split is None or split.method == main:
        return [{'method': main, 'amount': net - held, 'held': held, 'gross': net, 'kind': 'main', 'split': None}]
    if split.basis == 'percent':
        sec = (net * q2(split.value) / 100).quantize(Q2, rounding=ROUND_HALF_UP)
    else:
        sec = min(q2(split.value), net)
    sec = min(max(sec, ZERO), net)
    main_amt = net - sec
    h_main = min(held, main_amt)
    h_sec = min(held - h_main, sec)
    out = [{'method': main, 'amount': main_amt - h_main, 'held': h_main, 'gross': main_amt, 'kind': 'main', 'split': split.pk}]
    out.append({'method': split.method, 'amount': sec - h_sec, 'held': h_sec, 'gross': sec, 'kind': 'second', 'split': split.pk,
                'iban': split.iban or split.account_number, 'bank': split.bank_name, 'routing': split.routing_code})
    return out


def wps_amount(ps, net, held):
    """The amount the WPS file pays for a payslip: net − held without a split; with a split (main method WPS) the WPS portion –
    or the whole salary when the company setting says 'full' (to be confirmed). → (amount, split or None)."""
    net, held = max(q2(net), ZERO), max(q2(held), ZERO)
    sp = split_on(ps.employee_id, payslip_day(ps))
    if sp is None:
        return max(net - held, ZERO), None
    main = main_method(ps.employee_id)
    if main != 'wps' or settings()['wps_split_edr'] == 'full':
        return max(net - held, ZERO), sp
    p = next(x for x in parts(net, held, sp, main) if x['kind'] == 'main')
    return p['amount'], sp


# ------------------------------------------------------------------ rows and changes
def _user_name(user):
    try:
        return (user.get_full_name() or user.username or '')[:150] if user is not None else ''
    except Exception:
        return str(getattr(user, 'username', '') or '')[:150]


def method_name(code):
    if code in METHOD_NAMES:
        return tr(METHOD_NAMES[code])
    from . import run_payments as RP
    return next((tr(m['name']) for m in RP.methods() if m['code'] == code), code)


def method_choices():
    from . import run_payments as RP
    out = [{'value': c, 'label': tr(METHOD_NAMES[c])} for c in SECOND]
    out += [{'value': m['code'], 'label': tr(m['name'])} for m in RP.methods() if m.get('active', True)]
    return out


def row(s, emps=None):
    from .payctl import emp_label
    e = (emps or {}).get(s.employee_id) or M('EmpManagement', 'emp_master').objects.filter(pk=s.employee_id).first()
    cur = split_on(s.employee_id, timezone.localdate())
    return {'id': s.pk, 'employee_id': s.employee_id, 'employee': emp_label(e), 'emp_code': getattr(e, 'emp_code', ''),
            'effective_from': s.effective_from.isoformat(), 'method': s.method, 'method_label': method_name(s.method), 'basis': s.basis,
            'basis_label': tr(dict(s.BASIS).get(s.basis, s.basis)), 'value': str(q2(s.value)),
            'text': split_text(s), 'bank_name': s.bank_name, 'iban': s.iban, 'account_number': s.account_number, 'routing_code': s.routing_code,
            'note': s.note, 'status': s.status, 'status_label': tr(dict(s.STATUS).get(s.status, s.status)),
            'current': bool(cur and cur.pk == s.pk), 'future': s.effective_from > timezone.localdate(), 'history': s.history or [],
            'created_at': s.created_at.isoformat() if s.created_at else None}


def split_text(s):
    if s.method == 'none':
        return tr('Whole salary to the main account')
    v = tr('{0} % of the net pay', '{0:f}'.format(q2(s.value).normalize())) if s.basis == 'percent' else tr('{0} a month', f'{q2(s.value):,.2f}')
    return tr('{0} by {1}, the rest to the main account', v, method_name(s.method))


def validate(data, employee_id):
    """→ the cleaned fields of a split, or Refused with the problems per field."""
    from .payctl import _parse_day
    p = {}
    eff = _parse_day(data.get('effective_from'))
    if eff is None:
        p['effective_from'] = tr('Enter the effective date.')
    method = str(data.get('method') or '').strip()
    allowed = {x['value'] for x in method_choices()} | {'none'}
    if method not in allowed:
        p['method'] = tr('Choose how the second part is paid.')
    basis = str(data.get('basis') or 'percent')
    if basis not in ('amount', 'percent'):
        p['basis'] = tr('Invalid.')
    val = ZERO
    if method != 'none':
        try:
            val = Decimal(str(data.get('value')).replace(',', '').strip())
            if val <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            p['value'] = tr('Enter an amount or percentage above 0.')
            val = None
        if val is not None and basis == 'percent' and val >= 100:
            p['value'] = tr('A percentage below 100 – the rest is paid to the main account.')
    iban = re.sub(r'\s+', '', str(data.get('iban') or '')).upper()
    acct = str(data.get('account_number') or '').strip()[:50]
    routing = re.sub(r'\s+', '', str(data.get('routing_code') or ''))
    if method == 'bank_transfer':
        if not iban and not acct:
            p['iban'] = tr('Enter the IBAN of the second account.')
        elif iban:
            why = iban_problem(iban)
            if why:
                p['iban'] = why
        if routing and not re.fullmatch(r'\d{9}', routing):
            p['routing_code'] = tr('Must be a 9-digit number')
    if not p and method != 'none':
        main = main_method(employee_id)
        if main == method:
            p['method'] = tr('This is already the employee’s main payment method – choose another method for the second part.')
    if p:
        raise Refused(tr('Check the marked fields.'), problems=p)
    e = M('EmpManagement', 'emp_master').objects.filter(pk=employee_id).first()
    if e is not None and e.emp_joined_date and eff < e.emp_joined_date:
        raise Refused(tr('The effective date is before the joining date.'), problems={'effective_from': tr('Before the joining date.')})
    return {'effective_from': eff, 'method': method, 'basis': basis, 'value': q2(val) if method != 'none' else ZERO,
            'bank_name': str(data.get('bank_name') or '').strip()[:120] if method == 'bank_transfer' else '',
            'iban': iban if method == 'bank_transfer' else '', 'account_number': acct if method == 'bank_transfer' else '',
            'routing_code': routing if method == 'bank_transfer' else '', 'note': str(data.get('note') or '').strip()[:255]}


def _closed_month(employee_id, day):
    """a payroll run already paid / approved for the month of `day` (the split would change a closed month's payments)"""
    P = M('PayrollManagement', 'Payslip')
    return P.objects.filter(employee_id=employee_id, payroll_run__year=day.year, payroll_run__month=day.month,
                            payroll_run__status='paid').exists()


def create(employee_id, data, user=None):
    from .models import PaySplit
    v = validate(data, employee_id)
    if _closed_month(employee_id, v['effective_from']):
        raise Refused(tr('The salary of that month is already paid – choose a later effective date.'),
                      problems={'effective_from': tr('Month already paid.')})
    with transaction.atomic():
        same = PaySplit.objects.filter(employee_id=employee_id, status='active', effective_from=v['effective_from']).first()
        if same is not None:   # the same date again replaces it (kept in the history)
            same.status = 'cancelled'
            same.history = list(same.history or []) + [{'action': 'replaced', 'by': _user_name(user), 'at': timezone.now().isoformat(timespec='seconds')}]
            same.save(update_fields=['status', 'history', 'updated_at'])
        s = PaySplit.objects.create(employee_id=employee_id, created_by_id=getattr(user, 'pk', None),
                                    history=[{'action': 'created', 'by': _user_name(user), 'by_id': getattr(user, 'pk', None),
                                              'at': timezone.now().isoformat(timespec='seconds'), 'note': v['note']}], **v)
    return s


def cancel(s, user=None, note=''):
    if s.status != 'active':
        raise Refused(tr('This split is already cancelled.'))
    if s.effective_from <= timezone.localdate() and _closed_month(s.employee_id, s.effective_from):
        raise Refused(tr('A paid month used this split – add a new entry from a later date instead (e.g. "No split").'))
    s.status = 'cancelled'
    s.history = list(s.history or []) + [{'action': 'cancelled', 'by': _user_name(user), 'by_id': getattr(user, 'pk', None),
                                          'at': timezone.now().isoformat(timespec='seconds'), 'note': str(note or '')[:255]}]
    s.save(update_fields=['status', 'history', 'updated_at'])
    return s


# ------------------------------------------------------------------ import (employee code, method, account / IBAN, amount or %, from date)
TEMPLATE_COLUMNS = ['employee_code', 'method', 'iban_or_account', 'bank_name', 'routing_code', 'amount', 'percent', 'from_date', 'note']


def template_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = 'Salary splits'
    ws.append(TEMPLATE_COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(['E001', 'bank_transfer', 'AE070331234567890123456', 'Emirates NBD', '', '', 30, '2027-01-01', '30 % to the second account'])
    ws.append(['E002', 'cash', '', '', '', 2000, '', '2027-01-01', 'AED 2,000 in cash'])
    ws2 = wb.create_sheet('Methods')
    ws2.append(['method', 'meaning'])
    for x in method_choices() + [{'value': 'none', 'label': tr(METHOD_NAMES['none'])}]:
        ws2.append([x['value'], x['label']])
    b = io.BytesIO(); wb.save(b)
    return b.getvalue()


def import_rows(f, name='', apply=False, user=None, allowed=None):
    from .payctl import _read_rows, emp_label
    E = M('EmpManagement', 'emp_master')
    try:
        rows = _read_rows(f, name)
    except Exception as ex:
        raise Refused(tr('The file could not be read – use the template (CSV or Excel).'), problems={'file': str(ex)[:200]})
    if not rows:
        raise Refused(tr('The file has no rows.'), problems={'file': tr('The file has no rows.')})
    missing = [c for c in ('employee_code', 'method', 'from_date') if c not in rows[0]]
    if missing:
        raise Refused(tr('Columns missing: {0}', ', '.join(missing)), problems={'file': tr('Columns missing: {0}', ', '.join(missing))})
    out, seen = [], set()
    names = {x['label'].lower(): x['value'] for x in method_choices()}
    for i, r in enumerate(rows, start=2):
        errs = []
        code = r.get('employee_code')
        code = str(int(code)) if isinstance(code, float) and code.is_integer() else str(code or '').strip()
        e = E.objects.filter(emp_code__iexact=code).first() if code else None
        if e is None:
            errs.append(tr('Employee {0} not found.', code or '–'))
        elif allowed is not None and e.pk not in allowed:
            errs.append(tr('This employee is not in your branches.'))
            e = None
        method = str(r.get('method') or '').strip()
        method = names.get(method.lower(), method.lower() if method.lower() in SECOND + ('none',) else method)
        amt, pct = str(r.get('amount') or '').strip(), str(r.get('percent') or '').strip()
        if amt and pct:
            errs.append(tr('Enter an amount or a percentage, not both.'))
        acct = str(r.get('iban_or_account') or '').strip()
        is_iban = bool(re.match(r'^[A-Za-z]{2}\d{2}', re.sub(r'\s+', '', acct)))
        data = {'effective_from': r.get('from_date'), 'method': method, 'basis': 'amount' if amt else 'percent', 'value': amt or pct,
                'iban': acct if is_iban else '', 'account_number': '' if is_iban else acct, 'bank_name': r.get('bank_name') or '',
                'routing_code': str(r.get('routing_code') or '').strip(), 'note': r.get('note') or tr('Imported')}
        clean = None
        if e is not None:
            try:
                clean = validate(data, e.pk)
            except Refused as ex:
                errs += [str(v) for v in ex.problems.values()] or [ex.message]
            if clean is not None:
                k = (e.pk, clean['effective_from'])
                if k in seen:
                    errs.append(tr('The same employee and date are in the file twice.'))
                seen.add(k)
                if _closed_month(e.pk, clean['effective_from']):
                    errs.append(tr('The salary of that month is already paid – choose a later effective date.'))
        out.append({'row': i, 'employee_code': code, 'employee': emp_label(e) if e else '', 'method': method,
                    'method_label': method_name(method) if method else '', 'value': data['value'], 'basis': data['basis'],
                    'iban_or_account': acct, 'from_date': clean['effective_from'].isoformat() if clean else str(r.get('from_date') or ''),
                    'errors': errs, 'ok': not errs, '_c': (e.pk if e else None, clean)})
    applied = 0
    if apply:
        with transaction.atomic():
            for x in out:
                if x['ok']:
                    eid, clean = x['_c']
                    create(eid, {**clean, 'effective_from': clean['effective_from'].isoformat()}, user)
                    applied += 1
    for x in out:
        x.pop('_c', None)
    return {'rows': out, 'ok': sum(1 for x in out if x['ok']), 'errors': sum(1 for x in out if not x['ok']), 'applied': applied, 'dry_run': not apply}
