# v1.67.9 (screens) item 19: dates in texts dd/mm/yyyy
"""v1.62.11 (item 229) – the pension / end-of-service scheme of each branch (UAE):

    pension  'gpssa' (default)  UAE nationals: GPSSA (federal law)
             'adpf'             UAE nationals employed in Abu Dhabi: Abu Dhabi Pension Fund instead of GPSSA
    dews     False (default)    expatriates: UAE Labour Law gratuity at the end of service
             True               a DIFC branch: DIFC Employee Workplace Savings – a monthly employer contribution (statutory scheme
                                'dews') replaces the gratuity accrual from the DEWS start date (1 Feb 2020 or the branch's own date)

Stored in ZeoApps.CompanyOption 'branch_pension_schemes' {branch id: {pension, dews, dews_start}} – no migration. Nothing changes for a
branch until HR sets it (an Abu Dhabi branch is only suggested ADPF on the screen). Saving a branch on ADPF / DEWS makes the statutory
schemes 'adpf' / 'dews' (Payroll › Statutory schemes), their salary components (ST_ADPF deduction, STE_ADPF / STE_DEWS employer
contributions, hidden from the payslip) and gives every active employee of the branch these components (amount by formula).
"""
import time
from datetime import date

from django.apps import apps
from django.db import connection

from Translations.i18n import tr

M = apps.get_model
KEY = 'branch_pension_schemes'
GCC_WORDS = ('SAUDI', 'KUWAIT', 'BAHRAIN', 'OMAN', 'QATAR')
_CACHE = {}
TTL = 20

SCHEMES = {
    'adpf': {'code': 'adpf', 'name': 'ADPF pension (UAE nationals, Abu Dhabi)', 'authority': 'ADPF', 'kind': 'percent', 'applies_to': 'nationals',
             'base': 'gross', 'min_base': 6000, 'max_base': 100000, 'employee_rate': 11, 'employer_rate': 15,
             'law_reference': 'Abu Dhabi Law No. (2) of 2000 as amended by Law No. (18) of 2023 (Official Gazette 01/12/2023): Art. 15 – 11 % employee '
                              '(first insured from 01/12/2023; 5 % before), 15 % employer; Art. 1 – private sector: contract salary, AED 6,000 – 100,000',
             'notes': 'Used only for employees of branches set to ADPF (Payroll › Branch pension schemes); GPSSA is not deducted for them. '
                      'Rates and limits: Payroll › Legal rates (ae_adpf_*).'},
    'dews': {'code': 'dews', 'name': 'DIFC DEWS (employee workplace savings)', 'authority': 'DIFC', 'kind': 'percent', 'applies_to': 'expats',
             'base': 'basic', 'employee_rate': 0, 'employer_rate': 5.83,
             'law_reference': 'DIFC Employment Law No. 2 of 2019, Art. 66(7) (DIFC Law No. 4 of 2020): employer 5.83 % of the monthly basic wage for '
                              'the first 5 years of service, 8.33 % after; from 1 Feb 2020 instead of the gratuity',
             'notes': 'Used only for employees of branches set to DIFC DEWS; GPSSA-registered nationals are exempt. Rates: Payroll › Legal rates (ae_dews_*).'},
}


def _raw():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        return dict(o.value or {}) if o else {}
    except Exception:
        return {}


def all_branches():
    """{branch id (int): {'pension', 'dews', 'dews_start' (date)}} – only the branches HR set; cached a few seconds per company."""
    sch = connection.schema_name
    hit = _CACHE.get(sch)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    out = {}
    for k, v in _raw().items():
        try:
            st = date.fromisoformat(v.get('dews_start')) if v.get('dews_start') else None
        except ValueError:
            st = None
        out[int(k)] = {'pension': 'adpf' if v.get('pension') == 'adpf' else 'gpssa', 'dews': bool(v.get('dews')), 'dews_start': st}
    _CACHE[sch] = (time.monotonic() + TTL, out)
    return out


def clear_cache():
    _CACHE.pop(connection.schema_name, None)


def of_branch(branch_id):
    return all_branches().get(int(branch_id)) if branch_id else None


def of_employee(employee):
    return of_branch(getattr(employee, 'emp_branch_id_id', None))


def is_adpf(employee):
    b = of_employee(employee)
    return bool(b and b['pension'] == 'adpf')


def is_dews(employee):
    b = of_employee(employee)
    return bool(b and b['dews'])


def dews_start(employee):
    """The first day of DEWS for the employee: the branch's DEWS date (default 1 Feb 2020) or the joining day if later."""
    from .legal import DEWS_START
    b = of_employee(employee) or {}
    st = b.get('dews_start') or DEWS_START
    joined = getattr(employee, 'emp_joined_date', None)
    return max(st, joined) if joined else st


def is_gcc_national(employee):
    """A UAE or other GCC national – registered with GPSSA (the GCC extension), so exempt from DEWS (DIFC Employment Law Art. 65)."""
    n = str(getattr(employee, 'emp_nationality', '') or '').upper()
    from .statutory import is_national
    return is_national(employee) or any(w in n for w in GCC_WORDS)


def _emirate(b):
    try:
        return b.br_state_id.state_name if b.br_state_id_id else ''
    except Exception:
        return ''


def rows(c=None):
    """For the screen: every branch with its setting (and a suggestion: an Abu Dhabi branch → ADPF)."""
    Br = M('OrganisationManager', 'brnch_mstr')
    qs = Br.objects.select_related('br_state_id').order_by('branch_name')
    if c is not None and c.branches is not None:
        qs = qs.filter(pk__in=c.branches)
    cur = all_branches()
    E = M('EmpManagement', 'emp_master')
    out = []
    for b in qs:
        s = cur.get(b.pk) or {'pension': 'gpssa', 'dews': False, 'dews_start': None}
        em = _emirate(b)
        out.append({'branch': b.pk, 'branch_name': b.branch_name, 'emirate': em, 'pension': s['pension'], 'dews': s['dews'],
                    'dews_start': s['dews_start'].isoformat() if s['dews_start'] else '', 'set': b.pk in cur,
                    'suggest_adpf': 'ABU DHABI' in em.upper() and s['pension'] != 'adpf',
                    'employees': E.objects.filter(emp_branch_id=b.pk, is_active=True).count()})
    return out


def ensure_scheme(code, user=None):
    """The statutory scheme 'adpf' / 'dews' (made when missing, switched on) and its salary components."""
    SS = M('PayrollPlus', 'StatutoryScheme')
    d = SCHEMES[code]
    s = SS.objects.filter(code=code).first()
    if s is None:
        s = SS(country='AE', is_standard=True, updated_by_id=getattr(user, 'pk', None), effective_from=date(2023, 12, 1) if code == 'adpf' else date(2020, 2, 1))
        for k, v in d.items():
            setattr(s, k, v)
        s.save()
    elif not s.active:
        s.active = True
        s.save(update_fields=['active'])
    from .statutory import apply_components
    apply_components(user)
    s.refresh_from_db()
    return s


def attach(code, branch_ids):
    """Give every active employee of the branches the scheme's components (amount 0 – the formula works it out). Returns the number added."""
    SS = M('PayrollPlus', 'StatutoryScheme')
    SC = M('PayrollManagement', 'SalaryComponent')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    E = M('EmpManagement', 'emp_master')
    s = SS.objects.filter(code=code).first()
    if s is None or not branch_ids:
        return 0
    comps = [c for c in SC.objects.filter(code__in=[x for x in (s.employee_component, s.employer_component) if x], branch__isnull=True)]
    n = 0
    for e in E.objects.filter(emp_branch_id__in=branch_ids, is_active=True):
        for comp in comps:
            obj, made = ESS.objects.get_or_create(employee=e, component=comp, defaults={'amount': 0, 'is_active': True})
            if made:
                n += 1
            elif not obj.is_active:
                obj.is_active = True
                obj.save(update_fields=['is_active'])
                n += 1
    return n


def save(items, user=None, c=None):
    """items: [{branch, pension, dews, dews_start}] → the new rows. Raises ValueError(field, message)."""
    Br = M('OrganisationManager', 'brnch_mstr')
    allowed = None if c is None or c.branches is None else {int(x) for x in c.branches}
    cur = _raw()
    for it in items or []:
        try:
            bid = int(it.get('branch'))
        except (TypeError, ValueError):
            raise ValueError('branch', tr('Choose a branch.'))
        if not Br.objects.filter(pk=bid).exists() or (allowed is not None and bid not in allowed):
            raise ValueError('branch', tr('Choose a branch.'))
        pension = it.get('pension') or 'gpssa'
        if pension not in ('gpssa', 'adpf'):
            raise ValueError('pension', tr('Choose GPSSA or ADPF.'))
        st = (it.get('dews_start') or '').strip() if isinstance(it.get('dews_start'), str) else ''
        if st:
            try:
                d = date.fromisoformat(st[:10])
            except ValueError:
                raise ValueError('dews_start', tr('Enter a valid date.'))
            from .legal import DEWS_START
            if d < DEWS_START:
                raise ValueError('dews_start', tr('DEWS started on {0:%d/%m/%Y} – the date cannot be earlier.', DEWS_START))
        dews = str(it.get('dews')).lower() in ('1', 'true', 'yes', 'on')
        cur[str(bid)] = {'pension': pension, 'dews': dews, 'dews_start': st[:10] if st else ''}
    M('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    clear_cache()
    added = {}
    adpf = [int(k) for k, v in cur.items() if v.get('pension') == 'adpf']
    dews = [int(k) for k, v in cur.items() if v.get('dews')]
    if adpf:
        ensure_scheme('adpf', user)
        added['adpf'] = attach('adpf', adpf)
    if dews:
        ensure_scheme('dews', user)
        added['dews'] = attach('dews', dews)
    return added


# ------------------------------------------------------------------ DEWS: gratuity for the service before DEWS, the last part month
def pre_dews_gratuity(employee, basic, end):
    """DIFC Employment Law Art. 66(1)-(2): an employee with at least 1 year of continuous employment (before or after the DEWS date)
    gets a gratuity for the service BEFORE the DEWS date only – 21 days' basic wage a year for the first 5 years, 30 days after – at the
    basic wage on the termination / report date. Service after the DEWS date is covered by the monthly contributions.
    {'eligible', 'years_before', 'days', 'amount', 'daily', 'start', 'steps'}"""
    from decimal import Decimal
    from datetime import timedelta
    from .legal import DEWS_START, rate
    b = of_employee(employee) or {}
    start = b.get('dews_start') or DEWS_START
    joined = getattr(employee, 'emp_joined_date', None)
    out = {'eligible': False, 'years_before': Decimal('0'), 'days': Decimal('0'), 'amount': Decimal('0'), 'daily': Decimal('0'), 'start': start, 'steps': []}
    if not joined or not end or end < joined:
        return out
    if (end - joined).days + 1 < 365:
        out['steps'].append(tr('Less than one year of continuous employment – no gratuity'))
        return out
    if joined >= start:
        out['eligible'] = True
        out['steps'].append(tr('Joined on or after the DEWS date {0:%d/%m/%Y} – the whole service is covered by DEWS', start))
        return out
    pre_end = min(end, start - timedelta(days=1))
    yrs = Decimal((pre_end - joined).days + 1) / Decimal('365')
    first, after = rate('ae_dews_gratuity_days_first'), rate('ae_dews_gratuity_days_after')
    days = min(yrs, Decimal('5')) * first + max(yrs - Decimal('5'), Decimal('0')) * after
    daily = Decimal(str(basic or 0)) / rate('ae_dews_day_divisor')
    amt = (days * daily).quantize(Decimal('0.01'))
    out.update({'eligible': True, 'years_before': yrs.quantize(Decimal('0.0001')), 'days': days.quantize(Decimal('0.01')), 'amount': amt, 'daily': daily})
    out['steps'] = [tr('Service before DEWS {0:%d/%m/%Y} – {1:%d/%m/%Y}: {2} years', joined, pre_end, yrs.quantize(Decimal('0.01'))),
                    tr('{0} years × {1} days + {2} years × {3} days = {4} days', min(yrs, Decimal('5')).quantize(Decimal('0.01')), f'{first:g}',
                       max(yrs - Decimal('5'), Decimal('0')).quantize(Decimal('0.01')), f'{after:g}', days.quantize(Decimal('0.01'))),
                    tr('basic {0:,.2f} ÷ {1} = {2:,.2f} a day', Decimal(str(basic or 0)), f"{rate('ae_dews_day_divisor'):g}", daily.quantize(Decimal('0.01'))),
                    tr('{0} days × {1:,.2f} = {2:,.2f}', days.quantize(Decimal('0.01')), daily.quantize(Decimal('0.01')), amt)]
    return out


def exit_gratuity(emp, lwd, basic, salary_from, add, info, lines):
    """Final settlement (services.compute step 4). True when handled here: a DEWS employee (gratuity only for the service before DEWS,
    plus – setting to be confirmed – the DEWS contribution of the days the settlement pays) or an ADPF national (no gratuity)."""
    from decimal import Decimal
    from .legal import rate, dews_terms
    if is_adpf(emp):
        from .statutory import is_national
        if is_national(emp):
            lines.append({'key': 'gratuity', 'label': tr('No gratuity – UAE national (ADPF pension, Abu Dhabi)'), 'kind': 'info', 'amount': 0, 'basis': '',
                          'section': 'gratuity'})
            return True
    if not is_dews(emp) or is_gcc_national(emp):
        return False
    g = pre_dews_gratuity(emp, basic, lwd)
    if g['amount'] > 0:
        add('gratuity', tr('End-of-service gratuity for the service before DEWS ({0} days)', g['days']), 'earning', g['amount'],
            tr('DIFC Employment Law Art. 66(2): 21 days a year for the first 5 years, 30 after – service before {0:%d/%m/%Y}, basic on the last working day', g['start']),
            'gratuity', g['steps'])
    info('gratuity_dews', tr('DIFC DEWS from {0:%d/%m/%Y} – no gratuity for that service (paid monthly to the DEWS plan)', dews_start(emp)),
         '; '.join(g['steps']) if not g['amount'] else '', 'gratuity')
    # the contribution of the days this settlement pays as salary (no payroll ran for them) – paid to the employee (to be confirmed)
    if rate('ae_dews_last_month_to_employee') and salary_from and salary_from <= lwd and lwd >= dews_start(emp):
        from . import services as S
        from .statutory import _rows, base_amount
        rows = _rows(emp)
        a = max(salary_from, dews_start(emp))
        t = dews_terms(emp, base_amount(rows, 'basic'), base_amount(rows, 'gross'), lwd)
        part, days = S._salary_for(t['base'], a, lwd)
        amt = (part * t['rate']).quantize(Decimal('0.01'))
        if amt > 0:
            add('dews_last', tr('DEWS contribution {0:%d/%m/%Y} – {1:%d/%m/%Y} paid to the employee', a, lwd), 'earning', amt,
                tr('{0} % of the basic wage {1:,.2f} for {2} days on the 30-day basis (setting to be confirmed: Payroll › Legal rates)',
                   (t['rate'] * 100).quantize(Decimal('0.01')), t['base'], days), 'gratuity')
    return True


# ------------------------------------------------------------------ report: contributions per employee and month
def report(year, code, c=None):
    """ADPF / DEWS contributions of a year from the payslips (not rejected), per employee and month; for DEWS also the gratuity
    still owed for the service before DEWS (as of today, basic of today)."""
    from decimal import Decimal
    from django.utils import timezone
    SS = M('PayrollPlus', 'StatutoryScheme')
    PC = M('PayrollManagement', 'PayslipComponent')
    s = SS.objects.filter(code=code).first()
    comp_codes = [x for x in ((s.employee_component, s.employer_component) if s else ()) if x]
    qs = (PC.objects.filter(component__code__in=comp_codes, payslip__payroll_run__year=year)
          .exclude(payslip__status__iexact='rejected')
          .select_related('component', 'payslip__employee', 'payslip__payroll_run', 'payslip__employee__emp_branch_id'))
    if c is not None and c.branches is not None:
        qs = qs.filter(payslip__employee__emp_branch_id__in=c.branches)
    per = {}
    for pc in qs:
        e = pc.payslip.employee
        r = per.setdefault(e.pk, {'employee_id': e.pk, 'employee': f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip(), 'employee_code': e.emp_code,
                                  'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'months': {}, 'employee_total': Decimal('0'),
                                  'employer_total': Decimal('0')})
        m = pc.payslip.payroll_run.month
        cell = r['months'].setdefault(m, {'employee': Decimal('0'), 'employer': Decimal('0')})
        side = 'employee' if pc.component.code == (s.employee_component if s else None) else 'employer'
        cell[side] += pc.amount or 0
        r[side + '_total'] += pc.amount or 0
    rows = sorted(per.values(), key=lambda x: x['employee_code'])
    if code == 'dews':
        from .statutory import _rows, base_amount
        E = M('EmpManagement', 'emp_master')
        today = timezone.localdate()
        ids = [k for k, v in all_branches().items() if v['dews']]
        if c is not None and c.branches is not None:
            ids = [i for i in ids if i in {int(x) for x in c.branches}]
        known = {r['employee_id']: r for r in rows}
        for e in E.objects.filter(emp_branch_id__in=ids, is_active=True).select_related('emp_branch_id').order_by('emp_code'):
            if is_gcc_national(e):
                continue
            g = pre_dews_gratuity(e, base_amount(_rows(e), 'basic'), today)
            r = known.get(e.pk)
            if r is None:
                r = {'employee_id': e.pk, 'employee': f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip(), 'employee_code': e.emp_code,
                     'branch': e.emp_branch_id.branch_name if e.emp_branch_id_id else '', 'months': {}, 'employee_total': Decimal('0'), 'employer_total': Decimal('0')}
                rows.append(r)
            r['pre_dews_gratuity'] = g['amount']
            r['pre_dews_years'] = g['years_before']
        rows.sort(key=lambda x: x['employee_code'])
    for r in rows:
        r['months'] = {str(k): {'employee': float(v['employee']), 'employer': float(v['employer'])} for k, v in sorted(r['months'].items())}
        r['employee_total'], r['employer_total'] = float(r['employee_total']), float(r['employer_total'])
        if 'pre_dews_gratuity' in r:
            r['pre_dews_gratuity'] = float(r['pre_dews_gratuity'])
            r['pre_dews_years'] = float(r['pre_dews_years'])
    tot = {'employee': round(sum(r['employee_total'] for r in rows), 2), 'employer': round(sum(r['employer_total'] for r in rows), 2),
           'pre_dews_gratuity': round(sum(r.get('pre_dews_gratuity') or 0 for r in rows), 2)}
    return {'year': year, 'scheme': code, 'rows': rows, 'totals': tot}


def rates_now():
    """The ADPF / DEWS figures in force today (Payroll › Legal rates), with the 'to be confirmed' ones marked."""
    from .legal import KNOWN_RATES, rate
    out = []
    for k, meta in KNOWN_RATES.items():
        if k.startswith(('ae_adpf_', 'ae_dews_')):
            out.append({'key': k, 'label': tr(meta['label']), 'value': str(rate(k)), 'to_be_confirmed': bool(meta.get('tbc'))})
    return out


# ------------------------------------------------------------------ API
from rest_framework.response import Response   # noqa: E402

from .views import Base   # noqa: E402

VIEW = {'change_salarycomponent', 'add_salarycomponent', 'change_payrollplussettings', 'view_salarycomponent', 'view_payrollrun'}
CHANGE = {'change_salarycomponent', 'add_salarycomponent', 'change_payrollplussettings'}


class BranchSchemesView(Base):
    """v1.62.11 (item 229) – GET api/branch-schemes/   PUT {rows: [{branch, pension, dews, dews_start}]}   GET api/branch-schemes/report/?year=&scheme="""

    def get(self, request, action=None):
        from .services import Refused
        if not self.can(VIEW):
            raise Refused(tr('You may not see the payroll setup.'), status=403)
        if action == 'report':
            code = request.GET.get('scheme') or 'dews'
            if code not in ('adpf', 'dews'):
                raise Refused(tr('Choose ADPF or DEWS.'), problems={'scheme': tr('Choose ADPF or DEWS.')})
            try:
                from django.utils import timezone as _tz
                year = int(request.GET.get('year') or 0) or _tz.localdate().year
            except ValueError:
                raise Refused(tr('Enter a valid year.'), problems={'year': tr('Enter a valid year.')})
            return Response(report(year, code, self.c))
        if action:
            raise Refused(tr('Unknown action.'), status=405)
        SS = M('PayrollPlus', 'StatutoryScheme')
        sch = {s.code: {'active': s.active, 'employee_component': s.employee_component, 'employer_component': s.employer_component}
               for s in SS.objects.filter(code__in=('gpssa', 'adpf', 'dews'))}
        return Response({'rows': rows(self.c), 'schemes': sch, 'rates': rates_now(), 'can_change': self.can(CHANGE)})

    def put(self, request, action=None):
        from .services import Refused
        if not self.can(CHANGE):
            raise Refused(tr('You may not change the payroll setup.'), status=403)
        items = request.data.get('rows')
        if not isinstance(items, list):
            raise Refused(tr('Nothing to save.'))
        try:
            added = save(items, request.user, self.c)
        except ValueError as e:
            raise Refused(e.args[1], problems={e.args[0]: e.args[1]})
        SS = M('PayrollPlus', 'StatutoryScheme')
        sch = {s.code: {'active': s.active, 'employee_component': s.employee_component, 'employer_component': s.employer_component}
               for s in SS.objects.filter(code__in=('gpssa', 'adpf', 'dews'))}
        return Response({'rows': rows(self.c), 'schemes': sch, 'rates': rates_now(), 'can_change': True, 'added': added})
