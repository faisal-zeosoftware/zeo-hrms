"""
Projects, stages, tasks and timesheets.

Who sees what (the rules live in ProjectControl.services):
* company admin: everything;
* HR / project users (view_project / view_timesheet): the projects and the employees' timesheets of their branches;
* everybody else: the projects and tasks they are a member or manager of, their own timesheets, the timesheets of the
  projects / tasks they manage and of the employees who report to them.
Timesheet entries are validated (no future dates, max 24 h a day, member of the project / task, task of the project),
can only be changed by the employee (or HR / admin), and are locked once submitted or approved
(ProjectControl approval data, when that app is installed).
"""
from django.apps import apps as django_apps
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

from ProjectControl import services as S

from .models import Project, ProjectStage, Task, TimeSheet
from .serializer import ProjectSerializer, ProjectStageSerializer, TaskSerializer, TimeSheetSerializer


def control_installed():
    return django_apps.is_installed('ProjectControl')


class ProjectViewSet(viewsets.ModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer
    zeo_scope = False  # rows limited by get_queryset (members / managers / branches)

    def get_queryset(self):
        return S.visible_projects(self.request).order_by('-id')


class ProjectStageViewSet(viewsets.ModelViewSet):
    queryset = ProjectStage.objects.all()
    serializer_class = ProjectStageSerializer
    zeo_scope = False

    def get_queryset(self):
        return ProjectStage.objects.filter(project__in=S.visible_projects(self.request).values('pk'))


class TaskViewSet(viewsets.ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = TaskSerializer
    zeo_scope = False

    def get_queryset(self):
        return S.visible_tasks(self.request).order_by('id')


class TimeSheetViewSet(viewsets.ModelViewSet):
    queryset = TimeSheet.objects.all()
    serializer_class = TimeSheetSerializer
    zeo_scope = False

    def get_queryset(self):
        return S.visible_timesheets(self.request).select_related('project', 'task', 'employee').order_by('-date', '-id')

    def _billable(self):
        return S.parse_bool(self.request.data.get('billable')) if hasattr(self.request.data, 'get') else None

    def _check_lock(self, ts, verb):
        if not S.can_edit_entry(self.request, ts):
            raise PermissionDenied(f'You can only {verb} your own timesheet entries.')
        if control_installed():
            e = S.get_extra(ts, create=False)
            if e is not None and e.running:
                raise PermissionDenied('The timer of this entry is running. Stop it first.')
            if e is not None and e.approval_status == 'approved':
                raise PermissionDenied(f'This entry is approved and locked; it cannot be {verb}d.')
            if e is not None and e.approval_status == 'submitted':
                raise PermissionDenied(f'This entry is waiting for approval; it cannot be {verb}d until it is rejected.')

    def perform_create(self, serializer):
        ts = serializer.save()
        if control_installed():
            S.sync_extra(ts, self._billable())

    def perform_update(self, serializer):
        ts = serializer.instance
        self._check_lock(ts, 'change')
        new_emp = serializer.validated_data.get('employee')
        if new_emp is not None and new_emp.pk != ts.employee_id and not S.has(self.request, 'change_timesheet'):
            raise PermissionDenied('You cannot move an entry to another employee.')
        before = S.snapshot(ts)
        ts = serializer.save()
        if control_installed():
            e = S.sync_extra(ts, self._billable())
            S.record_correction(ts, before, e)

    def perform_destroy(self, instance):
        self._check_lock(instance, 'delete')
        if control_installed():
            from ProjectControl.models import TimesheetExtra
            TimesheetExtra.objects.filter(timesheet_id=instance.pk).delete()
        instance.delete()
