"""v1.62.0 people – General requests and Document requests as one register each (People › Requests).

GET  api/requests/<kind>/?view=waiting|open|all&status=&q=     kind = general | document
        one row per request (not one per approval level): number, employee (name + code), type, amount (AED, only for money
        requests), submitted, stage ("Level 1 of 2"), waiting for (a name), status, approval history, what the user may do.
GET  api/requests/<kind>/stats/        waiting for me · waiting for approval · approved this month · rejected this month
POST api/requests/<kind>/              new request through the EmpManagement API (numbering, workflow, e-mails unchanged)
POST api/requests/<kind>/<id>/approve|reject/ {note}    the current level (the approver, the person it was delegated to, an admin)
The approvals ("General approvals", "Document approvals") are the same register with "Waiting for me".
"""
import re
from datetime import date

from django.apps import apps
from django.db.models import Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AccessControl.access import allowed_branches
from Translations.i18n import tr

from .names import user_names

M = apps.get_model
KINDS = {
    'general': {'model': 'GeneralRequest', 'approval': 'Approval', 'fk': 'general_request', 'rel': 'approvals',
                'view': ('view_generalrequest', 'add_generalrequest', 'change_generalrequest', 'view_approval'),
                'viewset': 'GeneralRequestViewset', 'type_attr': 'name'},
    'document': {'model': 'DocumentRequest', 'approval': 'DocumentApproval', 'fk': 'document_request', 'rel': 'doc_approvals',
                 'view': ('view_documentrequest', 'add_documentrequest', 'change_documentrequest', 'view_documentapproval'),
                 'viewset': 'DocumentRequestViewset', 'type_attr': 'type_name'},
}


def _can(request, *codes):
    from Chatter.views import has_code
    return has_code(request, *codes)


def _person(e):
    n = ' '.join(x for x in [(e.emp_first_name or '').strip(), (e.emp_last_name or '').strip()] if x)
    return f'{n} ({e.emp_code})' if n else (e.emp_code or '')


def _mine(a, user):
    return bool(a and a.status == 'Pending' and (a.approver_id == user.pk or (a.is_deligate and a.deligate_to_id == user.pk)))


def _cfg(kind):
    if kind not in KINDS:
        return None
    return KINDS[kind]


def rows_for(request, kind, g):
    c = KINDS[kind]
    R = M('EmpManagement', c['model'])
    A = M('EmpManagement', c['approval'])
    user = request.user
    mine_ids = set(A.objects.filter(Q(approver=user) | Q(deligate_to=user, is_deligate=True)).values_list(c['fk'] + '_id', flat=True))
    qs = R.objects.select_related('employee', 'request_type', 'branch')
    see_all = _can(request, *c['view'])
    if not see_all:
        qs = qs.filter(Q(pk__in=mine_ids) | Q(employee__users=user))
    else:
        br = allowed_branches(request)
        if br is not None:
            qs = qs.filter(Q(employee__emp_branch_id__in=br) | Q(branch_id__in=br) | Q(pk__in=mine_ids))
    if g.get('status'):
        qs = qs.filter(status__iexact=g['status'])
    if g.get('view') == 'waiting':
        qs = qs.filter(pk__in=mine_ids, status__iexact='pending')
    res = list(qs.order_by('-created_at_date', '-id')[:2000])
    steps = {}
    for a in A.objects.filter(**{c['fk'] + '_id__in': [r.pk for r in res]}).order_by('level', 'id'):
        steps.setdefault(getattr(a, c['fk'] + '_id'), []).append(a)
    names = user_names({a.approver_id for v in steps.values() for a in v} | {a.deligate_to_id for v in steps.values() for a in v if a.deligate_to_id})
    out = []
    for r in res:
        st = steps.get(r.pk, [])
        cur = next((a for a in st if a.status == 'Pending'), None)
        total = max([a.level for a in st] + [1])
        try:
            need = getattr(r.request_type, 'min_approvals_required', None) or 0
            total = max(total, need)
        except Exception:
            pass
        status = (r.status or 'Pending').capitalize()
        who = ''
        if status == 'Pending':
            if cur is not None:
                who = names.get(cur.deligate_to_id) if cur.is_deligate and cur.deligate_to_id else names.get(cur.approver_id, '')
                stage = tr('Level {0} of {1}', cur.level, total)
            else:
                stage = tr('Waiting for approval')
        else:
            stage = tr(status)
        money = kind == 'general' and bool(getattr(r.request_type, 'salary_component_id', None) or (r.total or 0))
        tname = getattr(r.request_type, c['type_attr'], '') if r.request_type_id else ''
        out.append({
            'id': r.pk, 'number': r.document_number or '', 'employee_id': r.employee_id, 'employee_display': _person(r.employee),
            'type': tname, 'reason': r.reason or '', 'remarks': r.remarks or '', 'branch': r.branch.branch_name if r.branch_id else '',
            'amount': float(r.total) if money and r.total else None, 'submitted': r.created_at_date,
            'status': status.lower(), 'status_label': tr(status), 'stage_short': stage, 'waiting_for': re.sub(r' \(\S+\)$', '', who or ''),
            'stage_label': (tr('Level {0} of {1} – waiting for {2}', cur.level, total, who) if (status == 'Pending' and cur is not None) else stage),
            'can_act': bool(cur is not None and (_mine(cur, user) or getattr(user, 'is_superuser', False))),
            'waiting_for_me': bool(cur is not None and _mine(cur, user)),
            'paid_in_payroll': bool(getattr(r, 'is_processed', False)),
            'history': [{'level': a.level, 'approver': names.get(a.approver_id, ''), 'status': tr(a.status), 'note': a.note or '',
                         'date': a.updated_at} for a in st],
        })
    if g.get('view') == 'open':
        out = [x for x in out if x['status'] == 'pending']
    q = (g.get('q') or g.get('search') or '').lower().strip()
    if q:
        out = [x for x in out if q in f"{x['employee_display']} {x['number']} {x['type']}".lower()]
    return out


class RequestsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kind):
        if not _cfg(kind):
            return Response({'detail': tr('Unknown list.')}, status=404)
        return Response(rows_for(request, kind, request.query_params))

    def post(self, request, kind):
        c = _cfg(kind)
        if not c:
            return Response({'detail': tr('Unknown list.')}, status=404)
        from EmpManagement import views as EV
        E = M('EmpManagement', 'emp_master')
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        emp = E.objects.filter(pk=data.get('employee') or 0).first()
        if emp is None:
            return Response({'employee': [tr('Choose the employee.')]}, status=400)
        if not data.get('branch'):
            data['branch'] = emp.emp_branch_id_id
        for k in [k for k in list(data.keys()) if data.get(k) in ('', None)]:
            data.pop(k)
        v = getattr(EV, c['viewset'])()
        v.request, v.args, v.kwargs, v.format_kwarg, v.action = request, (), {}, None, 'create'
        v.check_permissions(request)
        ser = v.get_serializer(data=data)
        ser.is_valid(raise_exception=True)
        v.perform_create(ser)
        return Response({'id': ser.instance.pk, 'detail': tr('Request {0} sent for approval.', ser.instance.document_number or '')}, status=201)


class RequestStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, kind):
        if not _cfg(kind):
            return Response({'detail': tr('Unknown list.')}, status=404)
        rows = rows_for(request, kind, {})
        from datetime import timedelta
        m0 = date.today() - timedelta(days=30)   # the last 30 days
        return Response({'waiting': sum(1 for r in rows if r['waiting_for_me']), 'pending': sum(1 for r in rows if r['status'] == 'pending'),
                         'approved_30': sum(1 for r in rows if r['status'] == 'approved' and r['submitted'] and r['submitted'] >= m0),
                         'rejected_30': sum(1 for r in rows if r['status'] == 'rejected' and r['submitted'] and r['submitted'] >= m0)})


class RequestActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, kind, pk, action):
        c = _cfg(kind)
        if not c:
            return Response({'detail': tr('Unknown list.')}, status=404)
        A = M('EmpManagement', c['approval'])
        a = A.objects.filter(**{c['fk'] + '_id': pk, 'status': 'Pending'}).order_by('level', 'id').first()
        if a is None:
            return Response({'detail': tr('Nothing is waiting for approval on this request.')}, status=400)
        if not (_mine(a, request.user) or getattr(request.user, 'is_superuser', False)):
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
