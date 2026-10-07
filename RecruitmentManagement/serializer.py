from rest_framework import serializers

from zeo.module_helpers import DisplayFieldsMixin, model_clean
from .models import (Application, ApplicationStageHistory, Candidate, CandidateDocument, Interview, InterviewScore,
                     JobOpening, ManpowerRequisition, OnboardingTask, Offer, RequisitionApproval,
                     RequisitionApprovalLevel, VisaStep)


class RequisitionApprovalLevelSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = RequisitionApprovalLevel
        fields = '__all__'


class RequisitionApprovalSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = RequisitionApproval
        fields = '__all__'


class ManpowerRequisitionSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    approvals = RequisitionApprovalSerializer(many=True, read_only=True)
    pending_with = serializers.SerializerMethodField()

    class Meta:
        model = ManpowerRequisition
        fields = '__all__'
        read_only_fields = ('document_number', 'status', 'requested_by', 'submitted_on', 'approved_on', 'created_at')

    def get_pending_with(self, obj):
        step = obj.approvals.filter(status='pending').order_by('level').first()
        if not step:
            return None
        return f"{step.role}" + (f" ({step.approver})" if step.approver else '')

    def validate(self, attrs):
        if self.instance and self.instance.status not in ('draft', 'rejected'):
            raise serializers.ValidationError("Only draft or rejected requisitions can be edited.")
        return model_clean(self, attrs, ManpowerRequisition)


class JobOpeningSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    applicant_count = serializers.SerializerMethodField()
    stage_counts = serializers.SerializerMethodField()

    class Meta:
        model = JobOpening
        fields = '__all__'
        read_only_fields = ('job_code', 'published_on', 'created_at')

    def get_applicant_count(self, obj):
        return obj.applications.count()

    def get_stage_counts(self, obj):
        return {k: obj.applications.filter(stage=k).count() for k, _ in Application.STAGES}

    def validate_channels(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Send a list of channel names.")
        return value

    def validate(self, attrs):
        return model_clean(self, attrs, JobOpening)


class CandidateDocumentSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = CandidateDocument
        fields = '__all__'


class CandidateSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    documents = CandidateDocumentSerializer(many=True, read_only=True)
    full_name = serializers.CharField(read_only=True)
    applications_summary = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = '__all__'

    def get_applications_summary(self, obj):
        return [{'id': a.id, 'job': a.job.title, 'job_code': a.job.job_code, 'stage': a.stage} for a in obj.applications.select_related('job')]


class StageHistorySerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = ApplicationStageHistory
        fields = '__all__'


class ApplicationSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    candidate_name = serializers.CharField(source='candidate.full_name', read_only=True)
    candidate_nationality = serializers.SerializerMethodField()
    candidate_experience = serializers.DecimalField(source='candidate.experience_years', max_digits=4, decimal_places=1, read_only=True)
    candidate_source = serializers.CharField(source='candidate.get_source_display', read_only=True)
    job_title = serializers.CharField(source='job.title', read_only=True)
    has_offer = serializers.SerializerMethodField()
    history = StageHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Application
        fields = '__all__'
        read_only_fields = ('stage', 'rejection_reason', 'screened_by', 'updated_at')

    def get_candidate_nationality(self, obj):
        return str(obj.candidate.nationality) if obj.candidate.nationality else None

    def get_has_offer(self, obj):
        return hasattr(obj, 'offer')

    def validate(self, attrs):
        job = attrs.get('job') or getattr(self.instance, 'job', None)
        if job and job.status in ('closed', 'filled') and self.instance is None:
            raise serializers.ValidationError("This job opening is no longer accepting applications.")
        return model_clean(self, attrs, Application)


class InterviewScoreSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = InterviewScore
        fields = '__all__'

    def validate(self, attrs):
        return model_clean(self, attrs, InterviewScore)


class InterviewSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    scores = InterviewScoreSerializer(many=True, read_only=True)
    candidate_name = serializers.CharField(source='application.candidate.full_name', read_only=True)
    job_title = serializers.CharField(source='application.job.title', read_only=True)
    panel_names = serializers.SerializerMethodField()

    def get_panel_names(self, obj):
        return ', '.join(' '.join(x for x in (e.emp_first_name, e.emp_last_name) if x) or e.emp_code for e in obj.panel.all())

    class Meta:
        model = Interview
        fields = '__all__'
        read_only_fields = ('overall_score', 'created_at')


class VisaStepSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = VisaStep
        fields = '__all__'


class OnboardingTaskSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = OnboardingTask
        fields = '__all__'
        read_only_fields = ('done_on',)


class OfferSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    total_monthly = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    candidate_name = serializers.CharField(source='application.candidate.full_name', read_only=True)
    candidate_nationality = serializers.SerializerMethodField()
    job_title = serializers.CharField(source='application.job.title', read_only=True)
    visa_progress = serializers.SerializerMethodField()
    onboarding_progress = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = '__all__'
        read_only_fields = ('document_number', 'status', 'approved_by', 'approved_on', 'sent_on', 'responded_on',
                            'employee', 'created_by', 'created_at', 'decline_reason')

    def get_candidate_nationality(self, obj):
        n = obj.application.candidate.nationality
        return str(n) if n else None

    def get_visa_progress(self, obj):
        steps = obj.visa_steps.all()
        total = steps.count()
        done = steps.filter(status__in=('done', 'not_applicable')).count()
        return {'done': done, 'total': total}

    def get_onboarding_progress(self, obj):
        tasks = obj.onboarding_tasks.all()
        return {'done': tasks.filter(is_done=True).count(), 'total': tasks.count()}

    def validate(self, attrs):
        if self.instance and self.instance.status not in ('draft', 'pending_approval'):
            raise serializers.ValidationError("Only draft offers can be changed. Withdraw and create a new offer instead.")
        app = attrs.get('application')
        if app and app.stage in ('rejected', 'withdrawn', 'hired'):
            raise serializers.ValidationError("The application is closed.")
        return model_clean(self, attrs, Offer)
