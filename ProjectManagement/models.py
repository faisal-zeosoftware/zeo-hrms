from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from datetime import date



def validate_time_format(value):
    if len(value) > 5:
        raise ValidationError(_("Invalid format, should be HH:MM"))
    try:
        hour, minute = value.split(":")
        if len(hour) < 2 or len(minute) < 2:
            raise ValidationError(_("Format should be HH:MM"))
        if int(minute) not in range(60):
            raise ValidationError(_("Minutes must be between 00 and 59"))
    except ValueError:
        raise ValidationError(_("Invalid format"))


class Project(models.Model):
    PROJECT_STATUS = [
        ("new", "New"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("on_hold", "On Hold"),
        ("cancelled", "Cancelled"),
        ("expired", "Expired"),
    ]

    title = models.CharField(max_length=200, unique=True)
    managers = models.ManyToManyField("EmpManagement.emp_master", related_name="managed_projects", blank=True)
    members = models.ManyToManyField("EmpManagement.emp_master", related_name="member_projects", blank=True)
    status = models.CharField(choices=PROJECT_STATUS, default="new", max_length=50)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to='projects/', null=True, blank=True)
    description = models.TextField()
    is_active = models.BooleanField(default=True)

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError(_("End date must be after start date"))
        if self.end_date and self.end_date < date.today():
            self.status = 'expired'

    def __str__(self):
        return self.title


class ProjectStage(models.Model):
    title = models.CharField(max_length=200)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="stages")
    sequence = models.PositiveIntegerField(blank=True, null=True)
    is_end_stage = models.BooleanField(default=False)

    def clean(self):
        if self.is_end_stage:
            existing = ProjectStage.objects.filter(project=self.project, is_end_stage=True).exclude(pk=self.pk)
            if existing.exists():
                raise ValidationError(_("End stage already exists in this project"))

    def save(self, *args, **kwargs):
        if self.sequence is None:
            last_stage = ProjectStage.objects.filter(project=self.project).order_by("-sequence").first()
            self.sequence = last_stage.sequence + 1 if last_stage else 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.project.title} - {self.title}"


class Task(models.Model):
    TASK_STATUS = [
        ("to_do", "To Do"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
        ("expired", "Expired"),
    ]

    title = models.CharField(max_length=200)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    stage = models.ForeignKey(ProjectStage, on_delete=models.CASCADE, related_name="tasks")
    task_managers = models.ManyToManyField("EmpManagement.emp_master", related_name="task_managed", blank=True)
    task_members = models.ManyToManyField("EmpManagement.emp_master", related_name="task_assigned", blank=True)
    status = models.CharField(choices=TASK_STATUS, default="to_do", max_length=50)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    document = models.FileField(upload_to='tasks/', null=True, blank=True)
    description = models.TextField()
    sequence = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def clean(self):
        if self.end_date and self.project:
            if self.end_date > self.project.end_date or self.end_date < self.project.start_date:
                raise ValidationError(_("Task end date must be within project dates"))
        if self.end_date and self.end_date < date.today():
            self.status = "expired"

    def __str__(self):
        return f"{self.project.title} - {self.title}"


class TimeSheet(models.Model):
    TIME_SHEET_STATUS = [
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="timesheets")
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="timesheets", null=True, blank=True)
    employee = models.ForeignKey("EmpManagement.emp_master", on_delete=models.CASCADE, related_name="timesheets")
    date = models.DateField(default=timezone.now)
    time_spent = models.CharField(max_length=20, validators=[validate_time_format], default="00:00")
    status = models.CharField(choices=TIME_SHEET_STATUS, default="in_progress", max_length=50)
    description = models.TextField(blank=True, null=True)

    def clean(self):
        if not self.project:
            raise ValidationError(_("Project is required"))
        if not self.description:
            raise ValidationError(_("Description is required"))
        if self.date > date.today():
            raise ValidationError(_("Cannot select a future date"))

        if self.task:
            emp = self.employee
            task = self.task
            if not (emp in task.task_members.all() or emp in task.task_managers.all()
                    or emp in task.project.members.all() or emp in task.project.managers.all()):
                raise ValidationError(_("Employee not assigned to this task or project"))
        else:
            if self.employee not in self.project.members.all() and self.employee not in self.project.managers.all():
                raise ValidationError(_("Employee not assigned to this project"))

    def __str__(self):
        return f"{self.employee} - {self.project} - {self.date}"

    class Meta:
        ordering = ["-id"]

