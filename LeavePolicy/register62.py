"""v1.62.0 people – Leave requests register and the leave balance grid.

GET  api/requests/?view=waiting|on_leave|pending|all&status=&leave_type=&employee=&q=
        one row per leave request: employee (name + code), type, from–to, days, stage ("Level 1 of 2 – waiting for Ravi Pillai" /
        Approved / On leave / Returned – late 1 day / Rejected), the balance now and after approval, approval history, what the
        signed-in user may do. `view=waiting` = waiting for me (approver or delegate).
GET  api/requests/stats/                 on leave today · starting in the next 7 days · waiting for me · not back yet
POST api/requests/<id>/approve/ {note}   the current level (same checks as Leave approvals)
POST api/requests/<id>/reject/ {reason}
POST api/requests/<id>/cancel/ {reason}  cancel an approved leave and give the days back (same as Leave cancellation) – v1.65.0 status Cancelled
POST api/requests/<id>/cancel_part/ {from_date, reason}   v1.65.0 (item 83): give back the days from a date (return early)
POST api/requests/<id>/extend/ {end_date, reason}         v1.65.0 (item 84): a linked extension request through the same approval
GET  api/balance-grid/?branch=&department=&q=   employees × leave types (by name) with the balance; a cell opens api/ledger/
Approval, deduction and restore of the balance stay in calendars (LeaveApproval.approve / reject, restore_leave_balance).
"""
import re
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
    from .models import LeaveRequestInfo   # v1.65.0 (items 82–85)
    infos = {i.request_id: i for i in LeaveRequestInfo.objects.filter(request_id__in=ids)}
    ext_of = {}
    for i in infos.values():
        if i.extension_of_id:
            ext_of.setdefault(i.extension_of_id, []).append(i.request_id)
    for i in LeaveRequestInfo.objects.filter(extension_of_id__in=ids).exclude(request_id__in=ids):
        ext_of.setdefault(i.extension_of_id, []).append(i.request_id)
    nums = dict(LR.objects.filter(pk__in={i.extension_of_id for i in infos.values() if i.extension_of_id} | {x for v in ext_of.values() for x in v})
                .values_list('pk', 'document_number'))
    me = getattr(_ctx(request).emp, 'pk', None)
    out = []
    for r in lrs:
        e = r.employee
        st = steps.get(r.pk, [])
        cur = next((a for a in st if a.status == 'Pending'), None)
        total = max([a.level for a in st] + [1])
        days = r.approved_days or r.applied_days or r.number_of_days
        b = bal.get((r.employee_id, r.leave_type_id))
        back = rj.get(r.pk)
        short = None
        if r.status == 'pending':
            who = (names.get(cur.deligate_to_id) if cur and cur.is_deligate and cur.deligate_to_id else names.get(cur.approver_id, '')) if cur else ''
            stage_key = 'pending'
            stage = tr('Level {0} of {1} – waiting for {2}', cur.level, total, who) if cur else tr('Waiting for approval')
            short = tr('Level {0} of {1}', cur.level, total) if cur else tr('Waiting for approval')
            if cur and who:   # v1.62.0 people: who it waits for in the stage chip (the list has no room for its own column)
                short = f"{short} · {re.sub(r' \(\S+\)$', '', who)}"
        elif r.status == 'rejected':
            stage_key, stage, who = 'rejected', tr('Rejected'), ''
        elif r.status == 'cancelled':   # v1.65.0 (item 83): an approved leave cancelled – its own status (was "Rejected")
            stage_key, stage, who = 'cancelled', tr('Cancelled'), ''
        else:
            who = ''
            if getattr(r.leave_type, 'unit', '') == 'hours':   # v1.62.4 (item 81): short leave in hours – no return to record
                stage_key, stage = 'approved', tr('Approved')
            elif back is not None:
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
            'unit': getattr(r.leave_type, 'unit', None) or 'days',   # v1.62.4 (item 81): short leave: days = hours
            'status': r.status, 'status_label': tr(r.get_status_display()), 'stage': stage_key, 'stage_label': stage, 'waiting_for': re.sub(r' \(\S+\)$', '', who or ''),   # v1.62.0 people: name only in the list
           
            'stage_short': short or stage,   # v1.62.0 people: the list column (who it waits for is its own column)
            'balance_now': b, 'balance_after': (round(b - days, 2) if (b is not None and r.status == 'pending') else b),
            'can_act': bool(cur is not None and (_mine(cur, request.user) or getattr(request.user, 'is_superuser', False))),
            'waiting_for_me': bool(cur is not None and _mine(cur, request.user)),
            'can_cancel': bool(can_cancel and r.status == 'approved' and back is None),
            'history': [{'level': a.level, 'approver': names.get(a.approver_id, ''), 'status': tr(a.status),
                         'note': a.rejection_reason or a.note or '', 'date': a.updated_at} for a in st],
            'document': r.lv_document.url if r.lv_document else None,
            # v1.65.0 (items 82–85): birth date, days given back, extension links, leave salary in advance
            'birth_date': getattr(infos.get(r.pk), 'birth_date', None),
            'cancelled_days': getattr(infos.get(r.pk), 'cancelled_days', 0) or 0,
            'original_end_date': getattr(infos.get(r.pk), 'original_end_date', None),
            'extension_of': nums.get(getattr(infos.get(r.pk), 'extension_of_id', None)) or '',
            'extended_by': ', '.join(nums.get(x, '') for x in ext_of.get(r.pk, [])),
            'advance': _adv_label(r, infos.get(r.pk)),
            'can_cancel_part': bool(can_cancel and r.status == 'approved' and back is None and getattr(r.leave_type, 'unit', '') != 'hours'
                                    and r.end_date > r.start_date),
            'can_extend': bool(r.status == 'approved' and back is None and getattr(r.leave_type, 'unit', '') != 'hours'
                               and (can_cancel or _ctx(request).admin or (me is not None and r.employee_id == me) or bool(_ctx(request).codes & READ))),
        })
    if view == 'on_leave':
        out = [x for x in out if x['stage'] == 'on_leave']
    elif view == 'pending':
        out = [x for x in out if x['status'] == 'pending']
    q = (g.get('q') or g.get('search') or '').lower().strip()
    if q:
        out = [x for x in out if q in f"{x['employee_display']} {x['number']} {x['leave_type']} {x['department']}".lower()]
    return out


def _adv_label(r, i):
    """v1.65.0 (item 85): the leave salary paid in advance, in words."""
    a = (i.advance or {}) if i else {}
    if not a or a.get('status') in (None, 'none'):
        return ''
    if a.get('status') == 'same_payroll':
        return tr('Paid with the normal payroll (the leave starts this month)')
    try:
        from .leave_rules import advance_state
        st = (advance_state(r) or {}).get('state')
    except Exception:
        st = None
    words = {'waiting': tr('waiting for the next payroll'), 'paid': tr('paid'), 'settled': tr('paid, taken back from the leave month'),
             'cancelled': tr('cancelled')}
    return tr('Leave salary in advance {0} – {1}', a.get('amount'), words.get(st, st or ''))


class LeaveRequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(request_rows(request, request.query_params))

    def post(self, request):
        """New leave request through the calendars leave request API (rights, numbering, checks, approval chain unchanged)."""
        from calendars.views import LeaveRequestviewset
        E = M('EmpManagement', 'emp_master')
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        emp = E.objects.filter(pk=data.get('employee') or 0).first()
        if emp is None and _ctx(request).emp is not None:
            emp = _ctx(request).emp
            data['employee'] = emp.pk
        if emp is None:
            return Response({'employee': [tr('Choose the employee.')]}, status=400)
        if not data.get('branch'):
            data['branch'] = emp.emp_branch_id_id
        for k in [k for k in list(data.keys()) if data.get(k) in ('', None)]:
            data.pop(k)
        v = LeaveRequestviewset()
        v.request, v.args, v.kwargs, v.format_kwarg, v.action = request, (), {}, None, 'create'
        v.check_permissions(request)
        data.pop('confirm_paid_period', None)   # v1.62.5 (item 19): not a field – read by paid_period.deciding
        ser = v.get_serializer(data=data)
        ser.is_valid(raise_exception=True)
        from .paid_period import deciding
        with deciding(request):   # v1.62.5 (item 19): HR's answer to "salary already paid" (confirm_paid_period=next_salary)
            v.perform_create(ser)
        return Response({'id': ser.instance.pk, 'detail': tr('Leave request {0} sent for approval.', ser.instance.document_number)}, status=201)


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
        from django.db import transaction
        from .paid_period import deciding
        if action == 'cancel':
            with deciding(request), transaction.atomic():   # v1.62.5 (item 19): salary of the days already paid – nothing half done
                return self._cancel(request, lr)
        if action in ('cancel_part', 'extend'):   # v1.65.0 (items 83, 84)
            return self._v1650(request, lr, action)
        a = LA.objects.filter(leave_request=lr, status='Pending').order_by('level', 'id').first()
        if a is None:
            return Response({'detail': tr('Nothing is waiting for approval on this leave.')}, status=400)
        if not (_mine(a, request.user) or getattr(request.user, 'is_superuser', False)):
            return Response({'detail': tr('This leave is waiting for someone else.')}, status=403)
        if action == 'approve':
            try:
                with deciding(request):   # v1.62.5 (item 19)
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
        lr.status = 'cancelled'   # v1.65.0 (item 83): its own status (was 'rejected', shown as Rejected)
        lr.save()
        lr.restore_leave_balance()
        return Response({'detail': tr('Leave {0} cancelled – the days were given back.', lr.document_number)})

    def _v1650(self, request, lr, action):
        """v1.65.0 (item 83) give back the days from a date / (item 84) a linked extension request."""
        from django.utils.dateparse import parse_date
        from . import leave_rules as LRL
        from .paid_period import deciding
        c = _ctx(request)
        try:
            if action == 'cancel_part':
                if not (c.admin or c.codes & CANCEL):
                    return Response({'detail': tr('You may not cancel approved leave.')}, status=403)
                d = parse_date(str(request.data.get('from_date') or ''))
                why = (request.data.get('reason') or '').strip()
                with deciding(request):
                    res = LRL.cancel_part(lr, d, why, request.user)
                return Response(dict(res, detail=tr('{0:g} day(s) of {1} given back; the leave now ends on {2:%d/%m/%Y}.',
                                                    res['given_back'], lr.document_number, lr.end_date)))
            own = c.emp is not None and c.emp.pk == lr.employee_id
            if not (own or c.admin or c.codes & (CANCEL | READ)):
                return Response({'detail': tr('You may not extend this leave.')}, status=403)
            d = parse_date(str(request.data.get('end_date') or ''))
            x = LRL.extend(lr, d, (request.data.get('reason') or '').strip(), request)
            return Response({'id': x.pk, 'number': x.document_number, 'status': x.status,
                             'detail': tr('Extension {0} of {1} sent for approval ({2:%d/%m/%Y} – {3:%d/%m/%Y}).', x.document_number,
                                          lr.document_number, x.start_date, x.end_date)}, status=201)
        except LRL.RuleError as e:
            return Response({'detail': e.text}, status=e.status)


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
        rows = [{'employee_id': e.pk, 'employee': person(e), 'code': e.emp_code, 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
                 'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '', 'branch': str(e.emp_branch_id) if e.emp_branch_id_id else '',
                 'cells': cells.get(e.pk, {})} for e in emps]
        from .views import RUN
        can_adjust = c.admin or bool(c.codes & (RUN | {'add_emp_leave_balance', 'change_emp_leave_balance', 'import_emp_leave_balance'}))
        return Response({'columns': columns, 'rows': rows, 'can_adjust': can_adjust})
