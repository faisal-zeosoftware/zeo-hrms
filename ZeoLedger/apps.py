from django.apps import AppConfig


class ZeoLedgerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoLedger'
    verbose_name = 'Accounting'

    def ready(self):
        from . import app  # noqa: F401
