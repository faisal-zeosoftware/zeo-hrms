"""AI Workforce Analytics – headcount forecast, salary budget and overtime forecast, turnover/absenteeism hot-spots, recruitment
needs from planned growth, cost comparison across branches/departments/companies, natural-language questions."""
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Count, Q, Sum

M = apps.get_model


def _month_add(d, n):
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _months(n, end=None):
    end = end or date.today().replace(day=1)
    return [_month_add(end, -i) for i in range(n - 1, -1, -1)]


def _cfg():
    return M('ZeoAI', 'AIAssistantSettings').get()


def _emp_q(branch_id=None, department_id=None):
    q = M('EmpManagement', 'emp_master').objects.all()
    if branch_id:
        q = q.filter(emp_branch_id=branch_id)
    if department_id:
        q = q.filter(emp_dept_id=department_id)
    return q


def _leavers():
    """{employee_id: last_working_date} from resignations/terminations."""
    R = M('EmpManagement', 'EmployeeResignation')
    out = {}
    for r in R.objects.exclude(last_working_date=None).values('employee_id', 'last_working_date'):
        out[r['employee_id']] = r['last_working_date']
    return out


# ---------------------------------------------------------------- headcount
def headcount(branch_id=None, department_id=None, months=None):
    """Month-end headcount for the past 12 months from joined/left dates, joiners & leavers, and a forecast from the trend."""
    cfg = _cfg()
    months = months or cfg.forecast_months
    emps = list(_emp_q(branch_id, department_id).values('id', 'emp_joined_date', 'is_active'))
    left = _leavers()
    hist = []
    for m in _months(12):
        end = _month_add(m, 1) - timedelta(days=1)
        n = sum(1 for e in emps if e['emp_joined_date'] and e['emp_joined_date'] <= end and (e['id'] not in left or left[e['id']] > end) and (e['is_active'] or e['id'] in left))
        j = sum(1 for e in emps if e['emp_joined_date'] and m <= e['emp_joined_date'] <= end)
        l = sum(1 for d in left.values() if m <= d <= end)
        hist.append({'month': m.strftime('%Y-%m'), 'headcount': n, 'joiners': j, 'leavers': l})
    # trend: average monthly net change over the last 6 months
    net = [h['joiners'] - h['leavers'] for h in hist[-6:]]
    slope = sum(net) / max(1, len(net))
    avg_j = sum(h['joiners'] for h in hist[-6:]) / 6
    avg_l = sum(h['leavers'] for h in hist[-6:]) / 6
    cur = hist[-1]['headcount']
    known_leaving = sum(1 for d in left.values() if d > date.today())
    fc = []
    n = cur
    for i, m in enumerate(_month_add(date.today().replace(day=1), k) for k in range(1, months + 1)):
        n = max(0, n + slope)
        fc.append({'month': m.strftime('%Y-%m'), 'headcount': round(n), 'joiners': round(avg_j, 1), 'leavers': round(avg_l, 1)})
    notes = [f'Current headcount {cur}; average {avg_j:.1f} joiners and {avg_l:.1f} leavers per month over the last 6 months, net {slope:+.1f}/month.']
    if known_leaving:
        notes.append(f'{known_leaving} employee(s) already have a last working date in the future.')
    return {'history': hist, 'forecast': fc, 'current': cur, 'avg_joiners': round(avg_j, 1), 'avg_leavers': round(avg_l, 1), 'known_leaving': known_leaving, 'notes': notes}


# ---------------------------------------------------------------- cost
def _gross_by_employee(branch_id=None, department_id=None):
    """Monthly gross per active employee from the salary structure (additions − nothing; deductions are not cost)."""
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    q = ESS.objects.filter(is_active=True, employee__is_active=True, component__component_type='addition')
    if branch_id:
        q = q.filter(employee__emp_branch_id=branch_id)
    if department_id:
        q = q.filter(employee__emp_dept_id=department_id)
    out = defaultdict(Decimal)
    for r in q.values('employee_id', 'amount'):
        out[r['employee_id']] += Decimal(r['amount'] or 0)
    return out


def _run_totals(months=12, branch_id=None, department_id=None):
    """Actual payroll per month from processed/approved/paid runs: gross, net, overtime amount, payslips."""
    Payslip = M('PayrollManagement', 'Payslip')
    PC = M('PayrollManagement', 'PayslipComponent')
    rows = {}
    for m in _months(months):
        ps = Payslip.objects.filter(payroll_run__month=m.month, payroll_run__year=m.year).exclude(payroll_run__status='pending')
        if branch_id:
            ps = ps.filter(employee__emp_branch_id=branch_id)
        if department_id:
            ps = ps.filter(employee__emp_dept_id=department_id)
        agg = ps.aggregate(g=Sum('gross_salary'), n=Sum('net_salary'), c=Count('id'))
        ot = PC.objects.filter(payslip__in=ps, component__payroll_category='overtime').aggregate(s=Sum('amount'))['s'] or 0
        rows[m.strftime('%Y-%m')] = {'month': m.strftime('%Y-%m'), 'gross': float(agg['g'] or 0), 'net': float(agg['n'] or 0), 'overtime': float(ot), 'payslips': agg['c'] or 0}
    return rows


def salary_budget(branch_id=None, department_id=None, months=None, growth_pct=0.0, increment_pct=0.0, increment_month=None):
    """Forecast monthly salary cost: current structure × forecast headcount, plus planned increment and growth; overtime from the run history."""
    cfg = _cfg()
    months = months or cfg.forecast_months
    gross = _gross_by_employee(branch_id, department_id)
    base = float(sum(gross.values()))
    n_now = max(1, len(gross))
    avg = base / n_now
    hc = headcount(branch_id, department_id, months)
    actual = _run_totals(12, branch_id, department_id)
    ot_hist = [r['overtime'] for r in actual.values() if r['payslips']]
    ot_avg = sum(ot_hist[-6:]) / max(1, len(ot_hist[-6:])) if ot_hist else 0.0
    ot_slope = ((ot_hist[-1] - ot_hist[-4]) / 3) if len(ot_hist) >= 4 else 0.0
    out, n = [], len(gross)
    for i, f in enumerate(hc['forecast']):
        n = f['headcount'] * (1 + growth_pct / 100) ** ((i + 1) / 12) if growth_pct else f['headcount']
        inc = 1 + increment_pct / 100 if (increment_pct and increment_month and f['month'] >= increment_month) else 1
        sal = n * avg * inc
        ot = max(0.0, ot_avg + ot_slope * (i + 1))
        out.append({'month': f['month'], 'headcount': round(n), 'salary': round(sal, 2), 'overtime': round(ot, 2), 'total': round(sal + ot, 2)})
    return {'current_monthly_gross': round(base, 2), 'employees_with_salary': len(gross), 'average_gross': round(avg, 2), 'overtime_avg': round(ot_avg, 2),
            'actual': list(actual.values()), 'forecast': out, 'annual_forecast': round(sum(r['total'] for r in out) * 12 / max(1, len(out)), 2),
            'assumptions': [f'Average gross per employee {avg:,.0f} from the active salary structures.', f'Headcount follows the 6-month trend ({hc["notes"][0]})',
                            f'Overtime average {ot_avg:,.0f}/month over the last processed runs' + (f', trending {ot_slope:+,.0f}/month' if ot_slope else '') + '.',
                            *( [f'Planned growth {growth_pct:g}% per year.'] if growth_pct else []), *( [f'Increment {increment_pct:g}% from {increment_month}.'] if increment_pct else [])]}


# ---------------------------------------------------------------- turnover & absenteeism
def hotspots(months=12):
    """Departments with turnover above the alert % (annualised leavers / average headcount) or absence above the alert % of working days."""
    cfg = _cfg()
    Emp = M('EmpManagement', 'emp_master')
    AD = M('AttendancePlus', 'AttendanceDay')
    left = _leavers()
    since = _month_add(date.today().replace(day=1), -months)
    depts = {}
    for e in Emp.objects.select_related('emp_dept_id', 'emp_branch_id').values('id', 'emp_dept_id', 'emp_dept_id__dept_name', 'emp_branch_id__branch_name', 'is_active'):
        d = depts.setdefault(e['emp_dept_id'] or 0, {'department': e['emp_dept_id__dept_name'] or '—', 'active': 0, 'left': 0, 'ids': []})
        d['ids'].append(e['id'])
        if e['is_active'] and e['id'] not in left:
            d['active'] += 1
        if e['id'] in left and left[e['id']] >= since:
            d['left'] += 1
    a_since = date.today() - timedelta(days=90)
    abs_rows = AD.objects.filter(date__gte=a_since, date__lte=date.today()).values('employee_id', 'status')
    absent, days = defaultdict(int), defaultdict(int)
    for r in abs_rows:
        if r['status'] in ('present', 'absent', 'half_day', 'leave', 'missing_punch', 'not_marked'):
            days[r['employee_id']] += 1
        if r['status'] in ('absent', 'not_marked'):
            absent[r['employee_id']] += 1
        elif r['status'] == 'half_day':
            absent[r['employee_id']] += 0.5
    out = []
    for key, d in depts.items():
        avg_hc = d['active'] + d['left'] / 2
        turnover = round(100 * d['left'] / avg_hc * 12 / months, 1) if avg_hc else 0.0
        wd = sum(days[i] for i in d['ids'])
        ab = sum(absent[i] for i in d['ids'])
        absence = round(100 * ab / wd, 1) if wd else None
        flags = []
        if turnover >= cfg.turnover_alert_pct:
            flags.append(f'turnover {turnover}% (alert {cfg.turnover_alert_pct}%)')
        if absence is not None and absence >= cfg.absence_alert_pct:
            flags.append(f'absence {absence}% of working days (alert {cfg.absence_alert_pct}%)')
        out.append({'department': d['department'], 'department_id': key or None, 'headcount': d['active'], 'leavers': d['left'], 'turnover_pct': turnover, 'absence_pct': absence, 'working_days': wd, 'flags': flags})
    out.sort(key=lambda r: (-(len(r['flags'])), -r['turnover_pct']))
    return {'departments': out, 'months': months, 'absence_window_days': 90, 'alerts': [r for r in out if r['flags']]}


# ---------------------------------------------------------------- recruitment needs
def recruitment_needs(growth_pct=0.0, months=None):
    """Hires needed = growth on current headcount + replacement of expected leavers (trend) + open requisitions − pipeline at offer stage."""
    cfg = _cfg()
    months = months or cfg.forecast_months
    hc = headcount(None, None, months)
    cur = hc['current']
    growth = cur * growth_pct / 100 * months / 12
    replace = hc['avg_leavers'] * months + hc['known_leaving']
    Req = M('RecruitmentManagement', 'ManpowerRequisition')
    Job = M('RecruitmentManagement', 'JobOpening')
    App = M('RecruitmentManagement', 'Application')
    open_req = Req.objects.filter(status__in=('approved', 'open', 'in_progress')).aggregate(n=Sum('headcount'))['n'] or 0
    open_jobs = Job.objects.filter(status__in=('open', 'published')).aggregate(n=Sum('openings'))['n'] or 0
    at_offer = App.objects.filter(stage__in=('offer', 'selected')).count()
    by_dept = defaultdict(lambda: {'active': 0, 'leaving': 0})
    left = _leavers()
    for e in M('EmpManagement', 'emp_master').objects.filter(is_active=True).values('id', 'emp_dept_id__dept_name'):
        by_dept[e['emp_dept_id__dept_name'] or '—']['active'] += 1
        if e['id'] in left and left[e['id']] > date.today():
            by_dept[e['emp_dept_id__dept_name'] or '—']['leaving'] += 1
    rows = [{'department': k, 'headcount': v['active'], 'leaving': v['leaving'], 'growth': round(v['active'] * growth_pct / 100 * months / 12, 1),
             'replacement': round(v['leaving'] + hc['avg_leavers'] * months * v['active'] / max(1, cur), 1)} for k, v in by_dept.items()]
    for r in rows:
        r['hires'] = round(r['growth'] + r['replacement'], 1)
    rows.sort(key=lambda r: -r['hires'])
    total = growth + replace
    return {'months': months, 'current': cur, 'growth_hires': round(growth, 1), 'replacement_hires': round(replace, 1), 'total_hires': round(total, 1),
            'open_requisitions': open_req, 'open_job_openings': open_jobs, 'at_offer_stage': at_offer, 'still_to_open': max(0, round(total - open_jobs, 1)), 'by_department': rows,
            'text': f'Over the next {months} months about {total:.0f} hires are needed: {growth:.0f} for {growth_pct:g}% growth and {replace:.0f} to replace leavers '
                    f'({hc["avg_leavers"]} per month on trend plus {hc["known_leaving"]} already resigning). {open_jobs} openings are published and {at_offer} candidates are at offer stage.'}


# ---------------------------------------------------------------- cost comparison
def compare(by='branch', user=None):
    """Workforce cost per branch / department (this company) or per company (group access through ZeoGroup)."""
    if by == 'company':
        return _compare_companies(user)
    Emp = M('EmpManagement', 'emp_master')
    gross = _gross_by_employee()
    f = 'emp_branch_id__branch_name' if by == 'branch' else 'emp_dept_id__dept_name'
    rows = defaultdict(lambda: {'headcount': 0, 'gross': 0.0})
    for e in Emp.objects.filter(is_active=True).values('id', f):
        r = rows[e[f] or '—']
        r['headcount'] += 1
        r['gross'] += float(gross.get(e['id'], 0))
    last = _run_totals(3)
    ot_share = {}
    PC = M('PayrollManagement', 'PayslipComponent')
    for m in list(last)[-1:]:
        y, mo = map(int, m.split('-'))
        q = PC.objects.filter(payslip__payroll_run__year=y, payslip__payroll_run__month=mo, component__payroll_category='overtime').values(f'payslip__employee__{f}').annotate(s=Sum('amount'))
        for r in q:
            ot_share[r[f'payslip__employee__{f}'] or '—'] = float(r['s'] or 0)
    out = []
    tot = sum(r['gross'] for r in rows.values()) or 1
    for k, r in rows.items():
        out.append({'name': k, 'headcount': r['headcount'], 'monthly_gross': round(r['gross'], 2), 'annual': round(r['gross'] * 12, 2), 'average': round(r['gross'] / max(1, r['headcount']), 2),
                    'overtime_last_month': round(ot_share.get(k, 0.0), 2), 'share_pct': round(100 * r['gross'] / tot, 1)})
    out.sort(key=lambda r: -r['monthly_gross'])
    return {'by': by, 'rows': out, 'total_monthly': round(tot, 2), 'total_headcount': sum(r['headcount'] for r in out)}


def _compare_companies(user):
    try:
        from ZeoGroup.scope import groups_for
        from django_tenants.utils import schema_context
    except Exception:
        return {'by': 'company', 'rows': [], 'error': 'Group features are not available.'}
    groups = [g for g in groups_for(user) if g['features']] if user is not None else []
    if not groups:
        return {'by': 'company', 'rows': [], 'error': 'You have no group access, so only this company can be compared. Group dashboards are part of Premium/Enterprise.'}
    rows = []
    for comp in groups[0]['companies']:
        try:
            with schema_context(comp.schema_name):
                gross = _gross_by_employee()
                n = M('EmpManagement', 'emp_master').objects.filter(is_active=True).count()
                g = float(sum(gross.values()))
                last = list(_run_totals(1).values())[-1]
                rows.append({'name': getattr(comp, 'name', comp.schema_name), 'schema': comp.schema_name, 'headcount': n, 'monthly_gross': round(g, 2), 'annual': round(g * 12, 2),
                             'average': round(g / max(1, n), 2), 'overtime_last_month': last['overtime'], 'last_run_gross': last['gross']})
        except Exception as ex:
            rows.append({'name': getattr(comp, 'name', comp.schema_name), 'schema': comp.schema_name, 'error': str(ex)[:120]})
    tot = sum(r.get('monthly_gross', 0) for r in rows) or 1
    for r in rows:
        r['share_pct'] = round(100 * r.get('monthly_gross', 0) / tot, 1)
    rows.sort(key=lambda r: -r.get('monthly_gross', 0))
    return {'by': 'company', 'group': groups[0]['name'], 'rows': rows, 'total_monthly': round(tot, 2), 'total_headcount': sum(r.get('headcount', 0) for r in rows)}


# ---------------------------------------------------------------- natural language
def _pct(q, default=0.0):
    m = re.search(r'(\d+(?:\.\d+)?)\s*%', q)
    return float(m.group(1)) if m else default


def _months_in(q, default):
    m = re.search(r'(\d{1,2})\s*months?', q)
    if m:
        return int(m.group(1))
    if re.search(r'\byear\b|12 months|next year', q):
        return 12
    if 'quarter' in q:
        return 3
    return default


def answer(question, user=None):
    """Route a workforce question to the right analysis and return (markdown, data, kind) – or None when it is not a workforce question."""
    q = (question or '').lower()
    cfg = _cfg()
    if re.search(r'turnover|attrition|absentee|absence|leav(ing|ers) most|resign', q) and re.search(r'department|which|high|most|hot|where', q):
        h = hotspots()
        top = h['alerts'] or h['departments'][:3]
        md = '**Turnover and absence by department**\n\n| Department | Headcount | Leavers (12 m) | Turnover | Absence |\n|---|---|---|---|---|\n' + '\n'.join(
            f"| {r['department']} | {r['headcount']} | {r['leavers']} | {r['turnover_pct']}% | {r['absence_pct'] if r['absence_pct'] is not None else '—'}% |" for r in h['departments'][:10])
        md += ('\n\n**Alerts:** ' + '; '.join(f"{r['department']}: {', '.join(r['flags'])}" for r in h['alerts'])) if h['alerts'] else '\n\nNo department is above the alert levels.'
        return md, h, 'hotspots'
    if re.search(r'compare|per branch|by branch|per department|by department|per company|by company|across|cost of|workforce cost|which branch|which department', q) and re.search(r'cost|salary|payroll|expensive|spend|gross', q):
        by = 'company' if re.search(r'compan', q) else 'department' if re.search(r'department', q) else 'branch'
        c = compare(by, user)
        if c.get('error'):
            return c['error'], c, 'compare'
        md = f'**Workforce cost by {by}** (monthly gross from salary structures)\n\n| {by.title()} | Headcount | Monthly gross | Average | Share |\n|---|---|---|---|---|\n' + '\n'.join(
            f"| {r['name']} | {r.get('headcount', 0)} | {r.get('monthly_gross', 0):,.0f} | {r.get('average', 0):,.0f} | {r.get('share_pct', 0)}% |" for r in c['rows'][:15])
        md += f"\n\nTotal {c['total_monthly']:,.0f} per month for {c['total_headcount']} employees."
        return md, c, 'compare'
    if re.search(r'budget|salary cost|payroll cost|overtime (cost|spend|forecast)|forecast.*(salary|payroll|overtime)|(salary|payroll|overtime).*forecast|how much.*(salary|payroll)', q):
        months = _months_in(q, cfg.forecast_months)
        growth = _pct(q) if 'grow' in q else 0.0
        inc = _pct(q) if 'increment' in q or 'increase' in q or 'raise' in q else 0.0
        b = salary_budget(months=months, growth_pct=growth, increment_pct=inc, increment_month=_month_add(date.today().replace(day=1), 1).strftime('%Y-%m') if inc else None)
        md = f'**Salary budget forecast – next {months} months**\n\n| Month | Headcount | Salary | Overtime | Total |\n|---|---|---|---|---|\n' + '\n'.join(
            f"| {r['month']} | {r['headcount']} | {r['salary']:,.0f} | {r['overtime']:,.0f} | {r['total']:,.0f} |" for r in b['forecast'])
        md += f"\n\nAnnualised {b['annual_forecast']:,.0f}. " + ' '.join(b['assumptions'])
        return md, b, 'budget'
    if re.search(r'hire|hiring|recruit|vacanc|how many (people|staff|employees) (do we|will we) need', q):
        months = _months_in(q, cfg.forecast_months)
        r = recruitment_needs(growth_pct=_pct(q), months=months)
        md = '**Recruitment needs**\n\n' + r['text'] + '\n\n| Department | Headcount | Leaving | Growth | Replacement | Hires |\n|---|---|---|---|---|---|\n' + '\n'.join(
            f"| {d['department']} | {d['headcount']} | {d['leaving']} | {d['growth']} | {d['replacement']} | {d['hires']} |" for d in r['by_department'][:10])
        return md, r, 'recruitment'
    if re.search(r'headcount|staffing|how many (employees|staff|people)|workforce (size|forecast|trend)|manpower', q):
        months = _months_in(q, cfg.forecast_months)
        h = headcount(months=months)
        md = f'**Headcount** – now {h["current"]}. ' + ' '.join(h['notes']) + '\n\n| Month | Headcount | Joiners | Leavers |\n|---|---|---|---|\n' + '\n'.join(
            f"| {r['month']} | {r['headcount']} | {r['joiners']} | {r['leavers']} |" for r in h['history'][-6:] + h['forecast'])
        return md, h, 'headcount'
    return None
