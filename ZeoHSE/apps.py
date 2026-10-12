from django.apps import AppConfig


class ZeoHSEConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoHSE'
    verbose_name = 'HSE (health, safety and environment)'

    def ready(self):
        from . import links   # v1.17.0 – listen to the other ZEO apps that are installed
        links.connect()
