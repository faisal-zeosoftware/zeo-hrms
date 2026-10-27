"""v1.56.0 – one row per employee category with the main setting of the time, shift, leave and payroll steps (setup guide,
steps 6–9, category view). Most specific wins: employee › category › department › branch › company.

Columns: weekend calendar (Assign weekends, by category) · working hours (attendance rule of the category) · roster (shift
schedule of the category) · leave policy (Leave policies, by category) · pay structure (by category).
"""
import datetime

from django.apps import apps
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Translations.i18n import tr

M = apps.get_model


class MatrixError(ValueError):
    pass


def _has(label):
    try:
        M(label)
        return True
    except LookupError:
        return False


def _cats():
    C = M('OrganisationManager', 'ctgry_master')
    return list(C.objects.filter(Q(ctgry_is_active=True) | Q(ctgry_is_active__isnull=True)).order_by('ctgry_title'))


def options():
    W = M('calendars', 'weekend_calendar')
    P = M('calendars', 'ShiftPattern')
    LP = M('LeavePolicy', 'LeavePolicy') if _has('LeavePolicy.LeavePolicy') else None
    PS = M('PayrollManagement', 'PayStructure')
    def ps_label(p):
        t = {'FIXED_DAYS': tr('{0} fixed days', p.fixed_working_days or 30), 'CALENDAR_DAYS': tr('Calendar days'), 'ORGANIZATION_DAYS': tr('Working days')}.get(p.salary_calculation_type, '')
        cats = ', '.join(p.category.values_list('ctgry_title', flat=True)[:3])
        whole = not (p.branch.exists() or p.department.exists() or p.category.exists() or p.designation.exists())
        return f'#{p.pk} {t}' + (f' ({cats})' if cats else (f' ({tr("whole company")})' if whole else ''))
    return {
        'weekend': [{'value': w.pk, 'label': f'{w.calendar_code} {w.year or ""}'.strip()} for w in W.objects.order_by('-year', 'calendar_code')],
        'roster': [{'value': p.pk, 'label': p.name} for p in P.objects.order_by('name')],
        'leave': [{'value': p.pk, 'label': p.name} for p in LP.objects.filter(active=True).order_by('name')] if LP else [],
        'pay': [{'value': p.pk, 'label': ps_label(p)} for p in PS.objects.order_by('id')],
    }


def rows():
    AW = M('calendars', 'assign_weekend')
    R = M('AttendancePlus', 'AttendanceRule') if _has('AttendancePlus.AttendanceRule') else None
    Sch = M('calendars', 'EmployeeShiftSchedule')
    LP = M('LeavePolicy', 'LeavePolicy') if _has('LeavePolicy.LeavePolicy') else None
    PS = M('PayrollManagement', 'PayStructure')
    E = M('EmpManagement', 'emp_master')
    out = []
    pols = list(LP.objects.filter(active=True)) if LP else []
    for c in _cats():
        aw = AW.objects.filter(related_to='category', category=c).select_related('weekend_model').order_by('-id').first()
        rule = R.objects.filter(scope='category', category_id=c.pk, is_active=True).order_by('priority').first() if R else None
        sch = Sch.objects.filter(categories=c).order_by('-start_date').first()
        pol = next((p for p in pols if c.pk in (p.category_ids or [])), None) or next((p for p in pols if p.is_default), None)
        ps = PS.objects.filter(category=c).order_by('-id').first()
        if ps is None:   # the company-wide structure, when there is one
            ps = next((p for p in PS.objects.order_by('id') if not (p.branch.exists() or p.department.exists() or p.category.exists() or p.designation.exists())), None)
        n = E.objects.filter(emp_ctgry_id=c, is_active=True).count()
        out.append({
            'category': c.pk, 'name': c.ctgry_title, 'employees': n,
            'weekend': aw.weekend_model_id if aw else None, 'weekend_label': aw.weekend_model.calendar_code if aw else '',
            'start': rule.default_start.strftime('%H:%M') if rule and rule.default_start else '',
            'end': rule.default_end.strftime('%H:%M') if rule and rule.default_end else '',
            'roster': sch.shift_pattern_id if sch else None,
            'leave': pol.pk if pol else None, 'leave_default': bool(pol and c.pk not in (pol.category_ids or [])),
            'pay': ps.pk if ps else None,
        })
    return out


def exceptions():
    """Employee, branch and department exceptions (most specific wins)."""
    AW = M('calendars', 'assign_weekend')
    R = M('AttendancePlus', 'AttendanceRule') if _has('AttendancePlus.AttendanceRule') else None
    ELP = M('LeavePolicy', 'EmployeeLeavePolicy') if _has('LeavePolicy.EmployeeLeavePolicy') else None
    out = []
    for a in AW.objects.exclude(related_to='category').select_related('weekend_model')[:50]:
        who = ', '.join([str(x) for x in list(a.branch.all()[:3]) + list(a.department.all()[:3]) + list(a.employee.all()[:3])])
        out.append(tr('{0} – weekend {1}', who or tr(a.related_to), a.weekend_model.calendar_code))
    if R:
        for r in R.objects.filter(is_active=True).exclude(scope__in=('company', 'category'))[:30]:
            out.append(tr('{0} – attendance rule {1}', tr(r.get_scope_display()), r.name))
    if ELP:
        E = M('EmpManagement', 'emp_master')
        for x in ELP.objects.select_related('policy')[:30]:
            e = E.objects.filter(pk=x.employee_id).first()
            out.append(tr('{0} – own leave policy {1}', f'{e.emp_code} {e.emp_first_name or ""}'.strip() if e else x.employee_id, x.policy.name))
    return out


def _t(v):
    try:
        return datetime.time.fromisoformat(str(v)[:5]) if v else None
    except ValueError:
        raise MatrixError(tr('Enter a time (HH:MM).'))


@transaction.atomic
def save(data, user):
    AW = M('calendars', 'assign_weekend')
    W = M('calendars', 'weekend_calendar')
    R = M('AttendancePlus', 'AttendanceRule') if _has('AttendancePlus.AttendanceRule') else None
    Sch = M('calendars', 'EmployeeShiftSchedule')
    P = M('calendars', 'ShiftPattern')
    LP = M('LeavePolicy', 'LeavePolicy') if _has('LeavePolicy.LeavePolicy') else None
    PS = M('PayrollManagement', 'PayStructure')
    C = M('OrganisationManager', 'ctgry_master')
    current = {r['category']: r for r in rows()}
    changed = 0
    for row in data.get('rows') or []:
        c = C.objects.filter(pk=row.get('category')).first()
        if c is None:
            continue
        cur = current.get(c.pk, {})
        # weekend
        if 'weekend' in row and row['weekend'] != cur.get('weekend'):
            for a in AW.objects.filter(related_to='category', category=c):
                a.category.remove(c)
                if not a.category.exists():
                    a.delete()
            if row['weekend']:
                w = W.objects.filter(pk=row['weekend']).first()
                if w is None:
                    raise MatrixError(tr('Choose a weekend calendar.'))
                a = AW.objects.filter(related_to='category', weekend_model=w).first() or AW.objects.create(related_to='category', weekend_model=w, created_by=user)
                a.category.add(c)
            changed += 1
        # working hours
        if R is not None and ('start' in row or 'end' in row) and (row.get('start') or '', row.get('end') or '') != (cur.get('start') or '', cur.get('end') or ''):
            rule = R.objects.filter(scope='category', category_id=c.pk, is_active=True).order_by('priority').first()
            if row.get('start') and row.get('end'):
                if rule is None:
                    base = R.objects.filter(scope='company', is_active=True).order_by('priority').first()
                    rule = R(name=tr('{0} working hours', c.ctgry_title)[:100], scope='category', category_id=c.pk, is_active=True, priority=50)
                    if base is not None:   # the company rule's other settings
                        for f in base._meta.concrete_fields:
                            if f.name not in ('id', 'name', 'scope', 'category_id', 'branch_id', 'department_id', 'employee_id', 'priority', 'is_active'):
                                setattr(rule, f.name, getattr(base, f.name))
                rule.default_start, rule.default_end = _t(row['start']), _t(row['end'])
                rule.save()
            elif rule is not None:
                rule.is_active = False
                rule.save(update_fields=['is_active'])
            changed += 1
        # roster
        if 'roster' in row and row['roster'] != cur.get('roster'):
            for s in Sch.objects.filter(categories=c):
                s.categories.remove(c)
            if row['roster']:
                p = P.objects.filter(pk=row['roster']).first()
                if p is None:
                    raise MatrixError(tr('Choose a shift pattern.'))
                today = timezone.localdate()
                s = Sch.objects.create(schedule_name=tr('{0} roster', c.ctgry_title)[:100], start_date=today, end_date=today.replace(year=today.year + 1),
                                       shift_pattern=p, employee_offsets={}, created_by=user)
                s.categories.add(c)
            changed += 1
        # leave policy
        if LP is not None and 'leave' in row and row['leave'] and (row['leave'] != cur.get('leave') or cur.get('leave_default')):
            new = LP.objects.filter(pk=row['leave']).first()
            if new is None:
                raise MatrixError(tr('Choose a leave policy.'))
            for p in LP.objects.all():
                ids = [i for i in (p.category_ids or []) if int(i) != c.pk]
                if p.pk == new.pk:
                    ids.append(c.pk)
                if ids != (p.category_ids or []):
                    p.category_ids = ids
                    p.save(update_fields=['category_ids'])
            changed += 1
        # pay structure
        if 'pay' in row and row['pay'] != cur.get('pay'):
            for p in PS.objects.filter(category=c):
                p.category.remove(c)
            if row['pay']:
                p = PS.objects.filter(pk=row['pay']).first()
                if p is None:
                    raise MatrixError(tr('Choose a pay structure.'))
                if p.branch.exists() or p.department.exists() or p.category.exists() or p.designation.exists():
                    p.category.add(c)   # a company-wide structure already applies to every category: left as it is
            changed += 1
    return changed
