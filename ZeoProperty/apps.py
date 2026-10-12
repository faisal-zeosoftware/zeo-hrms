from django.apps import AppConfig


class ZeoPropertyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoProperty'
    verbose_name = 'Property'

    def ready(self):
        from . import app  # noqa: F401
