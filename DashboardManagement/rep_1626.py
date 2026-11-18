"""v1.67.26 (pending item 1.62.12, item 236) – Bradford factor.

Bradford factor = S² × D for each employee over the period (default: the last 52 weeks)
  S = spells of unplanned absence: each sick-leave request is one spell, and each run of consecutive "Absent" days in the attendance
      calendar (no leave, no permission) is one spell;
  D = the days of those spells (half days count 0.5).
Many short absences score far higher than one long one (10 one-day absences: 10² × 10 = 1,000; one 10-day absence: 1² × 10 = 10).
Bands (the usual triggers, a guide for a conversation – never an automatic sanction):
  0 – 50 no concern · 51 – 124 keep an eye · 125 – 399 talk to the employee · 400 and above formal review.
Read-only: no table of its own.
"""
from datetime import timedelta

from django.apps import apps

M = apps.get_model
BANDS = [(400, 'Formal review'), (125, 'Talk to the employee'), (51, 'Keep an eye'), (0, 'No concern')]


def band(score):
    return next(label for low, label in BANDS if score >= low)


def spells(run):
    """{employee id: [(start, end, days, kind)]} of the period."""
    out = {}
    LR = M('calendars', 'employee_leave_request')
    sick = LR.objects.filter(run.emp_q(), status='approved', start_date__lte=run.date_to, end_date__gte=run.date_from).filter(
        leave_type__name__icontains='sick').values('employee_id', 'start_date', 'end_date', 'number_of_days', 'dis_half_day')
    for r in sick:
        days = float(r['number_of_days'] or 0) or (0.5 if r['dis_half_day'] else (r['end_date'] - r['start_date']).days + 1)
        out.setdefault(r['employee_id'], []).append((r['start_date'], r['end_date'], days, 'Sick leave'))
    AC = M('calendars', 'AttendanceCalendar')
    rows = AC.objects.filter(run.emp_q(), date__gte=run.date_from, date__lte=run.date_to).values('employee_id', 'date', 'status', 'is_half_day').order_by('employee_id', 'date')
    cur = {}
    for r in rows:   # consecutive absent days; weekends and holidays in between do not break a spell, a missing or other day does
        e, st, dt = r['employee_id'], (r['status'] or ''), r['date']
        sp = cur.get(e)
        if sp is not None and (dt - sp[3]).days > 1:
            out.setdefault(e, []).append((sp[0], sp[1], sp[2], 'Absent'))
            cur.pop(e)
            sp = None
        if st == 'Absent':
            d = 0.5 if r['is_half_day'] else 1.0
            if sp is None:
                cur[e] = [dt, dt, d, dt]
            else:
                sp[1], sp[2], sp[3] = dt, sp[2] + d, dt
        elif st in ('Weekend', 'Holiday'):
            if sp is not None:
                sp[3] = dt
        else:
            sp = cur.pop(e, None)
            if sp:
                out.setdefault(e, []).append((sp[0], sp[1], sp[2], 'Absent'))
    for e, sp in cur.items():
        out.setdefault(e, []).append((sp[0], sp[1], sp[2], 'Absent'))
    return out


def r_bradford(run):
    if not (run.date_from and run.date_to):
        run.date_from, run.date_to = run.today - timedelta(days=364), run.today
    from .reports import EMP_COLS, emp_cols, src, drill, full_name
    sp = spells(run)
    rows = []
    for e in run.employees:
        lst = sp.get(e.id, [])
        s = len(lst)
        d = sum(x[2] for x in lst)
        score = round(s * s * d, 1)
        rows.append({**emp_cols(e), 'spells': s, 'days': d, 'sick_spells': sum(1 for x in lst if x[3] == 'Sick leave'),
                     'absent_spells': sum(1 for x in lst if x[3] == 'Absent'), 'score': score, 'band': band(score),
                     'last': max((x[1] for x in lst), default=None), **src(e)})
        per, who = {'from': run.date_from, 'to': run.date_to}, {'employee': full_name(e)}
        rows[-1]['_cell'] = {'absent_spells': drill('attendance', dict(who, status='Absent'), **per), 'sick_spells': drill('leave', who, **per)}
    rows.sort(key=lambda r: -r['score'])
    return EMP_COLS + [('spells', 'Spells (S)', 'number'), ('days', 'Days (D)', 'number'), ('score', 'Bradford factor (S² × D)', 'number'),
                       ('band', 'Band', 'text'), ('sick_spells', 'Sick-leave spells', 'number'), ('absent_spells', 'Absence spells', 'number'),
                       ('last', 'Last absence', 'date')], rows


def register(REPORTS, REPORT_MODELS):
    REPORTS.update({
        'bradford': ('Bradford factor', 'Leave & attendance',
                     'Short, frequent absences weigh more: S² × D per employee (S = spells of sick leave or unplanned absence, D = their days), '
                     'last 52 weeks by default. Bands: 0–50 no concern, 51–124 keep an eye, 125–399 talk to the employee, 400+ formal review.',
                     r_bradford, None, ['view_attendancereport', 'view_employee_leave_request']),
    })
    REPORT_MODELS.update({'bradford': set()})
