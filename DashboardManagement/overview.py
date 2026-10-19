"""
Company at a glance (main dashboard, v1.7.0): one card per module with the numbers that need attention.

Every count goes through the same access rules as the lists (AccessControl.scope_queryset), so a branch
HR user sees their branches only, and a card is left out when the user has no right to see that module.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from collections import Counter   # v1.17.1
from django.db.models import Count, Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)
M_ = '/main-sidebar/'


def rpt(key, f=None, **period):
    """Link from a dashboard figure to the report rows behind it (v1.8.1)."""
    from urllib.parse import urlencode
    q = {f'f_{k}': v for k, v in (f or {}).items() if v not in (None, '')}
    q.update({k: str(v) for k, v in period.items() if v})
    return f'{M_}report-options/r/{key}' + ('?' + urlencode(q) if q else '')


def rec(obj):
    """Link from a dashboard line to its record."""
    if obj is None:
        return None
    if obj._meta.label == 'EmpManagement.emp_master':
        return f'{M_}sub-sidebar/employee-details/{obj.pk}/details'
    return f'{M_}report-options/rec/{obj._meta.label}/{obj.pk}'


def rec_link(row):
    """v1.17.0: a report row's own record (employee page for an employee)."""
    m, i = row.get('_m'), row.get('_id')
    if not (m and i):
        return None
    if m == 'EmpManagement.emp_master':
        return f'{M_}sub-sidebar/employee-details/{i}/details'
    return f'{M_}report-options/rec/{m}/{i}'


def _model(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def payroll_summary(runs, payslips, today):
    """v1.15.1 – the payroll card: the latest processed / approved / paid run BY PERIOD that has payslips (a pending run or an
    empty run is not "the last payroll"), its real net pay (net_salary, or additions − deductions where net was never
    stored), payslip count, payslips with no net pay, how many months old it is and the runs still waiting to be processed."""
    from calendar import month_name
    from django.db.models import DecimalField, F, Value
    from django.db.models.functions import Coalesce
    out = {'run': None, 'period': '', 'net': 0, 'count': 0, 'zero': 0, 'months_old': 0, 'drafts': 0}
    if runs is None or payslips is None:
        return out
    out['drafts'] = runs.filter(status='pending').count()
    done = runs.filter(status__in=('processed', 'approved', 'paid', 'Approved', 'Processed', 'Paid')).order_by('-year', '-month', '-id')
    for r in done[:24]:
        if payslips.filter(payroll_run=r).exists():
            out['run'] = r
            break
    r = out['run']
    if r is None:
        return out
    dec = DecimalField(max_digits=14, decimal_places=2)
    q = payslips.filter(payroll_run=r).annotate(_net=Coalesce('net_salary', F('total_additions') - F('total_deductions'), Value(0), output_field=dec))
    agg = q.aggregate(n=Sum('_net'), c=Count('id'))
    out.update(net=agg['n'] or 0, count=agg['c'], zero=q.filter(_net__lte=0).count(), period=f'{month_name[r.month]} {r.year}',
               months_old=(today.year * 12 + today.month) - (r.year * 12 + r.month))
    out['drafts'] = runs.filter(status='pending').filter(Q(year__gt=r.year) | Q(year=r.year, month__gt=r.month)).count()
    return out


class _View:
    zeo_scope = True


def _qs(request, label):
    from AccessControl.access import scope_queryset
    m = _model(label)
    if m is None:
        return None
    qs = scope_queryset(request, _View(), m._default_manager.all())
    try:   # v1.31.1 location level: the switcher's / the user's locations, as on the lists
        from OrgStructure.location import filter_qs
        qs = filter_qs(request, qs, m)
    except Exception:
        pass
    return qs


def _can(c, *codes):
    return c.admin or any(x in c.codes for x in codes)


def _num(v):
    if isinstance(v, Decimal):
        return float(v)
    return v or 0


def _st(qs, *values):
    q = Q()
    for v in values:
        q |= Q(status__iexact=v)
    return qs.filter(q).count()


class Calc:
    """v1.16.0 – every dashboard figure that opens a report is counted FROM that report (same function, same period, same
    branches, same filters as the drill-down), so the number on the card is always the number of rows the click shows."""

    def __init__(self, request):
        self.request = request
        self.cache = {}

    def rows(self, key, d_from=None, d_to=None):
        from .reports import REPORTS, Run
        from datetime import date as _date
        ck = (key, d_from, d_to)
        if ck not in self.cache:
            run = Run(self.request, {'period': None})
            run.date_from, run.date_to = d_from or _date(1900, 1, 1), d_to or _date(2999, 12, 31)
            try:
                self.cache[ck] = REPORTS[key][3](run)[1]
            except ValueError:
                self.cache[ck] = []
        return self.cache[ck]

    @staticmethod
    def match(rows, f=None, nz=None):
        def norm(v):
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str('' if v is None else v).strip().lower()
        out = []
        for r in rows:
            ok = all(any(norm(r.get(k)) == norm(x) for x in str(v).split('|')) for k, v in (f or {}).items() if v not in (None, ''))
            if ok and nz:
                v = r.get(nz)
                ok = (v != 0) if isinstance(v, (int, float)) and not isinstance(v, bool) else bool(v)
            if ok:
                out.append(r)
        return out

    def kpi(self, label, key, f=None, d_from=None, d_to=None, sum_of=None, timed=True, **extra):
        """{label, value, link, measure, timed}; value = rows (or the sum of `sum_of` columns) the link shows."""
        rows = self.match(self.rows(key, d_from, d_to), f)
        if sum_of:
            cols = [sum_of] if isinstance(sum_of, str) else list(sum_of)
            value = round(sum(float(r.get(c) or 0) for r in rows for c in cols if isinstance(r.get(c), (int, float))), 2)
        else:
            value = len(rows)
        per = {'from': d_from, 'to': d_to} if d_from else {}
        out = {'label': label, 'value': value, 'link': rpt(key, f, **per), 'timed': timed,
               'measure': {'report': key, 'f': {k: v for k, v in (f or {}).items() if v not in (None, '')}, 'sum': sum_of, **{k: str(v) for k, v in per.items()}}}
        out.update(extra)
        return out


FAR = {'from': '2000-01-01', 'to': '2099-12-31'}


def overview(request):
    """v1.16.0: every card follows the dashboard period (?period= / ?from=&to=) or its own (?card=<key> with from / to);
    cards that show the current state (headcount, balances, queues) say so (timed = false)."""
    from datetime import date as _date
    from AccessControl.access import ctx
    from . import period as P
    from .attendance_data import WORKED
    c = ctx(request)
    today = timezone.localdate()
    q = request.query_params
    p_from, p_to, preset = P.resolve(q, today)
    only = q.get('card') or None
    calc = Calc(request)
    cards = []
    F = lambda d: _date(2000, 1, 1) if d == 'lo' else _date(2099, 12, 31)   # noqa: E731

    def card(key, title, icon, route, kpis, items=None, note='', timed=True, app=None, default_period=None, empty=None, see=None, subtitle=''):
        cards.append({'key': key, 'title': title, 'icon': icon, 'route': route if route.startswith('/') else M_ + route,
                      'app': (M_ + app) if app else None, 'kpis': kpis, 'items': items or [], 'note': note, 'timed': timed,
                      'default_period': default_period, 'empty': empty, 'see': see, 'subtitle': subtitle,
                      'period': {'from': p_from, 'to': p_to, 'preset': preset, 'label': P.label(p_from, p_to, today)} if timed else None})

    def want(key):
        return only is None or only == key

    def safe(key, fn):
        if not want(key):
            return
        try:
            fn()
        except Exception:
            logger.exception('dashboard card failed: %s', key)

    one_day = p_from == p_to
    a, b = p_from, p_to

    # v1.17.0 (dash17): HR operations – "What do I need to do today?". One home per figure: cost trends, liabilities and
    # overtime cost live on the Management dashboard (the cards link there with `see`); the user's own items on My dashboard.
    def opens(key, *codes):
        """The user may open the report a card is counted from (no dead links)."""
        from .reports import REPORTS
        spec = REPORTS.get(key)
        return bool(spec) and _can(c, *spec[5]) and (not codes or _can(c, *codes))

    # ---------------- approvals queue (every module, oldest first)
    def queue():
        if not opens('requests-pending'):
            return
        rows = calc.rows('requests-pending')
        total = calc.kpi('Waiting for approval', 'requests-pending', None, timed=False)
        total['tone'] = 'warn' if total['value'] else ''
        late = calc.kpi('Waiting more than 7 days', 'requests-pending', {'over_7': 'Yes'}, timed=False)
        late['tone'] = 'bad' if late['value'] else ''
        by = {}
        for r in rows:
            g = by.setdefault(r['type'], {'n': 0, 'oldest': 0})
            g['n'] += 1
            g['oldest'] = max(g['oldest'], r['waiting'] if r['waiting'] != '' else 0)
        items = []
        for t, g in sorted(by.items(), key=lambda x: -x[1]['oldest']):
            k = calc.kpi(t, 'requests-pending', {'type': t}, timed=False)
            k['sub'] = 'oldest {0} days' if g['oldest'] != 1 else 'oldest {0} day'
            k['sub_n'] = g['oldest']
            items.append(k)
        card('queue', 'Approvals queue', 'approval', rpt('requests-pending'), [total, late], items, 'By type, oldest first', timed=False,
             empty=None if rows else 'Nothing is waiting for approval.')
    safe('queue', queue)

    # ---------------- attendance (today by default; both attendance sources, attendance_data)
    def attendance():
        if not opens('attendance', 'view_attendance', 'view_attendancereport'):
            return
        daily = (b - a).days <= 92
        if daily:
            kp = [calc.kpi('Present' if one_day else 'Days present', 'attendance', {'status': '|'.join(WORKED)}, a, b, tone='good'),
                  calc.kpi('Late' if one_day else 'Late arrivals', 'attendance', {'late_in': 'Yes'}, a, b),
                  calc.kpi('On leave' if one_day else 'Leave days', 'attendance', {'status': 'Leave'}, a, b),
                  calc.kpi('Absent' if one_day else 'Days absent', 'attendance', {'status': 'Absent'}, a, b)]
            if one_day:
                kp.append(calc.kpi('Not checked in', 'attendance', {'status': 'Not marked yet'}, a, b))
        else:
            kp = [calc.kpi('Days present', 'attendance-summary', None, a, b, sum_of=['present', 'rest_worked'], tone='good'),
                  calc.kpi('Late arrivals', 'attendance-summary', None, a, b, sum_of='late'),
                  calc.kpi('Leave days', 'attendance-summary', None, a, b, sum_of='leave'),
                  calc.kpi('Days absent', 'attendance-summary', None, a, b, sum_of='absent')]
        for k in kp[1:]:
            k['tone'] = 'warn' if k['value'] and k['label'] in ('Late', 'Late arrivals', 'Absent', 'Days absent', 'Not checked in') else k.get('tone', '')
        open_ = rpt('attendance-register' if (b - a).days <= 62 else 'attendance-summary', None, **{'from': a, 'to': b})
        empty = None
        if one_day and not any(k['value'] for k in kp):   # v1.17.0: say why a day shows only zeros
            from .services import is_working_day
            if not is_working_day(a):
                empty = 'Weekend: nobody is expected to check in on this day.'
        card('attendance', 'Attendance', 'fact_check', open_, kp, app='attendance-plus/board', default_period='today', empty=empty)
    safe('attendance', attendance)

    # ---------------- joiners and leavers (period)
    def movement():
        if not opens('joiners-leavers', 'view_emp_master'):
            return
        k_join = calc.kpi('Joined', 'joiners-leavers', {'event': 'Joined'}, a, b)
        # v1.17.0 (audit L8): "Left" = last working day up to today; a last day still ahead is "Leaving" (not gone yet)
        k_left = calc.kpi('Left', 'joiners-leavers', {'event': 'Left'}, a, min(b, today)) if a <= today else \
            calc.kpi('Left', 'joiners-leavers', {'event': 'Left'}, a, a - timedelta(days=1))
        k_join['tone'] = 'good' if k_join['value'] else ''
        k_left['tone'] = 'warn' if k_left['value'] else ''
        kp = [k_join, k_left]
        if b > today:
            k_go = calc.kpi('Leaving (last day ahead)', 'joiners-leavers', {'event': 'Left'}, max(a, today + timedelta(days=1)), b)
            if k_go['value']:
                kp.append(k_go)
        if opens('exits'):
            kp.append(calc.kpi('Resignations approved', 'exits', {'status': 'Approved'}, a, b))
        card('movement', 'Joiners and leavers', 'swap_horiz', rpt('joiners-leavers', None, **{'from': a, 'to': b}), kp, app='sub-sidebar/employee-master',
             see={'label': 'Headcount trend and attrition', 'tab': 'management'})
    safe('movement', movement)

    # ---------------- headcount now by branch / department
    def headcount():
        if not opens('employees', 'view_emp_master'):
            return
        active = calc.match(calc.rows('employees'), {'status': 'Active'})
        by_b, by_d, by_l = {}, {}, {}
        # v1.31.1 location level: headcount by location (employees placed in a location) while control is on
        loc_of = {}
        try:
            from OrgStructure import location as _L
            if _L.enabled():
                from OrgStructure.models import EmployeeOrg, Location
                ln = dict(Location.objects.values_list('id', 'name'))
                loc_of = {e: ln.get(l, '') for e, l in EmployeeOrg.objects.filter(location_id__isnull=False).values_list('employee_id', 'location_id')}
        except Exception:
            loc_of = {}
        for r in active:
            by_b[r.get('branch') or ''] = by_b.get(r.get('branch') or '', 0) + 1
            by_d[r.get('department') or ''] = by_d.get(r.get('department') or '', 0) + 1
            if loc_of:
                ln_ = loc_of.get(r.get('_id') or r.get('id') or r.get('_emp'), '')
                by_l[ln_] = by_l.get(ln_, 0) + 1
        items = []
        groups = [('branch', 'branch', 'By branch', by_b), ('department', 'department', 'By department', by_d)]
        if loc_of:
            groups.append(('location', 'org_location', 'By location', by_l))
        for name, f, grp, data in groups:
            for v, _n in sorted(data.items(), key=lambda x: (-x[1], x[0]))[:6]:
                k = calc.kpi(v or ('No branch' if f == 'branch' else 'No location' if f == 'org_location' else 'No department'), 'employees', {'status': 'Active', f: v}, timed=False)
                k['group'] = grp
                items.append(k)
        card('headcount', 'Headcount now', 'groups', rpt('employees', {'status': 'Active'}),
             [calc.kpi('Active employees', 'employees', {'status': 'Active'}, timed=False)], items, '', timed=False, app='sub-sidebar/employee-master')
    safe('headcount', headcount)

    # ---------------- documents and visas expiring (today)
    def documents():
        if not opens('expiry-all'):
            return
        rows = calc.rows('expiry-all')
        kp = [calc.kpi('Expired', 'expiry-all', {'status': 'Expired'}, timed=False),
              calc.kpi('Within 30 days', 'expiry-all', {'status': 'Within 30 days'}, timed=False),
              calc.kpi('In 31–60 days', 'expiry-all', {'status': 'In 31–60 days'}, timed=False),
              calc.kpi('In 61–90 days', 'expiry-all', {'status': 'In 61–90 days'}, timed=False)]
        kp[0]['tone'] = 'bad' if kp[0]['value'] else ''
        kp[1]['tone'] = 'warn' if kp[1]['value'] else ''
        nxt = [r for r in rows if r['status'] in ('Within 30 days', 'In 31–60 days')][:5]
        items = [{'label': f"{r['document']} · {r['employee']}", 'value': r['expiry'].isoformat() if hasattr(r.get('expiry'), 'isoformat') else '',
                  'date': r['expiry'], 'timed': False, 'link': (rec_link(r) or rpt('expiry-all', {'employee': r['employee']}))} for r in nxt]
        card('documents', 'Documents and visas', 'badge', rpt('expiry-all', {'status': 'Expired|Within 30 days|In 31–60 days|In 61–90 days'}), kp, items,
             'Next to expire', timed=False, app='sub-sidebar/document-expired')
    safe('documents', documents)

    # ---------------- probation ending (today)
    def probation():
        if not opens('probation', 'view_emp_master'):
            return
        rows = calc.rows('probation')
        kp = [calc.kpi('Overdue', 'probation', {'status': 'Overdue'}, timed=False),
              calc.kpi('Ending in 30 days', 'probation', {'status': 'Due in 30 days'}, timed=False),
              calc.kpi('In 31–90 days', 'probation', {'status': 'Due in 90 days'}, timed=False)]
        kp[0]['tone'] = 'bad' if kp[0]['value'] else ''
        kp[1]['tone'] = 'warn' if kp[1]['value'] else ''
        nxt = [r for r in rows if r['status'] in ('Overdue', 'Due in 30 days')][:5]
        items = [{'label': r['employee'], 'value': r['probation_end'].isoformat() if hasattr(r.get('probation_end'), 'isoformat') else '',
                  'date': r.get('probation_end'), 'timed': False, 'tone': 'bad' if r['status'] == 'Overdue' else '',
                  'link': f"{M_}sub-sidebar/employee-details/{r['_id']}/details" if r.get('_id') else rpt('probation', {'employee': r['employee']})} for r in nxt]
        card('probation', 'Probation ending', 'hourglass_bottom', rpt('probation', {'status': 'Overdue|Due in 30 days|Due in 90 days'}), kp, items,
             'Decisions due', timed=False, app='sub-sidebar/employee-master')
    safe('probation', probation)

    # ---------------- payroll status of the month (no cost figures: those are on the Management dashboard)
    def payroll():
        if not opens('payslip-status', 'view_payrollrun', 'view_payslip'):
            return
        run = _qs(request, 'PayrollManagement.PayrollRun')
        rows = calc.rows('payslip-status', a, b)
        kp = [calc.kpi('Payslips', 'payslip-status', None, a, b),
              calc.kpi('Waiting for approval', 'payslip-status', {'status': 'Pending|Processed'}, a, b),
              calc.kpi('Approved', 'payslip-status', {'status': 'Approved'}, a, b),
              calc.kpi('Paid', 'payslip-status', {'status': 'Paid'}, a, b)]
        kp[1]['tone'] = 'warn' if kp[1]['value'] else ''
        kp[3]['tone'] = 'good' if kp[3]['value'] else ''
        zero = calc.kpi('No net pay – check the salary details', 'payslip-status', {'zero': 'Yes'}, a, b, tone='bad')
        items = []
        runs = {}
        for r in rows:
            runs.setdefault((r['run'], r['period'], r['run_status']), 0)
            runs[(r['run'], r['period'], r['run_status'])] += 1
        ar_names = {}
        try:   # v1.17.0 (audit L9): the payroll run's Arabic name in Arabic (the filter keeps the run name of the report rows)
            from Translations.i18n import current_lang
            if (current_lang(request) or 'en') == 'ar' and run is not None:
                ar_names = {r.name: r.arabic_name for r in run.exclude(arabic_name='') if r.name}
        except Exception:
            ar_names = {}
        for (name, per, st), _n in sorted(runs.items(), key=lambda x: x[0][1]):
            k = calc.kpi(ar_names.get(name) or f'{name}', 'payslip-status', {'run': name, 'run_status': st}, a, b)
            k['sub'] = 'Run {0}'
            k['sub_n'] = st
            items.append(k)
        if zero['value']:
            items.append(zero)
        m_lo, m_hi = a.replace(day=1), b
        drafts = run.filter(status='pending').filter(Q(year__gt=m_lo.year) | Q(year=m_lo.year, month__gte=m_lo.month)).filter(
            Q(year__lt=m_hi.year) | Q(year=m_hi.year, month__lte=m_hi.month)).count() if run is not None else 0
        if drafts:
            items.append({'label': 'Payroll runs in the period not processed yet', 'value': drafts, 'link': M_ + 'salary-options/pay-roll', 'tone': 'warn'})
        empty = None if rows else 'No payroll run has been processed for this period. Open Payroll to run it.'
        card('payroll', 'Payroll status', 'payments', rpt('payslip-status', None, **{'from': a, 'to': b}), kp if rows else [], items,
             'Payroll runs', app='salary-options/pay-roll', empty=empty, default_period='this_month',
             see={'label': 'Payroll cost and month over month', 'tab': 'management'})
    safe('payroll', payroll)

    # ---------------- leave (period)
    def leave():
        if not opens('leave', 'view_employee_leave_request', 'view_leaveapproval'):
            return
        on = calc.kpi('On leave' if one_day else 'Approved leave in the period', 'leave', {'status': 'Approved'}, a, b)
        days = calc.kpi('Days approved', 'leave', {'status': 'Approved'}, a, b, sum_of='approved')
        nxt = calc.kpi('Starting in the next 7 days', 'leave', {'status': 'Approved', 'starting': 'Yes'}, today + timedelta(days=1), today + timedelta(days=7), timed=False)
        card('leave', 'Leave', 'event_busy', rpt('leave', None, **{'from': a, 'to': b}), [on, days, nxt], app='leave-options/leave-request')
    safe('leave', leave)

    # ---------------- overtime waiting for approval (cost is on the Management dashboard)
    def overtime():
        if _model('calendars.EmployeeOvertime') is None or not opens('overtime', 'view_employeeovertime', 'view_attendance'):
            return
        pend = calc.kpi('Hours not approved yet', 'overtime', {'approved': 'No'}, a, b, sum_of='hours')
        pend['tone'] = 'warn' if pend['value'] else ''
        ent = calc.kpi('Entries not approved', 'overtime', {'approved': 'No'}, a, b)
        per = {}
        for r in calc.match(calc.rows('overtime', a, b), {'approved': 'No'}):
            per[r['employee']] = per.get(r['employee'], 0) + float(r.get('hours') or 0)
        items = []
        for name, _h in sorted(per.items(), key=lambda x: -x[1])[:4]:
            k = calc.kpi(name, 'overtime', {'employee': name, 'approved': 'No'}, a, b, sum_of='hours')
            k['unit'] = 'h'
            items.append(k)
        card('overtime', 'Overtime to approve', 'more_time', rpt('overtime', {'approved': 'No'}, **{'from': a, 'to': b}), [pend, ent], items, 'Most hours waiting',
             app='shift-options/employee-overtime', see={'label': 'Overtime cost', 'tab': 'management'})
    safe('overtime', overtime)

    # ---------------- help desk (general requests being worked on)
    def tickets():
        if not opens('general-requests', 'view_generalrequest'):
            return
        week_ago = today - timedelta(days=7)
        prog = {'status': 'In progress'}
        k_open = calc.kpi('In progress', 'general-requests', prog, F('lo'), F('hi'), timed=False)
        k_late = calc.kpi('In progress more than 7 days', 'general-requests', prog, F('lo'), week_ago - timedelta(days=1), timed=False)
        k_late['tone'] = 'bad' if k_late['value'] else ''
        raised = calc.kpi('Raised', 'general-requests', None, a, b)
        closed = calc.kpi('Closed', 'general-requests', {'status': 'Approved|Rejected|Closed'}, a, b, tone='good')
        card('tickets', 'Help desk', 'support_agent', rpt('general-requests', None, **{'from': a, 'to': b}), [k_open, k_late, raised, closed],
             app='general-sidebar/general-request')
    safe('tickets', tickets)

    # ---------------- assets (current state)
    def assets():
        if not opens('assets', 'view_asset', 'view_assetallocation', 'view_assetrequest'):
            return
        rows = calc.rows('assets')
        total = calc.kpi('Assets registered', 'assets', None, timed=False)
        held = calc.kpi('With employees', 'assets', {'held': 'Yes'}, timed=False, tone='good')
        free = calc.kpi('Free to allocate', 'assets', {'status': 'Available'}, timed=False)
        by = {}
        for r in rows:
            by[r.get('status') or ''] = by.get(r.get('status') or '', 0) + 1
        items = [calc.kpi(k or 'No status', 'assets', {'status': k}, timed=False) for k, _n in sorted(by.items(), key=lambda x: -x[1])[:5]]
        kp = [total, held, free]
        if opens('asset-maintenance-due'):   # v1.17.1: maintenance plans due / overdue (AssetPlus)
            kp.append(_nz(calc.kpi('Maintenance due', 'asset-maintenance-due', None, timed=False, tone='warn')))
        card('assets', 'Assets', 'inventory_2', rpt('assets'), kp, items, 'By status', timed=False, app='asset-plus/home')
    safe('assets', assets)

    # ---------------- recruitment (open positions now)
    def recruitment():
        if not opens('recruitment', 'view_jobopening', 'view_candidate', 'view_manpowerrequisition'):
            return
        A = _qs(request, 'RecruitmentManagement.Application')
        # v1.17.0 (audit M1/M3): jobs = rows, vacancies = the openings column of the same list; requisitions to approve
        # open the requisitions waiting for approval (report "requisitions"), so every figure = the list it opens
        kp = [calc.kpi('Open jobs', 'recruitment', {'open': 'Yes'}, timed=False),
              calc.kpi('Open vacancies', 'recruitment', {'open': 'Yes'}, sum_of='openings', timed=False),
              {'label': 'Candidates in process', 'value': A.exclude(stage__in=('hired', 'rejected', 'withdrawn')).count() if A is not None else 0, 'timed': False,
               'link': M_ + 'recruitment-options/pipeline'}]
        if opens('requisitions'):
            kp.append(calc.kpi('Requisitions to approve', 'requisitions', {'waiting': 'Yes'}, timed=False, tone='warn'))
        card('recruitment', 'Recruitment', 'person_add', rpt('recruitment', {'open': 'Yes'}), kp, timed=False, app='recruitment-options/requisitions')
    safe('recruitment', recruitment)

    # ---------------- performance (the active cycle)
    def performance():
        if not opens('appraisals', 'view_appraisalcycle', 'view_goalsheet'):
            return
        CY = _qs(request, 'PerformanceManagement.AppraisalCycle')
        cyc = CY.exclude(status__in=('closed', 'draft')).order_by('-period_from').first() if CY is not None else None
        name = cyc.name if cyc else ''
        if not name:
            card('performance', 'Performance', 'speed', 'performance-options', [], timed=False, app='performance-options',
                 empty='No appraisal cycle is running.')
            return
        k1 = calc.kpi('Goal sheets', 'appraisals', {'cycle_name': name}, timed=False)
        k2 = calc.kpi('Reviewed', 'appraisals', {'cycle_name': name, 'reviewed': 'Yes'}, timed=False, tone='good')
        k3 = calc.kpi('Still open', 'appraisals', {'cycle_name': name, 'reviewed': 'No'}, timed=False, tone='warn')
        card('performance', 'Performance', 'speed', rpt('appraisals', {'cycle_name': name}), [k1, k2, k3], timed=False, app='performance-options',
             subtitle=name)
    safe('performance', performance)

    # ---------------- learning
    def learning():
        if not opens('training', 'view_trainingsession', 'view_course', 'view_nomination'):
            return
        S = _qs(request, 'LearningManagement.TrainingSession')
        up = S.filter(start_date__gte=today).order_by('start_date') if S is not None else None
        items = [{'label': s.course.title if s.course_id else s.code, 'value': s.start_date.isoformat(), 'date': s.start_date, 'link': rec(s), 'timed': False}
                 for s in up.select_related('course')[:4]] if up is not None else []
        # v1.17.0 (audit M2/M7): "Sessions coming up" opens the sessions list filtered the same way; the module icon opens the
        # training calendar (the admin screen), not "My learning" (which needs an employee record)
        k_up = calc.kpi('Sessions coming up', 'sessions', {'upcoming': 'Yes'}, timed=False) if opens('sessions') else \
            {'label': 'Sessions coming up', 'value': up.count() if up is not None else 0, 'link': M_ + 'learning-options/calendar', 'timed': False}
        kp = [k_up, calc.kpi('Nominations to approve', 'training', {'waiting': 'Yes'}, F('lo'), F('hi'), timed=False, tone='warn')]
        if opens('certificates'):   # v1.17.1: certificates (not renewed) expiring within 60 days
            kp.append(_nz(calc.kpi('Certificates expiring in 60 days', 'certificates', {'expiring_60': 'Yes'}, timed=False, tone='warn')))
        card('learning', 'Learning', 'school', rpt('sessions', {'upcoming': 'Yes'}) if opens('sessions') else 'learning-options/calendar',
             kp, items, 'Next sessions', timed=False, app='learning-options/calendar')
    safe('learning', learning)

    # ---------------- v1.17.1 (dash171): leavers who still hold assets / have a loan to settle
    def leavers():
        if not opens('leaver-clearance'):
            return
        rows = calc.rows('leaver-clearance')
        kp = [_nz(calc.kpi('Leavers to clear', 'leaver-clearance', None, timed=False, tone='warn')),
              _nz(calc.kpi('Assets to return', 'leaver-clearance', {'assets': 'Yes'}, timed=False)),
              _nz(calc.kpi('Loans to settle', 'leaver-clearance', {'loans': 'Yes'}, timed=False))]
        items = [{'label': r['employee'], 'value': r['last_day'].isoformat() if r.get('last_day') else '', 'date': r.get('last_day'), 'timed': False,
                  'sub': r['reason'], 'link': rec_link(r)} for r in rows[:5]]
        card('leavers', "Leavers' clearance", 'logout', rpt('leaver-clearance'), kp, items, 'Last working day', timed=False, app='asset-plus/clearance',
             empty=None if rows else 'Nobody leaving holds company assets or a loan.')
    safe('leavers', leavers)

    # ---------------- v1.17.1: company policy acknowledgements (the HR user's branches)
    def policies():
        if not opens('policy-acks'):
            return
        rows = calc.rows('policy-acks')
        if not rows:
            return
        kp = [_nz(calc.kpi('Acknowledged', 'policy-acks', {'status': 'Acknowledged'}, timed=False, tone='good')),
              _nz(calc.kpi('Pending', 'policy-acks', {'status': 'Pending'}, timed=False, tone='warn')),
              _nz(calc.kpi('Overdue', 'policy-acks', {'status': 'Overdue'}, timed=False, tone='bad'))]
        per = {}
        for r in rows:
            g = per.setdefault(r['policy'], Counter())
            g[r['status']] += 1
        items = []
        for name, g in sorted(per.items(), key=lambda x: (-(x[1]['Overdue'] + x[1]['Pending']), x[0]))[:4]:
            for st in ('Acknowledged', 'Pending', 'Overdue'):
                if g.get(st):
                    k = calc.kpi(st, 'policy-acks', {'policy': name, 'status': st}, timed=False)
                    k['group'] = name
                    k['tone'] = {'Overdue': 'bad', 'Pending': 'warn', 'Acknowledged': 'good'}[st]
                    items.append(k)
        card('policies', 'Policy acknowledgements', 'policy', rpt('policy-acks', {'status': 'Pending|Overdue'}), kp, items, '', timed=False,
             app='org-structure/policy-acknowledgements')
    safe('policies', policies)

    # ---------------- v1.17.1: expense reports and advances (reimbursement is finance's next step)
    def expenses():
        if _model('ExpenseManagement.ExpenseReport') is None or not (opens('expense-reports') or opens('expense-advances')):
            return
        kp = []
        if opens('expense-reports'):
            # counts only – money lives on Management (one home per figure); the amounts are columns of the lists they open
            kp += [_nz(calc.kpi('Waiting for approval', 'expense-reports', {'status': 'Awaiting approval'}, timed=False, tone='warn')),
                   _nz(calc.kpi('Approved, not reimbursed', 'expense-reports', {'status': 'Approved'}, timed=False))]
        if opens('expense-advances'):
            kp += [_nz(calc.kpi('Advances outstanding', 'expense-advances', {'outstanding': 'Yes'}, timed=False))]
        has = any(k['value'] for k in kp) or bool(calc.rows('expense-reports') if opens('expense-reports') else [])
        card('expenses', 'Expenses', 'receipt_long', rpt('expense-reports', {'status': 'Awaiting approval|Approved'}) if opens('expense-reports') else rpt('expense-advances'),
             kp if has else [], [], '', timed=False, app='expense-options/home', empty=None if has else 'No expense reports yet.')
    safe('expenses', expenses)

    # v1.17.0: HR operations order – what needs doing first (queue, today's attendance, expiries, probations, payroll),
    # then the state of the workforce, then the modules (the user can still move cards; the admin can save a company layout)
    cards.sort(key=lambda x: ORDER.index(x['key']) if x['key'] in ORDER else len(ORDER))
    return {'cards': cards, 'today': today, 'period': {'from': p_from, 'to': p_to, 'preset': preset, 'label': P.label(p_from, p_to, today)}}


ORDER = ['queue', 'attendance', 'documents', 'probation', 'payroll', 'movement', 'leavers', 'headcount', 'leave', 'overtime', 'expenses',   # v1.17.1
         'policies', 'tickets', 'assets', 'recruitment', 'performance', 'learning']


def _nz(k):
    """v1.17.1: a zero with nothing behind it is not clickable."""
    if not k.get('value'):
        k['link'] = None
    return k


def my_space(request):
    """The logged-in user's own items for the main dashboard (any user linked to an employee)."""
    from .services import ess_summary
    from . import period as P
    try:
        a, b, _p = P.resolve(request.query_params, timezone.localdate())   # v1.16.0: "My pay" follows the dashboard period
        d = ess_summary(request.user, a, b)
    except Exception:
        logger.exception('my space failed')
        return {'employee': None}
    if not d.get('employee'):
        return {'employee': None}
    keep = ('employee', 'leave_balances', 'next_leave', 'payslip', 'loans', 'air_ticket', 'assets', 'documents', 'requests', 'performance', 'training')
    return {k: d.get(k) for k in keep}
