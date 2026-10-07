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
        n = AppraisalOutcome.objects.filter(sheet__cycle_id=cycle, status='pending').update(status='approved')
        return Response({'detail': f'{n} outcome(s) approved.'})

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
