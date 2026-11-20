from django.apps import AppConfig


class ZeoInventoryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoInventory'
    verbose_name = 'Inventory'

    def ready(self):
        from . import app  # noqa: F401
