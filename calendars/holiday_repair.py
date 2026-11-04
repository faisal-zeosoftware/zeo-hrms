"""v1.62.6 (item 22) – upgrade check and repair: employees whose public-holiday calendar was overwritten.

Bug fixed on 1 Nov 2026 (v1.62.0, calendars/models.py update_department_weekend_calendar / update_category_weekend_calendar):
assigning a WEEKEND calendar by department or category wrote the weekend calendar's id into the employees' HOLIDAY calendar
(emp_master.holiday_calendar). When a holiday calendar with that id existed, the employees silently got another calendar's
public holidays on their record.

check()  -> rows [{employee_id, code, name, active, current_id, current, expected_id, expected, kind, action}]
  kind 'bug'      – the employee's holiday calendar id equals the id of a weekend calendar assigned to the employee's
                    department or category (what the bug wrote) and differs from the holiday assignment → repaired
  kind 'differs'  – differs from the holiday assignment for another reason (e.g. set by hand on the employee) → listed only
apply(rows) – sets holiday_calendar to the calendar of the employee's holiday assignment for the 'bug' rows (queryset update,
no other field touched) and returns what changed.
"""
from django.apps import apps

M = apps.get_model


def check():
    from .workdays import HolidayRules
    E, HC, AW = M('EmpManagement', 'emp_master'), M('calendars', 'holiday_calendar'), M('calendars', 'assign_weekend')
    rules = HolidayRules()
    titles = dict(HC.objects.values_list('id', 'calendar_title'))
    wk = []   # (department ids, category ids, weekend id) of the department / category weekend assignments
    for a in AW.objects.prefetch_related('department', 'category'):
        deps = {x.pk for x in a.department.all()} if a.related_to in (None, 'department') else set()
        cats = {x.pk for x in a.category.all()} if a.related_to in (None, 'category') else set()
        if deps or cats:
            wk.append((deps, cats, a.weekend_model_id))
    out = []
    for e in E.objects.all().order_by('emp_code', 'pk'):
        h = rules.calendar(e)
        exp = h.pk if h is not None else None
        cur = e.holiday_calendar_id
        if cur == exp:
            continue
        if cur is None:
            continue      # no calendar on the record: the assignment is used everywhere (nothing was overwritten)
        hit = any(w == cur and (e.emp_dept_id_id in deps or e.emp_ctgry_id_id in cats) for deps, cats, w in wk)
        out.append({'employee_id': e.pk, 'code': e.emp_code, 'name': f'{e.emp_first_name or ""} {e.emp_last_name or ""}'.strip(),
                    'active': e.is_active, 'current_id': cur, 'current': titles.get(cur, f'#{cur} (does not exist)'),
                    'expected_id': exp, 'expected': titles.get(exp, '') if exp else '',
                    'kind': 'bug' if hit else 'differs',
                    'action': 'repair' if hit else 'listed only – not the weekend bug (check the employee)'})
    return out


def apply(rows):
    from django.db import transaction
    E = M('EmpManagement', 'emp_master')
    done = []
    with transaction.atomic():
        for r in rows:
            if r['kind'] != 'bug':
                continue
            n = E.objects.filter(pk=r['employee_id'], holiday_calendar_id=r['current_id']).update(holiday_calendar_id=r['expected_id'])
            if n:
                done.append(dict(r, changed_to=r['expected_id']))
    return done
