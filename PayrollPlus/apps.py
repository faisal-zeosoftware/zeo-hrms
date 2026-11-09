from django.apps import AppConfig


class PayrollPlusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'PayrollPlus'
    verbose_name = 'Payroll controls and final settlement'   # v1.18.0

    def ready(self):   # v1.62.11 (items 228, 231): payroll controls – period locks, holds, pay-date shift, loan limit, revisions
        from . import payctl_signals  # noqa: F401
        from . import pay163_signals  # noqa: F401   v1.63.0 (items 27, 28): recoveries – approval, deleted payslips
        from . import air_signals  # noqa: F401   v1.66.0 (item 120): family air ticket – partial use
        from . import lending_signals  # noqa: F401   v1.67.11 (loans): advance instalments / limit cuts of deleted payslips
        from . import effective  # noqa: F401   v1.67.12 (effective, item 64): dated versions of setup records
        effective.connect()
