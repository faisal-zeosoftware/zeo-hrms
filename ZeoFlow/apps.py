from django.apps import AppConfig


class ZeoFlowConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoFlow'
    verbose_name = 'Workflow automation: rules, triggers, conditions, actions, approvals, scheduled actions'

    def ready(self):
        from .engine import install
        install()
