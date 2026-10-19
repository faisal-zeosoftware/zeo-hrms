from django.apps import AppConfig


class OrgStructureConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'OrgStructure'
    verbose_name = 'Organisation structure'

    def ready(self):
        from . import location   # v1.30.0 location level: tag new transactions with their location
        location.install()
