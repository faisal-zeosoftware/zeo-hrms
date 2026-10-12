from django.apps import AppConfig


class ZeoCRMConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoCRM'
    verbose_name = 'CRM'

    def ready(self):
        from . import app  # noqa: F401  registers the app on the kit (links work when the other apps are installed too)
