from django.apps import AppConfig


class PayrollmanagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'PayrollManagement'
    
    def ready(self):
        import PayrollManagement.signals
