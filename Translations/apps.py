from django.apps import AppConfig


class TranslationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Translations'
    verbose_name = 'Translated master data'

    def ready(self):
        # v1.15.0: list / detail API responses of the translatable masters show the name in the request language
        from .hooks import install
        install()
