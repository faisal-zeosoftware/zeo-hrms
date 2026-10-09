from django.apps import AppConfig


class ProjectControlConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ProjectControl'
    verbose_name = 'Project control (timesheet approval, costing, billing)'
