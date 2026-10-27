"""v1.62.0 people – Overtime and Late in / early out registers (Time › Attendance).

GET  api/overtime/?view=waiting|pending|approved|rejected|all&month=YYYY-MM&employee=&type=
        one row per overtime day (calendars.EmployeeOvertime): employee (name + code), date, OT hours (h:mm), type with its rate
        (Normal ×1.25 …), weighted hours, status (Waiting for approval / Approved / Rejected / Entered by HR / Paid in Oct 2026
        payroll), over the 2 h/day limit of UAE Labour Law Art. 19, who approved, what the signed-in user may do.
GET  api/overtime/stats/?month=   hours this month · waiting for me · approved not yet paid · days over 2 h
POST api/overtime/<id>/approve/   the reporting manager or HR (sets the row approved – payroll reads it as before)
POST api/overtime/<id>/reject/ {note}   keeps the row unapproved and records the rejection (OvertimeDecision)
GET  api/late-early/?view=waiting|pending|all&status=   late in / early out requests with names, type, stage, waiting for
POST api/late-early/<id>/approve|reject/ {note}   the current level (calendars LateinEarlyoutApproval)
How overtime hours, rates and pay are calculated is not changed here.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.apps import apps
from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from . import engine as E
from .access import c as ctx, hr_employee_ids, is_hr, team_ids
from .models import AttendanceDay, OvertimeDecision

M = apps.get_model
OT_LABEL = {'NORMAL': 'Normal day', 'WEEKEND': 'Rest day', 'HOLIDAY': 'Public holiday', 'NIGHT': 'Night'}
OT_HR = {'change_employeeovertime', 'add_employeeovertime'}
MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _person(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [(e.emp_first_name or '').strip(), (e.emp_last_name or '').strip()] if x)
    return f'{n} ({e.emp_code})' if n else (e.emp_code or '')


def hm(hours):
    m = int(round(float(hours or 0) * 60))
    return f'{m // 60}:{m % 60:02d}'


def _ot_hr(request):
    x = ctx(request)
    return x.admin or bool(x.codes & OT_HR) or is_hr(request)


def _month(g):
    m = g.get('month') or ''
    try:
        y, mo = [int(x) for x in m.split('-')[:2]]
        d0 = date(y, mo, 1)
    except Exception:
        d0 = date.today().replace(day=1)
    d1 = (d0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return d0, d1


def overtime_rows(request, g):
    OT = M('calendars', 'EmployeeOvertime')
    d0, d1 = _month(g)
    if g.get('from'):
        d0 = date.fromisoformat(g['from'])
    if g.get('to'):
        d1 = date.fromisoformat(g['to'])
    qs = OT.objects.filter(date__range=(d0, d1)).select_related('employee', 'approved_by')
    hr = _ot_hr(request)
    team = team_ids(request)
    x = ctx(request)
    if not x.admin:
        ids = hr_employee_ids(request) if hr else set()
        own = {x.emp.id} if x.emp else set()
        if hr and ids is None:
            pass
        else:
            qs = qs.filter(employee_id__in=(ids or set()) | team | own)
    if str(g.get('employee') or '').isdigit():
        qs = qs.filter(employee_id=int(g['employee']))
    if g.get('type'):
        qs = qs.filter(ot_type=g['type'])
    rows = list(qs.order_by('-date', 'employee__emp_code')[:3000])
    dec = {(d.employee_id, d.date, d.ot_type): d for d in OvertimeDecision.objects.filter(date__range=(d0, d1))}
    days = {(a['employee_id'], a['date']): a for a in AttendanceDay.objects.filter(date__range=(d0, d1), employee_id__in={r.employee_id for r in rows})
            .values('employee_id', 'date', 'ot_rate', 'night_minutes')}
    P = M('PayrollManagement', 'Payslip')
    paid = set()
    try:
        for s in P.objects.filter(employee_id__in={r.employee_id for r in rows}, payroll_run__year__in={d0.year, d1.year}).values(
                'employee_id', 'payroll_run__year', 'payroll_run__month'):
            paid.add((s['employee_id'], int(s['payroll_run__year']), int(s['payroll_run__month'])))
    except Exception:
        pass
    from HRActions.names import user_names
    names = user_names({r.approved_by_id for r in rows if r.approved_by_id} | {d.by_id for d in dec.values() if d.by_id})
    rules = {}
    out = []
    for o in rows:
        e = o.employee
        t = o.ot_type or 'NORMAL'
        dd = days.get((o.employee_id, o.date)) or {}
        rate = dd.get('ot_rate')
        if not rate or rate == 1:
            try:
                if e.pk not in rules:
                    rules[e.pk] = E.rule_for(e)
                rate = E._ot_rate(e, rules[e.pk], t, night=bool(dd.get('night_minutes')))
            except Exception:
                rate = None
        h = Decimal(str(o.hours or 0))
        d = dec.get((o.employee_id, o.date, o.ot_type or ''))
        rejected = bool(d is not None and not o.approved and Decimal(str(d.hours)) >= h)
        manual = o.source == 'MANUAL'
        approved = bool(o.approved or manual)
        in_pay = (o.employee_id, o.date.year, o.date.month) in paid
        if rejected:
            stk, stl = 'rejected', tr('Rejected')
        elif approved and in_pay:
            stk, stl = 'paid', tr('Paid in {0} {1} payroll', tr(MONTHS[o.date.month - 1]), o.date.year)
        elif manual:
            stk, stl = 'approved', tr('Entered by HR')
        elif approved:
            stk, stl = 'approved', tr('Approved')
        else:
            stk, stl = 'pending', tr('Waiting for approval')
        mine = stk == 'pending' and (hr or e.pk in team)
        out.append({
            'id': o.pk, 'employee_id': e.pk, 'employee_display': _person(e), 'date': o.date,
            'hours': float(h), 'hours_hm': hm(h), 'type': t, 'type_label': tr(OT_LABEL.get(t, t.capitalize())),
            'rate': float(rate) if rate else None,
            'type_rate': f"{tr(OT_LABEL.get(t, t.capitalize()))} ×{Decimal(str(rate)).normalize():f}" if rate else tr(OT_LABEL.get(t, t)),
            'weighted_hm': hm(h * Decimal(str(rate))) if rate else '',
            'source_label': tr('Attendance') if o.source == 'ATTENDANCE' else tr('Entered by HR'),
            'status': stk, 'status_label': stl, 'over_limit': h > 2,
            'approved_by': names.get(o.approved_by_id, '') if o.approved_by_id else '',
            'decided_by': names.get(d.by_id, '') if d is not None and d.by_id else '', 'note': d.note if d is not None else '',
            'can_act': bool(mine), 'can_reject': bool((hr or e.pk in team) and not manual and stk in ('pending', 'approved')),
        })
    view = g.get('view', 'all')
    if view == 'waiting':
        out = [r for r in out if r['can_act']]
    elif view in ('pending', 'approved', 'rejected', 'paid'):
        out = [r for r in out if r['status'] == view]
    q = (g.get('q') or g.get('search') or '').lower().strip()
    if q:
        out = [r for r in out if q in f"{r['employee_display']} {r['type_label']}".lower()]
    return out


class OvertimeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(overtime_rows(request, request.query_params))


class OvertimeStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = overtime_rows(request, request.query_params)
        return Response({
            'hours': hm(sum(r['hours'] for r in rows if r['status'] in ('approved', 'paid'))),
            'waiting': sum(1 for r in rows if r['can_act']),
            'unpaid': sum(1 for r in rows if r['status'] == 'approved'),
            'over_limit': sum(1 for r in rows if r['over_limit'] and r['status'] != 'rejected'),
        })


class OvertimeActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        OT = M('calendars', 'EmployeeOvertime')
        o = OT.objects.filter(pk=pk).select_related('employee').first()
        if o is None:
            return Response({'detail': tr('Overtime not found.')}, status=404)
        hr = _ot_hr(request)
        ids = hr_employee_ids(request) if hr else set()
        if not (ctx(request).admin or (hr and (ids is None or o.employee_id in ids)) or o.employee_id in team_ids(request)):
            return Response({'detail': tr('Only the reporting manager or HR can decide on this overtime.')}, status=403)
        if o.source == 'MANUAL':
            return Response({'detail': tr('Overtime entered by HR is already approved.')}, status=400)
        if action == 'approve':
            o.approved, o.approved_by = True, request.user
            o.save(update_fields=['approved', 'approved_by'])
            OvertimeDecision.objects.filter(employee_id=o.employee_id, date=o.date, ot_type=o.ot_type or '').delete()
            return Response({'detail': tr('Overtime approved.')})
        if action == 'reject':
            note = (request.data.get('note') or '').strip()
            if not note:
                return Response({'note': [tr('Say why the overtime is rejected.')]}, status=400)
            if o.approved:
                o.approved, o.approved_by = False, None
                o.save(update_fields=['approved', 'approved_by'])
            OvertimeDecision.objects.update_or_create(employee_id=o.employee_id, date=o.date, ot_type=o.ot_type or '',
                                                      defaults={'hours': o.hours, 'status': 'rejected', 'note': note[:255], 'by_id': request.user.pk})
            return Response({'detail': tr('Overtime rejected.')})
        return Response({'detail': tr('Unknown action.')}, status=400)


# ------------------------------------------------------------------ late in / early out requests
LE_LABEL = {'LATE_IN': 'Late in', 'EARLY_OUT': 'Early out'}


def _le_mine(a, user):
    return bool(a and a.status == 'PENDING' and (a.approver_id == user.pk or (a.is_deligate and a.deligate_to_id == user.pk)))


def late_early_rows(request, g):
    R = M('calendars', 'LateinEarlyoutRequest')
    A = M('calendars', 'LateinEarlyoutApproval')
    x = ctx(request)
    mine_ids = set(A.objects.filter(Q(approver=request.user) | Q(deligate_to=request.user, is_deligate=True)).values_list('lateinearlyout_request_id', flat=True))
    qs = R.objects.select_related('employee', 'branch')
    if not x.admin:
        if is_hr(request):
            ids = hr_employee_ids(request)
            if ids is not None:
                qs = qs.filter(Q(employee_id__in=ids) | Q(pk__in=mine_ids))
        else:
            qs = qs.filter(Q(employee__users=request.user) | Q(pk__in=mine_ids) | Q(employee_id__in=team_ids(request)))
    if g.get('status'):
        qs = qs.filter(status__in=[s.upper() for s in g['status'].split(',')])
    rows = list(qs.order_by('-date', '-id')[:2000])
    steps = {}
    for a in A.objects.filter(lateinearlyout_request_id__in=[r.pk for r in rows]).order_by('level', 'id'):
        steps.setdefault(a.lateinearlyout_request_id, []).append(a)
    from HRActions.names import user_names
    names = user_names({a.approver_id for v in steps.values() for a in v} | {a.deligate_to_id for v in steps.values() for a in v if a.deligate_to_id})
    day = {(d['employee_id'], d['date']): d for d in AttendanceDay.objects.filter(date__in={r.date for r in rows if r.date}, employee_id__in={r.employee_id for r in rows})
           .values('employee_id', 'date', 'late_minutes', 'early_minutes', 'first_in', 'last_out')}
    out = []
    for r in rows:
        st = steps.get(r.pk, [])
        cur = next((a for a in st if a.status == 'PENDING'), None)
        total = max([a.level for a in st] + [1])
        dd = day.get((r.employee_id, r.date)) or {}
        mins = dd.get('late_minutes') if r.request_type == 'LATE_IN' else dd.get('early_minutes')
        if r.status == 'PENDING':
            who = (names.get(cur.deligate_to_id) if cur and cur.is_deligate and cur.deligate_to_id else names.get(cur.approver_id, '')) if cur else ''
            stage = tr('Level {0} of {1} – waiting for {2}', cur.level, total, who) if cur else tr('Waiting for approval')
        else:
            who, stage = '', tr(r.get_status_display())
        out.append({
            'id': r.pk, 'number': r.document_number, 'employee_id': r.employee_id, 'employee_display': _person(r.employee), 'date': r.date,
            'type': r.request_type, 'type_label': tr(LE_LABEL.get(r.request_type, r.request_type)),
            'minutes_label': (tr('{0} min', mins) if mins else ''), 'reason': r.reason or '', 'branch': r.branch.branch_name if r.branch_id else '',
            'status': r.status.lower(), 'status_label': tr(r.get_status_display()), 'stage_label': stage, 'waiting_for': who,
            'can_act': bool(cur is not None and (_le_mine(cur, request.user) or getattr(request.user, 'is_superuser', False))),
            'waiting_for_me': bool(cur is not None and _le_mine(cur, request.user)),
            'history': [{'level': a.level, 'approver': names.get(a.approver_id, ''), 'status': tr(a.get_status_display()), 'note': a.note or '',
                         'date': timezone.localtime(a.updated_at).date() if a.updated_at else None} for a in st],
        })
    view = g.get('view', 'all')
    if view == 'waiting':
        out = [r for r in out if r['waiting_for_me']]
    elif view == 'pending':
        out = [r for r in out if r['status'] == 'pending']
    q = (g.get('q') or g.get('search') or '').lower().strip()
    if q:
        out = [r for r in out if q in f"{r['employee_display']} {r['number']} {r['type_label']}".lower()]
    return out


class LateEarlyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(late_early_rows(request, request.query_params))


class LateEarlyStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = late_early_rows(request, {})
        m0 = date.today().replace(day=1)
        return Response({'waiting': sum(1 for r in rows if r['waiting_for_me']), 'pending': sum(1 for r in rows if r['status'] == 'pending'),
                         'late_month': sum(1 for r in rows if r['type'] == 'LATE_IN' and r['date'] and r['date'] >= m0),
                         'early_month': sum(1 for r in rows if r['type'] == 'EARLY_OUT' and r['date'] and r['date'] >= m0)})


class LateEarlyActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        A = M('calendars', 'LateinEarlyoutApproval')
        a = A.objects.filter(lateinearlyout_request_id=pk, status='PENDING').order_by('level', 'id').first()
        if a is None:
            return Response({'detail': tr('Nothing is waiting for approval on this request.')}, status=400)
        if not (_le_mine(a, request.user) or getattr(request.user, 'is_superuser', False)):
            return Response({'detail': tr('This request is waiting for someone else.')}, status=403)
        note = (request.data.get('note') or '').strip() or None
        if action == 'approve':
            a.approve(note=note)
            return Response({'detail': tr('Approved.')})
        if action == 'reject':
            if not note:
                return Response({'note': [tr('Say why it is rejected.')]}, status=400)
            a.reject(note=note)
            return Response({'detail': tr('Rejected.')})
        return Response({'detail': tr('Unknown action.')}, status=400)
