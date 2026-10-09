from django.apps import AppConfig


class AssetPlusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'AssetPlus'
    verbose_name = 'Asset management'

    def ready(self):
        from . import signals  # noqa: F401  (history, status guards, payroll recovery, exit clearance)
