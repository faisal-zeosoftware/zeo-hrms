"""v1.12.0 – shift planner rules: rights, leave look-ups, roster generation / copy / checks and applying approved changes."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import notify
from .models import Availability, RosterEntry, RosterPeriod, ShiftRule
from .resolver import Book, ZERO, describe, holidays

EDITABLE = ('draft', 'rejected', 'published')
LIVE = ('draft', 'submitted', 'approved', 'published', 'rejected')
MANAGE_CODES = ('add_rosterperiod', 'change_rosterperiod', 'change_employeeshiftschedule', 'add_employeeshiftschedule')
LEAVE_STATES = ('pending', 'approved')


class PlanError(Exception):
    def __init__(self, message, status=400, problems=None):
        super().__init__(message)
        self.message, self.status, self.problems = message, status, problems or []


# ------------------------------------------------------------------ people
def E():
    from EmpManagement.models import emp_master
    return emp_master


def person(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)
    return f'{n} ({e.emp_code})' if n else e.emp_code


def emp_by_id(i):
    try:
        return E().objects.filter(pk=int(i)).first()
    except (TypeError, ValueError):
        return None


def shifts_by_id():
    from calendars.models import Shift
    return {s.pk: s for s in Shift.objects.all()}


def shift_label(s, rules=None):
    if s is None:
        return 'Off'
    r = (rules or {}).get(s.pk)
    t = f' {s.start_time:%H:%M}–{s.end_time:%H:%M}' if s.start_time and s.end_time else ''
    if t and (t.strip() in s.name or t.strip().replace('–', '-') in s.name):   # v1.12.0: name already shows the times
        t = ''
    return f'{s.name}{t}' + (f' [{r.code}]' if r is not None and r.code else '')


# ------------------------------------------------------------------ rights
class Rights:
    """Who may do what (computed once per request)."""

    def __init__(self, request):
        from AccessControl.access import ctx
        c = ctx(request)
        self.user = request.user
        self.admin = c.admin
        self.codes = c.codes
        self.emp = c.emp
        self.branches = None if c.admin else (c.branches or [])
        self.manage = self.admin or any(x in self.codes for x in MANAGE_CODES)
        self._team = None

    def has(self, *codes):
        return self.admin or any(x in self.codes for x in codes)

    def branch_ok(self, branch_id):
        return self.branches is None or (branch_id is not None and int(branch_id) in self.branches)

    def manages_emp(self, emp):
        return emp is not None and self.manage and self.branch_ok(emp.emp_branch_id_id)

    @property
    def team(self):
        if self._team is None:
            self._team = set(E().objects.filter(emp_reporting_manager=self.user).values_list('pk', flat=True)) if self.user.is_authenticated else set()
        return self._team

    def is_manager_of(self, emp):
        return emp is not None and emp.pk in self.team

    def can_see_emp(self, emp):
        return emp is not None and (self.manages_emp(emp) or self.is_manager_of(emp) or (self.emp is not None and self.emp.pk == emp.pk))

    def can_decide_for(self, emp):
        """Approve requests / open-shift claims of this employee (never their own unless company admin)."""
        if emp is None:
            return False
        if self.emp is not None and self.emp.pk == emp.pk and not self.admin:
            return False
        return self.manages_emp(emp) or self.is_manager_of(emp)

    def can_approve_period(self, p):
        if self.admin:
            return True
        if p.approver_id and p.approver_id == self.user.pk:
            return True
        return 'approve_rosterperiod' in self.codes and self.branch_ok(p.branch_id) and p.submitted_by_id != self.user.pk

    def can_publish(self, p):
        if self.admin or (p.approver_id and p.approver_id == self.user.pk):
            return True
        return any(x in self.codes for x in ('publish_rosterperiod', 'approve_rosterperiod')) and self.branch_ok(p.branch_id)

    def can_manage_period(self, p):
        return self.manage and self.branch_ok(p.branch_id)


# ------------------------------------------------------------------ leave / availability
def leave_map(emp_ids, a, b):
    """{(emp_id, day): 'approved' | 'pending'} – leave requests overlapping a..b."""
    from calendars.models import employee_leave_request as LR
    out = {}
    qs = LR.objects.filter(employee_id__in=list(emp_ids), status__in=LEAVE_STATES, start_date__lte=b, end_date__gte=a) \
        .select_related('leave_type').order_by('status')
    for r in qs:
        d = max(r.start_date, a)
        while d <= min(r.end_date, b):
            k = (r.employee_id, d)
            if out.get(k, {}).get('status') != 'approved':
                out[k] = {'status': r.status, 'type': getattr(r.leave_type, 'name', '') or 'Leave', 'half': bool(r.dis_half_day)}
            d += timedelta(days=1)
    return out


def leave_on(emp_id, day):
    return leave_map([emp_id], day, day).get((emp_id, day))


def refuse_if_leave(emp, *days):
    for d in days:
        lv = leave_on(emp.pk, d)
        if lv:
            raise PlanError(f'{person(emp)} has {lv["status"]} leave ({lv["type"]}) on {d:%d %b %Y}. Cancel or change the leave first.')


def availability_index(emp_ids):
    idx = defaultdict(list)
    for a in Availability.objects.filter(employee_id__in=list(emp_ids)):
        idx[a.employee_id].append(a)
    return idx


def availability_on(records, day, shift_id=None):
    """The availability records that apply to the day: [(kind, record)]."""
    out = []
    for a in records:
        if a.weekday is not None:
            if a.weekday != day.weekday():
                continue
            if a.date_from and day < a.date_from:
                continue
            if a.date_to and day > a.date_to:
                continue
        elif a.date_from:
            if not (a.date_from <= day <= (a.date_to or a.date_from)):
                continue
        else:
            continue
        out.append(a)
    return out


def availability_flag(records, day, shift_id):
    """'unavailable' / 'not_preferred' / 'preferred' / '' for a shift on the day."""
    recs = availability_on(records, day, shift_id)
    for a in recs:
        if a.kind == 'unavailable' and (a.shift_id is None or a.shift_id == shift_id):
            return 'unavailable'
    for a in recs:
        if a.kind in ('preferred', 'available') and a.shift_id:
            return 'preferred' if a.shift_id == shift_id else 'not_preferred'
    return 'preferred' if any(a.kind in ('preferred', 'available') for a in recs) else ''


# ------------------------------------------------------------------ roster scope
def period_employees(p):
    qs = E().objects.filter(emp_branch_id_id=p.branch_id).exclude(is_active=False)
    if p.department_id:
        qs = qs.filter(emp_dept_id_id=p.department_id)
    return list(qs.select_related('emp_dept_id', 'emp_desgntn_id').order_by('emp_dept_id__dept_name', 'emp_first_name', 'emp_code'))


def days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _weekend_names(emp):
    from .resolver import _weekend_names as w
    return w(emp)


def check_editable(p):
    if p.status not in EDITABLE:
        raise PlanError(f'This roster is {p.get_status_display().lower()}. Send it back to draft before changing it.', status=409)


def upsert_entry(p, emp, day, shift_id, off, source, user=None, note=None, notify_after_publish=True):
    """Set one roster cell. On a published roster the change is marked and the employee is told."""
    if day < p.date_from or day > p.date_to:
        raise PlanError(f'{day:%d %b %Y} is outside this roster ({p.date_from:%d %b} – {p.date_to:%d %b %Y}).')
    if shift_id is None and not off:
        RosterEntry.objects.filter(period=p, employee_id=emp.pk, date=day).delete()
        return None
    e, created = RosterEntry.objects.get_or_create(period=p, employee_id=emp.pk, date=day,
                                                   defaults={'shift_id': None if off else shift_id, 'off': bool(off), 'source': source})
    before = (e.shift_id, e.off)
    e.shift_id = None if off else shift_id
    e.off = bool(off)
    e.source = source
    if note is not None:
        e.note = (note or '')[:255]
    e.updated_by_id = getattr(user, 'pk', None)
    if p.status == 'published' and (created or before != (e.shift_id, e.off)):
        e.changed_after_publish = True
    e.save()
    if p.status == 'published' and notify_after_publish and (created or before != (e.shift_id, e.off)):
        sh = shifts_by_id().get(e.shift_id) if e.shift_id else None
        notify.send(employee=emp, kind='changed', title='Your shift was changed',
                    message=f'Your shift on {day:%a %d %b %Y} is now: {shift_label(sh)}.' + (f' Note: {e.note}' if e.note else ''))
    return e


def generate(p, user=None, overwrite=False):
    """Fill the roster from shift overrides, shift schedules / patterns and the default shift. Returns counts."""
    check_editable(p)
    emps = period_employees(p)
    book = Book(p.date_from, p.date_to, [e.pk for e in emps], roster=False)
    existing = {(x.employee_id, x.date): x for x in RosterEntry.objects.filter(period=p)}
    out = {'created': 0, 'updated': 0, 'kept': 0, 'no_shift': 0}
    with transaction.atomic():
        for emp in emps:
            hol = holidays(emp, p.date_from, p.date_to)
            wk = None
            for d in days(p.date_from, p.date_to):
                cur = existing.get((emp.pk, d))
                if cur is not None and not overwrite and cur.source != 'generated':
                    out['kept'] += 1
                    continue
                r = book.get(emp, d)
                note = ''
                if r is not None:
                    shift_id, off = r['shift_id'], r['off']
                    if d in hol and not off:
                        shift_id, off, note = None, True, 'Public holiday'
                elif p.default_shift_id:
                    if wk is None:
                        wk = _weekend_names(emp)
                    off = d.strftime('%A').lower() in wk or d in hol
                    shift_id = None if off else p.default_shift_id
                    note = 'Public holiday' if d in hol else ''
                else:
                    out['no_shift'] += 1
                    continue
                if cur is None:
                    RosterEntry.objects.create(period=p, employee_id=emp.pk, date=d, shift_id=None if off else shift_id, off=off,
                                               note=note, source='generated', updated_by_id=getattr(user, 'pk', None))
                    out['created'] += 1
                else:
                    cur.shift_id, cur.off, cur.note, cur.source = (None if off else shift_id), off, note, 'generated'
                    cur.updated_by_id = getattr(user, 'pk', None)
                    cur.save()
                    out['updated'] += 1
    return out


def entry_on(emp_id, day, exclude_period=None):
    """Best roster entry of the employee on the day in any period (published first)."""
    qs = RosterEntry.objects.filter(employee_id=emp_id, date=day, period__status__in=LIVE)
    if exclude_period is not None:
        qs = qs.exclude(period=exclude_period)
    order = {'published': 0, 'approved': 1, 'submitted': 2, 'draft': 3, 'rejected': 4}
    rows = sorted(qs.select_related('period'), key=lambda e: (order.get(e.period.status, 9), -e.period_id))
    return rows[0] if rows else None


def copy_week(p, target_start, source_start=None, user=None):
    """Copy the 7 days from source_start (default: the week before) onto target_start.. of this roster."""
    check_editable(p)
    source_start = source_start or (target_start - timedelta(days=7))
    emps = period_employees(p)
    n = 0
    with transaction.atomic():
        for emp in emps:
            for i in range(7):
                t, s = target_start + timedelta(days=i), source_start + timedelta(days=i)
                if t < p.date_from or t > p.date_to:
                    continue
                src = RosterEntry.objects.filter(period=p, employee_id=emp.pk, date=s).first() or entry_on(emp.pk, s, exclude_period=p)
                if src is None:
                    continue
                upsert_entry(p, emp, t, src.shift_id, src.off, 'copy', user=user, note=src.note)
                n += 1
    return n


# ------------------------------------------------------------------ checks
def check_period(p, emp_ids=None):
    """Problems of the roster: [{employee_id, employee, date, kind, level, message}]. level 'error' blocks publishing."""
    rows = list(RosterEntry.objects.filter(period=p).order_by('employee_id', 'date'))
    if emp_ids is not None:
        rows = [r for r in rows if r.employee_id in emp_ids]
    ids = {r.employee_id for r in rows}
    emps = {e.pk: e for e in E().objects.filter(pk__in=ids)}
    shifts = shifts_by_id()
    rules = {r.shift_id: r for r in ShiftRule.objects.all()}
    leaves = leave_map(ids, p.date_from, p.date_to)
    avail = availability_index(ids)
    others = defaultdict(list)
    for o in RosterEntry.objects.filter(employee_id__in=ids, date__gte=p.date_from, date__lte=p.date_to, period__status__in=LIVE,
                                        off=False, shift_id__isnull=False).exclude(period=p).select_related('period'):
        others[(o.employee_id, o.date)].append(o)
    out = []

    def add(r, kind, level, msg):
        out.append({'employee_id': r.employee_id, 'employee': person(emps.get(r.employee_id)), 'date': r.date, 'kind': kind, 'level': level, 'message': msg})

    prev = {}
    week = defaultdict(lambda: ZERO)
    for r in rows:
        sh = shifts.get(r.shift_id) if (r.shift_id and not r.off) else None
        if r.shift_id and not r.off and sh is None:
            add(r, 'missing_shift', 'error', 'The shift of this day no longer exists. Choose another shift.')
            continue
        if sh is None:  # day off: nothing to check
            continue
        rule = rules.get(sh.pk)
        info = describe(sh, rule, r.date, 'roster')
        for o in others.get((r.employee_id, r.date), []):
            add(r, 'double', 'error', f'Also on shift {shift_label(shifts.get(o.shift_id))} in roster “{o.period.name}”. Keep one of them.')
        lv = leaves.get((r.employee_id, r.date))
        if lv:
            add(r, 'leave', 'warning', f'{lv["status"].capitalize()} leave ({lv["type"]}) on this day.')
        fl = availability_flag(avail.get(r.employee_id, []), r.date, sh.pk)
        if fl == 'unavailable':
            add(r, 'unavailable', 'warning', 'The employee said they are not available on this day.')
        elif fl == 'not_preferred':
            add(r, 'preference', 'info', 'The employee prefers another shift on this day.')
        if rule is not None and not rule.active:
            add(r, 'inactive', 'warning', f'Shift {sh.name} is switched off.')
        emp = emps.get(r.employee_id)
        if rule is not None and rule.branch_ids and emp is not None and emp.emp_branch_id_id not in [int(x) for x in rule.branch_ids]:
            add(r, 'branch', 'warning', f'Shift {sh.name} is not meant for this branch.')
        last = prev.get(r.employee_id)
        if last is not None and info['start'] is not None and last['end'] is not None:
            rest = Decimal((info['start'] - last['end']).total_seconds()) / Decimal(3600)
            if rest < Decimal(p.min_rest_hours or 0):
                add(r, 'rest', 'warning' if rest >= 0 else 'error',
                    f'Only {max(rest, ZERO):.1f} h rest after the previous shift (minimum {Decimal(p.min_rest_hours):.0f} h).' if rest >= 0
                    else 'This shift starts before the previous one ends.')
        prev[r.employee_id] = info
        wk = r.date - timedelta(days=r.date.weekday())
        week[(r.employee_id, wk)] += info['hours']
    for (eid, wk), h in week.items():
        if p.max_week_hours and h > Decimal(p.max_week_hours):
            out.append({'employee_id': eid, 'employee': person(emps.get(eid)), 'date': wk, 'kind': 'week_hours', 'level': 'warning',
                        'message': f'{h:.1f} h planned in the week of {wk:%d %b} (limit {Decimal(p.max_week_hours):.0f} h).'})
    return out


# ------------------------------------------------------------------ applying approved changes
def current_shift(emp, day):
    """(shift_id or None, off) the employee works on the day as it stands (published roster > override > schedule)."""
    from .resolver import shift_for
    r = shift_for(emp, day)
    if r is None:
        return None, True
    return r['shift_id'], r['off']


def set_shift(emp, day, shift_id, source, user=None, note=''):
    """Make shift_id (None = day off) the employee's shift on the day so attendance, payroll and ESS follow:
    every roster entry of the day is updated (a published one is marked changed); without a published roster a
    calendars.ShiftOverride is written."""
    from calendars.models import ShiftOverride
    off = shift_id is None
    touched = False
    for e in RosterEntry.objects.filter(employee_id=emp.pk, date=day, period__status__in=LIVE).select_related('period'):
        e.shift_id, e.off, e.source = (None if off else shift_id), off, source
        if note:
            e.note = note[:255]
        if e.period.status == 'published':
            e.changed_after_publish = True
            touched = True
        e.updated_by_id = getattr(user, 'pk', None)
        e.save()
    if not touched:
        published = RosterPeriod.objects.filter(status='published', branch_id=emp.emp_branch_id_id, date_from__lte=day, date_to__gte=day) \
            .filter(Q(department_id__isnull=True) | Q(department_id=emp.emp_dept_id_id)).order_by('-published_at').first()
        if published is not None:
            RosterEntry.objects.update_or_create(period=published, employee_id=emp.pk, date=day, defaults={
                'shift_id': None if off else shift_id, 'off': off, 'source': source, 'changed_after_publish': True,
                'note': note[:255], 'updated_by_id': getattr(user, 'pk', None)})
        else:
            ShiftOverride.objects.update_or_create(employee=emp, date=day, defaults={'override_shift_id': None if off else shift_id,
                                                                                        'created_by': user if getattr(user, 'pk', None) else None})


def stamp(obj, user, status, note=''):
    obj.status = status
    obj.decided_by_id = getattr(user, 'pk', None)
    obj.decided_at = timezone.now()
    obj.decision_note = (note or '')[:255]
