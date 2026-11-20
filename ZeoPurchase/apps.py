from django.apps import AppConfig


class ZeoPurchaseConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoPurchase'
    verbose_name = 'Purchase'

    def ready(self):
        from . import app  # noqa: F401
