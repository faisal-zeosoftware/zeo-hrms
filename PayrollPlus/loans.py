"""v1.49.0 – loan schedule, top-up, early settlement, new instalment, pause / resume and settlement at exit.

Payroll keeps deducting the instalment (emi_amount) of approved loans each month and records a LoanRepayment per payslip;
this module shows the schedule (paid + planned months) and changes a running loan the way payroll understands it:
amount_requested + top-ups − paid = balance; repayment_period = months paid + months left.
"""
import calendar
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Sum

from .models import LoanEvent

ACTIVE = ('Approved', 'Disbursed', 'In Progress', 'Paused')
Q2 = Decimal('0.01')


class LoanError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def M(name):
    return apps.get_model('PayrollManagement', name)


def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def paid(loan):
    return Decimal(str(M('LoanRepayment').objects.filter(loan=loan).aggregate(s=Sum('amount_paid'))['s'] or 0))


def balance(loan):
    return max(Decimal('0'), Decimal(str(loan.amount_requested or 0)) - paid(loan)).quantize(Q2)


def schedule(loan, today=None):
    """Paid months (from the repayments) and the planned months until the balance is nil."""
    today = today or date.today()
    reps = list(M('LoanRepayment').objects.filter(loan=loan).order_by('repayment_date', 'id').select_related('payslip__payroll_run'))
    rows, run = [], Decimal(str(loan.amount_requested or 0))
    for r in reps:
        run -= Decimal(str(r.amount_paid))
        pr = getattr(r.payslip, 'payroll_run', None) if r.payslip_id else None
        rows.append({'n': len(rows) + 1, 'month': (date(pr.year, pr.month, 1) if pr else (r.repayment_date or today).replace(day=1)),
                     'amount': str(Decimal(str(r.amount_paid)).quantize(Q2)), 'balance': str(max(run, Decimal(0)).quantize(Q2)),
                     'state': 'paid' if r.payslip_id else 'paid outside payroll'})
    bal = balance(loan)
    emi = Decimal(str(loan.emi_amount or 0))
    start = (rows[-1]['month'] if rows else (loan.disbursement_date or (loan.approved_on.date() if loan.approved_on else today)).replace(day=1))
    nxt = add_months(start, 1) if rows else start
    if nxt < today.replace(day=1):
        nxt = today.replace(day=1)
    if loan.status == 'Paused' and loan.resume_date and loan.resume_date.replace(day=1) > nxt:
        nxt = loan.resume_date.replace(day=1)
    if loan.status in ACTIVE and emi > 0:
        guard = 0
        while bal > 0 and guard < 240:
            amt = min(emi, bal)
            bal -= amt
            rows.append({'n': len(rows) + 1, 'month': nxt, 'amount': str(amt.quantize(Q2)), 'balance': str(bal.quantize(Q2)),
                         'state': 'paused' if loan.status == 'Paused' and not loan.resume_date else 'planned'})
            nxt = add_months(nxt, 1)
            guard += 1
    return rows


def summary(loan):
    rows = schedule(loan)
    planned = [r for r in rows if r['state'] in ('planned', 'paused')]
    return {'id': loan.pk, 'number': loan.document_number or str(loan.pk), 'employee_id': loan.employee_id,
            'employee': ' '.join(x for x in (loan.employee.emp_first_name, loan.employee.emp_last_name) if x), 'code': loan.employee.emp_code,
            'type': str(loan.loan_type), 'status': loan.status, 'amount': str(Decimal(str(loan.amount_requested or 0)).quantize(Q2)),
            'paid': str(paid(loan).quantize(Q2)), 'balance': str(balance(loan)), 'emi': str(Decimal(str(loan.emi_amount or 0)).quantize(Q2)),
            'period': loan.repayment_period, 'months_paid': len(rows) - len(planned), 'months_left': len(planned),
            'last_month': planned[-1]['month'] if planned else None, 'pause_start': loan.pause_start_date, 'resume_date': loan.resume_date,
            'schedule': rows,
            'events': [{'kind': e.get_kind_display(), 'date': e.date, 'amount': str(e.amount), 'new_emi': str(e.new_emi) if e.new_emi is not None else '',
                        'balance_after': str(e.balance_after) if e.balance_after is not None else '', 'note': e.note, 'by': e.user_name}
                       for e in LoanEvent.objects.filter(loan_id=loan.pk)]}


def _event(loan, kind, user, **kw):
    name = (getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', '')) if user else ''
    return LoanEvent.objects.create(loan_id=loan.pk, kind=kind, date=kw.pop('date', None) or date.today(), user_id=getattr(user, 'pk', None),
                                    user_name=name[:150], **kw)


def _months_paid(loan):
    return M('LoanRepayment').objects.filter(loan=loan).count()


@transaction.atomic
def topup(loan, amount, months=None, user=None, note=''):
    """Lend more on a running loan: the new balance is spread over `months` (default: the months left)."""
    if loan.status not in ACTIVE:
        raise LoanError('Only a running loan can be topped up.')
    amount = Decimal(str(amount or 0)).quantize(Q2)
    if amount <= 0:
        raise LoanError('Give the top-up amount.')
    lt = loan.loan_type
    if lt and lt.max_amount and Decimal(str(loan.amount_requested)) + amount - paid(loan) > Decimal(str(lt.max_amount)):
        raise LoanError(f'The balance would be above the limit of this loan type ({lt.max_amount}).')
    left = int(months or 0) or max(1, len([r for r in schedule(loan) if r['state'] != 'paid' and not r['state'].startswith('paid')]))
    old_emi, old_period = loan.emi_amount, loan.repayment_period
    loan.amount_requested = (Decimal(str(loan.amount_requested)) + amount).quantize(Q2)
    bal = balance(loan)
    loan.emi_amount = (bal / left).quantize(Q2, rounding=ROUND_HALF_UP)
    loan.repayment_period = _months_paid(loan) + left
    loan.remaining_balance = bal
    loan.save(update_fields=['amount_requested', 'emi_amount', 'repayment_period', 'remaining_balance'])
    _event(loan, 'topup', user, amount=amount, old_emi=old_emi, new_emi=loan.emi_amount, old_period=old_period, new_period=loan.repayment_period,
           balance_after=bal, note=note[:255])
    return loan


@transaction.atomic
def reschedule(loan, emi=None, months=None, user=None, note=''):
    if loan.status not in ACTIVE:
        raise LoanError('Only a running loan can be rescheduled.')
    bal = balance(loan)
    if bal <= 0:
        raise LoanError('Nothing is left to pay.')
    old_emi, old_period = loan.emi_amount, loan.repayment_period
    if months:
        months = int(months)
        if months <= 0:
            raise LoanError('Give the months left.')
        new = (bal / months).quantize(Q2, rounding=ROUND_HALF_UP)
    elif emi:
        new = Decimal(str(emi)).quantize(Q2)
        if new <= 0:
            raise LoanError('Give the new instalment.')
        months = int((bal / new).to_integral_value(rounding='ROUND_CEILING'))
    else:
        raise LoanError('Give the new instalment or the months left.')
    loan.emi_amount = new
    loan.repayment_period = _months_paid(loan) + months
    loan.save(update_fields=['emi_amount', 'repayment_period'])
    _event(loan, 'reschedule', user, old_emi=old_emi, new_emi=new, old_period=old_period, new_period=loan.repayment_period, balance_after=bal, note=note[:255])
    return loan


@transaction.atomic
def settle(loan, user=None, note='', at_exit=False, when=None):
    """Pay the whole balance now (cash / bank, or the final settlement): one repayment outside payroll, the loan is closed."""
    if loan.status not in ACTIVE:
        raise LoanError('This loan is not running.')
    bal = balance(loan)
    if bal <= 0:
        raise LoanError('Nothing is left to pay.')
    M('LoanRepayment').objects.create(loan=loan, payslip=None, amount_paid=bal, remaining_balance=Decimal('0'))
    loan.remaining_balance = Decimal('0')
    loan.status = 'Closed'
    loan.save(update_fields=['remaining_balance', 'status'])
    _event(loan, 'exit' if at_exit else 'settle', user, amount=bal, balance_after=Decimal('0'), note=note[:255], date=when or date.today())
    return loan


@transaction.atomic
def pause(loan, start=None, resume=None, user=None, note=''):
    if loan.status not in ('Approved', 'Disbursed', 'In Progress'):
        raise LoanError('Only a running loan can be paused.')
    loan.status, loan.pause_start_date, loan.resume_date, loan.pause_reason = 'Paused', start or date.today(), resume, note or loan.pause_reason
    loan.save(update_fields=['status', 'pause_start_date', 'resume_date', 'pause_reason'])
    _event(loan, 'pause', user, note=(note or '')[:255], date=start or date.today())
    return loan


@transaction.atomic
def resume(loan, user=None, note=''):
    if loan.status != 'Paused':
        raise LoanError('This loan is not paused.')
    loan.status, loan.resume_date = 'Approved', date.today()
    loan.save(update_fields=['status', 'resume_date'])
    _event(loan, 'resume', user, note=(note or '')[:255])
    return loan


def resume_due(today=None):
    """Daily: paused loans whose resume date has come start again."""
    today = today or date.today()
    n = 0
    for loan in M('LoanApplication').objects.filter(status='Paused', resume_date__isnull=False, resume_date__lte=today):
        loan.status = 'Approved'
        loan.save(update_fields=['status'])
        _event(loan, 'resume', None, note='Resume date reached')
        n += 1
    return n
