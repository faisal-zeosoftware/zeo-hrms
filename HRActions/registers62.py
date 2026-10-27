"""v1.62.0 people – the Resignations register (People › Employees › Resignations / Resignation approvals).

GET  api/resignations/?view=waiting|open|all&status=&type=&q=   one row per resignation: employee (name + code + designation),
        type, submitted, last working day, notice (given / required, short notice flagged), stage ("Level 1 of 2 – waiting for
        Samuel Otieno" → "Approved – end of service to calculate" → the Leavers settlement stage), waiting for, approval history,
        the Leavers case link, what the signed-in user may do. `view=waiting` = waiting for me.
GET  api/resignations/stats/        tiles: waiting for me · in notice period · leaving this month · settlement open
POST api/resignations/              record a resignation (the EmpManagement resignation API does numbering and the approval chain)
POST api/resignations/<id>/approve/ {note}      the current level (the approver, the person it was delegated to, or an admin)
POST api/resignations/<id>/reject/  {note}
Statuses, numbering, the approval chain and its e-mails stay in EmpManagement (EmployeeResignation / ResignationApproval).
"""
from datetime import date, timedelta

from django.apps import apps
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import allowed_branches
from Translations.i18n import tr

from .names import user_names

M = apps.get_model
VIEW_CODES = ('view_employeeresignation', 'add_employeeresignation', 'change_employeeresignation', 'view_resignationapproval',
              'view_approved_resignations')
TYPES = {'resignation': 'Resignation', 'termination': 'Termination', 'retirement': 'Retirement', 'death_or_disablement': 'Death or disability'}


def _can(request, *codes):
    from Chatter.views import has_code
    return has_code(request, *codes)


def _person(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [(e.emp_first_name or '').strip(), (e.emp_last_name or '').strip()] if x)
    return f'{n} ({e.emp_code})' if n else (e.emp_code or '')


def _mine(a, user):
    return bool(a and a.status == 'Pending' and (a.approver_id == user.pk or (a.is_deligate and a.deligate_to_id == user.pk)))


def _settlement(res_ids):
    try:
        from PayrollPlus.leavers import STAGE_LABEL, _maps
        eos, sts = _maps(res_ids)
        out = {}
        for rid, ev in eos.items():
            st = sts.get(ev.pk)
            key = st.status if st is not None else 'to_calculate'
            out[rid] = (key, STAGE_LABEL.get(key, key))
        return out
    except Exception:
        return {}


def rows_for(request, view='all', status='', kind='', q=''):
    R = M('EmpManagement', 'EmployeeResignation')
    A = M('EmpManagement', 'ResignationApproval')
    user = request.user
    see_all = _can(request, *VIEW_CODES)
    mine_ids = set(A.objects.filter(Q(approver=user) | Q(deligate_to=user, is_deligate=True)).values_list('resignation_request_id', flat=True))
    qs = R.objects.select_related('employee', 'employee__emp_desgntn_id', 'employee__emp_dept_id', 'branch')
    if not see_all:
        qs = qs.filter(Q(pk__in=mine_ids) | Q(employee__users=user))
    br = allowed_branches(request)
    if br is not None and see_all:
        qs = qs.filter(Q(employee__emp_branch_id__in=br) | Q(pk__in=mine_ids))
    if status:
        qs = qs.filter(status__iexact=status)
    if kind:
        qs = qs.filter(termination_type=kind)
    res = list(qs.order_by('-document_date', '-id')[:1000])
    apps_ = {}
    for a in A.objects.filter(resignation_request_id__in=[r.pk for r in res]).order_by('level', 'id'):
        apps_.setdefault(a.resignation_request_id, []).append(a)
    uids = {a.approver_id for v in apps_.values() for a in v} | {a.deligate_to_id for v in apps_.values() for a in v if a.deligate_to_id}
    names = user_names(uids)
    sett = _settlement([r.pk for r in res if (r.status or '').lower() == 'approved'])
    levels_of = {}
    today = date.today()
    out = []
    for r in res:
        e = r.employee
        steps = apps_.get(r.pk, [])
        cur = next((a for a in steps if a.status == 'Pending'), None)
        st = (r.status or 'Pending').capitalize()
        total = max([a.level for a in steps] + [0])
        try:
            wf = M('EmpManagement', 'ResignationApprovalWorkflow').objects.filter(branch=e.emp_branch_id_id).first() if e.emp_branch_id_id else None
            if wf is not None:
                if wf.pk not in levels_of:
                    levels_of[wf.pk] = wf.resignation_levels.count()
                total = max(total, levels_of[wf.pk] if wf.approval_type == 'multi_approval' else (1 if wf.approval_type == 'reporting_manager' else 0))
        except Exception:
            pass
        waiting = ''
        if st == 'Pending' and cur is not None:
            who = names.get(cur.deligate_to_id) if cur.is_deligate and cur.deligate_to_id else names.get(cur.approver_id, '')
            waiting = who
            stage_key, stage = 'approval', tr('Level {0} of {1} – waiting for {2}', cur.level, max(total, cur.level), who or tr('the approver'))
        elif st == 'Pending':
            stage_key, stage = 'approval', tr('Waiting for approval')
        elif st == 'Rejected':
            stage_key, stage = 'rejected', tr('Rejected')
        else:
            sk, sl = sett.get(r.pk, ('to_calculate', 'To calculate'))
            stage_key = sk
            stage = tr('Approved – end of service to calculate') if sk == 'to_calculate' else tr('Final settlement: {0}', tr(sl))
        given = (r.last_working_date - r.resigned_on).days if r.last_working_date and r.resigned_on else None
        need = r.notice_period or 0
        in_notice = st == 'Approved' and r.last_working_date and r.resigned_on and r.resigned_on <= today <= r.last_working_date
        row = {
            'id': r.pk, 'number': r.document_number or '', 'employee_id': e.pk, 'employee_display': _person(e), 'employee_code': e.emp_code,
            'designation': str(e.emp_desgntn_id) if e.emp_desgntn_id_id else '', 'department': str(e.emp_dept_id) if e.emp_dept_id_id else '',
            'branch': r.branch.branch_name if r.branch_id else '',
            'type': r.termination_type, 'type_label': tr(TYPES.get(r.termination_type, (r.termination_type or '').replace('_', ' ').capitalize())),
            'submitted': r.document_date, 'resigned_on': r.resigned_on, 'last_working_date': r.last_working_date,
            'notice_required': need, 'notice_given': given,
            'notice_label': (tr('{0} of {1} days', given, need) if need else tr('{0} days', given)) if given is not None else '',
            'notice_short': bool(need and given is not None and given < need),
            'status': st.lower(), 'status_label': tr(st), 'stage': stage_key, 'stage_label': stage, 'waiting_for': waiting,
            'in_notice': bool(in_notice), 'reason': r.reason_for_leaving or '',
            'can_act': bool(cur is not None and (_mine(cur, request.user) or getattr(request.user, 'is_superuser', False))),
            'waiting_for_me': bool(cur is not None and _mine(cur, request.user)),
            'approval_id': cur.pk if cur is not None else None,
            'leaver_url': f'/main-sidebar/sub-sidebar/leavers/{r.pk}' if st == 'Approved' else '',
            'history': [{'level': a.level, 'approver': names.get(a.approver_id, ''), 'status': tr(a.status), 'note': a.note or '',
                         'delegated_to': names.get(a.deligate_to_id, '') if a.is_deligate else '', 'date': a.updated_at} for a in steps],
            'attachment': r.attachment.url if r.attachment else None,
        }
        out.append(row)
    if view == 'waiting':
        out = [x for x in out if x['waiting_for_me']]
    elif view == 'open':
        out = [x for x in out if x['stage'] not in ('rejected', 'paid', 'cancelled')]
    if q:
        ql = q.lower()
        out = [x for x in out if ql in f"{x['employee_display']} {x['number']} {x['designation']} {x['type_label']}".lower()]
    return out


class ResignationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        g = request.query_params
        return Response(rows_for(request, g.get('view', 'all'), g.get('status', ''), g.get('type', ''), g.get('q', '') or g.get('search', '')))

    def post(self, request):
        """Record a resignation through the EmpManagement API (numbering, approval chain, e-mails)."""
        if not _can(request, 'add_employeeresignation'):
            return Response({'detail': tr('You may not record resignations.')}, status=403)
        from EmpManagement.views import EmployeeResignationViewset
        E = M('EmpManagement', 'emp_master')
        data = {k: v for k, v in request.data.items()}
        emp = E.objects.filter(pk=data.get('employee') or 0).select_related('emp_branch_id').first()
        if emp is None:
            return Response({'employee': [tr('Choose the employee.')]}, status=400)
        data.setdefault('document_date', date.today().isoformat())
        if not data.get('document_date'):
            data['document_date'] = date.today().isoformat()
        if not data.get('branch'):
            data['branch'] = emp.emp_branch_id_id
        if not data.get('location'):
            data['location'] = emp.emp_branch_id.branch_name if emp.emp_branch_id_id else '-'
        if not data.get('termination_type'):
            data['termination_type'] = 'resignation'
        if not data.get('last_working_date') and data.get('resigned_on') and str(data.get('notice_period') or '').isdigit():
            from django.utils.dateparse import parse_date
            d0 = parse_date(str(data['resigned_on']))
            if d0:
                data['last_working_date'] = (d0 + timedelta(days=int(data['notice_period']))).isoformat()
        for k in [k for k, v in data.items() if v in ('', None)]:
            data.pop(k)
        v = EmployeeResignationViewset()
        v.request, v.args, v.kwargs, v.format_kwarg, v.action = request, (), {}, None, 'create'
        ser = v.get_serializer(data=data)
        ser.is_valid(raise_exception=True)
        v.perform_create(ser)
        return Response({'id': ser.instance.pk, 'detail': tr('Resignation {0} recorded.', ser.instance.document_number or '')}, status=201)


class ResignationStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = rows_for(request)
        today = date.today()
        m0 = today.replace(day=1)
        m1 = (m0 + timedelta(days=32)).replace(day=1)
        return Response({
            'waiting': sum(1 for r in rows if r['waiting_for_me']),
            'pending': sum(1 for r in rows if r['stage'] == 'approval'),
            'in_notice': sum(1 for r in rows if r['in_notice']),
            'leaving_month': sum(1 for r in rows if r['status'] == 'approved' and r['last_working_date'] and m0 <= r['last_working_date'] < m1),
            'settlement_open': sum(1 for r in rows if r['status'] == 'approved' and r['stage'] not in ('paid', 'cancelled')),
        })


class ResignationActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        A = M('EmpManagement', 'ResignationApproval')
        a = A.objects.filter(resignation_request_id=pk, status='Pending').order_by('level', 'id').first()
        if a is None:
            return Response({'detail': tr('Nothing is waiting for approval on this resignation.')}, status=400)
        if not (_mine(a, request.user) or getattr(request.user, 'is_superuser', False) or _can(request, 'change_resignationapproval')):
            return Response({'detail': tr('This resignation is waiting for someone else.')}, status=403)
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
