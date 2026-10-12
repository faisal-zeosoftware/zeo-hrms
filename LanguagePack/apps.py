from django.apps import AppConfig


class LanguagePackConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'LanguagePack'
    verbose_name = 'Language packs'

    def ready(self):
        # v1.15.0: the Angular app sends `X-Language` on every call – browsers only send it when CORS allows the header.
        # settings.py should list it in CORS_ALLOW_HEADERS (see INTEGRATION); this is the safety net if it does not.
        from django.conf import settings
        try:
            hdrs = list(getattr(settings, 'CORS_ALLOW_HEADERS', []) or [])
            if hdrs and 'x-language' not in [h.lower() for h in hdrs]:
                settings.CORS_ALLOW_HEADERS = hdrs + ['x-language']
        except Exception:
            pass
