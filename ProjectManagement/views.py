from rest_framework import viewsets
from .models import Project, ProjectStage, Task, TimeSheet
from .serializer import (ProjectSerializer,ProjectStageSerializer,TaskSerializer,TimeSheetSerializer
)
from rest_framework.permissions import IsAuthenticated


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    # permission_classes = [IsAuthenticated]

class ProjectStageViewSet(viewsets.ModelViewSet):
    queryset = ProjectStage.objects.all()
    serializer_class = ProjectStageSerializer
    # permission_classes = [IsAuthenticated]

class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    # permission_classes = [IsAuthenticated]

class TimeSheetViewSet(viewsets.ModelViewSet):
    queryset = TimeSheet.objects.all()
    serializer_class = TimeSheetSerializer
    # permission_classes = [IsAuthenticated]


# from django.shortcuts import render
# from .models import Project, ProjectTask,TaskTimesheet
# from rest_framework import viewsets
# from rest_framework.permissions import IsAuthenticated
# from .serializer import (
#     ProjectSerializer, ProjectTaskSerializer,TaskTimesheetSerializer
# )

# class ProjectViewSet(viewsets.ModelViewSet):
#     queryset = Project.objects.all()
#     serializer_class = ProjectSerializer
#     # permission_classes = [IsAuthenticated]

# class ProjectTaskViewSet(viewsets.ModelViewSet):
#     queryset = ProjectTask.objects.all()
#     serializer_class = ProjectTaskSerializer
#     # permission_classes = [IsAuthenticated]


# class TaskTimesheetViewSet(viewsets.ModelViewSet):
#     queryset = TaskTimesheet.objects.all()
#     serializer_class = TaskTimesheetSerializer
#     # permission_classes = [IsAuthenticated]
