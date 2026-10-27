"""v1.61.0 – skills-based scheduling: a shift can say which skills it needs (Shift master › Skills needed: skill, level and how
many people), e.g. the night shift of a clinic needs 1 person with "Basic life support" at level 3. The skills of the
employees are the ones in Learning › Skills (EmployeeSkill, level 1–5).

coverage(period)      every day × shift of the roster where the people on the shift do not have the skills it needs
candidates(...)       who could cover a gap on a day: employees of the roster with the skill at the level, not on leave, not
                      already on that shift – free (empty or day off) and available first, then by level
The roster screen shows the gaps and offers the candidates; choosing one puts them on the shift through the normal cell
change (so rest-hour and week-hour checks run as usual).
"""
from datetime import timedelta

from django.apps import apps

from Translations.i18n import tr

from . import services as S
from .models import RosterEntry, ShiftRule


def _skill_model():
    try:
        return apps.get_model('LearningPlus', 'Skill'), apps.get_model('LearningPlus', 'EmployeeSkill')
    except LookupError:
        return None, None


def clean_needs(raw):
    """[{skill, level, count}] checked against the skills list; raises ValueError with a message."""
    Skill, _ = _skill_model()
    if raw in (None, '', []):
        return []
    if not isinstance(raw, list):
        raise ValueError(tr('Give the skills needed as a list.'))
    if Skill is None:
        raise ValueError(tr('Skills are kept in Learning – that app is not installed.'))
    out, seen = [], set()
    for x in raw:
        try:
            sid, lvl, n = int((x or {}).get('skill')), int((x or {}).get('level') or 1), int((x or {}).get('count') or 1)
        except (TypeError, ValueError):
            raise ValueError(tr('Each skill needs the skill, the level (1–5) and how many people.'))
        if not Skill.objects.filter(pk=sid).exists():
            raise ValueError(tr('Choose skills from the list.'))
        if not 1 <= lvl <= 5 or not 1 <= n <= 50:
            raise ValueError(tr('The level is 1–5 and the number of people 1–50.'))
        if sid in seen:
            raise ValueError(tr('Each skill once per shift.'))
        seen.add(sid)
        out.append({'skill': sid, 'level': lvl, 'count': n})
    return out


def _levels(emp_ids, skill_ids):
    _, ES = _skill_model()
    out = {}
    if ES is None or not emp_ids or not skill_ids:
        return out
    for e, s, lv in ES.objects.filter(employee_id__in=list(emp_ids), skill_id__in=list(skill_ids)).values_list('employee_id', 'skill_id', 'level'):
        out[(e, s)] = lv or 0
    return out


def needs_by_shift():
    return {r.shift_id: list(r.skills or []) for r in ShiftRule.objects.exclude(skills=[]) if r.skills}


def coverage(p, a=None, b=None):
    """{'gaps': [...], 'checked': n, 'shifts_with_needs': n} for a roster period."""
    Skill, _ = _skill_model()
    needs = needs_by_shift()
    a = max(a or p.date_from, p.date_from)
    b = min(b or p.date_to, p.date_to)
    if not needs or Skill is None:
        return {'gaps': [], 'checked': 0, 'shifts_with_needs': len(needs)}
    ids = [e.pk for e in S.period_employees(p)]
    leaves = S.leave_map(ids, a, b)
    by_cell = {}
    for x in RosterEntry.objects.filter(period=p, date__gte=a, date__lte=b, employee_id__in=ids, off=False, shift_id__in=list(needs)):
        if leaves.get((x.employee_id, x.date)):
            continue   # on leave: does not count on the shift
        by_cell.setdefault((x.date, x.shift_id), []).append(x.employee_id)
    skill_ids = {n['skill'] for v in needs.values() for n in v}
    lv = _levels(ids, skill_ids)
    names = dict(Skill.objects.filter(pk__in=skill_ids).values_list('id', 'name'))
    shifts = S.shifts_by_id()
    gaps, checked = [], 0
    d = a
    while d <= b:
        for sid, ns in needs.items():
            people = by_cell.get((d, sid), [])
            if not people:
                continue   # the shift is not planned that day – nothing to cover
            for n in ns:
                checked += 1
                have = [e for e in people if lv.get((e, n['skill']), 0) >= n['level']]
                if len(have) < n['count']:
                    sh = shifts.get(sid)
                    gaps.append({'date': d, 'shift_id': sid, 'shift': getattr(sh, 'name', str(sid)), 'skill_id': n['skill'],
                                 'skill': names.get(n['skill'], str(n['skill'])), 'level': n['level'], 'needed': n['count'], 'have': len(have),
                                 'short': n['count'] - len(have), 'on_shift': len(people)})
        d += timedelta(days=1)
    return {'gaps': gaps, 'checked': checked, 'shifts_with_needs': len(needs)}


def candidates(p, day, shift_id, skill_id, level):
    """Employees of the roster who have the skill at the level and could take the shift on the day."""
    emps = {e.pk: e for e in S.period_employees(p)}
    lv = _levels(list(emps), [skill_id])
    leaves = S.leave_map(list(emps), day, day)
    cur = {x.employee_id: x for x in RosterEntry.objects.filter(period=p, date=day, employee_id__in=list(emps))}
    avail = S.availability_index(list(emps))
    shifts = S.shifts_by_id()
    out = []
    for eid, e in emps.items():
        have = lv.get((eid, skill_id), 0)
        if have < level or leaves.get((eid, day)):
            continue
        x = cur.get(eid)
        if x is not None and not x.off and x.shift_id == shift_id:
            continue   # already on it
        flag = S.availability_flag(avail.get(eid, []), day, shift_id) if avail.get(eid) else ''
        state = 'free' if x is None else ('off' if x.off else 'other_shift')
        out.append({'employee_id': eid, 'code': e.emp_code, 'name': S.person(e), 'level': have,
                    'now': state, 'now_shift': getattr(shifts.get(x.shift_id), 'name', '') if x is not None and x.shift_id and not x.off else '',
                    'availability': flag})
    rank = {'free': 0, 'off': 1, 'other_shift': 2}
    out.sort(key=lambda c: (c['availability'] == 'unavailable', rank[c['now']], -c['level'], c['name']))
    return out
