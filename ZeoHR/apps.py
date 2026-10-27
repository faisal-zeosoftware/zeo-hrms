from django.apps import AppConfig


class ZeoHRConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoHR'
    verbose_name = 'ZEO HR operations'

    def ready(self):
        from . import app  # noqa: F401  registers the HR operations registers on the app kit
