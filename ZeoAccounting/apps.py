from django.apps import AppConfig


class ZeoAccountingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoAccounting'
    verbose_name = 'Accounting integration'

    def ready(self):
        from django.db.models.signals import post_migrate
        from . import signals
        signals.connect()
        post_migrate.connect(_roles, sender=self, dispatch_uid='zeoacc-roles')


def _roles(sender, using='default', **kw):
    """The three permission groups (Accounting – manager / accountant / viewer) in every company schema."""
    from django.db import connection
    if getattr(connection, 'schema_name', 'public') == 'public':
        return
    try:
        from .services import make_roles
        make_roles()
    except Exception:   # noqa: BLE001 – never stop a migration
        pass
