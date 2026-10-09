from django.apps import AppConfig


class LeavePolicyConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'LeavePolicy'
    verbose_name = 'Leave policies and UAE leave rules'

    def ready(self):
        from . import signals  # noqa: F401  (leave approvals → ledger, pay slabs, encashment → payroll)
