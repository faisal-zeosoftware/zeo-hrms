from django.apps import AppConfig


class ZeoLicenceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoLicence'
    verbose_name = 'ZEO Enterprise licence – signed licence files, Enterprise requests'

    def ready(self):
        from . import signals
        signals.connect()
