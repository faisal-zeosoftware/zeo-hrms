from django.apps import AppConfig


class ChatterConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Chatter'
    verbose_name = 'Record activity: history, notes, attachments, activities and designer fields'

    def ready(self):
        from .tracking import install
        install()
        from .chatwebhooks import connect   # v1.67.26 Teams and Slack: company events posted to a channel
        connect()
