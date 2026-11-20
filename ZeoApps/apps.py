from django.apps import AppConfig


class ZeoAppsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ZeoApps'
    verbose_name = 'Apps'

    def ready(self):
        # v1.22.1: document numbers of the other transaction screens from Settings › Document numbering
        from zeo.docnum import connect
        connect()
        from .hrfeatures import connect as _hrf   # v1.75.1 a new type gets its "No approval" chain while approvals are off
        _hrf()
