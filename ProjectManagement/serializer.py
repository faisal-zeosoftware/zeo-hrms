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


class TimeSheetSerializer(serializers.ModelSerializer):
    # project_title = serializers.CharField(source='project.title', read_only=True)
    # employee_name = serializers.CharField(source='employee.emp_code', read_only=True)
    class Meta:
        model = TimeSheet
        fields = '__all__'


class ProjectSerializer(serializers.ModelSerializer):
    project_stages = ProjectStageSerializer(many=True, read_only=True)
    task_set = TaskSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = '__all__'

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