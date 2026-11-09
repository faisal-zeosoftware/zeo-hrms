# v1.63.0 (item 28): new module – recovery schedules for any deduction
"""v1.63.0 (item 28) – recovery schedules (Payroll › Recoveries).

HR creates a recovery: employee, type (a deduction salary component – accommodation, transport, uniform, damage …), total, start month,
then N months (instalment = total ÷ N) or an amount per month, an optional monthly cap, and what happens at exit (deduct the balance
in the final settlement, or write it off). When the request type "Recovery schedule" has approval levels (Requests › Approval levels –
the general approval levels of 1.62.10) the recovery waits for them (a GeneralRequest without amounts, as payctl's salary revision);
without levels it is active at once.

Payroll (on_payslip, from PayrollManagement.signals._process_payroll, before the net-pay controls; once a month, like loans):
    due     = the planned instalment + what the last month could not take (carried) – never more than the balance; the whole balance
              when "settle early – next payroll" was chosen; a rounding leftover below 1.00 is taken with the instalment before it
    taken   = due, at most the recovery's monthly cap and – when Payroll controls › "Keep net pay at or above 0" is on – what still fits
              (gross, or gross × "maximum deductions %", less every other deduction of the payslip and the deductions carried over;
              recoveries come after statutory, loans, advances and the other deductions, in the order they were made)
    the rest carries to the next month (payslip note "… carried to next month"). Each instalment is its own payslip line (the
    recovery's deduction component), the payslip note gives the balance left; the journal credits the component's mapped account
    (ZeoAccounting AccountMap of the component, else the default deduction account) – the normal deduction posting.
A rejected / deleted payslip gives its instalments back (PayrollPlus/payslip_reject.py, the pre_delete receiver in pay163_signals.py).
Pause / resume, settle early (paid outside payroll, or the whole balance by the next payroll), write off (reason), cancel (nothing
taken yet). Final settlement: the balance is a deduction line `recovery_<id>` (or an information line when "write off at exit" is
chosen); paying the settlement closes the recovery (close_exit).
"""
import calendar
import logging
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from Translations.i18n import tr

log = logging.getLogger(__name__)
M = apps.get_model
ZERO = Decimal('0.00')
Q2 = Decimal('0.01')
REQUEST_TYPE = 'Recovery schedule'
MARK = 'RECOV:'
OPEN = ('active', 'paused')
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']


class Refused(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or {}


def q2(v):
    try:
        return Decimal(str(v if v not in (None, '') else 0)).quantize(Q2, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return ZERO


def add_months(d, k):
    y, m = divmod(d.month - 1 + k, 12)
    return date(d.year + y, m + 1, 1)


def month_label(d):
    return f'{tr(MONTHS[d.month - 1])} {d.year}'


def _user_name(user):
    try:
        return (user.get_full_name() or user.username or '')[:150] if user is not None else ''
    except Exception:
        return str(getattr(user, 'username', '') or '')[:150]


def _note(r, action, user=None, note='', **extra):
    r.history = list(r.history or []) + [dict({'action': action, 'by': _user_name(user), 'by_id': getattr(user, 'pk', None),
                                               'at': timezone.now().isoformat(timespec='seconds'), 'note': str(note or '')[:255]}, **extra)]


# ------------------------------------------------------------------ figures
def taken(r):
    return q2(r.entries.filter(status='taken').aggregate(t=Sum('amount'))['t'] or 0)


def balance(r):
    return max(q2(r.total) - taken(r), ZERO)


def _last_payroll(r, before=None):
    qs = r.entries.filter(kind='payroll', status='taken')
    if before is not None:
        qs = qs.filter(month__lt=before)
    return qs.order_by('-month', '-id').first()


def carried_in(r, month):
    last = _last_payroll(r, month)
    return max(q2(last.due) - q2(last.amount), ZERO) if last is not None else ZERO


def planned(r):
    return q2(r.per_month)


def due_for(r, month, bal=None):
    """the instalment due in `month` before the cap and the net-pay room"""
    bal = balance(r) if bal is None else bal
    if bal <= 0:
        return ZERO
    if r.settle_next:
        return bal
    d = min(bal, planned(r) + carried_in(r, month))
    if 0 < bal - d < Decimal('1.00'):   # a rounding leftover goes with this instalment
        d = bal
    return d


def schedule(r, horizon=240):
    """the months already taken (actual) and the plan of the rest (assuming each month takes its due within the cap)"""
    rows, run_bal = [], q2(r.total)
    for x in r.entries.filter(status='taken').order_by('month', 'id'):
        run_bal -= q2(x.amount)
        rows.append({'month': x.month.isoformat(), 'month_label': month_label(x.month), 'kind': x.kind, 'kind_label': tr(dict(x.KINDS).get(x.kind, x.kind)),
                     'due': str(q2(x.due)), 'amount': str(q2(x.amount)), 'carried': str(max(q2(x.due) - q2(x.amount), ZERO)) if x.kind == 'payroll' else '0.00',
                     'balance': str(max(run_bal, ZERO)), 'actual': True, 'payslip': x.payslip_id, 'note': x.note})
    if r.status not in ('active', 'paused', 'pending') or run_bal <= 0:
        return rows
    last = _last_payroll(r)
    m = add_months(last.month, 1) if last is not None else r.start_month
    if m < r.start_month:
        m = r.start_month
    carry = max(q2(last.due) - q2(last.amount), ZERO) if last is not None else ZERO
    cap = q2(r.monthly_cap) if r.monthly_cap else None
    bal = run_bal
    for _ in range(horizon):
        if bal <= 0:
            break
        d = bal if r.settle_next else min(bal, planned(r) + carry)
        if 0 < bal - d < Decimal('1.00'):
            d = bal
        a = min(d, cap) if cap else d
        if a <= 0:
            break
        bal -= a
        carry = d - a
        rows.append({'month': m.isoformat(), 'month_label': month_label(m), 'kind': 'plan', 'kind_label': tr('Planned'), 'due': str(d),
                     'amount': str(a), 'carried': str(carry), 'balance': str(bal), 'actual': False, 'paused': r.status == 'paused'})
        m = add_months(m, 1)
    return rows


# ------------------------------------------------------------------ rows
def number_of(r):
    return r.number or f'RCV-{r.pk:05d}'


def row(r, emps=None, comps=None, user=None, full=False):
    from .payctl import emp_label
    e = (emps or {}).get(r.employee_id) or M('EmpManagement', 'emp_master').objects.filter(pk=r.employee_id).first()
    c = (comps or {}).get(r.component_id) or M('PayrollManagement', 'SalaryComponent').objects.filter(pk=r.component_id).first()
    tk = taken(r)
    out = {'id': r.pk, 'number': number_of(r), 'employee_id': r.employee_id, 'employee': emp_label(e), 'emp_code': getattr(e, 'emp_code', ''),
           'branch_id': getattr(e, 'emp_branch_id_id', None), 'component_id': r.component_id, 'component': getattr(c, 'name', ''),
           'component_code': getattr(c, 'code', '') or '', 'total': str(q2(r.total)), 'taken': str(tk),
           'recovered': str(q2(r.entries.filter(status='taken', kind__in=('payroll', 'settled', 'exit')).aggregate(t=Sum('amount'))['t'] or 0)),
           'written_off': str(q2(r.entries.filter(status='taken', kind__in=('write_off', 'exit_write_off')).aggregate(t=Sum('amount'))['t'] or 0)),
           'balance': str(max(q2(r.total) - tk, ZERO)), 'start_month': r.start_month.isoformat(), 'start_label': month_label(r.start_month),
           'mode': r.mode, 'months': r.months, 'per_month': str(q2(r.per_month)), 'monthly_cap': str(q2(r.monthly_cap)) if r.monthly_cap else '',
           'reason': r.reason, 'status': r.status, 'status_label': tr(dict(r.STATUS).get(r.status, r.status)), 'exit_action': r.exit_action,
           'exit_label': tr(dict(r.EXIT).get(r.exit_action, r.exit_action)), 'settle_next': r.settle_next,
           'created_at': r.created_at.isoformat() if r.created_at else None}
    if r.general_request_id:
        GR = M('EmpManagement', 'GeneralRequest')
        gr = GR.objects.filter(pk=r.general_request_id).first()
        steps, mine = [], None
        if gr is not None:
            for a in gr.approvals.select_related('approver').order_by('level', 'pk'):
                steps.append({'level': a.level, 'role': a.role or '', 'approver': _user_name(a.approver), 'status': a.status, 'status_label': tr(a.status),
                              'note': a.note or ''})
                if user is not None and a.status == 'Pending' and (a.approver_id == getattr(user, 'pk', None) or getattr(a, 'deligate_to_id', None) == getattr(user, 'pk', None)):
                    mine = a.pk
        out.update(request_number=gr.document_number if gr else '', steps=steps, my_approval=mine)
    else:
        out.update(request_number='', steps=[], my_approval=None)
    if full:
        out['history'] = r.history or []
        out['schedule'] = schedule(r)
        out['entries'] = [{'id': x.pk, 'kind': x.kind, 'kind_label': tr(dict(x.KINDS).get(x.kind, x.kind)), 'month': x.month.isoformat(),
                           'due': str(q2(x.due)), 'amount': str(q2(x.amount)), 'payslip': x.payslip_id, 'status': x.status,
                           'status_label': tr('Given back (payslip rejected / deleted)') if x.status == 'given_back' else tr('Counted'), 'note': x.note}
                          for x in r.entries.all().order_by('month', 'id')]
    return out


# ------------------------------------------------------------------ create
def request_type(user=None):
    RT = M('EmpManagement', 'RequestType')
    rt = RT.objects.filter(name=REQUEST_TYPE).first()
    if rt is None:
        U = M('UserManagement', 'CustomUser')
        owner = user if getattr(user, 'pk', None) else U.objects.filter(is_superuser=True).order_by('pk').first()
        rt = RT.objects.create(name=REQUEST_TYPE, description='v1.63.0: recovery schedules (Payroll › Recoveries) – set the approval levels here',
                               created_by=owner)
    return rt


def needs_approval(rt, emp):
    """approval levels set for "Recovery schedule" (its own workflow with a first level, or the common workflow)"""
    if getattr(rt, 'use_common_workflow', False):
        CW = M('EmpManagement', 'CommonWorkflow')
        f = CW.objects.order_by('level').first()
        return bool(f and f.approver_id)
    AW = M('EmpManagement', 'ApprovalWorkflow')
    wf = AW.objects.filter(request_type=rt, branch=emp.emp_branch_id).first() or AW.objects.filter(request_type=rt).first()
    if wf is None or wf.approval_type == 'no_approval':
        return False
    if wf.approval_type == 'multi_approval':
        f = wf.levels.order_by('level').first()
        return bool(f and f.approver_id)
    return True


def validate(data, employee_id=None):
    from .payctl import _parse_month
    p = {}
    E = M('EmpManagement', 'emp_master')
    SC = M('PayrollManagement', 'SalaryComponent')
    try:
        e = E.objects.filter(pk=int(employee_id if employee_id is not None else data.get('employee'))).first()
    except (TypeError, ValueError):
        e = None
    if e is None:
        p['employee'] = tr('Choose the employee.')
    try:
        c = SC.objects.filter(pk=int(data.get('component'))).first()
    except (TypeError, ValueError):
        c = None
    if c is None:
        p['component'] = tr('Choose the type of recovery (a deduction component).')
    elif c.component_type != 'deduction':
        p['component'] = tr('{0} is not a deduction – choose a deduction component.', c.name)
    elif not c.show_in_payslip:
        p['component'] = tr('{0} is not shown on the payslip – it would not be deducted.', c.name)
    total = q2(data.get('total'))
    if total <= 0:
        p['total'] = tr('Enter the total to recover.')
    start = _parse_month(data.get('start_month'))
    if start is None:
        p['start_month'] = tr('Choose the first month.')
    mode = data.get('mode') or 'months'
    if mode not in ('months', 'amount'):
        p['mode'] = tr('Invalid.')
    months, per = None, ZERO
    if mode == 'months':
        try:
            months = int(data.get('months'))
            if months < 1 or months > 240:
                raise ValueError
        except (TypeError, ValueError):
            p['months'] = tr('Enter the number of months (1 to 240).')
        if months and total > 0:
            per = (total / months).quantize(Q2, rounding=ROUND_HALF_UP)
    else:
        per = q2(data.get('per_month'))
        if per <= 0:
            p['per_month'] = tr('Enter the amount per month.')
        elif total > 0 and per > total:
            p['per_month'] = tr('The amount per month is more than the total.')
        elif total > 0:
            months = int((total / per).to_integral_value(rounding=ROUND_CEILING))
    cap = None
    if str(data.get('monthly_cap') or '').strip() not in ('', 'None'):
        cap = q2(data.get('monthly_cap'))
        if cap <= 0:
            p['monthly_cap'] = tr('Enter a cap above 0, or leave it empty.')
    exit_action = data.get('exit_action') or 'deduct'
    if exit_action not in ('deduct', 'write_off'):
        p['exit_action'] = tr('Invalid.')
    reason = str(data.get('reason') or '').strip()[:255]
    if not reason:
        p['reason'] = tr('Enter the reason.')
    if p:
        raise Refused(tr('Check the marked fields.'), problems=p)
    if e.emp_joined_date and add_months(start, 1) <= e.emp_joined_date:
        raise Refused(tr('The first month is before the joining date.'), problems={'start_month': tr('Before the joining date.')})
    return {'employee': e, 'component': c, 'total': total, 'start_month': start, 'mode': mode, 'months': months, 'per_month': per,
            'monthly_cap': cap, 'exit_action': exit_action, 'reason': reason}


def preview(data):
    """the schedule of a recovery not saved yet (the form's preview)"""
    from types import SimpleNamespace
    v = validate(data)

    class _E:
        def filter(self, **k):
            return self

        def order_by(self, *a):
            return self

        def __iter__(self):
            return iter([])

        def first(self):
            return None

        def aggregate(self, **k):
            return {'t': 0}
    r = SimpleNamespace(total=v['total'], start_month=v['start_month'], per_month=v['per_month'], monthly_cap=v['monthly_cap'], settle_next=False,
                        status='active', entries=_E())
    rows = schedule(r)
    return {'per_month': str(v['per_month']), 'months': v['months'], 'schedule': rows,
            'last_month': rows[-1]['month_label'] if rows else '', 'instalments': len(rows)}


def _doc_number(r):
    from .models import Recovery
    n = f'RCV-{r.pk:05d}'
    k = 0
    while Recovery.objects.filter(number=n).exclude(pk=r.pk).exists():
        k += 1
        n = f'RCV-{r.pk:05d}-{k}'
    return n


def create(data, user=None):
    from .models import Recovery
    v = validate(data)
    e = v['employee']
    with transaction.atomic():
        r = Recovery.objects.create(employee_id=e.pk, component_id=v['component'].pk, total=v['total'], start_month=v['start_month'],
                                    mode=v['mode'], months=v['months'], per_month=v['per_month'], monthly_cap=v['monthly_cap'],
                                    exit_action=v['exit_action'], reason=v['reason'], status='pending', created_by_id=getattr(user, 'pk', None))
        r.number = _doc_number(r)
        _note(r, 'created', user, v['reason'])
        r.save(update_fields=['number', 'history'])
        rt = request_type(user)
        if not needs_approval(rt, e):
            r.status = 'active'
            _note(r, 'active', user, tr('No approval levels set for "Recovery schedule" – active at once.'))
            r.save(update_fields=['status', 'history'])
            return r
        GR = M('EmpManagement', 'GeneralRequest')
        branch = e.emp_branch_id or getattr(e, 'work_location', None)
        if branch is None:
            raise Refused(tr('The employee has no branch.'))
        DN = M('OrganisationManager', 'DocumentNumbering')
        num = None
        cfg = DN.objects.filter(branch_id=branch, type='general_request').first()
        if cfg is not None:
            try:
                num = cfg.next_unused(GR)
            except Exception:
                num = None
        num = num or r.number
        while GR.objects.filter(document_number=num).exists():
            num = f'{num}-1'
        gr = GR(document_number=num, reason=tr('Recovery {0} from {1}', r.number, month_label(r.start_month))[:200], branch=branch, request_type=rt,
                employee=e, status='Pending', remarks=f'{MARK}{r.pk}', created_by=user if getattr(user, 'pk', None) else None)
        gr.save()
        Recovery.objects.filter(pk=r.pk).update(general_request_id=gr.pk)
    r.refresh_from_db()
    gr.refresh_from_db()
    if gr.status == 'Approved' and r.status == 'pending':
        on_request_saved(gr)
        r.refresh_from_db()
    return r


def on_request_saved(gr):
    """post_save of a GeneralRequest: a "Recovery schedule" request approved → active; rejected → rejected."""
    rem = str(getattr(gr, 'remarks', '') or '')
    if not rem.startswith(MARK):
        return
    from .models import Recovery
    try:
        r = Recovery.objects.filter(pk=int(rem[len(MARK):])).first()
    except ValueError:
        return
    if r is None or r.status != 'pending':
        return
    st = str(gr.status or '').lower()
    if st == 'approved':
        r.status = 'active'
        _note(r, 'approved', None, gr.document_number or '')
        r.save(update_fields=['status', 'history', 'updated_at'])
    elif st == 'rejected':
        r.status = 'rejected'
        _note(r, 'rejected', None, '')
        r.save(update_fields=['status', 'history', 'updated_at'])


def decide(r, approve, user, note=''):
    A = M('EmpManagement', 'Approval')
    if r.status != 'pending' or not r.general_request_id:
        raise Refused(tr('This recovery is not waiting for approval.'))
    a = A.objects.filter(general_request_id=r.general_request_id, status='Pending').filter(Q(approver=user) | Q(deligate_to=user)).order_by('level').first()
    if a is None:
        raise Refused(tr('This step is not yours to approve.'), status=403)
    if approve:
        a.approve(note=note or None)
    else:
        if not str(note or '').strip():
            raise Refused(tr('Enter the reason for rejecting.'), problems={'note': tr('Enter the reason for rejecting.')})
        a.reject(note=note)
    r.refresh_from_db()
    if r.status == 'pending':
        on_request_saved(M('EmpManagement', 'GeneralRequest').objects.get(pk=r.general_request_id))
        r.refresh_from_db()
    return r


# ------------------------------------------------------------------ actions
def pause(r, user=None, note=''):
    if r.status != 'active':
        raise Refused(tr('Only an active recovery can be paused.'))
    r.status = 'paused'
    _note(r, 'paused', user, note)
    r.save(update_fields=['status', 'history', 'updated_at'])
    return r


def resume(r, user=None, note=''):
    if r.status != 'paused':
        raise Refused(tr('This recovery is not paused.'))
    r.status = 'active'
    _note(r, 'resumed', user, note)
    r.save(update_fields=['status', 'history', 'updated_at'])
    return r


def _close_if_done(r, status='completed'):
    if balance(r) <= 0 and r.status in OPEN + ('pending',):
        r.status = status
        r.save(update_fields=['status', 'updated_at'])


def settle_early(r, data, user=None):
    """mode 'paid': the employee paid the balance outside payroll (date, reference) → settled; mode 'next_payroll': the next payroll
    takes the whole balance (within the cap and the net-pay controls)."""
    from .payctl import _parse_day
    from .models import RecoveryEntry
    if r.status not in OPEN:
        raise Refused(tr('Only an active or paused recovery can be settled.'))
    bal = balance(r)
    if bal <= 0:
        raise Refused(tr('Nothing is left to recover.'))
    mode = data.get('mode') or 'paid'
    if mode == 'next_payroll':
        r.settle_next = True
        if r.status == 'paused':
            r.status = 'active'
        _note(r, 'settle_next', user, data.get('note') or '', amount=str(bal))
        r.save(update_fields=['settle_next', 'status', 'history', 'updated_at'])
        return r
    if mode != 'paid':
        raise Refused(tr('Check the marked fields.'), problems={'mode': tr('Invalid.')})
    on = _parse_day(data.get('paid_on')) or timezone.localdate()
    ref = str(data.get('reference') or '').strip()[:120]
    if not ref:
        raise Refused(tr('Check the marked fields.'), problems={'reference': tr('Enter the receipt or payment reference.')})
    with transaction.atomic():
        RecoveryEntry.objects.create(recovery=r, kind='settled', month=on.replace(day=1), due=bal, amount=bal, note=f'{on:%d/%m/%Y} {ref}'[:255],
                                     created_by_id=getattr(user, 'pk', None))
        r.status, r.settle_next = 'settled', False
        _note(r, 'settled', user, ref, amount=str(bal))
        r.save(update_fields=['status', 'settle_next', 'history', 'updated_at'])
    return r


def write_off(r, reason, user=None):
    from .models import RecoveryEntry
    if r.status not in OPEN:
        raise Refused(tr('Only an active or paused recovery can be written off.'))
    reason = str(reason or '').strip()[:255]
    if not reason:
        raise Refused(tr('Check the marked fields.'), problems={'reason': tr('Enter the reason.')})
    bal = balance(r)
    with transaction.atomic():
        if bal > 0:
            RecoveryEntry.objects.create(recovery=r, kind='write_off', month=timezone.localdate().replace(day=1), due=bal, amount=bal, note=reason,
                                         created_by_id=getattr(user, 'pk', None))
        r.status = 'written_off'
        _note(r, 'written_off', user, reason, amount=str(bal))
        r.save(update_fields=['status', 'history', 'updated_at'])
    return r


def cancel(r, user=None, note=''):
    if r.status not in ('pending', 'active', 'paused'):
        raise Refused(tr('This recovery cannot be cancelled.'))
    if r.entries.filter(status='taken').exists():
        raise Refused(tr('Instalments were already deducted – write the balance off instead.'))
    with transaction.atomic():
        if r.general_request_id and r.status == 'pending':
            M('EmpManagement', 'GeneralRequest').objects.filter(pk=r.general_request_id).update(status='Cancelled')
            M('EmpManagement', 'Approval').objects.filter(general_request_id=r.general_request_id, status='Pending').delete()
        r.status = 'cancelled'
        _note(r, 'cancelled', user, note)
        r.save(update_fields=['status', 'history', 'updated_at'])
    return r


def change(r, data, user=None):
    """monthly cap and the exit choice can be changed while the recovery runs"""
    if r.status not in ('pending',) + OPEN:
        raise Refused(tr('This recovery is closed.'))
    fields, notes = [], []
    if 'monthly_cap' in data:
        cap = None if str(data.get('monthly_cap') or '').strip() in ('', 'None') else q2(data.get('monthly_cap'))
        if cap is not None and cap <= 0:
            raise Refused(tr('Check the marked fields.'), problems={'monthly_cap': tr('Enter a cap above 0, or leave it empty.')})
        r.monthly_cap = cap
        fields.append('monthly_cap'); notes.append(tr('monthly cap {0}', f'{cap:,.2f}' if cap else tr('none')))
    if 'exit_action' in data:
        if data['exit_action'] not in ('deduct', 'write_off'):
            raise Refused(tr('Check the marked fields.'), problems={'exit_action': tr('Invalid.')})
        r.exit_action = data['exit_action']
        fields.append('exit_action'); notes.append(tr(dict(r.EXIT)[r.exit_action]))
    if fields:
        _note(r, 'changed', user, '; '.join(notes))
        r.save(update_fields=fields + ['history', 'updated_at'])
    return r


# ------------------------------------------------------------------ payroll
def _component_line(payslip, comp, amount):
    PC = M('PayrollManagement', 'PayslipComponent')
    return PC.objects.create(payslip=payslip, component=comp, amount=amount)


def give_back_payslip(payslip_id, delete_lines=True):
    """the instalments a payslip took are given back (payslip deleted / processed again): entries removed, lines removed"""
    from .models import RecoveryEntry
    PC = M('PayrollManagement', 'PayslipComponent')
    es = list(RecoveryEntry.objects.filter(payslip_id=payslip_id, kind='payroll').select_related('recovery'))
    if not es:
        return 0
    if delete_lines:
        PC.objects.filter(pk__in=[x.line_id for x in es if x.line_id]).delete()
    recs = {x.recovery for x in es}
    RecoveryEntry.objects.filter(pk__in=[x.pk for x in es]).delete()
    try:   # the payslip's recovery notes go with them
        from PayrollManagement import payroll_guards as G
        n = G.notes_of(payslip_id)
        if n.pop('recoveries', None) is not None:
            G._set_opt(G._NOTES.format(payslip_id), n)
    except Exception:
        log.exception('recovery notes of payslip %s', payslip_id)
    for r in recs:
        if r.status == 'completed' and balance(r) > 0:
            r.status = 'active'
            r.save(update_fields=['status', 'updated_at'])
    return len(es)


def on_payslip(run, payslip, employee, skip_events=False):
    """the month's instalments of the employee's active recoveries (see the module text). → [{recovery, due, amount, left, carried}]"""
    from .models import Recovery, RecoveryEntry
    if skip_events:
        return []
    m = date(int(run.year), int(run.month), 1)
    recs = list(Recovery.objects.filter(employee_id=employee.pk, status='active', start_month__lte=m).order_by('pk'))
    if not recs and not RecoveryEntry.objects.filter(payslip_id=payslip.pk).exists():
        return []
    give_back_payslip(payslip.pk)   # processed again: the earlier instalments of this payslip first
    if not recs:
        return []
    PC = M('PayrollManagement', 'PayslipComponent')
    SC = M('PayrollManagement', 'SalaryComponent')
    from PayrollManagement import payroll_guards as G
    st = G.net_settings()
    room = None
    _v16711_lim = G.limit_of(st)   # v1.67.11 (loans, item 51): the legal deduction limit counts too
    if st['keep_net_positive'] or _v16711_lim['legal_pct'] is not None:
        lines = list(PC.objects.filter(payslip=payslip).select_related('component'))
        gross = sum((q2(x.amount) for x in lines if x.component.show_in_payslip and x.component.component_type == 'addition'), ZERO)
        ded = sum((q2(x.amount) for x in lines if x.component.show_in_payslip and x.component.component_type == 'deduction' and q2(x.amount) > 0), ZERO)
        carry = sum((q2(x.get('amount')) for x in G._carries(employee.pk) if x.get('status') == 'pending' and x.get('from_payslip') != payslip.pk), ZERO)
        lim, _v16711_why = G.room_of(gross, st, _v16711_lim)
        room = max(lim - ded - carry, ZERO)
    out = []
    comps = {c.pk: c for c in SC.objects.filter(pk__in=[r.component_id for r in recs])}
    alive = set(M('PayrollManagement', 'Payslip').objects.filter(employee_id=employee.pk).values_list('pk', flat=True))
    for r in recs:
        comp = comps.get(r.component_id)
        if comp is None:
            continue
        if RecoveryEntry.objects.filter(recovery=r, kind='payroll', status='taken', month=m, payslip_id__in=alive).exclude(payslip_id=payslip.pk).exists():
            continue   # one instalment a month (a second run of the month takes nothing)
        bal = balance(r)
        due = due_for(r, m, bal)
        if due <= 0:
            continue
        amt, why = due, []
        if r.monthly_cap and amt > q2(r.monthly_cap):
            amt = q2(r.monthly_cap); why.append('cap')
        if room is not None and amt > room:
            amt = room; why.append('net')
        line = _component_line(payslip, comp, amt) if amt > 0 else None
        RecoveryEntry.objects.create(recovery=r, kind='payroll', month=m, due=due, amount=amt, payslip_id=payslip.pk,
                                     line_id=getattr(line, 'pk', None), note=','.join(why))
        if room is not None:
            room -= amt
        left = bal - amt
        carried = due - amt
        if r.settle_next and amt >= bal:
            r.settle_next = False
            r.save(update_fields=['settle_next', 'updated_at'])
        if left <= 0:
            r.status = 'completed'
            r.save(update_fields=['status', 'updated_at'])
        item = {'recovery_id': r.pk, 'ref': number_of(r), 'name': comp.name, 'amount': str(amt), 'left': str(left), 'carried': str(carried),
                'why': why}
        try:
            G._add_note(payslip.pk, 'recoveries', item)
        except Exception:
            log.exception('recovery note of payslip %s', payslip.pk)
        out.append(item)
    return out


# rejected payslip (PayrollPlus/payslip_reject.py)
def give_back_rejected(payslip_id):
    from .models import RecoveryEntry
    es = list(RecoveryEntry.objects.filter(payslip_id=payslip_id, kind='payroll', status='taken').select_related('recovery'))
    RecoveryEntry.objects.filter(pk__in=[x.pk for x in es]).update(status='given_back')
    for r in {x.recovery for x in es}:
        if r.status == 'completed' and balance(r) > 0:
            r.status = 'active'
            r.save(update_fields=['status', 'updated_at'])
    return [x.pk for x in es]


def rejected_problems(ids):
    """the entries given back were taken again since (the next payslip took the balance): approving again would recover twice"""
    from .models import RecoveryEntry
    for x in RecoveryEntry.objects.filter(pk__in=ids or [], status='given_back').select_related('recovery'):
        if not _can_retake(x):
            return True
    return False


def _can_retake(x):
    """the instalment of a rejected payslip can be counted again: no other payslip took that month's instalment since, and the
    balance still covers it"""
    from .models import RecoveryEntry
    other = RecoveryEntry.objects.filter(recovery_id=x.recovery_id, kind='payroll', status='taken', month=x.month).exclude(pk=x.pk).exists()
    return not other and balance(x.recovery) >= q2(x.amount)


def retake_rejected(ids):
    from .models import RecoveryEntry
    n = 0
    for x in RecoveryEntry.objects.filter(pk__in=ids or [], status='given_back').select_related('recovery'):
        if _can_retake(x):
            x.status = 'taken'
            x.save(update_fields=['status'])
            _close_if_done(x.recovery)
            n += 1
    return n


# ------------------------------------------------------------------ final settlement
def exit_lines(emp, add, info):
    """PayrollPlus.services.compute: the balance of each open recovery – a deduction line, or an information line (write off at exit)"""
    from .models import Recovery
    SC = M('PayrollManagement', 'SalaryComponent')
    for r in Recovery.objects.filter(employee_id=emp.pk, status__in=OPEN).order_by('pk'):
        bal = balance(r)
        if bal <= 0:
            continue
        c = SC.objects.filter(pk=r.component_id).first()
        name = getattr(c, 'name', '') or tr('Recovery')
        if r.exit_action == 'write_off':
            info(f'recovery_{r.pk}', tr('Recovery {0} ({1}) – balance {2} written off at exit', number_of(r), name, f'{bal:,.2f}'),
                 tr('recovered {0:,.2f} of {1:,.2f}', taken(r), q2(r.total)), 'recovery')
        else:
            add(f'recovery_{r.pk}', tr('Recovery {0} ({1}) – balance', number_of(r), name), 'deduction', bal,
                tr('recovered {0:,.2f} of {1:,.2f}', taken(r), q2(r.total)), 'recovery', component_id=r.component_id)


def close_exit(st, user=None):
    """a paid final settlement closes the recoveries: the balance deducted (entry 'exit') or written off at exit"""
    from .models import Recovery, RecoveryEntry
    out = []
    lines = {str(l.get('key')): l for l in (st.lines or [])}
    when = (st.paid_on or timezone.localdate())
    for r in Recovery.objects.filter(employee_id=st.employee_id, status__in=OPEN):
        bal = balance(r)
        ln = lines.get(f'recovery_{r.pk}')
        if bal <= 0:
            _close_if_done(r)
            continue
        if ln is not None and ln.get('kind') == 'deduction':
            amt = min(q2(ln.get('amount')), bal)
            RecoveryEntry.objects.create(recovery=r, kind='exit', month=when.replace(day=1), due=bal, amount=amt, settlement_id=st.pk,
                                         note=tr('Final settlement {0}', st.number or st.pk)[:255], created_by_id=getattr(user, 'pk', None))
            _note(r, 'exit', user, tr('Final settlement {0}', st.number or st.pk), amount=str(amt))
            r.save(update_fields=['history'])
            _close_if_done(r)
            r.refresh_from_db()
            if balance(r) > 0 and q2(ln.get('receivable') or 0) > 0:   # v1.67.11 (loans, item 116): over the settlement recovery limit –
                _note(r, 'exit', user, tr('Still owed by the employee after final settlement {0}', st.number or st.pk),   # the rest stays owed
                      amount=str(balance(r)))
                r.save(update_fields=['history'])
            elif balance(r) > 0:   # HR changed the line: the rest is written off with the exit
                rest = balance(r)
                RecoveryEntry.objects.create(recovery=r, kind='exit_write_off', month=when.replace(day=1), due=rest, amount=rest, settlement_id=st.pk,
                                             note=tr('Rest written off at exit'), created_by_id=getattr(user, 'pk', None))
                r.status = 'written_off'
                r.save(update_fields=['status', 'updated_at'])
        elif r.exit_action == 'write_off' or ln is not None:
            RecoveryEntry.objects.create(recovery=r, kind='exit_write_off', month=when.replace(day=1), due=bal, amount=bal, settlement_id=st.pk,
                                         note=tr('Written off at exit – final settlement {0}', st.number or st.pk)[:255], created_by_id=getattr(user, 'pk', None))
            r.status = 'written_off'
            _note(r, 'exit_write_off', user, tr('Final settlement {0}', st.number or st.pk), amount=str(bal))
            r.save(update_fields=['status', 'history', 'updated_at'])
        else:
            continue
        out.append(number_of(r))
    return out


# ------------------------------------------------------------------ import
TEMPLATE_COLUMNS = ['employee_code', 'component_code', 'total', 'start_month', 'months', 'per_month', 'monthly_cap', 'exit_action', 'reason']


def template_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font
    import io
    wb = Workbook(); ws = wb.active; ws.title = 'Recoveries'
    ws.append(TEMPLATE_COLUMNS)
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.append(['E001', 'ACCOM', 3000, '2027-01', 6, '', '', 'deduct', 'Accommodation deposit'])
    ws.append(['E002', 'UNIFORM', 450, '2027-02', '', 150, 200, 'write_off', 'Uniforms'])
    b = io.BytesIO(); wb.save(b)
    return b.getvalue()


def import_rows(f, name='', apply=False, user=None, allowed=None):
    from .payctl import _read_rows, emp_label
    E = M('EmpManagement', 'emp_master')
    SC = M('PayrollManagement', 'SalaryComponent')
    try:
        rows = _read_rows(f, name)
    except Exception as ex:
        raise Refused(tr('The file could not be read – use the template (CSV or Excel).'), problems={'file': str(ex)[:200]})
    if not rows:
        raise Refused(tr('The file has no rows.'), problems={'file': tr('The file has no rows.')})
    missing = [c for c in ('employee_code', 'component_code', 'total', 'start_month') if c not in rows[0]]
    if missing:
        raise Refused(tr('Columns missing: {0}', ', '.join(missing)), problems={'file': tr('Columns missing: {0}', ', '.join(missing))})
    out = []
    for i, r in enumerate(rows, start=2):
        errs = []

        def s(k):
            v = r.get(k)
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            if hasattr(v, 'strftime'):
                v = v.strftime('%Y-%m')
            return str(v if v is not None else '').strip()
        code, ccode = s('employee_code'), s('component_code')
        e = E.objects.filter(emp_code__iexact=code).first() if code else None
        if e is None:
            errs.append(tr('Employee {0} not found.', code or '–'))
        elif allowed is not None and e.pk not in allowed:
            errs.append(tr('This employee is not in your branches.'))
            e = None
        cs = list(SC.objects.filter(code__iexact=ccode)) if ccode else []
        c = (next((x for x in cs if e is not None and x.branch_id == e.emp_branch_id_id), None) or next((x for x in cs if x.branch_id is None), None)
             or (cs[0] if cs else None))
        if c is None:
            errs.append(tr('Component {0} not found.', ccode or '–'))
        months, per = s('months'), s('per_month')
        if months and per:
            errs.append(tr('Enter the number of months or the amount per month, not both.'))
        data = {'employee': e.pk if e else None, 'component': c.pk if c else None, 'total': s('total') or None, 'start_month': s('start_month')[:7],
                'mode': 'amount' if per and not months else 'months', 'months': months, 'per_month': per, 'monthly_cap': s('monthly_cap'),
                'exit_action': s('exit_action') or 'deduct', 'reason': s('reason') or tr('Imported')}
        v = None
        if e is not None and c is not None:
            try:
                v = validate(data)
            except Refused as ex:
                errs += [str(x) for x in ex.problems.values()] or [ex.message]
        out.append({'row': i, 'employee_code': code, 'employee': emp_label(e) if e else '', 'component_code': ccode, 'component': getattr(c, 'name', ''),
                    'total': data['total'] or '', 'start_month': data['start_month'], 'months': (v or {}).get('months') or months,
                    'per_month': str((v or {}).get('per_month') or per or ''), 'monthly_cap': data['monthly_cap'], 'errors': errs, 'ok': not errs, '_d': data})
    applied = 0
    if apply:
        with transaction.atomic():
            for x in out:
                if x['ok']:
                    create(x['_d'], user)
                    applied += 1
    for x in out:
        x.pop('_d', None)
    return {'rows': out, 'ok': sum(1 for x in out if x['ok']), 'errors': sum(1 for x in out if not x['ok']), 'applied': applied, 'dry_run': not apply}
