"""v1.68.0 – the logic of the UAE payroll extras (models.py says what each table is).

Everything that pays goes through the existing Payroll › Monthly inputs (PayrollPlus.monthly_inputs.save_grid): the commission, the
service charge share and the retention bonus are written as the figure of a variable salary component for the payroll month, so the
payslip, the WPS file, the payroll journal and the reports carry them as any other monthly input. The components are made the first
time (codes ZCOMM, ZSVC, ZRETB – addition, variable, no formula, marked as monthly input); HR may rename them.
The retention bonus clawback is a deduction line on the employee's final settlement (PayrollPlus.leavers – HR's own lines).
The bulk advance makes ordinary approved advance salary requests with their instalments (PayrollPlus.lending).
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import calendar
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from Translations.i18n import tr

M = apps.get_model
Q2 = Decimal('0.01')
ZERO = Decimal('0.00')
ILOE_LIMIT = Decimal('16000')   # basic salary up to AED 16,000: category A (AED 5 a month); above: category B (AED 10) – dubai.ae, Ius Laboris


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


def q2(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0)).quantize(Q2, ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise Refused(tr('Enter a number.'))


def first_of(v):
    if isinstance(v, date):
        return v.replace(day=1)
    try:
        y, m = str(v)[:7].split('-')
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        raise Refused(tr('Choose the month.'), problems={'month': tr('Required.')})


def month_end(m):
    return m.replace(day=calendar.monthrange(m.year, m.month)[1])


def months_between(a, b):
    """whole months from a to b (b after a)"""
    n = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        n -= 1
    return max(n, 0)


def emp(eid):
    return M('EmpManagement', 'emp_master').objects.select_related('emp_branch_id', 'emp_desgntn_id', 'emp_dept_id').filter(pk=eid).first()


def emp_name(e):
    if e is None:
        return ''
    n = ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x)
    return f'{n} ({e.emp_code})'


def salary_rows(e):
    return list(M('PayrollManagement', 'EmployeeSalaryStructure').objects.filter(employee_id=e.pk, is_active=True).select_related('component'))


def basic_of(e):
    rows = salary_rows(e)
    b = sum((Decimal(str(r.amount or 0)) for r in rows if r.component.payroll_category == 'basic' and r.component.component_type == 'addition'), ZERO)
    return b.quantize(Q2)


def gross_of(e):
    rows = salary_rows(e)
    g = sum((Decimal(str(r.amount or 0)) for r in rows if r.component.component_type == 'addition' and r.component.component_value_type == 'fixed'
             and r.component.show_in_payslip), ZERO)
    return g.quantize(Q2)


# ------------------------------------------------------------------ the monthly input components
COMPONENTS = {'ZCOMM': ('Commission', 'commission'), 'ZSVC': ('Service charge share', 'other_allowance'), 'ZRETB': ('Retention bonus', 'bonus')}


def component(code):
    """the (all-branch) variable component that carries the figure – made and marked as a monthly input the first time"""
    SC = M('PayrollManagement', 'SalaryComponent')
    code = (code or '').strip().upper()
    c = SC.objects.filter(code__iexact=code, branch__isnull=True).first() or SC.objects.filter(code__iexact=code).first()
    if c is None:
        name, cat = COMPONENTS.get(code, (code, 'other_allowance'))
        c = SC.objects.create(name=name, code=code, branch=None, component_type='addition', payroll_category=cat, component_value_type='variable',
                              description=f'v1.68.0 {name} – entered by Payroll › Payroll extras', show_in_payslip=True)
    from PayrollPlus import monthly_inputs as MI
    if not MI.component_flag(c)['monthly_input']:
        MI.save_flag(c, True)
    return c


@transaction.atomic
def send_amounts(month, amounts, user=None):
    """amounts: {(employee id, CODE): amount} → Payroll › Monthly inputs of the month (all or nothing)."""
    from PayrollPlus import monthly_inputs as MI
    for code in {k[1] for k in amounts}:
        component(code)
    by_branch = {}
    emps = {e.pk: e for e in M('EmpManagement', 'emp_master').objects.filter(pk__in={k[0] for k in amounts})}
    for (eid, code), amt in amounts.items():
        e = emps.get(eid)
        if e is None:
            raise Refused(tr('Employee not found.'))
        row = by_branch.setdefault(e.emp_branch_id_id, {}).setdefault(eid, {'employee': eid, 'values': {}})
        row['values'][code] = str(q2(row['values'].get(code, 0)) + q2(amt))
    out = {'saved': 0, 'changed': 0}
    for br, rows in by_branch.items():
        try:
            r = MI.save_grid(month, br, list(rows.values()), None, user)
        except MI.Refused as ex:
            raise Refused(ex.message, status=ex.status, problems=ex.problems)
        out['saved'] += r['saved']
        out['changed'] += r['changed']
    return out


# ------------------------------------------------------------------ 29 commission
def commission_of(plan, sales, target=None):
    """(commission, breakdown) for the sales of a month under the plan's tiers"""
    sales = q2(sales)
    tiers = sorted(plan.tiers or [], key=lambda t: (t.get('upto') is None, Decimal(str(t.get('upto') or 0))))
    if not tiers or sales <= 0:
        return ZERO, []
    if plan.basis == 'target':
        if not target or q2(target) <= 0:
            raise Refused(tr('Enter the target of the month – this plan pays on the % of the target reached.'), problems={'target': tr('Required.')})
        measure = (sales * 100 / q2(target)).quantize(Q2)
    else:
        measure = sales
    out, br = ZERO, []
    if plan.mode == 'slab' and plan.basis == 'sales':
        low = ZERO
        for t in tiers:
            hi = Decimal(str(t['upto'])) if t.get('upto') is not None else None
            part = (min(sales, hi) if hi is not None else sales) - low
            if part <= 0:
                break
            amt = (part * Decimal(str(t.get('rate') or 0)) / 100).quantize(Q2, ROUND_HALF_UP)
            br.append({'from': float(low), 'to': float(hi) if hi is not None else None, 'rate': float(t.get('rate') or 0), 'amount': float(amt)})
            out += amt
            if hi is None or sales <= hi:
                break
            low = hi
    else:
        hit = next((t for t in tiers if t.get('upto') is None or measure <= Decimal(str(t['upto']))), tiers[-1])
        if plan.mode == 'fixed':
            out = q2(hit.get('amount') or 0)
        else:
            out = (sales * Decimal(str(hit.get('rate') or 0)) / 100).quantize(Q2, ROUND_HALF_UP)
        br.append({'measure': float(measure), 'tier': hit, 'amount': float(out)})
    if plan.cap is not None and out > q2(plan.cap):
        br.append({'capped_at': float(plan.cap)})
        out = q2(plan.cap)
    return out, br


def check_tiers(tiers, mode, basis):
    if not isinstance(tiers, list) or not tiers:
        raise Refused(tr('Enter at least one tier.'), problems={'tiers': tr('Required.')})
    last = None
    for i, t in enumerate(tiers):
        up = t.get('upto')
        if up not in (None, ''):
            up = q2(up)
            if last is not None and up <= last:
                raise Refused(tr('The tiers must go up: each "up to" above the one before.'), problems={'tiers': tr('Invalid.')})
            last = up
        elif i != len(tiers) - 1:
            raise Refused(tr('Only the last tier can be without an upper limit.'), problems={'tiers': tr('Invalid.')})
        if mode == 'fixed':
            if q2(t.get('amount')) < 0:
                raise Refused(tr('Enter the amount of each tier.'), problems={'tiers': tr('Invalid.')})
        else:
            r = q2(t.get('rate'))
            if r < 0 or r > 100:
                raise Refused(tr('A rate is between 0 and 100 %.'), problems={'tiers': tr('Invalid.')})
    if mode == 'slab' and basis == 'target':
        raise Refused(tr('Slabs are for a plan on the sales amount – for a target plan choose the rate of the slab reached.'), problems={'mode': tr('Invalid.')})
    return [{'upto': (float(q2(t['upto'])) if t.get('upto') not in (None, '') else None), **({'amount': float(q2(t.get('amount')))} if mode == 'fixed'
             else {'rate': float(q2(t.get('rate')))})} for t in tiers]


# ------------------------------------------------------------------ 31 retention bonus and its clawback
def clawback_of(rb, last_day):
    """what the employee repays when leaving on last_day (0 when the retention period is over)"""
    if rb.status not in ('sent', 'paid') or rb.clawback_months <= 0:
        return ZERO, 0
    paid_on = month_end(rb.pay_month)
    if last_day <= paid_on:
        served = 0
    else:
        served = months_between(paid_on, last_day)
    if served >= rb.clawback_months:
        return ZERO, served
    if rb.clawback_basis == 'full':
        return q2(rb.amount), served
    return (q2(rb.amount) * (rb.clawback_months - served) / rb.clawback_months).quantize(Q2, ROUND_HALF_UP), served


def add_clawbacks(settlement, user=None):
    """the clawbacks of the leaver's retention bonuses as deduction lines of the draft settlement (once each)"""
    RB = M('PayrollExtras', 'RetentionBonus')
    if settlement.status != 'draft':
        return 0
    lines = list(settlement.other_lines or [])
    have = {str(x.get('ref')) for x in lines if x.get('ref')}
    n = 0
    for rb in RB.objects.filter(employee_id=settlement.employee_id, status__in=('sent', 'paid')):
        ref = f'retention:{rb.pk}'
        if ref in have:
            continue
        amt, served = clawback_of(rb, settlement.last_working_date)
        if amt <= 0:
            continue
        nid = max([int(x.get('id') or 0) for x in lines] + [0]) + 1
        lines.append({'id': nid, 'kind': 'deduction', 'label': tr('Retention bonus repaid (awarded {0})', rb.award_date.strftime('%d/%m/%Y'))[:120],
                      'amount': float(amt), 'note': tr('{0} of {1} months served after the payment', served, rb.clawback_months),
                      'by_id': getattr(user, 'pk', None), 'by': 'Payroll extras', 'at': timezone.now().isoformat(), 'ref': ref, 'account': ''})
        rb.status, rb.settlement_id, rb.clawed_amount = 'clawed', settlement.pk, amt
        rb.history = (rb.history or []) + [{'action': 'Clawback on the final settlement', 'amount': str(amt), 'at': timezone.now().isoformat(),
                                            'settlement': settlement.number or settlement.pk}]
        rb.save(update_fields=['status', 'settlement_id', 'clawed_amount', 'history'])
        n += 1
    if n:
        settlement.other_lines = lines
        settlement.save(update_fields=['other_lines'])
        try:
            from PayrollPlus import services as S
            S.compute(settlement, user)
        except Exception:   # noqa: BLE001 – the line is kept; the next "Calculate" includes it
            pass
    return n


# ------------------------------------------------------------------ 32 service charge
def pool_people(pool):
    E = M('EmpManagement', 'emp_master')
    m0, m1 = pool.month, month_end(pool.month)
    qs = E.objects.filter(emp_branch_id_id=pool.branch_id).select_related('emp_desgntn_id')
    out = []
    for e in qs:
        joined = getattr(e, 'emp_joined_date', None)
        if joined and joined > m1:
            continue
        if not e.is_active:
            continue
        if e.pk in set(pool.excluded or []):
            continue
        out.append(e)
    return out


def days_worked(e, m0, m1):
    try:
        A = M('calendars', 'Attendance')
        n = A.objects.filter(employee=e, date__range=(m0, m1)).exclude(check_in_time__isnull=True).values('date').distinct().count()
        if n:
            return Decimal(n)
    except Exception:   # noqa: BLE001
        pass
    joined = getattr(e, 'emp_joined_date', None)
    start = max(m0, joined) if joined else m0
    return Decimal((m1 - start).days + 1)


def preview_pool(pool):
    people = pool_people(pool)
    if not people:
        raise Refused(tr('No active employee in this branch for the month.'))
    pts = pool.points or {}
    w = {}
    for e in people:
        if pool.method == 'equal':
            w[e.pk] = Decimal(1)
        elif pool.method == 'days':
            w[e.pk] = days_worked(e, pool.month, month_end(pool.month))
        else:
            v = pts.get(f'employee:{e.pk}', pts.get(f'designation:{e.emp_desgntn_id_id}', 1))
            w[e.pk] = q2(v)
    tot = sum(w.values(), Decimal(0))
    if tot <= 0:
        raise Refused(tr('Nobody has points or days – give at least one employee a share.'))
    amt = q2(pool.amount)
    shares = []
    for e in people:
        shares.append({'employee_id': e.pk, 'name': emp_name(e), 'weight': float(w[e.pk]), 'amount': (amt * w[e.pk] / tot).quantize(Q2, ROUND_HALF_UP)})
    diff = amt - sum((s['amount'] for s in shares), ZERO)
    if diff and shares:   # the rounding cent goes to the biggest share, so the shares add up to the pool exactly
        big = max(shares, key=lambda s: s['amount'])
        big['amount'] += diff
    for s in shares:
        s['amount'] = float(s['amount'])
    pool.shares = shares
    return shares


# ------------------------------------------------------------------ 33 hourly / daily pay: formula variables
GCC = {'SA': ('Saudi Arabia', 'SAUDI'), 'KW': ('Kuwait', 'KUWAIT'), 'OM': ('Oman', 'OMAN'), 'BH': ('Bahrain', 'BAHRAIN'), 'QA': ('Qatar', 'QATAR')}
# seed (switched off until HR checks them with GPSSA): Oman 7.5 % / 11 % from 1 Jan 2024 – Vialto Partners and Sovereign Group (two
# sources); the others from Sovereign Group (July 2026) only – one source, so they are marked "to confirm"
GCC_SEED = {'SA': (11.75, 9.75, 'basic_housing', 'Sovereign Group, GCC nationals pension contributions (Jul 2026) – confirm with GPSSA'),
            'KW': (7.5, 11, 'gross', 'Sovereign Group (Jul 2026) – confirm with GPSSA'),
            'OM': (7.5, 11, 'gross', 'GPSSA change from 1 Jan 2024 (Vialto Partners; Sovereign Group Jul 2026) – confirm the wage base with GPSSA'),
            'BH': (7, 15, 'gross', 'Sovereign Group (Jul 2026) – confirm with GPSSA'),
            'QA': (7, 14, 'gross', 'Sovereign Group (Jul 2026) – confirm with GPSSA')}


def gcc_seed():
    R = M('PayrollExtras', 'GCCPensionRate')
    for c, (emp_r, er_r, base, src) in GCC_SEED.items():
        R.objects.get_or_create(country=c, defaults={'name': GCC[c][0], 'employee_rate': emp_r, 'employer_rate': er_r, 'base': base, 'source': src,
                                                    'effective_from': date(2024, 1, 1) if c == 'OM' else None})
    return list(R.objects.all())


def gcc_country(e):
    """SA / KW / OM / BH / QA from the nationality as a whole word (ROMANIAN is not OMANI)"""
    import re
    n = str(getattr(e, 'emp_nationality', '') or '').upper()
    for c, (_name, word) in GCC.items():
        if re.search(r'\b' + word + r'(I|IS|IAN)?\b', n):
            return c
    return None


def gcc_vars(e, rows=None):
    z = {'gcc_pension_base': ZERO, 'gcc_pension_emp': ZERO, 'gcc_pension_er': ZERO}
    c = gcc_country(e)
    if c is None:
        return z
    r = M('PayrollExtras', 'GCCPensionRate').objects.filter(country=c, active=True).first()
    if r is None:
        return z
    rows = salary_rows(e) if rows is None else rows
    def cat(r_, cats):
        return r_.component.component_type == 'addition' and r_.component.payroll_category in cats
    if r.base == 'basic':
        base = sum((q2(x.amount) for x in rows if cat(x, ('basic',))), ZERO)
    elif r.base == 'basic_housing':
        base = sum((q2(x.amount) for x in rows if cat(x, ('basic', 'hra', 'housing_allowance'))), ZERO)
    else:
        base = sum((q2(x.amount) for x in rows if x.component.component_type == 'addition' and x.component.component_value_type == 'fixed'
                    and x.component.show_in_payslip), ZERO)
    if r.max_base_aed is not None and base > q2(r.max_base_aed):
        base = q2(r.max_base_aed)
    return {'gcc_pension_base': base, 'gcc_pension_emp': (base * Decimal(str(r.employee_rate)) / 100).quantize(Q2, ROUND_HALF_UP),
            'gcc_pension_er': (base * Decimal(str(r.employer_rate)) / 100).quantize(Q2, ROUND_HALF_UP)}


def formula_vars(employee):
    """hourly_rate, daily_rate and is_hourly / is_daily for the payroll formulas (0 for a monthly paid employee), and the GCC pension
    (gcc_pension_emp / gcc_pension_er / gcc_pension_base) of a GCC national whose country is switched on"""
    try:   # in a savepoint – a company not migrated yet must not spoil the payroll's transaction
        with transaction.atomic():
            g = gcc_vars(employee)
    except Exception:   # noqa: BLE001
        g = {'gcc_pension_base': ZERO, 'gcc_pension_emp': ZERO, 'gcc_pension_er': ZERO}
    try:
        with transaction.atomic():
            pb = M('PayrollExtras', 'PayBasis').objects.filter(employee_id=employee.pk).first()
    except Exception:   # noqa: BLE001
        pb = None
    if pb is None:
        return {'hourly_rate': ZERO, 'daily_rate': ZERO, 'is_hourly': Decimal(0), 'is_daily': Decimal(0), **g}
    return {'hourly_rate': q2(pb.hourly_rate), 'daily_rate': q2(pb.daily_rate), 'is_hourly': Decimal(1 if pb.basis == 'hourly' else 0),
            'is_daily': Decimal(1 if pb.basis == 'daily' else 0), **g}


# ------------------------------------------------------------------ 35 ILOE
def iloe_row(e, rec, today=None):
    today = today or _zt_today()
    basic = basic_of(e)
    cat = (rec.category if rec and rec.category else ('A' if basic <= ILOE_LIMIT else 'B'))
    status = rec.status if rec else 'not_subscribed'
    flag = ''
    if status == 'not_subscribed':
        flag = 'missing'
    elif status == 'subscribed' and rec.expiry_date:
        if rec.expiry_date < today:
            flag = 'expired'
        elif (rec.expiry_date - today).days <= 30:
            flag = 'expiring'
    return {'employee_id': e.pk, 'employee': emp_name(e), 'branch': getattr(e.emp_branch_id, 'branch_name', '') if e.emp_branch_id_id else '',
            'basic': float(basic), 'category': cat, 'premium': 5 if cat == 'A' else 10, 'status': status, 'exempt_reason': rec.exempt_reason if rec else '',
            'policy_no': rec.policy_no if rec else '', 'start_date': rec.start_date.isoformat() if rec and rec.start_date else '',
            'expiry_date': rec.expiry_date.isoformat() if rec and rec.expiry_date else '', 'paid_by': rec.paid_by if rec else 'employee',
            'payment': rec.payment if rec else '', 'note': rec.note if rec else '', 'flag': flag}


# ------------------------------------------------------------------ 36 Emiratisation and Nafis
def is_uae_national(e):
    try:
        from PayrollPlus.statutory import is_national
        return bool(is_national(e))
    except Exception:   # noqa: BLE001
        n = str(getattr(e, 'emp_nationality', '') or '').lower()
        return 'emirat' in n or n in ('uae', 'ae')


def quota(employees=None):
    S = M('PayrollExtras', 'EmiratisationSettings').objects.order_by('id').first() or M('PayrollExtras', 'EmiratisationSettings')()
    P = {p.employee_id: p for p in M('PayrollExtras', 'EmiratisationProfile').objects.all()}
    levels = {int(x) for x in str(S.skilled_levels or '').replace(' ', '').split(',') if x.isdigit()}
    E = M('EmpManagement', 'emp_master')
    emps = list(employees) if employees is not None else list(E.objects.filter(is_active=True).select_related('emp_branch_id'))
    total = len(emps)
    skilled, unset, nat_sk, nat_all, nafis, support = [], [], [], [], [], []
    for e in emps:
        p = P.get(e.pk)
        lvl = p.skill_level if p else 0
        nat = is_uae_national(e)
        if nat:
            nat_all.append(e)
            if p and p.nafis_registered:
                nafis.append(e)
            if p and p.salary_support:
                support.append(e)
        if lvl == 0:
            unset.append(e)
            continue
        if lvl in levels:
            skilled.append(e)
            if nat and (p is None or p.counts_for_quota):
                nat_sk.append(e)
    target = Decimal(str(S.target_pct or 0))
    need = math.ceil(len(skilled) * target / 100) if target > 0 else 0
    if 20 <= total < 50 and S.min_nationals:
        need = max(need, int(S.min_nationals))
    gap = max(need - len(nat_sk), 0)
    fine_m = q2(S.fine_per_head_month) * gap
    return {'employees': total, 'skilled': len(skilled), 'skill_not_set': len(unset), 'nationals': len(nat_all), 'nationals_skilled': len(nat_sk),
            'target_pct': float(target), 'required': need, 'gap': gap, 'fine_per_head_month': float(q2(S.fine_per_head_month)),
            'exposure_month': float(fine_m), 'exposure_year': float(fine_m * 12), 'period_label': S.period_label,
            'deadline': S.deadline.isoformat() if S.deadline else '', 'nafis_registered': len(nafis), 'salary_support': len(support),
            'share_pct': float((Decimal(len(nat_sk)) * 100 / len(skilled)).quantize(Q2)) if skilled else 0.0, 'min_nationals': S.min_nationals,
            'skilled_levels': S.skilled_levels, 'note': S.note}


# ------------------------------------------------------------------ 37 bulk advance
def advance_amount(ba, e):
    if ba.mode == 'fixed':
        a = q2(ba.value)
    elif ba.mode == 'gross_pct':
        a = (gross_of(e) * q2(ba.value) / 100).quantize(Q2, ROUND_HALF_UP)
    else:
        a = (basic_of(e) * q2(ba.value) / 100).quantize(Q2, ROUND_HALF_UP)
    if ba.max_amount is not None and a > q2(ba.max_amount):
        a = q2(ba.max_amount)
    return a


def preview_bulk(ba):
    E = M('EmpManagement', 'emp_master')
    AR = M('PayrollManagement', 'AdvanceSalaryRequest')
    ids = [int(x) for x in (ba.employee_ids or []) if str(x).isdigit()]
    if not ids:
        raise Refused(tr('Choose the employees.'), problems={'employee_ids': tr('Required.')})
    lines = []
    open_ = set(AR.objects.filter(employee_id__in=ids, status__in=('Pending', 'Approved', 'Paused')).values_list('employee_id', flat=True))
    leaving = set()
    try:
        from PayrollPlus.lending import leaving as _leaving
        for e in E.objects.filter(pk__in=ids):
            if _leaving(e):
                leaving.add(e.pk)
    except Exception:   # noqa: BLE001
        pass
    for e in E.objects.filter(pk__in=ids).select_related('emp_branch_id'):
        a = advance_amount(ba, e)
        skip = ''
        if not e.is_active:
            skip = tr('Not active.')
        elif e.pk in leaving:
            skip = tr('Leaving – no advance.')
        elif e.pk in open_:
            skip = tr('Has an open advance.')
        elif a <= 0:
            skip = tr('No salary to work out the amount from.')
        lines.append({'employee_id': e.pk, 'name': emp_name(e), 'amount': float(a), 'instalment': float((a / max(ba.instalments, 1)).quantize(Q2)),
                      'skipped': skip, 'advance_id': None, 'number': ''})
    return sorted(lines, key=lambda x: x['name'])


@transaction.atomic
def create_bulk(ba, user):
    if ba.status != 'draft':
        raise Refused(tr('These advances are already made.'), status=409)
    AR, AP = M('PayrollManagement', 'AdvanceSalaryRequest'), M('PayrollManagement', 'AdvanceSalaryApproval')
    DN = M('OrganisationManager', 'DocumentNumbering')
    T = M('PayrollPlus', 'AdvanceTerms')
    lines = preview_bulk(ba)
    made = 0
    for ln in lines:
        if ln['skipped']:
            continue
        e = emp(ln['employee_id'])
        cfg = DN.objects.filter(branch_id=e.emp_branch_id_id, type='advance_salary_request').first()
        number = cfg.next_unused(AR) if cfg else None
        # v1.68.0: made without the request workflow – HR approved it here, so the branch workflow (reporting manager …) is not started and
        # nobody is asked to approve an advance that is already approved; one line failing does not stop the others
        try:
            with transaction.atomic():
                adv = AR.objects.bulk_create([AR(employee=e, branch_id=e.emp_branch_id_id, requested_amount=q2(ln['amount']), reason=ba.name[:200],
                                                 status='Approved', remarks=tr('Bulk advance "{0}" – approved by {1}', ba.name, getattr(user, 'username', '')),
                                                 created_by=user, document_number=number)])[0]
                if adv.pk is None:
                    adv = AR.objects.filter(employee=e, reason=ba.name[:200], document_number=number).order_by('-pk').first()
                AP.objects.create(request=adv, approver=user, role='Bulk advance (HR)', level=1, status='Approved', employee=e)
                T.objects.update_or_create(advance_id=adv.pk, defaults={'instalments': max(int(ba.instalments or 1), 1), 'requested_amount': q2(ln['amount']),
                                                                        'approved_amount': q2(ln['amount']), 'approved_by_id': getattr(user, 'pk', None),
                                                                        'approved_note': f'Bulk advance {ba.name}'[:255]})
        except Exception as ex:   # noqa: BLE001
            ln['skipped'] = tr('Not given: {0}', '; '.join(getattr(ex, 'messages', None) or [str(ex)])[:200])
            continue
        ln['advance_id'], ln['number'] = adv.pk, number or f'#{adv.pk}'
        made += 1
    if not made:
        raise Refused(tr('Nobody gets an advance – see the reasons on each line.'))
    ba.lines, ba.status = lines, 'created'
    ba.save(update_fields=['lines', 'status'])
    return made


# ------------------------------------------------------------------ 40 cost to company
def ctc(e):
    """monthly and yearly cost of the employee: the fixed salary, the employer statutory share, the gratuity, leave salary and air ticket
    accruals, and what the payslips of the last 12 months actually cost"""
    rows = salary_rows(e)
    fixed = [(r.component.name, q2(r.amount)) for r in rows if r.component.component_type == 'addition' and r.component.component_value_type == 'fixed'
             and r.component.show_in_payslip]
    gross = sum((a for _n, a in fixed), ZERO)
    basic = basic_of(e)
    out = {'employee': emp_name(e), 'salary': [{'label': n, 'month': float(a), 'year': float(a * 12)} for n, a in fixed], 'gross': float(gross)}
    extra = []
    try:   # employer statutory contributions (GPSSA, ILOE paid by the company …)
        from PayrollPlus.statutory import active_schemes, compute
        for s in active_schemes():
            _b, _emp, er = compute(s, e)
            if er:
                extra.append({'label': tr('{0} (employer)', s.name), 'month': float(er), 'year': float(er * 12)})
    except Exception:   # noqa: BLE001
        pass
    # gratuity accrual (UAE: 21 days' basic a year for the first 5 years, 30 days after) and leave salary (30 days' basic a year)
    joined = getattr(e, 'emp_joined_date', None)
    yrs = Decimal(months_between(joined, _zt_today()) / 12) if joined else Decimal(0)
    if not is_uae_national(e):
        days = Decimal(30) if yrs >= 5 else Decimal(21)
        g = (basic * days / 30 / 12).quantize(Q2, ROUND_HALF_UP)
        extra.append({'label': tr('Gratuity accrual ({0} days a year)', int(days)), 'month': float(g), 'year': float(g * 12)})
    lv = (basic * 30 / 30 / 12).quantize(Q2, ROUND_HALF_UP)
    extra.append({'label': tr('Leave salary accrual (30 days a year)'), 'month': float(lv), 'year': float(lv * 12)})
    out['employer'] = extra
    total = gross + sum((Decimal(str(x['month'])) for x in extra), ZERO)
    out['total_month'], out['total_year'] = float(total), float(total * 12)
    # actual: the payslips of the last 12 months
    PS = M('PayrollManagement', 'Payslip')
    today = _zt_today()
    since = date(today.year - 1, today.month, 1)
    act_g = act_n = ZERO
    n = 0
    for p in PS.objects.filter(employee_id=e.pk).exclude(status__iexact='rejected').select_related('payroll_run'):
        pr = p.payroll_run
        if pr and date(pr.year, pr.month, 1) >= since:
            act_g += q2(getattr(p, 'gross_salary', 0) or 0)
            act_n += q2(p.net_salary or 0)
            n += 1
    out['actual'] = {'payslips': n, 'gross': float(act_g), 'net': float(act_n), 'since': since.isoformat()}
    return out
