from decimal import Decimal

from rest_framework import serializers

from zeo.module_helpers import DisplayFieldsMixin, model_clean
from .models import (KPI, AppraisalCycle, AppraisalOutcome, AppraisalTemplate, CalibrationLog, CheckIn, Goal,
                     GoalSheet, IncrementBand, PerformanceImprovementPlan, PIPReview)


class KPISerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = KPI
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class AppraisalTemplateSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = AppraisalTemplate
        fields = '__all__'

    def validate(self, attrs):
        return model_clean(self, attrs, AppraisalTemplate)


class IncrementBandSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = IncrementBand
        fields = '__all__'


class AppraisalCycleSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    increment_bands = IncrementBandSerializer(many=True, read_only=True)
    sheet_count = serializers.SerializerMethodField()
    eligible_count = serializers.SerializerMethodField()

    class Meta:
        model = AppraisalCycle
        fields = '__all__'
        read_only_fields = ('status', 'launched_on', 'created_by', 'created_at')

    def get_sheet_count(self, obj):
        return obj.goal_sheets.count()

    def get_eligible_count(self, obj):
        return obj.eligible_employees().count()

    def validate(self, attrs):
        return model_clean(self, attrs, AppraisalCycle)


class GoalSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Goal
        fields = '__all__'

    def validate(self, attrs):
        sheet = attrs.get('sheet') or getattr(self.instance, 'sheet', None)
        kpi = attrs.get('kpi')
        if kpi and 'goal_type' not in attrs and self.instance is None:
            attrs['goal_type'] = kpi.kpi_type
        if sheet and sheet.status in ('calibrated', 'acknowledged'):
            raise serializers.ValidationError("This goal sheet is locked.")
        return model_clean(self, attrs, Goal)


class CheckInSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = CheckIn
        fields = '__all__'
        read_only_fields = ('created_by', 'created_at')


class CalibrationLogSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = CalibrationLog
        fields = '__all__'
        read_only_fields = ('changed_by', 'changed_at', 'old_rating')


class GoalSheetSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    goals = GoalSerializer(many=True, read_only=True)
    total_weight = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()
    designation = serializers.SerializerMethodField()
    employee_user = serializers.IntegerField(source='employee.users_id', read_only=True)
    cycle_status = serializers.CharField(source='cycle.status', read_only=True)
    kpi_weight = serializers.DecimalField(source='cycle.template.kpi_weight', max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = GoalSheet
        fields = '__all__'
        read_only_fields = ('status', 'self_score', 'manager_score', 'proposed_rating', 'final_rating',
                            'acknowledged_on', 'return_reason', 'created_at', 'updated_at')

    def get_total_weight(self, obj):
        return float(sum((g.weight for g in obj.goals.all()), Decimal('0')))

    def get_department(self, obj):
        return obj.employee.emp_dept_id.dept_name if obj.employee.emp_dept_id else None

    def get_designation(self, obj):
        return obj.employee.emp_desgntn_id.desgntn_job_title if obj.employee.emp_desgntn_id else None


class AppraisalOutcomeSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    employee_name = serializers.SerializerMethodField()
    employee_code = serializers.CharField(source='sheet.employee.emp_code', read_only=True)
    department = serializers.SerializerMethodField()
    cycle = serializers.CharField(source='sheet.cycle.name', read_only=True)

    class Meta:
        model = AppraisalOutcome
        fields = '__all__'
        read_only_fields = ('sheet', 'final_rating', 'current_basic', 'new_basic', 'status', 'pushed_on', 'pushed_by')

    def get_employee_name(self, obj):
        e = obj.sheet.employee
        return " ".join(p for p in [e.emp_first_name, e.emp_last_name] if p)

    def get_department(self, obj):
        d = obj.sheet.employee.emp_dept_id
        return d.dept_name if d else None

    def update(self, instance, validated_data):
        if instance.status == 'pushed':
            raise serializers.ValidationError("Outcome already pushed to payroll.")
        instance = super().update(instance, validated_data)
        instance.recalc()
        instance.save()
        return instance


class PIPReviewSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = PIPReview
        fields = '__all__'
        read_only_fields = ('reviewed_by',)


class PerformanceImprovementPlanSerializer(DisplayFieldsMixin, serializers.ModelSerializer):
    reviews = PIPReviewSerializer(many=True, read_only=True)

    class Meta:
        model = PerformanceImprovementPlan
        fields = '__all__'
        read_only_fields = ('created_at',)

    def validate(self, attrs):
        return model_clean(self, attrs, PerformanceImprovementPlan)
