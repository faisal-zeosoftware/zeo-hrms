"""v1.67.19 (shift check 3, 5, 10) – may a shift be planned for an employee?

A shift switched off in Shift master (ShiftRule.active = False, "hidden from new plans") and a shift limited to some
branches (ShiftRule.branch_ids) were accepted in shift patterns, employee shifts (allocations) and shift overrides; only the
roster check warned afterwards. These helpers say why a shift cannot be used, so the screens can refuse it when it is chosen.
"""
from Translations.i18n import tr


def _rules(ids):
    from .models import ShiftRule
    return {r.shift_id: r for r in ShiftRule.objects.filter(shift_id__in=[i for i in ids if i])}


def _names(ids):
    from calendars.models import Shift
    return dict(Shift.objects.filter(pk__in=[i for i in ids if i]).values_list('id', 'name'))


def _branch_names(ids):
    from OrganisationManager.models import brnch_mstr
    return dict(brnch_mstr.objects.filter(pk__in=ids).values_list('id', 'branch_name'))


def inactive(shift_ids):
    """[(id, name)] of the shifts switched off."""
    rules, names = _rules(shift_ids), _names(shift_ids)
    return [(i, names.get(i, str(i))) for i in dict.fromkeys(shift_ids) if i in rules and not rules[i].active]


def branch_problem(shift_id, emp):
    """Message when the shift is limited to branches the employee is not in, else ''."""
    r = _rules([shift_id]).get(shift_id)
    allowed = [int(b) for b in (r.branch_ids or [])] if r else []
    if not allowed or emp is None or emp.emp_branch_id_id in allowed:
        return ''
    bn = _branch_names(allowed + [emp.emp_branch_id_id])
    return tr('“{0}” is for {1} only – {2} works at {3}.', _names([shift_id]).get(shift_id, ''), ', '.join(bn.get(b, '') for b in allowed),
              f'{emp.emp_first_name} {emp.emp_last_name or ""}'.strip(), bn.get(emp.emp_branch_id_id, ''))


def shift_problem(shift_id, emp):
    """Why the shift cannot be planned for the employee ('' = it can)."""
    if inactive([shift_id]):
        return tr('“{0}” is switched off in Shift master (Active = No) – choose another shift.', _names([shift_id]).get(shift_id, ''))
    return branch_problem(shift_id, emp)


def pattern_shift_ids(p):
    """Every shift a calendars.ShiftPattern uses."""
    out = []
    cfg = p.pattern_config or {} if p is not None else {}
    for w in cfg.get('weeks') or []:
        out += [r.get('shift_id') for r in w.get('rules') or []]
    for m in cfg.get('months') or []:
        out += [r.get('shift_id') for r in m.get('rules') or []]
    out += list((cfg.get('rotating') or {}).get('shift_sequence') or [])
    if p is not None:
        out += [getattr(p, f'{d}_shift_id', None) for d in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')]
        out.append(getattr(p, 'rotating_shift_id', None))
    return [int(x) for x in dict.fromkeys(out) if str(x or '').isdigit()]


def config_shift_ids(cfg):
    out = []
    for w in (cfg or {}).get('weeks') or []:
        out += [r.get('shift_id') for r in w.get('rules') or []]
    for m in (cfg or {}).get('months') or []:
        out += [r.get('shift_id') for r in m.get('rules') or []]
    out += list(((cfg or {}).get('rotating') or {}).get('shift_sequence') or [])
    return [int(x) for x in dict.fromkeys(out) if str(x or '').isdigit()]


def pattern_branch_problem(p, emps):
    """First message for an employee who may not work a shift of the pattern, else ''."""
    ids = pattern_shift_ids(p)
    for e in emps:
        for sid in ids:
            m = branch_problem(sid, e)
            if m:
                return m
    return ''
