from django.apps import AppConfig


class ZeoAdminConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoAdmin'
    verbose_name = 'ZEO administration (scheduled reports, integrations, imports, out of office, health)'

    def ready(self):
        from . import signals  # noqa: F401  (webhooks on record events, out-of-office delegation of new approvals)
        signals.install()
        from . import legalhold   # v1.61.0 no deletes of an employee's records while a legal hold is active
        legalhold.install()
        from . import bankguard   # v1.62.0 bank-detail changes by HR wait for a second person (company rule)
        bankguard.install()
