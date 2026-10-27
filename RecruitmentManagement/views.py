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
    action_verbs = {'publish': 'change', 'close': 'change', 'dashboard': 'view', 'matching_candidates': 'view', 'to_pool': 'change'}

    @action(detail=True, methods=['get'])
    def matching_candidates(self, request, pk=None):
        """v1.57.0: earlier candidates who may be contacted and whose tags / skills match this job."""
        from . import careers as _c
        return Response({'job': self.get_object().title, 'candidates': _c.suggest_for_job(self.get_object())})

    @action(detail=True, methods=['post'])
    def to_pool(self, request, pk=None):
        """v1.57.0: {pool, stages} – put this job's candidates of those stages (default rejected / withdrawn) in a talent pool."""
        from . import careers as _c
        from .models import TalentPool
        job = self.get_object()
        pool = TalentPool.objects.filter(pk=request.data.get('pool')).first()
        if pool is None:
            raise ValidationError({'pool': 'Choose a talent pool.'})
        stages = request.data.get('stages') or ['rejected', 'withdrawn']
        if isinstance(stages, str):
            stages = [x for x in stages.split(',') if x]
        cands = list(Candidate.objects.filter(applications__job=job, applications__stage__in=stages).distinct())
        if not cands:
            raise ValidationError({'detail': 'No candidates of this job are at those stages.'})
        from Translations.i18n import tr
        n = _c.add_to_pool(pool, cands, request.user, f'From {job.job_code} {job.title}')
        return Response({'added': n, 'detail': tr('{0} candidate(s) added to {1}.', n, pool.name) + (' ' + tr('{0} were already in it.', len(cands) - n) if len(cands) > n else '')})

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
        # v1.57.0 talent pools: ?tag=<tag> (any of a comma list), ?pool=<id>, ?contactable=1
        tag = self.request.query_params.get('tag')
        if tag:
            cond = Q()
            for t in [x.strip() for x in tag.split(',') if x.strip()]:
                cond |= Q(tags__icontains=f'"{t}"')
            qs = qs.filter(cond)
        if self.request.query_params.get('pool'):
            qs = qs.filter(pool_memberships__pool_id=self.request.query_params['pool']).distinct()
        if self.request.query_params.get('contactable') in ('1', 'true'):
            qs = qs.filter(consent_to_retain=True).filter(Q(consent_until__isnull=True) | Q(consent_until__gte=timezone.localdate()))
        return qs

    action_verbs = {'duplicates': 'view', 'merge': 'change', 'add_to_pool': 'change'}

    @action(detail=True, methods=['post'])
    def add_to_pool(self, request, pk=None):
        """v1.57.0: {pool, note} – put this candidate in a talent pool."""
        from . import careers as _c
        from .models import TalentPool
        cand = self.get_object()
        pool = TalentPool.objects.filter(pk=request.data.get('pool')).first()
        if pool is None:
            raise ValidationError({'pool': 'Choose a talent pool.'})
        n = _c.add_to_pool(pool, [cand], request.user, str(request.data.get('note') or ''))
        from Translations.i18n import tr
        msg = tr('{0} added to {1}.', cand.full_name, pool.name) if n else tr('{0} is already in {1}.', cand.full_name, pool.name)
        if not cand.may_contact:
            msg += ' ' + tr('No consent to keep the CV: re-engage e-mails will leave this candidate out.')
        return Response({'added': n, 'detail': msg})

    # v1.45.0 duplicates and merge (the same person applied twice)
    @action(detail=False, methods=['get'])
    def duplicates(self, request):
        """Candidates sharing an e-mail or a mobile (last 9 digits): [{key, reason, candidates: [{id, name, email, phone, applications}]}]."""
        import re
        groups = {}
        for c in self.get_queryset():
            keys = []
            if c.email:
                keys.append(('e:' + c.email.strip().lower(), 'Same e-mail'))
            digits = re.sub(r'\D', '', c.phone or '')
            if len(digits) >= 9:
                keys.append(('p:' + digits[-9:], 'Same phone'))
            for k, why in keys:
                groups.setdefault(k, {'key': k, 'reason': why, 'candidates': []})['candidates'].append(
                    {'id': c.id, 'name': c.full_name, 'email': c.email, 'phone': c.phone, 'created_at': c.created_at,
                     'applications': [a.job.title for a in c.applications.all()]})
        seen, out = set(), []
        for g in groups.values():
            ids = tuple(sorted(x['id'] for x in g['candidates']))
            if len(ids) > 1 and ids not in seen:
                seen.add(ids)
                out.append(g)
        return Response(out)

    @action(detail=True, methods=['post'])
    def merge(self, request, pk=None):
        """Merge this candidate into {into}: applications, documents, notes and files move; empty details are filled; this one is deleted."""
        from django.db import transaction
        drop = self.get_object()
        keep = self.get_queryset().filter(pk=request.data.get('into')).first()
        if keep is None or keep.pk == drop.pk:
            raise ValidationError({'into': 'Choose the other candidate to keep.'})
        both = set(drop.applications.values_list('job_id', flat=True)) & set(keep.applications.values_list('job_id', flat=True))
        if both:
            titles = ', '.join(JobOpening.objects.filter(pk__in=both).values_list('title', flat=True))
            raise ValidationError({'detail': f'Both candidates applied for {titles}. Withdraw one of the two applications first.'})
        with transaction.atomic():
            drop.applications.update(candidate=keep)
            drop.documents.update(candidate=keep)
            for f in ('last_name', 'phone', 'gender', 'date_of_birth', 'nationality', 'current_location', 'current_employer', 'current_designation',
                      'current_salary', 'expected_salary', 'referred_by', 'skills', 'languages'):
                if getattr(keep, f) in (None, '') and getattr(drop, f) not in (None, ''):
                    setattr(keep, f, getattr(drop, f))
            if not keep.cv and drop.cv:
                keep.cv = drop.cv
            if drop.notes:
                keep.notes = ((keep.notes or '') + f'\n[merged from {drop.full_name} <{drop.email}>] ' + drop.notes).strip()
            keep.consent_to_retain = keep.consent_to_retain or drop.consent_to_retain
            keep.save()
            try:
                from Chatter.collab import move_record
                move_record('RecruitmentManagement.candidate', drop.pk, keep.pk, request.user,
                            f'{drop.full_name} <{drop.email}> (#{drop.pk}) merged into this candidate.')
            except Exception:
                pass
            drop.delete()
        return Response(CandidateSerializer(keep, context={'request': request}).data)


class CandidateDocumentViewSet(BaseViewSet):
    queryset = CandidateDocument.objects.all()
    serializer_class = CandidateDocumentSerializer
    filter_params = {'candidate': 'candidate_id'}


class ApplicationViewSet(BaseViewSet):
    queryset = Application.objects.select_related('candidate', 'candidate__nationality', 'job').prefetch_related('history')
    serializer_class = ApplicationSerializer
    action_verbs = {'move': 'change', 'board': 'view'}
    filter_params = {'job': 'job_id', 'stage': 'stage__in', 'candidate': 'candidate_id', 'source': 'source__in', 'knockout_failed': 'knockout_failed'}

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


# ====================================================================== v1.57.0 careers page and talent pools
from django.http import FileResponse, Http404, HttpResponse  # noqa: E402
from django.utils.decorators import method_decorator  # noqa: E402
from django.views.decorators.csrf import csrf_exempt  # noqa: E402
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser  # noqa: E402
from rest_framework.permissions import AllowAny  # noqa: E402
from rest_framework.views import APIView  # noqa: E402

from . import careers  # noqa: E402
from .models import CareersSettings, TalentCampaign, TalentPool, TalentPoolMember  # noqa: E402
from .serializer import (CareersSettingsSerializer, TalentCampaignSerializer, TalentPoolMemberSerializer,  # noqa: E402
                         TalentPoolSerializer)


class _Public(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    zeo_access = False
    zeo_scope = False

    def fail(self, e):
        from Translations.i18n import tr
        return Response({'detail': tr(str(e)), **({'field': e.field} if e.field else {})}, status=e.status)


@method_decorator(csrf_exempt, name='dispatch')
class CareersPageView(_Public):
    """GET /recruitment/api/careers/?schema=<company> – the company, the settings shown to visitors, the open jobs."""
    def get(self, request):
        try:
            return Response(careers.page(request))
        except careers.CareersError as e:
            return self.fail(e)


@method_decorator(csrf_exempt, name='dispatch')
class CareersJobView(_Public):
    def get(self, request, code):
        try:
            return Response(careers.job_detail(code))
        except careers.CareersError as e:
            return self.fail(e)


@method_decorator(csrf_exempt, name='dispatch')
class CareersApplyView(_Public):
    """POST multipart: job (code, empty = send a CV to the talent pool), first_name, last_name, email, phone, nationality, current_location,
    experience_years, notice_period_days, expected_salary, visa_status, linkedin, cover_letter, answers (JSON [{id, answer}]), cv,
    privacy (required), consent_to_retain, src (linkedin / indeed / bayt / pool …), website (honeypot)."""
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        # the last X-Forwarded-For entry is the one nginx added ($proxy_add_x_forwarded_for) – earlier ones can be made up by the visitor
        xff = [x.strip() for x in (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',') if x.strip()]
        ip = xff[-1] if xff else (request.META.get('REMOTE_ADDR') or '')
        try:
            out = careers.apply(request.data, request.FILES, ip)
        except careers.CareersError as e:
            return self.fail(e)
        return Response({'ok': True, 'message': out['message']}, status=201)


class CareersLogoView(_Public):
    def get(self, request):
        c = careers.company()
        if not (c and c.logo and CareersSettings.get().enabled):
            raise Http404
        try:
            return FileResponse(c.logo.open('rb'))
        except Exception:
            raise Http404


class CareersFeedView(_Public):
    """GET /recruitment/api/careers/feed.xml|feed.json?schema=<company>[&key=…] – open jobs for job boards."""
    def get(self, request, fmt):
        careers.use_request(request)
        try:
            if fmt == 'xml':
                return HttpResponse(careers.feed_xml(request.query_params.get('key', '')), content_type='application/xml; charset=utf-8')
            return Response(careers.feed_json(request.query_params.get('key', '')))
        except careers.CareersError as e:
            return self.fail(e)


class CareersSettingsView(APIView):
    """GET / PATCH the company's careers page settings (Recruitment › Careers page). Rights: job openings."""
    permission_classes = [IsAuthenticated]

    def _allowed(self, request, verb):
        from AccessControl.access import ctx as zctx
        c = zctx(request)
        return bool(c.admin or f'{verb}_jobopening' in c.codes)

    def payload(self, s):
        from django.db import connection
        d = CareersSettingsSerializer(s).data
        d.update({'page_url': careers.page_url(), 'schema': connection.schema_name, 'app_url_set': bool(getattr(__import__('django.conf', fromlist=['settings']).settings, 'ZEO_APP_URL', '')),
                  'feed_xml': f'/recruitment/api/careers/feed.xml?schema={connection.schema_name}' + (f'&key={s.feed_key}' if s.feed_key else ''),
                  'feed_json': f'/recruitment/api/careers/feed.json?schema={connection.schema_name}' + (f'&key={s.feed_key}' if s.feed_key else ''),
                  'open_jobs': len(careers.open_jobs()),
                  'jobs_not_listed': [{'code': j.job_code, 'title': j.title} for j in JobOpening.objects.filter(status__in=('published', 'interviewing'))
                                      if not j.on_careers_page][:20]})
        return d

    def get(self, request):
        careers.use_request(request)
        if not self._allowed(request, 'view'):
            return Response({'detail': 'You do not have the right to see the careers page settings.'}, status=403)
        return Response(self.payload(CareersSettings.get()))

    def patch(self, request):
        if not self._allowed(request, 'change'):
            return Response({'detail': 'You do not have the right to change the careers page settings.'}, status=403)
        s = CareersSettings.get()
        ser = CareersSettingsSerializer(s, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(self.payload(s))

    put = patch


class TalentPoolViewSet(BaseViewSet):
    queryset = TalentPool.objects.select_related('owner').all()
    serializer_class = TalentPoolSerializer
    action_verbs = {'add': 'change', 'reengage': 'change', 'remove': 'change', 'suggest': 'view'}

    def perform_create(self, serializer):
        serializer.save(owner=serializer.validated_data.get('owner') or self.request.user)

    def perform_destroy(self, instance):
        if instance.is_general:
            raise ValidationError({'detail': 'The general pool keeps the CVs sent from the careers page – it cannot be deleted.'})
        instance.delete()

    @action(detail=True, methods=['post'])
    def add(self, request, pk=None):
        """{candidates: [ids]} or {from_job: id, stages: ['rejected', ...]} – add candidates; returns the count added."""
        pool = self.get_object()
        ids = request.data.get('candidates') or []
        if isinstance(ids, (str, int)):
            ids = [x for x in str(ids).split(',') if x.strip()]
        qs = Candidate.objects.filter(pk__in=ids) if ids else Candidate.objects.none()
        if request.data.get('from_job'):
            stages = request.data.get('stages') or ['rejected', 'withdrawn']
            if isinstance(stages, str):
                stages = stages.split(',')
            qs = Candidate.objects.filter(applications__job_id=request.data['from_job'], applications__stage__in=stages).distinct()
        if not qs.exists():
            raise ValidationError({'candidates': 'Choose at least one candidate.'})
        n = careers.add_to_pool(pool, list(qs), request.user, str(request.data.get('note') or ''))
        no = sum(1 for c in qs if not c.may_contact)
        from Translations.i18n import tr
        msg = tr('{0} candidate(s) added.', n) + (' ' + tr('{0} already in the pool.', qs.count() - n) if qs.count() > n else '') + \
            (' ' + tr('{0} without consent to keep the CV (left out of re-engage e-mails).', no) if no else '')
        return Response({'added': n, 'already': qs.count() - n, 'no_consent': no, 'pool': TalentPoolSerializer(pool).data, 'detail': msg})

    @action(detail=True, methods=['post'])
    def reengage(self, request, pk=None):
        """{subject, message, job?, members?: [member ids]} – e-mail the members (consent only)."""
        pool = self.get_object()
        careers.use_request(request)
        job = JobOpening.objects.filter(pk=request.data.get('job')).first() if request.data.get('job') else None
        ids = request.data.get('members') or None
        try:
            camp = careers.reengage(pool, request.user, request.data.get('subject'), request.data.get('message'), job, ids)
        except careers.CareersError as e:
            raise ValidationError({e.field or 'detail': str(e)})
        d = TalentCampaignSerializer(camp).data
        from Translations.i18n import tr
        d['detail'] = tr('E-mail sent to {0} candidate(s).', camp.sent_count) + (' ' + tr('{0} left out:', len(camp.skipped)) + ' ' + '; '.join(
            f"{x['name']} ({tr(x['reason'])})" for x in camp.skipped[:8]) + ('…' if len(camp.skipped) > 8 else '') if camp.skipped else '')
        return Response(d, status=201)

    @action(detail=False, methods=['get'])
    def suggest(self, request):
        """?job=<id> – candidates who may be contacted and whose tags / skills match the job."""
        job = JobOpening.objects.filter(pk=request.query_params.get('job')).first()
        if job is None:
            raise ValidationError({'job': 'Choose a job opening.'})
        return Response(careers.suggest_for_job(job))


class TalentPoolMemberViewSet(BaseViewSet):
    queryset = TalentPoolMember.objects.select_related('pool', 'candidate', 'candidate__nationality', 'added_by').all()
    serializer_class = TalentPoolMemberSerializer
    filter_params = {'pool': 'pool_id', 'candidate': 'candidate_id', 'status': 'status__in'}

    def perform_create(self, serializer):
        serializer.save(added_by=self.request.user)


class TalentCampaignViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, TenantModelPermission]
    queryset = TalentCampaign.objects.select_related('pool', 'job', 'sent_by').all()
    serializer_class = TalentCampaignSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.query_params.get('pool'):
            qs = qs.filter(pool_id=self.request.query_params['pool'])
        return qs
