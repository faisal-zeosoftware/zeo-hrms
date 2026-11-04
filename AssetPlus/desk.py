"""
v1.62.0 talent-ops – read-only summaries for the legacy asset request screens (Assets › Asset requests, Asset request approvals):
one row per request with the employee's name, the asset type, the stage and its approvers by name, and the allocation an
approved request produced. Saving stays in the existing /organisation/api/asset-request… endpoints (approve / reject included).

GET  api/desk/requests/?branch_id=                          requests + tiles
GET  api/desk/approvals/?box=waiting|open|decided&branch_id=  one row per request; the step this user can act on
"""
import json

from django.apps import apps
from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from Translations.i18n import tr

from .views import Base, _ctx

M = apps.get_model
READ = {'view_assetrequest', 'change_assetrequest', 'add_assetrequest', 'view_assetapproval', 'change_assetapproval'}


def emp_name(e):
    if e is None:
        return ''
    return ' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x).strip() or e.emp_code


def user_names(ids):
    E = M('EmpManagement', 'emp_master')
    U = M('UserManagement', 'CustomUser')
    by_user = {e.users_id: emp_name(e) for e in E.objects.filter(users_id__in=ids)}
    return {u.pk: by_user.get(u.pk) or u.username for u in U.objects.filter(pk__in=ids)}


def branch_ids(request):
    raw = request.query_params.get('branch_id') or ''
    try:
        v = json.loads(raw) if raw.startswith('[') else [x for x in raw.split(',') if x.strip()]
        return [int(x) for x in v]
    except (ValueError, TypeError):
        return []


def stage(r):
    return {'pending': 'in_approval', 'approved': 'approved', 'rejected': 'rejected'}.get(r.status, r.status)


def row(r, names, alloc, codes):
    e = r.employee
    steps = sorted(r.approvals.all(), key=lambda s: (s.level, s.id))
    waiting = next((s for s in steps if s.status == 'Pending'), None)
    rej = next((s for s in steps if s.status == 'Rejected'), None)
    a = alloc.get((r.employee_id, r.requested_asset_id)) if r.requested_asset_id else None
    asset = r.requested_asset
    code = codes.get(asset.pk, '') if asset else ''
    return {
        'id': r.pk, 'kind': 'asset', 'number': r.document_number or str(r.pk), 'employee_id': e.pk, 'employee': emp_name(e), 'code': e.emp_code,
        'branch': r.branch.branch_name if r.branch_id else (e.emp_branch_id.branch_name if e.emp_branch_id_id else ''),
        'type': r.asset_type.name if r.asset_type_id else '', 'note': r.reason or '',
        'asset': ({'id': asset.pk, 'name': getattr(asset, 'name', '') or str(asset), 'code': code or asset.serial_number or ''}
                  if asset else None),
        'allocation': {'id': a.pk, 'since': a.assigned_date, 'returned': a.returned_date} if a else None,
        'status': r.status, 'stage': stage(r), 'applied': timezone.localtime(r.request_date).date() if r.request_date else None,
        'progress': {'type': '', 'done': len({s.level for s in steps if s.status == 'Approved'}), 'total': max([s.level for s in steps] or [0]),
                     'waiting_for': names.get(waiting.deligate_to_id if waiting and waiting.is_deligate and waiting.deligate_to_id else
                                              (waiting.approver_id if waiting else None), ''),
                     'waiting_level': waiting.level if waiting else None, 'rejected_by': names.get(rej.approver_id, '') if rej else '',
                     'reason': (rej.note or '') if rej else ''},
        'steps': [{'level': s.level, 'role': s.role or '', 'who': names.get(s.approver_id, ''), 'status': s.status, 'note': s.note or '',
                   'reason': '', 'on': s.updated_at} for s in steps],
    }


def base_qs(request, c):
    AR = M('OrganisationManager', 'AssetRequest')
    qs = AR.objects.select_related('employee', 'employee__emp_branch_id', 'branch', 'asset_type', 'requested_asset').prefetch_related('approvals')
    if c.branches is not None and not c.admin:
        qs = qs.filter(Q(branch_id__in=c.branches) | Q(branch__isnull=True, employee__emp_branch_id__in=c.branches))
    b = branch_ids(request)
    if b:
        qs = qs.filter(Q(branch_id__in=b) | Q(branch__isnull=True, employee__emp_branch_id__in=b))
    return qs


def rows_for(reqs):
    ids = set()
    for r in reqs:
        for s in r.approvals.all():
            ids.update(x for x in (s.approver_id, s.deligate_to_id) if x)
    names = user_names(ids)
    AL = M('OrganisationManager', 'AssetAllocation')
    pairs = [(r.employee_id, r.requested_asset_id) for r in reqs if r.requested_asset_id]
    alloc = {}
    if pairs:
        for a in AL.objects.filter(employee_id__in={p[0] for p in pairs}, asset_id__in={p[1] for p in pairs}).order_by('assigned_date', 'id'):
            alloc[(a.employee_id, a.asset_id)] = a
    P = M('AssetPlus', 'AssetProfile')
    codes = dict(P.objects.filter(asset_id__in={r.requested_asset_id for r in reqs if r.requested_asset_id}).values_list('asset_id', 'asset_code'))
    return [row(r, names, alloc, codes) for r in reqs]


class DeskRequestsView(Base, APIView):
    def get(self, request):
        c = _ctx(request)
        if not (c.admin or READ & set(c.codes)):
            return Response({'detail': tr('You do not have permission to do this.')}, status=403)
        reqs = list(base_qs(request, c).order_by('-request_date', '-id')[:500])
        rows = rows_for(reqs)
        AP = M('OrganisationManager', 'AssetApproval')
        u = request.user
        summary = {'in_approval': len([r for r in rows if r['stage'] == 'in_approval']),
                   'approved': len([r for r in rows if r['stage'] == 'approved']),
                   'allocated': len([r for r in rows if r['allocation']]),
                   'rejected': len([r for r in rows if r['stage'] == 'rejected']),
                   'waiting_me': AP.objects.filter(Q(approver=u) | Q(deligate_to=u, is_deligate=True), status='Pending').count()}
        return Response({'rows': rows, 'summary': summary})


class DeskApprovalsView(Base, APIView):
    def get(self, request):
        c = _ctx(request)
        u = request.user
        box = request.query_params.get('box') or 'waiting'
        AP = M('OrganisationManager', 'AssetApproval')
        mine = Q(approver=u) | Q(deligate_to=u, is_deligate=True)
        hr = bool(c.admin or READ & set(c.codes))
        steps = AP.objects.all()
        if box == 'open':
            if not hr:
                return Response({'detail': tr('You do not have permission to do this.')}, status=403)
            steps = steps.filter(status='Pending')
        elif box == 'decided':
            steps = steps.exclude(status='Pending')
            if not c.admin:
                steps = steps.filter(mine)
        else:
            steps = steps.filter(mine, status='Pending')
        req_ids = list(dict.fromkeys(steps.order_by('-id').values_list('asset_request_id', flat=True)))
        qs = base_qs(request, c).filter(pk__in=req_ids)
        if box == 'open':
            qs = qs.filter(status='pending')
        reqs = list(qs)
        rows = rows_for(reqs)
        is_super = bool(getattr(u, 'is_superuser', False))
        by_id = {r.pk: r for r in reqs}
        for x in rows:
            st = sorted(by_id[x['id']].approvals.all(), key=lambda s: (s.level, s.id))
            pending = next((s for s in st if s.status == 'Pending'), None)
            my = next((s for s in st if s.status == 'Pending' and (s.approver_id == u.pk or (s.is_deligate and s.deligate_to_id == u.pk))), None)
            act = my or (pending if is_super else None)
            decided = [s for s in st if s.status != 'Pending' and (c.admin or s.approver_id == u.pk)]
            last = decided[-1] if decided else None
            x.update({'approval_id': (act or pending or last or (st[-1] if st else None)).id if st else None, 'can_act': bool(act), 'for_me': bool(my),
                      'level': (act or pending).level if (act or pending) else (last.level if last else None)})
        order = {rid: i for i, rid in enumerate(req_ids)}
        rows.sort(key=lambda x: order.get(x['id'], 0))
        AR = M('OrganisationManager', 'AssetRequest')
        return Response({'rows': rows, 'box': box, 'kind': 'asset',
                         'waiting': AP.objects.filter(mine, status='Pending').count(),
                         'open': AR.objects.filter(status='pending').count() if hr else None})
