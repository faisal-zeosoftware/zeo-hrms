from django.apps import AppConfig


class ZeoAssetsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoAssets'
    verbose_name = 'Asset Management'

    def ready(self):
        from . import app  # noqa: F401
        from . import signals  # noqa: F401
