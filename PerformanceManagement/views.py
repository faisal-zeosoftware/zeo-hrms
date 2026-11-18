from zeo.tz import today as _zt_today   # v1.73.16 the user's / company's day, not the server's UTC day
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from zeo.module_helpers import (EmployeeScopedQuerysetMixin, TenantModelPermission, is_tenant_admin,
                                user_has_codename)
from . import services
from .models import (KPI, AppraisalCycle, AppraisalOutcome, AppraisalTemplate, CalibrationLog, CheckIn, Goal,
                     GoalSheet, IncrementBand, PerformanceImprovementPlan, PIPReview)
from .serializer import (AppraisalCycleSerializer, AppraisalOutcomeSerializer, AppraisalTemplateSerializer,
                         CalibrationLogSerializer, CheckInSerializer, GoalSerializer, GoalSheetSerializer,
                         IncrementBandSerializer, KPISerializer, PerformanceImprovementPlanSerializer,
                         PIPReviewSerializer)


def run(fn, *args, **kwargs):
    """Turn Django ValidationErrors from services into DRF 400 responses."""
    try:
        return fn(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError({'detail': exc.messages})


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantModelPermission]


def _is_owner(user, sheet):
    return sheet.employee.users_id == getattr(user, 'id', None)


def _is_manager(user, sheet):
    return sheet.manager_id == getattr(user, 'id', None) or is_tenant_admin(user) or user_has_codename(user, 'change_goalsheet')


class KPIViewSet(BaseViewSet):
    queryset = KPI.objects.select_related('department', 'designation').all()
    serializer_class = KPISerializer
    self_service_actions = {'list', 'retrieve'}

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('kpi_type'):
            qs = qs.filter(kpi_type=p['kpi_type'])
        if p.get('department'):
            qs = qs.filter(department_id=p['department'])
        if p.get('active') in ('1', 'true'):
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


class AppraisalTemplateViewSet(BaseViewSet):
    queryset = AppraisalTemplate.objects.all()
    serializer_class = AppraisalTemplateSerializer
    self_service_actions = {'list', 'retrieve'}


class AppraisalCycleViewSet(BaseViewSet):
    queryset = AppraisalCycle.objects.select_related('template').prefetch_related('branches', 'departments', 'increment_bands')
    serializer_class = AppraisalCycleSerializer
    action_verbs = {'launch': 'change', 'lock_ratings': 'change', 'generate_outcomes': 'change', 'close': 'change', 'distribution': 'view'}
    self_service_actions = {'list', 'retrieve'}

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.status != 'draft':
            raise ValidationError({'detail': 'Only draft cycles can be deleted.'})
        instance.delete()

    @action(detail=True, methods=['post'])
    def launch(self, request, pk=None):
        cycle = self.get_object()
        n = run(services.launch_cycle, cycle, request.user)
        return Response({'detail': f'Cycle launched. {n} goal sheet(s) created.', 'created': n})

    @action(detail=True, methods=['get'])
    def distribution(self, request, pk=None):
        return Response(services.distribution(self.get_object()))

    @action(detail=True, methods=['post'])
    def lock_ratings(self, request, pk=None):
        run(services.lock_ratings, self.get_object())
        return Response({'detail': 'Ratings locked.'})

    @action(detail=True, methods=['post'])
    def generate_outcomes(self, request, pk=None):
        n = run(services.generate_outcomes, self.get_object())
        return Response({'detail': f'Outcomes generated ({n} new).', 'created': n})

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        cycle = self.get_object()
        cycle.status = 'closed'
        cycle.save(update_fields=['status'])
        return Response({'detail': 'Cycle closed.'})


class IncrementBandViewSet(BaseViewSet):
    queryset = IncrementBand.objects.select_related('cycle').all()
    serializer_class = IncrementBandSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        cycle = self.request.query_params.get('cycle')
        return qs.filter(cycle_id=cycle) if cycle else qs


class GoalSheetViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = GoalSheet.objects.select_related('cycle', 'cycle__template', 'employee', 'employee__emp_dept_id',
                                                 'employee__emp_desgntn_id', 'manager').prefetch_related('goals')
    serializer_class = GoalSheetSerializer
    employee_lookup = 'employee'
    manager_lookup = 'manager'
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    action_verbs = {'submit_goals': 'change', 'approve_goals': 'change', 'return_form': 'change', 'submit_self': 'change',
                    'submit_review': 'change', 'calibrate': 'change', 'acknowledge': 'change'}
    self_service_actions = {'list', 'retrieve', 'partial_update', 'submit_goals', 'approve_goals', 'return_form',
                            'submit_self', 'submit_review', 'acknowledge'}

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('cycle'):
            qs = qs.filter(cycle_id=p['cycle'])
        if p.get('status'):
            qs = qs.filter(status__in=p['status'].split(','))
        if p.get('employee'):
            qs = qs.filter(employee_id=p['employee'])
        if p.get('mine') in ('1', 'true'):
            qs = qs.filter(employee__users=self.request.user)
        if p.get('team') in ('1', 'true'):
            qs = qs.filter(manager=self.request.user)
        return qs

    def perform_update(self, serializer):
        sheet = serializer.instance
        user = self.request.user
        if not (_is_owner(user, sheet) or _is_manager(user, sheet)):
            raise PermissionDenied("You can only edit your own or your team's goal sheet.")
        if not _is_manager(user, sheet) and set(serializer.validated_data) - {'key_achievements', 'development_needs', 'disagreement_note', 'employee_disagrees'}:
            raise PermissionDenied("Only the manager or HR can change these fields.")
        serializer.save()

    def _guard(self, sheet, who):
        user = self.request.user
        if who == 'owner' and not (_is_owner(user, sheet) or is_tenant_admin(user)):
            raise PermissionDenied("Only the employee can do this.")
        if who == 'manager' and not _is_manager(user, sheet):
            raise PermissionDenied("Only the reporting manager or HR can do this.")

    def _done(self, sheet, msg):
        return Response({'detail': msg, 'sheet': GoalSheetSerializer(sheet, context={'request': self.request}).data})

    @action(detail=True, methods=['post'])
    def submit_goals(self, request, pk=None):
        sheet = self.get_object(); self._guard(sheet, 'owner')
        run(services.submit_goals, sheet)
        return self._done(sheet, 'Goals submitted to your manager.')

    @action(detail=True, methods=['post'])
    def approve_goals(self, request, pk=None):
        sheet = self.get_object(); self._guard(sheet, 'manager')
        run(services.approve_goals, sheet)
        return self._done(sheet, 'Goals approved and locked.')

    @action(detail=True, methods=['post'])
    def return_form(self, request, pk=None):
        sheet = self.get_object(); self._guard(sheet, 'manager')
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({'reason': 'A reason is required.'})
        run(services.return_goals, sheet, reason)
        return self._done(sheet, 'Sent back to the employee.')

    @action(detail=True, methods=['post'])
    def submit_self(self, request, pk=None):
        sheet = self.get_object(); self._guard(sheet, 'owner')
        run(services.submit_self, sheet)
        return self._done(sheet, 'Self appraisal submitted.')

    @action(detail=True, methods=['post'])
    def submit_review(self, request, pk=None):
        sheet = self.get_object(); self._guard(sheet, 'manager')
        for key in ('manager_comments', 'development_needs'):
            if key in request.data:
                setattr(sheet, key, request.data.get(key))
        run(services.submit_review, sheet, request.user)
        return self._done(sheet, 'Manager review submitted.')

    @action(detail=True, methods=['post'])
    def calibrate(self, request, pk=None):
        sheet = self.get_object()
        if not (is_tenant_admin(request.user) or user_has_codename(request.user, 'add_calibrationlog')):
            raise PermissionDenied("Only HR can calibrate ratings.")
        run(services.calibrate, sheet, request.data.get('new_rating'), request.data.get('reason'), request.user)
        return self._done(sheet, 'Rating calibrated.')

    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        from django.utils import timezone
        sheet = self.get_object(); self._guard(sheet, 'owner')
        if sheet.status != 'calibrated':
            raise ValidationError({'detail': 'You can acknowledge once ratings are final.'})
        sheet.status = 'acknowledged'
        sheet.acknowledged_on = timezone.now()
        sheet.employee_disagrees = bool(request.data.get('disagree'))
        sheet.disagreement_note = request.data.get('note') or sheet.disagreement_note
        sheet.save()
        return self._done(sheet, 'Acknowledged.')


class GoalViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = Goal.objects.select_related('sheet', 'sheet__employee', 'kpi').all()
    serializer_class = GoalSerializer
    employee_lookup = 'sheet__employee'
    manager_lookup = 'sheet__manager'
    self_service_actions = {'list', 'retrieve', 'create', 'update', 'partial_update', 'destroy'}
    EMPLOYEE_DRAFT_FIELDS = {'sheet', 'kpi', 'title', 'goal_type', 'weight', 'target', 'due_date'}
    EMPLOYEE_PROGRESS_FIELDS = {'progress_percent', 'progress_status', 'self_rating', 'self_comment', 'evidence'}
    MANAGER_FIELDS = {'manager_rating', 'manager_comment', 'progress_percent', 'progress_status', 'revision_note',
                      'title', 'weight', 'target', 'due_date', 'kpi', 'goal_type'}

    def get_queryset(self):
        qs = super().get_queryset()
        sheet = self.request.query_params.get('sheet')
        return qs.filter(sheet_id=sheet) if sheet else qs

    def _check(self, sheet, fields):
        user = self.request.user
        if is_tenant_admin(user) or user_has_codename(user, 'change_goal'):
            return
        if _is_owner(user, sheet):
            allowed = self.EMPLOYEE_DRAFT_FIELDS if sheet.status == 'draft' else (
                self.EMPLOYEE_PROGRESS_FIELDS if sheet.status == 'approved' else set())
        elif sheet.manager_id == user.id:
            allowed = self.MANAGER_FIELDS if sheet.status in ('submitted', 'approved', 'self_submitted') else set()
        else:
            raise PermissionDenied("Not your goal sheet.")
        extra = set(fields) - allowed
        if extra:
            raise PermissionDenied(f"Not editable at status '{sheet.get_status_display()}': {', '.join(sorted(extra))}")

    def perform_create(self, serializer):
        self._check(serializer.validated_data['sheet'], serializer.validated_data.keys())
        serializer.save()

    def perform_update(self, serializer):
        self._check(serializer.instance.sheet, serializer.validated_data.keys())
        serializer.save()

    def perform_destroy(self, instance):
        self._check(instance.sheet, {'sheet'})
        instance.delete()


class CheckInViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = CheckIn.objects.select_related('sheet', 'sheet__employee').all()
    serializer_class = CheckInSerializer
    employee_lookup = 'sheet__employee'
    manager_lookup = 'sheet__manager'
    self_service_actions = {'list', 'retrieve', 'create'}

    def get_queryset(self):
        qs = super().get_queryset()
        sheet = self.request.query_params.get('sheet')
        return qs.filter(sheet_id=sheet) if sheet else qs

    def perform_create(self, serializer):
        sheet = serializer.validated_data['sheet']
        if not (_is_owner(self.request.user, sheet) or _is_manager(self.request.user, sheet)):
            raise PermissionDenied("Not your goal sheet.")
        serializer.save(created_by=self.request.user)


class CalibrationLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, TenantModelPermission]
    queryset = CalibrationLog.objects.select_related('sheet', 'sheet__employee', 'changed_by').all()
    serializer_class = CalibrationLogSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('cycle'):
            qs = qs.filter(sheet__cycle_id=p['cycle'])
        if p.get('sheet'):
            qs = qs.filter(sheet_id=p['sheet'])
        return qs


class AppraisalOutcomeViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = AppraisalOutcome.objects.select_related('sheet', 'sheet__cycle', 'sheet__employee', 'sheet__employee__emp_dept_id').all()
    serializer_class = AppraisalOutcomeSerializer
    employee_lookup = 'sheet__employee'
    http_method_names = ['get', 'patch', 'post', 'head', 'options']
    action_verbs = {'approve': 'change', 'reject': 'change', 'push_to_payroll': 'change', 'approve_all': 'change', 'push_all': 'change'}
    self_service_actions = {'list', 'retrieve'}

    def get_queryset(self):
        qs = super().get_queryset()
        p = self.request.query_params
        if p.get('cycle'):
            qs = qs.filter(sheet__cycle_id=p['cycle'])
        if p.get('status'):
            qs = qs.filter(status=p['status'])
        return qs

    def create(self, request, *args, **kwargs):
        return Response({'detail': 'Outcomes are generated from the appraisal cycle.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        o = self.get_object()
        if o.status not in ('pending', 'rejected'):
            raise ValidationError({'detail': 'Only pending outcomes can be approved.'})
        # v1.62.0 talent-ops: only on locked ratings, and only while the outcome still matches the calibrated rating
        if o.sheet.cycle.status not in ('calibration', 'closed'):
            raise ValidationError({'detail': 'Lock the ratings of the cycle before approving outcomes.'})
        if o.sheet.final_rating != o.final_rating:
            raise ValidationError({'detail': 'The rating changed after this outcome was made – generate the outcomes again before approving.'})
        o.status = 'approved'; o.save(update_fields=['status'])
        return Response(AppraisalOutcomeSerializer(o).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        o = self.get_object()
        if o.status == 'pushed':
            raise ValidationError({'detail': 'Already pushed to payroll.'})
        o.status = 'rejected'; o.remarks = request.data.get('remarks') or o.remarks; o.save()
        return Response(AppraisalOutcomeSerializer(o).data)

    @action(detail=True, methods=['post'])
    def push_to_payroll(self, request, pk=None):
        o = self.get_object()
        run(services.push_to_payroll, o, request.user)
        return Response(AppraisalOutcomeSerializer(o).data)

    @action(detail=False, methods=['post'])
    def approve_all(self, request):
        cycle = request.data.get('cycle')
        if not cycle:
            raise ValidationError({'cycle': 'Required.'})
        # v1.62.0 talent-ops: only on locked ratings; outcomes whose rating changed since they were made are left out
        from .models import AppraisalCycle
        c = AppraisalCycle.objects.filter(pk=cycle).first()
        if c is None or c.status not in ('calibration', 'closed'):
            raise ValidationError({'detail': 'Lock the ratings of the cycle before approving outcomes.'})
        qs = AppraisalOutcome.objects.filter(sheet__cycle_id=cycle, status='pending').select_related('sheet')
        ids = [o.pk for o in qs if o.sheet.final_rating == o.final_rating]
        stale = qs.count() - len(ids)
        n = AppraisalOutcome.objects.filter(pk__in=ids).update(status='approved')
        msg = f'{n} outcome(s) approved.' + (f' {stale} left out: the rating changed after the outcome was made – generate the outcomes again.' if stale else '')
        return Response({'detail': msg, 'approved': n, 'stale': stale})

    @action(detail=False, methods=['post'])
    def push_all(self, request):
        cycle = request.data.get('cycle')
        if not cycle:
            raise ValidationError({'cycle': 'Required.'})
        done, errors = 0, []
        for o in AppraisalOutcome.objects.filter(sheet__cycle_id=cycle, status='approved').select_related('sheet__employee', 'sheet__cycle'):
            try:
                services.push_to_payroll(o, request.user); done += 1
            except DjangoValidationError as exc:
                errors.append({'employee': o.sheet.employee.emp_code, 'error': exc.messages})
        return Response({'detail': f'{done} outcome(s) pushed to payroll.', 'errors': errors})


class PerformanceImprovementPlanViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = PerformanceImprovementPlan.objects.select_related('employee', 'sheet', 'owner').prefetch_related('reviews')
    serializer_class = PerformanceImprovementPlanSerializer
    manager_lookup = 'owner'
    self_service_actions = {'list', 'retrieve'}


class PIPReviewViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = PIPReview.objects.select_related('pip', 'pip__employee').all()
    serializer_class = PIPReviewSerializer
    employee_lookup = 'pip__employee'
    manager_lookup = 'pip__owner'
    self_service_actions = {'list', 'retrieve'}

    def get_queryset(self):
        qs = super().get_queryset()
        pip = self.request.query_params.get('pip')
        return qs.filter(pip_id=pip) if pip else qs

    def perform_create(self, serializer):
        serializer.save(reviewed_by=self.request.user)


# ====================================================================== v1.67.26 (pending item 1.62.12, item 234) compensation cycle
from rest_framework.views import APIView as _APIView  # noqa: E402
from rest_framework.permissions import IsAuthenticated as _IsAuth  # noqa: E402
from rest_framework.response import Response as _Resp  # noqa: E402

from . import compensation as CMP  # noqa: E402
from .models import CompensationCycle as _CC  # noqa: E402


def _cright(request, verb):
    from AccessControl.access import ctx as zctx
    c = zctx(request)
    return bool(c.admin or f'{verb}_appraisaloutcome' in c.codes)


def _cfail(e):
    from Translations.i18n import tr
    return _Resp({'detail': tr(str(e)), **({'field': e.field} if e.field else {})}, status=e.status)


class CompCyclesView(_APIView):
    """GET the cycles; POST a new one {name, effective_date, budget_percent, appraisal_cycle, branches, departments, joined_before}."""
    permission_classes = [_IsAuth]

    def get(self, request):
        if not _cright(request, 'view'):
            return _Resp({'detail': 'You do not have the right to see salary reviews.'}, status=403)
        from .models import AppraisalCycle
        return _Resp({'cycles': [CMP.cycle_json(c, lines=False) for c in _CC.objects.all()[:100]],
                      'appraisal_cycles': [{'id': a.pk, 'name': a.name, 'status': a.status} for a in AppraisalCycle.objects.all()[:100]]})

    def post(self, request):
        if not _cright(request, 'change'):
            return _Resp({'detail': 'You do not have the right to prepare salary reviews.'}, status=403)
        return self._save(request, _CC())

    @staticmethod
    def _save(request, c):
        from datetime import date as _d
        from decimal import Decimal, InvalidOperation
        d = request.data
        name = str(d.get('name', c.name) or '').strip()
        if not name:
            return _Resp({'detail': 'Give the cycle a name.', 'field': 'name'}, status=400)
        if _CC.objects.filter(name__iexact=name).exclude(pk=c.pk).exists():
            return _Resp({'detail': 'Another cycle has this name.', 'field': 'name'}, status=400)
        try:
            eff = _d.fromisoformat(str(d.get('effective_date') or c.effective_date)[:10])
        except ValueError:
            return _Resp({'detail': 'Enter the effective date.', 'field': 'effective_date'}, status=400)
        try:
            bud = Decimal(str(d.get('budget_percent', c.budget_percent) or 0))
        except InvalidOperation:
            return _Resp({'detail': 'Budget %: a number.', 'field': 'budget_percent'}, status=400)
        if bud < 0 or bud > 100:
            return _Resp({'detail': 'Budget %: from 0 to 100.', 'field': 'budget_percent'}, status=400)
        if c.pk and c.status != 'draft':
            return _Resp({'detail': 'Only a draft cycle can be changed.'}, status=409)
        c.name, c.effective_date, c.budget_percent = name[:150], eff, bud
        c.appraisal_cycle_id = d.get('appraisal_cycle') or None
        jb = d.get('joined_before')
        c.joined_before = _d.fromisoformat(str(jb)[:10]) if jb else None
        c.save()
        if 'branches' in d:
            c.branches.set(d.get('branches') or [])
        if 'departments' in d:
            c.departments.set(d.get('departments') or [])
        return _Resp(CMP.cycle_json(c), status=201)


class CompCycleView(_APIView):
    """GET one cycle with its lines; PUT changes the draft; POST {action: fill|line|submit|approve|return|apply|letters|cancel}."""
    permission_classes = [_IsAuth]

    def get(self, request, pk):
        if not _cright(request, 'view'):
            return _Resp({'detail': 'You do not have the right to see salary reviews.'}, status=403)
        c = _CC.objects.filter(pk=pk).first()
        if c is None:
            return _Resp({'detail': 'Not found.'}, status=404)
        out = CMP.cycle_json(c)
        out['can_approve'] = c.status == 'review' and c.prepared_by_id != request.user.pk and _cright(request, 'change')
        return _Resp(out)

    def put(self, request, pk):
        if not _cright(request, 'change'):
            return _Resp({'detail': 'You do not have the right to prepare salary reviews.'}, status=403)
        c = _CC.objects.filter(pk=pk).first()
        if c is None:
            return _Resp({'detail': 'Not found.'}, status=404)
        return CompCyclesView._save(request, c)

    def post(self, request, pk):
        if not _cright(request, 'change'):
            return _Resp({'detail': 'You do not have the right to change salary reviews.'}, status=403)
        c = _CC.objects.filter(pk=pk).first()
        if c is None:
            return _Resp({'detail': 'Not found.'}, status=404)
        a = request.data.get('action')
        extra = {}
        try:
            if a == 'fill':
                extra['filled'] = CMP.fill(c)
            elif a == 'line':
                CMP.set_line(c, request.data.get('line'), request.data)
            elif a == 'submit':
                CMP.submit(c, request.user)
            elif a in ('approve', 'return'):
                CMP.decide(c, request.user, a == 'approve', request.data.get('note', ''))
            elif a == 'apply':
                extra['applied'] = CMP.apply(c, request.user)
            elif a == 'letters':
                out, failed, sent = CMP.letters(c, request.user, request.data.get('language') or 'en', str(request.data.get('email')).lower() in ('1', 'true'))
                extra.update({'letters': len(out), 'failed': failed, 'emailed': sent, 'letter_ids': [x.pk for x in out]})
            elif a == 'cancel':
                if c.status in ('applied',):
                    return _Resp({'detail': 'An applied cycle cannot be cancelled.'}, status=409)
                c.status = 'cancelled'
                c.save(update_fields=['status'])
            else:
                return _Resp({'detail': 'Unknown action.'}, status=400)
        except CMP.CompError as e:
            return _cfail(e)
        c.refresh_from_db()
        return _Resp({**CMP.cycle_json(c), **extra})


# ====================================================================== v1.67.27 (pending item 1.62.13, item 241) nine-box grid
class NineBoxView(_APIView):
    """GET the nine-box of the employees the user may rate (HR: every employee of the user's branches; a line manager: their team):
    {rows: [{employee_id, employee, code, department, rating, cycle, performance, potential, box, box_label, note, can_set}], grid: {box: n}}.
    POST {employee, potential: 1 | 2 | 3, note} sets the potential (HR with "change goal sheet", or the employee's line manager)."""
    permission_classes = [_IsAuth]

    @staticmethod
    def _scope(request):
        from AccessControl.access import ctx as zctx
        from EmpManagement.models import emp_master
        c = zctx(request)
        hr = bool(c.admin or 'change_goalsheet' in c.codes or 'view_goalsheet' in c.codes)
        qs = emp_master.objects.filter(is_active=True).select_related('emp_dept_id', 'emp_branch_id', 'emp_desgntn_id')
        if hr:
            if c.branches is not None:
                qs = qs.filter(emp_branch_id__in=c.branches)
        else:
            qs = qs.filter(emp_reporting_manager=request.user)
        can_hr = bool(c.admin or 'change_goalsheet' in c.codes)
        return qs, hr, can_hr

    def get(self, request):
        from DashboardManagement.rep_1627 import nine_box_rows
        qs, hr, can_hr = self._scope(request)
        emps = list(qs.order_by('emp_code'))
        L = {1: 'Low', 2: 'Medium', 3: 'High'}
        rows, grid = [], {}
        for x in nine_box_rows(emps):
            e = x['e']
            rows.append({'employee_id': e.pk, 'employee': ' '.join(v for v in (e.emp_first_name, e.emp_last_name) if v), 'code': e.emp_code,
                         'department': e.emp_dept_id.dept_name if e.emp_dept_id_id else '', 'rating': x['rating'], 'cycle': x['cycle'],
                         'performance': x['performance'], 'performance_label': L.get(x['performance'], ''), 'potential': x['potential'],
                         'box': x['box'], 'box_label': x['box_label'], 'note': x['note'], 'set_on': x['set_on'].isoformat() if x['set_on'] else None,
                         'can_set': can_hr or e.emp_reporting_manager_id == request.user.pk})
            if x['box']:
                grid[str(x['box'])] = grid.get(str(x['box']), 0) + 1
        if not rows and not hr:
            return _Resp({'detail': 'You do not have the right to see the nine-box grid.'}, status=403)
        return _Resp({'rows': rows, 'grid': grid, 'hr': hr})

    def post(self, request):
        from datetime import date as _d
        from .models import TalentPotential
        qs, _hr, can_hr = self._scope(request)
        try:
            eid = int(request.data.get('employee'))
            pot = int(request.data.get('potential'))
        except (TypeError, ValueError):
            return _Resp({'detail': 'Choose the employee and the potential (low, medium or high).'}, status=400)
        if pot not in (1, 2, 3):
            return _Resp({'detail': 'The potential is low, medium or high.', 'field': 'potential'}, status=400)
        e = qs.filter(pk=eid).first()
        if e is None:
            return _Resp({'detail': 'Employee not found.'}, status=404)
        if not (can_hr or e.emp_reporting_manager_id == request.user.pk):
            return _Resp({'detail': 'Only HR or the line manager can set the potential.'}, status=403)
        note = str(request.data.get('note') or '').strip()[:255]
        who = request.user.get_full_name() or request.user.username
        tp = TalentPotential.objects.filter(employee=e).first()
        if tp is None:
            tp = TalentPotential(employee=e)
        elif tp.potential != pot or tp.note != note:
            tp.history = (tp.history or []) + [{'potential': tp.potential, 'note': tp.note, 'by': tp.set_by.username if tp.set_by_id else '',
                                                'on': tp.set_on.isoformat() if tp.set_on else None}]
        tp.potential, tp.note, tp.set_by, tp.set_on = pot, note, request.user, _zt_today()
        tp.save()
        return self.get(request)
