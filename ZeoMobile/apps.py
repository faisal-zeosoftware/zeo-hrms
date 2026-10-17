from django.apps import AppConfig


class ZeoMobileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoMobile'
    verbose_name = 'ZEO mobile app – devices and push notifications'

    def ready(self):
        from . import signals
        signals.connect()
