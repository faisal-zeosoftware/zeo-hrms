from django.apps import AppConfig


class ZeoMfgConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoMfg'
    verbose_name = 'Manufacturing'

    def ready(self):
        from . import app  # noqa: F401
