"""
v1.62.0 payroll – the monthly payroll workspace (read-only summaries; no calculation is done or changed here).

GET  api/workspace/runs/?year=                 runs with their stage, headcount, totals, change vs the previous month
GET  api/workspace/runs/<id>/                  one run: summary tiles, reasons vs last month, exceptions, payslips, approval
                                               progress, the actions of the current stage for this user, history
POST api/workspace/runs/<id>/wps-made/         {file, employees, total, skipped} – note in the run's history that the WPS file
                                               was made (the file itself is generated exactly as before)
GET  api/workspace/approvals/                  payslips waiting for this user's approval, grouped by run, with totals
GET  api/workspace/salaries/                   employees with their fixed monthly components and total, last change date
GET  api/workspace/components/                 salary components once per code with their branches, readable formula, use
"""
import calendar
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.response import Response

from AccessControl.access import run_in_branches
from Translations.i18n import tr

from . import services as S
from .views import Base

M = apps.get_model
ZERO = Decimal('0.00')
MONTHS = ['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
STEPS = [('draft', 'Draft'), ('calculated', 'Calculated'), ('checked', 'Checked'), ('sent', 'Sent for approval'), ('approved', 'Approved'),
         ('wps', 'WPS file made'), ('paid', 'Paid'), ('posted', 'Posted to accounting')]
RUN_MODEL = 'PayrollManagement.payrollrun'
HOUSING = ('hra', 'housing_allowance')


def d2(x):
    return str(S.q2(x))


def _iso(x):
    if not x:
        return None
    return x.isoformat() if hasattr(x, 'isoformat') else str(x)


def _name(e):
    return ' '.join(x for x in (getattr(e, 'emp_first_name', '') or '', getattr(e, 'emp_last_name', '') or '') if x).strip() if e else ''


def _period(run):
    return f'{tr(MONTHS[run.month])} {run.year}' if 1 <= (run.month or 0) <= 12 else str(run.year)


def _kinds():
    """{run_id: (kind, label)} for off-cycle and pay-calendar runs (regular monthly runs are not listed)."""
    out = {}
    try:
        for o in M('PayrollPlus', 'OffCycleRun').objects.all():
            out[o.payroll_run_id] = ('off_cycle', tr('Off-cycle – {0}', tr(o.get_kind_display())))
        for c in M('PayrollPlus', 'CalendarRun').objects.select_related('calendar'):
            out[c.payroll_run_id] = ('calendar', tr('Pay calendar – {0}', c.calendar.name))
    except LookupError:
        pass
    return out


def _wps_logs(run_ids):
    RL = M('Chatter', 'RecordLog')
    out = {}
    for r in RL.objects.filter(model=RUN_MODEL, object_id__in=[str(i) for i in run_ids], changes__has_key='wps_file').order_by('at'):
        out[int(r.object_id)] = r   # the newest wins
    return out


def _journals(run_ids):
    out = {}
    try:
        JB = M('ZeoAccounting', 'JournalBatch')
        for b in JB.objects.filter(source='payroll', source_id__in=[str(i) for i in run_ids]).exclude(status='cancelled').order_by('id'):
            out[int(b.source_id)] = b
    except LookupError:
        pass
    return out


def _audits(run_ids):
    out = {}
    try:
        AR = M('ZeoAI', 'AuditRun')
        for a in AR.objects.filter(payroll_run_id__in=run_ids).order_by('at'):
            out[a.payroll_run_id] = a
    except LookupError:
        pass
    return out


def _controls(run_ids):
    return {c.run_id: c for c in M('PayrollPlus', 'RunControl').objects.filter(run_id__in=run_ids)}


_UN = {}


def _user(uid):
    if not uid:
        return ''
    if uid not in _UN:
        _UN[uid] = S.user_name(uid)
    return _UN[uid]


def _journal_row(b):
    if b is None:
        return None
    lab = {'draft': tr('Draft'), 'ready': tr('Ready to post'), 'posted': tr('Posted'), 'exported': tr('Exported'), 'failed': tr('Failed')}
    return {'id': b.id, 'number': b.number, 'status': b.status, 'status_label': lab.get(b.status, b.status), 'posted_at': _iso(b.posted_at),
            'done': b.status in ('posted', 'exported')}


def _wps_row(run, log, ctl):
    st = str(run.status or '').lower()
    if log is not None:
        ch = (log.changes or {}).get('wps_file') or {}
        info = ch.get('info') or {}
        skipped = int(info.get('skipped') or 0)
        state = 'ready' if not skipped else 'gaps'
        label = tr('Bank-ready') if not skipped else tr('Made – {0} employee(s) left out', skipped)
        row = {'state': state, 'label': label, 'at': _iso(log.at), 'by': log.user_name, 'employees': info.get('employees'),
               'total': info.get('total'), 'skipped': skipped, 'done': True}
    elif st == 'paid' and ctl is not None and ctl.payment_ref:
        row = {'state': 'paid', 'label': tr('Paid – {0}', ctl.payment_ref), 'at': _iso(ctl.paid_at), 'by': _user(ctl.paid_by_id), 'done': True}
    elif st in ('approved', 'paid'):
        row = {'state': 'not_made', 'label': tr('Not made'), 'done': False}
    else:
        row = {'state': 'not_yet', 'label': tr('Not yet – the run must be approved first'), 'done': False}
    if st == 'paid':
        row['paid'] = True
    return row


def _steps(run, stats, audit, wps, journal, ctl):
    """The stage indicator: each step done / current / todo (skipped = passed without it, e.g. no AI audit)."""
    st = str(run.status or '').lower()
    n = stats.get('n', 0)
    open_crit = audit.findings.filter(severity='critical', status='open').count() if audit is not None else 0
    done = {
        'draft': True,
        'calculated': st in ('processed', 'approved', 'paid') and n > 0,
        'checked': audit is not None and not open_crit,
        'sent': st in ('approved', 'paid') or stats.get('confirmed', 0) > 0 or stats.get('decided', 0) > 0,
        'approved': st in ('approved', 'paid'),
        'wps': bool(wps.get('done')),
        'paid': st == 'paid',
        'posted': bool(journal and journal.get('done')),
    }
    out, current = [], None
    for i, (k, lab) in enumerate(STEPS):
        later = any(done[k2] for k2, _ in STEPS[i + 1:])
        if done[k]:
            state = 'done'
        elif later and k in ('checked', 'wps'):
            state = 'skipped'
        elif current is None:
            state, current = 'current', k
        else:
            state = 'todo'
        at = by = None
        if k == 'draft' and ctl is not None:
            at, by = ctl.prepared_at, _user(ctl.prepared_by_id)
        elif k == 'checked' and audit is not None:
            at, by = audit.at, (_user(audit.by_id) if audit.by_id else '')
        elif k == 'approved' and ctl is not None and ctl.approved_at:
            at, by = ctl.approved_at, _user(ctl.approved_by_id)
        elif k == 'wps' and wps.get('done'):
            at, by = wps.get('at'), wps.get('by')
        elif k == 'paid' and ctl is not None and ctl.paid_at:
            at, by = ctl.paid_at, _user(ctl.paid_by_id)
        elif k == 'posted' and journal and journal.get('done'):
            at = journal.get('posted_at')
        out.append({'key': k, 'label': tr(lab), 'state': state, 'at': _iso(at), 'by': by or ''})
    if current is None:
        current = 'done'
    return out, current


def _stats(run_ids):
    P = M('PayrollManagement', 'Payslip')
    PA = M('PayrollManagement', 'PayslipApproval')
    out = defaultdict(dict)
    for r in P.objects.filter(payroll_run_id__in=run_ids).values('payroll_run_id').annotate(
            n=Count('id'), gross=Sum('gross_salary'), ded=Sum('total_deductions'), net=Sum('net_salary'),
            confirmed=Count('id', filter=Q(confirm_status=True)), rejected=Count('id', filter=Q(status='rejected'))):
        out[r['payroll_run_id']].update(n=r['n'], gross=r['gross'] or ZERO, ded=r['ded'] or ZERO, net=r['net'] or ZERO,
                                        confirmed=r['confirmed'], rejected=r['rejected'])
    for r in PA.objects.filter(request__payroll_run_id__in=run_ids).exclude(role__startswith='Auto Approval').values('request__payroll_run_id').annotate(
            pending=Count('id', filter=Q(status='Pending', request__confirm_status=True)), decided=Count('id', filter=~Q(status='Pending'))):
        out[r['request__payroll_run_id']].update(pending=r['pending'], decided=r['decided'])
    return out


def _previous(run, runs_by_month, kinds, branches_of):
    y, m = (run.year, run.month - 1) if run.month > 1 else (run.year - 1, 12)
    mine = set(branches_of.get(run.pk, []))
    best, score = None, -1
    for r in runs_by_month.get((y, m), []):
        if r.pk == run.pk or r.pk in kinds:
            continue
        s = len(mine & set(branches_of.get(r.pk, [])))
        if s > score:
            best, score = r, s
    return best


def _pct(cur, prev):
    if prev in (None, 0) or not prev:
        return None
    return round(float((Decimal(cur) - Decimal(prev)) / Decimal(prev) * 100), 1)


class _RunBase(Base):
    def visible_runs(self):
        PR = M('PayrollManagement', 'PayrollRun')
        qs = PR.objects.order_by('-year', '-month', '-id').prefetch_related('branch')
        return [r for r in qs if run_in_branches(self.c.branches, r)]

    def need_view(self):
        if not self.can({'view_payrollrun', 'add_payrollrun', 'change_payrollrun', 'view_payslip'}):
            raise S.Refused(tr('You do not have the right to see payroll runs.'), status=403)


def _stage_text(current, st):
    if current == 'approved' and st.get('pending'):
        return tr('Waiting for approval – {0} payslip(s)', st.get('pending'))
    return {'calculated': tr('Not calculated yet'), 'checked': tr('Calculated – to be checked'), 'sent': tr('Checked – ready to send for approval'),
            'approved': tr('Ready to approve'), 'wps': tr('Approved – WPS file to make'), 'paid': tr('WPS file made – to be paid'),
            'posted': tr('Paid – to post to accounting'), 'done': tr('Completed')}.get(current, '')


def _run_rows(runs, kinds):
    ids = [r.pk for r in runs]
    stats = _stats(ids)
    ctls, wlogs, jns, auds = _controls(ids), _wps_logs(ids), _journals(ids), _audits(ids)
    branches_of = {r.pk: [b.id for b in r.branch.all()] for r in runs}
    by_month = defaultdict(list)
    for r in runs:
        by_month[(r.year, r.month)].append(r)
    rows = []
    for r in runs:
        st = stats.get(r.pk, {})
        ctl = ctls.get(r.pk)
        wps = _wps_row(r, wlogs.get(r.pk), ctl)
        jn = _journal_row(jns.get(r.pk))
        steps, current = _steps(r, st, auds.get(r.pk), wps, jn, ctl)
        prev = _previous(r, by_month, kinds, branches_of) if r.pk not in kinds else None
        pst = stats.get(prev.pk, {}) if prev else {}
        kind, kind_label = kinds.get(r.pk, ('regular', tr('Monthly payroll')))
        brs = [b.branch_name for b in r.branch.all()]
        rows.append({
            'id': r.pk, 'name': r.name or '', 'period': _period(r), 'month': r.month, 'year': r.year, 'kind': kind, 'kind_label': kind_label,
            'branches': brs, 'payment_date': _iso(r.payment_date), 'status': r.status,
            'headcount': st.get('n', 0), 'gross': d2(st.get('gross')), 'deductions': d2(st.get('ded')), 'net': d2(st.get('net')),
            'previous': {'id': prev.pk, 'period': _period(prev), 'net': d2(pst.get('net')), 'headcount': pst.get('n', 0)} if prev else None,
            'change_pct': _pct(st.get('net') or ZERO, pst.get('net')) if prev else None,
            'prepared_by': _user(ctl.prepared_by_id) if ctl else '', 'prepared_at': _iso(ctl.prepared_at) if ctl else None,
            'pending_approvals': st.get('pending', 0), 'confirmed': st.get('confirmed', 0), 'rejected': st.get('rejected', 0),
            'stage': current, 'stage_label': next((lab for k, lab in [(s['key'], s['label']) for s in steps] if k == current), tr('Completed')),
            'steps': steps, 'wps': wps, 'journal': jn, 'stage_text': _stage_text(current, st),
        })
    return rows


class WorkspaceRunsView(_RunBase):
    def get(self, request):
        self.need_view()
        _UN.clear()
        runs = self.visible_runs()
        y = request.query_params.get('year')
        if y and str(y).isdigit():
            runs = [r for r in runs if r.year == int(y) or (r.year == int(y) - 1 and r.month == 12)]
        runs = runs[:60]
        kinds = _kinds()
        rows = _run_rows(runs, kinds)
        if y and str(y).isdigit():
            rows = [x for x in rows if x['year'] == int(y)]
        years = sorted({r.year for r in self.visible_runs()} | {date.today().year}, reverse=True)
        return Response({'runs': rows, 'years': years, 'can_add': self.can({'add_payrollrun'}),
                         'segregation': S.settings().segregation, 'options': self._options() if self.can({'add_payrollrun'}) else {}})

    def _options(self):
        Br, D = M('OrganisationManager', 'brnch_mstr'), M('OrganisationManager', 'dept_master')
        C, G = M('OrganisationManager', 'ctgry_master'), M('OrganisationManager', 'desgntn_master')
        brs = Br.objects.order_by('branch_name')
        if self.c.branches is not None:
            brs = brs.filter(pk__in=self.c.branches)
        return {'branches': [{'id': b.pk, 'name': b.branch_name} for b in brs],
                'departments': [{'id': x.pk, 'name': x.dept_name} for x in D.objects.order_by('dept_name')],
                'categories': [{'id': x.pk, 'name': x.ctgry_title} for x in C.objects.order_by('ctgry_title')],
                'designations': [{'id': x.pk, 'name': x.desgntn_job_title} for x in G.objects.order_by('desgntn_job_title')]}


# ------------------------------------------------------------------ one run
def _amounts(ps):
    """basic / allowances (other fixed additions) / additions (the rest of gross) / deductions / employer contributions."""
    basic = allow = er = ZERO
    for pc in ps.components.all():
        c = pc.component
        a = Decimal(str(pc.amount or 0))
        if c.component_type == 'addition' and c.show_in_payslip:
            if c.payroll_category == 'basic':
                basic += a
            elif c.component_value_type == 'fixed':
                allow += a
        elif c.component_type == 'others':
            er += a
    gross = Decimal(str(ps.gross_salary or 0))
    return basic, allow, gross - basic - allow, Decimal(str(ps.total_deductions or 0)), er


def _bank_problem(e):
    bd = next((b for b in e.bank_details.all() if b.is_active), None)
    if bd is None:
        return tr('No active bank account')
    if not e.person_id or len(e.person_id) != 14:
        return tr('MoHRE person ID missing (14 digits)')
    if not bd.route_code or len(bd.route_code) != 9:
        return tr('Bank routing code missing (9 digits)')
    acct_ok = (bd.iban_number and len(bd.iban_number) == 23) or (bd.account_number and str(bd.account_number).isdigit() and len(str(bd.account_number)) == 16)
    if not acct_ok:
        return tr('IBAN missing (23 characters) or salary card number (16 digits)')
    return ''


class WorkspaceRunView(_RunBase):
    def _run(self, pk):
        PR = M('PayrollManagement', 'PayrollRun')
        run = PR.objects.filter(pk=pk).first()
        if run is None or not run_in_branches(self.c.branches, run):
            raise S.Refused(tr('Not found.'), status=404)
        return run

    def get(self, request, pk):
        self.need_view()
        _UN.clear()
        run = self._run(pk)
        kinds = _kinds()
        PR = M('PayrollManagement', 'PayrollRun')
        y, m = (run.year, run.month - 1) if run.month > 1 else (run.year - 1, 12)
        cands = [r for r in PR.objects.filter(year=y, month=m).prefetch_related('branch') if run_in_branches(self.c.branches, r)]
        row = _run_rows([run] + cands, kinds)[0]
        prev = PR.objects.filter(pk=row['previous']['id']).first() if row['previous'] else None

        from django.db.models import Prefetch
        P = M('PayrollManagement', 'Payslip')
        PC = M('PayrollManagement', 'PayslipComponent')
        slips = list(P.objects.filter(payroll_run=run).select_related('employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_branch_id')
                     .prefetch_related(Prefetch('components', queryset=PC.objects.select_related('component')), 'leave_details__leave_type',
                                       'employee__bank_details', 'approvals').order_by('employee__emp_code'))
        prev_slips = {}
        if prev is not None:
            for p in P.objects.filter(payroll_run=prev).prefetch_related(Prefetch('components', queryset=PC.objects.select_related('component')), 'leave_details__leave_type'):
                prev_slips[p.employee_id] = p

        # ---- tiles
        tot = defaultdict(lambda: ZERO)
        rows = []
        flags = defaultdict(list)
        ex = {k: [] for k in ('joiners', 'leavers', 'unpaid', 'zero_net', 'bank', 'salary_change', 'held')}
        m_start = date(run.year, run.month, 1)
        m_end = date(run.year, run.month, calendar.monthrange(run.year, run.month)[1])
        try:
            from PayrollManagement.signals import last_working_day
        except Exception:
            last_working_day = None
        appr_by_slip = {}
        for ps in slips:
            e = ps.employee
            basic, allow, add, ded, er = _amounts(ps)
            net = Decimal(str(ps.net_salary or 0))
            gross = Decimal(str(ps.gross_salary or 0))
            for k, v in (('basic', basic), ('allow', allow), ('add', add), ('ded', ded), ('er', er), ('gross', gross), ('net', net)):
                tot[k] += v
            item = {'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), 'payslip_id': ps.pk}
            if e.emp_joined_date and m_start <= e.emp_joined_date <= m_end:
                ex['joiners'].append({**item, 'detail': tr('Joined {0:%d/%m/%Y}', e.emp_joined_date)}); flags[ps.pk].append('joiner')
            lwd = last_working_day(e) if last_working_day else None
            if lwd and m_start <= lwd <= m_end:
                ex['leavers'].append({**item, 'detail': tr('Last working day {0:%d/%m/%Y}', lwd)}); flags[ps.pk].append('leaver')
            unpaid = sum((Decimal(str(x.days or 0)) for x in ps.leave_details.all() if (x.leave_type.type or '') == 'unpaid'), Decimal('0'))
            short = (ps.total_working_days or 0) - (ps.days_worked or 0)
            if unpaid or short > 0:
                parts = []
                if unpaid:
                    parts.append(tr('{0} unpaid leave day(s)', f'{unpaid.normalize():f}'))
                if short > 0:
                    parts.append(tr('paid {0} of {1} days', ps.days_worked, ps.total_working_days))
                ex['unpaid'].append({**item, 'detail': ' · '.join(parts)}); flags[ps.pk].append('unpaid')
            if net <= 0:
                ex['zero_net'].append({**item, 'detail': tr('Net pay {0}', d2(net))}); flags[ps.pk].append('zero_net')
            bp = _bank_problem(e)
            if bp:
                ex['bank'].append({**item, 'detail': bp}); flags[ps.pk].append('bank')
            pp = prev_slips.get(e.pk)
            full = lambda x: (x.days_worked or 0) >= (x.total_working_days or 0) and not any((y.leave_type.type or '') == 'unpaid' for y in x.leave_details.all())
            if pp is not None and full(ps) and full(pp):
                pb, pa, _, _, _ = _amounts(pp)
                if (pb + pa) != (basic + allow):
                    ex['salary_change'].append({**item, 'detail': tr('Fixed pay {0} → {1}', f'{pb + pa:,.2f}', f'{basic + allow:,.2f}')})
                    flags[ps.pk].append('salary_change')
            if (ps.status or '').lower() == 'rejected':
                ex['held'].append({**item, 'detail': tr('Rejected: {0}', ps.rejection_reason or '–')}); flags[ps.pk].append('held')
            steps = sorted(ps.approvals.all(), key=lambda a: (a.level, a.id))
            pend = next((a for a in steps if a.status == 'Pending'), None)
            appr_by_slip[ps.pk] = steps
            st = (ps.status or '').lower()
            if st == 'rejected':
                sl, sk = tr('Rejected'), 'rejected'
            elif st in ('approved', 'paid'):
                sl, sk = (tr('Paid') if str(run.status).lower() == 'paid' else tr('Approved')), 'approved'
            elif pend is not None and ps.confirm_status:
                who = _user(pend.approver_id) if pend.approver_id else tr('Company administrator')
                sl, sk = tr('Waiting – {0}', who.split(' (')[0]), 'waiting'
            else:
                sl, sk = tr('Draft'), 'draft'
            rows.append({'id': ps.pk, 'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e),
                         'department': getattr(e.emp_dept_id, 'dept_name', '') or '', 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '',
                         'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
                         'basic': d2(basic), 'allowances': d2(allow), 'additions': d2(add), 'deductions': d2(ded), 'gross': d2(gross), 'net': d2(net),
                         'days': ps.days_worked, 'of_days': ps.total_working_days, 'status': sk, 'status_label': sl, 'confirmed': ps.confirm_status,
                         'flags': flags[ps.pk]})

        # ---- vs last month: component totals and headcount
        reasons = []
        if prev is not None:
            def comp_tot(lst):
                t = defaultdict(lambda: ZERO)
                for p in lst:
                    for pc in p.components.all():
                        c = pc.component
                        if not c.show_in_payslip or c.component_type not in ('addition', 'deduction'):
                            continue
                        sign = 1 if c.component_type == 'addition' else -1
                        t[c.name] += sign * Decimal(str(pc.amount or 0))
                return t
            cur_t, prev_t = comp_tot(slips), comp_tot(prev_slips.values())
            diffs = sorted(((k, cur_t.get(k, ZERO) - prev_t.get(k, ZERO)) for k in set(cur_t) | set(prev_t)), key=lambda x: -abs(x[1]))
            n_now, n_prev = len(slips), len(prev_slips)
            joined = [s for s in slips if s.employee_id not in prev_slips]
            left = [e_id for e_id in prev_slips if e_id not in {s.employee_id for s in slips}]
            if n_now != n_prev or joined or left:
                reasons.append({'text': tr('Headcount {0} → {1} ({2} new, {3} not in this run)', n_prev, n_now, len(joined), len(left)), 'amount': None})
            for k, v in diffs[:4]:
                if abs(v) >= Decimal('0.01'):
                    reasons.append({'text': k, 'amount': d2(v), 'deduction': prev_t.get(k, ZERO) <= 0 and cur_t.get(k, ZERO) <= 0})

        tiles = {'employees': len(slips), 'basic': d2(tot['basic']), 'allowances': d2(tot['allow']), 'additions': d2(tot['add']),
                 'gross': d2(tot['gross']), 'deductions': d2(tot['ded']), 'net': d2(tot['net']), 'employer': d2(tot['er']),
                 'employer_cost': d2(tot['gross'] + tot['er'])}
        titles = {'joiners': tr('New joiners'), 'leavers': tr('Leavers in the month'), 'unpaid': tr('Unpaid leave / absence'),
                  'zero_net': tr('Zero or negative net pay'), 'bank': tr('Missing IBAN / bank details for WPS'),
                  'salary_change': tr('Salary changed since last month'), 'held': tr('Payslips rejected / on hold')}
        sev = {'zero_net': 'error', 'bank': 'warning', 'held': 'warning'}
        exceptions = [{'key': k, 'title': titles[k], 'severity': sev.get(k, 'info'), 'count': len(v), 'items': v} for k, v in ex.items()]

        # ---- AI auditor
        audit = None
        try:
            AR = M('ZeoAI', 'AuditRun')
            a = AR.objects.filter(payroll_run_id=run.pk).first()
            if a is not None:
                f = a.findings.all()
                audit = {'id': a.id, 'at': _iso(a.at), 'by': _user(a.by_id) if a.by_id else '', 'summary': a.summary,
                         'open': f.filter(status='open').count(), 'open_critical': f.filter(status='open', severity='critical').count(),
                         'total': f.count()}
            from ZeoAI.payroll_audit import gate
            gate_state, gate_msg = gate(run)
        except (LookupError, ImportError):
            gate_state, gate_msg = 'ok', ''

        # ---- approval progress per approver
        prog = {}
        for ps_id, steps in appr_by_slip.items():
            for a in steps:
                if (a.role or '').startswith('Auto Approval'):
                    continue
                key = (a.level, a.approver_id)
                g = prog.setdefault(key, {'level': a.level, 'approver': (_user(a.approver_id) if a.approver_id else tr('Company administrator')),
                                          'role': re.sub(r'\s*\(own payslip.*\)$', '', a.role or ''), 'approved': 0, 'pending': 0, 'rejected': 0,
                                          'mine': a.approver_id == request.user.pk or (a.approver_id is None and self.c.admin)})
                g[{'Approved': 'approved', 'Rejected': 'rejected'}.get(a.status, 'pending')] += 1
        progress = sorted(prog.values(), key=lambda g: (g['level'], g['approver']))
        my_pending = [a.id for steps in appr_by_slip.values() for a in steps if a.status == 'Pending'
                      and (a.approver_id == request.user.pk or (self.c.admin and a.approver_id is None))
                      and getattr(a.request.employee, 'users_id', None) != request.user.pk
                      and a.request.confirm_status]

        actions = self._actions(run, row, slips, audit, gate_state, gate_msg, my_pending)
        ctl = S.control_for(run)
        control = S.run_control_row(run)
        control['payment_ref'] = ctl.payment_ref if ctl else ''
        return Response({**row, 'arabic_name': run.arabic_name, 'attendance_start': _iso(run.attendance_start_date), 'attendance_end': _iso(run.attendance_end_date),
                         'departments': [d.dept_name for d in run.department.all()], 'categories': [c.ctgry_title for c in run.category.all()],
                         'designations': [d.desgntn_job_title for d in run.designation.all()],
                         'filters': {'branch_ids': [b.id for b in run.branch.all()], 'department': [d.pk for d in run.department.all()],
                                     'category': [c.pk for c in run.category.all()], 'designation': [d.pk for d in run.designation.all()]},
                         'tiles': tiles, 'reasons': reasons, 'exceptions': exceptions, 'payslips': rows, 'audit': audit,
                         'gate': {'state': gate_state, 'message': tr(gate_msg) if gate_msg else ''}, 'approvals': progress,
                         'my_pending_approvals': my_pending, 'actions': actions, 'control': control,
                         'history': self._history(run, slips, appr_by_slip, audit)})

    # -------------------------------------------------------------- what this user can do now
    def _actions(self, run, row, slips, audit, gate_state, gate_msg, my_pending):
        st = str(run.status or '').lower()
        sod = S.settings().segregation
        ctl = S.control_for(run)
        me = self.request.user.pk
        prepared_by_me = bool(sod and ctl and ctl.prepared_by_id == me)
        approved_by_me = bool(sod and ctl and ctl.approved_by_id == me)
        unconfirmed = [p.pk for p in slips if not p.confirm_status and (p.status or '').lower() not in ('approved', 'paid')]
        pending = row.get('pending_approvals', 0)
        out = []

        def add(key, label, allowed, ready, reason='', primary=False, **extra):
            out.append({'key': key, 'label': label, 'allowed': bool(allowed), 'ready': bool(ready), 'reason': reason, 'primary': primary, **extra})

        can_change = self.can({'change_payrollrun'})
        any_decided = row.get('confirmed', 0) > 0 or st in ('approved', 'paid')
        add('recalculate', tr('Recalculate'), can_change and self.can({'delete_payrollrun'}) and self.can({'add_payrollrun'}),
            st in ('pending', 'processed') and not any_decided,
            tr('Payslips were sent for approval – recalculating is no longer possible.') if any_decided else '',
            unconfirmed=len(unconfirmed))
        add('audit', tr('Run the AI auditor') if audit is None else tr('Run the AI auditor again'),
            self.can({'view_payrollrun', 'change_payrollrun', 'view_payslip', 'add_payrollrun'}), st == 'processed' and slips,
            '' if st == 'processed' else tr('The run is approved – the audit is closed.'),
            primary=row['stage'] == 'checked')
        send_reason = ''
        if gate_state != 'ok':
            send_reason = tr(gate_msg)
        # the confirm API only sees payslips the user may view (unreleased payslips need "Can view payslip")
        can_send = self.c.admin or ('view_payslip' in self.c.codes and bool({'change_payslip', 'change_payrollrun'} & self.c.codes))
        if not can_send and (self.can({'change_payslip'}) or can_change) and unconfirmed:
            send_reason = tr('Sending payslips for approval needs the rights "Can view payslip" and "Can change payslip" – ask your administrator.')
        add('send', tr('Send {0} payslip(s) for approval', len(unconfirmed)), self.can({'change_payslip'}) or can_change,
            st == 'processed' and bool(unconfirmed) and can_send, send_reason if unconfirmed else tr('Every payslip has been sent for approval.'),
            primary=row['stage'] in ('checked', 'sent') and bool(unconfirmed), ids=unconfirmed, warn=send_reason)
        add('approve_payslips', tr('Approve my {0} payslip(s)', len(my_pending)), bool(my_pending), bool(my_pending), '',
            primary=bool(my_pending), ids=my_pending)
        approve_reason = ''
        if pending:
            approve_reason = tr('{0} payslip(s) are still waiting for approval.', pending)
        elif unconfirmed and st == 'processed':
            approve_reason = tr('Send the payslips for approval first.')
        elif prepared_by_me:
            approve_reason = tr('You prepared this payroll run – somebody else must approve it (segregation of duties).')
        elif gate_state != 'ok':
            approve_reason = tr(gate_msg)
        add('approve_run', tr('Approve the payroll run'), can_change and not prepared_by_me, st == 'processed' and not pending and not unconfirmed and gate_state == 'ok',
            approve_reason, primary=st == 'processed' and not pending and not unconfirmed)
        add('undo_approval', tr('Undo the approval'), can_change, st == 'approved', '')
        add('wps', tr('Make the WPS file') if not row['wps'].get('done') else tr('Download the WPS file again'),
            self.can({'view_wps', 'export_wps', 'add_wps'}), st in ('approved', 'paid'),
            '' if st in ('approved', 'paid') else tr('Approve the run before the WPS file is made.'), primary=st == 'approved' and not row['wps'].get('done'))
        pay_reason = ''
        if prepared_by_me:
            pay_reason = tr('You prepared this payroll run – somebody else must release the payment (segregation of duties).')
        elif approved_by_me:
            pay_reason = tr('You approved this payroll run – somebody else must release the payment (segregation of duties).')
        add('pay', tr('Mark as paid and release the payslips'), can_change and not prepared_by_me and not approved_by_me, st == 'approved',
            pay_reason or ('' if st == 'approved' else tr('Approve the run first.')), primary=st == 'approved' and bool(row['wps'].get('done')))
        jn = row.get('journal')
        add('journal', tr('Open the journal') if jn else tr('Make the accounting journal'),
            self.can({'post_accounting', 'manage_accounting', 'view_accounting'}) if jn else self.can({'post_accounting', 'manage_accounting'}),
            st in ('approved', 'paid'), '' if st in ('approved', 'paid') else tr('Approve the run before it is posted to accounting.'),
            primary=st == 'paid' and not (jn and jn.get('done')), journal=jn)
        add('delete', tr('Delete the run'), self.can({'delete_payrollrun'}), st in ('pending', 'processed') and not any_decided, '')
        return out

    # -------------------------------------------------------------- who did what when
    def _history(self, run, slips, appr_by_slip, audit):
        RL = M('Chatter', 'RecordLog')
        ev = []
        for r in RL.objects.filter(model=RUN_MODEL, object_id=str(run.pk)).order_by('at'):
            ch = r.changes or {}
            if r.action == 'created':
                ev.append({'at': r.at, 'by': _user(r.user_id) or r.user_name, 'text': tr('Created the run')})
            elif 'wps_file' in ch:
                info = (ch['wps_file'] or {}).get('info') or {}
                ev.append({'at': r.at, 'by': _user(r.user_id) or r.user_name, 'text': tr('Made the WPS file – {0} employee(s), AED {1}', info.get('employees', '–'),
                                                                      f"{Decimal(str(info.get('total') or 0)):,.2f}")})
            elif 'status' in ch:
                new = str((ch['status'] or {}).get('new') or '')
                txt = {'Processed': tr('Calculated the payslips'), 'Approved': tr('Approved the run'), 'Paid': tr('Marked the run as paid'),
                       'Pending': tr('Created the run')}.get(new, tr('Status → {0}', tr(new)))
                if new == 'Processed' and (ch['status'] or {}).get('old') == 'Approved':
                    txt = tr('Took back the approval')
                ev.append({'at': r.at, 'by': _user(r.user_id) or r.user_name, 'text': txt})
        # payslips sent for approval / approved / rejected, by person and minute
        ids = [str(p.pk) for p in slips]
        groups = defaultdict(int)
        for r in RL.objects.filter(model='PayrollManagement.payslip', object_id__in=ids, action='updated', changes__has_key='confirm_status').only('at', 'user_name', 'user_id', 'changes'):
            if ((r.changes or {}).get('confirm_status') or {}).get('new') in (True, 'Yes', 'True', 'true'):
                groups[('sent', _user(r.user_id) or r.user_name, r.at.replace(second=0, microsecond=0))] += 1
        aids = [str(a.pk) for steps in appr_by_slip.values() for a in steps]
        for r in RL.objects.filter(model='PayrollManagement.payslipapproval', object_id__in=aids, action='updated', changes__has_key='status').only('at', 'user_name', 'user_id', 'changes'):
            new = str(((r.changes or {}).get('status') or {}).get('new') or '')
            if new in ('Approved', 'Rejected'):
                groups[(new.lower(), _user(r.user_id) or r.user_name, r.at.replace(second=0, microsecond=0))] += 1
        for (k, who, at), n in groups.items():
            ev.append({'at': at, 'by': who, 'text': {'sent': tr('Sent {0} payslip(s) for approval', n), 'approved': tr('Approved {0} payslip(s)', n),
                                                       'rejected': tr('Rejected {0} payslip(s)', n)}[k]})
        if audit is not None:
            ev.append({'at': audit['at'], 'by': audit['by'], 'text': tr('Ran the AI payroll audit – {0} finding(s), {1} open', audit['total'], audit['open'])})
        ctl = S.control_for(run)
        if ctl and ctl.paid_at and ctl.payment_ref and not any('paid' in e['text'].lower() for e in ev):
            ev.append({'at': ctl.paid_at, 'by': _user(ctl.paid_by_id), 'text': tr('Marked the run as paid')})
        if ctl and ctl.payment_ref:
            for e in ev:
                if e['text'] == tr('Marked the run as paid'):
                    e['text'] = tr('Marked the run as paid – reference {0}', ctl.payment_ref)
        try:
            JB = M('ZeoAccounting', 'JournalBatch')
            for b in JB.objects.filter(source='payroll', source_id=str(run.pk)).select_related('created_by', 'posted_by'):
                ev.append({'at': b.created_at, 'by': getattr(b.created_by, 'username', '') or '', 'text': tr('Made the accounting journal {0}', b.number or b.pk)})
                if b.posted_at:
                    ev.append({'at': b.posted_at, 'by': getattr(b.posted_by, 'username', '') or '',
                               'text': tr('Journal {0}: {1}', b.number or b.pk, tr('Posted') if b.status == 'posted' else tr('Exported'))})
        except LookupError:
            pass
        out = sorted(ev, key=lambda e: (str(_iso(e['at']) or '')), reverse=True)
        for e in out:
            e['at'] = _iso(e['at'])
        return out


class WorkspaceWpsMadeView(WorkspaceRunView):
    def post(self, request, pk):
        if not self.can({'view_wps', 'export_wps', 'add_wps'}):
            raise S.Refused(tr('You do not have the right to make the WPS file.'), status=403)
        run = self._run(pk)
        d = request.data or {}
        info = {'file': str(d.get('file') or 'sif')[:10], 'employees': int(d.get('employees') or 0),
                'total': str(S.q2(d.get('total') or 0)), 'skipped': int(d.get('skipped') or 0)}
        RL = M('Chatter', 'RecordLog')
        u = request.user
        RL.objects.create(model=RUN_MODEL, object_id=str(run.pk), record_label=(run.name or _period(run))[:255], action='updated',
                          changes={'wps_file': {'label': 'WPS file', 'old': None, 'new': f"{info['file'].upper()} – {info['employees']} – {info['total']}", 'info': info}},
                          user=u, user_name=getattr(u, 'username', '')[:150], path=request.path[:255], method='POST')
        return Response({'ok': True})


# ------------------------------------------------------------------ approvals grouped by run
class WorkspaceApprovalsView(Base):
    def get(self, request):
        PA = M('PayrollManagement', 'PayslipApproval')
        me = request.user.pk
        qs = (PA.objects.filter(status='Pending', request__confirm_status=True, request__status='pending')
              .select_related('request__payroll_run', 'request__employee__emp_dept_id', 'request__employee__emp_desgntn_id'))
        mine, others = defaultdict(list), defaultdict(lambda: defaultdict(int))
        for a in qs:
            run = a.request.payroll_run
            if not run_in_branches(self.c.branches, run):
                continue
            own = getattr(a.request.employee, 'users_id', None) == me
            if (a.approver_id == me or (self.c.admin and a.approver_id is None)) and not own:
                mine[run.pk].append(a)
            elif self.can({'view_payrollrun', 'view_payslipapproval'}):
                others[run.pk][(_user(a.approver_id) if a.approver_id else tr('Company administrator'))] += 1
        PR = M('PayrollManagement', 'PayrollRun')
        runs = {r.pk: r for r in PR.objects.filter(pk__in=set(mine) | set(others)).prefetch_related('branch')}
        groups = []
        for rid, lst in mine.items():
            r = runs[rid]
            items = []
            t = defaultdict(lambda: ZERO)
            for a in sorted(lst, key=lambda a: a.request.employee.emp_code):
                p, e = a.request, a.request.employee
                for k, v in (('gross', p.gross_salary), ('ded', p.total_deductions), ('net', p.net_salary)):
                    t[k] += Decimal(str(v or 0))
                items.append({'approval_id': a.pk, 'payslip_id': p.pk, 'code': e.emp_code, 'name': _name(e),
                              'department': getattr(e.emp_dept_id, 'dept_name', '') or '', 'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '',
                              'gross': d2(p.gross_salary), 'deductions': d2(p.total_deductions), 'net': d2(p.net_salary),
                              'level': a.level, 'role': re.sub(r'\s*\(own payslip.*\)$', '', a.role or '')})
            groups.append({'run': {'id': r.pk, 'name': r.name, 'period': _period(r), 'branches': [b.branch_name for b in r.branch.all()],
                                   'payment_date': _iso(r.payment_date)},
                           'count': len(items), 'gross': d2(t['gross']), 'deductions': d2(t['ded']), 'net': d2(t['net']), 'items': items})
        groups.sort(key=lambda g: (-runs[g['run']['id']].year, -runs[g['run']['id']].month))
        waiting = [{'run': {'id': rid, 'period': _period(runs[rid]), 'name': runs[rid].name}, 'with': [{'approver': k, 'count': v} for k, v in w.items()]}
                   for rid, w in others.items()]
        return Response({'groups': groups, 'others': waiting, 'total': sum(g['count'] for g in groups),
                         'can_setup': self.can({'view_payslipcommonworkflow', 'change_payslipcommonworkflow'}),
                         'can_open_run': self.can({'view_payrollrun', 'add_payrollrun', 'change_payrollrun', 'view_payslip'})})


# ------------------------------------------------------------------ employee salaries
class WorkspaceSalariesView(Base):
    def get(self, request):
        if not self.can({'view_employeesalarystructure', 'change_employeesalarystructure'}):
            raise S.Refused(tr('You do not have the right to see salaries.'), status=403)
        E = M('EmpManagement', 'emp_master')
        ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
        today = date.today()
        emps = E.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_desgntn_id', 'emp_branch_id').order_by('emp_code')
        if self.c.branches is not None:
            emps = emps.filter(emp_branch_id__in=self.c.branches)
        emps = list(emps)
        lines = defaultdict(list)
        for s in ESS.objects.filter(employee__in=emps, is_active=True).select_related('component'):
            if (s.valid_from and s.valid_from > today) or (s.valid_until and s.valid_until < today):
                continue
            lines[s.employee_id].append(s)
        last = {}
        for r in M('PayrollManagement', 'SalaryRevisionHistory').objects.filter(employee__in=emps).only('employee_id', 'date_updated'):
            if r.date_updated and (r.employee_id not in last or r.date_updated > last[r.employee_id]):
                last[r.employee_id] = r.date_updated
        rows, tot = [], defaultdict(lambda: ZERO)
        for e in emps:
            b = h = t = o = ded = ZERO
            comps, changed = [], last.get(e.pk)
            for s in lines.get(e.pk, []):
                c = s.component
                if c.component_value_type != 'fixed':
                    continue
                a = Decimal(str(s.amount or 0))
                if changed is None or (s.date_updated and s.date_updated > changed):
                    changed = s.date_updated
                if c.component_type == 'deduction':
                    ded += a
                    comps.append({'name': c.name, 'amount': d2(a), 'deduction': True})
                    continue
                if c.component_type != 'addition':
                    continue
                comps.append({'name': c.name, 'amount': d2(a)})
                if c.payroll_category == 'basic':
                    b += a
                elif c.payroll_category in HOUSING:
                    h += a
                elif c.payroll_category == 'transport_allowance':
                    t += a
                else:
                    o += a
            total = b + h + t + o
            for k, v in (('basic', b), ('housing', h), ('transport', t), ('other', o), ('total', total), ('deductions', ded)):
                tot[k] += v
            rows.append({'employee_id': e.pk, 'code': e.emp_code, 'name': _name(e), 'department': getattr(e.emp_dept_id, 'dept_name', '') or '',
                         'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') or '', 'branch': getattr(e.emp_branch_id, 'branch_name', '') or '',
                         'basic': d2(b), 'housing': d2(h), 'transport': d2(t), 'other': d2(o), 'total': d2(total), 'deductions': d2(ded),
                         'components': comps, 'last_change': _iso(changed.date() if hasattr(changed, 'date') else changed) if changed else None,
                         'no_salary': not comps})
        return Response({'rows': rows, 'totals': {k: d2(v) for k, v in tot.items()}, 'employees': len(rows),
                         'without_salary': sum(1 for r in rows if r['no_salary']),
                         'can_edit': self.can({'add_employeesalarystructure', 'change_employeesalarystructure'})})


# ------------------------------------------------------------------ salary components, once per code
SHORT = {'basic_salary': 'Basic', 'calendar_days': 'Days in the period', 'paid_days': 'Paid days', 'fixed_days': 'Fixed days',
         'employed_days': 'Days employed', 'days_without_pay': 'Unpaid days', 'unpaid_leave_days': 'Unpaid leave days', 'ot_hours': 'Overtime hours',
         'daily_wage': 'Daily wage', 'gratuity_days': 'Gratuity days', 'monthly_gratuity_accrual': 'Gratuity accrued this month',
         'is_national': 'UAE national (1 / 0)', 'pension_salary': 'Pension salary', 'pension_employee_rate': 'GPSSA employee rate',
         'pension_employer_rate': 'GPSSA employer rate', 'years_of_service': 'Years of service', 'absent_days': 'Absent days',
         'late_count': 'Days late', 'working_days': 'Working days', 'present_days': 'Present days'}


def readable_formula(formula, known, comp_names):
    if not formula:
        return ''
    def name(mo):
        w = mo.group(0)
        if w in ('and', 'or', 'not', 'IF', 'ROUND', 'MIN', 'MAX', 'ABS'):
            return {'and': tr('and'), 'or': tr('or'), 'not': tr('not')}.get(w, w)
        if w in SHORT:
            return tr(SHORT[w])
        if w in comp_names:
            return comp_names[w]
        lab = known.get(w) or known.get(w.lower())
        if lab:
            if lab.startswith('Component: '):
                return lab[11:]
            return tr(lab.split(' (')[0]) if len(lab) > 40 and not w.startswith(('stat_', 'statutory_')) else tr(lab)
        return w.replace('_', ' ')
    f = re.sub(r'\s*\*\s*', ' × ', formula)
    f = re.sub(r'\s*/\s*', ' ÷ ', f)
    out = re.sub(r'[A-Za-z_][A-Za-z0-9_]*', name, f)
    return re.sub(r'\s+', ' ', out).strip()


class WorkspaceComponentsView(Base):
    def get(self, request):
        if not self.can({'view_salarycomponent', 'change_salarycomponent', 'add_salarycomponent'}):
            raise S.Refused(tr('You do not have the right to see salary components.'), status=403)
        SC = M('PayrollManagement', 'SalaryComponent')
        ESS = M('PayrollManagement', 'EmployeeSalaryStructure')
        from PayrollManagement.formula import known_names
        known = known_names()
        comps = list(SC.objects.select_related('branch').order_by('component_type', 'name', 'branch_id'))
        comp_names = {}
        for c in comps:
            if c.code:
                comp_names[c.code] = c.name
        use = {r['component_id']: r for r in ESS.objects.filter(is_active=True, employee__is_active=True).values('component_id')
               .annotate(n=Count('employee_id', distinct=True), total=Sum('amount'))}
        stat_codes = set()
        try:
            for s in M('PayrollPlus', 'StatutoryScheme').objects.all():
                stat_codes.add(f'ST_{s.code}'.upper()); stat_codes.add(f'STE_{s.code}'.upper())
        except LookupError:
            pass
        groups = {}
        for c in comps:
            key = (c.code or c.name).upper()
            g = groups.get(key)
            if g is None:
                system = (c.code or '').upper().startswith(('ST_', 'STE_')) or (c.code or '').upper() in stat_codes or c.component_type == 'others' \
                    or c.payroll_category in ('gratuity', 'loan', 'advance_salary')
                section = 'system' if system else ('deduction' if c.component_type == 'deduction' else 'addition')
                g = groups[key] = {'key': key, 'code': c.code or '', 'name': c.name, 'type': c.component_type, 'type_label': tr(c.get_component_type_display()),
                                   'category': c.payroll_category, 'category_label': tr(c.get_payroll_category_display()),
                                   'value_type': c.component_value_type, 'value_type_label': tr(c.get_component_value_type_display()),
                                   'formula': c.formula or '', 'formula_readable': readable_formula(c.formula or '', known, comp_names),
                                   'deduct_leave': c.deduct_leave, 'show_in_payslip': c.show_in_payslip,
                                   'in_gross': c.component_type == 'addition' and c.show_in_payslip,
                                   'wps': ('variable' if c.component_value_type == 'variable' else 'fixed') if c.component_type == 'addition' and c.show_in_payslip else '',
                                   'description': c.description or '', 'section': section, 'items': [], 'employees': 0, 'monthly_total': ZERO,
                                   'differs': False}
            u = use.get(c.pk) or {}
            g['employees'] += u.get('n') or 0
            g['monthly_total'] += Decimal(str(u.get('total') or 0)) if c.component_value_type == 'fixed' else ZERO
            same = (c.formula or '') == g['formula'] and c.deduct_leave == g['deduct_leave'] and c.show_in_payslip == g['show_in_payslip'] \
                and c.component_type == g['type'] and c.component_value_type == g['value_type']
            if not same:
                g['differs'] = True
            g['items'].append({'id': c.pk, 'branch_id': c.branch_id, 'branch': c.branch.branch_name if c.branch_id else tr('All branches'),
                               'name': c.name, 'formula': c.formula or '', 'deduct_leave': c.deduct_leave, 'show_in_payslip': c.show_in_payslip,
                               'employees': u.get('n') or 0})
        rows = list(groups.values())
        for g in rows:
            g['monthly_total'] = d2(g['monthly_total'])
        order = {'addition': 0, 'deduction': 1, 'system': 2}
        rows.sort(key=lambda g: (order[g['section']], 0 if g['category'] == 'basic' else 1, g['name'].lower()))
        return Response({'rows': rows, 'count': len(comps),
                         'can_add': self.can({'add_salarycomponent'}), 'can_edit': self.can({'change_salarycomponent'}),
                         'can_delete': self.can({'delete_salarycomponent'})})
