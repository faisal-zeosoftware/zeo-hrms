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
        _tolerant_dates()


def _tolerant_dates():
    """v1.56.1 – a model DateField with default=timezone.now holds a date-time until it is read again, and DRF's DateField refused
    to show it ("Expected a date, but got a datetime") – the record was saved but the screen got a server error. Shown as the
    local date instead."""
    import datetime
    from django.utils import timezone
    from rest_framework import fields
    if getattr(fields.DateField, '_zeo_tolerant', False):
        return
    orig = fields.DateField.to_representation

    def to_representation(self, value):
        if isinstance(value, datetime.datetime):
            value = (timezone.localtime(value) if timezone.is_aware(value) else value).date()
        return orig(self, value)
    fields.DateField.to_representation = to_representation
    fields.DateField._zeo_tolerant = True
