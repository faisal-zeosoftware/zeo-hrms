from django.apps import AppConfig


class EmployeeProfileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'EmployeeProfile'
    verbose_name = 'Employee profile'

    def ready(self):
        # v1.13.0: employment status from resignations / end of service, employment info for new employees
        from . import signals  # noqa: F401
        signals.connect()
