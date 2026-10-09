"""
Rejoining after leave (v1.11.0). HR records the day the employee is back; ZEO works out the gap or the early return
and settles it the way HR chooses:

  late  → unpaid leave · from a leave balance (e.g. annual) · balance first, the rest unpaid · absent · excused (paid)
  early → the days not used go back to the balance and the leave is shortened

Each choice is written where payroll and reports read it: an approved leave request (balance, ledger, attendance calendar
with the unpaid part), or attendance-calendar days (absent = unpaid, excused = paid).
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.apps import apps
from django.db import transaction

log = logging.getLogger(__name__)
M = apps.get_model
TAG = 'Rejoining'


def _mode(req):
    try:
        from LeavePolicy.engine import line_for
        line = line_for(req.employee, req.leave_type_id)
        if line:
            return line.count_days
    except Exception:
        pass
    return 'calendar' if req.leave_type.include_weekend else 'working'


def _days_list(emp, a, b, mode):
    from LeavePolicy.engine import _weekend_days, _holidays
    wk, hol = (_weekend_days(emp), _holidays(emp)) if mode == 'working' else (set(), set())
    out, d = [], a
    while d <= b:
        if mode != 'working' or (d.strftime('%A').lower() not in wk and d not in hol):
            out.append(d)
        d += timedelta(days=1)
    return out


def gap(rj, count='working'):
    """{'kind': late|early|on_time, 'days': n, 'dates': [...]} for a rejoining record."""
    req, back = rj.leave_request, rj.rejoining_date
    if back <= req.end_date:
        dates = _days_list(req.employee, back, req.end_date, _mode(req))
        return {'kind': 'early' if dates else 'on_time', 'days': float(len(dates)), 'dates': dates}
    dates = _days_list(req.employee, req.end_date + timedelta(days=1), back - timedelta(days=1), count)
    return {'kind': 'late' if dates else 'on_time', 'days': float(len(dates)), 'dates': dates}


def _unpaid_type(emp):
    LT = M('calendars', 'leave_type')
    qs = LT.objects.filter(type='unpaid').exclude(name__icontains='hajj').exclude(name__icontains='maternity')
    return qs.filter(branch=emp.emp_branch_id).first() or qs.filter(branch__isnull=True).first() or qs.first()


def _doc_no(emp):
    DN = M('OrganisationManager', 'DocumentNumbering')
    LR = M('calendars', 'employee_leave_request')
    cfg = DN.objects.filter(branch_id=emp.emp_branch_id_id, type='leave_request').first()
    if cfg is None:
        n = LR.objects.count() + 1
        while LR.objects.filter(document_number=f'RJ-{n:05d}').exists():
            n += 1
        return f'RJ-{n:05d}'
    no = cfg.get_next_number()
    for _ in range(500):
        if not LR.objects.filter(document_number=no).exists():
            break
        no = cfg.get_next_number()
    return no


def _approved_request(emp, lt, dates, user, reason):
    """An approved leave request for consecutive dates (HR settlement – no approval steps, no notice checks)."""
    LR = M('calendars', 'employee_leave_request')
    r = LR(employee=emp, branch=emp.emp_branch_id, leave_type=lt, start_date=dates[0], end_date=dates[-1], reason=reason,
           status='approved', document_number=_doc_no(emp), created_by=user)
    r._hr_direct = True
    r.save()
    r.refresh_from_db()
    return r


def _runs(dates):
    """Split dates into runs of consecutive days."""
    runs, cur = [], []
    for d in dates:
        if cur and (d - cur[-1]).days != 1:
            runs.append(cur)
            cur = []
        cur.append(d)
    if cur:
        runs.append(cur)
    return runs


def plan(rj, treatment, leave_type_id=None, count='working'):
    g = gap(rj, count)
    emp = rj.employee
    out = {'kind': g['kind'], 'days': g['days'], 'from': g['dates'][0].isoformat() if g['dates'] else None,
           'to': g['dates'][-1].isoformat() if g['dates'] else None, 'lines': [], 'problems': []}
    if g['kind'] == 'on_time':
        out['lines'].append('Back on time – nothing to settle.')
        return out
    if g['kind'] == 'early':
        out['lines'].append(f'{g["days"]:g} day(s) of {rj.leave_request.leave_type.name} not used – back to the balance; the leave ends {rj.rejoining_date - timedelta(days=1):%d/%m/%Y}.')
        return out
    LT = M('calendars', 'leave_type')
    if treatment in ('leave', 'split'):
        lt = LT.objects.filter(pk=leave_type_id).first()
        if lt is None:
            out['problems'].append('Choose the leave type to take the days from.')
            return out
        from LeavePolicy.engine import balance_row
        bal = float(balance_row(emp.pk, lt.pk).balance or 0)
        take = g['days'] if treatment == 'leave' else min(g['days'], max(bal, 0))
        if treatment == 'leave' and bal < g['days'] and not lt.negative:
            out['problems'].append(f'Only {bal:g} days of {lt.name} – choose "balance first, rest unpaid" or another leave type.')
        out['lines'].append(f'{take:g} day(s) as {lt.name} (balance {bal:g} → {bal - take:g}).')
        if treatment == 'split' and g['days'] - take > 0:
            out['lines'].append(f'{g["days"] - take:g} day(s) unpaid leave.')
    elif treatment == 'unpaid':
        if _unpaid_type(emp) is None:
            out['problems'].append('There is no unpaid leave type.')
        out['lines'].append(f'{g["days"]:g} day(s) unpaid leave – deducted from salary.')
    elif treatment == 'absent':
        out['lines'].append(f'{g["days"]:g} day(s) absent – deducted from salary, shown as absence.')
    elif treatment == 'excused':
        out['lines'].append(f'{g["days"]:g} day(s) excused – paid, no leave taken.')
    else:
        out['problems'].append('Choose how to count the days.')
    return out


@transaction.atomic
def settle(rj, treatment, leave_type_id=None, user=None, note='', count='working'):
    from .models import RejoinSettlement
    from LeavePolicy import engine
    if RejoinSettlement.objects.filter(rejoining_id=rj.pk).exists():
        raise ValueError('This rejoining is already settled.')
    p = plan(rj, treatment, leave_type_id, count)
    if p['problems']:
        raise ValueError(' '.join(p['problems']))
    g = gap(rj, count)
    emp, req = rj.employee, rj.leave_request
    s = RejoinSettlement(rejoining_id=rj.pk, employee_id=emp.pk, leave_request_id=req.pk, rejoin_date=rj.rejoining_date, gap_days=g['days'],
                         treatment=g['kind'] if g['kind'] in ('early', 'on_time') else treatment, leave_type_id=leave_type_id, note=note[:255],
                         settled_by_id=getattr(user, 'pk', None))
    AC = M('calendars', 'AttendanceCalendar')
    LR = M('calendars', 'employee_leave_request')
    if g['kind'] == 'early':
        old = float(req.approved_days or req.number_of_days or 0)
        new_end = rj.rejoining_date - timedelta(days=1)
        if new_end < req.start_date:
            raise ValueError('The employee came back before the leave started – cancel the leave instead.')
        req.end_date = new_end
        new = float(req.calculate_leave_days())
        back = round(old - new, 2)
        LR.objects.filter(pk=req.pk).update(end_date=new_end, number_of_days=new, approved_days=new, applied_days=new)
        engine.post(emp.pk, req.leave_type_id, rj.rejoining_date, 'cancelled', back, ref=req, note=f'{req.document_number}: back early on {rj.rejoining_date:%d/%m/%Y}',
                    user_id=getattr(user, 'pk', None), move_balance=True)
        AC.objects.filter(employee=emp, date__gte=rj.rejoining_date, date__lte=g['dates'][-1], is_manual=False, remarks__startswith=engine.TAG).delete()
        s.returned_days = back
    elif g['kind'] == 'late':
        dates = g['dates']
        reason = f'Late return after {req.document_number} (rejoined {rj.rejoining_date:%d/%m/%Y})'
        if treatment in ('leave', 'split'):
            lt = M('calendars', 'leave_type').objects.get(pk=leave_type_id)
            bal = float(engine.balance_row(emp.pk, lt.pk).balance or 0)
            n = len(dates) if treatment == 'leave' else int(min(len(dates), max(bal, 0)))
            for run in _runs(dates[:n]):
                s.request_ids.append(_approved_request(emp, lt, run, user, reason).pk)
            s.days_from_leave = float(n)
            rest = dates[n:]
            if rest:
                ut = _unpaid_type(emp)
                for run in _runs(rest):
                    s.request_ids.append(_approved_request(emp, ut, run, user, reason + ' – no balance left').pk)
                s.unpaid_days = float(len(rest))
        elif treatment == 'unpaid':
            ut = _unpaid_type(emp)
            for run in _runs(dates):
                s.request_ids.append(_approved_request(emp, ut, run, user, reason).pk)
            s.unpaid_days = float(len(dates))
        elif treatment in ('absent', 'excused'):
            for d in dates:
                AC.objects.update_or_create(employee=emp, date=d, defaults={
                    'status': 'Absent' if treatment == 'absent' else 'Present', 'leave_type': None, 'is_half_day': False,
                    'unpaid_fraction': Decimal('1.00') if treatment == 'absent' else Decimal('0.00'), 'is_manual': True,
                    'remarks': f'{TAG}: {"absent" if treatment == "absent" else "excused"} after {req.document_number}'})
            if treatment == 'absent':
                s.absent_days = float(len(dates))
            else:
                s.excused_days = float(len(dates))
    s.save()
    M('calendars', 'EmployeeRejoining').objects.filter(pk=rj.pk).update(unpaid_leave_days=s.unpaid_days + s.absent_days, deducted=True,
                                                                        deduct_from_leave_type_id=leave_type_id if treatment in ('leave', 'split') else None)
    try:
        M('Chatter', 'Message').objects.create(model='calendars.employee_leave_request', object_id=str(req.pk), author=user,
                                               body=f'Rejoined {rj.rejoining_date:%d/%m/%Y}: ' + ' '.join(p['lines']) + (f' – {note}' if note else ''))
    except Exception:
        log.exception('rejoin note failed')
    return s, p
