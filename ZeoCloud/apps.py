from django.apps import AppConfig


class ZeoCloudConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoCloud'
    verbose_name = 'ZEO Cloud – accounts, plans and licences'

    def ready(self):
        from . import signals  # noqa: F401
        signals.connect()
