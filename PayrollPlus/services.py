"""
v1.18.0 – payroll controls, leave-encashment approval steps and the final settlement.
"""
import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
WAGE_CATEGORIES = ('basic', 'hra', 'housing_allowance', 'transport_allowance', 'food_allowance', 'telephone_allowance', 'medical_allowance',
                   'education_allowance', 'travel_allowance', 'other_allowance')
NOT_WAGE_CODES = ('ATK', 'PC', 'GTY')


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems


def q2(x):
    return Decimal(str(x or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def settings():
    return M('PayrollPlus', 'PayrollPlusSettings').get()


def user_name(uid):
    U = M('UserManagement', 'CustomUser')
    u = U.objects.filter(pk=uid).first() if uid else None
    if u is None:
        return ''
    E = M('EmpManagement', 'emp_master')
    e = E.objects.filter(users=u).first()
    return f'{e.emp_first_name} {e.emp_last_name or ""}'.strip() + f' ({u.username})' if e else u.username


# ------------------------------------------------------------------ payroll run controls
def month_end(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def run_warnings(run):
    """Checks shown on the payroll run: late payment (WPS), payment before the attendance cut-off, who prepared it."""
    out = []
    s = settings()
    pay = run.payment_date
    if pay:
        due_by = month_end(run.year, run.month) + timedelta(days=s.wps_late_days)
        if pay > due_by:
            out.append({'level': 'error', 'code': 'wps_late',
                        'text': tr('Payment date {0:%d/%m/%Y} is more than {1} days after the end of the salary month – the salary would be late under the Wage '
                                   'Protection System (due by {2:%d/%m/%Y}).', pay, s.wps_late_days, due_by)})
        end = run.attendance_end_date or month_end(run.year, run.month)
        if run.attendance_end_date and pay < run.attendance_end_date:
            out.append({'level': 'warning', 'code': 'pay_before_cutoff',
                        'text': tr('Payment date {0:%d/%m/%Y} is before the attendance cut-off {1:%d/%m/%Y}.', pay, end)})
        if run.attendance_end_date and run.attendance_end_date < month_end(run.year, run.month):
            out.append({'level': 'info', 'code': 'cutoff',
                        'text': tr('Current-month salary with a cut-off: attendance, leave and overtime from {0:%d/%m/%Y} to {1:%d/%m/%Y} are in this run; '
                                   'anything after {1:%d/%m/%Y} is paid or deducted in the next run.', run.attendance_start_date, run.attendance_end_date)})
    else:
        out.append({'level': 'warning', 'code': 'no_payment_date', 'text': tr('No payment date – enter it to check the Wage Protection System deadline.')})
    return out


def control_for(run):
    RC = M('PayrollPlus', 'RunControl')
    return RC.objects.filter(run_id=run.pk).first()


def run_control_row(run):
    c = control_for(run)
    return {'prepared_by': user_name(c.prepared_by_id) if c else '', 'prepared_at': c.prepared_at if c else None,
            'approved_by': user_name(c.approved_by_id) if c else '', 'approved_at': c.approved_at if c else None,
            'paid_by': user_name(c.paid_by_id) if c else '', 'paid_at': c.paid_at if c else None,
            'warnings': run_warnings(run)}


def record_prepared(run, user):
    RC = M('PayrollPlus', 'RunControl')
    RC.objects.get_or_create(run_id=run.pk, defaults={'prepared_by_id': getattr(user, 'pk', None)})


def check_run_status_change(run, new_status, user):
    """Segregation of duties: the user who prepared the run may not approve it or mark it paid. Raises Refused."""
    if not settings().segregation or user is None:
        return
    c = control_for(run)
    if c and c.prepared_by_id and c.prepared_by_id == user.pk and new_status in ('approved', 'paid'):
        raise Refused(tr('You prepared this payroll run – somebody else must {0} it (segregation of duties).',
                         tr('approve') if new_status == 'approved' else tr('release the payment of')), status=403)
    if new_status == 'paid' and c and c.approved_by_id and c.approved_by_id == user.pk:
        raise Refused(tr('You approved this payroll run – somebody else must release the payment (segregation of duties).'), status=403)


def record_status_change(run, new_status, user, payment_ref=''):
    RC = M('PayrollPlus', 'RunControl')
    c, _ = RC.objects.get_or_create(run_id=run.pk)
    now = timezone.now()
    if new_status == 'approved':
        c.approved_by_id, c.approved_at = user.pk, now
    elif new_status == 'paid':
        c.paid_by_id, c.paid_at = user.pk, now
        if payment_ref:
            c.payment_ref = payment_ref[:60]
    elif new_status == 'processed':
        c.approved_by_id, c.approved_at = None, None
    c.save()


def check_payslip_approver(payslip, user):
    """The preparer of the run may not approve its payslips."""
    if not settings().segregation or user is None:
        return
    c = control_for(payslip.payroll_run) if payslip and payslip.payroll_run_id else None
    if c and c.prepared_by_id == user.pk:
        raise Refused(tr('You prepared this payroll – somebody else must approve its payslips (segregation of duties).'), status=403)


# ------------------------------------------------------------------ leave encashment steps
STEP_ORDER = ('hr', 'finance', 'management')


def encashment_steps(e_id):
    S = M('PayrollPlus', 'EncashmentStep')
    return {s.step: s for s in S.objects.filter(encashment_id=e_id)}


def encashment_steps_row(e_id):
    st = encashment_steps(e_id)
    chain = settings().encashment_chain
    out = []
    for k, label in M('PayrollPlus', 'EncashmentStep').STEPS:
        s = st.get(k)
        out.append({'step': k, 'label': label, 'done': bool(s), 'by': s.user_name if s else '', 'at': s.at if s else None, 'note': s.note if s else ''})
    nxt = next((k for k in STEP_ORDER if k not in st), None)
    return {'chain': chain, 'steps': out, 'next': nxt if chain else None}


def can_step(codes, admin, step):
    if admin:
        return True
    if step == 'hr':
        return bool({'change_leaveencashment', 'add_leaveencashment'} & codes)
    if step == 'finance':
        return 'finance_check_leaveencashment' in codes
    return 'approve_leaveencashment_final' in codes


def do_step(e, step, user, codes, admin, note=''):
    """Record HR / Finance step; the management step is the final approval (done by LeavePolicy's approve)."""
    S = M('PayrollPlus', 'EncashmentStep')
    st = encashment_steps(e.pk)
    if e.status not in ('draft', 'pending'):
        raise Refused(tr('This encashment is already {0}.', e.get_status_display().lower()))
    want = next((k for k in STEP_ORDER if k not in st), None)
    if step != want:
        raise Refused(tr('Next step: {0}.', dict(S.STEPS).get(want, want)))
    if not can_step(codes, admin, step):
        raise Refused(tr('You may not do this step.'), status=403)
    if any(s.user_id == user.pk for s in st.values()):
        raise Refused(tr('Each step must be done by a different person.'), status=403)
    S.objects.create(encashment_id=e.pk, step=step, user_id=user.pk, user_name=user_name(user.pk)[:150], note=(note or '')[:255])


def check_final_encashment(e, user, codes, admin):
    """Before LeavePolicy approves an encashment: with the chain on, HR and Finance must have done their steps and the approver
    holds the management right and is a third person."""
    if not settings().encashment_chain:
        return
    st = encashment_steps(e.pk)
    missing = [dict(M('PayrollPlus', 'EncashmentStep').STEPS)[k] for k in ('hr', 'finance') if k not in st]
    if missing:
        raise Refused(tr('Not yet: {0}.', ', '.join(missing)))
    if not can_step(codes, admin, 'management'):
        raise Refused(tr('Only management can give the final approval of a leave encashment.'), status=403)
    if any(s.user_id == user.pk for s in st.values()):
        raise Refused(tr('Each step must be done by a different person.'), status=403)


def record_final_encashment(e, user, note=''):
    S = M('PayrollPlus', 'EncashmentStep')
    if settings().encashment_chain:
        S.objects.get_or_create(encashment_id=e.pk, step='management', defaults={'user_id': user.pk, 'user_name': user_name(user.pk)[:150],
                                                                                  'note': (note or '')[:255]})


# ------------------------------------------------------------------ final settlement
def _wage(emp):
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    basic, wage = ZERO, ZERO
    parts = []
    for r in ESS.objects.filter(employee=emp, is_active=True).select_related('component'):
        c = r.component
        if c.component_type != 'addition' or c.component_value_type != 'fixed' or (c.code or '') in NOT_WAGE_CODES:
            continue
        if c.payroll_category not in WAGE_CATEGORIES:
            continue
        amt = q2(r.amount)
        if c.payroll_category == 'basic':
            basic += amt
        wage += amt
        parts.append(f'{c.name} {amt:,.2f}')
    return basic, wage, parts


def _last_paid_to(emp):
    """(salary paid to, inputs paid to) from the payroll runs of the employee. A run pays the fixed salary of its whole month;
    with a cut-off the attendance / leave / overtime inputs are taken only to the run's attendance end (the rest goes to the next run)."""
    PS = M('PayrollManagement', 'Payslip')
    sal, inp = None, None
    for ps in PS.objects.filter(employee=emp).exclude(payroll_run__status='pending').select_related('payroll_run'):
        r = ps.payroll_run
        me = month_end(r.year, r.month)
        end = r.attendance_end_date or me
        sal = me if sal is None or me > sal else sal
        inp = end if inp is None or end > inp else inp
    return sal, inp


def _unpaid_between(emp, a, b):
    """Unpaid days between a and b: unpaid leave (attendance calendar) + days absent without leave."""
    if not a or not b or b < a:
        return Decimal('0')
    AC = M('calendars', 'AttendanceCalendar')
    from django.db.models import Sum
    v = Decimal(str(AC.objects.filter(employee=emp, date__range=(a, b)).aggregate(t=Sum('unpaid_fraction'))['t'] or 0))
    try:
        AD = M('AttendancePlus', 'AttendanceDay')
        v += AD.objects.filter(employee_id=emp.pk, date__range=(a, b), status='absent').count()
    except LookupError:
        pass
    return v


def _salary_for(wage, a, b):
    """Fixed salary from a to b on the 30-day basis: a full calendar month is one month's wage, a part month wage ÷ 30 × days."""
    total, days, d = Decimal('0'), 0, a
    while d <= b:
        me = month_end(d.year, d.month)
        stop = min(me, b)
        n = (stop - d).days + 1
        if d.day == 1 and stop == me:
            total += wage
            days += 30
        else:
            n = min(n, 30)
            total += wage / 30 * n
            days += n
        d = stop + timedelta(days=1)
    return total, days


def _annual_type():
    LT = M('calendars', 'leave_type')
    return LT.objects.filter(leave_category='annual').order_by('id').first()


def compute(st, user=None):
    """(Re)calculate every component of the settlement from the current records. Draft only."""
    E = M('EmpManagement', 'emp_master')
    EOS = M('EmpManagement', 'EndOfService')
    eos = EOS.objects.select_related('resignation__employee').get(pk=st.eos_id)
    res = eos.resignation
    emp = res.employee
    try:   # the end of service figures (service, unpaid days, gratuity, notice, air ticket) from the current records
        from EmpManagement.utils import calculate_settlement
        calculate_settlement(eos)
        eos.refresh_from_db()
    except Exception:
        log.exception('end of service recalculation failed')
    lwd = res.last_working_date
    basic, wage, wage_parts = _wage(emp)
    daily_wage, daily_basic = wage / 30, basic / 30
    lines = []

    def add(key, label, kind, amount, basis=''):
        amount = q2(amount)
        if amount:
            lines.append({'key': key, 'label': label, 'kind': kind, 'amount': float(amount), 'basis': basis})

    # 1. salary due – from the day after the month paid by the last payroll to the last working day; unpaid days from the day
    #    after the last payroll's attendance cut-off (those after the cut-off were not yet deducted)
    paid_to, inputs_to = _last_paid_to(emp)
    start = (paid_to + timedelta(days=1)) if paid_to else (max(emp.emp_joined_date, lwd.replace(day=1)) if emp.emp_joined_date else lwd.replace(day=1))
    st.salary_from, st.salary_to = start, lwd
    if start <= lwd:
        amount, days = _salary_for(wage, start, lwd)
        add('salary', tr('Salary due {0:%d/%m/%Y} – {1:%d/%m/%Y}', start, lwd), 'earning', amount,
            tr('{0} days on the 30-day basis × wage {1:,.2f} ÷ 30 ({2})', days, wage, ', '.join(wage_parts)))
    elif paid_to:
        add('salary', tr('Salary already paid by payroll to {0:%d/%m/%Y}', paid_to), 'earning', 0, '')
    ua = (inputs_to + timedelta(days=1)) if inputs_to else start
    unpaid = _unpaid_between(emp, ua, lwd)
    if unpaid:
        add('unpaid', tr('Unpaid leave / absence {0:%d/%m/%Y} – {1:%d/%m/%Y} ({2} days)', ua, lwd, q2(unpaid)), 'deduction', daily_wage * unpaid,
            tr('wage {0:,.2f} ÷ 30 × days', wage))
    # 2. notice period
    notice = int(res.notice_period or 0)
    served = (lwd - res.resigned_on).days if res.resigned_on else 0
    unserved = max(notice - max(served, 0), 0)
    if unserved:
        if res.termination_type in ('termination', 'retirement', 'death_or_disablement'):
            add('notice', tr('Pay in lieu of notice ({0} days not served)', unserved), 'earning', daily_wage * unserved,
                tr('wage {0:,.2f} ÷ 30 × {1} days (Art. 43)', wage, unserved))
        elif not st.notice_waived:
            add('notice_short', tr('Notice period not served ({0} days)', unserved), 'deduction', daily_wage * unserved,
                tr('wage {0:,.2f} ÷ 30 × {1} days (Art. 43) – HR can waive it', wage, unserved))
    # 3. unused annual leave – basic ÷ 30 per day (termination settlement)
    AL = _annual_type()
    leave_days = Decimal('0')
    if AL is not None:
        try:
            from LeavePolicy import engine
            leave_days = Decimal(str(engine.balance_row(emp.pk, AL.pk).balance or 0))
        except Exception:
            B = M('calendars', 'emp_leave_balance')
            leave_days = Decimal(str(B.objects.filter(employee=emp, leave_type=AL).values_list('balance', flat=True).first() or 0))
        LR = M('calendars', 'employee_leave_request')
        future = sum(Decimal(str(r.approved_days or r.number_of_days or 0)) for r in LR.objects.filter(employee=emp, leave_type=AL, status='approved', start_date__gt=lwd))
        leave_days = max(leave_days + future, Decimal('0'))   # leave booked after the last day is not taken
    if leave_days:
        add('leave', tr('Unused annual leave {0} days', q2(leave_days)), 'earning', daily_basic * leave_days, tr('basic {0:,.2f} ÷ 30 × days', basic))
    LE = M('PayrollManagement', 'LeaveEncashment')
    for e in LE.objects.filter(employee=emp, status='approved', payroll_run__isnull=True):
        add(f'encash_{e.pk}', tr('Leave encashment approved, not paid yet ({0} days)', e.encashment_days), 'earning', e.encashment_amount, tr('approved encashment'))
    # 4. end-of-service gratuity
    if eos.gratuity_amount:
        add('gratuity', tr('End-of-service gratuity ({0} days)', q2(eos.gratuity_days)), 'earning', eos.gratuity_amount,
            tr('basic {0:,.2f} ÷ 30; 21 days a year for the first 5 years, 30 after; capped at 2 years’ basic', basic))
    else:
        try:
            from PayrollManagement.signals import is_uae_national
            if is_uae_national(emp):
                lines.append({'key': 'gratuity', 'label': tr('No gratuity – UAE national (GPSSA pension)'), 'kind': 'info', 'amount': 0, 'basis': ''})
            elif (lwd - emp.emp_joined_date).days < 365:
                lines.append({'key': 'gratuity', 'label': tr('No gratuity – less than one year of service'), 'kind': 'info', 'amount': 0, 'basis': ''})
        except Exception:
            pass
    # 5. outstanding earnings: overtime after the last payroll, expenses, air ticket
    OT = M('calendars', 'EmployeeOvertime')
    if inputs_to:
        ot = OT.objects.filter(employee=emp, approved=True, date__gt=inputs_to, date__lte=lwd)
        normal = sum((Decimal(str(o.hours)) for o in ot if o.ot_type == 'NORMAL'), Decimal('0'))
        rest = sum((Decimal(str(o.hours)) for o in ot if o.ot_type != 'NORMAL'), Decimal('0'))
        if normal or rest:
            add('overtime', tr('Overtime after the last payroll ({0} h normal, {1} h rest day / holiday)', normal, rest), 'earning',
                (normal * Decimal('1.25') + rest * Decimal('1.5')) * daily_basic / 8, tr('basic ÷ 30 ÷ 8 × 1.25 / 1.5'))
    try:
        ER = M('ExpenseManagement', 'ExpenseReport')
        for r in ER.objects.filter(employee_id=emp.pk, status='approved', payroll_run_id__isnull=True):
            add(f'expense_{r.pk}', tr('Expense report {0} to reimburse', r.number), 'earning', getattr(r, 'amount_to_reimburse', 0) or 0, '')
    except LookupError:
        pass
    if eos.air_ticket:
        add('air_ticket', tr('Air ticket allowance'), 'earning', eos.air_ticket, tr('approved air ticket allocation'))
    # 6. deductions: loans, salary advances, approved asset recoveries (never the asset value without an approved decision)
    LA = M('PayrollManagement', 'LoanApplication')
    for l in LA.objects.filter(employee=emp, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')):
        if l.remaining_balance:
            add(f'loan_{l.pk}', tr('Loan {0} – outstanding balance', l.document_number or l.pk), 'deduction', l.remaining_balance, tr('verified loan balance'))
    AS = M('PayrollManagement', 'AdvanceSalaryRequest')
    for a in AS.objects.filter(employee=emp, status__iexact='approved'):
        left = a.requested_amount
        if left:
            add(f'advance_{a.pk}', tr('Salary advance {0} – not yet deducted', a.document_number or a.pk), 'deduction', left, '')
    try:
        from AssetPlus import services as AP
        c = AP.clearance(emp.pk)
        due = Decimal(str(c.get('recovery_due') or 0))
        if due:
            add('assets', tr('Asset recovery approved by management'), 'deduction', due, tr('approved damage / loss decisions'))
        open_items = [i for i in (c.get('items') or []) if i.get('kind') != 'recovery']   # the recovery is deducted here
        if open_items and not c.get('waived'):
            lines.append({'key': 'clearance', 'label': tr('Asset clearance not complete – {0}', '; '.join(i.get('text', '') for i in open_items[:3])),
                          'kind': 'block', 'amount': 0, 'basis': ''})
    except Exception:
        log.debug('asset clearance not available', exc_info=True)
    earn = sum((Decimal(str(l['amount'])) for l in lines if l['kind'] == 'earning'), Decimal('0'))
    ded = sum((Decimal(str(l['amount'])) for l in lines if l['kind'] == 'deduction'), Decimal('0'))
    st.lines = lines
    st.total_earnings, st.total_deductions, st.net = q2(earn), q2(ded), q2(earn - ded)
    st.calc_date = timezone.localdate()
    st.joined = emp.emp_joined_date
    st.last_working_date = lwd
    st.exit_type = res.get_termination_type_display() if hasattr(res, 'get_termination_type_display') else (res.termination_type or '')
    st.service_days = int(eos.total_service_days or 0)
    st.unpaid_days = q2(eos.leave_days_without_pay or 0)
    st.basic, st.wage = q2(basic), q2(wage)
    st.due_date = lwd + timedelta(days=settings().settlement_due_days)
    st.save()
    return st


def start(eos, user):
    S = M('PayrollPlus', 'ExitSettlement')
    st = S.objects.filter(eos_id=eos.pk).first()
    if st is None:
        st = S(eos_id=eos.pk, employee_id=eos.resignation.employee_id, calc_date=timezone.localdate(), last_working_date=eos.resignation.last_working_date,
               created_by_id=getattr(user, 'pk', None))
    if st.status == 'draft':
        compute(st, user)
    return st


FLOW = {'submit': ('draft', 'submitted'), 'validate': ('submitted', 'validated'), 'approve': ('validated', 'approved'), 'pay': ('approved', 'paid'),
        'return': (None, 'draft'), 'cancel': ('draft', 'cancelled')}


def act(st, action, user, codes, admin, data=None):
    data = data or {}
    if action not in FLOW:
        raise Refused(tr('Unknown action.'))
    frm, to = FLOW[action]
    if action == 'return':
        if st.status not in ('submitted', 'validated'):
            raise Refused(tr('Only a settlement waiting for Finance or management can be sent back.'))
        if not (admin or {'validate_exitsettlement', 'approve_exitsettlement'} & codes):
            raise Refused(tr('You may not send this settlement back.'), status=403)
        if not (data.get('note') or '').strip():
            raise Refused(tr('Say why the settlement is sent back.'), problems={'note': 'Required.'})
    elif st.status != frm:
        raise Refused(tr('This settlement is {0}.', st.get_status_display().lower()), status=409)
    need = {'submit': {'add_exitsettlement', 'change_exitsettlement'}, 'validate': {'validate_exitsettlement'},
            'approve': {'approve_exitsettlement'}, 'pay': {'pay_exitsettlement'}, 'cancel': {'change_exitsettlement'}}.get(action)
    if need and not admin and not (need & codes):
        raise Refused(tr('You may not do this step.'), status=403)
    done_by = {st.submitted_by_id, st.validated_by_id, st.approved_by_id} - {None}
    if action in ('validate', 'approve') and user.pk in done_by:
        raise Refused(tr('Each step must be done by a different person.'), status=403)
    if action == 'pay' and user.pk == st.approved_by_id:
        raise Refused(tr('The approver may not release the payment (segregation of duties).'), status=403)
    now = timezone.now()
    with transaction.atomic():
        if action == 'submit':
            compute(st, user)
            if any(l['kind'] == 'block' for l in st.lines):
                raise Refused(tr('Complete the asset clearance first: every company asset must be returned (or the clearance waived).'))
            st.submitted_by_id, st.submitted_at = user.pk, now
        elif action == 'validate':
            st.validated_by_id, st.validated_at = user.pk, now
        elif action == 'approve':
            st.approved_by_id, st.approved_at = user.pk, now
        elif action == 'pay':
            ref = (data.get('payment_ref') or '').strip()
            if not ref:
                raise Refused(tr('Enter the payment reference (bank transfer / WPS).'), problems={'payment_ref': 'Required.'})
            on = data.get('paid_on')
            st.paid_on = date.fromisoformat(str(on)[:10]) if on else timezone.localdate()
            st.payment_ref, st.paid_by_id, st.paid_at = ref[:60], user.pk, now
            _close_eos(st, user)
        elif action == 'return':
            st.submitted_by_id = st.validated_by_id = st.approved_by_id = None
            st.submitted_at = st.validated_at = st.approved_at = None
            st.notes = (st.notes + '\n' if st.notes else '') + f'{timezone.localdate():%d/%m/%Y} sent back by {user_name(user.pk)}: {data.get("note")}'
        if action in ('submit', 'validate', 'approve', 'pay') and data.get('note'):
            st.notes = (st.notes + '\n' if st.notes else '') + f'{timezone.localdate():%d/%m/%Y} {action} – {user_name(user.pk)}: {data.get("note")}'
        st.status = to
        st.save()
    return st


def _close_eos(st, user):
    EOS = M('EmpManagement', 'EndOfService')
    eos = EOS.objects.filter(pk=st.eos_id).first()
    if eos is None:
        return
    if any(l['key'] == 'assets' for l in st.lines):
        try:   # the approved asset recovery is deducted in this settlement
            from AssetPlus.services import settle_in_final_settlement
            settle_in_final_settlement(st.employee_id, user)
        except ImportError:
            pass
    for s in ('processed', 'paid'):
        if eos.status != s:
            eos.status = s
            eos.save()     # AssetPlus refuses this while assets are outstanding (exit clearance)
    LE = M('PayrollManagement', 'LeaveEncashment')
    for l in st.lines:
        if l['key'].startswith('encash_'):
            LE.objects.filter(pk=int(l['key'].split('_')[1])).update(status='processed', processed_at=timezone.now())


def _eligible_years(st):
    """Eligible service in years (calendar years, unpaid days taken off) as the gratuity uses it."""
    try:
        y = M('EmpManagement', 'EndOfService').objects.filter(pk=st.eos_id).values_list('years_of_service', flat=True).first()
        if y is not None:
            return round(float(y), 2)
    except Exception:
        pass
    return round(st.service_days / 365, 2) if st.service_days else 0


def settlement_row(st, full=False):
    E = M('EmpManagement', 'emp_master')
    e = E.objects.filter(pk=st.employee_id).select_related('emp_dept_id', 'emp_desgntn_id').first()
    today = timezone.localdate()
    row = {'id': st.pk, 'eos_id': st.eos_id, 'employee_id': st.employee_id, 'employee': f'{e.emp_first_name} {e.emp_last_name or ""}'.strip() if e else '',
           'employee_code': e.emp_code if e else '', 'department': str(e.emp_dept_id) if e and e.emp_dept_id_id else '',
           'designation': str(e.emp_desgntn_id) if e and e.emp_desgntn_id_id else '', 'joined': st.joined, 'last_working_date': st.last_working_date,
           'exit_type': st.exit_type, 'service_days': st.service_days, 'service_years': _eligible_years(st),
           'unpaid_days': float(st.unpaid_days), 'basic': float(st.basic), 'wage': float(st.wage), 'total_earnings': float(st.total_earnings),
           'total_deductions': float(st.total_deductions), 'net': float(st.net), 'status': st.status, 'status_label': st.get_status_display(),
           'due_date': st.due_date, 'overdue': bool(st.due_date and st.status not in ('paid', 'cancelled') and today > st.due_date),
           'days_to_due': (st.due_date - today).days if st.due_date else None, 'paid_on': st.paid_on, 'payment_ref': st.payment_ref,
           'calc_date': st.calc_date, 'notice_waived': st.notice_waived}
    if full:
        row.update({'lines': st.lines, 'salary_from': st.salary_from, 'salary_to': st.salary_to, 'notes': st.notes,
                    'trail': [{'step': tr('Calculated by HR'), 'by': user_name(st.submitted_by_id), 'at': st.submitted_at},
                              {'step': tr('Validated by Finance'), 'by': user_name(st.validated_by_id), 'at': st.validated_at},
                              {'step': tr('Approved by management'), 'by': user_name(st.approved_by_id), 'at': st.approved_at},
                              {'step': tr('Paid'), 'by': user_name(st.paid_by_id), 'at': st.paid_at}]})
    return row
