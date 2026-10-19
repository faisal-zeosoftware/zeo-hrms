"""AI Payroll Auditor – rules with explanations. audit(run, user) → AuditRun with findings.

Every finding says what was compared, the figures, and why it was flagged, so the approver can decide before approval.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import AIAssistantSettings, AuditRun, Finding

M = apps.get_model


def _d(v):
    return Decimal(str(v or 0))


def _pct(prev, cur):
    prev, cur = _d(prev), _d(cur)
    if not prev:
        return None
    return round(float((cur - prev) / abs(prev) * 100), 1)


def emp_name(e):
    return ' '.join(x for x in (getattr(e, 'emp_first_name', ''), getattr(e, 'emp_last_name', '')) if x) or getattr(e, 'emp_code', '')


def previous_run(run):
    PR = M('PayrollManagement', 'PayrollRun')
    y, m = (run.year, run.month - 1) if run.month > 1 else (run.year - 1, 12)
    qs = PR.objects.filter(year=y, month=m).exclude(pk=run.pk)
    # the run with the most common employees
    best, score = None, -1
    Payslip = M('PayrollManagement', 'Payslip')
    mine = set(Payslip.objects.filter(payroll_run=run).values_list('employee_id', flat=True))
    for r in qs:
        s = len(mine & set(Payslip.objects.filter(payroll_run=r).values_list('employee_id', flat=True)))
        if s > score:
            best, score = r, s
    return best


def period_of(run):
    start = run.attendance_start_date or date(run.year, run.month, 1)
    end = run.attendance_end_date or ((date(run.year, run.month, 1) + timedelta(days=32)).replace(day=1) - timedelta(days=1))
    return start, end


class Collector:
    def __init__(self, audit, prev_keys):
        self.audit, self.rows, self.prev = audit, [], prev_keys

    def add(self, rule, severity, title, explanation, employee=None, payslip_id=None, previous=None, current=None, change_pct=None, data=None):
        key = f'{rule}:{getattr(employee, "pk", "") or ""}:{payslip_id or ""}'
        st = self.prev.get(key, 'open')
        self.rows.append(Finding(audit=self.audit, rule=rule, severity=severity, title=title[:200], explanation=explanation, employee=employee, payslip_id=payslip_id,
                                 previous=previous, current=current, change_pct=change_pct, data=data or {}, key=key, status=st if st in ('acknowledged', 'dismissed') else 'open'))


def audit(run, user=None):
    """Runs every rule on the payroll run; keeps acknowledgements / dismissals of the previous audit of the same run."""
    s = AIAssistantSettings.get()
    Payslip = M('PayrollManagement', 'Payslip')
    PC = M('PayrollManagement', 'PayslipComponent')
    ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
    prev = previous_run(run)
    last = AuditRun.objects.filter(payroll_run_id=run.pk).first()
    prev_keys = {f.key: f.status for f in last.findings.all()} if last else {}
    a = AuditRun.objects.create(payroll_run_id=run.pk, previous_run_id=getattr(prev, 'pk', None), run_label=f'{run.name or ""} {run.month}/{run.year}'.strip(), by=user if getattr(user, 'is_authenticated', False) else None)
    c = Collector(a, prev_keys)
    slips = list(Payslip.objects.filter(payroll_run=run).select_related('employee'))
    prev_slips = {p.employee_id: p for p in Payslip.objects.filter(payroll_run=prev)} if prev else {}
    start, end = period_of(run)
    a.payslips = len(slips)

    # ---- totals
    tot = {'gross': sum(_d(p.gross_salary) for p in slips), 'net': sum(_d(p.net_salary) for p in slips), 'deductions': sum(_d(p.total_deductions) for p in slips), 'employees': len(slips)}
    ptot = {'gross': sum(_d(p.gross_salary) for p in prev_slips.values()), 'net': sum(_d(p.net_salary) for p in prev_slips.values()),
            'deductions': sum(_d(p.total_deductions) for p in prev_slips.values()), 'employees': len(prev_slips)} if prev else {}
    a.totals = {'now': {k: float(v) for k, v in tot.items()}, 'previous': {k: float(v) for k, v in ptot.items()}, 'previous_run': getattr(prev, 'pk', None),
                'previous_label': f'{prev.month}/{prev.year}' if prev else ''}
    if prev:
        for k, label in (('gross', 'Gross pay'), ('net', 'Net pay'), ('deductions', 'Deductions')):
            pc = _pct(ptot[k], tot[k])
            if pc is not None and abs(pc) > float(s.totals_change_pct):
                c.add('totals', 'warning' if abs(pc) < 25 else 'critical', f'{label} total moved {pc:+.1f}% against {prev.month}/{prev.year}',
                      f'{label} is {tot[k]:,.2f} this run and was {ptot[k]:,.2f} in the previous run ({len(slips)} vs {len(prev_slips)} payslips). '
                      f'A change above {s.totals_change_pct}% is flagged so the approver confirms it is expected (joiners, leavers, increments, bonus or an error).',
                      previous=ptot[k], current=tot[k], change_pct=pc)
        if len(slips) != len(prev_slips):
            c.add('headcount', 'info', f'{len(slips)} payslips now, {len(prev_slips)} in the previous run',
                  'The number of employees paid changed. New joiners and leavers are listed separately below.', previous=len(prev_slips), current=len(slips))
    else:
        c.add('no_previous', 'info', 'No previous month to compare with', 'This is the first run ZEO can see for these employees, so month-on-month checks are skipped. Component, attendance and bank checks still ran.')

    # ---- per payslip
    emp_ids = [p.employee_id for p in slips]
    comps = defaultdict(list)
    for x in PC.objects.filter(payslip__in=slips).select_related('component'):
        comps[x.payslip_id].append(x)
    att = {}
    try:
        AD = M('AttendancePlus', 'AttendanceDay')
        for r in AD.objects.filter(employee_id__in=emp_ids, date__gte=start, date__lte=end).values('employee_id').annotate(n=Count('id'), ot=Sum('ot_minutes'), absent=Count('id', filter=Q(status='absent'))):
            att[r['employee_id']] = r
        att_any = AD.objects.filter(date__gte=start, date__lte=end).exists()
    except Exception:
        att_any = False
    unpaid = defaultdict(float)
    try:
        LR = M('calendars', 'employee_leave_request')
        for r in LR.objects.filter(employee_id__in=emp_ids, status__in=('approved', 'Approved'), leave_type__type='unpaid', start_date__lte=end, end_date__gte=start):
            days = r.approved_days or r.number_of_days or 0
            unpaid[r.employee_id] += float(days)
    except Exception:
        pass
    bank_ok = set()
    try:
        BD = M('EmpManagement', 'EmployeeBankDetail')
        bank_ok = set(BD.objects.filter(employee_id__in=emp_ids, is_active=True).exclude(iban_number='').exclude(iban_number=None).values_list('employee_id', flat=True))
    except Exception:
        bank_ok = set(emp_ids)
    by_emp = defaultdict(list)
    for p in slips:
        by_emp[p.employee_id].append(p)
    seen_prev = set()
    for p in slips:
        e = p.employee
        name = emp_name(e)
        if len(by_emp[p.employee_id]) > 1 and p.pk == by_emp[p.employee_id][0].pk:
            c.add('duplicate_payslip', 'critical', f'{name}: {len(by_emp[p.employee_id])} payslips in this run',
                  'The employee has more than one payslip in the same run – they would be paid twice. Delete the extra payslip before approval.', employee=e, payslip_id=p.pk)
        if _d(p.net_salary) <= 0:
            c.add('zero_net', 'critical', f'{name}: net pay is {_d(p.net_salary):,.2f}',
                  'A zero or negative net pay means deductions are equal to or above the gross (loan, advance, unpaid leave or a wrong component). Check the deductions; a negative salary cannot be paid through WPS.',
                  employee=e, payslip_id=p.pk, current=p.net_salary)
        # duplicate components
        names = defaultdict(list)
        cats = defaultdict(list)
        for x in comps[p.pk]:
            names[x.component_id].append(x)
            if x.component.component_type == 'addition' and x.component.payroll_category not in ('basic', 'other_allowance', 'overtime', 'bonus', 'commission'):
                cats[x.component.payroll_category].append(x)
        for cid, xs in names.items():
            if len(xs) > 1:
                c.add('duplicate_component', 'critical', f'{name}: {xs[0].component.name} appears {len(xs)} times',
                      f'The same component is on the payslip {len(xs)} times ({", ".join(f"{_d(x.amount):,.2f}" for x in xs)}). The amount is counted twice. Remove the duplicate line.',
                      employee=e, payslip_id=p.pk, current=sum(_d(x.amount) for x in xs))
        for cat, xs in cats.items():
            if len(xs) > 1 and len({x.component_id for x in xs}) > 1:
                c.add('duplicate_allowance', 'warning', f'{name}: two {cat.replace("_", " ")} lines',
                      f'Two different components of the same allowance type are paid: {", ".join(f"{x.component.name} {_d(x.amount):,.2f}" for x in xs)}. Often one replaced the other and the old one was not switched off.',
                      employee=e, payslip_id=p.pk, current=sum(_d(x.amount) for x in xs))
        # month-on-month
        q = prev_slips.get(p.employee_id)
        if q is not None:
            seen_prev.add(p.employee_id)
            pc = _pct(q.gross_salary, p.gross_salary)
            if pc is not None and abs(pc) > float(s.gross_change_pct):
                sev = 'critical' if abs(pc) > float(s.gross_critical_pct) else 'warning'
                why = _why_changed(comps[p.pk], PC.objects.filter(payslip=q).select_related('component'))
                c.add('gross_change', sev, f'{name}: gross {pc:+.1f}% ({_d(q.gross_salary):,.2f} → {_d(p.gross_salary):,.2f})',
                      f'Gross pay moved by {pc:+.1f}% against the previous month. {why} Confirm there is an approved salary revision, bonus, overtime or pro-rata behind it.',
                      employee=e, payslip_id=p.pk, previous=q.gross_salary, current=p.gross_salary, change_pct=pc)
            else:
                npc = _pct(q.net_salary, p.net_salary)
                if npc is not None and abs(npc) > float(s.net_change_pct) and _d(p.net_salary) > 0:
                    why = _why_changed(comps[p.pk], PC.objects.filter(payslip=q).select_related('component'))
                    c.add('net_change', 'warning', f'{name}: net {npc:+.1f}% while gross is steady',
                          f'Net pay moved by {npc:+.1f}% ({_d(q.net_salary):,.2f} → {_d(p.net_salary):,.2f}) although the gross hardly changed – the deductions did. {why}',
                          employee=e, payslip_id=p.pk, previous=q.net_salary, current=p.net_salary, change_pct=npc)
        elif prev is not None:
            c.add('new_in_run', 'info', f'{name}: not in the previous run', 'First payslip for this employee in a run ZEO compared – a new joiner, a transfer or someone left out last month. Check the joining date and the pro-rata days.',
                  employee=e, payslip_id=p.pk, current=p.gross_salary)
        # attendance
        if att_any and p.employee_id not in att:
            c.add('no_attendance', 'warning', f'{name}: no attendance records in {start:%d/%m}–{end:%d/%m}',
                  f'Other employees have attendance days in the period but this one has none, and the payslip pays {p.days_worked or p.total_working_days or "full"} days. '
                  'If the employee was on leave, on a site without a device, or exempt from attendance this is fine; otherwise the days may be wrong.', employee=e, payslip_id=p.pk)
        elif p.employee_id in att:
            ot_min = att[p.employee_id].get('ot') or 0
            has_ot = any(x.component.payroll_category == 'overtime' and _d(x.amount) > 0 for x in comps[p.pk])
            if ot_min >= 60 and not has_ot:
                c.add('overtime_missing', 'warning', f'{name}: {ot_min / 60:.1f} h overtime in attendance, nothing on the payslip',
                      f'Attendance recorded {ot_min / 60:.1f} hours of overtime between {start:%d/%m} and {end:%d/%m}, but the payslip has no overtime amount. '
                      'Either the overtime is not approved / not payable for this grade, or the overtime component is missing from the salary structure.',
                      employee=e, payslip_id=p.pk, current=round(ot_min / 60, 1))
        if unpaid.get(p.employee_id) and not _d(p.pro_rata_adjustment) and (p.days_worked or 0) >= (p.total_working_days or 0):
            c.add('unpaid_leave', 'warning', f'{name}: {unpaid[p.employee_id]:g} unpaid leave days, no deduction',
                  f'Approved unpaid leave of {unpaid[p.employee_id]:g} day(s) falls in the period, yet the payslip shows full days worked and no pro-rata deduction. Check the leave dates against the attendance cut-off.',
                  employee=e, payslip_id=p.pk, current=unpaid[p.employee_id])
        if bank_ok and p.employee_id not in bank_ok:
            c.add('bank_missing', 'warning', f'{name}: no active bank account with IBAN', 'The WPS salary file needs an IBAN for every employee paid by transfer. Add the bank details or mark the employee as paid in cash.', employee=e, payslip_id=p.pk)
    # left out
    if prev:
        Emp = M('EmpManagement', 'emp_master')
        for eid, q in prev_slips.items():
            if eid in seen_prev:
                continue
            e = Emp.objects.filter(pk=eid).first()
            if e is None:
                continue
            active = getattr(e, 'is_active', True) and getattr(e, 'emp_status', True)
            c.add('missing_from_run', 'warning' if active else 'info', f'{emp_name(e)}: paid last month, not in this run',
                  ('The employee is still active but has no payslip in this run – left out by the run filters, on unpaid leave, or missed. ' if active else 'The employee has left; the final settlement is paid separately. ')
                  + f'Last month\'s net was {_d(q.net_salary):,.2f}.', employee=e, previous=q.net_salary)
    Finding.objects.bulk_create(c.rows)
    counts = defaultdict(int, {'critical': 0, 'warning': 0, 'info': 0, 'open': 0, 'open_critical': 0})
    for f in c.rows:
        counts[f.severity] += 1
        counts['rule:' + f.rule] += 1
        if f.status == 'open':
            counts['open'] += 1
            if f.severity == 'critical':
                counts['open_critical'] += 1
    a.counts = dict(counts)
    a.summary = summary_text(a, c.rows, s)
    a.save()
    return a


def _why_changed(now_comps, prev_comps):
    """Which components moved between two payslips – the explanation line."""
    now, before = defaultdict(Decimal), defaultdict(Decimal)
    for x in now_comps:
        now[x.component.name] += _d(x.amount)
    for x in prev_comps:
        before[x.component.name] += _d(x.amount)
    moves = []
    for k in sorted(set(now) | set(before)):
        a, b = before.get(k), now.get(k)
        if a is None:
            moves.append(f'{k} added ({b:,.2f})')
        elif b is None:
            moves.append(f'{k} removed (was {a:,.2f})')
        elif a != b:
            moves.append(f'{k} {a:,.2f} → {b:,.2f}')
    return ('Changed: ' + '; '.join(moves[:6]) + ('…' if len(moves) > 6 else '') + '.') if moves else 'No component changed – the difference comes from days worked / pro-rata.'


def summary_text(a, rows, s):
    crit = sum(1 for r in rows if r.severity == 'critical')
    warn = sum(1 for r in rows if r.severity == 'warning')
    info = sum(1 for r in rows if r.severity == 'info')
    t = a.totals.get('now', {})
    out = f'{a.payslips} payslips, gross {t.get("gross", 0):,.2f}, net {t.get("net", 0):,.2f}. '
    if crit:
        out += f'{crit} critical finding(s) – approval {"is refused" if s.block_on_critical else "is not advised"} until they are resolved or dismissed. '
    out += f'{warn} warning(s) to look at, {info} for information.'
    return out


def gate(run):
    """Used by the payroll approval: ('ok' | 'audit' | 'critical', message)."""
    s = AIAssistantSettings.get()
    a = AuditRun.objects.filter(payroll_run_id=run.pk).first()
    if a is None:
        return ('audit', 'Run the AI payroll audit before approving this run (Payroll → AI payroll auditor).') if s.require_audit else ('ok', '')
    n = a.findings.filter(severity='critical', status='open').count()
    if n and s.block_on_critical:
        return 'critical', f'{n} critical finding(s) of the AI payroll audit are still open – resolve or dismiss them before approving the run.'
    return 'ok', ''
