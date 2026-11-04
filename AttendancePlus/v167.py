"""v1.67.0 – attendance items 23 and 25 (settings kept in ZeoApps CompanyOption JSON – no new column, no migration).

Item 23 – rest-day chips (Mon … Sun) on the employee category and on the attendance rule. They write the SAME weekend records the
1.62.6 rule reads (calendars.assign_weekend at the level of the target + a calendars.weekend_calendar with those days off), so
calendars/workdays.py (employee > designation > category > department > branch) answers exactly as if the Weekend calendar screens
had been used. Half days, alternate weeks, a different weekend from a date and single employees stay on the Weekend calendar /
Weekend change screens.

Item 25 –
  (a) Ramadan hours: periods per year (dates editable – they follow the moon sighting and the yearly MOHRE announcement), the daily
      reduction (UAE private sector: 2 hours, verified – sources in RAMADAN_LAW) or Ramadan times per shift. Attendance late / early /
      minimum hours / overtime use the Ramadan hours on those days.
  (b) the hours after a night shift that still belong to it (engine.work_date_for: 6 h after the shift end, unless the next own shift
      starts within 2 h) – a setting of each attendance rule;
  (c) back-to-back shifts (e.g. 24-hour 08:00–08:00 shifts on following days): the out punch closes the open shift first;
  (d) hours of a shift that runs from a rest day / public holiday into a working day (or back): counted by the shift start date
      (today) or by the calendar date the hours fall on.
Every rule defaults to today's behaviour.
"""
from datetime import date, datetime, time, timedelta

from django.apps import apps

DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
ABBR = {d: d[:3] for d in DAY_NAMES}
RULE_KEY = 'attendance_rule_extras'      # {str(rule id): {...}}
RAMADAN_KEY = 'attendance_ramadan'
RULE_DEFAULTS = {'after_night_hours': 6, 'before_next_hours': 2, 'back_to_back': False, 'rest_day_hours_by': 'shift_start'}


def _CO():
    return apps.get_model('ZeoApps', 'CompanyOption')


def _get(key):
    try:
        o = _CO().objects.filter(key=key).first()
        return dict(o.value or {}) if o is not None else {}
    except Exception:
        return {}


def _put(key, value):
    _CO().objects.update_or_create(key=key, defaults={'value': value})


def _bool(v):
    return v.lower() in ('1', 'true', 'yes', 'on') if isinstance(v, str) else bool(v)


def _memo(key, factory):
    try:
        from PayrollManagement.perf import memo
    except Exception:
        return factory()
    return memo(key, factory)


# ====================================================================== 25 (b)(c)(d) rule settings
def all_rule_extras():
    return _get(RULE_KEY)


def rule_extras(rule):
    """v1.67.0 (item 25) the rule's settings b, c, d with today's behaviour as default (rule without id = the default rule)."""
    out = dict(RULE_DEFAULTS)
    rid = getattr(rule, 'id', None)
    if rid:
        out.update(_memo(('attendance_rule_extras',), all_rule_extras).get(str(rid)) or {})
    return out


def clean_rule_extras(data, errors=None):
    """Values of the rule form → stored values; errors {field: message}."""
    from Translations.i18n import tr
    errors = errors if errors is not None else {}
    v = {}
    for k, lo, hi in (('after_night_hours', 0, 12), ('before_next_hours', 0, 12)):
        if k in data and data[k] not in (None, ''):
            try:
                x = float(data[k])
                if x < lo or x > hi:
                    raise ValueError
                v[k] = int(x) if float(x).is_integer() else x
            except (TypeError, ValueError):
                errors[k] = tr('Enter a number between {0} and {1}.', lo, hi)
    if 'back_to_back' in data and data['back_to_back'] is not None:
        v['back_to_back'] = _bool(data['back_to_back'])
    if 'rest_day_hours_by' in data and data['rest_day_hours_by']:
        if data['rest_day_hours_by'] not in ('shift_start', 'calendar'):
            errors['rest_day_hours_by'] = tr('Choose the shift start date or the calendar date.')
        else:
            v['rest_day_hours_by'] = data['rest_day_hours_by']
    return v


def save_rule_extras(rule_id, values):
    cur = _get(RULE_KEY)
    row = dict(RULE_DEFAULTS)
    row.update(cur.get(str(rule_id)) or {})
    row.update(values)
    if row == RULE_DEFAULTS:
        cur.pop(str(rule_id), None)
    else:
        cur[str(rule_id)] = row
    _put(RULE_KEY, cur)
    return row


# ====================================================================== 25 (a) Ramadan
# Checked on 3 Nov 2026 – every source says the same: normal working hours of the private sector are reduced by two hours a day
# during Ramadan.
#   1. Ashish Mehta (Ashish Mehta & Associates, legal consultants), Khaleej Times "Ramadan: Are employees eligible for overtime while
#      working remotely in UAE?" (khaleejtimes.com/uae/legal/ramadan-employees-eligible-overtime-working-remotely): Federal Decree-Law
#      No. 33 of 2021 Art. 17(4) read with Cabinet Resolution No. 1 of 2022 Art. 15(2): "The regular working hours shall be reduced
#      by two hours during the holy month of Ramadan"; hours above that are overtime (Art. 19).
#   2. MOHRE – Workers' rights, Working hours (mohre.gov.ae/en/media-center/awareness-and-guidance/workers-rights.aspx): "During the
#      month of Ramadan, the normal working time is reduced by two hours."
#   3. MOHRE announcement of 12 Feb 2026 "Reduction of working hours for private sector employees by two hours daily during the holy
#      month of Ramadan" (in implementation of the Implementing Regulations of Decree-Law 33/2021; flexible / remote work allowed
#      within the reduced hours) – MOHRE announces it every year (2022, 2023, 2024, 2026 pages found).
#   4. u.ae – Working hours (government.ae/en/information-and-services/jobs/working-hours): "During the holy month of Ramadan,
#      working hours are reduced by 2."
# The DATES of Ramadan are not a fixed rule: they follow the moon sighting and are announced each year – HR enters them (to be
# confirmed every year). Other countries: not checked – no default reduction (to be confirmed).
RAMADAN_LAW = {
    'AE': {'reduce_hours': 2,
           'law': 'UAE Decree-Law 33/2021 Art. 17(4) and Cabinet Resolution 1/2022 Art. 15(2): normal working hours of the private '
                  'sector are reduced by two hours a day during Ramadan; hours worked above them are overtime.',
           'sources': ['Ashish Mehta & Associates (Khaleej Times, "Ramadan: Are employees eligible for overtime while working remotely")',
                       'MOHRE – Workers\' rights: Working hours',
                       'MOHRE announcement of 12/02/2026 – two hours less a day during Ramadan (announced every year)',
                       'u.ae – Working hours']},
}


def _country():
    try:
        from .v149 import country
        return country()
    except Exception:
        return 'AE'


def _pd(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _pt(v):
    if isinstance(v, time):
        return v
    try:
        h, m = str(v).strip()[:5].split(':')
        return time(int(h), int(m))
    except (TypeError, ValueError):
        return None


def ramadan_settings():
    law = RAMADAN_LAW.get(_country())
    v = _get(RAMADAN_KEY)
    periods = []
    for p in v.get('periods') or []:
        a, b = _pd(p.get('from')), _pd(p.get('to'))
        if a and b:
            periods.append({'year': int(p.get('year') or a.year), 'from': a.isoformat(), 'to': b.isoformat(), 'note': p.get('note') or ''})
    periods.sort(key=lambda x: x['from'])
    times = {}
    for k, t in (v.get('shift_times') or {}).items():
        if _pt(t.get('start')) and _pt(t.get('end')):
            times[str(k)] = {'start': _pt(t['start']).strftime('%H:%M'), 'end': _pt(t['end']).strftime('%H:%M')}
    return {'enabled': _bool(v.get('enabled', bool(law))),
            'reduce_hours': float(v['reduce_hours']) if v.get('reduce_hours') not in (None, '') else float(law['reduce_hours'] if law else 0),
            'periods': periods, 'shift_times': times,
            'exclude_category_ids': [int(x) for x in (v.get('exclude_category_ids') or []) if str(x).isdigit()],
            'country': _country(), 'law': law['law'] if law else '', 'sources': list(law['sources']) if law else [],
            'law_default_hours': law['reduce_hours'] if law else None, 'verified': bool(law), 'saved': bool(v)}


def save_ramadan(data):
    from Translations.i18n import tr
    errors = {}
    cur = _get(RAMADAN_KEY)
    if 'enabled' in data:
        cur['enabled'] = _bool(data['enabled'])
    if 'reduce_hours' in data:
        try:
            x = float(data['reduce_hours'])
            if x < 0 or x > 6:
                raise ValueError
            cur['reduce_hours'] = x
        except (TypeError, ValueError):
            errors['reduce_hours'] = tr('Enter a number between {0} and {1}.', 0, 6)
    if 'periods' in data:
        rows = []
        for i, p in enumerate(data.get('periods') or []):
            a, b = _pd(p.get('from')), _pd(p.get('to'))
            if not a or not b:
                errors[f'periods.{i}'] = tr('Enter the first and the last day of Ramadan.')
                continue
            if b < a or (b - a).days > 31:
                errors[f'periods.{i}'] = tr('The last day must be on or after the first day (at most 31 days).')
                continue
            rows.append({'year': int(p.get('year') or a.year), 'from': a.isoformat(), 'to': b.isoformat(), 'note': (p.get('note') or '')[:120]})
        rows.sort(key=lambda x: x['from'])
        for x, y in zip(rows, rows[1:]):
            if _pd(y['from']) <= _pd(x['to']):
                errors['periods'] = tr('Two Ramadan periods overlap ({0}).', y['from'])
        years = [x['year'] for x in rows]
        if len(years) != len(set(years)):
            errors['periods'] = tr('One Ramadan period per year.')
        cur['periods'] = rows
    if 'shift_times' in data:
        st = {}
        for k, t in (data.get('shift_times') or {}).items():
            if not t or (not t.get('start') and not t.get('end')):
                continue
            a, b = _pt(t.get('start')), _pt(t.get('end'))
            if not a or not b:
                errors[f'shift_times.{k}'] = tr('Enter the Ramadan start and end time (HH:MM).')
                continue
            st[str(int(k))] = {'start': a.strftime('%H:%M'), 'end': b.strftime('%H:%M')}
        cur['shift_times'] = st
    if 'exclude_category_ids' in data:
        cur['exclude_category_ids'] = [int(x) for x in (data.get('exclude_category_ids') or []) if str(x).isdigit()]
    if errors:
        return None, errors
    _put(RAMADAN_KEY, cur)
    return ramadan_settings(), None


def _ramadan_cached():
    return _memo(('attendance_ramadan',), ramadan_settings)


def ramadan_for(employee, day):
    """{'reduce_minutes': n, 'times': {shift_id: (start, end)}, 'period': {...}} when the day is in a Ramadan period of the company
    and the employee is not in an excluded category; else None."""
    s = _ramadan_cached()
    if not s['enabled'] or not s['periods']:
        return None
    per = next((p for p in s['periods'] if _pd(p['from']) <= day <= _pd(p['to'])), None)
    if per is None:
        return None
    if getattr(employee, 'emp_ctgry_id_id', None) in s['exclude_category_ids']:
        return None
    return {'reduce_minutes': int(round(float(s['reduce_hours'] or 0) * 60)),
            'times': {int(k): (_pt(t['start']), _pt(t['end'])) for k, t in s['shift_times'].items()}, 'period': per}


def apply_ramadan(employee, day, sh, rule, aware, minutes):
    """(shift dict, rule, flag) for a Ramadan day: the shift's Ramadan times (Ramadan › Shift times) or the shift ending
    `reduce_minutes` earlier; the rule's full-day / half-day minimum and daily overtime threshold lowered by the same minutes.
    `rule` is a copy when changed (the rule object is shared by the payroll run)."""
    rm = ramadan_for(employee, day)
    if rm is None:
        return sh, rule, None
    import copy
    cut = rm['reduce_minutes']
    flag = {'from': rm['period']['from'], 'to': rm['period']['to']}
    if sh and not sh.get('off') and sh.get('start') and sh.get('end'):
        sh = dict(sh)
        before = minutes(sh['end'] - sh['start'])
        t = rm['times'].get(sh.get('shift_id')) if sh.get('shift_id') else None
        if t:
            st, en = aware(day, t[0]), aware(day, t[1])
            if en <= st:
                en += timedelta(days=1)
            sh['start'], sh['end'] = st, en
            flag['times'] = f'{t[0]:%H:%M}–{t[1]:%H:%M}'
            cut = max(0, before - minutes(en - st))
        elif cut and before - cut >= 60:
            sh['end'] = sh['end'] - timedelta(minutes=cut)
            if sh.get('segments'):
                segs = list(sh['segments'])
                a, b = segs[-1]
                segs[-1] = (a, max(a, b - timedelta(minutes=cut)))
                sh['segments'] = segs
        else:
            cut = 0
        if cut and sh.get('min_hours'):
            sh['min_hours'] = max(0, float(sh['min_hours']) - cut / 60)
        if cut and sh.get('half_day_hours'):
            sh['half_day_hours'] = max(0, float(sh['half_day_hours']) - cut / 120)
        flag['shift'] = f"{sh['start'].strftime('%H:%M')}–{sh['end'].strftime('%H:%M')}"
    if cut:
        from decimal import Decimal
        rule = copy.copy(rule)
        h = Decimal(cut) / Decimal(60)
        for f, part in (('min_hours_full_day', 1), ('min_hours_half_day', 2), ('ot_daily_threshold_hours', 1)):
            v = Decimal(str(getattr(rule, f, 0) or 0))
            if v > 0:
                setattr(rule, f, max(Decimal(0), v - h / part))
    flag['minutes'] = cut
    return sh, rule, flag


# ====================================================================== 25 (d) rest-day / holiday hours by calendar date
def minutes_by_date(intervals, local, minutes):
    """{date: minutes} of the worked intervals split at local midnight."""
    out = {}
    for a, b in intervals:
        la, lb = local(a), local(b)
        while la < lb:
            nxt = datetime.combine(la.date() + timedelta(days=1), time(0)).replace(tzinfo=la.tzinfo)
            end = min(lb, nxt)
            out[la.date()] = out.get(la.date(), 0) + minutes(end - la)
            la = end
    return out


# ====================================================================== 23 rest-day chips
LEVEL_FIELD = {'branch': 'emp_branch_id', 'department': 'emp_dept_id', 'category': 'emp_ctgry_id', 'designation': 'emp_desgntn_id',
               'employee': 'pk'}


def norm_days(v):
    if v is None:
        return None
    if isinstance(v, str):
        import json
        try:
            v = json.loads(v)
        except ValueError:
            v = [x for x in v.split(',') if x.strip()]
    out = set()
    for x in v or []:
        s = str(x).strip().capitalize()
        full = next((d for d in DAY_NAMES if d == s or d[:3] == s[:3]), None)
        if full is None:
            raise ValueError(str(x))
        out.add(full)
    return sorted(out, key=DAY_NAMES.index)


def _holders(level, tid):
    AW = apps.get_model('calendars', 'assign_weekend')
    return [a for a in AW.objects.prefetch_related(level).order_by('pk')
            if (not a.related_to or a.related_to == level) and tid in {x.pk for x in getattr(a, level).all()}]


def rest_days_of(level, tid, rules=None):
    """The rest days the weekend records give the target at its own level today: {'days', 'calendar', 'shared', 'half_days',
    'alternate', 'set'}."""
    from calendars.workdays import WeekendRules, model_days, _today
    rules = rules or WeekendRules()
    d = _today()
    found = []
    for a, sets in rules.items:
        if a.related_to and a.related_to != level:
            continue
        if tid in sets[level]:
            m = rules.model_on(a, d)
            if m is not None:
                found.append((1 if getattr(m, 'year', None) == d.year else 0, a.pk, a, m, sets[level]))
    if not found:
        return {'days': [], 'calendar': '', 'shared': False, 'half_days': False, 'alternate': False, 'set': False}
    found.sort(key=lambda x: (x[0], x[1]))
    _, _, a, m, ids = found[-1]
    half = any(getattr(m, n.lower(), '') == 'halfday' for n in DAY_NAMES)
    return {'days': sorted(model_days(m, None), key=DAY_NAMES.index), 'calendar': m.calendar_code, 'calendar_id': m.pk,
            'shared': len(ids) > 1, 'half_days': half, 'alternate': bool(m.alternate_weekends), 'set': True}


def _calendar_for(days, user=None):
    from calendars.workdays import _today
    W = apps.get_model('calendars', 'weekend_calendar')
    y = _today().year
    want = set(days)
    best = None
    for w in W.objects.filter(year=y).order_by('pk'):
        vals = {n: getattr(w, n.lower(), 'fullday') for n in DAY_NAMES}
        if 'halfday' in vals.values() or w.alternate_weekends:
            continue
        if {n for n, x in vals.items() if x == 'leave'} == want:
            best = w if best is None or (w.calendar_code.startswith('REST-') and not best.calendar_code.startswith('REST-')) else best
    if best is not None:
        return best, False
    code = 'REST-' + '-'.join(ABBR[d].upper() for d in days) if days else 'REST-NONE'
    vals = {n.lower(): ('leave' if n in want else 'fullday') for n in DAY_NAMES}
    w = W.objects.create(calendar_code=code[:100], year=y, description=('Rest days: ' + ', '.join(days)) if days else 'No rest day',
                         created_by=user if getattr(user, 'pk', None) else None, is_alternate=False, alternate_weekends={}, **vals)
    return w, True


def set_rest_days(level, ids, days, user=None):
    """Write the rest days for the targets at `level` into the weekend records. days [] = take the targets out of their own
    weekend at this level (the next level decides again). Returns {'calendar', 'created', 'employees', 'targets'}."""
    from calendars.workdays import load_changes
    from Translations.i18n import tr
    AW = apps.get_model('calendars', 'assign_weekend')
    changes = load_changes()
    W, created = (None, False)
    if days:
        W, created = _calendar_for(days, user)
    for tid in ids:
        holders = _holders(level, tid)
        for a in holders:
            if changes.get(a.pk):
                raise ValueError(tr('A weekend change from a date is planned for this {0} – change it on Calendar › Weekend change from a date.', level))
        if not days:
            for a in holders:
                getattr(a, level).remove(tid)
            continue
        own = [a for a in holders if {x.pk for x in getattr(a, level).all()} == {tid}]
        if own:
            a = own[-1]
            if a.weekend_model_id != W.pk or a.related_to != level:
                AW.objects.filter(pk=a.pk).update(weekend_model=W, related_to=level)
            for b in holders:
                if b.pk != a.pk:
                    getattr(b, level).remove(tid)
            continue
        for b in holders:
            getattr(b, level).remove(tid)
        n = AW.objects.create(related_to=level, weekend_model=W, created_by=user if getattr(user, 'pk', None) else None)
        getattr(n, level).add(tid)
    E = apps.get_model('EmpManagement', 'emp_master')
    qs = E.objects.filter(pk__in=ids) if level == 'employee' else E.objects.filter(**{LEVEL_FIELD[level] + '_id__in': ids})
    emps = list(qs)
    from LeavePolicy.weekend_change import _sync_emp_calendar
    _sync_emp_calendar(emps)
    return {'calendar': W.calendar_code if W else '', 'calendar_id': W.pk if W else None, 'created': created, 'employees': len(emps),
            'targets': len(ids), 'days': days}


def rule_target(rule):
    """(level, ids) of the weekend records an attendance rule's rest days write (company = every branch)."""
    s = getattr(rule, 'scope', 'company') or 'company'
    if s == 'company':
        return 'branch', list(apps.get_model('OrganisationManager', 'brnch_mstr').objects.values_list('pk', flat=True))
    fid = {'branch': 'branch_id', 'department': 'department_id', 'category': 'category_id', 'employee': 'employee_id'}[s]
    v = getattr(rule, fid, None)
    return s, ([int(v)] if v else [])


def rest_days_of_rule(rule, rules=None):
    from calendars.workdays import WeekendRules
    level, ids = rule_target(rule)
    rules = rules or WeekendRules()
    rows = [rest_days_of(level, i, rules) for i in ids]
    if not rows:
        return {'days': [], 'set': False, 'level': level, 'mixed': False}
    first = rows[0]['days']
    mixed = any(r['days'] != first or r['set'] != rows[0]['set'] for r in rows)
    return {'days': [] if mixed else first, 'set': all(r['set'] for r in rows), 'level': level, 'mixed': mixed,
            'calendar': '' if mixed else rows[0].get('calendar', ''), 'half_days': any(r['half_days'] for r in rows),
            'alternate': any(r['alternate'] for r in rows)}


def can_set_weekends(request):
    try:
        from AccessControl.access import ctx
        c = ctx(request)
        return c.admin or bool({'add_assign_weekend', 'change_assign_weekend'} & set(c.codes))
    except Exception:
        return False
