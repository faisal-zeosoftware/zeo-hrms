from django.apps import AppConfig


class AccesscontrolConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'AccessControl'
    verbose_name = 'Company, branch and role access'

    def ready(self):
        from .access import install
        install()
