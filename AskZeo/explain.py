"""Ask ZEO 1.71.0 (128) – explaining numbers in plain words from the results ZEO already calculated. Nothing is
recalculated: the payslip lines, the leave balance and statement reports, the final settlement lines (with their own "basis"
text) and the gratuity report are read as they are and put into sentences.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.apps import apps

from Translations.i18n import tr

from . import scope as S

M = apps.get_model
MS = '/main-sidebar/'
D0 = Decimal('0')


def money(v):
    return f'{Decimal(str(v or 0)):,.2f}'


def _d(v):
    try:
        return Decimal(str(v or 0))
    except Exception:
        return D0


def _comps(ps):
    out = defaultdict(lambda: {'amount': D0, 'type': 'others', 'show': True})
    for pc in ps.components.select_related('component').all():
        k = pc.component.name
        out[k]['amount'] += _d(pc.amount)
        out[k]['type'] = pc.component.component_type or 'others'
        out[k]['show'] = bool(pc.component.show_in_payslip) and pc.component.component_type != 'others'
    return out


def _period(ps):
    r = ps.payroll_run
    return date(r.year, r.month, 1).strftime('%B %Y') if r else ''


def previous_payslip(c, ps):
    r = ps.payroll_run
    if not r:
        return None
    y, m = (r.year, r.month - 1) if r.month > 1 else (r.year - 1, 12)
    return (S.payslips(c).filter(employee=ps.employee, payroll_run__year=y, payroll_run__month=m).exclude(status__in=('rejected', 'Rejected'))
            .order_by('-id').first())


def payslip(c, emp, ym=None, lang=None):
    """{'text', 'card'} explaining one payslip and the difference from last month, or {'error'}."""
    qs = S.payslips(c).filter(employee=emp).exclude(status__in=('rejected', 'Rejected'))
    if ym:
        qs = qs.filter(payroll_run__year=ym[0], payroll_run__month=ym[1])
    ps = qs.order_by('-payroll_run__year', '-payroll_run__month', '-id').first()
    if ps is None:
        if c.emp and emp.pk == c.emp.pk and not (c.admin or 'view_payslip' in c.codes):
            return {'error': tr('No released payslip found for you{0}. Payslips show here once the payroll is approved.',
                                (' ' + tr('for {0}', date(ym[0], ym[1], 1).strftime('%B %Y'), lang=lang)) if ym else '', lang=lang)}
        return {'error': tr('No payslip found for {0}{1}.', S.emp_label(emp), (' – ' + date(ym[0], ym[1], 1).strftime('%B %Y')) if ym else '', lang=lang)}
    comps = _comps(ps)
    lines = []
    for name, v in sorted(comps.items(), key=lambda x: ({'addition': 0, 'deduction': 1}.get(x[1]['type'], 2), x[0])):
        if not v['show'] and v['type'] == 'others':
            continue
        lines.append({'label': name, 'kind': v['type'], 'amount': money(v['amount'])})
    unpaid = sum((_d(x.days) for x in ps.leave_details.select_related('leave_type').all() if (x.leave_type.type or '') == 'unpaid'), D0)
    leave_lines = [{'label': str(x.leave_type), 'days': f'{_d(x.days).normalize():f}'} for x in ps.leave_details.select_related('leave_type').all()]
    t = [tr('**Payslip of {0} – {1}** (status {2}).', S.emp_label(emp), _period(ps), tr(ps.status or '', lang=lang), lang=lang),
         tr('Gross pay {0}, deductions {1}, **net pay {2}**.', money(ps.gross_salary), money(ps.total_deductions), money(ps.net_salary), lang=lang)]
    if ps.total_working_days and (ps.days_worked or 0) < ps.total_working_days:
        t.append(tr('Paid for {0} of {1} days of the month.', ps.days_worked, ps.total_working_days, lang=lang))
    if unpaid:
        t.append(tr('{0} day(s) of unpaid leave were taken off.', f'{unpaid.normalize():f}', lang=lang))
    if _d(ps.arrears):
        t.append(tr('Arrears of {0} are included.', money(ps.arrears), lang=lang))
    prev = previous_payslip(c, ps)
    diff = []
    if prev is not None:
        pc = _comps(prev)
        delta = _d(ps.net_salary) - _d(prev.net_salary)
        if delta:
            t.append(tr('Net pay is {0} {1} than last month ({2} → {3}).', money(abs(delta)), tr('higher', lang=lang) if delta > 0 else tr('lower', lang=lang),
                        money(prev.net_salary), money(ps.net_salary), lang=lang))
        else:
            t.append(tr('Net pay is the same as last month ({0}).', money(ps.net_salary), lang=lang))
        for k in sorted(set(comps) | set(pc)):
            a, b = pc.get(k, {}).get('amount'), comps.get(k, {}).get('amount')
            typ = (comps.get(k) or pc.get(k))['type']
            if typ == 'others':
                continue
            if a is None:
                diff.append({'label': k, 'before': '–', 'now': money(b), 'why': tr('new this month', lang=lang)})
            elif b is None:
                diff.append({'label': k, 'before': money(a), 'now': '–', 'why': tr('not paid this month', lang=lang)})
            elif a != b:
                why = ''
                if (ps.days_worked or 0) != (prev.days_worked or 0):
                    why = tr('days paid {0} → {1}', prev.days_worked, ps.days_worked, lang=lang)
                diff.append({'label': k, 'before': money(a), 'now': money(b), 'why': why or tr('amount changed', lang=lang)})
        if delta and not diff:
            t.append(tr('No line changed – the difference comes from the days paid.', lang=lang))
    else:
        t.append(tr('There is no payslip of the month before to compare with.', lang=lang))
    link = f'{MS}salary-options/payroll-details/{ps.pk}' if (c.admin or 'view_payslip' in c.codes) else f'{MS}self-service/payslips'
    card = {'type': 'explain', 'title': tr('Payslip {0}', _period(ps), lang=lang), 'lines': lines, 'leave': leave_lines, 'diff': diff,
            'totals': {'gross': money(ps.gross_salary), 'deductions': money(ps.total_deductions), 'net': money(ps.net_salary)}, 'link': link}
    return {'text': '\n\n'.join(t), 'card': card, 'links': [{'label': tr('Open the payslip', lang=lang), 'url': link}], 'payslip_id': ps.pk,
            'figures': {'net': str(ps.net_salary), 'gross': str(ps.gross_salary)}}


def _report(request, key, emp, d_from=None, d_to=None):
    from DashboardManagement.assistant import Tools
    t = Tools(request, '')
    d = t.fetch(key, d_from, d_to)
    if d.get('error'):
        return None, d['error']
    rows = [r for r in d['rows'] if r.get('_emp') == emp.pk or (emp.emp_code and f'({emp.emp_code})' in str(r.get('employee') or ''))]
    return {'title': d['title'], 'columns': d['columns'], 'rows': rows, 'period': d.get('period')}, None


def leave_balance(request, c, emp, lang=None):
    d, err = _report(request, 'leave-balance', emp)
    if d is None and c.emp and emp.pk == c.emp.pk:
        from DashboardManagement.services import ess_summary
        s = ess_summary(request.user)
        bal = s.get('leave_balances') or []
        if not bal:
            return {'error': tr('No leave balance found for you.', lang=lang)}
        rows = [[b.get('name'), f"{_d(b.get('balance')).normalize():f}"] for b in bal]
        return {'text': tr('**Your leave balance today:** {0}.', ', '.join(f'{a} {b}' for a, b in rows), lang=lang) + '\n\n' +
                tr('The balance is the days earned this leave year plus the days carried forward, less the days taken, encashed or waiting for approval.', lang=lang),
                'card': {'type': 'table', 'title': tr('My leave balance', lang=lang), 'columns': [tr('Leave type', lang=lang), tr('Balance (days)', lang=lang)], 'rows': rows},
                'links': [{'label': tr('My leave', lang=lang), 'url': f'{MS}self-service/leave'}]}
    if d is None:
        return {'error': err}
    if not d['rows']:
        return {'error': tr('No leave balance found for {0} – check the leave policy of the employee.', S.emp_label(emp), lang=lang)}
    keep = [c2 for c2 in d['columns'] if c2['key'] not in ('employee', 'branch', 'department', 'designation', 'category')]
    rows = [[('' if r.get(k['key']) is None else (f"{r[k['key']]:g}" if isinstance(r.get(k['key']), float) else str(r[k['key']]))) for k in keep] for r in d['rows']]
    txt = [tr('**Leave balance of {0}** – from the Leave balance report, as of today.', S.emp_label(emp), lang=lang)]
    labels = {k['key']: tr(k['label'], lang=lang) for k in keep}
    rows_sorted = sorted(d['rows'], key=lambda r: (0 if 'annual' in str(r.get('type') or r.get('leave_type') or '').lower() else 1, str(r.get('type') or r.get('leave_type') or '')))
    for r in rows_sorted[:12]:
        parts = [f"{labels[k]}: {r[k]:g}" for k in labels if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool)]
        name = r.get('type') or r.get('leave_type') or ''
        txt.append(f'- **{name}** – ' + ', '.join(parts))
    txt.append(tr('Available = opening + earned so far − taken − encashed − waiting for approval. Earned grows each month by the accrual of the policy.', lang=lang))
    return {'text': '\n'.join(txt), 'card': {'type': 'table', 'title': tr(d['title'], lang=lang), 'columns': [tr(k['label'], lang=lang) for k in keep], 'rows': rows},
            'links': [{'label': tr('Leave balance report', lang=lang), 'url': f'{MS}report-options/r/leave-balance'}], 'rows': d['rows']}


SETTLE_READ = {'view_exitsettlement', 'view_payrollrun', 'change_payrollrun', 'view_emp_master'}


def settlement(request, c, emp, lang=None):
    X = M('PayrollPlus', 'ExitSettlement')
    st = X.objects.filter(employee_id=emp.pk).exclude(status='cancelled').order_by('-last_working_date', '-id').first()
    mine = c.emp is not None and emp.pk == c.emp.pk
    if st is None or not (S.can(c, SETTLE_READ) or (mine and st.status in ('approved', 'paid', 'validated', 'submitted'))):
        return {'error': tr('No final settlement found for {0}.', S.emp_label(emp), lang=lang)}
    t = [tr('**Final settlement of {0}** ({1}) – last working day {2}, service {3} days, status {4}.', S.emp_label(emp), st.number or st.pk,
            st.last_working_date.strftime('%d/%m/%Y'), st.service_days, tr(st.get_status_display(), lang=lang), lang=lang),
         tr('Monthly basic {0}; wage used for the other lines (basic + fixed allowances) {1}.', money(st.basic), money(st.wage), lang=lang)]
    rows = []
    for ln in (st.lines or []) + (st.other_lines or []):
        amt = _d(ln.get('amount'))
        if not amt:
            continue
        sign = '−' if ln.get('kind') == 'deduction' else '+'
        rows.append([ln.get('label') or ln.get('key') or '', sign + ' ' + money(amt), ln.get('basis') or ln.get('note') or ''])
        t.append(f"- {sign} **{ln.get('label') or ln.get('key')}** {money(amt)}" + (f" – {ln.get('basis')}" if ln.get('basis') else ''))
    t.append(tr('Total earnings {0}, deductions {1}, **net {2}**.', money(st.total_earnings), money(st.total_deductions), money(st.net), lang=lang))
    link = f'{MS}sub-sidebar/leavers'
    return {'text': '\n'.join(t), 'card': {'type': 'table', 'title': tr('Final settlement', lang=lang), 'columns': [tr('Line', lang=lang), tr('Amount', lang=lang), tr('How it was worked out', lang=lang)], 'rows': rows},
            'links': [{'label': tr('Leavers', lang=lang), 'url': link}], 'figures': {'net': str(st.net)}}


def gratuity(request, c, emp, lang=None):
    d, err = _report(request, 'gratuity', emp)
    if d is None:
        if c.emp and emp.pk == c.emp.pk:
            return {'error': tr('Your gratuity is shown by HR in the final settlement. Ask HR for an estimate.', lang=lang)}
        return {'error': err}
    if not d['rows']:
        return {'error': tr('{0} is not in the Gratuity accrual report (for example less than one year of service, or no basic salary).', S.emp_label(emp), lang=lang)}
    r = d['rows'][0]
    labels = {k['key']: tr(k['label'], lang=lang) for k in d['columns']}
    parts = [f"{labels[k]}: {('{:,.2f}'.format(v) if isinstance(v, float) else v)}" for k, v in r.items()
             if k in labels and k not in ('employee', 'branch', 'department', 'designation', 'category') and v not in (None, '')]
    t = [tr('**Gratuity of {0}** – from the Gratuity accrual report (UAE: 21 days of basic for each of the first five years, 30 days after; at most two years\' wage).', S.emp_label(emp), lang=lang)]
    t += ['- ' + p for p in parts]
    return {'text': '\n'.join(t), 'card': {'type': 'table', 'title': tr(d['title'], lang=lang), 'columns': [tr('Item', lang=lang), tr('Value', lang=lang)], 'rows': [p.split(': ', 1) for p in parts]},
            'links': [{'label': tr('Gratuity accrual report', lang=lang), 'url': f'{MS}report-options/r/gratuity'}], 'row': r}


def my_documents(c, lang=None):
    if not c.emp:
        return {'error': tr('Your login is not linked to an employee.', lang=lang)}
    from .checks import eid_problem
    rows = []
    try:
        x = M('EmployeeProfile', 'EmployeeIdentity').objects.filter(employee_id=c.emp.pk).first()
    except LookupError:
        x = None
    today = _zt_today()
    if x:
        for label, num, d in (('Passport', x.passport_no, x.passport_expiry_date), ('Residence visa', x.visa_number, x.visa_expiry_date),
                              ('Emirates ID', x.emirates_id, x.emirates_id_expiry_date), ('Labour card', x.labour_card_no, x.labour_card_expiry_date)):
            if num or d:
                left = (d - today).days if d else None
                rows.append([tr(label, lang=lang), num or '–', d.strftime('%d/%m/%Y') if d else '–',
                             (tr('expired', lang=lang) if left is not None and left < 0 else (tr('{0} days left', left, lang=lang) if left is not None else ''))])
    for d in M('EmpManagement', 'Emp_Documents').objects.filter(emp_id=c.emp, is_active=True).select_related('document_type'):
        left = (d.emp_doc_expiry_date - today).days if d.emp_doc_expiry_date else None
        rows.append([str(d.document_type or ''), d.emp_doc_number, d.emp_doc_expiry_date.strftime('%d/%m/%Y') if d.emp_doc_expiry_date else '–',
                     (tr('expired', lang=lang) if left is not None and left < 0 else (tr('{0} days left', left, lang=lang) if left is not None else ''))])
    if not rows:
        return {'text': tr('No documents are recorded for you yet. Upload them in My documents.', lang=lang),
                'links': [{'label': tr('My documents', lang=lang), 'url': f'{MS}self-service/documents'}]}
    soon = [r for r in rows if r[3] and (r[3] == tr('expired', lang=lang) or (r[3].split()[0].isdigit() and int(r[3].split()[0]) <= 60))]
    t = tr('You have {0} document(s) on record.', len(rows), lang=lang) + (' ' + tr('{0} expire(s) within 60 days or already expired – start the renewal.', len(soon), lang=lang) if soon else '')
    return {'text': t, 'card': {'type': 'table', 'title': tr('My documents', lang=lang), 'columns': [tr('Document', lang=lang), tr('Number', lang=lang), tr('Expires', lang=lang), ''], 'rows': rows},
            'links': [{'label': tr('My documents', lang=lang), 'url': f'{MS}self-service/documents'}]}
