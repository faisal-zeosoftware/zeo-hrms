from django.apps import AppConfig


class ZeoMaintenanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoMaintenance'
    verbose_name = 'Maintenance'

    def ready(self):
        from . import app  # noqa: F401
