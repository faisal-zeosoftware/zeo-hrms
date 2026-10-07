from rest_framework import serializers

from zeo.module_helpers import DisplayFieldsMixin, model_clean
from .models import Certificate, Course, Nomination, ParticipantResult, TrainingBond, TrainingNeed, TrainingSession


class CourseSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = '__all__'
        read_only_fields = ('code', 'created_at')


class TrainingNeedSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    department = serializers.SerializerMethodField()

    class Meta:
        model = TrainingNeed
        fields = '__all__'
        read_only_fields = ('created_at',)

    def get_department(self, obj):
        d = obj.employee.emp_dept_id
        return d.dept_name if d else None


class TrainingSessionSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    course_title = serializers.CharField(source='course.title', read_only=True)
    effective_cost = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    bond_required = serializers.BooleanField(read_only=True)
    confirmed = serializers.SerializerMethodField()
    waitlist = serializers.SerializerMethodField()

    class Meta:
        model = TrainingSession
        fields = '__all__'
        read_only_fields = ('code', 'created_at')

    def get_confirmed(self, obj):
        return obj.nominations.filter(seat_status='confirmed').count()

    def get_waitlist(self, obj):
        return obj.nominations.filter(seat_status='waitlist').count()

    def validate(self, attrs):
        return model_clean(self, attrs, TrainingSession)


class ParticipantResultSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    employee = serializers.SerializerMethodField()
    session = serializers.IntegerField(source='nomination.session_id', read_only=True)

    class Meta:
        model = ParticipantResult
        fields = '__all__'
        read_only_fields = ('passed',)

    def get_employee(self, obj):
        e = obj.nomination.employee
        return f"{e.emp_first_name or ''} {e.emp_last_name or ''} ({e.emp_code})".strip()

    def validate(self, attrs):
        return model_clean(self, attrs, ParticipantResult)

    def save(self, **kwargs):
        obj = super().save(**kwargs)
        obj.evaluate()
        obj.save(update_fields=['passed'])
        return obj


class NominationSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    result = ParticipantResultSerializer(read_only=True)
    course_title = serializers.CharField(source='session.course.title', read_only=True)
    session_date = serializers.DateField(source='session.start_date', read_only=True)
    department = serializers.SerializerMethodField()
    allow_leave_clash = serializers.BooleanField(write_only=True, required=False, default=False)
    bond_state = serializers.SerializerMethodField()
    bond_state_label = serializers.SerializerMethodField()

    BOND_LABELS = {'not_required': 'Not required', 'awaiting': 'Awaiting employee acceptance', 'accepted': 'Accepted'}

    def get_bond_state(self, obj):
        if not obj.bond_required:
            return 'not_required'
        return 'accepted' if obj.bond_accepted_on else 'awaiting'

    def get_bond_state_label(self, obj):
        return self.BOND_LABELS[self.get_bond_state(obj)]

    class Meta:
        model = Nomination
        fields = '__all__'
        read_only_fields = ('manager_status', 'ld_status', 'seat_status', 'rejection_reason', 'bond_required',
                            'bond_accepted_on', 'created_by', 'created_at')

    def get_department(self, obj):
        d = obj.employee.emp_dept_id
        return d.dept_name if d else None


class CertificateSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)

    class Meta:
        model = Certificate
        fields = '__all__'


class TrainingBondSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    recoverable_today = serializers.SerializerMethodField()

    class Meta:
        model = TrainingBond
        fields = '__all__'
        read_only_fields = ('accepted_on',)

    def get_recoverable_today(self, obj):
        return obj.recoverable_amount()

    def validate(self, attrs):
        return model_clean(self, attrs, TrainingBond)
