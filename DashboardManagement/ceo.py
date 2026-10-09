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
    d_to = min(run.date_to or today, today)
    m_end = d_to.replace(day=1)
    months12 = R._months((m_end - timedelta(days=330)).replace(day=1), d_to)[-12:]
    months6 = months12[-6:]
    d_from = run.date_from or months12[0]
    span = {'from': d_from, 'to': d_to}
    out = {'period': span, 'generated': today, 'tiles': [], 'sections': {}}
    Branch = _m('OrganisationManager.brnch_mstr')
    bq = Branch.objects.filter(br_is_active=True) if Branch else []
    if c.branches is not None and Branch:
        bq = bq.filter(id__in=c.branches)
    out['branches'] = [{'id': b.id, 'name': b.branch_name} for b in bq] if Branch else []
    out['branch'] = run.branches

    def safe(name, fn):
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
                       'drill': _drill('joiners-leavers', None, m0, R.month_end(m0))} for t, m0 in zip(trend, months12)],
            'joined': joined, 'exits': exits, 'attrition_12m': attr, 'left_12m': left_12, 'avg_headcount_12m': round(avg_12, 1),
            'nationals': sum(1 for e in emps if R.is_national(e)),
        }
        prev_head = trend[-2]['closing'] if len(trend) > 1 else None
        out['tiles'] += [
            {'key': 'headcount', 'label': 'Employees', 'value': head, 'unit': '', 'delta': _pct_delta(head, prev_head), 'delta_label': 'vs last month',
             'spark': [t['closing'] for t in trend], 'drill': _drill('headcount', None, d_from, d_to)},
            {'key': 'movement', 'label': 'Joiners / exits', 'value': f'{joined} / {exits}', 'unit': '', 'note': 'in the period',
             'spark': [t['joined'] - t['left'] for t in trend], 'drill': _drill('joiners-leavers', None, d_from, d_to)},
            {'key': 'attrition', 'label': 'Attrition (12 months)', 'value': attr, 'unit': '%', 'note': f'{left_12} leavers / avg {avg_12:.0f}', 'tone': 'bad' if attr > 15 else '',
             'spark': [t['turnover'] for t in trend], 'drill': R.drill('turnover-trend', None, **{'from': months12[0], 'to': R.month_end(months12[-1])})},
        ]
    safe('people', people)

    # ---- payroll & labour cost (6 months)
    def pay():
        w = _window(run, months6[0], R.month_end(months6[-1]))
        slips = R._payslips(w, components=True)
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
        prev = paid[-2] if len(paid) > 1 else None
        all_emps = [e.id for e in emps]
        basic = R.basic_by_employee(all_emps)
        grat_total = sum(R.gratuity_uae(R.years_between(e.emp_joined_date, today) or 0, basic.get(e.id, 0))[1] for e in emps)
        bal = defaultdict(float)
        for b in apps.get_model('calendars', 'emp_leave_balance').objects.filter(employee_id__in=all_emps, leave_type__leave_category='annual').values('employee_id', 'balance'):
            bal[b['employee_id']] += float(b['balance'] or 0)
        leave_liab = sum(basic.get(i, 0) * 12 / 365 * bal.get(i, 0) for i in all_emps)
        out['sections']['payroll'] = {'months': [dict(m, month=None) for m in months]}
        out['sections']['labour_cost'] = {'latest': dict(last, month=None) if last else None, 'gratuity_liability': round(grat_total, 2),
                                          'leave_liability': round(leave_liab, 2), 'per_employee': last['per_employee'] if last else 0,
                                          'drill_gratuity': _drill('gratuity'), 'drill_leave': _drill('leave-liability')}
        out['_ot_paid'] = ot_paid
        out['tiles'] += [
            {'key': 'payroll', 'label': f"Payroll {last['label']}" if last else 'Payroll', 'value': last['gross'] if last else 0, 'unit': 'AED', 'money': True,
             'note': f"net AED {last['net']:,.0f}" if last else 'no payroll yet', 'delta': _pct_delta(last['gross'], prev['gross']) if last and prev else None,
             'delta_label': 'vs previous month', 'spark': [m['gross'] for m in months],
             'drill': _drill('payroll-register', None, last['month'] if last else months6[0], R.month_end(last['month']) if last else d_to)},
            {'key': 'labour_cost', 'label': 'Labour cost (monthly)', 'value': last['cost'] if last else 0, 'unit': 'AED', 'money': True,
             'note': f"AED {last['per_employee']:,.0f} per employee" if last else '', 'delta': _pct_delta(last['cost'], prev['cost']) if last and prev else None,
             'delta_label': 'vs previous month', 'spark': [m['cost'] for m in months],
             'drill': _drill('payroll-cost', None, last['month'] if last else months6[0], R.month_end(last['month']) if last else d_to)},
            {'key': 'liabilities', 'label': 'Gratuity + leave liability', 'value': round(grat_total + leave_liab, 2), 'unit': 'AED', 'money': True,
             'note': f'gratuity {grat_total:,.0f} · leave {leave_liab:,.0f}', 'drill': _drill('gratuity')},
        ]
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
        cur = rows[-1]
        out['tiles'].append({'key': 'overtime', 'label': f"Overtime {cur['label']}", 'value': cur['hours'], 'unit': 'h',
                             'note': f"AED {cur['cost']:,.0f}{' (estimated)' if cur['estimated'] else ''}", 'delta': _pct_delta(cur['hours'], rows[-2]['hours']),
                             'delta_label': 'vs previous month', 'spark': [r['hours'] for r in rows], 'drill': cur['drill']})
    safe('overtime', overtime)

    # ---- absence (6 months)
    def absence():
        days = R.day_statuses(emps, months6[0], R.month_end(months6[-1]), today)
        cnt = defaultdict(lambda: defaultdict(int))
        for lst in days.values():
            for d in lst:
                cnt[d['date'].replace(day=1)][d['status']] += 1
        rows = []
        for m in months6:
            c_ = cnt[m]
            working = c_['Present'] + c_['Absent'] + c_['On Leave']
            rows.append({'label': _lbl(m), 'absent': c_['Absent'], 'leave': c_['On Leave'], 'working': working,
                         'rate': round(100 * c_['Absent'] / working, 1) if working else 0, 'leave_rate': round(100 * c_['On Leave'] / working, 1) if working else 0,
                         'drill': _drill('absence', None, m, R.month_end(m))})
        out['sections']['absence'] = {'months': rows}
        cur = rows[-1]
        out['tiles'].append({'key': 'absence', 'label': f"Absence rate {cur['label']}", 'value': cur['rate'], 'unit': '%', 'tone': 'bad' if cur['rate'] > 5 else '',
                             'note': f"{cur['absent']} absent days · leave {cur['leave_rate']}%", 'spark': [r['rate'] for r in rows], 'drill': cur['drill']})
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
        out['tiles'].append({'key': 'hiring', 'label': 'Open positions', 'value': sec['open_positions'], 'unit': '',
                             'note': f"{sec['offers_out']} offers out · {sec['hires']} hired · {sec['time_to_hire'] if sec['time_to_hire'] is not None else '–'} days to hire",
                             'drill': sec['drill_open']})
    safe('hiring', hiring)
    out.pop('_ot_paid', None)
    return out


class CeoDashboardView(APIView):
    """GET dashboard/api/ceo/?from&to&branch – executive KPIs (company admins or view_ceo_dashboard)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from AccessControl.access import ctx
        if not can_view(ctx(request)):
            return Response({'detail': 'The CEO dashboard is for company admins and users with the CEO dashboard right.'}, status=status.HTTP_403_FORBIDDEN)
        return Response(ceo_summary(request))
