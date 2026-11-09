# v1.67.11 (loans): new module – loans, advances and deduction limits (items 51, 60, 110–117)
"""v1.67.11 (loans) – the rules of loans and salary advances, the monthly deduction limit and the recovery at exit.

Law (UAE, confirmed in two or more sources – see the release notes, section 135):
  * Federal Decree-Law 33/2021 Art. 25(2): whatever the reasons, the deductions of a month may not exceed 50 % of the wage
    ("Wage" = basic wage + cash allowances, Art. 1). Art. 25(1)(a): a loan to the worker is repaid from the wage within that limit,
    with the worker's written consent and WITHOUT INTEREST – so a UAE company cannot give a loan type a profit rate.
  * Art. 51(7) + Cabinet Resolution 1/2022 Art. 29: amounts the worker owes (loans, overpayments) may be deducted from the
    end-of-service benefits; no percentage is set – the settlement recovery limit is a company setting (default: the earnings of
    the settlement, never a negative net) and what does not fit is left as a receivable.

Company settings (ZeoApps.CompanyOption 'v16711_lending', JSON – no new column in a core app):
  deduction   legal_cap (UAE companies: on, 50 %), loan_share_pct / advance_share_pct (own share of the wage, empty = none)
  loans       deduct_after_payout (only loans marked paid out are deducted)
  advances    max_pct (of the monthly wage), once_a_month, max_instalments, no_leavers
  settlement  order (loan, advance, recovery), cap_pct (of the settlement earnings, empty = 100 %)
  accounts    loan_receivable, advance_receivable, bank, write_off, profit_income
Per loan type (PayrollPlus.LoanTypeRule): service, probation, × basic, instalment ÷ wage, leavers, one open loan, profit, guarantor.
Per loan / advance (LoanTerms / AdvanceTerms): asked and approved amount, principal and profit, guarantor, months skipped, write-off,
payout; advance instalments (AdvanceInstalment); loan / advance instalments cut by the limit (DeductionCut, carried to next month).
"""
import calendar
import logging
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
Q2 = Decimal('0.01')
ZERO = Decimal('0.00')
KEY = 'v16711_lending'
LOAN_OPEN = ('Pending', 'Approved', 'Disbursed', 'Paused', 'In Progress')
LOAN_RUNNING = ('Approved', 'Disbursed', 'In Progress', 'Paused')
LEAVING = ('on_notice', 'left', 'terminated', 'absconded', 'retired')
ADV_CLOSED = ('Rejected', 'Cancelled')


class LendError(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


def d(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0')


def q2(v):
    return d(v).quantize(Q2, rounding=ROUND_HALF_UP)


def money(v):
    return f'{q2(v):,.2f}'


def month_key(dt):
    return f'{dt.year}-{dt.month:02d}'


def add_months(dt, n):
    y, m = divmod(dt.month - 1 + n, 12)
    y += dt.year
    return dt.replace(year=y, month=m + 1, day=min(dt.day, calendar.monthrange(y, m + 1)[1]))


def uae():
    try:
        from CountryPack.services import is_ae
        return bool(is_ae())
    except Exception:
        return True


def user_name(user):
    if not user:
        return ''
    return ((getattr(user, 'get_full_name', lambda: '')() or '').strip() or getattr(user, 'username', '') or '')[:150]


# ------------------------------------------------------------------ company settings
def _defaults():
    return {
        'deduction': {'legal_cap': uae(), 'legal_cap_pct': '50', 'loan_share_pct': None, 'advance_share_pct': None},
        'loans': {'deduct_after_payout': False},
        'advances': {'max_pct': None, 'once_a_month': True, 'max_instalments': 3, 'no_leavers': True},
        'settlement': {'order': ['loan', 'advance', 'recovery'], 'cap_pct': None},
        'accounts': {'loan_receivable': '1410', 'advance_receivable': '1420', 'bank': '1010', 'write_off': '6190', 'profit_income': '4910'},
    }


def _opt():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        return (o.value if o is not None else None) or {}
    except Exception:
        return {}


def settings():
    out = _defaults()
    cur = _opt()
    for sec, vals in out.items():
        if isinstance(cur.get(sec), dict):
            vals.update({k: v for k, v in cur[sec].items() if k in vals})
    return out


def _pct(v, field, lo=Decimal('0.01'), hi=Decimal('100')):
    if v in (None, ''):
        return None
    try:
        p = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        p = None
    if p is None or not (lo <= p <= hi):
        raise LendError(tr('Enter a percentage between {0} and {1}, or leave it empty.', f'{lo.normalize():f}', f'{hi.normalize():f}'), problems={field: 'Invalid.'})
    return format(p.normalize(), 'f')


def _flag(v):
    return str(v).lower() in ('1', 'true', 'yes', 'on')


def save_settings(data):
    cur = settings()
    dd, ad, sd, ld, acc = (data.get(k) or {} for k in ('deduction', 'advances', 'settlement', 'loans', 'accounts'))
    if 'legal_cap' in dd:
        cur['deduction']['legal_cap'] = _flag(dd['legal_cap'])
    if 'legal_cap_pct' in dd:
        p = _pct(dd['legal_cap_pct'], 'legal_cap_pct', Decimal('1'))
        if uae() and p is not None and Decimal(p) > 50:
            raise LendError(tr('The UAE Labour Law allows at most 50 % of the wage a month (Decree-Law 33/2021 Art. 25(2)).'),
                            problems={'legal_cap_pct': 'Above the law.'})
        cur['deduction']['legal_cap_pct'] = p or '50'
    for f in ('loan_share_pct', 'advance_share_pct'):
        if f in dd:
            cur['deduction'][f] = _pct(dd[f], f)
    if 'deduct_after_payout' in ld:
        cur['loans']['deduct_after_payout'] = _flag(ld['deduct_after_payout'])
    if 'max_pct' in ad:
        cur['advances']['max_pct'] = _pct(ad['max_pct'], 'max_pct')
    for f in ('once_a_month', 'no_leavers'):
        if f in ad:
            cur['advances'][f] = _flag(ad[f])
    if 'max_instalments' in ad:
        try:
            n = int(ad['max_instalments'])
            if not 1 <= n <= 24:
                raise ValueError
        except (TypeError, ValueError):
            raise LendError(tr('Enter a number of instalments between 1 and 24.'), problems={'max_instalments': 'Invalid.'})
        cur['advances']['max_instalments'] = n
    if 'order' in sd:
        o = [x for x in (sd.get('order') or []) if x in ('loan', 'advance', 'recovery')]
        cur['settlement']['order'] = o + [x for x in ('loan', 'advance', 'recovery') if x not in o]
    if 'cap_pct' in sd:
        cur['settlement']['cap_pct'] = _pct(sd['cap_pct'], 'cap_pct')
    for f in ('loan_receivable', 'advance_receivable', 'bank', 'write_off', 'profit_income'):
        if f in acc:
            v = str(acc.get(f) or '').strip()[:40]
            if not v:
                raise LendError(tr('Enter the account.'), problems={f: 'Required.'})
            cur['accounts'][f] = v
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    return settings()


# ------------------------------------------------------------------ the employee
def basic_wage(emp):
    """(monthly basic, monthly wage = basic + fixed cash allowances) – the final settlement's own definition."""
    from .services import _wage
    b, w, _p = _wage(emp)
    return q2(b), q2(w)


def service_months(emp, on):
    j = getattr(emp, 'emp_joined_date', None)
    if not j or on < j:
        return 0
    m = (on.year - j.year) * 12 + on.month - j.month
    if on.day < j.day:
        m -= 1
    return max(m, 0)


def in_probation(emp, on):
    try:
        info = M('EmployeeProfile', 'EmploymentInfo').objects.filter(employee_id=emp.pk).first()
    except LookupError:
        info = None
    if info is not None and info.probation_status in ('on_probation', 'extended'):
        return info.probation_end_date is None or info.probation_end_date >= on
    if info is not None and info.probation_status in ('confirmed', 'failed'):
        return info.probation_status == 'failed'
    c = getattr(emp, 'emp_date_of_confirmation', None)
    return bool(c and c > on)


def leaving(emp):
    """Why the employee counts as a leaver (resigned, on notice, leaving, inactive) – '' when not."""
    if emp.is_active is False or getattr(emp, 'emp_status', True) is False:
        return tr('the employee is inactive')
    try:
        info = M('EmployeeProfile', 'EmploymentInfo').objects.filter(employee_id=emp.pk).first()
        if info is not None and info.status in LEAVING:
            return tr('the employee is on notice or leaving')
    except LookupError:
        pass
    try:
        R = M('EmpManagement', 'EmployeeResignation')
        if R.objects.filter(employee_id=emp.pk).exclude(status__in=('Rejected', 'rejected', 'Cancelled', 'cancelled', 'Withdrawn', 'withdrawn')).exists():
            return tr('a resignation or termination is recorded')
    except LookupError:
        pass
    return ''


# ------------------------------------------------------------------ loan type rules (items 110–112)
RULE_FIELDS = ('min_service_months', 'not_in_probation', 'max_times_basic', 'max_instalment_pct', 'no_leavers', 'open_loans', 'interest',
               'interest_rate', 'guarantor')


def rule_of(loan_type_id):
    R = M('PayrollPlus', 'LoanTypeRule')
    r = R.objects.filter(loan_type_id=loan_type_id).first() if loan_type_id else None
    return r or R(loan_type_id=loan_type_id or 0)


def rule_dict(loan_type_id):
    r = rule_of(loan_type_id)
    return {'min_service_months': r.min_service_months, 'not_in_probation': r.not_in_probation,
            'max_times_basic': None if r.max_times_basic is None else format(r.max_times_basic.normalize(), 'f'),
            'max_instalment_pct': None if r.max_instalment_pct is None else format(r.max_instalment_pct.normalize(), 'f'),
            'no_leavers': r.no_leavers, 'open_loans': r.open_loans, 'interest': r.interest,
            'interest_rate': format(d(r.interest_rate).normalize(), 'f'), 'guarantor': r.guarantor}


def save_rule(loan_type_id, data):
    """data holds any of RULE_FIELDS (from the loan type form); wrong values are refused with the field."""
    r = rule_of(loan_type_id)
    if 'min_service_months' in data:
        try:
            v = int(data['min_service_months'] or 0)
            if not 0 <= v <= 600:
                raise ValueError
            r.min_service_months = v
        except (TypeError, ValueError):
            raise LendError(tr('Enter the months of service (0 or more).'), problems={'min_service_months': 'Invalid.'})
    for f in ('not_in_probation', 'no_leavers'):
        if f in data:
            setattr(r, f, _flag(data[f]))
    if 'max_times_basic' in data:
        v = data['max_times_basic']
        if v in (None, ''):
            r.max_times_basic = None
        else:
            x = d(v)
            if x <= 0 or x > 1000:
                raise LendError(tr('Enter how many times the monthly basic (more than 0), or leave it empty.'), problems={'max_times_basic': 'Invalid.'})
            r.max_times_basic = x
    if 'max_instalment_pct' in data:
        p = _pct(data['max_instalment_pct'], 'max_instalment_pct')
        r.max_instalment_pct = None if p is None else Decimal(p)
    if 'open_loans' in data and data['open_loans'] not in (None, ''):
        if data['open_loans'] not in ('type', 'any'):
            raise LendError(tr('Choose the open loan rule.'), problems={'open_loans': 'Invalid.'})
        r.open_loans = data['open_loans']
    if 'guarantor' in data and data['guarantor'] not in (None, ''):
        if data['guarantor'] not in ('none', 'optional', 'required'):
            raise LendError(tr('Choose whether a guarantor is asked.'), problems={'guarantor': 'Invalid.'})
        r.guarantor = data['guarantor']
    if 'interest' in data and data['interest'] not in (None, ''):
        if data['interest'] not in ('none', 'flat'):
            raise LendError(tr('Choose the profit of the loan type.'), problems={'interest': 'Invalid.'})
        r.interest = data['interest']
    if 'interest_rate' in data:
        x = d(data['interest_rate'])
        if x < 0 or x > 100:
            raise LendError(tr('Enter a profit rate between 0 and 100 % a year.'), problems={'interest_rate': 'Invalid.'})
        r.interest_rate = x
    if r.interest == 'none':
        r.interest_rate = Decimal('0')
    if r.interest == 'flat' and d(r.interest_rate) > 0 and uae():
        raise LendError(tr('A UAE company cannot charge profit or interest on a loan repaid from the wage (Decree-Law 33/2021 '
                           'Art. 25(1)(a): "without any interest"). Choose None.'), problems={'interest': 'Not allowed in the UAE.'})
    r.save()
    return rule_dict(loan_type_id)


def profit(rule, principal, months):
    """Flat profit: principal × rate % a year × months ÷ 12 (0 for 'none')."""
    if rule.interest != 'flat' or not d(rule.interest_rate) or not months:
        return ZERO
    return q2(d(principal) * d(rule.interest_rate) / 100 * int(months) / 12)


def check_loan(employee, loan_type, principal, months, emi=None, exclude_pk=None, on=None, guarantor_id=None):
    """Items 110 / 111: what stops this loan – {field: text}; empty when it may be asked."""
    on = on or timezone.localdate()
    out = {}

    class _Out(dict):   # several reasons for one field are all given
        def __setitem__(self, k, v):
            super().__setitem__(k, (self[k] + ' ' + v) if k in self else v)
    out = _Out()
    principal, months = q2(principal), int(months or 0)
    if principal <= 0:
        out['amount_requested'] = tr('Enter the loan amount.')
    if months <= 0:
        out['repayment_period'] = tr('Enter the repayment period in months.')
    if loan_type.max_amount and principal > q2(loan_type.max_amount):
        out['amount_requested'] = tr('The highest amount of the loan type {0} is {1}.', loan_type.loan_type, money(loan_type.max_amount))
    if loan_type.repayment_period and months > int(loan_type.repayment_period):
        out['repayment_period'] = tr('The loan type {0} is repaid in at most {1} months.', loan_type.loan_type, loan_type.repayment_period)
    r = rule_of(loan_type.pk)
    if r.no_leavers:
        why = leaving(employee)
        if why:
            out['employee'] = tr('No loan can be given: {0}.', why)
    if r.min_service_months and service_months(employee, on) < r.min_service_months:
        out['employee'] = tr('The loan type {0} needs {1} months of service; the employee has {2}.', loan_type.loan_type, r.min_service_months,
                             service_months(employee, on))
    if r.not_in_probation and in_probation(employee, on):
        out['employee'] = tr('The loan type {0} is not given during probation.', loan_type.loan_type)
    basic, wage = basic_wage(employee)
    if r.max_times_basic is not None and principal > 0:
        top = q2(basic * r.max_times_basic)
        if principal > top:
            out['amount_requested'] = tr('At most {0} × the monthly basic ({1}) = {2}.', format(r.max_times_basic.normalize(), 'f'), money(basic), money(top))
    if r.max_instalment_pct is not None and principal > 0 and months > 0:
        total = principal + profit(r, principal, months)
        inst = q2(emi) if emi not in (None, '') and d(emi) > 0 else q2(total / months)
        top = q2(wage * r.max_instalment_pct / 100)
        if inst > top:
            out['repayment_period'] = tr('The instalment {0} is more than {1} % of the monthly wage ({2}) – at most {3}. Give more months or a lower amount.',
                                         money(inst), format(r.max_instalment_pct.normalize(), 'f'), money(wage), money(top))
    LA = M('PayrollManagement', 'LoanApplication')
    others = LA.objects.filter(employee=employee, status__in=LOAN_OPEN).exclude(pk=exclude_pk or 0)
    if r.open_loans == 'any' and others.exists():
        o = others.first()
        out['loan_type'] = tr('The employee already has an open loan ({0}); this loan type allows no other open loan.', o.document_number or o.pk)
    if r.guarantor == 'required' and not guarantor_id:
        out['guarantor'] = tr('The loan type {0} needs a guarantor.', loan_type.loan_type)
    if guarantor_id and int(guarantor_id) == employee.pk:
        out['guarantor'] = tr('The employee cannot be his or her own guarantor.')
    return dict(out)


def terms_of(loan_id):
    T = M('PayrollPlus', 'LoanTerms')
    return T.objects.filter(loan_id=loan_id).first() or T(loan_id=loan_id)


def loan_created(loan, principal, months, guarantor_id=None, consent=False):
    """After a loan is made through the API: the amount asked, principal and profit (the loan's amount is principal + profit)."""
    t = terms_of(loan.pk)
    r = rule_of(loan.loan_type_id)
    pr = profit(r, principal, months)
    t.requested_amount, t.requested_months = q2(principal), int(months)
    t.principal, t.interest = q2(principal), pr
    t.guarantor_id = int(guarantor_id) if guarantor_id else None
    t.consent = bool(consent)
    t.save()
    return t


def total_with_profit(loan_type, principal, months):
    return q2(principal) + profit(rule_of(loan_type.pk), principal, months)


# ------------------------------------------------------------------ an approver gives a lower amount (item 114)
def _notify_employee(emp, title, message):
    try:
        from SelfService.services import notify
        u = getattr(emp, 'users', None)
        if u is not None:
            notify(user=u, employee=emp, title=title, message=message)
        else:
            notify(employee=emp, title=title, message=message)
    except Exception:
        log.debug('employee notice', exc_info=True)


@transaction.atomic
def approve_lower_loan(loan, amount, months=None, user=None, note=''):
    amount = q2(amount)
    t = terms_of(loan.pk)
    asked = q2(t.principal if t.principal is not None else loan.amount_requested)
    if amount <= 0:
        raise LendError(tr('Enter the amount approved.'), problems={'approved_amount': 'Required.'})
    if amount >= asked:
        if amount == asked and not months:
            return loan
        raise LendError(tr('The approved amount must be lower than the amount asked ({0}).', money(asked)), problems={'approved_amount': 'Too high.'})
    if loan.status not in ('Pending',):
        raise LendError(tr('Only a loan in approval can be approved for a lower amount.'))
    months = int(months or loan.repayment_period)
    lt = loan.loan_type
    if lt.repayment_period and months > int(lt.repayment_period):
        raise LendError(tr('The loan type {0} is repaid in at most {1} months.', lt.loan_type, lt.repayment_period), problems={'approved_months': 'Too many.'})
    if t.requested_amount is None:
        t.requested_amount, t.requested_months = asked, loan.repayment_period
    r = rule_of(loan.loan_type_id)
    t.principal, t.interest = amount, profit(r, amount, months)
    t.approved_amount, t.approved_by_id, t.approved_note = amount, getattr(user, 'pk', None), (note or '')[:255]
    t.save()
    loan.amount_requested = t.principal + t.interest
    loan.repayment_period = months
    loan.emi_amount = q2(loan.amount_requested / months)
    loan.remaining_balance = loan.amount_requested
    loan.save(update_fields=['amount_requested', 'repayment_period', 'emi_amount', 'remaining_balance'])
    from .loans import _event
    _event(loan, 'lower', user, amount=amount, new_emi=loan.emi_amount, new_period=months, balance_after=loan.amount_requested,
           note=(tr('Asked {0}, approved {1}', money(asked), money(amount)) + (f' – {note}' if note else ''))[:255])
    ref = loan.document_number or loan.pk
    _notify_employee(loan.employee, tr('Loan approved for a lower amount'),
                     tr('Your loan {0}: {1} approved instead of the {2} asked, {3} months, instalment {4}.', ref, money(amount), money(asked),
                        months, money(loan.emi_amount)))
    return loan


def adv_terms(advance_id):
    T = M('PayrollPlus', 'AdvanceTerms')
    return T.objects.filter(advance_id=advance_id).first() or T(advance_id=advance_id)


@transaction.atomic
def approve_lower_advance(adv, amount, user=None, note=''):
    amount = q2(amount)
    t = adv_terms(adv.pk)
    asked = q2(adv.requested_amount)
    if amount <= 0:
        raise LendError(tr('Enter the amount approved.'), problems={'approved_amount': 'Required.'})
    if amount >= asked:
        if amount == asked:
            return adv
        raise LendError(tr('The approved amount must be lower than the amount asked ({0}).', money(asked)), problems={'approved_amount': 'Too high.'})
    if adv.status != 'Pending':
        raise LendError(tr('Only an advance in approval can be approved for a lower amount.'))
    if t.requested_amount is None:
        t.requested_amount = asked
    t.approved_amount, t.approved_by_id, t.approved_note = amount, getattr(user, 'pk', None), (note or '')[:255]
    t.save()
    adv.requested_amount = amount
    adv.remarks = ((adv.remarks + '\n') if adv.remarks else '') + tr('Asked {0}, approved {1}', money(asked), money(amount))
    adv.save(update_fields=['requested_amount', 'remarks'])
    _notify_employee(adv.employee, tr('Advance approved for a lower amount'),
                     tr('Your salary advance {0}: {1} approved instead of the {2} asked.', adv.document_number or adv.pk, money(amount), money(asked)))
    return adv


# ------------------------------------------------------------------ advances (item 115)
def check_advance(employee, amount, instalments=1, exclude_pk=None, on=None):
    on = on or timezone.localdate()
    st = settings()['advances']
    out = {}
    amount = q2(amount)
    if amount <= 0:
        out['requested_amount'] = tr('Enter the amount of the advance.')
    n = int(instalments or 1)
    if n < 1 or n > int(st['max_instalments'] or 1):
        out['instalments'] = tr('An advance is repaid in 1 to {0} monthly instalments.', st['max_instalments'])
    if st['no_leavers']:
        why = leaving(employee)
        if why:
            out['employee'] = tr('No salary advance can be given: {0}.', why)
    if st['max_pct'] not in (None, ''):
        _b, wage = basic_wage(employee)
        top = q2(wage * Decimal(str(st['max_pct'])) / 100)
        if amount > top:
            out['requested_amount'] = tr('An advance is at most {0} % of the monthly wage ({1}) = {2}.', st['max_pct'], money(wage), money(top))
    if st['once_a_month']:
        AS = M('PayrollManagement', 'AdvanceSalaryRequest')
        start = on.replace(day=1)
        end = add_months(start, 1)
        o = (AS.objects.filter(employee=employee, created_at__date__gte=start, created_at__date__lt=end).exclude(status__in=ADV_CLOSED)
             .exclude(pk=exclude_pk or 0).first())
        if o is not None:
            out['requested_amount'] = tr('One salary advance a month: {0} was already asked on {1:%d/%m/%Y}.', o.document_number or o.pk,
                                         timezone.localtime(o.created_at).date())
    return out


def advance_taken(adv_id):
    return q2(M('PayrollPlus', 'AdvanceInstalment').objects.filter(advance_id=adv_id, status='taken').aggregate(t=Sum('amount'))['t'] or 0)


def advance_left(adv):
    """What is still to be deducted of an approved advance (amount − instalments taken − recovered at exit)."""
    t = adv_terms(adv.pk)
    return max(q2(adv.requested_amount) - advance_taken(adv.pk) - q2(t.exit_recovered), ZERO)


def advance_instalment(adv):
    """The planned instalment of an advance this month: amount ÷ instalments (the last one takes the rest)."""
    t = adv_terms(adv.pk)
    n = max(int(t.instalments or 1), 1)
    left = advance_left(adv)
    taken_n = M('PayrollPlus', 'AdvanceInstalment').objects.filter(advance_id=adv.pk, status='taken').count()
    if n <= 1 or taken_n >= n - 1:
        return left
    return min(q2(d(adv.requested_amount) / n), left)


# ------------------------------------------------------------------ payroll (items 51, 60, 113, 115)
def _alive(ids):
    return set(M('PayrollManagement', 'Payslip').objects.filter(pk__in=[i for i in ids if i]).values_list('pk', flat=True))


def pending_cuts(employee_id, kind, ref_id, payslip_id):
    """Cuts of earlier payslips of this loan / advance that wait for the next payslip (the payslip that took one no longer exists:
    it waits again)."""
    C = M('PayrollPlus', 'DeductionCut')
    rows = list(C.objects.filter(employee_id=employee_id, kind=kind, ref_id=ref_id).exclude(payslip_id=payslip_id))
    alive = _alive({r.payslip_id for r in rows} | {r.taken_by_payslip_id for r in rows})
    out = []
    for r in rows:
        if r.payslip_id not in alive:
            r.delete()
            continue
        if r.taken_by_payslip_id and r.taken_by_payslip_id not in alive:
            r.taken_by_payslip_id = None
            r.save(update_fields=['taken_by_payslip_id'])
        if not r.taken_by_payslip_id or r.taken_by_payslip_id == payslip_id:
            out.append(r)
    return out


def loan_skips(loan_id):
    t = M('PayrollPlus', 'LoanTerms').objects.filter(loan_id=loan_id).first()
    return list((t.skipped or []) if t else [])


def loan_deductible(loan, run, end):
    """'' when payroll deducts the loan this run, else why not (payslip note): a month skipped (item 113), not paid out yet / paid
    out after the period (item 60)."""
    mk = f'{int(run.year)}-{int(run.month):02d}'
    if mk in loan_skips(loan.pk):
        return tr('Loan {0}: {1} skipped – no instalment this month.', loan.document_number or loan.pk, f'{int(run.month):02d}/{run.year}')
    end_d = end.date() if hasattr(end, 'date') and callable(end.date) else end
    if loan.disbursement_date and end_d and loan.disbursement_date > end_d:
        return tr('Loan {0}: paid out on {1:%d/%m/%Y}, after this payroll period – first instalment next month.', loan.document_number or loan.pk,
                  loan.disbursement_date)
    if settings()['loans']['deduct_after_payout'] and not loan.disbursement_date and loan.status != 'Disbursed':
        return tr('Loan {0}: not paid out yet – no instalment.', loan.document_number or loan.pk)
    return ''


def loan_with_carry(loan, payslip, amount, balance):
    """The instalment plus what the deduction limit cut from it in earlier months (never more than the balance)."""
    cuts = pending_cuts(loan.employee_id, 'loan', loan.pk, payslip.pk)
    extra = sum((q2(c.amount) for c in cuts), ZERO)
    if extra <= 0:
        return amount
    for c in cuts:
        c.taken_by_payslip_id = payslip.pk
        c.save(update_fields=['taken_by_payslip_id'])
    return min(q2(amount) + extra, q2(balance))


def advance_due(adv, payslip):
    cuts = pending_cuts(adv.employee_id, 'advance', adv.pk, payslip.pk)
    base = advance_instalment(adv)
    extra = sum((q2(c.amount) for c in cuts), ZERO)
    for c in cuts:
        c.taken_by_payslip_id = payslip.pk
        c.save(update_fields=['taken_by_payslip_id'])
    return min(base + extra, advance_left(adv))


def take_advance(adv, payslip, run, amount):
    """Record the instalment; True when the advance is fully deducted."""
    AI = M('PayrollPlus', 'AdvanceInstalment')
    AI.objects.create(advance_id=adv.pk, payslip_id=payslip.pk, month=date(int(run.year), int(run.month), 1), amount=q2(amount))
    t = adv_terms(adv.pk)
    if not t.pk:
        t.instalments = 1
        t.save()
    return advance_left(adv) <= 0


def note(payslip_id, kind, item):
    from PayrollManagement import payroll_guards as G
    G._add_note(payslip_id, kind, item)


def cut(payslip, kind, amount, reason):
    """Item 51: the deduction limit takes `amount` off the loan / advance line of the payslip – the instalments of this payslip are
    reduced (newest first), the part cut is carried to the next payslip (DeductionCut). [{ref, name, cut}]"""
    amount = q2(amount)
    out = []
    run = payslip.payroll_run
    month = date(int(run.year), int(run.month), 1)
    C = M('PayrollPlus', 'DeductionCut')
    if kind == 'loan':
        LR = M('PayrollManagement', 'LoanRepayment')
        for rep in LR.objects.filter(payslip=payslip).select_related('loan').order_by('-pk'):
            if amount <= 0:
                break
            take = min(amount, q2(rep.amount_paid))
            loan = rep.loan
            rep.amount_paid = q2(rep.amount_paid) - take
            rep.remaining_balance = q2(rep.remaining_balance) + take
            if rep.amount_paid <= 0:
                rep.delete()
            else:
                rep.save(update_fields=['amount_paid', 'remaining_balance'])
            from .loans import balance
            bal = balance(loan)
            loan.remaining_balance = bal
            fields = ['remaining_balance']
            if loan.status == 'Closed' and bal > 0:
                loan.status = 'Approved'
                fields.append('status')
            loan.save(update_fields=fields)
            C.objects.create(payslip_id=payslip.pk, employee_id=payslip.employee_id, kind='loan', ref_id=loan.pk, month=month, amount=take, reason=reason[:255])
            out.append({'kind': 'loan', 'ref': loan.document_number or str(loan.pk), 'id': loan.pk, 'cut': str(take)})
            amount -= take
    elif kind == 'advance':
        AI = M('PayrollPlus', 'AdvanceInstalment')
        AS = M('PayrollManagement', 'AdvanceSalaryRequest')
        for ins in AI.objects.filter(payslip_id=payslip.pk, status='taken').order_by('-pk'):
            if amount <= 0:
                break
            take = min(amount, q2(ins.amount))
            ins.amount = q2(ins.amount) - take
            if ins.amount <= 0:
                ins.delete()
            else:
                ins.save(update_fields=['amount'])
            a = AS.objects.filter(pk=ins.advance_id).first()
            if a is not None and a.status == 'Deducted' and advance_left(a) > 0:
                a.status = 'Approved'   # not fully deducted any more: the rest waits for the next payroll
                a.save(update_fields=['status'])
                _drop_consumed_advance(payslip.pk, a.pk)
            C.objects.create(payslip_id=payslip.pk, employee_id=payslip.employee_id, kind='advance', ref_id=ins.advance_id, month=month, amount=take,
                             reason=reason[:255])
            out.append({'kind': 'advance', 'ref': (a.document_number if a is not None else '') or str(ins.advance_id), 'id': ins.advance_id, 'cut': str(take)})
            amount -= take
    return out


def _drop_consumed_advance(payslip_id, adv_id):
    """The advance is no longer used up by this payslip (v1.62.2 item 44 note) – a delete must not set it back again."""
    try:
        CO = M('ZeoApps', 'CompanyOption')
        row = CO.objects.filter(key=f'v1622_payslip_consumed:{payslip_id}').first()
        if row and isinstance(row.value, dict) and adv_id in (row.value.get('advances') or []):
            v = dict(row.value)
            v['advances'] = [x for x in v['advances'] if x != adv_id]
            row.value = v
            row.save(update_fields=['value'])
    except Exception:
        log.debug('consumed note', exc_info=True)


def give_back_payslip(payslip_id, delete=True):
    """A payslip deleted (run deleted / recalculated) or rejected: its advance instalments and the cuts it made come back; the cuts it
    took wait again."""
    AI, C = M('PayrollPlus', 'AdvanceInstalment'), M('PayrollPlus', 'DeductionCut')
    if delete:
        AI.objects.filter(payslip_id=payslip_id).delete()
    else:
        AI.objects.filter(payslip_id=payslip_id, status='taken').update(status='given_back')
    C.objects.filter(payslip_id=payslip_id).delete()
    C.objects.filter(taken_by_payslip_id=payslip_id).update(taken_by_payslip_id=None)


def retake_payslip(payslip_id):
    M('PayrollPlus', 'AdvanceInstalment').objects.filter(payslip_id=payslip_id, status='given_back').update(status='taken')


def limits():
    """The monthly deduction limit of the company: {'legal_pct': Decimal | None, 'loan_pct', 'advance_pct'}."""
    s = settings()['deduction']
    lp = Decimal(str(s['legal_cap_pct'] or '50')) if s['legal_cap'] else None
    return {'legal_pct': lp, 'loan_pct': d(s['loan_share_pct']) if s['loan_share_pct'] not in (None, '') else None,
            'advance_pct': d(s['advance_share_pct']) if s['advance_share_pct'] not in (None, '') else None}


# ------------------------------------------------------------------ running loans: prepay, skip, write-off, payout (items 112, 113, 117)
def _running(loan):
    if loan.status not in LOAN_RUNNING:
        raise LendError(tr('Only a running loan can be changed.'))


def _planned_months(loan):
    from .loans import schedule
    return len([r for r in schedule(loan) if r['state'] in ('planned', 'paused')])


@transaction.atomic
def prepay(loan, amount, keep='months', user=None, note='', when=None):
    """Item 113: part of the balance paid now (cash / bank). keep='months': the months left stay, the instalment goes down;
    keep='instalment': the instalment stays, the loan ends earlier."""
    from .loans import _event, _months_paid, balance
    _running(loan)
    amount = q2(amount)
    bal = balance(loan)
    if amount <= 0:
        raise LendError(tr('Enter the amount paid.'), problems={'amount': 'Required.'})
    if amount >= bal:
        raise LendError(tr('The amount is the whole balance ({0}) – use Settle early.', money(bal)), problems={'amount': 'Too high.'})
    left_months = max(_planned_months(loan), 1)
    old_emi, old_period = loan.emi_amount, loan.repayment_period
    M('PayrollManagement', 'LoanRepayment').objects.create(loan=loan, payslip=None, amount_paid=amount, remaining_balance=bal - amount)
    new_bal = bal - amount
    if keep == 'instalment':
        emi = q2(loan.emi_amount)
        months = int((new_bal / emi).to_integral_value(rounding=ROUND_CEILING)) if emi > 0 else left_months
    else:
        months = left_months
        emi = q2(new_bal / months)
    loan.emi_amount = emi
    loan.repayment_period = _months_paid(loan) + months
    loan.remaining_balance = new_bal
    loan.save(update_fields=['emi_amount', 'repayment_period', 'remaining_balance'])
    ev = _event(loan, 'prepay', user, amount=amount, old_emi=old_emi, new_emi=emi, old_period=old_period, new_period=loan.repayment_period,
                balance_after=new_bal, note=((note or '') + (' – ' if note else '') + (tr('instalment kept') if keep == 'instalment' else tr('months kept')))[:255],
                date=when or timezone.localdate())
    _auto_journal('loan', loan, 'prepay', ev.pk, user)
    return loan


@transaction.atomic
def skip(loan, month, user=None, note=''):
    """Item 113: no instalment in `month` ('YYYY-MM'); the plan moves one month."""
    from .loans import _event
    _running(loan)
    try:
        y, m = (int(x) for x in str(month)[:7].split('-'))
        first = date(y, m, 1)
    except (TypeError, ValueError):
        raise LendError(tr('Choose the month to skip.'), problems={'month': 'Required.'})
    if first < timezone.localdate().replace(day=1):
        raise LendError(tr('A month that has passed cannot be skipped.'), problems={'month': 'In the past.'})
    LR = M('PayrollManagement', 'LoanRepayment')
    if LR.objects.filter(loan=loan, payslip__payroll_run__year=y, payslip__payroll_run__month=m).exists():
        raise LendError(tr('The instalment of {0} is already deducted.', f'{m:02d}/{y}'), problems={'month': 'Already deducted.'})
    t = terms_of(loan.pk)
    k = f'{y}-{m:02d}'
    if k in (t.skipped or []):
        raise LendError(tr('{0} is already skipped.', f'{m:02d}/{y}'), problems={'month': 'Already skipped.'})
    t.skipped = sorted(set((t.skipped or []) + [k]))
    t.save()
    _event(loan, 'skip', user, note=(tr('{0} skipped', f'{m:02d}/{y}') + (f' – {note}' if note else ''))[:255], date=first)
    return loan


@transaction.atomic
def unskip(loan, month, user=None):
    from .loans import _event
    t = terms_of(loan.pk)
    k = str(month)[:7]
    if k not in (t.skipped or []):
        raise LendError(tr('This month is not skipped.'))
    y, m = (int(x) for x in k.split('-'))
    if date(y, m, 1) < timezone.localdate().replace(day=1):
        raise LendError(tr('A month that has passed cannot be changed.'))
    t.skipped = [x for x in t.skipped if x != k]
    t.save()
    _event(loan, 'skip', user, note=tr('{0} deducted again (skip undone)', f'{m:02d}/{y}')[:255])
    return loan


@transaction.atomic
def writeoff_request(loan, amount, reason, user=None):
    """Item 112: write off part or all of the balance – with a reason; another person approves it."""
    from .loans import balance
    _running(loan)
    reason = (reason or '').strip()
    if not reason:
        raise LendError(tr('Say why the loan is written off.'), problems={'reason': 'Required.'})
    bal = balance(loan)
    amount = q2(amount) if amount not in (None, '') else bal
    if amount <= 0 or amount > bal:
        raise LendError(tr('Write off more than 0 and at most the balance {0}.', money(bal)), problems={'amount': 'Invalid.'})
    t = terms_of(loan.pk)
    if (t.writeoff or {}).get('status') == 'pending':
        raise LendError(tr('A write-off of this loan is already waiting for approval.'))
    t.writeoff = {'status': 'pending', 'amount': str(amount), 'reason': reason[:255], 'by_id': getattr(user, 'pk', None), 'by': user_name(user),
                  'at': timezone.now().isoformat(), 'repayments': list((t.writeoff or {}).get('repayments') or [])}
    t.save()
    try:
        from SelfService.services import users_with, notify
        emp = loan.employee
        for u in users_with(emp.emp_branch_id_id, {'change_loanapplication'}, exclude=(getattr(user, 'pk', None),)):
            notify(user=u, title=tr('Loan write-off to approve'),
                   message=tr('Loan {0}: write-off of {1} asked by {2} – {3}.', loan.document_number or loan.pk, money(amount), user_name(user), reason))
    except Exception:
        log.debug('write-off notice', exc_info=True)
    return t


@transaction.atomic
def writeoff_decide(loan, approve, user=None, note=''):
    from .loans import _event, balance
    t = terms_of(loan.pk)
    w = dict(t.writeoff or {})
    if w.get('status') != 'pending':
        raise LendError(tr('No write-off of this loan is waiting for approval.'), status=409)
    if w.get('by_id') and getattr(user, 'pk', None) == w.get('by_id'):
        raise LendError(tr('The person who asked for the write-off cannot approve it (segregation of duties).'), status=403)
    w.update({'decided_by_id': getattr(user, 'pk', None), 'decided_by': user_name(user), 'decided_at': timezone.now().isoformat(), 'note': (note or '')[:255]})
    if not approve:
        if not (note or '').strip():
            raise LendError(tr('Say why the write-off is rejected.'), problems={'note': 'Required.'})
        w['status'] = 'rejected'
        t.writeoff = w
        t.save()
        return t
    bal = balance(loan)
    amount = min(q2(w.get('amount')), bal)
    if amount <= 0:
        raise LendError(tr('Nothing is left to write off.'))
    rep = M('PayrollManagement', 'LoanRepayment').objects.create(loan=loan, payslip=None, amount_paid=amount, remaining_balance=bal - amount)
    w.update({'status': 'approved', 'amount': str(amount), 'repayment_id': rep.pk, 'repayments': list(w.get('repayments') or []) + [rep.pk]})
    t.writeoff = w
    t.written_off = q2(t.written_off) + amount
    t.save()
    left = bal - amount
    loan.remaining_balance = left
    fields = ['remaining_balance']
    if left <= 0:
        loan.status = 'Closed'
        fields.append('status')
    loan.save(update_fields=fields)
    ev = _event(loan, 'writeoff', user, amount=amount, balance_after=left, note=(w.get('reason') or '')[:255])
    _auto_journal('loan', loan, 'writeoff', ev.pk, user)
    return t


@transaction.atomic
def payout_loan(loan, when=None, ref='', user=None):
    """Item 117 (and 60): the loan is paid out to the employee – status Disbursed, payout date; the payout journal is built."""
    from .loans import _event
    if loan.status not in ('Approved', 'Disbursed', 'In Progress'):
        raise LendError(tr('Only an approved loan can be paid out.'))
    t = terms_of(loan.pk)
    if (t.payout or {}).get('date'):
        raise LendError(tr('This loan is already paid out ({0}).', (t.payout or {}).get('date')), status=409)
    when = when or timezone.localdate()
    t.payout = {'date': when.isoformat(), 'ref': (ref or '')[:60], 'by': user_name(user), 'at': timezone.now().isoformat()}
    if t.principal is None:
        t.principal = q2(loan.amount_requested)
    t.save()
    loan.disbursement_date = when
    fields = ['disbursement_date']
    if loan.status == 'Approved':
        loan.status = 'Disbursed'
        fields.append('status')
    loan.save(update_fields=fields)
    ev = _event(loan, 'payout', user, amount=q2(t.principal), balance_after=q2(loan.amount_requested), note=(ref or '')[:255], date=when)
    j = _auto_journal('loan', loan, 'payout', ev.pk, user)
    if j:
        t.payout = {**t.payout, 'journal': j.number}
        t.save(update_fields=['payout'])
    return loan


@transaction.atomic
def payout_advance(adv, when=None, ref='', user=None):
    if adv.status not in ('Approved', 'Deducted'):
        raise LendError(tr('Only an approved advance can be paid out.'))
    t = adv_terms(adv.pk)
    if (t.payout or {}).get('date'):
        raise LendError(tr('This advance is already paid out ({0}).', (t.payout or {}).get('date')), status=409)
    when = when or timezone.localdate()
    t.payout = {'date': when.isoformat(), 'ref': (ref or '')[:60], 'by': user_name(user), 'at': timezone.now().isoformat()}
    t.save()
    j = _auto_journal('advance', adv, 'payout', 0, user)
    if j:
        t.payout = {**t.payout, 'journal': j.number}
        t.save(update_fields=['payout'])
    return adv


# ------------------------------------------------------------------ journals (item 117)
def _auto_journal(kind, obj, what, ev_id, user):
    """Build the journal now (Accounting › Journals) – never stops the change itself."""
    try:
        from django.db import transaction as _tx
        with _tx.atomic():
            return build_journal(kind, obj, what, ev_id, user)
    except Exception as e:   # noqa: BLE001
        log.info('loan journal not built: %s', getattr(e, 'message', e))
        return None


def build_journal(kind, obj, what, ev_id=0, user=None):
    """Balanced journals of a loan / advance outside payroll:
       loan payout     Dr loans receivable (principal + profit) / Cr bank (principal), Cr profit income (profit)
       loan prepay     Dr bank / Cr loans receivable
       loan write-off  Dr loan write-off expense / Cr loans receivable
       advance payout  Dr salary advances receivable / Cr bank
    The payroll journal credits the Loan / Advance deduction lines to the receivable accounts (a component mapping wins) and the
    final settlement journal its loan / advance recovery lines (a settlement mapping wins)."""
    from ZeoAccounting.services import Builder, Refused
    acc = settings()['accounts']
    e = obj.employee
    dept, branch = e.emp_dept_id_id, e.emp_branch_id_id
    ref = obj.document_number or str(obj.pk)
    LE = M('PayrollPlus', 'LoanEvent')
    if kind == 'loan':
        ev = LE.objects.filter(pk=ev_id, loan_id=obj.pk).first() if ev_id else None
        jdate = (ev.date if ev else None) or timezone.localdate()
        labels = {'payout': tr('Loan {0} paid out', ref), 'prepay': tr('Loan {0} – prepayment', ref), 'writeoff': tr('Loan {0} – written off', ref)}
        b = Builder('loan', f'{obj.pk}-{what}-{ev_id}', f"{labels[what]} – {' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)}", jdate, ref)
        b.dims.emps[e.pk] = e
        if what == 'payout':
            t = terms_of(obj.pk)
            pr = q2(t.principal if t.principal is not None else obj.amount_requested)
            it = q2(t.interest)
            b.add(acc['loan_receivable'], tr('Loans to employees'), pr + it, 'debit', e.pk, dept, branch, group='rec')
            b.add(acc['bank'], tr('Bank – loan paid out'), pr, 'credit', e.pk, dept, branch, group='bank')
            if it:
                b.add(acc['profit_income'], tr('Profit on employee loans'), it, 'credit', e.pk, dept, branch, group='profit')
        elif what == 'prepay':
            b.add(acc['bank'], tr('Bank – loan prepayment'), q2(ev.amount if ev else 0), 'debit', e.pk, dept, branch, group='bank')
            b.add(acc['loan_receivable'], tr('Loans to employees'), q2(ev.amount if ev else 0), 'credit', e.pk, dept, branch, group='rec')
        elif what == 'writeoff':
            b.add(acc['write_off'], tr('Loan written off'), q2(ev.amount if ev else 0), 'debit', e.pk, dept, branch, cost=True, group='wo')
            b.add(acc['loan_receivable'], tr('Loans to employees'), q2(ev.amount if ev else 0), 'credit', e.pk, dept, branch, group='rec')
        else:
            raise Refused(tr('Unknown journal.'))
    else:
        t = adv_terms(obj.pk)
        jdate = date.fromisoformat((t.payout or {}).get('date')) if (t.payout or {}).get('date') else timezone.localdate()
        b = Builder('advance', f'{obj.pk}-payout', f"{tr('Salary advance {0} paid out', ref)} – {' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)}",
                    jdate, ref)
        b.dims.emps[e.pk] = e
        amt = q2(t.approved_amount if t.approved_amount is not None else obj.requested_amount)
        b.add(acc['advance_receivable'], tr('Salary advances to employees'), amt, 'debit', e.pk, dept, branch, group='rec')
        b.add(acc['bank'], tr('Bank – salary advance paid out'), amt, 'credit', e.pk, dept, branch, group='bank')
    return b.save(user)


def component_default_account(category):
    """The account a Loan / Advance deduction line of the payroll journal is credited to when the component has no mapping."""
    acc = settings()['accounts']
    return {'loan': acc['loan_receivable'], 'advance_salary': acc['advance_receivable']}.get(category)


def settlement_default_account(key):
    acc = settings()['accounts']
    k = str(key or '')
    if k.startswith('loan_'):
        return acc['loan_receivable']
    if k.startswith('advance_'):
        return acc['advance_receivable']
    return None


# ------------------------------------------------------------------ final settlement (item 116)
def settlement_cap(lines):
    """Recover loans, advances and recovery schedules in the company's order up to the limit: the settlement earnings × cap %
    (empty = 100 %) less the other deductions; what does not fit stays a receivable (an information line). Changes `lines`."""
    st = settings()['settlement']
    order = st['order'] or ['loan', 'advance', 'recovery']
    kind_of = lambda k: 'loan' if k.startswith('loan_') else 'advance' if k.startswith('advance_') else 'recovery' if k.startswith('recovery_') else None  # noqa: E731
    earn = sum((d(x.get('amount')) for x in lines if x.get('kind') == 'earning'), Decimal('0'))
    rec = [x for x in lines if x.get('kind') == 'deduction' and kind_of(str(x.get('key') or ''))]
    other = sum((d(x.get('amount')) for x in lines if x.get('kind') == 'deduction' and x not in rec), Decimal('0'))
    pct = Decimal(str(st['cap_pct'])) if st['cap_pct'] not in (None, '') else Decimal('100')
    room = max(q2(earn * pct / 100) - other, ZERO)
    rec.sort(key=lambda x: order.index(kind_of(str(x['key']))))
    extra = []
    for x in rec:
        a = q2(x.get('amount'))
        take = min(a, room)
        room -= take
        if take < a:
            rest = a - take
            x['amount'] = str(take)
            x['receivable'] = str(rest)
            x['basis'] = (x.get('basis') or '') + ' · ' + tr('recovered {0} of {1} (settlement recovery limit {2} %)', money(take), money(a), f'{pct.normalize():f}')
            extra.append({'key': f"receivable_{x['key']}", 'label': tr('Still owed by the employee – {0}', x.get('label') or x['key']), 'kind': 'info',
                          'amount': str(rest), 'basis': tr('not recovered in the settlement: {0} (receivable)', money(rest)), 'section': x.get('section') or 'loan'})
    lines.extend(extra)
    return extra


def settlement_closed(st, user=None):
    """After a paid settlement (close_recovered): advances recovered in part keep the rest as a receivable; loans left with a balance
    get the history line 'Left as a receivable at exit'."""
    from .loans import _event, balance
    LA = M('PayrollManagement', 'LoanApplication')
    for ln in st.lines or []:
        k = str(ln.get('key') or '')
        if ln.get('kind') != 'deduction' or '_' not in k or not k.split('_', 1)[1].isdigit():
            continue
        pk = int(k.split('_', 1)[1])
        rest = q2(ln.get('receivable'))
        if k.startswith('advance_'):
            t = adv_terms(pk)
            t.exit_recovered, t.exit_receivable = q2(ln.get('amount')), rest
            t.save()
        elif k.startswith('loan_') and rest > 0:
            loan = LA.objects.filter(pk=pk).first()
            if loan is not None:
                _event(loan, 'receivable', user, amount=rest, balance_after=balance(loan),
                       note=tr('Final settlement {0}: {1} not recovered – still owed by the employee', st.number or st.pk, money(rest))[:255],
                       date=st.paid_on or timezone.localdate())


# ------------------------------------------------------------------ what the screens show
def loan_terms_public(loan):
    t = M('PayrollPlus', 'LoanTerms').objects.filter(loan_id=loan.pk).first()
    g = None
    if t and t.guarantor_id:
        e = M('EmpManagement', 'emp_master').objects.filter(pk=t.guarantor_id).first()
        g = {'id': t.guarantor_id, 'name': ' '.join(x for x in (getattr(e, 'emp_first_name', ''), getattr(e, 'emp_last_name', '')) if x),
             'code': getattr(e, 'emp_code', '')} if e else {'id': t.guarantor_id, 'name': '', 'code': ''}
    return {'requested_amount': str(q2(t.requested_amount)) if t and t.requested_amount is not None else None,
            'requested_months': t.requested_months if t else None,
            'approved_amount': str(q2(t.approved_amount)) if t and t.approved_amount is not None else None,
            'principal': str(q2(t.principal)) if t and t.principal is not None else str(q2(loan.amount_requested)),
            'profit': str(q2(t.interest)) if t else '0.00', 'guarantor': g, 'consent': bool(t and t.consent),
            'skipped': list((t.skipped or []) if t else []), 'written_off': str(q2(t.written_off)) if t else '0.00',
            'writeoff': (t.writeoff or {}) if t else {}, 'payout': (t.payout or {}) if t else {}}
