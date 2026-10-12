"""
v1.18.0 – PayrollPlus reports for the report centre (DashboardManagement/reports.py conventions: `def r_x(run): return columns, rows`).

* wps-status        – every payroll run: salary month, payment date, WPS due date, late / on time, who prepared, approved and paid
* final-settlements – final settlements with service, gratuity, net, due date (14 days) and payment
"""
from datetime import timedelta

from django.apps import apps

from . import services as S


def _h():
    from DashboardManagement import reports as R
    return R


def r_wps_status(run):
    R = _h()
    PR = apps.get_model('PayrollManagement', 'PayrollRun')
    PS = apps.get_model('PayrollManagement', 'Payslip')
    s = S.settings()
    qs = PR.objects.prefetch_related('branch').order_by('-year', '-month', 'id')
    rows = []
    for r in qs:
        me = S.month_end(r.year, r.month)
        if run.date_from and me < run.date_from or run.date_to and me.replace(day=1) > run.date_to:
            continue
        bids = [b.pk for b in r.branch.all()]
        if run.branches is not None and bids and not set(bids) & set(run.branches):
            continue
        c = S.control_for(r)
        due = me + timedelta(days=s.wps_late_days)
        slips = PS.objects.filter(payroll_run=r)
        paid_on = c.paid_at.date() if c and c.paid_at else None
        st = str(r.status or '').lower()
        if st == 'paid':
            state = 'Paid late' if (paid_on and paid_on > due) or (r.payment_date and r.payment_date > due) else 'Paid on time'
        else:
            state = 'Overdue' if run.today > due else ('Payment date after the WPS deadline' if r.payment_date and r.payment_date > due else 'Not paid yet')
        rows.append({'run': r.name, 'month': me.strftime('%b %Y'), 'window': f'{r.attendance_start_date:%d/%m} – {r.attendance_end_date:%d/%m}' if r.attendance_start_date and r.attendance_end_date else '',
                     'employees': slips.count(), 'net': R.money(sum(float(x or 0) for x in slips.values_list('net_salary', flat=True))),
                     'payment_date': r.payment_date or '', 'due': due, 'status': R.nice(r.status), 'state': state,
                     'prepared_by': S.user_name(c.prepared_by_id) if c else '', 'approved_by': S.user_name(c.approved_by_id) if c else '',
                     'paid_by': S.user_name(c.paid_by_id) if c else '', 'paid_on': paid_on or '', 'payment_ref': (c.payment_ref if c else '') or '', **R.src(r)})
    return [('run', 'Payroll run', 'text'), ('month', 'Salary month', 'text'), ('window', 'Attendance window', 'text'), ('employees', 'Employees', 'number'),
            ('net', 'Net pay (AED)', 'number'), ('payment_date', 'Payment date', 'date'), ('due', 'WPS due by', 'date'), ('status', 'Run status', 'text'),
            ('state', 'WPS', 'text'), ('prepared_by', 'Prepared by', 'text'), ('approved_by', 'Approved by', 'text'), ('paid_by', 'Paid by', 'text'),
            ('paid_on', 'Paid on', 'date'), ('payment_ref', 'Payment reference', 'text')], rows


def r_final_settlements(run):
    R = _h()
    X = apps.get_model('PayrollPlus', 'ExitSettlement')
    E = apps.get_model('EmpManagement', 'emp_master')
    rows = []
    for x in X.objects.exclude(status='cancelled').order_by('-last_working_date'):
        e = E.objects.filter(pk=x.employee_id).select_related('emp_branch_id', 'emp_dept_id', 'emp_desgntn_id').first()
        if e is None or (run.branches is not None and e.emp_branch_id_id not in run.branches):
            continue
        if run.date_from and x.last_working_date and x.last_working_date < run.date_from and x.status == 'paid':
            continue
        r = S.settlement_row(x, full=True)
        grat = sum(l['amount'] for l in (x.lines or []) if l['key'] == 'gratuity')
        rows.append({**R.emp_cols(e), 'last_day': x.last_working_date, 'exit': x.exit_type, 'years': r['service_years'], 'unpaid_days': float(x.unpaid_days),
                     'gratuity': R.money(grat), 'earnings': R.money(x.total_earnings), 'deductions': R.money(x.total_deductions), 'net': R.money(x.net),
                     'status': x.get_status_display(), 'due': x.due_date or '', 'paid_on': x.paid_on or '',
                     'state': 'Paid' if x.status == 'paid' else ('Overdue' if r['overdue'] else 'Open'), **R.src(x)})
    return R.EMP_COLS + [('last_day', 'Last working day', 'date'), ('exit', 'Exit', 'text'), ('years', 'Eligible years', 'number'),
                         ('unpaid_days', 'Unpaid days', 'number'), ('gratuity', 'Gratuity (AED)', 'number'), ('earnings', 'Earnings (AED)', 'number'),
                         ('deductions', 'Deductions (AED)', 'number'), ('net', 'Net (AED)', 'number'), ('status', 'Status', 'text'),
                         ('due', 'Due by (14 days)', 'date'), ('paid_on', 'Paid on', 'date'), ('state', 'Payment', 'text')], rows


REPORTS = {
    'wps-status': ('WPS payment status', 'Payroll', 'Every payroll run with salary month, payment date, the WPS due date, late or on time, and who prepared, approved and paid it.',
                   r_wps_status, 'year', ['view_payrollrun']),
    'final-settlements': ('Final settlements', 'Requests & exits', 'Final settlements with eligible service, gratuity, net amount, the 14-day due date and payment.',
                          r_final_settlements, 'year', ['view_exitsettlement', 'view_endofservice']),
}
REPORT_MODELS = {'wps-status': {'PayrollManagement.PayrollRun'}, 'final-settlements': {'EmpManagement.emp_master'}}
