"""v1.68.0 – the reports of the payroll extras (registered in the report centre by DashboardManagement.reports):
  commission          commission per employee and month: plan, sales, target, commission, status
  retention-bonus     retention bonuses: amount, paid month, clawback period and what an exit today would repay
  service-charge      service charge shares per month, branch and employee
  iloe-status         ILOE: category, policy, expiry, the missing / expired / expiring ones
  emiratisation       Emiratis, skill level, Nafis registration and salary support
  cost-to-company     monthly and yearly cost per employee (salary, employer contributions, accruals)
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from datetime import date
from decimal import Decimal

from django.apps import apps

M = apps.get_model


def _R():
    from DashboardManagement import reports as R
    return R


def _emps(run):
    return {e.id: e for e in run.all_employees}


def r_commission(run):
    R = _R()
    emps = _emps(run)
    qs = M('PayrollExtras', 'CommissionEntry').objects.filter(employee_id__in=list(emps)).select_related('plan')
    if run.date_from:
        qs = qs.filter(month__gte=run.date_from.replace(day=1))
    if run.date_to:
        qs = qs.filter(month__lte=run.date_to)
    rows = [{**R.emp_cols(emps[x.employee_id]), 'month': x.month.strftime('%b %Y'), 'plan': x.plan.name, 'sales': float(x.sales),
             'target': float(x.target) if x.target is not None else None, 'commission': float(x.commission), 'status': x.get_status_display(), **R.src(x)}
            for x in qs]
    cols = R.EMP_COLS + [('month', 'Month', 'text'), ('plan', 'Plan', 'text'), ('sales', 'Sales (AED)', 'number'), ('target', 'Target (AED)', 'number'),
                         ('commission', 'Commission (AED)', 'number'), ('status', 'Status', 'text')]
    return cols, rows


def r_retention(run):
    R = _R()
    from . import services as S
    emps = _emps(run)
    today = _zt_today()
    rows = []
    for x in M('PayrollExtras', 'RetentionBonus').objects.filter(employee_id__in=list(emps)):
        amt, _served = S.clawback_of(x, today)
        rows.append({**R.emp_cols(emps[x.employee_id]), 'award_date': x.award_date.isoformat(), 'pay_month': x.pay_month.strftime('%b %Y'),
                     'amount': float(x.amount), 'clawback_months': x.clawback_months, 'basis': x.get_clawback_basis_display(), 'status': x.get_status_display(),
                     'exit_today': float(amt), 'clawed': float(x.clawed_amount), **R.src(x)})
    cols = R.EMP_COLS + [('award_date', 'Awarded', 'date'), ('pay_month', 'Paid in', 'text'), ('amount', 'Bonus (AED)', 'number'),
                         ('clawback_months', 'Clawback months', 'number'), ('basis', 'Repaid', 'text'), ('status', 'Status', 'text'),
                         ('exit_today', 'Repaid on an exit today (AED)', 'number'), ('clawed', 'Clawed back (AED)', 'number')]
    return cols, rows, {'notes': ['Repaid on an exit today: what the clawback would be if the employee left today (0 after the clawback period).']}


def r_service(run):
    R = _R()
    emps = _emps(run)
    bn = dict(M('OrganisationManager', 'brnch_mstr').objects.values_list('pk', 'branch_name'))
    qs = M('PayrollExtras', 'ServiceChargePool').objects.all()
    if run.date_from:
        qs = qs.filter(month__gte=run.date_from.replace(day=1))
    if run.date_to:
        qs = qs.filter(month__lte=run.date_to)
    rows = []
    for p in qs:
        for s in p.shares or []:
            e = emps.get(s['employee_id'])
            if e is None:
                continue
            rows.append({**R.emp_cols(e), 'month': p.month.strftime('%b %Y'), 'pool_branch': bn.get(p.branch_id, ''), 'pool': float(p.amount),
                         'method': p.get_method_display(), 'weight': s['weight'], 'share': s['amount'], 'status': p.get_status_display(), **R.src(p)})
    cols = R.EMP_COLS + [('month', 'Month', 'text'), ('pool_branch', 'Pool of', 'text'), ('pool', 'Pool (AED)', 'number'), ('method', 'Shared', 'text'),
                         ('weight', 'Points / days', 'number'), ('share', 'Share (AED)', 'number'), ('status', 'Status', 'text')]
    return cols, rows


def r_iloe(run):
    R = _R()
    from . import services as S
    emps = _emps(run)
    recs = {r.employee_id: r for r in M('PayrollExtras', 'ILOERecord').objects.all()}
    lab = {'missing': 'Not subscribed', 'expired': 'Expired', 'expiring': 'Expires within 30 days', '': 'OK'}
    rows = []
    for e in emps.values():
        x = S.iloe_row(e, recs.get(e.pk))
        rows.append({**R.emp_cols(e), 'basic': x['basic'], 'category': x['category'], 'premium': x['premium'], 'status': dict(M('PayrollExtras', 'ILOERecord').STATUS)
                     .get(x['status'], x['status']), 'policy_no': x['policy_no'], 'expiry_date': x['expiry_date'], 'paid_by': x['paid_by'],
                     'attention': lab.get(x['flag'], x['flag']), **R.src(recs.get(e.pk) or e)})
    cols = R.EMP_COLS + [('basic', 'Basic salary (AED)', 'number'), ('category', 'Category', 'text'), ('premium', 'Premium a month (AED)', 'number'),
                         ('status', 'Status', 'text'), ('policy_no', 'Policy', 'text'), ('expiry_date', 'Expires', 'date'), ('paid_by', 'Paid by', 'text'),
                         ('attention', 'Attention', 'text')]
    return cols, rows, {'notes': ['Category A: basic salary up to AED 16,000 (AED 5 a month); B: above (AED 10). Not subscribing: AED 400 fine.']}


def r_emiratisation(run):
    R = _R()
    from . import services as S
    emps = _emps(run)
    P = {p.employee_id: p for p in M('PayrollExtras', 'EmiratisationProfile').objects.all()}
    rows = []
    for e in emps.values():
        p = P.get(e.pk)
        nat = S.is_uae_national(e)
        rows.append({**R.emp_cols(e), 'national': 'Yes' if nat else 'No', 'skill_level': p.skill_level if p else 0,
                     'nafis': 'Yes' if p and p.nafis_registered else 'No', 'nafis_no': p.nafis_no if p else '', 'support': 'Yes' if p and p.salary_support else 'No',
                     'support_until': p.support_until.isoformat() if p and p.support_until else '', **R.src(p or e)})
    q = S.quota(list(emps.values()))
    cols = R.EMP_COLS + [('national', 'UAE national', 'text'), ('skill_level', 'Skill level', 'number'), ('nafis', 'Nafis registered', 'text'),
                         ('nafis_no', 'Nafis no.', 'text'), ('support', 'Salary support', 'text'), ('support_until', 'Support until', 'date')]
    return cols, rows, {'notes': [f"Skilled workforce {q['skilled']}, Emiratis in skilled jobs {q['nationals_skilled']} ({q['share_pct']} %), target "
                                  f"{q['target_pct']} % = {q['required']}, still to hire {q['gap']}; contribution AED {q['exposure_month']:,.2f} a month."]}


def r_ctc(run):
    R = _R()
    from . import services as S
    emps = _emps(run)
    rows = []
    for e in emps.values():
        c = S.ctc(e)
        er = sum((Decimal(str(x['month'])) for x in c['employer']), Decimal(0))
        rows.append({**R.emp_cols(e), 'gross': c['gross'], 'employer': float(er), 'month': c['total_month'], 'year': c['total_year'],
                     'actual_gross': c['actual']['gross'], 'payslips': c['actual']['payslips'], **R.src(e)})
    cols = R.EMP_COLS + [('gross', 'Fixed salary a month (AED)', 'number'), ('employer', 'Employer cost a month (AED)', 'number'),
                         ('month', 'Cost to company a month (AED)', 'number'), ('year', 'Cost to company a year (AED)', 'number'),
                         ('actual_gross', 'Paid gross, last 12 months (AED)', 'number'), ('payslips', 'Payslips', 'number')]
    return cols, rows, {'notes': ['Employer cost: the employer share of the statutory schemes, the gratuity accrual (expatriates: 21 days\' basic a year for '
                                  'the first 5 years, 30 after – an estimate) and the leave salary accrual (30 days\' basic a year).']}


def register(REPORTS, REPORT_MODELS):
    PAY = ['view_payslip', 'view_payrollrun']
    REPORTS.update({
        'commission': ('Commission', 'Payroll', 'Commission per employee and month: plan, sales, target and commission (Payroll › Payroll extras).',
                       r_commission, 'year', PAY),
        'retention-bonus': ('Retention bonuses', 'Payroll', 'Retention bonuses: amount, paid month, clawback period and what an exit today would repay.',
                            r_retention, None, PAY),
        'service-charge': ('Service charge shares', 'Payroll', 'The service charge pool of each month and branch and each employee\'s share.',
                           r_service, 'year', PAY),
        'iloe-status': ('ILOE unemployment insurance', 'Payroll', 'ILOE category, policy and expiry of every employee – the missing, expired and expiring.',
                        r_iloe, None, PAY),
        'emiratisation': ('Emiratisation and Nafis', 'Payroll', 'UAE nationals, skill levels, Nafis registration and salary support, and the quota of the '
                          'company.', r_emiratisation, None, PAY),
        'cost-to-company': ('Cost to company', 'Payroll', 'Monthly and yearly cost per employee: fixed salary, employer contributions and accruals, and '
                            'the gross paid in the last 12 months.', r_ctc, None, PAY),
    })
    REPORT_MODELS.update({k: set() for k in ('commission', 'retention-bonus', 'service-charge', 'iloe-status', 'emiratisation', 'cost-to-company')})
