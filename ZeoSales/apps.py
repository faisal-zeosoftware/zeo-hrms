from django.apps import AppConfig


class ZeoSalesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoSales'
    verbose_name = 'Sales'

    def ready(self):
        from . import app  # noqa: F401
