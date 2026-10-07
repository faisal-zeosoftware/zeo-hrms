from django.apps import AppConfig


class DatatoolsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'DataTools'
    verbose_name = 'Import, export and duplicate checks'

    def ready(self):
        from .duplicates import install
        install()
        from .forms import install as install_forms
        install_forms()
