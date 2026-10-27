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


def _sod_hint():
    """v1.26.0 – a new company often has one administrator only: say how to go on instead of a dead end."""
    try:
        from django.db import connection
        U = M('UserManagement', 'CustomUser')
        n = U.objects.filter(is_active=True, is_ess=False, tenants__schema_name=connection.schema_name).distinct().count()
    except Exception:
        n = 2
    if n <= 1:
        return ' ' + tr('This company has only one user: add a second user with payroll approval rights (Settings → Users), '
                        'or switch off "Segregation of duties" in Payroll → Setup → Payroll controls.')
    return ' ' + tr('Ask another user with payroll approval rights, or switch off "Segregation of duties" in Payroll → Setup → Payroll controls.')


def check_run_status_change(run, new_status, user):
    """Segregation of duties: the user who prepared the run may not approve it or mark it paid. Raises Refused."""
    if new_status == 'approved':   # v1.37.0 AI payroll auditor gate (audit required / critical findings open)
        try:
            from ZeoAI.payroll_audit import gate
            state, msg = gate(run)
            if state != 'ok':
                raise Refused(tr(msg), status=409)
        except ImportError:
            pass
    if not settings().segregation or user is None:
        return
    c = control_for(run)
    if c and c.prepared_by_id and c.prepared_by_id == user.pk and new_status in ('approved', 'paid'):
        raise Refused(tr('You prepared this payroll run – somebody else must {0} it (segregation of duties).',
                         tr('approve') if new_status == 'approved' else tr('release the payment of')) + _sod_hint(), status=403)
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
        raise Refused(tr('You prepared this payroll – somebody else must approve its payslips (segregation of duties).') + _sod_hint(), status=403)


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



# ------------------------------------------------------------------ v1.62.0 EOS – items of the leaver's statement
def person(uid):
    """Name of a login without the user name (the statement says "added by Layla Farouk")."""
    U = M('UserManagement', 'CustomUser')
    u = U.objects.filter(pk=uid).first() if uid else None
    if u is None:
        return ''
    e = M('EmpManagement', 'emp_master').objects.filter(users=u).first()
    return f'{e.emp_first_name} {e.emp_last_name or ""}'.strip() if e else (u.get_full_name() if hasattr(u, 'get_full_name') and u.get_full_name() else u.username)


def _add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:   # 29 February
        return d.replace(year=d.year + n, day=28)


def _gratuity_steps(eos, res, basic):
    """(basis, [steps]) – years × days × basic ÷ 30 per slab of the gratuity table (or the UAE law), and the cap."""
    try:
        years = Decimal(str(eos.years_of_service or 0))
        days_total = Decimal(str(eos.gratuity_days or 0))
        daily = basic / 30
        GT = M('OrganisationManager', 'GratuityTable')
        rules = [(Decimal(str(r.minimum_value)), Decimal(str(r.maximum_value)) if r.maximum_value else None,
                  Decimal(str(r.termination_days if res.termination_type in ('termination', 'retirement', 'death_or_disablement') else r.resignation_days)))
                 for r in GT.objects.filter(is_active=True).order_by('minimum_value')]
        if not rules:
            rules = [(Decimal(0), Decimal(5), Decimal(21)), (Decimal(5), None, Decimal(30))]
        steps, got = [], Decimal(0)
        steps.append(tr('Eligible service {0} years (unpaid days taken off)', q2(years)))
        for lo, hi, per in rules:
            if years <= lo or not per:
                continue
            yrs = (min(years, hi) if hi else years) - lo
            if yrs <= 0:
                continue
            got += yrs * per
            steps.append(tr('{0} years × {1} days = {2} days', q2(yrs), f'{per:g}', q2(yrs * per)))
        if abs(got - days_total) > Decimal('0.05'):   # another rule set (country pack) – show its total only
            steps = steps[:1]
        capped = days_total * daily > Decimal(str(eos.gratuity_amount or 0)) + Decimal('1')
        steps.append(tr('{0} days × basic {1:,.2f} ÷ 30 = {2:,.2f}', q2(days_total), basic, q2(days_total * daily) if capped else q2(eos.gratuity_amount)))
        cap = basic * 24
        if capped:
            steps.append(tr('Capped at 2 years’ pay: {0:,.2f}', q2(eos.gratuity_amount)))
        else:
            steps.append(tr('Cap 2 years’ basic {0:,.2f} – not reached', q2(cap)))
        basis = tr('{0} years × days × basic {1:,.2f} ÷ 30 (UAE Labour Law Art. 51)', q2(years), basic)
        return basis, steps
    except Exception:
        log.debug('gratuity steps', exc_info=True)
        return '', None


NAT_COUNTRY = {'FILIPINO': 'Philippines', 'EMIRATI': 'United Arab Emirates', 'BRITISH': 'United Kingdom', 'DUTCH': 'Netherlands', 'SWISS': 'Switzerland',
               'FRENCH': 'France', 'GREEK': 'Greece', 'THAI': 'Thailand', 'CHINESE': 'China', 'SUDANESE': 'Sudan', 'SENEGALESE': 'Senegal'}


def _home_countries(emp):
    """Country of the employee for the air ticket policy: the country on the employee, else the one of the nationality."""
    ids = set()
    if emp.emp_country_id_id:
        ids.add(emp.emp_country_id_id)
    nat = str(getattr(getattr(emp, 'emp_nationality', None), 'N_name', '') or '').strip().upper()
    if nat:
        C = M('Core', 'cntry_mstr')
        name = NAT_COUNTRY.get(nat)
        qs = C.objects.filter(country_name__iexact=name) if name else C.objects.filter(country_name__istartswith=nat[:4])
        ids.update(qs.values_list('id', flat=True)[:3])
    return ids


def ticket_policy(emp):
    """The active air ticket policy of the employee (country, then department / designation / category / branch if the policy limits them)."""
    P = M('PayrollManagement', 'AirTicketPolicy')
    best = None
    for p in P.objects.filter(is_active=True, country_id__in=_home_countries(emp)).prefetch_related('rules').order_by('id'):
        ok = True
        for rel, val in (('eligible_departments', emp.emp_dept_id_id), ('eligible_designations', emp.emp_desgntn_id_id),
                         ('eligible_categories', emp.emp_ctgry_id_id), ('branch', emp.emp_branch_id_id)):
            ids = set(getattr(p, rel).values_list('id', flat=True))
            if ids and val not in ids:
                ok = False
                break
        if ok:
            best = p
            break
    return best


def _air_tickets(emp, lwd, res, add, info):
    """1. approved allocations not used yet (remaining amount, no approved / processed ticket request, not expired);
    2. the ticket earned on exit by the policy: one ticket every N years of service – when the last completed cycle has no allocation it is
       due now (the open allocation of that cycle is paid in 1., never both)."""
    A = M('PayrollManagement', 'AirTicketAllocation')
    R = M('PayrollManagement', 'AirTicketRequest')
    allocs = list(A.objects.filter(employee=emp, is_active=True).exclude(status='REJECTED').select_related('policy').order_by('allocated_date', 'id'))
    first = True
    for a in allocs:
        if a.status != 'APPROVED':
            continue
        used = R.objects.filter(allocation=a, status__in=('APPROVED', 'PROCESSED')).exists()
        left = Decimal(str(a.remaining_amount if a.remaining_amount is not None else a.amount or 0))
        if used or left <= 0:
            continue
        if a.expiry_date and a.expiry_date < lwd:
            continue
        add('air_ticket' if first else f'air_ticket_{a.pk}', tr('Air ticket allowance – approved allocation not used'), 'earning', left,
            tr('{0}, allocated {1:%d/%m/%Y}', a.policy.name if a.policy_id else tr('allocation'), a.allocated_date), 'ticket')
        first = False
    p = ticket_policy(emp)
    if p is None or not emp.emp_joined_date:
        return
    freq = int(p.frequency_years or 1) or 1
    need = min([r.required_service_years for r in p.rules.all()] or [freq])
    from EmpManagement.utils import service_years
    yrs = service_years(emp.emp_joined_date, lwd)
    cycles = int(yrs // freq)
    nxt = _add_years(emp.emp_joined_date, (cycles + 1) * freq)
    if cycles < 1 or yrs < need:
        info('ticket_next', tr('Air ticket not earned yet'), tr('{0}: one ticket every {1} years – the first is due on {2:%d/%m/%Y}', p.name, freq,
                                                                  _add_years(emp.emp_joined_date, max(freq, need))), 'ticket')
        return
    start, end = _add_years(emp.emp_joined_date, (cycles - 1) * freq), _add_years(emp.emp_joined_date, cycles * freq)
    covered = [a for a in allocs if a.policy_id == p.pk and a.allocated_date and a.allocated_date >= start]
    if covered:
        info('ticket_cycle', tr('Air ticket of the cycle to {0:%d/%m/%Y} already allocated', end),
             tr('{0}: allocation of {1:%d/%m/%Y} – not paid again', p.name, covered[-1].allocated_date), 'ticket')
        return
    cash = p.allowance_type in ('CASH', 'BOTH') or any(r.rule_type == 'ENCASHMENT' for r in p.rules.all())
    add('air_ticket_due', tr('Air ticket earned on exit ({0} – {1})', start.year, end.year), 'earning', p.amount,
        tr('{0}: one ticket every {1} years; the ticket earned on {2:%d/%m/%Y} was not given{3}', p.name, freq, end,
           '' if cash else tr(' (ticket policy – paid in cash at exit)')), 'ticket',
        [tr('Service {0} years ÷ {1} = {2} completed cycles', q2(yrs), freq, cycles), tr('Cycle {0:%d/%m/%Y} – {1:%d/%m/%Y}: no allocation', start, end),
         tr('Policy amount {0:,.2f}', q2(p.amount))])


def asset_items(emp_id, st=None, since=None):
    """Every company asset the employee holds or returned at exit: HR assets (allocations), shared-asset custody, Asset Management
    cards held by the employee – with returned / not returned and HR's decision in the settlement."""
    out, seen = [], set()
    dec = (st.asset_decisions if st is not None else {}) or {}
    AL = M('OrganisationManager', 'AssetAllocation')
    try:
        Prof = M('AssetPlus', 'AssetProfile')
    except LookupError:
        Prof = None
    qs = AL.objects.filter(employee_id=emp_id).select_related('asset', 'asset__asset_type').order_by('assigned_date', 'id')
    for a in qs:
        if a.returned_date and since and a.returned_date < since:
            continue
        key = f'om_{a.asset_id}'
        if not a.returned_date:
            seen.add(a.asset_id)
        code = ''
        if Prof is not None:
            p = Prof.objects.filter(asset_id=a.asset_id).first()
            code = getattr(p, 'asset_code', '') or ''
        lost = False
        if Prof is not None and a.returned_date:
            lost = Prof.objects.filter(asset_id=a.asset_id, extra_status='lost').exists()
        d = dec.get(key) or {}
        out.append({'key': key, 'asset_id': a.asset_id, 'allocation_id': a.pk, 'name': a.asset.name, 'code': code or (a.asset.serial_number or ''),
                    'serial': a.asset.serial_number or '', 'type': str(a.asset.asset_type) if getattr(a.asset, 'asset_type_id', None) else '',
                    'since': a.assigned_date, 'returned_on': a.returned_date, 'condition': a.get_return_condition_display() if a.return_condition else '',
                    'state': 'lost' if lost else ('returned' if a.returned_date else 'not_returned'), 'source': 'hr',
                    'decision': d.get('decision', ''), 'amount': d.get('amount') or 0, 'note': d.get('note', ''), 'by': d.get('by', ''), 'at': d.get('at')})
    try:
        SA = M('AssetPlus', 'SharedAllocation')
        for s_ in SA.objects.filter(custodian_employee_id=emp_id, returned_date__isnull=True):
            a = M('OrganisationManager', 'Asset').objects.filter(pk=s_.asset_id).first()
            key = f'om_{s_.asset_id}'
            d = dec.get(key) or {}
            out.append({'key': key, 'asset_id': s_.asset_id, 'name': (a.name if a else '') + ' – ' + tr('custody of a shared asset'), 'code': getattr(a, 'serial_number', '') or '',
                        'since': s_.assigned_date, 'returned_on': None, 'state': 'not_returned', 'source': 'custody', 'decision': d.get('decision', ''),
                        'amount': d.get('amount') or 0, 'note': d.get('note', ''), 'by': d.get('by', ''), 'at': d.get('at')})
    except LookupError:
        pass
    try:   # Asset Management (app kit): cards held by the employee that are not an HR asset already listed
        Card = M('ZeoAssets', 'AssetCard')
        for c in Card.objects.filter(holder_employee_id=emp_id, hr_asset__isnull=True):   # cards of an HR asset follow the HR allocation above
            key = f'zc_{c.pk}'
            d = dec.get(key) or {}
            out.append({'key': key, 'asset_id': None, 'card_id': c.pk, 'name': getattr(c, 'name', '') or str(c), 'code': getattr(c, 'code', '') or getattr(c, 'number', '') or '',
                        'since': None, 'returned_on': None, 'state': 'not_returned', 'source': 'asset_management', 'decision': d.get('decision', ''),
                        'amount': d.get('amount') or 0, 'note': d.get('note', ''), 'by': d.get('by', ''), 'at': d.get('at')})
    except LookupError:
        pass
    return out


def _decided_assets(st, emp, lines, add):
    """Recovery lines HR recorded for assets not returned, and a block line for Asset Management cards still held (no decision)."""
    dec = st.asset_decisions or {}
    names = {}
    try:
        for i in asset_items(emp.pk, st):
            names[i['key']] = i
    except Exception:
        log.debug('asset items', exc_info=True)
    for key, d in dec.items():
        if (d or {}).get('decision') == 'recover' and d.get('amount'):
            it = names.get(key) or {}
            add(f'asset_{key}', tr('Asset not returned – {0}', it.get('name') or d.get('asset') or key), 'deduction', d['amount'],
                (d.get('note') or tr('recovery decided by HR')), 'assets', by=d.get('by', ''))
    open_cards = [i for i in names.values() if i['source'] == 'asset_management' and i['state'] == 'not_returned' and not i['decision']]
    if open_cards and not any(l['kind'] == 'block' for l in lines):
        lines.append({'key': 'clearance', 'label': tr('Asset clearance not complete – {0}', '; '.join(i['name'] for i in open_cards[:3])), 'kind': 'block',
                      'amount': 0, 'basis': '', 'section': 'assets'})

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

    def add(key, label, kind, amount, basis='', section='', calc=None, **extra):   # v1.62.0 EOS: section + the calculation steps
        amount = q2(amount)
        if amount:
            ln = {'key': key, 'label': label, 'kind': kind, 'amount': float(amount), 'basis': basis, 'section': section or key.split('_')[0]}
            if calc:
                ln['calc'] = calc
            ln.update(extra)
            lines.append(ln)

    def info(key, label, basis='', section=''):   # v1.62.0 EOS
        lines.append({'key': key, 'label': label, 'kind': 'info', 'amount': 0, 'basis': basis, 'section': section or key.split('_')[0]})
    eoc = (st.exit_reason or '') == 'end_of_contract'   # v1.62.0 EOS

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
        info('salary_paid', tr('Salary to the last working day paid by payroll'), tr('payroll paid the salary to {0:%d/%m/%Y}', paid_to), 'salary')   # v1.62.0 EOS
    ua = (inputs_to + timedelta(days=1)) if inputs_to else start
    unpaid = _unpaid_between(emp, ua, lwd)
    if unpaid:
        add('unpaid', tr('Unpaid leave / absence {0:%d/%m/%Y} – {1:%d/%m/%Y} ({2} days)', ua, lwd, q2(unpaid)), 'deduction', daily_wage * unpaid,
            tr('wage {0:,.2f} ÷ 30 × days', wage))
    # 2. notice period
    notice = int(res.notice_period or 0)
    served = (lwd - res.resigned_on).days if res.resigned_on else 0
    unserved = max(notice - max(served, 0), 0)
    if eoc:   # v1.62.0 EOS: the fixed-term contract ends on its date – no notice is owed by either side
        unserved = 0
        info('notice_eoc', tr('End of contract – no notice pay or notice deduction'), tr('the contract ended on {0:%d/%m/%Y}', lwd), 'notice')
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
        add('leave', tr('Unused annual leave {0} days', q2(leave_days)), 'earning', daily_basic * leave_days, tr('basic {0:,.2f} ÷ 30 × days', basic), 'leave',
            [tr('{0:,.2f} ÷ 30 = {1:,.2f} a day', basic, q2(daily_basic)), tr('{0:,.2f} × {1} days = {2:,.2f}', q2(daily_basic), q2(leave_days), q2(daily_basic * leave_days))])   # v1.62.0 EOS
    LE = M('PayrollManagement', 'LeaveEncashment')
    for e in LE.objects.filter(employee=emp, status='approved', payroll_run__isnull=True):
        add(f'encash_{e.pk}', tr('Leave encashment approved, not paid yet ({0} days)', e.encashment_days), 'earning', e.encashment_amount, tr('approved encashment'))
    # 4. end-of-service gratuity
    if eos.gratuity_amount:
        gb, gcalc = _gratuity_steps(eos, res, basic)   # v1.62.0 EOS: the calculation shown step by step
        add('gratuity', tr('End-of-service gratuity ({0} days)', q2(eos.gratuity_days)), 'earning', eos.gratuity_amount,
            gb or tr('basic {0:,.2f} ÷ 30; 21 days a year for the first 5 years, 30 after; capped at 2 years’ basic', basic), 'gratuity', gcalc)
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
    _air_tickets(emp, lwd, res, add, info)   # v1.62.0 EOS: open allocation + the ticket earned on exit by the policy (never both)
    # 6. deductions: loans, salary advances, approved asset recoveries (never the asset value without an approved decision)
    LA = M('PayrollManagement', 'LoanApplication')
    for l in LA.objects.filter(employee=emp, status__in=('Approved', 'Disbursed', 'In Progress', 'Paused')):
        if l.remaining_balance:   # v1.62.0 EOS: each loan with its own basis
            paid = q2(Decimal(str(l.amount_requested or 0)) - Decimal(str(l.remaining_balance or 0)))
            add(f'loan_{l.pk}', tr('Loan {0} – outstanding balance', l.document_number or l.pk), 'deduction', l.remaining_balance,
                tr('loan {0:,.2f}, repaid {1:,.2f}', q2(l.amount_requested), paid), 'loan')
    AS = M('PayrollManagement', 'AdvanceSalaryRequest')
    for a in AS.objects.filter(employee=emp, status__iexact='approved'):
        left = a.requested_amount
        if left:
            add(f'advance_{a.pk}', tr('Salary advance {0} – not yet deducted', a.document_number or a.pk), 'deduction', left,
                tr('approved advance, not deducted by payroll'), 'advance')   # v1.62.0 EOS
    try:
        from AssetPlus import services as AP
        c = AP.clearance(emp.pk)
        due = Decimal(str(c.get('recovery_due') or 0))
        if due:
            add('assets', tr('Asset recovery approved by management'), 'deduction', due, tr('approved damage / loss decisions'))
        open_items = [i for i in (c.get('items') or []) if i.get('kind') != 'recovery']   # the recovery is deducted here
        dec = st.asset_decisions or {}   # v1.62.0 EOS: an asset HR decided on in the settlement (recover / waive / returned) no longer blocks
        open_items = [i for i in open_items if not (i.get('asset_id') and (dec.get(f"om_{i['asset_id']}") or {}).get('decision'))]
        if open_items and not c.get('waived'):
            lines.append({'key': 'clearance', 'label': tr('Asset clearance not complete – {0}', '; '.join(i.get('text', '') for i in open_items[:3])),
                          'kind': 'block', 'amount': 0, 'basis': ''})
    except Exception:
        log.debug('asset clearance not available', exc_info=True)
    _decided_assets(st, emp, lines, add)   # v1.62.0 EOS: recovery amounts HR recorded for assets not returned
    for o in (st.other_lines or []):        # v1.62.0 EOS: HR's own lines are kept on every recalculation
        kind = 'deduction' if o.get('kind') == 'deduction' else 'earning'
        add(f"other_{o.get('id')}", o.get('label') or tr('Other'), kind, o.get('amount') or 0, o.get('note') or '', 'other',
            by=o.get('by') or '', at=o.get('at'), manual=True)
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


def start(eos, user, exit_reason=None):
    S = M('PayrollPlus', 'ExitSettlement')
    st = S.objects.filter(eos_id=eos.pk).first()
    new = st is None
    if st is None:
        st = S(eos_id=eos.pk, employee_id=eos.resignation.employee_id, calc_date=timezone.localdate(), last_working_date=eos.resignation.last_working_date,
               created_by_id=getattr(user, 'pk', None))
        if exit_reason:   # v1.62.0 EOS
            st.exit_reason = exit_reason
        st.history = [{'action': 'calculated', 'by_id': getattr(user, 'pk', None), 'by': person(getattr(user, 'pk', None)),
                       'at': timezone.now().isoformat(), 'note': ''}]
    if st.status == 'draft':
        compute(st, user)
    return st


def log_step(st, action, user, note=''):
    """v1.62.0 EOS – one line of the settlement history. The time of the latest step is kept in last_action_at (so a business date set
    on it later – data loads – is what the history shows); the previous line takes its time when the next step comes."""
    h = list(st.history or [])
    if h:
        h[-1]['at'] = (st.last_action_at or st.created_at or timezone.now()).isoformat()
    now = timezone.now()
    h.append({'action': action, 'by_id': getattr(user, 'pk', None), 'by': person(getattr(user, 'pk', None)), 'at': now.isoformat(), 'note': (note or '')[:500]})
    st.history = h[-60:]
    st.last_action_at = now


FLOW = {'submit': ('draft', 'submitted'), 'validate': ('submitted', 'validated'), 'approve': ('validated', 'approved'), 'pay': ('approved', 'paid'),
        'return': (None, 'draft'), 'cancel': ('draft', 'cancelled')}


def act(st, action, user, codes, admin, data=None):
    data = data or {}
    if action == 'reject':   # v1.62.0 EOS: "Reject back to HR" with a reason = return
        action = 'return'
        if not data.get('note') and data.get('reason'):
            data = {**data, 'note': data.get('reason')}
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
        hn = data.get('note') or ''
        if action == 'pay':
            hn = (tr('Paid on {0:%d/%m/%Y}, reference {1}', st.paid_on, st.payment_ref) + (' – ' + hn if hn else ''))
        log_step(st, action, user, hn)   # v1.62.0 EOS
        st.status = to
        st.save()
    return st


def _close_eos(st, user):
    EOS = M('EmpManagement', 'EndOfService')
    eos = EOS.objects.filter(pk=st.eos_id).first()
    if eos is None:
        return
    _waive_decided(st, user)   # v1.62.0 EOS
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


def _waive_decided(st, user):
    """v1.62.0 EOS – assets HR decided on in the settlement (recovered / written off): the exit clearance of AssetPlus is released with
    that reason, so closing the end of service is not refused for them."""
    dec = {k: d for k, d in (st.asset_decisions or {}).items() if (d or {}).get('decision') in ('recover', 'waive')}
    if not dec:
        return
    try:
        from AssetPlus import services as AP
        from AssetPlus.models import ClearanceWaiver
        c = AP.clearance(st.employee_id)
        if not c.get('blocked'):
            return
        txt = '; '.join(f"{d.get('asset') or k}: {tr('recovered {0:,.2f}', q2(d.get('amount'))) if d.get('decision') == 'recover' else tr('written off')}"
                        for k, d in dec.items())
        ClearanceWaiver.objects.create(employee_id=st.employee_id, reason=(tr('Final settlement {0}', st.number or st.pk) + ' – ' + txt)[:500],
                                       waived_by_id=getattr(user, 'pk', None))
    except Exception:
        log.exception('clearance waiver for the settlement failed')


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
    row = {'id': st.pk, 'number': st.number, 'eos_id': st.eos_id, 'employee_id': st.employee_id, 'employee': f'{e.emp_first_name} {e.emp_last_name or ""}'.strip() if e else '',
           'employee_code': e.emp_code if e else '', 'department': str(e.emp_dept_id) if e and e.emp_dept_id_id else '',
           'designation': str(e.emp_desgntn_id) if e and e.emp_desgntn_id_id else '', 'joined': st.joined, 'last_working_date': st.last_working_date,
           'exit_type': st.exit_type, 'service_days': st.service_days, 'service_years': _eligible_years(st),
           'unpaid_days': float(st.unpaid_days), 'basic': float(st.basic), 'wage': float(st.wage), 'total_earnings': float(st.total_earnings),
           'total_deductions': float(st.total_deductions), 'net': float(st.net), 'status': st.status, 'status_label': st.get_status_display(),
           'due_date': st.due_date, 'overdue': bool(st.due_date and st.status not in ('paid', 'cancelled') and today > st.due_date),
           'days_to_due': (st.due_date - today).days if st.due_date else None, 'paid_on': st.paid_on, 'payment_ref': st.payment_ref,
           'calc_date': st.calc_date, 'notice_waived': st.notice_waived}
    if full:
        row.update({'lines': localize_lines(st.lines),   # v1.62.0 EOS: in the reader's language 'salary_from': st.salary_from, 'salary_to': st.salary_to, 'notes': st.notes,
                    'trail': [{'step': tr('Calculated by HR'), 'by': user_name(st.submitted_by_id), 'at': st.submitted_at},
                              {'step': tr('Validated by Finance'), 'by': user_name(st.validated_by_id), 'at': st.validated_at},
                              {'step': tr('Approved by management'), 'by': user_name(st.approved_by_id), 'at': st.approved_at},
                              {'step': tr('Paid'), 'by': user_name(st.paid_by_id), 'at': st.paid_at}]})
        # v1.62.0 EOS – the statement of the Leavers workspace
        row.update({'exit_reason': st.exit_reason, 'other_lines': st.other_lines or [], 'asset_decisions': st.asset_decisions or {},
                    'history': history_rows(st), 'blocked': any(l.get('kind') == 'block' for l in (st.lines or [])),
                    'net_words': amount_words(st.net), 'currency': 'AED'})
    return row


_PATTERNS = None


def _patterns():
    """(regex, plain template) of every statement text made by tr() in this module and leavers.py (stored in English)."""
    global _PATTERNS
    if _PATTERNS is None:
        import os
        import re
        here = os.path.dirname(os.path.abspath(__file__))
        src = ''.join(open(os.path.join(here, f), encoding='utf-8').read() for f in ('services.py', 'leavers.py'))
        out = []
        for m in re.finditer(r"tr\(\s*'((?:[^'\\]|\\.)*)'", src):
            t = m.group(1)
            if '{' not in t or len(re.sub(r'\{[^}]*\}', '', t)) < 6:
                continue
            plain = re.sub(r'\{(\d+):[^}]*\}', r'{\1}', t)
            rx = '^' + re.sub(r'\\\{(\d+)\\\}', r'(?P<g\1>.+?)', re.escape(plain)) + '$'
            try:
                out.append((re.compile(rx, re.S), plain))
            except re.error:
                pass
        out.sort(key=lambda x: -len(x[1]))
        _PATTERNS = out
    return _PATTERNS


def localize(text, lang=None):
    """v1.62.0 EOS – a statement text stored in English (lines are kept as text) in the reader's language: the template it was made
    from is found again and translated with the same values. Unknown texts stay as they are."""
    try:
        from Translations.i18n import current_lang
        lang = lang or current_lang()
    except Exception:
        return text
    if not text or not isinstance(text, str) or not lang or str(lang).startswith('en'):
        return text
    t = tr(text, lang=lang)
    if t != text:
        return t
    for rx, plain in _patterns():
        m = rx.match(text)
        if m:
            vals = m.groupdict()
            args = [localize(vals.get(f'g{i}', ''), lang) for i in range(len(vals))]
            try:
                return tr(plain, *args, lang=lang)
            except Exception:
                return text
    return text


def localize_lines(lines):
    out = []
    for l in lines or []:
        x = dict(l)
        for k in ('label', 'basis'):
            if x.get(k):
                x[k] = localize(x[k])
        if x.get('calc'):
            x['calc'] = [localize(c) for c in x['calc']]
        out.append(x)
    return out


HISTORY_LABEL = {'calculated': 'End of service calculated', 'recalculated': 'Calculated again', 'submit': 'Submitted to Finance',
                 'validate': 'Checked by Finance', 'approve': 'Approved', 'pay': 'Paid', 'return': 'Sent back to HR', 'cancel': 'Cancelled',
                 'line_added': 'Line added', 'line_removed': 'Line removed', 'asset': 'Asset decision', 'notice_waived': 'Notice waived'}


def history_rows(st):
    """v1.62.0 EOS – the steps of the settlement, oldest first (made from the step fields for settlements of earlier versions)."""
    h = list(st.history or [])
    if not h:
        h = [{'action': 'calculated', 'by': person(st.created_by_id), 'at': st.created_at}]
        for a, uid, at in (('submit', st.submitted_by_id, st.submitted_at), ('validate', st.validated_by_id, st.validated_at),
                           ('approve', st.approved_by_id, st.approved_at), ('pay', st.paid_by_id, st.paid_at)):
            if at:
                h.append({'action': a, 'by': person(uid), 'at': at, 'note': (tr('Paid on {0:%d/%m/%Y}, reference {1}', st.paid_on, st.payment_ref)
                                                                             if a == 'pay' and st.paid_on else '')})
        return [{**x, 'label': tr(HISTORY_LABEL.get(x['action'], x['action']))} for x in h]
    out = []
    for i, x in enumerate(h):
        at = x.get('at')
        if i == len(h) - 1:
            at = st.last_action_at or (st.created_at if i == 0 else at)
        out.append({'action': x.get('action'), 'label': tr(HISTORY_LABEL.get(x.get('action'), x.get('action') or '')), 'by': x.get('by', ''),
                    'at': at, 'note': x.get('note', '')})
    from datetime import datetime as _dt
    def _t(v):
        try:
            return v if isinstance(v, _dt) else _dt.fromisoformat(str(v))
        except (TypeError, ValueError):
            return None
    for i in range(len(out) - 2, -1, -1):   # a step dated later than the next one (records made before their business date was set)
        a, b = _t(out[i]['at']), _t(out[i + 1]['at'])
        if a and b and a > b:
            out[i]['at'] = out[i + 1]['at']
    return out


# ------------------------------------------------------------------ v1.62.0 EOS – amount in words (AED / dirham, fils)
_ONES = ['', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen', 'fifteen',
         'sixteen', 'seventeen', 'eighteen', 'nineteen']
_TENS = ['', '', 'twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']


def _en(n):
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ('-' + _ONES[n % 10] if n % 10 else '')
    if n < 1000:
        return _ONES[n // 100] + ' hundred' + (' and ' + _en(n % 100) if n % 100 else '')
    for div, word in ((10 ** 9, 'billion'), (10 ** 6, 'million'), (1000, 'thousand')):
        if n >= div:
            rest = n % div
            return _en(n // div) + ' ' + word + ((' and ' if rest < 100 else ' ') + _en(rest) if rest else '')
    return str(n)


_AR_ONES = ['', 'واحد', 'اثنان', 'ثلاثة', 'أربعة', 'خمسة', 'ستة', 'سبعة', 'ثمانية', 'تسعة', 'عشرة', 'أحد عشر', 'اثنا عشر', 'ثلاثة عشر', 'أربعة عشر',
            'خمسة عشر', 'ستة عشر', 'سبعة عشر', 'ثمانية عشر', 'تسعة عشر']
_AR_TENS = ['', '', 'عشرون', 'ثلاثون', 'أربعون', 'خمسون', 'ستون', 'سبعون', 'ثمانون', 'تسعون']
_AR_HUNDREDS = ['', 'مائة', 'مائتان', 'ثلاثمائة', 'أربعمائة', 'خمسمائة', 'ستمائة', 'سبعمائة', 'ثمانمائة', 'تسعمائة']


def _ar999(n):
    parts = []
    if n >= 100:
        parts.append(_AR_HUNDREDS[n // 100])
        n %= 100
    if n:
        if n < 20:
            parts.append(_AR_ONES[n])
        else:
            parts.append((_AR_ONES[n % 10] + ' و' if n % 10 else '') + _AR_TENS[n // 10])
    return ' و'.join(parts)


def _ar(n):
    if n == 0:
        return 'صفر'
    out = []
    for div, one, two, few, many in ((10 ** 6, 'مليون', 'مليونان', 'ملايين', 'مليون'), (1000, 'ألف', 'ألفان', 'آلاف', 'ألف')):
        if n >= div:
            k, n = n // div, n % div
            if k == 1:
                out.append(one)
            elif k == 2:
                out.append(two)
            elif 3 <= k <= 10:
                out.append(_ar999(k) + ' ' + few)
            else:
                out.append(_ar999(k) + ' ' + many)
    if n:
        out.append(_ar999(n))
    return ' و'.join(out)


def amount_words(v, lang=None):
    """'Twenty-four thousand eight hundred and twelve dirhams and 23 fils' (Arabic when the reader's language is Arabic)."""
    try:
        from Translations.i18n import current_lang
        lang = lang or current_lang()
    except Exception:
        lang = lang or 'en'
    v = q2(v)
    neg = v < 0
    v = abs(v)
    whole, fils = int(v), int((v - int(v)) * 100)
    if str(lang).startswith('ar'):
        t = _ar(whole) + ' درهم' + (f' و{_ar(fils)} فلس' if fils else '') + ' فقط لا غير'
        return ('سالب ' if neg else '') + t
    t = (_en(whole) or 'zero') + (' dirham' if whole == 1 else ' dirhams') + (f' and {fils} fils' if fils else '') + ' only'
    t = ('minus ' if neg else '') + t
    return t[:1].upper() + t[1:]
