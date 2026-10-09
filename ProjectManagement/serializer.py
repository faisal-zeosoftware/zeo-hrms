from rest_framework import serializers
from .models import Project, ProjectStage, Task, TimeSheet


class ProjectStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectStage
        fields = '__all__'
    def to_representation(self, instance):
            rep = super( ProjectStageSerializer, self).to_representation(instance)
            if instance.project:  
                rep['project'] = instance.project.title
            return rep
    


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = '__all__'
    
    def to_representation(self, instance):
        rep = super( TaskSerializer, self).to_representation(instance)
        # if instance.branches.exists():
        #     rep['branches'] = [branches.branch_name for branches in instance.branches.all()]
        if instance.project:  
            rep['project'] = instance.project.title
        if instance.stage:
            rep['stage'] = instance.stage.title
        if instance.task_managers.exists():
            rep['task_managers'] = [taskmanagers.emp_code for taskmanagers in instance.task_managers.all()]
        if instance.task_members.exists():
            rep['task_members'] = [taskmembers.emp_code for taskmembers in instance.task_members.all()]

        return rep


class TimeSheetSerializer(serializers.ModelSerializer):
    # project_title = serializers.CharField(source='project.title', read_only=True)
    # employee_name = serializers.CharField(source='employee.emp_code', read_only=True)
    class Meta:
        model = TimeSheet
        fields = '__all__'

    def validate(self, attrs):
        """No future dates, max 24 h a day, employee on the project / task, task of the project."""
        from ProjectControl import services as S
        inst = self.instance

        def get(k):
            return attrs[k] if k in attrs else (getattr(inst, k) if inst is not None else None)

        project, task, emp = get('project'), get('task'), get('employee')
        day = get('date') or S.local_today()
        if hasattr(day, 'date') and callable(day.date):
            day = day.date()
        hours = S.hours_of(get('time_spent') or '00:00')
        errors = S.validate_entry(emp, project, task, day, hours, exclude_id=inst.pk if inst is not None else None)
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    def to_representation(self, instance):
        rep = super( TimeSheetSerializer, self).to_representation(instance)
        rep['project_id'] = instance.project_id
        rep['task_id'] = instance.task_id
        rep['employee_id'] = instance.employee_id
        if instance.project:  
            rep['project'] = instance.project.title
        if instance.task:
            rep['task'] = instance.task.title
        if instance.employee:
            rep['employee'] = instance.employee.emp_code
        from django.apps import apps as django_apps
        if django_apps.is_installed('ProjectControl'):
            from ProjectControl import services as S
            e = S.get_extra(instance, create=False)
            rep['hours'] = float(e.hours if e and not e.running else S.hours_of(instance.time_spent))
            rep['billable'] = e.billable if e else S.default_billable(instance.project_id, instance.task_id)
            rep['approval_status'] = e.approval_status if e else 'draft'
            rep['rejection_reason'] = e.rejection_reason if e else ''
            rep['running'] = bool(e and e.running)
            rep['locked'] = bool(e and e.locked)
        return rep


class ProjectSerializer(serializers.ModelSerializer):
    project_stages = ProjectStageSerializer(many=True, read_only=True)
    tasks = TaskSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = '__all__'

    def to_representation(self, instance):
        rep = super( ProjectSerializer, self).to_representation(instance)
        # if instance.branches.exists():
        #     rep['branches'] = [branches.branch_name for branches in instance.branches.all()]
        if instance.managers.exists():
            rep['managers'] = [emp.emp_code for emp in instance.managers.all()]
        if instance.members.exists():
            rep['members'] = [emp.emp_code for emp in instance.members.all()]
        return rep

# from rest_framework import serializers
# from .models import Project, ProjectTask,TaskTimesheet

# class ProjectSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Project
#         fields = '__all__'

# class ProjectTaskSerializer(serializers.ModelSerializer):
#     # sub_tasks = serializers.SerializerMethodField()
#     class Meta:
#         model = ProjectTask
#         fields = '__all__'
#     # def get_sub_tasks(self, obj):
#     #     sub_tasks = obj.sub_tasks.all()
#     #     return ProjectTaskSerializer(sub_tasks, many=True, context=self.context).data

# class TaskTimesheetSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = TaskTimesheet
#         fields = '__all__'