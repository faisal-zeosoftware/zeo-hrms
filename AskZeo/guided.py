"""Ask ZEO 1.71.0 (140) – guided payroll: additions and deductions → preview → calculate → pre-check → approval → WPS file →
payslips. Each step reads the state of the month from ZEO's own APIs and offers ONE next action, which runs only after the
user confirms it (a Change, recorded "via Ask ZEO"). The actions call the same APIs as the payroll run page, so rights,
branches, segregation of duties and the audit gate apply exactly as on the screens.
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
import calendar
from datetime import date

from django.apps import apps

from Translations.i18n import tr

from . import apicall, changes
from . import scope as S

M = apps.get_model
MS = '/main-sidebar/'
STEPS = [('inputs', 'Additions and deductions'), ('preview', 'Preview'), ('calculate', 'Calculate'), ('precheck', 'Pre-check'),
         ('approval', 'Approval'), ('wps', 'WPS file'), ('payslips', 'Payslips')]


def _month_name(y, m):
    return date(y, m, 1).strftime('%B %Y')


def _branches(c):
    B = M('OrganisationManager', 'brnch_mstr')
    qs = B.objects.all()
    if c.branches is not None:
        qs = qs.filter(pk__in=c.branches)
    return list(qs.order_by('pk'))


def _runs(c, y, m):
    from AccessControl.access import run_in_branches
    PR = M('PayrollManagement', 'PayrollRun')
    return [r for r in PR.objects.filter(year=y, month=m).order_by('-id') if c.admin or run_in_branches(c.branches, r)]


def default_month(c):
    """The month to work on: the newest run not yet paid, else the month after the newest paid run, else this month."""
    PR = M('PayrollManagement', 'PayrollRun')
    from AccessControl.access import run_in_branches
    for r in PR.objects.order_by('-year', '-month', '-id')[:24]:
        if not (c.admin or run_in_branches(c.branches, r)):
            continue
        if str(r.status).lower() != 'paid':
            return r.year, r.month
        return (r.year + (r.month // 12), r.month % 12 + 1)
    t = _zt_today()
    return t.year, t.month


def state(request, c, y, m, lang=None):
    """The steps of the month, the run, and the next action."""
    steps = [{'key': k, 'label': tr(l, lang=lang), 'state': 'todo', 'text': '', 'link': ''} for k, l in STEPS]
    by = {s['key']: s for s in steps}
    runs = _runs(c, y, m)
    run = runs[0] if runs else None
    ws = None
    if run is not None:
        try:
            ws = apicall.call(request, 'get', f'/payroll-plus/api/workspace/runs/{run.pk}/')
        except apicall.ApiError as e:
            return {'error': e.detail}
    # 1 inputs
    n_inputs = None
    try:
        mi = apicall.call(request, 'get', '/payroll-plus/api/monthly-inputs/', query={'month': f'{y}-{m:02d}'})
        n_inputs = sum(1 for r in (mi.get('rows') or []) for v in (r.get('values') or {}).values() if v not in (None, '', 0, '0', '0.00'))
    except apicall.ApiError:
        pass
    by['inputs'].update(state='done' if run else 'current', link=MS + 'salary-options/monthly-inputs',
                        text=(tr('{0} amount(s) entered for {1}.', n_inputs, _month_name(y, m), lang=lang) if n_inputs is not None else
                              tr('Enter overtime, bonuses and deductions of the month in Monthly inputs.', lang=lang)))
    # 2 preview
    emps = S.employees(c).filter(is_active=True)
    with_salary = emps.filter(salary_structures__isnull=False).distinct().count()
    by['preview'].update(state='done' if run else 'todo', link=MS + 'report-options/r/salary',
                         text=tr('{0} active employee(s), {1} with a salary, in your branches.', emps.count(), with_salary, lang=lang))
    nxt = None
    if run is None:
        by['preview']['state'] = 'current'
        nxt = {'key': 'calculate', 'label': tr('Create and calculate the payroll run of {0}', _month_name(y, m), lang=lang)}
    else:
        st = str(run.status or '').lower()
        acts = {a['key']: a for a in (ws or {}).get('actions') or []}
        tiles = (ws or {}).get('totals') or {}
        by['calculate'].update(state='done', link=f'{MS}salary-options/payroll-run/{run.pk}',
                               text=tr('Run {0}: {1} payslip(s), status {2}.', run.name or run.pk, (ws or {}).get('headcount') or (ws or {}).get('n') or len((ws or {}).get('payslips') or []),
                                       tr(run.status or '', lang=lang), lang=lang))
        audit = (ws or {}).get('audit')
        crit = (audit or {}).get('open_critical') if isinstance(audit, dict) else None
        if audit:
            by['precheck'].update(state='done', text=tr('Pre-check done – {0}.', (audit.get('summary') if isinstance(audit, dict) else '') or tr('see the findings', lang=lang), lang=lang),
                                  link=f'{MS}salary-options/payroll-run/{run.pk}')
        else:
            by['precheck'].update(text=tr('Not run yet.', lang=lang), link=f'{MS}zeo-ai/payroll-auditor')
        approved = st in ('approved', 'paid')
        a_send, a_mine, a_run = acts.get('send') or {}, acts.get('approve_payslips') or {}, acts.get('approve_run') or {}
        by['approval'].update(state='done' if approved else 'todo', link=f'{MS}salary-options/payroll-run/{run.pk}',
                              text=tr('Approved.', lang=lang) if approved else (a_run.get('reason') or a_send.get('reason') or ''))
        wps_done = bool(((ws or {}).get('wps') or {}).get('done'))
        by['wps'].update(state='done' if wps_done else 'todo', link=f'{MS}salary-options/wps?run={run.pk}',
                         text=tr('WPS file made.', lang=lang) if wps_done else (tr('Made after the approval.', lang=lang) if not approved else ''))
        a_pay = acts.get('pay') or {}
        by['payslips'].update(state='done' if st == 'paid' else 'todo', link=f'{MS}salary-options/pay-roll',
                              text=tr('Paid – employees see their payslips.', lang=lang) if st == 'paid' else (a_pay.get('reason') or tr('Released when the run is marked as paid.', lang=lang)))
        # the next action, in the order of the steps
        if st in ('pending', 'processed') and not audit:
            by['precheck']['state'] = 'current'
            nxt = {'key': 'precheck', 'label': tr('Run the pre-check (AI payroll auditor)', lang=lang)}
        elif st == 'processed' and a_send.get('ready') and a_send.get('allowed'):
            by['approval']['state'] = 'current'
            nxt = {'key': 'send', 'label': a_send.get('label'), 'ids': a_send.get('ids') or []}
        elif st == 'processed' and a_mine.get('ready') and a_mine.get('allowed'):
            by['approval']['state'] = 'current'
            nxt = {'key': 'approve_payslips', 'label': a_mine.get('label'), 'ids': a_mine.get('ids') or []}
        elif st == 'processed' and a_run.get('ready') and a_run.get('allowed'):
            by['approval']['state'] = 'current'
            nxt = {'key': 'approve_run', 'label': a_run.get('label')}
        elif st == 'processed':
            by['approval']['state'] = 'current'
            nxt = {'key': 'wait', 'label': '', 'reason': a_mine.get('reason') or a_run.get('reason') or a_send.get('reason') or tr('Waiting for the approvers.', lang=lang)}
        elif approved and not wps_done and (acts.get('wps') or {}).get('allowed'):
            by['wps']['state'] = 'current'
            nxt = {'key': 'wps', 'label': tr('Make the WPS file', lang=lang)}
        elif st == 'approved' and a_pay.get('allowed') and a_pay.get('ready'):
            by['payslips']['state'] = 'current'
            nxt = {'key': 'pay', 'label': a_pay.get('label')}
        elif st == 'approved':
            by['payslips']['state'] = 'current'
            nxt = {'key': 'wait', 'label': '', 'reason': a_pay.get('reason') or tr('Somebody else releases the payment.', lang=lang)}
        else:
            nxt = {'key': 'done', 'label': '', 'reason': tr('This payroll month is complete.', lang=lang)}
        del tiles
    return {'year': y, 'month': m, 'month_name': _month_name(y, m), 'run': ({'id': run.pk, 'name': run.name, 'status': run.status} if run else None),
            'steps': steps, 'next': nxt}


def plan(request, c, conv, y, m, key, data=None, lang=None):
    """The Change (preview) for one guided step. Raises changes.Refused."""
    data = data or {}
    runs = _runs(c, y, m)
    run = runs[0] if runs else None
    title_m = _month_name(y, m)
    if key == 'calculate':
        if run is not None:
            raise changes.Refused(tr('{0} already has a payroll run.', title_m, lang=lang))
        if not S.can(c, {'add_payrollrun'}):
            raise changes.Refused(tr('You do not have the right to create payroll runs.', lang=lang), 403)
        last = calendar.monthrange(y, m)[1]
        pay = data.get('payment_date') or date(y, m, last).isoformat()
        bids = [b.pk for b in _branches(c)]
        body = {'name': (data.get('name') or f'Payroll {title_m}')[:100], 'year': y, 'month': m, 'payment_date': pay, 'branch_ids': bids,
                'department': [], 'category': [], 'designation': [], 'employees': []}
        api = [{'method': 'post', 'path': '/payroll/api/PayrollRun/', 'data': body, 'label': tr('Create the payroll run {0}', body['name'], lang=lang),
                'show': tr('{0} branch(es), payment date {1}', len(bids), pay, lang=lang), 'undo': {'method': 'delete', 'path': '/payroll/api/PayrollRun/{id}/'}}]
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – calculate {0}', title_m, lang=lang), {'api': api, 'step': key, 'ym': [y, m]},
                               tr('The run is created and every payslip is calculated. Undo deletes the run while nothing is approved.', lang=lang), group='work')
    if run is None:
        raise changes.Refused(tr('{0} has no payroll run yet – calculate it first.', title_m, lang=lang))
    rid = run.pk
    if key == 'precheck':
        api = [{'method': 'post', 'path': f'/zeo-ai/api/payroll/runs/{rid}/audit/', 'label': tr('Run the AI payroll auditor on {0}', run.name, lang=lang), 'show': '', 'undo': None}]
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – pre-check {0}', title_m, lang=lang), {'api': api, 'step': key, 'ym': [y, m]},
                               tr('Compares every payslip with last month and the rules (nothing is changed on the payslips).', lang=lang), group='work', undoable=False)
    ws = apicall.call(request, 'get', f'/payroll-plus/api/workspace/runs/{rid}/')
    acts = {a['key']: a for a in ws.get('actions') or []}
    a = acts.get(key) or {}
    if key in ('send', 'approve_payslips', 'approve_run', 'pay') and not (a.get('allowed') and a.get('ready')):
        raise changes.Refused(a.get('reason') or tr('This step is not possible now.', lang=lang), 409)
    if key == 'send':
        api = [{'method': 'post', 'path': '/payroll/api/payslip/confirm-bulk/', 'data': {'ids': a.get('ids') or []}, 'label': a['label'], 'show': '', 'undo': None}]
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – send for approval', lang=lang), {'api': api, 'step': key, 'ym': [y, m]},
                               tr('The approvers get the payslips in Payslip approvals.', lang=lang), group='work', undoable=False)
    if key == 'approve_payslips':
        api = [{'method': 'post', 'path': '/payroll/api/approval-payroll/bulk_approve/', 'data': {'approval_ids': a.get('ids') or [], 'note': 'via Ask ZEO'},
                'label': a['label'], 'show': '', 'undo': None}]
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – approve my payslips', lang=lang), {'api': api, 'step': key, 'ym': [y, m]},
                               '', group='work', undoable=False)
    if key == 'approve_run':
        api = [{'method': 'patch', 'path': f'/payroll/api/PayrollRun/{rid}/', 'data': {'status': 'approved'}, 'label': a['label'], 'show': 'processed → approved',
                'undo': {'method': 'patch', 'path': f'/payroll/api/PayrollRun/{rid}/', 'data': {'status': 'processed'}}}]
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – approve the run', lang=lang), {'api': api, 'step': key, 'ym': [y, m]},
                               tr('After the approval the WPS file can be made. Undo takes the approval back.', lang=lang), group='work')
    if key == 'wps':
        if not (acts.get('wps') or {}).get('allowed'):
            raise changes.Refused((acts.get('wps') or {}).get('reason') or tr('You may not make the WPS file.', lang=lang), 403)
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – WPS file of {0}', title_m, lang=lang),
                               {'custom': 'wps', 'run': rid, 'step': key, 'ym': [y, m],
                                'api': [{'method': 'post', 'path': '/payroll/sif-data/', 'label': tr('Make the WPS (SIF) data of run {0}', run.name, lang=lang), 'show': ''}]},
                               tr('The salary lines are checked and the file is recorded on the run; download it from the WPS file screen.', lang=lang), group='work', undoable=False)
    if key == 'pay':
        ref = (data.get('payment_ref') or '').strip()[:60] or f'ASKZEO-{rid}'
        api = [{'method': 'patch', 'path': f'/payroll/api/PayrollRun/{rid}/', 'data': {'status': 'paid', 'payment_ref': ref}, 'label': a['label'], 'show': f'approved → paid ({ref})',
                'undo': None}]
        return changes.preview(request, conv, 'guided_payroll', tr('Guided payroll – mark paid and release the payslips', lang=lang), {'api': api, 'step': key, 'ym': [y, m]},
                               tr('Employees can then see their payslips. A paid run is closed.', lang=lang), group='work', undoable=False)
    raise changes.Refused(tr('Unknown step.', lang=lang))


def run_custom(request, ch):
    """Steps that need two calls with the answer of the first (the WPS file): called by changes.confirm."""
    if ch.plan.get('custom') == 'wps':
        rid = ch.plan['run']
        d = apicall.call(request, 'post', '/payroll/sif-data/', {'payroll_run_id': rid})
        data = (d or {}).get('data') or {}
        s = data.get('summary') or {}
        apicall.call(request, 'post', f'/payroll-plus/api/workspace/runs/{rid}/wps-made/',
                     {'file': 'sif', 'employees': s.get('employees'), 'total': s.get('total_salary'), 'skipped': s.get('skipped')})
        return {'done': [{'label': 'WPS', 'answer': {'employees': s.get('employees'), 'total': s.get('total_salary'),
                                                      'skipped': len(s.get('skipped') or []) if isinstance(s.get('skipped'), list) else s.get('skipped')}}],
                'download': f'{MS}salary-options/wps?run={rid}'}
    return {}
