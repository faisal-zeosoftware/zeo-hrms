"""v1.62.0 EOS – the Leavers workspace: every leaver (approved resignation, termination, end of contract, retirement, death) from the
approved exit to the paid final settlement, in five steps:

    Exit approved → End of service calculated → Settlement checked by Finance → Approved → Paid

A leaver is an EmpManagement.EmployeeResignation (approved); its EndOfService is made by EmpManagement (signal) and its final settlement
is PayrollPlus.ExitSettlement (services.py calculates it). "New leaver" records a termination / end of contract without a resignation
request: an approved EmployeeResignation of HR (no approval workflow – it is the employer's decision) and its settlement at once.
"""
import logging
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from Translations.i18n import tr

from . import services as S

log = logging.getLogger(__name__)
M = apps.get_model

EXIT_TYPES = [('resignation', 'Resignation'), ('termination', 'Termination'), ('end_of_contract', 'End of contract'),
              ('retirement', 'Retirement'), ('death_or_disablement', 'Death or disability')]
EXIT_LABEL = dict(EXIT_TYPES)
NEW_TYPES = ('termination', 'end_of_contract', 'retirement', 'death_or_disablement')
STEPS = ['Exit approved', 'End of service calculated', 'Settlement checked by Finance', 'Approved', 'Paid']
STAGES = [('to_calculate', 'To calculate'), ('draft', 'Calculated – HR to submit'), ('submitted', 'Waiting for Finance'),
          ('validated', 'Waiting for approval'), ('approved', 'Approved – to be paid'), ('paid', 'Paid'), ('cancelled', 'Cancelled')]
STAGE_LABEL = dict(STAGES)
DONE = {'to_calculate': 1, 'draft': 2, 'submitted': 2, 'validated': 3, 'approved': 4, 'paid': 5, 'cancelled': 1}


def exit_key(res, st=None):
    if st is not None and st.exit_reason:
        return st.exit_reason
    return res.termination_type or 'resignation'


def emp_name(e):
    return f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip()


def initials(e):
    return ''.join(x[:1] for x in (e.emp_first_name or '', e.emp_last_name or '') if x).upper() or '?'


def photo(e):
    try:
        return e.emp_profile_pic.url if e.emp_profile_pic else ''
    except Exception:
        return ''


def service_ymd(joined, lwd):
    """Years, months and days from the joining day to the last working day (both counted)."""
    if not joined or not lwd or lwd < joined:
        return {'years': 0, 'months': 0, 'days': 0}
    end = lwd + timedelta(days=1)
    y, m = end.year - joined.year, end.month - joined.month
    d = end.day - joined.day
    if d < 0:
        m -= 1
        prev = (end.replace(day=1) - timedelta(days=1))
        d += prev.day
    if m < 0:
        y -= 1
        m += 12
    return {'years': y, 'months': m, 'days': d}


def due_days():
    return S.settings().settlement_due_days


def stage_of(st):
    if st is None:
        return 'to_calculate'
    return st.status


def leaver_queryset(c):
    R = M('EmpManagement', 'EmployeeResignation')
    qs = R.objects.filter(status__iexact='approved').select_related('employee', 'employee__emp_branch_id', 'employee__emp_dept_id',
                                                                    'employee__emp_desgntn_id', 'branch')
    if c.branches is not None:
        qs = qs.filter(employee__emp_branch_id__in=c.branches)
    return qs


def _maps(res_ids):
    EOS = M('EmpManagement', 'EndOfService')
    X = M('PayrollPlus', 'ExitSettlement')
    eos = {e.resignation_id: e for e in EOS.objects.filter(resignation_id__in=res_ids)}
    sts = {s.eos_id: s for s in X.objects.filter(eos_id__in=[e.pk for e in eos.values()])}
    return eos, sts


def row(res, eos, st, today=None):
    today = today or timezone.localdate()
    e = res.employee
    key = exit_key(res, st)
    stage = stage_of(st)
    due = (st.due_date if st is not None and st.due_date else res.last_working_date + timedelta(days=due_days()))
    days_left = None if stage in ('paid', 'cancelled') else (due - today).days
    late = None
    if st is not None and st.status == 'paid' and st.paid_on:
        late = (st.paid_on - due).days
    br = e.emp_branch_id
    return {'id': res.pk, 'resignation_id': res.pk, 'document_number': res.document_number or '', 'eos_id': eos.pk if eos else None,
            'settlement_id': st.pk if st is not None else None, 'number': st.number if st is not None else '',
            'employee_id': e.pk, 'employee': emp_name(e), 'employee_code': e.emp_code, 'initials': initials(e), 'photo': photo(e),
            'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '', 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
            'branch_id': br.pk if br else None, 'branch': br.branch_name if br else '',
            'exit_type': key, 'exit_type_label': tr(EXIT_LABEL.get(key, key.replace('_', ' ').capitalize())),
            'notice_date': res.resigned_on, 'last_working_date': res.last_working_date, 'due_date': due, 'days_left': days_left,
            'overdue': days_left is not None and days_left < 0, 'paid_on': st.paid_on if st is not None else None, 'paid_late_days': late,
            'net': float(st.net) if st is not None else None, 'stage': stage, 'stage_label': tr(STAGE_LABEL.get(stage, stage)),
            'steps_done': DONE.get(stage, 1), 'blocked': bool(st is not None and any(l.get('kind') == 'block' for l in (st.lines or [])))}


def leaver_rows(c, stage='', branch='', q=''):
    qs = leaver_queryset(c)
    if branch:
        qs = qs.filter(employee__emp_branch_id=branch)
    res = list(qs.order_by('-last_working_date', '-id')[:1000])
    eos, sts = _maps([r.pk for r in res])
    today = timezone.localdate()
    rows = []
    for r in res:
        ev = eos.get(r.pk)
        st = sts.get(ev.pk) if ev else None
        rows.append(row(r, ev, st, today))
    counts = {}
    for x in rows:
        counts[x['stage']] = counts.get(x['stage'], 0) + 1
    if q:
        ql = q.lower()
        rows = [x for x in rows if ql in (x['employee'] + ' ' + x['employee_code'] + ' ' + x['number'] + ' ' + x['document_number'] + ' ' + x['designation']).lower()]
    if stage == 'open':
        rows = [x for x in rows if x['stage'] not in ('paid', 'cancelled')]
    elif stage:
        rows = [x for x in rows if x['stage'] in stage.split(',')]
    return rows, counts


# ------------------------------------------------------------------ the case page
def case(res, c, user):
    """Everything of one leaver: who, the exit, service, the statement (when calculated), the assets, what the user may do."""
    EOS = M('EmpManagement', 'EndOfService')
    X = M('PayrollPlus', 'ExitSettlement')
    eos = EOS.objects.filter(resignation=res).first()
    st = X.objects.filter(eos_id=eos.pk).first() if eos else None
    e = res.employee
    base = row(res, eos, st)
    basic, wage, parts = S._wage(e)
    since = (res.resigned_on or res.last_working_date) - timedelta(days=60)
    out = {**base, 'joined': e.emp_joined_date, 'reason': res.reason_for_leaving or '', 'notice_days': res.notice_period or 0,
           'service': service_ymd(e.emp_joined_date, res.last_working_date),
           'unpaid_days': float(st.unpaid_days) if st is not None else float(getattr(eos, 'leave_days_without_pay', 0) or 0),
           'basic': float(st.basic) if st is not None else float(basic), 'wage': float(st.wage) if st is not None else float(wage),
           'wage_parts': parts, 'steps': [tr(x) for x in STEPS],
           'step_label_1': tr('Resignation approved') if exit_key(res, st) == 'resignation' else tr('Exit approved'),
           'assets': S.asset_items(e.pk, st, since), 'settlement': S.settlement_row(st, full=True) if st is not None else None,
           'company': _company_name()}
    out['rights'] = rights(st, c, user)
    return out


def _company_name():
    try:
        from SelfService.letters import company_name
        return company_name()
    except Exception:
        return ''


def rights(st, c, user):
    codes, admin = c.codes, c.admin
    hr = admin or bool({'add_exitsettlement', 'change_exitsettlement'} & codes)
    done_by = set() if st is None else {st.submitted_by_id, st.validated_by_id, st.approved_by_id} - {None}
    status = st.status if st is not None else None
    return {'calculate': hr and st is None, 'edit': hr and status == 'draft', 'submit': hr and status == 'draft',
            'validate': status == 'submitted' and (admin or 'validate_exitsettlement' in codes) and user.pk not in done_by,
            'approve': status == 'validated' and (admin or 'approve_exitsettlement' in codes) and user.pk not in done_by,
            'pay': status == 'approved' and (admin or 'pay_exitsettlement' in codes) and user.pk != (st.approved_by_id if st is not None else None),
            'reject': status in ('submitted', 'validated') and (admin or bool({'validate_exitsettlement', 'approve_exitsettlement'} & codes)),
            'new_leaver': hr, 'pdf': st is not None}


def calculate(res, user):
    """Start (or recalculate) the settlement of a leaver: the end of service is made first when it is missing."""
    EOS = M('EmpManagement', 'EndOfService')
    eos = EOS.objects.filter(resignation=res).first()
    if eos is None:
        e = res.employee
        if not e.emp_joined_date:
            raise S.Refused(tr('The employee has no joining date – enter it first.'))
        from EmpManagement.utils import calculate_settlement
        eos = EOS.objects.create(resignation=res, date_of_joining=e.emp_joined_date, date_of_resignation_termination=res.resigned_on,
                                 last_working_date=res.last_working_date, years_of_service=0, total_service_days=0)
        calculate_settlement(eos)
    return S.start(eos, user)


# ------------------------------------------------------------------ new leaver (termination / end of contract …)
def _date(v, field):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        raise S.Refused(tr('Enter a valid date.'), problems={field: 'Invalid.'})


@transaction.atomic
def new_leaver(data, user, c):
    E = M('EmpManagement', 'emp_master')
    R = M('EmpManagement', 'EmployeeResignation')
    problems = {}
    emp = E.objects.filter(pk=data.get('employee')).select_related('emp_branch_id').first()
    if emp is None:
        problems['employee'] = tr('Choose the employee.')
    kind = str(data.get('exit_type') or '')
    if kind not in NEW_TYPES:
        problems['exit_type'] = tr('Choose the exit type.')
    if not data.get('last_working_date'):
        problems['last_working_date'] = tr('Enter the last working day.')
    if problems:
        raise S.Refused(tr('Fill in the required fields.'), problems=problems)
    if c.branches is not None and emp.emp_branch_id_id not in c.branches:
        raise S.Refused(tr('Employee not found.'), status=404)
    lwd = _date(data.get('last_working_date'), 'last_working_date')
    notice_on = _date(data.get('notice_date') or lwd, 'notice_date')
    if notice_on > lwd:
        raise S.Refused(tr('The notice date is after the last working day.'), problems={'notice_date': 'Invalid.'})
    if emp.emp_joined_date and lwd < emp.emp_joined_date:
        raise S.Refused(tr('The last working day is before the joining date.'), problems={'last_working_date': 'Invalid.'})
    if R.objects.filter(employee=emp).exclude(status__iexact='rejected').exclude(status__iexact='cancelled').exists():
        raise S.Refused(tr('{0} already has a resignation or exit – open it in Leavers.', emp_name(emp)), status=409)
    try:
        notice = int(data.get('notice_days') if data.get('notice_days') not in (None, '') else 30)
    except (TypeError, ValueError):
        raise S.Refused(tr('Enter the notice period in days.'), problems={'notice_days': 'Invalid.'})
    branch = emp.emp_branch_id or getattr(emp, 'work_location', None)
    number = None
    try:
        DN = M('OrganisationManager', 'DocumentNumbering')
        cfg = DN.objects.filter(branch_id=getattr(branch, 'pk', None), type='resignation_request').first()
        if cfg is not None:
            number = cfg.next_unused(R)
    except Exception:
        log.debug('document number', exc_info=True)
    base_type = 'termination' if kind == 'end_of_contract' else kind
    res = R.objects.create(document_number=number, document_date=notice_on, employee=emp, branch=branch, resigned_on=notice_on,
                           notice_period=notice, last_working_date=lwd, location=(getattr(branch, 'branch_name', '') or '')[:255],
                           termination_type=base_type, reason_for_leaving=(data.get('reason') or '').strip() or tr(EXIT_LABEL[kind]),
                           status='Approved', created_by=user)
    # the employer's decision: no approval workflow – an approval waiting for a manager would only confuse
    A = M('EmpManagement', 'ResignationApproval')
    A.objects.filter(resignation_request=res).exclude(status=A.APPROVED).delete()
    if not A.objects.filter(resignation_request=res).exists():
        try:
            A.objects.create(resignation_request=res, approver=user, role=tr('HR – {0}', tr(EXIT_LABEL[kind]).lower()), level=1, status=A.APPROVED)
        except Exception:
            log.debug('approval record', exc_info=True)
    res.refresh_from_db()
    if res.status != 'Approved':
        R.objects.filter(pk=res.pk).update(status='Approved')
        res.status = 'Approved'
    eos = M('EmpManagement', 'EndOfService').objects.filter(resignation=res).first()
    if eos is None:
        st = calculate(res, user)
    else:
        st = S.start(eos, user)
    if kind == 'end_of_contract' and st.exit_reason != kind:
        st.exit_reason = kind
        S.compute(st, user)
    return res


# ------------------------------------------------------------------ HR's own lines and the asset decisions (draft only)
def _draft(st):
    if st.status != 'draft':
        raise S.Refused(tr('Only a draft settlement can be changed – send it back to HR first.'), status=409)


def add_line(st, data, user):
    _draft(st)
    kind = str(data.get('kind') or '')
    label = str(data.get('label') or '').strip()
    problems = {}
    if kind not in ('earning', 'deduction'):
        problems['kind'] = tr('Choose addition or deduction.')
    if not label:
        problems['label'] = tr('Enter what it is for.')
    try:
        amount = S.q2(data.get('amount'))
        if amount <= 0:
            raise ValueError
    except Exception:
        problems['amount'] = tr('Enter an amount above zero.')
        amount = None
    if problems:
        raise S.Refused(tr('Fill in the required fields.'), problems=problems)
    lines = list(st.other_lines or [])
    nid = max([int(x.get('id') or 0) for x in lines] + [0]) + 1
    lines.append({'id': nid, 'kind': kind, 'label': label[:120], 'amount': float(amount), 'note': str(data.get('note') or '').strip()[:300],
                  'by_id': user.pk, 'by': S.person(user.pk), 'at': timezone.now().isoformat()})
    st.other_lines = lines
    S.log_step(st, 'line_added', user, f'{label[:120]} {"−" if kind == "deduction" else "+"}{amount:,.2f}')
    S.compute(st, user)
    return st


def remove_line(st, line_id, user):
    _draft(st)
    lines = list(st.other_lines or [])
    gone = [x for x in lines if str(x.get('id')) == str(line_id)]
    if not gone:
        raise S.Refused(tr('Line not found.'), status=404)
    st.other_lines = [x for x in lines if str(x.get('id')) != str(line_id)]
    S.log_step(st, 'line_removed', user, gone[0].get('label', ''))
    S.compute(st, user)
    return st


DECISIONS = ('returned', 'recover', 'waive', 'clear')


def decide_asset(st, data, user):
    """returned – HR received it (the HR asset is returned in Assets too); recover – deduct an amount; waive – write it off (reason);
    clear – remove the decision."""
    _draft(st)
    key = str(data.get('key') or '')
    decision = str(data.get('decision') or '')
    items = {i['key']: i for i in S.asset_items(st.employee_id, st)}
    it = items.get(key)
    if it is None:
        raise S.Refused(tr('Asset not found.'), status=404)
    if decision not in DECISIONS:
        raise S.Refused(tr('Choose returned, recover or write off.'), problems={'decision': 'Invalid.'})
    dec = dict(st.asset_decisions or {})
    note = str(data.get('note') or '').strip()[:300]
    if decision == 'clear':
        dec.pop(key, None)
        S.log_step(st, 'asset', user, tr('{0}: decision removed', it['name']))
    elif decision == 'returned':
        if it['state'] != 'not_returned':
            raise S.Refused(tr('This asset is already returned.'), status=409)
        if it['source'] == 'hr' and it.get('asset_id'):
            try:
                from AssetPlus import services as AP
                a = M('OrganisationManager', 'Asset').objects.get(pk=it['asset_id'])
                rd = data.get('return_date')
                rd = date.fromisoformat(str(rd)[:10]) if rd else min(timezone.localdate(), max(st.last_working_date, it.get('since') or st.last_working_date))
                AP.complete_return(a, user, return_date=rd, condition=data.get('condition') or 'healthy', accessories_returned=True,
                                   notes=note or tr('Returned at exit (final settlement)'))
            except S.Refused:
                raise
            except Exception as x:
                raise S.Refused(str(getattr(x, 'message', None) or x) or tr('The return could not be recorded.'))
        else:
            dec[key] = {'decision': 'returned', 'note': note, 'by': S.person(user.pk), 'at': timezone.now().isoformat(), 'asset': it['name']}
        S.log_step(st, 'asset', user, tr('{0}: returned', it['name']))
    elif decision == 'recover':
        try:
            amount = S.q2(data.get('amount'))
            if amount <= 0:
                raise ValueError
        except Exception:
            raise S.Refused(tr('Enter the amount to recover.'), problems={'amount': tr('Enter an amount above zero.')})
        if it['state'] != 'not_returned':
            raise S.Refused(tr('This asset is already returned.'), status=409)
        dec[key] = {'decision': 'recover', 'amount': float(amount), 'note': note, 'by': S.person(user.pk), 'at': timezone.now().isoformat(), 'asset': it['name']}
        S.log_step(st, 'asset', user, tr('{0}: recover {1:,.2f}', it['name'], amount))
    else:   # waive
        if not note:
            raise S.Refused(tr('Say why the asset is written off.'), problems={'note': tr('Required.')})
        if it['state'] != 'not_returned':
            raise S.Refused(tr('This asset is already returned.'), status=409)
        dec[key] = {'decision': 'waive', 'note': note, 'by': S.person(user.pk), 'at': timezone.now().isoformat(), 'asset': it['name']}
        S.log_step(st, 'asset', user, tr('{0}: written off – {1}', it['name'], note))
    st.asset_decisions = dec
    S.compute(st, user)
    return st
