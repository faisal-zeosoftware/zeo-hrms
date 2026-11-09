# v1.67.9 (screens) item 19: dates shown dd/mm/yyyy (date-times dd/mm/yyyy HH:MM)
"""v1.67.0 (item 21, roster part) – the month roster grid: employees × days of a month for a branch (and department).

It uses the EXISTING roster model (ShiftPlanner.RosterPeriod / RosterEntry, v1.12.0): one roster period per branch / department /
month (made as a draft on the first change). Only a published roster counts – the 1.62.6 order roster > shift override > shift
pattern > weekend calendar (calendars/workdays.py) is unchanged, and My schedule shows the published days as before.

Each cell is a shift, a day off or empty; approved full-day leave is shown read-only (it cannot be planned over – change the leave
first); pending leave and public holidays are shown as a mark. Fill by drag (the screen sends the painted cells), by applying a shift
pattern (calendars.ShiftPattern) to chosen employees, or by copying last month (same day of the month, or the same weekday 4 weeks
earlier). Publish: draft → submitted → approved → published as before; with the setting "Publish without the approval step" a user
who may publish publishes a draft at once. Conflicts: two shifts on one day (another live roster – blocks publishing, as before),
rest hours / weekly hours / leave / availability (the 1.12.0 checks), and the rest-day rule (new, warnings only): a shift on the
employee's weekend-calendar rest day, and a week (Mon–Sun) of the month with fewer rest days than the setting (default 1 – UAE
Decree-Law 33/2021 Art. 21: at least one paid rest day a week; checked on MOHRE Workers' rights and Khaleej Times legal column).
Excel export / import of the same grid (rows = employees by code, columns = dates; cells = shift code / name, OFF, empty = not
planned; LEAVE cells are ignored).
Settings: ZeoApps CompanyOption 'roster_month_rules' (no new column).
"""
import calendar as _cal
import io
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.apps import apps
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.response import Response

from . import services as S
from .models import RosterEntry, RosterPeriod, ShiftRule
from .resolver import describe
from .services import PlanError, person
from Translations.i18n import tr

KEY = 'roster_month_rules'
DEFAULTS = {'direct_publish': False, 'min_rest_days_week': 1, 'flag_weekend_rest_day': True}
REST_LAW = {'AE': {'min_rest_days_week': 1,
                   'law': 'UAE Decree-Law 33/2021 Art. 21: at least one paid weekly rest day.',
                   'sources': ['MOHRE – Workers\' rights: "a paid weekly rest for not less than one day"',
                               'Khaleej Times legal column "Can employees be forced to work on their day off?" (Art. 21)']}}
DAY_ABBR = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


# ------------------------------------------------------------------ settings
def settings():
    out = dict(DEFAULTS)
    try:
        o = apps.get_model('ZeoApps', 'CompanyOption').objects.filter(key=KEY).first()
        out.update((o.value or {}) if o else {})
    except Exception:
        pass
    try:
        from AttendancePlus.v149 import country
        law = REST_LAW.get(country())
    except Exception:
        law = None
    out['law'] = law['law'] if law else ''
    out['sources'] = list(law['sources']) if law else []
    return out


def save_settings(data):
    cur = {k: v for k, v in settings().items() if k in DEFAULTS}
    if 'direct_publish' in data:
        cur['direct_publish'] = str(data['direct_publish']).lower() in ('1', 'true', 'yes', 'on')
    if 'flag_weekend_rest_day' in data:
        cur['flag_weekend_rest_day'] = str(data['flag_weekend_rest_day']).lower() in ('1', 'true', 'yes', 'on')
    if 'min_rest_days_week' in data:
        try:
            n = int(data['min_rest_days_week'])
            if n < 0 or n > 3:
                raise ValueError
        except (TypeError, ValueError):
            raise PlanError(tr('Enter a number between {0} and {1}.', 0, 3), problems={'min_rest_days_week': 'Not valid.'})
        cur['min_rest_days_week'] = n
    apps.get_model('ZeoApps', 'CompanyOption').objects.update_or_create(key=KEY, defaults={'value': cur})
    return settings()


# ------------------------------------------------------------------ month, scope, period
def month_of(v):
    s = str(v or '').strip()[:7]
    try:
        y, m = int(s[:4]), int(s[5:7])
        a = date(y, m, 1)
    except (TypeError, ValueError):
        raise PlanError(tr('Choose the month (YYYY-MM).'), problems={'month': 'Required.'})
    return a, date(y, m, _cal.monthrange(y, m)[1])


def scope_of(r, data, write=False):
    try:
        branch = int(data.get('branch'))
    except (TypeError, ValueError):
        raise PlanError(tr('Choose the branch.'), problems={'branch': 'Required.'})
    dv = data.get('department')
    dept = int(dv) if str(dv or '').isdigit() else None
    if not r.branch_ok(branch):
        raise PlanError(tr('You can only plan rosters for your own branches.'), status=403)
    if write and not r.manage:
        raise PlanError(tr('You may not change rosters.'), status=403)
    if not write and not (r.manage or r.has('approve_rosterperiod', 'publish_rosterperiod')):
        raise PlanError(tr('You may not see rosters of this branch.'), status=403)
    return branch, dept


def find_period(branch, dept, a, b):
    return RosterPeriod.objects.filter(branch_id=branch, department_id=dept, date_from=a, date_to=b).order_by('-id').first()


def _names(branch, dept):
    M = apps.get_model
    bn = M('OrganisationManager', 'brnch_mstr').objects.filter(pk=branch).values_list('branch_name', flat=True).first() or ''
    dn = M('OrganisationManager', 'dept_master').objects.filter(pk=dept).values_list('dept_name', flat=True).first() if dept else ''
    return bn, dn or ''


def ensure_period(branch, dept, a, b, user):
    p = find_period(branch, dept, a, b)
    if p is not None:
        return p, False
    bn, dn = _names(branch, dept)
    name = ' – '.join(x for x in (bn, dn, a.strftime('%B %Y')) if x)[:120]
    return RosterPeriod.objects.create(name=name, branch_id=branch, department_id=dept, date_from=a, date_to=b, status='draft',
                                       created_by_id=getattr(user, 'pk', None), notes='Month roster grid (v1.67.0)'), True


def employees(branch, dept):
    qs = S.E().objects.filter(emp_branch_id_id=branch).exclude(is_active=False)
    if dept:
        qs = qs.filter(emp_dept_id_id=dept)
    return list(qs.select_related('emp_dept_id', 'emp_desgntn_id').order_by('emp_dept_id__dept_name', 'emp_first_name', 'emp_code'))


def approved_leave(emp_ids, a, b):
    """{(emp, day): {...}} approved FULL-day leave (read-only cells) – short leave in hours and half days are only marks."""
    out = {}
    for k, v in S.leave_map(emp_ids, a, b).items():
        if v['status'] == 'approved' and not v['half']:
            out[k] = v
    try:   # short leave in hours (v1.62.4) is not a day of leave
        from calendars.models import employee_leave_request as LR
        for r in LR.objects.filter(employee_id__in=list(emp_ids), status='approved', start_date__lte=b, end_date__gte=a,
                                   leave_type__unit='hours'):
            d = max(r.start_date, a)
            while d <= min(r.end_date, b):
                out.pop((r.employee_id, d), None)
                d += timedelta(days=1)
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ conflicts
def rest_conflicts(emps, entries, a, b, cfg=None):
    """The rest-day rule (warnings / info only – they never block publishing)."""
    from calendars.workdays import WeekendRules, model_days
    cfg = cfg or settings()
    out = []
    shifts = S.shifts_by_id()
    rules = WeekendRules()
    need = int(cfg.get('min_rest_days_week') or 0)
    for e in emps:
        work = {d for (eid, d), x in entries.items() if eid == e.pk and not x.off and x.shift_id and x.shift_id in shifts}
        if cfg.get('flag_weekend_rest_day'):
            for d in sorted(work):
                m, _, _ = rules.pick(e, d)
                if d.strftime('%A') in model_days(m, d):
                    out.append({'employee_id': e.pk, 'employee': person(e), 'date': d, 'kind': 'rest_day', 'level': 'info',
                                'message': tr('Rest day by the weekend calendar ({0}) – the rostered shift makes it a working day.', d.strftime('%A'))})
        if need:
            wk = a - timedelta(days=a.weekday())
            if wk < a:
                wk += timedelta(days=7)
            while wk + timedelta(days=6) <= b:
                days = [wk + timedelta(days=i) for i in range(7)]
                rest = sum(1 for d in days if d not in work)
                if rest < need:
                    out.append({'employee_id': e.pk, 'employee': person(e), 'date': wk, 'kind': 'no_rest_week', 'level': 'warning',
                                'message': tr('Only {0} rest day(s) in the week of {1} (at least {2}).', rest, wk.strftime('%d/%m'), need)})
                wk += timedelta(days=7)
    return out


def all_conflicts(p, emps, entries, a, b, cfg=None):
    out = []
    if p is not None:
        for x in S.check_period(p, {e.pk for e in emps}):
            out.append(x)
    out += rest_conflicts(emps, entries, a, b, cfg)
    return out


# ------------------------------------------------------------------ grid
def shift_codes():
    """{shift id: short code} – the shift's own code (Shift master), else the initials of its name; never OFF / LEAVE and never twice
    (the code is what the Excel file carries)."""
    shifts = S.shifts_by_id()
    rules = {x.shift_id: x for x in ShiftRule.objects.all()}
    out, used = {}, {'off', 'leave', 'o', 'rest'}
    for s in sorted(shifts.values(), key=lambda x: x.pk):
        r = rules.get(s.pk)
        c = (r.code or '').strip() if r is not None else ''
        if not c:
            c = ''.join(w[0] for w in s.name.replace('–', ' ').replace('(', ' ').split() if w[:1].isalnum()).upper()[:4] or f'S{s.pk}'
        if c.lower() in used:
            c = f'{c}{s.pk}'
        used.add(c.lower())
        out[s.pk] = c
    return out


def shift_list():
    shifts = S.shifts_by_id()
    rules = {x.shift_id: x for x in ShiftRule.objects.all()}
    codes = shift_codes()
    out = []
    for s in sorted(shifts.values(), key=lambda x: x.name):
        r = rules.get(s.pk)
        if r is not None and not r.active:
            continue
        out.append({'id': s.pk, 'name': s.name, 'code': codes[s.pk],
                    'colour': (r.colour if r and r.colour else '#5b4ff5'),
                    'start': s.start_time.strftime('%H:%M') if s.start_time else None, 'end': s.end_time.strftime('%H:%M') if s.end_time else None,
                    'night': bool(s.start_time and s.end_time and s.end_time <= s.start_time)})
    return out


def grid(r, data):
    a, b = month_of(data.get('month'))
    branch, dept = scope_of(r, data)
    p = find_period(branch, dept, a, b)
    emps = employees(branch, dept)
    ids = [e.pk for e in emps]
    cfg = settings()
    shifts = {s['id']: s for s in shift_list()}
    all_shifts = S.shifts_by_id()
    entries = {(x.employee_id, x.date): x for x in RosterEntry.objects.filter(period=p)} if p else {}
    others = defaultdict(list)
    for o in RosterEntry.objects.filter(employee_id__in=ids, date__gte=a, date__lte=b, period__status__in=S.LIVE, off=False,
                                        shift_id__isnull=False).exclude(period=p).select_related('period'):
        others[(o.employee_id, o.date)].append(o)
    leaves = S.leave_map(ids, a, b)
    locked = approved_leave(ids, a, b)
    from calendars.workdays import WeekendRules, model_days
    from .resolver import holidays
    wr = WeekendRules()
    conflicts = all_conflicts(p, emps, entries, a, b, cfg)
    probs = defaultdict(list)
    for x in conflicts:
        probs[(x['employee_id'], x['date'])].append({'kind': x['kind'], 'level': x['level'], 'message': x['message']})
    days = []
    d = a
    while d <= b:
        days.append({'date': d.isoformat(), 'day': d.day, 'weekday': DAY_ABBR[d.weekday()]})
        d += timedelta(days=1)
    rows = []
    hol_cache = {}
    for e in emps:
        hk = (e.emp_branch_id_id, e.emp_dept_id_id, e.emp_ctgry_id_id)
        if hk not in hol_cache:
            hol_cache[hk] = holidays(e, a, b)
        cells, hours, worked = {}, 0.0, 0
        d = a
        while d <= b:
            x = entries.get((e.pk, d))
            sh = all_shifts.get(x.shift_id) if x is not None and x.shift_id and not x.off else None
            info = describe(sh, None, d, 'roster') if sh is not None and sh.start_time and sh.end_time else None
            if info:
                hours += float(info['hours'])
                worked += 1
            m, _, _ = wr.pick(e, d)
            lv = leaves.get((e.pk, d))
            s = shifts.get(sh.pk) if sh else None
            cells[d.isoformat()] = {
                'shift_id': sh.pk if sh else None, 'off': bool(x and x.off), 'code': (s['code'] if s else (sh.name[:3].upper() if sh else '')),
                'colour': s['colour'] if s else ('#5b4ff5' if sh else ''), 'start': s['start'] if s else None, 'end': s['end'] if s else None,
                'source': x.source if x else '', 'note': x.note if x else '', 'changed': bool(x and x.changed_after_publish),
                'leave': lv, 'locked': (e.pk, d) in locked, 'holiday': d in hol_cache[hk],
                'rest_day': d.strftime('%A') in model_days(m, d),
                'other': [{'period': o.period.name, 'status': o.period.status, 'code': (shifts.get(o.shift_id) or {}).get('code', '')} for o in others.get((e.pk, d), [])],
                'problems': probs.get((e.pk, d), []),
            }
            d += timedelta(days=1)
        week_notes = [y for (eid, dd), lst in probs.items() if eid == e.pk for y in lst if y['kind'] in ('no_rest_week', 'week_hours')]
        rows.append({'employee_id': e.pk, 'code': e.emp_code, 'name': person(e),
                     'department': getattr(e.emp_dept_id, 'dept_name', '') if e.emp_dept_id_id else '',
                     'designation': getattr(e.emp_desgntn_id, 'desgntn_job_title', '') if e.emp_desgntn_id_id else '',
                     'hours': round(hours, 2), 'shift_days': worked, 'cells': cells, 'notes': week_notes})
    from .views import period_row
    can_manage = r.manage and r.branch_ok(branch)
    pr = period_row(p, r=r) if p else None
    can = {'edit': can_manage and (p is None or p.status in S.EDITABLE),
           'publish_now': bool(cfg['direct_publish']) and r.can_publish(p or RosterPeriod(branch_id=branch)) and (p is None or p.status in ('draft', 'rejected', 'approved')),
           'submit': bool(pr and pr['can']['submit']), 'approve': bool(pr and pr['can']['approve']), 'publish': bool(pr and pr['can']['publish']),
           'reopen': bool(pr and pr['can']['reopen'])}
    from calendars.models import ShiftPattern
    return {'month': a.strftime('%Y-%m'), 'from': a, 'to': b, 'branch': branch, 'department': dept, 'period': pr, 'can': can,
            'settings': cfg, 'days': days, 'shifts': list(shifts.values()),
            'patterns': [{'id': x.pk, 'name': x.name, 'type': x.pattern_type} for x in ShiftPattern.objects.order_by('name')],
            'rows': rows, 'problems': [dict(x, date=x['date'].isoformat() if hasattr(x['date'], 'isoformat') else x['date']) for x in conflicts],
            'counts': {'errors': sum(1 for x in conflicts if x['level'] == 'error'), 'warnings': sum(1 for x in conflicts if x['level'] == 'warning'),
                       'info': sum(1 for x in conflicts if x['level'] == 'info'), 'planned': len(entries)}}


# ------------------------------------------------------------------ writes
def _pd(v):
    from .views import pdate
    return pdate(v, 'date')


def _write(r, data, cells, source, user, note_of=None):
    """[(emp, day, shift_id | None, off)] → upsert (approved full-day leave cells refused). Returns (period, saved, skipped)."""
    a, b = month_of(data.get('month'))
    branch, dept = scope_of(r, data, write=True)
    scope = {e.pk: e for e in employees(branch, dept)}
    shifts = S.shifts_by_id()
    locked = approved_leave(list(scope), a, b)
    bad, rows = [], []
    for i, (eid, d, sid, off) in enumerate(cells):
        emp = scope.get(eid)
        if emp is None:
            bad.append({'row': i + 1, 'employee': eid, 'date': d, 'message': tr('This employee is not part of the roster (branch / department).')})
            continue
        if d < a or d > b:
            bad.append({'row': i + 1, 'employee': eid, 'date': d, 'message': tr('{0:%d/%m/%Y} is outside this roster ({1:%d/%m/%Y} – {2:%d/%m/%Y}).', d, a, b)})
            continue
        if sid is not None and sid not in shifts:
            bad.append({'row': i + 1, 'employee': eid, 'date': d, 'message': tr('Choose a shift from the list.')})
            continue
        rows.append((emp, d, sid, off))
    if bad:
        raise PlanError(tr('Nothing was saved. Fix the cells listed.'), problems=bad)
    saved, skipped = 0, []
    with transaction.atomic():
        p, created = ensure_period(branch, dept, a, b, user)
        S.check_editable(p)
        for emp, d, sid, off in rows:
            if (emp.pk, d) in locked:
                skipped.append({'employee_id': emp.pk, 'employee': person(emp), 'date': d.isoformat(),
                                'message': tr('{0} leave (approved) – change the leave first.', locked[(emp.pk, d)]['type'])})
                continue
            S.upsert_entry(p, emp, d, sid, off, source, user, note=note_of(emp, d) if note_of else None)
            saved += 1
    return p, saved, skipped


def save_cells(r, data, user):
    from .views import pint, pbool
    cells = data.get('cells')
    if not isinstance(cells, list) or not cells:
        raise PlanError(tr('Send the cells to change.'))
    out = []
    for c in cells:
        sid = pint(c.get('shift'), 'shift')
        out.append((pint(c.get('employee'), 'employee', True), _pd(c.get('date')), sid, pbool(c.get('off')) and sid is None))
    p, saved, skipped = _write(r, data, out, 'manual', user)
    return {'saved': saved, 'skipped': skipped, 'period_id': p.pk}


def apply_pattern(r, data, user):
    """Shift pattern → the chosen employees' days (from / to inside the month; the rotation starts on `anchor`, default `from`).
    A pattern day without a shift = day off."""
    from calendars.models import ShiftPattern
    from .views import pint
    a, b = month_of(data.get('month'))
    pat = ShiftPattern.objects.filter(pk=pint(data.get('pattern'), 'pattern', True)).first()
    if pat is None:
        raise PlanError(tr('Choose a shift pattern.'), problems={'pattern': 'Required.'})
    emp_ids = [int(x) for x in (data.get('employees') or []) if str(x).isdigit()]
    if not emp_ids:
        raise PlanError(tr('Choose the employees.'), problems={'employees': 'Required.'})
    f = _pd(data.get('from')) if data.get('from') else a
    t = _pd(data.get('to')) if data.get('to') else b
    anchor = _pd(data.get('anchor')) if data.get('anchor') else f
    f, t = max(f, a), min(t, b)
    if t < f:
        raise PlanError(tr('The end date must be on or after the start date.'), problems={'to': 'Not valid.'})
    cells = []
    d = f
    while d <= t:
        sh = pat.get_shift_for_date(d, anchor)
        for eid in emp_ids:
            cells.append((eid, d, sh.pk if sh is not None else None, sh is None))
        d += timedelta(days=1)
    p, saved, skipped = _write(r, data, cells, 'manual', user, note_of=lambda e, d: f'Pattern: {pat.name}'[:255])
    return {'saved': saved, 'skipped': skipped, 'period_id': p.pk, 'pattern': pat.name}


def copy_last_month(r, data, user):
    """Copy the previous month's planned days (this grid's previous month roster, else any live roster of those employees) onto
    this month: align 'date' = the same day of the month (days the last month does not have stay empty), 'weekday' = the same weekday
    four weeks earlier. Days already planned are kept unless overwrite."""
    from .views import pbool
    a, b = month_of(data.get('month'))
    branch, dept = scope_of(r, data, write=True)
    align = data.get('align') or 'date'
    if align not in ('date', 'weekday'):
        raise PlanError(tr('Choose how to copy: same date or same weekday.'))
    pa = (a - timedelta(days=1)).replace(day=1)
    pb = a - timedelta(days=1)
    emps = employees(branch, dept)
    ids = [e.pk for e in emps]
    src_p = find_period(branch, dept, pa, pb)
    src = {}
    pri = {'published': 1, 'approved': 2, 'submitted': 3, 'draft': 4, 'rejected': 5}
    for x in RosterEntry.objects.filter(employee_id__in=ids, date__gte=pa - timedelta(days=7), date__lte=pb,
                                        period__status__in=S.LIVE).select_related('period'):
        k = (x.employee_id, x.date)
        rank = 0 if (src_p is not None and x.period_id == src_p.pk) else pri.get(x.period.status, 9)
        if k not in src or rank < src[k][0]:
            src[k] = (rank, x)
    src = {k: v[1] for k, v in src.items()}
    p = find_period(branch, dept, a, b)
    have = {(x.employee_id, x.date) for x in RosterEntry.objects.filter(period=p)} if p else set()
    overwrite = pbool(data.get('overwrite'))
    cells, empty = [], 0
    d = a
    while d <= b:
        if align == 'date':
            s = pa.replace(day=d.day) if d.day <= pb.day else None
        else:
            s = d - timedelta(days=28)
            if s >= a:   # day 29–31: four weeks back is still this month – five weeks back
                s -= timedelta(days=7)
        for e in emps:
            if (e.pk, d) in have and not overwrite:
                continue
            x = src.get((e.pk, s)) if s else None
            if x is None:
                empty += 1
                continue
            cells.append((e.pk, d, None if x.off else x.shift_id, bool(x.off)))
        d += timedelta(days=1)
    if not cells:
        raise PlanError(tr('Nothing to copy: last month has no planned days for these employees.'), status=409)
    p, saved, skipped = _write(r, data, cells, 'copy', user)
    return {'copied': saved, 'skipped': skipped, 'not_in_last_month': empty, 'period_id': p.pk, 'from_period': src_p.name if src_p else ''}


def publish_now(r, data, user):
    """With the setting "Publish without the approval step": a draft / sent-back / approved month roster is published at once."""
    a, b = month_of(data.get('month'))
    branch, dept = scope_of(r, data, write=True)
    p = find_period(branch, dept, a, b)
    if p is None or not p.entries.exists():
        raise PlanError(tr('The roster is empty. Generate it or plan some shifts first.'))
    if not settings()['direct_publish']:
        raise PlanError(tr('Publishing needs the approval step: send the roster for approval first.'), status=409)
    if not r.can_publish(p):
        raise PlanError(tr('You may not publish this roster.'), status=403)
    if p.status not in ('draft', 'rejected', 'approved'):
        raise PlanError(tr('This roster is {0} already.', p.get_status_display().lower()), status=409)
    from .views import PeriodAction
    now = timezone.now()
    probs = S.check_period(p)
    errs = [x for x in probs if x['level'] == 'error']
    if errs:
        raise PlanError(tr('Fix {0} problem(s) before publishing.', len(errs)), problems=errs)
    with transaction.atomic():
        p.status, p.approved_by_id, p.approved_at = 'approved', user.pk, now
        p.save()
        out = PeriodAction()._publish(p, user, now)
    return out


# ------------------------------------------------------------------ Excel
def export_xlsx(r, data):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    g = grid(r, data)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Roster ' + g['month']
    head = ['employee_code', 'employee', 'department'] + [x['date'] for x in g['days']]
    ws.append(head)
    ws.append(['', '', ''] + [f"{x['weekday']} {x['day']}" for x in g['days']])
    for c in ws[1] + ws[2]:
        c.font = Font(bold=True)
    for row in g['rows']:
        vals = [row['code'], row['name'], row['department']]
        for x in g['days']:
            c = row['cells'][x['date']]
            vals.append('LEAVE' if c['locked'] and not c['shift_id'] else (c['code'] if c['shift_id'] else ('OFF' if c['off'] else '')))
        ws.append(vals)
        rr = ws.max_row
        for j, x in enumerate(g['days']):
            c = row['cells'][x['date']]
            cell = ws.cell(row=rr, column=4 + j)
            cell.alignment = Alignment(horizontal='center')
            if c['shift_id'] and c['colour']:
                cell.fill = PatternFill('solid', fgColor=c['colour'].lstrip('#')[:6].rjust(6, '0'))
                cell.font = Font(color='FFFFFF', bold=True)
            elif c['locked']:
                cell.fill = PatternFill('solid', fgColor='DCEBFF')
    ws.column_dimensions['B'].width = 28
    ws.freeze_panes = 'D3'
    ws2 = wb.create_sheet('Shifts')
    ws2.append(['code', 'shift', 'from', 'to'])
    for s in g['shifts']:
        ws2.append([s['code'], s['name'], s['start'] or '', s['end'] or ''])
    ws2.append(['OFF', 'Day off', '', ''])
    ws2.append(['(empty)', 'Not planned', '', ''])
    ws2.append(['LEAVE', 'Approved leave (read-only, ignored on import)', '', ''])
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="roster_{g["month"]}.xlsx"'
    return resp


def _head_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or '').strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def import_xlsx(r, data, f, user):
    """Preview (commit 0) or save (commit 1, only when no row has an error) the grid file."""
    from .views import pbool, shift_lookup
    a, b = month_of(data.get('month'))
    branch, dept = scope_of(r, data, write=True)
    name = (getattr(f, 'name', '') or '').lower()
    if not (name.endswith('.xlsx') or name.endswith('.xlsm')):
        raise PlanError(tr('Upload the .xlsx file exported from this screen.'))
    from openpyxl import load_workbook
    ws = load_workbook(f, read_only=True, data_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    head = list(next(it, []) or [])
    cols = {}
    for j, h in enumerate(head):
        d = _head_date(h)
        if d is not None:
            cols[j] = d
    try:
        jc = [str(h or '').strip().lower() for h in head].index('employee_code')
    except ValueError:
        raise PlanError(tr('The file needs the column employee_code and one column per date. Export the grid first.'))
    if not cols:
        raise PlanError(tr('The file needs the column employee_code and one column per date. Export the grid first.'))
    scope = {e.emp_code.strip().lower(): e for e in employees(branch, dept)}
    look = shift_lookup()
    look.update({c.lower(): i for i, c in shift_codes().items()})
    p = find_period(branch, dept, a, b)
    cur = {(x.employee_id, x.date): x for x in RosterEntry.objects.filter(period=p)} if p else {}
    locked = approved_leave([e.pk for e in scope.values()], a, b)
    rows, cells, n = [], [], {'set': 0, 'cleared': 0, 'same': 0, 'ignored': 0}
    for i, vals in enumerate(it, start=2):
        vals = list(vals or [])
        code = str(vals[jc] if jc < len(vals) and vals[jc] is not None else '').strip()
        if not code:
            continue   # the weekday line under the header / empty rows
        errs = []
        emp = scope.get(code.lower())
        if emp is None:
            errs.append(tr('Employee code {0} is not in this branch / department.', code))
        for j, d in cols.items():
            v = str(vals[j] if j < len(vals) and vals[j] is not None else '').strip()
            if d < a or d > b:
                if v:
                    errs.append(tr('{0:%d/%m/%Y} is outside this roster ({1:%d/%m/%Y} – {2:%d/%m/%Y}).', d, a, b))
                continue
            if emp is None:
                continue
            if v.upper() == 'LEAVE' or (emp.pk, d) in locked:
                n['ignored'] += 1
                continue
            off = v.lower() in ('off', 'o', 'rest', 'day off')
            sid = None if (off or not v) else look.get(v.lower())
            if v and not off and sid is None:
                errs.append(tr('Shift “{0}” not found. Use a shift code, a shift name or OFF.', v) + f' ({d:%d/%m})')
                continue
            x = cur.get((emp.pk, d))
            now_v = (None, False) if x is None else ((None, True) if x.off else (x.shift_id, False))
            new_v = (sid, off)
            if now_v == new_v or (x is None and not v):
                n['same'] += 1
                continue
            n['cleared' if not v else 'set'] += 1
            cells.append((emp.pk, d, sid, off))
        rows.append({'row': i, 'employee_code': code, 'employee': person(emp) if emp else '', 'errors': errs, 'ok': not errs})
    bad = [x for x in rows if not x['ok']]
    out = {'rows': rows, 'valid': len(rows) - len(bad), 'errors': len(bad), 'changes': n}
    if not pbool(data.get('commit')):
        return out
    if bad:
        raise PlanError(tr('{0} row(s) have errors. Nothing was saved.', len(bad)), problems=bad)
    if cells:
        pp, saved, skipped = _write(r, data, cells, 'upload', user)
        out.update(saved=saved, skipped=skipped, period_id=pp.pk)
    else:
        out.update(saved=0, skipped=[])
    return out
