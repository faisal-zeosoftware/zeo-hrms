"""
CEO / executive dashboard (v1.12).

GET /dashboard/api/ceo/?from=YYYY-MM-DD&to=YYYY-MM-DD&branch=1,2
    → KPI tiles with trends; every figure carries a drill {report, f, from, to} to the report rows behind it.

Who: company admins, or users holding the permission code `view_ceo_dashboard`.
Branch users only see their branches (same rules as the report centre).
Trends end at `to` (default today): 12 months for headcount / attrition, 6 months for pay, cost, overtime and absence.
"""
import copy
import logging
from collections import defaultdict
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Q, Sum
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import reports as R

try:  # v1.15.0 language packs: tile captions in the request language (English when the app is missing)
    from Translations.i18n import tr
except ImportError:  # pragma: no cover
    def tr(text, *args, lang=None, **kw):
        return text.format(*args, **kw) if (args or kw) else text

logger = logging.getLogger(__name__)
CEO_CODE = 'view_ceo_dashboard'


def can_view(c):
    return bool(c.admin or CEO_CODE in c.codes)


def _m(label):
    try:
        return apps.get_model(label)
    except (LookupError, ValueError):
        return None


def _window(run, d_from, d_to):
    w = copy.copy(run)
    w.date_from, w.date_to = d_from, d_to
    return w


def _lbl(m0):
    return m0.strftime('%b %Y')


def _drill(report, f=None, d_from=None, d_to=None):
    return R.drill(report, f, **({'from': d_from, 'to': d_to} if d_from else {}))


def _pct_delta(cur, prev):
    if prev in (None, 0) or cur is None:
        return None
    return round(100 * (cur - prev) / prev, 1)


def ceo_summary(request):
    from AccessControl.access import ctx
    c = ctx(request)
    run = R.Run(request, {'period': None})
    today = run.today
    # v1.16.0: the dashboard period (?period= or ?from=&to=, default this month); the trend charts end with the period
    from .period import resolve, label as p_label
    from django.utils import timezone as _tz
    d_from, d_to_p, preset = resolve(request.query_params, _tz.localdate())
    d_to = min(d_to_p, today) if d_from <= today else d_to_p
    m_end = d_to.replace(day=1)
    months12 = R._months((m_end - timedelta(days=330)).replace(day=1), d_to)[-12:]
    months6 = months12[-6:] if len(R._months(d_from, d_to)) <= 6 else R._months(d_from, d_to)[-24:]
    p_months = R._months(d_from, d_to)
    span = {'from': d_from, 'to': d_to}
    only = request.query_params.get('card') or None
    plabel = p_label(d_from, d_to_p, today)
    # v1.17.0 (audit L5): the period as asked (e.g. 1–31 Oct); figures are counted up to today
    out = {'period': dict(span, to=d_to_p, counted_to=d_to, preset=preset, label=plabel), 'generated': today, 'tiles': [], 'sections': {}}
    Branch = _m('OrganisationManager.brnch_mstr')
    bq = Branch.objects.filter(br_is_active=True) if Branch else []
    if c.branches is not None and Branch:
        bq = bq.filter(id__in=c.branches)
    out['branches'] = [{'id': b.id, 'name': b.branch_name} for b in bq] if Branch else []
    out['branch'] = run.branches

    def safe(name, fn):
        if only and only != name:
            return
        try:
            fn()
        except Exception:
            logger.exception('ceo dashboard section failed: %s', name)
            out['sections'][name] = {'error': True}

    emps = list(run.employees)
    head = len(emps)

    # ---- people: headcount, by branch / department, movement & attrition
    def people():
        trend = R.turnover_months(_window(run, months12[0], R.month_end(months12[-1])), months12)
        left_12 = sum(t['left'] for t in trend)
        avg_12 = sum(t['average'] for t in trend) / len(trend) if trend else 0
        attr = round(100 * left_12 / avg_12, 1) if avg_12 else 0
        by_b, by_d = defaultdict(int), defaultdict(int)
        for e in emps:
            by_b[e.emp_branch_id.branch_name if e.emp_branch_id_id else '(no branch)'] += 1
            by_d[e.emp_dept_id.dept_name if e.emp_dept_id_id else '(no department)'] += 1
        joined = sum(1 for e in run.all_employees if e.emp_joined_date and d_from <= e.emp_joined_date <= d_to)
        left_map = R._left(run)
        exits = sum(1 for v in left_map.values() if v[0] and d_from <= v[0] <= d_to)
        out['sections']['people'] = {
            'headcount': head,
            'by_branch': [{'label': k, 'value': v, 'drill': _drill('employees', {'branch': k, 'status': 'Active'})} for k, v in sorted(by_b.items(), key=lambda x: -x[1])],
            'by_department': [{'label': k, 'value': v, 'drill': _drill('employees', {'department': k, 'status': 'Active'})} for k, v in sorted(by_d.items(), key=lambda x: -x[1])],
            'trend': [{'label': t['month'], 'headcount': t['closing'], 'joined': t['joined'], 'left': t['left'], 'attrition': t['turnover'],
                       'drill': _drill('joiners-leavers', None, m0, R.month_end(m0)),
                       # v1.17.0 (audit M4): a headcount point opens the people employed at that month end (= the value)
                       'drill_head': _drill('headcount-asof', None, m0, R.month_end(m0))}
                      for t, m0 in zip(trend, months12)],
            'drill_trend': R.drill('turnover-trend', None, **{'from': months12[0], 'to': R.month_end(months12[-1])}),   # the 12 months shown
            'joined': joined, 'exits': exits, 'attrition_12m': attr, 'left_12m': left_12, 'avg_headcount_12m': round(avg_12, 1),
            'nationals': sum(1 for e in emps if R.is_national(e)),
        }
        # v1.17.0: current headcount and the joiners / leavers of the period live on HR operations (one home per figure);
        # Management keeps the 12-month trend, attrition and the nationals share.
        nat = out['sections']['people']['nationals']
        # v1.17.0 (audit L5): the sparkline is the 12-month attrition up to each month (its last point = the tile)
        spark, cl, ca = [], 0, 0.0
        for i, t in enumerate(trend):
            cl += t['left']; ca += t['average']
            spark.append(round(100 * cl / (ca / (i + 1)), 2) if ca else 0)
        out['tiles'] += [
            {'key': 'attrition', 'label': tr('Attrition (12 months)'), 'value': attr, 'unit': '%',
             'note': tr('{0} leaver / average headcount {1}' if left_12 == 1 else '{0} leavers / average headcount {1}', left_12, f'{avg_12:.0f}'),
             'tone': 'bad' if attr > 15 else '', 'spark': spark, 'drill': R.drill('turnover-trend', None, **{'from': months12[0], 'to': R.month_end(months12[-1])}),
             'timed': True, 'section': 'people'},
            {'key': 'nationals', 'label': 'Emiratisation', 'value': round(100 * nat / head, 1) if head else 0, 'unit': '%', 'country': 'nationals_reports',
             'note': tr('{0} of {1} employees', nat, head), 'note_zc': True, 'timed': False, 'section': 'people', 'nationals': nat,
             # v1.17.0 (audit M5): opens the nationals themselves (count = the "{0}" of the note)
             'drill': _drill('headcount-asof', {'national': 'Yes'}, today, today), 'measure': {'sum': None, 'field': 'nationals', 'count': True}},
        ]
    safe('people', people)

    # ---- payroll & labour cost (6 months)
    def pay():
        w = _window(run, months6[0], R.month_end(months6[-1]))
        slips = [p for p in R._payslips(w, components=True) if p.payroll_run.status != 'pending']   # v1.15.1: processed runs only
        sb = R.basic_by_employee(list({p.employee_id for p in slips}))
        per = defaultdict(lambda: defaultdict(float))
        ot_paid = defaultdict(float)
        for p in slips:
            k = date(p.payroll_run.year, p.payroll_run.month, 1)
            ec = R.employer_cost(p, sb.get(p.employee_id, 0))
            per[k]['gross'] += ec['gross']
            per[k]['net'] += R.money(p.net_salary)
            per[k]['gratuity'] += ec['gratuity']
            per[k]['leave'] += ec['leave_accrual']
            per[k]['pension'] += ec['pension']
            per[k]['cost'] += ec['cost']
            per[k]['people'] += 1
            for cpt in p.components.all():
                if cpt.component.payroll_category == 'overtime' and cpt.component.component_type == 'addition':
                    ot_paid[k] += R.money(cpt.amount)
        months = [{'label': _lbl(m), 'month': m, **{f: round(per[m][f], 2) for f in ('gross', 'net', 'gratuity', 'leave', 'pension', 'cost')},
                   'people': int(per[m]['people']), 'per_employee': round(per[m]['cost'] / per[m]['people'], 2) if per[m]['people'] else 0,
                   'drill': _drill('payroll-cost', None, m, R.month_end(m))} for m in months6]
        paid = [m for m in months if m['people']]
        last = paid[-1] if paid else None
        in_p = [m for m in months if m['people'] and d_from.replace(day=1) <= m['month'] <= d_to]
        if not in_p:   # months outside the trend window: read the period itself
            wp = _window(run, d_from, d_to)
            in_p = []
            for p in R._payslips(wp, components=True):
                if p.payroll_run.status == 'pending':
                    continue
                ec = R.employer_cost(p, 0)
                in_p.append({'gross': ec['gross'], 'net': R.money(p.net_salary), 'cost': ec['cost'], 'people': 1})
        pg = round(sum(m['gross'] for m in in_p), 2)
        pn = round(sum(m['net'] for m in in_p), 2)
        pc = round(sum(m['cost'] for m in in_p), 2)
        ppl = sum(m['people'] for m in in_p)
        per_emp = round(pc / ppl, 2) if ppl else 0
        prev = None
        if in_p and 'month' in in_p[0]:
            first = in_p[0]['month']
            prev = next((m for m in reversed(months) if m['people'] and m['month'] < first), None)
        all_emps = [e.id for e in emps]
        basic = R.basic_by_employee(all_emps)
        grat_total = sum(R.gratuity_for(e, R.years_between(e.emp_joined_date, today) or 0, basic.get(e.id, 0))[1] for e in emps)
        bal = defaultdict(float)
        for b_ in apps.get_model('calendars', 'emp_leave_balance').objects.filter(employee_id__in=all_emps, leave_type__leave_category='annual').values('employee_id', 'balance'):
            bal[b_['employee_id']] += float(b_['balance'] or 0)
        leave_liab = sum(basic.get(i, 0) * 12 / 365 * bal.get(i, 0) for i in all_emps)
        out['sections']['payroll'] = {'months': [dict(m, month=None) for m in months]}
        out['sections']['labour_cost'] = {'latest': dict(last, month=None) if last else None, 'gratuity_liability': round(grat_total, 2),
                                          'leave_liability': round(leave_liab, 2), 'per_employee': last['per_employee'] if last else 0,
                                          'drill_gratuity': _drill('gratuity'), 'drill_leave': _drill('leave-liability')}
        out['_ot_paid'] = ot_paid
        done = {'run_status': 'Processed|Approved|Paid'}
        n_m = len(in_p) if in_p and 'month' in in_p[0] else 1
        out['tiles'] += [
            {'key': 'payroll', 'label': tr('Payroll (gross)'), 'value': pg, 'unit': 'AED', 'money': True, 'timed': True, 'section': 'payroll',
             'note': tr('net {0} {1}', 'AED', f"{pn:,.0f}") if ppl else tr('no payroll in the period'),
             'delta': _pct_delta(pg / n_m, prev['gross']) if prev and ppl else None, 'delta_label': tr('vs previous month'), 'spark': [m['gross'] for m in months],
             'drill': _drill('payroll-register', done, d_from, d_to), 'measure': {'sum': 'gross'}},
            {'key': 'labour_cost', 'label': tr('Labour cost'), 'value': pc, 'unit': 'AED', 'money': True, 'timed': True, 'section': 'payroll',
             'note': tr('{0} {1} per employee', 'AED', f"{per_emp:,.0f}") if ppl else '',   # v1.17.0 (audit L5): same words as the chart
             'delta': _pct_delta(pc / n_m, prev['cost']) if prev and ppl else None, 'delta_label': tr('vs previous month'), 'spark': [m['cost'] for m in months],
             'drill': _drill('payroll-cost', done, d_from, d_to), 'measure': {'sum': 'cost'}},
            {'key': 'liabilities', 'label': tr('Gratuity + leave liability'), 'value': round(grat_total + leave_liab, 2), 'unit': 'AED', 'money': True,
             'note': tr('gratuity {0} · leave {1}', f'{grat_total:,.0f}', f'{leave_liab:,.0f}'), 'drill': _drill('gratuity'), 'timed': False, 'section': 'payroll',
             'parts_money': [{'key': 'gratuity', 'value': round(grat_total, 2), 'drill': _drill('gratuity'), 'measure': {'sum': 'amount'}},   # v1.17.0
                             {'key': 'leave', 'value': round(leave_liab, 2), 'drill': _drill('leave-liability'), 'measure': {'sum': 'liability'}}]},
        ]
        # v1.17.0: labour cost per branch / department of the period – counted from the payroll cost report it opens
        from .overview import Calc
        cost_rows = Calc.match(Calc(request).rows('payroll-cost', d_from, d_to), done)
        by_b, by_d = defaultdict(float), defaultdict(float)
        for r in cost_rows:
            by_b[r.get('branch') or ''] += float(r.get('cost') or 0)
            by_d[r.get('department') or ''] += float(r.get('cost') or 0)
        out['sections']['cost'] = {
            'total': round(sum(by_b.values()), 2),
            'by_branch': [{'label': k, 'value': round(v, 2), 'drill': _drill('payroll-cost', dict(done, branch=k), d_from, d_to), 'measure': {'sum': 'cost'}}
                          for k, v in sorted(by_b.items(), key=lambda x: -x[1])],
            'by_department': [{'label': k, 'value': round(v, 2), 'drill': _drill('payroll-cost', dict(done, department=k), d_from, d_to), 'measure': {'sum': 'cost'}}
                              for k, v in sorted(by_d.items(), key=lambda x: -x[1])],
        }
    safe('payroll', pay)

    # ---- overtime (6 months)
    def overtime():
        OT = _m('calendars.EmployeeOvertime')
        ids = [e.id for e in run.all_employees]
        basic = R.basic_by_employee(ids)
        agg = defaultdict(lambda: {'hours': 0.0, 'est': 0.0})
        for o in OT.objects.filter(employee_id__in=ids, date__gte=months6[0], date__lte=R.month_end(months6[-1])).values('employee_id', 'date', 'hours', 'ot_type'):
            k = o['date'].replace(day=1)
            h = float(o['hours'] or 0)
            agg[k]['hours'] += h
            agg[k]['est'] += h * R.ot_hourly(basic.get(o['employee_id'], 0)) * R.OT_FACTOR.get(o['ot_type'] or 'NORMAL', 1.25)
        paid = out.pop('_ot_paid', {}) or {}
        rows = [{'label': _lbl(m), 'hours': round(agg[m]['hours'], 2), 'cost': round(paid.get(m) or agg[m]['est'], 2), 'estimated': not paid.get(m),
                 'drill': _drill('overtime-pay', None, m, R.month_end(m))} for m in months6]
        out['sections']['overtime'] = {'months': rows}
        # v1.16.0: the tile is the period (overtime rows dated in it), its drill the overtime list of the period
        ph, pe = 0.0, 0.0
        for o in OT.objects.filter(employee_id__in=ids, date__gte=d_from, date__lte=d_to).values('employee_id', 'hours', 'ot_type'):
            h = float(o['hours'] or 0)
            ph += h
            pe += h * R.ot_hourly(basic.get(o['employee_id'], 0)) * R.OT_FACTOR.get(o['ot_type'] or 'NORMAL', 1.25)
        prev_rows = [r for r, m in zip(rows, months6) if m < d_from.replace(day=1)]
        # v1.17.0: Management shows the overtime COST (pay due by law, from the overtime pay report the tile opens); hours in the note
        from .overview import Calc
        due = round(sum(float(r.get('due') or 0) for r in Calc(request).rows('overtime-pay', d_from, d_to)), 2)
        out['tiles'].append({'key': 'overtime', 'label': tr('Overtime cost'), 'value': due, 'unit': 'AED', 'money': True, 'timed': True, 'section': 'overtime',
                             'note': tr('{0} hours · pay due by law', f"{ph:,.1f}"),
                             'delta': _pct_delta(due, prev_rows[-1]['cost']) if prev_rows and len(p_months) == 1 and prev_rows[-1]['cost'] else None,
                             'delta_label': tr('vs previous month'), 'spark': [r['cost'] for r in rows], 'drill': _drill('overtime-pay', None, d_from, d_to),
                             'measure': {'sum': 'due'}})
    safe('overtime', overtime)

    # ---- v1.17.1 (dash171): expense claims approved in the period (from the expense reports list the tile opens)
    def expenses():
        ER = _m('ExpenseManagement.ExpenseReport')
        if ER is None or not ER.objects.filter(status__in=('approved', 'reimbursed'), employee_id__in=run.all_employees.values('id')).exists():
            return   # no approved expense claims of the user's branches at all: no tile
        from .overview import Calc
        f = {'status': 'Approved|Reimbursed'}
        rows = Calc.match(Calc(request).rows('expense-reports', d_from, d_to), f)
        total = round(sum(float(r.get('total') or 0) for r in rows), 2)
        out['tiles'].append({'key': 'expenses', 'label': tr('Expense claims'), 'value': total, 'unit': 'AED', 'money': True, 'timed': True,
                             'note': tr('{0} reports approved', len(rows)), 'drill': _drill('expense-reports', f, d_from, d_to) if rows else None,
                             'measure': {'sum': 'total'}})   # a zero with nothing behind it opens nothing
    safe('expenses', expenses)

    # ---- absence (6 months)
    def absence():
        # v1.16.0: both attendance sources (attendance_data), like the attendance reports
        from . import attendance_data as AD
        lo = min(months6[0], d_from)
        days = AD.days(emps, lo, max(R.month_end(months6[-1]), d_to), today)
        cnt = defaultdict(lambda: defaultdict(int))
        pc = defaultdict(int)
        for lst in days.values():
            for d in lst:
                cnt[d['date'].replace(day=1)][d['status']] += 1
                if d_from <= d['date'] <= d_to:
                    pc[d['status']] += 1

        def worked(c_):
            return c_['Present'] + c_['Missing punch'] + c_['Half day'] + c_['Absent'] + c_['Leave']
        rows = []
        for m in months6:
            c_ = cnt[m]
            w = worked(c_)
            rows.append({'label': _lbl(m), 'absent': c_['Absent'], 'leave': c_['Leave'], 'working': w,
                         'rate': round(100 * c_['Absent'] / w, 1) if w else 0, 'leave_rate': round(100 * c_['Leave'] / w, 1) if w else 0,
                         'drill': _drill('attendance-summary', None, m, min(R.month_end(m), today))})
        out['sections']['absence'] = {'months': rows}
        w = worked(pc)
        rate = round(100 * pc['Absent'] / w, 1) if w else 0
        out['tiles'].append({'key': 'absence', 'label': tr('Absence rate'), 'value': rate, 'unit': '%', 'tone': 'bad' if rate > 5 else '', 'timed': True,
                             'section': 'absence', 'absent': pc['Absent'],
                             'note': tr('{0} absent days · leave {1}%', pc['Absent'], round(100 * pc['Leave'] / w, 1) if w else 0), 'spark': [r['rate'] for r in rows],
                             'drill': _drill('attendance-summary', None, d_from, d_to), 'measure': {'sum': 'absent', 'field': 'absent'}})
    safe('absence', absence)

    # ---- hiring
    def hiring():
        J = R._jobs(run)
        open_j = J.exclude(status__in=('draft', 'filled', 'closed', 'cancelled', 'on_hold'))
        Offer = _m('RecruitmentManagement.Offer')
        offers = Offer.objects.filter(application__job__in=J) if Offer else None
        w = _window(run, d_from, d_to)
        tth = R.time_to_hire_rows(w)
        vals = [r['days_hire'] for r in tth if r['days_hire'] != '']
        joined = offers.filter(status='joined', joining_date__gte=d_from, joining_date__lte=d_to).count() if offers is not None else 0
        sec = {'open_positions': open_j.aggregate(s=Sum('openings'))['s'] or 0, 'open_jobs': open_j.count(),
               'offers_out': offers.filter(status__in=('pending_approval', 'approved', 'sent')).count() if offers is not None else 0,
               'offers_accepted': offers.filter(status='accepted').count() if offers is not None else 0, 'joined': joined, 'hires': len(tth),
               'time_to_hire': round(sum(vals) / len(vals), 1) if vals else None,
               'drill_open': R.drill('recruitment', {'open': 'Yes'}), 'drill_hires': R.drill('time-to-hire', None, **span)}
        out['sections']['hiring'] = sec
        # v1.17.0: open positions are on HR operations (recruitment card); Management keeps hires and time to hire
    safe('hiring', hiring)
    out.pop('_ot_paid', None)
    return out


class CeoDashboardView(APIView):
    """GET dashboard/api/ceo/?from&to&branch – executive KPIs (company admins or view_ceo_dashboard)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from AccessControl.access import ctx
        if not can_view(ctx(request)):
            return Response({'detail': tr('The CEO dashboard is for company admins and users with the CEO dashboard right.')}, status=status.HTTP_403_FORBIDDEN)
        data = ceo_summary(request)
        try:  # v1.14.0: the company currency instead of AED (country packs)
            from CountryPack.services import swap_currency
            data = swap_currency(data)
        except Exception:
            pass
        try:  # v1.15.0: names (branch, department), months and drill-down filters in the request language (after the country step)
            from Translations.reports import localize_ceo
            data = localize_ceo(data, request)
        except ImportError:
            pass
        return Response(data)
