# v1.67.8 (fixes)
"""v1.67.8 (fixes) – item 1: a holiday calendar can be made inactive.

`calendars.holiday_calendar` has no active column and calendars is a core app (no migration allowed), so the inactive
calendars of a company are kept in the non-core store `ZeoApps.CompanyOption` (key `holiday_calendar_inactive`,
value {"ids": [..]}). The calendar API reads / writes it as the field `is_active` (see HolidayCalandarSerializer), so the
shared list's "Make inactive / Make active" (DataTools.usage ActiveView → PATCH {is_active}) works as on every other master.

Meaning of "inactive": the calendar is no longer offered in pickers (Assign holidays, employee form) and is hidden by the
list's "Active only" filter. Assignments that already exist keep working (leave days and payroll of the dates it covers
are not changed behind the user's back); to stop applying it, change the assignment.
"""
KEY = 'holiday_calendar_inactive'


def _opt():
    from ZeoApps.models import CompanyOption
    return CompanyOption


def inactive_ids():
    try:
        o = _opt().objects.filter(key=KEY).first()
    except Exception:   # noqa: BLE001 – a schema without ZeoApps tables (public)
        return set()
    return {int(x) for x in ((o.value or {}).get('ids') or []) if str(x).isdigit()} if o else set()


def is_active(cal):
    return getattr(cal, 'pk', None) not in inactive_ids()


def set_active(cal_id, active):
    ids = inactive_ids()
    if active:
        ids.discard(int(cal_id))
    else:
        ids.add(int(cal_id))
    if ids:
        _opt().objects.update_or_create(key=KEY, defaults={'value': {'ids': sorted(ids)}})
    else:   # nothing inactive: no row left behind
        _opt().objects.filter(key=KEY).delete()


def forget(cal_id):
    """A deleted calendar leaves the store."""
    ids = inactive_ids()
    if int(cal_id) in ids:
        set_active(cal_id, True)


def in_use(cal):
    """(text, n) – what the calendar is used by: its assignments (branches, departments, categories, designations,
    employees) and the employees that carry it on their record."""
    from Translations.i18n import tr
    from calendars.models import assign_holiday
    from EmpManagement.models import emp_master
    bits, n = [], 0
    rows = list(assign_holiday.objects.filter(holiday_model=cal).prefetch_related('branch', 'department', 'category', 'designation', 'employee'))
    for field, one, many in (('branch', 'branch', 'branches'), ('department', 'department', 'departments'), ('category', 'category', 'categories'),
                             ('designation', 'designation', 'designations'), ('employee', 'employee', 'employees')):
        k = sum(getattr(r, field).count() for r in rows)
        if k:
            n += k
            bits.append(tr('{0} ' + (one if k == 1 else many), k))
    emps = emp_master.objects.filter(holiday_calendar=cal).count()
    if emps:
        n += emps
        bits.append(tr('{0} employee record(s)', emps))
    if rows and not n:   # an assignment without any target left
        n = len(rows)
        bits.append(tr('{0} assignment(s)', n))
    return ', '.join(bits), n


def used_message(cal):
    from Translations.i18n import tr
    what, n = in_use(cal)
    if not n:
        return ''
    return tr('The holiday calendar "{0}" is in use – it is assigned to {1}. It cannot be deleted. Make it inactive instead: '
              'it is then no longer offered for new assignments, and the existing assignments keep working until you change them.',
              getattr(cal, 'calendar_title', str(cal)), what)
