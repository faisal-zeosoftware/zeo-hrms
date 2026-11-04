"""v1.66.0 (items 118–123, decision 261) – the air ticket rules that were missing after the air ticket audit (B9–B14).

Every rule is a company setting or a policy setting and starts at today's behaviour: a company that saves nothing gets the same
allocations, requests, payroll payouts, provisions and final settlement lines as in 1.62.3 / 1.62.11. No new table (no migration):

  Company settings      ZeoApps.CompanyOption 'v1660_air_ticket_settings'      Air tickets › Setup › Air ticket settings
  Policy settings       ZeoApps.CompanyOption 'v1660_air_policy:<policy id>'   (one per AirTicketPolicy)
  Fare table            ZeoApps.CompanyOption 'v1660_air_fares'                (route / destination country, class, adult / child fare, valid from)
  Travellers of a trip  ZeoApps.CompanyOption 'v1660_air_travel:<request id>'  (partial use: who of the family travels on a ticket request)
  Policy change         ZeoApps.CompanyOption 'v1660_air_cycle:<emp>:<policy>' (category change: the new policy's cycle starts on the change day)

item 118  provision posted to accounts (post_provision): the month-end provisions journal carries the air ticket (Dr air ticket expense,
          Cr air ticket provision, per employee, the change since the last posting – ZeoAccounting.ProvisionBalance kind 'air_ticket'); a
          cash payout paid by payroll and the air ticket lines of a final settlement are taken from the provision first, the rest is expense.
item 119  rules the policy screen shows and the engine ignored: probation ("Can be used during probation"), rule types (one-way / return
          ticket / cash after N years of service), travel class (the fare of that class), "Pay in the next payroll" of a cash rule.
item 120  family tickets (employee + spouse + children with an age limit and a number limit), fares by route / class, partial use.
item 121  monthly accrual of the ticket value (pro-rata from the cycle start, unpaid days left out, to the exit day), lapse of an unused
          entitlement after N months; decision 261: earlier unused cycles paid at exit (carry-forward), ticket-only policy paid in cash at exit
          or not, a ticket-only policy that accepts a cash request.
item 122  request checks: a second request for the same cycle, leavers (approved resignation / termination, last working day), ticket or
          cash as the policy allows.
item 123  policy overlap: two active policies for the same employee – the first (today), the most specific wins, or saving the second
          policy is refused; a category change closes the old policy pro-rata and opens the new one (no second full allocation).
"""
import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.apps import apps

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
Q2 = Decimal('0.01')
KEY = 'v1660_air_ticket_settings'
POL_KEY = 'v1660_air_policy:'
FARES_KEY = 'v1660_air_fares'
TRAVEL_KEY = 'v1660_air_travel:'
CYCLE_KEY = 'v1660_air_cycle:'
USED_REQ = ('APPROVED', 'PROCESSED')
LIVE_REQ = ('PENDING', 'APPROVED', 'PROCESSED')

# ------------------------------------------------------------------ settings
CHOICES = {'probation': ('ignore', 'enforce'), 'rule_types': ('ignore', 'enforce'), 'next_payroll': ('ignore', 'enforce'),
           'overlap': ('first', 'specific', 'refuse'), 'category_change': ('off', 'split')}
DEFAULTS = {
    'post_provision': False,           # 118: air ticket in the posted provisions journal; payroll / settlement paid from the provision first
    'expense_account': '6160',         # 118: air ticket expense (an Accounting › Account mapping 'provision / air_ticket' wins)
    'provision_account': '2330',       # 118: air ticket provision
    'probation': 'ignore',             # 119: enforce = "Can be used during probation" off → no allocation, accrual or request in probation
    'rule_types': 'ignore',            # 119: enforce = one-way / return / cash rules by years of service decide the ticket and the cash request
    'next_payroll': 'ignore',          # 119: enforce = a cash payout is paid by payroll only when its cash rule has "Pay in the next payroll"
    'check_duplicate_cycle': False,    # 122: a second request of the same policy cycle is refused
    'check_leavers': False,            # 122: no request after an approved resignation / termination or after the last working day
    'check_type': False,               # 122: ticket or cash as the policy allows (cash only / ticket only / both)
    'overlap': 'first',                # 123: first (today: every matching policy allocates; the settlement takes the first) | specific | refuse
    'category_change': 'off',          # 123: split = a change of policy closes the old one pro-rata and opens the new one
}
POLICY_CHOICES = {'fare_source': ('policy', 'fares'), 'accrual': ('off', 'monthly'), 'unused': ('keep', 'lapse')}
POLICY_DEFAULTS = {
    'fare_source': 'policy',           # 120: policy (the policy amount, today) | fares (the fare table of the route / class)
    'route': '',                       # 120: fare table route of the policy ('' = the policy country)
    'family': False,                   # 120: tickets for the family too
    'spouse': True,                    # 120: … the spouse
    'max_children': 0,                 # 120: … up to N children
    'child_max_age': 18,               # 120: … younger than this age on the day
    'child_fare_age': 12,              # 120: a child younger than this travels on the child fare
    'partial_use': False,              # 120: one of the family may travel; what the others did not use stays on the allocation
    'one_way_percent': 50,             # 119: a one-way ticket is this % of the return fare
    'accrual': 'off',                  # 121: monthly = the ticket value accrues month by month (provision, pro-rata at exit)
    'unused': 'keep',                  # 121: keep (today – stays usable) | lapse (expires after lapse_months / when the next cycle starts)
    'lapse_months': 0,                 # 121: months after the allocation an unused entitlement lapses (0 = when the next cycle starts)
    'exit_carry_forward': False,       # 261: at exit every unused earlier cycle is paid, not only the latest
    'ticket_only_cash_at_exit': True,  # 261: a ticket-only policy is paid in cash at exit (today) – off: shown, not paid
    'ticket_only_accepts_cash': False, # 261: with the type check on, a ticket-only policy still accepts a cash request
}
SPOUSE_WORDS = ('wife', 'husband', 'spouse', 'زوج', 'زوجة')
CHILD_WORDS = ('son', 'daughter', 'child', 'kid', 'ابن', 'ابنة', 'بنت')


def D(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0))
    except Exception:
        return Decimal('0')


def q2(v):
    return D(v).quantize(Q2, rounding=ROUND_HALF_UP)


def _day(d):
    if d is None:
        return None
    if isinstance(d, str):
        try:
            return date.fromisoformat(d[:10])
        except ValueError:
            return None
    return d.date() if hasattr(d, 'date') and callable(d.date) else d


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def add_months(d, n):
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _opt(key, default=None):
    o = M('ZeoApps', 'CompanyOption').objects.filter(key=key).first()
    return o.value if o is not None and o.value is not None else default


def _put(key, value):
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=key, defaults={'value': value})


def _drop(key):
    M('ZeoApps', 'CompanyOption').objects.filter(key=key).delete()


class Refused(Exception):
    def __init__(self, message, problems=None):
        super().__init__(message)
        self.message, self.problems = message, problems


def settings():
    cur = dict(DEFAULTS)
    v = _opt(KEY, {})
    if isinstance(v, dict):
        cur.update({k: v[k] for k in DEFAULTS if k in v})
    return cur


def save_settings(data, user=None):
    cur = settings()
    errs = {}
    for k, dv in DEFAULTS.items():
        if k not in data:
            continue
        v = data[k]
        if k in CHOICES:
            if v not in CHOICES[k]:
                errs[k] = tr('Choose one of the options.')
                continue
        elif isinstance(dv, bool):
            v = bool(v)
        else:
            v = str(v or '').strip()[:40]
        cur[k] = v
    if errs:
        raise Refused(tr('Check the marked fields.'), errs)
    _put(KEY, {**cur, 'by': getattr(user, 'username', '') or ''})
    return settings()


def policy_rules(pid):
    cur = dict(POLICY_DEFAULTS)
    v = _opt(f'{POL_KEY}{pid}', {}) if pid else {}
    if isinstance(v, dict):
        cur.update({k: v[k] for k in POLICY_DEFAULTS if k in v})
    return cur


def save_policy_rules(pid, data, user=None):
    P = M('PayrollManagement', 'AirTicketPolicy')
    if not P.objects.filter(pk=pid).exists():
        raise Refused(tr('Air ticket policy not found.'))
    cur = policy_rules(pid)
    errs = {}
    for k, dv in POLICY_DEFAULTS.items():
        if k not in data:
            continue
        v = data[k]
        if k in POLICY_CHOICES:
            if v not in POLICY_CHOICES[k]:
                errs[k] = tr('Choose one of the options.')
                continue
        elif isinstance(dv, bool):
            v = bool(v)
        elif isinstance(dv, int):
            try:
                v = int(v or 0)
            except (TypeError, ValueError):
                errs[k] = tr('Enter a whole number.')
                continue
            lim = {'max_children': (0, 12), 'child_max_age': (0, 30), 'child_fare_age': (0, 30), 'one_way_percent': (1, 100),
                   'lapse_months': (0, 120)}.get(k, (0, 999))
            if not lim[0] <= v <= lim[1]:
                errs[k] = tr('Enter a number from {0} to {1}.', lim[0], lim[1])
                continue
        else:
            v = str(v or '').strip()[:60]
        cur[k] = v
    if errs:
        raise Refused(tr('Check the marked fields.'), errs)
    _put(f'{POL_KEY}{pid}', {**cur, 'by': getattr(user, 'username', '') or ''})
    return policy_rules(pid)


# ------------------------------------------------------------------ fare table (item 120)
def fares():
    v = _opt(FARES_KEY, {})
    rows = v.get('rows', []) if isinstance(v, dict) else []
    return sorted(rows, key=lambda r: (str(r.get('route') or ''), r.get('country_id') or 0, r.get('travel_class') or '', r.get('valid_from') or ''))


def save_fare(data, user=None):
    rows = fares()
    errs = {}
    route = str(data.get('route') or '').strip().upper()[:40]
    cid = data.get('country_id') or None
    try:
        cid = int(cid) if cid else None
    except (TypeError, ValueError):
        cid = None
    if not route and not cid:
        errs['route'] = tr('Enter the route or choose the destination country.')
    tc = data.get('travel_class') or 'ECONOMY'
    if tc not in ('ECONOMY', 'BUSINESS', 'FIRST'):
        errs['travel_class'] = tr('Choose one of the options.')
    adult, child = D(data.get('adult_fare')), D(data.get('child_fare'))
    if adult <= 0:
        errs['adult_fare'] = tr('Enter an amount above zero.')
    if child < 0:
        errs['child_fare'] = tr('Enter a number of zero or more.')
    vf = _day(data.get('valid_from'))
    if vf is None:
        errs['valid_from'] = tr('Enter a valid date.')
    if errs:
        raise Refused(tr('Check the marked fields.'), errs)
    fid = int(data.get('id') or 0)
    for r in rows:
        if r.get('id') != fid and (r.get('route') or '') == route and (r.get('country_id') or None) == cid \
                and r.get('travel_class') == tc and r.get('valid_from') == vf.isoformat():
            raise Refused(tr('A fare of this route and class from this date already exists.'), {'valid_from': tr('Already used.')})
    row = {'id': fid or (max([r.get('id') or 0 for r in rows] or [0]) + 1), 'route': route, 'country_id': cid, 'travel_class': tc,
           'adult_fare': str(q2(adult)), 'child_fare': str(q2(child)), 'valid_from': vf.isoformat(),
           'note': str(data.get('note') or '')[:120], 'by': getattr(user, 'username', '') or ''}
    rows = [r for r in rows if r.get('id') != row['id']] + [row]
    _put(FARES_KEY, {'rows': rows})
    return row


def delete_fare(fid):
    rows = fares()
    if not any(r.get('id') == fid for r in rows):
        raise Refused(tr('Fare not found.'))
    _put(FARES_KEY, {'rows': [r for r in rows if r.get('id') != fid]})


def fare_for(policy, day):
    """The fare table row of the policy route (else its country) and class valid on the day (latest valid_from on or before it)."""
    pr = policy_rules(policy.pk)
    route = (pr.get('route') or '').strip().upper()
    best = None
    for r in fares():
        if route:
            if (r.get('route') or '') != route:
                continue
        elif (r.get('country_id') or None) != policy.country_id or r.get('route'):
            continue
        if r.get('travel_class') != (policy.travel_class or 'ECONOMY'):
            continue
        vf = _day(r.get('valid_from'))
        if vf is None or vf > day:
            continue
        if best is None or vf > _day(best['valid_from']):
            best = r
    return best


# ------------------------------------------------------------------ probation (item 119)
def probation_end(emp):
    """The day probation ends: confirmed on (Employment info), else its probation end date, else the confirmation date of the employee.
    None = no probation recorded."""
    try:
        ei = M('EmployeeProfile', 'EmploymentInfo').objects.filter(employee_id=emp.pk).first()
    except LookupError:
        ei = None
    if ei is not None and (ei.confirmed_on or ei.probation_end_date):
        return ei.confirmed_on or ei.probation_end_date
    return getattr(emp, 'emp_date_of_confirmation', None)


def probation_blocks(emp, policy, day):
    """True when the company applies probation, the policy is not for probation and the employee is still in probation on the day."""
    if settings()['probation'] != 'enforce' or getattr(policy, 'allowed_in_probation', False):
        return False
    pe = probation_end(emp)
    return bool(pe and day < pe)


def accrual_anchor(emp, policy):
    """The first day the policy counts for the employee: the joining day, or the probation end (probation applied, policy not for
    probation), or the day of a policy change (item 123)."""
    start = emp.emp_joined_date
    if settings()['probation'] == 'enforce' and not policy.allowed_in_probation:
        pe = probation_end(emp)
        if pe and start and pe > start:
            start = pe
    ch = _opt(f'{CYCLE_KEY}{emp.pk}:{policy.pk}')
    if isinstance(ch, dict) and _day(ch.get('start')):
        start = max(start or _day(ch['start']), _day(ch['start']))
    return start


# ------------------------------------------------------------------ policy of the employee (item 123)
def _limits(p):
    return {rel: set(getattr(p, rel).values_list('id', flat=True))
            for rel in ('eligible_departments', 'eligible_designations', 'eligible_categories', 'branch')}


def matching_policies(emp):
    from . import air_rules as AR
    countries = AR.policy_countries(emp)
    P = M('PayrollManagement', 'AirTicketPolicy')
    out = []
    for p in P.objects.filter(is_active=True, country_id__in=countries).prefetch_related(
            'rules', 'eligible_departments', 'eligible_designations', 'eligible_categories', 'branch').order_by('id'):
        if AR.policy_matches(p, emp):
            out.append(p)
    return out


def specificity(p):
    return sum(1 for v in _limits(p).values() if v)


def policy_for(emp, pols=None):
    """The one policy of the employee: 'first' = the first matching (today, as the settlement took it); 'specific' / 'refuse' = the
    policy with the most limits (department / designation / category / branch) – a tie goes to the older policy."""
    pols = matching_policies(emp) if pols is None else pols
    if not pols:
        return None
    if settings()['overlap'] == 'first':
        return pols[0]
    return sorted(pols, key=lambda p: (-specificity(p), p.pk))[0]


def policies_to_allocate(emp, pols):
    """The policies the allocation job gives the employee: every matching one (today) or only the one that wins."""
    if settings()['overlap'] == 'first':
        return pols
    p = policy_for(emp, pols)
    return [p] if p else []


def _overlaps(a, b):
    """Two policy limit sets {dimension: ids} apply to a same employee: on every dimension one is open (all) or they share an id."""
    for k in a:
        if a[k] and b[k] and not (a[k] & b[k]):
            return False
    return True


def overlap_list():
    """Active policies that can apply to the same employee (same country, the limits cross)."""
    P = M('PayrollManagement', 'AirTicketPolicy')
    pols = list(P.objects.filter(is_active=True).prefetch_related('eligible_departments', 'eligible_designations', 'eligible_categories', 'branch')
                .order_by('id'))
    lim = {p.pk: _limits(p) for p in pols}
    out = []
    for i, a in enumerate(pols):
        for b in pols[i + 1:]:
            if a.country_id == b.country_id and _overlaps(lim[a.pk], lim[b.pk]):
                win = a if (specificity(a), -a.pk) >= (specificity(b), -b.pk) else b
                out.append({'a': a.pk, 'a_name': a.name, 'b': b.pk, 'b_name': b.name, 'winner': win.pk, 'winner_name': win.name})
    return out


def overlap_problem(data, exclude_pk=None):
    """Saving a policy with 'refuse': an active policy of the same country whose limits cross it is refused (EN / AR message)."""
    if settings()['overlap'] != 'refuse' or not data.get('is_active', True):
        return None
    country = data.get('country')
    cid = getattr(country, 'pk', country)
    if not cid:
        return None

    def ids(v):
        return {getattr(x, 'pk', x) for x in (v or [])}
    mine = {'eligible_departments': ids(data.get('eligible_departments')), 'eligible_designations': ids(data.get('eligible_designations')),
            'eligible_categories': ids(data.get('eligible_categories')), 'branch': ids(data.get('branch'))}
    P = M('PayrollManagement', 'AirTicketPolicy')
    for p in P.objects.filter(is_active=True, country_id=cid).exclude(pk=exclude_pk or 0).order_by('id'):
        if _overlaps(mine, _limits(p)):
            return tr('This policy applies to the same employees as the active policy "{0}" (same country, the departments, designations, '
                      'categories and branches cross). Limit one of them or make one inactive.', p.name)
    return None


# ------------------------------------------------------------------ the ticket value (items 119, 120)
def _age(dob, day):
    if not dob:
        return None
    return day.year - dob.year - ((day.month, day.day) < (dob.month, dob.day))


def family(emp, policy, day):
    """The travellers the policy covers on the day: the employee, the spouse (if the policy takes the spouse), and up to N children younger
    than the age limit (oldest first). From the employee's family records (EmpManagement emp_family, active)."""
    pr = policy_rules(policy.pk)
    out = [{'key': 'emp', 'name': ' '.join(x for x in (emp.emp_first_name, emp.emp_last_name) if x) or emp.emp_code,
            'relation': 'employee', 'age': None, 'fare': 'adult'}]
    if not pr['family']:
        return out
    F = M('EmpManagement', 'emp_family')
    kids = []
    spouse_done = False
    for f in F.objects.filter(emp_id=emp, is_active=True).order_by('ef_date_of_birth', 'pk'):
        rel = str(f.emp_relation or '').strip().lower()
        age = _age(f.ef_date_of_birth, day)
        if any(w in rel for w in SPOUSE_WORDS):
            if pr['spouse'] and not spouse_done:
                out.append({'key': f'fam:{f.pk}', 'name': f.ef_member_name, 'relation': 'spouse', 'age': age, 'fare': 'adult'})
                spouse_done = True
        elif any(w in rel for w in CHILD_WORDS):
            if age is not None and age < int(pr['child_max_age']):
                kids.append({'key': f'fam:{f.pk}', 'name': f.ef_member_name, 'relation': 'child', 'age': age,
                             'fare': 'child' if age < int(pr['child_fare_age']) else 'adult'})
    out += kids[:int(pr['max_children'])]
    return out


def applicable_rule(policy, emp, day, kinds):
    """The rule of the kinds with the most years of service the employee has on the day (None = none met)."""
    from EmpManagement.utils import service_years
    if not emp.emp_joined_date:
        return None
    yrs = service_years(emp.emp_joined_date, day)
    rs = [r for r in policy.rules.all() if r.rule_type in kinds and r.required_service_years <= yrs]
    return sorted(rs, key=lambda r: (r.required_service_years, r.pk))[-1] if rs else None


def value(emp, policy, day, keys=None):
    """(amount, lines) – the ticket value of the policy for the employee on the day, or of the travellers `keys` (partial use).
    Defaults: the policy amount (today). Fare table: the fare of the route / class valid on the day, child fare under the age.
    Rule types applied: a one-way ticket (the rule met is one-way) is the one-way % of the return fare."""
    pr = policy_rules(policy.pk)
    st = settings()
    lines = []
    fare = fare_for(policy, day) if pr['fare_source'] == 'fares' else None
    if pr['fare_source'] == 'fares' and fare is None:
        lines.append(tr('No fare for this route and class on {0:%d/%m/%Y} – the policy amount is used', day))
    adult = D(fare['adult_fare']) if fare else D(policy.amount)
    child = (D(fare['child_fare']) or adult) if fare else adult
    if fare:
        where = fare.get('route') or (policy.country.country_name if policy.country_id else '')
        lines.append(tr('Fare {0} {1} from {2}: adult {3:,.2f}, child {4:,.2f}', where, tr(policy.get_travel_class_display()),
                        fare['valid_from'], adult, child))
    share = Decimal('1')
    if st['rule_types'] == 'enforce':
        r = applicable_rule(policy, emp, day, ('ONE_WAY', 'TWO_WAY'))
        if r is not None and r.rule_type == 'ONE_WAY':
            share = D(pr['one_way_percent']) / Decimal('100')
            lines.append(tr('One-way ticket: {0} % of the return fare', pr['one_way_percent']))
    trav = family(emp, policy, day)
    if keys is not None:
        trav = [t for t in trav if t['key'] in set(keys)]
    total = ZERO
    for t in trav:
        f = (child if t['fare'] == 'child' else adult) * share
        t['amount'] = str(q2(f))
        total += f
    if pr['family'] or keys is not None:
        lines.append(tr('{0} travellers: {1}', len(trav), ', '.join(f"{t['name']} ({tr(t['relation'])}{', ' + str(t['age']) if t['age'] is not None else ''})" for t in trav)))
    return q2(total), lines, trav


# ------------------------------------------------------------------ accrual (item 121)
def running_cycle(emp, policy, day):
    """(start, end) of the cycle running on the day: from the last allocation of the policy (the job allocates every N years after it),
    else from the anchor (joining / probation end / policy change)."""
    A = M('PayrollManagement', 'AirTicketAllocation')
    freq = max(int(policy.frequency_years or 1), 1)
    anchor = accrual_anchor(emp, policy)
    last = (A.objects.filter(employee=emp, policy=policy, allocated_date__lte=day).exclude(status='REJECTED')
            .order_by('-allocated_date', '-pk').first())
    from_alloc = last is not None and (anchor is None or last.allocated_date >= anchor)
    start = last.allocated_date if from_alloc else anchor
    if start is None:
        return None, None
    end = add_years(start, freq)
    while end <= day:   # a completed cycle without a new allocation (paid as "earned on exit" / allocated by the job): the next one runs
        start, end = end, add_years(end, freq)
    return start, end


def accrued(emp, policy, day, stop=None):
    """(amount, lines) – the ticket value accrued in the running cycle up to the day (or the exit day `stop`): value ÷ (12 × years of the
    cycle) a month, a part month by its days, unpaid days (unpaid leave, absence) left out. Only for a policy with monthly accrual."""
    if policy_rules(policy.pk)['accrual'] != 'monthly':
        return ZERO, []
    end_day = min(day, stop) if stop else day
    start, cend = running_cycle(emp, policy, end_day)
    if start is None or start > end_day:
        return ZERO, []
    freq = max(int(policy.frequency_years or 1), 1)
    val, vlines, _ = value(emp, policy, end_day)
    monthly = val / Decimal(12 * freq)
    from .services import _unpaid_between
    months = Decimal('0')
    d = start
    while d <= end_day:
        me = d.replace(day=calendar.monthrange(d.year, d.month)[1])
        stop_m = min(me, end_day)
        days_in = Decimal(calendar.monthrange(d.year, d.month)[1])
        n = Decimal((stop_m - d).days + 1)
        unpaid = min(D(_unpaid_between(emp, d, stop_m)), n)
        months += (n - unpaid) / days_in
        d = me + timedelta(days=1)
    amt = q2(monthly * months)
    unpaid_all = D(_unpaid_between(emp, start, end_day))
    lines = [tr('Cycle {0:%d/%m/%Y} – {1:%d/%m/%Y}: {2} months accrued of {3}', start, cend, months.quantize(Decimal('0.01')), 12 * freq),
             tr('Monthly accrual {0:,.2f} (value {1:,.2f} ÷ {2} months)', q2(monthly), val, 12 * freq)] + vlines
    if unpaid_all:
        lines.append(tr('Unpaid days left out: {0}', unpaid_all.quantize(Decimal('0.01'))))
    return amt, lines


# ------------------------------------------------------------------ partial use (item 120)
def is_partial(policy_id):
    pr = policy_rules(policy_id)
    return bool(pr['family'] and pr['partial_use'])


def travel(req_id):
    v = _opt(f'{TRAVEL_KEY}{req_id}')
    return v if isinstance(v, dict) else None


def travelled_keys(allocation, exclude_req=None):
    R = M('PayrollManagement', 'AirTicketRequest')
    keys = set()
    for r in R.objects.filter(allocation=allocation, request_type='TICKET', status__in=LIVE_REQ).exclude(pk=exclude_req or 0):
        t = travel(r.pk)
        if t:
            keys.update(t.get('travellers') or [])
    return keys


def set_travel(req, keys, user=None):
    """Record who travels on a ticket request (partial use). Only a pending ticket request of a partial-use
    policy; the travellers must be covered by the policy and not have travelled on this allocation yet; their fares must fit what is left."""
    a = req.allocation
    if req.request_type != 'TICKET':
        raise Refused(tr('Travellers are recorded on a ticket request only.'))
    if a is None or not is_partial(a.policy_id):
        raise Refused(tr('The policy of this allocation does not allow partial use (Air ticket settings › Policy rules).'))
    cur = travel(req.pk) or {}
    if req.status != 'PENDING' or cur.get('applied'):   # recorded before the approval (an approved ticket without travellers used all of it)
        raise Refused(tr('The travellers of this request can no longer be changed.'))
    keys = [str(k) for k in (keys or [])]
    if not keys:
        raise Refused(tr('Choose who travels.'), {'travellers': tr('Choose who travels.')})
    day = _day(req.departure_date or req.request_date) or date.today()
    covered = {t['key']: t for t in family(req.employee, a.policy, day)}
    bad = [k for k in keys if k not in covered]
    if bad:
        raise Refused(tr('Not covered by the policy on {0:%d/%m/%Y}.', day), {'travellers': tr('Not covered by the policy on {0:%d/%m/%Y}.', day)})
    done = travelled_keys(a, exclude_req=req.pk) & set(keys)
    if done:
        raise Refused(tr('Already travelled on this allocation: {0}.', ', '.join(covered[k]['name'] for k in done)))
    amt, lines, trav = value(req.employee, a.policy, day, keys)
    left = D(a.remaining_amount if a.remaining_amount is not None else a.amount)
    if amt > left:
        raise Refused(tr('The fares of these travellers ({0:,.2f}) are more than what is left on the allocation ({1:,.2f}).', amt, left))
    rec = {'travellers': keys, 'names': [t['name'] for t in trav], 'amount': str(amt), 'applied': False,
           'by': getattr(user, 'username', '') or ''}
    _put(f'{TRAVEL_KEY}{req.pk}', rec)
    return travel(req.pk)


def on_request_status(req):
    """A ticket request with travellers: approved → their fares are taken off the allocation (the rest stays for the others);
    rejected after that → given back."""
    t = travel(req.pk)
    if not t or req.allocation_id is None:
        return
    A = M('PayrollManagement', 'AirTicketAllocation')
    a = A.objects.filter(pk=req.allocation_id).first()
    if a is None:
        return
    amt = D(t.get('amount'))
    left = D(a.remaining_amount if a.remaining_amount is not None else a.amount)
    if req.status in USED_REQ and not t.get('applied'):
        a.remaining_amount = q2(max(left - amt, ZERO))
        a.save(update_fields=['remaining_amount'])
        _put(f'{TRAVEL_KEY}{req.pk}', {**t, 'applied': True})
    elif req.status == 'REJECTED' and t.get('applied'):
        a.remaining_amount = q2(min(left + amt, D(a.amount)))
        a.save(update_fields=['remaining_amount'])
        _put(f'{TRAVEL_KEY}{req.pk}', {**t, 'applied': False})


def partial_taken(a):
    """Partial-use policy: the allocation's ticket is taken when nothing is left, or a ticket was approved without travellers (all of
    them, as before), or a cash payout was approved."""
    R = M('PayrollManagement', 'AirTicketRequest')
    if D(a.remaining_amount if a.remaining_amount is not None else a.amount) <= 0:
        return True
    for r in R.objects.filter(allocation=a, status__in=USED_REQ):
        if r.request_type != 'TICKET' or not travel(r.pk):
            return True
    return False


def taken_ids(legacy_taken):
    """The allocations whose ticket is taken: the 1.62.2 set (an approved / processed ticket request), less the partial-use allocations
    that still have travellers left."""
    if not legacy_taken:
        return set(legacy_taken)
    A = M('PayrollManagement', 'AirTicketAllocation')
    out = set(legacy_taken)
    for a in A.objects.filter(pk__in=list(legacy_taken)):
        if is_partial(a.policy_id) and not partial_taken(a):
            out.discard(a.pk)
    return out


def partial_request_ok(allocation, others, request_type):
    """air_rules.request_problem: on a partial-use allocation a further ticket request (or a cash payout of what is left) is fine while
    every other live request is a ticket with its travellers recorded and something is left."""
    if request_type not in ('TICKET', 'ENCASHMENT') or not is_partial(allocation.policy_id):
        return False
    if D(allocation.remaining_amount if allocation.remaining_amount is not None else allocation.amount) <= 0:
        return False
    return all(o.request_type == 'TICKET' and travel(o.pk) for o in others)


# ------------------------------------------------------------------ request checks (items 119, 122)
def request_problem(emp, allocation, request_type, request_date, departure_date=None, exclude_pk=None):
    """The 1.66.0 checks of a ticket / cash request (each one a company setting, off = today). None = fine."""
    st = settings()
    if allocation is None or emp is None:
        return None
    p = allocation.policy
    pr = policy_rules(p.pk)
    day = _day(request_date) or date.today()
    if probation_blocks(emp, p, day):   # 119
        return tr('The employee is in probation until {0:%d/%m/%Y} and the policy is not for probation.', probation_end(emp))
    if st['check_leavers']:   # 122
        from PayrollManagement.signals import last_working_day
        lwd = last_working_day(emp)
        if lwd is not None:   # an approved resignation / termination (the last working day ahead or passed)
            return tr('The employee is leaving (last working day {0:%d/%m/%Y}) – the air ticket is settled in the final settlement.', lwd)
        from . import air_rules as AR
        if AR.employee_left(emp, day):
            return tr('The employee has left – no new air ticket request.')
    if st['check_type']:   # 122 / 261
        if p.allowance_type == 'CASH' and request_type == 'TICKET':
            return tr('The policy "{0}" pays cash only – a ticket cannot be requested.', p.name)
        if p.allowance_type == 'TICKET' and request_type == 'ENCASHMENT' and not pr['ticket_only_accepts_cash']:
            return tr('The policy "{0}" gives a ticket only – cash cannot be requested.', p.name)
    if st['rule_types'] == 'enforce':   # 119
        kinds = ('ENCASHMENT',) if request_type == 'ENCASHMENT' else ('ONE_WAY', 'TWO_WAY')
        has_kind = [r for r in p.rules.all() if r.rule_type in kinds]
        if has_kind and applicable_rule(p, emp, day, kinds) is None:
            need = min(r.required_service_years for r in has_kind)
            return (tr('Cash instead of the ticket needs {0} years of service.', need) if request_type == 'ENCASHMENT'
                    else tr('The ticket needs {0} years of service.', need))
    if st['check_duplicate_cycle']:   # 122
        R = M('PayrollManagement', 'AirTicketRequest')
        freq = max(int(p.frequency_years or 1), 1)
        a0 = allocation.allocated_date
        for r in R.objects.filter(employee=emp, allocation__policy=p, status__in=LIVE_REQ).exclude(pk=exclude_pk or 0).exclude(
                allocation=allocation).select_related('allocation'):
            a1 = r.allocation.allocated_date
            if a0 and a1 and (a1 <= a0 < add_years(a1, freq) or a0 <= a1 < add_years(a0, freq)):
                return tr('A request of the same cycle already exists: {0}.', r.document_number or f'#{r.pk}')
    return None


def payout_problem(ticket):
    """119 'next payroll': with the rule applied, a cash payout is paid by payroll only when its cash rule has "Pay in the next payroll";
    otherwise HR pays it outside payroll (Air ticket settings › paid outside payroll). None = payroll pays it."""
    if settings()['next_payroll'] != 'enforce':
        return None
    a = getattr(ticket, 'allocation', None)
    if a is None:
        return None
    rs = [r for r in a.policy.rules.all() if r.rule_type == 'ENCASHMENT']
    if not rs:
        return None
    r = applicable_rule(a.policy, ticket.employee, _day(ticket.request_date) or date.today(), ('ENCASHMENT',)) or rs[0]
    return None if r.apply_in_next_payroll else 'paid outside payroll (cash rule without "Pay in the next payroll")'


def pay_outside(req, ref, user=None):
    """HR paid an approved cash payout outside payroll: the request is processed, the allocation used up (noted for the provision)."""
    if req.request_type != 'ENCASHMENT' or req.status != 'APPROVED':
        raise Refused(tr('Only an approved cash payout can be marked as paid outside payroll.'))
    a = req.allocation
    amt = D(a.remaining_amount if a.remaining_amount is not None else a.amount)
    a.remaining_amount = ZERO
    a.save(update_fields=['remaining_amount'])
    req.status = 'PROCESSED'
    req.notes = ((req.notes or '') + f"\nPaid outside payroll {date.today():%d/%m/%Y}: {ref or '-'} ({amt:,.2f})").strip()
    req.save(update_fields=['status', 'notes'])
    return amt


# ------------------------------------------------------------------ allocation job (items 119, 121, 123)
def allocation_amount(emp, policy, day):
    amt, _, _ = value(emp, policy, day)
    return amt


def allocation_expiry(policy, allocated):
    pr = policy_rules(policy.pk)
    if pr['unused'] != 'lapse':
        return None
    n = int(pr['lapse_months'] or 0)
    if n > 0:
        return add_months(allocated, n) - timedelta(days=1)
    return add_years(allocated, max(int(policy.frequency_years or 1), 1)) - timedelta(days=1)


def apply_lapse(today):
    """Open allocations of a lapsing policy without an expiry get one (allocation date + N months, or the day before the next cycle);
    returns how many were set."""
    A = M('PayrollManagement', 'AirTicketAllocation')
    n = 0
    for a in A.objects.filter(is_active=True, expiry_date__isnull=True).exclude(status='REJECTED').select_related('policy'):
        if policy_rules(a.policy_id)['unused'] != 'lapse' or not a.allocated_date:
            continue
        x = allocation_expiry(a.policy, a.allocated_date)
        if x:
            A.objects.filter(pk=a.pk).update(expiry_date=x)
            n += 1
    return n


def allocation_due(emp, policy, today):
    """1.62.3 rule (frequency since the last allocation of the policy) + a policy change (item 123): the new policy's first allocation
    comes a full cycle after the change day."""
    from . import air_rules as AR
    ch = _opt(f'{CYCLE_KEY}{emp.pk}:{policy.pk}')
    A = M('PayrollManagement', 'AirTicketAllocation')
    if isinstance(ch, dict) and _day(ch.get('start')) and not A.objects.filter(employee=emp, policy=policy,
                                                                               allocated_date__gt=_day(ch['start'])).exists():
        return add_years(_day(ch['start']), max(int(policy.frequency_years or 1), 1)) <= today
    return AR.allocation_due(emp, policy, today)


def category_change(emp, new_policy, today):
    """Item 123 (category_change 'split'): the employee's last allocation is of another policy that no longer applies, and its cycle is
    still running → the old policy is closed pro-rata (an allocation of the earned part of the running cycle, pending for HR) and the new
    policy's cycle starts today (its first allocation a full cycle later). Returns the closing allocation, or None (no change)."""
    if settings()['category_change'] != 'split':
        return None
    A = M('PayrollManagement', 'AirTicketAllocation')
    if A.objects.filter(employee=emp, policy=new_policy).exists() or _opt(f'{CYCLE_KEY}{emp.pk}:{new_policy.pk}'):
        return None
    last = (A.objects.filter(employee=emp).exclude(policy=new_policy).exclude(status='REJECTED').select_related('policy')
            .order_by('-allocated_date', '-pk').first())
    if last is None or not last.allocated_date:
        return None
    old = last.policy
    freq = max(int(old.frequency_years or 1), 1)
    cend = add_years(last.allocated_date, freq)
    if cend <= today:
        return None   # the old cycle is over – the new policy starts normally
    if old.is_active and old.pk in {p.pk for p in policies_to_allocate(emp, matching_policies(emp))}:
        return None   # the old policy still applies (overlap with 'first', or it still wins) – not a change
    days = Decimal((cend - last.allocated_date).days)
    served = Decimal((today - last.allocated_date).days)
    amt = q2(allocation_amount(emp, old, today) * served / days) if days else ZERO
    a = A.objects.create(employee=emp, policy=old, amount=amt, remaining_amount=amt, status='PENDING', is_active=True,
                         expiry_date=allocation_expiry(old, today))
    A.objects.filter(pk=a.pk).update(allocated_date=today)
    _put(f'{CYCLE_KEY}{emp.pk}:{new_policy.pk}', {'start': today.isoformat(), 'from_policy': old.pk, 'closed_allocation': a.pk,
                                                  'closed_amount': str(amt), 'cycle': [last.allocated_date.isoformat(), cend.isoformat()]})
    log.info('Air ticket: %s policy %s → %s on %s, old cycle closed pro-rata %s', emp.emp_code, old.pk, new_policy.pk, today, amt)
    return a


# ------------------------------------------------------------------ provisions (items 118, 121)
def liabilities(as_of):
    """{employee id: (amount, basis)} – open entitlements (1.62.2 rule: active, not rejected, not expired, remaining, ticket not taken –
    partial use: travellers left) + the value accrued in the running cycle for policies with monthly accrual."""
    from collections import defaultdict
    AT = M('PayrollManagement', 'AirTicketAllocation')
    R = M('PayrollManagement', 'AirTicketRequest')
    from .air_tickets import is_open
    taken = taken_ids(set(R.objects.filter(status__in=USED_REQ, request_type='TICKET').values_list('allocation_id', flat=True)))
    agg, basis = defaultdict(Decimal), defaultdict(list)
    for a in AT.objects.filter(is_active=True).exclude(status__iexact='REJECTED'):
        if not is_open(a, as_of) or a.pk in taken:   # as in 1.62.2 (an allocation made after the month end still counts – open point)
            continue
        agg[a.employee_id] += Decimal(str(a.remaining_amount or 0))
        basis[a.employee_id].append('open entitlement')
    acc_pols = [pid for pid in M('PayrollManagement', 'AirTicketPolicy').objects.filter(is_active=True).values_list('pk', flat=True)
                if policy_rules(pid)['accrual'] == 'monthly']
    if acc_pols:
        from . import air_rules as AR
        E = M('EmpManagement', 'emp_master')
        for e in E.objects.filter(is_active=True, emp_joined_date__lte=as_of).select_related('emp_branch_id', 'emp_nationality'):
            pols = [p for p in policies_to_allocate(e, matching_policies(e)) if p.pk in acc_pols]
            if not pols or AR.employee_left(e, as_of) and _lwd_before(e, as_of):
                continue
            for p in pols:
                amt, _ = accrued(e, p, as_of)
                if amt:
                    agg[e.pk] += amt
                    basis[e.pk].append(f'accrued {amt:,.2f}')
    return {k: (v.quantize(Q2), ', '.join(dict.fromkeys(basis[k]))[:255]) for k, v in agg.items() if v}


def _lwd_before(e, as_of):
    from PayrollManagement.signals import last_working_day
    lwd = last_working_day(e)
    return bool(lwd and lwd < as_of)


def provision_report(as_of):
    """Rows per employee: open entitlements, accrued, liability, posted balance (ZeoAccounting.ProvisionBalance 'air_ticket'), to post."""
    PB = M('ZeoAccounting', 'ProvisionBalance')
    posted = {p.employee_id: p.amount for p in PB.objects.filter(kind='air_ticket')}
    li = liabilities(as_of)
    E = M('EmpManagement', 'emp_master')
    ids = set(li) | {k for k, v in posted.items() if v}
    emps = {e.pk: e for e in E.objects.filter(pk__in=ids).select_related('emp_branch_id', 'emp_dept_id')}
    rows = []
    for eid in ids:
        e = emps.get(eid)
        if e is None:
            continue
        amt, basis = li.get(eid, (ZERO, 'no longer due'))
        pb = q2(posted.get(eid, 0))
        rows.append({'employee_id': eid, 'code': e.emp_code, 'name': ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x),
                     'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'liability': str(amt), 'basis': basis,
                     'posted': str(pb), 'to_post': str(q2(amt - pb))})
    rows.sort(key=lambda r: r['code'] or '')
    tot = {k: str(q2(sum((D(r[k]) for r in rows), ZERO))) for k in ('liability', 'posted', 'to_post')}
    return {'as_of': as_of, 'rows': rows, 'totals': tot, 'post_provision': settings()['post_provision']}


def accounts():
    st = settings()
    return st['expense_account'] or '6160', st['provision_account'] or '2330'


def payslip_ticket_cash(payslip):
    """The cash payouts of air tickets paid on a payslip (from the 1.62.2 note of what the payslip used up)."""
    v = _opt(f'v1622_payslip_consumed:{payslip.pk}', {}) or {}
    tot = ZERO
    for t in (v.get('tickets') or []):
        tot += D(t.get('remaining'))
    return q2(tot)


# ------------------------------------------------------------------ final settlement (items 121, 261)
def exit_rules(open_by_policy, policies):
    """{policy id: rules} for the settlement – the policy rules of the policies with open allocations or the ticket due."""
    return {pid: policy_rules(pid) for pid in set(open_by_policy) | set(policies)}


def employee_check(emp, day):
    """Everything the rules say for one employee on a day (the screen's "Check an employee")."""
    pols = matching_policies(emp)
    win = policy_for(emp, pols)
    out = {'employee_id': emp.pk, 'code': emp.emp_code, 'day': day, 'joined': emp.emp_joined_date, 'probation_end': probation_end(emp),
           'matching': [{'id': p.pk, 'name': p.name, 'limits': specificity(p)} for p in pols], 'policy': None}
    if win is None:
        return out
    amt, lines, trav = value(emp, win, day)
    acc, acc_lines = accrued(emp, win, day)
    acc_lines = [x for x in acc_lines if x not in lines]   # the value lines are shown once
    cs, ce = running_cycle(emp, win, day)
    from . import air_rules as AR
    out['policy'] = {'id': win.pk, 'name': win.name, 'allowance_type': win.allowance_type, 'travel_class': win.travel_class,
                     'frequency_years': win.frequency_years, 'rules': policy_rules(win.pk), 'value': str(amt), 'value_lines': lines,
                     'travellers': trav, 'accrued': str(acc), 'accrued_lines': acc_lines, 'cycle': [cs, ce],
                     'in_probation': probation_blocks(emp, win, day), 'allocation_due': allocation_due(emp, win, day),
                     'left': AR.employee_left(emp, day)}
    return out
