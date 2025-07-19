from django.apps import AppConfig


class OrganisationmanagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'OrganisationManager'

    def ready(self):
        import OrganisationManager.signals
