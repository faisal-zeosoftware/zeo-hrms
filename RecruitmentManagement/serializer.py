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

    def to_representation(self, instance):
        d = super().to_representation(instance)
        d['on_careers_page'] = instance.on_careers_page        # v1.57.0
        return d

    def validate_questions(self, value):
        """v1.57.0 apply-form questions: [{text, type, options, required, must}] – ids are given here."""
        if value in (None, ''):
            return []
        if isinstance(value, str):
            import json
            try:
                value = json.loads(value)
            except ValueError:
                raise serializers.ValidationError('Send a list of questions.')
        if not isinstance(value, list):
            raise serializers.ValidationError('Send a list of questions.')
        out, used = [], set()
        for i, q in enumerate(value):
            if not isinstance(q, dict):
                raise serializers.ValidationError(f'Question {i + 1}: send text and type.')
            text = str(q.get('text') or '').strip()
            if not text:
                continue
            kind = q.get('type') or 'text'
            if kind not in ('yes_no', 'text', 'number', 'choice'):
                raise serializers.ValidationError(f'Question {i + 1}: the type must be yes_no, text, number or choice.')
            opts = q.get('options') or []
            if isinstance(opts, str):
                opts = [o.strip() for o in opts.split('|') if o.strip()]
            if kind == 'choice' and len(opts) < 2:
                raise serializers.ValidationError(f'Question {i + 1} ("{text}"): give at least two answers to choose from.')
            must = q.get('must')
            if kind == 'yes_no' and must not in (None, '') and str(must).lower() not in ('yes', 'no'):
                raise serializers.ValidationError(f'Question {i + 1}: the needed answer is yes or no.')
            if kind == 'number' and must not in (None, ''):
                try:
                    must = float(must)
                except (TypeError, ValueError):
                    raise serializers.ValidationError(f'Question {i + 1}: the minimum must be a number.')
            if kind == 'choice' and must not in (None, '', []):
                must = must if isinstance(must, list) else [m.strip() for m in str(must).split('|') if m.strip()]
                if any(m not in opts for m in must):
                    raise serializers.ValidationError(f'Question {i + 1}: the accepted answers must be among the answers.')
            if kind == 'text':
                must = None
            qid = str(q.get('id') or '').strip() or str(i + 1)
            while qid in used:
                qid += 'b'
            used.add(qid)
            out.append({'id': qid, 'text': text[:300], 'type': kind, 'options': [str(o)[:100] for o in opts] if kind == 'choice' else [],
                        'required': bool(q.get('required')), 'must': must if must not in ('', []) else None})
        if len(out) > 15:
            raise serializers.ValidationError('Ask at most 15 questions.')
        return out

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
    may_contact = serializers.BooleanField(read_only=True)            # v1.57.0
    pools = serializers.SerializerMethodField()

    class Meta:
        model = Candidate
        fields = '__all__'
        read_only_fields = ('last_contacted',)

    def get_pools(self, obj):
        return [m.pool.name for m in obj.pool_memberships.select_related('pool')]

    def validate_tags(self, v):
        if isinstance(v, str):
            import json
            try:
                v = json.loads(v)
            except ValueError:
                v = v.split(',')
        if not isinstance(v, list):
            raise serializers.ValidationError('Send a list of tags.')
        return list(dict.fromkeys(str(t).strip() for t in v if str(t).strip()))

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
        read_only_fields = ('stage', 'rejection_reason', 'screened_by', 'updated_at', 'answers', 'knockout_failed')   # v1.57.0 answers come from the apply form

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


# v1.57.0 careers page and talent pools
from .models import CareersSettings, TalentCampaign, TalentPool, TalentPoolMember  # noqa: E402


class CareersSettingsSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = CareersSettings
        fields = '__all__'
        read_only_fields = ('updated_at',)

    def validate_brand_color(self, v):
        import re
        if v and not re.match(r'^#[0-9a-fA-F]{6}$', v):
            raise serializers.ValidationError('Give a colour as #RRGGBB.')
        return v

    def validate_notify_emails(self, v):
        import re
        bad = [x for x in re.split(r'[,;\s]+', v or '') if x and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', x)]
        if bad:
            raise serializers.ValidationError(f'Not an e-mail address: {", ".join(bad)}')
        return v

    def validate_retention_months(self, v):
        if v is not None and not 1 <= v <= 120:
            raise serializers.ValidationError('Give 1 to 120 months.')
        return v


class TalentPoolSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()
    contactable = serializers.SerializerMethodField()
    last_campaign = serializers.SerializerMethodField()

    class Meta:
        model = TalentPool
        fields = '__all__'
        read_only_fields = ('is_general', 'created_at')

    def get_member_count(self, o):
        return o.members.count()

    def get_contactable(self, o):
        return sum(1 for m in o.members.select_related('candidate') if m.candidate.may_contact and m.status not in ('not_interested', 'hired'))

    def get_last_campaign(self, o):
        c = o.campaigns.order_by('-sent_at').first()
        return c.sent_at if c else None

    def validate_tags(self, v):
        if not isinstance(v, list):
            raise serializers.ValidationError('Send a list of tags.')
        return [str(t).strip() for t in v if str(t).strip()]


class TalentPoolMemberSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    candidate_name = serializers.CharField(source='candidate.full_name', read_only=True)
    email = serializers.CharField(source='candidate.email', read_only=True)
    phone = serializers.CharField(source='candidate.phone', read_only=True)
    tags = serializers.JSONField(source='candidate.tags', read_only=True)
    skills = serializers.CharField(source='candidate.skills', read_only=True)
    experience_years = serializers.DecimalField(source='candidate.experience_years', max_digits=4, decimal_places=1, read_only=True)
    may_contact = serializers.BooleanField(source='candidate.may_contact', read_only=True)
    consent_until = serializers.DateField(source='candidate.consent_until', read_only=True)

    class Meta:
        model = TalentPoolMember
        fields = '__all__'
        read_only_fields = ('added_by', 'added_on', 'last_contacted')
        validators = []

    def validate(self, attrs):
        pool = attrs.get('pool') or getattr(self.instance, 'pool', None)
        cand = attrs.get('candidate') or getattr(self.instance, 'candidate', None)
        if pool and cand and TalentPoolMember.objects.filter(pool=pool, candidate=cand).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError({'candidate': f'{cand.full_name} is already in {pool.name}.'})
        return attrs


class TalentCampaignSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = TalentCampaign
        fields = '__all__'
