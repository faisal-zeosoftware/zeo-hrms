from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from zeo.module_helpers import (EmployeeScopedQuerysetMixin, TenantModelPermission, current_employee, is_tenant_admin,
                                user_has_codename)
from . import services
from .models import Certificate, Course, Nomination, ParticipantResult, TrainingBond, TrainingNeed, TrainingSession
from .serializer import (CertificateSerializer, CourseSerializer, NominationSerializer, ParticipantResultSerializer,
                         TrainingBondSerializer, TrainingNeedSerializer, TrainingSessionSerializer)


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError({'detail': exc.messages})


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantModelPermission]
    filter_params = {}

    def get_queryset(self):
        qs = super().get_queryset()
        for param, lookup in self.filter_params.items():
            val = self.request.query_params.get(param)
            if val not in (None, ''):
                if isinstance(val, str) and val.lower() in ('true', 'false'):
                    val = val.lower() == 'true'  # ?active=true from the calendar screen
                qs = qs.filter(**{lookup: val.split(',') if lookup.endswith('__in') else val})
        return qs


class CourseViewSet(BaseViewSet):
    queryset = Course.objects.prefetch_related('departments').all()
    serializer_class = CourseSerializer
    filter_params = {'course_type': 'course_type__in', 'active': 'is_active'}
    self_service_actions = {'list', 'retrieve'}


class TrainingNeedViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = TrainingNeed.objects.select_related('employee', 'employee__emp_dept_id', 'course').all()
    serializer_class = TrainingNeedSerializer
    manager_lookup = 'employee__emp_reporting_manager'
    filter_params = {'status': 'status__in', 'source': 'source__in', 'employee': 'employee_id', 'course': 'course_id'}
    self_service_actions = {'list', 'retrieve', 'create'}

    def perform_create(self, serializer):
        user = self.request.user
        emp = serializer.validated_data['employee']
        if not (is_tenant_admin(user) or user_has_codename(user, 'add_trainingneed')):
            if emp.users_id == user.id:
                serializer.save(source='self', status='pending_approval')
                return
            if emp.emp_reporting_manager_id == user.id:
                serializer.save(source='manager')
                return
            raise PermissionDenied("You can request training for yourself or your team only.")
        serializer.save()


class TrainingSessionViewSet(BaseViewSet):
    queryset = TrainingSession.objects.select_related('course', 'trainer_employee').all()
    serializer_class = TrainingSessionSerializer
    filter_params = {'status': 'status__in', 'course': 'course_id', 'from': 'end_date__gte', 'to': 'start_date__lte'}
    action_verbs = {'publish': 'change', 'close': 'change', 'cancel': 'change', 'summary': 'view'}
    self_service_actions = {'list', 'retrieve'}

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        s = self.get_object()
        if s.status != 'draft':
            raise ValidationError({'detail': 'Only draft sessions can be published.'})
        s.status = 'published'; s.save(update_fields=['status'])
        return Response(TrainingSessionSerializer(s).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        res = run(services.close_session, self.get_object(), request.user)
        return Response({'detail': f"Session closed: {res['certificates_issued']} certificate(s) issued, {res['not_passed']} to repeat.", **res})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        s = self.get_object()
        if s.status == 'completed':
            raise ValidationError({'detail': 'Completed sessions cannot be cancelled.'})
        s.status = 'cancelled'; s.save(update_fields=['status'])
        s.nominations.exclude(seat_status='cancelled').update(seat_status='cancelled')
        return Response(TrainingSessionSerializer(s).data)

    @action(detail=False, methods=['get'])
    def summary(self, request):
        return Response(services.summary())


class NominationViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = Nomination.objects.select_related('session', 'session__course', 'employee', 'employee__emp_dept_id').all()
    serializer_class = NominationSerializer
    manager_lookup = 'employee__emp_reporting_manager'
    http_method_names = ['get', 'post', 'delete', 'head', 'options']
    filter_params = {'session': 'session_id', 'employee': 'employee_id', 'seat_status': 'seat_status__in'}
    action_verbs = {'approve': 'change', 'reject': 'change'}
    self_service_actions = {'list', 'retrieve', 'create', 'approve', 'reject'}

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        user = request.user
        emp = d['employee']
        admin = is_tenant_admin(user) or user_has_codename(user, 'add_nomination')
        if admin:
            by = d.get('nominated_by') or 'hr'
        elif emp.users_id == user.id:
            by = 'self'
        elif emp.emp_reporting_manager_id == user.id:
            by = 'manager'
        else:
            raise PermissionDenied("You can nominate yourself or your team only.")
        nom = run(services.nominate, d['session'], emp, by, d.get('reason', ''), d.get('need'), user, d.get('allow_leave_clash', False), admin)
        return Response(NominationSerializer(nom).data, status=201)

    def _level(self, nom):
        user = self.request.user
        if is_tenant_admin(user) or user_has_codename(user, 'change_nomination'):
            return self.request.data.get('level') or ('manager' if nom.manager_status == 'pending' else 'ld')
        if nom.employee.emp_reporting_manager_id == user.id:
            return 'manager'
        raise PermissionDenied("Only the reporting manager or L&D can do this.")

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        nom = self.get_object()
        return Response(NominationSerializer(run(services.approve, nom, self._level(nom), request.user)).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        nom = self.get_object()
        return Response(NominationSerializer(run(services.reject, nom, self._level(nom), request.data.get('reason'), request.user)).data)


class ParticipantResultViewSet(BaseViewSet):
    queryset = ParticipantResult.objects.select_related('nomination', 'nomination__employee', 'nomination__session__course').all()
    serializer_class = ParticipantResultSerializer
    filter_params = {'session': 'nomination__session_id'}


class CertificateViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = Certificate.objects.select_related('employee', 'course', 'session').all()
    serializer_class = CertificateSerializer
    manager_lookup = 'employee__emp_reporting_manager'
    filter_params = {'employee': 'employee_id', 'course': 'course_id'}
    action_verbs = {'expiry_scan': 'add'}
    self_service_actions = {'list', 'retrieve'}

    def get_queryset(self):
        qs = super().get_queryset()
        st = self.request.query_params.get('status')
        if st:
            ids = [c.id for c in qs if c.status == st]
            qs = qs.filter(id__in=ids)
        return qs

    @action(detail=False, methods=['post'])
    def expiry_scan(self, request):
        n = services.expiry_scan(int(request.data.get('days', 60)))
        return Response({'detail': f'{n} renewal need(s) created.', 'created': n})


class TrainingBondViewSet(EmployeeScopedQuerysetMixin, BaseViewSet):
    queryset = TrainingBond.objects.select_related('employee', 'course', 'session').all()
    serializer_class = TrainingBondSerializer
    filter_params = {'employee': 'employee_id', 'status': 'status__in'}
    action_verbs = {'accept': 'change', 'recoverable': 'view'}
    self_service_actions = {'list', 'retrieve', 'accept', 'recoverable'}

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        bond = self.get_object()
        if not (bond.employee.users_id == request.user.id or is_tenant_admin(request.user)):
            raise PermissionDenied("Only the employee can accept the bond.")
        return Response(TrainingBondSerializer(run(services.accept_bond, bond, request.user)).data)

    @action(detail=True, methods=['get'])
    def recoverable(self, request, pk=None):
        bond = self.get_object()
        on = request.query_params.get('date')
        return Response({'date': on, 'amount': bond.recoverable_amount(on)})
