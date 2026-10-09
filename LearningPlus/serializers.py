from rest_framework import serializers

from zeo.module_helpers import DisplayFieldsMixin, model_clean
from . import models as M
from .services import course_progress, emp_name, trainer_stats


def _ref_models():
    from EmpManagement.models import emp_master
    from LearningManagement.models import Course, Nomination, TrainingSession
    from OrganisationManager.models import dept_master, desgntn_master
    return {
        'course_id': (Course, lambda o: f"{o.code} - {o.title}"),
        'session_id': (TrainingSession, lambda o: f"{o.code} - {o.course.title}"),
        'nomination_id': (Nomination, lambda o: f"{emp_name(o.employee)} @ {o.session.code}"),
        'employee_id': (emp_master, emp_name),
        'department_id': (dept_master, lambda o: o.dept_name),
        'designation_id': (desgntn_master, lambda o: o.desgntn_job_title),
    }


class IdRefMixin(DisplayFieldsMixin):
    """Integer links to other apps (course_id, employee_id ...): checked on save, shown as <name>_display."""

    def _refs(self):
        cache = self.context.setdefault('_lp_refs', {})
        if 'models' not in cache:
            cache['models'] = _ref_models()
        return cache

    def to_representation(self, instance):
        data = super().to_representation(instance)
        refs = self._refs()
        for key, (model, label) in refs['models'].items():
            if key in data:
                v = data[key]
                if v in (None, ''):
                    data[key[:-3] + '_display'] = None
                    continue
                ck = (key, v)
                if ck not in refs:
                    o = model.objects.filter(pk=v).first()
                    try:
                        refs[ck] = label(o) if o else f"#{v} (deleted)"
                    except Exception:
                        refs[ck] = str(o)
                data[key[:-3] + '_display'] = refs[ck]
        return data

    def validate(self, attrs):
        for key, (model, _) in _ref_models().items():
            v = attrs.get(key)
            if v not in (None, '') and not model.objects.filter(pk=v).exists():
                raise serializers.ValidationError({key: f"{key[:-3].replace('_', ' ').title()} {v} does not exist."})
        return model_clean(self, attrs, self.Meta.model)


def simple(model_cls, ro=(), extra=None):
    meta = type('Meta', (), {'model': model_cls, 'fields': '__all__', 'read_only_fields': tuple(ro)})
    attrs = {'Meta': meta}
    attrs.update(extra or {})
    return type(f"{model_cls.__name__}Serializer", (IdRefMixin, serializers.ModelSerializer), attrs)


TrainingCategorySerializer = simple(M.TrainingCategory)
ProviderSerializer = simple(M.Provider)
VenueSerializer = simple(M.Venue)
CourseExtraSerializer = simple(M.CourseExtra)
SessionExtraSerializer = simple(M.SessionExtra)
TrainingBudgetSerializer = simple(M.TrainingBudget)
SessionCostSerializer = simple(M.SessionCost)
SkillSerializer = simple(M.Skill)
SkillLevelSerializer = simple(M.SkillLevel)
CourseSkillSerializer = simple(M.CourseSkill)
RoleSkillSerializer = simple(M.RoleSkill)
EmployeeSkillSerializer = simple(M.EmployeeSkill, ro=('verified_by',))
SessionAttendanceSerializer = simple(M.SessionAttendance)
ModuleProgressSerializer = simple(M.ModuleProgress, ro=('started_at', 'completed_at', 'passed'))
CourseModuleSerializer = simple(M.CourseModule)


class TrainerSerializer(IdRefMixin, serializers.ModelSerializer):
    stats = serializers.SerializerMethodField()

    class Meta:
        model = M.Trainer
        fields = '__all__'

    def get_stats(self, obj):
        return trainer_stats(obj)

    def to_representation(self, instance):
        d = super().to_representation(instance)
        st = d.get('stats') or {}
        d['avg_rating'], d['sessions_count'] = st.get('avg_rating'), st.get('sessions')
        return d

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('trainer_type', getattr(self.instance, 'trainer_type', None)) == 'internal' and attrs.get('employee_id') and not attrs.get('name'):
            from EmpManagement.models import emp_master
            e = emp_master.objects.filter(pk=attrs['employee_id']).first()
            if e:
                attrs['name'] = ' '.join(x for x in [e.emp_first_name, e.emp_last_name] if x) or e.emp_code
                attrs.setdefault('email', e.emp_company_email or e.emp_personal_email)
        return attrs


class OnlineEnrolmentSerializer(IdRefMixin, serializers.ModelSerializer):
    progress = serializers.SerializerMethodField()

    class Meta:
        model = M.OnlineEnrolment
        fields = '__all__'
        read_only_fields = ('completed_at', 'score', 'status', 'certificate_id', 'assigned_by', 'nomination_id')

    def get_progress(self, obj):
        p = course_progress(obj.employee_id, obj.course_id)
        return {'percent': p['percent'], 'modules': p['modules'], 'completed': p['completed']}
