from django.apps import AppConfig


class PayrollExtrasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'PayrollExtras'
    verbose_name = 'UAE payroll extras (commission, retention bonus, service charge, ILOE, Emiratisation, bulk advances, cost to company)'

    def ready(self):
        from . import hooks
        hooks.connect()
