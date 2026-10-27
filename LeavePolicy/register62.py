"""v1.62.0 people – Leave requests register and the leave balance grid.

GET  api/requests/?view=waiting|on_leave|pending|all&status=&leave_type=&employee=&q=
        one row per leave request: employee (name + code), type, from–to, days, stage ("Level 1 of 2 – waiting for Ravi Pillai" /
        Approved / On leave / Returned – late 1 day / Rejected), the balance now and after approval, approval history, what the
        signed-in user may do. `view=waiting` = waiting for me (approver or delegate).
GET  api/requests/stats/                 on leave today · starting in the next 7 days · waiting for me · not back yet
POST api/requests/<id>/approve/ {note}   the current level (same checks as Leave approvals)
POST api/requests/<id>/reject/ {reason}
POST api/requests/<id>/cancel/ {reason}  cancel an approved leave and give the days back (same as Leave cancellation)
GET  api/balance-grid/?branch=&department=&q=   employees × leave types (by name) with the balance; a cell opens api/ledger/
Approval, deduction and restore of the balance stay in calendars (LeaveApproval.approve / reject, restore_leave_balance).
"""
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from .views import READ, _ctx, person

M = apps.get_model
CANCEL = {'add_lv_cancellation', 'delete_lv_cancellation'}


def _names(ids):
    from HRActions.names import user_names
    return user_names(ids)


def _mine(a, user):
    return bool(a and a.status == 'Pending' and (a.approver_id == user.pk or (a.is_deligate and a.deligate_to_id == user.pk)))


def _scope(request, qs, emp_field='employee'):
    """HR (leave read rights) sees its branches; others see their own requests and those waiting for them."""
    c = _ctx(request)
    LA = M('calendars', 'LeaveApproval')
    mine = set(LA.objects.filter(Q(approver=request.user) | Q(deligate_to=request.user, is_deligate=True))
               .exclude(leave_request__isnull=True).values_list('leave_request_id', flat=True))
    if c.admin:
        return qs, mine
    if c.codes & READ:
        if c.branches is not None:
            qs = qs.filter(Q(**{emp_field + '__emp_branch_id__in': c.branches}) | Q(pk__in=mine))
        return qs, mine
    own = Q(**{emp_field + '__users': request.user})
    return qs.filter(own | Q(pk__in=mine)), mine


def request_rows(request, g):
    LR = M('calendars', 'employee_leave_request')
    LA = M('calendars', 'LeaveApproval')
    RJ = M('calendars', 'EmployeeRejoining')
    BAL = M('calendars', 'emp_leave_balance')
    qs = LR.objects.select_related('employee', 'leave_type', 'employee__emp_dept_id')
    qs, mine = _scope(request, qs)
    if g.get('status'):
        qs = qs.filter(status__in=g['status'].split(','))
    if g.get('leave_type'):
        qs = qs.filter(leave_type__name=M('calendars', 'leave_type').objects.filter(pk=g['leave_type']).values_list('name', flat=True).first() or '')
    if str(g.get('employee') or '').isdigit():
        qs = qs.filter(employee_id=int(g['employee']))
    if g.get('from'):
        qs = qs.filter(end_date__gte=g['from'])
    if g.get('to'):
        qs = qs.filter(start_date__lte=g['to'])
    view = g.get('view', 'all')
    if view == 'waiting':
        qs = qs.filter(pk__in=mine, status='pending')
    lrs = list(qs.order_by('-start_date', '-id')[:1500])
    ids = [r.pk for r in lrs]
    steps = {}
    for a in LA.objects.filter(leave_request_id__in=ids).order_by('level', 'id'):
        steps.setdefault(a.leave_request_id, []).append(a)
    names = _names({a.approver_id for v in steps.values() for a in v} | {a.deligate_to_id for v in steps.values() for a in v if a.deligate_to_id})
    rj = {x.leave_request_id: x for x in RJ.objects.filter(leave_request_id__in=ids)}
    bal = {(b['employee_id'], b['leave_type_id']): b['balance'] for b in BAL.objects.filter(employee_id__in={r.employee_id for r in lrs})
           .values('employee_id', 'leave_type_id', 'balance')}
    today = date.today()
    can_cancel = _ctx(request).admin or bool(_ctx(request).codes & CANCEL)
    out = []
    for r in lrs:
        e = r.employee
        st = steps.get(r.pk, [])
        cur = next((a for a in st if a.status == 'Pending'), None)
        total = max([a.level for a in st] + [1])
        days = r.approved_days or r.applied_days or r.number_of_days
        b = bal.get((r.employee_id, r.leave_type_id))
        back = rj.get(r.pk)
        if r.status == 'pending':
            who = (names.get(cur.deligate_to_id) if cur and cur.is_deligate and cur.deligate_to_id else names.get(cur.approver_id, '')) if cur else ''
            stage_key = 'pending'
            stage = tr('Level {0} of {1} – waiting for {2}', cur.level, total, who) if cur else tr('Waiting for approval')
        elif r.status == 'rejected':
            stage_key, stage, who = 'rejected', tr('Rejected'), ''
        else:
            who = ''
            if back is not None:
                late = (back.rejoining_date - (r.end_date + timedelta(days=1))).days
                stage_key = 'returned'
                stage = tr('Back – on time') if late <= 0 else (tr('Back – late 1 day') if late == 1 else tr('Back – late {0} days', late))
            elif r.start_date <= today <= r.end_date:
                stage_key, stage = 'on_leave', tr('On leave')
            elif r.start_date > today:
                stage_key, stage = 'approved', tr('Approved – starts {0}', r.start_date.strftime('%d/%m/%Y'))
            else:
                stage_key, stage = 'not_back', tr('Approved – return not recorded')
        out.append({
            'id': r.pk, 'number': r.document_number, 'employee_id': e.pk, 'employee_display': person(e),
            'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
            'leave_type': r.leave_type.name if r.leave_type_id else '', 'leave_type_id': r.leave_type_id,
            'start_date': r.start_date, 'end_date': r.end_date,
            'period': (r.start_date.strftime('%d/%m/%Y') if r.start_date == r.end_date else f"{r.start_date.strftime('%d/%m/%Y')} – {r.end_date.strftime('%d/%m/%Y')}"),
            'days': days, 'half_day': bool(r.dis_half_day), 'applied_on': r.applied_on, 'reason': r.reason or '',
            'status': r.status, 'status_label': tr(r.get_status_display()), 'stage': stage_key, 'stage_label': stage, 'waiting_for': who,
            'balance_now': b, 'balance_after': (round(b - days, 2) if (b is not None and r.status == 'pending') else b),
            'can_act': bool(cur is not None and (_mine(cur, request.user) or getattr(request.user, 'is_superuser', False))),
            'waiting_for_me': bool(cur is not None and _mine(cur, request.user)),
            'can_cancel': bool(can_cancel and r.status == 'approved' and back is None),
            'history': [{'level': a.level, 'approver': names.get(a.approver_id, ''), 'status': tr(a.status),
                         'note': a.rejection_reason or a.note or '', 'date': a.updated_at} for a in st],
            'document': r.lv_document.url if r.lv_document else None,
        })
    if view == 'on_leave':
        out = [x for x in out if x['stage'] == 'on_leave']
    elif view == 'pending':
        out = [x for x in out if x['status'] == 'pending']
    q = (g.get('q') or g.get('search') or '').lower().strip()
    if q:
        out = [x for x in out if q in f"{x['employee_display']} {x['number']} {x['leave_type']} {x['department']}".lower()]
    return out


class LeaveRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(request_rows(request, request.query_params))


class LeaveRequestStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        today = date.today()
        rows = request_rows(request, {'from': (today - timedelta(days=45)).isoformat()})
        return Response({
            'on_leave': sum(1 for r in rows if r['stage'] == 'on_leave'),
            'starting': sum(1 for r in rows if r['status'] == 'approved' and today < r['start_date'] <= today + timedelta(days=7)),
            'waiting': sum(1 for r in rows if r['waiting_for_me']),
            'pending': sum(1 for r in rows if r['status'] == 'pending'),
            'not_back': sum(1 for r in rows if r['stage'] == 'not_back' and r['end_date'] >= today - timedelta(days=30)),
        })


class LeaveRequestActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        LR = M('calendars', 'employee_leave_request')
        LA = M('calendars', 'LeaveApproval')
        lr = LR.objects.filter(pk=pk).select_related('employee').first()
        if lr is None:
            return Response({'detail': tr('Leave request not found.')}, status=404)
        if action == 'cancel':
            return self._cancel(request, lr)
        a = LA.objects.filter(leave_request=lr, status='Pending').order_by('level', 'id').first()
        if a is None:
            return Response({'detail': tr('Nothing is waiting for approval on this leave.')}, status=400)
        if not (_mine(a, request.user) or getattr(request.user, 'is_superuser', False)):
            return Response({'detail': tr('This leave is waiting for someone else.')}, status=403)
        if action == 'approve':
            try:
                a.approve(note=(request.data.get('note') or None))
            except ValueError as e:
                return Response({'detail': str(e)}, status=400)
            return Response({'detail': tr('Approved.')})
        if action == 'reject':
            why = (request.data.get('reason') or request.data.get('rejection_reason') or '').strip()
            if not why:
                return Response({'reason': [tr('Say why it is rejected.')]}, status=400)
            a.reject(rejection_reason=why, note=request.data.get('note') or None)
            return Response({'detail': tr('Rejected.')})
        return Response({'detail': tr('Unknown action.')}, status=400)

    def _cancel(self, request, lr):
        """Same steps as Leave cancellation (calendars ImmediateRejectAPIView) for one approved leave."""
        c = _ctx(request)
        if not (c.admin or c.codes & CANCEL):
            return Response({'detail': tr('You may not cancel approved leave.')}, status=403)
        why = (request.data.get('reason') or '').strip()
        if not why:
            return Response({'reason': [tr('Say why the leave is cancelled.')]}, status=400)
        if lr.status != 'approved':
            return Response({'detail': tr('Only approved leave can be cancelled.')}, status=400)
        LA = M('calendars', 'LeaveApproval')
        a = LA.objects.filter(leave_request=lr, status='Approved').order_by('-level').first()
        if a is not None:
            a.status, a.rejection_reason, a.note = LA.REJECTED, why, (request.data.get('note') or a.note)
            a.save()
        lr.status = 'rejected'
        lr.save()
        lr.restore_leave_balance()
        return Response({'detail': tr('Leave {0} cancelled – the days were given back.', lr.document_number)})


class BalanceGridView(APIView):
    """Employees × leave types: the balance of each type (types of the same name in several branches are one column)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        c = _ctx(request)
        if not (c.admin or c.codes & READ):
            return Response({'detail': tr('You may not see leave balances.')}, status=403)
        g = request.query_params
        E = M('EmpManagement', 'emp_master')
        emps = E.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_branch_id', 'emp_desgntn_id')
        if c.branches is not None:
            emps = emps.filter(emp_branch_id__in=c.branches)
        if g.get('branch'):
            emps = emps.filter(emp_branch_id__in=[int(x) for x in g['branch'].split(',') if x.isdigit()])
        if g.get('department'):
            emps = emps.filter(emp_dept_id__in=[int(x) for x in g['department'].split(',') if x.isdigit()])
        q = (g.get('q') or '').strip()
        if q:
            emps = emps.filter(Q(emp_first_name__icontains=q) | Q(emp_last_name__icontains=q) | Q(emp_code__icontains=q))
        emps = list(emps.order_by('emp_code')[:2000])
        LT = M('calendars', 'leave_type')
        types = {t.pk: t for t in LT.objects.all()}
        BAL = M('calendars', 'emp_leave_balance')
        LR = M('calendars', 'employee_leave_request')
        pend = {}
        for r in LR.objects.filter(employee_id__in=[e.pk for e in emps], status='pending').values('employee_id', 'leave_type_id', 'applied_days', 'number_of_days'):
            k = (r['employee_id'], r['leave_type_id'])
            pend[k] = pend.get(k, 0) + (r['applied_days'] or r['number_of_days'] or 0)
        cells = {}
        cols = {}
        for b in BAL.objects.filter(employee_id__in=[e.pk for e in emps]).values('employee_id', 'leave_type_id', 'balance'):
            t = types.get(b['leave_type_id'])
            if t is None:
                continue
            emp_branch = next((e.emp_branch_id_id for e in emps if e.pk == b['employee_id']), None)
            # a type of another branch with nothing on it is the per-branch duplicate – left out
            if t.branch_id and emp_branch and t.branch_id != emp_branch and not b['balance']:
                continue
            name = t.name.strip()
            cols.setdefault(name, {'key': name, 'label': name, 'category': t.leave_category, 'unit': t.unit})
            cell = cells.setdefault(b['employee_id'], {}).setdefault(name, {'balance': 0.0, 'leave_type_id': t.pk, 'pending': 0})
            cell['balance'] = round(cell['balance'] + float(b['balance'] or 0), 2)
            cell['pending'] = round(cell['pending'] + pend.get((b['employee_id'], t.pk), 0), 2)
            if t.branch_id == emp_branch:
                cell['leave_type_id'] = t.pk
        order = {'annual': 0, 'sick': 1, 'casual': 2, 'compensatory': 3, 'maternity': 4, 'paternity': 5, 'bereavement': 6, 'unpaid': 7}
        for name, col in cols.items():   # how many employees have something on the type: the busy types first, empty ones folded away
            col['used'] = sum(1 for cc in cells.values() if name in cc and (cc[name]['balance'] or cc[name]['pending']))
            col['main'] = col['used'] > 0
        columns = sorted(cols.values(), key=lambda x: (order.get(x['category'], 9), -x['used'], x['label']))
        rows = [{'employee_id': e.pk, 'employee': person(e), 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                 'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '', 'branch': str(e.emp_branch_id) if e.emp_branch_id_id else '',
                 'cells': cells.get(e.pk, {})} for e in emps]
        return Response({'columns': columns, 'rows': rows})
