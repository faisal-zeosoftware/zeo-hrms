from django.apps import AppConfig


class EmpManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'EmpManagement'
    def ready(self):
        import EmpManagement.signals