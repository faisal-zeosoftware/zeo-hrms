from django.core.exceptions import ValidationError as DjangoValidationError
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from zeo.module_helpers import TenantModelPermission
from . import services
from .models import (Application, Candidate, CandidateDocument, Interview, InterviewScore, JobOpening,
                     ManpowerRequisition, OnboardingTask, Offer, RequisitionApprovalLevel, VisaStep)
from .serializer import (ApplicationSerializer, CandidateDocumentSerializer, CandidateSerializer,
                         InterviewScoreSerializer, InterviewSerializer, JobOpeningSerializer,
                         ManpowerRequisitionSerializer, OfferSerializer, OnboardingTaskSerializer,
                         RequisitionApprovalLevelSerializer, VisaStepSerializer)


def run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DjangoValidationError as exc:
        raise ValidationError({'detail': exc.messages})


class BaseViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantModelPermission]
    filter_params = {}  # query param -> ORM lookup

    def get_queryset(self):
        qs = super().get_queryset()
        for param, lookup in self.filter_params.items():
            val = self.request.query_params.get(param)
            if val not in (None, ''):
                qs = qs.filter(**{lookup: val.split(',') if lookup.endswith('__in') else val})
        return qs


class RequisitionApprovalLevelViewSet(BaseViewSet):
    queryset = RequisitionApprovalLevel.objects.select_related('approver').all()
    serializer_class = RequisitionApprovalLevelSerializer


class ManpowerRequisitionViewSet(BaseViewSet):
    queryset = ManpowerRequisition.objects.select_related('department', 'designation', 'branch', 'reporting_to', 'requested_by').prefetch_related('approvals')
    serializer_class = ManpowerRequisitionSerializer
    filter_params = {'status': 'status__in', 'department': 'department_id'}
    action_verbs = {'submit': 'change', 'approve': 'change', 'reject': 'change', 'create_opening': 'add', 'my_approvals': 'view'}
    self_service_actions = {'my_approvals', 'approve', 'reject'}

    def perform_create(self, serializer):
        serializer.save(requested_by=self.request.user)

    def perform_destroy(self, instance):
        if instance.status not in ('draft', 'rejected'):
            raise ValidationError({'detail': 'Only draft or rejected requisitions can be deleted.'})
        instance.delete()

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        req = run(services.submit_requisition, self.get_object(), request.user)
        return Response(ManpowerRequisitionSerializer(req).data)

    def _act(self, request, decision):
        req = ManpowerRequisition.objects.get(pk=self.kwargs['pk'])
        req = run(services.act_on_requisition, req, request.user, decision, request.data.get('comments', ''))
        return Response(ManpowerRequisitionSerializer(req).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._act(request, 'approve')

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        if not request.data.get('comments'):
            raise ValidationError({'comments': 'A reason is required.'})
        return self._act(request, 'reject')

    @action(detail=False, methods=['get'])
    def my_approvals(self, request):
        qs = ManpowerRequisition.objects.filter(status='pending', approvals__status='pending', approvals__approver=request.user).distinct()
        return Response(ManpowerRequisitionSerializer(qs, many=True).data)

    @action(detail=True, methods=['post'])
    def create_opening(self, request, pk=None):
        job = run(services.opening_from_requisition, self.get_object())
        return Response(JobOpeningSerializer(job).data, status=201)


class JobOpeningViewSet(BaseViewSet):
    queryset = JobOpening.objects.select_related('department', 'designation', 'branch', 'requisition').all()
    serializer_class = JobOpeningSerializer
    filter_params = {'status': 'status__in', 'department': 'department_id'}
    action_verbs = {'publish': 'change', 'close': 'change', 'dashboard': 'view'}

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        job = self.get_object()
        if job.status not in ('draft', 'on_hold'):
            raise ValidationError({'detail': 'Only draft or on-hold openings can be published.'})
        if not job.channels:
            raise ValidationError({'channels': 'Choose at least one channel.'})
        job.status, job.published_on = 'published', timezone.now()
        job.save()
        return Response(JobOpeningSerializer(job).data)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        job = self.get_object()
        job.status = request.data.get('status') if request.data.get('status') in ('closed', 'filled', 'on_hold') else 'closed'
        job.save(update_fields=['status'])
        return Response(JobOpeningSerializer(job).data)

    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        since = timezone.now() - timedelta(days=30)
        week_end = timezone.now() + timedelta(days=7)
        # Time to hire = application date -> offer accepted (industry standard). Back-dated or
        # imported records can give negative gaps, so those are left out of the average.
        hired = Offer.objects.filter(status__in=('accepted', 'joined'), responded_on__isnull=False).select_related('application')
        days = [d for d in ((o.responded_on.date() - o.application.applied_on.date()).days for o in hired if o.application.applied_on) if d >= 0]
        return Response({
            'open_positions': JobOpening.objects.filter(status__in=('published', 'interviewing')).count(),
            'applicants_30_days': Application.objects.filter(applied_on__gte=since).count(),
            'interviews_this_week': Interview.objects.filter(status='scheduled', scheduled_at__gte=timezone.now(), scheduled_at__lte=week_end).count(),
            'avg_time_to_hire_days': round(sum(days) / len(days)) if days else None,
            'offers_pending': Offer.objects.filter(status__in=('pending_approval', 'approved', 'sent')).count(),
            'pending_requisitions': ManpowerRequisition.objects.filter(status='pending').count(),
        })


class CandidateViewSet(BaseViewSet):
    queryset = Candidate.objects.select_related('nationality', 'referred_by').prefetch_related('documents', 'applications__job')
    serializer_class = CandidateSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.query_params.get('q')
        if q:
            qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q) | Q(skills__icontains=q))
        return qs


class CandidateDocumentViewSet(BaseViewSet):
    queryset = CandidateDocument.objects.all()
    serializer_class = CandidateDocumentSerializer
    filter_params = {'candidate': 'candidate_id'}


class ApplicationViewSet(BaseViewSet):
    queryset = Application.objects.select_related('candidate', 'candidate__nationality', 'job').prefetch_related('history')
    serializer_class = ApplicationSerializer
    filter_params = {'job': 'job_id', 'stage': 'stage__in', 'candidate': 'candidate_id'}
    action_verbs = {'move': 'change', 'board': 'view'}

    def perform_update(self, serializer):
        if 'screening_score' in serializer.validated_data:
            serializer.save(screened_by=self.request.user)
        else:
            serializer.save()

    @action(detail=True, methods=['post'])
    def move(self, request, pk=None):
        app = run(services.move_stage, self.get_object(), request.data.get('stage'), request.user, request.data.get('note', ''))
        return Response(ApplicationSerializer(app).data)

    @action(detail=False, methods=['get'])
    def board(self, request):
        job = request.query_params.get('job')
        if not job:
            raise ValidationError({'job': 'Required.'})
        apps_qs = self.get_queryset().filter(job_id=job)
        columns = []
        for key, label in Application.STAGES:
            items = [a for a in apps_qs if a.stage == key]
            columns.append({'stage': key, 'label': label, 'count': len(items),
                            'items': ApplicationSerializer(items, many=True).data})
        return Response({'job': job, 'columns': columns})


class InterviewViewSet(BaseViewSet):
    queryset = Interview.objects.select_related('application__candidate', 'application__job').prefetch_related('panel', 'scores')
    serializer_class = InterviewSerializer
    filter_params = {'application': 'application_id', 'status': 'status__in', 'job': 'application__job_id'}
    action_verbs = {'complete': 'change'}

    def perform_create(self, serializer):
        interview = serializer.save()
        app = interview.application
        if app.stage in ('applied', 'screening'):
            run(services.move_stage, app, 'interview', self.request.user, f"{interview.round_name} scheduled")

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        iv = self.get_object()
        if not iv.scores.exists():
            raise ValidationError({'detail': 'Add at least one scorecard line first.'})
        iv.recompute()
        iv.status = 'completed'
        iv.recommendation = request.data.get('recommendation') or iv.recommendation
        iv.comments = request.data.get('comments') or iv.comments
        iv.save()
        return Response(InterviewSerializer(iv).data)


class InterviewScoreViewSet(BaseViewSet):
    queryset = InterviewScore.objects.select_related('interview').all()
    serializer_class = InterviewScoreSerializer
    filter_params = {'interview': 'interview_id'}

    def perform_create(self, serializer):
        s = serializer.save()
        s.interview.recompute(); s.interview.save(update_fields=['overall_score'])

    def perform_update(self, serializer):
        s = serializer.save()
        s.interview.recompute(); s.interview.save(update_fields=['overall_score'])


class OfferViewSet(BaseViewSet):
    queryset = Offer.objects.select_related('application__candidate__nationality', 'application__job', 'department',
                                            'designation', 'branch', 'reporting_manager', 'employee')
    serializer_class = OfferSerializer
    filter_params = {'status': 'status__in', 'application': 'application_id'}
    action_verbs = {'approve': 'change', 'send': 'change', 'accept': 'change', 'decline': 'change', 'withdraw': 'change',
                    'convert_to_employee': 'change', 'letter': 'view'}

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, status='pending_approval')

    def _out(self, offer):
        return Response(OfferSerializer(offer).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        return self._out(run(services.approve_offer, self.get_object(), request.user))

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        return self._out(run(services.send_offer, self.get_object(), request.user))

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        return self._out(run(services.accept_offer, self.get_object(), request.user))

    @action(detail=True, methods=['post'])
    def decline(self, request, pk=None):
        return self._out(run(services.decline_offer, self.get_object(), request.data.get('reason', ''), request.user))

    @action(detail=True, methods=['post'])
    def withdraw(self, request, pk=None):
        offer = self.get_object()
        if offer.status in ('accepted', 'joined'):
            raise ValidationError({'detail': 'Accepted offers cannot be withdrawn here.'})
        offer.status = 'withdrawn'; offer.save(update_fields=['status'])
        return self._out(offer)

    @action(detail=True, methods=['post'])
    def convert_to_employee(self, request, pk=None):
        offer = self.get_object()
        force = str(request.data.get('ignore_open_steps', '')).lower() in ('1', 'true')
        emp = run(services.convert_to_employee, offer, request.user, request.data.get('emp_code'), not force)
        return Response({'detail': f'Employee {emp.emp_code} created.', 'employee_id': emp.id, 'emp_code': emp.emp_code,
                         'offer': OfferSerializer(offer).data}, status=201)

    @action(detail=True, methods=['get'])
    def letter(self, request, pk=None):
        """Data for the printable offer letter."""
        o = self.get_object()
        c = o.application.candidate
        return Response({
            'document_number': o.document_number, 'date': timezone.localdate(), 'candidate': c.full_name,
            'position': o.position_title, 'department': str(o.department) if o.department else None,
            'contract': f"{o.get_contract_type_display()} - {o.contract_years} year(s)" if o.contract_type == 'limited' else o.get_contract_type_display(),
            'basic': o.basic_salary, 'housing': o.housing_allowance, 'transport': o.transport_allowance,
            'other': o.other_allowance, 'total': o.total_monthly, 'probation_months': o.probation_months,
            'annual_leave_days': o.annual_leave_days, 'air_ticket': o.air_ticket, 'medical_insurance': o.medical_insurance,
            'joining_date': o.joining_date, 'valid_until': o.valid_until, 'terms': o.terms,
        })


class VisaStepViewSet(BaseViewSet):
    queryset = VisaStep.objects.select_related('offer').all()
    serializer_class = VisaStepSerializer
    filter_params = {'offer': 'offer_id', 'status': 'status__in'}


class OnboardingTaskViewSet(BaseViewSet):
    queryset = OnboardingTask.objects.select_related('offer', 'owner').all()
    serializer_class = OnboardingTaskSerializer
    filter_params = {'offer': 'offer_id', 'team': 'team'}
