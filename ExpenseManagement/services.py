"""
Expense rules: amounts (mileage, per diem, AED), policy checks, report submit / approval / send back,
advances, reimbursement (direct or payroll), accounting entries and analytics.

Every function raises ExpenseError with a readable message when something is not allowed.
"""
import logging
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (ZERO, AdvanceApplication, Expense, ExpenseAdvance, ExpenseApproval, ExpenseCategory, ExpensePolicy,
                     ExpenseReport, ExpenseSettings, Trip)

log = logging.getLogger(__name__)
M = apps.get_model
TWO = Decimal('0.01')
DEFAULT_ROLES = ['reporting_manager', 'branch_hr']
ROLE_LABELS = {'reporting_manager': 'Reporting manager', 'manager_of_manager': "Manager's manager", 'branch_hr': 'Branch HR / finance',
               'department_head': 'Department head', 'company_hr': 'Company HR', 'admin': 'Company admin'}
# currencies pegged to the US dollar – used when no exchange rate is given
PEGGED_AED = {'AED': Decimal('1'), 'USD': Decimal('3.6725'), 'SAR': Decimal('0.979333'), 'QAR': Decimal('1.008929'),
              'BHD': Decimal('9.767287'), 'OMR': Decimal('9.551365'), 'JOD': Decimal('5.179831')}


class ExpenseError(Exception):
    def __init__(self, message, problems=None, status=400):
        super().__init__(message)
        self.message = message
        self.problems = problems or []
        self.status = status


def money(v):
    return Decimal(str(v or 0)).quantize(TWO, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ people
def employee(pk):
    return M('EmpManagement', 'emp_master').objects.filter(pk=pk).first() if pk else None


def employee_of_user(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    return M('EmpManagement', 'emp_master').objects.filter(users=user).first()


def own_user_ids(emp):
    ids = set()
    if emp is None:
        return ids
    if getattr(emp, 'users_id', None):
        ids.add(emp.users_id)
    U = M('UserManagement', 'CustomUser')
    ids |= set(U.objects.filter(username=emp.emp_code).values_list('pk', flat=True))
    return ids


def person(emp):
    if emp is None:
        return ''
    n = ' '.join(x for x in [emp.emp_first_name, emp.emp_middle_name, emp.emp_last_name] if x and str(x).strip())
    return f'{n} ({emp.emp_code})' if n else emp.emp_code


def user_name(uid):
    U = M('UserManagement', 'CustomUser')
    u = U.objects.filter(pk=uid).first() if uid else None
    if u is None:
        return ''
    e = M('EmpManagement', 'emp_master').objects.filter(users=u).only('emp_first_name', 'emp_last_name', 'emp_code').first()
    if e:
        return ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x) or u.username
    full = ' '.join(x for x in [getattr(u, 'first_name', ''), getattr(u, 'last_name', '')] if x)
    return full or u.username


# ------------------------------------------------------------------ policy
def policy_for(emp):
    """The most specific active policy whose branch / employee category / grade (designation) lists match; else the default."""
    pols = list(ExpensePolicy.objects.filter(active=True).prefetch_related('limits'))
    if emp is not None:
        def ok(ids, v):
            return not ids or (v is not None and int(v) in [int(x) for x in ids if str(x).lstrip('-').isdigit()])
        best, score = None, -1
        for p in pols:
            if not (ok(p.branch_ids, emp.emp_branch_id_id) and ok(p.category_ids, emp.emp_ctgry_id_id) and ok(p.grade_ids, emp.emp_desgntn_id_id)):
                continue
            s = sum(1 for x in (p.branch_ids, p.category_ids, p.grade_ids) if x)
            if s == 0 and not p.is_default:
                s = 0.5      # applies to everyone, preferred over the default
            if s > score:
                best, score = p, s
        if best is not None:
            return best
    return next((p for p in pols if p.is_default), None) or (pols[0] if pols else None)


def rate_to_aed(currency, given=None):
    cur = (currency or 'AED').upper()
    if cur == 'AED':
        return Decimal('1')
    # a rate of exactly 1 for another currency is the form default, not a real rate
    if given not in (None, '') and Decimal(str(given)) > 0 and Decimal(str(given)) != 1:
        return Decimal(str(given))
    if cur in PEGGED_AED:
        return PEGGED_AED[cur]
    raise ExpenseError(f'Give the exchange rate from {cur} to AED.')


def compute_amounts(exp, policy):
    """Mileage = km × rate, per diem = days × rate, then AED. Works on an unsaved Expense too."""
    kind = exp.category.kind if exp.category_id else 'general'
    if kind == 'mileage':
        if not exp.mileage_km or Decimal(str(exp.mileage_km)) <= 0:
            raise ExpenseError('Enter the distance in km for a mileage expense.')
        rate = policy.mileage_rate if policy else Decimal('0.5')
        exp.amount = money(Decimal(str(exp.mileage_km)) * rate)
        exp.currency = 'AED'
    elif kind == 'per_diem':
        if not exp.per_diem_days or Decimal(str(exp.per_diem_days)) <= 0:
            raise ExpenseError('Enter the number of days for a per diem expense.')
        if policy and policy.per_diem_rate and policy.per_diem_rate > 0:
            exp.amount = money(Decimal(str(exp.per_diem_days)) * policy.per_diem_rate)
            exp.currency = 'AED'
    exp.amount = money(exp.amount)
    if exp.amount <= 0:
        raise ExpenseError('The amount must be more than zero.')
    exp.exchange_rate = rate_to_aed(exp.currency, exp.exchange_rate if (exp.currency or 'AED').upper() != 'AED' else None)
    exp.amount_aed = money(exp.amount * exp.exchange_rate)
    return exp


def _limit(policy, category_id):
    if policy is None:
        return None
    for lim in policy.limits.all():
        if lim.category_id == category_id:
            return lim
    return None


def check_expense(exp, policy, report=None):
    """Policy violations of one expense: [{code, level: warn|block, message}]."""
    out = []
    cat = exp.category
    amt = money(exp.amount_aed)
    if cat.receipt_required_above is not None and amt > cat.receipt_required_above and not exp.receipt:
        out.append({'code': 'receipt', 'level': 'block',
                    'message': f'Receipt required for {cat.name} above AED {cat.receipt_required_above:,.2f}.'})
    if cat.description_required and not (exp.description or '').strip():
        out.append({'code': 'description', 'level': 'block', 'message': f'A description is required for {cat.name}.'})
    lim = _limit(policy, cat.pk)
    if lim is not None:
        lvl = lim.block_or_warn
        if lim.per_expense_limit is not None and amt > lim.per_expense_limit:
            out.append({'code': 'per_expense', 'level': lvl,
                        'message': f'{cat.name}: AED {amt:,.2f} is over the limit of AED {lim.per_expense_limit:,.2f} per expense.'})
        if lim.per_day_limit is not None:
            day = Expense.objects.filter(employee_id=exp.employee_id, category_id=cat.pk, date=exp.date).exclude(status='rejected')
            if exp.pk:
                day = day.exclude(pk=exp.pk)
            day_total = money((day.aggregate(s=Sum('amount_aed'))['s'] or ZERO) + amt)
            if day_total > lim.per_day_limit:
                out.append({'code': 'per_day', 'level': lvl,
                            'message': f'{cat.name}: AED {day_total:,.2f} on {exp.date:%d/%m/%Y} is over the daily limit of AED {lim.per_day_limit:,.2f}.'})
        rep = report or (exp.report if exp.report_id else None)
        if lim.per_report_limit is not None and rep is not None:
            qs = rep.expenses.filter(category_id=cat.pk).exclude(status='rejected')
            if exp.pk:
                qs = qs.exclude(pk=exp.pk)
            rep_total = money((qs.aggregate(s=Sum('amount_aed'))['s'] or ZERO) + amt)
            if rep_total > lim.per_report_limit:
                out.append({'code': 'per_report', 'level': lvl,
                            'message': f'{cat.name}: AED {rep_total:,.2f} in this report is over the report limit of AED {lim.per_report_limit:,.2f}.'})
    return out


def has_block(violations):
    return any(v.get('level') == 'block' for v in violations or [])


def prepare_expense(exp):
    """Fill branch / department from the employee, amounts, violations. Does not save."""
    emp = employee(exp.employee_id)
    if emp is None:
        raise ExpenseError('Employee not found.')
    exp.branch_id = emp.emp_branch_id_id
    if not exp.department_id:
        exp.department_id = emp.emp_dept_id_id
    pol = policy_for(emp)
    compute_amounts(exp, pol)
    exp.policy_violations = check_expense(exp, pol)
    return exp


# ------------------------------------------------------------------ numbering
def next_number(model, prefix):
    year = timezone.now().year
    base = f'{prefix}-{year}-'
    last = model.objects.filter(number__startswith=base).order_by('-number').values_list('number', flat=True).first()
    n = 1
    if last:
        try:
            n = int(last.rsplit('-', 1)[1]) + 1
        except (ValueError, IndexError):
            n = model.objects.filter(number__startswith=base).count() + 1
    return f'{base}{n:04d}'


# ------------------------------------------------------------------ approvals (reports and trips)
def _admin_user():
    U = M('UserManagement', 'CustomUser')
    return U.objects.filter(is_active=True, is_superuser=True).order_by('pk').first()


def build_chain(emp, roles):
    """[(user, role)] – one step per role, never the employee, never the same person twice;
    a missing holder falls back to branch HR → company HR → company admin."""
    from LeavePolicy.approvers import holder
    own = own_user_ids(emp)
    chain, used = [], set()
    for role in roles or DEFAULT_ROLES:
        tries = [(role, holder(role, emp) if role != 'admin' else _admin_user())]
        tries += [('branch_hr', holder('branch_hr', emp)), ('company_hr', holder('company_hr', emp)), ('admin', _admin_user())]
        for how, u in tries:
            if u is None or u.pk in own:
                continue
            if u.pk in used:
                break           # the person for this level already approves an earlier level
            chain.append((u, role if how == role else f'{role}>{how}'))
            used.add(u.pk)
            break
    return chain


def _target_kw(target):
    return {'report': target} if isinstance(target, ExpenseReport) else {'trip': target}


def _round_of(target):
    if isinstance(target, ExpenseReport):
        return target.round
    return (ExpenseApproval.objects.filter(trip=target).order_by('-round').values_list('round', flat=True).first() or 0)


def start_approvals(target, emp, roles):
    rnd = _round_of(target) + (0 if isinstance(target, ExpenseReport) else 1)
    chain = build_chain(emp, roles)
    if not chain:
        raise ExpenseError('No approver could be found (reporting manager, branch HR, company HR or admin).')
    for i, (u, role) in enumerate(chain, start=1):
        ExpenseApproval.objects.create(round=rnd, level=i, approver_user_id=u.pk, role=role,
                                       status='pending' if i == 1 else 'waiting', **_target_kw(target))
        if i == 1:
            _notify(u, f'{"Expense report" if isinstance(target, ExpenseReport) else "Trip"} {target.number} waits for your approval',
                    f'{person(emp)} submitted {target.number}.')
    return chain


def current_step(target):
    return ExpenseApproval.objects.filter(status='pending', **_target_kw(target)).order_by('-round', 'level').first()


def _find_step(target, user, is_admin):
    step = current_step(target)
    if step is None:
        raise ExpenseError('Nothing is waiting for approval here.')
    emp = employee(target.employee_id)
    if user.pk in own_user_ids(emp):
        raise ExpenseError('You cannot approve your own request.', status=403)
    if step.approver_user_id != user.pk and not is_admin:
        raise ExpenseError('This step is waiting for another approver.', status=403)
    return step


def _close_steps(target, status='skipped'):
    ExpenseApproval.objects.filter(status__in=('pending', 'waiting'), **_target_kw(target)).update(status=status)


def _notify(user, title, message):
    try:
        from zeo.module_helpers import notify
        notify(user=user, title=title[:100], message=message, notification_type='expense')
    except Exception:
        log.debug('expense notice skipped', exc_info=True)


# ------------------------------------------------------------------ reports
def recalc_report(report):
    qs = report.expenses.exclude(status='rejected')
    report.total = money(qs.aggregate(s=Sum('amount_aed'))['s'] or ZERO)
    dates = list(qs.values_list('date', flat=True))
    if dates and not report.from_date:
        report.from_date = min(dates)
    if dates and not report.to_date:
        report.to_date = max(dates)
    report.advance_applied = money(report.advance_lines.aggregate(s=Sum('amount'))['s'] or ZERO)
    report.amount_to_reimburse = money(max(report.total - report.advance_applied, ZERO))
    report.save(update_fields=['total', 'from_date', 'to_date', 'advance_applied', 'amount_to_reimburse', 'updated_at'])
    return report


def advance_balance(adv):
    used = adv.applications.aggregate(s=Sum('amount'))['s'] or ZERO
    return money(adv.amount - used)


def release_advances(report):
    advs = list(ExpenseAdvance.objects.filter(applications__report=report).distinct())
    report.advance_lines.all().delete()
    for a in advs:
        if a.status == 'settled':
            a.status = 'paid'
            a.save(update_fields=['status'])


def apply_advances(report):
    """Use the employee's paid advances with a balance (the report's trip first) against the report total."""
    release_advances(report)
    recalc_report(report)
    left = report.total
    advs = list(ExpenseAdvance.objects.filter(employee_id=report.employee_id, status='paid').order_by('date', 'id'))
    if report.trip_id:
        advs.sort(key=lambda a: (a.trip_id != report.trip_id, a.date, a.id))
    for a in advs:
        if left <= 0:
            break
        bal = advance_balance(a)
        if bal <= 0:
            continue
        use = min(bal, left)
        AdvanceApplication.objects.create(advance=a, report=report, amount=use)
        left -= use
    return recalc_report(report)


def _settle_advances(report):
    for a in ExpenseAdvance.objects.filter(applications__report=report).distinct():
        if a.status == 'paid' and advance_balance(a) <= 0:
            a.status = 'settled'
            a.save(update_fields=['status'])


def add_expenses(report, ids):
    if report.status not in ('draft', 'sent_back'):
        raise ExpenseError('Expenses can only be added to a draft or sent-back report.')
    exps = list(Expense.objects.filter(pk__in=ids))
    if len(exps) != len(set(int(i) for i in ids)):
        raise ExpenseError('Some expenses were not found.')
    for e in exps:
        if e.employee_id != report.employee_id:
            raise ExpenseError('Only expenses of the report\'s employee can be added.')
        if e.status != 'unreported' or (e.report_id and e.report_id != report.pk):
            raise ExpenseError(f'The expense of {e.date:%d/%m/%Y} ({e.amount_aed}) is already in a report.')
    Expense.objects.filter(pk__in=[e.pk for e in exps]).update(report=report)
    return recalc_report(report)


def remove_expense(report, exp):
    if report.status not in ('draft', 'sent_back') or exp.report_id != report.pk:
        raise ExpenseError('This expense cannot be removed from the report now.')
    exp.report = None
    exp.status = 'unreported'
    exp.save(update_fields=['report', 'status', 'updated_at'])
    return recalc_report(report)


@transaction.atomic
def submit_report(report, user):
    report = ExpenseReport.objects.select_for_update().get(pk=report.pk)
    if report.status not in ('draft', 'sent_back'):
        raise ExpenseError(f'The report is {report.get_status_display().lower()} – it cannot be submitted.')
    emp = employee(report.employee_id)
    exps = list(report.expenses.select_related('category'))
    if not exps:
        raise ExpenseError('Add at least one expense to the report.')
    pol = report.policy or policy_for(emp)
    problems = []
    for e in exps:
        if e.employee_id != report.employee_id:
            raise ExpenseError('The report has expenses of another employee.')
        if e.status != 'unreported':
            raise ExpenseError(f'The expense of {e.date:%d/%m/%Y} is {e.get_status_display().lower()}.')
        e.policy_violations = check_expense(e, pol, report)
        e.save(update_fields=['policy_violations'])
        problems += [f'{e.date:%d/%m/%Y} {e.category.name}: {v["message"]}' for v in e.policy_violations if v['level'] == 'block']
    if problems:
        raise ExpenseError('The report breaks the expense policy: ' + ' '.join(problems), problems=problems)
    report.policy = pol
    report.round += 1
    report.status = 'submitted'
    report.submitted_at = timezone.now()
    report.save(update_fields=['policy', 'round', 'status', 'submitted_at', 'updated_at'])
    apply_advances(report)
    start_approvals(report, emp, pol.roles() if pol else DEFAULT_ROLES)
    report.expenses.update(status='submitted')
    report.refresh_from_db()
    return report


@transaction.atomic
def act_report(report, user, action, note='', is_admin=False):
    report = ExpenseReport.objects.select_for_update().get(pk=report.pk)
    if report.status != 'submitted':
        raise ExpenseError('The report is not waiting for approval.')
    if action in ('reject', 'send_back') and not (note or '').strip():
        raise ExpenseError('Give a reason.')
    step = _find_step(report, user, is_admin)
    step.acted_by_user_id, step.acted_at = user.pk, timezone.now()
    step.note = (note or '')[:1000] + ('' if step.approver_user_id == user.pk else ' (by company admin)')
    emp = employee(report.employee_id)
    if action == 'approve':
        step.status = 'approved'
        step.save()
        nxt = ExpenseApproval.objects.filter(report=report, round=step.round, status='waiting').order_by('level').first()
        if nxt:
            nxt.status = 'pending'
            nxt.save(update_fields=['status'])
            U = M('UserManagement', 'CustomUser')
            _notify(U.objects.filter(pk=nxt.approver_user_id).first(), f'Expense report {report.number} waits for your approval',
                    f'{report.number} of {person(emp)} – AED {report.total:,.2f} waits for your approval.')
        else:
            report.status, report.approved_at = 'approved', timezone.now()
            report.save(update_fields=['status', 'approved_at', 'updated_at'])
            report.expenses.exclude(status='rejected').update(status='approved')
            _settle_advances(report)
            _notify(getattr(emp, 'users', None), f'Expense report {report.number} approved', f'{report.number} approved – AED {report.amount_to_reimburse:,.2f} will be reimbursed.')
    elif action == 'reject':
        step.status = 'rejected'
        step.save()
        _close_steps(report)
        report.status = 'rejected'
        report.save(update_fields=['status', 'updated_at'])
        report.expenses.update(status='rejected')
        release_advances(report)
        recalc_report(report)
        _notify(getattr(emp, 'users', None), f'Expense report {report.number} rejected', f'{report.number} rejected: {note}')
    elif action == 'send_back':
        step.status = 'sent_back'
        step.save()
        _close_steps(report)
        report.status = 'sent_back'
        report.save(update_fields=['status', 'updated_at'])
        report.expenses.update(status='unreported')        # editable again, still in the report
        release_advances(report)
        recalc_report(report)
        _notify(getattr(emp, 'users', None), f'Expense report {report.number} sent back', f'{report.number} sent back: {note}')
    else:
        raise ExpenseError('Action is approve, reject or send_back.')
    report.refresh_from_db()
    return report


@transaction.atomic
def reimburse(report, via, reference='', on=None, user=None):
    report = ExpenseReport.objects.select_for_update().get(pk=report.pk)
    if report.status != 'approved':
        raise ExpenseError('Only an approved report can be reimbursed.')
    if report.reimburse_via == 'payroll' and report.payroll_run_id is None and via == 'payroll':
        raise ExpenseError('The report is already set to be paid with the next payroll.')
    if via not in ('direct', 'payroll'):
        raise ExpenseError('Reimburse via direct or payroll.')
    report.reimburse_via = via
    if via == 'direct' or report.amount_to_reimburse <= 0:
        if via == 'direct' and report.amount_to_reimburse > 0 and not (reference or '').strip():
            raise ExpenseError('Give the payment reference (bank transfer / cheque / cash voucher).')
        _mark_reimbursed(report, on or date.today(), reference or ('Covered by advance' if report.amount_to_reimburse <= 0 else ''))
    else:
        report.reimbursement_reference = (reference or 'With the next payroll')[:100]
        report.save(update_fields=['reimburse_via', 'reimbursement_reference', 'updated_at'])
    report.refresh_from_db()
    return report


def _mark_reimbursed(report, on, reference, run_id=None):
    report.status = 'reimbursed'
    report.reimbursed_on = on
    report.reimbursement_reference = (reference or '')[:100]
    if run_id:
        report.payroll_run_id = run_id
    report.save()
    report.expenses.filter(status='approved').update(status='reimbursed')
    _settle_advances(report)


def payroll_reports(employee_id, end_date=None):
    qs = ExpenseReport.objects.filter(employee_id=employee_id, status='approved', reimburse_via='payroll', payroll_run_id__isnull=True)
    if end_date:
        qs = qs.filter(approved_at__date__lte=end_date)
    return qs


def expense_reimbursement_amount(employee, start_date=None, end_date=None):
    """Payroll formula variable: approved expense reports set to be paid with the payroll and not paid yet."""
    emp_id = getattr(employee, 'pk', employee)
    total = payroll_reports(emp_id, end_date).aggregate(s=Sum('amount_to_reimburse'))['s']
    return money(total or ZERO)


def mark_paid_by_payslip(payslip):
    """A payslip was created: the reports its payroll paid are reimbursed with that run."""
    run = payslip.payroll_run
    end = getattr(run, 'attendance_end_date', None) or date(run.year, run.month, 28)
    on = getattr(run, 'payment_date', None) or date.today()
    n = 0
    for r in payroll_reports(payslip.employee_id, end):
        _mark_reimbursed(r, on, f'Payroll {run.month:02d}/{run.year}', run_id=payslip.payroll_run_id)
        n += 1
    return n


# ------------------------------------------------------------------ trips and advances
@transaction.atomic
def submit_trip(trip, user):
    if trip.status not in ('draft', 'rejected'):
        raise ExpenseError('Only a draft trip can be submitted.')
    if trip.end_date < trip.start_date:
        raise ExpenseError('The trip ends before it starts.')
    emp = employee(trip.employee_id)
    pol = policy_for(emp)
    start_approvals(trip, emp, pol.roles() if pol else DEFAULT_ROLES)
    trip.status = 'submitted'
    trip.save(update_fields=['status'])
    return trip


@transaction.atomic
def act_trip(trip, user, action, note='', is_admin=False):
    if trip.status != 'submitted':
        raise ExpenseError('The trip is not waiting for approval.')
    step = _find_step(trip, user, is_admin)
    step.acted_by_user_id, step.acted_at, step.note = user.pk, timezone.now(), (note or '')[:1000]
    if action == 'approve':
        step.status = 'approved'
        step.save()
        nxt = ExpenseApproval.objects.filter(trip=trip, round=step.round, status='waiting').order_by('level').first()
        if nxt:
            nxt.status = 'pending'
            nxt.save(update_fields=['status'])
        else:
            trip.status = 'approved'
            trip.save(update_fields=['status'])
            if trip.advance_requested and trip.advance_requested > 0 and not trip.advances.exists():
                ExpenseAdvance.objects.create(number=next_number(ExpenseAdvance, 'ADV'), employee_id=trip.employee_id, branch_id=trip.branch_id,
                                              trip=trip, amount=trip.advance_requested, date=date.today(), purpose=f'Trip {trip.number}: {trip.purpose}'[:200],
                                              status='approved', approved_by_id=user.pk, created_by_id=user.pk)
    elif action == 'reject':
        if not (note or '').strip():
            raise ExpenseError('Give a reason.')
        step.status = 'rejected'
        step.save()
        _close_steps(trip)
        trip.status = 'rejected'
        trip.save(update_fields=['status'])
    else:
        raise ExpenseError('Action is approve or reject.')
    return trip


def approve_advance(adv, user):
    if adv.status != 'requested':
        raise ExpenseError('Only a requested advance can be approved.')
    if user.pk in own_user_ids(employee(adv.employee_id)):
        raise ExpenseError('You cannot approve your own advance.', status=403)
    adv.status, adv.approved_by_id = 'approved', user.pk
    adv.save(update_fields=['status', 'approved_by_id'])
    return adv


def pay_advance(adv, user, reference, on=None):
    if adv.status != 'approved':
        raise ExpenseError('Only an approved advance can be paid.')
    if not (reference or '').strip():
        raise ExpenseError('Give the payment reference.')
    adv.status, adv.reference, adv.paid_on = 'paid', reference[:100], on or date.today()
    adv.save(update_fields=['status', 'reference', 'paid_on'])
    return adv


# ------------------------------------------------------------------ accounting export
def _names(app, model, field, ids):
    ids = {i for i in ids if i}
    return dict(M(app, model).objects.filter(pk__in=ids).values_list('pk', field)) if ids else {}


def journal(from_date=None, to_date=None, branches=None):
    """Journal lines of approved / reimbursed reports (by approval date): Dr expense account per category and cost centre,
    Cr advance clearing for the advance used, Cr employee payable for the amount to reimburse."""
    st = ExpenseSettings.get()
    qs = ExpenseReport.objects.filter(status__in=('approved', 'reimbursed')).order_by('approved_at', 'id')
    if from_date:
        qs = qs.filter(approved_at__date__gte=from_date)
    if to_date:
        qs = qs.filter(approved_at__date__lte=to_date)
    if branches is not None:
        qs = qs.filter(branch_id__in=branches)
    reports = list(qs)
    emps = {e.pk: e for e in M('EmpManagement', 'emp_master').objects.filter(pk__in={r.employee_id for r in reports})}
    lines = []
    for r in reports:
        emp = emps.get(r.employee_id)
        who = person(emp)
        jdate = (r.approved_at or r.created_at).date()
        for e in r.expenses.exclude(status='rejected').select_related('category', 'cost_center').order_by('date', 'id'):
            lines.append({'date': jdate, 'report': r.number, 'employee': who, 'account': e.category.gl_account or st.default_expense_account,
                          'description': f'{e.category.name} {e.date:%d/%m/%Y} {e.merchant}'.strip(), 'cost_center': e.cost_center.code if e.cost_center_id else '',
                          'department_id': e.department_id, 'project_id': e.project_id, 'debit': money(e.amount_aed), 'credit': ZERO})
        if r.advance_applied > 0:
            lines.append({'date': jdate, 'report': r.number, 'employee': who, 'account': st.advance_account, 'description': f'Advance used – {who}',
                          'cost_center': '', 'department_id': r.department_id, 'project_id': None, 'debit': ZERO, 'credit': money(r.advance_applied)})
        if r.amount_to_reimburse > 0:
            lines.append({'date': jdate, 'report': r.number, 'employee': who, 'account': st.payable_account, 'description': f'Reimbursement due – {who}',
                          'cost_center': '', 'department_id': r.department_id, 'project_id': None, 'debit': ZERO, 'credit': money(r.amount_to_reimburse)})
    dr = money(sum((x['debit'] for x in lines), ZERO))
    cr = money(sum((x['credit'] for x in lines), ZERO))
    return {'lines': lines, 'debit': dr, 'credit': cr, 'balanced': dr == cr, 'reports': len(reports)}


def journal_csv(data):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Date', 'Report', 'Employee', 'Account', 'Description', 'Cost centre', 'Debit', 'Credit'])
    for x in data['lines']:
        w.writerow([x['date'].isoformat(), x['report'], x['employee'], x['account'], x['description'], x['cost_center'],
                    f'{x["debit"]:.2f}', f'{x["credit"]:.2f}'])
    w.writerow(['', '', '', '', 'Total', '', f'{data["debit"]:.2f}', f'{data["credit"]:.2f}'])
    return buf.getvalue()


# ------------------------------------------------------------------ analytics
def analytics(qs):
    """Spend by category, department, employee and month of an Expense queryset."""
    by_cat, by_dept, by_emp, by_month = defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal), defaultdict(Decimal)
    rows = list(qs.values('category__name', 'department_id', 'employee_id', 'date', 'amount_aed'))
    for r in rows:
        a = r['amount_aed'] or ZERO
        by_cat[r['category__name']] += a
        by_dept[r['department_id']] += a
        by_emp[r['employee_id']] += a
        by_month[r['date'].strftime('%Y-%m')] += a
    depts = _names('OrganisationManager', 'dept_master', 'dept_name', by_dept.keys())
    emps = {e.pk: person(e) for e in M('EmpManagement', 'emp_master').objects.filter(pk__in=list(by_emp.keys()))}

    def lst(d, label=lambda k: k):
        return [{'label': label(k) or '–', 'key': k, 'amount': money(v)} for k, v in sorted(d.items(), key=lambda kv: -kv[1])]
    return {'total': money(sum(by_cat.values(), ZERO)), 'count': len(rows),
            'by_category': lst(by_cat), 'by_department': lst(by_dept, lambda k: depts.get(k, 'No department')),
            'by_employee': lst(by_emp, lambda k: emps.get(k, str(k)))[:50],
            'by_month': [{'label': k, 'key': k, 'amount': money(v)} for k, v in sorted(by_month.items())]}


def category_kinds():
    return dict(ExpenseCategory.KINDS)
