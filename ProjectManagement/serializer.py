from rest_framework import serializers
from .models import Project, ProjectStage, Task, TimeSheet


class ProjectStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProjectStage
        fields = '__all__'


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
    def to_representation(self, instance):
        rep = super( TimeSheetSerializer, self).to_representation(instance)
        # if instance.branches.exists():
        #     rep['branches'] = [branches.branch_name for branches in instance.branches.all()]
        if instance.project:  
            rep['project'] = instance.project.title
        if instance.task:
            rep['task'] = instance.task.title
        if instance.employee:
            rep['employee'] = instance.employee.emp_code
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