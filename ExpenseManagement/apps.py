from django.apps import AppConfig


class ExpenseManagementConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ExpenseManagement'
    verbose_name = 'Expense management'

    def ready(self):
        from . import signals  # noqa: F401  (payslips → expense reports paid with the payroll)
