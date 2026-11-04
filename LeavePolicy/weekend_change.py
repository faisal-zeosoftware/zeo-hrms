"""v1.62.6 (item 22) – weekend change with a start date and the leaves it affects.

HR picks who (a branch, department, category, designation or employees), the new weekend calendar and the date it starts.
Before the start date the old weekend still counts (calendars/workdays.py reads the change from the CompanyOption
'weekend_changes' – no new column). The preview lists every approved / waiting leave of those employees that ends on or after
the start date and whose day count changes: old → new days and the balance difference. For each approved leave HR chooses
  * Recalculate – the leave's days are counted again, the difference goes to the leave history as an 'adjustment' ledger line
                  (balance moved with it – engine.post(move_balance=True), the 1.62.4 balance guard adds nothing), the
                  attendance calendar is written again and the employee is notified;
  * Keep as approved – nothing changes on the leave (its day count stays frozen); the choice is kept in the change record.
A leave with a day in a processed / approved / paid payroll period (LeavePolicy/paid_period.closed_periods) never changes: it
is shown as "Payroll paid – kept". A waiting leave is counted again (no balance has moved yet).
"""
from datetime import date, timedelta

from django.apps import apps
from django.db import transaction
from django.utils.dateparse import parse_date
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

M = apps.get_model
LEVELS = ('employee', 'designation', 'category', 'department', 'branch')
EMP_FIELD = {'designation': 'emp_desgntn_id_id', 'category': 'emp_ctgry_id_id', 'department': 'emp_dept_id_id',
             'branch': 'emp_branch_id_id', 'employee': 'pk'}
READ = {'view_assign_weekend', 'add_assign_weekend', 'change_assign_weekend', 'view_employee_leave_request'}
WRITE = {'add_assign_weekend', 'change_assign_weekend'}


class Refused(Exception):
    pass


class _Rollback(Exception):
    pass


# ------------------------------------------------------------------ store
def _option():
    CO = M('ZeoApps', 'CompanyOption')
    o, _ = CO.objects.get_or_create(key='weekend_changes', defaults={'value': {'changes': []}})
    return o


def history():
    try:
        o = M('ZeoApps', 'CompanyOption').objects.filter(key='weekend_changes').first()
    except Exception:
        return []
    return list(((o.value or {}).get('changes') or [])) if o else []


# ------------------------------------------------------------------ who
def target_names(level, ids):
    Mo = {'branch': ('OrganisationManager', 'brnch_mstr', 'branch_name'), 'department': ('OrganisationManager', 'dept_master', 'dept_name'),
          'category': ('OrganisationManager', 'ctgry_master', 'ctgry_title'),
          'designation': ('OrganisationManager', 'desgntn_master', 'desgntn_job_title')}
    if level == 'employee':
        E = M('EmpManagement', 'emp_master')
        return [f'{e.emp_first_name or ""} {e.emp_last_name or ""} ({e.emp_code})'.replace('  ', ' ').strip() for e in E.objects.filter(pk__in=ids)]
    app, model, f = Mo[level]
    return list(M(app, model).objects.filter(pk__in=ids).values_list(f, flat=True))


def employees(level, ids):
    E = M('EmpManagement', 'emp_master')
    qs = E.objects.filter(is_active=True)
    return qs.filter(pk__in=ids) if level == 'employee' else qs.filter(**{EMP_FIELD[level].replace('_id_id', '_id') + '__in': ids})


def _parse(data):
    level = (data.get('level') or '').strip()
    if level not in LEVELS:
        raise Refused(tr('Choose who the weekend change is for: branch, department, category, designation or employees.'))
    ids = data.get('ids') or []
    if isinstance(ids, (str, int)):
        ids = [ids]
    try:
        ids = sorted({int(x) for x in ids})
    except (TypeError, ValueError):
        raise Refused(tr('Choose who the weekend change is for: branch, department, category, designation or employees.'))
    if not ids:
        raise Refused(tr('Choose who the weekend change is for: branch, department, category, designation or employees.'))
    W = M('calendars', 'weekend_calendar').objects.filter(pk=data.get('weekend')).first()
    if W is None:
        raise Refused(tr('Choose the new weekend calendar.'))
    start = data.get('start_date')
    start = start if isinstance(start, date) else (parse_date(str(start)[:10]) if start else None)
    if start is None:
        raise Refused(tr('Enter the date the new weekend starts.'))
    return level, ids, W, start


# ------------------------------------------------------------------ the assignment change
def _apply_structure(level, ids, W, start, user=None):
    """Make the assignments say: from `start` the targets have W. Returns the parts [{assignment, old, new}] (old = calendar before
    the start date, None when the assignment is new and nothing at this level counted before)."""
    from calendars.workdays import load_changes
    AW = M('calendars', 'assign_weekend')
    changes = load_changes()
    parts = []
    for tid in ids:
        holders = [a for a in AW.objects.prefetch_related(level).order_by('pk')
                   if (not a.related_to or a.related_to == level) and tid in {x.pk for x in getattr(a, level).all()}]
        for a in holders:
            if any(c['from'] >= start for c in changes.get(a.pk, ())):
                raise Refused(tr('A weekend change from {0} is already planned for {1}.',
                                 max(c['from'] for c in changes[a.pk]).strftime('%d/%m/%Y'), ', '.join(target_names(level, [tid]))))
        own = [a for a in holders if {x.pk for x in getattr(a, level).all()} == {tid}]
        if own:
            a = own[-1]
            parts.append({'assignment': a.pk, 'old': a.weekend_model_id, 'new': W.pk})
            if a.weekend_model_id != W.pk:
                AW.objects.filter(pk=a.pk).update(weekend_model=W)
            for b in holders:
                if b.pk != a.pk:
                    getattr(b, level).remove(tid)
            continue
        old = holders[-1].weekend_model_id if holders else None
        for b in holders:
            getattr(b, level).remove(tid)          # the shared assignment keeps the other targets
        n = AW.objects.create(related_to=level, weekend_model=W, created_by=user if getattr(user, 'pk', None) else None)
        parts.append({'assignment': n.pk, 'old': old, 'new': W.pk})
        getattr(n, level).add(tid)
    return parts


def _record(level, ids, W, start, parts, user=None, leaves=None):
    o = _option()
    v = dict(o.value or {})
    rows = list(v.get('changes') or [])
    nid = max([int(r.get('id') or 0) for r in rows] + [0]) + 1
    from django.utils import timezone
    row = {'id': nid, 'level': level, 'ids': ids, 'names': target_names(level, ids), 'weekend': W.pk, 'weekend_code': W.calendar_code,
           'start_date': start.isoformat(), 'parts': parts, 'by': getattr(user, 'pk', None),
           'by_name': (getattr(user, 'get_full_name', lambda: '')() or getattr(user, 'username', '') or '') if user else '',
           'at': timezone.now().isoformat(timespec='seconds'), 'leaves': leaves or []}
    rows.append(row)
    v['changes'] = rows
    o.value = v
    o.save(update_fields=['value', 'changed_at'])
    return row


def _sync_emp_calendar(emps):
    """emp_master.emp_weekend_calendar = the calendar the one rule gives today (shown on the employee record)."""
    from calendars.workdays import WeekendRules
    rules = WeekendRules()
    E = M('EmpManagement', 'emp_master')
    for e in emps:
        m = rules.pick(e)[0]
        if m is not None and e.emp_weekend_calendar_id != m.pk:
            E.objects.filter(pk=e.pk).update(emp_weekend_calendar=m)


# ------------------------------------------------------------------ leaves
def _candidates(emps, start):
    LR = M('calendars', 'employee_leave_request')
    return list(LR.objects.filter(employee__in=emps, status__in=('approved', 'pending'), end_date__gte=start)
                .exclude(leave_type__unit='hours').select_related('employee', 'leave_type').order_by('start_date', 'pk'))


def _approved(r):
    return float(r.approved_days or r.number_of_days or 0)


def _new_approved(r, old_days, new_days):
    a = _approved(r)
    if a >= float(old_days or 0):         # fully approved: all the new days
        return float(new_days)
    return min(a, float(new_days))         # partly approved (v1.62.4 item 67): never more than approved before


def _paid(r):
    from .paid_period import closed_periods
    try:
        return bool(closed_periods(r.employee, r.start_date, r.end_date))
    except Exception:
        return False


def _counts(leaves):
    return {r.pk: float(r.calculate_leave_days()) for r in leaves}


def preview(data, user=None):
    """{'employees', 'leaves': [...], 'names', ...} – nothing is saved (the change is made inside a rollback to count)."""
    level, ids, W, start = _parse(data)
    emps = list(employees(level, ids))
    leaves = _candidates(emps, start)
    out = {}
    try:
        with transaction.atomic():
            parts = _apply_structure(level, ids, W, start, user)
            _record(level, ids, W, start, parts, user)
            out['new'] = _counts(leaves)
            raise _Rollback()
    except _Rollback:
        pass
    rows = []
    for r in leaves:
        old = float(r.number_of_days or 0)
        new = out['new'].get(r.pk, old)
        if abs(new - old) < 0.001:
            continue
        paid = _paid(r) if r.status == 'approved' else False
        bal = 0.0
        if r.status == 'approved':
            bal = round(_approved(r) - _new_approved(r, old, new), 2)    # > 0: days given back
        rows.append({'id': r.pk, 'document_number': r.document_number, 'employee_id': r.employee_id,
                     'employee': f'{r.employee.emp_first_name or ""} {r.employee.emp_last_name or ""}'.strip() + f' ({r.employee.emp_code})',
                     'leave_type': r.leave_type.name, 'start_date': r.start_date.isoformat(), 'end_date': r.end_date.isoformat(),
                     'status': r.status, 'old_days': old, 'new_days': new, 'balance_difference': bal, 'payroll_paid': paid,
                     'choice': 'keep' if paid else ('recalculate' if r.status == 'pending' else None),
                     'can_choose': r.status == 'approved' and not paid})
    from calendars.workdays import model_days, DAY_NAMES
    return {'level': level, 'ids': ids, 'names': target_names(level, ids), 'weekend': W.pk, 'weekend_code': W.calendar_code,
            'weekend_days': sorted(model_days(W), key=DAY_NAMES.index), 'start_date': start.isoformat(),
            'employees': len(emps), 'leaves': rows}


def _notify(r, old, new, start):
    try:
        from EmpManagement.utils import send_notification_email
        u = getattr(r.employee, 'users', None)
        if u is None:
            return
        send_notification_email(user=u, employee=r.employee, branch=r.employee.emp_branch_id, title=tr('Leave recalculated'),
                                notification_type='leave_request',
                                message=tr('Your leave {0} ({1} – {2}) was counted again after the weekend change from {3}: {4} → {5} days.',
                                           r.document_number, r.start_date.strftime('%d/%m/%Y'), r.end_date.strftime('%d/%m/%Y'),
                                           start.strftime('%d/%m/%Y'), f'{old:g}', f'{new:g}'))
    except Exception:
        __import__('logging').getLogger(__name__).exception('weekend change notification for leave %s', r.pk)


def apply(data, user=None):
    """Make the change and carry out the choices {leave id: 'recalculate' | 'keep'} (approved leaves without a choice: keep)."""
    from . import engine
    level, ids, W, start = _parse(data)
    choices = {}
    for k, v in (data.get('choices') or {}).items():
        try:
            choices[int(k)] = 'recalculate' if v == 'recalculate' else 'keep'
        except (TypeError, ValueError):
            continue
    pv = preview(data, user)
    emps = list(employees(level, ids))
    done = []
    with transaction.atomic():
        parts = _apply_structure(level, ids, W, start, user)
        LR = M('calendars', 'employee_leave_request')
        for row in pv['leaves']:
            r = LR.objects.select_related('employee', 'leave_type').get(pk=row['id'])
            old, new = row['old_days'], row['new_days']
            if r.status == 'pending':
                r._v1626_recount = True
                r.applied_days = new
                r.save()
                done.append(dict(row, result='recounted'))
                continue
            if row['payroll_paid']:
                done.append(dict(row, result='kept_paid'))
                continue
            if choices.get(r.pk) != 'recalculate':
                done.append(dict(row, result='kept'))
                continue
            a_old = _approved(r)
            a_new = _new_approved(r, old, new)
            diff = round(a_old - a_new, 2)
            note = tr('Weekend change from {0}: {1} counted again, {2} → {3} days', start.strftime('%d/%m/%Y'), r.document_number,
                      f'{old:g}', f'{new:g}')
            if diff:   # the ledger line first, with the balance (1.62.4 balance guard)
                engine.post(r.employee_id, r.leave_type_id, date.today(), 'adjustment', diff, ref=r, note=note,
                            user_id=getattr(user, 'pk', None), move_balance=True)
            r._v1626_recount = True
            r.applied_days = new
            r.approved_days = a_new
            r.save()
            r.refresh_from_db()
            M('calendars', 'AttendanceCalendar').objects.filter(employee=r.employee, date__range=(r.start_date, r.end_date), is_manual=False,
                                                              remarks__startswith=engine.TAG, leave_type=r.leave_type).delete()
            engine.sync_calendar(r, approved=True)
            try:
                engine.resync_year(r)
            except Exception:
                pass
            _notify(r, old, new, start)
            done.append(dict(row, result='recalculated', ledger_days=diff))
        row = _record(level, ids, W, start, parts, user, leaves=done)
        _sync_emp_calendar(emps)
        _resync_attendance(emps, start)
    return row


def _resync_attendance(emps, start):
    """The attendance calendar of the days from the start date that are already written (past days up to today)."""
    try:
        from calendars.utils import sync_attendance_calendar
        from calendars.workdays import _today
        end = _today()
        if start > end:
            return
        for e in emps:
            sync_attendance_calendar(e, start, end)
    except Exception:
        __import__('logging').getLogger(__name__).exception('attendance calendar after a weekend change')


# ------------------------------------------------------------------ API
def _can(request, codes):
    from .views import can
    return can(request, codes)


class WeekendChangeView(APIView):
    """GET: the changes made (newest first). POST {level, ids, weekend, start_date, action: 'preview' | 'apply', choices}."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request, READ):
            return Response({'detail': tr('You may not change weekends.')}, status=403)
        from calendars.workdays import model_days, DAY_NAMES
        from AccessControl.access import limit_by_branch
        O = lambda app, model, f: [{'id': i, 'name': n} for i, n in M(app, model).objects.order_by(f).values_list('id', f)]
        E = limit_by_branch(request, M('EmpManagement', 'emp_master').objects.filter(is_active=True), branch_path='emp_branch_id')
        opts = {'branch': O('OrganisationManager', 'brnch_mstr', 'branch_name'), 'department': O('OrganisationManager', 'dept_master', 'dept_name'),
                'category': O('OrganisationManager', 'ctgry_master', 'ctgry_title'),
                'designation': O('OrganisationManager', 'desgntn_master', 'desgntn_job_title'),
                'employee': [{'id': e.pk, 'name': f'{e.emp_code} – {e.emp_first_name or ""} {e.emp_last_name or ""}'.strip()}
                             for e in E.order_by('emp_code')],
                'weekend': [{'id': w.pk, 'name': f'{w.calendar_code} ({", ".join(tr(d) for d in sorted(model_days(w), key=DAY_NAMES.index)) or tr("no weekend")})'}
                            for w in M('calendars', 'weekend_calendar').objects.order_by('-year', 'calendar_code')]}
        return Response({'changes': list(reversed(history())), 'options': opts, 'can_change': _can(request, WRITE)})

    def post(self, request):
        if not _can(request, WRITE):
            return Response({'detail': tr('You may not change weekends.')}, status=403)
        data = request.data
        try:
            # v1.62.6 (item 22): only for employees of the user's own branches (a branch HR user cannot change another branch)
            from AccessControl.access import allowed_branches
            allowed = allowed_branches(request)
            if allowed is not None:
                level, ids, _W, _s = _parse(data)
                if level == 'branch':
                    outside = [i for i in ids if i not in allowed]
                else:
                    outside = list(employees(level, ids).exclude(emp_branch_id_id__in=allowed).values_list('pk', flat=True)[:1])
                if outside:
                    return Response({'detail': tr('You can only work with your branches.')}, status=403)
            if (data.get('action') or 'preview') == 'apply':
                return Response(apply(data, request.user))
            return Response(preview(data, request.user))
        except Refused as e:
            return Response({'detail': str(e)}, status=400)


class WorkingDaysView(APIView):
    """GET ?employee=<id>[&date=yyyy-mm-dd&days=14] – what decides the employee's working days (calendars.workdays.explain):
    the weekend rule with each level, shift pattern, roster / overrides and the next days. Without ?employee: the user's own."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from AccessControl.access import ctx, check_branch
        from calendars import workdays
        c = ctx(request)
        me = c.emp
        pk = request.query_params.get('employee')
        if not pk:
            if me is None:
                return Response({'detail': tr('No employee record is linked to your login.')}, status=404)
            emp = me
        else:
            E = M('EmpManagement', 'emp_master')
            emp = E.objects.filter(pk=pk).first() if str(pk).isdigit() else None
            if emp is None:
                return Response({'detail': tr('Employee not found.')}, status=404)
            if not (me is not None and me.pk == emp.pk):
                if not (c.admin or c.codes & {'view_emp_master', 'change_emp_master', 'view_assign_weekend', 'view_employeeshiftschedule'}):
                    return Response({'detail': tr('You may not see this employee.')}, status=403)
                check_branch(request, employee_id=emp.pk)
        d = parse_date(str(request.query_params.get('date') or '')[:10]) if request.query_params.get('date') else None
        try:
            days = max(1, min(int(request.query_params.get('days') or 14), 62))
        except ValueError:
            days = 14
        return Response(workdays.explain(emp, d, days))
