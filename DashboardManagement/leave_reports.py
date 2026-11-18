"""v1.73.8 – leave reports by leave category and month, leave grants and anniversary credits, unpaid days (loss of pay) by month,
and the Bradford factor.

* leave-by-month        employee × leave category: approved days in each month of the period (+ total)
* leave-credits         leave grants and service anniversary credits (leave ledger) in the period
* leave-lop             employee × month: unpaid days (unpaid leave types, days not approved, days over the balance taken as
                        unpaid) and part-paid days (pay slabs below 100 %)
* leave-bradford        S² × D of the unplanned leave (sick and casual leave and the company categories like them) in the period
"""
from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from collections import defaultdict
from datetime import date, timedelta

from .reports import EMP_COLS, M, drill, emp_cols, src

SEL = ('employee__emp_branch_id', 'employee__emp_dept_id', 'employee__emp_desgntn_id', 'employee__emp_ctgry_id', 'leave_type')


def _months(run):
    a = (run.date_from or _zt_today().replace(month=1, day=1)).replace(day=1)
    b = run.date_to or _zt_today()
    out = []
    while a <= b and len(out) < 36:
        out.append(a)
        a = (a + timedelta(days=32)).replace(day=1)
    return out


def _approved(run):
    return M('calendars', 'employee_leave_request').objects.filter(run.emp_q(), status='approved', start_date__lte=run.date_to,
                                                                   end_date__gte=run.date_from).select_related(*SEL)


def _split(req):
    """[(day, paid fraction, unit)] of an approved request (the leave engine's own split; per day when no policy)."""
    from LeavePolicy import engine
    try:
        if engine.is_hourly(req.leave_type):
            return []
        return engine.paid_split(req)
    except Exception:   # noqa: BLE001
        unit = 0.5 if req.dis_half_day else 1.0
        d, out = req.start_date, []
        while d <= req.end_date:
            out.append((d, 0.0 if req.leave_type.type == 'unpaid' else 1.0, unit))
            d += timedelta(days=1)
        return out


def _cat(lt):
    from LeavePolicy.categories import label
    return label(lt.leave_category or 'annual', header=False)


def r_leave_by_month(run):
    months = _months(run)
    keys = [f'm{m:%Y%m}' for m in months]
    rows = {}
    for r in _approved(run):
        k = (r.employee_id, r.leave_type.leave_category)
        row = rows.get(k)
        if row is None:
            row = rows[k] = {**emp_cols(r.employee), 'leave_category': _cat(r.leave_type), **{x: 0.0 for x in keys}, 'total': 0.0,
                             '_cell': {}}
        for d, paid, unit in _split(r):
            if not (run.date_from <= d <= run.date_to):
                continue
            x = f'm{d:%Y%m}'
            if x in row:
                row[x] = round(row[x] + unit, 2)
                row['total'] = round(row['total'] + unit, 2)
    out = sorted(rows.values(), key=lambda r: (r['employee'], r['leave_category']))
    for r in out:
        r['_cell'] = {'total': drill('leave', {'employee': r['employee']}, **{'from': run.date_from, 'to': run.date_to})}
    return EMP_COLS + [('leave_category', 'Leave category', 'text')] + [(x, f'{m:%b %Y}', 'number') for x, m in zip(keys, months)] + \
        [('total', 'Total', 'number')], out


def r_leave_credits(run):
    from LeavePolicy.models import LeaveLedger
    emps = {e.pk: e for e in run.all_employees}
    types = {t.pk: t for t in M('calendars', 'leave_type').objects.all()}
    rows = []
    for l in LeaveLedger.objects.filter(employee_id__in=list(emps), kind__in=('grant', 'anniversary'), date__gte=run.date_from, date__lte=run.date_to) \
            .order_by('date', 'id'):
        e, lt = emps[l.employee_id], types.get(l.leave_type_id)
        rows.append({**emp_cols(e), 'type': lt.name if lt else '', 'leave_category': _cat(lt) if lt else '', 'date': l.date,
                     'entry': l.get_kind_display(), 'days': l.days, 'note': l.note, **src(e)})
    return EMP_COLS + [('type', 'Leave type', 'text'), ('leave_category', 'Leave category', 'text'), ('date', 'Date', 'date'),
                       ('entry', 'Entry', 'text'), ('days', 'Days', 'number'), ('note', 'Reason / note', 'text')], rows


def r_leave_lop(run):
    months = _months(run)
    keys = [f'm{m:%Y%m}' for m in months]
    rows = {}
    for r in _approved(run):
        for d, paid, unit in _split(r):
            if not (run.date_from <= d <= run.date_to) or paid >= 1:
                continue
            row = rows.get(r.employee_id)
            if row is None:
                row = rows[r.employee_id] = {**emp_cols(r.employee), **{x: 0.0 for x in keys}, 'unpaid': 0.0, 'part_paid': 0.0}
            if paid <= 0:
                x = f'm{d:%Y%m}'
                if x in row:
                    row[x] = round(row[x] + unit, 2)
                row['unpaid'] = round(row['unpaid'] + unit, 2)
            else:
                row['part_paid'] = round(row['part_paid'] + unit, 2)
    out = sorted(rows.values(), key=lambda r: -r['unpaid'])
    return EMP_COLS + [(x, f'{m:%b %Y}', 'number') for x, m in zip(keys, months)] + \
        [('unpaid', 'Unpaid days (total)', 'number'), ('part_paid', 'Part-paid days', 'number')], out


def r_leave_bradford(run):
    from LeavePolicy.categories import keys_like
    cats = keys_like('sick') | keys_like('casual')
    by_emp = defaultdict(list)
    for r in _approved(run).filter(leave_type__leave_category__in=cats).order_by('start_date'):
        by_emp[r.employee_id].append(r)
    rows = []
    for eid, reqs in by_emp.items():
        spells, days, last = 0, 0.0, None
        for r in reqs:
            ds = [(d, u) for d, p, u in _split(r) if run.date_from <= d <= run.date_to]
            if not ds:
                continue
            first = ds[0][0]
            if last is None or (first - last).days > 3:   # a leave right after the last one (only a weekend between) is the same spell
                spells += 1
            days += sum(u for _, u in ds)
            last = max(last or ds[-1][0], ds[-1][0])
        if not spells:
            continue
        score = spells * spells * days
        band = 'High' if score >= 500 else 'Watch' if score >= 125 else 'Low'
        e = reqs[0].employee
        rows.append({**emp_cols(e), 'spells': spells, 'days': round(days, 2), 'score': round(score, 1), 'band': band,
                     '_cell': {'spells': drill('leave', {'employee': emp_cols(e)['employee']}, **{'from': run.date_from, 'to': run.date_to})}, **src(e)})
    rows.sort(key=lambda r: -r['score'])
    return EMP_COLS + [('spells', 'Spells (S)', 'number'), ('days', 'Days (D)', 'number'), ('score', 'Bradford score (S² × D)', 'number'),
                       ('band', 'Band', 'text')], rows


VIEW = ['view_leavereport', 'view_employee_leave_request', 'view_lvbalancereport']
REPORTS = {
    'leave-by-month': ('Leave taken by category and month', 'Leave & attendance', 'Approved leave days of each employee by leave category, one column per month, with the total.', r_leave_by_month, 'year', VIEW),
    'leave-credits': ('Leave grants and anniversary credits', 'Leave & attendance', 'Days given by HR (leave grants) and the extra days credited on service anniversaries, in the period.', r_leave_credits, 'year', VIEW + ['view_emp_leave_balance']),
    'leave-lop': ('Unpaid leave (loss of pay) by month', 'Leave & attendance', 'Unpaid leave days of each employee by month – unpaid leave types, days the approver did not approve and days over the balance taken as unpaid – with the part-paid days (pay steps below 100 %).', r_leave_lop, 'year', VIEW),
    'leave-bradford': ('Bradford factor', 'Leave & attendance', 'S² × D of the unplanned leave (sick and casual leave) in the period: S = separate spells, D = days. 125 and above: watch; 500 and above: high.', r_leave_bradford, 'year', VIEW),
}
REPORT_MODELS = {'leave-by-month': {'EmpManagement.emp_master'}, 'leave-credits': {'EmpManagement.emp_master'},
                 'leave-lop': {'EmpManagement.emp_master'}, 'leave-bradford': {'EmpManagement.emp_master'}}
