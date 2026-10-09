"""v1.11.0 – employee transfer and rejoining settlement APIs."""
from datetime import date

from django.apps import apps
from django.utils.dateparse import parse_date
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import rejoin as rj_svc
from . import transfer as tr_svc
from .models import EmployeeTransfer, RejoinSettlement

M = apps.get_model
TRANSFER = ('run_employee_transfer', 'add_employeetransfer', 'change_emp_master')
REJOIN = ('change_employeerejoining', 'add_employeerejoining', 'change_employee_leave_request')


def _can(request, codes):
    from Chatter.views import has_code
    return has_code(request, *codes)


def _branches_ok(request, *ids):
    from AccessControl.access import ctx
    c = ctx(request)
    if c.admin or c.branches is None:
        return True
    return all(i in c.branches for i in ids if i)


def _person(e):
    if e is None:
        return ''
    n = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x)
    return f'{n} ({e.emp_code})' if n else e.emp_code


def _names():
    g = lambda m, f: dict(M(*m.split('.')).objects.values_list('id', f))
    return {'b': g('OrganisationManager.brnch_mstr', 'branch_name'), 'd': g('OrganisationManager.dept_master', 'dept_name'),
            'g': g('OrganisationManager.desgntn_master', 'desgntn_job_title'), 'c': g('OrganisationManager.ctgry_master', 'ctgry_title'),
            'u': g('UserManagement.CustomUser', 'username')}


def _build(data, user):
    t = EmployeeTransfer(employee_id=int(data.get('employee') or 0), effective_date=parse_date(str(data.get('effective_date') or '')) or date.today(),
                         to_branch_id=data.get('to_branch') or None, to_department_id=data.get('to_department') or None,
                         to_designation_id=data.get('to_designation') or None, to_category_id=data.get('to_category') or None,
                         to_manager_id=data.get('to_manager') or None, salary_changes={k: v for k, v in (data.get('salary_changes') or {}).items() if v not in ('', None)},
                         reason=(data.get('reason') or '')[:255], created_by_id=getattr(user, 'pk', None))
    try:  # v1.12.0: optional to_location / to_division / to_section / to_cost_center / to_grade / to_job_position / to_employment_type
        if apps.is_installed('OrgStructure'):
            from OrgStructure.hooks import transfer_options
            t.options = {**(t.options or {}), **transfer_options(data)}
    except Exception:
        pass
    return t


class TransfersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request, TRANSFER + ('view_employeetransfer',)):
            return Response({'detail': 'You may not see transfers.'}, status=403)
        n = _names()
        E = M('EmpManagement', 'emp_master')
        emps = {e.pk: e for e in E.objects.filter(pk__in=EmployeeTransfer.objects.values('employee_id'))}
        rows = []
        for t in EmployeeTransfer.objects.all()[:500]:
            rows.append({'id': t.id, 'employee_id': t.employee_id, 'employee': _person(emps.get(t.employee_id)), 'effective_date': t.effective_date,
                         'from_branch': n['b'].get(t.from_branch_id, ''), 'to_branch': n['b'].get(t.to_branch_id, ''),
                         'from_department': n['d'].get(t.from_department_id, ''), 'to_department': n['d'].get(t.to_department_id, ''),
                         'to_designation': n['g'].get(t.to_designation_id, ''), 'to_category': n['c'].get(t.to_category_id, ''),
                         'to_manager': n['u'].get(t.to_manager_id, ''), 'status': t.get_status_display(), 'reason': t.reason,
                         'done': (t.result or {}).get('done', []), 'done_at': t.done_at, 'org': (t.options or {}).get('org') or {}})
        return Response(rows)

    def post(self, request):
        """Create a transfer: carried out now when the date has come, else scheduled."""
        if not _can(request, TRANSFER):
            return Response({'detail': 'You may not transfer employees.'}, status=403)
        t = _build(request.data, request.user)
        emp = M('EmpManagement', 'emp_master').objects.filter(pk=t.employee_id).first()
        if emp is None:
            return Response({'detail': 'Choose the employee.'}, status=400)
        if not _branches_ok(request, emp.emp_branch_id_id, t.to_branch_id):
            return Response({'detail': 'You may only transfer between branches you have access to.'}, status=403)
        p = tr_svc.plan(t)
        if p['blockers']:
            return Response({'detail': ' '.join(p['blockers']), 'plan': p}, status=400)
        t.from_branch_id, t.from_department_id = emp.emp_branch_id_id, emp.emp_dept_id_id
        t.save()
        if t.effective_date <= date.today():
            try:
                done = tr_svc.apply(t, request.user)
            except ValueError as e:
                return Response({'detail': str(e)}, status=400)
            return Response({'id': t.pk, 'status': 'done', 'done': done})
        return Response({'id': t.pk, 'status': 'scheduled', 'plan': p})


class TransferPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not _can(request, TRANSFER):
            return Response({'detail': 'You may not transfer employees.'}, status=403)
        return Response(tr_svc.plan(_build(request.data, request.user)))


class TransferActionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        if not _can(request, TRANSFER):
            return Response({'detail': 'You may not transfer employees.'}, status=403)
        t = EmployeeTransfer.objects.filter(pk=pk).first()
        if t is None:
            return Response({'detail': 'Transfer not found.'}, status=404)
        if t.status != 'scheduled':
            return Response({'detail': f'This transfer is {t.get_status_display().lower()}.'}, status=400)
        if action == 'cancel':
            t.status = 'cancelled'
            t.save(update_fields=['status'])
            return Response({'status': 'cancelled'})
        try:
            return Response({'status': 'done', 'done': tr_svc.apply(t, request.user)})
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)


# ------------------------------------------------------------------ rejoining
class RejoinsView(APIView):
    """GET ?state=open|settled|all – returns from leave with the gap; POST {leave_request, rejoin_date} – record a return."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not _can(request, REJOIN + ('view_employeerejoining',)):
            return Response({'detail': 'You may not see rejoinings.'}, status=403)
        from AccessControl.access import ctx
        c = ctx(request)
        RJ = M('calendars', 'EmployeeRejoining')
        qs = RJ.objects.select_related('employee', 'leave_request', 'leave_request__leave_type').order_by('-rejoining_date')
        if not c.admin and c.branches is not None:
            qs = qs.filter(employee__emp_branch_id__in=c.branches)
        settled = {s.rejoining_id: s for s in RejoinSettlement.objects.all()}
        state = request.query_params.get('state', 'open')
        rows = []
        for r in qs[:500]:
            s = settled.get(r.pk)
            if state == 'open' and s or state == 'settled' and not s:
                continue
            g = rj_svc.gap(r)
            rows.append({'id': r.pk, 'employee': _person(r.employee), 'employee_id': r.employee_id, 'leave': r.leave_request.document_number,
                         'leave_request_id': r.leave_request_id, 'leave_type': r.leave_request.leave_type.name, 'start': r.leave_request.start_date,
                         'end': r.leave_request.end_date, 'rejoin_date': r.rejoining_date, 'kind': g['kind'], 'days': g['days'],
                         'settled': bool(s), 'treatment': s.get_treatment_display() if s else '', 'unpaid': s.unpaid_days if s else None,
                         'from_leave': s.days_from_leave if s else None, 'absent': s.absent_days if s else None, 'excused': s.excused_days if s else None, 'returned': s.returned_days if s else None})
        return Response(rows)

    def post(self, request):
        if not _can(request, REJOIN):
            return Response({'detail': 'You may not record rejoinings.'}, status=403)
        LR = M('calendars', 'employee_leave_request')
        req = LR.objects.filter(pk=request.data.get('leave_request'), status='approved').select_related('employee').first()
        back = parse_date(str(request.data.get('rejoin_date') or ''))
        if req is None or back is None:
            return Response({'detail': 'Choose an approved leave and the date the employee is back.'}, status=400)
        if back <= req.start_date:
            return Response({'detail': 'The return date must be after the leave started.'}, status=400)
        RJ = M('calendars', 'EmployeeRejoining')
        r = RJ.objects.filter(leave_request=req).first()
        if r and RejoinSettlement.objects.filter(rejoining_id=r.pk).exists():
            return Response({'detail': 'This return is already settled.'}, status=400)
        if r:
            RJ.objects.filter(pk=r.pk).update(rejoining_date=back)
        else:
            r = RJ.objects.create(employee=req.employee, leave_request=req, rejoining_date=back, created_by=request.user)
        r.refresh_from_db()
        g = rj_svc.gap(r)
        return Response({'id': r.pk, 'kind': g['kind'], 'days': g['days']})


class RejoinSettleView(APIView):
    """POST /<id>/preview|settle {treatment, leave_type, note}"""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk, action):
        if not _can(request, REJOIN):
            return Response({'detail': 'You may not settle rejoinings.'}, status=403)
        r = M('calendars', 'EmployeeRejoining').objects.select_related('employee', 'leave_request', 'leave_request__leave_type').filter(pk=pk).first()
        if r is None:
            return Response({'detail': 'Rejoining not found.'}, status=404)
        tr, lt = request.data.get('treatment') or 'unpaid', request.data.get('leave_type') or None
        if action == 'preview':
            return Response(rj_svc.plan(r, tr, lt))
        try:
            s, p = rj_svc.settle(r, tr, lt, user=request.user, note=request.data.get('note') or '')
        except ValueError as e:
            return Response({'detail': str(e)}, status=400)
        except Exception as e:
            from django.core.exceptions import ValidationError
            if isinstance(e, ValidationError):
                return Response({'detail': ' '.join(e.messages)}, status=400)
            raise
        return Response({'ok': True, 'lines': p['lines'], 'requests': s.request_ids})
